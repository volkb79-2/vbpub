"""Environment profiles for the CR-06 planner differential corpus.

A `ReconcileInput` is a projection PLUS an environment: routes, provider
health, budget, pause mode, carver session, cadence ages. The historical
event logs give the first half exactly (see `tools/extract_planner_corpus.py`);
they never persisted the second half per pass, so this module supplies it as
DECLARED profiles rather than pretending to recover it.

That separation is the point. The corpus is a CROSS PRODUCT: every real
projection is planned under every declared environment, so a rule whose
historical inputs happened never to vary is still exercised across the range
its guards discriminate on. A profile is a small named dict of overrides, and
each one names the guard it exists to move.

Shared by the extractor (which uses `baseline_input` to prove pruning is
plan-faithful) and by `tests/planner_differential.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from nyxloom.carver_session import CarverSessionSnapshot, CarverStatus
from nyxloom.config import MutexDef, Policy, ProjectConfig, RouteDef, Routes
from nyxloom.reconcile import ReconcileInput
from nyxloom.types import (
    AttemptState, Frontmatter, Role, Scope, Source, TaskStateFile,
)

#: Fixed clock. The planner reads `inp.now` only to age waves and attempts, so
#: a constant here keeps every corpus plan reproducible -- a differential that
#: moved with wall-clock time could not distinguish a real delta from a slow
#: test run.
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

TIER = "corpus-tier"
ROUTE_A = "corpus-route"
ROUTE_B = "corpus-route-b"


def make_config(**policy_overrides) -> ProjectConfig:
    policy_kwargs = dict(
        max_active_tasks=2,
        max_attempts_per_task=3,
        max_consecutive_zero_progress_merges=3,
        wave_max_diffs=3,
        carve_ahead_target=5,
        max_resume_failures=2,
        resume_progress_grace_seconds=120,
    )
    policy_kwargs.update(policy_overrides)
    return ProjectConfig(
        project_id="corpus",
        root=Path("/corpus"),
        default_branch="main",
        worktree_root=".worktrees",
        handoff_globs=["handoff/*.md"],
        gates={},
        mutexes={"stack": MutexDef(name="stack", scope="project", capacity=1)},
        policy=Policy(**policy_kwargs),
    )


def make_routes() -> Routes:
    """Two routes, reachable both by tier and by the review role.

    `frontier_route_available` resolves through `Routes.for_role`, and
    ordinary dispatch through `for_tier`; a corpus whose routes answered only
    one of them would leave every carve guard permanently closed.
    """
    return Routes(
        revision="corpus",
        tiers={TIER: [ROUTE_A, ROUTE_B], Role.REVIEW_INDEPENDENT.value: [ROUTE_A]},
        routes={
            ROUTE_A: RouteDef(route_id=ROUTE_A, cli="fake", model="fake-a"),
            ROUTE_B: RouteDef(route_id=ROUTE_B, cli="fake", model="fake-b"),
        },
    )


def make_frontmatter(task_id: str, *, input_revision: str = "abc1234") -> Frontmatter:
    return Frontmatter(
        schema_version=1,
        id=task_id,
        project="corpus",
        title="corpus task",
        tier=TIER,
        input_revision=input_revision,
        source=Source(kind="roadmap"),
        scope=Scope(touch=["src/x.py"]),
        oracles=[],
        gates=[],
        escalate_if=[],
        depends_on=[],
        stack="none",
        mutexes=[],
        budget=None,
    )


def baseline_input(states: dict[str, TaskStateFile], **overrides) -> ReconcileInput:
    """A `ReconcileInput` around a real projection, with a neutral environment.

    Neutral means: every route healthy, nothing paused, no budget cap, no
    carver session, every cadence knob off. Each profile below moves ONE part
    of that away from neutral, so a plan difference is attributable.

    Derived rather than declared, because these must agree with the projection
    or the input is not a coherent snapshot:

    * `frontmatters` -- one per projected task, so a QUEUED task is genuinely
      dispatchable (a projection whose tasks had no frontmatter would make the
      whole dispatch rule vacuous);
    * `receipts` / `pid_alive` -- read off each attempt record, so the receipt
      ladder sees the same world the projection describes: an attempt that
      RECORDED a receipt has one, a terminal attempt's pid is dead, a live one
      is alive.
    """
    frontmatters = {
        tid: (make_frontmatter(tid), f"handoff/{tid}.md") for tid in states
    }
    receipts: dict[str, dict | None] = {}
    pid_alive: dict[str, bool] = {}
    for tsf in states.values():
        for att in tsf.attempts:
            receipts[att.attempt_id] = (
                {"result": att.receipt.result.value} if att.receipt is not None else None
            )
            pid_alive[att.attempt_id] = att.state in (
                AttemptState.RUNNING, AttemptState.PREFLIGHTING)
    kwargs: dict = dict(
        now=NOW,
        cfg=make_config(),
        routes=make_routes(),
        states=states,
        frontmatters=frontmatters,
        lint_clean={tid: True for tid in states},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={ROUTE_A: True, ROUTE_B: True},
        log_quiet_seconds={},
        pid_alive=pid_alive,
        receipts=receipts,
    )
    policy_overrides = overrides.pop("_policy", None)
    if policy_overrides:
        kwargs["cfg"] = make_config(**policy_overrides)
    kwargs.update(overrides)
    return ReconcileInput(**kwargs)


def _carver(status: CarverStatus, **kw) -> CarverSessionSnapshot:
    fields = dict(
        project="corpus", generation=1, status=status, session_handle="corpus-session",
        successful_turns_since_compaction=0, resume_failures=0,
        measured_context_ratio=None, last_consumed_event_sequence=0,
    )
    fields.update(kw)
    return CarverSessionSnapshot(**{
        k: v for k, v in fields.items()
        if k in CarverSessionSnapshot.__dataclass_fields__
    })


#: (name, overrides, the guard this profile exists to move).
PROFILES: tuple[tuple[str, dict, str], ...] = (
    ("neutral", {}, "nothing -- the unmodified environment"),
    ("paused", {"project_paused": True}, "project_paused: dispatch, carve, merge, gate-verify"),
    ("drain-agents", {"project_paused": True, "pause_mode": "drain-agents"},
     "pause_mode: resume, review launch, self-review, gate diagnosis"),
    ("drain-handoffs", {"project_paused": True, "pause_mode": "drain-handoffs"},
     "pause_mode: the half that still launches agents"),
    ("no-healthy-route", {"provider_ok": {}},
     "dispatch_eligible check 9 and frontier_route_available"),
    ("budget-exhausted", {"budget_remaining": 0.0},
     "dispatch_eligible check 7 and budget_allows"),
    ("guarded-automatic", {"_policy": {"merge_mode": "guarded-automatic"}},
     "contract item 13: AutoMergeTask on MERGE_READY"),
    ("wave-aged", {"wave_open_after_seconds": 0},
     "contract item 5: the wave age trigger"),
    ("decisions-open", {"decisions_open": {"D-001"}},
     "contract item 2 and the carve ready-count's decision-held exclusion"),
    ("test-health-due", {"_policy": {"test_health_interval_days": 7},
                         "days_since_test_health_carve": None},
     "contract item 15"),
    ("gap-audit-due", {"_policy": {"gap_audit_after_changed_lines": 100},
                       "changed_lines_since_gap_audit": 5000},
     "contract item 17"),
    ("gate-verify-due", {"_policy": {"gate_verify_interval_days": 7},
                         "days_since_gate_verify": None},
     "contract item 16"),
    ("carver-warm", {"carver_session": _carver(CarverStatus.WARM)},
     "carver ladder: WARM with nothing pending"),
    ("carver-cold", {"carver_session": _carver(CarverStatus.COLD)},
     "carver ladder slot 2: bootstrap"),
    ("carver-degraded", {"carver_session": _carver(CarverStatus.DEGRADED)},
     "carver ladder slot 2 companion: bounded recovery"),
    ("carver-compaction-due",
     {"carver_session": _carver(CarverStatus.WARM, successful_turns_since_compaction=40)},
     "carver ladder slot 4: the turn-fallback compaction trigger"),
    ("stalled-logs", {"log_quiet_seconds": None},   # replaced below, per input
     "contract item 4: the stall ladder"),
    ("lease-held", {"leases_free": {"corpus/stack": False}},
     "dispatch_eligible check 8"),
)


def build(states: dict[str, TaskStateFile], profile: str) -> ReconcileInput:
    """The input for one (projection, profile) pair."""
    overrides = dict(next(o for name, o, _ in PROFILES if name == profile))
    if profile == "stalled-logs":
        # Every attempt's log is quiet past the threshold. Derived from the
        # projection rather than declared, because the keys are attempt ids
        # this snapshot happens to carry.
        overrides["log_quiet_seconds"] = {
            att.attempt_id: 100_000.0
            for tsf in states.values() for att in tsf.attempts
        }
        overrides["stall_confirmed"] = {
            att.attempt_id: True
            for tsf in states.values() for att in tsf.attempts
        }
    return baseline_input(states, **overrides)


PROFILE_NAMES: tuple[str, ...] = tuple(name for name, _, _ in PROFILES)
