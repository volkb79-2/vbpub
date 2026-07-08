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
        (
            f"ZSWAP {_fmt_bytes_metric(frame.host.get('host_zswap_pool'))} pool / {_fmt_bytes_metric(frame.host.get('host_zswap_stored'))} stored"
            f" / {_fmt_ratio_metric(frame.host.get('host_zswap_ratio'))} / disk swap {_fmt_bytes_metric(frame.host.get('host_disk_swap'))}"
        ),
        "TOP PRESSURE",
    ]
    pressure_lines = _top_pressure_lines(frame)
    lines.extend(pressure_lines if pressure_lines else ["n/a"])
    return BannerSnapshot(verdict=verdict, lines=tuple(lines), unprivileged_count=notice_count)


def _host_verdict(frame: Frame, config: GroopConfig) -> str:
    psi_full_warn, psi_full_crit = _threshold_pair(config, "psi_full_avg10", warn=1.0, crit=2.0)
    psi_some_warn, psi_some_crit = _threshold_pair(config, "psi_some_avg10", warn=5.0, crit=15.0)
    watched = (
        ("host_psi_mem_full_avg10", psi_full_warn, psi_full_crit),
        ("host_psi_io_full_avg10", psi_full_warn, psi_full_crit),
        ("host_psi_mem_some_avg10", psi_some_warn, psi_some_crit),
        ("host_psi_io_some_avg10", psi_some_warn, psi_some_crit),
        ("host_psi_cpu_some_avg10", psi_some_warn, psi_some_crit),
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


def _threshold_pair(config: GroopConfig, key: str, *, warn: float, crit: float) -> tuple[float, float]:
    raw = config.thresholds.get("default", {}).get(key, {})
    try:
        warn_value = float(raw.get("warn", warn))
    except (TypeError, ValueError, AttributeError):
        warn_value = warn
    try:
        crit_value = float(raw.get("crit", crit))
    except (TypeError, ValueError, AttributeError):
        crit_value = crit
    return warn_value, crit_value


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
