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
from .loader import UsageLoadError, default_projects_root, load_corpus
from .model import Call, Corpus, Node, Session, UsageEvent, Window
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


def render_report(report: dict[str, Any], format: str) -> str:
    """Dispatch a shared report object to one of the U1/U2 renderers."""
    if format == "table":
        return render_table(report)
    if format == "json":
        return render_json(report)
    if format == "csv":
        return render_csv(report)
    raise ValueError("format must be table, json, or csv")


__all__ = [
    "CSV_COLUMNS", "SORT_COLUMNS", "AnchorError", "Call", "Corpus", "Node", "UsageEvent",
    "Session", "UsageLoadError", "UsageQueryError", "Window", "build_report",
    "UsagePricingError", "add_anchor", "apply_valuation", "attach_price_extension",
    "build_usage_windows", "calibrate_anchors", "default_anchor_path", "fit_cost_state",
    "list_anchors", "load_weights", "prices_summary", "read_anchor_store", "value_call",
    "default_projects_root", "load_corpus", "parse_share_percent", "render_csv",
    "render_json", "render_report", "render_table", "resolve_window",
]
