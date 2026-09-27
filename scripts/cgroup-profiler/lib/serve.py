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
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import access, damon as damon_mod, metrics, sampler as sampler_mod
from . import store, subtree, summary, targets as targets_mod, util
from .version import runtime_version

CONTRACT_VERSION = 1
CGPROFILE_VERSION = runtime_version("1.0.0")

DEFAULT_SOCKET_PATH = "/run/cgprofile/ctl.sock"
DEFAULT_SESSIONS_DIR = "/var/lib/cgprofile/sessions"
DEFAULT_DAEMON_NAME = "cgprofile-host-daemon"
DEFAULT_MAX_SESSIONS = 16
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
        cgroup_root: str = access.CGROUP_ROOT,
        proc_root: str = access.PROC_ROOT,
        daemon_name: str = DEFAULT_DAEMON_NAME,
        clock: Callable[[], float] = time.time,
        sampler_clock: Callable[[], float] = time.monotonic,
        sampler_sleep: Callable[[float], None] = time.sleep,
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
        self.cgroup_root = cgroup_root
        self.proc_root = proc_root
        self.daemon_name = daemon_name
        self.clock = clock
        self.sampler_clock = sampler_clock
        self.sampler_sleep = sampler_sleep
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
            # directory with no translation step. `limits` is deliberately
            # `{}` (the daemon does not resolve effective cgroup limits —
            # that is `cmd_collect`'s own job, out of scope for P1; filed as
            # a backlog item, not silently reproduced here).
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
            "limits": {},
            "host": sess.host_snapshot,
            "config": {"interval": sess.interval},
        }

    # ── verb: version ────────────────────────────────────────────────────

    def handle_version(self, req: Dict[str, Any]) -> Dict[str, Any]:
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
        }

    # ── verb: start ──────────────────────────────────────────────────────

    def handle_start(self, req: Dict[str, Any]) -> Dict[str, Any]:
        target_spec = req.get("target")
        scope = req.get("scope")
        token = req.get("token")
        damon_req = req.get("damon") or self.damon_default
        interval_req = req.get("interval")
        meta = req.get("meta")

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
                interval=interval, damon_req=damon_req, meta=meta,
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
    ) -> _Session:
        session_id = self._session_id_fn()
        started_at = self._iso(self.clock())
        abs_target = os.path.join(self.cgroup_root, cgroup.lstrip("/"))
        initial_target_metrics = summary.sample_target_cgroup(abs_target)
        baseline = (initial_target_metrics.get("mem") or {}).get("current")
        # The token resolver below will resolve direct target-cgroup PIDs;
        # avoid doing the broader proc-to-cgroup map a second time here.
        pids_now = (
            targets_mod.pids_in_cgroup(cgroup, self.cgroup_root, self.proc_root)
            if token is None else []
        )
        # RW-14: sampled once, at session creation — the manifest's "host"
        # field mirrors `cmd_collect`'s own convention of a single snapshot
        # written into the manifest at start, not updated thereafter.
        host_snapshot = metrics.sample_host(proc_root=self.proc_root)
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
        with open(rundir.stream_path("events"), "a", encoding="utf-8"):
            pass  # touch: see the module docstring — event detection is CP-4.

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
            sampler_origin_mono=sampler_origin_mono,
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
                "host": metrics.sample_host(proc_root=self.proc_root),
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
                new_pids = targets_mod.pids_in_cgroup(
                    sess.cgroup, self.cgroup_root, self.proc_root,
                )
                sess.last_discovery_mono = mono
                if sess.damon_session is not None and set(new_pids) != set(sess.no_token_pids):
                    sess.damon_session.recommit_targets(new_pids)
                sess.no_token_pids = new_pids
            pids = sess.no_token_pids

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
            if sess._prev_cpu_usage_usec is not None and sess._prev_mono is not None:
                dt = mono - sess._prev_mono
                rate = util.rate(sess._prev_cpu_usage_usec, usage, dt)
                sess.live_cpu_cores_recent = None if rate is None else rate / 1_000_000.0
            sess._prev_cpu_usage_usec = usage
            sess._prev_mono = mono
            if damon_bytes is not None:
                sess.live_damon_hot_bytes_recent = damon_bytes.get("hot")

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

    # ── verb: status / host ──────────────────────────────────────────────

    def handle_status(self, req: Dict[str, Any]) -> Dict[str, Any]:
        session_id = req.get("session")
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
            }

    def handle_host(self, req: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "contract": CONTRACT_VERSION, "host": self._host_snapshot()}

    def _host_snapshot(self) -> Dict[str, Any]:
        host = metrics.sample_host(proc_root=self.proc_root)
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

    def handle_stop(self, req: Dict[str, Any]) -> Dict[str, Any]:
        session_id = req.get("session")
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
            summary_doc = sess.summary_acc.finalize(ended_at=ended_at)
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

    def handle_report(self, req: Dict[str, Any]) -> Dict[str, Any]:
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
        session_id = req.get("session")
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

    def handle_gc(self, req: Dict[str, Any]) -> Dict[str, Any]:
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
        verb = req.get("verb")
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
            resp = handler(req)
        except RequestError as exc:
            return self._error_response(exc.code, exc.message)
        if verb == "stop" and resp.get("ok"):
            resp["session_dir"] = os.path.join(self.sessions_dir, resp["session"])
        return resp

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
        try:
            conn.settimeout(25.0)
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
            conn.close()

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
