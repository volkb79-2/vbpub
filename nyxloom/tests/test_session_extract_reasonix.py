"""Reasonix adapter, lossless, and CLI contract tests.

The fixtures use the primary chat-object shape observed in local Reasonix
session files, including an assistant record with null content, plain-text
``reasoning_content``, and structured ``tool_calls``.  They remain small and
contain no real prompts, credentials, or session history.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom import cli
from nyxloom.session_extract import ExtractConfig
from nyxloom.session_extract.adapters import detect, reasonix
from nyxloom.session_extract.events import EventKind
from nyxloom.session_extract.follow import JsonlSource
from nyxloom.session_extract.lossless import dump_reasonix


def _write_reasonix_fixture(tmp_path: Path) -> Path:
    """A synthetic fixture derived from an actual local Reasonix file shape."""
    records = [
        {"role": "system", "content": "You are Reasonix."},
        {"role": "user", "content": "operator request"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "private reasoning",
            "tool_calls": [{"id": "call-1", "type": "function"}],
        },
        {
            "role": "tool",
            "content": "tool output must not become operator text",
            "name": "shell",
            "tool_call_id": "call-1",
        },
        {
            "role": "assistant",
            "content": "assistant answer",
            "reasoning_content": "supporting reasoning",
            "tool_calls": [],
            "workDurationMs": 17,
        },
        {"role": "user", "content": ""},
        {"role": "assistant", "content": {"structured": "not prose"}},
        {"role": "developer", "content": "unknown role"},
        "malformed record shape",
    ]
    path = tmp_path / "20260724-001821.579557040-deepseek-v4-flash.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    return path


def _append(path: Path, *records: object) -> int:
    payload = "".join(json.dumps(record) + "\n" for record in records)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
    return len(payload.encode("utf-8"))


def test_sniff_accepts_primary_reasonix_and_registry_detection(tmp_path):
    path = _write_reasonix_fixture(tmp_path)
    assert reasonix.sniff(path) is True
    assert reasonix.list_sessions(path) == [str(path)]
    assert detect(path).name == "reasonix"


def test_sniff_rejects_reasonix_event_snapshot_even_with_chat_messages(tmp_path):
    path = tmp_path / "20260724-001821.579557040-deepseek-v4-flash.events.jsonl"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "type": "replace",
                "revision": 1,
                "messages": [{"role": "user", "content": "snapshot"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert reasonix.sniff(path) is False
    with pytest.raises(ValueError, match="events snapshot"):
        reasonix.parse(path, str(path), ExtractConfig())


def test_sniff_is_content_based_and_does_not_claim_other_jsonl_shapes(tmp_path):
    claude = tmp_path / "claude.jsonl"
    claude.write_text(
        json.dumps({"type": "user", "sessionId": "s"}) + "\n", encoding="utf-8"
    )
    codex = tmp_path / "codex.jsonl"
    codex.write_text(
        json.dumps({"type": "session_meta", "payload": {"cli_version": "1"}}) + "\n",
        encoding="utf-8",
    )
    plain = tmp_path / "plain.txt"
    plain.write_text("not JSONL\n", encoding="utf-8")
    assert reasonix.sniff(claude) is False
    assert reasonix.sniff(codex) is False
    assert reasonix.sniff(plain) is False


def test_parse_filters_system_tool_structured_and_empty_records(tmp_path):
    path = _write_reasonix_fixture(tmp_path)
    events = reasonix.parse(path, str(path), ExtractConfig())
    assert [
        (event.kind, event.text, event.marker, event.timestamp) for event in events
    ] == [
        (EventKind.OPERATOR_TEXT, "operator request", "line1", ""),
        (EventKind.ASSISTANT_TEXT, "assistant answer", "line4", ""),
    ]
    assert all("Reasonix" not in event.text for event in events)
    assert all("tool output" not in event.text for event in events)


def test_parse_includes_reasoning_only_when_requested(tmp_path):
    path = _write_reasonix_fixture(tmp_path)
    events = reasonix.parse(path, str(path), ExtractConfig(include_thinking=True))
    assert [(event.kind, event.text, event.marker) for event in events] == [
        (EventKind.OPERATOR_TEXT, "operator request", "line1"),
        (EventKind.THINKING, "private reasoning", "line2"),
        (EventKind.ASSISTANT_TEXT, "assistant answer", "line4"),
        (EventKind.THINKING, "supporting reasoning", "line4"),
    ]


def test_parse_since_until_use_stable_pre_slice_line_markers(tmp_path):
    path = _write_reasonix_fixture(tmp_path)
    config = ExtractConfig(
        include_thinking=True, since_marker="line1", until_marker="line4"
    )
    events = reasonix.parse(path, str(path), config)
    assert [(event.kind, event.marker) for event in events] == [
        (EventKind.THINKING, "line2"),
        (EventKind.ASSISTANT_TEXT, "line4"),
        (EventKind.THINKING, "line4"),
    ]

    second_hop = reasonix.parse(
        path,
        str(path),
        ExtractConfig(include_thinking=True, since_marker="line4"),
    )
    assert second_hop == []


@pytest.mark.parametrize("field", ["since_marker", "until_marker"])
def test_parse_reports_unknown_since_or_until_markers(tmp_path, field):
    path = _write_reasonix_fixture(tmp_path)
    with pytest.raises(ValueError, match="Reasonix line marker"):
        reasonix.parse(path, str(path), ExtractConfig(**{field: "line999"}))


def test_lossless_keeps_text_and_reasoning_but_not_system_or_tool_records(tmp_path):
    path = _write_reasonix_fixture(tmp_path)
    rendered = dump_reasonix(path)
    assert "operator request" in rendered
    assert "private reasoning" in rendered
    assert "assistant answer" in rendered
    assert "supporting reasoning" in rendered
    assert "You are Reasonix" not in rendered
    assert "tool output must not become operator text" not in rendered
    assert "tool_calls" not in rendered
    assert "===[line1 |  | USER]===" in rendered
    assert "===[line2 |  | THINKING]===" in rendered
    assert "<!-- nyxloom-extract: format=reasonix marker=line4 -->" in rendered


def test_lossless_since_until_and_marker_errors_match_adapter_contract(tmp_path):
    path = _write_reasonix_fixture(tmp_path)
    delta = dump_reasonix(path, since_marker="line1", until_marker="line4")
    assert "operator request" not in delta
    assert "private reasoning" in delta
    assert "assistant answer" in delta
    assert "marker=line4" in delta
    with pytest.raises(ValueError, match="Reasonix line marker"):
        dump_reasonix(path, since_marker="line999")
    with pytest.raises(ValueError, match="Reasonix line marker"):
        dump_reasonix(path, until_marker="line999")


def test_reasonix_jsonl_source_follows_incrementally_for_normal_and_lossless_modes(
    tmp_path,
):
    path = tmp_path / "session.jsonl"
    initial = {"role": "system", "content": "system prompt"}
    _append(path, initial)
    offset = path.stat().st_size

    source = JsonlSource(
        path,
        "reasonix",
        offset,
        ExtractConfig(include_thinking=True),
        lossless_mode=False,
    )
    _append(
        path,
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "new thought",
            "tool_calls": [],
        },
        {"role": "tool", "content": "ignored output"},
        {"role": "user", "content": "new operator"},
    )
    arrivals = source.poll()
    assert [
        (event.kind, event.text) for arrival in arrivals for event in arrival.events
    ] == [
        (EventKind.THINKING, "new thought"),
        (EventKind.OPERATOR_TEXT, "new operator"),
    ]
    source.close()

    lossless_source = JsonlSource(
        path, "reasonix", path.stat().st_size, ExtractConfig(), lossless_mode=True
    )
    _append(path, {"role": "assistant", "content": "lossless answer", "tool_calls": []})
    lossless_arrivals = lossless_source.poll()
    assert [
        block.text for arrival in lossless_arrivals for block in arrival.blocks
    ] == ["lossless answer"]
    lossless_source.close()


def test_cli_extract_and_lossless_accept_reasonix_format_and_auto_detection(
    tmp_path, capsys
):
    path = _write_reasonix_fixture(tmp_path)
    assert cli.main(["extract-lossless", str(path)]) == 0
    auto_output = capsys.readouterr().out
    assert "format=reasonix" in auto_output
    assert "assistant answer" in auto_output

    assert (
        cli.main(
            [
                "extract",
                str(path),
                "--format",
                "reasonix",
                "--include-thinking",
                "--max-words",
                "1000",
            ]
        )
        == 0
    )
    explicit_output = capsys.readouterr().out
    assert "operator request" in explicit_output

    assert (
        cli.main(["extract-debug", str(path), "--format", "reasonix", "--no-color"])
        == 0
    )
    debug_output = capsys.readouterr().out
    assert "reasonix" in debug_output.lower()
