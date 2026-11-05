"""A fake topos-sampler: the wire protocol of docs/SAMPLER-PROTOCOL.md over a Unix socket.

Test support only. No timing assumptions beyond bounded waits; a test that wants a slow
or dead server sets ``delay_s`` / ``drop`` and the client's own timeout does the rest.
"""
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import Any

T0_MS = 1_790_000_000_000
DOCKER_ID = "ab" * 32


def v(x: float) -> dict:
    return {"s": "value", "v": x}


def c(rate: float, raw: int = 1000) -> dict:
    """A counter: the delta over the interval, its per-second rate, the kernel reading."""
    return {"s": "value", "v": rate, "rate": rate, "raw": raw}


def na(why: str = "feature_absent") -> dict:
    return {"s": "not_applicable", "why": why}


def un(why: str = "read_error") -> dict:
    return {"s": "unavailable", "why": why}


def cgroup(**over: dict) -> dict:
    base = {
        "mem.current": v(1e9), "mem.anon": v(6e8), "mem.file": v(3e8), "mem.shmem": v(1e7),
        "mem.zswap": v(1e8), "mem.zswapped": v(3e8),
        "cpu.usage_usec": c(500_000), "cpu.throttled_usec": c(0),
        "limit.memory.min": v(0), "limit.memory.low": v(0), "limit.memory.high": v(-1), "limit.memory.max": v(-1),
        "limit.memory.swap.max": v(-1), "limit.cpu.weight": v(100), "limit.io.weight": v(100),
        "limit.cpu.max.quota": v(-1), "limit.cpu.max.period": v(100000), "limit.io.max.capped": v(0),
        "psi.cpu.some.avg10": v(1.0), "psi.cpu.full.avg10": v(0.0),
        "psi.memory.some.avg10": v(2.0), "psi.memory.full.avg10": v(0.5),
        "psi.io.some.avg10": v(3.0), "psi.io.full.avg10": v(1.0),
        "io.rbytes": c(2_000_000), "io.wbytes": c(1_000_000), "io.rios": c(50), "io.wios": c(20),
        "io.cost.usage": na("feature_absent"),
        "mem.pgmajfault": c(0), "mem.pgscan": c(0), "mem.pgsteal": c(0),
        "mem.workingset_refault_file": c(0), "mem.workingset_refault_anon": c(30), "mem.zswpin": c(10),
        "memev.high": c(0), "memev.max": c(0), "memev.oom_kill": c(0),
        "pids.current": v(10), "procs.d_state": v(0),
        "mem.swap_current": v(5e8), "mem.swapcached": v(1e7), "limit.pids.max": v(4096),
    }
    base.update(over)
    return base


def build_current(tick: int = 1, *, epoch: str = "e1", truncated: bool = False, **over: Any) -> dict:
    entities = {
        "cg:/": cgroup(**{
            "mem.current": v(6e9), "psi.cpu.some.avg10": v(4.0), "io.cost.vrate": na("feature_absent"),
            "procs.d_state": v(1), "mem.swap_current": v(2e9),
            "limit.pids.max": na("controller_or_file_absent"), "mem.swapcached": na(), "mem.zswapped": na(),
        }),
        "cg:/system.slice": cgroup(**{
            "cpu.usage_usec": c(2_000_000), "limit.memory.high": v(2e9), "limit.memory.swap.max": v(0),
            "procs.d_state": v(2), "mem.pgmajfault": c(120), "io.cost.usage": c(150_000),
        }),
        "cg:/system.slice/db.service": cgroup(**{
            "cpu.usage_usec": c(1_500_000), "procs.d_state": v(2), "io.rbytes": un("first_sample"),
            "mem.pgmajfault": c(0),
        }),
        f"cg:/docker.slice/docker-{DOCKER_ID}.scope": cgroup(**{"cpu.usage_usec": c(10_000), "procs.d_state": v(0)}),
        "cg:/docker.slice": cgroup(),
        "host": {
            "meminfo.MemTotal": v(16e9), "meminfo.MemAvailable": v(8e9), "meminfo.SwapTotal": v(8e9),
            "meminfo.SwapFree": v(6e9), "meminfo.SwapCached": v(1e8),
            "memstack.anon": v(4e9), "memstack.file_cache": v(3e9), "memstack.shmem": v(2e8), "memstack.buffers": v(1e8),
            "memstack.slab_reclaimable": v(5e8), "memstack.slab_unreclaimable": v(2e8), "memstack.kernel_stack": v(3e7),
            "memstack.pagetables": v(1e8), "memstack.percpu": v(1e7), "memstack.zswap_pool": v(5e8),
            "memstack.free": v(7e9), "memstack.other": v(3e8),
            "host.load1": v(2.5), "host.load5": v(2.0), "host.load15": v(1.5),
            "host.procs_running": v(4), "host.procs_blocked": v(3),
            "zswap.pool_bytes": v(5e8), "zswap.stored_bytes": v(1.5e9), "zswap.enabled": v(1), "zswap.max_pool_percent": v(20),
            "zswap.written_back_pages": un("debugfs_unreadable"),
            "zswap.reject_alloc_fail": un("debugfs_unreadable"),
            "vm.zswpin": c(12), "vm.zswpout": c(7), "vm.zswpwb": c(3), "vm.pswpin": c(0), "vm.pswpout": c(25),
            "ksm.run": na("feature_absent"),
        },
        "dev:vda": {
            "disk.rd_sectors": c(2000), "disk.wr_sectors": c(1000), "disk.reads": c(40), "disk.writes": c(30),
            "disk.await_ms": v(4.5), "disk.util_pct": v(37.0), "disk.inflight": v(2),
        },
        "dev:vda1": {"disk.await_ms": v(9.0), "disk.util_pct": v(99.0)},
        "dev:vdb": {
            "disk.rd_sectors": c(0), "disk.wr_sectors": c(0), "disk.reads": c(0), "disk.writes": c(0),
            "disk.await_ms": na("no_activity_in_interval"), "disk.util_pct": v(0.0),
        },
        "sampler": {
            "sampler.work_us": v(3500), "sampler.rss_bytes": v(2_500_000), "sampler.cpu_ticks": c(1, 77),
            "sampler.overruns": c(0, 2), "sampler.skipped_ticks": c(0, 1),
        },
    }
    for n in range(4):
        entities[f"cpu:{n}"] = {
            "cpupct.us": v(50.0 + n), "cpupct.sy": v(10.0), "cpupct.io": v(5.0), "cpupct.st": v(0.0),
        }
    entities["cpu:2"]["cpupct.us"] = un("first_sample")
    procs = [
        {"pid": 100, "start": 1, "comm": "dbwriter", "state": "D", "why": "D", "cgroup": "cg:/system.slice/db.service",
         "cmdline": "dbwriter --x", "rss_bytes": 5_000_000, "threads": 4, "cpu_pct": v(1.0), "majflt_per_s": v(5),
         "io_read_bps": v(4e6), "io_write_bps": v(1e6), "run_delay_ms_per_s": v(0), "blkio_delay_ms_per_s": na("task_delayacct_off")},
        {"pid": 101, "start": 2, "comm": "burner", "state": "R", "why": "cpu", "cgroup": "cg:/system.slice/db.service",
         "cmdline": "burner", "rss_bytes": 1_000_000, "threads": 1, "cpu_pct": v(95.0), "majflt_per_s": v(0),
         "io_read_bps": v(0), "io_write_bps": v(0), "run_delay_ms_per_s": v(10), "blkio_delay_ms_per_s": na("task_delayacct_off")},
        {"pid": 102, "start": 3, "comm": "sleeper", "state": "S", "why": "cpu", "cgroup": "cg:/system.slice/db.service",
         "cmdline": "sleeper", "rss_bytes": 1_000, "threads": 1, "cpu_pct": v(0.1), "majflt_per_s": v(0),
         "io_read_bps": v(0), "io_write_bps": v(0), "run_delay_ms_per_s": v(0), "blkio_delay_ms_per_s": na("task_delayacct_off")},
        {"pid": 103, "start": 4, "comm": "firstseen", "state": "R", "why": "cpu", "cgroup": "cg:/system.slice",
         "cmdline": "", "rss_bytes": 1_000, "threads": 1, "cpu_pct": un("first_sample"), "majflt_per_s": un("first_sample"),
         "io_read_bps": un("not_read_this_tick"), "io_write_bps": un("not_read_this_tick"),
         "run_delay_ms_per_s": un("not_read_this_tick"), "blkio_delay_ms_per_s": na("task_delayacct_off")},
    ]
    cur = {
        "ok": True, "req": "current", "protocol": 1, "epoch": epoch, "tick": tick, "ts_ms": T0_MS + tick * 1000, "dt_ms": 1000,
        "work_us": 3500, "cgroups": 5, "counter_resets": 0,
        "pids": {"total": 120, "d_state": 3, "io_denied": 0, "io_read": 30, "full_sweep": False, "d_unattributed": 0, "scanned": True},
        "entities": entities, "procs": procs, "truncated": truncated,
    }
    cur.update(over)
    return cur


def build_hello(*, mode: str = "host", epoch: str = "e1", tick_ms: int = 1000) -> dict:
    return {
        "ok": True, "req": "hello", "protocol": 1, "epoch": epoch, "schema": 1, "version": "fake", "pid": 1,
        "started_ms": T0_MS, "tick_ms": tick_ms, "clk_tck": 100, "clk_tck_source": "sysconf", "page_size": 4096,
        "page_size_source": "sysconf", "limits": {"max_request_bytes": 4096, "max_response_bytes": 4_194_304, "max_clients": 4},
        "cgroup_view": {"mode": mode, "because": "self_at_cgroup_root" if mode == "namespaced" else "self_in_subtree",
                        "self_path": "/" if mode == "namespaced" else "/system.slice/x", "cgroup_root": "/sys/fs/cgroup"},
        "requests": ["hello", "current", "series", "events", "status"], "state_codes": {}, "tiers": [],
    }


def build_status() -> dict:
    return {
        "ok": True, "req": "status", "protocol": 1, "epoch": "e1", "uptime_s": 10, "ticks": 10, "tick_ms": 1000,
        "self": {"cpu_pct_recent_60s": v(1.25), "cpu_pct_since_start": v(1.0), "rss_bytes": v(2_500_000)},
        "timing": {"last_tick_us": 3500, "tick_budget_us": 1_000_000, "tick_overruns": 2, "skipped_ticks": 1, "last_stage_us": {}},
        "throttle": {"nr_throttled": v(4), "throttled_usec": v(900), "memory_high_events": v(0), "memory_max_events": v(0)},
        "tiers": [], "disk": {}, "tmpfs": {}, "dict": {}, "counter_resets": 0, "truncated": False,
    }


def shape_current(cur: dict, fields: list[str] | None, compact: bool) -> dict:
    """What the real sampler does with ``fields`` and ``compact`` (docs/SAMPLER-PROTOCOL.md, ``current`` shaping)."""

    def wanted(metric: str) -> bool:
        return fields is None or any(metric.startswith(f[:-1]) if f.endswith("*") else metric == f for f in fields)

    def shrink(o: Any) -> Any:
        if not compact or not isinstance(o, dict):
            return o
        if o.get("s") == "value":
            return [o["v"], o["rate"]] if "rate" in o else o["v"]
        return {k: o[k] for k in ("s", "why") if k in o}

    ents: dict[str, dict] = {}
    for name, metrics in cur["entities"].items():
        kept = {
            m: shrink(o)
            for m, o in metrics.items()
            if wanted(m) and not (compact and isinstance(o, dict) and o.get("s") == "not_applicable")
        }
        if kept:
            ents[name] = kept
    return {**cur, "entities": ents}


class FakeSampler:
    """Serves the protocol until :meth:`stop`. Everything it answers is a mutable attribute."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.epoch = "e1"
        self.tick = 1
        self.mode = "host"
        self.truncated = False
        self.current_over: dict[str, Any] = {}
        self.events: list[dict] = []
        self.series_points: list[list] = [[T0_MS + i * 1000, float(i)] for i in range(1, 6)]
        self.delay_s = 0.0
        self.drop = False  # accept and close without answering
        self.advance = False  # tick += 1 on every `current`
        self.raw_reply: bytes | None = None  # answer every request with these bytes
        self.requests: list[dict] = []
        self.remote_error: dict[str, str] = {}  # req -> error code
        self._stop = threading.Event()
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(path))
        self._srv.listen(8)
        self._srv.settimeout(0.05)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._conns: list[socket.socket] = []

    def start(self) -> "FakeSampler":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        for conn in self._conns:
            try:
                conn.close()
            except OSError:
                pass
        self._srv.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            self._conns.append(conn)
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        if self.drop:
            conn.close()
            return
        conn.settimeout(0.05)
        buf = b""
        while not self._stop.is_set():
            try:
                data = conn.recv(65536)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    req = json.loads(line)
                except ValueError:
                    continue
                self.requests.append(req)
                if self.delay_s and self._stop.wait(self.delay_s):
                    return
                out = self.raw_reply if self.raw_reply is not None else (json.dumps(self._answer(req)) + "\n").encode()
                try:
                    conn.sendall(out)
                except OSError:
                    return

    def _answer(self, req: dict) -> dict:
        name = req.get("req")
        base = {"protocol": 1, "epoch": self.epoch, "id": req.get("id")}
        if name in self.remote_error:
            return {"ok": False, **base, "error": {"code": self.remote_error[name], "message": "scripted"}}
        if name == "hello":
            return {**build_hello(mode=self.mode), **base}
        if name == "current":
            if self.advance:
                self.tick += 1
            cur = build_current(self.tick, epoch=self.epoch, truncated=self.truncated, **self.current_over)
            if req.get("fields") is not None or req.get("compact"):
                cur = shape_current(cur, req.get("fields"), bool(req.get("compact")))
            return {**cur, **base}
        if name == "status":
            return {**build_status(), **base}
        if name == "events":
            return {"ok": True, "req": "events", **base, "events": self.events, "truncated": False}
        if name == "series":
            return {"ok": True, "req": "series", **base, "entity": req.get("entity"), "metric": req.get("metric"),
                    "tier": "hot", "step_s": 1, "kind": "gauge", "fields": ["ts_ms", "v"],
                    "points": self.series_points, "window_s": req.get("window_s"), "truncated": False}
        return {"ok": False, **base, "error": {"code": "unknown_req", "message": str(name)}}
