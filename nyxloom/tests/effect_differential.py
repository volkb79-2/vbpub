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
import subprocess
import threading
from pathlib import Path
from typing import Any

from nyxloom import adapters, daemon, paths, reconcile, storage, wrapper
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Blocker, BlockerType, Event,
    EventType, GateResult, Role, Route, TaskState, TaskStateFile, utc_now,
)

TRANSCRIPT_VERSION = 1

_ID = re.compile(r"\b(att|wave|task|ev)-[0-9a-zA-Z]{4,}\b")
#: A git object name. Minted per run -- a commit hash depends on its own
#: timestamp -- so it normalizes to a placeholder that still asserts the field
#: is PRESENT and still sha-shaped. Full 40-hex only: a shorter pattern would
#: swallow ordinary hex-looking content and hide a real payload change.
_SHA = re.compile(r"\b[0-9a-f]{40}\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?")


def _normalize(value: Any, root: str, state_root: str = "") -> Any:
    """Replace values that legitimately differ per run with their shape.

    Two roots, not one. The project checkout and the STATE directory are
    different temporary trees, and an attempt record carries paths under both
    -- a log path under the state root normalized only against the project
    root still differs per run, which reads as a behavioural delta.
    """
    if isinstance(value, str):
        out = value
        if state_root:
            out = out.replace(state_root, "<state>")
        out = out.replace(root, "<root>")
        out = _ISO.sub("<ts>", out)
        out = _ID.sub(lambda m: f"<{m.group(1)}-id>", out)
        out = _SHA.sub("<sha>", out)
        return out
    if isinstance(value, dict):
        return {k: _normalize(v, root, state_root) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(v, root, state_root) for v in value]
    return value


def _event_row(ev: Event, root: str, state_root: str) -> dict:
    return {
        "type": ev.type.value,
        "task_id": _normalize(ev.task_id, root, state_root),
        "attempt_id": _normalize(ev.attempt_id, root, state_root),
        "wave_id": _normalize(ev.wave_id, root, state_root),
        "payload": _normalize(ev.payload, root, state_root),
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
    TaskState.MERGE_READY: (TaskState.QUEUED, TaskState.ACTIVE,
                            TaskState.AWAITING_REVIEW, TaskState.MERGE_READY),
    TaskState.REVIEW_REJECTED: (TaskState.QUEUED, TaskState.ACTIVE,
                                TaskState.AWAITING_REVIEW,
                                TaskState.REVIEW_REJECTED),
    TaskState.SELF_REVIEWING: (TaskState.QUEUED, TaskState.ACTIVE,
                               TaskState.SELF_REVIEWING),
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
            "seed": lambda p, cfg: None,
            "action": lambda: reconcile.CreateTask(
                task_id="demo-D01", handoff_path="handoff/demo-D01.md"),
        },
        {
            "name": "transition-plain",
            "seed": lambda p, cfg: _seed_task(p, "demo-D02", TaskState.CARVED),
            "action": lambda: reconcile.Transition(
                task_id="demo-D02", to=TaskState.QUEUED, notes="ready"),
        },
        {
            "name": "transition-noop-same-state",
            "seed": lambda p, cfg: _seed_task(p, "demo-D03", TaskState.QUEUED),
            "action": lambda: reconcile.Transition(
                task_id="demo-D03", to=TaskState.QUEUED, notes="again"),
        },
        {
            "name": "transition-typed-blocker",
            "seed": lambda p, cfg: _seed_task(p, "demo-D04", TaskState.QUEUED),
            "action": lambda: reconcile.Transition(
                task_id="demo-D04", to=TaskState.BLOCKED, notes="stuck",
                blocker=Blocker(type=BlockerType.CONTRACT,
                                unblock_condition="triage", detail="why")),
        },
        {
            "name": "mark-interrupted",
            "seed": lambda p, cfg: (_seed_task(p, "demo-D05", TaskState.ACTIVE),
                               _seed_attempt(p, "demo-D05", "att-d05")),
            "action": lambda: reconcile.MarkInterrupted(
                task_id="demo-D05", attempt_id="att-d05"),
        },
        {
            "name": "mark-stalled",
            "seed": lambda p, cfg: (_seed_task(p, "demo-D06", TaskState.ACTIVE),
                               _seed_attempt(p, "demo-D06", "att-d06")),
            "action": lambda: reconcile.MarkStalled(
                task_id="demo-D06", attempt_id="att-d06"),
        },
        {
            "name": "stall-check",
            "seed": lambda p, cfg: (_seed_task(p, "demo-D07", TaskState.ACTIVE),
                               _seed_attempt(p, "demo-D07", "att-d07")),
            "action": lambda: reconcile.StallCheck(
                task_id="demo-D07", attempt_id="att-d07"),
        },
        {
            "name": "interrupt-attempt-no-pid-files",
            "seed": lambda p, cfg: (_seed_task(p, "demo-D08", TaskState.ACTIVE),
                               _seed_attempt(p, "demo-D08", "att-d08")),
            "action": lambda: reconcile.InterruptAttempt(
                task_id="demo-D08", attempt_id="att-d08"),
        },
        {
            "name": "interrupt-attempt-unparseable-pid",
            "seed": lambda p, cfg: (
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
            "seed": lambda p, cfg: _seed_task(p, "demo-D10", TaskState.AWAITING_REVIEW),
            "action": lambda: reconcile.OpenWave(task_ids=["demo-D10"]),
        },
        {
            "name": "spec-attention-first",
            "seed": lambda p, cfg: None,
            "action": lambda: reconcile.SpecAttention(
                reason="ratchet", detail="no progress"),
        },
        {
            "name": "spec-attention-debounced",
            "seed": lambda p, cfg: storage.append_and_apply(
                p, {}, actor=Actor(ActorKind.TICK, "seed"),
                type=EventType.SPEC_ATTENTION,
                payload={"reason": "rejections", "detail": "earlier"}),
            "action": lambda: reconcile.SpecAttention(
                reason="rejections", detail="again"),
        },
        {
            "name": "provider-pause",
            "seed": lambda p, cfg: _seed_task(p, "demo-D11", TaskState.QUEUED),
            "action": lambda: reconcile.ProviderPause(
                task_id="demo-D11", route_id="r1"),
        },
        {
            "name": "verify-gate-dispatch",
            "seed": lambda p, cfg: None,
            "action": lambda: reconcile.VerifyGate(project="demo"),
        },
        {
            "name": "post-merge-gate-dispatch",
            "seed": lambda p, cfg: _seed_task(p, "demo-D12", TaskState.VALIDATING),
            "action": lambda: reconcile.RunPostMergeGate(task_id="demo-D12"),
        },
        # -- CR-05b: review dispatch and guarded merge -------------------
        {
            "name": "launch-review-wave",
            "seed": lambda p, cfg: (
                _branch_with_file(cfg.root, "feat/demo-D20", "d20.txt", "20\n"),
                _seed_task(p, "demo-D20", TaskState.AWAITING_REVIEW),
            ),
            "action": lambda: reconcile.LaunchReview(wave_id="wave-d20",
                                                     task_ids=["demo-D20"]),
        },
        {
            "name": "launch-review-empty-wave",
            "seed": lambda p, cfg: None,
            "action": lambda: reconcile.LaunchReview(wave_id="wave-empty",
                                                     task_ids=[]),
        },
        {
            "name": "launch-gate-diagnosis",
            "seed": lambda p, cfg: (
                _branch_with_file(cfg.root, "feat/demo-D21", "d21.txt", "21\n"),
                _seed_task(p, "demo-D21", TaskState.REVIEW_REJECTED),
                storage.append_and_apply(
                    p, {}, actor=Actor(ActorKind.TICK, "seed"),
                    type=EventType.GATE_FINISHED,
                    payload={"gate_result": GateResult(
                        gate_id="pytest-q", phase="pre-merge",
                        commit="c0ffee", exit_code=1, started=utc_now(),
                        ended=utc_now(), output_tail="FAILED tests").to_dict()},
                    task_id="demo-D21"),
            ),
            "action": lambda: reconcile.LaunchGateDiagnosis(task_id="demo-D21"),
        },
        {
            "name": "auto-merge-no-recorded-branch",
            "seed": lambda p, cfg: _seed_task(p, "demo-D22", TaskState.MERGE_READY),
            "action": lambda: reconcile.AutoMergeTask(task_id="demo-D22"),
        },
        {
            "name": "auto-merge-clean",
            "seed": lambda p, cfg: (
                _branch_with_file(cfg.root, "feat/demo-D23", "d23.txt", "23\n"),
                _seed_merge_ready(p, cfg, "demo-D23", "feat/demo-D23"),
            ),
            "action": lambda: reconcile.AutoMergeTask(task_id="demo-D23"),
        },
        # -- CR-05c: the three ways an agent leg starts ------------------
        {
            "name": "dispatch-implementer",
            "seed": lambda p, cfg: _seed_task(p, "demo-D30", TaskState.QUEUED),
            "action": lambda: reconcile.DispatchImplementer(
                task_id="demo-D30", route_id="fake-cli"),
        },
        {
            "name": "dispatch-implementer-admission-refused",
            "seed": lambda p, cfg: (
                _seed_task(p, "demo-D31", TaskState.QUEUED),
                _pause(p, "drain-agents"),
            ),
            "action": lambda: reconcile.DispatchImplementer(
                task_id="demo-D31", route_id="fake-cli"),
            "teardown": _unpause,
        },
        {
            "name": "resume-attempt",
            "seed": lambda p, cfg: (
                _seed_task(p, "demo-D32", TaskState.ACTIVE),
                _seed_attempt_full(p, "demo-D32", "att-d32",
                                   role=Role.IMPLEMENTER,
                                   state=AttemptState.INTERRUPTED,
                                   session="sess-d32",
                                   worktree=str(cfg.root),
                                   branch="feat/demo-D32"),
            ),
            "action": lambda: reconcile.ResumeAttempt(task_id="demo-D32",
                                                      attempt_id="att-d32"),
        },
        {
            "name": "launch-self-review-warm",
            "seed": lambda p, cfg: (
                _seed_task(p, "demo-D33", TaskState.SELF_REVIEWING),
                _seed_attempt_full(p, "demo-D33", "att-d33",
                                   role=Role.IMPLEMENTER,
                                   state=AttemptState.EXITED,
                                   session="sess-d33",
                                   worktree=str(cfg.root),
                                   branch="feat/demo-D33"),
            ),
            "action": lambda: reconcile.LaunchSelfReview(
                task_id="demo-D33", source_attempt_id="att-d33"),
        },
        {
            "name": "launch-self-review-no-warm-session",
            "seed": lambda p, cfg: (
                _seed_task(p, "demo-D34", TaskState.SELF_REVIEWING),
                _seed_attempt_full(p, "demo-D34", "att-d34",
                                   role=Role.IMPLEMENTER,
                                   state=AttemptState.EXITED, session=None),
            ),
            "action": lambda: reconcile.LaunchSelfReview(
                task_id="demo-D34", source_attempt_id="att-d34"),
        },
        # -- CR-05d: the carver session ---------------------------------
        {
            # Feature-off is the DEFAULT, and it must stay byte-identical:
            # `cfg.carve.session` is "fresh" for `sample_project`, so the
            # snapshot reader answers None and every verb refuses cleanly.
            "name": "start-carver-session-feature-off",
            "seed": lambda p, cfg: None,
            "action": lambda: reconcile.StartCarverSession(project="demo",
                                                           mode="headroom"),
        },
        {
            "name": "resume-carver-session-feature-off",
            "seed": lambda p, cfg: None,
            "action": lambda: reconcile.ResumeCarverSession(
                project="demo", mode="merge-feed", source_ids=("d1",),
                generation=1),
        },
        {
            "name": "compact-carver-session-feature-off",
            "seed": lambda p, cfg: None,
            "action": lambda: reconcile.CompactCarverSession(
                project="demo", generation=1, trigger="turns"),
        },
        {
            "name": "start-carver-session-admission-refused",
            "seed": lambda p, cfg: _pause(p, "drain-agents"),
            "action": lambda: reconcile.StartCarverSession(project="demo",
                                                           mode="headroom"),
            "teardown": _unpause,
        },
        {
            "name": "carve-dispatch-feature-off",
            "seed": lambda p, cfg: None,
            "action": lambda: reconcile.CarveDispatch(project="demo",
                                                      kind="headroom"),
        },
        {
            "name": "carve-dispatch-admission-refused",
            "seed": lambda p, cfg: _pause(p, "drain-handoffs"),
            "action": lambda: reconcile.CarveDispatch(project="demo",
                                                      kind="headroom"),
            "teardown": _unpause,
        },
        {
            "name": "admit-carve-proposal-feature-off",
            "seed": lambda p, cfg: None,
            "action": lambda: reconcile.AdmitCarveProposal(project="demo",
                                                           proposal_id="p1"),
        },
        {
            # The exit consumer's own refusal: an exit whose task is not in
            # the state the leg was dispatched from is STALE, and consuming
            # it would re-apply a verdict the task has already moved past.
            "name": "emit-attempt-exit-no-receipt",
            "seed": lambda p, cfg: (
                _seed_task(p, "demo-D40", TaskState.ACTIVE),
                _seed_attempt_full(p, "demo-D40", "att-d40",
                                   role=Role.IMPLEMENTER,
                                   state=AttemptState.RUNNING),
            ),
            "action": lambda: reconcile.EmitAttemptExit(task_id="demo-D40",
                                                        attempt_id="att-d40"),
            "expect_raises": True,
        },
        {
            "name": "auto-merge-conflict",
            "seed": lambda p, cfg: (
                _branch_with_file(cfg.root, "feat/demo-D24",
                                  "conflict.txt", "branch side\n"),
                _commit_on_main(cfg.root, "conflict.txt", "main side\n"),
                _seed_merge_ready(p, cfg, "demo-D24", "feat/demo-D24"),
            ),
            "action": lambda: reconcile.AutoMergeTask(task_id="demo-D24"),
        },
    ]


def _seed_attempt_full(project: str, task_id: str, attempt_id: str, *,
                       role: Role, state: AttemptState,
                       session: str | None = None,
                       worktree: str | None = None,
                       branch: str | None = None) -> None:
    """An attempt record with the fields a launch family actually reads.

    Resume needs a session handle and a worktree; self-review BORROWS both
    from the implementer's record, so a scenario that omits them exercises
    the graceful-degradation arm instead -- which is a different case, and
    one this file covers separately.
    """
    attempt = Attempt(attempt_id=attempt_id, role=role, state=state,
                      route=Route(route_id="fake-cli", cli="fake",
                                  model="fake-model"),
                      started=utc_now(), session_handle=session,
                      worktree=worktree, branch=branch)
    storage.append_and_apply(project, {}, actor=Actor(ActorKind.TICK, "seed"),
                             type=EventType.ATTEMPT_CREATED,
                             payload={"attempt": attempt.to_dict()},
                             task_id=task_id, attempt_id=attempt_id)


def _pause(project: str, mode: str) -> None:
    paths.pause_flag(project).write_text(mode, encoding="utf-8")


def _unpause(project: str) -> None:
    flag = paths.pause_flag(project)
    if flag.exists():
        flag.unlink()


def _commit_on_main(root, filename: str, body: str) -> None:
    """Diverge main on a file a branch also changed -- a REAL textual
    conflict, so the merge scenario exercises escalation rather than a
    fast-forward that happens to look like one."""
    (root / filename).write_text(body, encoding="utf-8")
    _git(root, "add", filename)
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "diverge on main")


#: Routes carrying a review-independent role. `sample_project` declares only
#: an implementer tier, and the review families resolve their route by ROLE --
#: so without this they would not dispatch at all and the scenarios would
#: record an empty sequence that proves nothing.
_REVIEW_ROUTES = """\
revision = "diff-rev"

[tiers.flash-high]
routes = ["fake-cli"]

[tiers.frontier-review]
routes = ["fake-review"]

[routes.fake-cli]
cli = "fake"
model = "fake-model"
probe = ["true"]
usage_source = "none"
# A resume template, which `sample_project`'s routes omit: without one
# `build_resume` raises and the resume and warm self-review scenarios would
# record an exception rather than an event sequence.
resume = ["fake", "--resume", "{session}", "--cwd", "{worktree}", "{prompt}"]

[routes.fake-review]
cli = "fake"
model = "fake-review-model"
role_default = "review-independent"
probe = ["true"]
usage_source = "none"
"""


def _git(root, *args) -> str:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True).stdout


def _branch_with_file(root, branch: str, filename: str, body: str) -> None:
    """A feature branch with one new file, leaving the checkout back on main."""
    _git(root, "checkout", "-q", "-b", branch)
    (root / filename).write_text(body, encoding="utf-8")
    _git(root, "add", filename)
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         f"add {filename}")
    _git(root, "checkout", "-q", "main")


def _seed_merge_ready(project: str, cfg, task_id: str, branch: str) -> None:
    """A MERGE_READY task carrying an implementer attempt on `branch`.

    The branch is what auto-merge merges, and it is recorded on the ATTEMPT
    rather than derived from the task id -- so a scenario that forgets it
    exercises the no-recorded-branch escalation instead.
    """
    _seed_task(project, task_id, TaskState.MERGE_READY)
    attempt = Attempt(attempt_id=f"att-{task_id}", role=Role.IMPLEMENTER,
                      state=AttemptState.EXITED,
                      route=Route(route_id="r1", cli="fake", model="m"),
                      started=utc_now(), branch=branch)
    storage.append_and_apply(project, {}, actor=Actor(ActorKind.TICK, "seed"),
                             type=EventType.ATTEMPT_CREATED,
                             payload={"attempt": attempt.to_dict()},
                             task_id=task_id, attempt_id=attempt.attempt_id)


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

    Two things are stubbed, and only two. Background threads become inert, so
    a dispatcher scenario records the DISPATCH rather than a real gate run;
    and the wrapper launch returns a fixed pid without forking, so a review
    scenario records the event sequence rather than the behaviour of a fake
    CLI. Everything else -- route resolution, packet assembly, prompt
    construction, git -- runs for real, because those are where a move would
    actually go wrong.
    """
    monkeypatch.setattr(threading, "Thread", _InertThread)
    launched: list[Any] = []

    def _fake_launch(spec):
        Path(spec.attempt_dir).mkdir(parents=True, exist_ok=True)
        launched.append(spec)
        return 4242

    monkeypatch.setattr(wrapper, "launch_detached", _fake_launch)
    paths.routes_path().write_text(_REVIEW_ROUTES, encoding="utf-8")
    root = str(cfg.root)
    state_root = str(paths.state_root())
    out: dict[str, Any] = {"version": TRANSCRIPT_VERSION, "scenarios": {}}
    for scenario in _scenarios():
        scenario["seed"](project, cfg)
        d = daemon.Daemon({project: cfg.root})
        action = scenario["action"]()
        states = storage.list_states(project)
        if scenario.get("expect_raises"):
            # Some effects RAISE on a premise the planner should have made
            # true. run_pass's per-action isolation records that as a
            # TICK_ERROR; the differential records the exception TYPE, since
            # which exception it is is part of the behaviour.
            try:
                events = d._execute(project, cfg, states, action)
            except Exception as exc:
                out["scenarios"][scenario["name"]] = [
                    {"raised": type(exc).__name__}]
                teardown = scenario.get("teardown")
                if teardown is not None:
                    teardown(project)
                continue
        else:
            events = d._execute(project, cfg, states, action)
        out["scenarios"][scenario["name"]] = [
            _event_row(ev, root, state_root) for ev in events]
        # A scenario that changes project-wide state (a pause flag) undoes it
        # here, so scenario ORDER cannot decide a later scenario's outcome.
        teardown = scenario.get("teardown")
        if teardown is not None:
            teardown(project)
    return out
