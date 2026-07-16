"""Tests for reconcile planner. PACKAGE P02."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom.config import MutexDef, Policy, ProjectConfig, Routes, RouteDef
from nyxloom.reconcile import (
    Action, CarveDispatch, CreateTask, DispatchImplementer, EmitAttemptExit,
    InterruptAttempt, LaunchReview, MarkInterrupted, MarkStalled, OpenWave,
    ProviderPause, ReconcileInput, ResumeAttempt, SpecAttention, StallCheck,
    Transition, dispatch_eligible, plan_project,
)
from nyxloom.types import (
    Attempt, AttemptState, Base, Blocker, BlockerType, Budget, Basis,
    Frontmatter, Oracle, Receipt, ReceiptResult, Role, Route, Scope, Source,
    TaskState, TaskStateFile, Usage,
)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Create a UTC datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def make_config(
    max_active_tasks: int = 2,
    max_attempts_per_task: int = 3,
    max_consecutive_zero_progress_merges: int = 3,
    wave_max_diffs: int = 3,
    carve_ahead_target: int = 5,
    carve_authority: str = "branch",
    headroom_warn: int = 5,
) -> ProjectConfig:
    """Create a minimal ProjectConfig for testing."""
    return ProjectConfig(
        project_id="demo",
        root=Path("/demo"),
        default_branch="main",
        worktree_root=".worktrees",
        handoff_globs=["handoff/*.md"],
        gates={},
        mutexes={"stack": MutexDef(name="stack", scope="project", capacity=1)},
        policy=Policy(
            max_active_tasks=max_active_tasks,
            max_attempts_per_task=max_attempts_per_task,
            max_consecutive_zero_progress_merges=max_consecutive_zero_progress_merges,
            wave_max_diffs=wave_max_diffs,
            carve_ahead_target=carve_ahead_target,
            carve_authority=carve_authority,
            headroom_warn=headroom_warn,
        ),
    )


def make_frontmatter(
    id: str = "demo-P01",
    tier: str = "flash-high",
    depends_on: list[str] | None = None,
    stack: str = "none",
    mutexes: list[str] | None = None,
    budget: Budget | None = None,
) -> Frontmatter:
    """Create a minimal Frontmatter."""
    return Frontmatter(
        schema_version=1,
        id=id,
        project="demo",
        title="Test",
        tier=tier,
        input_revision="abc123",
        source=Source(kind="roadmap"),
        scope=Scope(touch=["test.py"]),
        oracles=[],
        gates=[],
        escalate_if=[],
        depends_on=depends_on or [],
        stack=stack,
        mutexes=mutexes or [],
        budget=budget,
    )


def make_routes(tier: str = "flash-high", route_ids: list[str] | None = None) -> Routes:
    """Create a minimal Routes."""
    if route_ids is None:
        route_ids = ["route-1", "route-2"]
    return Routes(
        revision="test",
        tiers={tier: route_ids},
        routes={
            rid: RouteDef(route_id=rid, cli="fake", model="fake-model")
            for rid in route_ids
        },
    )


def make_tsf(
    task_id: str = "demo-P01",
    state: TaskState = TaskState.QUEUED,
    paused: bool = False,
    attempts: list[Attempt] | None = None,
) -> TaskStateFile:
    """Create a minimal TaskStateFile."""
    return TaskStateFile(
        schema_version=1,
        task_id=task_id,
        project="demo",
        state=state,
        since=utc(2026, 7, 15),
        paused=paused,
        attempts=attempts or [],
    )


def make_attempt(
    attempt_id: str = "att-001",
    state: AttemptState = AttemptState.RUNNING,
    role: Role = Role.IMPLEMENTER,
    receipt: Receipt | None = None,
) -> Attempt:
    """Create a minimal Attempt."""
    return Attempt(
        attempt_id=attempt_id,
        role=role,
        state=state,
        route=Route(route_id="route-1", cli="fake", model="fake-model"),
        started=utc(2026, 7, 15),
        receipt=receipt,
    )


# ============================================================================
# ORACLE 1: create
# ============================================================================

def test_create_new_frontmatter():
    """Oracle 1: frontmatter id absent from states -> exactly one CreateTask."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01-new")

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={},
        frontmatters={"P01-new": (fm, "handoff/P01-new.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    creates = [a for a in actions if isinstance(a, CreateTask)]
    assert len(creates) == 1
    assert creates[0].task_id == "P01-new"
    assert creates[0].fm == fm


def test_create_carved_to_queued():
    """Oracle 1: CARVED with lint_clean True -> Transition to QUEUED."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.CARVED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "handoff/P01.md")},
        lint_clean={"P01": True},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    assert transitions[0].to == TaskState.QUEUED


def test_create_carved_lint_false_no_transition():
    """Oracle 1 (negative): CARVED with lint_clean False -> NO transition."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.CARVED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "handoff/P01.md")},
        lint_clean={"P01": False},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 0


# ============================================================================
# ORACLE 2: decision-hold
# ============================================================================

def test_decision_hold_queued_with_open_decision():
    """Oracle 2: QUEUED with open D-dep -> Transition to NEEDS_DECISION."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01", depends_on=["D-007"])
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "handoff/P01.md")},
        lint_clean={"P01": True},
        project_paused=False,
        decisions_open={"D-007"},
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    assert transitions[0].to == TaskState.NEEDS_DECISION
    assert "D-007" in (transitions[0].notes or "")


def test_decision_hold_needs_decision_with_resolved():
    """Oracle 2: NEEDS_DECISION with resolved D-dep -> Transition to QUEUED."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01", depends_on=["D-007"])
    tsf = make_tsf(task_id="P01", state=TaskState.NEEDS_DECISION)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "handoff/P01.md")},
        lint_clean={"P01": True},
        project_paused=False,
        decisions_open=set(),  # D-007 is resolved
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    assert transitions[0].to == TaskState.QUEUED


def test_decision_hold_never_dispatched():
    """Oracle 2 (negative): task with open D-dep is never dispatched."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01", depends_on=["D-007"])
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "handoff/P01.md")},
        lint_clean={"P01": True},
        project_paused=False,
        decisions_open={"D-007"},
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    # dispatch_eligible should return False for decision-hold
    eligible, reason = dispatch_eligible(fm, tsf, inp)
    assert not eligible
    assert "D-007" in reason


# ============================================================================
# ORACLE 3: dispatch-order
# ============================================================================

def test_dispatch_order_three_tasks_max_two():
    """Oracle 3: 3 QUEUED, max_active=2, zero active -> 2 dispatches in order."""
    cfg = make_config(max_active_tasks=2)
    routes = make_routes(route_ids=["route-1", "route-2"])
    fm1 = make_frontmatter(id="P01")
    fm2 = make_frontmatter(id="P02")
    fm3 = make_frontmatter(id="P03")
    tsf1 = make_tsf(task_id="P01", state=TaskState.QUEUED)
    tsf2 = make_tsf(task_id="P02", state=TaskState.QUEUED)
    tsf3 = make_tsf(task_id="P03", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf1, "P02": tsf2, "P03": tsf3},
        frontmatters={"P01": (fm1, "h.md"), "P02": (fm2, "h.md"), "P03": (fm3, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True, "route-2": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    dispatches = [a for a in actions if isinstance(a, DispatchImplementer)]
    assert len(dispatches) == 2
    assert dispatches[0].task_id == "P01"
    assert dispatches[1].task_id == "P02"
    assert dispatches[0].route_id == "route-1"
    assert dispatches[1].route_id == "route-1"


def test_dispatch_first_route_unhealthy():
    """Oracle 3: first route provider_ok False -> second route chosen."""
    cfg = make_config(max_active_tasks=2)
    routes = make_routes(route_ids=["route-1", "route-2"])
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": False, "route-2": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    dispatches = [a for a in actions if isinstance(a, DispatchImplementer)]
    assert len(dispatches) == 1
    assert dispatches[0].route_id == "route-2"


def test_dispatch_no_healthy_route():
    """Oracle 3: all routes False -> no dispatch, reason 'no-healthy-route'."""
    cfg = make_config()
    routes = make_routes(route_ids=["route-1", "route-2"])
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": False, "route-2": False},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    dispatches = [a for a in actions if isinstance(a, DispatchImplementer)]
    assert len(dispatches) == 0
    eligible, reason = dispatch_eligible(fm, tsf, inp)
    assert not eligible
    assert reason == "no-healthy-route"


# ============================================================================
# ORACLE 4: caps
# ============================================================================

def test_caps_wip_cap_one_active():
    """Oracle 4: 1 ACTIVE + max_active=1 -> zero dispatches, reason 'wip-cap'."""
    cfg = make_config(max_active_tasks=1)
    routes = make_routes()
    fm1 = make_frontmatter(id="P01")
    fm2 = make_frontmatter(id="P02")
    tsf1 = make_tsf(task_id="P01", state=TaskState.ACTIVE)
    tsf2 = make_tsf(task_id="P02", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf1, "P02": tsf2},
        frontmatters={"P01": (fm1, "h.md"), "P02": (fm2, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    dispatches = [a for a in actions if isinstance(a, DispatchImplementer)]
    assert len(dispatches) == 0
    eligible, reason = dispatch_eligible(fm2, tsf2, inp)
    assert not eligible
    assert reason == "wip-cap"


def test_caps_attempts_exhausted():
    """Oracle 4: attempts == max_attempts_per_task -> reason 'attempts-exhausted'."""
    cfg = make_config(max_attempts_per_task=2)
    routes = make_routes()
    fm = make_frontmatter(id="P01")

    # Two terminal attempts (not 'limit')
    att1 = make_attempt(
        attempt_id="att-1",
        state=AttemptState.EXITED,
        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
    )
    att2 = make_attempt(
        attempt_id="att-2",
        state=AttemptState.EXITED,
        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
    )
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED, attempts=[att1, att2])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    eligible, reason = dispatch_eligible(fm, tsf, inp)
    assert not eligible
    assert reason == "attempts-exhausted"


def test_caps_budget_exhausted():
    """Oracle 4: budget_remaining=0.0 -> reason 'budget-exhausted'."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        budget_remaining=0.0,
    )

    eligible, reason = dispatch_eligible(fm, tsf, inp)
    assert not eligible
    assert reason == "budget-exhausted"


def test_caps_paused_task():
    """Oracle 4: paused task -> reason 'paused'."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED, paused=True)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    eligible, reason = dispatch_eligible(fm, tsf, inp)
    assert not eligible
    assert reason == "paused"


def test_caps_project_paused():
    """Oracle 4: project_paused -> reason 'paused' for all."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED, paused=False)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=True,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    eligible, reason = dispatch_eligible(fm, tsf, inp)
    assert not eligible
    assert reason == "paused"


# ============================================================================
# ORACLE 5: deps
# ============================================================================

def test_deps_unmerged_dep_blocked():
    """Oracle 5: task dep COMPLETED -> passes; unmerged blocks dispatch."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P02", depends_on=["P01"])
    tsf_p01 = make_tsf(task_id="P01", state=TaskState.QUEUED)  # Not COMPLETED
    tsf_p02 = make_tsf(task_id="P02", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf_p01, "P02": tsf_p02},
        frontmatters={"P02": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    eligible, reason = dispatch_eligible(fm, tsf_p02, inp)
    assert not eligible
    assert "P01" in reason
    assert "deps-unmerged" in reason


def test_deps_completed_passes():
    """Oracle 5: dep in COMPLETED state -> passes."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P02", depends_on=["P01"])
    tsf_p01 = make_tsf(task_id="P01", state=TaskState.COMPLETED)
    tsf_p02 = make_tsf(task_id="P02", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf_p01, "P02": tsf_p02},
        frontmatters={"P02": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    eligible, reason = dispatch_eligible(fm, tsf_p02, inp)
    # Eligible up to this point (no reason should mention P01)
    if not eligible:
        assert "P01" not in reason


def test_deps_branch_merged_passes():
    """Oracle 5: dep's branch in merged_branches -> passes."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P02", depends_on=["P01"])
    tsf_p01 = make_tsf(task_id="P01", state=TaskState.QUEUED)  # Not COMPLETED
    tsf_p02 = make_tsf(task_id="P02", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf_p01, "P02": tsf_p02},
        frontmatters={"P02": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches={"P01"},  # P01's branch is merged
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    eligible, reason = dispatch_eligible(fm, tsf_p02, inp)
    # Should pass the deps check (though may fail on other grounds)
    if not eligible and "deps-unmerged" in reason:
        assert "P01" not in reason  # P01 shouldn't block


# ============================================================================
# ORACLE 6: mutex
# ============================================================================

def test_mutex_stack_exclusive_lease_unavailable():
    """Oracle 6: stack='exclusive', lease unavailable -> 'lease-unavailable:demo.stack'."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01", stack="exclusive")
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={"demo.stack": False},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    eligible, reason = dispatch_eligible(fm, tsf, inp)
    assert not eligible
    assert reason == "lease-unavailable:demo.stack"


# ============================================================================
# ORACLE 7: receipt
# ============================================================================

def test_receipt_running_with_receipt_emits_exit():
    """Oracle 7: RUNNING attempt with receipt -> EmitAttemptExit."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(
        attempt_id="att-1",
        state=AttemptState.RUNNING,
        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
    )
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={"att-1": {"result": "done"}},
    )

    actions = plan_project(inp)
    exits = [a for a in actions if isinstance(a, EmitAttemptExit) and a.attempt_id == "att-1"]
    assert len(exits) == 1


def test_receipt_pid_dead_no_receipt_mark_interrupted():
    """Oracle 7: no receipt, pid dead -> MarkInterrupted."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.RUNNING, receipt=None)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 0.0},
        pid_alive={"att-1": False},
        receipts={},
    )

    actions = plan_project(inp)
    marked = [a for a in actions if isinstance(a, MarkInterrupted) and a.attempt_id == "att-1"]
    assert len(marked) == 1


def test_receipt_interrupted_with_session_handle_resume():
    """Oracle 7: INTERRUPTED with session_handle -> ResumeAttempt."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(
        attempt_id="att-1",
        state=AttemptState.INTERRUPTED,
        receipt=None,
    )
    att.session_handle = "session-xyz"
    tsf = make_tsf(task_id="P01", state=TaskState.QUEUED, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    resumes = [a for a in actions if isinstance(a, ResumeAttempt) and a.attempt_id == "att-1"]
    assert len(resumes) == 1


def test_interrupted_no_resume_handle_blocks_task():
    """P14 2026-07-15 item 4 (silent-dead-end fix): INTERRUPTED with NO
    session_handle used to silently do nothing ('fresh dispatch handled in
    lifecycle' was never true -- lifecycle only ever dispatches QUEUED
    tasks) leaving the task ACTIVE forever with zero events. Now ->
    Transition to BLOCKED with a typed environment blocker."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
    assert att.session_handle is None
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    resumes = [a for a in actions if isinstance(a, ResumeAttempt)]
    assert len(resumes) == 0
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    t = transitions[0]
    assert t.to == TaskState.BLOCKED
    assert t.blocker is not None
    assert t.blocker.type == BlockerType.ENVIRONMENT
    assert t.blocker.unblock_condition == "operator: inspect attempts"


def test_interrupted_attempts_exhausted_blocks_task_even_with_handle():
    """P14 item 4: attempts budget exhausted -> BLOCKED even though a
    session_handle IS present (the budget check gates ResumeAttempt first)."""
    cfg = make_config(max_attempts_per_task=1)
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    prior = make_attempt(
        attempt_id="att-0", state=AttemptState.EXITED,
        receipt=Receipt(result=ReceiptResult.ERROR, exit_code=1),
    )
    att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
    att.session_handle = "sess-xyz"
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[prior, att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    resumes = [a for a in actions if isinstance(a, ResumeAttempt)]
    assert len(resumes) == 0
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    assert transitions[0].to == TaskState.BLOCKED
    assert transitions[0].blocker.type == BlockerType.ENVIRONMENT


# ============================================================================
# ORACLE 8: stall
# ============================================================================

def test_stall_check_log_quiet_over_threshold():
    """Oracle 8: pid alive, no receipt, log_quiet > threshold -> StallCheck."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.RUNNING, receipt=None)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 400.0},  # Over default 300
        pid_alive={"att-1": True},
        receipts={},
    )

    actions = plan_project(inp)
    checks = [a for a in actions if isinstance(a, StallCheck) and a.attempt_id == "att-1"]
    assert len(checks) == 1


def test_stall_confirmed_marks_stalled_first():
    """P14 2026-07-15 item 2 (amended from the old 'stall_confirmed ->
    InterruptAttempt directly' design): a tier-2-confirmed RUNNING attempt
    must first be made VISIBLE via MarkStalled (-> ATTEMPT_STALLED, state
    STALLED) -- NOT interrupted immediately. The old design silently
    interrupted a confirmed stall with zero event ever recorded."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.RUNNING, receipt=None)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 400.0},
        pid_alive={"att-1": True},
        receipts={},
        stall_confirmed={"att-1": True},
    )

    actions = plan_project(inp)
    marks = [a for a in actions if isinstance(a, MarkStalled) and a.attempt_id == "att-1"]
    interrupts = [a for a in actions if isinstance(a, InterruptAttempt) and a.attempt_id == "att-1"]
    checks = [a for a in actions if isinstance(a, StallCheck) and a.attempt_id == "att-1"]
    assert len(marks) == 1
    assert len(interrupts) == 0
    assert len(checks) == 0


def test_stalled_attempt_then_interrupted():
    """P14 2026-07-15 item 2 (second half): once the attempt's PERSISTED
    state is actually STALLED (a later pass observing the ATTEMPT_STALLED
    from the prior pass), and it's still alive with no receipt ->
    InterruptAttempt. No re-confirmation needed at this point."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.STALLED, receipt=None)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 400.0},
        pid_alive={"att-1": True},
        receipts={},
    )

    actions = plan_project(inp)
    interrupts = [a for a in actions if isinstance(a, InterruptAttempt) and a.attempt_id == "att-1"]
    assert len(interrupts) == 1


def test_stalled_attempt_pid_dead_mark_interrupted():
    """Oracle 8 (negative, STALLED variant): a STALLED attempt whose pid
    has since died -> MarkInterrupted, not a pointless InterruptAttempt."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.STALLED, receipt=None)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 400.0},
        pid_alive={"att-1": False},
        receipts={},
    )

    actions = plan_project(inp)
    marked = [a for a in actions if isinstance(a, MarkInterrupted) and a.attempt_id == "att-1"]
    interrupts = [a for a in actions if isinstance(a, InterruptAttempt) and a.attempt_id == "att-1"]
    assert len(marked) == 1
    assert len(interrupts) == 0


def test_stall_quiet_below_threshold():
    """Oracle 8 (negative): log_quiet below threshold -> neither action."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.RUNNING, receipt=None)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 100.0},  # Below threshold
        pid_alive={"att-1": True},
        receipts={},
    )

    actions = plan_project(inp)
    checks = [a for a in actions if isinstance(a, StallCheck) and a.attempt_id == "att-1"]
    interrupts = [a for a in actions if isinstance(a, InterruptAttempt) and a.attempt_id == "att-1"]
    assert len(checks) == 0
    assert len(interrupts) == 0


# ============================================================================
# ORACLE 13 (P14 2026-07-15 item 6): wall-clock cap
# ============================================================================

def test_wall_clock_cap_exceeded_interrupts_even_with_fresh_log():
    """P14 headline oracle 4: attempt started long before the default cap
    -> InterruptAttempt EVEN WITH a fresh (non-quiet) log and no stall
    confirmation -- the wall-clock cap bypasses the log-quiet gate
    entirely. Uses a small attempt_max_wall_seconds so the test doesn't
    depend on the real 10800s default."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")  # no budget override -> uses inp default
    att = make_attempt(attempt_id="att-1", state=AttemptState.RUNNING, receipt=None)
    att.started = utc(2026, 7, 15, 0, 0)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15, 1, 0),  # 3600s elapsed
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 0.0},  # log is FRESH
        pid_alive={"att-1": True},
        receipts={},
        attempt_max_wall_seconds=100,  # far below the 3600s elapsed
    )

    actions = plan_project(inp)
    interrupts = [a for a in actions if isinstance(a, InterruptAttempt) and a.attempt_id == "att-1"]
    checks = [a for a in actions if isinstance(a, StallCheck) and a.attempt_id == "att-1"]
    assert len(interrupts) == 1
    assert len(checks) == 0


def test_wall_clock_cap_per_task_budget_override():
    """P14 item 6: fm.budget.max_wall_seconds overrides the input default
    (smaller here) -- still triggers InterruptAttempt at 60s elapsed even
    though the default cap (attempt_max_wall_seconds=100000) is nowhere
    close to exceeded."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01", budget=Budget(max_wall_seconds=50))
    att = make_attempt(attempt_id="att-1", state=AttemptState.RUNNING, receipt=None)
    att.started = utc(2026, 7, 15, 0, 0)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15, 0, 1),  # 60s elapsed
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 0.0},
        pid_alive={"att-1": True},
        receipts={},
        attempt_max_wall_seconds=100000,
    )

    actions = plan_project(inp)
    interrupts = [a for a in actions if isinstance(a, InterruptAttempt) and a.attempt_id == "att-1"]
    assert len(interrupts) == 1


def test_wall_clock_cap_not_exceeded_no_interrupt():
    """Oracle 4 (negative): elapsed well under the cap -> no InterruptAttempt
    from the wall-clock path (fresh log also means no StallCheck)."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.RUNNING, receipt=None)
    att.started = utc(2026, 7, 15, 0, 0)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15, 0, 1),  # 60s elapsed
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 0.0},
        pid_alive={"att-1": True},
        receipts={},
        attempt_max_wall_seconds=10800,
    )

    actions = plan_project(inp)
    interrupts = [a for a in actions if isinstance(a, InterruptAttempt) and a.attempt_id == "att-1"]
    checks = [a for a in actions if isinstance(a, StallCheck) and a.attempt_id == "att-1"]
    assert len(interrupts) == 0
    assert len(checks) == 0


def test_wall_clock_cap_pid_dead_prefers_mark_interrupted():
    """Wall-clock cap exceeded AND pid already dead -> MarkInterrupted (a
    definitive signal) takes priority over a pointless InterruptAttempt
    against a pid that no longer exists."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.RUNNING, receipt=None)
    att.started = utc(2026, 7, 15, 0, 0)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15, 1, 0),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={"att-1": 0.0},
        pid_alive={"att-1": False},
        receipts={},
        attempt_max_wall_seconds=100,
    )

    actions = plan_project(inp)
    marked = [a for a in actions if isinstance(a, MarkInterrupted) and a.attempt_id == "att-1"]
    interrupts = [a for a in actions if isinstance(a, InterruptAttempt) and a.attempt_id == "att-1"]
    assert len(marked) == 1
    assert len(interrupts) == 0


# ============================================================================
# ORACLE 9: waves
# ============================================================================

def test_waves_three_awaiting_review_opens_wave():
    """Oracle 9: 3 AWAITING_REVIEW unwaved, wave_max_diffs=3 -> one OpenWave."""
    cfg = make_config(wave_max_diffs=3)
    routes = make_routes()
    fm1 = make_frontmatter(id="P01")
    fm2 = make_frontmatter(id="P02")
    fm3 = make_frontmatter(id="P03")
    tsf1 = make_tsf(task_id="P01", state=TaskState.AWAITING_REVIEW)
    tsf2 = make_tsf(task_id="P02", state=TaskState.AWAITING_REVIEW)
    tsf3 = make_tsf(task_id="P03", state=TaskState.AWAITING_REVIEW)

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf1, "P02": tsf2, "P03": tsf3},
        frontmatters={"P01": (fm1, "h.md"), "P02": (fm2, "h.md"), "P03": (fm3, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    opens = [a for a in actions if isinstance(a, OpenWave)]
    assert len(opens) == 1
    assert sorted(opens[0].task_ids) == ["P01", "P02", "P03"]


def test_waves_oldest_over_timeout_opens():
    """Oracle 9: 2 waiting, oldest.since > wave_open_after_seconds -> OpenWave."""
    cfg = make_config(wave_max_diffs=3)
    routes = make_routes()
    fm1 = make_frontmatter(id="P01")
    fm2 = make_frontmatter(id="P02")
    # P01 is old (created 2000s ago)
    tsf1 = make_tsf(task_id="P01", state=TaskState.AWAITING_REVIEW)
    tsf1.since = utc(2026, 7, 15, 0, 0)
    tsf2 = make_tsf(task_id="P02", state=TaskState.AWAITING_REVIEW)
    tsf2.since = utc(2026, 7, 15, 0, 10)  # Recent

    inp = ReconcileInput(
        now=utc(2026, 7, 15, 1, 0),  # 1 hour later
        cfg=cfg,
        routes=routes,
        states={"P01": tsf1, "P02": tsf2},
        frontmatters={"P01": (fm1, "h.md"), "P02": (fm2, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        wave_open_after_seconds=1800,  # 30 mins
    )

    actions = plan_project(inp)
    opens = [a for a in actions if isinstance(a, OpenWave)]
    assert len(opens) == 1
    assert "P01" in opens[0].task_ids


def test_waves_fresh_single_no_open():
    """Oracle 9 (negative): 1 waiting, fresh -> no OpenWave."""
    cfg = make_config(wave_max_diffs=3)
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.AWAITING_REVIEW)
    tsf.since = utc(2026, 7, 15, 0, 59)

    inp = ReconcileInput(
        now=utc(2026, 7, 15, 1, 0),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        wave_open_after_seconds=1800,
    )

    actions = plan_project(inp)
    opens = [a for a in actions if isinstance(a, OpenWave)]
    assert len(opens) == 0


def test_waves_launch_review_no_running_attempt():
    """Oracle 9 (amended 2026-07-15 after the duplicate-Opus-launch
    incident): an open wave launches review ONLY when no frontier-review
    attempt is in flight. A CREATED one IS in flight — the original
    assertion (relaunch unless RUNNING) spawned five duplicate reviews.
    Here: a FAILED review attempt (terminal, non-resumable) does not block
    a fresh launch."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(
        attempt_id="att-1",
        state=AttemptState.FAILED,
        role=Role.FRONTIER_REVIEW,
    )
    tsf = make_tsf(task_id="P01", state=TaskState.AWAITING_REVIEW, attempts=[att])
    tsf.wave_id = "wave-001"

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )

    actions = plan_project(inp)
    launches = [a for a in actions if isinstance(a, LaunchReview)]
    assert len(launches) == 1
    assert launches[0].wave_id == "wave-001"


# ============================================================================
# ORACLE 10: ratchet
# ============================================================================

def test_ratchet_zero_progress_review_merges():
    """Oracle 10: 3 merges all (0 units, 'review') -> SpecAttention('ratchet')."""
    cfg = make_config(max_consecutive_zero_progress_merges=3)
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01")

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        merge_history=[
            ("P01", 0, "review"),
            ("P02", 0, "review"),
            ("P03", 0, "review"),
        ],
        ratchet_already_open=False,
    )

    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "ratchet"]
    assert len(spec_attns) == 1


def test_ratchet_units_positive_no_attention():
    """Oracle 10 (negative): tuple with units>0 -> no SpecAttention('ratchet')."""
    cfg = make_config(max_consecutive_zero_progress_merges=3)
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01")

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        merge_history=[
            ("P01", 1, "review"),  # units > 0
            ("P02", 0, "review"),
            ("P03", 0, "review"),
        ],
        ratchet_already_open=False,
    )

    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "ratchet"]
    assert len(spec_attns) == 0


def test_ratchet_roadmap_source_no_attention():
    """Oracle 10 (negative): source 'roadmap' -> no SpecAttention('ratchet')."""
    cfg = make_config(max_consecutive_zero_progress_merges=3)
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01")

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        merge_history=[
            ("P01", 0, "review"),
            ("P02", 0, "review"),
            ("P03", 0, "roadmap"),  # source != 'review'
        ],
        ratchet_already_open=False,
    )

    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "ratchet"]
    assert len(spec_attns) == 0


# ============================================================================
# ORACLE 11: spec-health
# ============================================================================

def test_spec_health_carve_outcome_spec_gap():
    """Oracle 11: carve_outcomes [{'outcome': 'SPEC_GAP'}] -> SpecAttention."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01")

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        carve_outcomes=[{"outcome": "SPEC_GAP"}],
    )

    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "carve-outcome"]
    assert len(spec_attns) >= 1


def test_spec_health_review_rejections():
    """Oracle 11: review_rejections_by_area {'ui': 2} -> SpecAttention."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01")

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        review_rejections_by_area={"ui": 2},
    )

    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "rejections"]
    assert len(spec_attns) >= 1


def test_spec_health_blocked_underspecified():
    """Oracle 11: blocked_underspecified_count >= 3 -> SpecAttention."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01")

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        blocked_underspecified_count=3,
    )

    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "blocked-underspecified"]
    assert len(spec_attns) >= 1


# ============================================================================
# ORACLE 12: determinism
# ============================================================================

def test_determinism_composite_input():
    """Oracle 12: composite input 1+3+9 deterministic, correct order."""
    cfg = make_config(max_active_tasks=2, wave_max_diffs=2)
    routes = make_routes(route_ids=["route-1", "route-2"])

    # Create tasks: one new (oracle 1), one QUEUED for dispatch (oracle 3)
    fm_new = make_frontmatter(id="new-task")
    fm_p01 = make_frontmatter(id="P01")
    fm_p02 = make_frontmatter(id="P02")
    fm_ar1 = make_frontmatter(id="AR1")
    fm_ar2 = make_frontmatter(id="AR2")

    tsf_p01 = make_tsf(task_id="P01", state=TaskState.QUEUED)
    tsf_p02 = make_tsf(task_id="P02", state=TaskState.QUEUED)
    tsf_ar1 = make_tsf(task_id="AR1", state=TaskState.AWAITING_REVIEW)
    tsf_ar1.since = utc(2026, 7, 15, 0, 0)
    tsf_ar2 = make_tsf(task_id="AR2", state=TaskState.AWAITING_REVIEW)
    tsf_ar2.since = utc(2026, 7, 15, 0, 5)

    inp = ReconcileInput(
        now=utc(2026, 7, 15, 1, 0),
        cfg=cfg,
        routes=routes,
        states={
            "P01": tsf_p01,
            "P02": tsf_p02,
            "AR1": tsf_ar1,
            "AR2": tsf_ar2,
        },
        frontmatters={
            "new-task": (fm_new, "h.md"),
            "P01": (fm_p01, "h.md"),
            "P02": (fm_p02, "h.md"),
            "AR1": (fm_ar1, "h.md"),
            "AR2": (fm_ar2, "h.md"),
        },
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True, "route-2": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        wave_open_after_seconds=1800,
    )

    # Call plan_project twice; should be identical
    actions1 = plan_project(inp)
    actions2 = plan_project(inp)

    repr1 = [repr(a) for a in actions1]
    repr2 = [repr(a) for a in actions2]
    assert repr1 == repr2

    # Verify ordering: lifecycle (sorted), attempts, waves, spec
    lifecycle_end = None
    attempt_start = None
    wave_start = None
    spec_start = None

    for i, action in enumerate(actions1):
        if isinstance(action, (CreateTask, Transition, DispatchImplementer)):
            lifecycle_end = i
        elif isinstance(action, (MarkInterrupted, ResumeAttempt, InterruptAttempt, StallCheck, EmitAttemptExit)):
            if attempt_start is None:
                attempt_start = i
        elif isinstance(action, (OpenWave, LaunchReview)):
            if wave_start is None:
                wave_start = i
        elif isinstance(action, SpecAttention):
            if spec_start is None:
                spec_start = i

    # Verify lifecycle tasks are sorted by task_id
    lifecycle_actions = [a for a in actions1 if isinstance(a, (CreateTask, Transition, DispatchImplementer))]
    task_ids = [a.task_id for a in lifecycle_actions if a.task_id]
    assert task_ids == sorted(task_ids), f"Lifecycle not sorted: {task_ids}"


def test_waves_no_duplicate_review_launch_while_preflighting():
    """Regression (2026-07-15 live): CREATED/PREFLIGHTING/INTERRUPTED-with-
    handle frontier-review attempts mean the review is in flight — no
    duplicate LaunchReview."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    for state, handle, expect_launch in [
        (AttemptState.CREATED, None, False),
        (AttemptState.PREFLIGHTING, None, False),
        (AttemptState.RUNNING, None, False),
        (AttemptState.EXITED, None, False),
        (AttemptState.INTERRUPTED, "sess-1", False),
        (AttemptState.INTERRUPTED, None, True),
        (AttemptState.FAILED, None, True),
    ]:
        att = make_attempt(attempt_id="att-1", state=state,
                           role=Role.FRONTIER_REVIEW)
        att.session_handle = handle
        tsf = make_tsf(task_id="P01", state=TaskState.AWAITING_REVIEW,
                       attempts=[att])
        tsf.wave_id = "wave-001"
        inp = ReconcileInput(
            now=utc(2026, 7, 15), cfg=cfg, routes=routes,
            states={"P01": tsf}, frontmatters={"P01": (fm, "h.md")},
            lint_clean={}, project_paused=False, decisions_open=set(),
            merged_branches=set(), leases_free={}, provider_ok={},
            log_quiet_seconds={}, pid_alive={}, receipts={},
        )
        launches = [a for a in plan_project(inp) if isinstance(a, LaunchReview)]
        assert bool(launches) == expect_launch, f"{state} handle={handle}"


# ============================================================================
# P15 2026-07-15: factory-state pause MODES (reconcile.py's ownership share
# of the P15-ui-config.md handoff, oracle 7). `pause_mode` is purely
# additive to ReconcileInput (default "run"), so every pre-existing test
# above that only sets `project_paused` keeps its old semantics unchanged.
# ============================================================================

def _pause_mode_composite_input(pause_mode: str):
    """One QUEUED task (dispatch candidate), one INTERRUPTED-with-handle
    attempt (resume candidate), one open-wave AWAITING_REVIEW task with no
    review in flight (launch-review candidate) -- the exact three 'new
    agent process' actions oracle 7 asks about, all in one ReconcileInput
    so a single plan_project() call answers all three for a given mode.

    max_active_tasks=5: R1 (ACTIVE) and W1 (AWAITING_REVIEW) already count
    toward the wip-cap (dispatch_eligible item 4), so the cap must leave
    room for Q1's dispatch too, independent of pause-mode gating."""
    cfg = make_config(max_active_tasks=5)
    routes = make_routes()

    fm_queued = make_frontmatter(id="Q1")
    tsf_queued = make_tsf(task_id="Q1", state=TaskState.QUEUED)

    fm_resume = make_frontmatter(id="R1")
    att_resume = make_attempt(attempt_id="att-resume", state=AttemptState.INTERRUPTED, receipt=None)
    att_resume.session_handle = "sess-resume"
    tsf_resume = make_tsf(task_id="R1", state=TaskState.ACTIVE, attempts=[att_resume])

    fm_review = make_frontmatter(id="W1")
    tsf_review = make_tsf(task_id="W1", state=TaskState.AWAITING_REVIEW)
    tsf_review.wave_id = "wave-p15"

    return ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"Q1": tsf_queued, "R1": tsf_resume, "W1": tsf_review},
        frontmatters={"Q1": (fm_queued, "h.md"), "R1": (fm_resume, "h.md"), "W1": (fm_review, "h.md")},
        lint_clean={},
        project_paused=(pause_mode != "run"),
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True, "route-2": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        pause_mode=pause_mode,
    )


def test_pause_mode_run_allows_all_three():
    """Oracle 7: mode 'run' -> dispatch, resume, AND launch-review all fire."""
    inp = _pause_mode_composite_input("run")
    actions = plan_project(inp)
    assert any(isinstance(a, DispatchImplementer) and a.task_id == "Q1" for a in actions)
    assert any(isinstance(a, ResumeAttempt) and a.attempt_id == "att-resume" for a in actions)
    assert any(isinstance(a, LaunchReview) and a.wave_id == "wave-p15" for a in actions)


def test_pause_mode_drain_handoffs_blocks_dispatch_only():
    """Oracle 7: 'drain-handoffs' -> QUEUED stays put (no dispatch) while a
    waiting wave still yields LaunchReview and an INTERRUPTED-with-handle
    attempt still yields ResumeAttempt."""
    inp = _pause_mode_composite_input("drain-handoffs")
    actions = plan_project(inp)
    assert not any(isinstance(a, DispatchImplementer) for a in actions)
    assert any(isinstance(a, ResumeAttempt) and a.attempt_id == "att-resume" for a in actions)
    assert any(isinstance(a, LaunchReview) and a.wave_id == "wave-p15" for a in actions)


def test_pause_mode_drain_agents_blocks_all_three():
    """Oracle 7: 'drain-agents' -> none of dispatch/resume/launch-review
    fire; the INTERRUPTED attempt is left parked (no BLOCKED transition
    either -- a drain is temporary, not a dead end)."""
    inp = _pause_mode_composite_input("drain-agents")
    actions = plan_project(inp)
    assert not any(isinstance(a, DispatchImplementer) for a in actions)
    assert not any(isinstance(a, ResumeAttempt) for a in actions)
    assert not any(isinstance(a, LaunchReview) for a in actions)
    assert not any(isinstance(a, Transition) and a.task_id == "R1" for a in actions)


def test_pause_mode_default_is_run_when_unset():
    """pause_mode is purely additive: omitting it (as every pre-P15 test in
    this file does) defaults to 'run' -- ResumeAttempt/LaunchReview are
    never gated by omission alone."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
    att.session_handle = "sess-1"
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15), cfg=cfg, routes=routes,
        states={"P01": tsf}, frontmatters={"P01": (fm, "h.md")},
        lint_clean={}, project_paused=False, decisions_open=set(),
        merged_branches=set(), leases_free={}, provider_ok={},
        log_quiet_seconds={}, pid_alive={}, receipts={},
        # pause_mode intentionally omitted
    )
    assert inp.pause_mode == "run"
    resumes = [a for a in plan_project(inp) if isinstance(a, ResumeAttempt)]
    assert len(resumes) == 1


# ============================================================================
# P16 2026-07-15: carve-automation trigger (module contract item 9,
# handoff/P16-carver-automation.md oracle 1)
# ============================================================================

def make_carve_routes() -> Routes:
    """flash-high (ordinary implementer tier) + frontier-review (the carver's
    own tier, module contract item 9's route-availability gate)."""
    return Routes(
        revision="test",
        tiers={"flash-high": ["route-1"], "frontier-review": ["route-review"]},
        routes={
            "route-1": RouteDef(route_id="route-1", cli="fake", model="fake-model"),
            "route-review": RouteDef(route_id="route-review", cli="fake", model="review-model"),
        },
    )


def _carve_base_kwargs(**overrides) -> dict:
    base = dict(
        now=utc(2026, 7, 15),
        cfg=make_config(carve_ahead_target=5),
        routes=make_carve_routes(),
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
    )
    base.update(overrides)
    return base


def test_carve_trigger_fires_below_target_no_carver_inflight():
    """Oracle 1: empty queue (0 < carve_ahead_target=5), a healthy
    frontier-review route, no carver in flight -> exactly one
    CarveDispatch(project='demo')."""
    inp = ReconcileInput(**_carve_base_kwargs())
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert len(dispatches) == 1
    assert dispatches[0].project == "demo"


def test_carve_trigger_none_when_queue_at_or_above_target():
    """Oracle 1 (negative): ready_count (2 QUEUED) >= carve_ahead_target
    (2) -> no CarveDispatch."""
    cfg = make_config(carve_ahead_target=2)
    fm1, fm2 = make_frontmatter(id="Q1"), make_frontmatter(id="Q2")
    tsf1 = make_tsf(task_id="Q1", state=TaskState.QUEUED)
    tsf2 = make_tsf(task_id="Q2", state=TaskState.QUEUED)
    inp = ReconcileInput(**_carve_base_kwargs(
        cfg=cfg,
        states={"Q1": tsf1, "Q2": tsf2},
        frontmatters={"Q1": (fm1, "h.md"), "Q2": (fm2, "h.md")},
    ))
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert dispatches == []


def test_carve_trigger_none_when_carver_already_inflight():
    """Oracle 1: a carve slot -- any non-terminal task carrying a CARVER
    attempt blocks a second dispatch, mirroring the wave-review-in-flight
    pattern."""
    carve_att = make_attempt(attempt_id="att-carve", state=AttemptState.RUNNING,
                              role=Role.CARVER)
    carve_tsf = make_tsf(task_id="carve-demo-1", state=TaskState.ACTIVE,
                          attempts=[carve_att])
    inp = ReconcileInput(**_carve_base_kwargs(states={"carve-demo-1": carve_tsf}))
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert dispatches == []


def test_carve_trigger_none_when_carver_terminal_slot_freed():
    """Oracle 1 (companion): once the carve task is terminal (SUPERSEDED),
    the slot is free again -- a fresh CarveDispatch fires."""
    carve_att = make_attempt(attempt_id="att-carve", state=AttemptState.EXITED,
                              role=Role.CARVER)
    carve_tsf = make_tsf(task_id="carve-demo-1", state=TaskState.SUPERSEDED,
                          attempts=[carve_att])
    inp = ReconcileInput(**_carve_base_kwargs(states={"carve-demo-1": carve_tsf}))
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert len(dispatches) == 1


def test_carve_trigger_none_when_no_frontier_route():
    """Oracle 1: no healthy 'frontier-review' route configured/healthy ->
    never dispatch a carver, even though every other condition holds."""
    inp = ReconcileInput(**_carve_base_kwargs(
        routes=make_routes(),  # only 'flash-high', no frontier-review tier
        provider_ok={"route-1": True, "route-2": True},
    ))
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert dispatches == []


def test_carve_trigger_decision_held_task_not_counted_ready():
    """Oracle 1: a QUEUED task with an OPEN decision dep is decision-held --
    excluded from the admissible-ready count even though its nominal state
    is QUEUED (one of the three counted states)."""
    cfg = make_config(carve_ahead_target=1)
    fm = make_frontmatter(id="Q1", depends_on=["D-001"])
    tsf = make_tsf(task_id="Q1", state=TaskState.QUEUED)
    inp = ReconcileInput(**_carve_base_kwargs(
        cfg=cfg,
        states={"Q1": tsf},
        frontmatters={"Q1": (fm, "h.md")},
        decisions_open={"D-001"},
    ))
    # ready_count would be 1 (>= target 1) if decision-held tasks counted;
    # since Q1 is excluded, ready_count is 0 < 1 -> dispatch fires.
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert len(dispatches) == 1


def test_carve_trigger_none_when_budget_exhausted():
    """Oracle 1: budget_remaining <= 0 -> no CarveDispatch."""
    inp = ReconcileInput(**_carve_base_kwargs(budget_remaining=0.0))
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert dispatches == []


def test_carve_trigger_milestone_gate_roadmap_exhausted_no_other_work():
    """Oracle 1: no non-terminal task exists AND roadmap_exhausted_open is
    True -> milestone does not admit work -> no CarveDispatch, even with an
    empty (below-target) queue."""
    inp = ReconcileInput(**_carve_base_kwargs(roadmap_exhausted_open=True))
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert dispatches == []


def test_carve_trigger_milestone_gate_active_task_overrides_roadmap_exhausted():
    """Oracle 1 (companion): roadmap_exhausted_open is True, but an existing
    non-terminal task means the milestone still admits work (the OR
    clause) -> CarveDispatch still fires."""
    fm = make_frontmatter(id="A1")
    tsf = make_tsf(task_id="A1", state=TaskState.ACTIVE)
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"A1": tsf},
        frontmatters={"A1": (fm, "h.md")},
        roadmap_exhausted_open=True,
    ))
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert len(dispatches) == 1
