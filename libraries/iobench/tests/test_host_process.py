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
    """Exercise PR_SET_PDEATHSIG and process groups with local Python workers, no disk or fio."""
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
    pidfd = None
    try:
        controller = subprocess.Popen([sys.executable, "-c", controller_source, socket_path, worker_source],
                                      env=parent_env, start_new_session=True)
        pidfd = os.pidfd_open(controller.pid)
        ready = {}
        with selectors.DefaultSelector() as events:
            events.register(ready_socket, selectors.EVENT_READ, "ready")
            events.register(pidfd, selectors.EVENT_READ, "controller")
            while "SUPERVISOR" not in ready or "WORKERS" not in ready:
                for key, _mask in events.select():
                    if key.data == "controller":
                        controller.wait()
                        raise AssertionError("controller exited before its forked worker became ready")
                    message = ready_socket.recv(1024).decode("ascii").split()
                    ready[message[0]] = tuple(int(pid) for pid in message[1:])
        supervisor_pid = ready["SUPERVISOR"][0]
        worker_pids = ready["WORKERS"]
        assert len(worker_pids) == 2 and all(_state(pid) not in (None, "Z") for pid in worker_pids)

        os.kill(controller.pid, signal.SIGKILL)
        controller.wait()
        # The test process is the supervisor's subreaper, so this waits for its
        # process-group cleanup and reaping to finish without a speed deadline.
        assert os.waitpid(supervisor_pid, 0)[0] == supervisor_pid
        remaining = tuple(pid for pid in worker_pids if not _reaped_or_gone(pid))
        assert not remaining, f"forked workers survived controller SIGKILL: {sorted(remaining)}"
        completed = True
    finally:
        if controller is not None and controller.poll() is None:
            controller.kill()
            controller.wait()
        if not completed:
            for process_group in (supervisor_pid, worker_pids[0] if worker_pids else None):
                if process_group is not None:
                    try:
                        os.killpg(process_group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            for pid in ({supervisor_pid} if supervisor_pid is not None else set()) | set(worker_pids):
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass
        if pidfd is not None:
            os.close(pidfd)
        ready_socket.close()
        _set_child_subreaper(was_subreaper)
