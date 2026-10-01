"""The ``candidates`` progress event names the mutation judge when a store is in play (C25, W9 O20)."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

from conftest import GitRepo

from assay.cli import main

_JUDGE = re.compile(r"[0-9a-f]{64}")

_LANE = """\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "grep -q 'x > 0' src/mod.py && grep -q 'y < 1' src/mod.py"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 50
operators = ["python:compare-swap"]
"""


def _seed(repo: GitRepo) -> Path:
    repo.write("src/mod.py", "def f(x, y):\n    return 0\n")
    base = repo.commit_all("add mod.py")
    repo.write("src/mod.py", "def f(x, y):\n    return x > 0 and y < 1\n")
    repo.commit_all("introduce two compare-swap sites")
    toml = repo.write("assay.toml", _LANE.format(base=base))
    repo.commit_all("add assay.toml")
    return toml


def _events(progress: Path) -> list[dict]:
    return [json.loads(line) for line in progress.read_text("utf-8").splitlines() if line]


def _candidates_events(progress: Path) -> list[dict]:
    return [event for event in _events(progress) if event["event"] == "candidates"]


def test_o20_the_candidates_event_carries_the_judge_every_record_carries(
    git_repo: GitRepo, tmp_path: Path
):
    toml = _seed(git_repo)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    state = evidence / "state"
    progress = evidence / "progress.jsonl"
    code = main(
        [
            "run",
            "package",
            "--file",
            str(toml),
            "--resume",
            "--state-dir",
            str(state),
            "--progress",
            str(progress),
            "--verdict-json",
            str(evidence / "verdict.json"),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code == 0
    (event,) = _candidates_events(progress)
    records = sorted(state.glob("*.json"))
    assert len(records) == 2
    judges = {json.loads(path.read_text("utf-8"))["judge_sha256"] for path in records}
    assert judges == {event["judge_sha256"]}
    assert _JUDGE.fullmatch(event["judge_sha256"])
    assert {"candidate_total", "selected_total", "pending_total", "commit"} <= set(event)
    assert event["candidate_total"] == 2 and event["commit"] == git_repo.head()


def test_o20_without_a_state_root_the_key_is_absent_not_null(git_repo: GitRepo, tmp_path: Path):
    toml = _seed(git_repo)
    evidence = tmp_path / "ev"
    evidence.mkdir()
    progress = evidence / "progress.jsonl"
    code = main(
        [
            "run",
            "package",
            "--file",
            str(toml),
            "--progress",
            str(progress),
            "--verdict-json",
            str(evidence / "verdict.json"),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code == 0
    (event,) = _candidates_events(progress)
    assert "judge_sha256" not in event
    assert event["candidate_total"] == 2
