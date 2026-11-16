"""pi (``@earendil-works/pi-coding-agent``) session records.

Two on-disk shapes carry the same conversation model, and this one adapter
reads both:

``stored session`` -- ``~/.pi/agent/sessions/<encoded-cwd>/<time>_<uuid>.jsonl``
    pi's own durable session file. Record types verified against the real
    files in that tree: ``session`` (first; ``version``, ``id``, ``cwd``),
    ``model_change``, ``thinking_level_change``, ``message`` (the message
    object nested under ``message``), ``compaction`` (a ``summary`` string),
    ``branch_summary``, ``custom_message``, ``context_edit``. A ``resume``
    appends further records to the SAME file, so one file can hold several
    agent cycles but exactly one ``session`` header. The complete set pi
    DECLARES is read from the installed package itself
    (``dist/core/session-manager.d.ts``'s ``FileEntry`` union), never guessed:
    ``session``, ``message``, ``thinking_level_change``, ``model_change``,
    ``usage``, ``compaction``, ``branch_summary``, ``custom``, ``custom_message``,
    ``context_edit``, ``label``, ``session_info``.

``run stream`` -- what ``pi --mode json`` writes for one run (the controller
    keeps these under its own ``codex/runs/*.jsonl``). Record types:
    ``session``, ``agent_start``, ``turn_start``, ``message_start``,
    ``message_update`` (streaming deltas), ``message_end`` (the authoritative
    message), ``tool_execution_start``/``..._update``/``..._end``,
    ``turn_end``, ``compaction_start``/``compaction_end``, ``agent_end``,
    ``agent_settled``.

What is read, and why:

* ``user`` text content -> ``OPERATOR_TEXT``. pi's ``system`` message carries
  the harness preamble/rules/skills, which is harness framing, never operator
  intent (a real stored session records it as ``role: "system"`` with
  ``sections``).
* ``assistant`` content blocks -> one event per block in content order:
  ``text`` -> ``ASSISTANT_TEXT``, ``thinking`` -> ``THINKING`` (only under
  ``--include-thinking``, like every other adapter), ``toolCall`` ->
  ``TOOL_CALL`` when the configured tool-call mode renders it.
* ``toolResult`` messages -> a FAILED result (``isError``) renders as
  ``[tool error: <tool>] <call> => <result>`` under ``--tool-errors show``.
  A successful result is never rendered: results are the noise this whole
  tool exists to discard, and the failure path is the one kept because it
  explains why the session changed course.
* ``compaction`` / ``compaction_end`` -> ``LIFECYCLE_MARKER`` with
  ``boundary_type="compaction"``; the summary text is included only under
  ``--show-compaction-content`` (the rule Claude Code's markers follow).
* ``branch_summary`` (record) and the ``branchSummary`` / ``compactionSummary``
  message roles -> ``LIFECYCLE_MARKER`` (``boundary_type="branch_summary"`` /
  ``"compaction_summary"``). pi converts all of them into ``user`` messages in
  ``buildSessionContext`` (``dist/core/messages.js``), so they are content the
  MODEL SAW and cannot be dropped silently; their summary text follows the same
  ``--show-compaction-content`` rule as a compaction's.
* ``custom_message`` (record) and the ``custom`` message role -> ``OPERATOR_TEXT``,
  because pi also converts them into a ``user`` message (an extension-injected
  one). The text is labelled ``[custom message: <customType>]`` and
  ``meta["pi_context_source"]`` says it came from an extension, so it can
  neither be mistaken for the operator's own words nor vanish.
* the ``bashExecution`` message role (a ``!`` command run inside pi) -> a
  tool-like ``TOOL_CALL`` line, ``[bash execution: $ <cmd>] exit=N``, tagged
  when pi excludes it from model context (``excludeFromContext``).
* ``context_edit`` (record) -> a ``LIFECYCLE_MARKER`` naming the target it
  edits, plus a count in the skip report: ``replacement: null`` REMOVES that
  target from the model's context (``dist/core/session-manager.d.ts``
  ``projectContextEntry``), while this extract still shows the original content.
* A ``stopReason: "error"`` assistant message also yields an
  ``[API ERROR: ...]`` assistant-channel event, so the shared
  ``--show-api-errors`` rule hides a failed model call by default, as it
  does for Claude Code.
* A Bash ``toolCall`` naming a ``--agent-launch-pattern`` launcher contributes
  ``AGENT-OUT``/``AGENT-IN`` through ``agentcomms.launch_events`` when
  ``--agent-comms`` is not ``off``. pi has no Agent/Task/SendMessage tool, so
  a controller launcher run inside pi is the only agent-communication shape
  that exists here.

Deliberately NOT read -- and COUNTED, never silently dropped:

* ``message_start`` / ``message_update`` of a run stream. ``message_end``
  carries the finished message and the earlier records are mostly cumulative
  partial snapshots (one real 292k-line run stream was 99.2% ``message_update``
  records), so reading them would emit the same message hundreds of times.
* ``tool_execution_*``. The call is in the assistant message and its outcome
  is in the ``toolResult`` message; these records repeat that pair. stopstate
  reads them instead, where the pair IS the in-flight evidence.
* ``turn_end`` / ``agent_end`` (they repeat the turn's own message) and
  ``agent_settled`` (a scheduling tick, not conversation content).
* file-entry metadata that carries no conversation content: ``custom`` (pi's
  own declaration says it does NOT participate in LLM context),
  ``label``, ``session_info``, ``usage``, ``model_change``,
  ``thinking_level_change``, ``compaction_start`` (``compaction_end``/
  ``compaction`` is the record that carries the summary).
* a ``system`` message: the harness preamble/rules/skills, which is framing,
  never operator intent (a real stored session records it as
  ``role: "system"`` with ``sections``).
* a SUCCESSFUL ``toolResult``: results are the noise this whole tool exists to
  discard, and only the failure path is kept because it explains why the
  session changed course.

NOTHING is dropped silently. ``parse_with_stats`` returns a ``PiSkipReport``
counting every record type, message role and content block this adapter does
not render, keeping the KNOWN-deliberate skips (the lists above, verified from
pi's own declarations) apart from UNKNOWN ones (anything else, which must be
visible) -- ``extract`` renders it as a footer line. It also counts
unparseable/non-object lines, image blocks (text-only extraction cannot carry
them), and records whose ``parentId`` breaks the linear chain: pi sessions are
TREES (``parentId``) and this adapter follows FILE ORDER, which is stated
rather than implied. ``parse()`` itself stays the plain Protocol method
(events only); ``parse_with_stats()`` is the same parse plus the report.

Timestamps: a message's own ``timestamp`` is epoch-milliseconds when it is
an int (real stored sessions carry those too, e.g. ``1794705004387``), its
ISO string when it is a non-empty string, and the WRAPPER record's ISO string
when the message carries none; a record with neither yields the normalized
empty timestamp, which is the honest representation rather than an invented
one.

Markers are absolute, pre-slice ``line<N>`` positions of the record that
produced the event -- the same stable fallback ``reasonix`` uses, so a marker
from an earlier ``--since`` hop still resolves to the same physical record on
a later run.

Stop-state (``stopstate.py``) is where pi's own ``stopReason`` vocabulary
becomes a cause: ``length`` is CONTEXT-FULL, ``error`` is ERROR, an
``assistant`` message with ``stopReason`` ``stop`` and no prose is
EMPTY-COMPLETION, and a session ending on ``toolUse`` is TOOL-USE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import agentcomms, toolresult
from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "pi"

_SNIFF_SCAN_LINES = 10
COMPACTION_LABEL = "[compact boundary]"
COMPACTION_SUMMARY_LABEL = "[compact summary]"
BRANCH_LABEL = "[branch boundary]"
BRANCH_SUMMARY_LABEL = "[branch summary]"
CONTEXT_EDIT_LABEL = "[context edit]"
#: Record types that carry a conversation message (``message`` in a stored
#: session, ``message_end`` in a run stream).
MESSAGE_RECORD_TYPES = ("message", "message_end")
#: Record types that mark a compaction boundary in either shape.
COMPACTION_RECORD_TYPES = ("compaction", "compaction_start", "compaction_end")

#: Record types pi DECLARES that carry NO conversation content, so this adapter
#: skips them on purpose -- and counts every one of them so the skip is visible.
#: Two sources, both read from the installed package rather than guessed:
#: ``dist/core/session-manager.d.ts`` (the ``FileEntry`` union) for the stored
#: session, and ``dist/core/agent-session.js``'s own emitted-event set for a
#: ``--mode json`` run stream. Anything NOT in here and not rendered is reported
#: as UNKNOWN instead, so a new record type cannot hide.
DELIBERATE_SKIP_RECORDS: frozenset[str] = frozenset({
    # stored-session file entries that are metadata/extension state only
    "session", "model_change", "thinking_level_change", "custom", "label",
    "session_info", "usage",
    # ``compaction_start`` opens the boundary; ``compaction_end``/``compaction``
    # is the record that carries the summary, and is rendered.
    "compaction_start",
    # run-stream lifecycle: repeat the message they belong to, or are ticks
    "agent_before_settle", "agent_end", "agent_settled", "agent_start",
    "auto_retry_end", "auto_retry_start",
    "entry_appended", "message_start", "message_update",
    "model_select", "queue_update",
    "session_before_compact", "session_before_tree", "session_compact",
    "session_compact_failed", "session_info_changed", "session_shutdown",
    "session_start", "session_tree",
    "summarization_retry_attempt_start", "summarization_retry_finished",
    "summarization_retry_scheduled",
    "thinking_level_changed", "thinking_level_select",
    "tool_execution_end", "tool_execution_start", "tool_execution_update",
    "turn_end", "turn_start",
    "bash_execution_update",
})
#: Message roles pi DECLARES that never reach the model as content of their own
#: (``system`` is framing; a successful ``toolResult`` is noise), counted as
#: deliberate skips.
DELIBERATE_SKIP_ROLES: frozenset[str] = frozenset({"system"})
#: Content block types pi DECLARES that this text extractor cannot carry.
DELIBERATE_SKIP_BLOCKS: frozenset[str] = frozenset({"image"})


def record_type(rec: dict[str, Any]) -> str:
    value = rec.get("type")
    return value if isinstance(value, str) else ""


def message_of(rec: dict[str, Any]) -> dict[str, Any] | None:
    """The message object a conversation record carries, if any."""
    if record_type(rec) not in MESSAGE_RECORD_TYPES:
        return None
    message = rec.get("message")
    return message if isinstance(message, dict) else None


def record_timestamp(rec: dict[str, Any], message: dict[str, Any]) -> str:
    """ISO-8601 UTC for one record, from whichever field carries it.

    A message's ``timestamp`` is epoch-milliseconds when it is an int (real
    STORED sessions carry those as well, not only run streams), an ISO string
    when it is a non-empty string, and the wrapper record's ISO string when
    the message carries none. pi writes none of the three on a few records,
    for which the normalized empty timestamp is the honest representation
    rather than an invented one.
    """
    value = message.get("timestamp")
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
    if isinstance(value, str) and value:
        return value
    wrapper_value = rec.get("timestamp")
    return wrapper_value if isinstance(wrapper_value, str) else ""


#: pi's tool names are lowercase (``bash``/``read``/``edit``/``write``/``grep``
#: /``find``/``ls``), while the shared toolresult vocabulary is Claude Code's
#: capitalized set. Mapping them here keeps the compression stage
#: (``--edit-calls``/``--read-calls``) and the ledger's own typology working
#: across both harnesses instead of silently classifying every pi call as
#: "other".
_TOOL_NAME_ALIASES: dict[str, str] = {
    "edit": "Edit", "write": "Write", "read": "Read", "grep": "Grep",
    "find": "Glob", "ls": "LS",
}


def shared_tool_name(tool_name: str) -> str:
    """pi's tool name as the shared capitalized vocabulary spells it."""
    return _TOOL_NAME_ALIASES.get(tool_name, tool_name)


def blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def block_kind(block: dict[str, Any]) -> str:
    value = block.get("type")
    return value if isinstance(value, str) and value else "missing"


@dataclass
class PiSkipReport:
    """Everything this parse did NOT render, counted.

    ``records``/``roles``/``blocks`` hold KNOWN-deliberate skips (pi's own
    declarations, see the module docstring); ``unknown_*`` hold anything else,
    which must never disappear quietly. ``lines()`` renders it as the footer
    text ``extract`` appends, so a reader of a readback is told what the
    adapter left out instead of discovering it by accident.
    """

    records: dict[str, int] = field(default_factory=dict)
    unknown_records: dict[str, int] = field(default_factory=dict)
    roles: dict[str, int] = field(default_factory=dict)
    unknown_roles: dict[str, int] = field(default_factory=dict)
    blocks: dict[str, int] = field(default_factory=dict)
    unknown_blocks: dict[str, int] = field(default_factory=dict)
    unparseable_lines: int = 0
    tree_breaks: int = 0
    context_edits: int = 0
    context_edits_masked: int = 0

    def bump(self, bucket: dict[str, int], key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1

    def skipped_records(self) -> int:
        return sum(self.records.values()) + sum(self.unknown_records.values())

    def unknown_total(self) -> int:
        return (
            sum(self.unknown_records.values()) + sum(self.unknown_roles.values())
            + sum(self.unknown_blocks.values())
        )

    def lines(self) -> list[str]:
        counts = _counts
        out: list[str] = []
        if self.records or self.unknown_records:
            out.append(
                f"[pi skipped {self.skipped_records()} record(s): deliberate "
                f"{counts(self.records)}; unknown {counts(self.unknown_records)}]"
            )
        if self.roles or self.unknown_roles:
            out.append(
                f"[pi skipped message(s) by role: deliberate {counts(self.roles)}; "
                f"unknown {counts(self.unknown_roles)}]"
            )
        if self.blocks or self.unknown_blocks:
            out.append(
                f"[pi skipped content block(s): deliberate {counts(self.blocks)}; "
                f"unknown {counts(self.unknown_blocks)}]"
            )
        if self.unparseable_lines:
            out.append(
                f"[pi skipped {self.unparseable_lines} unparseable or non-object "
                f"line(s), e.g. a run killed mid-write]"
            )
        if self.tree_breaks:
            out.append(
                f"[pi: {self.tree_breaks} record(s) whose parentId breaks the linear "
                f"chain -- this extract follows FILE ORDER, not the parent tree]"
            )
        if self.context_edits:
            out.append(
                f"[pi: {self.context_edits} context_edit record(s), "
                f"{self.context_edits_masked} of which REMOVED their target from "
                f"model context -- this extract still shows the original content]"
            )
        return out


def _counts(values: dict[str, int]) -> str:
    return " ".join(f"{key}={values[key]}" for key in sorted(values)) or "none"


def _count_blocks(blocks_: list[dict[str, Any]], report: PiSkipReport) -> None:
    for block in blocks_:
        kind = block_kind(block)
        if kind in DELIBERATE_SKIP_BLOCKS:
            report.bump(report.blocks, kind)
        elif kind not in ("text", "thinking", "toolCall"):
            report.bump(report.unknown_blocks, kind)


def block_text(blocks_: list[dict[str, Any]], kind: str, key: str) -> str:
    return "\n".join(
        block[key] for block in blocks_
        if block.get("type") == kind and isinstance(block.get(key), str) and block[key]
    )


def sniff(path: Path) -> bool:
    """Recognize pi's ``session`` header by its own content.

    Both shapes start with ``{"type": "session", "version": <int>, "id":
    <str>, "cwd": <str>}``. No other supported harness writes a record of
    that shape (Codex's nearest is ``session_meta`` with a ``payload``), so
    the content check -- not a file suffix -- is the discriminator.
    """
    try:
        with Path(path).open("r", errors="ignore") as handle:
            for index, line in enumerate(handle):
                if index >= _SNIFF_SCAN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or record_type(rec) != "session":
                    continue
                version = rec.get("version")
                if isinstance(version, bool) or not isinstance(version, int):
                    continue
                if isinstance(rec.get("id"), str) and isinstance(rec.get("cwd"), str):
                    return True
    except OSError:
        return False
    return False


def list_sessions(path: Path) -> list[str]:
    """pi stores one session per file, so the path is the whole session."""
    return [str(path)]


def compaction_summary(rec: dict[str, Any]) -> str:
    """The summary a compaction record carries, or "".

    A stored session's ``compaction`` record has ``summary``; a run stream's
    ``compaction_end`` carries it under ``result``.
    """
    summary = rec.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    result = rec.get("result")
    if isinstance(result, dict):
        nested = result.get("summary")
        if isinstance(nested, str):
            return nested
    return ""


def compaction_events(
    rec: dict[str, Any], seq: int, marker: str, ts: str, config: ExtractConfig,
) -> list[NormalizedEvent]:
    """One compaction boundary as a kept LIFECYCLE_MARKER.

    ``compaction_start`` opens the boundary ``compaction_end`` closes and
    carries no summary, so the marker is emitted once, from the record that
    can describe it: one marker per compaction in either shape.
    """
    if record_type(rec) == "compaction_start":
        return []
    summary = compaction_summary(rec)
    if config.hide_compaction_content or not summary:
        text = COMPACTION_LABEL
    else:
        text = f"{COMPACTION_SUMMARY_LABEL} {summary}"
    event = NormalizedEvent(seq, marker, ts, EventKind.LIFECYCLE_MARKER, text)
    event.meta["boundary_type"] = "compaction"
    return [event]


def summary_marker(
    label: str, summary_label: str, summary: Any, boundary_type: str,
    seq: int, marker: str, ts: str, config: ExtractConfig,
) -> NormalizedEvent:
    """A branch summary as a LIFECYCLE_MARKER.

    pi's ``buildSessionContext`` turns a ``branch_summary`` record and its
    ``branchSummary`` message into a ``user`` message (``dist/core/messages.js``),
    so this is content the MODEL SAW and cannot be dropped quietly. It marks a
    walk boundary exactly like a compaction does, and its summary text follows
    the same ``--show-compaction-content`` rule (that flag is the one the
    operator already has for "show me the summary text").
    """
    text = summary_label + " " + summary if isinstance(summary, str) and summary \
        and not config.hide_compaction_content else label
    event = NormalizedEvent(seq, marker, ts, EventKind.LIFECYCLE_MARKER, text)
    event.meta["boundary_type"] = boundary_type
    return event


def custom_message_event(
    text: str, source: str, seq: int, marker: str, ts: str,
) -> NormalizedEvent:
    """An extension-injected message as OPERATOR_TEXT, labelled as NOT the
    operator's own words.

    pi converts a ``custom_message`` record / ``custom`` message role into a
    ``user`` message (``dist/core/session-manager.d.ts``'s own docstring for
    ``CustomMessageEntry``: "this DOES participate in LLM context"), so it is
    context the model read. It is not operator intent, hence the label and the
    ``pi_context_source`` tag rather than a silent OPERATOR prefix.
    """
    event = NormalizedEvent(
        seq, marker, ts, EventKind.OPERATOR_TEXT, f"[custom message: {source}] {text}",
    )
    event.meta["pi_context_source"] = "extension"
    event.meta["pi_custom_type"] = source
    return event


def bash_execution_event(
    message: dict[str, Any], seq: int, marker: str, ts: str, config: ExtractConfig,
) -> NormalizedEvent:
    """A ``!`` command pi ran inside the session, as a tool-like line.

    ``BashExecutionMessage`` (``dist/core/messages.d.ts``) is persisted as an
    ordinary ``message`` record and converted to a ``user`` message for the
    model (``bashExecutionToText``), unless ``excludeFromContext`` (the ``!!``
    prefix) removed it. This extract renders neither a raw result nor prose
    here: it states the command, its exit code and whether the model saw it.
    """
    command = str(message.get("command") or "")
    call = toolresult.summarize_call(
        "Bash", {"command": command}, strip_cd_prefix=config.strip_cd_prefix,
        aliases=config.path_aliases,
    )
    exit_code = message.get("exitCode")
    status = "excluded from model context" if message.get("excludeFromContext") is True \
        else f"exit={exit_code}" if isinstance(exit_code, int) else "exit=unknown"
    cancelled = " cancelled" if message.get("cancelled") is True else ""
    event = NormalizedEvent(
        seq, marker, ts, EventKind.TOOL_CALL,
        f"[bash execution: {call}] {status}{cancelled}",
    )
    event.meta["tool"] = "bash"
    event.meta["tool_kind"] = toolresult.tool_kind(
        "Bash", {"command": command}, config.effect_patterns,
        config.effect_scp_uploads,
    )
    event.meta["pi_bash_execution"] = "1"
    return event


def context_edit_event(
    rec: dict[str, Any], seq: int, marker: str, ts: str,
) -> NormalizedEvent:
    """A ``context_edit`` as a LIFECYCLE_MARKER naming what it did.

    ``replacement: null`` REMOVES the target entry from the model's context
    (`session-manager.d.ts`'s ``projectContextEntry``); a replacement swaps
    only its content. This extract reads the FILE, so it still shows the
    original content -- the marker and the skip report say so instead of
    letting a reader believe the primer and the model saw the same thing.
    """
    target = rec.get("targetId")
    target_text = target if isinstance(target, str) and target else "unknown"
    if rec.get("replacement") is None and "replacement" in rec:
        text = f"{CONTEXT_EDIT_LABEL} target={target_text} removed from model context"
    else:
        text = f"{CONTEXT_EDIT_LABEL} target={target_text} content replaced"
    event = NormalizedEvent(seq, marker, ts, EventKind.LIFECYCLE_MARKER, text)
    event.meta["boundary_type"] = "context_edit"
    return event


def tool_label(tool_name: str, tool_input: Any, config: ExtractConfig) -> str | None:
    """One ``toolCall`` block -> its rendered text under ``--tool-calls``,
    or None when the mode renders nothing for it. Never includes the result;
    the mode vocabulary is shared with Claude Code because the option is."""
    mode = config.tool_call_mode
    if mode == "none":
        return None
    head = f"[tool call: {tool_name}]"
    intent = toolresult.tool_intent(tool_input, config.path_aliases)
    if mode == "label":
        return head
    if mode == "label-intent":
        return head + (f" {intent}" if intent else "")
    call = toolresult.summarize_call(
        tool_name, tool_input, strip_cd_prefix=config.strip_cd_prefix,
        aliases=config.path_aliases,
    )
    if mode == "intent":
        return f"{head} {intent}" if intent else None
    text = intent or call if mode == "intent-or-call" else call
    return f"{head} {text}" if text else head


def tool_call_event(
    seq: int, marker: str, ts: str, block: dict[str, Any], config: ExtractConfig,
) -> NormalizedEvent | None:
    tool_name = block.get("name")
    if not isinstance(tool_name, str) or not tool_name:
        return None
    arguments = block.get("arguments")
    label = tool_label(tool_name, arguments, config)
    if label is None:
        return None
    event = NormalizedEvent(seq, marker, ts, EventKind.TOOL_CALL, label)
    event.meta["tool"] = tool_name
    event.meta["tool_intent"] = toolresult.tool_intent(arguments, config.path_aliases)
    event.meta["tool_kind"] = toolresult.tool_kind(
        shared_tool_name(tool_name), arguments, config.effect_patterns,
        config.effect_scp_uploads,
    )
    if isinstance(arguments, dict):
        file_path = arguments.get("path") or arguments.get("file_path")
        if isinstance(file_path, str) and file_path:
            event.meta["tool_file"] = toolresult.apply_aliases(
                file_path, config.path_aliases,
            )
    return event


def tool_result_event(
    seq: int, marker: str, ts: str, message: dict[str, Any], config: ExtractConfig,
    calls: dict[str, str],
) -> NormalizedEvent | None:
    """A FAILED tool result, truncated, independent of the tool-call mode.

    The failed call comes first: an error line must say which call failed,
    and that call is the one the assistant message already carried.
    """
    if config.tool_errors != "show" or message.get("isError") is not True:
        return None
    tool_name = message.get("toolName")
    tool_name = tool_name if isinstance(tool_name, str) and tool_name else "tool"
    call_id = message.get("toolCallId")
    call = calls.get(call_id, "") if isinstance(call_id, str) else ""
    text = toolresult.result_text({"content": message.get("content")})
    error = toolresult.one_line(toolresult.apply_aliases(text, config.path_aliases), 240)
    label = f"[tool error: {tool_name}] {call} => {error}" if call else f"[tool error: {tool_name}] {error}"
    event = NormalizedEvent(seq, marker, ts, EventKind.TOOL_CALL, label)
    event.meta["tool_error"] = "true"
    return event


def launch_events(
    blocks_: list[dict[str, Any]], cwd: Any, config: ExtractConfig,
    seq: int, marker: str, ts: str,
) -> list[NormalizedEvent]:
    """AGENT-OUT/AGENT-IN for a Bash call that invokes a known launcher."""
    if config.agent_comms == "off":
        return []
    cwd_raw = cwd if isinstance(cwd, str) else None
    out: list[NormalizedEvent] = []
    for block in blocks_:
        if block.get("type") != "toolCall" or block.get("name") != "bash":
            continue
        arguments = block.get("arguments")
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if isinstance(command, str):
            out.extend(
                agentcomms.launch_events(command, cwd_raw, config, seq, marker, ts),
            )
    return out


def custom_message_text(content: Any) -> str:
    """The text of a ``custom_message`` record's ``content`` (a string, or a
    list of text/image blocks; images cannot be carried by a text extract)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        return "\n".join(part for part in parts if part)
    return ""


def message_events(
    rec: dict[str, Any], message: dict[str, Any], marker: str, config: ExtractConfig,
    calls: dict[str, str], seq: int, report: PiSkipReport,
) -> list[NormalizedEvent]:
    """The events one conversation message yields, in content order.

    `report` is REQUIRED, not optional-with-a-default: this adapter's whole
    contract is that a skipped record is counted somewhere a reader can see,
    and a caller that passes no report would be the one caller able to
    silently drop things. `parse_with_stats` is the only caller, and it always
    has a report.
    """
    role = message.get("role")
    ts = record_timestamp(rec, message)
    blocks_ = blocks(message)
    _count_blocks(blocks_, report)
    if role == "user":
        text = block_text(blocks_, "text", "text")
        if not text:
            return []
        return [NormalizedEvent(seq, marker, ts, EventKind.OPERATOR_TEXT, text)]
    if role == "toolResult":
        event = tool_result_event(seq, marker, ts, message, config, calls)
        if event is None:
            # A successful result is noise this tool exists to discard; count
            # it so the skip is stated rather than assumed.
            report.bump(report.roles, "toolResult (result not rendered)")
        return [event] if event is not None else []
    if role == "bashExecution":
        return [bash_execution_event(message, seq, marker, ts, config)]
    if role == "custom":
        source = message.get("customType")
        text = custom_message_text(message.get("content"))
        if not text:
            return []
        return [custom_message_event(
            text, source if isinstance(source, str) and source else "unknown",
            seq, marker, ts,
        )]
    if role == "branchSummary":
        summary = message.get("summary")
        return [summary_marker(
            BRANCH_LABEL, BRANCH_SUMMARY_LABEL, summary, "branch_summary",
            seq, marker, ts, config,
        )]
    if role == "compactionSummary":
        summary = message.get("summary")
        return [summary_marker(
            COMPACTION_LABEL, COMPACTION_SUMMARY_LABEL, summary, "compaction_summary",
            seq, marker, ts, config,
        )]
    if role != "assistant":
        # An unrecognized role is UNKNOWN: visible, never treated as prose and
        # never silently dropped.
        name = role if isinstance(role, str) and role else "missing"
        bucket = report.roles if name in DELIBERATE_SKIP_ROLES else report.unknown_roles
        report.bump(bucket, name)
        return []
    events: list[NormalizedEvent] = []
    for block in blocks_:
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                events.append(
                    NormalizedEvent(seq, marker, ts, EventKind.ASSISTANT_TEXT, text)
                )
        elif kind == "thinking":
            thinking = block.get("thinking")
            if config.include_thinking and isinstance(thinking, str) and thinking:
                events.append(
                    NormalizedEvent(seq, marker, ts, EventKind.THINKING, thinking)
                )
        elif kind == "toolCall":
            call_id = block.get("id")
            if isinstance(call_id, str) and call_id:
                calls[call_id] = toolresult.summarize_call(
                    str(block.get("name") or ""), block.get("arguments"),
                    strip_cd_prefix=config.strip_cd_prefix, aliases=config.path_aliases,
                )
            event = tool_call_event(seq, marker, ts, block, config)
            if event is not None:
                events.append(event)
    events.extend(launch_events(blocks_, rec.get("cwd"), config, seq, marker, ts))
    if message.get("stopReason") == "error":
        # The assistant channel's own error record, so the shared selector's
        # --show-api-errors rule hides a failed model call by default here
        # exactly as it does for Claude Code.
        detail = message.get("errorMessage") or message.get("error") or ""
        events.append(NormalizedEvent(
            seq, marker, ts, EventKind.ASSISTANT_TEXT,
            f"[API ERROR: pi stopReason=error] {toolresult.one_line(str(detail), 240)}",
        ))
    return events


def records(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """(absolute line index, record) for every parseable record.

    A malformed or truncated line (a run killed mid-write) is skipped rather
    than fatal: the intact records around it are still the session's content,
    and the file is appended to, never rewritten. ``records_and_skips`` counts
    those skipped lines, so a damaged file is disclosed, not silently shorter.
    """
    return records_and_skips(path)[0]


def records_and_skips(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    """The parseable records plus the count of lines that were not."""
    out: list[tuple[int, dict[str, Any]]] = []
    skipped = 0
    with Path(path).open("r", errors="replace") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(rec, dict):
                out.append((index, rec))
            else:
                skipped += 1
    return out, skipped


def _resolve_bound(records_: list[tuple[int, dict[str, Any]]], marker: str | None, flag: str,
                   path: Path) -> int | None:
    if marker is None:
        return None
    found = next((index for index, _rec in records_ if f"line{index}" == marker), None)
    if found is None:
        raise ValueError(f"{flag} marker {marker!r} not found as a pi line marker in {path}")
    return found


def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
    """The SessionAdapter contract: the events only (see ``parse_with_stats``)."""
    return parse_with_stats(path, session_id, config)[0]


def parse_with_stats(
    path: Path, session_id: str, config: ExtractConfig,
) -> tuple[list[NormalizedEvent], PiSkipReport]:
    """The events, plus a count of everything this parse did NOT render.

    Every record and message this adapter does not turn into an event lands in
    the report, KNOWN-deliberate skips apart from UNKNOWN ones, so no shape
    pi defines can disappear without the document saying so.
    """
    records_, unparseable = records_and_skips(path)
    since_line = _resolve_bound(records_, config.since_marker, "--since", path)
    until_line = _resolve_bound(records_, config.until_marker, "--until", path)

    calls: dict[str, str] = {}
    events: list[NormalizedEvent] = []
    report = PiSkipReport(unparseable_lines=unparseable)
    previous_id: Any = None
    for index, rec in records_:
        # The tree is counted BEFORE the window slice: how the file is shaped
        # is a fact about the file, not about which records this run shows.
        if record_type(rec) in MESSAGE_RECORD_TYPES:
            if previous_id is not None and rec.get("parentId") != previous_id:
                report.tree_breaks += 1
            previous_id = rec.get("id")
        if since_line is not None and index <= since_line:
            continue
        if until_line is not None and index > until_line:
            continue
        # `seq` is the record's ABSOLUTE, pre-slice line position and `marker`
        # spells that same position as a resolvable `line<N>` token, so a
        # marker minted by one --since hop resolves to the same physical
        # record on the next one.
        marker = f"line{index}"
        rtype = record_type(rec)
        message = message_of(rec)
        if message is not None:
            events.extend(
                message_events(rec, message, marker, config, calls, index, report)
            )
        elif rtype in COMPACTION_RECORD_TYPES:
            events.extend(
                compaction_events(rec, index, marker, record_timestamp(rec, {}), config)
            )
        elif rtype == "branch_summary":
            events.append(summary_marker(
                BRANCH_LABEL, BRANCH_SUMMARY_LABEL, rec.get("summary"),
                "branch_summary", index, marker, record_timestamp(rec, {}), config,
            ))
        elif rtype == "custom_message":
            text = custom_message_text(rec.get("content"))
            source = rec.get("customType")
            if text:
                events.append(custom_message_event(
                    text, source if isinstance(source, str) and source else "unknown",
                    index, marker, record_timestamp(rec, {}),
                ))
            else:
                # No renderable text (an image-only extension message):
                # deliberate, because a text extract cannot carry it, but
                # counted like every other skip.
                report.bump(report.blocks, "custom_message (no text)")
        elif rtype == "context_edit":
            report.context_edits += 1
            if rec.get("replacement") is None and "replacement" in rec:
                report.context_edits_masked += 1
            events.append(context_edit_event(
                rec, index, marker, record_timestamp(rec, {}),
            ))
        else:
            bucket = report.records if rtype in DELIBERATE_SKIP_RECORDS \
                else report.unknown_records
            report.bump(bucket, rtype or "missing type")
    return agentcomms.dedupe_events(events), report
