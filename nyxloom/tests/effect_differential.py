"""Differential verification for the effect boundary (amendment section 5.1).

The amendment requires CR-05 to prove more than "the tests we wrote still
pass": "identical action in, identical typed result and event sequence out".
The characterization corpus proves the cases someone thought to write down;
this proves the cases nobody did, for the families this package moved.

HOW IT WORKS
------------
This module is DELIBERATELY free of any reference to the new effect boundary.
It drives ``Daemon._execute`` -- the entry point that exists identically
before and after CR-05a -- over a fixed scenario list, and records the event
sequence each action produces. So the SAME file runs against both trees:

* on the pre-CR-05a tree, ``_execute`` is the isinstance ladder;
* on this tree, ``_execute`` is a registry lookup into an effector module.

The recorded transcript from the OLD tree is committed as
``tests/fixtures/effect_transcripts_v1.json`` and asserted against the NEW
one. Any difference is either explained in the package report or is a defect
-- and under the program's stop-loss, an unexplained difference stops the
package rather than being written down as expected.

Volatile values (minted ids, wall-clock stamps, temp paths) are normalized
rather than dropped: a field whose SHAPE changes still fails, only its
particular value is allowed to differ between runs.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from nyxloom import daemon, paths, reconcile, storage
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Blocker, BlockerType, Event,
    EventType, Role, Route, TaskState, TaskStateFile, utc_now,
)

TRANSCRIPT_VERSION = 1

_ID = re.compile(r"\b(att|wave|task|ev)-[0-9a-zA-Z]{4,}\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?")


def _normalize(value: Any, root: str) -> Any:
    """Replace values that legitimately differ per run with their shape."""
    if isinstance(value, str):
        out = value.replace(root, "<root>")
        out = _ISO.sub("<ts>", out)
        out = _ID.sub(lambda m: f"<{m.group(1)}-id>", out)
        return out
    if isinstance(value, dict):
        return {k: _normalize(v, root) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(v, root) for v in value]
    return value


def _event_row(ev: Event, root: str) -> dict:
    return {
        "type": ev.type.value,
        "task_id": _normalize(ev.task_id, root),
        "attempt_id": _normalize(ev.attempt_id, root),
        "wave_id": _normalize(ev.wave_id, root),
        "payload": _normalize(ev.payload, root),
    }


# ---------------------------------------------------------------------------
# scenario construction
#
# Each scenario seeds a project to a known state, then executes ONE action.
# Seeding goes through the store rather than through a hand-built dict: CR-04a
# made the projection derive from committed state, so a task that exists only
# in a caller's map cannot receive a transition.


#: How to reach each seeded state from CARVED, walking only LEGAL edges. The
#: store validates every transition against committed state (CR-04a), so a
#: seed cannot shortcut the graph -- which is a feature here: the differential
#: exercises effects on states the lifecycle can actually produce.
_SEED_PATHS: dict[TaskState, tuple[TaskState, ...]] = {
    TaskState.CARVED: (),
    TaskState.QUEUED: (TaskState.QUEUED,),
    TaskState.ACTIVE: (TaskState.QUEUED, TaskState.ACTIVE),
    TaskState.AWAITING_REVIEW: (TaskState.QUEUED, TaskState.ACTIVE,
                                TaskState.AWAITING_REVIEW),
    TaskState.VALIDATING: (TaskState.QUEUED, TaskState.ACTIVE,
                           TaskState.AWAITING_REVIEW, TaskState.MERGE_READY,
                           TaskState.MERGED, TaskState.VALIDATING),
}


def _seed_task(project: str, task_id: str, state: TaskState) -> None:
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id,
                        project=project, state=TaskState.CARVED,
                        since=utc_now(), handoff_path="handoff/x.md")
    storage.append_and_apply(project, {}, actor=Actor(ActorKind.TICK, "seed"),
                             type=EventType.TASK_CREATED,
                             payload={"statefile": tsf.to_dict()},
                             task_id=task_id)
    current = TaskState.CARVED
    for nxt in _SEED_PATHS[state]:
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.TICK, "seed"),
            type=EventType.TASK_TRANSITIONED,
            payload={"from": current.value, "to": nxt.value, "notes": "seed"},
            task_id=task_id)
        current = nxt


def _seed_attempt(project: str, task_id: str, attempt_id: str) -> None:
    attempt = Attempt(attempt_id=attempt_id, role=Role.IMPLEMENTER,
                      state=AttemptState.RUNNING,
                      route=Route(route_id="r1", cli="fake", model="m",
                                  variant="v", effort="low", routes_rev="rev1"),
                      started=utc_now(), worktree="/tmp/wt", branch="feat/x")
    storage.append_and_apply(project, {}, actor=Actor(ActorKind.TICK, "seed"),
                             type=EventType.ATTEMPT_CREATED,
                             payload={"attempt": attempt.to_dict()},
                             task_id=task_id, attempt_id=attempt_id)


def _scenarios() -> list[dict]:
    """Name -> how to seed, and the action to execute.

    Ordered and explicit rather than generated, so adding a family to the
    differential is a visible edit and dropping one cannot happen silently.
    """
    return [
        {
            "name": "create-task",
            "seed": lambda p: None,
            "action": lambda: reconcile.CreateTask(
                task_id="demo-D01", handoff_path="handoff/demo-D01.md"),
        },
        {
            "name": "transition-plain",
            "seed": lambda p: _seed_task(p, "demo-D02", TaskState.CARVED),
            "action": lambda: reconcile.Transition(
                task_id="demo-D02", to=TaskState.QUEUED, notes="ready"),
        },
        {
            "name": "transition-noop-same-state",
            "seed": lambda p: _seed_task(p, "demo-D03", TaskState.QUEUED),
            "action": lambda: reconcile.Transition(
                task_id="demo-D03", to=TaskState.QUEUED, notes="again"),
        },
        {
            "name": "transition-typed-blocker",
            "seed": lambda p: _seed_task(p, "demo-D04", TaskState.QUEUED),
            "action": lambda: reconcile.Transition(
                task_id="demo-D04", to=TaskState.BLOCKED, notes="stuck",
                blocker=Blocker(type=BlockerType.CONTRACT,
                                unblock_condition="triage", detail="why")),
        },
        {
            "name": "mark-interrupted",
            "seed": lambda p: (_seed_task(p, "demo-D05", TaskState.ACTIVE),
                               _seed_attempt(p, "demo-D05", "att-d05")),
            "action": lambda: reconcile.MarkInterrupted(
                task_id="demo-D05", attempt_id="att-d05"),
        },
        {
            "name": "mark-stalled",
            "seed": lambda p: (_seed_task(p, "demo-D06", TaskState.ACTIVE),
                               _seed_attempt(p, "demo-D06", "att-d06")),
            "action": lambda: reconcile.MarkStalled(
                task_id="demo-D06", attempt_id="att-d06"),
        },
        {
            "name": "stall-check",
            "seed": lambda p: (_seed_task(p, "demo-D07", TaskState.ACTIVE),
                               _seed_attempt(p, "demo-D07", "att-d07")),
            "action": lambda: reconcile.StallCheck(
                task_id="demo-D07", attempt_id="att-d07"),
        },
        {
            "name": "interrupt-attempt-no-pid-files",
            "seed": lambda p: (_seed_task(p, "demo-D08", TaskState.ACTIVE),
                               _seed_attempt(p, "demo-D08", "att-d08")),
            "action": lambda: reconcile.InterruptAttempt(
                task_id="demo-D08", attempt_id="att-d08"),
        },
        {
            "name": "interrupt-attempt-unparseable-pid",
            "seed": lambda p: (
                _seed_task(p, "demo-D09", TaskState.ACTIVE),
                _seed_attempt(p, "demo-D09", "att-d09"),
                _write_pid(p, "att-d09", "wrapper.pid", "not-a-pid"),
                _write_pid(p, "att-d09", "child.pid", "also-not-a-pid"),
            ),
            "action": lambda: reconcile.InterruptAttempt(
                task_id="demo-D09", attempt_id="att-d09"),
        },
        {
            "name": "open-wave",
            "seed": lambda p: _seed_task(p, "demo-D10", TaskState.AWAITING_REVIEW),
            "action": lambda: reconcile.OpenWave(task_ids=["demo-D10"]),
        },
        {
            "name": "spec-attention-first",
            "seed": lambda p: None,
            "action": lambda: reconcile.SpecAttention(
                reason="ratchet", detail="no progress"),
        },
        {
            "name": "spec-attention-debounced",
            "seed": lambda p: storage.append_and_apply(
                p, {}, actor=Actor(ActorKind.TICK, "seed"),
                type=EventType.SPEC_ATTENTION,
                payload={"reason": "rejections", "detail": "earlier"}),
            "action": lambda: reconcile.SpecAttention(
                reason="rejections", detail="again"),
        },
        {
            "name": "provider-pause",
            "seed": lambda p: _seed_task(p, "demo-D11", TaskState.QUEUED),
            "action": lambda: reconcile.ProviderPause(
                task_id="demo-D11", route_id="r1"),
        },
        {
            "name": "verify-gate-dispatch",
            "seed": lambda p: None,
            "action": lambda: reconcile.VerifyGate(project="demo"),
        },
        {
            "name": "post-merge-gate-dispatch",
            "seed": lambda p: _seed_task(p, "demo-D12", TaskState.VALIDATING),
            "action": lambda: reconcile.RunPostMergeGate(task_id="demo-D12"),
        },
    ]


def _write_pid(project: str, attempt_id: str, name: str, text: str) -> None:
    attempt_dir = paths.attempt_dir(project, attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / name).write_text(text, encoding="utf-8")


class _InertThread:
    """Stands in for a background worker so a dispatcher scenario records the
    DISPATCH, not a real gate run. Patched at ``threading.Thread``, which both
    trees reach through -- the pre-CR-05a daemon constructs one directly and
    the effector constructs one inside its background port."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        return None


def record(project: str, cfg: Any, monkeypatch: Any) -> dict:
    """Run every scenario and return the transcript.

    One fresh ``Daemon`` per scenario: the families under test own in-memory
    bookkeeping (background handles, provider backoff), and sharing a daemon
    would let one scenario's state decide another's outcome -- which is
    exactly the coupling this package exists to remove, so the differential
    must not depend on it.
    """
    monkeypatch.setattr(threading, "Thread", _InertThread)
    root = str(cfg.root)
    out: dict[str, Any] = {"version": TRANSCRIPT_VERSION, "scenarios": {}}
    for scenario in _scenarios():
        scenario["seed"](project)
        d = daemon.Daemon({project: cfg.root})
        action = scenario["action"]()
        states = storage.list_states(project)
        events = d._execute(project, cfg, states, action)
        out["scenarios"][scenario["name"]] = [_event_row(ev, root) for ev in events]
    return out
