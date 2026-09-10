"""lossless.dump_opencode() tests: the independent "keep prose, drop
machine calls" dumper for opencode sessions, against a synthetic SQLite
fixture matching the real session/message/part schema (verified this
session against a real local ~/.local/share/opencode/opencode.db).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nyxloom.session_extract import lossless

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

    conn.execute("INSERT INTO message VALUES ('m1', 's1', 1000, 1000, ?)",
                 (json.dumps({"role": "user"}),))
    conn.execute("INSERT INTO part VALUES ('p1', 'm1', 's1', 1000, 1000, ?)",
                 (json.dumps({"type": "text", "text": "real operator prompt"}),))

    conn.execute("INSERT INTO message VALUES ('m2', 's1', 2000, 2000, ?)",
                 (json.dumps({"role": "assistant"}),))
    conn.execute("INSERT INTO part VALUES ('p2a', 'm2', 's1', 2000, 2000, ?)",
                 (json.dumps({"type": "text", "text": "assistant prose"}),))
    # a non-text part (a real opencode session's tool-call fragment) must
    # be dropped, not raise
    conn.execute("INSERT INTO part VALUES ('p2b', 'm2', 's1', 2000, 2000, ?)",
                 (json.dumps({"type": "tool-call", "tool": "bash"}),))

    # a tool-call-only assistant message: no text part at all -> dropped entirely
    conn.execute("INSERT INTO message VALUES ('m3', 's1', 3000, 3000, ?)",
                 (json.dumps({"role": "assistant"}),))
    conn.execute("INSERT INTO part VALUES ('p3', 'm3', 's1', 3000, 3000, ?)",
                 (json.dumps({"type": "tool-call", "tool": "bash"}),))

    conn.execute("INSERT INTO message VALUES ('m4', 's1', 4000, 4000, ?)",
                 (json.dumps({"role": "user"}),))
    conn.execute("INSERT INTO part VALUES ('p4', 'm4', 's1', 4000, 4000, ?)",
                 (json.dumps({"type": "text", "text": "final prose"}),))

    # a second session -- must never leak into a dump scoped to s1
    conn.execute("INSERT INTO message VALUES ('other1', 's2', 1000, 1000, ?)",
                 (json.dumps({"role": "user"}),))
    conn.execute("INSERT INTO part VALUES ('otherp1', 'other1', 's2', 1000, 1000, ?)",
                 (json.dumps({"type": "text", "text": "belongs to a different session"}),))

    conn.commit()
    conn.close()
    return db


def test_keeps_text_parts_drops_tool_calls_and_other_sessions(tmp_path):
    db = _write_fixture(tmp_path)
    out = lossless.dump_opencode(db, "s1")

    assert "real operator prompt" in out
    assert "assistant prose" in out
    assert "final prose" in out

    assert "bash" not in out
    assert "belongs to a different session" not in out


def test_since_marker_slices_forward(tmp_path):
    db = _write_fixture(tmp_path)
    out = lossless.dump_opencode(db, "s1", since_marker="m1")
    assert "real operator prompt" not in out
    assert "assistant prose" in out
    assert "final prose" in out


def test_until_marker_slices_backward_inclusive(tmp_path):
    db = _write_fixture(tmp_path)
    out = lossless.dump_opencode(db, "s1", until_marker="m2")
    assert "real operator prompt" in out
    assert "assistant prose" in out
    assert "final prose" not in out


def test_since_and_until_together_bound_a_span(tmp_path):
    db = _write_fixture(tmp_path)
    out = lossless.dump_opencode(db, "s1", since_marker="m1", until_marker="m2")
    assert "real operator prompt" not in out
    assert "assistant prose" in out
    assert "final prose" not in out


def test_unknown_since_marker_raises(tmp_path):
    db = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="--since"):
        lossless.dump_opencode(db, "s1", since_marker="does-not-exist")


def test_unknown_until_marker_raises(tmp_path):
    db = _write_fixture(tmp_path)
    with pytest.raises(ValueError, match="--until"):
        lossless.dump_opencode(db, "s1", until_marker="does-not-exist")


def test_non_opencode_store_raises(tmp_path):
    with pytest.raises(ValueError, match="not an opencode SQLite store"):
        lossless.dump_opencode(tmp_path / "nope", "s1")


def test_malformed_message_and_part_json_are_skipped_not_raised(tmp_path):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO session VALUES ('s1', 100)")

    # a message row whose data isn't valid JSON -- treated as role=None, skipped
    conn.execute("INSERT INTO message VALUES ('bad', 's1', 1, 1, ?)", ("not json at all",))

    # a message with one malformed part (skipped) and one real text part (survives)
    conn.execute("INSERT INTO message VALUES ('m2', 's1', 2, 2, ?)",
                 (json.dumps({"role": "user"}),))
    conn.execute("INSERT INTO part VALUES ('p2a', 'm2', 's1', 2, 2, ?)", ("not json",))
    conn.execute("INSERT INTO part VALUES ('p2b', 'm2', 's1', 2, 2, ?)",
                 (json.dumps({"type": "text", "text": "real text survives"}),))

    conn.commit()
    conn.close()

    out = lossless.dump_opencode(db, "s1")
    assert "real text survives" in out
    assert "bad" not in out


def test_marker_is_the_real_message_id_not_a_positional_fallback(tmp_path):
    # opencode's marker is always the message's own real DB id -- confirmed
    # by since_marker="m1" (a real id, not "line0"/"0") resolving correctly
    # above; this asserts the dump's own delimiter line surfaces that same
    # id verbatim.
    db = _write_fixture(tmp_path)
    out = lossless.dump_opencode(db, "s1")
    assert "m1" in out.splitlines()[0]
