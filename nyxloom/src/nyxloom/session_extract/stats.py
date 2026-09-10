"""Session cost/timeline analysis -- `nyxloom session-stats`. V9 in
`nyxloom/docs/design-context-lifecycle-experiments.md` (E-009): every source
format already carries a full per-call token/cost ledger the extractor
itself never reads (it only needs event text, not usage). This module reads
that ledger and turns it into two views:

  - a DETAILED view, one row per real API call (`build_call_rows`), and
  - a CONDENSED view, one row per block of calls between prompt boundaries
    (`build_blocks`), meant to fit a whole session on one page.

Both are annotated with what `select.select()` would have kept, under each
of a small set of named `PROFILES`, if an extraction had been triggered at
that exact point -- so the timeline doubles as a "what would our extractor
have produced here" simulator (`_simulate_profile`), not just a usage log.

Claude Code, Codex, and opencode are all supported today. Each format's
usage ledger has a genuinely different shape -- not just different field
names -- verified against real local files, not guessed:

- Claude Code: usage lives DIRECTLY on the `assistant` record that produced
  it (`message.usage`) -- see `_build_call_rows_claude_code`.
- Codex: usage is a SEPARATE, periodically-emitted `event_msg.payload.type
  == "token_count"` record (a running/cumulative counter), not attached to
  any particular UserMessage/AgentMessage -- see `_build_call_rows_codex`'s
  own docstring for the real-data findings this required (including a
  correction to adapters/codex.py's own module docstring: its claim that
  the NEW generation's `compacted.payload.message` is always "Codex's own
  real compaction summary text" holds for only ~9% of real compaction
  records; the other ~91% -- ordinary auto-compactions -- carry an EMPTY
  `message`, with the real replacement content genuinely encrypted).
- opencode: usage lives DIRECTLY on the `assistant` message's own `data`
  (`tokens`/`cost`/`time`), like Claude Code, but ADDITIVELY decomposed
  (input/cache.read/cache.write/output/reasoning all sum to `tokens.total`)
  rather than Codex's subset-inclusive shape -- see
  `_build_call_rows_opencode`.

No --since/--until support yet, for any format: this always reads the FULL,
unsliced session. Marker fallback (`rec.get("uuid") or f"line{i}"` for
Claude Code) therefore only needs to match each adapter's own fallback for
an UNSLICED parse (config.since_marker=None) -- the absolute-position fix in
adapters/claude_code.py (commit `9d2f06ae`) doesn't need duplicating here
for that reason; if --since support is ever added here, it does. Codex's
`_codex_raw_scan` reuses adapters/codex.py's own `_TOP_LEVEL_TYPES` filter
for exactly this reason: an unsliced unfiltered-vs-filtered index mismatch
would silently misattribute every token_count row.

Known gaps, left honest rather than guessed:
- Codex rows carry no `tools_called` (the UserMessage/AgentMessage layer
  this reads doesn't carry tool_use blocks the way Claude Code's assistant
  records do -- CommandExecution/CollabAgentToolCall are separate, dropped
  item types) and no per-call `model` (Codex's `turn_context` record DOES
  carry one, but it lives outside the event_msg/compacted universe --
  `_build_call_rows_codex` stamps the SESSION's first-seen `turn_context`
  model uniformly on every api_call row rather than tracking mid-session
  model switches, which real Codex sessions can in principle have).
- Codex's `compacted`/`context_compacted` markers carry no `trigger`/
  `preTokens`/`postTokens`/`durationMs` the way Claude Code's
  `compactMetadata` does -- `compact_trigger`/`compact_pre_tokens`/
  `compact_post_tokens`/`compact_duration_ms` are therefore always None on
  Codex rows.
- `cost_usd` is populated only for opencode (the one format with a
  precomputed price-table cost already in the record) -- Claude Code and
  Codex leave it None rather than reverse-engineering a price table here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import classifier
from .adapters import claude_code, detect
from .config import PROFILES, ExtractConfig
from .events import EventKind, NormalizedEvent
from .select import select


@dataclass
class CallRow:
    """One real API call (an `assistant`-type record with a `usage` block)
    plus the OPERATOR_TEXT/QA_PAIR/LIFECYCLE_MARKER record immediately
    preceding it, if any -- Claude Code turns are (prompt, response) pairs
    on disk, and a cost row is meaningless without knowing what triggered it.

    For Codex, a row is also built per `token_count` event (see
    `_build_call_rows_codex`) -- those carry `kind == "api_call"`, a label
    deliberately distinct from "assistant_minor" so `Block.n_minor_updates`
    (a count of non-checkpoint PROSE rows) is never inflated by a
    content-free usage ping.
    """

    marker: str
    timestamp: str
    elapsed_since_prev_s: float | None
    kind: str  # "operator" | "qa" | "lifecycle" | "checkpoint" | "assistant_minor" | "thinking_only" | "api_call"
    text_preview: str  # first 80 chars of whatever text this row carries, for a human-readable view
    words: int
    input_tokens: int
    cache_creation_tokens: int
    cache_creation_1h_tokens: int
    cache_creation_5m_tokens: int
    cache_read_tokens: int
    output_tokens: int
    thinking_tokens: int
    cost_usd: float | None = None  # opencode only today -- see module docstring
    model: str | None = None
    effort: str | None = None
    tools_called: list[str] = field(default_factory=list)
    checkpoint_score: float | None = None
    is_real_lifecycle: bool = False
    compact_trigger: str | None = None
    compact_pre_tokens: int | None = None
    compact_post_tokens: int | None = None
    compact_duration_ms: int | None = None
    # profile name -> cumulative kept-word-count of select()'s walk if it
    # had been triggered starting from the newest row down to (and
    # including) this one; None if this row would not have survived under
    # that profile at all.
    profile_running_words: dict[str, int | None] = field(default_factory=dict)

    @property
    def context_size(self) -> int:
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens


@dataclass
class Block:
    """One span between prompt boundaries -- a human "turn" of the session:
    an OPERATOR_TEXT/QA_PAIR row (or the very start of the file) through the
    row right before the NEXT OPERATOR_TEXT/QA_PAIR/LIFECYCLE_MARKER row.
    """

    trigger_text_preview: str  # verbatim preview of whatever opened this block -- see module docstring
    trigger_kind: str
    start_ts: str
    end_ts: str
    n_calls: int
    n_checkpoints: int
    n_minor_updates: int  # non-checkpoint ASSISTANT_TEXT rows -- "mini prose status updates"
    tool_call_counts: dict[str, int]
    sum_input_tokens: int
    sum_cache_creation_tokens: int
    sum_cache_read_tokens: int
    sum_output_tokens: int
    sum_cost_usd: float  # opencode only today -- 0.0 for formats with no precomputed cost
    peak_context_size: int
    contains_real_lifecycle_marker: bool


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _claude_code_raw_scan(path: Path) -> list[dict]:
    """One pass over the raw JSONL, unfiltered by record type -- mirrors
    `adapters/claude_code.py._load_records` but keeps every record (incl.
    `mode`/bookkeeping types this package's extractor ignores) since a
    usage/cost view cares about call cadence, not just kept prose.
    """
    records: list[dict] = []
    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _event_kind_str(ev: NormalizedEvent, checkpoint_threshold: float) -> str:
    if ev.kind is EventKind.OPERATOR_TEXT:
        return "operator"
    if ev.kind is EventKind.QA_PAIR:
        return "qa"
    if ev.kind is EventKind.LIFECYCLE_MARKER:
        return "lifecycle"
    if ev.kind is EventKind.THINKING:
        return "thinking_only"
    if (ev.checkpoint_score or 0.0) >= checkpoint_threshold:
        return "checkpoint"
    return "assistant_minor"


def _simulate_profile(events: list[NormalizedEvent], config: ExtractConfig) -> dict[str, int]:
    """Duplicates select()'s own walk (newest -> oldest), instrumented to
    record the CUMULATIVE kept-word-count at every marker instead of only
    the final kept list -- select.py itself stays untouched/uncoupled from
    this analysis feature. Kept logic must track select.select() exactly;
    if that function's windowing rules change, this needs the same change.
    """
    running: dict[str, int] = {}
    checkpoints_found = 0
    word_count = 0
    markers_passed = 0

    for ev in reversed(events):
        if ev.kind is EventKind.LIFECYCLE_MARKER:
            running[ev.marker] = word_count
            may_pass = config.max_lifecycle_markers == -1 or markers_passed < config.max_lifecycle_markers
            if not may_pass:
                break
            markers_passed += 1
            continue

        if ev.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR):
            word_count += len(ev.text.split())
            running[ev.marker] = word_count

        elif ev.kind in (EventKind.ASSISTANT_TEXT, EventKind.THINKING):
            is_checkpoint = (
                ev.kind is EventKind.ASSISTANT_TEXT
                and (ev.checkpoint_score or 0.0) >= config.checkpoint_score_threshold
            )
            if is_checkpoint:
                checkpoints_found += 1
                word_count += len(ev.text.split())
                running[ev.marker] = word_count
                if checkpoints_found >= config.max_checkpoints:
                    break
            elif len(ev.text) > config.long_comment_chars or classifier.has_finding_signal(ev.text):
                word_count += len(ev.text.split())
                running[ev.marker] = word_count

        if word_count > config.max_words:
            break

    return running


def build_call_rows(path: Path, fmt: str | None = None, session_id: str | None = None) -> list[CallRow]:
    """Claude Code, Codex, and opencode -- see module docstring for the real
    per-format usage-ledger shape each dispatches to. Raises
    NotImplementedError for any other detected format rather than silently
    returning nothing.
    """
    path = Path(path)
    resolved_fmt = fmt or detect(path).name

    if resolved_fmt == "claude-code":
        return _build_call_rows_claude_code(path, session_id)
    if resolved_fmt == "codex":
        return _build_call_rows_codex(path, session_id)
    if resolved_fmt == "opencode":
        return _build_call_rows_opencode(path, session_id)
    raise NotImplementedError(
        f"session-stats does not support {resolved_fmt!r} -- see stats.py's module docstring"
    )


def _build_call_rows_claude_code(path: Path, session_id: str | None) -> list[CallRow]:
    sessions = claude_code.list_sessions(path)
    if session_id is None:
        session_id = sessions[0] if len(sessions) == 1 else str(path)

    config = ExtractConfig()
    events = claude_code.parse(path, session_id, config)
    classifier.score_events(events)
    checkpoint_threshold = config.checkpoint_score_threshold

    profile_running = {name: _simulate_profile(events, cfg) for name, cfg in PROFILES.items()}

    raw_by_marker: dict[str, dict] = {}
    for i, rec in enumerate(_claude_code_raw_scan(path)):
        marker = rec.get("uuid") or f"line{i}"
        raw_by_marker[marker] = rec

    rows: list[CallRow] = []
    prev_ts: datetime | None = None

    for ev in events:
        rec = raw_by_marker.get(ev.marker, {})
        msg = rec.get("message") or {}
        usage = msg.get("usage") or {}
        cache_creation = usage.get("cache_creation") or {}
        content = msg.get("content") or []
        tools_called = [
            b.get("name", "") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]

        ts = _parse_ts(ev.timestamp)
        elapsed = (ts - prev_ts).total_seconds() if ts and prev_ts else None
        if ts:
            prev_ts = ts

        compact_meta = rec.get("compactMetadata") or {}

        row = CallRow(
            marker=ev.marker,
            timestamp=ev.timestamp,
            elapsed_since_prev_s=elapsed,
            kind=_event_kind_str(ev, checkpoint_threshold),
            text_preview=ev.text[:80].replace("\n", " "),
            words=len(ev.text.split()),
            input_tokens=usage.get("input_tokens", 0) or 0,
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
            cache_creation_1h_tokens=cache_creation.get("ephemeral_1h_input_tokens", 0) or 0,
            cache_creation_5m_tokens=cache_creation.get("ephemeral_5m_input_tokens", 0) or 0,
            cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            thinking_tokens=(usage.get("output_tokens_details") or {}).get("thinking_tokens", 0) or 0,
            model=msg.get("model"),
            effort=rec.get("effort"),
            tools_called=tools_called,
            checkpoint_score=ev.checkpoint_score,
            is_real_lifecycle=ev.kind is EventKind.LIFECYCLE_MARKER and bool(compact_meta),
            compact_trigger=compact_meta.get("trigger"),
            compact_pre_tokens=compact_meta.get("preTokens"),
            compact_post_tokens=compact_meta.get("postTokens"),
            compact_duration_ms=compact_meta.get("durationMs"),
            profile_running_words={name: profile_running[name].get(ev.marker) for name in PROFILES},
        )
        rows.append(row)

    return rows


def _codex_raw_scan(path: Path) -> list[dict]:
    """Mirrors adapters/codex.py's own top-level-type filter (`event_msg`,
    `compacted`) -- NOT every raw JSONL record the way
    `_claude_code_raw_scan` is (response_item/turn_context/world_state/
    session_meta are excluded, exactly as codex.py's own `parse()` excludes
    them before it enumerates for its ordinal fallback). Kept in that same
    {event_msg, compacted} universe so a `str(rec.get("ordinal", i))`-style
    marker computed here lands on the EXACT same record codex.py's `parse()`
    would number it as -- diverging from that filter would silently
    misattribute every token_count row to the wrong nearby turn, not just
    miss a few.
    """
    from .adapters.codex import _TOP_LEVEL_TYPES

    raw: list[dict] = []
    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") in _TOP_LEVEL_TYPES:
                raw.append(obj)
    return raw


def _codex_session_model(path: Path) -> str | None:
    """The `turn_context.payload.model` of the FIRST turn_context record in
    the file -- `turn_context` lives outside the event_msg/compacted
    universe `_codex_raw_scan` reads, so this is a small separate pass.
    Stamped uniformly on every api_call row; a real session that switches
    models mid-way (Codex allows this) would not have that switch reflected
    here -- an honest simplification, not a claim of per-call fidelity.
    """
    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "turn_context":
                model = (obj.get("payload") or {}).get("model")
                if model:
                    return model
    return None


def _build_call_rows_codex(path: Path, session_id: str | None) -> list[CallRow]:
    """Codex: unlike Claude Code, usage isn't attached to the text-bearing
    record itself -- `event_msg.payload.type == "token_count"` is a
    separately-emitted, running/cumulative counter. A CallRow is built per
    REAL occurrence in file order: once for each kept NormalizedEvent
    (operator/assistant/lifecycle -- zero usage, Codex's own record for
    these carries none) and once more for each token_count event (all the
    usage, no text; `kind="api_call"`). `build_blocks` (untouched, generic)
    then naturally attributes every api_call row to whichever block the
    nearest PRECEDING operator/qa/lifecycle boundary row opened -- this is
    the "correlate a token_count event with the nearest preceding real
    turn" behavior, achieved by construction (both kinds of row share one
    time-ordered walk, sorted by the same marker/ordinal space) rather than
    by tracking a separate "current context" pointer.

    Real-data findings (against 400+ local rollout files spanning
    cli_version 0.142.2-0.151.0, both schema generations):

    - `last_token_usage` (not `total_token_usage`, which is a LIFETIME
      cumulative counter that does NOT reset or drop across a real
      compaction -- confirmed directly: `total_token_usage.input_tokens`
      either side of a real `compacted` record was IDENTICAL, 4,457,700
      both times) is the per-call delta this function reads.
    - `last_token_usage.input_tokens` already INCLUDES `cached_input_tokens`
      (and, by the same reasoning, `cache_write_input_tokens`) as a SUBSET,
      not an additive component -- confirmed via `total_tokens ==
      input_tokens + output_tokens` holding exactly across thousands of
      real records (a small fraction of records report all-zero component
      fields with only `total_tokens` nonzero -- handled by flooring at 0,
      not by trusting the mismatch). Mapping `cached_input_tokens`/
      `cache_write_input_tokens` directly onto CallRow's
      `cache_read_tokens`/`cache_creation_tokens` WITHOUT subtracting them
      back out of `input_tokens` first would silently double-count them
      into `context_size` (input+cache_creation+cache_read) -- this
      function subtracts both out, mirroring how Claude Code's OWN usage
      block is already additively split. (opencode, by contrast, IS
      natively additive -- see `_build_call_rows_opencode`; this
      subset-vs-additive split is a genuine per-CLI schema difference, not
      an inconsistency in this module.)
    - The `compacted` record (NEW generation) does NOT carry a `trigger`/
      `preTokens`/`postTokens`/`durationMs` the way Claude Code's
      `compactMetadata` does -- only `window_number`/`window_id`/
      `previous_window_id`/`first_window_id`/`message`/`replacement_history`.
      `compact_trigger`/`compact_pre_tokens`/`compact_post_tokens`/
      `compact_duration_ms` are therefore always None for Codex rows.
    - **Correction to adapters/codex.py's own module docstring**, found
      while validating this function against real files: it describes
      `payload.message` as "Codex's own real compaction summary text", but
      across 132 real `compacted` records in local history, 120 (91%) carry
      an EMPTY `message` -- the real replacement content for an ordinary
      auto-compaction lives in `replacement_history[-1]` as `{"type":
      "compaction", "encrypted_content": ...}`, genuinely encrypted, not
      plain text. The 12 non-empty-`message` records found all belong to a
      DIFFERENT boundary (a session resumed with another model's summary
      injected -- `window_id` discontinuous from the prior record's
      `previous_window_id`), not a routine auto-compaction. codex.py's own
      code already falls back safely (`payload.get("message") or
      "[compacted]"`), so behavior is unaffected -- only the docstring's
      characterization overclaimed. Not fixed here (out of this function's
      file); flagged for a follow-up doc correction.
    """
    from .adapters import codex as codex_adapter

    if session_id is None:
        session_id = str(path)

    config = ExtractConfig()
    events = codex_adapter.parse(path, session_id, config)
    classifier.score_events(events)
    checkpoint_threshold = config.checkpoint_score_threshold
    profile_running = {name: _simulate_profile(events, cfg) for name, cfg in PROFILES.items()}

    raw_records = _codex_raw_scan(path)
    session_model = _codex_session_model(path)
    events_by_marker = {ev.marker: ev for ev in events}

    # Merge every kept NormalizedEvent with every token_count raw record
    # (which never coincides with an existing marker -- token_count carries
    # no text, so codex.parse() never emits an event for it; the check
    # below is defensive, not load-bearing), sorted by the shared
    # ordinal/absolute-index marker space both sides come from. The marker
    # string is computed ONCE here and carried through the tuple -- NOT
    # recomputed in the row-building loop below, which has no access to
    # `i` (the raw_records enumeration index) and would otherwise need an
    # arbitrary, WRONG default (an earlier version of this function
    # defaulted to the literal string "0", silently mislabeling every
    # token_count row lacking a real `ordinal` field with the same marker).
    items: list[tuple[int, str, object]] = [(int(ev.marker), ev.marker, ev) for ev in events]
    for i, rec in enumerate(raw_records):
        if rec.get("type") != "event_msg":
            continue
        if (rec.get("payload") or {}).get("type") != "token_count":
            continue
        marker = str(rec.get("ordinal", i))
        if marker in events_by_marker:
            continue
        items.append((int(marker), marker, rec))
    items.sort(key=lambda t: t[0])

    rows: list[CallRow] = []
    prev_ts: datetime | None = None

    for _, marker, item in items:
        if isinstance(item, NormalizedEvent):
            ev = item
            ts = _parse_ts(ev.timestamp)
            elapsed = (ts - prev_ts).total_seconds() if ts and prev_ts else None
            if ts:
                prev_ts = ts
            rows.append(CallRow(
                marker=ev.marker,
                timestamp=ev.timestamp,
                elapsed_since_prev_s=elapsed,
                kind=_event_kind_str(ev, checkpoint_threshold),
                text_preview=ev.text[:80].replace("\n", " "),
                words=len(ev.text.split()),
                input_tokens=0,
                cache_creation_tokens=0,
                cache_creation_1h_tokens=0,
                cache_creation_5m_tokens=0,
                cache_read_tokens=0,
                output_tokens=0,
                thinking_tokens=0,
                model=None,
                effort=None,
                tools_called=[],
                checkpoint_score=ev.checkpoint_score,
                is_real_lifecycle=ev.kind is EventKind.LIFECYCLE_MARKER,
                profile_running_words={name: profile_running[name].get(ev.marker) for name in PROFILES},
            ))
        else:
            rec = item
            info = (rec.get("payload") or {}).get("info") or {}
            last = info.get("last_token_usage") or {}
            cached = last.get("cached_input_tokens", 0) or 0
            cache_write = last.get("cache_write_input_tokens", 0) or 0
            raw_input = last.get("input_tokens", 0) or 0
            fresh_input = max(0, raw_input - cached - cache_write)

            ts = _parse_ts(rec.get("timestamp", ""))
            elapsed = (ts - prev_ts).total_seconds() if ts and prev_ts else None
            if ts:
                prev_ts = ts

            rows.append(CallRow(
                marker=marker,
                timestamp=rec.get("timestamp", ""),
                elapsed_since_prev_s=elapsed,
                kind="api_call",
                text_preview="",
                words=0,
                input_tokens=fresh_input,
                cache_creation_tokens=cache_write,
                cache_creation_1h_tokens=0,
                cache_creation_5m_tokens=0,
                cache_read_tokens=cached,
                output_tokens=last.get("output_tokens", 0) or 0,
                thinking_tokens=last.get("reasoning_output_tokens", 0) or 0,
                model=session_model,
                effort=None,
                tools_called=[],
                checkpoint_score=None,
                is_real_lifecycle=False,
                profile_running_words={name: None for name in PROFILES},
            ))

    return rows


def _opencode_raw_scan(db_path: Path, session_id: str) -> list[dict]:
    """Every message row's raw `data` dict (plus its own `id` under `_id`)
    for one opencode session, both roles -- a SEPARATE sqlite3
    connection/query from adapters/opencode.py's own `parse()` (this
    module's usual "don't share code with the thing it's meant to help
    judge" convention, matching `_claude_code_raw_scan`'s own independence
    from adapters/claude_code.py), though the query shape itself mirrors
    that adapter's `SELECT ... FROM message WHERE session_id = ?` exactly.
    """
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, data FROM message WHERE session_id = ?", (session_id,)
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for msg_id, data_json in rows:
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        data["_id"] = msg_id
        out.append(data)
    return out


def _build_call_rows_opencode(path: Path, session_id: str | None) -> list[CallRow]:
    """opencode: unlike Codex, usage is embedded DIRECTLY on the assistant
    message's own `data` (`tokens`/`cost`/`time`) -- the same "usage on the
    record itself" shape Claude Code already has -- so this mirrors
    `_build_call_rows_claude_code` closely: one CallRow per NormalizedEvent
    (from adapters/opencode.py's own `parse()`, keyed by its `marker`,
    which IS the real `message.id` -- opencode's marker is never a
    positional fallback), usage looked up from the raw message row by that
    same id.

    Real-data findings (against a real local `opencode.db`, 4,455 message
    rows across 72 sessions):
    - `tokens`/`cost` keys are ALWAYS present on an assistant row (0/0.0 for
      an aborted/errored call, never a missing key) -- safe to `.get(...)`
      without a fallback-shape guess.
    - `tokens.{input,output,reasoning,cache.read,cache.write}` sum to
      `tokens.total` (confirmed on a real nonzero-cost row: 620+0+4+6805+0
      == 7429 ~= total 7428) -- ADDITIVE, unlike Codex's subset-inclusive
      `input_tokens` (see `_build_call_rows_codex`'s own docstring). No
      subtraction needed here; direct field mapping is correct.
    - `time.completed` is missing on 8/4,455 real assistant rows (an
      in-flight/interrupted call) -- `elapsed_since_prev_s` falls back to
      the generic timestamp-diff used by every other format for those rows.
    - A message with only tool-call/error parts (no `part` row of
      `type=="text"`) never becomes a NormalizedEvent at all
      (adapters/opencode.py's own `parse()` drops it) and therefore never
      gets a CallRow here -- a real, precedented limitation this module
      ALREADY has for Claude Code (a tool-use-only assistant record, no
      text block, is likewise dropped there), not a new gap introduced for
      opencode.

    A session's own real per-call latency (`time.completed - time.created`)
    is used for `elapsed_since_prev_s` on assistant rows directly, rather
    than diffing against the previous row's timestamp the way every other
    format here has to -- genuinely more accurate than "timestamp-diffing"
    for exactly the row where it matters, per E-009's own callout.
    """
    from .adapters import opencode as opencode_adapter

    db_path = opencode_adapter._db_path(Path(path))
    if db_path is None:
        raise ValueError(f"{path} is not an opencode SQLite store")

    if session_id is None:
        sessions = opencode_adapter.list_sessions(path)
        if len(sessions) == 1:
            session_id = sessions[0]
        elif not sessions:
            raise ValueError(f"{path}: no opencode sessions found")
        else:
            raise ValueError(
                f"{path} holds {len(sessions)} opencode sessions; pass session_id "
                f"(e.g. {sessions[0]!r}) -- session-stats' --session flag selects it"
            )

    config = ExtractConfig()
    events = opencode_adapter.parse(path, session_id, config)
    classifier.score_events(events)
    checkpoint_threshold = config.checkpoint_score_threshold
    profile_running = {name: _simulate_profile(events, cfg) for name, cfg in PROFILES.items()}

    raw_by_marker = {rec["_id"]: rec for rec in _opencode_raw_scan(db_path, session_id)}

    rows: list[CallRow] = []
    prev_ts: datetime | None = None

    for ev in events:
        rec = raw_by_marker.get(ev.marker, {})
        tokens = rec.get("tokens") or {}
        cache = tokens.get("cache") or {}
        time_info = rec.get("time") or {}

        ts = _parse_ts(ev.timestamp)
        real_latency = None
        if rec.get("role") == "assistant" and "completed" in time_info and "created" in time_info:
            real_latency = (time_info["completed"] - time_info["created"]) / 1000.0
        elapsed = real_latency if real_latency is not None else (
            (ts - prev_ts).total_seconds() if ts and prev_ts else None
        )
        if ts:
            prev_ts = ts

        rows.append(CallRow(
            marker=ev.marker,
            timestamp=ev.timestamp,
            elapsed_since_prev_s=elapsed,
            kind=_event_kind_str(ev, checkpoint_threshold),
            text_preview=ev.text[:80].replace("\n", " "),
            words=len(ev.text.split()),
            input_tokens=tokens.get("input", 0) or 0,
            cache_creation_tokens=cache.get("write", 0) or 0,
            cache_creation_1h_tokens=0,
            cache_creation_5m_tokens=0,
            cache_read_tokens=cache.get("read", 0) or 0,
            output_tokens=tokens.get("output", 0) or 0,
            thinking_tokens=tokens.get("reasoning", 0) or 0,
            cost_usd=rec.get("cost"),
            model=rec.get("modelID"),
            effort=rec.get("variant"),
            tools_called=[],
            checkpoint_score=ev.checkpoint_score,
            is_real_lifecycle=False,  # opencode never emits LIFECYCLE_MARKER -- adapters/opencode.py's own honest gap
            profile_running_words={name: profile_running[name].get(ev.marker) for name in PROFILES},
        ))

    return rows


_BOUNDARY_KINDS = {"operator", "qa", "lifecycle"}


def build_blocks(rows: list[CallRow]) -> list[Block]:
    """Groups rows into spans between prompt boundaries. A block always
    STARTS at a boundary row (operator/qa/lifecycle) and runs through every
    row up to (not including) the next boundary row -- so a block reads as
    "what happened in response to this one trigger." A non-boundary row
    (checkpoint/assistant_minor/thinking_only/api_call) always folds into
    whichever block is currently open, which is how a Codex `api_call` row
    ends up correlated with the nearest preceding real turn without any
    extra bookkeeping -- see `_build_call_rows_codex`'s own docstring.
    """
    blocks: list[Block] = []
    current: list[CallRow] = []

    def flush() -> None:
        if not current:
            return
        trigger = current[0]
        tool_counts: dict[str, int] = {}
        for r in current:
            for t in r.tools_called:
                tool_counts[t] = tool_counts.get(t, 0) + 1
        blocks.append(Block(
            trigger_text_preview=trigger.text_preview,
            trigger_kind=trigger.kind,
            start_ts=current[0].timestamp,
            end_ts=current[-1].timestamp,
            n_calls=len(current),
            n_checkpoints=sum(1 for r in current if r.kind == "checkpoint"),
            n_minor_updates=sum(1 for r in current if r.kind == "assistant_minor"),
            tool_call_counts=tool_counts,
            sum_input_tokens=sum(r.input_tokens for r in current),
            sum_cache_creation_tokens=sum(r.cache_creation_tokens for r in current),
            sum_cache_read_tokens=sum(r.cache_read_tokens for r in current),
            sum_output_tokens=sum(r.output_tokens for r in current),
            sum_cost_usd=sum((r.cost_usd or 0.0) for r in current),
            peak_context_size=max((r.context_size for r in current), default=0),
            contains_real_lifecycle_marker=any(r.is_real_lifecycle for r in current),
        ))

    for row in rows:
        if row.kind in _BOUNDARY_KINDS and current:
            flush()
            current = [row]
        else:
            current.append(row)
    flush()

    return blocks


def render_detailed_csv(rows: list[CallRow]) -> str:
    """One line per real API call. Profile columns show the cumulative
    kept-word-count select() would report if extraction had been triggered
    starting from the newest row down to this one, under that profile --
    blank if this row wouldn't have survived that profile's bar at all.
    """
    import csv
    import io

    profile_names = sorted(PROFILES)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "marker", "timestamp", "elapsed_since_prev_s", "kind", "text_preview", "words",
        "input_tokens", "cache_creation_tokens", "cache_creation_1h_tokens",
        "cache_creation_5m_tokens", "cache_read_tokens", "output_tokens", "thinking_tokens",
        "cost_usd", "context_size", "model", "effort", "tools_called", "checkpoint_score",
        "is_real_lifecycle", "compact_trigger", "compact_pre_tokens", "compact_post_tokens",
        "compact_duration_ms",
    ] + [f"profile_{p}_words" for p in profile_names])
    for r in rows:
        w.writerow([
            r.marker, r.timestamp,
            f"{r.elapsed_since_prev_s:.1f}" if r.elapsed_since_prev_s is not None else "",
            r.kind, r.text_preview, r.words,
            r.input_tokens, r.cache_creation_tokens, r.cache_creation_1h_tokens,
            r.cache_creation_5m_tokens, r.cache_read_tokens, r.output_tokens, r.thinking_tokens,
            f"{r.cost_usd:.6f}" if r.cost_usd is not None else "",
            r.context_size, r.model or "", r.effort or "", ";".join(r.tools_called),
            f"{r.checkpoint_score:.2f}" if r.checkpoint_score is not None else "",
            r.is_real_lifecycle, r.compact_trigger or "", r.compact_pre_tokens or "",
            r.compact_post_tokens or "", r.compact_duration_ms or "",
        ] + [
            r.profile_running_words.get(p) if r.profile_running_words.get(p) is not None else ""
            for p in profile_names
        ])
    return out.getvalue()


def render_condensed(blocks: list[Block]) -> str:
    """One line per block, meant to fit a whole session's shape on one page.
    A LIFECYCLE_MARKER-triggered block gets its own visible divider, since
    that's the one boundary this package treats as structurally different
    from an ordinary prompt.
    """
    lines = [
        f"{'#':>3}  {'trigger':<9} {'calls':>5} {'cp':>3} {'minor':>5} "
        f"{'in':>8} {'cache_w':>8} {'cache_r':>8} {'out':>7} {'cost_usd':>9} {'peak_ctx':>9}  trigger text",
        "-" * 120,
    ]
    for i, b in enumerate(blocks, 1):
        if b.contains_real_lifecycle_marker:
            lines.append(f"{'':>3}  {'=' * 114}  REAL COMPACTION")
        marker = "**" if b.trigger_kind == "lifecycle" else ""
        lines.append(
            f"{i:>3}  {b.trigger_kind:<9} {b.n_calls:>5} {b.n_checkpoints:>3} {b.n_minor_updates:>5} "
            f"{b.sum_input_tokens:>8} {b.sum_cache_creation_tokens:>8} {b.sum_cache_read_tokens:>8} "
            f"{b.sum_output_tokens:>7} {b.sum_cost_usd:>9.4f} {b.peak_context_size:>9}  {marker}{b.trigger_text_preview}"
        )
    totals = Block(
        trigger_text_preview="TOTAL", trigger_kind="", start_ts="", end_ts="",
        n_calls=sum(b.n_calls for b in blocks),
        n_checkpoints=sum(b.n_checkpoints for b in blocks),
        n_minor_updates=sum(b.n_minor_updates for b in blocks),
        tool_call_counts={},
        sum_input_tokens=sum(b.sum_input_tokens for b in blocks),
        sum_cache_creation_tokens=sum(b.sum_cache_creation_tokens for b in blocks),
        sum_cache_read_tokens=sum(b.sum_cache_read_tokens for b in blocks),
        sum_output_tokens=sum(b.sum_output_tokens for b in blocks),
        sum_cost_usd=sum(b.sum_cost_usd for b in blocks),
        peak_context_size=max((b.peak_context_size for b in blocks), default=0),
        contains_real_lifecycle_marker=False,
    )
    lines.append("-" * 120)
    lines.append(
        f"{'':>3}  {'':<9} {totals.n_calls:>5} {totals.n_checkpoints:>3} {totals.n_minor_updates:>5} "
        f"{totals.sum_input_tokens:>8} {totals.sum_cache_creation_tokens:>8} "
        f"{totals.sum_cache_read_tokens:>8} {totals.sum_output_tokens:>7} {totals.sum_cost_usd:>9.4f} "
        f"{totals.peak_context_size:>9}  ({len(blocks)} blocks)"
    )
    return "\n".join(lines) + "\n"
