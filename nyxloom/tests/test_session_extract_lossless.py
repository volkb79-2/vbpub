"""lossless.dump_claude_code() tests: the independent "keep prose, drop
machine calls" dumper used to build a ground-truth superset for judging the
real classifier -- deliberately not sharing code with adapters/claude_code.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.session_extract import lossless


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


def test_unknown_since_marker_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="--since"):
        lossless.dump_claude_code(fp, since_marker="does-not-exist")


def test_unknown_until_marker_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="--until"):
        lossless.dump_claude_code(fp, until_marker="does-not-exist")


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
