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


# ============================================================================
# logging-P05b: log_signals -- the diagnostic shell around the pure detector
#
# detect_runaways stays untouched (PURE, no I/O, no imports beyond .types --
# its own frozen interface contract). log_signals is a NEW, separate,
# explicitly-impure function (mirrors reconcile.py's plan_project / daemon.py
# trace-flush split, docs/plan-logging.md §4.3) that a caller runs the real
# signals from detect_runaways through to get the WARNING log oracle asks
# for. Not yet wired into daemon.py's _apply_watchdog (out of this
# package's touch scope) -- see P05b-REPORT.md.
# ============================================================================

def test_log_signals_emits_warning_for_a_real_detected_runaway(tmp_path):
    """Oracle 1: a watchdog escalation emits WARNING or ERROR. Non-hollow:
    runs the REAL detector over a synthetic stream that genuinely trips the
    reconcile-thrash detector, feeds the REAL returned signals through
    log_signals, and asserts the emitted record's level and fields match."""
    import json

    from nyxloom import log as nyx_log
    from nyxloom.watchdog import log_signals

    base = _utc(2026, 7, 16, 12, 0)
    events = [
        make_event(i, EventType.SPEC_ATTENTION, base + timedelta(minutes=i),
                   payload={"reason": "rejections"})
        for i in range(1, 7)  # 6 consecutive > default threshold of 5
    ]
    signals = detect_runaways(events, WatchdogConfig())
    assert len(signals) == 1  # sanity: the detector really did fire
    sig = signals[0]

    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.WARNING, log_dir=log_dir, console=False)
    try:
        log_signals(signals)

        log_path = log_dir / "nyxloom.jsonl"
        assert log_path.exists()
        records = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(records) == 1
        rec = records[0]
        assert rec["level"] == "warning"
        assert rec["msg"] == "watchdog runaway detected"
        assert rec["pattern"] == sig.pattern == "reconcile-thrash"
        assert rec["key"] == sig.key == "reconcile-thrash:rejections"
        assert rec["detail"] == sig.detail
    finally:
        nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)


def test_log_signals_emits_one_warning_per_signal(tmp_path):
    """One WARNING record per detected signal -- multiple concurrent
    runaways (here: a notification-storm AND an attempt-loop, from
    genuinely distinct synthetic streams merged into one call) each get
    their own record, not a single collapsed one."""
    import json

    from nyxloom import log as nyx_log
    from nyxloom.watchdog import log_signals

    base = _utc(2026, 7, 16, 12, 0)
    storm_events = [
        make_event(i, EventType.NOTIFICATION_REQUESTED, base + timedelta(seconds=i))
        for i in range(1, 25)  # > default notification_storm_count of 20
    ]
    loop_events = [
        make_event(100 + i, EventType.ATTEMPT_CREATED, base + timedelta(minutes=i), task_id="demo-P09")
        for i in range(1, 8)  # > default attempt_loop_count of 5
    ]
    all_events = storm_events + loop_events
    signals = detect_runaways(all_events, WatchdogConfig())
    patterns = {s.pattern for s in signals}
    assert {"notification-storm", "attempt-loop"} <= patterns

    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.WARNING, log_dir=log_dir, console=False)
    try:
        log_signals(signals)
        log_path = log_dir / "nyxloom.jsonl"
        records = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(records) == len(signals)
        assert all(r["level"] == "warning" for r in records)
        assert {r["pattern"] for r in records} == patterns
    finally:
        nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)


def test_log_signals_empty_list_emits_nothing(tmp_path):
    """Laziness/no-noise control: an empty signal list (the overwhelmingly
    common case -- a healthy pass) logs nothing at all."""
    import json

    from nyxloom import log as nyx_log
    from nyxloom.watchdog import log_signals

    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.WARNING, log_dir=log_dir, console=False)
    try:
        log_signals([])
        log_path = log_dir / "nyxloom.jsonl"
        records = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()] \
            if log_path.exists() else []
        assert records == []
    finally:
        nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)
