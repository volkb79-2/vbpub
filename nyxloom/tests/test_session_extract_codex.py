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
