"""Black-box session extraction acceptance tests.

These tests invoke the public CLI module in a fresh Python process, then
compare the complete normalized JSON event stream to a checked-in golden.
Adapter unit tests remain responsible for exhaustive schema edge cases.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "session_extract_cli"
PROJECT = HERE.parent


def _source_cli_env() -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (
            str(PROJECT / "src"),
            str(PROJECT.parent / "libraries" / "cli-extended" / "src"),
            existing_pythonpath,
        )
        if part
    )
    return env


@pytest.mark.parametrize(
    ("fixture", "source_format"),
    [
        ("claude_interview_compaction.jsonl", "claude-code"),
        ("codex_interview_compaction.jsonl", "codex"),
    ],
)
def test_extract_cli_matches_complete_sanitized_session_golden(
    fixture: str, source_format: str,
) -> None:
    source = FIXTURES / fixture
    expected_path = FIXTURES / fixture.replace(".jsonl", ".expected.json")
    env = _source_cli_env()
    argv = [
        sys.executable,
        "-m",
        "nyxloom.cli_harness",
        "extract",
        str(source),
        "--format",
        source_format,
        "--profile",
        "all",
        "--json",
        "--show-tool-calls",
        "--show-tool-call-intent",
        "--show-compaction-content",
    ]
    completed = subprocess.run(
        argv,
        cwd=PROJECT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)
    # Absolute fixture paths differ between checkout locations; all event
    # content, ordering, markers, and source format remain part of the oracle.
    actual.pop("source")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual == expected


@pytest.mark.parametrize(
    ("fixture", "source_format"),
    [
        ("claude_interview_compaction.jsonl", "claude-code"),
        ("codex_interview_compaction.jsonl", "codex"),
    ],
)
def test_extract_cli_text_matches_complete_prose_golden(
    fixture: str, source_format: str,
) -> None:
    source = FIXTURES / fixture
    argv = [
        sys.executable,
        "-m",
        "nyxloom.cli_harness",
        "extract",
        str(source),
        "--format",
        source_format,
        "--profile",
        "all",
        "--show-tool-calls",
        "--show-tool-call-intent",
        "--show-compaction-content",
        "--gap-marker",
        "none",
        "--insert-blank-lines",
        "0",
        "--extract-metadata",
        "post",
        "--no-color",
    ]
    completed = subprocess.run(
        argv,
        cwd=PROJECT,
        env=_source_cli_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    metadata_start = completed.stdout.rfind("\n<!-- nyxloom-extract:")
    assert metadata_start >= 0, completed.stdout
    actual = completed.stdout[:metadata_start]
    expected_path = FIXTURES / fixture.replace(".jsonl", ".expected.txt")
    expected = expected_path.read_text(encoding="utf-8")
    assert actual == expected


def test_claude_golden_labels_answered_and_unanswered_questions() -> None:
    expected = json.loads(
        (FIXTURES / "claude_interview_compaction.expected.json").read_text(
            encoding="utf-8"
        )
    )
    qa_events = [
        event for event in expected["events"] if event["kind"] == "qa_pair"
    ]
    prompts: dict[str, list[str]] = {}
    for event in qa_events:
        prompts.setdefault(event["marker"], []).append(event["text"])
    assert any(
        "INTERVIEW: Which prompt label should be used?" in text
        for text in prompts["a1"]
    )
    first_prompt = "\n".join(prompts["a1"])
    assert "Header: Prompt" in first_prompt
    assert "- Labeled prose: Mark the displayed question." in first_prompt
    assert "- Plain prose: Keep the bare question." in first_prompt
    assert "Header: Missing answer" in first_prompt
    assert "Multiple selections are allowed." in first_prompt
    answer_text = "\n".join(prompts["u2"])
    assert "OPERATOR: Something else" in answer_text
    assert "OPERATOR: (No answer provided)" in answer_text
    assert any(
        "INTERVIEW: Which word should label the answer?" in text
        for text in prompts["a2"]
    )
    assert "Header: Answer label" in "\n".join(prompts["a2"])
    assert "- OPERATOR: Label the user's answer." in "\n".join(prompts["a2"])
    assert "OPERATOR: OPERATOR" in "\n".join(prompts["u3"])
    assert "INTERVIEW: What should the extract show if the operator has not replied yet?" in "\n".join(prompts["a5"])
    assert "OPERATOR:" not in "\n".join(prompts["a5"])

    tool_text = [
        event["text"]
        for event in expected["events"]
        if event["kind"] == "tool_call"
    ]
    assert "[tool call: Bash] Inspect the sanitized test fixture" in tool_text
    assert all("cat private.txt" not in text for text in tool_text)
    assert any(
        event["kind"] == "lifecycle_marker"
        and "[compaction: steered happened" in event["text"]
        for event in expected["events"]
    )


def test_codex_golden_keeps_linked_answers_after_progress_and_compaction() -> None:
    expected = json.loads(
        (FIXTURES / "codex_interview_compaction.expected.json").read_text(
            encoding="utf-8"
        )
    )
    qa_text = [
        event["text"]
        for event in expected["events"]
        if event["kind"] == "qa_pair"
    ]
    combined = "\n".join(qa_text)
    assert "INTERVIEW: Which output should the CLI test compare?" in combined
    assert "OPERATOR: JSON" in combined
    assert "INTERVIEW: What should a custom answer preserve?" in combined
    assert "OPERATOR: Keep the complete wording exactly as entered." in combined
    assert "INTERVIEW: Should the suite mention skipped formats?" in combined
    assert any(
        event["kind"] == "tool_call"
        and "[tool call: exec_command] Inspect the fixture without exposing its command payload"
        == event["text"]
        for event in expected["events"]
    )
    assert all(
        "cat private.txt" not in event["text"]
        for event in expected["events"]
        if event["kind"] == "tool_call"
    )
    assert any(
        event["kind"] == "lifecycle_marker"
        and "deliberately compacted" in event["text"]
        for event in expected["events"]
    )
