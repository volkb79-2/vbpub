"""The effect boundary: ports, the handler registry, and the effect context.

WHY THIS MODULE EXISTS (CR-05a, DR-06)
--------------------------------------
``Daemon`` is one class with 155 methods over shared mutable instance state,
and ``Daemon._execute`` is a 1,090-line ``isinstance`` ladder that both DECIDES
what an action means and PERFORMS it. Two consequences the redesign is here to
remove:

* an effect cannot be exercised without constructing the whole shell, so tests
  reach for private methods and the daemon's internal dicts -- which is why
  ``test_daemon.py`` mirrors implementation structure;
* there is no place to say what an action is ALLOWED to do. A handler that
  quietly appends a different event, or launches a second copy of background
  work, looks exactly like one that does not.

The amendment's section 3.3 is explicit that moving code is not enough: "six
modules that all reach back into one god object ... passes the acceptance
criteria and delivers nothing". So the boundary this module defines is a
CAPABILITY boundary, not a file boundary.

THE THREE PIECES
----------------
:class:`EffectPorts`
    Every ambient capability an effector may use -- clock, processes, git,
    the event log, and the journal that appends events. An effector reaches
    the outside world through these and nowhere else, so a test substitutes a
    recorder and gets a transcript of exactly what the effect did. That
    transcript is what the amendment's section 5.1 differential verification
    compares: identical action in, identical typed result and event sequence
    out.

:class:`EffectContext`
    The per-action working set: project, config, the caller's state cache, the
    ports, and the pass's event log. It carries :meth:`EffectContext.append`
    and :meth:`EffectContext.transition` so every effector writes through ONE
    seam.

:class:`EffectRegistry`
    Action type -> :class:`HandlerSpec`. Exactly one handler owns each action
    type; a duplicate registration and an unknown action are both errors, and
    completeness is checked when the daemon builds the registry -- so an
    action class added to ``reconcile.py`` with no owner fails at startup
    rather than at the first pass that plans it.

WHAT A HANDLER SPEC DECLARES
----------------------------
Beyond the callable: the event types the handler may emit, and its idempotency
key. Both are load-bearing rather than documentation.

* ``emits`` is checked STRUCTURALLY by ``tests/test_effects.py``, which walks
  the handler's own call graph with ``ast`` and compares the ``EventType``
  members it can reach against what it declared. A handler that starts
  emitting a new event type fails until it says so.
* ``idempotency_key`` is what makes "re-executing every effect is idempotent
  or yields a typed conflict" checkable. A handler that starts background work
  (:attr:`HandlerSpec.starts_background_work`) MUST have one, and the key is
  the identity its owning effector uses to refuse a second copy.

THE LEGACY RATCHET
------------------
CR-05a moves the smaller route families and the two background-work
registries. The attempt, review, carve and merge families are CR-05b's, and
until they move their handlers still live on the shell and close over
``Daemon``. Rather than leave that undeclared, such a spec sets
:attr:`HandlerSpec.legacy_owner`, and ``tests/test_effects.py`` holds the count
to :data:`LEGACY_HANDLER_BUDGET` in BOTH directions -- over budget means new
debt, under budget means debt repaid without recording it. This is the same
ratchet shape ``exception_census.py`` uses, for the same reason: a number that
only goes down survives a package boundary, where a hand-maintained list does
not.

The effector MODULES are ``Daemon``-free by construction: no ``effects*.py``
module may import ``daemon`` or name ``Daemon``, and that too is a test.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from . import notify, snapshot, storage
from .config import ProjectConfig
from .log import get_logger
from .types import (
    Actor, ActorKind, Event, EventType, TaskState, TaskStateFile, utc_now,
)

log = get_logger("effects")

#: Handlers still implemented on ``Daemon`` rather than in an effector module,
#: with the package that owns moving them. CR-05a moved the lifecycle and gate
#: families; CR-05b owns the rest (attempt dispatch/resume, review launch and
#: exit consumption, carve and carver-session execution, auto-merge).
#:
#: This number may only go DOWN, and it must go down in the same commit that
#: moves the handler. See the module docstring.
LEGACY_HANDLER_BUDGET = 12


# ---------------------------------------------------------------------------
# ports
#
# These are concrete classes rather than Protocols on purpose. A Protocol's
# method stubs are unexecuted lines that a coverage gate correctly reports as
# uncovered, and paying for that with `pragma: no cover` would put an
# exclusion marker on the very surface this package exists to make visible.
# Substitution is by duck typing: a test passes any object with the same
# methods, which is what every port test below does.


class SystemClock:
    """Wall and monotonic time. Injected so a test can pin either."""

    def now(self) -> datetime:
        return utc_now()

    def monotonic(self) -> float:
        return time.monotonic()


class SystemProcesses:
    """Subprocess execution and signal delivery."""

    def run(self, argv: Sequence[str], *, cwd: str | None = None,
            timeout: float | None = None) -> subprocess.CompletedProcess:
        """Run ``argv`` captured and decoded.

        Errors are NOT swallowed here: ``subprocess.TimeoutExpired`` and
        ``OSError`` propagate to the caller, because what a timeout or a
        missing executable MEANS differs per effect (a gate timeout is exit
        124; a missing gate binary is exit 127) and the port must not choose.
        """
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout)

    def terminate(self, pid: int) -> None:
        """SIGTERM one process."""
        os.kill(pid, signal.SIGTERM)

    def terminate_group(self, pid: int) -> None:
        """SIGTERM the process GROUP that ``pid`` belongs to."""
        os.killpg(os.getpgid(pid), signal.SIGTERM)


class SystemFilesystem:
    """The reads an effector performs against paths the daemon does not own.

    Deliberately read-only: nothing at this boundary needs to create a file,
    and a port that cannot write is one fewer way for an effector to acquire
    durable authority outside the journal.
    """

    def exists(self, path: Path) -> bool:
        return path.exists()

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")


class SystemGit:
    """The git operations the effect boundary performs, named.

    Naming them is the point: a raw ``subprocess.run(["git", ...])`` inside an
    effector is indistinguishable from any other subprocess in a transcript,
    and "which refs did this effect move" is exactly the question a reviewer
    of an auto-revert path needs answered.
    """

    def __init__(self, processes: Any) -> None:
        self._processes = processes

    def run(self, root: str, *args: str,
            timeout: float | None = None) -> subprocess.CompletedProcess:
        return self._processes.run(["git", "-C", root, *args], timeout=timeout)

    def top_level(self, root: str) -> str:
        """``git rev-parse --show-toplevel``, unbounded and NOT total.

        A failure to exec git propagates. That is deliberate and differs from
        :meth:`top_level_or` below: the post-merge gate's caller runs on a
        background thread whose death is self-healing (the task stays
        VALIDATING and the trigger re-fires next pass), whereas substituting
        an empty root there would silently run the gate against the
        operator's live checkout instead of the merge commit.
        """
        return self.run(root, "rev-parse", "--show-toplevel").stdout.strip()

    def top_level_or(self, root: str, fallback: str, *, timeout: float) -> str:
        """``git rev-parse --show-toplevel``, bounded and total.

        Any failure -- non-zero exit, empty output, a timeout, or no git at
        all -- yields ``fallback``. Used where the value is a {worktree}
        substitution whose only reasonable default is the project root, and
        where a non-git test fixture is an expected case rather than a fault.
        """
        try:
            res = self.run(root, "rev-parse", "--show-toplevel", timeout=timeout)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return fallback

    def head_commit(self, root: str) -> str:
        """``git rev-parse HEAD``, or ``""`` when the call fails."""
        res = self.run(root, "rev-parse", "HEAD")
        return res.stdout.strip() if res.returncode == 0 else ""

    def rev_parse(self, root: str, rev: str) -> str:
        """Resolve ``rev``, or ``""`` when it does not exist.

        ``--verify --quiet`` so an unresolvable rev is an empty answer rather
        than noise on stderr; the caller decides what absence means.
        """
        return self.run(root, "rev-parse", "--verify", "--quiet", rev).stdout.strip()

    def worktree_add_detached(self, root: str, path: str, commit: str) -> bool:
        """Add a detached worktree at ``commit``; False if git refused."""
        return self.run(root, "worktree", "add", "--detach", path,
                        commit).returncode == 0

    def worktree_remove(self, root: str, path: str) -> None:
        """Force-remove a worktree. Best effort: the caller is cleaning up."""
        self.run(root, "worktree", "remove", "--force", path)

    def update_ref(self, root: str, ref: str, new: str,
                   old: str) -> subprocess.CompletedProcess:
        """Compare-and-swap ``ref`` from ``old`` to ``new``.

        The CAS operand is what makes an auto-revert safe: git refuses when
        the ref no longer points at ``old``, so a newer commit that landed in
        between is never clobbered. The whole result is returned because the
        caller reports the refusal, not just detects it.
        """
        return self.run(root, "update-ref", ref, new, old)


class ThreadBackground:
    """Background work as a port.

    An effector that starts work off the reconcile thread does it through
    here, so a test can run the work inline (or not at all) without patching
    ``threading`` globally.
    """

    def spawn(self, name: str, target: Callable[..., None],
              args: tuple) -> threading.Thread:
        t = threading.Thread(target=target, args=args, name=name, daemon=True)
        t.start()
        return t

    def is_running(self, handle: Any) -> bool:
        """Whether a handle from :meth:`spawn` is still running.

        ``None`` -- nothing was ever started for this key -- answers False,
        so the caller asks one question instead of two.
        """
        return handle is not None and handle.is_alive()


class StoreEventLog:
    """A fresh typed acquisition of a project's event log.

    Always fresh, never the pass's phase-A copy. An effector asks this to
    decide whether an escalation is already open, and the pass has been
    appending events since phase A -- answering from the stale copy would
    re-emit an escalation the same pass just recorded.

    AUTHORITATIVE, so an unreadable log raises rather than handing back a
    benign-looking empty one: "nothing escalated yet" is precisely the answer
    that reproduces the notification storm the debounce exists to stop.
    """

    def require(self, project: str) -> Sequence[Event]:
        return snapshot.acquire(
            "event_log", snapshot.InputClass.AUTHORITATIVE,
            lambda: tuple(storage.iter_events(project)),
            provenance=snapshot.Provenance("event-log", project)).require()


class StoreJournal:
    """The one seam through which an effect becomes a recorded event.

    Append and notify, in that order, with notification failure contained:
    the event is already committed by then, so a dead transport cannot turn a
    recorded effect into an unrecorded one.
    """

    def append(self, project: str, cfg: ProjectConfig,
               states: dict[str, TaskStateFile], ev_type: EventType,
               payload: dict[str, Any], **kw: Any) -> Event:
        ev = storage.append_and_apply(
            project, states, actor=Actor(ActorKind.TICK, "nyxloomd"),
            type=ev_type, payload=payload, **kw,
        )
        try:
            notify.notify_event(cfg, states, ev)
        except Exception:  # census: cleanup/containment (CR-02b)
            pass
        return ev

    def transition(self, project: str, cfg: ProjectConfig,
                   states: dict[str, TaskStateFile], task_id: str,
                   to: TaskState, notes: str | None) -> Event:
        frm = states[task_id].state
        # Every state transition funnels through this ONE helper, so one INFO
        # call here covers the whole daemon's "state transitions -> INFO"
        # instrumentation requirement (P05a section 5).
        log.info("state-transition", project=project, task=task_id,
                 frm=frm.value, to=to.value)
        return self.append(project, cfg, states, EventType.TASK_TRANSITIONED,
                           {"from": frm.value, "to": to.value, "notes": notes},
                           task_id=task_id)


@dataclass(frozen=True)
class EffectPorts:
    """Every ambient capability an effector may use.

    Frozen: an effector may not swap its own ports mid-pass, which is what
    makes a recorded transcript a complete account of the effect.
    """

    clock: Any
    processes: Any
    git: Any
    files: Any
    background: Any
    event_log: Any
    journal: Any

    @classmethod
    def system(cls) -> "EffectPorts":
        """The real ports the daemon runs with."""
        processes = SystemProcesses()
        return cls(
            clock=SystemClock(),
            processes=processes,
            git=SystemGit(processes),
            files=SystemFilesystem(),
            background=ThreadBackground(),
            event_log=StoreEventLog(),
            journal=StoreJournal(),
        )


# ---------------------------------------------------------------------------
# the per-action context


@dataclass
class EffectContext:
    """One action's working set.

    ``states`` is the caller's projection CACHE, not the source of truth --
    CR-04a made the store derive the projection from committed state inside
    its own transaction and refresh this map afterwards. An effector therefore
    reads ``states`` for the premises it was planned from and writes through
    :meth:`append` / :meth:`transition`, never by editing the map.
    """

    project: str
    cfg: ProjectConfig
    states: dict[str, TaskStateFile]
    ports: EffectPorts
    #: The identity this action must not be executed twice under, computed by
    #: the registry from the spec's declared builder. An effector that
    #: suppresses duplicates uses THIS rather than keying on a field it picks
    #: itself, so the identity the registry declares and the identity the
    #: effector enforces cannot drift apart.
    idempotency_key: str | None = None

    def events(self) -> Sequence[Event]:
        return self.ports.event_log.require(self.project)

    def append(self, ev_type: EventType, payload: dict[str, Any],
               **kw: Any) -> Event:
        return self.ports.journal.append(self.project, self.cfg, self.states,
                                         ev_type, payload, **kw)

    def transition(self, task_id: str, to: TaskState,
                   notes: str | None) -> Event:
        return self.ports.journal.transition(self.project, self.cfg,
                                             self.states, task_id, to, notes)


# ---------------------------------------------------------------------------
# the registry


class DuplicateHandler(Exception):
    """Two handlers claimed the same action type."""


class UnownedAction(Exception):
    """An action reached the effect boundary with no registered handler.

    Distinct from a handler that refuses: nothing was executed and nothing is
    known about what should have been, which is a wiring defect rather than a
    runtime outcome.
    """


@dataclass(frozen=True)
class HandlerSpec:
    """What one action type's handler is, and what it is allowed to do."""

    action_type: type
    #: Stable name for logs and transcripts. Independent of the class name so
    #: renaming an action class does not silently rewrite history.
    kind: str
    handler: Callable[[EffectContext, Any], list[Event]]
    #: Event types this handler may append. Verified structurally against the
    #: handler's own call graph -- see the module docstring. ``None`` is legal
    #: ONLY on a legacy spec: an undeclared set is the honest answer while the
    #: handler still lives on the shell, where the call graph is the shell's.
    emits: frozenset[EventType] | None
    #: Builds the identity under which a second execution of the same action
    #: must be refused. ``None`` -- no builder at all -- is the honest answer
    #: for an effect that mints fresh identity each time (opening a wave) and
    #: so has nothing to collide with.
    idempotency_key: Callable[[Any], str] | None
    #: True when the handler hands work to a thread that outlives the pass.
    #: Such a handler MUST declare an idempotency key: it is the key its
    #: effector uses to refuse starting a second copy.
    starts_background_work: bool = False
    #: Called once per pass to turn this family's finished background work
    #: into events. It belongs to the SPEC, not to the shell's pass loop, so
    #: `emits` covers the whole family -- a dispatcher that appends nothing
    #: and a drain that appends everything are one declaration, and a new
    #: background family does not require editing `run_pass`.
    drain: Callable[["EffectContext"], list[Event]] | None = None
    #: Set to the owning package while the handler still lives on the shell.
    #: Empty means it lives in an effector module. See LEGACY_HANDLER_BUDGET.
    legacy_owner: str = ""

    def __post_init__(self) -> None:
        if self.starts_background_work and self.idempotency_key is None:
            raise ValueError(
                f"{self.kind}: a handler that starts background work must "
                "declare an idempotency key")
        if self.emits is None and not self.legacy_owner:
            raise ValueError(
                f"{self.kind}: a handler in an effector module must declare "
                "the event types it emits")


class EffectRegistry:
    """Action type -> handler. Exactly one owner each."""

    def __init__(self) -> None:
        self._specs: dict[type, HandlerSpec] = {}

    def register(self, spec: HandlerSpec) -> None:
        existing = self._specs.get(spec.action_type)
        if existing is not None:
            raise DuplicateHandler(
                f"{spec.action_type.__name__} is already owned by "
                f"{existing.kind!r}; {spec.kind!r} may not also claim it")
        self._specs[spec.action_type] = spec

    def spec_for(self, action: Any) -> HandlerSpec:
        spec = self._specs.get(type(action))
        if spec is None:
            raise UnownedAction(
                f"no registered handler for action type {type(action).__name__}")
        return spec

    def execute(self, ctx: EffectContext, action: Any) -> list[Event]:
        spec = self.spec_for(action)
        if spec.idempotency_key is not None:
            ctx = replace(ctx, idempotency_key=spec.idempotency_key(action))
        return spec.handler(ctx, action)

    def drain(self, ctx: EffectContext) -> list[Event]:
        """Drain every family's finished background work, in registration
        order. Called once per pass, on the reconcile thread."""
        events: list[Event] = []
        for spec in self._specs.values():
            if spec.drain is not None:
                events.extend(spec.drain(ctx))
        return events

    @property
    def specs(self) -> tuple[HandlerSpec, ...]:
        return tuple(self._specs.values())

    def require_covers(self, action_types: Sequence[type]) -> None:
        """Fail unless every action type has an owner.

        Called when the daemon builds its registry, so an action class added
        to the planner with no handler is a startup failure rather than a
        TICK_ERROR on the first pass that plans it.
        """
        missing = sorted(t.__name__ for t in action_types
                         if t not in self._specs)
        if missing:
            raise UnownedAction(
                "action types with no registered effect handler: "
                + ", ".join(missing))

    def legacy_specs(self) -> tuple[HandlerSpec, ...]:
        return tuple(s for s in self._specs.values() if s.legacy_owner)


# ---------------------------------------------------------------------------
# pure predicates shared by effectors and the input builder
#
# Both read the event log and answer "has this already been said recently".
# They are pure functions over a sequence so an effector can ask without a
# store, and so the answer cannot differ between the planner's copy and the
# executor's.


def spec_attention_recently_emitted(events: Sequence[Event],
                                    reason: str | None) -> bool:
    """Whether SPEC_ATTENTION{reason} appears in the recent window.

    Debounce backstop: a PERSISTENT condition would otherwise re-emit -- and
    re-notify -- every reconcile cycle forever. The window is the last 500
    events, matching the pre-CR-05 behaviour exactly.
    """
    recent = list(events)[-500:]
    return any(ev.type is EventType.SPEC_ATTENTION
               and ev.payload.get("reason") == reason for ev in recent)


def needs_operator_recently_emitted(events: Sequence[Event],
                                    reason: str) -> bool:
    """Whether NEEDS_OPERATOR{reason} is open since the last session clear.

    A carver session rotation or start clears the episode, so the scan runs
    forward from the most recent of those rather than over all history --
    otherwise a resolved escalation suppresses its own recurrence forever.
    """
    events = list(events)
    clear_idx = -1
    for i in range(len(events) - 1, -1, -1):
        t = events[i].type
        if t is EventType.CARVER_SESSION_ROTATED or t is EventType.CARVER_SESSION_STARTED:
            clear_idx = i
            break
    start = clear_idx + 1 if clear_idx >= 0 else 0
    for ev in events[start:]:
        if ev.type is EventType.NEEDS_OPERATOR and ev.payload.get("reason") == reason:
            return True
    return False


# ---------------------------------------------------------------------------
# provider pause: effector-owned state, injected where it is read


class ProviderPauseRegistry:
    """How long a route stays paused after a provider limit.

    Owned by the lifecycle effector that sets it and injected into the input
    builder that reads it, rather than living on the shell where both reach
    for it. Disposable in-memory state, rebuilt on restart -- a pause is a
    backoff, not a durable decision.
    """

    def __init__(self, clock: Any) -> None:
        self._clock = clock
        self._until: dict[str, float] = {}

    def pause(self, route_id: str, seconds: float) -> None:
        """Back ``route_id`` off for ``seconds`` from now.

        The duration is the CALLER's, read at call time, so the tunable stays
        a module constant a test can shrink rather than a value frozen into
        this object when the daemon was constructed.
        """
        self._until[route_id] = self._clock.monotonic() + seconds

    def paused_until(self, route_id: str) -> float | None:
        return self._until.get(route_id)
