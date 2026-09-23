"""The session server — RG55-INTERFACE-CONTRACT.md §1/§2/§5, RG-55 C4.

``SessionServer`` listens on a Unix socket (JSON-lines request/response, one
request per connection) and runs one sampling thread per live profiling
session. Each thread drives :class:`lib.sampler.Sampler` at a FIXED cadence
(hot == idle == the session's own ``interval`` — the adaptive back-off
DESIGN.md §4.4 describes for ``cgprofile run``/``attach`` is deliberately
turned off here: the contract's own computation rules (§7) assume samples
land every ``interval_seconds`` with no gaps, which a caller reading
``memory.current`` for a p90/median needs to be true), samples the target
cgroup, its slice, and the host, and feeds :class:`lib.summary.SummaryAccumulator`
and :class:`lib.store.RunDir` so that ``ctl stop`` can answer within the
contract's 30 s budget without ever recomputing from the on-disk series
(§1.5 — the accumulator is already incremental; ``finalize()`` is O(1) in
wall time).

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

import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import access, damon as damon_mod, events as events_mod, metrics, sampler as sampler_mod
from . import store, subtree, summary, targets as targets_mod, util
from . import limits as limits_mod, liveness as liveness_mod, placement as placement_mod

CONTRACT_VERSION = 1
CGPROFILE_VERSION = "1.1.0"

DEFAULT_SOCKET_PATH = "/run/cgprofile/ctl.sock"
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

# RW-14 (C5): `ctl report` renders the REAL interactive HTML — `analyze.build`
# + `report_html.render`, which need pandas/plotly. This module stays
# collector-tier (stdlib only, per the module docstring) by never importing
# either directly; instead `handle_report` shells out to the report tier's
# OWN interpreter, exactly the split `cgprofile`'s own bash shim already
# makes between collector verbs (system python) and `report` (the venv) —
# see that shim's comment. Both paths are resolved relative to this file
# (``lib/serve.py`` -> the project root two levels up) so the daemon needs no
# extra configuration to find its own venv inside the built image; a test
# overrides both via the constructor to point at whatever interpreter the
# test environment actually has the report deps installed in.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REPORT_SCRIPT = os.path.join(_PROJECT_ROOT, "cgprofile.py")
DEFAULT_REPORT_PYTHON = os.path.join(_PROJECT_ROOT, "venv", "bin", "python")
DEFAULT_REPORT_TIMEOUT = 120.0

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
_SESSION_ID_TS_FMT = "%Y%m%dT%H%M%SZ"
_SESSION_ID_RE = re.compile(r"^s-\d{8}T\d{6}Z-[0-9a-f]{4}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")

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
STREAMING_VERBS = frozenset({"watch"})
# Asserted at every start on the directory the socket lives in and on the
# socket itself (§8.1 "belt and braces to the host's tmpfiles.d entry",
# RW-37: the host's own source of truth is mdt host-setup's
# `mdt-cgprofile.conf`, NOT this daemon — the daemon only re-asserts).
SOCKET_DIR_MODE = 0o770
SOCKET_MODE = 0o660
# Comma-separated uid allowlist (§8.1). Unset/empty = no uid restriction
# beyond the socket's own group mode (docker-group trust, D-30).
ALLOW_UIDS_ENV = "CGPROFILE_ALLOW_UIDS"


def parse_allow_uids(raw: Optional[str]) -> Optional[List[int]]:
    """Parse `CGPROFILE_ALLOW_UIDS` ("0,1000,1003") into a uid list.

    `None`/empty/whitespace → `None`, meaning "no uid allowlist" (§8.1's
    default: authorisation is the socket's group mode alone). A malformed
    entry raises `ValueError` — `cgprofile serve` turns that into a refusal
    to start, because silently ignoring a misspelled allowlist would widen
    access exactly where the operator asked to narrow it.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    uids: List[int] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            uid = int(item, 10)
        except ValueError:
            raise ValueError(
                f"{ALLOW_UIDS_ENV} must be a comma-separated list of uids, got {item!r}"
            ) from None
        if uid < 0:
            raise ValueError(f"{ALLOW_UIDS_ENV} uid must not be negative, got {uid}")
        if uid not in uids:
            uids.append(uid)
    return uids or None


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


class RequestError(Exception):
    """A verb refused the request — becomes ``{"ok": false, "error": {...}}``
    (contract exit code 2), never an uncaught traceback back to the socket."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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
    interval: float
    meta: Dict[str, Any]
    started_at: str
    baseline_memory_bytes: Optional[int]
    pids_at_start: int
    host_snapshot: Dict[str, Any]
    rundir: "store.RunDir"
    summary_acc: "summary.SummaryAccumulator"
    subtree_resolver: Optional["subtree.SubtreeResolver"]
    damon_session: Optional["damon_mod.DamonSession"]
    damon_status: str
    damon_requested_on: bool
    damon_unavailable_reason: Optional[str]
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    live_samples: int = 0
    live_memory_current_bytes: Optional[int] = None
    live_memory_peak_bytes: Optional[int] = None
    live_cpu_cores_recent: Optional[float] = None
    live_damon_hot_bytes_recent: Optional[int] = None
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
    # CP-9 (C8, §8.3): the session's leaf under the gates slice, or `None`
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
        damon_default: str = "on",
        interval: float = DEFAULT_INTERVAL,
        keep_sessions: int = DEFAULT_KEEP_SESSIONS,
        keep_days: int = DEFAULT_KEEP_DAYS,
        observe_slices: Sequence[str] = (),
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        gates_slice_name: str = DEFAULT_GATES_SLICE_NAME,
        allow_uids: Optional[Sequence[int]] = None,
        cgroup_root: str = access.CGROUP_ROOT,
        proc_root: str = "/proc",
        host_proc_root: Optional[str] = None,
        daemon_name: str = DEFAULT_DAEMON_NAME,
        clock: Callable[[], float] = time.time,
        sampler_clock: Callable[[], float] = time.monotonic,
        sampler_sleep: Callable[[float], None] = time.sleep,
        watch_wait: Optional[Callable[[threading.Event, float], Any]] = None,
        session_id_fn: Optional[Callable[[], str]] = None,
        damon_pool: Optional["damon_mod.KdamondPool"] = None,
        accept_timeout: float = 0.5,
        report_python: Optional[str] = None,
        report_script: Optional[str] = None,
        report_timeout: float = DEFAULT_REPORT_TIMEOUT,
    ) -> None:
        if damon_default not in ("on", "off"):
            raise ValueError(f"damon_default must be 'on' or 'off', got {damon_default!r}")
        self.sessions_dir = sessions_dir
        self.socket_path = socket_path
        self.damon_default = damon_default
        self.default_interval = interval
        self.keep_sessions = keep_sessions
        self.keep_days = keep_days
        self.observe_slices = list(observe_slices)
        self.max_sessions = max_sessions
        self.gates_slice_name = gates_slice_name
        # §8.1/§8.6: `None` = no uid allowlist (docker-group trust via the
        # socket mode); a list = SO_PEERCRED uid must be 0 or listed.
        self.allow_uids: Optional[List[int]] = list(allow_uids) if allow_uids is not None else None
        self.cgroup_root = cgroup_root
        self.proc_root = proc_root
        # CP-10: host-wide reads (meminfo/loadavg/pressure) default to
        # `proc_root` — every real caller and every existing test that
        # passes one root for both stays byte-identical. A test that must
        # use the REAL `/proc` for pid/subtree resolution but wants a
        # controlled host-pressure reading overrides this separately.
        self.host_proc_root = host_proc_root if host_proc_root is not None else proc_root
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
        self._session_id_fn = session_id_fn or self._default_session_id
        self.damon_pool = damon_pool if damon_pool is not None else damon_mod.KdamondPool()
        # RW-14: resolved once at construction (a long-lived daemon's venv,
        # baked into the image at build time in C6, never appears mid-run) —
        # `None` means "not found", checked at `ctl report` time so the
        # error names the exact path that was missing.
        if report_python is not None:
            self.report_python = report_python
        elif os.access(DEFAULT_REPORT_PYTHON, os.X_OK):
            self.report_python = DEFAULT_REPORT_PYTHON
        else:
            self.report_python = None
        self.report_script = report_script or DEFAULT_REPORT_SCRIPT
        self.report_timeout = report_timeout
        # CP-9: how a placement leaf is removed. `os.rmdir` on the real
        # kernfs cgroup directory; a test pointing `cgroup_root` at a tmp
        # tree replaces it, because a REAL directory holding the same
        # interface files is ENOTEMPTY where a cgroup is not (see
        # `lib.placement.LanePlacement`'s own note).
        self.cgroup_rmdir: Callable[[str], None] = os.rmdir

        self._sessions: Dict[str, _Session] = {}
        self._by_target: Dict[Tuple[str, Optional[str]], str] = {}
        self._lock = threading.RLock()
        self._sock: Optional[socket.socket] = None
        self._stopping = False

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
        real = os.path.realpath(path)
        roots = self._writable_roots()
        if not any(real == root or real.startswith(root + os.sep) for root in roots):
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
            if not os.path.isfile(manifest_path):
                continue
            try:
                with open(manifest_path, "r", encoding="utf-8") as fh:
                    manifest = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("status") != "live":
                continue
            self._finalize_orphan(name, manifest)

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
        manifest["aborted_reason"] = "daemon-restarted"
        manifest["ended_at"] = ended_at
        if acc.sample_count:
            summary_doc = acc.finalize(ended_at=ended_at)
            rundir.write_json("summary.json", summary_doc)
        rundir.write_manifest(manifest)

    # ── registry helpers ─────────────────────────────────────────────────

    def _count_live(self) -> int:
        return sum(1 for s in self._sessions.values() if not s.finished)

    def _manifest_for(self, sess: _Session, *, status: str) -> Dict[str, Any]:
        started_epoch = self._parse_iso_epoch(sess.started_at)
        ended_epoch = self._parse_iso_epoch(sess.ended_at) if sess.ended_at else None
        # CP-7 (C3): lazy, mirroring `cgprofile.py`'s own `cmd_serve` lazy
        # import of `lib.serve` — `cgprofile.py` is the top-level script
        # (never imported at this module's top level, so this stays a
        # one-directional dependency at call time only), and `_limits_snapshot`
        # is its own helper (the exact shape `cmd_collect` already writes)
        # rather than a duplicate reimplementation here.
        import cgprofile as cg_module
        return {
            "session": sess.session_id,
            "status": status,
            "scope": sess.scope,
            "container_id": sess.container_id,
            "cgroup": sess.cgroup,
            "token": sess.token,
            "slice_name": sess.slice_name,
            "interval_seconds": sess.interval,
            "started_at": sess.started_at,
            "ended_at": sess.ended_at,
            "damon_enabled": sess.damon_requested_on,
            "damon_kdamond": sess.damon_session.kdamond_idx if sess.damon_session else None,
            "damon_thresholds": sess.damon_session.thresholds if sess.damon_session else None,
            "damon_unavailable_reason": sess.damon_unavailable_reason,
            "meta": sess.meta,
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

    def handle_version(self, args: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            live = self._count_live()
        damon_state = "available" if damon_mod.available() else "unavailable:not available on this host"
        return {
            "ok": True,
            "contract": CONTRACT_VERSION,
            "cgprofile": CGPROFILE_VERSION,
            "daemon": {
                "name": self.daemon_name,
                "started_at": self.started_at,
                "damon": damon_state,
                "damon_default": self.damon_default,
                "sessions_live": live,
                "max_sessions": self.max_sessions,
            },
            # §8.6, additive under `contract: 1`: which carriers this daemon
            # answers on. `exec` is permanent (§8.1 rule 5) so it is a
            # literal `True`, not a probe; the socket half reports the real
            # listener state, the effective uid allowlist (`[]` = none
            # configured, i.e. docker-group trust) and that SO_PEERCRED is
            # enforced on every accepted connection.
            "transports": {
                "exec": True,
                "socket": {
                    "path": self.socket_path,
                    "listening": self._sock is not None,
                    "allow_uids": list(self.allow_uids or []),
                    "peer_cred": True,
                },
            },
        }

    # ── verb: start ──────────────────────────────────────────────────────

    def handle_start(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target_spec = args.get("target")
        scope = args.get("scope")
        token = args.get("token")
        damon_req = args.get("damon") or self.damon_default
        interval_req = args.get("interval")
        meta = args.get("meta")

        if not isinstance(target_spec, str) or not target_spec.startswith("containerid:"):
            raise RequestError("bad-argument", "--target must be 'containerid:<64 hex>'")
        container_id = target_spec[len("containerid:"):]
        if not _CONTAINER_ID_RE.match(container_id):
            raise RequestError("bad-argument", "containerid must be 64 lowercase hex characters")
        if scope not in ("container", "container-shared"):
            raise RequestError("bad-argument", "--scope must be 'container' or 'container-shared'")
        if damon_req not in ("on", "off"):
            raise RequestError("bad-argument", "--damon must be 'on' or 'off'")
        if token is not None and not _TOKEN_RE.match(token):
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

        with self._lock:
            if token is not None:
                key = (container_id, token)
                existing_id = self._by_target.get(key)
                if existing_id is not None:
                    existing = self._sessions.get(existing_id)
                    if existing is not None and not existing.finished:
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

            sess = self._create_session_locked(
                container_id=container_id, cgroup=cgroup, scope=scope, token=token,
                interval=interval, damon_req=damon_req, meta=meta, policy=policy,
                place_request=place_request,
            )
            self._sessions[sess.session_id] = sess
            if token is not None:
                self._by_target[(container_id, token)] = sess.session_id

            thread = threading.Thread(
                target=self._session_loop, args=(sess,), daemon=True,
                name=f"cgprofile-session-{sess.session_id}",
            )
            sess.thread = thread
            thread.start()

            return self._start_response(sess, reused=False)

    def _create_session_locked(
        self, *, container_id: str, cgroup: str, scope: str, token: Optional[str],
        interval: float, damon_req: str, meta: Dict[str, Any],
        policy: Optional["liveness_mod.Policy"] = None,
        place_request: Optional["placement_mod.PlacementRequest"] = None,
    ) -> _Session:
        session_id = self._session_id_fn()
        started_at = self._iso(self.clock())
        abs_target = os.path.join(self.cgroup_root, cgroup.lstrip("/"))
        initial_target_metrics = summary.sample_target_cgroup(abs_target)
        baseline = (initial_target_metrics.get("mem") or {}).get("current")
        pids_now = summary.read_cgroup_pids(abs_target)
        pids_at_start = len(pids_now)
        # RW-14: sampled once, at session creation — the manifest's "host"
        # field mirrors `cmd_collect`'s own convention of a single snapshot
        # written into the manifest at start, not updated thereafter.
        host_snapshot = metrics.sample_host(proc_root=self.host_proc_root)
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
                cgroup_abs_path=abs_target, token=token, proc_root=self.proc_root
            )
            subtree_resolver.refresh()

        initial_pids = (
            list(subtree_resolver.current_pids)
            if subtree_resolver is not None else list(pids_now)
        )

        rundir = store.RunDir(self.sessions_dir, run_id=session_id, create=True)
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
            placement_obj.apply(
                list(subtree_resolver.current_pids) if subtree_resolver is not None else []
            )
            if placement_obj.error is not None:
                self._log(
                    f"start: placement refused for {session_id}: {placement_obj.error}"
                )

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
                        candidate.__exit__(None, None, None)
                        damon_unavailable_reason = f"{type(exc).__name__}: {exc}"
                        damon_status = f"unavailable:{damon_unavailable_reason}"
                    else:
                        damon_session_obj = candidate
                        damon_status = "on"

        acc = summary.SummaryAccumulator(
            session=session_id, daemon_name=self.daemon_name, daemon_version=CGPROFILE_VERSION,
            scope=scope, started_at=started_at, interval_seconds=interval,
            container_id=container_id, cgroup=cgroup, token=token, slice_name=slice_name,
            damon_enabled=damon_requested_on,
            damon_kdamond=damon_session_obj.kdamond_idx if damon_session_obj else None,
            damon_thresholds=damon_session_obj.thresholds if damon_session_obj else None,
        )
        if damon_unavailable_reason is not None:
            acc.mark_damon_unavailable(damon_unavailable_reason)

        initial_damon_bytes: Optional[Dict[str, int]] = None
        if damon_session_obj is not None:
            initial_damon_bytes = damon_session_obj.last_class_bytes

        sess = _Session(
            session_id=session_id, scope=scope, container_id=container_id, cgroup=cgroup,
            slice_name=slice_name, slice_cgroup=slice_cgroup, token=token, interval=interval,
            meta=meta, started_at=started_at, baseline_memory_bytes=baseline,
            pids_at_start=pids_at_start, host_snapshot=host_snapshot, rundir=rundir,
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
        )
        # The synchronous sample-zero read is also the liveness baseline.
        # Summary accounting already records it below; feeding the same
        # snapshot to the tracker keeps the two views aligned, so an
        # immediate status reports the real initial reading and the first
        # later tick measures CPU/IO deltas from that same point.
        self._observe_liveness(
            sess, mono=0.0,
            record={
                "seq": 0, "t": self._parse_iso_epoch(started_at), "mono": 0.0,
                "cg": {cgroup: initial_target_metrics}, "host": host_snapshot,
            },
            abs_target=abs_target, target_metrics=initial_target_metrics,
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

        return placement_mod.LanePlacement(
            cgroup_root=self.cgroup_root,
            gates_cgroup=targets_mod.slice_to_path(self.gates_slice_name),
            token=token, origin_cgroup=origin_cgroup, request=request,
            on_write=on_write, log=self._log, sleep=self.sampler_sleep,
            rmdir=self.cgroup_rmdir,
        )

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

    def _session_loop(self, sess: _Session) -> None:
        abs_target = os.path.join(self.cgroup_root, sess.cgroup.lstrip("/"))
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
                "cg": {sess.cgroup: summary.sample_target_cgroup(abs_target)},
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
                pids = list(sess.subtree_resolver.refresh())
                sess.last_discovery_mono = mono
                if sess.damon_session is not None:
                    sess.damon_session.recommit_targets(pids)
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
                new_pids = summary.read_cgroup_pids(abs_target)
                sess.last_discovery_mono = mono
                if sess.damon_session is not None and set(new_pids) != set(sess.no_token_pids):
                    sess.damon_session.recommit_targets(new_pids)
                sess.no_token_pids = new_pids
            pids = sess.no_token_pids

        # CP-9 (§8.3): "migrate every pid the token resolver discovers — also
        # pids found later". The resolver keeps finding descendants for as
        # long as the lane forks, and a pid left behind in the devcontainer's
        # scope is a pid whose memory is not the lane's. Same discovery
        # cadence as the resolution above (`migrate` skips what it already
        # moved, so this is a set difference, not a re-write per tick).
        if due and sess.placement is not None and sess.placement.placed:
            sess.placement.migrate(pids)

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
                    sess.rundir.append("events", event.to_dict())
            sess.last_effective_limits = new_effective_limits

        damon_bytes: Optional[Dict[str, int]] = None
        if sess.damon_session is not None:
            sess.damon_session.collect()
            damon_bytes = sess.damon_session.last_class_bytes

        with sess.lock:
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
            sess.rundir.append("events", event.to_dict())

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
        sess.rundir.append("samples", {
            "seq": record.get("seq"), "t": record.get("t"), "mono": mono,
            "cg": {sess.cgroup: target_metrics},
        })
        sess.rundir.append("host", {"host": host_metrics, "slice": slice_metrics})
        if damon_bytes is not None:
            if isinstance(record.get("seq"), int):
                sess.rundir.append("damon", {"_seq": record.get("seq"), **damon_bytes})
            else:
                # Direct unit callers may provide a sample record without a
                # sampler sequence; retain the historical flat shape for
                # those synthetic records.
                sess.rundir.append("damon", damon_bytes)

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
            self._enforce_stall_kill(sess, pids)

    def _gates_slice_psi_full_avg10(self) -> Optional[float]:
        """The gates slice's own memory `full avg10` — half of §8.4's pause
        condition (the other half is host PSI, already in every sample).
        `None` when the slice does not exist on this host, which pauses
        nothing: an absent capacity object is not pressure."""
        cgroup_path = targets_mod.slice_to_path(self.gates_slice_name)
        abs_path = os.path.join(self.cgroup_root, cgroup_path.lstrip("/"))
        return util.read_pressure(os.path.join(abs_path, "memory.pressure")).get("full_avg10")

    @staticmethod
    def _kill_targets(sess: _Session, pids: List[int]) -> Tuple[List[int], Optional[str]]:
        """What `--on-stall kill` may kill, or why it may not.

        With a token the subtree IS the lane, and killing it is exactly what
        §8.4 asks for. Without one, scope `container` still means "the
        container is the lane" (an ephemeral gate container — everything in
        that cgroup is the work). Scope `container-shared` WITHOUT a token
        is the one case the daemon refuses: that cgroup is the whole
        devcontainer, and "SIGKILL to the token's pid subtree" with no token
        would mean killing the IDE, the agents and the caller that asked.
        The refusal is recorded on the verdict, never silently dropped.
        """
        if sess.token is not None or sess.scope == "container":
            return list(pids), None
        return [], "no-token-in-shared-scope"

    def _enforce_stall_kill(self, sess: _Session, pids: List[int]) -> None:
        # CP-9 (§8.3/§8.4): a PLACED session is killed with ONE write of "1"
        # to `<gates slice>/rg-<token>/cgroup.kill`. The kernel applies that
        # to every pid in the leaf atomically, so — unlike the pid loop below
        # — it cannot miss a process that forked between the resolver's walk
        # and the signal. No `_kill_targets` consultation is needed for it
        # either: the leaf holds THIS lane's pids and nothing else, which is
        # exactly what `_kill_targets`' shared-scope refusal exists to
        # protect against. The pid loop stays as the fallback for every
        # unplaced session (an exec-mode or bare-host lane that was refused
        # placement, or never asked for it), so it is never dead code.
        if sess.placement is not None and sess.placement.placed:
            if sess.placement.kill():
                with sess.lock:
                    sess.watch.record_kill(
                        pids,
                        via=f"cgroup.kill applied to {sess.placement.leaf_cgroup} "
                            f"({len(pids)} pid(s) known to the resolver)",
                    )
                self._log(
                    f"watch {sess.session_id}: state {sess.watch.state}, cgroup.kill written "
                    f"to {sess.placement.leaf_cgroup}"
                )
                self._finalize_after_kill(sess)
                return
            # The leaf would not take the write (a kernel too old for
            # `cgroup.kill`, or the leaf already gone). Fall through to the
            # pid loop rather than report a kill that did not happen.
        targets, refusal = self._kill_targets(sess, pids)
        if refusal is not None:
            with sess.lock:
                sess.watch.record_kill_refused(refusal)
            self._log(
                f"watch {sess.session_id}: --on-stall kill refused ({refusal}): "
                f"a shared-scope session without a token has no subtree to kill"
            )
            return
        killed: List[int] = []
        own = os.getpid()
        for pid in targets:
            # Never pid 1 (the container's own init, whose death takes the
            # daemon's container with it) and never this daemon.
            if pid <= 1 or pid == own:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                continue  # already gone between the walk and the signal
            killed.append(pid)
        with sess.lock:
            sess.watch.record_kill(killed)
        self._log(
            f"watch {sess.session_id}: state {sess.watch.state}, SIGKILL sent to "
            f"{len(killed)} pid(s)"
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
                self._finalize_session_locked(sess, aborted_reason=None)
        self._run_retention()

    # ── verb: status / host ──────────────────────────────────────────────

    def handle_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args.get("session")
        if session_id is None:
            with self._lock:
                entries = [
                    self._status_entry(s) for s in self._sessions.values() if not s.finished
                ]
            return {
                "ok": True, "contract": CONTRACT_VERSION, "at": self._iso(self.clock()),
                "sessions": entries, "host": self._host_snapshot(),
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
        return {
            "ok": True, "contract": CONTRACT_VERSION, "session": entry,
            "host": self._host_snapshot(),
        }

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

    def handle_host(self, args: Dict[str, Any]) -> Dict[str, Any]:
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

        return {
            "at": self._iso(self.clock()),
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
        it) -- the capacity object C8's placed leaves (`rg-<token>`) live
        under. `present: false` and nothing else when the slice does not
        exist on this host at all (host-setup's P8 unit not installed and
        nothing has ever rendered a child under it either) -- contract §8.5's
        own two-shape union, so a consumer can branch on one key."""
        cgroup_path = targets_mod.slice_to_path(self.gates_slice_name)
        abs_path = os.path.join(self.cgroup_root, cgroup_path.lstrip("/"))
        if not os.path.isdir(abs_path):
            return {"name": self.gates_slice_name, "present": False}
        leaves = sorted(
            os.path.basename(child)
            for child in targets_mod.list_children(cgroup_path, root=self.cgroup_root)
            if os.path.basename(child).startswith("rg-")
        )
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
            # `leaves` is read straight off disk, not from session bookkeeping
            # -- C8 has not landed yet in this codebase, so it is always `[]`
            # today; once placement exists this needs no further change, it
            # already counts whatever `rg-*` leaves are actually on disk.
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

    def handle_stop(self, args: Dict[str, Any]) -> Dict[str, Any]:
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
                return self._stop_response(session_id, on_disk, already_stopped=True)
            if sess.finished:
                return self._stop_response(session_id, sess.summary_doc, already_stopped=True)
            self._finalize_session_locked(sess, aborted_reason=None)
            summary_doc = sess.summary_doc
        response = self._stop_response(session_id, summary_doc, already_stopped=False)
        self._run_retention()
        return response

    def _read_stored_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(self.sessions_dir, session_id, "summary.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def _finalize_session_locked(self, sess: _Session, *, aborted_reason: Optional[str]) -> None:
        """Stop ``sess``'s thread and write its final summary/manifest.
        Called with :attr:`_lock` held — every caller (``stop``, shutdown,
        the session-loop's own error path) already holds it."""
        sess.stop_event.set()
        if sess.thread is not None and sess.thread is not threading.current_thread():
            sess.thread.join(timeout=10.0)
        ended_at = self._iso(self.clock())
        with sess.lock:
            if sess.summary_acc.sample_count == 0:
                # Stopped before a single tick landed — contract §1.5 still
                # promises a summary, so take one sample right now rather
                # than let finalize() refuse an empty accumulator.
                abs_target = os.path.join(self.cgroup_root, sess.cgroup.lstrip("/"))
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
        if sess.damon_session is not None:
            sess.damon_session.__exit__(None, None, None)
        sess.ended_at = ended_at
        sess.summary_doc = summary_doc
        sess.finished = True
        sess.rundir.write_json("summary.json", summary_doc)
        manifest = self._manifest_for(sess, status="aborted" if aborted_reason else "finished")
        manifest["aborted_reason"] = aborted_reason
        sess.rundir.write_manifest(manifest)

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

    # ── verb: report ─────────────────────────────────────────────────────

    def handle_report(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """RW-14: renders the REAL interactive HTML report — `analyze.build`
        + `report_html.render`, exactly what `cgprofile report` already
        produces for a `cgprofile run`/`attach` session — never a hand-rolled
        stub. This module stays collector-tier (stdlib only) by never
        importing either directly: the session directory this daemon writes
        is already shaped like any other `lib.store.RunDir` (see
        `_manifest_for` and `_on_session_sample`'s own RW-14 comments), so
        the existing report tier can render it unmodified, run from ITS OWN
        interpreter (`self.report_python`, the venv baked into the image at
        build time) as a subprocess of `self.report_script` (`cgprofile.py`
        itself) — the same system-python/venv split the `cgprofile` bash
        shim already makes between collector verbs and `report`.
        """
        session_id = args.get("session")
        if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
            raise RequestError("unknown-session", f"no session {session_id!r} is live or on record")
        session_dir = os.path.join(self.sessions_dir, session_id)
        summary_doc = self._read_stored_summary(session_id)
        if summary_doc is None:
            raise RequestError("unknown-session", f"no finished session {session_id} on record")
        if not self.report_python or not os.access(self.report_python, os.X_OK):
            raise RequestError(
                "report-unavailable",
                f"no executable report-tier interpreter at {self.report_python!r} — "
                "the image's venv is missing (rebuild the image, or run ./setup.sh in dev)",
            )
        report_path = os.path.join(session_dir, "report.html")
        self._guard_path(report_path)
        command = [
            self.report_python, self.report_script, "report",
            "--run-dir", session_dir, "--html-only",
        ]
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=self.report_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RequestError(
                "report-failed", f"report subprocess timed out after {self.report_timeout}s",
            ) from exc
        if proc.returncode != 0 or not os.path.isfile(report_path):
            tail_source = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = tail_source[-1] if tail_source else f"report subprocess exited {proc.returncode}"
            raise RequestError("report-failed", tail)
        return {"ok": True, "contract": CONTRACT_VERSION, "path": report_path}

    # ── verb: gc / retention ─────────────────────────────────────────────

    def handle_gc(self, args: Dict[str, Any]) -> Dict[str, Any]:
        removed, kept = self._run_retention()
        return {"ok": True, "contract": CONTRACT_VERSION, "removed": removed, "kept": kept}

    def _run_retention(self) -> Tuple[List[str], int]:
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
            finished.append((name, manifest))
        finished.sort(key=lambda pair: pair[0])

        keep_newest: Set[str] = (
            {name for name, _ in finished[-self.keep_sessions:]} if self.keep_sessions > 0 else set()
        )
        cutoff = self.clock() - self.keep_days * 86400.0

        removed: List[str] = []
        kept = 0
        for name, manifest in finished:
            ended_epoch = self._parse_iso_epoch(manifest.get("ended_at"))
            too_old = ended_epoch is not None and ended_epoch < cutoff
            if name in keep_newest and not too_old:
                kept += 1
            else:
                self._remove_session_dir(name)
                removed.append(name)
        return removed, kept

    def _remove_session_dir(self, name: str) -> None:
        path = os.path.join(self.sessions_dir, name)
        self._guard_path(path)
        shutil.rmtree(path, ignore_errors=True)

    # ── dispatch / socket loop ───────────────────────────────────────────

    def _dispatch(self, req: Dict[str, Any]) -> Dict[str, Any]:
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
        verb, args, wire_error = self._validate_wire(req)
        if wire_error is not None:
            return wire_error
        if verb in STREAMING_VERBS:
            # §8.8's `not-streaming`: the verb exists and the request is
            # well formed, but this path answers exactly one object and the
            # caller is waiting for exactly one. The streaming path is
            # `_handle_connection`'s own (see STREAMING_VERBS).
            return self._error_response(
                "not-streaming",
                f"{verb!r} streams one JSON object per line until the session ends "
                f"(contract §8.2) and cannot be answered as a single response",
            )
        handlers = {
            "version": self.handle_version,
            "start": self.handle_start,
            "status": self.handle_status,
            "host": self.handle_host,
            "stop": self.handle_stop,
            "report": self.handle_report,
            "gc": self.handle_gc,
        }
        handler = handlers.get(verb)
        if handler is None:
            return self._error_response("bad-argument", f"unknown verb {verb!r}")
        try:
            resp = handler(args)
        except RequestError as exc:
            return self._error_response(exc.code, exc.message)
        if verb == "stop" and resp.get("ok"):
            resp["session_dir"] = os.path.join(self.sessions_dir, resp["session"])
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
        unexpected = sorted(set(req) - _WIRE_KEYS)
        if unexpected:
            return None, {}, self._error_response(
                "bad-argument",
                f"unexpected request key(s) {unexpected!r}: a request is "
                f"{{\"verb\", \"args\", \"contract\"}} (contract §8.1)",
            )
        contract = req.get("contract", CONTRACT_VERSION)
        if contract != CONTRACT_VERSION:
            return None, {}, self._error_response(
                "bad-argument",
                f"request contract {contract!r}, this daemon speaks contract {CONTRACT_VERSION}",
            )
        args = req.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None, {}, self._error_response(
                "bad-argument", "request 'args' must be a JSON object"
            )
        return req.get("verb"), args, None

    @staticmethod
    def _error_response(code: str, message: str) -> Dict[str, Any]:
        return {"ok": False, "contract": CONTRACT_VERSION, "error": {"code": code, "message": message}}

    def _bind(self) -> None:
        socket_dir = os.path.dirname(self.socket_path)
        if socket_dir:
            os.makedirs(socket_dir, exist_ok=True)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.socket_path)
        sock.listen(16)
        sock.settimeout(self.accept_timeout)
        self._sock = sock
        self._assert_socket_permissions(socket_dir)

    def _assert_socket_permissions(self, socket_dir: str) -> None:
        """Re-assert §8.1's directory/socket permissions at every start.

        The HOST is the source of truth for the directory's ownership: mdt
        host-setup ships `mdt-cgprofile.conf` (`d /run/cgprofile 0770 root
        docker -`, RW-37) and that tmpfiles.d entry decides WHICH group may
        reach the carrier. This daemon only re-asserts, belt and braces:
        mode `0770` on the directory, and on the socket owner `root` with
        THE DIRECTORY'S OWN gid (RW-35(b) — never a hardcoded `docker`,
        which does not exist inside this image and whose host gid varies
        per host) at mode `0660`.

        A `root:root` directory means host-setup is not installed yet: the
        socket ends up root-only and ONE INFO line says so. That is not an
        error — the exec carrier is unaffected and stays the default
        (§8.1's carrier table), so the daemon keeps serving either way.
        Every step is best-effort for the same reason: a permission failure
        (the unprivileged daemon a test runs) must degrade the SOCKET
        carrier, never take the daemon down.
        """
        dir_gid: Optional[int] = None
        if socket_dir:
            try:
                os.chmod(socket_dir, SOCKET_DIR_MODE)
            except OSError as exc:
                self._log(f"could not chmod {socket_dir} to {SOCKET_DIR_MODE:04o}: {exc}")
            try:
                dir_gid = os.stat(socket_dir).st_gid
            except OSError as exc:
                self._log(f"could not stat {socket_dir}: {exc}")
        if dir_gid is not None:
            try:
                os.chown(self.socket_path, 0, dir_gid)
            except OSError as exc:
                self._log(
                    f"could not set {self.socket_path} owner root:gid={dir_gid}: {exc}"
                )
            if dir_gid == 0:
                self._log(
                    f"socket carrier root-only until host-setup is installed "
                    f"({socket_dir} is root:root — mdt host-setup's "
                    f"mdt-cgprofile.conf owns that directory); the exec "
                    f"carrier is unaffected"
                )
        try:
            os.chmod(self.socket_path, SOCKET_MODE)
        except OSError as exc:
            self._log(f"could not chmod {self.socket_path} to {SOCKET_MODE:04o}: {exc}")

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

    def _peer_allowed(self, uid: Optional[int]) -> bool:
        """§8.1's authorisation rule. uid 0 is ALWAYS allowed (that is how
        the exec carrier arrives — `docker exec` runs as root inside the
        daemon, D-30). With no allowlist configured, everyone the socket
        mode already let connect is allowed (docker-group trust). With an
        allowlist, only those uids — and an unreadable peer credential is
        REFUSED in that case, because failing open would quietly void the
        one control the operator explicitly asked for.
        """
        if self.allow_uids is None:
            return True
        if uid is None:
            return False
        return uid == 0 or uid in self.allow_uids

    def _close_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    def _handle_connection(self, conn: socket.socket) -> None:
        handed_off = False
        try:
            conn.settimeout(25.0)
            uid = self._peer_uid(conn)
            if not self._peer_allowed(uid):
                named = "unavailable" if uid is None else str(uid)
                resp = self._error_response(
                    "peer-refused",
                    f"peer uid {named} is not in {ALLOW_UIDS_ENV}",
                )
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                return
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            if not data.strip():
                return
            try:
                req = json.loads(data.decode("utf-8"))
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
                if wire_error is None and verb in STREAMING_VERBS:
                    thread = threading.Thread(
                        target=self._watch_connection, args=(conn, args), daemon=True,
                        name=f"cgprofile-watch-{args.get('session')}",
                    )
                    handed_off = True
                    thread.start()
                    return
                try:
                    resp = self._dispatch(req)
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
                    print(
                        f"cgprofile: unhandled error handling verb "
                        f"{req.get('verb')!r}: {type(exc).__name__}: {exc}",
                        file=sys.stderr, flush=True,
                    )
                    return
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        finally:
            if not handed_off:
                conn.close()

    # ── verb: watch (§8.2, the streaming exception) ─────────────────────

    @staticmethod
    def _send_line(conn: socket.socket, doc: Dict[str, Any]) -> None:
        conn.sendall((json.dumps(doc) + "\n").encode("utf-8"))

    def _watch_connection(self, conn: socket.socket, args: Dict[str, Any]) -> None:
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

    def request_shutdown(self) -> None:
        self._stopping = True

    def _stop_all_sessions(self, *, aborted_reason: str) -> None:
        with self._lock:
            live = [s for s in self._sessions.values() if not s.finished]
            for sess in live:
                self._finalize_session_locked(sess, aborted_reason=aborted_reason)

    def _install_signals(self) -> None:
        signal.signal(signal.SIGTERM, lambda signum, frame: self.request_shutdown())
        signal.signal(signal.SIGINT, lambda signum, frame: self.request_shutdown())

    def _accept_loop(self) -> None:
        """The bind/accept/dispatch loop, with no signal installation of its
        own — split out from :meth:`serve_forever` so a test can drive it
        from a background thread (``signal.signal`` only works on the main
        thread of the main interpreter; the real CLI entry point always runs
        this from there, via ``serve_forever``)."""
        self._bind()
        try:
            while not self._stopping:
                try:
                    conn, _addr = self._sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._handle_connection(conn)
        finally:
            self._stop_all_sessions(aborted_reason="daemon-stopped")
            self._close_socket()

    def serve_forever(self) -> None:
        """Blocks until :meth:`request_shutdown` is called or a signal
        arrives; stops every live session (``aborted: "daemon-stopped"``)
        and closes the socket before returning."""
        self._install_signals()
        self._accept_loop()
