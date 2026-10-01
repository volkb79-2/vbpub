---
schema_version: 1
id: ciu-P28-hotfix-worktree-branches-prune-safety
project: ciu
component: worktree
title: "HOTFIX (jumps the queue ahead of P20-P27 per operator priority decision): worktree branches -y can destroy a live managed CIU instance without cleaning it, judge branch mergedness against the wrong HEAD when invoked from a linked worktree, self-destruct mid-prune with no returned document, and report exit 0 on a partial --json result — four confirmed defects in already-released (v6.3.0/v6.4.0) code, found by two independent retrospective adversarial reviews"
tier: implement-3
input_revision: "d8b627cb"
source: {kind: retrospective-review, ref: "ciu-retrospective-review-findings.md, backlog-wave BLOCKING-1/2/3 + HIGH-6, worktree-identity-wave BLOCKING + MEDIUM (json-exit)"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/worktree.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_worktree_branches.py"
    - "docs/SPEC.md"
    - "docs/CONSUMERS.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P28-hotfix-worktree-branches-prune-safety-LOG.md"
  forbid:
    - "src/ciu/deploy.py"
    - "src/ciu/engine.py"
    - "src/ciu/config_model.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-no-clean-before-remove
    observable: "A branch/checkout carrying a MANAGED CIU instance record (i.e. `branch_hygiene`'s already-computed `ciu_instance` field is non-null, at whatever state — the record having been loaded is the point) is NEVER removed by bare `git worktree remove` in `prune_branches`. Instead: the branch/checkout is either (a) excluded from the `prunable` category entirely and reported as its own category (recommend a new category, e.g. `managed-instance`, closed vocabulary, never acted on by `-y`), forcing the operator to `ciu worktree rm` it explicitly, OR (b) routed through the existing `remove()` function (clean-then-remove, the same path `ciu worktree rm` uses) so cleanup actually happens before the checkout disappears. Pick ONE approach and apply it consistently; state your choice and why in the LOG (this is a real, bounded design choice within the fix, not something to leave ambiguous — (a) is safer/simpler, (b) preserves more automation value; either is acceptable, but the choice must be deliberate)."
    negative: "any code path where a branch with a non-null ciu_instance record reaches a bare `git worktree remove` call with no clean; a fix that merely adds a warning but still destroys the checkout"
    gate: "tester-unified"
  - id: O2-correct-head-for-mergedness
    observable: "The merge-safety check (`_prune_base_sanity` and the actual `git branch -d` invocation) is evaluated against the PRIMARY worktree's HEAD, never the invoking checkout's HEAD, regardless of which checkout `ciu worktree branches` is run from. Reproduce the finding's exact scenario as a test: a linked worktree whose OWN HEAD is behind main, with OTHER branches that ARE fully merged into main (but not into the invoking worktree's HEAD) — those other branches must be correctly identified as prunable and pruned, and the invoking worktree's own state must not corrupt that judgement."
    negative: "a fix that only prevents the invoking worktree's OWN branch from being mis-pruned (that's O4's job) without fixing the underlying wrong-HEAD comparison for OTHER branches too"
    gate: "tester-unified"
  - id: O3-no-self-destruct-mid-prune
    observable: "A `-y` prune invoked from a checkout whose OWN branch is (correctly) prunable does not destroy that checkout — either it's excluded from THIS invocation's destructive pass entirely (classified as `current`-equivalent: never a candidate this run, regardless of which worktree happens to be primary), or the operation refuses upfront naming the conflict, before any git mutation runs. Whichever you choose, the failure mode from the review (an unhandled `WorktreeError` mid-loop, no document returned, remaining prunable branches never processed) must not be reachable: a self-referential prune target must be handled as a NAMED, reported outcome, never an unhandled exception that aborts the whole operation."
    negative: "an exception escaping prune_branches under any input; remaining branches silently un-processed because an earlier one in the loop crashed"
    gate: "tester-unified"
  - id: O4-json-exit-code
    observable: "`ciu worktree branches -y --json`'s exit code matches the human-output path exactly: `status == \"partial\"` -> exit 1, `status == \"pruned\"`/clean survey -> exit 0, in EVERY output mode. Locate the exact code path where this check currently lives only inside the human/else branch (cli.py) and hoist it above the output-format branch so both paths share one exit-code decision."
    negative: "a fix that duplicates the partial-check logic into both branches separately (invites future drift — same species as this bug) instead of sharing one decision point"
    gate: "tester-unified"
  - id: O5-tests
    observable: "New tests reproducing each of O1-O4 end-to-end (not unit-testing an internal helper in isolation) using this codebase's existing fake-git-repo test fixtures for `test_ciu_worktree_branches.py` (grep the file for its existing scratch-repo helper and reuse it, do not invent a new one): (a) a managed instance record present on a prunable branch survives `-y` uncleaned OR is cleaned-then-removed, per your O1 choice; (b) a linked worktree behind HEAD does not prevent OTHER genuinely-merged branches from being correctly pruned; (c) invoking `-y` from a checkout whose own branch is prunable does not crash and does not silently drop it; (d) `--json -y` on a partial result exits 1, verified by literally checking the subprocess/CLI exit code, not just the JSON body's `status` field."
    negative: "a test that only checks the returned document's fields without also checking the actual process exit code for O4; a test that mocks git calls so thoroughly it no longer exercises the actual comparison logic being fixed"
    gate: "tester-unified"
  - id: O6-docs
    observable: "docs/SPEC.md's S16.8 section (worktree branches) is corrected to accurately describe the NEW safety guarantees (no longer claims something the old code didn't actually do, if it previously overclaimed). docs/CONSUMERS.md's worked example (search for 'gated twice so it can never half-prune' or similar language flagged by the review as now false) is corrected to describe the ACTUAL guarantee. CHANGES.md gets an Unreleased/Fixed entry describing this as a hotfix for already-released behavior (name the affected versions if you can determine them from CHANGES.md's own history — v6.3.0/v6.4.0 per the retrospective review). `KNOWN_ISSUES_TODO_BACKLOG.md`'s CIU-25 entry (the git-half prune feature) gets a note pointing at this hotfix."
    negative: "leaving CONSUMERS.md's false 'can never half-prune' claim in place"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "the O1 choice between 'exclude from prunable' and 'route through remove()' turns out to have a third, clearly-better option this handoff didn't anticipate — not a blocker, just document your reasoning for the actual choice made in the LOG"
  - "fixing O2 (correct-HEAD mergedness) requires threading the primary-worktree path through more of the call chain than expected, in a way that would touch a forbidden file — BLOCKED naming the exact chain, do not touch deploy.py/engine.py/config_model.py to work around it"
mutexes: [merge-lane]
review_focus:
  - "reproduce the ORIGINAL retrospective review's exact scenario (a scratch repo with main/feat1/feat2 merged, a linked worktree on an unmerged branch, invoking from that linked worktree) and confirm the fix actually closes it — this is not a hypothetical, it was reproduced end-to-end by the review, reproduce the FIX end-to-end the same way"
  - "confirm the self-destruct scenario specifically: -y from a checkout whose own branch is prunable, with OTHER prunable branches alphabetically after it in sort order — confirm those others still get processed correctly, not silently dropped by an early abort"
  - "the --json exit-code fix must not regress the ALREADY-PASSING human-output exit-code test — run it explicitly, don't just trust the full suite's green"
---

# ciu-P28 — hotfix: `worktree branches -y` unsafe prune

**This package jumps the dispatch queue ahead of ciu-P20 through ciu-P27** (QOL-13
hook templates and the V8-PREP items), which remain carved and queued but are
DEFERRED per an explicit operator priority decision: two independent retrospective
adversarial reviews of ciu's last major already-merged/released wave (see
`nyxloom-trove/handoffs/` is this SAME worktree, but the reviews themselves ran in
separate throwaway worktrees off `main` — `.worktrees/review-ciu-backlogwave` and
`.worktrees/review-ciu-worktree-wave`, both still present as of this carve if you
want to re-read their reproduction scripts) found real, reproducible data-loss and
silent-wrong-report defects in `ciu worktree branches -y`, already shipped in
v6.3.0/v6.4.0. This is a hotfix, not new feature work.

## Context to read first

1. Read the FULL review text this handoff distills — it is saved verbatim in this
   session's memory as `ciu-retrospective-review-findings.md` (you may not have
   direct access to that file from inside this worktree; the frontmatter oracles
   above and this section already extract everything load-bearing from it. If you
   want the reviewers' own reproduction scripts, check
   `.worktrees/review-ciu-backlogwave/ciu` and `.worktrees/review-ciu-worktree-wave/ciu`
   for scratch/probe scripts, if still present).
2. `src/ciu/worktree.py`: read `branch_hygiene` (~854-1058) and `prune_branches`
   (~1061-1136) in FULL, alongside `remove()` (~1965-2015, the clean-then-remove
   function `ciu worktree rm` uses) and `_prune_base_sanity` (~897-930). This is the
   entire surface this hotfix touches.
3. `src/ciu/cli.py`: the `worktree branches` verb dispatch (search for it), especially
   where `--json` vs. human output diverge and where an exit code is decided.
4. `tests/tests/test_ciu_worktree_branches.py` in full — the existing fixture style
   (a real scratch git repo built per-test) and the ALREADY-EXISTING regression test
   for the partial-exit-code oracle (search for `_exits_nonzero_on_partial` or
   similar) — note per the review this test invokes WITHOUT `--json`, which is
   exactly why the JSON path's bug wasn't caught; you are fixing both the code and
   (implicitly, per O5) adding the missing JSON-mode coverage.

## Work

1. Fix O1 (no bare-remove of a managed instance) — pick and document your approach.
2. Fix O2 (mergedness judged against the correct HEAD).
3. Fix O3 (no self-destruct / unhandled exception mid-prune).
4. Fix O4 (JSON exit code matches human exit code).
5. Tests per O5, reproducing each scenario end-to-end.
6. Docs per O6.

## Environment setup

Worktree: `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`, branch
`feat/ciu-qol-v8prep-wave`, venv at `.venv/` (already has `tests/conftest.py`
scrubbing ambient identity env vars).

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

## BLOCKED rule

Per `escalate_if`. Write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P28-hotfix-worktree-branches-prune-safety-LOG.md`, commit,
exit.
