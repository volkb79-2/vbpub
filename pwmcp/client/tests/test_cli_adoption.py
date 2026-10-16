"""cli-extended adoption of the ``pwmcp`` console script (PWMCP-02, AC-01..AC-25)."""

from __future__ import annotations

import json
import re
import runpy
import sys
import tomllib
from pathlib import Path

import pytest

import cli_extended
import pwmcp_client
from cli_extended import (
    CliIdentity,
    VersionLookupError,
    assert_cli_contract,
    invoke_module,
    make_invoker,
    validate_skill_source,
)
from cli_extended import identity as identity_module
from pwmcp_client import cli as cli_module
from pwmcp_client import diagnostics, session

CLIENT = Path(__file__).resolve().parents[1]
SRC = CLIENT / "src"
PWMCP = CLIENT.parent
VERSION = (SRC / "pwmcp_client" / "VERSION").read_text(encoding="utf-8").strip()
IDENTITY = CliIdentity("PWMCP", VERSION, "client utility", "pwmcp")


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "release": "1.63.0-r2",
        "playwright": {"python": "1.63.0", "protocol": "1.63"},
        "endpoints": {"playwright_ws": "ws://pwmcp:3000/", "health": "http://pwmcp:3000/health"},
        "run_server": {
            "limits": {
                "default_lease_seconds": 10,
                "max_lease_seconds": 20,
                "max_clients": 2,
                "idle_recycle_seconds": 30,
            }
        },
    }


@pytest.fixture
def contract_file(tmp_path: Path) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    return path


def _run(tmp_path: Path, *argv: str):
    """Run the real CLI in a subprocess with a hermetic HOME (never the real one)."""
    return invoke_module("pwmcp_client.cli", argv, home=tmp_path / "home", pythonpath=[SRC], cwd=tmp_path)


# --- AC-21: the real executable obeys the shared CLI contract -----------------------


def test_real_executable_obeys_cli_contract(tmp_path: Path) -> None:
    invoke = make_invoker(
        "pwmcp_client.cli", module=True, home=tmp_path / "home", pythonpath=[SRC], cwd=tmp_path
    )
    assert_cli_contract(
        invoke,
        IDENTITY,
        ("contract", "doctor", "inspect", "skills"),
        invalid_invocations={
            "unknown verb": ["bogus"],
            "contract operand": ["contract", "extra"],
            "doctor bad check option": ["doctor", "--nope"],
            "inspect missing URL": ["inspect"],
        },
        known_verb_errors={
            "contract operand": "contract",
            "doctor bad check option": "doctor",
            "inspect missing URL": "inspect",
        },
    )


# --- golden behaviour kept from the hand-rolled CLI ---------------------------------


def test_version_is_exactly_one_line_on_stdout(tmp_path: Path) -> None:
    result = _run(tmp_path, "--version")
    assert (result.returncode, result.stdout, result.stderr) == (0, f"pwmcp {VERSION}\n", "")


def test_contract_output_and_exit_code_are_the_golden_indented_sorted_json(
    tmp_path: Path, contract_file: Path
) -> None:
    for argv in (("contract", "--contract", str(contract_file)), ("--contract", str(contract_file), "contract")):
        result = _run(tmp_path, *argv)
        assert result.returncode == 0
        assert result.stderr == ""
        document = json.loads(result.stdout)
        assert result.stdout == json.dumps(document, indent=2, sort_keys=True) + "\n"
        assert document["release"] == "1.63.0-r2"
        assert document["ws_url"] == "ws://pwmcp:3000/"


def test_contract_json_mode_is_one_parseable_line(tmp_path: Path, contract_file: Path) -> None:
    result = _run(tmp_path, "contract", "--json", "--contract", str(contract_file))
    assert result.returncode == 0
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["max_clients"] == 2


def test_contract_failure_is_one_error_line_without_traceback(tmp_path: Path) -> None:
    # Intentional change (AC-10/AC-11): an unreadable contract was a traceback.
    result = _run(tmp_path, "contract", "--contract", str(tmp_path / "missing.json"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("[ERROR] cannot load the pwmcp contract from")
    assert "FileNotFoundError" in result.stderr
    assert "Traceback" not in result.stderr


def test_contract_failure_in_process_names_the_source_and_hints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.json"
    assert cli_module.main(["contract", "--contract", str(missing)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"[ERROR] cannot load the pwmcp contract from {missing}" in captured.err
    assert "Hint: start the pwmcp stack or pass --contract URL_OR_FILE" in captured.err


def test_malformed_contract_is_a_domain_failure(tmp_path: Path) -> None:
    for text in ("{", "[]", json.dumps({"release": "x"})):
        path = tmp_path / "bad.json"
        path.write_text(text, encoding="utf-8")
        result = _run(tmp_path, "contract", "--contract", str(path))
        assert result.returncode == 1, text
        assert "Traceback" not in result.stderr


def test_nothing_in_this_cli_mutates_so_dry_run_is_not_offered(tmp_path: Path, contract_file: Path) -> None:
    # AC-12: contract and doctor are read-only; the library offers --dry-run/--yes only
    # on mutating verbs, and `skills install` is the only such verb here.
    for verb in ("contract", "doctor"):
        for flag in ("--dry-run", "--yes"):
            assert _run(tmp_path, verb, flag, "--contract", str(contract_file)).returncode == 2


# --- AC-20: the shared doctor --------------------------------------------------------


def test_doctor_reports_each_named_check(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    contract = pwmcp_client.PwmcpContract.from_dict(_payload())
    monkeypatch.setattr(diagnostics, "load_contract", lambda source: contract)
    monkeypatch.setattr(diagnostics, "verify_installed_playwright", lambda value: "1.63.4")
    assert cli_module.main(["doctor", "--json", "--check", "contract", "--check", "playwright"]) == 0
    report = json.loads(capsys.readouterr().out)
    by_name = {check["name"]: check for check in report["checks"]}
    assert by_name["contract"]["status"] == "ok"
    assert by_name["contract"]["details"]["release"] == "1.63.0-r2"
    assert by_name["playwright"]["details"] == {"installed": "1.63.4", "required": "1.63.0"}


def test_doctor_fails_with_a_remedy_on_a_playwright_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    contract = pwmcp_client.PwmcpContract.from_dict(_payload())
    monkeypatch.setattr(diagnostics, "load_contract", lambda source: contract)

    def mismatch(value: object) -> str:
        raise session.VersionMismatch("Playwright client 1.62.0 does not match pwmcp protocol 1.63")

    monkeypatch.setattr(diagnostics, "verify_installed_playwright", mismatch)
    assert cli_module.main(["doctor", "--check", "playwright"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] playwright: Playwright client 1.62.0 does not match pwmcp protocol 1.63" in out
    assert "remedy: pip install playwright==1.63.0" in out


def test_doctor_skips_playwright_and_fails_contract_when_the_server_is_unreachable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = str(tmp_path / "nope.json")
    assert cli_module.main(["doctor", "--contract", missing, "--check", "contract", "--check", "playwright"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] contract: cannot load the pwmcp contract from" in out
    assert "[SKIP] playwright: the pwmcp contract is unavailable" in out


def test_doctor_unknown_check_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.main(["doctor", "--check", "bogus"]) == 2
    assert "unknown doctor check 'bogus'" in capsys.readouterr().err


def test_load_problem_is_one_line_even_for_a_multiline_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(source: str) -> None:
        raise OSError("first\nsecond")

    monkeypatch.setattr(diagnostics, "load_contract", boom)
    contract, problem = diagnostics.try_load_contract("x")
    assert contract is None
    assert problem == "OSError: first second"


# --- AC-01/AC-02: identity with no literal fallback ----------------------------------


def test_identity_reads_the_packaged_version_file() -> None:
    assert cli_module.identity().version == VERSION
    assert IDENTITY.headline == f"PWMCP {VERSION} — client utility"


def test_version_attribute_is_the_resolved_identity_version() -> None:
    assert pwmcp_client.__version__ == VERSION
    with pytest.raises(AttributeError, match="no attribute 'nothing'"):
        pwmcp_client.nothing  # noqa: B018


def test_identity_has_no_literal_fallback_when_no_source_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_installed(name: str) -> str:
        raise identity_module.PackageNotFoundError(name)

    monkeypatch.setattr(identity_module, "installed_version", not_installed)
    with pytest.raises(VersionLookupError):
        cli_module.identity(tmp_path / "VERSION-missing")


def test_identity_refuses_metadata_that_disagrees_with_the_version_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity_module, "installed_version", lambda name: "99.0.0")
    with pytest.raises(VersionLookupError, match="disagree"):
        cli_module.identity()


def test_main_exits_3_with_one_line_when_the_version_cannot_be_determined(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> None:
        raise VersionLookupError("no version source")

    monkeypatch.setattr(cli_module, "identity", broken)
    assert cli_module.main(["--version"]) == cli_module.EXIT_NOT_INSTALLED == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "[ERROR] cannot determine the pwmcp-client version: no version source\n"


@pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
def test_module_entrypoint_runs_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["pwmcp", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("pwmcp_client.cli", run_name="__main__")
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"pwmcp {VERSION}\n"


# --- AC-19: skills ship as package data ----------------------------------------------


def test_the_skill_is_packaged_data_and_valid_and_no_claude_skills_tree_remains() -> None:
    source = SRC / "pwmcp_client" / "skills"
    assert (source / "pwmcp-fetch" / "SKILL.md").is_file()
    files = {
        str(path.relative_to(source / "pwmcp-fetch")): path.read_bytes()
        for path in (source / "pwmcp-fetch").rglob("*")
        if path.is_file()
    }
    validate_skill_source("pwmcp-fetch", files)  # raises SkillError on any invalid skill
    assert not (PWMCP / ".claude" / "skills").exists()
    package_data = tomllib.loads((CLIENT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "skills/**/*" in package_data["tool"]["setuptools"]["package-data"]["pwmcp_client"]


def test_skills_install_check_and_dry_run_use_only_the_given_destination(tmp_path: Path) -> None:
    dest = tmp_path / "project" / ".claude" / "skills"
    preview = _run(tmp_path, "skills", "install", "--dest", str(dest), "--dry-run")
    assert preview.returncode == 0
    assert not dest.exists()

    installed = _run(tmp_path, "skills", "install", "--dest", str(dest))
    assert installed.returncode == 0, installed.stderr
    assert (dest / "pwmcp-fetch" / "SKILL.md").is_file()

    assert _run(tmp_path, "skills", "check", "--dest", str(dest)).returncode == 0
    (dest / "pwmcp-fetch" / "SKILL.md").write_text("tampered\n", encoding="utf-8")
    assert _run(tmp_path, "skills", "check", "--dest", str(dest)).returncode == 1
    # The hermetic HOME received nothing outside the destination.
    assert not (tmp_path / "home" / ".claude").exists()


def test_skills_group_accepts_the_consumer_global_option(tmp_path: Path, contract_file: Path) -> None:
    # CLI-EXT-26 (the reason for the 0.3.0 floor): --contract is forwarded to `skills`.
    result = _run(tmp_path, "skills", "list", "--contract", str(contract_file), "--dest", str(tmp_path / "s"))
    assert result.returncode == 0, result.stderr


# --- AC-16..AC-18, AC-22: the committed surface and the adoption audit ----------------


def _library_cli(tmp_path: Path, *argv: str):
    return invoke_module(
        "cli_extended.cli",
        [*argv, "--config", str(CLIENT / "pyproject.toml")],
        home=tmp_path / "home",
        pythonpath=[SRC],
        cwd=PWMCP,
    )


def test_committed_manifest_spec_and_catalog_are_current(tmp_path: Path) -> None:
    # `cli-extended surface check`: a grammar change without `surface sync` plus a
    # reviewed catalog update (docs/cli-review.toml) fails here.
    result = _library_cli(tmp_path, "surface", "check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLI surface check passed." in result.stdout + result.stderr


def test_adoption_audit_has_no_fail_and_nothing_left_to_judge(tmp_path: Path) -> None:
    result = _library_cli(tmp_path, "audit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[FAIL]" not in result.stdout and "[MANUAL]" not in result.stdout
    assert "0 fail, 0 manual" in result.stdout


# --- AC-24/AC-25: a real wheel dependency, never vendored, no path hacks --------------

_HACK = re.compile(r"sys\.path\.(?:insert|append)|PYTHONPATH|libraries[/\\]cli-extended|cmru[\"'/\\ ]*/?[\"']?src")


def test_cli_extended_is_a_declared_wheel_dependency_with_a_floor_and_reason() -> None:
    text = (CLIENT / "pyproject.toml").read_text(encoding="utf-8")
    project = tomllib.loads(text)
    runtime_requirements = [
        line.partition("#")[0].strip()
        for line in (CLIENT / "requirements-runtime.txt").read_text(encoding="utf-8").splitlines()
        if line.partition("#")[0].strip()
    ]
    assert project["project"]["dependencies"] == runtime_requirements
    assert runtime_requirements[0] == "cli-extended>=0.3.0"
    contract = json.loads((PWMCP / "pwmcp.contract.json").read_text(encoding="utf-8"))
    assert runtime_requirements[1] == f"playwright=={contract['playwright']['python']}"
    assert "package-dir" not in project["tool"]["setuptools"]
    assert "REAL wheel dependency" in text and "Floor 0.3.0" in text


def test_cli_extended_is_the_installed_library_not_a_vendored_copy() -> None:
    library = Path(cli_extended.__file__).resolve()
    for root in (PWMCP, PWMCP.parent / "pwmcp"):
        assert not library.is_relative_to(root.resolve() / "client"), library  # cli-extended: allow-path-assertion
        assert not library.is_relative_to(root.resolve() / "scripts"), library  # cli-extended: allow-path-assertion
    for directory in (SRC, PWMCP / "scripts", PWMCP):
        assert not (directory / "cli_extended").exists(), directory


def test_client_sources_put_no_library_or_cmru_source_on_sys_path() -> None:
    for path in sorted(SRC.rglob("*.py")):
        offending = [line for line in path.read_text(encoding="utf-8").splitlines() if _HACK.search(line)]
        assert not offending, (path, offending)
