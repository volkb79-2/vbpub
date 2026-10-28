"""UTC window selection, tree roll-up, and the frozen usage-report-v1 shape."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .model import Aggregate, Call, Corpus, Node, Session, UsageEvent, Window


class UsageQueryError(ValueError):
    """Raised for an ambiguous or invalid usage-report query."""


_DURATION_RE = re.compile(r"^([1-9][0-9]*)([smhdw])$")
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_BURST_GAP_SECONDS = 3600
SORT_COLUMNS = ("cost", "start", "dur", "calls", "peak", "out")
_INFORMATIONAL_COVERAGE_COUNTERS = frozenset({
    "duplicate_requests",
    "copied_prefixes",
    "parent_conflicts",
    "unclassified_rejections",
    "symlink_aliases",
})


def _parse_iso_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp {value!r}; use ISO-8601 with a timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_since(value: str, reference: datetime) -> datetime:
    match = _DURATION_RE.fullmatch(value)
    if match is None:
        return _parse_iso_timestamp(value)
    amount, unit = match.groups()
    return reference - timedelta(seconds=int(amount) * _DURATION_SECONDS[unit])


def resolve_window(
    since: str,
    until: str | None = None,
    *,
    now: datetime | None = None,
) -> Window:
    """Resolve a half-open UTC interval ``[since, until)``.

    Relative starts count backward from an explicit end when supplied,
    otherwise from the injectable invocation time. Record timestamps and
    explicit bounds are normalized from their supplied offsets to UTC.
    """
    if not isinstance(since, str) or not since.strip():
        raise ValueError("--since must be a duration such as 24h or a timezone-aware timestamp")
    reference = now if now is not None else datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("now must include a timezone")
    reference = reference.astimezone(timezone.utc)
    end = _parse_iso_timestamp(until) if until is not None else reference
    start = _parse_since(since.strip(), end if until is not None else reference)
    if end < start:
        raise ValueError("--until must not be before --since")
    return Window(start, end, since.strip(), until)


def parse_share_percent(value: str) -> float:
    """Parse a 0..100 percentage, accepting an optional trailing percent sign."""
    raw = value.strip()
    if raw.endswith("%"):
        raw = raw[:-1].strip()
    try:
        percentage = float(raw)
    except ValueError as exc:
        raise ValueError("--min-share must be a percentage from 0 to 100") from exc
    if not 0 <= percentage <= 100:
        raise ValueError("--min-share must be a percentage from 0 to 100")
    return percentage / 100


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _project_key(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(Path(value).expanduser().resolve(strict=False))
    material = json.dumps((normalized,), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"project_{digest}"


def _normalise_project(value: str) -> str:
    expanded = str(Path(value).expanduser())
    return os.path.normpath(expanded)


def _select_project_sessions(sessions: list[Session], selector: str) -> set[str]:
    observed: dict[str, set[str]] = {}
    for session in sessions:
        for node in _walk(session.root):
            for call in node.calls:
                if call.project:
                    observed.setdefault(_normalise_project(call.project), set()).add(session.session_id)
    requested = _normalise_project(selector)
    if requested in observed:
        return observed[requested]
    basename_matches = {
        path: ids for path, ids in observed.items()
        if Path(path).name == selector
    }
    if not basename_matches:
        raise UsageQueryError("--project did not match a recorded session cwd")
    if len(basename_matches) > 1:
        raise UsageQueryError("--project basename is ambiguous; pass the recorded cwd path")
    return set(next(iter(basename_matches.values())))


def _copy_windowed(node: Node, *, window: Window, no_labels: bool) -> Node | None:
    calls = [
        replace(call, topic=None if no_labels else call.topic)
        for call in node.calls
        if window.since <= call.timestamp < window.until
    ]
    events = [
        replace(event)
        for event in node.events
        if window.since <= event.timestamp < window.until
    ]
    children = [
        copied for child in node.children
        if (copied := _copy_windowed(child, window=window, no_labels=no_labels)) is not None
    ]
    if not calls and not events and not children:
        return None
    copied_node = Node(
        node_id=node.node_id,
        parent_id=node.parent_id,
        session_id=node.session_id,
        agent_id=node.agent_id,
        agent_type=node.agent_type,
        topic=None if no_labels else node.topic,
        kind=node.kind,
        project=node.project,
        spawn_depth=node.spawn_depth,
        is_fork=node.is_fork,
        source_ids=list(node.source_ids),
        lineage_status=node.lineage_status,
        lineage_candidates=list(node.lineage_candidates),
        claimed_parent_agent_id=node.claimed_parent_agent_id,
        lineage_tool_use_id=node.lineage_tool_use_id,
        clipped_before=node.clipped_before,
        clipped_after=node.clipped_after,
        calls=calls,
        events=events,
        children=children,
        omitted_descendants=node.omitted_descendants,
    )
    for child in copied_node.children:
        child.parent_id = copied_node.node_id
    return copied_node


def _combine(aggregates: list[Aggregate]) -> Aggregate:
    nonempty = [aggregate for aggregate in aggregates if aggregate.calls]
    if not nonempty:
        return Aggregate()
    models: Counter[str] = Counter()
    efforts: Counter[str] = Counter()
    for aggregate in nonempty:
        models.update(aggregate.models)
        efforts.update(aggregate.efforts)
    starts = [aggregate.start for aggregate in nonempty if aggregate.start is not None]
    ends = [aggregate.end for aggregate in nonempty if aggregate.end is not None]
    peaks = [aggregate for aggregate in nonempty if aggregate.peak_context_at is not None]
    peak = max(
        peaks,
        key=lambda aggregate: (aggregate.peak_context, -aggregate.peak_context_at.timestamp()),
    ) if peaks else None
    start = min(starts) if starts else None
    end = max(ends) if ends else None
    return Aggregate(
        calls=sum(aggregate.calls for aggregate in nonempty),
        input_tokens=sum(aggregate.input_tokens for aggregate in nonempty),
        output_tokens=sum(aggregate.output_tokens for aggregate in nonempty),
        cache_read_tokens=sum(aggregate.cache_read_tokens for aggregate in nonempty),
        cache_write_5m_tokens=sum(aggregate.cache_write_5m_tokens for aggregate in nonempty),
        cache_write_1h_tokens=sum(aggregate.cache_write_1h_tokens for aggregate in nonempty),
        cache_write_unclassified_tokens=sum(
            aggregate.cache_write_unclassified_tokens for aggregate in nonempty
        ),
        thinking_tokens=sum(aggregate.thinking_tokens for aggregate in nonempty),
        raw_token_units=sum(aggregate.raw_token_units for aggregate in nonempty),
        peak_context=peak.peak_context if peak is not None else 0,
        peak_context_at=peak.peak_context_at if peak is not None else None,
        start=start,
        end=end,
        wall_duration_seconds=int((end - start).total_seconds()) if start and end else 0,
        active_span_seconds=sum(aggregate.active_span_seconds for aggregate in nonempty),
        models=dict(sorted(models.items())),
        efforts=dict(sorted(efforts.items())),
        weighted_units=(
            sum(aggregate.weighted_units for aggregate in nonempty)
            if all(aggregate.weighted_units is not None for aggregate in nonempty) else None
        ),
        usd=(
            sum(aggregate.usd for aggregate in nonempty)
            if all(aggregate.usd is not None for aggregate in nonempty) else None
        ),
    )


def _roll_up(node: Node) -> Aggregate:
    node.self_aggregate = Aggregate.from_calls(node.calls)
    child_aggregates = [_roll_up(child) for child in node.children]
    node.subtree_aggregate = _combine([node.self_aggregate, *child_aggregates])
    return node.subtree_aggregate


def _sort_key(node: Node, column: str) -> tuple[Any, ...]:
    aggregate = node.subtree_aggregate
    if column == "start":
        return (
            aggregate.start is None,
            aggregate.start or datetime.max.replace(tzinfo=timezone.utc),
            node.node_id,
        )
    if column == "dur":
        return (-aggregate.wall_duration_seconds, node.node_id)
    if column == "calls":
        return (-aggregate.calls, node.node_id)
    if column == "peak":
        return (-aggregate.peak_context, node.node_id)
    if column == "out":
        return (-aggregate.output_tokens, node.node_id)
    return (-aggregate.raw_token_units, node.node_id)


def _sort_tree(node: Node, column: str) -> None:
    node.children.sort(key=lambda child: _sort_key(child, column))
    for child in node.children:
        _sort_tree(child, column)


def _node_count(node: Node) -> int:
    if node.kind == "other":
        return node.omitted_descendants
    own = 1 if node.agent_id is not None else 0
    return own + sum(_node_count(child) for child in node.children)


def _fold_small_children(node: Node, *, minimum: float, total_raw: int, sort: str) -> None:
    if minimum <= 0 or total_raw <= 0 or not node.children:
        for child in node.children:
            _fold_small_children(child, minimum=minimum, total_raw=total_raw, sort=sort)
        return
    small: list[Node] = []
    kept: list[Node] = []
    for child in node.children:
        share = child.subtree_aggregate.raw_token_units / total_raw if total_raw else 0.0
        if share < minimum:
            small.append(child)
        else:
            _fold_small_children(child, minimum=minimum, total_raw=total_raw, sort=sort)
            kept.append(child)
    if small:
        aggregate = _combine([child.subtree_aggregate for child in small])
        count = sum(_node_count(child) for child in small)
        other = Node(
            node_id=f"{node.node_id}:other",
            parent_id=node.node_id,
            session_id=node.session_id,
            agent_id=None,
            agent_type="other",
            topic=f"other ({count} agents)",
            kind="other",
            project=node.project,
            source_ids=sorted({source_id for child in small for source_id in child.source_ids}),
            lineage_status="folded",
            clipped_before=any(child.clipped_before for child in small),
            clipped_after=any(child.clipped_after for child in small),
            calls=sorted(
                [call for child in small for descendant in _walk(child) for call in descendant.calls],
                key=lambda call: (call.timestamp, call.source_id, call.source_line, call.call_id),
            ),
            events=sorted(
                [event for child in small for descendant in _walk(child) for event in descendant.events],
                key=lambda event: (event.timestamp, event.session_id, event.source_id, event.source_line),
            ),
            self_aggregate=aggregate,
            subtree_aggregate=aggregate,
            omitted_descendants=count,
        )
        kept.append(other)
    node.children = sorted(kept, key=lambda child: _sort_key(child, sort))


def _prune_depth(node: Node, *, current: int, maximum: int) -> None:
    if current >= maximum:
        node.omitted_descendants += sum(_node_count(child) for child in node.children)
        node.children = []
        return
    for child in node.children:
        _prune_depth(child, current=current + 1, maximum=maximum)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _tokens(aggregate: Aggregate) -> dict[str, int]:
    return {
        "input": aggregate.input_tokens,
        "output": aggregate.output_tokens,
        "cache_read": aggregate.cache_read_tokens,
        "cache_write_5m": aggregate.cache_write_5m_tokens,
        "cache_write_1h": aggregate.cache_write_1h_tokens,
        "cache_write_unclassified": aggregate.cache_write_unclassified_tokens,
        "thinking": aggregate.thinking_tokens,
    }


def _aggregate_dict(aggregate: Aggregate, *, share: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "calls": aggregate.calls,
        "start": _iso(aggregate.start),
        "end": _iso(aggregate.end),
        "duration_seconds": aggregate.wall_duration_seconds,
        "wall_duration_seconds": aggregate.wall_duration_seconds,
        "active_span_seconds": aggregate.active_span_seconds,
        "models": dict(aggregate.models),
        "efforts": dict(aggregate.efforts),
        "tokens": _tokens(aggregate),
        "raw_token_units": aggregate.raw_token_units,
        "peak_context": aggregate.peak_context,
        "peak_context_at": _iso(aggregate.peak_context_at),
        "weighted_units": aggregate.weighted_units,
        "api_equivalent_usd": aggregate.usd,
        "usd": aggregate.usd,
    }
    if share is not None:
        result["share_of_total"] = round(share, 8)
    return result


def _activity_bursts(calls: list[Call]) -> list[dict[str, Any]]:
    ordered = sorted(
        calls,
        key=lambda call: (call.timestamp, call.source_id, call.source_line, call.call_id),
    )
    if not ordered:
        return []
    bursts: list[dict[str, Any]] = []
    start = end = ordered[0].timestamp
    count = 1
    for call in ordered[1:]:
        if (call.timestamp - end).total_seconds() > _BURST_GAP_SECONDS:
            bursts.append({"start": _iso(start), "end": _iso(end), "calls": count})
            start = call.timestamp
            count = 0
        end = call.timestamp
        count += 1
    bursts.append({"start": _iso(start), "end": _iso(end), "calls": count})
    return bursts


def _call_dict(call: Call) -> dict[str, Any]:
    """Serialize one complete, deduplicated call without text or paths."""
    return {
        "call_id": call.call_id,
        "request_id": call.request_id,
        "request_ids": list(call.request_ids),
        "message_id": call.message_id,
        "timestamp": _iso(call.timestamp),
        "session_id": call.session_id,
        "agent_id": call.agent_id,
        "topic": call.topic,
        "agent_type": call.agent_type,
        "project_id": _project_key(call.project),
        "model": call.model,
        "effort": call.effort,
        "service_tier": call.service_tier,
        "speed": call.speed,
        "inference_geo": call.inference_geo,
        "tokens": {
            "input": call.input_tokens,
            "output": call.output_tokens,
            "cache_read": call.cache_read_tokens,
            "cache_write_5m": call.cache_write_5m_tokens,
            "cache_write_1h": call.cache_write_1h_tokens,
            "cache_write_unclassified": call.cache_write_unclassified_tokens,
            "thinking": call.thinking_tokens,
        },
        "raw_token_units": call.raw_token_units,
        "context_size": call.context_size,
        "sidechain": call.sidechain,
        "source_id": call.source_id,
        "source_ids": list(call.source_ids),
        "source_line": call.source_line,
        "provenance": [asdict(ref) for ref in call.provenance],
        "stream_block_count": call.stream_block_count,
        "copied_source_count": call.copied_source_count,
        "has_fork_source": call.has_fork_source,
        "weighted_units": call.weighted_cost,
        "api_equivalent_usd": call.usd_cost,
    }


def _event_dict(event: UsageEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "kind": event.kind,
        "timestamp": _iso(event.timestamp),
        "session_id": event.session_id,
        "agent_id": event.agent_id,
        "source_record_id": event.source_record_id,
        "source_id": event.source_id,
        "source_ids": list(event.source_ids),
        "source_line": event.source_line,
        "provenance": [asdict(ref) for ref in event.provenance],
        "pre_tokens": event.pre_tokens,
        "post_tokens": event.post_tokens,
        "duration_ms": event.duration_ms,
        "trigger": event.trigger,
        "summary_present": event.summary_present,
        "summary_timestamp": _iso(event.summary_timestamp),
        "limit_kind": event.limit_kind,
        "error_code": event.error_code,
        "status_code": event.status_code,
        "reset_at": _iso(event.reset_at),
        "previous_call_id": event.previous_call_id,
        "current_call_id": event.current_call_id,
    }


def _node_dict(node: Node, *, total_raw: int, depth: int) -> dict[str, Any]:
    subtree_share = node.subtree_aggregate.raw_token_units / total_raw if total_raw else 0.0
    self_share = node.self_aggregate.raw_token_units / total_raw if total_raw else 0.0
    return {
        "node_id": node.node_id,
        "parent_id": node.parent_id,
        "session_id": node.session_id,
        "agent_id": node.agent_id,
        "kind": node.kind,
        "agent_type": node.agent_type,
        "topic": node.topic,
        "project_id": _project_key(node.project),
        "depth": depth,
        "spawn_depth": node.spawn_depth,
        "is_fork": node.is_fork,
        "source_ids": list(node.source_ids),
        "lineage_status": node.lineage_status,
        "lineage_candidates": list(node.lineage_candidates),
        "claimed_parent_agent_id": node.claimed_parent_agent_id,
        "lineage_tool_use_id": node.lineage_tool_use_id,
        "clipped_before": node.clipped_before,
        "clipped_after": node.clipped_after,
        "start": _iso(node.subtree_aggregate.start),
        "end": _iso(node.subtree_aggregate.end),
        "duration_seconds": node.subtree_aggregate.wall_duration_seconds,
        "calls": node.subtree_aggregate.calls,
        "models": dict(node.subtree_aggregate.models),
        "efforts": dict(node.subtree_aggregate.efforts),
        "tokens": _tokens(node.subtree_aggregate),
        "raw_token_units": node.subtree_aggregate.raw_token_units,
        "peak_context": node.subtree_aggregate.peak_context,
        "peak_context_at": _iso(node.subtree_aggregate.peak_context_at),
        "weighted_units": node.subtree_aggregate.weighted_units,
        "api_equivalent_usd": node.subtree_aggregate.usd,
        "usd": node.subtree_aggregate.usd,
        "share_basis": "raw_token_units",
        "share_of_total": round(subtree_share, 8),
        "share_of_window": None,
        "self": _aggregate_dict(node.self_aggregate, share=self_share),
        "subtree": _aggregate_dict(node.subtree_aggregate, share=subtree_share),
        "call_ids": [call.call_id for call in node.calls],
        "event_ids": [event.event_id for event in node.events],
        "active_bursts": _activity_bursts(node.calls),
        "omitted_descendants": node.omitted_descendants,
        "children": [
            _node_dict(child, total_raw=total_raw, depth=depth + 1)
            for child in node.children
        ],
    }


def _coverage(corpus: Corpus) -> dict[str, Any]:
    warnings = asdict(corpus.warnings)
    # Unknown future warning fields fail conservatively as incomplete.
    incomplete = any(
        count for key, count in warnings.items()
        if key not in _INFORMATIONAL_COVERAGE_COUNTERS
    )
    return {
        "scanned_files": corpus.scanned_files,
        "warnings": warnings,
        "has_warnings": any(warnings.values()),
        # Successful stream and copied-prefix deduplication is reported for
        # auditability, but does not mean source coverage is incomplete.
        "partial": incomplete,
    }


def _lineage_edges(nodes: list[Node]) -> list[dict[str, Any]]:
    edges = []
    for root in nodes:
        for node in _walk(root):
            if node.agent_id is None:
                continue
            edges.append({
                "session_id": node.session_id,
                "agent_id": node.agent_id,
                "node_id": node.node_id,
                "parent_node_id": node.parent_id if node.lineage_status.startswith("resolved_") else None,
                "status": node.lineage_status,
                "candidate_parent_ids": list(node.lineage_candidates),
                "claimed_parent_agent_id": node.claimed_parent_agent_id,
                "tool_use_id": node.lineage_tool_use_id,
                "spawn_depth": node.spawn_depth,
                "is_fork": node.is_fork,
            })
    return sorted(edges, key=lambda edge: (edge["session_id"], edge["agent_id"]))


def build_report(
    corpus: Corpus,
    *,
    window: Window,
    project: str | None = None,
    session: str | None = None,
    depth: int | None = None,
    min_share: float = 0.0,
    flat: bool = False,
    sort: str = "cost",
    no_labels: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable, versioned report from the loaded corpus."""
    if sort not in SORT_COLUMNS:
        raise UsageQueryError(f"--sort must be one of: {', '.join(SORT_COLUMNS)}")
    if depth is not None and depth < 0:
        raise UsageQueryError("--depth must be zero or greater")
    if not 0 <= min_share <= 1:
        raise UsageQueryError("min_share must be between 0 and 1")
    if flat and depth is not None:
        raise UsageQueryError("--flat and --depth cannot be combined")
    known_sessions = {item.session_id for item in corpus.sessions}
    if session is not None and session not in known_sessions:
        raise UsageQueryError("--session did not match a loaded transcript session")
    project_sessions = _select_project_sessions(corpus.sessions, project) if project is not None else None
    full_roots: list[Node] = []
    for item in corpus.sessions:
        if session is not None and item.session_id != session:
            continue
        if project_sessions is not None and item.session_id not in project_sessions:
            continue
        copied = _copy_windowed(item.root, window=window, no_labels=no_labels)
        if copied is not None:
            _roll_up(copied)
            _sort_tree(copied, sort)
            full_roots.append(copied)
    full_roots.sort(key=lambda node: _sort_key(node, sort))

    total = _combine([node.subtree_aggregate for node in full_roots])
    all_calls = sorted(
        [call for root in full_roots for node in _walk(root) for call in node.calls],
        key=lambda call: (call.timestamp, call.session_id, call.source_id, call.source_line, call.call_id),
    )
    all_events = sorted(
        [event for root in full_roots for node in _walk(root) for event in node.events],
        key=lambda event: (event.timestamp, event.session_id, event.source_id, event.source_line, event.event_id),
    )
    lineage_edges = _lineage_edges(full_roots)

    displayed_roots = full_roots
    for node in displayed_roots:
        _fold_small_children(node, minimum=min_share, total_raw=total.raw_token_units, sort=sort)
        if depth is not None:
            _prune_depth(node, current=0, maximum=depth)
    if min_share > 0 and total.raw_token_units > 0 and len(displayed_roots) > 1:
        small = [
            node for node in displayed_roots
            if node.subtree_aggregate.raw_token_units / total.raw_token_units < min_share
        ]
        if small:
            small_ids = {node.node_id for node in small}
            count = len(small)
            aggregate = _combine([node.subtree_aggregate for node in small])
            other = Node(
                node_id="usage:other-sessions",
                parent_id=None,
                session_id="",
                agent_id=None,
                agent_type="other",
                topic=f"other ({count} sessions)",
                kind="other",
                source_ids=sorted({source_id for node in small for source_id in node.source_ids}),
                lineage_status="folded",
                clipped_before=any(node.clipped_before for node in small),
                clipped_after=any(node.clipped_after for node in small),
                calls=sorted(
                    [call for root in small for descendant in _walk(root) for call in descendant.calls],
                    key=lambda call: (call.timestamp, call.source_id, call.source_line, call.call_id),
                ),
                events=sorted(
                    [event for root in small for descendant in _walk(root) for event in descendant.events],
                    key=lambda event: (event.timestamp, event.session_id, event.source_id, event.source_line),
                ),
                self_aggregate=aggregate,
                subtree_aggregate=aggregate,
                omitted_descendants=count,
            )
            displayed_roots = [node for node in displayed_roots if node.node_id not in small_ids]
            displayed_roots.append(other)
            displayed_roots.sort(key=lambda node: _sort_key(node, sort))

    generated = generated_at if generated_at is not None else datetime.now(timezone.utc)
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    generated = generated.astimezone(timezone.utc)
    return {
        "schema_version": "usage-report-v1",
        "report": "nyxloom-usage-report",
        "generated_at": _iso(generated),
        "contract": {
            "window": "[since, until), UTC",
            "call_timestamp": "record.timestamp; timezone-aware ISO-8601 required",
            "stream_deduplication": "canonical source plus requestId, message.id fallback",
            "copied_prefix_identity": "sessionId, message.id, UTC timestamp, model, complete usage tuple",
            "csv_rows": "one report metadata row followed by one row per deduplicated call",
            "share_rounding_decimal_places": 8,
            "active_burst_gap_seconds": _BURST_GAP_SECONDS,
            "call_order": "timestamp, session_id, source_id, source_line, call_id",
            "event_order": "timestamp, session_id, source_id, source_line, event_id",
            "opaque_id": "truncated SHA-256 over canonical source and record identity",
            "json_key_order": "lexicographic",
        },
        "window": {
            "since": _iso(window.since),
            "until": _iso(window.until),
            "semantics": "[since, until)",
            "timezone": "UTC",
        },
        "filters": {
            "project_selected": project is not None,
            "session_selected": session is not None,
            "no_labels": no_labels,
        },
        "presentation": {
            "flat": flat,
            "depth": depth,
            "sort": sort,
            "min_share": min_share,
            "share_basis": "raw_token_units",
        },
        "basis": {
            "mode": "raw",
            "definition": "input + cache_read + cache_write_5m + cache_write_1h + cache_write_unclassified + output",
            "weighted_units": None,
            "api_equivalent_usd": None,
            "usd": None,
            "estimated_limit_pct": None,
        },
        "totals": _aggregate_dict(total, share=1.0 if total.raw_token_units else 0.0),
        "calls": [_call_dict(call) for call in all_calls],
        "events": [_event_dict(event) for event in all_events],
        "lineage_edges": lineage_edges,
        "conflicts": list(corpus.conflicts),
        "coverage": _coverage(corpus),
        "privacy": {
            "message_content": "excluded",
            "thinking_content": "excluded; token count is an output subset",
            "tool_content": "excluded",
            "filesystem_paths": "excluded; project and source identifiers are opaque",
        },
        "extensions": {"prices": None, "limit_anchors": None, "chart_data": None},
        "nodes": [
            _node_dict(node, total_raw=total.raw_token_units, depth=0)
            for node in displayed_roots
        ],
    }
