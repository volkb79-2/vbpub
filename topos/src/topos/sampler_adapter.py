"""Turn a sampler ``current`` answer into what the single view draws.

``convert_current`` produces a canonical :class:`~topos.model.Frame` (cgroup tree with
the registry metrics, host metrics) and puts everything the frame has no field for
into ``frame.host_meta['sampler']`` as plain data (per-CPU split, memory stack,
zswap and swap rates, KSM, io.cost state, the sampler's own health, process rows,
D-state counts, hard events). :class:`SamplerOverlay` hands those to the UI.

The rule that shapes every function here (docs/SAMPLER-PROTOCOL.md, Value states):
a metric the sampler marks ``not_applicable`` or ``unavailable`` is never a number.
It becomes a ``MetricValue`` with ``v=None`` (the UI prints ``n/a``), or ``unlimited``
for a limit of ``-1`` (the UI prints ``·``). A metric the sampler did not send at all
is absent from the frame (also ``n/a``). A real zero stays a real zero.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from topos.collect.cgroup import entity_kind, parent_key
from topos.model import Entity, EntityFrame, Frame, MetricValue
from topos.ui.overlay import ProcRow

CG_PREFIX = "cg:/"
DEFAULT_PAGE_SIZE = 4096
EVENT_RECENT_S = 120.0
"""A hard event is worth the WHY line for this long."""

Obj = dict[str, Any]

# ── value objects ─────────────────────────────────────────────────────


def _is_value(o: object) -> bool:
    return isinstance(o, dict) and o.get("s") == "value"


def _expand_value(o: Any) -> Any:
    """A ``compact`` value as the full value-object (protocol, ``current`` shaping).

    A bare number is a gauge, ``[delta, rate_per_s]`` a counter; a state object stays as it is
    (``unavailable`` states are kept, ``not_applicable`` ones are left out of a compact reply,
    so an absent metric is the way ``not_applicable`` arrives). ``raw`` is not sent.
    """
    if isinstance(o, bool):
        return o
    if isinstance(o, (int, float)):
        return {"s": "value", "v": o}
    if isinstance(o, list) and len(o) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in o):
        return {"s": "value", "v": o[0], "rate": o[1]}
    return o


def expand_entities(ents: Obj) -> Obj:
    """The ``entities`` of a ``current`` answer with every compact value expanded (full ones pass through)."""
    return {name: {m: _expand_value(o) for m, o in metrics.items()} if isinstance(metrics, dict) else metrics
            for name, metrics in ents.items()}


def _missing(o: Obj) -> MetricValue:
    """The metric exists but is not a number now: a permission problem or ``n/a``."""
    if o.get("s") == "unavailable" and o.get("why") == "permission_denied":
        return MetricValue(None, "unavail_perm")
    return MetricValue(None, "unavail_kernel")


def gauge(o: Obj | None, scale: float = 1.0, src: str = "exact") -> MetricValue | None:
    if o is None:
        return None
    if not _is_value(o):
        return _missing(o)
    return MetricValue(o["v"] * scale if scale != 1.0 else o["v"], src)


def rate(o: Obj | None, scale: float = 1.0, src: str = "derived") -> MetricValue | None:
    """A counter as its per-second rate (the sampler computed it over the measured interval)."""
    if o is None:
        return None
    if not _is_value(o) or "rate" not in o:
        return _missing(o)
    raw = o.get("raw")
    return MetricValue(o["rate"] * scale, src, raw if isinstance(raw, int) and not isinstance(raw, bool) else None)


def limit(o: Obj | None) -> MetricValue | None:
    """A configured limit: ``-1`` is ``max``, which is *not set*, never a number."""
    if o is None:
        return None
    if not _is_value(o):
        return _missing(o)
    if o["v"] == -1:
        return MetricValue(None, "unlimited")
    return MetricValue(o["v"], "exact")


def _num(o: Obj | None) -> float | None:
    return float(o["v"]) if _is_value(o) else None


def _rate_num(o: Obj | None) -> float | None:
    return float(o["rate"]) if _is_value(o) and "rate" in o else None


def _raw_num(o: Obj | None) -> float | None:
    return float(o["raw"]) if _is_value(o) and isinstance(o.get("raw"), (int, float)) else None


# ── cgroup metric table ───────────────────────────────────────────────

# (registry name, sampler metric, kind, scale)
_G, _R, _L = "g", "r", "l"
_CG_TABLE: tuple[tuple[str, str, str, float], ...] = (
    ("ram", "mem.current", _G, 1.0),
    ("anon", "mem.anon", _G, 1.0),
    ("file", "mem.file", _G, 1.0),
    ("shmem", "mem.shmem", _G, 1.0),
    ("sock", "mem.sock", _G, 1.0),
    ("z_pool", "mem.zswap", _G, 1.0),
    ("z_eq", "mem.zswapped", _G, 1.0),
    ("cpu_pct", "cpu.usage_usec", _R, 1e-4),  # usec per second -> percent of one CPU
    ("cpu_throttled_pct", "cpu.throttled_usec", _R, 1e-4),
    ("cpu_weight", "limit.cpu.weight", _G, 1.0),
    ("io_weight", "limit.io.weight", _G, 1.0),
    ("cpu_quota_us", "limit.cpu.max.quota", _L, 1.0),
    ("cpu_period_us", "limit.cpu.max.period", _G, 1.0),
    ("psi_mem_some_avg10", "psi.memory.some.avg10", _G, 1.0),
    ("psi_mem_full_avg10", "psi.memory.full.avg10", _G, 1.0),
    ("psi_io_some_avg10", "psi.io.some.avg10", _G, 1.0),
    ("psi_io_full_avg10", "psi.io.full.avg10", _G, 1.0),
    ("psi_cpu_some_avg10", "psi.cpu.some.avg10", _G, 1.0),
    ("psi_cpu_full_avg10", "psi.cpu.full.avg10", _G, 1.0),
    ("io_r_bps", "io.rbytes", _R, 1.0),
    ("io_w_bps", "io.wbytes", _R, 1.0),
    ("io_r_iops", "io.rios", _R, 1.0),
    ("io_w_iops", "io.wios", _R, 1.0),
    ("io_discard_bps", "io.dbytes", _R, 1.0),
    ("io_max_capped", "limit.io.max.capped", _G, 1.0),
    ("io_cost_usage_per_s", "io.cost.usage", _R, 1.0),
    ("mem_min", "limit.memory.min", _L, 1.0),
    ("mem_low", "limit.memory.low", _L, 1.0),
    ("mem_high", "limit.memory.high", _L, 1.0),
    ("mem_max", "limit.memory.max", _L, 1.0),
    ("swap_max", "limit.memory.swap.max", _L, 1.0),
    ("pids_current", "pids.current", _G, 1.0),
    ("pids_max", "limit.pids.max", _L, 1.0),
    ("mem_events_low_per_s", "memev.low", _R, 1.0),
    ("mem_events_high_per_s", "memev.high", _R, 1.0),
    ("mem_events_max_per_s", "memev.max", _R, 1.0),
    ("mem_events_oom_per_s", "memev.oom", _R, 1.0),
    ("mem_events_oom_kill_per_s", "memev.oom_kill", _R, 1.0),
    ("pgscan_per_s", "mem.pgscan", _R, 1.0),
    ("pgsteal_per_s", "mem.pgsteal", _R, 1.0),
    ("majflt_per_s", "mem.pgmajfault", _R, 1.0),
    ("rf_f_per_s", "mem.workingset_refault_file", _R, 1.0),
)


def cgroup_metrics(raw: Obj, host: Obj | None = None) -> dict[str, MetricValue]:
    """Registry metrics of one cgroup. *host* backs the root, which has no ``memory.stat``."""
    out: dict[str, MetricValue] = {}
    # swap held on disk: swap in use minus what sits compressed in zswap minus the swap cache
    sw = raw.get("mem.swap_current")
    if sw is not None:
        if host is not None:  # the root has no memory.stat: the host's Zswapped and SwapCached stand in
            zd, sc = host.get("zswap.stored_bytes"), host.get("meminfo.SwapCached")
        else:
            zd, sc = raw.get("mem.zswapped"), raw.get("mem.swapcached")
        parts = (_num(sw), _num(zd), _num(sc))
        if None in parts:
            out["swap_disk"] = MetricValue(None, "derived")
        else:
            out["swap_disk"] = MetricValue(max(0.0, parts[0] - parts[1] - parts[2]), "derived")  # type: ignore[operator]
    for name, key, kind, scale in _CG_TABLE:
        o = raw.get(key)
        if o is None:
            continue
        mv = gauge(o, scale) if kind == _G else rate(o, scale) if kind == _R else limit(o)
        if mv is not None:
            out[name] = mv
    # compression ratio: uncompressed-equivalent over compressed
    comp, equiv = _num(raw.get("mem.zswap")), _num(raw.get("mem.zswapped"))
    if comp is not None and equiv is not None and comp > 0:
        out["ratio"] = MetricValue(equiv / comp, "derived")
    elif "mem.zswap" in raw and "mem.zswapped" in raw:
        out["ratio"] = MetricValue(None, "derived")
    # refaults: zswap-served, disk-served (anon refaults not served by zswap), file
    zin, anon = raw.get("mem.zswpin"), raw.get("mem.workingset_refault_anon")
    z_rate, a_rate = _rate_num(zin), _rate_num(anon)
    if zin is not None:
        out["rf_z_per_s"] = MetricValue(z_rate, "derived") if z_rate is not None else _missing(zin)
    if anon is not None:
        if z_rate is not None and a_rate is not None:
            out["rf_d_per_s"] = MetricValue(max(0.0, a_rate - z_rate), "derived")
        elif a_rate is None:
            out["rf_d_per_s"] = _missing(anon)
    # usage as a percentage of each ceiling
    ram = out.get("ram")
    for name, lim_name in (("headroom_high_pct", "mem_high"), ("headroom_max_pct", "mem_max")):
        lim_mv = out.get(lim_name)
        if lim_mv is None:
            continue
        if lim_mv.src == "unlimited":
            out[name] = MetricValue(None, "unlimited")
        elif ram is not None and ram.v is not None and lim_mv.v:
            out[name] = MetricValue(ram.v / lim_mv.v * 100.0, "derived")
        else:
            out[name] = MetricValue(None, "derived")
    return out


# ── host ──────────────────────────────────────────────────────────────

_HOST_TABLE: tuple[tuple[str, str, str], ...] = (
    ("host_mem_total", "meminfo.MemTotal", _G),
    ("host_mem_available", "meminfo.MemAvailable", _G),
    ("host_swap_total", "meminfo.SwapTotal", _G),
    ("host_swap_free", "meminfo.SwapFree", _G),
    ("host_swapcached", "meminfo.SwapCached", _G),
    ("host_zswap_pool", "zswap.pool_bytes", _G),
    ("host_zswap_stored", "zswap.stored_bytes", _G),
    ("host_zswap_enabled", "zswap.enabled", _G),
    ("host_zswap_max_pool_percent", "zswap.max_pool_percent", _G),
    ("host_load1", "host.load1", _G),
    ("host_load5", "host.load5", _G),
    ("host_load15", "host.load15", _G),
)
_HOST_PSI = (
    ("host_psi_mem_some_avg10", "psi.memory.some.avg10"),
    ("host_psi_mem_full_avg10", "psi.memory.full.avg10"),
    ("host_psi_io_some_avg10", "psi.io.some.avg10"),
    ("host_psi_io_full_avg10", "psi.io.full.avg10"),
    ("host_psi_cpu_some_avg10", "psi.cpu.some.avg10"),
)


def host_metrics(host: Obj, root: Obj) -> dict[str, MetricValue]:
    out: dict[str, MetricValue] = {}
    for name, key, _kind in _HOST_TABLE:
        mv = gauge(host.get(key))
        if mv is not None:
            out[name] = mv
    for name, key in _HOST_PSI:  # the root cgroup's pressure is /proc/pressure/*
        mv = gauge(root.get(key))
        if mv is not None:
            out[name] = mv
    if "host_zswap_pool" in out and "host_zswap_stored" in out:
        pool, stored = out["host_zswap_pool"].v, out["host_zswap_stored"].v
        out["host_zswap_ratio"] = MetricValue(stored / pool if pool and stored is not None else None, "derived")
    vrate = root.get("io.cost.vrate")
    if _is_value(vrate):
        out["host_io_cost_vrate_pct"] = MetricValue(float(vrate["v"]), "exact")
        out["host_io_cost_enabled"] = MetricValue(1, "exact")
    elif (vrate is not None and vrate.get("s") == "not_applicable") or (vrate is None and root):
        out["host_io_cost_enabled"] = MetricValue(0, "exact")  # compact replies leave not_applicable out
    return out


_PARTITION = re.compile(r"^(?:(?:sd|vd|xvd|hd)[a-z]+\d+|(?:nvme\d+n\d+|mmcblk\d+)p\d+)$")
_NOT_DISKS = re.compile(r"^(?:loop|ram|zram|sr|fd)\d*")


def _await(o: Obj | None) -> float | str | None:
    if _is_value(o):
        return float(o["v"])  # type: ignore[index]
    if isinstance(o, dict) and o.get("s") == "not_applicable" and o.get("why") == "no_activity_in_interval":
        return "idle"  # nothing to wait for: not a zero, not unknown
    return None


def block_devices(entities: Obj, limit_n: int = 3) -> list[dict[str, Any]]:
    names = {k[4:] for k in entities if k.startswith("dev:")}
    rows: list[dict[str, Any]] = []
    for name in names:
        if _NOT_DISKS.match(name):
            continue
        # compact replies leave not_applicable out: a disk that answers but has no await had no IO to wait for
        if _PARTITION.match(name) and re.sub(r"p?\d+$", "", name) in names:
            continue  # the whole disk reports it
        d = entities[f"dev:{name}"]
        rd, wr = _rate_num(d.get("disk.rd_sectors")), _rate_num(d.get("disk.wr_sectors"))
        rows.append({
            "name": name,
            "read_bps": None if rd is None else rd * 512.0,
            "write_bps": None if wr is None else wr * 512.0,
            "read_iops": _rate_num(d.get("disk.reads")),
            "write_iops": _rate_num(d.get("disk.writes")),
            "await_ms": _await(d.get("disk.await_ms")) if "disk.await_ms" in d or "disk.util_pct" not in d else "idle",
            "util_pct": _num(d.get("disk.util_pct")),
            "inflight": _num(d.get("disk.inflight")),
        })
    rows.sort(key=lambda r: (-(r["util_pct"] or 0.0), -((r["read_bps"] or 0.0) + (r["write_bps"] or 0.0)), r["name"]))
    return rows[:limit_n]


# ── what to ask for ───────────────────────────────────────────────────

VIEW_FIELDS: tuple[str, ...] = tuple(dict.fromkeys((
    *(key for _n, key, _k, _s in _CG_TABLE),
    "mem.swap_current", "mem.swapcached", "mem.zswpin", "mem.workingset_refault_anon", "procs.d_state",
    *(key for _n, key, _k in _HOST_TABLE),
    *(key for _n, key in _HOST_PSI),
    "io.cost.vrate",
    "host.procs_running", "memstack.*", "vm.zswpin", "vm.zswpout", "vm.zswpwb", "vm.pswpin", "vm.pswpout",
    "zswap.reject_*", "ksm.run", "ksm.pages_sharing", "ksm.pages_shared", "ksm.general_profit",
    "cpupct.us", "cpupct.sy", "cpupct.io", "cpupct.st",
    "cpu.idle",  # always a value: keeps a CPU whose percentages are n/a (no jiffies passed) in the per-CPU list
    "disk.rd_sectors", "disk.wr_sectors", "disk.reads", "disk.writes", "disk.await_ms", "disk.util_pct", "disk.inflight",
    "sampler.rss_bytes", "sampler.work_us", "sampler.cpu_ticks", "sampler.overruns", "sampler.skipped_ticks",
)))
"""Exactly the metrics the single view shows (``current`` ``fields``; ``name*`` is a prefix)."""


# ── the extras the frame has no field for ─────────────────────────────

_MEMSTACK = (
    "anon", "file_cache", "shmem", "buffers", "slab_reclaimable", "slab_unreclaimable", "kernel_stack",
    "pagetables", "percpu", "zswap_pool", "free", "other",
)
_REJECTS = (
    "reject_alloc_fail", "reject_compress_fail", "reject_compress_poor", "reject_kmemcache_fail", "reject_reclaim_fail",
)


def _cpus(entities: Obj) -> list[list[float] | None] | None:
    idx = sorted(int(k[4:]) for k in entities if k.startswith("cpu:") and k[4:].isdigit())
    if not idx:
        return None
    out: list[list[float] | None] = []
    for n in idx:
        e = entities[f"cpu:{n}"]
        vals = [_num(e.get(f"cpupct.{k}")) for k in ("us", "sy", "io", "st")]
        out.append(None if any(v is None for v in vals) else [v / 100.0 for v in vals])  # type: ignore[operator]
    return out


def _ksm(host: Obj, page_size: int) -> dict[str, Any] | None:
    run = host.get("ksm.run")
    if run is None:
        return {"state": "off"} if host else None  # compact replies leave not_applicable (no KSM) out
    if isinstance(run, dict) and run.get("s") == "not_applicable":
        return {"state": "off"}
    if not _is_value(run):
        return {"state": "na"}
    if run["v"] == 0:
        return {"state": "off"}
    sharing, shared, profit = _num(host.get("ksm.pages_sharing")), _num(host.get("ksm.pages_shared")), _num(host.get("ksm.general_profit"))
    return {
        "state": "on",
        "sharing": None if sharing is None else sharing * page_size,
        "shared": None if shared is None else shared * page_size,
        "profit": profit,
    }


def sampler_extras(
    cur: Obj,
    *,
    hello: Obj | None,
    status: Obj | None,
    events: Iterable[Obj] | None,
    epoch_changed_ts: float | None,
    socket_path: str,
) -> dict[str, Any]:
    ents: Obj = cur.get("entities") or {}
    host: Obj = ents.get("host") or {}
    root: Obj = ents.get(CG_PREFIX) or {}
    page_size = int((hello or {}).get("page_size") or DEFAULT_PAGE_SIZE)
    clk_tck = float((hello or {}).get("clk_tck") or 100)
    pids: Obj = cur.get("pids") or {}
    scanned = bool(pids.get("scanned"))

    d_state: dict[str, int] = {}
    for name, ent in ents.items():
        if name.startswith(CG_PREFIX):
            v = _num(ent.get("procs.d_state"))
            if v is not None:
                d_state[_key(name)] = int(v)

    procs: dict[str, list[Obj]] = {}
    for p in cur.get("procs") or ():
        cg = p.get("cgroup")
        if not isinstance(cg, str) or not cg.startswith(CG_PREFIX):
            continue
        procs.setdefault(_key(cg), []).append(p)

    me: Obj = ents.get("sampler") or {}
    self_cpu = None
    if status is not None and _is_value((status.get("self") or {}).get("cpu_pct_recent_60s")):
        self_cpu = float(status["self"]["cpu_pct_recent_60s"]["v"])
    elif _rate_num(me.get("sampler.cpu_ticks")) is not None:
        self_cpu = _rate_num(me.get("sampler.cpu_ticks")) / clk_tck * 100.0  # type: ignore[operator]
    throttled = None
    if status is not None:
        nr = (status.get("throttle") or {}).get("nr_throttled")
        throttled = _num(nr)

    stack = {n: _num(host.get(f"memstack.{n}")) for n in _MEMSTACK if f"memstack.{n}" in host}
    pswpin, pswpout = _rate_num(host.get("vm.pswpin")), _rate_num(host.get("vm.pswpout"))
    rej = [_rate_num(host.get(f"zswap.{n}")) for n in _REJECTS]
    vrate = root.get("io.cost.vrate")
    timing: Obj = (status or {}).get("timing") or {}

    def _count(key: str, metric: str) -> float | None:
        n = timing.get(key)
        if isinstance(n, (int, float)) and not isinstance(n, bool):
            return float(n)
        return _raw_num(me.get(metric))  # compact replies carry no raw reading

    extras: dict[str, Any] = {
        "tick": cur.get("tick"),
        "epoch": cur.get("epoch"),
        "truncated": bool(cur.get("truncated")),
        "view": (hello or {}).get("cgroup_view"),
        "socket": socket_path,
        "cpus": _cpus(ents),
        "run": _num(host.get("host.procs_running")),
        "blocked_total": float(pids["d_state"]) if scanned and isinstance(pids.get("d_state"), (int, float)) else None,
        "d_state_unattributed": pids.get("d_unattributed") if scanned else None,
        "scanned": scanned,
        "d_state": d_state,
        "procs": procs,
        "memstack": stack or None,
        "zswap": {
            "in": _rate_num(host.get("vm.zswpin")),
            "out": _rate_num(host.get("vm.zswpout")),
            "wb": _rate_num(host.get("vm.zswpwb")),
            "rej": None if any(r is None for r in rej) else float(sum(rej)),  # type: ignore[arg-type]
        },
        "swap_io": {
            "in": None if pswpin is None else pswpin * page_size,
            "out": None if pswpout is None else pswpout * page_size,
        },
        "ksm": _ksm(host, page_size),
        "iocost": (  # a compact reply leaves not_applicable (io.cost off) out: absent next to a root is "off"
            {"state": "off"} if vrate is None and root else None if vrate is None else
            {"state": "on" if _is_value(vrate) else "off" if vrate.get("s") == "not_applicable" else "na"}
        ),
        "sampler": {
            "cpu_pct": self_cpu,
            "rss": _num(me.get("sampler.rss_bytes")),
            "work_us": _num(me.get("sampler.work_us")),
            "overruns": _count("tick_overruns", "sampler.overruns"),
            "skipped": _count("skipped_ticks", "sampler.skipped_ticks"),
            "throttled": throttled,
        },
        "events": [e for e in (events or ()) if isinstance(e, dict)],
        "epoch_changed_ts": epoch_changed_ts,
    }
    return extras


# ── the frame ─────────────────────────────────────────────────────────


def _key(entity_name: str) -> str:
    """``cg:/a/b`` -> ``a/b``; ``cg:/`` -> ``""`` (the collector's entity keys)."""
    return entity_name[len(CG_PREFIX):]


def convert_current(
    cur: Obj,
    *,
    hello: Obj | None = None,
    status: Obj | None = None,
    events: Iterable[Obj] | None = None,
    epoch_changed_ts: float | None = None,
    socket_path: str = "",
    enrich: Callable[[dict[str, Entity]], dict[str, Entity]] | None = None,
) -> Frame:
    cur = {**cur, "entities": expand_entities(cur.get("entities") or {})}
    ents: Obj = cur["entities"]
    cg_names = sorted(n for n in ents if n.startswith(CG_PREFIX))
    entities: dict[str, Entity] = {}
    for name in cg_names:
        key = _key(name)
        entities[key] = Entity(key=key, kind=entity_kind(key), parent=parent_key(key))  # type: ignore[arg-type]
    if enrich is not None:
        try:
            entities = enrich(entities)
        except Exception:  # noqa: BLE001 - docker names are decoration; the tree must still draw
            pass
    host: Obj = ents.get("host") or {}
    frames = {
        key: EntityFrame(entity=ent, metrics=cgroup_metrics(ents[f"{CG_PREFIX}{key}"], host if key == "" else None))
        for key, ent in entities.items()
    }
    root: Obj = ents.get(CG_PREFIX) or {}
    dt_ms = cur.get("dt_ms")
    extras = sampler_extras(
        cur, hello=hello, status=status, events=events, epoch_changed_ts=epoch_changed_ts, socket_path=socket_path
    )
    return Frame(
        schema_version=1,
        ts=float(cur["ts_ms"]) / 1000.0,
        interval_s=float(dt_ms) / 1000.0 if isinstance(dt_ms, (int, float)) and dt_ms > 0 else 1.0,
        host=host_metrics(host, root),
        entities=frames,
        host_meta={"block_devices": block_devices(ents), "sampler": extras},
    )


# ── overlay ───────────────────────────────────────────────────────────


def _proc_row(p: Obj) -> ProcRow:
    cpu = _num(p.get("cpu_pct"))
    rd, wr = _num(p.get("io_read_bps")), _num(p.get("io_write_bps"))
    io = None if rd is None and wr is None else (rd or 0.0) + (wr or 0.0)
    blk = _num(p.get("blkio_delay_ms_per_s"))
    rss = p.get("rss_bytes")
    return ProcRow(
        pid=int(p.get("pid", 0)),
        comm=str(p.get("comm", "?")),
        cpu=cpu,
        state=str(p.get("state", "?")),
        io_bps=io,
        majflt=_num(p.get("majflt_per_s")),
        blkio_ms=blk,
        rss=int(rss) if isinstance(rss, (int, float)) and not isinstance(rss, bool) else None,
        cmdline=str(p.get("cmdline") or ""),
    )


def describe_event(event: Obj, now: float) -> str | None:
    """One line for a hard event, or ``None`` when it is not an alarm (PSI coming back down)."""
    ent = str(event.get("entity") or "")
    ent = ent[len(CG_PREFIX) - 1:] if ent.startswith(CG_PREFIX) else ent
    detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
    age = max(0, int(now - float(event.get("ts_ms", 0)) / 1000.0))
    kind = event.get("type")
    if kind == "oom_kill":
        return f"OOM kill in {ent or '/'} (+{detail.get('delta', '?')}, total {detail.get('total', '?')}) {age}s ago"
    if kind == "cgroup_vanished":
        return f"cgroup {ent} vanished {age}s ago"
    if kind == "limit_changed":
        return f"{detail.get('file', 'limit')} of {ent or '/'} changed {detail.get('old', '?')} -> {detail.get('new', '?')} {age}s ago"
    if kind == "psi_cross" and detail.get("direction") == "up":
        return (
            f"{detail.get('resource', '?')} pressure crossed {detail.get('threshold', '?')}% "
            f"(avg10 {detail.get('avg10', '?')}) {age}s ago"
        )
    return None


class SamplerOverlay:
    """Real per-CPU, process and D-state data from ``frame.host_meta['sampler']``."""

    label = ""
    demo = False

    def __init__(self, socket_path: Path | str = "") -> None:
        self.socket_path = str(socket_path)

    @staticmethod
    def _x(frame: Frame) -> dict[str, Any]:
        extras = (frame.host_meta or {}).get("sampler")
        return extras if isinstance(extras, dict) else {}

    def per_cpu(self, frame: Frame):
        cpus = self._x(frame).get("cpus")
        return None if cpus is None else [None if c is None else tuple(c) for c in cpus]

    def processes(self, frame: Frame) -> dict[str, list[ProcRow]]:
        return {k: [_proc_row(p) for p in v] for k, v in (self._x(frame).get("procs") or {}).items()}

    def counts(self, frame: Frame) -> tuple[int | None, int | None] | None:
        x = self._x(frame)
        if not x:
            return None
        run, blocked = x.get("run"), x.get("blocked_total")
        return (None if run is None else int(run), None if blocked is None else int(blocked))

    def blocked(self, frame: Frame) -> dict[str, int] | None:
        x = self._x(frame)
        return x.get("d_state") if x.get("scanned") else None

    def why(self, frame: Frame) -> str | None:
        newest: tuple[float, str] | None = None
        for ev in self._x(frame).get("events") or ():
            if frame.ts - float(ev.get("ts_ms", 0)) / 1000.0 > EVENT_RECENT_S:
                continue
            text = describe_event(ev, frame.ts)
            if text is not None and (newest is None or ev["ts_ms"] >= newest[0]):
                newest = (ev["ts_ms"], text)
        return None if newest is None else newest[1]

    def notes(self, frame: Frame) -> list[str]:
        x = self._x(frame)
        out: list[str] = []
        view = x.get("view")
        if isinstance(view, dict) and view.get("mode") == "namespaced":
            out.append(
                f"NAMESPACED cgroup view ({view.get('because', '?')}): only this container's slice of the host is shown; "
                f"for the whole host connect to the daemon socket on the host ({x.get('socket') or self.socket_path})"
            )
        elif isinstance(view, dict) and view.get("mode") == "unknown":
            out.append(f"cgroup view unknown ({view.get('because', '?')}): host totals may not be the whole host")
        if x.get("truncated"):
            out.append("TRUNCATED: the sampler's answer hit its size cap, some cgroups or processes are missing")
        changed = x.get("epoch_changed_ts")
        if changed is not None and frame.ts - changed < 60.0:
            out.append("sampler restarted: counters and history before this point are not comparable")
        return out
