"""Synthetic contracts for the usage-report chart data extension."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nyxloom.session_extract.usage import (
    Call,
    ChartOptions,
    Corpus,
    Node,
    Session,
    UsageEvent,
    UsageChartError,
    Window,
    build_chart_data,
    build_report,
    load_weights,
    pack_lane_intervals,
    resolve_window,
)
from nyxloom.session_extract.usage import charts as chart_module


UTC = timezone.utc
START = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)
END = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
WINDOW = Window(START, END, "2026-10-28T08:00:00Z", "2026-10-28T10:00:00Z")
WEIGHTS = load_weights()


def _call(
    call_id: str,
    minute: int,
    *,
    node_id: str,
    session_id: str = "session-1",
    agent_id: str | None = None,
    model: str = "claude-sonnet-5-5",
    effort: str | None = "medium",
    context: int = 100,
    input_tokens: int = 2,
    output_tokens: int = 3,
    cache_read: int = 5,
    cache_5m: int = 7,
    cache_1h: int = 11,
    weighted: float = 40.0,
    topic: str | None = None,
) -> Call:
    timestamp = START + timedelta(minutes=minute)
    return Call(
        call_id=call_id,
        request_id=f"request-{call_id}",
        request_ids=[f"request-{call_id}"],
        message_id=f"message-{call_id}",
        timestamp=timestamp,
        session_id=session_id,
        agent_id=agent_id,
        model=model,
        effort=effort,
        speed="standard",
        service_tier="standard",
        inference_geo="global",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_5m_tokens=cache_5m,
        cache_write_1h_tokens=cache_1h,
        cache_write_unclassified_tokens=0,
        thinking_tokens=1,
        context_size=context,
        project=None,
        source_id=f"source-{node_id}",
        source_line=minute + 1,
        provenance=[],
        source_ids=[f"source-{node_id}"],
        weighted_cost=weighted,
        usd_cost=0.01,
        topic=topic,
    )


def _node(
    node_id: str,
    calls: list[Call],
    *,
    parent_id: str | None = None,
    session_id: str = "session-1",
    agent_id: str | None = None,
    topic: str | None = None,
    events: list[UsageEvent] | None = None,
) -> Node:
    return Node(
        node_id=node_id,
        parent_id=parent_id,
        session_id=session_id,
        agent_id=agent_id,
        topic=topic,
        kind="main" if agent_id is None else "subagent",
        calls=calls,
        events=events or [],
    )


def _fixture_nodes() -> list[Node]:
    root_calls = [
        _call("c1", 1, node_id="root", context=100, weighted=10),
        _call("c2", 2, node_id="root", context=700, weighted=20,
              model="claude-opus-5-5", effort="high"),
    ]
    child_a_calls = [
        _call("c3", 3, node_id="agent-a", agent_id="agent-a", context=500,
              weighted=30, topic="topic a"),
        _call("c4", 4, node_id="agent-a", agent_id="agent-a", context=60,
              weighted=40, model="claude-fable-5-1", effort="xhigh", topic="topic a"),
    ]
    child_b_calls = [
        _call("c5", 80, node_id="agent-b", agent_id="agent-b", context=300,
              weighted=50, topic="topic b"),
    ]
    return [
        _node("session-1:root", root_calls, topic="root topic", events=[]),
        _node("session-1:agent-a", child_a_calls, parent_id="session-1:root",
              agent_id="agent-a", topic="topic a"),
        _node("session-1:agent-b", child_b_calls, parent_id="session-1:root",
              agent_id="agent-b", topic="topic b"),
    ]


@pytest.mark.parametrize("group_by", ["agent", "topic", "model", "effort", "session"])
def test_top_n_and_other_partition_exact_self_cost_by_group_and_bucket(group_by):
    nodes = _fixture_nodes()
    nodes.append(_node(
        "session-2:root",
        [_call("c6", 90, node_id="session-2:root", session_id="session-2",
              model="claude-haiku-5-5", effort="low", context=80, weighted=60)],
        session_id="session-2",
        topic="second session",
    ))
    chart = build_chart_data(
        nodes,
        WINDOW,
        weights=WEIGHTS,
        top_n=1,
        group_by=group_by,
    )
    dataset = chart["datasets"]["weighted_cost_buckets"]
    assert dataset["group_by"] == group_by
    assert any(group["kind"] == "other" for group in dataset["groups"])
    assert sum(group["weighted_units"] for group in dataset["groups"]) == sum(
        call.weighted_cost for node in nodes for call in node.calls
    )
    assert len(dataset["groups"]) == 2
    assert sum(group["raw_token_units"] for group in dataset["groups"]) == sum(
        call.raw_token_units for node in nodes for call in node.calls
    )
    for bucket_index, bucket in enumerate(dataset["buckets"]):
        assert sum(
            group["buckets"][bucket_index]["weighted_units"]
            for group in dataset["groups"]
        ) == bucket["weighted_units"]
        assert sum(
            group["buckets"][bucket_index]["raw_token_units"]
            for group in dataset["groups"]
        ) == bucket["raw_token_units"]


def test_decimation_keeps_global_peak_and_both_sides_of_structural_drop():
    chart = build_chart_data(_fixture_nodes(), WINDOW, weights=WEIGHTS, top_n=12)
    points = chart["datasets"]["swim_lanes"]["lanes"][0]["points"]
    assert max(point["context_size"] for point in points) == 700

    from nyxloom.session_extract.usage.charts import decimate_context_points

    dense = [
        {"call_id": f"point-{index}", "timestamp": (START + timedelta(seconds=index)).isoformat(),
         "context_size": 1000 if index == 37 else (50 if index == 38 else index)}
        for index in range(100)
    ]
    sampled = decimate_context_points(
        dense, max_points=8, preserve_call_ids={"point-36", "point-38"},
    )
    sampled_ids = {point["call_id"] for point in sampled}
    assert "point-37" in sampled_ids
    assert {"point-36", "point-38"} <= sampled_ids


def test_structural_compaction_preserves_neighboring_calls_for_context_decimation():
    calls = [
        _call(
            f"call-{index}", index, node_id="agent-a",
            context=900 if index == 50 else 80 if index == 51 else index + 1,
        )
        for index in range(100)
    ]
    boundary = UsageEvent(
        event_id="compact-event",
        kind="compaction",
        timestamp=START + timedelta(minutes=50, seconds=30),
        session_id="session-1",
        agent_id="agent-a",
        source_record_id="compact-record",
        source_id="source-agent-a",
        source_line=51,
        pre_tokens=900,
        post_tokens=80,
    )
    node = _node("session-1:agent-a", calls, agent_id="agent-a", events=[boundary])
    chart = build_chart_data(
        [node], WINDOW, weights=WEIGHTS, top_n=1, context_point_limit=8,
    )
    lane = chart["datasets"]["swim_lanes"]["lanes"][0]
    point_ids = {point["call_id"] for point in lane["points"]}
    assert {"call-50", "call-51"} <= point_ids
    assert lane["peak_call_id"] == "call-50"
    assert lane["events"][0]["event_id"] == "compact-event"


def test_lane_packing_never_overlaps_inclusive_activity_intervals():
    lanes = pack_lane_intervals([
        {"node_id": "a", "start": "2026-10-28T08:00:00Z", "end": "2026-10-28T08:30:00Z"},
        {"node_id": "b", "start": "2026-10-28T08:10:00Z", "end": "2026-10-28T08:20:00Z"},
        {"node_id": "c", "start": "2026-10-28T08:30:01Z", "end": "2026-10-28T08:40:00Z"},
    ])
    assert {lane["node_id"]: lane["lane_index"] for lane in lanes} == {
        "a": 0, "b": 1, "c": 0,
    }
    for left in lanes:
        for right in lanes:
            if left["lane_index"] == right["lane_index"] and left["node_id"] < right["node_id"]:
                assert left["end"] < right["start"] or right["end"] < left["start"]


def test_window_chart_shows_observed_boundaries_and_only_verified_reset_pairs():
    events = [
        UsageEvent(
            event_id=f"reset-{index}", kind="api_rejection",
            timestamp=START + timedelta(hours=index + 1), session_id="session-1",
            agent_id=None, source_record_id=f"record-{index}", source_id="source-root",
            source_line=index + 1, limit_kind="five_hour",
            reset_at=START + timedelta(hours=3 + index * 5),
        )
        for index in range(2)
    ]
    root = _node("session-1:root", _fixture_nodes()[0].calls, events=events)
    long_window = Window(
        START, START + timedelta(hours=9), "start", "end",
    )
    chart = build_chart_data([root], long_window, weights=WEIGHTS, top_n=12)
    cost = chart["datasets"]["weighted_cost_buckets"]
    assert [item["reset_at"] for item in cost["window_boundaries"]] == [
        "2026-10-28T11:00:00Z", "2026-10-28T16:00:00Z",
    ]
    intervals = cost["observed_reset_intervals"]
    assert len(intervals) == 1
    assert intervals[0]["chart_id"].startswith("chart-observed-window-")
    assert intervals[0]["kind"] == "five_hour"
    assert intervals[0]["model"] == "observed-reset"
    assert intervals[0]["start"] == "2026-10-28T11:00:00Z"
    assert intervals[0]["end"] == "2026-10-28T16:00:00Z"


def test_switch_events_and_chart_ids_are_unique_and_stable():
    nodes = _fixture_nodes()
    first = build_chart_data(nodes, WINDOW, weights=WEIGHTS, top_n=12)
    second = build_chart_data(nodes, WINDOW, weights=WEIGHTS, top_n=12)
    assert first == second
    chart_ids = []

    def walk(value):
        if isinstance(value, dict):
            if "chart_id" in value:
                chart_ids.append(value["chart_id"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(first["datasets"])
    assert len(chart_ids) == len(set(chart_ids))
    event_kinds = {
        event["kind"]
        for lane in first["datasets"]["swim_lanes"]["lanes"]
        for event in lane["events"]
    }
    assert {"model_switch", "effort_switch"} <= event_kinds
    composition = first["datasets"]["cost_composition"]["rows"]
    assert sum(row["weighted_units"] for row in composition) == 150


def test_composition_weighted_components_reconcile_to_the_call_profile():
    from nyxloom.session_extract.usage.pricing import value_call

    call = _call("valued-call", 1, node_id="session-1:agent", agent_id="agent")
    valuation = value_call(call, WEIGHTS)
    call.weighted_cost = valuation.weighted_units
    call.usd_cost = valuation.api_equivalent_usd
    node = _node("session-1:agent", [call], agent_id="agent")

    chart = build_chart_data([node], WINDOW, weights=WEIGHTS, top_n=12)
    row = chart["datasets"]["cost_composition"]["rows"][0]
    component_total = sum(
        component["weighted_units"] for component in row["components"].values()
    )
    assert component_total == pytest.approx(row["weighted_units"])
    assert row["weighted_units"] == pytest.approx(valuation.weighted_units)


def test_report_chart_extension_uses_full_tree_even_when_display_is_folded():
    nodes = _fixture_nodes()
    root = nodes[0]
    root.children = nodes[1:]
    corpus = Corpus(sessions=[Session(session_id="session-1", root=root)])
    report = build_report(
        corpus,
        window=WINDOW,
        depth=0,
        generated_at=END,
        chart_options=ChartOptions(weights=WEIGHTS, top_n=12),
    )
    chart = report["extensions"]["chart_data"]
    assert chart["schema_version"] == "usage-charts-v1"
    assert chart["totals"]["weighted_units"] == 150
    assert sum(
        group["weighted_units"]
        for group in chart["datasets"]["weighted_cost_buckets"]["groups"]
    ) == 150
    assert chart["totals"]["call_count"] == report["totals"]["calls"] == len(report["calls"]) == 5
    assert {item["call_id"] for item in chart["call_lookup"]} == {
        call["call_id"] for call in report["calls"]
    }
    assert {item["node_id"] for item in chart["call_lookup"]} == {
        node.node_id for node in nodes
    }
    assert report["nodes"][0]["children"] == []


def test_report_charts_keep_ledger_ownership_when_min_share_folds_children():
    nodes = _fixture_nodes()
    root = nodes[0]
    root.children = nodes[1:]
    corpus = Corpus(sessions=[Session(session_id="session-1", root=root)])
    report = build_report(
        corpus,
        window=WINDOW,
        min_share=0.99,
        generated_at=END,
        chart_options=ChartOptions(weights=WEIGHTS, top_n=12),
    )

    chart = report["extensions"]["chart_data"]
    assert report["nodes"][0]["children"][0]["kind"] == "other"
    assert chart["totals"]["weighted_units"] == 150
    assert chart["totals"]["call_count"] == len(report["calls"]) == 5
    assert {item["node_id"] for item in chart["call_lookup"]} == {
        node.node_id for node in nodes
    }


def test_chart_helpers_validate_times_costs_and_lane_intervals():
    assert chart_module._parse_time(START) == START
    assert chart_module._parse_time("2026-10-28T08:00:00-02:00") == datetime(
        2026, 10, 28, 10, 0, tzinfo=UTC,
    )
    with pytest.raises(UsageChartError, match="must include a timezone"):
        chart_module._parse_time("2026-10-28T08:00:00")

    call = _call("edge", 1, node_id="edge")
    call.weighted_cost = None
    with pytest.raises(UsageChartError, match="valued calls"):
        chart_module._weighted(call)
    call.weighted_cost = float("inf")
    with pytest.raises(UsageChartError, match="valued calls"):
        chart_module._weighted(call)
    assert chart_module._effort_style("unlisted-effort") == {
        "dash": "1 2", "lightness": chart_module._effort_style("unlisted-effort")["lightness"],
    }
    assert chart_module._interval(_node("empty", [])) is None

    intervals = [
        {"node_id": "a", "start": "2026-10-28T08:00:00Z", "end": "2026-10-28T10:00:00Z"},
        {"node_id": "b", "start": "2026-10-28T09:00:00Z", "end": "2026-10-28T11:00:00Z"},
        {"node_id": "c", "start": "2026-10-28T10:00:00Z", "end": "2026-10-28T12:00:00Z"},
        {"node_id": "d", "start": "2026-10-28T12:00:00Z", "end": "2026-10-28T13:00:00Z"},
    ]
    packed = chart_module.pack_lane_intervals(intervals)
    assert [item["lane_index"] for item in packed] == [0, 1, 2, 0]
    assert chart_module.pack_lane_intervals([]) == []
    with pytest.raises(UsageChartError, match="non-empty node_id"):
        chart_module.pack_lane_intervals([{"start": "2026-10-28T08:00:00Z", "end": "2026-10-28T09:00:00Z"}])
    with pytest.raises(UsageChartError, match="one activity span per node"):
        chart_module.pack_lane_intervals([intervals[0], intervals[0]])
    with pytest.raises(UsageChartError, match="must not precede"):
        chart_module.pack_lane_intervals([{"node_id": "backwards", "start": "2026-10-28T10:00:00Z", "end": "2026-10-28T09:00:00Z"}])


def test_context_decimation_preserves_boundaries_peaks_and_event_neighbors():
    points = [
        {
            "call_id": f"point-{index}",
            "timestamp": (START + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
            "context_size": 1 if index != 6 else 99,
        }
        for index in range(12)
    ]
    with pytest.raises(UsageChartError, match="at least 2"):
        chart_module.decimate_context_points(points, max_points=1)
    assert chart_module.decimate_context_points([], max_points=2) == []
    sampled = chart_module.decimate_context_points(
        points, max_points=3, preserve_call_ids={"point-4"},
    )
    sampled_ids = {point["call_id"] for point in sampled}
    assert {"point-0", "point-11", "point-6", "point-4"} <= sampled_ids


def test_event_neighbors_and_grouping_cover_all_public_dimensions():
    first = _call("first", 1, node_id="agent", model="claude-sonnet-5-5", effort="low")
    second = _call("second", 2, node_id="agent", model="claude-opus-5-5", effort="high")
    node = _node("agent-node", [first, second], agent_id="agent", topic="topic")
    event_args = {
        "timestamp": START + timedelta(minutes=1, seconds=30),
        "session_id": "session-1", "agent_id": "agent", "source_record_id": None,
        "source_id": "source-agent", "source_line": 10,
    }
    node.events = [
        UsageEvent("unknown", "other", **event_args),
        UsageEvent(
            "drop", "unexplained_drop", **event_args,
            previous_call_id="first", current_call_id="second",
        ),
        UsageEvent("compact", "compaction", **event_args),
    ]
    assert chart_module._interval(node) == (first.timestamp, second.timestamp)
    assert chart_module._event_neighbor_call_ids(node) == {"first", "second"}
    assert chart_module._group_for_call(first, node, "agent")[0] == "agent:agent-node"
    assert chart_module._group_for_call(first, node, "session")[0] == "session:session-1"
    assert chart_module._group_for_call(first, node, "model")[0] == "model:claude-sonnet-5-5"
    assert chart_module._group_for_call(first, node, "effort")[0] == "effort:low"
    assert chart_module._group_for_call(first, node, "topic")[0] == "topic:topic"
    first.effort = None
    assert chart_module._group_for_call(first, node, "effort")[1] == "unknown"
    first.topic = None
    node.topic = None
    assert chart_module._group_for_call(first, node, "topic")[1] == "unlabeled"


def test_build_chart_data_rejects_invalid_inputs_and_missing_call_owners():
    nodes = _fixture_nodes()
    for top_n in (0, True, 1.5):
        with pytest.raises(UsageChartError, match="top_n must be a positive integer"):
            build_chart_data(nodes, WINDOW, weights=WEIGHTS, top_n=top_n)
    with pytest.raises(UsageChartError, match="group_by must be one of"):
        build_chart_data(nodes, WINDOW, weights=WEIGHTS, group_by="project")
    with pytest.raises(UsageChartError, match="context_point_limit must be at least 2"):
        build_chart_data(nodes, WINDOW, weights=WEIGHTS, context_point_limit=1)
    with pytest.raises(UsageChartError, match="profile is missing"):
        build_chart_data(
            nodes, WINDOW,
            weights=replace(WEIGHTS, default_weight_profile="absent"),
        )
    with pytest.raises(UsageChartError, match="duplicate node IDs"):
        build_chart_data([nodes[0], nodes[0]], WINDOW, weights=WEIGHTS)

    duplicate = _fixture_nodes()[0]
    other = _node("other-root", [duplicate.calls[0]])
    with pytest.raises(UsageChartError, match="duplicate call IDs"):
        build_chart_data([duplicate, other], WINDOW, weights=WEIGHTS)
    with pytest.raises(UsageChartError, match="outside its report window"):
        outside = _call("outside", 1, node_id="outside")
        outside.timestamp = WINDOW.until
        build_chart_data([_node("outside", [outside])], WINDOW, weights=WEIGHTS)
    with pytest.raises(UsageChartError, match="valued calls"):
        unpriced = _call("unpriced", 1, node_id="unpriced")
        unpriced.weighted_cost = None
        build_chart_data([_node("unpriced", [unpriced])], WINDOW, weights=WEIGHTS)
    with pytest.raises(UsageChartError, match="no self owner node"):
        chart_module._cost_buckets(
            [], [_call("orphan", 1, node_id="orphan")], WINDOW,
            top_n=1, group_by="agent", anchors=[], calibration=None,
            coverage_complete=True, scope_is_full=True,
        )


def test_reset_boundaries_only_pair_exact_observed_reset_durations():
    window = Window(START, START + timedelta(days=15), "start", "end")
    events = [
        UsageEvent(
            event_id="five-event", kind="threshold", timestamp=START,
            session_id="s", agent_id=None, source_record_id=None,
            source_id="src", source_line=1, limit_kind="five_hour",
            reset_at=START + timedelta(hours=5),
        ),
        UsageEvent(
            event_id="unknown-event", kind="threshold", timestamp=START,
            session_id="s", agent_id=None, source_record_id=None,
            source_id="src", source_line=2, limit_kind="unknown",
            reset_at=START + timedelta(hours=1),
        ),
        UsageEvent(
            event_id="no-reset", kind="threshold", timestamp=START,
            session_id="s", agent_id=None, source_record_id=None,
            source_id="src", source_line=3, limit_kind="weekly", reset_at=None,
        ),
        UsageEvent(
            event_id="weekly-event", kind="threshold", timestamp=START,
            session_id="s", agent_id=None, source_record_id=None,
            source_id="src", source_line=4, limit_kind="weekly",
            reset_at=START + timedelta(days=7),
        ),
    ]
    node = _node("root", [], events=events)
    anchors = [
        {"kind": "five_hour", "reset_at": (START + timedelta(hours=5)).isoformat(), "anchor_id": "anchor-five"},
        {"kind": "five_hour", "reset_at": (START + timedelta(hours=10)).isoformat(), "anchor_id": "anchor-five-next"},
        {"kind": "five_hour", "reset_at": (START + timedelta(hours=6)).isoformat(), "anchor_id": "anchor-five-mismatch"},
        {"kind": "five_hour", "reset_at": (START + timedelta(hours=15)).isoformat(), "anchor_id": "anchor-five-final"},
        {"kind": "weekly", "reset_at": (START + timedelta(days=7)).isoformat(), "anchor_id": "anchor-week"},
        {"kind": "weekly", "reset_at": (START + timedelta(days=14)).isoformat(), "anchor_id": "anchor-week-next"},
        {"kind": "invalid", "reset_at": (START + timedelta(hours=2)).isoformat(), "anchor_id": "anchor-invalid-kind"},
        {"kind": "weekly", "reset_at": 12, "anchor_id": "anchor-invalid-time-type"},
        {"kind": "weekly", "reset_at": "bad-time", "anchor_id": "anchor-invalid-time"},
        {"kind": "weekly", "reset_at": (START + timedelta(hours=2)).isoformat()},
    ]
    boundaries, intervals = chart_module._window_boundaries([node], anchors, window)
    assert len(boundaries) == 6
    assert any(
        item["kind"] == "five_hour"
        and item["start"] == chart_module._iso(START + timedelta(hours=10))
        and item["end"] == chart_module._iso(START + timedelta(hours=15))
        for item in intervals
    )
    assert any(item["kind"] == "weekly" for item in intervals)


def test_chart_activity_helpers_cover_empty_and_event_neighbor_paths():
    assert chart_module._sum_usd([replace(_call("missing-usd", 0, node_id="root"), usd_cost=None)]) is None
    with pytest.raises(UsageChartError, match="positive duration"):
        chart_module._bucket_grid(Window(START, START, "start", "end"))

    empty = _node("empty", [])
    assert chart_module._activity_lanes([empty], {empty.node_id}, point_limit=10) == []

    first = _call("first", 0, node_id="root")
    second = replace(_call("second", 60, node_id="root"), model="claude-fable-5-1")
    third = replace(_call("third", 90, node_id="root"), effort="high")
    node = _node("root", [first, second, third])
    node.events = [
        UsageEvent(
            event_id="drop", kind="unexplained_drop", timestamp=START,
            session_id="session-1", agent_id=None, source_record_id=None,
            source_id="source-root", source_line=1,
            previous_call_id=first.call_id, current_call_id="missing-call",
        ),
        UsageEvent(
            event_id="compact", kind="compaction", timestamp=START + timedelta(minutes=30),
            session_id="session-1", agent_id=None, source_record_id="compact-1",
            source_id="source-root", source_line=5,
            summary_timestamp=START + timedelta(minutes=45),
        ),
        UsageEvent(
            event_id="unrelated", kind="threshold", timestamp=START,
            session_id="session-1", agent_id=None, source_record_id=None,
            source_id="source-root", source_line=6,
        ),
        UsageEvent(
            event_id="before-all", kind="compaction", timestamp=START - timedelta(minutes=1),
            session_id="session-1", agent_id=None, source_record_id="before-all",
            source_id="source-root", source_line=7,
        ),
        UsageEvent(
            event_id="after-all", kind="compaction", timestamp=START + timedelta(hours=2),
            session_id="session-1", agent_id=None, source_record_id="after-all",
            source_id="source-root", source_line=8,
        ),
    ]
    preserved = chart_module._event_neighbor_call_ids(node)
    assert {first.call_id, second.call_id, third.call_id} <= preserved


@pytest.mark.parametrize(
    "group",
    [
        None,
        {"fit_status": "not-fit"},
        {"fit_status": "fit-account-coverage"},
        {"fit_status": "fit-account-coverage", "window_start": "bad"},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": "bad"},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": float("nan")},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": 0},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T12:00:00Z",
         "window_reset_at": "2026-10-28T13:00:00Z", "capacity_units": 100},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": 100,
         "anchor_fits": "bad"},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": 100,
         "anchor_fits": [{"anchor_id": "a"}]},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": 100,
         "anchor_fits": [{"anchor_id": "missing", "observed_at": "2026-10-28T08:30:00Z"},
                         {"anchor_id": "b", "observed_at": "2026-10-28T09:30:00Z"}]},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": 100,
         "anchor_fits": [{"anchor_id": "a", "observed_at": "bad"},
                         {"anchor_id": "b", "observed_at": "2026-10-28T09:30:00Z"}]},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": 100,
         "anchor_fits": [{"anchor_id": "a", "observed_at": "2026-10-28T07:00:00Z"},
                         {"anchor_id": "b", "observed_at": "2026-10-28T09:30:00Z"}]},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": 100,
         "anchor_fits": [{"anchor_id": "a", "observed_at": "2026-10-28T08:30:00Z"},
                         {"anchor_id": "b", "observed_at": "2026-10-28T09:30:00Z"}],
         "coverage_assumptions": "bad"},
        {"fit_status": "fit-account-coverage", "window_start": "2026-10-28T08:00:00Z",
         "window_reset_at": "2026-10-28T10:00:00Z", "capacity_units": 100,
         "anchor_fits": [{"anchor_id": "a", "observed_at": "2026-10-28T08:30:00Z"},
                         {"anchor_id": "b", "observed_at": "2026-10-28T09:30:00Z"}],
         "coverage_assumptions": ["all-account-usage-observed", "local-only-lower-bound"]},
    ],
)
def test_percentage_line_rejects_malformed_or_incomplete_fit_groups(group):
    anchors = [{"anchor_id": name} for name in ("a", "b")]
    line = chart_module._cumulative_percentage_line(
        [], WINDOW, [(START, END)], anchors=anchors,
        calibration={"capacity_fit_status": "fit", "groups": [group]},
        coverage_complete=True, scope_is_full=True,
    )
    assert line["status"] == "unknown"
    assert line["series"] == []


def test_percentage_line_is_unknown_when_fitted_observations_do_not_cover_visible_time():
    group = {
        "kind": "five_hour", "fit_status": "fit-account-coverage",
        "window_start": "2026-10-28T09:00:00Z", "window_reset_at": "2026-10-28T09:30:00Z",
        "capacity_units": 100,
        "coverage_assumptions": ["all-account-usage-observed"],
        "anchor_ids": ["a", "b"],
        "anchor_fits": [
            {"anchor_id": "a", "observed_at": "2026-10-28T08:15:00Z"},
            {"anchor_id": "b", "observed_at": "2026-10-28T08:30:00Z"},
        ],
    }
    line = chart_module._cumulative_percentage_line(
        [], WINDOW, [(START, END)], anchors=[{"anchor_id": "a"}, {"anchor_id": "b"}],
        calibration={"capacity_fit_status": "fit", "groups": [group]},
        coverage_complete=True, scope_is_full=True,
    )
    assert line["status"] == "unknown"
    assert "complete" in line["reason"]


def test_chart_data_rejects_events_outside_window_and_supports_empty_ledger():
    empty = build_chart_data([], WINDOW, weights=WEIGHTS)
    assert empty["global_peak"] is None
    assert empty["call_lookup"] == []
    assert empty["datasets"]["swim_lanes"]["lanes"] == []
    event_node = _node("event-root", [], events=[UsageEvent(
        event_id="outside-event", kind="threshold", timestamp=WINDOW.until,
        session_id="session-1", agent_id=None, source_record_id=None,
        source_id="source", source_line=1,
    )])
    with pytest.raises(UsageChartError, match="event outside its report window"):
        build_chart_data([event_node], WINDOW, weights=WEIGHTS)


def test_chart_data_keeps_limit_percentage_unknown_without_identifiable_calibration():
    chart = build_chart_data(
        _fixture_nodes(), WINDOW, weights=WEIGHTS,
        anchors=[{"anchor_id": "anchor-a", "observed_at": "2026-10-28T09:00:00Z",
                  "reset_at": "2026-10-28T10:00:00Z", "kind": "five_hour",
                  "percent": 50.0, "coverage_assumption": "all-account-usage-observed"}],
    )
    line = chart["datasets"]["weighted_cost_buckets"]["cumulative_percentage_line"]
    assert line["status"] == "unknown"
    assert line["series"] == []
    assert "calibration" in line["reason"]


def test_calibrated_percentage_line_distinguishes_account_estimate_and_local_contribution():
    anchors = [
        {"anchor_id": "anchor-a", "observed_at": "2026-10-28T08:30:00Z",
         "reset_at": "2026-10-28T10:00:00Z", "kind": "five_hour",
         "percent": 20.0, "coverage_assumption": "all-account-usage-observed"},
        {"anchor_id": "anchor-b", "observed_at": "2026-10-28T09:30:00Z",
         "reset_at": "2026-10-28T10:00:00Z", "kind": "five_hour",
         "percent": 60.0, "coverage_assumption": "all-account-usage-observed"},
    ]
    calibration = {
        "capacity_fit_status": "fit",
        "groups": [{
            "kind": "five_hour",
            "fit_status": "fit-account-coverage",
            "window_start": "2026-10-28T08:00:00Z",
            "window_reset_at": "2026-10-28T10:00:00Z",
            "capacity_units": 1000.0,
            "capacity_interpretation": "conditional estimate under all-account coverage",
            "coverage_assumptions": ["all-account-usage-observed"],
            "anchor_ids": ["anchor-a", "anchor-b"],
            "anchor_fits": [
                {"anchor_id": "anchor-a", "observed_at": "2026-10-28T08:30:00Z"},
                {"anchor_id": "anchor-b", "observed_at": "2026-10-28T09:30:00Z"},
            ],
        }],
    }
    chart = build_chart_data(
        _fixture_nodes(), WINDOW, weights=WEIGHTS, anchors=anchors,
        calibration=calibration,
    )
    cost = chart["datasets"]["weighted_cost_buckets"]
    line = cost["cumulative_percentage_line"]
    assert line["status"] == "available"
    assert line["series"][0]["metric"] == "estimated_limit_pct"
    assert line["series"][0]["points"]
    assert {"anchor-a", "anchor-b"} <= set(line["series"][0]["anchor_ids"])

    local_group = dict(calibration["groups"][0])
    local_group["fit_status"] = "fit-local-lower-bound"
    local_group["capacity_interpretation"] = "local lower bound"
    local_group["coverage_assumptions"] = ["local-only-lower-bound"]
    local_anchors = [dict(anchor, coverage_assumption="local-only-lower-bound") for anchor in anchors]
    local_chart = build_chart_data(
        _fixture_nodes(), WINDOW, weights=WEIGHTS, anchors=local_anchors,
        calibration={"capacity_fit_status": "fit", "groups": [local_group]},
    )
    assert local_chart["datasets"]["weighted_cost_buckets"]["cumulative_percentage_line"]["series"][0]["metric"] == "observed_local_contribution_pct"


def test_percentage_line_is_unknown_for_partial_or_incomplete_report_scope():
    calibration = {
        "capacity_fit_status": "fit",
        "groups": [{
            "kind": "five_hour", "fit_status": "fit-account-coverage",
            "window_start": "2026-10-28T08:00:00Z", "window_reset_at": "2026-10-28T10:00:00Z",
            "capacity_units": 1000.0, "coverage_assumptions": ["all-account-usage-observed"],
            "anchor_ids": ["a", "b"],
            "anchor_fits": [
                {"anchor_id": "a", "observed_at": "2026-10-28T08:30:00Z"},
                {"anchor_id": "b", "observed_at": "2026-10-28T09:30:00Z"},
            ],
        }],
    }
    anchors = [
        {"anchor_id": "a", "observed_at": "2026-10-28T08:30:00Z"},
        {"anchor_id": "b", "observed_at": "2026-10-28T09:30:00Z"},
    ]
    for kwargs, reason_part in (
        ({"scope_is_full": False}, "filter"),
        ({"coverage_complete": False}, "coverage"),
    ):
        chart = build_chart_data(
            _fixture_nodes(), WINDOW, weights=WEIGHTS, anchors=anchors,
            calibration=calibration, **kwargs,
        )
        line = chart["datasets"]["weighted_cost_buckets"]["cumulative_percentage_line"]
        assert line["status"] == "unknown"
        assert reason_part in line["reason"]


def test_usage_charts_cli_writes_the_versioned_chart_document_privately(tmp_path, monkeypatch):
    from nyxloom import cli

    target = tmp_path / "usage-charts.json"
    expected = {
        "schema_version": "usage-charts-v1",
        "source_schema_version": "usage-report-v1",
        "datasets": {},
    }
    monkeypatch.setattr(
        cli, "_build_usage_report_data",
        lambda args: {"extensions": {"chart_data": expected}},
    )
    assert cli.cmd_usage_charts(SimpleNamespace(out=str(target))) == 0
    assert json.loads(target.read_text(encoding="utf-8")) == expected
    assert target.stat().st_mode & 0o777 == 0o600


def test_primary_cli_registers_chart_options_and_requires_output(monkeypatch, capsys):
    from nyxloom import cli
    from nyxloom.cli_registry import primary_cli

    captured = {}

    def capture(args):
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(cli, "cmd_usage_charts", capture)
    assert primary_cli().run(argv=[
        "usage", "charts", "--since", "24h", "--top", "5",
        "--group-by", "model", "--no-labels", "--anchor-file", "anchors.json",
        "--out", "charts.json",
    ]) == 0
    assert captured["since"] == "24h"
    assert captured["top"] == 5
    assert captured["group_by"] == "model"
    assert captured["no_labels"] is True
    assert captured["anchor_file"] == "anchors.json"
    assert captured["out"] == "charts.json"
    assert primary_cli().run(argv=["usage", "charts", "--since", "24h"]) == 2
    assert "required: --out" in capsys.readouterr().err


def test_usage_anchor_help_keeps_its_parent_command_prefix(capsys):
    from nyxloom.cli_registry import primary_cli

    assert primary_cli().run(argv=["usage", "anchor", "--help"]) == 0
    assert "Usage: nyxloom usage anchor <verb>" in capsys.readouterr().out
