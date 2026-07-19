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
       gate-running marker to exempt against), budget_remaining from
       policy.max_cost minus summed attempt usage costs (same currency
       only), merge_history/carve_outcomes/rejections from recent events
       (iter_events tail), resume_failures (P34 2026-07-16) from
       _resume_failures() -- per receiptless INTERRUPTED attempt, the count
       of its attempt.resume-N.log files older than
       policy.resume_progress_grace_seconds, i.e. failed resume attempts.
    2. actions = reconcile.plan_project(inp)
    2.5. actions = self._apply_watchdog(project, cfg, states, actions) (P44
       2026-07-16, anti-runaway self-correction): watchdog.detect_runaways
       over the recent event window; a detected RunawaySignal escalates
       ONCE (NEEDS_OPERATOR{reason:'runaway',...}, recent-window deduped),
       ALWAYS drops the matching repeating action(s) from THIS pass, and
       once the same signal.key has persisted for
       RUNAWAY_PERSIST_AFTER_CYCLES consecutive passes (in-memory streak,
       disposable), auto-pauses the project ('drain-agents') — see
       _apply_watchdog's own docstring for the full contract.
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
- HTTP (bind/port from the registered project with min policy.http_port --
  P38: its policy.http_bind travels with it, default "127.0.0.1" loopback-only,
  "0.0.0.0" on a private ciu bridge network, never on host-network):
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

P41 2026-07-16 (direct carve from an intake brief):

- dispatch_targeted_carve(project, item_id) -> list[Event]: on-demand carve
  of ONE briefed backlog item, callable directly (CLI/UI) without waiting
  for a reconcile pass. Builds reconcile.CarveDispatch(item_id=...) and runs
  it through the SAME _execute_carve_dispatch flow as the untargeted
  headroom-refill trigger (reconcile.py module contract item 9) -- identical
  synthetic-task/seq/authority/route semantics, differing ONLY in the carve
  packet's sources: instead of the review/backlog/roadmap/product-goal list,
  the packet embeds that one item's P29 intake brief (gated on
  backlog_items.is_briefed: header-comment present AND non-empty detail, so
  an un-headered legacy bullet's body prose is never mistaken for a brief).
  The embedded brief is the item's detail prose PLUS its header-borne
  priority and linked D-NNN ids -- intake_chat._parse_brief splits those out
  of the prose into header tokens, so detail alone would drop the very
  interview answers this path exists to carry. The synthetic carve task's
  notes carry `item=<id>` so a targeted leg is identifiable in the log.
  Because this is operator-initiated, it deliberately does NOT consult the
  headroom/carve-ahead trigger conditions (those gate the AUTOMATIC refill),
  but it DOES keep the frontier-route defense-in-depth check: no healthy
  'frontier-review' route -> NEEDS_OPERATOR {reason: 'carve-no-route'} and
  no synthetic task is minted.

P47 2026-07-19 (carve-dispatch mutex, closes a real race): neither the
untargeted headroom-refill trigger's carve_in_flight scan (reconcile.py
item 9/12) nor dispatch_targeted_carve's direct call path had ever been
protected against two carve dispatches racing each other -- the scan is a
plain read of current statefiles, not atomic with the write that follows,
and dispatch_targeted_carve (being callable directly, with no reconcile
pass in between) skips the scan entirely. Two dispatch_targeted_carve
calls close enough in time (or one racing the automatic trigger) could
both pass their checks and each spawn a real CARVER attempt, violating
the single-strategic-carver invariant the operator was explicit about.
Fixed the ONE place both paths converge (_execute_carve_dispatch's
WrapperSpec) rather than each caller separately: it now carries
leases=[{"name": f"{project}.strategic-carver", "capacity": 1}], so
wrapper_main's existing (frozen, already-battle-tested for handoff-
declared serialize-with mutexes) lease-acquisition step 2 does the actual
enforcement -- non-blocking flock, race loser gets a clean
ATTEMPT_FAILED{blocked_reason: 'lease-lost-race'} and exits 75 without
ever starting a real carver CLI session, race winner holds the flock for
its ENTIRE wrapper process lifetime (not just the dispatch call), and the
kernel auto-releases it the instant that process exits for any reason --
crash, kill, or clean completion -- with zero daemon-side monitoring or
stale-lock recovery needed (leases.py's own frozen-core contract). Holding
the lease in the WRAPPER rather than the daemon's own process is
deliberate: P37's tini+supervisor design makes the daemon process itself
independently restart-safe from in-flight attempts, so a lease living in
the daemon's memory would spuriously free on a routine daemon respawn
while the actual carver subprocess (reparented to tini, still alive) kept
running -- exactly the bug P37 exists to prevent recurring here.
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
    adapters, backlog_items, commands, config, decision_chat, decisions, frontmatter,
    intake_chat, leases, lint, notify, paths, reconcile, render, storage, watchdog,
    wrapper,
)
from .config import GateDef, ProjectConfig
from .types import (
    Actor, ActorKind, Attempt, AttemptState, Blocker, BlockerType, Event,
    EventType, GateResult, Receipt, ReceiptResult, Role, Route, TaskState,
    TaskStateFile, TERMINAL_ATTEMPT_STATES, iso, new_id, utc_now,
)

# Tunables (module constants so tests can shrink them for determinism).
PROBE_TTL_SECONDS = 600
PROVIDER_PAUSE_SECONDS = 3600
SSE_POLL_SECONDS = 0.5
SSE_HEARTBEAT_SECONDS = 15.0
DEFAULT_HTTP_PORT = 8942
DEFAULT_HTTP_BIND = "127.0.0.1"
DEFAULT_RECONCILE_INTERVAL = 30.0
# P44 2026-07-16 (anti-runaway self-correction): trailing window for
# _history's review_rejections_by_area count (module constant, not
# config.Policy -- Policy is frozen for this package, same reasoning as
# DEFAULT_ATTEMPT_MAX_WALL_SECONDS in reconcile.py). 7 days is long enough
# that a genuinely active rejection streak still counts, short enough that
# a one-off rejection from weeks ago can no longer keep a project's
# SpecAttention('rejections') condition artificially open forever.
HISTORY_REJECTION_WINDOW_SECONDS = 7 * 24 * 3600
# P44 2026-07-16: how many CONSECUTIVE reconcile passes the identical
# RunawaySignal.key must re-fire before the watchdog escalates its remedy
# from "suppress the repeating action" to "auto-pause the project" (see
# Daemon._apply_watchdog). Disposable in-memory streak (rebuilt on
# restart, same convention as _stall_cache's two-pass CPU cache) -- a
# restart resetting the streak just costs a few extra graduated cycles,
# never a wrong-direction outcome.
RUNAWAY_PERSIST_AFTER_CYCLES = 3

# P15 2026-07-15: UI config endpoints (POST-only; GET on these -> 405).
# P18 2026-07-16: /api/decision/reply joins this POST-only set (not a config
# mutation, but the same GET->405 guard applies).
# P30 2026-07-16: /api/intake joins it too -- the ONE sanctioned write path
# into intake_chat.advance_intake, loopback-only like the rest of this surface.
_CONFIG_POST_PATHS = frozenset({
    "/api/config/policy", "/api/config/pause", "/api/config/tier",
    "/api/decision/reply", "/api/intake",
})

# /api/intake is the one route that lets a caller NAME the record it writes
# (every other id here must already exist, or is minted server-side), so the
# id is constrained to exactly what new_id("intake") emits. Unconstrained it
# reaches a filesystem path (intake_chat._chat_path) and an onclick= JS string
# literal in intake.html -- i.e. traversal and stored XSS.
_INTAKE_ID_RE = re.compile(r"intake-[0-9a-f]{12}")

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
        self.http_bind: str = ""
        self._stop_event = threading.Event()
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._cmd_listener: commands.CommandListener | None = None
        # Daemon memory: disposable, rebuilt on restart.
        self._probe_memo: dict[str, tuple[float, bool, str]] = {}
        self._stall_cache: dict[str, str | None] = {}
        self._provider_paused: dict[str, float] = {}
        self._decisions_seen: dict[str, dict[str, str]] = {}
        # P44 2026-07-16 (anti-runaway self-correction): consecutive-pass
        # streak per "{project}:{RunawaySignal.key}" -- disposable, same
        # convention as _stall_cache's two-pass CPU cache above. Drives the
        # graduated remedy (see _apply_watchdog); the human-facing
        # escalation itself is deduped via the persisted event log instead
        # (restart-safe), not via this dict.
        self._runaway_streak: dict[str, int] = {}

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
            actions, watchdog_events = self._apply_watchdog(project, cfg, states, actions)
            appended.extend(watchdog_events)
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

    def dispatch_targeted_carve(self, project: str, item_id: str) -> list[Event]:
        """P41 2026-07-16: on-demand carve of ONE briefed backlog item --
        distinct from reconcile.py's untargeted headroom-refill CarveDispatch
        trigger (module contract item 9, run via run_pass/plan_project).
        Builds a reconcile.CarveDispatch(item_id=...) and executes it
        through the SAME carve-dispatch control flow
        (_execute_carve_dispatch) the untargeted trigger uses -- not a
        parallel/stubbed path -- just parameterized so the carver is seeded
        with exactly `item_id`'s intake brief instead of the general
        review/backlog/roadmap source list. Callable directly (CLI/UI); does
        not require a reconcile pass to have run first."""
        cfg = config.ProjectConfig.load(self.registry[project])
        states = storage.list_states(project)
        action = reconcile.CarveDispatch(project=project, item_id=item_id)
        events = self._execute_carve_dispatch(project, cfg, states, action)
        if events:
            render.render_after_event(self.registry)
        return events

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
        resume_failures = self._resume_failures(project, states, cfg.policy.resume_progress_grace_seconds)
        budget_remaining = self._budget_remaining(cfg, states)
        merge_history, carve_outcomes, review_rejections_by_area, blocked_underspecified_count = \
            self._history(project)
        ratchet_already_open = self._ratchet_already_open(project)
        roadmap_exhausted_open = self._roadmap_exhausted_open(project)
        # P44 2026-07-16 (anti-runaway self-correction): reuse the existing
        # _spec_attention_recently_emitted debounce backstop as the SOURCE of
        # these three dedup flags (it already implements exactly
        # _ratchet_already_open's convention, generalized by reason) -- it
        # remains a belt-and-braces backstop at emission time too (see
        # _execute's SpecAttention branch), but is no longer the ONLY guard.
        rejections_already_open = self._spec_attention_recently_emitted(project, "rejections")
        carve_outcome_already_open = self._spec_attention_recently_emitted(project, "carve-outcome")
        blocked_underspecified_already_open = self._spec_attention_recently_emitted(
            project, "blocked-underspecified")
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
            rejections_already_open=rejections_already_open,
            carve_outcome_already_open=carve_outcome_already_open,
            blocked_underspecified_already_open=blocked_underspecified_already_open,
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

    def _resume_failures(self, project: str, states: dict[str, TaskStateFile],
                          grace_seconds: int) -> dict[str, int]:
        """P34 2026-07-16 (resume-safety re-cut): attempt_id -> count of
        aged attempt.resume-N.log files for each receiptless INTERRUPTED
        attempt. A resume that worked leaves the attempt RUNNING or
        EXITED-with-receipt, so an attempt sitting INTERRUPTED with N aged
        resume logs has had N failed resumes by construction -- do NOT
        score progress by log size (the P26 bug this replaces scored a
        noisily-dying session, stack traces and retry spam, as progress).
        The grace window is only a race guard so a just-launched resume
        whose ATTEMPT_RESUMED has not landed is not miscounted."""
        out: dict[str, int] = {}
        now = time.time()
        for tsf in states.values():
            for att in tsf.attempts:
                if att.state != AttemptState.INTERRUPTED:
                    continue
                attempt_dir = paths.attempt_dir(project, att.attempt_id)
                if (attempt_dir / "receipt.json").exists():
                    continue
                count = 0
                for log_path in attempt_dir.glob("attempt.resume-*.log"):
                    try:
                        mtime = log_path.stat().st_mtime
                    except OSError:
                        continue
                    if now - mtime > grace_seconds:
                        count += 1
                out[att.attempt_id] = count
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
        """P44 2026-07-16 (anti-runaway self-correction): review_rejections_by_area
        is now WINDOWED (only rejections within HISTORY_REJECTION_WINDOW_SECONDS of
        'now' count) -- the root cause of the 2026-07-16 notification storm. Before
        this fix it counted rejections over the ENTIRE event log and only ever
        increased, so a project that once hit 2 rejections in some area stayed
        >= 2 forever, even if every rejection was months old and long since
        resolved. A time window (not an event-count window like the
        `*_already_open` flags below) is the right shape here: an aged, resolved
        rejection should age OUT regardless of how much OTHER unrelated event
        traffic has or hasn't happened since. merge_history / carve_outcomes /
        blocked_underspecified_count are UNCHANGED (still full-log, then sliced)."""
        merge_history: list[tuple[str, int, str]] = []
        carve_outcomes: list[dict] = []
        review_rejections_by_area: dict[str, int] = {}
        blocked_underspecified_count = 0
        try:
            events = list(storage.iter_events(project))
        except Exception:
            events = []
        now = utc_now()
        for ev in events:
            if ev.type is EventType.MERGE_RECORDED and ev.task_id:
                units = len(ev.payload.get("progress_units", []) or [])
                source = ev.payload.get("source_kind", "review")
                merge_history.append((ev.task_id, units, source))
            elif ev.type is EventType.CARVE_OUTCOME:
                carve_outcomes.append(ev.payload)
            elif ev.type is EventType.REVIEW_RECORDED and ev.payload.get("result") == "rejected":
                age_seconds = (now - ev.timestamp).total_seconds()
                if age_seconds <= HISTORY_REJECTION_WINDOW_SECONDS:
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

    def _spec_attention_recently_emitted(self, project: str, reason: str | None) -> bool:
        """Debounce backstop (prod-bleed fix 2026-07-16). Suppress re-emitting a
        SPEC_ATTENTION whose reason already appears in the recent window --
        otherwise a PERSISTENT condition re-emits + notifies EVERY reconcile
        cycle forever. Root case: `review_rejections_by_area` (_history) counts
        rejections over the WHOLE event log and never decreases, and the
        reconcile 'rejections'/'carve-outcome'/'blocked-underspecified' branches
        (unlike 'ratchet'/'roadmap-exhausted') have no dedup flag -- so 2 rejects
        stormed ntfy at 1/cycle. Mirrors _ratchet_already_open's convention and
        covers ALL reasons as a general backstop. P44 2026-07-16: this is now
        ALSO the source of ReconcileInput.rejections_already_open /
        carve_outcome_already_open / blocked_underspecified_already_open (see
        _build_input) -- the durable fix -- so it is no longer the only guard,
        just a belt-and-braces backstop at emission time too. See watchdog.py
        for the general runaway backstop (not tied to any specific reason)."""
        try:
            recent = list(storage.iter_events(project))[-500:]
        except Exception:
            return False
        return any(ev.type is EventType.SPEC_ATTENTION and ev.payload.get("reason") == reason
                   for ev in recent)

    # -- runaway watchdog (P44 2026-07-16) ------------------------------

    def _apply_watchdog(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                        actions: list[reconcile.Action]) -> tuple[list[reconcile.Action], list[Event]]:
        """Run watchdog.detect_runaways over the recent event window BEFORE
        this pass's actions execute. For each detected RunawaySignal:
          (i)  escalate ONCE -- a NEEDS_OPERATOR{reason:'runaway', pattern,
               key, detail} event, deduped via a recent-window scan (see
               _runaway_recently_escalated) exactly like
               _spec_attention_recently_emitted -- persisted, restart-safe.
          (ii) suppress the matching repeating action(s) from THIS pass's
               action list, ALWAYS (never silently repeat a harmful action,
               even once more, regardless of whether (i) already fired).
         (iii) track an in-memory per-(project, signal.key) consecutive-pass
               streak (disposable, rebuilt on restart -- same convention as
               _stall_cache); once it reaches RUNAWAY_PERSIST_AFTER_CYCLES,
               grade the remedy up from suppress-only to auto-pausing the
               whole project ('drain-agents' -- blocks every new agent
               process: dispatch, resume, AND review launch) via
               paths.pause_flag(project), so a persistent runaway stops
               rather than merely slows down. A no-op if already paused
               (human or an earlier runaway already handled it).
        Returns (filtered_actions, new_events) -- both empty/unchanged when
        no runaway is detected (the overwhelmingly common case)."""
        try:
            recent_events = list(storage.iter_events(project))[-500:]
        except Exception:
            recent_events = []
        try:
            signals = watchdog.detect_runaways(recent_events, watchdog.WatchdogConfig())
        except Exception:
            signals = []
        if not signals:
            return actions, []

        filtered = list(actions)
        new_events: list[Event] = []
        for sig in signals:
            streak_key = f"{project}:{sig.key}"
            streak = self._runaway_streak.get(streak_key, 0) + 1
            self._runaway_streak[streak_key] = streak

            if not self._runaway_recently_escalated(project, sig.key):
                new_events.append(self._append_ev(
                    project, cfg, states, EventType.NEEDS_OPERATOR,
                    {"reason": "runaway", "pattern": sig.pattern, "key": sig.key,
                     "detail": sig.detail},
                ))

            filtered = self._suppress_runaway_action(filtered, sig)

            if streak >= RUNAWAY_PERSIST_AFTER_CYCLES:
                pause_ev = self._auto_pause_for_runaway(project, cfg, states, sig)
                if pause_ev is not None:
                    new_events.append(pause_ev)

        return filtered, new_events

    def _runaway_recently_escalated(self, project: str, key: str) -> bool:
        """Same recent-window convention as _spec_attention_recently_emitted,
        keyed on RunawaySignal.key (not just pattern -- multiple distinct
        conditions can share one pattern, e.g. two different
        'reconcile-thrash:<reason>' keys)."""
        try:
            recent = list(storage.iter_events(project))[-500:]
        except Exception:
            return False
        return any(
            ev.type is EventType.NEEDS_OPERATOR
            and ev.payload.get("reason") == "runaway"
            and ev.payload.get("key") == key
            for ev in recent
        )

    def _suppress_runaway_action(self, actions: list[reconcile.Action],
                                  sig: watchdog.RunawaySignal) -> list[reconcile.Action]:
        """Drop the specific repeating action(s) this pass's plan would
        otherwise (re-)execute for a detected runaway. Deliberately narrow
        (matches only the action shape the signal itself proves is
        repeating) rather than blanket-suppressing the whole pass."""
        if sig.pattern == "reconcile-thrash":
            reason = sig.key.split(":", 1)[1] if ":" in sig.key else None
            return [a for a in actions
                    if not (isinstance(a, reconcile.SpecAttention) and a.reason == reason)]

        if sig.pattern == "notification-storm":
            parts = sig.key.split(":")
            if len(parts) == 2:
                # 'notification-storm:total' -- blunt fallback: no single
                # reason dominates, so suppress every SpecAttention this pass.
                return [a for a in actions if not isinstance(a, reconcile.SpecAttention)]
            _, type_val, reason = parts
            if type_val == EventType.SPEC_ATTENTION.value:
                return [a for a in actions
                        if not (isinstance(a, reconcile.SpecAttention) and a.reason == reason)]
            # A NEEDS_OPERATOR reason storm isn't a reconcile.Action the
            # planner emits (it's a daemon-internal escalation, e.g.
            # carve-ready) -- nothing to filter here; the streak-graded
            # auto-pause below is the remedy instead.
            return actions

        if sig.pattern == "attempt-loop":
            task_id = sig.key.split(":", 1)[1] if ":" in sig.key else None
            return [a for a in actions
                    if not (isinstance(a, (reconcile.DispatchImplementer, reconcile.ResumeAttempt))
                            and a.task_id == task_id)]

        return actions

    def _auto_pause_for_runaway(self, project: str, cfg: ProjectConfig,
                                 states: dict[str, TaskStateFile],
                                 sig: watchdog.RunawaySignal) -> Event | None:
        """Graduated remedy for a PERSISTENT runaway: 'drain-agents' blocks
        every new agent process (dispatch, resume, review launch -- the
        strongest pause mode, see reconcile.py module contract item 5/P15),
        so the repeating action structurally cannot recur. A no-op (returns
        None, no event) if the project is already paused by anything --
        never downgrades an existing pause, never double-pauses."""
        flag = paths.pause_flag(project)
        if flag.exists():
            return None
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("drain-agents", encoding="utf-8")
        return self._append_ev(
            project, cfg, states, EventType.PAUSE_SET,
            {"mode": "drain-agents", "reason": "runaway", "pattern": sig.pattern, "key": sig.key},
        )

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

    # -- post-merge validation (nyxloom-post-merge-validation, 2026-07-17) --
    #
    # reconcile.py's module contract item 11 plans MERGED->VALIDATING (pure
    # bookkeeping), then RunPostMergeGate(task_id) every pass while
    # VALIDATING. The three helpers below do the actual work daemon-side.
    # Unlike DispatchImplementer/LaunchReview (an AI CLI leg supervised by
    # wrapper.launch_detached, async, receipt-polled over many passes), a
    # post-merge gate is a TRUSTED STRUCTURED argv (config.py's own docstring:
    # "model output can never introduce an executable") -- a deterministic,
    # non-LLM-mediated command, so there is no receipt/session/resume
    # machinery to reuse and none is invented here. This IS the first real
    # consumer of GateDef.phase/timeout_seconds and GateResult (both were
    # declared but never read/produced anywhere in daemon.py before this).
    #
    # SYNC, not planned-action-polled: _run_post_merge_gate blocks this one
    # pass for up to gate.timeout_seconds. The daemon's own tick loop
    # (Daemon.run) iterates registered projects SEQUENTIALLY in a single
    # thread, so a slow post-merge gate for one project does stall every
    # other registered project's reconcile pass for that same window -- a
    # real, but bounded (by timeout_seconds) and infrequent (merges are a
    # manual operator step under merge_mode=manual, not a hot dispatch path)
    # cost. Chosen anyway because it needs zero new Role/Attempt/wrapper
    # machinery (types.py's Role enum is out of scope for this package, and
    # none of its four members fits "re-verify a merged gate" without
    # misusing an existing one) -- "minimal and correct" per the handoff. A
    # fully async/detached re-cut (mirroring DispatchImplementer's
    # launch-then-poll-receipt shape) is the natural follow-up if this
    # blocking proves to matter in practice; flagged here, not silently
    # hidden.
    def _select_post_merge_gate(self, cfg: ProjectConfig) -> GateDef | None:
        """Prefer a gate the project declares phase == 'post-merge'. No
        project registered today declares one (nyxloom's own nyxloom-trove/
        nyxloom.toml has exactly one gate, phase 'implementation'), so the
        documented default (handoff's own "if a project declares no post-
        merge gate" clause) is to re-run the 'implementation' gate against
        the merged default branch instead -- the same gate the merged code
        was already required to pass, just re-verified post-merge (the
        CLAUDE.md "re-run the gate on main post-merge" discipline this
        pipeline exists to automate). None only if the project declares NO
        gates at all -- see the no-op-validated-pass branch below."""
        post_merge = [g for g in cfg.gates.values() if g.phase == "post-merge"]
        if post_merge:
            return sorted(post_merge, key=lambda g: g.gate_id)[0]
        impl = [g for g in cfg.gates.values() if g.phase == "implementation"]
        if impl:
            return sorted(impl, key=lambda g: g.gate_id)[0]
        return None

    def _post_merge_worktree_value(self, cfg: ProjectConfig) -> str:
        """The {worktree} substitution for a gate re-run against the
        already-MERGED default branch (as opposed to a feature-branch
        attempt, whose {worktree} is cfg.root / cfg.worktree_root / branch
        -- a fresh git worktree with its OWN top-level). Post-merge
        validation has no separate worktree: it runs against the project's
        one already-merged checkout, so the correct value is THAT
        checkout's git top-level.

        Using `git rev-parse --show-toplevel` (rather than cfg.root itself)
        is what makes this correct for BOTH project shapes seen in this
        codebase: dstdns's cfg.root IS its repo root (top-level == cfg.root,
        no-op), while nyxloom's own cfg.root is a SUBDIRECTORY of the vbpub
        repo it is self-hosted in (top-level == cfg.root.parent) -- exactly
        the repo-root convention nyxloom's own gate argv already assumes
        (`cd {worktree}/nyxloom`, matching a feature worktree's top-level +
        '/nyxloom'). Falls back to cfg.root if the git call fails for any
        reason (e.g. a non-git test fixture)."""
        try:
            res = subprocess.run(
                ["git", "-C", str(cfg.root), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return str(cfg.root)

    def _run_post_merge_gate(self, project: str, cfg: ProjectConfig,
                              states: dict[str, TaskStateFile],
                              action: "reconcile.RunPostMergeGate") -> list[Event]:
        """VALIDATING -> COMPLETED (gate passes) or BLOCKED (gate fails,
        errors, or times out) -- see the module-level note above for the
        sync/blocking rationale and gate-selection/worktree-substitution
        helpers this calls."""
        task_id = action.task_id
        events: list[Event] = []
        gate = self._select_post_merge_gate(cfg)

        if gate is None:
            # No gate declared at all for this project: the documented
            # default is a no-op-validated pass straight to COMPLETED (no
            # GateResult recorded -- there is nothing to record).
            events.append(self._transition(
                project, cfg, states, task_id, TaskState.COMPLETED,
                "post-merge validation: project declares no gate, no-op pass"))
            return events

        worktree_value = self._post_merge_worktree_value(cfg)
        argv = [tok.replace("{worktree}", worktree_value) for tok in gate.argv]
        commit = states[task_id].merge_commit or ""
        started = utc_now()
        try:
            proc = subprocess.run(argv, cwd=str(cfg.root), capture_output=True,
                                   text=True, timeout=gate.timeout_seconds)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124  # conventional shell timeout exit code
        except OSError:
            exit_code = 127  # command-not-found / exec failure
        ended = utc_now()

        gate_result = GateResult(
            gate_id=gate.gate_id, phase="post-merge", commit=commit,
            exit_code=exit_code, started=started, ended=ended,
            environment=gate.environment,
        )
        events.append(self._append_ev(project, cfg, states, EventType.GATE_FINISHED,
                                       {"gate_result": gate_result.to_dict()}, task_id=task_id))

        if exit_code == 0:
            events.append(self._transition(
                project, cfg, states, task_id, TaskState.COMPLETED,
                f"post-merge gate {gate.gate_id} passed"))
        else:
            blocker = Blocker(
                type=BlockerType.CONTRACT,
                unblock_condition="operator: inspect post-merge gate failure",
                detail=f"post-merge gate {gate.gate_id} exit_code={exit_code}"[:200],
            )
            events.append(self._append_ev(
                project, cfg, states, EventType.TASK_BLOCKED,
                {"from": states[task_id].state.value, "blocker": blocker.to_dict()},
                task_id=task_id))
        return events

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

    def _carve_source_note_lines(self, cfg: ProjectConfig,
                                  item_id: str | None = None) -> list[str]:
        """Carve sources #2/#3 (backlog, roadmap/gap-analysis): name the
        conventional file paths the carver reads itself (same economy as the
        review packet's diff-only embedding: point, don't slurp). ProjectConfig
        has no 'product_sources' field today (config.py is frozen beyond the
        P16-authorized Policy fields), so this probes fixed conventional paths.
        B2 2026-07-16: prefer the nyxloom-trove layout (backlog.md/roadmap.md
        under the managed trove) and fall back to the legacy docs/ convention
        for un-migrated projects -- mirroring config.load()'s own trove-first/
        legacy-fallback resolution of nyxloom.toml.

        P41 2026-07-16: when `item_id` names a single targeted backlog item
        (dispatch_targeted_carve), this embeds THAT item's brief (gated on
        backlog_items.is_briefed) instead of the generic file pointers below
        -- so a direct carve of a briefed item loses no interview context.
        An un-briefed/legacy item still gets only a plain reference (no
        invented brief)."""
        if item_id is not None:
            return self._targeted_item_note_lines(cfg, item_id)

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

    def _targeted_item_note_lines(self, cfg: ProjectConfig, item_id: str) -> list[str]:
        """P41 2026-07-16: the ONE carve source for a targeted carve --
        item_id's own intake brief, embedded verbatim (not a file pointer).
        A backlog with no such item, or one that is not is_briefed (legacy
        un-headered bullet, or a headered item with no detail), yields a
        plain reference line only -- never a fabricated brief.

        The brief is NOT the detail prose alone. intake_chat._parse_brief
        splits a P29 reply into title/Priority:/Decisions:/free prose, and
        backlog_items.create() then persists priority + decisions as HEADER
        tokens, leaving only the prose on the bullet's continuation lines.
        So embedding item.detail alone would silently drop the priority the
        interview explicitly asked the operator for (step 6) and the D-NNN
        decisions the intake agent filed on their behalf (step 4) -- the
        exact interview context this package exists to preserve. Emit the
        header fields alongside the prose. Decisions are named, not slurped
        (the carver reads decisions.md itself -- same point-don't-slurp
        economy as the untargeted source notes above)."""
        path = backlog_items.resolve_path(cfg)
        items = backlog_items.parse(path)
        item = next((it for it in items if it.id == item_id), None)
        rel = path.relative_to(cfg.root)
        if item is None:
            return [f"- targeted backlog item {item_id}: not found in {rel}"]
        if not backlog_items.is_briefed(item):
            return [f"- targeted backlog item {item_id} (status={item.status}): "
                    "no intake brief on file"]
        lines = [f"- targeted backlog item {item_id} -- intake brief:"]
        if item.priority is not None:
            lines.append(f"  priority: {item.priority}")
        if item.decisions:
            lines.append(f"  linked decisions: {', '.join(item.decisions)} "
                         "(read this project's decisions.md for their content)")
        lines.extend(f"  {ln}" for ln in item.detail.splitlines())
        return lines

    def _build_carve_packet(self, cfg: ProjectConfig, project: str, seq: int,
                             states: dict[str, TaskStateFile],
                             own_task_id: str | None = None,
                             item_id: str | None = None) -> str:
        """The carve packet (mirrors the review packet's economy: point at
        sources, embed only what is cheap and structured). Written to the
        carve attempt's own packet/packet.md, exactly like LaunchReview's
        packet.

        P41 2026-07-16: when `item_id` is set (dispatch_targeted_carve),
        this is a TARGETED carve -- the packet's only carve source is that
        one backlog item's intake brief (its pre-carve detail: aligned
        purpose, elicited detail, linked D-NNN, priority), distinct from the
        untargeted headroom-refill carve's review/backlog/roadmap/product-
        goal source list."""
        lines = [
            f"# Carve packet {seq}",
            "",
            "## Your role: CARVER",
            "",
        ]
        if item_id is not None:
            lines.extend([
                f"You are carving ONE new handoff package for project '{project}', "
                f"directly from backlog item {item_id}'s intake brief below -- it was "
                "already elicited via the intake-chat interview, so do not re-derive "
                "it from scratch. Write a single lint-clean handoff file under this "
                "project's handoff directory covering exactly that item. Do NOT "
                "implement the work yourself -- you carve a package for another agent "
                "to pick up later.",
                "",
                "## Carve source: targeted intake brief",
            ])
            lines.extend(self._carve_source_note_lines(cfg, item_id=item_id))
            lines.append("")
        else:
            lines.extend([
                f"You are proposing NEW handoff packages for project '{project}'.",
                "Read the carve sources below, then write new lint-clean handoff",
                "file(s) under this project's handoff directory. Do NOT implement",
                "the work yourself -- you carve packages for other agents to pick",
                "up later.",
                "",
                "## Carve sources (v2 SS8)",
                "1. Review-derived follow-ups (recent REVIEW_RECORDED events):",
            ])
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
        notes = f"carve seq={seq} authority={authority}"
        if action.item_id is not None:
            notes += f" item={action.item_id}"
        tsf = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
            state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
            notes=notes,
        )
        events.append(self._append_ev(project, cfg, states, EventType.TASK_CREATED,
                                       {"statefile": tsf.to_dict()}, task_id=task_id))

        attempt_id = new_id("att")
        attempt_dir = paths.attempt_dir(project, attempt_id)
        packet_dir = attempt_dir / "packet"
        packet_dir.mkdir(parents=True, exist_ok=True)
        packet_text = self._build_carve_packet(cfg, project, seq, states, own_task_id=task_id,
                                                item_id=action.item_id)
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
            role=Role.CARVER, carve_authority=authority,
        )
        spec = wrapper.WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
            cwd=str(carve_cwd), log_path=str(attempt_dir / "attempt.log"),
            receipt_path=receipt_path, attempt_dir=str(attempt_dir), route_def=asdict(route_def),
            leases=[{"name": f"{project}.strategic-carver", "capacity": 1}],
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

    def _parse_review_verdict(self, cfg: ProjectConfig, task_id: str) -> str:
        """P33 2026-07-16: the merge gate must reflect the reviewer's actual
        verdict, not just process exit (live P26 incident -- a correct
        REJECTED review report + clean process exit -> receipt DONE ->
        rubber-stamped MERGE_READY). Reads review artifacts committed to the
        task's OWN feat/<task_id> branch (git show, read-only) and extracts
        a `VERDICT: APPROVED` or `VERDICT: REJECTED` line.

        SELF-CORRECT 2026-07-16 (bug 1 of the review-verdict + reject-loop
        package): a live incident had a reviewer commit `P42-REVIEW.md`
        instead of the documented `<task_id>-REVIEW.md` -- the old rigid
        single-path lookup found nothing and fail-safed a genuinely-APPROVED
        task to REJECTED, which then had nowhere to go (bug 2: no reconcile
        handling for REVIEW_REJECTED) and STRANDED forever. Lookup is now
        two-step:
          1. the documented `<task_id>-REVIEW.md` path (preferred, as
             before -- cheapest, single git-show, matches the common case).
          2. only if that yields no VERDICT line (absent file, or present
             but silent), broaden to every `*REVIEW*.md` under reports_dir
             on the SAME branch, and treat any whose filename OR content
             mentions task_id as a candidate for this task -- catches a
             misnamed file like the live incident without depending on the
             reviewer following the naming convention.
        Verdicts from all matched candidates are pooled before classifying,
        so a real APPROVED anywhere for this task is found regardless of
        which file it landed in.

        Return values (a plain str; the FRONTIER_REVIEW call site only ever
        compares `== "approved"`, so any non-"approved" value already takes
        the existing fail-safe REVIEW_REJECTED path unchanged):
          "approved" -- exactly one unambiguous APPROVED verdict pooled
                        across all candidates for this task.
          "rejected" -- an explicit REJECTED verdict, OR conflicting/
                        ambiguous verdicts (two disagreeing VERDICT lines --
                        still fails safe to rejected), OR at least one
                        review artifact for this task exists but carries no
                        VERDICT line at all (a malformed review -- fail
                        safe exactly as before this fix).
          "missing"  -- NO review artifact referencing this task exists
                        ANYWHERE on the branch. This is a review-LEG
                        failure (the reviewer never produced any output),
                        distinct from a reviewer's genuine REJECTED verdict
                        -- it is NOT "approved", so the task still lands in
                        REVIEW_REJECTED either way (fail-safe preserved);
                        this is purely a distinguishing signal (visible in
                        the REVIEW_RECORDED event / transition notes) so a
                        missing verdict is never silently conflated with a
                        real rejection downstream.

        REVIEW-FIX 2026-07-16: the `./` prefix on `git show <rev>:<path>` is
        load-bearing under `-C` (bare paths resolve from the REPO ROOT,
        ignoring `-C`; reports_dir is relative to cfg.root, which is NOT
        always the repo root -- nyxloom self-hosts with cfg.root=<repo>/
        nyxloom). `git ls-tree -- <path>` pathspecs are cwd/-C relative
        regardless of a `./` prefix (verified empirically); `./` is kept
        below purely for visual consistency with the show calls."""
        branch = f"feat/{task_id}"

        def _verdicts_in(content: str) -> set[str]:
            return {
                m.group(1).upper()
                for m in re.finditer(r"^\s*VERDICT:\s*(APPROVED|REJECTED)\b", content,
                                      re.IGNORECASE | re.MULTILINE)
            }

        rel_path = f"{cfg.reports_dir}/{task_id}-REVIEW.md"
        show_res = subprocess.run(
            ["git", "-C", str(cfg.root), "show", f"{branch}:./{rel_path}"],
            capture_output=True, text=True,
        )
        any_candidate_found = False
        all_verdicts: set[str] = set()
        if show_res.returncode == 0:
            any_candidate_found = True
            all_verdicts |= _verdicts_in(show_res.stdout)

        if not all_verdicts:
            # The documented path was absent or silent -- broaden the
            # search (bug 1 fix: a misnamed review file still counts).
            ls_res = subprocess.run(
                ["git", "-C", str(cfg.root), "ls-tree", "-r", "--name-only", branch,
                 "--", f"./{cfg.reports_dir}"],
                capture_output=True, text=True,
            )
            if ls_res.returncode == 0:
                for path in ls_res.stdout.splitlines():
                    path = path.strip()
                    if not path or path == rel_path:
                        continue  # already handled above
                    name = path.rsplit("/", 1)[-1]
                    if "REVIEW" not in name.upper() or not name.upper().endswith(".MD"):
                        continue
                    show2 = subprocess.run(
                        ["git", "-C", str(cfg.root), "show", f"{branch}:./{path}"],
                        capture_output=True, text=True,
                    )
                    if show2.returncode != 0:
                        continue
                    content = show2.stdout
                    if task_id not in content and task_id not in name:
                        continue  # doesn't reference this task
                    any_candidate_found = True
                    all_verdicts |= _verdicts_in(content)

        if all_verdicts:
            return "approved" if all_verdicts == {"APPROVED"} else "rejected"
        if any_candidate_found:
            # A review artifact for this task exists but never carries a
            # VERDICT line -- malformed, fail safe exactly as before.
            return "rejected"
        # No review artifact referencing this task exists anywhere on the
        # branch -- a review-LEG failure, not a genuine reject verdict.
        return "missing"

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
                role=Role.IMPLEMENTER,
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
                # P33 2026-07-16: the receipt only reflects PROCESS exit
                # (wrapper.py infers DONE on any clean exit) -- it is never
                # the review's actual verdict (live P26 incident: a REJECTED
                # review report + clean exit rubber-stamped MERGE_READY).
                # The non-DONE receipt states (BLOCKED/LIMIT/ERROR) stay a
                # defense-in-depth fail-safe below WITHOUT reading the
                # report; only a DONE receipt's verdict is worth parsing.
                if result is ReceiptResult.DONE:
                    verdict = self._parse_review_verdict(cfg, task_id)
                else:
                    verdict = "rejected"
                events.append(self._append_ev(
                    project, cfg, states, EventType.REVIEW_RECORDED,
                    {"result": verdict}, task_id=task_id,
                    attempt_id=action.attempt_id, wave_id=attempt.wave_id))
                if verdict == "approved":
                    events.append(self._transition(project, cfg, states, task_id,
                                                    TaskState.MERGE_READY, None))
                else:
                    events.append(self._transition(project, cfg, states, task_id,
                                                    TaskState.REVIEW_REJECTED,
                                                    f"review verdict: {verdict} (receipt: {result.value})"))
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
                # REVIEW-FIX 2026-07-16: this path was hardcoded to a stale
                # `topos/handoff/reports/` that matches no nyxloom project
                # (nyxloom's reports_dir is `nyxloom-trove/reports`). Harmless
                # while the verdict came from the receipt; load-bearing now
                # that _parse_review_verdict reads THIS file -- a reviewer
                # obeying the old literal path wrote where the daemon never
                # looks, fail-safing every review to rejected. Derived from
                # cfg.reports_dir so prompt and parser cannot drift apart.
                f"6. Write {cfg.reports_dir}/<task>-REVIEW.md: findings,",
                "   what you fixed, verdict + reasoning. Commit it to the",
                "   feat/ branch (NOT main). Do NOT merge. Do NOT write the",
                "   implementer's LOG/REPORT. REQUIRED: this file MUST contain",
                "   a machine-readable verdict line, exactly one of:",
                "   `VERDICT: APPROVED` or `VERDICT: REJECTED — <reason>`.",
                "   The pipeline derives the merge decision from THIS line —",
                "   a missing or ambiguous VERDICT line fails safe to rejected,",
                "   even if your prose reasoning above it reads as approved.",
                "7. VERDICT signalling (drives the pipeline): if EVERY task",
                "   here is approved, finish normally. If ANY task must be",
                "   rejected, make your FINAL output line exactly:",
                "   `BLOCKED: rejected — <task ids and one-line reasons>`.",
                "   (kept as a second, defense-in-depth signal alongside the",
                "   per-task VERDICT: line in each REVIEW.md.)",
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
                gate_hint=gate_hint, receipt_path=receipt_path, role=Role.FRONTIER_REVIEW,
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
            # Debounce backstop: do not re-emit (and re-notify) the same
            # spec-attention reason every cycle for a persistent condition.
            if not self._spec_attention_recently_emitted(project, action.reason):
                events.append(self._append_ev(project, cfg, states, EventType.SPEC_ATTENTION,
                                               {"reason": action.reason, "detail": action.detail},
                                               task_id=action.task_id))

        elif isinstance(action, reconcile.CarveDispatch):
            events.extend(self._execute_carve_dispatch(project, cfg, states, action))

        elif isinstance(action, reconcile.RunPostMergeGate):
            events.extend(self._run_post_merge_gate(project, cfg, states, action))

        else:
            raise ValueError(f"unhandled action type: {type(action)!r}")

        return events

    # -- HTTP / SSE --------------------------------------------------------

    def _chosen_http(self) -> tuple[int, str]:
        """(port, bind) from the registered project with the lowest configured
        http_port (P38: that project's http_bind travels with it -- one HTTP
        server serves every project, so its bind is a single choice too)."""
        best: config.ProjectConfig | None = None
        for root in self.registry.values():
            try:
                cfg = config.ProjectConfig.load(root)
            except Exception:
                continue
            if best is None or cfg.policy.http_port < best.policy.http_port:
                best = cfg
        if best is None:
            return DEFAULT_HTTP_PORT, DEFAULT_HTTP_BIND
        return best.policy.http_port, best.policy.http_bind

    def _start_http(self) -> None:
        port, bind = self._chosen_http()
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

        httpd = http.server.ThreadingHTTPServer((bind, port), Handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        self.http_port = httpd.server_address[1]
        self.http_bind = httpd.server_address[0]
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
        if path == "/api/intake":
            self._post_intake(handler, body)
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

    def _post_intake(self, handler: http.server.BaseHTTPRequestHandler, body: dict) -> None:
        """P30: drive the intake-chat bridge from the UI (intake.html) --
        the ONE sanctioned write path into intake_chat.advance_intake().
        Body is untrusted operator input: passed through as plain text (no
        shell, no eval, no dynamic dispatch); advance_intake itself redacts
        the agent's reply before it is stored or returned here. Loopback-only,
        same as every other route on this server.

        `text` is free-form (it only ever becomes prompt/transcript text, and
        render.py escapes it), but `intake_id` names a file and is echoed into
        intake.html's JS, so it must match _INTAKE_ID_RE; omit it to open a
        fresh conversation and let the server mint one."""
        project = body.get("project")
        text = body.get("text")
        intake_id = body.get("intake_id")

        if not isinstance(project, str) or project not in self.registry:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        if not isinstance(text, str) or not text.strip():
            self._send_json(handler, 400, b'{"error":"missing text"}')
            return
        if intake_id is not None and (not isinstance(intake_id, str)
                                      or not _INTAKE_ID_RE.fullmatch(intake_id)):
            self._send_json(handler, 400, b'{"error":"invalid intake_id"}')
            return
        if not intake_id:
            intake_id = new_id("intake")

        try:
            cfg = config.ProjectConfig.load(self.registry[project])
        except Exception:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return

        try:
            reply = intake_chat.advance_intake(cfg, project, intake_id, text.strip())
        except Exception as exc:
            self._send_json(handler, 500, json.dumps({"error": repr(exc)[:200]}).encode("utf-8"))
            return

        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps(
            {"ok": True, "intake_id": intake_id, "reply": reply}).encode("utf-8"))

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
