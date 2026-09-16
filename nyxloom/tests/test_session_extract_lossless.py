"""lossless.dump_claude_code() tests: the independent "keep prose, drop
machine calls" dumper used to build a ground-truth superset for judging the
real classifier -- deliberately not sharing code with adapters/claude_code.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.session_extract import lossless, read_since_marker


def _rec(**kw):
    base = {"parentUuid": None, "sessionId": "s1", "isSidechain": False}
    base.update(kw)
    return base


def _write_fixture(tmp_path: Path) -> Path:
    records = [
        _rec(type="mode", mode="normal"),
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "real operator prompt"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [
                 {"type": "text", "text": "assistant prose"},
                 {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
             ]}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:02Z",
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "tu1", "content": "file1\nfile2"},
             ]}),
        _rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:03Z",
             message={"role": "assistant", "content": [
                 {"type": "thinking", "text": "internal reasoning"},
             ]}),
        _rec(type="system", uuid="sys1", timestamp="2026-01-01T00:00:04Z", subtype="compact_boundary",
             content="Conversation compacted"),
        _rec(type="attachment", uuid="att1", attachment={"type": "x"}),
        _rec(type="user", uuid="u3", timestamp="2026-01-01T00:00:05Z", isMeta=True,
             message={"role": "user", "content": "<local-command-caveat>Caveat</local-command-caveat>"}),
        _rec(type="assistant", uuid="a3", timestamp="2026-01-01T00:00:06Z",
             message={"role": "assistant", "content": [{"type": "text", "text": "final prose"}]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_keeps_prose_drops_tool_calls_and_bookkeeping(tmp_path):
    fp = _write_fixture(tmp_path)
    out = lossless.dump_claude_code(fp)

    assert "real operator prompt" in out
    assert "assistant prose" in out
    assert "internal reasoning" in out  # thinking IS kept -- lossless, unlike the smart adapter
    assert "final prose" in out
    assert "Conversation compacted" in out

    # machine calls and bookkeeping are dropped
    assert "Bash" not in out
    assert "file1" not in out
    assert '"type": "x"' not in out

    # NOT the smart adapter's classification: isMeta content is still prose
    # and IS kept here (this is a lossless dump, not the curated extraction)
    assert "Caveat" in out


def test_since_marker_slices_forward(tmp_path):
    fp = _write_fixture(tmp_path)
    out = lossless.dump_claude_code(fp, since_marker="u1")
    assert "real operator prompt" not in out
    assert "assistant prose" in out
    assert "final prose" in out


def test_until_marker_slices_backward_inclusive(tmp_path):
    fp = _write_fixture(tmp_path)
    out = lossless.dump_claude_code(fp, until_marker="a2")
    assert "real operator prompt" in out
    assert "internal reasoning" in out  # the until-marker event itself is included
    assert "final prose" not in out  # after the until marker: excluded


def test_since_and_until_together_bound_a_span(tmp_path):
    fp = _write_fixture(tmp_path)
    out = lossless.dump_claude_code(fp, since_marker="u1", until_marker="a2")
    assert "real operator prompt" not in out
    assert "assistant prose" in out
    assert "internal reasoning" in out
    assert "final prose" not in out


def test_claude_dump_emits_a_resumable_marker_for_uuidless_records(tmp_path):
    records = [
        _rec(type="user", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "first"}),
        _rec(type="assistant", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [{"type": "text", "text": "second"}]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    first = lossless.dump_claude_code(fp, until_marker="line0")
    prior = tmp_path / "prior.txt"
    prior.write_text(first, encoding="utf-8")

    assert read_since_marker(prior) == ("claude-code", "line0")
    resumed = lossless.dump_claude_code(fp, since_marker="line0")
    assert "first" not in resumed
    assert "second" in resumed
    assert "marker=line1" in resumed


def test_unknown_since_marker_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="--since"):
        lossless.dump_claude_code(fp, since_marker="does-not-exist")


def test_unknown_until_marker_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="--until"):
        lossless.dump_claude_code(fp, until_marker="does-not-exist")


def test_render_dump_omits_footer_without_a_last_marker():
    assert lossless._render_dump([], "reasonix", None) == "\n"


def test_reasonix_lossless_skips_blank_and_malformed_lines_before_rejecting_snapshot(
    tmp_path,
):
    fp = tmp_path / "reasonix.jsonl"
    fp.write_text(
        "\nnot json\n"
        + json.dumps({"role": "user", "content": "before"})
        + "\n"
        + json.dumps({"type": "append", "messages": []})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="events snapshot"):
        lossless.dump_reasonix(fp)


def test_reasonix_lossless_rejects_an_events_companion_path(tmp_path):
    fp = tmp_path / "session.events.jsonl"
    fp.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="events snapshot"):
        lossless.dump_reasonix(fp)


def test_skips_blank_and_malformed_lines(tmp_path):
    fp = tmp_path / "session.jsonl"
    fp.write_text(
        "\n   \nnot json at all\n"
        + json.dumps(_rec(type="assistant", uuid="a1", timestamp="t",
                           message={"role": "assistant", "content": [{"type": "text", "text": "survives"}]}))
        + "\n",
        encoding="utf-8",
    )
    out = lossless.dump_claude_code(fp)
    assert "survives" in out


def test_claude_code_blocks_is_the_dumps_own_per_record_rule():
    # The seam `extract-lossless --follow` streams through: header and prose
    # kept apart (follow.py's attention detection scores the PROSE, never
    # nyxloom's own header framing).
    from nyxloom.session_extract.lossless import claude_code_blocks

    rec = {"type": "assistant", "uuid": "a1", "timestamp": "2026-01-01T00:00:00Z",
           "message": {"role": "assistant", "content": [
               {"type": "text", "text": "Done."},
               {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
               {"type": "thinking", "thinking": "hmm", "signature": "sig"},
           ]}}
    blocks = claude_code_blocks(rec, "L7")
    assert [b.text for b in blocks] == ["Done.", "hmm"]
    assert blocks[0].header == "===[a1 | 2026-01-01T00:00:00Z | ASSISTANT text]==="
    assert blocks[0].render() == blocks[0].header + "\nDone."
    # bookkeeping records contribute nothing
    assert claude_code_blocks({"type": "mode", "mode": "normal"}, "L8") == []
    # a uuid-less record falls back to the caller's marker
    assert claude_code_blocks(
        {"type": "user", "timestamp": "t", "message": {"role": "user", "content": "hi"}}, "L9"
    )[0].header.startswith("===[L9 |")


def test_claude_code_blocks_surfaces_every_drop_reason_flag():
    from nyxloom.session_extract.lossless import claude_code_blocks

    rec = {"type": "user", "uuid": "u1", "timestamp": "t", "isMeta": True, "isSidechain": True,
           "message": {"role": "user", "content": "framing"}}
    header = claude_code_blocks(rec, "L0")[0].header
    assert "isMeta" in header and "isSidechain" in header


def test_claude_code_blocks_drops_blank_string_content():
    from nyxloom.session_extract.lossless import claude_code_blocks

    rec = {"type": "user", "uuid": "u1", "timestamp": "t",
           "message": {"role": "user", "content": "   "}}
    assert claude_code_blocks(rec, "L0") == []


def test_claude_code_blocks_drops_records_without_content():
    from nyxloom.session_extract.lossless import claude_code_blocks

    assert claude_code_blocks({"type": "user", "uuid": "u1", "timestamp": "t"}, "L0") == []


def test_claude_code_blocks_drops_blank_list_blocks():
    from nyxloom.session_extract.lossless import claude_code_blocks

    rec = {"type": "assistant", "uuid": "a1", "timestamp": "t",
           "message": {"role": "assistant", "content": [
               {"type": "text", "text": "   "},
               {"type": "thinking", "thinking": ""},
           ]}}
    assert claude_code_blocks(rec, "L0") == []


def test_thinking_blocks_are_dumped_from_their_own_field(tmp_path):
    # Regression for a real bug (2026-09-12): this dumper read
    # `block["text"]` for `thinking` blocks too, so every one dumped empty
    # and vanished -- contradicting this module's own "lossless means
    # lossless, include_thinking plays no part here" claim. Real thinking
    # blocks (5469 checked across 40 real sessions, zero exceptions) carry
    # {type, thinking, signature} and NO `text` key.
    fp = tmp_path / "session.jsonl"
    fp.write_text(json.dumps({
        "type": "assistant", "uuid": "a1", "sessionId": "s1", "parentUuid": None,
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "the real reasoning text", "signature": "sig"},
            {"type": "text", "text": "Done."},
        ]},
    }) + "\n", encoding="utf-8")
    out = lossless.dump_claude_code(fp)
    assert "the real reasoning text" in out
    assert "ASSISTANT thinking]===" in out


def test_reasonix_lossless_does_not_accept_non_string_reasoning_content():
    from nyxloom.session_extract.lossless import reasonix_blocks

    blocks = reasonix_blocks({
        "role": "assistant",
        "content": "answer",
        "reasoning_content": {"private": "not prose"},
    }, "line0")
    assert [block.text for block in blocks] == ["answer"]


def test_lossless_dumper_skips_a_json_record_that_is_not_a_mapping(tmp_path):
    fp = tmp_path / "session.jsonl"
    fp.write_text(
        "[]\n"
        + json.dumps({
            "type": "assistant", "uuid": "a1", "timestamp": "t",
            "message": {"role": "assistant", "content": "kept"},
        }) + "\n",
        encoding="utf-8",
    )
    out = lossless.dump_claude_code(fp)
    assert "kept" in out
