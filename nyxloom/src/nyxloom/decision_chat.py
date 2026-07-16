"""Decision-chat bridge: ntfy/UI <-> a live decision agent. PACKAGE P18.

Lets an operator turn a DECISIONS-INBOX.md entry into a live back-and-forth:
reply on the feedback channel (or via the UI), a resumable read-only claude
session answers/challenges, and a `DECISION: <choice> - <note>` line in its
reply (or an explicit `decide <D-id> <choice>` message) finalizes the entry
via decisions.decide().

DEVIATIONS FROM THE P18 HANDOFF TEXT (flagged for reviewer sign-off; see
handoff/P18-decision-chat-bridge.md + nyxloom-trove/nyxloom.toml [notify]):

1. CHANNEL TOPOLOGY (the headline conflict). The handoff's "Concept"/
   "Channel + security" sections describe a SEPARATE, dedicated ntfy topic
   (config `decision_topic` / `decision_token_env`, its own rw identity)
   for the escalation<->answer loop. The CURRENT project design
   (nyxloom-trove/nyxloom.toml [notify]: `notifications_topic` = progress,
   `feedback_topic` = "decisions + escalation Q&A, bidirectional... unifies
   the old cmd topic + the decision-chat escalation loop") explicitly
   UNIFIES that escalation loop onto the SAME channel P12 already uses for
   operator chat-ops. In the actual (frozen) config.py dataclass this
   2-channel model is realized with the EXISTING field names -- there is
   no separate `decision_topic`/`decision_token_env` field, nor could one
   be added (config.py is in STANDING.md's frozen list for this wave):
     notifications (progress, write-only)  -> cfg.notify.ntfy_topic
     feedback (bidirectional, Q&A + ops)   -> cfg.notify.cmd_topic
                                              (read: cmd_token_env,
                                               write: token_env)
   This module therefore runs the WHOLE decision-chat loop over
   cfg.notify.cmd_topic (P12's existing topic/tokens), reusing P12's
   poll/reply transport by WRAPPING CommandListener.handle_message
   (wrap_command_handler, called from daemon.py) rather than importing a
   parallel listener or a new topic. A dedicated decision-chat ntfy
   *identity* (P18's "provision `decision-chat` ntfy user, rw on that
   topic only") is NOT provisioned for the same reason: there is only one
   feedback topic/identity now, shared with P12's cmd verbs. This is a
   real behavioral narrowing versus the handoff text and is the item this
   report flags most prominently for reviewer sign-off.
2. CONFIG KNOBS. The handoff also asks for `Policy.decision_agent_route`/
   `decision_agent_effort` (config.py) with a 'frontier-review' default.
   Since config.py is frozen for this wave, this module hardcodes
   DECISION_AGENT_TIER = "frontier-review" (the SAME tier daemon.py's
   LaunchReview already uses) as a module constant instead of a Policy
   field. No config.py edit was made.
3. DISPATCH PROMPT SHAPE. adapters.build_dispatch's frozen contract
   derives its prompt from handoff/worktree/branch/gate/receipt fields --
   it has no parameter for arbitrary prompt text, so it cannot literally
   carry the decision's question/resume_prompt as its `-p` prompt. This
   module still calls build_dispatch (satisfying "reuse the adapters
   seam") for argv scaffolding on the FIRST turn, then layers the actual
   decision-priming text on via an appended `--append-system-prompt`
   argument (this IS a real claude CLI flag and is literally named in the
   P18 handoff's own Behavior section) built ONLY from the Decision
   dataclass's typed `question`/`resume_prompt` fields. Resume turns
   (2nd+) use adapters.build_resume directly with the user's new message
   as `prompt` -- a perfect fit for that function's existing contract.

INJECTION BOUNDARY (SPEC section 13) -- READ THIS BEFORE EDITING:
notify.notification_for's rule (typed fields only, never model/log prose)
is UNCHANGED and still enforced everywhere else. `_post_feedback` in this
module is the ONE sanctioned exception the P18 handoff carves out: it
posts the decision agent's own free-text reply because the operator
explicitly opted into a live conversation on the feedback channel. Even
there: (a) the reply is passed through cfg.redact() first; (b) the agent
is dispatched with a read-only tool allowlist (no Edit/Write/Bash -- see
READONLY_ARGV_SUFFIX); (c) length is capped (MAX_REPLY_CHARS). Every OTHER
push in this module (notify_decision_opened) uses fixed templates over
typed fields only, exactly like notify.py.

PERSISTENCE: one DecisionChat record per (project, decision_id) JSON file
under paths.project_dir(project)/"decision_chats"/<decision_id>.json --
session_id + the redacted transcript. Turn logs (raw CLI stdout/stderr,
pre-redaction) live alongside under .../<decision_id>/turn-N.log.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import adapters, commands, config, decisions, notify, paths, storage
from .config import NotifyConfig, ProjectConfig, RouteDef, Routes
from .decisions import Decision
from .types import Actor, ActorKind, EventType, utc_now

# --- tunables (module constants so tests can override behavior) -----------

# P18 deviation #2 (see module docstring): config.py is frozen this wave, so
# the decision-agent's route tier is a constant here, not a Policy field.
# Reuses the SAME tier daemon.py's LaunchReview action already dispatches
# to, rather than inventing a second frontier-model tier.
DECISION_AGENT_TIER = "frontier-review"

# Read-only tool policy (P18 SECURITY: "read-only repo tools... NO Edit/
# Write/Bash-mutation"), appended unconditionally to the final argv of
# every decision-agent turn -- independent of whatever a route's own
# dispatch_extra happens to declare, so the posture never depends on
# routes.toml being configured correctly.
READONLY_ARGV_SUFFIX = ["--allowedTools", "Read Grep Glob",
                         "--disallowedTools", "Edit Write Bash"]

TURN_TIMEOUT_SECONDS = 120
MAX_REPLY_CHARS = 1200

# ntfy tag on THIS module's own posts -- the loop-guard: a message carrying
# this tag (or commands.REPLY_TAG, P12's own loop-guard tag) is never
# re-ingested as a fresh decision-chat turn.
DECISION_AGENT_TAG = "decision-agent"

_DECISION_PREFIX_RE = re.compile(r"^\s*(D-\d+)\s*:\s*(.*)$", re.DOTALL)
_DECIDE_CMD_RE = re.compile(r"^\s*decide\s+(D-\d+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_DECISION_LINE_RE = re.compile(r"^DECISION:\s*(.*)$")

# Sentinel: "not a decision-chat message" -- distinct from None, which means
# "handled here, no further reply needed" (see wrap_command_handler).
_NOT_HANDLED = object()


# ---------------------------------------------------------------------------
# persistence: one DecisionChat record per (project, decision_id)

@dataclass
class DecisionChatMessage:
    role: str          # "user" | "agent"
    text: str
    ts: str

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "text": self.text, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DecisionChatMessage":
        return cls(role=d["role"], text=d["text"], ts=d["ts"])


@dataclass
class DecisionChat:
    decision_id: str
    project: str
    session_id: str | None = None
    route_id: str = ""
    transcript: list[DecisionChatMessage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "project": self.project,
            "session_id": self.session_id,
            "route_id": self.route_id,
            "transcript": [m.to_dict() for m in self.transcript],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DecisionChat":
        return cls(
            decision_id=d["decision_id"],
            project=d["project"],
            session_id=d.get("session_id"),
            route_id=d.get("route_id", ""),
            transcript=[DecisionChatMessage.from_dict(m) for m in d.get("transcript", [])],
        )


def _chat_dir(project: str) -> Path:
    return paths.project_dir(project) / "decision_chats"


def _chat_path(project: str, decision_id: str) -> Path:
    return _chat_dir(project) / f"{decision_id}.json"


def load_chat(project: str, decision_id: str) -> DecisionChat | None:
    p = _chat_path(project, decision_id)
    if not p.exists():
        return None
    try:
        return DecisionChat.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None


def save_chat(chat: DecisionChat) -> None:
    p = _chat_path(chat.project, chat.decision_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(chat.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# project / decision lookup helpers

def find_project_for_decision(registry: dict[str, Path], decision_id: str
                               ) -> tuple[str, ProjectConfig] | None:
    """First registered project whose inbox currently has decision_id OPEN
    or DISCUSSING; None if no project has it (caller sends 404/falls
    through to unknown-command)."""
    for project in sorted(registry):
        try:
            cfg = ProjectConfig.load(registry[project])
        except Exception:
            continue
        try:
            if decision_id in decisions.open_ids(cfg):
                return project, cfg
        except Exception:
            continue
    return None


def _find_decision(cfg: ProjectConfig, decision_id: str) -> Decision | None:
    inbox_path = cfg.root / cfg.decisions_inbox
    if not inbox_path.exists():
        return None
    try:
        parsed = decisions.parse_inbox(inbox_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for d in parsed:
        if d.id == decision_id:
            return d
    return None


def _find_sole_active_chat(registry: dict[str, Path]
                            ) -> tuple[str, ProjectConfig, str] | None:
    """The one currently-open decision that already has a chat session in
    progress, if there is EXACTLY one across all registered projects --
    the target for a bare (un-prefixed) reply."""
    candidates: list[tuple[str, ProjectConfig, str]] = []
    for project in sorted(registry):
        try:
            cfg = ProjectConfig.load(registry[project])
        except Exception:
            continue
        try:
            open_ids = decisions.open_ids(cfg)
        except Exception:
            continue
        chat_dir = _chat_dir(project)
        if not chat_dir.exists():
            continue
        for f in sorted(chat_dir.glob("*.json")):
            decision_id = f.stem
            if decision_id not in open_ids:
                continue
            chat = load_chat(project, decision_id)
            if chat is not None and chat.session_id is not None:
                candidates.append((project, cfg, decision_id))
    return candidates[0] if len(candidates) == 1 else None


def _pick_route(routes_obj: Routes) -> RouteDef | None:
    candidates = routes_obj.for_tier(DECISION_AGENT_TIER)
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# prompt construction (typed Decision fields ONLY -- never raw inbox prose)

def _first_turn_system_prompt(decision: Decision | None, decision_id: str) -> str:
    parts = [
        f"You are discussing decision {decision_id} with the operator over "
        "a chat bridge (nyxloom decision-chat, P18).",
    ]
    if decision is not None and decision.question:
        parts.append(f"Question: {decision.question}")
    if decision is not None and decision.resume_prompt:
        parts.append(decision.resume_prompt)
    parts.append(
        "Answer concisely; you may Read/Grep the repo for facts. When the "
        "operator states a decision, end your reply with a line "
        "`DECISION: <choice> - <one-line rationale>` and nothing after."
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# reply extraction (stream-json aware, degrades to raw text for fake CLIs)

def _extract_reply_text(log_text: str) -> str:
    """Best-effort reply extraction from a turn's captured stdout/stderr.

    A claude stream-json log's first line is a session_id preamble (see
    adapters.capture_session); skip it if present. Of what remains: if the
    LAST non-blank line json-parses to a dict with a 'result'/'text'/
    'message' string field, return that; otherwise the remaining raw text
    IS the reply verbatim (the path a 'fake'/test CLI or a plain-text
    codex/opencode transcript takes).
    """
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


def _parse_decision_line(reply_text: str) -> tuple[str, str] | None:
    """`DECISION: <choice> - <note>` (any dash-ish separator) on any line
    of the reply -> (choice, note); None if no such line is present."""
    for raw_line in reply_text.splitlines():
        m = _DECISION_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        rest = m.group(1).strip()
        for sep in ("—", " - ", "--"):
            if sep in rest:
                choice, _, note = rest.partition(sep)
                return choice.strip(), note.strip()
        return rest, ""
    return None


# ---------------------------------------------------------------------------
# turn execution

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
            f.write(f"\n[decision-chat turn failed: {exc!r}]\n")

    text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    session_id = adapters.capture_session(
        route, attempt_dir=log_path.parent, worktree=worktree,
        launched_at=utc_now(), log_path=log_path,
    ) or prior_session
    return _extract_reply_text(text), session_id


def _finalize_decision(cfg: ProjectConfig, project: str, decision_id: str,
                        choice: str, note: str, actor_kind: ActorKind,
                        actor_id: str) -> bool:
    """decisions.decide() + DECISION_RESOLVED (mirrors cli.cmd_decide's own
    sequence). Returns False (no event, no raise) if the id is already
    resolved/missing -- a stale DECISION: line racing a human `decide`
    reply must not crash the turn."""
    try:
        decisions.decide(cfg, decision_id, choice, note, actor_id)
    except decisions.DecisionError:
        return False
    storage.append_event(
        project, actor=Actor(actor_kind, actor_id), type=EventType.DECISION_RESOLVED,
        decision_id=decision_id, payload={},
    )
    return True


def advance_chat(cfg: ProjectConfig, project: str, decision_id: str, user_text: str) -> str:
    """Advance one decision-chat turn: launch (first) or resume (Nth), post
    the (redacted, capped) reply to the feedback channel, finalize the
    decision if the reply carries a DECISION: line. Returns the reply text
    actually posted (assertable by callers/tests without re-parsing ntfy
    traffic)."""
    chat = load_chat(project, decision_id) or DecisionChat(decision_id=decision_id, project=project)
    chat.transcript.append(DecisionChatMessage(role="user", text=user_text, ts=utc_now().isoformat()))

    routes_obj = Routes.load()
    route = _pick_route(routes_obj)
    if route is None:
        reply = f"decision-chat: no '{DECISION_AGENT_TIER}' route configured"
        chat.transcript.append(DecisionChatMessage(role="agent", text=reply, ts=utc_now().isoformat()))
        save_chat(chat)
        _post_feedback(cfg, decision_id, reply)
        return reply

    turn_n = len(chat.transcript)
    log_path = _chat_dir(project) / decision_id / f"turn-{turn_n}.log"
    worktree = str(cfg.root)

    if chat.session_id is None:
        decision = _find_decision(cfg, decision_id)
        system_prompt = _first_turn_system_prompt(decision, decision_id)
        argv, _prompt = adapters.build_dispatch(
            route, handoff_path=cfg.decisions_inbox, worktree=worktree,
            branch=cfg.default_branch, task_id=f"decision-{decision_id}",
            gate_hint="decision-chat", receipt_path="",
        )
        argv = list(argv) + ["--append-system-prompt", system_prompt] + READONLY_ARGV_SUFFIX
    else:
        argv = adapters.build_resume(route, session=chat.session_id, worktree=worktree, prompt=user_text)
        argv = list(argv) + READONLY_ARGV_SUFFIX

    reply_raw, new_session = _run_subprocess_turn(
        argv, route, worktree=worktree, log_path=log_path, prior_session=chat.session_id)
    reply = cfg.redact(reply_raw)[:MAX_REPLY_CHARS]

    chat.session_id = new_session
    chat.route_id = route.route_id
    chat.transcript.append(DecisionChatMessage(role="agent", text=reply, ts=utc_now().isoformat()))
    save_chat(chat)

    decided = _parse_decision_line(reply)
    if decided is not None:
        choice, note = decided
        _finalize_decision(cfg, project, decision_id, choice, note,
                            ActorKind.FRONTIER_SESSION, "decision-agent")

    _post_feedback(cfg, decision_id, reply)
    return reply


# ---------------------------------------------------------------------------
# outbound pushes

def notify_decision_opened(cfg: ProjectConfig, decision_id: str) -> None:
    """P18 Behavior #1: push an actionable notice to the feedback channel
    IN ADDITION to the normal DECISION_OPENED push already sent to the
    notifications channel by notify.notify_event's push_classes handling.
    Typed fields (decision_id) + a fixed template ONLY -- not the sanctioned
    free-text exception (that is _post_feedback, below)."""
    if not (cfg.notify.ntfy_url and cfg.notify.cmd_topic):
        return
    nc = NotifyConfig(ntfy_url=cfg.notify.ntfy_url, ntfy_topic=cfg.notify.cmd_topic,
                       token_env=cfg.notify.token_env)
    note = {
        "title": f"Decision needed: {decision_id}",
        "body": (f"Decision {decision_id} opened. Reply here to discuss "
                 f"(e.g. '{decision_id}: <your message>')."),
        "click": cfg.notify.ntfy_url or "",
        "priority": 5,
        "tags": ["decision"],
    }
    try:
        notify.send(nc, note)
    except Exception:
        pass


def _post_feedback(cfg: ProjectConfig, decision_id: str, reply_text: str) -> None:
    """SANCTIONED injection-boundary exception (see module docstring):
    reply_text is model-authored free text, posted ONLY because the
    operator explicitly opted into this conversation. Already redacted +
    length-capped by the caller (advance_chat)."""
    if not (cfg.notify.ntfy_url and cfg.notify.cmd_topic):
        return
    nc = NotifyConfig(ntfy_url=cfg.notify.ntfy_url, ntfy_topic=cfg.notify.cmd_topic,
                       token_env=cfg.notify.token_env)
    note = {
        "title": f"Decision {decision_id}",
        "body": reply_text,
        "click": cfg.notify.ntfy_url or "",
        "priority": 3,
        "tags": [DECISION_AGENT_TAG],
    }
    try:
        notify.send(nc, note)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# inbound routing (P12 transport reuse: wraps handle_message, never re-
# implements CommandListener's poll/backoff/reply plumbing)

def handle_feedback_message(registry: dict[str, Path], text: str, tags: list[str]) -> Any:
    """Pure routing (no I/O beyond advance_chat's own turn + post): returns
    None when a decision-chat message was fully handled here (the caller
    must NOT also post a reply -- this module posts its own via
    _post_feedback), or the _NOT_HANDLED sentinel when text/tags do not
    look like a decision-chat message at all (caller should fall through
    to its own verb dispatch)."""
    tags = tags or []
    if DECISION_AGENT_TAG in tags or commands.REPLY_TAG in tags:
        return None  # loop guard: our own echo, or P12's own echo

    stripped = (text or "").strip()

    dm = _DECIDE_CMD_RE.match(stripped)
    if dm:
        decision_id, choice = dm.group(1), dm.group(2).strip()
        target = find_project_for_decision(registry, decision_id)
        if target is None:
            return _NOT_HANDLED
        project, cfg = target
        if _finalize_decision(cfg, project, decision_id, choice, "",
                               ActorKind.OPERATOR, "feedback-chat"):
            _post_feedback(cfg, decision_id, f"Resolved {decision_id}: {choice}")
        return None

    pm = _DECISION_PREFIX_RE.match(stripped)
    if pm:
        decision_id, message = pm.group(1), pm.group(2).strip()
        target = find_project_for_decision(registry, decision_id)
        if target is None:
            return _NOT_HANDLED
        project, cfg = target
        advance_chat(cfg, project, decision_id, message)
        return None

    active = _find_sole_active_chat(registry)
    if active is not None and stripped:
        project, cfg, decision_id = active
        advance_chat(cfg, project, decision_id, stripped)
        return None

    return _NOT_HANDLED


def wrap_command_handler(registry: dict[str, Path],
                          base_handler: Callable[[str, list[str]], str | None]
                          ) -> Callable[[str, list[str]], str | None]:
    """Wrap a CommandListener.handle_message-shaped callable so decision-
    chat messages on the SAME feedback topic are intercepted before
    P12's verb allowlist ever sees them; anything not decision-chat-shaped
    (including P12's own loop-guard tag) falls through unchanged."""
    def _wrapped(text: str, tags: list[str]) -> str | None:
        handled = handle_feedback_message(registry, text, tags)
        if handled is _NOT_HANDLED:
            return base_handler(text, tags)
        return handled
    return _wrapped
