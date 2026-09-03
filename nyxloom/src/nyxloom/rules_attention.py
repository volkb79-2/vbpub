"""Attention rules (CR-06a): module contract items 6 and 7.

What these two have in common is that they claim NOTHING. They are the
planner's reporting surface: the progress ratchet and the spec-health signals
raise a flag for a human. Neither starts an agent turn, so neither contends
for the single carve slot -- which is precisely what the arbiter now makes
visible, where the monolith left it to a reader noticing which locals a
branch did and did not mention.

nyxloom-P98 (2026-09-02) retired this module's third rule, the GA4
gate-verify-cadence-overdue probe (module contract item 16) -- superseded by
Assay's own R2/R3 mechanisms once a project declares assay/run-gate lanes
(see `nyxloom-trove/decisions.md`).

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
from .reconcile import SpecAttention


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
