"""CR-05c: the three ways an agent leg starts.

The end-to-end behaviour of each launch is covered by `test_daemon.py` and by
the §5.1 differential. What is here is what those cannot reach cheaply: the
admission refusals, the worktree shapes, and the stale-receipt rule -- each of
which is a way a launch can go wrong rather than a way it goes right.
"""

from __future__ import annotations

import dataclasses
import subprocess

import pytest

from nyxloom import effects, effects_attempt, effects_dispatch, reconcile, storage
from nyxloom.types import (
    Attempt, AttemptState, EventType, Role, Route, TaskState, TaskStateFile,
    utc_now,
)


class _Files:
    """A filesystem port with a scripted set of existing paths."""

    def __init__(self, present=()) -> None:
        self.present = {str(p) for p in present}

    def exists(self, path) -> bool:
        return str(path) in self.present


class _Git:
    def __init__(self, branch_exists: bool = False) -> None:
        self.branch_exists = branch_exists
        self.calls: list[tuple] = []

    def resolve_verify(self, root, rev):
        self.calls.append(("resolve_verify", rev))
        return "sha" if self.branch_exists else None

    def worktree_add(self, root, path, branch):
        self.calls.append(("worktree_add", path, branch))

    def worktree_add_new_branch(self, root, branch, path, base):
        self.calls.append(("worktree_add_new_branch", branch, path, base))


def _ctx(cfg, states=None, *, ports=None):
    ports = ports or effects.EffectPorts.system()
    return effects.EffectContext(project="demo", cfg=cfg, states=states or {},
                                 ports=ports)


def _paused_ports(mode: str = "drain-agents"):
    class _PausedFiles:
        def exists(self, path):
            return True

        def read_text(self, path):
            return mode

    return dataclasses.replace(effects.EffectPorts.system(),
                               files=_PausedFiles())


def _task(task_id: str, state: TaskState, attempts=()) -> TaskStateFile:
    return TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id,
                         project="demo", state=state, since=utc_now(),
                         attempts=list(attempts))


class TestAdmissionRefusals:
    """Every launch re-checks admission at the EFFECT boundary, and a refusal
    emits nothing and leaves the task exactly where the planner found it."""

    def test_a_refused_dispatch_launches_nothing(self, tmp_state, sample_project):
        effector = effects_attempt.AttemptEffector(_paused_ports())
        ctx = _ctx(sample_project, {"t1": _task("t1", TaskState.QUEUED)},
                   ports=_paused_ports())
        assert effector.dispatch_implementer(
            ctx, reconcile.DispatchImplementer(task_id="t1", route_id="r1")) == []

    def test_a_refused_resume_launches_nothing(self, tmp_state, sample_project):
        """The resume path had no admission-refusal oracle before CR-05c: the
        branch existed and was never executed by any test."""
        effector = effects_attempt.AttemptEffector(_paused_ports())
        ctx = _ctx(sample_project, {"t1": _task("t1", TaskState.ACTIVE)},
                   ports=_paused_ports())
        assert effector.resume_attempt(
            ctx, reconcile.ResumeAttempt(task_id="t1", attempt_id="a1")) == []

    def test_a_refused_self_review_launches_nothing_and_does_not_degrade(
            self, tmp_state, sample_project):
        """Distinct from the no-warm-session case, which DOES transition. A
        refusal must leave the task SELF_REVIEWING to be retried, not push it
        on to frontier review as though the self-check had been considered."""
        effector = effects_attempt.AttemptEffector(_paused_ports())
        ctx = _ctx(sample_project, {"t1": _task("t1", TaskState.SELF_REVIEWING)},
                   ports=_paused_ports())
        assert effector.launch_self_review(
            ctx, reconcile.LaunchSelfReview(task_id="t1",
                                            source_attempt_id="a1")) == []

    def test_drain_handoffs_refuses_dispatch_but_admits_the_in_flight_legs(
            self, tmp_state, sample_project):
        """The mode distinction is the whole point of having two: new work
        stops, work already started finishes."""
        ports = _paused_ports("")   # legacy empty flag == drain-handoffs
        ctx = _ctx(sample_project, ports=ports)
        assert effects_dispatch.admissible(ctx, "dispatch")[0] is False
        assert effects_dispatch.admissible(ctx, "resume")[0] is True
        assert effects_dispatch.admissible(ctx, "self-review")[0] is True


class TestEnsureWorktree:

    def _ports(self, git, files):
        return dataclasses.replace(effects.EffectPorts.system(), git=git,
                                   files=files)

    def test_an_existing_worktree_is_left_alone(self, tmp_path):
        git = _Git()
        wt = tmp_path / "wt"
        effects_dispatch.ensure_worktree(
            self._ports(git, _Files([wt])), tmp_path, "feat/t1", wt, "main")
        assert git.calls == [], "an existing worktree must not be re-created"

    def test_an_existing_branch_is_checked_out_not_recreated(self, tmp_path):
        """A re-dispatch after a rejected review: the branch carries the
        previous attempt's commits, and `-b` would refuse or start over."""
        git = _Git(branch_exists=True)
        wt = tmp_path / "wt"
        effects_dispatch.ensure_worktree(
            self._ports(git, _Files()), tmp_path, "feat/t1", wt, "main")
        assert git.calls == [("resolve_verify", "feat/t1"),
                             ("worktree_add", str(wt), "feat/t1")]

    def test_a_first_dispatch_branches_from_the_default(self, tmp_path):
        git = _Git(branch_exists=False)
        wt = tmp_path / "wt"
        effects_dispatch.ensure_worktree(
            self._ports(git, _Files()), tmp_path, "feat/t1", wt, "main")
        assert git.calls == [
            ("resolve_verify", "feat/t1"),
            ("worktree_add_new_branch", "feat/t1", str(wt), "main")]

    def test_a_refused_worktree_add_raises_rather_than_launching_anyway(self):
        """The pass-level isolation records it as a TICK_ERROR and the task is
        re-planned. Degrading would launch an agent into a directory that is
        not there."""
        class _Processes:
            def run(self, argv, *, cwd=None, timeout=None):
                return subprocess.CompletedProcess(argv, 128, "", "fatal: exists")

        git = effects.SystemGit(_Processes())
        with pytest.raises(subprocess.CalledProcessError):
            git.worktree_add("/repo", "/wt", "feat/t1")
        with pytest.raises(subprocess.CalledProcessError):
            git.worktree_add_new_branch("/repo", "feat/t1", "/wt", "main")

    def test_a_successful_worktree_add_returns_quietly(self):
        class _Processes:
            def run(self, argv, *, cwd=None, timeout=None):
                return subprocess.CompletedProcess(argv, 0, "", "")

        git = effects.SystemGit(_Processes())
        assert git.worktree_add("/repo", "/wt", "feat/t1") is None


class TestNextResumeN:

    def test_the_first_resume_is_one(self, tmp_path):
        assert effects_attempt.next_resume_n(_Files(), tmp_path) == 1

    def test_it_skips_ordinals_already_on_disk(self, tmp_path):
        """Derived from disk rather than counted in state: a resume that
        landed but whose event did not would otherwise reuse an ordinal and
        overwrite the previous leg's log."""
        files = _Files([tmp_path / "attempt.resume-1.log",
                        tmp_path / "attempt.resume-2.log"])
        assert effects_attempt.next_resume_n(files, tmp_path) == 3

    def test_a_spec_file_alone_also_claims_an_ordinal(self, tmp_path):
        """A leg that wrote its spec and died before logging still used the
        ordinal."""
        files = _Files([tmp_path / "spec.resume-1.json"])
        assert effects_attempt.next_resume_n(files, tmp_path) == 2


class TestReviewRationale:

    def test_no_committed_report_leaves_the_prompt_unchanged(self, sample_project,
                                                             monkeypatch):
        monkeypatch.setattr(effects_attempt.effects_review, "review_report_text",
                            lambda git, cfg, task_id: None)
        assert effects_attempt.review_rationale(None, sample_project, "t1") is None

    def test_a_whitespace_only_report_is_not_a_rationale(self, sample_project,
                                                         monkeypatch):
        monkeypatch.setattr(effects_attempt.effects_review, "review_report_text",
                            lambda git, cfg, task_id: "   \n\n  ")
        assert effects_attempt.review_rationale(None, sample_project, "t1") is None

    def test_a_long_report_is_truncated_with_a_marker(self, sample_project,
                                                      monkeypatch):
        """Bounded so a pathologically long review cannot blow the
        prompt-length guard -- and the marker is what tells the agent it is
        reading an excerpt rather than the whole finding."""
        monkeypatch.setattr(effects_attempt.effects_review, "review_report_text",
                            lambda git, cfg, task_id: "x" * 500)
        out = effects_attempt.review_rationale(None, sample_project, "t1",
                                               max_chars=100)
        assert out.startswith("x" * 100)
        assert out.endswith("[... review report truncated ...]")

    def test_a_short_report_is_passed_through_stripped(self, sample_project,
                                                       monkeypatch):
        monkeypatch.setattr(effects_attempt.effects_review, "review_report_text",
                            lambda git, cfg, task_id: "\n  findings here \n")
        assert effects_attempt.review_rationale(
            None, sample_project, "t1") == "findings here"


class TestRouteSnapshot:

    def test_the_route_is_pinned_with_the_revision_it_was_dispatched_under(self):
        """A route id alone cannot answer "did the routes file change under
        this attempt", which is the question a later reader actually has."""
        from nyxloom.config import RouteDef, Routes

        route_def = RouteDef(route_id="r1", cli="fake", model="m",
                             variant="v", effort="high")
        routes = Routes(revision="rev-7", tiers={}, routes={"r1": route_def})
        snap = effects_attempt._route_snapshot(route_def, routes)
        assert snap == Route(route_id="r1", cli="fake", model="m", variant="v",
                             effort="high", routes_rev="rev-7")
