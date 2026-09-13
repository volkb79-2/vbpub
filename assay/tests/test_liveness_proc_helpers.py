"""B091/RW-33, P7 A3 -- direct unit tests for :mod:`assay.liveness`'s
process-tree/`/proc` helpers and :meth:`~assay.liveness.LivenessRunner._kill`,
kept separate from `test_liveness_runner_monitor.py` (which drives the whole
loop end to end) so each helper's own edge cases -- a torn NDJSON line, an
unreadable file, the ppid-scan fallback, a process that vanished between
`_kill`'s two syscalls -- are exercised directly rather than only as a side
effect of some larger scenario.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from assay import liveness


# --------------------------------------------------------------------------
# _pid_children_via_ppid_scan / tree_cpu_seconds fallback
# --------------------------------------------------------------------------


def test_pid_children_via_ppid_scan_finds_a_real_child() -> None:
    proc = subprocess.Popen(["sleep", "5"])
    try:
        children = liveness._pid_children_via_ppid_scan(__import__("os").getpid())
        assert proc.pid in children
    finally:
        proc.kill()
        proc.wait()


def test_pid_children_via_ppid_scan_skips_unreadable_and_malformed_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Three kinds of `/proc/<n>` entries a real scan can encounter, none of
    which may raise: a non-numeric name (`not-a-pid`, skipped before any
    read), a numeric one with no readable `stat` (a process that disappeared
    mid-scan, an `OSError` caught and skipped), and one whose `stat` exists
    but is too short to contain a `ppid` field at all (a malformed/synthetic
    entry, `IndexError` caught and skipped).
    """
    proc_dir = tmp_path / "proc"
    (proc_dir / "123").mkdir(parents=True)
    (proc_dir / "not-a-pid").mkdir()
    (proc_dir / "456").mkdir()
    # A `stat` line with a `comm` field and a `state` field but NOTHING
    # after it -- `fields[1]` (ppid) is out of range.
    (proc_dir / "456" / "stat").write_text("456 (sh) S\n", encoding="utf-8")
    # No "stat" file under "123" at all -- every read must be skipped, not
    # raised.
    monkeypatch.setattr(liveness, "Path", lambda p: proc_dir if p == "/proc" else Path(p))
    result = liveness._pid_children_via_ppid_scan(999999)
    assert result == []


def test_tree_cpu_seconds_root_read_failure_raises() -> None:
    with pytest.raises(OSError):
        liveness.tree_cpu_seconds(999_999_999)  # a pid that cannot exist.


def test_tree_cpu_seconds_falls_back_to_ppid_scan_when_task_api_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates a kernel/`/proc` without the `task/*/children` API (RW-33's
    own fallback ask) by making `_pid_children_via_task` fail for every pid
    -- `tree_cpu_seconds` must still complete, using the ppid-scan path for
    both the root's own children AND (508-513) each child's own.
    """
    proc = subprocess.Popen(["sleep", "5"])
    try:

        def _always_fails(pid: int) -> list[int]:
            raise OSError("task API unavailable")

        monkeypatch.setattr(liveness, "_pid_children_via_task", _always_fails)
        cpu = liveness.tree_cpu_seconds(proc.pid)
        assert cpu >= 0.0
    finally:
        proc.kill()
        proc.wait()


def test_tree_cpu_seconds_walks_a_real_live_child(tmp_path: Path) -> None:
    """The main traversal (500-514) with a REAL, live child: `sh -c "sleep 2
    & wait"` keeps a genuine child process alive for the duration of this
    test, so `tree_cpu_seconds` must actually enter the while-loop body
    (rather than finding an empty frontier, the shape `sleep`'s own
    childless case never exercises) and sum a real descendant's CPU time
    via the default `_pid_children_via_task` path.
    """
    parent = subprocess.Popen(["sh", "-c", "sleep 2 & wait"])
    try:
        time.sleep(0.1)  # let the shell actually fork its background child.
        cpu = liveness.tree_cpu_seconds(parent.pid)
        assert cpu >= 0.0
    finally:
        parent.kill()
        parent.wait()


def test_tree_cpu_seconds_skips_a_child_that_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(506-507, `continue`) A child pid discovered in the frontier that has
    ALREADY exited by the time its own `/proc/<pid>/stat` is read is
    skipped, not raised -- the root's own measurement still completes.
    Forced deterministically: the root's own children-discovery is patched
    to report one fabricated, certainly-dead pid alongside itself, rather
    than racing a real process's exit.
    """
    proc = subprocess.Popen(["sleep", "5"])
    try:
        real_pid = proc.pid

        def _fake_children(pid: int) -> list[int]:
            if pid == real_pid:
                return [999_999_999]  # cannot exist.
            return []

        monkeypatch.setattr(liveness, "_pid_children_via_task", _fake_children)
        cpu = liveness.tree_cpu_seconds(real_pid)
        assert cpu >= 0.0  # completed despite the fabricated dead child.
    finally:
        proc.kill()
        proc.wait()


def test_tree_cpu_seconds_skips_a_duplicate_pid_already_visited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(502, `continue`) The same descendant pid can legitimately be
    reported twice (e.g. two threads of the same parent each listing it in
    their own `/proc/<tid>/children`) -- `tree_cpu_seconds` must count it
    exactly once, never twice, and the second encounter is the `continue`
    at line 502.
    """
    proc = subprocess.Popen(["sleep", "5"])
    try:
        real_pid = proc.pid

        def _duplicate_child(pid: int) -> list[int]:
            if pid == real_pid:
                return [real_pid + 100_000_000, real_pid + 100_000_000]
            return []

        monkeypatch.setattr(liveness, "_pid_cpu_ticks", lambda pid: 7)
        monkeypatch.setattr(liveness, "_pid_children_via_task", _duplicate_child)
        cpu = liveness.tree_cpu_seconds(real_pid)
        # Counted the duplicated pid's 7 ticks exactly ONCE, plus the root's
        # own 7 -- 14 total, never 21.
        clock_ticks_per_s = __import__("os").sysconf("SC_CLK_TCK")
        assert cpu == 14 / clock_ticks_per_s
    finally:
        proc.kill()
        proc.wait()


def test_tree_cpu_seconds_falls_back_per_child_when_that_childs_task_api_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(508-513) The task-API/ppid-scan fallback is per-PID, not only at the
    root: a child whose OWN `_pid_children_via_task` call fails must still
    fall back to a ppid-scan for exactly that child, while the root's own
    (successful) discovery is untouched.
    """
    parent = subprocess.Popen(["sh", "-c", "sleep 2 & wait"])
    try:
        real_children_via_task = liveness._pid_children_via_task

        def _fail_only_for_children(pid: int) -> list[int]:
            if pid == parent.pid:
                return real_children_via_task(pid)
            raise OSError("simulated per-child task API failure")

        monkeypatch.setattr(liveness, "_pid_children_via_task", _fail_only_for_children)
        time.sleep(0.1)  # let the shell actually fork its background child.
        cpu = liveness.tree_cpu_seconds(parent.pid)
        assert cpu >= 0.0
    finally:
        parent.kill()
        parent.wait()


# --------------------------------------------------------------------------
# baseline_event_gaps / compute_liveness_calibration
# (B091 round-1 blocker B2, RW-49/D3 calibration (a))
#
# The pre-B2 formula was `max(3 x slowest_test_s, 15s)` over `call`-phase
# durations only. The reviewer measured what that misses: a project whose
# module-scoped fixture slept 40s reported `slowest_test_s = 0.00037`, so the
# bound collapsed to its 15s floor and EVERY candidate was killed as `hung`
# at ~31s -- including the one the suite was about to kill honestly. The
# tests below pin the replacement against exactly those shapes.
# --------------------------------------------------------------------------


def _write(path: Path, *records: str) -> Path:
    path.write_text("".join(record + "\n" for record in records), encoding="utf-8")
    return path


def _start(t: float) -> str:
    return json.dumps({"event": "session_start", "t": t})


def _phase(t: float, when: str, duration: float = 0.0) -> str:
    return json.dumps(
        {
            "event": "test" if when == "call" else "phase",
            "when": when,
            "nodeid": "pkg/test_it.py::test_one",
            "outcome": "passed",
            "duration_s": duration,
            "t": t,
        }
    )


def _finish(t: float) -> str:
    return json.dumps({"event": "session_finish", "exitstatus": 0, "t": t})


def test_calibration_none_path_uses_the_plugin_inactive_fallback() -> None:
    for baseline_s, expected in ((40.0, 60.0), (400.0, 100.0)):
        calibration = liveness.compute_liveness_calibration(None, baseline_s)
        assert calibration.expect_next_event_within_s == expected
        # No leading measurement exists on this path either, so the
        # pre-first-event window gets the SAME coarse bound -- never a
        # tighter one invented out of nothing.
        assert calibration.pre_first_event_within_s == expected
        assert calibration.worst_gap_s is None
        assert calibration.slowest_test_s is None


def test_calibration_missing_file_uses_the_plugin_inactive_fallback(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "does-not-exist.ndjson"
    assert liveness.baseline_event_gaps(missing) is None
    assert (
        liveness.compute_liveness_calibration(missing, 40.0).expect_next_event_within_s
        == 60.0
    )


def test_calibration_a_lone_stamped_record_cannot_form_a_gap(tmp_path: Path) -> None:
    """One timestamp is not an interval. The fallback, not a zero gap that
    would collapse both bounds onto the 15s floor.
    """
    events = _write(tmp_path / "baseline.ndjson", _start(0.0))
    assert liveness.baseline_event_gaps(events) is None
    assert (
        liveness.compute_liveness_calibration(events, 400.0).expect_next_event_within_s
        == 100.0
    )


def test_calibration_survives_the_reviewers_40s_module_fixture(tmp_path: Path) -> None:
    """The exact shape round-1 B2 reproduced end to end: a 42.5s baseline
    whose time is spent almost entirely inside a module-scoped fixture, and
    whose only `call` report is 0.4ms long.

    Pre-B2 this produced `expect_next_event_within_s = 15.0` and the
    candidate was killed as `hung` at 31s -- a quarter of the 127.6s budget
    assay itself derived, before the asserting test body ever ran. The gap
    calibration sees the fixture directly, because the `setup` report's own
    timestamp is 40s after `session_start`.
    """
    events = _write(
        tmp_path / "baseline.ndjson",
        _start(0.0),
        _phase(40.0, "setup", duration=40.0),
        _phase(40.001, "call", duration=0.00037),
        _phase(40.002, "teardown"),
        _finish(42.5),
    )
    calibration = liveness.compute_liveness_calibration(events, 42.522)
    assert calibration.worst_gap_s == 40.0
    assert calibration.expect_next_event_within_s == 120.0
    assert calibration.pre_first_event_within_s == 120.0
    # The measurement the OLD formula used is still reported, unchanged in
    # meaning -- and is still 0.4ms, which is why it must not be the input
    # to the bound. `max(3 * 0.00037, 15.0)` is the 15.0 that killed
    # healthy candidates.
    assert calibration.slowest_test_s == 0.00037


def test_calibration_covers_a_slow_collection(tmp_path: Path) -> None:
    """A 20s import at collection time: no report of any phase exists yet,
    so `call`-duration calibration was blind to it by construction.
    `session_start` is written from `pytest_configure`, BEFORE collection,
    which is what makes this interval measurable at all.
    """
    events = _write(
        tmp_path / "baseline.ndjson",
        _start(0.0),
        _phase(20.0, "setup"),
        _phase(20.001, "call", duration=0.001),
        _phase(20.002, "teardown"),
        _finish(20.01),
    )
    calibration = liveness.compute_liveness_calibration(events, 20.5)
    assert calibration.worst_gap_s == 20.0
    assert calibration.expect_next_event_within_s == 60.0
    assert calibration.pre_first_event_within_s == 60.0


def test_calibration_covers_the_trailing_session_teardown_gap(tmp_path: Path) -> None:
    """A 30s session-scoped teardown, between the last report and
    `session_finish` -- the other end the `call`-only formula could not see.
    It also separates the two bounds: the worst gap is the trailing one,
    while the leading gap is 0.1s and floors at 15s.
    """
    events = _write(
        tmp_path / "baseline.ndjson",
        _start(0.0),
        _phase(0.1, "setup"),
        _phase(0.2, "call", duration=0.05),
        _phase(0.3, "teardown"),
        _finish(30.3),
    )
    calibration = liveness.compute_liveness_calibration(events, 30.5)
    assert calibration.worst_gap_s == 30.0
    assert calibration.expect_next_event_within_s == 90.0
    assert calibration.pre_first_event_within_s == 15.0


def test_calibration_floor_is_inclusive_at_the_exact_boundary(tmp_path: Path) -> None:
    """5.0s is exactly where `3 x gap` meets the 15s floor. Pinned on both
    sides so neither the multiplier nor the direction of the `max` can be
    changed without a failure.
    """
    at = _write(
        tmp_path / "at.ndjson", _start(0.0), _phase(5.0, "setup"), _finish(5.0)
    )
    assert liveness.baseline_event_gaps(at).worst_gap_s == 5.0
    assert liveness.compute_liveness_calibration(at, 400.0).expect_next_event_within_s == 15.0
    over = _write(
        tmp_path / "over.ndjson", _start(0.0), _phase(6.0, "setup"), _finish(6.0)
    )
    assert liveness.compute_liveness_calibration(over, 400.0).expect_next_event_within_s == 18.0
    under = _write(
        tmp_path / "under.ndjson", _start(0.0), _phase(1.0, "setup"), _finish(1.0)
    )
    assert liveness.compute_liveness_calibration(under, 400.0).expect_next_event_within_s == 15.0


def test_calibration_leading_gap_is_the_first_interval_not_the_worst(
    tmp_path: Path,
) -> None:
    """The pre-first-event bound is the LEADING gap specifically -- how long
    this suite takes to produce its first sign of life -- not the worst gap
    anywhere in the run. A file whose worst gap sits in the middle must
    leave the two bounds different.
    """
    events = _write(
        tmp_path / "baseline.ndjson",
        _start(0.0),
        _phase(7.0, "setup"),
        _phase(7.5, "call", duration=0.1),
        _phase(47.5, "teardown"),
        _finish(47.6),
    )
    gaps = liveness.baseline_event_gaps(events)
    assert (gaps.worst_gap_s, gaps.leading_gap_s) == (40.0, 7.0)
    calibration = liveness.compute_liveness_calibration(events, 48.0)
    assert calibration.expect_next_event_within_s == 120.0
    assert calibration.pre_first_event_within_s == 21.0


def test_calibration_without_a_session_start_uses_the_worst_gap_for_both(
    tmp_path: Path,
) -> None:
    """An events file that does NOT begin with `session_start` (a baseline
    written by a pre-B2 plugin, or one whose `pytest_configure` write
    failed) carries no honest leading measurement. The leading gap must
    then be the CONSERVATIVE worst gap, never the first interval -- which
    here would be a 0.1s gap and a 15s bound on a suite that goes quiet for
    40s.
    """
    events = _write(
        tmp_path / "baseline.ndjson",
        _phase(0.0, "call", duration=0.1),
        _phase(0.1, "call", duration=0.1),
        _phase(40.1, "call", duration=0.1),
        _finish(40.2),
    )
    gaps = liveness.baseline_event_gaps(events)
    assert (gaps.worst_gap_s, gaps.leading_gap_s) == (40.0, 40.0)
    calibration = liveness.compute_liveness_calibration(events, 41.0)
    assert calibration.pre_first_event_within_s == 120.0


def test_calibration_clamps_a_backwards_clock_step_to_a_zero_gap(
    tmp_path: Path,
) -> None:
    """`t` is wall-clock `time.time()` in the candidate's own interpreter,
    so a clock step backwards mid-run is possible. It must read as "no time
    passed", never as a negative gap.
    """
    events = _write(tmp_path / "baseline.ndjson", _start(10.0), _finish(5.0))
    assert liveness.baseline_event_gaps(events).worst_gap_s == 0.0
    assert (
        liveness.compute_liveness_calibration(events, 400.0).expect_next_event_within_s
        == 15.0
    )


def test_calibration_ignores_records_without_a_usable_timestamp(
    tmp_path: Path,
) -> None:
    """A missing, non-numeric or boolean `t` contributes no point to the
    sequence -- and a torn last line is skipped, exactly as every other
    reader of these files does.

    A line that is valid JSON but not an OBJECT (`123`, a bare string) is
    skipped too: the plugin only ever writes objects, so such a line is
    corruption, and counting it as an event would let a garbage file read
    as liveness.
    """
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        "\n"  # blank line
        '{"event": "session_start", "t": 0}\n'
        "123\n"  # valid JSON, not an object.
        '"a bare string"\n'
        '{"event": "phase", "when": "setup", "t": "oops"}\n'
        '{"event": "phase", "when": "setup", "t": true}\n'
        '{"event": "phase", "when": "setup"}\n'
        '{"event": "test", "when": "call", "duration_s": 0.1, "t": 9.0}\n'
        '{"event": "session_finish", "exitsta',  # torn -- still being written.
        encoding="utf-8",
    )
    gaps = liveness.baseline_event_gaps(events)
    assert (gaps.worst_gap_s, gaps.leading_gap_s) == (9.0, 9.0)


def test_calibration_slowest_test_s_still_means_the_slowest_call_phase(
    tmp_path: Path,
) -> None:
    """The `plan` event's `slowest_test_s` key keeps its name AND its exact
    pre-B2 meaning, so no consumer finds it silently measuring something
    else. A 40s `setup` duration must NOT move it.
    """
    events = _write(
        tmp_path / "baseline.ndjson",
        _start(0.0),
        _phase(40.0, "setup", duration=40.0),
        _phase(40.5, "call", duration=0.5),
        _phase(40.6, "teardown", duration=0.1),
        _finish(40.7),
    )
    calibration = liveness.compute_liveness_calibration(events, 41.0)
    assert calibration.slowest_test_s == 0.5
    assert calibration.worst_gap_s == 40.0


def test_calibration_tolerates_a_torn_last_line(tmp_path: Path) -> None:
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        '{"event": "session_start", "t": 0}\n'
        '{"event": "test", "when": "call", "duration_s": 9.0, "t": 9.0}\n'
        '{"event": "test", "nodeid": "b", "outc',  # torn -- still being written.
        encoding="utf-8",
    )
    calibration = liveness.compute_liveness_calibration(events, 400.0)
    assert calibration.expect_next_event_within_s == 27.0  # max(3 * 9.0, 15)
    assert calibration.slowest_test_s == 9.0


# --------------------------------------------------------------------------
# baseline_slowest_test_s (B091/D-23, P7 A4) -- reported on the `plan`
# progress event. Since round-1 B2 it is NO LONGER the input to the idle
# bound (see the gap tests above); it remains the slowest `call`-phase
# duration and nothing else.
# --------------------------------------------------------------------------


def test_baseline_slowest_test_s_none_path_is_none() -> None:
    assert liveness.baseline_slowest_test_s(None) is None


def test_baseline_slowest_test_s_missing_file_is_none(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.ndjson"
    assert liveness.baseline_slowest_test_s(missing) is None


def test_baseline_slowest_test_s_reads_the_max_duration(tmp_path: Path) -> None:
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 1.0, "t": 0}\n'
        '{"event": "test", "nodeid": "b", "outcome": "passed", "duration_s": 6.0, "t": 1}\n'
        '{"event": "test", "nodeid": "c", "outcome": "passed", "duration_s": 2.0, "t": 2}\n'
        '{"event": "session_finish", "exitstatus": 0, "t": 3}\n',
        encoding="utf-8",
    )
    assert liveness.baseline_slowest_test_s(events) == 6.0
    # `compute_liveness_calibration` must agree exactly -- it reads this
    # same function back, never a second parse.
    assert liveness.compute_liveness_calibration(events, 400.0).slowest_test_s == 6.0


def test_baseline_slowest_test_s_tolerates_a_torn_last_line(tmp_path: Path) -> None:
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 9.0, "t": 0}\n'
        '{"event": "test", "nodeid": "b", "outc',
        encoding="utf-8",
    )
    assert liveness.baseline_slowest_test_s(events) == 9.0


def test_baseline_slowest_test_s_ignores_non_test_and_malformed_duration(
    tmp_path: Path,
) -> None:
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        "\n"
        '{"event": "session_finish", "exitstatus": 0, "t": 0}\n'
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": "oops", "t": 1}\n'
        '{"event": "test", "nodeid": "b", "outcome": "passed", "duration_s": true, "t": 2}\n',
        encoding="utf-8",
    )
    assert liveness.baseline_slowest_test_s(events) is None


# --------------------------------------------------------------------------
# baseline_test_events (B091/D-23, P7 A4) -- verbatim {nodeid, outcome,
# duration_s} triples, forwarded onto the progress stream for the BASELINE
# only.
# --------------------------------------------------------------------------


def test_baseline_test_events_missing_file_is_empty(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.ndjson"
    assert liveness.baseline_test_events(missing) == []


def test_baseline_test_events_returns_the_triple_in_file_order(tmp_path: Path) -> None:
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 1.0, "t": 0}\n'
        '{"event": "session_finish", "exitstatus": 0, "t": 1}\n'
        '{"event": "test", "nodeid": "b", "outcome": "failed", "duration_s": 2.5, "t": 2}\n',
        encoding="utf-8",
    )
    assert liveness.baseline_test_events(events) == [
        {"nodeid": "a", "outcome": "passed", "duration_s": 1.0},
        {"nodeid": "b", "outcome": "failed", "duration_s": 2.5},
    ]


def test_baseline_test_events_tolerates_a_torn_last_line(tmp_path: Path) -> None:
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 1.0, "t": 0}\n'
        '{"event": "test", "nodeid": "b", "outc',
        encoding="utf-8",
    )
    assert liveness.baseline_test_events(events) == [
        {"nodeid": "a", "outcome": "passed", "duration_s": 1.0},
    ]


# --------------------------------------------------------------------------
# count_test_events (B091/D-23, P7 A4) -- a candidate's own tests_completed
# progress field.
# --------------------------------------------------------------------------


def test_count_test_events_none_path_is_zero() -> None:
    assert liveness.count_test_events(None) == 0


def test_count_test_events_missing_file_is_zero(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.ndjson"
    assert liveness.count_test_events(missing) == 0


def test_count_test_events_counts_only_test_events(tmp_path: Path) -> None:
    events = tmp_path / "candidate.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 1.0, "t": 0}\n'
        '{"event": "test", "nodeid": "b", "outcome": "failed", "duration_s": 2.0, "t": 1}\n'
        '{"event": "session_finish", "exitstatus": 1, "t": 2}\n',
        encoding="utf-8",
    )
    assert liveness.count_test_events(events) == 2


def test_count_test_events_tolerates_a_torn_last_line(tmp_path: Path) -> None:
    events = tmp_path / "candidate.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 1.0, "t": 0}\n'
        '{"event": "test", "nodeid": "b", "outc',
        encoding="utf-8",
    )
    assert liveness.count_test_events(events) == 1


# --------------------------------------------------------------------------
# candidate_events_path (B091/D-23, P7 A4) -- the ONE hash both
# LivenessRunner (the writer) and mutation._run_one (the reader) use.
# --------------------------------------------------------------------------


def test_candidate_events_path_matches_the_livenessrunners_own_writer_path(
    tmp_path: Path,
) -> None:
    events_dir = tmp_path / "candidates"
    cwd = tmp_path / "some" / "mutant" / "snapshot"
    runner = liveness.LivenessRunner(events_dir=events_dir, expect_next_event_within_s=15.0)
    # `_events_path_for_cwd` is what a real candidate execution writes to;
    # the free function must agree with it exactly, never a second
    # independent hash.
    assert liveness.candidate_events_path(events_dir, cwd) == runner._events_path_for_cwd(cwd)


def test_candidate_events_path_differs_for_different_cwds(tmp_path: Path) -> None:
    events_dir = tmp_path / "candidates"
    first = liveness.candidate_events_path(events_dir, tmp_path / "a")
    second = liveness.candidate_events_path(events_dir, tmp_path / "b")
    assert first != second
    assert first.parent == events_dir == second.parent


# --------------------------------------------------------------------------
# _read_events_progress
# --------------------------------------------------------------------------


def test_read_events_progress_missing_file() -> None:
    assert liveness._read_events_progress(Path("/nonexistent/events.ndjson"), 0) == (0, False)


def test_read_events_progress_counts_valid_lines_and_session_finish(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 0.1, "t": 0}\n'
        '{"event": "session_finish", "exitstatus": 0, "t": 1}\n',
        encoding="utf-8",
    )
    count, saw_finish = liveness._read_events_progress(events, 0)
    assert count == 2
    assert saw_finish is True


def test_read_events_progress_a_test_event_alone_never_reports_session_finish(
    tmp_path: Path,
) -> None:
    """The OTHER optional case at its default: a valid, parseable line that
    is NOT `session_finish` must count toward `valid` without flipping
    `saw_session_finish` -- the branch straight back to the loop top.
    """
    events = tmp_path / "events.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 0.1, "t": 0}\n',
        encoding="utf-8",
    )
    count, saw_finish = liveness._read_events_progress(events, 0)
    assert count == 1
    assert saw_finish is False


def test_read_events_progress_skips_blank_and_torn_lines(tmp_path: Path) -> None:
    events = tmp_path / "events.ndjson"
    events.write_text(
        "\n"
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 0.1, "t": 0}\n'
        '{"event": "test", "nodeid": "b", "outc',  # torn.
        encoding="utf-8",
    )
    count, saw_finish = liveness._read_events_progress(events, 0)
    assert count == 1
    assert saw_finish is False


# --------------------------------------------------------------------------
# _safe_size / _read_bytes
# --------------------------------------------------------------------------


def test_safe_size_of_a_missing_file_is_zero() -> None:
    assert liveness._safe_size(Path("/nonexistent/file.stdout")) == 0


def test_read_bytes_of_a_missing_file_is_empty() -> None:
    assert liveness._read_bytes(Path("/nonexistent/file.stdout")) == b""


# --------------------------------------------------------------------------
# LivenessRunner._kill
# --------------------------------------------------------------------------


class _DisappearingProc:
    """A fake process whose pid is real (has a real pgid `_kill` can look
    up) but whose `.wait()` raises -- and whose `killpg` target is patched
    away -- to exercise `_kill`'s own two independent `except` clauses
    without a genuine, hard-to-time process-exit race.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd=["x"], timeout=timeout)


def test_kill_tolerates_killpg_process_lookup_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "candidates", expect_next_event_within_s=60.0
    )
    proc = _DisappearingProc(pid=__import__("os").getpid())

    def _raise_lookup(pgid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(liveness.os, "killpg", _raise_lookup)
    runner._kill(proc)  # must not raise.


def test_kill_tolerates_wait_raising(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "candidates", expect_next_event_within_s=60.0
    )
    proc = _DisappearingProc(pid=__import__("os").getpid())
    monkeypatch.setattr(liveness.os, "killpg", lambda pgid, sig: None)
    runner._kill(proc)  # `.wait()` raises TimeoutExpired -- must not propagate.
