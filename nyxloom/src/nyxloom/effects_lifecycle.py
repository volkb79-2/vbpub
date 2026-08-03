"""Lifecycle effects: task and attempt bookkeeping, and provider backoff.

WHAT IS IN HERE (CR-05a)
------------------------
The effect families that record a lifecycle fact or deliver a signal, and
nothing else. None of them launches an agent, merges, or authorizes a gate --
which is exactly why they are the first families to move: their whole
observable behaviour is the event they append, so a move is provable against
the event sequence alone.

* task creation and transition, including the typed-blocker form;
* attempt interruption -- the one signal-delivering effect here -- and the
  two marks (INTERRUPTED, STALLED) that make an attempt's fate visible;
* wave opening;
* spec-attention escalation, debounced;
* provider pause, whose backoff registry lives here rather than on the shell.

WHY THE PROVIDER PAUSE REGISTRY LIVES HERE
------------------------------------------
Setting a pause is an EFFECT (this module) and reading it is a PLANNING INPUT
(the daemon's route health probe). Before this package both reached into one
dict on ``Daemon``. Now the effector owns a
:class:`~nyxloom.effects.ProviderPauseRegistry` and the input builder is
handed the same instance, so the ownership is stated instead of shared: there
is exactly one writer, and the reader holds a reference it was given rather
than an attribute it found.
"""

from __future__ import annotations

from typing import Any

from . import effects, paths, reconcile, storage
from .log import get_logger
from .types import (
    AttemptState, Event, EventType, TaskState, TaskStateFile, new_id,
)

log = get_logger("effects.lifecycle")

#: How long a route stays paused after a provider limit. A module constant so
#: a test can shrink it for determinism; read at pause time, never captured.
PROVIDER_PAUSE_SECONDS = 3600


class LifecycleEffector:
    """Task/attempt bookkeeping and provider backoff."""

    def __init__(self, ports: effects.EffectPorts,
                 provider_pause: effects.ProviderPauseRegistry) -> None:
        self._ports = ports
        self.provider_pause = provider_pause

    # -- tasks ----------------------------------------------------------

    def create_task(self, ctx: effects.EffectContext,
                    action: reconcile.CreateTask) -> list[Event]:
        tsf = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id=action.task_id,
            project=ctx.project, state=TaskState.CARVED,
            since=self._ports.clock.now(), handoff_path=action.handoff_path,
        )
        return [ctx.append(EventType.TASK_CREATED, {"statefile": tsf.to_dict()},
                           task_id=action.task_id)]

    def transition(self, ctx: effects.EffectContext,
                   action: reconcile.Transition) -> list[Event]:
        """Move a task, or record a typed block, or do nothing.

        Three outcomes, and the third is the interesting one. A transition
        whose target EQUALS the current state is a NO-OP, not an error: it
        arises when two planning passes computed the same edge from a shared
        snapshot and the first already applied it. Letting the store's
        transition check raise there would surface a TICK_ERROR and pollute
        the log for a condition that is already correct.
        """
        if action.to is TaskState.BLOCKED and action.blocker is not None:
            # A typed-blocker BLOCKED transition emits TASK_BLOCKED rather
            # than a plain TASK_TRANSITIONED, so the blocker is recorded on
            # the task instead of only in a note.
            frm = ctx.states[action.task_id].state
            return [ctx.append(
                EventType.TASK_BLOCKED,
                {"from": frm.value, "blocker": action.blocker.to_dict(),
                 "notes": action.notes},
                task_id=action.task_id)]
        if ctx.states[action.task_id].state is action.to:
            return []
        return [ctx.transition(action.task_id, action.to, action.notes)]

    # -- attempts -------------------------------------------------------

    def interrupt_attempt(self, ctx: effects.EffectContext,
                          action: reconcile.InterruptAttempt) -> list[Event]:
        """Stop a running attempt, and emit nothing.

        Signal the WRAPPER first. Its own handler forwards SIGTERM to the
        child's process group AND classifies the resulting exit as
        'interrupted'. Signalling the child's group alone bypasses that
        handler entirely: the child dies from a signal the wrapper never
        observed, so the wrapper falls through to plain log-tail
        classification and reports 'error', not 'interrupted' -- and the
        confirmed-stall pipeline then silently retries instead of ever
        reaching INTERRUPTED.

        No event: the wrapper emits ATTEMPT_INTERRUPTED itself on exit.
        """
        # A premise check, not a read: an interrupt planned for a task this
        # pass does not know about is a planning defect, and raising surfaces
        # it as a TICK_ERROR rather than silently signalling nothing.
        _ = ctx.states[action.task_id]
        attempt_dir = paths.attempt_dir(ctx.project, action.attempt_id)
        signaled = False
        wrapper_pid_file = attempt_dir / "wrapper.pid"
        if self._ports.files.exists(wrapper_pid_file):
            try:
                wpid = int(self._ports.files.read_text(wrapper_pid_file).strip())
                self._ports.processes.terminate(wpid)
                signaled = True
            except (ValueError, ProcessLookupError, OSError):
                pass
        if not signaled:
            # Belt and braces: the wrapper may already be gone (crashed)
            # while the child it spawned is still alive.
            child_pid_file = attempt_dir / "child.pid"
            if self._ports.files.exists(child_pid_file):
                try:
                    child_pid = int(
                        self._ports.files.read_text(child_pid_file).strip())
                    self._ports.processes.terminate_group(child_pid)
                except (ValueError, ProcessLookupError, OSError):
                    pass
        return []

    def mark_interrupted(self, ctx: effects.EffectContext,
                         action: reconcile.MarkInterrupted) -> list[Event]:
        attempt = ctx.states[action.task_id].attempt_by_id(action.attempt_id)
        attempt.state = AttemptState.INTERRUPTED
        attempt.ended = self._ports.clock.now()
        return [ctx.append(EventType.ATTEMPT_INTERRUPTED,
                           {"attempt": attempt.to_dict()},
                           task_id=action.task_id,
                           attempt_id=action.attempt_id)]

    def mark_stalled(self, ctx: effects.EffectContext,
                     action: reconcile.MarkStalled) -> list[Event]:
        """Make a confirmed stall VISIBLE before anything interrupts it.

        The process is still running -- not ended, just flagged. Before this
        existed, a confirmed stall fed straight into an interrupt with no
        event in between, so it was invisible until the wrapper's own
        ATTEMPT_INTERRUPTED landed.
        """
        attempt = ctx.states[action.task_id].attempt_by_id(action.attempt_id)
        attempt.state = AttemptState.STALLED
        return [ctx.append(EventType.ATTEMPT_STALLED,
                           {"attempt": attempt.to_dict()},
                           task_id=action.task_id,
                           attempt_id=action.attempt_id)]

    def stall_check(self, ctx: effects.EffectContext,
                    action: reconcile.StallCheck) -> list[Event]:
        """A planned no-op: the stall cache was already updated during input
        build, so there is nothing left for the effect boundary to do. It
        stays a registered action rather than being dropped by the planner
        because the PLAN is what records that the check happened."""
        return []

    # -- waves and escalation -------------------------------------------

    def open_wave(self, ctx: effects.EffectContext,
                  action: reconcile.OpenWave) -> list[Event]:
        wave_id = new_id("wave")
        return [ctx.append(EventType.WAVE_OPENED,
                           {"task_ids": list(action.task_ids)},
                           wave_id=wave_id)]

    def spec_attention(self, ctx: effects.EffectContext,
                       action: reconcile.SpecAttention) -> list[Event]:
        """Escalate for spec attention, once per episode.

        Debounce backstop: a PERSISTENT condition would otherwise re-emit and
        re-notify every reconcile cycle forever. The counters that drive some
        of these reasons only ever increase, so without this a project with
        two old rejections notified at one message per pass.
        """
        if effects.spec_attention_recently_emitted(ctx.events(), action.reason):
            return []
        return [ctx.append(EventType.SPEC_ATTENTION,
                           {"reason": action.reason, "detail": action.detail},
                           task_id=action.task_id)]

    # -- provider backoff -----------------------------------------------

    def pause_provider(self, ctx: effects.EffectContext, route_id: str | None,
                       task_id: str | None, state: str = "limited") -> list[Event]:
        """Back a route off and tell an operator.

        ``state`` distinguishes a genuine provider LIMIT from an escalated
        local throttle in the event log without needing a second event type.
        The pause itself is in-memory backoff, so it is set even though the
        events are what make it visible.
        """
        out: list[Event] = []
        if route_id:
            self.provider_pause.pause(route_id, PROVIDER_PAUSE_SECONDS)
        out.append(ctx.append(EventType.PROVIDER_STATE_CHANGED,
                              {"route_id": route_id, "state": state},
                              task_id=task_id))
        out.append(ctx.append(EventType.NEEDS_OPERATOR,
                              {"route_id": route_id, "reason": "provider-limited"},
                              task_id=task_id))
        return out

    def provider_pause_action(self, ctx: effects.EffectContext,
                              action: reconcile.ProviderPause) -> list[Event]:
        return self.pause_provider(ctx, action.route_id, action.task_id)


# ---------------------------------------------------------------------------
# handler specs


def _state_name(to: Any) -> str:
    return getattr(to, "value", str(to))


def specs(effector: LifecycleEffector) -> tuple[effects.HandlerSpec, ...]:
    return (
        effects.HandlerSpec(
            action_type=reconcile.CreateTask,
            kind="create-task",
            handler=effector.create_task,
            emits=frozenset({EventType.TASK_CREATED}),
            idempotency_key=lambda a: f"create-task:{a.task_id}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.Transition,
            kind="transition",
            handler=effector.transition,
            emits=frozenset({EventType.TASK_BLOCKED,
                             EventType.TASK_TRANSITIONED}),
            idempotency_key=lambda a: f"transition:{a.task_id}:{_state_name(a.to)}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.InterruptAttempt,
            kind="interrupt-attempt",
            handler=effector.interrupt_attempt,
            emits=frozenset(),
            idempotency_key=lambda a: f"interrupt-attempt:{a.attempt_id}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.MarkInterrupted,
            kind="mark-interrupted",
            handler=effector.mark_interrupted,
            emits=frozenset({EventType.ATTEMPT_INTERRUPTED}),
            idempotency_key=lambda a: f"mark-interrupted:{a.attempt_id}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.MarkStalled,
            kind="mark-stalled",
            handler=effector.mark_stalled,
            emits=frozenset({EventType.ATTEMPT_STALLED}),
            idempotency_key=lambda a: f"mark-stalled:{a.attempt_id}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.StallCheck,
            kind="stall-check",
            handler=effector.stall_check,
            emits=frozenset(),
            # No key: the effect is empty, so a second execution has nothing
            # to collide with. Stated rather than invented.
            idempotency_key=None,
        ),
        effects.HandlerSpec(
            action_type=reconcile.OpenWave,
            kind="open-wave",
            handler=effector.open_wave,
            emits=frozenset({EventType.WAVE_OPENED}),
            # No key: opening a wave MINTS identity, so re-execution is a
            # genuinely new wave rather than a duplicate of the old one. The
            # planner's own has-open-wave guard is what prevents that; the
            # effect boundary cannot answer it from the action alone.
            idempotency_key=None,
        ),
        effects.HandlerSpec(
            action_type=reconcile.SpecAttention,
            kind="spec-attention",
            handler=effector.spec_attention,
            emits=frozenset({EventType.SPEC_ATTENTION}),
            idempotency_key=lambda a: f"spec-attention:{a.reason}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.ProviderPause,
            kind="provider-pause",
            handler=effector.provider_pause_action,
            emits=frozenset({EventType.PROVIDER_STATE_CHANGED,
                             EventType.NEEDS_OPERATOR}),
            idempotency_key=lambda a: f"provider-pause:{a.route_id}",
        ),
    )
