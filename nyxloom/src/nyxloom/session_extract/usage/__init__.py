"""Metadata-only Claude Code usage report loader and renderers."""

from __future__ import annotations

from typing import Any

from .anchors import (
    AnchorError,
    add_anchor,
    default_anchor_path,
    list_anchors,
    read_anchor_store,
)
from .calibration import calibrate_anchors
from .charts import (
    CHART_SCHEMA_VERSION,
    DEFAULT_CONTEXT_POINT_LIMIT,
    DEFAULT_TOP_N,
    GROUP_BY_VALUES,
    ChartOptions,
    UsageChartError,
    build_chart_data,
    decimate_context_points,
    pack_lane_intervals,
)
from .html import render_html
from .loader import UsageLoadError, default_projects_root, load_corpus
from .model import Call, Corpus, Node, Session, UsageEvent, Window
from .pdf import UsagePDFError, render_pdf
from .pricing import (
    UsagePricingError,
    apply_valuation,
    attach_price_extension,
    fit_cost_state,
    load_weights,
    prices_summary,
    value_call,
)
from .query import (
    SORT_COLUMNS,
    UsageQueryError,
    build_report,
    parse_share_percent,
    resolve_window,
)
from .render import CSV_COLUMNS, render_csv, render_json, render_table
from .windows import build_usage_windows


def render_report(
    report: dict[str, Any], format: str, *, weights_file: str = "usage-weights.toml",
) -> str | bytes:
    """Dispatch a shared report object to one of the usage-report-v1 renderers."""
    if format == "table":
        return render_table(report)
    if format == "json":
        return render_json(report)
    if format == "csv":
        return render_csv(report)
    if format == "html":
        return render_html(report, weights_file=weights_file)
    if format == "pdf":
        return render_pdf(report, weights_file=weights_file)
    raise ValueError("format must be table, json, csv, html, or pdf")


__all__ = [
    "CHART_SCHEMA_VERSION",
    "CSV_COLUMNS",
    "DEFAULT_CONTEXT_POINT_LIMIT",
    "DEFAULT_TOP_N",
    "GROUP_BY_VALUES",
    "SORT_COLUMNS",
    "AnchorError",
    "Call",
    "ChartOptions",
    "Corpus",
    "Node",
    "Session",
    "UsageChartError",
    "UsageEvent",
    "UsageLoadError",
    "UsagePDFError",
    "UsagePricingError",
    "UsageQueryError",
    "Window",
    "add_anchor",
    "apply_valuation",
    "attach_price_extension",
    "build_chart_data",
    "build_report",
    "build_usage_windows",
    "calibrate_anchors",
    "decimate_context_points",
    "default_anchor_path",
    "default_projects_root",
    "fit_cost_state",
    "list_anchors",
    "load_corpus",
    "load_weights",
    "pack_lane_intervals",
    "parse_share_percent",
    "prices_summary",
    "read_anchor_store",
    "render_csv",
    "render_html",
    "render_json",
    "render_pdf",
    "render_report",
    "render_table",
    "resolve_window",
    "value_call",
]
