"""Test-suite wiring: ``pytester`` drives the opt-in plugin tests; a guard keeps tests tidy.

The leftover-children guard (package P11). assay judges a command's process tree when the
command's leader exits, so a test that leaves a child of the pytest process behind, even a
zombie nobody ``wait()``ed for, fails the whole R0/R2 run with ``leaked-process-tree``. The
guard makes that visible per test instead of at the end of the run.

Linux only: it reads ``/proc`` and uses ``pidfd_open``/``pidfd_send_signal``/``waitid(P_PIDFD)``
(Python 3.9+, Linux 5.4+).
"""
import ctypes
import os
import signal
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]
PR_SET_CHILD_SUBREAPER = 36


def read_stat(pid):
    """``(comm, state, ppid, starttime)`` from ``/proc/<pid>/stat``, or None when it is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:  # the process vanished (or was never there)
        return None
    head, _, tail = stat.rpartition(")")  # the comm field may itself contain spaces/parens
    fields = tail.split()
    return head.partition("(")[2], fields[0], int(fields[1]), fields[19]


def direct_children() -> dict:
    """The direct children of this process as ``{(pid, starttime): (comm, state)}``.

    Reads ``/proc/<pid>/stat`` (Linux only). Zombies are included (state ``Z``). The start
    time is part of the key so a reused pid is never mistaken for a child that existed before.
    A pid that vanishes while the scan runs is skipped.
    """
    me = os.getpid()
    found = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        stat = read_stat(entry)
        if stat is not None and stat[2] == me:
            found[(int(entry), stat[3])] = (stat[0], stat[1])
    return found


def remove_if_still_ours(pid, starttime):
    """Kill (when alive) and reap ``pid`` if it is still the very child recorded; else None.

    The recorded list is a snapshot: the child may have exited and been reaped by someone else
    since, and its pid recycled. A pidfd pins the process identity: open it first, re-read the
    process (still our child, same start time), signal and reap only through that pidfd, so a
    recycled pid is never signalled or reaped. Returns how the child ended (exit status, or
    the negated signal number), or None when it is gone or no longer the same direct child.
    """
    try:
        fd = os.pidfd_open(pid)
    except OSError:  # already gone
        return None
    try:
        now = read_stat(pid)
        if now is None or now[2] != os.getpid() or now[3] != starttime:
            return None  # a different process under the same pid, or not our child any more
        if now[1] != "Z":
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        info = os.waitid(os.P_PIDFD, fd, os.WEXITED)
        killed = info.si_code in (os.CLD_KILLED, os.CLD_DUMPED)
        return -info.si_status if killed else info.si_status
    except (ProcessLookupError, ChildProcessError):  # it ended and was reaped by someone else
        return None
    finally:
        os.close(fd)


def remove_new_children(before) -> list:
    """Remove every direct child not in ``before`` (see ``remove_if_still_ours``); describe each."""
    leaked = []
    for (pid, starttime), (comm, state) in direct_children().items():
        if (pid, starttime) in before:
            continue
        ended = remove_if_still_ours(pid, starttime)
        if ended is None:
            continue  # gone or replaced since the snapshot: not ours to touch, not a leak
        kind = "zombie" if state == "Z" else f"alive, ended with status {ended}"
        leaked.append(f"pid {pid} comm {comm} ({kind})")
    return leaked


def pytest_configure(config):
    # assay makes the command's leader a child subreaper, so a test's orphaned grandchild (a
    # holder whose parent already exited) becomes a child of this process and, once killed,
    # a zombie nobody reaps. Do the same here so a plain pytest run sees exactly what assay
    # sees (idempotent when assay already did it). The suite is Linux-only (see the module
    # docstring); when prctl fails the run ABORTS on purpose, loudly: without the flag this
    # guard would silently see less than assay does and give a false green.
    if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER) failed")
    config.addinivalue_line(
        "markers",
        "allow_children(reason): the test legitimately keeps a helper child process of the "
        "pytest process alive past its end (for example a session-scoped helper); the "
        "leftover-children guard does not fail it. Always give the reason. Expected use: none.",
    )


@pytest.fixture(autouse=True)
def _no_leftover_children(request):
    """Fail a test that leaves a new child of the pytest process behind, alive or zombie.

    New children are removed by pid only, pinned by a pidfd (never by name): a zombie is
    reaped, a live child is sent SIGKILL and reaped. Children that existed before the test
    (helpers of xdist or pytester) are never touched. A test marked ``allow_children`` is left
    alone.
    """
    before = set(direct_children())
    yield
    if request.node.get_closest_marker("allow_children") is not None:
        return
    leaked = remove_new_children(before)
    if leaked:
        pytest.fail(
            "the test left child processes of the pytest process behind (killed and reaped "
            "by the guard; wait for what you start): " + "; ".join(leaked),
            pytrace=False,
        )
