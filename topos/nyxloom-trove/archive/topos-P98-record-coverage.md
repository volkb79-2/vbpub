---
schema_version: 1
id: topos-P98-record-coverage
project: topos
title: "Close all record-stack statement and branch gaps"
tier: sonnet5-high
input_revision: "64916087"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P97-coverage-quickwins]
session: "resume:topos-coverage"
scope:
  touch:
    - "src/topos/record/headless.py"
    - "src/topos/record/reader.py"
    - "src/topos/record/replay.py"
    - "src/topos/record/writer.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P98-record-coverage.md"
    - "nyxloom-trove/reports/P98-*.md"
  forbid:
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "headless.py, reader.py, replay.py, and writer.py each have empty missing_lines and missing_branches in branch-aware JSON from the complete parallel gate"
    negative: "completion is claimed from aggregate percentage, focused coverage, rounded output, or fewer than all four modules at exact 100%"
    gate: topos-suite
  - id: O2
    observable: "record tests assert durable bytes/JSONL, replay order and timing, corruption/truncation errors, cleanup, bounded stopping, and propagated failures through real temporary files and narrow clock/writer boundaries"
    negative: "tests only call branches, mock the record unit under test, assert non-None, swallow exceptions, or depend on wall-clock scheduling"
    gate: topos-suite
  - id: O3
    observable: "every new regression assertion has fail-before evidence against the P97 baseline or a deliberate mutation of the exact branch, then passes unchanged after repair"
    negative: "tests are accepted without evidence that the assertion rejects missing or incorrect behavior"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass and produce identical executed and missing statement/branch sets for all four record modules"
    negative: "serial-only evidence, xdist drift, or a green gate with any target gap is accepted"
    gate: topos-suite
  - id: O5
    observable: "no coverage pragma, omit, evaluator, gate, or dependency change is used; any target source edit is backed by a failing behavioral regression and preserves the record format contract"
    negative: "dead code, exclusions, or weakened validation are used solely to improve the percentage"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a remaining target branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires a source file outside the four named record modules"
  - "either of two clean full-gate runs fails or target executed/missing sets differ"
advances: []
---

# P98 — Close all record-stack coverage gaps

## Goal

Bring the remaining record subsystem to exact 100% statement and branch
coverage in the full xdist gate. This package is deliberately coherent and
bounded: only `record/headless.py`, `record/reader.py`, `record/replay.py`, and
`record/writer.py`. `record/ring.py` and `record/live.py` are already exact
100% and must remain green.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`, especially behavioral
   oracles, fail-before/pass-after, gate evidence, and BLOCKED.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, the gate contract and
   seven validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, PL2 and PL3.
5. `topos/nyxloom-trove/reports/P96-COVERAGE-GAPS.md`, only these entries:
   - `record/headless.py`: 28 missing lines, 3 missing branch pairs.
   - `record/reader.py`: 16 missing lines, 11 missing branch pairs.
   - `record/replay.py`: 5 missing lines, 3 missing branch pairs.
   - `record/writer.py`: 5 missing lines, 4 missing branch pairs.
6. Existing `test_record.py`, `test_headless_record.py`, and adjacent record
   tests. Extend their fixtures and real-file style.
7. P97 review history as a warning: a green aggregate gate is not completion;
   only per-target empty missing sets count.

## Work

1. Produce a pre-change machine-readable gap slice for all four target files.
2. Map every missing line and branch pair to record behavior. Prefer existing
   helpers and tests beside the relevant subsystem.
3. Add deterministic regression tests with exact outputs and errors. Use
   `tmp_path` for storage and injected clocks/writer boundaries for timing or
   failures. Do not sleep.
4. Run focused tests while iterating. After each full measurement, parse the
   full-suite coverage JSON and print the exact remaining line and branch sets
   for all four targets.
5. Do not stop at partial closure. A target described as
   infrastructure-dependent is still open unless a mechanical `escalate_if`
   trigger fires and the package follows BLOCKED.
6. Run the exact declared `topos-suite` twice from a clean worktree. Require
   all four targets to have empty missing sets in both runs and compare their
   executed/missing sets.
7. Write `P98-LOG.md`, `P98-REPORT.md`, and `P98-SELFREVIEW.md` with exact
   before/after counts, fail-before evidence, commands/exits, test inventory,
   source-edit justification, pragma/omit audit, and parity.
8. Commit coherent implementation, adversarially self-review the entire diff,
   repair every finding, rerun proportionate verification, and commit the final
   state.

## Gate

Use the exact `[gates.topos-suite]` argv. Focused commands are iteration
evidence only. Completion requires two complete xdist/pytest-cov/evaluator
runs and mechanical per-target closure.

## Scope / forbid

Only frontmatter touch paths may change. Do not edit the gate, evaluator,
dependencies, unrelated source, or P96/P97 evidence. Do not add a coverage
pragma or omit. A target source change is allowed only for a regression-proven
defect or demonstrably dead/redundant path and must be independently reviewed.

## BLOCKED

If an `escalate_if` trigger fires, stop, write
`BLOCKED: <trigger and exact evidence>` to `P98-LOG.md`, commit only coherent
evidence, and return the trigger. Never substitute partial coverage for
BLOCKED.
