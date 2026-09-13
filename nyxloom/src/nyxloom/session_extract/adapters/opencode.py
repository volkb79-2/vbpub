"""opencode adapter -- ~/.local/share/opencode/opencode.db (SQLite, not
JSONL -- the one adapter here with a fundamentally different storage
shape).

Schema facts verified directly against a real local opencode.db:

- `session` table, full real schema (2026-09-11, `sqlite_master` against a
  real local 935MB store -- superseding the vaguer version of this note):
  `id` (text PK, e.g. `ses_0a6bb813bffe...`), `project_id`, `workspace_id`,
  `parent_id` (a fork's parent session -- see below), `slug`, `directory`,
  `path`, `title` (text NOT NULL -- a genuinely descriptive human string in
  real data, e.g. `"Find P55 and P57 tests (@explore subagent)"` -- the
  session's OWN name, not derived from message content), `version`,
  `share_url`, `summary_additions`/`summary_deletions`/`summary_files`/
  `summary_diffs`, `metadata`, `cost` (real, a running total), `tokens_
  input`/`tokens_output`/`tokens_reasoning`/`tokens_cache_read`/`tokens_
  cache_write` (all integer, session-level running totals -- NOT used by
  this adapter's own stats.py yet, which reads per-call usage from
  elsewhere; a real gap worth closing separately), `revert`, `permission`,
  `agent` (text -- the session's role/persona tag; real values observed:
  `"build"`, `"explore"`, `"general"`), `model` (JSON string, e.g.
  `{"id":"openai/gpt-5.6-luna","providerID":"openrouter","variant":
  "medium"}`), `time_created`/`time_updated`/`time_compacting`/
  `time_archived` (integer, Unix ms -- `time_compacting`/`time_archived`
  suggest native compaction/archival concepts, still unused here, see
  gaps below).
  **Sub-agent targeting (2026-09-11, cross-adapter design pass, confirmed
  against the real store above)**: a forked/sub-agent session is a
  SEPARATE row in this table (real data: 72 total sessions, 24 with
  `parent_id` set, max chain depth 1 in what's on this machine -- but
  `parent_id` is a plain self-FK, nothing caps deeper chains), so it is
  already targetable through this adapter's EXISTING `--opencode-session
  <id>` mechanism -- `list_sessions()` below has no `parent_id` filter, so it
  already lists forked sessions alongside top-level ones today, with no
  code change needed to make them selectable. `list_agents()` (added
  2026-09-11 for `nyxloom extract-sessions`) is the discovery layer this
  was missing: it surfaces every row's `parent_id`/`title`/`agent` so a
  human or controller can find the right id without already knowing it --
  real `title` values are self-descriptive enough (opencode's own
  `@explore subagent` convention names the relationship directly in the
  title) that no further labeling was needed. Unlike Claude Code (separate
  file per agent) or Codex (separate rollout file per agent, confirmed by
  live test -- see codex.py), the targeting PRIMITIVE already existed here
  before any of this session's work; only the "list children of this
  parent" convenience was the real gap, now closed.
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


def list_agents(path: Path) -> list["SessionNode"]:
    """Every session in the store, root and forked alike -- there is no
    single-file scoping concept here the way path scopes a Claude Code/
    Codex family, so this is the whole store's forest, not one session's
    family. See module docstring's "Sub-agent targeting" note."""
    from ..sessions import SessionNode

    db = _db_path(path)
    if db is None:
        raise ValueError(f"{path} is not an opencode SQLite store")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, parent_id, title, agent, time_created, time_updated FROM session"
        ).fetchall()
        counts = dict(conn.execute("SELECT session_id, COUNT(*) FROM message GROUP BY session_id"))
    finally:
        conn.close()

    parent_of = {r[0]: r[1] for r in rows}

    def _depth(sid: str, seen: set[str] | None = None) -> int:
        seen = seen or set()
        if sid in seen:
            return 0  # cycle guard -- not expected in real data
        seen.add(sid)
        p = parent_of.get(sid)
        if not p or p not in parent_of:
            return 0
        return 1 + _depth(p, seen)

    nodes = []
    for sid, parent_id, title, agent, time_created, time_updated in rows:
        label = title or "(untitled session)"
        if agent:
            label = f"{label} [{agent}]"
        nodes.append(SessionNode(
            id=sid,
            parent_id=parent_id if parent_id in parent_of else None,
            depth=_depth(sid),
            label=label,
            path=str(path),
            record_count=counts.get(sid, 0),
            first_ts=_ms_to_iso(time_created),
            last_ts=_ms_to_iso(time_updated),
        ))
    return nodes


def _ms_to_iso(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else None


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


def event_for_texts(
    seq: int, msg_id: str, time_created: int, data_json: str, texts: list[str]
) -> NormalizedEvent | None:
    """Build one message event from selected text contributions.

    Follow mode uses this for a changed boundary row: the message's
    already-emitted parts must not be rendered a second time.
    """
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError:
        return None
    role = data.get("role")
    if role not in ("user", "assistant") or not texts:
        return None

    ts = datetime.fromtimestamp(time_created / 1000, tz=timezone.utc).isoformat()
    kind = EventKind.OPERATOR_TEXT if role == "user" else EventKind.ASSISTANT_TEXT
    return NormalizedEvent(seq, msg_id, ts, kind, "\n".join(texts))


def event_for_row(
    conn: sqlite3.Connection, seq: int, msg_id: str, time_created: int, data_json: str
) -> NormalizedEvent | None:
    """One `message` row -> its NormalizedEvent, or None when the row
    carries no prose (a non-user/assistant role, unparseable data, or no
    text `part` rows at all).

    Factored out of parse()'s own loop (2026-09-12) so follow.py can apply
    the same rule to a row that arrived after the one-shot pass, instead of
    reimplementing it. Takes the open connection because a message's text
    lives in a SECOND query against `part` -- which is also why a row seen
    mid-stream can legitimately have no text yet; see follow.py's
    OpencodeSource for the settle rule that handles that.
    """
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
    return event_for_texts(seq, msg_id, time_created, data_json, texts)


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
            ev = event_for_row(conn, seq, msg_id, time_created, data_json)
            if ev is not None:
                events.append(ev)

        return events
    finally:
        conn.close()
