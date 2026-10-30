"""Behavioral edge cases for the usage contract's defensive branches."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nyxloom.session_extract.usage import (
    Corpus,
    ChartOptions,
    Node,
    Session,
    build_report,
    parse_share_percent,
    render_report,
    resolve_window,
)
from nyxloom.session_extract.usage.html import render_html
from nyxloom.session_extract.usage.model import Aggregate
from nyxloom.session_extract.usage.query import (
    SORT_COLUMNS,
    UsageQueryError,
    _activity_bursts,
    _aggregate_dict,
    _node_count,
    _select_project_sessions,
    _sort_key,
)
from nyxloom.session_extract.usage.render import (
    _compact_duration,
    _effort_text,
    _label,
    _model_text,
    _short_time,
    _sorted_flat_nodes,
)
from test_usage_charts import WINDOW, _call, _fixture_nodes
from test_usage_charts import WEIGHTS
from test_usage_html import _report as html_report


UTC = timezone.utc
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _corpus() -> Corpus:
    nodes = _fixture_nodes()
    root = nodes[0]
    root.children = nodes[1:]
    return Corpus(sessions=[Session(root.session_id, root)])


def test_render_report_dispatches_all_formats(monkeypatch):
    report = build_report(
        _corpus(), window=WINDOW, generated_at=NOW,
        chart_options=ChartOptions(weights=WEIGHTS),
    )
    assert "NODE / TOPIC" in render_report(report, "table")
    assert '"schema_version": "usage-report-v1"' in render_report(report, "json")
    assert "schema_version" in render_report(report, "csv")
    assert "<!doctype html>" in render_report(report, "html")
    monkeypatch.setattr("nyxloom.session_extract.usage.render_pdf", lambda *_a, **_k: b"%PDF-test")
    assert render_report(report, "pdf") == b"%PDF-test"
    with pytest.raises(ValueError, match="format must be table"):
        render_report(report, "yaml")


def test_html_rejects_weights_metadata_without_a_filename():
    with pytest.raises(ValueError, match="weights_file must have a filename"):
        render_html(html_report(), weights_file="/")


def test_call_and_aggregate_compatibility_properties():
    call = _fixture_nodes()[0].calls[0]
    aggregate = Aggregate(wall_duration_seconds=91)
    assert call.source_order == call.source_line
    assert aggregate.duration_seconds == 91


@pytest.mark.parametrize(
    ("since", "until", "now", "message"),
    [
        (None, None, NOW, "--since must"),
        (" ", None, NOW, "--since must"),
        ("24h", None, datetime(2026, 10, 28, 12), "now must include a timezone"),
        ("not-a-window", None, NOW, "invalid timestamp"),
        ("2026-10-28T10:00:00", None, NOW, "timestamps must include a timezone"),
        ("2026-10-28T11:00:00Z", "2026-10-28T10:00:00Z", NOW,
         "--until must not be before"),
    ],
)
def test_window_parser_rejects_invalid_inputs(since, until, now, message):
    with pytest.raises(ValueError, match=message):
        resolve_window(since, until, now=now)


@pytest.mark.parametrize("value", ["", "nope", "101", "-0.1", "nan", "inf"])
def test_share_percent_rejects_invalid_or_unbounded_values(value):
    with pytest.raises(ValueError, match="percentage from 0 to 100"):
        parse_share_percent(value)


def test_project_selector_exact_basename_missing_and_ambiguous_paths():
    nodes = _fixture_nodes()
    first = nodes[0]
    first.calls[0].project = "/workspace/one/demo"
    second = _fixture_nodes()[0]
    second.session_id = "session-2"
    second.calls[0].session_id = "session-2"
    second.calls[0].project = "/workspace/two/demo"
    sessions = [Session("session-1", first), Session("session-2", second)]

    assert _select_project_sessions(sessions, "/workspace/one/demo") == {"session-1"}
    with pytest.raises(UsageQueryError, match="did not match"):
        _select_project_sessions(sessions, "/workspace/missing")
    with pytest.raises(UsageQueryError, match="basename is ambiguous"):
        _select_project_sessions(sessions, "demo")


@pytest.mark.parametrize("column", SORT_COLUMNS)
def test_report_sort_keys_cover_each_public_sort_column(column):
    node = Node(
        node_id="node", parent_id=None, session_id="session", agent_id="agent",
        subtree_aggregate=Aggregate(
            raw_token_units=10, wall_duration_seconds=8, calls=2,
            peak_context=99, output_tokens=3, start=NOW, end=NOW,
        ),
    )
    assert _sort_key(node, column)[-1] == "node"


def test_report_projection_helpers_cover_empty_and_synthetic_nodes():
    other = Node(
        node_id="other", parent_id=None, session_id="", agent_id=None,
        kind="other", omitted_descendants=3,
    )
    assert _node_count(other) == 3
    assert _aggregate_dict(Aggregate(), share=None).get("share_of_total") is None
    assert _activity_bursts([]) == []


def test_flat_table_render_helpers_cover_sorting_labels_and_empty_fields():
    nodes = [
        {
            "node_id": "later", "start": "2026-10-28T11:00:00Z", "kind": "subagent",
            "session_id": "session", "agent_id": "agent", "topic": None,
            "children": [], "node_id": "later",
            "calls": 2, "duration_seconds": 5, "peak_context": 10,
            "output_tokens": 3, "tokens": {"output": 3}, "raw_token_units": 20,
            "omitted_descendants": 2,
        },
        {
            "node_id": "earlier", "start": "2026-10-28T10:00:00Z", "kind": "main",
            "session_id": "session", "agent_id": None, "topic": None,
            "children": [], "node_id": "earlier",
            "calls": 1, "duration_seconds": 4, "peak_context": 20,
            "output_tokens": 8, "tokens": {"output": 8}, "raw_token_units": 30,
            "omitted_descendants": 0,
        },
    ]
    for column in ("start", "cost", "dur", "calls", "peak", "out", "unknown"):
        assert _sorted_flat_nodes({"nodes": nodes, "presentation": {"sort": column}})
    assert _label(nodes[0]) == "agent agent"
    assert _label({**nodes[0], "topic": "named"}) == "named"
    assert _label({**nodes[0], "agent_id": None, "kind": "unattributed"}) == "(unattributed)"
    assert _label({**nodes[0], "agent_id": None, "kind": "other"}) == "other (2 agents)"
    assert _label({**nodes[0], "agent_id": None, "kind": "custom"}) == "custom"
    assert _compact_duration(0) == "0s"
    assert _compact_duration(90) == "1m30s"
    assert _compact_duration(3600) == "1h0m"
    assert _compact_duration(86400) == "1d0h0m"
    assert _short_time(None) == "—"
    assert _short_time("2026-10-28T10:00:00Z") == "2026-10-28 10:00:00"
    assert _model_text({"models": {}}) == "—"
    assert _model_text({"models": {"model": 2}}) == "model (2)"
    assert _effort_text({"efforts": {}}) == "—"
    assert _effort_text({"efforts": {"high": 1}}) == "high (1)"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sort": "bad"}, "--sort must be one of"),
        ({"depth": -1}, "--depth must be zero or greater"),
        ({"min_share": 1.1}, "min_share must be between"),
        ({"flat": True, "depth": 0}, "--flat and --depth cannot be combined"),
        ({"session": "absent"}, "--session did not match"),
        ({"generated_at": datetime(2026, 10, 28, 12)}, "generated_at must include a timezone"),
    ],
)
def test_build_report_rejects_invalid_query_contracts(kwargs, message):
    with pytest.raises((ValueError, UsageQueryError), match=message):
        build_report(_corpus(), window=WINDOW, **kwargs)


def test_build_report_filters_selected_session_and_handles_empty_window():
    corpus = _corpus()
    second_root = _fixture_nodes()[0]
    second_root.session_id = "session-2"
    for call in second_root.calls:
        call.session_id = "session-2"
    corpus.sessions.append(Session("session-2", second_root))
    report = build_report(corpus, window=WINDOW, session="session-2", generated_at=NOW)
    assert report["totals"]["calls"] == len(second_root.calls)
    empty = build_report(Corpus(sessions=[]), window=WINDOW, generated_at=NOW)
    assert empty["calls"] == []
    assert empty["events"] == []


def test_build_report_project_filter_skips_other_session_and_min_share_can_keep_every_root():
    sessions = []
    for index, project in enumerate(("/workspace/one/demo", "/workspace/two/demo"), start=1):
        root = _fixture_nodes()[0]
        session_id = f"session-{index}"
        root.node_id = f"{session_id}:root"
        root.session_id = session_id
        root.project = project
        for call_index, call in enumerate(root.calls):
            call.call_id = f"{session_id}-call-{call_index}"
            call.session_id = session_id
            call.project = project
        sessions.append(Session(session_id, root))
    corpus = Corpus(sessions=sessions)

    selected = build_report(
        corpus, window=WINDOW, project="/workspace/one/demo", generated_at=NOW,
    )
    assert [node["session_id"] for node in selected["nodes"]] == ["session-1"]

    uncollapsed = build_report(
        corpus, window=WINDOW, min_share=0.01, generated_at=NOW,
    )
    assert [node["session_id"] for node in uncollapsed["nodes"]] == ["session-1", "session-2"]

    child_report = build_report(
        _corpus(), window=WINDOW, min_share=0.01, generated_at=NOW,
    )
    assert child_report["nodes"][0]["children"]
    assert all(child["kind"] != "other" for child in child_report["nodes"][0]["children"])
