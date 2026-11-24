"""``cli_extended.procio``: bounded child-process supervision (design note Decision 2).

Every process check is by pid, never by name, and every test removes what it started. The
wall-clock bounds are generous (a regression hangs on a 600 s sleeper, so each waiting test
runs the code under test on a thread or in a driver process and fails after ``JOIN_LIMIT``).
"""
from __future__ import annotations

import ast
import os
import resource
import signal
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Literal, get_type_hints

import pytest

from cli_extended import procio
from conftest import direct_children  # relies on pytest's default (prepend) import mode

DRAIN = 0.5
JOIN_LIMIT = 60.0
# A time limit that a test needs the child to OUTLAST while it starts and installs its traps
# or writes its pid file. A starved host (cpu quota, load) can delay a child by seconds; a
# limit of a second fired before the shell had run its first line (load-independence rule).
START_LIMIT = 5.0
ORPHAN_LIMIT = 15.0  # a scripted shell's own ``sleep 1`` ends by itself well inside this
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
pytestmark = pytest.mark.requires_contained_cgroup


# ----------------------------------------------------------------------------- helpers


def _alive(pid: int) -> bool:
    """True while ``pid`` is a live process (a zombie awaiting its reaper counts as gone)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _wait_pidfile(path: Path) -> int:
    deadline = time.monotonic() + JOIN_LIMIT
    while time.monotonic() < deadline:
        text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        if text.isdigit():
            return int(text)
        time.sleep(0.05)
    raise AssertionError(f"{path} was never written")


def _wait_gone(*pids: int) -> None:
    deadline = time.monotonic() + JOIN_LIMIT
    while time.monotonic() < deadline and any(_alive(pid) for pid in pids):
        time.sleep(0.05)
    assert not any(_alive(pid) for pid in pids), f"still running: {[p for p in pids if _alive(p)]}"


def _kill_quietly(pid: int | None) -> None:
    """Kill and reap a process this test started (an orphaned grandchild may be reparented)."""
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass  # not our child (reaped by another owner already)


@pytest.fixture
def reap_orphans():
    """Reap the grandchildren a test's scripted shell leaves behind (package P11).

    assay makes the pytest process a child subreaper (``conftest.py`` does the same), so when a
    test kills a shell group, a ``sleep`` the shell had running is orphaned onto this process
    and stays a zombie nobody ``wait()``s for. The test cannot know its pid in advance, so after
    the test this waits (bounded) for every new child to end and reaps it. A child that is
    still alive at the deadline is left for the leftover-children guard to report and kill.
    """
    before = set(direct_children())
    yield
    deadline = time.monotonic() + ORPHAN_LIMIT
    while True:
        new = {key: state for key, (_, state) in direct_children().items() if key not in before}
        for (pid, _), state in new.items():
            if state == "Z":
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass  # reaped by somebody else in the meantime
        if not new or time.monotonic() >= deadline:
            return
        time.sleep(0.02)


def _stream(argv, tmp_path, *, watchdog=600.0, **kwargs):
    lines: list[str] = []
    result = procio.run_streaming(
        argv, cwd=tmp_path, env=None, on_line=lines.append, drain_seconds=kwargs.pop("drain", DRAIN),
        no_progress_seconds=watchdog, **kwargs,
    )
    return result, lines


def _capture(argv, tmp_path, **kwargs):
    kwargs.setdefault("drain_seconds", DRAIN)
    return procio.run_capture(argv, cwd=tmp_path, env=None, **kwargs)


def _in_thread(function, *args, **kwargs):
    """Run ``function`` on a thread so a hang fails the test instead of the suite."""
    outcome: dict = {}

    def target():
        try:
            outcome["result"] = function(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - the test inspects whatever came out
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def _holder_script(pidfile: Path, exit_code: int) -> str:
    return f"sleep 600 & echo $! > {pidfile}; echo hello; echo world; exit {exit_code}"


def _python_pipe_holder_script(pidfile: Path) -> str:
    sleep_script = "import time; time.sleep(600)"
    return (
        "import subprocess, sys\n"
        f"grandchild = subprocess.Popen([sys.executable, '-c', {sleep_script!r}])\n"
        f"with open({str(pidfile)!r}, 'w', encoding='utf-8') as stream:\n"
        "    stream.write(str(grandchild.pid))\n"
    )


@pytest.fixture
def default_signals():
    """Start from the default handlers (a runner may itself run under nohup); restore after."""
    watched = (*TERMINATION_SIGNALS, signal.SIGINT)
    before = {signum: signal.getsignal(signum) for signum in watched}
    for signum in TERMINATION_SIGNALS:
        signal.signal(signum, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    for signum, handler in before.items():
        signal.signal(signum, handler)


# ------------------------------------------------- 1. held pipe: bounded drain, exit code


def test_streaming_holder_does_not_hang_and_keeps_exit_code_and_names_the_holder(tmp_path):
    pidfile = tmp_path / "holder.pid"
    holder = None
    try:
        thread, outcome = _in_thread(
            _stream, ["bash", "-c", _holder_script(pidfile, 3)], tmp_path,
        )
        holder = _wait_pidfile(pidfile)
        thread.join(JOIN_LIMIT)
        assert not thread.is_alive(), "run_streaming is still waiting for pipe EOF"
        result, lines = outcome["result"]
        assert result.returncode == 3 and result.outcome == "exited"
        assert result.abandoned is True
        assert result.abandoned_holders == (holder,)
        assert result.warning == (
            f"'bash' exited with code 3 but its output pipe is still held open by pid(s) {holder}; "
            f"stopped reading after {DRAIN:g}s and kept the exit code."
        )
        assert lines == ["hello\n", "world\n"] and result.last_line == "world"
        assert result.watchdog is None
        assert _alive(holder), "the holder is named, never killed"
    finally:
        _kill_quietly(holder)


def test_capture_holder_does_not_hang_and_keeps_exit_code_and_names_the_holder(tmp_path):
    pidfile = tmp_path / "holder.pid"
    holder = None
    try:
        thread, outcome = _in_thread(_capture, ["bash", "-c", _holder_script(pidfile, 3)], tmp_path)
        holder = _wait_pidfile(pidfile)
        thread.join(JOIN_LIMIT)
        assert not thread.is_alive(), "run_capture is still waiting for pipe EOF"
        result = outcome["result"]
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 3 and result.stdout == "hello\nworld\n" and result.stderr == ""
        assert result.args == ["bash", "-c", _holder_script(pidfile, 3)]
        assert result.abandoned is True
        assert result.abandoned_holders == (holder,)
        assert f"pid(s) {holder}" in result.warning and "exited with code 3" in result.warning
        assert _alive(holder)
    finally:
        _kill_quietly(holder)


def test_holder_with_a_successful_child_still_returns_zero(tmp_path):
    pidfile = tmp_path / "holder.pid"
    holder = None
    try:
        thread, outcome = _in_thread(_stream, ["bash", "-c", _holder_script(pidfile, 0)], tmp_path)
        holder = _wait_pidfile(pidfile)
        thread.join(JOIN_LIMIT)
        assert not thread.is_alive()
        assert outcome["result"][0].returncode == 0 and outcome["result"][0].warning
    finally:
        _kill_quietly(holder)


@pytest.mark.parametrize("function", ["stream", "capture"])
def test_holder_scan_failure_still_warns_with_no_pids(tmp_path, monkeypatch, function):
    def broken_scan(_inode):
        raise OSError("injected /proc failure")

    monkeypatch.setattr(procio, "pipe_holder_pids", broken_scan)
    pidfile = tmp_path / "holder.pid"
    holder = None
    try:
        call = _stream if function == "stream" else _capture
        thread, outcome = _in_thread(call, ["bash", "-c", _holder_script(pidfile, 0)], tmp_path)
        holder = _wait_pidfile(pidfile)
        thread.join(JOIN_LIMIT)
        assert not thread.is_alive()
        result = outcome["result"][0] if function == "stream" else outcome["result"]
        assert result.abandoned is True
        assert result.abandoned_holders is None
        assert "still held open by another process" in result.warning
        assert str(holder) not in result.warning
    finally:
        _kill_quietly(holder)


def test_holder_the_scan_cannot_see_is_reported_as_another_process(tmp_path, monkeypatch):
    monkeypatch.setattr(procio, "pipe_holder_pids", lambda _inode: [])
    pidfile = tmp_path / "holder.pid"
    holder = None
    try:
        thread, outcome = _in_thread(_stream, ["bash", "-c", _holder_script(pidfile, 0)], tmp_path)
        holder = _wait_pidfile(pidfile)
        thread.join(JOIN_LIMIT)
        result = outcome["result"][0]
        assert result.abandoned is True
        assert result.abandoned_holders == () and "another process" in result.warning
    finally:
        _kill_quietly(holder)


@pytest.mark.estate_allow(
    "wall-time-assert",
    reason="The drain deadline itself is the behavior under test.",
    backlog="CLI-EXT-47",
)
def test_drain_ends_the_wait_at_drain_seconds_not_before_and_not_at_eof(tmp_path, reap_orphans):
    """A holder that keeps writing is cut off at the drain deadline; earlier output is kept."""
    pidfile = tmp_path / "holder.pid"
    script = (
        f"echo first; (while :; do echo tick; sleep 0.05; done) & echo $! > {pidfile}; exit 0"
    )
    holder = None
    try:
        started = time.monotonic()
        thread, outcome = _in_thread(_stream, ["bash", "-c", script], tmp_path, drain=1.5)
        holder = _wait_pidfile(pidfile)
        thread.join(JOIN_LIMIT)
        assert not thread.is_alive()
        result, lines = outcome["result"]
        assert time.monotonic() - started >= 1.4, "stopped before the drain period ran out"
        assert lines[0] == "first\n" and result.returncode == 0 and result.abandoned
    finally:
        _kill_quietly(holder)


def test_ordinary_output_order_exit_code_and_clean_result(tmp_path):
    script = r"printf 'a\r\nb\rc\nd\n'; echo to-stderr >&2; printf 'last-without-newline'; exit 4"
    result, lines = _stream(["bash", "-c", script], tmp_path)
    assert lines == ["a\n", "b\n", "c\n", "d\n", "to-stderr\n", "last-without-newline"]
    assert result.returncode == 4 and result.outcome == "exited"
    assert result.abandoned is False
    assert result.abandoned_holders == () and result.warning is None and result.watchdog is None
    assert result.last_line == "last-without-newline"


@pytest.mark.parametrize("function", ["stream", "capture"])
def test_abandoned_is_true_for_a_python_grandchild_that_holds_the_pipe(tmp_path, function):
    pidfile = tmp_path / "holder.pid"
    holder = None
    try:
        argv = [sys.executable, "-c", _python_pipe_holder_script(pidfile)]
        call = _stream if function == "stream" else _capture
        thread, outcome = _in_thread(call, argv, tmp_path)
        thread.join(JOIN_LIMIT)
        assert not thread.is_alive(), f"run_{function} did not stop at the drain deadline"
        assert "error" not in outcome, outcome.get("error")
        result = outcome["result"][0] if function == "stream" else outcome["result"]
        holder = _wait_pidfile(pidfile)
        assert result.abandoned is True
        assert result.warning is not None
        assert result.abandoned_holders is None or holder in result.abandoned_holders
    finally:
        _kill_quietly(holder)


@pytest.mark.parametrize("function", ["stream", "capture"])
def test_abandoned_is_false_for_a_python_child_that_exits_cleanly(tmp_path, function):
    argv = [sys.executable, "-c", "print('done')"]
    if function == "stream":
        result, lines = _stream(argv, tmp_path)
        assert lines == ["done\n"]
    else:
        result = _capture(argv, tmp_path)
        assert result.stdout == "done\n"
    assert result.returncode == 0
    assert result.abandoned is False
    assert result.abandoned_holders == () and result.warning is None


def test_last_line_is_the_last_non_blank_line(tmp_path):
    result, lines = _stream(["bash", "-c", r"printf 'one\n\ntwo\r\n\n   \n'"], tmp_path)
    assert result.last_line == "two" and lines[-1] == "   \n"


def test_multibyte_output_split_across_reads_is_decoded_like_a_text_pipe(tmp_path):
    script = (
        "import sys, time\n"
        "data = ('\\u00e4\\u20ac\\U0001f600' * 3 + '\\n').encode('utf-8')\n"
        "for index in range(len(data)):\n"
        "    sys.stdout.buffer.write(data[index:index + 1]); sys.stdout.buffer.flush()\n"
        "    time.sleep(0.002)\n"
    )
    expected = "\u00e4\u20ac\U0001f600" * 3 + "\n"
    result, lines = _stream([sys.executable, "-c", script], tmp_path)
    assert lines == [expected] and result.returncode == 0
    assert _capture([sys.executable, "-c", script], tmp_path).stdout == expected


UNDECODABLE = "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end\\n\\xe2\\x82'); sys.stdout.flush()"


def test_streaming_lines_replace_undecodable_bytes_and_never_fail_a_finished_child(tmp_path):
    result, lines = _stream([sys.executable, "-c", UNDECODABLE], tmp_path)
    assert lines == ["ok \ufffd\ufffd end\n", "\ufffd"] and result.returncode == 0


def test_capture_text_is_strict_like_subprocess_run_and_bytes_mode_keeps_the_bytes(tmp_path):
    with pytest.raises(UnicodeDecodeError):
        _capture([sys.executable, "-c", UNDECODABLE], tmp_path)
    raw = _capture([sys.executable, "-c", UNDECODABLE], tmp_path, text=False)
    assert raw.stdout == b"ok \xff\xfe end\n\xe2\x82" and raw.returncode == 0


@pytest.mark.estate_allow(
    "wall-time-assert",
    reason="The bounded completion time is the regression oracle for quadratic copying.",
    backlog="CLI-EXT-47",
)
def test_one_huge_unterminated_line_is_assembled_without_quadratic_copying(tmp_path):
    script = (
        "import sys\n"
        "for _ in range(20):\n"
        "    sys.stdout.write('x' * 1_000_000); sys.stdout.flush()\n"
    )
    started = time.monotonic()
    result, lines = _stream([sys.executable, "-c", script], tmp_path)
    assert len(lines) == 1 and lines[0] == "x" * 20_000_000 and result.returncode == 0
    assert result.last_line == "x" * 20_000_000
    assert time.monotonic() - started < JOIN_LIMIT


def test_a_command_that_cannot_start_raises_the_plain_popen_error(tmp_path):
    missing = [str(tmp_path / "no-such-program")]
    with pytest.raises(FileNotFoundError):
        _stream(missing, tmp_path)
    with pytest.raises(FileNotFoundError):
        _capture(missing, tmp_path)


def test_on_line_exception_reaps_the_whole_group_before_propagating(tmp_path):
    shell_pidfile, holder_pidfile = tmp_path / "shell.pid", tmp_path / "holder.pid"
    script = f"echo $$ > {shell_pidfile}; sleep 600 & echo $! > {holder_pidfile}; echo ready; wait"

    def on_line(_line):
        raise RuntimeError("callback failed")

    shell = holder = None
    try:
        with pytest.raises(RuntimeError, match="callback failed"):
            procio.run_streaming(
                ["bash", "-c", script], cwd=tmp_path, env=None, on_line=on_line,
                no_progress_seconds=600,
            )
        shell, holder = _wait_pidfile(shell_pidfile), _wait_pidfile(holder_pidfile)
        _wait_gone(shell, holder)
    finally:
        _kill_quietly(shell)
        _kill_quietly(holder)


def test_child_starts_in_its_own_session_and_process_group(tmp_path):
    script = (
        "import os; print(os.getpgrp() == os.getpid(), os.getsid(0) == os.getpid(), "
        f"os.getsid(0) != {os.getsid(0)})"
    )
    assert _stream([sys.executable, "-c", script], tmp_path)[1] == ["True True True\n"]
    assert _capture([sys.executable, "-c", script], tmp_path).stdout == "True True True\n"


def test_pipe_closing_while_the_child_lives_waits_for_the_exit(tmp_path):
    """EOF is not the end: the child closes its output and runs on, and its exit code counts."""
    script = "exec >&- 2>&-; sleep 1; exit 5"
    result, lines = _stream(["bash", "-c", script], tmp_path)
    assert lines == [] and result.returncode == 5 and result.outcome == "exited"


# ---------------------------------------------------------------- 2. no-progress watchdog


def test_watchdog_ends_a_silent_live_child_with_the_tree_taken_before_the_kill(tmp_path):
    shell_pidfile, child_pidfile = tmp_path / "shell.pid", tmp_path / "child.pid"
    outside_pidfile, escaped_pidfile = tmp_path / "outside.pid", tmp_path / "escaped.pid"
    outside = subprocess.Popen(["sleep", "600"])  # same session as the test: outside the group
    script = (
        f"echo $$ > {shell_pidfile}; sleep 600 & echo $! > {child_pidfile}; "
        f"setsid sleep 600 & echo $! > {escaped_pidfile}; echo started; wait"
    )
    shell = child = escaped = None
    try:
        # The limit only has to outlast the child's setup (milliseconds): 5 s, never a close call.
        result, lines = _stream(["bash", "-c", script], tmp_path, watchdog=5.0, term_grace_seconds=5.0)
        shell, child = _wait_pidfile(shell_pidfile), _wait_pidfile(child_pidfile)
        escaped = _wait_pidfile(escaped_pidfile)
        assert result.outcome == "watchdog" and result.returncode < 0
        assert lines == ["started\n"] and result.last_line == "started"
        report = result.watchdog
        assert report.last_line == "started" and report.limit_seconds == 5.0
        assert report.render("no_progress_timeout_seconds") == (
            "'bash' produced no output for 5s (no_progress_timeout_seconds = 5); "
            "last output line: started"
        )
        assert "(no_progress_seconds = 5)" in report.render()
        tree = {entry.pid: entry for entry in report.process_tree}
        assert {shell, child, escaped} <= set(tree), "the tree is taken before the group is signalled"
        assert tree[shell].ppid == os.getpid() and tree[child].ppid == shell
        assert tree[shell].argv.startswith("bash -c") and tree[child].argv == "sleep 600"
        assert report.tree_lines[0] == f"{shell} {os.getpid()} S {tree[shell].argv}"
        assert report.escaped_pids == (escaped,)
        _wait_gone(shell, child)
        assert _alive(escaped), "a process that escaped the group is named, never killed"
        assert _alive(outside.pid), "a process outside the group is untouched"
    finally:
        outside.kill()
        outside.wait()
        for pid in (shell, child, escaped):
            _kill_quietly(pid)


def test_watchdog_escalates_to_sigkill_for_a_child_that_ignores_sigterm(tmp_path):
    shell_pidfile = tmp_path / "shell.pid"
    script = f"trap '' TERM; echo $$ > {shell_pidfile}; echo ready; while :; do :; done"
    shell = None
    try:
        result, _ = _stream(
            ["bash", "-c", script], tmp_path, watchdog=START_LIMIT, term_grace_seconds=0.3,
        )
        shell = _wait_pidfile(shell_pidfile)
        assert result.outcome == "watchdog" and result.returncode == -signal.SIGKILL
        _wait_gone(shell)
    finally:
        _kill_quietly(shell)


def test_watchdog_with_no_output_at_all_reports_no_last_line(tmp_path):
    result, _ = _stream(["sleep", "600"], tmp_path, watchdog=1.0)
    assert result.outcome == "watchdog" and result.last_line is None
    assert result.watchdog.last_line is None
    assert result.watchdog.render().endswith("last output line: <none>")


def test_watchdog_fires_after_the_child_closed_its_output(tmp_path):
    result, _ = _stream(["bash", "-c", "exec >&- 2>&-; sleep 600"], tmp_path, watchdog=1.0)
    assert result.outcome == "watchdog"


def test_output_bytes_count_as_progress_and_reset_the_clock(tmp_path):
    script = "for i in $(seq 12); do echo line$i; sleep 1; done"  # ~12 s > the 10 s limit
    result, lines = _stream(["bash", "-c", script], tmp_path, watchdog=10.0)
    assert result.outcome == "exited" and result.returncode == 0 and len(lines) == 12


def test_a_progress_path_size_change_counts_as_progress(tmp_path, reap_orphans):
    log = tmp_path / "step.log"
    script = f"for i in $(seq 12); do echo more >> {log}; sleep 1; done"  # ~12 s > the 10 s limit
    kept, _ = _stream(["bash", "-c", script], tmp_path, watchdog=10.0, progress_paths=[log])
    assert kept.outcome == "exited" and kept.returncode == 0
    log.unlink()
    silent, _ = _stream(["bash", "-c", script], tmp_path, watchdog=1.0)
    assert silent.outcome == "watchdog", "without progress_paths the same child is silent"


def test_a_progress_path_mtime_change_counts_as_progress(tmp_path):
    log = tmp_path / "step.log"
    log.write_text("fixed", encoding="utf-8")
    script = f"for i in $(seq 12); do touch -d @$((1000 + i)) {log}; sleep 1; done"
    result, _ = _stream(["bash", "-c", script], tmp_path, watchdog=10.0, progress_paths=(str(log),))
    assert result.outcome == "exited" and log.read_text(encoding="utf-8") == "fixed"


def test_a_progress_path_that_never_changes_does_not_hold_the_watchdog_off(tmp_path):
    log = tmp_path / "step.log"
    log.write_text("static", encoding="utf-8")
    result, _ = _stream(["sleep", "600"], tmp_path, watchdog=1.0, progress_paths=[log])
    assert result.outcome == "watchdog"


def test_a_missing_progress_path_appearing_counts_as_progress(tmp_path):
    log = tmp_path / "later.log"
    # 12 s in total is silent for longer than the 10 s limit unless the file appearing at 6 s resets it
    script = f"sleep 6; : > {log}; sleep 6"
    result, _ = _stream(["bash", "-c", script], tmp_path, watchdog=10.0, progress_paths=[log])
    assert result.outcome == "exited" and result.returncode == 0


@pytest.mark.parametrize("bad", [None, 0, -1, 0.0, True, False, float("nan"), float("inf"), "5"])
def test_there_is_no_off_value_for_the_watchdog(tmp_path, monkeypatch, bad):
    def never_spawn(*_args, **_kwargs):
        raise AssertionError("must refuse before spawning")

    monkeypatch.setattr(procio.subprocess, "Popen", never_spawn)
    with pytest.raises(ValueError, match="no_progress_seconds must be a positive finite number"):
        procio.run_streaming(
            ["true"], cwd=tmp_path, env=None, on_line=print, no_progress_seconds=bad,
        )


def test_the_watchdog_value_is_required_and_has_no_library_default(tmp_path):
    with pytest.raises(TypeError, match="no_progress_seconds"):
        procio.run_streaming(["true"], cwd=tmp_path, env=None, on_line=print)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("name", "value"),
    [("drain_seconds", -1), ("drain_seconds", None), ("term_grace_seconds", -0.5),
     ("term_grace_seconds", float("nan"))],
)
def test_other_durations_are_validated(tmp_path, name, value):
    with pytest.raises(ValueError, match=f"{name} must be a non-negative finite number"):
        procio.run_streaming(
            ["true"], cwd=tmp_path, env=None, on_line=print, no_progress_seconds=5, **{name: value},
        )


def test_capture_durations_are_validated(tmp_path):
    with pytest.raises(ValueError, match="drain_seconds must be a non-negative"):
        procio.run_capture(["true"], cwd=tmp_path, env=None, drain_seconds=-1)
    with pytest.raises(ValueError, match="timeout must be a positive finite number"):
        procio.run_capture(["true"], cwd=tmp_path, env=None, timeout=0)


@pytest.mark.parametrize("paths", ["a.log", b"a.log", Path("a.log")])
def test_progress_paths_must_be_a_sequence_not_one_path(tmp_path, paths):
    with pytest.raises(TypeError, match="progress_paths must be a sequence"):
        procio.run_streaming(
            ["true"], cwd=tmp_path, env=None, on_line=print, no_progress_seconds=5,
            progress_paths=paths,
        )


def test_the_outcome_vocabulary_is_closed():
    assert procio.OUTCOMES == ("exited", "watchdog")
    assert procio.Outcome == Literal["exited", "watchdog"]
    assert get_type_hints(procio.StreamResult)["outcome"] == procio.Outcome
    assert "Outcome" in procio.__all__


# ------------------------------------------------- 4. signals reach the whole group


DRIVER = """\
import signal
import sys

sys.path.insert(0, sys.argv[1])
from cli_extended import procio

function, script, cwd = sys.argv[2], sys.argv[3], sys.argv[4]
signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
signal.signal(signal.SIGHUP, signal.SIG_IGN if "ignore-hup" in sys.argv[5:] else signal.SIG_DFL)
argv = ["bash", "-c", script]
if function == "stream":
    procio.run_streaming(argv, cwd=cwd, env=None, on_line=lambda line: None, no_progress_seconds=600)
else:
    procio.run_capture(argv, cwd=cwd, env=None)
"""


def _start_driver(tmp_path: Path, function: str, script: str, *flags: str) -> subprocess.Popen:
    driver_file = tmp_path / "driver.py"
    driver_file.write_text(DRIVER, encoding="utf-8")
    source_root = str(Path(procio.__file__).resolve().parent.parent)
    argv = [sys.executable, str(driver_file), source_root, function, script, str(tmp_path), *flags]
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.mark.parametrize("function", ["stream", "capture"])
@pytest.mark.parametrize(
    ("signum", "flags", "expected"),
    [
        (signal.SIGTERM, (), 128 + signal.SIGTERM),
        (signal.SIGHUP, (), 128 + signal.SIGHUP),
        (signal.SIGTERM, ("ignore-hup",), 128 + signal.SIGTERM),
        (signal.SIGINT, (), -signal.SIGINT),
    ],
    ids=["SIGTERM", "SIGHUP", "SIGTERM-with-SIGHUP-ignored", "SIGINT"],
)
def test_signal_to_the_caller_reaps_the_whole_group(tmp_path, function, signum, flags, expected):
    pidfile, shell_pidfile = tmp_path / "holder.pid", tmp_path / "shell.pid"
    script = f"echo $$ > {shell_pidfile}; sleep 600 & echo $! > {pidfile}; wait"
    driver = _start_driver(tmp_path, function, script, *flags)
    holder = shell = None
    try:
        holder, shell = _wait_pidfile(pidfile), _wait_pidfile(shell_pidfile)
        os.kill(driver.pid, signum)
        assert driver.wait(timeout=JOIN_LIMIT) == expected
        _wait_gone(holder, shell)
    finally:
        if driver.poll() is None:
            driver.kill()
            driver.wait()
        _kill_quietly(holder)
        _kill_quietly(shell)


@pytest.mark.parametrize("function", ["stream", "capture"])
def test_sighup_ignored_at_entry_stays_ignored_for_the_caller_and_the_child(tmp_path, function):
    """``nohup``: SIGHUP is SIG_IGN when the caller starts, and the step must finish anyway."""
    shell_pidfile = tmp_path / "shell.pid"
    script = f"echo $$ > {shell_pidfile}; sleep 2; echo finished > {tmp_path / 'done'}"
    driver = _start_driver(tmp_path, function, script, "ignore-hup")
    shell = None
    try:
        shell = _wait_pidfile(shell_pidfile)
        os.kill(driver.pid, signal.SIGHUP)
        assert driver.wait(timeout=JOIN_LIMIT) == 0
        assert (tmp_path / "done").read_text(encoding="utf-8") == "finished\n"
    finally:
        if driver.poll() is None:
            driver.kill()
            driver.wait()
        _kill_quietly(shell)


@pytest.mark.parametrize("function", ["stream", "capture"])
def test_child_inherits_an_ignored_sighup_and_the_ignore_is_kept(tmp_path, default_signals, function):
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    script = "import signal; print(signal.getsignal(signal.SIGHUP) == signal.SIG_IGN)"
    argv = [sys.executable, "-c", script]
    if function == "stream":
        assert _stream(argv, tmp_path)[1] == ["True\n"]
    else:
        assert _capture(argv, tmp_path).stdout == "True\n"
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN


def _handlers() -> dict[int, object]:
    return {s: signal.getsignal(s) for s in (*TERMINATION_SIGNALS, signal.SIGINT)}


@pytest.mark.estate_allow(
    "short-timeout",
    reason="The short subprocess timeout is the signal and cleanup behavior under test.",
    backlog="CLI-EXT-47",
)
def test_handlers_are_installed_while_running_and_restored_afterwards(tmp_path, default_signals):
    before = _handlers()
    seen: dict[int, object] = {}
    procio.run_streaming(
        ["echo", "hi"], cwd=tmp_path, env=None, on_line=lambda _l: seen.update(_handlers()),
        no_progress_seconds=600,
    )
    assert seen and all(callable(seen[s]) and seen[s] is not before[s] for s in seen)
    assert _handlers() == before
    with pytest.raises(subprocess.TimeoutExpired):
        procio.run_capture(["sleep", "600"], cwd=tmp_path, env=None, timeout=0.3)
    assert _handlers() == before


def test_a_caller_owned_sigint_handler_is_left_alone(tmp_path, default_signals):
    def mine(_signum, _frame):
        raise AssertionError("a caller-owned SIGINT handler must not run in this test")

    signal.signal(signal.SIGINT, mine)
    seen: dict[int, object] = {}
    procio.run_streaming(
        ["echo", "hi"], cwd=tmp_path, env=None, on_line=lambda _l: seen.update(_handlers()),
        no_progress_seconds=600,
    )
    assert seen[signal.SIGINT] is mine and callable(seen[signal.SIGTERM])
    assert signal.getsignal(signal.SIGINT) is mine


def test_nothing_is_installed_off_the_main_thread_and_the_call_still_works(tmp_path):
    before = _handlers()
    seen: dict[int, object] = {}
    thread, outcome = _in_thread(
        procio.run_streaming, ["echo", "hi"], cwd=tmp_path, env=None,
        on_line=lambda _l: seen.update(_handlers()), no_progress_seconds=600,
    )
    thread.join(JOIN_LIMIT)
    assert not thread.is_alive() and "error" not in outcome
    assert seen == before and _handlers() == before


def test_terminate_as_exit_installs_and_restores_and_tolerates_other_threads(default_signals):
    before = _handlers()
    with procio.terminate_as_exit():
        installed = _handlers()
        assert callable(installed[signal.SIGTERM]) and callable(installed[signal.SIGHUP])
        assert installed[signal.SIGINT] == before[signal.SIGINT], "only TERM and HUP"
        with pytest.raises(SystemExit) as exited:
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(JOIN_LIMIT)  # the handler raises long before this returns
        assert exited.value.code == 128 + signal.SIGTERM
    assert _handlers() == before

    inner: dict = {}

    def other_thread():
        with procio.terminate_as_exit():
            inner["during"] = _handlers()

    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join()
    assert inner["during"] == before


def test_a_failed_second_install_restores_the_first(tmp_path, monkeypatch, default_signals):
    real_signal = signal.signal
    calls: list[int] = []

    def flaky(signum, handler):
        calls.append(signum)
        if len(calls) == 2:
            raise OSError("injected install failure")
        return real_signal(signum, handler)

    monkeypatch.setattr(signal, "signal", flaky)
    with pytest.raises(OSError, match="injected install failure"):
        with procio.terminate_as_exit():
            pytest.fail("the body must not run after a failed install")
    monkeypatch.undo()
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_DFL


def test_a_previous_handler_that_python_cannot_name_is_restored_as_default(default_signals, monkeypatch):
    real_signal = signal.signal
    monkeypatch.setattr(signal, "signal", lambda s, h: None if h is not signal.SIG_DFL else real_signal(s, h))
    with procio.terminate_as_exit():
        pass
    monkeypatch.undo()
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL


def _signalling_popen(monkeypatch, signum, *, fail=False):
    """Make ``Popen`` deliver ``signum`` to this process while it is still spawning."""
    real_popen = subprocess.Popen
    started: list[int] = []

    def factory(*args, **kwargs):
        if fail:
            os.kill(os.getpid(), signum)
            raise FileNotFoundError("injected spawn failure")
        process = real_popen(*args, **kwargs)
        started.append(process.pid)
        os.kill(os.getpid(), signum)  # lands between the fork and Popen returning
        return process

    monkeypatch.setattr(procio.subprocess, "Popen", factory)
    return started


@pytest.mark.parametrize("function", ["stream", "capture"])
@pytest.mark.parametrize(
    ("signum", "error"),
    [(signal.SIGTERM, SystemExit), (signal.SIGHUP, SystemExit), (signal.SIGINT, KeyboardInterrupt)],
)
def test_a_signal_during_the_spawn_still_reaps_the_new_child(
    tmp_path, monkeypatch, default_signals, function, signum, error,
):
    started = _signalling_popen(monkeypatch, signum)
    try:
        with pytest.raises(error) as raised:
            if function == "stream":
                _stream(["sleep", "600"], tmp_path)
            else:
                _capture(["sleep", "600"], tmp_path)
        if error is SystemExit:
            assert raised.value.code == 128 + signum
        assert started and not _alive(started[0]), "the child was left running after the signal"
    finally:
        for pid in started:
            _kill_quietly(pid)


def test_a_signal_during_a_failing_spawn_wins_over_the_spawn_error(tmp_path, monkeypatch, default_signals):
    _signalling_popen(monkeypatch, signal.SIGTERM, fail=True)
    with pytest.raises(SystemExit) as raised:
        _stream(["sleep", "600"], tmp_path)
    assert isinstance(raised.value.__cause__, FileNotFoundError)


def test_a_second_signal_during_the_spawn_keeps_the_first(tmp_path, monkeypatch, default_signals):
    real_popen = subprocess.Popen
    started: list[int] = []

    def factory(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started.append(process.pid)
        os.kill(os.getpid(), signal.SIGHUP)
        os.kill(os.getpid(), signal.SIGTERM)
        return process

    monkeypatch.setattr(procio.subprocess, "Popen", factory)
    try:
        with pytest.raises(SystemExit) as raised:
            _stream(["sleep", "600"], tmp_path)
        assert raised.value.code == 128 + signal.SIGHUP
    finally:
        for pid in started:
            _kill_quietly(pid)


# --------------------------------------------------- 5. descriptors above the select limit


def test_descriptors_above_the_select_limit_still_stream_and_capture(tmp_path):
    """``select.select`` rejects descriptors >= 1024; a busy process can easily have them."""
    wanted = 1300
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    held: list[int] = []
    raised = False
    try:
        if soft != resource.RLIM_INFINITY and soft < wanted + 64:
            assert hard == resource.RLIM_INFINITY or hard >= wanted + 64, "RLIMIT_NOFILE hard limit"
            resource.setrlimit(resource.RLIMIT_NOFILE, (wanted + 64, hard))
            raised = True
        while len(held) < 1100:
            held.append(os.open(os.devnull, os.O_RDONLY))
        assert held[-1] >= 1024
        result, lines = _stream(["bash", "-c", "echo hi; echo there; exit 3"], tmp_path)
        assert result.returncode == 3 and lines == ["hi\n", "there\n"]
        captured = _capture(["bash", "-c", "echo hi; echo err >&2; exit 3"], tmp_path, input="x")
        assert captured.returncode == 3 and captured.stdout == "hi\n" and captured.stderr == "err\n"
    finally:
        for descriptor in held:
            os.close(descriptor)
        if raised:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_the_module_never_uses_select_select():
    assert "select.select" not in Path(procio.__file__).read_text(encoding="utf-8")


# ------------------------------------------------------------------------ 6. run_capture


def test_capture_both_pipes_without_deadlock_when_one_is_filled_and_the_other_idle(tmp_path):
    script = (
        "import sys\n"
        "sys.stdout.write('o' * 500000); sys.stdout.flush()\n"
        "sys.stderr.write('e' * 500000); sys.stderr.flush()\n"
        "sys.stdout.write('end')\n"
    )
    thread, outcome = _in_thread(_capture, [sys.executable, "-c", script], tmp_path)
    thread.join(JOIN_LIMIT)
    assert not thread.is_alive(), "run_capture deadlocked on a full pipe"
    result = outcome["result"]
    assert result.stdout == "o" * 500000 + "end" and result.stderr == "e" * 500000
    assert result.abandoned_holders == () and result.warning is None


def test_capture_returns_bytes_when_text_is_false(tmp_path):
    script = "import sys; sys.stdout.buffer.write(b'\\xff\\x00ab\\r\\n'); sys.stderr.buffer.write(b'\\xfe')"
    result = _capture([sys.executable, "-c", script], tmp_path, text=False)
    assert result.stdout == b"\xff\x00ab\r\n" and result.stderr == b"\xfe"
    assert isinstance(result.stdout, bytes) and result.returncode == 0


def test_capture_stdin_devnull_gives_the_child_immediate_eof(tmp_path):
    read_fd, write_fd = os.pipe()
    saved_stdin = os.dup(0)
    try:
        # An inherited ambient stdin is often already EOF in CI. Hold a pipe open on fd 0
        # so only an explicit DEVNULL can make the child read immediately.
        os.dup2(read_fd, 0)
        result = _capture(
            [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
            tmp_path,
            stdin=subprocess.DEVNULL,
            timeout=JOIN_LIMIT,
        )
    finally:
        os.dup2(saved_stdin, 0)
        for descriptor in (saved_stdin, read_fd, write_fd):
            os.close(descriptor)
    assert result.returncode == 0
    assert result.stdout == "''\n"


def test_capture_rejects_input_with_devnull_stdin_before_spawning(tmp_path):
    marker = tmp_path / "spawned"
    script = f"open({str(marker)!r}, 'w', encoding='utf-8').write('spawned')"
    with pytest.raises(ValueError, match="input cannot be combined"):
        _capture(
            [sys.executable, "-c", script],
            tmp_path,
            input="ignored",
            stdin=subprocess.DEVNULL,
        )
    assert not marker.exists()


@pytest.mark.parametrize("stdin", [subprocess.PIPE, subprocess.STDOUT, "pipe"])
def test_capture_rejects_any_other_stdin_value_before_spawning(tmp_path, stdin):
    with pytest.raises(ValueError, match="stdin must be None or subprocess.DEVNULL"):
        _capture([sys.executable, "-c", "raise SystemExit('spawned')"], tmp_path, stdin=stdin)


def test_capture_text_uses_universal_newlines(tmp_path):
    result = _capture(["bash", "-c", r"printf 'a\r\nb\rc\n'"], tmp_path)
    assert result.stdout == "a\nb\nc\n"


@pytest.mark.parametrize("text", [True, False])
def test_capture_feeds_large_input_without_deadlock_and_closes_stdin(tmp_path, text):
    payload = "line\n" * 300000 if text else b"line\n" * 300000
    thread, outcome = _in_thread(_capture, ["cat"], tmp_path, input=payload, text=text)
    thread.join(JOIN_LIMIT)
    assert not thread.is_alive(), "run_capture deadlocked feeding stdin"
    assert outcome["result"].stdout == payload


def test_capture_with_empty_input_closes_stdin_at_once(tmp_path):
    assert _capture(["cat"], tmp_path, input="").stdout == ""


def test_capture_retries_a_write_that_would_block(tmp_path, monkeypatch):
    real_write = os.write
    refused: list[int] = []

    def write(fd, data):
        if not refused:
            refused.append(fd)
            raise BlockingIOError
        return real_write(fd, data)

    monkeypatch.setattr(procio.os, "write", write)
    assert _capture(["cat"], tmp_path, input="abc").stdout == "abc" and refused


def test_capture_ignores_a_child_that_stops_reading_its_input(tmp_path):
    script = "exec 0<&-; echo closed; sleep 1"
    result = _capture(["bash", "-c", script], tmp_path, input="x" * 2_000_000)
    assert result.returncode == 0 and result.stdout == "closed\n"


@pytest.mark.parametrize(("text", "bad"), [(True, b"bytes"), (False, "text")])
def test_capture_rejects_input_of_the_wrong_type(tmp_path, text, bad):
    with pytest.raises(TypeError, match="input must be"):
        _capture(["cat"], tmp_path, input=bad, text=text)


def test_capture_accepts_any_bytes_like_input_in_bytes_mode(tmp_path):
    assert _capture(["cat"], tmp_path, input=bytearray(b"abc"), text=False).stdout == b"abc"


def test_capture_timeout_reaps_the_group_and_raises_timeout_expired(tmp_path):
    shell_pidfile, holder_pidfile = tmp_path / "shell.pid", tmp_path / "holder.pid"
    script = (
        f"echo $$ > {shell_pidfile}; sleep 600 & echo $! > {holder_pidfile}; "
        "echo partial; echo oops >&2; wait"
    )
    shell = holder = None
    try:
        with pytest.raises(subprocess.TimeoutExpired) as raised:
            _capture(["bash", "-c", script], tmp_path, timeout=START_LIMIT)
        shell, holder = _wait_pidfile(shell_pidfile), _wait_pidfile(holder_pidfile)
        _wait_gone(shell, holder)
        assert raised.value.timeout == START_LIMIT and raised.value.cmd == ["bash", "-c", script]
        assert raised.value.stdout == b"partial\n" and raised.value.stderr == b"oops\n"
    finally:
        _kill_quietly(shell)
        _kill_quietly(holder)


@pytest.mark.estate_allow(
    "wall-time-assert",
    reason="The elapsed upper bound distinguishes timeout cleanup from waiting for the child.",
    backlog="CLI-EXT-47",
)
def test_capture_timeout_ends_the_call_at_the_timeout_not_when_the_child_ends(tmp_path):
    """A child with a bounded lifetime (25 s): reaping makes the call return long before that."""
    shell_pidfile, holder_pidfile = tmp_path / "shell.pid", tmp_path / "holder.pid"
    script = f"echo $$ > {shell_pidfile}; sleep 25 & echo $! > {holder_pidfile}; wait"
    shell = holder = None
    try:
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            _capture(["bash", "-c", script], tmp_path, timeout=START_LIMIT)
        elapsed = time.monotonic() - started
        shell, holder = _wait_pidfile(shell_pidfile), _wait_pidfile(holder_pidfile)
        assert elapsed < 15.0, f"the call waited for the child instead of reaping it ({elapsed:.1f}s)"
        assert not _alive(shell) and not _alive(holder), "the group was still running on return"
    finally:
        _kill_quietly(shell)
        _kill_quietly(holder)


def test_capture_timeout_escalates_to_sigkill_for_a_child_that_ignores_sigterm(tmp_path, monkeypatch):
    monkeypatch.setattr(procio, "DEFAULT_TERM_GRACE_SECONDS", 0.3)
    shell_pidfile = tmp_path / "shell.pid"
    script = f"trap '' TERM; echo $$ > {shell_pidfile}; while :; do :; done"
    shell = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _capture(["bash", "-c", script], tmp_path, timeout=START_LIMIT)
        shell = _wait_pidfile(shell_pidfile)
        _wait_gone(shell)
    finally:
        _kill_quietly(shell)


def test_capture_within_its_timeout_returns_normally(tmp_path):
    result = _capture(["bash", "-c", "echo fine; exit 2"], tmp_path, timeout=JOIN_LIMIT)
    assert result.returncode == 2 and result.stdout == "fine\n"
    with pytest.raises(subprocess.CalledProcessError):
        result.check_returncode()


# -------------------------------------------------- the pipe is closed exactly once


class _CloseCounter:
    """Wrap ``Popen`` so every pipe it creates counts its ``close`` calls."""

    def __init__(self, monkeypatch):
        self.calls: dict[str, int] = {}
        self.pids: list[int] = []
        real_popen = subprocess.Popen

        def factory(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            self.pids.append(process.pid)
            for name in ("stdin", "stdout", "stderr"):
                stream = getattr(process, name)
                if stream is not None:
                    self._count(name, stream)
            return process

        monkeypatch.setattr(procio.subprocess, "Popen", factory)

    def _count(self, name, stream):
        original = stream.close
        self.calls[name] = 0

        def close():
            self.calls[name] += 1
            original()

        stream.close = close

    def cleanup(self):
        for pid in self.pids:
            _kill_quietly(pid)


def test_streaming_pipe_is_closed_exactly_once_on_every_path(tmp_path, monkeypatch):
    counter = _CloseCounter(monkeypatch)
    try:
        _stream(["echo", "hi"], tmp_path)
        assert counter.calls == {"stdout": 1}

        counter.calls.clear()
        _stream(["sleep", "600"], tmp_path, watchdog=1.0)
        assert counter.calls == {"stdout": 1}

        counter.calls.clear()

        def interrupt(_line):
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            procio.run_streaming(
                ["bash", "-c", "echo x; sleep 600"], cwd=tmp_path, env=None, on_line=interrupt,
                no_progress_seconds=600,
            )
        assert counter.calls == {"stdout": 1}

        counter.calls.clear()
        monkeypatch.setattr(
            procio, "reap_process_group",
            lambda *_a: (_ for _ in ()).throw(RuntimeError("reap failed")),
        )
        with pytest.raises(RuntimeError, match="reap failed"):
            procio.run_streaming(
                ["bash", "-c", "echo x; sleep 600"], cwd=tmp_path, env=None, on_line=interrupt,
                no_progress_seconds=600,
            )
        assert counter.calls == {"stdout": 1}
    finally:
        counter.cleanup()


@pytest.mark.estate_allow(
    "short-timeout",
    reason="Short timeouts deliberately exercise every timeout cleanup path.",
    backlog="CLI-EXT-47",
)
def test_capture_pipes_are_closed_exactly_once_on_every_path(tmp_path, monkeypatch):
    counter = _CloseCounter(monkeypatch)
    try:
        _capture(["cat"], tmp_path, input="hello")
        assert counter.calls == {"stdin": 1, "stdout": 1, "stderr": 1}

        counter.calls.clear()
        _capture(["echo", "hi"], tmp_path)
        assert counter.calls == {"stdout": 1, "stderr": 1}

        counter.calls.clear()
        with pytest.raises(subprocess.TimeoutExpired):
            _capture(["sleep", "600"], tmp_path, input="x", timeout=0.5)
        assert counter.calls == {"stdin": 1, "stdout": 1, "stderr": 1}

        counter.calls.clear()  # interrupted while input is still being written: stdin is open
        with monkeypatch.context() as patch:
            patch.setattr(
                procio._StdinWriter, "step",
                lambda *_a: (_ for _ in ()).throw(KeyboardInterrupt()),
            )
            with pytest.raises(KeyboardInterrupt):
                _capture(["sleep", "600"], tmp_path, input="x" * 1_000_000)
        assert counter.calls == {"stdin": 1, "stdout": 1, "stderr": 1}

        counter.calls.clear()
        monkeypatch.setattr(
            procio, "reap_process_group",
            lambda *_a: (_ for _ in ()).throw(RuntimeError("reap failed")),
        )
        with pytest.raises(RuntimeError, match="reap failed"):
            _capture(["sleep", "600"], tmp_path, timeout=0.5)
        assert counter.calls == {"stdout": 1, "stderr": 1}
    finally:
        counter.cleanup()


# ---------------------------------------------------------------- reap_process_group


def test_reap_of_an_already_finished_group_finds_nothing_to_signal():
    process = subprocess.Popen(["true"], start_new_session=True)
    process.wait()
    procio.reap_process_group(process, 5.0)  # SIGTERM finds no group; the empty group ends the wait
    procio.reap_process_group(process, 0.0)  # no grace: SIGKILL finds no group either
    assert process.returncode == 0


def test_reap_of_a_reaped_leader_with_no_group_signals_nothing(monkeypatch):
    """A stale pgid may belong to an unrelated process by now: only the existence probe runs."""
    process = subprocess.Popen(["true"], start_new_session=True)
    process.wait()
    sent: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def killpg(group, signum):
        sent.append((group, signum))
        return real_killpg(group, signum)

    monkeypatch.setattr(procio.os, "killpg", killpg)
    procio.reap_process_group(process, 5.0)
    assert sent == [(process.pid, 0)]


def test_reap_tolerates_a_leader_reaped_behind_its_back(monkeypatch):
    process = subprocess.Popen(["true"], start_new_session=True)
    os.waitpid(process.pid, 0)  # reaped by someone else: returncode is still unknown here
    assert process.returncode is None
    procio.reap_process_group(process, 0.0)  # nothing to signal, nothing to wait for
    assert process.returncode == 0


def test_signal_arriving_while_handlers_are_restored_is_delivered_after_all_are_restored(
    monkeypatch, default_signals,
):
    real_signal = signal.signal
    first_restore: list[int] = []

    def signalling(signum, handler):
        if handler is signal.SIG_DFL and not first_restore:
            first_restore.append(signum)
            os.kill(os.getpid(), signal.SIGTERM)  # our handler is still installed for it
        return real_signal(signum, handler)

    with pytest.raises(SystemExit) as raised:
        with procio.terminate_as_exit():
            monkeypatch.setattr(signal, "signal", signalling)
    monkeypatch.undo()
    assert first_restore and raised.value.code == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_DFL


def test_a_failing_restore_does_not_skip_the_remaining_handlers(monkeypatch, default_signals):
    real_signal = signal.signal
    attempts: list[int] = []

    def flaky(signum, handler):
        if handler is signal.SIG_DFL:
            attempts.append(signum)
            if len(attempts) == 1:
                raise OSError("injected restore failure")
        return real_signal(signum, handler)

    with pytest.raises(OSError, match="injected restore failure"):
        with procio.terminate_as_exit():
            monkeypatch.setattr(signal, "signal", flaky)
    monkeypatch.undo()
    assert len(attempts) == 2, "the second handler was still restored"
    restored = [s for s in TERMINATION_SIGNALS if signal.getsignal(s) == signal.SIG_DFL]
    assert len(restored) == 1, "exactly the one whose restore failed keeps our handler"
    for signum in TERMINATION_SIGNALS:
        signal.signal(signum, signal.SIG_DFL)


def test_reap_with_no_grace_kills_at_once_and_reaps_the_leader(tmp_path):
    pidfile = tmp_path / "ready.pid"
    process = subprocess.Popen(
        ["bash", "-c", f"trap '' TERM; echo $$ > {pidfile}; sleep 600"], start_new_session=True,
    )
    try:
        _wait_pidfile(pidfile)
        procio.reap_process_group(process, 0.0)  # SIGTERM is ignored, so only SIGKILL ends it
        assert process.returncode == -signal.SIGKILL and not _alive(process.pid)
    finally:
        _kill_quietly(process.pid)


def test_reap_waits_out_a_group_member_that_outlives_its_leader_then_kills_it(tmp_path):
    pidfile = tmp_path / "member.pid"
    process = subprocess.Popen(
        # the member writes its own pid only after its trap is installed (a starved member
        # that had not installed it yet would die of the SIGTERM and skip the escalation)
        ["bash", "-c", f"(trap '' TERM; echo $BASHPID > {pidfile}; sleep 600) & exit 0"],
        start_new_session=True,
    )
    member = None
    try:
        member = _wait_pidfile(pidfile)
        process.wait()
        assert _alive(member), "the member ignores SIGTERM and outlives its leader"
        procio.reap_process_group(process, 0.3)
        _wait_gone(member)
    finally:
        _kill_quietly(member)


def test_an_interrupted_reap_wait_still_kills_the_group(tmp_path, reap_orphans):
    shell_pidfile = tmp_path / "shell.pid"
    real = subprocess.Popen(
        ["bash", "-c", f"trap '' TERM; echo $$ > {shell_pidfile}; while :; do sleep 0.1; done"],
        start_new_session=True,
    )

    class Interrupted:
        pid = real.pid
        returncode = None
        waited = False

        def poll(self):
            raise KeyboardInterrupt

        def wait(self):
            self.waited = True
            return real.wait()

    interrupted = Interrupted()
    try:
        _wait_pidfile(shell_pidfile)
        with pytest.raises(KeyboardInterrupt):
            procio.reap_process_group(interrupted, 30.0)
        assert interrupted.waited and real.returncode == -signal.SIGKILL, (
            "SIGKILL went out and the leader was waited for (no zombie) before the raise"
        )
    finally:
        _kill_quietly(real.pid)


# --------------------------------------------------------- pipe_holder_pids / process_tree


def test_pipe_holder_scan_names_holders_and_skips_what_it_cannot_inspect(monkeypatch):
    read_end, write_end = os.pipe()
    holder = subprocess.Popen(["sleep", "600"], pass_fds=[write_end])
    unreadable = subprocess.Popen(["sleep", "600"], pass_fds=[write_end])
    try:
        inode = os.fstat(read_end).st_ino
        assert procio.pipe_holder_pids(inode) == sorted([holder.pid, unreadable.pid])
        assert procio.pipe_holder_pids(inode + 10_000_000) == []

        real_listdir = os.listdir

        def listdir(path):
            if str(path) == f"/proc/{unreadable.pid}/fd":
                raise PermissionError("injected: not ours to inspect")
            return real_listdir(path)

        monkeypatch.setattr(procio.os, "listdir", listdir)
        assert procio.pipe_holder_pids(inode) == [holder.pid]
    finally:
        os.close(read_end)
        os.close(write_end)
        for process in (holder, unreadable):
            process.kill()
            process.wait()


def test_pipe_holder_scan_survives_a_descriptor_closing_mid_scan(monkeypatch):
    """A descriptor closed between the listing and its readlink must not hide the holder.

    A child inside ``execve`` closes its close-on-exec descriptors while ``Popen`` has already
    returned (the errpipe closes first), so ``/proc/<pid>/fd`` still lists a descriptor whose
    ``readlink`` then fails with ENOENT. The pipe end it passed with ``pass_fds`` is still open.
    """
    own = os.getpid()
    vanishing, other = own + 1, own + 2  # derived from our pid so they never collide with it
    listings = {
        "/proc": [str(vanishing), str(other)],
        f"/proc/{vanishing}/fd": ["3", "4"],  # the closing descriptor is listed first
        f"/proc/{other}/fd": ["3"],
    }
    links = {f"/proc/{vanishing}/fd/4": "pipe:[4242]", f"/proc/{other}/fd/3": "pipe:[1]"}

    def readlink(path):
        if str(path) not in links:
            raise FileNotFoundError("injected: closed since the listing")
        return links[str(path)]

    monkeypatch.setattr(procio, "_PROC_ROOT", "/proc")
    monkeypatch.setattr(procio.os, "listdir", lambda path: listings[str(path)])
    monkeypatch.setattr(procio.os, "readlink", readlink)
    assert procio.pipe_holder_pids(4242) == [vanishing]


def test_process_tree_lists_a_real_process_and_its_descendants(tmp_path, reap_orphans):
    shell_pidfile, child_pidfile = tmp_path / "shell.pid", tmp_path / "child.pid"
    process = subprocess.Popen(
        ["bash", "-c", f"echo $$ > {shell_pidfile}; sleep 600 & echo $! > {child_pidfile}; wait"],
        start_new_session=True,
    )
    try:
        child = _wait_pidfile(child_pidfile)
        tree = procio.process_tree(process.pid)
        assert [entry.pid for entry in tree] == [process.pid, child]
        assert tree[0].ppid == os.getpid() and tree[1].ppid == process.pid
        assert tree[0].pgid == tree[1].pgid == process.pid
        assert tree[1].argv == "sleep 600" and tree[1].line == f"{child} {process.pid} S sleep 600"
        assert procio.process_tree(2**30) == []
    finally:
        procio.reap_process_group(process, 5.0)


def _fake_proc(root: Path, pid: int, comm: str, state: str, ppid: int, pgid: int, cmdline: bytes):
    directory = root / str(pid)
    directory.mkdir()
    (directory / "stat").write_text(f"{pid} ({comm}) {state} {ppid} {pgid} 0 0\n", encoding="utf-8")
    (directory / "cmdline").write_bytes(cmdline)


def test_process_tree_parses_odd_entries_from_a_fake_proc(tmp_path, monkeypatch):
    root = tmp_path / "proc"
    root.mkdir()
    _fake_proc(root, 10, "root", "S", 1, 10, b"prog\0--flag\0value\0")
    _fake_proc(root, 20, "we) ird (name", "R", 10, 10, b"x" * 500 + b"\0")
    _fake_proc(root, 30, "zombie", "Z", 10, 10, b"")
    _fake_proc(root, 40, "cycle-a", "S", 50, 40, b"a\0")
    _fake_proc(root, 50, "cycle-b", "S", 40, 40, b"b\0")
    _fake_proc(root, 60, "grandchild", "S", 30, 60, b"\xff\xfe\0bin\0")
    _fake_proc(root, 95, "nocmdline", "S", 10, 10, b"")
    (root / "95" / "cmdline").unlink()  # unreadable command line: the command name stands in
    (root / "70").mkdir()  # no stat file: exited meanwhile
    (root / "80").mkdir()
    (root / "80" / "stat").write_text("80 (garbled", encoding="utf-8")
    (root / "90").mkdir()
    (root / "90" / "stat").write_text("90 (x) S notanumber 1 0\n", encoding="utf-8")
    (root / "self").mkdir()  # not a pid
    monkeypatch.setattr(procio, "_PROC_ROOT", str(root))

    tree = procio.process_tree(10)
    assert [entry.pid for entry in tree] == [10, 20, 30, 60, 95]
    assert tree[4].argv == "[nocmdline]"
    assert tree[0].argv == "prog --flag value" and tree[0].state == "S" and tree[0].ppid == 1
    assert tree[1].argv == "x" * 200 and tree[1].state == "R", "argv is cut to 200 characters"
    assert tree[2].argv == "[zombie]" and tree[2].state == "Z"
    assert tree[3].argv == "\ufffd\ufffd bin" and tree[3].ppid == 30
    assert [entry.pid for entry in procio.process_tree(40)] == [40, 50], "ppid cycles terminate"
    assert procio.process_tree(70) == [] and procio.process_tree(80) == []


def test_pipe_holder_scan_reads_descriptor_links_from_a_fake_proc(tmp_path, monkeypatch):
    root = tmp_path / "proc"
    own = os.getpid()  # fake pids are derived from it, so they can never collide with it
    first, second, third, no_fd = own + 1, own + 2, own + 3, own + 4
    for pid, target in ((first, "pipe:[777]"), (second, "pipe:[778]"), (third, "pipe:[777]")):
        (root / str(pid) / "fd").mkdir(parents=True)
        (root / str(pid) / "fd" / "3").symlink_to("/dev/null")
        (root / str(pid) / "fd" / "4").symlink_to(target)
    (root / str(no_fd)).mkdir()  # no fd directory
    (root / str(own) / "fd").mkdir(parents=True)
    (root / str(own) / "fd" / "5").symlink_to("pipe:[777]")  # never reported: ourselves
    (root / "not-a-pid").mkdir()
    monkeypatch.setattr(procio, "_PROC_ROOT", str(root))
    assert procio.pipe_holder_pids(777) == [first, third]


# ------------------------------------------------------------------- package contract


def test_procio_is_stdlib_only_and_the_package_stays_dependency_free():
    tree = ast.parse(Path(procio.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "procio must not use relative imports"
            assert node.module is not None
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
    assert "cli_extended" not in imported
    project = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == []


def test_procio_is_importable_as_a_submodule_without_touching_the_contract_version():
    import cli_extended

    assert cli_extended.procio is procio
    assert cli_extended.CONTRACT_VERSION == 1
