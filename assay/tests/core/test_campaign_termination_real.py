"""Real process-tree proof for B117's termination contract.

These tests require the registered tester-unified environment: native R2
intentionally refuses to classify candidates when the full cgroup ancestry is
not observable. Both subprocesses are launched in owned sessions and their
candidate marker exits when its pytest parent is reparented, so a failed test
cannot leave a spinning process on the host.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import GitRepo

from assay.errors import Outcome, ReasonCode


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _proc_identity(pid: int) -> tuple[str, str] | None:
    try:
        record = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError):
        return None
    tail = record.rsplit(")", 1)[1].split()
    return tail[0], tail[19]


def _read_candidate(pid_file: Path, assay: subprocess.Popen[str]) -> tuple[int, str]:
    end = time.monotonic() + 180
    while time.monotonic() < end:
        if pid_file.is_file():
            fields = pid_file.read_text(encoding="ascii").split()
            assert len(fields) == 2
            return int(fields[0]), fields[1]
        if assay.poll() is not None:
            stdout, stderr = assay.communicate()
            raise AssertionError(
                f"assay exited before the mutant started ({assay.returncode})\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        time.sleep(0.2)
    raise AssertionError("candidate process did not publish its pid before the failsafe")


def _seed_r2_repo(repo: GitRepo, pid_file: Path) -> Path:
    repo.write(".gitignore", ".assay/\n__pycache__/\n.pytest_cache/\n")
    repo.write("pkg/mod.py", "def guard(x):\n    return True\n")
    base = repo.commit_all("seed Python package")
    repo.write("pkg/mod.py", "def guard(x):\n    return x <= 0\n")
    repo.commit_all("add one compare-swap site")
    marker = repr(str(pid_file))
    repo.write(
        "tests/test_guard.py",
        "from pathlib import Path\n"
        "import os\n"
        "from pkg.mod import guard\n\n"
        "def test_guard():\n"
        "    if guard(0):\n"
        "        return\n"
        "    stat = Path('/proc/self/stat').read_text(encoding='ascii')\n"
        "    start_time = stat.rsplit(')', 1)[1].split()[19]\n"
        f"    Path({marker}).write_text(f'{{os.getpid()}} {{start_time}}', encoding='ascii')\n"
        "    parent = os.getppid()\n"
        "    while os.getppid() == parent:\n"
        "        pass\n",
    )
    repo.commit_all("add signal-safe mutant test")
    config = repo.write(
        "assay.toml",
        f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["{sys.executable}", "-m", "pytest", "tests/test_guard.py", "-q", "-p", "no:cacheprovider"]
env = {{ PYTHONDONTWRITEBYTECODE = "1" }}
env_passthrough = ["PATH", "HOME", "TMPDIR", "PYTHONPATH"]
budget = "15m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["pkg"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 1
operators = ["python:compare-swap"]
budget_per_candidate = "600s"
liveness = true
""",
    )
    repo.commit_all("declare the R2 lane")
    return config


def _start_assay(repo: GitRepo, config: Path, verdict: Path, state_dir: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    assay = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from assay.cli import main; raise SystemExit(main(sys.argv[1:]))",
            "run",
            "package",
            "--file",
            str(config),
            "--verdict-json",
            str(verdict),
            "--resume",
            "--state-dir",
            str(state_dir),
        ],
        cwd=repo.path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    group = os.getpgid(assay.pid)
    assert group == assay.pid
    assert group != os.getpgid(0)
    return assay, group


@pytest.mark.parametrize("signal_group", [False, True], ids=["sigterm-pid", "timeout-group"])
def test_sigterm_leaves_r2_candidate_unclassified_and_dead(
    git_repo: GitRepo,
    tmp_path: Path,
    signal_group: bool,
):
    pid_file = tmp_path / "candidate.pid"
    config = _seed_r2_repo(git_repo, pid_file)
    verdict_path = tmp_path / "verdict.json"
    state_dir = tmp_path / "mutation-state"
    assay, assay_group = _start_assay(git_repo, config, verdict_path, state_dir)
    candidate_pid: int | None = None
    candidate_start: str | None = None
    try:
        candidate_pid, candidate_start = _read_candidate(pid_file, assay)
        identity = _proc_identity(candidate_pid)
        assert identity is not None
        assert identity[1] == candidate_start

        if signal_group:
            os.killpg(assay_group, signal.SIGTERM)
        else:
            os.kill(assay.pid, signal.SIGTERM)
        stdout, stderr = assay.communicate(timeout=120)
        assert assay.returncode == Outcome.BUDGET_EXCEEDED.exit_code, (
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )

        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        assert verdict["outcome"] == Outcome.BUDGET_EXCEEDED.value
        assert verdict["reason_code"] == ReasonCode.LANE_TIMEOUT.value
        r2 = next(claim for claim in verdict["claims"] if claim["rigor"] == "R2")
        assert r2["status"] == Outcome.BUDGET_EXCEEDED.value
        mutation = r2["mutation"]
        assert len(mutation["budget_exceeded"]) == 1
        assert not mutation["killed"]
        assert not mutation["crashed"]
        assert not mutation["hung"]
        assert list(state_dir.glob("*.json")) == []

        after = _proc_identity(candidate_pid)
        assert after is None or after[0] == "Z" or after[1] != candidate_start
    finally:
        if assay.poll() is None:
            os.killpg(assay_group, signal.SIGKILL)
            assay.wait(timeout=10)
        if candidate_pid is not None and candidate_start is not None:
            current = _proc_identity(candidate_pid)
            if current is not None and current[1] == candidate_start:
                try:
                    os.killpg(candidate_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
