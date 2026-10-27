"""Kill switches, load-aware gating and the mid-run guard. Nothing here submits IO; it only reads and decides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .collectors import Collector, governed_write_bps
from .env import Env, StopRequested


def kill_switch_reason(cfg, environ=None) -> str | None:
    """Every kill switch, strongest first. Any one of them stops the run (and a running probe at its next safe point)."""

    environ = os.environ if environ is None else environ
    if not cfg.enabled:
        return "config:enabled=false"
    if environ.get("IOGOV_DISABLE") == "1":
        return "env:IOGOV_DISABLE=1"
    for path in (cfg.kill_switch_file, os.path.join(cfg.state_dir, "DISABLED")):
        if path and os.path.exists(path):
            return f"flag-file:{path}"
    return None


@dataclass
class GateResult:
    ok: bool
    reasons: list = field(default_factory=list)
    quiet: bool = False
    ctx: dict = field(default_factory=dict)
    before: dict | None = None  # the two passive snapshots the verdict rests on: a gated tick still journals them as evidence
    after: dict | None = None


def _some_avg10(pressure: dict | None) -> float | None:
    try:
        return pressure["some"]["avg10"]
    except (TypeError, KeyError):
        return None


def _full_avg10(pressure: dict | None) -> float | None:
    try:
        return pressure["full"]["avg10"]
    except (TypeError, KeyError):
        return None


def protected_pressure_max(snapshot: dict) -> float | None:
    values = []
    for group in ("protected", "containers"):
        for cur in snapshot["cgroups"][group].values():
            v = _some_avg10((cur or {}).get("pressure"))
            if v is not None:
                values.append(v)
    return max(values) if values else None


def inflight_total(snapshot: dict) -> int | None:
    total, seen = 0, False
    for st in snapshot["devices"].values():
        if st and "inflight_r" in st:  # the dedicated `inflight` file (reads, writes) is the authority
            total += st["inflight_r"] + st["inflight_w"]
            seen = True
        elif st and "in_flight" in st:
            total += st["in_flight"]
            seen = True
    return total if seen else None


class Gate:
    def __init__(self, cfg, collector: Collector, env: Env):
        self.cfg, self.collector, self.env = cfg, collector, env

    def check(self, kind: str) -> GateResult:
        """kind 'tick' (cheap read probes) or 'bench' (the write bench: strict calm requirement)."""

        cfg = self.cfg
        reasons: list[str] = []
        ks = kill_switch_reason(cfg)
        if ks:
            return GateResult(False, [f"kill_switch:{ks}"])
        a = self.collector.snapshot()
        self.env.sleep(cfg.quiet_probe_s)
        b = self.collector.snapshot()
        some = _some_avg10(b["pressure"])
        full = _full_avg10(b["pressure"])
        prot = protected_pressure_max(b)
        gbps = governed_write_bps(a, b)
        infl = inflight_total(b)
        ncpu = os.cpu_count() or 1
        load1 = b["loadavg"][0] if b["loadavg"] else None
        ctx = {
            "pressure_some_avg10": some, "pressure_full_avg10": full, "protected_some_avg10": prot,
            "memory_some_avg10": _some_avg10(b.get("pressure_memory")), "memory_full_avg10": _full_avg10(b.get("pressure_memory")),
            "governed_w_bps": gbps, "inflight": infl, "load1": load1,
        }
        if kind == "tick":
            if some is not None and some > cfg.tick_pressure_some_max:
                reasons.append(f"io_pressure_high:{some}>{cfg.tick_pressure_some_max}")
        else:
            if some is None:
                reasons.append("io_pressure_unreadable")
            elif some > cfg.bench_pressure_some_max:
                reasons.append(f"io_pressure_high:{some}>{cfg.bench_pressure_some_max}")
            if full is not None and full > cfg.bench_pressure_full_max:
                reasons.append(f"io_pressure_full:{full}>{cfg.bench_pressure_full_max}")
            if prot is not None and prot > cfg.protected_pressure_some_max:
                reasons.append(f"protected_pressure:{prot}>{cfg.protected_pressure_some_max}")
            if gbps is not None and gbps > cfg.bench_governed_write_bps_max:
                reasons.append(f"governed_writing:{int(gbps)}Bps")
            if infl:
                reasons.append(f"device_busy:inflight={infl}")
        if load1 is not None and load1 / ncpu > cfg.loadavg_per_cpu_max:
            reasons.append(f"load_high:{load1}/{ncpu}cpu")
        quiet = (
            not reasons
            and (infl in (0, None))
            and (gbps is None or gbps < cfg.quiet_governed_write_bps)
        )
        return GateResult(not reasons, reasons, quiet, ctx, a, b)


class RunGuard:
    """Called between IOs. Raises StopRequested on: stop flag, kill switch, rising pressure, or a too-slow IO."""

    def __init__(self, cfg, collector: Collector, env: Env, strict_pressure: bool):
        self.cfg, self.collector, self.env = cfg, collector, env
        self.strict = strict_pressure
        self.ops = 0

    def check(self) -> None:
        self.env.stop.check()
        self.ops += 1
        if self.ops % self.cfg.pressure_recheck_every_ops:
            return
        ks = kill_switch_reason(self.cfg)
        if ks:
            raise StopRequested(f"kill_switch:{ks}")
        some = _some_avg10(self.collector.system_pressure())
        limit = self.cfg.bench_pressure_some_max * 5 if self.strict else self.cfg.tick_pressure_some_max
        if some is not None and some > limit:
            raise StopRequested(f"io_pressure_rose:{some}>{limit}")

    def note_latency(self, seconds: float) -> None:
        if seconds * 1000.0 > self.cfg.abort_latency_ms:
            raise StopRequested(f"slow_io:{seconds * 1000.0:.0f}ms>{self.cfg.abort_latency_ms:.0f}ms")


class Backoff:
    """Exponential backoff after gated runs; state lives in the state dict (`backoff_until`, `backoff_n`)."""

    def __init__(self, state: dict, cfg, wall, key: str):
        self.state, self.cfg, self.wall, self.key = state, cfg, wall, key

    def blocked(self) -> float:
        until = self.state.get("backoff", {}).get(self.key, {}).get("until", 0.0)
        now = self.wall()
        return max(0.0, min(until, now + self.cfg.backoff_max_s) - now)  # a backwards clock jump cannot extend a backoff past its cap

    def hit(self) -> float:
        slot = self.state.setdefault("backoff", {}).setdefault(self.key, {"n": 0, "until": 0.0})
        slot["n"] += 1
        delay = min(self.cfg.backoff_max_s, self.cfg.backoff_base_s * 2 ** (slot["n"] - 1))
        slot["until"] = self.wall() + delay
        return delay

    def clear(self) -> None:
        self.state.setdefault("backoff", {})[self.key] = {"n": 0, "until": 0.0}
