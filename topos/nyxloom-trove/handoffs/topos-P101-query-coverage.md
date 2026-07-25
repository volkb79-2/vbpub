---
schema_version: 1
id: topos-P101-query-coverage
project: topos
title: "Close query semantic coverage gaps"
tier: sonnet5-high
input_revision: "f0b24e13"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P100-diag-coverage]
session: "resume:topos-coverage"
scope:
  touch:
    - "src/topos/query/semantics.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P101-query-coverage.md"
    - "nyxloom-trove/reports/P101-*.md"
  forbid:
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "src/topos/query/semantics.py has empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or partial target coverage"
    gate: topos-suite
  - id: O2
    observable: "tests drive real metric points and semantics and assert exact gauge, rate, counter, integral, event, state, reset, gap, finite-value, and current-value results"
    negative: "tests mock the function under test, assert only calls/non-None/ranges, restate implementation formulas without public outputs, swallow errors, or recapture fork-child coverage"
    gate: topos-suite
  - id: O3
    observable: "the receipt distinguishes test functions from collected cases, binds every residual JSON arc to exact-commit nl -ba source, and states only negative or mutation evidence that has a mechanical receipt"
    negative: "line identities are copied by hand, collection baselines are guessed, or universal mutation/fail-before claims are made without receipts"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass with identical executed/missing statement and branch sets for both targets"
    negative: "serial-only evidence, xdist drift, a green aggregate gate with a remaining target gap, or an unverified self-report is accepted"
    gate: topos-suite
  - id: O5
    observable: "every retained test has exact behavioral assertions; no pragma, omit, evaluator, gate, dependency, sleep, global leak, or nondeterministic host-state reliance is introduced"
    negative: "coverage is raised with assertion-free calls, weak ranges, duplicate tests, leaked module data, or weakened scope"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a target branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires source outside query/semantics.py"
  - "either clean full-gate run fails or the target executed/missing sets differ"
advances: []
---

# P101 — Close query semantic coverage gaps

## Goal

Bring `query/semantics.py` to exact 100% statement and branch coverage in the
full xdist gate. Preserve every previously exact target. The larger
`query/engine.py` target is intentionally deferred to P102 after an aborted
combined draft demonstrated that the two-file package was not bounded enough.

Assigned worktree:
`/workspaces/vbpub/.worktrees/feat/topos-P101-query-coverage`.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and seven
   validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, especially PL3 and
   living PL4 through the P100 validation.
5. `topos/nyxloom-trove/reports/P96-COVERAGE-GAPS.md`, only:
   - `query/semantics.py`: 12 missing lines, 12 missing branch pairs.
6. `topos/tests/test_query.py` and `topos/src/topos/query/semantics.py`.

## Work

1. Relocation preflight: print `pwd`, repository root, status, and HEAD.
   Treat every earlier P96–P100 worktree as stale and forbidden.
2. Run the full branch-aware gate once and mechanically print the current gap
   slice for `query/semantics.py`.
3. For each residual, bind the JSON line/arc to `nl -ba` output from this
   exact commit, then record the metric-point/domain input selecting it and the
   exact semantic summary or current value that changes.
4. Extend the established query fixtures. Prefer complete query behavior over
   direct helper calls; narrow helpers are acceptable only where their output
   is itself a stable contract and is asserted exactly.
5. Exercise gauge, rate, counter delta, integral, event count, state duration,
   reset/gap accounting, finite conversion, and current-value boundaries
   without host state or time dependence.
6. Iterate with focused bind-mounted tests, then full xdist JSON. Never rebuild
   `tester-unified`; no dependency or image input changes are in scope.
7. Do not claim a coverage-tool defect without a minimal serial reproducer,
   exact serial/xdist gap comparison, exact-commit source mapping, and a
   completed branch-input matrix.
8. Run the exact gate twice, require `1/1 exact`, and compare target
   executed/missing sets.
9. Derive test function count and collected-case delta separately with pytest
   collection including and excluding the new file. Run `git diff --check`.
10. Write `P101-LOG.md`, `P101-REPORT.md`, and `P101-SELFREVIEW.md` with the
    baseline/final sets, exact commands/exits, truthful negative evidence,
    collection arithmetic, assertion audit, and parity.
11. Commit, adversarially self-review, repair findings, rerun verification,
    and commit the final state.

## Gate

Use the exact `[gates.topos-suite]` argv. Focused or serial runs are diagnostic
only. Completion requires two complete parallel gate runs and the target JSON
checker exiting zero.

## Scope / forbid

Only frontmatter touch paths may change. Do not alter gate/tooling/dependencies
or unrelated source. No coverage pragmas or omissions. Target source edits
require a regression-proven defect or independently demonstrable dead path.

## BLOCKED

On a mechanical `escalate_if`, stop and write
`BLOCKED: <trigger and exact evidence>` to `P101-LOG.md`. Never return a
partial `done`.
