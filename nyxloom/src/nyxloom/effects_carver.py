"""The persistent strategic carver session (CR-05d).

WHAT A CARVER SESSION IS
------------------------
Ordinary carving mints a fresh throwaway agent per turn. A PROJECT-PERSISTENT
carver instead keeps ONE long-running provider session per generation, so the
strategic context it builds -- what has merged, what the spine says, which
proposals were rejected and why -- survives between turns instead of being
re-read from scratch every time.

That makes the session a cache, and the package's central rule follows from
it: **the provider session is never an evidence identity.** Every turn mints
a FRESH nyxloom attempt id, and the route is PINNED to the generation from
the durable snapshot rather than re-selected per turn, so a routes.toml
change is picked up at the next ROTATION rather than silently mid-generation.
A warm session that produced a stale answer must not be able to launder it
through an identity the pipeline already trusts.

THE THREE VERBS
---------------
* :meth:`CarverEffector.start_session` cold-bootstraps a generation (ABSENT,
  COLD, or a ROTATING one already advanced to its target).
* :meth:`CarverEffector.resume_session` runs a warm turn -- merge-feed,
  targeted intake, repair-proposal, or recover.
* :meth:`CarverEffector.compact_session` handles context exhaustion. There is
  no proven compaction driver, so the only supported strategy is ROTATE: end
  the generation and let the planner cold-bootstrap a fresh one. Any other
  configured strategy escalates rather than inventing a mechanism -- it never
  hardcodes a provider `/compact` and never launches a wrapper.

Each verb RE-CHECKS the state it was planned against and refuses cleanly when
it has moved on. A pass plans from a snapshot; by the time an effect runs,
another action may already have consumed this pass's single carver slot.

FEATURE-OFF IS BYTE-IDENTICAL
-----------------------------
``cfg.carve.session`` defaults to "fresh", and :meth:`_carver_session` returns
None for it. That None threads through the planner input, whose own top-level
gate means every downstream carve decision runs its exact pre-session path.
The enablement acknowledgement is emitted once per daemon instance per
project, and that "once" is EFFECTOR state now rather than another set on the
shell.

PROPOSAL VALIDATION LIVES HERE
------------------------------
A carver turn's output is a PROPOSAL, not an admission. The validators moved
with the session because the repair-proposal turn needs them to name which
proposals it is asking the carver to fix -- and because a proposal that fails
validation must degrade the turn rather than record a half-proposal.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from . import (
    adapters, carver_session, config, effects, effects_dispatch, frontmatter,
    lint, merge_digest, paths, reconcile, snapshot, storage, wrapper,
)
from .config import ProjectConfig
from .log import get_logger
from .types import (
    Attempt, AttemptState, CarverStatus, Event, EventType, Role, Route,
    ReceiptResult, TERMINAL_TASK_STATES, TaskState, TaskStateFile, new_id,
    utc_now,
)

log = get_logger("effects.carver")

# F018 P3c (plan §2.1): the two carver TURN MODES that carry handoff-
# authoring WRITE AUTHORITY -- every other mode (bootstrap/merge-feed/
# targeted-intake/recover/compact) is read-only and never produces a
# CarverTurnResult envelope or a CARVER_PROPOSAL_RECORDED event. Only
# "carve" is reachable today (the normalize branch in
# _execute_carve_via_session_resume); "repair-proposal" has no emitting
# ResumeCarverSession mode wired in reconcile.py yet (see
# _carve_proposal_repair_escalations' own docstring) -- included here so
# _consume_carver_session_exit's write-authority check needs no future
# edit when that mode is wired.
_CARVE_WRITE_AUTHORITY_MODES = frozenset({"carve", "repair-proposal"})


class CarverEffector:
    """Owns the carver session's launches, its snapshot reader, and the
    proposal validators the repair turn depends on."""

    def __init__(self, ports: effects.EffectPorts) -> None:
        self._ports = ports
        #: Projects that already got their enablement acknowledgement in this
        #: daemon instance. Effector state, not the shell's: the "once" is a
        #: property of this effector's lifetime, and nothing else reads it.
        self._carver_enablement_warned: set[str] = set()

    # -- forwarding seams -------------------------------------------------
    #
    # The moved bodies call these by the names they always used. Keeping the
    # names means the move is a MOVE -- reviewable against the original -- and
    # the implementations are one line each, straight to a port.

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

    def _carver_session(self, project: str, cfg: ProjectConfig,
                        *, events: Sequence[Event] | None = None,
                        ) -> carver_session.CarverSessionSnapshot | None:
        """F018 P2b-A2: the MASTER GATE for the whole carver-snapshot input
        surface. `cfg.carve.session` defaults to "fresh" ("feature off") --
        in that case this returns None, which _build_input threads straight
        through to ReconcileInput.carver_session (itself defaulting to None),
        and reconcile.plan_project's own top-level `if inp.carver_session is
        not None` gate means every downstream carve-dispatch decision runs
        its exact pre-P2b path, byte-identical.

        When "project-persistent", project the durable CARVER_* event log
        into a snapshot via carver_session's own pure projector -- reading
        the log the same way _history/_roadmap_exhausted_open/
        _spec_attention_recently_emitted above do (storage.iter_events,
        full log, fail-safe to an empty list on a read error so a projector
        call never raises out of _build_input)."""
        if cfg.carve.session != "project-persistent":
            return None
        # F018 P4a (AF3 #5): enablement acknowledgement -- emit ONCE per daemon
        # instance for every project that has opted into the persistent-carver
        # feature. The PRE-ENABLEMENT CHECKLIST is now fully clear (item 1/ack-
        # cursor closed by P4a; item 2/compact-half by P3d wiring
        # _execute_compact_carver_session; items 3-6 by P3d), so this is an
        # informational pilot acknowledgement -- NOT a "premature" warning and
        # NOT a hard reject. Enabling project-persistent is the operator's
        # decision; this line just makes an active pilot visible once at boot.
        if project not in self._carver_enablement_warned:
            self._carver_enablement_warned.add(project)
            log.warning(
                "carver.enablement.active",
                project=project,
                session=cfg.carve.session,
                notes=("project-persistent (long-running strategic carver) is "
                       "ACTIVE for this project: the pre-enablement checklist "
                       "is fully clear. Operator-acknowledged pilot -- monitor "
                       "merge-feed cadence and context growth."),
            )
        # CR-02a: the log is AUTHORITATIVE. The former fail-safe-to-[] made
        # an unreadable log project a BRAND NEW session -- generation 0, no
        # consumed cursor -- which re-ingests every merge feed and re-admits
        # every proposal.
        return carver_session.project_session(
            list(self._require_events(project, events)))

    def _spine_revisions(self, cfg: ProjectConfig) -> dict[str, str]:
        """F018 P3a (plan §2.2/§2.5): sha256 of each CONFIGURED spine doc's
        current on-disk content, keyed by spine level -- CARVER_SESSION_
        STARTED's spine_revisions payload. A level with no configured path,
        or whose file does not exist on disk, is simply omitted (never a
        fabricated hash). Deliberately a SEPARATE small implementation from
        merge_digest.py's own spine hashing (module-private, and hashes a
        specific PAST commit via `git show` for a merge digest) -- bootstrap
        has no single commit to hash against; it hashes the CURRENT working
        tree, the same "spine ground truth" merge_digest.py's spine_delta
        computes from, just at a different point in time."""
        out: dict[str, str] = {}
        for level, rel in (
            ("north_star", cfg.north_star),
            ("product_definition", cfg.product_definition),
            ("roadmap", cfg.roadmap),
            ("backlog", cfg.backlog),
        ):
            if not rel:
                continue
            p = cfg.root / rel
            try:
                content = p.read_bytes()
            except OSError:
                continue
            out[level] = hashlib.sha256(content).hexdigest()
        return out

    def _recent_merge_digest_ids(self, project: str, limit: int,
                                  *, events: Sequence[Event] | None = None) -> list[str]:
        """F018 P3a (plan §2.5 item 4): the most recent `limit` merge-digest
        ids, newest first -- named by POINTER only in the bootstrap packet
        (never the full digest body). Mirrors _pending_carver_feeds' own
        MERGE_RECORDED scan (A2), minus the consumed-cursor filter (a cold
        bootstrap has no session yet to have consumed anything)."""
        if limit <= 0:
            return []
        events = list(self._require_events(project, events))
        # A malformed carver_digest (present but missing digest_id) is skipped
        # rather than raising KeyError -- matching A2's `_pending_carver_feeds`
        # (digest.get("digest_id", "")) and P4a's _highest_consumed_feed_sequence
        # (digest.get("digest_id")); this is the only merge-digest scan that used
        # a raw index. Feature-on-only path (cold bootstrap packet).
        ids: list[str] = []
        for ev in events:
            if ev.type is not EventType.MERGE_RECORDED:
                continue
            digest = ev.payload.get("carver_digest")
            if not digest:
                continue
            did = digest.get("digest_id")
            if did is not None:
                ids.append(did)
        return ids[-limit:][::-1]

    def _highest_consumed_feed_sequence(self, project: str,
                                        source_ids: tuple[str, ...],
                                        *, events: Sequence[Event] | None = None,
                                        ) -> int | None:
        """F018 P4a: scan MERGE_RECORDED events for the highest
        event_sequence whose carver_digest.digest_id appears in
        source_ids -- so the emit can acknowledge exactly which feeds
        were consumed and stop re-firing them every pass. Returns None
        if no match (nothing to acknowledge).

        CR-02a: the former ``except Exception: return None`` reported "no
        feed consumed" from an unreadable log, so the consumed cursor never
        advanced and every feed re-fired every pass."""
        if not source_ids:
            return None
        id_set = set(source_ids)
        highest: int | None = None
        for ev in self._require_events(project, events):
            if ev.type is not EventType.MERGE_RECORDED:
                continue
            digest = ev.payload.get("carver_digest")
            if not digest:
                continue
            if digest.get("digest_id") in id_set:
                if highest is None or ev.sequence > highest:
                    highest = ev.sequence
        return highest

    def _next_carve_seq(self, project: str,
                         *, events: Sequence[Event] | None = None) -> int:
        """Monotonic per-project carve sequence: count of past
        ATTEMPT_CREATED events whose attempt.role == 'carver', + 1.
        Recomputed from the event log every call (never in-memory-only, per
        this codebase's "residency is never authority" rule) so a daemon
        restart, or a prior carve that never produced a CARVE_OUTCOME
        (parse failure), still never collides with the next dispatch's
        branch/worktree/report path.

        CR-02a: ``except Exception: events = []`` reset the sequence to 1 on
        an unreadable log -- COLLIDING with an existing carve branch,
        worktree and report path, i.e. the exact failure the whole-log recount
        exists to prevent."""
        count = 0
        for ev in self._require_events(project, events):
            if ev.type is EventType.ATTEMPT_CREATED:
                att = ev.payload.get("attempt") or {}
                if att.get("role") == Role.CARVER.value:
                    count += 1
        return count + 1

    def _carve_in_flight(self, states: dict[str, TaskStateFile]) -> bool:
        """The SAME predicate reconcile.py's planner already computes at
        PLAN time (shared by module contract items 9/12 and the carver-
        session ladder) -- recomputed fresh here at EXECUTION time, closing
        the identical plan/execute gap _dispatch_admissible already closes
        for pause/budget (P55/R5). Belt-and-braces: the planner's own
        carve_dispatch_planned mutex already keeps at most one carve/carver
        action per pass in the ordinary case; this refuses cleanly (no side
        effect) on the rare race instead of double-launching."""
        return any(
            tsf.state not in TERMINAL_TASK_STATES
            and any(a.role is Role.CARVER for a in tsf.attempts)
            for tsf in states.values()
        )

    def _build_carver_bootstrap_packet(self, cfg: ProjectConfig, project: str,
                                       seq: int, states: dict[str, TaskStateFile]) -> str:
        """F018 P3a (plan §2.5): the COLD BOOTSTRAP packet -- durable truth
        by POINTER, never a repository crawl (§2.5: "Bootstrap does not
        recursively inspect the repository"). Mirrors _build_carve_packet's
        economy (point at sources, embed only what is cheap/structured).
        This is a READ-ONLY turn (§2.1: bootstrap/feed/intake/compaction
        turns never author files) -- the only output asked for is a short
        acknowledgement, never a carve-proposal (proposal authoring/
        admission is P3b, out of scope here)."""
        lines = [
            f"# Strategic carver bootstrap {seq}",
            "",
            "## Your role: CARVER (session bootstrap, READ-ONLY)",
            "",
            f"You are establishing the persistent strategic-carver session for "
            f"project '{project}'. This is a COLD BOOTSTRAP turn: read the "
            "durable truth named below and establish your working context. Do "
            "NOT author, edit, or commit any handoff/code files this turn -- "
            "carving happens on a later, separate turn once this session is "
            "warm.",
            "",
            "## Durable truth (read by pointer, not inlined)",
        ]
        for level, rel in (
            ("north_star", cfg.north_star),
            ("product_definition", cfg.product_definition),
            ("roadmap", cfg.roadmap),
            ("backlog", cfg.backlog),
        ):
            lines.append(f"- {level}: {rel}" if rel else f"- {level}: not configured")
        lines.append(f"- decisions inbox: {cfg.decisions_inbox}")
        lines.append("- handoff-authoring doctrine: reference/AUTHORING.md")
        lines.append("")
        lines.append("## Current non-terminal tasks")
        task_lines = []
        for task_id in sorted(states.keys()):
            tsf = states[task_id]
            if tsf.state in TERMINAL_TASK_STATES:
                continue
            note = f"- {task_id}: {tsf.state.value}"
            if tsf.handoff_path:
                note += f" (handoff: {tsf.handoff_path})"
            if tsf.blocker is not None:
                note += f" [blocked: {tsf.blocker.type.value}]"
            task_lines.append(note)
        lines.extend(task_lines if task_lines else ["- (none)"])
        lines.append("")
        lines.append(f"## Recent merge digests (most recent {cfg.carve.retain_merge_digests})")
        digest_ids = self._recent_merge_digest_ids(project, cfg.carve.retain_merge_digests)
        lines.extend([f"- {d}" for d in digest_ids] if digest_ids else ["- (none yet)"])
        lines.append("")
        lines.append("## Spine revisions (echo these in your BOOTSTRAP-ACK)")
        spine_revs = self._spine_revisions(cfg)
        for level, rev in spine_revs.items():
            lines.append(f"- {level}: {rev}")
        if not spine_revs:
            lines.append("- (none configured)")
        lines.append("")
        lines.append("## REQUIRED OUTPUT")
        lines.append(
            f"Write {cfg.reports_dir}/BOOTSTRAP-ACK.json with the following "
            "content, once you have read the sources above:"
        )
        lines.append("")
        lines.append('```json')
        lines.append('{')
        lines.append('  "kind": "bootstrap-ack",')
        lines.append('  "spine_revisions": {')
        for i, (level, rev) in enumerate(spine_revs.items()):
            comma = "," if i < len(spine_revs) - 1 else ""
            lines.append(f'    "{level}": "{rev}"{comma}')
        if not spine_revs:
            lines.append('  }')
        else:
            lines.append('  }')
        lines.append('}')
        lines.append('```')
        lines.append(
            "No other output is required this turn. If you cannot write the "
            "acknowledgement file, print exactly one line `BOOTSTRAP-ACK: fail`."
        )
        return "\n".join(lines) + "\n"

    def _build_carver_resume_prompt(self, project: str, task_id: str, mode: str,
                                    source_ids: tuple[str, ...],
                                    repair_ids: tuple[str, ...] = ()) -> str:
        """F018 P3a (plan §3.2/§5.2/§8.2 packet-shaping vocabulary): the
        MODE-SPECIFIC resume prompt fed directly to adapters.build_resume
        (unchanged) -- build_resume takes a flat prompt string, unlike
        build_dispatch's handoff_path indirection, so there is no separate
        packet file for a resume turn. Names source ids by POINTER only --
        the full digest/intake bodies live in the durable event log (the
        session's own bootstrap + prior turns already cover it); never
        re-embedded here. Capped to a handful of ids as defense in depth --
        reconcile's planner already batches within a configured prompt-size
        bound per plan §3.2, this is a second, cheap backstop.

        F018 AD3: `mode == "repair-proposal"` is a WRITE-AUTHORITY turn (it
        re-authors the proposal artifacts), so it does NOT share the read-only
        framing the other resume modes carry -- it returns its own packet
        early. `repair_ids` are the invalid proposal ids the daemon re-derived
        for THIS turn (via _pending_carve_repairs), named as pointers; the
        packet states the re-validation CHECKLIST rather than a precise
        per-field reason (which _validate_carve_proposal_payload does not
        report -- see the DTO's own no-`validation_error` rationale)."""
        if mode == "repair-proposal":
            ids_note = ", ".join(repair_ids[:20]) if repair_ids else "(none)"
            return (
                f"Resume the strategic-carver session for project '{project}' "
                f"(mode=repair-proposal, turn={task_id}). This is a WRITE-"
                f"AUTHORITY turn. Your carve proposal(s) {ids_note} FAILED "
                "structural validation and were NOT admitted. Re-emit a "
                "corrected CARVER_PROPOSAL_RECORDED envelope that satisfies "
                "EVERY item of this re-validation checklist -- for each "
                "artifact: (1) its `sha256` matches the file content exactly; "
                "(2) its frontmatter parses; (3) its `input_revision` equals "
                "the source `base_revision`; (4) `nyxloom lint` is clean; "
                "(5) every oracle path is within the handoff's `scope.touch` "
                "(the L13 rule) -- plus, for the envelope itself, a well-formed "
                "`proposal_id`/`turn_id` for the CURRENT generation. Do NOT "
                "merge and do NOT ingest new merge feeds this turn. Emit only "
                "the corrected proposal envelope, then print a one-line "
                "acknowledgement when done."
            )
        ids_note = ", ".join(source_ids[:20]) if source_ids else "(none)"
        if mode == "merge-feed":
            task_note = f"Incorporate the pending merge digest(s): {ids_note}."
        elif mode == "targeted-intake":
            task_note = f"Incorporate the pending human intake turn(s): {ids_note}."
        else:  # "recover" (§5.4 bounded DEGRADED recovery)
            task_note = (
                "Recover this session after a prior resume failure. You MUST "
                "also write a BOOTSTRAP-ACK.json acknowledgement as described "
                "in the bootstrap packet instructions (spine revisions echo)."
            )
        return (
            f"Resume the strategic-carver session for project '{project}' "
            f"(mode={mode}, turn={task_id}). This is a READ-ONLY turn -- do "
            f"NOT author, edit, or commit any handoff/code files. {task_note} "
            "Print a one-line acknowledgement when done."
        )

    def _parse_proposal_id(self, cfg: ProjectConfig, proposal_id: Any
                           ) -> tuple[int, str] | None:
        """(generation, turn_id) parsed from a proposal_id matching plan
        §4.1's documented format '<project>:carve:<generation>:<turn-id>',
        or None on any structural mismatch (including a project prefix
        that is not THIS project, or an empty turn-id segment -- covers
        the 'wrong ... turn id' negative oracle for a forged/foreign
        proposal_id). Never raises -- untrusted model-adjacent input."""
        if not isinstance(proposal_id, str) or not proposal_id:
            return None
        parts = proposal_id.split(":")
        if len(parts) != 4 or parts[0] != cfg.project_id or parts[1] != "carve" or not parts[3]:
            return None
        try:
            generation = int(parts[2])
        except ValueError:
            return None
        return generation, parts[3]

    def _resolve_carve_proposal_artifact(self, cfg: ProjectConfig, raw_path: Any) -> Path | None:
        """Resolve one proposal artifact's declared path under cfg's
        configured handoff globs (plan §4.1: 'resolves the path under
        configured handoff globs; rejects traversal and unexpected
        paths'). Reuses frontmatter.discover_handoffs(cfg) -- the SAME
        source of truth _build_input's own ordinary 'new handoffs' scan
        uses -- as the membership test, rather than a second, independently
        drifting glob/traversal check: a path this rejects can also never
        become a task via the ordinary scan, and a path this accepts is
        exactly one the ordinary scan would also surface. None on any
        rejection (absolute/parent-relative traversal, not matching any
        handoff_globs pattern, or resolving outside cfg.root)."""
        if not isinstance(raw_path, str) or not raw_path:
            return None
        if raw_path.startswith("/") or ".." in Path(raw_path).parts:
            return None
        try:
            resolved = (cfg.root / raw_path).resolve()
            resolved.relative_to(cfg.root.resolve())
        except (ValueError, OSError):
            return None
        try:
            discovered = {p.resolve() for p in frontmatter.discover_handoffs(cfg)}
        except Exception:  # census: authority-bearing, fail-closed (CR-02b)
            # This decides whether a carver-named path is an ADMISSIBLE
            # handoff artifact. An unreadable handoff set must therefore
            # REFUSE the path rather than substitute an empty discovery set,
            # which would answer "not a known handoff" for every path and
            # look identical to a genuine rejection.
            return None
        return resolved if resolved in discovered else None

    def _proposal_already_admitted(self, project: str, proposal_id: str,
                                    *, events: Sequence[Event] | None = None) -> bool:
        """True iff a CARVER_PROPOSAL_ADMITTED event for this exact
        proposal_id already exists in the event log -- the durable
        'consumed' cursor _validated_carve_proposals uses (AD1 fix; see
        its own docstring for why a states-membership check is
        insufficient).

        CR-02a: the old ``except Exception: return False`` was described as
        "the conservative direction", and it is not -- False means NOT yet
        admitted, so an unreadable log RE-ADMITS an already-admitted proposal
        and re-runs its supersession. The log is now authoritative here too."""
        return any(
            ev.type is EventType.CARVER_PROPOSAL_ADMITTED
            and ev.payload.get("proposal_id") == proposal_id
            for ev in self._require_events(project, events)
        )

    def _validate_carve_proposal_payload(self, cfg: ProjectConfig,
                                          snap: carver_session.CarverSessionSnapshot,
                                          payload: dict[str, Any],
                                          ) -> tuple[carver_session.ValidatedCarveProposal | None, str]:
        """Plan §4.1's full per-artifact validation pipeline for ONE
        CARVER_PROPOSAL_RECORDED payload (a CarverTurnResult envelope, see
        carver_session.CarverTurnResult): proposal_id/turn_id structure and
        CONCERN-1 generation match, then for every artifact -- path
        resolution, content-hash verification, frontmatter parse,
        `nyxloom lint`, `input_revision == source.base_revision`, and
        every oracle satisfiable within scope.touch (L13) or a named
        mechanical escalation (L8 -- already lint-error severity).

        Returns ``(None, <stable reject code>)`` (never raises) the instant
        ANY dimension fails -- an invalid proposal creates no
        ValidatedCarveProposal and no task, only a bounded repair signal
        counted by _carve_proposal_repair_escalations.

        CR-02a: the reject CODE is new. Behaviour is unchanged (still None,
        still fail-closed) but the reason is no longer discarded: this is
        untrusted model output claiming a content digest for a file that will
        become a dispatched work package, so "the digest did not match" and
        "the file is not under handoff_globs" and "lint rejected it" must be
        distinguishable to an operator. The codes are short and closed; no
        artifact CONTENT is ever included.
        """
        parsed = self._parse_proposal_id(cfg, payload.get("proposal_id"))
        if parsed is None:
            return None, "proposal-id-malformed"
        generation, turn_seg = parsed
        if generation != snap.generation:
            return None, "generation-mismatch"

        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str) or turn_id != turn_seg:
            # absent, or inconsistent with proposal_id's own turn segment
            return None, "turn-id-mismatch"

        source = payload.get("source")
        if not isinstance(source, dict):
            return None, "source-malformed"
        base_revision = source.get("base_revision")

        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            return None, "artifacts-empty"

        artifact_ids: list[str] = []
        artifact_paths: list[str] = []
        artifact_hashes: list[str] = []
        for art in artifacts:
            if not isinstance(art, dict):
                return None, "artifact-malformed"
            declared_hash = art.get("sha256")
            if not isinstance(declared_hash, str) or not declared_hash:
                return None, "artifact-digest-absent"
            resolved = self._resolve_carve_proposal_artifact(cfg, art.get("path"))
            if resolved is None:
                return None, "artifact-path-rejected"
            try:
                content = resolved.read_bytes()
            except OSError:
                return None, "artifact-unreadable"
            actual_hash = hashlib.sha256(content).hexdigest()
            if actual_hash != declared_hash:
                return None, "artifact-digest-mismatch"
            try:
                fm, _body = frontmatter.parse_handoff(resolved)
            except Exception:  # census: authority-bearing, fail-closed (CR-02a)
                # Untrusted model-authored frontmatter: ANY parse failure is a
                # rejection, and the rejection is what makes it safe. Narrowing
                # this to a specific exception type would let an unanticipated
                # parser error propagate and abort the whole validation loop,
                # which is LESS closed than rejecting this one proposal.
                return None, "artifact-frontmatter-unparsable"
            if fm.input_revision != base_revision:
                return None, "artifact-base-revision-mismatch"
            try:
                findings = lint.lint_file(resolved, cfg)
            except Exception:  # census: authority-bearing, fail-closed (CR-02a)
                # Same shape as the frontmatter parse above: a lint that
                # cannot run is not a lint that passed.
                return None, "artifact-lint-error"
            # L13 ('oracle satisfiable within scope.touch') is warning-
            # severity in lint.py's own general policy (deliberately, to
            # avoid gating the pre-existing CARVED->QUEUED corpus-wide --
            # see lint.py's L13 docstring), but proposal admission has no
            # independent human reviewer standing in for it, so THIS gate
            # treats L13 as blocking in addition to lint.has_blocking's
            # own error-severity findings (which already include L8,
            # 'escalate_if triggers are mechanical').
            if lint.has_blocking(findings) or any(f.rule == "L13" for f in findings):
                return None, "artifact-lint-blocking"
            artifact_ids.append(fm.id)
            artifact_paths.append(str(resolved.relative_to(cfg.root.resolve())))
            artifact_hashes.append(actual_hash)

        return carver_session.ValidatedCarveProposal(
            proposal_id=payload["proposal_id"], generation=generation,
            source_mode=str(source.get("mode", "")),
            artifact_ids=artifact_ids, artifact_paths=artifact_paths,
            artifact_hashes=artifact_hashes, outcome=str(payload.get("outcome", "")),
        ), ""

    def _validated_carve_proposals(self, project: str, cfg: ProjectConfig,
                                    snap: carver_session.CarverSessionSnapshot | None,
                                    *, builder: snapshot.SnapshotBuilder | None = None,
                                    events: Sequence[Event] | None = None,
                                    ) -> tuple[carver_session.ValidatedCarveProposal, ...]:
        """F018 P3b (plan-long-running-carver.md §4.1-§4.3): the SOLE
        validation authority for a carver turn's proposal envelope --
        untrusted model output never reaches reconcile.plan_project
        directly (§4.3: "the daemon, not the model, decides whether a
        proposal was schema-valid"). `snap is None` means the MASTER GATE
        (_carver_session) is off -- () byte-identical to every pre-P3b
        test/pass, matching pending_carver_feeds' own convention.

        CONCERN-1 (single-authority generation filter, plan-next-batches.md
        A1-review finding): a proposal recorded under a PRIOR/stale (or
        future) generation is excluded HERE, inside
        _validate_carve_proposal_payload -- reconcile.plan_project's pure
        ladder does not re-check this, so this builder is the only gate.

        AD1 FIX (2026-07-25 adversarial review): exclusion of an already-
        admitted proposal is keyed on a durable CARVER_PROPOSAL_ADMITTED
        event (_proposal_already_admitted), NOT on task presence in
        `states`. The original "all artifact_ids already in states"
        structural check was WRONG: a proposal's artifact MUST sit under
        cfg.handoff_globs for validation to resolve/hash it, which means
        plan_project's OWN 'new handoffs' scan (item 1, CreateTask -- see
        its "Combine results in order" section: lifecycle actions,
        including CreateTask, are combined into `actions` BEFORE
        carver_actions) can independently discover and create the SAME
        task in the SAME pass, before AdmitCarveProposal ever executes.
        Under the old states-based check this made the proposal look
        pre-consumed and permanently skipped -- silently dropping step-4
        re-scope supersession (the origin task would never leave
        READY_TO_CARVE). Keying exclusion on the ADMISSION EVENT itself
        (only ever emitted by _execute_admit_carve_proposal's own step 2,
        which ALSO performs step 4) means a proposal is selected and
        admitted for real at least once regardless of which path (the
        ordinary scan or admission) happens to create its tasks first --
        see test_race_with_ordinary_scan_does_not_suppress_validation and
        test_ad1_real_run_pass_admits_and_supersedes_origin.
        """
        if snap is None:
            return ()
        b = builder if builder is not None else snapshot.SnapshotBuilder()
        events = list(self._require_events(project, events))

        candidates: list[carver_session.ValidatedCarveProposal] = []
        rejected: list[str] = []
        for ev in events:
            if ev.type is not EventType.CARVER_PROPOSAL_RECORDED:
                continue
            if ev.sequence <= snap.last_consumed_event_sequence:
                continue
            vp, reject_reason = self._validate_carve_proposal_payload(
                cfg, snap, ev.payload or {})
            if vp is None:
                # CR-02a: rejection was already fail-CLOSED (no proposal, no
                # task) but entirely invisible -- an operator watching a warm
                # carver produce nothing had no way to tell a clean "nothing
                # to carve" turn from a systematically malformed one. The
                # reason is now recorded as a bounded advisory degradation.
                # It stays advisory, not authoritative: refusing an invalid
                # proposal IS the correct outcome, and F018's own bounded
                # repair/escalation ladder already handles persistence.
                rejected.append(f"{ev.sequence}:{reject_reason}")
                continue
            if self._proposal_already_admitted(project, vp.proposal_id, events=events):
                continue
            candidates.append(vp)

        if rejected:
            b.add(snapshot.SnapshotInput.failed(
                "carve_proposal_artifacts", snapshot.InputClass.ADVISORY,
                snapshot.Reason.MALFORMED_VALUE,
                provenance=snapshot.Provenance("artifact-digest", cfg.project_id),
                status=snapshot.InputStatus.MALFORMED,
                detail=" ".join(rejected)))
        candidates.sort(key=lambda p: p.proposal_id)
        return tuple(candidates)

    def _pending_carve_repairs(self, project: str, cfg: ProjectConfig,
                               snap: carver_session.CarverSessionSnapshot | None,
                               *, events: Sequence[Event] | None = None,
                               ) -> tuple[carver_session.CarveRepairRequest, ...]:
        """F018 AD3 (docs/handoff/f018-ad3-carve-repair-proposal.md): the
        structurally-invalid carve proposals for the CURRENT generation that
        the warm session should re-emit correctly BEFORE ingesting any new
        merge feed (reconcile's WARM ladder plans a
        ResumeCarverSession(mode="repair-proposal") from this).

        Reuses _carve_proposal_repair_escalations' EXACT per-artifact counting
        -- iterate CARVER_PROPOSAL_RECORDED, keep this-generation events
        (_parse_proposal_id), count those _validate_carve_proposal_payload
        rejects -- so the two are computed from the identical raw signal (the
        append-only event log; no new counter/EventType). It differs only in
        the RETURN: the invalid proposal_ids (for the repair packet) instead of
        a bare count.

        COMPOSES with P3b's escalation, never double-fires. Returns () unless
        `1 <= invalid < cfg.carve.max_proposal_repairs`. At/above the ceiling
        the repair signal is EMPTY and _carve_proposal_repair_escalations' own
        `invalid >= max_proposal_repairs` NEEDS_OPERATOR escalation fires
        instead -- one shared `<` vs `>=` boundary makes them mutually
        exclusive by construction. `snap is None` (feature off) -> ()
        byte-identical to every pre-AD3 pass, matching
        _validated_carve_proposals' own convention.

        Determinism: proposal_ids are counted per-EVENT (matching P3b's
        boundary exactly -- NEVER dedup for the ceiling check, or the boundary
        would diverge from P3b and the two could double-fire) and the returned
        requests are sorted by proposal_id. The repair turn the planner emits
        from this carries NO source_ids, so the P4a merge-feed shared cursor is
        untouched; and when a later VALID proposal exists for the same
        generation it is admitted via the ladder's higher-priority slot-1
        AdmitCarveProposal, so this signal only ever drives a turn when there is
        nothing admittable -- the ladder ordering, not this builder, guarantees
        that."""
        if snap is None:
            return ()
        invalid_ids: list[str] = []
        for ev in self._require_events(project, events):
            if ev.type is not EventType.CARVER_PROPOSAL_RECORDED:
                continue
            payload = ev.payload or {}
            proposal_id = payload.get("proposal_id")
            parsed = self._parse_proposal_id(cfg, proposal_id)
            if parsed is None or parsed[0] != snap.generation:
                continue
            if self._validate_carve_proposal_payload(cfg, snap, payload)[0] is None:
                invalid_ids.append(proposal_id)
        invalid = len(invalid_ids)
        if invalid == 0 or invalid >= cfg.carve.max_proposal_repairs:
            return ()
        return tuple(
            carver_session.CarveRepairRequest(
                proposal_id=pid, generation=snap.generation)
            for pid in sorted(invalid_ids)
        )

    def start_session(self, ctx: effects.EffectContext,
                      action: reconcile.StartCarverSession) -> list[Event]:
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        """plan §5.1 items 1-4 (items 5-7 happen at exit-consumption, see
        _consume_carver_session_exit). Mirrors _execute_carve_dispatch's own
        structure (admission recheck, route resolution, synthetic task/
        attempt/worktree, wrapper dispatch under the strategic-carver
        lease) -- see that method's comments for the shared shape; this
        docstring only calls out what differs."""
        events: list[Event] = []

        # (a) recheck admissibility (pause/budget -- same "carve" kind
        # _execute_carve_dispatch already uses) + absence of a live carver
        # turn (execution-time recheck of the planner's own carve_in_flight
        # gate -- P55/R5 belt-and-braces). Refuse cleanly, no side effect.
        ok, _reason = effects_dispatch.admissible(ctx, "carve")
        if not ok:
            return events
        if self._carve_in_flight(states):
            return events

        snap = self._carver_session(project, cfg)
        if snap is None or snap.status not in (
                CarverStatus.ABSENT, CarverStatus.COLD, CarverStatus.ROTATING):
            # Stale plan (session state moved on since this pass was
            # planned, e.g. another action already consumed this pass's
            # single carver slot) -- refuse cleanly.
            return events

        # (b) resolve the carver route -- reuse EXACTLY what
        # _execute_carve_dispatch uses for the carve role (defense-in-depth:
        # reconcile.py's own trigger already required a healthy
        # 'review-independent' route before ever emitting StartCarverSession).
        routes_obj = config.Routes.load()
        review_routes = routes_obj.for_role(Role.REVIEW_INDEPENDENT.value)
        if not review_routes:
            if not self._needs_operator_recently_emitted(
                    project, "carver-no-route"):
                events.append(self._append_ev(
                    project, cfg, states, EventType.NEEDS_OPERATOR,
                    {"reason": "carver-no-route"}, task_id=None))
            return events
        route_def = review_routes[0]
        # (b2) re-ask admission now that the route is resolved (CR-13a):
        # containment is a property of the route, so it cannot be asked in
        # step (a) above.
        ok, _reason = effects_dispatch.admissible(ctx, "carve", route_def)
        if not ok:
            return events

        # (c) mint a synthetic carver attempt + worktree (branch authority,
        # like the current carve) -- SAME _next_carve_seq counter as legacy
        # carve (a shared monotonic id space is harmless: the DISTINCT
        # "carver-session-" task_id/branch prefix below is what prevents any
        # path/branch collision with a legacy carve-<project>-<seq>).
        seq = self._next_carve_seq(project)
        task_id = f"carver-session-{project}-{seq}"
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

        # ABSENT/COLD start generation 1 (or the next one, if a prior
        # generation existed without ever rotating); ROTATING has already
        # been advanced to its target generation by CARVER_SESSION_ROTATED
        # (P3d territory, not emitted by this package) -- bootstrap INTO
        # that already-computed generation rather than incrementing again.
        generation = (snap.generation if snap.status is CarverStatus.ROTATING
                     else snap.generation + 1)

        notes = f"carver-session seq={seq} kind=start mode={action.mode} generation={generation}"
        tsf = TaskStateFile(
            schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
            state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, notes=notes,
        )
        events.append(self._append_ev(project, cfg, states, EventType.TASK_CREATED,
                                       {"statefile": tsf.to_dict()}, task_id=task_id))

        attempt_id = new_id("att")
        attempt_dir = paths.attempt_dir(project, attempt_id)
        packet_dir = attempt_dir / "packet"
        packet_dir.mkdir(parents=True, exist_ok=True)
        packet_text = self._build_carver_bootstrap_packet(cfg, project, seq, states)
        (packet_dir / "packet.md").write_text(packet_text, encoding="utf-8")

        route_snap = Route(route_id=route_def.route_id, cli=route_def.cli, model=route_def.model,
                           variant=route_def.variant, effort=route_def.effort,
                           routes_rev=routes_obj.revision)
        attempt = Attempt(attempt_id=attempt_id, role=Role.CARVER, state=AttemptState.CREATED,
                          route=route_snap, started=utc_now(), worktree=str(carve_cwd),
                          branch=dispatch_branch if authority == "branch" else None)
        events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_CREATED,
                                       {"attempt": attempt.to_dict()}, task_id=task_id,
                                       attempt_id=attempt_id))

        # (d)+(e) build the bootstrap packet, launch through the wrapper
        # with the strategic-carver lease. (f)/(g) -- capture the session
        # id and decide STARTED-vs-DEGRADED -- happen later, at exit-
        # consumption time (see the class docstring above this section).
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
        return events

    def resume_session(self, ctx: effects.EffectContext,
                       action: reconcile.ResumeCarverSession) -> list[Event]:
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        """plan §5.2: a warm-session turn (merge-feed / targeted-intake /
        recover). Mints a FRESH nyxloom attempt/turn id every call -- the
        persistent provider session is a cache/context optimization, never
        reused as an evidence identity (§5.2). The route is PINNED for the
        generation: resolved from the durable session snapshot's own route
        record, never re-selected via for_role (a routes.toml change is
        picked up on the NEXT rotation, not mid-generation)."""
        events: list[Event] = []

        ok, _reason = effects_dispatch.admissible(ctx, "carve")
        if not ok:
            return events
        if self._carve_in_flight(states):
            return events

        snap = self._carver_session(project, cfg)
        required_status = CarverStatus.DEGRADED if action.mode == "recover" else CarverStatus.WARM
        if snap is None or snap.status is not required_status or not snap.session_id:
            # Stale plan (status/session moved on since planning) -- refuse
            # cleanly, same rationale as the Start recheck above.
            return events

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
        # The generation's PINNED route -- the first point at which
        # admission's containment half (CR-13a) can be asked.
        ok, _reason = effects_dispatch.admissible(ctx, "carve", route_def)
        if not ok:
            return events

        seq = self._next_carve_seq(project)
        task_id = f"carver-session-{project}-{seq}"
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

        # F018 P3c fold-in (plan §2.2 gap): 'sources=' persists action.
        # source_ids on the turn's own statefile notes (mirroring the
        # kind=/mode=/generation= convention _carver_turn_marker already
        # parses) so _consume_carver_session_exit can fold them into
        # CARVER_SESSION_RESUMED's payload at exit time -- notes is the
        # established mechanism for surviving a daemon restart between
        # launch and exit-consumption (see _carve_kind's identical
        # rationale). Comma-joined: every source id this codebase mints
        # (digest_id "merge:<project>:<n>", intake_id, task_id) is a single
        # whitespace-free token, so a comma-joined value round-trips exactly
        # via str.split(",") with no escaping needed.
        sources_token = ",".join(action.source_ids) if action.source_ids else "(none)"
        notes = (f"carver-session seq={seq} kind=resume mode={action.mode} "
                f"generation={snap.generation} sources={sources_token}")
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

        # F018 AD3: a repair-proposal turn names the invalid proposal ids in
        # its packet. Re-derive them fresh from the log here (the daemon side
        # has cfg+snap; the reconcile action deliberately carries no per-
        # proposal targeting, keeping the planner pure). Empty for every other
        # mode -> byte-identical packet.
        repair_ids: tuple[str, ...] = ()
        if action.mode == "repair-proposal":
            repair_ids = tuple(
                r.proposal_id for r in self._pending_carve_repairs(project, cfg, snap))
        prompt = self._build_carver_resume_prompt(
            project, task_id, action.mode, action.source_ids, repair_ids)
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
        return events

    def compact_session(self, ctx: effects.EffectContext,
                        action: reconcile.CompactCarverSession) -> list[Event]:
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        """F018 P3d AF1 (plan-long-running-carver.md §6.1): compaction→
        rotation fallback. No proven compaction driver exists (§6.1); the
        only supported strategy is "rotate" (rotate the generation and let
        the planner cold-bootstrap a fresh one next pass). For any other
        strategy value, emit NEEDS_OPERATOR{carver-compaction-no-driver}
        (debounced) and do not rotate. Never hardcodes /compact, never
        launches a wrapper."""
        events: list[Event] = []

        snap = self._carver_session(project, cfg)
        if snap is None or snap.status is not CarverStatus.WARM:
            return events  # stale plan -- refuse cleanly

        strategy = cfg.carve.compaction_strategy
        if strategy == "rotate":
            reason = "compaction-rotate-fallback"
            events.append(self._append_ev(
                project, cfg, states, EventType.CARVER_SESSION_ROTATED,
                {"new_generation": snap.generation + 1,
                 "reason": reason,
                 "trigger": action.trigger,
                 "from_generation": snap.generation},
                task_id=None))
        else:
            if not self._needs_operator_recently_emitted(
                    project, "carver-compaction-no-driver"):
                events.append(self._append_ev(
                    project, cfg, states, EventType.NEEDS_OPERATOR,
                    {"reason": "carver-compaction-no-driver",
                     "strategy": strategy},
                    task_id=None))
        return events
    @staticmethod
    def _carver_turn_marker(states: dict[str, TaskStateFile], task_id: str
                            ) -> tuple[str, str, int, tuple[str, ...]]:
        """Recover a carver-session turn's (kind, mode, generation,
        source_ids) at exit-consumption time, when only the task is in
        hand. Mirrors _carve_kind's identical convention: the marker rides
        tsf.notes (replayed statefile state, so it survives a daemon
        restart between launch and exit) rather than a second event-log
        scan. Defaults are the safe "nothing recognized" fallback; every
        task this package creates stamps all four tokens itself (F018 P3c:
        source_ids added, comma-joined under a 'sources=' token -- see
        _execute_resume_carver_session/_execute_carve_via_session_resume's
        own notes construction), so the fallback only matters for a
        malformed/foreign task_id or a pre-P3c note with no sources= token
        at all (source_ids=() -- indistinguishable from, and handled the
        same as, an explicitly recorded empty set)."""
        tsf = states.get(task_id)
        kind, mode, generation, source_ids = "start", "headroom", 0, ()
        if tsf is None or not tsf.notes:
            return kind, mode, generation, source_ids
        for token in tsf.notes.split():
            if token.startswith("kind="):
                kind = token[len("kind="):]
            elif token.startswith("mode="):
                mode = token[len("mode="):]
            elif token.startswith("generation="):
                try:
                    generation = int(token[len("generation="):])
                except ValueError:
                    generation = 0
            elif token.startswith("sources="):
                raw = token[len("sources="):]
                source_ids = () if raw == "(none)" else tuple(raw.split(","))
        return kind, mode, generation, source_ids

    def _carver_proposal_report_path(self, cfg: ProjectConfig, attempt: Attempt,
                                     seq: int) -> Path:
        """F018 P3c: resolve a write-authority carve turn's CarverTurnResult
        envelope's expected on-disk path. Mirrors _consume_carve_exit's own
        branch-vs-cfg.root worktree-prefix resolution (P51/P58) for the
        IDENTICAL reason: a branch-authority carve worktree is a git
        worktree of the WHOLE physical repo, so cfg.root's own repo-relative
        prefix (empty for a repo-root project, non-empty for a nested one
        such as nyxloom under vbpub) must be inserted between the worktree
        and cfg.reports_dir. Kept as a separate, small helper (not a shared
        refactor of _consume_carve_exit's own inline logic) -- see this
        package's module contract on minimizing risk to the frozen legacy
        path; the two are structurally identical but serve different
        filenames/callers."""
        worktree = Path(attempt.worktree) if attempt.worktree else cfg.root
        filename = f"CARVER-PROPOSAL-{seq}.json"
        if attempt.worktree and worktree.resolve() != cfg.root.resolve():
            prefix = subprocess.run(
                ["git", "-C", str(cfg.root), "rev-parse", "--show-prefix"],
                capture_output=True, text=True).stdout.strip()
            return worktree / prefix / cfg.reports_dir / filename
        return worktree / cfg.reports_dir / filename

    def _carver_bootstrap_ack_path(self, cfg: ProjectConfig, attempt: Attempt) -> Path:
        """F018 P3d (concern-3 #3): resolve the expected BOOTSTRAP-ACK.json
        path under cfg.reports_dir, using the SAME worktree-prefix resolution
        as _carver_proposal_report_path (P3c) so branch authority works."""
        worktree = Path(attempt.worktree) if attempt.worktree else cfg.root
        filename = "BOOTSTRAP-ACK.json"
        if attempt.worktree and worktree.resolve() != cfg.root.resolve():
            prefix = subprocess.run(
                ["git", "-C", str(cfg.root), "rev-parse", "--show-prefix"],
                capture_output=True, text=True).stdout.strip()
            return worktree / prefix / cfg.reports_dir / filename
        return worktree / cfg.reports_dir / filename

    def _parse_carver_turn_result(self, cfg: ProjectConfig, attempt: Attempt,
                                  task_id: str) -> carver_session.CarverTurnResult | None:
        """F018 P3c: STRUCTURAL-only parse of a write-authority turn's
        CarverTurnResult envelope (plan §4.1) -- existence, valid JSON, and
        an exact match of CarverTurnResult's own dataclass shape (via
        _Serde.from_dict, which rejects unknown keys and requires every
        field with no default). CONTENT validity (artifact hash/lint/
        revision/scope) is P3b's _validate_carve_proposal_payload's job at
        plan-input time, not this executor's -- this gate only distinguishes
        'the carver wrote something we can record as a proposal' from
        'nothing usable landed', so a genuinely malformed/absent output
        degrades the session rather than ever recording a half-proposal.
        Mirrors _consume_carve_exit's own CarveSummary.from_dict parse
        (same exception tuple); never raises."""
        m = re.match(r"^carver-session-.*-(\d+)$", task_id)
        seq = int(m.group(1)) if m else 0
        report_path = self._carver_proposal_report_path(cfg, attempt, seq)
        if not report_path.exists():
            return None
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
            return carver_session.CarverTurnResult.from_dict(data)
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return None

    def consume_session_exit(self, ctx: effects.EffectContext, task_id: str,
                             attempt_id: str) -> list[Event]:
        project, cfg, states = ctx.project, ctx.cfg, ctx.states
        """F018 P3a/P3c: the role==CARVER branch of EmitAttemptExit for a
        carver-session Start/Resume turn (see _execute's dispatch, which
        distinguishes this from a legacy CarveDispatch attempt by task_id
        prefix). plan §5.1 items 5-7 / §5.2: decide STARTED/RESUMED vs
        DEGRADED from the turn's ACTUAL outcome -- a non-empty captured
        session_handle AND a DONE receipt -- never from process exit alone.
        A capture/turn failure records CARVER_SESSION_DEGRADED (never
        STARTED/RESUMED); resume_failures itself is NOT computed here --
        the projector's own CARVER_SESSION_DEGRADED fold
        (`p.get("retry_count", snap.resume_failures + 1)`) already derives
        it correctly from the durable event log, so this never duplicates
        (and risks drifting from) that arithmetic. Either way, the synthetic
        turn task retires to SUPERSEDED -- freeing carve_in_flight for the
        next pass -- mirroring _consume_carve_exit's own shape.

        F018 P3c extension: for a WRITE-AUTHORITY turn (mode in
        _CARVE_WRITE_AUTHORITY_MODES -- today only "carve", produced by
        _execute_carve_via_session_resume's normalize branch), `ok` ALSO
        requires a structurally-valid CarverTurnResult envelope
        (_parse_carver_turn_result) -- a missing/malformed envelope on an
        otherwise-successful turn folds into the SAME DEGRADED path as a
        capture/turn failure (plan §4.1: 'invalid output ... does not
        create a task', never a half-proposal), so RESUMED and
        PROPOSAL_RECORDED are both skipped together, atomically. Read-only
        modes (merge-feed/targeted-intake/recover) never attempt this parse
        and so are byte-identical to P3a. CARVER_SESSION_RESUMED's payload
        also gains `mode`/`turn_id`/`source_ids` here (plan §2.2 fold-in --
        P3a shipped only {generation, route}). AD2 fix: a structurally-
        valid envelope with an EMPTY artifacts list ("carved nothing this
        turn") records RESUMED but NOT a proposal -- see the inline
        comment at the CARVER_PROPOSAL_RECORDED emission below."""
        events: list[Event] = []
        tsf = states[task_id]
        attempt = tsf.attempt_by_id(attempt_id)
        kind, mode, generation, source_ids = self._carver_turn_marker(states, task_id)

        session_handle = attempt.session_handle if attempt is not None else None
        receipt_ok = (attempt is not None and attempt.receipt is not None
                     and attempt.receipt.result is ReceiptResult.DONE)
        ok = receipt_ok and bool(session_handle)

        # F018 P3d (concern-3 #3): for a bootstrap (kind=start) or recover-
        # mode resume turn, ok ALSO requires a valid BOOTSTRAP-ACK.json
        # echoing the spine revisions the packet supplied. A missing/
        # malformed/mismatched ACK folds into the SAME DEGRADED path as a
        # bad envelope, with reason "bootstrap-ack-invalid".
        bootstrap_or_recover = ok and (
            kind == "start" or (kind == "resume" and mode == "recover"))
        ack_invalid = False
        if bootstrap_or_recover:
            ack_path = self._carver_bootstrap_ack_path(cfg, attempt)
            ack_valid = False
            if ack_path.exists():
                try:
                    ack_data = json.loads(ack_path.read_text(encoding="utf-8"))
                    if (isinstance(ack_data, dict)
                            and ack_data.get("kind") == "bootstrap-ack"):
                        ack_spine = ack_data.get("spine_revisions", {})
                        if (isinstance(ack_spine, dict)
                                and ack_spine == self._spine_revisions(cfg)):
                            ack_valid = True
                except (OSError, json.JSONDecodeError):
                    pass
            if not ack_valid:
                ok = False
                ack_invalid = True

        turn_result: carver_session.CarverTurnResult | None = None
        write_authority_turn = ok and kind == "resume" and mode in _CARVE_WRITE_AUTHORITY_MODES
        if write_authority_turn:
            turn_result = self._parse_carver_turn_result(cfg, attempt, task_id)
            if turn_result is None:
                ok = False  # malformed/missing envelope -> degraded, never a half-proposal

        if not ok:
            if ack_invalid:
                reason = "bootstrap-ack-invalid"
            elif not session_handle:
                reason = "capture-failed"
            elif not receipt_ok:
                reason = "turn-failed"
            else:
                reason = "proposal-invalid"
            events.append(self._append_ev(
                project, cfg, states, EventType.CARVER_SESSION_DEGRADED,
                {"reason": reason, "kind": kind, "mode": mode},
                task_id=task_id, attempt_id=attempt_id))
        elif kind == "start":
            events.append(self._append_ev(
                project, cfg, states, EventType.CARVER_SESSION_STARTED,
                {"generation": generation, "session_id": session_handle,
                 "route": attempt.route.to_dict(), "spine_revisions": self._spine_revisions(cfg)},
                task_id=task_id, attempt_id=attempt_id))
        else:
            events.append(self._append_ev(
                project, cfg, states, EventType.CARVER_SESSION_RESUMED,
                {"generation": generation, "route": attempt.route.to_dict(),
                 "mode": mode, "turn_id": task_id, "source_ids": list(source_ids)},
                task_id=task_id, attempt_id=attempt_id))
            # F018 P4a: acknowledge merge-feed consumption so the cursor
            # advances and consumed feeds stop re-firing every pass. Only
            # for a merge-feed turn; other resume modes (recover/
            # targeted-intake) do not advance the merge-feed cursor. A
            # failed turn is caught by `not ok` above and never reaches
            # this branch.
            if mode == "merge-feed" and source_ids:
                highest_seq = self._highest_consumed_feed_sequence(
                    project, source_ids)
                if highest_seq is not None:
                    events.append(self._append_ev(
                        project, cfg, states, EventType.CARVER_CONTEXT_CONSUMED,
                        {"highest_event_sequence": highest_seq,
                         "spine_revisions": self._spine_revisions(cfg)},
                        task_id=task_id, attempt_id=attempt_id))
            if turn_result is not None and turn_result.artifacts:
                # F018 P3c (plan §2.2/§4.1): the audit-only, admission-
                # untouched record of what this write-authority turn
                # authored. Payload is the envelope's OWN fields verbatim --
                # the exact shape P3b's _validate_carve_proposal_payload
                # already consumes (proposal_id/turn_id/source/artifacts/
                # outcome), so no separate/drifting payload builder exists.
                #
                # AD2 fix (2026-07-25 adversarial review): a STRUCTURALLY
                # valid envelope with an EMPTY artifacts list ("carved
                # nothing this turn" -- explicitly authorized by the carve
                # prompt, mirroring the legacy contract's own 'carved: []'
                # allowance) is a legitimate no-op, not a proposal. P3b's
                # _validate_carve_proposal_payload rejects an empty-
                # artifacts payload outright (`if not artifacts: return
                # None`) and counts every rejection toward the bounded
                # repair budget -- recording one here for a healthy idle
                # carver (e.g. outcome ROADMAP_EXHAUSTED with nothing left
                # to carve) would spuriously burn that budget and
                # eventually escalate NEEDS_OPERATOR{carver-proposal-
                # invalid} for a turn that did exactly what was asked.
                # RESUMED above already recorded the turn; skipping here
                # simply means no proposal audit trail for a turn that
                # produced no proposal.
                events.append(self._append_ev(
                    project, cfg, states, EventType.CARVER_PROPOSAL_RECORDED,
                    turn_result.to_dict(), task_id=task_id, attempt_id=attempt_id))

        events.append(self._append_ev(
            project, cfg, states, EventType.TASK_SUPERSEDED,
            {"from": states[task_id].state.value, "notes": "carver-session-consumed"},
            task_id=task_id))
        return events


def specs(effector: CarverEffector) -> tuple[effects.HandlerSpec, ...]:
    return (
        effects.HandlerSpec(
            action_type=reconcile.StartCarverSession,
            kind="start-carver-session",
            handler=effector.start_session,
            emits=frozenset({EventType.NEEDS_OPERATOR, EventType.TASK_CREATED,
                             EventType.ATTEMPT_CREATED,
                             EventType.ATTEMPT_PREFLIGHTED}),
            idempotency_key=lambda a: f"start-carver-session:{a.mode}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.ResumeCarverSession,
            kind="resume-carver-session",
            handler=effector.resume_session,
            emits=frozenset({EventType.NEEDS_OPERATOR, EventType.TASK_CREATED,
                             EventType.ATTEMPT_CREATED,
                             EventType.ATTEMPT_PREFLIGHTED}),
            idempotency_key=lambda a: f"resume-carver-session:{a.mode}",
        ),
        effects.HandlerSpec(
            action_type=reconcile.CompactCarverSession,
            kind="compact-carver-session",
            handler=effector.compact_session,
            emits=frozenset({EventType.CARVER_SESSION_ROTATED,
                             EventType.NEEDS_OPERATOR}),
            idempotency_key=lambda a: f"compact-carver-session:{a.trigger}",
        ),
    )
