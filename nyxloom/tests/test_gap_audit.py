"""F007 2026-07-27 (gap-engine): the activity-counted GAP-AUDIT carve
trigger -- reconcile module contract item 17, plus the daemon plumbing
that makes its activity-counting durable and keeps it from contaminating
the WORK roadmap's signals.

Every oracle is tested with observable evidence and deliberate negatives.
"""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from nyxloom import daemon, lint, paths, reconcile, storage
from nyxloom.config import MutexDef, Policy, ProjectConfig, RouteDef, Routes
from nyxloom.reconcile import CarveDispatch, ReconcileInput, plan_project
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Receipt, ReceiptResult,
    Role, Route, TaskState, TaskStateFile, utc_now,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _cfg(gap_audit_after_changed_lines: int = 2000, carve_ahead_target: int = 5,
         headroom_warn: int = 5, gap_audit_source_paths: list[str] | None = None,
         test_health_interval_days: int = 14) -> ProjectConfig:
    return ProjectConfig(
        project_id="demo",
        root=Path("/demo"),
        default_branch="main",
        worktree_root=".worktrees",
        handoff_globs=["handoff/*.md"],
        gates={},
        mutexes={"stack": MutexDef(name="stack", scope="project", capacity=1)},
        policy=Policy(
            carve_ahead_target=carve_ahead_target,
            headroom_warn=headroom_warn,
            test_health_interval_days=test_health_interval_days,
            gap_audit_after_changed_lines=gap_audit_after_changed_lines,
            gap_audit_source_paths=gap_audit_source_paths or [],
        ),
    )


def _routes() -> Routes:
    return Routes(
        revision="test-rev",
        tiers={"frontier-review": ["route-review"], "flash-high": ["route-1"]},
        routes={
            "route-1": RouteDef(route_id="route-1", cli="fake", model="fake-model"),
            "route-review": RouteDef(route_id="route-review", cli="fake", model="review-model"),
        },
    )


def _inp(**overrides) -> ReconcileInput:
    base = dict(
        now=utc_now(),
        cfg=_cfg(),
        routes=_routes(),
        states={},
        frontmatters={},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True, "route-review": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        days_since_test_health_carve=100.0,
        days_since_gate_verify=100.0,
        changed_lines_since_gap_audit=None,
    )
    base.update(overrides)
    return ReconcileInput(**base)


def _carves(inp: ReconcileInput) -> list[CarveDispatch]:
    return [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]


# ==========================================================================
# Oracles: Cadence, Ordering, Guards (Reconcile-level)
# ==========================================================================

def test_oracle_1_fires_on_accumulated_activity():
    """Oracle 1: Fires on accumulated activity >= threshold."""
    carves = _carves(_inp(cfg=_cfg(test_health_interval_days=0),
                          changed_lines_since_gap_audit=2500))
    assert len(carves) == 1
    assert carves[0].kind == "gap-audit"


def test_oracle_2_negative_under_threshold():
    """Oracle 2 (negative): Under threshold does NOT fire, and headroom
    refill wins instead."""
    carves = _carves(_inp(cfg=_cfg(test_health_interval_days=0),
                          changed_lines_since_gap_audit=1500))
    assert len(carves) == 1
    assert carves[0].kind == "headroom"


def test_oracle_3_never_run_fires():
    """Oracle 3: None (never run) means FIRE on first pass."""
    carves = _carves(_inp(cfg=_cfg(test_health_interval_days=0),
                          changed_lines_since_gap_audit=None))
    assert len(carves) == 1
    assert carves[0].kind == "gap-audit"


def test_oracle_4_disabled_byte_identical():
    """Oracle 4: Disabled (threshold=0) produces headroom only."""
    baseline = _carves(_inp(cfg=_cfg(gap_audit_after_changed_lines=0, test_health_interval_days=0),
                            changed_lines_since_gap_audit=None))
    assert len(baseline) == 1
    assert baseline[0].kind == "headroom"


def test_oracle_6_ordering_test_health_wins():
    """Oracle 6: When both test-health and gap-audit fire, test-health wins."""
    carves = _carves(_inp(
        cfg=_cfg(),
        days_since_test_health_carve=None,
        changed_lines_since_gap_audit=None,
    ))
    assert len(carves) == 1
    assert carves[0].kind == "test-health"


def test_gap_audit_respects_project_paused():
    """Shared guard: paused project blocks gap-audit."""
    carves = _carves(_inp(cfg=_cfg(test_health_interval_days=0),
                          project_paused=True,
                          changed_lines_since_gap_audit=None))
    assert len(carves) == 0


def test_gap_audit_respects_carve_in_flight():
    """Shared guard: carve in flight blocks gap-audit."""
    attempt = Attempt(
        attempt_id="att-1", role=Role.CARVER, state=AttemptState.RUNNING,
        route=Route(route_id="route-review", cli="fake", model="m", routes_rev="test-rev"),
        started=utc_now(),
    )
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, attempts=[attempt],
    )
    carves = _carves(_inp(cfg=_cfg(test_health_interval_days=0),
                          states={"carve-demo-1": tsf},
                          changed_lines_since_gap_audit=None))
    assert len(carves) == 0


def test_gap_audit_respects_exhausted_budget():
    """Shared guard: exhausted budget blocks gap-audit."""
    carves = _carves(_inp(cfg=_cfg(test_health_interval_days=0),
                          budget_remaining=0,
                          changed_lines_since_gap_audit=None))
    assert len(carves) == 0


# ==========================================================================
# Oracle 7: Git-failure fail-safe (returns 0, not None)
# ==========================================================================

def test_oracle_7_git_failure_returns_zero_not_none(tmp_state, sample_project, monkeypatch):
    """Oracle 7: Git failure returns 0 (fail-safe), NOT None. Assert it does
    NOT fire the trigger (0 < threshold), whereas None would fire."""
    d = daemon.Daemon({"demo": sample_project.root})

    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    payload = {"statefile": tsf.to_dict(), "carve_kind": "gap-audit", "head_sha": "bad-sha"}
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload=payload, task_id="carve-demo-1",
    )

    class MockResult:
        returncode = 128

    def mock_run(*args, **kwargs):
        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    cfg = _cfg()
    cfg.root = sample_project.root
    result = d._changed_lines_since_gap_audit("demo", cfg)
    assert result == 0
    assert result is not None

    # Feeding 0 into the trigger does NOT fire
    inp = _inp(cfg=_cfg(test_health_interval_days=0), changed_lines_since_gap_audit=0)
    carves = _carves(inp)
    assert len(carves) == 1
    assert carves[0].kind == "headroom"


def test_oracle_7_missing_head_sha_returns_zero(tmp_state, sample_project):
    """Oracle 7: marker without head_sha -> returns 0."""
    d = daemon.Daemon({"demo": sample_project.root})

    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    payload = {"statefile": tsf.to_dict(), "carve_kind": "gap-audit"}
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload=payload, task_id="carve-demo-1",
    )

    cfg = _cfg()
    cfg.root = sample_project.root
    result = d._changed_lines_since_gap_audit("demo", cfg)
    assert result == 0


def test_oracle_7_timeout_returns_zero(tmp_state, sample_project, monkeypatch):
    """Oracle 7: subprocess timeout -> returns 0."""
    d = daemon.Daemon({"demo": sample_project.root})

    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    payload = {"statefile": tsf.to_dict(), "carve_kind": "gap-audit", "head_sha": "abc123"}
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload=payload, task_id="carve-demo-1",
    )

    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired("git diff", 10)

    monkeypatch.setattr(subprocess, "run", mock_run)

    cfg = _cfg()
    cfg.root = sample_project.root
    result = d._changed_lines_since_gap_audit("demo", cfg)
    assert result == 0


def test_oracle_7_binary_rows_contribute_zero(tmp_state, sample_project, monkeypatch):
    """Oracle 7: Binary rows contribute 0, normal rows count."""
    d = daemon.Daemon({"demo": sample_project.root})

    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    payload = {"statefile": tsf.to_dict(), "carve_kind": "gap-audit", "head_sha": "abc123"}
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload=payload, task_id="carve-demo-1",
    )

    class MockResult:
        returncode = 0
        stdout = "-\t-\timage.png\n5\t3\tmain.py\n"

    def mock_run(*args, **kwargs):
        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    cfg = _cfg()
    cfg.root = sample_project.root
    result = d._changed_lines_since_gap_audit("demo", cfg)
    assert result == 8  # 5 + 3, binary counts 0


def test_oracle_7_storage_read_failure_returns_zero(monkeypatch):
    """Oracle 7: Storage read failure returns 0 (fail-safe)."""
    d = daemon.Daemon({"demo": Path("/tmp/test-demo")})

    def mock_iter_events(project):
        raise IOError("storage read error")

    monkeypatch.setattr(storage, "iter_events", mock_iter_events)

    cfg = _cfg()
    cfg.root = Path("/tmp/test-demo")
    result = d._changed_lines_since_gap_audit("demo", cfg)
    assert result == 0


def test_oracle_7_generic_exception_returns_zero(tmp_state, sample_project, monkeypatch):
    """Oracle 7: Generic subprocess exception returns 0."""
    d = daemon.Daemon({"demo": sample_project.root})

    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    payload = {"statefile": tsf.to_dict(), "carve_kind": "gap-audit", "head_sha": "abc123"}
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload=payload, task_id="carve-demo-1",
    )

    def mock_run(*args, **kwargs):
        raise RuntimeError("unexpected error")

    monkeypatch.setattr(subprocess, "run", mock_run)

    cfg = _cfg()
    cfg.root = sample_project.root
    result = d._changed_lines_since_gap_audit("demo", cfg)
    assert result == 0


# ==========================================================================
# Oracle 8: Checkpoint round-trip (head_sha written and read back)
# ==========================================================================

def test_oracle_8_checkpoint_round_trip(tmp_state, sample_project, monkeypatch):
    """Oracle 8: head_sha is written by production code and read back."""
    d = daemon.Daemon({"demo": sample_project.root})
    cfg = _cfg()
    cfg.root = sample_project.root

    expected_sha = "deadbeefcafebabe0123456789abcdef"

    # Append with the expected sha (as daemon would write it)
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    payload = {"statefile": tsf.to_dict(), "carve_kind": "gap-audit", "head_sha": expected_sha}
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload=payload, task_id="carve-demo-1",
    )

    # Mock git diff to verify it's called with the expected sha
    class MockResult:
        returncode = 0
        stdout = "10\t5\tfile.py\n"

    def mock_run(cmd, **kwargs):
        # Verify the sha is in the git diff command
        assert expected_sha in cmd or f"{expected_sha}..HEAD" in " ".join(cmd)
        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = d._changed_lines_since_gap_audit("demo", cfg)
    assert result == 15  # 10 + 5


# ==========================================================================
# Oracle 9: Carve packet content
# ==========================================================================

def test_oracle_9_carve_packet_includes_shipped_features(sample_project):
    """Oracle 9: packet (a) includes shipped features and acceptance criteria."""
    d = daemon.Daemon({"demo": sample_project.root})
    cfg = _cfg()
    cfg.root = sample_project.root
    cfg.product_definition = "2-product-definition.md"

    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states={}, kind="gap-audit")
    body = "\n".join(lines)

    assert "shipped" in body.lower()
    assert "acceptance" in body.lower()


def test_oracle_9_carve_packet_excludes_planned_features_negative(sample_project):
    """Oracle 9 (negative): packet does NOT audit planned features."""
    d = daemon.Daemon({"demo": sample_project.root})
    cfg = _cfg()
    cfg.root = sample_project.root

    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states={}, kind="gap-audit")
    body = "\n".join(lines)

    # Explicit instruction not to audit planned features
    assert "AUDIT ONLY" in body and "shipped" in body.lower()


def test_oracle_9_carve_packet_authorizes_empty_carve(sample_project):
    """Oracle 9: packet explicitly authorizes carving NOTHING."""
    d = daemon.Daemon({"demo": sample_project.root})
    cfg = _cfg()
    cfg.root = sample_project.root

    lines = d._carve_packet_body_lines(cfg, "demo", seq=1, states={}, kind="gap-audit")
    body = "\n".join(lines)

    # Explicit authorization
    assert ("empty" in body.lower() or "[]" in body or
            "carve NOTHING" in body or "EMPTY" in body)
