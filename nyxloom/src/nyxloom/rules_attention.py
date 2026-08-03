"""Attention and cadence rules (CR-06a): module contract items 6, 7 and 16.

What these three have in common is that they claim NOTHING. They are the
planner's reporting and maintenance surface: the progress ratchet and the
spec-health signals raise a flag for a human, and the gate-verify cadence runs
a subprocess probe. None of them starts an agent turn, so none of them
contends for the single carve slot -- which is precisely what the arbiter now
makes visible, where the monolith left it to a reader noticing which locals a
branch did and did not mention.

DEDUP IS THE DESIGN HERE, not an optimisation. Every SpecAttention branch is
gated on an "already open in the recent window" flag the daemon computes,
because these conditions are PERSISTENT: `review_rejections_by_area` staying
>= 2 forever (that count never decreases either) re-emitted SpecAttention
every single reconcile pass and stormed notifications -- the 2026-07-16 prod
incident that P44 closed. A rule whose condition does not clear itself needs
an external memory of having fired, and that memory arrives as input because
this module never reads an event log.
"""

from __future__ import annotations

from .planning import PlanContext, RuleEmitter
from .reconcile import SpecAttention, VerifyGate


def progress_ratchet(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Contract item 6 (SPEC §8): if the last
    policy.max_consecutive_zero_progress_merges merges all have 0 progress
    units AND all came from 'review', raise the ratchet -- once (deduped via
    ratchet_already_open)."""
    inp = ctx.inp
    if not inp.ratchet_already_open and inp.merge_history:
        # Get last N merges where N = max_consecutive_zero_progress_merges
        n = inp.cfg.policy.max_consecutive_zero_progress_merges
        recent_merges = inp.merge_history[:n]
        if len(recent_merges) == n:
            all_zero_review = all(
                units == 0 and source == 'review'
                for _, units, source in recent_merges
            )
            if all_zero_review:
                emit(SpecAttention(reason='ratchet', detail=None))


def spec_health(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Contract item 7 (SPEC §9 triggers 1-3): carve outcomes, repeated
    rejections in one area, and blocked-underspecified volume.

    Each branch is separately deduped (P44 2026-07-16, anti-runaway
    self-correction) -- before that fix these three had no dedup at all, so a
    persistent condition re-emitted every pass forever. The emission ORDER
    here is the order these appear in the plan, which is why they are one rule
    rather than three."""
    inp = ctx.inp

    # Spec health: carve outcomes (P44 2026-07-16: dedup via
    # carve_outcome_already_open -- see module contract item 7)
    if not inp.carve_outcome_already_open:
        for outcome in inp.carve_outcomes:
            outcome_type = outcome.get('outcome')
            if outcome_type == 'SPEC_GAP':
                emit(SpecAttention(reason='carve-outcome', detail=None))
                break

    # Spec health: review rejections (P44 2026-07-16: dedup via
    # rejections_already_open -- this was the actual notification-storm
    # root cause: review_rejections_by_area never decreased AND this
    # branch never deduped, so 2 rejections re-emitted every pass forever)
    if not inp.rejections_already_open:
        for area, count in inp.review_rejections_by_area.items():
            if count >= 2:
                emit(SpecAttention(reason='rejections', detail=None))
                break

    # Spec health: blocked underspecified (P44 2026-07-16: dedup via
    # blocked_underspecified_already_open -- see module contract item 7)
    if not inp.blocked_underspecified_already_open and inp.blocked_underspecified_count >= 3:
        emit(SpecAttention(reason='blocked-underspecified', detail=None))


def gate_verify(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Contract item 16 (GA4 2026-07-25): the periodic gate re-verification
    probe -- re-run GA1's `nyxloom gate verify` canary check to confirm the
    project's declared gate STILL rejects a known-bad commit. A gate can
    quietly stop discriminating (a lint exclusion widens, a test gets skipped)
    with nothing else in the planner noticing.

    OUTSIDE THE CARVE MUTEX, DELIBERATELY, and this rule claims no resource at
    all so that is now a structural fact rather than an omission a reader has
    to spot. A gate verify runs a subprocess against a few disposable canary
    commits: it needs no LLM frontier route, no carve budget and no agent
    turn, so it is not a carve sibling. Making it contend for the carve slot
    would mean a busy carve queue silently suppresses the one probe that
    notices a gate has stopped rejecting -- the failure this cadence exists to
    catch. It therefore never consults carve_in_flight /
    frontier_route_available / budget_allows either; only project_paused gates
    it, because a paused project starts no new process of any kind (the same
    invariant contract item 14 closed for carving).

    Fires when policy.gate_verify_interval_days > 0 (opt-in; 0 disables, the
    safety default) AND days_since_gate_verify is None (never run -- turning
    the knob on is itself the request for a first pass) or >= that interval.
    The daemon executes it on a background thread and is idempotent while one
    is already in flight for the project, so replanning the identical
    VerifyGate every pass until the drain step appends GATE_VERIFY_RECORDED is
    harmless, not a runaway.
    """
    inp = ctx.inp
    gate_verify_interval = inp.cfg.policy.gate_verify_interval_days
    if gate_verify_interval > 0:
        if inp.project_paused:
            emit.note("gate-verify", None, "paused")
        else:
            gv_age = inp.days_since_gate_verify
            if gv_age is None or gv_age >= gate_verify_interval:
                emit(VerifyGate(project=inp.cfg.project_id))
                emit.note("gate-verify", None, "fire")
