"""adapters/__init__.py: the registry + get_adapter()/detect() error paths.
Per-adapter parsing behavior is tested in each adapter's own test module;
this file only covers the dispatch/detection layer itself.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from nyxloom.session_extract.adapters import ADAPTERS, DetectionError, detect, get_adapter


def test_registry_has_all_three_adapters():
    names = {a.name for a in ADAPTERS}
    assert names == {"claude-code", "codex", "opencode"}


def test_get_adapter_known_name():
    assert get_adapter("codex").name == "codex"


def test_get_adapter_unknown_name_lists_known_formats():
    with pytest.raises(DetectionError) as exc:
        get_adapter("not-a-real-cli")
    msg = str(exc.value)
    assert "claude-code" in msg and "codex" in msg and "opencode" in msg


def test_detect_no_match_raises_and_lists_known_formats(tmp_path):
    fp = tmp_path / "nothing.txt"
    fp.write_text("just some plain text, not a session log\n")
    with pytest.raises(DetectionError) as exc:
        detect(fp)
    msg = str(exc.value)
    assert "could not detect" in msg
    assert "claude-code" in msg and "codex" in msg and "opencode" in msg


def test_detect_claude_code_match(tmp_path):
    fp = tmp_path / "session.jsonl"
    fp.write_text(json.dumps({"sessionId": "s1", "parentUuid": None, "type": "user"}) + "\n")
    assert detect(fp).name == "claude-code"


def test_detect_codex_match(tmp_path):
    fp = tmp_path / "rollout.jsonl"
    fp.write_text(json.dumps({"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta",
                               "payload": {"cli_version": "0.1.0"}}) + "\n")
    assert detect(fp).name == "codex"


def test_detect_ambiguous_match_raises_and_names_the_matches(tmp_path, monkeypatch):
    # No two real adapters actually collide on the same file today; force a
    # genuine ambiguous case by making a second adapter also claim a path
    # that already, legitimately, sniffs as claude-code.
    from nyxloom.session_extract.adapters import codex

    fp = tmp_path / "session.jsonl"
    fp.write_text(json.dumps({"sessionId": "s1", "parentUuid": None, "type": "user"}) + "\n")
    assert detect(fp).name == "claude-code"  # sanity: unambiguous before the patch

    monkeypatch.setattr(codex, "sniff", lambda path: True)
    with pytest.raises(DetectionError, match="more than one known format"):
        detect(fp)


def test_detect_opencode_match(tmp_path):
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
    assert detect(db).name == "opencode"
