"""W2-PKG5 items 1 and 2: the packaged skill verbs, the SKILL rewrite and ``doctor``.

Every skills test installs into a tmp directory or a tmp HOME; nothing here may
touch the real ``~/.claude/skills`` or ``~/.agents/skills``.
"""
from __future__ import annotations

import importlib.metadata
import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from cmru import cli, doctor
from tests.test_w2_integ import _registry_problem

PROJECT = Path(__file__).resolve().parents[1]
SKILL = PROJECT / "src" / "cmru" / "skills" / "cmru-cli" / "SKILL.md"
GITHUB_BLOCK = '[github]\nowner = "o"\nrepo = "r"\nowner_type = "user"\n'
CONFIG = f'''schema_version = 1

{GITHUB_BLOCK}
[targets]
host = "github"
registry = ["ghcr.io"]

[runtime]
kind = "none"

[env]
{{env}}

[project]
id = "demo"
description = "Test project"
prefix = "demo-v"
artifacts = ["wheel"]
template_revision = 4

[project.version]
strategy = "scm"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]

[steps.run-tests]
quiet = true
commands = [{{{{ label = "test", argv = ["true"], cwd = "." }}}}]

[steps.build]
quiet = true
commands = [{{{{ label = "build", argv = ["true"], cwd = "." }}}}]

[steps.push]
quiet = true
commands = [{{{{ label = "push", argv = ["true"], cwd = "." }}}}]
'''


@pytest.fixture
def home(monkeypatch, tmp_path):
    """A throw-away HOME: the library resolves skill destinations from it."""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return fake


@pytest.fixture(scope="module")
def registry():
    return cli._build_cli()


# --------------------------------------------------------------------- skills


def test_the_root_registers_the_four_skills_verbs(registry):
    skills = registry.delegates["skills"]
    assert set(skills.command_parsers) == {"install", "list", "check", "uninstall"}


def test_skills_install_check_uninstall_round_trip_in_a_tmp_dest(tmp_path, capsys):
    dest = tmp_path / "skills"
    assert cli.main(["skills", "install", "--dest", str(dest)]) == 0
    installed = dest / "cmru-cli" / "SKILL.md"
    assert installed.is_file()
    assert cli.main(["skills", "check", "--dest", str(dest)]) == 0
    assert cli.main(["skills", "list", "--dest", str(dest)]) == 0
    assert "current" in capsys.readouterr().out
    # a locally modified copy is refused by `check`, never silently clobbered
    installed.write_text(installed.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
    assert cli.main(["skills", "check", "--dest", str(dest)]) == 1
    capsys.readouterr()
    assert cli.main(["skills", "uninstall", "--overwrite-modified", "--dest", str(dest)]) == 0
    assert not (dest / "cmru-cli").exists()


def test_skills_check_is_nonzero_when_the_skill_is_not_installed(tmp_path):
    assert cli.main(["skills", "check", "--dest", str(tmp_path / "nothing")]) == 1


def test_skills_install_with_a_fake_home_writes_both_harness_dirs(home):
    assert cli.main(["skills", "install"]) == 0
    assert (home / ".claude" / "skills" / "cmru-cli" / "SKILL.md").is_file()
    assert (home / ".agents" / "skills" / "cmru-cli" / "SKILL.md").is_file()


def test_the_packaged_skill_source_validates_with_the_library():
    from cli_extended.skills import validate_skill_source

    folder = SKILL.parent
    files = {
        str(path.relative_to(folder)): path.read_bytes()
        for path in folder.rglob("*") if path.is_file()
    }
    validate_skill_source("cmru-cli", files)


# ------------------------------------------------- SKILL.md grammar guard (CLI-D1)

_FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`(cmru [^`]+)`")  # a bare `cmru` names the tool, not a command


def _skill_cmru_lines(text: str) -> list[str]:
    """Every ``cmru ...`` command the skill shows: fenced lines and inline spans."""
    lines: list[str] = []
    for block in _FENCE.findall(text):
        for raw in block.splitlines():
            if raw.startswith("cmru "):
                lines.append(raw)
    stripped = _FENCE.sub("", text)
    lines += [match.group(1) for match in _INLINE.finditer(stripped)]
    return lines


def _skill_problem(line: str, registry) -> str | None:
    """Why ``line`` is not a valid cmru invocation, or None. Parses against the registry."""
    tokens = shlex.split(line, comments=True)
    assert tokens[0] == "cmru", line
    rest = tokens[1:]
    if rest and rest[0] in {"--version", "--help"} and len(rest) == 1:
        return None
    if rest[:1] == ["version"] and len(rest) == 1:
        return None
    if rest[:1] == ["help"]:
        known = set(registry.command_parsers)
        return None if len(rest) <= 2 and (len(rest) == 1 or rest[1] in known) else "help of an unknown verb"
    if rest[1:] == ["--help"]:  # `cmru VERB --help`: argparse would exit; check the verb
        return None if rest[0] in registry.command_parsers else f"unknown verb {rest[0]!r}"
    return _registry_problem(["cmru", *rest], registry)


def test_the_skill_shows_a_real_number_of_commands(registry):
    lines = _skill_cmru_lines(SKILL.read_text(encoding="utf-8"))
    assert len(lines) >= 30
    verbs = {shlex.split(line, comments=True)[1] for line in lines if len(shlex.split(line, comments=True)) > 1}
    # every root verb the skill covers is demonstrated at least once
    assert {"status", "release", "build", "publish", "cleanup", "abandon", "doctor",
            "tester-gate", "worktrees", "dependencies", "changelog", "versions"} <= verbs


def test_every_cmru_command_in_the_skill_parses_against_the_registry(registry):
    problems = []
    for line in _skill_cmru_lines(SKILL.read_text(encoding="utf-8")):
        problem = _skill_problem(line, registry)
        if problem is not None:
            problems.append(f"{line!r}: {problem}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("line", [
    "cmru status --dry-run",                  # status is read-only: no --dry-run
    "cmru release --abandon all-previous",    # removed grammar
    "cmru get ciu",                           # removed verb
    "cmru release --no-such-flag",            # a flag the registry never had
    "cmru cleanup",                           # a mode is required
    "cmru publish",                           # exactly one of the two sources is required
])
def test_the_guard_rejects_each_removed_or_invalid_spelling(registry, line):
    assert _skill_problem(line, registry) is not None


def test_a_removed_flag_planted_in_a_skill_example_is_detected(registry):
    """Plant: an example line with a flag the registry no longer has must fail the guard."""
    text = SKILL.read_text(encoding="utf-8")
    assert "cmru release --dry-run " in text
    planted = text.replace("cmru release --dry-run ", "cmru release --abandon ", 1)
    problems = [
        line for line in _skill_cmru_lines(planted) if _skill_problem(line, registry) is not None
    ]
    assert problems and any("--abandon" in line for line in problems)


def test_the_skill_no_longer_carries_the_review_listed_errors():
    text = SKILL.read_text(encoding="utf-8")
    assert "`--version` is not a flag" not in text
    assert "--abandon" in text and "no `release --abandon`" in text  # only the explicit denial
    assert "all-previous" not in text
    assert "cmru get " not in text and "cmru get\n" not in text
    # no hard cmru version pin and no "verified as of" stamp (example project
    # versions such as 7.0.0 are fine; cmru's own version never appears)
    assert not re.search(r"cmru[^\n]{0,20}\b5\.\d+\.\d+", text)
    assert "5.2" not in text and "as of last verified" not in text
    # bare cleanup is not routine; dependencies --write and status are classified
    assert "is NOT routine" in text
    assert "cmru status --dry-run" not in text
    section = text.split("## Read-only verbs", 1)[1].split("##", 1)[0]
    assert "--write" not in section.replace("--write updates", "")
    assert "dependencies --write --dry-run" in text.split("## Writing verbs", 1)[1]


# ---------------------------------------------------------------------- doctor


def _doctor(args, capsys):
    code = cli.main(["doctor", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def isolated(monkeypatch, tmp_path, home):
    """No config above cwd, no token, no real subprocess."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    for name in doctor.CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return work


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _fake_run(monkeypatch, table):
    """Route doctor's one subprocess seam: ``table`` maps an argv prefix to a result/exception."""
    calls = []

    def run(argv, timeout=10):
        calls.append(list(argv))
        for prefix, outcome in table.items():
            if tuple(argv[:len(prefix)]) == prefix:
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome
        raise AssertionError(f"unexpected probe {argv}")

    monkeypatch.setattr(doctor, "_run", run)
    return calls


def test_git_ok_prints_the_version_and_fails_when_missing(monkeypatch):
    monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/git")
    _fake_run(monkeypatch, {("git", "--version"): _Done(0, "git version 9.9.9\n")})
    ok = doctor.check_git()
    assert (ok.status, ok.summary) == ("ok", "git version 9.9.9")
    monkeypatch.setattr(doctor, "_which", lambda name: None)
    missing = doctor.check_git()
    assert missing.status == "fail" and missing.remedy
    monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/git")
    _fake_run(monkeypatch, {("git", "--version"): _Done(1, "")})
    assert doctor.check_git().status == "fail"


def test_docker_cli_missing_daemon_down_timeout_and_ok(monkeypatch):
    monkeypatch.setattr(doctor, "_which", lambda name: None)
    assert doctor.check_docker().status == "fail"
    monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/docker")
    probe = ("docker", "version")
    _fake_run(monkeypatch, {probe: _Done(1, "", "Cannot connect to the Docker daemon")})
    down = doctor.check_docker()
    assert down.status == "fail" and "daemon" in down.summary and down.remedy
    _fake_run(monkeypatch, {probe: subprocess.TimeoutExpired("docker", 5)})
    slow = doctor.check_docker()
    assert slow.status == "fail" and "5 seconds" in slow.summary and slow.remedy
    calls = _fake_run(monkeypatch, {probe: _Done(0, "27.1.0\n")})
    ok = doctor.check_docker()
    assert ok.status == "ok" and "27.1.0" in ok.summary
    assert calls == [["docker", "version", "--format", "{{.Server.Version}}"]]


def test_config_skip_ok_and_fail(monkeypatch, isolated):
    assert doctor.check_config().status == "skip"
    assert doctor.check_config().summary == "no cmru config here"
    (isolated / "cmru.toml").write_text(CONFIG.format(env=""), encoding="utf-8")
    ok = doctor.check_config()
    assert ok.status == "ok" and "cmru.toml" in ok.summary
    (isolated / "cmru.toml").write_text("schema_version = 1\nbogus = 1\n", encoding="utf-8")
    bad = doctor.check_config()
    assert bad.status == "fail" and bad.remedy and "\n" not in bad.summary


def test_credentials_report_which_variable_never_the_value(monkeypatch, isolated):
    secret = "ghp_SUPERSECRETVALUE123"
    missing = doctor.check_credentials()
    assert missing.status == "warn" and missing.remedy
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    token = doctor.check_credentials()
    assert token.status == "ok" and token.details == {"variable": "GITHUB_TOKEN"}
    monkeypatch.setenv("GITHUB_PUSH_PAT", secret)
    both = doctor.check_credentials()
    assert both.details == {"variable": "GITHUB_PUSH_PAT"}  # the push PAT wins
    monkeypatch.setenv("GITHUB_PUSH_PAT", "   ")
    monkeypatch.delenv("GITHUB_TOKEN")
    assert doctor.check_credentials().status == "warn"  # blank is not set
    for result in (missing, token, both):
        assert secret not in repr(result)


@pytest.mark.parametrize("as_json", [False, True])
def test_a_token_value_never_appears_in_doctor_output(monkeypatch, isolated, capsys, as_json):
    secret = "ghp_SUPERSECRETVALUE123"
    for name in doctor.CREDENTIAL_VARIABLES:
        monkeypatch.setenv(name, secret)
    monkeypatch.setattr(doctor, "_which", lambda name: None)  # keep probes off the host
    code, out, err = _doctor(["--json"] if as_json else [], capsys)
    assert secret not in out and secret not in err
    assert "GITHUB_PUSH_PAT is set" in out or as_json
    if as_json:
        assert any(check["details"].get("variable") == "GITHUB_PUSH_PAT"
                   for check in json.loads(out)["checks"])


def test_images_skip_when_nothing_declared_or_docker_is_down_ok_and_fail(monkeypatch, isolated):
    assert doctor.check_images().summary == "no cmru config here"
    (isolated / "cmru.toml").write_text(CONFIG.format(env=""), encoding="utf-8")
    assert doctor.check_images().status == "skip"
    env = 'CMRU_TESTER_UNIFIED_IMAGE = "t:1"\nCMRU_WHEEL_BUILDER_IMAGE = "w:1"'
    (isolated / "cmru.toml").write_text(CONFIG.format(env=env), encoding="utf-8")
    monkeypatch.setattr(doctor, "_which", lambda name: None)
    assert doctor.check_images().status == "skip"  # no docker CLI
    monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/docker")
    _fake_run(monkeypatch, {("docker", "image", "inspect"): _Done(1, "", "Cannot connect to the Docker daemon")})
    assert doctor.check_images().status == "skip"  # daemon down
    calls = _fake_run(monkeypatch, {("docker", "image", "inspect"): _Done(0, "[]")})
    ok = doctor.check_images()
    assert ok.status == "ok"
    assert sorted(call[-1] for call in calls) == ["t:1", "w:1"]
    _fake_run(monkeypatch, {
        ("docker", "image", "inspect", "t:1"): _Done(0, "[]"),
        ("docker", "image", "inspect", "w:1"): _Done(1, "", "Error: No such image: w:1"),
    })
    absent = doctor.check_images()
    assert absent.status == "fail" and "CMRU_WHEEL_BUILDER_IMAGE=w:1" in absent.summary and absent.remedy


def _fake_metadata(monkeypatch, *, installed, requires):
    real_version, real_requires = importlib.metadata.version, importlib.metadata.requires
    monkeypatch.setattr(
        importlib.metadata, "version",
        lambda name: installed if name == "cli-extended" else real_version(name),
    )
    monkeypatch.setattr(
        importlib.metadata, "requires",
        lambda name: requires if name == "cmru" else real_requires(name),
    )


def test_cli_extended_check_floor_ok_fail_warn(monkeypatch):
    _fake_metadata(monkeypatch, installed="0.2.0", requires=["cli-extended>=0.2.0", 'pytest>=8; extra == "test"'])
    assert doctor.check_cli_extended().status == "ok"
    _fake_metadata(monkeypatch, installed="0.1.9", requires=["cli-extended>=0.2.0"])
    low = doctor.check_cli_extended()
    assert low.status == "fail" and "0.2.0" in low.summary and low.remedy
    _fake_metadata(monkeypatch, installed="0.10.0", requires=["cli-extended>=0.9"])
    assert doctor.check_cli_extended().status == "ok"  # numeric, not lexical, comparison
    _fake_metadata(monkeypatch, installed="0.2.0", requires=['pytest>=8; extra == "test"'])
    assert doctor.check_cli_extended().status == "warn"  # no declared floor
    _fake_metadata(monkeypatch, installed="0.2.0", requires=["cli-extended==0.2.0"])
    assert doctor.check_cli_extended().status == "warn"  # a floor it cannot evaluate

    def not_installed(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", not_installed)
    assert doctor.check_cli_extended().status == "fail"


def test_probe_failures_and_unreadable_metadata_degrade_to_a_result_never_a_crash(monkeypatch, isolated):
    """Every probe/metadata error path of the checks (100% line and branch coverage)."""
    monkeypatch.setattr(doctor, "_which", lambda name: "/usr/bin/" + name)
    # git: the executable exists but cannot run; a blank leading line is skipped in the version.
    _fake_run(monkeypatch, {("git", "--version"): OSError("exec format error")})
    git = doctor.check_git()
    assert git.status == "fail" and "OSError" in git.summary and git.remedy
    _fake_run(monkeypatch, {("git", "--version"): _Done(0, "\n\ngit version 9.9.9\n")})
    assert doctor.check_git().summary == "git version 9.9.9"
    # docker: the CLI cannot be executed.
    _fake_run(monkeypatch, {("docker", "version"): OSError("permission denied")})
    docker = doctor.check_docker()
    assert docker.status == "fail" and "OSError" in docker.summary and docker.remedy
    # images: a config that does not load, then an inspect that cannot run.
    (isolated / "cmru.toml").write_text("schema_version = 1\nbogus = 1\n", encoding="utf-8")
    assert doctor.check_images().summary == "config does not load; see the config check"
    env = 'CMRU_TESTER_UNIFIED_IMAGE = "t:1"'
    (isolated / "cmru.toml").write_text(CONFIG.format(env=env), encoding="utf-8")
    _fake_run(monkeypatch, {("docker", "image", "inspect"): OSError("no docker")})
    assert doctor.check_images().status == "skip"
    # cli-extended: no cmru distribution, a non-numeric installed version, a non-matching
    # requirement listed before the cli-extended one.
    _fake_metadata(monkeypatch, installed="0.2.0", requires=["cli-extended>=0.2.0"])

    def no_cmru(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "requires", no_cmru)
    assert doctor.check_cli_extended().status == "warn"
    _fake_metadata(monkeypatch, installed="dev", requires=["pytest>=8", "cli-extended>=0.2.0"])
    assert doctor.check_cli_extended().status == "warn"
    _fake_metadata(monkeypatch, installed="0.3.0", requires=["pytest>=8", "cli-extended>=0.2.0"])
    assert doctor.check_cli_extended().status == "ok"


def test_doctor_json_shape_lists_the_six_checks_and_the_automatic_skills_check(monkeypatch, isolated, capsys):
    monkeypatch.setattr(doctor, "_which", lambda name: None)
    _fake_metadata(monkeypatch, installed="0.2.0", requires=["cli-extended>=0.2.0"])
    code, out, _ = _doctor(["--json"], capsys)
    report = json.loads(out)
    assert [check["name"] for check in report["checks"]] == [
        "git", "docker", "config", "credentials", "images", "cli-extended", "skills",
    ]
    assert set(report) == {"tool", "version", "checks", "summary"} and report["tool"] == "cmru"
    for check in report["checks"]:
        assert set(check) == {"name", "status", "summary", "remedy", "details"}
    by_name = {check["name"]: check for check in report["checks"]}
    assert by_name["git"]["status"] == "fail" and by_name["config"]["status"] == "skip"
    assert by_name["credentials"]["status"] == "warn" and by_name["skills"]["status"] == "warn"
    assert code == 1 and report["summary"]["fail"] >= 2


def test_doctor_exit_is_zero_without_a_fail_and_check_selects_one(monkeypatch, isolated, capsys):
    monkeypatch.setattr(doctor, "_which", lambda name: None)
    code, out, _ = _doctor(["--check", "credentials", "--check", "config"], capsys)
    assert code == 0  # a warn and a skip are not a failure
    assert "[WARN] credentials" in out and "[SKIP] config" in out and "git" not in out
    code, _, err = _doctor(["--check", "nope"], capsys)
    assert code == 2
    code, out, _ = _doctor(["--check", "git"], capsys)
    assert code == 1 and "[FAIL] git" in out and "remedy:" in out


def test_a_crashing_check_is_a_fail_not_a_traceback(monkeypatch, isolated, capsys):
    def boom(name):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(doctor, "_which", boom)
    code, out, err = _doctor(["--check", "git"], capsys)
    assert code == 1 and "check crashed: RuntimeError" in out and "Traceback" not in out + err
