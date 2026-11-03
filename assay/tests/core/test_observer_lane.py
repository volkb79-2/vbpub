"""B206 end to end: a real native R2 lane with a fake cgprofile observer.

The same two-candidate liveness lane runs with the observer present, absent,
refusing and failing. Every profile carries the golden numbers or one explicit
`NO_MEASUREMENT` reason, and the verdict's claims (buckets) never change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import GitRepo, make_lane, make_r2_judge

from assay import mutation, runner
from assay import observer_windows as ow
from assay.adapters.python import PythonAdapter
from assay.config import MutationConfig
from assay.errors import Outcome
from observer_support import FakeObserver, error_reply, golden, golden_handler


def _seed(repo: GitRepo) -> tuple[str, str]:
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    repo.write("src/mod.py", "def flag(value):\n    return value >= 0\n")
    repo.write(
        "tests/test_behavior.py",
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        "from mod import flag\n\n"
        "def test_behavior():\n"
        "    assert flag(1)\n"
        "    assert not flag(0)\n"
        "    assert flag(9)\n"
        "    assert not flag(10)\n",
    )
    base = repo.commit_all("seed test project")
    repo.write("src/mod.py", "def flag(value):\n    return value > 0 and value < 10\n")
    return base, repo.commit_all("two compare-swap mutation sites")


@pytest.fixture
def lane_run(git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run the lane once; returns ``(verdict, progress events, state records)``."""
    base, head = _seed(git_repo)
    zero = mutation.ResourceLimitCounters(
        pids_max=0, memory_oom=0, memory_max=0, memory_oom_kill=0, memory_oom_group_kill=0
    )
    monkeypatch.setattr(mutation, "_read_candidate_resource_counters", lambda: zero)
    counter = {"n": 0}

    def run(socket_path: Path | None, *, jobs: int = 1):
        counter["n"] += 1
        if socket_path is None:
            monkeypatch.delenv(ow.OBSERVER_SOCKET_ENV, raising=False)
        else:
            monkeypatch.setenv(ow.OBSERVER_SOCKET_ENV, str(socket_path))
        lane = make_lane(
            rigor=("R0", "R2"),
            argv=(sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"),
            judge=make_r2_judge(
                source_root_paths=(git_repo.path / "src",),
                base=base,
                mutation=MutationConfig(
                    jobs=jobs, max_mutants=10, operators=("python:compare-swap",), liveness="true",
                ),
            ),
            budget="2m",
            budget_seconds=120,
        )
        state_dir = tmp_path / f"state-{counter['n']}"
        progress = tmp_path / f"progress-{counter['n']}.jsonl"
        verdict = runner.run_lane(
            lane, commit=head, repo=git_repo.path, project_root=git_repo.path,
            adapter=PythonAdapter(), assay_version="0.1.0", resume=True,
            state_dir=state_dir, progress_artifact=progress,
        )
        events = [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines()]
        records = [
            json.loads(path.read_text(encoding="utf-8")) for path in sorted(state_dir.glob("*.json"))
        ]
        return verdict, [e for e in events if e["event"] == "candidate"], records

    return run


def _claims(verdict) -> Any:
    return verdict.to_dict()["claims"]


def test_the_golden_window_becomes_each_candidates_profile(lane_run, tmp_path: Path):
    closed = golden("close-cgroup")["response"]
    with FakeObserver(tmp_path / "obs.sock", golden_handler("close-cgroup")) as observer:
        verdict, events, records = lane_run(observer.path)
    assert verdict.outcome is Outcome.PASS
    assert len(events) == 2
    opens = [r for r in observer.requests if r["verb"] == "window-open"]
    closes = [r for r in observer.requests if r["verb"] == "window-close"]
    assert len(opens) == len(closes) == 2
    assert {r["args"]["mode"] for r in opens} == {"cgroup"}  # jobs=1
    assert all(isinstance(r["args"]["root_pid"], int) and r["args"]["root_pid"] > 1 for r in opens)
    for event in events:
        profile = event["profile"]
        ow.validate_profile(profile)
        assert (profile["status"], profile["reason"]) == ("MEASURED", None)
        # Byte-equal JSON for the metrics (the golden numbers).
        assert json.dumps(profile["metrics"], sort_keys=True) == json.dumps(closed["metrics"], sort_keys=True)
        assert profile["observer"]["mode"] == "cgroup"
        assert profile["attribution"]["baseline_bytes"] == 700
    assert [r["profile"] for r in sorted(records, key=lambda r: r["candidate_id"])] == [
        e["profile"] for e in sorted(events, key=lambda e: e["candidate_id"])
    ]
    r2 = verdict.to_dict()["judgment"]["r2"]
    assert r2["profile"]["status_counts"] == {"MEASURED": 2, "PARTIAL": 0, "NO_MEASUREMENT": 0}
    assert r2["profile"]["observer"]["modes"] == ["cgroup"]


def test_the_verdict_buckets_are_identical_with_the_observer_present_absent_and_failing(
    lane_run, tmp_path: Path
):
    absent, _, _ = lane_run(None)
    with FakeObserver(tmp_path / "ok.sock", golden_handler()) as ok:
        present, _, _ = lane_run(ok.path)
    with FakeObserver(tmp_path / "bad.sock", lambda request: error_reply("daemon-error")) as bad:
        failing, _, _ = lane_run(bad.path)
    assert absent.outcome is present.outcome is failing.outcome is Outcome.PASS
    assert _claims(absent) == _claims(present) == _claims(failing)


@pytest.mark.parametrize(
    ("handler", "reason", "asked"),
    [
        (lambda request: error_reply("peer-refused"), "OBSERVER_REFUSED", 2),
        (lambda request: error_reply("daemon-error"), "OBSERVER_ERROR", 2),
        # Contract 3 is asked exactly once per run; the second candidate is not asked.
        (lambda request: error_reply("bad-argument"), "OBSERVER_CONTRACT_UNSUPPORTED", 1),
        (
            lambda request: (
                golden("open")["response"]
                if request["verb"] == "window-open"
                else error_reply("window-lost")
            ),
            "WINDOW_LOST",
            2,
        ),
    ],
)
def test_each_failure_reason_is_recorded_per_candidate_and_never_changes_the_verdict(
    lane_run, tmp_path: Path, handler, reason: str, asked: int
):
    with FakeObserver(tmp_path / "obs.sock", handler) as observer:
        verdict, events, records = lane_run(observer.path)
    assert verdict.outcome is Outcome.PASS
    assert [e["outcome_bucket"] for e in events] == ["killed", "killed"]
    opens = [r for r in observer.requests if r["verb"] == "window-open"]
    assert len(opens) == asked
    for event in events:
        profile = event["profile"]
        assert (profile["status"], profile["reason"]) == ("NO_MEASUREMENT", reason)
        assert all(value is None for value in profile["metrics"].values())
        assert profile["wall_seconds"] == event["elapsed_seconds"]
    r2 = verdict.to_dict()["judgment"]["r2"]
    assert r2["profile"]["reason_counts"] == {reason: 2}


def test_an_absent_socket_is_observer_unavailable_for_every_candidate(lane_run, tmp_path: Path):
    verdict, events, _ = lane_run(tmp_path / "nothing-listens-here.sock")
    assert verdict.outcome is Outcome.PASS
    assert {e["profile"]["reason"] for e in events} == {"OBSERVER_UNAVAILABLE"}
    r2 = verdict.to_dict()["judgment"]["r2"]
    assert r2["profile"]["reason_counts"] == {"OBSERVER_UNAVAILABLE": 2}
