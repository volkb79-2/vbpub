---
schema_version: 1
id: topos-P99-procs-coverage
project: topos
title: "Close process collection and sampling coverage gaps"
tier: sonnet5-high
input_revision: "c7343186"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P98-record-coverage]
session: "resume:topos-coverage"
scope:
  touch:
    - "src/topos/collect/procs.py"
    - "src/topos/procs/procfs.py"
    - "src/topos/procs/sampler.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P99-procs-coverage.md"
    - "nyxloom-trove/reports/P99-*.md"
  forbid:
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "src/topos/collect/procs.py, src/topos/procs/procfs.py, and src/topos/procs/sampler.py each have empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or partial target coverage"
    gate: topos-suite
  - id: O2
    observable: "tests prove process discovery, procfs parse/degradation, PID reuse identity, bounded sampling, ranking and failure behavior with real procfs fixtures or deterministic injected boundaries and exact results"
    negative: "tests mock the function under test, assert only calls/non-None, depend on host /proc or timing, swallow errors, or recapture fork-child coverage"
    gate: topos-suite
  - id: O3
    observable: "every retained regression assertion has fail-before evidence against the P98 baseline or a deliberate mutation of its exact behavior"
    negative: "new tests are accepted without evidence that their assertions reject missing or incorrect behavior"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass with identical executed/missing statement and branch sets for all three targets"
    negative: "serial-only evidence, xdist drift, a green aggregate gate with a remaining target gap, or an unverified self-report is accepted"
    gate: topos-suite
  - id: O5
    observable: "test collection count and diff hygiene are measured mechanically; no pragma, omit, evaluator, gate, dependency, sleep, or host-proc reliance is introduced"
    negative: "the report guesses test count, self-asserts diff cleanliness, or improves coverage by weakening scope or determinism"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a target branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires source outside the three named target modules"
  - "either clean full-gate run fails or target executed/missing sets differ"
advances: []
---

# P99 — Close process collection and sampling coverage gaps

## Goal

Bring `collect/procs.py`, `procs/procfs.py`, and `procs/sampler.py` to exact
100% statement and branch coverage in the full xdist gate. Preserve the
already-exact process identity, owner, sensitivity, and candidate modules.

Assigned worktree:
`/workspaces/vbpub/.worktrees/feat/topos-P99-procs-coverage`.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and seven
   validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, especially PL3 and
   living PL4.
5. `topos/nyxloom-trove/reports/P96-COVERAGE-GAPS.md`, only:
   - `collect/procs.py`: 4 missing lines, 1 missing branch pair.
   - `procs/procfs.py`: 37 missing lines, 8 missing branch pairs.
   - `procs/sampler.py`: 14 missing lines, 19 missing branch pairs.
6. Existing process/procfs/sampler tests and fixtures. Extend them rather than
   duplicating setup.

## Work

1. Run the full branch-aware gate once and mechanically print the current gap
   slice for all three targets.
2. Map each gap to process behavior. Use temporary procfs trees and injected
   readers/clocks; never depend on the container's live `/proc`.
3. Add deterministic tests with exact parsed structures, ordering, identities,
   exceptions, degradation states, and sampling outputs. No sleeps.
4. Iterate with focused tests, then full xdist JSON. Never describe a non-empty
   target gap as completion.
5. Run the exact gate twice, require `3/3 exact`, and compare target
   executed/missing sets.
6. Derive the new-test count with pytest collection and run
   `git diff --check`; do not copy a guessed count into evidence.
7. Write `P99-LOG.md`, `P99-REPORT.md`, and `P99-SELFREVIEW.md` with the
   baseline/final sets, fail-before evidence, commands/exits, exact collection
   count, mock-boundary audit, and parity.
8. Commit, adversarially self-review, repair findings, rerun verification, and
   commit the final state.

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
`BLOCKED: <trigger and exact evidence>` to `P99-LOG.md`. Never return a
partial `done`.
