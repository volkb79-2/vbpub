"""Attempt launch: implementer dispatch, resume, and the self-review leg.

THE THREE WAYS AN AGENT STARTS (CR-05c)
---------------------------------------
* **dispatch** mints a NEW attempt in a fresh worktree and moves the task to
  ACTIVE. It is the only one that creates work.
* **resume** re-enters an EXISTING attempt's session after an interrupt or a
  stall. Same attempt id, same directory, a new log.
* **self-review** mints a new attempt that BORROWS the implementer's warm
  session -- a hybrid, and the reason both of the above exist separately.

The exit consumption that reads what any of them produced is CR-05e's: what
the daemon does with an exit depends on the attempt's ROLE, not on which
effector dispatched it, and its carver branch delegates into carve (CR-05d).
So the consumer lands after both, and this module is dispatch only.

WHY SELF-REVIEW IS A NEW ATTEMPT AND NOT A RESUME
-------------------------------------------------
``Attempt.role`` is immutable and the exit consumer keys on it, so the
self-check needs its own record. But it runs WARM -- built from the
implementer's borrowed session handle -- because a cold start would pay a
35-40k token orientation tax to re-read work the same session just produced.

If that handle is absent (capture failed), this DEGRADES to AWAITING_REVIEW
rather than blocking. Self-review is an optional pre-gate; the frontier
reviewer is the real one, and a task must never be stranded because its cheap
self-check could not run.

THE STALE RECEIPT RULE
----------------------
A resumed attempt reuses its directory and the SAME receipt path, and the leg
being resumed already wrote an interrupt receipt at its own exit. Leaving
that in place makes the next pass see a RUNNING attempt WITH a receipt --
"the wrapper died before its exit event" -- and emit a premature exit on the
STALE receipt while the resumed session is still live, transitioning the task
off ACTIVE and letting a SECOND implementer into the same worktree. The
receipt is therefore ARCHIVED before launch, never deleted: consumed exactly
once, and still auditable.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from . import (
    adapters, config, effects, effects_dispatch, effects_review, paths,
    reconcile, results, wrapper,
)
from .config import ProjectConfig
from .log import bind, get_logger
from .types import (
    Attempt, AttemptState, Event, EventType, Role, Route, TaskState,
    TaskStateFile, new_id,
)

log = get_logger("effects.attempt")

#: Bound on the reviewer prose embedded in a re-dispatch prompt, so a
#: pathologically long report cannot blow the prompt-length guard.
MAX_PRIOR_VERDICT_CHARS = 4000


def review_rationale(git: Any, cfg: ProjectConfig, task_id: str,
                     max_chars: int = MAX_PRIOR_VERDICT_CHARS) -> str | None:
    """The reviewer's rejection PROSE, bounded, for a re-dispatch prompt.

    A re-queued fix must target exactly what was flagged rather than being a
    context-free same-model retry. None on a FIRST dispatch -- no committed
    review exists yet -- which leaves the prompt exactly as it was before this
    behaviour existed.
    """
    text = effects_review.review_report_text(git, cfg, task_id)
    if not text or not text.strip():
        return None
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n[... review report truncated ...]"
    return text


def next_resume_n(files: Any, attempt_dir) -> int:
    """The next unused resume ordinal for this attempt directory.

    Derived from what is on disk rather than counted in state: a resume that
    landed but whose event did not would otherwise reuse an ordinal and
    overwrite the previous leg's log.
    """
    n = 1
    while (files.exists(attempt_dir / f"attempt.resume-{n}.log")
           or files.exists(attempt_dir / f"spec.resume-{n}.json")):
        n += 1
    return n


class AttemptEffector:
    """Starts agent legs. Holds no state of its own."""

    def __init__(self, ports: effects.EffectPorts) -> None:
        self._ports = ports

    # -- implementer dispatch -------------------------------------------

    def dispatch_implementer(self, ctx: effects.EffectContext,
                             action: reconcile.DispatchImplementer) -> list[Event]:
        # The route is resolved BEFORE admission (CR-13a): containment is a
        # property of the route, so the gate cannot ask about it otherwise.
        routes_obj = config.Routes.load()
        route_def = routes_obj.routes[action.route_id]
        ok, _reason = effects_dispatch.admissible(ctx, "dispatch", route_def)
        if not ok:
            return []  # admission refused; task stays QUEUED, re-evaluated next pass
        events: list[Event] = []
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        task_id = action.task_id
        tsf = states[task_id]
        branch = f"feat/{task_id}"
        worktree_path = cfg.root / cfg.worktree_root / branch
        effects_dispatch.ensure_worktree(self._ports, cfg.root, branch,
                                         worktree_path, cfg.default_branch)

        attempt_id = new_id("att")
        with bind(task=task_id, attempt=attempt_id):
            log.info("dispatch", project=project, route=route_def.route_id)
        attempt = Attempt(
            attempt_id=attempt_id, role=Role.IMPLEMENTER,
            state=AttemptState.CREATED, route=_route_snapshot(route_def, routes_obj),
            started=self._ports.clock.now(), worktree=str(worktree_path),
            branch=branch)
        events.append(ctx.append(EventType.ATTEMPT_CREATED,
                                 {"attempt": attempt.to_dict()},
                                 task_id=task_id, attempt_id=attempt_id))

        attempt_dir = paths.attempt_dir(project, attempt_id)
        receipt_path = str(attempt_dir / "receipt.json")
        argv, _prompt = adapters.build_dispatch(
            route_def, handoff_path=tsf.handoff_path or "",
            worktree=str(worktree_path), branch=branch, task_id=task_id,
            gate_hint=effects_dispatch.gate_hint(cfg),
            receipt_path=receipt_path, role=Role.IMPLEMENTER,
            # The reviewer's findings, so the fix is targeted.
            prior_verdict=review_rationale(self._ports.git, cfg, task_id),
            # The widened allowlist reaches the agent through the PROMPT; the
            # handoff's scope.touch on disk is never rewritten.
            approved_amendments=effects_dispatch.scope_amendment_files(
                ctx.events(), task_id),
        )
        spec = wrapper.WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
            cwd=str(worktree_path), log_path=str(attempt_dir / "attempt.log"),
            receipt_path=receipt_path, attempt_dir=str(attempt_dir),
            route_def=asdict(route_def), repo_root=str(cfg.root),
            leases=effects_dispatch.lease_specs(
                cfg, effects_dispatch.frontmatter_for(cfg, tsf)),
        )
        attempt.pid = self._ports.processes.launch_detached(spec)
        attempt.state = AttemptState.PREFLIGHTING
        events.append(ctx.append(EventType.ATTEMPT_PREFLIGHTED,
                                 {"attempt": attempt.to_dict()},
                                 task_id=task_id, attempt_id=attempt_id))
        events.append(ctx.transition(task_id, TaskState.ACTIVE, None))
        return events

    # -- resume ----------------------------------------------------------

    def resume_attempt(self, ctx: effects.EffectContext,
                       action: reconcile.ResumeAttempt) -> list[Event]:
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        task_id = action.task_id
        tsf = states[task_id]
        attempt = tsf.attempt_by_id(action.attempt_id)
        attempt_dir = paths.attempt_dir(project, action.attempt_id)
        resume_n = next_resume_n(self._ports.files, attempt_dir)
        routes_obj = config.Routes.load()
        route_def = routes_obj.routes[attempt.route.route_id]
        ok, _reason = effects_dispatch.admissible(ctx, "resume", route_def)
        if not ok:
            return []  # admission refused; attempt stays INTERRUPTED, re-evaluated next pass
        # A retry is degraded-but-continuing: WARNING, not INFO.
        with bind(task=task_id, attempt=action.attempt_id):
            log.warning("attempt-retry", project=project,
                        route=route_def.route_id, resume_n=resume_n)
        worktree = attempt.worktree or str(cfg.root)
        argv = adapters.build_resume(
            route_def, session=attempt.session_handle, worktree=worktree,
            prompt=f"Resume {task_id} attempt {action.attempt_id} in {worktree}")
        spec = wrapper.WrapperSpec(
            project=project, task_id=task_id, attempt_id=action.attempt_id,
            argv=argv, cwd=worktree,
            log_path=str(attempt_dir / f"attempt.resume-{resume_n}.log"),
            receipt_path=str(attempt_dir / "receipt.json"),
            attempt_dir=str(attempt_dir), route_def=asdict(route_def),
            repo_root=str(cfg.root),
            leases=effects_dispatch.lease_specs(
                cfg, effects_dispatch.frontmatter_for(cfg, tsf)),
        )
        # ARCHIVE the previous leg's receipt before launching -- see the
        # module docstring's stale-receipt rule. Renamed, not deleted: it is
        # audit trail, and what matters is only that `receipt.json` does not
        # exist again until THIS run writes its own.
        stale_receipt = attempt_dir / "receipt.json"
        if self._ports.files.exists(stale_receipt):
            stale_receipt.rename(
                attempt_dir / f"receipt.pre-resume-{resume_n}.json")
        attempt.pid = self._ports.processes.launch_detached(spec)
        attempt.state = AttemptState.RUNNING
        # Refresh the log path HERE rather than leaving it stale until the
        # wrapper's own later ATTEMPT_STARTED lands: a stale path made the
        # quiet-log stall check watch a dead file while the live resumed
        # process went unwatched.
        attempt.log_path = spec.log_path
        return [ctx.append(EventType.ATTEMPT_RESUMED,
                           {"attempt": attempt.to_dict()},
                           task_id=task_id, attempt_id=action.attempt_id)]

    # -- the self-review leg ---------------------------------------------

    def launch_self_review(self, ctx: effects.EffectContext,
                           action: reconcile.LaunchSelfReview) -> list[Event]:
        """A warm self-check before the expensive frontier reviewer.

        No transition on success: the task is already SELF_REVIEWING, and the
        leg's own exit is what moves it on.
        """
        ok, _reason = effects_dispatch.admissible(ctx, "self-review")
        if not ok:
            return []  # admission refused; task stays SELF_REVIEWING, retried next pass
        events: list[Event] = []
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        task_id = action.task_id
        tsf = states[task_id]
        source = tsf.attempt_by_id(action.source_attempt_id)
        if source is None or not source.session_handle:
            # No warm session to borrow. Skip the optional pre-gate rather
            # than stranding the task -- the frontier reviewer still runs.
            return [ctx.transition(
                task_id, TaskState.AWAITING_REVIEW,
                "self-review skipped (no warm session to resume) -- "
                "proceeding to frontier review")]
        worktree = source.worktree or str(cfg.root)
        branch = source.branch or f"feat/{task_id}"
        routes_obj = config.Routes.load()
        route_def = routes_obj.routes[source.route.route_id]
        # Asked a SECOND time, now that the borrowed route is known: this leg
        # cannot resolve its route before deciding whether there is a session
        # to borrow at all, and the containment question is a property of the
        # route. The pause/budget half is idempotent, so re-asking costs
        # nothing and keeps ONE gate rather than a second, weaker one.
        ok, _reason = effects_dispatch.admissible(ctx, "self-review", route_def)
        if not ok:
            return []  # admission refused; task stays SELF_REVIEWING, retried next pass
        attempt_id = new_id("att")
        attempt = Attempt(
            attempt_id=attempt_id, role=Role.SELF_REVIEW,
            state=AttemptState.CREATED, route=_route_snapshot(route_def, routes_obj),
            started=self._ports.clock.now(), worktree=worktree, branch=branch,
            session_handle=source.session_handle)
        events.append(ctx.append(EventType.ATTEMPT_CREATED,
                                 {"attempt": attempt.to_dict()},
                                 task_id=task_id, attempt_id=attempt_id))
        attempt_dir = paths.attempt_dir(project, attempt_id)
        argv = adapters.build_resume(
            route_def, session=source.session_handle, worktree=worktree,
            prompt=adapters.self_review_prompt(
                task_id=task_id, worktree=worktree, branch=branch,
                report_path=f"{cfg.reports_dir}/{task_id}-SELFREVIEW.md",
                attempt_id=attempt_id))
        spec = wrapper.WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
            cwd=worktree, log_path=str(attempt_dir / "attempt.log"),
            receipt_path=str(attempt_dir / "receipt.json"),
            attempt_dir=str(attempt_dir), route_def=asdict(route_def),
            repo_root=str(cfg.root),
            leases=effects_dispatch.lease_specs(
                cfg, effects_dispatch.frontmatter_for(cfg, tsf)),
            result_kind=results.ResultKind.SELF_REVIEW.value,
        )
        attempt.pid = self._ports.processes.launch_detached(spec)
        attempt.state = AttemptState.PREFLIGHTING
        events.append(ctx.append(EventType.ATTEMPT_PREFLIGHTED,
                                 {"attempt": attempt.to_dict()},
                                 task_id=task_id, attempt_id=attempt_id))
        return events


def _route_snapshot(route_def, routes_obj) -> Route:
    """Pin the route AS DISPATCHED onto the attempt.

    Carrying `routes_rev` is what lets a later reader tell "this attempt used
    the route it was planned with" from "the routes file changed underneath
    it", which a route id alone cannot answer.
    """
    return Route(route_id=route_def.route_id, cli=route_def.cli,
                 model=route_def.model, variant=route_def.variant,
                 effort=route_def.effort, routes_rev=routes_obj.revision)


def specs(effector: AttemptEffector) -> tuple[effects.HandlerSpec, ...]:
    return (
        effects.HandlerSpec(
            action_type=reconcile.DispatchImplementer,
            kind="dispatch-implementer",
            handler=effector.dispatch_implementer,
            emits=frozenset({EventType.ATTEMPT_CREATED,
                             EventType.ATTEMPT_PREFLIGHTED,
                             EventType.TASK_TRANSITIONED}),
            idempotency_key=lambda a: f"dispatch-implementer:{a.task_id}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.ResumeAttempt,
            kind="resume-attempt",
            handler=effector.resume_attempt,
            emits=frozenset({EventType.ATTEMPT_RESUMED}),
            idempotency_key=lambda a: f"resume-attempt:{a.attempt_id}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.LaunchSelfReview,
            kind="launch-self-review",
            handler=effector.launch_self_review,
            emits=frozenset({EventType.ATTEMPT_CREATED,
                             EventType.ATTEMPT_PREFLIGHTED,
                             EventType.TASK_TRANSITIONED}),
            idempotency_key=lambda a: f"launch-self-review:{a.task_id}",
        ),
    )
