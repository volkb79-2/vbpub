from __future__ import annotations

from pathlib import Path

from groop.collect.cgroup import read_int, read_pressure, read_text
from groop.model import MetricValue

KIB = 1024
PAGE = 4096

SWAP_BACKEND_CODES = {
    "none": 0,
    "zswap_only": 1,
    "zram_only": 2,
    "disk_only": 3,
    "zswap_zram": 4,
    "zswap_disk": 5,
    "mixed": 6,
    "unknown": 7,
}


def _meminfo(proc_root: Path) -> dict[str, int]:
    result = read_text(proc_root / "meminfo")
    if result.value is None:
        return {}
    out: dict[str, int] = {}
    for line in str(result.value).splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            try:
                out[key] = int(parts[0]) * KIB
            except ValueError:
                pass
    return out


def _mem_metric(meminfo: dict[str, int], key: str) -> MetricValue:
    return MetricValue(meminfo[key], "host") if key in meminfo else MetricValue(None, "unavail_kernel")


def _loadavg(proc_root: Path) -> tuple[MetricValue, MetricValue, MetricValue]:
    result = read_text(proc_root / "loadavg")
    if result.value is None:
        missing = MetricValue(None, result.src)
        return missing, missing, missing
    try:
        a, b, c = str(result.value).split()[:3]
        return MetricValue(float(a), "host"), MetricValue(float(b), "host"), MetricValue(float(c), "host")
    except (ValueError, IndexError):
        missing = MetricValue(None, "unavail_kernel")
        return missing, missing, missing


def _uptime(proc_root: Path) -> MetricValue:
    result = read_text(proc_root / "uptime")
    if result.value is None:
        return MetricValue(None, result.src)
    try:
        return MetricValue(float(str(result.value).split()[0]), "host")
    except (ValueError, IndexError):
        return MetricValue(None, "unavail_kernel")


def _psi(proc_root: Path, name: str, section: str) -> MetricValue:
    pressure, src = read_pressure(proc_root / "pressure" / name)
    if src != "exact":
        return MetricValue(None, src)
    value = pressure.get(section, {}).get("avg10")
    return MetricValue(value, "host") if value is not None else MetricValue(None, "unavail_kernel")


def _zswap_param(sys_root: Path, name: str) -> MetricValue:
    result = read_text(sys_root / "module" / "zswap" / "parameters" / name)
    if result.value is None:
        return MetricValue(None, result.src)
    text = str(result.value).strip()
    if text in ("Y", "N"):
        return MetricValue(1 if text == "Y" else 0, "host")
    try:
        return MetricValue(int(text), "host")
    except ValueError:
        return MetricValue(None, "unavail_kernel")


def _debugfs_zswap(sys_root: Path, meminfo: dict[str, int]) -> tuple[MetricValue, MetricValue]:
    pool, pool_src = read_int(sys_root / "kernel" / "debug" / "zswap" / "pool_total_size")
    stored_pages, stored_src = read_int(sys_root / "kernel" / "debug" / "zswap" / "stored_pages")
    if pool_src == "exact" and stored_src == "exact" and pool is not None and stored_pages is not None:
        return MetricValue(pool, "host"), MetricValue(stored_pages * 4096, "host")
    pool_metric = MetricValue(meminfo["Zswap"], "host") if "Zswap" in meminfo else MetricValue(None, pool_src if pool_src != "exact" else "unavail_kernel")
    stored_metric = MetricValue(meminfo["Zswapped"], "host") if "Zswapped" in meminfo else MetricValue(None, stored_src if stored_src != "exact" else "unavail_kernel")
    return pool_metric, stored_metric


def _swap_devices(proc_root: Path) -> tuple[list[dict[str, int | str | bool]], str]:
    result = read_text(proc_root / "swaps")
    if result.value is None:
        return [], result.src
    devices: list[dict[str, int | str | bool]] = []
    for line in str(result.value).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            try:
                used = int(parts[3]) * KIB
                size = int(parts[2]) * KIB
            except ValueError:
                continue
            name = parts[0]
            devices.append({"name": name, "size": size, "used": used, "zram": Path(name).name.startswith("zram")})
    return devices, "host"


def _disk_swap(proc_root: Path, meminfo: dict[str, int], zswap_stored: MetricValue) -> MetricValue:
    devices, src = _swap_devices(proc_root)
    if src != "host":
        return MetricValue(None, src)
    used = sum(int(device["used"]) for device in devices if not bool(device["zram"]))
    zswap_bytes = zswap_stored.v if isinstance(zswap_stored.v, int) else 0
    return MetricValue(max(0, used - meminfo.get("SwapCached", 0) - zswap_bytes), "host")


def _swap_backend_metrics(proc_root: Path, zswap_enabled: MetricValue, zswap_stored: MetricValue) -> dict[str, MetricValue]:
    devices, src = _swap_devices(proc_root)
    if src != "host":
        return {
            "host_swap_backend": MetricValue(SWAP_BACKEND_CODES["unknown"], src),
            "host_zram_swap_devices": MetricValue(None, src),
            "host_disk_swap_devices": MetricValue(None, src),
        }
    zram_count = sum(1 for device in devices if bool(device["zram"]))
    disk_count = sum(1 for device in devices if not bool(device["zram"]))
    zswap_active = zswap_enabled.v == 1 or (isinstance(zswap_stored.v, int) and zswap_stored.v > 0)
    if zram_count and disk_count:
        state = "mixed"
    elif zram_count:
        state = "zswap_zram" if zswap_active else "zram_only"
    elif disk_count:
        state = "zswap_disk" if zswap_active else "disk_only"
    elif zswap_active:
        state = "zswap_only"
    else:
        state = "none"
    return {
        "host_swap_backend": MetricValue(SWAP_BACKEND_CODES[state], "host"),
        "host_zram_swap_devices": MetricValue(zram_count, "host"),
        "host_disk_swap_devices": MetricValue(disk_count, "host"),
    }


def _zram_metrics(sys_root: Path) -> dict[str, MetricValue]:
    totals = {
        "host_zram_orig_bytes": 0,
        "host_zram_compr_bytes": 0,
        "host_zram_mem_used_bytes": 0,
        "host_zram_mem_limit_bytes": 0,
        "host_zram_mem_used_max_bytes": 0,
        "host_zram_same_pages": 0,
        "host_zram_huge_pages": 0,
        "host_zram_failed_reads": 0,
        "host_zram_failed_writes": 0,
        "host_zram_writeback_bytes": 0,
    }
    count = 0
    for device in sorted((sys_root / "block").glob("zram*")):
        if not device.is_dir():
            continue
        count += 1
        mm = _parse_stat_line(device / "mm_stat")
        io = _parse_stat_line(device / "io_stat")
        bd = _parse_stat_line(device / "bd_stat")
        for name, index in (
            ("host_zram_orig_bytes", 0),
            ("host_zram_compr_bytes", 1),
            ("host_zram_mem_used_bytes", 2),
            ("host_zram_mem_limit_bytes", 3),
            ("host_zram_mem_used_max_bytes", 4),
            ("host_zram_same_pages", 5),
            ("host_zram_huge_pages", 7),
        ):
            totals[name] += _stat_value(mm, index)
        totals["host_zram_failed_reads"] += _stat_value(io, 0)
        totals["host_zram_failed_writes"] += _stat_value(io, 1)
        totals["host_zram_writeback_bytes"] += _stat_value(bd, 0) * PAGE
    out = {name: MetricValue(value, "host") for name, value in totals.items()}
    out["host_zram_device_count"] = MetricValue(count, "host")
    compr = totals["host_zram_compr_bytes"]
    mem_used = totals["host_zram_mem_used_bytes"]
    out["host_zram_ratio"] = MetricValue(totals["host_zram_orig_bytes"] / compr, "host") if compr > 0 else MetricValue(None, "host")
    out["host_zram_efficiency"] = MetricValue(compr / mem_used, "host") if mem_used > 0 else MetricValue(None, "host")
    return out


def _parse_stat_line(path: Path) -> tuple[int, ...]:
    result = read_text(path)
    if result.value is None:
        return ()
    out: list[int] = []
    for part in str(result.value).split():
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def _stat_value(values: tuple[int, ...], index: int) -> int:
    return values[index] if index < len(values) else 0


def collect_host(proc_root: Path = Path("/proc"), sys_root: Path = Path("/sys")) -> dict[str, MetricValue]:
    meminfo = _meminfo(proc_root)
    host = {
        "host_mem_total": _mem_metric(meminfo, "MemTotal"),
        "host_mem_available": _mem_metric(meminfo, "MemAvailable"),
        "host_swap_total": _mem_metric(meminfo, "SwapTotal"),
        "host_swap_free": _mem_metric(meminfo, "SwapFree"),
        "host_swapcached": _mem_metric(meminfo, "SwapCached"),
        "host_uptime_s": _uptime(proc_root),
        "host_psi_mem_some_avg10": _psi(proc_root, "memory", "some"),
        "host_psi_mem_full_avg10": _psi(proc_root, "memory", "full"),
        "host_psi_io_some_avg10": _psi(proc_root, "io", "some"),
        "host_psi_io_full_avg10": _psi(proc_root, "io", "full"),
        "host_psi_cpu_some_avg10": _psi(proc_root, "cpu", "some"),
        "host_zswap_enabled": _zswap_param(sys_root, "enabled"),
        "host_zswap_max_pool_percent": _zswap_param(sys_root, "max_pool_percent"),
    }
    host["host_load1"], host["host_load5"], host["host_load15"] = _loadavg(proc_root)
    host["host_zswap_pool"], host["host_zswap_stored"] = _debugfs_zswap(sys_root, meminfo)
    pool, stored = host["host_zswap_pool"].v, host["host_zswap_stored"].v
    host["host_zswap_ratio"] = MetricValue(stored / pool, "host") if isinstance(pool, int) and isinstance(stored, int) and pool > 0 else MetricValue(None, "host")
    host["host_disk_swap"] = _disk_swap(proc_root, meminfo, host["host_zswap_stored"])
    host.update(_swap_backend_metrics(proc_root, host["host_zswap_enabled"], host["host_zswap_stored"]))
    host.update(_zram_metrics(sys_root))
    return host
