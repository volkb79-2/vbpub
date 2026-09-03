"""Unit suite for tools/buildkite/ — the two Buildkite seams of
REMOTE-LANES-BUILDKITE.md (§3 pipeline generator, §4 trigger/collector).

Both scripts are exercised WITHOUT a network and WITHOUT a Buildkite account:

* `pipeline.sh` is pure output, so the real script runs against a fake project
  directory whose `run-gate.py` is a stub printing a fixed `--list` table. That
  is the whole contract between them — `name<TAB>kind<TAB>environment`, one
  lane per line — so a stub is a faithful stand-in, and no test here may parse
  `run-gate.toml` any more than the generator may.
* `bk-lane.sh` is exercised only through `--dry-run`, which prints the curl
  invocations it would make (with the bearer token redacted) and touches
  nothing. NO TEST IN THIS FILE MAKES A NETWORK CALL. The live path has
  therefore never been run; that is stated in the manual too.

No test in this file starts a container either.
"""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

try:                                    # optional: strengthens the YAML asserts
    import yaml
except ImportError:                     # pragma: no cover - depends on the venv
    yaml = None

TOOLS = Path(__file__).resolve().parent.parent / "tools" / "buildkite"
PIPELINE_SH = TOOLS / "pipeline.sh"
BK_LANE_SH = TOOLS / "bk-lane.sh"

LISTING = "mutation\tassay\ttester-unified\n" \
          "nightly-properties\tassay\ttester-unified\n" \
          "selftest\tcommand\thost\n"


def _stub_project(root: Path, listing: str = LISTING) -> Path:
    """A directory that answers `./run-gate.py --list` and nothing else."""
    root.mkdir(parents=True, exist_ok=True)
    script = root / "run-gate.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:] != ['--list']:\n"
        "    sys.exit('stub: only --list is implemented')\n"
        "sys.stdout.write(%r)\n" % listing
    )
    script.chmod(0o755)
    return root


def _uncommented(script: Path) -> str:
    return "\n".join(ln for ln in script.read_text().splitlines()
                     if not ln.lstrip().startswith("#"))


def _run(script: Path, *args, cwd=None, env=None):
    environ = dict(os.environ)
    environ.pop("RUN_GATE_LANES", None)
    environ.pop("RUN_GATE_QUEUE", None)
    environ.pop("RUN_GATE_TIMEOUT_MINUTES", None)
    environ.update(env or {})
    return subprocess.run(
        [str(script), *args], cwd=str(cwd) if cwd else None, env=environ,
        capture_output=True, text=True)


# ---------------------------------------------------------------------------
# seam 2 — tools/buildkite/pipeline.sh
# ---------------------------------------------------------------------------

def test_pipeline_emits_the_section_3_step_shape(tmp_path):
    """Every key of the §3 step, for every lane the listing shows, in order."""
    proj = _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "gate-alpha"})
    assert res.returncode == 0, res.stderr
    assert res.stdout == textwrap.dedent("""\
        steps:
          - label: "run-gate: mutation on gate-alpha"
            command: "cd proj && ./run-gate.py mutation"
            agents:
              queue: "gate-alpha"
            concurrency: 1
            concurrency_group: "gate/gate-alpha"
            timeout_in_minutes: 300
            artifact_paths:
              - "proj/.assay/**/*"
              - "proj/.run-gate/history.json"
            env:
              RUN_GATE_LANE: "mutation"
          - label: "run-gate: nightly-properties on gate-alpha"
            command: "cd proj && ./run-gate.py nightly-properties"
            agents:
              queue: "gate-alpha"
            concurrency: 1
            concurrency_group: "gate/gate-alpha"
            timeout_in_minutes: 300
            artifact_paths:
              - "proj/.assay/**/*"
              - "proj/.run-gate/history.json"
            env:
              RUN_GATE_LANE: "nightly-properties"
          - label: "run-gate: selftest on gate-alpha"
            command: "cd proj && ./run-gate.py selftest"
            agents:
              queue: "gate-alpha"
            concurrency: 1
            concurrency_group: "gate/gate-alpha"
            timeout_in_minutes: 300
            artifact_paths:
              - "proj/.assay/**/*"
              - "proj/.run-gate/history.json"
            env:
              RUN_GATE_LANE: "selftest"
        """)
    assert proj.joinpath("run-gate.py").exists()


@pytest.mark.skipif(yaml is None, reason="PyYAML not importable in this venv")
def test_pipeline_output_parses_as_the_documented_pipeline(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "gate-alpha",
                    "RUN_GATE_LANES": "mutation"})
    assert res.returncode == 0, res.stderr
    doc = yaml.safe_load(res.stdout)
    assert list(doc) == ["steps"]
    (step,) = doc["steps"]
    assert step == {
        "label": "run-gate: mutation on gate-alpha",
        "command": "cd proj && ./run-gate.py mutation",
        "agents": {"queue": "gate-alpha"},
        "concurrency": 1,
        "concurrency_group": "gate/gate-alpha",
        "timeout_in_minutes": 300,
        "artifact_paths": ["proj/.assay/**/*", "proj/.run-gate/history.json"],
        "env": {"RUN_GATE_LANE": "mutation"},
    }


def test_pipeline_default_project_is_dot_and_emits_no_cd(tmp_path):
    proj = _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, cwd=proj, env={"RUN_GATE_QUEUE": "q1",
                                           "RUN_GATE_LANES": "selftest"})
    assert res.returncode == 0, res.stderr
    assert '    command: "./run-gate.py selftest"\n' in res.stdout
    assert '      - ".assay/**/*"\n' in res.stdout
    assert '      - ".run-gate/history.json"\n' in res.stdout
    assert "cd " not in res.stdout


def test_pipeline_selects_named_lanes_in_the_requested_order(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj/", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "gate-beta",
                    "RUN_GATE_LANES": "  selftest   mutation "})
    assert res.returncode == 0, res.stderr
    labels = [ln.strip() for ln in res.stdout.splitlines() if "label:" in ln]
    assert labels == ['- label: "run-gate: selftest on gate-beta"',
                      '- label: "run-gate: mutation on gate-beta"']
    # the trailing slash of "proj/" is normalized away, not doubled
    assert "proj//" not in res.stdout


def test_pipeline_timeout_comes_from_the_environment(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1", "RUN_GATE_LANES": "selftest",
                    "RUN_GATE_TIMEOUT_MINUTES": "720"})
    assert res.returncode == 0, res.stderr
    assert "    timeout_in_minutes: 720\n" in res.stdout


def test_pipeline_refuses_a_bad_timeout(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1", "RUN_GATE_TIMEOUT_MINUTES": "5m"})
    assert res.returncode == 2
    assert "RUN_GATE_TIMEOUT_MINUTES='5m'" in res.stderr


def test_pipeline_refuses_an_unknown_lane_by_name(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1",
                    "RUN_GATE_LANES": "selftest nosuchlane"})
    assert res.returncode == 2
    assert "nosuchlane" in res.stderr
    assert "mutation" in res.stderr and "selftest" in res.stderr  # what it does show
    assert res.stdout == ""


def test_pipeline_refuses_a_missing_queue_by_name(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path)
    assert res.returncode == 2
    assert "RUN_GATE_QUEUE" in res.stderr
    assert res.stdout == ""


def test_pipeline_refuses_an_unquotable_queue(tmp_path):
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": 'q" x'})
    assert res.returncode == 2
    assert "RUN_GATE_QUEUE" in res.stderr


def test_pipeline_refuses_a_listing_with_a_fourth_column(tmp_path):
    """The generator is written against name<TAB>kind<TAB>environment; a wider
    listing means the contract moved, and guessing is how a second parser is
    born."""
    _stub_project(tmp_path / "proj", listing="lint\tcommand\thost\tremote\n")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 2
    assert "three documented columns" in res.stderr


def test_pipeline_skips_a_kind_it_was_not_written_against(tmp_path):
    _stub_project(tmp_path / "proj",
                  listing="futurish\tsomethingelse\thost\nselftest\tcommand\thost\n")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 0, res.stderr
    assert "futurish" not in res.stdout
    assert "selftest" in res.stdout


def test_pipeline_refuses_when_no_lane_survives(tmp_path):
    _stub_project(tmp_path / "proj", listing="futurish\tsomethingelse\thost\n")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 2
    assert "nothing to run remotely" in res.stderr


def test_pipeline_refuses_a_project_without_run_gate_py(tmp_path):
    (tmp_path / "empty").mkdir()
    res = _run(PIPELINE_SH, "empty", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 2
    assert "run-gate.py" in res.stderr


def test_pipeline_refuses_when_the_listing_itself_fails(tmp_path):
    proj = _stub_project(tmp_path / "proj")
    (proj / "run-gate.py").write_text("#!/usr/bin/env python3\n"
                                      "raise SystemExit('broken config')\n")
    (proj / "run-gate.py").chmod(0o755)
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path, env={"RUN_GATE_QUEUE": "q1"})
    assert res.returncode == 2
    assert "--list' failed" in res.stderr


def test_pipeline_help_exits_zero_and_names_the_env_contract():
    res = _run(PIPELINE_SH, "--help")
    assert res.returncode == 0
    for key in ("RUN_GATE_QUEUE", "RUN_GATE_LANES", "RUN_GATE_TIMEOUT_MINUTES"):
        assert key in res.stdout


def test_pipeline_never_reads_run_gate_toml():
    """The anti-goal, pinned as a test: no second parser of run-gate.toml."""
    assert "run-gate.toml" not in _uncommented(PIPELINE_SH)


# ---------------------------------------------------------------------------
# seam 4 — tools/buildkite/bk-lane.sh (--dry-run only; no network, ever)
# ---------------------------------------------------------------------------

TOKEN = "bkua-secret-do-not-print"
API = "https://api.buildkite.com/v2/organizations/acme/pipelines/vbpub/builds"


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "api-token"
    path.write_text(TOKEN + "\n")
    path.chmod(0o600)
    return path


@pytest.fixture(scope="module")
def git_repo(tmp_path_factory):
    """A one-commit repo on a named branch: `run` reads commit+branch from git."""
    root = tmp_path_factory.mktemp("bk-repo")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(root)], check=True,
                   env=env, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty",
                    "-m", "seed"], check=True, env=env, capture_output=True)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return root, head


def _bk(*args, token_file=None, cwd=None, **overrides):
    env = dict(os.environ, BK_ORG="acme", BK_PIPELINE="vbpub")
    env.pop("BK_POLL_SECONDS", None)
    if token_file is not None:
        env["BK_TOKEN_FILE"] = str(token_file)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run([str(BK_LANE_SH), *args], env=env,
                          cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True)


def test_bk_run_dry_run_prints_the_post_with_a_redacted_token(token_file, git_repo):
    root, head = git_repo
    res = _bk("--dry-run", "run", "mutation", "nightly-properties",
              token_file=token_file, cwd=root)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    post = next(ln for ln in lines if "-X" in ln and "POST" in ln)
    assert post.startswith("curl ")
    assert "'POST'" in post and "'-X'" in post
    assert "'%s'" % API in post
    assert "'Authorization: Bearer <redacted>'" in post
    assert "'Content-Type: application/json'" in post
    # the JSON body: commit, branch, and the lane selection the generator reads
    assert '"commit": "%s"' % head in post
    assert '"branch": "trunk"' in post
    assert '"env": {"RUN_GATE_LANES": "mutation nightly-properties"}' in post
    assert '"message": "run-gate: mutation nightly-properties"' in post
    # the poll it would then do, and the terminal states it waits for
    assert "curl '-fsS' '%s/<build-number>'" % API in res.stdout
    for state in ("passed", "failed", "canceled", "blocked", "skipped",
                  "not_run", "waiting_failed"):
        assert state in res.stdout
    assert TOKEN not in res.stdout and TOKEN not in res.stderr


def test_bk_run_dry_run_honours_the_poll_interval(token_file, git_repo):
    root, _ = git_repo
    res = _bk("--dry-run", "run", "selftest", token_file=token_file, cwd=root,
              BK_POLL_SECONDS="5")
    assert res.returncode == 0, res.stderr
    assert "poll every 5s" in res.stdout


def test_bk_status_dry_run_prints_one_get(token_file):
    res = _bk("--dry-run", "status", "1234", token_file=token_file)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == (
        "curl '-fsS' '%s/1234' '-H' 'Authorization: Bearer <redacted>'" % API)
    assert TOKEN not in res.stdout


def test_bk_collect_dry_run_prints_the_verified_artifacts_endpoint(token_file,
                                                                   tmp_path):
    res = _bk("--dry-run", "collect", "1234", str(tmp_path / "in"),
              token_file=token_file)
    assert res.returncode == 0, res.stderr
    assert "curl '-fsS' '%s/1234' '-H' 'Authorization: Bearer <redacted>'" % API \
        in res.stdout
    assert "curl '-fsS' '%s/1234/artifacts' '-H' 'Authorization: Bearer <redacted>'" \
        % API in res.stdout
    assert "'-o' '%s/<commit>/<artifact path>'" % (tmp_path / "in") in res.stdout
    assert TOKEN not in res.stdout
    assert not (tmp_path / "in").exists()      # dry-run creates nothing


def test_bk_collect_refuses_without_a_destination(token_file):
    res = _bk("--dry-run", "collect", "1234", token_file=token_file)
    assert res.returncode == 2
    assert "destination directory" in res.stderr


def test_bk_refuses_a_non_numeric_build(token_file):
    res = _bk("--dry-run", "status", "latest", token_file=token_file)
    assert res.returncode == 2
    assert "build number 'latest'" in res.stderr


def test_bk_refuses_a_token_file_that_is_not_0600(token_file):
    token_file.chmod(0o644)
    res = _bk("--dry-run", "status", "1", token_file=token_file)
    assert res.returncode == 2
    assert "mode 644" in res.stderr and "0600" in res.stderr
    assert TOKEN not in res.stdout and TOKEN not in res.stderr


def test_bk_refuses_a_missing_token_file(tmp_path):
    res = _bk("--dry-run", "status", "1", token_file=tmp_path / "absent")
    assert res.returncode == 2
    assert "does not exist" in res.stderr


def test_bk_refuses_an_empty_token_file(tmp_path):
    path = tmp_path / "api-token"
    path.write_text("")
    path.chmod(0o600)
    res = _bk("--dry-run", "status", "1", token_file=path)
    assert res.returncode == 2
    assert "is empty" in res.stderr


@pytest.mark.parametrize("missing", ["BK_ORG", "BK_PIPELINE"])
def test_bk_refuses_a_missing_env_var_by_name(token_file, missing):
    res = _bk("--dry-run", "status", "1", token_file=token_file,
              **{missing: None})
    assert res.returncode == 2
    assert missing in res.stderr


def test_bk_refuses_a_bad_poll_interval(token_file):
    res = _bk("--dry-run", "status", "1", token_file=token_file,
              BK_POLL_SECONDS="0")
    assert res.returncode == 2
    assert "BK_POLL_SECONDS='0'" in res.stderr


def test_bk_refuses_an_unknown_verb_and_an_unknown_option(token_file):
    res = _bk("--dry-run", "trigger", token_file=token_file)
    assert res.returncode == 2
    assert "unknown verb 'trigger'" in res.stderr
    res = _bk("--wait", "status", "1", token_file=token_file)
    assert res.returncode == 2
    assert "unknown option '--wait'" in res.stderr


def test_bk_refuses_a_lane_name_it_cannot_quote(token_file, git_repo):
    root, _ = git_repo
    res = _bk("--dry-run", "run", "lane;rm -rf /", token_file=token_file, cwd=root)
    assert res.returncode == 2
    assert "lane name" in res.stderr


def test_bk_run_needs_at_least_one_lane(token_file, git_repo):
    root, _ = git_repo
    res = _bk("--dry-run", "run", token_file=token_file, cwd=root)
    assert res.returncode == 2
    assert "at least one lane" in res.stderr


def test_bk_run_refuses_outside_a_git_work_tree(token_file, tmp_path):
    outside = tmp_path / "nogit"
    outside.mkdir()
    res = _bk("--dry-run", "run", "selftest", token_file=token_file, cwd=outside)
    assert res.returncode == 2
    assert "git work tree" in res.stderr


def test_bk_help_exits_zero_and_names_the_env_contract():
    res = _bk("--help")
    assert res.returncode == 0
    for key in ("BK_ORG", "BK_PIPELINE", "BK_TOKEN_FILE", "BK_POLL_SECONDS"):
        assert key in res.stdout


def test_bk_lane_never_reads_run_gate_toml():
    assert "run-gate.toml" not in _uncommented(BK_LANE_SH)


def test_both_tools_are_executable_and_need_only_documented_binaries():
    for script in (PIPELINE_SH, BK_LANE_SH):
        assert os.access(script, os.X_OK), script
        assert script.read_text().startswith("#!/usr/bin/env bash\n")
    for binary in ("bash", "git", "curl", "python3"):
        assert shutil.which(binary), binary
    # jq is deliberately not a dependency of either script
    assert "jq " not in _uncommented(BK_LANE_SH)
