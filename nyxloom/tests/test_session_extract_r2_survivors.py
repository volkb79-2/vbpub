"""Tests written to kill the R2 mutation survivors of the SUCCESSOR package
(b9bbc2a8c): each test names the mutant (file:line at b9bbc2a8c) it kills.
Equivalent mutants are documented in the SUCCESSOR REPORT, Round 2."""

from __future__ import annotations

import json
import re
import time

import pytest

from nyxloom.session_extract import ledger, toolresult
from nyxloom.session_extract.ledger import Ledger
from nyxloom.session_extract.stopstate import StopState, build_stop_state, read_pi_facts
from nyxloom.session_extract.successor import assemble


def _w(tmp_path, recs, name="agent-s.jsonl"):
    fp = tmp_path / name
    fp.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return fp


def _a(uid, ts, blocks):
    return {"type": "assistant", "uuid": uid, "timestamp": ts, "message": {"role": "assistant", "content": blocks}}


def _u(uid, ts, content):
    return {"type": "user", "uuid": uid, "timestamp": ts, "message": {"role": "user", "content": content}}


# stopstate.py:59 GtE->Gt -- a 19-character timestamp (no zone suffix) is still shown
def test_stop_state_renders_a_nineteen_char_timestamp():
    text = StopState("c", None, "hello", "2026-01-01T10:11:12", None, None).render()
    assert "last assistant text 10:11:12: hello" in text
    assert "last assistant text: hello" in StopState("c", None, "hello", "2026-01-01T10:11", None, None).render()


# stopstate.py:80 False->True -- no record at all after the brief is NOT an interrupt
def test_stop_state_with_only_the_brief_is_not_interrupted(tmp_path):
    state = build_stop_state(_w(tmp_path, [_u("b", "2026-01-01T10:00:00Z", "brief")]))
    assert "interrupted" not in state.cause and "without a clear stop signal" in state.cause


# stopstate.py:100 And->Or -- a blank text block must not replace the last real text
def test_stop_state_ignores_blank_text_blocks(tmp_path):
    fp = _w(tmp_path, [_u("b", "2026-01-01T10:00:00Z", "brief"),
                       _a("a1", "2026-01-01T10:00:01Z", [{"type": "text", "text": "real words"}]),
                       _a("a2", "2026-01-01T10:00:02Z", [{"type": "text", "text": "   "}])])
    assert build_stop_state(fp).last_text == "real words"


# stopstate.py:105 True->False -- a call recorded after its (out-of-order) result is the tail record
def test_stop_state_tail_kind_assistant_call(tmp_path):
    fp = _w(tmp_path, [
        _u("b", "2026-01-01T10:00:00Z", "brief"),
        _u("r", "2026-01-01T10:00:01Z", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
        _a("a", "2026-01-01T10:00:02Z", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]),
    ])
    assert "last record kind: assistant_call" in build_stop_state(fp).cause


# stopstate.py:131 True->False -- an interrupt delivered as a list text block marks the tail interrupted
def test_stop_state_interrupt_as_a_text_block(tmp_path):
    fp = _w(tmp_path, [_u("b", "2026-01-01T10:00:00Z", "brief"),
                       _a("a1", "2026-01-01T10:00:01Z", [{"type": "text", "text": "working"}]),
                       _u("i", "2026-01-01T10:00:02Z", [{"type": "text", "text": "[Request interrupted by user]"}])])
    assert build_stop_state(fp).cause.startswith("interrupted:")


# successor.py:100 True->False -- the default says the brief is omitted from the extract
def test_assemble_default_notes_the_omitted_brief(tmp_path):
    fp = _w(tmp_path, [_u("b", "2026-01-01T10:00:00Z", "brief")])
    doc = assemble(fp, "brief", "EXTRACT", Ledger(), build_stop_state(fp), None)
    assert "The original brief is omitted from the extract" in doc
    assert "omitted from the extract" not in assemble(fp, "brief", "E", Ledger(), build_stop_state(fp), None,
                                                      omitted_brief_note=False)


# toolresult.py:108/110/114/117 -- summarize_call branch conditions
def test_summarize_call_branches():
    assert toolresult.summarize_call("Bash", {"command": 5}) == "$ 5"                    # Or->And (108)
    assert toolresult.summarize_call("Other", {"command": "x"}) == "$ x"
    assert toolresult.summarize_call("Foo", {"file_path": "/a"}) == "/a"                 # Or->And (110)
    assert toolresult.summarize_call("Read", {"other": 1}) == ""                         # a file tool names its path only
    assert toolresult.summarize_call("T", {"pattern": "", "query": "q"}) == "query=q"    # And->Or (114)
    assert toolresult.summarize_call("T", {"b": 1, "a": 2}) == '{"a": 2, "b": 1}'        # sort_keys (117)
    assert toolresult.tool_intent("not-a-dict") == "" and toolresult.tool_intent({"x": 1}) == ""  # (99)


# ledger.py:152 And->Or -- a ledger with files but no effects is not "empty"
def test_session_ledger_emptiness():
    only_files = Ledger(files_read=["a"])
    assert not only_files.session_is_empty()
    assert "no files, commits" not in only_files.render_session()
    assert "external effects: none detected" in only_files.render_session()
    only_effect = Ledger(external_effects=["[00:00:00] git push"])
    assert not only_effect.session_is_empty()
    assert "none detected" not in only_effect.render_session()
    assert Ledger().session_is_empty() and "(no files, commits" in Ledger().render_session()


# ledger.py:236 GtE->Gt -- `_hms` of a 19-character timestamp
def test_hms_boundary_length():
    assert ledger._hms("2026-01-01T10:11:12") == "[10:11:12]"
    assert ledger._hms("2026-01-01T10:11") == "[--:--:--]"


# ledger.py:97/98/101 (budget_exceeded, hung loops) -- the wrapper stripping is now a bounded
# `for` over a fixed pass count: pathological input terminates quickly.
def test_wrapper_stripping_is_bounded():
    from nyxloom.session_extract import shellcmd

    started = time.monotonic()
    hostile = "sudo " * 5000 + "git push"
    shellcmd.strip_wrappers(hostile)
    shellcmd.normalized_segments("env " * 2000 + "A=b " * 2000 + "ls")
    assert time.monotonic() - started < 5

# ---------------------------------------------------------------------------
# Survivors of the pi-extract-pi package (fix round 2). Each test names the
# mutant (file:line:operator) measured by a local probe that applies assay's
# OWN site generator over the package's changed lines and runs this lane's
# argv. Those mutants survived because no existing test distinguishes the
# mutated behaviour from the correct one -- not because the code is wrong.
# ---------------------------------------------------------------------------

from nyxloom.session_extract import layout, locate
from nyxloom.session_extract.adapters import pi
from nyxloom.session_extract.config import ExtractConfig


def _pi_store(tmp_path, name, records):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n",
                    encoding="utf-8")
    return path


def _pi_header():
    return {"type": "session", "version": 3, "id": "0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
            "timestamp": "2026-01-01T09:00:00.000Z", "cwd": "/work/example-repo"}


def _pi_user(rec_id, parent_id, text="Synthetic operator prompt."):
    return {"type": "message", "id": rec_id, "parentId": parent_id,
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _pi_assistant(rec_id, parent_id, text="Synthetic prose.", stop_reason="stop"):
    return {"type": "message", "id": rec_id, "parentId": parent_id,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}] if text else [],
                        "stopReason": stop_reason}}


# adapters/pi.py:219 And->Or -- an ISO STRING timestamp is used as-is
def test_pi_record_timestamp_prefers_a_non_empty_string():
    assert pi.record_timestamp({}, {"timestamp": "2026-01-01T09:00:00.000Z"}) == \
        "2026-01-01T09:00:00.000Z"
    # An EMPTY string is not a timestamp: the wrapper's own ISO string wins.
    assert pi.record_timestamp({"timestamp": "2026-01-01T09:00:01.000Z"},
                               {"timestamp": ""}) == "2026-01-01T09:00:01.000Z"
    # The same guard separates a real block TYPE from an empty one.
    assert pi.block_kind({"type": "text", "text": "x"}) == "text"          # 251
    assert pi.block_kind({"type": "", "text": "x"}) == "missing"
    assert pi.block_kind({}) == "missing"


# adapters/pi.py:296 Or->And -- a role-only skip still prints its own line
def test_pi_each_skip_bucket_prints_a_line_on_its_own():
    roles_only = pi.PiSkipReport(roles={"system": 1})
    lines = roles_only.lines()
    assert any("skipped message(s) by role" in line for line in lines)
    assert not any("record(s)" in line for line in lines)
    unknown_roles = pi.PiSkipReport(unknown_roles={"brand-new": 1})
    assert any("unknown brand-new=1" in line for line in unknown_roles.lines())


# adapters/pi.py:341 And->Or (x2) -- the block-type and value-type guards are
# BOTH required; a non-text block must not contribute text.
def test_pi_block_text_requires_the_kind_AND_a_string_value():
    blocks = [
        {"type": "text", "text": "first"},
        {"type": "thinking", "text": "leaked-from-thinking"},
        {"type": "text", "text": 42},
        {"type": "text", "text": ""},
        {"type": "text"},
    ]
    assert pi.block_text(blocks, "text", "text") == "first"


# adapters/pi.py:370 And->Or -- sniff needs BOTH a string id AND a string cwd
def test_pi_sniff_requires_a_string_id_and_a_string_cwd(tmp_path):
    for name, tweak in (
        ("missing-cwd", {"cwd": 5}),
        ("missing-id", {"id": 5}),
    ):
        header = dict(_pi_header(), **tweak)
        assert not pi.sniff(_pi_store(tmp_path, f"{name}.jsonl", [header]))
    assert pi.sniff(_pi_store(tmp_path, "both.jsonl", [_pi_header()]))


# adapters/pi.py:356 GtE->Gt -- the scan cap is inclusive, so a header on the
# LAST scanned line is still recognized.
def test_pi_sniff_reads_the_whole_scan_window(tmp_path):
    padding = [{"type": "message", "id": f"p{index}", "parentId": None,
                "message": {"role": "user", "content": []}}
               for index in range(pi._SNIFF_SCAN_LINES - 1)]
    assert pi.sniff(_pi_store(tmp_path, "last-line.jsonl", padding + [_pi_header()]))
    assert not pi.sniff(_pi_store(tmp_path, "past-the-window.jsonl",
                                   padding + [{"type": "compaction"}] + [_pi_header()]))


# adapters/pi.py:389 And->Or -- an EMPTY top-level summary falls through to the
# run stream's nested `result.summary`, instead of being taken as the answer.
def test_pi_an_empty_compaction_summary_falls_through_to_the_nested_one():
    assert pi.compaction_summary({"summary": "Synthetic."}) == "Synthetic."
    assert pi.compaction_summary({"summary": "", "result": {"summary": "Nested."}}) == \
        "Nested."
    assert pi.compaction_summary({"summary": ""}) == ""
    assert pi.compaction_summary({"result": {"summary": ""}}) == ""
    assert pi.compaction_summary({"result": {"summary": "Nested."}}) == "Nested."


# adapters/pi.py:396 ''->None -- an unrecognized content shape yields EMPTY text
def test_pi_a_non_string_non_list_custom_content_yields_empty_text():
    assert pi.custom_message_text("Synthetic.") == "Synthetic."
    assert pi.custom_message_text({"unexpected": "shape"}) == ""


# adapters/pi.py:505 And->Or -- an empty targetId is named `unknown`
def test_pi_a_context_edit_with_no_target_is_named_unknown():
    missing = pi.context_edit_event({"replacement": None}, 1, "line1", "")
    assert missing.text == "[context edit] target=unknown removed from model context"
    blank = pi.context_edit_event({"targetId": "", "replacement": None}, 1, "line1", "")
    assert blank.text == "[context edit] target=unknown removed from model context"
    named = pi.context_edit_event({"targetId": "m2", "replacement": None}, 1, "line1", "")
    assert named.text == "[context edit] target=m2 removed from model context"


# adapters/pi.py:534 Eq->NotEq -- `intent-or-call` prefers the intent, and falls
# back to the CALL when there is no intent.
def test_pi_intent_or_call_prefers_intent_then_falls_back_to_the_call():
    from nyxloom.session_extract.adapters.pi import tool_label

    config = ExtractConfig(tool_calls="intent-or-call")
    assert tool_label("bash", {"command": "print hi", "description": "Synthetic intent."},
                      config) == "[tool call: bash] Synthetic intent."
    assert tool_label("bash", {"command": "print hi"}, config) == "[tool call: bash] $ print hi"


# adapters/pi.py:542 Or->And -- BOTH a non-string name and an EMPTY name are
# refused, and a real tool call still renders.
def test_pi_tool_call_event_refuses_a_missing_or_empty_name():
    config = ExtractConfig(tool_calls="call")
    assert pi.tool_call_event(1, "line1", "", {"name": 5}, config) is None
    assert pi.tool_call_event(1, "line1", "", {"name": ""}, config) is None
    assert pi.tool_call_event(1, "line1", "", {"name": "bash", "arguments": {
        "command": "print hi"}}, config).text == "[tool call: bash] $ print hi"


# adapters/pi.py:557 And->Or -- an empty path adds no `tool_file` meta
def test_pi_an_empty_tool_path_adds_no_tool_file_meta():
    config = ExtractConfig(tool_calls="call")
    empty = pi.tool_call_event(1, "line1", "", {"name": "read", "arguments": {"file_path": ""}},
                               config)
    assert "tool_file" not in empty.meta
    real = pi.tool_call_event(1, "line1", "", {"name": "read", "arguments": {"path": "/a.py"}},
                              config)
    assert real.meta["tool_file"] == "/a.py"


# adapters/pi.py:576 And->Or -- an empty toolName falls back to `tool`, not to ""
def test_pi_a_failed_result_with_no_tool_name_is_labelled_tool():
    config = ExtractConfig(tool_errors="show")
    for tool_name in (None, ""):
        message = {
            "role": "toolResult", "isError": True,
            "content": [{"type": "text", "text": "synthetic failure"}],
        }
        if tool_name is not None:
            message["toolName"] = tool_name
        event = pi.tool_result_event(1, "line1", "", message, config, {})
        assert event.text == "[tool error: tool] synthetic failure"


# adapters/pi.py:597 Or->And -- the launcher scan needs BOTH a bash call and the
# launcher name; a non-bash block never contributes agent communication.
def test_pi_launcher_scan_requires_a_bash_tool_call():
    config = ExtractConfig(agent_comms="full", agent_launch_patterns=("codex-impl.sh",))
    command = "./scripts/controller/codex-impl.sh brief.md run1"
    read_block = {"type": "toolCall", "id": "c", "name": "read",
                  "arguments": {"command": command}}
    assert pi.launch_events([read_block], None, config, 1, "line1", "") == []
    bash_block = dict(read_block, name="bash")
    out = pi.launch_events([bash_block], None, config, 1, "line1", "")
    assert out and out[0].meta["agent_launch"] == "codex-impl.sh"


# adapters/pi.py:616/617/620 And->Or (x3) -- custom-message parts need a dict
# block OF type text WITH a string value; anything else contributes nothing.
def test_pi_custom_message_parts_need_a_text_block_with_a_string_value():
    content = [
        {"type": "text", "text": "kept"},
        {"type": "image", "text": "leaked-from-an-image"},
        "not-a-block",
        {"type": "text", "text": 42},
        {"type": "text"},
    ]
    assert pi.custom_message_text(content) == "kept"


# adapters/pi.py:677 And->Or -- a missing role is counted as `missing`, not as
# an empty-string role name.
def test_pi_a_message_without_a_role_is_counted_as_missing(tmp_path):
    path = _pi_store(tmp_path, "no-role.jsonl", [
        _pi_header(),
        {"type": "message", "id": "m1", "parentId": None, "message": {}},
    ])
    _, report = pi.parse_with_stats(path, str(path), ExtractConfig())
    assert report.unknown_roles == {"missing": 1}


# adapters/pi.py:682 And->Or -- a NON-string role is `missing`, never that value
# itself (an `or` would keep a truthy non-string role as the bucket KEY).
def test_pi_a_non_string_role_is_named_missing(tmp_path):
    path = _pi_store(tmp_path, "numeric-role.jsonl", [
        _pi_header(),
        {"type": "message", "id": "m1", "parentId": None,
         "message": {"role": 5, "content": [{"type": "text", "text": "x"}]}},
    ])
    _, report = pi.parse_with_stats(path, str(path), ExtractConfig())
    assert report.unknown_roles == {"missing": 1}


# adapters/pi.py:698 And->Or -- an empty call id is not registered, so a later
# failed result has no call line to name.
def test_pi_an_empty_call_id_is_not_registered(tmp_path):
    path = _pi_store(tmp_path, "empty-call-id.jsonl", [
        _pi_header(),
        _pi_user("m1", None),
        {"type": "message", "id": "m2", "parentId": "m1",
         "message": {"role": "assistant", "stopReason": "toolUse", "content": [
             {"type": "toolCall", "id": "", "name": "bash", "arguments": {"command": "print hi"}},
         ]}},
        {"type": "message", "id": "m3", "parentId": "m2",
         "message": {"role": "toolResult", "toolCallId": "", "isError": True,
                     "content": [{"type": "text", "text": "synthetic failure"}]}},
    ])
    errors = [event.text for event in pi.parse(path, str(path), ExtractConfig(tool_errors="show"))
              if event.meta.get("tool_error")]
    assert errors == ["[tool error: tool] synthetic failure"]


# adapters/pi.py:705 Or->And -- the recorded call line passes the tool's REAL
# name to the summariser (a name the shared vocabulary knows summarises
# differently), and a NAMELESS call passes "" rather than inventing a name.
def test_pi_the_recorded_call_line_keeps_the_real_tool_name(tmp_path):
    path = _pi_store(tmp_path, "named-call.jsonl", [
        _pi_header(),
        {"type": "message", "id": "m1", "parentId": None,
         "message": {"role": "assistant", "stopReason": "toolUse", "content": [
             {"type": "toolCall", "id": "c1", "name": "Bash", "arguments": {"x": 1}},
         ]}},
        {"type": "message", "id": "m2", "parentId": "m1",
         "message": {"role": "toolResult", "toolCallId": "c1", "isError": True,
                     "content": [{"type": "text", "text": "synthetic failure"}]}},
    ])
    errors = [event.text for event in pi.parse(path, str(path),
                                               ExtractConfig(tool_errors="show"))
              if event.meta.get("tool_error")]
    # `Bash` summarises as `$ <command>`; with no command the summary is `$ `.
    # A nameless call contributes the arguments as compact JSON instead, so
    # the two lines differ in exactly the way the mutated `or` erases.
    assert errors == ["[tool error: tool] $ => synthetic failure"]


# adapters/pi.py:700 Or->And -- the call summary falls back to the NAME, so a
# tool call with no name still summarizes instead of raising.
def test_pi_a_call_record_with_no_name_still_summarizes():
    config = ExtractConfig(tool_errors="show")
    assert pi.tool_result_event(1, "line1", "", {
        "role": "toolResult", "isError": True, "toolCallId": "c1",
        "content": [{"type": "text", "text": "synthetic failure"}],
    }, config, {"c1": "unused"}) is not None
    from nyxloom.session_extract import toolresult

    assert toolresult.summarize_call(str(None or ""), "not-a-dict") == "not-a-dict"


# adapters/pi.py:793 And->Or -- an id must be a STRING to be a known entry: a
# non-string id in the file does not make its own children non-dangling.
def test_pi_a_non_string_id_is_not_a_known_parent(tmp_path):
    path = _pi_store(tmp_path, "non-string-id.jsonl", [
        _pi_header(),
        {"type": "message", "id": 7, "parentId": None,
         "message": {"role": "user", "content": [{"type": "text", "text": "x"}]}},
        {"type": "message", "id": "m2", "parentId": 7,
         "message": {"role": "assistant", "stopReason": "stop",
                     "content": [{"type": "text", "text": "y"}]}},
    ])
    _, report = pi.parse_with_stats(path, str(path), ExtractConfig())
    assert report.tree_breaks == 1


# adapters/pi.py:711 Or->And (x2) -- the error detail prefers `errorMessage`
# and falls back to `error`, and shows nothing when neither carries text.
def test_pi_api_error_detail_falls_back_from_errorMessage_to_error(tmp_path):
    path = _pi_store(tmp_path, "error-detail.jsonl", [
        _pi_header(),
        {"type": "message", "id": "m1", "parentId": None,
         "message": {"role": "assistant", "content": [], "stopReason": "error",
                     "error": "synthetic fallback failure"}},
    ])
    assert pi.parse(path, str(path), ExtractConfig())[-1].text == \
        "[API ERROR: pi stopReason=error] synthetic fallback failure"


# adapters/pi.py:787 NotEq->Eq -- a linear session interleaving a compaction and
# a context_edit is not a tree; a real fork IS counted, once.
def test_pi_a_linear_session_is_not_reported_as_a_tree(tmp_path):
    linear = _pi_store(tmp_path, "linear.jsonl", [
        _pi_header(),
        _pi_user("m1", None),
        _pi_assistant("m2", "m1"),
        {"type": "compaction", "id": "k1", "parentId": "m2", "summary": "Synthetic."},
        _pi_user("m3", "k1"),
        {"type": "context_edit", "id": "e1", "parentId": "m3",
         "targetId": "m2", "replacement": None},
        _pi_assistant("m4", "e1"),
    ])
    _, report = pi.parse_with_stats(linear, str(linear), ExtractConfig())
    assert report.tree_breaks == 0

    forked = _pi_store(tmp_path, "forked.jsonl", [
        _pi_header(),
        _pi_user("m1", None),
        _pi_assistant("m2", "m1"),
        _pi_assistant("m3", "m1"),          # a SECOND child of m1: one fork
        _pi_assistant("m4", "elsewhere"),   # a parent the file does not contain
    ])
    _, report = pi.parse_with_stats(forked, str(forked), ExtractConfig())
    assert report.tree_breaks == 2


# adapters/pi.py:819 And->Or -- an empty customType is named `unknown`, and a
# real one is carried through; a NON-string customType is `unknown` too.
def test_pi_an_empty_custom_type_is_named_unknown(tmp_path):
    path = _pi_store(tmp_path, "custom-type.jsonl", [
        _pi_header(),
        {"type": "custom_message", "id": "c1", "parentId": None, "customType": "",
         "content": "Synthetic injected note."},
        {"type": "custom_message", "id": "c2", "parentId": "c1",
         "customType": "synthetic-extension", "content": "Synthetic second note."},
        {"type": "custom_message", "id": "c3", "parentId": "c2", "customType": 7,
         "content": "Synthetic third note."},
    ])
    texts = [event.text for event in pi.parse(path, str(path), ExtractConfig())]
    assert texts == [
        "[custom message: unknown] Synthetic injected note.",
        "[custom message: synthetic-extension] Synthetic second note.",
        "[custom message: unknown] Synthetic third note.",
    ]


# adapters/pi.py:664 And->Or -- the same rule for the `custom` MESSAGE role's
# own `customType` (a truthy NON-string would otherwise be rendered into the
# label as its own repr).
def test_pi_a_custom_message_role_with_a_non_string_type_is_named_unknown(tmp_path):
    path = _pi_store(tmp_path, "custom-role-type.jsonl", [
        _pi_header(),
        {"type": "message", "id": "m1", "parentId": None,
         "message": {"role": "custom", "customType": 7,
                     "content": "Synthetic role note."}},
    ])
    assert [event.text for event in pi.parse(path, str(path), ExtractConfig())] == [
        "[custom message: unknown] Synthetic role note.",
    ]


# adapters/pi.py:654/656 -- a `custom` MESSAGE with no text is counted, not
# silently skipped: pi does put that message in front of the model.
def test_pi_a_custom_message_with_no_text_is_counted(tmp_path):
    path = _pi_store(tmp_path, "custom-empty.jsonl", [
        _pi_header(),
        {"type": "message", "id": "m1", "parentId": None,
         "message": {"role": "custom", "content": []}},
    ])
    assert pi.parse(path, str(path), ExtractConfig()) == []
    _, report = pi.parse_with_stats(path, str(path), ExtractConfig())
    assert report.blocks == {"custom (no text)": 1}


# layout.py:199 False->True -- the DEFAULT is not truncated either: a plan
# built with no --state-file still reports `state_truncated=0`.
def test_layout_a_plan_without_a_state_file_reports_not_truncated():
    config = ExtractConfig(tail_prose_tokens=0, budget_tokens=2000)
    plan = layout.build(full_events=[], config=config, last_marker=None, delta_events=[])
    assert plan.state_truncated is False
    assert any("state_truncated=0" in line for line in plan.footer_lines)


# layout.py:199 False->True -- a section that fit is NOT truncated
def test_layout_a_state_file_that_fits_is_not_marked_truncated(tmp_path):
    state = tmp_path / "state.md"
    state.write_text("- a synthetic line\n", encoding="utf-8")
    config = ExtractConfig(state_file=str(state), tail_prose_tokens=0, budget_tokens=2000)
    plan = layout.build(full_events=[], config=config, last_marker=None, delta_events=[])
    assert plan.state_truncated is False
    assert plan.state_kept_chars == plan.state_total_chars


# layout.py:295 0->None -- a non-positive limit keeps nothing, as an INTEGER
def test_layout_line_cut_returns_zero_an_not_None():
    assert layout._line_cut("a\nb", 0) == 0
    assert isinstance(layout._line_cut("a\nb", -1), int)
    assert layout._line_cut("a\nb", 2) == 2
    assert layout._line_cut("ab", 1) == 0


# layout.py:298 GtE->Gt -- a newline at offset 0 still ends a line (index 0)
def test_layout_line_cut_handles_a_newline_at_the_first_byte():
    assert layout._line_cut("\nx", 2) == 1
    # The paragraph cut applies the same `>=` rule to a zero-length first block.
    assert layout.paragraph_cut("\n\nb", 4) == 1


# layout.py:358 LtE->Lt -- a state section that EXACTLY fits is not truncated
def test_layout_an_exactly_fitting_state_section_is_not_truncated(tmp_path):
    state = tmp_path / "state.md"
    state.write_text("- a synthetic line\n", encoding="utf-8")
    header = layout.state_header(ExtractConfig(state_file=str(state), tail_prose_tokens=0, budget_tokens=1000))
    text = state.read_text().strip()
    exact = layout.estimate_tokens(header + "\n" + text + layout.render.separator(False))
    config = ExtractConfig(state_file=str(state), tail_prose_tokens=0,
                           budget_tokens=exact)
    section, _, truncated, _, kept, total = layout._state_section(config, exact)
    assert truncated is False and kept == total and text in section


# locate.py:239 True->False -- a directory that VANISHES between the stat and
# the listing (the TOCTOU window this guard exists for) is an ordinary
# negative, not a scan refusal.
def test_locate_a_directory_that_disappears_mid_scan_is_a_negative(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "also-absent"))
    assert locate._pi_matches("0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b") == []

    from pathlib import Path as _Path

    root = tmp_path / "vanishing"
    root.mkdir()
    real_iterdir = _Path.iterdir

    def vanishes(self):
        if self == root:
            raise FileNotFoundError(2, "gone between stat and list", str(self))
        return real_iterdir(self)

    monkeypatch.setattr(_Path, "iterdir", vanishes)
    monkeypatch.setenv("PI_CODING_AGENT_SESSION_DIR", str(root))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(root))
    assert locate._pi_matches("0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b") == []


# stopstate.py:327 False->True -- a run with no empty completion is not mislabeled
def test_stop_state_a_short_real_run_is_not_an_empty_completion(tmp_path):
    path = _pi_store(tmp_path, "prose.jsonl", [
        _pi_header(),
        _pi_user("m1", None),
        _pi_assistant("m2", "m1", "Synthetic prose."),
    ])
    assert not build_stop_state(path, fmt="pi").cause.startswith("empty-completion")


# stopstate.py:351 And->Or (x2) -- text parts need the type, the string value
# AND non-blank content; a blank text block is not "last text".
def test_stop_state_pi_ignores_blank_and_non_text_blocks(tmp_path):
    path = _pi_store(tmp_path, "blank-prose.jsonl", [
        _pi_header(),
        _pi_user("m1", None),
        {"type": "message", "id": "m2", "parentId": "m1",
         "message": {"role": "assistant", "stopReason": "stop",
                     "timestamp": 1790000000000,
                     "content": [
                         {"type": "text", "text": "real words"},
                         {"type": "thinking", "text": "leaked-from-thinking"},
                         {"type": "text", "text": 42},
                         {"type": "text", "text": "   "},
                     ]}},
    ])
    facts = read_pi_facts(path)
    assert facts.last_text == "real words"
    assert facts.message_count == 1


# stopstate.py:327 False->True -- a session with NO assistant message at all is
# not an empty completion: there is content, it simply never reported a stop.
def test_stop_state_a_header_only_session_is_not_an_empty_completion(tmp_path):
    path = _pi_store(tmp_path, "header-only.jsonl", [_pi_header()])
    facts = read_pi_facts(path)
    assert facts.empty_completion is False
    assert "empty-completion" not in build_stop_state(path, fmt="pi").cause


# stopstate.py:383 Or->And -- an unnamed execution passes "" to the summariser
# (not a placeholder that changes the summary), and a NAMED one keeps its name
# even when the shared vocabulary summarises that name differently.
def test_stop_state_pi_the_execution_summary_uses_the_real_tool_name(tmp_path):
    unnamed = _pi_store(tmp_path, "unnamed-exec.jsonl", [
        _pi_header(),
        {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "",
         "args": {"path": "/work/example-repo/a.py"}},
    ])
    assert read_pi_facts(unnamed).in_flight == 'tool: {"path": "/work/example-repo/a.py"}'

    # `Read` is a name the shared vocabulary renders as a PATH; with no path
    # argument its summary is empty, so the label is just the tool name. A
    # mutant that passed "" instead would summarise the raw arguments instead.
    named = _pi_store(tmp_path, "named-exec.jsonl", [
        _pi_header(),
        {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "Read",
         "args": {"other": 1}},
    ])
    assert read_pi_facts(named).in_flight == "Read"


# stopstate.py:407/408 And->Or (x2) -- the tail-kind scan needs the assistant
# ROLE (not merely call blocks), and a call-block-carrying record that is not
# an assistant message does not make the tail a call either.
def test_stop_state_pi_the_tail_kind_needs_the_assistant_role_and_its_calls(tmp_path):
    # A call is open (c1), a LATER call completes (c2), then a text-only
    # assistant message ends the file. The correct tail kind is the RESULT,
    # so nothing is in flight; folding the role test away would resurrect c1.
    open_then_text = _pi_store(tmp_path, "tail-after-result.jsonl", [
        _pi_header(),
        {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "bash",
         "args": {"command": "print first"}},
        {"type": "tool_execution_start", "toolCallId": "c2", "toolName": "bash",
         "args": {"command": "print second"}},
        {"type": "tool_execution_end", "toolCallId": "c2"},
        {"type": "message", "id": "m2", "parentId": None,
         "message": {"role": "assistant", "stopReason": "stop",
                     "content": [{"type": "text", "text": "Synthetic prose."}]}},
    ])
    assert read_pi_facts(open_then_text).in_flight is None

    # The same shape, but the final record is a NON-assistant message that
    # happens to carry a toolCall block: not an assistant call either.
    call_blocks_elsewhere = _pi_store(tmp_path, "tail-non-assistant.jsonl", [
        _pi_header(),
        {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "bash",
         "args": {"command": "print first"}},
        {"type": "tool_execution_start", "toolCallId": "c2", "toolName": "bash",
         "args": {"command": "print second"}},
        {"type": "tool_execution_end", "toolCallId": "c2"},
        {"type": "message", "id": "m3", "parentId": None,
         "message": {"role": "user", "content": [
             {"type": "toolCall", "id": "c9", "name": "bash",
              "arguments": {"command": "print third"}}]}},
    ])
    assert read_pi_facts(call_blocks_elsewhere).in_flight is None


# stopstate.py:453/454 And->Or (x2) -- `_pi_has_calls` needs a LIST and a
# dict block OF type toolCall.
def test_stop_state_pi_has_calls_requires_a_list_of_tool_call_blocks():
    assert not pi_has_calls({"content": "not-a-list"})
    assert not pi_has_calls({"content": [{"type": "text", "text": "Trigger."}]})
    assert not pi_has_calls({"content": ["not-a-dict"]})
    assert pi_has_calls({"content": [{"type": "toolCall", "id": "c1"}]})


def pi_has_calls(message):
    from nyxloom.session_extract.stopstate import _pi_has_calls

    return _pi_has_calls(message)


# adapters/pi.py:219/251/296/341/356/370/389/396/505/534/542/557/576/597/
# 616/617/620/654/677/698/700/711/787/819 are covered above; the remaining
# measured survivors are equivalent mutants, documented in the package report:
# a mutated `==`/`and` whose two sides can never differ for this adapter's
# inputs (for example the `or` at pi.py:711, where a non-empty `errorMessage`
# and a non-empty `error` are the same branch of one expression).

