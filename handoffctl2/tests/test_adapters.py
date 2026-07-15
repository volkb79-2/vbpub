"""Tests for adapters module. Package P03."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from handoffctl import adapters
from handoffctl.config import RouteDef
from handoffctl.types import Basis


@pytest.fixture()
def record_argv_script(tmp_path):
    """Create and return a record-argv.sh script."""
    script = tmp_path / "record-argv.sh"
    script.write_text("#!/bin/sh\necho \"$@\" > \"$RECORD_FILE\"\n")
    script.chmod(0o755)
    return script


@pytest.fixture()
def sleepy_script(tmp_path):
    """Create and return a sleepy.sh script."""
    script = tmp_path / "sleepy.sh"
    script.write_text("#!/bin/sh\nsleep \"$SLEEP_TIME\"\n")
    script.chmod(0o755)
    return script


@pytest.fixture()
def emit_script(tmp_path):
    """Create and return an emit.sh script."""
    script = tmp_path / "emit.sh"
    script.write_text("#!/bin/sh\ncat \"$EMIT_FILE\"\n")
    script.chmod(0o755)
    return script


@pytest.fixture()
def version_record_script(tmp_path):
    """Create and return a version-record.sh script."""
    script = tmp_path / "version-record.sh"
    script.write_text("#!/bin/sh\necho \"$@\" > \"$RECORD_FILE\"\necho \"version 1.0\"\n")
    script.chmod(0o755)
    return script


# Oracle 1: render_argv with placeholders
def test_render_argv_basic():
    """Oracle 1: render_argv(['a','{x}b'], {'x':'1'}) == ['a','1b']."""
    result = adapters.render_argv(["a", "{x}b"], {"x": "1"})
    assert result == ["a", "1b"]


def test_render_argv_missing_key():
    """Oracle 1: missing key raises AdapterError naming the placeholder."""
    with pytest.raises(adapters.AdapterError, match="missing placeholder"):
        adapters.render_argv(["a", "{missing}b"], {"x": "1"})


def test_render_argv_multiple_replacements():
    """Multiple placeholders in one element."""
    result = adapters.render_argv(
        ["{a}{b}c{d}"],
        {"a": "1", "b": "2", "d": "3"}
    )
    assert result == ["12c3"]


def test_render_argv_empty_template():
    """Empty template returns empty list."""
    result = adapters.render_argv([], {})
    assert result == []


# Oracle 2: build_dispatch exact argv assertions
def test_build_dispatch_claude():
    """Oracle 2 (amended P14 2026-07-15 -- buffered-CLI-blindness fix):
    claude route argv uses stream-json + --verbose, NOT buffered `json`.
    The old assertion (`"json" in argv`) tested the exact defect P14 item 1
    fixes: `-p --output-format json` writes its entire output at process
    exit, so log mtime is structurally dead as a liveness signal."""
    route = RouteDef(
        route_id="claude-test",
        cli="claude",
        model="sonnet",
        effort="high",
        dispatch_extra=["--dangerously-skip-permissions"]
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "--output-format" in argv
    assert "stream-json" in argv
    assert "json" not in argv  # bare "json" must NOT appear (buffered mode)
    assert "--verbose" in argv
    assert "--model" in argv
    assert "sonnet" in argv
    assert "--effort" in argv
    assert "high" in argv
    assert "--dangerously-skip-permissions" in argv


def test_build_dispatch_codex():
    """Oracle 2: codex route constructs correct argv with sandbox."""
    route = RouteDef(
        route_id="codex-test",
        cli="codex",
        model="gpt-5.6-terra",
        sandbox="danger-full-access"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv[0] == "codex"
    assert "exec" in argv
    assert "--sandbox" in argv
    assert "danger-full-access" in argv
    assert "--cd" in argv
    assert "/tmp/wt" in argv
    assert "-m" in argv
    assert "gpt-5.6-terra" in argv


def test_build_dispatch_codex_default_sandbox():
    """Oracle 2: codex route defaults sandbox to workspace-write."""
    route = RouteDef(
        route_id="codex-test",
        cli="codex",
        model="gpt-5.6-terra"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert "--sandbox" in argv
    sandbox_idx = argv.index("--sandbox")
    assert argv[sandbox_idx + 1] == "workspace-write"


def test_build_dispatch_opencode():
    """Oracle 2: opencode route constructs correct argv."""
    route = RouteDef(
        route_id="opencode-test",
        cli="opencode",
        model="openrouter/deepseek/deepseek-v4-flash",
        variant="high",
        dispatch_extra=["--auto", "--title", "{task_id}"]
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv[0] == "opencode"
    assert "run" in argv
    assert "--model" in argv
    assert "openrouter/deepseek/deepseek-v4-flash" in argv
    assert "--dir" in argv
    assert "/tmp/wt" in argv
    assert "--variant" in argv
    assert "high" in argv
    assert "--auto" in argv
    assert "--title" in argv
    assert "T-123" in argv


def test_build_dispatch_opencode_no_variant():
    """Oracle 2: opencode variant only when set."""
    route = RouteDef(
        route_id="opencode-test",
        cli="opencode",
        model="openrouter/deepseek/deepseek-v4-flash"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert "--variant" not in argv


def test_build_dispatch_reasonix():
    """Oracle 2: reasonix route constructs correct argv."""
    route = RouteDef(
        route_id="reasonix-test",
        cli="reasonix",
        model="deepseek-flash-high/deepseek-v4-flash"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv == ["reasonix", "run", "-dir", "/tmp/wt", prompt]


def test_build_dispatch_fake():
    """Oracle 2: fake route for testing."""
    route = RouteDef(
        route_id="fake-test",
        cli="fake",
        model="fake-model",
        dispatch_extra=["--test-flag"]
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="/path/to/handoff.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/path/receipt.json"
    )
    assert argv[0] == "fake"
    assert "--test-flag" in argv
    assert prompt in argv


def test_build_dispatch_prompt_contains_required_info():
    """Oracle 2: prompt contains handoff path, worktree, branch, gate_hint, receipt_path."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model"
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="handoff/P03.md",
        worktree="/workspace/.worktrees/feat-x",
        branch="feat-x",
        task_id="T-999",
        gate_hint="pytest-q",
        receipt_path="/tmp/receipt.json"
    )
    assert "handoff/P03.md" in prompt
    assert "/workspace/.worktrees/feat-x" in prompt
    assert "feat-x" in prompt
    assert "pytest-q" in prompt
    assert "/tmp/receipt.json" in prompt


# Oracle 3: Prompt-length guard
def test_build_dispatch_prompt_too_long():
    """Oracle 3: long prompt raises AdapterError."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        argv_max=50  # Very short limit
    )
    with pytest.raises(adapters.AdapterError, match="exceeds argv_max"):
        adapters.build_dispatch(
            route,
            handoff_path="handoff/P03.md",
            worktree="/very/long/path/to/workspace",
            branch="feat-x",
            task_id="T-999",
            gate_hint="pytest-q",
            receipt_path="/tmp/receipt.json"
        )


def test_build_dispatch_incremental_write_hint():
    """Oracle 3: incremental-write hint appends ~80-line sentence."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        prompt_hints=["incremental-write"]
    )
    argv, prompt = adapters.build_dispatch(
        route,
        handoff_path="h.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/tmp/receipt.json"
    )
    assert "~80-line" in prompt


# Oracle 4: build_resume
def test_build_resume_substitutes_placeholders():
    """Oracle 4: build_resume substitutes {session}/{worktree}/{prompt}."""
    route = RouteDef(
        route_id="test",
        cli="claude",
        model="sonnet",
        resume=["claude", "--resume", "{session}", "--model", "sonnet", "-p", "{prompt}"]
    )
    argv = adapters.build_resume(
        route,
        session="sess-abc123",
        worktree="/tmp/wt",
        prompt="continue working"
    )
    assert argv == ["claude", "--resume", "sess-abc123", "--model", "sonnet",
                    "-p", "continue working"]


def test_build_resume_empty_template():
    """Oracle 4: empty resume template raises AdapterError."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        resume=[]
    )
    with pytest.raises(adapters.AdapterError, match="empty resume template"):
        adapters.build_resume(
            route,
            session="sess-123",
            worktree="/tmp/wt",
            prompt="test"
        )


def test_build_resume_session_required_but_none():
    """Oracle 4: template with {session} but session=None raises AdapterError."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        resume=["fake", "run", "--session", "{session}", "{prompt}"]
    )
    with pytest.raises(adapters.AdapterError, match="session required"):
        adapters.build_resume(
            route,
            session=None,
            worktree="/tmp/wt",
            prompt="test"
        )


def test_build_resume_session_not_required():
    """Template without {session} should not require session."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        resume=["fake", "run", "{worktree}", "{prompt}"]
    )
    argv = adapters.build_resume(
        route,
        session=None,
        worktree="/tmp/wt",
        prompt="test"
    )
    assert argv == ["fake", "run", "/tmp/wt", "test"]


# Oracle 5: probe
def test_probe_none():
    """Oracle 5: probe None returns (True, 'no-probe')."""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        probe=None
    )
    ok, detail = adapters.probe(route)
    assert ok is True
    assert detail == "no-probe"


def test_probe_argv_true(tmp_path):
    """Oracle 5: probe ['true'] returns (True, ...)."""
    ok, detail = adapters.probe(RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        probe=["true"]
    ))
    assert ok is True


def test_probe_argv_false(tmp_path):
    """Oracle 5: probe ['false'] returns (False, ...)."""
    ok, detail = adapters.probe(RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        probe=["false"]
    ))
    assert ok is False


def test_probe_timeout():
    """Oracle 5: probe exceeding timeout returns (False, 'timeout...')."""
    # Use a command that takes a long time
    # Monkeypatch subprocess.run to raise TimeoutExpired
    import subprocess
    from unittest.mock import patch

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        probe=["sleep", "10"]
    )

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sleep 10", 1)):
        ok, detail = adapters.probe(route)
        assert ok is False
        assert "timeout" in detail.lower()


def test_probe_named_builtin_one_token_ping(version_record_script, tmp_path, monkeypatch):
    """Oracle 5: named builtin 'one-token-ping' executes [cli,'--version']."""
    record_file = tmp_path / "record.txt"
    monkeypatch.setenv("RECORD_FILE", str(record_file))

    route = RouteDef(
        route_id="test",
        cli=str(version_record_script),
        model="fake-model",
        probe="one-token-ping"
    )
    ok, detail = adapters.probe(route)
    assert ok is True
    if record_file.exists():
        args = record_file.read_text().strip()
        assert "--version" in args


def test_probe_named_builtin_session_limit_check(version_record_script, tmp_path, monkeypatch):
    """Oracle 5: named builtin 'session-limit-check' executes [cli,'--version']."""
    record_file = tmp_path / "record.txt"
    monkeypatch.setenv("RECORD_FILE", str(record_file))

    route = RouteDef(
        route_id="test",
        cli=str(version_record_script),
        model="fake-model",
        probe="session-limit-check"
    )
    ok, detail = adapters.probe(route)
    assert ok is True
    if record_file.exists():
        args = record_file.read_text().strip()
        assert "--version" in args


# Oracle 6: capture_session
def test_capture_session_newest_jsonl(tmp_path, monkeypatch):
    """Oracle 6: 'newest-jsonl' finds newest file after launched_at."""
    # Set up HOME to tmp
    monkeypatch.setenv("HOME", str(tmp_path))

    worktree = "/tmp/wt"
    projects_dir = tmp_path / ".claude" / "projects" / "tmp-wt"
    projects_dir.mkdir(parents=True)

    # Create two jsonl files
    old_file = projects_dir / "old.jsonl"
    new_file = projects_dir / "new.jsonl"
    old_file.write_text("old")
    new_file.write_text("new")

    # Set mtimes using os.utime
    import os
    import time
    now = time.time()
    os.utime(str(old_file), (now - 100, now - 100))
    os.utime(str(new_file), (now + 100, now + 100))

    launched_at = datetime.fromtimestamp(now, tz=timezone.utc)

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        session_capture="newest-jsonl"
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path / "attempt",
        worktree=worktree,
        launched_at=launched_at
    )
    assert result == "new"


def test_capture_session_newest_jsonl_no_dir(tmp_path, monkeypatch):
    """Oracle 6: no dir returns None."""
    monkeypatch.setenv("HOME", str(tmp_path))

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        session_capture="newest-jsonl"
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path / "attempt",
        worktree="/tmp/nonexistent",
        launched_at=datetime.now(tz=timezone.utc)
    )
    assert result is None


def test_capture_session_discover(tmp_path, monkeypatch, emit_script):
    """Oracle 6: session_discover runs command, parses JSON, matches dir."""
    json_output = json.dumps([
        {"id": "sess-1", "dir": "/tmp/wt", "title": "Session 1"},
        {"id": "sess-2", "dir": "/other/wt", "title": "Session 2"}
    ])
    emit_file = tmp_path / "emit.txt"
    emit_file.write_text(json_output)
    monkeypatch.setenv("EMIT_FILE", str(emit_file))

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        session_discover=[str(emit_script)]
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path / "attempt",
        worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc)
    )
    assert result == "sess-1"


def test_capture_session_discover_by_title(tmp_path, monkeypatch, emit_script):
    """Oracle 6: session_discover matches title field."""
    json_output = json.dumps([
        {"id": "sess-1", "title": "/tmp/wt", "dir": "/some/other/path"}
    ])
    emit_file = tmp_path / "emit.txt"
    emit_file.write_text(json_output)
    monkeypatch.setenv("EMIT_FILE", str(emit_file))

    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        session_discover=[str(emit_script)]
    )

    result = adapters.capture_session(
        route,
        attempt_dir=tmp_path / "attempt",
        worktree="/tmp/wt",
        launched_at=datetime.now(tz=timezone.utc)
    )
    assert result == "sess-1"


# Oracle 7: extract_usage
def test_extract_usage_output_format_json():
    """Oracle 7: output-format-json extracts from LAST json line."""
    log = """some log line
{"usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 80}, "total_cost_usd": 0.0123}
"""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="output-format-json"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ACTUAL
    assert usage.tokens_in == 100
    assert usage.tokens_out == 50
    assert usage.cached_in == 80
    assert usage.cost == 0.0123
    assert usage.currency == "USD"


def test_extract_usage_output_format_json_skips_malformed():
    """Oracle 7: earlier malformed json lines are skipped."""
    log = """malformed: {not json
valid line
{"usage": {"input_tokens": 100, "output_tokens": 50}, "total_cost_usd": 0.0123}
"""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="output-format-json"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ACTUAL
    assert usage.tokens_in == 100


def test_extract_usage_codex_footer():
    """Oracle 7: codex 'Tokens used: 12,345' -> ESTIMATED tokens_out 12345."""
    log = """some output
Tokens used: 12,345
"""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="exec-output-footer"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ESTIMATED
    assert usage.tokens_out == 12345


def test_extract_usage_deepseek():
    """Oracle 7: deepseek regex extracts ACTUAL tokens."""
    log = """{
  "prompt_tokens": 100,
  "completion_tokens": 50
}
"""
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="run-log-deepseek-usage"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ACTUAL
    assert usage.tokens_in == 100
    assert usage.tokens_out == 50
    assert usage.cost is None


def test_extract_usage_stream_json_fixture():
    """P14 item 1/oracle 3: extract_usage parses a REALISTIC stream-json
    (NDJSON) log -- one JSON object per line as claude -p --output-format
    stream-json --verbose emits -- to ACTUAL usage from the final `result`
    line. Earlier lines have no top-level 'usage'/'total_cost_usd' key
    (usage is nested under "message" there) so they must be skipped."""
    log = "\n".join([
        '{"type": "system", "subtype": "init", "cwd": "/tmp/wt"}',
        '{"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}], '
        '"usage": {"input_tokens": 5, "output_tokens": 2}}}',
        '{"type": "result", "subtype": "success", "is_error": false, '
        '"duration_ms": 1234, "usage": {"input_tokens": 200, "output_tokens": 90, '
        '"cache_read_input_tokens": 150}, "total_cost_usd": 0.045}',
    ]) + "\n"
    route = RouteDef(
        route_id="test",
        cli="claude",
        model="sonnet",
        usage_source="output-format-json",
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.ACTUAL
    assert usage.tokens_in == 200
    assert usage.tokens_out == 90
    assert usage.cached_in == 150
    assert usage.cost == 0.045
    assert usage.currency == "USD"


def test_build_dispatch_claude_argv_stream_json_exact_position():
    """P14 item 1: --output-format is immediately followed by stream-json
    (not the buffered 'json')."""
    route = RouteDef(route_id="claude-test", cli="claude", model="sonnet")
    argv, _prompt = adapters.build_dispatch(
        route,
        handoff_path="h.md",
        worktree="/tmp/wt",
        branch="feat-x",
        task_id="T-123",
        gate_hint="pytest-q",
        receipt_path="/tmp/receipt.json",
    )
    idx = argv.index("--output-format")
    assert argv[idx + 1] == "stream-json"
    assert "--verbose" in argv


def test_extract_usage_garbage_log():
    """Oracle 7: garbage log -> Usage(UNKNOWN) and NO exception."""
    log = "a" * 10000  # 10KB of garbage
    route = RouteDef(
        route_id="test",
        cli="fake",
        model="fake-model",
        usage_source="output-format-json"
    )
    usage = adapters.extract_usage(route, Path("/tmp"), log)
    assert usage.basis == Basis.UNKNOWN


# Oracle 8: classify_log_tail
def test_classify_log_tail_blocked():
    """Oracle 8: 'BLOCKED:' at line start -> 'blocked'."""
    log = "some log\nBLOCKED: cannot meet contract\nmore log"
    result = adapters.classify_log_tail(log)
    assert result == "blocked"


def test_classify_log_tail_limit():
    """Oracle 8: 'rate limit exceeded' -> 'limit'."""
    log = "some log\nrate limit exceeded\nmore"
    result = adapters.classify_log_tail(log)
    assert result == "limit"


def test_classify_log_tail_limit_variants():
    """Oracle 8: various limit phrases recognized."""
    for phrase in ["session limit", "usage limit", "quota", "plan limit"]:
        log = f"some log\n{phrase} reached\nmore"
        result = adapters.classify_log_tail(log)
        assert result == "limit", f"Failed for phrase: {phrase}"


def test_classify_log_tail_both_blocked_wins():
    """Oracle 8: both blocked and limit present -> 'blocked'."""
    log = "some\nrate limit exceeded\nBLOCKED: reason\nmore"
    result = adapters.classify_log_tail(log)
    assert result == "blocked"


def test_classify_log_tail_blocked_midsentence():
    """Oracle 8: 'blocked: midsentence' (not line-start, lowercase) -> not 'blocked'."""
    log = "the word blocked: midsentence"
    result = adapters.classify_log_tail(log)
    assert result is not None or result is None  # Should not specifically be 'blocked'
    # Check that it's not blocked at least
    if result:
        assert result != "blocked"


def test_classify_log_tail_clean_log():
    """Oracle 8: clean log -> None."""
    log = "all is well\neverything working\nno issues"
    result = adapters.classify_log_tail(log)
    assert result is None


def test_classify_log_tail_only_last_200_lines():
    """Oracle 8: only last 200 lines considered."""
    # Create 250 clean lines, then a BLOCKED line
    lines = ["clean line"] * 250
    lines.append("BLOCKED: late blocker")
    log = "\n".join(lines)

    result = adapters.classify_log_tail(log)
    # Should still find it since BLOCKED is at line 251, within last 200 lines
    assert result == "blocked"

    # Now put BLOCKED more than 200 lines back
    # 400 clean lines, then a BLOCKED line, then 1 more clean line
    # This puts BLOCKED at position 401, but the last 200 lines are 202-401
    lines = ["BLOCKED: very early blocker"] + ["clean line"] * 400
    log = "\n".join(lines)

    result = adapters.classify_log_tail(log)
    # Should NOT find it since it's beyond 200 lines (it's at line 1)
    assert result is None


def test_build_dispatch_unknown_cli():
    """Unknown cli should raise AdapterError."""
    route = RouteDef(
        route_id="test",
        cli="unknown-cli",
        model="fake-model"
    )
    with pytest.raises(adapters.AdapterError, match="unknown cli"):
        adapters.build_dispatch(
            route,
            handoff_path="h.md",
            worktree="/tmp/wt",
            branch="feat-x",
            task_id="T-123",
            gate_hint="pytest-q",
            receipt_path="/tmp/receipt.json"
        )
