from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from groop.model import Entity, EntityKey, MetricSource, MetricValue


@dataclass
class ReadResult:
    value: object | None
    src: MetricSource


@dataclass
class CgroupSample:
    entity: Entity
    path: Path
    metrics: dict[str, MetricValue] = field(default_factory=dict)
    raw_counters: dict[str, int] = field(default_factory=dict)


def _unavail_for(exc: OSError) -> MetricSource:
    return "unavail_perm" if isinstance(exc, PermissionError) else "unavail_kernel"


def read_text(path: Path) -> ReadResult:
    try:
        return ReadResult(path.read_text().strip(), "exact")
    except OSError as exc:
        return ReadResult(None, _unavail_for(exc))


def parse_int_text(text: str) -> int | None:
    if text.strip() == "max":
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def read_int(path: Path) -> tuple[int | None, MetricSource]:
    result = read_text(path)
    if result.value is None:
        return None, result.src
    value = parse_int_text(str(result.value))
    return (value, "exact") if value is not None else (None, "unavail_kernel")


def read_limit(path: Path) -> tuple[int | None, MetricSource]:
    result = read_text(path)
    if result.value is None:
        return None, result.src
    text = str(result.value).strip()
    if text == "max":
        return None, "unlimited"
    value = parse_int_text(text)
    return (value, "exact") if value is not None else (None, "unavail_kernel")


def read_flat_kv(path: Path) -> tuple[dict[str, int], MetricSource]:
    result = read_text(path)
    if result.value is None:
        return {}, result.src
    out: dict[str, int] = {}
    for line in str(result.value).splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out, "exact"


def read_pressure(path: Path) -> tuple[dict[str, dict[str, float]], MetricSource]:
    result = read_text(path)
    if result.value is None:
        return {}, result.src
    out: dict[str, dict[str, float]] = {}
    for line in str(result.value).splitlines():
        parts = line.split()
        if not parts:
            continue
        values: dict[str, float] = {}
        for part in parts[1:]:
            key, _, value = part.partition("=")
            try:
                values[key] = float(value)
            except ValueError:
                pass
        out[parts[0]] = values
    return out, "exact"


def read_io_stat(path: Path) -> tuple[dict[str, dict[str, int]], MetricSource]:
    result = read_text(path)
    if result.value is None:
        return {}, result.src
    devices: dict[str, dict[str, int]] = {}
    for line in str(result.value).splitlines():
        parts = line.split()
        if not parts:
            continue
        values: dict[str, int] = {}
        for part in parts[1:]:
            key, _, raw = part.partition("=")
            try:
                values[key] = int(raw)
            except ValueError:
                pass
        devices[parts[0]] = values
    return devices, "exact"


def read_io_weight(path: Path) -> tuple[int | None, MetricSource]:
    result = read_text(path)
    if result.value is None:
        return None, result.src
    for line in str(result.value).splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "default" and len(parts) >= 2:
            try:
                return int(parts[1]), "exact"
            except ValueError:
                continue
        for part in parts:
            if part.startswith("default="):
                try:
                    return int(part.partition("=")[2]), "exact"
                except ValueError:
                    continue
    return None, "unavail_kernel"


def read_io_max_capped(path: Path) -> tuple[int | None, MetricSource]:
    result = read_text(path)
    if result.value is None:
        return None, result.src
    capped = 0
    for line in str(result.value).splitlines():
        parts = line.split()
        for part in parts[1:]:
            _key, _, raw = part.partition("=")
            if raw and raw != "max":
                try:
                    int(raw)
                except ValueError:
                    continue
                capped = 1
                break
        if capped:
            break
    return capped, "exact"


def read_cpu_max(path: Path) -> tuple[int | None, int | None, MetricSource]:
    result = read_text(path)
    if result.value is None:
        return None, None, result.src
    parts = str(result.value).split()
    if len(parts) != 2:
        return None, None, "unavail_kernel"
    unlimited = parts[0] == "max"
    quota = None if unlimited else parse_int_text(parts[0])
    period = parse_int_text(parts[1])
    if period is None:
        return None, None, "unavail_kernel"
    return quota, period, "unlimited" if unlimited else "exact"


def entity_kind(key: EntityKey) -> str:
    if key == "":
        return "root"
    name = Path(key).name
    if name.endswith(".slice"):
        return "slice"
    if name.endswith(".scope"):
        return "scope"
    if name.endswith(".service"):
        return "service"
    return "other"


def parent_key(key: EntityKey) -> EntityKey | None:
    if key == "":
        return None
    parent = str(Path(key).parent)
    return "" if parent == "." else parent


def walk_entities(root: Path) -> dict[EntityKey, Entity]:
    entities: dict[EntityKey, Entity] = {}
    for dirpath, dirnames, _filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        path = Path(dirpath)
        rel = path.relative_to(root)
        key = "" if str(rel) == "." else rel.as_posix()
        entities[key] = Entity(key=key, kind=entity_kind(key), parent=parent_key(key))
    return dict(sorted(entities.items()))


def _metric_from_int(path: Path) -> MetricValue:
    value, src = read_int(path)
    return MetricValue(value, src)


def _metric_from_limit(path: Path) -> MetricValue:
    value, src = read_limit(path)
    return MetricValue(value, src)


def _stat_metric(stats: dict[str, int], src: MetricSource, name: str) -> MetricValue:
    if src != "exact":
        return MetricValue(None, src)
    return MetricValue(stats[name], "exact") if name in stats else MetricValue(None, "unavail_kernel")


def _psi_metric(pressure: dict[str, dict[str, float]], src: MetricSource, section: str) -> MetricValue:
    if src != "exact":
        return MetricValue(None, src)
    value = pressure.get(section, {}).get("avg10")
    return MetricValue(value, "exact") if value is not None else MetricValue(None, "unavail_kernel")


def _sum_io(devices: dict[str, dict[str, int]], key: str) -> int:
    return sum(values.get(key, 0) for values in devices.values())


def collect_cgroup(root: Path, key: EntityKey, entity: Entity) -> CgroupSample:
    path = root if key == "" else root / key
    sample = CgroupSample(entity=entity, path=path)
    stats, stat_src = read_flat_kv(path / "memory.stat")
    events, events_src = read_flat_kv(path / "memory.events")
    pids_events, pids_events_src = read_flat_kv(path / "pids.events")
    cpu_stat, cpu_stat_src = read_flat_kv(path / "cpu.stat")
    memory_pressure, mem_pressure_src = read_pressure(path / "memory.pressure")
    io_pressure, io_pressure_src = read_pressure(path / "io.pressure")
    cpu_pressure, cpu_pressure_src = read_pressure(path / "cpu.pressure")
    io_stat, io_stat_src = read_io_stat(path / "io.stat")

    sample.metrics["ram"] = _metric_from_int(path / "memory.current")
    for metric, stat_name in (("anon", "anon"), ("file", "file"), ("shmem", "shmem"), ("sock", "sock"), ("z_eq", "zswapped")):
        sample.metrics[metric] = _stat_metric(stats, stat_src, stat_name)
    sample.metrics["z_pool"] = _metric_from_int(path / "memory.zswap.current")
    swap_current, swap_src = read_int(path / "memory.swap.current")
    if swap_src == "exact" and stat_src == "exact" and swap_current is not None and "zswapped" in stats and "swapcached" in stats:
        sample.metrics["swap_disk"] = MetricValue(max(0, swap_current - stats["zswapped"] - stats["swapcached"]), "derived")
    else:
        sample.metrics["swap_disk"] = MetricValue(None, swap_src if swap_src != "exact" else stat_src)
    z_pool, z_eq = sample.metrics["z_pool"].v, sample.metrics["z_eq"].v
    sample.metrics["ratio"] = MetricValue(z_eq / z_pool, "derived") if isinstance(z_pool, int) and isinstance(z_eq, int) and z_pool > 0 else MetricValue(None, "derived")

    for metric, filename in (("mem_min", "memory.min"), ("mem_low", "memory.low"), ("mem_high", "memory.high"), ("mem_max", "memory.max")):
        sample.metrics[metric] = _metric_from_limit(path / filename)
    ram = sample.metrics["ram"].v
    for metric, limit_metric in (("headroom_high_pct", "mem_high"), ("headroom_max_pct", "mem_max")):
        limit = sample.metrics[limit_metric].v
        limit_src = sample.metrics[limit_metric].src
        if isinstance(ram, int) and isinstance(limit, int) and limit > 0:
            sample.metrics[metric] = MetricValue((ram / limit) * 100.0, "derived")
        elif limit_src == "unlimited":
            sample.metrics[metric] = MetricValue(None, "unlimited")
        else:
            sample.metrics[metric] = MetricValue(None, "derived")

    sample.metrics["cpu_weight"] = _metric_from_int(path / "cpu.weight")
    io_weight, io_weight_src = read_io_weight(path / "io.weight")
    sample.metrics["io_weight"] = MetricValue(io_weight, io_weight_src)
    io_max_capped, io_max_src = read_io_max_capped(path / "io.max")
    sample.metrics["io_max_capped"] = MetricValue(io_max_capped, io_max_src)
    quota, period, cpu_max_src = read_cpu_max(path / "cpu.max")
    sample.metrics["cpu_quota_us"] = MetricValue(quota, cpu_max_src)
    sample.metrics["cpu_period_us"] = MetricValue(period, "exact" if period is not None else cpu_max_src)
    sample.metrics["pids_current"] = _metric_from_int(path / "pids.current")
    sample.metrics["pids_max"] = _metric_from_limit(path / "pids.max")
    procs = read_text(path / "cgroup.procs")
    sample.metrics["cgroup_procs"] = MetricValue(None, procs.src) if procs.value is None else MetricValue(len([line for line in str(procs.value).splitlines() if line.strip()]), "exact")

    for prefix, pressure, src in (("psi_mem", memory_pressure, mem_pressure_src), ("psi_io", io_pressure, io_pressure_src), ("psi_cpu", cpu_pressure, cpu_pressure_src)):
        sample.metrics[f"{prefix}_some_avg10"] = _psi_metric(pressure, src, "some")
        sample.metrics[f"{prefix}_full_avg10"] = _psi_metric(pressure, src, "full")

    if stat_src == "exact":
        for raw_name in ("workingset_refault_anon", "workingset_refault_file", "zswpin", "pgscan", "pgsteal", "workingset_restore_anon"):
            if raw_name in stats:
                sample.raw_counters[f"memory.stat:{raw_name}"] = stats[raw_name]
    if events_src == "exact":
        for raw_name in ("low", "high", "max", "oom", "oom_kill"):
            if raw_name in events:
                sample.raw_counters[f"memory.events:{raw_name}"] = events[raw_name]
    if pids_events_src == "exact" and "max" in pids_events:
        sample.raw_counters["pids.events:max"] = pids_events["max"]
    if cpu_stat_src == "exact":
        for raw_name in ("usage_usec", "throttled_usec"):
            if raw_name in cpu_stat:
                sample.raw_counters[f"cpu.stat:{raw_name}"] = cpu_stat[raw_name]
    if io_stat_src == "exact":
        for raw_name in ("rbytes", "wbytes", "rios", "wios", "dbytes"):
            sample.raw_counters[f"io.stat:{raw_name}"] = _sum_io(io_stat, raw_name)
    for name in ("net_rx_bps", "net_tx_bps", "net_rx_pps", "net_tx_pps", "pressure"):
        sample.metrics[name] = MetricValue(None, "unavail_kernel")
    return sample
