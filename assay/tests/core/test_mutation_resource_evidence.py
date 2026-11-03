"""B111 measurement evidence: baseline setup/teardown forwarding (O9), the
resource sidecar and event-time readers, and the end-to-end candidate
evidence (O8).

B206 (assay 12): resource NUMBERS no longer come from Assay. The sidecar
keeps only the spawn time, the candidate's evidence is Assay's own clock
(phase and startup seconds) plus the cgprofile `profile`, and the first
failing test (B205). Nothing here asserts a duration or a sample count.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import GitRepo, make_lane, make_r2_judge

from assay import liveness, mutation, runner
from assay import observer_windows as ow
from assay.adapters.python import PythonAdapter
from assay.config import MutationConfig
from assay.errors import Outcome


def _write(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _phase(nodeid: Any, when: str, duration: Any, pid: int = 100) -> dict[str, Any]:
    return {"event": "phase", "when": when, "nodeid": nodeid, "duration_s": duration, "t": 1.0, "pid": pid}


def _call(nodeid: Any, pid: int = 100) -> dict[str, Any]:
    return {
        "event": "test",
        "when": "call",
        "nodeid": nodeid,
        "outcome": "passed",
        "duration_s": 1.0,
        "t": 1.0,
        "pid": pid,
    }


# --------------------------------------------------------------------------
# O9: baseline phase durations
# --------------------------------------------------------------------------


def test_o9_setup_and_teardown_pair_positionally_in_the_owner_stream(tmp_path: Path) -> None:
    events = _write(
        tmp_path / "baseline.ndjson",
        [
            {"event": "session_start", "t": 0.0, "pid": 100},
            _phase("a", "setup", 0.1),  # a setup error: no call follows it
            _phase("a", "setup", 0.2),
            _call("a"),
            _phase("a", "teardown", 0.3),
            _phase("a", "teardown", 9.9),  # a second teardown is never paired
            _phase("a", "setup", 0.4),  # the duplicate run of the same nodeid
            _call("a"),
            _phase("a", "teardown", 0.5),
            _phase("b", "setup", True),
            _call("b"),
            _phase("b", "teardown", -1.0),
            _phase("c", "setup", 0.6),
            _call("c"),  # the final call has no teardown
            _phase("d", "setup", 10**400),
            _call("d"),
            _phase("zz", "teardown", 0.7),  # no call is waiting for it
            _phase("a", "other", 0.8),
            _phase(["unhashable"], "setup", 0.9),
            _call(["unhashable"]),
            {"event": "phase", "when": "setup", "duration_s": 5.0, "pid": 100},
            _phase("a", "setup", 8.0, pid=200),  # a foreign process
            _call("a", pid=200),
            _phase("a", "teardown", 8.5, pid=200),
        ],
    )
    assert liveness.baseline_phase_durations(events) == [
        {"setup_s": 0.2, "teardown_s": 0.3},
        {"setup_s": 0.4, "teardown_s": 0.5},
        {"setup_s": None, "teardown_s": None},
        {"setup_s": 0.6, "teardown_s": None},
        {"setup_s": None, "teardown_s": None},
        {"setup_s": None, "teardown_s": None},
    ]
    assert len(liveness.baseline_phase_durations(events)) == len(
        liveness.baseline_test_events(events)
    )


def test_o9_a_legacy_file_without_pids_keeps_the_merged_view(tmp_path: Path) -> None:
    events = _write(
        tmp_path / "legacy.ndjson",
        [
            {"event": "phase", "when": "setup", "nodeid": "a", "duration_s": 0.1},
            {"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 1.0},
            {"event": "phase", "when": "teardown", "nodeid": "a", "duration_s": 0.2},
        ],
    )
    assert liveness.baseline_phase_durations(events) == [{"setup_s": 0.1, "teardown_s": 0.2}]


def test_o9_a_missing_or_absent_events_file_has_no_rows(tmp_path: Path) -> None:
    assert liveness.baseline_phase_durations(None) == []
    assert liveness.baseline_phase_durations(tmp_path / "absent.ndjson") == []


# --------------------------------------------------------------------------
# read_resource_sidecar / first_event_times
# --------------------------------------------------------------------------

_SIDECAR = {"format": 1, "spawned_at": 1790000000.5}


def test_read_resource_sidecar_is_none_unless_the_file_is_a_format_1_object(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.ndjson"
    sidecar = events.with_suffix(liveness.RESOURCE_SIDECAR_SUFFIX)
    assert liveness.read_resource_sidecar(events) is None  # absent
    for content in (b"\xff", b"[]", b'{"format": 2}', b"{not json"):
        sidecar.write_bytes(content)
        assert liveness.read_resource_sidecar(events) is None
    sidecar.write_text(json.dumps(_SIDECAR), encoding="utf-8")
    assert liveness.read_resource_sidecar(events) == _SIDECAR


def test_first_event_times_of_a_missing_file_is_none_none(tmp_path: Path) -> None:
    assert liveness.first_event_times(None) == (None, None)
    assert liveness.first_event_times(tmp_path / "absent.ndjson") == (None, None)


def test_first_event_times_uses_the_owners_first_test_record(tmp_path: Path) -> None:
    events = tmp_path / "events.ndjson"
    _write(
        events,
        [
            {"event": "session_start", "t": 5.0, "pid": 9},
            {"event": "test", "t": 6.0, "pid": 8, "nodeid": "foreign"},
            {"event": "test", "t": 7, "pid": 9, "nodeid": "owned"},
        ],
    )
    assert liveness.first_event_times(events) == (5.0, 7)


def test_first_event_times_without_a_test_record_is_start_only(tmp_path: Path) -> None:
    events = _write(
        tmp_path / "events.ndjson",
        [
            {"event": "session_start", "t": 5.0, "pid": 9},
            {"event": "phase", "when": "setup", "t": 6.0, "pid": 9},
        ],
    )
    assert liveness.first_event_times(events) == (5.0, None)


@pytest.mark.parametrize("raw", ["true", '"1"', "1e999", "null"])
def test_first_event_times_rejects_a_t_that_is_not_a_finite_number(
    tmp_path: Path, raw: str
) -> None:
    events = tmp_path / "events.ndjson"
    events.write_text(
        '{"event": "session_start", "t": %s, "pid": 9}\n'
        '{"event": "test", "t": %s, "pid": 9, "nodeid": "a"}\n' % (raw, raw),
        encoding="utf-8",
    )
    assert liveness.first_event_times(events) == (None, None)


# --------------------------------------------------------------------------
# _startup_seconds: the startup gaps are exact differences of recorded
# stamps (hand-written sidecar and events, no clock)
# --------------------------------------------------------------------------


def _startup_fixture(
    tmp_path: Path, *, sidecar: dict[str, Any] | None, events: list[dict[str, Any]] | None
) -> Path:
    events_path = tmp_path / "candidate.ndjson"
    if sidecar is not None:
        events_path.with_suffix(liveness.RESOURCE_SIDECAR_SUFFIX).write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
    if events is not None:
        _write(events_path, events)
    return events_path


_STAMPED_EVENTS = [
    {"event": "session_start", "t": 100.25, "pid": 9},
    {"event": "test", "t": 101.5, "pid": 9, "nodeid": "a"},
]


def test_startup_seconds_are_the_stamp_differences(tmp_path: Path) -> None:
    path = _startup_fixture(tmp_path, sidecar={**_SIDECAR, "spawned_at": 100.0}, events=_STAMPED_EVENTS)
    assert mutation._startup_seconds(path) == {"to_session_start": 0.25, "to_first_test": 1.5}


@pytest.mark.parametrize(
    ("sidecar", "events"),
    [
        (None, _STAMPED_EVENTS),  # no sidecar: no spawn stamp
        ({**_SIDECAR, "spawned_at": 100.0}, None),  # no events file: no session or test stamp
        ({**_SIDECAR, "spawned_at": True}, _STAMPED_EVENTS),  # an ill-typed spawn stamp
    ],
)
def test_startup_seconds_are_none_when_an_operand_is_missing(
    tmp_path: Path, sidecar: dict[str, Any] | None, events: list[dict[str, Any]] | None
) -> None:
    startup = mutation._startup_seconds(
        _startup_fixture(tmp_path, sidecar=sidecar, events=events)
    )
    assert startup == {"to_session_start": None, "to_first_test": None}


def test_startup_seconds_refuse_a_negative_gap(tmp_path: Path) -> None:
    path = _startup_fixture(
        tmp_path, sidecar={**_SIDECAR, "spawned_at": 102.0}, events=_STAMPED_EVENTS
    )
    assert mutation._startup_seconds(path) == {"to_session_start": None, "to_first_test": None}


def test_the_sidecar_is_written_with_the_spawn_time_only(tmp_path: Path) -> None:
    """B206: the liveness monitor records no CPU, RSS or IO number."""
    runner_ = liveness.LivenessRunner(events_dir=tmp_path / "events", expect_next_event_within_s=30)
    completed = runner_(
        [sys.executable, "-c", "pass"], env=dict(os.environ), cwd=tmp_path, timeout=60
    )
    assert completed.returncode == 0
    sidecar = liveness.read_resource_sidecar(runner_._events_path_for_cwd(tmp_path))
    assert sidecar is not None
    assert set(sidecar) == {"format", "spawned_at"}


# --------------------------------------------------------------------------
# first_failing_test (B205)
# --------------------------------------------------------------------------


def _failed(nodeid: Any, pid: int = 9, outcome: str = "failed") -> dict[str, Any]:
    return {"event": "test", "when": "call", "nodeid": nodeid, "outcome": outcome,
            "duration_s": 1.0, "t": 1.0, "pid": pid}


def test_first_failing_test_is_the_first_failed_test_event_of_the_owner(tmp_path: Path) -> None:
    events = _write(
        tmp_path / "e.ndjson",
        [
            {"event": "session_start", "t": 0.0, "pid": 9},
            _failed("foreign::t", pid=8),  # not the owner's stream
            _failed("ok::t", outcome="passed"),
            _failed("tests/a.py::test_first"),
            _failed("tests/a.py::test_second"),
        ],
    )
    assert liveness.first_failing_test(events) == "tests/a.py::test_first"


def test_first_failing_test_is_none_when_unknown(tmp_path: Path) -> None:
    assert liveness.first_failing_test(None) is None
    assert liveness.first_failing_test(tmp_path / "absent.ndjson") is None
    passed = _write(
        tmp_path / "p.ndjson",
        [{"event": "session_start", "t": 0.0, "pid": 9}, _failed("a::t", outcome="passed")],
    )
    assert liveness.first_failing_test(passed) is None
    nameless = _write(
        tmp_path / "n.ndjson",
        [{"event": "session_start", "t": 0.0, "pid": 9}, _failed(["a"]), _failed("")],
    )
    assert liveness.first_failing_test(nameless) is None


# --------------------------------------------------------------------------
# O8: end-to-end candidate resource evidence on a real liveness R2 lane
# --------------------------------------------------------------------------


def _seed_two_candidate_lane(repo: GitRepo) -> tuple[str, str]:
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


def test_candidate_event_carries_a_profile_and_no_assay_measured_numbers(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ASSAY_RESOURCE_OBSERVER_SOCKET", raising=False)
    base, head = _seed_two_candidate_lane(git_repo)
    lane = make_lane(
        rigor=("R0", "R2"),
        argv=(sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"),
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "src",),
            base=base,
            mutation=MutationConfig(
                jobs=1,
                max_mutants=10,
                operators=("python:compare-swap",),
                liveness="true",
            ),
        ),
        budget="2m",
        budget_seconds=120,
    )
    state_dir = tmp_path / "state"
    progress = tmp_path / "progress.jsonl"
    constructed: list[dict[str, Any]] = []

    class SpyRunner(liveness.LivenessRunner):
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(liveness, "LivenessRunner", SpyRunner)

    def judge_lane(**extra: Any):
        return runner.run_lane(
            lane,
            commit=head,
            repo=git_repo.path,
            project_root=git_repo.path,
            adapter=PythonAdapter(),
            assay_version="0.1.0",
            resume=True,
            state_dir=state_dir,
            **extra,
        )

    verdict = judge_lane(progress_artifact=progress)
    assert verdict.outcome is Outcome.PASS
    assert constructed and all("sampler" not in kwargs for kwargs in constructed)

    events = [
        json.loads(line)
        for line in progress.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event"] == "candidate"
    ]
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(state_dir.glob("*.json"))
    ]
    assert len(events) == 2
    assert len(records) == len(events)
    keys = ("phase_seconds", "startup_seconds")
    removed = {"cpu_seconds", "peak_rss_bytes", "write_bytes", "judge_rss_bytes", "resources"}
    for event in events:
        assert set(keys) <= set(event)
        assert not removed & set(event)  # hard cut: no old key, no alias
        profile = event["profile"]
        ow.validate_profile(profile)
        # No observer: an explicit NO_MEASUREMENT, every metric null.
        assert (profile["status"], profile["reason"]) == ("NO_MEASUREMENT", "OBSERVER_UNAVAILABLE")
        assert all(value is None for value in profile["metrics"].values())
        assert profile["wall_seconds"] == event["elapsed_seconds"]  # Assay's own clock
        assert event["worker_count"] == 1
        assert event["first_failing_test"] == "tests/test_behavior.py::test_behavior"
        phases = event["phase_seconds"]
        assert set(phases) == {"materialize", "command", "integrity", "teardown"}
        assert all(isinstance(value, float) and value >= 0 for value in phases.values())
        startup = event["startup_seconds"]
        assert set(startup) == {"to_session_start", "to_first_test"}
        assert all(
            value is None or isinstance(value, float) for value in startup.values()
        )
    records_by_id = {record["candidate_id"]: record for record in records}
    for event in events:
        record = records_by_id[event["candidate_id"]]
        assert record["resources"] == {key: event[key] for key in keys}
        assert record["profile"] == event["profile"]
        assert record["worker_count"] == 1
        assert record["first_failing_test"] == event["first_failing_test"]
    # One resource-profile/1 line per candidate, next to the progress stream.
    profile_lines = [
        json.loads(line)
        for line in (tmp_path / "progress.profile.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert sorted(line["subject"]["id"] for line in profile_lines) == sorted(
        event["candidate_id"] for event in events
    )
    assert [line for line in profile_lines if line["status"] != "NO_MEASUREMENT"] == []
    document = verdict.to_dict()
    r2 = document["judgment"]["r2"]
    assert r2["jobs"] == 1 and r2["worker_counts"] == {"1": 2}
    assert r2["profile"]["status_counts"] == {"MEASURED": 0, "PARTIAL": 0, "NO_MEASUREMENT": 2}
    assert r2["profile"]["reason_counts"] == {"OBSERVER_UNAVAILABLE": 2}
    killed = [item for claim in document["claims"] if claim.get("mutation")
              for item in claim["mutation"]["killed"]]
    assert {item["first_failing_test"] for item in killed} == {
        "tests/test_behavior.py::test_behavior"
    }

    # A resumed run reuses the state records (the loader accepts `resources`)
    # and classifies identically.
    resumed_progress = tmp_path / "resumed.jsonl"
    resumed = judge_lane(progress_artifact=resumed_progress)
    assert resumed.outcome is Outcome.PASS
    assert resumed.to_dict()["claims"] == verdict.to_dict()["claims"]
    resumed_events = [
        json.loads(line)
        for line in resumed_progress.read_text(encoding="utf-8").splitlines()
    ]
    resume = [event for event in resumed_events if event["event"] == "resume"]
    assert [(event["resumed_total"], event["rejected_total"]) for event in resume] == [
        (2, 0)
    ]
    assert [event for event in resumed_events if event["event"] == "candidate"] == []
