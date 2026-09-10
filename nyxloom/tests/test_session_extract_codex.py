"""Codex CLI adapter tests, against a small synthetic rollout fixture
mirroring the real event_msg/session_meta shape verified on this machine's
actual ~/.codex/sessions/**/*.jsonl files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.session_extract import ExtractConfig
from nyxloom.session_extract.adapters import codex
from nyxloom.session_extract.events import EventKind


def _write_fixture(tmp_path: Path) -> Path:
    lines = [
        {"timestamp": "2026-01-01T00:00:00Z", "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": "abc", "cli_version": "0.149.0", "originator": "codex_exec"}},
        {"timestamp": "2026-01-01T00:00:01Z", "ordinal": 1, "type": "event_msg",
         "payload": {"type": "task_started", "turn_id": "t1"}},
        {"timestamp": "2026-01-01T00:00:02Z", "ordinal": 2, "type": "response_item",
         "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "system framing"}]}},
        {"timestamp": "2026-01-01T00:00:03Z", "ordinal": 3, "type": "event_msg",
         "payload": {"type": "user_message", "message": "evaluate docker-repack for our image"}},
        {"timestamp": "2026-01-01T00:00:04Z", "ordinal": 4, "type": "response_item",
         "payload": {"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": "{}"}},
        {"timestamp": "2026-01-01T00:00:05Z", "ordinal": 5, "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "c1", "output": "..."}},
        {"timestamp": "2026-01-01T00:00:06Z", "ordinal": 6, "type": "event_msg",
         "payload": {"type": "agent_message", "message": "Verdict: it works, here's why.", "phase": "commentary"}},
        {"timestamp": "2026-01-01T00:00:07Z", "ordinal": 7, "type": "event_msg",
         "payload": {"type": "context_compacted"}},
        {"timestamp": "2026-01-01T00:00:08Z", "ordinal": 8, "type": "event_msg",
         "payload": {"type": "agent_message", "message": "should never be reached"}},
    ]
    fp = tmp_path / "rollout-2026-01-01T00-00-00-abc.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return fp


def test_sniff(tmp_path):
    fp = _write_fixture(tmp_path)
    assert codex.sniff(fp)


def test_parse_uses_event_msg_layer_not_response_item(tmp_path):
    fp = _write_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig())

    # response_item noise (developer framing, function_call, function_call_output)
    # never becomes an event -- only event_msg is consulted.
    assert len(events) == 4  # user_message, agent_message, context_compacted, agent_message

    op = next(e for e in events if e.kind is EventKind.OPERATOR_TEXT)
    assert "docker-repack" in op.text
    assert op.marker == "3"

    asst = [e for e in events if e.kind is EventKind.ASSISTANT_TEXT]
    assert any("Verdict" in e.text for e in asst)

    marker = next(e for e in events if e.kind is EventKind.LIFECYCLE_MARKER)
    assert marker.marker == "7"


def test_since_marker(tmp_path):
    fp = _write_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig(since_marker="6"))
    assert all(e.marker != "3" for e in events)
    assert any(e.kind is EventKind.LIFECYCLE_MARKER for e in events)


def test_since_marker_unknown_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="not found as an ordinal"):
        codex.parse(fp, str(fp), ExtractConfig(since_marker="does-not-exist"))


def test_until_marker(tmp_path):
    fp = _write_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig(until_marker="6"))
    assert [e.marker for e in events] == ["3", "6"]  # the context_compacted (7) is excluded


def test_until_marker_unknown_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="not found as an ordinal"):
        codex.parse(fp, str(fp), ExtractConfig(until_marker="does-not-exist"))


def test_list_sessions_returns_the_one_synthetic_id(tmp_path):
    fp = _write_fixture(tmp_path)
    assert codex.list_sessions(fp) == [str(fp)]


def test_sniff_rejects_non_jsonl_suffix(tmp_path):
    fp = tmp_path / "rollout.txt"
    fp.write_text("irrelevant\n")
    assert not codex.sniff(fp)


def test_sniff_skips_blank_and_malformed_lines(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(
        "\n   \nnot json\n"
        + json.dumps({"timestamp": "t", "type": "session_meta",
                       "payload": {"cli_version": "0.1.0"}})
        + "\n",
        encoding="utf-8",
    )
    assert codex.sniff(fp)


def test_sniff_directory_with_jsonl_suffix_is_false(tmp_path):
    a_dir = tmp_path / "adir.jsonl"
    a_dir.mkdir()
    assert not codex.sniff(a_dir)  # open() raises IsADirectoryError (an OSError) -> False


def test_since_marker_resolves_an_ordinal_less_rollout_via_the_same_fallback(tmp_path):
    # Adversarial-review finding: pre-2026-08 rollout files lack `ordinal`
    # entirely (per this adapter's own docstring); the marker embedded on a
    # full parse falls back to str(seq), but resolution compared only
    # against str(r.get("ordinal")) -- the literal string "None" -- so
    # --since could never be resolved against such a file at all.
    lines = [
        {"timestamp": "t0", "type": "event_msg", "payload": {"type": "user_message", "message": "first"}},
        {"timestamp": "t1", "type": "event_msg", "payload": {"type": "agent_message", "message": "second"}},
    ]
    fp = tmp_path / "rollout.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    full = codex.parse(fp, str(fp), ExtractConfig())
    assert full[0].marker == "0"

    resumed = codex.parse(fp, str(fp), ExtractConfig(since_marker="0"))
    assert [e.text for e in resumed] == ["second"]


def _write_new_generation_fixture(tmp_path: Path) -> Path:
    # Mirrors the real item_completed schema Codex switched to at
    # cli_version 0.147.0 (2026-08-09) -- verified directly against real
    # local rollout files, not guessed. Every file from that version onward
    # uses ONLY this shape; the OLD flat user_message/agent_message shape
    # never appears.
    lines = [
        {"timestamp": "2026-08-23T00:00:00Z", "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": "abc", "cli_version": "0.149.0"}},
        {"timestamp": "2026-08-23T00:00:01Z", "ordinal": 1, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "UserMessage", "content": [{"type": "text", "text": "evaluate the new schema"}]}}},
        {"timestamp": "2026-08-23T00:00:02Z", "ordinal": 2, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "Reasoning", "raw_content": ["thinking about the schema change"]}}},
        {"timestamp": "2026-08-23T00:00:03Z", "ordinal": 3, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "AgentMessage", "content": [{"type": "Text", "text": "Confirmed, it works."}]}}},
        {"timestamp": "2026-08-23T00:00:04Z", "ordinal": 4, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "CommandExecution", "command": "ls"}}},
        {"timestamp": "2026-08-23T00:00:05Z", "ordinal": 5, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "CollabAgentToolCall", "tool": "spawn_agent"}}},
        {"timestamp": "2026-08-24T00:00:00Z", "ordinal": 6, "type": "compacted",
         "payload": {"message": "Summary of prior work: schema migration verified."}},
        {"timestamp": "2026-08-24T00:00:01Z", "ordinal": 7, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {"type": "ContextCompaction"}}},
        {"timestamp": "2026-08-24T00:00:02Z", "ordinal": 8, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "AgentMessage", "content": [{"type": "Text", "text": "should never be reached"}]}}},
    ]
    fp = tmp_path / "rollout-new-schema.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return fp


def test_new_generation_item_completed_schema_is_parsed(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig())

    op = next(e for e in events if e.kind is EventKind.OPERATOR_TEXT)
    assert op.text == "evaluate the new schema"

    asst = [e for e in events if e.kind is EventKind.ASSISTANT_TEXT]
    assert any("Confirmed, it works." in e.text for e in asst)

    # CommandExecution/CollabAgentToolCall/ContextCompaction are all noise
    assert not any("spawn_agent" in e.text for e in events)
    assert len(events) == 4  # UserMessage, AgentMessage x2, the compacted marker

    marker = next(e for e in events if e.kind is EventKind.LIFECYCLE_MARKER)
    assert "schema migration verified" in marker.text


def test_new_generation_reasoning_emitted_only_when_include_thinking_set(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    default = codex.parse(fp, str(fp), ExtractConfig())
    assert not any(e.kind is EventKind.THINKING for e in default)

    with_thinking = codex.parse(fp, str(fp), ExtractConfig(include_thinking=True))
    thinking = next(e for e in with_thinking if e.kind is EventKind.THINKING)
    assert "thinking about the schema change" in thinking.text


def test_new_generation_compacted_record_hard_stops_the_walk(tmp_path):
    from nyxloom.session_extract import classifier
    from nyxloom.session_extract.select import select

    fp = _write_new_generation_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig())
    classifier.score_events(events)
    kept = select(events, ExtractConfig())
    kept_texts = [e.text for e in kept]
    assert not any("evaluate the new schema" in t for t in kept_texts)  # older than the boundary
    assert any("schema migration verified" in t for t in kept_texts)


def test_parse_skips_blank_and_malformed_lines(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(
        "\n   \nnot json at all\n"
        + json.dumps({"timestamp": "2026-01-01T00:00:00Z", "ordinal": 0, "type": "event_msg",
                       "payload": {"type": "user_message", "message": "hi"}})
        + "\n",
        encoding="utf-8",
    )
    events = codex.parse(fp, str(fp), ExtractConfig())
    assert len(events) == 1
    assert events[0].kind is EventKind.OPERATOR_TEXT
    assert events[0].text == "hi"
