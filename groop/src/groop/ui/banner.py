from __future__ import annotations

from dataclasses import dataclass

from groop.config import GroopConfig
from groop.model import EntityFrame, Frame, MetricValue


@dataclass(frozen=True)
class BannerSnapshot:
    verdict: str
    lines: tuple[str, ...]
    unprivileged_count: int


def render_banner(frame: Frame, config: GroopConfig, *, collapsed: bool = False) -> BannerSnapshot:
    verdict = _host_verdict(frame, config)
    notice_count = _count_unavailable_permissions(frame)
    host_line = f"HOST {verdict}"
    if notice_count:
        host_line += f" | running unprivileged - {notice_count} fields unavailable"
    if collapsed:
        return BannerSnapshot(verdict=verdict, lines=(host_line,), unprivileged_count=notice_count)
    lines = [
        host_line,
        (
            f"LOAD {_fmt_metric(frame.host.get('host_load1'))}/{_fmt_metric(frame.host.get('host_load5'))}/{_fmt_metric(frame.host.get('host_load15'))}"
            f" | MEM {_fmt_bytes_metric(frame.host.get('host_mem_available'))} avail / {_fmt_bytes_metric(frame.host.get('host_mem_total'))} total"
            f" | SWAP {_fmt_bytes_metric(frame.host.get('host_swap_free'))} free / {_fmt_bytes_metric(frame.host.get('host_swap_total'))} total"
        ),
        (
            f"PSI mem full {_fmt_metric(frame.host.get('host_psi_mem_full_avg10'), digits=1)} some {_fmt_metric(frame.host.get('host_psi_mem_some_avg10'), digits=1)}"
            f" | io full {_fmt_metric(frame.host.get('host_psi_io_full_avg10'), digits=1)} some {_fmt_metric(frame.host.get('host_psi_io_some_avg10'), digits=1)}"
            f" | cpu some {_fmt_metric(frame.host.get('host_psi_cpu_some_avg10'), digits=1)}"
        ),
        _swap_backend_line(frame),
    ]
    device_lines = _host_device_lines(frame)
    lines.extend(device_lines)
    heat_line = _host_damon_heat_line(frame)
    if heat_line is not None:
        lines.append(heat_line)
    arc_line = _zfs_arc_line(frame)
    if arc_line is not None:
        lines.append(arc_line)
    lines.append("TOP PRESSURE")
    pressure_lines = _top_pressure_lines(frame)
    lines.extend(pressure_lines if pressure_lines else ["n/a"])
    return BannerSnapshot(verdict=verdict, lines=tuple(lines), unprivileged_count=notice_count)


def _host_verdict(frame: Frame, config: GroopConfig) -> str:
    psi_full = config.threshold_band("psi_full_avg10", warn=1.0, crit=2.0)
    psi_some = config.threshold_band("psi_some_avg10", warn=5.0, crit=15.0)
    watched = (
        ("host_psi_mem_full_avg10", psi_full.warn, psi_full.crit),
        ("host_psi_io_full_avg10", psi_full.warn, psi_full.crit),
        ("host_psi_mem_some_avg10", psi_some.warn, psi_some.crit),
        ("host_psi_io_some_avg10", psi_some.warn, psi_some.crit),
        ("host_psi_cpu_some_avg10", psi_some.warn, psi_some.crit),
    )
    verdict = "OK"
    for name, warn, crit in watched:
        value = frame.host.get(name)
        if not isinstance(value, MetricValue) or value.v is None:
            continue
        sample = float(value.v)
        if sample >= crit:
            return "CRIT"
        if sample >= warn:
            verdict = "WARN"
    return verdict


def _top_pressure_lines(frame: Frame) -> list[str]:
    ranked: list[tuple[float, EntityFrame]] = []
    for entity_frame in frame.entities.values():
        metric = entity_frame.metrics.get("pressure")
        if metric is None or metric.v is None:
            continue
        ranked.append((float(metric.v), entity_frame))
    ranked.sort(key=lambda item: item[0], reverse=True)
    lines: list[str] = []
    for index, (_, entity_frame) in enumerate(ranked[:3], start=1):
        metrics = entity_frame.metrics
        lines.append(
            f"{index} {_display_name(entity_frame).ljust(24)[:24]} "
            f"pressure {_fmt_metric(metrics.get('pressure'), digits=1)} "
            f"rf_d/s {_fmt_metric(metrics.get('rf_d_per_s'), digits=1)} "
            f"psi_mem_full {_fmt_metric(metrics.get('psi_mem_full_avg10'), digits=1)} "
            f"ram {_fmt_bytes_metric(metrics.get('ram'))}"
        )
    return lines


def _host_damon_heat_line(frame: Frame) -> str | None:
    if frame.host.get("host_damon_mode") is None or frame.host.get("host_damon_hot_bytes") is None:
        return None
    bytes_by_class = {
        "hot": frame.host.get("host_damon_hot_bytes"),
        "warm": frame.host.get("host_damon_warm_bytes"),
        "cold": frame.host.get("host_damon_cold_bytes"),
        "idle": frame.host.get("host_damon_idle_bytes"),
    }
    if any(metric is None or metric.v is None for metric in bytes_by_class.values()):
        return None
    pct_by_class = {
        "hot": frame.host.get("host_damon_hot_pct"),
        "warm": frame.host.get("host_damon_warm_pct"),
        "cold": frame.host.get("host_damon_cold_pct"),
        "idle": frame.host.get("host_damon_idle_pct"),
    }
    owners = _host_damon_owners(frame)
    owner_text = ",".join(owners) if owners else "unknown"
    return (
        f"DRAM HEAT {_heat_bar(pct_by_class)} "
        f"hot {_fmt_bytes_metric(bytes_by_class['hot'])}/{_fmt_metric(pct_by_class['hot'], digits=1)}% "
        f"warm {_fmt_bytes_metric(bytes_by_class['warm'])}/{_fmt_metric(pct_by_class['warm'], digits=1)}% "
        f"cold {_fmt_bytes_metric(bytes_by_class['cold'])}/{_fmt_metric(pct_by_class['cold'], digits=1)}% "
        f"idle {_fmt_bytes_metric(bytes_by_class['idle'])}/{_fmt_metric(pct_by_class['idle'], digits=1)}% "
        f"age {_fmt_metric(frame.host.get('host_damon_sample_age_s'), digits=1)}s owner {owner_text}"
    )


def _heat_bar(pct_by_class: dict[str, MetricValue | None], *, width: int = 20) -> str:
    chars = {"hot": "H", "warm": "W", "cold": "C", "idle": "I"}
    remaining = width
    parts: list[str] = []
    for name in ("hot", "warm", "cold", "idle"):
        metric = pct_by_class.get(name)
        pct = float(metric.v) if metric is not None and metric.v is not None else 0.0
        count = min(remaining, int(round((pct / 100.0) * width)))
        parts.append(chars[name] * count)
        remaining -= count
    if remaining > 0:
        parts.append("." * remaining)
    return "[" + "".join(parts)[:width].ljust(width, ".") + "]"


def _host_damon_owners(frame: Frame) -> tuple[str, ...]:
    root = frame.entities.get("")
    if root is None or not isinstance(root.damon, dict):
        return ()
    sessions = root.damon.get("host_sessions")
    if not isinstance(sessions, list):
        return ()
    owners = {str(session.get("owner")) for session in sessions if isinstance(session, dict) and session.get("mode") == "paddr" and session.get("owner")}
    return tuple(sorted(owners))


def _zfs_arc_line(frame: Frame) -> str | None:
    arc_size = frame.host.get("host_zfs_arc_size")
    arc_max = frame.host.get("host_zfs_arc_max")
    if arc_size is None or arc_size.v is None or arc_max is None or arc_max.v is None:
        return None
    hit_ratio = frame.host.get("host_zfs_arc_hit_ratio")
    if hit_ratio is not None and hit_ratio.v is not None:
        hit_text = f" (hit {float(hit_ratio.v) * 100:.0f}%)"
    else:
        hit_text = ""
    return f"ARC {_fmt_bytes_metric(arc_size)}/{_fmt_bytes_metric(arc_max)}{hit_text}"


def _swap_backend_line(frame: Frame) -> str:
    zram_devices = frame.host.get("host_zram_swap_devices")
    disk_devices = frame.host.get("host_disk_swap_devices")
    return (
        f"SWAP backend {_swap_backend_label(frame.host.get('host_swap_backend'))} "
        f"zswap {_fmt_bytes_metric(frame.host.get('host_zswap_pool'))}/{_fmt_bytes_metric(frame.host.get('host_zswap_stored'))}"
        f" {_fmt_ratio_metric(frame.host.get('host_zswap_ratio'))} "
        f"zram {_fmt_bytes_metric(frame.host.get('host_zram_orig_bytes'))}/{_fmt_bytes_metric(frame.host.get('host_zram_mem_used_bytes'))}"
        f" {_fmt_ratio_metric(frame.host.get('host_zram_ratio'))} devs {_fmt_metric(zram_devices, digits=0)} "
        f"disk {_fmt_bytes_metric(frame.host.get('host_disk_swap'))} devs {_fmt_metric(disk_devices, digits=0)}"
    )


def _host_device_lines(frame: Frame) -> list[str]:
    """Render NET and DISK device-rate summary lines for the banner.

    Shows the busiest 2-3 devices by total bytes/sec.
    If rates are unavailable (first frame or missing data), renders
    a graceful collecting/unavailable line.
    """
    lines: list[str] = []

    meta = frame.host_meta
    net_devices = _get_device_list(meta, "net_devices")
    block_devices = _get_device_list(meta, "block_devices")

    if net_devices is not None:
        lines.append(_net_device_line(net_devices))
    if block_devices is not None:
        lines.append(_block_device_line(block_devices))

    return lines


def _get_device_list(meta: dict[str, object] | None, key: str) -> list[dict[str, object]] | None:
    """Safely extract a device list from host_meta, returning None if absent."""
    if meta is None:
        return None
    devices = meta.get(key)
    if not isinstance(devices, list):
        return None
    return [device for device in devices if isinstance(device, dict) and "name" in device]


def _net_device_line(devices: list[dict[str, object]]) -> str:
    """Render the NET banner line from net_devices host_meta.

    Shows the 2-3 busiest interfaces by rx_bps + tx_bps.
    Appends drop/error annotations when any interface has non-zero loss.
    """
    # Filter to devices with valid rates
    with_rates = [d for d in devices if d.get("rx_bps") is not None or d.get("tx_bps") is not None]
    if not with_rates:
        return "NET collecting..." if devices else "NET n/a"

    # Sort by total bytes/sec descending, take top 2-3
    sorted_devs = sorted(
        with_rates,
        key=lambda d: (float(d.get("rx_bps", 0) or 0) + float(d.get("tx_bps", 0) or 0)),
        reverse=True,
    )
    top = sorted_devs[:3]
    parts: list[str] = []
    for d in top:
        name = str(d["name"])
        rx_b = float(d.get("rx_bps", 0) or 0)
        tx_b = float(d.get("tx_bps", 0) or 0)
        rx_p = d.get("rx_pps")
        tx_p = d.get("tx_pps")
        dev_str = f"{name} rx{_fmt_bytes(rx_b)}/s tx{_fmt_bytes(tx_b)}/s"
        if rx_p is not None and tx_p is not None:
            dev_str += f" rx{_fmt_float_pps(float(rx_p))}/s tx{_fmt_float_pps(float(tx_p))}/s"
        # Append loss/error info when non-zero
        rx_d = _positive_float(d.get("rx_drops_s"))
        tx_d = _positive_float(d.get("tx_drops_s"))
        rx_e = _positive_float(d.get("rx_errors_s"))
        tx_e = _positive_float(d.get("tx_errors_s"))
        loss_parts: list[str] = []
        if rx_d is not None:
            loss_parts.append(f"rx_drop{_fmt_loss_rate(rx_d)}/s")
        if tx_d is not None:
            loss_parts.append(f"tx_drop{_fmt_loss_rate(tx_d)}/s")
        if rx_e is not None:
            loss_parts.append(f"rx_err{_fmt_loss_rate(rx_e)}/s")
        if tx_e is not None:
            loss_parts.append(f"tx_err{_fmt_loss_rate(tx_e)}/s")
        if loss_parts:
            dev_str += " LOSS " + " ".join(loss_parts)
        parts.append(dev_str)
    return f"NET {' | '.join(parts)}"


def _block_device_line(devices: list[dict[str, object]]) -> str:
    """Render the DISK banner line from block_devices host_meta.

    Shows the 2-3 busiest devices by read_bps + write_bps.
    """
    with_rates = [d for d in devices if d.get("read_bps") is not None or d.get("write_bps") is not None]
    if not with_rates:
        return "DISK collecting..." if devices else "DISK n/a"

    sorted_devs = sorted(
        with_rates,
        key=lambda d: (float(d.get("read_bps", 0) or 0) + float(d.get("write_bps", 0) or 0)),
        reverse=True,
    )
    top = sorted_devs[:3]
    parts: list[str] = []
    for d in top:
        name = str(d["name"])
        r_b = float(d.get("read_bps", 0) or 0)
        w_b = float(d.get("write_bps", 0) or 0)
        r_i = d.get("read_iops")
        w_i = d.get("write_iops")
        dev_str = f"{name} rd{_fmt_bytes(r_b)}/s wr{_fmt_bytes(w_b)}/s"
        if r_i is not None and w_i is not None:
            dev_str += f" rd{_fmt_float_pps(float(r_i))}/s wr{_fmt_float_pps(float(w_i))}/s"
        parts.append(dev_str)
    return f"DISK {' | '.join(parts)}"


def _fmt_float_pps(value: float) -> str:
    """Format packets/ops per second with appropriate unit."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    if value >= 1:
        return f"{value:.1f}"
    return "0"


def _fmt_loss_rate(value: float) -> str:
    """Format a loss/error rate (typically <1 so show as-is with 2 decimal places)."""
    if value >= 100:
        return f"{value:.0f}"
    if value >= 1:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _positive_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _swap_backend_label(metric: MetricValue | None) -> str:
    labels = {
        0: "none",
        1: "zswap",
        2: "zram",
        3: "disk",
        4: "zswap+zram",
        5: "zswap+disk",
        6: "mixed",
        7: "?",
    }
    if metric is None or metric.v is None:
        return "?"
    try:
        return labels[int(metric.v)]
    except (TypeError, ValueError, KeyError):
        return "?"


def _display_name(entity_frame: EntityFrame) -> str:
    entity = entity_frame.entity
    if entity.docker is not None:
        return entity.docker.name
    return entity.key.rsplit("/", 1)[-1] if entity.key else "/"


def _count_unavailable_permissions(frame: Frame) -> int:
    total = sum(1 for metric in frame.host.values() if metric.src == "unavail_perm")
    for entity_frame in frame.entities.values():
        total += sum(1 for metric in entity_frame.metrics.values() if metric.src == "unavail_perm")
    return total


def _fmt_metric(metric: MetricValue | None, *, digits: int = 2) -> str:
    if metric is None or metric.v is None:
        return _fmt_missing(metric)
    if isinstance(metric.v, int):
        return str(metric.v)
    return f"{metric.v:.{digits}f}"


def _fmt_ratio_metric(metric: MetricValue | None) -> str:
    if metric is None or metric.v is None:
        return _fmt_missing(metric)
    return f"{float(metric.v):.1f}x"


def _fmt_bytes_metric(metric: MetricValue | None) -> str:
    if metric is None or metric.v is None:
        return _fmt_missing(metric)
    return _fmt_bytes(float(metric.v))


def _fmt_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    scaled = value
    unit = units[0]
    for unit in units:
        if abs(scaled) < 1024 or unit == units[-1]:
            break
        scaled /= 1024.0
    if unit == "B":
        return f"{int(scaled)}{unit}"
    return f"{scaled:.1f}{unit}"


def _fmt_missing(metric: MetricValue | None) -> str:
    if metric is not None and metric.src == "unavail_perm":
        return "[dim]-[/]"
    return "-"
