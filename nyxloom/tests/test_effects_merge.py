"""CR-05b: guarded-automatic merge.

The failure paths get the attention here, because they are the ones that
decide whether a task is left where an operator can rescue it. The happy path
is covered end to end by `test_auto_merge.py` against a real git repository,
and by the §5.1 differential; what those cannot easily reach is git refusing.
"""

from __future__ import annotations

import dataclasses
import subprocess

import pytest

from nyxloom import effects, effects_merge, reconcile, storage
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Role, Route, TaskState,
    TaskStateFile, utc_now,
)


class _Git:
    """A git port scripted per operation, recording what was asked of it."""

    def __init__(self, **answers) -> None:
        self.answers = answers
        self.calls: list[tuple] = []

    def _ok(self, name, default=None):
        return self.answers.get(name, default)

    def top_level_checked(self, root):
        self.calls.append(("top_level_checked", root))
        if isinstance(self._ok("top_level_checked", "/repo"), Exception):
            raise self.answers["top_level_checked"]
        return self._ok("top_level_checked", "/repo")

    def resolve(self, root, rev):
        self.calls.append(("resolve", root, rev))
        return self._ok("resolve", "abc1234")

    def changed_paths(self, root, commit):
        self.calls.append(("changed_paths", root, commit))
        return self._ok("changed_paths", [])

    def worktree_remove(self, root, path):
        self.calls.append(("worktree_remove", path))

    def worktree_add_detached(self, root, path, commit):
        self.calls.append(("worktree_add_detached", path, commit))
        return self._ok("worktree_add_detached",
                        subprocess.CompletedProcess([], 0, "", ""))

    def merge_no_ff(self, worktree, branch, message):
        self.calls.append(("merge_no_ff", worktree, branch))
        return self._ok("merge_no_ff", subprocess.CompletedProcess([], 0, "", ""))

    def merge_abort(self, worktree):
        self.calls.append(("merge_abort", worktree))

    def update_ref(self, root, ref, new, old):
        self.calls.append(("update_ref", ref, new, old))
        return self._ok("update_ref", subprocess.CompletedProcess([], 0, "", ""))


class _Files:
    def __init__(self, exists=False) -> None:
        self._exists = exists

    def exists(self, path):
        return self._exists


def _seed(project: str, task_id: str, *, branch: str | None) -> dict:
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id,
                        project=project, state=TaskState.CARVED,
                        since=utc_now(), handoff_path="handoff/x.md")
    storage.append_and_apply(project, {}, actor=Actor(ActorKind.TICK, "seed"),
                             type=EventType.TASK_CREATED,
                             payload={"statefile": tsf.to_dict()},
                             task_id=task_id)
    for frm, to in ((TaskState.CARVED, TaskState.QUEUED),
                    (TaskState.QUEUED, TaskState.ACTIVE),
                    (TaskState.ACTIVE, TaskState.AWAITING_REVIEW),
                    (TaskState.AWAITING_REVIEW, TaskState.MERGE_READY)):
        storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.TICK, "seed"),
            type=EventType.TASK_TRANSITIONED,
            payload={"from": frm.value, "to": to.value, "notes": None},
            task_id=task_id)
    if branch is not None:
        attempt = Attempt(attempt_id=f"att-{task_id}", role=Role.IMPLEMENTER,
                          state=AttemptState.EXITED,
                          route=Route(route_id="r1", cli="fake", model="m"),
                          started=utc_now(), branch=branch)
        storage.append_and_apply(project, {}, actor=Actor(ActorKind.TICK, "seed"),
                                 type=EventType.ATTEMPT_CREATED,
                                 payload={"attempt": attempt.to_dict()},
                                 task_id=task_id, attempt_id=attempt.attempt_id)
    return storage.list_states(project)


def _run(cfg, states, task_id, git, *, files=None, processes=None):
    ports = dataclasses.replace(effects.EffectPorts.system(), git=git,
                                files=files or _Files())
    if processes is not None:
        ports = dataclasses.replace(ports, processes=processes)
    effector = effects_merge.MergeEffector(ports)
    ctx = effects.EffectContext(project="demo", cfg=cfg, states=states,
                                ports=ports)
    return effector.auto_merge(ctx, reconcile.AutoMergeTask(task_id=task_id))


def _no_gates(cfg):
    return dataclasses.replace(cfg, gates={})


class TestEscalations:

    def test_a_task_with_no_recorded_implementer_branch_escalates(
            self, tmp_state, sample_project):
        """The branch lives on the ATTEMPT, not on the task id. Deriving it
        from the id would merge whatever happens to sit at that name."""
        states = _seed("demo", "t-nobranch", branch=None)
        git = _Git()
        events = _run(_no_gates(sample_project), states, "t-nobranch", git)
        assert [e.type for e in events] == [EventType.NEEDS_OPERATOR]
        assert events[0].payload["reason"] == "auto-merge-error"
        assert git.calls == [], "nothing may touch git before the branch is known"
        assert storage.load_state("demo", "t-nobranch").state is TaskState.MERGE_READY

    def test_an_unresolvable_repo_root_escalates_without_merging(
            self, tmp_state, sample_project):
        states = _seed("demo", "t-noroot", branch="feat/t-noroot")
        git = _Git(top_level_checked=None)
        events = _run(_no_gates(sample_project), states, "t-noroot", git)
        assert [e.type for e in events] == [EventType.NEEDS_OPERATOR]
        assert "could not resolve repo root" in events[0].payload["detail"]
        assert not any(c[0] == "merge_no_ff" for c in git.calls)

    def test_a_refused_scratch_worktree_escalates_with_git_s_own_reason(
            self, tmp_state, sample_project):
        """"scratch worktree setup failed" with no reason is a page nobody
        can act on."""
        states = _seed("demo", "t-noscratch", branch="feat/t-noscratch")
        git = _Git(worktree_add_detached=subprocess.CompletedProcess(
            [], 128, "", "fatal: 'x' already exists"))
        events = _run(_no_gates(sample_project), states, "t-noscratch", git)
        assert [e.type for e in events] == [EventType.NEEDS_OPERATOR]
        assert "already exists" in events[0].payload["detail"]

    def test_a_leftover_scratch_worktree_is_cleared_before_adding(
            self, tmp_state, sample_project):
        states = _seed("demo", "t-leftover", branch="feat/t-leftover")
        git = _Git()
        _run(_no_gates(sample_project), states, "t-leftover", git,
             files=_Files(exists=True))
        ops = [c[0] for c in git.calls]
        assert ops.index("worktree_remove") < ops.index("worktree_add_detached"), (
            "a leftover scratch worktree must be cleared BEFORE adding over "
            f"it, else the add fails and the gate runs on the wrong tree: {ops}")

    def test_a_real_conflict_aborts_cleans_up_and_escalates(
            self, tmp_state, sample_project):
        states = _seed("demo", "t-conflict", branch="feat/t-conflict")
        git = _Git(merge_no_ff=subprocess.CompletedProcess([], 1, "CONFLICT", ""))
        events = _run(_no_gates(sample_project), states, "t-conflict", git)
        assert [e.type for e in events] == [EventType.NEEDS_OPERATOR]
        assert events[0].payload["reason"] == "auto-merge-conflict"
        assert ("merge_abort", "/repo/.worktrees/automerge-t-conflict") in git.calls
        assert not any(c[0] == "update_ref" for c in git.calls)
        assert storage.load_state("demo", "t-conflict").state is TaskState.MERGE_READY

    def test_a_refused_compare_and_swap_escalates_and_publishes_nothing(
            self, tmp_state, sample_project):
        """git refuses when the branch no longer points at the value read at
        the start, which means something else merged in between. Never force
        over it."""
        states = _seed("demo", "t-race", branch="feat/t-race")
        git = _Git(update_ref=subprocess.CompletedProcess([], 1, "", "stale ref"))
        events = _run(_no_gates(sample_project), states, "t-race", git)
        assert [e.type for e in events] == [EventType.NEEDS_OPERATOR]
        assert "ref update raced" in events[0].payload["detail"]
        assert storage.load_state("demo", "t-race").state is TaskState.MERGE_READY


class TestProgressUnits:

    def test_the_changed_files_are_reported(self, sample_project):
        git = _Git(changed_paths=["a.py", "b.py"])
        assert effects_merge.merge_progress_units(git, sample_project, "c1") == [
            "a.py", "b.py"]

    def test_a_git_that_cannot_run_reports_no_progress_rather_than_raising(
            self, sample_project):
        """This value rides on an event describing a merge that ALREADY
        happened. A progress signal that cannot be computed must not undo it.
        """
        git = _Git(top_level_checked=OSError("no git"))
        assert effects_merge.merge_progress_units(git, sample_project, "c1") == []

    def test_an_empty_result_is_real_zero_progress(self, sample_project):
        """Not "unknown": the ratchet fires on genuinely empty consecutive
        merges, so an empty list has to mean the merge touched nothing."""
        git = _Git(changed_paths=[])
        assert effects_merge.merge_progress_units(git, sample_project, "c1") == []


class TestBacklogTick:

    def test_an_unreadable_backlog_does_not_sink_a_completed_merge(
            self, tmp_state, sample_project, monkeypatch):
        """The merge is durably recorded by the time the tick runs, and git
        has already published it. A backlog file that cannot be read is not a
        reason to report failure for work that succeeded."""
        states = _seed("demo", "t-backlog", branch="feat/t-backlog")

        def boom(path, task_id, commit):
            raise OSError("backlog unreadable")

        monkeypatch.setattr(effects_merge.backlog_items, "tick_merged", boom)
        events = _run(_no_gates(sample_project), states, "t-backlog", _Git())
        assert [e.type for e in events] == [EventType.TASK_TRANSITIONED,
                                            EventType.MERGE_RECORDED]
        assert storage.load_state("demo", "t-backlog").state is TaskState.MERGED

    def test_a_carver_digest_that_cannot_be_built_still_records_the_merge(
            self, tmp_state, sample_project, monkeypatch):
        states = _seed("demo", "t-digest", branch="feat/t-digest")

        def boom(*a, **k):
            raise RuntimeError("digest builder exploded")

        monkeypatch.setattr(effects_merge.merge_digest, "carver_digest_payload", boom)
        events = _run(_no_gates(sample_project), states, "t-digest", _Git())
        recorded = next(e for e in events if e.type is EventType.MERGE_RECORDED)
        assert "carver_digest" not in recorded.payload
        assert recorded.payload["merge_commit"] == "abc1234"
