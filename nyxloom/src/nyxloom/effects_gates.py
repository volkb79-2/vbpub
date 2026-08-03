"""Gate effects: the verify cadence and post-merge validation (CR-05a).

WHY THESE TWO TOGETHER
----------------------
They are the daemon's only two BACKGROUND-work families, and they share one
execution model that the amendment's section 3.3 names directly: the
background-work registries belong to the effector that owns the work, not to
the shell. Before this package ``_gate_verify_running``, ``_gate_verify_
results``, ``_post_merge_gate_running`` and ``_post_merge_gate_results`` were
four attributes on ``Daemon``, which is why every test of either family had to
construct a daemon and reach into its private dicts.

THE EXECUTION MODEL (unchanged, and the reason the split is safe)
----------------------------------------------------------------
Both a gate verify (several real gate runs against disposable canary commits)
and a post-merge gate (a full suite against the merged tree) take minutes. The
daemon's tick loop iterates registered projects SEQUENTIALLY on one thread, so
running either inline stalls every other project. Both therefore run as:

1. an IDEMPOTENT DISPATCHER on the reconcile thread. A live handle under this
   work's key means the work is already in flight, so it starts NO second
   copy -- which is exactly why the planner may harmlessly replan the same
   trigger every pass until the drain moves the task or resets the cadence;
2. a probe on a background thread that does ONLY external side effects --
   git, filesystem, the gate subprocess -- and NEVER appends an event, calls
   a transition, or touches ``states``. It ends by putting a small result
   dict on a queue;
3. a DRAIN on the reconcile thread, called once per pass, which is the sole
   place either family's events are ever appended.

Rule 2 is not a style preference. Every mutation from a non-main thread would
race the reconcile loop, and the projection the store writes is derived from
committed state inside a transaction (CR-04a) -- a second writer would not
corrupt it, it would interleave with it, which is harder to see.

On a daemon restart mid-work the in-memory handle is simply lost. VALIDATING
re-emits ``RunPostMergeGate`` and the cadence re-emits ``VerifyGate`` on the
next pass, so the work re-runs: idempotent, with no durable half-state to
reconcile.

WHAT THE KEYS ARE, AND WHY THEY DIFFER
--------------------------------------
Gate verify is keyed by PROJECT: it is a project-wide cadence probe, and two
concurrent verifies of one project would race for the same canary worktrees.
Post-merge is keyed by TASK: more than one task can be VALIDATING for the
same project at once, and each gates its own merge commit. Those keys are the
handler specs' declared idempotency keys, so the registry states the identity
each family refuses to duplicate rather than leaving it implicit in a dict.
"""

from __future__ import annotations

import queue
import subprocess
from pathlib import Path
from typing import Any

from . import effects, gate_canary, gate_runner, reconcile
from .config import GateDef, ProjectConfig
from .log import get_logger
from .types import (
    Blocker, BlockerType, Event, EventType, GateResult, TaskState, iso,
)

log = get_logger("effects.gates")


# ---------------------------------------------------------------------------
# gate selection and output shaping -- pure, and shared with the merge path


def select_post_merge_gate(cfg: ProjectConfig) -> GateDef | None:
    """The gate a post-merge validation re-runs.

    Prefer one the project declares ``phase == 'post-merge'``; otherwise
    re-run the ``implementation`` gate against the merged default branch --
    the same gate the merged code already had to pass, just re-verified after
    the merge. ``None`` only when the project declares no gates at all.

    The selection itself lives in ``gate_runner`` so this path and
    ``cli.cmd_merge`` share ONE implementation.
    """
    return gate_runner.select_verification_gate(cfg)


def select_mutation_gate(cfg: ProjectConfig) -> GateDef | None:
    """The lowest-``gate_id`` gate with ``phase == 'mutation'``, or None."""
    mutation = [g for g in cfg.gates.values() if g.phase == "mutation"]
    if mutation:
        return sorted(mutation, key=lambda g: g.gate_id)[0]
    return None


def gate_output_tail(stdout: object, stderr: object, limit: int = 4096) -> str:
    """A bounded text tail of a gate subprocess's stdout+stderr.

    TAIL, not head: the actionable summary -- pytest FAILED lines, the
    diff-coverage verdict -- is at the END. Tolerates ``str | bytes | None``
    because ``subprocess.run`` yields str under ``text=True`` while a
    ``TimeoutExpired`` may carry bytes or nothing at all, and the timeout
    branch needs a tail just as much as the ordinary failure does.
    """
    def _s(v: object) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, (bytes, bytearray)):
            return bytes(v).decode("utf-8", "replace")
        return str(v)
    out, err = _s(stdout), _s(stderr)
    combined = f"{out}\n{err}" if (out and err) else (out or err)
    return combined[-limit:]


# ---------------------------------------------------------------------------
# the effector


class GateEffector:
    """Owns both gate families' background work and its bookkeeping."""

    def __init__(self, ports: effects.EffectPorts) -> None:
        self._ports = ports
        #: The verify family's declared idempotency key (one per project) ->
        #: its live (or last) handle. ``is_running`` on this is what makes the
        #: dispatcher idempotent, and the key comes from the SPEC rather than
        #: from a field this effector chose, so the identity the registry
        #: declares and the one enforced here cannot drift apart.
        self.verify_running: dict[str, Any] = {}
        self.verify_results: queue.Queue = queue.Queue()
        #: The post-merge family's declared idempotency key (one per TASK,
        #: since several tasks can be VALIDATING at once) -> its handle.
        self.post_merge_running: dict[str, Any] = {}
        self.post_merge_results: queue.Queue = queue.Queue()

    # -- gate-verify cadence -------------------------------------------

    def verify_gate(self, ctx: effects.EffectContext,
                    action: reconcile.VerifyGate) -> list[Event]:
        """Start a gate-verify probe unless one is already running.

        Appends no event: the probe takes minutes, and the only main-thread
        seam that may record anything is :meth:`drain_verify`.
        """
        key = ctx.idempotency_key
        if self._ports.background.is_running(self.verify_running.get(key)):
            return []
        self.verify_running[key] = self._ports.background.spawn(
            f"gate-verify-{ctx.project}", self._run_verify_probe,
            (ctx.project, ctx.cfg, key))
        return []

    def _run_verify_probe(self, project: str, cfg: ProjectConfig,
                          key: str) -> None:
        """The probe itself, on the background thread.

        Confirms the project's verification gate PASSES known-good HEAD, then
        tries a known-bad canary against it, deriving the same verdict
        ``cli.cmd_gate_verify`` does: BROKEN if good HEAD fails; else
        TRUSTWORTHY if a canary was killed, LAUNDERS if every canary
        survived, INCONCLUSIVE if no canary rendered a real verdict.

        Guarded end to end. A thread that died would look -- from the
        daemon's side -- exactly like a verify that simply never finishes,
        and the cadence would never reset.
        """
        verdict = "INCONCLUSIVE"
        gate_id: str | None = None
        try:
            gate = gate_runner.select_verification_gate(cfg)
            if gate is None:
                verdict = "NO_GATE"
            else:
                gate_id = gate.gate_id
                commit = self._ports.git.head_commit(str(cfg.root))
                if not commit:
                    verdict = "INCONCLUSIVE"
                else:
                    good = gate_runner.run_gate_at_commit(
                        cfg, gate, commit, phase="verify-good")
                    if good.exit_code != 0:
                        verdict = "BROKEN"
                    else:
                        canary_result = gate_canary.verify_gate_rejects_canary(
                            cfg, gate, commit)
                        if canary_result.killed:
                            verdict = "TRUSTWORTHY"
                        elif canary_result.inconclusive:
                            verdict = "INCONCLUSIVE"
                        else:
                            verdict = "LAUNDERS"
        except Exception:  # census: advisory-degradation (CR-02b)
            # A probe can only ever REDUCE trust in a gate. It cannot mark one
            # trustworthy, so degrading an unexpected failure to INCONCLUSIVE
            # withholds a verdict rather than granting one.
            verdict = "INCONCLUSIVE"
        # `key` travels with the result so the drain clears exactly the entry
        # the dispatcher created, instead of deriving the same identity a
        # second time and risking the two derivations diverging.
        self.verify_results.put(
            {"project": project, "verdict": verdict, "gate_id": gate_id,
             "key": key})

    def drain_verify(self, ctx: effects.EffectContext) -> list[Event]:
        """Turn finished verify probes into events. Main thread, once a pass.

        The queue is shared across every registered project (one effector,
        one queue), so this drains the WHOLE queue and re-queues anything
        belonging to another project -- harmless, since the tick loop visits
        every project each pass and that project's own call picks it back up.
        """
        events: list[Event] = []
        for result in self._take(self.verify_results, ctx.project):
            verdict = result.get("verdict")
            gate_id = result.get("gate_id")
            events.append(ctx.append(
                EventType.GATE_VERIFY_RECORDED,
                {"project": ctx.project, "verdict": verdict, "gate_id": gate_id,
                 "at": iso(self._ports.clock.now())},
                task_id=None))
            self.verify_running.pop(result.get("key"), None)
            if verdict in ("LAUNDERS", "BROKEN"):
                reason = f"gate-verify-{verdict.lower()}"
                if not effects.needs_operator_recently_emitted(ctx.events(), reason):
                    events.append(ctx.append(
                        EventType.NEEDS_OPERATOR,
                        {"reason": reason, "gate_id": gate_id}, task_id=None))
        return events

    # -- post-merge validation -----------------------------------------

    def post_merge_worktree_value(self, cfg: ProjectConfig) -> str:
        """The ``{worktree}`` substitution for a gate re-run against the
        already-MERGED default branch.

        Post-merge validation has no separate worktree: it runs against the
        project's one already-merged checkout, so the correct value is THAT
        checkout's git top-level. Using the top-level rather than ``cfg.root``
        is what makes it right for both project shapes in this codebase -- a
        project whose root IS its repo root, and this one, whose root is a
        SUBDIRECTORY of the repo it is self-hosted in.
        """
        return self._ports.git.top_level_or(str(cfg.root), str(cfg.root),
                                            timeout=15)

    def run_post_merge_gate(self, ctx: effects.EffectContext,
                            action: reconcile.RunPostMergeGate) -> list[Event]:
        """Start a post-merge gate for this task unless one is running."""
        task_id = action.task_id
        key = ctx.idempotency_key
        if self._ports.background.is_running(self.post_merge_running.get(key)):
            return []
        # The merge commit is the one piece of durable task state the probe
        # needs. Read it HERE, on the reconcile thread, and hand it down as a
        # plain string -- the probe must not touch `states` itself.
        commit = ctx.states[task_id].merge_commit or ""
        self.post_merge_running[key] = self._ports.background.spawn(
            f"post-merge-gate-{task_id}", self._run_post_merge_probe,
            (ctx.project, ctx.cfg, task_id, commit, key))
        return []

    def _run_post_merge_probe(self, project: str, cfg: ProjectConfig,
                              task_id: str, commit: str,
                              key: str) -> None:
        """Run the post-merge gate on the background thread.

        Every outcome -- including "this project declares no gate" -- leaves
        through the same ``.put()`` seam, so the dispatcher stays generic and
        the drain has exactly one shape to read.
        """
        gate = select_post_merge_gate(cfg)
        if gate is None:
            self.post_merge_results.put({
                "project": project, "task_id": task_id, "key": key,
                "gate_id": None, "gate_result": None, "outcome": "no_gate",
            })
            return

        started = self._ports.clock.now()
        # Run the gate in a CLEAN scratch worktree checked out at the merge
        # commit -- never against the operator's live checkout, which may sit
        # on a feature branch or carry uncommitted edits and would validate
        # the WRONG tree, minting a COMPLETED/BLOCKED verdict against code
        # that is not the merge commit.
        repo_root = self._ports.git.top_level(str(cfg.root))
        scratch: Path | None = None
        if commit and repo_root:
            scratch = Path(repo_root) / ".worktrees" / f"postmerge-{task_id}"
            if self._ports.files.exists(scratch):
                self._ports.git.worktree_remove(repo_root, str(scratch))
            if self._ports.git.worktree_add_detached(
                    repo_root, str(scratch), commit).returncode != 0:
                scratch = None  # fall back to the live root below
        gate_cwd = str(scratch) if scratch is not None else str(cfg.root)
        worktree_value = (str(scratch) if scratch is not None
                          else self.post_merge_worktree_value(cfg))
        argv = [tok.replace("{worktree}", worktree_value) for tok in gate.argv]
        out_tail = ""
        try:
            proc = self._ports.processes.run(argv, cwd=gate_cwd,
                                             timeout=gate.timeout_seconds)
            exit_code = proc.returncode
            if exit_code != 0:
                out_tail = gate_output_tail(proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as _te:
            exit_code = 124  # conventional shell timeout exit code
            out_tail = gate_output_tail(_te.stdout, _te.stderr)
        except OSError:
            exit_code = 127  # command-not-found / exec failure
        finally:
            if scratch is not None:
                self._ports.git.worktree_remove(repo_root, str(scratch))
        ended = self._ports.clock.now()

        gate_result = GateResult(
            gate_id=gate.gate_id, phase="post-merge", commit=commit,
            exit_code=exit_code, started=started, ended=ended,
            environment=gate.environment, output_tail=out_tail,
        )
        # `key` travels with the result so the drain clears exactly the entry
        # the dispatcher created, rather than deriving the identity twice.
        result: dict[str, Any] = {
            "project": project, "task_id": task_id, "key": key,
            "gate_id": gate.gate_id, "gate_result": gate_result.to_dict(),
        }

        if exit_code == 0:
            result["outcome"] = "completed"
            self.post_merge_results.put(result)
            return

        # The gate FAILED on a tree already published to the default branch
        # (the pre-merge gate is skippable, and a forced merge or a flaky pass
        # can also let a red commit through). Only BLOCKING it would leave the
        # broken commit live. Auto-revert by CAS: git refuses if a newer merge
        # landed since, and a refused CAS falls through to BLOCKED for a human.
        result["outcome"] = "blocked"
        result["reverted"] = False
        if getattr(cfg.policy, "auto_revert_failed_merge", True) and commit and repo_root:
            parent = self._ports.git.rev_parse(repo_root, f"{commit}^1")
            if parent:
                rv = self._ports.git.update_ref(
                    repo_root, f"refs/heads/{cfg.default_branch}", parent, commit)
                if rv.returncode == 0:
                    # Logging is not the states/event-log mutation the
                    # main-thread-only rule is about.
                    log.warning("merge-reverted", project=project, task=task_id,
                                frm=commit, to=parent, gate=gate.gate_id)
                    result["reverted"] = True
                    result["revert_from"] = commit
                    result["revert_to"] = parent
                else:
                    # CAS refused: the branch no longer points at the merge
                    # commit. Never force over it -- leave it and let the
                    # BLOCKED escalation get a human.
                    log.error("merge-revert-cas-refused", project=project,
                              task=task_id, frm=commit,
                              branch=cfg.default_branch, stderr=rv.stderr[:150])
        log.error("gate-failed", project=project, task=task_id,
                  gate=gate.gate_id, exit_code=exit_code)
        self.post_merge_results.put(result)

    def drain_post_merge(self, ctx: effects.EffectContext) -> list[Event]:
        """Turn finished post-merge gates into events and the terminal
        transition. Main thread, once a pass -- the sole place a post-merge
        GATE_FINISHED / MERGE_REVERTED / TASK_BLOCKED / COMPLETED is appended.
        """
        events: list[Event] = []
        for result in self._take(self.post_merge_results, ctx.project):
            task_id = result["task_id"]
            self.post_merge_running.pop(result.get("key"), None)
            outcome = result.get("outcome")

            if outcome == "no_gate":
                events.append(ctx.transition(
                    task_id, TaskState.COMPLETED,
                    "post-merge validation: project declares no gate, no-op pass"))
                continue

            gate_id = result.get("gate_id")
            gate_result = result.get("gate_result")
            events.append(ctx.append(EventType.GATE_FINISHED,
                                     {"gate_result": gate_result},
                                     task_id=task_id))

            if outcome == "completed":
                events.append(ctx.transition(
                    task_id, TaskState.COMPLETED,
                    f"post-merge gate {gate_id} passed"))
                continue

            # outcome == "blocked"
            exit_code = gate_result["exit_code"]
            if result.get("reverted"):
                events.append(ctx.append(
                    EventType.MERGE_REVERTED,
                    {"from": result.get("revert_from"), "to": result.get("revert_to"),
                     "branch": ctx.cfg.default_branch,
                     "reason": f"post-merge gate {gate_id} failed (exit {exit_code})"},
                    task_id=task_id))
            blocker = Blocker(
                # A post-merge GATE failure is an ENVIRONMENT failure (the
                # merged tree failed its own tests), NOT an underspecified
                # handoff. Typing it CONTRACT inflated
                # blocked_underspecified_count and could trip
                # SpecAttention('blocked-underspecified').
                type=BlockerType.ENVIRONMENT,
                unblock_condition="operator: inspect post-merge gate failure",
                detail=f"post-merge gate {gate_id} exit_code={exit_code}"[:200],
            )
            events.append(ctx.append(
                EventType.TASK_BLOCKED,
                {"from": ctx.states[task_id].state.value,
                 "blocker": blocker.to_dict()},
                task_id=task_id))
        return events

    # -- shared queue handling -----------------------------------------

    @staticmethod
    def _take(q: queue.Queue, project: str) -> list[dict]:
        """Drain ``q`` and return this project's results, re-queueing the rest.

        One queue serves every registered project, so a result for another
        project must go back rather than be discarded; the tick loop reaches
        that project in the same pass or the next one.
        """
        pending: list[dict] = []
        while True:
            try:
                pending.append(q.get_nowait())
            except queue.Empty:
                break
        mine: list[dict] = []
        for result in pending:
            if result.get("project") != project:
                q.put(result)
            else:
                mine.append(result)
        return mine


# ---------------------------------------------------------------------------
# handler specs


def specs(effector: GateEffector) -> tuple[effects.HandlerSpec, ...]:
    return (
        effects.HandlerSpec(
            action_type=reconcile.VerifyGate,
            kind="verify-gate",
            handler=effector.verify_gate,
            # The dispatcher emits nothing; the family's events are the
            # drain's, which is why `emits` and `drain` belong to one spec.
            emits=frozenset({EventType.GATE_VERIFY_RECORDED,
                             EventType.NEEDS_OPERATOR}),
            idempotency_key=lambda a: f"verify-gate:{a.project}",
            starts_background_work=True,
            drain=effector.drain_verify,
        ),
        effects.HandlerSpec(
            action_type=reconcile.RunPostMergeGate,
            kind="post-merge-gate",
            handler=effector.run_post_merge_gate,
            emits=frozenset({EventType.GATE_FINISHED, EventType.MERGE_REVERTED,
                             EventType.TASK_BLOCKED,
                             EventType.TASK_TRANSITIONED}),
            idempotency_key=lambda a: f"post-merge-gate:{a.task_id}",
            starts_background_work=True,
            drain=effector.drain_post_merge,
        ),
    )
