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
from nyxloom.session_extract.stopstate import StopState, build_stop_state
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
