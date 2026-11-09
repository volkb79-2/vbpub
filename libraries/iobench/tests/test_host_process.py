"""IOB-HOST-01: a controller death stops commands with forked workers."""
import ctypes
import os
import selectors
import signal
import socket
import subprocess
import sys
from pathlib import Path


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
