"""Deterministic edge coverage for liveness event and cleanup boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay import liveness


def _write_events(path: Path, records, *, final_newline=True):
    text = "".join(json.dumps(record) + "\n" for record in records)
    if not final_newline:
        text = text[:-1]
    path.write_text(text, encoding="utf-8")


def test_owner_and_selected_tests_fall_back_when_no_session_start_exists(tmp_path):
    events = tmp_path / "events.jsonl"
    records = [
        {"event": "test", "pid": 10, "nodeid": "first"},
        {"event": "test", "pid": 11, "nodeid": "second"},
    ]
    _write_events(events, records)

    assert liveness._session_owner_pid(records) is None
    assert liveness._selected_test_events(events) == records


def test_baseline_gaps_fall_back_to_merged_events_for_single_record_processes(
    tmp_path,
):
    events = tmp_path / "events.jsonl"
    _write_events(
        events,
        [
            {"event": "session_start", "pid": 10, "t": 0},
            {"event": "test", "pid": 11, "t": 1},
            {"event": "test", "pid": 12, "t": 4},
        ],
    )
    gaps = liveness.baseline_event_gaps(events)
    assert gaps == liveness.BaselineEventGaps(worst_gap_s=3.0, leading_gap_s=1.0)


def test_baseline_gaps_without_a_stamped_session_owner_use_the_measured_worst(
    tmp_path,
):
    events = tmp_path / "events.jsonl"
    _write_events(
        events,
        [
            {"event": "phase", "pid": 10, "t": 0},
            {"event": "test", "pid": 10, "t": 2},
        ],
    )
    gaps = liveness.baseline_event_gaps(events)
    assert gaps == liveness.BaselineEventGaps(worst_gap_s=2.0, leading_gap_s=2.0)


@pytest.mark.parametrize(
    ("pid", "expected"),
    [(None, (1, True)), (101, (1, False))],
    ids=("missing-finish-pid", "other-process-finish"),
)
def test_incremental_reader_classifies_torn_finish_record(pid, expected, tmp_path):
    events = tmp_path / "events.jsonl"
    record = {"event": "session_finish"}
    if pid is not None:
        record["pid"] = pid
    _write_events(events, [record], final_newline=False)

    assert liveness._EventProgressReader(candidate_pid=100).read(events) == expected


def test_incremental_reader_open_failure_resets_and_can_recover(monkeypatch, tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text('{"event":"session_finish","pid":100}\n', encoding="utf-8")
    reader = liveness._EventProgressReader(candidate_pid=100)
    real_open = Path.open

    def fail_selected_path(self, *args, **kwargs):
        if self == events:
            raise PermissionError("injected read refusal")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_selected_path)
    assert reader.read(events) == (0, False)
    monkeypatch.setattr(Path, "open", real_open)
    assert reader.read(events) == (1, True)


def test_cleanup_process_group_tolerates_disappearance_and_wait_failure(tmp_path):
    killed = []

    def gone(pgid, signum):
        killed.append((pgid, signum))
        raise ProcessLookupError(pgid)

    class Process:
        pid = 123

        def wait(self, *, timeout):
            assert timeout == 5.0
            raise RuntimeError("already reaped")

    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "events",
        expect_next_event_within_s=1.0,
        process_group_killer=gone,
    )
    runner._cleanup_process_group(Process())
    assert len(killed) == 1
