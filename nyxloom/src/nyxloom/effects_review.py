"""Review effects: the frontier review wave and gate-failure diagnosis.

WHAT THESE TWO SHARE (CR-05b)
-----------------------------
Both dispatch the SAME role -- an independent frontier reviewer -- against a
committed diff, through the same machinery: admission, packet assembly, warm
session reuse, wrapper launch. They differ in what they are ASKED, and the
difference is load-bearing:

* :meth:`ReviewEffector.launch_review` opens a wave over AWAITING_REVIEW tasks
  and asks for a merge verdict. One attempt covers every member.
* :meth:`ReviewEffector.launch_gate_diagnosis` targets ONE task that a
  deterministic gate already failed, and asks only for a CLASSIFICATION. The
  task stays REVIEW_REJECTED throughout, so an un-re-gated diff can never
  reach the merge path; the reviewer is a classifier here, never a
  controller.

The receipt-exit consumption that reads either one's verdict is CR-05c's --
it lives with the attempt lifecycle, because what the daemon does with an
exit depends on the attempt's ROLE rather than on which effector dispatched
it. This module owns the dispatch half and the committed-report readers both
halves need.

THE REPORT READERS ARE HERE ON PURPOSE
--------------------------------------
:func:`review_report_text` and :func:`parse_reject_class` read the reviewer's
committed prose. CR-03 removed prose from the MERGE decision -- that comes
from a typed judgement bound to the attempt -- and these two are what is
left: routing hints and re-dispatch text, carrying no merge authority. They
live in the review module so the later packages that also read a reject class
(the exit consumer, the carve re-scope) depend on ONE implementation rather
than growing a second.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, Sequence

from . import (
    adapters, config, effects, effects_dispatch, gate_runner, paths, reconcile,
    results, stages, wrapper,
)
from .config import ProjectConfig
from .log import get_logger
from .types import (
    Attempt, AttemptState, Event, EventType, Role, Route, TaskStateFile,
    new_id, utc_now,
)

log = get_logger("effects.review")

#: The reject classes a reviewer may self-stamp. Closed, because an
#: unrecognised value must read as UNCLASSIFIED (falling back to the
#: mechanical attempt-budget path) rather than as a class nothing routes.
REJECT_CLASSES = "fixable|architectural|incapable|product|transient"

_REJECT_CLASS_RE = re.compile(
    rf"^\s*REJECT_CLASS:\s*({REJECT_CLASSES})\b", re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# committed-report readers


def review_report_text(git: Any, cfg: ProjectConfig, task_id: str) -> str | None:
    """The reviewer's committed ``<task_id>-REVIEW.md`` on ``feat/<task_id>``.

    Documented path first, then -- only when that is absent or empty -- a
    broadened search for any ``*REVIEW*.md`` on the branch whose name or
    content references the task. The broadened arm exists because a live
    incident had a reviewer write its report to the wrong filename, and a
    report that exists but cannot be found reads exactly like a reviewer that
    never ran.
    """
    branch = f"feat/{task_id}"
    rel_path = f"{cfg.reports_dir}/{task_id}-REVIEW.md"
    show_res = git.show(str(cfg.root), f"{branch}:./{rel_path}")
    if show_res.returncode == 0 and show_res.stdout.strip():
        return show_res.stdout
    ls_res = git.ls_tree_names(str(cfg.root), branch, f"./{cfg.reports_dir}")
    if ls_res.returncode == 0:
        for path in ls_res.stdout.splitlines():
            path = path.strip()
            if not path or path == rel_path:
                continue
            name = path.rsplit("/", 1)[-1]
            if "REVIEW" not in name.upper() or not name.upper().endswith(".MD"):
                continue
            show2 = git.show(str(cfg.root), f"{branch}:./{path}")
            if show2.returncode != 0:
                continue
            if task_id in name or task_id in show2.stdout:
                return show2.stdout
    return None


def parse_reject_class(git: Any, cfg: ProjectConfig, task_id: str) -> str | None:
    """The reviewer's self-stamped reject class, lowercased, or None.

    A ROUTING HINT, never authority: it selects which remedy a rejection gets,
    and the rejection itself came from the typed judgement. None when the line
    is absent or its value is outside the closed vocabulary, which leaves the
    task unclassified and falls back to the mechanical attempt budget.
    """
    text = review_report_text(git, cfg, task_id)
    if not text:
        return None
    m = _REJECT_CLASS_RE.search(text)
    return m.group(1).lower() if m else None


def latest_gate_failure(events: Sequence[Event],
                        task_id: str) -> tuple[str, str]:
    """``(output_tail, phase)`` of this task's most recent FAILED gate.

    Only pre-merge and mutation phases: a post-merge failure is a different
    remedy (revert or block), not something a reviewer classifies. ``("", "")``
    when none is found, which degrades gracefully -- the reviewer still
    classifies from the diff and handoff.
    """
    out_tail, phase = "", ""
    for ev in events:
        if ev.type is EventType.GATE_FINISHED and ev.task_id == task_id:
            gr = (ev.payload or {}).get("gate_result") or {}
            if (gr.get("phase") in ("pre-merge", "mutation")
                    and gr.get("exit_code", 0) != 0):
                out_tail = gr.get("output_tail", "") or ""
                phase = gr.get("phase", "") or ""
    return out_tail, phase


def task_branch_head_sha(git: Any, cfg: ProjectConfig,
                         task_id: str) -> str | None:
    """``feat/<task_id>``'s current HEAD, read-only.

    Recorded at review dispatch as the PRE-REVIEW baseline a reviewer
    repair's blast radius is later measured from. None when the branch cannot
    be resolved -- and a missing baseline INVALIDATES an unverifiable repair
    rather than silently trusting it.
    """
    return git.resolve_verify(str(cfg.root), f"feat/{task_id}")


def incapable_escalation_note(git: Any, cfg: ProjectConfig, fm) -> str:
    """A terse, NON-SPECIFIC note when this task re-does incapable work.

    Resolved from the handoff's ``source.ref`` provenance marker -- the origin
    task's id, NOT ``depends_on``, which would gate this dispatch on the
    origin reaching COMPLETED, something a rejected-then-superseded origin
    never does.

    Deliberately says nothing about WHAT was wrong: the reviewer must form an
    independent judgement of THIS implementation, and telling it the previous
    findings would anchor exactly the judgement being asked for. Returns ``""``
    for every ordinary task, which makes the prompt append a no-op.
    """
    if fm is None or fm.source is None:
        return ""
    origin_id = fm.source.ref
    if not origin_id:
        return ""
    if parse_reject_class(git, cfg, origin_id) != "incapable":
        return ""
    return (
        "This is a fresh implementation from a higher-tier model, dispatched "
        "because a previous reviewer judged the assigned tier incapable of "
        "this task across multiple dimensions. Form your OWN independent "
        "judgment of THIS implementation -- do not assume anything about "
        "what was previously wrong."
    )


def warmest_review_session(states: dict[str, TaskStateFile]) -> str | None:
    """The most recently started EXITED review session's handle, or None.

    Warm resume replays the ~35-40k role-contract prefix from prompt cache.
    It is a CACHE optimization and never a relaxation of verdict binding: the
    resumed leg still mints a fresh attempt id and the reader still compares
    identity, so a stale judgement from the prior wave cannot be consumed.

    The tie is broken on `attempt_id`, not left to iteration order. `max`
    returns the FIRST maximal element, so with two EXITED review attempts
    sharing a `started` stamp the winner followed `states` iteration order --
    and the daemon builds that mapping by scanning a directory, so the choice
    genuinely varied run to run. CR-06b repaired the identical tie in the
    planner's own copy of this selection (`rules_review`, reached via
    `LaunchReview.resume_session`); this is the SECOND implementation, on the
    gate-diagnosis launch path, and leaving it meant one tie resolving two
    ways in one product.

    The safety argument is the one already accepted for the planner copy, and
    it is structural rather than empirical: comparing `(started, attempt_id)`
    compares `started` first, so the winner is always drawn from the
    `started`-maximal set. The tie-break can only choose among candidates this
    function's own criterion already calls equal -- it can never reach past a
    tie to a strictly older session -- and the worst case of choosing a
    different tied handle is a prompt-cache miss, never a correctness change.
    """
    prior = [a for tsf in states.values() for a in tsf.attempts
             if a.role is Role.REVIEW_INDEPENDENT
             and a.state is AttemptState.EXITED and a.session_handle]
    if not prior:
        return None
    return max(prior, key=lambda a: (a.started, a.attempt_id)).session_handle


class ReviewEffector:
    """Dispatches the independent reviewer. Holds no state of its own."""

    def __init__(self, ports: effects.EffectPorts) -> None:
        self._ports = ports

    # -- gate-failure diagnosis -----------------------------------------

    def launch_gate_diagnosis(
            self, ctx: effects.EffectContext,
            action: reconcile.LaunchGateDiagnosis) -> list[Event]:
        """Ask the reviewer to CLASSIFY one gate failure. Never to decide it.

        The gate already decided "fail". This dispatch produces a
        ``REVIEW_RECORDED{reject_class}`` through the exit consumer, in the
        same shape a review rejection uses, so the existing triage table
        routes it -- and the task stays REVIEW_REJECTED throughout.
        ``wave_id=None`` is the marker the consumer uses to tell a diagnosis
        leg from a wave review.
        """
        ok, _reason = effects_dispatch.admissible(ctx, "review")
        if not ok:
            return []  # admission refused; task stays REVIEW_REJECTED, retried next pass
        events: list[Event] = []
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        task_id = action.task_id
        tsf_d = states[task_id]
        attempt_id = new_id("att")
        attempt_dir = paths.attempt_dir(project, attempt_id)
        packet_dir = attempt_dir / "packet"
        packet_dir.mkdir(parents=True, exist_ok=True)
        branch = f"feat/{task_id}"
        diff_text = self._ports.git.diff(str(cfg.root),
                                         f"{cfg.default_branch}...{branch}")
        (packet_dir / f"{task_id}.diff").write_text(diff_text, encoding="utf-8")
        out_tail, phase = latest_gate_failure(ctx.events(), task_id)
        packet_lines = [
            "# Gate-failure diagnosis packet",
            "",
            "## Your role: INDEPENDENT REVIEWER — GATE-FAILURE DIAGNOSIS (classify only)",
            "",
            f"The deterministic {phase or 'merge'} gate for `{task_id}` FAILED. The",
            "gate ALREADY decided \"fail\" — you are NOT re-deciding pass/fail, NOT",
            "approving, and NOT merging or editing the implementation. Read the gate",
            "output, the diff, and the handoff, then CLASSIFY the failure so the",
            "pipeline routes it to the right place.",
            "",
            f"Write `{cfg.reports_dir}/{task_id}-REVIEW.md` containing EXACTLY one line:",
            "  `REJECT_CLASS: <fixable|architectural|product|transient>`",
            "  - fixable       — a bounded code/test defect a targeted re-implementation fixes",
            "  - architectural — design/scope is wrong; a same-base retry cannot fix it (re-scope)",
            "  - product       — a product/direction decision is required (escalate to a human)",
            "  - transient     — a flaky/infra gate failure unrelated to the diff (plain retry)",
            f"Commit the report to `{branch}`. Do NOT merge, approve, or edit the code.",
            "",
            f"### Gate output tail ({phase or 'gate'})",
            "```",
            out_tail or "(no captured gate output)",
            "```",
            "",
            f"### Diff ({cfg.default_branch}...{branch}) — full text in {task_id}.diff here",
            "### Handoff",
            f"- {tsf_d.handoff_path or '(no handoff path on record)'}",
        ]
        (packet_dir / "packet.md").write_text("\n".join(packet_lines), encoding="utf-8")

        routes_obj = config.Routes.load()
        route_def = effects_dispatch.route_for_role(
            routes_obj, Role.REVIEW_INDEPENDENT.value, project=project,
            kind="review")
        if route_def is None:
            return []  # no review route configured; task stays REVIEW_REJECTED
        # Re-asked with the resolved route (CR-13a): the review role's route
        # is chosen here, not by the planner, so the containment half of
        # admission has nothing to answer about until this line has run.
        ok, _reason = effects_dispatch.admissible(ctx, "review", route_def)
        if not ok:
            return []  # admission refused; task stays REVIEW_REJECTED, retried next pass
        route_snap = Route(route_id=route_def.route_id, cli=route_def.cli,
                           model=route_def.model, variant=route_def.variant,
                           effort=route_def.effort, routes_rev=routes_obj.revision)
        resume_session = None
        if "session-reuse" in stages.stage_context("review_independent"):
            resume_session = warmest_review_session(states)
        log.info("gate-diagnosis-launch", project=project, task=task_id,
                 route=route_def.route_id, phase=phase, resumed=bool(resume_session))
        attempt = Attempt(attempt_id=attempt_id, role=Role.REVIEW_INDEPENDENT,
                          state=AttemptState.CREATED, route=route_snap,
                          started=self._ports.clock.now(), wave_id=None)
        created_payload: dict[str, Any] = {"attempt": attempt.to_dict()}
        if resume_session:
            created_payload["resumed_from"] = resume_session
        events.append(ctx.append(EventType.ATTEMPT_CREATED, created_payload,
                                 task_id=task_id, attempt_id=attempt_id))
        hint = effects_dispatch.gate_hint(cfg)
        receipt_path = str(attempt_dir / "receipt.json")
        packet_md = str(packet_dir / "packet.md")
        if resume_session:
            resume_prompt = adapters.review_resume_prompt(
                packet_path=packet_md, attempt_id=attempt_id,
                gate_hint=hint, spine_pointer=None)
            argv = adapters.build_resume(
                route_def, session=resume_session, worktree=str(cfg.root),
                prompt=resume_prompt)
        else:
            argv, _prompt = adapters.build_dispatch(
                route_def, handoff_path=packet_md, worktree=str(cfg.root),
                branch=cfg.default_branch, task_id=task_id, gate_hint=hint,
                receipt_path=receipt_path, role=Role.REVIEW_INDEPENDENT,
                attempt_id=attempt_id, approved_amendments=[],
            )
        fm_d = effects_dispatch.frontmatter_for(cfg, tsf_d)
        spec = wrapper_spec(
            project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
            cwd=str(cfg.root), attempt_dir=attempt_dir,
            receipt_path=receipt_path, route_def=route_def,
            repo_root=str(cfg.root),
            leases=effects_dispatch.lease_specs(cfg, fm_d))
        attempt.pid = self._ports.processes.launch_detached(spec)
        attempt.state = AttemptState.PREFLIGHTING
        events.append(ctx.append(EventType.ATTEMPT_PREFLIGHTED,
                                 {"attempt": attempt.to_dict()},
                                 task_id=task_id, attempt_id=attempt_id))
        return events

    # -- the review wave -------------------------------------------------

    def launch_review(self, ctx: effects.EffectContext,
                      action: reconcile.LaunchReview) -> list[Event]:
        """Dispatch ONE review attempt covering every member of a wave.

        The single attempt is recorded on EVERY member, so each member's
        latest attempt is this review (the per-task in-flight guard then sees
        it for all of them and does not relaunch) and the per-member verdict
        binding reads a correct review history.
        """
        ok, _reason = effects_dispatch.admissible(ctx, "review")
        if not ok:
            return []  # admission refused; task stays AWAITING_REVIEW, re-evaluated next pass
        events: list[Event] = []
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        wave_id = action.wave_id
        attempt_id = new_id("att")
        attempt_dir = paths.attempt_dir(project, attempt_id)
        packet_dir = attempt_dir / "packet"
        packet_dir.mkdir(parents=True, exist_ok=True)
        packet_lines = _review_packet_header(cfg, attempt_id)
        spine_pointer = None
        if "spine-digest" in stages.stage_context("review_independent"):
            spine_pointer = f"{cfg.reports_dir}/SPINE-DIGEST.md"
            packet_lines.extend([
                "## Standing spine digest (read from the repo; not inlined here)",
                f"- {spine_pointer} -- the carver-maintained catalog of standing",
                "  invariants, recent review reflections, and open risks. Read it",
                "  in the repo for cross-cutting context; it is referenced here by",
                "  pointer, never pasted.",
                "",
            ])
        for t in action.task_ids:
            packet_lines.extend(
                self._member_packet_section(cfg, states.get(t), t, packet_dir))
        (packet_dir / "packet.md").write_text("\n".join(packet_lines), encoding="utf-8")

        routes_obj = config.Routes.load()
        route_def = effects_dispatch.route_for_role(
            routes_obj, Role.REVIEW_INDEPENDENT.value, project=project,
            kind="review")
        if route_def is None:
            return []  # no review route configured; task stays AWAITING_REVIEW
        # Re-asked with the resolved route -- see launch_gate_diagnosis.
        ok, _reason = effects_dispatch.admissible(ctx, "review", route_def)
        if not ok:
            return []  # admission refused; task stays AWAITING_REVIEW, re-evaluated next pass
        log.info("review-launch", project=project, tasks=list(action.task_ids),
                 route=route_def.route_id, wave=wave_id)
        route_snap = Route(route_id=route_def.route_id, cli=route_def.cli,
                           model=route_def.model, variant=route_def.variant,
                           effort=route_def.effort, routes_rev=routes_obj.revision)
        members = list(action.task_ids)
        first_task = members[0] if members else None
        # A launch with no members is a no-op (nothing to review).
        record_on = members or ([first_task] if first_task else [])
        attempt = Attempt(attempt_id=attempt_id, role=Role.REVIEW_INDEPENDENT,
                          state=AttemptState.CREATED, route=route_snap,
                          started=self._ports.clock.now(), wave_id=wave_id)
        for t in record_on:
            created_payload: dict[str, Any] = {"attempt": attempt.to_dict()}
            if action.resume_session:
                created_payload["resumed_from"] = action.resume_session
            if cfg.policy.reviewer_repair:
                # The PRE-review baseline for this member's own branch: the
                # boundary "everything committed after this is the reviewer's"
                # that a repair's blast radius is measured against. Only
                # resolved when the policy is on -- an unused rev-parse per
                # member is pointless when the whole post-hoc path is inert.
                pre_sha = task_branch_head_sha(self._ports.git, cfg, t)
                if pre_sha:
                    created_payload["pre_review_sha"] = pre_sha
                else:
                    log.warning("reviewer-repair-baseline-unresolved",
                                project=project, task=t, attempt=attempt_id)
            events.append(ctx.append(EventType.ATTEMPT_CREATED, created_payload,
                                     task_id=t, attempt_id=attempt_id,
                                     wave_id=wave_id))

        hint = effects_dispatch.gate_hint(cfg)
        receipt_path = str(attempt_dir / "receipt.json")
        packet_md = str(packet_dir / "packet.md")
        # Every approved scope-amendment file across ALL wave members, so the
        # reviewer is told about every widened allowlist in this review rather
        # than one member's.
        pass_events = ctx.events()
        approved_amendments: list[str] = []
        for t in members:
            for f in effects_dispatch.scope_amendment_files(pass_events, t):
                if f not in approved_amendments:
                    approved_amendments.append(f)
        # Review depth and the escalation note are both scoped to the wave's
        # PRIMARY task rather than aggregated, mirroring the gate hint's own
        # project-level scope. Both return "" for the neutral case, so the
        # prompt append is a no-op then.
        first_tsf = states.get(first_task) if first_task else None
        first_fm = (effects_dispatch.frontmatter_for(cfg, first_tsf)
                    if first_tsf is not None else None)
        review_gate = gate_runner.select_verification_gate(cfg)
        review_depth = adapters.compute_review_depth_directive(
            scope_touch=first_fm.scope.touch if first_fm is not None else [],
            gate_asserts=review_gate.asserts if review_gate is not None else [],
        )
        escalation_note = incapable_escalation_note(self._ports.git, cfg, first_fm)
        if action.resume_session:
            # Warm resume: the role-contract prefix replays from prompt cache.
            # The binding is PRESERVED -- the resume prompt threads THIS fresh
            # attempt id and requires the identical stamp the cold dispatch
            # does, so a warm session still holding the prior wave's packet
            # cannot misbind a stale verdict.
            resume_prompt = adapters.review_resume_prompt(
                packet_path=packet_md, attempt_id=attempt_id,
                gate_hint=hint, spine_pointer=spine_pointer)
            argv = adapters.build_resume(
                route_def, session=action.resume_session,
                worktree=str(cfg.root), prompt=resume_prompt)
        else:
            argv, _prompt = adapters.build_dispatch(
                route_def, handoff_path=packet_md, worktree=str(cfg.root),
                branch=cfg.default_branch,
                task_id=first_task or wave_id or "review",
                gate_hint=hint, receipt_path=receipt_path,
                role=Role.REVIEW_INDEPENDENT, attempt_id=attempt_id,
                approved_amendments=approved_amendments,
                review_depth=review_depth, escalation_note=escalation_note,
                repair_allowed=cfg.policy.reviewer_repair,
            )
        # The review holds the UNION of its members' leases, so a concurrent
        # carve or dispatch cannot touch a task while it is under review.
        review_leases: list[dict[str, Any]] = []
        seen_leases: set[str] = set()
        for t in members:
            tsf_t = states.get(t)
            fm_t = (effects_dispatch.frontmatter_for(cfg, tsf_t)
                    if tsf_t is not None else None)
            for ls in effects_dispatch.lease_specs(cfg, fm_t):
                if ls["name"] not in seen_leases:
                    seen_leases.add(ls["name"])
                    review_leases.append(ls)
        spec = wrapper_spec(
            project=project, task_id=first_task or wave_id or "review",
            attempt_id=attempt_id, argv=argv, cwd=str(cfg.root),
            attempt_dir=attempt_dir, receipt_path=receipt_path,
            route_def=route_def, repo_root=str(cfg.root),
            leases=review_leases,
            # A wave review judges EVERY member in one run, so the wrapper is
            # TOLD the membership: it cannot derive it, and scanning the
            # worktree for files that look like reviews is the defect the
            # typed-result package removed.
            result_tasks=list(members))
        attempt.pid = self._ports.processes.launch_detached(spec)
        attempt.state = AttemptState.PREFLIGHTING
        for t in record_on:
            events.append(ctx.append(EventType.ATTEMPT_PREFLIGHTED,
                                     {"attempt": attempt.to_dict()},
                                     task_id=t, attempt_id=attempt_id,
                                     wave_id=wave_id))
        return events

    def _member_packet_section(self, cfg: ProjectConfig,
                               tsf_t: TaskStateFile | None, task_id: str,
                               packet_dir) -> list[str]:
        """One wave member's section: committed diff, and UNCOMMITTED state.

        The uncommitted half is not belt-and-braces. Commit discipline is not
        guaranteed, so a committed-only diff misses real work still sitting in
        the task's worktree -- and that worktree may be torn down, taking the
        work with it. The reviewer is told to review it too.
        """
        root = str(cfg.root)
        branch = f"feat/{task_id}"
        rev_range = f"{cfg.default_branch}...{branch}"
        (packet_dir / f"{task_id}.diff").write_text(
            self._ports.git.diff(root, rev_range), encoding="utf-8")
        lines = [f"## {task_id}"]
        if tsf_t is not None and tsf_t.handoff_path:
            lines.append(f"- handoff: {tsf_t.handoff_path}")
        lines.append(f"### COMMITTED ({rev_range})")
        lines.append(f"- diff stat:\n{self._ports.git.diff_stat(root, rev_range)}")
        lines.append("")
        worktree_path = cfg.root / cfg.worktree_root / branch
        lines.append("### UNCOMMITTED (worktree — may be lost on teardown; REVIEW IT)")
        if not self._ports.files.exists(worktree_path):
            lines.append(
                f"- worktree {worktree_path} is absent (already torn down); "
                "no uncommitted state could be captured.")
        else:
            wt = str(worktree_path)
            status = self._ports.git.status_porcelain(wt)
            unstaged = self._ports.git.diff_unstaged(wt)
            staged = self._ports.git.diff_staged(wt)
            if not (status.strip() or unstaged.strip() or staged.strip()):
                lines.append("- clean: no uncommitted changes in the worktree.")
            else:
                lines.append(f"- git status --porcelain:\n{status}")
                lines.append(f"- unstaged diff:\n{unstaged}")
                lines.append(f"- staged diff (--cached):\n{staged}")
        lines.append("")
        return lines


def wrapper_spec(*, project: str, task_id: str, attempt_id: str, argv: list[str],
                 cwd: str, attempt_dir, receipt_path: str, route_def,
                 leases: list[dict[str, Any]], repo_root: str,
                 result_tasks: list[str] | None = None):
    """A review leg's ``WrapperSpec``.

    Always carries ``result_kind``: this leg produces a TYPED judgement, and
    the wrapper collecting it is what makes the merge decision readable from
    a schema rather than scanned out of prose.

    ``repo_root`` is required rather than defaulted (CR-13a): it is the only
    host path a contained leg is given, and a default would make "the launch
    site forgot" indistinguishable from "this leg needs nothing".
    """
    return wrapper.WrapperSpec(
        project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
        cwd=cwd, log_path=str(attempt_dir / "attempt.log"),
        receipt_path=receipt_path, attempt_dir=str(attempt_dir),
        route_def=asdict(route_def), repo_root=repo_root, leases=leases,
        result_kind=results.ResultKind.REVIEW_INDEPENDENT.value,
        result_tasks=result_tasks,
    )


def _review_packet_header(cfg: ProjectConfig, attempt_id: str) -> list[str]:
    """The reviewer's role contract.

    Item 7 is what drives the pipeline: the typed judgement, stamped with
    THIS attempt id. The prose report in item 6 is for HUMANS -- nothing
    parses it, which is why its formatting is explicitly the reviewer's
    choice. A task with no readable judgement is a review LEG failure and the
    review is relaunched: never read as an approval, and never as a rejection
    of the work.
    """
    return [
        "# Review packet",
        "",
        "## Your role: INDEPENDENT FRONTIER REVIEWER (merge gate)",
        "",
        "You are reviewing another agent's committed work — you did",
        "not write it. For each task below (2026-07-15 role contract;",
        "the first live review wrote implementer artifacts instead):",
        "1. Read the handoff contract, then the diff (<task>.diff",
        "   here, or `git diff main...feat/<task>` in the repo).",
        "2. Verify actual git state — git state is truth, receipts",
        f"   lie: run `git log {cfg.default_branch}..feat/<task>` and",
        "   `git status` in the worktree. Do NOT trust the receipt's",
        "   `head_commit` / `files_touched` / `oracles` fields: they",
        "   have been observed null/empty even when real work was",
        "   committed (live P93 lesson). If the worktree holds",
        "   UNCOMMITTED changes (see the UNCOMMITTED section below,",
        "   per task), review them too — the implementer's commit",
        "   discipline is not guaranteed; do not treat uncommitted",
        "   work as nonexistent.",
        "3. Adversarially verify against the handoff's oracles:",
        "   hollow tests, overclaimed evidence, missing handoff",
        "   requirements, edge-case gaps, env-specific claims.",
        "4. Re-run the handoff's declared gate yourself; never trust",
        "   a report's pasted output.",
        "5. Small defects: fix them YOURSELF, commit to the task's",
        "   feat/ branch. Large/architectural defects: REJECT.",
        f"6. Write {cfg.reports_dir}/<task>-REVIEW.md: findings,",
        "   what you fixed, verdict + reasoning, for HUMAN readers.",
        "   Commit it to the feat/ branch (NOT main). Do NOT merge.",
        "   Do NOT write the implementer's LOG/REPORT. Its formatting",
        "   is yours to choose: nothing in the pipeline parses it.",
        "7. REQUIRED, and this is what drives the pipeline: for EACH",
        "   task you reviewed, write a typed judgement as JSON to",
        "   `nyxloom-judgement-<task>.json` in the worktree root:",
        '   {"schema_version": ' + str(results.SCHEMA_VERSION) + ',',
        '    "kind": "review_independent", "task_id": "<task>",',
        '    "attempt_id": "' + attempt_id + '",',
        '    "verdict": "approved" or "rejected",',
        '    "summary": "<one line>"}',
        "   The attempt_id above is THIS review attempt -- copy it",
        "   exactly. It is what binds your verdict to this run, so a",
        "   judgement left by an earlier attempt cannot be consumed.",
        "   A task with no readable judgement is a review LEG failure",
        "   and the review is relaunched: never read as an approval,",
        "   and never as a rejection of the work.",
        "",
    ]


def specs(effector: ReviewEffector) -> tuple[effects.HandlerSpec, ...]:
    return (
        effects.HandlerSpec(
            action_type=reconcile.LaunchReview,
            kind="launch-review",
            handler=effector.launch_review,
            emits=frozenset({EventType.ATTEMPT_CREATED,
                             EventType.ATTEMPT_PREFLIGHTED}),
            idempotency_key=lambda a: f"launch-review:{a.wave_id}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.LaunchGateDiagnosis,
            kind="launch-gate-diagnosis",
            handler=effector.launch_gate_diagnosis,
            emits=frozenset({EventType.ATTEMPT_CREATED,
                             EventType.ATTEMPT_PREFLIGHTED}),
            idempotency_key=lambda a: f"launch-gate-diagnosis:{a.task_id}",
        ),
    )
