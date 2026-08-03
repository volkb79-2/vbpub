"""CR-05e: what an agent leg's exit MEANS.

The consumption paths are covered end to end by `test_daemon.py`,
`test_carver.py` and `test_reviewer_repair.py`. What is here is the STALE-exit
refusals and the read seams those cannot reach cheaply -- and a stale exit is
the sharp case: consuming one re-applies a verdict the task has already moved
past, which is how a task ends up in a state nobody planned.
"""

from __future__ import annotations

import dataclasses
import subprocess

import pytest

from nyxloom import (
    daemon, effects, effects_carve, effects_carver, effects_exit,
    effects_lifecycle, reconcile, storage,
)
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Event, EventType, Role, Route,
    TaskState, TaskStateFile, utc_now,
)


def _effector(ports=None):
    ports = ports or effects.EffectPorts.system()
    carver = effects_carver.CarverEffector(ports)
    lifecycle = effects_lifecycle.LifecycleEffector(
        ports, effects.ProviderPauseRegistry(ports.clock))
    carve = effects_carve.CarveEffector(ports, carver, lifecycle)
    return effects_exit.ExitEffector(ports, carve, carver, lifecycle)


def _event(kind: EventType, task_id=None, attempt_id=None, **payload) -> Event:
    return Event(schema_version=storage.SCHEMA_VERSION, sequence=1,
                 timestamp=utc_now(), project="demo",
                 actor=Actor(ActorKind.TICK, "t"), type=kind, payload=payload,
                 task_id=task_id, attempt_id=attempt_id)


class TestRequireEvents:

    def test_a_handed_down_log_is_reused_rather_than_re_read(self):
        """A caller INSIDE a pass already holds the log. Re-reading would not
        be wrong, but it would make the consumer's answer depend on writes the
        same pass made after the snapshot it was planned from."""
        given = [_event(EventType.REVIEW_RECORDED, task_id="t1")]
        assert _effector()._require_events("demo", given) is given

    def test_a_caller_outside_a_pass_gets_a_real_read(self, tmp_state,
                                                      sample_project):
        """Not an empty list: at the effect boundary an unread log has to be
        an error, not a benign "nothing recorded"."""
        storage.append_and_apply("demo", {}, actor=Actor(ActorKind.TICK, "t"),
                                 type=EventType.REVIEW_RECORDED,
                                 payload={"result": "rejected"}, task_id="t1")
        assert len(_effector()._require_events("demo")) == 1


class TestPreReviewSha:

    def test_a_repair_baseline_that_cannot_be_resolved_is_absent(self):
        """A missing baseline INVALIDATES an unverifiable repair rather than
        silently trusting it -- so this must answer None, never a guess."""
        assert _effector()._pre_review_sha("demo", "t1", "att-1",
                                           events=[]) is None


class TestCrosscheckHeadCommit:
    """Git state is truth, receipts lie. A receipt has been observed reporting
    a null head commit while the branch held real work, so the receipt is
    cross-checked BEFORE it is used -- and the cross-check itself must refuse
    to guess when git cannot answer."""

    def _receipt(self):
        from nyxloom.types import Receipt, ReceiptResult
        return Receipt(result=ReceiptResult.DONE, exit_code=0)

    def test_an_unresolvable_task_branch_leaves_the_receipt_alone(
            self, tmp_state, sample_project):
        receipt = self._receipt()
        _effector()._crosscheck_head_commit(sample_project, "no-such-task", receipt)
        assert receipt.head_commit is None

    def test_an_unresolvable_default_branch_leaves_the_receipt_alone(
            self, tmp_state, sample_project):
        """The task branch resolves but the DEFAULT branch does not, so there
        is nothing to compare against. Substituting the branch sha anyway
        would report every commit on it as this attempt's work."""
        cfg = dataclasses.replace(sample_project, default_branch="no-such-base")
        subprocess.run(["git", "-C", str(cfg.root), "branch", "feat/t-cc"],
                       capture_output=True, text=True)
        receipt = self._receipt()
        _effector()._crosscheck_head_commit(cfg, "t-cc", receipt)
        assert receipt.head_commit is None


class TestSelfReviewStaleExit:

    def test_a_self_review_exit_for_a_task_that_moved_on_is_refused(
            self, tmp_state, sample_project):
        """The task is no longer SELF_REVIEWING, so this exit is stale or
        superseded. Consuming it would transition a task off a state it
        already left -- re-applying a verdict the pipeline has moved past.
        """
        task_id, attempt_id = "t-stale", "att-stale"
        tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION,
                            task_id=task_id, project="demo",
                            state=TaskState.CARVED, since=utc_now())
        storage.append_and_apply("demo", {}, actor=Actor(ActorKind.TICK, "t"),
                                 type=EventType.TASK_CREATED,
                                 payload={"statefile": tsf.to_dict()},
                                 task_id=task_id)
        attempt = Attempt(attempt_id=attempt_id, role=Role.SELF_REVIEW,
                          state=AttemptState.EXITED,
                          route=Route(route_id="r1", cli="fake", model="m"),
                          started=utc_now())
        storage.append_and_apply("demo", {}, actor=Actor(ActorKind.TICK, "t"),
                                 type=EventType.ATTEMPT_CREATED,
                                 payload={"attempt": attempt.to_dict()},
                                 task_id=task_id, attempt_id=attempt_id)
        states = storage.list_states("demo")
        # CARVED, not SELF_REVIEWING -- the leg's task has moved on.
        assert states[task_id].state is TaskState.CARVED

        d = daemon.Daemon({"demo": sample_project.root})
        from nyxloom import paths
        ad = paths.attempt_dir("demo", attempt_id)
        ad.mkdir(parents=True, exist_ok=True)
        (ad / "receipt.json").write_text('{"result": "done", "exit_code": 0}',
                                         encoding="utf-8")
        ctx = d._effect_context("demo", sample_project, states)
        events = d._exit.emit_attempt_exit(
            ctx, reconcile.EmitAttemptExit(task_id=task_id,
                                           attempt_id=attempt_id))
        assert not [e for e in events if e.type is EventType.TASK_TRANSITIONED]
        assert storage.load_state("demo", task_id).state is TaskState.CARVED
