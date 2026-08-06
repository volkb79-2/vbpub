---
schema_version: 1
id: assay-P02-changed-lines-base-resolution-measurability
project: assay
title: "Changed-line extraction, base resolution, and the guards that refuse a vacuous verdict"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P01a-skeleton-and-lane-config]
session: fresh
scope:
  touch:
    - "src/assay/diff.py"
    - "src/assay/measurability.py"
    - "src/assay/git.py"
    - "tests/**"
  forbid:
    - "src/assay/verdict.py"
    - "../nyxloom/**"
oracles:
  - id: O1
    observable: "`parse_added_lines` on a `-U0` diff returns only NEW-side added line numbers: a pure deletion advances nothing, `+++ /dev/null` contributes no lines, a hunk header with an omitted count defaults to 1, and a `b/` prefix is stripped while its absence is tolerated"
    negative: "deleted lines are counted as changed, so a pure-deletion commit demands coverage of lines that no longer exist"
    gate: tester-unified
  - id: O2
    observable: "base resolution on a merge commit (HEAD with >=2 parents) returns the FIRST parent; on a normal tip it returns merge-base(base, HEAD) -- both proven against git repos materialised in tmp_path"
    negative: "a post-merge run diffs against the fork point instead of the merged delta, re-measuring work already gated"
    gate: tester-unified
  - id: O3
    observable: "uncommitted changes under a declared source root yield exit 3 / NO_MEASUREMENT / DIRTY_TREE, catching staged, unstaged AND untracked paths in one pass, with the affected paths named in the reason"
    negative: "a working-tree edit is invisible to the base..HEAD diff and renders as 0/0 -- 'the diff cannot see what is being tested' read as 'nothing changed'"
    gate: tester-unified
  - id: O4
    observable: "a resolved base identical to HEAD yields exit 3 / NO_MEASUREMENT / BASE_IS_HEAD"
    negative: "`--base main` resolving to main itself produces a delta-free 100% pass by construction"
    gate: tester-unified
  - id: O5
    observable: "a CLEAN tree with a genuinely empty delta trips NEITHER guard and reaches evaluation normally -- a docs-only commit still passes"
    negative: "the guards fire on a legitimate empty delta, making every docs commit unmergeable"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a guard cannot be tested without a real git binary in the gate image"
mutexes: []
---

# P02 — changed lines, base resolution, measurability

The claim to attack: **does it refuse to render a verdict it cannot justify?**

## Context to read first

1. `docs/DESIGN-GUIDE.md` §6 "Nailing NO MEASUREMENT" — the three causes and why
   two of them are not tree state.
2. `/workspaces/dstdns/scripts/coverage_gate.py` `parse_added_lines`,
   `_resolve_base`, `_dirty_paths_under_sources`, `_check_measurable` — the
   reference implementations. Read `/workspaces/vbpub/nyxloom/src/nyxloom/coverage_gate.py`
   `_dirty_paths_under_source` too: it routes status paths through the prefix
   normaliser because `git status --porcelain` always reports repo-top-level
   paths while `git diff --relative` does not. dstdns's copy omits that.

## What P01a already built for you

Read `nyxloom-trove/reports/assay-P01a-BRIEF.md` before starting — it is short
and it is written for you specifically. The load-bearing parts here:
`JudgeConfig.source_roots` is the DECLARED strings while `source_root_paths` is
resolved, existence-checked directories — **you want the latter**. `errors.py`
owns `Outcome`/`ReasonCode`/`EXIT_CODES`; import them, never redefine (A-066).
`tests/conftest.py` exports fixtures and the ACCEPT/REJECT test pattern this
series uses.

## Work

1. `src/assay/git.py` — thin subprocess boundary; a non-zero git exit raises,
   mapping to ERROR / GIT_FAILED.
2. `src/assay/diff.py` — `parse_added_lines`, taking the union of the four copies.
3. `src/assay/measurability.py` — DIRTY_TREE and BASE_IS_HEAD, checked BEFORE any
   diff is computed so the evaluation core stays pure and never sees either.

EMPTY_COVERAGE is the third NO_MEASUREMENT cause but belongs to P03, where the
coverage artifact is first read.

## Test fixtures

Git-state fixtures are `git init`'d into `tmp_path` at test time (A-042); do not
commit a git repo inside this repo.
