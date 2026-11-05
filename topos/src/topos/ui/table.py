"""Generic metric formatting shared by the drill-down; the main view's columns live in ``columns``."""
from __future__ import annotations

from rich.text import Text

from topos.model import Entity, MetricValue

NAME_MAX_WIDTH = 40


def truncate_middle(text: str, limit: int) -> str:
    """Shorten *text* to *limit* characters with ``…`` in the middle (ends stay readable)."""
    if limit < 3 or len(text) <= limit:
        return text
    keep = limit - 1
    head = (keep + 1) // 2
    tail = keep - head
    return f"{text[:head]}…{text[len(text) - tail:]}" if tail else f"{text[:head]}…"


def display_name(entity: Entity) -> str:
    if entity.docker is not None:
        return entity.docker.name
    if not entity.key:
        return "/"
    return entity.key.rsplit("/", 1)[-1]


def _format_metric(metric: MetricValue | None, spec) -> Text:
    if metric is not None and metric.src == "unlimited":
        return Text("max", style="yellow")
    if metric is None or metric.v is None:
        style = "dim" if metric is None or metric.src in {"unavail_perm", "unavail_kernel"} else ""
        return Text("-", style=style)
    value = metric.v
    if spec is None:
        return Text(str(value))
    if spec.unit in {"bytes", "bytes/s"}:
        suffix = "/s" if spec.unit == "bytes/s" else ""
        return Text(f"{_fmt_bytes(float(value))}{suffix}")
    if spec.unit == "%":
        return Text(f"{float(value):.1f}%")
    if spec.unit == "/s":
        return Text(f"{float(value):.1f}/s")
    if spec.unit == "ratio":
        return Text(f"{float(value):.1f}x")
    if metric is not None and spec.name == "damon_sample_age_s":
        return Text(f"{float(value):.1f}s")
    if spec.unit == "us":
        return Text(str(int(value)))
    if isinstance(value, float):
        return Text(f"{value:.1f}")
    return Text(str(value))


def _fmt_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    scaled = value
    unit = units[0]
    for unit in units[:-1]:
        if abs(scaled) < 1024:
            break
        scaled /= 1024.0
    else:
        unit = units[-1]
    if unit == "B":
        return f"{int(scaled)}{unit}"
    return f"{scaled:.1f}{unit}"
