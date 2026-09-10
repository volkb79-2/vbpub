"""lossless.dump_codex() tests: the independent "keep prose, drop machine
calls" dumper for Codex rollouts, against synthetic fixtures mirroring both
real schema generations (verified this session against 417 real local
~/.codex/sessions/**/*.jsonl files, cli_version 0.142.2-0.151.0 -- see
lossless.py's own module docstring and stats.py's `_build_call_rows_codex`
docstring for the full real-data writeup, incl. the `compacted.payload.
message` finding this dumper's fallback also relies on).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.session_extract import lossless


def _write_new_generation_fixture(tmp_path: Path) -> Path:
    lines = [
        {"timestamp": "2026-01-01T00:00:00Z", "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": "abc", "cli_version": "0.151.0"}},
        {"timestamp": "2026-01-01T00:00:01Z", "ordinal": 1, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "UserMessage", "content": [{"type": "text", "text": "evaluate the new schema"}]}}},
        {"timestamp": "2026-01-01T00:00:02Z", "ordinal": 2, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "Reasoning", "raw_content": ["thinking about the schema change"]}}},
        {"timestamp": "2026-01-01T00:00:03Z", "ordinal": 3, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "AgentMessage", "content": [{"type": "Text", "text": "Confirmed, it works."}]}}},
        {"timestamp": "2026-01-01T00:00:04Z", "ordinal": 4, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "command": "ls"}}},
        {"timestamp": "2026-01-01T00:00:05Z", "ordinal": 5, "type": "event_msg",
         "payload": {"type": "token_count", "info": {
             "total_token_usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
             "last_token_usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
             "model_context_window": 258400}}},
        {"timestamp": "2026-01-01T00:00:06Z", "ordinal": 6, "type": "compacted",
         # Real finding: an ordinary auto-compaction's payload.message is
         # usually empty -- the real content is encrypted, unrecoverable.
         "payload": {"message": "", "window_number": 1, "window_id": "w1",
                      "previous_window_id": "w0", "first_window_id": "w0",
                      "replacement_history": [{"type": "compaction", "encrypted_content": "gAAAA..."}]}},
        {"timestamp": "2026-01-01T00:00:07Z", "ordinal": 7, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {"type": "ContextCompaction"}}},
        {"timestamp": "2026-01-01T00:00:08Z", "ordinal": 8, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "AgentMessage", "content": [{"type": "Text", "text": "post-compaction reply"}]}}},
    ]
    fp = tmp_path / "rollout-new-schema.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return fp


def test_new_generation_keeps_prose_and_thinking_drops_tool_noise(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    out = lossless.dump_codex(fp)

    assert "evaluate the new schema" in out
    assert "Confirmed, it works." in out
    assert "post-compaction reply" in out
    # THINKING kept unconditionally -- lossless, unlike the smart adapter
    # (which requires ExtractConfig.include_thinking)
    assert "thinking about the schema change" in out

    assert "CommandExecution" not in out
    assert "ContextCompaction" not in out  # NEW generation's content-free echo of the same "compacted" record
    assert "token_count" not in out


def test_empty_compacted_message_falls_back_to_placeholder(tmp_path):
    # The real-data finding: 91% of real compacted records carry an empty
    # payload.message -- must fall back to "[compacted]", same as
    # adapters/codex.py's own parse().
    fp = _write_new_generation_fixture(tmp_path)
    out = lossless.dump_codex(fp)
    assert "[compacted]" in out


def test_nonempty_compacted_message_is_kept_verbatim(tmp_path):
    lines = [
        {"timestamp": "2026-01-01T00:00:00Z", "ordinal": 0, "type": "compacted",
         "payload": {"message": "Another language model started to solve this problem...",
                      "window_number": 6, "window_id": "w6", "previous_window_id": "wX",
                      "first_window_id": "w0", "replacement_history": []}},
    ]
    fp = tmp_path / "rollout.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    out = lossless.dump_codex(fp)
    assert "Another language model started to solve this problem" in out
    assert "[compacted]" not in out


def test_since_marker_slices_forward(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    out = lossless.dump_codex(fp, since_marker="3")
    assert "evaluate the new schema" not in out
    assert "post-compaction reply" in out


def test_until_marker_slices_backward_inclusive(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    out = lossless.dump_codex(fp, until_marker="3")
    assert "evaluate the new schema" in out
    assert "Confirmed, it works." in out
    assert "post-compaction reply" not in out


def test_since_and_until_together_bound_a_span(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    out = lossless.dump_codex(fp, since_marker="1", until_marker="3")
    assert "evaluate the new schema" not in out
    assert "thinking about the schema change" in out
    assert "Confirmed, it works." in out
    assert "post-compaction reply" not in out


def test_unknown_since_marker_raises(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    with pytest.raises(ValueError, match="--since"):
        lossless.dump_codex(fp, since_marker="does-not-exist")


def test_unknown_until_marker_raises(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    with pytest.raises(ValueError, match="--until"):
        lossless.dump_codex(fp, until_marker="does-not-exist")


def test_old_generation_shape_is_supported(tmp_path):
    # Real old-generation (pre-0.147.0) shape: flat user_message/
    # agent_message payload.type, content-free context_compacted, and (per
    # adapters/codex.py's own docstring) no `ordinal` field at all on some
    # real files -- the absolute-position-within-{event_msg,compacted}
    # fallback is exercised here.
    lines = [
        {"timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
         "payload": {"type": "task_started", "turn_id": "t1"}},
        {"timestamp": "2026-01-01T00:00:01Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "first turn"}},
        {"timestamp": "2026-01-01T00:00:02Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "first reply"}},
        {"timestamp": "2026-01-01T00:00:03Z", "type": "event_msg",
         "payload": {"type": "context_compacted"}},
        {"timestamp": "2026-01-01T00:00:04Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "post-compaction reply"}},
    ]
    fp = tmp_path / "rollout-old-schema.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    out = lossless.dump_codex(fp)
    assert "first turn" in out
    assert "first reply" in out
    assert "[context_compacted]" in out
    assert "post-compaction reply" in out


def test_skips_blank_and_malformed_lines(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(
        "\n   \nnot json at all\n"
        + json.dumps({"timestamp": "t", "ordinal": 0, "type": "event_msg",
                       "payload": {"type": "user_message", "message": "survives"}})
        + "\n",
        encoding="utf-8",
    )
    out = lossless.dump_codex(fp)
    assert "survives" in out
