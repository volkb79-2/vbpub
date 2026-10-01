---
schema_version: 1
id: topos-P100-diag-coverage
project: topos
title: "Close diagnostic scoring and rule coverage gaps"
tier: sonnet5-high
input_revision: "67df243f"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P99-procs-coverage]
session: "resume:topos-coverage"
scope:
  touch:
    - "src/topos/diag/__init__.py"
    - "src/topos/diag/rules.py"
    - "src/topos/diag/score.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P100-diag-coverage.md"
    - "nyxloom-trove/reports/P100-*.md"
  forbid:
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "src/topos/diag/__init__.py, src/topos/diag/rules.py, and src/topos/diag/score.py each have empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or partial target coverage"
    gate: topos-suite
  - id: O2
    observable: "tests drive diagnostic selection, rule evaluation, evidence construction, and score boundaries through real domain inputs and assert exact findings, scores, ordering, confidence, and reasons"
    negative: "tests mock the function under test, assert only calls/non-None, restate implementation formulas without behavioral outputs, swallow errors, or recapture fork-child coverage"
    gate: topos-suite
  - id: O3
    observable: "every retained regression assertion has fail-before evidence against the P99 baseline or a deliberate mutation of its exact behavior"
    negative: "new tests are accepted without evidence that their assertions reject missing or incorrect behavior"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass with identical executed/missing statement and branch sets for all three targets"
    negative: "serial-only evidence, xdist drift, a green aggregate gate with a remaining target gap, or an unverified self-report is accepted"
    gate: topos-suite
  - id: O5
    observable: "test collection count and diff hygiene are measured mechanically; no pragma, omit, evaluator, gate, dependency, sleep, or nondeterministic host-state reliance is introduced"
    negative: "the report guesses test count, self-asserts diff cleanliness, or improves coverage by weakening scope or determinism"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a target branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires source outside the three named diagnostic modules"
  - "either clean full-gate run fails or target executed/missing sets differ"
advances: []
---

# P100 — Close diagnostic scoring and rule coverage gaps

## Goal

Bring `diag/__init__.py`, `diag/rules.py`, and `diag/score.py` to exact 100%
statement and branch coverage in the full xdist gate. Preserve every
previously exact target.

Assigned worktree:
`/workspaces/vbpub/.worktrees/feat/topos-P100-diag-coverage`.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and seven
   validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, especially PL3 and
   living PL4, including the P99 tool-blind-spot evidence rule.
5. `topos/nyxloom-trove/reports/P96-COVERAGE-GAPS.md`, only:
   - `diag/__init__.py`: 3 missing lines, 3 missing branch pairs.
   - `diag/rules.py`: 6 missing lines, 6 missing branch pairs.
   - `diag/score.py`: 10 missing lines, 9 missing branch pairs.
6. `topos/tests/test_diag.py` and the three target modules.

## Work

1. Relocation preflight: print `pwd`, repository root, status, and HEAD.
   Treat every earlier P96–P99 worktree as stale and forbidden.
2. Run the full branch-aware gate once and mechanically print the current gap
   slice for all three targets.
3. Build a source-branch matrix: for every uncovered arc, state the real
   diagnostic input and the exact observable output that should change.
4. Add deterministic behavioral tests with exact findings, ordering, scores,
   confidence, evidence, and reasons. Never mock the function under test.
5. Iterate with focused bind-mounted tests, then full xdist JSON. Never rebuild
   `tester-unified`; no dependency or image input changes are in scope.
6. Do not claim a coverage-tool defect without a minimal serial reproducer,
   serial/xdist gap comparison, and the completed branch-input matrix.
7. Run the exact gate twice, require `3/3 exact`, and compare target
   executed/missing sets.
8. Derive the new-test count with pytest collection and run
   `git diff --check`; do not copy a guessed count into evidence.
9. Write `P100-LOG.md`, `P100-REPORT.md`, and `P100-SELFREVIEW.md` with the
   baseline/final sets, fail-before evidence, commands/exits, exact collection
   count, mock-boundary audit, and parity.
10. Commit, adversarially self-review, repair findings, rerun verification,
    and commit the final state.

## Gate

Use the exact `[gates.topos-suite]` argv. Focused or serial runs are diagnostic
only. Completion requires two complete parallel gate runs and a per-target
JSON checker exiting zero.

## Scope / forbid

Only frontmatter touch paths may change. Do not alter gate/tooling/dependencies
or unrelated source. No coverage pragmas or omissions. Target source edits
require a regression-proven defect or independently demonstrable dead path.

## BLOCKED

On a mechanical `escalate_if`, stop and write
`BLOCKED: <trigger and exact evidence>` to `P100-LOG.md`. Never return a
partial `done`.
