"""Observer contract 3: per-subject measurement windows.

A window measures one subject (a process tree rooted at ``root_pid``) between
``window-open`` and ``window-close`` and returns the ``resource-profile/1``
metric block (docs/design/RESOURCE-PROFILES.md section 2). The daemon is the
single source of these numbers: consumers never read procfs or cgroupfs to fill
a gap.

Two modes:

``process-tree``
    Works in a shared container. Summed RSS and the I/O byte counters come from
    walking the root's descendants every interval; CPU comes from the root's
    own ``/proc/<pid>/stat`` counters at close. Everything that only a cgroup
    can answer is ``REQUIRES_CGROUP_MODE``.

``cgroup``
    Exact numbers from the leaf that holds the root: per-file-reset
    ``memory.peak``/``memory.swap.peak``, ``cpu.stat``, ``io.stat`` and PSI
    deltas. It is attributable only while nothing but the root's tree (plus the
    idle peer and the peer's ancestors) runs in that leaf; a foreign PID seen
    during the window turns every cgroup-only metric into ``SHARED_CGROUP``.

Sampling runs on one daemon-owned thread that serves every window, never inside
a request handler. A window keeps running maxima and sums only, so its memory is
bounded. Windows are lost on daemon restart: the window id carries a per-daemon
epoch so a stale id is answered ``window-lost`` rather than "unknown".
"""

from __future__ import annotations

import errno
import os
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import ancestor_events, proc_stat, resource_observer, util

MODES = ("process-tree", "cgroup")
INTERVAL_MIN_MS = 100
INTERVAL_MAX_MS = 5000
DEFAULT_INTERVAL_MS = 250
MAX_WINDOWS_PER_PEER = 64
MAX_WINDOWS_PER_DAEMON = 4096
WINDOW_TTL_SECONDS = 6 * 3600
_TOMBSTONES = 4096
#: Longest parent chain `_descends_from` follows (also ends a ppid cycle).
_MAX_CHAIN_DEPTH = 64

METRIC_KEYS = (
    "rss_peak_bytes", "memory_peak_bytes", "swap_peak_bytes",
    "cpu_seconds", "cpu_user_seconds", "cpu_system_seconds",
    "io_read_bytes", "io_write_bytes", "io_read_ops", "io_write_ops",
    "psi_memory_some_seconds", "psi_memory_full_seconds",
    "psi_cpu_some_seconds", "psi_io_some_seconds", "psi_io_full_seconds",
)
# Metrics that have no per-process source: process-tree mode cannot answer them,
# and a shared cgroup cannot attribute them.
_CGROUP_EXACT_ONLY = tuple(key for key in METRIC_KEYS if key not in (
    "rss_peak_bytes", "cpu_seconds", "cpu_user_seconds", "cpu_system_seconds",
    "io_read_bytes", "io_write_bytes",
))
_PSI_FILES = (
    ("psi_memory_some_seconds", "memory.pressure", "some_total"),
    ("psi_memory_full_seconds", "memory.pressure", "full_total"),
    ("psi_cpu_some_seconds", "cpu.pressure", "some_total"),
    ("psi_io_some_seconds", "io.pressure", "some_total"),
    ("psi_io_full_seconds", "io.pressure", "full_total"),
)

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
_CLK_TCK = os.sysconf("SC_CLK_TCK")


class WindowError(Exception):
    """A window verb refused; ``code`` is the wire error code."""

    def __init__(self, code: str, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason


# ── procfs table ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProcRow:
    ppid: int
    start_ticks: int
    state: str
    utime: int
    stime: int
    cutime: int
    cstime: int


def read_proc_row(proc_root: str, pid: int) -> Optional[ProcRow]:
    """Parse ``/proc/<pid>/stat``; None when the process is gone or malformed."""
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), encoding="ascii") as fh:
            text = fh.read()
    except (OSError, UnicodeError):
        return None
    parsed = proc_stat.split_after_comm(text)
    if parsed is None:
        return None
    fields = parsed[1]
    # fields[0] is stat field 3 (state); field n is fields[n - 3].
    if len(fields) < 20:
        return None
    try:
        return ProcRow(
            ppid=int(fields[1]), start_ticks=int(fields[19]), state=fields[0],
            utime=int(fields[11]), stime=int(fields[12]),
            cutime=int(fields[13]), cstime=int(fields[14]),
        )
    except ValueError:
        return None


class ProcTable:
    """One scan of every process, shared by every window due in a round."""

    def __init__(self, proc_root: str) -> None:
        self.proc_root = proc_root
        self.rows: Dict[int, ProcRow] = {}
        self.children: Dict[int, List[int]] = {}
        try:
            names = os.listdir(proc_root)
        except OSError:
            names = []
        for name in names:
            if not name.isdecimal():
                continue
            row = read_proc_row(proc_root, int(name))
            if row is None:
                continue
            pid = int(name)
            self.rows[pid] = row
            self.children.setdefault(row.ppid, []).append(pid)

    def descendants(self, root: int) -> List[int]:
        """``root`` and every live descendant, root first."""
        out: List[int] = []
        seen: Set[int] = set()
        stack = [root]
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            out.append(pid)
            stack.extend(self.children.get(pid, ()))
        return out

    def ancestors(self, pid: int) -> Set[int]:
        out: Set[int] = set()
        row = self.rows.get(pid)
        while row is not None and row.ppid > 0 and row.ppid not in out:
            out.add(row.ppid)
            row = self.rows.get(row.ppid)
        return out


def _read_rss_bytes(proc_root: str, pid: int) -> Optional[int]:
    try:
        with open(os.path.join(proc_root, str(pid), "statm"), encoding="ascii") as fh:
            fields = fh.read().split()
        return int(fields[1]) * _PAGE_SIZE
    except (OSError, UnicodeError, ValueError, IndexError):
        return None


def _read_proc_io(proc_root: str, pid: int) -> Optional[Tuple[int, int]]:
    try:
        with open(os.path.join(proc_root, str(pid), "io"), encoding="ascii") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeError):
        return None
    values: Dict[str, int] = {}
    for line in lines:
        key, _, value = line.partition(":")
        value = value.strip()
        if value.isdecimal():
            values[key] = int(value)
    if "read_bytes" not in values or "write_bytes" not in values:
        return None
    return values["read_bytes"], values["write_bytes"]


# ── cgroup counters ─────────────────────────────────────────────────────────

class PeakFile:
    """A ``memory.peak``-style file held open so its peak can be reset per fd.

    Linux 6.12 and later keep a local peak per open file description and reset
    it when anything is written to that description. Where the write is
    refused the file still reads, but its peak is the cgroup's lifetime peak
    and must not be reported as the window's: ``reset_ok`` is then False.
    ``write`` is the single seam for the reset so a test can stand in for a
    kernel that lacks it.
    """

    def __init__(self, path: str, write: Callable[[int, bytes], int] = os.write) -> None:
        self.fd: Optional[int] = None
        self.present = False
        self.reset_ok = False
        try:
            self.fd = os.open(path, os.O_RDWR)
        except OSError as exc:
            if exc.errno in (errno.EMFILE, errno.ENFILE):
                raise                       # out of descriptors: the open is refused
            # Present but not writable is a kernel without per-file reset.
            self.present = os.path.exists(path)
            return
        self.present = True
        try:
            write(self.fd, b"reset")
            self.reset_ok = True
        except OSError:
            self.reset_ok = False

    def read(self) -> Optional[int]:
        if self.fd is None:
            return None
        try:
            text = os.pread(self.fd, 64, 0).decode("ascii").strip()
        except (OSError, UnicodeError):
            return None
        return int(text) if text.isdecimal() else None

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def _read_usec_file(directory: str, name: str, key: str) -> Optional[float]:
    path = os.path.join(directory, name)
    if not os.path.exists(path):
        return None
    value = util.read_pressure(path).get(key)
    return None if value is None else float(value)


def _read_cpu(directory: str) -> Optional[Dict[str, int]]:
    path = os.path.join(directory, "cpu.stat")
    if not os.path.exists(path):
        return None
    kv = util.read_kv(path)
    if not all(key in kv for key in ("usage_usec", "user_usec", "system_usec")):
        return None
    return {key: kv[key] for key in ("usage_usec", "user_usec", "system_usec")}


def _read_io_stat(directory: str) -> Optional[Dict[str, int]]:
    path = os.path.join(directory, "io.stat")
    if not os.path.exists(path):
        return None
    totals = {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0}
    for fields in util.read_flat_keyed(path).values():
        for key in totals:
            totals[key] += fields.get(key, 0)
    return totals


def _read_psi(directory: str) -> Dict[str, Optional[float]]:
    return {
        metric: _read_usec_file(directory, name, key)
        for metric, name, key in _PSI_FILES
    }


def _read_host_psi(host_proc_root: str) -> Dict[str, Optional[float]]:
    return {
        metric: _read_usec_file(os.path.join(host_proc_root, "pressure"), name.split(".")[0], key)
        for metric, name, key in _PSI_FILES
    }


def _read_foreign_candidates(directory: str) -> Optional[Set[int]]:
    text = util.read_text(os.path.join(directory, "cgroup.procs"))
    if text is None:
        return None
    return {int(line) for line in text.split() if line.isdecimal() and int(line) > 0}


# ── one window ──────────────────────────────────────────────────────────────

@dataclass
class _Window:
    window_id: str
    mode: str
    interval: float
    peer_key: Tuple[int, int]          # (peer pid, peer start ticks)
    peer_pid: int
    root_pid: int
    root_start: int
    last_cpu: Tuple[int, int, int, int]     # the root's counters at its latest observation
    opened_mono: float
    started_at: str
    leaf_dir: str                      # the root's cgroup directory
    container_dir: str                 # the peer's cgroup directory (context)
    container_path: str
    next_due: float
    # sampled state
    samples: int = 0
    rss_max: Optional[int] = None
    tree_max: int = 0
    foreign_max: int = 0
    live_io: Dict[Tuple[int, int], Tuple[int, int]] = field(default_factory=dict)
    exited_read: int = 0
    exited_write: int = 0
    io_seen: bool = False
    # cgroup-mode state
    mem_peak: Optional[PeakFile] = None
    swap_peak: Optional[PeakFile] = None
    baseline_bytes: Optional[int] = None
    start_cpu: Optional[Dict[str, int]] = None
    start_io: Optional[Dict[str, int]] = None
    start_psi: Dict[str, Optional[float]] = field(default_factory=dict)
    # context baselines
    start_container_psi: Dict[str, Optional[float]] = field(default_factory=dict)
    start_container_events: Optional[Dict[str, int]] = None
    start_host_psi: Dict[str, Optional[float]] = field(default_factory=dict)
    start_ancestors: Dict[str, Any] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def release(self) -> None:
        for peak in (self.mem_peak, self.swap_peak):
            if peak is not None:
                peak.close()


def _delta_seconds(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None or end < start:
        return None
    return round((end - start) / 1e6, 6)


class WindowRegistry:
    """Open windows, their sampling thread, and the bounds on both."""

    def __init__(
        self,
        *,
        proc_root: str,
        host_proc_root: str,
        cgroup_root: str,
        mountinfo_path: str,
        gates_slice_name: str,
        mono: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        ttl_seconds: float = WINDOW_TTL_SECONDS,
        max_per_peer: int = MAX_WINDOWS_PER_PEER,
        max_total: int = MAX_WINDOWS_PER_DAEMON,
        autostart: bool = True,
        peak_write: Callable[[int, bytes], int] = os.write,
    ) -> None:
        self.proc_root = proc_root
        self.host_proc_root = host_proc_root
        self.cgroup_root = cgroup_root
        self.mountinfo_path = mountinfo_path
        self.gates_slice_name = gates_slice_name
        self._mono = mono
        self._wall = wall
        self.ttl_seconds = ttl_seconds
        self.max_per_peer = max_per_peer
        self.max_total = max_total
        self._autostart = autostart
        self._peak_write = peak_write
        # 32 bits of per-daemon epoch + 96 random bits = the 128-bit id.
        self._epoch = secrets.token_hex(4)
        self._lock = threading.Lock()
        self._windows: Dict[str, _Window] = {}
        self._gone: "OrderedDict[str, None]" = OrderedDict()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ── verbs ───────────────────────────────────────────────────────────

    def open(
        self, *, peer_pid: Optional[int], peer_uid: Optional[int],
        thread_id: int, root_pid: int, mode: str, interval_ms: int,
    ) -> Dict[str, Any]:
        if mode not in MODES:
            raise WindowError("bad-argument", "mode must be 'process-tree' or 'cgroup'")
        if not INTERVAL_MIN_MS <= interval_ms <= INTERVAL_MAX_MS:
            raise WindowError(
                "bad-argument",
                f"interval_ms must be between {INTERVAL_MIN_MS} and {INTERVAL_MAX_MS}",
            )
        peer = self._peer(peer_pid, peer_uid, thread_id)
        peer_key = (peer["pid"], peer["start_ticks"])
        table = ProcTable(self.proc_root)
        host_pid, root_row, leaf = self._bind_root(peer, table, root_pid)
        window_id = f"w-{self._epoch}{secrets.token_hex(12)}"
        now = self._mono()
        leaf_dir = os.path.join(self.cgroup_root, str(leaf).lstrip("/"))
        container_path = str(peer["leaf_path"])
        window = _Window(
            window_id=window_id, mode=mode, interval=interval_ms / 1000.0,
            peer_key=peer_key, peer_pid=peer["pid"], root_pid=host_pid,
            root_start=root_row.start_ticks, opened_mono=now,
            started_at=datetime.fromtimestamp(self._wall(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            leaf_dir=leaf_dir,
            container_dir=os.path.join(self.cgroup_root, container_path.lstrip("/")),
            container_path=container_path, next_due=now + interval_ms / 1000.0,
            last_cpu=(root_row.utime, root_row.stime, root_row.cutime, root_row.cstime),
        )
        peak_reset = False
        try:
            if mode == "cgroup":
                window.mem_peak = PeakFile(os.path.join(leaf_dir, "memory.peak"), self._peak_write)
                window.swap_peak = PeakFile(os.path.join(leaf_dir, "memory.swap.peak"), self._peak_write)
                peak_reset = window.mem_peak.reset_ok
                window.baseline_bytes = util.read_int(os.path.join(leaf_dir, "memory.current"))
                window.start_cpu = _read_cpu(leaf_dir)
                window.start_io = _read_io_stat(leaf_dir)
                window.start_psi = _read_psi(leaf_dir)
            self._read_context_start(window)
            self._tick(window, table)
        except OSError as exc:
            window.release()
            raise WindowError(
                "window-refused", f"cannot open the window's files: {exc}", reason="limit",
            ) from exc
        with self._lock:
            try:
                self._check_capacity(peer_key)
            except WindowError:
                window.release()
                raise
            self._windows[window_id] = window
            self._ensure_thread()
        return {
            "window": window_id, "mode": mode, "started_at": window.started_at,
            "kernel": {"peak_reset": peak_reset},
        }

    def close(
        self, *, peer_pid: Optional[int], peer_uid: Optional[int],
        thread_id: int, window_id: str,
    ) -> Dict[str, Any]:
        peer = self._peer(peer_pid, peer_uid, thread_id)
        peer_key = (peer["pid"], peer["start_ticks"])
        with self._lock:
            window = self._windows.get(window_id)
            if window is None:
                self._raise_missing(window_id)
            assert window is not None
            if window.peer_key != peer_key:
                raise WindowError("peer-refused", "the window belongs to another peer")
            del self._windows[window_id]
        with window.lock:
            try:
                return self._result(window)
            finally:
                window.release()

    def shutdown(self) -> None:
        """Stop sampling and release every window (daemon shutdown)."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
        with self._lock:
            windows = list(self._windows.values())
            self._windows.clear()
            self._thread = None
        for window in windows:
            window.release()

    def open_count(self) -> int:
        with self._lock:
            return len(self._windows)

    # ── binding ─────────────────────────────────────────────────────────

    def _peer(self, peer_pid: Optional[int], peer_uid: Optional[int], thread_id: int) -> Dict[str, Any]:
        if peer_pid is None or peer_uid is None:
            raise WindowError("peer-refused", "SO_PEERCRED is required for resource observation")
        try:
            return resource_observer.resolve_window_peer(
                peer_pid=peer_pid, peer_uid=peer_uid, thread_id=thread_id,
                proc_root=self.proc_root, cgroup_root=self.cgroup_root,
                mountinfo_path=self.mountinfo_path, gates_slice_name=self.gates_slice_name,
            )
        except resource_observer.ResourceObserverError as exc:
            raise WindowError("observer-refused", str(exc)) from exc

    def _bind_root(
        self, peer: Dict[str, Any], table: ProcTable, root_pid: int
    ) -> Tuple[int, ProcRow, PurePosixPath]:
        """Map ``root_pid`` (peer namespace) to a live host process of the peer's tree.

        The root must be the peer or a descendant of it (parent chain), live,
        and in the peer's cgroup or below it; its start time is rechecked after
        every read that could race with PID reuse.
        """
        peer_pid = peer["pid"]
        peer_row = table.rows.get(peer_pid)
        if peer_row is None or peer_row.start_ticks != peer["start_ticks"]:
            raise WindowError("observer-refused", "peer process identity changed")
        depth = len(peer["nspid"])
        matches: List[int] = []
        for host_pid in table.descendants(peer_pid):
            nspid = resource_observer.process_nspid(self.proc_root, host_pid)
            # A descendant lives in the peer's PID namespace or one nested
            # below it, so its PID in the peer's namespace is the entry at the
            # peer's own depth.
            if nspid is not None and len(nspid) >= depth and nspid[depth - 1] == root_pid:
                matches.append(host_pid)
        if len(matches) != 1:
            raise WindowError(
                "observer-refused",
                "root_pid is not the peer or a live descendant of it",
            )
        host_pid = matches[0]
        row = table.rows[host_pid]
        try:
            leaf = resource_observer.process_cgroup_path(
                self.proc_root, host_pid, peer["parent_levels"]
            )
        except resource_observer.ResourceObserverError as exc:
            raise WindowError("observer-refused", str(exc)) from exc
        if not leaf.is_relative_to(peer["leaf_path"]):
            raise WindowError(
                "observer-refused", "root_pid is outside the peer's cgroup",
            )
        again = read_proc_row(self.proc_root, host_pid)
        if again is None or again.start_ticks != row.start_ticks:
            raise WindowError("observer-refused", "root_pid identity changed during binding")
        return host_pid, row, leaf

    def _check_capacity(self, peer_key: Tuple[int, int]) -> None:
        if len(self._windows) >= self.max_total:
            raise WindowError("window-refused", "daemon window limit reached", reason="limit")
        mine = sum(1 for w in self._windows.values() if w.peer_key == peer_key)
        if mine >= self.max_per_peer:
            raise WindowError("window-refused", "per-peer window limit reached", reason="limit")

    def _raise_missing(self, window_id: str) -> None:
        if (
            len(window_id) == 34 and window_id.startswith("w-")
            and all(c in "0123456789abcdef" for c in window_id[2:])
        ):
            if window_id[2:10] != self._epoch or window_id in self._gone:
                raise WindowError(
                    "window-lost", "the window is gone (daemon restart or expiry)",
                )
        raise WindowError("bad-argument", "unknown window")

    # ── sampling ────────────────────────────────────────────────────────

    def _ensure_thread(self) -> None:
        if not self._autostart:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="cgprofile-observer-windows",
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample_due()
            with self._lock:
                if not self._windows:
                    self._thread = None
                    return
                soonest = min(w.next_due for w in self._windows.values())
            self._stop.wait(max(0.01, min(0.5, soonest - self._mono())))

    def sample_due(self, *, force: bool = False) -> None:
        """One sampling round: expire dead windows, tick every due window.

        ``force`` ticks every window regardless of its schedule (tests drive a
        registry built with ``autostart=False`` this way).
        """
        now = self._mono()
        with self._lock:
            windows = list(self._windows.values())
        due = [w for w in windows if force or w.next_due <= now]
        if not due:
            return
        table = ProcTable(self.proc_root)
        for window in due:
            peer_row = table.rows.get(window.peer_pid)
            expired = now - window.opened_mono >= self.ttl_seconds
            gone = peer_row is None or peer_row.start_ticks != window.peer_key[1]
            if expired or gone:
                self._drop(window)
                continue
            with window.lock:
                self._tick(window, table)
                window.next_due = now + window.interval

    def _drop(self, window: _Window) -> None:
        with self._lock:
            if self._windows.pop(window.window_id, None) is None:
                return
            self._gone[window.window_id] = None
            while len(self._gone) > _TOMBSTONES:
                self._gone.popitem(last=False)
        with window.lock:
            window.release()

    def _tick(self, window: _Window, table: ProcTable) -> None:
        root_row = table.rows.get(window.root_pid)
        alive = root_row is not None and root_row.start_ticks == window.root_start
        tree = table.descendants(window.root_pid) if alive else []
        rss = 0
        read_any = False
        current: Dict[Tuple[int, int], Tuple[int, int]] = {}
        for pid in tree:
            size = _read_rss_bytes(self.proc_root, pid)
            if size is not None:
                rss += size
                read_any = True
            io = _read_proc_io(self.proc_root, pid)
            if io is not None:
                current[(pid, table.rows[pid].start_ticks)] = io
        if read_any:
            window.rss_max = rss if window.rss_max is None else max(window.rss_max, rss)
        for key, values in window.live_io.items():
            if key not in current:
                window.exited_read += values[0]
                window.exited_write += values[1]
        window.live_io = current
        if current or window.exited_read or window.exited_write:
            window.io_seen = True
        window.tree_max = max(window.tree_max, len(tree))
        if alive and root_row is not None:
            window.last_cpu = (root_row.utime, root_row.stime, root_row.cutime, root_row.cstime)
        if window.mode == "cgroup":
            members = _read_foreign_candidates(window.leaf_dir)
            if members is not None:
                allowed = set(tree) | {window.peer_pid} | table.ancestors(window.peer_pid)
                unknown = members - allowed
                if unknown:
                    # A child forked after the shared table was scanned is
                    # not in `tree` yet; it is the tree's own when its live
                    # parent chain reaches a tree member.
                    unknown = {
                        pid for pid in unknown
                        if not self._descends_from(pid, set(tree))
                    }
                window.foreign_max = max(window.foreign_max, len(unknown))
        window.samples += 1

    def _descends_from(self, pid: int, tree: Set[int]) -> bool:
        """True when ``pid``'s live parent chain reaches a member of ``tree``.

        Re-reads ``/proc`` (bounded depth); a pid that has already exited, or
        whose chain cannot be read, is NOT proven to descend, so it stays
        foreign (the fail-safe direction: null metrics, never a wrong number).
        """
        current = pid
        for _ in range(_MAX_CHAIN_DEPTH):
            row = read_proc_row(self.proc_root, current)
            if row is None:
                return False
            if row.ppid in tree:
                return True
            current = row.ppid
        return False

    def _read_context_start(self, window: _Window) -> None:
        window.start_container_psi = _read_psi(window.container_dir)
        window.start_container_events = ancestor_events.read_local_events(window.container_dir)
        window.start_host_psi = _read_host_psi(self.host_proc_root)
        window.start_ancestors = ancestor_events.read_ancestors(
            self.cgroup_root, ancestor_events.ancestor_paths(window.container_path),
        )

    # ── result ──────────────────────────────────────────────────────────

    def _result(self, window: _Window) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {key: None for key in METRIC_KEYS}
        missing: Dict[str, str] = {}
        sources: Dict[str, str] = {}

        def put(key: str, value: Any, source: str) -> None:
            metrics[key] = value
            sources[key] = source

        if window.rss_max is not None:
            put("rss_peak_bytes", window.rss_max, "sampled-sum-rss")
        else:
            missing["rss_peak_bytes"] = "OBSERVER_ERROR"

        if window.mode == "process-tree":
            self._process_tree_metrics(window, put, missing)
            for key in _CGROUP_EXACT_ONLY:
                missing[key] = "REQUIRES_CGROUP_MODE"
        else:
            self._cgroup_metrics(window, put, missing)
        foreign = window.foreign_max > 0 and window.mode == "cgroup"
        if foreign:
            for key in (*_CGROUP_EXACT_ONLY, "cpu_seconds", "cpu_user_seconds",
                        "cpu_system_seconds", "io_read_bytes", "io_write_bytes"):
                metrics[key] = None
                sources.pop(key, None)
                missing[key] = "SHARED_CGROUP"
        return {
            "window": window.window_id,
            "metrics": metrics,
            "missing": {key: missing[key] for key in METRIC_KEYS if key in missing},
            "sources": sources,
            "context": self._context(window),
            "attribution": {
                "mode": window.mode,
                "foreign_pids_max": window.foreign_max if window.mode == "cgroup" else 0,
                "tree_pids_max": window.tree_max,
                "samples": window.samples,
                "baseline_bytes": window.baseline_bytes if window.mode == "cgroup" else None,
            },
        }

    def _process_tree_metrics(
        self, window: _Window, put: Callable[[str, Any, str], None],
        missing: Dict[str, str],
    ) -> None:
        if window.io_seen:
            read = window.exited_read + sum(v[0] for v in window.live_io.values())
            wrote = window.exited_write + sum(v[1] for v in window.live_io.values())
            put("io_read_bytes", read, "proc-io-lower-bound")
            put("io_write_bytes", wrote, "proc-io-lower-bound")
        else:
            missing["io_read_bytes"] = missing["io_write_bytes"] = "OBSERVER_ERROR"
        row = read_proc_row(self.proc_root, window.root_pid)
        cpu = window.last_cpu
        label = "proc-stat-sampled"
        if row is not None and row.start_ticks == window.root_start:
            cpu = (row.utime, row.stime, row.cutime, row.cstime)
            label = "proc-stat-exact" if row.state == "Z" else "proc-stat-live"
        put("cpu_seconds", round(sum(cpu) / _CLK_TCK, 6), label)
        put("cpu_user_seconds", round((cpu[0] + cpu[2]) / _CLK_TCK, 6), label)
        put("cpu_system_seconds", round((cpu[1] + cpu[3]) / _CLK_TCK, 6), label)

    def _cgroup_metrics(
        self, window: _Window, put: Callable[[str, Any, str], None],
        missing: Dict[str, str],
    ) -> None:
        directory = window.leaf_dir
        for key, peak, source in (
            ("memory_peak_bytes", window.mem_peak, "memory.peak"),
            ("swap_peak_bytes", window.swap_peak, "memory.swap.peak"),
        ):
            if peak is None or not peak.present:
                missing[key] = "CONTROLLER_DISABLED"
            elif not peak.reset_ok:
                missing[key] = "PEAK_RESET_UNSUPPORTED"
            else:
                value = peak.read()
                if value is None:
                    missing[key] = "OBSERVER_ERROR"
                else:
                    put(key, value, source)
        end_cpu = _read_cpu(directory)
        if window.start_cpu is None:
            for key in ("cpu_seconds", "cpu_user_seconds", "cpu_system_seconds"):
                missing[key] = "CONTROLLER_DISABLED"
        elif end_cpu is None:
            for key in ("cpu_seconds", "cpu_user_seconds", "cpu_system_seconds"):
                missing[key] = "OBSERVER_ERROR"
        else:
            for key, field_name in (
                ("cpu_seconds", "usage_usec"), ("cpu_user_seconds", "user_usec"),
                ("cpu_system_seconds", "system_usec"),
            ):
                value = _delta_seconds(window.start_cpu[field_name], end_cpu[field_name])
                if value is None:
                    missing[key] = "OBSERVER_ERROR"
                else:
                    put(key, value, "cpu.stat")
        end_io = _read_io_stat(directory)
        io_keys = (
            ("io_read_bytes", "rbytes"), ("io_write_bytes", "wbytes"),
            ("io_read_ops", "rios"), ("io_write_ops", "wios"),
        )
        for key, field_name in io_keys:
            if window.start_io is None:
                missing[key] = "CONTROLLER_DISABLED"
            elif end_io is None or end_io[field_name] < window.start_io[field_name]:
                missing[key] = "OBSERVER_ERROR"
            else:
                put(key, end_io[field_name] - window.start_io[field_name], "io.stat")
        end_psi = _read_psi(directory)
        for key, _name, _field in _PSI_FILES:
            start, end = window.start_psi.get(key), end_psi.get(key)
            if start is None:
                missing[key] = "CONTROLLER_DISABLED"
            else:
                value = _delta_seconds(start, end)
                if value is None:
                    missing[key] = "OBSERVER_ERROR"
                else:
                    put(key, value, "psi")

    def _context(self, window: _Window) -> Dict[str, Any]:
        end_psi = _read_psi(window.container_dir)
        end_host = _read_host_psi(self.host_proc_root)
        context: Dict[str, Any] = {}
        for key, _name, _field in _PSI_FILES:
            suffix = key[len("psi_"):]
            context[f"container_psi_{suffix}"] = _delta_seconds(
                window.start_container_psi.get(key), end_psi.get(key)
            )
        for key, _name, _field in _PSI_FILES:
            suffix = key[len("psi_"):]
            context[f"host_psi_{suffix}"] = _delta_seconds(
                window.start_host_psi.get(key), end_host.get(key)
            )
        end_events = ancestor_events.read_local_events(window.container_dir)
        start_events = window.start_container_events
        context["container_memory_events_local"] = {
            key: (None if start_events is None or end_events is None
                  else max(0, end_events[key] - start_events[key]))
            for key in ancestor_events.EVENT_KEYS
        }
        context["ancestors_memory_events_local"] = ancestor_events.deltas(
            window.start_ancestors,
            ancestor_events.read_ancestors(self.cgroup_root, list(window.start_ancestors)),
        )
        return context
