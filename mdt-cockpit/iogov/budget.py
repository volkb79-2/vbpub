"""IO budget: token buckets (rate ceilings), the per-day byte budget, and the worst-case numbers printed by `plan`."""

from __future__ import annotations

import datetime as dt
import threading

from .config import MiB, Config


class BudgetExceeded(Exception):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class TokenBucket:
    """Rate ceiling: `acquire(n)` blocks (via the injected sleep) until n units may be spent. Thread-safe."""

    def __init__(self, rate: float, mono, sleep, burst: float | None = None):
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.rate = float(rate)
        self.capacity = float(burst if burst is not None else rate)
        self.tokens = self.capacity
        self.mono = mono
        self.sleep = sleep
        self.last = mono()
        self._lock = threading.Lock()

    def acquire(self, n: float) -> float:
        """Reserve n units now and sleep (outside the lock) for any debt; returns the seconds slept."""

        with self._lock:
            now = self.mono()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            self.tokens -= n
            wait = -self.tokens / self.rate if self.tokens < 0 else 0.0
        if wait > 0:
            self.sleep(wait)
        return wait


class DailyBudget:
    """Counters per UTC day inside the state dict. `charge` is called BEFORE the IO is submitted."""

    def __init__(self, state: dict, cfg: Config, wall):
        self.state = state
        self.cfg = cfg
        self.wall = wall

    def _today(self) -> dict:
        day = dt.datetime.fromtimestamp(self.wall(), dt.timezone.utc).strftime("%Y-%m-%d")
        b = self.state.setdefault("budget", {})
        if b.get("day") != day:
            b.clear()
            b.update({"day": day, "read_bytes": 0, "write_bytes": 0, "read_ops": 0, "write_ops": 0, "sync_ops": 0})
        return b

    def charge(self, kind: str, nbytes: int, ops: int = 1) -> None:
        b = self._today()
        if kind == "read":
            if b["read_bytes"] + nbytes > self.cfg.max_read_bytes_per_day:
                raise BudgetExceeded("daily read byte budget exhausted")
            b["read_bytes"] += nbytes
            b["read_ops"] += ops
        elif kind == "write":
            if b["write_bytes"] + nbytes > self.cfg.max_write_bytes_per_day:
                raise BudgetExceeded("daily write byte budget exhausted")
            b["write_bytes"] += nbytes
            b["write_ops"] += ops
        elif kind == "sync":
            b["sync_ops"] += ops
        else:
            raise ValueError(kind)

    def remaining(self, kind: str) -> int:
        b = self._today()
        if kind == "read":
            return self.cfg.max_read_bytes_per_day - b["read_bytes"]
        return self.cfg.max_write_bytes_per_day - b["write_bytes"]

    def snapshot(self) -> dict:
        return dict(self._today())


def added_wait_ms(inflight_bytes: int, device_bps: float) -> float:
    """Worst-case extra wait a neighbour request sees behind our in-flight bytes at a given device rate."""

    return inflight_bytes / device_bps * 1000.0


def compute_budget(cfg: Config, probe_bytes: int | None = None, region_mode: str = "file") -> dict:
    """Worst-case/typical IO per day and the ceilings, straight from the config (what `plan` prints and the docs quote)."""

    ticks = 86400.0 / cfg.tick_interval_s
    benches = 24.0 / cfg.bench_interval_h
    probe = cfg.probe_bytes_cap if probe_bytes is None else probe_bytes
    escalations_per_day = 24.0 / cfg.escalation_every_h
    sync4k = cfg.sync4k_samples * 4096
    write_day = 0.0
    sync_ops_day = 0.0
    read_day = ticks * (cfg.read_samples_per_tick + cfg.witness_reads) * cfg.read_block_bytes + benches * cfg.seq_read_bytes
    read_day_worst = ticks * (cfg.read_max_attempts + cfg.witness_reads) * cfg.read_block_bytes + benches * cfg.seq_read_bytes
    read_ops_day = ticks * (cfg.read_samples_per_tick + cfg.witness_reads)
    read_ops_day_worst = ticks * (cfg.read_max_attempts + cfg.witness_reads)
    if region_mode != "none":
        extra = max(0, cfg.escalation_bytes_cap - probe)
        write_day = benches * (probe + sync4k) + escalations_per_day * extra
        syncs_per_run = cfg.sync4k_samples + 2 * (max(1, probe // cfg.sync_every_bytes) + 1)
        sync_ops_day = benches * syncs_per_run
    inflight = cfg.qd_high * cfg.block_bytes
    return {
        "ticks_per_day": ticks,
        "benches_per_day": benches,
        "read_bytes_per_day_typical": int(read_day),
        "read_bytes_per_day_worst": int(read_day_worst),
        "read_ops_per_day_typical": int(read_ops_day),
        "read_ops_per_day_worst": int(read_ops_day_worst),
        "write_bytes_per_day": int(write_day),
        "sync_ops_per_day": int(sync_ops_day),
        "read_bytes_per_day_cap": cfg.max_read_bytes_per_day,
        "write_bytes_per_day_cap": cfg.max_write_bytes_per_day,
        "ceilings": {
            "read_iops_max": cfg.read_iops_max,
            "sync4k_iops_max": cfg.sync4k_iops_max,
            "write_bps_max": cfg.write_bps_max,
            "read_bps_max": cfg.read_bps_max,
            "max_requests_in_flight": cfg.qd_high,
            "max_bytes_in_flight": inflight,
            "max_unsynced_bytes": cfg.sync_every_bytes,
        },
        "added_wait_ms_at_device_rate": {
            f"{rate // MiB} MiB/s": round(added_wait_ms(inflight, rate), 2) for rate in (6 * MiB, 15 * MiB, 340 * MiB)
        },
        "flush_barrier_ms_at_device_rate": {
            f"{rate // MiB} MiB/s": round(added_wait_ms(cfg.sync_every_bytes, rate), 1) for rate in (6 * MiB, 15 * MiB, 340 * MiB)
        },
    }
