"""General runaway watchdog: a backstop over recent events. PACKAGE P44
(2026-07-16, anti-runaway self-correction).

BACKGROUND (a real prod incident, 2026-07-16): nyxloomd stormed ntfy with a
notification EVERY reconcile cycle (~31s), indefinitely. Root cause chain:
daemon.py's `_history` counted `review_rejections_by_area` over the ENTIRE
event log (only ever increasing), and reconcile.py's 'rejections' /
'carve-outcome' / 'blocked-underspecified' SpecAttention branches (unlike
'ratchet'/'roadmap-exhausted') had NO dedup -- so once an area hit 2
rejections, SpecAttention('rejections') fired, and re-fired, every single
pass forever. Two narrow fixes address that SPECIFIC chain: `_history`'s
review_rejections_by_area is now windowed (daemon.py
HISTORY_REJECTION_WINDOW_SECONDS), and all three branches now dedupe via
`*_already_open` input flags (reconcile.py, mirroring ratchet_already_open).

THIS module is the GENERAL backstop: even an unanticipated repeat pattern
those two narrower fixes don't cover should still be caught and stopped,
never silently repeated forever. It is intentionally independent of the
specific 'rejections' bug -- it looks at raw event shape, not at
reconcile.py's specific branches.

CR-16 2026-08-03 (liveness, channel health, silent-failure detection;
RISK-007) ADDS pattern (d) below: every pattern up to that point detects
TOO MUCH activity. Nothing here detected a daemon that is up, dispatching
its HTTP healthcheck fine, and failing every single reconcile pass --
exactly the shape a bare TCP check cannot see. (d) closes that gap using
the SAME pure event-shape analysis as (a)-(c); the two OTHER RISK-007
mechanisms -- the durable per-project heartbeat and the transport-health
probe -- live in daemon.py/doctor.py and notify.py respectively, because
neither is a pattern over one project's event window the way (a)-(d) are.

INTERFACE CONTRACT (frozen except for CR-16's (d), see above):

- WatchdogConfig: safe, conservative, configurable thresholds. Every field
  has a default so a caller need not tune anything to get protection.
- RunawaySignal: one detected pattern.
    pattern: 'notification-storm' | 'reconcile-thrash' | 'attempt-loop'
      | 'tick-error-streak'
    key: a STABLE dedup key for the underlying condition (same condition
      across calls -> same key; used by the daemon both to dedup the
      human escalation event and to track a same-condition streak for
      graduated remedy). Never includes prose -- ids/reasons/enum values
      only (injection boundary, same rule as reconcile.py's Action
      payloads).
    detail: a short, FIXED-FIELD description (counts, ids, reasons only --
      never event/handoff prose) suitable for a notification body.
- detect_runaways(recent_events, cfg) -> list[RunawaySignal]: PURE. No I/O,
  no imports beyond .types. `recent_events` is a chronologically-ordered
  (oldest-first) slice of one project's event log -- callers pass
  `list(storage.iter_events(project))[-N:]`, matching every existing
  daemon.py `_ratchet_already_open`-style window convention. Detects:

  (a) notification storm: > cfg.notification_storm_count
      NOTIFICATION_REQUESTED events within the trailing cfg.window_seconds
      (total volume, key 'notification-storm:total'); OR > cfg.reason_storm_count
      SPEC_ATTENTION/NEEDS_OPERATOR events sharing the same
      (event type, payload['reason']) within the same window (one
      class/reason dominating, key 'notification-storm:<TYPE>:<reason>').

  (b) reconcile thrash: the TRAILING run of SPEC_ATTENTION events (in
      emission order across the full `recent_events`, not time-windowed --
      this is a per-CYCLE measure, not a per-TIME measure) that share the
      most recent reason is longer than cfg.thrash_consecutive_count
      (key 'reconcile-thrash:<reason>').

  (c) attempt/retry loop: a task_id with more than cfg.attempt_loop_count
      ATTEMPT_CREATED events within the trailing cfg.window_seconds AND no
      PROGRESS_RECORDED or MERGE_RECORDED event for that SAME task_id in
      the same window (i.e. repeated attempts with no forward progress)
      (key 'attempt-loop:<task_id>').

  (d) TICK_ERROR streak (CR-16, RISK-007): the TRAILING run of TICK_ERROR
      events at the very tail of `recent_events` (emission order, not
      time-windowed -- a per-CYCLE measure like (b)) is longer than
      cfg.tick_error_streak_count (key 'tick-error-streak', a single fixed
      key -- unlike (b)/(c) there is no reason/task_id to key on:
      TICK_ERROR's payload['error'] is a free-text exception repr, never
      safe to carry into a stable dedup key or a notification body, so the
      detail below reports a count only).

  Order is deterministic: (a) total-volume storm, then (a) per-reason
  storms sorted by (type, reason), then (b) thrash, then (c) attempt-loop
  sorted by task_id, then (d) the tick-error streak.

B13 2026-09-08 (nyxloom-P104) ADDS `resume_baseline` (and its private
helpers `_last_resume_index` / `_is_evidence_for` / `_newest_evidence_after`)
BELOW the frozen trio above. They are ADDITIVE -- WatchdogConfig, RunawaySignal and
detect_runaways are untouched, byte-for-byte, and the new functions are
just as pure (no I/O, no imports beyond .types). See `resume_baseline`'s
own docstring for the defect they fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .log import get_logger
from .types import Event, EventType

log = get_logger("watchdog")


@dataclass
class WatchdogConfig:
    """Safe, conservative defaults -- every threshold is comfortably above
    healthy operation but well below the shape of the 2026-07-16 incident
    (a notification every ~31s, indefinitely: that is ~116/hour)."""
    window_seconds: int = 3600
    notification_storm_count: int = 20
    reason_storm_count: int = 5
    thrash_consecutive_count: int = 5
    attempt_loop_count: int = 5
    # CR-16 2026-08-03 (RISK-007): a healthy pass occasionally hits a single
    # contained failure (the per-action isolation daemon.py's run_pass
    # already has) -- three or more IN A ROW is not that, it is a daemon
    # failing every cycle. Well below "every pass forever" (the RISK-007
    # incident shape) and well above the occasional single TICK_ERROR a
    # transient fault can legitimately produce.
    tick_error_streak_count: int = 3


@dataclass(frozen=True)
class RunawaySignal:
    pattern: str   # 'notification-storm' | 'reconcile-thrash' | 'attempt-loop'
                   # | 'tick-error-streak' (CR-16)
    key: str       # stable dedup key for the underlying condition
    detail: str    # short fixed-field description -- no event/handoff prose


def detect_runaways(recent_events: list[Event], cfg: WatchdogConfig) -> list[RunawaySignal]:
    """Pure. See module docstring for the three detection classes."""
    if not recent_events:
        return []

    signals: list[RunawaySignal] = []
    now = recent_events[-1].timestamp
    window_start = now - timedelta(seconds=cfg.window_seconds)
    windowed = [ev for ev in recent_events if ev.timestamp >= window_start]

    # (a) notification storm -- total volume within the window.
    notif_count = sum(1 for ev in windowed if ev.type is EventType.NOTIFICATION_REQUESTED)
    if notif_count > cfg.notification_storm_count:
        signals.append(RunawaySignal(
            pattern="notification-storm",
            key="notification-storm:total",
            detail=f"{notif_count} notifications in {cfg.window_seconds}s",
        ))

    # (a) notification storm -- one (type, reason) dominating the window.
    reason_counts: dict[tuple[str, str], int] = {}
    for ev in windowed:
        if ev.type in (EventType.SPEC_ATTENTION, EventType.NEEDS_OPERATOR):
            reason = ev.payload.get("reason")
            if reason:
                k = (ev.type.value, reason)
                reason_counts[k] = reason_counts.get(k, 0) + 1
    for (type_val, reason) in sorted(reason_counts):
        count = reason_counts[(type_val, reason)]
        if count > cfg.reason_storm_count:
            signals.append(RunawaySignal(
                pattern="notification-storm",
                key=f"notification-storm:{type_val}:{reason}",
                detail=f"{count}x {type_val}({reason}) in {cfg.window_seconds}s",
            ))

    # (b) reconcile thrash -- trailing same-reason run of SPEC_ATTENTION
    # events, in emission order (a per-CYCLE measure, so NOT time-windowed).
    spec_reasons = [
        ev.payload.get("reason") for ev in recent_events if ev.type is EventType.SPEC_ATTENTION
    ]
    if spec_reasons:
        current = spec_reasons[-1]
        run = 0
        for r in reversed(spec_reasons):
            if r == current:
                run += 1
            else:
                break
        if run > cfg.thrash_consecutive_count:
            signals.append(RunawaySignal(
                pattern="reconcile-thrash",
                key=f"reconcile-thrash:{current}",
                detail=f"SpecAttention({current}) emitted {run} consecutive times",
            ))

    # (c) attempt/retry loop -- same task_id, repeated attempts, no progress.
    attempts_by_task: dict[str, int] = {}
    progressed_tasks: set[str] = set()
    for ev in windowed:
        if ev.type is EventType.ATTEMPT_CREATED and ev.task_id:
            attempts_by_task[ev.task_id] = attempts_by_task.get(ev.task_id, 0) + 1
        elif ev.type in (EventType.PROGRESS_RECORDED, EventType.MERGE_RECORDED) and ev.task_id:
            progressed_tasks.add(ev.task_id)
    for task_id in sorted(attempts_by_task):
        count = attempts_by_task[task_id]
        if count > cfg.attempt_loop_count and task_id not in progressed_tasks:
            signals.append(RunawaySignal(
                pattern="attempt-loop",
                key=f"attempt-loop:{task_id}",
                detail=f"{count} attempts for {task_id} with no recorded progress",
            ))

    # (d) TICK_ERROR streak (CR-16, RISK-007) -- trailing run at the tail of
    # `recent_events`, in emission order (a per-CYCLE measure like (b), NOT
    # time-windowed): a daemon that is up and answering its TCP healthcheck
    # but raising every single pass. See module docstring.
    #
    # CR-16's own deadman heartbeat is deliberately NOT an event (it is a
    # gauge -- storage.record_heartbeat), which is what keeps this raw
    # adjacency scan meaningful: an event written at the end of every pass,
    # success or failure alike, would sit between every pair of TICK_ERRORs
    # and this pattern would never find two "consecutive".
    tick_error_run = 0
    for ev in reversed(recent_events):
        if ev.type is EventType.TICK_ERROR:
            tick_error_run += 1
        else:
            break
    if tick_error_run > cfg.tick_error_streak_count:
        signals.append(RunawaySignal(
            pattern="tick-error-streak",
            key="tick-error-streak",
            detail=f"{tick_error_run} consecutive TICK_ERROR events",
        ))

    return signals


# ---------------------------------------------------------------------------
# B13 2026-09-08 (nyxloom-P104): post-resume streak baseline.
#
# ADDITIVE to the frozen contract above -- these three functions do not touch
# WatchdogConfig / RunawaySignal / detect_runaways, and are PURE in exactly
# the same sense (no I/O, no imports beyond .types).
# ---------------------------------------------------------------------------

# The watchdog's OWN escalation event (daemon.py's _apply_watchdog appends
# NEEDS_OPERATOR{reason:'runaway', ...}). It is the watchdog's output, never
# independent evidence that the underlying condition is still worsening --
# and it happens to carry a `reason`, so without this exclusion it would
# feed detector (a)'s per-reason storm key
# 'notification-storm:NEEDS_OPERATOR:runaway' with its own tail.
_SELF_ESCALATION_REASON = "runaway"


def _last_resume_index(recent_events: list[Event]) -> int | None:
    """Index of the most recent PAUSE_CLEARED in `recent_events`, or None.

    PAUSE_CLEARED is the one event every operator resume surface appends --
    the control-plane HTTP handler (daemon.py `_post_config_pause`, mode
    'run'), both CLI paths (cli.py `cmd_resume`, incl. the forced override)
    and chat-ops (commands.py `_cmd_resume`) -- so it is the authoritative
    'the operator has acknowledged this' marker.

    Deliberately an INDEX, not a timestamp: `recent_events` is
    chronologically-ordered oldest-first by this module's own contract, and
    emission order is the ordering authority patterns (b)/(d) already use.
    Comparing positions is immune to same-second timestamp ties (several
    events can be appended within one reconcile pass) and to clock skew.
    """
    for i in range(len(recent_events) - 1, -1, -1):
        if recent_events[i].type is EventType.PAUSE_CLEARED:
            return i
    return None


def _is_evidence_for(sig: RunawaySignal, ev: Event) -> bool:
    """Is `ev` a fresh instance of the condition `sig` reports?

    One branch per detector, deriving the discriminator from the SAME parse
    of `RunawaySignal.key` the detector used to build it (and that
    daemon.py's `_suppress_runaway_action` uses to pick the action to drop),
    because the four patterns rest on different underlying event shapes.
    An unrecognised pattern returns True (fail-open: an unknown detector
    keeps its full pre-B13 escalation power rather than being silently
    disarmed).

    IMPORTANT (B13 review F1): this is only HALF of the evidence a caller
    needs, and for two patterns it is the half that can go permanently
    silent. For `reconcile-thrash:<reason>` and `attempt-loop:<task_id>`
    the event named here is exactly the one daemon.py's
    `_suppress_runaway_action` prevents from ever being appended again --
    the report is what gets suppressed, so the underlying condition can
    keep worsening while producing no event of this shape at all. The
    caller MUST also count "this pass would have suppressed an action for
    this signal" as evidence; see daemon.py `_apply_watchdog` and
    `resume_baseline`'s own note.
    """
    payload = ev.payload or {}
    if ev.type is EventType.NEEDS_OPERATOR and payload.get("reason") == _SELF_ESCALATION_REASON:
        return False

    if sig.pattern == "notification-storm":
        # 'notification-storm:total' | 'notification-storm:<TYPE>:<reason>'
        # (maxsplit=2 so a reason containing ':' stays intact).
        parts = sig.key.split(":", 2)
        if len(parts) < 3:
            return ev.type is EventType.NOTIFICATION_REQUESTED
        _, type_val, reason = parts
        return ev.type.value == type_val and payload.get("reason") == reason

    if sig.pattern == "reconcile-thrash":
        reason = sig.key.split(":", 1)[1] if ":" in sig.key else None
        return ev.type is EventType.SPEC_ATTENTION and payload.get("reason") == reason

    if sig.pattern == "attempt-loop":
        task_id = sig.key.split(":", 1)[1] if ":" in sig.key else None
        return ev.type is EventType.ATTEMPT_CREATED and ev.task_id == task_id

    if sig.pattern == "tick-error-streak":
        return ev.type is EventType.TICK_ERROR

    return True


def _newest_evidence_after(sig: RunawaySignal, recent_events: list[Event],
                           index: int) -> int | None:
    """`sequence` of the NEWEST event after position `index` that is fresh
    evidence for `sig`, or None if there is none.

    A sequence rather than a bool so the caller can tell evidence it has
    ALREADY counted from evidence that arrived since -- which is what makes
    a STRICT per-pass streak possible (see `resume_baseline`)."""
    newest: int | None = None
    for ev in recent_events[index + 1:]:
        if _is_evidence_for(sig, ev) and (newest is None or ev.sequence > newest):
            newest = ev.sequence
    return newest


def resume_baseline(sig: RunawaySignal,
                    recent_events: list[Event]) -> tuple[int | None, int | None]:
    """The per-signal post-resume streak baseline. Returns
    ``(resume_marker, newest_evidence)``:

    - ``resume_marker``: the ``sequence`` of the most recent PAUSE_CLEARED in
      `recent_events`, or None if the operator has never resumed within it.
      A STABLE identity (sequence, not index -- the window slides), so the
      caller can tell "the same resume I already counted from" apart from
      "a resume that has happened since my last pass" and reset its streak
      exactly once per resume.
    - ``newest_evidence``: the ``sequence`` of the newest event AFTER that
      resume that is a fresh instance of this signal's own condition, or
      None if there is none. Scanned over the WHOLE window when
      ``resume_marker`` is None (there is no resume to be new relative to).

    B13 (the defect this exists for): P49 froze the streak at 0 for every
    pass spent ALREADY PAUSED. It did NOT cover the passes AFTER a resume.
    `detect_runaways` re-detects a persistent-but-acknowledged condition
    identically on every pass -- a still-trailing `reconcile-thrash:<reason>`
    run, or a rejection history daemon.py's HISTORY_REJECTION_WINDOW_SECONDS
    keeps true for a full 7 days -- so the streak climbed back to
    RUNAWAY_PERSIST_AFTER_CYCLES on nothing but PASSES-SINCE-RESUME and
    re-paused the project a few cycles after every resume, defeating the
    operator's acknowledgment until the condition finally aged out. (P49's
    own reset does not even fire in the common shape, where the operator
    resumes before another pass runs while paused: the streak is still
    sitting AT the pause threshold, so the very first post-resume pass
    re-pauses.)

    The fix is a BASELINE SHIFT, not a decay: a resume drops the streak back
    to zero, and past that resume only genuinely NEW evidence may climb it
    again.

    THIS FUNCTION IS NOT THE WHOLE EVIDENCE TEST (review F1). Event-shaped
    evidence is necessary but NOT sufficient, because for
    `reconcile-thrash:<reason>` and `attempt-loop:<task_id>` the event it
    looks for is precisely the one `_suppress_runaway_action` stops from
    ever being appended again -- so a condition that is ACTIVELY worsening
    produces none, the streak freezes at 0, and the project can never
    re-pause. daemon.py's `_apply_watchdog` therefore ORs this with
    "this pass's plan contained an action this signal would suppress",
    which is live-this-pass proof that the planner is still trying to
    re-run the very thing the watchdog is blocking. See `_apply_watchdog`
    for why that second source cannot resurrect the original B13 bug.

    Safety-symmetric, deliberately:
      - Suppression is NOT gated by this. daemon.py still drops the matching
        repeating action on EVERY pass a signal is detected, resumed or not
        -- an acknowledgment must never re-arm a harmful action.
      - Escalation is NOT gated by this either (it has its own
        once-per-condition dedup).
      - A condition that IS still worsening still re-pauses.
      - No PAUSE_CLEARED in the window at all (never paused, or the resume
        has aged out of the caller's ~500-event slice) => pre-B13 behaviour,
        unconditional advance. Fail-open toward staying armed.
      - The watchdog's own NEEDS_OPERATOR{reason:'runaway'} escalation never
        counts as evidence (see _SELF_ESCALATION_REASON).
    """
    idx = _last_resume_index(recent_events)
    if idx is None:
        return None, _newest_evidence_after(sig, recent_events, -1)
    return recent_events[idx].sequence, _newest_evidence_after(sig, recent_events, idx)


def log_signals(signals: list[RunawaySignal]) -> None:
    """Diagnostic side effect for detected RunawaySignals (logging-P05b).

    Deliberately SEPARATE from `detect_runaways` above, which stays exactly
    as documented -- PURE, no I/O, no imports beyond `.types` -- so this
    function (and its `.log` import) never touches that frozen contract.
    This mirrors the established pure-core/logging-shell split this project
    already uses for reconcile.py's `plan_project` (docs/plan-logging.md
    §4.3, D-L5): the pure detector computes WHAT was detected; a thin,
    separate, explicitly-impure function decides how it's logged.

    Emits one WARNING per signal -- §5's own canonical WARNING example is
    "a watchdog suppression", and every detected RunawaySignal here
    corresponds 1:1 to daemon.py's `_apply_watchdog` escalating (a
    NEEDS_OPERATOR event) and/or suppressing a repeating action for it.
    `pattern`/`key`/`detail` are already injection-safe by this module's
    own frozen contract above (ids/reasons/enum values and counts only,
    never event/handoff prose), so they are safe to log verbatim.

    Integration note: call this right after `detect_runaways(...)`, e.g.
    daemon.py's `_apply_watchdog`:
        signals = detect_runaways(recent_events, cfg)
        log_signals(signals)
        ... (the existing escalate/suppress/auto-pause handling, unchanged)
    `daemon.py` is outside this package's touch scope (logging-P05b), so
    that one-line call site is NOT yet wired in -- see this phase's REPORT.
    """
    for sig in signals:
        log.warning("watchdog runaway detected", pattern=sig.pattern,
                    key=sig.key, detail=sig.detail)
