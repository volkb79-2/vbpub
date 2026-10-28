"""Synthetic contracts for the usage-report chart data extension."""

from __future__ import annotations

import json
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
    Window,
    build_chart_data,
    build_report,
    load_weights,
    pack_lane_intervals,
    resolve_window,
)


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
    assert report["nodes"][0]["children"] == []


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
        "--group-by", "model", "--no-labels", "--out", "charts.json",
    ]) == 0
    assert captured["since"] == "24h"
    assert captured["top"] == 5
    assert captured["group_by"] == "model"
    assert captured["no_labels"] is True
    assert captured["out"] == "charts.json"
    assert primary_cli().run(argv=["usage", "charts", "--since", "24h"]) == 2
    assert "required: --out" in capsys.readouterr().err


def test_usage_anchor_help_keeps_its_parent_command_prefix(capsys):
    from nyxloom.cli_registry import primary_cli

    assert primary_cli().run(argv=["usage", "anchor", "--help"]) == 0
    assert "Usage: nyxloom usage anchor <verb>" in capsys.readouterr().out

