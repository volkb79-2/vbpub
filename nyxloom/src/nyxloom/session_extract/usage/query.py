"""Pure UTC window selection, tree roll-up, sorting, and report shaping."""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .model import Aggregate, Call, Compaction, Corpus, Node, Session, Window


class UsageQueryError(ValueError):
    """Raised for an ambiguous or invalid usage-report query."""


_DURATION_RE = re.compile(r"^([1-9][0-9]*)([smhdw])$")
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
SORT_COLUMNS = ("cost", "start", "dur", "calls", "peak", "out")


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
    """Resolve relative or explicit bounds to an inclusive UTC interval.

    A relative ``since`` is measured backwards from ``until`` when an explicit
    end is supplied, otherwise from ``now``. Both bounds are normalized to UTC.
    """
    if not isinstance(since, str) or not since.strip():
        raise ValueError("--since must be a duration such as 24h or a timezone-aware timestamp")
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("now must include a timezone")
    reference = reference.astimezone(timezone.utc)
    end = _parse_iso_timestamp(until) if until else reference
    start = _parse_since(since.strip(), end if until else reference)
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


def _copy_windowed(
    node: Node,
    *,
    window: Window,
    no_labels: bool,
) -> Node | None:
    calls = [
        replace(call, topic=None if no_labels else call.topic)
        for call in node.calls
        if window.since <= call.timestamp <= window.until
    ]
    children = [
        copied for child in node.children
        if (copied := _copy_windowed(child, window=window, no_labels=no_labels)) is not None
    ]
    if not calls and not children:
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
        calls=calls,
        compactions=[
            event for event in node.compactions
            if window.since <= event.timestamp <= window.until
        ],
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
    peak = max(peaks, key=lambda aggregate: (aggregate.peak_context, -aggregate.peak_context_at.timestamp())) if peaks else None
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
        duration_seconds=int((end - start).total_seconds()) if start and end else 0,
        models=dict(sorted(models.items())),
        efforts=dict(sorted(efforts.items())),
    )


def _roll_up(node: Node) -> Aggregate:
    node.self_aggregate = Aggregate.from_calls(node.calls)
    child_aggregates = [_roll_up(child) for child in node.children]
    node.subtree_aggregate = _combine([node.self_aggregate, *child_aggregates])
    return node.subtree_aggregate


def _sort_key(node: Node, column: str) -> tuple[Any, ...]:
    aggregate = node.subtree_aggregate
    if column == "start":
        return (aggregate.start is None, aggregate.start or datetime.max.replace(tzinfo=timezone.utc), node.node_id)
    if column == "dur":
        return (-aggregate.duration_seconds, node.node_id)
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
            calls=sorted(
                [call for child in small for descendant in _walk(child) for call in descendant.calls],
                key=lambda call: (call.timestamp, call.source_order),
            ),
            compactions=sorted(
                [event for child in small for descendant in _walk(child)
                 for event in descendant.compactions],
                key=lambda event: event.timestamp,
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


def _call_dict(call: Call) -> dict[str, Any]:
    """Serialize only request metadata needed by later timeline renderers."""
    return {
        "request_id": call.request_id,
        "timestamp": _iso(call.timestamp),
        "source_order": call.source_order,
        "session_id": call.session_id,
        "agent_id": call.agent_id,
        "topic": call.topic,
        "agent_type": call.agent_type,
        "project": call.project,
        "model": call.model,
        "effort": call.effort,
        "service_tier": call.service_tier,
        "speed": call.speed,
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
        "kind": call.kind,
        "compaction_before": call.compaction_before,
        "sidechain": call.sidechain,
        "compaction_pre_tokens": call.compact_pre,
        "compaction_post_tokens": call.compact_post,
        "weighted_units": call.weighted_cost,
        "usd": call.usd_cost,
    }


def _aggregate_dict(aggregate: Aggregate, *, share: float | None = None) -> dict[str, Any]:
    result = {
        "calls": aggregate.calls,
        "start": _iso(aggregate.start),
        "end": _iso(aggregate.end),
        "duration_seconds": aggregate.duration_seconds,
        "models": dict(aggregate.models),
        "efforts": dict(aggregate.efforts),
        "tokens": _tokens(aggregate),
        "raw_token_units": aggregate.raw_token_units,
        "peak_context": aggregate.peak_context,
        "peak_context_at": _iso(aggregate.peak_context_at),
        "weighted_units": aggregate.weighted_units,
        "usd": aggregate.usd,
    }
    if share is not None:
        result["share_of_total"] = round(share, 8)
    return result


def _node_dict(node: Node, *, total_raw: int, depth: int) -> dict[str, Any]:
    subtree_share = (
        node.subtree_aggregate.raw_token_units / total_raw if total_raw else 0.0
    )
    self_share = node.self_aggregate.raw_token_units / total_raw if total_raw else 0.0
    return {
        "node_id": node.node_id,
        "parent_id": node.parent_id,
        "session_id": node.session_id,
        "agent_id": node.agent_id,
        "kind": node.kind,
        "agent_type": node.agent_type,
        "topic": node.topic,
        "project": node.project,
        "depth": depth,
        "start": _iso(node.subtree_aggregate.start),
        "end": _iso(node.subtree_aggregate.end),
        "duration_seconds": node.subtree_aggregate.duration_seconds,
        "calls": node.subtree_aggregate.calls,
        "models": dict(node.subtree_aggregate.models),
        "efforts": dict(node.subtree_aggregate.efforts),
        "tokens": _tokens(node.subtree_aggregate),
        "raw_token_units": node.subtree_aggregate.raw_token_units,
        "peak_context": node.subtree_aggregate.peak_context,
        "peak_context_at": _iso(node.subtree_aggregate.peak_context_at),
        "weighted_units": node.subtree_aggregate.weighted_units,
        "usd": node.subtree_aggregate.usd,
        "share_basis": "raw_token_units",
        "share_of_total": round(subtree_share, 8),
        "share_of_window": None,
        "self": _aggregate_dict(node.self_aggregate, share=self_share),
        "subtree": _aggregate_dict(node.subtree_aggregate, share=subtree_share),
        "call_details": [_call_dict(call) for call in node.calls],
        "compactions": [
            {
                "timestamp": _iso(event.timestamp),
                "kind": "compaction",
                "pre_tokens": event.pre_tokens,
                "post_tokens": event.post_tokens,
                "trigger": event.trigger,
            }
            for event in node.compactions
        ],
        "omitted_descendants": node.omitted_descendants,
        "children": [
            _node_dict(child, total_raw=total_raw, depth=depth + 1)
            for child in node.children
        ],
    }


def _coverage(corpus: Corpus) -> dict[str, Any]:
    warnings = asdict(corpus.warnings)
    return {
        "scanned_files": corpus.scanned_files,
        "pruned_files": corpus.pruned_files,
        "pruned_sessions": corpus.pruned_sessions,
        "warnings": warnings,
        "has_warnings": any(warnings.values()),
    }


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
    """Build a JSON-serializable tree report from a loaded corpus."""
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
    project_sessions = (
        _select_project_sessions(corpus.sessions, project) if project is not None else None
    )
    selected: list[Node] = []
    for item in corpus.sessions:
        if session is not None and item.session_id != session:
            continue
        if project_sessions is not None and item.session_id not in project_sessions:
            continue
        copied = _copy_windowed(item.root, window=window, no_labels=no_labels)
        if copied is not None:
            _roll_up(copied)
            _sort_tree(copied, sort)
            selected.append(copied)
    selected.sort(key=lambda node: _sort_key(node, sort))
    total = _combine([node.subtree_aggregate for node in selected])
    for node in selected:
        _fold_small_children(
            node, minimum=min_share, total_raw=total.raw_token_units, sort=sort,
        )
        if depth is not None:
            _prune_depth(node, current=0, maximum=depth)
    if min_share > 0 and total.raw_token_units > 0 and len(selected) > 1:
        small = [
            node for node in selected
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
                calls=sorted(
                    [call for root in small for descendant in _walk(root) for call in descendant.calls],
                    key=lambda call: (call.timestamp, call.source_order),
                ),
                compactions=sorted(
                    [event for root in small for descendant in _walk(root)
                     for event in descendant.compactions],
                    key=lambda event: event.timestamp,
                ),
                self_aggregate=aggregate,
                subtree_aggregate=aggregate,
                omitted_descendants=count,
            )
            selected = [node for node in selected if node.node_id not in small_ids]
            selected.append(other)
            selected.sort(key=lambda node: _sort_key(node, sort))
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    generated = generated.astimezone(timezone.utc)
    return {
        "schema_version": 1,
        "report": "nyxloom-usage-tree",
        "generated_at": _iso(generated),
        "window": {
            "since": _iso(window.since),
            "until": _iso(window.until),
            "inclusive": True,
            "timezone": "UTC",
        },
        "filters": {
            "project": project,
            "session": session,
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
            "usd": None,
            "limit_share": None,
        },
        "totals": _aggregate_dict(total, share=1.0 if total.raw_token_units else 0.0),
        "coverage": _coverage(corpus),
        "privacy": {"message_content": "excluded", "tool_content": "excluded"},
        "nodes": [
            _node_dict(node, total_raw=total.raw_token_units, depth=0)
            for node in selected
        ],
    }
