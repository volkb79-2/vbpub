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

    # u1 opens block 1 (a1 + a2's tool-result-only turn folds in as
    # agent-induced calls); the compact_boundary opens its own block; u3
    # opens the final block.
    assert blocks[0].trigger_kind == "operator"
    assert blocks[0].trigger_text_preview.startswith("please look into")
    assert blocks[0].has_response
    assert "Bash" in blocks[0].tool_call_counts

    lifecycle_block = next(b for b in blocks if b.contains_real_lifecycle_marker)
    # A real compaction is coalesced into its own kind="compaction" block
    # (see build_blocks's _coalesce_compaction_clusters), not left as an
    # ordinary "lifecycle"-kind block.
    assert lifecycle_block.trigger_kind == "compaction"
    assert lifecycle_block.compact_trigger == "auto"
    assert lifecycle_block.compact_pre_tokens == 900000
    assert lifecycle_block.compact_post_tokens == 12000
    assert lifecycle_block.compact_duration_ms == 150000

    assert blocks[-1].trigger_text_preview.startswith("great, what's next")


def test_build_blocks_first_response_carries_its_own_tokens_not_a_sum(tmp_path):
    # The boundary row (u1) itself never carries usage in any format
    # (confirmed against real data, see Block's own docstring) -- the
    # cache-warmth signal lives on the FIRST REAL RESPONSE (a1) instead,
    # and must NOT be folded into the trailing aggregate (which here is
    # only a2, since a1 IS the first response).
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    blocks = stats.build_blocks(rows)

    a1 = next(r for r in rows if r.marker == "a1")
    a2 = next(r for r in rows if r.marker == "a2")
    block = blocks[0]
    assert block.trigger_marker == "u1"
    assert block.has_response is True
    assert block.first_response_cache_creation_tokens == a1.cache_creation_tokens
    # a2's real usage (cache_read=5000) shows up only in the trailing sum,
    # never folded into the first response's own fields.
    assert block.sum_cache_read_tokens == a2.cache_read_tokens


def test_build_blocks_first_response_timestamp_is_the_first_responses_own_ts(tmp_path):
    # 2026-09-10, second round of operator feedback: "a row's elapsed time is
    # from its first call's start to its last call's return" -- corrects the
    # first cut's elapsed_since_prev_block_s (gap to the PREVIOUS block's
    # trigger, which mixes in operator think-time). first_response_timestamp
    # is what render_condensed now uses to compute each printed row's own
    # span (trigger -> first_response for the numbered row, first_response
    # -> end_ts for the trailing ↳agents row).
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    blocks = stats.build_blocks(rows)
    a1 = next(r for r in rows if r.marker == "a1")
    assert blocks[0].first_response_timestamp == a1.timestamp
    # A block with no real response at all carries no first_response_timestamp.
    assert blocks[-1].first_response_timestamp == ""


def test_render_detailed_csv_has_a_header_and_one_row_per_call(tmp_path):
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    csv_text = stats.render_detailed_csv(rows)
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("marker,timestamp")
    assert len(lines) == 1 + len(rows)


def test_render_condensed_shows_a_typed_compaction_row(tmp_path):
    # 2026-09-10, second round of operator feedback: a real compaction is an
    # ordinary numbered ROW (kind="compaction"), not a separate divider line
    # -- the type/stats move into the bracketed trigger-text field instead.
    fp = _write_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    blocks = stats.build_blocks(rows)
    text = stats.render_condensed(blocks)
    assert "=====" not in text  # no more divider chrome
    assert " compaction " in text  # the kind column
    # The fixture's compactMetadata.trigger is "auto" -> "Auto" label.
    assert "[Auto, LLM-Endpoint, 900,000→12,000 tok, 150.0s]" in text
    assert str(len(blocks)) + " blocks total" in text


def test_render_condensed_suppresses_uncoalesced_lifecycle_marker_rows():
    # render_condensed's OWN suppression only ever sees kind=="lifecycle" --
    # a REAL compaction never reaches it that way (build_blocks's
    # _coalesce_compaction_clusters already relabels it kind="compaction"
    # before render_condensed runs); this test exercises render_condensed in
    # isolation, feeding it raw lifecycle-kind blocks directly, the shape
    # it would see for the "no real member in this run" edge case
    # _coalesce_compaction_clusters deliberately leaves untouched.
    from nyxloom.session_extract.stats import Block

    real = Block(
        trigger_marker="lc1", trigger_kind="lifecycle", trigger_text_preview="[compact boundary]",
        trigger_timestamp="2026-01-01T00:00:00Z",
        has_response=False, first_response_input_tokens=0, first_response_cache_creation_tokens=0,
        first_response_cache_read_tokens=0, first_response_output_tokens=0, first_response_context_size=0,
        first_response_timestamp="", start_ts="", end_ts="",
        n_trailing_calls=0, n_checkpoints=0, n_minor_updates=0, tool_call_counts={},
        sum_input_tokens=0, sum_cache_creation_tokens=0, sum_cache_read_tokens=0,
        sum_output_tokens=0, sum_cost_usd=0.0, peak_context_size=0,
        contains_real_lifecycle_marker=True, compact_trigger="manual",
        compact_pre_tokens=500000, compact_post_tokens=10000, compact_duration_ms=100000,
    )
    summary = Block(
        trigger_marker="lcs1", trigger_kind="lifecycle", trigger_text_preview="[compact summary]",
        trigger_timestamp="2026-01-01T00:00:01Z",
        has_response=False, first_response_input_tokens=0, first_response_cache_creation_tokens=0,
        first_response_cache_read_tokens=0, first_response_output_tokens=0, first_response_context_size=0,
        first_response_timestamp="", start_ts="", end_ts="",
        n_trailing_calls=0, n_checkpoints=0, n_minor_updates=0, tool_call_counts={},
        sum_input_tokens=0, sum_cache_creation_tokens=0, sum_cache_read_tokens=0,
        sum_output_tokens=0, sum_cost_usd=0.0, peak_context_size=0,
        contains_real_lifecycle_marker=False,
    )
    text = stats.render_condensed([real, summary])
    assert "[compact boundary]" not in text
    assert "[compact summary]" not in text
    assert "0 rows shown, 2 blocks total" in text


def test_render_condensed_suppresses_bare_compact_dispatch_rows():
    from nyxloom.session_extract.stats import Block

    real_ask = Block(
        trigger_marker="op1", trigger_kind="operator", trigger_text_preview="give me a compaction prompt",
        trigger_timestamp="2026-01-01T00:00:00Z",
        has_response=True, first_response_input_tokens=1, first_response_cache_creation_tokens=0,
        first_response_cache_read_tokens=0, first_response_output_tokens=0, first_response_context_size=1,
        first_response_timestamp="2026-01-01T00:00:05Z", start_ts="", end_ts="",
        n_trailing_calls=0, n_checkpoints=0, n_minor_updates=0, tool_call_counts={},
        sum_input_tokens=0, sum_cache_creation_tokens=0, sum_cache_read_tokens=0,
        sum_output_tokens=0, sum_cost_usd=0.0, peak_context_size=1,
        contains_real_lifecycle_marker=False,
    )
    dispatch = Block(
        trigger_marker="op2", trigger_kind="operator", trigger_text_preview="/compact KEEP: exact per-package state",
        trigger_timestamp="2026-01-01T00:00:01Z",
        has_response=False, first_response_input_tokens=0, first_response_cache_creation_tokens=0,
        first_response_cache_read_tokens=0, first_response_output_tokens=0, first_response_context_size=0,
        first_response_timestamp="", start_ts="", end_ts="",
        n_trailing_calls=0, n_checkpoints=0, n_minor_updates=0, tool_call_counts={},
        sum_input_tokens=0, sum_cache_creation_tokens=0, sum_cache_read_tokens=0,
        sum_output_tokens=0, sum_cost_usd=0.0, peak_context_size=0,
        contains_real_lifecycle_marker=False,
    )
    text = stats.render_condensed([real_ask, dispatch])
    assert "give me a compaction prompt" in text
    assert "/compact KEEP" not in text
    assert "1 rows shown, 2 blocks total" in text


def test_render_condensed_shows_first_response_and_trailing_rows_separately():
    from nyxloom.session_extract.stats import Block

    block = Block(
        trigger_marker="op1", trigger_kind="operator", trigger_text_preview="do the thing",
        trigger_timestamp="2026-01-01T00:00:00Z",
        has_response=True, first_response_input_tokens=5, first_response_cache_creation_tokens=100,
        first_response_cache_read_tokens=200000, first_response_output_tokens=0,
        first_response_context_size=200105, first_response_timestamp="2026-01-01T00:00:05Z",
        start_ts="", end_ts="2026-01-01T00:01:00Z",
        n_trailing_calls=3, n_checkpoints=1, n_minor_updates=2,
        tool_call_counts={"Bash": 5, "Edit": 2},
        sum_input_tokens=10, sum_cache_creation_tokens=5000, sum_cache_read_tokens=1000,
        sum_output_tokens=900, sum_cost_usd=0.0, peak_context_size=210000,
        contains_real_lifecycle_marker=False,
    )
    text = stats.render_condensed([block])
    lines = [line for line in text.splitlines() if line.strip()]
    trigger_line = next(line for line in lines if "do the thing" in line)
    agent_line = next(line for line in lines if "↳agents" in line)
    # The first response's own high cache_read (200000) must appear on ITS
    # row -- this is the actual cache-warmth signal for the operator's
    # prompt -- not summed with the trailing aggregate's much smaller
    # cache_read (1000).
    assert "200000" in trigger_line
    assert "1000" in agent_line and "200000" not in agent_line
    assert "Bash×5" in agent_line


def test_render_condensed_no_cost_column():
    from nyxloom.session_extract.stats import Block

    block = Block(
        trigger_marker="op1", trigger_kind="operator", trigger_text_preview="hi",
        trigger_timestamp="2026-01-01T00:00:00Z",
        has_response=False, first_response_input_tokens=0, first_response_cache_creation_tokens=0,
        first_response_cache_read_tokens=0, first_response_output_tokens=0, first_response_context_size=0,
        first_response_timestamp="", start_ts="", end_ts="",
        n_trailing_calls=0, n_checkpoints=0, n_minor_updates=0, tool_call_counts={},
        sum_input_tokens=0, sum_cache_creation_tokens=0, sum_cache_read_tokens=0,
        sum_output_tokens=0, sum_cost_usd=1.2345, peak_context_size=0,
        contains_real_lifecycle_marker=False,
    )
    text = stats.render_condensed([block])
    assert "cost" not in text.lower()
    assert "1.2345" not in text


def _write_compaction_recovery_fixture(tmp_path: Path) -> Path:
    # Reproduces the exact real-data pattern found in the dstdns 8ebff140
    # replay (2026-09-10, second round of operator feedback): a raw-typed
    # `/compact ...` dispatch (kind=operator, since it's not wrapped in
    # Claude Code's own <command-name> harness tag), immediately followed by
    # the real `[compact boundary]` system record and a `[compact summary]`
    # synthetic one -- THREE back-to-back near-content-free boundary rows --
    # and only THEN the real post-compaction recovery work. The first cut of
    # this view suppressed every lifecycle-triggered block outright, which
    # silently dropped that recovery work (a3/a4 below) from the rendered
    # output entirely, not just the three boundary markers.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "give me a compaction prompt"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:05Z",
             message={"role": "assistant", "model": "claude-sonnet-5",
                       "usage": _usage(cache_read=900000), "content": [{"type": "text", "text": "Here it is."}]}),
        _rec(type="user", uuid="u_dispatch", timestamp="2026-01-01T00:00:06Z",
             message={"role": "user", "content": "/compact KEEP: exact per-package state"}),
        _rec(type="system", subtype="compact_boundary", uuid="lc1", timestamp="2026-01-01T00:00:10Z",
             compactMetadata={"trigger": "manual", "preTokens": 500000, "postTokens": 10000, "durationMs": 100000}),
        _rec(type="user", uuid="lcs1", timestamp="2026-01-01T00:00:11Z", isCompactSummary=True,
             message={"role": "user", "content": "Summary of prior work."}),
        _rec(type="assistant", uuid="a3", timestamp="2026-01-01T00:00:15Z",
             message={"role": "assistant", "model": "claude-sonnet-5",
                       "usage": _usage(cache_read=40000), "content": [{"type": "text", "text": "Resuming work."}]}),
        _rec(type="assistant", uuid="a4", timestamp="2026-01-01T00:00:20Z",
             message={"role": "assistant", "model": "claude-sonnet-5",
                       "usage": _usage(cache_read=45000), "content": [
                           {"type": "text", "text": "Continuing."},
                           {"type": "tool_use", "id": "tu2", "name": "Bash", "input": {"command": "git status"}},
                       ]}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:25Z",
             message={"role": "user", "content": "what's next?"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_coalesce_compaction_cluster_keeps_the_real_post_compaction_work_visible(tmp_path):
    fp = _write_compaction_recovery_fixture(tmp_path)
    rows = stats.build_call_rows(fp)
    blocks = stats.build_blocks(rows)
    a3 = next(r for r in rows if r.marker == "a3")
    a4 = next(r for r in rows if r.marker == "a4")

    # u1 ("give me a compaction prompt"), the merged compaction cluster
    # (u_dispatch + lc1 + lcs1), and u2 -- three blocks, not five: the run
    # of three back-to-back boundary-only records collapses into one.
    assert len(blocks) == 3
    compaction = blocks[1]
    assert compaction.trigger_kind == "compaction"
    assert compaction.contains_real_lifecycle_marker is True
    # Earliest timestamp in the cluster (the raw dispatch), not the system
    # record's own (later) timestamp -- closest to "when did the operator
    # actually trigger this."
    assert compaction.trigger_timestamp == "2026-01-01T00:00:06Z"
    assert compaction.compact_trigger == "manual"
    assert compaction.compact_pre_tokens == 500000
    assert compaction.compact_post_tokens == 10000
    # The real post-compaction work (a3/a4) is NOT dropped -- it's the
    # merged block's own first_response/trailing content, inherited from
    # the run's LAST member (lcs1's own block), exactly as if lcs1 itself
    # had opened this block.
    assert compaction.has_response is True
    assert compaction.first_response_cache_read_tokens == a3.cache_read_tokens
    assert compaction.n_trailing_calls == 1
    assert compaction.sum_cache_read_tokens == a4.cache_read_tokens

    text = stats.render_condensed(blocks)
    assert "[Steered, LLM-Endpoint, 500,000→10,000 tok, 100.0s]" in text
    # The recovered work is visible in the rendered table, not silently
    # absorbed into a suppressed block's totals.
    assert "40000" in text
    assert "45000" in text
    assert "Bash×1" in text


def test_coalesce_compaction_clusters_leaves_a_run_with_no_real_member_untouched():
    # _coalesce_compaction_clusters's own docstring: a lifecycle-kind
    # cluster run where NEITHER member carries a real compaction "shouldn't
    # normally happen, but not assumed away" -- left block-for-block, not
    # merged into a synthetic kind="compaction" block.
    from nyxloom.session_extract.stats import Block

    def _lifecycle_block(marker, preview, ts):
        return Block(
            trigger_marker=marker, trigger_kind="lifecycle", trigger_text_preview=preview,
            trigger_timestamp=ts,
            has_response=False, first_response_input_tokens=0, first_response_cache_creation_tokens=0,
            first_response_cache_read_tokens=0, first_response_output_tokens=0, first_response_context_size=0,
            first_response_timestamp="", start_ts="", end_ts="",
            n_trailing_calls=0, n_checkpoints=0, n_minor_updates=0, tool_call_counts={},
            sum_input_tokens=0, sum_cache_creation_tokens=0, sum_cache_read_tokens=0,
            sum_output_tokens=0, sum_cost_usd=0.0, peak_context_size=0,
            contains_real_lifecycle_marker=False,
        )

    b1 = _lifecycle_block("lc1", "[compact boundary]", "2026-01-01T00:00:00Z")
    b2 = _lifecycle_block("lc2", "[compact summary]", "2026-01-01T00:00:01Z")
    assert stats._coalesce_compaction_clusters([b1, b2]) == [b1, b2]


def test_fmt_elapsed_formats_hours_and_minutes_branches():
    assert stats._fmt_elapsed(None) == ""
    assert stats._fmt_elapsed(45) == "45s"
    assert stats._fmt_elapsed(125) == "2m05s"
    assert stats._fmt_elapsed(3725) == "1h02m"


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
