"""Carve dispatch and proposal admission (CR-05f).

WHAT CARVING IS, AND WHY IT IS AN EFFECT
----------------------------------------
Carving is the factory writing its OWN next unit of work: an agent reads the
project's roadmap, backlog, recent reviews and merge history, and produces a
handoff document another agent will implement. That makes it the one effect
whose output is an INPUT to the planner, which is why every guard around it
is about authority rather than correctness -- a carve that runs twice, or
that writes outside its authority branch, corrupts the queue rather than one
task.

TWO SHAPES, ONE ENTRY POINT
---------------------------
:meth:`CarveEffector.carve_dispatch` runs the carve. When the project has a
warm persistent carver session it NORMALIZES into a session resume turn
instead of minting a fresh throwaway agent -- same authority, same lease,
different launch -- which is why this module and `effects_carver` share the
sequence counter and the snapshot rather than each keeping their own.

:meth:`CarveEffector.admit_carve_proposal` is the other half: a carver turn
produces a PROPOSAL, and admission is what turns it into a real task. It is
deliberately separate so that producing and accepting are different effects
with different failure modes -- a bad turn wastes a turn, a bad admission
puts work in the queue.

THE PACKET IS MOST OF THE CODE
------------------------------
Everything between is packet assembly: what the carver is told about the
project's state, its recent rejections, its verdict-audit disputes, and where
its output must go. That volume is the point -- the packet is the entire
interface between the factory's state and the agent that extends it, and it
is assembled from committed facts rather than from anything an agent said.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from . import (
    adapters, backlog_items, carver_session, config, decisions, doc_lifecycle,
    effects, effects_attempt, effects_dispatch, effects_review, frontmatter,
    lint, paths, reconcile, snapshot, stages, storage, wrapper,
)
from .config import ProjectConfig
from .log import get_logger
from .types import (
    Attempt, AttemptState, CarverStatus, Event, EventType, ReceiptResult, Role,
    Route,
    TERMINAL_ATTEMPT_STATES, TERMINAL_TASK_STATES, TaskState, TaskStateFile,
    iso, new_id, utc_now,
)

log = get_logger("effects.carve")

#: The carver's REQUIRED OUTPUT CONTRACT record. Imported LAZILY inside the
#: consumer: it is still declared in `daemon.py`, and a module-level import
#: would make this effector import the shell -- the one direction the
#: boundary forbids. Moving the dataclass itself is CR-07's, which owns the
#: persisted-type surface.

# v2 §8 stop-policy outcomes (docs/SPEC.md §8), inherited verbatim. These are
# the CARVER agent's SELF-REPORTED run outcome (summary.outcome) -- what its
# whole carve pass concluded about the roadmap runway. The carver decides them.
_CARVE_OUTCOMES = frozenset({
    "CANDIDATES_READY", "MILESTONE_COMPLETE", "ROADMAP_EXHAUSTED",
    "SPEC_GAP", "DECISION_REQUIRED", "EXTERNAL_BLOCKER", "BUDGET_EXHAUSTED",
})

# B7 2026-07-20 (P75, D-060 carver re-scope entry; D-067): the DAEMON-determined
# fate stamped on the ORIGINAL rejected task's carve-driven SUPERSEDED event when
# a re-scope carve is launched to replace it. Deliberately NOT a member of
# _CARVE_OUTCOMES above: the carver never reports RESCOPED (it is not a stop-policy
# conclusion about its run); the daemon writes it, atomically, only AFTER the
# re-scope carve dispatch actually launches. Mirrors the stages.py carve exit label
# ("rescope_superseded" -> SUPERSEDED), which B7 makes real. It is an OUTCOME
# marker, NOT a new TaskState -- the origin task lands in the existing SUPERSEDED.
_RESCOPE_OUTCOME = "RESCOPED"


class CarveEffector:
    """Runs carves and admits their proposals.

    Holds a reference to the CARVER effector, not to the shell: the two share
    a sequence counter and a session snapshot because a carve can normalize
    into a session turn, and effector-to-effector composition is the allowed
    direction.
    """

    def __init__(self, ports: effects.EffectPorts, carver: Any,
                 lifecycle: Any) -> None:
        self._ports = ports
        self._carver = carver
        # A carve leg that exits on a provider LIMIT pauses the route, which
        # is the LIFECYCLE effector's effect -- reached by composition rather
        # than by growing a second pause registry here.
        self._lifecycle = lifecycle

    # -- forwarding seams -------------------------------------------------

    def _append_ev(self, project: str, cfg: ProjectConfig,
                   states: dict[str, TaskStateFile], ev_type: EventType,
                   payload: dict[str, Any], **kw: Any) -> Event:
        return self._ports.journal.append(project, cfg, states, ev_type,
                                          payload, **kw)

    def _require_events(self, project: str,
                        events: Sequence[Event] | None = None) -> Sequence[Event]:
        if events is not None:
            return events
        return self._ports.event_log.require(project)

    def _needs_operator_recently_emitted(self, project: str, reason: str,
                                         *, events: Sequence[Event] | None = None) -> bool:
        return effects.needs_operator_recently_emitted(
            self._require_events(project, events), reason)

    @staticmethod
    def _carve_kind(states: dict[str, TaskStateFile], task_id: str | None) -> str:
        """D-065 (B63): recover a synthetic carve task's kind at OUTCOME time,
        when only the task is in hand. Reads the `kind=<x>` marker
        _execute_carve_dispatch writes into the task's notes -- which is
        replayed state (it rides the TASK_CREATED statefile), so this survives
        a daemon restart between the carve's dispatch and its exit. Anything
        without the marker is 'headroom', matching the dispatch-side default
        and keeping every pre-B63 carve in the log classified exactly as
        before."""
        tsf = states.get(task_id) if task_id else None
        if tsf is None or not tsf.notes:
            return "headroom"
        for token in tsf.notes.split():
            if token.startswith("kind="):
                return token[len("kind="):]
        return "headroom"

    def _head_revision(self, cfg: ProjectConfig,
                        *, builder: snapshot.SnapshotBuilder | None = None,
                        ) -> snapshot.SnapshotInput[str]:
        """AUTHORITATIVE git fact: the current default-branch HEAD sha.

        The triage stage's drift-guard (``reconcile._premise_drifted``) tells a
        handoff whose ``input_revision`` premise has gone stale against main
        from one still valid. Before CR-02a a git failure returned None and the
        drift-guard fail-safed to "no drift" -- so an unreadable repository
        silently asserted that every premise was still current and re-dispatched
        work against a base that may have moved (P40's own root cause). An
        unknown HEAD is now an unavailable authoritative input.

        Resolves ``cfg.default_branch`` (not HEAD): the daemon process may sit
        on a feat/ worktree, but the premise a handoff is measured against is
        always main -- exactly the ref ``_merged_branches`` already uses.
        """
        b = builder if builder is not None else snapshot.SnapshotBuilder()

        def _load() -> str:
            res = subprocess.run(
                ["git", "-C", str(cfg.root), "rev-parse", cfg.default_branch],
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode != 0:
                raise RuntimeError(f"git rev-parse exited {res.returncode}")
            sha = res.stdout.strip()
            if not sha:
                raise ValueError("git rev-parse produced no revision")
            return sha

        return b.authoritative(
            "git_head_revision", _load,
            provenance=snapshot.Provenance("git", f"{cfg.root}#{cfg.default_branch}"),
            reason=snapshot.Reason.PROBE_FAILED)

    @staticmethod
    def _first_live_source(cfg: ProjectConfig, candidates: list[str | None]) -> Path | None:
        """First candidate (repo-root-relative or None) that resolves to an
        existing, non-archived file. None entries (an unset spine config
        key) are skipped. CR-01 (DR-04): the archive check is containment-
        only (doc_lifecycle.is_archived), so this never has to read a
        candidate's content to reject it."""
        for candidate in candidates:
            if not candidate:
                continue
            resolved = cfg.root / candidate
            if resolved.exists() and not doc_lifecycle.is_archived(cfg.root, resolved):
                return resolved
        return None

    def _recent_review_follow_ups(self, project: str, limit: int = 10,
                                   *, events: Sequence[Event] | None = None
                                   ) -> list[tuple[str, str]]:
        """Carve source #1: recent REVIEW_RECORDED (task_id, result) pairs,
        newest first, capped at `limit`."""
        out: list[tuple[str, str]] = []
        for ev in reversed(list(self._require_events(project, events))):
            if ev.type is EventType.REVIEW_RECORDED and ev.task_id:
                out.append((ev.task_id, ev.payload.get("result", "?")))
                if len(out) >= limit:
                    break
        return out

    def _carve_source_note_lines(self, cfg: ProjectConfig,
                                  item_id: str | None = None) -> list[str]:
        """Carve sources #2/#3 (backlog, roadmap/gap-analysis): name the
        conventional file paths the carver reads itself (same economy as the
        review packet's diff-only embedding: point, don't slurp). ProjectConfig
        has no 'product_sources' field today (config.py is frozen beyond the
        P16-authorized Policy fields), so this probes fixed conventional paths.
        B2 2026-07-16: prefer the nyxloom-trove layout (backlog.md/roadmap.md
        under the managed trove) and fall back to the legacy docs/ convention
        for un-migrated projects -- mirroring config.load()'s own trove-first/
        legacy-fallback resolution of nyxloom.toml.

        P41 2026-07-16: when `item_id` names a single targeted backlog item
        (dispatch_targeted_carve), this embeds THAT item's brief (gated on
        backlog_items.is_briefed) instead of the generic file pointers below
        -- so a direct carve of a briefed item loses no interview context.
        An un-briefed/legacy item still gets only a plain reference (no
        invented brief).

        CR-01 (DR-04) 2026-08-03: resolution order is now the ADOPTED
        direction-spine config key (`cfg.backlog`/`cfg.roadmap`, e.g.
        nyxloom-trove/4-backlog.md / 3-roadmap.md) FIRST, then the plain
        trove convention, then the legacy docs/ path -- a project that
        adopted the spine (like nyxloom itself) must never fall through to
        a stale legacy doc just because the plain `nyxloom-trove/roadmap.md`
        filename doesn't exist. Every candidate is also checked against
        doc_lifecycle.is_archived (containment-only, no content read) so an
        archived/superseded document can never surface here even if a
        caller later repoints a legacy path back onto one by hand -- this is
        the daemon's "context collection" surface CR-01's contract names."""
        if item_id is not None:
            return self._targeted_item_note_lines(cfg, item_id)

        lines = []
        backlog = self._first_live_source(cfg, [cfg.backlog, "nyxloom-trove/backlog.md", "docs/BACKLOG.md"])
        lines.append(
            f"- backlog: {backlog.relative_to(cfg.root)}"
            if backlog is not None
            else "- backlog: none found (nyxloom-trove/backlog.md, docs/BACKLOG.md)"
        )
        roadmap = self._first_live_source(cfg, [cfg.roadmap, "nyxloom-trove/roadmap.md", "docs/ROADMAP.md"])
        if roadmap is not None:
            lines.append(f"- roadmap: {roadmap.relative_to(cfg.root)}")
        gap_files = sorted(
            p for p in ((cfg.root / "docs").glob("gap-*.md") if (cfg.root / "docs").exists() else [])
            if not doc_lifecycle.is_archived(cfg.root, p)
        )
        if gap_files:
            lines.append("- gap analysis: " + ", ".join(
                str(p.relative_to(cfg.root)) for p in gap_files))
        if roadmap is None and not gap_files:
            lines.append(
                "- roadmap/gap analysis: none found "
                "(nyxloom-trove/roadmap.md, docs/ROADMAP.md, docs/gap-*.md)"
            )
        return lines

    def _targeted_item_note_lines(self, cfg: ProjectConfig, item_id: str) -> list[str]:
        """P41 2026-07-16: the ONE carve source for a targeted carve --
        item_id's own intake brief, embedded verbatim (not a file pointer).
        A backlog with no such item, or one that is not is_briefed (legacy
        un-headered bullet, or a headered item with no detail), yields a
        plain reference line only -- never a fabricated brief.

        The brief is NOT the detail prose alone. intake_chat._parse_brief
        splits a P29 reply into title/Priority:/Decisions:/free prose, and
        backlog_items.create() then persists priority + decisions as HEADER
        tokens, leaving only the prose on the bullet's continuation lines.
        So embedding item.detail alone would silently drop the priority the
        interview explicitly asked the operator for (step 6) and the D-NNN
        decisions the intake agent filed on their behalf (step 4) -- the
        exact interview context this package exists to preserve. Emit the
        header fields alongside the prose. Decisions are named, not slurped
        (the carver reads decisions.md itself -- same point-don't-slurp
        economy as the untargeted source notes above)."""
        path = backlog_items.resolve_path(cfg)
        items = backlog_items.parse(path)
        item = next((it for it in items if it.id == item_id), None)
        rel = path.relative_to(cfg.root)
        if item is None:
            return [f"- targeted backlog item {item_id}: not found in {rel}"]
        if not backlog_items.is_briefed(item):
            return [f"- targeted backlog item {item_id} (status={item.status}): "
                    "no intake brief on file"]
        lines = [f"- targeted backlog item {item_id} -- intake brief:"]
        if item.priority is not None:
            lines.append(f"  priority: {item.priority}")
        if item.decisions:
            lines.append(f"  linked decisions: {', '.join(item.decisions)} "
                         "(read this project's decisions.md for their content)")
        lines.extend(f"  {ln}" for ln in item.detail.splitlines())
        return lines

    def _sample_verdict_audit_tasks(self, states: dict[str, TaskStateFile],
                                     sample_size: int) -> list[TaskStateFile]:
        """F007 2026-07-27 (GAP2): the recently-COMPLETED tasks a verdict-audit
        pass samples, most-recent-first (`since` descending), bounded to
        `sample_size`. An idle/small project with fewer COMPLETED tasks than
        the bound samples all of them -- this is a floor, not a target. When
        the bound truncates, the DROPPED task ids are logged (never silently
        discarded -- oracle 5): an operator reading the log can see exactly
        which completed work this pass did NOT re-judge, rather than having
        to infer it from an unbounded search of `states`."""
        completed = sorted(
            (tsf for tsf in states.values() if tsf.state is TaskState.COMPLETED),
            key=lambda t: t.since, reverse=True,
        )
        sampled = completed[:sample_size]
        dropped = [t.task_id for t in completed[sample_size:]]
        if dropped:
            log.info("verdict-audit-sample-truncated", dropped_count=len(dropped),
                      dropped_ids=dropped, sample_size=sample_size)
        return sampled

    def _task_final_diff(self, cfg: ProjectConfig, tsf: TaskStateFile) -> str | None:
        """F007 2026-07-27 (GAP2): the FINAL MERGED diff a completed task
        introduced -- `git diff <merge_commit>^1 <merge_commit>`, i.e. exactly
        what landed when feat/<task_id> merged (the merge commit's own parent
        range, not a moving comparison against the CURRENT default branch tip,
        which would pick up every unrelated change merged since). This is the
        ONLY task-specific evidence the verdict-audit's blind judgment sees,
        alongside the handoff's own oracles -- deliberately never the recorded
        verdict, rationale, or REJECT_CLASS (see _verdict_audit_section_lines).

        Returns None -- DISTINCT from a genuine empty-string diff (a real
        no-op merge) -- when the diff cannot be resolved at all: no recorded
        `merge_commit` (a COMPLETED task predating merge-commit tracking, or a
        non-branch carve authority) or the git command itself fails (unknown
        commit, shallow clone, corrupt object). The caller renders these two
        cases with different, honest prose rather than treating an
        unresolvable diff as if it were a boringly-empty one.

        EXCLUDES `cfg.reports_dir` via a git pathspec magic exclude. A real
        review commits `<task>-REVIEW.md` (VERDICT/REJECT_CLASS/rationale)
        onto the SAME feat/<task_id> branch this diff is taken from (see
        _review_report_text) -- without this exclusion the exact content
        this whole extension exists to withhold would leak straight through
        the diff itself, making 'blind' a fiction. No other path is
        excluded: everything else the task actually changed is legitimate
        evidence for the blind judgment."""
        if not tsf.merge_commit:
            return None
        res = subprocess.run(
            ["git", "-C", str(cfg.root), "diff", f"{tsf.merge_commit}^1", tsf.merge_commit,
             "--", ".", f":(exclude){cfg.reports_dir}/*"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            return None
        return res.stdout

    def _verdict_audit_section_lines(self, cfg: ProjectConfig,
                                      states: dict[str, TaskStateFile],
                                      sample_size: int) -> list[str]:
        """F007 2026-07-27 (GAP2, plan-gap-engine-and-reviewer-repair.md
        §GAP2): the BLIND verdict-audit section appended to a gap-audit carve
        packet. Extends the EXISTING `kind == 'gap-audit'` branch (not a
        sibling `kind`) -- both share the exact same cadence trigger, carve
        authority, and REQUIRED OUTPUT CONTRACT tail, so there was no
        separate dispatch mechanism worth standing up; only this SOURCE
        section differs, the same axis every other carve mode already
        branches on.

        Samples recently-COMPLETED tasks (_sample_verdict_audit_tasks),
        grouped by the handoff's `component` field (a task whose handoff is
        missing/unparsable, or whose frontmatter omits `component`, still
        audits -- grouped under '(uncategorized)', never crashing or being
        dropped). For each task this embeds ONLY its oracles and its final
        merged diff (_task_final_diff) -- it NEVER calls
        _parse_review_verdict/_review_rationale/_parse_reject_class, so the
        recorded verdict, the reviewer's prose, and the REJECT_CLASS are
        structurally absent from this prompt text, not merely instructed
        away (mirrors D-R3's escalation-note non-anchoring precedent:
        adapters.py's REVIEW_INDEPENDENT branch proves the same property by
        never being HANDED the prior findings in the first place)."""
        lines = [
            "## Verdict audit: judge these COMPLETED tasks BLIND",
            "For EACH task below, form your OWN independent judgment of whether "
            "it should be APPROVED or REJECTED, using ONLY the oracles and diff "
            "shown -- you are not told what was actually decided at the time. "
            "Report every judgment in the `verdict_audit` field of your JSON "
            "output contract (see below). Do not carve a package for a task "
            "purely because you would have rejected it; the daemon compares "
            "your judgment against the record and decides what, if anything, "
            "needs a follow-up.",
            "",
        ]
        sampled = self._sample_verdict_audit_tasks(states, sample_size)
        if not sampled:
            lines.append("No COMPLETED tasks are available to sample this pass.")
            lines.append("")
            return lines
        grouped: dict[str, list[tuple[TaskStateFile, Any]]] = {}
        for tsf in sampled:
            fm = effects_dispatch.frontmatter_for(cfg, tsf)
            component = fm.component if (fm is not None and fm.component) else "(uncategorized)"
            grouped.setdefault(component, []).append((tsf, fm))
        for component in sorted(grouped):
            lines.append(f"### Component: {component}")
            for tsf, fm in grouped[component]:
                lines.append(f"#### Task {tsf.task_id}")
                if fm is not None and fm.oracles:
                    lines.append("Oracles:")
                    for o in fm.oracles:
                        lines.append(f"- {o.id}: {o.observable} (negative: {o.negative})")
                else:
                    lines.append("Oracles: (handoff unavailable -- judge from the diff alone)")
                diff = self._task_final_diff(cfg, tsf)
                if diff is None:
                    lines.append("Diff: (unavailable -- could not resolve the merged diff)")
                elif diff == "":
                    lines.append("Diff: (empty -- merge introduced no changes)")
                else:
                    lines.append("Diff:")
                    lines.append("```diff")
                    lines.append(diff)
                    lines.append("```")
                lines.append("")
        return lines

    def _verdict_audit_disputes(self, project: str,
                                 judgments: list[dict[str, str]],
                                 *, events: Sequence[Event] | None = None,
                                 ) -> list[dict[str, str]]:
        """F007 2026-07-27 (GAP2): THE COMPARISON -- daemon-side, never the
        agent's. `judgments` is the carver's self-reported BLIND per-task
        verdicts (CarveSummary.verdict_audit, built with no access to the
        recorded verdict -- see _verdict_audit_section_lines). For each, this
        reads the RECORDED verdict and compares. A mismatch is a DISPUTED
        carve candidate naming the task; agreement contributes nothing (the
        common, boring case). Malformed entries (no task_id, or a judgment
        that is neither APPROVED nor REJECTED) are skipped rather than
        mis-flagged.

        CR-03 (DR-13): the recorded verdict now comes from the daemon's OWN
        `REVIEW_RECORDED` event rather than from a fresh markdown scan. That
        is the same fact the merge decision was made on -- re-deriving it from
        the branch could disagree with what the daemon actually did, and an
        audit that compares against a re-derivation audits the derivation
        instead of the decision. A task with no recorded review is skipped:
        there is nothing to dispute."""
        disputes: list[dict[str, str]] = []
        recorded_by_task: dict[str, str] = {}
        for ev in self._require_events(project, events):
            if ev.type is EventType.REVIEW_RECORDED and ev.task_id:
                recorded_by_task[ev.task_id] = str((ev.payload or {}).get("result", ""))
        for j in judgments:
            task_id = str(j.get("task_id", "")).strip()
            blind = str(j.get("judgment", "")).strip().upper()
            if not task_id or blind not in ("APPROVED", "REJECTED"):
                continue
            recorded = recorded_by_task.get(task_id)
            if recorded is None:
                continue
            recorded_norm = "APPROVED" if recorded == "approved" else "REJECTED"
            if blind != recorded_norm:
                disputes.append({
                    "task_id": task_id,
                    "blind_judgment": blind,
                    "recorded_verdict": recorded_norm,
                })
        return disputes

    def _rescope_context(self, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                         origin_task_id: str) -> dict:
        """B7 2026-07-20 (P75, D-060 carver re-scope entry; critique CRITIQUE.md:206).
        Assemble the re-scope packet inputs for an origin task that triage routed to
        READY_TO_CARVE (architectural / stale-premise / attempt-exhausted). The
        strategic carver -- the only component with whole-system context -- reads
        these and decides re-carve vs. drop vs. escalate-as-D-NNN, so a same-base
        retry that could never fix the flagged defect is never blindly re-issued
        (the critique's ban on the bare context-free retry, at the carve tier).

        Every field degrades gracefully: a missing/unparsable handoff -> input_revision
        None -> drifted False (the same fail-safe-to-no-drift rule as
        reconcile._premise_drifted); an un-committed review -> verdict None. A re-scope
        carve MUST still launch with whatever context exists -- the ATOMIC supersede
        (emitted only AFTER the carve launches) is what must never be skipped, not the
        packet's richness. Read-only: no writes, no transitions.

        D-R3 (2026-07-26, refined; routing-model-redesign.md D-R3): also assembles
        `reject_class` (the origin's self-stamped triage class, re-read directly via
        `_parse_reject_class` -- the same helper that produced the class stamped on
        the REVIEW_RECORDED event in the first place, so no new plumbing) and
        `origin_tier` (the origin handoff's `tier:` frontmatter field). Both degrade
        gracefully to None (missing report, missing/unparsable handoff) -- consumed
        by `_carve_packet_body_lines`'s rescope branch to distinguish an
        `incapable` tier-bump from every other re-scope reason; None on either key
        falls back to the existing generic re-scope text unchanged."""
        origin = states.get(origin_task_id)
        handoff_path = origin.handoff_path if origin else None
        input_revision: str | None = None
        origin_tier: str | None = None
        if handoff_path:
            try:
                fm, _body = frontmatter.parse_handoff(cfg.root / handoff_path)
                input_revision = fm.input_revision
                origin_tier = fm.tier
            except Exception:  # census: advisory-degradation (CR-02b)
                # Both values only ENRICH the re-scope prompt -- the origin's
                # input revision and tier tell the carver what it is replacing.
                # An unreadable origin handoff yields a less-informed carve,
                # never a wrongly-authorized one, and the planner's own drift
                # guard reads the revision from the event log rather than here.
                input_revision = None
                origin_tier = None
        # Re-scope PROMPT context. The planner's own drift guard reads the
        # authoritative ReconcileInput.head_revision, which the fan-in has
        # already proven available before any action was planned; this copy
        # only decorates the carver packet, so .value is the right read.
        head_revision = self._head_revision(cfg).value
        return {
            "origin_task_id": origin_task_id,
            "handoff_path": handoff_path,
            "verdict": effects_attempt.review_rationale(self._ports.git, cfg, origin_task_id),
            "input_revision": input_revision,
            "head_revision": head_revision,
            "drifted": reconcile._premise_drifted(input_revision, head_revision),
            "reject_class": effects_review.parse_reject_class(self._ports.git, cfg, origin_task_id),
            "origin_tier": origin_tier,
        }

    def _carve_packet_body_lines(self, cfg: ProjectConfig, project: str, seq: int,
                                  states: dict[str, TaskStateFile],
                                  own_task_id: str | None = None,
                                  item_id: str | None = None,
                                  rescope: dict | None = None,
                                  kind: str = "headroom") -> list[str]:
        """F018 P3c: the carve packet's SOURCE-CONTEXT body, extracted
        verbatim from _build_carve_packet (same params, same content, same
        order) so a project-persistent session's carve-mode resume turn
        (_build_carver_carve_prompt) can carry the IDENTICAL mode-specific
        source context (headroom / re-scope / targeted-intake / test-health)
        the legacy fresh-dispatch path already builds, without a second,
        independently-drifting copy of this prose. Returns a mutable lines
        list (never joined) so each caller appends its own REQUIRED OUTPUT
        CONTRACT tail. See _build_carve_packet's own docstring for the full
        per-mode rationale."""
        lines = [
            f"# Carve packet {seq}",
            "",
            "## Your role: CARVER",
            "",
        ]
        if kind == "test-health":
            gate_cmd = effects_dispatch.gate_hint(cfg) or "(no gate configured for this project)"
            lines.extend([
                f"You are performing a periodic project-WIDE TEST-HEALTH review "
                f"for project '{project}'. This is NOT a per-task job and NOT a "
                "queue refill: step back from the work roadmap entirely and assess "
                "the health of the TEST SUITE AS A WHOLE, then carve handoff "
                "package(s) that improve it. Do NOT implement the improvements "
                "yourself -- you carve packages for other agents to pick up later.",
                "",
                "## Carve source: the test suite's standing debt",
                "Look for debt the per-change gate structurally CANNOT catch. That",
                "gate enforces coverage on lines a change TOUCHES, so it says",
                "nothing about code that was already there and already untested --",
                "which is exactly what this pass exists to find. Specifically:",
                "- modules with low or no coverage that no recent change has touched;",
                "- behaviour covered only by happy-path tests, with no negative/error",
                "  case asserting the contract actually fails when it should;",
                "- tests that pass without asserting the behavioral contract they",
                "  name (hollow tests) -- a passing suite is not evidence of health;",
                "- missing regression tests for defects that reached review or prod;",
                "- slow, flaky, or interdependent tests that erode the gate's value.",
                "",
                f"This project's gate command is:\n    {gate_cmd}",
                "Run it (or just its coverage leg) to get real numbers rather than",
                "guessing. Prefer evidence you actually measured over impressions.",
                "",
                "## Carve NOTHING if the suite is healthy",
                "You are explicitly authorized to return an EMPTY `carved` list. A",
                "periodic trigger that always produces packages manufactures",
                "busywork and devalues every real finding. If you find no debt worth",
                'a package, report `"carved": []` with outcome MILESTONE_COMPLETE',
                "and say why in review_reflection.",
                "",
                "## Reporting notes specific to this pass",
                '- Use `"source_kind": "review"` for anything you carve here.',
                "- Do NOT report outcome ROADMAP_EXHAUSTED. That outcome is a",
                "  statement about the PRODUCT roadmap's runway, which this pass",
                "  does not read; the daemon reads it back to throttle ordinary",
                "  work carving. MILESTONE_COMPLETE is the correct 'nothing left",
                "  to do here' answer for a test-health pass.",
                "- headroom_estimate/headroom_rationale: report how many FURTHER",
                "  test-health areas you left un-carved this pass, and how you",
                "  judged that. (The daemon does not raise a headroom-low alert",
                "  from a test-health carve -- these numbers are for the record.)",
                "",
            ])
        elif kind == "gap-audit":
            lines.extend([
                f"You are performing a periodic project-WIDE GAP-AUDIT review "
                f"for project '{project}'. This is NOT a per-task job and NOT a "
                "queue refill: evaluate whether features the project CLAIMS are "
                "shipped are actually backed by code reality, then carve handoff "
                "package(s) for the gaps. Do NOT implement the fixes yourself -- "
                "you carve packages for other agents to pick up later.",
                "",
                "## Carve source: the product-definition",
                "Read the project's PRODUCT-DEFINITION document (below). AUDIT ONLY "
                "features marked `status: shipped` -- planned/building features are "
                "legitimately incomplete and auditing them manufactures busywork.",
                "",
                "For each shipped feature, examine each of its `acceptance` criteria "
                "and verify it against the ACTUAL CODE. Explicitly: you do not have "
                "a component→file map -- discovery is your job. Verify each criterion "
                "as one of:",
                "  - HOLDS: The code actually implements and tests this criterion.",
                "  - PARTIAL: The code partially addresses it; more work needed.",
                "  - ABSENT: The criterion is not found in the code.",
                "",
                "For EACH partial/absent criterion, carve a new package. Name the "
                "feature id and restate the exact criterion text so a future "
                "implementer knows exactly what's missing.",
                "",
                "## Carve NOTHING if every shipped feature's criteria hold",
                "You are explicitly authorized to return an EMPTY `carved` list. A "
                "periodic trigger that always produces packages manufactures "
                "busywork and devalues every real finding. If every checked criterion "
                "holds across all shipped features, report `\"carved\": []` with outcome "
                "MILESTONE_COMPLETE and say so in review_reflection.",
                "",
                "## Product definition",
                f"Read the trove and extract features: {cfg.product_definition}",
                "",
                "## Reporting notes specific to this pass",
                '- Use `"source_kind": "gap"` for anything you carve here.',
                "- Do NOT report outcome ROADMAP_EXHAUSTED. That outcome is a",
                "  statement about the PRODUCT roadmap's runway, which this pass",
                "  does not read; the daemon reads it back to throttle ordinary",
                "  work carving. MILESTONE_COMPLETE is the correct 'no gaps found'",
                "  answer for a gap-audit pass.",
                "- headroom_estimate/headroom_rationale: report how many ADDITIONAL",
                "  gaps you discovered but left un-carved (scope/risk/priority), and",
                "  how you judged that. (The daemon does not raise a headroom-low",
                "  alert from a gap-audit carve -- these numbers are for the record.)",
                "",
            ])
            # F007 2026-07-27 (GAP2, plan-gap-engine-and-reviewer-repair.md
            # §GAP2): the verdict-audit extension -- OFF by default
            # (verdict_audit_sample_size == 0), a complete no-op so the
            # gap-audit packet stays byte-identical to pre-GAP2 output.
            # Appended to the SAME 'gap-audit' branch rather than a sibling
            # `kind` (see _verdict_audit_section_lines' own docstring for
            # the reasoning) because it reuses the identical cadence trigger,
            # carve authority, and REQUIRED OUTPUT CONTRACT envelope -- the
            # two prompts share the dispatch mechanism, only the SOURCE
            # section differs, exactly the axis _carve_packet_body_lines
            # already branches on for every other mode.
            sample_size = cfg.policy.verdict_audit_sample_size
            if sample_size > 0:
                lines.extend(self._verdict_audit_section_lines(cfg, states, sample_size))
        elif rescope is not None:
            origin_id = rescope.get("origin_task_id")
            input_rev = rescope.get("input_revision") or "(unknown)"
            head_rev = rescope.get("head_revision") or "(unknown)"
            drift_line = (
                f"DRIFTED -- input_revision {input_rev} is not an ancestor of main "
                f"{(head_rev[:7] if isinstance(head_rev, str) else head_rev)}; the "
                "package was written against a base main has since moved past"
                if rescope.get("drifted")
                else f"current -- input_revision {input_rev} still matches main "
                     f"{(head_rev[:7] if isinstance(head_rev, str) else head_rev)}"
            )
            # D-R3 (2026-07-26, refined; routing-model-redesign.md D-R3): an
            # `incapable`-classified rejection (the model wasn't capable of a
            # correctly-scoped task -- a structural/repeated capability gap, NOT
            # a design defect) gets a DIFFERENT intro paragraph: tier bump, scope
            # held constant, explicitly NOT a re-scope. Only fires when BOTH the
            # class is `incapable` AND an origin tier was actually recovered
            # (Work 3's graceful degradation) -- every other case (architectural,
            # stale-premise, reject_class absent/unrecognised, or origin_tier
            # unavailable) falls through to the EXISTING generic re-scope text,
            # UNCHANGED byte-for-byte (see
            # test_carve_packet_rescope_architectural_intro_byte_identical_to_pre_incapable).
            # Purely additive: a new branch, never a rewrite of the old one.
            reject_class = rescope.get("reject_class")
            origin_tier = rescope.get("origin_tier")
            if reject_class == "incapable" and origin_tier is not None:
                intro = (
                    f"You are RE-SCOPING one rejected task for project '{project}'. Task "
                    f"'{origin_id}' was implemented, REVIEWED, and REJECTED -- the "
                    f"reviewer judged the SCOPE fine but the assigned tier "
                    f"'{origin_tier}' INCAPABLE of it (a model-capability failure, "
                    "NOT a design/scope defect -- do not confuse this with an "
                    "architectural rescope). Write a fresh, corrected handoff for the "
                    f"SAME work, targeted at an implementer tier HIGHER than "
                    f"'{origin_tier}'. Do NOT redesign the scope -- only re-target it "
                    "at a stronger tier. Record this task's id, "
                    f"'{origin_id}', as the new handoff's `source.ref` (parent task "
                    "id) so the follow-up review is seeded with why a stronger tier "
                    "ran. Do NOT simply re-emit the original handoff unchanged, and "
                    "do NOT implement the work yourself."
                )
            else:
                intro = (
                    f"You are RE-SCOPING one rejected task for project '{project}'. Task "
                    f"'{origin_id}' was implemented, REVIEWED, and REJECTED, and triage "
                    "classified it as architectural or stale-premise -- a same-base retry by "
                    "the implementer cannot fix it. YOU decide what happens: write a fresh, "
                    "corrected handoff package (re-carve), drop the work if the review shows "
                    "it is no longer worth doing, or escalate a genuine product question as a "
                    "D-NNN decision. Do NOT simply re-emit the original handoff unchanged, and "
                    "do NOT implement the work yourself."
                )
            lines.extend([
                intro,
                "",
                "## Re-scope source: the rejected task",
                f"- Original handoff: {rescope.get('handoff_path') or '(none recorded)'}",
                f"- Premise drift: {drift_line}",
                "- Reviewer's rejection verdict (why it was rejected):",
            ])
            verdict = rescope.get("verdict")
            if verdict:
                lines.append("")
                lines.extend(f"  > {ln}" for ln in verdict.splitlines())
            else:
                lines.append("  (no committed review report found for the origin task)")
            lines.append("")
        elif item_id is not None:
            lines.extend([
                f"You are carving ONE new handoff package for project '{project}', "
                f"directly from backlog item {item_id}'s intake brief below -- it was "
                "already elicited via the intake-chat interview, so do not re-derive "
                "it from scratch. Write a single lint-clean handoff file under this "
                "project's handoff directory covering exactly that item. Do NOT "
                "implement the work yourself -- you carve a package for another agent "
                "to pick up later.",
                "",
                "## Carve source: targeted intake brief",
            ])
            lines.extend(self._carve_source_note_lines(cfg, item_id=item_id))
            lines.append("")
        else:
            lines.extend([
                f"You are proposing NEW handoff packages for project '{project}'.",
                "Read the carve sources below, then write new lint-clean handoff",
                "file(s) under this project's handoff directory. Do NOT implement",
                "the work yourself -- you carve packages for other agents to pick",
                "up later.",
                "",
                "## Carve sources (v2 SS8)",
                "1. Review-derived follow-ups (recent REVIEW_RECORDED events):",
            ])
            follow_ups = self._recent_review_follow_ups(project)
            if follow_ups:
                for task_id, result in follow_ups:
                    lines.append(f"   - {task_id}: {result}")
            else:
                lines.append("   - none recorded yet")
            lines.append("2. Backlog / 3. Roadmap and gap analysis:")
            lines.extend(f"   {line}" for line in self._carve_source_note_lines(cfg))
            lines.append(
                "4. Standing product goal: read this project's own README/"
                "CLAUDE.md and its nyxloom-trove/nyxloom.toml (or legacy "
                ".nyxloom/project.toml) for its product intent."
            )
            lines.append("")
        lines.append("## Current queue")
        queue_lines = [f"- {task_id}: {states[task_id].state.value}"
                       for task_id in sorted(states.keys()) if task_id != own_task_id]
        lines.extend(queue_lines if queue_lines else ["- (queue empty)"])
        lines.append("")
        authority = getattr(cfg.policy, "carve_authority", "branch")
        lines.append(f"## Carve authority: {authority}")
        if authority == "branch":
            lines.append(
                "You are on a dedicated carve branch. Commit your new handoff "
                "file(s) here. Do NOT merge -- a human admits this carve by "
                "merging the branch."
            )
        elif authority == "main":
            lines.append(
                "You are working directly on the project's checked-out branch. "
                "Commit your new handoff file(s) directly (lint-gated)."
            )
        else:
            lines.append(
                "Write your new handoff file(s) to disk WITHOUT committing "
                "(no git). They will be picked up on the next reconcile pass."
            )
        lines.append("")
        # B6/P74: the carver is the component that MAINTAINS the spine digest, and
        # its packet references it BY POINTER (never slurps the body) when the
        # carve stage's context declares "spine-digest". This is where carve-6-style
        # reflections ("hollow tests dominate") stop being one-off prose and become
        # standing, versioned reviewer/carver instructions.
        if "spine-digest" in stages.stage_context("carve"):
            spine_rel = f"{cfg.reports_dir}/SPINE-DIGEST.md"
            lines.extend([
                f"## Standing spine digest -- {spine_rel}",
                f"Read `{spine_rel}` in this repo for the standing catalog of",
                "invariants, recent review reflections, and open risks (referenced",
                "by pointer, not inlined here). As part of THIS carve, MAINTAIN it:",
                "create it if absent, and fold in what the recent reviews/merges",
                "revealed -- keep it terse and current, not append-only sprawl.",
                "",
            ])
        return lines

    def _build_carve_packet(self, cfg: ProjectConfig, project: str, seq: int,
                             states: dict[str, TaskStateFile],
                             own_task_id: str | None = None,
                             item_id: str | None = None,
                             rescope: dict | None = None,
                             kind: str = "headroom") -> str:
        """The carve packet (mirrors the review packet's economy: point at
        sources, embed only what is cheap and structured). Written to the
        carve attempt's own packet/packet.md, exactly like LaunchReview's
        packet.

        P41 2026-07-16: when `item_id` is set (dispatch_targeted_carve),
        this is a TARGETED carve -- the packet's only carve source is that
        one backlog item's intake brief (its pre-carve detail: aligned
        purpose, elicited detail, linked D-NNN, priority), distinct from the
        untargeted headroom-refill carve's review/backlog/roadmap/product-
        goal source list.

        B7 2026-07-20 (P75): when `rescope` is set (built by _rescope_context
        for a task triage routed to READY_TO_CARVE), this is a RE-SCOPE carve
        -- the primary source is the rejected task itself: its original handoff
        (pointer), the reviewer's rejection verdict (embedded prose), and the
        input_revision drift report. The carve authority + output-contract tail
        is shared with the other two modes; only the source section differs.
        The three modes are mutually exclusive (item 12 sets task_id not item_id;
        dispatch_targeted_carve sets item_id not task_id; item 9 sets neither),
        so rescope takes precedence, then item_id, then the untargeted default.

        D-065 (B63 2026-07-20): when `kind` == 'test-health' (reconcile module
        contract item 15's seldom-run project-WIDE trigger), the carve source is
        the TEST SUITE rather than any work source -- the carver evaluates
        standing suite debt and carves test-IMPROVEMENT packages. It is checked
        FIRST because a test-health carve is untargeted (no item_id, no rescope),
        so it would otherwise fall through to the work-source default. The
        packet explicitly authorizes carving NOTHING when the suite is healthy:
        a periodic trigger that must always produce output would manufacture
        busywork, which is worse than not running at all.

        F018 P3c: the SOURCE-CONTEXT body (everything above the REQUIRED
        OUTPUT CONTRACT tail) now lives in _carve_packet_body_lines, reused
        verbatim by _build_carver_carve_prompt for a project-persistent
        session's carve-mode resume turn -- pure extraction, this method's
        OWN output is unchanged (same lines, same order, same tail)."""
        lines = self._carve_packet_body_lines(cfg, project, seq, states,
                                               own_task_id=own_task_id, item_id=item_id,
                                               rescope=rescope, kind=kind)
        report_rel = f"{cfg.reports_dir}/CARVE-{seq}.md"
        lines.extend([
            "## REQUIRED OUTPUT CONTRACT",
            f"Write `{report_rel}` containing EXACTLY one JSON object (no",
            "markdown code fences) with these fields:",
            '  "carved": [{"id": "<new-task-id>", "why": "<one line>", '
            '"source_kind": "review|backlog|roadmap|product-goal"}, ...]',
            '  "review_reflection": "<what the recent reviews/merges revealed '
            'about quality/gaps>"',
            '  "headroom_estimate": <int -- how many more carve-able packages '
            "exist before ROADMAP_EXHAUSTED/SPEC_GAP>",
            '  "headroom_rationale": "<one paragraph: how you read the '
            'roadmap/backlog runway>"',
            '  "outcome": one of ' + ", ".join(sorted(_CARVE_OUTCOMES)),
            "Also print this exact JSON as your final output line.",
        ])
        # F007 2026-07-27 (GAP2): the verdict_audit contract field, ONLY when
        # this pass's packet actually carries a "## Verdict audit" section
        # (kind == 'gap-audit' and the sample-size knob is on) -- appended
        # AFTER the shared tail above so every other kind, and a disabled
        # verdict-audit, gets the EXACT pre-GAP2 contract text unchanged
        # (oracle 6: byte-identical when off).
        if kind == "gap-audit" and cfg.policy.verdict_audit_sample_size > 0:
            lines.append(
                '  "verdict_audit": [{"task_id": "<sampled task id>", "judgment": '
                '"APPROVED|REJECTED", "rationale": "<one paragraph, your OWN blind '
                'judgment>"}, ...] -- one entry for EVERY task listed under '
                '"Verdict audit" above.'
            )
        return "\n".join(lines) + "\n"

    def _build_carver_carve_prompt(self, cfg: ProjectConfig, project: str, task_id: str,
                                    seq: int, states: dict[str, TaskStateFile],
                                    generation: int, sub_mode: str,
                                    item_id: str | None = None,
                                    rescope: dict | None = None,
                                    kind: str = "headroom",
                                    base_revision: str | None = None) -> str:
        """F018 P3c (plan-long-running-carver.md §4.1, §4.2 alias): the
        WRITE-AUTHORITY carve-mode resume prompt for a project-persistent
        carver session -- fed directly to adapters.build_resume (a flat
        prompt string, unlike build_dispatch's packet-file indirection; see
        adapters.self_review_prompt for the precedent of a long structured
        resume prompt).

        Reuses the EXACT SAME mode-specific source context
        _build_carve_packet's legacy dispatch path builds
        (_carve_packet_body_lines: headroom / re-scope (`rescope`) /
        targeted-intake (`item_id`) / test-health (`kind`)), then replaces
        the legacy CARVE-<seq>.md contract with the plan §4.1
        CarverTurnResult envelope contract. `proposal_id`/`turn_id` are
        DICTATED here, never left for the carver to invent -- P3b's
        _validate_carve_proposal_payload rejects any proposal_id whose
        generation/turn segment does not match, so a self-chosen id would
        just be silently discarded; handing the carver the exact literal
        string it must echo back is the simplest thing that reliably
        satisfies that check. The envelope is written ALONGSIDE any newly
        authored handoff file(s) (or an empty `artifacts`/`carved: []`-style
        turn, mirroring the legacy contract's own 'carve nothing is a valid
        outcome' allowance), never INSTEAD of them."""
        lines = self._carve_packet_body_lines(cfg, project, seq, states, own_task_id=task_id,
                                               item_id=item_id, rescope=rescope, kind=kind)
        proposal_id = f"{cfg.project_id}:carve:{generation}:{task_id}"
        envelope_rel = f"{cfg.reports_dir}/CARVER-PROPOSAL-{seq}.json"
        lines.extend([
            "## REQUIRED OUTPUT CONTRACT (persistent-session carve turn)",
            "This is a WRITE-AUTHORITY turn of your standing strategic-carver "
            "session. Author the handoff file(s) as instructed above (or none, "
            "if nothing is carve-worthy this turn), then ALSO write "
            f"`{envelope_rel}` containing EXACTLY one JSON object (no markdown "
            "code fences) with these fields:",
            '  "kind": "carve-proposal"',
            '  "schema_version": 1',
            f'  "proposal_id": "{proposal_id}" -- copy this EXACT string, do '
            'not invent your own',
            f'  "turn_id": "{task_id}" -- copy this EXACT string',
            '  "source": {"mode": "' + sub_mode + '", "refs": [<backlog/roadmap/'
            'review refs you drew from>], "base_revision": '
            f'"{base_revision or ""}", "merge_digest_cursor": 0}}',
            '  "artifacts": [{"kind": "handoff", "path": "<repo-relative path '
            'you just authored>", "sha256": "<its sha256>", "source_ref": '
            '"<ref this artifact answers, or null>"}, ...] -- empty list if '
            "you carved nothing this turn",
            '  "dispositions": [{"source_ref": "<ref>", "result": '
            '"handoff|decision|backlog|redundant|drop", "artifact_ref": '
            '"<path or null>", "reason_code": "<short enum>"}, ...]',
            '  "outcome": one of ' + ", ".join(sorted(_CARVE_OUTCOMES)),
            '  "headroom_estimate": <int -- how many more carve-able packages '
            "exist before ROADMAP_EXHAUSTED/SPEC_GAP>",
            "Also print this exact JSON as your final output line.",
        ])
        if rescope is not None:
            lines.append(
                "If you re-carve the rejected task above, its disposition "
                f'MUST read "source_ref": "{rescope.get("origin_task_id")}", '
                '"result": "handoff", "artifact_ref": "<the new handoff\'s '
                'path>" -- that is what tells the daemon to retire the '
                "rejected task in favor of your replacement."
            )
        return "\n".join(lines) + "\n"

    def _normalize_repo_relpath(self, value: Any) -> str | None:
        """Textual-only normalization for comparing two repo-relative path
        strings for equivalence (AD2 fix): collapses a leading './',
        internal './' segments, and duplicate slashes via PurePosixPath.
        Never resolves '..' or touches the filesystem -- this is a pure
        surface-form comparison helper for the re-scope disposition match,
        NOT a security boundary (_resolve_carve_proposal_artifact already
        owns traversal rejection for the artifacts that matter)."""
        if not isinstance(value, str) or not value:
            return None
        return PurePosixPath(value).as_posix()

    def _carve_proposal_recorded_payload(self, project: str, proposal_id: str,
                                          *, events: Sequence[Event] | None = None
                                          ) -> dict | None:
        """The raw CARVER_PROPOSAL_RECORDED payload for `proposal_id`, or
        None if never recorded. Used by _execute_admit_carve_proposal to read
        `dispositions` (not carried by the bounded ValidatedCarveProposal
        snapshot) for the re-scope supersession step (§4.2 item 4).

        CR-02a: an unreadable log used to be indistinguishable from "never
        recorded", which silently skipped supersession."""
        for ev in self._require_events(project, events):
            if (ev.type is EventType.CARVER_PROPOSAL_RECORDED
                    and (ev.payload or {}).get("proposal_id") == proposal_id):
                return ev.payload
        return None

    def _execute_carve_via_session_resume(self, project: str, cfg: ProjectConfig,
                                          states: dict[str, TaskStateFile],
                                          action: "reconcile.CarveDispatch",
                                          snap: carver_session.CarverSessionSnapshot
                                          ) -> list[Event]:
        """F018 P3c (plan §4.2 alias, §2.1): the normalize branch's BODY --
        launches a write-authority `carve`-mode SESSION RESUME turn in place
        of _execute_carve_dispatch's own fresh-throwaway-carve tail. Mirrors
        _execute_resume_carver_session's launch shape (pinned route from the
        durable snapshot, fresh attempt/turn id every call, strategic-carver
        lease) but marks the turn kind=resume mode=carve -- a WRITE-
        AUTHORITY mode (see _CARVE_WRITE_AUTHORITY_MODES), distinct from
        merge-feed/targeted-intake/recover -- and builds the SAME mode-
        specific carve source context _execute_carve_dispatch's legacy path
        builds (headroom / re-scope via action.task_id / test-health via
        action.kind / targeted intake via action.item_id), instructing the
        carver to author a CarverTurnResult envelope (§4.1) instead of
        merely acknowledging.

        The caller (_execute_carve_dispatch) already ran the SAME admission
        recheck (_dispatch_admissible) this method would otherwise repeat;
        the route-resolution/no-route handling below mirrors _execute_
        resume_carver_session's own (the session's PINNED route, never
        re-selected via for_role).

        AD1 fix (2026-07-25 adversarial review): UNLIKE the legacy launch-
        time B7 supersede, a normalized re-scope does NOT supersede the
        origin here. Plan §4.2 explicitly tightens B7 for the proposal
        protocol: "supersession waits until the replacement is validated/
        admitted... not merely after launching a carve." The origin's
        context still seeds this turn's PROMPT (rescope_ctx below) so the
        carver knows what it is re-scoping, but the actual TASK_SUPERSEDED
        is emitted later, by _execute_admit_carve_proposal, driven by the
        RECORDED proposal's own `dispositions` (result=="handoff",
        source_ref=origin) -- see that method's own step-4 docstring. Firing
        it at launch, as the legacy path does, would mean a normalized
        re-scope that turns out empty/invalid still loses the origin with
        nothing to replace it -- exactly the data-loss bug §4.2 exists to
        close."""
        events: list[Event] = []

        route_id = snap.route.get("route_id") if isinstance(snap.route, dict) else None
        routes_obj = config.Routes.load()
        route_def = routes_obj.routes.get(route_id) if route_id else None
        if route_def is None:
            if not self._needs_operator_recently_emitted(
                    project, "carver-no-route"):
                events.append(self._append_ev(
                    project, cfg, states, EventType.NEEDS_OPERATOR,
                    {"reason": "carver-no-route"}, task_id=None))
            return events
        # The session's PINNED route, so this is the first point at which
        # admission's containment half (CR-13a) can be asked.
        ok, _reason = effects_dispatch.admissible(ctx, "carve", route_def)
        if not ok:
            return events  # refused at the effect boundary; nothing launched

        seq = self._carver._next_carve_seq(project)
        task_id = f"carver-session-{project}-{seq}"

        # Same mutually-exclusive precedence _execute_carve_dispatch's
        # legacy path already documents (rescope, then item_id, then
        # test-health, then the untargeted default) -- sub_mode is the
        # plan §4.1 source.mode vocabulary the carve prompt/envelope uses.
        is_rescope = action.task_id is not None and action.item_id is None
        origin_task_id = action.task_id if is_rescope else None
        kind = getattr(action, "kind", "headroom")
        if is_rescope:
            sub_mode = "rescope"
        elif action.item_id is not None:
            sub_mode = "targeted-intake"
        elif kind == "test-health":
            sub_mode = "test-health"
        else:
            sub_mode = "headroom"

        authority = getattr(cfg.policy, "carve_authority", "branch")
        if authority == "branch":
            branch = f"carver-session/{project}-{seq}"
            carve_cwd = cfg.root / cfg.worktree_root / branch
            effects_dispatch.ensure_worktree(self._ports, cfg.root, branch,
                                             carve_cwd, cfg.default_branch)
            dispatch_branch = branch
        else:
            carve_cwd = cfg.root
            dispatch_branch = cfg.default_branch

        source_ids: tuple[str, ...]
        if origin_task_id is not None:
            source_ids = (origin_task_id,)
        elif action.item_id is not None:
            source_ids = (action.item_id,)
        else:
            source_ids = ()
        sources_token = ",".join(source_ids) if source_ids else "(none)"
        notes = (f"carver-session seq={seq} kind=resume mode=carve "
                f"generation={snap.generation} sources={sources_token}")
        if origin_task_id is not None:
            notes += f" rescope-of={origin_task_id}"
        if action.item_id is not None:
            notes += f" item={action.item_id}"
        tsf = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
            state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, notes=notes,
        )
        events.append(self._append_ev(project, cfg, states, EventType.TASK_CREATED,
                                       {"statefile": tsf.to_dict()}, task_id=task_id))

        attempt_id = new_id("att")
        attempt_dir = paths.attempt_dir(project, attempt_id)
        route_snap = Route(route_id=route_def.route_id, cli=route_def.cli, model=route_def.model,
                           variant=route_def.variant, effort=route_def.effort,
                           routes_rev=routes_obj.revision)
        attempt = Attempt(attempt_id=attempt_id, role=Role.CARVER, state=AttemptState.CREATED,
                          route=route_snap, started=utc_now(), worktree=str(carve_cwd),
                          branch=dispatch_branch if authority == "branch" else None)
        events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_CREATED,
                                       {"attempt": attempt.to_dict()}, task_id=task_id,
                                       attempt_id=attempt_id))

        rescope_ctx = (self._rescope_context(cfg, states, origin_task_id)
                       if origin_task_id is not None else None)
        prompt = self._build_carver_carve_prompt(
            cfg, project, task_id, seq, states, generation=snap.generation, sub_mode=sub_mode,
            item_id=action.item_id, rescope=rescope_ctx, kind=kind,
            # Prompt context only (see the gap-audit marker above): None
            # simply omits the base-revision line from the carver packet.
            base_revision=self._head_revision(cfg).value)
        argv = adapters.build_resume(route_def, session=snap.session_id,
                                     worktree=str(carve_cwd), prompt=prompt)
        spec = wrapper.WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
            cwd=str(carve_cwd), log_path=str(attempt_dir / "attempt.log"),
            receipt_path=str(attempt_dir / "receipt.json"), attempt_dir=str(attempt_dir),
            route_def=asdict(route_def), repo_root=str(cfg.root),
            leases=[{"name": f"{project}.strategic-carver", "capacity": 1}],
        )
        pid = self._ports.processes.launch_detached(spec)
        attempt.state = AttemptState.PREFLIGHTING
        attempt.pid = pid
        events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_PREFLIGHTED,
                                       {"attempt": attempt.to_dict()}, task_id=task_id,
                                       attempt_id=attempt_id))

        # AD1 fix: NO launch-time supersede here (unlike the legacy path
        # above) -- see this method's own docstring. The origin stays
        # READY_TO_CARVE until _execute_admit_carve_proposal admits a
        # replacement that names it in a "handoff" disposition.
        return events

    def carve_dispatch(self, ctx: effects.EffectContext,
                       action: reconcile.CarveDispatch) -> list[Event]:
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        events: list[Event] = []

        # P55 2026-07-19 (R5): execute-time admission at the effect boundary --
        # pause/budget re-checked immediately before any side effect (the
        # route re-check just below is the SAME idea, already present for
        # routes only). This is the single carve chokepoint, so it covers
        # BOTH the untargeted headroom trigger (via _execute's CarveDispatch
        # branch) AND the operator-initiated dispatch_targeted_carve path
        # (M15, which previously bypassed pause/budget entirely). An explicit
        # operator-override of pause for a targeted carve is a product
        # decision (D-062), added as an audited flag later -- not the current
        # silent bypass.
        ok, _reason = effects_dispatch.admissible(ctx, "carve")
        if not ok:
            return events  # refused at the effect boundary; no synthetic task minted

        # F018 P3c (plan-long-running-carver.md §4.2 alias, §2.1): NORMALIZE
        # -- when this project's persistent carver session is WARM, run THIS
        # carve as a write-authority `carve`-mode SESSION RESUME instead of
        # minting a fresh throwaway carve. Normalization happens HERE, at the
        # EXECUTOR, never inside reconcile.py's pure planner -- module
        # contract items 9/12/15 keep emitting the SAME CarveDispatch,
        # unchanged, regardless of cfg.carve.session (that planner ladder
        # does not itself gate on session status; it only ever cares that
        # this pass's single carve_dispatch_planned slot is free). The
        # EXECUTOR is the sole place this package changes: `_carver_session`
        # is its own MASTER GATE (returns None unless cfg.carve.session ==
        # "project-persistent"), and this branch additionally requires
        # status WARM with a captured session_id -- a session that is COLD/
        # STARTING/DEGRADED/ROTATING/COMPACTING (or absent/off) falls
        # straight through to every line below, unchanged, running the
        # EXACT pre-P3c fresh-carve path.
        snap = self._carver._carver_session(project, cfg)
        if snap is not None and snap.status is CarverStatus.WARM and snap.session_id:
            return self._execute_carve_via_session_resume(project, cfg, states, action, snap)

        # F018 P3d (concern-3 #2 + checklist #6): close the not-WARM legacy
        # fall-through. When the persistent session is DEGRADED with recovery
        # exhausted (the only DEGRADED state that reaches here -- the planner
        # consumed the mutex for every other feature-on state), do NOT run the
        # legacy fresh-carve-and-supersede path. Instead, ROTATE the generation
        # so the planner cold-bootstraps a fresh session next pass.
        if snap is not None:
            if snap.status is CarverStatus.DEGRADED:
                events.append(self._append_ev(
                    project, cfg, states, EventType.CARVER_SESSION_ROTATED,
                    {"new_generation": snap.generation + 1,
                     "reason": "degraded-recovery-exhausted",
                     "from_generation": snap.generation},
                    task_id=None))
                return events
            # Defensive: any other non-WARM feature-on status (STARTING/
            # COMPACTING/ROTATING/COLD) that somehow reaches here should
            # never fall through to legacy carve.
            events.append(self._append_ev(
                project, cfg, states, EventType.NEEDS_OPERATOR,
                {"reason": "carve-dispatch-unexpected-status",
                 "status": snap.status.value},
                task_id=None))
            return events

        # Defense in depth: reconcile.py's own trigger already requires a
        # healthy 'frontier-review' route before ever emitting CarveDispatch
        # (module contract item 9), but routes.toml could change between
        # planning and execution within the same pass. Never mint a
        # synthetic task / worktree we cannot actually dispatch into.
        routes_obj = config.Routes.load()
        review_routes = routes_obj.for_role(Role.REVIEW_INDEPENDENT.value)
        if not review_routes:
            events.append(self._append_ev(
                project, cfg, states, EventType.NEEDS_OPERATOR,
                {"reason": "carve-no-route"}, task_id=None))
            return events

        seq = self._carver._next_carve_seq(project)
        task_id = f"carve-{project}-{seq}"
        authority = getattr(cfg.policy, "carve_authority", "branch")

        # B7 2026-07-20 (P75): a RE-SCOPE carve carries the ORIGINAL rejected
        # task's id in action.task_id (set by reconcile item 12 for a task triage
        # routed to READY_TO_CARVE). It is distinct from the untargeted headroom
        # trigger (task_id None) and the targeted backlog carve (item_id set,
        # task_id None). Only a re-scope embeds the rejected task's context in the
        # packet AND atomically supersedes the origin -- but ONLY after the carve
        # actually launches (below), so a carve that early-returns (admission
        # refused / no route above) leaves the origin in READY_TO_CARVE to retry,
        # never silently dropping the re-carve (critique M20; the A10 atomicity
        # B7's oracle requires -- the old item-12 code planned the supersede as an
        # independent action that fired even when the carve did not).
        is_rescope = action.task_id is not None and action.item_id is None
        origin_task_id = action.task_id if is_rescope else None

        if authority == "branch":
            branch = f"carve/{project}-{seq}"
            carve_cwd = cfg.root / cfg.worktree_root / branch
            effects_dispatch.ensure_worktree(self._ports, cfg.root, branch,
                                             carve_cwd, cfg.default_branch)
            dispatch_branch = branch
        else:
            carve_cwd = cfg.root
            dispatch_branch = cfg.default_branch

        # Synthetic carve task: hosts the CARVER attempt so wrapper.py's
        # frozen load_state()/attempt_by_id() contract is satisfied (a
        # carve has no pre-existing task to attach to). See module
        # docstring for why ACTIVE (counts toward wip-cap like a review
        # attempt already does) and why SUPERSEDED is the eventual terminal
        # edge.
        # D-065 (B63): 'headroom' (the default) covers every carve that reads the
        # project's WORK sources -- item 9's refill, item 12's re-scope, the
        # targeted backlog carve. 'test-health' is item 15's project-wide suite-
        # debt pass. A test-health carve is untargeted (no item_id, no origin), so
        # kind is the ONLY thing distinguishing it from item 9's carve here.
        kind = getattr(action, "kind", "headroom")
        notes = f"carve seq={seq} authority={authority}"
        if kind != "headroom":
            notes += f" kind={kind}"
        if action.item_id is not None:
            notes += f" item={action.item_id}"
        if origin_task_id is not None:
            notes += f" rescope-of={origin_task_id}"
        tsf = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
            state=TaskState.ACTIVE, since=utc_now(), handoff_path=None,
            notes=notes,
        )
        # `carve_kind` is the STRUCTURED marker _days_since_test_health_carve
        # scans for (notes above is the human-legible twin -- never the machine
        # source). Payload-additive: storage's TASK_CREATED replay reads only
        # payload["statefile"], so an extra key cannot cause replay divergence.
        created_payload: dict[str, Any] = {"statefile": tsf.to_dict()}
        if kind != "headroom":
            created_payload["carve_kind"] = kind
        # F007: gap-audit carves also stamp head_sha so the next gap-audit trigger
        # can compute changed lines since this carve.
        if kind == "gap-audit":
            # CR-02a: this is the PROMPT/marker side, not the planner's
            # drift guard. An unresolvable HEAD here means the marker
            # carries no head_sha, which _changed_lines_since_gap_audit
            # already treats as an advisory "cannot measure" degradation --
            # it can never authorize anything, so .value (None on failure)
            # is the correct read.
            head_sha = self._head_revision(cfg).value
            if head_sha:
                created_payload["head_sha"] = head_sha
        events.append(self._append_ev(project, cfg, states, EventType.TASK_CREATED,
                                       created_payload, task_id=task_id))

        attempt_id = new_id("att")
        attempt_dir = paths.attempt_dir(project, attempt_id)
        packet_dir = attempt_dir / "packet"
        packet_dir.mkdir(parents=True, exist_ok=True)
        rescope_ctx = (self._rescope_context(cfg, states, origin_task_id)
                       if origin_task_id is not None else None)
        packet_text = self._build_carve_packet(cfg, project, seq, states, own_task_id=task_id,
                                                item_id=action.item_id, rescope=rescope_ctx,
                                                kind=kind)
        (packet_dir / "packet.md").write_text(packet_text, encoding="utf-8")

        route_def = review_routes[0]
        # Re-asked with the resolved route (CR-13a): admission's containment
        # half is a property of the route, and a carve selects its own.
        ok, _reason = effects_dispatch.admissible(ctx, "carve", route_def)
        if not ok:
            return events  # refused at the effect boundary; nothing launched
        route_snap = Route(route_id=route_def.route_id, cli=route_def.cli, model=route_def.model,
                            variant=route_def.variant, effort=route_def.effort,
                            routes_rev=routes_obj.revision)
        attempt = Attempt(attempt_id=attempt_id, role=Role.CARVER, state=AttemptState.CREATED,
                           route=route_snap, started=utc_now(), worktree=str(carve_cwd),
                           branch=dispatch_branch if authority == "branch" else None)
        events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_CREATED,
                                       {"attempt": attempt.to_dict()}, task_id=task_id,
                                       attempt_id=attempt_id))

        gate_hint = effects_dispatch.gate_hint(cfg)
        receipt_path = str(attempt_dir / "receipt.json")
        argv, _prompt = adapters.build_dispatch(
            route_def, handoff_path=str(packet_dir / "packet.md"), worktree=str(carve_cwd),
            branch=dispatch_branch, task_id=task_id, gate_hint=gate_hint, receipt_path=receipt_path,
            role=Role.CARVER, carve_authority=authority,
        )
        spec = wrapper.WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
            cwd=str(carve_cwd), log_path=str(attempt_dir / "attempt.log"),
            receipt_path=receipt_path, attempt_dir=str(attempt_dir), route_def=asdict(route_def),
            repo_root=str(cfg.root),
            leases=[{"name": f"{project}.strategic-carver", "capacity": 1}],
        )
        pid = self._ports.processes.launch_detached(spec)
        attempt.state = AttemptState.PREFLIGHTING
        attempt.pid = pid
        events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_PREFLIGHTED,
                                       {"attempt": attempt.to_dict()}, task_id=task_id,
                                       attempt_id=attempt_id))

        # B7 2026-07-20 (P75; critique CRITIQUE.md:249, A10 atomicity): supersede
        # the ORIGINAL rejected task NOW -- and ONLY now, after launch_detached +
        # ATTEMPT_PREFLIGHTED have committed the re-scope carve. Every early return
        # above (admission refused, no frontier route) skips this, so a carve that
        # never launched leaves the origin in READY_TO_CARVE for the next pass to
        # retry -- closing M20 (the pre-B7 item-12 code planned CarveDispatch and
        # Transition(SUPERSEDED) as two independent actions, so the supersede fired
        # even when the carve early-returned, silently dropping the re-carve). The
        # RESCOPED outcome distinguishes this daemon-driven re-scope supersede from
        # a plain carve-consumed one; the origin lands in the existing SUPERSEDED
        # state (READY_TO_CARVE -> SUPERSEDED is the stages.py carve `rescope_
        # superseded` edge, now made real). Guarded on membership: item 12 only
        # emits a re-scope CarveDispatch for a task present in states.
        if origin_task_id is not None and origin_task_id in states:
            events.append(self._append_ev(
                project, cfg, states, EventType.TASK_SUPERSEDED,
                {"from": states[origin_task_id].state.value,
                 "outcome": _RESCOPE_OUTCOME,
                 "carve_task_id": task_id,
                 "notes": f"rescoped -- re-scope carve {task_id} launched (seq={seq})"},
                task_id=origin_task_id))
        return events

    def admit_carve_proposal(self, ctx: effects.EffectContext,
                             action: reconcile.AdmitCarveProposal) -> list[Event]:
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        """F018 P3b (plan §4.2 steps 1-5): atomic admission of an already-
        validated carve proposal.

        Step 1 (effect-boundary recheck, 'a proposal whose file changed
        since validation is refused') is a FRESH call into
        _validated_carve_proposals: since that call re-reads and re-hashes
        every artifact from disk every time (no caching between passes),
        it both re-derives the chosen proposal AND performs the recheck in
        one step -- a proposal whose artifact changed (or whose generation
        rotated, or that a repair superseded) simply no longer appears in
        the fresh result, so `chosen is None` covers refusal for free, the
        same 'stale plan -> refuse cleanly, no side effect' idiom
        _execute_start_carver_session/_execute_resume_carver_session
        already use for their own execution-time rechecks.

        Steps 2-5 (below) only run once a still-valid match is found, and
        are themselves idempotent -- see each step's own comment."""
        events: list[Event] = []

        snap = self._carver._carver_session(project, cfg)
        if snap is None:
            return events  # feature toggled off since planning -- refuse cleanly

        proposals = self._carver._validated_carve_proposals(project, cfg, snap)
        chosen = next((p for p in proposals if p.proposal_id == action.proposal_id), None)
        if chosen is None:
            return events  # effect-boundary recheck failed -- refuse cleanly

        # (2) admission marker: a durable, replayable "this proposal is
        # admitted" audit record -- CARVER_PROPOSAL_ADMITTED (AD1 fix,
        # 2026-07-25: authorized addition to types.py/event.schema.json/
        # test_invariants.KNOWN_IGNORED_EVENT_TYPES, mirroring the other
        # audit-only CARVER_* members). This is THE exclusion cursor
        # _validated_carve_proposals reads via _proposal_already_admitted
        # -- see that method's own docstring for why a states-membership
        # check was wrong (it raced the ordinary 'new handoffs' scan and
        # could silently skip step 4 below).
        events.append(self._append_ev(
            project, cfg, states, EventType.CARVER_PROPOSAL_ADMITTED,
            {"proposal_id": chosen.proposal_id, "generation": chosen.generation,
             "artifact_ids": list(chosen.artifact_ids)},
            task_id=None))

        # (3) create normal tasks from the already-parsed Frontmatter/paths
        # -- the SAME CARVED-state TaskStateFile/TASK_CREATED shape
        # _execute's own CreateTask branch (plan_project's item-1 'new
        # handoffs' scan) already uses, so a task admitted here is
        # indistinguishable from one the ordinary scan would have created
        # for the same on-disk file. Idempotent: an artifact_id already in
        # `states` (created by a prior admission pass, OR by that SAME
        # ordinary scan independently discovering the identical file --
        # _resolve_carve_proposal_artifact resolves against the exact same
        # frontmatter.discover_handoffs(cfg) source of truth, so the two
        # paths can never disagree on what is a legitimate handoff) is
        # skipped, never duplicated -- 'admitting the same proposal_id
        # twice creates each task ONCE'.
        for artifact_id, relpath in zip(chosen.artifact_ids, chosen.artifact_paths):
            if artifact_id in states:
                continue
            tsf = TaskStateFile(
                schema_version=storage.SCHEMA_VERSION, task_id=artifact_id, project=project,
                state=TaskState.CARVED, since=utc_now(), handoff_path=relpath,
            )
            events.append(self._append_ev(
                project, cfg, states, EventType.TASK_CREATED,
                {"statefile": tsf.to_dict()}, task_id=artifact_id))

        # (4) re-scope supersession -- ONLY now, on successful admission
        # (plan §4.2 tightens B7: no longer 'fires once the re-scope carve
        # merely launches'). The origin task is read from the RECORDED
        # proposal's own `dispositions` (§4.1), never from action itself
        # (AdmitCarveProposal carries no origin field -- reconcile.py is
        # scope.forbid, so its frozen shape cannot grow one). A
        # disposition whose result is 'handoff' names the READY_TO_CARVE
        # task this ADMITTED artifact replaces; any other disposition
        # (decision/backlog/redundant/drop) leaves its origin untouched --
        # exactly the negative oracle ('origin remains READY_TO_CARVE ...
        # until an explicit typed decision/drop disposition'). Cross-
        # checking artifact_ref against chosen.artifact_paths guards
        # against a disposition naming an artifact that was never actually
        # part of THIS validated/admitted proposal -- AD2 fix (2026-07-25):
        # compared via _normalize_repo_relpath (collapses a leading './'/
        # internal './'/duplicate slashes) on BOTH sides, so a carver that
        # emits a slightly differently-normalized but equivalent
        # artifact_ref (e.g. './nyxloom-trove/handoffs/x.md') is not
        # silently treated as a mismatch.
        record_payload = self._carve_proposal_recorded_payload(project, chosen.proposal_id)
        dispositions = (record_payload or {}).get("dispositions") or []
        normalized_artifact_paths = {
            self._normalize_repo_relpath(p) for p in chosen.artifact_paths
        }
        for d in dispositions:
            if not isinstance(d, dict) or d.get("result") != "handoff":
                continue
            origin_id = d.get("source_ref")
            if not origin_id or origin_id not in states:
                continue
            if states[origin_id].state is not TaskState.READY_TO_CARVE:
                continue
            artifact_ref = self._normalize_repo_relpath(d.get("artifact_ref"))
            if artifact_ref is None or artifact_ref not in normalized_artifact_paths:
                continue
            events.append(self._append_ev(
                project, cfg, states, EventType.TASK_SUPERSEDED,
                {"from": states[origin_id].state.value, "outcome": _RESCOPE_OUTCOME,
                 "proposal_id": chosen.proposal_id,
                 "notes": f"rescoped -- carve proposal {chosen.proposal_id} admitted"},
                task_id=origin_id))

        # (5) mark the proposal cursor consumed -- step 2's
        # CARVER_PROPOSAL_ADMITTED event above IS the durable cursor;
        # _proposal_already_admitted (see _validated_carve_proposals'
        # docstring, AD1 fix) means this exact proposal_id is never
        # re-selected by a later pass, regardless of `states` contents.
        return events
    def consume_carve_exit(self, ctx: effects.EffectContext, task_id: str,
                           attempt_id: str) -> list[Event]:
        """role == CARVER branch of EmitAttemptExit (called
        from _execute BEFORE the REVIEW_INDEPENDENT/implementer branches).
        Parses the carver's REQUIRED OUTPUT CONTRACT file, persists the
        full CarveSummary for the dashboard, emits a typed-only
        CARVE_OUTCOME, raises headroom/roadmap-exhausted SPEC_ATTENTION and
        (branch authority only) a NEEDS_OPERATOR, then retires the
        synthetic carve task to SUPERSEDED (clears reconcile.py's carve
        slot).

        P51 2026-07-19 (live incident: two real carves both hit
        carve-parse-failed despite writing a genuinely valid report):
        _execute_carve_dispatch's 'branch' authority worktree is a git
        worktree of the WHOLE physical repo (worktree_root = '../.worktrees'
        is relative to cfg.root, one level ABOVE it), so the carver's own
        cwd there is the repo root, not cfg.root -- it correctly wrote (and
        committed) its report at <worktree>/<cfg.root.name>/<reports_dir>/
        CARVE-N.md, e.g. <worktree>/nyxloom/nyxloom-trove/reports/CARVE-6.md.
        'main'/'files' authority never hits this: _execute_carve_dispatch
        sets carve_cwd = cfg.root directly for both (no separate worktree).
        The distinguishing signal is deliberately "does the recorded
        worktree differ from cfg.root", NOT the current policy's
        carve_authority string -- carve_authority is a live-editable POLICY
        value (config.html can change it any time after this attempt was
        dispatched), while attempt.worktree is what was ACTUALLY used for
        THIS specific attempt; keying off the live policy instead would
        silently break replaying an older attempt after an operator
        changes carve_authority later. Reusing cfg.root.name (not a
        repo-root subprocess call) matches the SAME repo-layout assumption
        cfg.worktree_root's '../.worktrees' already bakes in throughout
        this carve mechanism -- not a new, riskier one."""
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        events: list[Event] = []
        tsf = states[task_id]
        attempt = tsf.attempt_by_id(attempt_id)
        m = re.match(r"^carve-.*-(\d+)$", task_id)
        seq = int(m.group(1)) if m else 0
        authority = getattr(cfg.policy, "carve_authority", "branch")

        # P57 2026-07-20 (M4, Fable-xhigh critique): a carver that exited on a
        # provider LIMIT / ERROR / BLOCKED wrote NO report (it never ran the
        # carve to completion). Before this, the code fell straight through to
        # report-parsing, found nothing, and emitted NEEDS_OPERATOR{carve-
        # parse-failed} -- conflating an INFRA failure with a malformed report
        # -- then SUPERSEDED (freeing the slot) so the NEXT pass immediately
        # dispatched a fresh carver into the SAME rate limit (a burn loop the
        # watchdog only catches after several wasted frontier dispatches +
        # operator pings, because attempt-loop counts per task_id and every
        # carve mints a fresh carve-<seq> task). Read the wrapper receipt
        # first: on a non-DONE result, ProviderPause the carve route on a
        # LIMIT (so the re-carve does not dive back into the same limit) and
        # escalate with a DISTINCT, typed reason -- never carve-parse-failed
        # -- then SUPERSEDE to free the slot as before.
        carve_result = attempt.receipt.result if attempt.receipt else None
        if carve_result is not None and carve_result is not ReceiptResult.DONE:
            if carve_result is ReceiptResult.LIMIT:
                events.extend(self._lifecycle.pause_provider(
                    ctx, attempt.route.route_id, task_id))
            events.append(self._append_ev(
                project, cfg, states, EventType.NEEDS_OPERATOR,
                {"reason": "carve-leg-failed", "result": carve_result.value, "seq": seq},
                task_id=task_id))
            events.append(self._append_ev(
                project, cfg, states, EventType.TASK_SUPERSEDED,
                {"from": states[task_id].state.value, "notes": "carve-leg-failed"},
                task_id=task_id))
            return events

        worktree = Path(attempt.worktree) if attempt.worktree else cfg.root
        if attempt.worktree and worktree.resolve() != cfg.root.resolve():
            # P58 2026-07-20 (M5, Fable-xhigh critique -- corrects P51). A
            # branch-authority carve worktree is a git worktree of the WHOLE
            # physical repo. cfg.root may BE the repo root (dstdns:
            # worktree_root=".worktrees") or a SUBDIR of it (nyxloom:
            # worktree_root="../.worktrees", nyxloom is a subdir of the vbpub
            # repo). The carver writes its report at <worktree>/<repo-relative
            # path of cfg.root>/<reports_dir>. P51 hard-coded cfg.root.name --
            # correct ONLY for the one-level-nested nyxloom layout; for a
            # repo-root project it inserted a bogus segment and EVERY carve
            # parse-failed (dstdns would reproduce the P51 incident the moment
            # its carving resumed). `git rev-parse --show-prefix` from
            # cfg.root is that repo-relative path exactly: "" for a repo-root
            # project, "nyxloom/" for the nested one. An empty component is a
            # no-op in Path joining, so one expression handles both layouts.
            prefix = subprocess.run(
                ["git", "-C", str(cfg.root), "rev-parse", "--show-prefix"],
                capture_output=True, text=True).stdout.strip()
            report_path = worktree / prefix / cfg.reports_dir / f"CARVE-{seq}.md"
        else:
            report_path = worktree / cfg.reports_dir / f"CARVE-{seq}.md"

        summary: CarveSummary | None = None
        if report_path.exists():
            try:
                from .daemon import CarveSummary  # see the note at module scope
                data = json.loads(report_path.read_text(encoding="utf-8"))
                summary = CarveSummary.from_dict(data)
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                summary = None

        if summary is not None:
            carves_dir = paths.project_dir(project) / "carves"
            carves_dir.mkdir(parents=True, exist_ok=True)
            persisted = {"seq": seq, "timestamp": iso(utc_now())}
            persisted.update(summary.to_dict())
            (carves_dir / f"{seq}.json").write_text(
                json.dumps(persisted, sort_keys=True), encoding="utf-8")

            carved_ids = [c.get("id", "") for c in summary.carved]
            outcome_payload: dict[str, Any] = {
                "seq": seq, "carved_ids": carved_ids, "outcome": summary.outcome,
                "headroom_estimate": summary.headroom_estimate,
            }
            # F007 2026-07-27 (GAP2): THE COMPARISON, daemon-side -- only when
            # this report actually carries blind judgments (every pre-GAP2
            # report, and every report from a project with the knob off,
            # leaves summary.verdict_audit == [] -> this whole block is a
            # no-op and outcome_payload is UNCHANGED, so the existing exact-
            # equality CARVE_OUTCOME payload assertions are unaffected).
            # DISPUTED entries become carve candidates through this SAME
            # envelope -- the CARVE_OUTCOME event this pass already emits to
            # report what came out of the carve -- rather than a second,
            # independent dispatch mechanism.
            if summary.verdict_audit:
                # No `events=`: this is an effect-boundary caller, so it takes
                # CR-02a's fresh typed acquisition, which raises rather than
                # handing back a silently empty log.
                disputes = self._verdict_audit_disputes(project, summary.verdict_audit)
                if disputes:
                    outcome_payload["verdict_audit_disputes"] = disputes
            events.append(self._append_ev(
                project, cfg, states, EventType.CARVE_OUTCOME, outcome_payload,
                task_id=task_id, attempt_id=attempt_id))

            # D-065 (B63 2026-07-20): 'headroom-low' and 'roadmap-exhausted' are
            # readings of the WORK roadmap's runway -- and 'roadmap-exhausted' is
            # read straight back by reconcile as roadmap_exhausted_open, which
            # THROTTLES item 9's headroom refill. A test-health carve (item 15)
            # never reads the work roadmap at all: it reports how much TEST debt
            # remains. Letting its numbers through here would mean a healthy suite
            # ("0 test-debt areas left") announcing the product roadmap was
            # exhausted and suppressing all further work carving -- a false alarm
            # that silently stalls the factory. Suppressed at the SOURCE rather
            # than steered around in packet prose, which would be LLM-dependent.
            is_test_health = self._carve_kind(states, task_id) == "test-health"
            if not is_test_health and summary.headroom_estimate < cfg.policy.headroom_warn:
                events.append(self._append_ev(
                    project, cfg, states, EventType.SPEC_ATTENTION,
                    {"reason": "headroom-low",
                     "detail": f"{summary.headroom_estimate} packages left"},
                    task_id=task_id))
            if not is_test_health and summary.outcome == "ROADMAP_EXHAUSTED":
                events.append(self._append_ev(
                    project, cfg, states, EventType.SPEC_ATTENTION,
                    {"reason": "roadmap-exhausted",
                     "detail": f"{summary.headroom_estimate} packages left"},
                    task_id=task_id))
            if authority == "branch":
                events.append(self._append_ev(
                    project, cfg, states, EventType.NEEDS_OPERATOR,
                    {"reason": "carve-ready", "carved_count": len(summary.carved),
                     "headroom_estimate": summary.headroom_estimate},
                    task_id=task_id))
        else:
            events.append(self._append_ev(
                project, cfg, states, EventType.NEEDS_OPERATOR,
                {"reason": "carve-parse-failed", "seq": seq}, task_id=task_id))

        events.append(self._append_ev(
            project, cfg, states, EventType.TASK_SUPERSEDED,
            {"from": states[task_id].state.value, "notes": "carve-consumed"},
            task_id=task_id))
        return events


def specs(effector: CarveEffector) -> tuple[effects.HandlerSpec, ...]:
    return (
        effects.HandlerSpec(
            action_type=reconcile.CarveDispatch,
            kind="carve-dispatch",
            handler=effector.carve_dispatch,
            # CARVER_SESSION_ROTATED is here because a carve that finds an
            # exhausted session ROTATES it rather than launching into one that
            # cannot answer -- the rotation is this handler's effect, not the
            # compact verb's. TASK_TRANSITIONED and CARVE_OUTCOME are NOT: the
            # first belongs to the re-scope path's supersede (a TASK_SUPERSEDED)
            # and the second to the exit CONSUMER, which CR-05e still owns.
            emits=frozenset({EventType.NEEDS_OPERATOR, EventType.TASK_CREATED,
                             EventType.ATTEMPT_CREATED,
                             EventType.ATTEMPT_PREFLIGHTED,
                             EventType.TASK_SUPERSEDED,
                             EventType.CARVER_SESSION_ROTATED}),
            idempotency_key=lambda a: f"carve-dispatch:{a.project}:{a.kind}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.AdmitCarveProposal,
            kind="admit-carve-proposal",
            handler=effector.admit_carve_proposal,
            emits=frozenset({EventType.TASK_CREATED, EventType.TASK_SUPERSEDED,
                             EventType.CARVER_PROPOSAL_ADMITTED}),
            idempotency_key=lambda a: f"admit-carve-proposal:{a.proposal_id}",
        ),
    )
