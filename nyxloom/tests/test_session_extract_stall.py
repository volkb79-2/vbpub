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

import pytest

from nyxloom.cli_harness import main as harness_main
from nyxloom.session_extract import ExtractConfig
from nyxloom.session_extract.adapters import codex, codex_exec, detect
from nyxloom.session_extract.events import EventKind
from nyxloom.session_extract.stall import (
    VERDICTS,
    check_report,
    check_stall,
)
from nyxloom.session_extract.stopstate import build_stop_state

FIX = Path(__file__).parent / "fixtures" / "stall"
REVIEW = FIX / "review-rg-flake.exec.jsonl"
AUDIT = FIX / "session-audit-6.exec.jsonl"
HC4 = FIX / "hc4.exec.jsonl"
CLEAN = FIX / "clean.exec.jsonl"
INTERRUPTED = FIX / "interrupted.exec.jsonl"
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
        (CLEAN, "ok"),
        (INTERRUPTED, "ended-mid-wait"),
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


def test_explicit_wait_without_in_flight_call_is_ended_mid_wait(tmp_path):
    fp = tmp_path / "events.jsonl"
    fp.write_text(
        json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n"
        + json.dumps({"type": "item.completed", "item": {
            "id": "m1", "type": "agent_message",
            "text": "The gate is still running; I am waiting for it to return.",
        }}) + "\n"
        + json.dumps({"type": "turn.completed"}) + "\n",
        encoding="utf-8",
    )
    assert check_stall(fp, "codex-exec").run_verdict == "ended-mid-wait"


def test_stall_check_text_and_json_and_exit_codes(tmp_path, capsys):
    code, out, err = _run(capsys, REVIEW, "--stall-check")
    assert code == 1 and err == ""
    assert out.startswith("[stall check]\n")
    assert "verdict: intent-only-ending" in out

    code, out, err = _run(capsys, CLEAN, "--stall-check")
    assert code == 0 and err == ""
    assert "verdict: ok" in out

    code, out, err = _run(capsys, REVIEW, "--stall-check", "--json")
    assert code == 1 and err == ""
    payload = json.loads(out)
    assert payload["verdict"] == "intent-only-ending"
    assert payload["stop_state"]["cause"].startswith("completed message")


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
