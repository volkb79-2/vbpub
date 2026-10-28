"""Metadata-only Claude Code usage report loader and renderers."""

from __future__ import annotations

from typing import Any

from .loader import UsageLoadError, default_projects_root, load_corpus
from .model import Call, Compaction, Corpus, Node, Session, Window
from .query import (
    SORT_COLUMNS,
    UsageQueryError,
    build_report,
    parse_share_percent,
    resolve_window,
)
from .render import CSV_COLUMNS, render_csv, render_json, render_table


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
    "CSV_COLUMNS", "SORT_COLUMNS", "Call", "Compaction", "Corpus", "Node",
    "Session", "UsageLoadError", "UsageQueryError", "Window", "build_report",
    "default_projects_root", "load_corpus", "parse_share_percent", "render_csv",
    "render_json", "render_report", "render_table", "resolve_window",
]
