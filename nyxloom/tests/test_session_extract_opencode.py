"""opencode adapter tests, against a minimal synthetic SQLite store built to
match the session/message/part schema verified against a real local
opencode.db (session, message.data JSON with role, part.data JSON with
{"type": "text", "text": ...}).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nyxloom.session_extract import ExtractConfig
from nyxloom.session_extract.adapters import opencode
from nyxloom.session_extract.events import EventKind

_SCHEMA = """
CREATE TABLE session (id TEXT PRIMARY KEY, time_updated INTEGER);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
"""


def _write_fixture(tmp_path: Path) -> Path:
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO session VALUES ('s1', 100)")
    conn.execute("INSERT INTO session VALUES ('s2', 200)")

    conn.execute(
        "INSERT INTO message VALUES ('m1', 's1', 1, 1, ?)",
        (json.dumps({"role": "user"}),),
    )
    conn.execute(
        "INSERT INTO part VALUES ('p1', 'm1', 's1', 1, 1, ?)",
        (json.dumps({"type": "text", "text": "i say ping, you say ___"}),),
    )
    conn.execute(
        "INSERT INTO message VALUES ('m2', 's1', 2, 2, ?)",
        (json.dumps({"role": "assistant"}),),
    )
    conn.execute(
        "INSERT INTO part VALUES ('p2', 'm2', 's1', 2, 2, ?)",
        (json.dumps({"type": "text", "text": "pong"}),),
    )
    # an unrecognized part type must be silently skipped, not raise
    conn.execute(
        "INSERT INTO part VALUES ('p3', 'm2', 's1', 2, 2, ?)",
        (json.dumps({"type": "tool-call", "tool": "bash"}),),
    )
    conn.commit()
    conn.close()
    return db


def test_sniff_and_list_sessions(tmp_path):
    db = _write_fixture(tmp_path)
    assert opencode.sniff(db)
    assert opencode.sniff(tmp_path)  # directory form resolves to opencode.db
    assert opencode.list_sessions(db) == ["s2", "s1"]  # newest time_updated first


def test_parse_roles_and_text_only_parts(tmp_path):
    db = _write_fixture(tmp_path)
    events = opencode.parse(db, "s1", ExtractConfig())
    assert len(events) == 2
    op = next(e for e in events if e.kind is EventKind.OPERATOR_TEXT)
    assert "ping" in op.text
    asst = next(e for e in events if e.kind is EventKind.ASSISTANT_TEXT)
    assert asst.text == "pong"


def test_since_marker(tmp_path):
    db = _write_fixture(tmp_path)
    events = opencode.parse(db, "s1", ExtractConfig(since_marker="m1"))
    assert [e.marker for e in events] == ["m2"]


def test_since_marker_unknown_raises(tmp_path):
    db = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="not found in session"):
        opencode.parse(db, "s1", ExtractConfig(since_marker="does-not-exist"))


def test_sniff_rejects_a_db_missing_the_expected_tables(tmp_path):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated (id TEXT)")
    conn.commit()
    conn.close()
    assert not opencode.sniff(db)


def test_sniff_rejects_a_file_that_is_not_a_database(tmp_path):
    not_a_db = tmp_path / "opencode.db"
    not_a_db.write_text("not a sqlite file at all")
    assert not opencode.sniff(not_a_db)


def test_list_sessions_on_non_db_path_is_empty(tmp_path):
    assert opencode.list_sessions(tmp_path / "nope") == []


def test_parse_on_non_db_path_raises(tmp_path):
    with pytest.raises(ValueError, match="not an opencode SQLite store"):
        opencode.parse(tmp_path / "nope", "s1", ExtractConfig())


def test_parse_skips_non_user_assistant_roles_and_malformed_rows(tmp_path):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO session VALUES ('s1', 100)")
    # a "system" role -- not user/assistant, must be skipped
    conn.execute("INSERT INTO message VALUES ('m1', 's1', 1, 1, ?)", (json.dumps({"role": "system"}),))
    conn.execute("INSERT INTO part VALUES ('p1', 'm1', 's1', 1, 1, ?)",
                 (json.dumps({"type": "text", "text": "system framing"}),))
    # a message row whose data isn't valid JSON at all -- must be skipped
    conn.execute("INSERT INTO message VALUES ('m2', 's1', 2, 2, ?)", ("not json",))
    # a message with a part whose data isn't valid JSON -- the message
    # itself survives, but that one malformed part contributes no text
    conn.execute("INSERT INTO message VALUES ('m3', 's1', 3, 3, ?)", (json.dumps({"role": "user"}),))
    conn.execute("INSERT INTO part VALUES ('p3', 'm3', 's1', 3, 3, ?)", ("not json",))
    conn.execute("INSERT INTO part VALUES ('p3b', 'm3', 's1', 3, 3, ?)",
                 (json.dumps({"type": "text", "text": "real text survives"}),))
    # a message with no text parts at all -- dropped entirely
    conn.execute("INSERT INTO message VALUES ('m4', 's1', 4, 4, ?)", (json.dumps({"role": "assistant"}),))
    conn.execute("INSERT INTO part VALUES ('p4', 'm4', 's1', 4, 4, ?)",
                 (json.dumps({"type": "tool-call", "tool": "bash"}),))
    conn.commit()
    conn.close()

    events = opencode.parse(db, "s1", ExtractConfig())
    assert [e.marker for e in events] == ["m3"]
    assert events[0].text == "real text survives"
