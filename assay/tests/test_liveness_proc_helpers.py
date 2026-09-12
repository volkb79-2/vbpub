"""B091/RW-33, P7 A3 -- direct unit tests for :mod:`assay.liveness`'s
process-tree/`/proc` helpers and :meth:`~assay.liveness.LivenessRunner._kill`,
kept separate from `test_liveness_runner_monitor.py` (which drives the whole
loop end to end) so each helper's own edge cases -- a torn NDJSON line, an
unreadable file, the ppid-scan fallback, a process that vanished between
`_kill`'s two syscalls -- are exercised directly rather than only as a side
effect of some larger scenario.
"""

from __future__ import annotations

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
# compute_expect_next_event_within_s
# --------------------------------------------------------------------------


def test_compute_expect_next_event_within_s_none_path_uses_fallback() -> None:
    assert liveness.compute_expect_next_event_within_s(None, 40.0) == 60.0  # max(60, 10)
    assert liveness.compute_expect_next_event_within_s(None, 400.0) == 100.0  # max(60, 100)


def test_compute_expect_next_event_within_s_missing_file_uses_fallback(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "does-not-exist.ndjson"
    assert liveness.compute_expect_next_event_within_s(missing, 40.0) == 60.0


def test_compute_expect_next_event_within_s_reads_slowest_test(tmp_path: Path) -> None:
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 1.0, "t": 0}\n'
        '{"event": "test", "nodeid": "b", "outcome": "passed", "duration_s": 6.0, "t": 1}\n'
        '{"event": "test", "nodeid": "c", "outcome": "passed", "duration_s": 2.0, "t": 2}\n'
        '{"event": "session_finish", "exitstatus": 0, "t": 3}\n',
        encoding="utf-8",
    )
    # slowest_test_s = 6.0 -> max(3 * 6.0, 15.0) = 18.0
    assert liveness.compute_expect_next_event_within_s(events, 400.0) == 18.0


def test_compute_expect_next_event_within_s_tolerates_a_torn_last_line(
    tmp_path: Path,
) -> None:
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 9.0, "t": 0}\n'
        '{"event": "test", "nodeid": "b", "outc',  # torn -- the plugin was still writing.
        encoding="utf-8",
    )
    assert liveness.compute_expect_next_event_within_s(events, 400.0) == 27.0  # max(27, 15)


def test_compute_expect_next_event_within_s_ignores_non_test_and_malformed_duration(
    tmp_path: Path,
) -> None:
    events = tmp_path / "baseline.ndjson"
    events.write_text(
        "\n"  # blank line
        '{"event": "session_finish", "exitstatus": 0, "t": 0}\n'
        '{"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": "oops", "t": 1}\n'
        '{"event": "test", "nodeid": "b", "outcome": "passed", "duration_s": true, "t": 2}\n',
        encoding="utf-8",
    )
    # Neither "test" record has a usable numeric duration_s -> fallback.
    assert liveness.compute_expect_next_event_within_s(events, 40.0) == 60.0


# --------------------------------------------------------------------------
# baseline_slowest_test_s (B091/D-23, P7 A4) -- the same measurement
# compute_expect_next_event_within_s above already made internally, now
# extracted so a caller (the `plan` progress event) reads it back rather
# than re-deriving.
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
    # compute_expect_next_event_within_s must agree exactly -- it now
    # delegates to this same function, never a second parse.
    assert liveness.compute_expect_next_event_within_s(events, 400.0) == 18.0


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
