"""Synthetic contracts for the Claude Code usage report.

Every transcript in this file is generated in a temporary directory. The
sentinel proves that serialized output never includes message or tool content.
"""

from __future__ import annotations

import json
import os
import csv
import io
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom.cli_registry import primary_cli
from nyxloom.session_extract.usage.render import _display_call_owners
from nyxloom.session_extract.usage import (
    ChartOptions,
    build_report,
    load_corpus,
    render_csv,
    render_html,
    render_json,
    render_table,
    resolve_window,
)


NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
SESSION = "session-001"
PROJECT = "/work/sample"
MESSAGE_SENTINEL = "PRIVATE_MESSAGE_SENTINEL_7e2f9c"
TOOL_SENTINEL = "PRIVATE_TOOL_SENTINEL_a481d0"


def _usage(
    *, input_tokens: int = 5, output_tokens: int = 7,
    cache_read: int = 11, cache_5m: int = 9, cache_1h: int = 4,
    thinking: int = 2,
) -> dict[str, object]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_5m + cache_1h,
        "cache_creation": {
            "ephemeral_5m_input_tokens": cache_5m,
            "ephemeral_1h_input_tokens": cache_1h,
        },
        "output_tokens_details": {"thinking_tokens": thinking},
        "service_tier": "standard",
        "speed": "standard",
    }


def _assistant(
    request_id: str,
    timestamp: str,
    *,
    message_id: str | None = None,
    agent_id: str | None = None,
    cwd: str = PROJECT,
    effort: str = "medium",
    model: str = "claude-sonnet-5-5",
    sidechain: bool = False,
    usage: dict[str, object] | None = None,
    agent_tool_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    content: list[dict[str, object]] = [
        {"type": "text", "text": MESSAGE_SENTINEL},
        {
            "type": "tool_use",
            "id": "ordinary-tool-call",
            "name": "Bash",
            "input": {"command": TOOL_SENTINEL},
        },
    ]
    content.extend(
        {
            "type": "tool_use",
            "id": tool_id,
            "name": "Agent",
            "input": {"description": TOOL_SENTINEL},
        }
        for tool_id in agent_tool_ids
    )
    message: dict[str, object] = {
        "id": message_id or f"msg-{request_id}",
        "model": model,
        "usage": usage or _usage(),
        "content": content,
    }
    row: dict[str, object] = {
        "type": "assistant",
        "timestamp": timestamp,
        "requestId": request_id,
        "sessionId": SESSION,
        "cwd": cwd,
        "effort": effort,
        "perTurnEffort": effort,
        "isSidechain": sidechain,
        "message": message,
    }
    if agent_id is not None:
        row["agentId"] = agent_id
    return row


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_meta(
    session_dir: Path,
    agent_id: str,
    *,
    tool_use_id: str,
    depth: int,
    description: str,
    parent_agent_id: str | None = None,
    is_fork: bool = False,
) -> None:
    value: dict[str, object] = {
        "agentType": "general-purpose",
        "description": description,
        "toolUseId": tool_use_id,
        "spawnDepth": depth,
        "model": "sonnet",
        "isFork": is_fork,
    }
    if parent_agent_id is not None:
        value["parentAgentId"] = parent_agent_id
    path = session_dir / "subagents" / f"agent-{agent_id}.meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_tree_fixture(root: Path) -> Path:
    project_dir = root / "project-key"
    session_dir = project_dir / SESSION
    root_file = project_dir / f"{SESSION}.jsonl"
    _write_jsonl(
        root_file,
        [
            _assistant(
                "req-root", "2026-10-28T11:00:00Z",
                agent_tool_ids=("spawn-outer",),
            ),
            # Same request and usage, different offset spelling. The final
            # stream row is retained but contributes only one API call.
            _assistant(
                "req-root", "2026-10-28T13:00:00+02:00",
                agent_tool_ids=("spawn-outer",),
            ),
        ],
    )
    outer_id = "outer"
    inner_id = "inner"
    outer_file = session_dir / "subagents" / f"agent-{outer_id}.jsonl"
    inner_file = session_dir / "subagents" / f"agent-{inner_id}.jsonl"
    _write_jsonl(
        outer_file,
        [
            _assistant(
                "req-outer-1", "2026-10-28T11:20:00Z",
                agent_id=outer_id, sidechain=True,
                agent_tool_ids=("spawn-inner",),
            ),
            # A later burst in the same file is a resumed agent, not a new
            # tree node.
            _assistant(
                "req-outer-2", "2026-10-28T11:40:00Z",
                agent_id=outer_id, sidechain=True,
            ),
        ],
    )
    _write_jsonl(
        inner_file,
        [
            _assistant(
                "req-inner", "2026-10-28T11:50:00Z",
                agent_id=inner_id, sidechain=True,
                usage=_usage(input_tokens=13, output_tokens=17),
            ),
        ],
    )
    _write_meta(
        session_dir, outer_id, tool_use_id="spawn-outer", depth=1,
        description="outer topic",
    )
    _write_meta(
        session_dir, inner_id, tool_use_id="spawn-inner", depth=2,
        description="inner topic", parent_agent_id=outer_id,
    )
    return root_file


def _build(corpus, **kwargs):
    window = resolve_window("24h", None, now=NOW)
    return build_report(corpus, window=window, **kwargs)


def _normalise_opaque_ids(value, maps=None):
    maps = maps if maps is not None else {}
    if isinstance(value, dict):
        return {key: _normalise_opaque_ids(item, maps) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_opaque_ids(item, maps) for item in value]
    if isinstance(value, str):
        for prefix, label in (
            ("src_", "source"), ("project_", "project"), ("call_", "call"),
            ("event_", "event"), ("conflict_", "conflict"),
        ):
            if value.startswith(prefix):
                mapping = maps.setdefault(label, {})
                if value not in mapping:
                    mapping[value] = f"{label}-fixture-{len(mapping) + 1}"
                return mapping[value]
    return value


def test_deduplicates_streaming_rows_and_preserves_raw_usage_fields(tmp_path):
    _write_tree_fixture(tmp_path)
    corpus = load_corpus(tmp_path)
    root = corpus.sessions[0].root

    assert len(root.calls) == 1
    call = root.calls[0]
    assert call.request_id == "req-root"
    assert call.timestamp == NOW.replace(hour=11)
    assert call.model == "claude-sonnet-5-5"
    assert call.effort == "medium"
    assert call.input_tokens == 5
    assert call.cache_read_tokens == 11
    assert call.cache_write_5m_tokens == 9
    assert call.cache_write_1h_tokens == 4
    assert call.output_tokens == 7
    assert call.thinking_tokens == 2
    assert call.service_tier == "standard"
    assert call.speed == "standard"
    assert call.context_size == 29
    assert call.weighted_cost is None
    assert call.usd_cost is None
    assert corpus.warnings.duplicate_requests == 1
    coverage = _build(corpus)["coverage"]
    assert coverage["has_warnings"] is True
    assert coverage["partial"] is False


def test_duplicate_after_explicit_window_end_does_not_replace_in_window_call(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(
        root_file,
        [
            _assistant(
                "bounded-request", "2026-10-28T11:50:00Z",
                usage=_usage(output_tokens=7),
            ),
            _assistant(
                "bounded-request", "2026-10-28T10:50:00Z",
                usage=_usage(output_tokens=7),
            ),
            _assistant(
                "bounded-request", "2026-10-28T12:10:00Z",
                usage=_usage(output_tokens=7),
            ),
        ],
    )
    os.utime(root_file, (NOW.timestamp(), NOW.timestamp()))

    corpus = load_corpus(
        tmp_path,
        since=datetime(2026, 10, 28, 11, 0, tzinfo=timezone.utc),
        until=NOW,
    )
    report = build_report(
        corpus,
        window=resolve_window("1h", "2026-10-28T12:00:00Z", now=NOW),
    )

    assert report["totals"]["calls"] == 1
    assert report["totals"]["tokens"]["output"] == 7
    assert corpus.warnings.duplicate_requests == 2


def test_nested_subagents_resume_in_one_node_and_roll_up_once(tmp_path):
    _write_tree_fixture(tmp_path)
    report = _build(load_corpus(tmp_path))
    root = report["nodes"][0]
    outer = root["children"][0]
    inner = outer["children"][0]

    assert (outer["agent_id"], outer["parent_id"], outer["topic"]) == (
        "outer", SESSION, "outer topic",
    )
    assert (inner["agent_id"], inner["parent_id"], inner["topic"]) == (
        "inner", outer["node_id"], "inner topic",
    )
    assert outer["self"]["calls"] == 2
    assert outer["subtree"]["calls"] == 3
    assert root["self"]["calls"] == 1
    assert root["subtree"]["calls"] == 4
    assert report["totals"]["calls"] == 4
    assert report["totals"]["tokens"]["output"] == 38
    outer_call = next(call for call in report["calls"] if call["agent_id"] == "outer")
    assert outer_call["sidechain"] is True
    assert outer_call["context_size"] == 29
    assert outer_call["service_tier"] == "standard"
    assert outer_call["speed"] == "standard"
    assert outer_call["api_equivalent_usd"] is None


def test_agent_name_record_is_a_fallback_topic_and_fork_kind_is_retained(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    _write_jsonl(
        project_dir / f"{SESSION}.jsonl",
        [_assistant("root", "2026-10-28T11:00:00Z", agent_tool_ids=("fork-link",))],
    )
    agent_path = session_dir / "subagents" / "agent-forked.jsonl"
    _write_jsonl(
        agent_path,
        [
            {"type": "agent-name", "agentName": "named agent", "sessionId": SESSION},
            _assistant("fork-call", "2026-10-28T11:10:00Z", agent_id="forked", sidechain=True),
        ],
    )
    _write_meta(
        session_dir, "forked", tool_use_id="fork-link", depth=1,
        description="", is_fork=True,
    )

    report = _build(load_corpus(tmp_path))
    fork = report["nodes"][0]["children"][0]
    assert fork["kind"] == "fork"
    assert fork["topic"] == "named agent"
    assert next(call for call in report["calls"] if call["agent_id"] == "forked")["topic"] == "named agent"


def test_request_id_is_file_local_but_exact_copied_prefix_deduplicates(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    _write_jsonl(
        project_dir / f"{SESSION}.jsonl",
        [_assistant(
            "shared-request", "2026-10-28T11:00:00Z", message_id="msg-root",
            agent_tool_ids=("spawn",),
        )],
    )
    _write_jsonl(
        session_dir / "subagents" / "agent-child.jsonl",
        [_assistant(
            "shared-request", "2026-10-28T11:00:00Z", message_id="msg-child",
            agent_id="child", sidechain=True,
        )],
    )
    _write_meta(session_dir, "child", tool_use_id="spawn", depth=1, description="child")

    corpus = load_corpus(tmp_path)
    report = _build(corpus)
    assert report["totals"]["calls"] == 2
    assert len(report["calls"]) == 2
    assert report["nodes"][0]["self"]["calls"] == 1
    assert report["nodes"][0]["children"][0]["self"]["calls"] == 1
    assert corpus.warnings.duplicate_requests == 0

    # An exact copied prefix is the separate cross-file identity: same
    # session, message, UTC timestamp, model, and complete usage tuple.
    copy_path = session_dir / "subagents" / "agent-copy.jsonl"
    copied = _assistant(
        "different-request-id", "2026-10-28T11:00:00+00:00",
        message_id="msg-root", agent_id="copy", sidechain=True,
    )
    _write_jsonl(copy_path, [copied])
    _write_meta(session_dir, "copy", tool_use_id="spawn", depth=1, description="copy", is_fork=True)
    copied_report = _build(load_corpus(tmp_path))
    assert copied_report["totals"]["calls"] == 2
    assert copied_report["coverage"]["partial"] is False
    root_call = next(call for call in copied_report["calls"] if call["message_id"] == "msg-root")
    assert root_call["copied_source_count"] == 2
    assert root_call["request_ids"] == ["different-request-id", "shared-request"]
    assert len(root_call["provenance"]) == 2
    assert root_call["has_fork_source"] is True


def test_request_id_is_not_a_global_key_across_unrelated_sessions(tmp_path):
    first = _assistant("shared-request", "2026-10-28T11:00:00Z", message_id="message-one")
    second = _assistant("shared-request", "2026-10-28T11:00:00Z", message_id="message-two")
    first["sessionId"] = "session-one"
    second["sessionId"] = "session-two"
    _write_jsonl(tmp_path / "project-key" / "session-one.jsonl", [first])
    _write_jsonl(tmp_path / "project-key" / "session-two.jsonl", [second])

    corpus = load_corpus(tmp_path)
    report = _build(corpus)

    assert report["totals"]["calls"] == 2
    assert {call["request_id"] for call in report["calls"]} == {"shared-request"}
    assert {call["message_id"] for call in report["calls"]} == {"message-one", "message-two"}
    assert corpus.warnings.duplicate_requests == 0


def test_conflicting_stream_duplicates_are_excluded_and_disclosed(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(root_file, [
        _assistant("conflict", "2026-10-28T11:00:00Z", usage=_usage(output_tokens=7)),
        _assistant("conflict", "2026-10-28T11:00:01Z", usage=_usage(output_tokens=70)),
    ])
    corpus = load_corpus(tmp_path)
    report = _build(corpus)

    assert report["totals"]["calls"] == 0
    assert report["conflicts"][0]["kind"] == "duplicate_conflict"
    assert report["conflicts"][0]["candidate_count"] == 2
    assert corpus.warnings.duplicate_conflicts == 1
    assert report["coverage"]["partial"] is True


def test_usage_report_v1_has_exact_call_ledger_and_versioned_csv(tmp_path):
    _write_tree_fixture(tmp_path)
    corpus = load_corpus(tmp_path)
    window = resolve_window("24h", None, now=NOW)
    report = build_report(corpus, window=window, generated_at=NOW)
    root = report["nodes"][0]

    assert report["schema_version"] == "usage-report-v1"
    assert report["basis"]["mode"] == "raw"
    assert report["extensions"] == {
        "prices": None, "limit_anchors": None, "chart_data": None,
    }
    assert len(report["calls"]) == report["totals"]["calls"] == 4
    assert sum(call["raw_token_units"] for call in report["calls"]) == report["totals"]["raw_token_units"]
    assert root["call_ids"]
    assert root["self"]["calls"] == 1
    assert root["subtree"]["calls"] == 4
    assert all("source_id" in call and call["provenance"] for call in report["calls"])
    assert all("/" not in call["source_id"] for call in report["calls"])
    assert str(tmp_path) not in render_json(report)
    assert str(tmp_path) not in render_csv(report)
    repeated_corpus = load_corpus(tmp_path)
    repeated = build_report(
        repeated_corpus,
        window=window,
        generated_at=NOW,
    )
    assert render_json(report) == render_json(repeated)
    csv_rows = list(csv.DictReader(render_csv(report).splitlines()))
    assert csv_rows[0]["record_type"] == "report"
    assert csv_rows[0]["schema_version"] == "usage-report-v1"
    assert sum(row["record_type"] == "call" for row in csv_rows) == 4
    assert "Schema: usage-report-v1" in render_table(report)


def test_usage_csv_neutralizes_formula_leading_text_in_every_text_column():
    prefixes = ("=", "+", "-", "@", "\t", "\r")
    fields = (
        "call_id", "session_id", "agent_id", "topic", "agent_type",
        "project_id", "request_id", "message_id", "timestamp", "model",
        "effort", "service_tier", "speed", "inference_geo", "source_id",
    )
    labels = {
        field: f"{prefixes[index % len(prefixes)]}FORMULA_{field}"
        for index, field in enumerate(fields)
    }
    call = {
        **labels,
        "tokens": {
            "input": 1,
            "cache_read": 0,
            "cache_write_5m": 0,
            "cache_write_1h": 0,
            "cache_write_unclassified": 0,
            "output": 0,
            "thinking": 0,
        },
        "request_ids": ["=FORMULA_request_alias"],
        "source_ids": ["+FORMULA_source_alias"],
        "context_size": 1,
        "sidechain": False,
        "has_fork_source": False,
        "source_line": 1,
        "provenance": {"source": "@FORMULA_provenance"},
        "stream_block_count": 1,
        "copied_source_count": 0,
        "conflicted": False,
        "raw_token_units": 1,
        "weighted_units": None,
        "api_equivalent_usd": None,
    }
    metadata = {
        "schema_version": "=FORMULA_schema",
        "report": "+FORMULA_report",
        "window": {
            "since": "-FORMULA_since",
            "until": "@FORMULA_until",
        },
        "calls": [call],
        "lineage_edges": [],
    }

    rows = list(csv.DictReader(io.StringIO(render_csv(metadata), newline="")))
    report_row = next(row for row in rows if row["record_type"] == "report")
    call_row = next(row for row in rows if row["record_type"] == "call")

    assert report_row["schema_version"] == "'=FORMULA_schema"
    assert report_row["report"] == "'+FORMULA_report"
    assert report_row["window_since"] == "'-FORMULA_since"
    assert report_row["window_until"] == "'@FORMULA_until"
    assert call_row["node_id"] == "'" + labels["session_id"]
    for field, label in labels.items():
        assert call_row[field] == "'" + label
    assert call_row["request_ids"].startswith("[")
    assert call_row["source_ids"].startswith("[")
    assert call_row["provenance"].startswith("{")


@pytest.mark.parametrize("missing_request_first", [False, True])
def test_mixed_request_id_presence_deduplicates_by_message_id(tmp_path, missing_request_first):
    rows = [
        _assistant("shared-request", "2026-10-28T11:00:00Z", message_id="shared-message"),
        _assistant("shared-request", "2026-10-28T11:00:00Z", message_id="shared-message"),
    ]
    rows[1].pop("requestId")
    if missing_request_first:
        rows.reverse()
    _write_jsonl(tmp_path / "project-key" / f"{SESSION}.jsonl", rows)

    corpus = load_corpus(tmp_path)
    report = _build(corpus)

    assert len(report["calls"]) == report["totals"]["calls"] == 1
    assert report["calls"][0]["request_id"] == "shared-request"
    assert report["calls"][0]["request_ids"] == ["shared-request"]
    assert report["calls"][0]["stream_block_count"] == 2
    assert corpus.warnings.duplicate_requests == 1


def test_utc_half_open_window_clipping_and_disjoint_active_bursts(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(root_file, [
        _assistant("before", "2026-10-28T09:00:00Z"),
        _assistant("start", "2026-10-28T12:00:00+02:00"),
        _assistant("near", "2026-10-28T10:30:00Z"),
        _assistant("burst", "2026-10-28T12:00:00Z"),
        _assistant("at-until", "2026-10-28T13:00:00Z"),
    ])
    os.utime(root_file, (NOW.timestamp(), NOW.timestamp()))
    window = resolve_window(
        "2026-10-28T12:00:00+02:00", "2026-10-28T13:00:00Z", now=NOW,
    )
    report = build_report(load_corpus(tmp_path, since=window.since, until=window.until), window=window)
    root = report["nodes"][0]

    assert report["window"]["semantics"] == "[since, until)"
    assert [call["request_id"] for call in report["calls"]] == ["start", "near", "burst"]
    assert root["clipped_before"] is True
    assert root["clipped_after"] is True
    assert root["self"]["wall_duration_seconds"] == 7200
    assert root["self"]["active_span_seconds"] == 1800
    table = render_table(report)
    assert "ACTIVE SPAN SUM" in table
    assert "can exceed wall duration" in table
    assert root["active_bursts"] == [
        {"start": "2026-10-28T10:00:00Z", "end": "2026-10-28T10:30:00Z", "calls": 2},
        {"start": "2026-10-28T12:00:00Z", "end": "2026-10-28T12:00:00Z", "calls": 1},
    ]


def test_structural_compaction_summary_and_unexplained_drops_are_distinct(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    before = _assistant("before", "2026-10-28T09:00:00Z", usage=_usage(input_tokens=900))
    after_compact = _assistant("after-compact", "2026-10-28T09:10:00Z", usage=_usage(input_tokens=400))
    after_clear = _assistant("after-clear", "2026-10-28T09:20:00Z", usage=_usage(input_tokens=100))
    retry_a = _assistant("retry", "2026-10-28T09:24:00Z", usage=_usage(input_tokens=100))
    retry_b = _assistant("retry", "2026-10-28T09:25:00Z", usage=_usage(input_tokens=100))
    after_model_switch = _assistant(
        "after-switch", "2026-10-28T09:30:00Z", model="claude-haiku-5-5",
        usage=_usage(input_tokens=40),
    )
    _write_jsonl(root_file, [
        before,
        {"type": "system", "subtype": "compact_boundary", "uuid": "compact-1",
         "timestamp": "2026-10-28T09:00:00Z", "compactMetadata": {
             "trigger": "auto", "preTokens": 1200, "postTokens": 500, "durationMs": 1234,
         }},
        {"type": "user", "uuid": "summary-1", "parentUuid": "compact-1",
         "timestamp": "2026-10-28T09:06:00Z", "isCompactSummary": True,
         "message": {"content": MESSAGE_SENTINEL}},
        after_compact,
        {"type": "assistant", "isApiErrorMessage": True, "apiErrorStatus": 429,
         "error": "rate_limit", "uuid": "reject-1", "timestamp": "2026-10-28T09:08:00Z",
         "message": {"content": TOOL_SENTINEL}},
        {"type": "user", "uuid": "clear-1", "timestamp": "2026-10-28T09:15:00Z",
         "message": {"content": "<command-name>/clear</command-name>"}},
        after_clear,
        retry_a,
        retry_b,
        after_model_switch,
    ])
    report = _build(load_corpus(tmp_path))

    assert [event["kind"] for event in report["events"]] == [
        "compaction", "api_rejection", "unexplained_drop", "unexplained_drop",
    ]
    compact = report["events"][0]
    assert compact["summary_present"] is True
    assert compact["summary_timestamp"] == "2026-10-28T09:06:00Z"
    assert compact["duration_ms"] == 1234
    rejection = report["events"][1]
    assert rejection["source_record_id"] == "reject-1"
    assert rejection["limit_kind"] == "rate_limit"
    assert rejection["status_code"] == 429
    assert sum(call["request_id"] == "retry" for call in report["calls"]) == 1
    assert load_corpus(tmp_path).warnings.duplicate_requests == 1
    assert all(event["kind"] != "compaction" for event in report["events"][1:])
    assert MESSAGE_SENTINEL not in render_json(report)
    assert TOOL_SENTINEL not in render_json(report)


def test_cross_file_copied_prefix_conflict_preserves_and_flags_representative(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    first = _assistant("copy-a", "2026-10-28T11:00:00Z", message_id="same-message")
    conflicting = _assistant(
        "copy-b", "2026-10-28T11:00:00Z", message_id="same-message",
        agent_id="fork", usage=_usage(output_tokens=99),
    )
    _write_jsonl(project_dir / f"{SESSION}.jsonl", [first])
    _write_jsonl(session_dir / "subagents" / "agent-fork.jsonl", [conflicting])
    _write_meta(session_dir, "fork", tool_use_id="missing", depth=1, description="fork", is_fork=True)

    corpus = load_corpus(tmp_path)
    report = _build(corpus)

    assert len(report["calls"]) == report["totals"]["calls"] == 1
    assert report["calls"][0]["tokens"]["output"] == 7
    assert report["calls"][0]["conflicted"] is True
    assert report["conflicts"][0]["kind"] == "cross_file_conflict"
    assert report["conflicts"][0]["candidate_count"] == 2
    assert report["conflicts"][0]["representative_call_id"] == report["calls"][0]["call_id"]
    assert corpus.warnings.cross_file_conflicts == 1
    assert corpus.warnings.conflicted_calls_excluded == 1
    csv_call = next(
        row for row in csv.DictReader(render_csv(report).splitlines())
        if row["record_type"] == "call"
    )
    assert csv_call["conflicted"] == "True"
    assert "CONFLICTED" in render_table(report)
    folded = _build(corpus, depth=0)
    assert "CONFLICTED" in render_table(folded)
    assert _display_call_owners(folded)[report["calls"][0]["call_id"]] == (
        folded["nodes"][0]["node_id"]
    )
    assert "cross_file_conflict" in render_json(report)


def test_record_timestamps_control_window_not_mtime_and_truncated_tail_is_visible(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(root_file, [_assistant("in-window", "2026-10-28T11:00:00Z")])
    with root_file.open("ab") as handle:
        handle.write(b'{"type":"assistant"')
    os.utime(root_file, (1, 1))

    corpus = load_corpus(
        tmp_path,
        since=datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc),
        until=NOW,
    )
    report = build_report(
        corpus,
        window=resolve_window("2026-10-28T10:00:00Z", None, now=NOW),
        generated_at=NOW,
    )

    assert [call["request_id"] for call in report["calls"]] == ["in-window"]
    assert report["coverage"]["warnings"]["truncated_lines"] == 1
    assert report["nodes"][0]["clipped_before"] is False
    assert report["nodes"][0]["clipped_after"] is False


def test_naive_or_missing_record_timestamps_never_enter_timed_report(tmp_path):
    first = _assistant("missing-time", "2026-10-28T11:00:00Z")
    first.pop("timestamp")
    second = _assistant("naive-time", "2026-10-28T11:00:00Z")
    second["timestamp"] = "2026-10-28T11:00:00"
    _write_jsonl(tmp_path / "project-key" / f"{SESSION}.jsonl", [first, second])

    corpus = load_corpus(tmp_path)
    report = _build(corpus)

    assert report["calls"] == []
    assert report["coverage"]["warnings"]["missing_timestamps"] == 2
    assert report["coverage"]["partial"] is True


def test_ambiguous_lineage_is_visible_and_shared_with_session_discovery(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    _write_jsonl(project_dir / f"{SESSION}.jsonl", [_assistant("root", "2026-10-28T09:00:00Z")])
    _write_jsonl(session_dir / "subagents" / "agent-parent-a.jsonl", [
        _assistant("parent-a", "2026-10-28T09:10:00Z", agent_id="parent-a", sidechain=True,
                   agent_tool_ids=("ambiguous-spawn",)),
    ])
    _write_jsonl(session_dir / "subagents" / "agent-parent-b.jsonl", [
        _assistant("parent-b", "2026-10-28T09:11:00Z", agent_id="parent-b", sidechain=True,
                   agent_tool_ids=("ambiguous-spawn",)),
    ])
    _write_jsonl(session_dir / "subagents" / "agent-child.jsonl", [
        _assistant("child", "2026-10-28T09:12:00Z", agent_id="child", sidechain=True),
    ])
    _write_meta(session_dir, "parent-a", tool_use_id="none-a", depth=1, description="parent a")
    _write_meta(session_dir, "parent-b", tool_use_id="none-b", depth=1, description="parent b")
    _write_meta(session_dir, "child", tool_use_id="ambiguous-spawn", depth=2, description="child")

    corpus = load_corpus(tmp_path)
    report = _build(corpus)
    edge = next(edge for edge in report["lineage_edges"] if edge["agent_id"] == "child")
    assert edge["status"] == "unresolved_ambiguous_tool_use"
    assert edge["candidate_parent_ids"] == sorted([
        f"{SESSION}:agent:parent-a", f"{SESSION}:agent:parent-b",
    ])
    assert corpus.warnings.ambiguous_parent_edges == 1

    from nyxloom.session_extract.adapters.claude_code import list_agents

    discovered = list_agents(project_dir / f"{SESSION}.jsonl")
    discovered_by_name = {Path(node.id).stem: node for node in discovered if "agent-" in Path(node.id).name}
    assert discovered_by_name["agent-child"].parent_id is None


def test_inconsistent_parent_metadata_falls_back_to_unique_tool_owner(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    _write_jsonl(project_dir / f"{SESSION}.jsonl", [
        _assistant("root", "2026-10-28T09:00:00Z", agent_tool_ids=("spawn-parent",)),
    ])
    _write_jsonl(session_dir / "subagents" / "agent-parent.jsonl", [
        _assistant("parent", "2026-10-28T09:10:00Z", agent_id="parent", sidechain=True,
                   agent_tool_ids=("spawn-child",)),
    ])
    _write_jsonl(session_dir / "subagents" / "agent-child.jsonl", [
        _assistant("child", "2026-10-28T09:12:00Z", agent_id="child", sidechain=True),
    ])
    _write_meta(session_dir, "parent", tool_use_id="spawn-parent", depth=1, description="parent")
    _write_meta(session_dir, "wrong", tool_use_id="missing", depth=1, description="wrong")
    _write_meta(
        session_dir, "child", tool_use_id="spawn-child", depth=2,
        description="child", parent_agent_id="wrong",
    )

    corpus = load_corpus(tmp_path)
    report = _build(corpus)
    child_edge = next(edge for edge in report["lineage_edges"] if edge["agent_id"] == "child")
    assert child_edge["parent_node_id"] == f"{SESSION}:agent:parent"
    assert child_edge["status"] == "resolved_tool_use_id_parent_metadata_conflict"
    assert corpus.warnings.parent_conflicts == 1

    from nyxloom.session_extract.adapters.claude_code import list_agents

    discovered = list_agents(project_dir / f"{SESSION}.jsonl")
    child = next(node for node in discovered if node.id.endswith("agent-child.jsonl"))
    assert child.parent_id.endswith("agent-parent.jsonl")


def test_missing_meta_and_unmatched_nested_parent_are_explicitly_unattributed(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    root_file = project_dir / f"{SESSION}.jsonl"
    _write_jsonl(root_file, [_assistant("root", "2026-10-28T11:00:00Z")])
    missing_file = session_dir / "subagents" / "agent-no-meta.jsonl"
    _write_jsonl(
        missing_file,
        [_assistant("missing", "2026-10-28T11:10:00Z", agent_id="no-meta", sidechain=True)],
    )
    orphan_file = session_dir / "subagents" / "agent-orphan.jsonl"
    _write_jsonl(
        orphan_file,
        [_assistant("orphan", "2026-10-28T11:15:00Z", agent_id="orphan", sidechain=True)],
    )
    _write_meta(
        session_dir, "orphan", tool_use_id="not-in-any-parent", depth=2,
        description="orphan topic",
    )

    corpus = load_corpus(tmp_path)
    report = _build(corpus)
    root = report["nodes"][0]
    unatt = next(node for node in root["children"] if node["kind"] == "unattributed")

    assert {child["agent_id"] for child in unatt["children"]} == {"no-meta", "orphan"}
    assert corpus.warnings.missing_meta == 1
    assert corpus.warnings.unattributed_agents == 2


def test_timezone_offsets_sidechain_order_and_project_selection(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    root_file = project_dir / f"{SESSION}.jsonl"
    _write_jsonl(
        root_file,
        [
            _assistant("later", "2026-10-28T13:30:00+02:00", agent_tool_ids=("spawn-agent",)),
            _assistant("earlier", "2026-10-28T11:10:00Z"),
        ],
    )
    agent_file = session_dir / "subagents" / "agent-side.jsonl"
    _write_jsonl(
        agent_file,
        [_assistant("side", "2026-10-28T11:20:00Z", agent_id="side", sidechain=True)],
    )
    _write_meta(session_dir, "side", tool_use_id="spawn-agent", depth=1, description="side topic")
    corpus = load_corpus(tmp_path)
    report = build_report(
        corpus,
        window=resolve_window("2h", None, now=NOW),
        project="sample",
    )

    assert report["totals"]["calls"] == 3
    assert report["nodes"][0]["children"][0]["self"]["calls"] == 1
    root_calls = corpus.sessions[0].root.calls
    assert [call.timestamp.hour for call in root_calls] == [11, 11]
    assert any(call.sidechain for call in corpus.sessions[0].root.children[0].calls)


def test_empty_window_and_naive_timestamp_policy(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(root_file, [_assistant("old", "2026-10-20T11:00:00Z")])
    report = build_report(
        load_corpus(tmp_path),
        window=resolve_window("1h", None, now=NOW),
    )
    assert report["nodes"] == []
    assert report["totals"]["calls"] == 0

    with pytest.raises(ValueError, match="timezone"):
        resolve_window("2026-10-28T11:00:00", None, now=NOW)


def test_nested_text_cannot_spoof_a_top_level_record_type(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(
        root_file,
        [{
            "type": "user",
            "timestamp": "2026-10-28T11:00:00Z",
            "message": {
                "id": "not-an-api-call",
                "model": "claude-sonnet-5-5",
                "usage": _usage(),
                "text": '\\"type\\":\\"assistant\\"',
            },
        }],
    )
    assert load_corpus(tmp_path).sessions == []


def test_unclassified_cache_write_and_message_id_dedup_fallback(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    usage = {
        "input_tokens": 2,
        "output_tokens": 3,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 17,
    }
    first = _assistant("ignored-a", "2026-10-28T11:00:00Z", usage=usage)
    second = _assistant("ignored-b", "2026-10-28T11:00:00Z", usage=usage)
    first.pop("requestId")
    second.pop("requestId")
    first["message"]["id"] = "fallback-id"
    second["message"]["id"] = "fallback-id"
    _write_jsonl(root_file, [first, second])

    corpus = load_corpus(tmp_path)
    call = corpus.sessions[0].root.calls[0]
    assert len(corpus.sessions[0].root.calls) == 1
    assert call.request_id is None
    assert call.message_id == "fallback-id"
    assert call.cache_write_unclassified_tokens == 17
    assert call.raw_token_units == 27
    assert corpus.warnings.duplicate_requests == 1


def test_compaction_boundary_is_a_structural_event_without_summary_content(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(
        root_file,
        [
            {
                "type": "system",
                "subtype": "compact_boundary",
                "timestamp": "2026-10-28T10:59:00Z",
                "compactMetadata": {
                    "preTokens": 1200,
                    "postTokens": 500,
                    "trigger": "auto",
                },
                "summary": MESSAGE_SENTINEL,
            },
            _assistant("after-compact", "2026-10-28T11:00:00Z"),
        ],
    )
    report = _build(load_corpus(tmp_path))
    node = report["nodes"][0]
    assert report["events"][0]["kind"] == "compaction"
    assert report["events"][0]["timestamp"] == "2026-10-28T10:59:00Z"
    assert report["events"][0]["pre_tokens"] == 1200
    assert report["events"][0]["post_tokens"] == 500
    assert report["events"][0]["trigger"] == "auto"
    assert report["events"][0]["summary_present"] is False
    assert node["event_ids"] == [report["events"][0]["event_id"]]
    assert MESSAGE_SENTINEL not in render_json(report)


def test_node_events_are_sorted_after_unexplained_drop_events_are_added(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(root_file, [
        _assistant("before-drop", "2026-10-28T11:00:00Z", usage=_usage(input_tokens=900)),
        _assistant("after-drop", "2026-10-28T11:10:00Z", usage=_usage(input_tokens=100)),
        {
            "type": "system",
            "subtype": "compact_boundary",
            "timestamp": "2026-10-28T11:20:00Z",
            "compactMetadata": {"preTokens": 1200, "postTokens": 500, "trigger": "auto"},
        },
    ])

    report = _build(load_corpus(tmp_path))
    root = report["nodes"][0]

    assert [event["kind"] for event in report["events"]] == [
        "unexplained_drop", "compaction",
    ]
    assert root["event_ids"] == [event["event_id"] for event in report["events"]]


def test_min_share_folds_small_root_sessions_and_depth_counts_omitted_agents(tmp_path):
    project_dir = tmp_path / "project-key"
    _write_jsonl(
        project_dir / f"{SESSION}.jsonl",
        [_assistant("large", "2026-10-28T11:00:00Z")],
    )
    _write_jsonl(
        project_dir / "session-002.jsonl",
        [_assistant(
            "tiny", "2026-10-28T11:10:00Z",
            usage=_usage(input_tokens=1, output_tokens=0, cache_read=0, cache_5m=0, cache_1h=0, thinking=0),
        )],
    )
    report = build_report(
        load_corpus(tmp_path),
        window=resolve_window("24h", None, now=NOW),
        min_share=0.1,
    )
    assert report["totals"]["calls"] == 2
    assert any(node["topic"] == "other (1 sessions)" for node in report["nodes"])

    deep = build_report(
        load_corpus(_tree_root(tmp_path)),
        window=resolve_window("24h", None, now=NOW),
        depth=1,
    )
    outer = next(child for child in deep["nodes"][0]["children"] if child["agent_id"] == "outer")
    assert outer["children"] == []
    assert outer["omitted_descendants"] == 1


def test_min_share_folds_small_agent_children_and_flat_csv_is_globally_sorted(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    _write_jsonl(
        project_dir / f"{SESSION}.jsonl",
        [_assistant("root", "2026-10-28T11:00:00Z", agent_tool_ids=("big", "tiny"))],
    )
    subagents = session_dir / "subagents"
    _write_jsonl(
        subagents / "agent-big.jsonl",
        [_assistant("big-call", "2026-10-28T11:10:00Z", agent_id="big", sidechain=True)],
    )
    _write_jsonl(
        subagents / "agent-tiny.jsonl",
        [_assistant(
            "tiny-call", "2026-10-28T11:20:00Z", agent_id="tiny", sidechain=True,
            usage=_usage(input_tokens=1, output_tokens=0, cache_read=0, cache_5m=0, cache_1h=0, thinking=0),
        )],
    )
    _write_meta(session_dir, "big", tool_use_id="big", depth=1, description="large")
    _write_meta(session_dir, "tiny", tool_use_id="tiny", depth=1, description="small")

    report = build_report(
        load_corpus(tmp_path), window=resolve_window("24h", None, now=NOW), min_share=0.1,
        flat=True, sort="calls",
    )
    root = next(node for node in report["nodes"] if node["kind"] == "main")
    assert [child["topic"] for child in root["children"]] == ["large", "other (1 agents)"]
    csv_rows = list(csv.DictReader(render_csv(report).splitlines()))
    assert [row["record_type"] for row in csv_rows] == ["report", "call", "call", "call"]
    assert [row["call_id"] for row in csv_rows[1:]] == [
        call["call_id"] for call in report["calls"]
    ]


def _tree_root(tmp_path: Path) -> Path:
    # Use a separate fixture subtree so the preceding two-session case does
    # not change depth or omitted-descendant assertions.
    root = tmp_path / "nested"
    _write_tree_fixture(root)
    return root


def test_absolute_window_normalizes_to_utc_and_rejects_reverse_range():
    window = resolve_window(
        "2026-10-28T13:00:00+02:00",
        "2026-10-28T12:00:00Z",
        now=NOW,
    )
    assert window.since == NOW.replace(hour=11)
    with pytest.raises(ValueError, match="before"):
        resolve_window("2026-10-28T12:00:00Z", "2026-10-28T11:00:00Z", now=NOW)
    with pytest.raises(ValueError, match="invalid timestamp"):
        resolve_window("1h", "", now=NOW)


def test_golden_json_for_synthetic_report(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(root_file, [_assistant("golden", "2026-10-28T11:00:00Z")])
    report = build_report(
        load_corpus(tmp_path),
        window=resolve_window("1h", "2026-10-28T12:00:00Z", now=NOW),
        generated_at=NOW,
    )
    actual = json.loads(render_json(report))
    actual_projection = _normalise_opaque_ids(actual)
    expected_path = Path(__file__).parent / "fixtures" / "usage_report" / "golden.json"
    assert actual_projection == json.loads(expected_path.read_text(encoding="utf-8"))
    repeated = build_report(
        load_corpus(tmp_path),
        window=resolve_window("1h", "2026-10-28T12:00:00Z", now=NOW),
        generated_at=NOW,
    )
    assert render_json(report) == render_json(repeated)


def test_table_json_and_csv_never_serialize_message_or_tool_content(tmp_path):
    root_file = _write_tree_fixture(tmp_path)
    from nyxloom.session_extract.usage.pricing import apply_valuation, load_weights

    weights = load_weights()
    corpus = load_corpus(tmp_path)
    apply_valuation(corpus, weights)
    report = _build(
        corpus, no_labels=False, chart_options=ChartOptions(weights=weights),
    )
    no_label_corpus = load_corpus(tmp_path)
    apply_valuation(no_label_corpus, weights)
    no_labels = _build(
        no_label_corpus, no_labels=True, chart_options=ChartOptions(weights=weights),
    )
    outputs = (
        render_table(report), render_json(report), render_csv(report),
        render_html(report), render_json(no_labels), render_html(no_labels),
    )
    assert all(MESSAGE_SENTINEL not in output for output in outputs)
    assert all(TOOL_SENTINEL not in output for output in outputs)
    assert "outer topic" in render_json(report)
    assert "outer topic" not in render_json(no_labels)
    assert "project_id is a stable hash of recorded cwd" in no_labels["privacy"]["filesystem_paths"]
    assert report["extensions"]["chart_data"]["schema_version"] == "usage-charts-v1"
    assert "PRIVATE_MESSAGE_SENTINEL" not in render_json(report)
    assert "PRIVATE_TOOL_SENTINEL" not in render_json(report)
    assert "outer topic" not in render_json(no_labels["extensions"]["chart_data"])
    child_id = no_labels["nodes"][0]["children"][0]["agent_id"]
    child_call = next(call for call in no_labels["calls"] if call["agent_id"] == child_id)
    assert child_call["topic"] is None
    assert child_call["agent_type"] == "general-purpose"
    assert child_call["project_id"] == next(
        call for call in report["calls"] if call["agent_id"] == child_id
    )["project_id"]
    assert root_file.exists()


def test_display_call_owners_maps_folded_calls_to_visible_nodes():
    report = {
        "nodes": [{
            "node_id": "root", "session_id": "session", "kind": "main",
            "call_ids": ["visible-call"],
            "children": [{
                "node_id": "visible-child", "session_id": "session",
                "kind": "subagent", "call_ids": [], "children": [],
            }],
        }],
        "calls": [
            {"call_id": "visible-call", "session_id": "session", "agent_id": None},
            {"call_id": "chart-call", "session_id": "session", "agent_id": None},
            {"call_id": "edge-call", "session_id": "session", "agent_id": "edge-agent"},
            {"call_id": "root-call", "session_id": "session", "agent_id": None},
            {"call_id": "orphan-call", "session_id": "session", "agent_id": None},
            {"call_id": "missing-root-call", "session_id": "missing", "agent_id": None},
        ],
        "lineage_edges": [
            {"node_id": "hidden-chart", "parent_node_id": "root",
             "session_id": "session", "agent_id": "chart-agent"},
            {"node_id": "hidden-edge", "parent_node_id": "root",
             "session_id": "session", "agent_id": "edge-agent"},
            {"node_id": "orphan", "parent_node_id": None,
             "session_id": "session", "agent_id": "orphan-agent"},
        ],
        "extensions": {"chart_data": {"call_lookup": [
            {"call_id": "chart-call", "node_id": "hidden-chart"},
            {"call_id": "orphan-call", "node_id": "orphan"},
        ]}},
    }

    owners = _display_call_owners(report)

    assert owners == {
        "visible-call": "root",
        "chart-call": "root",
        "edge-call": "root",
        "root-call": "root",
        "orphan-call": "root",
    }




def test_report_does_not_serialize_paths_from_free_text_labels(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    _write_jsonl(project_dir / f"{SESSION}.jsonl", [
        _assistant("root", "2026-10-28T11:00:00Z", agent_tool_ids=("spawn",)),
    ])
    _write_jsonl(session_dir / "subagents" / "agent-child.jsonl", [
        _assistant("child", "2026-10-28T11:10:00Z", agent_id="child", sidechain=True),
    ])
    private_path = str(tmp_path / "private" / "worktree")
    _write_meta(session_dir, "child", tool_use_id="spawn", depth=1, description=f"task {private_path}")

    report = _build(load_corpus(tmp_path))

    assert private_path not in render_json(report)
    assert private_path not in render_csv(report)
    child = report["nodes"][0]["children"][0]
    assert child["topic"] is None


def test_primary_cli_registers_usage_report_options(monkeypatch, capsys):
    from nyxloom import cli

    captured: dict[str, object] = {}

    def capture(args):
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(cli, "cmd_usage_report", capture)
    status = primary_cli().run(
        argv=[
            "usage", "report", "--since", "24h", "--format", "html",
            "--out", "usage.html", "--sort", "calls", "--min-share", "1%", "--no-labels",
            "--anchor-file", "anchors.json",
        ],
    )

    assert status == 0
    assert captured["since"] == "24h"
    assert captured["usage_format"] == "html"
    assert captured["out"] == "usage.html"
    assert captured["sort"] == "calls"
    assert captured["min_share"] == pytest.approx(0.01)
    assert captured["no_labels"] is True
    assert captured["anchor_file"] == "anchors.json"
    assert capsys.readouterr().out == ""


def test_primary_cli_usage_spelling_has_help_and_required_since_error(capsys):
    cli = primary_cli()

    assert cli.run(argv=["usage", "report", "--help"]) == 0
    help_output = capsys.readouterr().out
    assert "nyxloom usage report" in help_output
    assert "Exclusive ISO-8601 end timestamp" in help_output
    assert "--format {table,json,csv,html,pdf}" in help_output

    assert cli.run(argv=["usage", "report"]) == 2
    assert "required: --since" in capsys.readouterr().err


def test_usage_html_rejects_missing_output_before_calling_handler(monkeypatch, capsys):
    from nyxloom import cli

    called = False

    def forbidden(_args):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "cmd_usage_report", forbidden)
    status = primary_cli().run(
        argv=["usage", "report", "--since", "24h", "--format", "html"],
    )

    assert status == 2
    assert not called
    assert "--format html requires --out FILE" in capsys.readouterr().err


def test_usage_pdf_rejects_missing_output_before_calling_handler(monkeypatch, capsys):
    from nyxloom import cli

    called = False

    def forbidden(_args):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "cmd_usage_report", forbidden)
    status = primary_cli().run(
        argv=["usage", "report", "--since", "24h", "--format", "pdf"],
    )

    assert status == 2
    assert not called
    assert "--format pdf requires --out FILE" in capsys.readouterr().err


def test_cost_state_non_finite_integer_metadata_is_counted_as_malformed(tmp_path):
    session_id = "synthetic-cost-overflow"
    row = {
        "type": "cost-state",
        "sessionId": session_id,
        "hasUnknownModelCost": False,
        "totalCostUSD": 10**400,
        "modelUsage": {},
    }
    _write_jsonl(tmp_path / "project-key" / f"{session_id}.jsonl", [row])

    corpus = load_corpus(tmp_path)

    assert corpus.cost_states == []
    assert corpus.malformed_cost_states == 1


def test_cost_state_parser_counts_malformed_metadata_without_retaining_it(tmp_path):
    session_id = "synthetic-cost-malformed"
    valid_model = {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "cacheCreationEphemeral5mInputTokens": 0,
        "cacheCreationEphemeral1hInputTokens": 0,
        "costUSD": 0.0,
    }
    valid = {
        "type": "cost-state", "sessionId": session_id,
        "hasUnknownModelCost": False, "totalCostUSD": 0.0,
        "modelUsage": {"claude-sonnet-5-5": valid_model},
    }
    malformed = []
    missing_session = deepcopy(valid)
    del missing_session["sessionId"]
    malformed.append(missing_session)
    mismatch = deepcopy(valid)
    mismatch["sessionId"] = "different-family"
    malformed.append(mismatch)
    bad_table = deepcopy(valid)
    bad_table["modelUsage"] = []
    malformed.append(bad_table)
    bad_unknown_flag = deepcopy(valid)
    bad_unknown_flag["hasUnknownModelCost"] = 1
    malformed.append(bad_unknown_flag)
    bad_model_name = deepcopy(valid)
    bad_model_name["modelUsage"] = {"model/name": valid_model}
    malformed.append(bad_model_name)
    bad_model_row = deepcopy(valid)
    bad_model_row["modelUsage"] = {"claude-sonnet-5-5": []}
    malformed.append(bad_model_row)
    bad_input = deepcopy(valid)
    bad_input["modelUsage"]["claude-sonnet-5-5"]["inputTokens"] = True
    malformed.append(bad_input)
    bad_output = deepcopy(valid)
    bad_output["modelUsage"]["claude-sonnet-5-5"]["outputTokens"] = -1
    malformed.append(bad_output)
    bad_cost = deepcopy(valid)
    bad_cost["modelUsage"]["claude-sonnet-5-5"]["costUSD"] = float("inf")
    malformed.append(bad_cost)
    bad_total = deepcopy(valid)
    bad_total["totalCostUSD"] = -1
    malformed.append(bad_total)

    _write_jsonl(tmp_path / "project-key" / f"{session_id}.jsonl", malformed)

    corpus = load_corpus(tmp_path)

    assert corpus.cost_states == []
    assert corpus.malformed_cost_states == len(malformed)
    assert corpus.warnings.session_id_mismatches == 1


def test_cost_state_keeps_valid_base_counters_but_marks_bad_optional_ttl_split_unidentifiable(tmp_path):
    session_id = "synthetic-cost-ttl"
    row = {
        "type": "cost-state", "sessionId": session_id,
        "hasUnknownModelCost": False, "totalCostUSD": 0.0,
        "modelUsage": {
            "claude-sonnet-5-5": {
                "inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
                "cacheCreationEphemeral5mInputTokens": -1,
                "cacheCreationEphemeral1hInputTokens": 0, "costUSD": 0.0,
            },
        },
    }
    _write_jsonl(tmp_path / "project-key" / f"{session_id}.jsonl", [row])

    corpus = load_corpus(tmp_path)

    assert corpus.malformed_cost_states == 0
    assert corpus.cost_states[0].models[0].cache_write_5m_tokens is None
