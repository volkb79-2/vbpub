"""B111 measurement evidence: baseline setup/teardown forwarding (O9), the
resource sidecar and event-time readers, `tree_sample` (O7b) and the
end-to-end candidate resource evidence (O8).

Nothing here asserts a duration or a sample count: the values are properties
of records the tests write themselves or of kernel-reported counters.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assay import liveness


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
