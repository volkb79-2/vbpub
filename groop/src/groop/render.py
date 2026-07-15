"""Pure ASCII text/table rendering for P88 query results and reports (P65).

``render_query`` and ``render_report`` each consume ONLY the same canonical
JSONable dict that ``--json`` would print (``Result.to_jsonable()`` /
``report_to_jsonable(...)``). They perform no collection, selection,
aggregation, rounding from raw floats, or file reads: every displayed number
is exactly the value already computed upstream, just formatted for a
terminal. ``--json`` output is untouched by this module.

Closed vocabulary for typed cell/series values (never a blank or a dash, so
a real zero is never confused with "nothing here"):

    missing            — no value was ever collected (``src == "absent"``,
                          or a summary metric with zero samples)
    redacted           — the value carries the daemon/HTTP redaction marker
    warming             — a derived rate/counter with only a raw counter
                          seeded so far (``src == "derived"``, ``value is
                          None``)
    permission-denied  — the collector could not read the underlying file
                          (``src == "unavail_perm"``)
    unlimited           — a real, known-infinite kernel value (``src ==
                          "unlimited"``), rendered distinctly from unavailable
    unsupported         — the running kernel does not expose this stat
                          (``src == "unavail_kernel"``)

Two more spellings live at the result-header level, since they describe the
whole result rather than one cell:

    stale       — appended to the freshness line when ``coverage.complete``
                  is False (a gap or eviction occurred in the window)
    truncated   — the truncation line, driven entirely by ``meta.truncation``

Zero always renders as the literal ``0``.
"""

from __future__ import annotations

from typing import Any

_INDENT = "  "


# ---------------------------------------------------------------------------
# Scalar formatting.
# ---------------------------------------------------------------------------

def _format_number(value: Any) -> str:
    """Format a JSONable scalar without re-rounding it.

    Floats are shown at their full stored precision (values are already
    rounded to 6 decimals upstream) with trailing zeros trimmed, so an
    integral value like ``0.0`` prints as ``0`` rather than ``0.0``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return "nan"
        if value == float("inf"):
            return "inf"
        if value == float("-inf"):
            return "-inf"
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text else "0"
    if value is None:
        return "missing"
    return str(value)


def _format_stat(value: Any) -> str:
    """Format one summary/assertion stat value (may be a nested states dict)."""
    if value is None:
        return "missing"
    if isinstance(value, dict):
        if not value:
            return "missing"
        return ",".join(f"{k}={_format_number(v)}" for k, v in sorted(value.items()))
    return _format_number(value)


def _is_redacted_marker(value: Any) -> bool:
    return isinstance(value, dict) and value.get("redacted") is True


def _format_src_value(value: Any, src: Any) -> str:
    """Format one typed (value, src) pair using the closed vocabulary.

    A cell suppressed under ``--visibility available`` (``hidden`` in the JSON)
    still carries its true ``src``, so it classifies exactly as it would under
    ``--visibility all`` — an ``unavail_kernel`` value reads ``unsupported`` and
    an ``unavail_perm`` value reads ``permission-denied`` regardless of
    visibility, never collapsing those two distinct states together.
    """
    if _is_redacted_marker(value):
        return "redacted"
    if src == "absent":
        return "missing"
    if src == "unavail_perm":
        return "permission-denied"
    if src == "unavail_kernel":
        return "unsupported"
    if src == "unlimited":
        return "unlimited"
    if value is None:
        return "warming" if src == "derived" else "missing"
    return _format_number(value)


# ---------------------------------------------------------------------------
# Table layout — deterministic widths, ASCII only, no trailing whitespace.
# ---------------------------------------------------------------------------

def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        parts = []
        last = len(cells) - 1
        for i, cell in enumerate(cells):
            parts.append(cell if i == last else cell.ljust(widths[i]))
        return "  ".join(parts).rstrip()

    lines = [fmt_row(headers), fmt_row(["-" * w for w in widths])]
    for row in rows:
        lines.append(fmt_row(row))
    return lines


def _fmt_or_none(value: Any, none_str: str = "none") -> str:
    return none_str if value is None else _format_number(value)


def _drop_trailing_blank(lines: list[str]) -> list[str]:
    while lines and lines[-1] == "":
        lines.pop()
    return lines


# ---------------------------------------------------------------------------
# Query result rendering (current / summary / raw shapes).
# ---------------------------------------------------------------------------

def render_query(result: dict[str, Any]) -> str:
    """Render a P88 query ``Result.to_jsonable()`` dict as ASCII text."""
    meta = result["meta"]
    rows = result["rows"]
    lines: list[str] = []

    lines.append(f"shape: {meta['shape']}")
    lines.append(f"projection: {meta['projection']}  visibility: {meta['visibility']}")

    source = meta["source"]
    source_str = source["kind"]
    detail = source.get("detail") or {}
    if detail:
        source_str += " (" + " ".join(f"{k}={v}" for k, v in detail.items()) + ")"
    lines.append(f"source: {source_str}")

    requested = meta["requested_window"]
    requested_str = (
        "all"
        if requested is None
        else f"[{_format_number(requested['start_ts'])}, {_format_number(requested['end_ts'])}]"
    )
    observed_start = meta["observed_start_ts"]
    observed_end = meta["observed_end_ts"]
    observed_str = (
        "none"
        if observed_start is None
        else f"[{_format_number(observed_start)}, {_format_number(observed_end)}]"
    )
    lines.append(
        f"window: requested={requested_str}  observed={observed_str}  samples={meta['sample_count']}"
    )

    coverage = meta["coverage"]
    lines.append(
        f"coverage: frames={coverage['frames']}  span_s={_format_number(coverage['span_s'])}  "
        f"gap_count={coverage['gap_count']}  complete={_format_number(coverage['complete'])}"
    )

    freshness = meta["freshness"]
    stale_suffix = "  (stale)" if not coverage["complete"] else ""
    lines.append(
        f"freshness: newest={_fmt_or_none(freshness['newest_ts'])}  "
        f"oldest={_fmt_or_none(freshness['oldest_ts'])}{stale_suffix}"
    )

    lines.append(
        f"resets: {meta['resets']['count']}  eviction: {_format_number(meta['eviction']['occurred'])}"
    )

    truncation = meta["truncation"]
    if truncation.get("truncated"):
        extra = " ".join(
            f"{k}={v}" for k, v in truncation.items() if k not in ("truncated", "policy")
        )
        lines.append(f"truncation: truncated  policy={truncation['policy']}  {extra}".rstrip())
    else:
        lines.append(f"truncation: none  policy={truncation['policy']}")

    lines.append("")

    if meta["shape"] == "current":
        lines.extend(_render_current_rows(rows))
    elif meta["shape"] == "summary":
        lines.extend(_render_summary_rows(rows))
    else:
        lines.extend(_render_raw_rows(rows))

    return "\n".join(line.rstrip() for line in _drop_trailing_blank(lines))


def _row_key_column(row: dict[str, Any], hierarchy: bool) -> str:
    if hierarchy:
        return (_INDENT * row["depth"]) + row["key"]
    return row["key"]


def _render_current_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["(no rows)"]
    metric_names = list(rows[0]["metrics"].keys())
    hierarchy = "depth" in rows[0]
    has_subtree = hierarchy and "subtree" in rows[0]

    headers = ["KEY"] + [m.upper() for m in metric_names]
    if has_subtree:
        headers.append(f"SUBTREE({rows[0]['subtree']['metric']})")

    table_rows = []
    for row in rows:
        cells = [_row_key_column(row, hierarchy)]
        cells.extend(_format_src_value(cell.get("value"), cell.get("src"))
                     for cell in (row["metrics"][name] for name in metric_names))
        if has_subtree:
            subtree_value = row["subtree"]["value"]
            cells.append("missing" if subtree_value is None else _format_number(subtree_value))
        table_rows.append(cells)
    return _table(headers, table_rows)


def _render_summary_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["(no rows)"]
    metric_names = list(rows[0]["metrics"].keys())
    hierarchy = "depth" in rows[0]
    lines: list[str] = []
    for metric_name in metric_names:
        first_entry = rows[0]["metrics"][metric_name]
        semantic = first_entry.get("semantic", "")
        stat_keys = [k for k in first_entry if k not in ("semantic", "sample_count")]
        headers = ["KEY"] + [k.upper() for k in stat_keys]
        table_rows = []
        for row in rows:
            entry = row["metrics"].get(metric_name, {})
            key_col = _row_key_column(row, hierarchy)
            if entry.get("sample_count", 0) == 0:
                cells = [key_col] + ["missing" for _ in stat_keys]
            else:
                cells = [key_col] + [_format_stat(entry.get(k)) for k in stat_keys]
            table_rows.append(cells)
        lines.append(f"metric: {metric_name} ({semantic})")
        lines.extend(_table(headers, table_rows))
        lines.append("")
    return _drop_trailing_blank(lines)


def _render_raw_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["(no rows)"]
    lines: list[str] = []
    headers = ["TS", "VALUE", "SRC"]
    for row in rows:
        lines.append(f"series: key={row['key']}  metric={row['metric']} ({row['semantic']})")
        table_rows = [
            [_format_number(pt["ts"]), _format_src_value(pt.get("value"), pt.get("src")), pt["src"]]
            for pt in row["points"]
        ]
        lines.extend(_table(headers, table_rows))
        lines.append("")
    return _drop_trailing_blank(lines)


# ---------------------------------------------------------------------------
# Report rendering (P54 profiles + P61 assertions).
# ---------------------------------------------------------------------------

def render_report(report: dict[str, Any]) -> str:
    """Render a ``report_to_jsonable(...)`` dict as ASCII text."""
    lines: list[str] = []

    if report.get("window_mode") == "auto":
        if report.get("window_detected"):
            lines.append(
                "window: auto  detected=true  "
                f"[{_format_number(report['window_start_ts'])}, {_format_number(report['window_end_ts'])}]"
            )
        else:
            lines.append("window: auto  detected=false  (no stable trailing window found)")
        lines.append("")

    profiles = report.get("profiles", [])
    lines.append(f"profiles: {len(profiles)}")
    lines.append("")
    for profile in profiles:
        lines.append(f"group: {profile['key']}  samples={profile['sample_count']}")
        for label, metrics in (("gauges", profile.get("gauges") or {}), ("rates", profile.get("rates") or {})):
            if not metrics:
                continue
            headers = ["METRIC", "P50", "P95", "MAX"]
            table_rows = [
                [name, _format_stat(stats.get("p50")), _format_stat(stats.get("p95")), _format_stat(stats.get("max"))]
                for name, stats in sorted(metrics.items())
            ]
            lines.append(f"  {label}:")
            lines.extend(_INDENT + line for line in _table(headers, table_rows))
        lines.append("")

    assertions = report.get("assertions")
    if assertions is not None:
        lines.append(f"assertions: {len(assertions)}")
        headers = ["GROUP", "METRIC", "STAT", "OP", "THRESHOLD", "ACTUAL", "RESULT", "REASON"]
        table_rows = [
            [
                a["group"],
                a["metric"],
                a["stat"],
                a["op"],
                _format_number(a["threshold"]),
                _format_stat(a["actual"]),
                "PASS" if a["passed"] else "FAIL",
                a.get("reason") or "",
            ]
            for a in assertions
        ]
        lines.extend(_table(headers, table_rows))
        lines.append("")

    return "\n".join(line.rstrip() for line in _drop_trailing_blank(lines))
