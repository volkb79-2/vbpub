"""Shared fixtures for the run-gate test suite.

RW-46a: `run_gate.SHARED_LOCK_DIR` (`run-gate.py:168`) is `/tmp` — HOST-WIDE
by design in production, because R-41's exec mutex and R-29's shared-infra
mutex both exist specifically to coordinate SEPARATE run-gate invocations on
one host. That design is correct for production and stays unchanged here.
But this test suite is not production: it is one of several *concurrent*
copies of the SAME suite this estate now runs routinely (RW-39/RW-42), and
every copy that imports this file was, before this fixture, writing its
locks to that SAME host-wide `/tmp`.

The evidence this fixture exists to prevent a repeat of: 493 stale
`run-gate-exec-*-runner.lock` DIRECTORIES were found under production /tmp,
dated back to Sep 3 — traced to `TestResourceAdmission`'s and
`TestExecModeMutex`'s own `test_unusable_lock_path_is_infra_failure_not_
traceback` tests, which each deliberately `mkdir()` a directory at the lock
path (to prove `acquire_*_lock`'s OSError-not-traceback behavior) with no
cleanup — fixed at the two call sites directly (each now `rmdir()`s in a
`finally`). This fixture closes the OTHER half: even a future test that
forgets cleanup can now only ever leave a stray entry under a THROWAWAY
per-test directory, never host /tmp — and its own teardown assertion below
turns any such leak into an immediate, loud test failure instead of a silent
directory nobody notices for nine days.
"""

import os
import sys
from pathlib import Path

import pytest

# RUN_GATE_LOCK_DIR is production's own override knob for SHARED_LOCK_DIR
# (`run-gate.py`'s `_lock_dir()`), the same override shape as the existing
# RUN_GATE_CGROUPFS_ROOT/RUN_GATE_PROC_ROOT test/namespace levers: the
# production code re-reads `os.environ.get(LOCK_DIR_ENV_VAR, ...)` on every
# call rather than caching it at import, so setting the env var here reaches
# BOTH in-process `run_gate.main()` calls AND subprocess `run_tool()`
# invocations (subprocess.run() inherits the current os.environ by default)
# without needing to separately monkeypatch the `run_gate` module object —
# which would be unsafe here anyway, since `tests/test_run_gate.py` and
# `tests/test_coverage_gate.py` each load their tool via a fresh
# `importlib.util.spec_from_file_location`/`exec_module` at THEIR OWN
# collection time; a module-attribute patch made against one loaded copy
# would not reach a different copy's own module-global lookups.
_LOCK_DIR_ENV_VAR = "RUN_GATE_LOCK_DIR"


@pytest.fixture(autouse=True)
def isolate_shared_lock_dir(tmp_path_factory, monkeypatch):
    lock_dir = tmp_path_factory.mktemp("run-gate-locks")
    monkeypatch.setenv(_LOCK_DIR_ENV_VAR, str(lock_dir))
    yield lock_dir
    # Regression guard for the root cause above, estate-wide and permanent:
    # a lock path must NEVER be a directory — every lock this file's own
    # code creates is a plain `os.open()`ed FILE (`_open_lockfile`), so a
    # directory found here, from ANY test, present or future, is corruption
    # by definition. Assert it loudly at the point it happened rather than
    # letting it accumulate silently for nine days under a shared /tmp.
    leftover_dirs = sorted(p for p in lock_dir.rglob("*") if p.is_dir())
    assert not leftover_dirs, (
        "a lock path was left as a DIRECTORY, which is never legitimate "
        "(RW-46a — this is exactly how 493 stale run-gate-exec-*-runner."
        f"lock directories accumulated under production /tmp): {leftover_dirs}"
    )


# RG-87: the session-level "no test writes into a real PATH directory" guard.
# On 2026-10-06 a test helper wrote a PASS-fabricating fake `assay` into the
# developer's real PATH[0] (~/.local/bin), replacing the user's real command.
# Shims belong in a private per-test dir PREPENDED to PATH; this guard turns
# any future regression into a loud, session-failing error.


def guarded_dirs(environ=None) -> list[Path]:
    """Every real directory a test must never write into: each PATH entry
    plus `$HOME/.local/bin` and `$HOME/.venv/bin` (even if not on PATH)."""
    environ = os.environ if environ is None else environ
    entries = [p for p in environ.get("PATH", "").split(":") if p]
    home = environ.get("HOME")
    if home:
        entries += [os.path.join(home, ".local", "bin"),
                    os.path.join(home, ".venv", "bin")]
    seen, out = set(), []
    for entry in entries:
        key = os.path.abspath(entry)
        if key not in seen:
            seen.add(key)
            out.append(Path(key))
    return out


def snapshot_dirs(dirs) -> dict:
    """{dir: None if absent else {entry name: (mode, size, mtime_ns)}}."""
    snap: dict = {}
    for directory in dirs:
        if not directory.is_dir():
            snap[str(directory)] = None
            continue
        listing = {}
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    listing[entry.name] = (st.st_mode, st.st_size,
                                           st.st_mtime_ns)
        except OSError:
            snap[str(directory)] = None
            continue
        snap[str(directory)] = listing
    return snap


def diff_snapshots(before: dict, after: dict) -> list[str]:
    problems = []
    for directory, old in before.items():
        new = after.get(directory)
        if old is None and new is not None:
            problems.append(f"{directory}: directory was CREATED")
            continue
        if old is None or new is None:
            continue
        for name in sorted(new.keys() - old.keys()):
            problems.append(f"{directory}/{name}: CREATED")
        for name in sorted(old.keys() - new.keys()):
            problems.append(f"{directory}/{name}: REMOVED")
        for name in sorted(old.keys() & new.keys()):
            if old[name] != new[name]:
                problems.append(f"{directory}/{name}: MODIFIED")
    return problems


_PATH_GUARD_BEFORE: dict = {}


def pytest_sessionstart(session):
    _PATH_GUARD_BEFORE.clear()
    _PATH_GUARD_BEFORE.update(snapshot_dirs(guarded_dirs()))


def pytest_sessionfinish(session, exitstatus):
    if not _PATH_GUARD_BEFORE:
        return
    problems = diff_snapshots(_PATH_GUARD_BEFORE,
                              snapshot_dirs(guarded_dirs()))
    if problems:
        sys.stderr.write(
            "\nRG-87 PATH-WRITE GUARD: the test session wrote into real PATH "
            "/ HOME bin directories (tests must put shims in a private "
            "per-test dir prepended to PATH):\n  "
            + "\n  ".join(problems) + "\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
