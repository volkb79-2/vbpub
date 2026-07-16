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
  ``^(help|status|pause|unpause|digest)( [a-z][a-z0-9-]{0,30}){0,2}$`` on the
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
- Every executed verb (pause/unpause) appends an audited event via
  storage.append_event with actor Actor(OPERATOR, "ntfy-cmd"). status/
  digest/help never mutate state and never append events.

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

from . import config, notify, paths, storage
from .config import NotifyConfig, ProjectConfig
from .types import Actor, ActorKind, EventType, TaskState

# Marks (and lets us recognize) our own replies -- the loop-prevention
# mechanism, since ntfy exposes no sender identity.
REPLY_TAG = "nyxloomd-reply"

# Strict, anchored verb allowlist. No case-insensitivity, no punctuation,
# no shell metacharacters can ever reach a handler: anything that doesn't
# fully match this pattern falls through to UNKNOWN_REPLY. P15 2026-07-15:
# widened to two optional trailing tokens (project, then pause's optional
# mode word) -- see module docstring.
_VERB_RE = re.compile(
    r"^(help|status|pause|unpause|digest)"
    r"(?: ([a-z][a-z0-9-]{0,30}))?(?: ([a-z][a-z0-9-]{0,30}))?$"
)

UNKNOWN_REPLY = "unknown command \u2014 send: help"

HELP_TEXT = "\n".join([
    "nyxloom commands:",
    "help                        - this message",
    "status <project>            - per-state task counts",
    "pause <project> [mode]      - pause; mode is agents|handoffs (default handoffs)",
    "unpause <project>           - resume the project (mode: run)",
    "digest <project>            - recent activity summary",
])

# P15 2026-07-15: ntfy/CLI shorthand mode words -> the flag-file/event mode
# strings reconcile.py and daemon.py use. `pause <project>` with no mode word
# defaults to "handoffs" (drain-handoffs) -- unchanged legacy meaning of a
# bare pause.
_MODE_WORD_TO_MODE = {"agents": "drain-agents", "handoffs": "drain-handoffs"}

DIGEST_MAX_CHARS = 1500


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

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # -- verb dispatch (pure; no transport) ----------------------------------

    def handle_message(self, text: str, tags: list[str]) -> str | None:
        """Pure verb dispatch: returns the reply text (or None for
        nyxloomd-reply-tagged input). Separated from transport for tests."""
        if REPLY_TAG in (tags or []):
            return None

        trimmed = (text or "").strip()
        m = _VERB_RE.match(trimmed)
        if not m:
            return UNKNOWN_REPLY

        verb = m.group(1)
        project = m.group(2)
        mode_word = m.group(3)

        if verb == "help":
            return HELP_TEXT

        if project is None:
            return f"missing project: send '{verb} <project>'"
        if project not in self.registry:
            return f"unknown project: {project}"

        if verb == "status":
            return self._cmd_status(project)
        if verb == "pause":
            return self._cmd_pause(project, mode_word)
        if verb == "unpause":
            return self._cmd_unpause(project)
        if verb == "digest":
            return self._cmd_digest(project)
        return UNKNOWN_REPLY  # unreachable given _VERB_RE; kept defensive

    def _cmd_status(self, project: str) -> str:
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

    def _cmd_pause(self, project: str, mode_word: str | None) -> str:
        """P15 2026-07-15: `pause <project> [agents|handoffs]` -- default
        'handoffs' (drain-handoffs), the legacy meaning of a bare pause. The
        flag file's CONTENT becomes the mode (reconcile.py/daemon.py's
        pause-mode contract); PAUSE_SET carries {"mode": ...}."""
        if mode_word is not None and mode_word not in _MODE_WORD_TO_MODE:
            return f"unknown mode: {mode_word} (use agents|handoffs)"
        mode = _MODE_WORD_TO_MODE.get(mode_word, "drain-handoffs")

        flag_path = paths.pause_flag(project)
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.write_text(mode, encoding="utf-8")
        storage.append_event(
            project, actor=Actor(ActorKind.OPERATOR, "ntfy-cmd"),
            type=EventType.PAUSE_SET, payload={"mode": mode},
        )
        return f"paused ({mode}): {project}"

    def _cmd_unpause(self, project: str) -> str:
        flag_path = paths.pause_flag(project)
        flag_path.unlink(missing_ok=True)
        storage.append_event(
            project, actor=Actor(ActorKind.OPERATOR, "ntfy-cmd"),
            type=EventType.PAUSE_CLEARED, payload={},
        )
        return f"unpaused: {project}"

    def _cmd_digest(self, project: str) -> str:
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
            except Exception:
                pass
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
