"""Synthetic contracts for the Claude Code usage report.

Every transcript in this file is generated in a temporary directory. The
sentinel proves that serialized output never includes message or tool content.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom.cli_registry import primary_cli
from nyxloom.session_extract.usage import (
    build_report,
    load_corpus,
    render_csv,
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
                usage=_usage(output_tokens=3),
            ),
            _assistant(
                "bounded-request", "2026-10-28T12:10:00Z",
                usage=_usage(output_tokens=70),
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
    assert corpus.warnings.duplicate_requests == 0


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
    outer_call = outer["call_details"][0]
    assert outer_call["sidechain"] is True
    assert outer_call["context_size"] == 29
    assert outer_call["service_tier"] == "standard"
    assert outer_call["speed"] == "standard"
    assert outer_call["usd"] is None


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
    assert fork["call_details"][0]["topic"] == "named agent"


def test_request_id_dedup_spans_session_and_agent_files(tmp_path):
    project_dir = tmp_path / "project-key"
    session_dir = project_dir / SESSION
    _write_jsonl(
        project_dir / f"{SESSION}.jsonl",
        [_assistant("shared-request", "2026-10-28T11:00:00Z", agent_tool_ids=("spawn",))],
    )
    _write_jsonl(
        session_dir / "subagents" / "agent-child.jsonl",
        [_assistant("shared-request", "2026-10-28T11:00:00Z", agent_id="child", sidechain=True)],
    )
    _write_meta(session_dir, "child", tool_use_id="spawn", depth=1, description="child")

    corpus = load_corpus(tmp_path)
    report = _build(corpus)
    assert report["totals"]["calls"] == 1
    assert report["nodes"][0]["calls"] == 1
    assert report["nodes"][0]["self"]["calls"] == 0
    assert report["nodes"][0]["children"][0]["self"]["calls"] == 1
    assert corpus.warnings.duplicate_requests == 1
    assert corpus.warnings.duplicate_conflicts == 1


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
    assert call.request_id == "fallback-id"
    assert call.cache_write_unclassified_tokens == 17
    assert call.raw_token_units == 27
    assert corpus.warnings.duplicate_requests == 1


def test_compaction_boundary_marks_next_call_without_summary_content(tmp_path):
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
    assert node["compactions"] == [{
        "timestamp": "2026-10-28T10:59:00Z",
        "kind": "compaction",
        "pre_tokens": 1200,
        "post_tokens": 500,
        "trigger": "auto",
    }]
    assert node["call_details"][0]["kind"] == "normal"
    assert node["call_details"][0]["compaction_before"] is True
    assert node["call_details"][0]["compaction_pre_tokens"] == 1200
    assert MESSAGE_SENTINEL not in render_json(report)


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
    csv_rows = render_csv(report).splitlines()[1:]
    assert [row.split(",", 1)[0] for row in csv_rows] == [
        SESSION, f"{SESSION}:agent:big", f"{SESSION}:other",
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


def test_golden_json_for_synthetic_report(tmp_path):
    root_file = tmp_path / "project-key" / f"{SESSION}.jsonl"
    _write_jsonl(root_file, [_assistant("golden", "2026-10-28T11:00:00Z")])
    report = build_report(
        load_corpus(tmp_path),
        window=resolve_window("1h", "2026-10-28T12:00:00Z", now=NOW),
        generated_at=NOW,
    )
    actual = json.loads(render_json(report))
    root = actual["nodes"][0]
    actual_projection = {
        "basis": actual["basis"],
        "schema_version": actual["schema_version"],
        "report": actual["report"],
        "totals": actual["totals"],
        "node": {
            "kind": root["kind"],
            "calls": root["calls"],
            "tokens": root["tokens"],
            "raw_token_units": root["raw_token_units"],
            "call_details": root["call_details"],
        },
    }
    expected_path = Path(__file__).parent / "fixtures" / "usage_report" / "golden.json"
    assert actual_projection == json.loads(expected_path.read_text(encoding="utf-8"))


def test_table_json_and_csv_never_serialize_message_or_tool_content(tmp_path):
    root_file = _write_tree_fixture(tmp_path)
    report = _build(load_corpus(tmp_path), no_labels=False)
    no_labels = _build(load_corpus(tmp_path), no_labels=True)
    outputs = (
        render_table(report), render_json(report), render_csv(report),
        render_json(no_labels),
    )
    assert all(MESSAGE_SENTINEL not in output for output in outputs)
    assert all(TOOL_SENTINEL not in output for output in outputs)
    assert "outer topic" in render_json(report)
    assert "outer topic" not in render_json(no_labels)
    assert no_labels["nodes"][0]["children"][0]["call_details"][0]["topic"] is None
    assert root_file.exists()


def test_primary_cli_registers_usage_report_options(monkeypatch, capsys):
    from nyxloom import cli

    captured: dict[str, object] = {}

    def capture(args):
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(cli, "cmd_usage_report", capture)
    status = primary_cli().run(
        argv=[
            "usage", "report", "--since", "24h", "--format", "json",
            "--sort", "calls", "--min-share", "1%", "--no-labels",
        ],
    )

    assert status == 0
    assert captured["since"] == "24h"
    assert captured["usage_format"] == "json"
    assert captured["sort"] == "calls"
    assert captured["min_share"] == pytest.approx(0.01)
    assert captured["no_labels"] is True
    assert capsys.readouterr().out == ""
