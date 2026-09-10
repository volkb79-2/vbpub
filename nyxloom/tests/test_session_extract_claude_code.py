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
             message={"role": "assistant", "content": [{"type": "text", "text": (
                 "Found it -- this line should surface because it postdates the "
                 "compact boundary, not because of its own length or shape."
             )}]}),
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


def test_until_marker_slices_backward_inclusive(tmp_path):
    fp = _write_fixture(tmp_path)
    cfg = ExtractConfig(until_marker="a1")
    events = claude_code.parse(fp, str(fp), cfg)
    assert any(e.marker == "a1" for e in events)
    assert not any(e.marker == "a5" for e in events)


def test_since_and_until_together_bound_a_span(tmp_path):
    fp = _write_fixture(tmp_path)
    cfg = ExtractConfig(since_marker="u4", until_marker="a5")
    events = claude_code.parse(fp, str(fp), cfg)
    markers = {e.marker for e in events}
    assert "a5" in markers
    assert not markers & {"u1", "a1", "u4"}
    assert "u5" not in markers  # after the until marker


def test_until_marker_unknown_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError):
        claude_code.parse(fp, str(fp), ExtractConfig(until_marker="does-not-exist"))


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
    assert "postdates the compact boundary" in text
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


def test_sniff_skips_malformed_json_lines_and_directories(tmp_path):
    fp = tmp_path / "session.jsonl"
    fp.write_text("not json at all\n" + json.dumps(_rec(type="user", uuid="u1")) + "\n", encoding="utf-8")
    assert claude_code.sniff(fp)

    a_dir = tmp_path / "adir.jsonl"
    a_dir.mkdir()
    assert not claude_code.sniff(a_dir)  # open() raises IsADirectoryError (an OSError) -> False


def test_sniff_rejects_non_jsonl_suffix(tmp_path):
    fp = tmp_path / "session.txt"
    fp.write_text(json.dumps(_rec(type="user", uuid="u1")) + "\n")
    assert not claude_code.sniff(fp)


def test_sniff_skips_blank_lines(tmp_path):
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n   \n" + json.dumps(_rec(type="user", uuid="u1")) + "\n", encoding="utf-8")
    assert claude_code.sniff(fp)


def test_sniff_gives_up_past_the_scan_window(tmp_path):
    # A real match beyond _SNIFF_SCAN_LINES (50) must NOT be found -- the
    # scan-forward-a-bit design is deliberately bounded, not unlimited.
    housekeeping = [json.dumps({"type": "mode", "value": "plan"}) for _ in range(60)]
    real = json.dumps(_rec(type="user", uuid="u1"))
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(housekeeping + [real]) + "\n", encoding="utf-8")
    assert not claude_code.sniff(fp)


def test_load_records_skips_malformed_json_lines(tmp_path):
    fp = tmp_path / "session.jsonl"
    fp.write_text(
        "garbage, not json\n"
        + json.dumps(_rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
                           message={"role": "user", "content": "hello"}))
        + "\n",
        encoding="utf-8",
    )
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert [e.marker for e in events] == ["u1"]


def test_load_records_skips_blank_lines(tmp_path):
    fp = tmp_path / "session.jsonl"
    fp.write_text(
        "\n   \n"
        + json.dumps(_rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
                           message={"role": "user", "content": "hello"}))
        + "\n",
        encoding="utf-8",
    )
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert [e.marker for e in events] == ["u1"]


def test_user_content_neither_list_nor_string_is_dropped(tmp_path):
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": None}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert events == []


def test_operator_text_that_is_only_harness_tags_is_dropped(tmp_path):
    # After stripping harness wrapper tags, nothing real is left -- must not
    # surface as an empty-string OPERATOR_TEXT event.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "<local-command-caveat>Caveat: ...</local-command-caveat>"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert events == []


def test_thinking_block_included_only_when_configured(tmp_path):
    records = [
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "assistant", "content": [
                 "not-a-dict-block",
                 {"type": "thinking", "thinking": "reasoning about the bug"},
             ]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    default_events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert not any(e.kind is EventKind.THINKING for e in default_events)

    with_thinking = claude_code.parse(fp, str(fp), ExtractConfig(include_thinking=True))
    thinking_ev = next(e for e in with_thinking if e.kind is EventKind.THINKING)
    assert thinking_ev.text == "reasoning about the bug"


def test_is_compact_summary_flag_is_a_lifecycle_marker(tmp_path):
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z", isCompactSummary=True,
             message={"role": "user", "content": "whatever the summary body is"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert len(events) == 1
    assert events[0].kind is EventKind.LIFECYCLE_MARKER
    assert events[0].text == "[compact summary]"


def test_user_list_content_with_only_text_blocks_becomes_operator_text(tmp_path):
    # A real operator turn can arrive as a list containing a lone "text"
    # block instead of a plain string -- both shapes mean the same thing.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": [
                 "not-a-dict-block",
                 {"type": "text", "text": "please continue"},
             ]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert len(events) == 1
    assert events[0].kind is EventKind.OPERATOR_TEXT
    assert events[0].text == "please continue"


def test_user_list_content_with_no_text_and_no_tool_result_is_dropped(tmp_path):
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": [{"type": "image", "source": {}}]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert events == []


def test_askuserquestion_answer_with_non_string_content_is_json_dumped(tmp_path):
    records = [
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "assistant", "content": [
                 {"type": "tool_use", "id": "aq1", "name": "AskUserQuestion",
                  "input": {"questions": [{"question": "Which?", "header": "H",
                                            "options": [{"label": "A", "description": "d"}],
                                            "multiSelect": False}]}},
             ]}),
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "aq1", "content": {"answer": "A"}},
             ]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    qa = next(e for e in events if e.kind is EventKind.QA_PAIR)
    assert json.loads(qa.text) == {"answer": "A"}


# Adversarial-review regression tests, one per finding.


def test_custom_command_args_survive_as_operator_text(tmp_path):
    # Review finding: <command-args> was stripped along with its content --
    # a custom command's typed argument text (real operator intent, not
    # harness noise) was silently dropped entirely, with zero trace.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": (
                 "<command-name>my-custom-cmd</command-name>\n"
                 "<command-message>my-custom-cmd</command-message>\n"
                 "<command-args>please review the auth module for security holes</command-args>"
             )}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert len(events) == 1
    assert events[0].kind is EventKind.OPERATOR_TEXT
    assert "please review the auth module for security holes" in events[0].text


def test_compact_command_guidance_text_survives_in_the_lifecycle_label(tmp_path):
    # Milder version of the same bug: /compact's own guidance text ("focus
    # on X, drop Y") must not be thrown away just because /compact is a
    # lifecycle command.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": (
                 "<command-name>compact</command-name>\n"
                 "<command-args>focus on the auth bug, drop the docs tangent</command-args>"
             )}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert events[0].kind is EventKind.LIFECYCLE_MARKER
    assert "focus on the auth bug" in events[0].text


def test_command_name_tag_merely_mentioned_in_prose_is_not_a_real_command(tmp_path):
    # Review finding: an unanchored search matched <command-name> ANYWHERE
    # in the text, so prose merely discussing this tool's own tag format
    # was misclassified as a real /compact invocation and hard-stopped the
    # backward walk.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": (
                 "Please note the format looks like <command-name>compact</command-name> "
                 "in the logs, can you handle that case?"
             )}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert events[0].kind is EventKind.OPERATOR_TEXT
    assert "in the logs, can you handle that case?" in events[0].text


def test_task_notification_embedded_mid_message_is_stripped_not_the_whole_message(tmp_path):
    # Review finding: the old check only looked at the START of the text,
    # so a notification appearing anywhere else survived verbatim as
    # OPERATOR_TEXT -- a provenance-smuggling risk (controller-injected
    # content rendered as trustworthy operator intent). The fix strips the
    # notification block wherever it appears, preserving real surrounding
    # operator text rather than either keeping the raw block or discarding
    # genuine commentary along with it.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": (
                 "By the way, <task-notification><task-id>x</task-id>"
                 "<result>should not survive</result></task-notification> "
                 "please keep going"
             )}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert len(events) == 1
    assert events[0].kind is EventKind.OPERATOR_TEXT
    assert "should not survive" not in events[0].text
    assert "By the way" in events[0].text and "please keep going" in events[0].text


def test_real_text_alongside_an_unrelated_tool_result_is_not_dropped(tmp_path):
    # Review finding: a user record mixing a real text block with an
    # unrelated tool_result block (not an AskUserQuestion answer) in the
    # same content list dropped the real text entirely.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "tu1", "content": "irrelevant"},
                 {"type": "text", "text": "actually stop, use a different approach instead"},
             ]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert len(events) == 1
    assert events[0].kind is EventKind.OPERATOR_TEXT
    assert "actually stop" in events[0].text


def test_since_marker_resolves_a_uuid_less_record_via_the_same_fallback(tmp_path):
    # Review finding: a record without a uuid gets a synthetic f"line{seq}"
    # marker at generation time, but resolution compared only against the
    # raw uuid field, never replicating that fallback -- an extraction
    # ending on a marker-less record produced a last_marker that could
    # never be fed back in as --since.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "first"}),
        _rec(type="system", subtype="compact_boundary", timestamp="2026-01-01T00:00:01Z"),  # no uuid
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:02Z",
             message={"role": "user", "content": "after the boundary"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    full = claude_code.parse(fp, str(fp), ExtractConfig())
    boundary = next(e for e in full if e.kind is EventKind.LIFECYCLE_MARKER)
    assert boundary.marker == "line1"

    resumed = claude_code.parse(fp, str(fp), ExtractConfig(since_marker="line1"))
    assert [e.text for e in resumed] == ["after the boundary"]


def test_chained_since_on_uuid_less_records_never_reindexes_from_zero(tmp_path):
    # Adversarial-review finding: marker resolution (before slicing) and
    # marker emission (after slicing) each independently re-enumerated
    # `records`, so a marker minted from an already-sliced parse used a
    # DIFFERENT fallback index space than a fresh, unsliced parse would
    # resolve it against. A second --since hop, chained off a first hop's
    # own output marker, would then resolve to the wrong record and
    # silently re-emit content already flushed to a prior snapshot -- the
    # exact "tears the cache" failure this tool's whole design forbids.
    # None of these four records carry a uuid, forcing every marker through
    # the f"line{i}" fallback.
    records = [
        _rec(type="user", timestamp="2026-01-01T00:00:00Z", message={"role": "user", "content": "msg0"}),
        _rec(type="user", timestamp="2026-01-01T00:00:01Z", message={"role": "user", "content": "msg1"}),
        _rec(type="user", timestamp="2026-01-01T00:00:02Z", message={"role": "user", "content": "msg2"}),
        _rec(type="user", timestamp="2026-01-01T00:00:03Z", message={"role": "user", "content": "msg3"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    hop1 = claude_code.parse(fp, str(fp), ExtractConfig())
    assert [e.marker for e in hop1] == ["line0", "line1", "line2", "line3"]

    # First hop: resume after msg1 -- the two survivors must keep their
    # TRUE absolute-position markers (line2/line3), not re-based-from-zero
    # ones (line0/line1), or this hop's own output would already collide
    # with markers used as --since input.
    hop2 = claude_code.parse(fp, str(fp), ExtractConfig(since_marker="line1"))
    assert [(e.text, e.marker) for e in hop2] == [("msg2", "line2"), ("msg3", "line3")]

    # Second hop, chained off hop2's own last marker: everything was
    # already emitted, so nothing should come back. Under the bug this
    # resolved "line3" against the wrong record and re-emitted msg3 (or
    # worse) a second time.
    hop3 = claude_code.parse(fp, str(fp), ExtractConfig(since_marker=hop2[-1].marker))
    assert hop3 == []


def test_task_notification_with_attributes_and_body_is_fully_stripped(tmp_path):
    # Adversarial-review finding: the harness-tag regex only fully
    # consumed a tag's body when its opening tag had zero attributes; an
    # attributed opening tag (e.g. a real id="...") matched only the
    # opening-tag fallback branch, leaking the body and the stray closing
    # tag into what became OPERATOR_TEXT.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": (
                 '<task-notification id="55">should not survive</task-notification> keep this'
             )}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert len(events) == 1
    assert events[0].text == "keep this"


def test_task_notification_is_dropped_as_noise(tmp_path):
    # Found by comparing this tool's output against the operator's own
    # hand-curated excerpt of a real session: a background-agent completion
    # push arrives as a genuine "user"-type, plain-string-content record
    # (same shape as real operator text -- no isMeta flag) but is
    # controller-injected tool output, not something the operator said, and
    # the operator's own curation never kept these raw blocks.
    notification_text = (
        "<task-notification>\n<task-id>abc123</task-id>\n<status>completed</status>\n"
        "<summary>Agent finished</summary>\n<result>some long nested review report "
        + ("filler " * 100) + "</result>\n</task-notification>"
    )
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": notification_text}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [{"type": "text", "text": "Real bug confirmed, fixing it."}]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    events = claude_code.parse(fp, str(fp), ExtractConfig())
    assert not any(e.marker == "u1" for e in events)
    assert any(e.marker == "a1" for e in events)
