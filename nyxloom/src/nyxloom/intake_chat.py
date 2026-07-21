"""Feature-intake conversational agent (backend). PACKAGE P29.

The factory's front door: a conversational agent that turns a ROUGH feature
request into a carve-ready structured brief. A deliberate SIBLING of
decision_chat.py (P18) -- same skeleton (resumable read-only redacted claude
session, persisted chat state, confirm-token finalize), a different goal
(interview -> brief) and output (a structured backlog item via P28, plus any
spawned D-NNN decisions).

MIRROR, NOT FORK: decision_chat.py is a forbidden file for this package (see
handoff/P29-intake-agent-backend.md scope.forbid). Everything below that
looks like decision_chat.py -- the persistence shape, the read-only tool
posture, the launch-first/resume-Nth turn engine -- is independently
re-authored here (same literal values, e.g. READONLY_ARGV_SUFFIX) rather
than imported from that module, so this package never depends on, or
modifies, decision_chat's internals.

READ-ONLY + REDACTED POSTURE (unconditional, same as decision_chat.py): the
intake agent gets Read/Grep/Glob only -- no Edit/Write/Bash -- and every
reply is passed through cfg.redact() before it is stored or returned. This
is a security invariant, not a default: an intake turn must never be able to
mutate the repo or leak an unredacted secret, no matter what routes.toml
says.

INTERVIEW SHAPE (the first-turn system prompt encodes 7 steps): (1) confirm
understanding of the request, (2) elicit missing detail, (3) surface
consequences/risks, (4) file a product call via a `PRODUCT_CALL: <question>
| <resume prompt>` line for anything only the operator can decide (never
guess), (5) estimate blockers/priority against the backlog/roadmap's
depends_on graph, (6) ask the operator for a priority, (7) once satisfied,
finalize with a `BRIEF:` block (format below) and nothing after.

PRODUCT_CALL PROTOCOL: any reply (not just the final one) may carry one or
more `PRODUCT_CALL: <question> | <resume prompt>` lines. Each one opens a
brand-new D-NNN via decisions.open_decision() (file-write only -- the next
daemon reconcile tick emits DECISION_OPENED itself, same as any other
decisions.md edit) and the id is remembered on the chat record
(IntakeChat.opened_decisions) so the eventual brief can link it even if the
model's own BRIEF: block forgets to restate it.

BRIEF PROTOCOL (the finalize token, parsed like decision_chat's
`DECISION:` line):
    BRIEF: <one-line title>
    Priority: <int>                (optional)
    Decisions: D-001,D-002         (optional; falls back to
                                     opened_decisions accumulated so far)
    Detail:
    <free prose -- purpose, elicited detail, consequences>
A reply carrying a `BRIEF:` line persists a STRUCTURED backlog item via
backlog_items.create() (status=open) and links any spawned decisions,
exactly once per chat (IntakeChat.brief_id guards re-finalization on a
stale/duplicate BRIEF: line racing a later turn).

PERSISTENCE: one IntakeChat record per (project, intake_id) JSON file under
paths.project_dir(project)/"intake_chats"/<intake_id>.json -- session_id +
the redacted transcript + opened_decisions + brief_id. Turn logs (raw CLI
stdout/stderr, pre-redaction) live alongside under
.../<intake_id>/turn-N.log, same layout as decision_chat's decision_chats/.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import adapters, backlog_items, decisions, paths
from .config import ProjectConfig, RouteDef, Routes
from .log import get_logger
from .types import utc_now

log = get_logger("intake_chat")

# --- tunables (module constants; independently owned, see module docstring)

INTAKE_AGENT_TIER = "frontier-review"

READONLY_ARGV_SUFFIX = ["--allowedTools", "Read Grep Glob",
                         "--disallowedTools", "Edit Write Bash"]

TURN_TIMEOUT_SECONDS = 120
MAX_REPLY_CHARS = 1200

# Context sources named in the first-turn system prompt (SPEC-convention
# relative paths under cfg.root; the intake session Reads these itself with
# its read-only tools -- they are never parsed/injected as raw prose here).
ROADMAP_RELPATH = "nyxloom-trove/roadmap.md"
TROVE_CONFIG_RELPATH = "nyxloom-trove/nyxloom.toml"
HANDOFFS_RELDIR = "nyxloom-trove/handoffs"

_PRODUCT_CALL_RE = re.compile(r"^\s*PRODUCT_CALL:\s*(.+?)\s*\|\s*(.+?)\s*$")
_BRIEF_LINE_RE = re.compile(r"^\s*BRIEF:\s*(.+)$")
_BRIEF_PRIORITY_RE = re.compile(r"^Priority:\s*(\d+)\s*$", re.IGNORECASE)
_BRIEF_DECISIONS_RE = re.compile(r"^Decisions:\s*(.+)$", re.IGNORECASE)
_BRIEF_DETAIL_RE = re.compile(r"^Detail:\s*(.*)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# persistence: one IntakeChat record per (project, intake_id)

@dataclass
class IntakeChatMessage:
    role: str          # "user" | "agent"
    text: str
    ts: str

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "text": self.text, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IntakeChatMessage":
        return cls(role=d["role"], text=d["text"], ts=d["ts"])


@dataclass
class IntakeChat:
    intake_id: str
    project: str
    session_id: str | None = None
    route_id: str = ""
    transcript: list[IntakeChatMessage] = field(default_factory=list)
    opened_decisions: list[str] = field(default_factory=list)
    brief_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intake_id": self.intake_id,
            "project": self.project,
            "session_id": self.session_id,
            "route_id": self.route_id,
            "transcript": [m.to_dict() for m in self.transcript],
            "opened_decisions": list(self.opened_decisions),
            "brief_id": self.brief_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IntakeChat":
        return cls(
            intake_id=d["intake_id"],
            project=d["project"],
            session_id=d.get("session_id"),
            route_id=d.get("route_id", ""),
            transcript=[IntakeChatMessage.from_dict(m) for m in d.get("transcript", [])],
            opened_decisions=list(d.get("opened_decisions", [])),
            brief_id=d.get("brief_id"),
        )


def _chat_dir(project: str) -> Path:
    return paths.project_dir(project) / "intake_chats"


def _chat_path(project: str, intake_id: str) -> Path:
    return _chat_dir(project) / f"{intake_id}.json"


def load_chat(project: str, intake_id: str) -> IntakeChat | None:
    p = _chat_path(project, intake_id)
    if not p.exists():
        return None
    try:
        return IntakeChat.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None


def save_chat(chat: IntakeChat) -> None:
    p = _chat_path(chat.project, chat.intake_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(chat.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def _pick_route(routes_obj: Routes) -> RouteDef | None:
    candidates = routes_obj.for_tier(INTAKE_AGENT_TIER)
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# prompt construction (names the context sources; never raw file prose)

def _first_turn_system_prompt(cfg: ProjectConfig, project: str, intake_id: str,
                              user_text: str) -> str:
    backlog_path = backlog_items.resolve_path(cfg)
    parts = [
        f"You are conducting a feature-intake interview ({intake_id}) with "
        f"the operator for project '{project}' over a chat bridge (nyxloom "
        "intake-chat, P29). Your job: turn a rough feature request into a "
        "carve-ready structured brief.",
        # The request itself only ever arrives as the first turn's user_text:
        # unlike decision_chat (whose subject is a D-entry the agent can read
        # back from the inbox), intake has no on-disk source for it, and
        # build_dispatch's frozen contract has no free-prose prompt parameter.
        # It therefore has to ride in on the system prompt, or step (1) below
        # asks the agent to confirm a request it was never shown.
        f"The operator's request, verbatim:\n{user_text}",
        "Before asking anything, Read the project context you will need: "
        f"the [refs] docs declared in {cfg.root / TROVE_CONFIG_RELPATH}, "
        f"the roadmap ({cfg.root / ROADMAP_RELPATH}), the backlog "
        f"({backlog_path}), and recent handoffs under "
        f"{cfg.root / HANDOFFS_RELDIR}/.",
        "Interview in 7 steps: (1) confirm your understanding of the "
        "request back to the operator, (2) elicit missing detail, (3) "
        "surface consequences/risks, (4) if a genuine product call surfaces "
        "-- a choice only the operator can make -- do not guess: emit a "
        "line `PRODUCT_CALL: <question> | <resume prompt>` and the bridge "
        "will file a D-NNN decision for you, (5) estimate blockers/priority "
        "by relating this request to the depends_on graph in the backlog "
        "and roadmap, (6) ask the operator for a priority, (7) once "
        "satisfied, finalize.",
        "To finalize, end your reply with:\n"
        "BRIEF: <one-line title>\n"
        "Priority: <int>\n"
        "Decisions: D-NNN[,D-NNN...]   (only if you filed any PRODUCT_CALLs)\n"
        "Detail:\n"
        "<distilled purpose, elicited detail, and consequences -- this "
        "becomes the pre-carve item body>\n"
        "and nothing after.",
    ]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# reply extraction (same shape as decision_chat._extract_reply_text)

def _extract_reply_text(log_text: str) -> str:
    lines = log_text.splitlines()
    if not lines:
        return ""

    body_lines = lines
    first = lines[0].strip()
    if first:
        try:
            head = json.loads(first)
        except json.JSONDecodeError:
            head = None
        if isinstance(head, dict) and "session_id" in head:
            body_lines = lines[1:]

    non_blank = [l for l in body_lines if l.strip()]
    if not non_blank:
        return ""

    last = non_blank[-1].strip()
    try:
        data = json.loads(last)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        for key in ("result", "text", "message"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val

    return "\n".join(body_lines).strip()


# ---------------------------------------------------------------------------
# PRODUCT_CALL / BRIEF parsing

def _parse_product_calls(reply_text: str) -> list[tuple[str, str]]:
    calls = []
    for line in reply_text.splitlines():
        m = _PRODUCT_CALL_RE.match(line)
        if m:
            calls.append((m.group(1).strip(), m.group(2).strip()))
    return calls


@dataclass
class ParsedBrief:
    title: str
    priority: int | None
    decisions: list[str]
    detail: str


def _parse_brief(reply_text: str) -> ParsedBrief | None:
    """`BRIEF: <title>` (any line) plus optional Priority:/Decisions: fields
    and a Detail: section -> ParsedBrief; None if no BRIEF: line present."""
    lines = reply_text.splitlines()
    brief_idx = None
    title = ""
    for i, line in enumerate(lines):
        m = _BRIEF_LINE_RE.match(line.strip())
        if m:
            brief_idx = i
            title = m.group(1).strip()
            break
    if brief_idx is None:
        return None

    priority: int | None = None
    decisions_ids: list[str] = []
    detail_lines: list[str] = []

    for raw_line in lines[brief_idx + 1:]:
        stripped = raw_line.strip()
        if not stripped:
            continue
        pm = _BRIEF_PRIORITY_RE.match(stripped)
        if pm:
            priority = int(pm.group(1))
            continue
        dm = _BRIEF_DECISIONS_RE.match(stripped)
        if dm:
            decisions_ids = [d.strip() for d in dm.group(1).split(",") if d.strip()]
            continue
        tm = _BRIEF_DETAIL_RE.match(stripped)
        if tm:
            rest = tm.group(1).strip()
            if rest:
                detail_lines.append(rest)
            continue
        detail_lines.append(stripped)

    return ParsedBrief(title=title, priority=priority, decisions=decisions_ids,
                        detail="\n".join(detail_lines).strip())


# ---------------------------------------------------------------------------
# turn execution (same shape as decision_chat._run_subprocess_turn)

def _run_subprocess_turn(argv: list[str], route: RouteDef, *, worktree: str,
                          log_path: Path, prior_session: str | None
                          ) -> tuple[str, str | None]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8") as f:
            subprocess.run(argv, stdout=f, stderr=subprocess.STDOUT, text=True,
                            cwd=worktree or None, timeout=TURN_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError) as exc:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[intake-chat turn failed: {exc!r}]\n")

    text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    session_id = adapters.capture_session(
        route, attempt_dir=log_path.parent, worktree=worktree,
        launched_at=utc_now(), log_path=log_path,
    ) or prior_session
    return _extract_reply_text(text), session_id


def _finalize_brief(cfg: ProjectConfig, chat: IntakeChat, parsed: ParsedBrief) -> str:
    """backlog_items.create() persistence + decision-link fallback (see
    module docstring's BRIEF PROTOCOL). Called at most once per chat --
    callers must check chat.brief_id is None first."""
    decisions_links = parsed.decisions or list(chat.opened_decisions)
    path = backlog_items.resolve_path(cfg)
    return backlog_items.create(
        path, parsed.title, parsed.detail,
        priority=parsed.priority, decisions=decisions_links,
    )


def advance_intake(cfg: ProjectConfig, project: str, intake_id: str, user_text: str) -> str:
    """Advance one intake-chat turn: launch (first) or resume (Nth). Any
    PRODUCT_CALL: lines in the reply open D-NNN decisions immediately; a
    BRIEF: line (once, per chat) persists the structured backlog item.
    Returns the reply text (assertable by callers/tests directly)."""
    chat = load_chat(project, intake_id) or IntakeChat(intake_id=intake_id, project=project)
    chat.transcript.append(IntakeChatMessage(role="user", text=user_text, ts=utc_now().isoformat()))
    log.debug("intake turn begin", project=project, intake_id=intake_id,
              turn=len(chat.transcript))

    routes_obj = Routes.load()
    route = _pick_route(routes_obj)
    if route is None:
        log.warning("intake turn: no route configured", tier=INTAKE_AGENT_TIER)
        reply = f"intake-chat: no '{INTAKE_AGENT_TIER}' route configured"
        chat.transcript.append(IntakeChatMessage(role="agent", text=reply, ts=utc_now().isoformat()))
        save_chat(chat)
        return reply

    turn_n = len(chat.transcript)
    log_path = _chat_dir(project) / intake_id / f"turn-{turn_n}.log"
    worktree = str(cfg.root)

    if chat.session_id is None:
        system_prompt = _first_turn_system_prompt(cfg, project, intake_id, user_text)
        argv, _prompt = adapters.build_dispatch(
            route, handoff_path=backlog_items.DEFAULT_RELPATH, worktree=worktree,
            branch=cfg.default_branch, task_id=f"intake-{intake_id}",
            gate_hint="intake-chat", receipt_path="",
        )
        argv = list(argv) + ["--append-system-prompt", system_prompt] + READONLY_ARGV_SUFFIX
    else:
        argv = adapters.build_resume(route, session=chat.session_id, worktree=worktree, prompt=user_text)
        argv = list(argv) + READONLY_ARGV_SUFFIX

    reply_raw, new_session = _run_subprocess_turn(
        argv, route, worktree=worktree, log_path=log_path, prior_session=chat.session_id)
    # Redact first (the security invariant), then PARSE THE FULL redacted
    # reply and cap only what is stored/echoed. Parsing the capped text
    # instead would silently drop a PRODUCT_CALL:/BRIEF: block that happens
    # to sit past MAX_REPLY_CHARS -- and the finalize block is by
    # construction at the END of a long recap turn, i.e. exactly where the
    # cap bites. decision_chat caps before parsing because its cap is an
    # ntfy message-length bound on text it POSTS; this module posts nothing,
    # so here the cap is purely a storage/echo bound and must not gate
    # persistence.
    reply_full = cfg.redact(reply_raw)
    reply = reply_full[:MAX_REPLY_CHARS]

    chat.session_id = new_session
    chat.route_id = route.route_id
    chat.transcript.append(IntakeChatMessage(role="agent", text=reply, ts=utc_now().isoformat()))

    for question, resume_prompt in _parse_product_calls(reply_full):
        chat.opened_decisions.append(decisions.open_decision(cfg, question, resume_prompt))

    if chat.brief_id is None:
        parsed_brief = _parse_brief(reply_full)
        if parsed_brief is not None:
            chat.brief_id = _finalize_brief(cfg, chat, parsed_brief)

    save_chat(chat)
    log.debug("intake turn advanced", project=project, intake_id=intake_id,
              route=route.route_id, brief_id=chat.brief_id)
    return reply
