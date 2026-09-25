"""Behavioral edges for the extraction surfaces changed in the Q&A work."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nyxloom import cli
from nyxloom.session_extract import ExtractConfig
from nyxloom.session_extract.adapters import claude_code, codex
from nyxloom.session_extract.debug_diff import _drop_reason, _expected_kinds, render_debug
from nyxloom.session_extract.events import EventKind, NormalizedEvent
from nyxloom.session_extract.lossless import LosslessBlock, codex_blocks
from nyxloom.session_extract.render import _format_timestamp, render_text
from nyxloom.session_extract.select import select
from nyxloom.session_extract.stats import _simulate_profile
from nyxloom.session_extract.sessions import SessionNode, list_agents


def _question_call(call_id: str = "q1") -> dict:
    return {
        "type": "response_item",
        "ordinal": 1,
        "payload": {
            "type": "function_call",
            "name": "request_user_input_async",
            "call_id": call_id,
            "arguments": {
                "questions": [{
                    "title": "Deployment",
                    "question": "Where should it run?",
                    "options": ["Local", {"label": "Remote", "description": "shared host"}],
                }],
            },
        },
    }


def test_codex_question_shapes_and_prose_copies_preserve_each_prompt():
    payload = {
        "arguments": {
            "questions": [
                None,
                {"title": "Deploy", "question": "Where?", "options": [
                    "Local", {"label": "Remote", "description": "shared"}, 7,
                    {"label": 5, "description": "ignored"},
                    {"title": "Same", "description": "Same"},
                ]},
                {"title": "Only title", "question": " "},
                {"title": 3, "question": "Only question"},
                {"title": " ", "question": None},
            ],
        },
    }
    prompts = codex._question_call_items(payload)
    assert [item["item_index"] for item in prompts] == [1, 2, 3]
    assert prompts[0]["options"] == ("Local", "Remote: shared", "Same")
    assert prompts[1]["title"] == "Only title"
    assert prompts[2]["question"] == "Only question"
    assert codex._question_call_items({"arguments": "{"}) == []
    assert codex._question_call_items({"arguments": []}) == []
    assert codex._question_call_items({"arguments": {"questions": "bad"}}) == []

    state = codex.StreamState()
    state.remember_question_call({**_question_call()["payload"]}, prompt_emitted=False)
    prompt = state.questions[("q1", 0)]
    assert state.question_for_reply({"questionItemId": ["request_user_input_async", "q1", 0]}) == prompt
    assert state.question_for_reply({"questionItemId": "not json", "question": "Where should it run?"}) == prompt
    assert state.consume_question_prose_copy(" \n ") is None
    assert state.consume_question_prose_copy("Where should it run?\n- Local\n- Remote: shared host") == [prompt]
    assert state.consume_question_prose_copy("ordinary assistant prose") is None

    state.remember_question_call({**_question_call("q2")["payload"]}, prompt_emitted=True)
    assert state.consume_question_prose_copy(
        "Deployment\nWhere should it run?\n- Local\n- Remote: shared host"
    ) == []
    assert state.pending_prose_copies == []


def test_codex_reply_envelopes_fail_closed_and_keep_free_text():
    state = codex.StreamState()
    state.remember_question_call({**_question_call()["payload"]})
    opening = "<send_user_message_question_reply>"
    closing = "</send_user_message_question_reply>"
    reply = lambda rows: opening + json.dumps(rows) + closing

    assert codex._format_question_reply("ordinary chat", state) is None
    assert codex._format_question_reply(opening + "{" + closing, state) is None
    assert codex._format_question_reply(reply({"not": "a list"}), state) is None
    assert codex._format_question_reply(reply([]), state) is None
    assert codex._format_question_reply(reply(["bad row"]), state) is None
    assert codex._format_question_reply(reply([{"question": " ", "answer": "Local"}]), state) is None

    text = codex._format_question_reply(reply([{
        "question": "Where should it run?", "questionItemId": ["request_user_input_async", "q1", 0],
        "answer": "I will use a temporary remote host.",
    }]), state)
    assert text is not None
    assert "INTERVIEW: Deployment" in text
    assert "- Remote: shared host" in text
    assert "OPERATOR: I will use a temporary remote host." in text

    ambiguous = codex.StreamState()
    ambiguous.remember_question_call({**_question_call("q2")["payload"]})
    ambiguous.remember_question_call({**_question_call("q3")["payload"]})
    assert ambiguous.question_for_reply({"question": "Where should it run?"}) is None


def test_codex_stream_priming_replays_complete_records_and_tolerates_missing_sources(tmp_path):
    question = _question_call()
    prose = {
        "ordinal": 2,
        "type": "event_msg",
        "payload": {"type": "agent_message", "message": "Where should it run?\n- Local\n- Remote: shared host"},
    }
    raw = (json.dumps(question) + "\nnot-json\n" + json.dumps(prose) + "\npartial").encode()
    path = tmp_path / "rollout.jsonl"
    path.write_bytes(raw)

    state = codex.StreamState()
    codex.prime_stream_state(path, state, 0)
    assert state.questions == {}
    codex.prime_stream_state(path, state, len(raw))
    assert state.questions[("q1", 0)]["title"] == "Deployment"
    assert state.pending_prose_copies == []
    codex.prime_stream_state(tmp_path / "missing.jsonl", codex.StreamState(), 10)

    before = tmp_path / "since.jsonl"
    prefix = {"type": "event_msg", "ordinal": 0, "payload": {"type": "task_started"}}
    before.write_text(json.dumps(prefix) + "\n" + json.dumps(question) + "\n", encoding="utf-8")
    since_state = codex.StreamState()
    codex.prime_stream_state(before, since_state, before.stat().st_size, since_marker="0")
    assert since_state.pending_prose_copies[0][1] is True


def test_codex_old_and_new_records_clear_pending_copies_and_keep_tool_intent():
    state = codex.StreamState()
    state.remember_question_call({**_question_call()["payload"]})
    answer = {
        "type": "event_msg", "payload": {"type": "user_message", "message": "my answer"},
    }
    events = codex.parse_record(answer, 2, "2", ExtractConfig(), state)
    assert events[0].kind is EventKind.OPERATOR_TEXT
    assert state.pending_prose_copies == []

    tool = {
        "type": "response_item",
        "payload": {"type": "custom_tool_call", "name": "Shell", "input": {"description": "  inspect\n tree "}},
    }
    hidden = codex.parse_record(tool, 3, "3", ExtractConfig(), codex.StreamState())
    visible = codex.parse_record(
        tool, 3, "3", ExtractConfig(show_tool_calls=True, show_tool_call_intent=True), codex.StreamState(),
    )
    assert hidden == []
    assert visible[0].kind is EventKind.TOOL_CALL
    assert visible[0].text == "[tool call: Shell] inspect tree"

    old_copy = {"type": "event_msg", "payload": {
        "type": "agent_message", "message": "plain response",
    }}
    assert codex.parse_record(old_copy, 4, "4", ExtractConfig())[0].text == "plain response"


def test_codex_lossless_blocks_render_questions_replies_and_safe_tool_calls():
    call = _question_call()
    state = codex.StreamState()
    prompt = codex_blocks(call, "1", state=state)
    assert prompt[0].header.endswith("INTERVIEW]===" )
    assert "- Remote: shared host" in prompt[0].text

    tool = {"type": "response_item", "timestamp": "t", "payload": {
        "type": "custom_tool_call", "name": "Shell", "input": {"intent": "inspect files"},
    }}
    assert codex_blocks(tool, "2") == []
    visible = codex_blocks(tool, "2", show_tool_calls=True, show_tool_call_intent=True)
    assert visible == [LosslessBlock("===[2 | t | TOOL_CALL]===", "[tool call: Shell] inspect files")]

    compacted = {"type": "compacted", "payload": {"message": ""}}
    assert "[compacted]" == codex_blocks(compacted, "3")[0].text
    unknown = {"type": "event_msg", "payload": {"type": "task_started"}}
    assert codex_blocks(unknown, "4") == []


def test_timestamp_edges_are_deterministic_for_selection_rendering_and_debug():
    undated = NormalizedEvent(0, "bad", "not-a-date", EventKind.OPERATOR_TEXT, "bad timestamp")
    recent = NormalizedEvent(1, "recent", "2026-01-01T00:00:00", EventKind.OPERATOR_TEXT, "recent")
    with pytest.raises(ValueError, match="valid timestamp"):
        select([undated, recent], ExtractConfig(max_time_minutes=5))

    assert _format_timestamp("not-a-date", "%H:%M") is None
    assert _format_timestamp("2026-01-01T00:00:00", "%H:%M") == "00:00"
    naive = NormalizedEvent(0, "m0", "2026-01-01T00:00:00", EventKind.OPERATOR_TEXT, "hello")
    rendered = render_text(
        [naive], "codex", "m0", show_timestamps="pre", timestamp_format="%H:%M",
        metadata_position="pre",
    )
    assert "OPERATOR: 00:00 hello" in rendered
    assert rendered.startswith("<!-- nyxloom-extract: format=codex marker=m0")

    config = ExtractConfig(show_timestamps="pre", timestamp_format="%H:%M")
    diff = render_debug(
        "===[m0 | 2026-01-01T00:00:00 | USER]===\nhello\n",
        "OPERATOR: 00:00 hello\n", False, config=config, fmt="codex", all_events=[naive],
    )
    assert "[gap:" not in diff


def test_claude_tool_call_intent_is_opt_in_and_requires_string_text():
    record = {
        "type": "assistant", "uuid": "a1", "timestamp": "t",
        "message": {"role": "assistant", "content": [
            None,
            {"type": "tool_use", "name": "NoInput", "input": "private"},
            {"type": "tool_use", "name": "BadDescription", "input": {"description": 7}},
            {"type": "tool_use", "name": "Inspect", "input": {"description": "  list\n files "}},
        ]},
    }
    state = claude_code.StreamState()
    hidden = claude_code.parse_record(record, 0, "0", ExtractConfig(show_tool_calls=False), state)
    visible = claude_code.parse_record(
        record, 0, "0", ExtractConfig(show_tool_calls=True, show_tool_call_intent=True), state,
    )
    assert hidden == []
    assert [event.text for event in visible] == [
        "[tool call: NoInput]", "[tool call: BadDescription]", "[tool call: Inspect] list files",
    ]


def test_session_directory_discovery_skips_child_files_already_owned_by_parent(tmp_path):
    root = tmp_path / "project"
    child_dir = root / "subagents"
    child_dir.mkdir(parents=True)
    base_record = {
        "type": "user", "sessionId": "s1", "parentUuid": None,
        "message": {"role": "user", "content": "request"},
    }
    parent = root / "parent.jsonl"
    parent.write_text(json.dumps({**base_record, "uuid": "parent"}) + "\n", encoding="utf-8")
    child = child_dir / "agent-child.jsonl"
    child.write_text(json.dumps({**base_record, "uuid": "child", "isSidechain": True}) + "\n", encoding="utf-8")

    nodes = list_agents(root, fmt="claude-code")
    assert [node.path for node in nodes] == [str(parent)]


def test_source_metadata_points_at_the_opencode_database_inside_a_directory(tmp_path):
    directory = tmp_path / "opencode"
    directory.mkdir()
    database = directory / "opencode.db"
    database.write_bytes(b"SQLite placeholder")

    metadata = cli._source_metadata(directory, "opencode")
    assert metadata["name"] == "opencode.db"
    assert metadata["path"] == str(database)
    assert metadata["bytes"] == str(database.stat().st_size)


def test_debug_question_epoch_and_post_selection_reasons_are_precise():
    assert _expected_kinds("INTERVIEW") == (EventKind.QA_PAIR,)
    assert _expected_kinds("QA_PAIR") == (EventKind.QA_PAIR,)
    assert _expected_kinds("TOOL_CALL") == (EventKind.TOOL_CALL,)
    assert _expected_kinds("LIFECYCLE") == (EventKind.LIFECYCLE_MARKER,)

    event = NormalizedEvent(1, "m1", "t", EventKind.QA_PAIR, "question")
    event.meta["epoch"] = "1"
    event.meta["excluded_epoch"] = "true"
    reason = _drop_reason(
        "m1", "INTERVIEW", "question", ExtractConfig(), fmt="codex", is_leading_gap=False,
        events_by_marker={"m1": [event]}, kept_markers=set(),
    )
    assert "outside the selected --epochs span" in reason

    event.meta.pop("excluded_epoch")
    reason = _drop_reason(
        "m1", "INTERVIEW", "question", ExtractConfig(), fmt="codex", is_leading_gap=False,
        events_by_marker={"m1": [event]}, kept_markers=set(), post_selection_removed_markers={"m1"},
    )
    assert "post-selection --strip-stale-wakeups" in reason


def test_profile_simulation_obeys_compaction_error_and_word_stops():
    old = NormalizedEvent(0, "old", "t", EventKind.OPERATOR_TEXT, "older request")
    compact = NormalizedEvent(1, "compact", "t", EventKind.LIFECYCLE_MARKER, "[compact boundary]")
    compact.meta["boundary_type"] = "compaction"
    assert _simulate_profile([old, compact], ExtractConfig(max_compactions=0)) == {"compact": 2}

    api_error = NormalizedEvent(
        0, "error", "t", EventKind.ASSISTANT_TEXT, "[API ERROR: rate_limit] limit", checkpoint_score=0.0,
    )
    short = NormalizedEvent(1, "new", "t", EventKind.OPERATOR_TEXT, "new request")
    simulated = _simulate_profile([api_error, short], ExtractConfig())
    assert "error" not in simulated
    assert simulated["new"] == 2

    too_old = NormalizedEvent(0, "old", "t", EventKind.OPERATOR_TEXT, "older request")
    large = NormalizedEvent(1, "large", "t", EventKind.OPERATOR_TEXT, "one two three four")
    assert _simulate_profile([too_old, large], ExtractConfig(max_words=2)) == {"large": 4}


def test_cli_follow_and_json_flag_edges_return_human_errors(tmp_path, capsys):
    source = tmp_path / "session.jsonl"
    source.write_text(json.dumps({
        "type": "user", "uuid": "u1", "sessionId": "s1", "parentUuid": None,
        "timestamp": "2026-01-01T00:00:00Z", "message": {"role": "user", "content": "hello"},
    }) + "\n")

    for extra in (
        ["--epochs", "1"],
        ["--max-time-minutes", "5"],
        ["--extract-metadata", "pre"],
    ):
        assert cli.main(["extract", str(source), "--follow", *extra]) == 1
        error = capsys.readouterr().err
        assert "cannot be combined" in error or "cannot keep" in error

    for extra in (
        ["--show-timestamps", "post"],
        ["--timestamp-format", "%H:%M"],
        ["--extract-metadata", "pre"],
    ):
        assert cli.main(["extract", str(source), "--json", *extra]) == 1
        assert "only affect" in capsys.readouterr().err


def test_cli_argument_aliases_and_shared_follow_validation_are_preserved():
    args = SimpleNamespace(
        profile=None, max_checkpoints=None, checkpoints=3, answer_length=None, long_threshold=80,
        max_compactions=None, max_lifecycle_markers=2, blank_lines=None, insert_blank_lines=0,
        json=False, show_tool_calls=False, show_tool_call_intent=False, show_timestamps=None,
    )
    config = cli._extract_config_from_args(args)
    assert (config.max_checkpoints, config.long_comment_chars, config.max_compactions) == (3, 80, 2)
    assert config.insert_blank_lines == 0

    shared = SimpleNamespace(
        render_markdown=False, highlight=False, show_tool_call_intent=False, show_tool_calls=False,
        follow=True, interval=None, json=False, until=None, epochs=None, max_time_minutes=-1,
        extract_metadata="both", task=None, task_file=None,
    )
    assert cli._validate_render_and_follow_flags(shared) is None


def test_cli_report_conflict_and_detailed_json_paths(tmp_path, capsys):
    source = tmp_path / "session.jsonl"
    source.write_text("\n".join(json.dumps(record) for record in [
        {"type": "user", "uuid": "u1", "sessionId": "s1", "parentUuid": None,
         "timestamp": "2026-01-01T00:00:00Z", "message": {"role": "user", "content": "please check"}},
        {"type": "assistant", "uuid": "a1", "sessionId": "s1", "parentUuid": "u1",
         "timestamp": "2026-01-01T00:00:01Z", "message": {"role": "assistant", "usage": {
             "input_tokens": 2, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
             "output_tokens": 3,
         }, "content": [{"type": "text", "text": "Found the issue."}]}}]) + "\n")

    assert cli.main(["extract-report", str(source), "--detailed", "--type", "report-detailed"]) == 1
    assert "legacy alias" in capsys.readouterr().err
    assert cli.main(["extract-report", str(source), "--type", "report-detailed", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)


def test_cli_debug_rejects_missing_cursor_and_unsupported_tool_visibility(tmp_path, capsys, monkeypatch):
    source = tmp_path / "source.jsonl"
    source.write_text("{}\n")
    no_cursor = tmp_path / "no-cursor.txt"
    no_cursor.write_text("no marker here")
    assert cli.main([
        "extract-debug", str(source), "--format", "claude-code", "--since-file", str(no_cursor),
    ]) == 1
    assert "no embedded" in capsys.readouterr().err

    from nyxloom.session_extract import adapters

    monkeypatch.setattr(adapters, "detect", lambda _path: SimpleNamespace(name="reasonix"))
    assert cli.main(["extract-debug", str(source), "--show-tool-calls"]) == 1
    assert "not supported for 'reasonix'" in capsys.readouterr().err
