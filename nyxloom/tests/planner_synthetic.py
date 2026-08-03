"""Synthetic projections for states real history never reached.

KEPT SEPARATE FROM `planner_corpus.py` ON PURPOSE. That module is the real
event logs of three projects; this one is hand-built. Merging them would make
the corpus look bigger than the ground truth behind it, which is the failure
this split exists to prevent -- so the differential runs both and reports them
separately, and `test_corpus_states_are_attributed_to_their_source` fails if a
state silently migrates from the real set to this one.

WHY THIS EXISTS. CR-06a's independent review found that
`self_review_dispatch` iterated the snapshot map unsorted -- the same
order-dependence defect the package had already found twice elsewhere -- and
that its permutation test PASSED ANYWAY, because **zero of the 877 real
projections contain a `SELF_REVIEWING` task**. These projects never composed
the `self_review` stage, so that channel's acceptance was vacuous rather than
met. A corpus drawn from history can only exercise the history that happened.

Four states are absent from the real corpus, and each owns real rules:

    DRAFT            -- inert today; CR-07 removes it as an executable state
    NEEDS_DECISION   -- contract item 2 (decision holds), item 10's product route
    READY_TO_CARVE   -- contract item 12's re-carve handler, ALL of it (CR-06c)
    SELF_REVIEWING   -- the B5 self-review dispatch (where the defect was)

`READY_TO_CARVE` is the sharpest gap: item 12 is a whole rule that the
historical differential cannot exercise at all, and it is CR-06c's scope.

Scenarios that target an ORDERING claim carry MULTIPLE tasks in the state they
target -- `self-reviewing-pair`, `ready-to-carve-pair`, `needs-decision-pair`,
the two review-wave scenarios and `mixed-unreached-states`. A single-task
projection cannot exhibit an ordering defect, which is precisely how the
`SELF_REVIEWING` one hid: the rule picks a task, and with one candidate every
iteration order agrees.

CORRECTED BY CR-06c. This paragraph used to claim that of EVERY scenario, and
it was untrue of three: `self-reviewing-without-implementer`,
`self-reviewing-leg-in-flight` and `draft` each carry ONE task, deliberately,
because what they pin is a guard refusing to fire rather than an order. That
is a fine thing for a scenario to do; the docstring promising otherwise was
the defect, because a reader checking whether this corpus can see an ordering
bug would have taken the promise for the property.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nyxloom.types import (
    Attempt, AttemptState, Blocker, BlockerType, Receipt, ReceiptResult, Role,
    Route, TaskState, TaskStateFile,
)

BASE = datetime(2026, 7, 1, tzinfo=timezone.utc)
_ROUTE = Route(route_id="corpus-route", cli="fake", model="fake-a")


def _attempt(
    attempt_id: str,
    role: Role,
    state: AttemptState,
    *,
    minutes: int = 0,
    handle: str | None = None,
    result: ReceiptResult | None = None,
) -> Attempt:
    return Attempt(
        attempt_id=attempt_id,
        role=role,
        state=state,
        route=_ROUTE,
        started=BASE + timedelta(minutes=minutes),
        session_handle=handle,
        receipt=None if result is None else Receipt(result=result, exit_code=0),
    )


def _task(
    task_id: str,
    state: TaskState,
    *,
    minutes: int = 0,
    attempts: list[Attempt] | None = None,
    wave_id: str | None = None,
    paused: bool = False,
    blocker: Blocker | None = None,
) -> TaskStateFile:
    return TaskStateFile(
        schema_version=1,
        task_id=task_id,
        project="corpus",
        state=state,
        since=BASE + timedelta(minutes=minutes),
        handoff_path=f"handoff/{task_id}.md",
        wave_id=wave_id,
        paused=paused,
        blocker=blocker,
        attempts=list(attempts or []),
    )


def _states(*tasks: TaskStateFile) -> dict[str, TaskStateFile]:
    return {t.task_id: t for t in tasks}


def _impl_done(n: str, minutes: int = 0, handle: str | None = "h") -> Attempt:
    return _attempt(f"att-{n}", Role.IMPLEMENTER, AttemptState.EXITED,
                    minutes=minutes, handle=handle, result=ReceiptResult.DONE)


#: (name, projection, why real history could not produce it).
SCENARIOS: tuple[tuple[str, dict[str, TaskStateFile], str], ...] = (
    (
        "self-reviewing-pair",
        _states(
            _task("t-b", TaskState.SELF_REVIEWING, minutes=10,
                  attempts=[_impl_done("b", 5)]),
            _task("t-a", TaskState.SELF_REVIEWING, minutes=20,
                  attempts=[_impl_done("a", 15)]),
        ),
        "no project composed the self_review stage, so SELF_REVIEWING is "
        "unreachable in every recorded log. Two tasks, inserted in "
        "non-sorted order, so a rule that iterates the map raw is "
        "distinguishable from one that sorts.",
    ),
    (
        "self-reviewing-without-implementer",
        _states(_task("t-a", TaskState.SELF_REVIEWING, minutes=10)),
        "a SELF_REVIEWING task with no implementer attempt to borrow a warm "
        "session from -- the rule must park rather than launch a "
        "session-less leg.",
    ),
    (
        "self-reviewing-leg-in-flight",
        _states(
            _task("t-a", TaskState.SELF_REVIEWING, minutes=10, attempts=[
                _impl_done("a", 5),
                _attempt("att-sr", Role.SELF_REVIEW, AttemptState.RUNNING, minutes=8),
            ]),
        ),
        "the in-flight recency guard: a live SELF_REVIEW attempt must "
        "suppress a second launch.",
    ),
    (
        "ready-to-carve-pair",
        _states(
            _task("t-b", TaskState.READY_TO_CARVE, minutes=10),
            _task("t-a", TaskState.READY_TO_CARVE, minutes=20),
        ),
        "contract item 12's re-carve handler is reachable only from item "
        "10's exhausted/architectural routes, which no recorded log took. "
        "Two tasks pin 'the single LOWEST task id gets the pass's carve "
        "slot' -- with one task that rule is indistinguishable from 'any'.",
    ),
    (
        "ready-to-carve-with-live-carver",
        _states(
            _task("t-a", TaskState.READY_TO_CARVE, minutes=10),
            _task("carve-1", TaskState.ACTIVE, minutes=5, attempts=[
                _attempt("att-c", Role.CARVER, AttemptState.RUNNING, minutes=5),
            ]),
        ),
        "the shared carve_in_flight guard: a live CARVER attempt on a "
        "non-terminal task must block item 12 as well as item 9.",
    ),
    (
        "needs-decision-pair",
        _states(
            _task("t-b", TaskState.NEEDS_DECISION, minutes=10),
            _task("t-a", TaskState.NEEDS_DECISION, minutes=20),
        ),
        "contract item 2's release edge -- NEEDS_DECISION returns to QUEUED "
        "once its D-deps close. The 'decisions-open' profile holds them; the "
        "others release them.",
    ),
    (
        "draft",
        _states(_task("t-a", TaskState.DRAFT, minutes=10)),
        "DRAFT is orphaned in the shipped enum: nothing plans it and CR-07 "
        "removes it as an executable state. Pinned so its inertness is "
        "OBSERVED before CR-07 changes it, rather than assumed.",
    ),
    (
        "mixed-unreached-states",
        _states(
            _task("t-a", TaskState.READY_TO_CARVE, minutes=10),
            _task("t-b", TaskState.SELF_REVIEWING, minutes=12,
                  attempts=[_impl_done("b", 8)]),
            _task("t-c", TaskState.NEEDS_DECISION, minutes=14),
            _task("t-d", TaskState.QUEUED, minutes=16),
        ),
        "the four absent states alongside a dispatchable task, so the carve "
        "slot, the self-review launch and the dispatch capacity contend in "
        "one pass -- the interaction the single-state scenarios cannot show.",
    ),
    (
        "review-wave-tie-on-started",
        _states(
            _task("t-a", TaskState.AWAITING_REVIEW, minutes=10, wave_id="wave-1",
                  attempts=[_attempt("att-r1", Role.REVIEW_INDEPENDENT,
                                     AttemptState.EXITED, minutes=5, handle="h1")]),
            _task("t-b", TaskState.AWAITING_REVIEW, minutes=10, wave_id="wave-1",
                  attempts=[_attempt("att-r2", Role.REVIEW_INDEPENDENT,
                                     AttemptState.EXITED, minutes=5, handle="h2")]),
        ),
        "two EXITED review attempts sharing a `started` timestamp. WHAT IT "
        "ACTUALLY PINS, restated by CR-06b's review: NOT the resume_session "
        "tie -- both tasks carry a REVIEW_INDEPENDENT attempt as their "
        "LATEST, so the in-flight recency guard suppresses every launch and "
        "no LaunchReview is planned, which is why "
        "`review-resume-tie-reaches-a-launch` below had to be added. What is "
        "left is a real case worth keeping: a whole WAVE whose every member "
        "already has a live review leg plans nothing, so a relaxation of the "
        "recency guard shows up as a launch appearing here. The original "
        "rationale claimed it kept the tie-break defect visible; the defect "
        "is repaired and the claim was never true of this projection.",
    ),
    (
        "review-resume-tie-reaches-a-launch",
        _states(
            _task("t-a", TaskState.AWAITING_REVIEW, minutes=10, wave_id="wave-1",
                  attempts=[_attempt("att-r1", Role.REVIEW_INDEPENDENT,
                                     AttemptState.EXITED, minutes=5, handle="h1")]),
            _task("t-b", TaskState.AWAITING_REVIEW, minutes=10, wave_id="wave-1",
                  attempts=[_attempt("att-r2", Role.REVIEW_INDEPENDENT,
                                     AttemptState.EXITED, minutes=5, handle="h2")]),
            _task("t-c", TaskState.AWAITING_REVIEW, minutes=10, wave_id="wave-1",
                  attempts=[_impl_done("c", 6)]),
        ),
        "CR-06b: the scenario above RECORDS the tie but cannot exercise it -- "
        "both its tasks have a REVIEW_INDEPENDENT attempt as their LATEST, so "
        "the in-flight recency guard suppresses every launch and no "
        "LaunchReview is planned at all. A tie nothing reads is not a "
        "divergence. This adds a third member whose latest attempt is the "
        "IMPLEMENTER leg, so the wave DOES launch and the tied warm handle is "
        "actually chosen -- which is what makes the (started, attempt_id) "
        "tie-break observable outside the historical corpus.",
    ),
)

SCENARIO_NAMES: tuple[str, ...] = tuple(name for name, _, _ in SCENARIOS)


def projections() -> tuple[tuple[str, dict[str, TaskStateFile]], ...]:
    """Same shape as `planner_corpus.projections()`, so callers treat both alike."""
    return tuple((name, states) for name, states, _ in SCENARIOS)


def states_covered() -> set[TaskState]:
    return {t.state for _, states, _ in SCENARIOS for t in states.values()}
