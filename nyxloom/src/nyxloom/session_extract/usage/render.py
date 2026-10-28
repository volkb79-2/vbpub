"""Stdlib renderers for the shared usage-report-v1 result."""

from __future__ import annotations

import csv
import io
import json
from typing import Any


CSV_COLUMNS = (
    "record_type", "schema_version", "report", "window_since", "window_until",
    "call_id", "node_id", "parent_id", "depth", "lineage_status", "session_id",
    "agent_id", "topic", "agent_type", "project_id", "request_id", "request_ids", "message_id",
    "timestamp", "model", "effort", "service_tier", "speed", "inference_geo",
    "context_size", "sidechain", "has_fork_source", "source_id", "source_ids",
    "source_line", "provenance", "stream_block_count", "copied_source_count",
    "input_tokens", "cache_read_tokens", "cache_write_5m_tokens",
    "cache_write_1h_tokens", "cache_write_unclassified_tokens", "output_tokens",
    "thinking_tokens", "raw_token_units", "weighted_units", "api_equivalent_usd",
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
        _compact_duration(aggregate["wall_duration_seconds"]),
        _compact_duration(aggregate["active_span_seconds"]),
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
    "NODE / TOPIC", "MODEL(S)", "EFFORT", "START UTC", "END UTC", "WALL DUR",
    "ACTIVE SPAN", "CALLS", "PEAK CTX", "IN", "CACHE READ", "OUT", "WRITE 5M",
    "WRITE 1H", "WRITE ?", "RAW TOKENS", "RAW SHARE",
)


def render_table(report: dict[str, Any]) -> str:
    """Render the expanded tree, with self and subtree rows."""
    lines = [
        "nyxloom usage report",
        f"Schema: {report['schema_version']}",
        f"Window: [{report['window']['since']}, {report['window']['until']}) UTC",
        "Basis: raw token units; shares are local report fractions, not account-limit percentages.",
        "Wall duration spans first-to-last call; active span sums within-node bursts separated by gaps over one hour.",
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
                rows.append(_table_values(prefix + "  (subtree)", subtree, subtree["share_of_total"]))
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
        "Weighted cost, API-equivalent USD, and limit percentages are unavailable in usage-report-v1.",
    ))
    coverage = report["coverage"]
    lines.append(
        f"Coverage: {coverage['scanned_files']} transcript files scanned; "
        f"{'partial' if coverage['partial'] else 'complete within the scanned snapshot'}."
    )
    nonzero = [f"{key}={value}" for key, value in coverage["warnings"].items() if value]
    if nonzero:
        lines.append("Coverage diagnostics: " + ", ".join(nonzero))
    return "\n".join(lines) + "\n"


def _call_row(call: dict[str, Any], report: dict[str, Any], edges: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    tokens = call["tokens"]
    agent_id = call["agent_id"]
    edge = edges.get((call["session_id"], agent_id)) if agent_id is not None else None
    node_id = edge["node_id"] if edge else call["session_id"]
    parent_id = edge["parent_node_id"] if edge else ""
    depth = edge["spawn_depth"] if edge else 0
    lineage_status = edge["status"] if edge else "root"
    result = {column: "" for column in CSV_COLUMNS}
    result.update({
        "record_type": "call",
        "schema_version": report["schema_version"],
        "report": report["report"],
        "call_id": call["call_id"],
        "node_id": node_id,
        "parent_id": parent_id or "",
        "depth": depth if depth is not None else "",
        "lineage_status": lineage_status,
        "session_id": call["session_id"],
        "agent_id": agent_id or "",
        "topic": call["topic"] or "",
        "agent_type": call["agent_type"] or "",
        "project_id": call["project_id"] or "",
        "request_id": call["request_id"] or "",
        "request_ids": json.dumps(call["request_ids"], separators=(",", ":")),
        "message_id": call["message_id"] or "",
        "timestamp": call["timestamp"],
        "model": call["model"],
        "effort": call["effort"] or "",
        "service_tier": call["service_tier"] or "",
        "speed": call["speed"] or "",
        "inference_geo": call["inference_geo"] or "",
        "context_size": call["context_size"],
        "sidechain": call["sidechain"],
        "has_fork_source": call["has_fork_source"],
        "source_id": call["source_id"],
        "source_ids": json.dumps(call["source_ids"], separators=(",", ":")),
        "source_line": call["source_line"],
        "provenance": json.dumps(call["provenance"], separators=(",", ":"), sort_keys=True),
        "stream_block_count": call["stream_block_count"],
        "copied_source_count": call["copied_source_count"],
        "input_tokens": tokens["input"],
        "cache_read_tokens": tokens["cache_read"],
        "cache_write_5m_tokens": tokens["cache_write_5m"],
        "cache_write_1h_tokens": tokens["cache_write_1h"],
        "cache_write_unclassified_tokens": tokens["cache_write_unclassified"],
        "output_tokens": tokens["output"],
        "thinking_tokens": tokens["thinking"],
        "raw_token_units": call["raw_token_units"],
        "weighted_units": call["weighted_units"] if call["weighted_units"] is not None else "",
        "api_equivalent_usd": call["api_equivalent_usd"] if call["api_equivalent_usd"] is not None else "",
    })
    return result


def render_csv(report: dict[str, Any]) -> str:
    """Render a flat exact call ledger with one leading report metadata row."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    report_row = {column: "" for column in CSV_COLUMNS}
    report_row.update({
        "record_type": "report",
        "schema_version": report["schema_version"],
        "report": report["report"],
        "window_since": report["window"]["since"],
        "window_until": report["window"]["until"],
    })
    writer.writerow(report_row)
    edges = {
        (edge["session_id"], edge["agent_id"]): edge
        for edge in report["lineage_edges"]
    }
    for call in report["calls"]:
        writer.writerow(_call_row(call, report, edges))
    return output.getvalue()
