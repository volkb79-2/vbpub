"""OpenAI Codex CLI adapter -- $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl
(default ``~/.codex/sessions``).

**Session storage and identity (source-level verification, Codex CLI 0.154.0,
matching upstream ``rust-v0.154.0``):** ``CODEX_HOME`` is the state root, not
an ID namespace. A normal new thread calls ``ThreadId::new()``, which calls
``Uuid::now_v7()``; ``SessionId::new()`` uses the same UUIDv7 generator, and
the ordinary rollout recorder derives ``session_id`` from the thread ID. The
rollout filename is ``rollout-<local-second>-<thread-id>.jsonl`` and the first
``session_meta`` record repeats the identity as both ``payload.id`` and
``payload.session_id``. UUIDv7 combines a millisecond timestamp with random
material from the OS; the uuid crate adds a process-local monotonic counter,
reseeded with randomness per millisecond. There is no cross-home reservation
or collision registry, so an independent cross-process collision is
astronomically unlikely but not a filesystem guarantee.

The writer's new-rollout path does not check for an existing filename. Its
``OpenOptions`` uses ``append(true).create(true)``: if a path already exists,
Codex appends to that JSONL file and does not generate a replacement ID. A
shared live ``sessions`` directory can therefore combine two sessions into
one stream; separate ``CODEX_HOME`` profiles also have separate writer-lock
directories. Renaming a rollout file alone is not an ID migration because
the embedded metadata and state/index references retain the original ID.
This is why ``locate.py`` treats every matching ``~/.codex*`` rollout as a
candidate and refuses a bare UUID when more than one path exists.

Schema facts verified directly against 400+ real local rollout files
spanning cli_version 0.142.2 through 0.151.0 (this machine has genuine
Codex CLI history going back months -- not guessed from documentation).
**Real, load-bearing finding from that spread: Codex's event_msg schema was
restructured entirely around cli_version 0.147.0 (2026-08-09).** Every file
from 0.147.0 onward uses ONLY the new shape below -- an adapter reading
just the old shape (this adapter's own first version) silently extracted
ZERO events from every real Codex session in the last month of local
history. Both generations are handled here; a real corpus this size having
a breaking schema change mid-window is itself the reason to always verify
an adapter against many real files spanning time, not a handful from one
period.

- One JSON object per line: {timestamp, ordinal, type, payload}. Top-level
  `type` in {"session_meta", "event_msg", "response_item", "turn_context",
  "world_state", "compacted" (new-schema only)}.
- `response_item` carries the raw API-level conversation. Ordinary tool calls
  stay hidden unless explicitly requested; `request_user_input_async` is the
  exception because it is the only source for every displayed UI prompt and
  its offered choices. These are normalized as `INTERVIEW:` prose at the call's
  source position.
- `event_msg` is a cleaner, already-classified layer, but its payload shape
  changed between generations:
  - OLD (cli_version <= ~0.145.0): `payload.type` directly names the kind --
    "user_message" (payload.message = operator text), "agent_message"
    (payload.message = assistant text, payload also carries "phase" e.g.
    "commentary" -- not yet used, a candidate future checkpoint signal),
    "context_compacted" (Codex's compaction-boundary marker).
  - NEW (cli_version >= 0.147.0): every real event arrives wrapped as
    `payload.type == "item_completed"`, `payload.item.type` naming the real
    kind -- "UserMessage" (item.content[].text where block type=="text"),
    "AgentMessage" (item.content[].text where block type=="Text" -- note
    the differing case between the two message kinds' block type strings,
    verified directly, not assumed consistent), "Reasoning" (see THINKING
    below), "ContextCompaction" (a content-free completion echo of the
    SAME event the top-level "compacted" record already carries with real
    text -- skipped here as redundant, not as unrecognized). Everything
    else under item_completed ("CommandExecution", "CollabAgentToolCall" --
    a sub-agent-spawning tool, verified via its "tool": "spawn_agent"
    field, not a Q&A mechanism -- "FileChange") is machine/tool noise.
  - Every other event_msg type in either generation (task_started,
    token_count, web_search_end, task_complete, patch_apply_end,
    turn_aborted, thread_settings_applied, thread_goal_updated,
    sub_agent_activity) is noise and is dropped.
- The NEW generation's compaction marker is a top-level `type: "compacted"`
  record (not nested under event_msg), whose `payload.message` is Codex's
  own real compaction summary text -- kept as the LIFECYCLE_MARKER's text,
  the direct analog of Claude Code's isCompactSummary content.
- `ordinal` is a per-line, monotonically increasing integer -- present on
  every record in every rollout file from 2026-08 onward (both the old and
  new event_msg shapes appear with it) but ABSENT on older ones (verified
  on a 2026-06/07 file: only {timestamp, type, payload} at the top level).
  Used directly as seq/marker when present. Without it, `event_msg` and
  `compacted` records retain their historical numeric index markers for
  cursor compatibility; newly surfaced `response_item` records use a
  namespaced `response_item-<position>` marker. --since chaining across an
  old/new Codex CLI version boundary is not guaranteed to line up.

Known gaps, left honest rather than guessed:
- This Codex interface records `request_user_input_async` as a
  `response_item.function_call`; each question and its offered choices are
  rendered as an `INTERVIEW:` block, even when Codex writes no assistant-prose
  copy. An identical assistant copy is deduplicated. A submitted answer arrives
  in a `UserMessage` containing a `send_user_message_question_reply` JSON
  envelope and is rendered as `OPERATOR:` prose at the reply's source
  position. The envelope's `questionItemId` links delayed answers to the right
  choices; exact selected-option text and arbitrary free text are both kept
  verbatim. Ordinary chat answers also remain visible as operator prose, with
  no guessed association when the record carries no question ID.
- **Sub-agent targeting (2026-09-11, corrected same day by a live test --
  the note this replaces was WRONG).** The original note here claimed
  Codex sub-agent activity is inline-only with no separate file, based on
  400+ pre-existing local rollout files that apparently never contained a
  real `spawn_agent` dispatch. A live test (`codex exec -m gpt-5.6-luna`,
  prompted to delegate one of two tasks via its collab-agent tool) proved
  the opposite: Codex creates a genuine SEPARATE rollout file per spawned
  sub-agent, one directory listing away from its dispatcher --
  `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread_id>.jsonl`, where
  `<thread_id>` is exactly the id returned in the parent's own
  `CollabAgentToolCall` event (`item.receiver_thread_ids`, alongside
  `item.receiver_agents[].agent_nickname` -- sub-agents get human-readable
  codenames, e.g. "Feynman" in the test run). The child file is a FULLY
  NORMAL, self-contained rollout (session_meta/event_msg/response_item/
  world_state/turn_context/token_usage_record, same as any top-level
  session) -- its `session_meta.payload` carries `thread_source:
  "subagent"` (vs `"user"` for a real top-level session -- a single,
  direct discriminator, cleaner than Claude Code's isSidechain, which
  needs a same-file-has-a-primary-thread heuristic -- see
  claude_code.py's parse()) plus `forked_from_id` / `source.subagent.
  thread_spawn.{parent_thread_id, depth, agent_nickname}` -- `depth`
  is the direct analog of Claude Code's `.meta.json` `spawnDepth`.
  Verified end to end: this adapter's existing `parse()`, UNCHANGED,
  already extracts a sub-agent's own rollout file correctly (its
  `UserMessage`/`AgentMessage`/`FileChange` records are schema-identical
  to a normal session) -- `nyxloom extract <that child rollout file>`
  works today, no adapter code change needed. What's still missing, same
  as Claude Code and opencode (see their own adapters' notes): a
  DISCOVERY step that lists a session's sub-agent files and their
  lineage without already knowing the thread id -- `list_sessions()`
  below still returns only `[str(path)]` (one file = one session, still
  correct per-file) and does not yet walk sibling rollout files for a
  shared root `session_id` / matching `forked_from_id` chain.
- **Superseded finding, corrected by wider real-data sampling**: an earlier
  version of this adapter (reading only `response_item`-layer `reasoning`
  items, which carry `encrypted_content`) concluded Codex's chain-of-
  thought was opaque. The NEW event_msg generation's "Reasoning" item
  carries a `raw_content` list of PLAIN-TEXT reasoning strings -- not
  encrypted at all. THINKING is now emitted from this field (new-generation
  files only) when config.include_thinking is set.
- **RESOLVED 2026-09-11** (was the open question directly above):
  `encrypted_content` in the OLD `response_item.reasoning` layer is
  genuinely, permanently unrecoverable -- not a config-dependent alternate
  code path to the same plaintext `raw_content` above. It's OpenAI's
  Responses API "encrypted reasoning items" feature (stateless mode:
  `store: false` or org-level Zero Data Retention). Mechanism: the
  reasoning payload is sealed by OpenAI's backend at generation time;
  Codex round-trips the opaque blob back on the next turn so the model can
  recall its own prior reasoning; the backend decrypts it server-side, in
  memory only, reconstructs reasoning context, then discards the plaintext
  -- the client never holds a key and cannot decrypt it, ever, by design
  (both a CoT-exposure policy and a ZDR-compliance mechanism at once).
  Confirmed by real-world failure, not just docs: openai/codex#25290 shows
  Codex's OWN later runs failing to replay locally-persisted
  `encrypted_content` it wrote itself ("the encrypted content ... could
  not be decrypted or parsed") after a backend-side key/format change --
  proof the opacity is real and outlives even the writing client's own
  later sessions, not an artifact of this adapter's own limited access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "codex"

_TOP_LEVEL_TYPES = {"event_msg", "compacted", "response_item"}
_LEGACY_MARKER_TYPES = {"event_msg", "compacted"}
_SKIPPED_ITEM_TYPES = {"CommandExecution", "CollabAgentToolCall", "FileChange", "ContextCompaction"}


_SNIFF_SCAN_LINES = 10


def sniff(path: Path) -> bool:
    """Not gated on path.suffix == ".jsonl" (removed 2026-09-11, see
    claude_code.py's sniff() docstring for the motivating case) -- the
    session_meta + payload.cli_version content check below was already the
    real discriminator for every .jsonl-suffixed file too."""
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
                if (
                    isinstance(obj, dict)
                    and obj.get("type") == "session_meta"
                    and isinstance(obj.get("payload"), dict)
                    and "cli_version" in obj["payload"]
                ):
                    return True
    except OSError:
        return False
    return False


def list_sessions(path: Path) -> list[str]:
    return [str(path)]


def _first_session_meta(path: Path) -> dict | None:
    """The FIRST session_meta record's own payload -- a rollout file that
    is itself a sub-agent's own file carries its OWN session_meta first,
    THEN (in a real file this session inspected) a second session_meta
    copying the root/parent's -- confirmed by real ordinal order (0, then
    1), not assumed. Only a few lines are scanned, not the whole file."""
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
                if isinstance(obj, dict) and obj.get("type") == "session_meta":
                    payload = obj.get("payload")
                    return payload if isinstance(payload, dict) else None
    except OSError:
        return None
    return None


def _scan_event_counts(path: Path) -> tuple[int, str | None, str | None]:
    count = 0
    first_ts: str | None = None
    last_ts: str | None = None
    try:
        with path.open("r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("type") in _TOP_LEVEL_TYPES:
                    count += 1
                    ts = obj.get("timestamp")
                    if ts:
                        first_ts = first_ts or ts
                        last_ts = ts
    except OSError:
        pass
    return count, first_ts, last_ts


def list_agents(path: Path) -> list["SessionNode"]:
    """Discovery for `nyxloom extract-sessions` -- every rollout file that
    shares path's own root `session_id`, walked from real, verified schema
    (2026-09-11 live test, corrected this module's earlier wrong "inline
    only" claim -- see the "Sub-agent targeting" note above). path may be
    the top-level session's own file or a specific sub-agent's file --
    either way the search is by shared session_id, so the whole family is
    found regardless of which member path names.

    A depth>1 chain (a sub-agent that itself spawned a sub-agent) was not
    produced by the one live test this was verified against -- `depth`/
    `parent_thread_id` come directly from `source.subagent.thread_spawn`
    when present, so a real deeper chain should resolve correctly if one
    is ever seen, but that specific case is UNVERIFIED, not guessed away.
    """
    from ..sessions import SessionNode

    own_meta = _first_session_meta(path)
    if own_meta is None:
        return []
    root_id = own_meta.get("session_id")
    if root_id is None:
        return []

    sessions_root = next((anc for anc in path.parents if anc.name == "sessions"), path.parent)

    nodes = []
    for candidate in sessions_root.glob("**/rollout-*.jsonl"):
        meta = _first_session_meta(candidate)
        if meta is None or meta.get("session_id") != root_id:
            continue
        cid = meta.get("id") or str(candidate)
        thread_source = meta.get("thread_source")
        # `source` is a plain string ("exec") for a real top-level session
        # and only an object ({"subagent": {"thread_spawn": {...}}}) for a
        # real sub-agent's own file -- verified directly, not assumed
        # consistent (see codex.py's module docstring's session_meta dump).
        raw_source = meta.get("source")
        subagent = raw_source.get("subagent") if isinstance(raw_source, dict) else None
        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        spawn = spawn if isinstance(spawn, dict) else {}
        is_subagent = thread_source == "subagent"
        depth = spawn.get("depth", 1) if is_subagent else 0
        parent_id = meta.get("forked_from_id") or spawn.get("parent_thread_id")
        label = spawn.get("agent_nickname") or ("(sub-agent)" if is_subagent else "(interactive session)")
        if is_subagent:
            label = f"{label} (depth {depth})"
        count, first_ts, last_ts = _scan_event_counts(candidate)
        nodes.append(SessionNode(
            id=cid, parent_id=parent_id, depth=depth, label=label, path=str(candidate),
            record_count=count, first_ts=first_ts, last_ts=last_ts,
        ))
    return nodes


def _item_text(item: dict, block_type: str) -> str:
    blocks = item.get("content") or []
    return "\n".join(
        b.get("text", "") for b in blocks
        if isinstance(b, dict) and b.get("type", "").lower() == block_type
    )


@dataclass
class StreamState:
    """Question metadata shared by a forward Codex parse or follow stream."""

    questions: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict)
    pending_prose_copies: list[tuple[list[dict[str, Any]], bool]] = field(default_factory=list)

    def remember_question_call(
        self, payload: dict[str, Any], *, prompt_emitted: bool = True,
    ) -> list[dict[str, Any]]:
        prompts = _question_call_items(payload)
        call_id = payload.get("call_id") or payload.get("id")
        if isinstance(call_id, str):
            for prompt in prompts:
                self.questions[(call_id, prompt["item_index"])] = prompt
        if prompts:
            self.pending_prose_copies.append((prompts, prompt_emitted))
        return prompts

    def question_for_reply(self, row: dict[str, Any]) -> dict[str, Any] | None:
        key = _question_item_key(row.get("questionItemId"))
        if key is not None and key in self.questions:
            return self.questions[key]
        question = row.get("question")
        if isinstance(question, str):
            matches = [
                prompt for prompt in self.questions.values()
                if question in (prompt.get("title"), prompt.get("question"))
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    def consume_question_prose_copy(self, text: str) -> list[dict[str, Any]] | None:
        """Consume an exact assistant rendering of a known UI question.

        Return ``[]`` when its marked prompt was already emitted, the matching
        prompt records when a since/follow boundary excluded the call, and
        ``None`` when the assistant text is not a question copy.
        """
        normalized = _normalize_question_prose(text)
        if not normalized:
            return None
        blocks = _question_prose_blocks(text)
        for group_index, (prompts, prompt_emitted) in enumerate(self.pending_prose_copies):
            combined = _normalize_question_prose("\n\n".join(
                _question_copy_text(prompt) for prompt in prompts
            ))
            if normalized == combined:
                self.pending_prose_copies.pop(group_index)
                return [] if prompt_emitted else prompts
            # Codex sometimes copies an entire multi-question call into one
            # AgentMessage. Match the rows as separate blank-line-delimited
            # blocks too, including when each row's title differs from its
            # question text.
            if len(blocks) == len(prompts) and all(
                block in {
                    _normalize_question_prose(copy_text)
                    for copy_text in _question_copy_variants(prompt)
                }
                for block, prompt in zip(blocks, prompts)
            ):
                self.pending_prose_copies.pop(group_index)
                return [] if prompt_emitted else prompts
            for prompt_index, prompt in enumerate(prompts):
                if normalized in {
                    _normalize_question_prose(copy_text)
                    for copy_text in _question_copy_variants(prompt)
                }:
                    prompts.pop(prompt_index)
                    if not prompts:
                        self.pending_prose_copies.pop(group_index)
                    return [] if prompt_emitted else [prompt]
        return None

    def clear_pending_prose_copies(self) -> None:
        self.pending_prose_copies.clear()


def _question_item_key(value: Any) -> tuple[str, int] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if (
        isinstance(value, list)
        and len(value) >= 3
        and value[0] == "request_user_input_async"
        and isinstance(value[1], str)
        and isinstance(value[2], int)
        and not isinstance(value[2], bool)
    ):
        return value[1], value[2]
    return None


def _option_text(option: Any) -> str | None:
    if isinstance(option, str):
        return option
    if not isinstance(option, dict):
        return None
    label = option.get("label") or option.get("title") or option.get("value")
    description = option.get("description")
    if isinstance(label, str) and isinstance(description, str) and description and description != label:
        return f"{label}: {description}"
    return label if isinstance(label, str) else None


def _question_call_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    arguments = payload.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return []
    if not isinstance(arguments, dict):
        return []
    rows = arguments.get("questions")
    if not isinstance(rows, list):
        return []
    prompts = []
    for item_index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        title = row.get("title")
        question = row.get("question")
        if not isinstance(title, str) or not title.strip():
            title = None
        if not isinstance(question, str) or not question.strip():
            question = None
        if title is None and question is None:
            continue
        options = row.get("options")
        option_texts = tuple(
            text for text in (_option_text(option) for option in options or [])
            if text is not None
        ) if isinstance(options, list) else ()
        prompts.append({
            "item_index": item_index,
            "title": title,
            "question": question,
            "options": option_texts,
        })
    return prompts


def _question_header(prompt: dict[str, Any]) -> str:
    title = prompt.get("title")
    question = prompt.get("question")
    if isinstance(title, str) and isinstance(question, str) and title != question:
        return f"INTERVIEW: {title}\n{question}"
    text = question if isinstance(question, str) else title
    return f"INTERVIEW: {text or '[question text unavailable]'}"


def _format_question_prompt(prompt: dict[str, Any]) -> str:
    lines = [_question_header(prompt)]
    lines.extend(f"- {option}" for option in prompt.get("options", ()))
    return "\n".join(lines)


def _question_copy_variants(prompt: dict[str, Any]) -> tuple[str, ...]:
    headers = [
        value for value in (prompt.get("title"), prompt.get("question"))
        if isinstance(value, str) and value
    ]
    options = [f"- {option}" for option in prompt.get("options", ())]
    variants = ["\n".join([header, *options]) for header in dict.fromkeys(headers)]
    if len(headers) > 1:
        variants.append("\n".join([*headers, *options]))
    return tuple(variants)


def _question_copy_text(prompt: dict[str, Any]) -> str:
    variants = _question_copy_variants(prompt)
    return variants[0] if variants else ""


def _normalize_question_prose(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _question_prose_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    lines: list[str] = []
    for line in text.splitlines():
        if line.strip():
            lines.append(line.strip())
        elif lines:
            blocks.append("\n".join(lines))
            lines = []
    if lines:
        blocks.append("\n".join(lines))
    return blocks


def _advance_stream_state(
    rec: dict[str, Any], state: StreamState, *, prompt_emitted: bool,
) -> None:
    """Replay one already-consumed record into the question-correlation state."""
    payload = rec.get("payload", {}) or {}
    if (
        rec.get("type") == "response_item"
        and payload.get("type") == "function_call"
        and payload.get("name") == "request_user_input_async"
    ):
        state.remember_question_call(payload, prompt_emitted=prompt_emitted)
        return

    ptype = payload.get("type")
    if ptype == "user_message":
        if payload.get("message"):
            state.clear_pending_prose_copies()
        return
    if ptype == "agent_message":
        text = payload.get("message", "")
        if text:
            state.consume_question_prose_copy(text)
        return
    if ptype == "item_completed":
        item = payload.get("item") or {}
        if item.get("type") == "UserMessage":
            if _item_text(item, "text"):
                state.clear_pending_prose_copies()
        elif item.get("type") == "AgentMessage":
            text = _item_text(item, "text")
            if text:
                state.consume_question_prose_copy(text)


def prime_stream_state(
    path: Path, state: StreamState, upto_bytes: int, since_marker: str | None = None,
) -> None:
    """Restore question ids/options before a live-follow anchor.

    The one-shot prefix has rendered question calls after its exclusive
    ``since_marker``. Mark only those prompts as emitted so a delayed
    assistant prose copy in the live tail is suppressed instead of repeating
    the question. A pre-since call is kept as state, but its first visible
    prose copy remains the marked prompt for this delta.
    """
    if upto_bytes <= 0:
        return
    records: list[dict[str, Any]] = []
    consumed = 0
    try:
        handle = Path(path).open("rb")
    except OSError:
        return
    with handle:
        for raw_line in handle:
            next_consumed = consumed + len(raw_line)
            if next_consumed > upto_bytes:
                break
            consumed = next_consumed
            try:
                rec = json.loads(raw_line)
            except (TypeError, ValueError):
                continue
            if isinstance(rec, dict) and is_top_level_record(rec):
                records.append(rec)

    fallbacks = _fallback_markers(records)
    since_index = None
    if since_marker is not None:
        since_index = next((
            index for index, rec in enumerate(records)
            if str(rec.get("ordinal", fallbacks[index])) == since_marker
        ), None)
    for index, rec in enumerate(records):
        prompt_emitted = since_marker is None or (
            since_index is not None and index > since_index
        )
        _advance_stream_state(rec, state, prompt_emitted=prompt_emitted)


def _format_question_reply(text: str, state: StreamState | None = None) -> str | None:
    """Decode Codex's user-facing question reply without losing free text.

    Each JSON row carries its own question and answer. `questionItemId` ties it
    to the original request so the declared choices can be included. Choice
    selections and arbitrary free text use the same path and are preserved
    verbatim. Unknown shapes return None so the caller preserves the original
    operator text instead of partially decoding it.
    """
    raw = text.strip()
    opening = "<send_user_message_question_reply>"
    closing = "</send_user_message_question_reply>"
    if not raw.startswith(opening) or not raw.endswith(closing):
        return None
    body = raw[len(opening):-len(closing)].strip()
    try:
        rows = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list) or not rows:
        return None

    blocks: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        question = row.get("question")
        answer = row.get("answer")
        if not isinstance(question, str) or not question.strip() or not isinstance(answer, str):
            return None
        prompt = state.question_for_reply(row) if state is not None else None
        if prompt is not None:
            question_text = prompt.get("question") or prompt.get("title") or question
            prompt_copy = _format_question_prompt({**prompt, "question": question_text})
        else:
            prompt_copy = f"INTERVIEW: {question}"
        blocks.append(f"{prompt_copy}\n\nOPERATOR: {answer}")
    return "\n\n".join(blocks)


def _operator_event(
    seq: int, marker: str, ts: str, text: str, state: StreamState | None = None,
) -> NormalizedEvent:
    """Normalize plain operator prose or a structured question reply."""
    qa_text = _format_question_reply(text, state)
    if qa_text is not None:
        return NormalizedEvent(seq, marker, ts, EventKind.QA_PAIR, qa_text)
    return NormalizedEvent(seq, marker, ts, EventKind.OPERATOR_TEXT, text)


def is_top_level_record(rec: dict) -> bool:
    """Whether this raw record is one parse() would even look at -- the
    event_msg/compacted/response_item universe that also defines this adapter's own
    ordinal-fallback marker space. Public because follow.py's tailer decides
    the same thing about each newly-arrived line, and because a disagreement
    there would silently shift every fallback marker."""
    return rec.get("type") in _TOP_LEVEL_TYPES


def _fallback_markers(records: list[dict]) -> list[str]:
    """Fallback marker per filtered rollout record.

    Older nyxloom releases numbered only event_msg/compacted records. Keep
    those marker values stable when adding response_item parsing so existing
    --since-file cursors still resolve. Newly surfaced response items, which
    had no old marker, receive a namespaced position.
    """
    legacy_index = 0
    markers: list[str] = []
    for position, rec in enumerate(records):
        if rec.get("type") in _LEGACY_MARKER_TYPES:
            markers.append(str(legacy_index))
            legacy_index += 1
        else:
            markers.append(f"response_item-{position}")
    return markers


def parse_record(
    rec: dict, seq: int, fallback_marker: str, config: ExtractConfig,
    state: StreamState | None = None,
) -> list[NormalizedEvent]:
    """One raw record -> the NormalizedEvents it yields (usually zero or one).

    Factored out of parse()'s own loop (2026-09-12) so follow.py's
    incremental tailer applies literally the same per-record rules to a
    newly-appended line as a full parse does. `fallback_marker` is the
    marker for a record with no `ordinal` of its own (real pre-2026-08
    rollout files have none -- see the module docstring): parse() passes its
    absolute pre-slice position, follow.py a stream-local token. The optional
    state carries request_user_input_async question metadata so prose copies
    can be normalized once and later replies can include the declared choices.
    """
    if state is None:
        state = StreamState()
    ordinal = str(rec.get("ordinal", fallback_marker))
    ts = rec.get("timestamp", "")
    payload = rec.get("payload", {}) or {}
    ptype = payload.get("type")

    if rec.get("type") == "compacted":
        # NEW generation's top-level compaction record -- carries Codex's own
        # real compaction summary text, unlike the OLD generation's
        # content-free "context_compacted" event_msg.
        text = payload.get("message", "") or "[compacted]"
        if config.hide_compaction_content:
            text = "[compaction summary omitted]"
        event = NormalizedEvent(seq, ordinal, ts, EventKind.LIFECYCLE_MARKER, text)
        event.meta["boundary_type"] = "compaction"
        return [event]

    if rec.get("type") == "response_item":
        item_type = payload.get("type")
        if item_type == "function_call" and payload.get("name") == "request_user_input_async":
            prompts = state.remember_question_call(payload)
            return [
                NormalizedEvent(
                    seq, ordinal, ts, EventKind.QA_PAIR,
                    _format_question_prompt(prompt),
                )
                for prompt in prompts
            ]
        if item_type == "custom_tool_call" and config.show_tool_calls:
            name = str(payload.get("name") or "unknown")
            intent = ""
            raw_input = payload.get("input")
            if config.show_tool_call_intent and isinstance(raw_input, dict):
                description = raw_input.get("description") or raw_input.get("intent")
                if isinstance(description, str):
                    intent = " ".join(description.split())[:240]
            label = f"[tool call: {name}]" + (f" {intent}" if intent else "")
            return [NormalizedEvent(seq, ordinal, ts, EventKind.TOOL_CALL, label)]
        return []

    if ptype == "user_message":  # OLD generation
        text = payload.get("message", "")
        if text:
            event = _operator_event(seq, ordinal, ts, text, state)
            state.clear_pending_prose_copies()
            return [event]
    elif ptype == "agent_message":  # OLD generation
        text = payload.get("message", "")
        if text:
            copy_prompts = state.consume_question_prose_copy(text)
            if copy_prompts is not None:
                return [NormalizedEvent(
                    seq, ordinal, ts, EventKind.QA_PAIR, _format_question_prompt(prompt),
                ) for prompt in copy_prompts]
            return [NormalizedEvent(seq, ordinal, ts, EventKind.ASSISTANT_TEXT, text)]
    elif ptype == "context_compacted":  # OLD generation
        event = NormalizedEvent(seq, ordinal, ts, EventKind.LIFECYCLE_MARKER, "[context_compacted]")
        event.meta["boundary_type"] = "compaction"
        return [event]
    elif ptype == "item_completed":  # NEW generation
        item = payload.get("item") or {}
        itype = item.get("type")
        if itype == "UserMessage":
            text = _item_text(item, "text")
            if text:
                event = _operator_event(seq, ordinal, ts, text, state)
                state.clear_pending_prose_copies()
                return [event]
        elif itype == "AgentMessage":
            text = _item_text(item, "text")
            if text:
                copy_prompts = state.consume_question_prose_copy(text)
                if copy_prompts is not None:
                    return [NormalizedEvent(
                        seq, ordinal, ts, EventKind.QA_PAIR, _format_question_prompt(prompt),
                    ) for prompt in copy_prompts]
                return [NormalizedEvent(seq, ordinal, ts, EventKind.ASSISTANT_TEXT, text)]
        elif itype == "Reasoning" and config.include_thinking:
            text = "\n".join(item.get("raw_content") or [])
            if text:
                return [NormalizedEvent(seq, ordinal, ts, EventKind.THINKING, text)]
        # else: itype in _SKIPPED_ITEM_TYPES, or an unrecognized future item
        # type -- either way, machine/tool noise, not emitted.
        # CollabAgentToolCall (sub-agent dispatch) included: the spawned
        # sub-agent's OWN conversation is NOT here -- it lives in its own
        # separate rollout file (item.receiver_thread_ids names it) -- see the
        # module docstring's "Sub-agent targeting" note.
    return []


def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
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
            if isinstance(obj, dict) and is_top_level_record(obj):
                raw.append(obj)

    # Keep stable pre-slice marker fallbacks. Older marker values for
    # event_msg/compacted records remain their index in that original
    # two-type universe; response_item records get a new namespaced fallback
    # so adding them cannot invalidate a saved --since-file cursor.
    indexed = list(enumerate(raw))
    fallback_markers = _fallback_markers(raw)
    indexed_with_markers = [
        (index, rec, fallback_markers[index]) for index, rec in indexed
    ]
    all_indexed_with_markers = indexed_with_markers
    since_index: int | None = None

    if config.since_marker is not None:
        idx = next((i for i, r, fallback in indexed_with_markers
                    if str(r.get("ordinal", fallback)) == config.since_marker), None)
        if idx is None:
            raise ValueError(f"--since marker {config.since_marker!r} not found as a source marker in {path}")
        since_index = idx
        indexed_with_markers = [row for row in indexed_with_markers if row[0] > idx]

    if config.until_marker is not None:
        idx = next((i for i, r, fallback in indexed_with_markers
                    if str(r.get("ordinal", fallback)) == config.until_marker), None)
        if idx is None:
            raise ValueError(f"--until marker {config.until_marker!r} not found as a source marker in {path}")
        indexed_with_markers = [row for row in indexed_with_markers if row[0] <= idx]

    state = StreamState()
    if since_index is not None:
        # Rebuild question state through the exclusive anchor. If a question
        # call itself is before the span but its prose copy is after it, the
        # visible copy becomes the marked prompt for this delta.
        for index, rec, _fallback in all_indexed_with_markers:
            if index > since_index:
                break
            _advance_stream_state(rec, state, prompt_emitted=False)

    events: list[NormalizedEvent] = []
    for seq, (_abs_i, rec, fallback) in enumerate(indexed_with_markers):
        events += parse_record(rec, seq, fallback, config, state)

    return events
