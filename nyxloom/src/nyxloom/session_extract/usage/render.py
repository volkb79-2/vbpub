"""Stdlib table, JSON, and CSV renderers for the shared usage tree report."""

from __future__ import annotations

import csv
import io
import json
from typing import Any


CSV_COLUMNS = (
    "node_id", "parent_id", "depth", "kind", "session_id", "agent_id",
    "topic", "agent_type", "project", "models", "efforts", "start", "end",
    "duration_seconds", "calls", "peak_context", "peak_context_at", "input_tokens",
    "cache_read_tokens", "output_tokens", "cache_write_5m_tokens",
    "cache_write_1h_tokens", "cache_write_unclassified_tokens",
    "thinking_tokens", "raw_token_units", "share_of_total", "weighted_units",
    "usd", "self_start", "self_end", "self_duration_seconds", "self_calls",
    "self_peak_context", "self_peak_context_at", "self_input_tokens",
    "self_cache_read_tokens", "self_output_tokens", "self_cache_write_5m_tokens",
    "self_cache_write_1h_tokens", "self_cache_write_unclassified_tokens",
    "self_thinking_tokens", "self_raw_token_units", "self_share_of_total",
)


def render_json(report: dict[str, Any]) -> str:
    """Serialize the shared, versioned report contract as JSON."""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _walk(nodes: list[dict[str, Any]]):
    for node in nodes:
        yield node
        yield from _walk(node["children"])


def _sorted_flat_nodes(report: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = list(_walk(report["nodes"]))
    column = report["presentation"]["sort"]
    if column == "start":
        return sorted(nodes, key=lambda node: (node["start"] is None, node["start"] or "", node["node_id"]))
    metric = {
        "cost": "raw_token_units",
        "dur": "duration_seconds",
        "calls": "calls",
        "peak": "peak_context",
        "out": "output_tokens",
    }.get(column)
    if metric is None:
        return nodes
    def value(node: dict[str, Any]) -> int:
        if metric == "output_tokens":
            return int(node["tokens"]["output"])
        return int(node[metric])
    return sorted(nodes, key=lambda node: (-value(node), node["node_id"]))


def _label(node: dict[str, Any]) -> str:
    topic = node.get("topic")
    if isinstance(topic, str) and topic:
        return topic
    if node["kind"] == "main":
        return f"session {node['session_id']}"
    if node["kind"] == "unattributed":
        return "(unattributed)"
    if node["kind"] == "other":
        return f"other ({node['omitted_descendants']} agents)"
    if node.get("agent_id"):
        return f"agent {node['agent_id']}"
    return node["kind"]


def _compact_duration(seconds: int) -> str:
    remaining = max(0, seconds)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    if not parts or seconds:
        parts.append(f"{seconds}s")
    return "".join(parts)


def _short_time(value: str | None) -> str:
    if value is None:
        return "—"
    return value.replace("T", " ").removesuffix("Z")


def _model_text(aggregate: dict[str, Any]) -> str:
    models = aggregate["models"]
    if not models:
        return "—"
    return ", ".join(
        f"{model} ({count})"
        for model, count in sorted(models.items(), key=lambda item: (-item[1], item[0]))
    )


def _effort_text(aggregate: dict[str, Any]) -> str:
    efforts = aggregate["efforts"]
    if not efforts:
        return "—"
    return ", ".join(
        f"{effort} ({count})"
        for effort, count in sorted(efforts.items(), key=lambda item: (-item[1], item[0]))
    )


def _share_text(value: float) -> str:
    return f"{100 * value:.1f}%"


def _table_values(label: str, aggregate: dict[str, Any], share: float) -> list[str]:
    tokens = aggregate["tokens"]
    return [
        label,
        _model_text(aggregate),
        _effort_text(aggregate),
        _short_time(aggregate["start"]),
        _short_time(aggregate["end"]),
        _compact_duration(aggregate["duration_seconds"]),
        f"{aggregate['calls']:,}",
        f"{aggregate['peak_context']:,}",
        f"{tokens['input']:,}",
        f"{tokens['cache_read']:,}",
        f"{tokens['output']:,}",
        f"{tokens['cache_write_5m']:,}",
        f"{tokens['cache_write_1h']:,}",
        f"{tokens['cache_write_unclassified']:,}",
        f"{aggregate['raw_token_units']:,}",
        _share_text(share),
    ]


_HEADERS = (
    "NODE / TOPIC", "MODEL(S)", "EFFORT", "START UTC", "END UTC", "DUR",
    "CALLS", "PEAK CTX", "IN", "CACHE READ", "OUT", "WRITE 5M",
    "WRITE 1H", "WRITE ?", "RAW TOKENS", "SHARE",
)


def render_table(report: dict[str, Any]) -> str:
    """Render an expanded text tree, with self and subtree rows."""
    lines = [
        "nyxloom usage report",
        f"Window: {report['window']['since']} through {report['window']['until']} (inclusive, UTC)",
        "Basis: raw tokens; share and cost sort use raw token units, not weighted cost.",
        "",
    ]
    rows: list[list[str]] = []
    flat = bool(report["presentation"]["flat"])
    if flat:
        for node in _sorted_flat_nodes(report):
            aggregate = node["subtree"]
            rows.append(_table_values(_label(node), aggregate, aggregate["share_of_total"]))
    else:
        def append_node(node: dict[str, Any], depth: int) -> None:
            prefix = "  " * depth
            rows.append(_table_values(prefix + _label(node), node["self"], node["self"]["share_of_total"]))
            self_aggregate = node["self"]
            subtree = node["subtree"]
            if (
                subtree["calls"] != self_aggregate["calls"]
                or subtree["raw_token_units"] != self_aggregate["raw_token_units"]
                or node["omitted_descendants"]
            ):
                rows.append(_table_values(
                    prefix + "  (subtree)", subtree, subtree["share_of_total"],
                ))
            for child in node["children"]:
                append_node(child, depth + 1)

        for node in report["nodes"]:
            append_node(node, 0)
    matrix = [list(_HEADERS), *rows]
    widths = [max(len(row[index]) for row in matrix) for index in range(len(_HEADERS))]
    for row in matrix:
        lines.append("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip())
    total = report["totals"]
    lines.extend((
        "",
        f"Total: {total['calls']:,} calls, {total['raw_token_units']:,} raw token units; "
        f"in={total['tokens']['input']:,}, cache-read={total['tokens']['cache_read']:,}, "
        f"out={total['tokens']['output']:,}, cache-write-5m={total['tokens']['cache_write_5m']:,}, "
        f"cache-write-1h={total['tokens']['cache_write_1h']:,}, "
        f"unclassified-cache-write={total['tokens']['cache_write_unclassified']:,}.",
        "Weighted cost and USD are not computed in U1–U2.",
    ))
    coverage = report["coverage"]
    lines.append(
        f"Coverage: {coverage['scanned_files']} files scanned; "
        f"{coverage['pruned_files']} files omitted by mtime; "
        f"{coverage['pruned_sessions']} session families fully pruned."
    )
    nonzero = [f"{key}={value}" for key, value in coverage["warnings"].items() if value]
    if nonzero:
        lines.append("Coverage warnings: " + ", ".join(nonzero))
    return "\n".join(lines) + "\n"


def _csv_node(node: dict[str, Any]) -> dict[str, Any]:
    tokens = node["tokens"]
    self_value = node["self"]
    self_tokens = self_value["tokens"]
    return {
        "node_id": node["node_id"],
        "parent_id": node["parent_id"] or "",
        "depth": node["depth"],
        "kind": node["kind"],
        "session_id": node["session_id"],
        "agent_id": node["agent_id"] or "",
        "topic": node["topic"] or "",
        "agent_type": node["agent_type"] or "",
        "project": node["project"] or "",
        "models": json.dumps(node["models"], ensure_ascii=False, sort_keys=True),
        "efforts": json.dumps(node["efforts"], ensure_ascii=False, sort_keys=True),
        "start": node["start"] or "",
        "end": node["end"] or "",
        "duration_seconds": node["duration_seconds"],
        "calls": node["calls"],
        "peak_context": node["peak_context"],
        "peak_context_at": node["peak_context_at"] or "",
        "input_tokens": tokens["input"],
        "cache_read_tokens": tokens["cache_read"],
        "output_tokens": tokens["output"],
        "cache_write_5m_tokens": tokens["cache_write_5m"],
        "cache_write_1h_tokens": tokens["cache_write_1h"],
        "cache_write_unclassified_tokens": tokens["cache_write_unclassified"],
        "thinking_tokens": tokens["thinking"],
        "raw_token_units": node["raw_token_units"],
        "share_of_total": node["share_of_total"],
        "weighted_units": "",
        "usd": "",
        "self_start": self_value["start"] or "",
        "self_end": self_value["end"] or "",
        "self_duration_seconds": self_value["duration_seconds"],
        "self_calls": self_value["calls"],
        "self_peak_context": self_value["peak_context"],
        "self_peak_context_at": self_value["peak_context_at"] or "",
        "self_input_tokens": self_tokens["input"],
        "self_cache_read_tokens": self_tokens["cache_read"],
        "self_output_tokens": self_tokens["output"],
        "self_cache_write_5m_tokens": self_tokens["cache_write_5m"],
        "self_cache_write_1h_tokens": self_tokens["cache_write_1h"],
        "self_cache_write_unclassified_tokens": self_tokens["cache_write_unclassified"],
        "self_thinking_tokens": self_tokens["thinking"],
        "self_raw_token_units": self_value["raw_token_units"],
        "self_share_of_total": self_value["share_of_total"],
    }


def render_csv(report: dict[str, Any]) -> str:
    """Render one flat row per visible tree node."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    nodes = (
        _sorted_flat_nodes(report)
        if report["presentation"]["flat"]
        else list(_walk(report["nodes"]))
    )
    for node in nodes:
        writer.writerow(_csv_node(node))
    return output.getvalue()
