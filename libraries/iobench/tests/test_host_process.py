"""IOB-HOST-01: a controller death stops commands with forked workers."""
import ctypes
import os
import selectors
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from iobench import host as host_mod


def _state(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    return raw.rsplit(")", 1)[1].split()[0]


def _reaped_or_gone(pid):
    try:
        child, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return _state(pid) is None
    return child == pid or _state(pid) is None


def _wait_pidfd_exit(pidfd):
    """Wait for kernel-reported process exit without a scheduling deadline."""
    with selectors.DefaultSelector() as events:
        events.register(pidfd, selectors.EVENT_READ)
        events.select()


class _RecordingPopen:
    """A Popen stand-in that records every call the handle makes to it; no child process exists."""

    def __init__(self, argv, **kwargs) -> None:
        self.argv, self.kwargs = argv, kwargs
        self.pid, self.returncode = 4242, 0
        self.calls: list[object] = []
        self.running = True

    def poll(self):
        self.calls.append("poll")
        return None if self.running else self.returncode

    def send_signal(self, signum):
        self.calls.append(("send_signal", signum))
        self.running = False

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        self.running = False
        return self.returncode


def _recorded_handle(monkeypatch):
    monkeypatch.setattr(host_mod, "_PRCTL", object())
    monkeypatch.setattr(host_mod.subprocess, "Popen", _RecordingPopen)
    return host_mod._ProcHandle(["true"])


def test_c1_kill_marks_the_handle_closed_and_a_second_kill_touches_nothing(monkeypatch):
    """C1 (host.py:535 ``self._closed = True`` True->False): after ``kill`` the handle is closed, so the abort path can be
    reached twice: the second ``kill`` returns at once and never signals or waits on the process again."""
    handle = _recorded_handle(monkeypatch)
    process = handle._p
    assert handle._closed is False
    try:
        handle.kill()
    finally:
        # Under the mutant ``kill`` raises ValueError on the file it just closed, before its last line runs; this
        # assertion still executes then, so the defect (a handle that never marks itself closed) is reported as a plain
        # test failure instead of an uncaught ValueError.
        assert handle._closed is True
    assert handle._out.closed and handle._err.closed
    assert process.calls == ["poll", ("send_signal", signal.SIGTERM), ("wait", host_mod.SUPERVISOR_EXIT_TIMEOUT_S)]
    handle.kill()
    assert process.calls == ["poll", ("send_signal", signal.SIGTERM), ("wait", host_mod.SUPERVISOR_EXIT_TIMEOUT_S)]


def test_c2_wait_marks_the_handle_closed_and_a_later_kill_touches_nothing(monkeypatch):
    """C2 (host.py:515 ``self._closed = True`` in ``wait``'s finally True->False): a finished handle is closed, so the
    claim cleanup that later calls ``kill`` neither signals nor waits on the process again."""
    handle = _recorded_handle(monkeypatch)
    process = handle._p
    assert handle.wait() == host_mod.RunResult(0, "", "")
    # Under the mutant this is still False; after the kill below the handle would then reach the closed files.
    assert handle._closed is True
    assert process.calls == [("wait", None)]
    handle.kill()
    assert process.calls == [("wait", None)]
    assert handle._out.closed and handle._err.closed


def test_a_second_wait_after_a_kill_raises_on_the_closed_output_files(monkeypatch):
    """What the real code does with a second ``wait()`` after ``kill()``: the closed temporary files raise, so a caller
    cannot read an answer from a handle the abort path already closed. Pinned, not silently tolerated."""
    handle = _recorded_handle(monkeypatch)
    handle.kill()
    with pytest.raises(ValueError, match="closed file"):
        handle.wait()


def _set_child_subreaper(enabled):
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:  # PR_GET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if libc.prctl(36, int(enabled), 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return previous.value


def test_host_01_sigkill_controller_stops_and_reaps_forked_workers(tmp_path):
    """Exercise async parent-death cleanup with local Python workers, no disk or fio."""
    project = Path(__file__).resolve().parents[1]
    parent_env = os.environ.copy()
    parent_env["PYTHONPATH"] = os.pathsep.join((str(project / "src"), parent_env.get("PYTHONPATH", "")))
    socket_path = str(tmp_path / "ready.sock")
    worker_source = """
import os, signal, socket, sys
child = os.fork()
if child:
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as ready:
        ready.sendto(f"WORKERS {os.getpid()} {child}".encode(), sys.argv[1])
while True:
    signal.pause()
"""
    controller_source = """
import signal, socket, sys
from iobench.host import _ProcHandle
handle = _ProcHandle([sys.executable, "-c", sys.argv[2], sys.argv[1]])
with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as ready:
    ready.sendto(f"SUPERVISOR {handle._p.pid}".encode(), sys.argv[1])
while True:
    signal.pause()
"""

    was_subreaper = _set_child_subreaper(True)
    ready_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    ready_socket.bind(socket_path)
    controller = None
    supervisor_pid = None
    worker_pids = ()
    completed = False
    controller_pidfd = None
    supervisor_pidfd = None
    worker_pidfds = {}
    try:
        controller = subprocess.Popen([sys.executable, "-c", controller_source, socket_path, worker_source],
                                      env=parent_env, start_new_session=True)
        controller_pidfd = os.pidfd_open(controller.pid)
        ready = {}
        with selectors.DefaultSelector() as events:
            events.register(ready_socket, selectors.EVENT_READ, "ready")
            events.register(controller_pidfd, selectors.EVENT_READ, "controller")
            while "SUPERVISOR" not in ready or "WORKERS" not in ready:
                for key, _mask in events.select():
                    if key.data == "controller":
                        controller.wait()
                        raise AssertionError("controller exited before its forked worker became ready")
                    message = ready_socket.recv(1024).decode("ascii").split()
                    ready[message[0]] = tuple(int(pid) for pid in message[1:])
        supervisor_pid = ready["SUPERVISOR"][0]
        worker_pids = ready["WORKERS"]
        supervisor_pidfd = os.pidfd_open(supervisor_pid)
        for pid in worker_pids:
            worker_pidfds[pid] = os.pidfd_open(pid)
        assert len(worker_pids) == 2 and all(_state(pid) not in (None, "Z") for pid in worker_pids)

        os.kill(controller.pid, signal.SIGKILL)
        _wait_pidfd_exit(controller_pidfd)
        controller.wait()
        # pidfd readiness waits for actual process exit without making the
        # result depend on a wall-clock speed threshold.
        _wait_pidfd_exit(supervisor_pidfd)
        reaped, _status = os.waitpid(supervisor_pid, os.WNOHANG)
        assert reaped == supervisor_pid, "supervisor pidfd became readable without a waitable exit"
        remaining = tuple(pid for pid in worker_pids if not _reaped_or_gone(pid))
        assert not remaining, f"forked workers survived controller SIGKILL: {sorted(remaining)}"
        completed = True
    finally:
        if controller is not None and controller.poll() is None:
            controller.kill()
            if controller_pidfd is not None:
                _wait_pidfd_exit(controller_pidfd)
            controller.wait()
        if not completed:
            for process_group in (supervisor_pid, worker_pids[0] if worker_pids else None):
                if process_group is not None:
                    try:
                        os.killpg(process_group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            cleanup_pidfds = [supervisor_pidfd] if supervisor_pidfd is not None else []
            cleanup_pidfds.extend(worker_pidfds.values())
            for process_pidfd in cleanup_pidfds:
                _wait_pidfd_exit(process_pidfd)
            for pid in ({supervisor_pid} if supervisor_pid is not None else set()) | set(worker_pids):
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass
        all_pidfds = [pidfd for pidfd in (controller_pidfd, supervisor_pidfd) if pidfd is not None]
        all_pidfds.extend(worker_pidfds.values())
        for process_pidfd in all_pidfds:
            os.close(process_pidfd)
        ready_socket.close()
        _set_child_subreaper(was_subreaper)
