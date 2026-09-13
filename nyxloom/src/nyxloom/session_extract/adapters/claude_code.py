"""Claude Code adapter -- ~/.claude/projects/<project>/<session>.jsonl.

Schema facts this adapter relies on (verified directly against real session
files during this tool's design, not from documentation):

- One JSON object per line. Relevant top-level `type` values: "user",
  "assistant", "system" (plus a long tail of harness bookkeeping types --
  mode, bridge-session, attachment, last-prompt, atis-latch, ai-title,
  queue-operation, cost-state, file-history-* -- all ignored here).
- `assistant` messages carry message.content as a list of blocks: "text",
  "thinking", "tool_use". A message with BOTH tool_use and text blocks is
  common (the model narrates, then calls a tool) -- text blocks are always
  emitted as ASSISTANT_TEXT regardless; classifier.py, not this adapter,
  decides whether a given block reads as a checkpoint.
- `user` messages are mostly synthetic tool_result feedback, not real
  operator input -- in one 10k-line real session, 1753 of 1812 "user"-type
  records were tool_result, only 58 were a real typed prompt. Real operator
  text has message.content as a plain string (or a list containing exactly
  one "text" block); harness-injected framing (a context-usage report, a
  local-command-caveat notice, "session continued from...") is marked
  `isMeta: true` or `isVisibleInTranscriptOnly: true` and is excluded here
  rather than treated as operator intent. A Ctrl+C interrupt injects its own
  synthetic `user`-type record ("[Request interrupted by user]") carrying a
  top-level `interruptedMessageId`; excluded the same way -- it is harness
  bookkeeping, not something the operator typed.
- A real API-level failure (429 rate limit, `overloaded_error`, etc.) is an
  `assistant`-type record too (`model: "<synthetic>"`, `isApiErrorMessage:
  true`, `error`/`apiErrorStatus` giving the kind/HTTP status), indistin-
  guishable from genuine model prose without checking that flag -- verified
  against a real 429 ("You've hit your session limit ... resets 12:20am
  (UTC)", `error: "rate_limit"`, `apiErrorStatus: 429`). Tagged as its own
  ASSISTANT_TEXT with a `[API ERROR: ...]` prefix (still assistant-channel
  content, just not model-generated) rather than a new EventKind, so
  classifier.has_finding_signal's own dedicated pattern keeps it regardless
  of length -- a rate-limit notice is usually one short line, exactly what
  select()'s length filter would otherwise silently drop, and the fact a
  session actually stalled on a real API error is never noise.
- Claude Code wraps slash-command invocations in the operator's own text as
  `<command-name>NAME</command-name>` (+ optional `<command-message>` /
  `<command-args>`); `/compact` and `/clear` specifically are promoted to
  LIFECYCLE_MARKER since they bound what a resume ever needs to look past.
- AskUserQuestion: the tool_use lives on an assistant turn; its answer
  arrives later as a `user`-type tool_result whose `tool_use_id` matches.
  Verified on a real 4-question batch: the harness ALREADY renders every
  question+answer pair into one string ('"Q1"="A1", "Q2"="A2", ...') --
  this adapter uses that string verbatim rather than re-deriving pairing
  from tool_use.input.questions.
- `system` records with `subtype: "compact_boundary"`, and `user` records
  with top-level `isCompactSummary: true`, mark an auto-compaction; both
  become LIFECYCLE_MARKER (selection never looks earlier than the nearest
  one -- that content is already a different kind of artifact).
- Branch points (a parentUuid with >1 child) were investigated directly:
  every one found was a parallel-tool-call fan-out artifact (one assistant
  turn issuing several tool calls forces a tree structure onto what is
  really a fan-out), never message-edit/retry branching, and file order was
  confirmed strictly timestamp-monotonic. Since this adapter discards all
  tool_use/tool_result content except AskUserQuestion answers anyway, that
  branching is invisible here -- this is a straight top-to-bottom scan,
  deliberately NOT parentUuid-chain-aware. Genuine message-edit/resubmit
  branching was not observed and is not specifically handled; if it turns
  out to matter, this is the place to add it.
- A dispatched Agent-tool subagent's own conversation lives in a SEPARATE
  file, `<project>/<session>/subagents/agent-<agentId>.jsonl` (reachable
  from the Agent tool's own `output_file` result, a symlink typically ending
  in `.output` rather than `.jsonl` -- see sniff()'s docstring), paired with
  an `agent-<agentId>.meta.json` sidecar (agentType, isFork, description,
  toolUseId, spawnDepth). The TOP-LEVEL interactive session file never
  contains this subagent's content inline and never carries an `agentId`
  itself (verified: 59/59 real top-level files on this machine, zero
  isSidechain=true records and zero agentId values among them) -- dispatch
  leaves no trace in the parent file beyond the Agent tool_use/tool_result
  pair that requested it.
- **Nested subagents (a subagent that itself dispatches another subagent
  via the Agent tool) get their own separate file too** -- verified
  directly, not assumed: real sessions were found where a subagent's own
  file uses the Agent tool, and the resulting child file exists as a FLAT
  SIBLING under the SAME top-level session's `subagents/` directory (no
  `subagents/.../subagents/...` nesting in the path). The parent-child link
  is carried EXTERNALLY, in the child's `.meta.json` (`spawnDepth`: 1 =
  dispatched directly from the interactive session, 2 = dispatched from
  within another subagent, etc. -- a real session showed the histogram
  {1: 34, 2: 3}, confirming multi-level nesting occurs) and `toolUseId`
  (cross-referenced against the Agent-tool tool_use block ids found inside
  the presumed PARENT's own file to establish the actual edge -- confirmed
  concretely on real data: a depth-1 subagent file containing 4 Agent
  tool_use blocks, two of which matched the `toolUseId` of two sibling
  depth-2 `.meta.json` files). isSidechain does NOT carry this signal --
  it is `true` uniformly for every record at every depth, so it tells you
  "this file is someone's subagent," never which one dispatched it or how
  deep. Lineage/depth is a `.meta.json`-only fact.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "claude-code"

# The only top-level record types that can carry conversation content --
# everything else in this file's long tail (mode, bridge-session,
# attachment, file-history-*, ...) is harness bookkeeping. Named here
# because both the whole-file loader below and follow.py's incremental
# tailer must agree exactly on what counts as a record, or their record
# numbering (and therefore a uuid-less record's fallback marker) drifts.
_CONVERSATION_TYPES = ("user", "assistant", "system")

_COMMAND_NAME_RE = re.compile(r"<command-name>([^<]*)</command-name>", re.IGNORECASE)
_LEADING_CAVEAT_RE = re.compile(r"\A\s*<local-command-caveat>.*?</local-command-caveat>\s*", re.IGNORECASE | re.DOTALL)
# command-args is deliberately NOT in this strip set: its inner content is
# the operator's own typed argument text (e.g. "/review please check the
# auth module"), not harness noise -- see _strip_harness_tags, which
# unwraps it to plain text instead of discarding it. task-notification IS
# noise (a controller-injected background-agent push, never operator
# intent) and belongs here so it's removed wherever it appears in a
# message, not just when it's the entire message (see the docstring above
# on the "task-notification is pure noise" finding).
_HARNESS_TAG_RE = re.compile(
    # First branch: an opening tag (attributes allowed -- a real
    # task-notification carries e.g. id="...") through to its matching
    # close tag via backreference, so both the tag AND its body are
    # removed. Second branch is the fallback for a tag with no matching
    # close anywhere in the text (malformed/truncated content) or a
    # genuinely self-closing tag -- strips just the tag itself, since
    # there is no body to consume.
    r"<(local-command-caveat|local-command-stdout|command-name|command-message|task-notification)"
    r"\b[^>]*>.*?</\1>"
    r"|<(local-command-caveat|local-command-stdout|command-name|command-message|task-notification)\b[^>]*/?>",
    re.IGNORECASE | re.DOTALL,
)
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.IGNORECASE | re.DOTALL)
_LIFECYCLE_COMMANDS = {"compact", "clear"}

# Mirrors stats.py's own _COMPACT_LABEL -- same trigger vocabulary
# (compactMetadata.trigger is "manual" for an operator-issued /compact,
# "auto" for the harness's own auto-compaction), a distinct dict because
# stats.py's is capitalized for a table column ("Steered"/"Auto") while this
# one reads as prose inside a bracketed hint ("steered"/"automatic"). Not
# shared code on purpose -- see this module's and lossless.py's own "each
# reads independently" design point; both call the same underlying concept
# by name only, not by an imported constant.
_COMPACTION_TRIGGER_WORD = {"manual": "steered", "auto": "automatic"}


def _compaction_boundary_label(rec: dict) -> str:
    """Text for a compact_boundary record's own LIFECYCLE_MARKER (2026-09-11,
    operator direction: "the compaction step itself should only be present
    as hint `[compaction: steered|automatic happened]`... the extract prose
    should never include the content of the compaction itself -- it would
    be repetitive, we concat all text before"). compactMetadata (present on
    every real compact_boundary record) carries the real token/duration
    facts -- format mirrors stats.py's own _compaction_label bracket
    ("just like our extract-report", operator's own words) rather than the
    record's own `content` field, which is always just the fixed literal
    string "Conversation compacted" (verified against real dstdns session
    data), never the actual multi-KB retained-context summary -- that lives
    on the SEPARATE isCompactSummary record this adapter already hints-only
    ("[compact summary]", unchanged by this feature)."""
    meta = rec.get("compactMetadata") or {}
    trigger = meta.get("trigger")
    word = _COMPACTION_TRIGGER_WORD.get(trigger, trigger or "unknown")
    pre, post, dur_ms = meta.get("preTokens"), meta.get("postTokens"), meta.get("durationMs")
    detail = f", {pre:,}→{post:,} tok, {(dur_ms or 0) / 1000.0:.1f}s" if pre is not None and post is not None else ""
    return f"[compaction: {word} happened{detail}]"

def _split_qa_pairs(text: str, questions: list[Any]) -> list[tuple[str, str]] | None:
    """Best-effort re-split of the harness's own flattened tool_result
    string ('"Q1"="A1", "Q2"="A2", ...') back into (question, answer) pairs,
    anchored on each question's OWN verbatim text from
    tool_use.input.questions -- unambiguous, no quote-escaping guesswork
    needed. Returns None (render the raw string unmodified) the moment an
    expected marker isn't found, e.g. a harness rendering change this
    adapter hasn't seen yet.
    """
    pairs: list[tuple[str, str]] = []
    pos = 0
    for i, q in enumerate(questions):
        qtext = q.get("question") if isinstance(q, dict) else None
        if not qtext:
            return None
        marker = f'"{qtext}"='
        idx = text.find(marker, pos)
        if idx == -1:
            return None
        start = idx + len(marker)
        if i + 1 < len(questions):
            next_q = questions[i + 1]
            next_qtext = next_q.get("question") if isinstance(next_q, dict) else None
            if not next_qtext:
                return None
            end = text.find(f'"{next_qtext}"=', start)
            if end == -1:
                return None
            segment = text[start:end].rstrip()
            if segment.endswith(","):
                segment = segment[:-1].rstrip()
        else:
            # Last question: the harness always wraps the answer value in
            # its own matching quotes immediately after "="; whatever
            # trailing prose it appends afterward starts right after that
            # closing quote. Observed to vary ("Read the answers
            # carefully..." vs "You can now continue with these answers in
            # mind.") -- anchoring on the quote pairing itself, not specific
            # wording, is robust to that.
            remainder = text[start:]
            if remainder.startswith('"'):
                close = remainder.find('"', 1)
                segment = remainder[1:close] if close != -1 else remainder[1:]
            else:
                segment = remainder
        answer = segment.strip()
        if len(answer) >= 2 and answer.startswith('"') and answer.endswith('"'):
            answer = answer[1:-1]
        pairs.append((qtext, answer))
        pos = start
    return pairs or None


def _format_qa_pairs(text: str, questions: list[Any]) -> str:
    """Render an AskUserQuestion tool_result as, per question: an
    `INTERVIEW: <question text>` line, every declared option as a bullet
    list, a blank line, then `OPERATOR: <the actual answer>` -- a batch
    answering several questions at once gets one such block per question,
    blank line between blocks (operator-reported, 2026-09-10: the harness's
    own verbatim '"Q"="A"'-joined string was unreadable; INTERVIEW: prefix
    added 2026-09-11, operator direction, so a question line reads as a
    labeled question at a glance, distinct from the OPERATOR: answer below
    it and from unprefixed prose elsewhere in the render). Falls back to
    the raw string unmodified if re-splitting doesn't line up (see
    _split_qa_pairs) -- never raises, never silently drops content it
    couldn't parse.
    """
    if not questions:
        return text
    pairs = _split_qa_pairs(text, questions)
    if pairs is None:
        return text
    blocks = []
    for (qtext, answer), q in zip(pairs, questions):
        options = q.get("options") if isinstance(q, dict) else None
        lines = [f"INTERVIEW: {qtext}"]
        if isinstance(options, list):
            for opt in options:
                label = opt.get("label") if isinstance(opt, dict) else None
                if label:
                    lines.append(f"- {label}")
        lines.append("")
        lines.append(f"OPERATOR: {answer}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_SNIFF_SCAN_LINES = 50


def sniff(path: Path) -> bool:
    """The first line is often a housekeeping record (type "mode",
    "bridge-session", ...) that carries sessionId but no parentUuid --
    real user/assistant records normally appear within the first handful of
    lines, so this scans forward rather than trusting line 1 alone.

    Deliberately NOT gated on path.suffix == ".jsonl" (removed 2026-09-11,
    operator-reported): a Claude Code Agent-tool subagent's own transcript
    -- genuinely this exact format, `isSidechain: true` throughout -- is
    reachable through the Agent tool's own `output_file` result, which is a
    symlink ending in `.output`, not `.jsonl`; the extension gate rejected
    it outright before this function's own content check (sessionId +
    parentUuid, already adapter-specific enough on its own) ever ran. Since
    that content check was already the REAL discriminator for every
    .jsonl-suffixed file too, dropping the suffix gate only widens which
    extensions are eligible for the same check -- it doesn't weaken it."""
    try:
        with path.open("r", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= _SNIFF_SCAN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and "sessionId" in obj and "parentUuid" in obj:
                    return True
    except OSError:
        return False
    return False


def list_sessions(path: Path) -> list[str]:
    return [str(path)]


def _file_timestamps(path: Path) -> tuple[int, str | None, str | None]:
    records = _load_records(path)
    first_ts = records[0].get("timestamp") if records else None
    last_ts = records[-1].get("timestamp") if records else None
    return len(records), first_ts, last_ts


def _tool_use_ids(path: Path) -> set[str]:
    """Every tool_use block id found in an assistant record in this file --
    used to resolve which file dispatched a given sub-agent (its
    `.meta.json`'s toolUseId), regardless of the tool's own name (an Agent-
    tool dispatch today, but this deliberately doesn't hardcode that name)."""
    ids: set[str] = set()
    for rec in _load_records(path):
        if rec.get("type") != "assistant":
            continue
        for block in rec.get("message", {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                ids.add(block["id"])
    return ids


def list_agents(path: Path) -> list["SessionNode"]:
    """Discovery for `nyxloom extract-sessions` -- the whole family rooted
    at path's top-level session, whether path itself is that top-level
    file or one specific sub-agent's own file (see module docstring's
    "Nested subagents" section for the on-disk layout this walks, and
    E-015 for how it was verified against real data).

    Lineage is resolved the same way it was confirmed by hand this
    session: a sub-agent's `.meta.json` gives its own `spawnDepth`
    directly (the source of truth for "how deep"), and its `toolUseId` is
    cross-referenced against every CANDIDATE parent file's own tool_use
    ids (root file + every other sub-agent file, since a depth>1 agent's
    real dispatcher is another sub-agent, not necessarily the root) to
    find which file actually dispatched it. When that cross-reference
    can't resolve an edge (data anomaly, or a future spawn mechanism this
    wasn't written against), the node still surfaces -- with its own known
    spawnDepth kept visible -- rather than being silently dropped; only
    the tree NESTING for that one node is left unresolved.
    """
    from ..sessions import SessionNode

    if path.parent.name == "subagents":
        session_dir = path.parent.parent
        root_file = session_dir.parent / f"{session_dir.name}.jsonl"
    else:
        root_file = path
        session_dir = path.parent / path.stem

    root_id = str(root_file)
    nodes = []
    if root_file.exists():
        count, first_ts, last_ts = _file_timestamps(root_file)
        nodes.append(SessionNode(
            id=root_id, parent_id=None, depth=0, label="(interactive session)",
            path=root_id, record_count=count, first_ts=first_ts, last_ts=last_ts,
        ))

    subagents_dir = session_dir / "subagents"
    meta_files = sorted(subagents_dir.glob("agent-*.meta.json")) if subagents_dir.is_dir() else []
    if not meta_files:
        return nodes

    agent_files = {m: subagents_dir / (m.name[: -len(".meta.json")] + ".jsonl") for m in meta_files}
    candidate_files = ([root_file] if root_file.exists() else []) + [
        f for f in agent_files.values() if f.exists()
    ]
    owner_of_tool_use: dict[str, str] = {}
    for f in candidate_files:
        owner = root_id if f == root_file else str(f)
        for tid in _tool_use_ids(f):
            owner_of_tool_use.setdefault(tid, owner)

    for meta_path in meta_files:
        jsonl_path = agent_files[meta_path]
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        agent_id = meta_path.name[len("agent-"): -len(".meta.json")]
        spawn_depth = meta.get("spawnDepth")
        tool_use_id = meta.get("toolUseId")
        parent_id = owner_of_tool_use.get(tool_use_id) if tool_use_id else None
        if parent_id is None and spawn_depth == 1:
            parent_id = root_id if root_file.exists() else None
        label = meta.get("description") or meta.get("agentType") or "(sub-agent)"
        if spawn_depth is not None:
            label = f"{label} (spawnDepth {spawn_depth})"
        count, first_ts, last_ts = _file_timestamps(jsonl_path) if jsonl_path.exists() else (0, None, None)
        nodes.append(SessionNode(
            id=str(jsonl_path), parent_id=parent_id,
            depth=spawn_depth if isinstance(spawn_depth, int) else 1,
            label=label, path=str(jsonl_path),
            record_count=count, first_ts=first_ts, last_ts=last_ts,
        ))
    return nodes


def _strip_harness_tags(text: str) -> str:
    text = _COMMAND_ARGS_RE.sub(r"\1", text)  # unwrap, don't discard
    return _HARNESS_TAG_RE.sub("", text).strip()


def is_harness_tag_only(text: str) -> bool:
    """True when `text` consists ENTIRELY of harness-tag framing
    (`<task-notification>`, `<command-name>`, `<local-command-caveat>`,
    etc. -- see _HARNESS_TAG_RE) with nothing left once stripped -- the
    exact condition parse() itself uses (`if not cleaned: continue`) to
    drop a record without ever emitting a NormalizedEvent for it. Exposed
    (not just `_strip_harness_tags` directly) for extract-debug's own
    reason-labeling (debug_diff.py): a lossless block matching this is
    real, precisely-known noise, not a guess -- distinct from the
    checkpoint/length-based reasons debug_diff otherwise reconstructs,
    which only approximate select()'s per-event decision.
    """
    return not _strip_harness_tags(text)


def _command_name(text: str) -> str | None:
    # Anchored to the actual start of the message (past an optional leading
    # caveat block) -- a real slash-command invocation always begins the
    # message that way. An unanchored search would also match prose merely
    # *mentioning* "<command-name>compact</command-name>" mid-sentence
    # (this tool's own docs/tests are full of that literal string), which
    # would misclassify ordinary narration as a real /compact and hard-stop
    # the backward walk there.
    head = _LEADING_CAVEAT_RE.sub("", text, count=1).lstrip()
    m = _COMMAND_NAME_RE.match(head)
    if not m:
        return None
    return m.group(1).strip().lstrip("/").lower()


def is_conversation_record(rec: dict[str, Any]) -> bool:
    """Whether this raw record is one parse() would even look at -- see
    _CONVERSATION_TYPES. Public because follow.py's tailer decides the same
    thing about each newly-arrived line."""
    return rec.get("type") in _CONVERSATION_TYPES


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_conversation_record(obj):
                records.append(obj)
    return records


def has_primary_thread(path: Path) -> bool:
    """Whether this file has any non-sidechain record -- the auto-detected
    signal parse() uses to decide whether isSidechain records are noise to
    drop or ARE the conversation (a dispatched sub-agent's own transcript,
    where every record is isSidechain=true). See parse()'s own comment for
    the exhaustive 59/59 + 358/358 real-data verification behind it.

    Exposed for follow.py, which has to know this before the first tailed
    record arrives: it is a property of the whole file, unanswerable from
    one newly-appended line.
    """
    return any(not r.get("isSidechain") for r in _load_records(path))


@dataclass
class StreamState:
    """The only cross-record state parse_record() needs, so a forward
    stream can carry it the way parse()'s own whole-file pre-passes do.

    `askuserquestion_inputs` accumulates as AskUserQuestion tool_use blocks
    arrive; a real answer always arrives LATER as a tool_result naming the
    same id, so registering forward is enough (parse() keeps its own
    pre-pass as well -- the registration is idempotent).
    """

    has_primary_thread: bool = True
    askuserquestion_inputs: dict[str, list[Any]] = field(default_factory=dict)


def _remember_askuserquestion(rec: dict[str, Any], state: StreamState) -> None:
    """Carry the question metadata needed by a later tool_result."""
    if rec.get("type") != "assistant":
        return
    for block in rec.get("message", {}).get("content", []) or []:
        if (isinstance(block, dict) and block.get("type") == "tool_use"
                and block.get("name") == "AskUserQuestion" and block.get("id")):
            questions = block.get("input", {}).get("questions")
            state.askuserquestion_inputs[block["id"]] = (
                questions if isinstance(questions, list) else []
            )


def prime_stream_state(path: Path, state: StreamState, upto_bytes: int) -> None:
    """Seed forward-only parsing with question metadata before its anchor.

    A follow stream starts after phase 1, but Claude Code can answer an
    AskUserQuestion in a newly-appended record whose tool_use was already in
    the phase-1 prefix. Replaying only that prefix's tiny cross-record state
    prevents the answer from being mistaken for ordinary tool noise. This is
    one startup read, bounded by the caller's anchor; JsonlTailer remains the
    only reader used on every subsequent poll.
    """
    if upto_bytes <= 0:
        return
    consumed = 0
    try:
        handle = Path(path).open("rb")
    except OSError:
        return
    with handle:
        for raw_line in handle:
            next_consumed = consumed + len(raw_line)
            if next_consumed > upto_bytes:
                break  # the anchor itself may be in the middle of a line
            consumed = next_consumed
            try:
                rec = json.loads(raw_line)
            except (TypeError, ValueError):
                continue
            if not isinstance(rec, dict) or not is_conversation_record(rec):
                continue
            if rec.get("isSidechain") and state.has_primary_thread:
                continue
            _remember_askuserquestion(rec, state)


def update_interview_pending(rec: dict[str, Any], pending: dict[str, str]) -> str | None:
    """Track unanswered AskUserQuestion tool calls across a record stream;
    return the question text when THIS record leaves one outstanding.

    `pending` maps an AskUserQuestion tool_use id -> its first question's
    text, and is owned by the caller (follow.py) so state lives with the
    stream, not in module scope. Uses the same pairing this adapter already
    relies on everywhere else -- the question is a tool_use on an assistant
    turn, its answer a LATER `user`-type tool_result carrying the matching
    `tool_use_id` -- so "asked but not yet answered" is a real structural
    fact here, not a heuristic on prose.

    This is the one genuinely structural "needs you" signal in this adapter
    family. **No equivalent exists in the Codex or opencode schema as
    currently understood** (both adapters' own "Known gaps" notes say so:
    neither has a structured-question tool this package has identified), so
    follow.py's interview_pending signal is Claude-Code-only rather than
    guessed at for the others.
    """
    rtype = rec.get("type")
    content = (rec.get("message") or {}).get("content")

    if rtype == "assistant":
        asked: str | None = None
        for block in content or []:
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("name") == "AskUserQuestion" and block.get("id")):
                questions = block.get("input", {}).get("questions")
                first = questions[0] if isinstance(questions, list) and questions else None
                text = first.get("question") if isinstance(first, dict) else None
                pending[block["id"]] = text or "(question)"
                asked = pending[block["id"]]
        return asked

    if rtype == "user" and isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                pending.pop(block.get("tool_use_id"), None)
    return None


def parse_record(
    rec: dict[str, Any], seq: int, fallback_marker: str, config: ExtractConfig, state: StreamState
) -> list[NormalizedEvent]:
    """One raw record -> the NormalizedEvents it yields (0, 1, or -- for an
    assistant turn with several text/thinking blocks -- more than one).

    Factored out of parse()'s own loop (2026-09-12) so follow.py's
    incremental tailer applies literally the same per-record rules to a
    newly-appended line as a full parse does; duplicating them would let
    "what counts as an event" drift between a one-shot brief and a live
    stream. `fallback_marker` is the marker to use when the record has no
    uuid of its own -- parse() passes its absolute pre-slice position (see
    its own comment on why that must be pre-slice), follow.py passes a
    stream-local token.
    """
    if rec.get("isSidechain") and state.has_primary_thread:
        return []

    uuid = rec.get("uuid") or fallback_marker
    ts = rec.get("timestamp", "")
    rtype = rec.get("type")
    events: list[NormalizedEvent] = []

    if rtype == "system":
        if rec.get("subtype") == "compact_boundary":
            # Unconditional, regardless of hide_compaction_content: the
            # record's own `content` field is always just the fixed literal
            # "Conversation compacted" (never the real retained-context
            # summary -- see _compaction_boundary_label's docstring), so
            # there is no verbatim "content" for that flag to hide here in
            # the first place -- only the trigger/token metadata that flag
            # has no reason to gate, a strict improvement over the old bare
            # "[compact boundary]" text either way.
            events.append(NormalizedEvent(
                seq, uuid, ts, EventKind.LIFECYCLE_MARKER, _compaction_boundary_label(rec)))
        return events

    if rtype == "assistant":
        content = rec.get("message", {}).get("content", []) or []
        _remember_askuserquestion(rec, state)

        if rec.get("isApiErrorMessage"):
            # A real API-level failure (429 rate limit, overloaded_error,
            # etc.) -- Claude Code injects it as an ordinary "assistant"
            # record with model="<synthetic>", so without this check it
            # renders indistinguishably from genuine model prose despite
            # being harness/API-injected notification text, and (being
            # typically short) is exactly the shape select()'s length
            # filter silently drops -- the session-relevant fact that a
            # rate limit was actually HIT would vanish. Tagged
            # ASSISTANT_TEXT still (it IS on the assistant channel, just
            # not model-generated), with the error identity in the text
            # itself so classifier.has_finding_signal keeps it regardless
            # of length.
            text_blocks = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            body = "\n".join(t for t in text_blocks if t)
            label_parts = [p for p in (rec.get("error"), rec.get("apiErrorStatus")) if p is not None]
            label = ", ".join(
                str(p) if not isinstance(p, int) else f"HTTP {p}" for p in label_parts
            )
            prefix = f"[API ERROR: {label}]" if label else "[API ERROR]"
            events.append(NormalizedEvent(
                seq, uuid, ts, EventKind.ASSISTANT_TEXT,
                f"{prefix} {body}".rstrip() if body else prefix,
            ))
            return events

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "")
                if text:
                    events.append(NormalizedEvent(seq, uuid, ts, EventKind.ASSISTANT_TEXT, text))
            elif btype == "thinking" and config.include_thinking:
                text = block.get("thinking", "")
                if text:
                    events.append(NormalizedEvent(seq, uuid, ts, EventKind.THINKING, text))
        return events

    if rtype == "user":
        if rec.get("isCompactSummary"):
            events.append(NormalizedEvent(seq, uuid, ts, EventKind.LIFECYCLE_MARKER, "[compact summary]"))
            return events

        if rec.get("interruptedMessageId"):
            # Claude Code's OWN synthetic "[Request interrupted by user]"
            # record, injected when Ctrl+C cuts off a running response --
            # not real operator intent (operator-reported, 2026-09-10: it
            # was rendering as an ordinary all-zero OPERATOR_TEXT row,
            # indistinguishable from real content). interruptedMessageId
            # is a structural field the harness sets specifically for this
            # record, not a text-match on the message body, so this can't
            # misfire on a real prompt that happens to contain that phrase.
            return events

        content = rec.get("message", {}).get("content")

        if isinstance(content, list):
            # A tool_result block for some OTHER tool call can share a
            # content list with a genuine text block (e.g. an operator
            # follow-up typed alongside residual tool output) -- the
            # tool_result is still noise, but a real text block sitting
            # next to it must not be discarded along with it.
            qa_text = None
            qa_tool_id = None
            for block in content:
                if not isinstance(block, dict):
                    continue
                if (block.get("type") == "tool_result"
                        and block.get("tool_use_id") in state.askuserquestion_inputs):
                    c = block.get("content")
                    qa_text = c if isinstance(c, str) else json.dumps(c)
                    qa_tool_id = block.get("tool_use_id")
            if qa_text is not None:
                formatted = _format_qa_pairs(qa_text, state.askuserquestion_inputs.get(qa_tool_id, []))
                events.append(NormalizedEvent(seq, uuid, ts, EventKind.QA_PAIR, formatted))
                return events
            text_blocks = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if not text_blocks:
                return events
            raw_text = "\n".join(text_blocks)
        elif isinstance(content, str):
            raw_text = content
        else:
            return events

        if rec.get("isMeta") or rec.get("isVisibleInTranscriptOnly"):
            return events

        cmd = _command_name(raw_text)
        cleaned = _strip_harness_tags(raw_text)
        if cmd in _LIFECYCLE_COMMANDS:
            # An operator-issued `/compact <prompt>` dispatch's own argument
            # text is hint-only by default (2026-09-11, operator direction --
            # see _compaction_boundary_label's docstring): it almost always
            # just re-quotes a "compaction prompt" the model already produced
            # as ordinary ASSISTANT_TEXT moments earlier (unaffected by this
            # -- kept in full there, classifier.py's meta_compact scoring
            # sees to that), so repeating it here in full is exactly the
            # redundancy the operator flagged. /clear carries no such risk
            # (no KEEP-block-style argument in practice) and is left as-is.
            # --show-compaction-content (hide_compaction_content=False)
            # restores the pre-2026-09-11 verbatim behavior for either
            # command.
            if cmd == "compact" and config.hide_compaction_content:
                label = "[compaction: steered dispatched]"
            else:
                label = f"[/{cmd}]" + (f" {cleaned}" if cleaned else "")
            events.append(NormalizedEvent(seq, uuid, ts, EventKind.LIFECYCLE_MARKER, label))
            return events
        if not cleaned:
            return events
        events.append(NormalizedEvent(seq, uuid, ts, EventKind.OPERATOR_TEXT, cleaned))

    return events


def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
    all_records = _load_records(path)

    # Tag every record with its absolute position in the full (unsliced)
    # file BEFORE any --since/--until slicing, and use that absolute
    # position -- never a position re-numbered from 0 after slicing -- as
    # the uuid fallback everywhere below. A record's real marker is its
    # uuid, falling back to f"line{i}" when absent; resolution must
    # replicate the exact fallback used when a marker was originally
    # emitted, or a marker from a uuid-less record (e.g. a system record in
    # some real sessions) resolves to the WRONG record on a later run once
    # an earlier --since has already sliced the list once. Concretely: if
    # emission re-enumerated the post-slice list from 0, a record's fallback
    # marker would depend on how many prior --since hops had already been
    # applied, so the same physical record could mint a different marker on
    # every chained run -- and a later run resolving an old marker against
    # a freshly re-parsed (unsliced) file would then land on the wrong
    # record entirely, silently re-emitting content already flushed to a
    # prior snapshot. Absolute, pre-slice position is stable across any
    # number of chained --since/--until runs since every run re-parses the
    # same on-disk file from scratch.
    indexed = list(enumerate(all_records))

    # These two values describe the whole transcript, not only the requested
    # output span. A --since cut may begin after the AskUserQuestion tool_use
    # that gives a later tool_result its QA_PAIR shape, and it may leave only
    # sidechain records in the visible suffix even though the file has a
    # primary thread. Derive both before slicing so one-shot extraction has
    # the same state as a forward parse of the complete file.
    has_primary_thread = any(not r.get("isSidechain") for r in all_records)
    askuserquestion_inputs: dict[str, list[Any]] = {}
    for rec in all_records:
        if rec.get("isSidechain") and has_primary_thread:
            continue
        if rec.get("type") != "assistant":
            continue
        for block in rec.get("message", {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                tid = block.get("id")
                if tid:
                    questions = block.get("input", {}).get("questions")
                    askuserquestion_inputs[tid] = questions if isinstance(questions, list) else []

    if config.since_marker is not None:
        idx = next((i for i, r in indexed if (r.get("uuid") or f"line{i}") == config.since_marker), None)
        if idx is None:
            raise ValueError(
                f"--since marker {config.since_marker!r} not found as a uuid in {path}"
            )
        indexed = [(i, r) for i, r in indexed if i > idx]

    if config.until_marker is not None:
        idx = next((i for i, r in indexed if (r.get("uuid") or f"line{i}") == config.until_marker), None)
        if idx is None:
            raise ValueError(
                f"--until marker {config.until_marker!r} not found as a uuid in {path}"
            )
        indexed = [(i, r) for i, r in indexed if i <= idx]

    # isSidechain targeting is auto-detected from the file's OWN content, not
    # a CLI flag (an earlier --include-sidechain flag was removed 2026-09-11,
    # operator design critique: the shared `nyxloom extract` surface should
    # stay adapter-agnostic -- "adapters solve the CLI specifics," not expose
    # a Claude-Code-only vocabulary word on the generic command). The signal
    # this replaces it with was verified exhaustively, not assumed: every
    # top-level interactive session file on this machine (59/59) has
    # isSidechain False/absent on EVERY record; every dispatched Agent-tool
    # subagent's own dedicated file (.../subagents/agent-<id>.jsonl, 358/358)
    # has isSidechain=True on EVERY record. Zero exceptions in either
    # direction -- no file mixes the two. So a file with no non-sidechain
    # record present has no "primary thread" to distinguish sidechain
    # content FROM -- dropping it there would silently zero out an entire
    # otherwise-valid file (exit 0, no warning), which is never the right
    # default. A file that DOES have a primary thread keeps the original
    # behavior of dropping sidechain records as noise (their originally-
    # documented case: parallel tool-call fan-out branches interleaved with
    # the real conversation -- not reproduced in this machine's corpus, but
    # the logic below still defends against it if a future session has it).
    # Net effect: targeting a specific subagent's own conversation is just
    # `nyxloom extract <path to that agent's file>` -- same as targeting any
    # other adapter's session, no separate flag required. See this module's
    # own docstring for the nested-subagent (subagent-spawns-subagent) case.
    state = StreamState(
        has_primary_thread=has_primary_thread, askuserquestion_inputs=askuserquestion_inputs
    )
    events: list[NormalizedEvent] = []
    for seq, (abs_i, rec) in enumerate(indexed):
        events += parse_record(rec, seq, f"line{abs_i}", config, state)

    return events
