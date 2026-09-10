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


def _write_opencode_fixture(tmp_path: Path, n_sessions: int = 1) -> Path:
    import sqlite3

    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE session (id TEXT PRIMARY KEY, time_updated INTEGER);"
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, "
        "time_updated INTEGER, data TEXT);"
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "time_created INTEGER, time_updated INTEGER, data TEXT);"
    )
    for n in range(n_sessions):
        sid = f"s{n}"
        conn.execute("INSERT INTO session VALUES (?, ?)", (sid, 100 + n))
        conn.execute(
            "INSERT INTO message VALUES (?, ?, 1, 1, ?)",
            (f"m{n}a", sid, json.dumps({"role": "user"})),
        )
        conn.execute(
            "INSERT INTO part VALUES (?, ?, ?, 1, 1, ?)",
            (f"p{n}a", f"m{n}a", sid, json.dumps({"type": "text", "text": "please look into this"})),
        )
        conn.execute(
            "INSERT INTO message VALUES (?, ?, 2, 2, ?)",
            (f"m{n}b", sid, json.dumps({
                "role": "assistant", "modelID": "z-ai/glm-5.2", "cost": 0.001234,
                "tokens": {"input": 620, "output": 40, "reasoning": 0, "cache": {"read": 100, "write": 0}},
                "time": {"created": 2, "completed": 3},
            })),
        )
        conn.execute(
            "INSERT INTO part VALUES (?, ?, ?, 2, 2, ?)",
            (f"p{n}b", f"m{n}b", sid, json.dumps({"type": "text", "text": "Let me check."})),
        )
    conn.commit()
    conn.close()
    return db


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


def test_extract_lossless_works_for_codex_too(tmp_path, capsys):
    # codex is now a supported --lossless format (was errored-out once);
    # the genuinely-unsupported-format path is covered separately below.
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--lossless"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "hi" in out


def test_extract_format_flag_rejects_an_unregistered_adapter_name(tmp_path, capsys):
    # --format's argparse choices are exactly the three registered
    # adapters -- an unregistered name is rejected before cmd_extract (or
    # its --lossless dispatch) ever runs, whether --lossless is passed or
    # not; the "unsupported format" branch inside cmd_extract's --lossless
    # dispatch itself is therefore reachable only via a future 4th adapter
    # that gets auto-DETECTED without also being added to --format's
    # choices/the lossless dispatch -- not exercisable against today's
    # registered adapter set.
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--lossless", "--format", "does-not-exist"])
    assert exit_code == 2
    assert "invalid choice" in capsys.readouterr().err


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


def _write_claude_code_ledger_fixture(tmp_path: Path) -> Path:
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "fix the flaky test"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [
                 {"type": "text", "text": "Fixing it."},
                 {"type": "tool_use", "id": "tu1", "name": "Edit", "input": {"file_path": "/repo/t.py"}},
             ]}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:02Z",
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "tu1", "content": "edited"},
             ]}),
        _rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:03Z",
             message={"role": "assistant", "content": [
                 {"type": "text", "text": "Committing."},
                 {"type": "tool_use", "id": "tu2", "name": "Bash", "input": {"command": 'git commit -m "fix"'}},
             ]}),
        _rec(type="user", uuid="u3", timestamp="2026-01-01T00:00:04Z",
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "tu2", "content": "[main abc1234] fix"},
             ]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_extract_ledger_appends_files_and_commits_line(tmp_path, capsys):
    fp = _write_claude_code_ledger_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--ledger"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "fix the flaky test" in out
    assert "[files edited: /repo/t.py]" in out
    assert "[commits created: abc1234]" in out


def test_extract_ledger_rejects_json(tmp_path, capsys):
    fp = _write_claude_code_ledger_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--ledger", "--json"])
    assert exit_code == 1
    assert "--ledger has no JSON equivalent" in capsys.readouterr().err


def test_extract_ledger_rejects_non_claude_code_format(tmp_path, capsys):
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--ledger"])
    assert exit_code == 1
    assert "--ledger does not support 'codex'" in capsys.readouterr().err


def test_extract_debug_shows_dropped_content_as_a_gap_note(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    # max_words=1 forces select() to drop everything past the operator's
    # own turn, so a1/a2's real content shows up on the lossless-only side.
    exit_code = cli.main(["extract-debug", str(fp), "--max-words", "1", "--no-color"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "please look into this" in out
    assert ">>> [gap:" in out
    assert "lossless block" in out
    assert "\x1b[" not in out  # --no-color: no ANSI codes at all


def test_extract_debug_color_forces_ansi_codes_even_when_piped(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract-debug", str(fp), "--max-words", "1", "--color"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "\x1b[36m" in out  # cyan gap note, forced on despite capsys not being a tty


def test_extract_debug_rejects_unregistered_format(tmp_path, capsys):
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["extract-debug", str(fp), "--format", "does-not-exist"])
    assert exit_code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_extract_debug_works_for_codex_too(tmp_path, capsys):
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["extract-debug", str(fp)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "hi" in out


def test_extract_since_and_since_file_are_mutually_exclusive(tmp_path, capsys):
    # cli.main() catches argparse's SystemExit itself and converts it to a
    # plain return code (see main()'s parse_args try/except) -- it never
    # propagates as a raised SystemExit to the caller.
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--since", "u1", "--since-file", str(fp)])
    assert exit_code == 2
    assert "not allowed with argument --since" in capsys.readouterr().err


def test_session_stats_condensed_view_by_default(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["session-stats", str(fp)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "trigger text" in out  # condensed view's header row
    assert "blocks total)" in out


def test_session_stats_detailed_is_csv_with_one_row_per_call(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["session-stats", str(fp), "--detailed"])
    out = capsys.readouterr().out
    assert exit_code == 0
    lines = out.strip().splitlines()
    assert lines[0].startswith("marker,timestamp")
    assert "please look into this" in out


def test_session_stats_json_output(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["session-stats", str(fp), "--json"])
    out = capsys.readouterr().out
    assert exit_code == 0
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed  # at least one block


def test_session_stats_works_for_codex_too(tmp_path, capsys):
    # codex is now a supported session-stats format (was errored-out once).
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["session-stats", str(fp)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "blocks total)" in out


def test_extract_max_lifecycle_markers_walks_past_a_compaction(tmp_path, capsys):
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "before the boundary"}),
        _rec(type="system", subtype="compact_boundary", uuid="lc1",
             timestamp="2026-01-01T00:00:01Z", compactMetadata={}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:02Z",
             message={"role": "user", "content": "after the boundary"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    default = cli.main(["extract", str(fp)])
    assert default == 0
    assert "before the boundary" not in capsys.readouterr().out

    past_boundary = cli.main(["extract", str(fp), "--max-lifecycle-markers", "-1"])
    assert past_boundary == 0
    assert "before the boundary" in capsys.readouterr().out


def test_extract_profile_supplies_defaults_for_the_selection_knobs(tmp_path, capsys):
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "before the boundary"}),
        _rec(type="system", subtype="compact_boundary", uuid="lc1",
             timestamp="2026-01-01T00:00:01Z", compactMetadata={}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:02Z",
             message={"role": "user", "content": "after the boundary"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    # "manual_fresh"'s max_lifecycle_markers=-1 default applies with no
    # --max-lifecycle-markers flag at all.
    exit_code = cli.main(["extract", str(fp), "--profile", "manual_fresh"])
    assert exit_code == 0
    assert "before the boundary" in capsys.readouterr().out

    # "default" (or no --profile) keeps today's hard-stop behavior.
    exit_code = cli.main(["extract", str(fp), "--profile", "default"])
    assert exit_code == 0
    assert "before the boundary" not in capsys.readouterr().out


def test_extract_max_words_independently_overrides_a_profiles_own_default(tmp_path, capsys):
    # --max-words is a separate axis from --profile (operator request,
    # 2026-09-10): passing it alongside --profile overrides only the
    # target-length knob, leaving the profile's other knobs (here,
    # max_lifecycle_markers=-1) intact.
    records = [
        _rec(type="assistant", uuid="old0", timestamp="2026-01-01T00:00:00Z",
             message={"role": "assistant", "content": [
                 {"type": "text", "text": "Found it -- a genuinely long root-cause finding worth keeping."},
             ]}),
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "user", "content": "before the boundary"}),
        _rec(type="system", subtype="compact_boundary", uuid="lc1",
             timestamp="2026-01-01T00:00:02Z", compactMetadata={}),
        _rec(type="user", uuid="u2", timestamp="2026-01-01T00:00:03Z",
             message={"role": "user", "content": "after the boundary"}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    # manual_fresh's own 8000-word default comfortably reaches old0.
    exit_code = cli.main(["extract", str(fp), "--profile", "manual_fresh"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Found it" in out

    # Overridden down to a 5-word budget: still walks past the marker
    # (manual_fresh's max_lifecycle_markers=-1 is untouched)...
    exit_code = cli.main(["extract", str(fp), "--profile", "manual_fresh", "--max-words", "5"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "before the boundary" in out
    # ...but the tighter budget now stops the walk before reaching old0.
    assert "Found it" not in out


def test_extract_since_file_format_mismatch_errors_cleanly(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"format": "codex", "events": [], "last_marker": "5"}), encoding="utf-8")

    exit_code = cli.main(["extract", str(fp), "--since-file", str(prior), "--format", "claude-code"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "codex" in captured.err
    assert "claude-code" in captured.err


def test_extract_lossless_opencode_single_session_needs_no_session_flag(tmp_path, capsys):
    db = _write_opencode_fixture(tmp_path, n_sessions=1)
    exit_code = cli.main(["extract", str(db), "--lossless"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "please look into this" in out
    assert "Let me check." in out


def test_extract_lossless_opencode_multi_session_requires_session_flag(tmp_path, capsys):
    db = _write_opencode_fixture(tmp_path, n_sessions=2)
    exit_code = cli.main(["extract", str(db), "--lossless"])
    assert exit_code == 1
    assert "2 opencode sessions" in capsys.readouterr().err

    exit_code = cli.main(["extract", str(db), "--lossless", "--session", "s0"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "please look into this" in out


def test_session_stats_opencode_needs_session_flag_when_ambiguous(tmp_path, capsys):
    db = _write_opencode_fixture(tmp_path, n_sessions=2)
    exit_code = cli.main(["session-stats", str(db)])
    assert exit_code == 1
    assert "2 opencode sessions" in capsys.readouterr().err

    exit_code = cli.main(["session-stats", str(db), "--session", "s0", "--detailed"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "cost_usd" in out.splitlines()[0]
    assert "0.001234" in out
