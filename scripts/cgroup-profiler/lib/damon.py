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
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import util

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


class HostWriteError(RuntimeError):
    """Raised when this module is about to write outside the one host
    location it is allowed to mutate: the DAMON admin sysfs root
    (RG-55 C4's ``WRITABLE_ROOTS`` boundary — the other half lives in
    ``lib.serve``, guarding the sessions directory).

    Every real mutation this module performs — creating a kdamond, a
    context, a target, a scheme, turning a kdamond on/off — is delegated to
    ``SysfsInterface`` (a sibling library this module does not own, and does
    not re-guard: it never writes anywhere but under ``KDAMONDS_DIR`` by
    construction). The one write this module issues directly is
    ``nr_kdamonds`` itself, in :func:`_write_nr_kdamonds` below — and that is
    the write this guard exists for.
    """


def _write_nr_kdamonds(value: int) -> None:
    """The one raw sysfs write this module performs directly, checked
    against the DAMON admin root before it happens.

    ``admin_root`` is derived from ``KDAMONDS_DIR`` itself (its parent
    directory) rather than hardcoded, so a test that monkeypatches
    ``damon.KDAMONDS_DIR`` to a fake tree is guarded against exactly that
    fake tree, and the real daemon is guarded against the real
    ``/sys/kernel/mm/damon/admin``. ``os.path.realpath`` on both sides means
    a symlink planted at (or above) ``KDAMONDS_DIR`` cannot smuggle this
    write outside the intended root.
    """
    path = os.path.join(KDAMONDS_DIR, "nr_kdamonds")
    admin_root = os.path.realpath(os.path.dirname(KDAMONDS_DIR))
    if not util.realpath_is_within(path, admin_root, allow_root=False):
        raise HostWriteError(
            f"refusing to write outside the DAMON admin root ({admin_root!r}): {path!r}"
        )
    SysfsInterface._write_int(path, value)


class KdamondPool:
    """Owns ``nr_kdamonds`` across every concurrent :class:`DamonSession`
    (RG-55 C3 — DAMON multiplexing).

    A bare ``DamonSession`` captures "what ``nr_kdamonds`` was before me"
    once, at its own ``__enter__``, and restores exactly that at its own
    ``__exit__`` — correct for exactly one session at a time, and provably
    wrong for two: a second session's acquire grows ``nr_kdamonds`` further,
    so the first session's "before me" snapshot is now stale, and its
    teardown writes ``nr_kdamonds`` back down past the second session's
    still-live, higher index, tearing that kdamond down from underneath it
    (writing ``nr_kdamonds`` down is documented, both here and in
    ``damon_analysis``, as tearing down every kdamond dir above the new
    count). A daemon that runs more than one profiling session at a time
    (``lib.serve``) must therefore route every session through ONE shared
    pool rather than let each session manage the counter itself.

    The kernel refuses to resize ``nr_kdamonds`` while any kdamond is
    running. Consequently, a pool reserves its whole configured capacity
    while every owned kdamond is still off, before the first session starts.
    A running session only claims one of those pre-created indices; it never
    grows the shared counter. If the kernel cannot reserve the requested
    capacity, the pool uses any slots it did successfully reserve, and a
    later session degrades DAMON rather than attempting a live resize.

    The pool's initial count is an ownership boundary: indices below it were
    already present and are foreign, so they are never configured, stopped,
    or removed here. A released index is reusable only after sysfs confirms
    its state is ``off``. If stopping or confirming a slot fails, it is
    quarantined: the pool neither reuses it nor shrinks the count across it.
    The next acquisition and daemon shutdown retry cleanup. An unreadable
    baseline is a refusal, not permission to invent index zero.
    """

    def __init__(self, capacity: int = 1) -> None:
        if capacity < 0:
            raise ValueError("DAMON pool capacity must be nonnegative")
        self.capacity = capacity
        self._live: set = set()
        self._owned: set = set()
        self._free: set = set()
        self._quarantined: set = set()
        self._baseline: Optional[int] = None
        self._foreign_growth = False
        self._lock = threading.RLock()

    def _clear_if_restored(self) -> None:
        self._baseline = None
        self._owned.clear()
        self._free.clear()
        self._quarantined.clear()
        self._foreign_growth = False

    def _confirm_off(self, idx: int) -> bool:
        try:
            return SysfsInterface.kdamond_state(idx) == "off"
        except Exception:
            return False

    def _stop_and_confirm_off(self, idx: int) -> bool:
        try:
            if SysfsInterface.kdamond_state(idx) != "off":
                SysfsInterface.kdamond_off(idx)
            return self._confirm_off(idx)
        except Exception:
            return False

    def _recover_quarantined(self) -> None:
        for idx in sorted(self._quarantined):
            if self._stop_and_confirm_off(idx):
                self._quarantined.remove(idx)
                self._free.add(idx)

    def _reconcile_live_indices(self) -> None:
        """Quarantine pool claims that an external shrink removed while off."""
        current = _read_nr_kdamonds()
        if current is None:
            return
        for idx in tuple(self._live):
            if current <= idx:
                self._live.remove(idx)
                self._quarantined.add(idx)

    def _all_kdamonds_off(self, count: int) -> bool:
        """Prove the kernel-wide resize precondition, including foreign slots."""
        try:
            return all(
                SysfsInterface.kdamond_state(idx) == "off"
                for idx in range(count)
            )
        except Exception:
            return False

    def _reserve_capacity(self) -> None:
        if self._baseline is None:
            baseline = _read_nr_kdamonds()
            if baseline is None or baseline < 0:
                raise DamonSessionError(
                    "cannot determine nr_kdamonds baseline; refusing to claim a slot"
                )
            self._baseline = baseline

        if self._live or self._quarantined:
            raise DamonSessionError(
                "cannot grow the DAMON pool while an owned kdamond may be running"
            )

        while len(self._owned) < self.capacity:
            baseline = self._baseline
            current = _read_nr_kdamonds()
            expected = max(self._owned, default=baseline - 1) + 1
            if current is None or current < expected:
                raise DamonSessionError(
                    f"cannot prove the next pool-owned kdamond slot from nr_kdamonds={current!r}"
                )
            idx = expected
            if current > expected:
                # Preserve intervening indices created by another owner.
                self._foreign_growth = True
                idx = current
            try:
                SysfsInterface.create_kdamond(idx)
            except Exception as exc:
                after_failure = _read_nr_kdamonds()
                if after_failure is None or after_failure > current:
                    # The write failed, but the counter's final state is
                    # ambiguous. Do not claim the possibly-created index or
                    # later shrink across it; a fresh read may still show the
                    # original baseline and clear this conservative marker.
                    self._foreign_growth = True
                if self._free:
                    # A partial reservation remains usable, but its actual
                    # capacity is lower; never try to grow it while active.
                    break
                if not self._owned and not self._foreign_growth:
                    self._baseline = None
                raise DamonSessionError(
                    f"cannot reserve DAMON pool slot {idx}: {type(exc).__name__}: {exc}"
                ) from exc

            after = _read_nr_kdamonds()
            if after is None or after <= idx:
                # create_kdamond returned, so retain ownership conservatively
                # even though the counter read cannot prove the slot is
                # usable. Shutdown retries state=off but never reuses or
                # shrinks across this ambiguous slot.
                self._owned.add(idx)
                self._quarantined.add(idx)
                raise DamonSessionError(
                    f"cannot prove reserved DAMON pool slot {idx} exists"
                )
            if after > idx + 1:
                self._foreign_growth = True
            self._owned.add(idx)
            self._free.add(idx)

    def acquire(self) -> int:
        """Claim a verified-off pool-owned index without resizing live sysfs."""
        with self._lock:
            if self._baseline is None:
                baseline = _read_nr_kdamonds()
                if baseline is None or baseline < 0:
                    raise DamonSessionError(
                        "cannot determine nr_kdamonds baseline; refusing to claim a slot"
                    )
                self._baseline = baseline

            self._reconcile_live_indices()
            self._recover_quarantined()
            # Reserve the configured concurrency before any owned monitor is
            # turned on. A partial reservation can be used, but capacity is
            # immutable until every owned monitor is confirmed off again.
            if len(self._owned) < self.capacity and not self._free:
                self._reserve_capacity()

            for idx in sorted(self._free):
                current = _read_nr_kdamonds()
                if current is None or current <= idx:
                    self._free.remove(idx)
                    self._quarantined.add(idx)
                    continue
                expected_end = max(self._owned, default=self._baseline - 1) + 1
                if current > expected_end:
                    self._foreign_growth = True
                if not self._confirm_off(idx):
                    self._free.remove(idx)
                    self._quarantined.add(idx)
                    continue
                self._free.remove(idx)
                self._live.add(idx)
                return idx

            raise DamonSessionError(
                f"no verified-off DAMON pool slot available (capacity={self.capacity}, "
                f"live={sorted(self._live)}, quarantined={sorted(self._quarantined)})"
            )

    def _restore_baseline(self) -> bool:
        if self._live or self._quarantined:
            return False
        baseline = self._baseline
        if baseline is None:
            return True
        if not self._owned:
            self._clear_if_restored()
            return True
        try:
            current = _read_nr_kdamonds()
            expected_end = max(self._owned) + 1
            if current == baseline:
                self._clear_if_restored()
                return True
            if (
                self._foreign_growth
                or current != expected_end
                or current <= baseline
            ):
                return False
            # The kernel rejects *any* nr_kdamonds resize while any monitor
            # is running, not only when one of this pool's indices is live.
            # Avoid a predictable failed write when a foreign monitor is on;
            # if state changes after this read, the kernel remains the final
            # guard and ownership is retained on write failure.
            if not self._all_kdamonds_off(current):
                return False
            _write_nr_kdamonds(baseline)
            if _read_nr_kdamonds() != baseline:
                return False
        except Exception:
            # Retain the free-slot ownership record so a later acquire or
            # close can retry; forgetting it could abandon active state.
            return False
        self._clear_if_restored()
        return True

    def release(self, idx: int) -> bool:
        """Return an index only after sysfs confirms it is off.

        Never raises: teardown runs on normal exit, exceptions and signals.
        A live or unreadable state is quarantined instead of freed, reused,
        or crossed by a ``nr_kdamonds`` shrink.
        """
        with self._lock:
            if idx not in self._live:
                return idx in self._free
            self._live.remove(idx)
            if not self._confirm_off(idx):
                self._quarantined.add(idx)
                return False
            self._quarantined.discard(idx)
            self._free.add(idx)
            if not self._live and not self._quarantined:
                self._restore_baseline()
            return True

    def close(self) -> bool:
        """Retry stopping every owned slot and restore the original count.

        Called after the server has finalized its sessions. A failure is
        reported to the caller and leaves ownership recorded; it never
        shrinks the registry across a monitor whose stopped state is unknown.
        """
        with self._lock:
            stopped = True
            for idx in sorted(self._owned):
                if self._stop_and_confirm_off(idx):
                    self._live.discard(idx)
                    self._quarantined.discard(idx)
                    self._free.add(idx)
                else:
                    self._live.discard(idx)
                    self._free.discard(idx)
                    self._quarantined.add(idx)
                    stopped = False
            if not stopped:
                return False
            return self._restore_baseline()

    @property
    def live_indices(self) -> frozenset:
        return frozenset(self._live)

    @property
    def quarantined_indices(self) -> frozenset:
        return frozenset(self._quarantined)


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
        pool: Optional["KdamondPool"] = None,
        hot_rate_pct: float = 50.0,
        warm_rate_pct: float = 5.0,
        cold_age_sec: float = 30.0,
        idle_age_sec: float = 120.0,
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
        self.pool = pool
        self.update_us = aggr_us * 20
        self._ctx_idx = 0
        self._scheme_idx = 0
        self._prev_nr_kdamonds: Optional[int] = None
        self._acquired_from_pool = False
        self._owns_kdamond = False
        self._prev_handlers: Dict[int, Any] = {}
        self._torn_down = True   # nothing to tear down until __enter__ acquires it
        self._entered = False
        self._cleanup_confirmed = True
        self.last_summary: Dict[str, Any] = {}
        self._hot_rate_pct = hot_rate_pct
        self._warm_rate_pct = warm_rate_pct
        self._cold_age_sec = cold_age_sec
        self._idle_age_sec = idle_age_sec
        self._classifier: Any = None

    @property
    def thresholds(self) -> Dict[str, float]:
        """Contract §3 ``damon.thresholds`` — this session's classifier
        cutoffs, in the units the summary reports them (seconds, not the
        microseconds ``Classifier`` itself takes internally)."""
        return {
            "hot_rate_pct": self._hot_rate_pct,
            "warm_rate_pct": self._warm_rate_pct,
            "cold_age_s": self._cold_age_sec,
            "idle_age_s": self._idle_age_sec,
        }

    @property
    def last_class_bytes(self) -> Dict[str, int]:
        """The last :meth:`collect` rollup reshaped to the flat
        ``{"hot": bytes, "warm": bytes, "cold": bytes, "idle": bytes}`` dict
        ``SummaryAccumulator.add_sample(damon=...)`` consumes per aggregation
        interval (contract §3; see ``lib/summary.py``'s module docstring).
        Zero, not missing, for a class with no regions yet — a session that
        has collected at least once always has all four keys."""
        return {
            cls: self.last_summary.get(cls, {}).get("bytes", 0)
            for cls in ("hot", "warm", "cold", "idle")
        }

    def __enter__(self) -> "DamonSession":
        if not available():
            raise DamonSessionError(
                "DAMON is not available here (damon-analysis checkout missing, "
                "or /sys/kernel/mm/damon/admin/kdamonds not present) — check "
                "damon.available() before constructing a DamonSession"
            )
        self._prev_handlers = _install_signal_teardown(self._teardown)
        self._torn_down = False
        self._classifier = Classifier(
            hot_access_rate_pct=self._hot_rate_pct,
            warm_access_rate_pct=self._warm_rate_pct,
            cold_age_sec=self._cold_age_sec,
            idle_age_sec=self._idle_age_sec,
        )
        try:
            if self.pool is not None:
                self.kdamond_idx = self.pool.acquire()
                self._acquired_from_pool = True
                self._owns_kdamond = True
                self._cleanup_confirmed = False
            else:
                self._prev_nr_kdamonds = _read_nr_kdamonds()
                if self._prev_nr_kdamonds is None:
                    raise DamonSessionError(
                        "cannot determine nr_kdamonds baseline; refusing to claim a slot"
                    )
                if self.kdamond_idx != self._prev_nr_kdamonds:
                    raise DamonSessionError(
                        f"refusing to claim foreign kdamond slot {self.kdamond_idx}; "
                        f"fresh slot is {self._prev_nr_kdamonds}"
                    )
                SysfsInterface.create_kdamond(self.kdamond_idx)
                self._owns_kdamond = True
                self._cleanup_confirmed = False
            SysfsInterface.create_context(self.kdamond_idx, self._ctx_idx)
            SysfsInterface.set_operations(self.kdamond_idx, self._ctx_idx, self.targets[0].kind)
            SysfsInterface.set_intervals(
                self.kdamond_idx, self._ctx_idx, self.sample_us, self.aggr_us, self.update_us
            )
            # `create_target()` is deliberately grow-only. A pooled index may
            # have been used by a prior session, so rebuild this session's
            # exact sysfs input array before writing any target data.
            SysfsInterface.set_nr_targets(
                self.kdamond_idx, self._ctx_idx, len(self.targets)
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
            # `commit` updates a running kdamond's existing context; it is
            # invalid before first start and returns EINVAL from the kernel.
            # Initial sysfs inputs are consumed by `on` itself.
            SysfsInterface.kdamond_on(self.kdamond_idx)
        except Exception as exc:
            self._teardown()
            _restore_signal_handlers(self._prev_handlers)
            if isinstance(exc, DamonSessionError):
                raise
            # `SysfsInterface` writes or kdamond startup can raise plain
            # `OSError`. A bare `raise` here left
            # that as an untranslated OSError, which `_create_session_locked`
            # 's own `except damon_mod.DamonSessionError` never catches —
            # it propagated all the way out of the socket dispatch loop and
            # killed the WHOLE daemon process (every other live session
            # with it), not just this one `start` call. The contract's own
            # promise (§2.2: "an unavailable DAMON never fails start") only
            # holds if every failure mode reaching this method surfaces as
            # `DamonSessionError` — wrap whatever else comes through
            # `SysfsInterface`, never let a foreign exception type escape.
            raise DamonSessionError(f"{type(exc).__name__}: {exc}") from exc
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
        classified = self._classifier.classify_regions(regions, self.sample_us, self.aggr_us)
        self.last_summary = self._classifier.summary(classified)
        return classified

    def recommit_targets(self, pids: List[int]) -> None:
        """Re-point this session's vaddr targets at a newly-discovered pid
        set without tearing the kdamond down, for a subtree
        (``lib.subtree.SubtreeResolver``) whose membership changes mid-session.

        A no-op when ``pids`` is empty: an empty discovery can be a transient
        gap in a fast-moving subtree (for example, a parent exits between
        fork and discovery), and stopping monitoring for that observation
        would discard a still-valid session. For a non-empty changed set,
        rebuild the exact staged target array before committing. The count
        write recreates the input directories, so both a shrinking subtree
        and a reused pooled index stop carrying departed PIDs forward.
        """
        if not self._entered:
            raise DamonSessionError(
                "recommit_targets() called outside the session's `with` block"
            )
        if self.targets[0].kind != "vaddr":
            raise DamonSessionError("recommit_targets() only applies to vaddr sessions")
        if not pids:
            return
        new_pids = sorted(set(pids))
        current_pids = [target.pid for target in self.targets]
        if len(new_pids) == len(current_pids) and set(new_pids) == set(current_pids):
            return

        # Recreate exactly the staged array before committing. The kernel
        # updates targets from this source list and removes live targets that
        # have no corresponding source entry; grow-only setup would keep
        # monitoring PIDs which left the lane's subtree.
        SysfsInterface.set_nr_targets(
            self.kdamond_idx, self._ctx_idx, len(new_pids)
        )
        for index, pid in enumerate(new_pids):
            SysfsInterface.create_target(self.kdamond_idx, self._ctx_idx, index)
            SysfsInterface.set_pid_target(self.kdamond_idx, self._ctx_idx, index, pid)
        SysfsInterface.kdamond_commit(self.kdamond_idx)
        self.targets = [
            DamonTarget(kind="vaddr", pid=pid, label=str(pid)) for pid in new_pids
        ]

    def _teardown(self) -> bool:
        """Stop this owned monitor and release its index only when proven off.

        A state-write error is ambiguous, so read the state back. Pooled slots
        whose state is still on or unreadable are quarantined rather than
        reused or crossed by a counter shrink. The teardown flag is set only
        after cleanup was attempted so a reentrant signal-handler call can
        finish work interrupted by the outer call.
        """
        if self._torn_down:
            return self._cleanup_confirmed
        off_confirmed = not self._owns_kdamond
        if self._owns_kdamond:
            try:
                SysfsInterface.kdamond_off(self.kdamond_idx)
            except Exception:
                pass
            try:
                off_confirmed = SysfsInterface.kdamond_state(self.kdamond_idx) == "off"
            except Exception:
                off_confirmed = False
        if self.pool is not None:
            # acquire() can fail before an index is claimed; only release the
            # exact index this session actually acquired.
            if self._acquired_from_pool:
                off_confirmed = self.pool.release(self.kdamond_idx)
        elif self._owns_kdamond and off_confirmed:
            prev = self._prev_nr_kdamonds
            if prev is not None:
                try:
                    current = _read_nr_kdamonds()
                    if current == prev + 1:
                        _write_nr_kdamonds(prev)
                except Exception:
                    pass
        self._cleanup_confirmed = off_confirmed
        self._torn_down = True
        return off_confirmed

    @property
    def cleanup_confirmed(self) -> bool:
        """Whether teardown verified that this session's kdamond is off."""
        return self._cleanup_confirmed

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            self._teardown()
        finally:
            _restore_signal_handlers(self._prev_handlers)
