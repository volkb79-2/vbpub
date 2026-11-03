"""B207 -- boundary and fault paths of the scheduler and its side files.

The happy paths live in ``test_schedule_tem.py`` and
``test_schedule_integration.py``; these pin the guards: history entries that
lack one measurement, a live median that changes nothing, a scheduler that
cannot make progress, the CLI budget override, and every way a schedule side
file can be unreadable or unwritable (always advisory, never a campaign error).
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import R0_LANE

from assay import _schedule, cli as cli_module, mutation as mutation_module
from assay._schedule import HistoryEntry, Prediction, TemScheduler
from assay.errors import LaneConfigError

MIB = 2**20


def test_history_entry_without_a_wall_time_still_supplies_an_rss_prediction():
    history = {"x" * 64: HistoryEntry("a.py", None, 5 * MIB)}
    (prediction,) = _schedule.resolve_predictions([("y" * 64, "a.py")], history)
    assert prediction.wall_seconds is None
    assert prediction.rss_peak_bytes == 5 * MIB


def test_initial_prediction_is_the_prediction_made_before_any_live_update():
    pairs = [(f"{i:064x}", "a.py") for i in range(2)]
    predictions = [Prediction(1.0, MIB, _schedule.SOURCE_PROFILE)] * 2
    scheduler = TemScheduler(pairs, predictions, budget_bytes=None, workers=1)
    assert scheduler.initial_prediction(1) == predictions[1]


def test_a_live_median_equal_to_the_current_prediction_changes_nothing():
    pairs = [(f"{i:064x}", "a.py") for i in range(8)]
    same = Prediction(30.0, 200 * MIB, _schedule.SOURCE_HISTORY_FILE)
    scheduler = TemScheduler(pairs, [same] * 8, budget_bytes=None, workers=1)
    for step in range(5):
        index = scheduler.next_candidate(float(step))
        scheduler.complete(index, step + 1.0, wall_seconds=30.0, rss_peak_bytes=200 * MIB)
    assert scheduler.prediction(5) == same
    assert scheduler.summary(errors={})["live_median_files"] == 0


def test_error_says_so_when_the_candidate_had_no_rss_prediction():
    scheduler = TemScheduler(
        [("a" * 64, "a.py")],
        [Prediction(None, None, _schedule.SOURCE_NONE)],
        budget_bytes=None,
        workers=1,
    )
    error = scheduler.error(0, wall_seconds=1.0, rss_peak_bytes=5 * MIB)
    assert error["rss_ratio"] is None
    assert "no RSS prediction" in error["reason"]


def test_replay_refuses_a_scheduler_that_stalls_with_work_remaining(monkeypatch):
    monkeypatch.setattr(TemScheduler, "next_candidate", lambda self, now: None)
    with pytest.raises(RuntimeError, match="stalled"):
        _schedule.plan_dispatch_order(
            [("a" * 64, "a.py")],
            [Prediction(1.0, MIB, _schedule.SOURCE_PROFILE)],
            [1.0],
            workers=1,
        )


def test_memory_budget_override_requires_a_native_r2_lane(tmp_path):
    lane_file = tmp_path / "assay.toml"
    lane_file.write_text(R0_LANE, encoding="utf-8")
    args = SimpleNamespace(file=lane_file, lane="package", memory_budget_bytes=1000)
    with pytest.raises(LaneConfigError, match="--memory-budget-bytes requires a native R2"):
        cli_module._cmd_run(args, [], io.StringIO(), io.StringIO())


def test_memory_budget_override_is_applied_to_a_native_r2_lane(tmp_path):
    (tmp_path / "src").mkdir()
    lane_file = tmp_path / "assay.toml"
    lane_file.write_text(
        "schema_version = 2\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0", "R2"]\n'
        'enforcement = "gate"\n'
        'argv = ["/bin/true"]\n'
        "env = {}\n"
        'env_passthrough = ["PATH"]\n'
        'budget = "1m"\n'
        "allow_argv_append = false\n\n"
        "[lanes.package.isolation]\n"
        'snapshot_selection = "repository"\n\n'
        "[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["src"]\n'
        'base = "main"\n\n'
        "[lanes.package.judge.mutation]\n"
        "jobs_max = 2\n"
        "max_mutants = 10\n"
        'operators = ["python:bool-const-flip"]\n',
        encoding="utf-8",
    )
    # The override is applied, then the unrelated --jobs/--candidates-file
    # conflict (raised after it) ends the call before any lane executes.
    args = SimpleNamespace(
        file=lane_file,
        lane="package",
        jobs=2,
        memory_budget_bytes=10**9,
        candidates_file=tmp_path / "missing-candidates.json",
        state_dir=tmp_path / "state",
        pilot_jobs=None,
    )
    with pytest.raises(LaneConfigError, match="--jobs cannot be combined"):
        cli_module._cmd_run(args, [], io.StringIO(), io.StringIO())


@pytest.fixture
def state_dir(tmp_path: Path):
    path = tmp_path / "state"
    path.mkdir()
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield path, descriptor
    finally:
        os.close(descriptor)


def test_history_that_is_not_a_regular_file_is_reported_and_ignored(state_dir):
    path, descriptor = state_dir
    os.mkfifo(path / _schedule.HISTORY_FILE_NAME)
    history, reason = mutation_module._read_schedule_history(path, descriptor)
    assert history == {}
    assert "not a regular file" in reason


def test_history_over_its_size_limit_is_reported_and_ignored(state_dir, monkeypatch):
    path, descriptor = state_dir
    (path / _schedule.HISTORY_FILE_NAME).write_bytes(b"{}")
    monkeypatch.setattr(_schedule, "HISTORY_LIMIT_BYTES", 1)
    history, reason = mutation_module._read_schedule_history(path, descriptor)
    assert history == {}
    assert "size limit" in reason


@pytest.mark.parametrize("with_descriptor", [False, True])
@pytest.mark.parametrize("temporary_already_gone", [False, True])
def test_a_failed_side_file_replace_leaves_no_temporary_behind(
    state_dir, monkeypatch, with_descriptor, temporary_already_gone
):
    path, descriptor = state_dir
    real_replace = os.replace

    def failing_replace(source, destination, *args, **kwargs):
        if temporary_already_gone:
            if with_descriptor:
                os.unlink(source, dir_fd=kwargs["src_dir_fd"])
            else:
                os.unlink(source)
        raise OSError("replace refused")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="replace refused"):
        mutation_module._write_state_side_file(
            path, descriptor if with_descriptor else None, "side.v1", b"payload"
        )
    monkeypatch.setattr(os, "replace", real_replace)
    assert list(path.iterdir()) == []


def test_an_unwritable_side_file_is_an_advisory_not_an_error(tmp_path, capsys):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    mutation_module._persist_schedule_side_files(
        blocker,
        None,
        history={},
        updates={"a" * 64: HistoryEntry("a.py", 1.0, MIB)},
        scheduler=None,
        errors={},
        history_reason=None,
    )
    assert "could not write the candidate schedule side files" in capsys.readouterr().err
