from __future__ import annotations

from pathlib import Path
import re

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


def _zfs_arc_metrics(proc_root: Path) -> dict[str, MetricValue]:
    path = proc_root / "spl" / "kstat" / "zfs" / "arcstats"
    result = read_text(path)
    if result.value is None:
        unavail = MetricValue(None, result.src)
        return {
            "host_zfs_arc_size": unavail,
            "host_zfs_arc_target": unavail,
            "host_zfs_arc_max": unavail,
            "host_zfs_arc_min": unavail,
            "host_zfs_arc_hit_ratio": MetricValue(None, result.src),
        }
    kstat = _parse_arcstats(str(result.value))
    if kstat is None:
        unavail = MetricValue(None, "unavail_kernel")
        return {
            "host_zfs_arc_size": unavail,
            "host_zfs_arc_target": unavail,
            "host_zfs_arc_max": unavail,
            "host_zfs_arc_min": unavail,
            "host_zfs_arc_hit_ratio": MetricValue(None, "unavail_kernel"),
        }

    def _gauge(field: str) -> MetricValue:
        v = kstat.get(field)
        if v is None or not isinstance(v, int):
            return MetricValue(None, "unavail_kernel")
        return MetricValue(v, "host")

    size = _gauge("size")
    target = _gauge("c")
    max_ = _gauge("c_max")
    min_ = _gauge("c_min")

    hits_raw = kstat.get("hits")
    misses_raw = kstat.get("misses")
    if not isinstance(hits_raw, int) or not isinstance(misses_raw, int):
        hit_ratio = MetricValue(None, "unavail_kernel", None)
    else:
        # A ratio needs two samples, so a single read can never produce one.
        # The Collector derives it from the raw counters it carries in
        # host_meta["zfs_arc"], using the same per-instance delta/reset
        # machinery as every other counter (Collector._apply_zfs_arc_rate).
        hit_ratio = MetricValue(None, "derived", hits_raw)

    return {
        "host_zfs_arc_size": size,
        "host_zfs_arc_target": target,
        "host_zfs_arc_max": max_,
        "host_zfs_arc_min": min_,
        "host_zfs_arc_hit_ratio": hit_ratio,
    }


def _zfs_arc_detail(proc_root: Path) -> dict[str, object] | None:
    path = proc_root / "spl" / "kstat" / "zfs" / "arcstats"
    result = read_text(path)
    if result.value is None:
        return None
    kstat = _parse_arcstats(str(result.value))
    if kstat is None:
        return None
    return {k: v for k, v in kstat.items() if isinstance(v, int)}


def _parse_arcstats(text: str) -> dict[str, object] | None:
    out: dict[str, object] = {}
    ok = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            return None
        name = parts[0]
        dtype = parts[1]
        data = parts[2]
        try:
            # kstat types 4 (uint64) and 8 (hrtime) carry a numeric data column;
            # anything else (the file's own header rows included) stays a string.
            out[name] = int(data) if dtype in ("4", "8") else data
        except (ValueError, TypeError):
            return None
        if name in ("size", "c", "c_max", "c_min", "hits", "misses"):
            ok = True
    if not ok:
        return None
    return out


_CARD_RE = re.compile(r"^card\d+$")


def _read_sysfs_int(sys_root: Path, *parts: str) -> int | None:
    """Read an integer from a sysfs file. Returns None on any failure."""
    result = read_text(sys_root / "class" / "drm" / Path(*parts))
    if result.value is None:
        return None
    try:
        return int(str(result.value).strip())
    except (ValueError, TypeError):
        return None


def _gpu_metrics(sys_root: Path) -> dict[str, MetricValue]:
    """Read GPU metrics from /sys/class/drm/card*/device/.

    amdgpu exposes mem_info_vram_total, mem_info_vram_used, and
    gpu_busy_percent. Other drivers (i915, nvidia) expose none of these.
    Multi-GPU sums VRAM, takes max busy percent, counts cards.

    Returns host_gpu_vram_total, host_gpu_vram_used, host_gpu_busy_pct,
    host_gpu_count -- all unavail_kernel when no DRM cards are readable.

    An absent /sys/class/drm means the count is unreadable (unavail_kernel),
    not zero: a kernel without the DRM sysfs tree is not the same host as one
    that exposes the tree with no cards in it, and only the latter is a
    measurement of "zero GPUs".
    """
    drm_dir = sys_root / "class" / "drm"
    unavail = MetricValue(None, "unavail_kernel")
    if not drm_dir.is_dir():
        return {
            "host_gpu_vram_total": unavail,
            "host_gpu_vram_used": unavail,
            "host_gpu_busy_pct": unavail,
            "host_gpu_count": unavail,
        }

    cards: list[str] = []
    for entry in sorted(drm_dir.iterdir()):
        if entry.is_dir() and _CARD_RE.match(entry.name):
            cards.append(entry.name)

    if not cards:
        return {
            "host_gpu_vram_total": unavail,
            "host_gpu_vram_used": unavail,
            "host_gpu_busy_pct": unavail,
            "host_gpu_count": MetricValue(0, "host"),
        }

    total_vram: int = 0
    total_used: int = 0
    max_busy: float | None = None
    vram_readable = False
    used_readable = False
    busy_readable = False

    for name in cards:
        device_dir = drm_dir / name / "device"
        if not device_dir.is_dir():
            continue

        vram_total = _read_sysfs_int(sys_root, name, "device", "mem_info_vram_total")
        vram_used = _read_sysfs_int(sys_root, name, "device", "mem_info_vram_used")
        busy_pct = _read_sysfs_int(sys_root, name, "device", "gpu_busy_percent")

        if vram_total is not None:
            total_vram += vram_total
            vram_readable = True
        if vram_used is not None:
            total_used += vram_used
            used_readable = True
        if busy_pct is not None:
            max_busy = max(max_busy or 0, float(busy_pct))
            busy_readable = True

    if not vram_readable and not used_readable and not busy_readable:
        # Cards exist but none expose DRM facts -- driver without the files.
        return {
            "host_gpu_vram_total": MetricValue(None, "unavail_kernel"),
            "host_gpu_vram_used": MetricValue(None, "unavail_kernel"),
            "host_gpu_busy_pct": MetricValue(None, "unavail_kernel"),
            "host_gpu_count": MetricValue(len(cards), "host"),
        }

    return {
        "host_gpu_vram_total": MetricValue(total_vram, "host") if vram_readable else MetricValue(None, "unavail_kernel"),
        "host_gpu_vram_used": MetricValue(total_used, "host") if used_readable else MetricValue(None, "unavail_kernel"),
        "host_gpu_busy_pct": MetricValue(max_busy, "host") if busy_readable else MetricValue(None, "unavail_kernel"),
        "host_gpu_count": MetricValue(len(cards), "host"),
    }


def _gpu_detail(sys_root: Path) -> dict[str, object] | None:
    """Collect per-card GPU details for host_meta.

    Returns a dict keyed by card name with per-card vram_total, vram_used,
    and busy_pct where available. Returns None when no DRM cards exist.
    """
    drm_dir = sys_root / "class" / "drm"
    if not drm_dir.is_dir():
        return None

    cards: list[str] = []
    for entry in sorted(drm_dir.iterdir()):
        if entry.is_dir() and _CARD_RE.match(entry.name):
            cards.append(entry.name)

    if not cards:
        return None

    details: dict[str, object] = {}
    for name in cards:
        card_info: dict[str, object] = {}
        vram_total = _read_sysfs_int(sys_root, name, "device", "mem_info_vram_total")
        vram_used = _read_sysfs_int(sys_root, name, "device", "mem_info_vram_used")
        busy_pct = _read_sysfs_int(sys_root, name, "device", "gpu_busy_percent")
        if vram_total is not None:
            card_info["vram_total"] = vram_total
        if vram_used is not None:
            card_info["vram_used"] = vram_used
        if busy_pct is not None:
            card_info["busy_pct"] = busy_pct
        if card_info:
            details[name] = card_info
    return details if details else None


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
    host.update(_zfs_arc_metrics(proc_root))
    host.update(_gpu_metrics(sys_root))
    return host


def _zram_device_details(sys_root: Path) -> list[dict[str, object]]:
    """Collect per-device zram details for host_meta."""
    devices: list[dict[str, object]] = []
    for device in sorted((sys_root / "block").glob("zram*")):
        if not device.is_dir():
            continue
        name = device.name
        mm = _parse_stat_line(device / "mm_stat")
        io = _parse_stat_line(device / "io_stat")
        bd = _parse_stat_line(device / "bd_stat")
        orig = _stat_value(mm, 0)
        compr = _stat_value(mm, 1)
        mem_used = _stat_value(mm, 2)
        mem_limit = _stat_value(mm, 3)
        mem_used_max = _stat_value(mm, 4)
        same_pages = _stat_value(mm, 5)
        huge_pages = _stat_value(mm, 7)
        failed_reads = _stat_value(io, 0)
        failed_writes = _stat_value(io, 1)
        writeback_bytes = _stat_value(bd, 0) * PAGE
        ratio = orig / compr if compr > 0 else None
        efficiency = compr / mem_used if mem_used > 0 else None
        devices.append({
            "name": name,
            "orig_bytes": orig,
            "compr_bytes": compr,
            "mem_used_bytes": mem_used,
            "mem_limit_bytes": mem_limit,
            "mem_used_max_bytes": mem_used_max,
            "same_pages": same_pages,
            "huge_pages": huge_pages,
            "failed_reads": failed_reads,
            "failed_writes": failed_writes,
            "writeback_bytes": writeback_bytes,
            "ratio": ratio,
            "efficiency": efficiency,
        })
    return devices


# Default device exclusion glob prefixes for host_meta device collection.
# Matches the spec §3.0 [banner] net_device_exclude / disk_device_exclude defaults.
_NET_DEVICE_EXCLUDE_PREFIXES = ("veth", "br-", "docker", "lo")
_BLOCK_DEVICE_EXCLUDE_PREFIXES = ("loop", "ram", "zram")


def _net_dev_counters(proc_root: Path) -> list[dict[str, object]]:
    """Parse /proc/net/dev for per-interface byte/packet/error/drop counters.

    Returns a list of dicts with name, rx_bytes/tx_bytes, rx_packets/tx_packets,
    rx_errors/tx_errors, and rx_drops/tx_drops.
    Excludes interfaces matching _NET_DEVICE_EXCLUDE_PREFIXES (veth*, br-*, docker*, lo).
    Returns empty list on unreadable /proc/net/dev.
    """
    result = read_text(proc_root / "net" / "dev")
    if result.value is None:
        return []
    devices: list[dict[str, object]] = []
    lines = str(result.value).splitlines()
    # Skip first two header lines: "Inter-|   Receive ..." and " face |bytes ..."
    for line in lines[2:]:
        if ":" not in line:
            continue
        name_part, _, rest = line.partition(":")
        name = name_part.strip()
        if any(name.startswith(p) for p in _NET_DEVICE_EXCLUDE_PREFIXES):
            continue
        parts = rest.split()
        if len(parts) < 16:
            continue
        try:
            rx_bytes = int(parts[0])
            rx_packets = int(parts[1])
            rx_errors = int(parts[2])
            rx_drop = int(parts[3])
            tx_bytes = int(parts[8])
            tx_packets = int(parts[9])
            tx_errors = int(parts[10])
            tx_drop = int(parts[11])
        except (ValueError, IndexError):
            continue
        devices.append({
            "name": name,
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "rx_packets": rx_packets,
            "tx_packets": tx_packets,
            "rx_errors": rx_errors,
            "rx_drop": rx_drop,
            "tx_errors": tx_errors,
            "tx_drop": tx_drop,
            "src": "host",
        })
    return devices


def _block_dev_counters(sys_root: Path) -> list[dict[str, object]]:
    """Parse /sys/block/*/stat for per-device I/O counters.

    Returns a list of dicts with name, rd_sectors, wr_sectors, rd_ios, wr_ios.
    Excludes devices matching _BLOCK_DEVICE_EXCLUDE_PREFIXES (loop*, ram*, zram*).
    Returns empty list on unreadable /sys/block or stat files.
    """
    block_dir = sys_root / "block"
    if not block_dir.is_dir():
        return []
    devices: list[dict[str, object]] = []
    for device in sorted(block_dir.iterdir()):
        if not device.is_dir():
            continue
        name = device.name
        if any(name.startswith(p) for p in _BLOCK_DEVICE_EXCLUDE_PREFIXES):
            continue
        stat = _parse_stat_line(device / "stat")
        if len(stat) < 7:
            continue
        devices.append({
            "name": name,
            "rd_ios": stat[0],
            "rd_sectors": stat[2],
            "wr_ios": stat[4],
            "wr_sectors": stat[6],
            "src": "host",
        })
    return devices


def collect_host_meta(proc_root: Path = Path("/proc"), sys_root: Path = Path("/sys")) -> dict[str, object]:
    """Collect host-level non-metric metadata for the Frame.

    Includes zram device details, raw net device counters, raw block device
    counters, and ZFS ARC kstat detail. The Collector computes rates from the
    raw counters.
    """
    meta: dict[str, object] = {
        "zram_devices": _zram_device_details(sys_root),
        "net_device_counters": _net_dev_counters(proc_root),
        "block_device_counters": _block_dev_counters(sys_root),
    }
    arc_detail = _zfs_arc_detail(proc_root)
    if arc_detail is not None:
        meta["zfs_arc"] = arc_detail
    gpu_detail = _gpu_detail(sys_root)
    if gpu_detail is not None:
        meta["gpu"] = gpu_detail
    return meta
