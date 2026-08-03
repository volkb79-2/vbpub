"""Inbound ntfy command listener: operator chat-ops. PACKAGE P12.

Lets an operator drive nyxloom from the ntfy phone app by sending short
text commands to a dedicated ntfy topic (`cfg.notify.cmd_topic`).

SECURITY MODEL (non-negotiable, see handoff/P12-ntfy-command-listener.md):

- The listener READS the command topic using a separate, READ-ONLY ntfy
  identity: the token named by `cfg.notify.cmd_token_env` (default
  NTFY_CMD_TOKEN). It never uses the write-only publisher token to read.
- Replies are PUBLISHED via the existing WRITE-ONLY publisher path
  (`notify.send`, using `cfg.notify.token_env`) back to the SAME cmd
  topic, always tagged `nyxloomd-reply`. Any inbound message carrying that
  tag is ignored -- ntfy exposes no sender identity, so tag-based loop
  prevention is the only guard against the listener replying to itself.
- Verb allowlist, strict parse: only
  ``^(help|status|pause|resume|digest)( [a-z][a-z0-9-]{0,30}){0,2}$`` on the
  TRIMMED message body is accepted (P15 2026-07-15: widened from one
  optional arg to two, to carry `pause`'s optional mode word -- the
  compiled pattern uses two explicit capture groups rather than a single
  repeated one, since Python `re` cannot recover more than the LAST match
  of a repeated capturing group; the accepted SHAPE -- up to two
  ``[a-z][a-z0-9-]{0,30}`` tokens, space-separated, same bounds -- is
  unchanged). Anything else -> a fixed "unknown command" reply. There is no
  shell, no eval, and no free-text interpolation into replies: only typed/
  validated fields (the matched verb, the validated [a-z0-9-] project
  token, the validated {agents,handoffs} mode word, and numbers/enum
  values read from storage) are ever placed into a reply, always through
  fixed templates -- the same injection boundary as notify.py, applied to
  replies too.
- CR-15 2026-08-03 (RISK-005): the MUTATING verbs (pause/resume) are CLOSED
  by default. Everything above is transport hygiene, not authentication:
  `cmd_token_env` is this daemon's own READ credential for subscribing, ntfy
  exposes no sender identity, and the old `Actor(OPERATOR, "ntfy-cmd")` was a
  transport name -- the same non-identity as the HTTP surface's "ui". So a
  message on this topic cannot be attributed to a human, and pausing a
  project is the emergency brake. `pause`/`resume` therefore refuse with a
  fixed reply, write nothing, and append ONE audited refusal to the control
  ledger unless the deployment has named the operator this topic's write ACL
  belongs to (`control_auth.channel_operator()` /
  NYXLOOM_CHANNEL_OPERATOR_ID). When it has, the executed verb appends its
  audited event with THAT named operator as the actor. The READ verbs
  (help/status/digest) are unchanged and stay open, exactly as the HTTP read
  surface does.

INTERFACE CONTRACT (frozen; see handoff P12):

- CommandListener(registry, poll_timeout=60) -- registry is the same
  project_id -> repo-root mapping as config.load_registry().
- start()/stop() -- daemon-thread lifecycle; never raises out of the
  thread (all transport errors are caught and retried with capped
  backoff).
- handle_message(text, tags) -> reply text or None -- pure verb dispatch,
  deliberately separated from transport so it is trivially unit-testable.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from pathlib import Path

from . import config, control_auth, notify, paths, resync, storage
from .config import NotifyConfig, ProjectConfig
from .log import get_logger
from .types import Actor, EventType, TaskState

log = get_logger("commands")

# Marks (and lets us recognize) our own replies -- the loop-prevention
# mechanism, since ntfy exposes no sender identity.
REPLY_TAG = "nyxloomd-reply"

# Strict, anchored verb allowlist. No case-insensitivity, no punctuation,
# no shell metacharacters can ever reach a handler: anything that doesn't
# fully match this pattern falls through to UNKNOWN_REPLY. P15 2026-07-15:
# widened to two optional trailing tokens (project, then pause's optional
# mode word) -- see module docstring.
_VERB_RE = re.compile(
    r"^(help|status|pause|resume|digest)"
    r"(?: ([a-z][a-z0-9-]{0,30}))?(?: ([a-z][a-z0-9-]{0,30}))?$"
)

UNKNOWN_REPLY = "unknown command \u2014 send: help"

# CR-15: the fixed refusal for a mutating verb on an unattributable channel.
# Constant text, no interpolation, and identical for every project and verb --
# a refusal must not become an oracle for what exists (same property the HTTP
# 401 has). It names the remedy surfaces, not the deployment knob: the knob is
# a daemon-side trust assertion, not something to advertise on the topic.
CHANNEL_CLOSED_REPLY = (
    "refused: this channel cannot change state \u2014 it carries no verified "
    "sender identity. Use the dashboard or the nyxloom CLI."
)

HELP_TEXT = "\n".join([
    "nyxloom commands:",
    "help                        - this message",
    "status <project>            - per-state task counts",
    "pause <project> [mode]      - pause; mode is agents|handoffs (default handoffs)",
    "resume <project>            - resume the project (mode: run)",
    "digest <project>            - recent activity summary",
])

# P15 2026-07-15: ntfy/CLI shorthand mode words -> the flag-file/event mode
# strings reconcile.py and daemon.py use. `pause <project>` with no mode word
# defaults to "handoffs" (drain-handoffs) -- unchanged legacy meaning of a
# bare pause.
_MODE_WORD_TO_MODE = {"agents": "drain-agents", "handoffs": "drain-handoffs"}

# CR-15: the verbs that change state. Membership here is what makes a verb
# authenticated in `handle_message` -- a new mutating verb that forgets to
# join this set is caught by test_commands.py's verb census, which derives the
# mutating set from the module rather than restating it.
_MUTATING_VERBS = frozenset({"pause", "resume"})

DIGEST_MAX_CHARS = 1500


def _pre_resume_drift_scan(project: str, cfg: ProjectConfig) -> list | None:
    """The ntfy chat-ops resume guard's pre-flight -- mirrors
    `cli._pre_resume_drift_scan` (PACKAGE RP03) EXACTLY: same shared
    ground-truth planner (`resync.resync_plan`, fed by its own
    `gather_handoff_presence` / `gather_git_facts` I/O boundaries), not a
    second drift-detection implementation. Duplicated here (rather than
    imported from cli.py) to avoid a commands.py -> cli.py import that
    would risk a circular import (cli.py's own module wires up other
    pieces of the package); the DETECTOR itself (`resync.resync_plan`) is
    still the one shared implementation.

    Returns the list of `ProposedTransition` rows whose `proposed_action
    != ACTION_NONE` (a possibly-EMPTY list -- "ran clean, no drift"), or
    `None` if the scan itself could not complete (a storage/git failure
    while gathering ground truth). `None` vs `[]` is the same
    load-bearing distinction RP03 established: a scan that could not run
    must never be read as "no drift found" -- see `_cmd_resume` below,
    which refuses on BOTH `None` and a non-empty list, with a DIFFERENT
    message for each."""
    try:
        states = storage.list_states(project)
        frontmatters = resync.gather_handoff_presence(cfg, states)
        git_facts = resync.gather_git_facts(str(cfg.root), cfg.default_branch, states)
        plan = resync.resync_plan(states, frontmatters, git_facts)
    except Exception:
        return None

    return [p for p in plan if p.proposed_action != resync.ACTION_NONE]


class CommandListener:
    """Long-poll listener on the ntfy inbound command topic."""

    BACKOFF_INITIAL = 1.0
    BACKOFF_MAX = 60.0

    def __init__(self, registry: dict[str, Path], poll_timeout: int = 60):
        self.registry = dict(registry)
        self.poll_timeout = poll_timeout
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._since = "0"

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._stop_event.clear()
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        self._thread = t
        log.info("command listener started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        log.info("command listener stopped")

    # -- verb dispatch (pure; no transport) ----------------------------------

    def handle_message(self, text: str, tags: list[str]) -> str | None:
        """Pure verb dispatch: returns the reply text (or None for
        nyxloomd-reply-tagged input). Separated from transport for tests."""
        if REPLY_TAG in (tags or []):
            return None

        trimmed = (text or "").strip()
        m = _VERB_RE.match(trimmed)
        if not m:
            log.debug("command unmatched", trimmed_len=len(trimmed))
            return UNKNOWN_REPLY

        verb = m.group(1)
        project = m.group(2)
        mode_word = m.group(3)

        if verb == "help":
            return HELP_TEXT

        # CR-15: a mutating verb resolves its operator BEFORE the project
        # argument is looked up, so a refused `pause <real>` and a refused
        # `pause <ghost>` are the same fixed string with the same audit record
        # -- the channel twin of the HTTP surface's auth-before-lookup order.
        operator: Actor | None = None
        if verb in _MUTATING_VERBS:
            operator = control_auth.channel_operator_for(verb)
            if operator is None:
                return CHANNEL_CLOSED_REPLY

        if project is None:
            log.debug("command missing project", verb=verb)
            return f"missing project: send '{verb} <project>'"
        if project not in self.registry:
            log.debug("command unknown project", verb=verb, project=project)
            return f"unknown project: {project}"

        if verb == "status":
            return self._cmd_status(project)
        if verb == "pause":
            return self._cmd_pause(project, mode_word, operator)
        if verb == "resume":
            return self._cmd_resume(project, operator)
        if verb == "digest":
            return self._cmd_digest(project)
        return UNKNOWN_REPLY  # unreachable given _VERB_RE; kept defensive

    def _cmd_status(self, project: str) -> str:
        log.debug("status queried", project=project)
        states = storage.list_states(project)
        counts: dict[str, int] = {}
        for tsf in states.values():
            counts[tsf.state.value] = counts.get(tsf.state.value, 0) + 1
        # QUEUED and ACTIVE are always reported (even at zero) -- they are
        # the two operationally interesting buckets; any other non-zero
        # state is appended after, in enum declaration order.
        parts = [
            f"{counts.get(TaskState.QUEUED.value, 0)} {TaskState.QUEUED.value}",
            f"{counts.get(TaskState.ACTIVE.value, 0)} {TaskState.ACTIVE.value}",
        ]
        for st in TaskState:
            if st in (TaskState.QUEUED, TaskState.ACTIVE):
                continue
            c = counts.get(st.value, 0)
            if c:
                parts.append(f"{c} {st.value}")
        line = f"{project}: " + ", ".join(parts)
        if paths.pause_flag(project).exists():
            line += " (paused)"
        return line

    def _cmd_pause(self, project: str, mode_word: str | None, operator: Actor) -> str:
        """P15 2026-07-15: `pause <project> [agents|handoffs]` -- default
        'handoffs' (drain-handoffs), the legacy meaning of a bare pause. The
        flag file's CONTENT becomes the mode (reconcile.py/daemon.py's
        pause-mode contract); PAUSE_SET carries {"mode": ...}.

        CR-15: pausing is the emergency brake, so `operator` is the named
        channel operator `handle_message` already proved -- required, not
        optional, and it becomes the event's actor."""
        if mode_word is not None and mode_word not in _MODE_WORD_TO_MODE:
            log.warning("pause command rejected", reason="unknown-mode",
                        project=project, mode=mode_word)
            return f"unknown mode: {mode_word} (use agents|handoffs)"
        mode = _MODE_WORD_TO_MODE.get(mode_word, "drain-handoffs")

        flag_path = paths.pause_flag(project)
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.write_text(mode, encoding="utf-8")
        storage.append_event(
            project, actor=operator, type=EventType.PAUSE_SET,
            payload={"mode": mode},
        )
        log.info("project paused", project=project, mode=mode,
                 ingress="ntfy", operator_id=operator.id)
        return f"paused ({mode}): {project}"

    def _cmd_resume(self, project: str, operator: Actor) -> str:
        """resume <project> -- the SECOND resume surface (chat-ops), now
        carrying the same RP03 pre-resume drift guard as `cli.cmd_resume`'s
        project-level path (see `_pre_resume_drift_scan` above -- the
        SHARED detector, not a reimplementation). There is no `--force`
        available over ntfy, so a refusal here is unconditional: an
        operator who hits it must go verify/repair via the CLI
        (`nyxloom resync <project>` / `--apply`) -- intentional, since a
        chat-ops override of the riskiest operator action in the system
        with no audit-trail nuance (no forced-payload distinction like the
        CLI's) would be worse than just requiring the CLI for that case.

        A refusal writes NOTHING -- the scan runs and this method returns
        the refusal string BEFORE either the pause-flag unlink or the
        PAUSE_CLEARED append, so a repeated call refuses identically
        (mirrors the CLI's own atomicity contract).

        CR-15: `handle_message` proved the named channel operator before this
        method (and therefore before the project lookup and the drift scan),
        so an unattributable resume never reaches this project's state at
        all; `operator` is that identity and becomes the event's actor."""
        try:
            cfg = ProjectConfig.load(self.registry[project])
        except Exception:
            log.warning("resume refused", project=project, reason="cfg-load-failed")
            return (
                f"error: refusing to resume '{project}' -- could not verify "
                f"its state first (failed to load its project config). Use "
                f"the CLI: nyxloom resync {project}"
            )

        drift = _pre_resume_drift_scan(project, cfg)

        if drift is None:
            # The scan itself could not complete -- NEVER read as "no
            # drift" (the exact bug class RP03 exists to avoid).
            log.warning("resume refused", project=project, reason="scan-failed")
            return (
                f"error: refusing to resume '{project}' -- could not verify "
                f"its state first (the pre-resume drift scan itself "
                f"failed). Inspect manually: nyxloom resync {project}"
            )
        if drift:
            summary = "; ".join(
                f"{p.task_id} ({p.proposed_action}: {p.evidence})" for p in drift
            )
            log.warning("resume refused", project=project, reason="drift",
                        drifted=len(drift))
            return (
                f"error: refusing to resume '{project}' -- drift detected "
                f"in {len(drift)} task(s): {summary}. Repair via the CLI: "
                f"nyxloom resync {project} / nyxloom resync {project} --apply"
            )[:DIGEST_MAX_CHARS]

        # drift == [] -- verified clean, resume proceeds exactly as before
        # this guard was added.
        flag_path = paths.pause_flag(project)
        flag_path.unlink(missing_ok=True)
        storage.append_event(
            project, actor=operator, type=EventType.PAUSE_CLEARED, payload={},
        )
        log.info("project resumed", project=project,
                 ingress="ntfy", operator_id=operator.id)
        return f"resumed: {project}"

    def _cmd_digest(self, project: str) -> str:
        log.debug("digest queried", project=project)
        cfg = ProjectConfig.load(self.registry[project])
        text = notify.digest(cfg, project, 0)
        if not text:
            text = "no recent activity"
        return text[:DIGEST_MAX_CHARS]

    # -- transport ------------------------------------------------------------

    def _run(self) -> None:
        """Reconnect loop with capped backoff; never raises."""
        backoff = self.BACKOFF_INITIAL
        while not self._stop_event.is_set():
            cfg = self._find_cmd_config()
            if cfg is None:
                if self._stop_event.wait(backoff):
                    return
                backoff = min(backoff * 2, self.BACKOFF_MAX)
                continue
            try:
                self._listen_once(cfg)
                backoff = self.BACKOFF_INITIAL
                log.debug("command listener poll cycle complete")
            except Exception as e:
                log.warning("command listener transport failed", error=type(e).__name__)
            if self._stop_event.is_set():
                return
            if self._stop_event.wait(backoff):
                return
            backoff = min(backoff * 2, self.BACKOFF_MAX)

    def _find_cmd_config(self) -> ProjectConfig | None:
        for project in sorted(self.registry):
            try:
                cfg = config.ProjectConfig.load(self.registry[project])
            except Exception:
                continue
            if (cfg.notify.cmd_topic and cfg.notify.ntfy_url
                    and os.environ.get(cfg.notify.cmd_token_env)):
                return cfg
        return None

    def _listen_once(self, cfg: ProjectConfig) -> None:
        token = os.environ.get(cfg.notify.cmd_token_env, "")
        url = f"{cfg.notify.ntfy_url}/{cfg.notify.cmd_topic}/json?poll=0&since={self._since}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.poll_timeout) as resp:
            for raw in resp:
                if self._stop_event.is_set():
                    return
                self._handle_line(cfg, raw)

    def _handle_line(self, cfg: ProjectConfig, raw: bytes) -> None:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        mid = msg.get("id")
        if mid:
            self._since = str(mid)
        if msg.get("event") != "message":
            return  # keepalive / open events are skipped for dispatch
        text = msg.get("message") or ""
        tags = msg.get("tags") or []
        reply = self.handle_message(text, tags)
        if reply is not None:
            self._send_reply(cfg, reply)

    def _send_reply(self, cfg: ProjectConfig, text: str) -> None:
        nc = NotifyConfig(
            ntfy_url=cfg.notify.ntfy_url,
            ntfy_topic=cfg.notify.cmd_topic,
            token_env=cfg.notify.token_env,
        )
        note = {
            "title": "nyxloom",
            "body": text,
            "click": cfg.notify.ntfy_url or "",
            "priority": 3,
            "tags": [REPLY_TAG],
        }
        try:
            notify.send(nc, note)
        except Exception:
            pass
