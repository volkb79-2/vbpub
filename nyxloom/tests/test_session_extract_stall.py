"""Mechanical Codex stop-state and stall-check contract.

The exec fixtures are trimmed from real controller runs: review-rg-flake,
session-audit-6, hc4, hc4-fin (the clean negative), and the interrupted
nyx-stall run.  The rollout fixture is a trimmed slice of the rollout carrying
review-rg-flake's real thread id.  Paths and command details with no contract
value were shortened; record shapes and final prose are preserved.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nyxloom.cli_harness import main as harness_main
from nyxloom.session_extract import ExtractConfig
from nyxloom.session_extract import adapters, stall as stall_mod
from nyxloom.session_extract.adapters import codex, codex_exec, detect
from nyxloom.session_extract.events import EventKind
from nyxloom.session_extract.stall import (
    VERDICTS,
    check_report,
    check_stall,
)
from nyxloom.session_extract.stopstate import (
    CodexRunFacts,
    _call_summary,
    _codex_command,
    _codex_text,
    _response_call_summary,
    build_stop_state,
    read_codex_facts,
)

FIX = Path(__file__).parent / "fixtures" / "stall"
REVIEW = FIX / "review-rg-flake.exec.jsonl"
AUDIT = FIX / "session-audit-6.exec.jsonl"
HC4 = FIX / "hc4.exec.jsonl"
REPLACING = FIX / "nyx-stall2-replacing.exec.jsonl"
DISCOURSE_ACTIVE_WAIT = FIX / "no-comma-active-wait.exec.jsonl"
CLEAN = FIX / "clean.exec.jsonl"
INTERRUPTED = FIX / "interrupted.exec.jsonl"
FAILED_EMPTY = FIX / "usage-limit-no-message.exec.jsonl"
WAITING = FIX / "ended-mid-wait.exec.jsonl"
TOOL_TEXT = FIX / "tool-call-as-text.exec.jsonl"
EMPTY = FIX / "empty.exec.jsonl"
ROLLOUT = FIX / "review-rg-flake.rollout.jsonl"
SKELETON = FIX / "session-audit-6.skeleton.md"


def _run(capsys, *argv) -> tuple[int, str, str]:
    code = harness_main(["extract", *map(str, argv)])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _facts(name: Path):
    return check_stall(name, "codex-exec")


def _write_text_run(path: Path, text: str) -> None:
    path.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "m1", "type": "agent_message", "text": text,
        }}) + "\n"
        + json.dumps({"type": "turn.completed"}) + "\n",
        encoding="utf-8",
    )


def test_codex_exec_adapter_is_registered_and_parses_prose(tmp_path):
    assert detect(REVIEW) is codex_exec
    assert detect(ROLLOUT) is codex
    events = codex_exec.parse(REVIEW, str(REVIEW), ExtractConfig())
    assert len(events) == 1
    assert events[0].kind is EventKind.ASSISTANT_TEXT
    assert "I\u2019ll rerun" in events[0].text
    assert events[0].marker == "2"


def test_codex_exec_sniff_accepts_thread_only_and_item_only_streams(tmp_path):
    thread_only = tmp_path / "thread.jsonl"
    thread_only.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n",
        encoding="utf-8",
    )
    assert detect(thread_only) is codex_exec

    item_only = tmp_path / "item.jsonl"
    item_only.write_text(
        json.dumps({"type": "item.started", "item": {
            "id": "c1", "type": "command_execution",
        }}) + "\n",
        encoding="utf-8",
    )
    assert detect(item_only) is codex_exec


def test_codex_exec_sniff_rejects_non_text_item_type_and_unreadable_path(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps({"type": "item.started", "item": {"type": 7}}) + "\n",
        encoding="utf-8",
    )
    assert codex_exec.sniff(bad) is False
    assert codex_exec.sniff(tmp_path / "missing.jsonl") is False


def test_codex_exec_sniff_has_an_exact_window_and_requires_a_thread_id(tmp_path):
    after_window = tmp_path / "after-window.jsonl"
    after_window.write_text(
        "\n".join(["not json"] * 10 + [json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "too late"},
        })]) + "\n",
        encoding="utf-8",
    )
    assert codex_exec.sniff(after_window) is False

    invalid_thread = tmp_path / "invalid-thread.jsonl"
    invalid_thread.write_text(
        json.dumps({"type": "thread.started", "thread_id": 7}) + "\n",
        encoding="utf-8",
    )
    assert codex_exec.sniff(invalid_thread) is False


def test_codex_exec_helpers_cover_text_and_command_shapes():
    assert codex_exec._item_text({"text": "text"}) == "text"
    assert codex_exec._item_text({"content": "content"}) == "content"
    assert codex_exec._item_text({"content": [
        {"text": "one"}, "ignored", {"text": 3},
    ]}) == "one"
    assert codex_exec._item_text({}) == ""
    assert codex_exec._command({"command": "pytest -q"}) == "pytest -q"
    assert codex_exec._command({"command": ["run-gate", "lane"]}) == "run-gate lane"
    assert codex_exec._command({}) == ""


def test_codex_exec_parser_covers_noise_and_empty_items(tmp_path):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        "\n"
        "not json\n"
        "[]\n"
        '{"type":"unknown"}\n'
        '{"type":"item.completed","item":"not-an-object"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":""}}\n'
        '{"type":"item.completed","item":{"type":"reasoning"}}\n'
        '{"type":"item.completed","item":{"type":"command_execution"}}\n',
        encoding="utf-8",
    )
    events = codex_exec.parse(
        fp, str(fp), ExtractConfig(include_thinking=True, show_tool_calls=True),
    )
    assert [event.kind for event in events] == [EventKind.TOOL_CALL]
    assert events[0].text == "[tool call: command_execution]"


def test_codex_exec_parser_marker_ranges_and_missing_markers(tmp_path):
    config = ExtractConfig(since_marker="1", until_marker="2")
    events = codex_exec.parse(REVIEW, str(REVIEW), config)
    assert len(events) == 1
    assert events[0].marker == "2"
    with pytest.raises(ValueError, match="--since marker"):
        codex_exec.parse(REVIEW, str(REVIEW), ExtractConfig(since_marker="99"))
    with pytest.raises(ValueError, match="--until marker"):
        codex_exec.parse(REVIEW, str(REVIEW), ExtractConfig(until_marker="99"))


def test_codex_exec_since_marker_excludes_its_own_event(tmp_path):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        "\n".join(json.dumps({
            "type": "item.completed",
            "item": {"id": f"m{index}", "type": "agent_message",
                     "text": f"message {index}"},
        }) for index in range(3)) + "\n",
        encoding="utf-8",
    )
    events = codex_exec.parse(fp, str(fp), ExtractConfig(since_marker="1"))
    assert [event.marker for event in events] == ["2"]


def test_codex_exec_adapter_thinking_and_tool_calls_are_optional(tmp_path):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "r1", "type": "reasoning", "text": "considering",
        }}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "c1", "type": "command_execution", "command": "pytest -q",
        }}) + "\n",
        encoding="utf-8",
    )
    default = codex_exec.parse(fp, str(fp), ExtractConfig())
    assert default == []
    visible = codex_exec.parse(
        fp, str(fp), ExtractConfig(include_thinking=True, show_tool_calls=True),
    )
    assert [event.kind for event in visible] == [EventKind.THINKING, EventKind.TOOL_CALL]
    assert "[tool call: command_execution] $ pytest -q" == visible[1].text


def test_completed_non_command_call_is_not_left_in_flight(tmp_path):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps({"type": "item.started", "item": {
            "id": "f1", "type": "file_change", "status": "in_progress",
        }}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "f1", "type": "file_change", "status": "completed",
        }}) + "\n"
        + json.dumps({"type": "turn.completed"}) + "\n",
        encoding="utf-8",
    )
    state = build_stop_state(fp, fmt="codex-exec")
    assert state.cause.startswith("empty")
    assert state.in_flight is None


def test_command_only_run_is_not_empty(tmp_path):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "c1", "type": "command_execution", "status": "completed",
        }}) + "\n"
        + json.dumps({"type": "turn.completed"}) + "\n",
        encoding="utf-8",
    )
    state = build_stop_state(fp, fmt="codex-exec")
    assert state.cause.startswith("ended after completed tool calls")
    assert check_stall(fp, "codex-exec").run_verdict == "ended-mid-wait"


def test_codex_rollout_stop_state_is_completed_message():
    state = build_stop_state(ROLLOUT, fmt="codex")
    assert state.cause.startswith("completed message")
    assert state.last_text.startswith("**Verdict: FIX-FIRST**")
    assert state.in_flight is None
    assert state.stopped_by_user is None
    assert check_stall(ROLLOUT, "codex").run_verdict == "ok"


def test_rollout_task_complete_supplies_the_only_message(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(
        json.dumps({"type": "session_meta", "payload": {"cli_version": "test"}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {
            "type": "task_complete",
            "last_agent_message": "Final report written.",
        }}) + "\n",
        encoding="utf-8",
    )
    state = build_stop_state(fp, fmt="codex")
    assert state.cause.startswith("completed message")
    assert state.last_text == "Final report written."


def test_rollout_agent_message_without_task_complete_is_completed(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(
        json.dumps({"type": "session_meta", "payload": {"cli_version": "test"}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {
            "type": "item_completed",
            "item": {
                "type": "AgentMessage",
                "content": [{"type": "Text", "text": "The report is written."}],
            },
        }}) + "\n",
        encoding="utf-8",
    )
    state = build_stop_state(fp, fmt="codex")
    assert state.cause.startswith("completed message")
    assert state.last_text == "The report is written."
    assert state.last_text_ts == ""


def test_codex_fact_helpers_cover_text_command_and_call_summaries():
    assert _codex_text({"text": "text"}) == "text"
    assert _codex_text({"content": "content"}) == "content"
    assert _codex_text({"content": [{"text": "one"}, {"text": "two"}]}) == "one\ntwo"
    assert _codex_text({"content": [{"text": 7}, "ignored"]}) == ""
    assert _codex_text({}) == ""
    assert _codex_command({"command": "pytest -q"}) == "pytest -q"
    assert _codex_command({"command": ["run-gate", "lane"]}) == "run-gate lane"
    assert _codex_command({}) == ""
    assert _call_summary({"type": "command_execution", "command": ["echo", "hi"]}) == (
        "command_execution: $ echo hi"
    )
    assert _call_summary({"type": "file_change", "name": "patch"}) == "patch"
    assert _call_summary({"type": "file_change", "name": 7}) == "file_change"
    assert _call_summary({"type": "file_change"}) == "file_change"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"name": "shell", "arguments": '{"cmd":["echo","hi"]}'}, "shell: $ echo hi"),
        ({"name": "shell", "arguments": '{"command":"pytest -q"}'}, "shell: $ pytest -q"),
        ({"name": "search", "arguments": '{"description":"find report"}'}, "search: find report"),
        ({"name": "search", "input": '{"intent":"inspect"}'}, "search: inspect"),
        ({"name": "shell", "arguments": "not-json"}, "shell"),
        ({"name": "shell", "arguments": []}, "shell"),
        ({"name": "shell", "arguments": {"cmd": 7}}, "shell"),
        ({"name": "search", "arguments": {"description": 7}}, "search"),
        ({"name": "shell", "arguments": {"note": "no command or intent"}}, "shell"),
        ({"type": "function_call"}, "function_call"),
    ],
)
def test_response_call_summary_handles_real_argument_shapes(payload, expected):
    assert _response_call_summary(payload) == expected


@pytest.mark.parametrize(
    ("ptype", "payload", "terminal_text"),
    [
        ("task_aborted", {"reason": "operator stopped"}, "operator stopped"),
        ("turn_aborted", {"message": "harness stopped"}, "harness stopped"),
        ("task_aborted", {"reason": 7}, ""),
    ],
)
def test_rollout_abort_records_are_terminal(tmp_path, ptype, payload, terminal_text):
    fp = tmp_path / f"{ptype}.jsonl"
    fp.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": ptype, **payload}}) + "\n",
        encoding="utf-8",
    )
    facts = read_codex_facts(fp)
    assert facts.terminal == "interrupted"
    assert facts.terminal_message == terminal_text


def test_rollout_noise_legacy_messages_and_response_items_are_consumed(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    records = [
        "",
        "not json",
        "[]",
        {"type": "unknown"},
        {"type": "turn.aborted", "error": {"message": 7}},
        {"type": "error", "message": 7},
        {"type": "event_msg", "payload": {
            "type": "agent_message", "message": " legacy answer ",
        }, "timestamp": "2026-11-10T00:00:00Z"},
        {"type": "event_msg", "payload": {
            "type": "item_completed", "item": {"type": "Other"},
        }},
        {"type": "response_item", "payload": {"type": "reasoning"}},
        {"type": "response_item", "payload": {"type": "function_call"}},
        {"type": "response_item", "payload": {
            "type": "function_call", "call_id": "call-1",
            "name": "shell", "arguments": "{\"cmd\": \"pytest -q\"}",
        }},
        {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call-1",
        }},
        {"type": "response_item", "payload": {"type": "function_call_output"}},
    ]
    fp.write_text(
        "\n".join(record if isinstance(record, str) else json.dumps(record)
                  for record in records) + "\n",
        encoding="utf-8",
    )
    facts = read_codex_facts(fp)
    assert facts.message_count == 1
    assert facts.last_text == "legacy answer"
    assert facts.last_text_ts == "2026-11-10T00:00:00Z"
    assert facts.command_count == 1
    assert facts.in_flight is None
    assert facts.terminal == "interrupted"
    assert facts.terminal_message == ""


def test_rollout_malformed_optional_fields_do_not_create_facts(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    records = [
        # Call IDs and text are optional labels, but malformed values cannot
        # create open calls or completed assistant messages.
        {"type": "item.started", "item": {
            "id": 7, "type": "command_execution", "command": "pytest -q",
        }},
        {"type": "item.completed", "item": {
            "id": 7, "type": "agent_message", "text": "   ",
        }},
        {"type": "event_msg", "payload": {
            "type": "task_complete", "last_agent_message": 7,
        }},
        {"type": "event_msg", "payload": {
            "type": "agent_message", "message": 7,
        }},
        {"type": "event_msg", "payload": {"type": "unrelated"}},
        {"type": "event_msg", "payload": {
            "type": "item_completed",
            "item": {"type": "AgentMessage", "content": []},
        }},
        {"type": "event_msg", "payload": {
            "type": "item_completed",
            "item": {"type": "CommandExecution", "id": 7},
        }},
        # An interrupted run without a textual reason still gets the stable
        # base cause, with no invented suffix after "--".
        {"type": "error", "message": 7},
    ]
    fp.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    facts = read_codex_facts(fp)
    assert facts.message_count == 0
    assert facts.last_text == ""
    assert facts.in_flight is None
    assert facts.command_count == 1
    assert facts.terminal == "interrupted"
    assert facts.terminal_message == ""

    state = build_stop_state(fp, fmt="codex")
    assert state.cause == "interrupted: the run recorded a terminal error/abort"


def test_rollout_command_completion_is_not_empty(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(
        json.dumps({"type": "session_meta", "payload": {"cli_version": "test"}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {
            "type": "item_completed",
            "item": {"type": "CommandExecution", "id": "call-1"},
        }}) + "\n",
        encoding="utf-8",
    )
    state = build_stop_state(fp, fmt="codex")
    assert state.cause.startswith("ended after completed tool calls")


@pytest.mark.parametrize("terminal", ["turn.failed", "error"])
def test_each_exec_terminal_error_record_is_interrupted(tmp_path, terminal):
    fp = tmp_path / f"{terminal}.jsonl"
    record = {"type": terminal}
    if terminal == "turn.failed":
        record["error"] = {"message": "aborted"}
    else:
        record["message"] = "aborted"
    fp.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps(record) + "\n",
        encoding="utf-8",
    )
    state = build_stop_state(fp, fmt="codex-exec")
    assert state.cause.startswith("interrupted")


def test_non_object_terminal_error_has_no_invented_message(tmp_path):
    fp = tmp_path / "turn.failed.jsonl"
    fp.write_text(
        json.dumps({"type": "turn.failed", "error": 7}) + "\n",
        encoding="utf-8",
    )
    facts = read_codex_facts(fp)
    assert facts.terminal == "interrupted"
    assert facts.terminal_message == ""


@pytest.mark.parametrize(
    ("fixture", "cause"),
    [
        (CLEAN, "completed message"),
        (INTERRUPTED, "interrupted"),
        (WAITING, "killed mid-call"),
        (EMPTY, "empty"),
    ],
)
def test_codex_exec_stop_state_causes(fixture, cause):
    state = build_stop_state(fixture, fmt="codex-exec")
    assert state.cause.startswith(cause)
    if cause == "killed mid-call":
        assert "run-gate rg-254-review" in state.in_flight
        assert "no completion recorded" in state.in_flight_outcome


def test_stop_state_cli_accepts_codex_exec(tmp_path, capsys):
    code, out, err = _run(capsys, REVIEW, "--profile", "all", "--stop-state")
    assert code == 0, err
    assert "[stop state]" in out
    assert "cause: completed message" in out
    assert "last assistant text: The first scratch gate" in out
    assert "in-flight call: none" in out


def test_stop_state_cli_accepts_codex_rollout(capsys):
    code, out, err = _run(
        capsys, ROLLOUT, "--format", "codex", "--profile", "all", "--stop-state",
    )
    assert code == 0, err
    assert "cause: completed message" in out
    assert "**Verdict: FIX-FIRST**" in out


@pytest.mark.parametrize(
    ("fixture", "verdict"),
    [
        (REVIEW, "intent-only-ending"),
        (AUDIT, "intent-only-ending"),
        (HC4, "intent-only-ending"),
        (REPLACING, "intent-only-ending"),
        (CLEAN, "ok"),
        (INTERRUPTED, "ended-mid-wait"),
        (FAILED_EMPTY, "ended-mid-wait"),
        (WAITING, "ended-mid-wait"),
        (TOOL_TEXT, "tool-call-as-text"),
        (EMPTY, "empty-run"),
    ],
)
def test_run_verdicts_are_closed_vocabulary(fixture, verdict):
    report = _facts(fixture)
    assert report.verdict in VERDICTS
    assert report.run_verdict == verdict


def test_review_run_has_the_real_intent_only_text():
    report = _facts(REVIEW)
    assert report.stop_state.last_text.endswith("for the throwaway copy.")
    assert "next action without an outcome" in report.detail


def test_nyx_stall2_replacing_run_has_the_real_intent_only_text():
    report = _facts(REPLACING)
    assert report.stop_state.last_text.endswith(
        "I’m replacing the stale report with the final receipts and complete "
        "R2 convergence record now."
    )
    assert "next action without an outcome" in report.detail


@pytest.mark.parametrize(
    "text",
    [
        "I’m doing the final worktree and commit checks now.",
        "I’m running R3 now.",
        "I’m updating the closeout now.",
    ],
)
def test_real_present_continuous_ongoing_actions_are_intent_only(tmp_path, text):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "m1", "type": "agent_message", "text": text,
        }}) + "\n"
        + json.dumps({"type": "turn.completed"}) + "\n",
        encoding="utf-8",
    )
    assert check_stall(fp, "codex-exec").run_verdict == "intent-only-ending"


def test_action_with_later_outcome_is_not_intent_only(tmp_path):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "m1", "type": "agent_message",
            "text": "I'll rerun the lane. The rerun passed and the report is written.",
        }}) + "\n"
        + json.dumps({"type": "turn.completed"}) + "\n",
        encoding="utf-8",
    )
    assert check_stall(fp, "codex-exec").run_verdict == "ok"


def test_terminal_failure_without_message_is_a_real_ended_mid_wait():
    report = _facts(FAILED_EMPTY)
    assert report.stop_state.last_text == ""
    assert report.stop_state.cause.startswith("interrupted")
    assert report.detail == (
        "the terminal error/abort ended the run before a completed outcome"
    )


def test_stall_check_builds_state_from_the_same_single_fact_snapshot(monkeypatch):
    facts = CodexRunFacts(
        message_count=1,
        command_count=0,
        last_text="The report is complete.",
        last_text_ts="",
        in_flight=None,
        terminal="completed",
        terminal_message="",
    )
    reads = []

    def first_read(path):
        reads.append(Path(path))
        return facts

    def refuse_second_read(path):
        reads.append(Path(path))
        raise AssertionError("check_stall parsed the run more than once")

    monkeypatch.setattr(stall_mod, "read_codex_facts", first_read)
    monkeypatch.setattr(
        "nyxloom.session_extract.stopstate.read_codex_facts",
        refuse_second_read,
    )
    report = check_stall(REVIEW, "codex-exec")
    assert reads == [REVIEW]
    assert report.stop_state.last_text == facts.last_text
    assert report.stop_state.cause.startswith("completed message")


def test_explicit_wait_without_in_flight_call_is_ended_mid_wait(tmp_path):
    fp = tmp_path / "events.jsonl"
    _write_text_run(
        fp,
        "The gate is still running; I am waiting for it to return.",
    )
    assert check_stall(fp, "codex-exec").run_verdict == "ended-mid-wait"


def test_negated_wait_prose_is_not_ended_mid_wait(tmp_path):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "m1", "type": "agent_message",
            "text": (
                "The report is complete and the worktree is clean. "
                "No campaign is in progress."
            ),
        }}) + "\n"
        + json.dumps({"type": "turn.completed"}) + "\n",
        encoding="utf-8",
    )
    assert check_stall(fp, "codex-exec").run_verdict == "ok"


def test_discourse_opener_before_active_wait_is_ended_mid_wait():
    report = _facts(DISCOURSE_ACTIVE_WAIT)
    assert report.stop_state.last_text == "No, the gate is still running"
    assert report.run_verdict == "ended-mid-wait"
    assert report.detail == (
        "the final text says a gate or process is still running"
    )


def test_explicit_wait_followed_by_completion_is_not_ended_mid_wait(tmp_path):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "m1", "type": "agent_message",
            "text": (
                "The gate was still running. It completed and the report is written."
            ),
        }}) + "\n"
        + json.dumps({"type": "turn.completed"}) + "\n",
        encoding="utf-8",
    )
    assert check_stall(fp, "codex-exec").run_verdict == "ok"


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        # The six sentences from the round-3 review.
        ("The campaign is not in progress.", "ok"),
        ("The campaign is in progress.", "ended-mid-wait"),
        ("Nothing is still running.", "ok"),
        ("The process is still running.", "ended-mid-wait"),
        ("The gate is still running, not yet finished.", "ended-mid-wait"),
        ("The gate was still running, then finished.", "ok"),
        # Indefinite negated subjects paired with active subjects.
        ("No one is still running.", "ok"),
        ("One process is still running.", "ended-mid-wait"),
        ("None is still running.", "ok"),
        ("The build is still running.", "ended-mid-wait"),
        ("Nobody was still running.", "ok"),
        ("The build was still running.", "ended-mid-wait"),
        # Negated completions paired with positive completions.
        ("The gate is still running and has not finished.", "ended-mid-wait"),
        ("The gate was still running and has now finished.", "ok"),
        ("The gate is still running and never finished.", "ended-mid-wait"),
        ("The gate was still running and eventually finished.", "ok"),
        ("The gate is still running; it is unfinished.", "ended-mid-wait"),
        ("The gate was still running; it is now finished.", "ok"),
    ],
)
def test_wait_subject_and_completion_boundary_pairs(tmp_path, text, verdict):
    fp = tmp_path / "events.jsonl"
    _write_text_run(fp, text)
    assert check_stall(fp, "codex-exec").run_verdict == verdict


def test_stall_check_text_and_json_and_exit_codes(tmp_path, capsys):
    code, out, err = _run(capsys, REVIEW, "--stall-check")
    assert code == 1 and err == ""
    assert out.startswith("[stall check]\n")
    assert "verdict: intent-only-ending" in out
    assert "last assistant text: The first scratch gate" in out
    assert "in-flight call: none" in out

    code, out, err = _run(capsys, CLEAN, "--stall-check")
    assert code == 0 and err == ""
    assert "verdict: ok" in out

    code, out, err = _run(capsys, REVIEW, "--stall-check", "--json")
    assert code == 1 and err == ""
    payload = json.loads(out)
    assert payload["verdict"] == "intent-only-ending"
    assert payload["stop_state"]["cause"].startswith("completed message")
    assert out.startswith('{\n  "detail":')


def test_report_missing_takes_primary_verdict(tmp_path, capsys):
    missing = tmp_path / "missing.md"
    report = check_stall(REVIEW, "codex-exec", expect_file=missing)
    assert report.run_verdict == "intent-only-ending"
    assert report.verdict == "report-missing"
    code, out, _err = _run(capsys, REVIEW, "--stall-check", "--expect-file", missing)
    assert code == 1
    assert "report: report-missing" in out


def test_report_skeleton_uses_real_session_audit_fixture(capsys):
    report = check_stall(AUDIT, "codex-exec", expect_file=SKELETON)
    assert report.run_verdict == "intent-only-ending"
    assert report.verdict == "report-skeleton"
    assert report.report.pending_markers == 3
    assert report.report.bytes < report.report.min_bytes
    code, out, _err = _run(
        capsys, AUDIT, "--stall-check", "--expect-file", SKELETON, "--json",
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["verdict"] == "report-skeleton"
    assert payload["report"]["min_bytes"] == 1500
    assert payload["report"]["max_pending_markers"] == 3


def test_report_is_ok_at_size_and_pending_marker_boundaries(tmp_path):
    report_path = tmp_path / "REPORT.md"
    report_path.write_text("P" * 1500, encoding="utf-8")
    check = check_report(report_path, min_bytes=1500, max_pending_markers=3)
    assert (check.status, check.bytes, check.pending_markers) == ("ok", 1500, 0)

    below = check_report(report_path, min_bytes=1501, max_pending_markers=3)
    assert below.status == "report-skeleton"

    marker_text = "Pending\nPending\nPending\nPending\n" + "P" * 1500
    report_path.write_text(marker_text, encoding="utf-8")
    too_pending = check_report(report_path, min_bytes=1500, max_pending_markers=3)
    assert too_pending.status == "report-skeleton" and too_pending.pending_markers == 4

    exact = check_report(report_path, min_bytes=1500, max_pending_markers=4)
    assert exact.status == "ok" and exact.pending_markers == 4


def test_stall_threshold_zero_is_valid():
    report = check_stall(
        REVIEW,
        "codex-exec",
        report_min_bytes=0,
        report_max_pending_markers=0,
    )
    assert report.report.min_bytes == 0
    assert report.report.max_pending_markers == 0


def test_directory_at_report_path_is_a_skeleton(tmp_path):
    directory = tmp_path / "REPORT.md"
    directory.mkdir()
    check = check_report(directory)
    assert check.status == "report-skeleton"
    assert check.bytes is None


def test_unreadable_report_is_a_skeleton(tmp_path, monkeypatch):
    report_path = tmp_path / "REPORT.md"
    report_path.write_text("complete report", encoding="utf-8")

    def refuse_read(*_args, **_kwargs):
        raise PermissionError("unreadable")

    monkeypatch.setattr(Path, "read_text", refuse_read)
    check = check_report(report_path)
    assert check.status == "report-skeleton"
    assert check.bytes is None


def test_cli_exposes_report_thresholds_and_guard(tmp_path, capsys):
    report_path = tmp_path / "REPORT.md"
    report_path.write_text("# Report\n\nPending\n", encoding="utf-8")
    code, out, _err = _run(
        capsys, CLEAN, "--stall-check", "--expect-file", report_path,
        "--report-min-bytes", "1", "--report-max-pending-markers", "1",
    )
    assert code == 0
    assert "report: ok" in out

    code, _out, err = _run(capsys, REVIEW, "--stall-check", "--expect-file", report_path,
                           "--report-min-bytes", "-1")
    assert code == 2 and "must be non-negative" in err


def test_stall_check_refuses_options_it_would_ignore(capsys):
    code, _out, err = _run(capsys, REVIEW, "--stall-check", "--profile", "all")
    assert code == 2
    assert "cannot be combined with --profile" in err

    code, _out, err = _run(capsys, REVIEW, "--stall-check", "--format", "claude-code")
    assert code == 2
    assert "Codex runs only" in err

    code, _out, err = _run(
        capsys, REVIEW, "--report-min-bytes", "1",
    )
    assert code == 2
    assert "need --stall-check" in err


def test_stall_check_refuses_thresholds_without_expect_file(capsys):
    code, _out, err = _run(
        capsys, REVIEW, "--stall-check", "--report-min-bytes", "1",
    )
    assert code == 2
    assert "need --expect-file" in err

    code, _out, err = _run(
        capsys, REVIEW, "--stall-check", "--expect-file", "missing.md",
        "--report-max-pending-markers", "-1",
    )
    assert code == 2
    assert "must be non-negative" in err


def test_check_stall_and_stop_state_reject_unknown_formats():
    with pytest.raises(ValueError, match="stall check does not support"):
        check_stall(REVIEW, "claude-code")
    with pytest.raises(ValueError, match="report minimum bytes"):
        check_stall(REVIEW, "codex-exec", report_min_bytes=-1)
    with pytest.raises(ValueError, match="pending-marker maximum"):
        check_stall(REVIEW, "codex-exec", report_max_pending_markers=-1)
    with pytest.raises(ValueError, match="stop state does not support"):
        build_stop_state(REVIEW, fmt="opencode")


def test_stall_check_runtime_guard_for_a_detected_non_codex_source(capsys, monkeypatch):
    monkeypatch.setattr(adapters, "detect", lambda _path: SimpleNamespace(name="claude-code"))
    code, _out, err = _run(capsys, REVIEW, "--stall-check")
    assert code == 1
    assert "--stall-check reads Codex runs only" in err


def test_stop_state_cli_refuses_an_unsupported_format(tmp_path, capsys):
    fp = tmp_path / "reasonix.jsonl"
    fp.write_text(
        "\n".join(json.dumps(record) for record in [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "request"},
            {"role": "assistant", "content": "answer"},
        ]) + "\n",
        encoding="utf-8",
    )
    code, _out, err = _run(
        capsys, fp, "--format", "reasonix", "--stop-state",
    )
    assert code == 1
    assert "--stop-state does not support 'reasonix'" in err
