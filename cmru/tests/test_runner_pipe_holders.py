"""KI-178 (items 1 and 2): a step ends when its child exits, not when every pipe holder does.

A grandchild that inherited the output pipe keeps it open after the step's own process has
exited, so reading to EOF never finished and an unattended release hung without a message. The
runner stops reading a bounded drain period after the child exits, keeps the child's exit code and
names the holders; children start in their own process group so an interrupt reaps them all.
All process checks are by pid, never by name.
"""
from __future__ import annotations

import os
import resource
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cmru import runner

DRAIN = 0.5
JOIN_LIMIT = 60.0  # far above the drain; a regression hangs on a 600 s sleeper


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


def _kill_quietly(pid: int | None) -> None:
    """Kill and reap a process this test started (an orphaned grandchild is reparented to us)."""
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


def _run_in_thread(argv, cwd, log, **kwargs):
    """Run ``run_command`` on a thread so a hanging runner fails the test instead of the suite."""
    outcome: dict = {}

    def target():
        try:
            outcome["result"] = runner.run_command(argv, cwd, log, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - the test inspects whatever came out
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


@pytest.fixture(autouse=True)
def _short_drain(monkeypatch):
    monkeypatch.setattr(runner, "OUTPUT_DRAIN_SECONDS", DRAIN)


def _holder_script(pidfile: Path, exit_code: int) -> str:
    return f"sleep 600 & echo $! > {pidfile}; echo hello; echo world; exit {exit_code}"


def test_grandchild_holding_the_pipe_does_not_hang_and_keeps_exit_code(tmp_path, capsys):
    pidfile = tmp_path / "holder.pid"
    log = tmp_path / "step.log"
    holder = None
    try:
        with log.open("a", encoding="utf-8") as handle:
            thread, outcome = _run_in_thread(
                ["bash", "-c", _holder_script(pidfile, 3)], tmp_path, handle, quiet=True, log_path=log,
            )
            holder = _wait_pidfile(pidfile)
            thread.join(JOIN_LIMIT)
            assert not thread.is_alive(), "run_command is still waiting for pipe EOF"
        error = outcome.get("error")
        assert isinstance(error, subprocess.CalledProcessError) and error.returncode == 3
        logged = log.read_text(encoding="utf-8")
        assert logged.startswith("hello\nworld\n")
        assert f"pid(s) {holder}" in logged and logged.count("\n") == 3
        out = capsys.readouterr().out
        warn = [line for line in out.splitlines() if line.startswith("[WARN]")]
        assert len(warn) == 1 and str(holder) in warn[0]
        assert _alive(holder), "the runner must name the holder, not kill it"
    finally:
        _kill_quietly(holder)


def test_grandchild_holding_the_pipe_with_a_successful_child_still_returns(tmp_path, capsys):
    pidfile = tmp_path / "holder.pid"
    log = tmp_path / "step.log"
    holder = None
    try:
        with log.open("a", encoding="utf-8") as handle:
            thread, outcome = _run_in_thread(
                ["bash", "-c", _holder_script(pidfile, 0)], tmp_path, handle,
            )
            holder = _wait_pidfile(pidfile)
            thread.join(JOIN_LIMIT)
            assert not thread.is_alive(), "run_command is still waiting for pipe EOF"
        assert "error" not in outcome and outcome["result"].elapsed_seconds >= 0
        out = capsys.readouterr().out
        assert "hello\nworld\n" in out
        assert sum(line.startswith("[WARN]") for line in out.splitlines()) == 1
    finally:
        _kill_quietly(holder)


def test_holder_scan_failure_still_warns_without_pids(tmp_path, monkeypatch, capsys):
    def broken_scan(_inode):
        raise OSError("injected /proc failure")

    monkeypatch.setattr(runner, "_pipe_holder_pids", broken_scan)
    pidfile = tmp_path / "holder.pid"
    log = tmp_path / "step.log"
    holder = None
    try:
        with log.open("a", encoding="utf-8") as handle:
            thread, outcome = _run_in_thread(
                ["bash", "-c", _holder_script(pidfile, 0)], tmp_path, handle,
            )
            holder = _wait_pidfile(pidfile)
            thread.join(JOIN_LIMIT)
            assert not thread.is_alive()
        assert "error" not in outcome
        warn = [line for line in capsys.readouterr().out.splitlines() if line.startswith("[WARN]")]
        assert len(warn) == 1 and str(holder) not in warn[0]
    finally:
        _kill_quietly(holder)


def test_ordinary_child_output_lines_order_and_exit_code_are_unchanged(tmp_path, capsys):
    script = r"printf 'a\r\nb\rc\nd\n'; echo to-stderr >&2; printf 'last-without-newline'; exit 4"
    log = tmp_path / "step.log"
    with log.open("a", encoding="utf-8") as handle:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            runner.run_command(["bash", "-c", script], tmp_path, handle)
    assert caught.value.returncode == 4
    assert log.read_text(encoding="utf-8") == "a\nb\nc\nd\nto-stderr\nlast-without-newline"
    out = capsys.readouterr().out
    assert out.endswith("a\nb\nc\nd\nto-stderr\nlast-without-newline")
    assert "[WARN]" not in out


def test_multibyte_output_split_across_reads_is_decoded_intact(tmp_path):
    script = (
        "import sys, time\n"
        "data = ('\\u00e4\\u20ac\\U0001f600' * 3 + '\\n').encode('utf-8')\n"
        "for index in range(len(data)):\n"
        "    sys.stdout.buffer.write(data[index:index + 1]); sys.stdout.buffer.flush()\n"
        "    time.sleep(0.002)\n"
    )
    log = tmp_path / "step.log"
    with log.open("a", encoding="utf-8") as handle:
        runner.run_command(["python3", "-c", script], tmp_path, handle, quiet=True, log_path=log)
    assert log.read_text(encoding="utf-8") == "\u00e4\u20ac\U0001f600" * 3 + "\n"


def test_child_starts_in_its_own_session_and_process_group(tmp_path):
    script = "import os; print(os.getpgrp() == os.getpid(), os.getsid(0) == os.getpid(), os.getsid(0) != %d)" % (
        os.getsid(0),
    )
    log = tmp_path / "step.log"
    with log.open("a", encoding="utf-8") as handle:
        runner.run_command(["python3", "-c", script], tmp_path, handle, quiet=True, log_path=log)
    assert log.read_text(encoding="utf-8") == "True True True\n"


class _InterruptingLog:
    """A log handle that raises ``KeyboardInterrupt`` once a marker line is written."""

    def __init__(self, marker: str):
        self.marker = marker

    def write(self, text: str) -> int:
        if self.marker in text:
            raise KeyboardInterrupt
        return len(text)

    def flush(self) -> None:
        pass


def test_interrupt_reaps_the_whole_process_group(tmp_path):
    pidfile = tmp_path / "holder.pid"
    shell_pidfile = tmp_path / "shell.pid"
    script = f"echo $$ > {shell_pidfile}; sleep 600 & echo $! > {pidfile}; echo ready; wait"
    holder = shell = None
    try:
        with pytest.raises(KeyboardInterrupt):
            runner.run_command(["bash", "-c", script], tmp_path, _InterruptingLog("ready"), quiet=True)
        holder = _wait_pidfile(pidfile)
        shell = _wait_pidfile(shell_pidfile)
        deadline = time.monotonic() + JOIN_LIMIT
        while time.monotonic() < deadline and (_alive(holder) or _alive(shell)):
            time.sleep(0.05)
        assert not _alive(shell), "the direct child survived the interrupt"
        assert not _alive(holder), "a grandchild in the step's process group survived the interrupt"
    finally:
        _kill_quietly(holder)
        _kill_quietly(shell)


def test_interrupt_escalates_to_sigkill_for_a_group_that_ignores_sigterm(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "GROUP_TERM_GRACE_SECONDS", 0.3)
    shell_pidfile = tmp_path / "shell.pid"
    script = f"trap '' TERM; echo $$ > {shell_pidfile}; echo ready; while :; do :; done"
    shell = None
    try:
        with pytest.raises(KeyboardInterrupt):
            runner.run_command(["bash", "-c", script], tmp_path, _InterruptingLog("ready"), quiet=True)
        shell = _wait_pidfile(shell_pidfile)
        deadline = time.monotonic() + JOIN_LIMIT
        while time.monotonic() < deadline and _alive(shell):
            time.sleep(0.05)
        assert not _alive(shell), "a child that ignores SIGTERM must still be killed"
    finally:
        _kill_quietly(shell)


def test_pipe_descriptor_above_the_select_limit_still_streams_output_and_exit_code(tmp_path):
    """``select.select`` rejects descriptors >= 1024; the pipe of a busy process can be that high."""
    wanted = 1300
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    held: list[int] = []
    raised = False
    try:
        if soft != resource.RLIM_INFINITY and soft < wanted + 64:
            assert hard == resource.RLIM_INFINITY or hard >= wanted + 64, "RLIMIT_NOFILE hard limit too low"
            resource.setrlimit(resource.RLIMIT_NOFILE, (wanted + 64, hard))
            raised = True
        while len(held) < 1100:
            held.append(os.open(os.devnull, os.O_RDONLY))
        assert held[-1] >= 1024
        log = tmp_path / "step.log"
        with log.open("a", encoding="utf-8") as handle:
            with pytest.raises(subprocess.CalledProcessError) as caught:
                runner.run_command(["bash", "-c", "echo hi; echo there; exit 3"], tmp_path, handle)
        assert caught.value.returncode == 3
        assert log.read_text(encoding="utf-8") == "hi\nthere\n"
    finally:
        for descriptor in held:
            os.close(descriptor)
        if raised:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_runner_module_does_not_use_select_select():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "select.select" not in source


TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


class _HandlerSpy:
    """A log handle recording which signal handlers are installed while the command streams."""

    def __init__(self):
        self.seen: dict[int, object] = {}

    def write(self, text: str) -> int:
        self.seen = {signum: signal.getsignal(signum) for signum in TERMINATION_SIGNALS}
        return len(text)

    def flush(self) -> None:
        pass


def test_run_command_installs_termination_handlers_and_restores_the_previous_ones(tmp_path):
    before = {signum: signal.getsignal(signum) for signum in TERMINATION_SIGNALS}
    spy = _HandlerSpy()

    runner.run_command(["echo", "hi"], tmp_path, spy)
    assert spy.seen and all(
        callable(spy.seen[signum]) and spy.seen[signum] is not before[signum]
        for signum in TERMINATION_SIGNALS
    )
    assert {signum: signal.getsignal(signum) for signum in TERMINATION_SIGNALS} == before

    with pytest.raises(subprocess.CalledProcessError):
        runner.run_command(["false"], tmp_path, spy)
    assert {signum: signal.getsignal(signum) for signum in TERMINATION_SIGNALS} == before


def test_run_command_off_the_main_thread_installs_no_handlers_and_still_runs(tmp_path):
    before = {signum: signal.getsignal(signum) for signum in TERMINATION_SIGNALS}
    spy = _HandlerSpy()
    thread, outcome = _run_in_thread(["echo", "hi"], tmp_path, spy)
    thread.join(JOIN_LIMIT)
    assert not thread.is_alive() and "error" not in outcome
    assert spy.seen == {signum: before[signum] for signum in TERMINATION_SIGNALS}
    assert {signum: signal.getsignal(signum) for signum in TERMINATION_SIGNALS} == before


DRIVER = """\
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from cmru import runner

with open(sys.argv[2], "a", encoding="utf-8") as log:
    runner.run_command(["bash", "-c", sys.argv[3]], Path(sys.argv[4]), log, quiet=True)
"""


@pytest.mark.parametrize("signum", TERMINATION_SIGNALS, ids=["SIGTERM", "SIGHUP"])
def test_termination_signal_to_cmru_reaps_the_whole_step_tree(tmp_path, signum):
    """cmru dying of SIGTERM/SIGHUP must not leave the step running as an orphan in its own session."""
    driver_file = tmp_path / "driver.py"
    driver_file.write_text(DRIVER, encoding="utf-8")
    pidfile = tmp_path / "holder.pid"
    shell_pidfile = tmp_path / "shell.pid"
    script = f"echo $$ > {shell_pidfile}; sleep 600 & echo $! > {pidfile}; wait"
    source_root = str(Path(runner.__file__).resolve().parent.parent)
    driver = subprocess.Popen(
        [sys.executable, str(driver_file), source_root, str(tmp_path / "step.log"), script, str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    holder = shell = None
    try:
        holder = _wait_pidfile(pidfile)
        shell = _wait_pidfile(shell_pidfile)
        os.kill(driver.pid, signum)
        assert driver.wait(timeout=JOIN_LIMIT) == 128 + signum
        deadline = time.monotonic() + JOIN_LIMIT
        while time.monotonic() < deadline and (_alive(holder) or _alive(shell)):
            time.sleep(0.05)
        assert not _alive(shell), "the step's shell survived the signal sent to cmru"
        assert not _alive(holder), "a grandchild of the step survived the signal sent to cmru"
    finally:
        if driver.poll() is None:
            driver.kill()
            driver.wait()
        _kill_quietly(holder)
        _kill_quietly(shell)
