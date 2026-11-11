"""Agent communication, time-bounded --since, and the readback budget."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nyxloom.cli_harness import main as harness_main
from nyxloom.session_extract import agentcomms, extract, select
from nyxloom.session_extract.config import ExtractConfig
from nyxloom.session_extract.events import EventKind, NormalizedEvent
from nyxloom.session_extract.timewindow import parse_since

FIX = Path(__file__).parent / "fixtures" / "readback"


def _run(capsys, *argv):
    code = harness_main(["extract", *map(str, argv)])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _event(seq, ts, kind, text, **meta):
    event = NormalizedEvent(seq, f"m{seq}", ts, kind, text)
    event.meta.update(meta)
    return event


def _notification(task="task-1", summary="Agent finished", result="result", output=True):
    fields = [
        f"<task-id>{task}</task-id>",
        "<status>completed</status>",
        f"<summary>{summary}</summary>",
    ]
    if output:
        fields.append("<output-file>/work/tasks/task-1.output</output-file>")
    if result:
        fields.append(f"<result>{result}</result>")
    return "<task-notification>\n" + "\n".join(fields) + "\n<note>ignored</note>\n</task-notification>"


# --- --since time grammar ----------------------------------------------------

def test_parse_since_keeps_markers_and_disambiguates_time_forms():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    assert parse_since("166", now=now) is None
    definition = parse_since("90m", now=now)
    assert definition.cutoff == "2026-11-11T10:30:00Z"
    assert definition.inclusive is True
    assert "relative 90m" in definition.description
    fixed = parse_since("2026-11-11T10:00:00+01:00", now=now)
    assert fixed.cutoff == "2026-11-11T09:00:00Z"
    assert fixed.inclusive is False
    naive_now = datetime(2026, 11, 11, 12, 0)
    assert parse_since("1d", now=naive_now).cutoff == "2026-11-10T12:00:00Z"


@pytest.mark.parametrize(
    "value",
    [" ", "2026-11-11T10:00:00", "2026-13-40T10:00:00Z", "0h", "10x", "999999d"],
)
def test_parse_since_refuses_malformed_time_shapes(value):
    with pytest.raises(ValueError, match="--since"):
        parse_since(value)


def test_since_time_filter_is_strict_for_iso_and_inclusive_for_relative():
    events = [
        _event(0, "2026-11-11T09:00:00Z", EventKind.OPERATOR_TEXT, "before"),
        _event(1, "2026-11-11T10:00:00Z", EventKind.OPERATOR_TEXT, "boundary"),
        _event(2, "2026-11-11T11:00:00Z", EventKind.OPERATOR_TEXT, "after"),
    ]
    strict = select.filter_since_time(
        events, ExtractConfig(since_time="2026-11-11T10:00:00Z"),
    )
    inclusive = select.filter_since_time(
        events,
        ExtractConfig(since_time="2026-11-11T10:00:00Z", since_time_inclusive=True),
    )
    assert [event.text for event in strict] == ["after"]
    assert [event.text for event in inclusive] == ["boundary", "after"]
    assert select.filter_since_time(events, ExtractConfig()) == events


def test_since_time_filter_drops_undated_events_and_rejects_bad_config_cutoff():
    undated = _event(0, "", EventKind.OPERATOR_TEXT, "unknown")
    assert select.filter_since_time(
        [undated], ExtractConfig(since_time="2026-11-11T10:00:00Z"),
    ) == []
    config = ExtractConfig()
    config.since_time = "not-time"
    with pytest.raises(ValueError, match="valid ISO"):
        select.filter_since_time([undated], config)


def test_cli_since_iso_filters_real_fixture_and_discloses_cutoff(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--profile", "all",
        "--since", "2026-11-11T10:07:00Z", "--agent-comms", "full",
        "--gap-marker", "none", "--no-color",
    )
    assert code == 0, err
    assert "Implement the bounded readback" not in out
    assert "task-example" in out
    assert "[readback resolved since:" not in out
    assert "[resolved since: ISO 2026-11-11T10:07:00Z -> " in out
    assert "(strictly after)" in out
    assert "Confirm the gate evidence" in out


def test_cli_since_cutoff_is_disclosed_in_json(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--json",
        "--since", "2026-11-11T10:07:00Z",
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["resolved_since"].startswith(
        "ISO 2026-11-11T10:07:00Z -> 2026-11-11T10:07:00Z"
    )


def test_cli_since_relative_and_malformed_values(capsys):
    code, out, _ = _run(
        capsys, FIX / "agent-comms.jsonl", "--profile", "all",
        "--since", "9999d", "--gap-marker", "none", "--no-color",
    )
    assert code == 0
    assert "source span" not in out
    code, _, err = _run(capsys, FIX / "agent-comms.jsonl", "--since", "10x")
    assert code == 2 and "relative durations" in err


def test_time_bound_keeps_the_full_parse_cursor(tmp_path):
    source = tmp_path / "session.jsonl"
    records = [
        {
            "isSidechain": False, "sessionId": "cursor-session", "parentUuid": None,
            "cwd": "/work/repo", "type": "user", "uuid": "u-before",
            "timestamp": "2026-11-11T10:00:00Z",
            "message": {"role": "user", "content": "before cutoff"},
        },
        {
            "isSidechain": False, "sessionId": "cursor-session", "parentUuid": "u-before",
            "cwd": "/work/repo", "type": "user", "uuid": "u-undated",
            "timestamp": "",
            "message": {"role": "user", "content": "undated tail"},
        },
    ]
    source.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    result = extract(
        source,
        ExtractConfig(since_time="2026-11-11T11:00:00Z"),
    )
    assert result.events == []
    assert result.last_marker == "u-undated"


# --- agent communication -----------------------------------------------------

def test_agent_comms_off_is_default_and_emits_no_agent_events(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--profile", "all",
        "--tool-calls", "none", "--gap-marker", "none", "--no-color",
    )
    assert code == 0, err
    assert "AGENT-OUT:" not in out and "AGENT-IN:" not in out
    assert "task-example" not in out


def test_agent_comms_are_retained_by_the_ordinary_selector(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--profile", "all",
        "--agent-comms", "full", "--gap-marker", "none", "--no-color",
    )
    assert code == 0, err
    assert "AGENT-OUT: Agent" in out
    assert "AGENT-IN: task=task-example" in out


def test_prose_only_excludes_enabled_agent_communication(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--profile", "all", "--prose-only",
        "--agent-comms", "full", "--no-color",
    )
    assert code == 0, err
    assert "AGENT-OUT:" not in out
    assert "AGENT-IN:" not in out


def test_agent_comms_summary_is_bounded_and_reports_identity(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--profile", "all",
        "--agent-comms", "summary", "--gap-marker", "none", "--no-color",
    )
    assert code == 0, err
    assert "AGENT-OUT: Agent [general-purpose; Review the extractor]" in out
    assert "AGENT-OUT: Bash launch codex-impl.sh" in out
    assert "brief: /work/brief.md" in out
    assert "AGENT-IN: task=task-example status=completed" in out
    assert "Harness scheduling note" not in out
    assert out.count("duplicate communication must not inflate") == 1


def test_agent_comms_full_keeps_prompt_summary_and_result(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--profile", "all",
        "--agent-comms", "full", "--gap-marker", "none", "--no-color",
    )
    assert code == 0, err
    assert "Read the implementation and report every behavioral gap" in out
    assert "summary: Agent \"Review the extractor\" finished" in out
    assert "result:\nTwo findings:" in out
    assert "note" not in out.lower().split("agent-in:", 1)[1].split("agent-out:", 1)[0]


def test_agent_tool_event_modes_cover_agent_task_sendmessage_and_fallbacks():
    config = ExtractConfig(agent_comms="summary")
    agent = agentcomms.agent_tool_events(
        "Agent", {"description": "Review\n  carefully", "prompt": "long prompt"},
        config, 1, "m1", "",
    )
    task = agentcomms.agent_tool_events(
        "Task", {"prompt": "task prompt"}, config, 2, "m2", "",
    )
    send_str = agentcomms.agent_tool_events(
        "SendMessage", {"to": "worker", "message": "short"}, config, 3, "m3", "",
    )
    send_obj = agentcomms.agent_tool_events(
        "SendMessage", {"recipient": "worker", "content": {"step": 2}}, config, 4, "m4", "",
    )
    fallback = agentcomms.agent_tool_events("Agent", {"foo": "bar"}, config, 5, "m5", "")
    empty_send = agentcomms.agent_tool_events(
        "SendMessage", {"to": "worker"}, config, 6, "m6", "",
    )
    assert agent[0].text.startswith("AGENT-OUT: Agent")
    assert "Review carefully" in agent[0].text
    assert task[0].text.startswith("AGENT-OUT: Task\n")
    assert "To=worker" not in send_str[0].text
    assert "to=worker" in send_str[0].text
    assert '"step": 2' in send_obj[0].text
    assert '{"foo": "bar"}' in fallback[0].text
    assert '{"to": "worker"}' in empty_send[0].text
    assert agentcomms.agent_tool_events("Read", {}, config, 6, "m6", "") == []
    assert agentcomms.agent_tool_events("Agent", {}, ExtractConfig(), 7, "m7", "") == []


def test_agent_bounds_full_and_summary_items():
    small = ExtractConfig(agent_comms="full", agent_comms_item_max_chars=20)
    event = agentcomms.agent_tool_events(
        "Agent", {"prompt": "abcdefghijklmnopqrstuvwxyz"}, small, 0, "m", "",
    )[0]
    assert "[truncated]" in event.text
    assert len(event.text.splitlines()[-1]) <= 20
    summary = ExtractConfig(agent_comms="summary", agent_comms_summary_chars=5)
    event = agentcomms.agent_tool_events("Agent", {"prompt": "a\nb c"}, summary, 0, "m", "")[0]
    assert "a b c" in event.text
    summary = ExtractConfig(agent_comms="summary", agent_comms_summary_chars=20)
    event = agentcomms.agent_tool_events(
        "Agent", {"prompt": "abcdefghij klmnopqrst uvw"}, summary, 0, "m", "",
    )[0]
    assert event.text.splitlines()[-1] == "abcdefgh [truncated]"
    assert agentcomms._bounded("abcdefghijkl", "full",
                               ExtractConfig(agent_comms_item_max_chars=10)) == "abcdefghij"


def test_notification_parser_strips_harness_fields_and_expiry_noise():
    config = ExtractConfig(agent_comms="full")
    payloads = agentcomms._notification_payloads(_notification())
    assert payloads == [{
        "task-id": "task-1",
        "status": "completed",
        "summary": "Agent finished",
        "output-file": "/work/tasks/task-1.output",
        "result": "result",
        "note": "ignored",
    }]
    event = agentcomms.notification_event(
        {
            "task-id": "task-a",
            "status": "completed",
            "summary": "Agent done",
            "result": "kept result",
            "output-file": "/work/run.output",
        }, config, 1, "m", "2026-01-01T00:00:00Z",
    )
    assert event is not None
    assert "output-file=/work/run.output" in event.text
    assert "summary: Agent done" in event.text
    assert "result:\nkept result" in event.text
    no_output = agentcomms.notification_event(
        {"task-id": "task-b", "status": "completed", "summary": "Agent done"},
        config, 2, "m", "",
    )
    assert no_output is not None
    assert no_output.text == "AGENT-IN: task=task-b status=completed\nsummary: Agent done"
    assert agentcomms.is_monitor_expired_noise({"summary": "Monitor expired: no change"})
    assert agentcomms.notification_event(
        {"summary": "Monitor expired: no change"}, config, 2, "m", "",
    ) is None
    assert agentcomms.notification_events(
        "<task-notification><summary>plain</summary></task-notification>",
        ExtractConfig(), 3, "m", "",
    ) == []
    assert agentcomms.notification_free_text(
        "before " + _notification() + " after"
    ) == "before  after"


def test_launch_runner_and_final_message_fallbacks(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    launcher = tmp_path / "codex-impl.sh"
    command = f"{launcher} /work /work/brief.md run-a"
    config = ExtractConfig(agent_comms="full")
    out = agentcomms.launch_events(command, str(tmp_path), config, 1, "m", "")
    assert out[0].text == "AGENT-OUT: Bash launch codex-impl.sh\nbrief: /work/brief.md"
    assert len(out) == 1

    (runs / "run-a.jsonl").write_text(json.dumps({
        "item": {"type": "agent_message", "text": "json result"},
    }) + "\n", encoding="utf-8")
    out = agentcomms.launch_events(command, str(tmp_path), config, 2, "m", "")
    assert out[-1].kind is EventKind.AGENT_IN
    assert "json result" in out[-1].text
    assert "run=run-a" in out[-1].text

    (runs / "run-a.last.txt").write_text("last-file result\n", encoding="utf-8")
    assert "last-file result" in agentcomms._final_agent_message(runs / "run-a.jsonl", config)
    (runs / "run-a.last.txt").write_text("\n", encoding="utf-8")
    assert "json result" in agentcomms._final_agent_message(runs / "run-a.jsonl", config)
    (runs / "run-a.jsonl").write_text(
        json.dumps({"item": {"type": "AgentMessage", "content": [{"text": "list result"}]}}) + "\n"
        + json.dumps({"text": "text result"}) + "\n"
        + json.dumps({"part": {"text": "part result"}}) + "\n"
        + "not-json\n",
        encoding="utf-8",
    )
    assert "part result" in agentcomms._final_agent_message(runs, config)
    assert agentcomms._final_agent_message(runs / "missing.jsonl", config) == ""
    assert agentcomms.launch_events(
        f"{launcher} /work /work/brief.md no-run", str(tmp_path), config, 3, "m", "",
    )[0].text.startswith("AGENT-OUT:")
    assert agentcomms.launch_events("ordinary command", None, config, 4, "m", "") == []
    assert agentcomms.launch_events(command, None, ExtractConfig(), 5, "m", "") == []


def test_launch_runner_uses_the_leading_cd_directory(tmp_path):
    program = tmp_path / "program"
    runs = program / "runs"
    runs.mkdir(parents=True)
    (runs / "run-cd.jsonl").write_text(
        json.dumps({"item": {"type": "agent_message", "text": "cd run result"}}) + "\n",
        encoding="utf-8",
    )
    config = ExtractConfig(agent_comms="full")
    events = agentcomms.launch_events(
        f"cd {program} && nohup ./codex-impl.sh /work /work/brief.md run-cd &",
        "/work",
        config,
        1,
        "m",
        "",
    )
    assert events[0].text.endswith("brief: /work/brief.md")
    assert events[-1].kind is EventKind.AGENT_IN
    assert "cd run result" in events[-1].text


def test_launch_patterns_are_configurable(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "custom-run.jsonl").write_text(
        json.dumps({"item": {"type": "agent_message", "text": "custom result"}}) + "\n",
        encoding="utf-8",
    )
    config = ExtractConfig(
        agent_comms="summary", agent_launch_patterns=("custom-impl.sh",),
    )
    events = agentcomms.launch_events(
        "custom-impl.sh /work /work/brief.md custom-run",
        str(tmp_path),
        config,
        1,
        "m",
        "",
    )
    assert events[0].meta["agent_launch"] == "custom-impl.sh"
    assert "custom result" in events[-1].text
    assert agentcomms.launch_events(
        "codex-impl.sh /work /work/brief.md ignored", str(tmp_path), config, 2, "m", "",
    ) == []


def test_launch_parser_handles_malformed_quoting_and_unresolved_paths():
    config = ExtractConfig(agent_comms="summary")
    events = agentcomms.launch_events(
        "codex-impl.sh /work '$BRIEF' run", None, config, 1, "m", "",
    )
    assert "brief: (brief path not found)" in events[0].text
    assert agentcomms.launch_events(
        "codex-impl.sh /work ignored run", "/not/a/directory", config, 2, "m", "",
    )[0].meta["agent_brief"] == "(brief path not found)"


def test_agent_path_and_launch_helpers_cover_refusal_and_io_boundaries(
    monkeypatch, tmp_path,
):
    config = ExtractConfig(agent_comms="full")
    cwd = Path("/work/repo")
    assert agentcomms._resolve_path("", cwd) is None
    assert agentcomms._resolve_path("-flag", cwd) is None
    assert agentcomms._resolve_path("/work/brief.md", cwd) == Path("/work/brief.md")
    assert agentcomms._resolve_path("brief.md", cwd) == cwd / "brief.md"
    assert agentcomms._effective_cwd(["cd", "-flag"], cwd) is None
    assert agentcomms._effective_cwd(["cd", "$WORK"], cwd) is None
    assert agentcomms._effective_cwd(["cd", "subdir"], cwd) == cwd / "subdir"

    def fail_resolve(*_args, **_kwargs):
        raise RuntimeError("unresolvable")

    def fail_expanduser(*_args, **_kwargs):
        raise OSError("no home")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", fail_resolve)
        assert agentcomms._safe_cwd("/work") is None
        assert agentcomms._effective_cwd(["cd", "subdir"], cwd) is None
    with monkeypatch.context() as patch:
        patch.setattr("nyxloom.session_extract.agentcomms.os.path.expanduser", fail_expanduser)
        assert agentcomms._resolve_path("brief.md", cwd) is None
        assert agentcomms._effective_cwd(["cd", "subdir"], cwd) is None

    malformed = "codex-impl.sh /work /work/brief.md 'unterminated"
    assert agentcomms._launch_argv(
        malformed, config.agent_launch_patterns,
    ) is not None
    events = agentcomms.launch_events(malformed, str(tmp_path), config, 1, "m", "")
    assert events[0].text == "AGENT-OUT: Bash launch codex-impl.sh\nbrief: /work/brief.md"


def test_final_agent_message_survives_unreadable_and_empty_streams(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    config = ExtractConfig(agent_comms="full")
    (runs / "run.empty.jsonl").write_text(
        "\n".join([
            json.dumps({"item": {"type": "agent_message", "text": ""}}),
            json.dumps({"text": ""}),
            json.dumps({"part": {"text": ""}}),
            "not-json",
        ]) + "\n",
        encoding="utf-8",
    )
    assert agentcomms._final_agent_message(runs, config) == ""

    last_path = tmp_path / "unreadable.last.txt"
    last_path.write_text("ignored\n", encoding="utf-8")
    unreadable_json = tmp_path / "unreadable.jsonl"
    unreadable_json.write_text('{"text": "ignored"}\n', encoding="utf-8")

    def fail_read_text(*_args, **_kwargs):
        raise OSError("unreadable")

    def fail_open(*_args, **_kwargs):
        raise OSError("unreadable")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", fail_read_text)
        assert agentcomms._final_agent_message(unreadable_json, config) == ""
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail_open)
        assert agentcomms._final_agent_message(unreadable_json, config) == ""


def test_agent_event_dedupe_keeps_noncommunication_and_first_copy():
    config = ExtractConfig(agent_comms="summary")
    first = agentcomms.agent_tool_events("Agent", {"prompt": "same"}, config, 1, "m1", "")[0]
    second = agentcomms.agent_tool_events("Agent", {"prompt": "same"}, config, 2, "m2", "")[0]
    prose = _event(3, "", EventKind.ASSISTANT_TEXT, "prose")
    assert agentcomms.dedupe_events([first, second, prose]) == [first, prose]


def test_agent_dedupe_uses_the_full_payload_beyond_the_display_cap():
    config = ExtractConfig(agent_comms="full", agent_comms_item_max_chars=10_000)
    prefix = "shared prefix " * 800
    first = agentcomms.agent_tool_events(
        "Agent", {"prompt": prefix + "first ending"}, config, 1, "m1", "",
    )[0]
    second = agentcomms.agent_tool_events(
        "Agent", {"prompt": prefix + "second ending"}, config, 2, "m2", "",
    )[0]
    assert agentcomms.dedupe_events([first, second]) == [first, second]


# --- readback budget ---------------------------------------------------------

def test_readback_fills_by_priority_without_partial_records():
    events = [
        _event(0, "2026-01-01T00:00:00Z", EventKind.ASSISTANT_TEXT, "old prose " + "a" * 400),
        _event(1, "", EventKind.TOOL_CALL, "Intent: old intent"),
        _event(2, "2026-01-01T00:01:00Z", EventKind.OPERATOR_TEXT, "newest operator"),
        _event(3, "2026-01-01T00:02:00Z", EventKind.ASSISTANT_TEXT, "new prose"),
        _event(4, "", EventKind.AGENT_IN, "AGENT-IN: result", agent_result_kind="notification"),
        _event(5, "", EventKind.AGENT_IN, "AGENT-IN: run result", agent_result_kind="run_log"),
    ]
    config = ExtractConfig(
        budget_tokens=533, agent_comms="summary", show_timestamps="none",
        gap_marker_mode="none",
    )
    kept, report = select.readback_select(events, config)
    assert [event.seq for event in kept] == [1, 2, 3, 4]
    assert report.categories["operator-interview"].kept == 1
    assert report.categories["assistant-prose"].dropped == 1
    assert report.categories["intent-lines"].kept == 1
    assert report.categories["run-results"].dropped == 1
    assert report.estimated_content_tokens <= 533 - select.READBACK_FOOTER_RESERVE_TOKENS


def test_readback_category_boundaries_and_invalid_budget():
    config = ExtractConfig(budget_tokens=2000, agent_comms="full")
    events = [
        _event(0, "", EventKind.OPERATOR_TEXT, "op"),
        _event(1, "", EventKind.QA_PAIR, "qa"),
        _event(2, "", EventKind.ASSISTANT_TEXT, "[API ERROR: rate_limit] retry"),
        _event(3, "", EventKind.ASSISTANT_TEXT, "prose"),
        _event(4, "", EventKind.AGENT_OUT, "out"),
        _event(5, "", EventKind.AGENT_IN, "in", agent_result_kind="run_log"),
        _event(6, "", EventKind.TOOL_CALL, "intent"),
        _event(7, "", EventKind.LIFECYCLE_MARKER, "lifecycle"),
    ]
    kept, report = select.readback_select(events, config)
    assert [event.seq for event in kept] == [0, 1, 3, 4, 5, 6]
    assert "lifecycle" not in [event.text for event in kept]
    assert report.source_first == ""
    off = ExtractConfig(budget_tokens=2000, agent_comms="off")
    assert select._readback_category(events[4], off) is None
    with pytest.raises(ValueError, match="positive"):
        select.readback_select(events, ExtractConfig())


def test_readback_keeps_newest_in_category_and_reports_dropped_span():
    events = [
        _event(0, "2026-01-01T00:00:00Z", EventKind.ASSISTANT_TEXT, "same"),
        _event(1, "2026-01-01T00:01:00Z", EventKind.ASSISTANT_TEXT, "same"),
        _event(2, "2026-01-01T00:02:00Z", EventKind.ASSISTANT_TEXT, "same"),
    ]
    config = ExtractConfig(
        budget_tokens=517, show_timestamps="none", gap_marker_mode="none",
    )
    kept, report = select.readback_select(events, config)
    assert [event.seq for event in kept] == [1, 2]
    category = report.categories["assistant-prose"]
    assert category.dropped == 1
    assert category.dropped_first == "2026-01-01T00:00:00Z"
    assert category.dropped_last == "2026-01-01T00:00:00Z"


def test_readback_drop_span_tracks_out_of_order_timestamps():
    events = [
        _event(0, "2026-01-01T00:00:00Z", EventKind.ASSISTANT_TEXT, "oldest"),
        _event(1, "2026-01-01T00:02:00Z", EventKind.ASSISTANT_TEXT, "newest"),
        _event(2, "2026-01-01T00:01:00Z", EventKind.ASSISTANT_TEXT, "middle"),
    ]
    config = ExtractConfig(
        budget_tokens=513, show_timestamps="none", gap_marker_mode="none",
    )
    kept, report = select.readback_select(events, config)
    assert kept == []
    category = report.categories["assistant-prose"]
    assert category.dropped == 3
    assert category.dropped_first == "2026-01-01T00:00:00Z"
    assert category.dropped_last == "2026-01-01T00:02:00Z"


def test_readback_estimator_and_footer_name_every_category():
    assert select.estimate_tokens("abcde") == 1
    report = select.ReadbackReport(100, 20, 12, agent_comms="full")
    for name in select.READBACK_CATEGORIES:
        report.categories[name] = select.BudgetCategory(
            kept=1, dropped=2, dropped_chars=8, dropped_tokens=2,
            dropped_first="2026-01-01T00:00:00Z",
            dropped_last="2026-01-01T01:00:00Z",
        )
    footer = __import__("nyxloom.session_extract.render", fromlist=["readback_footer"]).readback_footer(
        report, "relative 5h -> 2026-01-01T00:00:00Z",
    )
    assert "estimator=chars/4" in footer
    assert "operator-interview: kept=1 dropped=2" in footer
    assert "dropped_span=2026-01-01T00:00:00Z..2026-01-01T01:00:00Z" in footer
    assert "source span: empty" in footer
    assert "resolved since: relative 5h" in footer
    assert "operator/interview material did not fit" in footer


def test_readback_preset_applies_budget_and_prints_drop_footer(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--preset", "readback",
        "--budget-tokens", "1000", "--no-color",
    )
    assert code == 0, err
    assert "AGENT-IN: task=task-example" in out
    assert "[readback budget: limit=1000" in out
    assert "[readback operator-interview:" in out
    assert "[readback agent-comms: full]" in out
    assert "source span:" in out
    assert "<!-- nyxloom-extract:" in out


def test_readback_total_estimate_honors_requested_bound(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--preset", "readback",
        "--budget-tokens", "400", "--extract-metadata", "pre", "--no-color",
    )
    assert code == 0, err
    footer = "\n".join(line for line in out.splitlines() if line.startswith("[readback"))
    budget_line = footer.splitlines()[0]
    estimated_content = int(
        budget_line.split("estimated_content_tokens=")[1].split()[0]
    )
    assert estimated_content + select.estimate_tokens(footer) <= 400


def test_readback_explicit_options_override_the_preset(capsys):
    code, out, err = _run(
        capsys, FIX / "agent-comms.jsonl", "--preset", "readback",
        "--agent-comms", "off", "--budget-tokens", "1000", "--no-color",
    )
    assert code == 0, err
    assert "AGENT-IN:" not in out
    assert "[readback agent-comms: off]" in out


def test_agent_comms_refuses_adapters_without_agent_records(capsys):
    code, _, err = _run(
        capsys, Path(__file__).parent / "fixtures" / "stall" / "clean.exec.jsonl",
        "--format", "codex-exec", "--agent-comms", "full",
    )
    assert code == 1
    assert "--agent-comms does not support 'codex-exec'" in err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("--budget-tokens", "10"), "--preset readback"),
        (("--preset", "readback", "--budget-tokens", "0"), "must be positive"),
        (("--preset", "readback", "--max-words", "10"), "primary control"),
        (("--agent-launch-pattern", "custom.sh"), "needs --agent-comms"),
    ],
)
def test_readback_cli_refusals(capsys, argv, message):
    code, _, err = _run(capsys, FIX / "agent-comms.jsonl", *argv)
    assert code == 2
    assert message in err


def test_config_rejects_new_invalid_values():
    for kwargs in (
        {"agent_comms": "all"},
        {"agent_comms_summary_chars": 0},
        {"agent_comms_item_max_chars": 0},
        {"agent_launch_patterns": ()},
        {"agent_launch_patterns": ("",)},
        {"since_time": "2026-01-01T00:00:00"},
        {"since_time": "bad"},
        {"budget_tokens": 0},
    ):
        with pytest.raises(ValueError):
            ExtractConfig(**kwargs)
