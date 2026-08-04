"""A scoped DAMON monitoring session, built on the sysfs primitives that
``scripts/damon-analysis/lib/damon_analysis.py`` already implements.

Two things shape this module (DESIGN.md §4.7, §6):

**Do not shell out to ``damo``.** The sibling venv's ``damo`` script has a
shebang naming the *build host's* interpreter path, so it is not executable
from inside this container or after the checkout moves — the ``Monitor``
class in ``damon_analysis`` depends on that binary and is therefore unusable
here. ``SysfsInterface`` and ``Classifier`` are pure sysfs I/O and pure
Python respectively; those are the parts to reuse, called directly.

**DAMON is one shared kernel facility, not a per-process resource.** There is
exactly one ``nr_kdamonds`` knob for the whole host, so acquiring a kdamond
slot and forgetting to release it leaks a live monitoring thread against
whatever the kernel happens to be watching next. ``DamonSession`` therefore
follows the same discipline as ``caps.TempCaps``: remember what was there
before, touch only the slot this session adds, and guarantee release on
normal exit, on an exception raised anywhere inside the ``with`` block, and
on SIGINT/SIGTERM — a killed profiling run must never leave a kdamond running
against production.
"""

from __future__ import annotations

import os
import signal
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# The reuse library lives in a sibling top-level script package, not a
# dependency of this one — see the module docstring. The checkout might
# simply not be present (a partial clone, a differently-laid-out host), so
# this import is guarded and nothing above this module fails to import
# because of it; callers must check available() before doing anything real.
_DAMON_ANALYSIS_LIB = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "damon-analysis", "lib")
)
_DAMO_BIN = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "damon-analysis", "venv", "bin", "damo",
    )
)

try:
    if _DAMON_ANALYSIS_LIB not in sys.path:
        sys.path.insert(0, _DAMON_ANALYSIS_LIB)
    from damon_analysis import Classifier, KDAMONDS_DIR, SysfsInterface  # type: ignore
except Exception:
    SysfsInterface = None  # type: ignore[assignment]
    Classifier = None  # type: ignore[assignment]
    KDAMONDS_DIR = "/sys/kernel/mm/damon/admin/kdamonds"


def available() -> bool:
    """True when both the reuse library imported *and* the kernel exposes
    DAMON here.

    Two independent reasons this can be False: the sibling damon-analysis
    checkout is missing or broken (``SysfsInterface`` is ``None``), or this
    kernel/container simply doesn't expose
    ``/sys/kernel/mm/damon/admin/kdamonds`` (``CONFIG_DAMON_SYSFS=n``, an
    unprivileged view, a kernel too old). Callers check this one flag rather
    than re-deriving both conditions themselves, and it never raises —
    absence is exactly the case a profiler must keep running through.
    """
    if SysfsInterface is None:
        return False
    try:
        return bool(SysfsInterface.is_available())
    except Exception:
        return False


def damo_usable() -> bool:
    """Best-effort: could the sibling venv's ``damo`` CLI actually exec here?

    Diagnostic only — ``DamonSession`` never shells out to ``damo`` (see the
    module docstring). The answer is almost always False in the environments
    this tool runs in; ``cgprofile doctor`` uses it to *explain* that, not to
    decide anything, by checking whether the script's own shebang names an
    interpreter that exists and is executable from here.
    """
    try:
        with open(_DAMO_BIN, "r", encoding="utf-8", errors="replace") as fh:
            first_line = fh.readline().strip()
    except OSError:
        return False
    if not first_line.startswith("#!"):
        return False
    rest = first_line[2:].strip()
    interpreter = rest.split(" ")[0] if rest else ""
    return bool(interpreter) and os.access(interpreter, os.X_OK)


@dataclass
class DamonTarget:
    kind: str                      # "vaddr" | "paddr"
    pid: Optional[int]
    label: str


class DamonSessionError(RuntimeError):
    """Raised when a session cannot be safely entered, or used out of turn."""


# ── signal handling: identical contract to caps.TempCaps ───────────────────
#
# Duplicated rather than shared: this and caps.py are the two collector-tier
# modules that hold a piece of live, shared host/kernel state open across a
# `with` block, and each is small and self-contained enough that a shared
# helper module would only add an import between two otherwise-unrelated
# files for fifteen lines of code.

def _set_signal(sig: int, handler: Any) -> Any:
    return signal.signal(sig, handler)


def _install_signal_teardown(cleanup: Callable[[], None]) -> Dict[int, Any]:
    """Arm SIGINT/SIGTERM so a killed profiling run still releases the
    kdamond before the process actually dies.

    The handler runs ``cleanup``, puts back whatever handler was previously
    registered, and then raises ``SystemExit`` rather than re-sending the raw
    signal to this process. Re-sending is tempting (it preserves the exact
    "killed by signal N" wait-status a supervisor sees) but it is racy: after
    ``os.kill`` returns, the interpreter is free to resume a few more
    bytecodes of whatever was interrupted — including, worst case, another
    loop iteration inside this very module — before the re-delivered signal
    actually lands. ``SystemExit`` instead unwinds *right now*, through
    Python's own exception machinery, which is exactly what walks back out
    through every ``with``/``try-finally`` between here and the top of the
    program — including a ``DamonSession`` nested inside a ``TempCaps``, or
    vice versa, not just this one. The exit code (128 + signum) still follows
    the conventional shell/exec convention for a signal-terminated process.
    ``signal.signal`` only works from the main thread (a CPython
    restriction); every call site here is the top-level profiling loop, so
    that always holds in practice, but a failure to install is swallowed
    rather than raised — losing only the signal-specific guarantee still
    leaves the normal-exit and exception-path guarantees (``__exit__``)
    intact.
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


def _read_nr_kdamonds() -> Optional[int]:
    try:
        return SysfsInterface._read_int(os.path.join(KDAMONDS_DIR, "nr_kdamonds"))
    except Exception:
        return None


class DamonSession:
    """One DAMON monitoring session, scoped to a ``with`` block.

    All targets in one session share a single DAMON *context* and therefore
    a single operations mode — mixing ``vaddr`` and ``paddr`` targets in one
    session is a caller error, not something this class resolves for you;
    ask for two sessions (two ``kdamond_idx`` values) instead.
    """

    def __init__(
        self,
        targets: List[DamonTarget],
        sample_us: int = 100_000,
        aggr_us: int = 2_000_000,
        kdamond_idx: int = 0,
    ):
        if not targets:
            raise ValueError("DamonSession needs at least one target")
        kinds = {t.kind for t in targets}
        unknown = kinds - {"vaddr", "paddr"}
        if unknown:
            raise ValueError(f"unknown DAMON target kind(s): {sorted(unknown)}")
        if len(kinds) > 1:
            raise ValueError(
                "DamonSession targets must share one operations mode (vaddr xor paddr)"
            )
        for target in targets:
            if target.kind == "vaddr" and target.pid is None:
                raise ValueError(f"vaddr target {target.label!r} needs a pid")

        self.targets = list(targets)
        self.sample_us = sample_us
        self.aggr_us = aggr_us
        self.kdamond_idx = kdamond_idx
        self.update_us = aggr_us * 20
        self._ctx_idx = 0
        self._scheme_idx = 0
        self._prev_nr_kdamonds: Optional[int] = None
        self._prev_handlers: Dict[int, Any] = {}
        self._torn_down = True   # nothing to tear down until __enter__ acquires it
        self._entered = False
        self.last_summary: Dict[str, Any] = {}

    def __enter__(self) -> "DamonSession":
        if not available():
            raise DamonSessionError(
                "DAMON is not available here (damon-analysis checkout missing, "
                "or /sys/kernel/mm/damon/admin/kdamonds not present) — check "
                "damon.available() before constructing a DamonSession"
            )
        self._prev_handlers = _install_signal_teardown(self._teardown)
        self._torn_down = False
        try:
            self._prev_nr_kdamonds = _read_nr_kdamonds()
            SysfsInterface.create_kdamond(self.kdamond_idx)
            SysfsInterface.create_context(self.kdamond_idx, self._ctx_idx)
            SysfsInterface.set_operations(self.kdamond_idx, self._ctx_idx, self.targets[0].kind)
            SysfsInterface.set_intervals(
                self.kdamond_idx, self._ctx_idx, self.sample_us, self.aggr_us, self.update_us
            )
            for index, target in enumerate(self.targets):
                SysfsInterface.create_target(self.kdamond_idx, self._ctx_idx, index)
                if target.kind == "vaddr":
                    SysfsInterface.set_pid_target(
                        self.kdamond_idx, self._ctx_idx, index, target.pid
                    )
            SysfsInterface.create_scheme(self.kdamond_idx, self._ctx_idx, self._scheme_idx)
            # "stat" only observes — it never migrates or reclaims a region —
            # because a profiler must not become the thing perturbing the
            # workload it exists to measure. The access pattern is wide open
            # on every axis so every region is tried and reported.
            SysfsInterface.set_scheme_action(
                self.kdamond_idx, self._ctx_idx, self._scheme_idx, "stat"
            )
            SysfsInterface.set_scheme_access_pattern(
                self.kdamond_idx, self._ctx_idx, self._scheme_idx,
                0, 2 ** 63 - 1, 0, 2 ** 32 - 1, 0, 2 ** 32 - 1,
            )
            SysfsInterface.kdamond_commit(self.kdamond_idx)
            SysfsInterface.kdamond_on(self.kdamond_idx)
        except Exception:
            self._teardown()
            _restore_signal_handlers(self._prev_handlers)
            raise
        self._entered = True
        return self

    def collect(self) -> List[Dict]:
        """Classified regions for right now: each region's ``nr_accesses``/
        ``age`` turned into a hot/warm/cold/idle class and a temperature
        score by the same ``Classifier`` damon-analysis's own tools use. The
        rollup by class (count/bytes) is left on ``self.last_summary`` for
        the caller to fold into the ``damon.jsonl`` record alongside the
        region list — ``collect`` itself stays a flat ``List[Dict]`` so it
        composes directly with ``RunDir.append``'s one-record-at-a-time shape
        for the "regions" payload.
        """
        if not self._entered:
            raise DamonSessionError("collect() called outside the session's `with` block")
        SysfsInterface.kdamond_update_tried_regions(self.kdamond_idx)
        regions = SysfsInterface.read_tried_regions(self.kdamond_idx, self._ctx_idx, self._scheme_idx)
        classifier = Classifier()
        classified = classifier.classify_regions(regions, self.sample_us, self.aggr_us)
        self.last_summary = classifier.summary(classified)
        return classified

    def _teardown(self) -> None:
        """Idempotent by design: called from ``__exit__``, from the exception
        path inside ``__enter__``, and from a signal handler — in any order,
        any combination, any number of times, including *reentrantly* (a
        SIGTERM landing while this very call is mid-flight, because the
        signal handler's cleanup callback is this same bound method).

        ``self._torn_down`` is set only once the real work below has been
        attempted, not at the top — setting it first would make a reentrant
        call (the signal handler firing while ``kdamond_off`` is in flight)
        see "already done" and return immediately, permanently skipping the
        ``nr_kdamonds`` shrink and leaking a phantom (harmless but
        undercounted) kdamond slot. With the flag set at the end, the
        reentrant call — which runs to completion synchronously before the
        interrupted outer call ever gets to resume — is the one that
        actually finishes the job; the abandoned outer call simply never
        reaches the flag-set, which is fine because there is nothing left
        for it to do.
        """
        if self._torn_down:
            return
        try:
            SysfsInterface.kdamond_off(self.kdamond_idx)
        except Exception:
            pass
        prev = self._prev_nr_kdamonds
        if prev is not None:
            try:
                current = _read_nr_kdamonds()
                if current is not None and current > prev:
                    # Shrink back to exactly what was there before this
                    # session — verified on this host that writing
                    # nr_kdamonds back down tears the higher-indexed kdamond
                    # dir(s) down. Restoring the prior count rather than
                    # unconditionally writing 0 means a kdamond some other
                    # process already owned before we started is left
                    # running, never destroyed out from under it.
                    SysfsInterface._write_int(os.path.join(KDAMONDS_DIR, "nr_kdamonds"), prev)
            except Exception:
                pass
        self._torn_down = True

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            self._teardown()
        finally:
            _restore_signal_handlers(self._prev_handlers)
