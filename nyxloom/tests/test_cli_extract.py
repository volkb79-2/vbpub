"""CLI-level tests for `nyxloom extract`, exercising cli.main() end to end
(argparse -> cmd_extract) rather than calling session_extract functions
directly -- covers the --lossless/--until wiring cmd_extract itself owns.
"""

from __future__ import annotations

import json
from pathlib import Path

from nyxloom import cli


def _rec(**kw):
    base = {"parentUuid": None, "sessionId": "s1", "isSidechain": False}
    base.update(kw)
    return base


def _write_claude_code_fixture(tmp_path: Path) -> Path:
    records = [
        _rec(type="mode", mode="normal"),
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "please look into this"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [
                 {"type": "text", "text": "Let me check."},
                 {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}},
             ]}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:02Z",
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "tu1", "content": "file1\nfile2"},
             ]}),
        _rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:03Z",
             message={"role": "assistant", "content": [{"type": "text", "text": (
                 "## Status\n\nDone -- everything landed, main clean at `abc123`."
             )}]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def _write_codex_fixture(tmp_path: Path) -> Path:
    lines = [
        {"timestamp": "2026-01-01T00:00:00Z", "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": "abc", "cli_version": "0.149.0"}},
        {"timestamp": "2026-01-01T00:00:01Z", "ordinal": 1, "type": "event_msg",
         "payload": {"type": "user_message", "message": "hi"}},
    ]
    fp = tmp_path / "rollout.jsonl"
    fp.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return fp


def test_extract_lossless_dumps_prose_and_drops_tool_calls(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--lossless"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "please look into this" in out
    assert "Let me check." in out
    assert "Status" in out
    # tool_use/tool_result content never appears in a lossless dump
    assert "Bash" not in out
    assert "file1" not in out


def test_extract_lossless_errors_for_non_claude_code_format(tmp_path, capsys):
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--lossless"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "codex" in captured.err
    assert "claude-code" in captured.err


def test_extract_until_bounds_the_walk(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--until", "a1"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "please look into this" in out  # operator text is always kept
    assert "Status" not in out  # a2 comes after the --until marker


def test_extract_default_run_produces_delimited_text(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "please look into this" in out
    assert "nyxloom-extract: format=claude-code" in out


def test_extract_since_and_since_file_are_mutually_exclusive(tmp_path, capsys):
    # cli.main() catches argparse's SystemExit itself and converts it to a
    # plain return code (see main()'s parse_args try/except) -- it never
    # propagates as a raised SystemExit to the caller.
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--since", "u1", "--since-file", str(fp)])
    assert exit_code == 2
    assert "not allowed with argument --since" in capsys.readouterr().err


def test_extract_since_file_format_mismatch_errors_cleanly(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"format": "codex", "events": [], "last_marker": "5"}), encoding="utf-8")

    exit_code = cli.main(["extract", str(fp), "--since-file", str(prior), "--format", "claude-code"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "codex" in captured.err
    assert "claude-code" in captured.err
