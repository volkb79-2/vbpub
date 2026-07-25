---
schema_version: 1
id: topos-P103-query-engine-coverage
project: topos
title: "Complete query engine projection and execution coverage"
tier: sonnet5-high
input_revision: "f76f6731"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P102-REPORT.md"}
stack: none
depends_on: [topos-P102-query-validation-coverage]
session: "resume:topos-coverage"
scope:
  touch:
    - "src/topos/query/engine.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P103-query-engine-coverage.md"
    - "nyxloom-trove/reports/P103-*.md"
  forbid:
    - "src/topos/query/semantics.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "query/engine.py has empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 17-line/19-pair residual and the whole file"
    negative: "completion is claimed from aggregate, serial, focused, rounded, partial residual closure, or only the P103 subset without whole-file emptiness"
    gate: topos-suite
  - id: O2
    observable: "tests drive real frame sources and Query values and assert exact slice traversal, JSON formatting, visibility filtering, missing-cell ranking, hierarchy ordering/subtrees, combined truncation metadata, empty-current output, and raw-series inclusion/skips"
    negative: "tests mock the function under test, contain pass or assertion-free bodies, assert only non-None/ranges/calls, swallow errors, or weaken projection/cap behavior"
    gate: topos-suite
  - id: O3
    observable: "the receipt binds the literal 17 lines and 19 pairs to exact-commit nl -ba source and concrete inputs, prints empty literal intersections and whole-file missing sets for two runs, and distinguishes functions from collected cases"
    negative: "line/arc identities or collection baselines are guessed, counts replace literal sets, or mutation/fail-before claims lack receipts"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass with identical complete engine.py executed/missing sets and whole-file emptiness"
    negative: "serial-only evidence, xdist drift, or a green suite with any engine.py statement or branch missing is accepted"
    gate: topos-suite
  - id: O5
    observable: "every retained test has exact behavioral assertions; no pragma, omit, evaluator, gate, dependency, sleep, global leak, or nondeterministic host-state reliance is introduced"
    negative: "coverage is raised with hollow/duplicate tests, private-state leakage, or scope weakening"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a residual branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires source outside query/engine.py"
  - "either clean full-gate run fails or engine.py executed/missing sets differ"
advances: []
---

# P103 — Complete query engine projection and execution coverage

## Goal

Close the complementary projection/execution/cap tranche and bring all of
`query/engine.py` to exact 100% statement and branch coverage. P102 already
closed the validation tranche.

Assigned worktree:
`/workspaces/vbpub/.worktrees/feat/topos-P103-query-engine-coverage`.

## Literal residual

The full-gate JSON must close these 17 remaining lines:

```text
397 398 477 581 597 660 661 662 754 784 855 858 860 862 863 866 882
```

and these 19 remaining branch pairs:

```text
392->398 395->397 429->427 476->477 580->581
596->597 659->660 661->662 661->665 675->684
753->754 783->784 854->855 857->858 859->860
861->862 865->866 869->847 881->882
```

Completion additionally requires `missing_lines=[]` and
`missing_branches=[]` for the entire `query/engine.py` record.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and seven
   validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, especially PL3 and
   living PL4 through P102.
5. `topos/nyxloom-trove/reports/P102-REPORT.md` and `P102-REVIEW.md`, only the
   final residual and literal-evidence discipline.
6. `topos/tests/test_query.py`, P102 query tests, and `query/engine.py` from
   `_in_slice` through `_finish`.

## Work

1. Relocation preflight: print `pwd`, repository root, status, and HEAD.
   Treat every earlier P96–P102 worktree as stale and forbidden.
2. Run the full branch-aware gate and mechanically confirm the literal
   17-line/19-pair residual plus the complete engine missing sets.
3. Bind every line/pair to exact-commit `nl -ba`, a real frame/query input,
   and an exact public result. Direct narrow helper tests are allowed only for
   stable outputs such as cycle-safe slice membership and missing-cell stats.
4. Exercise cycle/not-found slice traversal, pretty JSON, available-only
   summary filtering, missing sort cells, unsorted hierarchy children, sorted
   subtree metadata, pre-existing plus byte truncation, empty current windows,
   absent entities/metrics, hidden visibility, point caps, raw values, loop
   fallthrough, and simultaneous row/point truncation.
5. Zero `pass` bodies, assertion-free calls, weak ranges, non-None-only
   assertions, function-under-test mocks, or duplicate cases.
6. Use bind-mounted `tester-unified:local`; never rebuild it.
7. Run the full xdist gate twice. On both JSON files require the literal P103
   intersections empty AND the complete engine.py `missing_lines` and
   `missing_branches` empty; compare complete executed/missing sets for parity.
8. Separately collect test function count and pytest case delta against the
   verified P102 total of 2002. Run `git diff --check`.
9. Write `P103-LOG.md`, `P103-REPORT.md`, and `P103-SELFREVIEW.md` with literal
   before/after sets, whole-file final sets, exact commands/exits, truthful
   negative evidence, collection arithmetic, assertion audit, and parity.
10. Commit, adversarially self-review, repair findings, rerun verification,
    and commit the final state.

## Gate

Use the exact `[gates.topos-suite]` argv. Focused/serial runs are diagnostic.
Completion requires two complete parallel runs, the literal residual checker,
and the whole-file engine checker all exiting zero.

## Scope / forbid

Only frontmatter touch paths may change. Do not alter gate/tooling/dependencies
or `query/semantics.py`. No coverage pragmas or omissions. Target source edits
require a regression-proven defect or independently demonstrable dead path.

## BLOCKED

On a mechanical `escalate_if`, stop and write
`BLOCKED: <trigger and exact evidence>` to `P103-LOG.md`. Never return a
partial `done`.
