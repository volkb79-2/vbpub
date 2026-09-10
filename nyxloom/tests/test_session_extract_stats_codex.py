"""stats.py Codex support: `_build_call_rows_codex` and its helpers, against
synthetic fixtures mirroring the real event_msg/token_count/compacted shapes
verified this session against real local `~/.codex/sessions/**/*.jsonl`
files spanning cli_version 0.142.2-0.151.0 (see stats.py's own module and
`_build_call_rows_codex` docstrings for the full real-data writeup).
"""

from __future__ import annotations

import json
from pathlib import Path

from nyxloom.session_extract import stats


def _token_count(ordinal: int, ts: str, *, input_tokens: int, cached: int = 0,
                  cache_write: int = 0, output_tokens: int = 0, reasoning: int = 0) -> dict:
    return {
        "timestamp": ts, "ordinal": ordinal, "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens, "cached_input_tokens": cached,
                    "cache_write_input_tokens": cache_write, "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning, "total_tokens": input_tokens + output_tokens,
                },
                "last_token_usage": {
                    "input_tokens": input_tokens, "cached_input_tokens": cached,
                    "cache_write_input_tokens": cache_write, "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning, "total_tokens": input_tokens + output_tokens,
                },
                "model_context_window": 258400,
            },
        },
    }


def _write_new_generation_fixture(tmp_path: Path) -> Path:
    # Mirrors real item_completed/token_count/compacted shapes (cli_version
    # >= 0.147.0), including the real finding that a routine `compacted`
    # record's `payload.message` is usually EMPTY (91% of 132 real records
    # checked) -- the nonempty case is a distinct resume-with-injected-
    # summary boundary, not this one.
    lines = [
        {"timestamp": "2026-01-01T00:00:00Z", "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": "abc", "cli_version": "0.151.0"}},
        {"timestamp": "2026-01-01T00:00:01Z", "ordinal": 1, "type": "turn_context",
         "payload": {"turn_id": "t1", "model": "gpt-5.6-sol"}},
        {"timestamp": "2026-01-01T00:00:02Z", "ordinal": 2, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "UserMessage", "content": [{"type": "text", "text": "evaluate the new schema"}]}}},
        {"timestamp": "2026-01-01T00:00:03Z", "ordinal": 3, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "AgentMessage", "content": [{"type": "Text", "text": "Confirmed, it works."}]}}},
        {"timestamp": "2026-01-01T00:00:04Z", "ordinal": 4, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "command": "ls"}}},
        _token_count(5, "2026-01-01T00:00:05Z", input_tokens=1200, cached=800, output_tokens=150, reasoning=40),
        {"timestamp": "2026-01-01T00:00:06Z", "ordinal": 6, "type": "compacted",
         "payload": {"message": "", "window_number": 1, "window_id": "w1",
                      "previous_window_id": "w0", "first_window_id": "w0",
                      "replacement_history": [{"type": "compaction", "encrypted_content": "gAAAA..."}]}},
        _token_count(7, "2026-01-01T00:00:07Z", input_tokens=2000, cached=1500,
                     cache_write=100, output_tokens=90, reasoning=10),
        {"timestamp": "2026-01-01T00:00:08Z", "ordinal": 8, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "AgentMessage", "content": [{"type": "Text", "text": "post-compaction reply"}]}}},
    ]
    fp = tmp_path / "rollout-new-schema.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return fp


def test_operator_and_assistant_rows_carry_zero_usage(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    rows = stats.build_call_rows(fp, fmt="codex")

    op = next(r for r in rows if r.kind == "operator")
    assert op.text_preview == "evaluate the new schema"
    assert op.input_tokens == 0 and op.output_tokens == 0

    asst = next(r for r in rows if r.text_preview == "Confirmed, it works.")
    assert asst.input_tokens == 0
    assert asst.kind in ("checkpoint", "assistant_minor")


def test_api_call_rows_carry_the_real_usage_ledger(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    rows = stats.build_call_rows(fp, fmt="codex")

    api_rows = [r for r in rows if r.kind == "api_call"]
    assert len(api_rows) == 2

    first = api_rows[0]
    assert first.cache_read_tokens == 800
    assert first.input_tokens == 1200 - 800  # fresh-only, cached subtracted out
    assert first.output_tokens == 150
    assert first.thinking_tokens == 40
    assert first.words == 0 and first.text_preview == ""
    assert first.model == "gpt-5.6-sol"  # stamped from the session's turn_context


def test_cache_write_is_also_subtracted_out_of_input_tokens_to_avoid_double_counting(tmp_path):
    # Real-data finding: Codex's last_token_usage.input_tokens is a SUBSET-
    # INCLUSIVE total (cached_input_tokens and cache_write_input_tokens are
    # both subsets of it, confirmed via total_tokens == input_tokens +
    # output_tokens holding exactly across thousands of real records) --
    # unlike Claude Code's/opencode's additive decomposition. Mapping the
    # subset fields onto CallRow's additive cache_read/cache_creation
    # fields WITHOUT this subtraction would double-count them into
    # context_size.
    fp = _write_new_generation_fixture(tmp_path)
    rows = stats.build_call_rows(fp, fmt="codex")
    second = [r for r in rows if r.kind == "api_call"][1]

    assert second.cache_read_tokens == 1500
    assert second.cache_creation_tokens == 100
    assert second.input_tokens == 2000 - 1500 - 100
    assert second.context_size == 2000  # no double count: back to the raw total


def test_compacted_marker_falls_back_to_placeholder_when_message_is_empty(tmp_path):
    # Real-data finding (see stats.py's _build_call_rows_codex docstring):
    # 91% of real `compacted` records carry an EMPTY payload.message -- the
    # real content is encrypted (replacement_history[-1].encrypted_content).
    fp = _write_new_generation_fixture(tmp_path)
    rows = stats.build_call_rows(fp, fmt="codex")
    lifecycle = next(r for r in rows if r.kind == "lifecycle")
    assert lifecycle.is_real_lifecycle is True
    assert lifecycle.compact_trigger is None  # Codex's own record carries no trigger/pre/post/duration
    assert lifecycle.compact_pre_tokens is None
    assert lifecycle.text_preview == "[compacted]"


def test_api_call_rows_fold_into_the_block_opened_by_the_nearest_preceding_boundary(tmp_path):
    # This is the "correlate a token_count event with the nearest
    # preceding real turn" behavior -- achieved by build_blocks (untouched,
    # generic) grouping every non-boundary row into whichever block is
    # currently open, not by any special-cased Codex logic.
    fp = _write_new_generation_fixture(tmp_path)
    rows = stats.build_call_rows(fp, fmt="codex")
    blocks = stats.build_blocks(rows)

    first_block = blocks[0]
    assert first_block.trigger_kind == "operator"
    assert any(True for _ in [first_block])  # sanity: block exists
    assert first_block.sum_input_tokens == 1200 - 800  # the first token_count's usage

    lifecycle_block = next(b for b in blocks if b.contains_real_lifecycle_marker)
    # This block has only one call after the boundary (the token_count
    # itself) -- it becomes the block's first_response, not a trailing
    # aggregate member (Block's own docstring: the first real response
    # after a boundary carries the cache-warmth signal on its own fields).
    assert lifecycle_block.first_response_input_tokens == 2000 - 1500 - 100  # the second token_count's usage


def test_old_generation_shape_is_supported(tmp_path):
    # Real old-generation (pre-0.147.0) files have NO `ordinal` field at
    # all -- verified directly against a real 2026-07 rollout file -- and
    # `context_compacted` (this generation's lifecycle marker) is entirely
    # content-free, no trigger/pre/post/duration keys whatsoever.
    lines = [
        {"timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
         "payload": {"type": "task_started", "turn_id": "t1"}},
        {"timestamp": "2026-01-01T00:00:01Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "first turn"}},
        {"timestamp": "2026-01-01T00:00:02Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "first reply"}},
        {"timestamp": "2026-01-01T00:00:03Z", "type": "event_msg", "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {"input_tokens": 500, "cached_input_tokens": 100,
                                       "output_tokens": 80, "reasoning_output_tokens": 5,
                                       "total_tokens": 580},
                "last_token_usage": {"input_tokens": 500, "cached_input_tokens": 100,
                                      "output_tokens": 80, "reasoning_output_tokens": 5,
                                      "total_tokens": 580},
                "model_context_window": 128000,
            },
        }},
        {"timestamp": "2026-01-01T00:00:04Z", "type": "event_msg",
         "payload": {"type": "context_compacted"}},
    ]
    fp = tmp_path / "rollout-old-schema.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    rows = stats.build_call_rows(fp, fmt="codex")
    kinds = [r.kind for r in rows]
    assert kinds == ["operator", "assistant_minor", "api_call", "lifecycle"]

    api_row = rows[2]
    assert api_row.input_tokens == 500 - 100  # cache_write_input_tokens absent in old gen -> defaults 0
    assert api_row.cache_read_tokens == 100

    lifecycle = rows[3]
    assert lifecycle.text_preview == "[context_compacted]"
    assert lifecycle.compact_trigger is None


def test_degenerate_all_zero_token_count_defaults_safely(tmp_path):
    # Real-data finding: a small fraction of real token_count records
    # report all-zero component fields with only total_tokens nonzero
    # (rate-limit/heartbeat-only pings, not a real per-call delta) --
    # this must degrade to zeros, not crash or fabricate a value.
    lines = [
        {"timestamp": "2026-01-01T00:00:00Z", "ordinal": 0, "type": "event_msg",
         "payload": {"type": "user_message", "message": "hi"}},
        _token_count(1, "2026-01-01T00:00:01Z", input_tokens=0, cached=0, output_tokens=0, reasoning=0),
    ]
    fp = tmp_path / "rollout.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    rows = stats.build_call_rows(fp, fmt="codex")
    api_row = next(r for r in rows if r.kind == "api_call")
    assert api_row.input_tokens == 0
    assert api_row.context_size == 0


def test_markers_stay_unique_and_ordered_when_token_count_and_events_interleave(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    rows = stats.build_call_rows(fp, fmt="codex")
    markers = [r.marker for r in rows]
    assert len(set(markers)) == len(markers)
    assert markers == sorted(markers, key=int)


def test_raw_scan_and_session_model_skip_blank_and_malformed_lines(tmp_path):
    lines = [
        "",
        "   ",
        "not json at all",
        json.dumps({"timestamp": "t0", "ordinal": 0, "type": "turn_context",
                     "payload": {"turn_id": "t1"}}),  # no "model" key -- keeps scanning
        json.dumps({"timestamp": "t1", "ordinal": 1, "type": "turn_context",
                     "payload": {"turn_id": "t1", "model": "gpt-5.6-sol"}}),
        json.dumps({"timestamp": "t2", "ordinal": 2, "type": "event_msg",
                     "payload": {"type": "user_message", "message": "survives"}}),
    ]
    fp = tmp_path / "rollout.jsonl"
    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rows = stats.build_call_rows(fp, fmt="codex")
    assert [r.text_preview for r in rows] == ["survives"]

    assert stats._codex_session_model(fp) == "gpt-5.6-sol"


def test_a_token_count_record_that_collides_with_an_existing_marker_is_skipped(tmp_path):
    # Defensive branch: a token_count event never legitimately coincides
    # with a real NormalizedEvent's marker (token_count carries no text, so
    # codex.parse() never emits an event for it) -- this can only happen on
    # a corrupt/duplicate-`ordinal` file. Must not double-count or crash.
    lines = [
        {"timestamp": "t0", "ordinal": 5, "type": "event_msg",
         "payload": {"type": "user_message", "message": "hello"}},
        {"timestamp": "t1", "ordinal": 5, "type": "event_msg",  # duplicate ordinal, corrupt file
         "payload": {"type": "token_count", "info": {
             "last_token_usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
             "total_token_usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
             "model_context_window": 128000}}},
    ]
    fp = tmp_path / "rollout.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    rows = stats.build_call_rows(fp, fmt="codex")
    assert len(rows) == 1  # the colliding token_count row was skipped, not appended twice
    assert rows[0].kind == "operator"


def test_render_detailed_csv_includes_codex_rows(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    rows = stats.build_call_rows(fp, fmt="codex")
    csv_text = stats.render_detailed_csv(rows)
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("marker,timestamp")
    assert len(lines) == 1 + len(rows)
    assert "api_call" in csv_text
