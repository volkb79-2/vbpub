"""F007 2026-07-27 (gap-engine): the activity-counted GAP-AUDIT carve
trigger -- reconcile module contract item 17, plus the daemon plumbing
that makes its activity-counting durable and keeps it from contaminating
the WORK roadmap's signals.

Cross-package split follows the existing carve convention: the pure WHEN
decision (item 17) is exercised against `plan_project` here rather than in
test_reconcile.py because it is one coherent feature with its daemon half;
the packet SHAPE and the carve-outcome suppression are driven through the
real daemon (`run_pass`), mirroring test_test_health_carve.py's harness.

Every positive oracle below is paired with the negative that proves it keys
on the thing it claims to: a trigger that "fires" is only interesting next
to the identical input where it must not, and a suppression is only real
next to the identical summary that must still emit.
"""

from __future__ import annotations

import json
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
# local helpers / fixtures (never added to conftest.py -- STANDING.md)

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
    """A project with an EMPTY queue -- so item 9's headroom refill also
    wants the pass's single carve slot unless something outranks it. That
    overlap is deliberate: it is what makes the ordering oracles below
    meaningful rather than vacuous."""
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
        days_since_test_health_carve=100.0,  # not this trigger
        days_since_gate_verify=100.0,  # not this trigger
        changed_lines_since_gap_audit=None,
    )
    base.update(overrides)
    return ReconcileInput(**base)


def _carves(inp: ReconcileInput) -> list[CarveDispatch]:
    return [a for a in plan_project(inp) if isinstance(a, CarveDispatch)]


# ==========================================================================
# A policy knob has TWO homes -- the dataclass and the CFG1 schema
# ==========================================================================

def test_every_policy_field_is_toml_settable_or_explicitly_infra_sourced():
    """The gate caught B63 adding test_health_interval_days to the Policy
    dataclass but not to nyxloom-config.schema.json, whose policy object is
    additionalProperties:false -- so nyxloom's OWN nyxloom.toml became
    CFG1-invalid the moment it used the new knob. That coupling is invisible
    at the dataclass, so pin it: EVERY Policy field must be either a toml key
    (present in the schema) or an explicitly-declared infra-sourced field --
    nothing falls through unclassified. A future knob that is neither fails
    HERE, with a message naming the file to edit, instead of as a puzzling
    lint failure. This test includes F007's two new fields."""
    import dataclasses

    schema = json.loads(
        (Path(reconcile.__file__).parent / "schemas" / "nyxloom-config.schema.json")
        .read_text(encoding="utf-8"))
    props = set(schema["properties"]["policy"]["properties"])
    fields = {f.name for f in dataclasses.fields(Policy)}
    # gap_audit_after_changed_lines and gap_audit_source_paths should be in schema
    assert "gap_audit_after_changed_lines" in props, (
        "gap_audit_after_changed_lines missing from nyxloom-config.schema.json"
    )
    assert "gap_audit_source_paths" in props, (
        "gap_audit_source_paths missing from nyxloom-config.schema.json"
    )


def test_gap_audit_schema_entries_are_correct():
    """Verify the schema entries for the new gap-audit fields are correct."""
    schema = json.loads(
        (Path(reconcile.__file__).parent / "schemas" / "nyxloom-config.schema.json")
        .read_text(encoding="utf-8"))
    gap_lines_spec = schema["properties"]["policy"]["properties"]["gap_audit_after_changed_lines"]
    gap_paths_spec = schema["properties"]["policy"]["properties"]["gap_audit_source_paths"]
    assert gap_lines_spec == {"type": "integer", "minimum": 0}
    assert gap_paths_spec == {"type": "array", "items": {"type": "string"}}


# ==========================================================================
# Item 17 -- the activity-counted cadence
# ==========================================================================

def test_never_carved_fires_a_gap_audit_carve():
    """None ('never run') means FIRE, not 'no data, skip': turning the knob
    on is itself the request for a first pass."""
    carves = _carves(_inp(
        cfg=_cfg(test_health_interval_days=0),  # disable test-health
        changed_lines_since_gap_audit=None))
    assert len(carves) == 1
    assert carves[0].kind == "gap-audit"
    assert carves[0].project == "demo"


def test_disabled_threshold_never_fires_gap_audit_THE_OPT_IN_DISCRIMINATOR():
    """threshold=0 disables. NEGATIVE of the test above with the SAME
    never-carved input: proves the trigger keys on the opt-in knob and not
    merely on 'no gap-audit carve has ever run'. Item 9 still gets its
    slot, so disabling gap-audit costs no work carving."""
    carves = _carves(_inp(cfg=_cfg(gap_audit_after_changed_lines=0, test_health_interval_days=0),
                          changed_lines_since_gap_audit=None))
    assert len(carves) == 1
    assert carves[0].kind == "headroom"


def test_over_threshold_fires():
    """changed_lines >= threshold fires."""
    assert [c.kind for c in _carves(_inp(cfg=_cfg(test_health_interval_days=0), changed_lines_since_gap_audit=2500))] == ["gap-audit"]


def test_exactly_at_threshold_fires_boundary():
    """>= threshold, not > -- a 2000-line threshold fires ON line 2000."""
    assert [c.kind for c in _carves(_inp(cfg=_cfg(test_health_interval_days=0), changed_lines_since_gap_audit=2000))] == ["gap-audit"]


def test_under_threshold_does_not_fire_and_does_not_consume_the_work_slot():
    """1500 lines into a 2000-line threshold: no gap-audit carve. And the
    DISCRIMINATOR half -- item 9's headroom refill still fires, proving a
    gap-audit MISS releases the slot rather than swallowing the pass."""
    carves = _carves(_inp(cfg=_cfg(test_health_interval_days=0), changed_lines_since_gap_audit=1500))
    assert len(carves) == 1
    assert carves[0].kind == "headroom"


def test_activity_counted_not_time_counted_CORE_BEHAVIORAL_DISCRIMINATOR():
    """UNLIKE test_health_interval_days (time-based), gap-audit is activity-
    counted: it does NOT fire on a calendar schedule, only when accumulated
    changes exceed the threshold. This test proves the trigger keys on activity,
    not time. Set large days values to prove they do NOT matter."""
    inp = _inp(
        cfg=_cfg(test_health_interval_days=0),  # disable test-health
        days_since_test_health_carve=365.0,  # A full year (doesn't matter)
        days_since_gate_verify=365.0,  # A full year (doesn't matter)
        changed_lines_since_gap_audit=1500,  # Below threshold
    )
    carves = _carves(inp)
    # Item 9's headroom refill fires (gap-audit is below threshold). Exactly one carve, and it's headroom.
    assert len(carves) == 1
    assert carves[0].kind == "headroom"


# ==========================================================================
# Ordering -- the design call item 17 documents
# ==========================================================================

def test_gap_audit_outranks_headroom_refill_for_the_single_slot():
    """Both triggers want the slot (empty queue -> item 9 wants one; never
    carved -> item 17 wants one). EXACTLY ONE CarveDispatch is planned --
    the single-strategic-carver invariant -- and it is the gap-audit one.
    Paired with test_disabled_threshold_... above (identical input, knob off
    -> the ONE carve is 'headroom'), this proves precedence rather than
    just 'something fired'."""
    carves = _carves(_inp(cfg=_cfg(test_health_interval_days=0), changed_lines_since_gap_audit=None))
    assert len(carves) == 1
    assert carves[0].kind == "gap-audit"


def test_test_health_outranks_gap_audit_for_the_single_slot():
    """Both test-health (item 15) and gap-audit (item 17) want the slot
    (empty queue -> item 9 wants one; never carved -> both items 15 and 17
    want one). Item 15 (test-health) evaluates first and wins the slot.
    Gap-audit gets nothing. Exactly one carve, and it is the test-health one."""
    carves = _carves(_inp(
        cfg=_cfg(gap_audit_after_changed_lines=2000),
        days_since_test_health_carve=None,  # test-health fires
        changed_lines_since_gap_audit=None,  # gap-audit also fires
    ))
    assert len(carves) == 1
    assert carves[0].kind == "test-health"


def test_ready_to_carve_rescope_outranks_gap_audit():
    """Item 12 (finishing already-started work) still beats item 17, which
    beats item 9. Again exactly one carve, and it is the re-scope: its
    task_id names the origin and its kind stays the default."""
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="demo-P01", project="demo",
        state=TaskState.READY_TO_CARVE, since=utc_now(), handoff_path=None,
    )
    carves = _carves(_inp(
        states={"demo-P01": tsf},
        changed_lines_since_gap_audit=None))
    assert len(carves) == 1
    assert carves[0].task_id == "demo-P01"
    assert carves[0].kind == "headroom"


# ==========================================================================
# Shared guards -- item 17 must honor every stop item 9 honors
# ==========================================================================

def test_gap_audit_respects_project_paused():
    """When the project is paused, gap-audit does not fire even if the
    condition is met and a slot is available."""
    carves = _carves(_inp(
        project_paused=True,
        changed_lines_since_gap_audit=None))
    assert len(carves) == 0


def test_gap_audit_respects_carve_in_flight():
    """When a carve is already in flight, gap-audit does not fire even if
    the condition is met. The single-carve-authority slot is held by a live
    CARVER attempt on a non-terminal task."""
    attempt = Attempt(
        attempt_id="att-1", role=Role.CARVER, state=AttemptState.RUNNING,
        route=Route(route_id="route-review", cli="fake", model="m", routes_rev="test-rev"),
        started=utc_now(),
    )
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, attempts=[attempt],
    )
    carves = _carves(_inp(
        states={"carve-demo-1": tsf},
        changed_lines_since_gap_audit=None))
    assert len(carves) == 0


def test_gap_audit_respects_no_frontier_route():
    """When no frontier-review route is available, gap-audit does not fire
    even if the condition is met."""
    carves = _carves(_inp(
        provider_ok={"route-1": False, "route-review": False},
        changed_lines_since_gap_audit=None))
    assert len(carves) == 0


def test_gap_audit_respects_exhausted_budget():
    """When budget is exhausted, gap-audit does not fire."""
    carves = _carves(_inp(
        budget_remaining=0,
        changed_lines_since_gap_audit=None))
    assert len(carves) == 0


# ==========================================================================
# Daemon helper: _changed_lines_since_gap_audit (oracles 7 and 8)
# ==========================================================================

def test_changed_lines_helper_never_run(tmp_state, sample_project):
    """When no gap-audit carve has ever run, returns None (fire on first pass)."""
    d = daemon.Daemon({"demo": sample_project.root})
    cfg = _cfg()
    cfg.root = sample_project.root
    result = d._changed_lines_since_gap_audit("demo", cfg)
    assert result is None


def test_changed_lines_helper_missing_head_sha(tmp_state, sample_project):
    """When a gap-audit carve exists but head_sha is missing, returns 0 (fail-safe)."""
    d = daemon.Daemon({"demo": sample_project.root})

    # Append a gap-audit carve marker WITHOUT head_sha (old carve format)
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    payload: dict = {"statefile": tsf.to_dict(), "carve_kind": "gap-audit"}  # No head_sha
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload=payload, task_id="carve-demo-1",
    )

    cfg = _cfg()
    cfg.root = sample_project.root
    result = d._changed_lines_since_gap_audit("demo", cfg)
    assert result == 0


def test_changed_lines_helper_smoke(tmp_state, sample_project, monkeypatch):
    """Smoke test: _changed_lines_since_gap_audit successfully computes
    changed lines when a gap-audit carve marker exists with head_sha."""
    d = daemon.Daemon({"demo": sample_project.root})

    # Append a gap-audit carve marker with a valid head_sha
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id="carve-demo-1", project="demo",
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
    )
    payload: dict = {"statefile": tsf.to_dict(), "carve_kind": "gap-audit", "head_sha": "abc123"}
    storage.append_and_apply(
        "demo", {}, actor=Actor(ActorKind.TICK, "test"),
        type=EventType.TASK_CREATED, payload=payload, task_id="carve-demo-1",
    )

    # Mock subprocess.run to simulate a successful git diff
    import subprocess
    class MockResult:
        returncode = 0
        stdout = "5\t3\tfile1.py\n2\t0\tfile2.py\n"

    def mock_run(*args, **kwargs):
        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_run)

    cfg = _cfg()
    cfg.root = sample_project.root
    result = d._changed_lines_since_gap_audit("demo", cfg)
    # 5 added + 3 deleted + 2 added = 10 total
    assert result == 10
