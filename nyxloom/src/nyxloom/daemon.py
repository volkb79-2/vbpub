"""nyxloomd: resident reconciler + read-only HTTP/SSE surface. PACKAGE P09.

Residency is an optimization, never an authority: every pass rebuilds its
snapshot FROM DISK, so kill -9 on the daemon loses nothing (deciding log
2026-07-15). Wrappers are detached and keep running across daemon restarts.

INTERFACE CONTRACT (frozen):

- Daemon(registry: dict[str, Path]) — one daemon supervises all registered
  projects (config.load_registry()).
- run():
    * write pidfile paths.daemon_dir()/nyxloomd.pid (refuse to start if an
      existing pidfile's pid is alive); append DAEMON_STARTED per project.
    * loop until SIGTERM/SIGINT: for each project run_pass(project), then
      sleep min(reconcile_interval_seconds over projects) — a plain
      time.sleep loop in the main thread is acceptable (no asyncio needed;
      the HTTP server runs in its own thread).
    * on shutdown: append DAEMON_STOPPED per project, remove pidfile. Never
      kill wrappers on shutdown.
- run_pass(project) — ONE reconcile pass, also the body of `tick --once`:
    1. Build ReconcileInput from disk:
       states=storage.list_states, frontmatters/lint via frontmatter+lint
       modules (lint_clean = not lint.has_blocking), decisions_open from
       decisions.parse_inbox, merged_branches from
       `git -C root branch --merged <default_branch>` plus branches whose
       task statefile is MERGED+, leases_free via leases.holder_info,
       provider_ok via adapters.probe MEMOIZED for probe_ttl_seconds (600)
       in daemon memory (a restart just re-probes), log_quiet_seconds /
       pid_alive / receipts by scanning attempt dirs of non-terminal
       attempts (P14 2026-07-15 item 5 belt-and-braces: when the recorded
       attempt.pid looks dead, also check attempt_dir/wrapper.pid -- a
       resumed attempt's freshest wrapper pid on disk -- before declaring
       the attempt dead; a stale statefile pid must never hide a live
       process), stall_confirmed from _confirm_stall() (tier 2, P14
       2026-07-15 item 3 made REAL: pid alive AND log quiet AND
       /proc/<pid>/stat unchanged utime+stime over two consecutive passes
       AND no CPU-active descendant either -- a best-effort /proc walk from
       pid via ppid matching, since a CLI that forks a busy child while its
       own top-level process idles must NOT be confirmed stalled -- keep
       the two-pass cache in daemon memory; a declared-long-gate exemption
       per v2 §5.4 is NOT implemented -- the wrapper does not run gates yet
       (receipt.oracles stays [], see wrapper.py), so there is no
       gate-running marker to exempt against), resume_failures from
       _resume_failures_scan() (P26 2026-07-16: for each INTERRUPTED
       attempt, count consecutive `.resume-N` logs, most recent first, that
       sat at/below RESUME_NO_PROGRESS_LOG_BYTES for at least policy.
       resume_progress_grace_seconds without growing -- a resume that died
       without making real progress; the planner stops resuming a session
       once this reaches policy.max_resume_failures and fresh-starts
       instead), budget_remaining from policy.max_cost minus summed attempt
       usage costs (same currency only), merge_history/carve_outcomes/
       rejections from recent events (iter_events tail).
    2. actions = reconcile.plan_project(inp)
    3. execute(project, action) for each — see EXECUTION MAP below.
    4. render.render_all(...) if any event was appended this pass.
    5. Wrap the whole pass in try/except: append TICK_ERROR (bounded repr)
       and continue — one project's failure never stops the loop.
- EXECUTION MAP (all storage writes via append_and_apply, actor
  Actor(TICK, 'nyxloomd')):
    CreateTask -> TASK_CREATED (statefile CARVED, handoff_path set)
    Transition -> TASK_TRANSITIONED (payload from/to/notes); P14 2026-07-15
      item 4: when action.to is BLOCKED and action.blocker is set, emits
      TASK_BLOCKED (payload from/blocker/notes) instead -- the typed-blocker
      path for an INTERRUPTED attempt with no resume handle or an exhausted
      attempt budget (silent-dead-end fix).
    DispatchImplementer -> create worktree if missing (git worktree add -b
      feat/<task_id> <worktree_root>/feat/<task_id> <default_branch>; if
      branch exists, add without -b), build Attempt record (types.new_id
      ('att'), role IMPLEMENTER, state CREATED, route snapshot with
      routes_rev), ATTEMPT_CREATED; adapters.build_dispatch ->
      WrapperSpec -> wrapper.launch_detached; ATTEMPT_PREFLIGHTED (state
      PREFLIGHTING, pid=wrapper pid). Task ACTIVE via Transition.
    ResumeAttempt -> adapters.build_resume argv -> new WrapperSpec into the
      SAME attempt dir (suffix .resume-N) -> launch; ATTEMPT_RESUMED
      (state RUNNING, pid=NEW wrapper pid, log_path=the resume-N log path --
      P14 2026-07-15 item 5: both are refreshed on the attempt record at
      resume time rather than left stale until the wrapper's own later
      ATTEMPT_STARTED catches up).
    InterruptAttempt -> SIGTERM to the WRAPPER's own pid (attempt_dir/
      wrapper.pid; P14 2026-07-15: NOT child.pid's pgid directly -- that
      bypasses the wrapper's own signal handler, which is what forwards to
      the child AND classifies the exit as 'interrupted'; falls back to
      signaling child.pid's pgid directly only if wrapper.pid is missing/
      dead, i.e. the wrapper already crashed); the WRAPPER emits the
      interrupted event, not the daemon. Fires both for tier-2-confirmed
      stalls (attempt already STALLED) and for the P14 item 6 wall-clock
      cap (attempt running longer than fm.budget.max_wall_seconds or the
      default, regardless of liveness).
    MarkInterrupted -> ATTEMPT_INTERRUPTED (state INTERRUPTED, ended=now).
    MarkStalled -> ATTEMPT_STALLED (state STALLED only; NOT ended -- the
      process is still running, just confirmed unresponsive). P14
      2026-07-15 item 2: makes a tier-2-confirmed stall visible BEFORE the
      next pass's InterruptAttempt; ATTEMPT_STALLED is a default notify
      push class (config.py NotifyConfig.push_classes).
    StallCheck -> feed _confirm_stall cache only (no event).
    EmitAttemptExit -> idempotent healing (amended 2026-07-15): if the
      attempt is not yet EXITED (wrapper died before writing its event but
      receipt.json exists), emit ATTEMPT_EXITED from the receipt; in every
      case perform the task transition per reconcile contract item 4. The
      attempt scan feeding the planner includes EXITED attempts of
      still-ACTIVE tasks for exactly this purpose.
    ProviderPause -> PROVIDER_STATE_CHANGED {route_id, state:'limited'};
      daemon memory marks provider_ok[route_id]=False for
      provider_pause_seconds (3600) — and NEEDS_OPERATOR notification.
    OpenWave -> WAVE_OPENED (wave_id=new_id('wave'), task_ids).
    LaunchReview -> assemble packet dir under attempts dir of a synthetic
      review attempt: per task, `git -C root diff <default_branch>...HEAD`
      of its branch dumped to <packet>/<task_id>.diff + --stat + handoff/
      report paths list in packet.md; create Attempt (role FRONTIER_REVIEW,
      route = first route of tier 'frontier-review'), dispatch via wrapper
      like an implementer with the packet path in the prompt.
    SpecAttention -> SPEC_ATTENTION event.
    After every event append: notify.notify_event(cfg, states, ev).
- HTTP (loopback only, port = min over projects' policy.http_port):
  thread with http.server.ThreadingHTTPServer.
    GET /                    -> 302 /www/index.html
    GET /www/<path>          -> serve paths.www_dir() files (no traversal:
                                resolve() must stay under www_dir)
    GET /api/projects        -> registry summary JSON
    GET /api/tasks?project=  -> [statefile dicts]
    GET /api/task/<project>/<task_id> -> statefile dict
    GET /api/events?project=&since=   -> [event dicts] (cap 500)
    GET /api/log/<project>/<attempt_id>?tail=65536 -> text/plain, LAST n
        bytes of the attempt log passed through cfg.redact
    P22 2026-07-16 (read-only agent drilldown, live attach): GET
      /api/drilldown/<project>/<attempt_id>?tail=65536 -> text/html: the
      LAST n bytes of the attempt log, rendered via
      render.render_transcript (assistant text deltas + tool names, never
      raw JSON) and ONLY THEN passed through cfg.redact — redacting the
      raw stream-json first (i.e. /api/log's order) can splice
      '[REDACTED]' across a JSON string's closing quote/braces and
      silently drop that whole line (including its tool name) from the
      transcript; redacting the human-readable rendering instead is safe
      and lossless. render.render_drilldown_page wraps the redacted
      transcript in a small auto-refreshing (<meta http-equiv="refresh">,
      no JS/websocket) page. This surfaces a running OR recent attempt's
      live output/reasoning; it is READ-ONLY like every GET here — no
      control on the page mutates state.
    GET /api/stream?project= -> text/event-stream: poll events.jsonl every
        2s, emit new events as `data: <json>\n\n` (heartbeat comment line
        every 15s); connection ends when client disconnects.
  P15 2026-07-15 (spec amendment, user directive): CONFIG mutations are now
  allowed through audited loopback endpoints (workflow-STATE mutations
  remain CLI-only). All three are POST, JSON in/out, 400 on validation
  failure with NO write performed, 404 for an unknown project/tier, 405 for
  GET on these paths:
    POST /api/config/policy {project, key, value} -> config.
        update_project_policy surgical edit of <root>/.nyxloom/
        project.toml's [policy] section; key must be one of the seven
        editable Policy fields, value an int within that key's sane bounds
        (see daemon._POLICY_BOUNDS); appends CONFIG_CHANGED {scope:
        "policy", key, old, new} and re-renders.
    POST /api/config/pause {project, mode: "run"|"drain-handoffs"|
        "drain-agents"} -> writes/removes paths.pause_flag(project) with
        the mode as its CONTENT (see reconcile.py's pause-mode semantics
        and Daemon._pause_mode); appends PAUSE_SET {"mode": mode} (mode !=
        "run") or PAUSE_CLEARED (mode == "run"), actor OPERATOR 'ui' — the
        SAME event shape the CLI/ntfy surfaces use, so all three pause
        surfaces are audited identically; re-renders.
    POST /api/config/tier {tier, routes: [route_id, ...]} -> config.
        update_routes surgical edit of the LIVE routes.toml's
        `[tiers.<tier>] routes = [...]` line (route ids must already be
        DEFINED — v1 never creates new route definitions from the UI);
        appends CONFIG_CHANGED {scope: "routes", key: tier, old, new} to
        EVERY registered project's event log (routes.toml is shared, not
        project-scoped) and re-renders.
  Every other GET endpoint above remains read-only.
  P16 2026-07-15 (carver automation, user directive): POST /api/config/
  policy also accepts key='carve_authority' (value one of "branch"/"main"/
  "files", string not int -- validated separately from _POLICY_BOUNDS'
  numeric keys, same surgical-edit + CONFIG_CHANGED contract otherwise).
- stop(): set the loop flag false and shut the HTTP server down (used by
  tests; signal handlers call it).

P16 2026-07-15 (carver automation, user directives: carve authority is
configurable per-project, default factory = carve-branch-then-human-admit;
carve-ahead count configurable; the carver emits a persisted NARRATIVE
summary each cycle):

- CarveDispatch execution (reconcile.py's carve trigger, module contract
  item 9): dispatches a FRONTIER carver leg (tier 'frontier-review' route,
  role CARVER) via the wrapper. Since a carve produces brand-new handoffs
  (no pre-existing task to host the attempt), the daemon mints a SYNTHETIC
  task statefile (task_id f'carve-{project}-{seq}', state ACTIVE, no
  handoff_path) purely to satisfy wrapper.py's frozen contract (it always
  loads a real statefile + attempt by id) -- this mirrors how a wave
  review attempt "borrows" a real task's ACTIVE/AWAITING_REVIEW capacity
  slot (SPEC §5.7's active_count already counts AWAITING_REVIEW); `seq` is
  a monotonic per-project counter (count of past ATTEMPT_CREATED events
  whose attempt.role == 'carver', +1 -- recomputed from the event log every
  time, never in-memory-only, so a daemon restart or a parse-failed prior
  carve never collides with the next). cfg.policy.carve_authority routes
  where the carver works and what happens once it exits:
    'branch' (DEFAULT): a fresh `carve/<project>-<seq>` worktree/branch off
      default_branch (mirrors _ensure_worktree); the carver commits new
      handoff files there and does NOT merge -- a human admits by merging
      (the next tick's frontmatter.discover_handoffs then materializes them
      from cfg.root once merged).
    'main': the carver works directly in cfg.root; it commits new handoff
      files straight to the currently-checked-out branch (lint-gated by the
      EXISTING CARVED->QUEUED lint_clean transition, item 1 of reconcile.py
      -- no new lint code needed here).
    'files': the carver works directly in cfg.root and writes new handoff
      files WITHOUT committing (no git); frontmatter.discover_handoffs
      globs disk files regardless of git status, so the next tick
      materializes them the same way.
  The packet (built fresh per dispatch, written to the attempt's own
  packet/packet.md like a review packet) gives the carver: recent
  REVIEW_RECORDED follow-ups, conventional backlog/roadmap file paths under
  cfg.root/docs (named, not slurped -- the carver reads them itself, same
  economy as the review packet's diff-only embedding), the current
  non-terminal queue, and the REQUIRED OUTPUT CONTRACT below.
- REQUIRED carver output contract: the carver writes
  `<reports_dir>/CARVE-<seq>.md` (in whichever worktree it is dispatched
  into) containing EXACTLY one JSON object (a CarveSummary: carved
  [{id, why, source_kind}], review_reflection str, headroom_estimate int,
  headroom_rationale str, outcome one of the 7 v2 §8 outcomes). On
  EmitAttemptExit (reconcile.py's existing per-attempt scan already detects
  this generically; the daemon adds a role == CARVER branch here, checked
  BEFORE the FRONTIER_REVIEW/implementer branches): read+parse that file;
  persist the FULL CarveSummary (+ 'seq' + a 'timestamp') to
  `$XDG_STATE/nyxloom/<project>/carves/<seq>.json` (daemon-written; NOT in
  the consumer repo even when authority puts the .md there too -- this is
  the dashboard's own durable record, read directly off disk by render.py,
  never replayed from events.jsonl); emit CARVE_OUTCOME with TYPED FIELDS
  ONLY (seq, carved_ids, outcome, headroom_estimate -- no why/reflection/
  rationale prose, even though CARVE_OUTCOME is not itself a notify.py
  push/digest class today: the free-text reflection is persisted for the
  dashboard but NEVER sent to a notification channel -- injection
  boundary); if headroom_estimate < policy.headroom_warn, also push
  SPEC_ATTENTION {reason: 'headroom-low', detail: '<n> packages left'}; if
  outcome == 'ROADMAP_EXHAUSTED', also push SPEC_ATTENTION {reason:
  'roadmap-exhausted', detail: '<n> packages left'} (this is what
  reconcile.py's carve trigger later reads back via
  ReconcileInput.roadmap_exhausted_open, computed the same way
  _ratchet_already_open scans for its own reason string); when
  cfg.policy.carve_authority == 'branch', ALSO push NEEDS_OPERATOR {reason:
  'carve-ready', carved_count, headroom_estimate} (typed only -- a human
  admits by merging the carve branch). A missing/unparsable CARVE-<seq>.md
  is NOT fatal: no CARVE_OUTCOME is emitted, but a NEEDS_OPERATOR {reason:
  'carve-parse-failed', seq} still fires so a broken carve leg surfaces
  rather than silently vanishing. Either way the synthetic carve task is
  finally moved to TaskState.SUPERSEDED (the only terminal edge reachable
  from ACTIVE per TASK_TRANSITIONS; COMPLETED requires the full MERGED->
  VALIDATING pipeline, which a bookkeeping-only task never enters) --
  this is what clears reconcile.py's "carve slot" (a carve task counts as
  in-flight only while non-terminal).
"""

from __future__ import annotations

import http.server
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import (
    adapters, commands, config, decision_chat, decisions, frontmatter, leases,
    lint, notify, paths, reconcile, render, storage, wrapper,
)
from .config import ProjectConfig
from .types import (
    Actor, ActorKind, Attempt, AttemptState, Blocker, BlockerType, Event,
    EventType, Receipt, ReceiptResult, Role, Route, TaskState, TaskStateFile,
    TERMINAL_ATTEMPT_STATES, iso, new_id, utc_now,
)

# Tunables (module constants so tests can shrink them for determinism).
PROBE_TTL_SECONDS = 600
PROVIDER_PAUSE_SECONDS = 3600
SSE_POLL_SECONDS = 0.5
SSE_HEARTBEAT_SECONDS = 15.0
DEFAULT_HTTP_PORT = 8942
DEFAULT_RECONCILE_INTERVAL = 30.0
# P26 2026-07-16: a resume log at or below this size is treated as "no
# progress" (banner/prompt only, no real work output) once it has sat
# unwritten for policy.resume_progress_grace_seconds.
RESUME_NO_PROGRESS_LOG_BYTES = 200

# P15 2026-07-15: UI config endpoints (POST-only; GET on these -> 405).
# P18 2026-07-16: /api/decision/reply joins this POST-only set (not a config
# mutation, but the same GET->405 guard applies).
_CONFIG_POST_PATHS = frozenset({
    "/api/config/policy", "/api/config/pause", "/api/config/tier",
    "/api/decision/reply",
})

# Sane per-key int bounds for POST /api/config/policy. The handoff spells
# out "(1..64, interval 5..600)" for the count-like knobs and the reconcile
# interval respectively; the two duration knobs (quiet/wall-clock seconds)
# aren't literally bounded by the same tiny range in the handoff text (their
# real-world defaults, 300s and 10800s, would themselves be "out of bounds"
# under 1..64) so this package picks generous but sane second-denominated
# ceilings for them instead — flagged as an assumption in the P15 REPORT.
_POLICY_BOUNDS: dict[str, tuple[int, int]] = {
    "max_active_tasks": (1, 64),
    "ready_queue_target": (1, 64),
    "max_attempts_per_task": (1, 64),
    "wave_max_diffs": (1, 64),
    "stall_log_quiet_seconds": (1, 86400),
    "attempt_max_wall_seconds": (1, 604800),
    "reconcile_interval_seconds": (5, 600),
    # P16 2026-07-15: the two INT carve-automation Policy keys (bounds: 0 is
    # a valid "disable carve automation for this project" setting for
    # either -- see reconcile.py's carve trigger, which never fires when
    # carve_ahead_target is 0 since ready_count >= 0 is never < 0).
    "carve_ahead_target": (0, 64),
    "headroom_warn": (0, 64),
}

# P15 2026-07-15: factory-state pause modes accepted by POST /api/config/pause.
_PAUSE_MODES = frozenset({"run", "drain-handoffs", "drain-agents"})

# P16 2026-07-15: the one STRING-valued editable Policy key (validated
# separately from _POLICY_BOUNDS' int keys in _post_config_policy).
_CARVE_AUTHORITIES = frozenset({"branch", "main", "files"})

# v2 §8 stop-policy outcomes (docs/SPEC.md §8), inherited verbatim.
_CARVE_OUTCOMES = frozenset({
    "CANDIDATES_READY", "MILESTONE_COMPLETE", "ROADMAP_EXHAUSTED",
    "SPEC_GAP", "DECISION_REQUIRED", "EXTERNAL_BLOCKER", "BUDGET_EXHAUSTED",
})


@dataclass
class CarveSummary:
    """P16 2026-07-15: the carver's REQUIRED output contract (module
    docstring). A small dataclass local to this module (not types.py, which
    is frozen for this package per STANDING.md) -- plain-JSON fields only,
    matching the rest of this codebase's serde convention (manual to_dict/
    from_dict rather than the private types._Serde mixin, which is not
    exported for use outside types.py)."""
    carved: list[dict[str, str]] = field(default_factory=list)
    review_reflection: str = ""
    headroom_estimate: int = 0
    headroom_rationale: str = ""
    outcome: str = "CANDIDATES_READY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "carved": [dict(c) for c in self.carved],
            "review_reflection": self.review_reflection,
            "headroom_estimate": self.headroom_estimate,
            "headroom_rationale": self.headroom_rationale,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CarveSummary":
        carved_raw = d.get("carved") or []
        carved = [
            {
                "id": str(c.get("id", "")),
                "why": str(c.get("why", "")),
                "source_kind": str(c.get("source_kind", "")),
            }
            for c in carved_raw if isinstance(c, dict)
        ]
        return cls(
            carved=carved,
            review_reflection=str(d.get("review_reflection", "")),
            headroom_estimate=int(d.get("headroom_estimate", 0) or 0),
            headroom_rationale=str(d.get("headroom_rationale", "")),
            outcome=str(d.get("outcome", "CANDIDATES_READY")),
        )


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Daemon:
    def __init__(self, registry: dict[str, Path]):
        self.registry = registry
        self.http_port: int = 0
        self._stop_event = threading.Event()
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._cmd_listener: commands.CommandListener | None = None
        # Daemon memory: disposable, rebuilt on restart.
        self._probe_memo: dict[str, tuple[float, bool, str]] = {}
        self._stall_cache: dict[str, str | None] = {}
        self._provider_paused: dict[str, float] = {}
        self._decisions_seen: dict[str, dict[str, str]] = {}

    # -- lifecycle ------------------------------------------------------

    def run(self) -> None:
        pidfile = paths.daemon_dir() / "nyxloomd.pid"
        if pidfile.exists():
            try:
                existing = int(pidfile.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                existing = None
            if existing is not None and _pid_alive(existing):
                raise RuntimeError(f"nyxloomd already running (pid {existing})")
        paths.daemon_dir().mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(os.getpid()), encoding="utf-8")
        self._install_signal_handlers()
        try:
            for project in self.registry:
                self._emit_lifecycle(project, EventType.DAEMON_STARTED)
            self._start_http()
            self._start_cmd_listener()
            try:
                while not self._stop_event.is_set():
                    for project in list(self.registry):
                        self.run_pass(project)
                    self._stop_event.wait(self._min_interval())
            finally:
                self._stop_cmd_listener()
                self._stop_http()
                for project in self.registry:
                    self._emit_lifecycle(project, EventType.DAEMON_STOPPED)
        finally:
            try:
                pidfile.unlink()
            except FileNotFoundError:
                pass

    def stop(self) -> None:
        self._stop_event.set()
        self._stop_cmd_listener()
        self._stop_http()

    def _start_cmd_listener(self) -> None:
        """P12: start the ntfy command listener if any project wants one.

        P18 2026-07-16: wrap its pure verb dispatch (handle_message) with
        decision_chat's feedback-channel router FIRST -- the 2-channel
        design (nyxloom-trove/nyxloom.toml [notify]) unifies P12's cmd
        topic with the decision-chat escalation loop onto the SAME
        `feedback` channel (cfg.notify.cmd_topic), so both concerns share
        one listener/topic/identity. A decision-shaped message ('<D-id>:
        ...', 'decide <D-id> <choice>', or bare text with exactly one
        active chat) is fully handled by decision_chat and never reaches
        the verb allowlist; everything else (including P12's own
        REPLY_TAG loop-guard) falls through to the original handler
        unchanged. This reuses P12's transport (poll/backoff/reply)
        verbatim -- only the pure dispatch function is wrapped, never the
        listener's I/O (see decision_chat.wrap_command_handler)."""
        for project, root in self.registry.items():
            try:
                cfg = config.ProjectConfig.load(root)
            except Exception:
                continue
            if cfg.notify.cmd_topic and os.environ.get(cfg.notify.cmd_token_env):
                listener = commands.CommandListener(self.registry)
                listener.handle_message = decision_chat.wrap_command_handler(
                    self.registry, listener.handle_message)
                listener.start()
                self._cmd_listener = listener
                return

    def _stop_cmd_listener(self) -> None:
        if self._cmd_listener is not None:
            self._cmd_listener.stop()
            self._cmd_listener = None

    def _install_signal_handlers(self) -> None:
        try:
            signal.signal(signal.SIGTERM, lambda signum, frame: self.stop())
            signal.signal(signal.SIGINT, lambda signum, frame: self.stop())
        except ValueError:
            pass  # not the main thread (e.g. under test); stop() is still callable directly

    def _min_interval(self) -> float:
        intervals = []
        for project, root in self.registry.items():
            try:
                cfg = config.ProjectConfig.load(root)
                intervals.append(cfg.policy.reconcile_interval_seconds)
            except Exception:
                continue
        return float(min(intervals)) if intervals else DEFAULT_RECONCILE_INTERVAL

    def _emit_lifecycle(self, project: str, ev_type: EventType) -> None:
        try:
            cfg: ProjectConfig | None = config.ProjectConfig.load(self.registry[project])
        except Exception:
            cfg = None
        ev = storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.TICK, "nyxloomd"), type=ev_type, payload={},
        )
        if cfg is not None:
            try:
                notify.notify_event(cfg, {}, ev)
            except Exception:
                pass

    # -- one reconcile pass -----------------------------------------------

    def run_pass(self, project: str) -> int:
        """One reconcile pass; returns number of actions executed."""
        try:
            root = self.registry[project]
            cfg = config.ProjectConfig.load(root)
            states = storage.list_states(project)
            appended: list[Event] = []

            appended.extend(self._reconcile_decisions(project, cfg, states))

            inp = self._build_input(project, cfg, states)
            actions = reconcile.plan_project(inp)
            for action in actions:
                appended.extend(self._execute(project, cfg, states, action))
            if appended:
                render.render_after_event(self.registry)
            return len(actions)
        except Exception as exc:
            detail = repr(exc)[:500]
            try:
                ev = storage.append_and_apply(
                    project, {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
                    type=EventType.TICK_ERROR, payload={"error": detail},
                )
                try:
                    cfg2 = config.ProjectConfig.load(self.registry[project])
                    notify.notify_event(cfg2, {}, ev)
                except Exception:
                    pass
            except Exception:
                pass
            return 0

    def _reconcile_decisions(self, project: str, cfg: ProjectConfig,
                              states: dict[str, TaskStateFile]) -> list[Event]:
        seen = self._decisions_seen.setdefault(project, {})
        out: list[Event] = []
        try:
            events = decisions.reconcile_decisions(cfg, states, seen)
        except Exception:
            events = []
        for ev_type_str, decision_id in events:
            out.append(self._append_ev(project, cfg, states, EventType(ev_type_str), {},
                                        decision_id=decision_id))
            if ev_type_str == "DECISION_OPENED":
                # P18: additional actionable push to the feedback channel,
                # in ADDITION to the normal notifications-channel push
                # notify.notify_event already sent above via _append_ev.
                try:
                    decision_chat.notify_decision_opened(cfg, decision_id)
                except Exception:
                    pass
        inbox_path = cfg.root / cfg.decisions_inbox
        if inbox_path.exists():
            try:
                parsed = decisions.parse_inbox(inbox_path.read_text(encoding="utf-8"))
                for d in parsed:
                    seen[d.id] = d.status
            except Exception:
                pass
        return out

    # -- input building ----------------------------------------------------

    def _build_input(self, project: str, cfg: ProjectConfig,
                      states: dict[str, TaskStateFile]) -> reconcile.ReconcileInput:
        routes = config.Routes.load()
        frontmatters: dict[str, tuple] = {}
        for path in frontmatter.discover_handoffs(cfg):
            try:
                fm, _body = frontmatter.parse_handoff(path)
            except Exception:
                continue
            try:
                relpath = str(path.resolve().relative_to(cfg.root.resolve()))
            except ValueError:
                relpath = str(path)
            frontmatters[fm.id] = (fm, relpath)

        try:
            findings = lint.lint_project(cfg)
        except Exception:
            findings = {}
        lint_clean: dict[str, bool] = {}
        for fm_id, (_fm, relpath) in frontmatters.items():
            f = findings.get(relpath, [])
            lint_clean[fm_id] = not lint.has_blocking(f)

        pause_mode = self._pause_mode(project)
        project_paused = pause_mode != "run"
        try:
            decisions_open = decisions.open_ids(cfg)
        except Exception:
            decisions_open = set()
        merged_branches = self._merged_branches(cfg, states)
        leases_free = self._leases_free(cfg)
        provider_ok = self._provider_ok(routes)
        log_quiet_seconds, pid_alive, receipts = self._attempt_scan(project, states)
        stall_confirmed = self._confirm_stall(states, log_quiet_seconds, pid_alive, cfg)
        resume_failures = self._resume_failures_scan(
            project, states, cfg.policy.resume_progress_grace_seconds)
        budget_remaining = self._budget_remaining(cfg, states)
        merge_history, carve_outcomes, review_rejections_by_area, blocked_underspecified_count = \
            self._history(project)
        ratchet_already_open = self._ratchet_already_open(project)
        roadmap_exhausted_open = self._roadmap_exhausted_open(project)
        # P14 2026-07-15 item 6: config.Policy is frozen for this package
        # (only NotifyConfig.push_classes may be edited), so
        # attempt_max_wall_seconds is NOT a Policy field here -- getattr
        # falls back to reconcile's own default, but forward-compatibly
        # picks up a future Policy field with zero code change if one is
        # ever added.
        attempt_max_wall_seconds = (
            getattr(cfg.policy, "attempt_max_wall_seconds", None)
            or reconcile.DEFAULT_ATTEMPT_MAX_WALL_SECONDS
        )

        return reconcile.ReconcileInput(
            now=utc_now(),
            cfg=cfg,
            routes=routes,
            states=states,
            frontmatters=frontmatters,
            lint_clean=lint_clean,
            project_paused=project_paused,
            decisions_open=decisions_open,
            merged_branches=merged_branches,
            leases_free=leases_free,
            provider_ok=provider_ok,
            log_quiet_seconds=log_quiet_seconds,
            pid_alive=pid_alive,
            receipts=receipts,
            stall_confirmed=stall_confirmed,
            resume_failures=resume_failures,
            budget_remaining=budget_remaining,
            merge_history=merge_history,
            ratchet_already_open=ratchet_already_open,
            carve_outcomes=carve_outcomes,
            review_rejections_by_area=review_rejections_by_area,
            blocked_underspecified_count=blocked_underspecified_count,
            attempt_max_wall_seconds=attempt_max_wall_seconds,
            pause_mode=pause_mode,
            roadmap_exhausted_open=roadmap_exhausted_open,
        )

    def _pause_mode(self, project: str) -> str:
        """P15 2026-07-15 (factory-state pause MODES): the project pause
        flag file's CONTENT is now the mode. Absent -> 'run'. An explicit
        'drain-agents' content selects that mode; anything else (including
        the legacy EMPTY flag file — today's pre-P15 behaviour) is
        'drain-handoffs', since that mode is exactly what a bare boolean
        pause flag always meant (block new dispatch only)."""
        p = paths.pause_flag(project)
        if not p.exists():
            return "run"
        try:
            content = p.read_text(encoding="utf-8").strip()
        except OSError:
            content = ""
        if content == "drain-agents":
            return "drain-agents"
        return "drain-handoffs"

    def _merged_branches(self, cfg: ProjectConfig, states: dict[str, TaskStateFile]) -> set[str]:
        out: set[str] = set()
        try:
            res = subprocess.run(
                ["git", "-C", str(cfg.root), "branch", "--merged", cfg.default_branch],
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    name = line.strip().lstrip("*").strip()
                    if not name:
                        continue
                    out.add(name)
                    if name.startswith("feat/"):
                        out.add(name[len("feat/"):])
        except Exception:
            pass
        for tsf in states.values():
            if tsf.state in (TaskState.MERGED, TaskState.VALIDATING, TaskState.COMPLETED):
                out.add(tsf.task_id)
                out.add(f"feat/{tsf.task_id}")
        return out

    def _leases_free(self, cfg: ProjectConfig) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for mdef in cfg.mutexes.values():
            lease_name = mdef.lease_name(cfg.project_id)
            try:
                info = leases.holder_info(lease_name, capacity=mdef.capacity)
                out[lease_name] = any(not slot["held"] for slot in info)
            except Exception:
                out[lease_name] = True
        return out

    def _provider_ok(self, routes: config.Routes) -> dict[str, bool]:
        now = time.monotonic()
        out: dict[str, bool] = {}
        for route_id, route in routes.routes.items():
            paused_until = self._provider_paused.get(route_id)
            if paused_until is not None and now < paused_until:
                out[route_id] = False
                continue
            memo = self._probe_memo.get(route_id)
            if memo is not None and (now - memo[0]) < PROBE_TTL_SECONDS:
                out[route_id] = memo[1]
                continue
            try:
                ok, detail = adapters.probe(route)
            except Exception as exc:
                ok, detail = False, repr(exc)[:200]
            self._probe_memo[route_id] = (now, ok, detail)
            out[route_id] = ok
        return out

    def _attempt_scan(self, project: str, states: dict[str, TaskStateFile]):
        log_quiet: dict[str, float | None] = {}
        pid_alive: dict[str, bool] = {}
        receipts: dict[str, dict | None] = {}
        now = time.time()
        for tsf in states.values():
            for att in tsf.attempts:
                if att.state in TERMINAL_ATTEMPT_STATES:
                    # EXITED attempts whose receipt still needs consuming:
                    # implementer exit while task ACTIVE, or (2026-07-15
                    # deadlock fix) frontier-review exit while task is still
                    # AWAITING_REVIEW — the planner needs both receipts.
                    if not (att.state == AttemptState.EXITED
                            and ((tsf.state == TaskState.ACTIVE
                                  and att.role == Role.IMPLEMENTER)
                                 or (tsf.state == TaskState.AWAITING_REVIEW
                                     and att.role == Role.FRONTIER_REVIEW))):
                        continue
                attempt_dir = paths.attempt_dir(project, att.attempt_id)
                receipt_path = attempt_dir / "receipt.json"
                if receipt_path.exists():
                    try:
                        receipts[att.attempt_id] = json.loads(receipt_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        receipts[att.attempt_id] = None
                else:
                    receipts[att.attempt_id] = None
                alive = _pid_alive(att.pid)
                if not alive:
                    # P14 2026-07-15 item 5 belt-and-braces: the statefile's
                    # recorded pid may be stale (a resume that hasn't been
                    # bookkept yet, or any other drift) -- cross-check the
                    # freshest wrapper.pid file actually on disk before
                    # declaring the attempt dead.
                    wpid_file = attempt_dir / "wrapper.pid"
                    if wpid_file.exists():
                        try:
                            wpid = int(wpid_file.read_text(encoding="utf-8").strip())
                        except (ValueError, OSError):
                            wpid = None
                        if wpid is not None and wpid != att.pid and _pid_alive(wpid):
                            alive = True
                pid_alive[att.attempt_id] = alive
                log_path = Path(att.log_path) if att.log_path else (attempt_dir / "attempt.log")
                if log_path.exists():
                    log_quiet[att.attempt_id] = max(0.0, now - log_path.stat().st_mtime)
                else:
                    log_quiet[att.attempt_id] = None
        return log_quiet, pid_alive, receipts

    def _resume_failures_scan(self, project: str, states: dict[str, TaskStateFile],
                               grace_seconds: int) -> dict[str, int]:
        """P26 2026-07-16: attempt_id -> count of consecutive `.resume-N`
        logs (most recent first) that sat at/below RESUME_NO_PROGRESS_
        LOG_BYTES for at least grace_seconds without growing -- i.e. a
        resume that produced no real progress before dying. Only computed
        for INTERRUPTED attempts (the ones reconcile.plan_project actually
        consults this for); stops counting at the first resume that shows
        real progress or is still too fresh to judge."""
        now = time.time()
        out: dict[str, int] = {}
        for tsf in states.values():
            for att in tsf.attempts:
                if att.state != AttemptState.INTERRUPTED:
                    continue
                attempt_dir = paths.attempt_dir(project, att.attempt_id)
                resume_logs = []
                n = 1
                while True:
                    log_path = attempt_dir / f"attempt.resume-{n}.log"
                    if not log_path.exists():
                        break
                    resume_logs.append(log_path)
                    n += 1
                failures = 0
                for log_path in reversed(resume_logs):
                    try:
                        st = log_path.stat()
                    except OSError:
                        break
                    age = now - st.st_mtime
                    if st.st_size <= RESUME_NO_PROGRESS_LOG_BYTES and age >= grace_seconds:
                        failures += 1
                    else:
                        break
                out[att.attempt_id] = failures
        return out

    def _confirm_stall(self, states: dict[str, TaskStateFile], log_quiet_seconds, pid_alive,
                        cfg: ProjectConfig) -> dict[str, bool]:
        """Tier-2 confirmation (P14 2026-07-15 item 3, made REAL): pid alive
        AND log quiet over the policy threshold AND the combined CPU
        signature (this pid PLUS every descendant found via a best-effort
        /proc walk) is unchanged across two consecutive passes. A CLI that
        forks a busy child while its own top-level process idles (the
        oracle-2 negative case) must NOT be confirmed -- the child's rising
        utime/stime changes the composite signature each pass.

        A declared-long-gate exemption (v2 §5.4) is intentionally NOT
        implemented: the wrapper does not run gates yet (receipt.oracles
        stays [], see wrapper.py's own contract), so there is no
        gate-running marker to exempt against.
        """
        out: dict[str, bool] = {}
        for tsf in states.values():
            for att in tsf.attempts:
                if att.state in TERMINAL_ATTEMPT_STATES:
                    continue
                aid = att.attempt_id
                quiet = log_quiet_seconds.get(aid)
                alive = pid_alive.get(aid, False)
                if not alive or quiet is None or quiet <= cfg.policy.stall_log_quiet_seconds:
                    self._stall_cache.pop(aid, None)
                    out[aid] = False
                    continue
                cpu = self._proc_cpu_snapshot(att.pid)
                prev = self._stall_cache.get(aid)
                out[aid] = prev is not None and cpu is not None and prev == cpu
                self._stall_cache[aid] = cpu
        return out

    @staticmethod
    def _read_proc_cpu(pid: int | None) -> str | None:
        if not pid:
            return None
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            parts = stat.split()
            return f"{parts[13]}:{parts[14]}"
        except Exception:
            return None

    @classmethod
    def _proc_children_map(cls) -> dict[int, list[int]]:
        """Best-effort single /proc walk: parent pid -> [child pids]."""
        by_parent: dict[int, list[int]] = {}
        try:
            entries = list(Path("/proc").iterdir())
        except OSError:
            return by_parent
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text(encoding="utf-8")
                parts = stat.split()
                cpid, ppid = int(parts[0]), int(parts[3])
            except (OSError, ValueError, IndexError):
                continue
            by_parent.setdefault(ppid, []).append(cpid)
        return by_parent

    @classmethod
    def _proc_cpu_snapshot(cls, pid: int | None) -> str | None:
        """Combined utime+stime signature for pid PLUS all its descendants
        (cheap best-effort /proc walk, ppid matching); None if pid itself is
        unreadable (process gone)."""
        if not pid:
            return None
        own = cls._read_proc_cpu(pid)
        if own is None:
            return None
        by_parent = cls._proc_children_map()
        parts_sig = [f"{pid}:{own}"]
        frontier = [pid]
        visited = {pid}
        while frontier:
            cur = frontier.pop()
            for child in by_parent.get(cur, []):
                if child in visited:
                    continue
                visited.add(child)
                child_cpu = cls._read_proc_cpu(child)
                if child_cpu is not None:
                    parts_sig.append(f"{child}:{child_cpu}")
                frontier.append(child)
        return "|".join(sorted(parts_sig))

    def _budget_remaining(self, cfg: ProjectConfig, states: dict[str, TaskStateFile]) -> float | None:
        if cfg.policy.max_cost is None:
            return None
        spent = 0.0
        for tsf in states.values():
            for att in tsf.attempts:
                if att.usage is not None and att.usage.cost is not None:
                    if cfg.policy.cost_currency is None or att.usage.currency == cfg.policy.cost_currency:
                        spent += att.usage.cost
        return cfg.policy.max_cost - spent

    def _history(self, project: str):
        merge_history: list[tuple[str, int, str]] = []
        carve_outcomes: list[dict] = []
        review_rejections_by_area: dict[str, int] = {}
        blocked_underspecified_count = 0
        try:
            events = list(storage.iter_events(project))
        except Exception:
            events = []
        for ev in events:
            if ev.type is EventType.MERGE_RECORDED and ev.task_id:
                units = len(ev.payload.get("progress_units", []) or [])
                source = ev.payload.get("source_kind", "review")
                merge_history.append((ev.task_id, units, source))
            elif ev.type is EventType.CARVE_OUTCOME:
                carve_outcomes.append(ev.payload)
            elif ev.type is EventType.REVIEW_RECORDED and ev.payload.get("result") == "rejected":
                area = ev.payload.get("area", "unknown")
                review_rejections_by_area[area] = review_rejections_by_area.get(area, 0) + 1
            elif ev.type is EventType.TASK_BLOCKED:
                blocker = ev.payload.get("blocker") or {}
                if blocker.get("type") == "contract":
                    blocked_underspecified_count += 1
        merge_history.reverse()  # most recent first
        return merge_history[:50], carve_outcomes[-20:], review_rejections_by_area, blocked_underspecified_count

    def _ratchet_already_open(self, project: str) -> bool:
        try:
            recent = list(storage.iter_events(project))[-500:]
        except Exception:
            return False
        return any(ev.type is EventType.SPEC_ATTENTION and ev.payload.get("reason") == "ratchet"
                   for ev in recent)

    def _roadmap_exhausted_open(self, project: str) -> bool:
        """P16 2026-07-15: mirrors _ratchet_already_open's convention (a
        recent-window dedup flag, not a true clear/reset state machine) --
        feeds ReconcileInput.roadmap_exhausted_open, which the carve
        trigger (module contract item 9) consults so it stops requesting
        more carvers once the carver itself has already reported the
        roadmap exhausted."""
        try:
            recent = list(storage.iter_events(project))[-500:]
        except Exception:
            return False
        return any(ev.type is EventType.SPEC_ATTENTION and ev.payload.get("reason") == "roadmap-exhausted"
                   for ev in recent)

    # -- event helpers -------------------------------------------------

    def _append_ev(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                   ev_type: EventType, payload: dict[str, Any], **kw) -> Event:
        ev = storage.append_and_apply(
            project, states, actor=Actor(ActorKind.TICK, "nyxloomd"), type=ev_type,
            payload=payload, **kw,
        )
        try:
            notify.notify_event(cfg, states, ev)
        except Exception:
            pass
        return ev

    def _transition(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                     task_id: str, to: TaskState, notes: str | None) -> Event:
        frm = states[task_id].state
        return self._append_ev(project, cfg, states, EventType.TASK_TRANSITIONED,
                                {"from": frm.value, "to": to.value, "notes": notes}, task_id=task_id)

    def _provider_pause(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                        route_id: str | None, task_id: str | None) -> list[Event]:
        out: list[Event] = []
        if route_id:
            self._provider_paused[route_id] = time.monotonic() + PROVIDER_PAUSE_SECONDS
        out.append(self._append_ev(project, cfg, states, EventType.PROVIDER_STATE_CHANGED,
                                    {"route_id": route_id, "state": "limited"}, task_id=task_id))
        out.append(self._append_ev(project, cfg, states, EventType.NEEDS_OPERATOR,
                                    {"route_id": route_id, "reason": "provider-limited"}, task_id=task_id))
        return out

    # -- execution map ---------------------------------------------------

    def _gate_hint(self, cfg: ProjectConfig) -> str:
        if not cfg.gates:
            return ""
        gate = sorted(cfg.gates.values(), key=lambda g: g.gate_id)[0]
        return " ".join(gate.argv)

    def _frontmatter_for(self, cfg: ProjectConfig, tsf: TaskStateFile):
        if not tsf.handoff_path:
            return None
        path = cfg.root / tsf.handoff_path
        if not path.exists():
            return None
        try:
            fm, _body = frontmatter.parse_handoff(path)
            return fm
        except Exception:
            return None

    def _lease_specs(self, cfg: ProjectConfig, fm) -> list[dict[str, Any]]:
        if fm is None:
            return []
        out = []
        for m in fm.effective_mutexes():
            mdef = cfg.mutexes.get(m)
            if mdef is None:
                continue
            out.append({"name": mdef.lease_name(cfg.project_id), "capacity": mdef.capacity})
        return out

    def _ensure_worktree(self, root: Path, branch: str, worktree_path: Path, default_branch: str) -> None:
        if worktree_path.exists():
            return
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        check = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", branch],
            capture_output=True, text=True,
        )
        if check.returncode == 0:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "add", str(worktree_path), branch],
                check=True, capture_output=True, text=True,
            )
        else:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "add", "-b", branch, str(worktree_path), default_branch],
                check=True, capture_output=True, text=True,
            )

    # -- carve automation (P16 2026-07-15) --------------------------------

    def _next_carve_seq(self, project: str) -> int:
        """Monotonic per-project carve sequence: count of past
        ATTEMPT_CREATED events whose attempt.role == 'carver', + 1.
        Recomputed from the event log every call (never in-memory-only, per
        this codebase's "residency is never authority" rule) so a daemon
        restart, or a prior carve that never produced a CARVE_OUTCOME
        (parse failure), still never collides with the next dispatch's
        branch/worktree/report path."""
        count = 0
        try:
            events = storage.iter_events(project)
        except Exception:
            events = []
        for ev in events:
            if ev.type is EventType.ATTEMPT_CREATED:
                att = ev.payload.get("attempt") or {}
                if att.get("role") == Role.CARVER.value:
                    count += 1
        return count + 1

    def _recent_review_follow_ups(self, project: str, limit: int = 10) -> list[tuple[str, str]]:
        """Carve source #1: recent REVIEW_RECORDED (task_id, result) pairs,
        newest first, capped at `limit`."""
        out: list[tuple[str, str]] = []
        try:
            events = list(storage.iter_events(project))
        except Exception:
            events = []
        for ev in reversed(events):
            if ev.type is EventType.REVIEW_RECORDED and ev.task_id:
                out.append((ev.task_id, ev.payload.get("result", "?")))
                if len(out) >= limit:
                    break
        return out

    def _carve_source_note_lines(self, cfg: ProjectConfig) -> list[str]:
        """Carve sources #2/#3 (backlog, roadmap/gap-analysis): name the
        conventional file paths the carver reads itself (same economy as the
        review packet's diff-only embedding: point, don't slurp). ProjectConfig
        has no 'product_sources' field today (config.py is frozen beyond the
        P16-authorized Policy fields), so this probes fixed conventional paths.
        B2 2026-07-16: prefer the nyxloom-trove layout (backlog.md/roadmap.md
        under the managed trove) and fall back to the legacy docs/ convention
        for un-migrated projects -- mirroring config.load()'s own trove-first/
        legacy-fallback resolution of nyxloom.toml."""
        lines = []
        # Backlog: trove-first, then legacy docs/BACKLOG.md.
        backlog = cfg.root / "nyxloom-trove" / "backlog.md"
        if not backlog.exists():
            backlog = cfg.root / "docs" / "BACKLOG.md"
        lines.append(
            f"- backlog: {backlog.relative_to(cfg.root)}"
            if backlog.exists()
            else "- backlog: none found (nyxloom-trove/backlog.md, docs/BACKLOG.md)"
        )
        # Roadmap: trove-first, then legacy docs/ROADMAP.md.
        roadmap = cfg.root / "nyxloom-trove" / "roadmap.md"
        if not roadmap.exists():
            roadmap = cfg.root / "docs" / "ROADMAP.md"
        if roadmap.exists():
            lines.append(f"- roadmap: {roadmap.relative_to(cfg.root)}")
        gap_files = sorted((cfg.root / "docs").glob("gap-*.md")) if (cfg.root / "docs").exists() else []
        if gap_files:
            lines.append("- gap analysis: " + ", ".join(
                str(p.relative_to(cfg.root)) for p in gap_files))
        if not roadmap.exists() and not gap_files:
            lines.append(
                "- roadmap/gap analysis: none found "
                "(nyxloom-trove/roadmap.md, docs/ROADMAP.md, docs/gap-*.md)"
            )
        return lines

    def _build_carve_packet(self, cfg: ProjectConfig, project: str, seq: int,
                             states: dict[str, TaskStateFile],
                             own_task_id: str | None = None) -> str:
        """The carve packet (mirrors the review packet's economy: point at
        sources, embed only what is cheap and structured). Written to the
        carve attempt's own packet/packet.md, exactly like LaunchReview's
        packet."""
        lines = [
            f"# Carve packet {seq}",
            "",
            "## Your role: CARVER",
            "",
            f"You are proposing NEW handoff packages for project '{project}'.",
            "Read the carve sources below, then write new lint-clean handoff",
            "file(s) under this project's handoff directory. Do NOT implement",
            "the work yourself -- you carve packages for other agents to pick",
            "up later.",
            "",
            "## Carve sources (v2 SS8)",
            "1. Review-derived follow-ups (recent REVIEW_RECORDED events):",
        ]
        follow_ups = self._recent_review_follow_ups(project)
        if follow_ups:
            for task_id, result in follow_ups:
                lines.append(f"   - {task_id}: {result}")
        else:
            lines.append("   - none recorded yet")
        lines.append("2. Backlog / 3. Roadmap and gap analysis:")
        lines.extend(f"   {line}" for line in self._carve_source_note_lines(cfg))
        lines.append(
            "4. Standing product goal: read this project's own README/"
            "CLAUDE.md and its nyxloom-trove/nyxloom.toml (or legacy "
            ".nyxloom/project.toml) for its product intent."
        )
        lines.append("")
        lines.append("## Current queue")
        queue_lines = [f"- {task_id}: {states[task_id].state.value}"
                       for task_id in sorted(states.keys()) if task_id != own_task_id]
        lines.extend(queue_lines if queue_lines else ["- (queue empty)"])
        lines.append("")
        authority = getattr(cfg.policy, "carve_authority", "branch")
        lines.append(f"## Carve authority: {authority}")
        if authority == "branch":
            lines.append(
                "You are on a dedicated carve branch. Commit your new handoff "
                "file(s) here. Do NOT merge -- a human admits this carve by "
                "merging the branch."
            )
        elif authority == "main":
            lines.append(
                "You are working directly on the project's checked-out branch. "
                "Commit your new handoff file(s) directly (lint-gated)."
            )
        else:
            lines.append(
                "Write your new handoff file(s) to disk WITHOUT committing "
                "(no git). They will be picked up on the next reconcile pass."
            )
        lines.append("")
        report_rel = f"{cfg.reports_dir}/CARVE-{seq}.md"
        lines.extend([
            "## REQUIRED OUTPUT CONTRACT",
            f"Write `{report_rel}` containing EXACTLY one JSON object (no",
            "markdown code fences) with these fields:",
            '  "carved": [{"id": "<new-task-id>", "why": "<one line>", '
            '"source_kind": "review|backlog|roadmap|product-goal"}, ...]',
            '  "review_reflection": "<what the recent reviews/merges revealed '
            'about quality/gaps>"',
            '  "headroom_estimate": <int -- how many more carve-able packages '
            "exist before ROADMAP_EXHAUSTED/SPEC_GAP>",
            '  "headroom_rationale": "<one paragraph: how you read the '
            'roadmap/backlog runway>"',
            '  "outcome": one of ' + ", ".join(sorted(_CARVE_OUTCOMES)),
            "Also print this exact JSON as your final output line.",
        ])
        return "\n".join(lines) + "\n"

    def _execute_carve_dispatch(self, project: str, cfg: ProjectConfig,
                                 states: dict[str, TaskStateFile],
                                 action: "reconcile.CarveDispatch") -> list[Event]:
        events: list[Event] = []

        # Defense in depth: reconcile.py's own trigger already requires a
        # healthy 'frontier-review' route before ever emitting CarveDispatch
        # (module contract item 9), but routes.toml could change between
        # planning and execution within the same pass. Never mint a
        # synthetic task / worktree we cannot actually dispatch into.
        routes_obj = config.Routes.load()
        review_routes = routes_obj.for_tier("frontier-review")
        if not review_routes:
            events.append(self._append_ev(
                project, cfg, states, EventType.NEEDS_OPERATOR,
                {"reason": "carve-no-route"}, task_id=None))
            return events

        seq = self._next_carve_seq(project)
        task_id = f"carve-{project}-{seq}"
        authority = getattr(cfg.policy, "carve_authority", "branch")

        if authority == "branch":
            branch = f"carve/{project}-{seq}"
            carve_cwd = cfg.root / cfg.worktree_root / branch
            self._ensure_worktree(cfg.root, branch, carve_cwd, cfg.default_branch)
            dispatch_branch = branch
        else:
            carve_cwd = cfg.root
            dispatch_branch = cfg.default_branch

        # Synthetic carve task: hosts the CARVER attempt so wrapper.py's
        # frozen load_state()/attempt_by_id() contract is satisfied (a
        # carve has no pre-existing task to attach to). See module
        # docstring for why ACTIVE (counts toward wip-cap like a review
        # attempt already does) and why SUPERSEDED is the eventual terminal
        # edge.
        tsf = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
            state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
            notes=f"carve seq={seq} authority={authority}",
        )
        events.append(self._append_ev(project, cfg, states, EventType.TASK_CREATED,
                                       {"statefile": tsf.to_dict()}, task_id=task_id))

        attempt_id = new_id("att")
        attempt_dir = paths.attempt_dir(project, attempt_id)
        packet_dir = attempt_dir / "packet"
        packet_dir.mkdir(parents=True, exist_ok=True)
        packet_text = self._build_carve_packet(cfg, project, seq, states, own_task_id=task_id)
        (packet_dir / "packet.md").write_text(packet_text, encoding="utf-8")

        route_def = review_routes[0]
        route_snap = Route(route_id=route_def.route_id, cli=route_def.cli, model=route_def.model,
                            variant=route_def.variant, effort=route_def.effort,
                            routes_rev=routes_obj.revision)
        attempt = Attempt(attempt_id=attempt_id, role=Role.CARVER, state=AttemptState.CREATED,
                           route=route_snap, started=utc_now(), worktree=str(carve_cwd),
                           branch=dispatch_branch if authority == "branch" else None)
        events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_CREATED,
                                       {"attempt": attempt.to_dict()}, task_id=task_id,
                                       attempt_id=attempt_id))

        gate_hint = self._gate_hint(cfg)
        receipt_path = str(attempt_dir / "receipt.json")
        argv, _prompt = adapters.build_dispatch(
            route_def, handoff_path=str(packet_dir / "packet.md"), worktree=str(carve_cwd),
            branch=dispatch_branch, task_id=task_id, gate_hint=gate_hint, receipt_path=receipt_path,
        )
        spec = wrapper.WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
            cwd=str(carve_cwd), log_path=str(attempt_dir / "attempt.log"),
            receipt_path=receipt_path, attempt_dir=str(attempt_dir), route_def=asdict(route_def),
        )
        pid = wrapper.launch_detached(spec)
        attempt.state = AttemptState.PREFLIGHTING
        attempt.pid = pid
        events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_PREFLIGHTED,
                                       {"attempt": attempt.to_dict()}, task_id=task_id,
                                       attempt_id=attempt_id))
        return events

    def _consume_carve_exit(self, project: str, cfg: ProjectConfig,
                             states: dict[str, TaskStateFile], task_id: str,
                             attempt_id: str) -> list[Event]:
        """P16 2026-07-15: role == CARVER branch of EmitAttemptExit (called
        from _execute BEFORE the FRONTIER_REVIEW/implementer branches).
        Parses the carver's REQUIRED OUTPUT CONTRACT file, persists the
        full CarveSummary for the dashboard, emits a typed-only
        CARVE_OUTCOME, raises headroom/roadmap-exhausted SPEC_ATTENTION and
        (branch authority only) a NEEDS_OPERATOR, then retires the
        synthetic carve task to SUPERSEDED (clears reconcile.py's carve
        slot)."""
        events: list[Event] = []
        tsf = states[task_id]
        attempt = tsf.attempt_by_id(attempt_id)
        m = re.match(r"^carve-.*-(\d+)$", task_id)
        seq = int(m.group(1)) if m else 0
        authority = getattr(cfg.policy, "carve_authority", "branch")

        worktree = Path(attempt.worktree) if attempt.worktree else cfg.root
        report_path = worktree / cfg.reports_dir / f"CARVE-{seq}.md"

        summary: CarveSummary | None = None
        if report_path.exists():
            try:
                data = json.loads(report_path.read_text(encoding="utf-8"))
                summary = CarveSummary.from_dict(data)
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                summary = None

        if summary is not None:
            carves_dir = paths.project_dir(project) / "carves"
            carves_dir.mkdir(parents=True, exist_ok=True)
            persisted = {"seq": seq, "timestamp": iso(utc_now())}
            persisted.update(summary.to_dict())
            (carves_dir / f"{seq}.json").write_text(
                json.dumps(persisted, sort_keys=True), encoding="utf-8")

            carved_ids = [c.get("id", "") for c in summary.carved]
            events.append(self._append_ev(
                project, cfg, states, EventType.CARVE_OUTCOME,
                {"seq": seq, "carved_ids": carved_ids, "outcome": summary.outcome,
                 "headroom_estimate": summary.headroom_estimate},
                task_id=task_id, attempt_id=attempt_id))

            if summary.headroom_estimate < cfg.policy.headroom_warn:
                events.append(self._append_ev(
                    project, cfg, states, EventType.SPEC_ATTENTION,
                    {"reason": "headroom-low",
                     "detail": f"{summary.headroom_estimate} packages left"},
                    task_id=task_id))
            if summary.outcome == "ROADMAP_EXHAUSTED":
                events.append(self._append_ev(
                    project, cfg, states, EventType.SPEC_ATTENTION,
                    {"reason": "roadmap-exhausted",
                     "detail": f"{summary.headroom_estimate} packages left"},
                    task_id=task_id))
            if authority == "branch":
                events.append(self._append_ev(
                    project, cfg, states, EventType.NEEDS_OPERATOR,
                    {"reason": "carve-ready", "carved_count": len(summary.carved),
                     "headroom_estimate": summary.headroom_estimate},
                    task_id=task_id))
        else:
            events.append(self._append_ev(
                project, cfg, states, EventType.NEEDS_OPERATOR,
                {"reason": "carve-parse-failed", "seq": seq}, task_id=task_id))

        events.append(self._append_ev(
            project, cfg, states, EventType.TASK_SUPERSEDED,
            {"from": states[task_id].state.value, "notes": "carve-consumed"},
            task_id=task_id))
        return events

    def _crosscheck_head_commit(self, cfg: ProjectConfig, task_id: str, receipt: Receipt) -> None:
        """P21 2026-07-16: never let a lying null head_commit read as "no
        work done" (live P93 lesson: a receipt claimed head_commit=null
        while feat/<task> actually held a real commit). If the receipt
        already reports a commit, trust it -- only a null/empty value gets
        cross-checked. Compares feat/<task_id> against cfg.default_branch
        in cfg.root (read-only: rev-parse only, never a write to the
        branch); a branch that is ahead of default gets its real HEAD
        recorded; a branch with no commits ahead, or that does not exist,
        still records null/none."""
        if receipt.head_commit:
            return
        branch = f"feat/{task_id}"
        branch_res = subprocess.run(
            ["git", "-C", str(cfg.root), "rev-parse", "--verify", branch],
            capture_output=True, text=True,
        )
        if branch_res.returncode != 0:
            return  # no such branch -- leave null/none
        default_res = subprocess.run(
            ["git", "-C", str(cfg.root), "rev-parse", "--verify", cfg.default_branch],
            capture_output=True, text=True,
        )
        if default_res.returncode != 0:
            return
        branch_sha = branch_res.stdout.strip()
        if branch_sha and branch_sha != default_res.stdout.strip():
            receipt.head_commit = branch_sha

    def _next_resume_n(self, attempt_dir: Path) -> int:
        n = 1
        while (attempt_dir / f"attempt.resume-{n}.log").exists() or (attempt_dir / f"spec.resume-{n}.json").exists():
            n += 1
        return n

    def _execute(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                 action: reconcile.Action) -> list[Event]:
        events: list[Event] = []

        if isinstance(action, reconcile.CreateTask):
            tsf = TaskStateFile(
                schema_version=storage.SCHEMA_VERSION, task_id=action.task_id, project=project,
                state=TaskState.CARVED, since=utc_now(), handoff_path=action.handoff_path,
            )
            events.append(self._append_ev(project, cfg, states, EventType.TASK_CREATED,
                                           {"statefile": tsf.to_dict()}, task_id=action.task_id))

        elif isinstance(action, reconcile.Transition):
            if action.to is TaskState.BLOCKED and action.blocker is not None:
                # P14 2026-07-15 item 4: a typed-blocker BLOCKED transition
                # (the INTERRUPTED silent-dead-end fix) emits TASK_BLOCKED,
                # not a plain TASK_TRANSITIONED, so tsf.blocker gets set.
                frm = states[action.task_id].state
                events.append(self._append_ev(
                    project, cfg, states, EventType.TASK_BLOCKED,
                    {"from": frm.value, "blocker": action.blocker.to_dict(), "notes": action.notes},
                    task_id=action.task_id))
            elif states[action.task_id].state is action.to:
                # Race-tolerant no-op guard: a transition whose target
                # equals the current state is a no-op, not an error. This
                # arises when two planning passes computed the same edge
                # from a shared snapshot (e.g. both saw CARVED and planned
                # CARVED->QUEUED) and the first already applied it — the
                # classic symptom being the QUEUED->QUEUED TICK_ERROR under
                # a transient double-dispatcher. Skip silently rather than
                # letting check_task_transition raise (which surfaces as a
                # TICK_ERROR and pollutes the event log). Root singleton
                # enforcement is P19 (ciu-managed container); this keeps the
                # planner idempotent regardless.
                pass
            else:
                events.append(self._transition(project, cfg, states, action.task_id, action.to, action.notes))

        elif isinstance(action, reconcile.DispatchImplementer):
            task_id = action.task_id
            tsf = states[task_id]
            branch = f"feat/{task_id}"
            worktree_path = cfg.root / cfg.worktree_root / branch
            self._ensure_worktree(cfg.root, branch, worktree_path, cfg.default_branch)

            routes_obj = config.Routes.load()
            route_def = routes_obj.routes[action.route_id]
            attempt_id = new_id("att")
            route_snap = Route(route_id=route_def.route_id, cli=route_def.cli, model=route_def.model,
                                variant=route_def.variant, effort=route_def.effort,
                                routes_rev=routes_obj.revision)
            attempt = Attempt(attempt_id=attempt_id, role=Role.IMPLEMENTER, state=AttemptState.CREATED,
                               route=route_snap, started=utc_now(), worktree=str(worktree_path),
                               branch=branch)
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_CREATED,
                                           {"attempt": attempt.to_dict()}, task_id=task_id,
                                           attempt_id=attempt_id))

            attempt_dir = paths.attempt_dir(project, attempt_id)
            fm_obj = self._frontmatter_for(cfg, tsf)
            gate_hint = self._gate_hint(cfg)
            receipt_path = str(attempt_dir / "receipt.json")
            argv, _prompt = adapters.build_dispatch(
                route_def, handoff_path=tsf.handoff_path or "", worktree=str(worktree_path),
                branch=branch, task_id=task_id, gate_hint=gate_hint, receipt_path=receipt_path,
            )
            spec = wrapper.WrapperSpec(
                project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
                cwd=str(worktree_path), log_path=str(attempt_dir / "attempt.log"),
                receipt_path=receipt_path, attempt_dir=str(attempt_dir),
                route_def=asdict(route_def), leases=self._lease_specs(cfg, fm_obj),
            )
            pid = wrapper.launch_detached(spec)
            attempt.state = AttemptState.PREFLIGHTING
            attempt.pid = pid
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_PREFLIGHTED,
                                           {"attempt": attempt.to_dict()}, task_id=task_id,
                                           attempt_id=attempt_id))
            events.append(self._transition(project, cfg, states, task_id, TaskState.ACTIVE, None))

        elif isinstance(action, reconcile.ResumeAttempt):
            task_id = action.task_id
            tsf = states[task_id]
            attempt = tsf.attempt_by_id(action.attempt_id)
            attempt_dir = paths.attempt_dir(project, action.attempt_id)
            resume_n = self._next_resume_n(attempt_dir)
            routes_obj = config.Routes.load()
            route_def = routes_obj.routes[attempt.route.route_id]
            worktree = attempt.worktree or str(cfg.root)
            prompt = f"Resume {task_id} attempt {action.attempt_id} in {worktree}"
            argv = adapters.build_resume(route_def, session=attempt.session_handle,
                                          worktree=worktree, prompt=prompt)
            fm_obj = self._frontmatter_for(cfg, tsf)
            spec = wrapper.WrapperSpec(
                project=project, task_id=task_id, attempt_id=action.attempt_id, argv=argv,
                cwd=worktree, log_path=str(attempt_dir / f"attempt.resume-{resume_n}.log"),
                receipt_path=str(attempt_dir / "receipt.json"), attempt_dir=str(attempt_dir),
                route_def=asdict(route_def), leases=self._lease_specs(cfg, fm_obj),
            )
            pid = wrapper.launch_detached(spec)
            attempt.state = AttemptState.RUNNING
            attempt.pid = pid
            # P14 2026-07-15 item 5 (resume bookkeeping drift): refresh the
            # log path to the NEW resume log right here, rather than leaving
            # it stale until the wrapper's own later ATTEMPT_STARTED lands --
            # a stale log_path made log_quiet_seconds watch a dead file
            # while the live resumed process went unwatched.
            attempt.log_path = spec.log_path
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_RESUMED,
                                           {"attempt": attempt.to_dict()}, task_id=task_id,
                                           attempt_id=action.attempt_id))

        elif isinstance(action, reconcile.InterruptAttempt):
            tsf = states[action.task_id]
            attempt_dir = paths.attempt_dir(project, action.attempt_id)
            # P14 2026-07-15 (discovered building the oracle-1 end-to-end
            # hang-detection test): signal the WRAPPER itself first -- its
            # own installed handler forwards SIGTERM to the child's process
            # group AND classifies the resulting exit as 'interrupted' (see
            # wrapper.py's contract and its own real-signal tests, which
            # signal wrapper_pid directly). Signaling child.pid's pgid
            # alone bypasses the wrapper's handler entirely: the child dies
            # from an unforwarded signal the wrapper never observed, so it
            # falls through to plain log-tail classification and reports
            # 'error', not 'interrupted' -- the confirmed-stall pipeline
            # would then silently retry instead of ever reaching INTERRUPTED.
            signaled = False
            wrapper_pid_file = attempt_dir / "wrapper.pid"
            if wrapper_pid_file.exists():
                try:
                    wpid = int(wrapper_pid_file.read_text(encoding="utf-8").strip())
                    os.kill(wpid, signal.SIGTERM)
                    signaled = True
                except (ValueError, ProcessLookupError, OSError):
                    pass
            if not signaled:
                # Belt and braces: the wrapper may already be gone (crashed)
                # while the child it spawned is still alive -- kill the
                # child's process group directly as a fallback.
                child_pid_file = attempt_dir / "child.pid"
                if child_pid_file.exists():
                    try:
                        child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
                        pgid = os.getpgid(child_pid)
                        os.killpg(pgid, signal.SIGTERM)
                    except (ValueError, ProcessLookupError, OSError):
                        pass
            # No event: the wrapper emits ATTEMPT_INTERRUPTED itself on exit.

        elif isinstance(action, reconcile.MarkInterrupted):
            tsf = states[action.task_id]
            attempt = tsf.attempt_by_id(action.attempt_id)
            attempt.state = AttemptState.INTERRUPTED
            attempt.ended = utc_now()
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_INTERRUPTED,
                                           {"attempt": attempt.to_dict()}, task_id=action.task_id,
                                           attempt_id=action.attempt_id))

        elif isinstance(action, reconcile.MarkStalled):
            # P14 2026-07-15 item 2: make a tier-2-confirmed stall visible.
            # The process is still running (not ended) -- just flagged.
            tsf = states[action.task_id]
            attempt = tsf.attempt_by_id(action.attempt_id)
            attempt.state = AttemptState.STALLED
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_STALLED,
                                           {"attempt": attempt.to_dict()}, task_id=action.task_id,
                                           attempt_id=action.attempt_id))

        elif isinstance(action, reconcile.StallCheck):
            pass  # _confirm_stall cache already updated during input build

        elif isinstance(action, reconcile.EmitAttemptExit):
            task_id = action.task_id
            tsf = states[task_id]
            attempt = tsf.attempt_by_id(action.attempt_id)
            attempt_dir = paths.attempt_dir(project, action.attempt_id)
            receipt_data = json.loads((attempt_dir / "receipt.json").read_text(encoding="utf-8"))
            receipt = Receipt.from_dict(receipt_data)
            # P21 2026-07-16: git state is truth, receipts lie (live P93
            # lesson -- a receipt reported head_commit=null/files_touched=[]
            # while the branch actually held a real commit). Cross-check
            # before the receipt is used/logged below.
            self._crosscheck_head_commit(cfg, task_id, receipt)
            if attempt.state != AttemptState.EXITED:
                # Wrapper died before emitting its own exit event — heal it.
                attempt.state = AttemptState.EXITED
                attempt.ended = utc_now()
                attempt.receipt = receipt
                events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_EXITED,
                                               {"attempt": attempt.to_dict()}, task_id=task_id,
                                               attempt_id=action.attempt_id))
            # else: wrapper already recorded the exit; only the task
            # transition below remains (idempotent healing).

            result = receipt.result

            if attempt.role == Role.CARVER:
                # P16 2026-07-15: consume the carver's REQUIRED OUTPUT
                # CONTRACT (a CarveSummary file, not the wrapper's own
                # process-level Receipt above) -- see _consume_carve_exit
                # and the module docstring's carve-automation section.
                events.extend(self._consume_carve_exit(project, cfg, states, task_id, action.attempt_id))
                return events

            if attempt.role == Role.FRONTIER_REVIEW:
                # 2026-07-15: consume the REVIEW receipt (was unmapped —
                # live deadlock). merge_mode=manual: MERGE_READY is declared,
                # never auto-merged (SPEC §7).
                events.append(self._append_ev(
                    project, cfg, states, EventType.REVIEW_RECORDED,
                    {"result": result.value}, task_id=task_id,
                    attempt_id=action.attempt_id, wave_id=attempt.wave_id))
                if result is ReceiptResult.DONE:
                    events.append(self._transition(project, cfg, states, task_id,
                                                    TaskState.MERGE_READY, None))
                else:
                    events.append(self._transition(project, cfg, states, task_id,
                                                    TaskState.REVIEW_REJECTED,
                                                    f"review receipt: {result.value}"))
                return events

            if result is ReceiptResult.DONE:
                events.append(self._transition(project, cfg, states, task_id,
                                                TaskState.AWAITING_REVIEW, None))
            elif result is ReceiptResult.BLOCKED:
                blocker = Blocker(type=BlockerType.CONTRACT, unblock_condition="triage BLOCKED reason",
                                   detail=(receipt.blocked_reason or "")[:200])
                events.append(self._append_ev(project, cfg, states, EventType.TASK_BLOCKED,
                                               {"from": states[task_id].state.value,
                                                "blocker": blocker.to_dict()}, task_id=task_id))
            elif result is ReceiptResult.LIMIT:
                events.append(self._transition(project, cfg, states, task_id, TaskState.QUEUED, None))
                events.extend(self._provider_pause(project, cfg, states, attempt.route.route_id, task_id))
            elif result is ReceiptResult.ERROR:
                attempts_used = sum(
                    1 for a in states[task_id].attempts
                    if a.receipt is not None and a.receipt.result != ReceiptResult.LIMIT
                )
                if attempts_used < cfg.policy.max_attempts_per_task:
                    events.append(self._transition(project, cfg, states, task_id, TaskState.QUEUED, None))
                else:
                    blocker = Blocker(type=BlockerType.ENVIRONMENT,
                                       unblock_condition="triage BLOCKED reason",
                                       detail="attempts exhausted")
                    events.append(self._append_ev(project, cfg, states, EventType.TASK_BLOCKED,
                                                   {"from": states[task_id].state.value,
                                                    "blocker": blocker.to_dict()}, task_id=task_id))

        elif isinstance(action, reconcile.ProviderPause):
            events.extend(self._provider_pause(project, cfg, states, action.route_id, action.task_id))

        elif isinstance(action, reconcile.OpenWave):
            wave_id = new_id("wave")
            events.append(self._append_ev(project, cfg, states, EventType.WAVE_OPENED,
                                           {"task_ids": list(action.task_ids)}, wave_id=wave_id))

        elif isinstance(action, reconcile.LaunchReview):
            wave_id = action.wave_id
            attempt_id = new_id("att")
            attempt_dir = paths.attempt_dir(project, attempt_id)
            packet_dir = attempt_dir / "packet"
            packet_dir.mkdir(parents=True, exist_ok=True)
            packet_lines = [
                "# Review packet",
                "",
                "## Your role: INDEPENDENT FRONTIER REVIEWER (merge gate)",
                "",
                "You are reviewing another agent's committed work — you did",
                "not write it. For each task below (2026-07-15 role contract;",
                "the first live review wrote implementer artifacts instead):",
                "1. Read the handoff contract, then the diff (<task>.diff",
                "   here, or `git diff main...feat/<task>` in the repo).",
                "2. Verify actual git state — git state is truth, receipts",
                f"   lie: run `git log {cfg.default_branch}..feat/<task>` and",
                "   `git status` in the worktree. Do NOT trust the receipt's",
                "   `head_commit` / `files_touched` / `oracles` fields: they",
                "   have been observed null/empty even when real work was",
                "   committed (live P93 lesson). If the worktree holds",
                "   UNCOMMITTED changes (see the UNCOMMITTED section below,",
                "   per task), review them too — the implementer's commit",
                "   discipline is not guaranteed; do not treat uncommitted",
                "   work as nonexistent.",
                "3. Adversarially verify against the handoff's oracles:",
                "   hollow tests, overclaimed evidence, missing handoff",
                "   requirements, edge-case gaps, env-specific claims.",
                "4. Re-run the handoff's declared gate yourself; never trust",
                "   a report's pasted output.",
                "5. Small defects: fix them YOURSELF, commit to the task's",
                "   feat/ branch. Large/architectural defects: REJECT.",
                "6. Write topos/handoff/reports/<task>-REVIEW.md: findings,",
                "   what you fixed, verdict + reasoning. Commit it to the",
                "   feat/ branch (NOT main). Do NOT merge. Do NOT write the",
                "   implementer's LOG/REPORT.",
                "7. VERDICT signalling (drives the pipeline): if EVERY task",
                "   here is approved, finish normally. If ANY task must be",
                "   rejected, make your FINAL output line exactly:",
                "   `BLOCKED: rejected — <task ids and one-line reasons>`.",
                "",
            ]
            for t in action.task_ids:
                tsf_t = states.get(t)
                branch = f"feat/{t}"
                diff_res = subprocess.run(
                    ["git", "-C", str(cfg.root), "diff", f"{cfg.default_branch}...{branch}"],
                    capture_output=True, text=True,
                )
                stat_res = subprocess.run(
                    ["git", "-C", str(cfg.root), "diff", "--stat", f"{cfg.default_branch}...{branch}"],
                    capture_output=True, text=True,
                )
                (packet_dir / f"{t}.diff").write_text(diff_res.stdout, encoding="utf-8")
                packet_lines.append(f"## {t}")
                if tsf_t is not None and tsf_t.handoff_path:
                    packet_lines.append(f"- handoff: {tsf_t.handoff_path}")
                packet_lines.append(f"### COMMITTED ({cfg.default_branch}...{branch})")
                packet_lines.append(f"- diff stat:\n{stat_res.stdout}")
                packet_lines.append("")

                # P21 2026-07-16: also capture UNCOMMITTED worktree state --
                # "experience shows the commit requirement is often not
                # honored" (user directive), so a committed-only diff misses
                # real work still sitting in the task's worktree. Same
                # worktree derivation DispatchImplementer uses (~845):
                # cfg.root / cfg.worktree_root / feat/<task>.
                worktree_path = cfg.root / cfg.worktree_root / branch
                packet_lines.append(
                    "### UNCOMMITTED (worktree — may be lost on teardown; REVIEW IT)"
                )
                if not worktree_path.exists():
                    packet_lines.append(
                        f"- worktree {worktree_path} is absent (already torn down); "
                        "no uncommitted state could be captured."
                    )
                else:
                    status_res = subprocess.run(
                        ["git", "-C", str(worktree_path), "status", "--porcelain"],
                        capture_output=True, text=True,
                    )
                    unstaged_res = subprocess.run(
                        ["git", "-C", str(worktree_path), "diff"],
                        capture_output=True, text=True,
                    )
                    staged_res = subprocess.run(
                        ["git", "-C", str(worktree_path), "diff", "--cached"],
                        capture_output=True, text=True,
                    )
                    if not (status_res.stdout.strip() or unstaged_res.stdout.strip()
                            or staged_res.stdout.strip()):
                        packet_lines.append("- clean: no uncommitted changes in the worktree.")
                    else:
                        packet_lines.append(f"- git status --porcelain:\n{status_res.stdout}")
                        packet_lines.append(f"- unstaged diff:\n{unstaged_res.stdout}")
                        packet_lines.append(f"- staged diff (--cached):\n{staged_res.stdout}")
                packet_lines.append("")
            (packet_dir / "packet.md").write_text("\n".join(packet_lines), encoding="utf-8")

            routes_obj = config.Routes.load()
            review_routes = routes_obj.for_tier("frontier-review")
            route_def = review_routes[0]
            route_snap = Route(route_id=route_def.route_id, cli=route_def.cli, model=route_def.model,
                                variant=route_def.variant, effort=route_def.effort,
                                routes_rev=routes_obj.revision)
            first_task = action.task_ids[0] if action.task_ids else None
            attempt = Attempt(attempt_id=attempt_id, role=Role.FRONTIER_REVIEW,
                               state=AttemptState.CREATED, route=route_snap, started=utc_now(),
                               wave_id=wave_id)
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_CREATED,
                                           {"attempt": attempt.to_dict()}, task_id=first_task,
                                           attempt_id=attempt_id, wave_id=wave_id))

            gate_hint = self._gate_hint(cfg)
            receipt_path = str(attempt_dir / "receipt.json")
            argv, _prompt = adapters.build_dispatch(
                route_def, handoff_path=str(packet_dir / "packet.md"), worktree=str(cfg.root),
                branch=cfg.default_branch, task_id=first_task or wave_id or "review",
                gate_hint=gate_hint, receipt_path=receipt_path,
            )
            spec = wrapper.WrapperSpec(
                project=project, task_id=first_task or wave_id or "review", attempt_id=attempt_id,
                argv=argv, cwd=str(cfg.root), log_path=str(attempt_dir / "attempt.log"),
                receipt_path=receipt_path, attempt_dir=str(attempt_dir), route_def=asdict(route_def),
            )
            pid = wrapper.launch_detached(spec)
            attempt.state = AttemptState.PREFLIGHTING
            attempt.pid = pid
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_PREFLIGHTED,
                                           {"attempt": attempt.to_dict()}, task_id=first_task,
                                           attempt_id=attempt_id, wave_id=wave_id))

        elif isinstance(action, reconcile.SpecAttention):
            events.append(self._append_ev(project, cfg, states, EventType.SPEC_ATTENTION,
                                           {"reason": action.reason, "detail": action.detail},
                                           task_id=action.task_id))

        elif isinstance(action, reconcile.CarveDispatch):
            events.extend(self._execute_carve_dispatch(project, cfg, states, action))

        else:
            raise ValueError(f"unhandled action type: {type(action)!r}")

        return events

    # -- HTTP / SSE --------------------------------------------------------

    def _chosen_port(self) -> int:
        ports = []
        for root in self.registry.values():
            try:
                cfg = config.ProjectConfig.load(root)
                ports.append(cfg.policy.http_port)
            except Exception:
                continue
        return min(ports) if ports else DEFAULT_HTTP_PORT

    def _start_http(self) -> None:
        port = self._chosen_port()
        daemon = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
                pass

            def do_GET(self) -> None:  # noqa: N802
                try:
                    daemon._handle_get(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    try:
                        body = str(exc).encode("utf-8")
                        self.send_response(500)
                        self.send_header("Content-Type", "text/plain")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception:
                        pass

            def do_POST(self) -> None:  # noqa: N802
                try:
                    daemon._handle_post(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    try:
                        body = str(exc).encode("utf-8")
                        self.send_response(500)
                        self.send_header("Content-Type", "text/plain")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception:
                        pass

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        self.http_port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        t.start()
        self._http_thread = t

    def _stop_http(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        if self._http_thread is not None:
            self._http_thread.join(timeout=5)
            self._http_thread = None

    @staticmethod
    def _send_json(handler: http.server.BaseHTTPRequestHandler, code: int, body: bytes) -> None:
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _handle_get(self, handler: http.server.BaseHTTPRequestHandler) -> None:
        parsed = urllib.parse.urlparse(handler.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            handler.send_response(302)
            handler.send_header("Location", "/www/index.html")
            handler.end_headers()
            return

        if path in _CONFIG_POST_PATHS:
            self._send_json(handler, 405, b'{"error":"method not allowed"}')
            return

        if path.startswith("/www/"):
            self._serve_www(handler, path[len("/www/"):])
            return

        if path == "/api/projects":
            self._send_json(handler, 200, json.dumps(self._api_projects()).encode("utf-8"))
            return

        if path == "/api/tasks":
            project = qs.get("project", [None])[0]
            if project is None or project not in self.registry:
                self._send_json(handler, 404, b'{"error":"not found"}')
                return
            states = storage.list_states(project)
            body = json.dumps([s.to_dict() for s in states.values()]).encode("utf-8")
            self._send_json(handler, 200, body)
            return

        m = re.match(r"^/api/task/([^/]+)/([^/]+)$", path)
        if m:
            project, task_id = m.group(1), m.group(2)
            tsf = storage.load_state(project, task_id) if project in self.registry else None
            if tsf is None:
                self._send_json(handler, 404, b'{"error":"not found"}')
                return
            self._send_json(handler, 200, json.dumps(tsf.to_dict()).encode("utf-8"))
            return

        m = re.match(r"^/api/log/([^/]+)/([^/]+)$", path)
        if m:
            project, attempt_id = m.group(1), m.group(2)
            try:
                tail = int(qs.get("tail", ["65536"])[0])
            except ValueError:
                tail = 65536
            self._serve_log(handler, project, attempt_id, tail)
            return

        m = re.match(r"^/api/drilldown/([^/]+)/([^/]+)$", path)
        if m:
            project, attempt_id = m.group(1), m.group(2)
            try:
                tail = int(qs.get("tail", ["65536"])[0])
            except ValueError:
                tail = 65536
            self._serve_drilldown(handler, project, attempt_id, tail)
            return

        if path == "/api/events":
            project = qs.get("project", [None])[0]
            try:
                since = int(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0
            if project is None or project not in self.registry:
                self._send_json(handler, 404, b'{"error":"not found"}')
                return
            evs = list(storage.iter_events(project, since=since))[:500]
            body = json.dumps([e.to_dict() for e in evs]).encode("utf-8")
            self._send_json(handler, 200, body)
            return

        if path == "/api/stream":
            # No ?project= (e.g. live.html's bare EventSource): default to
            # the first registered project instead of closing the stream.
            project = qs.get("project", [None])[0] or next(iter(sorted(self.registry)), None)
            self._serve_sse(handler, project)
            return

        self._send_json(handler, 404, b'{"error":"not found"}')

    # -- HTTP config mutation endpoints (P15 2026-07-15) -----------------

    def _append_ui_event(self, project: str, cfg: ProjectConfig | None,
                          states: dict[str, TaskStateFile], ev_type: EventType,
                          payload: dict[str, Any], **kw) -> Event:
        """Same append+apply+notify shape as `_append_ev`, but with actor
        OPERATOR 'ui' — the audited identity for every HTTP config-mutation
        endpoint (module docstring's P15 CONFIG-mutation amendment).
        `_append_ev` is deliberately NOT reused here: it hardcodes actor
        TICK/'nyxloomd', which is correct for reconcile-pass-triggered
        events but wrong for operator-initiated UI writes."""
        ev = storage.append_and_apply(
            project, states, actor=Actor(ActorKind.OPERATOR, "ui"), type=ev_type,
            payload=payload, **kw,
        )
        if cfg is not None:
            try:
                notify.notify_event(cfg, states, ev)
            except Exception:
                pass
        return ev

    def _read_json_body(self, handler: http.server.BaseHTTPRequestHandler) -> dict | None:
        """Read+parse the request body; None (caller sends 400) on any
        malformed input, including a non-object JSON value."""
        try:
            length = int(handler.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        raw = handler.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return body if isinstance(body, dict) else None

    def _handle_post(self, handler: http.server.BaseHTTPRequestHandler) -> None:
        parsed = urllib.parse.urlparse(handler.path)
        path = parsed.path
        body = self._read_json_body(handler)
        if body is None:
            self._send_json(handler, 400, b'{"error":"malformed json body"}')
            return

        if path == "/api/config/policy":
            self._post_config_policy(handler, body)
            return
        if path == "/api/config/pause":
            self._post_config_pause(handler, body)
            return
        if path == "/api/config/tier":
            self._post_config_tier(handler, body)
            return
        if path == "/api/decision/reply":
            self._post_decision_reply(handler, body)
            return

        self._send_json(handler, 404, b'{"error":"not found"}')

    def _post_decision_reply(self, handler: http.server.BaseHTTPRequestHandler, body: dict) -> None:
        """P18: drive the decision-chat bridge from the UI (decisions.html),
        the same advance_chat() path the feedback-channel router uses."""
        decision_id = body.get("decision_id")
        text = body.get("text")
        if not isinstance(decision_id, str) or not decision_id:
            self._send_json(handler, 400, b'{"error":"missing decision_id"}')
            return
        if not isinstance(text, str) or not text.strip():
            self._send_json(handler, 400, b'{"error":"missing text"}')
            return

        target = decision_chat.find_project_for_decision(self.registry, decision_id)
        if target is None:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        project, cfg = target

        try:
            decision_chat.advance_chat(cfg, project, decision_id, text.strip())
        except Exception as exc:
            self._send_json(handler, 500, json.dumps({"error": repr(exc)[:200]}).encode("utf-8"))
            return

        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps({"ok": True}).encode("utf-8"))

    def _post_config_policy(self, handler: http.server.BaseHTTPRequestHandler, body: dict) -> None:
        project = body.get("project")
        key = body.get("key")
        value = body.get("value")

        if project not in self.registry:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return

        if key == "carve_authority":
            # P16 2026-07-15: the one STRING-valued editable Policy key.
            # Same surgical-edit + CONFIG_CHANGED contract as the numeric
            # keys below, but validated separately (str, fixed enum) and
            # written via a json.dumps-quoted value so update_project_
            # policy's plain f-string interpolation still yields valid TOML
            # (`carve_authority = "branch"`) without touching that frozen
            # (P15-authored) function at all.
            if not isinstance(value, str) or value not in _CARVE_AUTHORITIES:
                self._send_json(handler, 400, json.dumps(
                    {"error": f"carve_authority must be one of {sorted(_CARVE_AUTHORITIES)}"}
                ).encode("utf-8"))
                return
            root = self.registry[project]
            try:
                cfg = config.ProjectConfig.load(root)
            except Exception:
                self._send_json(handler, 404, b'{"error":"not found"}')
                return
            old_value = getattr(cfg.policy, key)
            try:
                config.update_project_policy(root, {key: json.dumps(value)})
            except ValueError as exc:
                self._send_json(handler, 400, json.dumps({"error": str(exc)}).encode("utf-8"))
                return
            states = storage.list_states(project)
            self._append_ui_event(project, cfg, states, EventType.CONFIG_CHANGED,
                                   {"scope": "policy", "key": key, "old": old_value, "new": value})
            render.render_after_event(self.registry)
            self._send_json(handler, 200, json.dumps({"ok": True}).encode("utf-8"))
            return

        if key not in _POLICY_BOUNDS:
            self._send_json(handler, 400,
                             json.dumps({"error": f"unknown policy key: {key!r}"}).encode("utf-8"))
            return
        if not isinstance(value, int) or isinstance(value, bool):
            self._send_json(handler, 400, b'{"error":"value must be an integer"}')
            return
        lo, hi = _POLICY_BOUNDS[key]
        if not (lo <= value <= hi):
            self._send_json(handler, 400, json.dumps(
                {"error": f"{key} must be within [{lo}, {hi}]"}).encode("utf-8"))
            return

        root = self.registry[project]
        try:
            cfg = config.ProjectConfig.load(root)
        except Exception:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        old_value = getattr(cfg.policy, key)

        try:
            config.update_project_policy(root, {key: value})
        except ValueError as exc:
            self._send_json(handler, 400, json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        states = storage.list_states(project)
        self._append_ui_event(project, cfg, states, EventType.CONFIG_CHANGED,
                               {"scope": "policy", "key": key, "old": old_value, "new": value})
        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps({"ok": True}).encode("utf-8"))

    def _post_config_pause(self, handler: http.server.BaseHTTPRequestHandler, body: dict) -> None:
        project = body.get("project")
        mode = body.get("mode")

        if project not in self.registry:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        if mode not in _PAUSE_MODES:
            self._send_json(handler, 400,
                             json.dumps({"error": f"unknown mode: {mode!r}"}).encode("utf-8"))
            return

        try:
            cfg: ProjectConfig | None = config.ProjectConfig.load(self.registry[project])
        except Exception:
            cfg = None
        states = storage.list_states(project)
        flag = paths.pause_flag(project)

        if mode == "run":
            flag.unlink(missing_ok=True)
            self._append_ui_event(project, cfg, states, EventType.PAUSE_CLEARED, {})
        else:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(mode, encoding="utf-8")
            self._append_ui_event(project, cfg, states, EventType.PAUSE_SET, {"mode": mode})

        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps({"ok": True, "mode": mode}).encode("utf-8"))

    def _post_config_tier(self, handler: http.server.BaseHTTPRequestHandler, body: dict) -> None:
        tier = body.get("tier")
        route_ids = body.get("routes")

        if not isinstance(tier, str) or not tier:
            self._send_json(handler, 400, b'{"error":"missing tier"}')
            return
        if not isinstance(route_ids, list) or not all(isinstance(r, str) for r in route_ids):
            self._send_json(handler, 400, b'{"error":"routes must be a list of strings"}')
            return

        try:
            routes_obj = config.Routes.load()
        except Exception:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        if tier not in routes_obj.tiers:
            self._send_json(handler, 404,
                             json.dumps({"error": f"unknown tier: {tier}"}).encode("utf-8"))
            return
        unknown = [r for r in route_ids if r not in routes_obj.routes]
        if unknown:
            self._send_json(handler, 400,
                             json.dumps({"error": f"unknown route id(s): {unknown}"}).encode("utf-8"))
            return

        old_routes = list(routes_obj.tiers.get(tier, []))
        try:
            config.update_routes({tier: route_ids})
        except ValueError as exc:
            self._send_json(handler, 400, json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        # routes.toml is a single shared state file (not project-scoped), so
        # the audit trail is appended to EVERY registered project's own
        # event log -- each project can see routing changes that affect it.
        for project, root in self.registry.items():
            try:
                cfg: ProjectConfig | None = config.ProjectConfig.load(root)
            except Exception:
                cfg = None
            states = storage.list_states(project)
            self._append_ui_event(project, cfg, states, EventType.CONFIG_CHANGED,
                                   {"scope": "routes", "key": tier, "old": old_routes, "new": route_ids})

        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps({"ok": True}).encode("utf-8"))

    def _api_projects(self) -> list[dict]:
        out = []
        for project, root in sorted(self.registry.items()):
            entry = {"project_id": project, "root": str(root)}
            try:
                cfg = config.ProjectConfig.load(root)
                entry["default_branch"] = cfg.default_branch
            except Exception:
                pass
            out.append(entry)
        return out

    def _serve_www(self, handler: http.server.BaseHTTPRequestHandler, rel: str) -> None:
        www = paths.www_dir().resolve()
        target = (www / rel).resolve()
        if not target.is_relative_to(www) or not target.is_file():
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        data = target.read_bytes()
        ctype = "text/html; charset=utf-8" if target.suffix in (".html", ".htm") else "application/octet-stream"
        handler.send_response(200)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _serve_log(self, handler: http.server.BaseHTTPRequestHandler, project: str,
                   attempt_id: str, tail: int) -> None:
        if project not in self.registry:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        try:
            cfg = config.ProjectConfig.load(self.registry[project])
        except Exception:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        log_path = paths.attempt_dir(project, attempt_id) / "attempt.log"
        if not log_path.exists():
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        data = log_path.read_bytes()
        if tail > 0 and len(data) > tail:
            data = data[-tail:]
        text = data.decode("utf-8", errors="replace")
        redacted = cfg.redact(text)
        body = redacted.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _serve_drilldown(self, handler: http.server.BaseHTTPRequestHandler, project: str,
                          attempt_id: str, tail: int) -> None:
        """P22 2026-07-16: read-only agent drilldown (live attach). Tail
        the raw log, RENDER it (render.render_transcript — assistant text
        deltas + tool names, never raw JSON), and ONLY THEN redact the
        rendered text (see render.render_drilldown_page's docstring for
        why this order, not /api/log's redact-then-serve order, is
        required) — READ-ONLY, no mutating control anywhere on the
        returned page."""
        if project not in self.registry:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        try:
            cfg = config.ProjectConfig.load(self.registry[project])
        except Exception:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        log_path = paths.attempt_dir(project, attempt_id) / "attempt.log"
        if not log_path.exists():
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        data = log_path.read_bytes()
        if tail > 0 and len(data) > tail:
            data = data[-tail:]
        text = data.decode("utf-8", errors="replace")
        transcript = render.render_transcript(text)
        redacted = cfg.redact(transcript)
        page = render.render_drilldown_page(project, attempt_id, redacted)
        body = page.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _serve_sse(self, handler: http.server.BaseHTTPRequestHandler, project: str | None) -> None:
        if project is None or project not in self.registry:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "close")
        handler.end_headers()
        since = 0
        last_heartbeat = time.monotonic()
        try:
            while not self._stop_event.is_set():
                evs = list(storage.iter_events(project, since=since))
                for ev in evs:
                    since = ev.sequence
                    chunk = f"data: {json.dumps(ev.to_dict())}\n\n".encode("utf-8")
                    handler.wfile.write(chunk)
                handler.wfile.flush()
                now = time.monotonic()
                if now - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
                    handler.wfile.write(b": hb\n\n")
                    handler.wfile.flush()
                    last_heartbeat = now
                time.sleep(SSE_POLL_SECONDS)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def run_once(project: str | None = None) -> int:
    """`tick --once`: single pass over one or all registered projects,
    no HTTP server, no pidfile. Returns total actions executed."""
    registry = config.load_registry()
    daemon = Daemon(registry)
    projects = [project] if project else list(registry)
    total = 0
    for p in projects:
        total += daemon.run_pass(p)
    return total
