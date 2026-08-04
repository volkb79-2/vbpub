"""Differential verification for the planner (amendment §5.1).

The amendment makes this a REQUIRED acceptance mechanism for CR-06, not an
idea: "run the old and new planner over identical snapshots -- the CR-00
fixtures *and* the historical event log of the self-host project -- and diff
the planned action sequences. Any difference is either explained in the
package report or is a defect."

The two halves it names are both here:

* `test_historical_corpus_plans_are_identical` -- 800-odd projections replayed
  from three projects' real event logs, each planned under every declared
  environment profile.
* `test_cr00_corpus_plans_are_identical` -- the CR-00 characterization
  scenarios, which are the cases someone did think to write down.

HOW THE BASELINE CANNOT DRIFT. `tests/legacy_planner.py` is a verbatim copy of
`reconcile.py` at the branch point, and imports no production planning code --
so a change to the new planner cannot reach it. The first test below fails if
that copy stops matching the commit it claims to be.

WHAT "IDENTICAL" MEANS, precisely, and why the two halves differ:

* ACTIONS: identical, in order. The legacy module's action classes are
  distinct Python classes from production's, so plans are compared as
  normalized (class name, field mapping) tuples -- which is the observable the
  amendment names, and is insensitive to a class moving modules.
* TRACE: every legacy breadcrumb still appears, in order, with the same
  `(kind, task_id, detail)` -- a SUBSEQUENCE check rather than equality.
  Equality would forbid CR-06's own deliverable: the parent's work item 3 is
  "return actions plus structured rule-match explanations", so the breadcrumb
  stream is expected to GROW. Subsequence keeps every existing reader intact
  (the CR-00 corpus classifies waits from these strings verbatim) while
  letting the explanation model deepen.

WHAT CHANGED IN CR-06b. The package closed the two permutation exemptions
CR-06a had recorded (the attempt ladder's raw map walk, and the warm-review
tie-break), and both move real corpus plans -- so "identical" now means
"identical to the frozen baseline WITH the declared repairs applied to it".
That is still exact equality, not a tolerance: each repair is a MODEL that
reconstructs the new plan from the old one, so a behavioural change the model
does not produce still fails. See `KNOWN_DIVERGENCES` below, and
`test_the_declared_repairs_cannot_absorb_a_foreign_delta`, which is the
control that keeps the models from becoming an exemption.

Under the program's stop-loss (§5.4) an unexplained difference stops the
package. It is not written down as expected.
"""

from __future__ import annotations

import dataclasses
import inspect
import subprocess
from dataclasses import fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import pytest

import corpus_profiles
import legacy_planner
import planner_corpus
import planner_synthetic
from nyxloom import planning, reconcile
from nyxloom.types import AttemptState, CarverStatus, Role, TaskState


def _real_states() -> set:
    return {
        tsf.state for _, states in planner_corpus.projections()
        for tsf in states.values()
    }

REPO = Path(__file__).resolve().parents[1]
LEGACY_PATH = Path(__file__).with_name("legacy_planner.py")

#: The commit `tests/legacy_planner.py` was copied from -- CR-06's branch
#: point on `main`.
BRANCH_POINT = "052857ae"
SOURCE_IN_TREE = "nyxloom/src/nyxloom/reconcile.py"

#: The two mechanical edits the frozen copy declares in its own header, as
#: (frozen text, original text). Applying them in reverse must reproduce the
#: committed blob byte for byte.
_FROZEN_EDITS = (
    ("from nyxloom.carver_session import", "from .carver_session import"),
    ("from nyxloom import snapshot", "from . import snapshot"),
    ("from nyxloom.config import", "from .config import"),
    ("from nyxloom.stages import", "from .stages import"),
    ("from nyxloom.types import", "from .types import"),
)
_HEADER_END = "Pure reconcile planner (SPEC §5, §8, §9). PACKAGE P02."


def _normalize_action(action) -> tuple:
    """(class name, field mapping) -- comparable across the two modules."""
    assert is_dataclass(action), action
    return (
        type(action).__name__,
        tuple(sorted((f.name, repr(getattr(action, f.name))) for f in fields(action))),
    )


def _normalize_plan(plan) -> tuple:
    return tuple(_normalize_action(a) for a in plan)


def _breadcrumbs(plan) -> tuple:
    trace = getattr(plan, "trace", None)
    if trace is None:
        return ()
    return tuple((n.kind, n.task_id, n.detail) for n in trace.breadcrumbs)


def _is_subsequence(needle: tuple, haystack: tuple) -> bool:
    it = iter(haystack)
    return all(item in it for item in needle)


def compare(inp) -> list[str]:
    """Plan `inp` with both planners; return a list of human-readable deltas.

    Empty list means zero delta. Returning the deltas rather than asserting
    lets the mutation test below prove this function can actually SEE a
    difference -- a comparator nobody has watched fail is not a comparator.

    CR-06b: the legacy plan is first put through the DECLARED repairs (see
    `KNOWN_DIVERGENCES`) and the comparison against the result is still exact
    equality. That is deliberately not a tolerance: each repair is a MODEL of
    the change, applied to the baseline, so a behavioural difference the model
    does not produce still fails. A comparator that merely ignored the
    differing region would be the blanket exemption this mechanism exists to
    avoid.
    """
    old = legacy_planner.plan_project(inp)
    new = reconcile.plan_project(inp)
    deltas: list[str] = []
    repaired, problems = apply_declared_repairs(list(old), list(new), inp)
    deltas.extend(problems)
    old_actions, new_actions = _normalize_plan(repaired), _normalize_plan(new)
    if old_actions != new_actions:
        only_old = [a for a in old_actions if a not in new_actions]
        only_new = [a for a in new_actions if a not in old_actions]
        deltas.append(
            f"actions differ: only-legacy={only_old!r} only-new={only_new!r} "
            f"(order: legacy={[a[0] for a in old_actions]} new={[a[0] for a in new_actions]})"
        )
    old_trace, new_trace = _breadcrumbs(old), _breadcrumbs(new)
    old_trace, trace_problems = apply_declared_breadcrumb_repairs(old_trace, new_trace, inp)
    deltas.extend(trace_problems)
    if not _is_subsequence(old_trace, new_trace):
        missing = [n for n in old_trace if n not in new_trace]
        deltas.append(f"trace lost breadcrumbs: {missing!r} (new={new_trace!r})")
    return deltas


# ---------------------------------------------------------------------------
# the baseline and the corpus are what they claim to be


def test_legacy_baseline_is_the_committed_branch_point():
    """Undoing the two declared edits must reproduce the committed blob.

    Without this the baseline is only as trustworthy as the promise that
    nobody edited it, and the cheapest way past a failing differential would
    be to edit the thing it is measured against.
    """
    committed = subprocess.run(
        ["git", "show", f"{BRANCH_POINT}:{SOURCE_IN_TREE}"],
        cwd=REPO.parent, capture_output=True, text=True, check=True,
    ).stdout
    frozen = LEGACY_PATH.read_text(encoding="utf-8")
    head, _, rest = frozen.partition(_HEADER_END)
    assert head.startswith('"""FROZEN LEGACY PLANNER'), "frozen header missing"
    restored = _HEADER_END + rest
    for frozen_text, original_text in _FROZEN_EDITS:
        assert frozen_text in restored, f"declared edit not present: {frozen_text}"
        restored = restored.replace(frozen_text, original_text)
    assert '"""' + restored == committed, (
        "tests/legacy_planner.py is no longer a verbatim copy of "
        f"{BRANCH_POINT}:{SOURCE_IN_TREE} modulo its two declared edits"
    )


def test_corpus_is_the_recorded_historical_logs():
    """The fixture's manifest must describe the log it actually contains."""
    manifest = {row["project"]: row for row in planner_corpus.manifest()}
    assert set(manifest) == {"nyxloom", "dstdns", "topos"}
    for project, row in manifest.items():
        assert row["events"] > 0
        assert row["replayed_events"] == row["events"] - row["refused_by_projector"]
        assert row["distinct_projections"] > 0
    total = sum(r["distinct_projections"] for r in manifest.values())
    # Distinct-by-content across projects, so the whole is <= the sum.
    assert 0 < len(planner_corpus.projections()) <= total


def test_corpus_projections_cover_every_state_the_planner_branches_on():
    """Every `TaskState` must appear in SOME projection, real or synthetic.

    The earlier version of this test listed the ten states the real logs
    happened to contain and asserted those. That is a description dressed as a
    requirement: it passed while `SELF_REVIEWING` was absent, which is exactly
    how CR-06a's `self_review_dispatch` shipped an order-dependence defect
    under a green permutation test. The requirement is now the FULL enum, so a
    state nothing exercises fails here and names itself.
    """
    from nyxloom.types import TaskState
    seen = _real_states() | planner_synthetic.states_covered()
    missing = sorted(s.value for s in set(TaskState) - seen)
    assert not missing, (
        f"no projection reaches {missing} -- add a scenario to "
        "tests/planner_synthetic.py, or the rules owning those states are "
        "differentially unverified")


def _carver_status_inputs():
    """One input per `CarverStatus`, over a real projection.

    THE SECOND ENUM THE PLANNER BRANCHES ON, and the one with no coverage
    oracle. `TaskState` got one above because a state the corpus never reached
    hid a defect behind a green; `CarverStatus` is exactly the same shape --
    the carve ladder's whole slot precedence keys on it -- and FOUR of its
    seven members appear in no corpus or synthetic input at all. The declared
    profiles reach WARM, COLD and DEGRADED; `ABSENT` and `ROTATING` share
    COLD's branch, but `STARTING` and `COMPACTING` are a branch of their own
    and it is the one CR-06c changed the MECHANISM of, from a
    `carve_dispatch_planned` flag to `RuleEmitter.reserve`.

    Built here rather than in `planner_synthetic.py` because a carver session
    is an ENVIRONMENT fact, not a projection: a synthetic scenario supplies
    `states`, and `tests/corpus_profiles.py` -- which supplies environments --
    is frozen for this package. `dataclasses.replace` over a built profile is
    the way to vary it without touching either.
    """
    base = corpus_profiles.build(
        next(states for _, states in planner_corpus.projections()), "carver-warm")
    for status in CarverStatus:
        yield status, dataclasses.replace(
            base, carver_session=dataclasses.replace(base.carver_session, status=status))


def test_every_carver_status_is_differentially_verified():
    """The carve ladder's own enum, swept against the frozen baseline.

    Zero delta for every status, which is the point: CR-06c's report says the
    move is delta-free, and for two of these statuses no corpus or synthetic
    input could have told it. `STARTING` and `COMPACTING` are the reservation
    path -- the ladder consumes the pass's carve slot and plans NOTHING -- so
    "the differential is clean" was silent about the one branch whose
    implementation this package replaced.
    """
    statuses = 0
    for status, inp in _carver_status_inputs():
        found = compare(inp)
        assert not found, f"{status.value}: {found[0]}"
        statuses += 1
    assert statuses == len(set(CarverStatus)), statuses


def test_the_reservation_spends_the_slot_and_starves_every_rule_below_it():
    """What a reservation IS, asserted against its own contrast.

    A reservation is not "this rule planned nothing" -- that is the
    forgot-to-fire case `UnusedGrant` refuses. It is "a carver turn is already
    in flight for this generation, so the pass's carve authority is spent" and
    its whole observable effect is on OTHER rules: item 12, item 15, item 17
    and item 9 must all find the slot gone.

    The contrast is what makes that non-vacuous, because "no carve action" is
    also what a rule that silently broke would produce: the SAME projection
    with the session WARM plans a `CarveDispatch` from `headroom-carve`, and
    with it STARTING or COMPACTING plans none -- and the ledger says which
    rule consumed the slot and why, in the no-prose vocabulary a breadcrumb
    detail uses.
    """
    by_status = dict(_carver_status_inputs())

    def plan_with_ledger(inp):
        table = planning.rule_table()
        arbiter = planning.PlanArbiter(table)
        channels = planning.run_rules(
            planning.PlanContext.derive(inp), table, reconcile.ReconcileTrace(), arbiter)
        carves = [type(a).__name__ for a in channels.assemble()
                  if type(a).__name__ in planning.EXCLUSIVE_ACTIONS]
        return carves, arbiter.ledger()

    warm_carves, warm_ledger = plan_with_ledger(by_status[CarverStatus.WARM])
    assert warm_carves == ["CarveDispatch"], warm_carves
    assert [r.rule for r in warm_ledger] == ["headroom-carve"], warm_ledger
    assert [r.intent for r in warm_ledger] == [planning.GrantIntent.EMIT]
    assert all(r.spent for r in warm_ledger)

    for status in (CarverStatus.STARTING, CarverStatus.COMPACTING):
        carves, ledger = plan_with_ledger(by_status[status])
        assert carves == [], f"{status.value}: {carves}"
        assert [r.rule for r in ledger] == ["carver-session-ladder"], ledger
        held = ledger[0]
        assert held.intent is planning.GrantIntent.RESERVE
        assert held.spent is False
        assert held.reason == f"wait:{status.value.lower()}"
        # the reason is the breadcrumb, in the same closed vocabulary -- not a
        # second, drifting explanation of the same decision
        assert ("carver", None, held.reason) in _breadcrumbs(
            reconcile.plan_project(by_status[status]))


def test_corpus_states_are_attributed_to_their_source():
    """Real and synthetic stay distinguishable.

    The synthetic set exists only for what history could not produce. If a
    state migrates into it that the real logs DO cover, the corpus starts
    looking like ground truth it is not -- so the two are asserted disjoint
    where it matters, and the real set is pinned so it cannot quietly shrink.
    """
    from nyxloom.types import TaskState
    real = _real_states()
    synthetic_only = {
        TaskState.NEEDS_DECISION,
        TaskState.READY_TO_CARVE, TaskState.SELF_REVIEWING,
    }
    assert not (real & synthetic_only), (
        "the real logs now reach a state the synthetic set claims to be "
        f"covering for: {sorted(s.value for s in real & synthetic_only)} -- "
        "re-derive the split rather than leaving the claim stale")
    assert synthetic_only <= planner_synthetic.states_covered()
    # The real corpus is the ground truth; pin its breadth so a corpus
    # regeneration that silently lost history fails loudly.
    assert len(real) == 12, f"real corpus state breadth changed: {len(real)}"


# ---------------------------------------------------------------------------
# the differential itself


@pytest.mark.parametrize("profile", corpus_profiles.PROFILE_NAMES)
def test_historical_corpus_plans_are_identical(profile: str):
    """Every real projection, planned under one declared environment.

    "Identical" means identical to the frozen baseline with the DECLARED
    repairs applied to it -- see `compare` and `KNOWN_DIVERGENCES`. Still
    exact equality: the repairs are models that reconstruct the new plan, not
    regions the comparison agrees to skip.
    """
    deltas: list[str] = []
    for case_id, states in planner_corpus.projections():
        found = compare(corpus_profiles.build(states, profile))
        if found:
            deltas.append(f"{case_id}/{profile}: {found[0]}")
            if len(deltas) >= 5:
                break
    assert not deltas, "planner differential delta:\n" + "\n".join(deltas)


# ---------------------------------------------------------------------------
# the declared divergences
#
# The amendment permits a difference "explained in the package report". An
# entry below is that explanation, kept next to the check rather than in prose
# nobody runs -- and it buys NOTHING on its own. A divergence comes in one of
# two shapes and each is closed differently:
#
#   SCENARIO-SCOPED -- the difference exists in exactly one hand-built
#     projection. That scenario is excluded from the general comparison and
#     pinned EXACTLY by its own test.
#
#   REPAIR-SCOPED -- the difference is spread across the historical corpus, so
#     there is no scenario to exclude. Excluding a case per occurrence would
#     be a blanket exemption by instalments, so instead the repair is MODELLED:
#     a function applies it to the legacy plan, and the comparison stays exact
#     equality against the result. The model is what makes this safe -- a
#     behavioural change the model does not produce still fails, because the
#     model reconstructs the new plan rather than agreeing to overlook part of
#     it.
#
# Either way an entry fails if the divergence changes shape, and fails if it
# DISAPPEARS -- which would mean the repair was reverted and the entry is now
# a permanent excuse for nothing.


@dataclasses.dataclass(frozen=True)
class Divergence:
    """One deliberate difference between the new planner and the baseline."""

    #: The package that introduced it, and why it is not a defect.
    why: str
    #: The test that pins it exactly. Named here so an entry cannot be added
    #: without one -- `test_every_declared_divergence_has_a_pin_and_a_shape`
    #: fails if this names a test that does not exist.
    pin: str
    #: For a scenario-scoped divergence: the `planner_synthetic` scenario.
    scenario: str | None = None
    #: For a repair-scoped divergence: `(legacy_actions, new_actions, inp) ->
    #: (repaired_legacy_actions, problems)`. A non-empty `problems` list is a
    #: delta in its own right: it means the observed difference is NOT the one
    #: this entry claims.
    repair: Callable[[list, list, object], tuple[list, list[str]]] | None = None
    #: Optional companion to `repair`, for a divergence that also changes
    #: which breadcrumbs get emitted (a reordering repair never needs this --
    #: the same breadcrumbs still appear in both traces; a DELETION repair
    #: does, since the dropped action's own breadcrumb note is dropped too).
    #: `(legacy_breadcrumbs, new_breadcrumbs, inp) -> (repaired_legacy_
    #: breadcrumbs, problems)`, same non-empty-problems-is-a-delta contract
    #: as `repair`.
    breadcrumb_repair: Callable[[tuple, tuple, object], tuple[tuple, list[str]]] | None = None


#: Actions the attempt ladder can plan, taken from the rule's own declaration
#: (which `tests/test_planning.py` derives from its call graph), so the
#: divergence model below cannot quietly widen to cover another channel.
_ATTEMPT_LADDER_EMITS = frozenset(
    next(s for s in planning.rule_table() if s.name == "attempt-ladder").emits)


def _task_id_of(normalized: tuple) -> str:
    return dict(normalized[1])["task_id"]


def _retie_the_warm_review_session(old: list, new: list, inp) -> tuple[list, list[str]]:
    """Model CR-06b divergence (b): the `resume_session` tie-break.

    The legacy rule takes `max(priors, key=lambda a: a.started)`, and `max`
    returns the FIRST maximal element, so a tie is resolved by whatever order
    the snapshot map happened to have. The repair breaks the tie on
    `attempt_id`.

    The model RECOMPUTES the expected handle from the snapshot and, crucially,
    asserts that the handle the baseline chose was itself `started`-maximal.
    That is the whole safety claim: the tie-break only ever discriminates
    among candidates the policy already calls equally warm. A repair that
    reached PAST a tie to a genuinely older session would be a behavioural
    change, and it would be reported here rather than absorbed.
    """
    priors = [
        a for tsf in inp.states.values() for a in tsf.attempts
        if a.role == Role.REVIEW_INDEPENDENT
        and a.state == AttemptState.EXITED
        and a.session_handle
    ]
    if not priors:
        return old, []
    best = max(priors, key=lambda a: (a.started, a.attempt_id))
    newest = max(a.started for a in priors)
    problems: list[str] = []
    repaired: list = []
    for action in old:
        if type(action).__name__ != "LaunchReview" or action.resume_session is None:
            repaired.append(action)
            continue
        chosen = [p for p in priors if p.session_handle == action.resume_session]
        if not chosen or all(p.started != newest for p in chosen):
            problems.append(
                f"resume_session {action.resume_session!r} is not a "
                f"`started`-maximal prior review session -- the declared "
                f"tie-break does not explain this difference")
            repaired.append(action)
            continue
        repaired.append(dataclasses.replace(action, resume_session=best.session_handle))
    return repaired, problems


def _sort_the_attempt_run(old: list, new: list, inp) -> tuple[list, list[str]]:
    """Model CR-06b divergence (a): the attempt ladder walks tasks sorted.

    The ladder emits a contiguous BLOCK of actions per task, in one contiguous
    run of the plan (its own channel). Sorting the outer loop therefore
    permutes whole blocks and never touches what is inside one -- so the model
    is a STABLE SORT BY TASK ID of the run, which reproduces the new plan
    exactly or not at all.

    The run is located as the minimal window where the two plans differ (the
    maximal common prefix and suffix are peeled off). That cannot be laxity:
    the assertion the caller then makes is full equality against the
    reconstruction, so the window only decides WHERE the sort is applied, not
    what counts as agreement. Three claims are checked here rather than left
    implicit, because they are what makes this a reordering and not a change:

    * every action in the window is one the attempt ladder can emit -- so a
      delta in some other channel is reported, never sorted away;
    * each task's actions are CONTIGUOUS in the window -- the block structure
      the sort relies on;
    * the window's multiset is unchanged -- nothing gained, nothing lost.
    """
    old_norm, new_norm = _normalize_plan(old), _normalize_plan(new)
    if old_norm == new_norm:
        return old, []
    prefix = 0
    while (prefix < len(old_norm) and prefix < len(new_norm)
           and old_norm[prefix] == new_norm[prefix]):
        prefix += 1
    suffix = 0
    while (suffix < len(old_norm) - prefix and suffix < len(new_norm) - prefix
           and old_norm[len(old_norm) - 1 - suffix] == new_norm[len(new_norm) - 1 - suffix]):
        suffix += 1
    window = old[prefix:len(old) - suffix]
    window_norm = old_norm[prefix:len(old_norm) - suffix]
    target_norm = new_norm[prefix:len(new_norm) - suffix]

    foreign = sorted({k for k, _ in window_norm} - _ATTEMPT_LADDER_EMITS)
    if foreign:
        return old, [
            f"the differing window contains {foreign}, which the attempt "
            f"ladder cannot emit -- this is not the declared reordering"]
    task_ids = [_task_id_of(a) for a in window_norm]
    if not _blocks_are_contiguous(task_ids):
        return old, [
            f"the differing window interleaves tasks {task_ids} -- the ladder "
            f"emits one contiguous block per task, so this is not a block "
            f"reordering"]
    if sorted(window_norm) != sorted(target_norm):
        return old, [
            "the differing window gained or lost an action -- it is a "
            "behavioural change, not a reordering"]
    ordered = sorted(window, key=lambda a: a.task_id)
    return old[:prefix] + ordered + old[len(old) - suffix:], []


def _runs(values: list[str]):
    """The consecutive runs of equal values, as a list of values."""
    seen: list[str] = []
    for value in values:
        if not seen or seen[-1] != value:
            seen.append(value)
    return seen


def _blocks_are_contiguous(task_ids: list[str]) -> bool:
    runs = _runs(task_ids)
    return len(runs) == len(set(runs))


def _is_reasonless_needs_decision_park(task_id: str, inp) -> bool:
    """True when `task_id` is CURRENTLY NEEDS_DECISION with no D-dep
    declared at all -- the exact shape rules_lifecycle.decision_hold's
    fixed release condition now refuses to auto-release (see
    `_drop_reason_blind_decision_hold_releases`'s own docstring for why)."""
    tsf = inp.states.get(task_id)
    if tsf is None or tsf.state is not TaskState.NEEDS_DECISION:
        return False
    fm_entry = inp.frontmatters.get(task_id)
    if fm_entry is None:
        return False
    fm, _relpath = fm_entry
    return not fm.decision_deps()


def _drop_reason_blind_decision_hold_releases(old: list, new: list, inp) -> tuple[list, list[str]]:
    """Model the decision_hold release-blindness fix (2026-08-04, found
    scoping CR-11b): the legacy planner releases ANY NEEDS_DECISION task
    with no currently-open D-dep straight back to QUEUED -- including one
    that never declared a D-dep at all (rules_lifecycle.reject_triage's
    "product"/architectural-no-carve/attempts-exhausted-no-carve
    escalations never mint one). The fixed planner requires the task to
    have genuinely declared at least one D-dep before releasing, so a
    reason-less park now stays parked.

    This is a DELETION, not a reordering: the fix removes an action, it
    does not move one. The model is correspondingly narrow -- it drops
    ONLY a `Transition(to=QUEUED)` whose target task is CURRENTLY
    NEEDS_DECISION in `inp.states` AND whose frontmatter declares NO D-dep
    at all (`fm.decision_deps() == []`), and reports every OTHER
    NEEDS_DECISION->QUEUED transition it finds as a problem instead of
    silently keeping it -- so a real, unrelated release-path regression
    (e.g. the genuine "D-dep closed" case breaking) is reported, never
    absorbed by this entry. See `_drop_reason_blind_decision_hold_
    breadcrumbs` for this deletion's trace-level companion -- dropping the
    action alone leaves its `state-transition` breadcrumb note stranded in
    the legacy trace with nothing to match in the new one.
    """
    problems: list[str] = []
    repaired: list = []
    for action in old:
        if not (type(action).__name__ == "Transition"
                and action.to is TaskState.QUEUED):
            repaired.append(action)
            continue
        tsf = inp.states.get(action.task_id)
        if tsf is None or tsf.state is not TaskState.NEEDS_DECISION:
            repaired.append(action)
            continue
        if not _is_reasonless_needs_decision_park(action.task_id, inp):
            fm, _ = inp.frontmatters.get(action.task_id, (None, None))
            deps = fm.decision_deps() if fm is not None else None
            problems.append(
                f"{action.task_id}: legacy plans NEEDS_DECISION->QUEUED for a "
                f"task that DOES declare a D-dep ({deps!r}) -- the declared "
                f"repair only models the reason-less (no D-dep) release, so "
                f"this is a different divergence, not this one")
            repaired.append(action)
            continue
        # modeled divergence: a reason-less NEEDS_DECISION->QUEUED release --
        # the fixed planner never emits it, so it is dropped, not kept.
    return repaired, problems


def _drop_reason_blind_decision_hold_breadcrumbs(
        old_trace: tuple, new_trace: tuple, inp) -> tuple[tuple, list[str]]:
    """The trace-level companion to `_drop_reason_blind_decision_hold_
    releases`: the legacy trace carries a `('state-transition', task_id,
    'NEEDS_DECISION->QUEUED')` breadcrumb for every action that repair
    drops -- the fixed planner never plans the transition, so it never
    emits the note either. Drops ONLY that exact breadcrumb shape for a
    task `_is_reasonless_needs_decision_park` confirms is this divergence's
    shape; any OTHER breadcrumb naming the same phrase for a task that is
    NOT a reason-less park is reported, never silently dropped."""
    problems: list[str] = []
    repaired: list = []
    for crumb in old_trace:
        kind, task_id, detail = crumb
        if kind == "state-transition" and detail == "NEEDS_DECISION->QUEUED":
            if _is_reasonless_needs_decision_park(task_id, inp):
                continue  # modeled divergence -- drop it, not keep it
            problems.append(
                f"{task_id}: legacy trace carries a NEEDS_DECISION->QUEUED "
                f"breadcrumb for a task that is not a reason-less park -- "
                f"outside this model's claimed shape")
        repaired.append(crumb)
    return tuple(repaired), problems


KNOWN_DIVERGENCES: dict[str, Divergence] = {
    "self-reviewing-pair": Divergence(
        scenario="self-reviewing-pair",
        pin="test_the_self_review_divergence_is_exactly_the_repair_it_claims_to_be",
        why=(
            "CR-06a review (8a6073a8): `self_review_dispatch` iterated the "
            "snapshot map raw, so two SELF_REVIEWING tasks launched in "
            "insertion order. Repaired to sort by task id, matching contract "
            "item 3's determinism promise and every other rule. The repair was "
            "accepted on the argument that it moved zero projections -- true "
            "only because the HISTORICAL corpus contains no SELF_REVIEWING "
            "task at all, which is the blind spot this synthetic set exists "
            "to close."),
    ),
    # ORDER MATTERS here too: this DELETION must run BEFORE
    # attempt-ladder-sorted-task-order's window-based reorder repair. That
    # repair locates its window as the maximal common prefix/suffix of the
    # two (partially repaired) plans and assumes the window's MULTISET is
    # unchanged (a pure reorder) -- an unrepaired reason-less
    # NEEDS_DECISION->QUEUED action sitting inside that window would make
    # the two plans differ by COUNT, not just order, and the ladder repair
    # would correctly refuse to touch it ("gained or lost an action"). The
    # LIFECYCLE-channel deletion this repair performs is otherwise
    # independent of the WAVE/ATTEMPT-channel repairs below.
    "decision-hold-reason-blind-release": Divergence(
        repair=_drop_reason_blind_decision_hold_releases,
        breadcrumb_repair=_drop_reason_blind_decision_hold_breadcrumbs,
        pin="test_the_decision_hold_divergence_only_ever_drops_a_reasonless_release",
        why=(
            "2026-08-04 (found scoping CR-11b): rules_lifecycle.decision_hold's "
            "release condition was `NEEDS_DECISION and not open_d_deps -> "
            "QUEUED` -- vacuously true both when a task's D-deps are now all "
            "resolved AND when the task never declared a D-dep at all. "
            "fm.decision_deps() is static frontmatter, never mutated by any "
            "transition, so a task with none declared could never have been "
            "parked by decision_hold's OWN entry branch (which requires "
            "open_d_deps non-empty to fire) -- it must have arrived via "
            "reject_triage's human-judgment escalations (product / "
            "architectural-no-carve / attempts-exhausted-no-carve), none of "
            "which mint a D-dep. The old condition silently reversed every "
            "one of them on the VERY NEXT reconcile pass, re-dispatching work "
            "a reviewer explicitly said needed a human decision, with no "
            "operator-visible event and no D-NNN decision for an operator to "
            "act on even if they noticed. Confirmed empirically with a "
            "two-pass repro before the fix. Release now requires "
            "fm.decision_deps() to be non-empty -- a reason-less park stays "
            "parked, correctly requiring an operator to act rather than "
            "being silently, mechanically undone. This is a real correctness "
            "fix, not a decomposition artifact; it is declared here (a "
            "DELETION model, not a reordering) rather than folded into a "
            "scenario exemption, so the general comparison keeps checking "
            "everything else the way it always has."),
    ),
    # ORDER MATTERS, and only here. The resume-session repair must run FIRST:
    # `LaunchReview` sits in the WAVE channel, AFTER the attempt run, so an
    # unrepaired `resume_session` would truncate the common suffix and drag
    # wave actions into the attempt window -- where the "foreign action" check
    # would correctly refuse to sort them.
    "review-resume-tie-break": Divergence(
        repair=_retie_the_warm_review_session,
        pin="test_the_review_resume_divergence_only_ever_breaks_a_tie",
        why=(
            "CR-06b closes the second permutation exemption CR-06a recorded. "
            "`LaunchReview.resume_session` was `max(priors, key=started)`, and "
            "`max` returns the FIRST maximal element, so two EXITED review "
            "attempts sharing a `started` timestamp made the resumed warm "
            "session a function of `inp.states` iteration order -- which the "
            "daemon builds by scanning a directory. Unlike the ladder's "
            "reordering this changes a FIELD VALUE, so it is argued rather "
            "than waved through: the tie-break discriminates ONLY among "
            "candidates the rule itself calls equally warm, the wrong guess "
            "was never worse than a prompt-cache miss (the pre-B6 cost), and "
            "the A7 verdict-attempt binding is stamped downstream either way. "
            "Its pin checks the 'only among ties' claim directly."),
    ),
    "attempt-ladder-sorted-task-order": Divergence(
        repair=_sort_the_attempt_run,
        pin="test_the_attempt_ladder_divergence_is_exactly_a_block_reordering",
        why=(
            "CR-06b closes the first permutation exemption CR-06a recorded. "
            "The attempt ladder walked `inp.states.items()` RAW where every "
            "other rule sorts, against contract item 3's own 'sorted task-id "
            "order (determinism)' promise, so the ATTEMPT channel's action "
            "order followed the daemon's directory scan. CR-06a deferred the "
            "repair because it moves real corpus plans and 'that is not a "
            "delta to explain in a report, it is a different planner'. That "
            "argument was about VOLUME. The delta is a permutation of whole "
            "per-task blocks within ONE channel: the same actions, the same "
            "fields, the same decisions -- which is what the model here "
            "reconstructs and what its pin asserts."),
    ),
}


def apply_declared_repairs(old: list, new: list, inp) -> tuple[list, list[str]]:
    """Put the legacy plan through every declared repair, in declared order."""
    problems: list[str] = []
    for name, divergence in KNOWN_DIVERGENCES.items():
        if divergence.repair is None:
            continue
        old, found = divergence.repair(old, new, inp)
        problems.extend(f"{name}: {p}" for p in found)
    return old, problems


def apply_declared_breadcrumb_repairs(old_trace: tuple, new_trace: tuple,
                                      inp) -> tuple[tuple, list[str]]:
    """Put the legacy trace through every declared breadcrumb repair, in
    declared order -- the trace-level companion to `apply_declared_repairs`,
    needed only by a divergence that DELETES an action (a reordering
    divergence's breadcrumbs still all appear in both traces unchanged)."""
    problems: list[str] = []
    for name, divergence in KNOWN_DIVERGENCES.items():
        if divergence.breadcrumb_repair is None:
            continue
        old_trace, found = divergence.breadcrumb_repair(old_trace, new_trace, inp)
        problems.extend(f"{name}: {p}" for p in found)
    return old_trace, problems


@pytest.mark.parametrize("profile", corpus_profiles.PROFILE_NAMES)
def test_synthetic_corpus_plans_are_identical(profile: str):
    """The states real history never reached, under every profile."""
    deltas: list[str] = []
    for case_id, states in planner_synthetic.projections():
        if case_id in {d.scenario for d in KNOWN_DIVERGENCES.values()}:
            continue
        found = compare(corpus_profiles.build(states, profile))
        if found:
            deltas.append(f"{case_id}/{profile}: {found[0]}")
    assert not deltas, "planner differential delta:\n" + "\n".join(deltas)


def test_every_declared_divergence_has_a_pin_and_a_shape():
    """An entry with no pinning test is exactly the blanket exemption this
    mechanism exists to refuse, and it would be invisible: the general
    comparison would go green and nothing would say why.

    CR-06b REVIEW: `pin in globals()` did not achieve that. It refused a
    MISSPELLED pin, which is a typo check; it accepted an entry that named any
    other test in this module, which is the blanket exemption itself wearing a
    name -- verified by pointing an entry at
    `test_corpus_is_the_recorded_historical_logs` and watching it pass. Three
    conditions replace it, and together they mean "pinned" cannot be claimed
    without wiring:

    * the pin must be a callable TEST, so it is collected and actually runs;
    * its source must NAME this divergence's own machinery -- the synthetic
      scenario for a scenario-scoped entry, the repair function for a
      repair-scoped one. A test that never mentions the thing cannot be
      failing when that thing changes shape;
    * no two entries may share a pin, so a second divergence cannot shelter
      under the first one's test.
    """
    for name, divergence in KNOWN_DIVERGENCES.items():
        assert divergence.why, name
        assert (divergence.scenario is None) != (divergence.repair is None), (
            f"{name}: a divergence is scenario-scoped OR repair-scoped, never "
            f"both and never neither")
        if divergence.scenario is not None:
            assert divergence.scenario in planner_synthetic.SCENARIO_NAMES, (
                f"{name}: declared for a scenario that no longer exists")
        pin = globals().get(divergence.pin)
        assert callable(pin) and divergence.pin.startswith("test_"), (
            f"{name}: names a pinning test {divergence.pin!r} that does not "
            f"exist -- an unpinned divergence is a blanket exemption")
        anchor = divergence.scenario or divergence.repair.__name__
        assert anchor in inspect.getsource(pin), (
            f"{name}: its pin {divergence.pin!r} never mentions {anchor!r}, so "
            f"it is not pinning THIS divergence -- naming an unrelated test "
            f"is the blanket exemption with a citation attached")

    pins = [divergence.pin for divergence in KNOWN_DIVERGENCES.values()]
    assert len(set(pins)) == len(pins), (
        f"two divergences share a pinning test: {sorted(pins)} -- the second "
        f"is sheltering under the first one's evidence")


def test_the_self_review_divergence_is_exactly_the_repair_it_claims_to_be():
    """Pin the scenario-scoped difference, in both directions.

    Asserting three things, so the exemption cannot widen into cover:
    nothing was gained or lost (multiset equality); the NEW plan is sorted;
    and the LEGACY plan is NOT -- because if the baseline ever agreed, the
    divergence would not exist and the entry should be retired rather than
    left standing as a permanent excuse.
    """
    states = dict(next(s for n, s in planner_synthetic.projections()
                       if n == "self-reviewing-pair"))
    inp = corpus_profiles.build(states, "neutral")
    old, new = legacy_planner.plan_project(inp), reconcile.plan_project(inp)

    assert sorted(_normalize_plan(old)) == sorted(_normalize_plan(new)), (
        "the divergence gained or lost an action -- it is not an ordering "
        "repair, and the exemption does not cover it")

    def launches(plan):
        return [a.task_id for a in plan if type(a).__name__ == "LaunchSelfReview"]

    assert len(launches(new)) == 2, "scenario no longer exercises two launches"
    assert launches(new) == sorted(launches(new)), "the repair is not in effect"
    assert launches(old) != sorted(launches(old)), (
        "the frozen baseline now agrees -- retire this divergence entry")


def test_the_attempt_ladder_divergence_is_exactly_a_block_reordering():
    """Pin the repair-scoped divergence (a) across the HISTORICAL corpus.

    Four claims, per differing projection, in the order that matters:

    1. the whole plan's multiset is unchanged -- nothing gained, nothing lost,
       so it is not a decision that changed;
    2. the difference is confined to actions the attempt LADDER can emit, so a
       delta somewhere else could never be absorbed by this entry;
    3. each task's actions stay contiguous, and the new plan is EXACTLY the
       stable sort of the legacy run by task id -- reconstructed, not merely
       tolerated;
    4. it still happens. A count, because a repair that was reverted would
       make every claim above vacuously true and this test green.

    The companion repair (the `resume_session` tie-break) is applied first,
    for the same reason `compare` applies it first: an unrepaired warm-session
    handle sits AFTER the attempt run and would drag wave actions into the
    window, where claim 2 would correctly refuse them.
    """
    moved = 0
    for case_id, states in planner_corpus.projections():
        inp = corpus_profiles.build(states, "neutral")
        old = list(legacy_planner.plan_project(inp))
        new = list(reconcile.plan_project(inp))
        old, problems = _retie_the_warm_review_session(old, new, inp)
        assert not problems, f"{case_id}: {problems}"
        if _normalize_plan(old) == _normalize_plan(new):
            continue
        moved += 1
        assert sorted(_normalize_plan(old)) == sorted(_normalize_plan(new)), (
            f"{case_id}: the delta gained or lost an action -- it is a "
            f"behavioural change, not a reordering")
        repaired, problems = _sort_the_attempt_run(old, new, inp)
        assert not problems, f"{case_id}: {problems}"
        assert _normalize_plan(repaired) == _normalize_plan(new), (
            f"{case_id}: sorting the legacy attempt run by task id does not "
            f"reproduce the new plan -- the ladder changed more than its "
            f"iteration order")
    assert moved >= 150, (
        f"only {moved} of {len(planner_corpus.projections())} projections "
        f"reorder -- CR-06b measured 199 under this profile, so either the "
        f"repair was reverted or the corpus no longer exercises it")


def test_the_decision_hold_divergence_only_ever_drops_a_reasonless_release():
    """Pin the repair-scoped decision-hold divergence, in both directions,
    across every environment profile the "needs-decision-pair" scenario is
    built under (its own comment claims the 'decisions-open' profile
    differs from the rest -- irrelevant here, since NEITHER task ever
    declares a D-dep, so the legacy planner released both under EVERY
    profile including 'decisions-open': `open_d_deps` was always
    vacuously empty regardless of `decisions_open`'s contents).

    Four claims, so the exemption cannot widen into cover:
    1. the legacy (unrepaired) plan genuinely disagrees with the new one --
       if it now agreed, the divergence would be stale and should be
       retired rather than left standing as a permanent excuse;
    2. the ONLY difference is a subset relationship (new is legacy minus
       some actions) -- never a reorder, never a gained action;
    3. every action legacy has and new lacks is a NEEDS_DECISION->QUEUED
       Transition for a task with an empty fm.decision_deps() -- the exact
       and only shape this model claims to drop;
    4. applying the repair reproduces the new plan exactly, with zero
       reported problems.
    """
    states = dict(next(s for n, s in planner_synthetic.projections()
                       if n == "needs-decision-pair"))
    checked = 0
    trace_saw_a_repair = False
    for profile in corpus_profiles.PROFILE_NAMES:
        inp = corpus_profiles.build(states, profile)
        # NOT list()-wrapped here -- _breadcrumbs below needs the original
        # PlanResult's `.trace` attribute, which a plain `list(...)` cast
        # would strip. `_drop_reason_blind_decision_hold_releases` only
        # needs `old`/`new` to be iterable, so the PlanResult itself (list-
        # like by its own docstring) works directly.
        old = legacy_planner.plan_project(inp)
        new = reconcile.plan_project(inp)

        old_norm, new_norm = _normalize_plan(old), _normalize_plan(new)
        assert old_norm != new_norm, (
            f"{profile}: the frozen baseline now agrees -- retire this "
            f"divergence entry")

        only_legacy = [a for a in old if _normalize_action(a) not in new_norm]
        only_new = [a for a in new if _normalize_action(a) not in old_norm]
        assert not only_new, (
            f"{profile}: the new plan has actions the legacy plan lacks "
            f"({only_new!r}) -- this is not a pure deletion, the exemption "
            f"does not cover it")
        assert only_legacy, f"{profile}: expected legacy to plan >=1 extra action"
        for action in only_legacy:
            assert type(action).__name__ == "Transition" and action.to is TaskState.QUEUED, (
                f"{profile}: {action!r} is not a NEEDS_DECISION->QUEUED "
                f"Transition -- outside this model's claimed shape")
            tsf = inp.states[action.task_id]
            fm, _ = inp.frontmatters[action.task_id]
            assert tsf.state is TaskState.NEEDS_DECISION and not fm.decision_deps(), (
                f"{profile}: {action.task_id} is not a reason-less "
                f"NEEDS_DECISION park -- outside this model's claimed shape")

        repaired, problems = _drop_reason_blind_decision_hold_releases(old, new, inp)
        assert not problems, f"{profile}: {problems}"
        assert _normalize_plan(repaired) == new_norm, (
            f"{profile}: dropping the reason-less releases does not "
            f"reproduce the new plan exactly")

        old_trace, new_trace = _breadcrumbs(old), _breadcrumbs(new)
        repaired_trace, trace_problems = _drop_reason_blind_decision_hold_breadcrumbs(
            old_trace, new_trace, inp)
        assert not trace_problems, f"{profile}: {trace_problems}"
        assert _is_subsequence(repaired_trace, new_trace), (
            f"{profile}: dropping the reason-less release breadcrumbs does "
            f"not make the legacy trace a subsequence of the new one")
        if old_trace:
            trace_saw_a_repair = trace_saw_a_repair or len(repaired_trace) < len(old_trace)
        checked += 1
    assert checked == len(corpus_profiles.PROFILE_NAMES)
    assert trace_saw_a_repair, (
        "no profile's trace ever needed the breadcrumb repair -- the "
        "frozen baseline's trace now agrees, retire breadcrumb_repair")


def test_the_review_resume_divergence_only_ever_breaks_a_tie():
    """Pin the repair-scoped divergence (b) across the HISTORICAL corpus.

    This one changes a FIELD VALUE rather than an order, so the pin has to say
    more than "a multiset is preserved". What makes it safe is that the
    tie-break never REACHES past a tie: both the handle the baseline chose and
    the handle the repair chooses must be `started`-maximal -- the rule's own
    definition of "the warmest prior session" -- and the new one must be the
    deterministic maximum. Everything else about the action, and every other
    action in the plan, must be identical.

    If a future change ever made the new planner prefer a strictly OLDER
    session, that is a behavioural change and it fails here rather than being
    read as "the known tie-break".
    """
    observed = 0
    for case_id, states in planner_corpus.projections():
        inp = corpus_profiles.build(states, "neutral")
        old = list(legacy_planner.plan_project(inp))
        new = list(reconcile.plan_project(inp))
        priors = [
            a for tsf in inp.states.values() for a in tsf.attempts
            if a.role == Role.REVIEW_INDEPENDENT
            and a.state == AttemptState.EXITED and a.session_handle
        ]
        old_waves = [a for a in old if type(a).__name__ == "LaunchReview"]
        new_waves = [a for a in new if type(a).__name__ == "LaunchReview"]
        assert len(old_waves) == len(new_waves), case_id

        # CR-06b review: the pin also checks the MODEL `compare` relies on,
        # not only the planner. `_retie_the_warm_review_session` applied to
        # the baseline must reproduce the handles the new planner chose --
        # otherwise the model and the behaviour it claims to describe have
        # drifted apart, and every green above is measured against the wrong
        # reconstruction. Outside the tie branch on purpose: it must also be a
        # no-op wherever there is no tie to break.
        retied, model_problems = _retie_the_warm_review_session(
            list(old), list(new), inp)
        assert not model_problems, f"{case_id}: {model_problems}"
        assert ([a.resume_session for a in retied
                 if type(a).__name__ == "LaunchReview"]
                == [a.resume_session for a in new_waves]), (
            f"{case_id}: the declared tie-break MODEL does not reproduce the "
            f"planner it describes")
        for before, after in zip(old_waves, new_waves):
            assert before.wave_id == after.wave_id, case_id
            assert before.task_ids == after.task_ids, case_id
            if before.resume_session == after.resume_session:
                continue
            observed += 1
            newest = max(a.started for a in priors)
            chosen_before = [p for p in priors
                             if p.session_handle == before.resume_session]
            chosen_after = [p for p in priors
                            if p.session_handle == after.resume_session]
            assert any(p.started == newest for p in chosen_before), (
                f"{case_id}: the BASELINE resumed a session that was not "
                f"`started`-maximal -- this is not a tie")
            assert any(p.started == newest for p in chosen_after), (
                f"{case_id}: the repair reached PAST a tie to an older "
                f"session -- that is a behavioural change, not a tie-break")
            assert after.resume_session == max(
                priors, key=lambda a: (a.started, a.attempt_id)).session_handle, (
                f"{case_id}: the repair is not the declared "
                f"(started, attempt_id) maximum")
    assert observed >= 20, (
        f"only {observed} corpus projections resolve a warm-session tie -- "
        f"CR-06b measured 26 under this profile, so either the repair was "
        f"reverted or the corpus no longer ties two review sessions")

    # And the hand-built case, where the tie is exactly two candidates so the
    # choice is readable rather than inferred. `planner_synthetic`'s original
    # tie scenario RECORDED the ambiguity without exercising it -- both its
    # tasks had a review attempt as their LATEST, so the recency guard
    # suppressed every launch and no `resume_session` was ever read.
    states = dict(next(s for n, s in planner_synthetic.projections()
                       if n == "review-resume-tie-reaches-a-launch"))
    inp = corpus_profiles.build(states, "neutral")

    def handles(plan):
        return [a.resume_session for a in plan
                if type(a).__name__ == "LaunchReview"]

    assert handles(legacy_planner.plan_project(inp)) == ["h1"], (
        "the frozen baseline no longer resumes the map-order handle -- "
        "retire this divergence entry")
    assert handles(reconcile.plan_project(inp)) == ["h2"], (
        "the tie-break is not choosing the (started, attempt_id) maximum")


def _corpus_plans():
    for case_id, states in planner_corpus.projections():
        inp = corpus_profiles.build(states, "neutral")
        yield (case_id, inp,
               list(legacy_planner.plan_project(inp)),
               list(reconcile.plan_project(inp)))


@lru_cache(maxsize=1)
def _corpus_case_that_diverges():
    """A real projection the two planners genuinely disagree about, and a
    NON-DEGENERATE one.

    `next` with no default on purpose: if the corpus stops producing one, the
    controls below are testing nothing and should blow up rather than skip.

    The three extra conditions are the CR-06b review's finding, and they are
    not fastidiousness. The first diverging projection in the corpus
    (`dstdns-179`) plans exactly TWO actions, both `InterruptAttempt`,
    differing only in order -- so every mutation below changed the action
    multiset, every one of them was refused by the multiset guard, and the
    foreign-class and contiguity guards were never reached at all. Four
    controls that read as four independent probes were one probe run four
    times, and deleting the foreign-class guard would not have failed any of
    them. Requiring several actions, several tasks, and at least one action
    the ladder CANNOT emit puts the other guards back inside this control's
    reach.
    """
    return next(
        case for case in _corpus_plans()
        if _normalize_plan(case[2]) != _normalize_plan(case[3])
        and len(case[2]) >= 4
        and len({a.task_id for a in case[2] if hasattr(a, "task_id")}) >= 2
        and {type(a).__name__ for a in case[2]} - _ATTEMPT_LADDER_EMITS)


@pytest.mark.parametrize("mutate, why", [
    (lambda plan: plan[:-1], "an action was dropped"),
    (lambda plan: plan + [reconcile.StallCheck(task_id="zzz", attempt_id="a")],
     "an action was invented"),
    (lambda plan: [dataclasses.replace(a, task_id="zzz") if i == 0 else a
                   for i, a in enumerate(plan)], "a field changed"),
    (lambda plan: [reconcile.OpenWave(task_ids=["zzz"])] + plan[1:],
     "an action from another channel replaced one"),
    (lambda plan: list(reversed(plan)),
     "the whole plan was reordered, multiset intact"),
])
def test_the_declared_repairs_cannot_absorb_a_foreign_delta(mutate, why):
    """The anti-decoration control for the repair mechanism itself.

    A model of a repair is only safe if it REFUSES everything that is not that
    repair. Each mutation here is a genuine behavioural difference dressed in
    the same clothes -- same channel, same action classes -- and each must
    still be reported, or `KNOWN_DIVERGENCES` has become the blanket exemption
    it is written to avoid.

    The last mutation is the CR-06b review's addition, and it is the only one
    the MULTISET guard cannot refuse: the other four all add, drop or rewrite
    an action, so they die on the cheapest check and never exercise the
    others. A whole-plan reversal keeps every action and every field, which is
    what a genuine reordering defect would look like -- and it drags actions
    from other channels into the differing window, where the foreign-class
    guard is what has to refuse them. Paired with the non-degenerate case
    `_corpus_case_that_diverges` now insists on; either alone is not enough.
    """
    case_id, inp, old, new = _corpus_case_that_diverges()
    mutated = mutate(list(new))
    repaired, problems = apply_declared_repairs(list(old), mutated, inp)
    assert problems or _normalize_plan(repaired) != _normalize_plan(mutated), (
        f"{case_id}: the declared repairs absorbed a foreign delta ({why})")


def test_the_repair_model_refuses_a_run_it_cannot_explain():
    """The two structural claims inside the sort model, seen to FAIL.

    Built rather than found: production cannot produce any of these shapes
    today, which is exactly why a control is needed -- an unexercised guard is
    a comment. The first plants an action from ANOTHER channel in the
    differing window. The second INTERLEAVES two tasks *and* reorders within
    one of them: no per-task loop can produce that, and a "stable sort by
    task" that reconstructed it would be reconstructing a coincidence. The
    third loses an action, which is a decision that changed and not an order.
    """
    inp = corpus_profiles.build(
        next(states for _, states in planner_corpus.projections()), "neutral")

    foreign_old = [reconcile.OpenWave(task_ids=["t2"]), reconcile.OpenWave(task_ids=["t1"])]
    foreign_new = list(reversed(foreign_old))
    _, problems = _sort_the_attempt_run(foreign_old, foreign_new, inp)
    assert problems and "cannot emit" in problems[0], problems

    interleaved_old = [
        reconcile.StallCheck(task_id="t2", attempt_id="a1"),
        reconcile.StallCheck(task_id="t1", attempt_id="b1"),
        reconcile.StallCheck(task_id="t2", attempt_id="a2"),
    ]
    interleaved_new = [interleaved_old[2], interleaved_old[1], interleaved_old[0]]
    _, problems = _sort_the_attempt_run(interleaved_old, interleaved_new, inp)
    assert problems and "interleaves" in problems[0], problems

    lost_old = [reconcile.StallCheck(task_id="t2", attempt_id="a1"),
                reconcile.StallCheck(task_id="t1", attempt_id="b1")]
    lost_new = [reconcile.StallCheck(task_id="t1", attempt_id="b1"),
                reconcile.StallCheck(task_id="t1", attempt_id="b2")]
    _, problems = _sort_the_attempt_run(lost_old, lost_new, inp)
    assert problems and "gained or lost" in problems[0], problems


def test_the_tie_break_model_refuses_a_handle_that_was_never_maximal():
    """The safety claim inside the resume model, seen to FAIL.

    A legacy plan that resumed a session which is NOT `started`-maximal cannot
    be explained by a tie-break, and must be reported instead of rewritten.
    """
    tied = next(
        (corpus_profiles.build(states, "neutral")
         for _, states in planner_corpus.projections()
         if any(a.role == Role.REVIEW_INDEPENDENT
                and a.state == AttemptState.EXITED and a.session_handle
                for tsf in states.values() for a in tsf.attempts)),
        None)
    assert tied is not None, "no corpus projection has a resumable review session"
    forged = [reconcile.LaunchReview(wave_id="w1", task_ids=["t1"],
                                     resume_session="no-such-handle")]
    _, problems = _retie_the_warm_review_session(forged, forged, tied)
    assert problems and "not a `started`-maximal" in problems[0], problems


@pytest.mark.parametrize("case_id", planner_synthetic.SCENARIO_NAMES)
def test_synthetic_scenarios_are_permutation_stable(case_id: str):
    """Each synthetic scenario carries several tasks in its target state, so
    map order is OBSERVABLE -- which is what the historical corpus could not
    do for these states, and why a defect hid behind a green.

    Asserted as a SEQUENCE. CR-06a had to weaken this to a multiset because
    the attempt channel's order followed the snapshot; CR-06b repaired that,
    so the acceptance the parent actually asked for -- "permuting input-map
    order cannot change planned actions" -- is what is checked.
    """
    states = dict(next(s for n, s in planner_synthetic.projections() if n == case_id))
    forward = reconcile.plan_project(corpus_profiles.build(states, "neutral"))
    reversed_states = {k: states[k] for k in reversed(list(states))}
    backward = reconcile.plan_project(corpus_profiles.build(reversed_states, "neutral"))
    assert _normalize_plan(forward) == _normalize_plan(backward), (
        f"{case_id}: permuting the snapshot changed the planned actions")


def test_cr00_corpus_plans_are_identical():
    """The other half of §5.1: the CR-00 characterization scenarios.

    Built through the characterization module's own input builder, so this
    plans exactly what that corpus plans -- not a re-derivation of it that
    could drift into agreeing with itself.
    """
    import test_core_characterization as cc

    checked = 0
    for scenario in cc._active():
        state = cc._project(cc._history(scenario))[cc.TASK_ID]
        found = compare(cc._plan_input(scenario, state))
        assert not found, f"{scenario['name']}: {found[0]}"
        checked += 1
    assert checked >= 10, f"only {checked} CR-00 scenarios exercised"


# ---------------------------------------------------------------------------
# the anti-decoration check


def test_the_comparator_reports_an_action_delta():
    """A differential that has never been seen to fail is decoration.

    The two planners agree by construction while CR-06 is unstarted, so this
    plants the difference instead: the same projection is planned under two
    environments that a rule genuinely discriminates on, through the same
    comparison code the tests above use. If this passes silently, every green
    above means nothing.
    """
    states = next(
        states for _, states in planner_corpus.projections()
        if any(t.state.value == "QUEUED" for t in states.values())
    )
    old = legacy_planner.plan_project(corpus_profiles.build(states, "neutral"))
    new = reconcile.plan_project(corpus_profiles.build(states, "paused"))
    assert _normalize_plan(old) != _normalize_plan(new), (
        "pausing a project with queued work changed no plan -- the corpus "
        "case chosen here cannot discriminate, so the comparator is untested")


def test_the_comparator_reports_a_lost_breadcrumb():
    """The trace half of the comparison, exercised the same way."""
    full = (("dispatch", "t1", "route:r"), ("carve", None, "headroom"))
    assert _is_subsequence(full, full)
    assert _is_subsequence((("carve", None, "headroom"),), full)
    # a breadcrumb the new plan dropped, and one whose detail changed
    assert not _is_subsequence((("dispatch", "t1", "route:other"),), full)
    # order matters: a subsequence is not a subset
    assert not _is_subsequence(tuple(reversed(full)), full)
