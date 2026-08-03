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
    6. Drain every registered family's finished background work once (CR-05a:
       `EffectRegistry.drain`, which replaced two hardcoded drain calls).
- EXECUTION MAP (all storage writes via append_and_apply, actor
  Actor(TICK, 'nyxloomd')).

  CR-05a: this map is the CONTRACT; `_execute` is no longer its
  implementation. Each action type is registered to exactly one handler
  (`_build_registry`), and an action with no owner fails when the Daemon is
  CONSTRUCTED rather than on the first pass that plans it. The lifecycle
  families (CreateTask, Transition, Interrupt/Mark*/StallCheck, OpenWave,
  SpecAttention, ProviderPause) live in `effects_lifecycle.py`; the two gate
  families (VerifyGate, RunPostMergeGate) and both of their background-work
  registries live in `effects_gates.py`. The rest are still branches of
  `_execute_legacy` here, registered as legacy handlers owned by CR-05b and
  counted by `effects.LEGACY_HANDLER_BUDGET`. The behaviour described below
  is unchanged either way, and `tests/test_effect_differential.py` holds the
  moved families to the event sequences the pre-CR-05a code produced.
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
      report paths list in packet.md; create Attempt (role REVIEW_INDEPENDENT,
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
    P02 2026-07-21 (docs/plan-logging.md §4.4, D-L3): GET /api/logs/level
        -> {"level": <name>, "source": "runtime-file"|"env"|"config"|
        "default"} — the daemon's current effective log level and which
        D-L3 precedence layer supplied it (see `resolve_level`).
  P15 2026-07-15 (spec amendment, user directive): CONFIG mutations are now
  allowed through audited HTTP endpoints (workflow-STATE mutations
  remain CLI-only). 2026-08-02: "loopback" struck from that sentence -- the
  deployed bind is 0.0.0.0 (P38).
  CR-15 2026-08-02 (RISK-005): every POST in `_CONFIG_POST_PATHS` requires an
  operator credential (`control_auth`, `Authorization: Bearer <secret>`),
  checked before the request body is read; the authenticated identity becomes
  the `Actor` of the resulting events. GETs are deliberately left open so a
  trusted network can serve the dashboard read-only. `_reject_cross_site` is
  CSRF hardening that runs in ADDITION to that check, never instead of it.
  See `_handle_post` for the fixed order and control_auth's module docstring
  for what the trust boundary does and does not claim.
  This HTTP surface is NOT the only mutation ingress: the ntfy feedback topic
  (`commands.CommandListener`, `decision_chat.handle_feedback_message`) is the
  other one, and it carries no verified sender identity. CR-15 closes its
  mutating verbs by default -- see `control_auth.channel_operator`. Both
  ingresses audit refusals into the same control ledger through the same
  helper, so they cannot drift into two different refusal shapes.
  All three are POST, JSON in/out, 400 on validation
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
  P02 2026-07-21 (docs/plan-logging.md §4.4, D-L3 runtime control): POST
  /api/config/log-level {level} -> validates against the same level names
  log_module._normalize_level accepts (400, level unchanged, on a bad
  name); on success, log_module.set_level(level) flips the daemon's
  EFFECTIVE level immediately (no restart) AND persists it to
  paths.daemon_log_level_path() (D-L3 layer 1, so a respawn's
  resolve_level() bootstrap picks the same level back up). Deliberately
  emits an INFO **log record**, not a domain event -- D-L4, logs != events
  (§2) -- so no storage.append_and_apply / CONFIG_CHANGED here, unlike
  every other endpoint in this section.
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
  BEFORE the REVIEW_INDEPENDENT/implementer branches): read+parse that file;
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

import fnmatch
import hashlib
import http.server
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from . import (
    adapters, backlog_items, carver_session, commands, config, control_auth, decision_chat,
    decisions, doc_lifecycle, effects, effects_attempt, effects_carve, effects_carver,
    effects_exit,
    effects_dispatch, effects_gates,
    effects_lifecycle, effects_merge, effects_review, frontmatter,
    gate_canary, gate_runner, intake_chat, leases,
    lint, merge_digest, notify, paths, reconcile, render, results, snapshot, stages,
    storage, watchdog, wrapper,
)
from . import __version__
from .config import GateDef, ProjectConfig
from . import log as log_module
from .log import bind, get_logger
from .types import (
    Actor, ActorKind, Attempt, AttemptState, Blocker, BlockerType, CarverStatus,
    Event, EventType, GateResult, Receipt, ReceiptResult, Role, Route, TaskState,
    TaskStateFile, TERMINAL_ATTEMPT_STATES, TERMINAL_TASK_STATES, iso, new_id, utc_now,
)

log = get_logger("daemon")  # P01: first real user of nyxloom.log (proof it works end-to-end)

# Tunables (module constants so tests can shrink them for determinism).
PROBE_TTL_SECONDS = 600
SSE_POLL_SECONDS = 0.5
SSE_HEARTBEAT_SECONDS = 15.0
DEFAULT_HTTP_PORT = 8942
DEFAULT_HTTP_BIND = "127.0.0.1"
DEFAULT_RECONCILE_INTERVAL = 30.0
# CR-15: the instance-global control ledger (refusals, rotations, daemon-
# scoped config changes).  Owned by control_auth, re-exported here because
# every writer on this surface lives in this module.
CONTROL_AUDIT_PROJECT = control_auth.CONTROL_LEDGER_PROJECT
# CR-15: cap on a mutating POST's declared body length. Every payload on this
# surface is small JSON; the largest realistic one is an intake/decision reply
# text, which intake_chat/decision_chat cap far below this anyway.
_MAX_REQUEST_BODY_BYTES = 1 << 20
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

# B21 2026-07-23 (D-R16 §3, scope-amendment escalation; D-B21-2): the bounded
# cap on mid-flight scope.touch widenings PER TASK -- a module constant, not a
# config.Policy field (Policy is frozen for this package, same reasoning as
# HISTORY_REJECTION_WINDOW_SECONDS/RUNAWAY_PERSIST_AFTER_CYCLES above).
# Enforced by counting prior SCOPE_AMENDMENT_APPROVED events for the task (see
# Daemon._scope_amendments_approved): under the cap, a SCOPE_AMENDMENT_REQUEST
# receipt is approved and the task re-dispatched; at the cap, it falls through
# to the SAME hard-BLOCK path CONTRACT-blocked receipts use -- bounded, never
# an infinite loop (mirrors D-R8's bounded reviewer-fix cap).
MAX_SCOPE_AMENDMENTS_PER_TASK = 2

# B24 2026-07-23 (D-R17, transient-failure backoff-resume; D-B24-2/4/5):
# module constants (config.Policy is frozen for this package, same reasoning
# as HISTORY_REJECTION_WINDOW_SECONDS/RUNAWAY_PERSIST_AFTER_CYCLES/
# MAX_SCOPE_AMENDMENTS_PER_TASK above -- daemon.py:358 says outright these
# are "module constants so tests can shrink them for determinism").
#
# TRANSIENT_BACKOFF_SCHEDULE[n]: seconds to wait after the (n+1)-th resume of
# a TRANSIENT-classified INTERRUPTED attempt before the (n+2)-th resume may
# fire (n is 0-based: index 0 governs the wait after the FIRST resume, before
# the second). No wait gates the very first resume (see
# Daemon._transient_backoff_ready) -- the schedule governs waits BETWEEN
# resumes, not before the first one. Backoff readiness is derived from disk
# (newest attempt.resume-N.log mtime), never a new persisted field -- no
# timer wheel exists in this daemon. Backoff granularity floors at
# reconcile_interval_seconds (~30s) -- fine at these magnitudes.
TRANSIENT_BACKOFF_SCHEDULE = (60, 300, 900)
# Bound on how many times the SAME transient-classified attempt may be
# resumed before the daemon gives up on resuming THAT attempt and escalates
# instead: pauses the throttled route (Daemon._provider_pause) and requeues
# the task (Daemon._transient_escalate) so the EXISTING first-healthy-route
# dispatch loop (reconcile.py's queued-task dispatch, unmodified) naturally
# re-routes to the NEXT route in the tier. NOT enforced by
# Daemon._resume_failures' existing poison counter -- that counter skips any
# attempt whose receipt.json exists, and a transient attempt ALWAYS has one
# (the wrapper writes a receipt before every exit/interrupt event) -- so
# without this SEPARATE bound, transient resumes would be unbounded.
MAX_TRANSIENT_RESUMES = 3

# P02 2026-07-21 (docs/plan-logging.md §3 D-L3, §4.4): bootstrap env var for
# the daemon-global log level -- layer 2 of the verbosity precedence chain
# (below the runtime-override file, above a project's own `[logging] level`).
NYXLOOM_LOG_LEVEL_ENV = "NYXLOOM_LOG_LEVEL"


def resolve_level(registry: dict[str, Path] | None = None) -> tuple[str, str]:
    """D-L3 verbosity precedence (highest wins), returning ``(level, source)``:

    1. **runtime override file** -- ``paths.daemon_log_level_path()``, written
       by a live ``POST /api/config/log-level`` flip so it survives a daemon
       respawn (source ``"runtime-file"``).
    2. **``NYXLOOM_LOG_LEVEL`` env** -- compose/infra bootstrap default
       (source ``"env"``).
    3. **``[logging] level``** in the "primary" project's config (source
       ``"config"``) -- the alphabetically-first registered project id is the
       stand-in for "primary" here, the SAME convention ``/api/stream``'s
       bare-``EventSource`` fallback already uses elsewhere in this module
       (``next(iter(sorted(self.registry)), None)``) for "one project must be
       picked and none is more authoritative than the others."
    4. hardcoded **INFO** (source ``"default"``).

    A layer whose value is not a level ``log_module`` recognises is treated
    as ABSENT (falls through to the next layer) rather than raising --
    ``resolve_level()`` always returns something ``log_module.configure()``/
    ``log_module.set_level()`` accept outright, so a corrupted runtime-file
    or a typo'd project toml can never crash daemon bootstrap.
    """
    override_path = paths.daemon_log_level_path()
    if override_path.exists():
        try:
            candidate = override_path.read_text(encoding="utf-8").strip()
        except OSError:
            candidate = ""
        if candidate:
            try:
                log_module._normalize_level(candidate)
            except ValueError:
                pass
            else:
                return candidate, "runtime-file"

    env_candidate = os.environ.get(NYXLOOM_LOG_LEVEL_ENV)
    if env_candidate:
        try:
            log_module._normalize_level(env_candidate)
        except ValueError:
            pass
        else:
            return env_candidate, "env"

    if registry:
        primary = next(iter(sorted(registry)), None)
        if primary is not None:
            try:
                cfg = config.ProjectConfig.load(registry[primary])
            except Exception:
                cfg = None
            if cfg is not None and cfg.logging_level:
                try:
                    log_module._normalize_level(cfg.logging_level)
                except ValueError:
                    pass
                else:
                    return cfg.logging_level, "config"

    return "info", "default"


# P15 2026-07-15: UI config endpoints (POST-only; GET on these -> 405).
# P18 2026-07-16: /api/decision/reply joins this POST-only set (not a config
# mutation, but the same GET->405 guard applies).
# P30 2026-07-16: /api/intake joins it too -- the ONE sanctioned write path
# into intake_chat.advance_intake. (2026-08-02: the words "loopback-only like
# the rest of this surface" were struck here -- untrue since P38 deployed
# NYXLOOM_HTTP_BIND=0.0.0.0.)
# CR-15 2026-08-02: this table is now the authentication surface, not just the
# GET->405 set. `_handle_post` requires a valid operator credential for every
# member before it reads a body, so membership here means "authenticated
# mutation" and nothing in this set is reachable without a credential.
# P02 2026-07-21: /api/config/log-level joins it too (D-L3 runtime control).
_CONFIG_POST_PATHS = frozenset({
    "/api/config/policy", "/api/config/pause", "/api/config/tier",
    "/api/decision/reply", "/api/intake", "/api/config/log-level",
    # FN-6 2026-07-24: promote a finding to an interactive intake conversation
    "/api/finding/promote",
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

# CR-05f: the carve outcome vocabulary is DEFINED in `effects_carve` and
# re-exported here for the exit consumer CR-05e still owns. One definition:
# two copies of a closed vocabulary are two things that can disagree, and the
# disagreement would be silent -- an outcome the carver reports and the
# consumer does not recognise reads as "no outcome".
_CARVE_OUTCOMES = effects_carve._CARVE_OUTCOMES
_RESCOPE_OUTCOME = effects_carve._RESCOPE_OUTCOME

# F018 P3c (plan §2.1): the two carver TURN MODES that carry handoff-
# authoring WRITE AUTHORITY -- every other mode (bootstrap/merge-feed/
# targeted-intake/recover/compact) is read-only and never produces a
# CarverTurnResult envelope or a CARVER_PROPOSAL_RECORDED event. Only
# "carve" is reachable today (the normalize branch in
# _execute_carve_via_session_resume); "repair-proposal" has no emitting
# ResumeCarverSession mode wired in reconcile.py yet (see
# _carve_proposal_repair_escalations' own docstring) -- included here so
# _consume_carver_session_exit's write-authority check needs no future
# edit when that mode is wired.
_CARVE_WRITE_AUTHORITY_MODES = frozenset({"carve", "repair-proposal"})


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
    # F007 2026-07-27 (gap-engine wave 2, GAP2): the carver's BLIND per-task
    # judgments from a gap-audit carve's verdict-audit section (see
    # _verdict_audit_section_lines) -- {"task_id", "judgment", "rationale"}
    # per sampled COMPLETED task. Empty for every pre-GAP2 report and every
    # report from a project with verdict_audit_sample_size == 0 (the field
    # is simply never populated), so to_dict()/from_dict() stay additive:
    # existing exact-equality assertions on CarveSummary/CARVE_OUTCOME
    # payloads are unaffected (_consume_carve_exit only adds the disputes
    # key to the emitted event when this list is non-empty).
    verdict_audit: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "carved": [dict(c) for c in self.carved],
            "review_reflection": self.review_reflection,
            "headroom_estimate": self.headroom_estimate,
            "headroom_rationale": self.headroom_rationale,
            "outcome": self.outcome,
            "verdict_audit": [dict(v) for v in self.verdict_audit],
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
        verdict_audit_raw = d.get("verdict_audit") or []
        verdict_audit = [
            {
                "task_id": str(v.get("task_id", "")),
                "judgment": str(v.get("judgment", "")),
                "rationale": str(v.get("rationale", "")),
            }
            for v in verdict_audit_raw if isinstance(v, dict)
        ]
        return cls(
            carved=carved,
            review_reflection=str(d.get("review_reflection", "")),
            headroom_estimate=int(d.get("headroom_estimate", 0) or 0),
            headroom_rationale=str(d.get("headroom_rationale", "")),
            outcome=str(d.get("outcome", "CANDIDATES_READY")),
            verdict_audit=verdict_audit,
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
        # CR-15: the control-plane trust root is instance state.  Only the
        # handle is built here (no I/O in __init__ -- `nyxloom tick` builds a
        # Daemon too and never serves HTTP); _start_http bootstraps the file,
        # and every mutation re-reads it so an atomic CLI rotation invalidates
        # the old credential immediately, with no cache to expire.
        self._control_auth = control_auth.CredentialStore(paths.daemon_dir())
        self.http_port: int = 0
        self.http_bind: str = ""
        self._stop_event = threading.Event()
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._cmd_listener: commands.CommandListener | None = None
        # Daemon memory: disposable, rebuilt on restart.
        # (monotonic_ts, ok, bounded_detail, probe_fault_code). CR-02a added
        # the fourth element: a memoized PROBE FAULT must keep reporting its
        # degradation, or the TTL alone silently "recovers" it next pass.
        self._probe_memo: dict[str, tuple[float, bool, str, str]] = {}
        self._stall_cache: dict[str, str | None] = {}
        self._decisions_seen: dict[str, dict[str, str]] = {}
        # P44 2026-07-16 (anti-runaway self-correction): consecutive-pass
        # streak per "{project}:{RunawaySignal.key}" -- disposable, same
        # convention as _stall_cache's two-pass CPU cache above. Drives the
        # graduated remedy (see _apply_watchdog); the human-facing
        # escalation itself is deduped via the persisted event log instead
        # (restart-safe), not via this dict.
        self._runaway_streak: dict[str, int] = {}
        # F018 P3d: set of project_ids that already got their enablement-guard
        # WARN in this daemon instance (emit once per daemon lifetime).
        self._carver_enablement_warned: set[str] = set()
        # CR-05a: the effect boundary. The daemon holds the ports and the
        # registry; NO effector holds a reference back to this object, which
        # is what stops "moved out of the god object" from meaning "still
        # reaches into it". Background-work registries (gate verify, post-
        # merge gate) now live on the effector that owns the work rather than
        # as four more attributes here.
        self._ports = effects.EffectPorts.system()
        self._provider_backoff = effects.ProviderPauseRegistry(self._ports.clock)
        self._gates = effects_gates.GateEffector(self._ports)
        self._lifecycle = effects_lifecycle.LifecycleEffector(
            self._ports, self._provider_backoff)
        self._attempt = effects_attempt.AttemptEffector(self._ports)
        self._carver = effects_carver.CarverEffector(self._ports)
        self._carve = effects_carve.CarveEffector(
            self._ports, self._carver, self._lifecycle)
        self._exit = effects_exit.ExitEffector(
            self._ports, self._carve, self._carver, self._lifecycle)
        self._review = effects_review.ReviewEffector(self._ports)
        self._merge = effects_merge.MergeEffector(self._ports)
        self._registry = self._build_registry()
        # CR-02a 2026-08-03 (authoritative snapshot fail-closed audit): the
        # audit produced by the CURRENT pass's fan-in, per project. Set by
        # _build_input, cleared by run_pass's `finally`. The effect boundary
        # (_dispatch_admissible) consults it so an authoritative fault refuses
        # a launch even if some future caller reaches _execute without going
        # through run_pass's own gate. Absent == "no fan-in has run in this
        # call stack" (an operator-initiated path such as
        # dispatch_targeted_carve), which is NOT the same as "clean" -- see
        # _dispatch_admissible for why absence is permitted there.
        self._snapshot_audit: dict[str, snapshot.SnapshotAudit] = {}

    # -- the effect registry (CR-05a) --------------------------------------

    #: CR-05e: EMPTY. Every action type is owned by an effector module, so
    #: there is no legacy list, no ladder, and no shim. The field remains so
    #: `effects.LEGACY_HANDLER_BUDGET` has something to be zero against -- a
    #: ratchet with nothing to count is a ratchet nobody notices breaking.
    _LEGACY_ACTIONS: tuple[tuple[str, str, str], ...] = ()

    def _build_registry(self) -> effects.EffectRegistry:
        """One handler per action type, checked here rather than at first use.

        ``require_covers`` is what turns "an action class was added to the
        planner and nobody wrote its effect" from a TICK_ERROR on the first
        pass that plans it into a construction failure -- which is the
        difference between a defect the operator sees and one the event log
        absorbs.
        """
        registry = effects.EffectRegistry()
        for spec in effects_lifecycle.specs(self._lifecycle):
            registry.register(spec)
        for spec in effects_gates.specs(self._gates):
            registry.register(spec)
        for spec in effects_attempt.specs(self._attempt):
            registry.register(spec)
        for spec in effects_carver.specs(self._carver):
            registry.register(spec)
        for spec in effects_carve.specs(self._carve):
            registry.register(spec)
        for spec in effects_exit.specs(self._exit):
            registry.register(spec)
        for spec in effects_review.specs(self._review):
            registry.register(spec)
        for spec in effects_merge.specs(self._merge):
            registry.register(spec)
        registry.require_covers(reconcile.Action.__subclasses__())
        return registry

    def _effect_context(self, project: str, cfg: ProjectConfig,
                        states: dict[str, TaskStateFile]) -> effects.EffectContext:
        # CR-05b: the pass's snapshot verdict travels ON the context. The
        # SHELL owns it -- `run_pass` records it and clears it in a `finally`
        # -- and hands it down, so an effector consults THIS pass's verdict
        # and cannot reach one that outlived its pass.
        return effects.EffectContext(project=project, cfg=cfg, states=states,
                                     ports=self._ports,
                                     snapshot_audit=self._snapshot_audit.get(project))

    # -- lifecycle ------------------------------------------------------

    def run(self) -> None:
        # P02 (D-L3 §4.4): configure logging FIRST, before anything else in
        # the boot sequence, so every subsequent line -- including the
        # pidfile-conflict RuntimeError's own callers -- runs under the
        # resolved level. resolve_level reads self.registry for layer 3
        # ([logging] level in the primary project's config).
        level, level_source = resolve_level(self.registry)
        log_module.configure(level, paths.logs_dir())
        # NB: field name deliberately NOT "level" -- structlog.stdlib.
        # add_log_level unconditionally overwrites event_dict["level"]
        # with the record's own SEVERITY name (here always "info"), so a
        # same-named custom field would be silently clobbered.
        log.info("daemon started", version=__version__, effective_level=level,
                  level_source=level_source, projects=sorted(self.registry))

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
            self._render_dashboard_on_startup()
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

    def _render_dashboard_on_startup(self) -> None:
        try:
            render.render_all(self.registry)
        except Exception as exc:
            log.warning("dashboard startup render failed", error=repr(exc))

    # -- one reconcile pass -----------------------------------------------

    def run_pass(self, project: str) -> int:
        """One reconcile pass; returns number of actions executed.

        CR-02a (fail-closed authoritative snapshot): the pass is now built in
        two acquisition phases around ONE ``snapshot.SnapshotBuilder``.

        Phase A acquires the three facts every later acquisition is expressed
        in terms of -- project config, projected task state, and the event log
        -- BEFORE anything mutates state. If any of them is unavailable the
        pass emits one ``SNAPSHOT_UNAVAILABLE`` and returns 0 having touched
        nothing at all.

        Phase B (inside ``_build_input``) acquires the remaining authoritative
        domains -- decisions, handoffs, lint, leases, routes, git facts,
        receipts, artifact digests -- plus the advisory ones. If ANY
        authoritative input is not OK, ``plan_project`` is never called, no
        action is executed, and the pass emits exactly one
        ``SNAPSHOT_UNAVAILABLE`` naming every failed source with its reason
        code and provenance.

        The early-mutation helpers between the phases (`_reconcile_decisions`,
        `_transient_escalate`, `_carve_proposal_repair_escalations`) run on
        phase-A facts only. They record decision status and requeue a
        transient-throttled attempt; none of them launches a process, merges,
        or authorizes a gate, so running them before phase B completes cannot
        produce an irreversible effect from an incomplete snapshot.
        """
        try:
            root = self.registry[project]
            builder = snapshot.SnapshotBuilder()
            cfg_in = builder.authoritative(
                "config", lambda: config.ProjectConfig.load(root),
                provenance=snapshot.Provenance("config-file", str(root)))
            states_in = builder.authoritative(
                "state", lambda: storage.list_states(project),
                provenance=snapshot.Provenance("state-store", project))
            events_in = builder.authoritative(
                "event_log", lambda: tuple(storage.iter_events(project)),
                provenance=snapshot.Provenance("event-log", project))
            phase_a = builder.audit()
            if not phase_a.permits_effects:
                # Nothing has been read successfully enough to even notify
                # from, so this emits the bare event and stops.
                self._emit_snapshot_unavailable(
                    project, cfg_in.value if cfg_in.ok else None, {}, phase_a)
                return 0
            cfg = cfg_in.require()
            states = states_in.require()
            events = events_in.require()
            appended: list[Event] = []

            appended.extend(self._reconcile_decisions(project, cfg, states,
                                                       builder=builder, events=events))
            if not builder.audit().permits_effects:
                audit = builder.audit()
                self._emit_snapshot_unavailable(project, cfg, states, audit)
                return 0
            # B24 2026-07-23 (D-R17, D-B24-5/6): the at-cap escalation for
            # TRANSIENT-classified INTERRUPTED attempts. Runs here (same
            # early-mutation timing as _reconcile_decisions above, BEFORE
            # _build_input/plan_project) so a requeued task's fresh
            # DispatchImplementer can fire in this SAME pass.
            appended.extend(self._transient_escalate(project, cfg, states))
            # F018 P3b: bounded repair-count escalation for invalid carve
            # proposals (plan §4.1) -- same early-mutation timing as the two
            # calls above; a no-op ([]) whenever the carver-session feature
            # is off (_carver_session's own MASTER GATE).
            appended.extend(self._carve_proposal_repair_escalations(project, cfg, states,
                                                                     events=events))

            inp = self._build_input(project, cfg, states, builder=builder, events=events)
            audit = builder.audit()
            self._snapshot_audit[project] = audit
            # Advisory degradation is durable and de-duplicated, and it is
            # recorded whether or not the pass proceeds -- an advisory fault
            # alongside an authoritative one must not be swallowed by the
            # fail-closed return.
            appended.extend(self._record_snapshot_degradation(project, cfg, states, events, audit))
            if inp is None or not audit.permits_effects:
                self._emit_snapshot_unavailable(project, cfg, states, audit)
                if appended:
                    render.render_after_event(self.registry)
                return 0
            actions = reconcile.plan_project(inp)
            # P03 (D-L5): plan_project stays pure (no clock/IO/logger import)
            # but its PlanResult return value optionally carries `.trace` --
            # a pure ReconcileTrace of breadcrumbs recording WHY it made each
            # decision this pass. THIS is the only place that trace ever
            # touches the logger: one DEBUG record per breadcrumb, with
            # `project` bound. `getattr` degrades gracefully when a test (or
            # any caller) stubs plan_project to return a bare list with no
            # `.trace` attribute -- there is simply nothing to flush.
            trace = getattr(actions, "trace", None)
            if trace is not None:
                with bind(project=project):
                    for note in trace.breadcrumbs:
                        log.debug("reconcile-trace", kind=note.kind, task=note.task_id, detail=note.detail)
            actions, watchdog_events = self._apply_watchdog(
                project, cfg, states, actions, inp.project_paused, events=events)
            appended.extend(watchdog_events)
            for action in actions:
                # P62 2026-07-20 (A10, M12): per-action isolation. The action
                # loop was inside ONE try/except spanning the whole pass, so a
                # single raising executor (a routes.toml KeyError, a receipt
                # race, an IndexError) aborted every REMAINING action that pass
                # -- starving unrelated tasks. Isolate each action: log the
                # failure as a TICK_ERROR and continue with the rest. The
                # outer try/except still covers pass-level setup (plan_project,
                # _build_input, watchdog).
                try:
                    appended.extend(self._execute(project, cfg, states, action))
                except Exception as action_exc:  # census: cleanup/containment (CR-02b)
                    detail = f"action {type(action).__name__}: {repr(action_exc)[:400]}"
                    try:
                        ev = storage.append_and_apply(
                            project, {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
                            type=EventType.TICK_ERROR, payload={"error": detail})
                        # Reporting a failure must never itself become one: the
                        # remaining actions are independent of this one, and a
                        # dead notification transport is not a reason to abandon
                        # them. Both handlers are containment, not authority --
                        # neither can turn a refusal into a permission, because
                        # the effect has already failed.
                        try:
                            notify.notify_event(cfg, {}, ev)
                        except Exception:  # census: cleanup/containment (CR-02b)
                            pass
                    except Exception:  # census: cleanup/containment (CR-02b)
                        pass
            # CR-05a: drain every registered family's finished background work
            # ONCE PER PASS, on this thread. The drains are declared on the
            # handler specs rather than called by name here, so a family added
            # later (CR-16's liveness probes) joins this loop by registering
            # rather than by editing it -- and a family that forgets to
            # register a drain has its results sit in a queue nobody reads,
            # which its own tests catch, instead of quietly working because
            # someone remembered to add a line here.
            appended.extend(self._registry.drain(
                self._effect_context(project, cfg, states)))
            # P05a (§5): "per-pass counts ... -> DEBUG" -- one summary line
            # per reconcile pass (the reconcile-trace breadcrumbs above
            # already cover the per-decision "guard evals" half of this
            # rubric bullet).
            log.debug("pass-summary", project=project, actions=len(actions), events=len(appended))
            if appended:
                render.render_after_event(self.registry)
            return len(actions)
        except Exception as exc:  # census: process-boundary translation (CR-02b)
            # The pass-level net. It reports and returns 0 -- it never lets a
            # partially-applied pass look like a completed one. Distinct from
            # the fail-closed SNAPSHOT_UNAVAILABLE return above: that one names
            # WHICH authoritative source was missing, this one only knows that
            # something outside the fan-in raised, which is why CR-16 watches
            # TICK_ERROR streaks rather than treating one as actionable.
            detail = repr(exc)[:500]
            try:
                ev = storage.append_and_apply(
                    project, {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
                    type=EventType.TICK_ERROR, payload={"error": detail},
                )
                try:
                    cfg2 = config.ProjectConfig.load(self.registry[project])
                    notify.notify_event(cfg2, {}, ev)
                except Exception:  # census: cleanup/containment (CR-02b)
                    pass
            except Exception:  # census: cleanup/containment (CR-02b)
                pass
            return 0
        finally:
            # The audit describes THIS pass only. Leaving it behind would let
            # a later operator-initiated effect consult a stale verdict.
            self._snapshot_audit.pop(project, None)

    # -- snapshot fan-in reporting (CR-02a) --------------------------------

    def _emit_snapshot_unavailable(
        self, project: str, cfg: ProjectConfig | None,
        states: dict[str, TaskStateFile], audit: snapshot.SnapshotAudit,
    ) -> None:
        """The ONE actionable durable event a fail-closed pass emits.

        Exactly one per affected pass no matter how many sources failed: the
        payload carries every failure, each with its stable reason code and
        provenance, deterministically ordered by source name (see
        ``SnapshotAudit.event_payload``). Emitting one per failed source would
        turn a single unreadable state directory into an N-event storm that
        buries the actionable signal -- the exact shape `watchdog.py` and
        `reference/DOCTRINE.md` were written to prevent.

        Best-effort by construction: if the event store itself is the thing
        that is broken, the append cannot succeed and the WARNING log line is
        the only remaining channel. That is why the log line carries the same
        summary rather than pointing at the event.
        """
        payload = audit.event_payload()
        log.warning(
            "snapshot-unavailable", project=project,
            sources=audit.summary(), digest=payload["digest"],
        )
        if cfg is None:
            # No config means notification routing is unknown too; append
            # directly rather than going through _append_ev.
            try:
                storage.append_and_apply(
                    project, {}, actor=Actor(ActorKind.TICK, "nyxloomd"),
                    type=EventType.SNAPSHOT_UNAVAILABLE, payload=payload)
            except Exception as exc:  # census: cleanup/containment (CR-02a)
                # The store is unreachable; there is no further channel and
                # nothing this method could do would make the pass LESS
                # closed. It has already executed zero effects.
                log.error("snapshot-unavailable append failed",
                           project=project, error=snapshot.bounded_detail(repr(exc)))
            return
        self._append_ev(project, cfg, states, EventType.SNAPSHOT_UNAVAILABLE, payload,
                         task_id=None)

    def _event_log(self, project: str) -> snapshot.SnapshotInput[tuple[Event, ...]]:
        """THE typed acquisition of a project's event log.

        CR-02a: before this, twenty-seven separate helpers each wrapped
        ``list(storage.iter_events(project))`` in its own broad ``except`` and
        chose its own fail direction -- ``[]``, ``False``, ``None``, ``0.0``,
        ``frozenset()``. Several of those directions were fail-OPEN (an
        unreadable log reported "no runaway escalated yet", "no proposal
        admitted yet", "no diagnosis in flight"). There is now exactly one
        acquisition, one class (AUTHORITATIVE), and one reason code.
        """
        return snapshot.acquire(
            "event_log", snapshot.InputClass.AUTHORITATIVE,
            lambda: tuple(storage.iter_events(project)),
            provenance=snapshot.Provenance("event-log", project))

    def _require_events(self, project: str,
                         events: Sequence[Event] | None) -> Sequence[Event]:
        """The pass's already-acquired event log, or a fresh typed acquisition.

        Callers inside the fan-in pass the tuple they already hold. Callers at
        the EFFECT boundary (an executor reached outside a reconcile pass, or
        an operator-initiated verb) pass nothing and get a fresh read that
        raises :class:`snapshot.SnapshotUnavailable` on failure -- which
        `run_pass`'s per-action isolation records as a TICK_ERROR and which
        aborts exactly that one effect. What it can never do is hand back a
        benign-looking empty log.
        """
        if events is not None:
            return events
        return self._event_log(project).require()

    def _record_snapshot_degradation(
        self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
        events: Sequence[Event], audit: snapshot.SnapshotAudit,
    ) -> list[Event]:
        """Emit ``SNAPSHOT_DEGRADED`` when the advisory degradation set CHANGES.

        Advisory faults are allowed to persist while progress continues, so a
        per-pass event would storm. De-duplication is on the audit digest
        (name+status+reason only -- never the run-specific detail text), read
        back from the durable log rather than from daemon memory, so it
        survives a restart and replays identically.

        The recovery edge is emitted too: when the last recorded digest was a
        degraded one and the current set is empty, one event with an empty
        ``degraded`` list records the recovery. Without it the dashboard's
        last word on the project would remain the stale degradation -- a
        false-dirty latch, the mirror image of the false-clean latch this
        package exists to remove.
        """
        current = snapshot.SnapshotAudit(audit.degradations())
        last_digest: str | None = None
        for ev in events:
            if ev.type is EventType.SNAPSHOT_DEGRADED:
                last_digest = str((ev.payload or {}).get("digest") or "")
        digest = current.digest()
        if last_digest is None:
            # Never recorded: only onset is newsworthy, not a clean start.
            if not current.inputs:
                return []
        elif digest == last_digest:
            return []
        payload = current.event_payload()
        payload["summary"] = current.degradation_summary()
        if current.inputs:
            log.warning("snapshot-degraded", project=project,
                         sources=current.degradation_summary(), digest=digest)
        else:
            log.info("snapshot-degraded-cleared", project=project, digest=digest)
        return [self._append_ev(project, cfg, states, EventType.SNAPSHOT_DEGRADED,
                                 payload, task_id=None)]

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
        events = self._carve.carve_dispatch(
            self._effect_context(project, cfg, states), action)
        if events:
            render.render_after_event(self.registry)
        return events

    def _reconcile_decisions(self, project: str, cfg: ProjectConfig,
                              states: dict[str, TaskStateFile],
                              *, builder: snapshot.SnapshotBuilder | None = None,
                              events: Sequence[Event] | None = None) -> list[Event]:
        """Turn decision-inbox status changes into DECISION_* events.

        CR-02a: the inbox is AUTHORITATIVE in both directions and used to fail
        open in one of them. A `DECISION_RESOLVED` that never fires leaves
        tasks held (safe); a `DECISION_OPENED` that never fires leaves a task
        running that a human meant to stop -- and the old
        ``except Exception: events = []`` produced exactly that from an
        unreadable or malformed inbox. Both the reconcile pass and the
        seen-status refresh are now typed acquisitions: on failure this
        returns [] WITHOUT emitting anything, and the fan-in audit that the
        caller checks immediately afterwards stops the pass.

        ``builder`` is optional so the method stays directly callable (tests,
        and any future caller that only wants the events); without it the old
        signature and a self-contained audit still hold, and a fault simply
        yields no events.
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()
        seen = self._decisions_seen.setdefault(project, {})
        out: list[Event] = []
        inbox_path = cfg.root / cfg.decisions_inbox
        prov = snapshot.Provenance("decisions-inbox", str(cfg.decisions_inbox))
        planned_in = b.authoritative(
            "decision_reconcile",
            lambda: decisions.reconcile_decisions(cfg, states, seen),
            provenance=prov)
        # The seen-status refresh is the dedup cursor for the NEXT pass; a
        # failure to update it re-fires every DECISION_OPENED forever, so it
        # is acquired (and fails closed) rather than swallowed.
        parsed_in = b.authoritative(
            "decisions_inbox",
            lambda: (decisions.parse_inbox(inbox_path.read_text(encoding="utf-8"))
                     if inbox_path.exists() else []),
            provenance=prov)
        if not planned_in.ok or not parsed_in.ok:
            return out
        push_failures: list[str] = []
        for ev_type_str, decision_id in planned_in.require():
            out.append(self._append_ev(project, cfg, states, EventType(ev_type_str), {},
                                        decision_id=decision_id))
            if ev_type_str == "DECISION_OPENED":
                # P18: additional actionable push to the feedback channel,
                # in ADDITION to the normal notifications-channel push
                # notify.notify_event already sent above via _append_ev.
                # A feedback-channel push that fails must not undo the durable
                # DECISION_OPENED that has already been appended.
                try:
                    decision_chat.notify_decision_opened(cfg, decision_id)
                except Exception as exc:  # census: advisory-degradation (CR-02b)
                    push_failures.append(f"{decision_id}:{type(exc).__name__}")
        if push_failures:
            # ONE aggregate descriptor: descriptor names are unique per audit,
            # and N failed pushes are one degradation of one channel.
            b.add(snapshot.SnapshotInput.failed(
                "decision_feedback_push", snapshot.InputClass.ADVISORY,
                snapshot.Reason.SOURCE_ERROR,
                provenance=snapshot.Provenance("notify", "feedback-channel"),
                detail=" ".join(sorted(push_failures))))
        for d in parsed_in.require():
            seen[d.id] = d.status
        return out

    # -- input building ----------------------------------------------------

    def _build_input(self, project: str, cfg: ProjectConfig,
                      states: dict[str, TaskStateFile],
                      *, builder: snapshot.SnapshotBuilder | None = None,
                      events: Sequence[Event] | None = None,
                      ) -> reconcile.ReconcileInput | None:
        """Phase B of the fan-in: acquire every remaining domain, then assemble.

        CR-02a. Returns ``None`` -- never a half-built input -- if any
        AUTHORITATIVE acquisition is not OK. The caller (`run_pass`) reads the
        builder's audit for the reason set; it does not have to inspect the
        returned value to know what went wrong.

        Every independently-acquirable authoritative domain is acquired
        BEFORE the first refusal check, deliberately: short-circuiting on the
        first fault would report one broken source per pass, so an operator
        fixes lint, re-runs, and only then discovers the lease store is down
        too. One outage should produce one complete picture. Only
        acquisitions that genuinely need an earlier value (the provider
        probes need `routes`, `lint_clean` needs both the findings and the
        parsed frontmatters) happen after the check.

        Every authoritative domain named in the CR-02 contract is acquired
        here or in phase A: config and projected state and the event log
        (phase A), decisions (`_reconcile_decisions`), handoffs and refs/lint,
        leases, routes/capabilities, gate evidence, git facts, receipts, and
        artifact digests. Advisory domains -- provider probes, attempt-log
        mtimes, /proc CPU signatures, gap-audit activity counting -- are
        acquired as ADVISORY and may degrade, but the degradation is recorded
        in the same audit and surfaces as a durable event.
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()
        if events is None:
            events_in = b.authoritative(
                "event_log", lambda: tuple(storage.iter_events(project)),
                provenance=snapshot.Provenance("event-log", project))
            if not events_in.ok:
                return None
            events = events_in.require()

        routes_in = b.authoritative(
            "routes", config.Routes.load,
            provenance=snapshot.Provenance("routes-file", str(paths.routes_path())))
        handoffs_in = b.authoritative(
            "handoffs", lambda: list(frontmatter.discover_handoffs(cfg)),
            provenance=snapshot.Provenance("handoff-glob", ",".join(cfg.handoff_globs)))
        lint_in = b.authoritative(
            "lint", lambda: lint.lint_project(cfg),
            provenance=snapshot.Provenance("lint", str(cfg.root)))
        # An unreadable or malformed decisions inbox is not evidence that every
        # decision has been resolved: a task held on D-007 must stay held.
        decisions_in = b.authoritative(
            "decisions_open", lambda: decisions.open_ids(cfg),
            provenance=snapshot.Provenance("decisions-inbox", str(cfg.decisions_inbox)))
        merged_in = self._merged_branches(cfg, states, builder=b)
        head_in = self._head_revision(cfg, builder=b)
        leases_in = self._leases_free(cfg, builder=b)
        log_quiet_seconds, pid_alive, receipts_in = self._attempt_scan(
            project, states, builder=b)
        if not b.audit().permits_effects:
            return None
        routes = routes_in.require()
        findings = lint_in.require()
        decisions_open = decisions_in.require()
        merged_branches = merged_in.require()
        head_revision = head_in.require()
        leases_free = leases_in.require()
        receipts = receipts_in.require()

        frontmatters: dict[str, tuple] = {}
        unparsable: list[str] = []
        for path in handoffs_in.require():
            try:
                fm, _body = frontmatter.parse_handoff(path)
            except Exception as exc:  # census: advisory-degradation (CR-02a)
                # A handoff whose frontmatter will not parse cannot become a
                # task: it is absent from `frontmatters`, so the planner never
                # dispatches it. That is already fail-CLOSED for the handoff
                # itself, so this is not an authoritative fault -- but a
                # silently-vanishing work package is exactly the invisible
                # degradation this package exists to remove, so it is recorded
                # with the file that failed and why.
                unparsable.append(f"{path.name}:{type(exc).__name__}")
                continue
            try:
                relpath = str(path.resolve().relative_to(cfg.root.resolve()))
            except ValueError:
                relpath = str(path)
            frontmatters[fm.id] = (fm, relpath)
        if unparsable:
            b.add(snapshot.SnapshotInput.failed(
                "handoff_frontmatter", snapshot.InputClass.ADVISORY,
                snapshot.Reason.MALFORMED_VALUE,
                provenance=snapshot.Provenance("handoff-file", cfg.project_id),
                status=snapshot.InputStatus.MALFORMED,
                detail=" ".join(sorted(unparsable))))

        # lint_clean is DERIVED from a known-good finding set only. Before
        # CR-02a a lint exception produced `findings = {}`, every lookup
        # returned [], `has_blocking([])` was False, and every task in the
        # project was marked lint_clean=True -- an unreadable lint result
        # authorizing dispatch. There is now no path from a lint fault to a
        # True here: the acquisition above returns None for the whole pass.
        lint_clean: dict[str, bool] = {}
        for fm_id, (_fm, relpath) in frontmatters.items():
            f = findings.get(relpath, [])
            lint_clean[fm_id] = not lint.has_blocking(f)

        pause_mode = self._pause_mode(project)
        project_paused = pause_mode != "run"
        triage_class = self._triage_classes(project, states, events=events)
        gate_diagnosis_pending, gate_diagnosis_attempts = self._gate_diagnosis_state(
            project, cfg, states, events=events)
        provider_ok = self._provider_ok(routes, builder=b)
        stall_confirmed = self._confirm_stall(states, log_quiet_seconds, pid_alive, cfg)
        resume_failures = self._resume_failures(project, states, cfg.policy.resume_progress_grace_seconds)
        transient_backoff_ready = self._transient_backoff_ready(project, states)
        budget_remaining = self._budget_remaining(cfg, states)
        merge_history, carve_outcomes, review_rejections_by_area, blocked_underspecified_count = \
            self._history(project, events=events)
        ratchet_already_open = self._ratchet_already_open(project, events=events)
        roadmap_exhausted_open = self._roadmap_exhausted_open(project, events=events)
        # P44 2026-07-16 (anti-runaway self-correction): reuse the existing
        # _spec_attention_recently_emitted debounce backstop as the SOURCE of
        # these three dedup flags (it already implements exactly
        # _ratchet_already_open's convention, generalized by reason) -- it
        # remains a belt-and-braces backstop at emission time too (see
        # _execute's SpecAttention branch), but is no longer the ONLY guard.
        rejections_already_open = self._spec_attention_recently_emitted(
            project, "rejections", events=events)
        carve_outcome_already_open = self._spec_attention_recently_emitted(
            project, "carve-outcome", events=events)
        blocked_underspecified_already_open = self._spec_attention_recently_emitted(
            project, "blocked-underspecified", events=events)
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
        # F018 P2b-A2: carver-snapshot input surface. carver_session is the
        # MASTER GATE -- _carver_session returns None unless
        # cfg.carve.session == "project-persistent" (default "fresh"), and
        # every downstream field defaults empty when it is None, so
        # ReconcileInput is byte-identical to pre-A2 whenever the feature is
        # off (see reconcile.plan_project's own top-level
        # `if inp.carver_session is not None` gate).
        carver_session_snap = self._carver_session(project, cfg, events=events)
        pending_carver_feeds = self._pending_carver_feeds(
            project, cfg, carver_session_snap, events=events)
        # F018 P3b: validated_carve_proposals is now DERIVED (was hardcoded
        # () by A2) -- see _validated_carve_proposals for the full §4.1
        # validation pipeline, the CONCERN-1 generation filter, and (AD1
        # fix) the CARVER_PROPOSAL_ADMITTED-marker exclusion (this builder
        # is the SOLE authority; the pure planner does not re-check any of
        # it).
        validated_carve_proposals = self._validated_carve_proposals(
            project, cfg, carver_session_snap, builder=b, events=events)
        # F018 AD3: structurally-invalid proposals for the current generation
        # the warm session should REPAIR (re-emit correctly) before ingesting
        # new feeds. Gated to `1 <= invalid < max_proposal_repairs` so it
        # composes with P3b's ceiling escalation (never double-fires).
        pending_carve_repairs = self._pending_carve_repairs(
            project, cfg, carver_session_snap, events=events)
        # NO second `permits_effects` guard here. A trailing re-check stood at
        # this line and could never fire: every AUTHORITATIVE acquisition
        # happens at or above the guard that protects the `.require()` calls,
        # and everything between them (provider probes, handoff frontmatter,
        # carve-proposal artifacts) is ADVISORY by construction. An unreachable
        # guard is not free -- it reads as protection that is not there, and it
        # is a line no test can honestly cover. The invariant it was reaching
        # for is asserted instead by
        # test_snapshot_faults.test_the_authoritative_input_set_is_closed,
        # which fails the moment an acquisition below the guard is classified
        # authoritative. Add one, and that test tells you to move the guard.
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
            transient_backoff_ready=transient_backoff_ready,
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
            head_revision=head_revision,
            triage_class=triage_class,
            gate_diagnosis_pending=gate_diagnosis_pending,
            gate_diagnosis_attempts=gate_diagnosis_attempts,
            days_since_test_health_carve=self._days_since_test_health_carve(
                project, events=events),
            days_since_gate_verify=self._days_since_gate_verify(project, events=events),
            changed_lines_since_gap_audit=self._changed_lines_since_gap_audit(
                project, cfg, builder=b, events=events),
            carver_session=carver_session_snap,
            pending_carver_feeds=pending_carver_feeds,
            # no source events yet -- human-intake package (#17) will populate this
            pending_human_intakes=(),
            validated_carve_proposals=validated_carve_proposals,
            pending_carve_repairs=pending_carve_repairs,
            snapshot_audit=b.audit(),
        )

    # CR-05b: the launch primitives below are DELEGATES. Their bodies moved
    # to `effects_dispatch`, where the four families that launch an agent
    # share them as plain functions over the effect context instead of
    # sharing an object.

    def _pause_mode(self, project: str) -> str:
        return effects_dispatch.pause_mode(self._ports.files, project)

    def _merged_branches(self, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                          *, builder: snapshot.SnapshotBuilder | None = None,
                          ) -> snapshot.SnapshotInput[set[str]]:
        """AUTHORITATIVE git fact: which branches are already merged into main.

        CR-02a: was ``except Exception: pass``, which produced a PARTIAL set
        built only from projected task state. That is fail-open in the
        direction that matters -- a branch that IS merged but is missing from
        the set reads as unmerged, and an unmerged branch is a merge
        candidate. A git failure is now an unavailable authoritative input and
        the pass stops.
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()

        def _load() -> set[str]:
            out: set[str] = set()
            res = subprocess.run(
                ["git", "-C", str(cfg.root), "branch", "--merged", cfg.default_branch],
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode != 0:
                raise RuntimeError(
                    f"git branch --merged exited {res.returncode}")
            for line in res.stdout.splitlines():
                name = line.strip().lstrip("*").strip()
                if not name:
                    continue
                out.add(name)
                if name.startswith("feat/"):
                    out.add(name[len("feat/"):])
            for tsf in states.values():
                if tsf.state in (TaskState.MERGED, TaskState.VALIDATING, TaskState.COMPLETED):
                    out.add(tsf.task_id)
                    out.add(f"feat/{tsf.task_id}")
            return out

        return b.authoritative(
            "git_merged_branches", _load,
            provenance=snapshot.Provenance("git", f"{cfg.root}#{cfg.default_branch}"),
            reason=snapshot.Reason.PROBE_FAILED)

    def _triage_classes(self, project: str, states: dict[str, TaskStateFile],
                         *, events: Sequence[Event] | None = None) -> dict[str, str]:
        """B4b (D-060 triage Tier-2; D-066): for each task CURRENTLY in
        REVIEW_REJECTED, the frontier reviewer's self-classification of the
        rejection ("fixable"|"architectural"|"incapable"|"product"), read from
        the reject_class stamped on its LATEST REVIEW_RECORDED event. reconcile
        stays pure -- it consumes this precomputed dict rather than reading
        events itself, exactly like review_rejections_by_area.

        Bind to the latest REVIEW_RECORDED of ANY result (not just the latest
        'rejected' one), then classify only if THAT event is a genuine rejection
        carrying a class. This is what prevents stale-class bleed: if a task's
        newest review leg was an infra 'incomplete' failure, or a rejection from an
        older reviewer that stamped no class, the entry is dropped and the task
        falls to reconcile's mechanical attempt-budget path (graceful degradation)
        rather than inheriting a class from a superseded earlier cycle. Events are
        append-only and ordered, so 'latest event' is an inherently current binding
        (no committed-file staleness to guard, unlike _parse_review_verdict).

        CR-02a: the event log is an AUTHORITATIVE input acquired once by the
        fan-in. The former ``except Exception: return {}`` reported "no task
        carries a triage class" from an unreadable log, silently routing every
        rejected task to the mechanical retry path."""
        events = self._require_events(project, events)
        latest_payload: dict[str, dict] = {}
        for ev in events:
            if ev.type is EventType.REVIEW_RECORDED and ev.task_id:
                latest_payload[ev.task_id] = ev.payload or {}
        out: dict[str, str] = {}
        for task_id, payload in latest_payload.items():
            tsf = states.get(task_id)
            if (tsf is not None and tsf.state is TaskState.REVIEW_REJECTED
                    and payload.get("result") == "rejected"):
                cls = payload.get("reject_class")
                # F019 P1b: `transient` (a flaky gate, per D-F019-2) joins the
                # vocabulary so a gate-diagnosis verdict of "transient" is
                # faithfully surfaced. It carries no dedicated route: not
                # product/architectural, it falls through reconcile's triage table
                # to the same plain retry `fixable`/unclassified already take.
                # D-R3 (2026-07-26, refined): `incapable` also joins the
                # vocabulary -- routes like `architectural` (READY_TO_CARVE),
                # but reconcile's triage table keeps the two distinguishable in
                # the transition note so the carve consumer can branch on which
                # one fired (tier bump vs. re-scope).
                if cls in ("fixable", "architectural", "incapable", "product", "transient"):
                    out[task_id] = cls
        return out

    def _gate_diagnosis_state(
        self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
        *, events: Sequence[Event] | None = None,
    ) -> tuple[frozenset[str], frozenset[str]]:
        """F019 P1b (plan-f019-failure-diagnosis.md §P1): compute the two
        gate-diagnosis inputs the pure planner consumes -- (pending_task_ids,
        unconsumed_diagnosis_attempt_ids). reconcile stays pure; this is the
        daemon reading the event log on its behalf, exactly like _triage_classes.

        A task is diagnosis-PENDING when it is CURRENTLY in REVIEW_REJECTED, its
        latest rejection cause is a pre-merge/mutation gate failure (NOT a
        reviewer rejection -- a reviewer verdict supersedes the gate as the
        current cause), its consecutive gate-fail streak has reached
        policy.gate_diagnosis_after_failures, and no diagnosis attempt is already
        in flight for it. The streak is counted since the last PASSING gate -- the
        approving reviews between merge attempts do NOT reset it, so a
        fix-review-approve-remerge-refail cycle correctly counts as two
        consecutive gate failures (what makes a threshold > 1 reachable).

        The unconsumed set is every EXITED REVIEW_INDEPENDENT attempt on such a
        task that carries NO REVIEW_RECORDED binding -- the fresh diagnosis leg.
        The approving review that always precedes a merge-gate failure carries its
        own {approved} binding, so it is never mistaken for an unconsumed
        diagnosis; that invariant is what lets the receipt-exit scan add a
        REVIEW_REJECTED+REVIEW_INDEPENDENT clause without re-consuming a normal
        review (O5).

        CR-02a: gate evidence is AUTHORITATIVE. The former
        ``except Exception: return frozenset(), frozenset()`` reported "no
        diagnosis pending and none in flight" from an unreadable log, which
        both suppressed the diagnosis dispatch and un-suppressed the blind
        mechanical retry it exists to replace -- fail-open in the expensive
        direction.
        """
        threshold = getattr(cfg.policy, "gate_diagnosis_after_failures", 1)
        events = self._require_events(project, events)
        streak: dict[str, int] = {}
        cause_is_gate: dict[str, bool] = {}
        recorded_attempts: set[str] = set()
        for ev in events:
            if ev.type is EventType.REVIEW_RECORDED and ev.task_id:
                # A reviewer verdict is the CURRENT rejection cause only when it
                # is itself a rejection (approved -> MERGE_READY, never a
                # rejection). Either result binds its attempt (idempotency: a
                # bound attempt is a consumed one, so never an in-flight
                # diagnosis).
                if (ev.payload or {}).get("result") != "approved":
                    cause_is_gate[ev.task_id] = False
                if ev.attempt_id:
                    recorded_attempts.add(ev.attempt_id)
            elif ev.type is EventType.GATE_FINISHED and ev.task_id:
                gr = (ev.payload or {}).get("gate_result") or {}
                # Only the pre-merge/mutation gates route a task to
                # REVIEW_REJECTED; a post-merge gate fails on already-published
                # code (-> BLOCKED / auto-revert), never a re-work signal.
                if gr.get("phase") in ("pre-merge", "mutation"):
                    if gr.get("exit_code", 0) != 0:
                        streak[ev.task_id] = streak.get(ev.task_id, 0) + 1
                        cause_is_gate[ev.task_id] = True
                    else:
                        streak[ev.task_id] = 0
        pending: set[str] = set()
        diag_attempts: set[str] = set()
        for task_id, tsf in states.items():
            if tsf.state is not TaskState.REVIEW_REJECTED:
                continue
            if not cause_is_gate.get(task_id, False):
                continue
            if streak.get(task_id, 0) < threshold:
                continue
            unconsumed = [
                a for a in tsf.attempts
                if a.role is Role.REVIEW_INDEPENDENT
                and a.attempt_id not in recorded_attempts
            ]
            if unconsumed:
                # A diagnosis is already in flight -> do NOT re-dispatch. Surface
                # only its EXITED leg (if any) so the receipt-exit scan consumes it
                # even when the wrapper pre-emitted ATTEMPT_EXITED.
                for a in unconsumed:
                    if a.state is AttemptState.EXITED:
                        diag_attempts.add(a.attempt_id)
            else:
                pending.add(task_id)
        return frozenset(pending), frozenset(diag_attempts)

    def _leases_free(self, cfg: ProjectConfig,
                      *, builder: snapshot.SnapshotBuilder | None = None,
                      ) -> snapshot.SnapshotInput[dict[str, bool]]:
        """AUTHORITATIVE: per-mutex "is a slot available".

        A failed exclusivity probe is not evidence that the shared resource is
        free. Before CR-02a this fell back to ``False`` per lease and logged a
        warning -- fail-closed for the tasks that declare that mutex, but
        invisible to the planner, to the event log, and to the operator, and it
        left the *rest* of the pass planning against a snapshot one of whose
        authoritative facts was a guess. It is now a typed authoritative fault:
        the pass stops and says which lease could not be probed.
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()
        names = [mdef.lease_name(cfg.project_id) for mdef in cfg.mutexes.values()]

        def _load() -> dict[str, bool]:
            out: dict[str, bool] = {}
            for mdef in cfg.mutexes.values():
                lease_name = mdef.lease_name(cfg.project_id)
                info = leases.holder_info(lease_name, capacity=mdef.capacity)
                out[lease_name] = any(not slot["held"] for slot in info)
            return out

        return b.authoritative(
            "leases", _load,
            provenance=snapshot.Provenance("lease-store", ",".join(sorted(names))),
            reason=snapshot.Reason.PROBE_FAILED)

    def _provider_ok(self, routes: config.Routes,
                      *, builder: snapshot.SnapshotBuilder | None = None,
                      ) -> dict[str, bool]:
        """ADVISORY: per-route provider preflight.

        A probe that cannot run marks the route NOT ok, which only ever
        *removes* a dispatch option -- it can never authorize one. That is why
        this is advisory rather than authoritative: the degradation reduces
        progress instead of manufacturing it. It must still be visible, so a
        failed probe is recorded as a typed degradation carrying the route ids
        and the exception type (never the probe's raw output, which is a
        provider CLI's stderr and can quote credentials).

        The memo records WHETHER the reading was a probe fault, not just its
        boolean result, so a memoized fault keeps reporting the degradation
        for as long as it is in force. Without that, the degradation appeared
        once and then "recovered" on the very next pass purely because the
        memo answered instead of the probe -- a false recovery, which is the
        same class of lie as a false clean.
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()
        now = time.monotonic()
        out: dict[str, bool] = {}
        failed: list[str] = []
        for route_id, route in routes.routes.items():
            # CR-05a: the pause registry is OWNED by the lifecycle effector
            # that writes it and injected here, where it is only ever read --
            # one writer, one reader, both holding the same instance rather
            # than both reaching for an attribute on the shell.
            paused_until = self._provider_backoff.paused_until(route_id)
            if paused_until is not None and now < paused_until:
                out[route_id] = False
                continue
            memo = self._probe_memo.get(route_id)
            if memo is not None and (now - memo[0]) < PROBE_TTL_SECONDS:
                out[route_id] = memo[1]
                if memo[3]:
                    failed.append(f"{route_id}:{memo[3]}")
                continue
            probed = snapshot.acquire(
                f"provider_probe:{route_id}", snapshot.InputClass.ADVISORY,
                lambda route=route: adapters.probe(route),
                provenance=snapshot.Provenance("provider-probe", route_id),
                reason=snapshot.Reason.PROBE_FAILED)
            fault_code = ""
            if probed.ok:
                ok, detail = probed.require()
            else:
                # A probe that RAISED is a degradation. A probe that cleanly
                # returned False is a healthy reading of an unhealthy
                # provider, and is deliberately NOT recorded here.
                ok, detail = False, probed.detail
                fault_code = probed.reason.value
                failed.append(f"{route_id}:{fault_code}")
            self._probe_memo[route_id] = (
                now, ok, snapshot.bounded_detail(detail), fault_code)
            out[route_id] = ok
        if failed:
            b.add(snapshot.SnapshotInput.failed(
                "provider_probe", snapshot.InputClass.ADVISORY,
                snapshot.Reason.PROBE_FAILED,
                provenance=snapshot.Provenance("provider-probe", "routes"),
                detail=" ".join(sorted(failed))))
        return out

    def _attempt_scan(self, project: str, states: dict[str, TaskStateFile],
                       *, builder: snapshot.SnapshotBuilder | None = None):
        """Per-attempt disk scan: log quiet time, liveness, and receipts.

        Returns ``(log_quiet, pid_alive, receipts_input)``. The receipts half
        is AUTHORITATIVE and typed -- it decides whether an attempt's verdict
        is consumed -- while log mtimes and pid liveness are advisory stall
        heuristics that only ever make the daemon *less* willing to act.
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()
        log_quiet: dict[str, float | None] = {}
        pid_alive: dict[str, bool] = {}
        receipts: dict[str, dict | None] = {}
        malformed_receipts: list[str] = []
        now = time.time()
        for tsf in states.values():
            for att in tsf.attempts:
                if att.state in TERMINAL_ATTEMPT_STATES:
                    # EXITED attempts whose receipt still needs consuming:
                    # implementer exit while task ACTIVE, frontier-review
                    # exit while task is still AWAITING_REVIEW (2026-07-15
                    # deadlock fix), or (P50 2026-07-19: a SEPARATE deadlock,
                    # same shape) a carver exit while its synthetic task is
                    # still ACTIVE. reconcile.py's dispatch_eligible-style
                    # EXITED-while-ACTIVE-and-role-CARVER branch has existed
                    # since P32 (2026-07-16) specifically to plan
                    # EmitAttemptExit for this case -- its own comment there
                    # already documented that "_consume_carve_exit ... only
                    # ever ran off this same re-scan, which didn't cover
                    # CARVER" -- but this scan never actually included the
                    # CARVER case, so has_receipt was always False for a
                    # carve attempt and that branch could never fire. Two
                    # synthetic carve tasks sat ACTIVE forever (permanently
                    # eating a wip slot each) until this fix.
                    if not (att.state == AttemptState.EXITED
                            and ((tsf.state == TaskState.ACTIVE
                                  and att.role == Role.IMPLEMENTER)
                                 or (tsf.state == TaskState.AWAITING_REVIEW
                                     and att.role == Role.REVIEW_INDEPENDENT)
                                 or (tsf.state == TaskState.ACTIVE
                                     and att.role == Role.CARVER)
                                 # B5 2026-07-20: a self_review exit while the
                                 # task is still SELF_REVIEWING -- its verdict
                                 # receipt must reach reconcile so EmitAttemptExit
                                 # is planned (mirror of reconcile.py's tuple).
                                 or (tsf.state == TaskState.SELF_REVIEWING
                                     and att.role == Role.SELF_REVIEW))):
                        continue
                attempt_dir = paths.attempt_dir(project, att.attempt_id)
                receipt_path = attempt_dir / "receipt.json"
                if receipt_path.exists():
                    # CR-02a: an UNREADABLE receipt used to be stored as the
                    # same `None` an ABSENT receipt gets. Downstream, `None`
                    # means "the attempt has not reported yet" -- so a
                    # truncated or half-written receipt read as a still-running
                    # attempt, and its real result (BLOCKED, LIMIT, a scope
                    # amendment) was silently discarded. The two are now
                    # distinguished: absent stays None, malformed becomes an
                    # authoritative fault naming the attempt.
                    try:
                        receipts[att.attempt_id] = json.loads(
                            receipt_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
                        malformed_receipts.append(
                            f"{att.attempt_id}:{type(exc).__name__}")
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
        if malformed_receipts:
            receipts_in: snapshot.SnapshotInput[dict[str, dict | None]] = (
                snapshot.SnapshotInput.failed(
                    "receipts", snapshot.InputClass.AUTHORITATIVE,
                    snapshot.Reason.MALFORMED_VALUE,
                    provenance=snapshot.Provenance("receipt-file", project),
                    status=snapshot.InputStatus.MALFORMED,
                    detail=" ".join(sorted(malformed_receipts))))
        else:
            receipts_in = snapshot.SnapshotInput.ok_value(
                "receipts", snapshot.InputClass.AUTHORITATIVE, receipts,
                provenance=snapshot.Provenance("receipt-file", project))
        b.add(receipts_in)
        return log_quiet, pid_alive, receipts_in

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

    def _transient_backoff_ready(self, project: str,
                                  states: dict[str, TaskStateFile]) -> dict[str, bool]:
        """B24 2026-07-23 (D-R17, D-B24-2): attempt_id -> whether a
        TRANSIENT-classified INTERRUPTED attempt has waited out its
        exponential backoff and may be resumed again THIS pass. Missing
        entries default to True at the reconcile.py call site
        (`.get(attempt_id, True)`) -- an ordinary (non-transient, e.g.
        signal-interrupted) INTERRUPTED attempt is unaffected, byte-
        identical to pre-B24 behavior.

        Readiness is DERIVED from disk, not a persisted field (no timer
        wheel exists in this daemon): the resume count so far is
        `self._next_resume_n(attempt_dir) - 1` (mirrors _resume_failures'
        reliance on the same helper, one glob of attempt.resume-N.log); the
        wait clock starts at the newest such log's mtime (the moment the
        last resume was launched). An attempt that has never been resumed
        (prior_resumes == 0) is always ready -- TRANSIENT_BACKOFF_SCHEDULE
        governs the wait BETWEEN resumes, not before the first one.

        Once prior_resumes reaches MAX_TRANSIENT_RESUMES this returns False
        FOREVER for that attempt_id (not just "not yet") -- this is what
        keeps reconcile.py's ResumeAttempt gate from ever planning another
        resume for it. That permanent False does NOT by itself move the
        task forward -- Daemon._transient_escalate (run once per pass,
        before this snapshot is even built) is the piece that actually
        gives up on the task's ACTIVE state and re-dispatches elsewhere;
        this helper only ever answers "is a resume of THIS SAME attempt
        allowed right now."""
        out: dict[str, bool] = {}
        now = time.time()
        for tsf in states.values():
            for att in tsf.attempts:
                if att.state != AttemptState.INTERRUPTED:
                    continue
                if att.receipt is None or att.receipt.result != ReceiptResult.TRANSIENT:
                    continue
                attempt_dir = paths.attempt_dir(project, att.attempt_id)
                resume_n = self._next_resume_n(attempt_dir)
                prior_resumes = resume_n - 1
                if prior_resumes == 0:
                    out[att.attempt_id] = True
                    continue
                if prior_resumes >= MAX_TRANSIENT_RESUMES:
                    out[att.attempt_id] = False
                    continue
                newest_mtime: float | None = None
                for n in range(1, resume_n):
                    log_path = attempt_dir / f"attempt.resume-{n}.log"
                    try:
                        mtime = log_path.stat().st_mtime
                    except OSError:
                        continue
                    if newest_mtime is None or mtime > newest_mtime:
                        newest_mtime = mtime
                if newest_mtime is None:
                    # Belt-and-braces: no resume log actually found on disk
                    # despite _next_resume_n counting some -- do not wedge
                    # the attempt on a backoff it cannot time from.
                    out[att.attempt_id] = True
                    continue
                idx = min(prior_resumes - 1, len(TRANSIENT_BACKOFF_SCHEDULE) - 1)
                wait = TRANSIENT_BACKOFF_SCHEDULE[idx]
                out[att.attempt_id] = (now - newest_mtime) >= wait
        return out

    def _transient_escalate(self, project: str, cfg: ProjectConfig,
                             states: dict[str, TaskStateFile]) -> list[Event]:
        """B24 2026-07-23 (D-R17, D-B24-5/6): the at-cap escalation. Once a
        TRANSIENT-classified INTERRUPTED attempt has exhausted
        MAX_TRANSIENT_RESUMES resumes, _transient_backoff_ready above
        already permanently parks it (never plans another ResumeAttempt for
        THIS attempt_id) -- this method is what actually moves the TASK
        forward: pause the throttled route (self._provider_pause, a new
        state='throttled' so it reads distinctly from a genuine LIMIT
        receipt's 'limited' in the event log) and requeue the task
        (ACTIVE->QUEUED, the SAME legal edge the LIMIT branch in
        EmitAttemptExit already uses) so the EXISTING first-healthy-route
        dispatch loop (reconcile.py's queued-task lifecycle dispatch,
        UNMODIFIED) naturally re-routes to the next route in the tier on a
        following dispatch.

        Called early in run_pass (mirrors _reconcile_decisions' pattern:
        BEFORE _build_input/plan_project, mutating `states` in place) so a
        fresh DispatchImplementer can even fire in the SAME pass the
        provider gets paused, exactly like _reconcile_decisions' own
        early-mutation timing.

        Scoped to the task's LATEST attempt + tsf.state == ACTIVE so this
        fires EXACTLY ONCE per stuck attempt: after the transition below,
        the task is no longer ACTIVE (self-limiting -- the same "moves the
        task off its current state" idiom reconcile.py's FAILED/CARVER
        branch already documents), and once a fresh attempt becomes the
        task's latest, this old attempt is no longer tsf.attempts[-1] and is
        never revisited -- it stays INTERRUPTED forever, inert, the SAME
        "stale record" shape a resume-poisoned attempt already has today
        (implementer_record_count's whole reason to exist)."""
        events: list[Event] = []
        for task_id, tsf in states.items():
            if tsf.state != TaskState.ACTIVE or not tsf.attempts:
                continue
            attempt = tsf.attempts[-1]
            if attempt.state != AttemptState.INTERRUPTED or attempt.role != Role.IMPLEMENTER:
                continue
            if attempt.receipt is None or attempt.receipt.result != ReceiptResult.TRANSIENT:
                continue
            attempt_dir = paths.attempt_dir(project, attempt.attempt_id)
            prior_resumes = self._next_resume_n(attempt_dir) - 1
            if prior_resumes < MAX_TRANSIENT_RESUMES:
                continue
            route_id = attempt.route.route_id if attempt.route else None
            events.append(self._transition(
                project, cfg, states, task_id, TaskState.QUEUED,
                f"transient-resume cap ({MAX_TRANSIENT_RESUMES}) reached on route "
                f"{route_id} -- provider-pausing and re-dispatching to the next route"))
            events.extend(self._provider_pause(project, cfg, states, route_id, task_id,
                                                state="throttled"))
        return events

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
        return effects_dispatch.budget_remaining(cfg, states)

    def _dispatch_admissible(self, project: str, cfg: ProjectConfig,
                             states: dict[str, TaskStateFile], kind: str) -> tuple[bool, str]:
        """Execute-time admission, delegated to `effects_dispatch.admissible`.

        Retained as the daemon-side call site for the carve families CR-05d
        still owns; the rule itself, and the reason it lives at the effect
        boundary rather than in the planner, are documented there.
        """
        return effects_dispatch.admissible(
            self._effect_context(project, cfg, states), kind)

    def _history(self, project: str, *, events: Sequence[Event] | None = None):
        """P44 2026-07-16 (anti-runaway self-correction): review_rejections_by_area
        is now WINDOWED (only rejections within HISTORY_REJECTION_WINDOW_SECONDS of
        'now' count) -- the root cause of the 2026-07-16 notification storm. Before
        this fix it counted rejections over the ENTIRE event log and only ever
        increased, so a project that once hit 2 rejections in some area stayed
        >= 2 forever, even if every rejection was months old and long since
        resolved. A time window (not an event-count window like the
        `*_already_open` flags below) is the right shape here: an aged, resolved
        rejection should age OUT regardless of how much OTHER unrelated event
        traffic has or hasn't happened since. P64 2026-07-20 (A12, M16):
        blocked_underspecified_count is now WINDOWED the same way (was
        full-log-forever). merge_history / carve_outcomes remain full-log, then
        sliced; merge_history now carries a REAL progress_units file count
        (A12/D-061), not the structurally-zero placeholder."""
        merge_history: list[tuple[str, int, str]] = []
        carve_outcomes: list[dict] = []
        review_rejections_by_area: dict[str, int] = {}
        blocked_underspecified_count = 0
        events = self._require_events(project, events)
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
                    # P64 2026-07-20 (A12, M16): WINDOW this count, same shape
                    # and rationale as review_rejections_by_area above. It was
                    # a full-log-forever counter, so a project that ever hit a
                    # few contract blockers stayed >= threshold forever and
                    # re-fired SpecAttention('blocked-underspecified') every
                    # time its dedup event scrolled out of the 500-event
                    # window. An aged, resolved blocker must age OUT.
                    age_seconds = (now - ev.timestamp).total_seconds()
                    if age_seconds <= HISTORY_REJECTION_WINDOW_SECONDS:
                        blocked_underspecified_count += 1
        merge_history.reverse()  # most recent first
        return merge_history[:50], carve_outcomes[-20:], review_rejections_by_area, blocked_underspecified_count

    def _ratchet_already_open(self, project: str,
                               *, events: Sequence[Event] | None = None) -> bool:
        """Recent-window dedup flag for SpecAttention('ratchet').

        CR-02a: ``except Exception: return False`` said "not open yet" from an
        unreadable log -- the answer that re-fires the escalation."""
        recent = list(self._require_events(project, events))[-500:]
        return any(ev.type is EventType.SPEC_ATTENTION and ev.payload.get("reason") == "ratchet"
                   for ev in recent)

    def _scope_amendment_files(self, project: str, task_id: str,
                                *, events: Sequence[Event] | None = None) -> list[str]:
        """B21: the granted file path(s) from every approved amendment for
        this task, in approval order, de-duplicated. Fed to build_dispatch's
        `approved_amendments` kwarg -- both the IMPLEMENTER re-dispatch (D-
        B21-1: the widened allowlist reaches the agent via the PROMPT, never
        by rewriting the handoff's scope.touch on disk) and the FRONTIER_
        REVIEW dispatch (so the reviewer does not reject the now-legitimate
        out-of-scope edit)."""
        return effects_dispatch.scope_amendment_files(
            self._require_events(project, events), task_id)

    def _days_since_test_health_carve(self, project: str,
                                       *, events: Sequence[Event] | None = None,
                                       builder: snapshot.SnapshotBuilder | None = None,
                                       ) -> float | None:
        """D-065 (B63 2026-07-20): age in days of the most recent test-health
        carve, feeding ReconcileInput.days_since_test_health_carve so module
        contract item 15's cadence is durable across daemon restarts (the
        event log is the only state that survives one). None = never carved,
        which item 15 reads as "fire" -- enabling the knob is itself the
        request for a first pass.

        Scans the WHOLE log, unlike the [-500:] recent-window dedup helpers
        above, and deliberately so: those answer "is this condition currently
        open", where a stale hit is harmless. This one answers "how long ago",
        and a window that scrolled past the last carve would report None =
        never = FIRE -- turning a 14-day cadence into a carve every pass on
        any busy project. The marker is the structured `carve_kind` key
        _execute_carve_dispatch stamps on the carve's own TASK_CREATED
        (payload-additive; storage's replay reads only payload["statefile"],
        so it cannot cause divergence).

        CR-02a: the log read and the age arithmetic are both AUTHORITATIVE.
        The old code answered 0.0 ("just carved") for BOTH an unreadable log
        and a marker whose timestamp will not subtract. 0.0 suppresses spend,
        which sounds safe -- but it is a PERMANENT false-clean latch: a single
        corrupt marker timestamp silently disables the cadence forever, with
        no event and no operator signal. The log read now fails the pass
        closed; a malformed marker timestamp is recorded as a typed
        MALFORMED authoritative fault (and still answers 0.0, which is
        irrelevant because the pass will not proceed).
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()
        latest = None
        for ev in self._require_events(project, events):
            if ev.type is EventType.TASK_CREATED and ev.payload.get("carve_kind") == "test-health":
                if latest is None or ev.timestamp > latest:
                    latest = ev.timestamp
        if latest is None:
            return None
        age = self._marker_age_days("test_health_carve_marker", latest, b)
        return 0.0 if age is None else age

    @staticmethod
    def _marker_age_days(name: str, stamp: "Any",
                          b: snapshot.SnapshotBuilder) -> float | None:
        """Age in days of a cadence marker, or None with a typed fault.

        A naive/aware datetime mix (or any other unsubtractable timestamp) is
        a MALFORMED authoritative reading of a cadence anchor, not a reason to
        report "just done". Shared by the two cadence helpers so both report
        the same reason code and the same provenance shape.
        """
        try:
            return max(0.0, (utc_now() - stamp).total_seconds() / 86400.0)
        except Exception as exc:  # census: process-boundary translation (CR-02a)
            b.add(snapshot.SnapshotInput.failed(
                name, snapshot.InputClass.AUTHORITATIVE,
                snapshot.Reason.MALFORMED_VALUE,
                provenance=snapshot.Provenance("event-log", name),
                status=snapshot.InputStatus.MALFORMED,
                detail=f"{type(exc).__name__}: {exc}"))
            return None

    def _days_since_gate_verify(self, project: str,
                                 *, events: Sequence[Event] | None = None,
                                 builder: snapshot.SnapshotBuilder | None = None,
                                 ) -> float | None:
        """GA4 2026-07-25 (module contract item 16): age in days of the most
        recent GATE_VERIFY_RECORDED, feeding
        ReconcileInput.days_since_gate_verify so the cadence is durable across
        daemon restarts -- MIRRORS _days_since_test_health_carve above
        exactly, including its fail-safe direction and the "scan the whole
        log, not a recent window" rationale (a window that scrolled past the
        last verify would report None = never = FIRE on every pass of a busy
        project). Unlike the test-health marker (a structured key on a
        TASK_CREATED payload), GATE_VERIFY_RECORDED is its own dedicated
        project-wide event (task_id=None) -- so this scans for the event TYPE
        itself, using the event's own timestamp.

        CR-02a: same treatment as _days_since_test_health_carve above -- an
        unreadable log fails the pass closed, and a marker timestamp that will
        not subtract is a typed MALFORMED authoritative fault rather than a
        silent "just verified" that would disable the cadence forever.
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()
        latest = None
        for ev in self._require_events(project, events):
            if ev.type is EventType.GATE_VERIFY_RECORDED:
                if latest is None or ev.timestamp > latest:
                    latest = ev.timestamp
        if latest is None:
            return None
        age = self._marker_age_days("gate_verify_marker", latest, b)
        return 0.0 if age is None else age

    def _changed_lines_since_gap_audit(self, project: str, cfg: ProjectConfig,
                                        *, events: Sequence[Event] | None = None,
                                        builder: snapshot.SnapshotBuilder | None = None,
                                        ) -> int | None:
        """F007 2026-07-27 (gap-engine, module contract item 17): accumulated
        changed production lines (added+deleted via git diff --numstat) since the
        most recent gap-audit carve, feeding ReconcileInput.changed_lines_since_gap_audit
        so the activity-counted cadence is durable across daemon restarts. None =
        never carved, which item 17 reads as "fire" -- enabling the knob is itself
        the request for a first pass. Returns the sum of added+deleted lines across
        all rows, excluding binary changes (which contribute 0).

        Scans the WHOLE log (like _days_since_test_health_carve), not a recent
        window: a window that scrolled past the last carve would report None =
        never = FIRE -- turning a 2000-line threshold into a carve every pass on
        any busy project. The marker is the structured `carve_kind` key
        _execute_carve_dispatch stamps on the carve's own TASK_CREATED, with the
        additional `head_sha` payload field recording the repo HEAD at that time
        (see _execute_carve_dispatch's payload-writing code).

        Fail-safe on a git failure or missing head_sha is 0 (NOT None): this value
        gates spawning a real carver process, so an I/O error must never be the
        thing that authorizes spend. A missing head_sha is structurally an old
        carve (pre-item-17); returning 0 means "no measurable activity since then",
        which is the safe answer when we cannot measure.

        CR-02a: 0 remains the value, and this stays ADVISORY -- unlike the two
        cadence helpers above, "cannot measure activity" here only ever
        SUPPRESSES a carve and cannot latch: the very next pass re-runs the
        same git diff from the same durable marker, so a transient git failure
        self-heals with no state to unstick. What changes is that the
        suppression is now recorded as a typed degradation instead of only a
        log line, so an operator can see why the gap-audit cadence went quiet.
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()

        def _degrade(reason: snapshot.Reason, detail: str) -> int:
            b.add(snapshot.SnapshotInput.failed(
                "gap_audit_activity", snapshot.InputClass.ADVISORY, reason,
                provenance=snapshot.Provenance("git", f"{cfg.root}#gap-audit"),
                detail=detail))
            return 0

        latest = None
        latest_sha = None
        for ev in self._require_events(project, events):
            if ev.type is EventType.TASK_CREATED and ev.payload.get("carve_kind") == "gap-audit":
                if latest is None or ev.timestamp > latest:
                    latest = ev.timestamp
                    latest_sha = ev.payload.get("head_sha")
        if latest is None:
            return None
        if not latest_sha:
            # Old carve without head_sha, or payload corruption.
            log.warning("gap_audit: marker present but head_sha missing", project=project)
            return _degrade(snapshot.Reason.MALFORMED_VALUE,
                             "gap-audit marker carries no head_sha")
        try:
            # git diff --numstat <sha>..HEAD [--] <paths...>
            # Returns lines with: <added>\t<deleted>\t<path>
            # Binary files show: -\t-\t<path>
            repo_root = str(cfg.root)
            pathspecs = cfg.policy.gap_audit_source_paths or []
            cmd = ["git", "diff", "--numstat", f"{latest_sha}..HEAD"]
            if pathspecs:
                cmd.append("--")
                cmd.extend(pathspecs)
            result = subprocess.run(
                cmd,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                # git diff failed (invalid sha, not a repo, etc.)
                log.warning("gap_audit: git diff failed", project=project,
                           head_sha=latest_sha[:8], returncode=result.returncode)
                return _degrade(snapshot.Reason.PROBE_FAILED,
                                 f"git diff exited {result.returncode}")
            total = 0
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    added_str, deleted_str = parts[0], parts[1]
                    # Binary files show "-" in both fields
                    if added_str != "-":
                        try:
                            total += int(added_str)
                        except ValueError:
                            pass
                    if deleted_str != "-":
                        try:
                            total += int(deleted_str)
                        except ValueError:
                            pass
            return total
        except subprocess.TimeoutExpired:
            log.warning("gap_audit: git diff timeout", project=project, head_sha=latest_sha[:8])
            return _degrade(snapshot.Reason.TIMEOUT, "git diff --numstat timed out")
        except Exception as e:  # census: advisory-degradation (CR-02a)
            log.warning("gap_audit: git diff error", project=project, exc=e)
            return _degrade(snapshot.Reason.SOURCE_ERROR, f"{type(e).__name__}: {e}")

    # CR-05f: the carve surface moved to `effects_carve`. Delegates for the
    # readers the shell still calls -- the input builder's git-fact
    # acquisition, and the two the exit consumer (CR-05e) reads.

    def _head_revision(self, cfg: ProjectConfig, *,
                       builder: snapshot.SnapshotBuilder | None = None):
        # `builder` threaded through: CR-02b derives fan-in membership from
        # this parameter, so dropping it would move the acquisition out of
        # the strictest exception rule in the package.
        return self._carve._head_revision(cfg, builder=builder)

    def _carve_kind(self, states: dict[str, TaskStateFile],
                    task_id: str | None) -> str:
        return self._carve._carve_kind(states, task_id)

    def _verdict_audit_disputes(self, project: str, judgments,
                                 *, events: Sequence[Event] | None = None):
        return self._carve._verdict_audit_disputes(project, judgments,
                                                   events=events)

    # CR-05d: the carver-session surface moved to `effects_carver`. These are
    # DELEGATES for the shell's remaining readers -- and only for the ones it
    # actually still calls. `_carve_in_flight` and `_build_carver_resume_prompt`
    # were written as delegates too and DELETED when the gate found them
    # uncovered: after the move nothing on this side calls them, and an unused
    # forwarding method is decoration that makes the shell look more coupled
    # to the carver than it is -- `_build_input` threads the
    # snapshot and the validated proposals into the planner, and the carve
    # families CR-05f still owns share the sequence counter and the packet
    # builders. The shell may call an effector; the reverse is what the
    # boundary forbids.

    def _carver_session(self, project: str, cfg: ProjectConfig,
                        *, events: Sequence[Event] | None = None):
        return self._carver._carver_session(project, cfg, events=events)

    def _validated_carve_proposals(self, project: str, cfg: ProjectConfig, snap,
                                    *, builder: snapshot.SnapshotBuilder | None = None,
                                    events: Sequence[Event] | None = None):
        # `builder` is threaded through deliberately: CR-02b derives snapshot
        # fan-in membership STRUCTURALLY from a `builder` parameter, so
        # dropping it here would silently move this acquisition out of the
        # strictest exception rule in the package.
        return self._carver._validated_carve_proposals(
            project, cfg, snap, builder=builder, events=events)

    def _pending_carve_repairs(self, project: str, cfg: ProjectConfig, snap,
                                *, events: Sequence[Event] | None = None):
        return self._carver._pending_carve_repairs(project, cfg, snap,
                                                   events=events)

    def _validate_carve_proposal_payload(self, cfg: ProjectConfig, snap,
                                          payload: dict):
        return self._carver._validate_carve_proposal_payload(cfg, snap, payload)

    def _parse_proposal_id(self, cfg: ProjectConfig, proposal_id: Any):
        return self._carver._parse_proposal_id(cfg, proposal_id)

    def _next_carve_seq(self, project: str,
                        *, events: Sequence[Event] | None = None) -> int:
        return self._carver._next_carve_seq(project, events=events)

    def _spine_revisions(self, cfg: ProjectConfig) -> dict[str, str]:
        return self._carver._spine_revisions(cfg)

    def _highest_consumed_feed_sequence(self, project: str,
                                         source_ids: tuple[str, ...],
                                         *, events: Sequence[Event] | None = None):
        return self._carver._highest_consumed_feed_sequence(
            project, source_ids, events=events)

    def _roadmap_exhausted_open(self, project: str,
                                 *, events: Sequence[Event] | None = None) -> bool:
        """P16 2026-07-15: mirrors _ratchet_already_open's convention (a
        recent-window dedup flag, not a true clear/reset state machine) --
        feeds ReconcileInput.roadmap_exhausted_open, which the carve
        trigger (module contract item 9) consults so it stops requesting
        more carvers once the carver itself has already reported the
        roadmap exhausted.

        CR-02a: ``except Exception: return False`` said "not exhausted" from
        an unreadable log -- the answer that keeps requesting more carvers."""
        recent = list(self._require_events(project, events))[-500:]
        return any(ev.type is EventType.SPEC_ATTENTION and ev.payload.get("reason") == "roadmap-exhausted"
                   for ev in recent)

    def _spec_attention_recently_emitted(self, project: str, reason: str | None,
                                          *, events: Sequence[Event] | None = None) -> bool:
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
        for the general runaway backstop (not tied to any specific reason).

        CR-02a: ``except Exception: return False`` said "nothing emitted yet"
        from an unreadable log -- the answer that reproduces the very
        notification storm this debounce was written to stop.
        """
        return effects.spec_attention_recently_emitted(
            self._require_events(project, events), reason)

    def _needs_operator_recently_emitted(self, project: str, reason: str,
                                          *, events: Sequence[Event] | None = None) -> bool:
        """F018 P3d (concern-5 #4): debounce for NEEDS_OPERATOR{reason} --
        suppress re-emitting the same unresolved episode. Scans the event log
        for an already-emitted NEEDS_OPERATOR with matching reason since the
        most recent CARVER_SESSION_ROTATED or CARVER_SESSION_STARTED for this
        project (a rotation/new-generation clears the episode). Pure log-
        derived, no durable marker needed.

        CR-02a: ``except Exception: return False`` said "not yet escalated"
        from an unreadable log, re-firing the operator page every pass."""
        return effects.needs_operator_recently_emitted(
            self._require_events(project, events), reason)

    def _pending_carver_feeds(self, project: str, cfg: ProjectConfig,
                              snap: carver_session.CarverSessionSnapshot | None,
                              *, events: Sequence[Event] | None = None,
                              ) -> tuple[carver_session.CarverFeed, ...]:
        """F018 P2b-A2: pending merge digests the carver session hasn't
        consumed yet -- sourced from the P2a `carver_digest` payload the
        auto-merge path already attaches to MERGE_RECORDED (see the
        MERGE_RECORDED emission in _execute_auto_merge, just above the
        backlog auto-tick). `snap is None` means _carver_session's own
        MASTER GATE was off, so this returns () too -- matching
        ReconcileInput.pending_carver_feeds' own default and keeping
        _build_input byte-identical on that path.

        A MERGE_RECORDED without a carver_digest (pre-P2a, or a non-carve
        project) is skipped -- it cannot become a CarverFeed. Events at or
        below snap.last_consumed_event_sequence are already-consumed and
        excluded. The remainder is ordered by event_sequence ascending
        (arrival order, matching A1's slot-3 consumption -- see
        reconcile.plan_project's `sorted(inp.pending_carver_feeds, key=...)`
        use, which is itself just a safety net over this already-sorted
        tuple) and capped to the most recent cfg.carve.retain_merge_digests
        (default 10)."""
        if snap is None:
            return ()
        events = list(self._require_events(project, events))
        feeds: list[carver_session.CarverFeed] = []
        for ev in events:
            if ev.type is not EventType.MERGE_RECORDED:
                continue
            if ev.sequence <= snap.last_consumed_event_sequence:
                continue
            digest = ev.payload.get("carver_digest")
            if not digest:
                continue
            diffstat = digest.get("diffstat") or {}
            feeds.append(carver_session.CarverFeed(
                digest_id=digest.get("digest_id", ""),
                merge_commit=digest.get("merge_commit", ""),
                task_id=digest.get("task_id", ""),
                event_sequence=ev.sequence,
                files_changed=diffstat.get("files_changed", 0),
            ))
        feeds.sort(key=lambda f: f.event_sequence)
        retain = cfg.carve.retain_merge_digests
        if retain <= 0:
            feeds = []
        elif len(feeds) > retain:
            feeds = feeds[-retain:]
        return tuple(feeds)

    # -- carve-proposal validation + admission (F018 P3b) -----------------

    def _carve_proposal_repair_escalations(self, project: str, cfg: ProjectConfig,
                                            states: dict[str, TaskStateFile],
                                            *, events: Sequence[Event] | None = None,
                                            ) -> list[Event]:
        """F018 P3b (plan §4.1): 'Invalid output creates a bounded repair
        input for the same warm session... After the configured repair
        count, emit a typed NEEDS_OPERATOR{reason: carver-proposal-
        invalid} and preserve the artifacts.' Called early in run_pass
        (mirrors _transient_escalate's own early-mutation timing, BEFORE
        _build_input/plan_project) -- a project-level check, not tied to
        any single reconcile.Action.

        Actually feeding the invalidity back to the carver as a fresh turn
        is out of scope for this package (no repair ResumeCarverSession
        mode exists in reconcile.py's ladder yet -- see class contract).
        This method only tracks + escalates: the count of invalid
        CARVER_PROPOSAL_RECORDED payloads for the CURRENT generation is
        recomputed fresh every pass by re-running the SAME per-artifact
        validation _validated_carve_proposals uses (the append-only event
        log already IS the durable record -- no new EventType/counter
        needed). Debounced via _carve_proposal_repair_escalated so a
        persistent condition escalates ONCE per generation, not every
        pass forever (mirrors _spec_attention_recently_emitted /
        _runaway_recently_escalated's own recent-window convention)."""
        events = list(self._require_events(project, events))
        snap = self._carver_session(project, cfg, events=events)
        if snap is None:
            return []
        invalid = 0
        for ev in events:
            if ev.type is not EventType.CARVER_PROPOSAL_RECORDED:
                continue
            payload = ev.payload or {}
            parsed = self._parse_proposal_id(cfg, payload.get("proposal_id"))
            if parsed is None or parsed[0] != snap.generation:
                continue
            if self._validate_carve_proposal_payload(cfg, snap, payload)[0] is None:
                invalid += 1
        if invalid < cfg.carve.max_proposal_repairs:
            return []
        if self._carve_proposal_repair_escalated(project, snap.generation, events=events):
            return []
        return [self._append_ev(
            project, cfg, states, EventType.NEEDS_OPERATOR,
            {"reason": "carver-proposal-invalid", "generation": snap.generation,
             "invalid_count": invalid},
            task_id=None)]

    def _carve_proposal_repair_escalated(self, project: str, generation: int,
                                          *, events: Sequence[Event] | None = None) -> bool:
        """Same recent-window debounce convention as
        _runaway_recently_escalated -- keyed on generation so a NEW
        generation (post-rotation) gets its own fresh repair budget.

        CR-02a: ``except Exception: return False`` said "not yet escalated"
        from an unreadable log, re-paging the operator every pass."""
        recent = list(self._require_events(project, events))[-500:]
        return any(
            ev.type is EventType.NEEDS_OPERATOR
            and ev.payload.get("reason") == "carver-proposal-invalid"
            and ev.payload.get("generation") == generation
            for ev in recent
        )

    # -- runaway watchdog (P44 2026-07-16) ------------------------------

    def _apply_watchdog(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                        actions: list[reconcile.Action], project_paused: bool,
                        *, events: Sequence[Event] | None = None,
                        ) -> tuple[list[reconcile.Action], list[Event]]:
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

        P49 2026-07-19 (fixes a live incident: resuming re-paused within
        one reconcile_interval_seconds, repeatedly): the streak used to
        increment UNCONDITIONALLY every pass a signal was (re-)detected --
        including every pass spent ALREADY paused, since detect_runaways
        keeps re-finding the same still-undecayed historical condition
        (e.g. review_rejections_by_area>=2, true for a full 7-day window
        regardless of an operator having acted). By the time an operator
        resumed, the in-memory streak had climbed far past
        RUNAWAY_PERSIST_AFTER_CYCLES from all the passes spent paused, so
        the very next pass re-paused almost instantly -- the pause flag
        was the only thing gating _auto_pause_for_runaway's OWN no-op, not
        the streak that decides whether to even try. Fix: while
        project_paused (for ANY reason -- an operator's own pause counts
        the same as this watchdog's own prior pause), freeze+reset each
        signal's streak to 0 instead of incrementing it, and skip
        escalating/suppressing for it this pass (there is nothing new to
        suppress while already paused). Resuming starts every streak at 0,
        so the SAME still-open
        condition needs RUNAWAY_PERSIST_AFTER_CYCLES fresh detections
        again (not zero) before re-pausing -- a real window, not an
        instant re-trip, while still re-pausing if the condition is
        genuinely still active rather than silently disabling the
        watchdog.
        Returns (filtered_actions, new_events) -- both empty/unchanged when
        no runaway is detected (the overwhelmingly common case).

        CR-02a: the event read is the fan-in's authoritative one. The
        detector itself keeps a broad catch -- census class
        advisory-degradation, justified in the handler comment -- because a
        detector crash must not become a reason to skip the actions the
        planner already decided are correct."""
        recent_events = list(self._require_events(project, events))[-500:]
        try:
            signals = watchdog.detect_runaways(recent_events, watchdog.WatchdogConfig())
        except Exception as exc:  # census: advisory-degradation (CR-02a)
            # The watchdog SUPPRESSES actions; it never authorizes one. A
            # detector fault therefore removes a safety net rather than
            # opening a gate, so it degrades rather than failing the pass --
            # but it is logged, because a permanently-crashing watchdog is a
            # silently disabled watchdog, which is RISK-007's whole shape.
            log.warning("watchdog detector failed", project=project,
                         error=snapshot.bounded_detail(repr(exc)))
            signals = []
        if not signals:
            return actions, []

        filtered = list(actions)
        new_events: list[Event] = []
        for sig in signals:
            streak_key = f"{project}:{sig.key}"

            if project_paused:
                self._runaway_streak[streak_key] = 0
                continue

            streak = self._runaway_streak.get(streak_key, 0) + 1
            self._runaway_streak[streak_key] = streak

            if not self._runaway_recently_escalated(project, sig.key,
                                                     events=recent_events):
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

    def _runaway_recently_escalated(self, project: str, key: str,
                                     *, events: Sequence[Event] | None = None) -> bool:
        """Same recent-window convention as _spec_attention_recently_emitted,
        keyed on RunawaySignal.key (not just pattern -- multiple distinct
        conditions can share one pattern, e.g. two different
        'reconcile-thrash:<reason>' keys).

        CR-02a: ``except Exception: return False`` said "not yet escalated"
        from an unreadable log, which re-pages on every pass of a runaway."""
        recent = list(self._require_events(project, events))[-500:]
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

    # CR-05a: the three write helpers below are now DELEGATES. Their bodies
    # moved behind the effect ports (`effects.StoreJournal`) and the
    # lifecycle effector, so the ~190 call sites across this class keep
    # working while there is exactly one implementation of "append an event"
    # and one owner of the provider backoff registry.

    def _append_ev(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                   ev_type: EventType, payload: dict[str, Any], **kw) -> Event:
        return self._ports.journal.append(project, cfg, states, ev_type, payload, **kw)

    def _transition(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                     task_id: str, to: TaskState, notes: str | None) -> Event:
        return self._ports.journal.transition(project, cfg, states, task_id, to, notes)

    def _provider_pause(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                        route_id: str | None, task_id: str | None,
                        state: str = "limited") -> list[Event]:
        # B24 2026-07-23 (D-R17, D-B24-3): `state` defaults to "limited"
        # (byte-identical to every pre-B24 caller); _transient_escalate
        # passes state="throttled" so a provider-throttle escalation reads
        # distinctly from a genuine LIMIT receipt's pause in the event log,
        # without a new EventType (PROVIDER_STATE_CHANGED already carries a
        # free-form `state` payload value).
        return self._lifecycle.pause_provider(
            self._effect_context(project, cfg, states), route_id, task_id, state)

    # -- execution map ---------------------------------------------------

    def _gate_hint(self, cfg: ProjectConfig) -> str:
        return effects_dispatch.gate_hint(cfg)

    def _frontmatter_for(self, cfg: ProjectConfig, tsf: TaskStateFile):
        return effects_dispatch.frontmatter_for(cfg, tsf)

    def _lease_specs(self, cfg: ProjectConfig, fm) -> list[dict[str, Any]]:
        return effects_dispatch.lease_specs(cfg, fm)

    def _ensure_worktree(self, root: Path, branch: str, worktree_path: Path,
                          default_branch: str) -> None:
        # CR-05c: retained as the daemon-side call site for the carve families
        # CR-05d still owns; the two branch shapes it distinguishes, and why a
        # failure must raise rather than degrade, are documented on the effector.
        effects_dispatch.ensure_worktree(self._ports, root, branch,
                                         worktree_path, default_branch)

    # -- carve automation (P16 2026-07-15) --------------------------------

    # -- persistent carver session executor (F018 P3a) --------------------
    #
    # plan-long-running-carver.md §5.1 (Start) / §5.2 (Resume) / §5.3
    # (Lease). Executes the TWO carver-session actions the pure planner
    # (reconcile.py, A1/A2, unchanged here) emits once cfg.carve.session ==
    # "project-persistent": StartCarverSession (cold bootstrap) and
    # ResumeCarverSession (a warm turn: merge-feed / targeted-intake /
    # recover). Mirrors _execute_carve_dispatch's own launch shape (synthetic
    # task+attempt+worktree under the SAME strategic-carver lease,
    # task_id="carve-<project>-<seq>") but with a DISTINCT task_id prefix
    # ("carver-session-<project>-<seq>") so:
    #   (a) _consume_carve_exit's legacy CARVE-<seq>.md report parsing is
    #       never accidentally applied to a bootstrap/resume turn (see
    #       _execute's EmitAttemptExit dispatch, which branches on this
    #       prefix), and
    #   (b) reconcile.py's existing, UNCHANGED carve_in_flight guard (any
    #       non-terminal task hosting a Role.CARVER attempt) and its
    #       existing FAILED-attempt lease-lost-race handling (Transition to
    #       SUPERSEDED, module contract item mirrored above _consume_
    #       carve_exit) apply to these tasks for free, with zero reconcile.py
    #       changes -- both keys are ROLE-based (Role.CARVER), not prefix-
    #       based, and every carver-session task uses role=Role.CARVER too.
    #
    # Session STARTED/RESUMED/DEGRADED is never decided at LAUNCH time --
    # session capture is the wrapper's own async, post-launch job (wrapper.py
    # step 5, unchanged/unread here beyond its documented contract), so
    # _execute_start_carver_session/_execute_resume_carver_session only ever
    # emit the LAUNCH-time events (TASK_CREATED/ATTEMPT_CREATED/ATTEMPT_
    # PREFLIGHTED), exactly like CarveDispatch. The actual outcome is decided
    # at EXIT-CONSUMPTION time by _consume_carver_session_exit, from the
    # turn's real captured session_handle + receipt result -- "never record
    # warm based only on process exit" (plan §5.1) applied symmetrically to
    # both Start and Resume.

    def _next_resume_n(self, attempt_dir: Path) -> int:
        return effects_attempt.next_resume_n(self._ports.files, attempt_dir)

    def _execute(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                 action: reconcile.Action) -> list[Event]:
        """Dispatch one planned action to its registered handler.

        CR-05a: this used to BE the effect layer -- a 1,090-line isinstance
        ladder that both decided what an action meant and performed it. It is
        now a lookup, and the only thing it can do with an action it does not
        recognise is raise :class:`effects.UnownedAction`, which run_pass's
        per-action isolation records as a TICK_ERROR. The families still
        implemented below (`_execute_legacy`) are registered as legacy specs
        owned by CR-05b, so they are reached through the SAME lookup -- there
        is no second dispatch path a handler could hide in.
        """
        return self._registry.execute(
            self._effect_context(project, cfg, states), action)

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
        # CR-15: bootstrap the operator credential before the socket exists,
        # so no request can ever race a missing store.  A failure here is
        # NOT fatal: reads and the reconcile loop stay up, and every mutation
        # fails closed (503) because it re-reads the store per request.
        # Crashing the daemon instead would turn "the credential file has the
        # wrong mode" into "the factory stops", which is a worse outcome than
        # a control plane that refuses to be driven -- recover with
        # `nyxloom auth rotate --force`.
        try:
            self._control_auth.ensure()
        except control_auth.CredentialStoreError as exc:
            log.error(
                "operator credential unavailable; every control-plane mutation "
                "will be refused until it is repaired "
                "(nyxloom auth rotate --force)",
                reason=str(exc), credential_path=str(self._control_auth.path),
            )
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
        # 2026-07-20: state the security assumption out loud at bind time.
        # CR-15 2026-08-02 narrows WHAT the assumption is: mutations are
        # authenticated now, so the remaining exposure of a non-loopback bind
        # is the READ surface, which stays open on purpose (reads must remain
        # separable so a trusted network can serve the dashboard without
        # granting control authority). Still a WARNING, not INFO (P01): it
        # states an assumption rather than crying wolf, but a non-loopback bind
        # is worth a level above the routine operational narrative. Loopback
        # (the default) needs no callout. The message must not re-assert the
        # old "UNAUTHENTICATED" claim -- asserted by
        # test_nonloopback_bind_warns_about_the_open_read_surface.
        if self.http_bind not in ("127.0.0.1", "::1"):
            log.warning(
                "HTTP control plane bound to a non-loopback address "
                "(mutations require operator authentication; read endpoints "
                "still assume a trusted network)",
                http_bind=self.http_bind, http_port=self.http_port,
            )
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
    def _send_json(handler: http.server.BaseHTTPRequestHandler, code: int, body: bytes,
                   *, close: bool = False) -> None:
        """`close=True` ends the connection after this response.

        CR-15 refuses a mutation BEFORE reading its body, so those bytes are
        still queued on a keep-alive HTTP/1.1 socket and the next read would
        parse them as a fresh request line. Sending `Connection: close` both
        tells the client (http.server also flips close_connection on this
        header) and drops the unread body with the socket -- cheaper than
        reading an unauthenticated body just to discard it.
        """
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        if close:
            handler.send_header("Connection", "close")
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

        if path == "/favicon.ico":
            # Browsers auto-request this; the dashboard ships no icon. Answer 204
            # (No Content) so it is not a recurring 404 in the access log.
            handler.send_response(204)
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

        if path == "/api/logs/level":
            # P02 (D-L3 §4.4): the current effective level + which
            # precedence layer supplied it -- reruns the exact same
            # resolve_level() chain the daemon's own bootstrap and the
            # POST-side live flip both go through, so this always reflects
            # the truth (a POST persists to the runtime-file, the top layer
            # resolve_level checks).
            level, source = resolve_level(self.registry)
            body = json.dumps({"level": level, "source": source}).encode("utf-8")
            self._send_json(handler, 200, body)
            return

        if path == "/api/logs":
            # P04 (§4.5): server-side filtered read of paths.nyxloom_log_path().
            # D5: a missing file is a clean 200 [] (handled inside the helper),
            # never a 404/500.
            level = qs.get("level", [None])[0]
            project = qs.get("project", [None])[0]
            q = qs.get("q", [None])[0]
            try:
                since = int(qs.get("since", ["-1"])[0])
            except ValueError:
                since = -1
            try:
                limit = int(qs.get("limit", ["500"])[0])
            except ValueError:
                limit = 500
            records = self._read_log_records(
                level=level, since=since, limit=limit, project=project, q=q)
            self._send_json(handler, 200, json.dumps(records).encode("utf-8"))
            return

        if path == "/api/logs/export":
            # D2: a distinct route from /api/logs (not a query flag on it),
            # same filters, JSONL download rather than a JSON array body.
            level = qs.get("level", [None])[0]
            project = qs.get("project", [None])[0]
            q = qs.get("q", [None])[0]
            try:
                since = int(qs.get("since", ["-1"])[0])
            except ValueError:
                since = -1
            try:
                limit = int(qs.get("limit", ["500"])[0])
            except ValueError:
                limit = 500
            records = self._read_log_records(
                level=level, since=since, limit=limit, project=project, q=q)
            lines = "".join(json.dumps(r) + "\n" for r in records)
            body = lines.encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "application/x-ndjson")
            handler.send_header("Content-Disposition",
                                 'attachment; filename="nyxloom-logs.jsonl"')
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return

        if path == "/api/logs/stream":
            # D6/§4.5: SSE tail of the log FILE (byte offset), the log-stream
            # twin of /api/stream's event-sequence tail (_serve_sse above).
            level = qs.get("level", [None])[0]
            self._serve_log_stream(handler, level)
            return

        self._send_json(handler, 404, b'{"error":"not found"}')

    # -- HTTP config mutation endpoints (P15 2026-07-15) -----------------

    def _append_ui_event(self, project: str, cfg: ProjectConfig | None,
                          states: dict[str, TaskStateFile], ev_type: EventType,
                          payload: dict[str, Any], *, actor: Actor, **kw) -> Event:
        """Same append+apply+notify shape as `_append_ev`, with the named
        authenticated operator responsible for this HTTP mutation.
        `_append_ev` is deliberately NOT reused here: it hardcodes actor
        TICK/'nyxloomd', which is correct for reconcile-pass-triggered
        events but wrong for operator-initiated UI writes."""
        ev = storage.append_and_apply(
            project, states, actor=actor, type=ev_type,
            payload=payload, **kw,
        )
        if cfg is not None:
            try:
                notify.notify_event(cfg, states, ev)
            except Exception:
                pass
        return ev

    @staticmethod
    def _audit_control_refusal(path: str, reason: str) -> None:
        """One audited refusal, on the SAME helper the ntfy ingress uses.

        The behaviour (exactly one event, path+reason only, never raises)
        lives in `control_auth.audit_control_refusal` so both mutation
        ingresses -- this HTTP surface and the notification channel -- can
        never drift into two different refusal shapes.
        """
        control_auth.audit_control_refusal(path, reason)

    def _authenticate_control_mutation(
        self, handler: http.server.BaseHTTPRequestHandler, path: str,
    ) -> Actor | None:
        """Authenticate before body parsing or any target/id lookup.

        Returns the named operator, or None after having already audited the
        refusal and sent a response.  Both refusal responses are constant
        byte strings: the caller learns whether the trust root is readable,
        never anything about the target it named."""
        try:
            actor = self._control_auth.authenticate(handler.headers)
        except control_auth.CredentialStoreError:
            self._audit_control_refusal(path, "credential-store-unavailable")
            self._send_json(handler, 503,
                            b'{"error":"mutation authentication unavailable"}', close=True)
            return None
        if actor is None:
            self._audit_control_refusal(path, "invalid-or-missing-credential")
            self._send_json(handler, 401,
                            b'{"error":"mutation authentication required"}', close=True)
            return None
        return actor

    @staticmethod
    def _declared_body_length(handler: http.server.BaseHTTPRequestHandler) -> int | None:
        """The request's framing length, or None when it is not unambiguous.

        THE one place a body length is derived, because a body length IS the
        message framing: get it wrong and the next bytes on a keep-alive
        socket are read as a new request.  Three ways to be ambiguous, all
        refused rather than guessed:

        - Two Content-Length headers (`.get` would silently take the first --
          the classic request-smuggling desync, since the peer at the other
          end may take the second).
        - A value `int()` accepts but RFC 9110's `Content-Length = 1*DIGIT`
          does not: "+9", "9_9", and any surrounding whitespace.  `int("+9")`
          == 9 would have quietly under-declared the cap check below.
          Trailing OWS is technically legal and is still refused: no real
          client emits it, and a length parse that tolerates decoration is
          exactly the primitive a desync is built from -- the two ends must
          agree on the number, or there must be no request.
        - `Transfer-Encoding` PRESENT, whatever its value: http.server does
          not de-chunk, so the body would stay on the socket after the
          response.  Tested with `get_all`, because an empty value is still a
          present header and `.get` would report it as falsy.
        """
        if handler.headers.get_all("Transfer-Encoding"):
            return None
        values = handler.headers.get_all("Content-Length") or []
        if not values:
            return 0
        if len(values) != 1:
            return None
        raw = values[0]
        if raw != raw.strip() or not raw.isascii() or not raw.isdigit():
            return None
        return int(raw)

    def _read_json_body(self, handler: http.server.BaseHTTPRequestHandler,
                        length: int) -> dict | None:
        """Read+parse `length` body bytes; None (caller sends 400) on any
        malformed input, including a non-object JSON value.

        CR-15: only reached AFTER authentication, and only with a length
        `_handle_post` has already parsed strictly and capped at
        _MAX_REQUEST_BODY_BYTES -- so this read cannot be used to make the
        daemon allocate an arbitrary buffer, which it could before from any
        unauthenticated socket."""
        raw = handler.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return body if isinstance(body, dict) else None

    @staticmethod
    def _reject_cross_site(handler: http.server.BaseHTTPRequestHandler) -> str | None:
        """None when this POST may proceed; otherwise the refusal reason.

        2026-08-02 (review amendment RISK-005).  These standards-based checks
        remain a defense in depth ahead of CR-15 operator authentication:

        - Require Content-Type: application/json. A cross-site <form> can only
          send urlencoded/multipart/text-plain, so requiring a type it cannot
          produce blocks form-based CSRF without any token. Every dashboard
          fetch() already sets this header (render.py), so nothing legitimate
          changes.
        - Require a same-origin Origin when the header is present. Browsers
          send Origin on cross-site POSTs; non-browser clients (curl, the CLI)
          send none and are unaffected. Same-origin is host-compared, so
          reaching the dashboard as nyxloomd:8942 or localhost:8942 both work.

        These checks do not replace the credential check in ``_handle_post``.
        """
        ctype = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return "content-type must be application/json"
        origin = handler.headers.get("Origin")
        if origin:
            host = (handler.headers.get("Host") or "").strip().lower()
            try:
                origin_host = urllib.parse.urlparse(origin).netloc.strip().lower()
            except ValueError:
                return "malformed origin"
            if not host or origin_host != host:
                return "cross-site origin"
        return None

    def _handle_post(self, handler: http.server.BaseHTTPRequestHandler) -> None:
        """The ONE gate every mutating route passes through.

        Order is deliberate and load-bearing (CR-15):

        1. Unknown path -> 404, decided from a static table before anything
           else runs, so an unrouted path cannot reach the credential check
           and flood the ledger.  The table leaks nothing: the dashboard's own
           JavaScript names every path already.
        2. Cross-site refusal (CSRF: Content-Type + Origin), unchanged from
           the 2026-08-02 amendment.
        3. Operator authentication -- BEFORE the body is read and therefore
           before any project, decision, intake or finding id is resolved.
        4. Body framing and cap, then parse, then dispatch with the
           authenticated Actor threaded into every handler; no handler may
           invent its own actor.

        Adding a path to `_CONFIG_POST_PATHS` therefore authenticates it by
        construction -- a new endpoint cannot forget the credential check
        (asserted for every path by test_control_auth.py, whose census also
        fails if a branch below names a path the table does not) -- but it
        must also gain a branch below, or it falls through to the 500 at the
        end.

        EVERY refusal that returns without reading the body passes
        `close=True`.  Those undrained bytes are still queued on an HTTP/1.1
        keep-alive socket, and the next read on that connection would parse
        them as a fresh request line: a refusal is exactly where an attacker
        would smuggle a second, unrefused request.  Dropping the connection
        discards them with it, which is also cheaper than reading a body we
        have already decided to reject.
        """
        parsed = urllib.parse.urlparse(handler.path)
        path = parsed.path
        if path not in _CONFIG_POST_PATHS:
            self._send_json(handler, 404, b'{"error":"not found"}', close=True)
            return
        refusal = self._reject_cross_site(handler)
        if refusal is not None:
            self._audit_control_refusal(path, refusal)
            self._send_json(handler, 403, json.dumps({"error": refusal}).encode("utf-8"),
                            close=True)
            return
        actor = self._authenticate_control_mutation(handler, path)
        if actor is None:
            return
        # Frame and bound the body only once the caller is known: every payload
        # on this surface is small JSON (ids, a mode name, a reply text), so an
        # unframeable or over-cap length is a mistake or an attempt to make the
        # daemon allocate. Refuse before reading a byte of it.
        length = self._declared_body_length(handler)
        if length is None:
            self._send_json(handler, 400, b'{"error":"ambiguous request framing"}',
                            close=True)
            return
        if length > _MAX_REQUEST_BODY_BYTES:
            self._send_json(handler, 413, b'{"error":"request body too large"}', close=True)
            return
        body = self._read_json_body(handler, length)
        if body is None:
            self._send_json(handler, 400, b'{"error":"malformed json body"}')
            return

        if path == "/api/config/policy":
            self._post_config_policy(handler, body, actor)
            return
        if path == "/api/config/pause":
            self._post_config_pause(handler, body, actor)
            return
        if path == "/api/config/tier":
            self._post_config_tier(handler, body, actor)
            return
        if path == "/api/decision/reply":
            self._post_decision_reply(handler, body, actor)
            return
        if path == "/api/intake":
            self._post_intake(handler, body, actor)
            return
        if path == "/api/config/log-level":
            self._post_config_log_level(handler, body, actor)
            return
        # FN-6: promote a finding to an interactive intake conversation
        if path == "/api/finding/promote":
            self._post_finding_promote(handler, body, actor)
            return

        # Unreachable while _CONFIG_POST_PATHS and the branches above agree.
        # Answer anyway: falling off the end of a BaseHTTPRequestHandler
        # method sends NO response at all, so a future path added to the table
        # but not to the dispatch chain would hang every client on it until
        # their own timeout rather than failing visibly.
        log.error("routed control path has no handler", control_path=path)
        self._send_json(handler, 500, b'{"error":"unrouted control path"}')

    def _post_config_log_level(self, handler: http.server.BaseHTTPRequestHandler,
                               body: dict, actor: Actor) -> None:
        """P02 (D-L3 §4.4): live-flip the daemon's effective log level, no
        restart required, and persist it to paths.daemon_log_level_path()
        so a respawn's bootstrap resolve_level() picks the same level back
        up (the runtime-file is precedence layer 1 -- the highest).

        D-L4: this is a LOG record, never a domain one -- unlike every
        other endpoint in this config-mutation section, there is
        deliberately NO storage.append_and_apply / _append_ui_event call
        here (a log-level flip is an operational-diagnostics concern, not
        a fact about task/attempt state the event log needs to replay).

        CR-15 refines, and does not repeal, that rule: the flip is an
        authenticated control-plane mutation, so it appends ONE CONFIG_CHANGED
        to the instance control ledger (CONTROL_AUDIT_PROJECT) naming the
        operator who made it.  No PROJECT event log is touched, so D-L4's
        actual invariant -- a level flip never enters a project's replayable
        history -- still holds byte-for-byte (asserted by
        test_log_level_post_emits_log_not_domain_event)."""
        level = body.get("level")
        if not isinstance(level, str) or not level.strip():
            self._send_json(handler, 400, b'{"error":"missing level"}')
            return
        canonical = level.strip().lower()
        try:
            log_module._normalize_level(canonical)
        except ValueError:
            self._send_json(handler, 400, json.dumps(
                {"error": f"unknown log level: {level!r}"}).encode("utf-8"))
            return

        # Log the transition BEFORE applying it -- emitted under the OLD
        # (still-active) effective level, not the new one. Ordering matters:
        # flipping to a STRICTER level (e.g. info -> warning) would gate out
        # this very INFO announcement if set_level() ran first, silently
        # swallowing the one record an operator most wants to see.
        # NB: field name "new_level", NOT "level" -- see the matching
        # comment on the "daemon started" log call in Daemon.run(); "level"
        # in the rendered record is always the record's own severity
        # ("info" here), overwritten unconditionally by structlog.stdlib.
        # add_log_level regardless of what a same-named kwarg carried.
        old_level, _old_source = resolve_level(self.registry)
        log.info("log level changed", new_level=canonical, change_source="http",
                 operator_id=actor.id)

        log_module.set_level(canonical)
        override_path = paths.daemon_log_level_path()
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_text(canonical, encoding="utf-8")

        storage.append_event(
            CONTROL_AUDIT_PROJECT, actor=actor, type=EventType.CONFIG_CHANGED,
            payload={"scope": "daemon", "key": "log-level",
                     "old": old_level, "new": canonical},
        )

        self._send_json(handler, 200, json.dumps({"ok": True, "level": canonical}).encode("utf-8"))

    def _post_decision_reply(self, handler: http.server.BaseHTTPRequestHandler,
                             body: dict, actor: Actor) -> None:
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

        # CR-15: audit BEFORE the effect, not after. This is the endpoint the
        # "human owns direction" invariant runs on, so "operator X submitted a
        # reply to decision Y" must be durable even if the turn then fails --
        # and an unwritable ledger must REFUSE the turn rather than let an
        # unrecorded answer through. The record is true at the point of
        # receipt: the target is already resolved, and the id is the one the
        # 404 above just proved exists.
        try:
            storage.append_event(
                project, actor=actor, type=EventType.DECISION_REPLY_RECORDED,
                decision_id=decision_id, payload={},
            )
        except Exception as exc:
            log.error("decision reply refused: it could not be audited",
                      control_path="/api/decision/reply", error=repr(exc)[:200])
            self._send_json(handler, 503, b'{"error":"mutation audit unavailable"}')
            return

        try:
            decision_chat.advance_chat(cfg, project, decision_id, text.strip(), actor=actor)
        except Exception as exc:
            self._send_json(handler, 500, json.dumps({"error": repr(exc)[:200]}).encode("utf-8"))
            return

        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps({"ok": True}).encode("utf-8"))

    def _post_intake(self, handler: http.server.BaseHTTPRequestHandler,
                     body: dict, actor: Actor) -> None:
        """P30: drive the intake-chat bridge from the UI (intake.html) --
        the ONE sanctioned write path into intake_chat.advance_intake().
        Body is untrusted operator input: passed through as plain text (no
        shell, no eval, no dynamic dispatch); advance_intake itself redacts
        the agent's reply before it is stored or returned here.

        `text` is free-form (it only ever becomes prompt/transcript text, and
        render.py escapes it), but `intake_id` names a file and is echoed into
        intake.html's JS, so it must match _INTAKE_ID_RE; omit it to open a
        fresh conversation and let the server mint one.

        2026-08-02: the preceding sentence used to end "Loopback-only, same as
        every other route on this server" -- untrue since P38 moved the deployed
        bind to 0.0.0.0 on the ciu bridge. CR-15: this endpoint is reached only
        with a valid operator credential (_handle_post), and `actor` is that
        operator; _reject_cross_site adds the CSRF check on top."""
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

        storage.append_event(
            project, actor=actor, type=EventType.INTAKE_REPLY_RECORDED,
            payload={"intake_id": intake_id},
        )

        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps(
            {"ok": True, "intake_id": intake_id, "reply": reply}).encode("utf-8"))

    def _post_finding_promote(self, handler, body: dict, actor: Actor) -> None:
        """FN-6: promote a finding to an interactive intake conversation. The
        finding's typed content seeds a NEW intake (new_id('intake')); everything
        downstream is the existing intake pipeline. Same surface as /api/intake
        (NOT loopback-only since P38; CR-15 authenticates it -- see
        _handle_post and _reject_cross_site).
        `finding_id`/`project` are validated against real records; the
        minted intake_id (not any client value) names the file."""
        from . import findings as findings_mod
        project = body.get("project")
        finding_id = body.get("finding_id")
        if not isinstance(project, str) or project not in self.registry:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        if not isinstance(finding_id, str) or not finding_id.strip():
            self._send_json(handler, 400, b'{"error":"missing finding_id"}')
            return
        match = next((f for f in findings_mod.load_findings(project)
                      if f.finding_id == finding_id), None)
        if match is None:
            self._send_json(handler, 404, b'{"error":"finding not found"}')
            return
        try:
            cfg = config.ProjectConfig.load(self.registry[project])
        except Exception:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        intake_id = new_id("intake")
        seed = findings_mod.promote_seed_text(match)
        try:
            reply = intake_chat.advance_intake(cfg, project, intake_id, seed)
        except Exception as exc:
            self._send_json(handler, 500, json.dumps({"error": repr(exc)[:200]}).encode("utf-8"))
            return
        storage.append_event(
            project, actor=actor, type=EventType.FINDING_PROMOTED,
            payload={"finding_id": finding_id, "intake_id": intake_id},
        )
        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps(
            {"ok": True, "intake_id": intake_id, "reply": reply}).encode("utf-8"))

    def _post_config_policy(self, handler: http.server.BaseHTTPRequestHandler,
                            body: dict, actor: Actor) -> None:
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
                                   {"scope": "policy", "key": key, "old": old_value, "new": value},
                                   actor=actor)
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
                               {"scope": "policy", "key": key, "old": old_value, "new": value},
                               actor=actor)
        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps({"ok": True}).encode("utf-8"))

    def _post_config_pause(self, handler: http.server.BaseHTTPRequestHandler,
                           body: dict, actor: Actor) -> None:
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
            self._append_ui_event(project, cfg, states, EventType.PAUSE_CLEARED, {}, actor=actor)
        else:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(mode, encoding="utf-8")
            self._append_ui_event(project, cfg, states, EventType.PAUSE_SET,
                                  {"mode": mode}, actor=actor)

        render.render_after_event(self.registry)
        self._send_json(handler, 200, json.dumps({"ok": True, "mode": mode}).encode("utf-8"))

    def _post_config_tier(self, handler: http.server.BaseHTTPRequestHandler,
                          body: dict, actor: Actor) -> None:
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
                                   {"scope": "routes", "key": tier, "old": old_routes,
                                    "new": route_ids}, actor=actor)

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

    def _read_log_records(
        self, *, level: str | int | None = None, since: int | None = None,
        limit: int | None = None, project: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        """P04 (D7, docs/plan-logging.md §4.5): read back
        ``paths.nyxloom_log_path()`` for the ``/api/logs*`` endpoints.

        D3: there is no persisted sequence field and ``ts`` is only second-
        precision, so a 0-based ``seq`` -- the record's line index in the
        CURRENT file -- is injected on every record; a client pages with
        ``since=<last seq>`` (kept strictly greater than). D5: a missing
        file returns ``[]``, never raises. Malformed lines (a partial write
        mid-append) are skipped defensively rather than failing the whole
        read. Returned newest-last (the file's own append order), capped to
        the *last* ``limit`` entries so a cap keeps the most recent ones.
        """
        log_path = paths.nyxloom_log_path()
        if not log_path.exists():
            return []
        min_level = log_module._normalize_level(level) if level is not None else None
        q_lower = q.lower() if q else None
        records: list[dict] = []
        for idx, line in enumerate(log_path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            record["seq"] = idx
            if since is not None and idx <= since:
                continue
            if min_level is not None:
                rec_level = log_module._normalize_level(record.get("level", "info"))
                if rec_level < min_level:
                    continue
            if project is not None and record.get("project") != project:
                continue
            if q_lower is not None and q_lower not in json.dumps(record).lower():
                continue
            records.append(record)
        if limit is not None and limit >= 0:
            records = records[-limit:]
        return records

    def _log_stream_tick(
        self, now: float, log_path: Path, offset: int,
        last_heartbeat: float, min_level: int | None,
    ) -> tuple[list[bytes], int, float]:
        """One SSE poll iteration of the log tail, as PURE data: read any new
        complete lines from ``offset`` (rotation-aware -- a file that shrank
        below ``offset`` resets to 0), drop blank/malformed lines and records
        below ``min_level``, format each survivor as an SSE ``data:`` frame,
        and append a ``: hb`` heartbeat frame once ``SSE_HEARTBEAT_SECONDS``
        has elapsed. Returns ``(chunks, new_offset, new_last_heartbeat)``.

        Extracted from ``_serve_log_stream`` so the tail's parse/filter/
        rotation/heartbeat branches are exercised deterministically from the
        MAIN thread: coverage of a loop body running inside the HTTP handler
        thread is otherwise racy, and these edge branches (rotation, a blank
        or partial-write line, a below-level line) never arise on the happy
        integration path. The thread shell below only does blocking I/O."""
        size = log_path.stat().st_size if log_path.exists() else 0
        if size < offset:
            offset = 0  # D6: rotation -- do not chase the rotated-away tail
        chunks: list[bytes] = []
        if size > offset:
            with log_path.open("r", encoding="utf-8") as fh:
                fh.seek(offset)
                new_text = fh.read()
                offset = fh.tell()
            for line in new_text.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if min_level is not None:
                    rec_level = log_module._normalize_level(record.get("level", "info"))
                    if rec_level < min_level:
                        continue
                chunks.append(f"data: {json.dumps(record)}\n\n".encode("utf-8"))
        if now - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
            chunks.append(b": hb\n\n")
            last_heartbeat = now
        return chunks, offset, last_heartbeat

    def _serve_log_stream(self, handler: http.server.BaseHTTPRequestHandler,
                           level: str | None) -> None:
        """P04 (D6/§4.5): SSE tail of ``paths.nyxloom_log_path()``, mirroring
        ``_serve_sse``'s exact poll/heartbeat/disconnect shape but tailing the
        log FILE by byte offset (there is no event-sequence store for logs).
        Starts at EOF (only NEW lines stream). The per-poll read/filter/
        rotation/heartbeat logic lives in ``_log_stream_tick`` (main-thread
        tested); this shell only performs the blocking writes and sleeps."""
        min_level = log_module._normalize_level(level) if level is not None else None
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "close")
        handler.end_headers()
        log_path = paths.nyxloom_log_path()
        offset = log_path.stat().st_size if log_path.exists() else 0
        last_heartbeat = time.monotonic()
        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                chunks, offset, last_heartbeat = self._log_stream_tick(
                    now, log_path, offset, last_heartbeat, min_level)
                for chunk in chunks:
                    handler.wfile.write(chunk)
                if chunks:
                    handler.wfile.flush()
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
