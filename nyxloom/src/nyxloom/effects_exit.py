"""Attempt-exit consumption: what an agent leg's exit MEANS (CR-05e).

THE LAST EFFECT FAMILY, AND WHY IT IS LAST
------------------------------------------
Every other family either starts work or records a decision. This one READS
what a finished leg produced and decides where the task goes -- and what an
exit means depends on the attempt's ROLE, not on which effector dispatched
it. A carver exit is consumed by carve, a review exit by the verdict reader,
an implementer exit by the outcome table. So this module could only be
written once all of those existed, and it composes them rather than
duplicating any.

RECEIPTS ARE PROCESS FACTS, NEVER VERDICTS
------------------------------------------
The wrapper infers a receipt from how the child process exited. A clean exit
says the agent finished; it says NOTHING about what it concluded. A live
incident had a REJECTED review report with a clean exit rubber-stamp a merge.
So a DONE receipt is only permission to go LOOK for a typed judgement, and the
judgement is read through a schema and compared against the attempt identity
the daemon dispatched -- a record left by an earlier attempt cannot be
consumed. Non-DONE receipts stay a defence-in-depth fail-safe that never reads
a report at all.

GIT STATE IS TRUTH, RECEIPTS LIE
--------------------------------
:meth:`ExitEffector._crosscheck_head_commit` runs BEFORE the receipt is used,
because receipts have been observed reporting a null head commit and no files
touched while the branch held real work.

IDEMPOTENT HEALING
------------------
A wrapper that died before emitting its own exit event leaves a receipt and no
event. This consumer emits the missing ATTEMPT_EXITED itself, then performs
the task transition -- so the same exit consumed twice produces the transition
once. A wave review is the sharp case: ONE attempt covers every member, the
receipt scan can emit an exit per member in a single pass, and the first to
run transitions all of them, leaving the rest to find no members and no-op.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from . import (
    carver_session, effects, effects_review, paths, reconcile, results, stages, storage,
)
from .config import ProjectConfig
from .log import get_logger
from .types import (
    Attempt, AttemptState, Blocker, BlockerType, Event, EventType, Receipt,
    ReceiptResult, Role, TaskState, TaskStateFile, utc_now,
)

log = get_logger("effects.exit")

#: Per-task LIFETIME cap on scope amendments. A mid-flight "I need file X
#: outside my allowlist" request is approved under it and hard-BLOCKS at it --
#: which is what makes the escalation bounded rather than an infinite loop.
MAX_SCOPE_AMENDMENTS_PER_TASK = 2


class ExitEffector:
    """Consumes attempt exits. Composes the role-specific consumers rather
    than re-implementing any of them."""

    def __init__(self, ports: effects.EffectPorts, carve: Any, carver: Any,
                 lifecycle: Any) -> None:
        self._ports = ports
        self._carve = carve
        self._carver = carver
        self._lifecycle = lifecycle

    # -- forwarding seams -------------------------------------------------

    def _append_ev(self, project: str, cfg: ProjectConfig,
                   states: dict[str, TaskStateFile], ev_type: EventType,
                   payload: dict[str, Any], **kw: Any) -> Event:
        return self._ports.journal.append(project, cfg, states, ev_type,
                                          payload, **kw)

    def _transition(self, ctx: effects.EffectContext, task_id: str,
                    to: TaskState, notes: str | None) -> Event:
        return ctx.transition(task_id, to, notes)

    def _require_events(self, project: str,
                        events: Sequence[Event] | None = None) -> Sequence[Event]:
        if events is not None:
            return events
        return self._ports.event_log.require(project)

    def _scope_amendments_approved(self, project: str, task_id: str,
                                    *, events: Sequence[Event] | None = None) -> list[dict]:
        """B21 2026-07-23 (D-R16 §3): every SCOPE_AMENDMENT_APPROVED event
        payload recorded for this task, in log order. SCOPE_AMENDMENT_APPROVED
        has no TaskStateFile projection (storage.apply_event does not
        special-case it -- same pass-through shape as DECISION_OPENED and
        other events the projection switch does not touch), so counting/
        reading it back requires a direct event-log scan, not the projected
        tsf -- mirrors _ratchet_already_open's own iter_events scan just
        above. Unlike that helper's 500-event recent window, this is NOT
        windowed: MAX_SCOPE_AMENDMENTS_PER_TASK is a per-task LIFETIME cap
        (the whole point of D-B21-2's bound), so an old approval scrolling
        out of a recent-N window must never let the cap silently re-open.

        CR-02a: ``except Exception: return []`` reported ZERO prior approvals
        from an unreadable log -- which re-opens the per-task lifetime cap the
        count exists to enforce, the single worst direction for this helper."""
        return [ev.payload for ev in self._require_events(project, events)
                if ev.type is EventType.SCOPE_AMENDMENT_APPROVED and ev.task_id == task_id]

    def _attempt_has_review_recorded(self, project: str, attempt_id: str,
                                      *, events: Sequence[Event] | None = None) -> bool:
        """F019 P1b: True when a REVIEW_RECORDED event is already bound to this
        attempt id -- i.e. this review/diagnosis leg was already consumed. Makes
        the gate-diagnosis consumption idempotent, and (with the task's
        REVIEW_REJECTED state) distinguishes a fresh, unconsumed diagnosis leg
        (no binding) from an already-consumed normal review whose members also
        sit in REVIEW_REJECTED -- so the consumption never re-records a normal
        review's verdict (O5).

        CR-02a: the former ``except Exception: return False`` reported "this
        leg has NOT been consumed" from an unreadable log -- the answer that
        re-consumes a verdict."""
        return any(ev.type is EventType.REVIEW_RECORDED and ev.attempt_id == attempt_id
                    for ev in self._require_events(project, events))

    def _crosscheck_head_commit(self, cfg: ProjectConfig, task_id: str, receipt: Receipt) -> None:
        """P21 2026-07-16: never let a lying null head_commit read as "no
        work done" (live P93 lesson: a receipt claimed head_commit=null
        while feat/<task> actually held a real commit). If the receipt
        already reports a commit, trust it -- only a null/empty value gets
        cross-checked. Compares feat/<task_id> against cfg.default_branch
        in cfg.root (read-only: rev-parse only, never a write to the
        branch); a branch that is ahead of default gets its real HEAD
        recorded; a branch with no commits ahead, or that does not exist,
        still records null/none."""
        if receipt.head_commit:
            return
        branch = f"feat/{task_id}"
        branch_res = subprocess.run(
            ["git", "-C", str(cfg.root), "rev-parse", "--verify", branch],
            capture_output=True, text=True,
        )
        if branch_res.returncode != 0:
            return  # no such branch -- leave null/none
        default_res = subprocess.run(
            ["git", "-C", str(cfg.root), "rev-parse", "--verify", cfg.default_branch],
            capture_output=True, text=True,
        )
        if default_res.returncode != 0:
            return
        branch_sha = branch_res.stdout.strip()
        if branch_sha and branch_sha != default_res.stdout.strip():
            receipt.head_commit = branch_sha

    def _pre_review_sha(self, project: str, task_id: str, attempt_id: str,
                         *, events: Sequence[Event] | None = None) -> str | None:
        """DR8: read back the pre-review baseline sha THIS review attempt's
        ATTEMPT_CREATED event recorded for `task_id` (see LaunchReview's
        per-member created_payload) -- everything committed on feat/<task_id>
        strictly after this sha is the reviewer's own work, the diff range
        _enforce_reviewer_repair inspects. Mirrors _scope_amendment_files'
        direct event-log scan shape (ATTEMPT_CREATED's payload extension here
        has no TaskStateFile projection field of its own). Returns None when
        no ATTEMPT_CREATED for this exact (task_id, attempt_id) pair carries
        the key -- an unresolved baseline (_task_branch_head_sha returned
        None) or a pre-DR8/policy-off review.

        CR-02a: the artifact BINDING for a review is authority-bearing --
        None disables the reviewer-repair diff check entirely -- so an
        unreadable log must not be able to produce it."""
        for ev in self._require_events(project, events):
            if (ev.type is EventType.ATTEMPT_CREATED and ev.task_id == task_id
                    and ev.attempt_id == attempt_id):
                sha = ev.payload.get("pre_review_sha")
                return sha if isinstance(sha, str) and sha else None
        return None

    def _verdict_is_repaired(self, cfg: ProjectConfig, task_id: str) -> bool:
        """DR8: True when the committed <task>-REVIEW.md's VERDICT line carries
        the `(repaired)` marker adapters.py's REVIEW_INDEPENDENT bounded-repair
        note asks the reviewer to stamp (`VERDICT: APPROVED (repaired)`) on a
        self-repair. Mirrors _parse_reject_class's lookup -- reads the SAME
        committed report text (_review_report_text) _parse_review_verdict
        already bound "approved" against, no new git plumbing. Read-only."""
        text = effects_review.review_report_text(self._ports.git, cfg, task_id)
        if not text:
            return False
        return bool(re.search(
            r"^\s*VERDICT:\s*APPROVED\b[^\S\n]*\(repaired\)",
            text, re.IGNORECASE | re.MULTILINE,
        ))

    def _reviewer_repair_touched_paths(self, cfg: ProjectConfig, task_id: str,
                                       pre_sha: str) -> list[str] | None:
        """DR8: `git diff --name-only <pre_sha>..feat/<task_id>` -- the repo-
        root-relative paths the reviewer itself touched strictly after the
        pre-review baseline. Read-only.

        `[]` (empty diff -- the reviewer committed nothing) and `None` (the
        diff could not be taken at all) are DELIBERATELY DISTINCT, and
        conflating them is a fail-OPEN: "I found nothing" and "I could not
        look" are opposites in a bound check. `[]` is vacuously in-bounds;
        `None` makes the repair unverifiable, which _enforce_reviewer_repair
        treats exactly like a missing baseline -- rejected. An unverifiable
        repair is not a verified one (D-R8 refined, 2026-07-27).

        EXCLUDES the reviewer's own committed `<task>-REVIEW.md` (under
        cfg.reports_dir): every review -- repaired or not -- commits this
        file (the packet's REQUIRED OUTPUT CONTRACT), so it is orthogonal to
        the repair bound, never something the reviewer needed `repair_
        allowed` permission for. Without this exclusion EVERY `(repaired)`
        verdict would trip the out-of-bounds check on its own required
        artifact alone, making the whole permission a no-op. `git diff
        --name-only` prints paths relative to the TRUE git top-level (proven
        empirically -- `-C` does not re-root diff/show output the way it
        re-roots a `git show <rev>:<path>` INPUT pathspec, unlike the `./`
        trick _parse_review_verdict's own docstring documents), which can
        differ from cfg.root (nyxloom self-hosts with cfg.root=<repo>/
        nyxloom) -- matched by SUFFIX, not exact equality, so this is
        correct whether or not cfg.root is the repo top-level."""
        branch = f"feat/{task_id}"
        res = subprocess.run(
            ["git", "-C", str(cfg.root), "diff", "--name-only", f"{pre_sha}..{branch}"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            return None  # could not look -- NOT the same as "found nothing"
        own_review_rel = f"{cfg.reports_dir}/{task_id}-REVIEW.md"
        touched = []
        for p in res.stdout.splitlines():
            p = p.strip()
            if not p:
                continue
            if p == own_review_rel or p.endswith("/" + own_review_rel):
                continue
            touched.append(p)
        return touched

    def _reviewer_repair_offending_paths(self, cfg: ProjectConfig,
                                         touched: list[str]) -> list[str]:
        """DR8: pure (no git, no I/O) bound check -- the subset of `touched`
        that matches NONE of `cfg.policy.reviewer_repair_paths` (fnmatch on
        the repo-root-relative POSIX path, exactly as the glob-matching
        oracle specifies). Empty return means EVERY touched path is in
        bounds. Split out from _enforce_reviewer_repair so the matching rule
        itself is directly testable without a real git repo."""
        patterns = cfg.policy.reviewer_repair_paths
        return [p for p in touched if not any(fnmatch.fnmatch(p, pat) for pat in patterns)]

    def _revert_reviewer_repair(self, project: str, cfg: ProjectConfig,
                                task_id: str, pre_sha: str) -> bool:
        """DR8 (step 5): undo an OUT-OF-BOUNDS reviewer repair so no
        unreviewed production edit survives on feat/<task_id> -- `git revert
        --no-edit --no-merges <pre_sha>..<branch tip>` in a DETACHED scratch
        worktree (mirrors _run_post_merge_gate_bg's own scratch-worktree
        pattern; a revert needs a working tree, and a detached checkout never
        collides with the branch's own live worktree at cfg.root/
        cfg.worktree_root/branch), then CAS `update-ref` the branch onto the
        revert commit -- the SAME style precedent as the post-merge
        auto-revert above (log it, record an event -- here, the caller's
        REVIEW_RECORDED payload -- never crash the pass on a git failure).
        Returns False (and logs) on ANY git failure; the caller still forces
        the REJECTED/fixable outcome regardless -- a failed revert must never
        silently downgrade back to trusting the unverified repair.

        `-c user.name=/-c user.email=` are passed explicitly on the revert
        itself, not left to ambient global git config -- same reason
        _execute_auto_merge's own `--no-ff` merge commit does (see its
        docstring): a revert ALWAYS creates a commit, and an environment with
        no global identity configured (observed live in the test-runner
        container -- this exact gap, caught by its own gate run) fails with
        "Please tell me who you are" otherwise, which the `revert-failed`
        log/return-False path below would otherwise misreport as an ordinary
        git failure rather than a fixable identity gap."""
        branch = f"feat/{task_id}"
        repo_root = subprocess.run(
            ["git", "-C", str(cfg.root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True).stdout.strip()
        if not repo_root:
            log.error("reviewer-repair-revert-failed", project=project, task=task_id,
                      reason="repo-root-unresolved")
            return False
        tip_res = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--verify", branch],
            capture_output=True, text=True)
        tip = tip_res.stdout.strip()
        if tip_res.returncode != 0 or not tip:
            log.error("reviewer-repair-revert-failed", project=project, task=task_id,
                      reason="branch-tip-unresolved")
            return False
        scratch = Path(repo_root) / ".worktrees" / f"repair-revert-{task_id}"
        if scratch.exists():
            subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", str(scratch)],
                           capture_output=True, text=True)
        add = subprocess.run(
            ["git", "-C", repo_root, "worktree", "add", "--detach", str(scratch), tip],
            capture_output=True, text=True)
        if add.returncode != 0:
            log.error("reviewer-repair-revert-failed", project=project, task=task_id,
                      reason="worktree-add-failed", stderr=add.stderr[:200])
            return False
        ok = False
        try:
            rv = subprocess.run(
                ["git", "-C", str(scratch),
                 "-c", "user.name=nyxloomd", "-c", "user.email=nyxloomd@localhost",
                 "revert", "--no-edit", "--no-merges", f"{pre_sha}..{tip}"],
                capture_output=True, text=True)
            if rv.returncode != 0:
                log.error("reviewer-repair-revert-failed", project=project, task=task_id,
                          reason="revert-failed", stderr=rv.stderr[:200])
                subprocess.run(["git", "-C", str(scratch), "revert", "--abort"],
                               capture_output=True, text=True)
            else:
                new_head = subprocess.run(
                    ["git", "-C", str(scratch), "rev-parse", "HEAD"],
                    capture_output=True, text=True).stdout.strip()
                if new_head:
                    cas = subprocess.run(
                        ["git", "-C", repo_root, "update-ref",
                         f"refs/heads/{branch}", new_head, tip],
                        capture_output=True, text=True)
                    if cas.returncode == 0:
                        ok = True
                        log.warning("reviewer-repair-reverted", project=project, task=task_id,
                                   frm=tip, to=new_head)
                    else:
                        log.error("reviewer-repair-revert-failed", project=project, task=task_id,
                                  reason="update-ref-cas-refused", stderr=cas.stderr[:200])
        finally:
            subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", str(scratch)],
                           capture_output=True, text=True)
        return ok

    def _enforce_reviewer_repair(self, project: str, cfg: ProjectConfig,
                                 task_id: str, attempt_id: str
                                 ) -> tuple[str, dict[str, Any] | None]:
        """DR8 (step 5): post-hoc bounded-repair enforcement for a member whose
        committed verdict reads `VERDICT: APPROVED (repaired)`. Only called
        when cfg.policy.reviewer_repair is on and the verdict text-scan
        already read "approved" -- see the EmitAttemptExit REVIEW_INDEPENDENT
        call site.

        Returns ("approved", None) when the repair is IN BOUNDS, or the diff
        is EMPTY (a mis-stamped verdict -- the reviewer wrote `(repaired)`
        but committed nothing on the branch: not a violation, just noise) --
        proceeds exactly like an ordinary APPROVED, no new verification layer
        (the ordinary full gate rerun every commit already faces is what's
        trusted here, per reconcile.py module contract item 13: no separate
        verdict re-check belongs at merge time).

        Returns ("rejected", details) when the repair is INVALIDATED --
        `details["reason"]` is "missing_baseline" (pre_review_sha absent),
        "undeterminable_diff" (the baseline exists but the diff against it
        failed) -- both are the same rule, an unverifiable repair is never
        silently trusted, and note they must NOT collapse into the empty-diff
        "approved" path -- or "out_of_scope"
        (`details["offending_paths"]` names what the reviewer touched outside
        `reviewer_repair_paths`; `details["reverted"]` records whether the
        revert itself succeeded). An out-of-scope repair is reverted HERE
        (not by the caller) so the branch is clean the instant the
        REJECTED/fixable outcome is recorded -- never a window where an
        unreviewed production edit sits on a branch the caller has already
        stamped MERGE_READY-eligible."""
        pre_sha = self._pre_review_sha(project, task_id, attempt_id)
        if not pre_sha:
            log.warning("reviewer-repair-unverifiable", project=project, task=task_id,
                       attempt=attempt_id, reason="missing-pre-review-sha")
            return "rejected", {"reason": "missing_baseline"}
        touched = self._reviewer_repair_touched_paths(cfg, task_id, pre_sha)
        if touched is None:
            # The diff itself failed -- we cannot say WHAT the reviewer
            # touched, so we cannot say it stayed in bounds. Same rule as the
            # missing baseline above: unverifiable is not verified. Failing
            # open here would silently void the bound in exactly the
            # circumstance (a broken/mangled branch) where it matters most.
            log.warning("reviewer-repair-unverifiable", project=project, task=task_id,
                        attempt=attempt_id, reason="diff-failed")
            return "rejected", {"reason": "undeterminable_diff"}
        if not touched:
            return "approved", None
        offending = self._reviewer_repair_offending_paths(cfg, touched)
        if not offending:
            return "approved", None
        reverted = self._revert_reviewer_repair(project, cfg, task_id, pre_sha)
        log.warning("reviewer-repair-out-of-bounds", project=project, task=task_id,
                   attempt=attempt_id, offending=offending, reverted=reverted)
        return "rejected", {"reason": "out_of_scope", "offending_paths": offending,
                            "reverted": reverted}

    def _typed_verdict(self, project: str, task_id: str, attempt_id: str,
                       kind: results.ResultKind) -> tuple[str, str]:
        """THE review-authority read (CR-03, DR-13). Returns (verdict, why).

        Replaces `_parse_review_verdict`/`_parse_self_review_verdict`, which
        ran regexes over every `*REVIEW*.md` committed to a branch and decided
        whether a change could be merged from what that pool looked like.
        Their own docstrings record the incidents that shaped them: a review
        file named `P42-REVIEW.md` instead of `<task>-REVIEW.md` once
        fail-safed a genuinely approved task to REJECTED and stranded it; a
        stale `VERDICT: REJECTED` from a previous attempt once re-implemented
        work that was fine. Both were answered by adding a rule to the regex.

        This reads ONE file at ONE derived path -- the result the wrapper
        assembled for this exact attempt -- validates it against a schema, and
        compares its identity to the identity the daemon dispatched. The
        staleness case is now a mismatch rather than a heuristic, and a
        misnamed file is simply absent.

        Verdicts are the vocabulary the call sites already used:
          "approved" -- the reviewer passed the artifact.
          "rejected" -- the reviewer failed it, OR the record was refused.
                        A refused record is NOT an approval, and the second
                        element says which rejection code refused it so the
                        distinction survives into the transition note.
          "missing"  -- no result document at all: the leg never produced
                        one, which is a review-LEG failure and routes to a
                        relaunch rather than to a re-implementation.
        """
        path = (paths.attempt_dir(project, attempt_id)
                / results.result_filename(task_id))
        try:
            raw = path.read_bytes()
        except OSError:
            return "missing", "no typed result for this attempt"
        try:
            record = results.load_result(
                raw,
                expect=results.ResultIdentity(task_id=task_id, attempt_id=attempt_id),
                kind=kind,
                evidence_root=path.parent,
            )
        except results.ResultRejected as exc:
            # Fail closed, and say why. A refused record must never read as an
            # approval; it also must not read as "missing", because missing
            # means the leg produced nothing and this one produced something
            # that could not be trusted -- an operator repairs those
            # differently.
            log.warning("typed-result-refused", project=project, task=task_id,
                        attempt=attempt_id, code=exc.code.value, detail=exc.detail[:200])
            return "rejected", f"result refused: {exc.code.value}"
        if record.verdict is results.Verdict.APPROVED:
            return "approved", ""
        if record.verdict is results.Verdict.REJECTED:
            return "rejected", record.summary[:200]
        # DECLINED and FAILED are leg outcomes, not judgements about the work.
        # Neither may merge, and neither is a rejection of the artifact --
        # reporting them as "missing" routes to the relaunch that is actually
        # correct for both.
        return "missing", f"review leg {record.verdict.value}"

    def emit_attempt_exit(self, ctx: effects.EffectContext,
                      action: reconcile.EmitAttemptExit) -> list[Event]:
        """The effect families that have not moved yet.

        CR-05a took the lifecycle and gate families; CR-05b took review
        dispatch and the guarded merge. What is left is attempt
        dispatch/resume, the receipt-exit consumer, and carve -- registered
        in :meth:`_build_registry` with the package that owns moving them,
        and counted by ``effects.LEGACY_HANDLER_BUDGET`` in both directions,
        so this ladder can only shrink and cannot shrink silently.
        """
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        events: list[Event] = []

        task_id = action.task_id
        tsf = states[task_id]
        attempt = tsf.attempt_by_id(action.attempt_id)
        attempt_dir = paths.attempt_dir(project, action.attempt_id)
        receipt_data = json.loads((attempt_dir / "receipt.json").read_text(encoding="utf-8"))
        receipt = Receipt.from_dict(receipt_data)
        # P21 2026-07-16: git state is truth, receipts lie (live P93
        # lesson -- a receipt reported head_commit=null/files_touched=[]
        # while the branch actually held a real commit). Cross-check
        # before the receipt is used/logged below.
        self._crosscheck_head_commit(cfg, task_id, receipt)
        if attempt.state != AttemptState.EXITED:
            # Wrapper died before emitting its own exit event — heal it.
            attempt.state = AttemptState.EXITED
            attempt.ended = utc_now()
            attempt.receipt = receipt
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_EXITED,
                                           {"attempt": attempt.to_dict()}, task_id=task_id,
                                           attempt_id=action.attempt_id))
        # else: wrapper already recorded the exit; only the task
        # transition below remains (idempotent healing).

        result = receipt.result

        if attempt.role == Role.CARVER:
            # F018 P3a: a "carver-session-" task_id is a Start/Resume
            # persistent-session turn (this package), NOT a legacy
            # CarveDispatch attempt -- route it to the session-specific
            # consumer instead of _consume_carve_exit's CARVE-<seq>.md
            # REQUIRED OUTPUT CONTRACT parsing, which does not apply to
            # a bootstrap/resume turn. Feature-off byte-identical: this
            # prefix is only ever minted by _execute_start_carver_session/
            # _execute_resume_carver_session, themselves only ever
            # reached when the planner emits Start/ResumeCarverSession
            # (cfg.carve.session == "project-persistent"), so every
            # pre-P3a CARVER attempt (task_id "carve-<project>-<seq>")
            # falls through to _consume_carve_exit exactly as before.
            if task_id.startswith("carver-session-"):
                events.extend(self._carver.consume_session_exit(
                    ctx, task_id, action.attempt_id))
                return events
            # P16 2026-07-15: consume the carver's REQUIRED OUTPUT
            # CONTRACT (a CarveSummary file, not the wrapper's own
            # process-level Receipt above) -- see _consume_carve_exit
            # and the module docstring's carve-automation section.
            events.extend(self._carve.consume_carve_exit(ctx, task_id, action.attempt_id))
            return events

        if attempt.role == Role.REVIEW_INDEPENDENT:
            # F019 P1b: a REVIEW_INDEPENDENT leg whose task is STILL in
            # REVIEW_REJECTED and that carries no REVIEW_RECORDED binding is a
            # GATE-DIAGNOSIS dispatch (a normal wave review targets
            # AWAITING_REVIEW; the review that produced THIS rejection already
            # carries its own binding). Consume it CLASSIFY-ONLY: stamp the
            # reviewer's reject_class into a REVIEW_RECORDED{result: rejected}
            # in the SAME shape a review rejection uses, so the existing triage
            # table (_triage_classes -> reconcile) routes it. Crucially, DO NOT
            # transition -- the task stays REVIEW_REJECTED throughout, so an
            # un-re-gated diff can never reach the merge path (invariant #2).
            # The state+no-binding guard is what keeps an already-consumed
            # normal review (also REVIEW_REJECTED, but bound) out of here (O5).
            diag_tsf = states.get(task_id)
            if (diag_tsf is not None
                    and diag_tsf.state is TaskState.REVIEW_REJECTED
                    and not self._attempt_has_review_recorded(project, action.attempt_id)):
                if result is ReceiptResult.DONE:
                    reject_class = effects_review.parse_reject_class(self._ports.git, cfg, task_id)
                    diag_payload = {"result": "rejected"}
                    if reject_class:
                        diag_payload["reject_class"] = reject_class
                    events.append(self._append_ev(
                        project, cfg, states, EventType.REVIEW_RECORDED,
                        diag_payload, task_id=task_id,
                        attempt_id=action.attempt_id, wave_id=attempt.wave_id))
                else:
                    # An infra failure of the diagnosis LEG (LIMIT/ERROR/
                    # BLOCKED) is not a classification. Record "incomplete" so
                    # it binds the attempt (no re-consumption) and carries no
                    # class -> the task falls back to the mechanical retry
                    # path; a later gate-fail re-triggers diagnosis.
                    events.append(self._append_ev(
                        project, cfg, states, EventType.REVIEW_RECORDED,
                        {"result": "incomplete"}, task_id=task_id,
                        attempt_id=action.attempt_id, wave_id=attempt.wave_id))
                return events
            # 2026-07-15: consume the REVIEW receipt (was unmapped —
            # live deadlock). merge_mode=manual: MERGE_READY is declared,
            # never auto-merged (SPEC §7).
            # P33 2026-07-16: the receipt only reflects PROCESS exit
            # (wrapper.py infers DONE on any clean exit) -- it is never
            # the review's actual verdict (live P26 incident: a REJECTED
            # review report + clean exit rubber-stamped MERGE_READY).
            # The non-DONE receipt states (BLOCKED/LIMIT/ERROR) stay a
            # defense-in-depth fail-safe below WITHOUT reading the
            # report; only a DONE receipt's verdict is worth parsing.
            # P61 2026-07-20 (A9): ONE review attempt covers ALL wave
            # members. Fan the single exit out to a per-member verdict +
            # transition. IDEMPOTENT: the receipt-based reconcile scan can
            # emit an EmitAttemptExit for EACH member this pass (every
            # member's attempt copy is receipt-bearing); the FIRST to run
            # transitions every member out of AWAITING_REVIEW, so the rest
            # find no members and no-op here (the exit-healing preamble
            # above still ran for each, so every member's attempt copy
            # reaches EXITED).
            # A task is a member of THIS review iff it is still awaiting
            # review AND this attempt is its LATEST (attempts[-1]) -- the
            # same recency discriminator the 2026-07-17 stale-wave_id fix
            # uses. Without it, on a reject-loop's SECOND cycle the stale
            # first-review attempt (still EXITED in the task's history, and
            # re-emitted by the scan because the task is AWAITING_REVIEW
            # again) would be re-consumed and re-record its old verdict
            # against the fresh review's task -- masking the genuine second
            # review attempt.
            members = sorted(
                t for t, tsf_m in states.items()
                if tsf_m.state == TaskState.AWAITING_REVIEW
                and tsf_m.attempts
                and tsf_m.attempts[-1].attempt_id == action.attempt_id
            )
            if not members:
                return events  # stale/superseded exit, or already consumed by a sibling this pass

            # CR-07f: the target for each verdict comes from stages.py's
            # declared review_independent.exit_map -- the SAME source
            # stages.validate_pipeline reads -- rather than a second,
            # independently-maintained copy of the rule (the drift-risk
            # class CR-07a found and CR-07c fixed for implement.done).
            review_targets = dict(stages.effective_exit_map(
                stages.STAGE_REGISTRY["review_independent"], cfg.pipeline))

            if result is ReceiptResult.DONE:
                for member in members:
                    # P59b (A7): bind the verdict to THIS review attempt,
                    # per member. A re-review (a prior REVIEW_INDEPENDENT
                    # attempt for this member exists) counts only verdicts
                    # stamped with the current attempt id; the first review
                    # keeps the unbound path (no prior attempt -> no
                    # staleness possible).
                    # CR-03 (DR-13): the verdict is READ FROM THE TYPED
                    # RESULT this attempt produced, not scanned out of the
                    # markdown on the branch. The P59b binding this block
                    # used to compute by hand -- "count only verdicts
                    # stamped with the current attempt id" -- is now the
                    # identity comparison inside the reader, so a stale
                    # record from a previous attempt does not match and
                    # cannot be consumed.
                    verdict, verdict_why = self._typed_verdict(
                        project, member, action.attempt_id,
                        results.ResultKind.REVIEW_INDEPENDENT)
                    # DR8 (routing-model-redesign.md D-R8, refined): a
                    # `(repaired)` verdict is a PERMISSION, not a free
                    # pass -- mechanically re-check its blast radius AFTER
                    # the fact, here, before the reject_class text-scan
                    # below even runs. Only when the policy is on (else
                    # this whole path is inert, by design -- an operator
                    # who never granted the permission gets the ordinary
                    # pre-existing verdict handling regardless of what the
                    # text says) and only on a verdict the text-scan
                    # already read as "approved" (a genuine REJECTED
                    # review has nothing to enforce here). This is a
                    # daemon-recorded OVERRIDE that takes precedence over
                    # the text scan below -- the same "documented path
                    # first, then fall back" precedence _parse_review_
                    # verdict/_parse_reject_class already use, just at
                    # this call site rather than inside those two
                    # general-purpose parsers (which other, non-repair
                    # callers -- LaunchGateDiagnosis, _rescope_context --
                    # also use and must not be affected).
                    repair_details: dict[str, Any] | None = None
                    if (cfg.policy.reviewer_repair and verdict == "approved"
                            and self._verdict_is_repaired(cfg, member)):
                        verdict, repair_details = self._enforce_reviewer_repair(
                            project, cfg, member, action.attempt_id)
                    # B4b (D-060 triage Tier-2; D-066): on a genuine rejection
                    # the reviewer stamps a REJECT_CLASS line into the SAME
                    # committed report -- capture it in the event so
                    # _triage_classes (at input-build) can route the reject by
                    # {fixable, architectural, product}. None on approval, or
                    # when an older/unclassified reviewer stamped no line ->
                    # reconcile's mechanical attempt-budget fallback.
                    payload = {"result": verdict}
                    if verdict != "approved":
                        if repair_details is not None:
                            # DR8: the invalidated-repair override IS the
                            # class -- `fixable` (a real, local, low-
                            # blast-radius gap the reviewer both
                            # identified and knew how to fix; see D-R8's
                            # rationale for why this routes to a targeted
                            # retry, not a re-carve). Never falls through
                            # to the text-scan below: the committed
                            # REVIEW.md carries no REJECT_CLASS line (it
                            # says APPROVED), so that scan would find
                            # nothing anyway.
                            payload["reject_class"] = "fixable"
                            payload["repair_invalidated"] = True
                            payload.update(repair_details)
                        else:
                            reject_class = effects_review.parse_reject_class(self._ports.git, cfg, member)
                            if reject_class:
                                payload["reject_class"] = reject_class
                    events.append(self._append_ev(
                        project, cfg, states, EventType.REVIEW_RECORDED,
                        payload, task_id=member,
                        attempt_id=action.attempt_id, wave_id=attempt.wave_id))
                    if verdict == "approved":
                        events.append(self._transition(ctx, member,
                                                        review_targets["approved"], None))
                    else:
                        note = f"review verdict: {verdict} (receipt: {result.value})"
                        if verdict_why:
                            # CR-03: a refused record and a genuine
                            # rejection both land here, and an operator
                            # repairs them differently -- so the reason
                            # the reader gave travels into the note rather
                            # than only into a log line.
                            note += f" -- {verdict_why}"
                        if repair_details is not None:
                            note += f" -- reviewer repair invalidated: {repair_details.get('reason')}"
                        events.append(self._transition(ctx, member,
                                                        review_targets["rejected"], note))
            else:
                # P56 2026-07-20 (M7). A non-DONE review receipt
                # (LIMIT/ERROR/BLOCKED) is an INFRA failure of the review
                # LEG covering the WHOLE wave, not a semantic rejection of
                # any member's work. Record every member "incomplete" (NOT
                # "rejected") so a provider outage does not pollute
                # review_rejections_by_area (which counts only
                # result=="rejected") and trip SpecAttention('rejections')
                # -> a false runaway auto-pause; ProviderPause the review
                # route ONCE on a LIMIT so the re-review does not dive
                # straight back into the same rate limit. Members still go
                # to REVIEW_REJECTED below (defense-in-depth, unchanged).
                if result is ReceiptResult.LIMIT:
                    events.extend(self._lifecycle.pause_provider(
                        ctx, attempt.route.route_id, members[0]))
                for member in members:
                    events.append(self._append_ev(
                        project, cfg, states, EventType.REVIEW_RECORDED,
                        {"result": "incomplete"}, task_id=member,
                        attempt_id=action.attempt_id, wave_id=attempt.wave_id))
                    events.append(self._transition(ctx, member,
                                                    review_targets["rejected"],
                                                    f"review verdict: incomplete (receipt: {result.value})"))
            return events

        if attempt.role == Role.SELF_REVIEW:
            # B5 2026-07-20: consume the self_review leg's verdict for a
            # SELF_REVIEWING task. Like the frontier reviewer (P33), the
            # verdict lives in a COMMITTED report, never the process receipt
            # (a clean exit says nothing about the finding). approved ->
            # AWAITING_REVIEW (hand to the frontier reviewer); rejected ->
            # QUEUED (a fresh, budget-bounded fix attempt -- deliberately NOT
            # ACTIVE, which would re-expose the ACTIVE-scoped stale-receipt
            # re-consumption the frontier reject loop avoids; see D-063). A
            # non-DONE receipt or a missing verdict degrades to
            # AWAITING_REVIEW: self-review is an OPTIONAL pre-gate, so never
            # BLOCK a task because its cheap self-check could not complete --
            # the frontier reviewer (the real gate) still runs.
            if states[task_id].state is not TaskState.SELF_REVIEWING:
                return events  # stale/superseded exit; the task already moved on
            verdict = (self._typed_verdict(project, task_id, action.attempt_id,
                                            results.ResultKind.SELF_REVIEW)[0]
                       if result is ReceiptResult.DONE else "missing")
            # CR-07f: the target comes from stages.py's declared
            # self_review.exit_map -- the SAME source stages.validate_pipeline
            # reads -- rather than a second, independently-maintained copy of
            # the rule. Only "approved"/"rejected" are declared labels; any
            # other verdict (including "missing", a non-DONE receipt or an
            # unreadable typed result) degrades to AWAITING_REVIEW -- self-
            # review is an OPTIONAL pre-gate, so it never BLOCKS a task
            # because its cheap self-check could not complete.
            self_review_targets = dict(stages.effective_exit_map(
                stages.STAGE_REGISTRY["self_review"], cfg.pipeline))
            target = self_review_targets.get(verdict, TaskState.AWAITING_REVIEW)
            if target is TaskState.QUEUED:
                note = "self-review verdict: rejected -- fresh fix attempt"
            else:
                note = (None if verdict == "approved"
                        else f"self-review {verdict} (receipt: {result.value}) -- proceeding to frontier review")
            events.append(self._transition(ctx, task_id, target, note))
            return events

        if result is ReceiptResult.DONE:
            # implement-done: route to the self_review stage when it is
            # composed into the pipeline (the warm self-check before the
            # expensive frontier reviewer); else straight to frontier review
            # -- byte-identical to pre-B5 for a legacy (no-self_review)
            # pipeline. attempt.role is IMPLEMENTER here unconditionally: every
            # other role (CARVER, REVIEW_INDEPENDENT, SELF_REVIEW) returns
            # above before this point, and those are all four members of Role
            # (test_a_non_implementer_done_receipt_is_unreachable pins it).
            # The target comes from stages.effective_exit_map -- the SAME
            # function validate_pipeline and the shadow-compile projection
            # read -- rather than a second, independently-maintained copy of
            # this rule (CR-07c: the two copies had already drifted once).
            target = dict(stages.effective_exit_map(
                stages.STAGE_REGISTRY["implement"], cfg.pipeline))["done"]
            events.append(self._transition(ctx, task_id, target, None))
        elif result is ReceiptResult.BLOCKED:
            blocker = Blocker(type=BlockerType.CONTRACT, unblock_condition="triage BLOCKED reason",
                               detail=(receipt.blocked_reason or "")[:200])
            events.append(self._append_ev(project, cfg, states, EventType.TASK_BLOCKED,
                                           {"from": states[task_id].state.value,
                                            "blocker": blocker.to_dict()}, task_id=task_id))
        elif result is ReceiptResult.SCOPE_AMENDMENT:
            # B21 2026-07-23 (D-R16 §3, scope-amendment escalation): a
            # mid-flight "I need file X outside my scope.touch allowlist"
            # request (see adapters.classify_log_tail's SCOPE_AMENDMENT_
            # REQUEST: marker + wrapper.py's receipt translation). Bounded
            # like D-R8's reviewer-fix cap: under
            # MAX_SCOPE_AMENDMENTS_PER_TASK prior approvals for this task,
            # approve it (record BOTH the raw request and the approval as
            # events -- the APPROVED count IS the cap's enforcement) and
            # re-dispatch via the EXISTING legal ACTIVE->QUEUED edge (the
            # same one the LIMIT branch below uses) -- no new TaskState,
            # no TASK_TRANSITIONS edit (D-B21-3). The widened allowlist
            # itself never touches the handoff file on disk (D-B21-1
            # overlay) -- DispatchImplementer's build_dispatch call
            # reads these SAME SCOPE_AMENDMENT_APPROVED events back
            # (_scope_amendment_files) and injects the approved file(s)
            # into the re-dispatch prompt. At the cap, fall through to
            # the SAME hard-BLOCK the CONTRACT-blocked path above uses --
            # this is what makes the escalation bounded, never an
            # infinite loop.
            amendment = receipt.amendment_request or {}
            requested_file = amendment.get("file") or "(unspecified)"
            reason = amendment.get("reason") or ""
            prior_count = len(self._scope_amendments_approved(project, task_id))
            if prior_count < MAX_SCOPE_AMENDMENTS_PER_TASK:
                events.append(self._append_ev(
                    project, cfg, states, EventType.SCOPE_AMENDMENT_REQUESTED,
                    {"file": requested_file, "reason": reason}, task_id=task_id))
                events.append(self._append_ev(
                    project, cfg, states, EventType.SCOPE_AMENDMENT_APPROVED,
                    {"file": requested_file, "reason": reason}, task_id=task_id))
                events.append(self._transition(
                    ctx, task_id, TaskState.QUEUED,
                    f"scope amendment approved: {requested_file} -- "
                    "re-dispatching with widened allowlist"))
            else:
                blocker = Blocker(
                    type=BlockerType.CONTRACT,
                    unblock_condition="triage BLOCKED reason",
                    detail=(f"scope-amendment cap ({MAX_SCOPE_AMENDMENTS_PER_TASK}) "
                            f"reached; further request: {requested_file} -- {reason}")[:200])
                events.append(self._append_ev(project, cfg, states, EventType.TASK_BLOCKED,
                                               {"from": states[task_id].state.value,
                                                "blocker": blocker.to_dict()}, task_id=task_id))
        elif result is ReceiptResult.LIMIT:
            events.append(self._transition(ctx, task_id, TaskState.QUEUED, None))
            events.extend(self._lifecycle.pause_provider(ctx, attempt.route.route_id, task_id))
        elif result is ReceiptResult.ERROR:
            # P60 (M8): the single role-filtered accessor -- was this same
            # role-blind formula inline, counting reviews against the budget.
            if reconcile.attempts_used(states[task_id]) < cfg.policy.max_attempts_per_task:
                events.append(self._transition(ctx, task_id, TaskState.QUEUED, None))
            else:
                blocker = Blocker(type=BlockerType.ENVIRONMENT,
                                   unblock_condition="triage BLOCKED reason",
                                   detail="attempts exhausted")
                events.append(self._append_ev(project, cfg, states, EventType.TASK_BLOCKED,
                                               {"from": states[task_id].state.value,
                                                "blocker": blocker.to_dict()}, task_id=task_id))

        # No `else` branch: an action with no owner never reaches here. The
        # registry refuses it at lookup (effects.UnownedAction), and only the
        # types registered as legacy are routed to this method at all.
        return events

def specs(effector: ExitEffector) -> tuple[effects.HandlerSpec, ...]:
    return (
        effects.HandlerSpec(
            action_type=reconcile.EmitAttemptExit,
            kind="emit-attempt-exit",
            handler=effector.emit_attempt_exit,
            emits=frozenset({EventType.ATTEMPT_EXITED, EventType.REVIEW_RECORDED,
                             EventType.TASK_TRANSITIONED, EventType.TASK_BLOCKED,
                             EventType.SCOPE_AMENDMENT_REQUESTED,
                             EventType.SCOPE_AMENDMENT_APPROVED}),
            idempotency_key=lambda a: f"emit-attempt-exit:{a.attempt_id}",
        ),
    )
