"""Synthetic privacy and serialization contracts for the offline HTML report."""

from __future__ import annotations

import json
import re
import stat
from html.parser import HTMLParser
from types import SimpleNamespace

from nyxloom.session_extract.usage.html import render_html


HOSTILE_TOPIC = "topic </script><img src=x onerror=alert(1)> https://label.invalid"
WINDOW_START = "2026-10-28T08:00:00Z"
WINDOW_END = "2026-10-28T12:00:00Z"


def _call(call_id: str, *, node_id: str, agent_id: str | None, minute: int,
          model: str, effort: str, topic: str | None, context: int,
          raw: int, weighted: float, usd: float) -> dict[str, object]:
    return {
        "call_id": call_id,
        "timestamp": f"2026-10-28T{8 + minute // 60:02}:{minute % 60:02}:00Z",
        "session_id": "session-001",
        "agent_id": agent_id,
        "topic": topic,
        "model": model,
        "effort": effort,
        "context_size": context,
        "raw_token_units": raw,
        "weighted_units": weighted,
        "api_equivalent_usd": usd,
        "tokens": {
            "input": raw // 6,
            "output": raw // 6,
            "cache_read": raw // 6,
            "cache_write_5m": raw // 6,
            "cache_write_1h": raw // 6,
            "cache_write_unclassified": 0,
            "thinking": 1,
        },
        "node_id": node_id,
    }


def _aggregate(calls: list[dict[str, object]]) -> dict[str, object]:
    tokens = {key: 0 for key in (
        "input", "output", "cache_read", "cache_write_5m",
        "cache_write_1h", "cache_write_unclassified", "thinking",
    )}
    models: dict[str, int] = {}
    efforts: dict[str, int] = {}
    for call in calls:
        for key, value in call["tokens"].items():
            tokens[key] += value
        models[call["model"]] = models.get(call["model"], 0) + 1
        efforts[call["effort"]] = efforts.get(call["effort"], 0) + 1
    return {
        "calls": len(calls),
        "tokens": tokens,
        "models": models,
        "efforts": efforts,
        "start": calls[0]["timestamp"] if calls else None,
        "end": calls[-1]["timestamp"] if calls else None,
        "raw_token_units": sum(call["raw_token_units"] for call in calls),
        "peak_context": max((call["context_size"] for call in calls), default=0),
        "weighted_units": sum(call["weighted_units"] for call in calls),
        "api_equivalent_usd": sum(call["api_equivalent_usd"] for call in calls),
        "share_of_total": 1.0 if calls else 0.0,
    }


def _report(*, no_labels: bool = False) -> dict[str, object]:
    root_id = "session-001:root"
    agent_a_id = "session-001:agent-a"
    agent_b_id = "session-001:agent-b"
    root_call = _call(
        "call-root", node_id=root_id, agent_id=None, minute=60,
        model="claude-sonnet-5-5", effort="medium", topic=None,
        context=120, raw=60, weighted=4.0, usd=0.01,
    )
    call_a = _call(
        "call-a", node_id=agent_a_id, agent_id="agent-a", minute=120,
        model="claude-sonnet-5-5", effort="medium",
        topic=None if no_labels else HOSTILE_TOPIC, context=300,
        raw=120, weighted=7.0, usd=0.02,
    )
    call_b = _call(
        "call-b", node_id=agent_b_id, agent_id="agent-b", minute=180,
        model="claude-fable-5-1", effort="high",
        topic=None if no_labels else "second topic", context=180,
        raw=90, weighted=9.0, usd=0.03,
    )
    calls = [root_call, call_a, call_b]
    root_self = _aggregate([root_call])
    agent_a_self = _aggregate([call_a])
    agent_b_self = _aggregate([call_b])
    root_tree = _aggregate(calls)
    nodes = [{
        "node_id": root_id, "parent_id": None, "session_id": "session-001",
        "agent_id": None, "kind": "main", "agent_type": "controller",
        "topic": None if no_labels else "root topic", "depth": 0,
        "self": root_self, "subtree": root_tree,
        "omitted_descendants": 0,
        "children": [
            {
                "node_id": agent_a_id, "parent_id": root_id,
                "session_id": "session-001", "agent_id": "agent-a",
                "kind": "subagent", "agent_type": "general-purpose",
                "topic": None if no_labels else HOSTILE_TOPIC, "depth": 1,
                "self": agent_a_self, "subtree": agent_a_self,
                "omitted_descendants": 0, "children": [],
            },
            {
                "node_id": agent_b_id, "parent_id": root_id,
                "session_id": "session-001", "agent_id": "agent-b",
                "kind": "subagent", "agent_type": "general-purpose",
                "topic": None if no_labels else "second topic", "depth": 1,
                "self": agent_b_self, "subtree": agent_b_self,
                "omitted_descendants": 0, "children": [],
            },
        ],
    }]
    call_lookup = [
        {
            "call_id": call["call_id"], "node_id": call["node_id"],
            "session_id": call["session_id"], "agent_id": call["agent_id"],
            "topic": call["topic"], "timestamp": call["timestamp"],
            "model": call["model"], "effort": call["effort"],
            "context_size": call["context_size"], "tokens": call["tokens"],
        }
        for call in calls
    ]
    series = []
    lanes = []
    for node_id, label, node_calls in (
        (root_id, "session session-001", [root_call]),
        (agent_a_id, "agent-a", [call_a]),
        (agent_b_id, "agent-b", [call_b]),
    ):
        points = [{
            "call_id": call["call_id"], "node_id": node_id,
            "timestamp": call["timestamp"], "context_size": call["context_size"],
            "model": call["model"], "effort": call["effort"],
        } for call in node_calls]
        series.append({"node_id": node_id, "label": label, "points": points, "events": []})
        lanes.append({
            "node_id": node_id, "label": label, "peak_context": points[0]["context_size"],
            "points": points, "events": [],
        })
    bucket_specs = [
        {"start": WINDOW_START, "end": "2026-10-28T10:00:00Z"},
        {"start": "2026-10-28T10:00:00Z", "end": WINDOW_END},
    ]
    groups = []
    for call in calls:
        group_buckets = []
        for bucket in bucket_specs:
            in_bucket = bucket["start"] <= call["timestamp"] < bucket["end"]
            group_buckets.append({
                "chart_id": f"bucket-{call['call_id']}-{bucket['start']}",
                **bucket,
                "call_count": int(in_bucket),
                "call_ids": [call["call_id"]] if in_bucket else [],
                "raw_token_units": call["raw_token_units"] if in_bucket else 0,
                "weighted_units": call["weighted_units"] if in_bucket else 0,
                "api_equivalent_usd": call["api_equivalent_usd"] if in_bucket else 0,
            })
        groups.append({
            "chart_id": f"group-{call['call_id']}",
            "group_id": f"agent:{call['node_id']}",
            "group_value": call["node_id"],
            "label": call["topic"] or call["agent_id"] or "session session-001",
            "kind": "value",
            "member_ids": [call["node_id"]],
            "call_count": 1,
            "raw_token_units": call["raw_token_units"],
            "weighted_units": call["weighted_units"],
            "api_equivalent_usd": call["api_equivalent_usd"],
            "buckets": group_buckets,
        })
    report_buckets = []
    for bucket in bucket_specs:
        bucket_calls = [call for call in calls if bucket["start"] <= call["timestamp"] < bucket["end"]]
        report_buckets.append({
            "chart_id": f"total-{bucket['start']}",
            **bucket,
            "call_count": len(bucket_calls),
            "call_ids": [call["call_id"] for call in bucket_calls],
            "raw_token_units": sum(call["raw_token_units"] for call in bucket_calls),
            "weighted_units": sum(call["weighted_units"] for call in bucket_calls),
            "api_equivalent_usd": sum(call["api_equivalent_usd"] for call in bucket_calls),
        })
    composition = [
        {
            "node_id": call["node_id"], "label": call["topic"] or call["agent_id"] or "controller",
            "call_ids": [call["call_id"]],
            "components": {
                key: {
                    "token_count": call["tokens"][key],
                    "weight_per_token": 1.0,
                    "weighted_units": call["tokens"][key],
                    "call_ids": [call["call_id"]] if call["tokens"][key] else [],
                }
                for key in (
                    "input", "cache_read", "cache_write_5m", "cache_write_1h",
                    "cache_write_unclassified", "output",
                )
            },
        }
        for call in calls
    ]
    chart_data = {
        "schema_version": "usage-charts-v1",
        "source_schema_version": "usage-report-v1",
        "call_lookup": call_lookup,
        "color_keys": {
            "model_hues": {"claude-sonnet-5-5": 221, "claude-fable-5-1": 41},
            "effort_styles": {"medium": {"dash": "", "lightness": 54}, "high": {"dash": "6 3", "lightness": 44}},
        },
        "datasets": {
            "swim_lanes": {"lanes": lanes},
            "context_over_time": {
                "shared_y_max": 300,
                "sessions": [{"session_id": "session-001", "label": "session session-001", "series": series}],
            },
            "weighted_cost_buckets": {
                "metric": "weighted_units", "unit": "experimental_weighted_units",
                "group_by": "agent", "groups": groups, "buckets": report_buckets,
            },
            "cost_composition": {"rows": composition},
        },
    }
    return {
        "schema_version": "usage-report-v1", "report": "nyxloom-usage-report",
        "generated_at": "2026-10-28T12:00:00Z",
        "window": {"since": WINDOW_START, "until": WINDOW_END, "semantics": "[since, until)"},
        "filters": {"no_labels": no_labels},
        "presentation": {"flat": False, "sort": "cost"},
        "totals": _aggregate(calls), "calls": calls, "events": [],
        "lineage_edges": [], "coverage": {"scanned_files": 2, "warnings": {}, "partial": False},
        "extensions": {
            "prices": {
                "retrieved_on": "2026-10-28", "weights_sha256": "a" * 64,
                "source_url": "https://platform.claude.com/docs/en/about-claude/pricing",
                "weighted_profile": "e009-historical",
            },
            "limit_anchors": {"estimated_limit_pct": None},
            "chart_data": chart_data,
        },
        "nodes": nodes,
    }


class _ScriptDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.capture = False
        self.data: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.capture = tag == "script" and dict(attrs).get("id") == "usage-report-data"

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.capture = False

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.data.append(data)


def test_html_embeds_the_unchanged_report_with_script_safe_json_and_private_name():
    report = _report()
    output = render_html(report, weights_file="/private/path/custom-weights.toml")
    parser = _ScriptDataParser()
    parser.feed(output)

    assert json.loads("".join(parser.data)) == report
    assert "</script><img" not in output
    assert r"\u003c/script\u003e" in output
    assert "https://" not in output
    assert "/private/path" not in output
    assert "custom-weights.toml" in output


def test_html_is_single_file_offline_and_has_landscape_print_layout():
    output = render_html(_report())
    markup = re.sub(
        r'<script type="application/json" id="[^\"]+">.*?</script>',
        "",
        output,
        flags=re.DOTALL,
    )

    assert output.startswith("<!doctype html>")
    assert output.count("<html") == 1
    assert "Content-Security-Policy" in output
    assert "connect-src 'none'" in output
    assert "@media print" in output
    assert "@page{size:A4 landscape" in output
    assert "display:table-header-group" in output
    assert "page-break-inside:avoid" in output
    assert ".tree-hidden{display:table-row!important}" in output
    assert re.search(r"https?://", output, re.IGNORECASE) is None
    assert re.search(r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|eval)\s*(?:\(|\b)", markup) is None
    assert re.search(r"\s(?:src|href)\s*=", markup, re.IGNORECASE) is None
    assert 'id="usage-report-data"' in output
    assert 'id="swim-chart"' in output and 'id="context-chart"' in output
    assert 'id="cost-chart"' in output and 'id="composition-chart"' in output
    assert 'id="footer-provenance"' in output and 'id="lower-bound-note"' in output


def test_no_labels_html_keeps_no_hostile_topic_in_serialized_output():
    output = render_html(_report(no_labels=True))

    assert HOSTILE_TOPIC not in output
    assert "Display anonymizing does not remove original labels" in output
    assert "Labels were removed before serialization" in output


def test_html_cli_writes_one_private_report_file(monkeypatch, tmp_path, capsys):
    from nyxloom import cli

    target = tmp_path / "usage.html"
    args = SimpleNamespace(usage_format="html", out=str(target), weights=None)
    monkeypatch.setattr(cli, "_build_usage_report_data", lambda _args: _report())

    assert cli.cmd_usage_report(args) == 0

    output = target.read_text(encoding="utf-8")
    assert output.startswith("<!doctype html>")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert capsys.readouterr().out == ""


def test_html_requires_both_frozen_contracts():
    import pytest

    report = _report()
    report["schema_version"] = "usage-report-v2"
    with pytest.raises(ValueError, match="usage-report-v1"):
        render_html(report)

    report = _report()
    report["extensions"]["chart_data"]["schema_version"] = "usage-charts-v2"
    with pytest.raises(ValueError, match="usage-charts-v1"):
        render_html(report)
