import ctypes
import os
from pathlib import Path

import pytest

from iobench import testing


def _set_child_subreaper(enabled):
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if libc.prctl(36, int(enabled), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return bool(previous.value)


def _direct_child_pids(pid):
    children = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        raw = children.read_text(encoding="ascii")
    except OSError as exc:
        raise AssertionError(f"cannot inspect pytest child processes at {children}: {exc}") from exc
    return [int(value) for value in raw.split()]


def _child_description(pid):
    try:
        name = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
        raw_stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        state = raw_stat.rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return f"pid={pid} (exited during inspection)"
    return f"pid={pid} comm={name!r} state={state}"


@pytest.fixture(scope="session", autouse=True)
def session_process_tree_is_empty():
    """Keep orphaned test children adoptable and reject any left at teardown."""
    previous_subreaper = _set_child_subreaper(True)
    try:
        yield
        children = _direct_child_pids(os.getpid())
        if children:
            details = ", ".join(_child_description(pid) for pid in children)
            pytest.fail(f"test session left unreaped child process(es): {details}")
    finally:
        _set_child_subreaper(previous_subreaper)


@pytest.fixture
def host():
    return testing.standard_host()
