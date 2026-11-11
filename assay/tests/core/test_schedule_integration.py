"""B207 -- the scheduler through the real native R2 dispatch loop.

* ``jobs=N`` produces the same judgment as serial for a small synthetic suite,
  whatever order the scheduler dispatched in;
* progress and state drain by DISPATCH position while ``candidate_index`` keeps
  its canonical meaning;
* the side files, the new progress keys and the admission budget reach the
  stream; configuration and CLI refuse malformed scheduling input.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from conftest import (
    R0_LANE,
    Project,
    make_deadline,
    make_lane,
    make_plan,
    prepared_snapshot,
    set_key,
)

from assay import _schedule, _window_host, mutation as mutation_module
from assay import runner as runner_module
from assay.adapters.python import PythonAdapter
from assay.config import load_lane_file
from assay.errors import LaneConfigError
from assay.mutation import MutationTarget
from assay.resource_limits import ResourceLimitCounters
from assay.runner import execute_command

from test_mutation_isolation import (
    _TEXT,
    _CheapCloneWorkspaceProvider,
    _clock,
    _make_decide_by_mutated_content,
    _prepared_snapshot_with_workspace,
    _seed_repo,
)

MIB = 2**20

#: Three sites (killed, survived, crashed). The fourth site of the shared text
#: raises a timeout the executor treats as the LANE's own, which stops a sweep
#: by design and so cannot take part in an order-parity comparison.
_TARGETS = (MutationTarget(path="pkg/flags.py", text=_TEXT, lines=frozenset({2, 3, 4})),)


@pytest.fixture(autouse=True)
def _hosted_windows(monkeypatch):
    # The gate container exports a real observer socket; these tests assert the
    # no-observer (OBSERVER_UNAVAILABLE) profile, so the ambient one must not leak in.
    monkeypatch.delenv("ASSAY_RESOURCE_OBSERVER_SOCKET", raising=False)
    counters = ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    monkeypatch.setattr(
        mutation_module, "_read_candidate_resource_counters", lambda: counters
    )
    monkeypatch.setattr(
        mutation_module,
        "_read_candidate_resource_counters_during_campaign",
        lambda: (counters, None),
    )
    monkeypatch.setattr(
        _window_host,
        "run_window",
        lambda plan, *, cwd, timeout, process_runner, clock, profile=None: runner_module.execute_plan(
            plan, cwd=cwd, timeout=timeout, process_runner=process_runner, clock=clock
        ),
    )
    monkeypatch.setattr(_window_host, "can_host", lambda *_args: True)


def _run(
    tmp_path: Path,
    name: str,
    *,
    jobs: int,
    state_root: Path | None = None,
    schedule=None,
    resume: bool = False,
):
    lane = make_lane(argv=("pytest", "-q"))
    repo = _seed_repo(tmp_path, f"repo-{name}")
    scratch_root = tmp_path / f"scratch-{name}"
    scratch_root.mkdir()
    decide = _make_decide_by_mutated_content(repo.path)
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    progress = tmp_path / f"progress-{name}.jsonl"
    if jobs == 1:
        context = prepared_snapshot(repo, scratch_root=scratch_root)
    else:
        context = _prepared_snapshot_with_workspace(
            repo,
            scratch_root,
            workspace=_CheapCloneWorkspaceProvider(tmp_path / f"cheap-{name}"),
        )
    with context as prepared:
        mutation = mutation_module._run_mutation_impl(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=jobs,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=_clock,
            state_root=state_root,
            progress_artifact=progress,
            schedule=schedule,
            resume=resume,
        )
    events = [
        json.loads(line)
        for line in progress.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return mutation, [e for e in events if e.get("event") == "candidate"]


def _history_for(ids_by_index: dict[int, str], rss_mib: dict[int, int], wall: dict[int, float]):
    return _schedule.render_history(
        {
            ids_by_index[i]: _schedule.HistoryEntry(
                "pkg/flags.py", wall[i], rss_mib[i] * MIB
            )
            for i in ids_by_index
        }
    )


def test_parallel_judgment_equals_serial_under_a_non_canonical_dispatch_order(tmp_path):
    serial_state = tmp_path / "state-serial"
    serial, serial_events = _run(tmp_path, "serial", jobs=1, state_root=serial_state)
    ids = {event["candidate_index"]: event["candidate_id"] for event in serial_events}
    assert sorted(ids) == [0, 1, 2]

    # Prime a history so that candidate 1 is high and candidate 2 is the longest
    # normal: the dispatch order must differ from canonical.
    parallel_state = tmp_path / "state-parallel"
    parallel_state.mkdir()
    (parallel_state / _schedule.HISTORY_FILE_NAME).write_bytes(
        _history_for(
            ids,
            {0: 100, 1: 600, 2: 100},
            {0: 5.0, 1: 50.0, 2: 20.0},
        )
    )
    parallel, parallel_events = _run(tmp_path, "parallel", jobs=3, state_root=parallel_state)

    assert parallel.to_dict() == serial.to_dict()

    by_slot = sorted(parallel_events, key=lambda e: e["dispatch_position"])
    assert [e["dispatch_position"] for e in by_slot] == [0, 1, 2]
    # the high goes first, then the longest-predicted normal
    assert [e["candidate_index"] for e in by_slot] == [1, 2, 0]
    assert by_slot[0]["prediction"]["class"] == "high"
    assert by_slot[0]["prediction"]["source"] == "profile"
    assert by_slot[0]["prediction"]["wall_seconds"] == 50.0
    # emitted in dispatch order, never in canonical order
    assert [e["dispatch_position"] for e in parallel_events] == [0, 1, 2]
    assert [e["candidate_index"] for e in parallel_events] == [1, 2, 0]
    for event in parallel_events:
        assert "prediction_error" in event
        # the fake runner measures no RSS: the error says why instead of a ratio
        assert event["prediction_error"]["rss_ratio"] is None
        assert "no peak RSS" in event["prediction_error"]["reason"]
    # a serial run keeps the pre-B207 event shape
    assert all("dispatch_position" not in event for event in serial_events)


def test_schedule_json_and_history_are_written_for_a_parallel_run(tmp_path):
    state = tmp_path / "state"
    _, events = _run(tmp_path, "one", jobs=2, state_root=state, schedule=_schedule.ScheduleOptions(
        memory_budget_bytes=900 * MIB,
        prediction_rss_bytes=100 * MIB,
        prediction_wall_seconds=4.0,
    ))
    summary = json.loads((state / _schedule.SCHEDULE_FILE_NAME).read_text(encoding="utf-8"))
    assert summary["schema"] == _schedule.SCHEDULE_SCHEMA
    assert summary["policy"] == "tem-lpt"
    assert summary["workers"] == 2
    assert summary["memory_budget_bytes"] == 900 * MIB
    assert summary["candidates"] == 3
    assert summary["source_counts"]["operator"] == 3
    assert summary["admission_overshoots"] == 0
    assert len(summary["input_sha256"]) == 64
    history, reason = _schedule.parse_history((state / _schedule.HISTORY_FILE_NAME).read_bytes())
    assert reason is None
    assert {e["candidate_id"] for e in events} == set(history)
    # neither side file is mistaken for a candidate state record
    assert len(list(state.glob("*.json"))) == 3


def test_a_second_run_uses_the_first_runs_profiles_as_source_one(tmp_path):
    state = tmp_path / "state"
    _run(tmp_path, "first", jobs=1, state_root=state)
    # the serial run recorded history; a fresh parallel run over a copy of just
    # the history file predicts every candidate from its own profile
    copy = tmp_path / "state-copy"
    copy.mkdir()
    shutil.copy(state / _schedule.HISTORY_FILE_NAME, copy / _schedule.HISTORY_FILE_NAME)
    _, events = _run(tmp_path, "second", jobs=2, state_root=copy)
    assert {e["prediction"]["source"] for e in events} == {"profile"}


def test_a_corrupt_history_never_stops_a_campaign_or_changes_its_result(tmp_path):
    serial, _ = _run(tmp_path, "serial", jobs=1)
    state = tmp_path / "state"
    state.mkdir()
    (state / _schedule.HISTORY_FILE_NAME).write_bytes(b"{ not json")
    parallel, events = _run(tmp_path, "parallel", jobs=3, state_root=state)
    assert parallel.to_dict() == serial.to_dict()
    assert {e["prediction"]["source"] for e in events} == {"none"}
    # no predictions at all: canonical dispatch order
    assert [e["candidate_index"] for e in events] == [0, 1, 2]
    summary = json.loads((state / _schedule.SCHEDULE_FILE_NAME).read_text(encoding="utf-8"))
    assert "not valid JSON" in summary["history_reason"]


def test_admission_with_a_tiny_budget_still_completes_every_candidate(tmp_path):
    """Every candidate is predicted over the budget: each one runs alone, none
    is refused forever, and the judgment equals the serial one."""
    serial, _ = _run(tmp_path, "serial", jobs=1)
    parallel, events = _run(
        tmp_path,
        "tiny",
        jobs=3,
        state_root=tmp_path / "state",
        schedule=_schedule.ScheduleOptions(memory_budget_bytes=1, prediction_rss_bytes=MIB),
    )
    assert parallel.to_dict() == serial.to_dict()
    assert sorted(e["candidate_index"] for e in events) == [0, 1, 2]


def test_a_parallel_sweep_carries_profile_worker_count_and_schedule_telemetry(tmp_path):
    mutation, events = _run(
        tmp_path, "telemetry", jobs=3, state_root=tmp_path / "state",
        schedule=_schedule.ScheduleOptions(memory_budget_bytes=4 * MIB * 1024),
    )
    telemetry = mutation.r2_telemetry
    assert telemetry is not None
    # No observer in this test: every candidate is an explicit NO_MEASUREMENT.
    assert telemetry["profile"]["status_counts"] == {"NO_MEASUREMENT": 3}
    assert telemetry["profile"]["reason_counts"] == {"OBSERVER_UNAVAILABLE": 3}
    assert telemetry["worker_counts"] == {"3": 3}
    schedule = telemetry["schedule"]
    assert schedule["policy"] == "tem-lpt" and schedule["jobs"] == 3
    assert schedule["memory_budget_bytes"] == 4 * MIB * 1024
    assert sum(schedule["prediction_sources"].values()) == 3
    assert set(schedule["error"]) == {
        "rss_ratio", "wall_ratio", "class_confusion", "admission_overshoots",
    }
    for event in events:
        assert event["worker_count"] == 3
        assert event["profile"]["reason"] == "OBSERVER_UNAVAILABLE"
        # the scheduler's error record names the profile's reason, not a guess
        assert event["prediction_error"]["profile_reason"] == "OBSERVER_UNAVAILABLE"
        assert "OBSERVER_UNAVAILABLE" in event["prediction_error"]["reason"]


def test_a_serial_sweep_reports_the_canonical_schedule(tmp_path):
    mutation, _ = _run(tmp_path, "serial-telemetry", jobs=1)
    schedule = mutation.r2_telemetry["schedule"]
    assert schedule["policy"] == "canonical"
    assert schedule["thresholds"] is None and schedule["error"] is None
    assert mutation.r2_telemetry["worker_counts"] == {"1": 3}


def test_a_resumed_campaign_with_mixed_worker_counts_reports_each_count(tmp_path):
    """B203: every state record states the worker count that produced it, and
    the telemetry (hence the verdict) carries per-count totals, not just the
    latest invocation's selection."""
    state = tmp_path / "state"
    _run(tmp_path, "first", jobs=1, state_root=state)
    records = sorted(state.glob("[0-9a-f]*.json"))
    assert len(records) == 3
    assert {json.loads(p.read_text())["worker_count"] for p in records} == {1}
    records[0].unlink()  # one candidate must run again, now at 3 workers
    mutation, events = _run(tmp_path, "second", jobs=3, state_root=state, resume=True)
    assert len(events) == 1 and events[0]["worker_count"] == 3
    assert mutation.r2_telemetry["worker_counts"] == {"1": 2, "3": 1}
    assert mutation.r2_telemetry["profile"]["status_counts"] == {"NO_MEASUREMENT": 3}
    counts = {
        json.loads(p.read_text())["worker_count"] for p in state.glob("[0-9a-f]*.json")
    }
    assert counts == {1, 3}


# -- configuration and CLI --------------------------------------------------


_ISOLATION_TABLE = '\n[lanes.package.isolation]\nsnapshot_selection = "repository"\n'
_JUDGE = '\n[lanes.package.judge]\nlanguage = "python"\nsource_roots = ["src"]\nbase = "main"\n'


def _lane_with(extra: str) -> str:
    body = set_key(R0_LANE, "rigor", '["R0", "R2"]')
    return (
        body
        + _ISOLATION_TABLE
        + _JUDGE
        + "\n[lanes.package.judge.mutation]\njobs_max = 4\nmax_mutants = 50\n"
        + 'operators = ["python:compare-swap"]\n'
        + extra
    )


def test_scheduling_keys_load_and_echo_when_declared(project: Project):
    lane = _lane_with(
        "schedule_high_factor = 2\nschedule_low_factor = 0.25\n"
        "prediction_wall_seconds = 12.5\nprediction_rss_bytes = 1048576\n"
    )
    mutation = load_lane_file(project.write(lane)).lane("package").judge.mutation
    assert mutation.schedule_high_factor == 2.0
    assert mutation.schedule_low_factor == 0.25
    assert mutation.prediction_wall_seconds == 12.5
    assert mutation.prediction_rss_bytes == 1048576
    declared = mutation.as_declared()
    assert declared["schedule_high_factor"] == 2.0
    assert declared["prediction_rss_bytes"] == 1048576


def test_scheduling_keys_are_absent_by_default(project: Project):
    mutation = load_lane_file(project.write(_lane_with(""))).lane("package").judge.mutation
    assert mutation.schedule_high_factor is None
    assert mutation.memory_budget_bytes is None
    assert "schedule_high_factor" not in mutation.as_declared()


@pytest.mark.parametrize(
    "bad",
    [
        "schedule_high_factor = 1.0",
        "schedule_high_factor = 0.5",
        "schedule_low_factor = 1.0",
        "schedule_low_factor = 0",
        "schedule_low_factor = true",
        'schedule_high_factor = "2"',
        "prediction_wall_seconds = 0",
        "prediction_wall_seconds = -1.5",
        "prediction_rss_bytes = 0",
        "prediction_rss_bytes = 1.5",
        "prediction_rss_bytes = true",
    ],
)
def test_malformed_scheduling_keys_are_refused_at_load(bad, project: Project):
    with pytest.raises(LaneConfigError, match="judge.mutation"):
        load_lane_file(project.write(_lane_with(bad + "\n")))


def test_memory_budget_resolution_prefers_the_flag_and_refuses_garbage(monkeypatch):
    from assay import cli

    monkeypatch.delenv(cli.MEMORY_BUDGET_ENV, raising=False)
    assert cli._resolve_memory_budget(None) is None
    assert cli._resolve_memory_budget(123) == 123
    monkeypatch.setenv(cli.MEMORY_BUDGET_ENV, "456")
    assert cli._resolve_memory_budget(None) == 456
    assert cli._resolve_memory_budget(7) == 7
    monkeypatch.setenv(cli.MEMORY_BUDGET_ENV, "")
    assert cli._resolve_memory_budget(None) is None
    for bad in ("0", "-5", "1.5", "abc", "0x10"):
        monkeypatch.setenv(cli.MEMORY_BUDGET_ENV, bad)
        with pytest.raises(LaneConfigError):
            cli._resolve_memory_budget(None)
    monkeypatch.delenv(cli.MEMORY_BUDGET_ENV)
    with pytest.raises(LaneConfigError):
        cli._resolve_memory_budget(0)


def test_lane_inventory_advertises_the_scheduling_capabilities():
    from assay import cli

    assert cli.CAPABILITIES == (
        "r2-memory-budget",
        "r2-schedule-tem",
        "r2-resource-profile",
        "verdict-v19",
    )
