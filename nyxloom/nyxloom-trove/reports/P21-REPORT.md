# P21 — review integrity: git state is truth, receipts lie — Implementation Report

**Status:** done · **Date:** 2026-07-16

## Summary

Implements all four changes from `handoff/P21-review-integrity-git-truth.md`.

1. **Review packet captures uncommitted worktree state.** In `daemon.py`'s
   `LaunchReview` handler (`_execute`, `reconcile.LaunchReview` branch), the
   per-task packet section now writes a `### COMMITTED (<default>...<branch>)`
   heading (the pre-existing `git diff <default>...feat/<task>` stat, now
   labeled) followed by a `### UNCOMMITTED (worktree — may be lost on
   teardown; REVIEW IT)` heading. The worktree path is derived the same way
   `DispatchImplementer` does it (`cfg.root / cfg.worktree_root /
   f"feat/{task}"`). If the worktree exists, it runs `git status --porcelain`,
   `git diff` (unstaged), and `git diff --cached` (staged) inside it and
   writes all three (or "clean: no uncommitted changes" if all three are
   empty). If the worktree is absent (already torn down), it writes an
   explicit absent-note instead of silently omitting the section.
2. **Reviewer role text gained a git-truth clause.** A new numbered step
   ("2.") in the packet's role-instructions preamble tells the reviewer to
   run `git log <default>..feat/<task>` and `git status` in the worktree,
   NOT to trust the receipt's `head_commit` / `files_touched` / `oracles`
   fields (observed null/empty even when real work was committed — the live
   P93 lesson), and to review uncommitted changes too. The following steps
   were renumbered (3-7); no content besides the numbering changed.
3. **Receipt consumption cross-checks the branch.** New private method
   `Daemon._crosscheck_head_commit(cfg, task_id, receipt)`, called
   immediately after `receipt = Receipt.from_dict(receipt_data)` in the
   `EmitAttemptExit` handler (before the receipt is used or logged anywhere
   downstream, i.e. before the `FRONTIER_REVIEW`/`DONE`/`BLOCKED`/etc.
   branches). If `receipt.head_commit` is already truthy, it is trusted
   as-is (no override). If null/empty, it runs `git rev-parse --verify
   feat/<task_id>` and `git rev-parse --verify <cfg.default_branch>` in
   `cfg.root` (read-only — no writes to the branch); if the branch exists
   and its SHA differs from default's, that SHA is written into
   `receipt.head_commit` (mutating the same object that flows into
   `attempt.receipt` and the `ATTEMPT_EXITED` event payload). A branch that
   doesn't exist, or exists but has no commits ahead of default, leaves
   `head_commit` as `None`.
4. **Reframed the implementer prompt** (`adapters.py`, `build_dispatch`).
   Replaced "uncommitted work is discarded" with: "You MUST `git add` and
   `git commit` ALL your work on the branch before finishing. Uncommitted
   work will be surfaced to review but risks loss on worktree teardown —
   committing is required for a clean review." Commit pressure is kept;
   the false "discarded" claim is gone (uncommitted work is now genuinely
   surfaced to review per change 1).

Scoped gate (`tests/test_daemon.py tests/test_adapters.py`): 84 passed.
Full suite: 376 passed, 0 failed (baseline was green at 376 before this
change too — no test count regression, only additions).

## Oracle Results

| # | Oracle (from handoff) | Status | Notes |
|---|---|---|---|
| 1 | A review packet for a task whose worktree has an uncommitted change contains that change under an "UNCOMMITTED" heading in `packet.md` | **PASS** | `test_daemon.py::test_launch_review_packet_captures_uncommitted_worktree` — asserts `### COMMITTED`, `### UNCOMMITTED`, the literal uncommitted marker line, and the absent-worktree note for a torn-down task, all present in `packet.md` |
| 2 | The reviewer role text in `packet.md` contains the git-truth / don't-trust-the-receipt clause | **PASS** | `test_daemon.py::test_launch_review_packet_reviewer_text_has_git_truth_clause` — asserts the "git state is truth, receipts [lie]" phrase, `git log`/`git status` mentions, "Do NOT trust the receipt's", and the `head_commit`/`files_touched`/`oracles` field names all appear |
| 3 | A done-receipt with `head_commit=null` on a branch with a real commit ahead of default results in the REAL commit being recorded; a branch with NO commits ahead still records null/none | **PASS** | `test_daemon.py::test_emit_attempt_exit_head_commit_crosscheck_branch_ahead` (real commit recorded, verified against an independent `git rev-parse` in the test) and `..._no_commits_ahead` (branch exists, no divergence -> `head_commit is None`); added `..._receipt_trusted_when_present` as a bonus positive-trust case (a receipt that already reports a commit is never overridden) |
| 4 | The implementer dispatch prompt no longer contains "uncommitted work is discarded"; it contains the reframed commit instruction | **PASS** | `test_adapters.py::test_build_dispatch_prompt_commit_instruction_is_truthful` — asserts the literal string is absent and `git commit` + "surfaced to review" are present |
| 5 | Full suite green | **PASS** | 376 passed, 0 failed (see Gate Output below) |

## Files Touched

- `src/nyxloom/daemon.py` —
  - `LaunchReview` branch (`_execute`): reviewer role-text preamble gained
    the git-truth clause (renumbered steps 2-7); per-task loop gained the
    `### COMMITTED`/`### UNCOMMITTED` headings and the worktree
    status/diff/diff-cached capture (or absent-note).
  - `EmitAttemptExit` branch: one new call to `self._crosscheck_head_commit(
    cfg, task_id, receipt)` right after the receipt is loaded from disk.
  - New method `Daemon._crosscheck_head_commit` (placed next to
    `_ensure_worktree`, using the same `subprocess`/`cfg.root`/
    `cfg.default_branch` conventions already in this class).
- `src/nyxloom/adapters.py` — `build_dispatch`'s prompt text: replaced
  the "uncommitted work is discarded" sentence with the reframed,
  truthful-but-firm commit instruction; added a short 2026-07-16 comment
  explaining why (P21, live P93 lesson).
- `tests/test_daemon.py` — added:
  - `test_emit_attempt_exit_head_commit_crosscheck_branch_ahead`
  - `test_emit_attempt_exit_head_commit_crosscheck_no_commits_ahead`
  - `test_emit_attempt_exit_head_commit_receipt_trusted_when_present`
  - `test_launch_review_packet_captures_uncommitted_worktree`
  - `test_launch_review_packet_reviewer_text_has_git_truth_clause`
  (no existing tests modified; `_make_feature_branch`/`_seed_running_attempt`/
  `_write_receipt`/`_seed_task` helpers reused as-is.)
- `tests/test_adapters.py` — added
  `test_build_dispatch_prompt_commit_instruction_is_truthful`.
- `handoff/reports/P21-REPORT.md` — this report.

No changes to `storage.py`, `types.py`, `reconcile.py`, or the transition
apply path (P20's scope) — all untouched, per the handoff's ownership
boundary.

## Gate Output (tail)

Command: `cd /workspaces/vbpub/.worktrees/nyxloom-P21/nyxloom && PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -m pytest tests/ -q`

```
........................................................................ [ 19%]
........................................................................ [ 38%]
........................................................................ [ 57%]
........................................................................ [ 76%]
........................................................................ [ 95%]
................                                                         [100%]
```

Exit code: 0. 376 tests collected post-change (per `pytest --collect-only
-q`, module totals summed: adapters.py 50, cli.py 36, commands.py 19,
config_ui.py 14, crash.py 5, daemon.py 34, decisions.py 23, doctor.py 16,
frontmatter.py 19, integration.py 2, lint.py 37, notify.py 23,
properties.py 16, reconcile.py 48, render.py 20, wrapper.py 14) — this
includes the 6 tests added by this change (5 in `test_daemon.py`, 1 in
`test_adapters.py`; pre-change baseline was 370). This pytest/pyproject
config does not print a final "N passed in Ys" summary line even with
`-rA`; absence of any `F`/`E`/`s` character in the dot stream plus exit
code 0 is the evidence of a fully green run (also spot checked
module-scoped: `pytest tests/test_daemon.py tests/test_adapters.py -q` ->
same all-dots, exit 0, 84 passed by count).

## Deviations / Assumptions

- **Scope of the head_commit crosscheck:** the handoff names the patch
  point as "daemon ~994-1000" which in the pre-change file is the
  `FRONTIER_REVIEW` / `REVIEW_RECORDED` block, immediately followed by the
  implementer `DONE`/`BLOCKED`/etc. mapping. Rather than duplicating the
  crosscheck in both branches, it is called once, right after the receipt
  is parsed from disk and before either branch runs — this covers both the
  `FRONTIER_REVIEW` receipt (whose `task_id` is the wave's `first_task`,
  which does have its own `feat/<task_id>` branch) and the implementer
  `DONE` receipt uniformly, with no duplicated git subprocess calls.
- **Renumbering the reviewer instructions:** inserting the new git-truth
  clause as step "2" pushed the pre-existing steps 2-6 to 3-7. This is a
  cosmetic renumbering of packet.md's literal instructional text only — no
  step's content or meaning changed, and no test (existing or new) asserts
  on the numeric prefixes, only on substring content.
- **"ahead" comparison implementation:** "the branch is ahead" is
  implemented as `git rev-parse --verify` SHA inequality between
  `feat/<task>` and `cfg.default_branch` (not a full `git merge-base
  --is-ancestor` ahead/behind check) — sufficient for this repo's
  feature-branch convention (branches are always created from default and
  only ever gain commits forward from it; they are never rebased backward
  by this system) and keeps the crosscheck a single cheap pair of
  read-only `rev-parse` calls, matching the "keep it defensive and
  read-only" instruction.
- No other deviations. All four owned-path restrictions honored (only
  `daemon.py`, `adapters.py`, `test_daemon.py`, `test_adapters.py`, and
  this report were touched); no git write commands were run against the
  branch by the implementation code (only `rev-parse`/`diff`/`status`,
  all read-only); no daemon/docker/ciu commands were run during
  implementation.
