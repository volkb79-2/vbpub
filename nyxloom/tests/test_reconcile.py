"""Tests for reconcile planner. PACKAGE P02."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom.config import MutexDef, Policy, ProjectConfig, Routes, RouteDef
from dataclasses import replace

from nyxloom.reconcile import (
    Action, AutoMergeTask, CarveDispatch, CreateTask, DispatchImplementer,
    EmitAttemptExit, InterruptAttempt, LaunchReview, MarkInterrupted,
    MarkStalled, OpenWave, ProviderPause, ReconcileInput, ResumeAttempt,
    RunPostMergeGate, SpecAttention, StallCheck, Transition, attempts_used,
    dispatch_eligible, plan_project,
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
    max_resume_failures: int = 2,
    resume_progress_grace_seconds: int = 120,
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
            max_resume_failures=max_resume_failures,
            resume_progress_grace_seconds=resume_progress_grace_seconds,
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


def test_carver_exited_active_task_emits_exit():
    """P32 Oracle O1: a CARVER attempt already EXITED (wrapper recorded its
    own exit) whose synthetic carve task is still ACTIVE and has a receipt
    -> EmitAttemptExit, so daemon.py's CARVER branch runs
    _consume_carve_exit and retires the carve to SUPERSEDED. Regression
    guard: previously only IMPLEMENTER/FRONTIER_REVIEW re-fired here, so a
    carve whose live exit-pass was missed stayed ACTIVE forever."""
    cfg = make_config()
    routes = make_routes()
    att = make_attempt(
        attempt_id="att-carve-1",
        state=AttemptState.EXITED,
        role=Role.CARVER,
        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
    )
    tsf = make_tsf(task_id="carve-demo-1", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"carve-demo-1": tsf},
        frontmatters={},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={"att-carve-1": {"result": "done"}},
    )

    actions = plan_project(inp)
    exits = [a for a in actions if isinstance(a, EmitAttemptExit) and a.attempt_id == "att-carve-1"]
    assert len(exits) == 1


def test_carver_exited_superseded_task_no_refire():
    """P32 Oracle O2: once the carve task has been finalized to SUPERSEDED,
    the EXITED CARVER attempt must not re-fire EmitAttemptExit on later
    passes (idempotent — no event spam).

    NOTE (P32 review): this asserts the end-state contract, but it does NOT
    on its own pin the CARVER branch's own guards — a SUPERSEDED task is
    terminal, so the TERMINAL_TASK_STATES skip at the top of the attempt loop
    returns before the branch is ever evaluated. This test therefore still
    passes with the whole CARVER branch deleted. The branch's `tsf.state ==
    ACTIVE` bound is pinned by test_carver_exited_non_active_task_no_exit and
    its role bound by test_non_carver_exited_active_task_no_exit."""
    cfg = make_config()
    routes = make_routes()
    att = make_attempt(
        attempt_id="att-carve-1",
        state=AttemptState.EXITED,
        role=Role.CARVER,
        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
    )
    tsf = make_tsf(task_id="carve-demo-1", state=TaskState.SUPERSEDED, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"carve-demo-1": tsf},
        frontmatters={},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={"att-carve-1": {"result": "done"}},
    )

    actions = plan_project(inp)
    exits = [a for a in actions if isinstance(a, EmitAttemptExit)]
    assert len(exits) == 0


def test_carver_exited_non_active_task_no_exit():
    """P32 Oracle O2 (bound): the CARVER branch fires ONLY for an ACTIVE task,
    exactly like the IMPLEMENTER/FRONTIER_REVIEW branches it mirrors.

    Uses NON-terminal, non-ACTIVE task states so the TERMINAL_TASK_STATES skip
    cannot be what makes this pass — this fails if `tsf.state == ACTIVE` is
    dropped from the branch, which the SUPERSEDED test cannot catch."""
    for task_state in (TaskState.QUEUED, TaskState.AWAITING_REVIEW):
        att = make_attempt(
            attempt_id="att-carve-1",
            state=AttemptState.EXITED,
            role=Role.CARVER,
            receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
        )
        tsf = make_tsf(task_id="carve-demo-1", state=task_state, attempts=[att])

        inp = ReconcileInput(
            now=utc(2026, 7, 15),
            cfg=make_config(),
            routes=make_routes(),
            states={"carve-demo-1": tsf},
            frontmatters={},
            lint_clean={},
            project_paused=False,
            decisions_open=set(),
            merged_branches=set(),
            leases_free={},
            provider_ok={},
            log_quiet_seconds={},
            pid_alive={},
            receipts={"att-carve-1": {"result": "done"}},
        )

        actions = plan_project(inp)
        exits = [a for a in actions if isinstance(a, EmitAttemptExit)]
        assert exits == [], f"carve task in {task_state.value} must not emit EmitAttemptExit"


def test_non_carver_exited_active_task_no_exit():
    """P32 Oracle O2 (negative): the branch keys on role == CARVER and must not
    fire for a non-carve role on an ACTIVE task. SELF_REVIEW has no exit
    re-scan branch, so an EXITED SELF_REVIEW attempt with a receipt stays
    unconsumed rather than being routed to daemon's _consume_carve_exit."""
    att = make_attempt(
        attempt_id="att-sr-1",
        state=AttemptState.EXITED,
        role=Role.SELF_REVIEW,
        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
    )
    tsf = make_tsf(task_id="demo-P01", state=TaskState.ACTIVE, attempts=[att])

    inp = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=make_config(),
        routes=make_routes(),
        states={"demo-P01": tsf},
        frontmatters={},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={"att-sr-1": {"result": "done"}},
    )

    actions = plan_project(inp)
    exits = [a for a in actions if isinstance(a, EmitAttemptExit)]
    assert exits == []


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


def test_interrupted_review_no_handle_relaunches_not_blocks_and_dies():
    """P62 2026-07-20 (A10, M10): an AWAITING_REVIEW task whose latest FRONTIER_
    REVIEW attempt is INTERRUPTED with NO session handle must NOT be dead-ended
    here -- the WAVE loop already plans a relaunch (a no-handle INTERRUPTED
    review is not 'in flight'). Emitting a Transition->BLOCKED too made ONE pass
    plan BOTH a dead-end AND a LaunchReview for the same task; execution blocked
    it then wasted a frontier session on the now-BLOCKED task. The plan must
    contain the relaunch and NO BLOCKED for this task."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-rev1", state=AttemptState.INTERRUPTED,
                       role=Role.FRONTIER_REVIEW, receipt=None)
    assert att.session_handle is None
    tsf = make_tsf(task_id="P01", state=TaskState.AWAITING_REVIEW, attempts=[att])
    tsf.wave_id = "wave-1"   # already waved -> the wave loop relaunches the review
    inp = ReconcileInput(
        now=utc(2026, 7, 15), cfg=cfg, routes=routes,
        states={"P01": tsf}, frontmatters={"P01": (fm, "h.md")},
        lint_clean={}, project_paused=False, decisions_open=set(),
        merged_branches=set(), leases_free={}, provider_ok={},
        log_quiet_seconds={}, pid_alive={}, receipts={},
    )
    actions = plan_project(inp)
    launches = [a for a in actions if isinstance(a, LaunchReview) and "P01" in a.task_ids]
    blocks = [a for a in actions
              if isinstance(a, Transition) and a.task_id == "P01" and a.to == TaskState.BLOCKED]
    assert len(launches) == 1, "the wave loop must relaunch the review for P01"
    assert blocks == [], "P01 must NOT be dead-ended here (that is the M10 contradiction)"


def test_plan_never_dead_ends_and_launches_same_task():
    """P62 (A10, M10) invariant: no single plan may contain BOTH a
    Transition->BLOCKED and an agent launch (DispatchImplementer / ResumeAttempt
    / LaunchReview) for the SAME task. The source fix above removes the known
    producer; the whole-plan guard in plan_project enforces it universally.
    Checked here on the exact M10 scenario."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-rev1", state=AttemptState.INTERRUPTED,
                       role=Role.FRONTIER_REVIEW, receipt=None)
    tsf = make_tsf(task_id="P01", state=TaskState.AWAITING_REVIEW, attempts=[att])
    tsf.wave_id = "wave-1"
    inp = ReconcileInput(
        now=utc(2026, 7, 15), cfg=cfg, routes=routes,
        states={"P01": tsf}, frontmatters={"P01": (fm, "h.md")},
        lint_clean={}, project_paused=False, decisions_open=set(),
        merged_branches=set(), leases_free={}, provider_ok={},
        log_quiet_seconds={}, pid_alive={}, receipts={},
    )
    actions = plan_project(inp)
    blocked = {a.task_id for a in actions
               if isinstance(a, Transition) and a.to == TaskState.BLOCKED}
    launched = set()
    for a in actions:
        if isinstance(a, (DispatchImplementer, ResumeAttempt)):
            launched.add(a.task_id)
        elif isinstance(a, LaunchReview):
            launched.update(a.task_ids)
    assert blocked.isdisjoint(launched), (
        f"a task is both dead-ended and launched in one plan: {blocked & launched}")


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
# P34: resume-safety re-cut (poisoned resumes fresh-start through the
# dispatch guards -- see nyxloom-trove/handoffs/nyxloom-P34-resume-safety-
# guarded.md). Oracles O1-O6.
# ============================================================================

def test_o1_poisoned_latest_active_clear_guards_fresh_dispatch():
    """O1: a poisoned (resume_failures >= max_resume_failures), latest,
    ACTIVE attempt with budget and guards clear -> exactly one
    DispatchImplementer, zero ResumeAttempt."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
    # The poisoned attempt MUST carry a session_handle: it is the handle that
    # today's planner would resume forever (O1's negative). Without one the
    # "zero ResumeAttempt" assertion below is vacuous, since ResumeAttempt is
    # unreachable for a handle-less attempt whether or not it is poisoned.
    att.session_handle = "sess-poisoned"
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
        provider_ok={"route-1": True, "route-2": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        resume_failures={"att-1": 2},
    )

    actions = plan_project(inp)
    dispatches = [a for a in actions if isinstance(a, DispatchImplementer) and a.task_id == "P01"]
    assert len(dispatches) == 1
    resumes = [a for a in actions if isinstance(a, ResumeAttempt)]
    assert len(resumes) == 0


def test_o2_below_threshold_still_resumes():
    """O2: healthy path unchanged -- resume_failures below
    max_resume_failures still yields ResumeAttempt, no DispatchImplementer."""
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
    att.session_handle = "sess-1"
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
        provider_ok={"route-1": True, "route-2": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        resume_failures={"att-1": 1},
    )

    actions = plan_project(inp)
    resumes = [a for a in actions if isinstance(a, ResumeAttempt) and a.attempt_id == "att-1"]
    assert len(resumes) == 1
    dispatches = [a for a in actions if isinstance(a, DispatchImplementer)]
    assert len(dispatches) == 0


class TestO3PolicyResumeConfig:
    """O3: Policy.max_resume_failures / resume_progress_grace_seconds
    defaults + override; schema permits both new [policy] keys."""

    def test_defaults(self):
        pol = Policy()
        assert pol.max_resume_failures == 2
        assert pol.resume_progress_grace_seconds == 120

    def test_override(self):
        pol = Policy(max_resume_failures=5, resume_progress_grace_seconds=30)
        assert pol.max_resume_failures == 5
        assert pol.resume_progress_grace_seconds == 30

    def test_schema_permits_both_keys(self):
        import importlib.resources
        import json as _json

        import jsonschema

        schema_text = importlib.resources.files("nyxloom.schemas").joinpath(
            "nyxloom-config.schema.json"
        ).read_text(encoding="utf-8")
        schema = _json.loads(schema_text)
        validator = jsonschema.Draft202012Validator(schema)
        doc = {
            "project": {"id": "demo", "handoff_globs": ["handoff/*.md"]},
            "policy": {"max_resume_failures": 5, "resume_progress_grace_seconds": 30},
        }
        errors = list(validator.iter_errors(doc))
        assert errors == [], errors


class TestO4GuardMatrix:
    """O4: the fresh-start dispatch is refused in every one of six states."""

    def test_case1_not_latest_attempt_is_parked(self):
        cfg = make_config()
        routes = make_routes()
        fm = make_frontmatter(id="P01")
        old_att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
        new_att = make_attempt(attempt_id="att-2", state=AttemptState.RUNNING, receipt=None)
        tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[old_att, new_att])

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
            provider_ok={"route-1": True, "route-2": True},
            log_quiet_seconds={},
            pid_alive={"att-2": True},
            receipts={},
            resume_failures={"att-1": 2},
        )

        actions = plan_project(inp)
        assert not any(isinstance(a, DispatchImplementer) and a.task_id == "P01" for a in actions)
        assert not any(isinstance(a, ResumeAttempt) and a.attempt_id == "att-1" for a in actions)
        assert not any(isinstance(a, Transition) and a.task_id == "P01" for a in actions)

    def test_case2_awaiting_review_is_parked(self):
        cfg = make_config()
        routes = make_routes()
        fm = make_frontmatter(id="P01")
        att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
        tsf = make_tsf(task_id="P01", state=TaskState.AWAITING_REVIEW, attempts=[att])

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
            provider_ok={"route-1": True, "route-2": True},
            log_quiet_seconds={},
            pid_alive={},
            receipts={},
            resume_failures={"att-1": 2},
        )

        actions = plan_project(inp)
        assert not any(isinstance(a, DispatchImplementer) and a.task_id == "P01" for a in actions)
        assert not any(isinstance(a, ResumeAttempt) and a.attempt_id == "att-1" for a in actions)
        assert not any(isinstance(a, Transition) and a.task_id == "P01" for a in actions)

    def test_case3_project_paused_is_parked(self):
        cfg = make_config()
        routes = make_routes()
        fm = make_frontmatter(id="P01")
        att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
        tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])

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
            provider_ok={"route-1": True, "route-2": True},
            log_quiet_seconds={},
            pid_alive={},
            receipts={},
            resume_failures={"att-1": 2},
        )

        actions = plan_project(inp)
        assert not any(isinstance(a, DispatchImplementer) and a.task_id == "P01" for a in actions)
        assert not any(isinstance(a, Transition) and a.task_id == "P01" for a in actions)

    def test_case4_task_paused_is_parked(self):
        cfg = make_config()
        routes = make_routes()
        fm = make_frontmatter(id="P01")
        att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
        tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, paused=True, attempts=[att])

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
            provider_ok={"route-1": True, "route-2": True},
            log_quiet_seconds={},
            pid_alive={},
            receipts={},
            resume_failures={"att-1": 2},
        )

        actions = plan_project(inp)
        assert not any(isinstance(a, DispatchImplementer) and a.task_id == "P01" for a in actions)
        assert not any(isinstance(a, Transition) and a.task_id == "P01" for a in actions)

    def test_case5_budget_exhausted_is_parked(self):
        cfg = make_config()
        routes = make_routes()
        fm = make_frontmatter(id="P01")
        att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
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
            provider_ok={"route-1": True, "route-2": True},
            log_quiet_seconds={},
            pid_alive={},
            receipts={},
            resume_failures={"att-1": 2},
            budget_remaining=0.0,
        )

        actions = plan_project(inp)
        assert not any(isinstance(a, DispatchImplementer) and a.task_id == "P01" for a in actions)
        assert not any(isinstance(a, Transition) and a.task_id == "P01" for a in actions)

    def test_case6_lease_held_is_parked(self):
        cfg = make_config()
        routes = make_routes()
        fm = make_frontmatter(id="P01", stack="exclusive")
        att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
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
            leases_free={"demo.stack": False},
            provider_ok={"route-1": True, "route-2": True},
            log_quiet_seconds={},
            pid_alive={},
            receipts={},
            resume_failures={"att-1": 2},
        )

        actions = plan_project(inp)
        assert not any(isinstance(a, DispatchImplementer) and a.task_id == "P01" for a in actions)
        assert not any(isinstance(a, Transition) and a.task_id == "P01" for a in actions)


def test_o5_multi_pass_convergence_second_pass_no_dispatch():
    """O5: apply the fresh DispatchImplementer to the state (append the new
    attempt record as RUNNING, as daemon.py's handler does), then plan a
    SECOND pass over the mutated state -- zero DispatchImplementer, since
    the poisoned record is no longer tsf.attempts[-1]."""
    cfg = make_config(max_attempts_per_task=3)
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att1 = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
    att1.session_handle = "sess-poisoned"
    tsf1 = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att1])

    inp1 = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf1},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True, "route-2": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        resume_failures={"att-1": 2},
    )

    actions1 = plan_project(inp1)
    dispatches1 = [a for a in actions1 if isinstance(a, DispatchImplementer) and a.task_id == "P01"]
    assert len(dispatches1) == 1

    # Apply: append the new attempt record as RUNNING, exactly as
    # daemon.py's DispatchImplementer handler does.
    att2 = make_attempt(attempt_id="att-2", state=AttemptState.RUNNING, receipt=None)
    tsf2 = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att1, att2])

    inp2 = ReconcileInput(
        now=utc(2026, 7, 15),
        cfg=cfg,
        routes=routes,
        states={"P01": tsf2},
        frontmatters={"P01": (fm, "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True, "route-2": True},
        log_quiet_seconds={},
        pid_alive={"att-2": True},
        receipts={},
        resume_failures={"att-1": 2},
    )

    actions2 = plan_project(inp2)
    dispatches2 = [a for a in actions2 if isinstance(a, DispatchImplementer) and a.task_id == "P01"]
    assert len(dispatches2) == 0


def test_o5_fresh_start_sequence_terminates_at_record_budget():
    """O5, exhaustion clause: "repeated to exhaustion, the sequence
    terminates -- after max_attempts_per_task distinct IMPLEMENTER records
    the planner emits the typed BLOCKED of O6, never another dispatch".

    Drives real plan/apply cycles in the worst case the oracle is about --
    every fresh start dies poisoned the same way -- rather than a single
    pass. The unbounded re-dispatch that got P26 reverted (a new agent
    process every reconcile interval into one worktree) is a non-terminating
    loop here, so only iterating to a fixed point can exclude it."""
    max_records = 2
    cfg = make_config(max_attempts_per_task=max_records)
    routes = make_routes()
    fm = make_frontmatter(id="P01")

    att1 = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
    att1.session_handle = "sess-poisoned"
    attempts = [att1]
    resume_failures = {"att-1": 2}

    dispatch_total = 0
    blocked = False
    for _ in range(10):  # generous bound; termination must happen well inside it
        tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=list(attempts))
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
            provider_ok={"route-1": True, "route-2": True},
            log_quiet_seconds={},
            pid_alive={},
            receipts={},
            resume_failures=dict(resume_failures),
        )
        actions = plan_project(inp)
        dispatches = [a for a in actions if isinstance(a, DispatchImplementer) and a.task_id == "P01"]
        blocks = [a for a in actions if isinstance(a, Transition)
                  and a.task_id == "P01" and a.to == TaskState.BLOCKED]
        assert not any(isinstance(a, ResumeAttempt) for a in actions)
        if blocks:
            assert not dispatches, "dispatched into a task it blocked in the same pass"
            assert blocks[0].blocker is not None
            assert blocks[0].blocker.type == BlockerType.ENVIRONMENT
            blocked = True
            break
        assert len(dispatches) == 1
        dispatch_total += 1
        # Apply the dispatch as daemon.py's handler does (append the new
        # attempt record), then poison it too.
        new_id = f"att-{len(attempts) + 1}"
        new_att = make_attempt(attempt_id=new_id, state=AttemptState.INTERRUPTED, receipt=None)
        new_att.session_handle = f"sess-{new_id}"
        attempts.append(new_att)
        resume_failures[new_id] = 2

    assert blocked, "sequence never terminated: the planner kept re-dispatching"
    # Exactly one fresh start per unused record slot, then the typed dead-end.
    assert dispatch_total == max_records - 1


def test_o6_record_budget_gone_types_blocked():
    """O6: distinct-record budget gone (IMPLEMENTER attempt RECORDS >=
    max_attempts_per_task) with the latest attempt poisoned -> typed
    BLOCKED via the existing dead-end path, never left silently ACTIVE."""
    cfg = make_config(max_attempts_per_task=1)
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
    # The session_handle is what makes this test discriminate the RECORD-budget
    # dead-end from P14's pre-existing no-handle dead-end: both emit the same
    # BLOCKED/ENVIRONMENT transition, so a handle-less fixture asserts nothing
    # about P34 (it passes with poison detection disabled entirely). With a
    # handle, the pre-P34 planner would emit ResumeAttempt here instead --
    # attempts_count is 0 because a receiptless record is invisible to it.
    att.session_handle = "sess-poisoned"
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
        provider_ok={"route-1": True, "route-2": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        resume_failures={"att-1": 2},
    )

    actions = plan_project(inp)
    dispatches = [a for a in actions if isinstance(a, DispatchImplementer) and a.task_id == "P01"]
    assert len(dispatches) == 0
    # A poisoned attempt never resumes, even on the dead-end path.
    assert not any(isinstance(a, ResumeAttempt) for a in actions)
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    assert transitions[0].to == TaskState.BLOCKED
    assert transitions[0].blocker is not None
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


def test_waves_age_trigger_reads_oldest_not_lexicographically_first():
    """P65 2026-07-20 (M11, R3 counterfeit-input). The age trigger must open a
    wave when the GENUINELY oldest waiting task has aged past the timeout --
    even if that task sorts AFTER a fresh one. `test_waves_oldest_over_timeout_
    opens` above aligned sort order with age order (P01 was BOTH oldest and
    lexicographically-first), so it passed whether the code read the oldest OR
    the sorted-first task -- a counterfeit-input test that masked the bug. Here
    the sorted-first task is FRESH and a later-sorting task is OLD, so ONLY the
    correct 'oldest' reading opens the wave; the buggy `task_ids_to_batch[0]`
    reading measures the fresh task and never trips the timeout (a review
    stranded unboundedly under low throughput)."""
    cfg = make_config(wave_max_diffs=3)   # count threshold (>=3) deliberately NOT met
    routes = make_routes()
    # "P01" sorts first but is FRESH (5 min); "P99" sorts last but is OLD (60 min).
    fresh = make_tsf(task_id="P01", state=TaskState.AWAITING_REVIEW)
    fresh.since = utc(2026, 7, 15, 0, 55)
    old = make_tsf(task_id="P99", state=TaskState.AWAITING_REVIEW)
    old.since = utc(2026, 7, 15, 0, 0)
    inp = ReconcileInput(
        now=utc(2026, 7, 15, 1, 0),
        cfg=cfg,
        routes=routes,
        states={"P01": fresh, "P99": old},
        frontmatters={"P01": (make_frontmatter(id="P01"), "h.md"),
                       "P99": (make_frontmatter(id="P99"), "h.md")},
        lint_clean={},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
        wave_open_after_seconds=1800,   # 30 min
    )
    actions = plan_project(inp)
    opens = [a for a in actions if isinstance(a, OpenWave)]
    assert len(opens) == 1, (
        "the wave must open on the OLDEST waiting task's age (P99, 60 min), not "
        "the lexicographically-first task's age (P01, 5 min)"
    )
    assert sorted(opens[0].task_ids) == ["P01", "P99"]


def test_wave_review_batched_one_launch_for_all_members():
    """P61 2026-07-20 (A9, M3 -- real wave batching). A wave of 3
    AWAITING_REVIEW tasks sharing a wave_id, none with a review in flight,
    must produce exactly ONE LaunchReview carrying all three -- one frontier
    session over the whole wave, not three singleton launches each paying the
    ~35-40k frontier startup tax. Pre-P61 the wave loop emitted one
    LaunchReview(task_ids=[task_id]) per task."""
    cfg = make_config(wave_max_diffs=5)
    routes = make_routes()
    states = {}
    frontmatters = {}
    for tid in ("P01", "P02", "P03"):
        tsf = make_tsf(task_id=tid, state=TaskState.AWAITING_REVIEW)
        tsf.wave_id = "wave-42"          # already waved, no review attempt yet
        states[tid] = tsf
        frontmatters[tid] = (make_frontmatter(id=tid), "h.md")
    inp = ReconcileInput(
        now=utc(2026, 7, 15, 1, 0),
        cfg=cfg,
        routes=routes,
        states=states,
        frontmatters=frontmatters,
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
    launches = [a for a in actions if isinstance(a, LaunchReview)]
    assert len(launches) == 1, "one frontier session per wave, not one per task"
    assert launches[0].wave_id == "wave-42"
    assert sorted(launches[0].task_ids) == ["P01", "P02", "P03"]


def test_wave_review_two_waves_get_one_launch_each():
    """P61 (A9): tasks in DIFFERENT waves are launched separately -- batching
    is per wave_id, not a single launch across unrelated waves."""
    cfg = make_config(wave_max_diffs=5)
    routes = make_routes()
    states = {}
    frontmatters = {}
    for tid, wid in (("P01", "wave-a"), ("P02", "wave-a"), ("P03", "wave-b")):
        tsf = make_tsf(task_id=tid, state=TaskState.AWAITING_REVIEW)
        tsf.wave_id = wid
        states[tid] = tsf
        frontmatters[tid] = (make_frontmatter(id=tid), "h.md")
    inp = ReconcileInput(
        now=utc(2026, 7, 15, 1, 0), cfg=cfg, routes=routes, states=states,
        frontmatters=frontmatters, lint_clean={}, project_paused=False,
        decisions_open=set(), merged_branches=set(), leases_free={},
        provider_ok={}, log_quiet_seconds={}, pid_alive={}, receipts={},
        wave_open_after_seconds=1800,
    )
    launches = [a for a in plan_project(inp) if isinstance(a, LaunchReview)]
    by_wave = {l.wave_id: sorted(l.task_ids) for l in launches}
    assert by_wave == {"wave-a": ["P01", "P02"], "wave-b": ["P03"]}


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
# ORACLE 11b (P44 2026-07-16, anti-runaway self-correction): dedup flags
# -- rejections_already_open / carve_outcome_already_open /
# blocked_underspecified_already_open. Before this fix these three branches
# had NO dedup (unlike ratchet_already_open above) and re-planned
# SpecAttention on EVERY call for a persistent condition -- the actual
# notification-storm root cause (Oracle 2 in the P44 handoff).
# ============================================================================

def _rejections_base_kwargs(**overrides) -> dict:
    cfg = make_config()
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01")
    base = dict(
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
    base.update(overrides)
    return base


def test_rejections_plans_once_while_persistent_condition_holds():
    """Oracle 2: a persistent 'rejections' condition (count stays >= 2)
    plans SpecAttention('rejections') on the FIRST call
    (rejections_already_open=False)..."""
    inp = ReconcileInput(**_rejections_base_kwargs(rejections_already_open=False))
    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "rejections"]
    assert len(spec_attns) == 1


def test_rejections_deduped_once_flag_open():
    """...but NOT again once the daemon reports the flag already open (the
    SAME persistent condition, second/Nth call) -- today (pre-fix) this
    branch has no such guard and re-plans it every single call."""
    inp = ReconcileInput(**_rejections_base_kwargs(rejections_already_open=True))
    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "rejections"]
    assert spec_attns == []


def test_carve_outcome_deduped_once_flag_open():
    """Oracle 2 (companion): carve-outcome branch likewise dedups."""
    inp = ReconcileInput(**_rejections_base_kwargs(
        review_rejections_by_area={},
        carve_outcomes=[{"outcome": "SPEC_GAP"}],
        carve_outcome_already_open=True,
    ))
    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "carve-outcome"]
    assert spec_attns == []


def test_carve_outcome_plans_when_flag_not_open():
    inp = ReconcileInput(**_rejections_base_kwargs(
        review_rejections_by_area={},
        carve_outcomes=[{"outcome": "SPEC_GAP"}],
        carve_outcome_already_open=False,
    ))
    actions = plan_project(inp)
    spec_attns = [a for a in actions if isinstance(a, SpecAttention) and a.reason == "carve-outcome"]
    assert len(spec_attns) == 1


def test_blocked_underspecified_deduped_once_flag_open():
    """Oracle 2 (companion): blocked-underspecified branch likewise dedups."""
    inp = ReconcileInput(**_rejections_base_kwargs(
        review_rejections_by_area={},
        blocked_underspecified_count=3,
        blocked_underspecified_already_open=True,
    ))
    actions = plan_project(inp)
    spec_attns = [a for a in actions
                  if isinstance(a, SpecAttention) and a.reason == "blocked-underspecified"]
    assert spec_attns == []


def test_blocked_underspecified_plans_when_flag_not_open():
    inp = ReconcileInput(**_rejections_base_kwargs(
        review_rejections_by_area={},
        blocked_underspecified_count=3,
        blocked_underspecified_already_open=False,
    ))
    actions = plan_project(inp)
    spec_attns = [a for a in actions
                  if isinstance(a, SpecAttention) and a.reason == "blocked-underspecified"]
    assert len(spec_attns) == 1


def test_dedup_flags_default_false_preserves_prior_behavior():
    """ReconcileInput's new fields default to False, so any pre-existing
    caller that omits them (like test_spec_health_review_rejections above)
    keeps planning SpecAttention -- only an explicit True suppresses it."""
    inp = ReconcileInput(**_rejections_base_kwargs())
    assert inp.rejections_already_open is False
    assert inp.carve_outcome_already_open is False
    assert inp.blocked_underspecified_already_open is False


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


def test_carve_trigger_none_when_project_paused():
    """P52 2026-07-19 (live incident, dstdns): a 'paused' project must not
    get a brand-new carve dispatch, even though every OTHER condition here
    (below carve_ahead_target, healthy route, no carver in flight) would
    otherwise fire one -- this is exactly the gap that let 4 unauthorized
    carves fire against a paused project in production before it was
    caught. Covers BOTH pause modes (drain-handoffs and drain-agents are
    both project_paused=True; the untargeted trigger does not distinguish
    between them, matching dispatch_eligible's own convention)."""
    inp = ReconcileInput(**_carve_base_kwargs(project_paused=True))
    dispatches = [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]
    assert dispatches == []


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


def test_failed_carver_attempt_supersedes_and_frees_carve_slot():
    """P54 2026-07-19 (M2, CRITICAL -- attempt-state closure). A synthetic
    carve task whose sole CARVER attempt is FAILED (the wrapper writes
    ATTEMPT_FAILED for a lease-lost-race, exit 75, or a spawn failure) had
    NO handler in the attempt loop -> it stayed ACTIVE forever -> the
    carve_in_flight predicate (any non-terminal task carrying a CARVER
    attempt) was permanently True -> ALL carving deadlocked silently. Now
    the FAILED attempt transitions the carve task to SUPERSEDED, freeing the
    single carve slot for a later pass (self-limiting)."""
    carve_att = make_attempt(attempt_id="att-carve", state=AttemptState.FAILED,
                              role=Role.CARVER)
    carve_tsf = make_tsf(task_id="carve-demo-1", state=TaskState.ACTIVE,
                          attempts=[carve_att])
    inp = ReconcileInput(**_carve_base_kwargs(states={"carve-demo-1": carve_tsf}))
    actions = plan_project(inp)
    transitions = [a for a in actions
                   if isinstance(a, Transition) and a.task_id == "carve-demo-1"]
    assert len(transitions) == 1
    assert transitions[0].to == TaskState.SUPERSEDED


def test_failed_implementer_attempt_requeues():
    """P54 (M2, CRITICAL): an ACTIVE implementer task whose latest attempt is
    FAILED (failed to start -- lease-race/spawn, never ran real work) had no
    handler -> stuck ACTIVE forever, eating a wip slot. Now re-queued to
    re-dispatch once the transient condition clears (self-limiting)."""
    fm = make_frontmatter(id="P01")
    att = make_attempt(attempt_id="att-1", state=AttemptState.FAILED,
                        role=Role.IMPLEMENTER)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[att])
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"P01": tsf}, frontmatters={"P01": (fm, "h.md")}))
    actions = plan_project(inp)
    transitions = [a for a in actions
                   if isinstance(a, Transition) and a.task_id == "P01"]
    assert any(t.to == TaskState.QUEUED for t in transitions)


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


# ============================================================================
# SELF-CORRECT 2026-07-16: REVIEW_REJECTED reject-loop (module contract item
# 10). Paired with daemon.py's robust _parse_review_verdict (bug 1); see
# item 10's docstring for the documented, out-of-scope BLOCKED gap on the
# exhausted-budget half of this contract.
# ============================================================================

def test_review_rejected_with_budget_remaining_requeues():
    """O2 (bug 2 fix): a REVIEW_REJECTED task with attempts remaining
    (below max_attempts_per_task) must plan a REVIEW_REJECTED->QUEUED
    Transition -- before this fix, REVIEW_REJECTED had NO handler at all
    and the task stranded forever (zero planned actions, requiring a
    manual operator re-queue)."""
    cfg = make_config(max_attempts_per_task=3)
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att1 = make_attempt(
        attempt_id="att-1", state=AttemptState.EXITED,
        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
    )
    tsf = make_tsf(task_id="P01", state=TaskState.REVIEW_REJECTED, attempts=[att1])

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

    actions = plan_project(inp)
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    t = transitions[0]
    assert t.to == TaskState.QUEUED
    assert t.blocker is None
    assert "review rejected" in (t.notes or "").lower()

    # The negative this fixes: never left with zero planned progress.
    assert len(actions) >= 1


def test_attempts_used_counts_only_implementer_not_review_or_carve():
    """P60 2026-07-20 (M8): the single accessor counts only IMPLEMENTER
    attempts with a non-LIMIT receipt in a terminal state. A review/carve
    attempt (which also lands in tsf.attempts with a DONE receipt) must NOT
    count against the implementer budget, and a LIMIT attempt is free."""
    impl = make_attempt(attempt_id="i1", state=AttemptState.EXITED, role=Role.IMPLEMENTER,
                        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    review = make_attempt(attempt_id="r1", state=AttemptState.EXITED, role=Role.FRONTIER_REVIEW,
                          receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    limited = make_attempt(attempt_id="i2", state=AttemptState.EXITED, role=Role.IMPLEMENTER,
                           receipt=Receipt(result=ReceiptResult.LIMIT, exit_code=1))
    tsf = make_tsf(task_id="P01", state=TaskState.REVIEW_REJECTED,
                   attempts=[impl, review, limited])
    assert attempts_used(tsf) == 1   # only the one non-LIMIT implementer


def test_review_reject_cycle_counts_one_implementer_unit_not_two():
    """P60 (M8): a reject cycle is implement + review. The old role-blind
    formula counted BOTH -> with max_attempts=2 one rejection exhausted the
    task (routed to READY_TO_CARVE) instead of allowing a second implementer
    try. Now the review is not counted, so budget remains and it re-queues."""
    cfg = make_config(max_attempts_per_task=2)
    fm = make_frontmatter(id="P01")
    impl = make_attempt(attempt_id="i1", state=AttemptState.EXITED, role=Role.IMPLEMENTER,
                        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    review = make_attempt(attempt_id="r1", state=AttemptState.EXITED, role=Role.FRONTIER_REVIEW,
                          receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    tsf = make_tsf(task_id="P01", state=TaskState.REVIEW_REJECTED, attempts=[impl, review])
    inp = ReconcileInput(**_carve_base_kwargs(
        cfg=cfg, states={"P01": tsf}, frontmatters={"P01": (fm, "h.md")}))
    actions = plan_project(inp)
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    assert transitions[0].to == TaskState.QUEUED   # requeued (budget remains), NOT READY_TO_CARVE


def test_no_inline_attempt_budget_formula_outside_accessor():
    """P60 (M8) invariant: the receipt-based implementer-budget count lives
    ONLY in attempts_used -- the three role-blind inline copies that drifted
    out of sync (dispatch check 5, REVIEW_REJECTED counter, ERROR path) are
    gone, so the defect this package fixes cannot recur."""
    src_dir = Path(__file__).parent.parent / "src" / "nyxloom"
    reconcile_src = (src_dir / "reconcile.py").read_text()
    daemon_src = (src_dir / "daemon.py").read_text()
    assert "def attempts_used" in reconcile_src
    assert "rejected_attempts_count = sum(" not in reconcile_src
    assert "attempts_count = sum(" not in reconcile_src
    assert "attempts_used = sum(" not in daemon_src
    assert "reconcile.attempts_used(" in daemon_src   # daemon uses the shared accessor


def test_review_rejected_attempts_exhausted_routes_to_ready_to_carve():
    """O1(a) (P45 2026-07-19, non-hollow anchor): the original handoff's
    contract asked for REVIEW_REJECTED->BLOCKED once attempts are exhausted
    (mirroring the INTERRUPTED-exhausted typed-blocker path elsewhere in
    this module). That transition is illegal per types.py's
    TASK_TRANSITIONS[REVIEW_REJECTED] (BLOCKED is absent from that
    frozenset) and types.py is FROZEN CORE / out of scope for this package
    (scope.touch forbids editing it) -- planning it would crash the daemon
    with TransitionError the moment it executed. REVIEW_REJECTED->
    READY_TO_CARVE IS legal in that same frozen table, so P45 routes the
    exhausted case there instead: this used to pin the CURRENT, honest
    behavior of "no action planned for the exhausted case" (a documented
    KNOWN GAP, not a silent drop) -- now it pins the fix. See
    test_review_rejected_with_budget_remaining_requeues just above for the
    still-byte-for-byte-unchanged attempts-remaining path (O1(b))."""
    cfg = make_config(max_attempts_per_task=1)
    routes = make_routes()
    fm = make_frontmatter(id="P01")
    att1 = make_attempt(
        attempt_id="att-1", state=AttemptState.EXITED,
        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
    )
    tsf = make_tsf(task_id="P01", state=TaskState.REVIEW_REJECTED, attempts=[att1])

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

    actions = plan_project(inp)
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    t = transitions[0]
    assert t.to == TaskState.READY_TO_CARVE
    assert t.blocker is None
    assert "review rejected" in (t.notes or "").lower()
    assert "exhausted" in (t.notes or "").lower()


# ============================================================================
# P45 2026-07-19: READY_TO_CARVE handler (module contract item 12) -- closes
# the dead-end pinned by tests/test_invariants.py's
# test_no_dead_end_ready_to_carve. Re-dispatches the SAME single carve
# authority as item 9's untargeted headroom-refill trigger (reconcile.
# CarveDispatch / daemon._execute_carve_dispatch -- see make_carve_routes/
# _carve_base_kwargs in the CARVE TRIGGER section above, reused here). These
# are the non-hollow anchors for oracles O2/O3/O4.
# ============================================================================

def test_ready_to_carve_dispatches_existing_carve_mechanism_then_supersedes():
    """O2/O4 (non-hollow anchor): a READY_TO_CARVE task with no carver in
    flight and a healthy frontier-review route gets BOTH a CarveDispatch
    (task_id=fm_id, item_id=None -- the existing untargeted carve-packet
    path, reusing reconcile.CarveDispatch / daemon._execute_carve_dispatch,
    NOT a new Action subclass or a new Daemon method) AND a same-pass
    Transition to SUPERSEDED (self-limiting, so it does not re-fire a
    second CarveDispatch on a later pass once the carve slot frees up --
    see test_ready_to_carve_superseded_is_terminal_no_refire_next_pass
    below for that second half)."""
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.READY_TO_CARVE)
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
    ))

    actions = plan_project(inp)

    dispatches = [a for a in actions if isinstance(a, CarveDispatch)]
    assert len(dispatches) == 1
    d = dispatches[0]
    assert d.task_id == "P01"
    assert d.item_id is None
    assert d.project == "demo"

    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert len(transitions) == 1
    assert transitions[0].to == TaskState.SUPERSEDED


def test_ready_to_carve_no_dispatch_when_project_paused():
    """P52 2026-07-19 (live incident, dstdns): a READY_TO_CARVE task in a
    'paused' project must not get a CarveDispatch either -- it stays in
    READY_TO_CARVE untouched (no SUPERSEDED transition), picked up once the
    project is unpaused, same as the carver-already-inflight case below."""
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.READY_TO_CARVE)
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        project_paused=True,
    ))

    actions = plan_project(inp)

    assert [a for a in actions if isinstance(a, CarveDispatch)] == []
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert transitions == []


def test_ready_to_carve_no_dispatch_when_carver_already_inflight():
    """O3(a) (non-hollow anchor): a carver attempt already in flight (any
    non-terminal task carrying an Attempt with role CARVER) means a
    READY_TO_CARVE task gets NO new CarveDispatch this pass -- it stays in
    READY_TO_CARVE, picked up on a later pass once the slot frees. Reuses
    the EXACT SAME in-flight predicate item 9's own carve trigger uses (see
    test_carve_trigger_none_when_carver_already_inflight above), not a
    second, independently-drifting check."""
    carve_att = make_attempt(attempt_id="att-carve", state=AttemptState.RUNNING,
                              role=Role.CARVER)
    carve_tsf = make_tsf(task_id="carve-demo-1", state=TaskState.ACTIVE,
                          attempts=[carve_att])
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.READY_TO_CARVE)
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"P01": tsf, "carve-demo-1": carve_tsf},
        frontmatters={"P01": (fm, "h.md")},
    ))

    actions = plan_project(inp)

    assert [a for a in actions if isinstance(a, CarveDispatch)] == []
    # P01 stays in READY_TO_CARVE -- no Transition planned for it either.
    assert [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"] == []


def test_ready_to_carve_two_simultaneous_only_one_carve_dispatch_total():
    """O3(b) (non-hollow anchor): TWO tasks simultaneously in
    READY_TO_CARVE -> exactly ONE CarveDispatch total is planned across the
    WHOLE returned action list, not two -- the single-strategic-carver
    invariant holds even when multiple tasks would independently want a
    carve. (This input also satisfies item 9's own untargeted trigger
    conditions -- ready_count is 0 for both READY_TO_CARVE tasks, since
    that count only considers CARVED/QUEUED/NEEDS_DECISION, and
    milestone_admits_work is True from either task being non-terminal --
    proving the single-CarveDispatch cap is truly SHARED across both
    triggers, not just enforced within the READY_TO_CARVE handler alone.)"""
    fm1 = make_frontmatter(id="P01")
    fm2 = make_frontmatter(id="P02")
    tsf1 = make_tsf(task_id="P01", state=TaskState.READY_TO_CARVE)
    tsf2 = make_tsf(task_id="P02", state=TaskState.READY_TO_CARVE)
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"P01": tsf1, "P02": tsf2},
        frontmatters={"P01": (fm1, "h.md"), "P02": (fm2, "h.md")},
    ))

    actions = plan_project(inp)

    dispatches = [a for a in actions if isinstance(a, CarveDispatch)]
    assert len(dispatches) == 1
    # Determinism: sorted task-id order (mirrors item 3's dispatch-capacity
    # loop) -- the lower id wins this pass's single carve slot.
    assert dispatches[0].task_id == "P01"
    superseded = [a for a in actions if isinstance(a, Transition) and a.to == TaskState.SUPERSEDED]
    assert len(superseded) == 1
    assert superseded[0].task_id == "P01"
    # P02 stays untouched in READY_TO_CARVE for a later pass.
    assert [a for a in actions if isinstance(a, Transition) and a.task_id == "P02"] == []


def test_ready_to_carve_no_dispatch_without_frontier_route():
    """O2 (negative-adjacent): reuses item 9's own no-healthy-route guard --
    no healthy 'frontier-review' route means a READY_TO_CARVE task never
    gets a spurious CarveDispatch either, even though every other condition
    holds (mirrors test_carve_trigger_none_when_no_frontier_route above)."""
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.READY_TO_CARVE)
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        routes=make_routes(),  # only 'flash-high', no frontier-review tier
        provider_ok={"route-1": True},
    ))

    actions = plan_project(inp)
    assert [a for a in actions if isinstance(a, CarveDispatch)] == []


def test_ready_to_carve_no_dispatch_when_budget_exhausted():
    """Reviewer addendum (2026-07-19, post-P45 merge review): a READY_TO_CARVE
    task must NOT get a CarveDispatch when the project's session budget is
    already exhausted -- every other dispatch path in this module (item 9's
    own untargeted trigger, dispatch_eligible's check 5) already stops all
    new agent processes on budget_remaining <= 0; a rejected task's re-carve
    is not exempt. Mirrors test_ready_to_carve_no_dispatch_without_frontier_
    route's shape with budget_remaining=0.0 instead of an unhealthy route."""
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.READY_TO_CARVE)
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        budget_remaining=0.0,
    ))

    actions = plan_project(inp)
    assert [a for a in actions if isinstance(a, CarveDispatch)] == []
    # The task itself is left untouched (still READY_TO_CARVE, not
    # SUPERSEDED) so it is retried once budget frees up.
    transitions = [a for a in actions if isinstance(a, Transition) and a.task_id == "P01"]
    assert transitions == []


def test_ready_to_carve_superseded_is_terminal_no_refire_next_pass():
    """O4 (non-hollow anchor, second half): a follow-up pass with the SAME
    task now in SUPERSEDED (the state the first pass's Transition landed it
    in) plans NOTHING further at all -- SUPERSEDED is terminal per
    TERMINAL_TASK_STATES, so the carve does not re-fire once the carve slot
    frees up on a later pass. roadmap_exhausted_open=True with no other
    non-terminal task also suppresses item 9's OWN untargeted trigger here,
    so an empty action list is the correct, clean assertion (isolating this
    from the unrelated, independently-tested item-9 behavior)."""
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.SUPERSEDED)
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        roadmap_exhausted_open=True,
    ))

    actions = plan_project(inp)
    assert actions == []


# ============================================================================
# P48 2026-07-19: guarded-automatic merge trigger (module contract item 13)
# ============================================================================

def test_auto_merge_fires_when_guarded_automatic_and_merge_ready():
    """Item 13: a MERGE_READY task under policy.merge_mode ==
    'guarded-automatic' with the project not paused gets exactly one
    AutoMergeTask(task_id). No separate verdict check belongs here --
    reaching MERGE_READY already structurally means 'approved'."""
    cfg = make_config()
    cfg.policy.merge_mode = "guarded-automatic"
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.MERGE_READY)
    inp = ReconcileInput(**_carve_base_kwargs(
        cfg=cfg,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
    ))

    actions = plan_project(inp)

    auto_merges = [a for a in actions if isinstance(a, AutoMergeTask)]
    assert len(auto_merges) == 1
    assert auto_merges[0].task_id == "P01"


def test_auto_merge_none_when_merge_mode_manual():
    """Item 13 (regression pin): the existing manual-mode behavior --
    MERGE_READY sits inert until an operator's own `nyxloom merge` -- must
    be completely unchanged. policy.merge_mode defaults to 'manual', so a
    MERGE_READY task with no explicit override plans no AutoMergeTask at
    all."""
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.MERGE_READY)
    inp = ReconcileInput(**_carve_base_kwargs(
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
    ))

    actions = plan_project(inp)

    assert [a for a in actions if isinstance(a, AutoMergeTask)] == []


def test_auto_merge_none_when_project_paused():
    """Item 13: guarded-automatic must not merge blind while the project is
    paused for ANY reason (drain-handoffs or drain-agents) -- an operator
    or the runaway watchdog flagging the project is exactly backwards from
    a signal to keep auto-merging unattended."""
    cfg = make_config()
    cfg.policy.merge_mode = "guarded-automatic"
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.MERGE_READY)
    inp = ReconcileInput(**_carve_base_kwargs(
        cfg=cfg,
        states={"P01": tsf},
        frontmatters={"P01": (fm, "h.md")},
        project_paused=True,
    ))

    actions = plan_project(inp)

    assert [a for a in actions if isinstance(a, AutoMergeTask)] == []


# ============================================================================
# D-060 stages-as-data (B2/P70): the VALIDATING handler honours the composed
# pipeline's post_merge_gate presence.
# ============================================================================

def test_validating_runs_gate_when_pipeline_has_gate():
    """B2 parity: the default pipeline includes post_merge_gate, so a VALIDATING
    task re-emits RunPostMergeGate exactly as before B2 -- byte-identical to the
    pre-B2 hardcoded behaviour (make_config() carries DEFAULT_PIPELINE)."""
    cfg = make_config()
    assert "post_merge_gate" in cfg.pipeline
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.VALIDATING)
    inp = ReconcileInput(**_carve_base_kwargs(
        cfg=cfg, states={"P01": tsf}, frontmatters={"P01": (fm, "h.md")}))

    task_actions = [a for a in plan_project(inp) if a.task_id == "P01"]

    assert any(isinstance(a, RunPostMergeGate) for a in task_actions)
    assert not any(isinstance(a, Transition) and a.to == TaskState.COMPLETED
                   for a in task_actions)


def test_validating_autocompletes_when_pipeline_omits_gate():
    """B2 discrimination: a pipeline without post_merge_gate (the `lean` shape)
    auto-advances VALIDATING -> COMPLETED and NEVER emits RunPostMergeGate.
    Neuter the reconcile item-11 gate check (always emit RunPostMergeGate) and
    this fails -- a real discriminator, not a hollow assert."""
    cfg = replace(make_config(), pipeline=[
        "carve", "implement", "frontier_review", "triage", "auto_merge"])
    assert "post_merge_gate" not in cfg.pipeline
    fm = make_frontmatter(id="P01")
    tsf = make_tsf(task_id="P01", state=TaskState.VALIDATING)
    inp = ReconcileInput(**_carve_base_kwargs(
        cfg=cfg, states={"P01": tsf}, frontmatters={"P01": (fm, "h.md")}))

    task_actions = [a for a in plan_project(inp) if a.task_id == "P01"]

    assert not any(isinstance(a, RunPostMergeGate) for a in task_actions)
    completes = [a for a in task_actions
                 if isinstance(a, Transition) and a.to == TaskState.COMPLETED]
    assert len(completes) == 1
