"""opencode adapter -- ~/.local/share/opencode/opencode.db (SQLite, not
JSONL -- the one adapter here with a fundamentally different storage
shape).

Schema facts verified directly against a real local opencode.db:

- `session` table: one row per session, including a `parent_id` column --
  opencode sessions can natively fork from a parent session -- and a
  `time_compacting` column, suggesting a native compaction concept. Neither
  is used by this adapter yet (see gaps below).
- `message` table: one row per message (id, session_id, time_created,
  time_updated, data). `data` is a JSON blob carrying at least {role, time,
  agent, model, summary} -- confirmed role "user" in the row inspected;
  "assistant" is inferred from the schema's evident intent, not directly
  observed in the one sample pulled during design.
- `part` table: one row per content fragment of a message (id, message_id,
  session_id, time_created, time_updated, data). `data` for a text
  fragment is {"type": "text", "text": "..."}. Only this one part `type`
  was directly observed -- a real opencode session almost certainly also
  has tool-call/tool-result/patch/file part types (the DB has separate
  `todo`, `permission`, `session_share` tables hinting at more structure),
  none of which were seen in the sample queried. This adapter therefore
  treats "text" parts as content and silently skips every other part type
  -- correct for "don't leak tool noise into a resume brief" by accident,
  but unverified as complete; do not trust this adapter for anything more
  than a first look until a real multi-part-type session has been read
  against it.

Known gaps, left honest rather than guessed:
- LIFECYCLE_MARKER is never emitted. `session.time_compacting` and the
  separate `session_context_epoch` table (baseline/snapshot/baseline_seq)
  are almost certainly opencode's own compaction-boundary equivalent, but
  their exact semantics were not verified against a session that has
  actually compacted -- guessing here risks silently mis-scoping a resume
  (the one thing this whole tool must get right), so it's left undone.
- QA_PAIR is never emitted -- no structured-question-tool equivalent was
  identified.
- Whether a `user`-role message was the human typing live versus a queued/
  injected input is not distinguished. The `session_input` table (prompt,
  delivery, admitted_seq, promoted_seq) looks like it carries exactly this
  distinction, but `delivery`'s value set was not inspected -- another
  place a wrong guess would be worse than an honest gap.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "opencode"


def _db_path(path: Path) -> Path | None:
    if path.is_file() and path.suffix == ".db":
        return path
    if path.is_dir() and (path / "opencode.db").exists():
        return path / "opencode.db"
    return None


def sniff(path: Path) -> bool:
    db = _db_path(path)
    if db is None:
        return False
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('session','message','part')"
            )
            return len(cur.fetchall()) == 3
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def list_sessions(path: Path) -> list[str]:
    db = _db_path(path)
    if db is None:
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id FROM session ORDER BY time_updated DESC").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
    db = _db_path(path)
    if db is None:
        raise ValueError(f"{path} is not an opencode SQLite store")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? "
            "ORDER BY time_created ASC, id ASC",
            (session_id,),
        ).fetchall()

        if config.since_marker is not None:
            idx = next((i for i, r in enumerate(rows) if r[0] == config.since_marker), None)
            if idx is None:
                raise ValueError(f"--since marker {config.since_marker!r} not found in session {session_id}")
            rows = rows[idx + 1 :]

        if config.until_marker is not None:
            idx = next((i for i, r in enumerate(rows) if r[0] == config.until_marker), None)
            if idx is None:
                raise ValueError(f"--until marker {config.until_marker!r} not found in session {session_id}")
            rows = rows[: idx + 1]

        events: list[NormalizedEvent] = []
        for seq, (msg_id, time_created, data_json) in enumerate(rows):
            try:
                data = json.loads(data_json)
            except json.JSONDecodeError:
                continue
            role = data.get("role")
            if role not in ("user", "assistant"):
                continue

            part_rows = conn.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY id ASC", (msg_id,)
            ).fetchall()
            texts = []
            for (part_json,) in part_rows:
                try:
                    part = json.loads(part_json)
                except json.JSONDecodeError:
                    continue
                if part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
            if not texts:
                continue

            # time_created is Unix milliseconds (verified against a real
            # row's value against its session's human-readable title).
            ts = datetime.fromtimestamp(time_created / 1000, tz=timezone.utc).isoformat()
            kind = EventKind.OPERATOR_TEXT if role == "user" else EventKind.ASSISTANT_TEXT
            events.append(NormalizedEvent(seq, msg_id, ts, kind, "\n".join(texts)))

        return events
    finally:
        conn.close()
