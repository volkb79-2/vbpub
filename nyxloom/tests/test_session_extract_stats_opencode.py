"""stats.py opencode support: `_build_call_rows_opencode` and its helpers,
against a synthetic SQLite fixture mirroring the real session/message/part
schema verified this session against a real local `~/.local/share/opencode/
opencode.db` (4,455 message rows across 72 sessions) -- see stats.py's own
`_build_call_rows_opencode` docstring for the full real-data writeup.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nyxloom.session_extract import stats

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

    conn.execute(
        "INSERT INTO message VALUES ('m1', 's1', 1000, 1000, ?)",
        (json.dumps({"role": "user"}),),
    )
    conn.execute(
        "INSERT INTO part VALUES ('p1', 'm1', 's1', 1000, 1000, ?)",
        (json.dumps({"type": "text", "text": "please look into the flaky test"}),),
    )

    # a real nonzero-cost assistant row (shape verified against a real
    # local opencode.db row: tokens.{input,output,reasoning,cache.{read,
    # write}} sum to ~= tokens.total -- ADDITIVE, unlike Codex).
    conn.execute(
        "INSERT INTO message VALUES ('m2', 's1', 2000, 2000, ?)",
        (json.dumps({
            "role": "assistant", "modelID": "z-ai/glm-5.2", "providerID": "openrouter",
            "variant": "medium", "cost": 0.00079647,
            "tokens": {"total": 7428, "input": 620, "output": 0, "reasoning": 4,
                       "cache": {"write": 0, "read": 6805}},
            "time": {"created": 1990, "completed": 2500},
        }),),
    )
    conn.execute(
        "INSERT INTO part VALUES ('p2', 'm2', 's1', 2000, 2000, ?)",
        (json.dumps({"type": "text", "text": "Let me check."}),),
    )

    # a tool-call-only / errored assistant row: no text part at all, so it
    # never becomes a NormalizedEvent and never gets a CallRow -- a real,
    # precedented limitation (Claude Code has the exact same gap for a
    # tool_use-only assistant record).
    conn.execute(
        "INSERT INTO message VALUES ('m3', 's1', 3000, 3000, ?)",
        (json.dumps({
            "role": "assistant", "modelID": "x-ai/grok-4.5", "cost": 0,
            "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "time": {"created": 2990},  # no "completed" -- an in-flight/interrupted call
            "error": {"name": "APIError"},
        }),),
    )
    conn.execute(
        "INSERT INTO part VALUES ('p3', 'm3', 's1', 3000, 3000, ?)",
        (json.dumps({"type": "tool-call", "tool": "bash"}),),
    )

    conn.execute(
        "INSERT INTO message VALUES ('m4', 's1', 4000, 4000, ?)",
        (json.dumps({"role": "user"}),),
    )
    conn.execute(
        "INSERT INTO part VALUES ('p4', 'm4', 's1', 4000, 4000, ?)",
        (json.dumps({"type": "text", "text": "great, thanks"}),),
    )

    conn.commit()
    conn.close()
    return db


def test_usage_fields_map_additively_no_subtraction_needed(tmp_path):
    # Contrast with Codex (_build_call_rows_codex): opencode's tokens.input
    # is ALREADY the fresh-only component (confirmed additive against a
    # real row: 620+0+4+6805+0 ~= total 7428) -- direct field mapping is
    # correct here, unlike Codex's subset-inclusive shape.
    db = _write_fixture(tmp_path)
    rows = stats.build_call_rows(db, fmt="opencode", session_id="s1")

    asst = next(r for r in rows if r.marker == "m2")
    assert asst.input_tokens == 620
    assert asst.cache_read_tokens == 6805
    assert asst.cache_creation_tokens == 0
    assert asst.output_tokens == 0
    assert asst.thinking_tokens == 4
    assert asst.cost_usd == 0.00079647
    assert asst.model == "z-ai/glm-5.2"
    assert asst.effort == "medium"
    assert asst.context_size == 620 + 6805


def test_real_per_call_latency_used_instead_of_timestamp_diffing(tmp_path):
    # opencode's own time.completed - time.created is genuine per-call
    # latency (0.51s here: 2500 - 1990 ms), used directly instead of the
    # generic "diff against the previous row's timestamp" every other
    # format has to fall back to.
    db = _write_fixture(tmp_path)
    rows = stats.build_call_rows(db, fmt="opencode", session_id="s1")
    asst = next(r for r in rows if r.marker == "m2")
    assert asst.elapsed_since_prev_s == pytest.approx(0.51)


def test_tool_only_or_errored_assistant_row_never_becomes_a_call_row(tmp_path):
    db = _write_fixture(tmp_path)
    rows = stats.build_call_rows(db, fmt="opencode", session_id="s1")
    assert not any(r.marker == "m3" for r in rows)
    assert [r.marker for r in rows] == ["m1", "m2", "m4"]


def test_opencode_rows_never_report_a_real_lifecycle_marker(tmp_path):
    # adapters/opencode.py's own honest gap: LIFECYCLE_MARKER is never
    # emitted for opencode.
    db = _write_fixture(tmp_path)
    rows = stats.build_call_rows(db, fmt="opencode", session_id="s1")
    assert not any(r.is_real_lifecycle for r in rows)
    assert not any(r.kind == "lifecycle" for r in rows)


def test_build_blocks_sums_cost_across_a_block(tmp_path):
    db = _write_fixture(tmp_path)
    rows = stats.build_call_rows(db, fmt="opencode", session_id="s1")
    blocks = stats.build_blocks(rows)
    assert blocks[0].sum_cost_usd == pytest.approx(0.00079647)


def test_single_session_store_resolves_without_a_session_id(tmp_path):
    db = _write_fixture(tmp_path)
    rows = stats.build_call_rows(db, fmt="opencode")  # no session_id passed
    assert [r.marker for r in rows] == ["m1", "m2", "m4"]


def test_ambiguous_multi_session_store_without_session_id_raises(tmp_path):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO session VALUES ('s1', 100)")
    conn.execute("INSERT INTO session VALUES ('s2', 200)")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="2 opencode sessions"):
        stats.build_call_rows(db, fmt="opencode")


def test_no_sessions_at_all_raises(tmp_path):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="no opencode sessions found"):
        stats.build_call_rows(db, fmt="opencode")


def test_non_opencode_store_raises(tmp_path):
    with pytest.raises(ValueError, match="not an opencode SQLite store"):
        stats.build_call_rows(tmp_path / "nope", fmt="opencode")


def test_malformed_message_data_json_is_skipped_by_the_raw_scan(tmp_path):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO session VALUES ('s1', 100)")
    conn.execute("INSERT INTO message VALUES ('bad', 's1', 1, 1, ?)", ("not json at all",))
    conn.execute("INSERT INTO message VALUES ('m1', 's1', 2, 2, ?)",
                 (json.dumps({"role": "user"}),))
    conn.execute("INSERT INTO part VALUES ('p1', 'm1', 's1', 2, 2, ?)",
                 (json.dumps({"type": "text", "text": "still works"}),))
    conn.commit()
    conn.close()

    rows = stats.build_call_rows(db, fmt="opencode", session_id="s1")
    assert [r.marker for r in rows] == ["m1"]


def test_render_detailed_csv_shows_cost_column(tmp_path):
    db = _write_fixture(tmp_path)
    rows = stats.build_call_rows(db, fmt="opencode", session_id="s1")
    csv_text = stats.render_detailed_csv(rows)
    header = csv_text.splitlines()[0]
    assert "cost_usd" in header
    assert "0.000796" in csv_text  # cost_usd rendered to 6 decimal places
