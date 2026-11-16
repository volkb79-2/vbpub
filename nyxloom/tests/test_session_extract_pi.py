"""pi adapter + stop state, against SYNTHETIC fixtures written here.

Every fixture in this module is invented for the test: the prompts, paths,
tool arguments and results are placeholders, because real pi sessions carry
private prompts and controller paths. The record SHAPES are the ones verified
against real files (a stored session under
``~/.pi/agent/sessions/<encoded-cwd>/`` and a ``pi --mode json`` run stream):
which record types exist, where the message object sits, how a compaction
summary is spelled, and how the run stream reports tool execution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.session_extract import ExtractConfig, extract
from nyxloom.session_extract.adapters import DetectionError, detect, pi
from nyxloom.session_extract.events import EventKind
from nyxloom.session_extract.stopstate import build_stop_state, read_pi_facts

UUID = "0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"
CWD = "/work/example-repo"


def _session_header(timestamp="2026-01-01T09:00:00.000Z") -> dict:
    return {"type": "session", "version": 3, "id": UUID, "timestamp": timestamp, "cwd": CWD}


def _stored_message(role: str, content, *, stop_reason=None, error=None) -> dict:
    """One stored-session ``message`` record (message carries no timestamp)."""
    message = {"role": role, "content": content}
    if stop_reason is not None:
        message.update({
            "api": "openai-completions", "provider": "synthetic-provider",
            "model": "synthetic-model", "stopReason": stop_reason,
            "usage": {"input": 10, "output": 5, "totalTokens": 15},
        })
    if error is not None:
        message["errorMessage"] = error
    return {"type": "message", "id": "rec", "parentId": "prev", "message": message}


def _text(value: str) -> dict:
    return {"type": "text", "text": value}


def _thinking(value: str) -> dict:
    return {"type": "thinking", "thinking": value, "thinkingSignature": "reasoning"}


def _call(call_id: str, name: str, **arguments) -> dict:
    return {"type": "toolCall", "id": call_id, "name": name, "arguments": arguments}


def _stored(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def _normal_session(tmp_path: Path) -> Path:
    return _stored(tmp_path / "normal.jsonl", [
        _session_header(),
        {"type": "model_change", "id": "m0", "provider": "synthetic-provider", "modelId": "synthetic-model"},
        {"type": "thinking_level_change", "id": "m1", "thinkingLevel": "high"},
        _stored_message("system", "", stop_reason=None),
        _stored_message("user", [_text("Synthetic operator prompt.")]),
        _stored_message("assistant", [
            _thinking("Synthetic plan."),
            _text("Starting on it."),
            _call("call_synth_1", "bash", command="print hello"),
        ], stop_reason="toolUse"),
        _stored_message("toolResult", [_text("hello")]),
        _stored_message("toolResult", [_text("synthetic failure")], stop_reason=None),
        _stored_message("assistant", [_text("Done: the synthetic work is finished.")],
                        stop_reason="stop"),
    ])


def _run_stream(tmp_path: Path) -> Path:
    user = [_text("Synthetic operator prompt.")]
    assistant = [
        _text("Starting on it."),
        _call("call_synth_9", "bash", command="print hello"),
    ]
    tool_result = {"role": "toolResult", "toolCallId": "call_synth_9", "toolName": "bash",
                   "content": [_text("synthetic failure")], "isError": True,
                   "timestamp": 1790000000000}
    return _stored(tmp_path / "run.jsonl", [
        _session_header(),
        {"type": "agent_start"},
        {"type": "turn_start"},
        {"type": "message_start", "message": {"role": "user", "content": user}},
        {"type": "message_update", "usage": {"input": 0}, "assistantMessageEvent": {"type": "text_start"}},
        {"type": "message_end", "message": {"role": "user", "content": user}},
        {"type": "message_start", "message": {"role": "assistant", "content": []}},
        {"type": "message_update", "usage": {"input": 12}, "assistantMessageEvent": {"type": "text_start"}},
        {"type": "message_end", "message": {
            "role": "assistant", "content": assistant, "stopReason": "toolUse",
            "timestamp": 1790000000000, "errorMessage": None,
        }},
        {"type": "tool_execution_start", "toolCallId": "call_synth_9", "toolName": "bash",
         "args": {"command": "print hello"}},
        {"type": "tool_execution_update", "toolCallId": "call_synth_9", "partialOutput": "hel"},
        {"type": "tool_execution_end", "toolCallId": "call_synth_9", "toolName": "bash",
         "result": {"content": [_text("synthetic failure")], "isError": True}, "isError": True},
        {"type": "message_start", "message": tool_result},
        {"type": "message_end", "message": tool_result},
        {"type": "turn_end", "message": {"role": "assistant", "content": []}, "toolResults": []},
        {"type": "agent_end", "messages": []},
        {"type": "agent_settled"},
    ])


def _events(path: Path, **config) -> list:
    return pi.parse(path, str(path), ExtractConfig(**config))


# --- detection and discovery -------------------------------------------------

def test_sniff_recognizes_both_real_shapes(tmp_path):
    assert pi.sniff(_normal_session(tmp_path))
    assert pi.sniff(_run_stream(tmp_path))
    assert detect(_normal_session(tmp_path)).name == "pi"
    assert pi.list_sessions(_normal_session(tmp_path)) == [str(_normal_session(tmp_path))]


def test_sniff_rejects_other_harness_shapes(tmp_path):
    for name, record in (
        ("claude", {"type": "user", "sessionId": "s", "parentUuid": None}),
        ("codex", {"type": "session_meta", "payload": {"cli_version": "1.0"}}),
        ("reasonix", {"role": "user", "content": "hi"}),
    ):
        path = _stored(tmp_path / f"{name}.jsonl", [record])
        assert pi.sniff(path) is False
    # A `session` record without the id/cwd pair is not pi's header.
    assert pi.sniff(_stored(tmp_path / "partial.jsonl", [{"type": "session", "version": 3}])) is False
    assert pi.sniff(tmp_path / "missing.jsonl") is False
    with pytest.raises(DetectionError):
        detect(_stored(tmp_path / "nothing.jsonl", [{"note": "no session record"}]))


# --- stored session ----------------------------------------------------------

def test_stored_session_emits_operator_prose_and_no_system_framing(tmp_path):
    path = _normal_session(tmp_path)
    events = _events(path)
    operators = [event for event in events if event.kind is EventKind.OPERATOR_TEXT]
    assert [event.text for event in operators] == ["Synthetic operator prompt."]
    assert all("coding assistant" not in event.text for event in events)
    # Markers are the record's own line position, so --since can chain.
    assert operators[0].marker == "line4"


def test_thinking_is_opt_in_and_tool_calls_are_mode_driven(tmp_path):
    path = _normal_session(tmp_path)
    assert not [event for event in _events(path) if event.kind is EventKind.THINKING]
    thinking = [event for event in _events(path, include_thinking=True)
                if event.kind is EventKind.THINKING]
    assert [event.text for event in thinking] == ["Synthetic plan."]

    assert not [event for event in _events(path) if event.kind is EventKind.TOOL_CALL]
    for kwargs, expected in (
        ({"show_tool_calls": True}, "[tool call: bash]"),
        ({"show_tool_calls": True, "show_tool_call_intent": True}, "[tool call: bash]"),
        ({"tool_calls": "intent"}, None),
        ({"tool_calls": "intent-or-call"}, "[tool call: bash] $ print hello"),
        ({"tool_calls": "call"}, "[tool call: bash] $ print hello"),
    ):
        calls = [event.text for event in _events(path, **kwargs)
                 if event.kind is EventKind.TOOL_CALL]
        assert calls == ([expected] if expected else []), kwargs


def test_tool_call_meta_matches_the_shared_compress_contract(tmp_path):
    path = _normal_session(tmp_path)
    calls = [event for event in _events(path, tool_calls="call")
             if event.kind is EventKind.TOOL_CALL]
    assert calls[0].meta["tool"] == "bash"
    assert calls[0].meta["tool_kind"] == "other"
    assert calls[0].seq == 5 and calls[0].marker == "line5"

    edit = _stored(tmp_path / "edit.jsonl", [
        _session_header(),
        _stored_message("assistant", [_call("c1", "edit", path="/work/example-repo/a.py")],
                        stop_reason="toolUse"),
    ])
    event = next(event for event in _events(edit, tool_calls="call")
                 if event.kind is EventKind.TOOL_CALL)
    assert event.meta["tool_kind"] == "edit"
    assert event.meta["tool_file"] == "/work/example-repo/a.py"


def test_a_failed_tool_result_renders_with_the_call_and_can_be_hidden(tmp_path):
    path = _run_stream(tmp_path)
    errors = [event.text for event in _events(path)
              if event.kind is EventKind.TOOL_CALL and event.meta.get("tool_error")]
    assert errors == ["[tool error: bash] $ print hello => synthetic failure"]
    assert not [event for event in _events(path, tool_errors="hide")
                if event.meta.get("tool_error")]
    # A successful result is never rendered: it is the noise this tool drops.
    stored = _events(_normal_session(tmp_path))
    assert [event.text for event in stored if event.kind is EventKind.TOOL_CALL] == []


def test_compaction_marker_is_a_boundary_and_its_summary_is_opt_in(tmp_path):
    summary = "Synthetic compaction summary."
    stored = _stored(tmp_path / "compacted.jsonl", [
        _session_header(),
        _stored_message("user", [_text("Synthetic operator prompt.")]),
        {"type": "compaction", "id": "c0", "summary": summary},
        _stored_message("assistant", [_text("After the boundary.")], stop_reason="stop"),
    ])
    hidden = [event for event in _events(stored) if event.kind is EventKind.LIFECYCLE_MARKER]
    assert [event.text for event in hidden] == [pi.COMPACTION_LABEL]
    assert hidden[0].meta["boundary_type"] == "compaction"
    shown = [event for event in _events(stored, hide_compaction_content=False)
             if event.kind is EventKind.LIFECYCLE_MARKER]
    assert shown[0].text == f"{pi.COMPACTION_SUMMARY_LABEL} {summary}"

    # A run stream's compaction_end carries the summary under `result`, and
    # compaction_start alone contributes no marker (one boundary, not two).
    stream = _stored(tmp_path / "compaction-stream.jsonl", [
        _session_header(),
        {"type": "compaction_start", "reason": "threshold"},
        {"type": "compaction_end", "reason": "threshold", "result": {"summary": summary}},
    ])
    markers = [event.text for event in _events(stream, hide_compaction_content=False)
               if event.kind is EventKind.LIFECYCLE_MARKER]
    assert markers == [f"{pi.COMPACTION_SUMMARY_LABEL} {summary}"]


def test_a_failed_model_call_is_hidden_as_an_api_error(tmp_path):
    path = _stored(tmp_path / "error.jsonl", [
        _session_header(),
        _stored_message("user", [_text("Synthetic operator prompt.")]),
        _stored_message("assistant", [], stop_reason="error", error="synthetic 402"),
    ])
    events = _events(path)
    assert events[-1].text.startswith("[API ERROR: pi stopReason=error] synthetic 402")
    # The shared selector honours --show-api-errors for it, like every harness.
    hidden = [event for event in extract(path, ExtractConfig()).all_events
              if event.kind is EventKind.ASSISTANT_TEXT]
    assert [event.text for event in hidden if not event.text.startswith("[API ERROR")] == []


def test_launcher_run_inside_pi_is_agent_communication(tmp_path):
    path = _stored(tmp_path / "launch.jsonl", [
        _session_header(),
        _stored_message("assistant", [_call(
            "c2", "bash",
            command="./scripts/controller/codex-impl.sh synthetic-brief.md synthetic-run",
        )], stop_reason="toolUse"),
    ])
    assert not [event for event in _events(path) if event.kind is EventKind.AGENT_OUT]
    events = _events(path, agent_comms="summary",
                     agent_launch_patterns=("codex-impl.sh",))
    out = [event for event in events if event.kind is EventKind.AGENT_OUT]
    assert out[0].meta["agent_launch"] == "codex-impl.sh"
    assert "synthetic-brief.md" in out[0].text


def test_resumed_session_reads_every_cycle_in_one_file(tmp_path):
    path = _stored(tmp_path / "resumed.jsonl", [
        _session_header(),
        {"type": "agent_start"},
        _stored_message("user", [_text("First synthetic prompt.")]),
        _stored_message("assistant", [_text("First answer.")], stop_reason="stop"),
        {"type": "agent_end"},
        {"type": "agent_start"},
        _stored_message("user", [_text("Second synthetic prompt.")]),
        _stored_message("assistant", [_text("Second answer.")], stop_reason="stop"),
        {"type": "agent_settled"},
    ])
    operators = [event.text for event in _events(path)
                 if event.kind is EventKind.OPERATOR_TEXT]
    assert operators == ["First synthetic prompt.", "Second synthetic prompt."]
    # One file, both agent cycles: the stop state describes the LAST cycle.
    assert "completed message" in build_stop_state(path, fmt="pi").cause


def test_since_and_until_resolve_record_positions_and_refuse_unknown_markers(tmp_path):
    path = _normal_session(tmp_path)
    after = _events(path, since_marker="line4")
    assert next(event.marker for event in after) == "line5"
    until = _events(path, until_marker="line4")
    assert [event.text for event in until
            if event.kind is EventKind.OPERATOR_TEXT] == ["Synthetic operator prompt."]
    assert [event.marker for event in until][-1] == "line4"
    with pytest.raises(ValueError, match="not found as a pi line marker"):
        _events(path, since_marker="line99")
    with pytest.raises(ValueError, match="not found as a pi line marker"):
        _events(path, until_marker="line99")


def test_malformed_and_non_utf8_records_are_skipped_not_fatal(tmp_path):
    valid = json.dumps(_stored_message("user", [_text("Synthetic operator prompt.")]))
    broken = '{"type": "message", "message": {"role": "user", "content": [{"type": "text", "te'
    path = tmp_path / "ragged.jsonl"
    # The bytes carry a RAW 0xff (not the two-character escape JSON would
    # accept), which is what a damaged file really looks like; the record still
    # parses after errors="replace" and the truncated last line is dropped.
    raw_record = b'{"type": "message", "message": {"role": "assistant", "content": ['
    raw_record += b'{"type": "text", "text": "non\xffutf8"}]}, "stopReason": "stop"}\n'
    path.write_bytes(
        (json.dumps(_session_header()) + "\n").encode("utf-8")
        + valid.encode("utf-8") + b"\n"
        + raw_record.replace(b'non\xffutf8', b'non\xffutf8', 1)
        + broken.encode("utf-8")
    )
    events = _events(path)
    assert [event.text for event in events] == ["Synthetic operator prompt.", "non\ufffdutf8"]


def test_a_non_message_record_contributes_no_events(tmp_path):
    path = _stored(tmp_path / "lifecycle.jsonl", [
        _session_header(),
        {"type": "agent_start"},
        {"type": "turn_start"},
        {"type": "message_start", "message": {"role": "assistant", "content": []}},
        {"type": "model_change", "id": "m1"},
        {"type": "context_edit", "id": "e1", "targetId": "e0", "replacement": None},
        {"type": "agent_settled"},
    ])
    assert _events(path) == []


# --- stop state ----------------------------------------------------------------

def test_stop_state_names_the_empty_completion_end(tmp_path):
    path = _stored(tmp_path / "empty.jsonl", [
        _session_header(),
        _stored_message("user", [_text("Synthetic operator prompt.")]),
        _stored_message("assistant", [_text("Working on it.")], stop_reason="toolUse"),
        _stored_message("assistant", [], stop_reason="stop"),
    ])
    state = build_stop_state(path, fmt="pi")
    assert state.cause.startswith("empty-completion")
    assert state.last_text == "Working on it."
    assert state.in_flight is None


def test_stop_state_labels_length_as_context_full_and_thinking_only_as_empty(tmp_path):
    path = _stored(tmp_path / "length.jsonl", [
        _session_header(),
        _stored_message("assistant", [_thinking("out of room")], stop_reason="length"),
    ])
    assert "context-full" in build_stop_state(path, fmt="pi").cause
    for reason, fragment in (
        ("aborted", "aborted"),
        ("deferred", "deferred"),
        ("pending", "incomplete"),
    ):
        path = _stored(tmp_path / f"{reason}.jsonl", [
            _session_header(),
            _stored_message("assistant", [_thinking("cut")], stop_reason=reason),
        ])
        assert fragment in build_stop_state(path, fmt="pi").cause


def test_stop_state_reports_an_in_flight_call_and_a_tool_use_end(tmp_path):
    path = _stored(tmp_path / "in-flight.jsonl", [
        _session_header(),
        _stored_message("assistant", [
            _text("Calling."), _call("call_synth_5", "bash", command="print hello"),
        ], stop_reason="toolUse"),
    ])
    state = build_stop_state(path, fmt="pi")
    assert state.in_flight == "bash: $ print hello"
    assert state.in_flight_outcome.startswith("no completion recorded")

    # A run stream records the pair at tool_execution boundaries instead.
    stream = _stored(tmp_path / "in-flight-stream.jsonl", [
        _session_header(),
        {"type": "tool_execution_start", "toolCallId": "call_synth_6", "toolName": "read",
         "args": {"path": "/work/example-repo/a.py"}},
    ])
    assert build_stop_state(stream, fmt="pi").in_flight == \
        'read: {"path": "/work/example-repo/a.py"}'

    finished = _stored(tmp_path / "tooluse-end.jsonl", [
        _session_header(),
        _stored_message("assistant", [_call("call_synth_7", "bash", command="print hi")],
                        stop_reason="toolUse"),
        _stored_message("toolResult", [_text("hi")]),
    ])
    assert "tool-use" in build_stop_state(finished, fmt="pi").cause


def test_stop_state_branches_for_an_empty_or_call_only_session(tmp_path):
    header_only = _stored(tmp_path / "header.jsonl", [_session_header()])
    assert build_stop_state(header_only, fmt="pi").cause.startswith("empty")

    call_only = _stored(tmp_path / "call-only.jsonl", [
        _session_header(),
        _stored_message("assistant", [_call("call_synth_8", "bash", command="print hi")],
                        stop_reason="stop"),
        _stored_message("toolResult", [_text("hi")]),
    ])
    assert "without an assistant message" in build_stop_state(call_only, fmt="pi").cause

    unfinished = _stored(tmp_path / "unfinished.jsonl", [
        _session_header(),
        _stored_message("assistant", [_call("call_synth_9", "bash", command="print hi")],
                        stop_reason="toolUse"),
        _stored_message("toolResult", [_text("hi")]),
    ])
    assert build_stop_state(unfinished, fmt="pi").in_flight is None


def test_read_pi_facts_counts_messages_and_calls(tmp_path):
    facts = read_pi_facts(_run_stream(tmp_path))
    # Assistant prose only: the count is of assistant messages, which is what
    # the successor workflow asks a stop state for.
    assert facts.message_count == 1
    assert facts.tool_call_count == 1
    assert facts.stop_reason == "toolUse"
    assert facts.in_flight is None


def test_stop_state_cli_refuses_unsupported_formats_but_accepts_pi(tmp_path):
    reasonix = _stored(tmp_path / "reasonix.jsonl", [{"role": "user", "content": "x"}])
    with pytest.raises(ValueError, match="stop state does not support"):
        build_stop_state(reasonix, fmt="reasonix")


# --- record shapes the real files vary on ---------------------------------------

def test_record_timestamp_follows_the_record_that_carries_it(tmp_path):
    from nyxloom.session_extract.adapters.pi import record_timestamp

    assert record_timestamp({}, {"timestamp": 1790000000000}) == \
        "2026-09-21T14:13:20+00:00"
    # A boolean is an int subclass, so it must not be read as epoch-ms.
    assert record_timestamp({}, {"timestamp": True}) == ""
    assert record_timestamp({}, {"timestamp": "2026-01-01T09:00:00.000Z"}) == \
        "2026-01-01T09:00:00.000Z"
    # A stored message carries none; its WRAPPER carries the ISO string.
    assert record_timestamp({"timestamp": "2026-01-01T09:00:00.000Z"}, {}) == \
        "2026-01-01T09:00:00.000Z"
    assert record_timestamp({"timestamp": 5}, {}) == ""
    assert record_timestamp({}, {}) == ""


def test_sniff_ignores_blank_lines_and_a_non_numeric_session_version(tmp_path):
    path = _stored(tmp_path / "blank.jsonl", [
        {},
        {"type": "session", "version": True, "id": UUID, "cwd": CWD},
    ])
    assert pi.sniff(path) is False
    # A blank line before the header is skipped rather than ending the scan.
    path = tmp_path / "blank-then-header.jsonl"
    path.write_text("\n" + json.dumps(_session_header()) + "\n", encoding="utf-8")
    assert pi.sniff(path) is True


def test_a_compaction_summary_is_read_from_both_shapes(tmp_path):
    stream = _stored(tmp_path / "compaction-end.jsonl", [
        _session_header(),
        {"type": "compaction_start", "reason": "threshold"},
        {"type": "compaction_end", "result": {"summary": "Synthetic summary."}},
    ])
    summaries = [event.text for event in _events(stream, hide_compaction_content=False)
                 if event.kind is EventKind.LIFECYCLE_MARKER]
    assert summaries == ["[compact summary] Synthetic summary."]
    # A record whose result carries no summary still marks the boundary.
    bare = _stored(tmp_path / "compaction-bare.jsonl", [
        _session_header(),
        {"type": "compaction_end", "result": {}},
    ])
    assert [event.text for event in _events(bare) if event.kind is EventKind.LIFECYCLE_MARKER] == \
        ["[compact boundary]"]


def test_a_content_block_of_an_unexpected_shape_adds_no_event(tmp_path):
    path = _stored(tmp_path / "odd-blocks.jsonl", [
        _session_header(),
        _stored_message("user", []),
        _stored_message("assistant", [
            {"type": "text", "text": 7},
            {"type": "image", "source": "ignored"},
            {"type": "toolCall", "name": "bash", "arguments": {"command": "print x"}},
            {"type": "toolCall", "id": "c3", "arguments": {"command": "print y"}},
            {"type": "text", "text": "Real prose."},
        ], stop_reason="stop"),
    ])
    events = _events(path, tool_calls="call")
    assert [event.text for event in events] == ["[tool call: bash] $ print x", "Real prose."]


def test_launcher_detection_skips_non_bash_calls_and_a_non_string_command(tmp_path):
    path = _stored(tmp_path / "launch-odd.jsonl", [
        _session_header(),
        _stored_message("assistant", [
            _call("c4", "read", path="/work/example-repo/a.py"),
            _call("c5", "bash", command=7),
            _call("c6", "bash", command="./scripts/controller/codex-impl.sh brief.md run"),
        ], stop_reason="toolUse"),
    ])
    events = _events(path, agent_comms="summary",
                     agent_launch_patterns=("codex-impl.sh",))
    out = [event.text for event in events if event.kind is EventKind.AGENT_OUT]
    assert len(out) == 1 and "brief.md" in out[0]


def test_tool_file_meta_comes_from_either_argument_spelling(tmp_path):
    path = _stored(tmp_path / "file-args.jsonl", [
        _session_header(),
        _stored_message("assistant", [
            _call("c7", "read", file_path="/work/example-repo/b.py"),
            _call("c8", "bash", command="print hi"),
        ], stop_reason="toolUse"),
    ])
    calls = [event for event in _events(path, tool_calls="call")
             if event.kind is EventKind.TOOL_CALL]
    assert calls[0].meta["tool_file"] == "/work/example-repo/b.py"
    assert "tool_file" not in calls[1].meta


def test_parse_skips_blank_lines_and_json_that_is_not_an_object(tmp_path):
    path = tmp_path / "ragged-records.jsonl"
    path.write_text(
        json.dumps(_session_header()) + "\n"
        + "\n"
        + json.dumps(["a list, not a record"]) + "\n"
        + json.dumps(_stored_message("user", [_text("Synthetic operator prompt.")])) + "\n",
        encoding="utf-8",
    )
    assert [event.text for event in _events(path)] == ["Synthetic operator prompt."]


def test_stop_state_skips_a_record_whose_message_is_not_an_object(tmp_path):
    path = tmp_path / "ragged-stop.jsonl"
    path.write_text(
        json.dumps(_session_header()) + "\n"
        + "\n"
        + json.dumps({"type": "message", "message": "not an object"}) + "\n"
        + json.dumps(_stored_message("assistant", [_text("Real prose.")], stop_reason="stop")) + "\n",
        encoding="utf-8",
    )
    state = build_stop_state(path, fmt="pi")
    assert state.last_text == "Real prose."
    assert "completed message" in state.cause


def test_stop_state_appends_the_recorded_error_detail(tmp_path):
    path = _stored(tmp_path / "error-detail.jsonl", [
        _session_header(),
        _stored_message("assistant", [], stop_reason="error",
                        error="synthetic provider failure"),
    ])
    assert build_stop_state(path, fmt="pi").cause.endswith(
        "-- synthetic provider failure"
    )


def test_sniff_skips_a_line_that_is_not_json(tmp_path):
    path = tmp_path / "broken-line.jsonl"
    path.write_text(
        "not json at all\n" + json.dumps(_session_header()) + "\n", encoding="utf-8"
    )
    assert pi.sniff(path) is True


def test_a_compaction_result_that_is_not_an_object_carries_no_summary(tmp_path):
    path = _stored(tmp_path / "compaction-result.jsonl", [
        _session_header(),
        {"type": "compaction_end", "result": "a string, not an object"},
    ])
    assert [event.text for event in _events(path) if event.kind is EventKind.LIFECYCLE_MARKER] == \
        ["[compact boundary]"]


def test_a_tool_call_without_a_dict_of_arguments_still_renders(tmp_path):
    path = _stored(tmp_path / "odd-arguments.jsonl", [
        _session_header(),
        _stored_message("assistant", [
            {"type": "toolCall", "id": "c9", "name": "bash", "arguments": "print hi"},
        ], stop_reason="toolUse"),
    ])
    event = next(event for event in _events(path, tool_calls="call")
                 if event.kind is EventKind.TOOL_CALL)
    assert event.meta["tool"] == "bash" and "tool_file" not in event.meta


def test_stop_state_survives_a_truncated_last_record(tmp_path):
    path = tmp_path / "truncated-stop.jsonl"
    path.write_text(
        json.dumps(_session_header()) + "\n"
        + "\n"
        + json.dumps(_stored_message("assistant", [_text("Real prose.")], stop_reason="stop")) + "\n"
        + '{"type": "message", "message": {"role": "assis',
        encoding="utf-8",
    )
    state = build_stop_state(path, fmt="pi")
    assert state.last_text == "Real prose."


def test_stop_state_ignores_call_records_without_an_id(tmp_path):
    path = _stored(tmp_path / "no-id.jsonl", [
        _session_header(),
        _stored_message("assistant", [
            _text("Calling."),
            {"type": "toolCall", "arguments": {"command": "print hi"}},
        ], stop_reason="toolUse"),
        {"type": "tool_execution_start", "toolName": "read", "args": {"path": "a.py"}},
    ])
    state = build_stop_state(path, fmt="pi")
    # No id anywhere -> nothing to pair, so nothing is "in flight".
    assert state.in_flight is None
    assert read_pi_facts(path).tool_call_count == 1


def test_stop_state_reads_an_assistant_record_without_a_stop_reason(tmp_path):
    path = _stored(tmp_path / "no-reason.jsonl", [
        _session_header(),
        _stored_message("assistant", [_text("Real prose.")]),
    ])
    state = build_stop_state(path, fmt="pi")
    assert state.last_text == "Real prose."
    assert "completed message" in state.cause


def test_stop_state_prefers_an_open_execution_and_falls_back_to_an_open_call(tmp_path):
    # A closed execution plus a later message-level call: the execution loop
    # finds nothing open and the message-level bookkeeping answers instead.
    path = _stored(tmp_path / "mixed.jsonl", [
        _session_header(),
        {"type": "tool_execution_start", "toolCallId": "ex1", "toolName": "bash",
         "args": {"command": "print one"}},
        {"type": "tool_execution_end", "toolCallId": "ex1", "toolName": "bash"},
        _stored_message("assistant", [_call("c10", "bash", command="print two")],
                        stop_reason="toolUse"),
    ])
    assert build_stop_state(path, fmt="pi").in_flight == "bash: $ print two"

    # A closed call BEFORE an open one: the backward scan must skip the closed
    # one rather than reporting it as the call that never returned.
    path = _stored(tmp_path / "ordered.jsonl", [
        _session_header(),
        _stored_message("assistant", [_call("c11", "bash", command="print first")],
                        stop_reason="toolUse"),
        _stored_message("toolResult", [_text("first")]),
        _stored_message("assistant", [_call("c12", "bash", command="print second")],
                        stop_reason="toolUse"),
    ])
    assert build_stop_state(path, fmt="pi").in_flight == "bash: $ print second"


def test_stop_state_skips_a_line_that_is_json_but_not_an_object(tmp_path):
    path = tmp_path / "list-record.jsonl"
    path.write_text(
        json.dumps(_session_header()) + "\n"
        + json.dumps(["not a record"]) + "\n"
        + json.dumps(_stored_message("assistant", [_text("Real prose.")], stop_reason="stop")) + "\n",
        encoding="utf-8",
    )
    assert build_stop_state(path, fmt="pi").last_text == "Real prose."


def test_stop_state_scans_past_a_closed_execution_to_the_older_open_one(tmp_path):
    # A newer execution that already closed must be SKIPPED, not reported as
    # the call that never returned; the older open one is the answer.
    path = _stored(tmp_path / "executions-ordered.jsonl", [
        _session_header(),
        {"type": "tool_execution_start", "toolCallId": "old", "toolName": "bash",
         "args": {"command": "print older"}},
        {"type": "tool_execution_start", "toolCallId": "new", "toolName": "bash",
         "args": {"command": "print newer"}},
        {"type": "tool_execution_end", "toolCallId": "new", "toolName": "bash"},
        _stored_message("assistant", [_call("c14", "bash", command="print third")],
                        stop_reason="toolUse"),
    ])
    assert build_stop_state(path, fmt="pi").in_flight == "bash: $ print older"


def test_stop_state_message_level_scan_skips_a_closed_call_too(tmp_path):
    # calls_in_order = [closed, open, closed]: the backward scan must step
    # over the newer CLOSED call and reach the open one before it.
    def recorded(call_id: str, command: str) -> dict:
        return _stored_message("assistant", [_call(call_id, "bash", command=command)],
                               stop_reason="toolUse")

    result = {"type": "message", "message": {"role": "toolResult", "toolCallId": "c30",
                                             "content": [_text("done")]}}
    later_result = {"type": "message", "message": {"role": "toolResult", "toolCallId": "c32",
                                                   "content": [_text("done")]}}
    path = _stored(tmp_path / "message-order.jsonl", [
        _session_header(),
        recorded("c30", "print one"),
        result,
        recorded("c31", "print two"),
        recorded("c32", "print three"),
        later_result,
        recorded("c31", "print four"),
    ])
    assert build_stop_state(path, fmt="pi").in_flight == "bash: $ print four"


def test_stop_state_steps_over_a_closed_message_call_before_the_open_one(tmp_path):
    # The file ENDS on a tool_execution_start with no id (so "call" is the last
    # kind and no EXECUTION is open), and the newest MESSAGE-level call is
    # already closed: the loop must skip it and reach the older open call.
    path = _stored(tmp_path / "closed-newest.jsonl", [
        _session_header(),
        _stored_message("assistant", [_call("c70", "bash", command="print open")],
                        stop_reason="toolUse"),
        _stored_message("assistant", [_call("c71", "bash", command="print closed")],
                        stop_reason="toolUse"),
        {"type": "message", "message": {"role": "toolResult", "toolCallId": "c71",
                                        "content": [_text("closed")]}},
        {"type": "tool_execution_start", "toolName": "read", "args": {"path": "a.py"}},
    ])
    assert build_stop_state(path, fmt="pi").in_flight == "bash: $ print open"

