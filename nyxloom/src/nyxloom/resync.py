"""Ground-truth re-baseline — PROBE + DRY-RUN ONLY. PACKAGE RP01.

`docs/plan-state-integrity.md` Part B: nyxloom's statefiles only transition
via nyxloom's OWN actions, so a project advanced manually (or squash/CAS
merged, or a branch deleted post-merge) drifts from reality. `resync`
compares each task's *believed* state against three ground-truth sources
(B.1) — handoff presence in the trove, merge state of its branch, and the
statefile's own belief — and proposes (never applies) a re-baseline action
per B.2's decision table.

RP01 is data-only: it produces a `list[ProposedTransition]`, never an
event, never a statefile write. `nyxloom resync <project>` prints the plan
as a table. Applying the plan (`--apply`, real `TASK_TRANSITIONED` /
`TASK_SUPERSEDED` events through `storage.append_and_apply`) is RP02, out
of scope here.

Two I/O boundaries feed the pure planner, mirroring reconcile.py's own
purity discipline (ReconcileInput as a precomputed snapshot):

  gather_handoff_presence(root, states)  -> dict[task_id, bool]
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

from . import frontmatter
from .types import TERMINAL_TASK_STATES, TaskState, TaskStateFile

# ---------------------------------------------------------------------------
# proposed-action vocabulary (B.2's decision-table outputs)

ACTION_NONE = "none"
ACTION_ADVANCE = "MERGED/COMPLETED"
ACTION_NEEDS_OPERATOR = "NEEDS_OPERATOR"


@dataclass(frozen=True)
class ProposedTransition:
    """One row of the resync plan. Never applied by RP01 — printed only."""

    task_id: str
    believed_state: TaskState
    ground_truth: str          # "terminal" | "merged" | "open" | "orphan"
    proposed_action: str       # ACTION_NONE | ACTION_ADVANCE | ACTION_NEEDS_OPERATOR
    evidence: str


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

def gather_handoff_presence(root: Path, states: dict[str, TaskStateFile]) -> dict[str, bool]:
    """task_id -> is its handoff still present + parseable in the trove?

    Mirrors render.py's `_load_frontmatter`: never raises. Absent
    `handoff_path`, a missing file, or a parse failure are ALL "not
    present" (the handoff is gone/archived from this task's point of
    view) — resync only cares about presence, not the frontmatter's
    content.
    """
    out: dict[str, bool] = {}
    for task_id, tsf in states.items():
        present = False
        if tsf.handoff_path:
            handoff_file = root / tsf.handoff_path
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

def _merge_evidence(task_id: str, tsf: TaskStateFile, git_facts: GitFacts) -> str | None:
    """None when not merged by either signal; else a human-readable
    evidence string naming WHICH signal fired."""
    candidates = _task_branch_candidates(task_id, tsf)
    hit = candidates & git_facts.merged_refs
    if hit:
        return f"branch {sorted(hit)[0]!r} present in `git branch --merged`"
    if task_id in git_facts.content_merged:
        return git_facts.content_merged[task_id]
    return None


def resync_plan(
    states: dict[str, TaskStateFile],
    frontmatters: dict[str, bool],
    git_facts: GitFacts,
) -> list[ProposedTransition]:
    """B.2's decision table, pure. Deterministic and I/O-free: called twice
    with identical inputs, yields an identical plan (list order is sorted
    task_id, so even list equality — not just set equality — holds).

    Precedence (case-by-case, first match wins):
      1. already TERMINAL (COMPLETED/SUPERSEDED/CANCELLED) -> no-op; ground
         truth is already settled regardless of any git signal.
      2. merged (either git_facts channel) -> propose MERGED/COMPLETED —
         B.2 rows 1 and 2 collapse into one case: ANY non-terminal believed
         state with a confirmed merge proposes the same advance.
      3. not merged, handoff still present -> no-op ("genuinely open" —
         B.2 row 5's counterpart: a statefile WITH its handoff is left to
         normal carve/dispatch, resync makes no claim).
      4. not merged, handoff gone -> NEEDS_OPERATOR ("orphan" / stale;
         B.2 rows 3 and 4 — never silently dropped).
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

        merge_evidence = _merge_evidence(task_id, tsf, git_facts)
        if merge_evidence is not None:
            out.append(ProposedTransition(
                task_id=task_id,
                believed_state=believed,
                ground_truth="merged",
                proposed_action=ACTION_ADVANCE,
                evidence=merge_evidence,
            ))
            continue

        handoff_present = frontmatters.get(task_id, False)
        if handoff_present:
            out.append(ProposedTransition(
                task_id=task_id,
                believed_state=believed,
                ground_truth="open",
                proposed_action=ACTION_NONE,
                evidence="handoff present in trove; no merge detected — genuinely open",
            ))
        else:
            out.append(ProposedTransition(
                task_id=task_id,
                believed_state=believed,
                ground_truth="orphan",
                proposed_action=ACTION_NEEDS_OPERATOR,
                evidence="handoff missing from trove and no merge detected "
                         "— flagged for operator, never silently dropped",
            ))
    return out
