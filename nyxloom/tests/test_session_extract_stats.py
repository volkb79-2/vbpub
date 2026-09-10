"""stats.py (V9 cost/timeline analysis) tests against a small synthetic
Claude Code fixture with real usage-block shapes (mirroring what real
session files actually carry, verified this session against real local
files -- see design-context-lifecycle-experiments.md's E-009).
"""

from __future__ import annotations

import json
from pathlib import Path

from nyxloom.session_extract import stats


def _rec(**kw):
    base = {"parentUuid": None, "sessionId": "s1", "isSidechain": False}
    base.update(kw)
    return base


def _usage(input_tokens=2, cache_creation=0, cache_read=0, output_tokens=10, thinking_tokens=0):
    return {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_creation": {"ephemeral_1h_input_tokens": cache_creation, "ephemeral_5m_input_tokens": 0},
        "cache_read_input_tokens": cache_read,
        "output_tokens": output_tokens,
        "output_tokens_details": {"thinking_tokens": thinking_tokens},
    }


def _write_fixture(tmp_path: Path) -> Path:
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "please look into the flaky test"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:05Z", effort="high",
             message={"role": "assistant", "model": "claude-sonnet-5", "usage": _usage(cache_creation=5000),
                       "content": [
                           {"type": "text", "text": "Let me check."},
                           {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "pytest"}},
                       ]}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:06Z",
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "tu1", "content": "1 failed"},
             ]}),
        _rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:20Z", effort="high",
             message={"role": "assistant", "model": "claude-sonnet-5",
                       "usage": _usage(cache_read=5000, output_tokens=400, thinking_tokens=50),
                       "content": [{"type": "text", "text": (
                           "## Status\n\nDone -- root cause confirmed, fix applied, tests green."
                       )}]}),
        _rec(type="system", subtype="compact_boundary", uuid="lc1", timestamp="2026-01-01T00:00:25Z",
             compactMetadata={"trigger": "auto", "preTokens": 900000, "postTokens": 12000, "durationMs": 150000}),
        _rec(type="user", uuid="lcs1", timestamp="2026-01-01T00:00:26Z", isCompactSummary=True,
             message={"role": "user", "content": "Summary of prior work."}),
        _rec(type="user", uuid="u3", timestamp="2026-01-01T00:00:30Z",
             message={"role": "user", "content": "great, what's next?"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_build_call_rows_pulls_real_usage_fields(tmp_path):
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)

    op = next(r for r in rows if r.marker == "u1")
    assert op.kind == "operator"
    assert op.words == 6

    a1 = next(r for r in rows if r.marker == "a1")
    assert a1.cache_creation_tokens == 5000
    assert a1.cache_creation_1h_tokens == 5000
    assert a1.tools_called == ["Bash"]
    assert a1.model == "claude-sonnet-5"
    assert a1.effort == "high"
    assert a1.kind == "assistant_minor"  # short, no finding signal, below checkpoint threshold

    a2 = next(r for r in rows if r.marker == "a2")
    assert a2.cache_read_tokens == 5000
    assert a2.thinking_tokens == 50
    assert a2.kind == "checkpoint"  # header + closure language
    assert a2.checkpoint_score is not None and a2.checkpoint_score > 0

    lc = next(r for r in rows if r.kind == "lifecycle" and r.is_real_lifecycle)
    assert lc.compact_trigger == "auto"
    assert lc.compact_pre_tokens == 900000
    assert lc.compact_post_tokens == 12000
    assert lc.compact_duration_ms == 150000


def test_elapsed_since_prev_tracks_real_gaps(tmp_path):
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    a1 = next(r for r in rows if r.marker == "a1")
    assert a1.elapsed_since_prev_s == 5.0  # u1 at :00, a1 at :05


def test_context_size_sums_the_three_usage_components(tmp_path):
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    a1 = next(r for r in rows if r.marker == "a1")
    assert a1.context_size == a1.input_tokens + a1.cache_creation_tokens + a1.cache_read_tokens


def test_profile_running_words_matches_what_select_would_keep(tmp_path):
    from nyxloom.session_extract.adapters import claude_code
    from nyxloom.session_extract.config import ExtractConfig
    from nyxloom.session_extract import classifier
    from nyxloom.session_extract.select import select

    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)

    from nyxloom.session_extract.events import EventKind

    events = claude_code.parse(fp, str(fp), ExtractConfig())
    classifier.score_events(events)
    kept = select(events, ExtractConfig())

    # select()'s own `word_count` budget variable never counts a
    # LIFECYCLE_MARKER's cosmetic label text (markers are exempt from the
    # word budget by design -- see select.py) -- match that semantics here.
    default_total = sum(len(e.text.split()) for e in kept if e.kind is not EventKind.LIFECYCLE_MARKER)
    last_kept_row = next(r for r in reversed(rows) if r.profile_running_words.get("default") is not None)
    assert last_kept_row.profile_running_words["default"] == default_total


def test_build_blocks_groups_by_prompt_boundary_and_flags_real_compaction(tmp_path):
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    blocks = stats.build_blocks(rows)

    # u1 opens block 1 (a1 + a2's tool-result-only turn folds in); the
    # compact_boundary opens its own block; u3 opens the final block.
    assert blocks[0].trigger_kind == "operator"
    assert blocks[0].trigger_text_preview.startswith("please look into")
    assert blocks[0].n_calls >= 1
    assert "Bash" in blocks[0].tool_call_counts

    lifecycle_block = next(b for b in blocks if b.contains_real_lifecycle_marker)
    assert lifecycle_block.trigger_kind == "lifecycle"

    assert blocks[-1].trigger_text_preview.startswith("great, what's next")


def test_render_detailed_csv_has_a_header_and_one_row_per_call(tmp_path):
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    csv_text = stats.render_detailed_csv(rows)
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("marker,timestamp")
    assert len(lines) == 1 + len(rows)


def test_render_condensed_shows_a_real_compaction_divider(tmp_path):
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    blocks = stats.build_blocks(rows)
    text = stats.render_condensed(blocks)
    assert "REAL COMPACTION" in text
    assert "TOTAL" not in text  # totals row has no literal label column collision
    assert str(len(blocks)) + " blocks" in text


def test_build_blocks_on_an_empty_row_list_returns_no_blocks():
    assert stats.build_blocks([]) == []


def test_raw_scan_skips_blank_and_malformed_lines(tmp_path):
    fp = tmp_path / "session.jsonl"
    fp.write_text(
        "\n   \nnot json at all\n"
        + json.dumps(_rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
                           message={"role": "user", "content": "hi"}))
        + "\n",
        encoding="utf-8",
    )
    rows = stats.build_call_rows(fp)
    assert [r.marker for r in rows] == ["u1"]


def test_simulate_profile_agrees_with_select_across_checkpoint_and_length_branches():
    # Drives _simulate_profile's checkpoint-limit break and long-comment
    # branch directly via hand-built events (precise control over score/
    # length), cross-checked against the real select() it duplicates.
    from nyxloom.session_extract.config import ExtractConfig
    from nyxloom.session_extract.events import EventKind, NormalizedEvent
    from nyxloom.session_extract.select import select

    ts = "2026-01-01T00:00:00Z"
    events = [
        NormalizedEvent(-1, "too_old", ts, EventKind.ASSISTANT_TEXT, "checkpoint too old", checkpoint_score=5.0),
        NormalizedEvent(0, "cp0", ts, EventKind.ASSISTANT_TEXT, "checkpoint zero", checkpoint_score=5.0),
        NormalizedEvent(1, "long1", ts, EventKind.ASSISTANT_TEXT, "x " * 100, checkpoint_score=0.0),
        NormalizedEvent(2, "cp2", ts, EventKind.ASSISTANT_TEXT, "checkpoint two", checkpoint_score=5.0),
        NormalizedEvent(3, "cp3", ts, EventKind.ASSISTANT_TEXT, "checkpoint three", checkpoint_score=5.0),
    ]
    config = ExtractConfig(max_checkpoints=3, long_comment_chars=10)

    running = stats._simulate_profile(events, config)
    kept = select(events, config)
    kept_total = sum(len(e.text.split()) for e in kept if e.kind is not EventKind.LIFECYCLE_MARKER)

    # max_checkpoints=3 walks past long1 (clears long_comment_chars=10, so
    # it survives on length alone) on the way to cp0, the 3rd checkpoint,
    # where the walk finally stops -- too_old (the 4th checkpoint) must
    # never appear.
    assert "long1" in running
    assert "too_old" not in running
    assert running["cp0"] == kept_total


def test_unsupported_format_raises_not_implemented(tmp_path):
    # codex/opencode are now both supported (see test_session_extract_stats_codex.py
    # and test_session_extract_stats_opencode.py) -- this exercises the
    # still-unsupported-format branch with an explicit fmt override, since
    # every REAL adapter this package ships is now wired up.
    fp = tmp_path / "session.jsonl"
    fp.write_text(json.dumps({"foo": "bar"}) + "\n", encoding="utf-8")
    try:
        stats.build_call_rows(fp, fmt="some-future-cli")
        assert False, "expected NotImplementedError"
    except NotImplementedError as e:
        assert "some-future-cli" in str(e)
