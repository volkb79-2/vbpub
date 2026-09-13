"""CLI-level tests for `nyxloom extract` and `nyxloom extract-lossless`,
exercising cli.main() end to end (argparse -> cmd_extract/
cmd_extract_lossless) rather than calling session_extract functions
directly -- covers the --until wiring and the extract-lossless dispatch
these two commands own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom import cli
from nyxloom.session_extract.locate import escape_cwd


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
    exit_code = cli.main(["extract-lossless", str(fp)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "please look into this" in out
    assert "Let me check." in out
    assert "Status" in out
    # tool_use/tool_result content never appears in a lossless dump
    assert "Bash" not in out
    assert "file1" not in out


def test_extract_lossless_works_for_codex_too(tmp_path, capsys):
    # codex is now a supported extract-lossless format (was errored-out
    # once); the genuinely-unsupported-format path is covered separately
    # below.
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["extract-lossless", str(fp)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "hi" in out


def test_extract_lossless_format_flag_rejects_an_unregistered_adapter_name(tmp_path, capsys):
    # --format's argparse choices are exactly the three registered
    # adapters -- an unregistered name is rejected before cmd_extract_lossless
    # ever runs; the "unsupported format" branch inside cmd_extract_lossless
    # itself is therefore reachable only via a future 4th adapter that gets
    # auto-DETECTED without also being added to --format's choices/the
    # lossless dispatch -- not exercisable against today's registered
    # adapter set.
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["extract-lossless", str(fp), "--format", "does-not-exist"])
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


def _write_subagent_fixture(tmp_path: Path) -> Path:
    # Mirrors a real dispatched Agent-tool subagent's own transcript: every
    # record carries isSidechain=true (operator-verified against a live one).
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z", isSidechain=True,
             message={"role": "user", "content": "research question dispatched by the controller"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z", isSidechain=True,
             message={"role": "assistant", "content": [{"type": "text", "text": (
                 "## Findings\n\nHere is the researched answer with real detail."
             )}]}),
    ]
    fp = tmp_path / "agent-abc123.output"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_extract_on_a_subagent_transcript_needs_no_flag(tmp_path, capsys):
    # 2026-09-11 redesign: no --include-sidechain flag exists on this shared
    # CLI surface (operator critique: adapter-specific vocabulary doesn't
    # belong there). Targeting a subagent's own transcript is just pointing
    # path at that file -- claude_code.py auto-detects it has no primary
    # thread of its own and extracts its (wholly-sidechain) content anyway.
    fp = _write_subagent_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "research question dispatched by the controller" in out
    assert "Findings" in out


def test_extract_auto_detects_a_non_jsonl_suffixed_subagent_transcript(tmp_path, capsys):
    # 2026-09-11 fix: format auto-detection no longer gates on the ".jsonl"
    # suffix -- a real Agent-tool subagent's output_file ends in ".output".
    fp = _write_subagent_fixture(tmp_path)
    exit_code = cli.main(["extract-lossless", str(fp)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "research question dispatched by the controller" in out


def _write_claude_code_ledger_fixture(tmp_path: Path) -> Path:
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "fix the flaky test"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [
                 {"type": "text", "text": "Fixing it."},
                 {"type": "tool_use", "id": "tu1", "name": "Edit", "input": {"file_path": str(tmp_path / "t.py")}},
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


def test_extract_ledger_appends_files_and_commits_line(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fp = _write_claude_code_ledger_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--ledger"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "fix the flaky test" in out
    assert "[files edited: t.py]" in out
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


# --- --insert-blank-lines / --gap-marker / --min-gap-records / ------------
# --- --show-gap-source CLI wiring (2026-09-11 operator direction) ---------

def _write_gap_fixture(tmp_path: Path) -> Path:
    # A checkpoint, then several short/no-finding-signal records dropped by
    # the default long_comment_chars bar, then a second checkpoint -- forces
    # a real gap_after annotation between the two kept checkpoints.
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "please look into this"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [{"type": "text", "text": (
                 "## Status\n\nDone -- everything landed, main clean at `abc123`."
             )}]}),
    ]
    for i in range(5):
        records.append(_rec(type="assistant", uuid=f"drop{i}", timestamp=f"2026-01-01T00:00:{2+i:02d}Z",
                             message={"role": "assistant", "content": [{"type": "text", "text": "ok"}]}))
    records.append(_rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:10Z",
                         message={"role": "assistant", "content": [{"type": "text", "text": (
                             "## Second status\n\nAlso done -- second checkpoint clean at `def456`."
                         )}]}))
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_extract_gap_marker_inline_folds_gap_into_the_separator(tmp_path, capsys):
    fp = _write_gap_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--gap-marker", "inline"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "--- [gap:" in out


def test_extract_insert_blank_lines_zero_is_tight(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--insert-blank-lines", "0"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "\n\n---\n\n" not in out
    assert "\n---\n" in out


def test_extract_min_gap_records_lowers_the_threshold(tmp_path, capsys):
    fp = _write_gap_fixture(tmp_path)
    # default threshold (3) would already catch this fixture's 5-record gap;
    # confirm the flag reaches ExtractConfig by using a wider gate and a
    # threshold high enough to suppress it, then lowering the threshold back
    exit_code = cli.main(["extract", str(fp), "--min-gap-records", "100"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "[gap:" not in out

    exit_code = cli.main(["extract", str(fp), "--min-gap-records", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "[gap:" in out


def test_extract_show_gap_source_names_the_marker(tmp_path, capsys):
    fp = _write_gap_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--show-gap-source"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "raw log continues after marker" in out


@pytest.mark.parametrize("flag,value", [
    ("--insert-blank-lines", "0"),
    ("--gap-marker", "inline"),
    ("--min-gap-records", "1"),
    ("--task", "do the next thing"),
    ("--task-file", "/nonexistent/path/does/not/matter"),
])
def test_extract_render_only_flags_reject_json(tmp_path, capsys, flag, value):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--json", flag, value])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "only affect" in err
    assert flag in err


def test_extract_show_gap_source_rejects_json(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--json", "--show-gap-source"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "--show-gap-source" in err
    assert "only affect" in err


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


def test_extract_debug_annotates_each_dropped_block_with_a_yellow_reason(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract-debug", str(fp), "--max-words", "1", "--no-color"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "reason:" in out


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


def test_extract_debug_opencode_single_session_needs_no_session_flag(tmp_path, capsys):
    db = _write_opencode_fixture(tmp_path, n_sessions=1)
    exit_code = cli.main(["extract-debug", str(db)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "please look into this" in out


def test_extract_debug_opencode_multi_session_requires_session_flag(tmp_path, capsys):
    db = _write_opencode_fixture(tmp_path, n_sessions=2)
    exit_code = cli.main(["extract-debug", str(db)])
    assert exit_code == 1
    assert "2 opencode sessions" in capsys.readouterr().err

    exit_code = cli.main(["extract-debug", str(db), "--opencode-session", "s0"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "please look into this" in out


def test_extract_debug_opencode_no_sessions_errors(tmp_path, capsys):
    db = _write_opencode_fixture(tmp_path, n_sessions=0)
    exit_code = cli.main(["extract-debug", str(db)])
    assert exit_code == 1
    assert "no opencode sessions found" in capsys.readouterr().err


def test_extract_debug_rejects_a_format_unsupported_by_lossless(tmp_path, capsys, monkeypatch):
    # --format's own argparse choices already refuse anything outside
    # claude-code/codex/opencode, so this branch (cmd_extract_debug's own
    # "does not support {fmt!r}" fallback) is only reachable if adapter
    # auto-detection ever returns a name --format's choices haven't caught
    # up to -- a real registry/CLI drift scenario, exercised here by
    # monkeypatching detect() the way that drift would actually present.
    from nyxloom.session_extract import adapters as adapters_mod

    fp = _write_codex_fixture(tmp_path)
    fake = type("FakeAdapter", (), {"name": "does-not-exist"})()
    monkeypatch.setattr(adapters_mod, "detect", lambda path: fake)
    exit_code = cli.main(["extract-debug", str(fp)])
    assert exit_code == 1
    assert "extract-debug does not support 'does-not-exist'" in capsys.readouterr().err


def test_extract_since_and_since_file_are_mutually_exclusive(tmp_path, capsys):
    # cli.main() catches argparse's SystemExit itself and converts it to a
    # plain return code (see main()'s parse_args try/except) -- it never
    # propagates as a raised SystemExit to the caller.
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--since", "u1", "--since-file", str(fp)])
    assert exit_code == 2
    assert "not allowed with argument --since" in capsys.readouterr().err


def test_extract_report_condensed_view_by_default(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract-report", str(fp)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "trigger text" in out  # condensed view's header row
    assert "blocks total)" in out


def test_extract_report_detailed_is_csv_with_one_row_per_call(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract-report", str(fp), "--detailed"])
    out = capsys.readouterr().out
    assert exit_code == 0
    lines = out.strip().splitlines()
    assert lines[0].startswith("marker,timestamp")
    assert "please look into this" in out


def test_extract_report_json_output(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract-report", str(fp), "--json"])
    out = capsys.readouterr().out
    assert exit_code == 0
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed  # at least one block


def test_extract_report_works_for_codex_too(tmp_path, capsys):
    # codex is now a supported extract-report format (was errored-out once).
    fp = _write_codex_fixture(tmp_path)
    exit_code = cli.main(["extract-report", str(fp)])
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


def test_extract_debug_max_lifecycle_markers_walks_past_a_compaction_too(tmp_path, capsys):
    # cmd_extract_debug carries its OWN copy of the --max-lifecycle-markers
    # override (separate from cmd_extract's, same shape) -- must honor an
    # explicit value exactly the same way, not silently fall back to the
    # profile default.
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

    default = cli.main(["extract-debug", str(fp), "--no-color"])
    assert default == 0
    default_out = capsys.readouterr().out
    # "before the boundary" was dropped -- its own lossless block sits
    # AFTER the gap marker that reports it, inside the dropped span.
    assert default_out.index(">>> [gap:") < default_out.index("before the boundary")

    past_boundary = cli.main(["extract-debug", str(fp), "--max-lifecycle-markers", "-1", "--no-color"])
    assert past_boundary == 0
    past_out = capsys.readouterr().out
    # Walked past the boundary: "before the boundary" is kept, printed
    # BEFORE any gap marker (the remaining gap covers only the boundary's
    # own bookkeeping blocks, not this content).
    assert past_out.index("before the boundary") < past_out.index(">>> [gap:")


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

    # Overridden down to an 8-word budget: still walks past the marker
    # (manual_fresh's max_lifecycle_markers=-1 is untouched). 8, not a
    # smaller number, deliberately: the marker's own rendered text now
    # counts toward the budget too (select.py's 2026-09-11 fix -- see its
    # module docstring), and "after the boundary" (3 words) + the marker
    # ("[compaction: unknown happened]", 3 words) already total 6 before
    # "before the boundary" (3 more words) is even considered -- a 5-word
    # budget would stop the walk right at the marker and never reach it.
    exit_code = cli.main(["extract", str(fp), "--profile", "manual_fresh", "--max-words", "8"])
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
    exit_code = cli.main(["extract-lossless", str(db)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "please look into this" in out
    assert "Let me check." in out


def test_extract_lossless_opencode_multi_session_requires_session_flag(tmp_path, capsys):
    db = _write_opencode_fixture(tmp_path, n_sessions=2)
    exit_code = cli.main(["extract-lossless", str(db)])
    assert exit_code == 1
    assert "2 opencode sessions" in capsys.readouterr().err

    exit_code = cli.main(["extract-lossless", str(db), "--opencode-session", "s0"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "please look into this" in out


def test_extract_report_opencode_needs_session_flag_when_ambiguous(tmp_path, capsys):
    db = _write_opencode_fixture(tmp_path, n_sessions=2)
    exit_code = cli.main(["extract-report", str(db)])
    assert exit_code == 1
    assert "2 opencode sessions" in capsys.readouterr().err

    exit_code = cli.main(["extract-report", str(db), "--opencode-session", "s0", "--detailed"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "cost_usd" in out.splitlines()[0]
    assert "0.001234" in out


# -- extract-sessions (discovery: what sessions/sub-agents exist) --------


def _write_claude_code_family_fixture(tmp_path: Path) -> Path:
    """root.jsonl dispatches child1 (spawnDepth 1); child1 dispatches
    child2 (spawnDepth 2) -- the real on-disk shape confirmed this session
    against production data (E-015): a flat subagents/ dir, lineage
    resolved via toolUseId cross-reference, not isSidechain."""
    root = tmp_path / "root.jsonl"
    root.write_text("\n".join(json.dumps(r) for r in [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "please look into this"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [
                 {"type": "tool_use", "id": "tu-child1", "name": "Agent", "input": {}},
             ]}),
    ]) + "\n", encoding="utf-8")

    subagents_dir = tmp_path / "root" / "subagents"
    subagents_dir.mkdir(parents=True)

    (subagents_dir / "agent-child1.jsonl").write_text("\n".join(json.dumps(r) for r in [
        _rec(type="user", uuid="cu1", timestamp="2026-01-01T00:00:02Z", isSidechain=True,
             message={"role": "user", "content": "dispatched task"}),
        _rec(type="assistant", uuid="ca1", timestamp="2026-01-01T00:00:03Z", isSidechain=True,
             message={"role": "assistant", "content": [
                 {"type": "tool_use", "id": "tu-child2", "name": "Agent", "input": {}},
             ]}),
    ]) + "\n", encoding="utf-8")
    (subagents_dir / "agent-child1.meta.json").write_text(json.dumps({
        "agentType": "fork", "isFork": True, "description": "Child one task",
        "toolUseId": "tu-child1", "spawnDepth": 1,
    }), encoding="utf-8")

    (subagents_dir / "agent-child2.jsonl").write_text("\n".join(json.dumps(r) for r in [
        _rec(type="user", uuid="gu1", timestamp="2026-01-01T00:00:04Z", isSidechain=True,
             message={"role": "user", "content": "nested dispatched task"}),
    ]) + "\n", encoding="utf-8")
    (subagents_dir / "agent-child2.meta.json").write_text(json.dumps({
        "agentType": "fork", "isFork": True, "description": "Grandchild task",
        "toolUseId": "tu-child2", "spawnDepth": 2,
    }), encoding="utf-8")

    return root


def test_extract_sessions_claude_code_resolves_nested_lineage(tmp_path, capsys):
    root = _write_claude_code_family_fixture(tmp_path)
    exit_code = cli.main(["extract-sessions", str(root)])
    out = capsys.readouterr().out
    assert exit_code == 0

    lines = out.splitlines()
    root_idx = next(i for i, l in enumerate(lines) if "(interactive session)" in l)
    child1_idx = next(i for i, l in enumerate(lines) if "Child one task" in l)
    child2_idx = next(i for i, l in enumerate(lines) if "Grandchild task" in l)
    assert root_idx < child1_idx < child2_idx
    # child2 is nested under child1 (4-space indent = 2 levels), not under root.
    assert lines[child2_idx].startswith("    -")
    assert lines[child1_idx].startswith("  -")
    assert "spawnDepth 1" in lines[child1_idx]
    assert "spawnDepth 2" in lines[child2_idx]


def test_extract_sessions_claude_code_same_family_from_a_child_file(tmp_path, capsys):
    root = _write_claude_code_family_fixture(tmp_path)
    child2 = root.parent / "root" / "subagents" / "agent-child2.jsonl"
    exit_code = cli.main(["extract-sessions", str(child2)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "(interactive session)" in out
    assert "Child one task" in out
    assert "Grandchild task" in out


def _write_codex_family_fixture(tmp_path: Path) -> tuple[Path, Path]:
    parent = tmp_path / "rollout-parent.jsonl"
    parent.write_text("\n".join(json.dumps(r) for r in [
        {"type": "session_meta", "payload": {
            "session_id": "root-thread", "id": "root-thread",
            "thread_source": "user", "source": "exec", "cli_version": "0.154.0",
        }},
        {"type": "event_msg", "timestamp": "2026-01-01T00:00:00Z", "ordinal": 1, "payload": {
            "type": "user_message", "message": "hi",
        }},
    ]) + "\n", encoding="utf-8")

    child = tmp_path / "rollout-child.jsonl"
    child.write_text("\n".join(json.dumps(r) for r in [
        {"type": "session_meta", "payload": {
            "session_id": "root-thread", "id": "child-thread", "cli_version": "0.154.0",
            "forked_from_id": "root-thread", "thread_source": "subagent",
            "source": {"subagent": {"thread_spawn": {
                "parent_thread_id": "root-thread", "depth": 1, "agent_nickname": "Ada",
            }}},
        }},
        {"type": "event_msg", "timestamp": "2026-01-01T00:00:01Z", "ordinal": 1, "payload": {
            "type": "user_message", "message": "delegated task",
        }},
    ]) + "\n", encoding="utf-8")
    return parent, child


def test_extract_sessions_codex_finds_spawned_subagent(tmp_path, capsys):
    parent, _child = _write_codex_family_fixture(tmp_path)
    exit_code = cli.main(["extract-sessions", str(parent)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "(interactive session)" in out
    assert "Ada (depth 1)" in out
    assert "  -" in out  # child is indented under the root


def _write_opencode_family_fixture(tmp_path: Path) -> Path:
    import sqlite3

    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT, "
        "agent TEXT, time_created INTEGER, time_updated INTEGER);"
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, "
        "time_updated INTEGER, data TEXT);"
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "time_created INTEGER, time_updated INTEGER, data TEXT);"
    )
    conn.execute("INSERT INTO session VALUES ('parent', NULL, 'Parent session', 'build', 1000, 2000)")
    conn.execute("INSERT INTO session VALUES ('kid', 'parent', 'Explore subtask', 'explore', 1100, 1200)")
    conn.commit()
    conn.close()
    return db


def test_extract_sessions_opencode_shows_forked_session(tmp_path, capsys):
    db = _write_opencode_family_fixture(tmp_path)
    exit_code = cli.main(["extract-sessions", str(db)])
    out = capsys.readouterr().out
    assert exit_code == 0
    lines = out.splitlines()
    parent_idx = next(i for i, l in enumerate(lines) if "Parent session" in l)
    kid_idx = next(i for i, l in enumerate(lines) if "Explore subtask" in l)
    assert parent_idx < kid_idx
    assert lines[kid_idx].startswith("  -")  # nested under the parent


def test_extract_sessions_json_output(tmp_path, capsys):
    db = _write_opencode_family_fixture(tmp_path)
    exit_code = cli.main(["extract-sessions", str(db), "--json"])
    out = capsys.readouterr().out
    assert exit_code == 0
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    ids = {n["id"] for n in parsed}
    assert ids == {"parent", "kid"}
    kid = next(n for n in parsed if n["id"] == "kid")
    assert kid["parent_id"] == "parent"


# -- extract-sessions on a DIRECTORY of many sessions (2026-09-11, real ---
# operator repro: pointing it at a Claude Code project directory failed) --


def test_extract_sessions_directory_lists_every_top_level_session(tmp_path, capsys):
    # Two independent top-level sessions, directly in the same directory --
    # the real ~/.claude/projects/<project>/ layout (one *.jsonl per
    # session, no shared family between them).
    for name, text in [("sessionA", "first session prompt"), ("sessionB", "second session prompt")]:
        fp = tmp_path / f"{name}.jsonl"
        fp.write_text("\n".join(json.dumps(r) for r in [
            _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
                 message={"role": "user", "content": text}),
        ]) + "\n", encoding="utf-8")

    exit_code = cli.main(["extract-sessions", str(tmp_path)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert str(tmp_path / "sessionA.jsonl") in out
    assert str(tmp_path / "sessionB.jsonl") in out
    # Both are independent roots -- neither indented under the other.
    for line in out.splitlines():
        if "sessionA.jsonl" in line or "sessionB.jsonl" in line:
            assert line.startswith("- ")


def test_extract_sessions_directory_hints_at_subdirectory_one_level_down(tmp_path, capsys):
    subproj = tmp_path / "-workspaces-dstdns"
    subproj.mkdir()
    fp = subproj / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "hi"}),
    ]) + "\n", encoding="utf-8")

    # Pointed at the PARENT (like the real ~/.claude/projects itself) --
    # nothing directly in it, but a real project one level down.
    exit_code = cli.main(["extract-sessions", str(tmp_path)])
    err = capsys.readouterr().err
    assert exit_code == 1
    assert "did you mean" in err
    assert str(subproj) in err


# --- handoff-to-a-fresh-agent flags (--task/--task-file/--strip-stale-wakeups/
# --redact-pattern) -- session_extract/mangle.py, verified against a real
# dstdns session (design-context-lifecycle-experiments.md's E-015 follow-up).

def test_extract_task_appends_a_labeled_banner_after_the_render(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--task", "Carve the next candidate."])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "please look into this" in out
    assert out.index("nyxloom-extract: format=claude-code") < out.index("TASK FOR THIS SESSION")
    assert "Carve the next candidate." in out


def test_extract_task_file_reads_the_task_from_a_file(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    task_fp = tmp_path / "task.txt"
    task_fp.write_text("Do the thing described in this file.\n", encoding="utf-8")
    exit_code = cli.main(["extract", str(fp), "--task-file", str(task_fp)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Do the thing described in this file." in out


def test_extract_task_and_task_file_are_mutually_exclusive(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--task", "x", "--task-file", str(fp)])
    assert exit_code == 2
    assert "not allowed with argument --task" in capsys.readouterr().err


def _write_stale_wakeup_tail_fixture(tmp_path: Path) -> Path:
    # A trailing run of 2 near-duplicate "stale wakeup, nothing new"
    # checkpoints after a real, informative one -- mirrors the real pattern
    # found in the dstdns session this feature was built against. Each
    # block is long enough to clear the default long_comment_chars filter
    # on its own (independent of checkpoint scoring).
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             message={"role": "user", "content": "please look into this"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             message={"role": "assistant", "content": [{"type": "text", "text": (
                 "The package is fully closed: merged, gate green across every lane, "
                 "worktree and all containers torn down and independently verified clean, "
                 "docs archived, memory updated with the new operational lessons learned."
             )}]}),
        _rec(type="assistant", uuid="a2", timestamp="2026-01-01T00:00:02Z",
             message={"role": "assistant", "content": [{"type": "text", "text": (
                 "This is the stale fallback wakeup I armed earlier -- everything it asks "
                 "for already completed in the meantime. Quick confirmation, then done."
             )}]}),
        _rec(type="assistant", uuid="a3", timestamp="2026-01-01T00:00:03Z",
             message={"role": "assistant", "content": [{"type": "text", "text": (
                 "This is the same stale fallback message repeating -- already confirmed "
                 "in the previous turn that everything is done and consistent. No new "
                 "action needed."
             )}]}),
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_extract_strip_stale_wakeups_collapses_the_trailing_repeat(tmp_path, capsys):
    fp = _write_stale_wakeup_tail_fixture(tmp_path)
    # --long-threshold 0 isolates this test from select()'s independent
    # length filter -- every ASSISTANT_TEXT survives selection regardless
    # of length, so only --strip-stale-wakeups's own behavior is exercised.
    exit_code = cli.main(["extract", str(fp), "--long-threshold", "0", "--strip-stale-wakeups"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "stripped 1 stale-wakeup checkpoint(s) from the tail" in captured.err
    assert "This is the stale fallback wakeup I armed earlier" in captured.out
    assert "This is the same stale fallback message repeating" not in captured.out


def test_extract_without_the_flag_keeps_every_stale_wakeup_repeat(tmp_path, capsys):
    fp = _write_stale_wakeup_tail_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--long-threshold", "0"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "This is the same stale fallback message repeating" in out


def test_extract_redact_pattern_replaces_matching_paragraphs(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--redact-pattern", "please look"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "redacted 1 paragraph(s) matching --redact-pattern" in captured.err
    assert "please look into this" not in captured.out
    assert "redacted paragraph -- matched --redact-pattern" in captured.out


def test_extract_redact_pattern_rejects_an_invalid_regex(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--redact-pattern", "(unclosed"])
    assert exit_code == 1
    assert "invalid regex" in capsys.readouterr().err


def test_extract_accepts_a_bare_claude_code_session_uuid(tmp_path, capsys, monkeypatch):
    # Feature A wiring: every extract-* verb routes its SESSION_LOG
    # positional through locate.resolve_session_ref, so a pasted session id
    # with no path at all resolves to the file holding it (locate.py's own
    # tests cover the search/priority/ambiguity rules).
    uuid = "03b58ae4-5a21-4667-bf22-7eb364115ba3"
    home = tmp_path / "home"
    project = home / ".claude" / "projects" / escape_cwd(tmp_path)
    project.mkdir(parents=True)
    fixture = _write_claude_code_fixture(tmp_path)
    (project / f"{uuid}.jsonl").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["extract", uuid])
    assert exit_code == 0
    assert "please look into this" in capsys.readouterr().out


def test_extract_reports_an_unresolvable_session_ref(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    exit_code = cli.main(["extract", "03b58ae4-5a21-4667-bf22-7eb364115ba3"])
    assert exit_code == 1
    assert "not found under" in capsys.readouterr().err


def test_extract_sessions_accepts_a_bare_session_uuid_too(tmp_path, capsys, monkeypatch):
    uuid = "03b58ae4-5a21-4667-bf22-7eb364115ba3"
    home = tmp_path / "home"
    project = home / ".claude" / "projects" / escape_cwd(tmp_path)
    project.mkdir(parents=True)
    fixture = _write_claude_code_fixture(tmp_path)
    (project / f"{uuid}.jsonl").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["extract-sessions", uuid])
    assert exit_code == 0
    assert "(interactive session)" in capsys.readouterr().out


def test_extract_render_markdown_renders_blocks_but_not_the_scaffolding(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--render-markdown", "--no-color"])
    out = capsys.readouterr().out
    assert exit_code == 0
    # the '## Status' header rendered (its own '##' consumed)...
    assert "Status" in out and "## Status" not in out
    # ...while render.py's own separator and marker footer survive verbatim
    assert "\n---\n" in out
    assert "<!-- nyxloom-extract: format=claude-code marker=" in out
    assert "\x1b[" not in out


def test_extract_render_markdown_errors_with_json(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--json", "--render-markdown"])
    assert exit_code == 1
    assert "--render-markdown" in capsys.readouterr().err


def test_extract_color_without_a_render_mode_errors(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--no-color"])
    assert exit_code == 1
    assert "--color/--no-color only apply to" in capsys.readouterr().err


def test_extract_color_and_no_color_are_mutually_exclusive(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--render-markdown", "--color", "--no-color"])
    assert exit_code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_extract_highlight_keeps_every_markdown_character(tmp_path, capsys):
    import re

    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--highlight", "--color"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "\x1b[" in out  # colored
    # ...and unlike --render-markdown, the markup itself survives, which is
    # the whole reason this is a separate flag.
    assert "## Status" in re.sub(r"\x1b\[[0-9;]*m", "", out)


def test_extract_lossless_highlight_colors_the_dump(tmp_path, capsys):
    import re

    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract-lossless", str(fp), "--highlight", "--color"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "\x1b[" in out
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    assert "===[a2 |" in plain and "## Status" in plain


def test_extract_lossless_no_color_disables_highlight_ansi(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    assert cli.main(["extract-lossless", str(fp), "--highlight", "--no-color"]) == 0
    assert "\x1b[" not in capsys.readouterr().out


def test_extract_render_markdown_and_highlight_are_mutually_exclusive(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--render-markdown", "--highlight"])
    assert exit_code == 1
    assert "opposite goals" in capsys.readouterr().err


def test_extract_follow_rejects_json(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--follow", "--json"])
    assert exit_code == 1
    assert "no JSON document to emit" in capsys.readouterr().err


def test_extract_follow_rejects_until(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--follow", "--until", "a2"])
    assert exit_code == 1
    assert "contradictory" in capsys.readouterr().err


def test_extract_follow_rejects_a_task_banner(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--follow", "--task", "do the next thing"])
    assert exit_code == 1
    assert "contradictory" in capsys.readouterr().err


@pytest.mark.parametrize("flag,value", [
    ("--interval", "0.5"),
    ("--bell", None),
    ("--on-attention", "true"),
    ("--notify-project", "someproject"),
    ("--attention-min-chars", "100"),
])
def test_follow_only_flags_error_without_follow(tmp_path, capsys, flag, value):
    # Every one of these would silently do nothing otherwise -- the trap this
    # package consistently errors on instead.
    fp = _write_claude_code_fixture(tmp_path)
    argv = ["extract", str(fp), flag] + ([value] if value else [])
    exit_code = cli.main(argv)
    assert exit_code == 1
    assert f"{flag} only has an effect with --follow" in capsys.readouterr().err


def test_extract_lossless_follow_only_flags_error_without_follow(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract-lossless", str(fp), "--bell"])
    assert exit_code == 1
    assert "--bell only has an effect with --follow" in capsys.readouterr().err


def test_extract_lossless_color_without_highlight_errors(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract-lossless", str(fp), "--no-color"])
    assert exit_code == 1
    assert "--color/--no-color only apply to --highlight" in capsys.readouterr().err


def test_follow_anchor_is_taken_before_phase_one_parses(tmp_path, capsys, monkeypatch):
    # The two-phase handoff: phase 1 prints the one-shot brief, then phase 2
    # starts from the byte offset captured BEFORE that parse (so a record
    # appended mid-parse is duplicated rather than lost -- see cli.py's
    # _follow_anchor).
    from nyxloom.session_extract import follow as follow_mod

    fp = _write_claude_code_fixture(tmp_path)
    size_before = fp.stat().st_size
    seen = {}

    def _fake_run_forever(self):
        seen["offset"] = self._source.tailer.offset
        seen["lossless"] = self._lossless
        seen["interval"] = self._follow.interval
        return 0

    monkeypatch.setattr(follow_mod.Follower, "run_forever", _fake_run_forever)
    exit_code = cli.main(["extract", str(fp), "--follow"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "please look into this" in out  # phase 1 still printed in full
    assert seen == {"offset": size_before, "lossless": False, "interval": 1.0}


def test_follow_preserves_sidechain_only_primary_detection(tmp_path, monkeypatch):
    from nyxloom.session_extract import follow as follow_mod

    fp = tmp_path / "sidechain.jsonl"
    records = [
        _rec(type="user", uuid="u1", timestamp="2026-01-01T00:00:00Z",
             isSidechain=True, message={"role": "user", "content": "subtask"}),
        _rec(type="assistant", uuid="a1", timestamp="2026-01-01T00:00:01Z",
             isSidechain=True, message={"role": "assistant", "content": [
                 {"type": "text", "text": "subtask answer"},
             ]}),
    ]
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    seen = {}

    def _fake_run_forever(self):
        seen["has_primary_thread"] = self._source._state.has_primary_thread
        return 0

    monkeypatch.setattr(follow_mod.Follower, "run_forever", _fake_run_forever)
    assert cli.main(["extract", str(fp), "--follow"]) == 0
    assert seen == {"has_primary_thread": False}


def test_follow_keeps_primary_thread_true_for_codex_jsonl(tmp_path, monkeypatch):
    from nyxloom.session_extract import follow as follow_mod

    fp = _write_codex_fixture(tmp_path)
    seen = {}

    def _fake_run_forever(self):
        seen["has_primary_thread"] = self._source._state.has_primary_thread
        return 0

    monkeypatch.setattr(follow_mod.Follower, "run_forever", _fake_run_forever)
    assert cli.main(["extract", str(fp), "--follow"]) == 0
    assert seen == {"has_primary_thread": True}


def test_follow_anchor_rewinds_to_the_start_of_a_partial_jsonl_record(tmp_path):
    prefix = b'{"type":"user"}\n'
    partial = b'{"type":"assistant","message":{"content":['
    fp = tmp_path / "session.jsonl"
    fp.write_bytes(prefix + partial)

    assert cli._follow_anchor(fp, "claude-code", None) == len(prefix)


def test_follow_anchor_accepts_a_newline_at_the_start_of_the_file(tmp_path):
    fp = tmp_path / "session.jsonl"
    fp.write_bytes(b"\n")
    assert cli._follow_anchor(fp, "claude-code", None) == 1


def test_extract_lossless_follow_uses_lossless_semantics_for_phase_two(tmp_path, capsys, monkeypatch):
    from nyxloom.session_extract import follow as follow_mod

    fp = _write_claude_code_fixture(tmp_path)
    seen = {}

    def _fake_run_forever(self):
        seen["lossless"] = self._lossless
        return 0

    monkeypatch.setattr(follow_mod.Follower, "run_forever", _fake_run_forever)
    exit_code = cli.main(["extract-lossless", str(fp), "--follow"])
    assert exit_code == 0
    assert "Let me check." in capsys.readouterr().out
    assert seen == {"lossless": True}


def test_extract_follow_rejects_a_busy_loop_interval(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--follow", "--interval", "0"])
    assert exit_code == 1
    assert "busy loop" in capsys.readouterr().err


def test_follow_reports_an_unknown_notify_project_instead_of_crashing(tmp_path, capsys, monkeypatch):
    # --notify-project resolves through the ordinary project registry, so an
    # id that isn't registered has to be a clean error, not a traceback out of
    # the middle of a tail.
    from nyxloom import config as config_mod

    monkeypatch.setattr(config_mod, "load_registry", lambda: {})
    fp = _write_claude_code_fixture(tmp_path)
    exit_code = cli.main(["extract", str(fp), "--follow", "--notify-project", "nope"])
    assert exit_code == 1
    assert "--notify-project" in capsys.readouterr().err


def test_follow_anchor_handles_empty_unterminated_and_missing_jsonl_files(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert cli._follow_anchor(empty, "claude-code", None) == 0

    unterminated = tmp_path / "unterminated.jsonl"
    unterminated.write_bytes(b"x" * 9000)
    assert cli._follow_anchor(unterminated, "claude-code", None) == 0

    assert cli._follow_anchor(tmp_path / "missing.jsonl", "claude-code", None) == 0


def test_follow_anchor_handles_an_opencode_store_without_a_matching_message(tmp_path):
    db = _write_opencode_fixture(tmp_path)
    anchor = cli._follow_anchor(db, "opencode", "does-not-exist")
    assert anchor.cursor == (-1, "")


def test_follow_anchor_returns_the_opencode_message_and_part_fingerprint(tmp_path):
    db = _write_opencode_fixture(tmp_path)
    anchor = cli._follow_anchor(db, "opencode", "s0")
    assert anchor.cursor == (2, "m0b")
    assert anchor.fingerprint[0]  # message data JSON
    assert anchor.fingerprint[1][0][0] == "p0b"


def test_follow_anchor_opencode_uses_a_read_only_sqlite_uri(tmp_path, monkeypatch):
    import sqlite3

    db = _write_opencode_fixture(tmp_path)
    real_connect = sqlite3.connect
    calls = []

    def _connect(*args, **kwargs):
        calls.append((args, kwargs))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _connect)
    cli._follow_anchor(db, "opencode", "s0")
    assert calls and calls[-1][1].get("uri") is True


def test_follow_anchor_returns_none_for_a_non_opencode_path(tmp_path):
    assert cli._follow_anchor(tmp_path, "opencode", "s0") is None


def test_extract_follow_opencode_resolves_the_single_session_and_wires_the_source(
    tmp_path, capsys, monkeypatch
):
    from nyxloom.session_extract import follow as follow_mod

    db = _write_opencode_fixture(tmp_path)
    seen = {}
    real_anchor = cli._follow_anchor

    def _anchor(path, fmt, session):
        seen["anchor_session"] = session
        return real_anchor(path, fmt, session)

    def _fake_run_forever(self):
        seen["source"] = type(self._source).__name__
        seen["session"] = self._source._session_id
        seen["anchor"] = self._source._anchor_cursor
        return 0

    monkeypatch.setattr(follow_mod.Follower, "run_forever", _fake_run_forever)
    monkeypatch.setattr(cli, "_follow_anchor", _anchor)
    exit_code = cli.main(["extract", str(db), "--follow"])
    assert exit_code == 0
    assert seen == {
        "source": "OpencodeSource", "session": "s0", "anchor": (2, "m0b"),
        "anchor_session": "s0",
    }
    assert "please look into this" in capsys.readouterr().out


def test_extract_follow_opencode_accepts_the_legacy_tuple_anchor_shape(
    tmp_path, monkeypatch
):
    from nyxloom.session_extract import follow as follow_mod

    db = _write_opencode_fixture(tmp_path)
    seen = {}

    def _fake_run_forever(self):
        seen["cursor"] = self._source._cursor
        seen["fingerprint"] = self._source._anchor_fingerprint
        return 0

    monkeypatch.setattr(follow_mod.Follower, "run_forever", _fake_run_forever)
    monkeypatch.setattr(cli, "_follow_anchor", lambda *_args: (-1, ""))
    assert cli.main(["extract", str(db), "--follow", "--opencode-session", "s0"]) == 0
    assert seen == {"cursor": (-1, ""), "fingerprint": None}


def test_extract_follow_opencode_does_not_discover_twice_when_session_is_explicit(
    tmp_path, monkeypatch
):
    from nyxloom.session_extract import follow as follow_mod
    from nyxloom.session_extract.adapters import opencode as opencode_adapter

    db = _write_opencode_fixture(tmp_path)

    calls = []
    real_list_sessions = opencode_adapter.list_sessions

    def _list_sessions(*args, **kwargs):
        calls.append((args, kwargs))
        return real_list_sessions(*args, **kwargs)

    monkeypatch.setattr(opencode_adapter, "list_sessions", _list_sessions)
    monkeypatch.setattr(follow_mod.Follower, "run_forever", lambda self: 0)
    assert cli.main([
        "extract", str(db), "--follow", "--opencode-session", "s0",
    ]) == 0
    # One call belongs to extract()'s ordinary validation. A second call would
    # mean the pre-phase follow resolver ignored the explicit id.
    assert len(calls) == 1


def test_extract_lossless_follow_opencode_resolves_a_single_session(
    tmp_path, monkeypatch
):
    from nyxloom.session_extract import follow as follow_mod

    db = _write_opencode_fixture(tmp_path)
    seen = {}

    def _fake_run_forever(self):
        seen["source"] = type(self._source).__name__
        seen["session"] = self._source._session_id
        seen["anchor"] = self._source._anchor_cursor
        return 0

    monkeypatch.setattr(follow_mod.Follower, "run_forever", _fake_run_forever)
    assert cli.main(["extract-lossless", str(db), "--follow"]) == 0
    assert seen == {"source": "OpencodeSource", "session": "s0", "anchor": (2, "m0b")}


def test_extract_lossless_follow_opencode_does_not_discover_twice_when_session_is_explicit(
    tmp_path, monkeypatch
):
    from nyxloom.session_extract import follow as follow_mod
    from nyxloom.session_extract.adapters import opencode as opencode_adapter

    db = _write_opencode_fixture(tmp_path)
    calls = []
    real_list_sessions = opencode_adapter.list_sessions

    def _list_sessions(*args, **kwargs):
        calls.append((args, kwargs))
        return real_list_sessions(*args, **kwargs)

    monkeypatch.setattr(opencode_adapter, "list_sessions", _list_sessions)
    monkeypatch.setattr(follow_mod.Follower, "run_forever", lambda self: 0)
    assert cli.main([
        "extract-lossless", str(db), "--follow", "--opencode-session", "s0",
    ]) == 0
    assert calls == []


def test_extract_follow_opencode_leaves_an_ambiguous_session_unselected(tmp_path, capsys, monkeypatch):
    db = _write_opencode_fixture(tmp_path, n_sessions=2)
    monkeypatch.setattr(
        "nyxloom.session_extract.follow.Follower.run_forever", lambda self: 0
    )
    assert cli.main(["extract", str(db), "--follow"]) == 1
    assert "holds 2 sessions" in capsys.readouterr().err


def test_extract_lossless_follow_leaves_an_ambiguous_session_unselected(tmp_path, capsys, monkeypatch):
    db = _write_opencode_fixture(tmp_path, n_sessions=2)
    monkeypatch.setattr(
        "nyxloom.session_extract.follow.Follower.run_forever", lambda self: 0
    )
    assert cli.main(["extract-lossless", str(db), "--follow"]) == 1
    assert "holds 2 opencode sessions" in capsys.readouterr().err


def test_extract_json_rejects_highlight_as_text_only(tmp_path, capsys):
    fp = _write_claude_code_fixture(tmp_path)
    assert cli.main(["extract", str(fp), "--json", "--highlight", "--color"]) == 1
    err = capsys.readouterr().err
    assert "--highlight" in err and "only affect" in err


@pytest.mark.parametrize("verb", ["extract-lossless", "extract-debug", "extract-report", "extract-sessions"])
def test_extract_family_reports_an_unresolvable_path_before_dispatch(tmp_path, capsys, verb):
    assert cli.main([verb, str(tmp_path / "missing-session.jsonl")]) == 1
    assert "neither an existing path" in capsys.readouterr().err


def test_session_log_resolution_failure_returns_no_resolution(tmp_path, capsys):
    args = type("Args", (), {"path": str(tmp_path / "missing-session.jsonl")})()
    assert cli._resolve_session_log(args) is None
    assert "neither an existing path" in capsys.readouterr().err
