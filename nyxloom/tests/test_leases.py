"""Tests for nyxloom.leases (logging-P05b instrumentation sweep).

leases.py itself is FROZEN CORE (ARCHITECTURE §4, SPEC §11) -- the actual
flock(2) mutual-exclusion mechanics are untouched by this phase. These
tests exist to pin the NEW, additive logging behaviour added on top:

  - acquire() success -> DEBUG ("lease acquired")
  - acquire() miss on an exclusive (capacity<=1) lease -> INFO
    ("lease unavailable") -- routine contention, explains why a caller
    stays QUEUED, not an alarm
  - acquire() miss on EVERY slot of a counted (capacity>1) lease -> WARNING
    ("lease pool exhausted") -- the escalation-worthy case: the whole
    resource pool is saturated
  - Lease.release() -> DEBUG ("lease released")

Every assertion drives the REAL flock-backed acquire()/release() (no
mocking) and reads back real JSONL records via nyxloom.log.configure().
"""

from __future__ import annotations

import json

import pytest

from nyxloom import leases, log as nyx_log


def _read_records(path) -> list[dict]:
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


@pytest.fixture(autouse=True)
def _reset_log_after(tmp_path):
    """Local to this file only (conftest.py is FROZEN -- see test_log.py's
    own precedent for this same convention). Ensures no dangling file
    handler survives past this file's tests."""
    yield
    nyx_log.configure(level=nyx_log.CRITICAL, log_dir=None, console=False)


# ============================================================================
# acquire() -- exclusive (capacity<=1) lease
# ============================================================================

def test_acquire_exclusive_success_logs_debug(tmp_state, tmp_path):
    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.DEBUG, log_dir=log_dir, console=False)

    lease = leases.acquire("stack", owner="worker-1", purpose="drill")
    try:
        assert lease is not None

        records = _read_records(log_dir / "nyxloom.jsonl")
        acquired = [r for r in records if r["msg"] == "lease acquired"]
        assert len(acquired) == 1
        assert acquired[0]["level"] == "debug"
        assert acquired[0]["name"] == "stack"
        assert acquired[0]["owner"] == "worker-1"
        assert acquired[0]["capacity"] == 1
    finally:
        lease.release()


def test_acquire_exclusive_miss_logs_info(tmp_state, tmp_path):
    """A second acquire() attempt on an already-held exclusive lease fails
    (real flock contention, no mocking) and logs INFO, not DEBUG/WARNING --
    this is routine, expected contention (SPEC §11: non-blocking, the task
    just stays QUEUED), not an alarm."""
    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.DEBUG, log_dir=log_dir, console=False)

    holder = leases.acquire("stack", owner="worker-1", purpose="drill")
    assert holder is not None
    try:
        contender = leases.acquire("stack", owner="worker-2", purpose="drill")
        assert contender is None

        records = _read_records(log_dir / "nyxloom.jsonl")
        misses = [r for r in records if r["msg"] == "lease unavailable"]
        assert len(misses) == 1
        assert misses[0]["level"] == "info"
        assert misses[0]["name"] == "stack"
        assert misses[0]["owner"] == "worker-2"
        assert misses[0]["capacity"] == 1
    finally:
        holder.release()


# ============================================================================
# acquire() -- counted (capacity>1) lease
# ============================================================================

def test_acquire_counted_success_logs_debug_with_slot(tmp_state, tmp_path):
    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.DEBUG, log_dir=log_dir, console=False)

    lease = leases.acquire("pool", owner="worker-1", capacity=3)
    try:
        assert lease is not None
        records = _read_records(log_dir / "nyxloom.jsonl")
        acquired = [r for r in records if r["msg"] == "lease acquired"]
        assert len(acquired) == 1
        assert acquired[0]["level"] == "debug"
        assert acquired[0]["capacity"] == 3
        assert "slot" in acquired[0]
    finally:
        lease.release()


def test_acquire_counted_pool_exhausted_logs_warning(tmp_state, tmp_path):
    """Oracle 2 (the 'escalation-worthy' case): every slot of a capacity>1
    pool contended -> WARNING, distinct from a single exclusive-lease miss
    (INFO) above."""
    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.DEBUG, log_dir=log_dir, console=False)

    held = [leases.acquire("pool", owner=f"worker-{i}", capacity=2) for i in range(2)]
    assert all(h is not None for h in held)
    try:
        overflow = leases.acquire("pool", owner="worker-overflow", capacity=2)
        assert overflow is None

        records = _read_records(log_dir / "nyxloom.jsonl")
        exhausted = [r for r in records if r["msg"] == "lease pool exhausted"]
        assert len(exhausted) == 1
        assert exhausted[0]["level"] == "warning"
        assert exhausted[0]["name"] == "pool"
        assert exhausted[0]["owner"] == "worker-overflow"
        assert exhausted[0]["capacity"] == 2
    finally:
        for h in held:
            h.release()


# ============================================================================
# Lease.release()
# ============================================================================

def test_release_logs_debug(tmp_state, tmp_path):
    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.DEBUG, log_dir=log_dir, console=False)

    lease = leases.acquire("stack", owner="worker-1")
    assert lease is not None
    lease.release()

    records = _read_records(log_dir / "nyxloom.jsonl")
    released = [r for r in records if r["msg"] == "lease released"]
    assert len(released) == 1
    assert released[0]["level"] == "debug"
    assert released[0]["name"] == "stack"

    # Real behavioural proof this isn't hollow: releasing actually frees the
    # lock for a new acquirer.
    again = leases.acquire("stack", owner="worker-2")
    assert again is not None
    again.release()


# ============================================================================
# Level gating -- laziness rule: at INFO the DEBUG records are dropped, but
# the acquire()/release() call sites still execute unconditionally.
# ============================================================================

def test_debug_records_dropped_at_info_level_acquire_still_works(tmp_state, tmp_path):
    log_dir = tmp_path / "logs"
    nyx_log.configure(level=nyx_log.INFO, log_dir=log_dir, console=False)

    lease = leases.acquire("stack", owner="worker-1")
    try:
        assert lease is not None  # the lock mechanics are unaffected by log level
        records = _read_records(log_dir / "nyxloom.jsonl")
        assert not any(r["msg"] == "lease acquired" for r in records)
    finally:
        lease.release()
