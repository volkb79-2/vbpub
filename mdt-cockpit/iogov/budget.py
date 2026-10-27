"""IO budget: token buckets (rate ceilings), the per-day byte budget, and the worst-case numbers printed by `plan`."""

from __future__ import annotations

import datetime as dt
import threading

from .config import MiB, Config
from .state import valid_budget


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


READ_BLOCK = 4096


def today_str(wall) -> str:
    return dt.datetime.fromtimestamp(wall(), dt.timezone.utc).strftime("%Y-%m-%d")


def spent_budget(cfg: Config, wall) -> dict:
    """A budget slot for today with both byte caps already consumed: what a DAMAGED or unreadable slot is replaced with. Failing closed
    costs at most one day of probes; failing open would hand a damaged file the whole cap again."""

    return {"day": today_str(wall), "read_bytes": cfg.max_read_bytes_per_day, "write_bytes": cfg.max_write_bytes_per_day,
            "read_ops": 0, "write_ops": 0, "sync_ops": 0}


class DailyBudget:
    """Counters per UTC day inside the state dict.

    Crash-safe by RESERVATION: a run first reserves its worst-case bytes (counted as spent and persisted BEFORE any IO), per-IO
    `charge` calls draw from the reservation (and are checked against the daily cap if they exceed it), and `finish()` refunds
    what was not used. A run that dies without reaching `finish()` keeps its whole reservation charged: conservative on purpose."""

    def __init__(self, state: dict, cfg: Config, wall, persist=None):
        self.state = state
        self.cfg = cfg
        self.wall = wall
        self.persist = persist or (lambda: None)
        self.reserved = {"read": 0, "write": 0}
        self._day = None

    def _today(self) -> dict:
        day = today_str(self.wall)
        b = self.state.get("budget")
        if b is None:  # first run ever: nothing spent
            b = self.state["budget"] = {}
        elif not valid_budget(b):  # a damaged slot (NaN, string, missing counter) FAILS CLOSED: today counts as fully spent
            b = self.state["budget"] = spent_budget(self.cfg, self.wall)
        if b.get("day") != day:
            b.clear()
            b.update({"day": day, "read_bytes": 0, "write_bytes": 0, "read_ops": 0, "write_ops": 0, "sync_ops": 0})
        return b

    def _cap(self, kind: str) -> int:
        return self.cfg.max_read_bytes_per_day if kind == "read" else self.cfg.max_write_bytes_per_day

    def reserve(self, kind: str, nbytes: int) -> int:
        """Reserve up to nbytes (what the daily cap still allows); persisted before returning. Returns the amount reserved."""

        b = self._today()
        self._day = b["day"]
        take = max(0, min(nbytes, self._cap(kind) - b[f"{kind}_bytes"]))
        b[f"{kind}_bytes"] += take
        self.reserved[kind] += take
        self.persist()
        return take

    def charge(self, kind: str, nbytes: int, ops: int = 1) -> None:
        b = self._today()
        if kind == "sync":
            b["sync_ops"] += ops
            return
        if kind not in ("read", "write"):
            raise ValueError(kind)
        if b["day"] != self._day:  # midnight passed mid-run: the reservation belonged to the cleared day, so today pays in full
            self.reserved = {"read": 0, "write": 0}
            self._day = b["day"]
        from_reserve = min(self.reserved[kind], nbytes)
        extra = nbytes - from_reserve
        if extra and b[f"{kind}_bytes"] + extra > self._cap(kind):
            raise BudgetExceeded(f"daily {kind} byte budget exhausted")
        self.reserved[kind] -= from_reserve
        b[f"{kind}_bytes"] += extra
        b[f"{kind}_ops"] += ops

    def finish(self) -> None:
        """Refund the unused part of the reservations and persist. Safe to call twice."""

        b = self._today()
        if b["day"] == self._day:
            for kind in ("read", "write"):
                b[f"{kind}_bytes"] = max(0, b[f"{kind}_bytes"] - self.reserved[kind])
        self.reserved = {"read": 0, "write": 0}
        self.persist()

    def remaining(self, kind: str) -> int:
        return max(0, self._cap(kind) - self._today()[f"{kind}_bytes"])  # a counter above the cap (lowered cap) is "spent", not negative

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
    read_day = ticks * (cfg.read_samples_per_tick + cfg.witness_reads) * READ_BLOCK + benches * cfg.seq_read_bytes
    read_day_worst = ticks * (cfg.read_max_attempts + cfg.witness_reads) * READ_BLOCK + benches * cfg.seq_read_bytes
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
