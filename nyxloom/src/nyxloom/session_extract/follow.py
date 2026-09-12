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
     exists in Codex's or opencode's schema as currently understood -- see
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

**Delivery** is orthogonal to detection: `--bell` writes `\\a`,
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
time, committing the cursor only to rows that a newer sibling proves are
finished. Same shape of trade as the one-event delay above, for the same
reason.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import classifier, render
from .adapters import claude_code, codex, opencode
from .config import ExtractConfig
from .events import EventKind, NormalizedEvent
from .lossless import LosslessBlock, claude_code_blocks, codex_blocks, opencode_blocks
from .select import decide

#: How much of the flagged text an attention payload carries.
EXCERPT_CHARS = 100

#: Poll interval when the file/DB has not changed. A session log grows in
#: bursts of one human/model turn, so sub-second polling buys nothing.
DEFAULT_INTERVAL_S = 1.0


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
    ticks -- the last invisible to any size comparison, caught instead by
    that newline invariant. None of this is expected for these harness logs,
    which are append-only, but the failure mode without it is silent garbage
    rather than an error.
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

    def _rewritten(self) -> bool:
        if not self._own_offset or self.offset == 0:
            return False
        self._handle.seek(self.offset - 1)
        return self._handle.read(1) != b"\n"

    def poll(self) -> list[str]:
        """Every COMPLETE new line since the last poll."""
        try:
            st = self.path.stat()
        except OSError:
            return []

        if self._handle is None:
            self._open()
        # Checked on EVERY poll including the first, deliberately: the handle
        # having just been opened says nothing about whether the file still
        # matches the offset we were handed.
        if st.st_ino != self._inode or st.st_size < self.offset or self._rewritten():
            self.close()
            self.offset = 0
            self._own_offset = False
            self._open()

        if st.st_size <= self.offset:
            return []

        self._handle.seek(self.offset)
        chunk = self._handle.read()
        if not chunk:
            return []
        self.bytes_read += len(chunk)

        parts = chunk.split(b"\n")
        trailing = parts[-1]  # incomplete -- left for the next tick
        self.offset += len(chunk) - len(trailing)
        self._own_offset = True
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
    import json

    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


class JsonlSource:
    """New Claude Code / Codex records, byte-offset tailed.

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

    def close(self) -> None:
        self.tailer.close()

    def poll(self) -> list[Arrival]:
        arrivals: list[Arrival] = []
        for line in self.tailer.poll():
            rec = _parse_line(line)
            if rec is None:
                continue
            if self._fmt == "claude-code":
                if not claude_code.is_conversation_record(rec):
                    continue
            elif not codex.is_top_level_record(rec):
                continue

            marker = f"follow{self._seq}"
            if self._lossless:
                if self._fmt == "claude-code":
                    blocks = claude_code_blocks(rec, marker)
                else:
                    blocks = codex_blocks(rec, str(rec.get("ordinal", marker)))
                arrivals.append(Arrival(blocks=blocks, raw=rec))
            else:
                if self._fmt == "claude-code":
                    events = claude_code.parse_record(rec, self._seq, marker, self._config, self._state)
                else:
                    events = codex.parse_record(rec, self._seq, marker, self._config)
                arrivals.append(Arrival(events=events, raw=rec))
            self._seq += 1
        return arrivals


class OpencodeSource:
    """New opencode `message` rows, by indexed `(time_created, id)` cursor,
    with the newest row held back until a newer sibling proves its `part`
    rows are done streaming -- see the module docstring."""

    def __init__(
        self, db: Path, session_id: str, config: ExtractConfig, lossless_mode: bool,
        cursor: tuple[int, str] | None = None,
    ):
        self._conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        self._session_id = session_id
        self._config = config
        self._lossless = lossless_mode
        self._cursor = cursor or (-1, "")
        self._seq = 0

    def close(self) -> None:
        self._conn.close()

    def poll(self) -> list[Arrival]:
        last_time, last_id = self._cursor
        rows = self._conn.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? "
            "AND (time_created > ? OR (time_created = ? AND id > ?)) "
            "ORDER BY time_created ASC, id ASC",
            (self._session_id, last_time, last_time, last_id),
        ).fetchall()
        if len(rows) < 2:
            return []

        arrivals: list[Arrival] = []
        for msg_id, time_created, data_json in rows[:-1]:
            if self._lossless:
                blocks = opencode_blocks(self._conn, msg_id, time_created, data_json)
                if blocks:
                    arrivals.append(Arrival(blocks=blocks))
            else:
                ev = opencode.event_for_row(self._conn, self._seq, msg_id, time_created, data_json)
                if ev is not None:
                    arrivals.append(Arrival(events=[ev]))
            self._seq += 1
        settled_id, settled_time, _ = rows[-2]
        self._cursor = (settled_time, settled_id)
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
            if verdict.keep and verdict.is_checkpoint:
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


def deliver(att: AttentionEvent, config: FollowConfig, out) -> None:
    """Every configured delivery channel for one fired signal. Never raises:
    a follow loop that dies because a notification hook failed is worse than
    a missed notification, so failures go to stderr and the stream continues.
    """
    if config.bell:
        out.write("\a")
        out.flush()

    if config.on_attention:
        env = dict(os.environ)
        env.update({
            "NYXLOOM_ATTENTION_REASON": att.reason,
            "NYXLOOM_ATTENTION_HARNESS": att.harness,
            "NYXLOOM_ATTENTION_SESSION_PATH": att.session_path,
            "NYXLOOM_ATTENTION_EXCERPT": att.excerpt,
        })
        try:
            subprocess.run(config.on_attention, shell=True, env=env, check=False)
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
        except Exception as e:  # a transport this module cannot reason about
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
    ):
        self._source = source
        self._harness = harness
        self._session_path = session_path
        self._config = config
        self._follow = follow_config
        self._out = out
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
        self._interview_pending: dict[str, str] = {}

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

    def _fire(self, reason: str, text: str) -> None:
        deliver(
            AttentionEvent(reason, self._harness, self._session_path, self._excerpt(text)),
            self._follow, self._out,
        )

    def _long_block_chars(self) -> int | None:
        return self._follow.attention_min_chars

    def tick(self) -> int:
        """One poll cycle: returns how many blocks were printed."""
        printed = 0
        for arrival in self._source.poll():
            if arrival.raw is not None and self._harness == "claude-code":
                question = claude_code.update_interview_pending(arrival.raw, self._interview_pending)
                if question is not None:
                    self._fire("interview_pending", question)

            min_chars = self._long_block_chars()

            if self._lossless:
                for block in arrival.blocks:
                    self._write(
                        self._block_render(block.render()) if self._block_render else block.render()
                    )
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
                    self._write(render.render_event_block(emitted, self._block_render))
                    printed += 1
                for checkpoint in result.checkpoints:
                    self._fire("checkpoint_detected", checkpoint.text)

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
