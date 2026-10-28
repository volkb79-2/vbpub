"""Deterministic, content-free chart data derived from the usage ledger."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from typing import Any, Iterable, Sequence

from .model import Call, Node, UsageEvent, Window
from .pricing import UsageWeights

CHART_SCHEMA_VERSION = "usage-charts-v1"
GROUP_BY_VALUES = ("agent", "topic", "model", "effort", "session")
DEFAULT_TOP_N = 12
DEFAULT_CONTEXT_POINT_LIMIT = 500
_TOKEN_FIELDS = (
    ("input", "input_tokens", "input"),
    ("cache_read", "cache_read_tokens", "cache_read"),
    ("cache_write_5m", "cache_write_5m_tokens", "cache_write_5m"),
    ("cache_write_1h", "cache_write_1h_tokens", "cache_write_1h"),
    ("cache_write_unclassified", "cache_write_unclassified_tokens", "cache_write_unclassified"),
    ("output", "output_tokens", "output"),
)
_KNOWN_EFFORT_STYLES = {
    None: {"dash": "", "lightness": 54},
    "low": {"dash": "4 2", "lightness": 64},
    "medium": {"dash": "", "lightness": 54},
    "high": {"dash": "6 3", "lightness": 44},
    "xhigh": {"dash": "2 2", "lightness": 36},
}


class UsageChartError(ValueError):
    """Raised when chart data cannot be built without weakening its contract."""


@dataclass(frozen=True, slots=True)
class ChartOptions:
    """Inputs for the reserved chart-data extension in ``usage-report-v1``."""

    weights: UsageWeights
    top_n: int = DEFAULT_TOP_N
    group_by: str = "agent"
    anchors: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    calibration: dict[str, Any] | None = None
    context_point_limit: int = DEFAULT_CONTEXT_POINT_LIMIT


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UsageChartError("chart timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_id(kind: str, *parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return f"chart-{kind}-{digest}"


def _walk_nodes(roots: Iterable[Node]) -> Iterable[Node]:
    for root in roots:
        yield root
        yield from _walk_nodes(root.children)


def _node_label(node: Node) -> str:
    if node.topic:
        return node.topic
    if node.agent_id:
        return f"agent {node.agent_id}"
    return f"session {node.session_id}"


def _call_order(call: Call) -> tuple[datetime, str, int, str]:
    return (call.timestamp, call.source_id, call.source_line, call.call_id)


def _weighted(call: Call) -> float:
    if call.weighted_cost is None or not math.isfinite(call.weighted_cost):
        raise UsageChartError(
            "chart weighted-cost datasets require valued calls from the selected weights profile"
        )
    return float(call.weighted_cost)


def _sum_weighted(calls: Iterable[Call]) -> float:
    return math.fsum(_weighted(call) for call in calls)


def _sum_usd(calls: Sequence[Call]) -> float | None:
    if any(call.usd_cost is None or not math.isfinite(call.usd_cost) for call in calls):
        return None
    return math.fsum(float(call.usd_cost) for call in calls if call.usd_cost is not None)


def _raw_total(calls: Iterable[Call]) -> int:
    return sum(call.raw_token_units for call in calls)


def _token_counts(calls: Sequence[Call]) -> dict[str, int]:
    return {
        name: sum(int(getattr(call, attr)) for call in calls)
        for name, attr, _ in _TOKEN_FIELDS
    }


def _weighted_components(
    calls: Sequence[Call], weights: UsageWeights,
) -> dict[str, dict[str, Any]]:
    profile = weights.weight_profiles[weights.default_weight_profile]
    result: dict[str, dict[str, Any]] = {}
    for name, attr, weight_key in _TOKEN_FIELDS:
        count = sum(int(getattr(call, attr)) for call in calls)
        contributing = [call for call in calls if int(getattr(call, attr)) > 0]
        result[name] = {
            "token_count": count,
            "weight_per_token": profile[weight_key],
            "weighted_units": math.fsum(
                int(getattr(call, attr)) * profile[weight_key] for call in contributing
            ),
            "call_ids": sorted(call.call_id for call in contributing),
        }
    return result


def _call_point(call: Call, node: Node, *, dataset: str) -> dict[str, Any]:
    return {
        "chart_id": _stable_id(dataset, node.node_id, call.call_id),
        "call_id": call.call_id,
        "node_id": node.node_id,
        "session_id": call.session_id,
        "agent_id": call.agent_id,
        "topic": call.topic or node.topic,
        "timestamp": _iso(call.timestamp),
        "context_size": call.context_size,
        "model": call.model,
        "effort": call.effort,
        "speed": call.speed,
        "service_tier": call.service_tier,
        "inference_geo": call.inference_geo,
        "tokens": {
            "input": call.input_tokens,
            "cache_read": call.cache_read_tokens,
            "cache_write_5m": call.cache_write_5m_tokens,
            "cache_write_1h": call.cache_write_1h_tokens,
            "cache_write_unclassified": call.cache_write_unclassified_tokens,
            "output": call.output_tokens,
            "thinking": call.thinking_tokens,
        },
        "raw_token_units": call.raw_token_units,
        "weighted_units": call.weighted_cost,
        "api_equivalent_usd": call.usd_cost,
    }


def _hover_record(call: Call, node: Node) -> dict[str, Any]:
    """Content-free call lookup so standalone charts can resolve every call ID."""
    return {
        "call_id": call.call_id,
        "node_id": node.node_id,
        "session_id": call.session_id,
        "agent_id": call.agent_id,
        "topic": call.topic or node.topic,
        "agent_type": node.agent_type,
        "timestamp": _iso(call.timestamp),
        "model": call.model,
        "effort": call.effort,
        "speed": call.speed,
        "service_tier": call.service_tier,
        "inference_geo": call.inference_geo,
        "context_size": call.context_size,
        "tokens": {
            "input": call.input_tokens,
            "cache_read": call.cache_read_tokens,
            "cache_write_5m": call.cache_write_5m_tokens,
            "cache_write_1h": call.cache_write_1h_tokens,
            "cache_write_unclassified": call.cache_write_unclassified_tokens,
            "output": call.output_tokens,
            "thinking": call.thinking_tokens,
        },
        "raw_token_units": call.raw_token_units,
        "weighted_units": call.weighted_cost,
        "api_equivalent_usd": call.usd_cost,
    }


def _event_point(event: UsageEvent, node: Node, *, dataset: str) -> dict[str, Any]:
    return {
        "chart_id": _stable_id(dataset, node.node_id, event.event_id),
        "event_id": event.event_id,
        "node_id": node.node_id,
        "session_id": event.session_id,
        "agent_id": event.agent_id,
        "timestamp": _iso(event.timestamp),
        "kind": event.kind,
        "pre_tokens": event.pre_tokens,
        "post_tokens": event.post_tokens,
        "duration_ms": event.duration_ms,
        "trigger": event.trigger,
        "summary_present": event.summary_present,
        "summary_timestamp": _iso(event.summary_timestamp),
        "limit_kind": event.limit_kind,
        "reset_at": _iso(event.reset_at),
        "previous_call_id": event.previous_call_id,
        "current_call_id": event.current_call_id,
        "interpretation": (
            "threshold_event_only; not a percentage or capacity anchor"
            if event.kind == "api_rejection" else None
        ),
    }


def _switch_events(node: Node, *, dataset: str) -> list[dict[str, Any]]:
    calls = sorted(node.calls, key=_call_order)
    result: list[dict[str, Any]] = []
    for previous, current in pairwise(calls):
        for kind, old, new in (
            ("model_switch", previous.model, current.model),
            ("effort_switch", previous.effort, current.effort),
        ):
            if old == new:
                continue
            result.append({
                "chart_id": _stable_id(dataset, kind, node.node_id, previous.call_id, current.call_id),
                "event_id": _stable_id(kind, node.node_id, previous.call_id, current.call_id),
                "kind": kind,
                "node_id": node.node_id,
                "session_id": current.session_id,
                "agent_id": current.agent_id,
                "timestamp": _iso(current.timestamp),
                "previous_call_id": previous.call_id,
                "current_call_id": current.call_id,
                "previous_value": old,
                "current_value": new,
            })
    return result


def _effort_style(effort: str | None) -> dict[str, int | str]:
    style = _KNOWN_EFFORT_STYLES.get(effort)
    if style is not None:
        return dict(style)
    digest = int(hashlib.sha256(str(effort).encode("utf-8")).hexdigest()[:4], 16)
    return {"dash": "1 2", "lightness": 38 + digest % 35}


def _model_hue(model: str) -> int:
    digest = hashlib.sha256(model.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 360


def _color_keys(calls: Sequence[Call]) -> dict[str, Any]:
    models = sorted({call.model for call in calls})
    efforts = sorted({call.effort for call in calls}, key=lambda value: (value is not None, value or ""))
    return {
        "model_hues": {model: _model_hue(model) for model in models},
        "effort_styles": {effort or "unknown": _effort_style(effort) for effort in efforts},
        "derivation": "exact model ID SHA-256 prefix modulo 360; effort has a fixed dash/lightness key",
    }


def _interval(node: Node) -> tuple[datetime, datetime] | None:
    times = [call.timestamp for call in node.calls]
    times.extend(event.timestamp for event in node.events)
    if not times:
        return None
    return min(times), max(times)


def _event_neighbor_call_ids(node: Node) -> set[str]:
    calls = sorted(node.calls, key=_call_order)
    call_by_id = {call.call_id: call for call in calls}
    preserve: set[str] = set()
    for previous, current in pairwise(calls):
        if previous.model != current.model or previous.effort != current.effort:
            preserve.add(current.call_id)
    for event in node.events:
        if event.kind == "unexplained_drop":
            preserve.update(
                call_id for call_id in (event.previous_call_id, event.current_call_id)
                if call_id in call_by_id
            )
            continue
        if event.kind != "compaction":
            continue
        left_key = (event.timestamp, event.source_id, event.source_line, event.event_id)
        left = [
            call for call in calls
            if (call.timestamp, call.source_id, call.source_line, call.call_id) < left_key
        ]
        right_at = event.summary_timestamp or event.timestamp
        right_key = (right_at, event.source_id, event.source_line, event.event_id)
        right = [
            call for call in calls
            if (call.timestamp, call.source_id, call.source_line, call.call_id) > right_key
        ]
        if left:
            preserve.add(left[-1].call_id)
        if right:
            preserve.add(right[0].call_id)
    return preserve


def pack_lane_intervals(intervals: Sequence[dict[str, str]]) -> list[dict[str, Any]]:
    """Greedily pack inclusive activity intervals into stable non-overlapping lanes."""
    parsed: list[tuple[datetime, datetime, str, dict[str, str]]] = []
    seen: set[str] = set()
    for interval in intervals:
        node_id = interval.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise UsageChartError("lane intervals require a non-empty node_id")
        if node_id in seen:
            raise UsageChartError("lane intervals require one activity span per node")
        seen.add(node_id)
        start = _parse_time(interval["start"])
        end = _parse_time(interval["end"])
        if end < start:
            raise UsageChartError("lane interval end must not precede its start")
        parsed.append((start, end, node_id, interval))
    parsed.sort(key=lambda item: (item[0], item[1], item[2]))
    lane_ends: list[datetime] = []
    result = []
    for start, end, node_id, original in parsed:
        lane_index = next(
            (index for index, last_end in enumerate(lane_ends) if last_end < start),
            len(lane_ends),
        )
        if lane_index == len(lane_ends):
            lane_ends.append(end)
        else:
            lane_ends[lane_index] = end
        result.append({
            **original,
            "start": _iso(start),
            "end": _iso(end),
            "lane_index": lane_index,
        })
    return result


def decimate_context_points(
    points: Sequence[dict[str, Any]],
    *,
    max_points: int = DEFAULT_CONTEXT_POINT_LIMIT,
    preserve_call_ids: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Sample context points by time-bucket min/max while retaining semantic points.

    The limit is a target: endpoints, the global peak and event-adjacent calls
    remain even when that makes the result larger than ``max_points``.
    """
    if max_points < 2:
        raise UsageChartError("context point limit must be at least 2")
    ordered = sorted(
        points,
        key=lambda point: (_parse_time(point["timestamp"]), str(point["call_id"])),
    )
    if len(ordered) <= max_points:
        return ordered
    if not ordered:
        return []
    selected: dict[str, dict[str, Any]] = {}

    def keep(point: dict[str, Any]) -> None:
        selected[str(point["call_id"])] = point

    keep(ordered[0])
    keep(ordered[-1])
    peak = max(
        ordered,
        key=lambda point: (
            int(point["context_size"]),
            -_parse_time(point["timestamp"]).timestamp(),
            str(point["call_id"]),
        ),
    )
    keep(peak)
    for point in ordered:
        if str(point["call_id"]) in preserve_call_ids:
            keep(point)

    available = max(1, (max_points - len(selected) + 1) // 2)
    start = _parse_time(ordered[0]["timestamp"])
    end = _parse_time(ordered[-1]["timestamp"])
    duration = max((end - start).total_seconds(), 1.0)
    buckets: dict[int, list[dict[str, Any]]] = {}
    for point in ordered:
        elapsed = (_parse_time(point["timestamp"]) - start).total_seconds()
        bucket_index = min(available - 1, int(elapsed / duration * available))
        buckets.setdefault(bucket_index, []).append(point)
    for bucket in buckets.values():
        keep(min(bucket, key=lambda point: (int(point["context_size"]), point["timestamp"], point["call_id"])))
        keep(max(bucket, key=lambda point: (int(point["context_size"]), point["timestamp"], point["call_id"])))
    return sorted(
        selected.values(),
        key=lambda point: (_parse_time(point["timestamp"]), str(point["call_id"])),
    )


def _activity_lanes(
    nodes: Sequence[Node],
    selected_ids: set[str],
    *,
    point_limit: int,
) -> list[dict[str, Any]]:
    intervals = []
    node_by_id = {node.node_id: node for node in nodes}
    for node in nodes:
        if node.node_id not in selected_ids:
            continue
        span = _interval(node)
        if span is not None:
            intervals.append({"node_id": node.node_id, "start": _iso(span[0]), "end": _iso(span[1])})
    packed = pack_lane_intervals(intervals)
    result = []
    for interval in packed:
        node = node_by_id[interval["node_id"]]
        points = [
            _call_point(call, node, dataset="swim-point")
            for call in sorted(node.calls, key=_call_order)
        ]
        preserve_ids = _event_neighbor_call_ids(node)
        sampled = decimate_context_points(
            points, max_points=point_limit, preserve_call_ids=preserve_ids,
        )
        events = [
            _event_point(event, node, dataset="swim-event") for event in node.events
        ]
        events.extend(_switch_events(node, dataset="swim-switch"))
        events.sort(key=lambda event: (event["timestamp"], event["kind"], event["chart_id"]))
        result.append({
            "chart_id": _stable_id("lane", node.node_id),
            "node_id": node.node_id,
            "parent_id": node.parent_id,
            "session_id": node.session_id,
            "agent_id": node.agent_id,
            "label": _node_label(node),
            "kind": node.kind,
            "lane_index": interval["lane_index"],
            "start": interval["start"],
            "end": interval["end"],
            "clipped_before": node.clipped_before,
            "clipped_after": node.clipped_after,
            "peak_context": max((call.context_size for call in node.calls), default=0),
            "peak_call_id": max(
                node.calls,
                key=lambda call: (call.context_size, -call.timestamp.timestamp(), call.call_id),
                default=None,
            ).call_id if node.calls else None,
            "point_count_before_decimation": len(points),
            "point_count_after_decimation": len(sampled),
            "decimation": {
                "strategy": "time-bucket-min-max",
                "target_max_points": point_limit,
                "preserves_global_peak": True,
                "preserves_unexplained_drop_neighbors": True,
            },
            "points": sampled,
            "events": events,
        })
    return sorted(result, key=lambda lane: (lane["lane_index"], lane["start"], lane["node_id"]))


def _context_series(
    nodes: Sequence[Node],
    calls: Sequence[Call],
    *,
    top_n: int,
    point_limit: int,
) -> dict[str, Any]:
    by_session: dict[str, list[Node]] = {}
    for node in nodes:
        by_session.setdefault(node.session_id, []).append(node)
    session_scores = {
        session_id: _sum_weighted(call for node in items for call in node.calls)
        for session_id, items in by_session.items()
    }
    selected_sessions = sorted(
        by_session,
        key=lambda session_id: (-session_scores[session_id], session_id),
    )[:3]
    all_peak = max((call.context_size for call in calls), default=0)
    sessions = []
    for session_id in selected_sessions:
        session_nodes = by_session[session_id]
        ranked_nodes = sorted(
            session_nodes,
            key=lambda node: (
                -_sum_weighted(node.calls), node.node_id,
            ),
        )
        chosen = ranked_nodes[:top_n]
        series = []
        for node in chosen:
            points = [
                _call_point(call, node, dataset="context-point")
                for call in sorted(node.calls, key=_call_order)
            ]
            preserve_ids = _event_neighbor_call_ids(node)
            sampled = decimate_context_points(
                points, max_points=point_limit, preserve_call_ids=preserve_ids,
            )
            events = [
                _event_point(event, node, dataset="context-event")
                for event in node.events
            ]
            events.extend(_switch_events(node, dataset="context-switch"))
            events.sort(key=lambda event: (event["timestamp"], event["kind"], event["chart_id"]))
            series.append({
                "chart_id": _stable_id("context-series", session_id, node.node_id),
                "node_id": node.node_id,
                "parent_id": node.parent_id,
                "session_id": session_id,
                "agent_id": node.agent_id,
                "label": _node_label(node),
                "peak_context": max((call.context_size for call in node.calls), default=0),
                "point_count_before_decimation": len(points),
                "point_count_after_decimation": len(sampled),
                "points": sampled,
                "events": events,
            })
        sessions.append({
            "session_id": session_id,
            "label": f"session {session_id}",
            "self_weighted_units": session_scores[session_id],
            "selected_node_ids": [node.node_id for node in chosen],
            "omitted_node_count": max(0, len(ranked_nodes) - len(chosen)),
            "series": series,
        })
    return {
        "metric": "context_size_tokens",
        "scale": "absolute_shared_y_scale",
        "shared_y_min": 0,
        "shared_y_max": all_peak,
        "session_limit": 3,
        "session_selection": "descending subtree self weighted units, then session_id",
        "agent_top_n": top_n,
        "agent_selection": "descending node self weighted units, then node_id",
        "sessions": sessions,
    }


def _group_for_call(call: Call, node: Node, group_by: str) -> tuple[str, str, str]:
    if group_by == "agent":
        return f"agent:{node.node_id}", node.node_id, _node_label(node)
    if group_by == "session":
        return f"session:{call.session_id}", call.session_id, f"session {call.session_id}"
    if group_by == "model":
        return f"model:{call.model}", call.model, call.model
    if group_by == "effort":
        value = call.effort or "unknown"
        return f"effort:{value}", value, value
    value = call.topic or node.topic or "unlabeled"
    return f"topic:{value}", value, value


def _bucket_grid(window: Window) -> tuple[int, list[tuple[datetime, datetime]]]:
    span = (window.until - window.since).total_seconds()
    if span <= 0:
        raise UsageChartError("chart window must have positive duration")
    # Five-minute buckets for ordinary ranges, growing in five-minute steps up
    # to fifteen minutes as the requested span grows.
    seconds = max(300, min(900, int(math.ceil(span / 288 / 300) * 300)))
    count = int(math.ceil(span / seconds))
    buckets = []
    for index in range(count):
        start = window.since + timedelta(seconds=index * seconds)
        end = min(window.until, start + timedelta(seconds=seconds))
        buckets.append((start, end))
    return seconds, buckets


def _cost_buckets(
    nodes: Sequence[Node],
    calls: Sequence[Call],
    window: Window,
    *,
    top_n: int,
    group_by: str,
    anchors: Sequence[dict[str, Any]],
    calibration: dict[str, Any] | None,
    coverage_complete: bool,
    scope_is_full: bool,
) -> dict[str, Any]:
    nodes_by_call = {
        call.call_id: node
        for node in nodes
        for call in node.calls
    }
    grouped: dict[str, dict[str, Any]] = {}
    for call in calls:
        node = nodes_by_call.get(call.call_id)
        if node is None:
            raise UsageChartError(f"call {call.call_id!r} has no self owner node")
        group_id, value, label = _group_for_call(call, node, group_by)
        entry = grouped.setdefault(group_id, {
            "group_id": group_id,
            "group_value": value,
            "label": label,
            "kind": "value",
            "calls": [],
            "member_ids": set(),
        })
        entry["calls"].append(call)
        entry["member_ids"].add(value)
    ranked = sorted(
        grouped.values(),
        key=lambda group: (-_sum_weighted(group["calls"]), group["group_id"]),
    )
    chosen = ranked[:top_n]
    omitted = ranked[top_n:]
    groups_data: list[dict[str, Any]] = []
    seconds, bucket_grid = _bucket_grid(window)
    for group in chosen:
        group_calls = sorted(group["calls"], key=_call_order)
        group_data = {
            "chart_id": _stable_id("cost-group", group_by, group["group_id"]),
            "group_id": group["group_id"],
            "group_value": group["group_value"],
            "label": group["label"],
            "kind": group["kind"],
            "member_ids": sorted(group["member_ids"]),
            "call_count": len(group_calls),
            "raw_token_units": _raw_total(group_calls),
            "weighted_units": _sum_weighted(group_calls),
            "api_equivalent_usd": _sum_usd(group_calls),
            "observed_local_share": None,
            "buckets": [],
        }
        for start, end in bucket_grid:
            in_bucket = [call for call in group_calls if start <= call.timestamp < end]
            group_data["buckets"].append(_cost_bucket_point(
                group["group_id"], in_bucket, start, end,
            ))
        groups_data.append(group_data)
    if omitted:
        omitted_calls = sorted(
            [call for group in omitted for call in group["calls"]], key=_call_order,
        )
        other = {
            "chart_id": _stable_id("cost-group", group_by, "other"),
            "group_id": f"other:{group_by}",
            "group_value": "other",
            "label": f"other ({len(omitted)} groups)",
            "kind": "other",
            "member_ids": sorted(group["group_id"] for group in omitted),
            "omitted_group_count": len(omitted),
            "call_count": len(omitted_calls),
            "raw_token_units": _raw_total(omitted_calls),
            "weighted_units": _sum_weighted(omitted_calls),
            "api_equivalent_usd": _sum_usd(omitted_calls),
            "observed_local_share": None,
            "buckets": [],
        }
        for start, end in bucket_grid:
            in_bucket = [call for call in omitted_calls if start <= call.timestamp < end]
            other["buckets"].append(_cost_bucket_point(
                other["group_id"], in_bucket, start, end,
            ))
        groups_data.append(other)
    total_weighted = _sum_weighted(calls)
    for group in groups_data:
        group["observed_local_share"] = (
            group["weighted_units"] / total_weighted if total_weighted else 0.0
        )
    bucket_rows = []
    for start, end in bucket_grid:
        in_bucket = [call for call in calls if start <= call.timestamp < end]
        bucket_rows.append({
            "chart_id": _stable_id("cost-total", _iso(start), _iso(end)),
            "start": _iso(start),
            "end": _iso(end),
            "call_count": len(in_bucket),
            "call_ids": sorted(call.call_id for call in in_bucket),
            "raw_token_units": _raw_total(in_bucket),
            "weighted_units": _sum_weighted(in_bucket),
            "api_equivalent_usd": _sum_usd(in_bucket),
        })
    boundaries, intervals = _window_boundaries(nodes, anchors, window)
    percentage_line = _cumulative_percentage_line(
        nodes,
        window,
        bucket_grid,
        anchors=anchors,
        calibration=calibration,
        coverage_complete=coverage_complete,
        scope_is_full=scope_is_full,
    )
    return {
        "metric": "weighted_units",
        "unit": "experimental_weighted_units",
        "weight_profile": "selected profile in the price extension; not subscription usage",
        "group_by": group_by,
        "rank_basis": "self weighted units",
        "top_n": top_n,
        "tie_breaker": "group_id ascending",
        "bucket_seconds": seconds,
        "bucket_origin": _iso(window.since),
        "groups": groups_data,
        "buckets": bucket_rows,
        "call_count": len(calls),
        "raw_token_units": _raw_total(calls),
        "weighted_units": total_weighted,
        "api_equivalent_usd": _sum_usd(calls),
        "observed_local_share_definition": "local group weighted units / selected report weighted units; not account usage",
        "window_boundaries": boundaries,
        "observed_reset_intervals": intervals,
        "cumulative_percentage_line": percentage_line,
    }


def _cost_bucket_point(
    group_id: str,
    in_bucket: Sequence[Call],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    return {
        "chart_id": _stable_id("cost-bucket", group_id, _iso(start), _iso(end)),
        "start": _iso(start),
        "end": _iso(end),
        "call_count": len(in_bucket),
        "call_ids": sorted(call.call_id for call in in_bucket),
        "raw_token_units": _raw_total(in_bucket),
        "weighted_units": _sum_weighted(in_bucket),
        "api_equivalent_usd": _sum_usd(in_bucket),
    }


def _window_boundaries(
    nodes: Sequence[Node],
    anchors: Sequence[dict[str, Any]],
    window: Window,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, reset_at: datetime, source: str, evidence_id: str) -> None:
        if not window.since <= reset_at < window.until:
            return
        key = (kind, _iso(reset_at) or "")
        item = grouped.setdefault(key, {
            "kind": kind,
            "reset_at": _iso(reset_at),
            "sources": set(),
            "evidence_ids": set(),
        })
        item["sources"].add(source)
        item["evidence_ids"].add(evidence_id)

    for node in nodes:
        for event in node.events:
            if event.reset_at is None:
                continue
            if event.limit_kind == "five_hour":
                kind = "five_hour"
            elif event.limit_kind == "weekly":
                kind = "weekly"
            else:
                continue
            add(kind, event.reset_at, "threshold_event", event.event_id)
    for anchor in anchors:
        kind = anchor.get("kind")
        reset_text = anchor.get("reset_at")
        anchor_id = anchor.get("anchor_id")
        if kind not in {"five_hour", "weekly"} or not isinstance(reset_text, str):
            continue
        if not isinstance(anchor_id, str):
            continue
        try:
            reset_at = _parse_time(reset_text)
        except (TypeError, ValueError, UsageChartError):
            continue
        add(kind, reset_at, "operator_ui_anchor", anchor_id)
    boundaries = [
        {
            "chart_id": _stable_id("window-boundary", kind, reset_text),
            "kind": kind,
            "reset_at": reset_text,
            "sources": sorted(item["sources"]),
            "evidence_ids": sorted(item["evidence_ids"]),
            "interpretation": "observed reset boundary; start is unknown without a paired reset",
        }
        for (kind, reset_text), item in sorted(grouped.items())
    ]
    intervals = []
    duration_by_kind = {"five_hour": timedelta(hours=5), "weekly": timedelta(days=7)}
    for kind in sorted(duration_by_kind):
        entries = [
            item for item in boundaries if item["kind"] == kind
        ]
        ordered = sorted(entries, key=lambda item: (item["reset_at"], item["chart_id"]))
        for previous, current in pairwise(ordered):
            start = _parse_time(previous["reset_at"])
            end = _parse_time(current["reset_at"])
            if end - start != duration_by_kind[kind]:
                continue
            intervals.append({
                "chart_id": _stable_id("observed-window", kind, previous["reset_at"], current["reset_at"]),
                "kind": kind,
                "model": "observed-reset",
                "start": previous["reset_at"],
                "end": current["reset_at"],
            })
    return boundaries, sorted(intervals, key=lambda item: (item["start"], item["kind"]))


def _cumulative_percentage_line(
    nodes: Sequence[Node],
    window: Window,
    bucket_grid: Sequence[tuple[datetime, datetime]],
    *,
    anchors: Sequence[dict[str, Any]],
    calibration: dict[str, Any] | None,
    coverage_complete: bool,
    scope_is_full: bool,
) -> dict[str, Any]:
    reason = "no identifiable calibration is available; anchors and rejections alone do not establish capacity"
    if not calibration or calibration.get("capacity_fit_status") != "fit":
        return {"status": "unknown", "metric": "estimated_limit_pct", "reason": reason, "series": []}
    if not coverage_complete:
        reason = "source coverage is incomplete for the calibration interval"
        return {"status": "unknown", "metric": "estimated_limit_pct", "reason": reason, "series": []}
    if not scope_is_full:
        reason = "a project or session filter selects only part of local usage"
        return {"status": "unknown", "metric": "estimated_limit_pct", "reason": reason, "series": []}
    anchor_by_id = {
        anchor.get("anchor_id"): anchor for anchor in anchors
        if isinstance(anchor.get("anchor_id"), str)
    }
    calls = [call for node in nodes for call in node.calls]
    series = []
    for group in calibration.get("groups", []):
        if not isinstance(group, dict) or group.get("fit_status") not in {
            "fit-account-coverage", "fit-local-lower-bound",
        }:
            continue
        try:
            start = _parse_time(group["window_start"])
            reset_at = _parse_time(group["window_reset_at"])
            capacity = float(group["capacity_units"])
        except (KeyError, TypeError, ValueError, UsageChartError):
            continue
        if not math.isfinite(capacity) or capacity <= 0 or start < window.since or start >= window.until:
            continue
        fitted_anchors = group.get("anchor_fits")
        if not isinstance(fitted_anchors, list) or len(fitted_anchors) < 2:
            continue
        fit_times: dict[str, datetime] = {}
        invalid_fit = False
        for fit in fitted_anchors:
            if not isinstance(fit, dict) or fit.get("anchor_id") not in anchor_by_id:
                invalid_fit = True
                break
            try:
                observed_at = _parse_time(fit["observed_at"])
            except (KeyError, TypeError, ValueError, UsageChartError):
                invalid_fit = True
                break
            if not window.since <= observed_at < window.until:
                invalid_fit = True
                break
            fit_times[str(fit["anchor_id"])] = observed_at
        if invalid_fit:
            continue
        coverage = group.get("coverage_assumptions")
        if not isinstance(coverage, list) or len(set(coverage)) != 1:
            continue
        all_account = coverage == ["all-account-usage-observed"]
        metric = "estimated_limit_pct" if all_account else "observed_local_contribution_pct"
        entries = []
        evaluation_times = {end for _, end in bucket_grid}
        evaluation_times.update(fit_times.values())
        for timestamp in sorted(evaluation_times):
            if timestamp < start or timestamp > reset_at or timestamp < window.since:
                continue
            local_calls = [call for call in calls if start <= call.timestamp < timestamp]
            units = _sum_weighted(local_calls)
            entries.append({
                "chart_id": _stable_id("cumulative-percent", group["kind"], group["window_reset_at"], _iso(timestamp)),
                "timestamp": _iso(timestamp),
                "weighted_units": units,
                "percentage": units * 100.0 / capacity,
                "call_count": len(local_calls),
                "source_anchor_ids": sorted(
                    fit["anchor_id"] for fit in fitted_anchors
                    if isinstance(fit, dict) and fit_times.get(str(fit.get("anchor_id"))) == timestamp
                ),
            })
        if not entries:
            continue
        series.append({
            "chart_id": _stable_id("cumulative-series", group["kind"], group["window_start"], group["window_reset_at"]),
            "kind": group["kind"],
            "start": group["window_start"],
            "reset_at": group["window_reset_at"],
            "metric": metric,
            "fit_status": group["fit_status"],
            "capacity_interpretation": group.get("capacity_interpretation"),
            "coverage_assumptions": coverage,
            "anchor_ids": sorted(group.get("anchor_ids", [])),
            "points": entries,
        })
    if not series:
        return {
            "status": "unknown",
            "metric": "estimated_limit_pct",
            "reason": "calibration does not cover a complete, visible report interval",
            "series": [],
        }
    return {
        "status": "available",
        "metric": "per-series; see metric for account estimate versus local contribution",
        "reason": None,
        "series": series,
    }


def build_chart_data(
    roots: Sequence[Node],
    window: Window,
    *,
    weights: UsageWeights,
    top_n: int = DEFAULT_TOP_N,
    group_by: str = "agent",
    anchors: Sequence[dict[str, Any]] = (),
    calibration: dict[str, Any] | None = None,
    coverage_complete: bool = True,
    scope_is_full: bool = True,
    context_point_limit: int = DEFAULT_CONTEXT_POINT_LIMIT,
) -> dict[str, Any]:
    """Build four chart datasets without changing or decimating ledger totals."""
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise UsageChartError("top_n must be a positive integer")
    if group_by not in GROUP_BY_VALUES:
        raise UsageChartError(f"group_by must be one of: {', '.join(GROUP_BY_VALUES)}")
    if context_point_limit < 2:
        raise UsageChartError("context_point_limit must be at least 2")
    if weights.default_weight_profile not in weights.weight_profiles:
        raise UsageChartError("selected weight profile is missing from its weights table")
    nodes = list(_walk_nodes(roots))
    node_ids = [node.node_id for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise UsageChartError("usage chart input contains duplicate node IDs")
    calls = sorted([call for node in nodes for call in node.calls], key=_call_order)
    call_ids = [call.call_id for call in calls]
    if len(call_ids) != len(set(call_ids)):
        raise UsageChartError("usage chart input contains duplicate call IDs")
    for call in calls:
        if not window.since <= call.timestamp < window.until:
            raise UsageChartError("chart input contains a call outside its report window")
        _weighted(call)
    if any(
        not window.since <= event.timestamp < window.until
        for node in nodes for event in node.events
    ):
        raise UsageChartError("chart input contains an event outside its report window")
    node_self_cost = {
        node.node_id: _sum_weighted(node.calls) for node in nodes
    }
    ranked_nodes = sorted(nodes, key=lambda node: (-node_self_cost[node.node_id], node.node_id))
    top_nodes = ranked_nodes[:top_n]
    top_node_ids = {node.node_id for node in top_nodes}
    swim_lanes = _activity_lanes(
        nodes, top_node_ids, point_limit=context_point_limit,
    )
    context = _context_series(
        nodes, calls, top_n=top_n, point_limit=context_point_limit,
    )
    cost = _cost_buckets(
        nodes,
        calls,
        window,
        top_n=top_n,
        group_by=group_by,
        anchors=anchors,
        calibration=calibration,
        coverage_complete=coverage_complete,
        scope_is_full=scope_is_full,
    )
    composition_rows = []
    for node in top_nodes:
        owned_calls = sorted(node.calls, key=_call_order)
        components = _weighted_components(owned_calls, weights)
        composition_rows.append({
            "chart_id": _stable_id("composition-node", node.node_id),
            "node_id": node.node_id,
            "session_id": node.session_id,
            "agent_id": node.agent_id,
            "label": _node_label(node),
            "call_count": len(owned_calls),
            "call_ids": [call.call_id for call in owned_calls],
            "raw_token_units": _raw_total(owned_calls),
            "weighted_units": _sum_weighted(owned_calls),
            "api_equivalent_usd": _sum_usd(owned_calls),
            "components": components,
        })
    omitted_nodes = ranked_nodes[top_n:]
    if omitted_nodes:
        omitted_calls = sorted(
            [call for node in omitted_nodes for call in node.calls], key=_call_order,
        )
        composition_rows.append({
            "chart_id": _stable_id("composition-other", "node"),
            "node_id": None,
            "session_id": None,
            "agent_id": None,
            "label": f"other ({len(omitted_nodes)} agents)",
            "kind": "other",
            "omitted_node_count": len(omitted_nodes),
            "member_node_ids": sorted(node.node_id for node in omitted_nodes),
            "call_count": len(omitted_calls),
            "call_ids": [call.call_id for call in omitted_calls],
            "raw_token_units": _raw_total(omitted_calls),
            "weighted_units": _sum_weighted(omitted_calls),
            "api_equivalent_usd": _sum_usd(omitted_calls),
            "components": _weighted_components(omitted_calls, weights),
        })
    global_peak = max(
        calls,
        key=lambda call: (call.context_size, -call.timestamp.timestamp(), call.call_id),
        default=None,
    )
    node_by_call_id = {
        call.call_id: node.node_id for node in nodes for call in node.calls
    }
    node_for_call = {
        call.call_id: node for node in nodes for call in node.calls
    }
    total_components = _weighted_components(calls, weights)
    return {
        "schema_version": CHART_SCHEMA_VERSION,
        "source_schema_version": "usage-report-v1",
        "window": {
            "since": _iso(window.since),
            "until": _iso(window.until),
            "semantics": "[since, until), UTC",
        },
        "selection": {
            "top_n": top_n,
            "rank_basis": "self weighted units",
            "tie_breaker": "node_id ascending",
            "group_by": group_by,
            "cost_group_membership": "each deduplicated call belongs to exactly one self-cost group",
            "cost_other_bucket": "one exact residual group per grouping; parent subtree totals are never stacked",
            "context_sessions": 3,
            "context_point_target": context_point_limit,
            "context_decimation": "time-bucket min/max with endpoints, global peak, explicit drops and switch points retained",
        },
        "valuation": {
            "weighted_profile": weights.default_weight_profile,
            "weighted_profile_description": weights.weight_profile_descriptions.get(weights.default_weight_profile),
            "weights_sha256": weights.source_sha256,
            "weighted_units_are_subscription_usage": False,
            "api_equivalent_usd_is_billed_cost": False,
            "observed_local_share": "weighted group / selected report weighted total; not an account limit percentage",
        },
        "totals": {
            "call_count": len(calls),
            "raw_token_units": _raw_total(calls),
            "weighted_units": _sum_weighted(calls),
            "api_equivalent_usd": _sum_usd(calls),
            "tokens": _token_counts(calls),
            "weighted_components": total_components,
        },
        "global_peak": (
            {
                "call_id": global_peak.call_id,
                "node_id": node_by_call_id[global_peak.call_id],
                "timestamp": _iso(global_peak.timestamp),
                "context_size": global_peak.context_size,
            }
            if global_peak is not None else None
        ),
        "call_lookup": [
            _hover_record(call, node_for_call[call.call_id]) for call in calls
        ],
        "color_keys": _color_keys(calls),
        "datasets": {
            "swim_lanes": {
                "metric": "context_size_tokens",
                "visual": "per-agent context line within a packed lane",
                "lane_packing": "greedy interval partition; inclusive activity spans cannot overlap within a lane",
                "selected_node_ids": [node.node_id for node in top_nodes],
                "omitted_node_count": max(0, len(nodes) - len(top_nodes)),
                "global_peak_call_id": global_peak.call_id if global_peak else None,
                "lanes": swim_lanes,
            },
            "context_over_time": context,
            "weighted_cost_buckets": cost,
            "cost_composition": {
                "metric": "weighted_units",
                "unit": "experimental_weighted_units",
                "components": [name for name, _, _ in _TOKEN_FIELDS],
                "rows": composition_rows,
            },
        },
        "privacy": {
            "message_content": "excluded",
            "thinking_content": "excluded; only output-subset token counts are present",
            "tool_content": "excluded",
            "filesystem_paths": "excluded; source identifiers are opaque",
        },
    }


__all__ = [
    "CHART_SCHEMA_VERSION", "DEFAULT_CONTEXT_POINT_LIMIT", "DEFAULT_TOP_N",
    "GROUP_BY_VALUES", "ChartOptions", "UsageChartError", "build_chart_data",
    "decimate_context_points", "pack_lane_intervals",
]
