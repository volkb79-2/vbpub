"""Review planning rules (CR-06a): module contract item 5, and the B5
self-review leg.

Two rules, one shape. Both answer "does this task still need a review leg
launched", both use the SAME latest-attempt recency guard, and both refuse to
start anything under ``drain-agents`` -- a review launch and a self-review are
each a new agent process, and the drain mode's whole content is that no new
agent process of any kind may start.

The recency guard is the load-bearing part and it is scar tissue: a task's
attempts list keeps every attempt forever, so "does this task have a review
attempt" was permanently true from the first review onward and a second review
could never be launched. Only the LATEST attempt can meaningfully be in
flight, because anything newer having run proves the older one is done. The
comments below record which incident taught which clause; they travel with the
code they explain.

Wave batching is contract item 5 proper: tasks with no wave get batched into
one OpenWave, and an already-open wave whose members need review gets ONE
LaunchReview carrying all of them -- one frontier session per wave, not one
per task.
"""

from __future__ import annotations

from .planning import PlanContext, RuleEmitter
from .reconcile import LaunchReview, LaunchSelfReview, OpenWave
from .types import AttemptState, Role, TaskState

#: Attempt states in which a launched leg is still the one in flight. EXITED
#: counts: the exit exists but has not been consumed yet, and relaunching over
#: an unconsumed exit is how a task gets two verdicts for one diff.
_LIVE_OR_PENDING = (
    AttemptState.CREATED, AttemptState.PREFLIGHTING, AttemptState.RUNNING,
    AttemptState.STALLED, AttemptState.EXITED,
)


def _leg_in_flight(tsf, role: Role) -> bool:
    """Whether this task's LATEST attempt is a still-live leg of ``role``.

    Recency, not existence (2026-07-17 fix, stale-wave_id strand): the
    pre-fix check scanned ALL of tsf.attempts for ANY REVIEW_INDEPENDENT
    attempt in these states, including terminal EXITED -- with no scoping to
    "is this attempt still current". Once a task's first review attempt
    reached EXITED (approved OR rejected) it stayed in tsf.attempts forever,
    so the guard was permanently True from that point on. Combined with the
    reject-loop (REVIEW_REJECTED -> QUEUED -> a fresh implementer ->
    AWAITING_REVIEW a SECOND time), that meant a second review could never be
    launched -- the task silently stranded AWAITING_REVIEW forever.

    Scoping by tsf.wave_id does not help on its own: wave_id is set once by
    OpenWave/WAVE_OPENED and never reset (REVIEW_RECORDED is a true audit-only
    no-op in storage.apply_event -- see test_invariants.py's
    test_known_ignored_event_types_are_true_noops -- so it cannot clear it, and
    every review attempt for this task is dispatched with that SAME wave_id
    forever). The actual discriminator is RECENCY: only the task's LATEST
    attempt can meaningfully be "the leg in flight" -- a stale EXITED review
    from a prior cycle that has since been superseded by a fresh implementer
    attempt (the reject-loop's re-work) is provably no longer in flight,
    because something newer already ran after it. This mirrors the is_latest
    check the interrupted-poisoned handling already uses.

    An INTERRUPTED leg counts as in flight only when it kept a session handle:
    that is the one the ordinary resume path will pick up, so launching a cold
    duplicate over it would waste a session. Without a handle there is nothing
    to resume, and this task needs a fresh launch.
    """
    latest = tsf.attempts[-1] if tsf.attempts else None
    return (
        latest is not None
        and latest.role == role
        and (
            latest.state in _LIVE_OR_PENDING
            or (latest.state == AttemptState.INTERRUPTED
                and latest.session_handle is not None)
        )
    )


def review_waves(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Contract item 5: batch waiting reviews into a wave, then launch the
    open waves that still need a reviewer.

    A wave opens when wave_max_diffs tasks are waiting OR the oldest has
    waited past wave_open_after_seconds. OpenWave itself is pure bookkeeping
    (no process start) and is never gated by pause mode; LaunchReview IS a
    process start and is skipped entirely under drain-agents.
    """
    inp = ctx.inp
    awaiting_review = [
        (task_id, tsf) for task_id, tsf in inp.states.items()
        if tsf.state == TaskState.AWAITING_REVIEW and tsf.wave_id is None
    ]
    awaiting_review.sort(key=lambda x: x[0])  # Sort by task_id

    if awaiting_review:
        # Batch into waves
        wave_max = inp.cfg.policy.wave_max_diffs
        task_ids_to_batch = [tid for tid, _ in awaiting_review]
        now_timestamp = inp.now.timestamp()

        # Check if we should open a wave
        should_open = len(task_ids_to_batch) >= wave_max
        if not should_open and task_ids_to_batch:
            # P65 2026-07-20 (M11): the age trigger must read the age of the
            # GENUINELY oldest waiting task. task_ids_to_batch is sorted by
            # task_id, so [0] is the lexicographically-first task -- which need
            # not be the oldest. An old task sorted after a fresh one would
            # otherwise never trip the timeout, stranding a review unboundedly
            # under low throughput. Take the minimum `since` across the batch.
            oldest_since = min(
                inp.states[t].since.timestamp() for t in task_ids_to_batch)
            age = now_timestamp - oldest_since
            if age > inp.wave_open_after_seconds:
                should_open = True

        if should_open:
            batched = task_ids_to_batch[:wave_max]
            emit(OpenWave(task_ids=batched))

    # P61 2026-07-20 (A9, M3 -- real wave batching). A wave's review is ONE
    # frontier session over ALL its members, not one session per task. The
    # per-task in-flight recency check is unchanged (it still decides, task by
    # task, "does THIS task still need a review launched") -- but the tasks
    # that pass it are GROUPED BY wave_id and launched together, so a 3-task
    # wave pays the ~35-40k frontier startup tax ONCE, not three times, and
    # (with the executor recording the shared attempt on every member) each
    # member's LATEST attempt is that review, so the next pass sees it in
    # flight for all of them and does not relaunch. The reject-loop still works
    # per task: a rejected member re-queues, gets a fresh implementer attempt
    # (its new LATEST, role IMPLEMENTER), and on returning to AWAITING_REVIEW
    # is the sole member of its own re-review launch -- preserving the
    # 2026-07-17 recency fix.
    needs_review_by_wave: dict[str, list[str]] = {}
    for task_id, tsf in inp.states.items():
        if tsf.state == TaskState.AWAITING_REVIEW and tsf.wave_id is not None:
            if ctx.drain_agents:
                # P15 2026-07-15: no new agent process (a review launch IS
                # one) while draining agents; the task stays parked
                # AWAITING_REVIEW until a later pass sees run/drain-handoffs.
                continue
            if not _leg_in_flight(tsf, Role.REVIEW_INDEPENDENT):
                needs_review_by_wave.setdefault(tsf.wave_id, []).append(task_id)

    # B6/P74 (D-R10): reviewer session-reuse. If the review_independent stage's
    # context declares "session-reuse" (resolved once into the pass context),
    # every review launched THIS pass resumes the warmest prior review session
    # -- the most-recent EXITED REVIEW_INDEPENDENT attempt that captured a
    # session_handle (max by `started`). A cold first wave (no such prior)
    # leaves this None -> the executor cold-dispatches, exactly as before.
    # Resuming an old/cache-expired session is harmless (a cache MISS, the
    # pre-B6 cost), and A7 binding is preserved by the executor stamping a
    # fresh attempt id -- so the only precondition is "a resumable review
    # session exists", nothing about which wave it reviewed. review_independent
    # is serial, so in practice at most one review is in flight and a second
    # wave planned the same pass simply shares the same warm handle (each still
    # gets its own fresh attempt id downstream).
    resume_session: str | None = None
    if needs_review_by_wave and ctx.review_session_reuse:
        prior_review_sessions = [
            a for tsf in inp.states.values() for a in tsf.attempts
            if a.role == Role.REVIEW_INDEPENDENT
            and a.state == AttemptState.EXITED
            and a.session_handle
        ]
        if prior_review_sessions:
            resume_session = max(
                prior_review_sessions, key=lambda a: a.started).session_handle

    # One LaunchReview per wave, carrying all its members (sorted for a
    # deterministic plan / stable first_task anchor).
    for wave_id in sorted(needs_review_by_wave):
        emit(LaunchReview(wave_id=wave_id, task_ids=sorted(needs_review_by_wave[wave_id]),
                          resume_session=resume_session))


def self_review_dispatch(ctx: PlanContext, emit: RuleEmitter) -> None:
    """B5 2026-07-20: launch a SELF_REVIEWING task's self_review leg ONCE.

    A warm resume of the implementer's session under Role.SELF_REVIEW. Mirrors
    the wave review's in-flight recency guard -- a SELF_REVIEW attempt that is
    the task's LATEST and still live/pending (or EXITED-pending-consumption)
    means the leg is already running, so do not relaunch. The daemon reads the
    source implementer attempt's session_handle; if absent it degrades to
    AWAITING_REVIEW (skip). Inert unless the `self_review` stage is composed
    into the pipeline -- nothing routes a task into SELF_REVIEWING otherwise,
    so a pipeline without self_review plans byte-identically to before B5.
    """
    for task_id, tsf in ctx.inp.states.items():
        if tsf.state != TaskState.SELF_REVIEWING:
            continue
        if ctx.drain_agents:
            # no NEW agent process while draining (a self-review IS one); the
            # task parks in SELF_REVIEWING until a later run/drain-handoffs pass.
            continue
        if _leg_in_flight(tsf, Role.SELF_REVIEW):
            continue
        # the implementer attempt whose done-exit routed the task here -- its
        # warm session_handle is what the self-review borrows.
        source = next(
            (a for a in reversed(tsf.attempts) if a.role == Role.IMPLEMENTER), None)
        if source is None:
            continue  # unreachable in practice: SELF_REVIEWING is only entered via implement-done
        emit(LaunchSelfReview(task_id=task_id, source_attempt_id=source.attempt_id))
