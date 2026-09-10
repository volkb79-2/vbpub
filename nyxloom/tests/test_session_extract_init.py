"""extract()'s own orchestration logic (session_id resolution, error paths)
and read_since_marker() -- the delta-extraction file-format-agnostic reader.
Per-adapter parsing is covered by each adapter's own test module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyxloom.session_extract import DetectionError, ExtractConfig, extract, read_since_marker
from nyxloom.session_extract.render import render_json, render_text


def _write_claude_fixture(tmp_path: Path, session_id: str = "s1") -> Path:
    records = [
        {"type": "user", "uuid": "u1", "sessionId": session_id, "parentUuid": None,
         "timestamp": "2026-01-01T00:00:00Z", "isSidechain": False,
         "message": {"role": "user", "content": "status?"}},
        {"type": "assistant", "uuid": "a1", "sessionId": session_id, "parentUuid": "u1",
         "timestamp": "2026-01-01T00:00:01Z", "isSidechain": False,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "all good"}]}},
    ]
    fp = tmp_path / "session.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_extract_resolves_the_single_session_automatically(tmp_path):
    fp = _write_claude_fixture(tmp_path)
    result = extract(fp)
    assert result.session_id == str(fp)
    assert result.format == "claude-code"


def test_extract_fmt_override_skips_autodetect(tmp_path):
    fp = _write_claude_fixture(tmp_path)
    result = extract(fp, fmt="claude-code")
    assert result.format == "claude-code"


def test_extract_unknown_session_id_raises(tmp_path):
    fp = _write_claude_fixture(tmp_path)
    with pytest.raises(DetectionError, match="not found"):
        extract(fp, session_id="does-not-exist")


def test_extract_undetectable_path_raises(tmp_path):
    fp = tmp_path / "nothing.txt"
    fp.write_text("plain text, not a session log\n")
    with pytest.raises(DetectionError, match="could not detect"):
        extract(fp)


def test_extract_last_marker_reflects_full_parse_not_just_selection(tmp_path):
    # A tight max_words cuts the rendered/selected output down, but
    # last_marker must still report the true end of the whole session so
    # --since-file chaining never silently skips content.
    fp = _write_claude_fixture(tmp_path)
    result = extract(fp, ExtractConfig(max_words=1))
    assert result.last_marker == "a1"


def test_extract_no_sessions_found_raises(tmp_path):
    # Claude Code/Codex are one-file-per-session formats: list_sessions()
    # always returns a single synthetic id regardless of content, so "no
    # sessions found" can only genuinely happen for a shared store
    # (opencode) whose DB holds zero session rows.
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
    conn.commit()
    conn.close()
    with pytest.raises(DetectionError, match="no sessions found"):
        extract(db, fmt="opencode")


def test_extract_multiple_sessions_without_session_id_raises(tmp_path):
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
    conn.execute("INSERT INTO session VALUES ('s1', 100)")
    conn.execute("INSERT INTO session VALUES ('s2', 200)")
    conn.commit()
    conn.close()
    with pytest.raises(DetectionError, match="pass --session"):
        extract(db, fmt="opencode")


def test_read_since_marker_from_text_output(tmp_path):
    text = render_text([], checkpoint_threshold=3.0, fmt="claude-code", last_marker="abc-123")
    fp = tmp_path / "prior_run.txt"
    fp.write_text(text, encoding="utf-8")
    fmt, marker = read_since_marker(fp)
    assert (fmt, marker) == ("claude-code", "abc-123")


def test_read_since_marker_from_json_output(tmp_path):
    payload = render_json([], checkpoint_threshold=3.0, fmt="codex", last_marker="xyz-789")
    fp = tmp_path / "prior_run.json"
    fp.write_text(payload, encoding="utf-8")
    fmt, marker = read_since_marker(fp)
    assert (fmt, marker) == ("codex", "xyz-789")


def test_read_since_marker_raises_on_file_with_no_marker(tmp_path):
    text = render_text([], checkpoint_threshold=3.0, fmt="claude-code", last_marker=None)
    fp = tmp_path / "prior_run.txt"
    fp.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="no embedded"):
        read_since_marker(fp)


def test_read_since_marker_takes_the_last_footer_not_the_first(tmp_path):
    # Adversarial-review finding: a kept event's own text can quote an old
    # footer verbatim (the README's own snapshot-chain pattern pastes a
    # prior run's output back into a session as context) -- MARKER_FOOTER_RE
    # must resolve to the true trailing marker, not whichever one appears
    # first in the file.
    body = (
        "## [t] OPERATOR\n\n"
        "for context, here's what the last run said: "
        "<!-- nyxloom-extract: format=claude-code marker=STALE-FAKE-MARKER -->\n"
    )
    text = body + "\n<!-- nyxloom-extract: format=claude-code marker=REAL-CURRENT-MARKER -->\n"
    fp = tmp_path / "prior_run.txt"
    fp.write_text(text, encoding="utf-8")
    fmt, marker = read_since_marker(fp)
    assert (fmt, marker) == ("claude-code", "REAL-CURRENT-MARKER")
