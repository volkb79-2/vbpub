"""Real CLI coverage for B114 cold, survivor, fallback, and baseline paths."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from assay import cli
from conftest import GitRepo


def _seed_campaign(repo: GitRepo, attempt_log: Path) -> Path:
    repo.write(
        ".gitignore",
        ".assay/\n.pytest_cache/\n__pycache__/\ncoverage.json\n.coverage\n",
    )
    repo.write(
        "src/pkg/mod.py",
        "def early(value):\n    return value > 0\n\n"
        "def survivor(value):\n    return value > 0\n\n"
        "def fallback(value):\n    return value > 0\n",
    )
    repo.write(
        "tests/test_campaign.py",
        "import inspect\n"
        "import os\n"
        "from pathlib import Path\n"
        "import pytest\n"
        "from pkg import mod\n\n"
        "def _flags():\n"
        "    return ''.join('1' if '>= 0' in inspect.getsource(fn) else '0'\n"
        "                   for fn in (mod.early, mod.survivor, mod.fallback))\n\n"
        "def _record(name):\n"
        "    mode = 'cold' if os.environ.get('ASSAY_MUTATION_WITNESS_COLD') == '1' else 'full'\n"
        "    with Path(os.environ['ASSAY_TEST_ATTEMPT_LOG']).open('a', encoding='utf-8') as stream:\n"
        "        stream.write(f'{mode}|{name}|{_flags()}\\n')\n\n"
        "def test_early_kill():\n"
        "    _record('early')\n"
        "    assert mod.early(0) is False\n\n"
        "def test_survivor():\n"
        "    _record('survivor')\n"
        "    assert mod.survivor(1) is True\n\n"
        "def test_declared_fallback(pytestconfig):\n"
        "    _record('fallback')\n"
        "    if _flags() == '001' and os.environ.get('ASSAY_MUTATION_WITNESS_COLD') == '1':\n"
        "        class LateHook:\n"
        "            @pytest.hookimpl\n"
        "            def pytest_runtest_logreport(self, report):\n"
        "                return None\n"
        "        pytestconfig.pluginmanager.register(LateHook(), 'cold-only-dynamic-hook')\n"
        "    assert mod.fallback(0) is False\n",
    )
    repo.write(
        "assay.toml",
        f'''\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1", "R2"]
enforcement = "gate"
argv = [
  {json.dumps(sys.executable)}, "-m", "pytest", "tests", "-q",
  "--cov=src/pkg", "--cov-branch", "--cov-report=json:coverage.json",
]
env = {{ PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }}
env_passthrough = ["PATH", "HOME", "TMPDIR", "ASSAY_TEST_ATTEMPT_LOG"]
budget = "10m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src/pkg"]
mode = "whole_target"
targets = ["src/pkg/mod.py"]
fail_under = 100.0
allow_excluded = true
require_branch = false

[lanes.package.judge.coverage]
format = "coverage-py-json"
artifact = "coverage.json"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 3
operators = ["python:compare-swap"]
budget_per_candidate = "120s"
liveness = false
''',
    )
    repo.commit_all("seed B114 cold witness integration lane")
    return repo.path / "assay.toml"


def _run_cold_campaign(repo: GitRepo, config: Path, *, tmp_path: Path, attempt_log: Path):
    (repo.path / ".assay").mkdir(exist_ok=True)
    progress = repo.path / ".assay/progress-package.jsonl"
    manifest = repo.path / ".assay/r2-manifest-package.txt"
    verdict = repo.path / ".assay/verdict-package.json"
    state_dir = repo.path / ".assay/mutation-state"
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(
        [
            "run",
            "package",
            "--file",
            str(config),
            "--cold-witness",
            "--r2-manifest",
            str(manifest),
            "--resume",
            "--progress",
            str(progress),
            "--state-dir",
            str(state_dir),
            "--verdict-json",
            str(verdict),
        ],
        stdout=stdout,
        stderr=stderr,
    )
    return (
        code,
        stdout.getvalue(),
        stderr.getvalue(),
        progress,
        manifest,
        verdict,
        state_dir,
    )


def test_assay_run_cold_witness_covers_early_kill_survivor_and_one_fallback(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    attempt_log = tmp_path / "attempts.log"
    config = _seed_campaign(git_repo, attempt_log)
    monkeypatch.setenv("ASSAY_TEST_ATTEMPT_LOG", str(attempt_log))

    plan_stdout, plan_stderr = io.StringIO(), io.StringIO()
    plan_code = cli.main(
        ["plan", "package", "--file", str(config)],
        stdout=plan_stdout,
        stderr=plan_stderr,
    )
    assert plan_code == 0, plan_stderr.getvalue()
    plan = json.loads(plan_stdout.getvalue())
    assert plan["status"] == "ok"
    assert plan["candidate_count"] == 3
    assert len(plan["candidates"]) == 3

    code, stdout, stderr, progress_path, manifest_path, verdict_path, state_dir = (
        _run_cold_campaign(
            git_repo, config, tmp_path=tmp_path, attempt_log=attempt_log
        )
    )
    assert code == 0, f"stdout:\n{stdout}\nstderr:\n{stderr}"
    document = json.loads(verdict_path.read_text(encoding="utf-8"))
    r2 = next(claim for claim in document["claims"] if claim["rigor"] == "R2")
    mutation = r2["mutation"]
    command = document["judgment"]["r2"]["r2_command"]
    assert (
        command["coverage_baseline"]["hook_fingerprint_sha256"]
        != command["r2_baseline"]["hook_fingerprint_sha256"]
    )
    assert len(mutation["killed"]) == 2
    assert len(mutation["survived"]) == 1

    cold_kills = [
        outcome
        for outcome in mutation["killed"]
        if outcome["execution"]["mode"] == "witness-cold"
    ]
    declared_kills = [
        outcome
        for outcome in mutation["killed"]
        if outcome["execution"]["mode"] == "full"
        and outcome["evidence"]["command"] == "declared"
    ]
    assert len(cold_kills) == 1
    assert cold_kills[0]["evidence"]["hook_fingerprint_sha256"] == command[
        "r2_baseline"
    ]["hook_fingerprint_sha256"]
    assert cold_kills[0]["evidence"]["collection_sha256"] == command[
        "r2_baseline"
    ]["collection_sha256"]
    assert cold_kills[0]["execution"]["witness"]["node_id"].endswith(
        "test_campaign.py::test_early_kill"
    )
    assert cold_kills[0]["evidence"]["started_count"] == 1
    assert cold_kills[0]["evidence"]["failed_call_index"] == 0
    assert len(declared_kills) == 1
    assert declared_kills[0]["evidence"]["hook_fingerprint_sha256"] == command[
        "coverage_baseline"
    ]["hook_fingerprint_sha256"]
    assert declared_kills[0]["evidence"]["collection_sha256"] == command[
        "coverage_baseline"
    ]["collection_sha256"]
    assert mutation["survived"][0]["evidence"]["command"] == "r2"
    assert mutation["survived"][0]["evidence"]["started_count"] is None

    rows = [line.split("|") for line in attempt_log.read_text(encoding="utf-8").splitlines()]
    rows_by_attempt = {
        (mode, flags): [name for row_mode, name, row_flags in rows if (row_mode, row_flags) == (mode, flags)]
        for mode, flags in {(row[0], row[2]) for row in rows}
    }
    assert rows_by_attempt[("cold", "100")] == ["early"]
    assert rows_by_attempt[("cold", "010")] == ["early", "survivor", "fallback"]
    assert rows_by_attempt[("cold", "001")] == ["early", "survivor", "fallback"]
    assert rows_by_attempt[("full", "001")] == ["early", "survivor", "fallback"]
    assert sum(1 for mode, flags in rows_by_attempt if mode == "full" and flags != "000") == 1

    manifest_nodes = manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(manifest_nodes) == 3
    assert all("test_campaign.py::" in node for node in manifest_nodes)
    events = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert sum(event.get("event") == "candidate" for event in events) == 3
    assert len(list(state_dir.glob("*.json"))) == 3
    assert cli.main(["verify", str(verdict_path)], stdout=io.StringIO(), stderr=io.StringIO()) == 0


@pytest.mark.parametrize("failed_baseline", ["coverage", "r2"])
def test_cold_witness_rejects_a_masked_failing_baseline_before_candidates(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_baseline: str,
):
    attempt_log = tmp_path / "attempts.log"
    repo = git_repo
    repo.write(
        ".gitignore",
        ".assay/\n.pytest_cache/\n__pycache__/\ncoverage.json\n.coverage\n",
    )
    repo.write("src/pkg/mod.py", "def flag(value):\n    return value > 0\n")
    fail_with_cov = failed_baseline == "coverage"
    repo.write(
        "tests/conftest.py",
        "def pytest_sessionfinish(session, exitstatus):\n"
        f"    if session.config.pluginmanager.hasplugin('_cov') is {fail_with_cov!r} and exitstatus != 0:\n"
        "        session.exitstatus = 0\n",
    )
    repo.write(
        "tests/test_masked_baseline.py",
        "import os\n"
            "from pathlib import Path\n"
            "from pkg.mod import flag\n\n"
        "def test_baseline(pytestconfig):\n"
        "    if os.environ.get('ASSAY_MUTATION_WITNESS_COLD') == '1':\n"
        "        Path(os.environ['ASSAY_TEST_ATTEMPT_LOG']).write_text('candidate-started')\n"
        "    has_coverage = pytestconfig.pluginmanager.hasplugin('_cov')\n"
        f"    assert flag(0) is (has_coverage is {fail_with_cov!r})\n",
    )
    config = repo.write(
        "assay.toml",
        f'''\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1", "R2"]
enforcement = "gate"
argv = [
  {json.dumps(sys.executable)}, "-m", "pytest", "tests", "-q",
  "--cov=src/pkg", "--cov-branch", "--cov-report=json:coverage.json",
]
env = {{ PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }}
env_passthrough = ["PATH", "HOME", "TMPDIR", "ASSAY_TEST_ATTEMPT_LOG"]
budget = "10m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src/pkg"]
mode = "whole_target"
targets = ["src/pkg/mod.py"]
fail_under = 100.0
allow_excluded = true
require_branch = false

[lanes.package.judge.coverage]
format = "coverage-py-json"
artifact = "coverage.json"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 1
operators = ["python:compare-swap"]
budget_per_candidate = "120s"
liveness = false
''',
    )
    repo.commit_all(f"seed masked {failed_baseline} baseline")
    monkeypatch.setenv("ASSAY_TEST_ATTEMPT_LOG", str(attempt_log))

    code, stdout, stderr, _progress_path, _manifest_path, verdict_path, _state_dir = (
        _run_cold_campaign(
            repo, config, tmp_path=tmp_path, attempt_log=attempt_log
        )
    )
    assert code != 0, f"stdout:\n{stdout}\nstderr:\n{stderr}"
    document = json.loads(verdict_path.read_text(encoding="utf-8"))
    r2 = next(claim for claim in document["claims"] if claim["rigor"] == "R2")
    assert r2["status"] == "ERROR"
    assert r2["reason_code"] == "BAD_LANE_CONFIG"
    assert "cold witness:" in r2["detail"]
    assert not attempt_log.exists()
    assert cli.main(["verify", str(verdict_path)], stdout=io.StringIO(), stderr=io.StringIO()) == 0
