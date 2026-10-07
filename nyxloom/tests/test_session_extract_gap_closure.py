"""SUCCESSOR-2 round 3: branch-coverage closure for the session_extract modules
this package added or changed (harness, shellcmd, successor, watch, render,
config, claude_code tool-call labels and lineage helpers), plus the compact
interview rendering of `--preset watch` (controller ruling 2026-10-06)."""

from __future__ import annotations

import json

import pytest

from nyxloom.session_extract import harness, shellcmd, successor, toolresult, watch
from nyxloom.session_extract.adapters import claude_code
from nyxloom.session_extract.config import ExtractConfig
from nyxloom.session_extract.events import EventKind, NormalizedEvent
from nyxloom.session_extract.render import _gap_inline_text

T0 = "2026-01-01T10:00:00Z"


def _ev(kind, text, ts=T0):
    return NormalizedEvent(1, "m1", ts, kind, text)


# --- harness / successor / shellcmd -------------------------------------------

def test_transcript_versions_skips_lines_that_mention_version_but_are_not_json(tmp_path):
    fp = tmp_path / "t.jsonl"
    fp.write_text('{"version": broken\n{"version": "2.1.289"}\n{"version": "2.1.289"}\n', encoding="utf-8")
    assert harness.transcript_versions(fp) == ["2.1.289"]


def test_transcript_context_skips_malformed_and_non_object_lines(tmp_path):
    fp = tmp_path / "t.jsonl"
    fp.write_text('not json\n[1, 2]\n{"cwd": "/w", "gitBranch": "b1"}\n{"gitBranch": "b2"}\n', encoding="utf-8")
    assert successor.transcript_context(fp) == {"cwd": "/w", "gitBranch": "b2"}


def test_first_arg_survives_unbalanced_quotes_and_empty_input():
    assert shellcmd._first_arg("'abc") == "abc"
    assert shellcmd._first_arg("   ") == ""


def test_bash_dash_c_with_an_empty_body_yields_no_segments():
    assert shellcmd.normalized_segments("bash -c ''") == []


# --- config validation ----------------------------------------------------------

@pytest.mark.parametrize("field", ["tool_calls", "tool_errors", "edit_calls", "read_calls",
                                   "effect_calls", "timestamps"])
def test_config_rejects_an_unknown_value_for_each_enumerated_field(field):
    with pytest.raises(ValueError, match=field):
        ExtractConfig(**{field: "bogus"})


def test_config_rejects_a_negative_timestamp_gap():
    with pytest.raises(ValueError, match="timestamp_gap_minutes"):
        ExtractConfig(timestamp_gap_minutes=-1)


# --- render / claude_code helpers -------------------------------------------------

def test_gap_inline_text_is_none_for_a_marker_mode_that_is_not_inline():
    assert _gap_inline_text(_ev(EventKind.ASSISTANT_TEXT, "x"), 1, "none", True) is None


def test_tool_call_label_modes_none_and_derived_call():
    assert claude_code._tool_call_label("Bash", {"command": "ls"}, "none") is None
    derived = claude_code._tool_call_label("Bash", {"command": "ls -l"}, "call")
    assert derived == f"[tool call: Bash] {toolresult.summarize_call('Bash', {'command': 'ls -l'})}"
    assert derived != "[tool call: Bash]"


def test_tool_use_ids_ignores_non_tool_use_blocks(tmp_path):
    fp = tmp_path / "agent-x.jsonl"
    rec = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}}
    fp.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    assert claude_code._tool_use_ids(fp) == {"t1"}


def test_list_agents_without_a_root_file_or_meta_files_is_empty(tmp_path):
    sub = tmp_path / "sess" / "subagents"
    sub.mkdir(parents=True)
    fp = sub / "agent-x.jsonl"
    fp.write_text("{}\n", encoding="utf-8")
    assert claude_code.list_agents(fp) == []


# --- watch: colour with no timestamp, interviews ----------------------------------------

def test_watch_colour_without_a_timestamp_keeps_only_the_operator_highlight():
    fmt = watch.WatchFormatter(color=True, timestamps="none")
    out = fmt.format(_ev(EventKind.OPERATOR_TEXT, "go"))
    assert out == f"{watch._BOLD_CYAN}OPERATOR: go{watch._RESET}\n\n"
    assert fmt.format(_ev(EventKind.ASSISTANT_TEXT, "ok")) == "ok\n\n"


def test_watch_ignores_events_that_are_not_prose():
    fmt = watch.WatchFormatter()
    assert fmt.format(_ev(EventKind.TOOL_CALL, "[tool call: Bash]")) is None
    assert fmt.format_all([_ev(EventKind.THINKING, "hm")]) == ""


PROMPT = ("INTERVIEW: Which branch should I merge?\nHeader: Branch\n- main: the default\n"
          "- dev\nMultiple selections are allowed.")
ANSWER = ("INTERVIEW: Which branch should I merge?\nHeader: Branch\n- main\n\n"
          "OPERATOR: main\n\n"
          "INTERVIEW: Release now?\n\nOPERATOR: yes\nbut wait for CI")


def test_watch_interview_prompt_is_one_assistant_line_and_answers_are_operator_lines():
    assert watch.items(_ev(EventKind.QA_PAIR, PROMPT)) == [("assistant", "Which branch should I merge?")]
    assert watch.items(_ev(EventKind.QA_PAIR, ANSWER)) == [("operator", "main"), ("operator", "yes\nbut wait for CI")]
    # an unparseable (raw fallback) answer is still operator content, never dropped
    assert watch.items(_ev(EventKind.QA_PAIR, '"Q"="A"')) == [("operator", '"Q"="A"')]
    assert watch.items(_ev(EventKind.TOOL_CALL, "x")) == []


def test_watch_formats_an_interview_exchange_compactly():
    fmt = watch.WatchFormatter()
    out = fmt.format(_ev(EventKind.QA_PAIR, PROMPT)) + fmt.format(_ev(EventKind.QA_PAIR, ANSWER, "2026-01-01T10:00:09Z"))
    assert out == ("[10:00:00] Which branch should I merge?\n\n"
                   "[10:00:09] OPERATOR: main\n\n"
                   "OPERATOR: yes\nbut wait for CI\n\n")


def test_watch_jsonl_interview_rows_carry_the_version():
    fmt = watch.WatchFormatter(jsonl=True)
    rows = [json.loads(line) for line in fmt.format(_ev(EventKind.QA_PAIR, ANSWER)).splitlines()]
    assert [(r["v"], r["role"], r["text"]) for r in rows] == [(1, "operator", "main"), (1, "operator", "yes\nbut wait for CI")]
    assert watch.JSONL_VERSION == 1


def test_strip_cd_prefix_is_refused_for_a_non_claude_code_format(tmp_path, capsys, monkeypatch):
    from types import SimpleNamespace

    import nyxloom.session_extract as extraction
    from nyxloom.cli_harness import main as harness_main

    source = tmp_path / "source.db"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(extraction, "extract", lambda *args, **kwargs: SimpleNamespace(format="opencode"))
    assert harness_main(["extract", str(source), "--format", "opencode", "--strip-cd-prefix"]) == 1
    assert "--strip-cd-prefix is not supported for 'opencode'" in capsys.readouterr().err


def test_jsonl_and_json_are_refused_together(tmp_path, capsys):
    from nyxloom.cli_harness import main as harness_main

    fp = tmp_path / "agent-x.jsonl"
    fp.write_text("{}\n", encoding="utf-8")
    assert harness_main(["extract", str(fp), "--prose-only", "--jsonl", "--json"]) == 2
    assert "--jsonl and --json are different outputs" in capsys.readouterr().err


def test_watch_applies_an_explicit_block_renderer_to_every_text_row():
    fmt = watch.WatchFormatter(block_render=str.upper, timestamps="none")
    assert fmt.format(_ev(EventKind.ASSISTANT_TEXT, "quiet")) == "QUIET\n\n"


def test_strip_cd_stops_after_its_bounded_number_of_passes():
    command = "cd /a && cd /b && cd /c && cd /d && cd /e && ls"
    assert toolresult.strip_cd(command) == "cd /e && ls"


def test_agent_control_call_without_a_tool_use_id_is_still_listed(tmp_path, capsys):
    from nyxloom.cli_harness import main as harness_main

    base = {"isSidechain": True, "sessionId": "s", "cwd": "/w"}
    records = [
        {**base, "type": "user", "uuid": "u0", "parentUuid": None, "timestamp": T0,
         "message": {"role": "user", "content": "start"}},
        {**base, "type": "assistant", "uuid": "a0", "parentUuid": "u0", "timestamp": "2026-01-01T10:00:05Z",
         "message": {"role": "assistant", "id": "m1", "content": [
             {"type": "tool_use", "name": "TaskStop", "input": {"task_id": "abc"}}]}},
    ]
    fp = tmp_path / "agent-idless.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    assert harness_main(["extract", str(fp), "--preset", "ledger"]) == 0
    assert "TaskStop" in capsys.readouterr().out


def test_follow_in_watch_mode_skips_events_that_are_not_prose(tmp_path):
    import dataclasses
    import io

    from nyxloom.session_extract.config import PROFILES
    from nyxloom.session_extract.follow import Follower, FollowConfig, JsonlSource

    fp = tmp_path / "agent-f00d.jsonl"
    base = {"isSidechain": True, "sessionId": "s", "cwd": "/w"}
    first = {**base, "type": "user", "uuid": "u0", "parentUuid": None, "timestamp": T0,
             "message": {"role": "user", "content": "first"}}
    fp.write_text(json.dumps(first) + "\n", encoding="utf-8")
    config = dataclasses.replace(PROFILES["all"], tool_calls="call")
    out = io.StringIO()
    source = JsonlSource(fp, "claude-code", fp.stat().st_size, config, False, has_primary_thread=False)
    follower = Follower(source, harness="claude-code", session_path=str(fp), config=config,
                        follow_config=FollowConfig(), out=out, lossless_mode=False,
                        printed_any=True, watch=watch.WatchFormatter(jsonl=True))
    call = {**base, "type": "assistant", "uuid": "a0", "parentUuid": "u0", "timestamp": "2026-01-01T10:00:05Z",
            "message": {"role": "assistant", "id": "m1", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}}
    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(call) + "\n")
    follower.tick()
    follower.close()
    assert out.getvalue() == ""


# --- round 3 (SUCCESSOR-2 review fixes) -------------------------------------------

def test_stamp_mode_gap_boundary_is_inclusive():
    """A gap of EXACTLY `gap_minutes` is shown (`>=`); one second less is not."""
    from nyxloom.session_extract.render import _stamp_mode

    prev = "2026-01-01T10:00:00Z"
    exact = _ev(EventKind.ASSISTANT_TEXT, "x", "2026-01-01T10:05:00Z")
    short = _ev(EventKind.ASSISTANT_TEXT, "x", "2026-01-01T10:04:59Z")
    over = _ev(EventKind.ASSISTANT_TEXT, "x", "2026-01-01T10:05:01Z")
    assert _stamp_mode(exact, prev, "gaps", "pre", 5) == "pre"
    assert _stamp_mode(short, prev, "gaps", "pre", 5) == "none"
    assert _stamp_mode(over, prev, "gaps", "pre", 5) == "pre"


_DECLINED = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a "
    "file edit, the new_string was NOT written to the file). To tell you how to proceed, the user said:\n"
    "The user wants to clarify these questions.\n"
    "    This means they may have additional information, context or questions for you.\n"
    "    Start by asking them what they would like to clarify.\n\n"
    "    Questions asked:\n"
    '- "How should CLI-EXT-05 reach them?"\n'
    "  (No answer provided)"
)


def test_watch_clarify_declined_interview_is_one_compact_operator_line():
    assert watch.items(_ev(EventKind.QA_PAIR, _DECLINED)) == [("operator", watch.DECLINED_LABEL)]
    assert watch.DECLINED_LABEL == "[declined; wants to clarify]"
    # whitespace, curly apostrophe and case variations still match structurally
    variant = _DECLINED.replace("doesn't", "doesn’t").replace("\n    ", "\n\t").upper()
    assert watch.is_clarify_declined(variant)
    out = watch.WatchFormatter(timestamps="none").format(_ev(EventKind.QA_PAIR, _DECLINED))
    assert out == "OPERATOR: [declined; wants to clarify]\n\n"


def test_watch_clarify_declined_requires_the_whole_shape():
    # a real answer in the envelope is operator content and stays verbatim
    answered = _DECLINED.replace("(No answer provided)", "Answer: use the shim")
    assert not watch.is_clarify_declined(answered)
    assert watch.items(_ev(EventKind.QA_PAIR, answered)) == [("operator", answered)]
    # missing opener / clarify phrase / unanswered row: not matched
    assert not watch.is_clarify_declined(_DECLINED.replace("doesn't want to proceed", "refused"))
    assert not watch.is_clarify_declined(_DECLINED.replace("wants to clarify", "has questions"))
    assert not watch.is_clarify_declined(_DECLINED.replace("(No answer provided)", ""))
    # ordinary operator text mentioning the phrase mid-text is not the boilerplate
    assert not watch.is_clarify_declined("I said: The user doesn't want to proceed. wants to clarify (No answer provided)")
