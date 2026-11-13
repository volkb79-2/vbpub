import ctypes
import os
import signal
import warnings
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
        # kernels without CONFIG_PROC_CHILDREN or a restricted /proc: the check cannot run; say so, do not error the session
        warnings.warn(f"cannot inspect pytest child processes at {children}: {exc}; the unreaped-child check is skipped")
        return []
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


class TimeLimitExceeded(BaseException):
    """A test used more CPU time than its limit (a non-terminating loop); BaseException so a broad ``except Exception`` cannot swallow it."""


# The limit counts CPU seconds of this process (ITIMER_PROF: user + system), never wall-clock seconds, so host load and
# scheduler contention cannot fail a slow-but-correct test (a functional verdict must not depend on load).  A busy-looping
# mutant burns CPU and is still stopped.  Chosen from a measured run (2026-11-04, whole suite, 946 tests, per-test
# time.process_time): the most CPU any single test used was 9.78 s (test_sta_05_status_collapse_audit; the next is 2.84 s;
# tests that sleep or wait on subprocesses use almost none), so 100 s keeps 10x headroom over the slowest legitimate test.
TEST_TIME_LIMIT_S = 100.0


@pytest.fixture(autouse=True)
def test_time_limit(request):
    """IOB-R2-01: a mutant (or a bug) that makes a loop non-terminating must FAIL the test fast instead of running into the
    mutation campaign's whole per-candidate budget.  The limit is CPU time of the test process (ITIMER_PROF / SIGPROF), so it
    does not depend on host load.  The timer re-fires every second of CPU so a handler that is swallowed cannot rescue the
    loop; ``@pytest.mark.time_limit(cpu_seconds)`` overrides the default for one test."""
    marker = request.node.get_closest_marker("time_limit")
    limit = float(marker.args[0]) if marker else TEST_TIME_LIMIT_S

    def expired(signum, frame):
        raise TimeLimitExceeded(f"{request.node.nodeid} used more than {limit:g} s of CPU time (a non-terminating loop?)")

    previous = signal.signal(signal.SIGPROF, expired)
    signal.setitimer(signal.ITIMER_PROF, limit, 1.0)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_PROF, 0)
        signal.signal(signal.SIGPROF, previous)


# R2 calibrate.py:202 (``while remaining > 0`` -> ``>= 0``): the idle wait then sleeps zero seconds forever.  Almost every
# calibration run reaches ``remaining == 0.0`` exactly, and module-scoped fixtures run outside the per-test CPU limit
# above, so without this guard the mutant hangs the whole suite until the campaign budget (BUDGET_EXCEEDED) instead of
# failing tests.  A fake sleep that cannot advance the fake clock can never end a wait loop; refusing it makes every such
# loop fail at its first iteration, independent of host load.  Applied at import so fixtures of every scope see it.
_fake_sleep = testing.FakeHost.sleep


def _advancing_fake_sleep(self, seconds):
    if seconds <= 0:
        raise AssertionError(f"FakeHost.sleep({seconds!r}): a non-advancing sleep cannot end a wait loop")
    return _fake_sleep(self, seconds)


testing.FakeHost.sleep = _advancing_fake_sleep


@pytest.fixture
def host():
    return testing.standard_host()
