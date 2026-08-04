"""Temporarily narrow cgroup limits for the duration of a profiling run, and
guarantee they come back — on normal exit, on an exception, or on a killed
process.

This is the one collector-tier module that *writes* to a live production
host rather than only reading it (DESIGN.md §4.8, §7's "never widen a limit,
write to a cgroup... from a code path the user did not ask for"). Every path
in and out has to be provably safe, so the rule here is stricter than
elsewhere in the package: refuse the *entire* request before touching
anything if it cannot be fully honoured — a half-applied batch of caps is
worse than refusing outright, because the operator has to notice it happened
before they can undo it.
"""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .access import CGROUP_ROOT, cgroup_root_is_writable
from .util import MAX, read_text


@dataclass
class CapChange:
    cgroup: str
    file: str
    old: Optional[str]     # None means "was max" (or unreadable) — see TempCaps
    new: str


class CapsError(RuntimeError):
    """Raised before anything is written — the whole request is refused."""


def _cgroup_dir(root: str, cgroup: str) -> str:
    return os.path.join(root, cgroup.strip("/"))


def _file_path(root: str, cgroup: str, file: str) -> str:
    return os.path.join(_cgroup_dir(root, cgroup), file)


def _write(path: str, value: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"{value}\n")


# ── signal handling: identical contract to damon.DamonSession ──────────────
#
# Duplicated rather than shared — see the note in damon.py; these are the two
# collector-tier modules that hold live host state open across a `with`
# block, and the helper is small enough that sharing it would only add a
# cross-import between two otherwise-unrelated files.

def _set_signal(sig: int, handler: Any) -> Any:
    return signal.signal(sig, handler)


def _install_signal_teardown(cleanup: Callable[[], None]) -> Dict[int, Any]:
    """Arm SIGINT/SIGTERM so a killed profiling run still restores every
    applied cap before the process actually dies.

    The handler runs ``cleanup``, puts back whatever handler was previously
    registered, and then raises ``SystemExit`` rather than re-sending the raw
    signal to this process. Re-sending is tempting (it preserves the exact
    "killed by signal N" wait-status a supervisor sees) but it is racy: after
    ``os.kill`` returns, the interpreter is free to resume a few more
    bytecodes of whatever was interrupted — including, worst case, another
    iteration of the apply loop in ``__enter__`` — before the re-delivered
    signal actually lands. ``SystemExit`` instead unwinds *right now*,
    through Python's own exception machinery, which is exactly what walks
    back out through every ``with``/``try-finally`` between here and the top
    of the program — including a ``DamonSession`` nested inside this
    ``TempCaps``, or vice versa, not just this one. The exit code
    (128 + signum) still follows the conventional shell/exec convention for
    a signal-terminated process. Only installable from the main thread (a
    CPython restriction) — every call site here is the top-level profiling
    loop, so that holds in practice, but a failure to install is swallowed
    rather than raised: the normal-exit and exception-path restores
    (``__exit__``) still apply regardless.
    """
    previous: Dict[int, Any] = {}

    def handler(signum: int, frame: Any) -> None:
        try:
            cleanup()
        finally:
            _set_signal(signum, previous.get(signum, signal.SIG_DFL))
        raise SystemExit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = _set_signal(sig, handler)
        except (ValueError, OSError):
            pass
    return previous


def _restore_signal_handlers(previous: Dict[int, Any]) -> None:
    for sig, prior in previous.items():
        try:
            _set_signal(sig, prior)
        except (ValueError, OSError):
            pass


class TempCaps:
    """Apply a batch of cgroup limit files for the life of a ``with`` block.

    ``changes`` is ``{cgroup_path: {file_name: new_value}}`` where
    ``cgroup_path`` is a cgroup-v2-relative path (``"/dev.slice/dev-
    background.slice"``, matching ``util.ancestors``' convention) and
    ``file_name``/``new_value`` are written exactly as given — e.g.
    ``{"memory.max": "4G"}`` or ``{"cpu.max": "500000 100000"}``. This module
    does not parse or validate cgroup-file syntax, only guarantees that
    whatever is applied is also restored.

    Only the files named here are ever touched. Nothing is widened, loosened,
    or otherwise written on the caller's behalf.
    """

    def __init__(self, changes: Dict[str, Dict[str, str]], root: str = CGROUP_ROOT):
        self.changes = changes
        self.root = root
        self.applied: List[CapChange] = []
        self._prev_handlers: Dict[int, Any] = {}

    def _refuse_unless_safe(self) -> None:
        """Validate the *entire* request before writing a single byte.

        Checking each target file's existence up front (not just the cgroup
        directories) narrows the TOCTOU window between "we said yes" and "we
        wrote something": a controller that isn't enabled on a given cgroup
        (no ``pids.max`` because the ``pids`` controller was never delegated
        there, say) is caught here with a clear message instead of surfacing
        as a raw ``OSError`` mid-batch — though even that raw failure would
        still trigger the same full rollback via ``__enter__``'s exception
        handler, this just makes the common case diagnosable without one.
        """
        if not cgroup_root_is_writable(self.root):
            raise CapsError(
                f"cgroup root {self.root!r} is not writable — refusing to apply any cap"
            )
        missing_cgroups = sorted(
            cgroup for cgroup in self.changes
            if not os.path.isdir(_cgroup_dir(self.root, cgroup))
        )
        if missing_cgroups:
            raise CapsError(
                "refusing to apply any cap: cgroup(s) not present: "
                + ", ".join(missing_cgroups)
            )
        missing_files = sorted(
            f"{cgroup}:{file}"
            for cgroup, files in self.changes.items()
            for file in files
            if not os.path.isfile(_file_path(self.root, cgroup, file))
        )
        if missing_files:
            raise CapsError(
                "refusing to apply any cap: file(s) not present: "
                + ", ".join(missing_files)
            )

    def __enter__(self) -> List[CapChange]:
        self._refuse_unless_safe()
        self._prev_handlers = _install_signal_teardown(self._restore)
        try:
            for cgroup, files in self.changes.items():
                for file, new_value in files.items():
                    self._apply_one(cgroup, file, new_value)
        except Exception:
            self._restore()
            _restore_signal_handlers(self._prev_handlers)
            raise
        return self.applied

    def _apply_one(self, cgroup: str, file: str, new_value: str) -> None:
        path = _file_path(self.root, cgroup, file)
        old_text = read_text(path)
        # None here covers two cases the same way: the file genuinely reads
        # back "max" (util's own sentinel convention), or it could not be
        # read at all (raced out from under us between the refuse-check and
        # here). Either way _restore below must write back the literal
        # string "max", never a stale number.
        old = None if old_text is None or old_text.strip() == MAX else old_text.strip()
        _write(path, new_value)
        self.applied.append(CapChange(cgroup=cgroup, file=file, old=old, new=new_value))

    def _restore(self) -> None:
        # LIFO: undo the most recent change first. Nothing today depends on
        # ordering between two cap files, but restoring as a stack rather
        # than a set costs nothing and removes the question if it ever does.
        while self.applied:
            change = self.applied.pop()
            path = _file_path(self.root, change.cgroup, change.file)
            try:
                _write(path, change.old if change.old is not None else MAX)
            except OSError:
                # A cgroup that vanished entirely (its container exited)
                # while we held it has nothing left to restore — and nothing
                # left to leak, either.
                pass

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            self._restore()
        finally:
            _restore_signal_handlers(self._prev_handlers)
