"""Unit suite for tools/buildkite/ — the two Buildkite seams of
REMOTE-LANES-BUILDKITE.md (§3 pipeline generator, §4 trigger/collector).

Both scripts are exercised WITHOUT a network and WITHOUT a Buildkite account:

* `pipeline.sh` is pure output, so the real script runs against a fake project
  directory whose `run-gate.py` is a stub printing a fixed `--list` table. That
  is the whole contract between them — `name<TAB>kind<TAB>environment`, one
  lane per line — so a stub is a faithful stand-in, and no test here may parse
  `run-gate.toml` any more than the generator may.
* `bk-lane.sh` is exercised two ways, neither of which reaches Buildkite:
  through `--dry-run`, which prints the curl invocations it would make (with
  the bearer token redacted) and touches nothing; and through the LIVE code
  path with a `curl` (and `sleep`) stub on `PATH`, which is what covers the
  exit-code contract, all seven terminal states, the wait budget and the two
  containment guards on `collect`. NO TEST IN THIS FILE MAKES A NETWORK CALL —
  the stub is a shell script that reads a canned response out of the
  environment. A real build has still never been created; that is what the
  manual says too, and the stub does not change it.

No test in this file starts a container either.
"""

import json
import os
import re
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
              - "proj/.assay/*"
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
              - "proj/.assay/*"
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
              - "proj/.assay/*"
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
        "artifact_paths": ["proj/.assay/*", "proj/.assay/**/*",
                           "proj/.run-gate/history.json"],
        "env": {"RUN_GATE_LANE": "mutation"},
    }


def test_pipeline_default_project_is_dot_and_emits_no_cd(tmp_path):
    proj = _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, cwd=proj, env={"RUN_GATE_QUEUE": "q1",
                                           "RUN_GATE_LANES": "selftest"})
    assert res.returncode == 0, res.stderr
    assert '    command: "./run-gate.py selftest"\n' in res.stdout
    assert '      - ".assay/*"\n' in res.stdout
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


@pytest.mark.parametrize("value", ["0300", "030", "0"])
def test_pipeline_refuses_a_leading_zero_timeout(tmp_path, value):
    """N4: `timeout_in_minutes: 0300` is read by a YAML parser as OCTAL 192,
    so emitting it verbatim would silently quarter the budget."""
    assert yaml is None or yaml.safe_load("c: 0300")["c"] == 192   # the reason
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1", "RUN_GATE_LANES": "selftest",
                    "RUN_GATE_TIMEOUT_MINUTES": value})
    assert res.returncode == 2
    assert "RUN_GATE_TIMEOUT_MINUTES='%s'" % value in res.stderr


def test_pipeline_refuses_a_duplicate_lane_name(tmp_path):
    """N6: two identical steps with identical labels is never what was meant."""
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1",
                    "RUN_GATE_LANES": "selftest mutation selftest"})
    assert res.returncode == 2
    assert "more than once" in res.stderr
    assert "selftest" in res.stderr


def test_pipeline_names_a_repeated_unknown_lane_once(tmp_path):
    """N7: `RUN_GATE_LANES="nope nope"` used to list `nope` twice, reading like
    two different mistakes; duplicates are judged over every requested name."""
    _stub_project(tmp_path / "proj")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1", "RUN_GATE_LANES": "nope nope"})
    assert res.returncode == 2
    assert res.stderr.count("nope") == 1


def test_pipeline_does_not_glob_the_lane_selection(tmp_path):
    """N5: `RUN_GATE_LANES='*'` used to expand against the working directory,
    so a FILE called `selftest` could enter the selection as a lane."""
    _stub_project(tmp_path / "proj")
    (tmp_path / "selftest").write_text("decoy\n")
    (tmp_path / "decoyfile").write_text("decoy\n")
    res = _run(PIPELINE_SH, "proj", cwd=tmp_path,
               env={"RUN_GATE_QUEUE": "q1", "RUN_GATE_LANES": "*"})
    assert res.returncode == 2
    assert "'*'" in res.stderr or "* that" in res.stderr   # the literal name
    assert "decoyfile" not in res.stderr                   # nothing expanded
    assert res.stdout == ""


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
# seam 4 — tools/buildkite/bk-lane.sh, dry-run half (no network, ever; the
# live half, with curl stubbed on PATH, follows further down)
# ---------------------------------------------------------------------------

TOKEN = "bkua-secret-do-not-print"
API = "https://api.buildkite.com/v2/organizations/acme/pipelines/vbpub/builds"
# Every request is bounded so a hung connection cannot hang the caller: the
# wait budget counts only sleep time, so these are what make the wall-clock
# bound finite (E5-R14). The two kinds are bounded differently (E5-R16): API
# reads by TOTAL time, artifact downloads by STALL — a total cap would fail a
# large healthy transfer.
API_BOUNDS = "'--connect-timeout' '10' '--max-time' '120'"
DOWNLOAD_BOUNDS = "'--connect-timeout' '10' '--speed-time' '60' '--speed-limit' '1024'"
GET = "curl '-fsS' " + API_BOUNDS


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
    for key in ("BK_POLL_SECONDS", "BK_QUEUE", "BK_MAX_WAIT_MINUTES"):
        env.pop(key, None)
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


# --- the live path, with `curl` stubbed on PATH (still no network) ----------

CURL_STUB = """\
#!/usr/bin/env bash
# Test double for curl: records the invocation, answers from the environment.
url=""; out=""; prev=""
for a in "$@"; do
  case "$prev" in -o) out=$a ;; esac
  case "$a" in http://*|https://*) url=$a ;; esac
  prev=$a
done
printf '%s\\n' "$*" >> "$FAKE_CALLS"
if [ -n "$out" ]; then
  # A download. FAKE_DL_FAIL makes it fail the way curl really does — a
  # non-zero exit and no file — which is a 404, a 5xx or the stall bound.
  if [ -n "${FAKE_DL_FAIL-}" ]; then
    echo "curl: (22) The requested URL returned error: 404" >&2
    exit 22
  fi
  printf '%s' "${FAKE_BODY-artifact-bytes}" > "$out"
  exit 0
fi
case "$url" in
  */artifacts) printf '%s' "$FAKE_ARTIFACTS" ;;
  *) printf '%s' "$FAKE_BUILD" ;;
esac
"""

SLEEP_STUB = "#!/usr/bin/env bash\n# Test double for sleep: the wait budget is\n" \
             "# counted in poll intervals, so it need not really elapse.\nexit 0\n"


@pytest.fixture
def stub_bin(tmp_path):
    """`curl` and `sleep` doubles on PATH — this is what makes bk-lane.sh's
    LIVE path testable without a network, a token or a Buildkite account."""
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    (bindir / "curl").write_text(CURL_STUB)
    (bindir / "sleep").write_text(SLEEP_STUB)
    for name in ("curl", "sleep"):
        (bindir / name).chmod(0o755)
    return bindir, tmp_path / "curl-calls.txt"


def _bk_live(stub_bin, *args, build=None, artifacts=None, body=None, **kwargs):
    bindir, calls = stub_bin
    env = {
        "PATH": "%s:%s" % (bindir, os.environ["PATH"]),
        "FAKE_CALLS": str(calls),
        "FAKE_BUILD": json.dumps(build if build is not None else
                                 {"number": 4242, "state": "passed",
                                  "commit": "abc123"}),
        "FAKE_ARTIFACTS": json.dumps(artifacts if artifacts is not None else []),
    }
    if body is not None:
        env["FAKE_BODY"] = body
    res = _bk(*args, **kwargs, **env)
    res.curl_calls = (calls.read_text().splitlines()
                      if calls.exists() else [])       # type: ignore[attr-defined]
    return res


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
    # the poll it would then do, and the terminal states it waits for. Matched
    # as WHOLE WORDS: a bare `"failed" in stdout` is satisfied by the substring
    # inside "waiting_failed", so it would pass with `failed` dropped entirely.
    assert "%s '%s/<build-number>'" % (GET, API) in res.stdout
    printed = set(re.findall(r"[A-Za-z_]+", res.stdout))
    assert {"passed", "failed", "canceled", "blocked", "skipped", "not_run",
            "waiting_failed"} <= printed
    assert TOKEN not in res.stdout and TOKEN not in res.stderr


def test_bk_run_sends_bk_queue_as_the_builds_own_run_gate_queue(token_file,
                                                                git_repo):
    """E5-R6: a build's env overrides the pipeline's env, so BK_QUEUE moves one
    run to another host's queue without editing the pipeline."""
    root, _ = git_repo
    res = _bk("--dry-run", "run", "mutation", token_file=token_file, cwd=root,
              BK_QUEUE="gate-beta")
    assert res.returncode == 0, res.stderr
    post = next(ln for ln in res.stdout.splitlines()
                if "-X" in ln and "POST" in ln)
    assert ('"env": {"RUN_GATE_LANES": "mutation", '
            '"RUN_GATE_QUEUE": "gate-beta"}') in post
    assert "RUN_GATE_QUEUE=gate-beta" in res.stdout      # said in prose too


def test_bk_run_omits_run_gate_queue_when_bk_queue_is_unset(token_file, git_repo):
    """Unset means the pipeline's own default queue stands — the key must not
    be sent empty, which would override it with nothing."""
    root, _ = git_repo
    res = _bk("--dry-run", "run", "mutation", token_file=token_file, cwd=root)
    assert res.returncode == 0, res.stderr
    post = next(ln for ln in res.stdout.splitlines()
                if "-X" in ln and "POST" in ln)
    assert '"env": {"RUN_GATE_LANES": "mutation"}' in post
    assert "RUN_GATE_QUEUE" not in res.stdout


def test_bk_run_refuses_an_unquotable_bk_queue(token_file, git_repo):
    root, _ = git_repo
    res = _bk("--dry-run", "run", "mutation", token_file=token_file, cwd=root,
              BK_QUEUE='gate beta"')
    assert res.returncode == 2
    assert "BK_QUEUE" in res.stderr


def test_every_dry_run_curl_line_is_bounded(token_file, git_repo, tmp_path):
    """E5-R14/E5-R16: the wait budget counts sleep time only, so an unbounded
    request would make the real wall-clock bound infinite. Every printed
    invocation is bounded, and the two kinds differently: the five API reads by
    total time, the artifact download by stall — with NO --max-time, which
    would fail a large healthy transfer. Real calls come from the same two
    arrays, so the printed lines cannot drift from them."""
    root, _ = git_repo
    runs = [_bk("--dry-run", "run", "lint", token_file=token_file, cwd=root),
            _bk("--dry-run", "status", "7", token_file=token_file),
            _bk("--dry-run", "collect", "7", str(tmp_path), token_file=token_file)]
    lines = []
    for res in runs:
        assert res.returncode == 0, res.stderr
        lines += [ln for ln in res.stdout.splitlines() if ln.startswith("curl ")]
    assert len(lines) == 6      # run: create + poll; status: 1; collect: 3
    downloads = [ln for ln in lines if "'-o'" in ln]
    api = [ln for ln in lines if "'-o'" not in ln]
    assert len(downloads) == 1 and len(api) == 5
    for line in api:
        assert API_BOUNDS in line, line
        assert "--speed-time" not in line, line
    for line in downloads:
        assert DOWNLOAD_BOUNDS in line, line
        assert "--max-time" not in line, line


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
        "%s '%s/1234' '-H' 'Authorization: Bearer <redacted>'" % (GET, API))
    assert TOKEN not in res.stdout


def test_bk_collect_dry_run_prints_the_verified_artifacts_endpoint(token_file,
                                                                   tmp_path):
    res = _bk("--dry-run", "collect", "1234", str(tmp_path / "in"),
              token_file=token_file)
    assert res.returncode == 0, res.stderr
    assert "%s '%s/1234' '-H' 'Authorization: Bearer <redacted>'" % (GET, API) \
        in res.stdout
    assert "%s '%s/1234/artifacts' '-H' 'Authorization: Bearer <redacted>'" \
        % (GET, API) in res.stdout
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
    # jq is deliberately not a dependency of either script. As a WHOLE WORD:
    # `"jq " not in ...` would pass for `| jq -r`, `/usr/bin/jq` or `jq\n`.
    for script in (PIPELINE_SH, BK_LANE_SH):
        assert not re.search(r"\bjq\b", _uncommented(script)), script


# ---------------------------------------------------------------------------
# seam 4, LIVE path — `curl` and `sleep` stubbed on PATH. Still no network:
# the stub is a shell script answering out of the environment.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state,expected_exit", [
    ("passed", 0),
    ("failed", 1),
    ("canceled", 1),
    ("blocked", 1),          # E5-R2: terminal, and NOT a pass
    ("skipped", 1),
    ("not_run", 1),
    ("waiting_failed", 1),
])
def test_bk_run_exit_code_for_every_terminal_state(stub_bin, token_file,
                                                   git_repo, state,
                                                   expected_exit):
    """The exit-code contract, on the path that actually implements it: 0 for
    `passed` and 1 for every other terminal state. Asserting the WORD appears
    in dry-run prose (what the suite used to do) would pass against a script
    that printed the word and then looped forever."""
    root, _ = git_repo
    res = _bk_live(stub_bin, "run", "lint", token_file=token_file, cwd=root,
                   build={"number": 77, "state": state, "commit": "abc123"})
    assert res.returncode == expected_exit, (res.stdout, res.stderr)
    assert "build 77 state: %s" % state in res.stdout
    assert any("-X POST" in call for call in res.curl_calls)
    assert TOKEN not in res.stdout and TOKEN not in res.stderr


def test_bk_run_after_verb_dry_run_makes_no_curl_call(stub_bin, token_file,
                                                      git_repo):
    """S1: `run --dry-run lint` used to create a REAL build, because the flag
    was taken as a lane name. With curl stubbed, "made no call" is checkable."""
    root, _ = git_repo
    res = _bk_live(stub_bin, "run", "--dry-run", "lint", token_file=token_file,
                   cwd=root)
    assert res.returncode == 0, res.stderr
    assert res.curl_calls == []
    assert "would create a build" in res.stdout
    assert "RUN_GATE_LANES" in res.stdout and "--dry-run" not in res.stdout


def test_bk_run_refuses_a_lane_name_starting_with_a_dash(token_file, git_repo):
    root, _ = git_repo
    res = _bk("run", "-lint", token_file=token_file, cwd=root)
    assert res.returncode == 2
    assert "-lint" in res.stderr
    # and after `--`, where it reaches the lane check itself
    res = _bk("run", "--", "-lint", token_file=token_file, cwd=root)
    assert res.returncode == 2
    assert "starts with '-'" in res.stderr


def test_bk_run_gives_up_waiting_with_exit_3(stub_bin, token_file, git_repo):
    """E5-R10: a build parked in a non-terminal state (a mistyped BK_QUEUE is
    the way in) must not spin forever unattended."""
    root, _ = git_repo
    res = _bk_live(stub_bin, "run", "lint", token_file=token_file, cwd=root,
                   build={"number": 99, "state": "scheduled", "commit": "abc"},
                   BK_MAX_WAIT_MINUTES="1", BK_POLL_SECONDS="30")
    assert res.returncode == 3, (res.stdout, res.stderr)
    assert "build 99 is still scheduled after 1 minutes" in res.stderr
    assert "status 99" in res.stderr          # how to pick it up later
    assert len(res.curl_calls) == 4           # POST + three polls, then give up


def test_bk_status_live_path_prints_the_state(stub_bin, token_file):
    res = _bk_live(stub_bin, "status", "7", token_file=token_file,
                   build={"number": 7, "state": "running", "commit": "abc"})
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "running"


def test_bk_refuses_a_response_missing_the_field_with_exit_2(stub_bin,
                                                             token_file):
    """E5-R12: a short response is a refusal (2), not "the build did not
    pass" (1) — the one distinction the exit-code contract exists to make."""
    res = _bk_live(stub_bin, "status", "7", token_file=token_file,
                   build={"number": 7})
    assert res.returncode == 2
    assert "state" in res.stderr


def test_bk_collect_live_path_writes_under_the_commit_directory(stub_bin,
                                                                token_file,
                                                                tmp_path):
    dest = tmp_path / "inbox"
    res = _bk_live(
        stub_bin, "collect", "7", str(dest), token_file=token_file,
        build={"number": 7, "state": "passed", "commit": "deadbee"},
        artifacts=[{"path": ".assay/verdict.json",
                    "download_url": "https://example.invalid/dl/1"},
                   {"path": ".run-gate/history.json",
                    "download_url": "https://example.invalid/dl/2"}],
        body="ARTIFACT-BYTES")
    assert res.returncode == 0, res.stderr
    assert (dest / "deadbee" / ".assay" / "verdict.json").read_text() \
        == "ARTIFACT-BYTES"
    assert (dest / "deadbee" / ".run-gate" / "history.json").exists()
    assert "collected 2 artifact(s) of build 7" in res.stdout


def test_bk_collect_refuses_a_commit_that_would_escape_the_directory(
        stub_bin, token_file, tmp_path):
    """B1: `commit` is a free-form field of the build response ("Ref, SHA or
    tag"), used as a path component. `../../../escape` used to write outside
    <dir> — the one containment §4.2 advertises."""
    dest = tmp_path / "inbox" / "deep"
    dest.mkdir(parents=True)
    res = _bk_live(
        stub_bin, "collect", "7", str(dest), token_file=token_file,
        build={"number": 7, "state": "passed", "commit": "../../../escape"},
        artifacts=[{"path": "pwned.txt",
                    "download_url": "https://example.invalid/dl/1"}])
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "../../../escape" in res.stderr
    assert list(tmp_path.glob("**/pwned.txt")) == []
    assert not (tmp_path.parent / "escape").exists()


@pytest.mark.parametrize("bad_path", ["../../escape/x", "/etc/passwd",
                                      "a/../../b"])
def test_bk_collect_refuses_an_escaping_artifact_path_with_exit_2(
        stub_bin, token_file, tmp_path, bad_path):
    """Also E5-R12: this refusal used to exit 1, colliding with "the build did
    not pass"."""
    dest = tmp_path / "inbox"
    res = _bk_live(
        stub_bin, "collect", "7", str(dest), token_file=token_file,
        build={"number": 7, "state": "passed", "commit": "abc123"},
        artifacts=[{"path": bad_path,
                    "download_url": "https://example.invalid/dl/1"}])
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "refusing artifact path" in res.stderr
    assert list(tmp_path.glob("**/x")) == []


def test_bk_collect_exits_2_when_a_download_fails(stub_bin, token_file,
                                                  tmp_path):
    """SF1 / mutation M13: a failed download used to propagate curl's own exit
    22 — outside the documented set — and `curl … || true` would have made
    `collect` report success. It is a refusal, and nothing is claimed."""
    dest = tmp_path / "inbox"
    res = _bk_live(
        stub_bin, "collect", "7", str(dest), token_file=token_file,
        build={"number": 7, "state": "passed", "commit": "abc123"},
        artifacts=[{"path": ".assay/verdict.json",
                    "download_url": "https://example.invalid/dl/1"}],
        FAKE_DL_FAIL="yes")
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "downloading artifact '.assay/verdict.json' failed" in res.stderr
    assert "collected" not in res.stdout
    assert not (dest / "abc123" / ".assay" / "verdict.json").exists()


def test_bk_collect_exits_2_when_the_destination_cannot_be_created(
        stub_bin, token_file, tmp_path):
    """SF1's other half: mkdir's exit 1 is the code reserved for "the build did
    not pass", which `collect` never reports at all."""
    blocked = tmp_path / "inbox"
    blocked.write_text("a file sits where the directory must go\n")
    res = _bk_live(stub_bin, "collect", "7", str(blocked), token_file=token_file,
                   build={"number": 7, "state": "passed", "commit": "abc123"},
                   artifacts=[])
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "cannot create" in res.stderr


@pytest.mark.parametrize("number", ["abc", "1.2", "../../x"])
def test_bk_run_refuses_a_non_numeric_build_number_from_the_response(
        stub_bin, token_file, git_repo, number):
    """N1 / mutation M14: E5-R7's letter is `[0-9]+` on the build number the
    create response hands back. The looser path-component charset kept
    `../../x` out but let `abc` and `1.2` straight into the poll URL."""
    root, _ = git_repo
    res = _bk_live(stub_bin, "run", "lint", token_file=token_file, cwd=root,
                   build={"number": number, "state": "passed",
                          "commit": "abc123"})
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "build number '%s'" % number in res.stderr
    # the POST happened; no poll was ever attempted with that number
    assert len(res.curl_calls) == 1


def test_bk_collect_refuses_a_malformed_artifact_listing_with_exit_2(
        stub_bin, token_file, tmp_path):
    res = _bk_live(stub_bin, "collect", "7", str(tmp_path / "inbox"),
                   token_file=token_file,
                   build={"number": 7, "state": "passed", "commit": "abc123"},
                   artifacts=[{"path": ".assay/x"}])   # no download_url
    assert res.returncode == 2
    assert "unusable" in res.stderr


def test_bk_collect_refuses_extra_arguments(token_file, tmp_path):
    res = _bk("--dry-run", "collect", "7", str(tmp_path), "extra",
              token_file=token_file)
    assert res.returncode == 2
    assert "extra" in res.stderr


def test_bk_run_refuses_a_duplicate_lane(token_file, git_repo):
    root, _ = git_repo
    res = _bk("--dry-run", "run", "lint", "lint", token_file=token_file,
              cwd=root)
    assert res.returncode == 2
    assert "named twice" in res.stderr


@pytest.mark.parametrize("var,value", [
    ("BK_POLL_SECONDS", "030"),
    ("BK_MAX_WAIT_MINUTES", "0300"),
])
def test_bk_refuses_a_leading_zero_by_name(token_file, var, value):
    res = _bk("--dry-run", "status", "1", token_file=token_file,
              **{var: value})
    assert res.returncode == 2
    assert "%s='%s'" % (var, value) in res.stderr
    assert "leading zero" in res.stderr
