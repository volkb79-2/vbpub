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

INTERFACE CONTRACT (frozen):

- WatchdogConfig: safe, conservative, configurable thresholds. Every field
  has a default so a caller need not tune anything to get protection.
- RunawaySignal: one detected pattern.
    pattern: 'notification-storm' | 'reconcile-thrash' | 'attempt-loop'
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

  Order is deterministic: (a) total-volume storm, then (a) per-reason
  storms sorted by (type, reason), then (b) thrash, then (c) attempt-loop
  sorted by task_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .types import Event, EventType


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


@dataclass(frozen=True)
class RunawaySignal:
    pattern: str   # 'notification-storm' | 'reconcile-thrash' | 'attempt-loop'
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

    return signals
