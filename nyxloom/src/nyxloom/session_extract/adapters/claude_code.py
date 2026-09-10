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
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "claude-code"

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
    """Render an AskUserQuestion tool_result as, per question: the question
    text, every declared option as a bullet list, a blank line, then
    `OPERATOR: <the actual answer>` -- a batch answering several questions
    at once gets one such block per question, blank line between blocks
    (operator-reported, 2026-09-10: the harness's own verbatim
    '"Q"="A"'-joined string was unreadable). Falls back to the raw string
    unmodified if re-splitting doesn't line up (see _split_qa_pairs) --
    never raises, never silently drops content it couldn't parse.
    """
    if not questions:
        return text
    pairs = _split_qa_pairs(text, questions)
    if pairs is None:
        return text
    blocks = []
    for (qtext, answer), q in zip(pairs, questions):
        options = q.get("options") if isinstance(q, dict) else None
        lines = [qtext]
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
    lines, so this scans forward rather than trusting line 1 alone."""
    if path.suffix != ".jsonl":
        return False
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


def _strip_harness_tags(text: str) -> str:
    text = _COMMAND_ARGS_RE.sub(r"\1", text)  # unwrap, don't discard
    return _HARNESS_TAG_RE.sub("", text).strip()


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
            if obj.get("type") in ("user", "assistant", "system"):
                records.append(obj)
    return records


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

    # tool_use_id -> its own input.questions list (question/header/options),
    # captured here (not just an id set) so the tool_result branch below can
    # re-render the Q&A pair with each question's real options shown,
    # instead of the harness's own flattened '"Q"="A"' string verbatim.
    askuserquestion_inputs: dict[str, list[Any]] = {}
    for _, rec in indexed:
        if rec.get("type") != "assistant":
            continue
        for block in rec.get("message", {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                tid = block.get("id")
                if tid:
                    questions = block.get("input", {}).get("questions")
                    askuserquestion_inputs[tid] = questions if isinstance(questions, list) else []
    askuserquestion_ids = set(askuserquestion_inputs)

    events: list[NormalizedEvent] = []
    for seq, (abs_i, rec) in enumerate(indexed):
        if rec.get("isSidechain"):
            continue

        uuid = rec.get("uuid") or f"line{abs_i}"
        ts = rec.get("timestamp", "")
        rtype = rec.get("type")

        if rtype == "system":
            if rec.get("subtype") == "compact_boundary":
                events.append(NormalizedEvent(seq, uuid, ts, EventKind.LIFECYCLE_MARKER, "[compact boundary]"))
            continue

        if rtype == "assistant":
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
                    b.get("text", "") for b in rec.get("message", {}).get("content", []) or []
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
                continue
            content = rec.get("message", {}).get("content", []) or []
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
            continue

        if rtype == "user":
            if rec.get("isCompactSummary"):
                events.append(NormalizedEvent(seq, uuid, ts, EventKind.LIFECYCLE_MARKER, "[compact summary]"))
                continue

            if rec.get("interruptedMessageId"):
                # Claude Code's OWN synthetic "[Request interrupted by user]"
                # record, injected when Ctrl+C cuts off a running response --
                # not real operator intent (operator-reported, 2026-09-10: it
                # was rendering as an ordinary all-zero OPERATOR_TEXT row,
                # indistinguishable from real content). interruptedMessageId
                # is a structural field the harness sets specifically for
                # this record, not a text-match on the message body, so this
                # can't misfire on a real prompt that happens to contain that
                # phrase.
                continue

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
                    if block.get("type") == "tool_result" and block.get("tool_use_id") in askuserquestion_ids:
                        c = block.get("content")
                        qa_text = c if isinstance(c, str) else json.dumps(c)
                        qa_tool_id = block.get("tool_use_id")
                if qa_text is not None:
                    formatted = _format_qa_pairs(qa_text, askuserquestion_inputs.get(qa_tool_id, []))
                    events.append(NormalizedEvent(seq, uuid, ts, EventKind.QA_PAIR, formatted))
                    continue
                text_blocks = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if not text_blocks:
                    continue
                raw_text = "\n".join(text_blocks)
            elif isinstance(content, str):
                raw_text = content
            else:
                continue

            if rec.get("isMeta") or rec.get("isVisibleInTranscriptOnly"):
                continue

            cmd = _command_name(raw_text)
            cleaned = _strip_harness_tags(raw_text)
            if cmd in _LIFECYCLE_COMMANDS:
                label = f"[/{cmd}]" + (f" {cleaned}" if cleaned else "")
                events.append(NormalizedEvent(seq, uuid, ts, EventKind.LIFECYCLE_MARKER, label))
                continue
            if not cleaned:
                continue
            events.append(NormalizedEvent(seq, uuid, ts, EventKind.OPERATOR_TEXT, cleaned))

    return events
