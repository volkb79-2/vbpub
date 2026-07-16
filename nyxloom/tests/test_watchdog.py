"""Tests for nyxloom.watchdog. PACKAGE P44 (anti-runaway self-correction).

Oracle 3 (unit half): detect_runaways is a pure function -- these tests feed
it synthetic Event streams directly (no storage/daemon involved) and assert
on the returned RunawaySignal list. The daemon-integration half of Oracle 3
(auto-pause + single escalation across passes) lives in test_daemon.py,
since it needs Daemon.run_pass and the real event log.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nyxloom.types import Actor, ActorKind, Event, EventType
from nyxloom.watchdog import RunawaySignal, WatchdogConfig, detect_runaways


def _utc(*args, **kwargs) -> datetime:
    return datetime(*args, tzinfo=timezone.utc, **kwargs)


def make_event(seq: int, ev_type: EventType, ts: datetime, *, payload=None,
                task_id: str | None = None) -> Event:
    return Event(
        schema_version=1,
        sequence=seq,
        timestamp=ts,
        project="demo",
        actor=Actor(ActorKind.TICK, "nyxloomd"),
        type=ev_type,
        payload=payload or {},
        task_id=task_id,
    )


# ============================================================================
# baseline: empty / healthy streams -> no signals
# ============================================================================

def test_detect_runaways_empty_events_returns_empty():
    assert detect_runaways([], WatchdogConfig()) == []


def test_detect_runaways_healthy_stream_no_signals():
    """Oracle 3: a healthy stream (varied event types, single occurrences,
    progress recorded) returns none."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(1, EventType.TASK_CREATED, base, task_id="demo-P01"),
        make_event(2, EventType.ATTEMPT_CREATED, base + timedelta(minutes=1), task_id="demo-P01"),
        make_event(3, EventType.SPEC_ATTENTION, base + timedelta(minutes=2),
                   payload={"reason": "ratchet"}),
        make_event(4, EventType.MERGE_RECORDED, base + timedelta(minutes=3), task_id="demo-P01",
                   payload={"progress_units": ["u1"]}),
        make_event(5, EventType.NOTIFICATION_REQUESTED, base + timedelta(minutes=4)),
    ]
    assert detect_runaways(events, WatchdogConfig()) == []


# ============================================================================
# (b) reconcile thrash
# ============================================================================

def test_reconcile_thrash_detected_above_threshold():
    """Oracle 3: > K (default 5) consecutive SPEC_ATTENTION events sharing
    the same reason -> a 'reconcile-thrash' signal."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.SPEC_ATTENTION, base + timedelta(minutes=i),
                   payload={"reason": "rejections"})
        for i in range(1, 7)  # 6 consecutive > 5
    ]
    signals = detect_runaways(events, WatchdogConfig())
    thrash = [s for s in signals if s.pattern == "reconcile-thrash"]
    assert len(thrash) == 1
    assert thrash[0].key == "reconcile-thrash:rejections"


def test_reconcile_thrash_not_flagged_at_threshold():
    """Negative: exactly K (5) consecutive -- not > K -- stays healthy."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.SPEC_ATTENTION, base + timedelta(minutes=i),
                   payload={"reason": "rejections"})
        for i in range(1, 6)  # exactly 5
    ]
    assert detect_runaways(events, WatchdogConfig()) == []


def test_reconcile_thrash_reason_change_resets_run():
    """Negative: alternating reasons never build a run longer than 1, even
    over 10 total SPEC_ATTENTION events."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i + 1, EventType.SPEC_ATTENTION, base + timedelta(minutes=i),
                   payload={"reason": "rejections" if i % 2 == 0 else "carve-outcome"})
        for i in range(10)
    ]
    signals = detect_runaways(events, WatchdogConfig())
    assert [s for s in signals if s.pattern == "reconcile-thrash"] == []


def test_reconcile_thrash_custom_threshold_more_sensitive():
    """Configurability: the same 3-consecutive stream is healthy at the
    default threshold (5) but flags with a lower configured threshold."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.SPEC_ATTENTION, base + timedelta(minutes=i),
                   payload={"reason": "rejections"})
        for i in range(1, 4)  # 3 consecutive
    ]
    assert detect_runaways(events, WatchdogConfig()) == []
    signals = detect_runaways(events, WatchdogConfig(thrash_consecutive_count=2))
    assert any(s.pattern == "reconcile-thrash" and s.key == "reconcile-thrash:rejections"
               for s in signals)


# ============================================================================
# (a) notification storm
# ============================================================================

def test_notification_storm_total_volume_detected():
    """Oracle 3: > K (default 20) NOTIFICATION_REQUESTED events within the
    trailing window -> a 'notification-storm:total' signal (the shape of
    the real 2026-07-16 incident: one every ~31s, indefinitely)."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.NOTIFICATION_REQUESTED, base + timedelta(seconds=i * 10))
        for i in range(1, 25)  # 24 > 20, all within a few minutes (well inside 1hr window)
    ]
    signals = detect_runaways(events, WatchdogConfig())
    storms = [s for s in signals if s.key == "notification-storm:total"]
    assert len(storms) == 1


def test_notification_storm_total_volume_healthy_below_threshold():
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.NOTIFICATION_REQUESTED, base + timedelta(seconds=i * 10))
        for i in range(1, 15)  # 14 < 20
    ]
    assert detect_runaways(events, WatchdogConfig()) == []


def test_notification_storm_old_notifications_age_out_of_window():
    """Old notifications outside the trailing window (relative to the most
    recent event's timestamp) do not count toward the storm threshold --
    this is what lets a resolved storm stop being flagged."""
    base = _utc(2026, 7, 16, 9, 0)
    events = [
        make_event(i, EventType.NOTIFICATION_REQUESTED, base + timedelta(minutes=i * 6))
        for i in range(1, 31)  # spread over 180 minutes; only last ~60min is in-window
    ]
    signals = detect_runaways(events, WatchdogConfig())
    assert [s for s in signals if s.key == "notification-storm:total"] == []


def test_notification_storm_one_reason_dominates():
    """Oracle 3: > K (default 5) SPEC_ATTENTION/NEEDS_OPERATOR events
    sharing one (type, reason) within the window -> a per-reason storm
    signal, distinct from the total-volume one."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.NEEDS_OPERATOR, base + timedelta(minutes=i),
                   payload={"reason": "carve-ready"})
        for i in range(1, 8)  # 7 > 5
    ]
    signals = detect_runaways(events, WatchdogConfig())
    per_reason = [s for s in signals if s.key == "notification-storm:NEEDS_OPERATOR:carve-ready"]
    assert len(per_reason) == 1


# ============================================================================
# (c) attempt / retry loop
# ============================================================================

def test_attempt_loop_detected_no_progress():
    """Oracle 3: a task_id with > K (default 5) ATTEMPT_CREATED events and
    no PROGRESS_RECORDED/MERGE_RECORDED for it in the window -> an
    'attempt-loop' signal."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.ATTEMPT_CREATED, base + timedelta(minutes=i), task_id="demo-P01")
        for i in range(1, 8)  # 7 > 5
    ]
    signals = detect_runaways(events, WatchdogConfig())
    loops = [s for s in signals if s.pattern == "attempt-loop"]
    assert len(loops) == 1
    assert loops[0].key == "attempt-loop:demo-P01"


def test_attempt_loop_not_flagged_with_recorded_progress():
    """Negative: the same repeated-attempt shape, but a MERGE_RECORDED for
    that task_id lands in the window too -> no attempt-loop signal (real
    forward progress, not a stuck loop)."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.ATTEMPT_CREATED, base + timedelta(minutes=i), task_id="demo-P01")
        for i in range(1, 8)
    ]
    events.append(make_event(8, EventType.MERGE_RECORDED, base + timedelta(minutes=8),
                              task_id="demo-P01", payload={"progress_units": ["u1"]}))
    signals = detect_runaways(events, WatchdogConfig())
    assert [s for s in signals if s.pattern == "attempt-loop"] == []


def test_attempt_loop_not_flagged_below_threshold():
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.ATTEMPT_CREATED, base + timedelta(minutes=i), task_id="demo-P01")
        for i in range(1, 6)  # exactly 5, not > 5
    ]
    assert detect_runaways(events, WatchdogConfig()) == []


def test_attempt_loop_distinguishes_task_ids():
    """Only the task_id that actually exceeds the threshold is flagged."""
    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.ATTEMPT_CREATED, base + timedelta(minutes=i), task_id="demo-P01")
        for i in range(1, 8)
    ]
    events += [
        make_event(100 + i, EventType.ATTEMPT_CREATED, base + timedelta(minutes=i), task_id="demo-P02")
        for i in range(1, 3)  # only 2 -- healthy
    ]
    signals = detect_runaways(events, WatchdogConfig())
    loops = [s for s in signals if s.pattern == "attempt-loop"]
    assert len(loops) == 1
    assert loops[0].key == "attempt-loop:demo-P01"


# ============================================================================
# RunawaySignal itself
# ============================================================================

def test_runaway_signal_fields():
    sig = RunawaySignal(pattern="reconcile-thrash", key="reconcile-thrash:rejections",
                         detail="SpecAttention(rejections) emitted 6 consecutive times")
    assert sig.pattern == "reconcile-thrash"
    assert sig.key == "reconcile-thrash:rejections"
    assert "rejections" in sig.detail
