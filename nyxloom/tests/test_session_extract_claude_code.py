"""Claude Code adapter + end-to-end extract() tests, against a small
synthetic fixture built to exercise every case the adapter's own docstring
claims to handle: real operator text, harness-injected isMeta noise, an
AskUserQuestion Q&A pair, an unrelated tool call (noise), a sidechain
record (excluded), a compact_boundary, and an explicit /compact command.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.session_extract import ExtractConfig, extract
from nyxloom.session_extract.adapters import claude_code
from nyxloom.session_extract.events import EventKind


def _rec(**kw):
    base = {"parentUuid": None, "sessionId": "s1", "isSidechain": False, "cwd": "/x", "gitBranch": "main"}
    base.update(kw)
    return base


def _write_fixture(tmp_path: Path) -> Path:
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "so how about the telegram alternative?"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [{"type": "text", "text": "Let me check that."}]}),
        _rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:02Z",
             message={"role": "assistant", "content": [
                 {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
             ]}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:03Z",
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "tu1", "content": "file1\nfile2"},
             ]}),
        _rec(type="user", uuid="u3", timestamp="2026-01-01T00:00:04Z", isMeta=True,
             message={"role": "user", "content": "<local-command-caveat>Caveat: ...</local-command-caveat>"}),
        _rec(type="assistant", uuid="a3", timestamp="2026-01-01T00:00:05Z",
             message={"role": "assistant", "content": [
                 {"type": "tool_use", "id": "aq1", "name": "AskUserQuestion",
                  "input": {"questions": [{"question": "Which host?", "header": "Host",
                                            "options": [{"label": "A", "description": "d"}], "multiSelect": False}]}},
             ]}),
        _rec(type="user", uuid="u4", timestamp="2026-01-01T00:00:06Z",
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "aq1",
                  "content": 'The user answered: "Which host?"="A"'},
             ]}),
        _rec(type="assistant", uuid="a4-sidechain", timestamp="2026-01-01T00:00:07Z", isSidechain=True,
             message={"role": "assistant", "content": [{"type": "text", "text": "x" * 5000}]}),
        _rec(type="assistant", uuid="a5", timestamp="2026-01-01T00:00:08Z",
             message={"role": "assistant", "content": [{"type": "text", "text": (
                 "## Status update\n\nDone -- everything landed. All four parts landed, "
                 "main clean at `abc123`. " + ("filler " * 200)
             )}]}),
        _rec(type="user", uuid="u5", timestamp="2026-01-01T00:00:09Z",
             message={"role": "user", "content": "proceed"}),
        _rec(type="user", uuid="u6", timestamp="2026-01-01T00:00:10Z",
             message={"role": "user",
                      "content": "<command-name>/compact</command-name>\n<command-message>compact</command-message>"}),
        _rec(type="system", uuid="sys1", timestamp="2026-01-01T00:00:11Z", subtype="compact_boundary"),
        _rec(type="assistant", uuid="a6", timestamp="2026-01-01T00:00:12Z",
             message={"role": "assistant", "content": [{"type": "text", "text": "should never be reached"}]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_sniff_and_list_sessions(tmp_path):
    fp = _write_fixture(tmp_path)
    assert claude_code.sniff(fp)
    assert claude_code.list_sessions(fp) == [str(fp)]
    assert not claude_code.sniff(tmp_path / "nope.jsonl")


def test_parse_shapes(tmp_path):
    fp = _write_fixture(tmp_path)
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    kinds = [e.kind for e in events]

    # sidechain and the plain tool_use/tool_result pair are absent
    assert not any(e.marker == "a4-sidechain" for e in events)
    assert not any(e.marker == "u2" for e in events)
    # the mid-work narration IS emitted as ASSISTANT_TEXT (classifier decides checkpoint-ness later)
    assert any(e.marker == "a1" and e.kind is EventKind.ASSISTANT_TEXT for e in events)
    # isMeta framing is dropped entirely
    assert not any(e.marker == "u3" for e in events)
    # AskUserQuestion answer becomes one QA_PAIR carrying the full rendered string
    qa = next(e for e in events if e.marker == "u4")
    assert qa.kind is EventKind.QA_PAIR
    assert "Which host?" in qa.text and "=\"A\"" in qa.text
    # /compact is promoted to a lifecycle marker, not plain operator text
    compact_ev = next(e for e in events if e.marker == "u6")
    assert compact_ev.kind is EventKind.LIFECYCLE_MARKER
    # the system compact_boundary is also a lifecycle marker
    assert any(e.marker == "sys1" and e.kind is EventKind.LIFECYCLE_MARKER for e in events)
    # parse() itself does not truncate at a lifecycle marker -- that's
    # select()'s job (see test_end_to_end_extract_stops_at_lifecycle_boundary)
    assert any(e.marker == "a6" for e in events)
    # order is preserved (source order == chronological here)
    assert [e.seq for e in events] == sorted(e.seq for e in events)


def test_since_marker_slices_forward(tmp_path):
    fp = _write_fixture(tmp_path)
    cfg = ExtractConfig(since_marker="u4")
    events = claude_code.parse(fp, str(fp), cfg)
    assert not any(e.marker in ("u1", "a1", "u4") for e in events)
    assert any(e.marker == "a5" for e in events)


def test_since_marker_unknown_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError):
        claude_code.parse(fp, str(fp), ExtractConfig(since_marker="does-not-exist"))


def test_end_to_end_extract_stops_at_lifecycle_boundary(tmp_path):
    # In this fixture the compact_boundary (sys1) sits right before the very
    # last event (a6). Selection walks backward from the newest event, hits
    # the boundary almost immediately, and stops THERE -- content newer than
    # the boundary (a6) survives, everything older (the whole earlier
    # conversation, including the "Which host?" Q&A and the a5 checkpoint)
    # is correctly excluded, since it belongs to what the boundary already
    # summarized away.
    fp = _write_fixture(tmp_path)
    result = extract(fp)
    assert result.format == "claude-code"
    text = result.render()
    assert "should never be reached" in text
    assert "compact boundary" in text
    assert "telegram alternative" not in text
    assert "Which host?" not in text
    # last_marker reflects the true end of the FULL parse, not just what survived selection
    assert result.last_marker == "a6"


def test_json_output_marks_checkpoint(tmp_path):
    # A dedicated, boundary-free fixture: the shared _write_fixture's
    # compact_boundary sits right before its checkpoint, which would always
    # exclude it (see test_end_to_end_extract_stops_at_lifecycle_boundary) --
    # this isolates "does a real checkpoint surface in --json output" from
    # that separate boundary behavior.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "status?"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [{"type": "text", "text": (
                 "## Status update\n\nDone -- everything landed. All four parts landed, "
                 "main clean at `abc123`."
             )}]}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:02Z",
             message={"role": "user", "content": "proceed"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    cfg = ExtractConfig(output_format="json")
    result = extract(fp, cfg)
    payload = json.loads(result.render())
    checkpoint_events = [e for e in payload["events"] if e["checkpoint"]]
    assert any("Status update" in e["text"] for e in checkpoint_events)
