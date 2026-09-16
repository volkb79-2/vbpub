"""`--follow`/`-f`: keep printing a session's new content as it is written,
with an attention hook so a busy pane can page you when it needs input.

Two phases, and only the second one lives here:

  - **Phase 1** is exactly today's one-shot run of whichever verb was
    invoked -- `extract`'s full parse -> score -> backward walk, or
    `extract-lossless`'s dump -- printed unchanged. cli.py records the
    file's size (or the newest DB row) BEFORE that pass and hands it here.
  - **Phase 2**, this module: incremental tailing from that point, applying
    THAT VERB'S OWN per-record/per-event rules to each newly-arrived record
    (see select.decide, adapters' parse_record, lossless's *_blocks -- all
    factored out for exactly this, so a live stream and a one-shot brief can
    never drift on what counts as content).

**Genuinely incremental, which was a real bug in this feature's first
design.** That draft would have called `lossless.dump_claude_code` once per
poll tick; that function opens the file and iterates from byte 0 every time,
using `since_marker` only to decide when to start EMITTING. Against a
growing 50MB+ session log that rescans the entire file every second. What
`tail -f` actually does, and what JsonlTailer does here: keep the handle and
the byte offset, `stat()` for a size change (no read at all when unchanged),
`seek()` and read ONLY the new bytes when it grows, hold back a trailing
partial line for the next tick. `JsonlTailer.bytes_read` exists as that
bug's regression witness -- a test asserts it stays at the appended size
instead of the whole file's.

**The lookahead problem, and the one-event delay.** `select()`'s checkpoint
rule depends on `classifier.score_events`'s "followed by a pause" bonus
(+2.0 when the next real event is an operator prompt or Q&A), which is
unknowable for the newest arrival: the pause has not happened yet. So
FollowSelector holds an ASSISTANT_TEXT back until a DECISIVE next event
arrives and the true score is known -- but only when the verdict actually
hinges on it. Three cases, and only the third ever waits:

  1. shape score alone already clears the threshold -> it is a checkpoint no
     matter what follows (the bonus only ever ADDS), so emit immediately.
  2. long enough, or a concrete-finding signal -> kept regardless of score,
     so emit immediately. The pending SCORE stays open, and if the bonus
     later pushes it over the threshold the checkpoint ATTENTION fires then
     -- attention is a notification, not output order, so arriving a beat
     late costs nothing while a delayed line on screen would.
  3. short, no finding signal, sub-threshold shape -> whether it is kept at
     all depends entirely on the bonus. This one waits for the next decisive
     event, which is inherent to the rule, not an approximation of it.

This is a deliberate refinement of the design's "hold exactly one pending
event" instruction: identical keep/drop outcomes to `select()` in every case
(cases 1 and 2 are provably verdict-stable), but a checkpoint at the end of
a turn shows up NOW rather than whenever the session next moves -- which
matters, because "the model just hit a checkpoint" is half of what this
feature exists to tell you. A THINKING event arriving behind a still-pending
one is held in order behind it (score_events' own pause scan skips THINKING,
so it is not decisive either) -- the design's one-event framing did not
cover that, and emitting it immediately would reorder the stream.

On exit, a case-3 event still waiting is dropped, not flushed: we never saw
the event its verdict depended on, and guessing "no pause" could print
something `select()` would have kept (or the reverse).

**Attention detection** -- three independent signals, each with a typed
reason:

  1. `interview_pending` -- **Claude Code only**, and the only structural
     one: an `AskUserQuestion` tool_use with no matching tool_result yet
     (adapters/claude_code.update_interview_pending, reusing that adapter's
     own tool_use/tool_result pairing). **Documented gap**: no equivalent
     exists in Codex's, Reasonix's, or opencode's schema as currently understood -- see
     both adapters' own "Known gaps" notes. Not guessed at.
  2. `checkpoint_detected` -- all adapters. Under `extract --follow` this is
     the real scored decision above. Under `extract-lossless --follow` there
     is no scoring at all by design, so it falls back to
     `classifier.shape_score(text)` alone (the same public helper
     extract-debug uses) against every new block -- a weaker signal,
     honestly: no pause bonus, and it also sees operator/thinking text,
     which the scored path never scores.
  3. `long_block` -- opt-in via `--attention-min-chars N`, off by default.
     Any new assistant/thinking block over N chars under `extract`; under
     `extract-lossless`, any new block at all over N chars, since that verb
     deliberately does no kind classification.

"Turn end" is not a distinct marker in any adapter's schema, so it is not a
fourth signal; the practical proxy ("the file stopped growing") is just this
loop's own idle state.

**Delivery** is orthogonal to detection: `--bell` writes `\\a` to stderr
(a bell is a notification, not session content -- piping the stream onward
must not carry stray bell bytes),
`--on-attention '<cmd>'` runs a command with `NYXLOOM_ATTENTION_REASON` /
`_HARNESS` / `_SESSION_PATH` / `_EXCERPT` in its environment (your script
decides whether that reaches Telegram, Mattermost or nothing -- nyxloom
holds no credentials for it), and `--notify-project NAME` pushes through
that project's already-configured notify channel. That last one is a
**conscious, scoped exception** to notify.py's SPEC §13 rule that a
notification body is built only from fixed templates over typed fields:
this body carries the flagged text's first ~100 characters, per explicit
operator direction on what the payload may say. Recorded as a departure
rather than quietly done.

**opencode has no byte-offset problem to solve** -- "what's new" is an
indexed query on `(time_created, id)`. It has a different one: a message row
is created when a turn starts and its `part` rows stream in afterwards, so a
row read the instant it appears can have no text yet, or only some of it --
and a cursor advanced past it would lose that prose permanently. So
OpencodeSource holds back the NEWEST row every tick and re-reads it next
time, committing the cursor to rows that a newer sibling proves are
finished. When a session has no newer sibling, two unchanged observations
are the only available end signal, so a stable final row is emitted rather
than held forever. Same shape of trade as the one-event delay above, for the
same reason.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import classifier, mangle, render
from .adapters import claude_code, codex, opencode, reasonix
from .config import ExtractConfig
from .events import EventKind, NormalizedEvent
from .lossless import (
    LosslessBlock,
    claude_code_blocks,
    codex_blocks,
    opencode_block_for_texts,
    opencode_blocks,
    reasonix_blocks,
)
from .select import decide

#: How much of the flagged text an attention payload carries.
EXCERPT_CHARS = 100

#: Poll interval when the file/DB has not changed. A session log grows in
#: bursts of one human/model turn, so sub-second polling buys nothing.
DEFAULT_INTERVAL_S = 1.0


class FollowSourceError(RuntimeError):
    """The source of an active follow stream became unreadable.

    A source that has not been opened yet may legitimately be absent: this is
    how a follow started just before a new session file is created waits for
    startup.  Once the tailer owns an open handle, however, an unavailable
    path or failed metadata read is indeterminate, not an empty poll.  This
    typed error makes that distinction visible to the CLI and callers.
    """

# A late part is expected while a turn is still live, not years after the
# session ended. Keep a bounded recent-row view so that checking for that
# late part remains indexed point lookups rather than a whole-database scan.
# The phase-one anchor and the newest settled rows share this same window.
OPENCODE_TRACKED_ROWS = 128


class JsonlTailer:
    """`tail -f` for an append-only JSONL: handle + byte offset, read only
    what is new. See the module docstring for the rescan bug this replaces.

    The committed offset only ever advances past COMPLETE lines; a trailing
    partial line (a writer caught mid-flush) is left behind and re-read on
    the next tick rather than buffered in memory. Re-reading it costs one
    line, and it buys a checkable invariant -- our own offset always sits
    immediately after a newline -- which is what makes rewrite detection
    below actually work.

    Three ways the file can change out from under us, all guarded: the inode
    changes (rotation/replacement), the file shrinks below our offset
    (truncation), or it was truncated AND regrown past our offset between two
    ticks. The last case is checked with a bounded prefix fingerprint as well
    as the boundary newline: a same-inode rewrite can preserve the old
    boundary newline while replacing the bytes before it. None of this is
    expected for these harness logs, which are append-only, but the failure
    mode without it is silent garbage rather than an error.
    """

    def __init__(self, path: Path, offset: int = 0):
        self.path = Path(path)
        self.offset = offset
        #: Total bytes actually read from the file by this tailer. Exists as
        #: the regression witness for the "rescans from byte 0 every tick"
        #: bug -- a test asserts it equals the APPENDED size, not the file's.
        self.bytes_read = 0
        self._handle = None
        self._inode: int | None = None
        self._stat_signature: tuple[int, int, int, int] | None = None
        self._prefix_fingerprint: tuple[bytes, bytes] | None = None
        # The caller's starting offset is a plain file size (cli.py's
        # _follow_anchor), so it CAN sit mid-line if the harness was writing
        # at that moment; only offsets this tailer committed itself satisfy
        # the newline invariant, so only those are validated. A mid-line
        # start just yields one unparseable fragment, which JsonlSource drops.
        self._own_offset = False

    def _open(self) -> None:
        self._handle = self.path.open("rb")
        self._inode = os.fstat(self._handle.fileno()).st_ino

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _prefix_snapshot(self) -> tuple[bytes, bytes]:
        """Capture bounded fingerprints on both sides of the consumed prefix.

        The tailer must not re-read a large session on every unchanged poll,
        but checking only ``offset - 1`` lets a same-inode replacement hide
        behind a preserved newline. Two bounded samples catch ordinary
        rewrites without turning validation into another whole-file scan.
        """
        sample_size = 4096
        self._handle.seek(0)
        head = self._handle.read(min(self.offset, sample_size))
        tail_start = max(0, self.offset - sample_size)
        self._handle.seek(tail_start)
        tail = self._handle.read(self.offset - tail_start)
        return head, tail

    def _rewritten(self) -> bool:
        if not self._own_offset or self.offset == 0:
            return False
        if self._prefix_fingerprint is not None:
            return self._prefix_snapshot() != self._prefix_fingerprint
        self._handle.seek(self.offset - 1)
        return self._handle.read(1) != b"\n"

    @staticmethod
    def _stat_signature_for(st) -> tuple[int, int, int, int]:
        return st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns

    def poll(self) -> list[str]:
        """Every COMPLETE new line since the last poll."""
        try:
            st = self.path.stat()
        except FileNotFoundError as exc:
            # Before the first successful open, a missing path is a valid
            # startup state: the session producer may create it shortly.  An
            # active stream losing its path is different; returning [] would
            # report disappearance as "no new content" and wait forever.
            if self._handle is None:
                return []
            raise FollowSourceError(
                f"followed session file disappeared while following: {self.path}"
            ) from exc
        except OSError as exc:
            # Permission errors and other metadata I/O failures are equally
            # indeterminate after a stream exists.  Fail closed rather than
            # silently converting them into an idle poll.
            raise FollowSourceError(
                f"cannot stat followed session file while following {self.path}: {exc}"
            ) from exc

        if self._handle is None:
            self._open()
        signature = self._stat_signature_for(st)
        metadata_changed = signature != self._stat_signature
        # Checked on EVERY poll including the first, deliberately: the handle
        # having just been opened says nothing about whether the file still
        # matches the offset we were handed.
        if (st.st_ino != self._inode or st.st_size < self.offset
                or (metadata_changed and self._rewritten())):
            self.close()
            self.offset = 0
            self._own_offset = False
            self._prefix_fingerprint = None
            self._stat_signature = None
            self._open()

        if st.st_size <= self.offset:
            self._stat_signature = signature
            return []

        previous_offset = self.offset
        self._handle.seek(self.offset)
        chunk = self._handle.read()
        if not chunk:
            return []
        self.bytes_read += len(chunk)

        parts = chunk.split(b"\n")
        trailing = parts[-1]  # incomplete -- left for the next tick
        self.offset += len(chunk) - len(trailing)
        # A caller-supplied anchor may be mid-line. Reading only another
        # partial fragment must not turn that unverified offset into one of
        # our own newline-boundary offsets: on the next poll _rewritten()
        # would otherwise mistake the original line for a rewrite and replay
        # the whole file. Ownership starts only after a complete line moved
        # the offset forward.
        if self.offset > previous_offset:
            self._own_offset = True
            self._prefix_fingerprint = self._prefix_snapshot()
        self._stat_signature = signature
        return [p.decode("utf-8", "replace") for p in parts[:-1] if p.strip()]


@dataclass
class Arrival:
    """What one newly-arrived source record contributes, in whichever shape
    the invoking verb needs: `events` for `extract --follow` (selection
    applies), `blocks` for `extract-lossless --follow` (kept
    unconditionally). `raw` is the source record itself, for the
    interview-pending signal -- None where the harness has no such record
    (opencode).
    """

    events: list[NormalizedEvent] = field(default_factory=list)
    blocks: list[LosslessBlock] = field(default_factory=list)
    raw: dict | None = None


def _parse_line(line: str) -> dict | None:
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _prime_interview_pending(
    path: Path, upto_bytes: int, has_primary_thread: bool,
) -> dict[str, str]:
    """Reconstruct unanswered Claude questions before a follow anchor.

    ``prime_stream_state`` seeds the parser's question metadata, but its
    state is intentionally about formatting a later answer. Attention needs
    the separate unanswered-question view: replay the same raw records
    through the adapter's public pairing helper, stopping at the phase-one
    boundary. This is a startup-only read; subsequent polls remain tail-only.
    """
    pending: dict[str, str] = {}
    if upto_bytes <= 0:
        return pending
    consumed = 0
    try:
        handle = Path(path).open("rb")
    except OSError:
        return pending
    with handle:
        for raw_line in handle:
            next_consumed = consumed + len(raw_line)
            if next_consumed > upto_bytes:
                break
            consumed = next_consumed
            rec = _parse_line(raw_line.decode("utf-8", "replace"))
            if rec is None or not claude_code.is_conversation_record(rec):
                continue
            if rec.get("isSidechain") and has_primary_thread:
                continue
            claude_code.update_interview_pending(rec, pending)
    return pending


class JsonlSource:
    """New Claude Code / Codex / Reasonix records, byte-offset tailed.

    `seq` and a uuid-less/ordinal-less record's fallback marker are
    STREAM-LOCAL here (`follow<n>`), deliberately distinct from phase 1's
    own `lineN`/position fallbacks rather than a guess at continuing them:
    the tailer knows bytes, not how many records phase 1 read, and a
    colliding fallback would be worse than an obviously different one. Real
    records carry their own uuid/ordinal and are unaffected. Nothing in a
    live stream consumes these markers for --since chaining.
    """

    def __init__(
        self, path: Path, fmt: str, offset: int, config: ExtractConfig, lossless_mode: bool,
        has_primary_thread: bool = True,
    ):
        self.tailer = JsonlTailer(path, offset)
        self._fmt = fmt
        self._config = config
        self._lossless = lossless_mode
        self._seq = 0
        self._state = claude_code.StreamState(has_primary_thread=has_primary_thread)
        self.initial_interview_pending: dict[str, str] = {}
        if self._fmt == "claude-code":
            # The first live answer may refer to an AskUserQuestion whose
            # tool_use was already in phase 1. Rehydrate that small
            # cross-record state once; later polls remain tail-only.
            claude_code.prime_stream_state(path, self._state, offset)
            self.initial_interview_pending = _prime_interview_pending(
                path, offset, has_primary_thread,
            )

    def close(self) -> None:
        self.tailer.close()

    def poll(self) -> list[Arrival]:
        arrivals: list[Arrival] = []
        records: list[dict] = []
        for line in self.tailer.poll():
            rec = _parse_line(line)
            if rec is None:
                continue
            if self._fmt == "claude-code":
                if not claude_code.is_conversation_record(rec):
                    continue
            elif self._fmt == "codex":
                if not codex.is_top_level_record(rec):
                    continue
            elif self._fmt == "reasonix":
                if not reasonix.is_chat_record(rec):
                    continue
            else:
                continue
            records.append(rec)

        # A follow can begin on an empty file. Once a primary record arrives,
        # sidechain records in that same poll (and every later poll) are
        # noise, matching parse()'s whole-file policy as soon as the fact is
        # knowable from the stream.
        if self._fmt == "claude-code" and any(not rec.get("isSidechain") for rec in records):
            self._state.has_primary_thread = True
        if self._fmt == "claude-code" and self._state.has_primary_thread:
            # parse_record() emits no events for these records, but Follower
            # also uses Arrival.raw for interview attention. Drop them at the
            # source boundary so an ignored sidechain cannot alert through a
            # channel that selection would never render.
            records = [rec for rec in records if not rec.get("isSidechain")]

        for rec in records:

            marker = f"follow{self._seq}"
            if self._lossless:
                if self._fmt == "claude-code":
                    blocks = claude_code_blocks(rec, marker)
                elif self._fmt == "codex":
                    blocks = codex_blocks(rec, str(rec.get("ordinal", marker)))
                else:
                    blocks = reasonix_blocks(rec, marker)
                arrivals.append(Arrival(blocks=blocks, raw=rec))
            else:
                if self._fmt == "claude-code":
                    events = claude_code.parse_record(rec, self._seq, marker, self._config, self._state)
                elif self._fmt == "codex":
                    events = codex.parse_record(rec, self._seq, marker, self._config)
                else:
                    events = reasonix.parse_record(rec, self._seq, marker, self._config)
                arrivals.append(Arrival(events=events, raw=rec))
            self._seq += 1
        return arrivals


@dataclass(frozen=True)
class OpencodeAnchor:
    """The pre-phase-1 boundary for an opencode follow.

    The cursor identifies the newest message row that phase 1 saw. Its part
    fingerprint lets phase 2 notice text parts appended to that same row
    while phase 1 was reading it; a cursor alone would skip those updates.
    ``tracked`` carries the same bounded fingerprint view for recent rows
    before the cursor, whose parts can also still arrive after phase 1.
    """

    cursor: tuple[int, str]
    fingerprint: tuple | None = None
    tracked: tuple[tuple[tuple[int, str], tuple], ...] = ()


class OpencodeSource:
    """New opencode `message` rows, by indexed `(time_created, id)` cursor,
    with the newest row held back until a newer sibling proves its `part`
    rows are done streaming. A row with no newer sibling is emitted after
    two unchanged observations -- see the module docstring."""

    def __init__(
        self, db: Path, session_id: str, config: ExtractConfig, lossless_mode: bool,
        cursor: tuple[int, str] | None = None,
        anchor_fingerprint: tuple | None = None,
        tracked_fingerprints: tuple[tuple[tuple[int, str], tuple], ...] = (),
    ):
        self._conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        self._session_id = session_id
        self._config = config
        self._lossless = lossless_mode
        self._cursor = cursor or (-1, "")
        self._seq = 0
        self._anchor_cursor = self._cursor if anchor_fingerprint is not None else None
        self._anchor_fingerprint = anchor_fingerprint
        self._tracked_rows: OrderedDict[tuple[int, str], tuple] = OrderedDict()
        for key, fingerprint in tracked_fingerprints:
            self._remember_tracked(key, fingerprint)
        if self._anchor_cursor is not None and self._anchor_fingerprint is not None:
            self._remember_tracked(self._anchor_cursor, self._anchor_fingerprint)
        self._pending_key: tuple[int, str] | None = None
        self._pending_fingerprint: tuple | None = None
        self._pending_stable = False

    def close(self) -> None:
        self._conn.close()

    def _fingerprint(self, msg_id: str, data_json: str) -> tuple:
        parts = self._conn.execute(
            "SELECT id, time_updated, data FROM part WHERE message_id = ? ORDER BY id ASC",
            (msg_id,),
        ).fetchall()
        return (data_json, tuple(parts))

    def _remember_tracked(self, key: tuple[int, str], fingerprint: tuple) -> None:
        self._tracked_rows[key] = fingerprint
        self._tracked_rows.move_to_end(key)
        while len(self._tracked_rows) > OPENCODE_TRACKED_ROWS:
            self._tracked_rows.popitem(last=False)

    def _arrival(self, msg_id: str, time_created: int, data_json: str) -> Arrival | None:
        if self._lossless:
            blocks = opencode_blocks(self._conn, msg_id, time_created, data_json)
            arrival = Arrival(blocks=blocks) if blocks else None
        else:
            ev = opencode.event_for_row(self._conn, self._seq, msg_id, time_created, data_json)
            arrival = Arrival(events=[ev]) if ev is not None else None
        self._seq += 1
        return arrival

    def _changed_texts(self, old_fingerprint: tuple, new_fingerprint: tuple) -> list[str]:
        """Return only text added or changed since the last boundary view.

        New parts are the normal streaming shape. If opencode extends an
        existing text part instead, emit only its new suffix when the old
        value is a prefix; an in-place rewrite cannot retract bytes already
        printed, so its replacement is the only honest contribution.
        """
        old_parts = {part[0]: part for part in old_fingerprint[1]}
        texts: list[str] = []
        for part_id, updated, part_json in new_fingerprint[1]:
            previous = old_parts.get(part_id)
            # A missing entry cannot equal the complete part tuple, so the
            # explicit `is not None` half is redundant. Keeping the equality
            # as the sole guard also makes this branch's actual invariant
            # visible: an unchanged part is skipped; every changed/new part
            # is considered below.
            if previous == (part_id, updated, part_json):
                continue
            try:
                part = json.loads(part_json)
            except json.JSONDecodeError:
                continue
            if part.get("type") != "text" or not part.get("text"):
                continue
            text = part["text"]
            if previous is not None:
                try:
                    old_part = json.loads(previous[2])
                except json.JSONDecodeError:
                    old_part = {}
                old_text = old_part.get("text") if old_part.get("type") == "text" else None
                if isinstance(old_text, str) and text.startswith(old_text):
                    text = text[len(old_text):]
            if text:
                texts.append(text)
        return texts

    def _arrival_for_texts(
        self, msg_id: str, time_created: int, data_json: str, texts: list[str]
    ) -> Arrival | None:
        if self._lossless:
            block = opencode_block_for_texts(msg_id, time_created, data_json, texts)
            arrival = Arrival(blocks=[block]) if block is not None else None
        else:
            ev = opencode.event_for_texts(self._seq, msg_id, time_created, data_json, texts)
            arrival = Arrival(events=[ev]) if ev is not None else None
        self._seq += 1
        return arrival

    def _commit_row(self, row, fingerprint: tuple | None = None) -> None:
        """Advance to an emitted/settled row while retaining its live view.

        ``cursor`` excludes a row from the indexed query.  That is useful for
        avoiding a full-row re-emission, but it also means the cursor cannot be
        the only state for the newest row: opencode may append or extend its
        ``part`` rows after the row looked stable.  Keep the same row as an
        anchor, with the fingerprint representing exactly what was last
        observed, so ``_poll_anchor`` can emit only a later contribution.
        """
        key = (row[1], row[0])
        self._cursor = key
        self._anchor_cursor = key
        self._anchor_fingerprint = (
            fingerprint if fingerprint is not None else self._fingerprint(row[0], row[2])
        )
        self._remember_tracked(key, self._anchor_fingerprint)

    def _poll_anchor(self) -> list[Arrival]:
        """Emit changed tracked rows without re-emitting unchanged rows.

        The newest row is the usual late-part case, but opencode may finish
        an older message after a newer message has advanced the cursor too.
        Every tracked row is checked by its exact primary key; the bounded
        window keeps this work proportional to recent live rows, never to the
        size of the message table.
        """
        if self._anchor_cursor is not None and self._anchor_fingerprint is not None:
            # Some callers from before the bounded window was added supplied
            # the private anchor fields directly. Preserve that compatible
            # shape while bringing it into the same tracking path.
            self._remember_tracked(self._anchor_cursor, self._anchor_fingerprint)

        if not self._tracked_rows:
            return []

        arrivals: list[Arrival] = []
        # Cursor order, rather than OrderedDict insertion order, preserves
        # normal message order when more than one older row changes together.
        for key in sorted(tuple(self._tracked_rows)):
            old_fingerprint = self._tracked_rows.get(key)
            if old_fingerprint is None:
                continue
            row = self._conn.execute(
                "SELECT id, time_created, data FROM message WHERE session_id = ? "
                "AND time_created = ? AND id = ?",
                (self._session_id, key[0], key[1]),
            ).fetchone()
            if row is None:
                # A deleted row cannot produce a text contribution. Drop it
                # so a deletion does not consume one of the bounded slots on
                # every future poll.
                self._tracked_rows.pop(key, None)
                continue
            fingerprint = self._fingerprint(row[0], row[2])
            if fingerprint == old_fingerprint:
                continue
            self._tracked_rows[key] = fingerprint
            self._tracked_rows.move_to_end(key)
            if key == self._anchor_cursor:
                self._anchor_fingerprint = fingerprint
            texts = self._changed_texts(old_fingerprint, fingerprint)
            arrival = self._arrival_for_texts(row[0], row[1], row[2], texts)
            if arrival is not None:
                arrivals.append(arrival)
        return arrivals

    def _remember_pending(self, row) -> None:
        key = (row[1], row[0])
        fingerprint = self._fingerprint(row[0], row[2])
        if key == self._pending_key and fingerprint == self._pending_fingerprint:
            self._pending_stable = True
        else:
            self._pending_key = key
            self._pending_fingerprint = fingerprint
            self._pending_stable = False

    def poll(self) -> list[Arrival]:
        arrivals = self._poll_anchor()
        last_time, last_id = self._cursor
        rows = self._conn.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? "
            "AND (time_created > ? OR (time_created = ? AND id > ?)) "
            "ORDER BY time_created ASC, id ASC",
            (self._session_id, last_time, last_time, last_id),
        ).fetchall()
        if not rows:
            return arrivals

        if len(rows) == 1:
            row = rows[0]
            self._remember_pending(row)
            if not self._pending_stable:
                return arrivals
            arrival = self._arrival(row[0], row[1], row[2])
            self._commit_row(row, self._pending_fingerprint)
            self._pending_key = None
            self._pending_fingerprint = None
            self._pending_stable = False
            if arrival is not None:
                arrivals.append(arrival)
            return arrivals

        for row in rows[:-1]:
            arrival = self._arrival(row[0], row[1], row[2])
            # Rows before the newest one are settled by the newer sibling, but
            # keep the most recently emitted row as an anchor too. This closes
            # the same late-part window for a row that was emitted by the
            # multi-row path rather than by the two-observation path above.
            self._commit_row(row)
            if arrival is not None:
                arrivals.append(arrival)
        self._pending_key = None
        self._pending_fingerprint = None
        self._pending_stable = False
        return arrivals


@dataclass
class SelectionResult:
    """`emitted` in stream order; `checkpoints` are events whose FINAL score
    cleared the threshold -- possibly one emitted on an earlier feed(), since
    a score can only be finalized once the next event arrives."""

    emitted: list[NormalizedEvent] = field(default_factory=list)
    checkpoints: list[NormalizedEvent] = field(default_factory=list)


class FollowSelector:
    """`select.decide()` applied forward, plus the checkpoint-score
    lookahead buffer. See the module docstring for the three cases and why
    only the third one waits."""

    def __init__(self, config: ExtractConfig):
        self._config = config
        self._pending: NormalizedEvent | None = None
        self._pending_emitted = False
        self._held: list[NormalizedEvent] = []

    def _pause_bonus(self, nxt: NormalizedEvent) -> float:
        # Mirrors classifier.score_events' own scan: the bonus applies only
        # when the next REAL event is an operator prompt or a Q&A exchange.
        if nxt.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR):
            return classifier.WEIGHTS["followed_by_pause"]
        return 0.0

    def feed(self, ev: NormalizedEvent) -> SelectionResult:
        result = SelectionResult()

        if self._pending is not None:
            if ev.kind is EventKind.THINKING:
                if self._pending_emitted:
                    # Nothing to reorder -- the pending event is already on
                    # screen -- and THINKING is not decisive for its score
                    # either, so this passes straight through.
                    if decide(ev, self._config).keep:
                        result.emitted.append(ev)
                    return result
                self._held.append(ev)
                return result

            pending = self._pending
            pending.checkpoint_score = (pending.checkpoint_score or 0.0) + self._pause_bonus(ev)
            verdict = decide(pending, self._config)
            if verdict.keep and not self._pending_emitted:
                result.emitted.append(pending)
            if verdict.is_checkpoint:
                result.checkpoints.append(pending)
            self._pending = None
            self._pending_emitted = False
            for held in self._held:
                if decide(held, self._config).keep:
                    result.emitted.append(held)
            self._held.clear()

        if ev.kind is EventKind.ASSISTANT_TEXT:
            ev.checkpoint_score = classifier.shape_score(ev.text)
            verdict = decide(ev, self._config)
            if verdict.is_checkpoint:
                result.emitted.append(ev)
                result.checkpoints.append(ev)
            elif verdict.keep:
                result.emitted.append(ev)
                self._pending = ev
                self._pending_emitted = True
            elif self._config.hide_api_errors and classifier.is_api_error(ev.text):
                pass  # a certain drop, never a checkpoint -- nothing to wait for
            else:
                self._pending = ev
                self._pending_emitted = False
        elif decide(ev, self._config).keep:
            result.emitted.append(ev)

        return result


@dataclass(frozen=True)
class AttentionEvent:
    reason: str
    harness: str
    session_path: str
    excerpt: str


@dataclass
class FollowConfig:
    interval: float = DEFAULT_INTERVAL_S
    bell: bool = False
    on_attention: str | None = None
    #: A nyxloom.config.NotifyConfig, resolved by cli.py from
    #: --notify-project (this module never loads a project itself).
    notify: object | None = None
    attention_min_chars: int | None = None


def deliver(att: AttentionEvent, config: FollowConfig, bell_out) -> None:
    """Every configured delivery channel for one fired signal. Never raises:
    a follow loop that dies because a notification hook failed is worse than
    a missed notification, so failures go to stderr and the stream continues.

    The bell goes to `bell_out` (stderr in production, not the content
    stream): `\a` is a notification, not session content, and `nyxloom
    extract --follow | claude` should not carry stray bell bytes into
    another agent's prompt.
    """
    if config.bell:
        bell_out.write("\a")
        bell_out.flush()

    if config.on_attention:
        env = dict(os.environ)
        env.update({
            "NYXLOOM_ATTENTION_REASON": att.reason,
            "NYXLOOM_ATTENTION_HARNESS": att.harness,
            "NYXLOOM_ATTENTION_SESSION_PATH": att.session_path,
            "NYXLOOM_ATTENTION_EXCERPT": att.excerpt,
        })
        try:
            # A hook is a side effect, not part of the extracted content. Its
            # stdout must not corrupt `--follow | another-agent`; capture both
            # streams and keep any diagnostic output visible on stderr.
            completed = subprocess.run(
                config.on_attention, shell=True, env=env, check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for diagnostic in (completed.stdout, completed.stderr):
                if diagnostic:
                    print(diagnostic, end="", file=sys.stderr)
        except OSError as e:
            print(f"nyxloom follow: --on-attention command failed: {e}", file=sys.stderr)

    if config.notify is not None:
        from .. import notify as notify_mod

        # SPEC §13 exception, deliberate and scoped -- see the module
        # docstring: this body carries session prose (the excerpt), which a
        # template-only notification never does.
        note = {
            "title": f"{att.harness} session needs attention ({att.reason})",
            "body": f"{att.session_path}\n{att.excerpt}",
            "priority": 4,
            "tags": ["attention"],
        }
        try:
            ok, detail = notify_mod.send(config.notify, note)
        except Exception as e:  # census: advisory-degradation (nyxloom-P115)
            # Matches notify.py's own classification of a delivery failure: a
            # notification that does not arrive can only ever REDUCE what
            # happens, never authorize anything, and a follow loop that dies
            # because a push failed is worse than a missed push.
            ok, detail = False, str(e)
        if not ok:
            print(f"nyxloom follow: --notify-project delivery failed: {detail}", file=sys.stderr)


class Follower:
    """One `--follow` stream: poll the source, apply the verb's own rules,
    print what survives, fire attention signals.

    `tick()` is one poll cycle and is what tests drive; `run_forever()` is
    just tick + sleep, so the loop itself holds no logic a test cannot
    reach.
    """

    def __init__(
        self,
        source,
        harness: str,
        session_path: str,
        config: ExtractConfig,
        follow_config: FollowConfig,
        out,
        lossless_mode: bool,
        block_render: Callable[[str], str] | None = None,
        insert_blank_lines: int = 1,
        printed_any: bool = True,
        bell_out=None,
    ):
        self._source = source
        self._harness = harness
        self._session_path = session_path
        self._config = config
        self._follow = follow_config
        self._out = out
        self._bell_out = bell_out if bell_out is not None else sys.stderr
        self._lossless = lossless_mode
        self._block_render = block_render
        self._selector = None if lossless_mode else FollowSelector(config)
        # lossless blocks join with a blank line, exactly as its own dump
        # does; extract blocks join with the same "---" separator render.py
        # would have used.
        self._separator = "\n\n" if lossless_mode else render.separator(insert_blank_lines)
        # Phase 1 normally printed something already, so the first live block
        # needs a separator in front of it too.
        self._printed_any = printed_any
        self._startup_interview_pending = dict(
            getattr(source, "initial_interview_pending", {})
        )
        self._interview_pending: dict[str, str] = dict(self._startup_interview_pending)
        self._startup_attention_fired: set[str] = set()

    def close(self) -> None:
        close = getattr(self._source, "close", None)
        if close is not None:
            close()

    def _write(self, text: str) -> None:
        if self._printed_any:
            self._out.write(self._separator)
        self._out.write(text)
        self._printed_any = True

    def _excerpt(self, text: str) -> str:
        return text[:EXCERPT_CHARS]

    def _redacted_attention_text(self, text: str) -> str:
        """Return attention prose under the live extract redaction policy.

        Attention is delivered through side channels as well as stdout.  It
        must therefore pass through the same paragraph redactor before the
        excerpt is constructed.  Lossless mode deliberately skips this: the
        CLI refuses ``--redact-pattern`` for that verb, whose contract is
        verbatim output.
        """
        if self._lossless or not self._config.redact_patterns:
            return text
        event = NormalizedEvent(
            seq=0,
            marker="attention",
            timestamp="",
            kind=EventKind.ASSISTANT_TEXT,
            text=text,
        )
        self._redact(event)
        return event.text

    def _redact(self, ev: NormalizedEvent) -> None:
        """Apply extract's post-selection redaction to live output too.

        Redaction is deliberately after selection, matching
        ``session_extract.extract``. The selector therefore sees the same
        original prose it saw in phase one, while the event is sanitized
        before it can reach stdout (or an attention excerpt).
        """
        if self._config.redact_patterns:
            mangle.redact_paragraphs([ev], list(self._config.redact_patterns))

    def _fire(self, reason: str, text: str) -> None:
        deliver(
            AttentionEvent(
                reason, self._harness, self._session_path,
                self._excerpt(self._redacted_attention_text(text)),
            ),
            self._follow, self._bell_out,
        )

    def tick(self) -> int:
        """One poll cycle: returns how many blocks were printed."""
        printed = 0
        for arrival in self._source.poll():
            if arrival.raw is not None and self._harness == "claude-code":
                question = claude_code.update_interview_pending(arrival.raw, self._interview_pending)
                if question is not None:
                    self._fire("interview_pending", question)

            min_chars = self._follow.attention_min_chars

            if self._lossless:
                for block in arrival.blocks:
                    self._write(block.render(self._block_render))
                    printed += 1
                    if classifier.shape_score(block.text) >= self._config.checkpoint_score_threshold:
                        self._fire("checkpoint_detected", block.text)
                    if min_chars is not None and len(block.text) > min_chars:
                        self._fire("long_block", block.text)
                continue

            for ev in arrival.events:
                if (min_chars is not None
                        and ev.kind in (EventKind.ASSISTANT_TEXT, EventKind.THINKING)
                        and len(ev.text) > min_chars):
                    self._fire("long_block", ev.text)
                result = self._selector.feed(ev)
                for emitted in result.emitted:
                    self._redact(emitted)
                    self._write(render.render_event_block(emitted, self._block_render))
                    printed += 1
                for checkpoint in result.checkpoints:
                    self._fire("checkpoint_detected", checkpoint.text)

        # A follow may start after Claude has already asked its question. The
        # question is in phase one's prefix, so no new arrival would trigger
        # the ordinary per-record detector. Deliver it once after processing
        # this tick's arrivals, which also lets a just-arrived answer clear it
        # before we announce attention.
        for tool_use_id, question in self._startup_interview_pending.items():
            if (tool_use_id not in self._startup_attention_fired
                    and tool_use_id in self._interview_pending):
                self._fire("interview_pending", question)
                self._startup_attention_fired.add(tool_use_id)

        if printed:
            self._out.flush()
        return printed

    def run_forever(self) -> int:
        """Poll until interrupted. Ctrl-C is the normal way to stop a tail,
        so it is an exit 0, not an error."""
        try:
            while True:
                self.tick()
                time.sleep(self._follow.interval)
        except KeyboardInterrupt:
            return 0
        finally:
            self.close()
