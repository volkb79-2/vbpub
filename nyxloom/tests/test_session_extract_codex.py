"""Codex CLI adapter tests, against a small synthetic rollout fixture
mirroring the real event_msg/session_meta shape verified on this machine's
actual ~/.codex/sessions/**/*.jsonl files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.session_extract import ExtractConfig
from nyxloom.session_extract.adapters import codex
from nyxloom.session_extract.events import EventKind


def _write_fixture(tmp_path: Path) -> Path:
    lines = [
        {"timestamp": "2026-01-01T00:00:00Z", "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": "abc", "cli_version": "0.149.0", "originator": "codex_exec"}},
        {"timestamp": "2026-01-01T00:00:01Z", "ordinal": 1, "type": "event_msg",
         "payload": {"type": "task_started", "turn_id": "t1"}},
        {"timestamp": "2026-01-01T00:00:02Z", "ordinal": 2, "type": "response_item",
         "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "system framing"}]}},
        {"timestamp": "2026-01-01T00:00:03Z", "ordinal": 3, "type": "event_msg",
         "payload": {"type": "user_message", "message": "evaluate docker-repack for our image"}},
        {"timestamp": "2026-01-01T00:00:04Z", "ordinal": 4, "type": "response_item",
         "payload": {"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": "{}"}},
        {"timestamp": "2026-01-01T00:00:05Z", "ordinal": 5, "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "c1", "output": "..."}},
        {"timestamp": "2026-01-01T00:00:06Z", "ordinal": 6, "type": "event_msg",
         "payload": {"type": "agent_message", "message": "Verdict: it works, here's why.", "phase": "commentary"}},
        {"timestamp": "2026-01-01T00:00:07Z", "ordinal": 7, "type": "event_msg",
         "payload": {"type": "context_compacted"}},
        {"timestamp": "2026-01-01T00:00:08Z", "ordinal": 8, "type": "event_msg",
         "payload": {"type": "agent_message", "message": "should never be reached"}},
    ]
    fp = tmp_path / "rollout-2026-01-01T00-00-00-abc.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return fp


def test_sniff(tmp_path):
    fp = _write_fixture(tmp_path)
    assert codex.sniff(fp)


def test_parse_uses_event_msg_layer_not_response_item(tmp_path):
    fp = _write_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig())

    # response_item stays outside ordinary extraction unless tool-call
    # visibility is requested; only event_msg prose is emitted by default.
    assert len(events) == 4  # user_message, agent_message, context_compacted, agent_message

    op = next(e for e in events if e.kind is EventKind.OPERATOR_TEXT)
    assert "docker-repack" in op.text
    assert op.marker == "3"

    asst = [e for e in events if e.kind is EventKind.ASSISTANT_TEXT]
    assert any("Verdict" in e.text for e in asst)

    marker = next(e for e in events if e.kind is EventKind.LIFECYCLE_MARKER)
    assert marker.marker == "7"


def test_since_marker(tmp_path):
    fp = _write_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig(since_marker="6"))
    assert all(e.marker != "3" for e in events)
    assert any(e.kind is EventKind.LIFECYCLE_MARKER for e in events)


def test_since_marker_unknown_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="not found as a source marker"):
        codex.parse(fp, str(fp), ExtractConfig(since_marker="does-not-exist"))


def test_until_marker(tmp_path):
    fp = _write_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig(until_marker="6"))
    assert [e.marker for e in events] == ["3", "6"]  # the context_compacted (7) is excluded


def test_until_marker_unknown_raises(tmp_path):
    fp = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="not found as a source marker"):
        codex.parse(fp, str(fp), ExtractConfig(until_marker="does-not-exist"))


def test_list_sessions_returns_the_one_synthetic_id(tmp_path):
    fp = _write_fixture(tmp_path)
    assert codex.list_sessions(fp) == [str(fp)]


def test_sniff_rejects_non_matching_content_regardless_of_suffix(tmp_path):
    fp = tmp_path / "rollout.txt"
    fp.write_text("irrelevant\n")
    assert not codex.sniff(fp)


def test_sniff_accepts_non_jsonl_suffix_when_content_matches(tmp_path):
    # 2026-09-11 fix: the session_meta/cli_version content check is the
    # real discriminator, not the suffix -- see claude_code.py's sniff()
    # docstring for the motivating case (an Agent-tool subagent's `.output`
    # transcript file).
    fp = tmp_path / "rollout.output"
    fp.write_text(
        json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"cli_version": "0.1.0"}}) + "\n",
        encoding="utf-8",
    )
    assert codex.sniff(fp)


def test_sniff_skips_blank_and_malformed_lines(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(
        "\n   \nnot json\n"
        + json.dumps({"timestamp": "t", "type": "session_meta",
                       "payload": {"cli_version": "0.1.0"}})
        + "\n",
        encoding="utf-8",
    )
    assert codex.sniff(fp)


def test_sniff_directory_with_jsonl_suffix_is_false(tmp_path):
    a_dir = tmp_path / "adir.jsonl"
    a_dir.mkdir()
    assert not codex.sniff(a_dir)  # open() raises IsADirectoryError (an OSError) -> False


def test_since_marker_resolves_an_ordinal_less_rollout_via_the_same_fallback(tmp_path):
    # Adversarial-review finding: pre-2026-08 rollout files lack `ordinal`
    # entirely (per this adapter's own docstring); the marker embedded on a
    # full parse falls back to str(seq), but resolution compared only
    # against str(r.get("ordinal")) -- the literal string "None" -- so
    # --since could never be resolved against such a file at all.
    lines = [
        {"timestamp": "t0", "type": "event_msg", "payload": {"type": "user_message", "message": "first"}},
        {"timestamp": "t1", "type": "event_msg", "payload": {"type": "agent_message", "message": "second"}},
    ]
    fp = tmp_path / "rollout.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    full = codex.parse(fp, str(fp), ExtractConfig())
    assert full[0].marker == "0"

    resumed = codex.parse(fp, str(fp), ExtractConfig(since_marker="0"))
    assert [e.text for e in resumed] == ["second"]


def test_chained_since_on_ordinal_less_rollout_never_reindexes_from_zero(tmp_path):
    # Adversarial-review finding: marker resolution (before slicing) and
    # marker emission (after slicing) each independently re-enumerated
    # `raw`, so a marker minted from an already-sliced parse used a
    # DIFFERENT fallback index space than a fresh, unsliced parse would
    # resolve it against -- a second --since hop chained off a first hop's
    # own output marker could resolve to the wrong record and silently
    # re-emit content already flushed to a prior snapshot. None of these
    # records carry `ordinal`, forcing every marker through the
    # str(seq)-style fallback (real pre-2026-08 rollout shape).
    lines = [
        {"timestamp": "t0", "type": "event_msg", "payload": {"type": "user_message", "message": "msg0"}},
        {"timestamp": "t1", "type": "event_msg", "payload": {"type": "user_message", "message": "msg1"}},
        {"timestamp": "t2", "type": "event_msg", "payload": {"type": "user_message", "message": "msg2"}},
        {"timestamp": "t3", "type": "event_msg", "payload": {"type": "user_message", "message": "msg3"}},
    ]
    fp = tmp_path / "rollout.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    hop1 = codex.parse(fp, str(fp), ExtractConfig())
    assert [e.marker for e in hop1] == ["0", "1", "2", "3"]

    hop2 = codex.parse(fp, str(fp), ExtractConfig(since_marker="1"))
    assert [(e.text, e.marker) for e in hop2] == [("msg2", "2"), ("msg3", "3")]

    hop3 = codex.parse(fp, str(fp), ExtractConfig(since_marker=hop2[-1].marker))
    assert hop3 == []


def _write_new_generation_fixture(tmp_path: Path) -> Path:
    # Mirrors the real item_completed schema Codex switched to at
    # cli_version 0.147.0 (2026-08-09) -- verified directly against real
    # local rollout files, not guessed. Every file from that version onward
    # uses ONLY this shape; the OLD flat user_message/agent_message shape
    # never appears.
    lines = [
        {"timestamp": "2026-08-23T00:00:00Z", "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": "abc", "cli_version": "0.149.0"}},
        {"timestamp": "2026-08-23T00:00:01Z", "ordinal": 1, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "UserMessage", "content": [{"type": "text", "text": "evaluate the new schema"}]}}},
        {"timestamp": "2026-08-23T00:00:02Z", "ordinal": 2, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "Reasoning", "raw_content": ["thinking about the schema change"]}}},
        {"timestamp": "2026-08-23T00:00:03Z", "ordinal": 3, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "AgentMessage", "content": [{"type": "Text", "text": "Confirmed, it works."}]}}},
        {"timestamp": "2026-08-23T00:00:04Z", "ordinal": 4, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "CommandExecution", "command": "ls"}}},
        {"timestamp": "2026-08-23T00:00:05Z", "ordinal": 5, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "CollabAgentToolCall", "tool": "spawn_agent"}}},
        {"timestamp": "2026-08-24T00:00:00Z", "ordinal": 6, "type": "compacted",
         "payload": {"message": "Summary of prior work: schema migration verified."}},
        {"timestamp": "2026-08-24T00:00:01Z", "ordinal": 7, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {"type": "ContextCompaction"}}},
        {"timestamp": "2026-08-24T00:00:02Z", "ordinal": 8, "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "type": "AgentMessage", "content": [{"type": "Text", "text": "should never be reached"}]}}},
    ]
    fp = tmp_path / "rollout-new-schema.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return fp


def test_new_generation_item_completed_schema_is_parsed(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig())

    op = next(e for e in events if e.kind is EventKind.OPERATOR_TEXT)
    assert op.text == "evaluate the new schema"

    asst = [e for e in events if e.kind is EventKind.ASSISTANT_TEXT]
    assert any("Confirmed, it works." in e.text for e in asst)

    # CommandExecution/CollabAgentToolCall/ContextCompaction are all noise
    assert not any("spawn_agent" in e.text for e in events)
    assert len(events) == 4  # UserMessage, AgentMessage x2, the compacted marker

    marker = next(e for e in events if e.kind is EventKind.LIFECYCLE_MARKER)
    assert marker.text == "[compaction summary omitted]"

    visible = codex.parse(fp, str(fp), ExtractConfig(hide_compaction_content=False))
    visible_marker = next(e for e in visible if e.kind is EventKind.LIFECYCLE_MARKER)
    assert "schema migration verified" in visible_marker.text


def test_new_generation_reasoning_emitted_only_when_include_thinking_set(tmp_path):
    fp = _write_new_generation_fixture(tmp_path)
    default = codex.parse(fp, str(fp), ExtractConfig())
    assert not any(e.kind is EventKind.THINKING for e in default)

    with_thinking = codex.parse(fp, str(fp), ExtractConfig(include_thinking=True))
    thinking = next(e for e in with_thinking if e.kind is EventKind.THINKING)
    assert "thinking about the schema change" in thinking.text


def test_new_generation_compacted_record_is_ignored_by_default_and_can_stop_walk(tmp_path):
    from nyxloom.session_extract import classifier
    from nyxloom.session_extract.select import select

    fp = _write_new_generation_fixture(tmp_path)
    events = codex.parse(fp, str(fp), ExtractConfig())
    classifier.score_events(events)
    kept = select(events, ExtractConfig())
    kept_texts = [e.text for e in kept]
    assert any("evaluate the new schema" in t for t in kept_texts)
    assert "[compaction summary omitted]" in kept_texts

    bounded = select(events, ExtractConfig(max_compactions=0))
    bounded_texts = [e.text for e in bounded]
    assert not any("evaluate the new schema" in t for t in bounded_texts)
    assert "[compaction summary omitted]" in bounded_texts


def test_parse_skips_blank_and_malformed_lines(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(
        "\n   \nnot json at all\n"
        + json.dumps({"timestamp": "2026-01-01T00:00:00Z", "ordinal": 0, "type": "event_msg",
                       "payload": {"type": "user_message", "message": "hi"}})
        + "\n",
        encoding="utf-8",
    )
    events = codex.parse(fp, str(fp), ExtractConfig())
    assert len(events) == 1
    assert events[0].kind is EventKind.OPERATOR_TEXT
    assert events[0].text == "hi"


def test_parse_record_is_the_same_per_record_rule_parse_itself_uses(tmp_path):
    # Same seam contract as the Claude Code adapter's: follow.py tails
    # through parse_record, so streaming parse()'s own record list through it
    # one record at a time must reproduce parse()'s output exactly.
    fp = _write_fixture(tmp_path)
    config = ExtractConfig(include_thinking=True)
    expected = codex.parse(fp, str(fp), config)

    raw = [
        rec for rec in (json.loads(line) for line in fp.read_text().splitlines() if line.strip())
        if codex.is_top_level_record(rec)
    ]
    streamed = []
    for seq, rec in enumerate(raw):
        streamed += codex.parse_record(rec, seq, str(seq), config)

    assert [(e.kind, e.text, e.marker) for e in streamed] == [
        (e.kind, e.text, e.marker) for e in expected
    ]


def test_parse_record_falls_back_to_the_given_marker_without_an_ordinal():
    # Real pre-2026-08 rollout files carry no `ordinal` at all -- see the
    # adapter's own module docstring.
    config = ExtractConfig()
    with_ordinal = {"timestamp": "t", "ordinal": 42, "type": "event_msg",
                    "payload": {"type": "user_message", "message": "hi"}}
    without = {"timestamp": "t", "type": "event_msg",
               "payload": {"type": "user_message", "message": "hi"}}
    assert codex.parse_record(with_ordinal, 0, "FALLBACK", config)[0].marker == "42"
    assert codex.parse_record(without, 0, "FALLBACK", config)[0].marker == "FALLBACK"


def test_is_top_level_record_includes_codex_response_items_for_tool_visibility():
    assert codex.is_top_level_record({"type": "event_msg"})
    assert codex.is_top_level_record({"type": "compacted"})
    assert codex.is_top_level_record({"type": "response_item"})


def test_custom_tool_call_intent_is_opt_in_and_does_not_expose_input(tmp_path):
    fp = _write_fixture(tmp_path)
    records = [json.loads(line) for line in fp.read_text(encoding="utf-8").splitlines()]
    records.insert(4, {
        "timestamp": "2026-01-01T00:00:04Z", "ordinal": 40, "type": "response_item",
        "payload": {"type": "custom_tool_call", "name": "exec_command",
                    "input": {"description": "  Inspect   the checkout ", "command": "cat private.txt"}},
    })
    fp.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")

    default = codex.parse(fp, str(fp), ExtractConfig())
    assert not any(event.kind is EventKind.TOOL_CALL for event in default)

    visible = codex.parse(
        fp, str(fp), ExtractConfig(show_tool_calls=True, show_tool_call_intent=True),
    )
    tool = next(event for event in visible if event.kind is EventKind.TOOL_CALL)
    assert tool.text == "[tool call: exec_command] Inspect the checkout"
    assert "cat private.txt" not in tool.text


def test_legacy_marker_fallbacks_survive_added_response_items(tmp_path):
    fp = tmp_path / "old-rollout.jsonl"
    rows = [
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "legacy prompt"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec_command",
                                                    "input": {"description": "Inspect"}}},
    ]
    fp.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    events = codex.parse(fp, str(fp), ExtractConfig(show_tool_calls=True, show_tool_call_intent=True))
    prompt = next(event for event in events if event.kind is EventKind.OPERATOR_TEXT)
    tool = next(event for event in events if event.kind is EventKind.TOOL_CALL)
    assert prompt.marker == "1"  # old event_msg-only fallback is unchanged
    assert tool.marker == "response_item-3"
    resumed = codex.parse(fp, str(fp), ExtractConfig(since_marker="1", show_tool_calls=True))
    assert any(event.kind is EventKind.TOOL_CALL for event in resumed)
    assert not codex.is_top_level_record({"type": "session_meta"})


def test_parse_record_returns_empty_for_empty_messages_and_unknown_items():
    config = ExtractConfig(include_thinking=True)
    empty_old_user = {"type": "event_msg", "payload": {"type": "user_message", "message": ""}}
    empty_old_agent = {"type": "event_msg", "payload": {"type": "agent_message", "message": ""}}
    assert codex.parse_record(empty_old_user, 0, "m", config) == []
    assert codex.parse_record(empty_old_agent, 0, "m", config) == []

    assert codex.parse_record(
        {"type": "compacted", "payload": {"message": ""}}, 0, "m", config
    )[0].text == "[compaction summary omitted]"
    assert codex.parse_record(
        {"type": "event_msg", "payload": {"type": "context_compacted"}}, 0, "m", config
    )[0].kind is EventKind.LIFECYCLE_MARKER

    for item_type in ("UserMessage", "AgentMessage"):
        assert codex.parse_record(
            {"type": "event_msg", "payload": {"type": "item_completed",
             "item": {"type": item_type, "content": []}}}, 0, "m", config
        ) == []
    assert codex.parse_record(
        {"type": "event_msg", "payload": {"type": "item_completed",
         "item": {"type": "Reasoning", "raw_content": []}}}, 0, "m", config
    ) == []
    assert codex.parse_record(
        {"type": "event_msg", "payload": {"type": "item_completed",
         "item": {"type": "FutureItem"}}}, 0, "m", config
    ) == []


def test_discovery_helpers_bound_scans_and_skip_malformed_records(tmp_path, monkeypatch):
    beyond_window = tmp_path / "beyond.jsonl"
    beyond_window.write_text("\n".join(["{}"] * 11) + "\n", encoding="utf-8")
    assert codex._first_session_meta(beyond_window) is None

    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text(
        "\nnot json\n"
        + json.dumps({"type": "event_msg", "timestamp": "t0"})
        + "\n",
        encoding="utf-8",
    )
    assert codex._first_session_meta(malformed) is None
    assert codex._scan_event_counts(malformed) == (1, "t0", "t0")

    empty_meta = tmp_path / "empty-meta.jsonl"
    empty_meta.write_text(json.dumps({"type": "session_meta", "payload": []}) + "\n")
    assert codex._first_session_meta(empty_meta) is None

    def fail_open(_self, *_args, **_kwargs):
        raise OSError("read denied")

    monkeypatch.setattr(Path, "open", fail_open)
    assert codex._first_session_meta(malformed) is None
    assert codex._scan_event_counts(malformed) == (0, None, None)


def test_list_agents_handles_missing_metadata_and_unrelated_rollouts(tmp_path):
    no_meta = tmp_path / "rollout-no-meta.jsonl"
    no_meta.write_text("{}\n", encoding="utf-8")
    assert codex.list_agents(no_meta) == []

    no_id = tmp_path / "rollout-no-id.jsonl"
    no_id.write_text(json.dumps({"type": "session_meta", "payload": {}}) + "\n")
    assert codex.list_agents(no_id) == []

    root = tmp_path / "rollout-root.jsonl"
    root.write_text(json.dumps({"type": "session_meta", "payload": {
        "session_id": "root", "id": "root", "thread_source": "user",
    }}) + "\n", encoding="utf-8")
    unrelated = tmp_path / "rollout-unrelated.jsonl"
    unrelated.write_text(json.dumps({"type": "session_meta", "payload": {
        "session_id": "other", "id": "other", "thread_source": "user",
    }}) + "\n", encoding="utf-8")

    nodes = codex.list_agents(root)
    assert [node.id for node in nodes] == ["root"]


def _question_reply_text(question: str, answer: str, call_id: str = "question-1") -> str:
    reply = [{
        "question": question,
        "answer": answer,
        "questionItemId": json.dumps(["request_user_input_async", call_id, 0]),
    }]
    return (
        "<send_user_message_question_reply>\n"
        + json.dumps(reply)
        + "\n</send_user_message_question_reply>"
    )


def _write_question_reply_fixture(
    tmp_path: Path, answer: str, *, delayed: bool
) -> tuple[Path, str, int, int]:
    question = "Which implementation approach should we use?"
    call_id = "question-1"
    records = [{
        "timestamp": "2026-09-25T00:00:00Z", "ordinal": 0, "type": "session_meta",
        "payload": {"session_id": "qa-session", "cli_version": "0.154.0"},
    }]

    def add(record_type: str, payload: dict) -> int:
        ordinal = len(records)
        records.append({
            "timestamp": f"2026-09-25T00:00:{ordinal:02d}Z",
            "ordinal": ordinal,
            "type": record_type,
            "payload": payload,
        })
        return ordinal

    question_ordinal = add("response_item", {
        "type": "function_call",
        "name": "request_user_input_async",
        "call_id": call_id,
        "arguments": json.dumps({"questions": [{
            "title": question,
            "question": question,
            "options": ["Keep the current worktree", "Create a new worktree"],
        }]}),
    })
    add("event_msg", {"type": "item_completed", "item": {
        "type": "AgentMessage",
        "content": [{"type": "Text", "text": (
            f"{question}\n- Keep the current worktree\n- Create a new worktree"
        )}],
    }})
    if delayed:
        add("event_msg", {"type": "item_completed", "item": {
            "type": "AgentMessage",
            "content": [{"type": "Text", "text": "I will inspect the tradeoffs first."}],
        }})
        add("response_item", {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "unrelated-command",
            "arguments": "{}",
        })
        add("event_msg", {"type": "item_completed", "item": {
            "type": "AgentMessage",
            "content": [{"type": "Text", "text": "The review found one relevant constraint."}],
        }})
        # A newer UI question can be issued before the older question's
        # submitted reply arrives. The reply envelope still identifies its
        # own question; an adapter must not bind it by recency or adjacency.
        later_question = "Should the report include tool-call labels?"
        add("response_item", {
            "type": "function_call",
            "name": "request_user_input_async",
            "call_id": "question-2",
            "arguments": json.dumps({"questions": [{
                "title": later_question,
                "question": later_question,
                "options": ["Yes", "No"],
            }]}),
        })
        add("event_msg", {"type": "item_completed", "item": {
            "type": "AgentMessage",
            "content": [{"type": "Text", "text": f"{later_question}\n- Yes\n- No"}],
        }})
    answer_ordinal = add("event_msg", {"type": "item_completed", "item": {
        "type": "UserMessage",
        "content": [{"type": "text", "text": _question_reply_text(question, answer, call_id)}],
    }})

    fp = tmp_path / f"question-reply-{delayed}-{len(answer)}.jsonl"
    fp.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return fp, question, question_ordinal, answer_ordinal


@pytest.mark.parametrize("delayed", [False, True], ids=["immediate", "after-progress"])
@pytest.mark.parametrize("answer_kind", ["choice", "free-text"])
def test_question_replies_keep_choice_and_free_text_at_their_source_position(
    tmp_path, delayed, answer_kind
):
    choice = "Keep the current worktree"
    answer = choice if answer_kind == "choice" else "Use the existing worktree after checking its base."
    fp, question, question_ordinal, answer_ordinal = _write_question_reply_fixture(
        tmp_path, answer, delayed=delayed
    )

    events = codex.parse(fp, str(fp), ExtractConfig())
    qa = [event for event in events if event.kind is EventKind.QA_PAIR]

    prompt = next(event for event in qa if event.marker == str(question_ordinal))
    assert prompt.text == (
        f"INTERVIEW: {question}\n"
        "- Keep the current worktree\n"
        "- Create a new worktree"
    )
    reply = next(event for event in qa if event.marker == str(answer_ordinal))
    assert reply.text == (
        f"INTERVIEW: {question}\n"
        "- Keep the current worktree\n"
        "- Create a new worktree\n\n"
        f"OPERATOR: {answer}"
    )
    assert "<send_user_message_question_reply>" not in reply.text
    assert not any(
        event.kind is EventKind.ASSISTANT_TEXT and question in event.text
        for event in events
    )
    assert len(qa) == (3 if delayed else 2)


@pytest.mark.parametrize("delayed", [False, True], ids=["immediate", "after-progress"])
@pytest.mark.parametrize(
    "answer", ["Keep the current worktree", "Use the existing checkout after reviewing its base."],
    ids=["typed-choice-text", "free-text"],
)
def test_plain_chat_response_is_preserved_without_inferred_question_link(
    tmp_path, delayed, answer
):
    """Ordinary Codex chat has no questionItemId; preserve it as authored text."""
    question = "Which implementation approach should we use?"
    records = [{
        "timestamp": "2026-09-25T00:00:00Z", "ordinal": 0, "type": "session_meta",
        "payload": {"session_id": "plain-chat-qa", "cli_version": "0.154.0"},
    }]

    def add(record_type: str, payload: dict) -> None:
        ordinal = len(records)
        records.append({
            "timestamp": f"2026-09-25T00:00:{ordinal:02d}Z",
            "ordinal": ordinal,
            "type": record_type,
            "payload": payload,
        })

    add("response_item", {
        "type": "function_call",
        "name": "request_user_input_async",
        "call_id": "plain-question",
        "arguments": json.dumps({"questions": [{
            "title": question,
            "question": question,
            "options": ["Keep the current worktree", "Create a new worktree"],
        }]}),
    })
    add("event_msg", {"type": "item_completed", "item": {
        "type": "AgentMessage",
        "content": [{"type": "Text", "text": f"{question}\n- Keep the current worktree\n- Create a new worktree"}],
    }})
    if delayed:
        add("event_msg", {"type": "item_completed", "item": {
            "type": "AgentMessage",
            "content": [{"type": "Text", "text": "I will check the branch state first."}],
        }})
    add("event_msg", {"type": "item_completed", "item": {
        "type": "UserMessage",
        "content": [{"type": "text", "text": answer}],
    }})

    fp = tmp_path / f"plain-question-reply-{delayed}-{len(answer)}.jsonl"
    fp.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    events = codex.parse(fp, str(fp), ExtractConfig())

    prompt = next(event for event in events if event.kind is EventKind.QA_PAIR)
    assert prompt.text == (
        f"INTERVIEW: {question}\n"
        "- Keep the current worktree\n"
        "- Create a new worktree"
    )
    assert not any(
        event.kind is EventKind.ASSISTANT_TEXT and question in event.text
        for event in events
    )
    operator = [event for event in events if event.kind is EventKind.OPERATOR_TEXT]
    assert len(operator) == 1
    assert operator[0].text == answer
    assert [event for event in events if event.kind is EventKind.QA_PAIR] == [prompt]


def test_question_reply_batch_keeps_every_pair_and_malformed_envelope_falls_back(tmp_path):
    rows = [
        {"question": "First choice?", "answer": "Choice A"},
        {"question": "Second question?", "answer": "My own words"},
    ]
    text = "<send_user_message_question_reply>\n" + json.dumps(rows) + "\n</send_user_message_question_reply>"
    rec = {"ordinal": 1, "timestamp": "t", "type": "event_msg", "payload": {
        "type": "item_completed", "item": {"type": "UserMessage", "content": [
            {"type": "text", "text": text},
        ]},
    }}
    event = codex.parse_record(rec, 0, "fallback", ExtractConfig())[0]
    assert event.kind is EventKind.QA_PAIR
    assert event.text == (
        "INTERVIEW: First choice?\n\nOPERATOR: Choice A\n\n"
        "INTERVIEW: Second question?\n\nOPERATOR: My own words"
    )

    malformed = dict(rec)
    malformed["payload"] = {"type": "item_completed", "item": {
        "type": "UserMessage", "content": [{"type": "text", "text": (
            "<send_user_message_question_reply>not json</send_user_message_question_reply>"
        )}],
    }}
    fallback = codex.parse_record(malformed, 1, "fallback-2", ExtractConfig())[0]
    assert fallback.kind is EventKind.OPERATOR_TEXT
    assert fallback.text == "<send_user_message_question_reply>not json</send_user_message_question_reply>"


def test_batched_answers_use_their_own_question_choices_for_choice_and_free_text(tmp_path):
    call_id = "question-batch"
    questions = [
        {
            "title": "Profile",
            "question": "Which profile should be the default?",
            "options": ["Review", "All"],
        },
        {
            "title": "Worktree",
            "question": "Where should the fix be made?",
            "options": ["Current worktree", "A new worktree"],
        },
    ]
    state = codex.StreamState()
    prompt_record = {
        "ordinal": 11,
        "timestamp": "t0",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "request_user_input_async",
            "call_id": call_id,
            "arguments": json.dumps({"questions": questions}),
        },
    }
    prompts = codex.parse_record(prompt_record, 0, "fallback", ExtractConfig(), state)
    assert [event.text for event in prompts] == [
        "INTERVIEW: Profile\nWhich profile should be the default?\n- Review\n- All",
        "INTERVIEW: Worktree\nWhere should the fix be made?\n- Current worktree\n- A new worktree",
    ]

    rows = [
        {
            "question": questions[0]["question"],
            "answer": "All",  # direct selection of a declared choice
            "questionItemId": json.dumps(["request_user_input_async", call_id, 0]),
        },
        {
            "question": questions[1]["question"],
            "answer": "Keep the existing checkout after checking its base.",
            "questionItemId": json.dumps(["request_user_input_async", call_id, 1]),
        },
    ]
    reply = "<send_user_message_question_reply>" + json.dumps(rows) + "</send_user_message_question_reply>"
    answer_record = {
        "ordinal": 12,
        "timestamp": "t1",
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "content": [{"type": "text", "text": reply}],
        }},
    }

    answer = codex.parse_record(answer_record, 1, "fallback", ExtractConfig(), state)[0]
    assert answer.kind is EventKind.QA_PAIR
    assert answer.text == (
        "INTERVIEW: Profile\nWhich profile should be the default?\n- Review\n- All\n\n"
        "OPERATOR: All\n\n"
        "INTERVIEW: Worktree\nWhere should the fix be made?\n"
        "- Current worktree\n- A new worktree\n\n"
        "OPERATOR: Keep the existing checkout after checking its base."
    )


def test_since_span_marks_a_question_copy_when_its_call_is_before_the_anchor(tmp_path):
    fp, question, question_ordinal, answer_ordinal = _write_question_reply_fixture(
        tmp_path, "Keep the current worktree", delayed=False,
    )

    events = codex.parse(
        fp, str(fp), ExtractConfig(since_marker=str(question_ordinal)),
    )

    prompt, reply = [event for event in events if event.kind is EventKind.QA_PAIR]
    assert prompt.marker == str(question_ordinal + 1)  # visible prose-copy record
    assert prompt.text == (
        f"INTERVIEW: {question}\n"
        "- Keep the current worktree\n"
        "- Create a new worktree"
    )
    assert reply.marker == str(answer_ordinal)
    assert "OPERATOR: Keep the current worktree" in reply.text
    assert "- Create a new worktree" in reply.text


def test_question_call_without_prose_copy_is_rendered_with_all_options(tmp_path):
    rec = {
        "timestamp": "2026-09-25T00:00:00Z", "ordinal": 12, "type": "response_item",
        "payload": {
            "type": "function_call", "name": "request_user_input_async", "call_id": "q-call",
            "arguments": json.dumps({"questions": [{
                "title": "Choose a profile",
                "question": "Which profile should be the default?",
                "options": [
                    {"label": "Review", "description": "short and focused"},
                    {"label": "All", "description": "full prose history"},
                ],
            }]}),
        },
    }

    event = codex.parse_record(rec, 0, "fallback", ExtractConfig())[0]

    assert event.kind is EventKind.QA_PAIR
    assert event.text == (
        "INTERVIEW: Choose a profile\n"
        "Which profile should be the default?\n"
        "- Review: short and focused\n"
        "- All: full prose history"
    )


def test_batch_question_prose_copy_is_replaced_by_marked_prompts(tmp_path):
    questions = [
        {"title": "First decision", "question": "First?", "options": ["A", "B"]},
        {"title": "Second decision", "question": "Second?", "options": ["C", "D"]},
    ]
    records = [
        {"timestamp": "t0", "ordinal": 0, "type": "response_item", "payload": {
            "type": "function_call", "name": "request_user_input_async", "call_id": "batch",
            "arguments": json.dumps({"questions": questions}),
        }},
        {"timestamp": "t1", "ordinal": 1, "type": "event_msg", "payload": {
            "type": "item_completed", "item": {
                "type": "AgentMessage", "content": [{"type": "Text", "text": (
                    "First?\n- A\n- B\n\nSecond?\n- C\n- D"
                )}],
            },
        }},
    ]
    fp = tmp_path / "batch-question-copy.jsonl"
    fp.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    events = codex.parse(fp, str(fp), ExtractConfig())

    assert [event.text for event in events] == [
        "INTERVIEW: First decision\nFirst?\n- A\n- B",
        "INTERVIEW: Second decision\nSecond?\n- C\n- D",
    ]
