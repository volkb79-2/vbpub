"""Guarded-automatic merge: the effect that publishes work to main (CR-05b).

WHY THIS IS THE MOST CONSERVATIVE EFFECTOR IN THE TREE
------------------------------------------------------
Every other effect can be re-run. This one moves a ref that other people and
other processes read, so its failure modes are asymmetric: refusing to merge
costs a pass, and merging wrongly costs whatever was on the branch.

Three properties carry that weight, and none of them is incidental:

1. **A real ``git merge --no-ff`` in a disposable scratch worktree.** Never
   the surgical commit-tree technique an operator uses by hand: that grafts a
   tree without any 3-way merge algorithm running, so it has NO conflict
   detection at all. Fine under human supervision, where the operator reads
   the diff; never fine unattended.
2. **Compare-and-swap publication.** The final ``update-ref`` passes the OLD
   value, so if anything moved the default branch between reading it and
   writing the merge result, git refuses and this escalates rather than
   silently rebasing over an unknown state.
3. **Gates run on the MERGED tree, before publication.** The pre-merge gate
   and the optional mutation gate both run in the scratch worktree at the
   merge commit. A failure routes back to REVIEW_REJECTED and main is never
   touched -- the merge is abandoned, not reverted, because nothing was
   published to revert.

The daemon does NOT materialize the merge into the operator's working tree.
It advances the ref; a running system picks up new code through a deliberate
rebuild, never a surprise mid-merge checkout. An earlier version did check
out into the live tree and clobbered uncommitted operator edits.

NO VERDICT RE-CHECK HAPPENS HERE
--------------------------------
Reaching MERGE_READY is the verdict. Re-reading it at merge time would put
merge authority back into a second place, which is precisely what the typed
result protocol removed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import backlog_items, effects, effects_gates, merge_digest, reconcile
from .config import ProjectConfig
from .log import get_logger
from .types import Event, EventType, GateResult, Role, TaskState

log = get_logger("effects.merge")


def merge_progress_units(git: Any, cfg: ProjectConfig, commit: str) -> list[str]:
    """The files a merge actually changed -- an objective progress signal.

    Before this existed the merge record carried NO progress units, so the
    ratchet's reader defaulted every merge to zero progress and false-fired
    after any N merges. An empty list is now REAL zero-progress (a no-op
    merge), and a merge that touched files is not, so the ratchet fires only
    on genuinely empty consecutive merges.
    """
    try:
        root = git.top_level_checked(str(cfg.root)) or str(cfg.root)
        return git.changed_paths(root, commit)
    except OSError:
        return []


class MergeEffector:
    """Publishes a reviewed task to the default branch. Holds no state."""

    def __init__(self, ports: effects.EffectPorts) -> None:
        self._ports = ports

    def auto_merge(self, ctx: effects.EffectContext,
                   action: reconcile.AutoMergeTask) -> list[Event]:
        """MERGE_READY -> MERGED, or an escalation left at MERGE_READY.

        Every failure path leaves the task exactly where it was and raises
        NEEDS_OPERATOR. That is the asymmetry this effect is built around: a
        task that stays MERGE_READY is re-planned next pass and costs a cycle,
        while a task moved on from a merge that did not really happen is a
        silent lie in the projection.
        """
        events: list[Event] = []
        git = self._ports.git
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        task_id = action.task_id
        tsf = states[task_id]

        branch = next(
            (a.branch for a in tsf.attempts
             if a.role is Role.IMPLEMENTER and a.branch), None)
        if branch is None:
            return [self._escalate(
                ctx, task_id, "auto-merge-error",
                f"auto-merge: no recorded implementer branch for {task_id}")]

        repo_root = git.top_level_checked(str(cfg.root))
        if repo_root is None:
            return [self._escalate(
                ctx, task_id, "auto-merge-error",
                f"auto-merge: could not resolve repo root for {task_id}")]

        old_commit = git.resolve(repo_root, cfg.default_branch)
        scratch = Path(repo_root) / ".worktrees" / f"automerge-{task_id}"
        if self._ports.files.exists(scratch):
            git.worktree_remove(repo_root, str(scratch))
        add = git.worktree_add_detached(repo_root, str(scratch), old_commit)
        if add.returncode != 0:
            return [self._escalate(
                ctx, task_id, "auto-merge-error",
                f"auto-merge: scratch worktree setup failed for {task_id}: "
                f"{add.stderr[:150]}")]

        merge = git.merge_no_ff(str(scratch), branch,
                                f"Merge {task_id} (guarded-automatic)")
        if merge.returncode != 0:
            git.merge_abort(str(scratch))
            git.worktree_remove(repo_root, str(scratch))
            log.error("merge-conflict", project=project, task=task_id,
                      branch=branch)
            return [self._escalate(
                ctx, task_id, "auto-merge-conflict",
                f"auto-merge: real conflict merging {task_id} -- "
                "operator must resolve by hand")]

        new_commit = git.resolve(str(scratch), "HEAD")

        # Both gates run on the MERGED tree in the scratch worktree, and both
        # abandon rather than revert: nothing has been published yet.
        for phase, enabled, select in (
            ("pre-merge", getattr(cfg.policy, "pre_merge_gate", True),
             effects_gates.select_post_merge_gate),
            ("mutation", getattr(cfg.policy, "mutation_gate", False),
             effects_gates.select_mutation_gate),
        ):
            if not enabled:
                continue
            gate = select(cfg)
            if gate is None:
                continue
            result, exit_code = self._run_gate(gate, phase, scratch, new_commit)
            events.append(ctx.append(EventType.GATE_FINISHED,
                                     {"gate_result": result.to_dict()},
                                     task_id=task_id))
            if exit_code != 0:
                git.worktree_remove(repo_root, str(scratch))
                events.append(ctx.transition(
                    task_id, TaskState.REVIEW_REJECTED,
                    f"{phase} gate failed (exit {exit_code}); not published"))
                return events

        git.worktree_remove(repo_root, str(scratch))

        ff = git.update_ref(repo_root, f"refs/heads/{cfg.default_branch}",
                            new_commit, old_commit)
        if ff.returncode != 0:
            events.append(self._escalate(
                ctx, task_id, "auto-merge-error",
                f"auto-merge: ref update raced for {task_id} -- "
                "git state needs inspection"))
            return events

        events.append(ctx.transition(task_id, TaskState.MERGED, None))
        log.info("merge", project=project, task=task_id,
                 merge_commit=new_commit, branch=branch)
        payload: dict[str, object] = {
            "merge_commit": new_commit,
            "progress_units": merge_progress_units(git, cfg, new_commit),
            "source_kind": "review",
        }
        digest = self._carver_digest(cfg, states, task_id, new_commit)
        if digest is not None:
            payload["carver_digest"] = digest
        events.append(ctx.append(EventType.MERGE_RECORDED, payload,
                                 task_id=task_id))
        # Best-effort backlog auto-tick. The merge is already durably
        # recorded, so a backlog that cannot be read or written must not sink
        # an otherwise-successful merge.
        try:
            backlog_items.tick_merged(backlog_items.resolve_path(cfg),
                                      task_id, new_commit)
        except (OSError, UnicodeDecodeError):
            pass
        return events

    def _escalate(self, ctx: effects.EffectContext, task_id: str,
                  reason: str, detail: str) -> Event:
        """Raise NEEDS_OPERATOR and leave the task where it is."""
        return ctx.append(EventType.NEEDS_OPERATOR,
                          {"detail": detail, "reason": reason},
                          task_id=task_id)

    def _run_gate(self, gate, phase: str, scratch: Path,
                  commit: str) -> tuple[GateResult, int]:
        """Run one gate against the scratch merge tree.

        A timeout is exit 124 and a missing binary is exit 127 -- the shell's
        own conventions, kept distinct because an operator repairs them
        differently and neither is a test failure.
        """
        argv = [tok.replace("{worktree}", str(scratch)) for tok in gate.argv]
        started = self._ports.clock.now()
        out_tail = ""
        try:
            proc = self._ports.processes.run(argv, cwd=str(scratch),
                                             timeout=gate.timeout_seconds)
            exit_code = proc.returncode
            if exit_code != 0:
                out_tail = effects_gates.gate_output_tail(proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as _te:
            exit_code = 124
            out_tail = effects_gates.gate_output_tail(_te.stdout, _te.stderr)
        except OSError:
            exit_code = 127
        return GateResult(
            gate_id=gate.gate_id, phase=phase, commit=commit,
            exit_code=exit_code, started=started, ended=self._ports.clock.now(),
            environment=gate.environment, output_tail=out_tail), exit_code

    @staticmethod
    def _carver_digest(cfg: ProjectConfig, states, task_id: str,
                       commit: str):
        """The carver's merge digest payload, or None if it cannot be built.

        Guarded because the digest is OBSERVABILITY carried on an event that
        has already been decided. A digest builder that raises must not undo
        a merge that git has already published.
        """
        try:
            return merge_digest.carver_digest_payload(
                cfg, states, task_id, commit, source_kind="review")
        except Exception:  # census: cleanup/containment (CR-02b)
            log.warning("carver_digest build failed", exc_info=True)
            return None


def specs(effector: MergeEffector) -> tuple[effects.HandlerSpec, ...]:
    return (
        effects.HandlerSpec(
            action_type=reconcile.AutoMergeTask,
            kind="auto-merge",
            handler=effector.auto_merge,
            emits=frozenset({EventType.NEEDS_OPERATOR, EventType.GATE_FINISHED,
                             EventType.TASK_TRANSITIONED,
                             EventType.MERGE_RECORDED}),
            idempotency_key=lambda a: f"auto-merge:{a.task_id}",
        ),
    )
