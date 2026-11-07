"""The session server — RG55-INTERFACE-CONTRACT.md §1/§2/§5, RG-55 C4.

``SessionServer`` listens on a Unix socket (JSON-lines request/response, one
request per connection) and runs one sampling thread per live profiling
session. Each thread drives :class:`lib.sampler.Sampler` at a FIXED cadence
(hot == idle == the session's own ``interval`` — the adaptive back-off
DESIGN.md §4.4 describes for ``cgprofile run`` is deliberately
turned off here: the contract's own computation rules (§7) assume samples
land every ``interval_seconds`` with no gaps, which a caller reading
``memory.current`` for a p90/median needs to be true), samples the target
cgroup, its slice, and the host, and feeds :class:`lib.summary.SummaryAccumulator`
and :class:`lib.store.RunDir` so that ``ctl stop`` can answer within the
contract's 30 s budget without ever recomputing from the on-disk series
(§1.5 — each sample updates the accumulator; ``finalize()`` builds the
fixed-size response and performs only a fixed number of O(log n) percentile
lookups).

**Two safety properties this module owns, both restated from the handoff:**

* ``serve`` never imports :class:`lib.caps.TempCaps` and never accepts
  ``--cap`` (that flag simply does not exist on the ``serve`` CLI parser —
  see ``cgprofile.py``). The daemon reads; it does not widen or narrow a
  cgroup limit.
* Every raw filesystem write this module performs directly (as opposed to
  a write delegated to the already-safe :class:`lib.store.RunDir`, which is
  confined to the sessions directory by construction — it is only ever
  handed that one base) is checked against :data:`WRITABLE_ROOTS`:
  the sessions directory and the DAMON admin sysfs root. The DAMON half of
  that boundary lives in :mod:`lib.damon` (:func:`lib.damon._write_nr_kdamonds`)
  — this module's own half is :meth:`SessionServer._guard_path`, used before
  every ``os.makedirs``/``shutil.rmtree``/report write this class performs.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

from . import access, daemon_config, damon as damon_mod, events as events_mod, metrics, sampler as sampler_mod
from . import store, subtree, summary, targets as targets_mod, util
from . import (
    ancestor_events as ancestor_events_mod,
    limits as limits_mod,
    liveness as liveness_mod,
    machine as machine_mod,
    observer_windows as observer_windows_mod,
    placement as placement_mod,
    resource_observer as resource_observer_mod,
)
from .version import installed_version, runtime_version

CONTRACT_VERSION = 1
#: Control-socket contracts a 2.x daemon serves (PROTOCOL 10.1). A response
#: is stamped with the contract number of the request it answers.
CONTRACTS = (1, 2)
CONTRACT_2_VERBS = frozenset({"export", "config", "config-set", "config-reset"})
RESOURCE_OBSERVER_CONTRACT_VERSION = (
    resource_observer_mod.RESOURCE_OBSERVER_CONTRACT_VERSION
)
# Contract 3 adds the measurement-window verbs next to contract 2's
# `resource-limits`; `RESOURCE_OBSERVER_CONTRACT_VERSION` stays 2 for 1.x so a
# contract-2 client sees no difference.
RESOURCE_OBSERVER_CONTRACTS = (2, 3)
CGPROFILE_VERSION = runtime_version(installed_version())

DEFAULT_SOCKET_PATH = "/run/cgprofile/control/cgprofile.sock"
DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH = "/run/cgprofile/observer/cgprofile.sock"
DEFAULT_SESSIONS_DIR = "/var/lib/cgprofile/sessions"
DEFAULT_DAEMON_NAME = "cgprofile-host-daemon"
DEFAULT_MAX_SESSIONS = 16
# RG-55 C5 (contract §8.5, D-29): the gates slice is mdt's (dev-gates.slice
# under dev.slice) -- its name is the one thing about it this daemon must be
# told (`serve --gates-slice`), since C8's placement leaves live under it.
# The daemon's OWN slice is never configurable: it ships its unit with this
# EXACT name (infra/cgprofile.slice) and the compose template authors
# `cgroup_parent: cgprofile.slice` to match, so there is nothing to resolve.
DEFAULT_GATES_SLICE_NAME = "dev-gates.slice"
DAEMON_SLICE_NAME = "cgprofile.slice"
DEFAULT_KEEP_SESSIONS = 200
DEFAULT_KEEP_DAYS = 14
DEFAULT_INTERVAL = 1.0
DISCOVERY_INTERVAL_SECONDS = 2.0
REQUEST_LINE_TIMEOUT_SECONDS = 25.0
MAX_REQUEST_LINE_BYTES = 1024 * 1024
MAX_RESOURCE_OBSERVER_REQUEST_LINE_BYTES = 4096

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
_SESSION_ID_TS_FMT = "%Y%m%dT%H%M%SZ"
_SESSION_ID_RE = re.compile(r"^s-\d{8}T\d{6}Z-[0-9a-f]{4}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# ── contract §8.1: the socket carrier ───────────────────────────────────
# The ONLY top-level keys a request line may carry. Anything else is a
# `bad-argument` (see `_dispatch`) — this is what makes "one shape, no
# compatibility branch" checkable rather than a claim.
_WIRE_KEYS = frozenset({"verb", "args", "contract"})
# ── contract §8.2: the ONE streaming verb ───────────────────────────────
# `watch` writes one object per line until the session ends, which is the
# documented exception to §8.1's "one request per connection, one response,
# then close" (see `_handle_connection` and `docs/PROTOCOL.md` §2). It never
# goes through `_dispatch` — asked there (a caller that cannot stream) it is
# §8.8's `not-streaming`.
#
# Contract 2 adds `export` (PROTOCOL 10.3), the second streaming verb. It has
# its own slot class and thread (`_export_connection`) and exists at
# contract 2 only.
STREAMING_VERBS = frozenset({"watch", "export"})
# Asserted at every start on the directory the socket lives in and on the
# socket itself (§8.1 "belt and braces to the host's tmpfiles.d entry",
# RW-37: the host's own source of truth is mdt host-setup's
# `mdt-cgprofile.conf`, NOT this daemon — the daemon only re-asserts).
SOCKET_MODE = 0o660
# The uid allowlist (§8.1) is the `allow_uids` setting (cgprofiled.toml, runtime
# `config-set`). Empty = no uid restriction beyond the dedicated socket group's
# permissions (D-30).


def _placement_block(sess: "_Session") -> Optional[Dict[str, Any]]:
    """§8.3's `placement`, or `None` for a session that never asked to be
    placed. One function because five documents carry the identical block
    (`start`, `status`, `stop`'s Summary, every `watch` reading, §8.7)."""
    return sess.placement.block() if sess.placement is not None else None


def _expected_duration_seconds(meta: Dict[str, Any]) -> Optional[float]:
    """``meta.expected.duration_s`` when the consumer declared one (§2.2) —
    the input to `--ceiling auto` (§8.4: `3 x meta.expected.duration_s` when
    known, else NO ceiling). `meta.expected` is explicitly nullable in the
    contract's own fixture, and every other shape a consumer might send
    (a string, a negative number) reads the same way as absent: unknown, so
    no ceiling. A guessed ceiling kills real work."""
    expected = meta.get("expected")
    if not isinstance(expected, dict):
        return None
    value = expected.get("duration_s")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


#: How long ``stop`` waits for a session's sampling worker to exit before it
#: reports the stop as incomplete (Sol B2). Module level so tests can shorten it.
STOP_JOIN_SECONDS = 10.0

#: A worker that is still alive after this many consecutive ``stop`` attempts
#: is treated as wedged: the next attempt finalizes the session anyway
#: (bounded escalation, ``stop_forced``), because a permanently wedged worker
#: must not hold placement and DAMON resources forever.
STOP_FORCE_AFTER_ATTEMPTS = 3
STOP_FORCED_REASON = "sampler-still-running"

#: CP-33: bounded admission on the control socket (the numbers CP-24 D-33
#: reserves). One thread serves each accepted connection; these caps keep a
#: slow or looping client from growing the thread count without bound, and a
#: connection over a cap is answered with the additive `busy` error.
MAX_REQUEST_READERS = 32
MAX_CONTROL_HANDLERS = 16
MAX_WATCH_STREAMS = 32
#: `export` streams (PROTOCOL 10.3): their own slot class, beyond which `busy`.
MAX_EXPORTS = 4
DEFAULT_EXPORT_MAX_BYTES = 256 * 1024 * 1024
#: Raw bytes per `export` data line (65,536 base64 characters).
EXPORT_CHUNK_BYTES = 49152
#: A stalled export reader is cut after this many seconds without progress.
EXPORT_STALL_SECONDS = 25.0
EXPORT_PARTS = ("summary.json", "samples.jsonl.gz", "damon.jsonl", "events.jsonl")
#: How long the accept thread waits to deliver a `busy` line to a peer it is
#: refusing (it must never block the accept loop on a peer that never reads).
BUSY_SEND_TIMEOUT_SECONDS = 0.2
#: Daemon shutdown budget. docker's default stop grace is 10 s, so the sum of
#: the in-flight drain, the worker join and one forced finalize must stay
#: under it on the normal path (4 + 4 + 1 = 9 s); the ciu project also sets
#: `stop_grace_period: 30s` so a wedged session still finalizes before SIGKILL.
#: In-flight verbs, then the sampling workers, each get a bounded wait.
SHUTDOWN_DRAIN_SECONDS = 4.0
SHUTDOWN_JOIN_SECONDS = 4.0
#: Session-lock wait for a forced finalize at shutdown (the process is exiting).
SHUTDOWN_LOCK_SECONDS = 1.0

#: CP-31: how often the accept loop verifies that the control socket path
#: still holds THIS daemon's listener (same inode). 0 disables the check.
SOCKET_CHECK_INTERVAL_SECONDS = 5.0
#: `serve_forever` exit status when the control socket was lost and could
#: not be rebound: non-zero so the restart policy recreates the daemon.
EXIT_SOCKET_LOST = 3


class SocketActivationError(ValueError):
    """Invalid systemd socket activation environment or descriptor set."""


def adopt_systemd_sockets(
    environ: Optional[MutableMapping[str, str]] = None,
    *,
    pid: Optional[int] = None,
) -> Optional[Dict[str, socket.socket]]:
    """Adopt systemd's named listening descriptors, or return ``None``.

    A returned empty mapping means activation was explicitly present but
    supplied no sockets. Descriptor ownership transfers to the returned
    socket objects. Activation variables are removed before returning so
    children cannot mistake the daemon's descriptors for their own.
    """
    env = os.environ if environ is None else environ
    raw_count = env.get("LISTEN_FDS")
    if raw_count is None:
        return None
    current_pid = os.getpid() if pid is None else pid
    raw_pid = env.get("LISTEN_PID")
    if raw_pid != str(current_pid):
        raise SocketActivationError(
            f"LISTEN_FDS is set but LISTEN_PID {raw_pid!r} does not match process {current_pid}"
        )
    try:
        count = int(raw_count, 10)
    except ValueError:
        raise SocketActivationError(f"LISTEN_FDS is not a non-negative integer: {raw_count!r}") from None
    if count < 0:
        raise SocketActivationError(f"LISTEN_FDS is not a non-negative integer: {raw_count!r}")
    names = env.get("LISTEN_FDNAMES", "").split(":") if count else []
    if len(names) != count:
        raise SocketActivationError(
            f"LISTEN_FDNAMES names {len(names)} descriptors but LISTEN_FDS is {count}"
        )
    adopted: Dict[str, socket.socket] = {}
    opened: List[socket.socket] = []
    try:
        for offset in range(count):
            opened.append(socket.socket(fileno=3 + offset))
        for listener, name in zip(opened, names):
            if name not in ("control", "observer"):
                raise SocketActivationError(f"unknown systemd socket descriptor name {name!r}")
            if name in adopted:
                raise SocketActivationError(f"more than one systemd socket descriptor is named {name!r}")
            if listener.family != socket.AF_UNIX or listener.type & socket.SOCK_STREAM != socket.SOCK_STREAM:
                raise SocketActivationError(f"systemd descriptor {name!r} is not an AF_UNIX stream socket")
            if not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN):
                raise SocketActivationError(f"systemd descriptor {name!r} is not listening")
            listener.set_inheritable(False)
            adopted[name] = listener
        for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
            env.pop(key, None)
        return adopted
    except BaseException:
        for listener in opened:
            try:
                listener.close()
            except OSError:
                pass
        raise


def daemon_slice_problem(cgroup_root: str, proc_root: str) -> Optional[str]:
    """Why the daemon is not provably contained, or None when it is (Sol B4).

    The daemon's OWN cgroup is derived from the host proc view (never looked
    up by slice name): its ancestry must start at ``cgprofile.slice`` and that
    ancestor must carry a finite, positive ``memory.max``. A bounded
    ``cgprofile.slice`` that exists while the container was created under some
    other parent therefore does not pass. ``util.read_int`` maps ``max``, an
    empty file, a garbage value and an unreadable file all to None, so the
    message names every cause. ``LoadState`` cannot be read from inside the
    container; that Docker-host proof is CIU-150."""
    pid = placement_mod._host_pid_of_self(proc_root)
    if pid is None:
        return "cannot determine this daemon's host PID from the host proc view"
    own = targets_mod.cgroup_of_pid(pid, cgroup_root, proc_root)
    if own is None:
        return f"cannot derive this daemon's own cgroup (host PID {pid})"
    parts = [part for part in own.split("/") if part]
    if not parts or parts[0] != DAEMON_SLICE_NAME:
        return (
            f"this daemon runs in cgroup {own}, which is not under "
            f"{DAEMON_SLICE_NAME}: its container was created with another "
            f"cgroup parent"
        )
    slice_cgroup = "/" + parts[0]
    value = util.read_int(
        os.path.join(cgroup_root, slice_cgroup.lstrip("/"), "memory.max")
    )
    if value is None or value <= 0:
        return (
            f"memory.max of {slice_cgroup} is not a finite positive integer "
            f"(it is unlimited, zero, empty, unreadable or not a number); zero "
            f"is refused because a slice that can hold no memory is a "
            f"misconfiguration, not containment"
        )
    return None


def daemon_slice_bounded(cgroup_root: str, proc_root: str) -> bool:
    return daemon_slice_problem(cgroup_root, proc_root) is None


class _RestoreClosedOnError:
    """Context manager for the finalize section (CP-27 N9): on an exception
    it clears ``sess.closed`` (and a forced-finalize mark), records the error
    and SUPPRESSES it so the caller can report "not finalized". It must nest
    inside the held session lock so the restore happens under it."""

    def __init__(self, sess: Any, *, was_forced: bool) -> None:
        self._sess = sess
        self._was_forced = was_forced
        self.error: Optional[BaseException] = None

    def __enter__(self) -> "_RestoreClosedOnError":
        return self

    def __exit__(self, exc_type: Any, exc: Any, _tb: Any) -> bool:
        if exc is None or not isinstance(exc, Exception):
            return False
        self._sess.closed = False
        if self._was_forced:
            self._sess.stop_forced = False
        self.error = exc
        return True


class _HeldLock:
    """Context manager that releases an ALREADY-acquired lock on exit, so the
    finalize section can acquire with a timeout and still read as a ``with``."""

    def __init__(self, lock: Any) -> None:
        self._lock = lock

    def __enter__(self) -> "_HeldLock":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._lock.release()


class _ResizableSlots:
    """A non-blocking counting limit whose cap can change at runtime
    (`BoundedSemaphore` cannot be resized). Lowering the cap never evicts a
    holder; it only refuses new acquires until enough have released."""

    def __init__(self, limit: int) -> None:
        self._lock = threading.Lock()
        self._limit = limit
        self._held = 0

    def acquire(self, blocking: bool = False) -> bool:
        with self._lock:
            if self._held >= self._limit:
                return False
            self._held += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._held <= 0:
                raise ValueError("released more slots than were acquired")
            self._held -= 1

    def resize(self, limit: int) -> None:
        with self._lock:
            self._limit = limit


class _Ctx:
    """Per-request context: the wire contract and the peer (SO_PEERCRED)."""

    __slots__ = ("contract", "uid", "pid")

    def __init__(self, contract: int = CONTRACT_VERSION, uid: Optional[int] = 0,
                 pid: Optional[int] = None) -> None:
        self.contract = contract
        self.uid = uid
        self.pid = pid


#: In-process callers (tests, direct handler calls) are the trusted uid 0.
_TRUSTED = _Ctx()
#: Sentinel: `_dispatch(req)` without a peer means "in-process, trusted".
_UNSET: Any = object()


class RequestError(Exception):
    """A verb refused the request — becomes ``{"ok": false, "error": {...}}``
    (contract exit code 2), never an uncaught traceback back to the socket."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ControlSocketError(OSError):
    """The control socket path cannot be bound safely (CP-34): it holds a
    non-socket node or another daemon still answers there. `cgprofile serve`
    turns this into a refusal to start (exit 2) with the message."""


class HostWriteError(RuntimeError):
    """This module's half of the RG-55 C4 ``WRITABLE_ROOTS`` boundary — the
    other half is :class:`lib.damon.HostWriteError`, guarding the DAMON
    admin sysfs root. Raised by :meth:`SessionServer._guard_path` when a
    write this class is about to perform would land outside the sessions
    directory or that same DAMON root.
    """


# ── the live session record ─────────────────────────────────────────────────

@dataclass
class _Session:
    session_id: str
    scope: str
    container_id: str
    cgroup: str
    slice_name: Optional[str]
    slice_cgroup: Optional[str]
    token: Optional[str]
    # PROTOCOL 10.5: the SO_PEERCRED uid that started the session; None for a
    # session recovered from a 1.x store (uid 0 only).
    owner_uid: Optional[int] = field(default=None, kw_only=True)
    interval: float
    meta: Dict[str, Any]
    started_at: str
    baseline_memory_bytes: Optional[int]
    pids_at_start: int
    host_snapshot: Dict[str, Any]
    # CP-1: the `machine/1` facts fixed at session start (also in the manifest,
    # so a recovered session's Summary carries the same block).
    machine: Optional[Dict[str, Any]] = field(default=None, kw_only=True)
    rundir: "store.RunDir"
    summary_acc: "summary.SummaryAccumulator"
    subtree_resolver: Optional["subtree.SubtreeResolver"]
    damon_session: Optional["damon_mod.DamonSession"]
    damon_status: str
    damon_requested_on: bool
    damon_unavailable_reason: Optional[str]
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop_event: threading.Event = field(default_factory=threading.Event)
    stop_attempts: int = 0
    stop_forced: bool = False
    # Why the last `_finalize_session_locked` returned False: "worker" (the
    # sampling thread is still alive) or "lock" (the session lock stayed
    # busy); "" when it did not. Lets `stop` say which (CP-27 N8).
    finalize_refusal: str = ""
    # With `finalize_refusal == "error"`: the exception text (CP-27 N9).
    finalize_error: str = ""
    # Set under ``lock`` BEFORE placement is released and DAMON is closed
    # (Sol S6). Every sample-side action that touches those resources or the
    # session's records re-checks it under the same lock, so a wedged worker
    # cannot migrate, recommit or append after release has begun.
    closed: bool = False
    thread: Optional[threading.Thread] = None
    damon_cleanup: Optional[Dict[str, Any]] = None
    live_samples: int = 0
    live_memory_current_bytes: Optional[int] = None
    live_memory_peak_bytes: Optional[int] = None
    live_cpu_cores_recent: Optional[float] = None
    live_damon_hot_bytes_recent: Optional[int] = None
    # CP-40: the ancestors' local memory.events at session start; the delta
    # to the read at finalization is the Summary's
    # `ancestors_memory_events_local`.
    ancestor_events_start: Optional[Dict[str, Any]] = None
    last_mono: Optional[float] = None
    last_discovery_mono: Optional[float] = None
    sampler_origin_mono: Optional[float] = None
    # RW-15: the no-token path's own pid cache between discovery ticks —
    # the token path already has one (SubtreeResolver.current_pids); a
    # no-token session has no resolver object, so this is the equivalent
    # cache `_on_session_sample` diffs against to decide whether DAMON needs
    # recommitting.
    no_token_pids: List[int] = field(default_factory=list)
    _prev_cpu_usage_usec: Optional[int] = None
    _prev_mono: Optional[float] = None
    # CP-5: the collector's own `lib.events.Detector`, one instance per
    # session (it is stateful/edge-triggered, exactly like `cmd_collect`'s),
    # and the previous tick's full sample record it diffs against.
    # `last_effective_limits` is the same detector's `limit_drift` input,
    # refreshed on the discovery cadence (see `_on_session_sample`) rather
    # than every tick — `lib.limits.effective()` walks the ancestor chain,
    # and this daemon's interval can be as low as 0.25s.
    detector: Optional["events_mod.Detector"] = None
    _prev_record: Optional[Dict[str, Any]] = None
    last_effective_limits: Optional["limits_mod.Effective"] = None
    # CP-8 (C7, §8.4): the session's own watcher. Always present — a session
    # started with no policy options at all still gets a tracker under the
    # default policy (idle bound `auto`, `--on-stall report`), because §8.4's
    # `liveness` block is per SESSION, not per policy: `status` reports it
    # either way and nothing is ever killed unless `kill` was asked for.
    # Mutated only on the session's own sampler thread, read under
    # `sess.lock` by `status`/`stop`/the `watch` stream.
    watch: Optional["liveness_mod.LivenessTracker"] = None
    # CP-9 / D-31 (§8.3): the session's leaf under its delegated scope, or `None`
    # when `--place` was never asked for. A REFUSED placement is still an
    # object (it carries the `place-refused:*` code the caller must see);
    # only "never asked" is `None`, which is what makes `placement: null`
    # in a reading line mean exactly one thing (§8.2's own union).
    placement: Optional["placement_mod.LanePlacement"] = None
    finished: bool = False
    ended_at: Optional[str] = None
    summary_doc: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class SessionServer:
    """Owns the registry, the listening socket, and every session thread.

    ``cgroup_root``/``proc_root`` are test seams (every real caller uses the
    defaults) — a test points them at a fake tree the same way every other
    reader in this package does; :meth:`_session_loop` re-resolves paths
    under them on every tick rather than caching file handles, so a test can
    swap what a root resolves to (a symlink repointed between ticks) and the
    session picks up the new frame with no server-side support needed for
    that specifically.

    ``host_proc_root`` is a SEPARATE seam (CP-10) for the host-wide reads in
    :func:`lib.metrics.sample_host` (meminfo, loadavg, and — the one that
    matters for liveness — ``pressure/memory``, §8.4's pause condition). It
    defaults to ``proc_root`` when not given, so every existing caller (real
    or test) is unaffected. A test that needs REAL ``/proc`` for subtree/pid
    resolution (walking `/proc/<pid>/...` to find an actual subprocess) but
    a CONTROLLED, low-pressure host reading — so its liveness assertions do
    not depend on this host's ambient memory PSI at the moment it happens to
    run — passes `proc_root="/proc"` and `host_proc_root=<a fake proc dir>`
    separately. Conflating the two (one knob for both) is exactly what made
    `TestRealSubtreeEnforcement` flake on real host memory pressure: see the
    CP-10 backlog row and `cgprofile-P6-FOLLOWUPS-REPORT.md`.
    """

    def __init__(
        self,
        *,
        sessions_dir: str = DEFAULT_SESSIONS_DIR,
        socket_path: str = DEFAULT_SOCKET_PATH,
        observer_socket_path: Optional[str] = None,
        damon_default: str = "on",
        interval: float = DEFAULT_INTERVAL,
        keep_sessions: int = DEFAULT_KEEP_SESSIONS,
        keep_days: int = DEFAULT_KEEP_DAYS,
        observe_slices: Sequence[str] = (),
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        gates_slice_name: str = DEFAULT_GATES_SLICE_NAME,
        slice_unit_verifier: Optional[Callable[[str, str], bool]] = None,
        allow_uids: Optional[Sequence[int]] = None,
        cgroup_root: str = access.CGROUP_ROOT,
        proc_root: str = access.PROC_ROOT,
        host_proc_root: Optional[str] = None,
        daemon_name: str = DEFAULT_DAEMON_NAME,
        clock: Callable[[], float] = time.time,
        sampler_clock: Callable[[], float] = time.monotonic,
        sampler_sleep: Callable[[float], None] = time.sleep,
        watch_wait: Optional[Callable[[threading.Event, float], Any]] = None,
        session_id_fn: Optional[Callable[[], str]] = None,
        damon_pool: Optional["damon_mod.KdamondPool"] = None,
        accept_timeout: float = 0.5,
        request_line_timeout: float = REQUEST_LINE_TIMEOUT_SECONDS,
        max_request_line_bytes: int = MAX_REQUEST_LINE_BYTES,
        request_clock: Callable[[], float] = time.monotonic,
        max_request_readers: int = MAX_REQUEST_READERS,
        max_control_handlers: int = MAX_CONTROL_HANDLERS,
        max_watch_streams: int = MAX_WATCH_STREAMS,
        max_exports: int = MAX_EXPORTS,
        export_max_bytes: int = DEFAULT_EXPORT_MAX_BYTES,
        socket_check_interval: float = SOCKET_CHECK_INTERVAL_SECONDS,
        placement_factory: Optional[Callable[..., "placement_mod.LanePlacement"]] = None,
        settings: Optional["daemon_config.Settings"] = None,
        activated_sockets: Optional[Mapping[str, socket.socket]] = None,
        host_benchmark_path: Optional[str] = None,
        sys_root: Optional[str] = None,
    ) -> None:
        if damon_default not in ("on", "off"):
            raise ValueError(f"damon_default must be 'on' or 'off', got {damon_default!r}")
        self.sessions_dir = sessions_dir
        self.socket_path = socket_path
        self.observer_socket_path = (
            observer_socket_path or DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH
        )
        if os.path.normpath(self.observer_socket_path) == os.path.normpath(self.socket_path):
            raise ValueError("resource observer socket must be distinct from the control socket")
        self.damon_default = damon_default
        self.default_interval = interval
        self.keep_sessions = keep_sessions
        self.keep_days = keep_days
        self.observe_slices = list(observe_slices)
        self.max_sessions = max_sessions
        self.gates_slice_name = gates_slice_name
        self.export_max_bytes = export_max_bytes
        self.slice_unit_verifier = slice_unit_verifier or access.verify_systemd_slice
        self._gates_slice_verification: Optional[Tuple[float, bool]] = None
        self._gates_slice_verify_lock = threading.Lock()
        # §8.1/§8.6: `None` = no uid allowlist (the dedicated socket group's
        # permissions grant access); a list = SO_PEERCRED uid must be 0 or listed.
        self.allow_uids: Optional[List[int]] = list(allow_uids) if allow_uids is not None else None
        self.cgroup_root = cgroup_root
        self.proc_root = proc_root
        # CP-10: host-wide reads (meminfo/loadavg/pressure) default to
        # `proc_root` — every real caller and every existing test that
        # passes one root for both stays byte-identical. A test that must
        # use the REAL `/proc` for pid/subtree resolution but wants a
        # controlled host-pressure reading overrides this separately.
        self.host_proc_root = host_proc_root if host_proc_root is not None else proc_root
        # CP-1: machine facts. The persisted host benchmark (read only, never
        # measured) is loaded now and again on reload; the packaged fallback
        # table is read once.
        self.host_benchmark_path = host_benchmark_path or machine_mod.DEFAULT_BENCHMARK_PATH
        self.sys_root = sys_root or machine_mod.DEFAULT_SYS_ROOT
        self._scores_table = machine_mod.load_scores_table()
        self._host_benchmark = machine_mod.load_host_benchmark(self.host_benchmark_path)
        self.windows = observer_windows_mod.WindowRegistry(
            proc_root=self.proc_root,
            host_proc_root=self.host_proc_root,
            cgroup_root=self.cgroup_root,
            mountinfo_path=os.path.join(self.proc_root, "self", "mountinfo"),
            gates_slice_name=self.gates_slice_name,
        )
        self.daemon_name = daemon_name
        self.clock = clock
        self.sampler_clock = sampler_clock
        self.sampler_sleep = sampler_sleep
        # §8.2's inter-reading wait. The real one is the session's own stop
        # event, so a `stop` ends every watch stream on it immediately
        # instead of after up to `--watch-interval` (300 s at the clamp's
        # top) of dead air. A test substitutes a wait that returns at once —
        # the same seam `sampler_sleep` already is for the sampler, and the
        # only way a streaming test can run in milliseconds when the
        # contract's own floor for the interval is 5 s.
        self.watch_wait: Callable[[threading.Event, float], Any] = (
            watch_wait if watch_wait is not None
            else (lambda event, timeout: event.wait(timeout))
        )
        self.accept_timeout = accept_timeout
        if request_line_timeout <= 0:
            raise ValueError("request_line_timeout must be positive")
        if max_request_line_bytes < 3:
            raise ValueError("max_request_line_bytes must be at least 3")
        self.request_line_timeout = request_line_timeout
        self.max_request_line_bytes = max_request_line_bytes
        self.request_clock = request_clock
        self._session_id_fn = session_id_fn or self._default_session_id
        self.damon_pool = (
            damon_pool if damon_pool is not None
            else damon_mod.KdamondPool(capacity=max_sessions)
        )
        # CP-9: how a placement leaf is removed. `os.rmdir` on the real
        # kernfs cgroup directory; a test pointing `cgroup_root` at a tmp
        # tree replaces it, because a REAL directory holding the same
        # interface files is ENOTEMPTY where a cgroup is not (see
        # `lib.placement.LanePlacement`'s own note).
        self.cgroup_rmdir: Callable[[str], None] = os.rmdir
        self.placement_factory = placement_factory

        self._sessions: Dict[str, _Session] = {}
        self._by_target: Dict[Tuple[str, Optional[str]], str] = {}
        self._lock = threading.RLock()
        self._sock: Optional[socket.socket] = None
        self._observer_sock: Optional[socket.socket] = None
        self._activated_sockets = None if activated_sockets is None else dict(activated_sockets)
        self.socket_mode = "self-bind" if activated_sockets is None else "activated"
        self._observer_socket_identity: Optional[Tuple[int, int]] = None
        self._observer_thread: Optional[threading.Thread] = None
        self._stopping = False
        # CP-34/CP-31: the (dev, inode) of the control listener this process
        # bound, so shutdown and the periodic self-check act only on its own
        # node, never on a successor's.
        self._sock_identity: Optional[Tuple[int, int]] = None
        self.socket_check_interval = socket_check_interval
        self._socket_lost = False
        # CP-33: bounded admission. Non-blocking acquires only.
        self._reader_slots = threading.BoundedSemaphore(max_request_readers)
        self._handler_slots = threading.BoundedSemaphore(max_control_handlers)
        self._watch_slots = _ResizableSlots(max_watch_streams)
        self._export_slots = _ResizableSlots(max_exports)
        self._exporting: Dict[str, int] = {}
        self._inflight = 0
        self._inflight_cond = threading.Condition()

        seeded = settings is None
        if settings is None:
            # In-process construction (tests, embedding): the keyword
            # arguments are the file layer of a config-less Settings, so the
            # `config` verbs work the same way for every server.
            settings = daemon_config.Settings(
                os.path.join(os.path.dirname(sessions_dir), "cgprofiled.toml"),
                overrides_path=os.path.join(os.path.dirname(sessions_dir), "overrides.toml"),
                log=self._log,
            )
            settings.load()
            settings.seed({
                "sessions_dir": sessions_dir,
                "sockets.control": {
                    "path": socket_path, "group": "cgprofile-control", "mode": "0660",
                },
                "sockets.observer": {
                    "path": observer_socket_path or DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH,
                    "group": "cgprofile-observer", "mode": "0660",
                },
                "damon_default": damon_default, "interval": interval,
                "keep_sessions": keep_sessions, "keep_days": keep_days,
                "max_sessions": max_sessions, "observe_slices": list(observe_slices),
                "gates_slice": gates_slice_name, "max_watchers": max_watch_streams,
                "max_exports": max_exports, "export_max_bytes": export_max_bytes,
                "allow_uids": list(allow_uids or []),
            })
        self.settings = settings
        self._applied: Dict[str, Any] = {}
        # A Settings handed in (cgprofiled) is authoritative; one built above from
        # the keyword arguments already equals the attributes, so it is only
        # snapshotted (an `allow_uids=[]` argument must stay a list).
        self._apply_settings(snapshot_only=seeded)

        self.started_at = self._iso(self.clock())
        self._guard_path(self.sessions_dir)
        os.makedirs(self.sessions_dir, exist_ok=True)
        self._recover_orphans()

    # ── time / ids ───────────────────────────────────────────────────────

    @staticmethod
    def _iso(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(_ISO_FMT)

    @staticmethod
    def _parse_iso_epoch(text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        try:
            dt = datetime.strptime(text, _ISO_FMT).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return dt.timestamp()

    def _default_session_id(self) -> str:
        ts = datetime.fromtimestamp(self.clock(), tz=timezone.utc).strftime(_SESSION_ID_TS_FMT)
        return f"s-{ts}-{secrets.token_hex(2)}"

    # ── WRITABLE_ROOTS boundary (RG-55 C4) ──────────────────────────────

    def _writable_roots(self) -> Tuple[str, str]:
        return (
            os.path.realpath(self.sessions_dir),
            os.path.realpath(os.path.dirname(damon_mod.KDAMONDS_DIR)),
        )

    def _guard_path(self, path: str) -> None:
        """Refuse any write whose target does not resolve under one of
        :meth:`_writable_roots`. Every raw write this class issues directly
        goes through this first — see the module docstring."""
        roots = self._writable_roots()
        if not any(
            util.realpath_is_within(path, root, allow_root=True) for root in roots
        ):
            raise HostWriteError(
                f"refusing to write outside WRITABLE_ROOTS {roots!r}: {path!r}"
            )

    # ── restart recovery ─────────────────────────────────────────────────

    def _recover_orphans(self) -> None:
        """Any session directory whose manifest still says ``"live"`` was
        abandoned by a previous daemon process that never got to stop it —
        finalize it now, from whatever series data it managed to write,
        rather than leave a session neither live nor summarized forever."""
        try:
            names = sorted(os.listdir(self.sessions_dir))
        except OSError:
            return
        for name in names:
            session_dir = os.path.join(self.sessions_dir, name)
            if not os.path.isdir(session_dir):
                continue
            manifest_path = os.path.join(session_dir, "manifest.json")
            manifest: Optional[Dict[str, Any]] = None
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as fh:
                        candidate = json.load(fh)
                    if isinstance(candidate, dict):
                        manifest = candidate
                except (OSError, json.JSONDecodeError) as exc:
                    self._log(f"recovery: cannot read manifest for {name}: {exc}")
            placement_path = os.path.join(session_dir, "placement-state.json")
            if manifest is None:
                # Placement journals are written before StartTransientUnit and
                # before the live manifest. A daemon crash in that interval
                # must still restore or preserve the exact scope; absence of
                # a manifest is not evidence that no PID was moved.
                if os.path.isfile(placement_path):
                    error = self._recover_placement_file(name, placement_path)
                    self._write_json_path(
                        os.path.join(session_dir, "placement-recovery.json"),
                        {
                            "session": name,
                            "status": "restored" if error is None else "failed",
                            "error": error,
                            "checked_at": self._iso(self.clock()),
                        },
                    )
                    if error is not None:
                        self._log(
                            f"recovery: journal-only placement for {name} "
                            f"requires operator attention: {error}"
                        )
                continue
            if manifest.get("status") != "live":
                # A session may have been marked finished after lane cleanup
                # failed. Its durable placement journal is still the only
                # authority for restoring the PIDs or preserving the owned
                # scope, so retry recovery on startup until the journal says
                # complete. Do not finalize the already-finished session a
                # second time, and do not revisit completed journals (the
                # token may have since been reused by a later session).
                if os.path.isfile(placement_path):
                    try:
                        with open(placement_path, "r", encoding="utf-8") as fh:
                            journal = json.load(fh)
                        needs_recovery = (
                            not isinstance(journal, dict)
                            or journal.get("state") != "complete"
                        )
                    except (OSError, json.JSONDecodeError):
                        needs_recovery = True
                    if needs_recovery:
                        error = self._recover_placement_file(name, placement_path)
                        manifest["placement_recovery"] = (
                            {"status": "failed", "error": error}
                            if error is not None else
                            {"status": "restored", "error": None}
                        )
                        self._write_json_path(manifest_path, manifest)
                        if error is not None:
                            self._log(
                                f"recovery: completed session {name} has an "
                                f"incomplete placement requiring operator attention: {error}"
                            )
                continue
            placement_recovery: Optional[str] = None
            if os.path.exists(placement_path):
                placement_recovery = self._recover_placement_file(name, placement_path)
            elif (
                isinstance(manifest.get("placement"), dict)
                and manifest["placement"].get("requested") is True
            ):
                placement_recovery = placement_mod.REFUSED_STATE_UNAVAILABLE
            if placement_recovery is not None:
                manifest["placement_recovery"] = {
                    "status": "failed", "error": placement_recovery,
                }
            elif os.path.exists(placement_path):
                manifest["placement_recovery"] = {"status": "restored", "error": None}
            self._finalize_orphan(name, manifest)

    def _recover_placement_file(self, session_id: str, path: str) -> Optional[str]:
        """Run identity-checked placement recovery from one durable journal."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                journal = json.load(fh)
            return placement_mod.recover_journal(
                journal,
                cgroup_root=self.cgroup_root,
                gates_cgroup=targets_mod.slice_to_path(self.gates_slice_name),
                proc_root=self.proc_root,
                state_write=lambda state: self._write_json_path(path, state),
                log=self._log,
            )
        except Exception as exc:  # noqa: BLE001 - corrupt recovery state is fail-closed
            self._log(f"recovery: cannot read placement journal for {session_id}: {exc}")
            return placement_mod.REFUSED_STATE_UNAVAILABLE

    @staticmethod
    def _write_json_path(path: str, value: Dict[str, Any]) -> None:
        directory, filename = os.path.split(path)
        rundir = store.RunDir(
            os.path.dirname(directory), run_id=os.path.basename(directory), create=False
        )
        rundir.write_json(filename, value)

    def _finalize_orphan(self, session_id: str, manifest: Dict[str, Any]) -> None:
        rundir = store.RunDir(self.sessions_dir, run_id=session_id, create=False)
        ended_at = self._iso(self.clock())
        acc = summary.SummaryAccumulator(
            session=session_id,
            daemon_name=self.daemon_name,
            daemon_version=CGPROFILE_VERSION,
            scope=manifest["scope"],
            started_at=manifest["started_at"],
            interval_seconds=manifest["interval_seconds"],
            container_id=manifest["container_id"],
            cgroup=manifest["cgroup"],
            token=manifest.get("token"),
            slice_name=manifest.get("slice_name"),
            damon_enabled=bool(manifest.get("damon_enabled")),
            damon_kdamond=manifest.get("damon_kdamond"),
            damon_thresholds=manifest.get("damon_thresholds"),
            machine=manifest.get("machine"),
        )
        if manifest.get("damon_unavailable_reason"):
            acc.mark_damon_unavailable(manifest["damon_unavailable_reason"])
        samples = list(rundir.read("samples"))
        hosts = list(rundir.read("host"))
        damons = list(rundir.read("damon"))
        damon_by_seq = {
            item.get("_seq"): {key: value for key, value in item.items() if key != "_seq"}
            for item in damons
            if isinstance(item, dict) and isinstance(item.get("_seq"), int)
        }
        target_cgroup = manifest["cgroup"]
        for index in range(min(len(samples), len(hosts))):
            sample = samples[index]
            host_rec = hosts[index]
            # RW-14: "samples" is persisted keyed by cgroup path (matching
            # `lib.analyze.to_frame`'s own expectation of the on-disk shape —
            # see `_on_session_sample`'s own comment), so replay unwraps the
            # one path this session ever wrote before feeding the flat
            # per-target dict `SummaryAccumulator.add_sample` expects.
            acc.add_sample(
                cgroup=(sample.get("cg") or {}).get(target_cgroup, {}),
                host=host_rec.get("host") or {},
                slice_cgroup=host_rec.get("slice"),
                damon=(
                    damon_by_seq.get(sample.get("seq"))
                    if damon_by_seq
                    else (damons[index] if index < len(damons) else None)
                ),
                pids=sample.get("pids") or [],
                mono=sample.get("mono"),
            )
        manifest["status"] = "aborted"
        recovery_failed = manifest.get("placement_recovery", {}).get("status") == "failed"
        manifest["aborted_reason"] = (
            "daemon-restarted-placement-recovery-failed"
            if recovery_failed else "daemon-restarted"
        )
        manifest["ended_at"] = ended_at
        if acc.sample_count:
            summary_doc = acc.finalize(ended_at=ended_at)
            if "placement_recovery" in manifest:
                summary_doc["placement"] = {
                    "requested": True,
                    "leaf": None,
                    "applied": {},
                    "pids_moved": 0,
                    "error": (
                        manifest["placement_recovery"].get("error")
                        if recovery_failed else None
                    ),
                }
                summary_doc["placement_recovery"] = manifest["placement_recovery"]
            rundir.write_json("summary.json", summary_doc)
        rundir.write_manifest(manifest)

    # ── registry helpers ─────────────────────────────────────────────────

    def _count_live(self) -> int:
        return sum(1 for s in self._sessions.values() if not s.finished)

    def _manifest_for(self, sess: _Session, *, status: str) -> Dict[str, Any]:
        started_epoch = self._parse_iso_epoch(sess.started_at)
        ended_epoch = self._parse_iso_epoch(sess.ended_at) if sess.ended_at else None
        # CP-7 (C3): lazy, mirroring `cli/legacy.py`'s own `cmd_serve` lazy
        # import of `cgprofile.serve` — `legacy` is the CLI module
        # (never imported at this module's top level, so this stays a
        # one-directional dependency at call time only), and `_limits_snapshot`
        # is its own helper (the exact shape `cmd_collect` already writes)
        # rather than a duplicate reimplementation here.
        from .cli import legacy as cg_module
        return {
            "session": sess.session_id,
            "status": status,
            "scope": sess.scope,
            "container_id": sess.container_id,
            "cgroup": sess.cgroup,
            "token": sess.token,
            "owner_uid": sess.owner_uid,
            "slice_name": sess.slice_name,
            "interval_seconds": sess.interval,
            "started_at": sess.started_at,
            "ended_at": sess.ended_at,
            "damon_enabled": sess.damon_requested_on,
            "damon_kdamond": sess.damon_session.kdamond_idx if sess.damon_session else None,
            "damon_thresholds": sess.damon_session.thresholds if sess.damon_session else None,
            "damon_unavailable_reason": sess.damon_unavailable_reason,
            "damon_cleanup": sess.damon_cleanup,
            "placement": _placement_block(sess),
            "meta": sess.meta,
            "machine": sess.machine,
            "aborted_reason": None,
            # RW-14: everything from here down is never read by an RG-55
            # contract verb — it exists so this same manifest.json also
            # satisfies the *other* shape a run directory must have
            # (DESIGN.md §4.3, `lib.store.RunDir`'s promise to `analyze.py`):
            # `run_id`, `started`, `targets`, `host`, `limits` are exactly
            # the keys `lib.analyze.build`/`cgprofile.py`'s own collector
            # (`cmd_collect`) writes, so `handle_report`'s subprocess can
            # point the existing report tier straight at this session
            # directory with no translation step. `limits` (CP-7, C3): the
            # session's own cgroup resolved once at `start` time by
            # `_create_session_locked` (`limits_mod.effective()`, the same
            # call CP-5's `Detector` construction already made and stored on
            # `sess.last_effective_limits` for drift detection) — reused
            # here rather than re-resolved, in `cgprofile.py`'s own
            # `_limits_snapshot` shape (`{"resolved": {...}, "described":
            # [...], "fingerprint": "..."}`) so `analyze.py`'s proposal
            # checks (`_effective_limits`) see exactly the schema
            # `cmd_collect` already produces. A daemon session only ever
            # profiles ONE cgroup, so this table has exactly one entry — a
            # structural fact worth naming: `_check_oversubscription` needs
            # >= 2 sibling entries under a shared parent to fire at all, so
            # it is permanently a no-op for a single-target daemon session
            # regardless of this fix; single-cgroup checks like
            # `_check_recursiveprot_gap` are the ones this actually unlocks.
            "run_id": sess.session_id,
            "started": started_epoch,
            "ended": ended_epoch,
            "duration": (
                (ended_epoch - started_epoch)
                if started_epoch is not None and ended_epoch is not None
                else None
            ),
            "argv": None,
            "mode": "daemon-session",
            "cgroup_root": self.cgroup_root,
            "mount_flags": [],
            "targets": [{
                "key": "target", "cgroup": sess.cgroup, "label": sess.session_id,
                "kind": "container", "role": "subject", "follow_children": False,
                "container_id": sess.container_id, "pid": None,
            }],
            "limits": {sess.cgroup: cg_module._limits_snapshot(limits_mod, sess.last_effective_limits)}
                       if sess.last_effective_limits is not None else {},
            "host": sess.host_snapshot,
            "config": {"interval": sess.interval},
        }

    # ── verb: version ────────────────────────────────────────────────────

    def handle_version(self, args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        with self._lock:
            live = self._count_live()
            observer_listening = self._observer_sock is not None
        damon_state = "available" if damon_mod.available() else "unavailable:not available on this host"
        doc = {
            "ok": True,
            "contract": CONTRACT_VERSION,
            "contracts": list(CONTRACTS),
            "cgprofile": CGPROFILE_VERSION,
            "daemon": {
                "name": self.daemon_name,
                "version": CGPROFILE_VERSION,
                "started_at": self.started_at,
                "damon": damon_state,
                "damon_default": self.damon_default,
                "sessions_live": live,
                "max_sessions": self.max_sessions,
            },
            # §8.6, additive under control contract 1: which carriers this
            # daemon answers on. No exec carrier exists; keep the false key
            # for contract-1 readers and drop it from contract-2 replies. The
            # socket half reports the real listener state, the effective
            # uid allowlist (`[]` = none
            # configured, so the dedicated socket group controls access) and
            # that SO_PEERCRED is enforced on every accepted connection.
            "transports": {
                "exec": False,
                "socket": {
                    "path": self.socket_path,
                    "listening": self._sock is not None,
                    "allow_uids": list(self.allow_uids or []),
                    "peer_cred": True,
                },
            },
            # Resource observation is a separate wire contract and listener;
            # advertise it so operators can distinguish a control-v1 daemon
            # that supports the contract-2 R2 observer from an older daemon.
            "resource_observer": {
                "contract": RESOURCE_OBSERVER_CONTRACT_VERSION,
                "contracts": list(RESOURCE_OBSERVER_CONTRACTS),
                "path": self.observer_socket_path,
                "listening": observer_listening,
            },
        }
        if ctx.contract >= 2:
            # The exec carrier no longer exists: the key is dropped at
            # contract 2 and kept (false) at contract 1 (PROTOCOL 10.1).
            del doc["transports"]["exec"]
            doc["config"] = {
                "digest": self.settings.digest,
                "last_reload_error": self.settings.last_reload_error,
                "overrides": self.settings.override_count,
            }
            doc["socket_activation"] = self._socket_activation_state()
        return doc

    def _socket_activation_state(self) -> Dict[str, Any]:
        """Report the serving mode and the accepted descriptor count per level."""
        return {
            "mode": self.socket_mode,
            "fds": {
                "control": int(self._sock is not None),
                "observer": int(self._observer_sock is not None),
            },
        }

    # ── verbs: config / config-set / config-reset (PROTOCOL 10.4) ────────

    def handle_config(self, args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        if args:
            raise RequestError("bad-argument", f"config takes no arguments, got {sorted(args)!r}")
        return {"ok": True, "contract": 2, "config": self.settings.describe()}

    def _config_audit(self, ctx: _Ctx, op: str, key: Any, old: Any, new: Any,
                      persist: Any) -> str:
        audit_id = self.settings.next_audit_id()
        self._log(
            f"config-audit id={audit_id} op={op} uid={ctx.uid} pid={ctx.pid} "
            f"key={key} old={json.dumps(old)} new={json.dumps(new)} "
            f"persist={json.dumps(persist)} ts={self._iso(self.clock())}"
        )
        return audit_id

    def _require_root(self, ctx: _Ctx, op: str, args: Dict[str, Any]) -> None:
        """Config writes are uid 0 only, audited even when refused."""
        if ctx.uid != 0:
            self._config_audit(
                ctx, "refused", args.get("key"), None, args.get("value"),
                args.get("persist"),
            )
            who = "unavailable" if ctx.uid is None else str(ctx.uid)
            raise RequestError(
                "peer-refused", f"{op} is allowed to uid 0 only (peer uid {who})"
            )

    def handle_config_set(self, args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        self._require_root(ctx, "set", args)
        unknown = sorted(set(args) - {"key", "value", "persist", "dry_run"})
        if unknown:
            raise RequestError("bad-argument", f"unexpected config-set argument(s) {unknown!r}")
        if "value" not in args:
            raise RequestError("bad-argument", "config-set requires 'key' and 'value'")
        for flag in ("persist", "dry_run"):
            if not isinstance(args.get(flag, False), bool):
                raise RequestError("bad-argument", f"{flag!r} must be a boolean")
        persist = args.get("persist", False)
        dry_run = args.get("dry_run", False)
        key = args.get("key")
        try:
            result = self.settings.set_override(
                key, args["value"], persist=persist, dry_run=dry_run
            )
        except daemon_config.ConfigError as exc:
            self._config_audit(ctx, "refused", key, None, args["value"], persist)
            raise RequestError("bad-argument", str(exc)) from None
        except daemon_config.PersistError as exc:
            self._config_audit(ctx, "refused", key, None, args["value"], persist)
            raise RequestError("persist-failed", str(exc)) from None
        if not dry_run:
            self._apply_settings()
        result["audit_id"] = self._config_audit(
            ctx, "set", key, result["old"]["value"], result["new"]["value"], persist,
        )
        return {"ok": True, "contract": 2, "config-set": result}

    def handle_config_reset(self, args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        self._require_root(ctx, "reset", args)
        unknown = sorted(set(args) - {"key", "all"})
        if unknown:
            raise RequestError("bad-argument", f"unexpected config-reset argument(s) {unknown!r}")
        key = args.get("key")
        every = args.get("all", False)
        if not isinstance(every, bool):
            raise RequestError("bad-argument", "'all' must be a boolean")
        if (key is None) == (not every):
            raise RequestError(
                "bad-argument", "config-reset needs exactly one of 'key' or 'all': true"
            )
        try:
            result = self.settings.reset(None if every else key)
        except daemon_config.ConfigError as exc:
            self._config_audit(ctx, "refused", key, None, None, None)
            raise RequestError("bad-argument", str(exc)) from None
        except daemon_config.PersistError as exc:
            self._config_audit(ctx, "refused", key, None, None, None)
            raise RequestError("persist-failed", str(exc)) from None
        self._apply_settings()
        result["audit_id"] = self._config_audit(
            ctx, "reset", "all" if every else key, result["reset"], None, None,
        )
        return {"ok": True, "contract": 2, "config-reset": result}

    # ── verb: start ──────────────────────────────────────────────────────

    def handle_start(self, args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        target_spec = args.get("target")
        scope = args.get("scope")
        token = args.get("token")
        damon_req = args["damon"] if "damon" in args else self.damon_default
        interval_req = args.get("interval")
        meta = args.get("meta")

        if not isinstance(target_spec, str) or not target_spec.startswith("containerid:"):
            raise RequestError("bad-argument", "--target must be 'containerid:<64 hex>'")
        container_id = target_spec[len("containerid:"):]
        if not util.is_container_id(container_id):
            raise RequestError("bad-argument", "containerid must be 64 lowercase hex characters")
        if scope not in ("container", "container-shared"):
            raise RequestError("bad-argument", "--scope must be 'container' or 'container-shared'")
        if damon_req not in ("on", "off"):
            raise RequestError("bad-argument", "--damon must be 'on' or 'off'")
        if token is not None and (
            not isinstance(token, str) or not _TOKEN_RE.fullmatch(token)
        ):
            raise RequestError("bad-argument", "--token must match [A-Za-z0-9._-]{8,64}")
        if not isinstance(meta, dict):
            raise RequestError("bad-argument", "--meta must be a JSON object")
        if interval_req is None:
            interval = self.default_interval
        else:
            if isinstance(interval_req, bool):
                raise RequestError("bad-argument", "--interval must be a finite number")
            try:
                interval = float(interval_req)
            except (TypeError, ValueError, OverflowError) as exc:
                raise RequestError("bad-argument", "--interval must be a finite number") from exc
            if not math.isfinite(interval):
                raise RequestError("bad-argument", "--interval must be a finite number")
        interval = util.clamp(interval, 0.25, 30.0)
        # CP-8 (§8.4/§8.8): the stall policy is parsed BEFORE anything is
        # created — `bad-policy` means exit 2 and NO session, never a session
        # running under a policy the daemon had to guess at. Note this is
        # outside the registry lock deliberately: a refusal must not be able
        # to reuse an existing session either (the `reused: true` branch
        # below would otherwise hand a caller a session under the OLD
        # policy while it believes it authored a new one).
        try:
            policy = liveness_mod.parse_policy(args)
        except liveness_mod.PolicyError as exc:
            raise RequestError("bad-policy", str(exc)) from None
        # CP-9 (§8.3): parsed in the same place and for the same reason. A
        # cap value the CLIENT typed wrong is `bad-argument` (exit 2, no
        # session) — §8.8's `place-refused:*` codes are reserved for what the
        # HOST turned out to be, and those never fail `start`.
        try:
            place_request = placement_mod.parse_request(args)
        except placement_mod.CapsError as exc:
            raise RequestError("bad-argument", str(exc)) from None

        if policy.on_stall == "kill" and scope == "container-shared":
            if token is None:
                raise RequestError(
                    "bad-policy",
                    "--on-stall kill for container-shared requires --token and --place",
                )
            if place_request is None:
                raise RequestError(
                    "bad-policy",
                    "--on-stall kill for container-shared requires --place",
                )

        with self._lock:
            if token is not None:
                key = (container_id, token)
                existing_id = self._by_target.get(key)
                if existing_id is not None:
                    existing = self._sessions.get(existing_id)
                    if existing is not None and not existing.finished:
                        if (
                            existing.scope != scope
                            or existing.watch is None
                            or existing.watch.policy != policy
                        ):
                            raise RequestError(
                                "bad-policy",
                                "a live target/token session already uses a different scope or liveness policy",
                            )
                        if policy.on_stall == "kill" and scope == "container-shared" and (
                            existing.placement is None
                            or not existing.placement.placed
                            or existing.placement.error is not None
                        ):
                            raise RequestError(
                                "bad-policy",
                                "the existing shared-scope session has no verified kill leaf",
                            )
                        return self._start_response(existing, reused=True)

            if self._count_live() >= self.max_sessions:
                raise RequestError(
                    "too-many-sessions", f"already at max_sessions={self.max_sessions}"
                )

            cgroup = targets_mod.find_container_cgroup(container_id, root=self.cgroup_root)
            if cgroup is None:
                raise RequestError(
                    "target-not-found", f"no cgroup for container {container_id[:12]}"
                )

            if policy.on_stall == "kill" and scope == "container":
                target_killer = self._make_container_killer(
                    container_id=container_id, cgroup=cgroup,
                )
                if (
                    target_killer.target_abs is None
                    or not os.path.isdir(target_killer.target_abs)
                    or not self._gates_slice_is_verified(refresh=True)
                ):
                    raise RequestError(
                        "bad-policy",
                        "--on-stall kill for scope container requires the exact target cgroup "
                        "under the verified, bounded dev-gates.slice",
                    )

            sess = self._create_session_locked(
                container_id=container_id, cgroup=cgroup, scope=scope, token=token,
                interval=interval, damon_req=damon_req, meta=meta, policy=policy,
                place_request=place_request, owner_uid=ctx.uid,
            )
            try:
                thread = threading.Thread(
                    target=self._session_loop, args=(sess,), daemon=True,
                    name=f"cgprofile-session-{sess.session_id}",
                )
                sess.thread = thread
                thread.start()
            except BaseException:
                # A failed thread launch has not published a usable session.
                # In particular, do not strand the kdamond acquired at start.
                try:
                    if sess.damon_session is not None:
                        self._close_damon_session(
                            sess.damon_session, session_id=sess.session_id,
                            context="sampler startup failed",
                        )
                finally:
                    self._rollback_failed_start(sess.session_id, sess.placement)
                raise

            self._sessions[sess.session_id] = sess
            if token is not None:
                self._by_target[(container_id, token)] = sess.session_id

            return self._start_response(sess, reused=False)

    def _create_session_locked(
        self, *, container_id: str, cgroup: str, scope: str, token: Optional[str],
        interval: float, damon_req: str, meta: Dict[str, Any],
        policy: Optional["liveness_mod.Policy"] = None,
        place_request: Optional["placement_mod.PlacementRequest"] = None,
        owner_uid: Optional[int] = None,
    ) -> _Session:
        start_state: Dict[str, Any] = {}
        try:
            return self._assemble_session_locked(
                container_id=container_id, cgroup=cgroup, scope=scope,
                token=token, interval=interval, damon_req=damon_req,
                meta=meta, policy=policy, place_request=place_request,
                start_state=start_state, owner_uid=owner_uid,
            )
        except BaseException:
            if start_state.get("created") and not start_state.get("rejected"):
                self._rollback_failed_start(
                    start_state["session_id"], start_state.get("placement"),
                )
            raise

    def _rollback_failed_start(
        self, session_id: str,
        placement_obj: Optional["placement_mod.LanePlacement"],
    ) -> None:
        """Undo placement before discarding its only recovery journal."""
        if placement_obj is not None:
            try:
                placement_obj.release()
            except Exception as exc:  # noqa: BLE001 - keep the journal on failed cleanup
                self._log(f"start: placement rollback failed for {session_id}: {exc}")
            if placement_obj.scope_unit is not None and not placement_obj.released:
                self._log(
                    f"start: preserving placement recovery journal for {session_id}"
                )
                return
        self._remove_session_dir(session_id)

    def _assemble_session_locked(
        self, *, container_id: str, cgroup: str, scope: str, token: Optional[str],
        interval: float, damon_req: str, meta: Dict[str, Any], start_state: Dict[str, Any],
        policy: Optional["liveness_mod.Policy"] = None,
        place_request: Optional["placement_mod.PlacementRequest"] = None,
        owner_uid: Optional[int] = None,
    ) -> _Session:
        session_id = self._session_id_fn()
        if session_id in self._sessions or os.path.lexists(
            os.path.join(self.sessions_dir, session_id)
        ):
            raise OSError(f"session id collision: {session_id}")
        started_at = self._iso(self.clock())
        # The token resolver below will resolve direct target-cgroup PIDs;
        # avoid doing the broader proc-to-cgroup map a second time here.
        pids_now = (
            targets_mod.pids_in_cgroup(cgroup, self.cgroup_root, self.proc_root)
            if token is None else []
        )
        # RW-14: sampled once, at session creation — the manifest's "host"
        # field mirrors `cmd_collect`'s own convention of a single snapshot
        # written into the manifest at start, not updated thereafter.
        host_snapshot = metrics.sample_host(proc_root=self.host_proc_root)
        machine_facts = self._machine(cgroup)
        sampler_origin_mono = self.sampler_clock()

        slice_cgroup = os.path.dirname(cgroup.rstrip("/")) or "/"
        slice_name = os.path.basename(slice_cgroup) if slice_cgroup not in ("", "/") else None
        if slice_name is None:
            slice_cgroup = None
        initial_slice_metrics = (
            summary.sample_slice_cgroup(
                os.path.join(self.cgroup_root, slice_cgroup.lstrip("/"))
            )
            if slice_cgroup else None
        )

        subtree_resolver: Optional[subtree.SubtreeResolver] = None
        if token:
            subtree_resolver = subtree.SubtreeResolver(
                cgroup=cgroup, cgroup_root=self.cgroup_root,
                token=token, proc_root=self.proc_root,
            )
            subtree_resolver.refresh()

        initial_pids = (
            list(subtree_resolver.current_pids)
            if subtree_resolver is not None else list(pids_now)
        )
        pids_at_start = len(initial_pids)

        rundir = store.RunDir(self.sessions_dir, run_id=session_id, create=True)
        start_state.update(session_id=session_id, created=True)
        with open(rundir.stream_path("events"), "a", encoding="utf-8"):
            pass  # touch: the file exists from the first tick even if the
            # session ends before any real row lands (CP-5, `_on_session_sample`
            # is what appends real rows via `lib.events.Detector`).

        # CP-9 (§8.3, D-20/D-25): the leaf, before the first sample — a lane
        # placed after its first tick would have that tick's memory accounted
        # to the devcontainer's scope, which is the number placement exists
        # to stop reporting. Refusals never reach here as exceptions: they
        # are `placement.error` on a session that starts anyway.
        placement_obj: Optional[placement_mod.LanePlacement] = None
        if place_request is not None:
            placement_obj = self._make_placement(
                token=token, origin_cgroup=cgroup, request=place_request, rundir=rundir,
            )
            start_state["placement"] = placement_obj
            placement_obj.apply(
                list(subtree_resolver.current_pids) if subtree_resolver is not None else []
            )
            if subtree_resolver is not None:
                if placement_obj.placed:
                    if placement_obj.error is None:
                        subtree_resolver.owned_cgroup = lambda: (
                            placement_obj.leaf_cgroup if placement_obj.placed else None
                        )
            if placement_obj.error is not None:
                self._log(
                    f"start: placement refused for {session_id}: {placement_obj.error}"
                )
            if (
                policy is not None and policy.on_stall == "kill"
                and scope == "container-shared"
                and (placement_obj.error is not None or not placement_obj.placed)
            ):
                placement_error = placement_obj.error or "place-refused:no-leaf"
                placement_obj.release()
                cleanup_error = (
                    placement_obj.error if placement_obj.placed else None
                )
                refusal = {
                    "session": session_id,
                    "status": "rejected",
                    "scope": scope,
                    "container_id": container_id,
                    "cgroup": cgroup,
                    "token": token,
                    "started_at": started_at,
                    "ended_at": self._iso(self.clock()),
                    "interval_seconds": interval,
                    "aborted_reason": "required-stall-kill-placement-refused",
                    "placement_error": placement_error,
                    "placement_cleanup_error": cleanup_error,
                }
                try:
                    rundir.write_manifest(refusal)
                except OSError as exc:
                    self._log(
                        f"start: could not persist placement refusal for {session_id}: {exc}"
                    )
                detail = f"--on-stall kill requires a verified placement leaf ({placement_error})"
                if cleanup_error is not None:
                    detail += (
                        f"; cleanup was incomplete ({cleanup_error}); "
                        f"leaf {placement_obj.leaf_cgroup!r} remains"
                    )
                # The refusal manifest is the durable public record for this
                # intentional bad-policy response; it is not a failed start
                # artifact to discard during transaction rollback.
                start_state["rejected"] = True
                raise RequestError("bad-policy", detail)

        # After successful placement, sample the profiler-owned leaf: its
        # cgroup counters are the kernel's charges for pages attributed to
        # this leaf since placement, not a claim about every process's total
        # RSS or pages charged before migration. Keep `cgroup` as the logical
        # target key in every stored record; only the source path changes.
        initial_sample_target_abs = self._sample_source_abs(
            cgroup, placement_obj,
        )
        initial_target_metrics = summary.sample_target_cgroup(initial_sample_target_abs)
        baseline = (initial_target_metrics.get("mem") or {}).get("current")

        limits_flags = limits_mod.mount_flags(proc_root=self.proc_root)
        initial_effective_limits = limits_mod.effective(cgroup, self.cgroup_root, limits_flags)
        detector = events_mod.Detector(
            events_mod.DetectorConfig(), {cgroup: initial_effective_limits}, roles={cgroup: "subject"}
        )

        damon_requested_on = damon_req == "on"
        damon_session_obj: Optional[damon_mod.DamonSession] = None
        damon_unavailable_reason: Optional[str] = None
        damon_status = "off"
        if damon_requested_on:
            initial_pids = (
                list(subtree_resolver.current_pids) if subtree_resolver is not None else pids_now
            )
            if not initial_pids:
                damon_unavailable_reason = "no pids to monitor yet"
                damon_status = f"unavailable:{damon_unavailable_reason}"
            else:
                targets = [
                    damon_mod.DamonTarget(kind="vaddr", pid=pid, label=str(pid))
                    for pid in initial_pids
                ]
                candidate = damon_mod.DamonSession(
                    targets, pool=self.damon_pool,
                    aggr_us=max(100_000, int(interval * 1_000_000)),
                )
                try:
                    candidate.__enter__()
                except damon_mod.DamonSessionError as exc:
                    damon_unavailable_reason = str(exc)
                    damon_status = f"unavailable:{damon_unavailable_reason}"
                else:
                    try:
                        # Establish the DAMON sample-zero state while the
                        # start transaction is still assembling its own
                        # sample-zero record. A collector failure is a
                        # per-session unavailability, never a start fault.
                        candidate.collect()
                    except Exception as exc:  # noqa: BLE001 - degrade DAMON only
                        self._close_damon_session(
                            candidate, session_id=session_id,
                            context="initial DAMON collection failed",
                        )
                        damon_unavailable_reason = f"{type(exc).__name__}: {exc}"
                        damon_status = f"unavailable:{damon_unavailable_reason}"
                    else:
                        damon_session_obj = candidate
                        damon_status = "on"

        try:
            acc = summary.SummaryAccumulator(
                session=session_id, daemon_name=self.daemon_name, daemon_version=CGPROFILE_VERSION,
                scope=scope, started_at=started_at, interval_seconds=interval,
                container_id=container_id, cgroup=cgroup, token=token, slice_name=slice_name,
                damon_enabled=damon_requested_on,
                damon_kdamond=damon_session_obj.kdamond_idx if damon_session_obj else None,
                damon_thresholds=damon_session_obj.thresholds if damon_session_obj else None,
                machine=machine_facts,
            )
            if damon_unavailable_reason is not None:
                acc.mark_damon_unavailable(damon_unavailable_reason)

            initial_damon_bytes: Optional[Dict[str, int]] = None
            if damon_session_obj is not None:
                initial_damon_bytes = damon_session_obj.last_class_bytes

            sess = _Session(
                session_id=session_id, scope=scope, container_id=container_id, cgroup=cgroup,
                owner_uid=owner_uid, slice_name=slice_name, slice_cgroup=slice_cgroup, token=token, interval=interval,
                meta=meta, started_at=started_at, baseline_memory_bytes=baseline,
                pids_at_start=pids_at_start, host_snapshot=host_snapshot, machine=machine_facts,
                rundir=rundir,
                summary_acc=acc, subtree_resolver=subtree_resolver, damon_session=damon_session_obj,
                damon_status=damon_status, damon_requested_on=damon_requested_on,
                damon_unavailable_reason=damon_unavailable_reason,
                detector=detector, last_effective_limits=initial_effective_limits,
                watch=liveness_mod.LivenessTracker(
                    policy if policy is not None else liveness_mod.Policy(),
                    started_at=started_at,
                    expected_duration_seconds=_expected_duration_seconds(meta),
                ),
                placement=placement_obj,
                sampler_origin_mono=sampler_origin_mono,
                ancestor_events_start=ancestor_events_mod.read_ancestors(
                    self.cgroup_root, ancestor_events_mod.ancestor_paths(cgroup),
                ),
            )
            # Sample zero is the liveness baseline as well as the summary's
            # initial sample, so status and later deltas share one observation.
            self._observe_liveness(
                sess, mono=0.0,
                record={
                    "seq": 0, "t": self._parse_iso_epoch(started_at), "mono": 0.0,
                    "cg": {cgroup: initial_target_metrics}, "host": host_snapshot,
                },
                abs_target=initial_sample_target_abs, target_metrics=initial_target_metrics,
                host_metrics=host_snapshot, pids=initial_pids,
            )
            # Sample zero is recorded as part of start, from the target/host/slice
            # and PID reads that already established this session's baseline.
            # Therefore an immediate stop has a populated, honest one-sample
            # summary instead of taking a later replacement read.
            acc.add_sample(
                cgroup=initial_target_metrics, host=host_snapshot,
                slice_cgroup=initial_slice_metrics, damon=initial_damon_bytes,
                pids=initial_pids, mono=0.0,
            )
            sess.live_samples = 1
            sess.last_mono = 0.0
            sess.no_token_pids = list(pids_now)
            target_mem = initial_target_metrics.get("mem") or {}
            sess.live_memory_current_bytes = target_mem.get("current")
            sess.live_memory_peak_bytes = target_mem.get("peak")
            target_cpu = initial_target_metrics.get("cpu") or {}
            sess._prev_cpu_usage_usec = target_cpu.get("usage_usec")
            sess._prev_mono = 0.0
            sess.rundir.append("samples", {
                "seq": 0, "t": self._parse_iso_epoch(started_at), "mono": 0.0,
                "cg": {sess.cgroup: initial_target_metrics}, "pids": initial_pids,
            })
            sess.rundir.append("host", {"host": host_snapshot, "slice": initial_slice_metrics})
            if initial_damon_bytes is not None:
                sess.rundir.append("damon", {"_seq": 0, **initial_damon_bytes})
            rundir.write_manifest(self._manifest_for(sess, status="live"))
            return sess
        except BaseException:
            # The session is not yet published. Release any owned kdamond
            # even when storage or summary assembly fails after acquisition.
            # The outer transaction rolls placement back and retains its
            # recovery journal if that rollback cannot be verified.
            if damon_session_obj is not None:
                self._close_damon_session(
                    damon_session_obj, session_id=session_id,
                    context="session start failed",
                )
            raise

    def _make_placement(
        self, *, token: Optional[str], origin_cgroup: str,
        request: "placement_mod.PlacementRequest", rundir: "store.RunDir",
    ) -> "placement_mod.LanePlacement":
        """One lane's placement object, wired to this daemon's roots, its
        gates slice and its `events.jsonl`.

        D-25's "every cgroup write is an `events.jsonl` row" is the `on_write`
        sink: a row per write, in the same file and the same
        :class:`lib.model.Event` shape CP-5's detected events use, so
        `ctl report` renders a cgroup write as a marker on the timeline
        beside the `memory_high_breach` it was meant to prevent. `severity`
        is `info` — the report's colour map has exactly four bands and a
        deliberate write by this daemon is not a warning about the lane.
        """
        def on_write(relative_path: str, value: str) -> None:
            rundir.append("events", events_mod.Event(
                t=self.clock(), mono=0.0, kind="cgroup_write", severity="info",
                target="/" + relative_path,
                message=f"placement wrote {value!r} to /{relative_path}",
                data={"file": "/" + relative_path, "value": value},
            ).to_dict())

        factory = self.placement_factory or placement_mod.LanePlacement
        return factory(
            cgroup_root=self.cgroup_root,
            gates_cgroup=targets_mod.slice_to_path(self.gates_slice_name),
            token=token, origin_cgroup=origin_cgroup, request=request,
            on_write=on_write, log=self._log, sleep=self.sampler_sleep,
            rmdir=self.cgroup_rmdir, proc_root=self.proc_root,
            state_write=lambda state: rundir.write_json("placement-state.json", state),
            slice_unit_verifier=self._verify_gates_slice_unit,
        )

    def _make_container_killer(
        self, *, container_id: str, cgroup: str,
        rundir: Optional["store.RunDir"] = None,
    ) -> "placement_mod.TargetContainerKill":
        """Build the exact-target cgroup.kill writer for an ephemeral lane.

        No Docker API, PID signal, or cgroup migration is involved: D-15
        authorizes only one kill-file write on the runtime cgroup whose leaf
        name proves this exact container ID beneath the verified gates slice.
        """
        on_write = None
        if rundir is not None:
            def on_write(relative_path: str, value: str) -> None:
                rundir.append("events", events_mod.Event(
                    t=self.clock(), mono=0.0, kind="cgroup_write", severity="info",
                    target="/" + relative_path,
                    message=f"container enforcement wrote {value!r} to /{relative_path}",
                    data={"file": "/" + relative_path, "value": value},
                ).to_dict())

        return placement_mod.TargetContainerKill(
            cgroup_root=self.cgroup_root,
            gates_cgroup=targets_mod.slice_to_path(self.gates_slice_name),
            container_cgroup=cgroup, container_id=container_id,
            slice_unit_verifier=self._verify_gates_slice_unit,
            on_write=on_write,
            log=self._log,
        )

    def _verify_gates_slice_unit(self, unit_name: str, cgroup_path: str) -> bool:
        expected_path = targets_mod.slice_to_path(self.gates_slice_name)
        return (
            unit_name == self.gates_slice_name
            and cgroup_path == expected_path
            and self.slice_unit_verifier(unit_name, cgroup_path)
        )

    def _gates_slice_is_verified(self, *, refresh: bool = False) -> bool:
        """Require loaded-unit and finite-capacity proof for the gates slice.

        The five-second cache keeps a one-second sampler from spawning
        `systemctl` on every tick. Placement and public host snapshots refresh
        before making a consequential decision or presence claim.
        """
        now = time.monotonic()
        with self._gates_slice_verify_lock:
            cached = self._gates_slice_verification
            if not refresh and cached is not None and now - cached[0] < 5.0:
                return cached[1]
            cgroup_path = targets_mod.slice_to_path(self.gates_slice_name)
            abs_path = os.path.join(self.cgroup_root, cgroup_path.lstrip("/"))
            verified = (
                os.path.isdir(abs_path)
                and self._verify_gates_slice_unit(self.gates_slice_name, cgroup_path)
                and placement_mod.bounded_slice_capacity(self.cgroup_root, cgroup_path)
            )
            self._gates_slice_verification = (now, verified)
            return verified

    def _start_response(self, sess: _Session, *, reused: bool) -> Dict[str, Any]:
        return {
            "ok": True,
            "contract": CONTRACT_VERSION,
            "session": sess.session_id,
            "reused": reused,
            "started_at": sess.started_at,
            "scope": sess.scope,
            "damon": sess.damon_status,
            "interval_seconds": sess.interval,
            "target": {
                "container_id": sess.container_id,
                "cgroup": sess.cgroup,
                "baseline_memory_bytes": sess.baseline_memory_bytes,
                "pids_at_start": sess.pids_at_start,
                "token": sess.token,
            },
            # §8.3, additive under `contract: 1`: the block when `--place`
            # was asked for (refused or not), `null` when it was not — the
            # same union §8.2's `reading` line publishes.
            "placement": _placement_block(sess),
        }

    # ── per-session sampling thread ──────────────────────────────────────

    def _sample_source_abs(
        self, cgroup: str, placed: Optional["placement_mod.LanePlacement"],
    ) -> str:
        """The exact metric source, preserving the target's logical key.

        Only a complete placement is a valid leaf accounting source. A
        refused or partially completed placement continues to read the
        original target cgroup and exposes the placement error separately.
        """
        if placed is not None and placed.placed and placed.error is None:
            leaf_abs = placed.leaf_abs
            if leaf_abs is not None:
                return leaf_abs
        return os.path.join(self.cgroup_root, cgroup.lstrip("/"))

    def _session_loop(self, sess: _Session) -> None:
        abs_target = self._sample_source_abs(sess.cgroup, sess.placement)
        abs_slice = (
            os.path.join(self.cgroup_root, sess.slice_cgroup.lstrip("/"))
            if sess.slice_cgroup else None
        )
        target = targets_mod.Target(
            key="target", cgroup=sess.cgroup, label=sess.session_id,
            kind="container", spec=sess.cgroup,
        )
        membership = targets_mod.Membership([target], root=self.cgroup_root)
        membership.refresh()

        def sample_fn(_membership: targets_mod.Membership) -> Dict[str, Any]:
            return {
                "cg": {
                    sess.cgroup: summary.sample_target_cgroup(
                        self._sample_source_abs(sess.cgroup, sess.placement)
                    )
                },
                "host": metrics.sample_host(proc_root=self.host_proc_root),
            }

        config = sampler_mod.SamplerConfig(
            hot_interval=sess.interval,
            idle_interval=sess.interval,
            # A score can never reach 2.0 (activity_score's contract is
            # `[0, 1]`) — this daemon's cadence is fixed by contract §2.2,
            # so the adaptive hot/idle split in `sampler.py` must never
            # actually change the interval it already computed above.
            hot_threshold=2.0,
            discovery_interval=DISCOVERY_INTERVAL_SECONDS,
        )
        smp = sampler_mod.Sampler(
            membership, config, sample_fn, clock=self.sampler_clock, sleep=self.sampler_sleep,
            start_mono=sess.sampler_origin_mono,
        )

        def on_sample(record: Dict[str, Any]) -> None:
            self._on_session_sample(sess, record, abs_target, abs_slice)

        try:
            # The synchronous start read is discovery at t=0. The first
            # sampler tick must therefore measure the two-second discovery
            # cadence from that baseline, rather than restarting the cadence
            # at the first post-start tick.
            sess.last_discovery_mono = 0.0
            # Sample zero was recorded synchronously at start. Advance one
            # cadence before the loop so its first tick is the next sample,
            # not a duplicate of sample zero.
            if not sess.stop_event.is_set():
                self.sampler_sleep(sess.interval)
            smp.run(lambda: sess.stop_event.is_set(), on_sample, self._on_topology_noop)
        except Exception as exc:  # noqa: BLE001 - a session thread must not die silently
            with self._lock:
                if not sess.finished:
                    sess.error = str(exc)
                    # Runs on the session's own thread: the join is skipped,
                    # so it cannot return False for a live worker (Sol B2). It
                    # CAN return False when the bounded session-lock acquire
                    # times out; the return is ignored here and the failure is
                    # logged inside `_finalize_session_locked` (CP-27 N6).
                    self._finalize_session_locked(sess, aborted_reason=f"session-error:{exc}")

    def _on_topology_noop(self, appeared: List[str], disappeared: List[str]) -> None:
        """``Sampler.run``'s third callback. The daemon tracks pid-subtree
        topology itself (``_on_session_sample``'s own discovery-interval
        check against ``SubtreeResolver``) — a CGROUP appearing/disappearing
        under the single fixed target ``Membership`` this session watches is
        not a case the daemon reacts to further, so this is a deliberate
        no-op rather than an unused parameter to ``Sampler.run``."""
        return None

    def _on_session_sample(
        self, sess: _Session, record: Dict[str, Any], abs_target: str, abs_slice: Optional[str],
    ) -> None:
        with sess.lock:
            if sess.closed or sess.finished:
                # A wedged worker that outlived (or raced) finalization must
                # not touch the session's resources or records.
                return
        mono = record.get("mono", 0.0)
        target_metrics = (record.get("cg") or {}).get(sess.cgroup, {})
        host_metrics = record.get("host") or {}
        slice_metrics = summary.sample_slice_cgroup(abs_slice) if abs_slice else None

        if sess.subtree_resolver is not None:
            due = (
                sess.last_discovery_mono is None
                or (mono - sess.last_discovery_mono) >= DISCOVERY_INTERVAL_SECONDS
            )
            if due:
                previous_pids = set(sess.subtree_resolver.current_pids)
                pids = list(sess.subtree_resolver.refresh())
                sess.last_discovery_mono = mono
                if (
                    sess.damon_session is not None
                    and set(pids) != previous_pids
                ):
                    try:
                        self._run_if_open(
                            sess, lambda: sess.damon_session.recommit_targets(pids))
                    except Exception as exc:  # noqa: BLE001 - profiling is best-effort
                        self._disable_damon_for_session(sess, exc)
            else:
                pids = list(sess.subtree_resolver.current_pids)
        else:
            # RW-15: the no-token path re-discovers `cgroup.procs` on the
            # SAME discovery cadence as the token path (rather than every
            # sample tick — the daemon's own default interval can be as low
            # as 0.25 s, and re-reading + potentially recommitting DAMON
            # targets that often for a subtree that rarely changes is pure
            # waste) and recommits DAMON targets only when the pid set
            # actually changed (a cheap set-diff — there is no
            # SubtreeResolver object to own this cache without a token, so
            # `sess.no_token_pids` is it).
            due = (
                sess.last_discovery_mono is None
                or (mono - sess.last_discovery_mono) >= DISCOVERY_INTERVAL_SECONDS
            )
            if due:
                new_pids = targets_mod.pids_in_cgroup(
                    sess.cgroup, self.cgroup_root, self.proc_root,
                )
                sess.last_discovery_mono = mono
                if sess.damon_session is not None and set(new_pids) != set(sess.no_token_pids):
                    try:
                        self._run_if_open(
                            sess, lambda: sess.damon_session.recommit_targets(new_pids))
                    except Exception as exc:  # noqa: BLE001 - profiling is best-effort
                        self._disable_damon_for_session(sess, exc)
                sess.no_token_pids = new_pids
            pids = sess.no_token_pids

        # CP-9 (§8.3): "migrate every pid the token resolver discovers — also
        # pids found later". The resolver keeps finding descendants for as
        # long as the lane forks, and a pid left behind in the devcontainer's
        # scope is a pid whose memory is not the lane's. Same discovery
        # cadence as the resolution above (`migrate` skips what it already
        # moved, so this is a set difference, not a re-write per tick).
        if due and sess.placement is not None and sess.placement.placed:
            self._run_if_open(sess, lambda: sess.placement.migrate(pids))

        # CP-5: limit_drift is the one Detector event kind observe() cannot
        # produce on its own (it needs an old/new lib.limits.Effective pair,
        # not a metrics sample) -- refreshed on the same discovery cadence as
        # pid resolution above, not every tick, for the same "0.25s interval,
        # ancestor-chain walk" reason RW-15 gives for the pid-cache split.
        if due and sess.detector is not None:
            limits_flags = limits_mod.mount_flags(proc_root=self.proc_root)
            new_effective_limits = limits_mod.effective(sess.cgroup, self.cgroup_root, limits_flags)
            if sess.last_effective_limits is not None:
                drift_t = record.get("t") or time.time()
                for event in sess.detector.limits_changed(
                    sess.cgroup, sess.last_effective_limits, new_effective_limits, drift_t, mono,
                ):
                    self._run_if_open(
                        sess, lambda event=event: sess.rundir.append("events", event.to_dict()))
            sess.last_effective_limits = new_effective_limits

        damon_bytes: Optional[Dict[str, int]] = None
        if sess.damon_session is not None:
            damon_session = sess.damon_session
            try:
                def collect_damon() -> None:
                    nonlocal damon_bytes
                    damon_session.collect()
                    damon_bytes = damon_session.last_class_bytes

                self._run_if_open(sess, collect_damon)
            except Exception as exc:  # noqa: BLE001 - profiling is best-effort
                self._disable_damon_for_session(sess, exc)

        with sess.lock:
            if sess.closed:
                return
            sess.summary_acc.add_sample(
                cgroup=target_metrics, host=host_metrics, slice_cgroup=slice_metrics,
                damon=damon_bytes, pids=pids, mono=mono,
            )
            sess.live_samples += 1
            sess.last_mono = mono
            mem = target_metrics.get("mem") or {}
            sess.live_memory_current_bytes = mem.get("current")
            sess.live_memory_peak_bytes = mem.get("peak")
            cpu = target_metrics.get("cpu") or {}
            usage = cpu.get("usage_usec")
            # `dt_since_prev` is the elapsed-time gate for event detection --
            # independent of whether `usage_usec` happened to be present this
            # tick (the cpu-rate calc below has its own, narrower gate on
            # `_prev_cpu_usage_usec`; tying event detection to that too would
            # silently skip observe() on any tick where cpu accounting is
            # unavailable, which is not the same condition).
            dt_since_prev: Optional[float] = (
                mono - sess._prev_mono if sess._prev_mono is not None else None
            )
            if sess._prev_cpu_usage_usec is not None and dt_since_prev is not None:
                rate = util.rate(sess._prev_cpu_usage_usec, usage, dt_since_prev)
                sess.live_cpu_cores_recent = None if rate is None else rate / 1_000_000.0
            # CP-5: same detector cmd_collect's own `on_sample` closure uses
            # (cgprofile.py), same call shape (`observe(prev, cur, dt)`) --
            # `record` already carries the `{"cg": {cgroup: metrics}}` wrapper
            # Detector.observe expects because `sample_fn` above builds it
            # that way for `lib.analyze.to_frame` (RW-14). The Summary's own
            # `events` counters (`summary_acc.add_sample` above) are computed
            # independently of this -- this only ever appends to
            # events.jsonl, never touches summary_acc.
            detected_events: List["events_mod.Event"] = []
            if sess.detector is not None and sess._prev_record is not None and dt_since_prev is not None and dt_since_prev > 0:
                detected_events = sess.detector.observe(sess._prev_record, record, dt_since_prev)
            sess._prev_cpu_usage_usec = usage
            sess._prev_mono = mono
            sess._prev_record = record
            if damon_bytes is not None:
                sess.live_damon_hot_bytes_recent = damon_bytes.get("hot")

        # CP-8 (§8.4): the watcher runs on the DISCOVERY cadence, not the
        # sample cadence — it reads `/proc/<pid>/stat` for every pid of the
        # subtree, `io.stat`, the gates slice's `memory.pressure` and (when
        # a progress stream is authored) up to 64 KiB of it, and this
        # daemon's sample interval can be as low as 0.25 s. Same reasoning,
        # and the same `due` flag, as RW-15's pid re-discovery and CP-5's
        # limit refresh above. The idle bounds it judges against start at
        # 300 s, so a 2 s judging cadence loses nothing.
        if due:
            self._observe_liveness(
                sess, mono=mono, record=record, abs_target=abs_target,
                target_metrics=target_metrics, host_metrics=host_metrics, pids=pids,
            )

        for event in detected_events:
            self._run_if_open(
                sess, lambda event=event: sess.rundir.append("events", event.to_dict()))

        # RW-14: persisted keyed by cgroup path, one row per `mono` tick —
        # the same on-disk shape `cgprofile run`'s own collector writes
        # (cgprofile.py's `cmd_collect`, `sample_fn`'s "cg": {path: entry}),
        # which is what `lib.analyze.to_frame` requires (it keys strictly on
        # `record["cg"]` being `{cgroup_path: entry}`) and therefore what
        # `handle_report`'s subprocess needs this session directory to look
        # like. An earlier draft of this method wrote only the single
        # target's already-unwrapped flat metric dict with no "cg" wrapper
        # and no "mono" at all — silently unreadable by `to_frame`, caught
        # by RW-14 before it shipped. `_finalize_orphan`'s replay path reads
        # this same shape back (see its own RW-14 comment).
        def append_records() -> None:
            sess.rundir.append("samples", {
                "seq": record.get("seq"), "t": record.get("t"), "mono": mono,
                "cg": {sess.cgroup: target_metrics},
            })
            sess.rundir.append("host", {"host": host_metrics, "slice": slice_metrics})
            if damon_bytes is not None:
                if isinstance(record.get("seq"), int):
                    sess.rundir.append("damon", {"_seq": record.get("seq"), **damon_bytes})
                else:
                    # Direct unit callers may provide a sample record without
                    # a sampler sequence; retain the historical flat shape for
                    # those synthetic records.
                    sess.rundir.append("damon", damon_bytes)

        self._run_if_open(sess, append_records)

    @staticmethod
    def _run_if_open(sess: _Session, action: Callable[[], Any]) -> bool:
        """Run ``action`` under ``sess.lock`` only while the session is not
        closed (Sol S6). Finalization sets ``closed`` under the same lock
        before releasing placement and closing DAMON, so an action either
        completes before the release begins or is not run at all."""
        with sess.lock:
            if sess.closed:
                return False
            action()
            return True

    def _close_damon_session(
        self, damon_session: Any, *, session_id: str, context: str,
    ) -> Dict[str, Any]:
        """Close one DAMON owner without letting optional cleanup alter a verdict.

        Returns the cleanup disposition (Sol B3): ``confirmed`` is True only
        when the owner affirmatively reports a verified teardown. An exception
        from ``__exit__`` or a missing confirmation is "unconfirmed", which a
        consumer must be able to tell apart from "off"."""
        try:
            damon_session.__exit__(None, None, None)
        except Exception as cleanup_exc:  # noqa: BLE001 - cleanup cannot affect verdict
            self._log(
                f"session {session_id}: DAMON cleanup raised during {context}: "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            )
        confirmed = bool(getattr(damon_session, "cleanup_confirmed", False))
        slot_idx = getattr(damon_session, "kdamond_idx", None)
        disposition_doc: Dict[str, Any] = {
            "confirmed": confirmed, "slot": slot_idx, "quarantined": False,
        }
        if not confirmed:
            pool = self.damon_pool
            quarantined = set(getattr(pool, "quarantined_indices", ()))
            if slot_idx is not None and slot_idx in quarantined:
                disposition_doc["quarantined"] = True
                disposition = (
                    f"owned slot {slot_idx} is quarantined and will not be reused"
                )
            else:
                disposition = (
                    f"cleanup remains unverified; slot {slot_idx!r} is not recorded "
                    f"as quarantined (pool quarantine={sorted(quarantined)}); "
                    "no no-reuse guarantee is established"
                )
            self._log(
                f"session {session_id}: DAMON stop is unconfirmed during {context}; "
                f"{disposition}"
            )
        return disposition_doc

    def _disable_damon_for_session(self, sess: _Session, exc: Exception) -> None:
        """Stop only DAMON after a runtime profiling failure.

        DAMON is optional evidence, not part of the measured program's
        verdict. A late sysfs error (for example, a PID disappearing during
        an online target commit) must therefore leave cgroup, CPU, memory,
        and liveness sampling active. Detach the session before teardown so
        finalization cannot close it twice, and persist the unavailable
        reason in the eventual summary/manifest.
        """
        reason = f"{type(exc).__name__}: {exc}"
        with sess.lock:
            damon_session = sess.damon_session
            if damon_session is None or sess.closed:
                # Closed: finalization owns (and is closing) the DAMON
                # session; a second close would overwrite its disposition.
                return
            sess.damon_session = None
            sess.damon_status = f"unavailable:{reason}"
            sess.damon_unavailable_reason = reason
            sess.summary_acc.mark_damon_unavailable(reason)
            # CP-27 N10: close WHILE holding the session lock. Finalization
            # takes the same lock before it reads `damon_session` and
            # `damon_cleanup`, so it either sees the session still attached
            # (and closes it itself) or waits and reads the finished
            # disposition; the Summary can no longer omit `damon_cleanup`,
            # and the late close can no longer land after the manifest.
            sess.damon_cleanup = self._close_damon_session(
                damon_session, session_id=sess.session_id,
                context=f"runtime failure ({reason})",
            )
        self._log(f"session {sess.session_id}: DAMON disabled after runtime failure: {reason}")

    # ── CP-8: liveness, the watch state machine, enforcement (§8.4) ──────

    def _observe_liveness(
        self, sess: _Session, *, mono: float, record: Dict[str, Any], abs_target: str,
        target_metrics: Dict[str, Any], host_metrics: Dict[str, Any], pids: List[int],
    ) -> None:
        """One tick of :class:`lib.liveness.LivenessTracker` for ``sess``,
        and the enforcement `--on-stall kill` asks for."""
        tracker = sess.watch
        if tracker is None:  # pragma: no cover - every session gets one
            return
        with sess.lock:
            if sess.closed:
                # Finalization has begun: a stall kill must not reach a
                # leaf that release is restoring or has removed (Sol S7).
                return
        # The daemon's OWN wall clock, not the sampler's `record["t"]`:
        # every other timestamp a consumer reads (`status.at`, `started_at`,
        # `ended_at`) comes from `self.clock()`, and `last_activity_at` is
        # compared against those.
        at = self._iso(self.clock())

        stream_sample: Optional["liveness_mod.StreamSample"] = None
        if tracker.policy.progress_stream is not None and pids:
            # "read through `/proc/<first token pid>/root/<path>`" — the
            # lowest pid of the subtree is the lane's own process (the
            # resolver's owners are found before their descendants and pids
            # are allocated in order), and any pid of the subtree would do:
            # they share the lane's mount namespace, which is the only thing
            # this path needs.
            stream_sample = liveness_mod.read_progress_stream(
                liveness_mod.resolve_stream_path(
                    self.proc_root, min(pids), tracker.policy.progress_stream
                ),
                previous=tracker.stream,
            )

        if sess.token is not None:
            # Scope `container-shared`: the cgroup is the whole devcontainer
            # and is permanently busy, so its CPU says nothing about the
            # LANE. The token subtree is the lane.
            cpu_seconds = liveness_mod.subtree_cpu_seconds(pids, self.proc_root)
        else:
            usage_usec = (target_metrics.get("cpu") or {}).get("usage_usec")
            cpu_seconds = None if usage_usec is None else float(usage_usec) / 1_000_000.0

        host_psi = ((host_metrics.get("psi") or {}).get("memory") or {}).get("full_avg10")
        # CP-9 wires C7's two waiting seams: §8.4's `throttled` is "the LEAF's
        # memory.pressure full avg10 > 20 while memory.high is applied", so
        # it is unreachable — correctly — for a session with no leaf. An
        # unplaced session keeps the tracker's defaults (no leaf PSI, no
        # `memory.high`), which is not "no pressure": it is "no per-lane
        # pressure reading exists", and the tracker treats the two the same
        # way because neither may produce a `throttled` verdict.
        leaf = (
            sess.placement.leaf_readings()
            if sess.placement is not None and sess.placement.placed
            else {"psi_full_avg10": None, "memory_high_applied": False}
        )
        sample = liveness_mod.LivenessSample(
            mono=mono, at=at, elapsed_seconds=mono,
            cpu_seconds_total=cpu_seconds,
            io_bytes_total=liveness_mod.cgroup_io_bytes(abs_target),
            stream=stream_sample,
            host_psi_full_avg10=host_psi,
            slice_psi_full_avg10=self._gates_slice_psi_full_avg10(),
            subtree_alive=bool(pids),
            leaf_psi_full_avg10=leaf["psi_full_avg10"],
            leaf_memory_high_applied=leaf["memory_high_applied"],
        )
        with sess.lock:
            tracker.observe(sample)
            kill_now = tracker.kill_requested
        if kill_now:
            with sess.lock:
                still_open = not sess.closed
            if still_open:
                self._enforce_stall_kill(sess, pids)

    def _gates_slice_psi_full_avg10(self) -> Optional[float]:
        """The gates slice's own memory `full avg10` — half of §8.4's pause
        condition (the other half is host PSI, already in every sample).
        `None` when the slice does not exist on this host, which pauses
        nothing: an absent capacity object is not pressure."""
        if not self._gates_slice_is_verified():
            return None
        cgroup_path = targets_mod.slice_to_path(self.gates_slice_name)
        abs_path = os.path.join(self.cgroup_root, cgroup_path.lstrip("/"))
        return util.read_pressure(os.path.join(abs_path, "memory.pressure")).get("full_avg10")

    @staticmethod
    def _kill_targets(sess: _Session, pids: List[int]) -> Tuple[List[int], Optional[str]]:
        """Whether the daemon has an exact cgroup boundary it may kill.

        Numeric PID signalling is never an enforcement boundary, even though
        the daemon uses the host PID namespace for DAMON target resolution. A
        shared-scope token is killable only after successful placement into
        its dedicated leaf; an unplaced token never falls back to PID signals.
        """
        if sess.scope == "container":
            return list(pids), None
        if sess.token is None:
            return [], "no-token-in-shared-scope"
        if sess.placement is None or not sess.placement.placed:
            return [], "unplaced-token-subtree"
        if sess.placement.error is not None:
            return [], "placement-incomplete"
        return list(pids), None

    def _enforce_stall_kill(self, sess: _Session, pids: List[int]) -> None:
        def refused(reason: str) -> None:
            with sess.lock:
                sess.watch.record_kill_refused(reason)
            self._log(
                f"watch {sess.session_id}: --on-stall kill refused ({reason}); "
                "no exact, verified lane cgroup was killable"
            )

        if sess.scope == "container":
            killer = self._make_container_killer(
                container_id=sess.container_id, cgroup=sess.cgroup, rundir=sess.rundir,
            )
            if not killer.kill():
                refused(killer.refusal_reason or "target-container-kill-refused")
                return
            with sess.lock:
                sess.watch.record_kill(
                    pids, via=f"cgroup.kill applied to exact target {sess.cgroup}",
                )
            self._log(
                f"watch {sess.session_id}: state {sess.watch.state}, cgroup.kill written "
                f"to exact target {sess.cgroup}"
            )
            self._finalize_after_kill(sess)
            return

        targets, refusal = self._kill_targets(sess, pids)
        if refusal is not None:
            refused(refusal)
            return
        assert sess.placement is not None  # `_kill_targets` proved this boundary.
        if not sess.placement.kill():
            refused("lane-leaf-cgroup-kill-refused")
            return
        with sess.lock:
            sess.watch.record_kill(
                targets,
                via=f"cgroup.kill applied to {sess.placement.leaf_cgroup}",
            )
        self._log(
            f"watch {sess.session_id}: state {sess.watch.state}, cgroup.kill written "
            f"to {sess.placement.leaf_cgroup}"
        )
        self._finalize_after_kill(sess)

    def _finalize_after_kill(self, sess: _Session) -> None:
        """CP-9/CP-8 (§8.2/§8.4): an ENFORCED kill (``sess.watch.verdict ==
        VERDICT_KILLED``, set by ``record_kill`` just above — never by
        ``record_kill_refused``, whose lane is still running) ends the
        lane, so it ends the SESSION too: `--on-stall kill` with nothing
        left to observe is not a reason to keep sampling a dead cgroup.
        Before this, only `stop`/shutdown/a session-loop crash ever called
        `_finalize_session_locked`, so an enforced kill left `sess.finished`
        False forever — `_stream_watch`'s own `VERDICT_KILLED -> "killed"`
        end-reason branch was unreachable, `watch`'s §8.2 "exactly one end,
        last" promise never resolved (a real probe against the live daemon,
        RG-55 P6 session 8, ran a killing on-stall session and read stalled
        `reading` events for two more minutes with no `end`), and the
        session held its slot forever. Called from `_enforce_stall_kill`,
        which always runs on `sess`'s OWN sampler thread (`_session_loop`),
        exactly like the session-loop's own crash-path call to
        `_finalize_session_locked` — so the `sess.thread is not
        threading.current_thread()` guard inside it already skips the
        self-join; only the server-wide lock needs acquiring here."""
        with self._lock:
            if not sess.finished:
                # Called from the session's own sampler thread: the join is
                # skipped, so it cannot return False for a live worker (Sol
                # B2). It CAN return False when the bounded session-lock
                # acquire times out; the return is ignored here and the
                # failure is logged inside `_finalize_session_locked`
                # (CP-27 N6).
                self._finalize_session_locked(sess, aborted_reason=None)
        self._run_retention()

    # ── verb: status / host ──────────────────────────────────────────────

    def handle_status(self, args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        session_id = args.get("session")
        state = args.get("state")
        if state is not None:
            if ctx.contract < 2:
                raise RequestError(
                    "bad-argument", "status 'state' exists only at contract 2 (request is contract 1)"
                )
            if state not in ("live", "done", "all"):
                raise RequestError("bad-argument", "status 'state' must be live, done or all")
        if session_id is None:
            with self._lock:
                entries = [
                    self._status_entry(s) for s in self._sessions.values() if not s.finished
                ]
            if ctx.contract >= 2:
                for entry in entries:
                    entry["state"] = "live"
                    entry["owner_uid"] = self._sessions[entry["session"]].owner_uid
                if state in ("done", "all"):
                    stored = self._stored_entries()
                    entries = stored if state == "done" else entries + stored
                    entries.sort(key=lambda entry: entry["session"], reverse=True)
                    entries = entries[: self.keep_sessions]
            return {
                "ok": True, "contract": CONTRACT_VERSION, "at": self._iso(self.clock()),
                "sessions": entries, "host": self._host_snapshot(),
                **({"socket_activation": self._socket_activation_state()} if ctx.contract >= 2 else {}),
            }
        if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
            raise RequestError("unknown-session", f"no session {session_id!r} is live or on record")
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None or sess.finished:
                raise RequestError(
                    "unknown-session", f"no session {session_id} is live or on record"
                )
            entry = self._status_entry(sess)
            if ctx.contract >= 2:
                entry["state"] = "live"
                entry["owner_uid"] = sess.owner_uid
        return {
            "ok": True, "contract": CONTRACT_VERSION, "at": self._iso(self.clock()),
            "session": entry,
            "host": self._host_snapshot(),
            **({"socket_activation": self._socket_activation_state()} if ctx.contract >= 2 else {}),
        }

    def _stored_entries(self) -> List[Dict[str, Any]]:
        """Finished or aborted sessions on disk (contract-2 `status --state`)."""
        try:
            names = os.listdir(self.sessions_dir)
        except OSError:
            return []
        with self._lock:
            live_ids = {s.session_id for s in self._sessions.values() if not s.finished}
        entries: List[Dict[str, Any]] = []
        for name in names:
            if name in live_ids or not _SESSION_ID_RE.match(name):
                continue
            manifest = self._read_manifest(name)
            if manifest is None or manifest.get("status") not in ("finished", "aborted"):
                continue
            owner = manifest.get("owner_uid")
            entries.append({
                "session": name,
                "state": "aborted" if manifest["status"] == "aborted" else "done",
                "scope": manifest.get("scope"),
                "container_id": manifest.get("container_id"),
                "token": manifest.get("token"),
                "started_at": manifest.get("started_at"),
                "ended_at": manifest.get("ended_at"),
                "owner_uid": owner if type(owner) is int else None,
            })
        return entries

    def _status_entry(self, sess: _Session) -> Dict[str, Any]:
        with sess.lock:
            if sess.damon_session is not None:
                damon_live = {"status": "on", "hot_bytes_recent": sess.live_damon_hot_bytes_recent}
            elif sess.damon_requested_on:
                damon_live = {"status": "unavailable", "hot_bytes_recent": None}
            else:
                damon_live = None
            return {
                "session": sess.session_id,
                "started_at": sess.started_at,
                "scope": sess.scope,
                "elapsed_seconds": round(sess.last_mono, 3) if sess.last_mono is not None else 0.0,
                "target": {
                    "container_id": sess.container_id,
                    "cgroup": sess.cgroup,
                    "token": sess.token,
                    "targets_seen": sess.summary_acc.targets_seen,
                },
                "meta": sess.meta,
                "live": {
                    "memory_current_bytes": sess.live_memory_current_bytes,
                    "memory_peak_bytes": sess.live_memory_peak_bytes,
                    "cpu_cores_recent": sess.live_cpu_cores_recent,
                    "samples": sess.live_samples,
                    "damon": damon_live,
                },
                # CP-8, contract §8.4: additive under `contract: 1` and
                # ALWAYS present (a session under the default policy still
                # reports its liveness — that is what makes `status` an
                # answer to "is the lane alive?" rather than "how much
                # memory is it using?"). The v1 status golden is compared
                # after stripping these two keys, the same treatment C5/C6
                # gave `host`'s and `version`'s new blocks.
                "liveness": sess.watch.liveness_block() if sess.watch else None,
                "watch": sess.watch.watch_block() if sess.watch else None,
                # CP-9, §8.3 — same additive treatment, same union (`null`
                # when `--place` was never asked for).
                "placement": _placement_block(sess),
            }

    def handle_host(self, args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        return {"ok": True, "contract": CONTRACT_VERSION, "host": self._host_snapshot()}

    def _host_snapshot(self) -> Dict[str, Any]:
        host = metrics.sample_host(proc_root=self.host_proc_root)
        meminfo = host.get("meminfo") or {}
        psi = host.get("psi") or {}

        slice_names = ["dev.slice"]
        for child in targets_mod.list_children("dev.slice", root=self.cgroup_root):
            name = os.path.basename(child)
            if name.endswith(".slice") and name not in slice_names:
                slice_names.append(name)
        for name in self.observe_slices:
            if name not in slice_names:
                slice_names.append(name)

        slices: Dict[str, Any] = {}
        for name in slice_names:
            cgroup_path = targets_mod.slice_to_path(name)
            abs_path = os.path.join(self.cgroup_root, cgroup_path.lstrip("/"))
            slices[name] = self._slice_snapshot(cgroup_path, abs_path)

        cpu = host.get("cpu") or {}
        hz = util.clk_tck()

        def cpu_seconds(key: str) -> Optional[float]:
            jiffies = cpu.get(key)
            return None if jiffies is None else round(jiffies / hz, 3)

        return {
            "at": self._iso(self.clock()),
            # CP-1: additive. Counters are cumulative since boot.
            "machine": self._machine(),
            "steal_seconds": cpu_seconds("steal_jiffies"),
            "cpu_seconds_total": cpu_seconds("accounted_jiffies"),
            "loadavg": host.get("loadavg"),
            "meminfo": {
                "total_bytes": meminfo.get("MemTotal"),
                "available_bytes": meminfo.get("MemAvailable"),
                "swap_total_bytes": meminfo.get("SwapTotal"),
                "swap_free_bytes": meminfo.get("SwapFree"),
            },
            "pressure": {
                "memory": self._pressure_snapshot(psi.get("memory") or {}),
                "cpu": self._pressure_snapshot(psi.get("cpu") or {}),
                "io": self._pressure_snapshot(psi.get("io") or {}),
            },
            "slices": slices,
            # RG-55 C5 (contract §8.5, D-29) -- additive under contract: 1;
            # existing consumers ignore the two new keys.
            "gates_slice": self._gates_slice_snapshot(),
            "daemon_slice": self._daemon_slice_snapshot(),
        }

    def _gates_slice_snapshot(self) -> Dict[str, Any]:
        """mdt's `dev-gates.slice` (default; `serve --gates-slice` renames
        it) -- the capacity object whose direct children are delegated
        `rg-profile-*.scope` units, each holding a profiler-owned `rg-*` leaf.
        `present: false` and nothing else when the slice does not
        exist on this host at all (host-setup's P8 unit not installed and
        nothing has ever rendered a child under it either) -- contract §8.5's
        own two-shape union, so a consumer can branch on one key."""
        cgroup_path = targets_mod.slice_to_path(self.gates_slice_name)
        abs_path = os.path.join(self.cgroup_root, cgroup_path.lstrip("/"))
        if not self._gates_slice_is_verified(refresh=True):
            return {"name": self.gates_slice_name, "present": False}
        leaves: List[str] = []
        for scope_path in targets_mod.list_children(cgroup_path, root=self.cgroup_root):
            scope_name = os.path.basename(scope_path)
            if not scope_name.startswith("rg-profile-") or not scope_name.endswith(".scope"):
                continue
            leaves.extend(
                os.path.basename(child)
                for child in targets_mod.list_children(scope_path, root=self.cgroup_root)
                if os.path.basename(child).startswith("rg-")
            )
        leaves.sort()
        psi_mem = util.read_pressure(os.path.join(abs_path, "memory.pressure"))
        return {
            "name": self.gates_slice_name,
            "cgroup": cgroup_path,
            "present": True,
            "memory_max_bytes": util.read_int(os.path.join(abs_path, "memory.max")),
            "memory_high_bytes": util.read_int(os.path.join(abs_path, "memory.high")),
            "memory_current_bytes": util.read_int(os.path.join(abs_path, "memory.current")),
            "memory_swap_current_bytes": util.read_int(
                os.path.join(abs_path, "memory.swap.current")
            ),
            "pressure": {"memory": self._pressure_snapshot(psi_mem)},
            # `leaves` is read straight off disk, not from session bookkeeping.
            # A direct rg-* child is invalid: the delegated scope owns the
            # writable boundary between the systemd slice and profiler leaf.
            "leaves": leaves,
            "sessions_live": len(leaves),
        }

    def _daemon_slice_snapshot(self) -> Dict[str, Any]:
        """The daemon's OWN top-level `cgprofile.slice` (D-29) -- a sibling
        of `dev.slice`, never nested under it. Reports exactly what is on
        disk; when `infra/cgprofile.slice` is not installed, systemd still
        auto-vivifies the slice (any container naming it as `cgroup_parent`
        forces that), just unbounded -- so `memory.min`/`memory.high` read
        back as the cgroup v2 defaults (`0` / unset -> `None` via
        `util.read_int`'s own `max`-is-None convention) rather than the
        unit's authored values. There is no separate "installed" bit to
        report (nothing in this container can see `/etc/systemd/system`) --
        `ctl host`/`doctor` read "unbounded" straight off these numbers."""
        cgroup_path = targets_mod.slice_to_path(DAEMON_SLICE_NAME)
        abs_path = os.path.join(self.cgroup_root, cgroup_path.lstrip("/"))
        return {
            "cgroup": cgroup_path,
            "memory_min_bytes": util.read_int(os.path.join(abs_path, "memory.min")),
            "memory_high_bytes": util.read_int(os.path.join(abs_path, "memory.high")),
        }

    @staticmethod
    def _pressure_snapshot(raw: Dict[str, float]) -> Dict[str, Optional[float]]:
        def seconds(key: str) -> Optional[float]:
            total = raw.get(key)
            return None if total is None else round(total / 1e6, 3)

        return {
            "some_avg10": raw.get("some_avg10"),
            "some_avg60": raw.get("some_avg60"),
            "some_avg300": raw.get("some_avg300"),
            "some_total_seconds": seconds("some_total"),
            "full_avg10": raw.get("full_avg10"),
            "full_avg60": raw.get("full_avg60"),
            "full_avg300": raw.get("full_avg300"),
            "full_total_seconds": seconds("full_total"),
        }

    def _slice_snapshot(self, cgroup_path: str, abs_path: str) -> Dict[str, Any]:
        psi_mem = util.read_pressure(os.path.join(abs_path, "memory.pressure"))
        psi_cpu = util.read_pressure(os.path.join(abs_path, "cpu.pressure"))
        return {
            "cgroup": cgroup_path,
            "memory_current_bytes": util.read_int(os.path.join(abs_path, "memory.current")),
            "memory_max_bytes": util.read_int(os.path.join(abs_path, "memory.max")),
            "memory_high_bytes": util.read_int(os.path.join(abs_path, "memory.high")),
            "memory_swap_current_bytes": util.read_int(
                os.path.join(abs_path, "memory.swap.current")
            ),
            "pressure": {
                "memory": self._pressure_snapshot(psi_mem),
                "cpu": self._pressure_snapshot(psi_cpu),
            },
        }

    # ── verb: stop ───────────────────────────────────────────────────────

    def handle_stop(self, args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        session_id = args.get("session")
        if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
            raise RequestError("unknown-session", f"no session {session_id!r} is live or on record")
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                on_disk = self._read_stored_summary(session_id)
                if on_disk is None:
                    raise RequestError(
                        "unknown-session", f"no session {session_id} is live or on record"
                    )
                self._check_owner(ctx, self._stored_owner(session_id), "stop")
                return self._stop_response(session_id, on_disk, already_stopped=True)
            self._check_owner(ctx, sess.owner_uid, "stop")
            if sess.finished:
                return self._stop_response(session_id, sess.summary_doc, already_stopped=True)
            worker = sess.thread
        # Join the sampling worker OUTSIDE the daemon-wide lock. The worker's
        # own terminal paths (a session error, an enforced stall kill) take
        # that lock to finalize; joining under it would make a worker that is
        # merely finishing look wedged and answer a spurious `stop-incomplete`
        # (review CGP-110 wave r1).
        sess.stop_event.set()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=STOP_JOIN_SECONDS)
        with self._lock:
            if sess.finished:
                # The worker finalized itself while we waited.
                return self._stop_response(session_id, sess.summary_doc, already_stopped=True)
            if not self._finalize_session_locked(sess, aborted_reason=None, prejoined=True):
                if sess.finalize_refusal == "error":
                    raise RequestError(
                        "stop-incomplete",
                        f"session {session_id} could not be finalized: "
                        f"{sess.finalize_error}; it keeps its resources and "
                        f"was NOT finalized; retry stop",
                    )
                if sess.finalize_refusal == "lock":
                    # The worker exited but the session lock stayed busy
                    # (a short-lived verb, or a wedged locked action):
                    # not the "worker did not exit" case (CP-27 N8).
                    raise RequestError(
                        "stop-incomplete",
                        f"session {session_id} could not be finalized: the "
                        f"session lock was busy for {STOP_JOIN_SECONDS:g}s "
                        f"(another verb or action holds it), so it was NOT "
                        f"finalized and its resources are still held; retry stop",
                    )
                # Sol B2: the sampling worker did not exit in time. The
                # session stays live and owns its placement and DAMON
                # resources; no finished manifest was written. The caller can
                # retry `stop` once the worker has exited.
                raise RequestError(
                    "stop-incomplete",
                    f"session {session_id} is still sampling: its worker did "
                    f"not exit within {STOP_JOIN_SECONDS:g}s, so it was NOT "
                    f"finalized and its resources are still held; retry stop "
                    f"(attempt {sess.stop_attempts} of "
                    f"{STOP_FORCE_AFTER_ATTEMPTS}: the last one finalizes "
                    f"anyway with stop_forced)",
                )
            summary_doc = sess.summary_doc
        response = self._stop_response(session_id, summary_doc, already_stopped=False)
        self._run_retention()
        return response

    @staticmethod
    def _check_owner(ctx: _Ctx, owner: Optional[int], op: str) -> None:
        """PROTOCOL 10.5: the session's owner uid or uid 0. A session with no
        recorded owner (a 1.x store) is uid 0 only; unreadable peer
        credentials fail closed."""
        if ctx.uid == 0:
            return
        if ctx.uid is None or owner is None or ctx.uid != owner:
            named = "no owner recorded (uid 0 only)" if owner is None else f"owner uid {owner}"
            who = "unavailable" if ctx.uid is None else str(ctx.uid)
            raise RequestError(
                "peer-refused", f"{op} of this session is allowed to its {named}; peer uid {who}",
            )

    def _read_manifest(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self.sessions_dir, session_id, "manifest.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        return doc if isinstance(doc, dict) else None

    def _stored_owner(self, session_id: str) -> Optional[int]:
        manifest = self._read_manifest(session_id) or {}
        owner = manifest.get("owner_uid")
        return owner if type(owner) is int else None

    def _read_stored_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self.sessions_dir, session_id, "summary.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def _finalize_session_locked(
        self, sess: _Session, *, aborted_reason: Optional[str], force: bool = False,
        prejoined: bool = False,
    ) -> bool:
        """Stop ``sess``'s thread and write its final summary/manifest.
        Called with :attr:`_lock` held — every caller (``stop``, shutdown,
        the session-loop's own error path) already holds it.

        Returns False, having changed NOTHING but the stop request, when the
        sampling worker is still alive after the bounded join (Sol B2):
        releasing placement, closing DAMON and writing ``finished`` under a
        live sampler would let it append records to a finalized session. The
        session then stays live and a later call retries.

        Bounded escalation: the ``STOP_FORCE_AFTER_ATTEMPTS``-th consecutive
        incomplete attempt, or ``force=True`` (daemon shutdown, where the
        process is about to exit anyway), finalizes with the worker still
        alive: placement and DAMON are released, the manifest is ``aborted``
        with reason ``sampler-still-running`` and the Summary carries
        ``stop_forced``. A late sample from the wedged worker is then
        discarded (``_on_session_sample`` ignores a finished session). If the
        worker holds the session lock the forced path still returns False
        rather than deadlock the daemon."""
        sess.stop_event.set()
        sess.finalize_refusal = ""
        sess.finalize_error = ""
        worker_alive = False
        if sess.thread is not None and sess.thread is not threading.current_thread():
            # `prejoined`: the caller already waited a full bound outside the
            # daemon-wide lock; only re-check liveness here.
            sess.thread.join(timeout=0 if prejoined else STOP_JOIN_SECONDS)
            if sess.thread.is_alive():
                sess.stop_attempts += 1
                if not force and sess.stop_attempts < STOP_FORCE_AFTER_ATTEMPTS:
                    self._log(
                        f"session {sess.session_id}: sampling worker still "
                        f"running after {STOP_JOIN_SECONDS:g}s; stop is "
                        f"incomplete and the session keeps its placement and "
                        f"DAMON resources (attempt {sess.stop_attempts} of "
                        f"{STOP_FORCE_AFTER_ATTEMPTS})"
                    )
                    sess.finalize_refusal = "worker"
                    return False
                worker_alive = True
        # Take the session lock ONCE, bounded, and keep it for the whole
        # section (Sol S9): an unbounded wait here would hold the daemon-wide
        # lock while a worker wedged inside a locked action never lets go.
        # On timeout nothing has changed; the caller falls back to the
        # documented orphan recovery.
        lock_wait = SHUTDOWN_LOCK_SECONDS if force else STOP_JOIN_SECONDS
        if not sess.lock.acquire(timeout=lock_wait):
            self._log(
                f"session {sess.session_id}: the session lock is held "
                f"(a worker is wedged inside a locked action) for "
                f"{lock_wait:g}s; cannot finalize, the session keeps "
                f"its resources and a restart recovers it as an orphan"
            )
            sess.finalize_refusal = "lock"
            return False
        if worker_alive:
            sess.stop_forced = True
            aborted_reason = STOP_FORCED_REASON
            self._log(
                f"session {sess.session_id}: finalizing with the sampling "
                f"worker still running ({STOP_FORCED_REASON}); resources "
                f"are released and the manifest is aborted"
            )
        ended_at = self._iso(self.clock())
        # CP-27 N9: `closed` is set first (below) so a wedged worker cannot
        # act on resources under release; when the section raises, the guard
        # restores it (under the still-held session lock) and the failure is
        # reported as "not finalized" instead of leaving a session that is
        # neither sampled nor finalized.
        guard = _RestoreClosedOnError(sess, was_forced=worker_alive)
        with _HeldLock(sess.lock), guard:
            sess.closed = True      # before release/close; see _Session.closed
            if sess.summary_acc.sample_count == 0:
                # Stopped before a single tick landed — contract §1.5 still
                # promises a summary, so take one sample right now rather
                # than let finalize() refuse an empty accumulator.
                abs_target = self._sample_source_abs(sess.cgroup, sess.placement)
                abs_slice = (
                    os.path.join(self.cgroup_root, sess.slice_cgroup.lstrip("/"))
                    if sess.slice_cgroup else None
                )
                sess.summary_acc.add_sample(
                    cgroup=summary.sample_target_cgroup(abs_target),
                    host=metrics.sample_host(proc_root=self.host_proc_root),
                    slice_cgroup=summary.sample_slice_cgroup(abs_slice) if abs_slice else None,
                    damon=None,
                    pids=summary.read_cgroup_pids(abs_target),
                )
            summary_doc = sess.summary_acc.finalize(ended_at=ended_at)
            # §8.7: the Summary gains `liveness` and `watch` (schema 1,
            # optional keys, additive). Injected HERE rather than inside
            # `SummaryAccumulator.finalize` on purpose — the accumulator is
            # the contract's §7 computation object, shared with `cgprofile
            # run`/`attach`, which have no daemon watcher at all; making it
            # carry a watch block would mean either a null key in every
            # collector summary or a second code path inside it. `placement`
            # (the third §8.7 key) lands with C8.
            if sess.watch is not None:
                summary_doc["liveness"] = sess.watch.liveness_block()
                summary_doc["watch"] = sess.watch.watch_block()
            # CP-9 (§8.3): survivors go back to the scope they came from and
            # the leaf is removed BEFORE the block is read, so the Summary's
            # `placement` is the final state (including a `write-failed` leaf
            # that would not go) rather than a snapshot from mid-session.
            # `release` is idempotent and never raises: a cleanup failure is
            # reported, it does not cost the caller its Summary.
            if sess.placement is not None:
                sess.placement.release()
                summary_doc["placement"] = sess.placement.block()
            if sess.ancestor_events_start is not None:
                summary_doc["ancestors_memory_events_local"] = ancestor_events_mod.deltas(
                    sess.ancestor_events_start,
                    ancestor_events_mod.read_ancestors(
                        self.cgroup_root, list(sess.ancestor_events_start),
                    ),
                )
        if guard.error is not None:
            self._log(
                f"session {sess.session_id}: finalization failed "
                f"({type(guard.error).__name__}: {guard.error}); the session "
                f"is reopened and keeps its resources, a later stop retries"
            )
            sess.finalize_refusal = "error"
            sess.finalize_error = f"{type(guard.error).__name__}: {guard.error}"
            return False
        if sess.damon_session is not None:
            # Sol B3: an unverified (possibly still active) kdamond must not
            # look like "DAMON off" to the consumer. The disposition travels
            # in the Summary (additive `damon_cleanup`) and the manifest; it
            # never changes the workload verdict.
            sess.damon_cleanup = self._close_damon_session(
                sess.damon_session,
                session_id=sess.session_id,
                context="session finalization",
            )
        if sess.damon_cleanup is not None:
            summary_doc["damon_cleanup"] = dict(sess.damon_cleanup)
        if worker_alive:
            summary_doc["stop_forced"] = STOP_FORCED_REASON
        sess.ended_at = ended_at
        sess.summary_doc = summary_doc
        sess.finished = True
        sess.rundir.write_json("summary.json", summary_doc)
        manifest = self._manifest_for(sess, status="aborted" if aborted_reason else "finished")
        manifest["aborted_reason"] = aborted_reason
        sess.rundir.write_manifest(manifest)
        return True

    def _stop_response(
        self, session_id: str, summary_doc: Optional[Dict[str, Any]], *, already_stopped: bool,
    ) -> Dict[str, Any]:
        damon_series: Optional[str] = None
        try:
            rundir = store.RunDir(self.sessions_dir, run_id=session_id, create=False)
            for item in rundir.read("damon"):
                if isinstance(item, dict) and any(
                    key in item for key in ("hot", "warm", "cold", "idle")
                ):
                    damon_series = "damon.jsonl"
                    break
        except (OSError, ValueError):
            damon_series = None
        return {
            "ok": True,
            "contract": CONTRACT_VERSION,
            "session": session_id,
            "already_stopped": already_stopped,
            "summary": summary_doc,
            "session_dir": None,  # filled in by the caller once rundir is known
            "series": {
                "samples": "samples.jsonl.gz", "damon": damon_series, "events": "events.jsonl",
                "host": "host.jsonl", "manifest": "manifest.json", "summary": "summary.json",
            },
        }

    # ── verb: report (removed in 2.0.0, PROTOCOL 10.1/10.2) ─────────────

    @staticmethod
    def handle_report(args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        """The daemon no longer renders reports: it holds no pandas, plotly
        or matplotlib and runs no report subprocess. Every contract answers
        the additive `report-removed` error so a 1.x client is told, never
        dropped silently."""
        raise RequestError(
            "report-removed",
            "the daemon no longer renders reports; export the session with "
            "contract 2 and render locally (cgprofile report <session>)",
        )

    # ── verb: gc / retention ─────────────────────────────────────────────

    def handle_gc(self, args: Dict[str, Any], ctx: _Ctx = _TRUSTED) -> Dict[str, Any]:
        """Retention on demand (CP-52: `dry_run`, `keep`, `keep_days`). Only
        sessions the peer owns are removed (uid 0: any); a session being
        exported is skipped."""
        unknown = sorted(set(args) - {"dry_run", "keep", "keep_days"})
        if unknown:
            raise RequestError("bad-argument", f"unexpected gc argument(s) {unknown!r}")
        dry_run = args.get("dry_run", False)
        if not isinstance(dry_run, bool):
            raise RequestError("bad-argument", "'dry_run' must be a boolean")
        limits: Dict[str, Optional[int]] = {}
        for name, ceiling in (("keep", self.keep_sessions), ("keep_days", self.keep_days)):
            value = args.get(name)
            if value is None:
                limits[name] = None
                continue
            if type(value) is not int or value < 1:
                raise RequestError("bad-argument", f"{name!r} must be an integer >= 1")
            if value > ceiling:
                raise RequestError(
                    "bad-argument",
                    f"{name!r}={value} is above the configured maximum {ceiling} "
                    f"(gc may only keep fewer or expire sooner)",
                )
            limits[name] = value
        if ctx.uid is None:
            raise RequestError(
                "peer-refused", "gc needs readable peer credentials (peer uid unavailable)"
            )
        removed, kept = self._run_retention(
            keep=limits["keep"], keep_days=limits["keep_days"],
            owner_uid=None if ctx.uid == 0 else ctx.uid, dry_run=dry_run,
        )
        return {
            "ok": True, "contract": CONTRACT_VERSION, "removed": removed, "kept": kept,
            "dry_run": dry_run,
        }

    def _run_retention(
        self, *, keep: Optional[int] = None, keep_days: Optional[int] = None,
        owner_uid: Optional[int] = None, dry_run: bool = False,
    ) -> Tuple[List[str], int]:
        try:
            names = sorted(os.listdir(self.sessions_dir))
        except OSError:
            return [], 0
        with self._lock:
            live_ids = {s.session_id for s in self._sessions.values() if not s.finished}
        finished: List[Tuple[str, Dict[str, Any]]] = []
        for name in names:
            if name in live_ids:
                continue
            manifest_path = os.path.join(self.sessions_dir, name, "manifest.json")
            try:
                with open(manifest_path, "r", encoding="utf-8") as fh:
                    manifest = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("status") == "live":
                continue
            # A failed PID restoration can leave a finished summary beside a
            # live delegated scope. Its journal is the only restart recovery
            # authority, so count/age retention applies only after that
            # journal positively records complete cleanup.
            placement_path = os.path.join(self.sessions_dir, name, "placement-state.json")
            if os.path.lexists(placement_path):
                try:
                    with open(placement_path, "r", encoding="utf-8") as fh:
                        placement_state = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(placement_state, dict) or placement_state.get("state") != "complete":
                    continue
            finished.append((name, manifest))
        finished.sort(key=lambda pair: pair[0])

        keep_count = keep if keep is not None else self.keep_sessions
        keep_newest: Set[str] = (
            {name for name, _ in finished[-keep_count:]} if keep_count > 0 else set()
        )
        cutoff = self.clock() - (keep_days if keep_days is not None else self.keep_days) * 86400.0

        removed: List[str] = []
        kept = 0
        for name, manifest in finished:
            ended_epoch = self._parse_iso_epoch(manifest.get("ended_at"))
            too_old = ended_epoch is not None and ended_epoch < cutoff
            expendable = not (name in keep_newest and not too_old)
            if expendable and owner_uid is not None and manifest.get("owner_uid") != owner_uid:
                expendable = False
            with self._lock:
                if expendable and self._exporting.get(name):
                    expendable = False
            if not expendable:
                kept += 1
            else:
                if not dry_run:
                    self._remove_session_dir(name)
                removed.append(name)
        return removed, kept

    def _remove_session_dir(self, name: str) -> None:
        path = os.path.join(self.sessions_dir, name)
        self._guard_path(path)
        shutil.rmtree(path, ignore_errors=True)

    # ── dispatch / socket loop ───────────────────────────────────────────

    # ── settings (cgprofiled config, CGPROFILECTL-SPEC 3) ────────────────────────────

    #: settings key -> how a changed value reaches the running server.
    _APPLIED_KEYS = (
        "interval", "damon_default", "keep_sessions", "keep_days", "max_sessions",
        "allow_uids", "export_max_bytes", "observe_slices", "gates_slice",
        "max_watchers", "max_exports",
    )

    def _apply_settings(self, *, snapshot_only: bool = False) -> None:
        """Copy every changed runtime key from `self.settings` onto the
        attributes the verbs read, and resize the stream slot caps. Only keys
        whose value changed since the last call are touched."""
        current = {key: self.settings.get(key) for key in self._APPLIED_KEYS}
        if not snapshot_only:
            for key, value in current.items():
                if key in self._applied and self._applied[key] == value:
                    continue
                if key == "interval":
                    self.default_interval = value
                elif key == "damon_default":
                    self.damon_default = value
                elif key == "allow_uids":
                    self.allow_uids = list(value) if value else None
                elif key == "observe_slices":
                    self.observe_slices = list(value)
                elif key == "gates_slice":
                    self.gates_slice_name = value
                    self.windows.gates_slice_name = value
                elif key == "max_watchers":
                    self._watch_slots.resize(value)
                elif key == "max_exports":
                    self._export_slots.resize(value)
                else:
                    setattr(self, key, value)
        self._applied = current

    def reload_settings(self) -> bool:
        """SIGHUP / `cgprofiled reload`: re-read the file and apply. Never raises."""
        ok = self.settings.reload()
        self._apply_settings()
        self._host_benchmark = machine_mod.load_host_benchmark(self.host_benchmark_path)
        return ok

    def _machine(self, cgroup: Optional[str] = None) -> Dict[str, Any]:
        """The `machine/1` facts: the host's, or a lane's when `cgroup` is given."""
        return machine_mod.build_machine(
            loaded=self._host_benchmark,
            entries=self._scores_table,
            proc_root=self.host_proc_root,
            cgroup_root=self.cgroup_root,
            sys_root=self.sys_root,
            cgroup=cgroup,
        )

    @staticmethod
    def _request_contract(req: Any) -> int:
        """The contract a response to `req` is stamped with: the requested
        one when this daemon serves it, else 1 (an omitted or unsupported
        contract is answered as a contract-1 error)."""
        if isinstance(req, dict):
            contract = req.get("contract", CONTRACT_VERSION)
            if type(contract) is int and contract in CONTRACTS:
                return contract
        return CONTRACT_VERSION

    def _make_ctx(self, req: Any, peer: Any) -> _Ctx:
        """The request context: `peer` is `(pid, uid, gid)`, `None` for
        unreadable credentials (uid None, fails closed) or `_UNSET` for an
        in-process, trusted caller."""
        contract = self._request_contract(req)
        if peer is _UNSET:
            return _Ctx(contract)
        if peer is None:
            return _Ctx(contract, uid=None)
        return _Ctx(contract, uid=peer[1], pid=peer[0])

    def _dispatch(
        self, req: Dict[str, Any], peer: Any = _UNSET
    ) -> Dict[str, Any]:
        """Route ONE §8.1 request line to its handler.

        `req` is the wire request exactly as contract §8.1 defines it —
        `{"verb": …, "args": {…}, "contract": 1}` — and nothing else is
        accepted: v1's flat shape (`{"verb": "stop", "session": …}`) is
        REFUSED with `bad-argument` naming the offending key rather than
        silently read as "stop with no session" (P6 C6 migrated the
        in-image `ctl` client, the only producer of these lines, to the
        §8.1 shape in the same commit — one shape on the wire, no
        compatibility branch). Handlers receive the `args` object alone;
        the long-option arg NAMES are documented per verb in
        `docs/PROTOCOL.md`.
        """
        ctx = self._make_ctx(req, peer)
        verb, args, wire_error = self._validate_wire(req)
        if wire_error is not None:
            return wire_error
        if verb in CONTRACT_2_VERBS and ctx.contract < 2:
            return self._error_response(
                "bad-argument",
                f"verb {verb!r} exists only at contract 2; this request is contract "
                f"{ctx.contract} (this daemon speaks contracts {list(CONTRACTS)})",
                contract=ctx.contract,
            )
        if verb in STREAMING_VERBS:
            # §8.8's `not-streaming`: the verb exists and the request is
            # well formed, but this path answers exactly one object and the
            # caller is waiting for exactly one. The streaming path is
            # `_handle_connection`'s own (see STREAMING_VERBS).
            return self._error_response(
                "not-streaming",
                f"{verb!r} streams one JSON object per line until the session ends "
                f"(contract §8.2) and cannot be answered as a single response",
                contract=ctx.contract,
            )
        handlers = {
            "version": self.handle_version,
            "start": self.handle_start,
            "status": self.handle_status,
            "host": self.handle_host,
            "stop": self.handle_stop,
            "report": self.handle_report,
            "gc": self.handle_gc,
            "config": self.handle_config,
            "config-set": self.handle_config_set,
            "config-reset": self.handle_config_reset,
        }
        handler = handlers.get(verb)
        if handler is None:
            return self._error_response(
                "bad-argument", f"unknown verb {verb!r}", contract=ctx.contract
            )
        try:
            resp = handler(args, ctx)
        except RequestError as exc:
            return self._error_response(exc.code, exc.message, contract=ctx.contract)
        if verb == "stop" and resp.get("ok"):
            resp["session_dir"] = os.path.join(self.sessions_dir, resp["session"])
        resp["contract"] = ctx.contract
        return resp

    def _validate_wire(
        self, req: Dict[str, Any]
    ) -> Tuple[Optional[str], Dict[str, Any], Optional[Dict[str, Any]]]:
        """``(verb, args, error_response)`` for ONE §8.1 request line.

        Split out of :meth:`_dispatch` by C7 so the streaming verb gets the
        IDENTICAL wire validation as every other verb (one shape, no second
        parser) before `_handle_connection` hands its connection off.
        """
        if not isinstance(req, dict):
            return None, {}, self._error_response("bad-argument", "request must be a JSON object")
        if any(not isinstance(key, str) for key in req):
            return None, {}, self._error_response(
                "bad-argument", "request object keys must be strings"
            )
        unexpected = sorted(set(req) - _WIRE_KEYS)
        if unexpected:
            return None, {}, self._error_response(
                "bad-argument",
                f"unexpected request key(s) {unexpected!r}: a request is "
                f"{{\"verb\", \"args\", \"contract\"}} (contract §8.1)",
                contract=self._request_contract(req),
            )
        contract = req.get("contract", CONTRACT_VERSION)
        if (
            isinstance(contract, bool)
            or not isinstance(contract, int)
            or contract not in CONTRACTS
        ):
            return None, {}, self._error_response(
                "bad-argument",
                f"request contract {contract!r}, this daemon speaks contracts {list(CONTRACTS)}",
            )
        verb = req.get("verb")
        if not isinstance(verb, str):
            return None, {}, self._error_response(
                "bad-argument", "request 'verb' must be a string", contract=contract
            )
        args = req.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None, {}, self._error_response(
                "bad-argument", "request 'args' must be a JSON object", contract=contract
            )
        return verb, args, None

    @staticmethod
    def _error_response(
        code: str, message: str, *, contract: int = CONTRACT_VERSION
    ) -> Dict[str, Any]:
        return {"ok": False, "contract": contract, "error": {"code": code, "message": message}}

    @staticmethod
    def _requested_observer_contract(payload: bytes) -> int:
        """The contract an error envelope uses: the requested one when supported."""
        try:
            request = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return RESOURCE_OBSERVER_CONTRACT_VERSION
        if not isinstance(request, dict):
            return RESOURCE_OBSERVER_CONTRACT_VERSION
        contract = request.get("contract")
        if type(contract) is int and contract in RESOURCE_OBSERVER_CONTRACTS:
            return contract
        return RESOURCE_OBSERVER_CONTRACT_VERSION

    def _clear_control_socket_path(self) -> None:
        """CP-34: make the control socket path free for a bind, or refuse.

        Nothing is unlinked blindly: a non-socket node (regular file,
        directory, symlink) is refused with its type named, and a socket
        node is removed only when one bounded ``connect()`` proves it dead
        (ECONNREFUSED). A peer that still accepts is another daemon; stealing
        its path would leave it running, unreachable, with its sessions."""
        path = self.socket_path
        try:
            existing = os.lstat(path)
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(existing.st_mode):
            kind = (
                "a directory" if stat.S_ISDIR(existing.st_mode)
                else "a symlink" if stat.S_ISLNK(existing.st_mode)
                else "a regular file" if stat.S_ISREG(existing.st_mode)
                else f"a non-socket node (mode {stat.S_IFMT(existing.st_mode):o})"
            )
            raise ControlSocketError(
                errno.EEXIST,
                f"refusing to replace {path}: it is {kind}, not a socket",
                path,
            )
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect(path)
        except FileNotFoundError:
            return
        except ConnectionRefusedError:
            pass
        except OSError as exc:
            raise ControlSocketError(
                errno.EADDRINUSE,
                f"cannot prove the socket at {path} is stale ({exc}); not removing it",
                path,
            ) from exc
        else:
            raise ControlSocketError(
                errno.EADDRINUSE,
                f"another daemon answers at {path}; refusing to replace it",
                path,
            )
        finally:
            probe.close()
        current = os.lstat(path)
        if (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino):
            raise ControlSocketError(
                errno.EADDRINUSE,
                f"the socket at {path} changed while checking it; not removing it",
                path,
            )
        os.unlink(path)
        self._log(f"removed a stale control socket node at {path}")

    def _bind_control_socket(self) -> None:
        socket_dir = os.path.dirname(self.socket_path)
        if socket_dir:
            os.makedirs(socket_dir, exist_ok=True)
        self._clear_control_socket_path()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(self.socket_path)
            bound = os.lstat(self.socket_path)
            sock.listen(16)
            sock.settimeout(self.accept_timeout)
        except BaseException:
            sock.close()
            raise
        self._sock = sock
        self._sock_identity = (bound.st_dev, bound.st_ino)
        self._apply_self_bind_socket_mode(self.socket_path, "control")

    def _bind(self) -> None:
        if self._activated_sockets is not None:
            for level in ("control", "observer"):
                listener = self._activated_sockets.get(level)
                if listener is None:
                    self._log(f"systemd socket descriptor missing for configured level {level}")
                    continue
                listener.settimeout(self.accept_timeout)
                if level == "control":
                    self._sock = listener
                else:
                    self._observer_sock = listener
            return
        self._bind_control_socket()
        try:
            self._bind_observer_socket()
        except OSError as exc:
            # The profiler's established control carrier remains available
            # if this additive observer endpoint cannot be created. Native
            # R2 consumers fail closed when their opted-in socket is absent.
            self._log(
                f"resource observer socket unavailable at "
                f"{self.observer_socket_path}: {type(exc).__name__}: {exc}"
            )

    def _bind_observer_socket(self) -> None:
        observer_dir = os.path.dirname(self.observer_socket_path) or "."
        try:
            directory = os.lstat(observer_dir)
        except OSError as exc:
            raise OSError(
                exc.errno,
                f"resource observer directory must be provisioned by host setup: {exc}",
                observer_dir,
            ) from exc
        if not stat.S_ISDIR(directory.st_mode):
            raise OSError(
                errno.EPERM,
                "resource observer path parent must be an existing directory",
                observer_dir,
            )
        default_directory = os.path.normpath(observer_dir) == os.path.dirname(
            os.path.normpath(DEFAULT_RESOURCE_OBSERVER_SOCKET_PATH)
        )
        if default_directory and (
            directory.st_uid != 0
            or directory.st_mode & 0o022
            or not directory.st_mode & 0o010
        ):
            raise OSError(
                errno.EPERM,
                "default resource observer directory must be root-owned, group-searchable, "
                "and not writable by group or other users",
                observer_dir,
            )
        try:
            existing = os.lstat(self.observer_socket_path)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(existing.st_mode):
                raise OSError(
                    errno.EEXIST,
                    "refusing to replace a non-socket resource observer path",
                    self.observer_socket_path,
                )
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.2)
            try:
                probe.connect(self.observer_socket_path)
            except OSError as exc:
                if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                    raise OSError(
                        errno.EADDRINUSE,
                        "cannot prove existing observer socket is stale",
                        self.observer_socket_path,
                    ) from exc
            else:
                raise OSError(
                    errno.EADDRINUSE,
                    "resource observer socket is already accepting connections",
                    self.observer_socket_path,
                )
            finally:
                probe.close()
            current = os.lstat(self.observer_socket_path)
            if (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino):
                raise OSError(
                    errno.EADDRINUSE,
                    "resource observer socket path changed while checking stale entry",
                    self.observer_socket_path,
                )
            os.unlink(self.observer_socket_path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        owned_identity: Optional[Tuple[int, int]] = None
        try:
            sock.bind(self.observer_socket_path)
            bound = os.lstat(self.observer_socket_path)
            if not stat.S_ISSOCK(bound.st_mode):
                raise OSError(errno.EINVAL, "bound resource observer path is not a socket")
            owned_identity = (bound.st_dev, bound.st_ino)
            sock.listen(16)
            sock.settimeout(self.accept_timeout)
            # Apply the node mode before publishing the socket, so a failure
            # here never leaves a closed socket assigned to ``_observer_sock``.
            self._apply_self_bind_socket_mode(self.observer_socket_path, "observer")
            self._observer_sock = sock
            self._observer_socket_identity = owned_identity
        except Exception:
            sock.close()
            self._unlink_observer_socket_if_owned(owned_identity)
            raise

    def _unlink_observer_socket_if_owned(
        self, identity: Optional[Tuple[int, int]]
    ) -> None:
        if identity is None:
            return
        try:
            current = os.lstat(self.observer_socket_path)
        except OSError:
            return
        if not stat.S_ISSOCK(current.st_mode) or (current.st_dev, current.st_ino) != identity:
            return
        try:
            os.unlink(self.observer_socket_path)
        except OSError:
            pass

    def _apply_self_bind_socket_mode(self, path: str, level: str) -> None:
        """Apply the configured node mode in explicit development self-bind mode.

        The caller provisions the parent directory. Production socket access
        is set by systemd's SocketUser, SocketGroup and SocketMode settings.
        """
        try:
            settings = self.settings.get(f"sockets.{level}")
            mode = int(settings["mode"], 8)
        except (KeyError, TypeError, ValueError):
            mode = SOCKET_MODE
        try:
            os.chmod(path, mode)
        except OSError as exc:
            self._log(f"could not chmod self-bound {level} socket {path} to {mode:04o}: {exc}")

    @staticmethod
    def _log(message: str) -> None:
        """One INFO line on stderr (`docker logs` is where an operator reads
        it; stdout belongs to `ctl`'s single JSON document, §1.2)."""
        print(f"cgprofile: {message}", file=sys.stderr, flush=True)

    def _peer_uid(self, conn: socket.socket) -> Optional[int]:
        """The connecting process's uid via `SO_PEERCRED`, or `None` when
        the kernel/socket cannot answer (a non-AF_UNIX test double)."""
        try:
            raw = conn.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
        except (OSError, AttributeError):
            return None
        try:
            _pid, uid, _gid = struct.unpack("3i", raw)
        except struct.error:
            return None
        return uid

    def _peer_credentials(self, conn: socket.socket) -> Optional[Tuple[int, int, int]]:
        """Return `(pid, uid, gid)` captured by SO_PEERCRED."""
        try:
            raw = conn.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
            pid, uid, gid = struct.unpack("3i", raw)
        except (OSError, AttributeError, struct.error):
            return None
        return (pid, uid, gid)

    def _peer_allowed(self, uid: Optional[int]) -> bool:
        """§8.1's authorisation rule. uid 0 is ALWAYS allowed by policy.
        With no allowlist configured, any peer permitted by the
        dedicated socket group's mode is allowed. With an allowlist, only
        those uids are allowed. An unreadable peer credential is refused in
        that case, because failing open would quietly void the one control the
        operator explicitly asked for.
        """
        if self.allow_uids is None:
            return True
        if uid is None:
            return False
        return uid == 0 or uid in self.allow_uids

    def _unlink_control_socket_if_owned(self) -> None:
        """Remove the control socket node only when it is the one this
        process bound (CP-34/CP-31): a successor's node is never touched."""
        identity = self._sock_identity
        self._sock_identity = None
        if identity is None:
            return
        try:
            current = os.lstat(self.socket_path)
        except OSError:
            return
        if not stat.S_ISSOCK(current.st_mode) or (current.st_dev, current.st_ino) != identity:
            return
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    def _check_control_socket(self) -> None:
        """CP-31: verify the control path still holds THIS daemon's
        listener, and rebind when it does not (the node was removed or
        replaced while the container stayed up). When it cannot be rebound,
        flag the loss so the daemon exits non-zero and the restart policy
        recreates it, rather than serve unreachable.

        Runs only in explicit ``--self-bind`` mode: under socket activation
        systemd owns the node and the daemon never rebinds or unlinks it."""
        identity = self._sock_identity
        try:
            current = os.lstat(self.socket_path)
        except OSError:
            current = None
        if (
            current is not None and stat.S_ISSOCK(current.st_mode)
            and (current.st_dev, current.st_ino) == identity
        ):
            return
        self._log(
            f"control socket {self.socket_path} is "
            f"{'missing' if current is None else 'no longer this daemon listener'}; rebinding"
        )
        old = self._sock
        self._sock = None
        self._sock_identity = None   # the node, if any, is not ours to unlink
        if old is not None:
            old.close()
        try:
            self._bind_control_socket()
        except OSError as exc:
            self._log(
                f"could not rebind the control socket ({type(exc).__name__}: {exc}); "
                f"exiting so the restart policy recreates the daemon"
            )
            self._socket_lost = True
            self._stopping = True
        else:
            self._log(f"control socket {self.socket_path} rebound")

    def _close_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        if self.socket_mode == "self-bind":
            self._unlink_control_socket_if_owned()
        owned_observer_socket = self._observer_sock is not None
        observer_identity = self._observer_socket_identity
        if owned_observer_socket:
            try:
                self._observer_sock.close()
            finally:
                self._observer_sock = None
                self._observer_socket_identity = None
        if owned_observer_socket and self.socket_mode == "self-bind":
            self._unlink_observer_socket_if_owned(observer_identity)

    def _admit_connection(self, conn: socket.socket) -> None:
        """CP-33: hand one accepted connection to its own thread, so a slow
        peer or a long verb never stalls the accept loop. A connection over
        the request-reader cap (or one the OS cannot give a thread) is
        answered `busy` and closed."""
        if not self._reader_slots.acquire(blocking=False):
            self._refuse_busy(conn, "too many connections are being read")
            return
        try:
            threading.Thread(
                target=self._handle_connection, args=(conn,),
                kwargs={"reader_slot": True}, daemon=True,
                name="cgprofile-conn",
            ).start()
        except RuntimeError:
            self._reader_slots.release()
            self._refuse_busy(conn, "the daemon could not start a handler thread")

    def _refuse_busy(self, conn: socket.socket, why: str) -> None:
        """Best-effort `busy` line, then close; never blocks for long."""
        try:
            conn.settimeout(BUSY_SEND_TIMEOUT_SECONDS)
            self._send_line(conn, self._error_response(
                "busy", f"{why}; retry shortly",
            ))
        except OSError:
            pass
        finally:
            conn.close()

    def _drain_inflight(self) -> None:
        deadline = time.monotonic() + SHUTDOWN_DRAIN_SECONDS
        with self._inflight_cond:
            while self._inflight > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._log(
                        f"{self._inflight} verb(s) still running after "
                        f"{SHUTDOWN_DRAIN_SECONDS:g}s at shutdown"
                    )
                    return
                self._inflight_cond.wait(remaining)

    def _begin_inflight(self) -> bool:
        with self._inflight_cond:
            if self._stopping:
                return False
            self._inflight += 1
            return True

    def _end_inflight(self) -> None:
        with self._inflight_cond:
            self._inflight -= 1
            self._inflight_cond.notify_all()

    def _dispatch_admitted(self, req: Dict[str, Any], peer: Any = _UNSET) -> Dict[str, Any]:
        """`_dispatch` under the bounded-handler admission (CP-33). Verbs run
        concurrently across connections; the registry/session locks already
        guard every piece of shared state (`version`, `host`, `status` and
        `gc` are read-only snapshots), and the cap answers `busy`."""
        # A non-object request is refused by `_dispatch`, inside a slot.
        slots = self._handler_slots
        if not slots.acquire(blocking=False):
            return self._error_response(
                "busy", "too many verbs are running; retry shortly",
                contract=self._request_contract(req),
            )
        try:
            if not self._begin_inflight():
                return self._error_response("busy", "the daemon is shutting down")
            try:
                return self._dispatch(req, peer)
            finally:
                self._end_inflight()
        finally:
            slots.release()

    def _handle_connection(self, conn: socket.socket, *, reader_slot: bool = False) -> None:
        handed_off = False
        holding_reader = reader_slot
        try:
            uid = self._peer_uid(conn)
            peer_allowed = self._peer_allowed(uid)
            peer = self._peer_credentials(conn)
            if peer is None and uid is not None:
                peer = (0, uid, 0)
            data = bytearray()
            deadline = self.request_clock() + self.request_line_timeout
            while True:
                if self.request_clock() >= deadline:
                    return
                newline = data.find(b"\n")
                if newline >= 0:
                    if newline + 1 > self.max_request_line_bytes:
                        self._send_line(conn, self._error_response(
                            "bad-argument",
                            f"request line exceeds {self.max_request_line_bytes} bytes",
                        ))
                        return
                    break
                if len(data) >= self.max_request_line_bytes:
                    self._send_line(conn, self._error_response(
                        "bad-argument",
                        f"request line exceeds {self.max_request_line_bytes} bytes",
                    ))
                    return
                remaining = deadline - self.request_clock()
                if remaining <= 0:
                    return
                conn.settimeout(remaining)
                try:
                    chunk = conn.recv(min(
                        65536, self.max_request_line_bytes + 1 - len(data)
                    ))
                except socket.timeout:
                    # A partial line is not dispatched. Closing bounds both
                    # the serial accept-loop occupancy and request memory.
                    return
                if not chunk:
                    # EOF does not terminate a newline-delimited request.
                    # Never turn an unterminated partial message into a
                    # dispatched operation.
                    return
                data.extend(chunk)
            if holding_reader:
                # The request line is in: the reader slot guards only the
                # slow part (waiting for a peer's bytes).
                holding_reader = False
                self._reader_slots.release()
            if not peer_allowed:
                # Wait for the bounded request line before replying. Closing
                # with the peer's write still in flight can reset AF_UNIX and
                # discard the promised peer-refused JSON. No denied request
                # is parsed or dispatched; incomplete lines still time out.
                named = "unavailable" if uid is None else str(uid)
                resp = self._error_response(
                    "peer-refused",
                    f"peer uid {named} is not in allow_uids",
                )
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                return
            if not data.strip():
                return
            try:
                req = json.loads(bytes(data).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                resp = self._error_response("bad-argument", "request was not valid JSON")
            else:
                # §8.2/§8.1 rule 4: the ONE streaming verb. The connection
                # leaves this method alive, on its own thread, with NO
                # socket timeout — a `watch` may legitimately say nothing
                # for `--watch-interval` seconds and outlive any per-verb
                # budget. It runs on its own thread because `_accept_loop`
                # is deliberately serial: streaming inline would block every
                # other verb (including the `stop` that ends the very
                # session being watched) for the life of the stream.
                verb, args, wire_error = self._validate_wire(req)
                ctx = self._make_ctx(req, peer)
                if (
                    wire_error is None and verb in STREAMING_VERBS
                    and not (verb in CONTRACT_2_VERBS and ctx.contract < 2)
                ):
                    # `watch` and `export` each have their own slot class;
                    # `export` also needs the peer (ownership, PROTOCOL 10.5).
                    exporting = verb == "export"
                    slots = self._export_slots if exporting else self._watch_slots
                    if not slots.acquire(blocking=False):
                        self._send_line(conn, self._error_response(
                            "busy", f"too many {verb} streams are open; retry shortly",
                            contract=ctx.contract,
                        ))
                        return
                    if exporting:
                        thread = threading.Thread(
                            target=self._export_connection, args=(conn, args, ctx),
                            daemon=True, name=f"cgprofile-export-{args.get('session')}",
                        )
                    else:
                        thread = threading.Thread(
                            target=self._watch_connection, args=(conn, args),
                            kwargs={"watch_slot": True}, daemon=True,
                            name=f"cgprofile-watch-{args.get('session')}",
                        )
                    handed_off = True
                    try:
                        thread.start()
                    except RuntimeError:
                        handed_off = False
                        slots.release()
                        self._send_line(conn, self._error_response(
                            "busy", f"the daemon could not start a {verb} thread; retry shortly",
                            contract=ctx.contract,
                        ))
                    return
                try:
                    resp = self._dispatch_admitted(req, peer)
                except Exception as exc:  # noqa: BLE001 - RG-55 live acceptance
                    # An unanticipated bug in ONE handler must never take
                    # down the whole accept loop — that would silently kill
                    # every OTHER live session's sampling thread along with
                    # it (each thread is daemon=True, so the process dying
                    # takes them with no finalize/summary at all — strictly
                    # worse than the ordinary `_session_loop` crash path,
                    # which already catches exactly this class of exception
                    # per-session and finalizes it as `aborted:
                    # "session-error:…"`). Found live 2026-09-12: a raw
                    # OSError from DamonSession.__enter__() (since fixed at
                    # its source — see that module's own comment) escaped
                    # `_dispatch`'s narrower `except RequestError` and
                    # crashed the entire daemon process, restarted only by
                    # `restart: unless-stopped`. Logged to stderr (visible
                    # in `docker logs`) and the connection is closed with NO
                    # reply — the same client-visible shape an ordinary
                    # connection drop already has (contract §1.3: exit 3,
                    # "daemon fault"), just without the blast radius.
                    # `_dispatch` rejects non-object requests and non-string
                    # verbs before a handler can throw, so the log can retain
                    # the useful verb without trusting malformed JSON shapes.
                    print(
                        f"cgprofile: unhandled error handling verb "
                        f"{req.get('verb')!r}: {type(exc).__name__}: {exc}",
                        file=sys.stderr, flush=True,
                    )
                    return
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            # The client closed before the reply was written (a probe that disconnects early, a
            # killed `cgprofilectl`): there is nobody to answer, and it is not a daemon fault, so
            # no thread traceback in the journal (VM acceptance observation, 2026-11-07).
            return
        finally:
            if holding_reader:
                self._reader_slots.release()
            if not handed_off:
                conn.close()

    # ── verb: watch (§8.2, the streaming exception) ─────────────────────

    @staticmethod
    def _send_line(conn: socket.socket, doc: Dict[str, Any]) -> None:
        conn.sendall((json.dumps(doc) + "\n").encode("utf-8"))

    def _watch_connection(
        self, conn: socket.socket, args: Dict[str, Any], *, watch_slot: bool = False,
    ) -> None:
        """One `watch` connection, on its own thread, start to `end`."""
        try:
            conn.settimeout(None)
            try:
                sess, interval = self._watch_prepare(args)
            except RequestError as exc:
                # §8.2: "unknown session → exit 2 `unknown-session` as a
                # single line" — one error object, then close, exactly the
                # shape every other refusal has.
                self._send_line(conn, self._error_response(exc.code, exc.message))
                return
            self._stream_watch(conn, sess, interval)
        except OSError:
            pass  # the consumer stopped reading; nothing to report to it
        except Exception as exc:  # noqa: BLE001 - same blast-radius rule as _handle_connection
            print(
                f"cgprofile: unhandled error streaming watch: {type(exc).__name__}: {exc}",
                file=sys.stderr, flush=True,
            )
        finally:
            if watch_slot:
                self._watch_slots.release()
            conn.close()

    def _watch_prepare(self, args: Dict[str, Any]) -> Tuple[_Session, float]:
        session_id = args.get("session")
        if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
            raise RequestError("unknown-session", f"no session {session_id!r} is live")
        try:
            interval = liveness_mod.clamp_watch_interval(args.get("watch_interval"))
        except ValueError as exc:
            raise RequestError("bad-argument", str(exc)) from None
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None or sess.finished:
                # A finished session has nothing left to stream: §8.2 streams
                # "until the session ends", and its verdict is already in
                # `stop`'s Summary.
                raise RequestError("unknown-session", f"no session {session_id} is live")
        return sess, interval

    def _stream_watch(self, conn: socket.socket, sess: _Session, interval: float) -> None:
        """§8.2's line protocol: a `reading` every ``interval``, a `verdict`
        on every state CHANGE, exactly one `end`.

        The last-state baseline is per STREAM, not per session, so a watcher
        that attaches to an already-stalled session is told the state on its
        first reading rather than waiting for the next transition — and two
        watchers of one session each get their own complete picture.
        """
        last_state = liveness_mod.STATE_OK
        while True:
            reading, watch_block = self._watch_lines(sess)
            self._send_line(conn, reading)
            if watch_block["state"] != last_state:
                last_state = watch_block["state"]
                self._send_line(conn, {
                    "contract": CONTRACT_VERSION, "event": "verdict",
                    "session": sess.session_id, "at": self._iso(self.clock()),
                    # §8.2 lists exactly these four keys on the wire; the
                    # policy that produced them travels in `status`/`stop`/
                    # the Summary (§8.7), not on every verdict line.
                    "watch": {k: watch_block[k]
                              for k in ("state", "verdict", "reason", "readings")},
                })
            # `stop_event` is set by `_finalize_session_locked` BEFORE it
            # joins the sampler thread and flips `finished`, so a stream
            # that woke in between must look at it too — otherwise it waits
            # out a whole `--watch-interval` (up to 300 s) after the session
            # it is watching has already been told to stop.
            if sess.finished or sess.stop_event.is_set() or self._stopping:
                break
            self.watch_wait(sess.stop_event, interval)
        if sess.watch is not None and sess.watch.verdict == liveness_mod.VERDICT_KILLED:
            reason = "killed"
        elif self._stopping:
            reason = "daemon-shutdown"
        else:
            reason = "stopped"
        self._send_line(conn, {
            "contract": CONTRACT_VERSION, "event": "end", "session": sess.session_id,
            "at": self._iso(self.clock()), "reason": reason,
        })

    def _watch_lines(self, sess: _Session) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """One `reading` line plus the watch block it was read with (one
        lock acquisition for both, so a state change between them is
        impossible)."""
        entry = self._status_entry(sess)
        return (
            {
                "contract": CONTRACT_VERSION, "event": "reading", "session": sess.session_id,
                "at": self._iso(self.clock()), "elapsed_seconds": entry["elapsed_seconds"],
                "live": entry["live"], "liveness": entry["liveness"],
                # §8.3, read under the SAME lock acquisition as `live` and
                # `liveness` (`_status_entry` built all three), so a reading
                # line is one consistent frame rather than three.
                "placement": entry["placement"],
            },
            entry["watch"],
        )

    # ── verb: export (PROTOCOL 10.3, contract 2) ─────────────────────────

    @staticmethod
    def _export_open(path: str) -> Any:
        """Open one stored part read-only, refusing a symlink or non-regular file."""
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(errno.EINVAL, "not a regular file")
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise

    def _export_admit(self, args: Dict[str, Any], ctx: _Ctx) -> Tuple[str, List[str], Optional[int]]:
        """Validate an `export` request and register the session as being
        exported (so retention skips it). Registration is the last step: a
        raise means nothing was registered."""
        unknown = sorted(set(args) - {"session", "parts"})
        if unknown:
            raise RequestError("bad-argument", f"unexpected export argument(s) {unknown!r}")
        session_id = args.get("session")
        if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
            raise RequestError("unknown-session", f"no session {session_id!r} is on record")
        parts = args.get("parts")
        if parts is None:
            names = list(EXPORT_PARTS)
        elif isinstance(parts, list) and all(
            isinstance(item, str) and item in EXPORT_PARTS for item in parts
        ):
            names = [name for name in EXPORT_PARTS if name in parts]
        else:
            raise RequestError(
                "bad-argument", f"'parts' must be null or a list drawn from {list(EXPORT_PARTS)!r}"
            )
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is not None and not sess.finished:
                raise RequestError(
                    "session-live", f"session {session_id} is still running; stop it first"
                )
            manifest = self._read_manifest(session_id)
            if manifest is None:
                raise RequestError("unknown-session", f"no session {session_id} is on record")
            if manifest.get("status") == "live":
                raise RequestError(
                    "session-live", f"session {session_id} is still running; stop it first"
                )
            owner = manifest.get("owner_uid")
            owner = owner if type(owner) is int else None
            self._check_owner(ctx, owner, "export")
            self._exporting[session_id] = self._exporting.get(session_id, 0) + 1
        return session_id, names, owner

    def _export_release(self, session_id: str) -> None:
        with self._lock:
            left = self._exporting.get(session_id, 0) - 1
            if left > 0:
                self._exporting[session_id] = left
            else:
                self._exporting.pop(session_id, None)

    def _export_connection(self, conn: socket.socket, args: Dict[str, Any], ctx: _Ctx) -> None:
        """One `export` connection, on its own thread, start to `end`."""
        session_id: Optional[str] = None
        files: List[Tuple[str, Any]] = []
        try:
            conn.settimeout(EXPORT_STALL_SECONDS)
            try:
                session_id, names, owner = self._export_admit(args, ctx)
            except RequestError as exc:
                self._send_line(conn, self._error_response(exc.code, exc.message, contract=2))
                return
            try:
                header = self._export_header(session_id, names, owner, files)
            except RequestError as exc:
                self._send_line(conn, self._error_response(exc.code, exc.message, contract=2))
                return
            self._send_line(conn, header)
            self._export_stream(conn, files)
        except OSError:
            pass  # the consumer stopped reading or stalled; nothing to report to it
        except Exception as exc:  # noqa: BLE001 - same blast-radius rule as _handle_connection
            print(
                f"cgprofile: unhandled error streaming export: {type(exc).__name__}: {exc}",
                file=sys.stderr, flush=True,
            )
        finally:
            for _name, handle in files:
                handle.close()
            if session_id is not None:
                self._export_release(session_id)
            self._export_slots.release()
            conn.close()

    def _export_header(
        self, session_id: str, names: List[str], owner: Optional[int],
        files: List[Tuple[str, Any]],
    ) -> Dict[str, Any]:
        """Open every existing part (kept open in `files`, so a retention
        pass or restart cannot pull a file from under the stream), size-check
        against `export_max_bytes`, hash, and build the header line."""
        folder = os.path.join(self.sessions_dir, session_id)
        total = 0
        try:
            for name in names:
                try:
                    handle = self._export_open(os.path.join(folder, name))
                except FileNotFoundError:
                    continue  # a part the session does not have is omitted
                files.append((name, handle))
                total += os.fstat(handle.fileno()).st_size
            if total > self.export_max_bytes:
                raise RequestError(
                    "export-too-large",
                    f"session {session_id} is {total} bytes, over export_max_bytes "
                    f"({self.export_max_bytes}); raise it (cgprofilectl config set "
                    f"export_max_bytes) or export fewer parts",
                )
            parts: List[Dict[str, Any]] = []
            for name, handle in files:
                digest = hashlib.sha256()
                size = 0
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
                handle.seek(0)
                parts.append({"name": name, "bytes": size, "sha256": digest.hexdigest()})
        except OSError as exc:
            raise RequestError(
                "export-failed", f"cannot read session {session_id}: {exc.strerror or exc}"
            ) from None
        return {
            "ok": True, "contract": 2, "session": session_id, "parts": parts,
            "total_bytes": sum(part["bytes"] for part in parts), "owner_uid": owner,
        }

    def _export_stream(self, conn: socket.socket, files: List[Tuple[str, Any]]) -> None:
        for name, handle in files:
            seq = 0
            while True:
                if self._stopping:
                    self._export_abort(conn, "the daemon is shutting down")
                    return
                try:
                    chunk = handle.read(EXPORT_CHUNK_BYTES)
                except OSError as exc:
                    self._export_abort(conn, f"cannot read {name}: {exc.strerror or exc}")
                    return
                if not chunk:
                    break
                self._send_line(conn, {
                    "part": name, "seq": seq, "data": base64.b64encode(chunk).decode("ascii"),
                })
                seq += 1
        self._send_line(conn, {"event": "end", "status": "complete"})

    def _export_abort(self, conn: socket.socket, message: str) -> None:
        self._send_line(conn, {
            "event": "end", "status": "aborted",
            "error": {"code": "export-failed", "message": message},
        })

    def request_shutdown(self) -> None:
        self._stopping = True

    def _stop_all_sessions(self, *, aborted_reason: str) -> None:
        # CP-27 N11: ask every worker to stop and wait for them OUTSIDE the
        # daemon-wide lock (one shared STOP_JOIN_SECONDS budget), as `stop`
        # does; the locked section then only re-checks liveness.
        with self._lock:
            pending = [
                (s, s.thread) for s in self._sessions.values() if not s.finished
            ]
        for sess, _thread in pending:
            sess.stop_event.set()
        deadline = time.monotonic() + SHUTDOWN_JOIN_SECONDS
        for _sess, thread in pending:
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            live = [s for s in self._sessions.values() if not s.finished]
            for sess in live:
                # The process is exiting: a still-running sampler must not
                # strand placement and DAMON, so shutdown forces (Sol S1).
                if not self._finalize_session_locked(
                    sess, aborted_reason=aborted_reason, force=True,
                    prejoined=True,
                ):
                    self._log(
                        f"session {sess.session_id}: could not be finalized at "
                        f"shutdown; the manifest stays running and the next "
                        f"start recovers it as an orphan"
                    )

    def _install_signals(self) -> None:
        signal.signal(signal.SIGTERM, lambda signum, frame: self.request_shutdown())
        signal.signal(signal.SIGINT, lambda signum, frame: self.request_shutdown())
        signal.signal(signal.SIGHUP, lambda signum, frame: self._reload_in_thread())

    def _reload_in_thread(self) -> None:
        """SIGHUP: reload off the signal handler. A handler runs on the main
        thread between bytecodes and could interrupt it while it holds the
        settings lock; a worker thread takes the lock like any other caller."""
        threading.Thread(target=self.reload_settings, daemon=True, name="cgprofiled-reload").start()

    def _accept_loop(self) -> None:
        """The bind/accept/dispatch loop, with no signal installation of its
        own — split out from :meth:`serve_forever` so a test can drive it
        from a background thread (``signal.signal`` only works on the main
        thread of the main interpreter; the real CLI entry point always runs
        this from there, via ``serve_forever``)."""
        self._bind()
        if self._observer_sock is not None:
            self._observer_thread = threading.Thread(
                target=self._observer_accept_loop,
                daemon=True,
                name="cgprofile-resource-observer",
            )
            self._observer_thread.start()
        try:
            next_check = time.monotonic() + self.socket_check_interval
            while not self._stopping:
                if (self.socket_mode == "self-bind" and self._sock is not None
                        and self.socket_check_interval > 0 and time.monotonic() >= next_check):
                    next_check = time.monotonic() + self.socket_check_interval
                    self._check_control_socket()
                    if self._stopping:
                        break
                if self._sock is None:
                    # Keep an observer-only activated service available and
                    # let doctor report the missing control descriptor.
                    time.sleep(min(self.accept_timeout, 0.5))
                    continue
                try:
                    conn, _addr = self._sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._admit_connection(conn)
        finally:
            # No new verb may start from here on; wait for the ones in
            # flight so a late `start` cannot land after the sessions were
            # finalized (CP-33: verbs now run on their own threads).
            self._stopping = True
            self._drain_inflight()
            try:
                self._stop_all_sessions(aborted_reason="daemon-stopped")
            finally:
                try:
                    self._close_socket()
                finally:
                    if self._observer_thread is not None:
                        self._observer_thread.join(timeout=6.0)
                        if self._observer_thread.is_alive():
                            self._log(
                                "resource observer accept thread did not stop within 6 seconds"
                            )
                        self._observer_thread = None
                    self.windows.shutdown()
                    try:
                        pool_closed = self.damon_pool.close()
                    except Exception as exc:  # noqa: BLE001 - shutdown remains best-effort
                        pool_closed = False
                        self._log(
                            f"DAMON pool shutdown raised: {type(exc).__name__}: {exc}"
                        )
                    if not pool_closed:
                        quarantined = sorted(
                            getattr(self.damon_pool, "quarantined_indices", ())
                        )
                        self._log(
                            "DAMON pool shutdown could not confirm every owned slot off "
                            f"or restore its original count; quarantined={quarantined}"
                        )

    def _observer_accept_loop(self) -> None:
        observer_sock = self._observer_sock
        if observer_sock is None:
            return
        observer_identity = self._observer_socket_identity
        try:
            while not self._stopping:
                try:
                    conn, _addr = observer_sock.accept()
                except socket.timeout:
                    continue
                except OSError as exc:
                    if not self._stopping:
                        self._log(
                            "resource observer accept loop stopped unexpectedly: "
                            f"{type(exc).__name__}: {exc}"
                        )
                    return
                self._handle_observer_connection(conn)
        finally:
            with self._lock:
                still_owned = self._observer_sock is observer_sock
                if still_owned:
                    self._observer_sock = None
                    self._observer_socket_identity = None
            try:
                observer_sock.close()
            except OSError:
                pass
            if still_owned and self.socket_mode == "self-bind":
                self._unlink_observer_socket_if_owned(observer_identity)

    def _handle_observer_connection(self, conn: socket.socket) -> None:
        """Serve one strictly read-only query on the dedicated socket."""
        request_contract = RESOURCE_OBSERVER_CONTRACT_VERSION
        try:
            peer = self._peer_credentials(conn)
            uid = None if peer is None else peer[1]
            if not self._peer_allowed(uid):
                conn.sendall((json.dumps(self._error_response(
                    "peer-refused", "peer UID is not allowed by the configured socket policy",
                    contract=RESOURCE_OBSERVER_CONTRACT_VERSION,
                )) + "\n").encode("utf-8"))
                return
            deadline = time.monotonic() + 5.0
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                conn.settimeout(remaining)
                chunk = conn.recv(
                    MAX_RESOURCE_OBSERVER_REQUEST_LINE_BYTES + 1 - len(data)
                )
                if not chunk:
                    return
                data.extend(chunk)
                newline = data.find(b"\n")
                if newline >= 0:
                    if newline + 1 > MAX_RESOURCE_OBSERVER_REQUEST_LINE_BYTES:
                        response = self._error_response(
                            "bad-argument",
                            f"observer request exceeds "
                            f"{MAX_RESOURCE_OBSERVER_REQUEST_LINE_BYTES} bytes",
                            contract=RESOURCE_OBSERVER_CONTRACT_VERSION,
                        )
                    elif newline + 1 != len(data):
                        response = self._error_response(
                            "bad-argument", "observer accepts exactly one request line",
                            contract=RESOURCE_OBSERVER_CONTRACT_VERSION,
                        )
                    else:
                        payload = bytes(data[:newline])
                        request_contract = self._requested_observer_contract(payload)
                        response = self._observer_dispatch(payload, peer)
                    conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                    return
                if len(data) > MAX_RESOURCE_OBSERVER_REQUEST_LINE_BYTES:
                    response = self._error_response(
                        "bad-argument",
                        f"observer request exceeds "
                        f"{MAX_RESOURCE_OBSERVER_REQUEST_LINE_BYTES} bytes",
                        contract=RESOURCE_OBSERVER_CONTRACT_VERSION,
                    )
                    conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                    return
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        except Exception as exc:  # noqa: BLE001 - keep the daemon accept loop alive
            self._log(
                f"unhandled resource observer error: {type(exc).__name__}: {exc}"
            )
            try:
                conn.sendall((json.dumps(self._error_response(
                    "daemon-error", "resource observer failed closed",
                    contract=request_contract,
                )) + "\n").encode("utf-8"))
            except OSError:
                pass
        finally:
            conn.close()

    def _observer_dispatch(
        self, payload: bytes, peer: Optional[Tuple[int, int, int]]
    ) -> Dict[str, Any]:
        envelope_contract = self._requested_observer_contract(payload)

        def error(code: str, message: str) -> Dict[str, Any]:
            return {
                "ok": False,
                "contract": envelope_contract,
                "error": {"code": code, "message": message},
            }

        try:
            req = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return error("bad-argument", "request was not valid JSON")
        if not isinstance(req, dict) or any(not isinstance(key, str) for key in req):
            return error("bad-argument", "request must be a JSON object")
        contract = req.get("contract")
        if type(contract) is not int or contract not in RESOURCE_OBSERVER_CONTRACTS:
            return error("bad-argument", "resource observer supports contracts 2 and 3 only")

        if set(req) != {"verb", "args", "contract"}:
            return error(
                "bad-argument",
                "resource observer request requires exactly verb, args, and contract",
            )
        verb = req["verb"]
        args = req["args"]
        if not isinstance(verb, str):
            return error("bad-argument", "request 'verb' must be a string")
        if not isinstance(args, dict):
            return error("bad-argument", "request 'args' must be a JSON object")
        if contract == 3:
            return self._observer_window_verb(verb, args, peer, error)
        if verb != "resource-limits":
            return error(
                "bad-argument", "resource observer socket accepts only 'resource-limits'"
            )
        if set(args) != {"thread_id"}:
            return error(
                "bad-argument", "resource-limits requires exactly one 'thread_id' argument"
            )
        if type(args["thread_id"]) is not int or args["thread_id"] <= 0:
            return error(
                "bad-argument", "resource-limits 'thread_id' must be a positive integer"
            )
        if peer is None:
            return error(
                "peer-refused", "SO_PEERCRED is required for resource observation"
            )
        try:
            body = resource_observer_mod.observe_peer_cgroup(
                peer_pid=peer[0],
                peer_uid=peer[1],
                thread_id=args["thread_id"],
                proc_root=self.proc_root,
                cgroup_root=self.cgroup_root,
                mountinfo_path=os.path.join(self.proc_root, "self", "mountinfo"),
                gates_slice_name=self.gates_slice_name,
                contract=contract,
            )
        except resource_observer_mod.ResourceObserverError as exc:
            return error("observer-refused", str(exc))
        return {"ok": True, "contract": contract, **body}

    def _observer_window_verb(
        self,
        verb: str,
        args: Dict[str, Any],
        peer: Optional[Tuple[int, int, int]],
        error: Callable[[str, str], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Contract 3: ``window-open`` and ``window-close``.

        The bind, sampling and result live in :mod:`lib.observer_windows`; this
        method validates the wire shape and maps refusals to error codes.
        """
        def is_int(value: object) -> bool:
            return type(value) is int and value > 0

        if verb == "window-open":
            allowed = {"thread_id", "root_pid", "mode", "interval_ms"}
            required = {"thread_id", "root_pid", "mode"}
            if not required <= set(args) <= allowed:
                return error(
                    "bad-argument",
                    "window-open takes thread_id, root_pid, mode and optional interval_ms",
                )
            interval = args.get("interval_ms", observer_windows_mod.DEFAULT_INTERVAL_MS)
            if not is_int(args["thread_id"]) or not is_int(args["root_pid"]):
                return error("bad-argument", "thread_id and root_pid must be positive integers")
            if type(interval) is not int or not isinstance(args["mode"], str):
                return error("bad-argument", "mode must be a string and interval_ms an integer")
            call = lambda: self.windows.open(  # noqa: E731 - one call, two verbs
                peer_pid=None if peer is None else peer[0],
                peer_uid=None if peer is None else peer[1],
                thread_id=args["thread_id"], root_pid=args["root_pid"],
                mode=args["mode"], interval_ms=interval,
            )
        elif verb == "window-close":
            if set(args) != {"thread_id", "window"}:
                return error("bad-argument", "window-close takes exactly thread_id and window")
            if not is_int(args["thread_id"]) or not isinstance(args["window"], str):
                return error("bad-argument", "thread_id must be a positive integer, window a string")
            call = lambda: self.windows.close(  # noqa: E731
                peer_pid=None if peer is None else peer[0],
                peer_uid=None if peer is None else peer[1],
                thread_id=args["thread_id"], window_id=args["window"],
            )
        else:
            return error(
                "bad-argument",
                "contract 3 accepts only 'window-open' and 'window-close'",
            )
        try:
            body = call()
        except observer_windows_mod.WindowError as exc:
            response = error(exc.code, str(exc))
            if exc.reason is not None:
                response["error"]["reason"] = exc.reason
            return response
        return {"ok": True, "contract": 3, **body}

    def serve_forever(self) -> int:
        """Blocks until :meth:`request_shutdown` is called or a signal
        arrives; stops every live session (``aborted: "daemon-stopped"``)
        and closes the socket before returning. Returns 0, or
        :data:`EXIT_SOCKET_LOST` when the control socket vanished and could
        not be rebound (CP-31), so the restart policy recreates the daemon."""
        self._install_signals()
        self._accept_loop()
        return EXIT_SOCKET_LOST if self._socket_lost else 0
