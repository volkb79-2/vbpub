"""Test-suite wiring: ``pytester`` drives the opt-in plugin tests; a guard keeps tests tidy.

The leftover-children guard (package P11). assay judges a command's process tree when the
command's leader exits, so a test that leaves a child of the pytest process behind, even a
zombie nobody ``wait()``ed for, fails the whole R0/R2 run with ``leaked-process-tree``. The
guard makes that visible per test instead of at the end of the run.
"""
import ctypes
import os
import signal
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]
PR_SET_CHILD_SUBREAPER = 36


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
        try:
            stat = Path(f"/proc/{entry}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:  # the process vanished between listdir and open
            continue
        head, _, tail = stat.rpartition(")")  # the comm field may itself contain spaces/parens
        fields = tail.split()
        if int(fields[1]) == me:
            found[(int(entry), fields[19])] = (head.partition("(")[2], fields[0])
    return found


def pytest_configure(config):
    # assay makes the command's leader a child subreaper, so a test's orphaned grandchild (a
    # holder whose parent already exited) becomes a child of this process and, once killed,
    # a zombie nobody reaps. Do the same here so a plain pytest run sees exactly what assay
    # sees (idempotent when assay already did it).
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

    New children are removed by pid only (never by name): a zombie is reaped, a live child is
    sent SIGKILL and reaped. Children that existed before the test (helpers of xdist or
    pytester) are never touched. A test marked ``allow_children`` is left alone.
    """
    before = set(direct_children())
    yield
    if request.node.get_closest_marker("allow_children") is not None:
        return
    leaked = []
    for (pid, starttime), (comm, state) in direct_children().items():
        if (pid, starttime) in before:
            continue
        if state != "Z":
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:  # it ended on its own just now
                pass
        try:
            os.waitpid(pid, os.WNOHANG if state == "Z" else 0)
        except ChildProcessError:  # somebody else reaped it in the meantime
            pass
        leaked.append(f"pid {pid} comm {comm} ({'zombie' if state == 'Z' else 'alive'})")
    if leaked:
        pytest.fail(
            "the test left child processes of the pytest process behind (killed and reaped "
            "by the guard; wait for what you start): " + "; ".join(leaked),
            pytrace=False,
        )
