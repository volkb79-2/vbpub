"""opencode adapter tests, against a minimal synthetic SQLite store built to
match the session/message/part schema verified against a real local
opencode.db (session, message.data JSON with role, part.data JSON with
{"type": "text", "text": ...}).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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
