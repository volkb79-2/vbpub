"""F018 P2b-A1 — deterministic carver-session planner tests.

Covers docs/plan-long-running-carver.md §12 Package 2's oracles for the
planner half (P2b): the carver-turn priority ladder (§3.3), the four new
ReconcileInput fields' MASTER GATE (byte-identical-when-off), the pure
§6.2 compaction-trigger predicate, and the single-carver-authority mutex
shared with the pre-existing CarveDispatch triggers.

This file is deliberately self-contained (small local `make_*` helpers,
mirroring every other test_*.py in this suite -- conftest.py is FROZEN
and shared fixtures are not added there per its own docstring) rather
than importing test_reconcile.py's helpers.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom.carver_session import (
    CarveRepairRequest, CarverFeed, CarverSessionSnapshot, HumanIntake,
    ValidatedCarveProposal,
)
from nyxloom.config import MutexDef, Policy, ProjectConfig, RouteDef, Routes
from nyxloom.reconcile import (
    Action, AdmitCarveProposal, CarveDispatch, CompactCarverSession,
    ReconcileInput, ResumeCarverSession, StartCarverSession,
    _compaction_due, plan_project,
)
from nyxloom.types import (
    Attempt, AttemptState, CarverStatus, Frontmatter, Role, Route, Scope,
    Source, TaskState, TaskStateFile,
)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def make_config(pipeline: list[str] | None = None, **policy_overrides) -> ProjectConfig:
    """Minimal ProjectConfig. Default pipeline (DEFAULT_PIPELINE) includes
    `carve`; pass pipeline=[...] to build a carve-less (gated/lean) shape."""
    cfg = ProjectConfig(
        project_id="demo",
        root=Path("/demo"),
        default_branch="main",
        worktree_root=".worktrees",
        handoff_globs=["handoff/*.md"],
        gates={},
        mutexes={"stack": MutexDef(name="stack", scope="project", capacity=1)},
        policy=Policy(carve_ahead_target=5, **policy_overrides),
    )
    if pipeline is not None:
        cfg = replace(cfg, pipeline=pipeline)
    return cfg


def make_frontmatter(id: str = "demo-P01", depends_on: list[str] | None = None) -> Frontmatter:
    return Frontmatter(
        schema_version=1,
        id=id,
        project="demo",
        title="Test",
        tier="flash-high",
        input_revision="abc123",
        source=Source(kind="roadmap"),
        scope=Scope(touch=["test.py"]),
        oracles=[],
        gates=[],
        escalate_if=[],
        depends_on=depends_on or [],
        stack="none",
        mutexes=[],
        budget=None,
    )


def make_tsf(task_id: str = "demo-P01", state: TaskState = TaskState.QUEUED,
             attempts: list[Attempt] | None = None) -> TaskStateFile:
    return TaskStateFile(
        schema_version=1, task_id=task_id, project="demo", state=state,
        since=utc(2026, 7, 15), paused=False, attempts=attempts or [],
    )


def make_routes() -> Routes:
    """flash-high (ordinary implementer tier) + review-independent (the
    carver's own frontier-review route). Mirrors test_reconcile.py's
    make_carve_routes."""
    return Routes(
        revision="test",
        tiers={"flash-high": ["route-1"], "review-independent": ["route-review"]},
        routes={
            "route-1": RouteDef(route_id="route-1", cli="fake", model="fake-model"),
            "route-review": RouteDef(route_id="route-review", cli="fake", model="review-model",
                                      role_default="review-independent"),
        },
    )


def base_kwargs(**overrides) -> dict:
    base = dict(
        now=utc(2026, 7, 15),
        cfg=make_config(),
        routes=make_routes(),
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


def make_snapshot(status: CarverStatus, generation: int = 1, **overrides) -> CarverSessionSnapshot:
    return CarverSessionSnapshot(project="demo", generation=generation, status=status, **overrides)


def carver_turn_actions(actions: list[Action]) -> list[Action]:
    """Every action this pass's single carve/carver-turn slot could have
    produced -- legacy CarveDispatch plus all 4 new-vocabulary actions."""
    kinds = (CarveDispatch, StartCarverSession, ResumeCarverSession,
             CompactCarverSession, AdmitCarveProposal)
    return [a for a in actions if isinstance(a, kinds)]


# ============================================================================
# MASTER GATE (the load-bearing negative): carver_session is None -> the
# plan is byte-identical to the pre-P2b behaviour, for a representative set
# of existing carve scenarios -- even when the OTHER 3 new fields are
# populated (a stronger check than merely omitting them).
# ============================================================================

def _stray_new_fields() -> dict:
    """Non-empty feed/intake/proposal payloads, deliberately paired with
    carver_session=None below -- if the MASTER GATE were checking the wrong
    field, this would catch it."""
    return dict(
        pending_carver_feeds=(
            CarverFeed(digest_id="merge:demo:1", merge_commit="a" * 40,
                       task_id="demo-P01", event_sequence=1, files_changed=2),
        ),
        pending_human_intakes=(
            HumanIntake(intake_id="intake-1", kind="idea", event_sequence=2),
        ),
        validated_carve_proposals=(
            ValidatedCarveProposal(
                proposal_id="demo:carve:1:t1", generation=1, source_mode="headroom",
                artifact_ids=["demo-P02"], artifact_paths=["handoff/demo-P02.md"],
                artifact_hashes=["deadbeef"], outcome="CANDIDATES_READY",
            ),
        ),
        # F018 AD3: the MASTER GATE must key off carver_session alone, so a
        # stray non-empty pending_carve_repairs paired with carver_session=None
        # must ALSO leave the plan byte-identical (O6, off-path).
        pending_carve_repairs=(
            CarveRepairRequest(proposal_id="demo:carve:1:t1", generation=1),
        ),
    )


@pytest.mark.parametrize("scenario", [
    "headroom-fires",
    "ready-to-carve-fires",
    "paused-no-fire",
    "budget-exhausted-no-fire",
])
def test_carver_session_none_is_byte_identical_to_pre_p2b(scenario):
    if scenario == "headroom-fires":
        kwargs = base_kwargs()
    elif scenario == "ready-to-carve-fires":
        fm = make_frontmatter(id="R1")
        tsf = make_tsf(task_id="R1", state=TaskState.READY_TO_CARVE)
        kwargs = base_kwargs(states={"R1": tsf}, frontmatters={"R1": (fm, "h.md")})
    elif scenario == "paused-no-fire":
        kwargs = base_kwargs(project_paused=True)
    else:  # budget-exhausted-no-fire
        kwargs = base_kwargs(budget_remaining=0.0)

    baseline = plan_project(ReconcileInput(**kwargs))
    # carver_session explicitly None, but the other 3 fields NON-empty --
    # the MASTER GATE must key off carver_session alone.
    with_stray_fields = plan_project(ReconcileInput(**kwargs, carver_session=None, **_stray_new_fields()))

    assert list(with_stray_fields) == list(baseline)
    assert list(with_stray_fields.trace.breadcrumbs) == list(baseline.trace.breadcrumbs)


# ============================================================================
# PROPERTY: identical ReconcileInput -> identical ordered actions; never
# more than one carver turn (old CarveDispatch OR one new carver action,
# never both).
# ============================================================================

@pytest.mark.parametrize("snapshot_kwargs", [
    dict(status=CarverStatus.ABSENT),
    dict(status=CarverStatus.WARM),
    dict(status=CarverStatus.DEGRADED, resume_failures=0),
    dict(status=CarverStatus.STARTING),
])
def test_identical_input_yields_identical_plan_and_at_most_one_carver_turn(snapshot_kwargs):
    snap = make_snapshot(**snapshot_kwargs)
    kwargs = base_kwargs(
        carver_session=snap,
        pending_carver_feeds=(
            CarverFeed(digest_id="merge:demo:1", merge_commit="a" * 40,
                       task_id="demo-P01", event_sequence=1, files_changed=1),
        ),
    )
    inp = ReconcileInput(**kwargs)
    plan_a = plan_project(inp)
    plan_b = plan_project(inp)

    assert list(plan_a) == list(plan_b)
    assert len(carver_turn_actions(plan_a)) <= 1


# ============================================================================
# NEGATIVE: WARM session + pending merge feed + headroom conditions both
# true -> plans the FEED (ResumeCarverSession merge-feed), NOT a
# CarveDispatch headroom carve.
# ============================================================================

def test_warm_feed_beats_headroom_carve():
    snap = make_snapshot(status=CarverStatus.WARM)
    base = base_kwargs(carver_session=snap)  # empty queue -> headroom eligible

    # Self-contained premise (review Fix 3): establish that WITHOUT the
    # pending feed, this exact setup plans the legacy headroom CarveDispatch
    # -- so "feed beats headroom" below proves an actual override, not an
    # accident of a headroom trigger that was never going to fire anyway.
    baseline_actions = plan_project(ReconcileInput(**base))
    baseline_carves = [a for a in baseline_actions if isinstance(a, CarveDispatch)]
    assert len(baseline_carves) == 1

    kwargs = base_kwargs(
        carver_session=snap,
        pending_carver_feeds=(
            CarverFeed(digest_id="merge:demo:1", merge_commit="a" * 40,
                       task_id="demo-P01", event_sequence=1, files_changed=1),
        ),
    )
    actions = plan_project(ReconcileInput(**kwargs))

    feeds = [a for a in actions if isinstance(a, ResumeCarverSession) and a.mode == "merge-feed"]
    carve_dispatches = [a for a in actions if isinstance(a, CarveDispatch)]
    assert len(feeds) == 1
    assert feeds[0].source_ids == ("merge:demo:1",)
    assert carve_dispatches == []


def test_warm_feed_source_ids_ordered_by_event_sequence_not_digest_id():
    """Review Fix 1 (frozen-core semantic defect): plan §3.2 requires
    'preserving event order'. digest_id is 'merge:{project}:{commit_sha}' --
    an arbitrary hash whose sort order has nothing to do with when each
    merge happened. Construct two feeds where the digest_id sort order is
    the EXACT REVERSE of the event_sequence (arrival) order, so this test
    would fail against the old (buggy) `sorted(f.digest_id for f in ...)`
    implementation."""
    snap = make_snapshot(status=CarverStatus.WARM)
    feed_first = CarverFeed(digest_id="merge:demo:zzz", merge_commit="a" * 40,
                             task_id="demo-P01", event_sequence=1, files_changed=1)
    feed_second = CarverFeed(digest_id="merge:demo:aaa", merge_commit="b" * 40,
                              task_id="demo-P02", event_sequence=2, files_changed=1)
    # digest_id-sorted order would be (aaa, zzz) -- the reverse of arrival order.
    assert sorted([feed_first.digest_id, feed_second.digest_id]) == [feed_second.digest_id, feed_first.digest_id]

    kwargs = base_kwargs(carver_session=snap, pending_carver_feeds=(feed_second, feed_first))
    actions = plan_project(ReconcileInput(**kwargs))
    feeds = [a for a in actions if isinstance(a, ResumeCarverSession) and a.mode == "merge-feed"]
    assert len(feeds) == 1
    assert feeds[0].source_ids == ("merge:demo:zzz", "merge:demo:aaa")


# ============================================================================
# MATRIX: `full` plans session work; `gated`/`lean` never do, even with a
# synthetic carver_session set.
# ============================================================================

_GATED_PIPELINE = ["implement", "self_review", "review_independent", "triage", "auto_merge", "post_merge_gate"]
_LEAN_PIPELINE = ["implement", "self_review", "review_independent", "triage", "auto_merge"]


def test_full_pipeline_plans_session_work():
    snap = make_snapshot(status=CarverStatus.ABSENT)
    assert "carve" in make_config().pipeline
    kwargs = base_kwargs(carver_session=snap)
    actions = plan_project(ReconcileInput(**kwargs))
    starts = [a for a in actions if isinstance(a, StartCarverSession)]
    assert len(starts) == 1


@pytest.mark.parametrize("pipeline", [_GATED_PIPELINE, _LEAN_PIPELINE])
def test_carveless_pipelines_never_plan_session_work(pipeline):
    assert "carve" not in pipeline
    snap = make_snapshot(status=CarverStatus.WARM)
    kwargs = base_kwargs(
        cfg=make_config(pipeline=pipeline),
        carver_session=snap,
        pending_carver_feeds=(
            CarverFeed(digest_id="merge:demo:1", merge_commit="a" * 40,
                       task_id="demo-P01", event_sequence=1, files_changed=1),
        ),
        pending_human_intakes=(HumanIntake(intake_id="i1", kind="idea", event_sequence=1),),
        validated_carve_proposals=(
            ValidatedCarveProposal(
                proposal_id="demo:carve:1:t1", generation=1, source_mode="headroom",
                artifact_ids=["demo-P02"], artifact_paths=["h.md"],
                artifact_hashes=["deadbeef"], outcome="CANDIDATES_READY",
            ),
        ),
    )
    actions = plan_project(ReconcileInput(**kwargs))
    new_vocab = [a for a in actions
                 if isinstance(a, (StartCarverSession, ResumeCarverSession,
                                   CompactCarverSession, AdmitCarveProposal))]
    assert new_vocab == []


# ============================================================================
# Per-status routing (§2.4).
# ============================================================================

@pytest.mark.parametrize("status", [CarverStatus.ABSENT, CarverStatus.COLD, CarverStatus.ROTATING])
def test_cold_absent_rotating_bootstraps(status):
    snap = make_snapshot(status=status)
    actions = plan_project(ReconcileInput(**base_kwargs(carver_session=snap)))
    starts = [a for a in actions if isinstance(a, StartCarverSession)]
    assert len(starts) == 1
    assert starts[0].project == "demo"


@pytest.mark.parametrize("status", [CarverStatus.STARTING, CarverStatus.COMPACTING])
def test_starting_and_compacting_plan_no_turn_at_all(status):
    """§2.4: 'planner emits no second carver turn' / 'all work waits' --
    DESIGN CHOICE: this package treats the pass's single carve/carver slot
    as already consumed for STARTING/COMPACTING, so the LEGACY CarveDispatch
    triggers (item 9's headroom refill, which would otherwise fire on this
    empty-queue setup) are ALSO suppressed, not just the new-vocabulary
    actions."""
    snap = make_snapshot(status=status)
    actions = plan_project(ReconcileInput(**base_kwargs(carver_session=snap)))
    assert carver_turn_actions(actions) == []


def test_degraded_resumes_when_recovery_budget_not_exhausted():
    snap = make_snapshot(status=CarverStatus.DEGRADED, resume_failures=0)
    cfg = make_config()
    assert snap.resume_failures < cfg.carve.max_resume_failures
    actions = plan_project(ReconcileInput(**base_kwargs(cfg=cfg, carver_session=snap)))
    resumes = [a for a in actions if isinstance(a, ResumeCarverSession) and a.mode == "recover"]
    assert len(resumes) == 1
    assert resumes[0].generation == snap.generation


def test_degraded_no_recovery_once_budget_exhausted_falls_back_to_legacy_headroom():
    """DESIGN CHOICE: once resume_failures reaches CarveStageConfig.
    max_resume_failures, this package plans NO new-vocabulary recovery
    action (leaving the DEGRADED session for A2/the operator) and does NOT
    consume the shared mutex -- so the pre-existing legacy headroom trigger
    (item 9) is free to fire on this empty-queue setup, exactly as it
    would with no carver_session at all."""
    cfg = make_config()
    snap = make_snapshot(status=CarverStatus.DEGRADED, resume_failures=cfg.carve.max_resume_failures)
    actions = plan_project(ReconcileInput(**base_kwargs(cfg=cfg, carver_session=snap)))
    new_vocab = [a for a in actions
                 if isinstance(a, (StartCarverSession, ResumeCarverSession,
                                   CompactCarverSession, AdmitCarveProposal))]
    assert new_vocab == []
    carve_dispatches = [a for a in actions if isinstance(a, CarveDispatch)]
    assert len(carve_dispatches) == 1


def test_warm_no_pending_work_plans_nothing_new_falls_through_to_ready_to_carve():
    """WARM with no feed/compaction-due/proposal/intake plans no new-
    vocabulary action this pass; item 12's READY_TO_CARVE re-scope (slot 5,
    the pre-existing CarveDispatch path) still gets its normal chance."""
    snap = make_snapshot(status=CarverStatus.WARM)
    fm = make_frontmatter(id="R1")
    tsf = make_tsf(task_id="R1", state=TaskState.READY_TO_CARVE)
    actions = plan_project(ReconcileInput(**base_kwargs(
        carver_session=snap, states={"R1": tsf}, frontmatters={"R1": (fm, "h.md")})))
    new_vocab = [a for a in actions
                 if isinstance(a, (StartCarverSession, ResumeCarverSession,
                                   CompactCarverSession, AdmitCarveProposal))]
    assert new_vocab == []
    carve_dispatches = [a for a in actions if isinstance(a, CarveDispatch) and a.task_id == "R1"]
    assert len(carve_dispatches) == 1


# ============================================================================
# Compaction trigger predicate (§6.2): turn (24), size (0.70), hard-
# fallback (32 with ratio None); below threshold does not fire.
# ============================================================================

def test_compaction_turn_threshold_fires():
    snap = make_snapshot(status=CarverStatus.WARM, successful_turns_since_compaction=24,
                          measured_context_ratio=0.1)
    assert _compaction_due(snap, make_config()) == "turns"


def test_compaction_size_threshold_fires():
    snap = make_snapshot(status=CarverStatus.WARM, successful_turns_since_compaction=0,
                          measured_context_ratio=0.70)
    assert _compaction_due(snap, make_config()) == "size"


def test_compaction_hard_fallback_fires_when_ratio_untrusted():
    snap = make_snapshot(status=CarverStatus.WARM, successful_turns_since_compaction=32,
                          measured_context_ratio=None)
    assert _compaction_due(snap, make_config()) == "turns-fallback"


def test_compaction_below_all_thresholds_does_not_fire():
    snap = make_snapshot(status=CarverStatus.WARM, successful_turns_since_compaction=23,
                          measured_context_ratio=0.5)
    assert _compaction_due(snap, make_config()) is None


def test_compaction_ratio_none_below_hard_fallback_does_not_fire():
    """Ratio untrusted and turns below the 32-turn hard fallback -> no
    trigger, even though 31 >= the ordinary 24-turn threshold; the ordinary
    threshold does not apply without a trusted ratio (see _compaction_due's
    docstring -- otherwise 'turns-fallback' would be unreachable dead code
    since 24 < 32)."""
    snap = make_snapshot(status=CarverStatus.WARM, successful_turns_since_compaction=31,
                          measured_context_ratio=None)
    assert _compaction_due(snap, make_config()) is None


def test_compaction_due_plans_compact_carver_session():
    snap = make_snapshot(status=CarverStatus.WARM, successful_turns_since_compaction=0,
                          measured_context_ratio=0.85)
    actions = plan_project(ReconcileInput(**base_kwargs(carver_session=snap)))
    compacts = [a for a in actions if isinstance(a, CompactCarverSession)]
    assert len(compacts) == 1
    assert compacts[0].trigger == "size"
    assert compacts[0].generation == snap.generation


# ============================================================================
# Priority ladder (§3.3): when multiple slots are simultaneously eligible,
# the higher-priority slot wins.
# ============================================================================

def _proposal(pid: str = "demo:carve:1:t1") -> ValidatedCarveProposal:
    return ValidatedCarveProposal(
        proposal_id=pid, generation=1, source_mode="headroom",
        artifact_ids=["demo-P02", "demo-P03"], artifact_paths=["h2.md", "h3.md"],
        artifact_hashes=["hash2", "hash3"], outcome="CANDIDATES_READY",
    )


def test_priority_admission_beats_pending_feed():
    snap = make_snapshot(status=CarverStatus.WARM)
    kwargs = base_kwargs(
        carver_session=snap,
        pending_carver_feeds=(
            CarverFeed(digest_id="merge:demo:1", merge_commit="a" * 40,
                       task_id="demo-P01", event_sequence=1, files_changed=1),
        ),
        validated_carve_proposals=(_proposal(),),
    )
    actions = plan_project(ReconcileInput(**kwargs))
    admits = [a for a in actions if isinstance(a, AdmitCarveProposal)]
    feeds = [a for a in actions if isinstance(a, ResumeCarverSession)]
    assert len(admits) == 1
    assert admits[0].artifact_ids == ("demo-P02", "demo-P03")
    assert feeds == []


def test_priority_feed_beats_due_compaction():
    snap = make_snapshot(status=CarverStatus.WARM, successful_turns_since_compaction=0,
                          measured_context_ratio=0.95)  # compaction due (size)
    kwargs = base_kwargs(
        carver_session=snap,
        pending_carver_feeds=(
            CarverFeed(digest_id="merge:demo:1", merge_commit="a" * 40,
                       task_id="demo-P01", event_sequence=1, files_changed=1),
        ),
    )
    actions = plan_project(ReconcileInput(**kwargs))
    feeds = [a for a in actions if isinstance(a, ResumeCarverSession) and a.mode == "merge-feed"]
    compacts = [a for a in actions if isinstance(a, CompactCarverSession)]
    assert len(feeds) == 1
    assert compacts == []


def test_priority_ready_to_carve_beats_targeted_intake():
    snap = make_snapshot(status=CarverStatus.WARM)
    fm = make_frontmatter(id="R1")
    tsf = make_tsf(task_id="R1", state=TaskState.READY_TO_CARVE)
    kwargs = base_kwargs(
        carver_session=snap,
        states={"R1": tsf}, frontmatters={"R1": (fm, "h.md")},
        pending_human_intakes=(HumanIntake(intake_id="i1", kind="idea", event_sequence=1),),
    )
    actions = plan_project(ReconcileInput(**kwargs))
    carve_dispatches = [a for a in actions if isinstance(a, CarveDispatch) and a.task_id == "R1"]
    intake_resumes = [a for a in actions if isinstance(a, ResumeCarverSession) and a.mode == "targeted-intake"]
    assert len(carve_dispatches) == 1
    assert intake_resumes == []


def test_priority_targeted_intake_beats_test_health():
    snap = make_snapshot(status=CarverStatus.WARM)
    cfg = make_config(test_health_interval_days=14)
    kwargs = base_kwargs(
        cfg=cfg,
        carver_session=snap,
        pending_human_intakes=(HumanIntake(intake_id="i1", kind="idea", event_sequence=1),),
        days_since_test_health_carve=30.0,  # overdue -> item 15 would fire alone
    )
    actions = plan_project(ReconcileInput(**kwargs))
    intake_resumes = [a for a in actions if isinstance(a, ResumeCarverSession) and a.mode == "targeted-intake"]
    test_health_dispatches = [a for a in actions
                               if isinstance(a, CarveDispatch) and a.kind == "test-health"]
    assert len(intake_resumes) == 1
    assert intake_resumes[0].source_ids == ("i1",)
    assert test_health_dispatches == []


def test_warm_intake_source_ids_ordered_by_event_sequence_not_intake_id():
    """Review Fix 2 (same class of bug as Fix 1, softer): arrival order
    (event_sequence), not intake_id sort order. Construct two intakes where
    the intake_id sort order is the EXACT REVERSE of the event_sequence
    order, so this test would fail against a `sorted(i.intake_id for ...)`
    implementation."""
    snap = make_snapshot(status=CarverStatus.WARM)
    intake_first = HumanIntake(intake_id="zzz", kind="idea", event_sequence=1)
    intake_second = HumanIntake(intake_id="aaa", kind="idea", event_sequence=2)
    assert sorted([intake_first.intake_id, intake_second.intake_id]) == [intake_second.intake_id, intake_first.intake_id]

    kwargs = base_kwargs(carver_session=snap, pending_human_intakes=(intake_second, intake_first))
    actions = plan_project(ReconcileInput(**kwargs))
    intake_resumes = [a for a in actions if isinstance(a, ResumeCarverSession) and a.mode == "targeted-intake"]
    assert len(intake_resumes) == 1
    assert intake_resumes[0].source_ids == ("zzz", "aaa")


def test_priority_bootstrap_beats_ready_to_carve():
    """Slot 2 (bootstrap, ABSENT) fires before item 12's READY_TO_CARVE
    re-scope even when a READY_TO_CARVE task is present."""
    snap = make_snapshot(status=CarverStatus.ABSENT)
    fm = make_frontmatter(id="R1")
    tsf = make_tsf(task_id="R1", state=TaskState.READY_TO_CARVE)
    kwargs = base_kwargs(
        carver_session=snap, states={"R1": tsf}, frontmatters={"R1": (fm, "h.md")},
    )
    actions = plan_project(ReconcileInput(**kwargs))
    starts = [a for a in actions if isinstance(a, StartCarverSession)]
    carve_dispatches = [a for a in actions if isinstance(a, CarveDispatch)]
    assert len(starts) == 1
    assert carve_dispatches == []


# ============================================================================
# Shared-guard parity: the carver-session ladder respects the SAME hoisted
# guards (project_paused, carve_in_flight, frontier_route_available,
# budget_allows) as the legacy CarveDispatch triggers.
# ============================================================================

def test_ladder_respects_project_paused():
    snap = make_snapshot(status=CarverStatus.ABSENT)
    actions = plan_project(ReconcileInput(**base_kwargs(carver_session=snap, project_paused=True)))
    assert carver_turn_actions(actions) == []


def test_ladder_respects_carve_in_flight():
    carve_att = Attempt(attempt_id="att-carve", role=Role.CARVER, state=AttemptState.RUNNING,
                         route=Route(route_id="route-1", cli="fake", model="fake-model"),
                         started=utc(2026, 7, 15), receipt=None)
    carve_tsf = make_tsf(task_id="carve-demo-1", state=TaskState.ACTIVE, attempts=[carve_att])
    snap = make_snapshot(status=CarverStatus.ABSENT)
    actions = plan_project(ReconcileInput(**base_kwargs(
        carver_session=snap, states={"carve-demo-1": carve_tsf})))
    assert carver_turn_actions(actions) == []


def test_ladder_respects_budget_exhausted():
    snap = make_snapshot(status=CarverStatus.ABSENT)
    actions = plan_project(ReconcileInput(**base_kwargs(carver_session=snap, budget_remaining=0.0)))
    assert carver_turn_actions(actions) == []


def test_ladder_respects_no_frontier_route():
    snap = make_snapshot(status=CarverStatus.ABSENT)
    routes_no_review = Routes(
        revision="test", tiers={"flash-high": ["route-1"]},
        routes={"route-1": RouteDef(route_id="route-1", cli="fake", model="fake-model")},
    )
    actions = plan_project(ReconcileInput(**base_kwargs(
        carver_session=snap, routes=routes_no_review, provider_ok={"route-1": True})))
    assert carver_turn_actions(actions) == []


# ============================================================================
# Trace breadcrumbs (§4.2: short enum/id breadcrumbs, no prose).
# ============================================================================

def test_trace_notes_carver_kind_for_bootstrap():
    snap = make_snapshot(status=CarverStatus.ABSENT)
    result = plan_project(ReconcileInput(**base_kwargs(carver_session=snap)))
    notes = [n for n in result.trace.breadcrumbs if n.kind == "carver"]
    assert len(notes) == 1
    assert notes[0].detail == "start"


# ============================================================================
# F018 AD3 (docs/handoff/f018-ad3-carve-repair-proposal.md): the WARM-ladder
# repair slot -- planner-side oracles (O1 planner, O3 admit>repair,
# O4 repair>feed, ladder completeness repair>compaction, O6 off-path). The
# daemon-side compose/no-double-fire (O2), shared-cursor (O5) and packet (O7)
# oracles live in the executor/daemon test files (they need the event log +
# the _pending_carve_repairs / _build_carver_resume_prompt daemon helpers).
# ============================================================================

def _repair(pid: str = "demo:carve:1:t1", generation: int = 1) -> CarveRepairRequest:
    return CarveRepairRequest(proposal_id=pid, generation=generation)


def test_ad3_warm_repair_plans_repair_proposal_resume_no_source_ids():
    """O1 (planner): a WARM session with a pending repair, no valid proposal,
    no feed -> exactly one ResumeCarverSession(mode='repair-proposal',
    generation=g) carrying NO source_ids (invariant #2: not a feed, so the P4a
    cursor is untouched), and NO merge-feed/compaction. It also SETS the
    single-carver mutex, so the legacy headroom CarveDispatch this empty-queue
    WARM setup would otherwise fire (see test_warm_feed_beats_headroom_carve's
    baseline) is suppressed."""
    snap = make_snapshot(status=CarverStatus.WARM)

    # Same self-contained premise as test_warm_feed_beats_headroom_carve:
    # WITHOUT the repair, this exact setup plans the legacy headroom carve, so
    # "repair suppresses it" below proves a real override, not a no-op.
    baseline = plan_project(ReconcileInput(**base_kwargs(carver_session=snap)))
    assert len([a for a in baseline if isinstance(a, CarveDispatch)]) == 1

    actions = plan_project(ReconcileInput(**base_kwargs(
        carver_session=snap, pending_carve_repairs=(_repair(),))))
    repairs = [a for a in actions
               if isinstance(a, ResumeCarverSession) and a.mode == "repair-proposal"]
    assert len(repairs) == 1
    assert repairs[0].generation == snap.generation
    assert repairs[0].source_ids == ()
    # No other carver turn, and the legacy headroom carve is suppressed.
    assert [a for a in actions
            if isinstance(a, ResumeCarverSession) and a.mode == "merge-feed"] == []
    assert [a for a in actions if isinstance(a, CompactCarverSession)] == []
    assert [a for a in actions if isinstance(a, CarveDispatch)] == []
    assert len(carver_turn_actions(actions)) == 1
    # Trace breadcrumb records the slot with a short enum, no prose.
    notes = [n for n in actions.trace.breadcrumbs if n.kind == "carver"]
    assert [n.detail for n in notes] == ["repair-proposal"]


def test_ad3_admission_beats_repair():
    """O3: a valid proposal to admit out-prioritizes a repair (slot 1 >
    repair) -> AdmitCarveProposal is planned, NO repair turn. Repair only
    matters when there is nothing admittable."""
    snap = make_snapshot(status=CarverStatus.WARM)
    actions = plan_project(ReconcileInput(**base_kwargs(
        carver_session=snap,
        validated_carve_proposals=(_proposal(),),
        pending_carve_repairs=(_repair(),),
    )))
    admits = [a for a in actions if isinstance(a, AdmitCarveProposal)]
    repairs = [a for a in actions
               if isinstance(a, ResumeCarverSession) and a.mode == "repair-proposal"]
    assert len(admits) == 1
    assert repairs == []


def test_ad3_repair_beats_pending_feed():
    """O4: repair fires BEFORE ingesting a new merge feed (a broken premise is
    fixed first) -> the repair turn is planned, the merge-feed is NOT this
    pass. The feed is still present in the input (untouched), so a later pass
    ingests it once the proposal is repaired/admitted."""
    snap = make_snapshot(status=CarverStatus.WARM)
    feed = CarverFeed(digest_id="merge:demo:1", merge_commit="a" * 40,
                      task_id="demo-P01", event_sequence=1, files_changed=1)
    actions = plan_project(ReconcileInput(**base_kwargs(
        carver_session=snap,
        pending_carver_feeds=(feed,),
        pending_carve_repairs=(_repair(),),
    )))
    repairs = [a for a in actions
               if isinstance(a, ResumeCarverSession) and a.mode == "repair-proposal"]
    feeds = [a for a in actions
             if isinstance(a, ResumeCarverSession) and a.mode == "merge-feed"]
    assert len(repairs) == 1
    assert feeds == []


def test_ad3_repair_beats_due_compaction():
    """Ladder completeness (admit > repair > merge-feed > compaction): repair
    outranks a due compaction, mirroring test_priority_feed_beats_due_
    compaction one slot up."""
    snap = make_snapshot(status=CarverStatus.WARM, successful_turns_since_compaction=0,
                          measured_context_ratio=0.95)  # compaction due (size)
    assert _compaction_due(snap, make_config()) == "size"
    actions = plan_project(ReconcileInput(**base_kwargs(
        carver_session=snap, pending_carve_repairs=(_repair(),))))
    repairs = [a for a in actions
               if isinstance(a, ResumeCarverSession) and a.mode == "repair-proposal"]
    compacts = [a for a in actions if isinstance(a, CompactCarverSession)]
    assert len(repairs) == 1
    assert compacts == []


def test_ad3_empty_pending_carve_repairs_plans_no_repair():
    """O6 (on-path, empty signal): the persistent carver is ON (WARM) but
    pending_carve_repairs is empty -> NO repair turn; the pass falls through to
    the pre-AD3 behaviour byte-identically (here, the legacy headroom carve)."""
    snap = make_snapshot(status=CarverStatus.WARM)
    with_off = plan_project(ReconcileInput(**base_kwargs(carver_session=snap)))
    with_empty = plan_project(ReconcileInput(**base_kwargs(
        carver_session=snap, pending_carve_repairs=())))
    assert list(with_empty) == list(with_off)
    assert [a for a in with_empty
            if isinstance(a, ResumeCarverSession) and a.mode == "repair-proposal"] == []
