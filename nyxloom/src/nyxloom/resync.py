"""Ground-truth re-baseline — PROBE + DRY-RUN (RP01) + APPLY (RP02).

`docs/plan-state-integrity.md` Part B: nyxloom's statefiles only transition
via nyxloom's OWN actions, so a project advanced manually (or squash/CAS
merged, or a branch deleted post-merge) drifts from reality. `resync`
compares each task's *believed* state against three ground-truth sources
(B.1) — handoff presence in the trove, merge state of its branch, and the
statefile's own belief — and proposes a re-baseline action per B.2's
decision table.

RP01 (`resync_plan`) is data-only: pure, I/O-free, produces a
`list[ProposedTransition]`, never an event, never a statefile write.
`nyxloom resync <project>` prints the plan as a table.

RP02 (`resync_apply`, below) turns the `ACTION_ADVANCE` rows of that same
plan into REAL audited events via `storage.append_and_apply` — never a
silent statefile edit (B.3). `ACTION_NONE` and `ACTION_NEEDS_OPERATOR` rows
are NEVER auto-applied (flag/skip only); an orphan or a genuinely-open task
always needs an operator's own judgment call, not resync's.

SAFETY (RP02, load-bearing): a merge-confirmed row's evidence comes from
one of two channels of very different reliability (`GitFacts.merged_refs`
vs `GitFacts.content_merged` — see that dataclass's own docstring). A
`content_merged`-only hit (the commit-log grep / archive-path scan) CAN
match an unrelated commit, so it is NEVER auto-applied by a bare
`--apply` — only a `merged_refs` hit (an actual `git branch --merged` ref)
auto-applies. The content-check channel applies only with the caller's
explicit extra opt-in (`allow_content_merge=True` / the CLI's
`--apply-content-merges`). See `ProposedTransition.merge_source` and
`resync_apply`'s docstring for the full contract.

Two I/O boundaries feed the pure planner, mirroring reconcile.py's own
purity discipline (ReconcileInput as a precomputed snapshot):

  gather_handoff_presence(cfg, states)   -> dict[task_id, bool]
  gather_git_facts(repo_root, branch, states) -> GitFacts

  resync_plan(states, frontmatters, git_facts) -> list[ProposedTransition]
      PURE. No filesystem, no subprocess, no clock — everything it needs
      arrives already gathered. Deterministic: identical inputs always
      yield an identical (order-stable) plan.

Merge detection (B.1's "more robust than `_merged_branches`" ask) is a
STANDALONE reimplementation here, not an import of daemon.py's
`Daemon._merged_branches` (a bound method on a live daemon instance) or an
edit to reconcile.py/daemon.py (both out of scope for this package). It
reproduces the existing `git branch --merged <default_branch>` check
(daemon.py:838-859: bare branch name, and for a `feat/`-prefixed branch
also the bare task-id token) and ADDS a content-check fallback for
anything `--merged` misses — a squash commit, or a merge whose source
branch was subsequently deleted:

  (a) a commit-log grep on the default branch for any commit message
      referencing the branch/task-id token (a squash commit's subject line
      conventionally carries the original branch/PR name; a deleted
      branch's merge commit message still lives in `main`'s own history);
  (b) an archive-directory content scan of the default branch's tree for
      any path containing both "archive" and the task_id (generalizes "the
      handoff's archived path under docs/archive" — CLAUDE.md's "Docs
      lifecycle on merge" convention — without assuming one fixed layout).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter, storage
from .config import ProjectConfig
from .types import (
    TERMINAL_TASK_STATES, Actor, ActorKind, Event, EventType, TaskState,
    TaskStateFile,
)

# ---------------------------------------------------------------------------
# proposed-action vocabulary (B.2's decision-table outputs)

ACTION_NONE = "none"
ACTION_ADVANCE = "MERGED/COMPLETED"
ACTION_NEEDS_OPERATOR = "NEEDS_OPERATOR"

# Merge-evidence source tags (RP02 SAFETY): which GitFacts channel produced
# the evidence backing an ACTION_ADVANCE row. MERGE_SOURCE_REFS is a real
# `git branch --merged` hit (high confidence); MERGE_SOURCE_CONTENT is the
# commit-log-grep / archive-path-scan fallback (lower confidence — it CAN
# match an unrelated commit), so it gates auto-apply differently. See
# `resync_apply`.
MERGE_SOURCE_REFS = "merged_refs"
MERGE_SOURCE_CONTENT = "content_merged"


@dataclass(frozen=True)
class ProposedTransition:
    """One row of the resync plan. Only `resync_apply` (RP02) turns an
    ACTION_ADVANCE row into a real event — never a silent statefile edit."""

    task_id: str
    believed_state: TaskState
    ground_truth: str          # "terminal" | "merged" | "open" | "orphan"
    proposed_action: str       # ACTION_NONE | ACTION_ADVANCE | ACTION_NEEDS_OPERATOR
    evidence: str
    # None for non-merge rows (open/orphan/terminal); for a "merged" row,
    # MERGE_SOURCE_REFS or MERGE_SOURCE_CONTENT — see the constants above.
    merge_source: str | None = None


@dataclass(frozen=True)
class GitFacts:
    """Ground-truth merge facts for one project, gathered ONCE per resync
    run by `gather_git_facts` (the I/O boundary) and consumed read-only by
    the pure `resync_plan`.

    `merged_refs`: every branch-name-shaped token `git branch --merged
    <default_branch>` reported, PLUS (mirroring daemon.py's own
    `_merged_branches`) the bare task-id for any `feat/<id>` entry.
    `content_merged`: task_id -> human-readable evidence string, populated
    ONLY for a task whose branch/id is NOT already in `merged_refs` — this
    is exactly the squash/CAS/deleted-branch case `--merged` alone misses.
    """

    merged_refs: frozenset[str] = frozenset()
    content_merged: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# I/O boundary #1 — handoff presence (filesystem + frontmatter parse)

def gather_handoff_presence(cfg: ProjectConfig, states: dict[str, TaskStateFile]) -> dict[str, bool]:
    """task_id -> is its handoff still present + parseable in the trove?

    Never raises (a parse failure or a missing file is simply "not
    present"). Two channels, EITHER sufficient:

      1. ID SCAN (authoritative, path-drift-proof): the set of frontmatter
         `id`s physically discoverable+parseable under the project's
         `handoff_globs` right now. A task whose statefile `handoff_path`
         is STALE — a pre-standardization or relocated path, e.g. topos's
         legacy `handoff/<id>.md` vs the current
         `nyxloom-trove/handoffs/<id>.md` — is STILL correctly "present"
         when a handoff carrying its id exists in the trove. Presence is a
         fact about the trove, not about a possibly-outdated path string in
         the statefile.
      2. HANDOFF_PATH fallback: an explicit `handoff_path` that resolves and
         parses — covers a handoff whose file id does not equal its task_id,
         or one living outside the active glob but still pointed-to.

    resync only cares about presence, not the frontmatter's content.
    """
    present_ids: set[str] = set()
    for handoff_file in frontmatter.discover_handoffs(cfg):
        try:
            fm, _ = frontmatter.parse_handoff(handoff_file)
            present_ids.add(fm.id)
        except Exception:
            continue

    out: dict[str, bool] = {}
    for task_id, tsf in states.items():
        present = task_id in present_ids
        if not present and tsf.handoff_path:
            handoff_file = cfg.root / tsf.handoff_path
            if handoff_file.exists():
                try:
                    frontmatter.parse_handoff(handoff_file)
                    present = True
                except Exception:
                    present = False
        out[task_id] = present
    return out


# ---------------------------------------------------------------------------
# I/O boundary #2 — git merge facts (subprocess only)

def _git(repo_root: str, args: list[str]) -> str:
    """Run one git subcommand; empty string (never raise) on any failure —
    a transient/absent-repo git hiccup must fail SAFE (treat as "not
    merged"), never crash the probe."""
    try:
        res = subprocess.run(
            ["git", "-C", repo_root, *args],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if res.returncode != 0:
        return ""
    return res.stdout


def _branch_merged_refs(repo_root: str, default_branch: str) -> frozenset[str]:
    """Reproduces daemon.py's `_merged_branches` git half exactly: every
    line of `git branch --merged <default_branch>`, plus the bare task-id
    token for a `feat/`-prefixed branch."""
    out: set[str] = set()
    for line in _git(repo_root, ["branch", "--merged", default_branch]).splitlines():
        name = line.strip().lstrip("*").strip()
        if name:
            out.add(name)
            if name.startswith("feat/"):
                out.add(name[len("feat/"):])
    return frozenset(out)


def _task_branch_candidates(task_id: str, tsf: TaskStateFile) -> set[str]:
    """Every branch-name-shaped token that could plausibly identify this
    task's work: the bare id, the conventional `feat/<id>`, and any real
    branch name any of its attempts recorded."""
    candidates = {task_id, f"feat/{task_id}"}
    for att in tsf.attempts:
        if att.branch:
            candidates.add(att.branch)
    return candidates


def _content_merge_evidence(repo_root: str, default_branch: str, task_id: str,
                             candidates: set[str]) -> str | None:
    """The hardened check `--merged` alone misses: a squash commit (subject
    line conventionally names the source branch/PR), a merge commit whose
    branch ref was since deleted (the commit message text survives in
    `main`'s history regardless), or an archived handoff path landing
    under a directory literally named "archive" on the default branch.
    Returns the first evidence string found, or None (genuinely no
    evidence of a merge)."""
    for candidate in sorted(candidates):
        log_out = _git(repo_root, ["log", default_branch, f"--grep={candidate}",
                                    "--fixed-strings", "--oneline"])
        first_line = next((ln for ln in log_out.splitlines() if ln.strip()), None)
        if first_line:
            return f"commit-log match for {candidate!r} on {default_branch}: {first_line.strip()}"

    for path in _git(repo_root, ["ls-tree", "-r", "--name-only", default_branch]).splitlines():
        path = path.strip()
        if "archive" in path.lower() and task_id in path:
            return f"archived path on {default_branch}: {path}"

    return None


def gather_git_facts(repo_root: str, default_branch: str,
                      states: dict[str, TaskStateFile]) -> GitFacts:
    """The single I/O pass resync needs: `--merged` once, then the content
    check ONLY for tasks it didn't already resolve (cheap — most projects
    have few drifted tasks)."""
    merged_refs = _branch_merged_refs(repo_root, default_branch)
    content_merged: dict[str, str] = {}
    for task_id, tsf in states.items():
        candidates = _task_branch_candidates(task_id, tsf)
        if candidates & merged_refs:
            continue  # already covered by --merged; no content check needed
        evidence = _content_merge_evidence(repo_root, default_branch, task_id, candidates)
        if evidence is not None:
            content_merged[task_id] = evidence
    return GitFacts(merged_refs=merged_refs, content_merged=content_merged)


# ---------------------------------------------------------------------------
# the pure planner (B.2's decision table)

def _refs_merge_evidence(task_id: str, tsf: TaskStateFile,
                         git_facts: GitFacts) -> str | None:
    """HIGH-confidence merge evidence ONLY: the task's branch (bare id,
    `feat/<id>`, or a recorded attempt branch) appears in `git branch
    --merged <default_branch>`. Returns the evidence string, or None.

    This is the ONE signal authoritative enough to outrank physical trove
    presence in `resync_plan`: a real merged ref means the work landed even
    if the handoff file still lingers un-archived. The LOWER-confidence
    content channel (`GitFacts.content_merged` — a commit-log grep / archive
    scan that CAN match an unrelated commit, most dangerously the carve
    commit that merely NAMES a task id when creating its handoff) is
    deliberately NOT consulted here; `resync_plan` consults it only AFTER
    confirming the handoff is gone from the trove."""
    candidates = _task_branch_candidates(task_id, tsf)
    hit = candidates & git_facts.merged_refs
    if hit:
        return f"branch {sorted(hit)[0]!r} present in `git branch --merged`"
    return None


def resync_plan(
    states: dict[str, TaskStateFile],
    frontmatters: dict[str, bool],
    git_facts: GitFacts,
) -> list[ProposedTransition]:
    """B.2's decision table, pure. Deterministic and I/O-free: called twice
    with identical inputs, yields an identical plan (list order is sorted
    task_id, so even list equality — not just set equality — holds).

    Precedence (case-by-case, first match wins). CONFIDENCE-ORDERED so the
    low-confidence content channel can never override physical trove
    presence (the dstdns-P31/P32 carve-commit false-positive fix):
      1. already TERMINAL (COMPLETED/SUPERSEDED/CANCELLED) -> no-op; ground
         truth is already settled regardless of any git signal.
      2. merged by a real `git branch --merged` REF (MERGE_SOURCE_REFS) ->
         propose MERGED/COMPLETED. Authoritative even if the handoff file
         still lingers un-archived.
      3. handoff still PRESENT+parseable in the trove -> no-op ("genuinely
         open"). Physical presence outranks the content channel: a loose
         commit-log/archive match (e.g. the carve commit that merely NAMES
         this id) must NOT retire a handoff that is still on disk.
      4. handoff GONE, but the CONTENT channel has evidence (squash /
         deleted-branch / archived-path, MERGE_SOURCE_CONTENT) -> propose
         MERGED/COMPLETED (still confidence-gated at apply time).
      5. handoff gone AND no merge evidence anywhere -> NEEDS_OPERATOR
         ("orphan" / stale — never silently dropped).
    """
    out: list[ProposedTransition] = []
    for task_id in sorted(states):
        tsf = states[task_id]
        believed = tsf.state

        if believed in TERMINAL_TASK_STATES:
            out.append(ProposedTransition(
                task_id=task_id,
                believed_state=believed,
                ground_truth="terminal",
                proposed_action=ACTION_NONE,
                evidence=f"already terminal ({believed.value}); no resync action",
            ))
            continue

        # (2) HIGH-CONFIDENCE merge: a real `git branch --merged` ref is
        # authoritative even if the handoff file still lingers in the trove.
        refs_evidence = _refs_merge_evidence(task_id, tsf, git_facts)
        if refs_evidence is not None:
            out.append(ProposedTransition(
                task_id=task_id,
                believed_state=believed,
                ground_truth="merged",
                proposed_action=ACTION_ADVANCE,
                evidence=refs_evidence,
                merge_source=MERGE_SOURCE_REFS,
            ))
            continue

        # (3) PHYSICAL PRESENCE outranks the LOW-confidence content channel:
        # a handoff still present+parseable in the active trove is
        # authoritatively OPEN. The content-check (commit-log grep / archive
        # scan) CAN match an unrelated commit — most dangerously the carve
        # commit that merely NAMES this task id when creating its handoff —
        # so it must never retire a task whose handoff is still on disk.
        handoff_present = frontmatters.get(task_id, False)
        if handoff_present:
            out.append(ProposedTransition(
                task_id=task_id,
                believed_state=believed,
                ground_truth="open",
                proposed_action=ACTION_NONE,
                evidence="handoff present in trove; no merge detected — genuinely open",
            ))
            continue

        # (4) handoff GONE: only now does the lower-confidence content channel
        # speak (squash / deleted-branch / archived-path). Its rows are still
        # confidence-gated at apply time — a MERGE_SOURCE_CONTENT row needs
        # the explicit --apply-content-merges opt-in (see `resync_apply`).
        content_evidence = git_facts.content_merged.get(task_id)
        if content_evidence is not None:
            out.append(ProposedTransition(
                task_id=task_id,
                believed_state=believed,
                ground_truth="merged",
                proposed_action=ACTION_ADVANCE,
                evidence=content_evidence,
                merge_source=MERGE_SOURCE_CONTENT,
            ))
            continue

        # (5) gone AND no merge evidence anywhere -> orphan (never dropped).
        out.append(ProposedTransition(
            task_id=task_id,
            believed_state=believed,
            ground_truth="orphan",
            proposed_action=ACTION_NEEDS_OPERATOR,
            evidence="handoff missing from trove and no merge detected "
                     "— flagged for operator, never silently dropped",
        ))
    return out


# ---------------------------------------------------------------------------
# RP02 — the apply layer (audited, never a silent statefile edit — B.3)

@dataclass(frozen=True)
class ApplyResult:
    """One outcome row per plan entry that `resync_apply` considered (every
    ACTION_ADVANCE / ACTION_NEEDS_OPERATOR row — ACTION_NONE rows carry
    nothing actionable and are skipped entirely, not even reported here).
    `event` is the real `Event` written to the log when `applied` is True,
    else None (nothing was written for this task)."""

    task_id: str
    applied: bool
    reason: str
    event: Event | None = None


def _legal_advance_transition(believed: TaskState) -> tuple[TaskState, EventType] | None:
    """The B.3 legal-transition map for a merge-confirmed ACTION_ADVANCE
    row — resync NEVER fabricates an illegal or multi-hop transition just
    to reach a nominal target; it only ever emits ONE real, machine-legal
    edge (see `types.TASK_TRANSITIONS`):

      MERGE_READY -- the ONLY state with a direct edge into MERGED (the
                      dstdns-P30 / ui-P10 case) -- emits TASK_TRANSITIONED
                      to MERGED.
      any OTHER non-terminal, non-MERGED believed state (CARVED/QUEUED/
      ACTIVE/AWAITING_REVIEW/SELF_REVIEWING/REVIEW_REJECTED/BLOCKED/DRAFT/
      NEEDS_DECISION/READY_TO_CARVE) -- MERGED has exactly one incoming
      edge (from MERGE_READY), so it is NOT directly reachable from any of
      these. Faking a multi-hop chain through the review/gate stages would
      manufacture events for review/gate work that never happened --
      corrupting the audit trail. Instead resync retires the task's OWN
      tracked lifecycle via TASK_SUPERSEDED, which is legal from every
      non-terminal state: the ground truth is that this task's real work
      already landed through a channel nyxloom wasn't tracking, so its
      *nyxloom-side* lifecycle is superseded by that reality.
      MERGED itself (already advanced by a prior --apply), or any state
      already in TERMINAL_TASK_STATES -- nothing further for resync to do
      -- returns None. This is the idempotency contract: a second --apply
      against an already-advanced task computes None here and therefore
      never calls `storage.append_and_apply` at all (no redundant event,
      not even a from==to no-op one).
    """
    if believed is TaskState.MERGE_READY:
        return TaskState.MERGED, EventType.TASK_TRANSITIONED
    if believed is TaskState.MERGED or believed in TERMINAL_TASK_STATES:
        return None
    return TaskState.SUPERSEDED, EventType.TASK_SUPERSEDED


def resync_apply(
    project: str,
    states: dict[str, TaskStateFile],
    plan: list[ProposedTransition],
    *,
    allow_content_merge: bool = False,
    actor_id: str = "resync",
) -> list[ApplyResult]:
    """Turn `plan`'s ACTION_ADVANCE rows into REAL audited events via
    `storage.append_and_apply` (B.3: never a silent statefile edit). Every
    event's actor is `Actor(ActorKind.RESYNC, actor_id)`; its payload names
    the ground-truth evidence (both `"reason"`, verbatim, and folded into
    `"notes"` for the normal statefile-notes projection).

    `ACTION_NONE` rows are skipped entirely — nothing to apply or flag.
    `ACTION_NEEDS_OPERATOR` rows are NEVER auto-applied — reported as an
    unapplied `ApplyResult` only, so the CLI can surface them, exactly per
    B.3 ("never silently dropped") and the RP02 contract ("never auto-
    applied": an operator's own judgment call, not resync's).

    SAFETY (RP02's load-bearing rule) — confidence gate on ACTION_ADVANCE:
    a row backed ONLY by the content-check channel
    (`merge_source == MERGE_SOURCE_CONTENT`, i.e. `GitFacts.content_merged`
    — a commit-log grep / archive-path scan that CAN match an unrelated
    commit) is LOWER-CONFIDENCE than a `git branch --merged` hit
    (`MERGE_SOURCE_REFS`). A `merged_refs`-backed row auto-applies under a
    bare `--apply`. A `content_merged`-only row is left untouched (flagged,
    not silently retired) UNLESS the caller passes `allow_content_merge=True`
    (the CLI's `--apply-content-merges`) — emitting a real
    TASK_TRANSITIONED/TASK_SUPERSEDED off a false-positive grep would
    wrongly retire a still-live task.

    Legal-transition mapping: see `_legal_advance_transition`. A row whose
    mapping is None (already MERGED or already terminal) is reported as an
    unapplied `ApplyResult` too — this IS the idempotency contract: a
    second `--apply` computes None for every already-advanced task and so
    performs zero new `append_and_apply` calls (nothing left drifted).
    """
    results: list[ApplyResult] = []
    actor = Actor(kind=ActorKind.RESYNC, id=actor_id)

    for row in plan:
        if row.proposed_action == ACTION_NONE:
            continue  # genuinely open or already terminal -- nothing to report

        if row.proposed_action == ACTION_NEEDS_OPERATOR:
            results.append(ApplyResult(
                task_id=row.task_id, applied=False,
                reason="NEEDS_OPERATOR rows are never auto-applied "
                       "(operator judgment required)",
            ))
            continue

        # ACTION_ADVANCE from here on.
        if row.merge_source == MERGE_SOURCE_CONTENT and not allow_content_merge:
            results.append(ApplyResult(
                task_id=row.task_id, applied=False,
                reason="content-check-only merge evidence requires the explicit "
                       "content-merge opt-in (--apply-content-merges) -- lower "
                       "confidence than a `git branch --merged` hit",
            ))
            continue

        mapping = _legal_advance_transition(row.believed_state)
        if mapping is None:
            results.append(ApplyResult(
                task_id=row.task_id, applied=False,
                reason="already MERGED or terminal -- nothing further for resync "
                       "(idempotent: no event written)",
            ))
            continue

        to_state, event_type = mapping
        if event_type is EventType.TASK_TRANSITIONED:
            payload = {
                "from": row.believed_state.value,
                "to": to_state.value,
                "reason": row.evidence,
                "notes": f"resync: {row.evidence}",
            }
        else:
            payload = {
                "from": row.believed_state.value,
                "reason": row.evidence,
                "notes": f"resync: {row.evidence}",
            }

        ev = storage.append_and_apply(
            project, states,
            actor=actor, type=event_type, payload=payload, task_id=row.task_id,
        )
        results.append(ApplyResult(task_id=row.task_id, applied=True,
                                    reason=row.evidence, event=ev))

    return results
