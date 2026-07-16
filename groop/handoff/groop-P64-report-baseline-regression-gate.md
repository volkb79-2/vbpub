---
schema_version: 1
id: groop-P64-report-baseline-regression-gate
project: groop
title: "Informational baseline comparison"
tier: flash-high
input_revision: "f77727e"
session: fresh
source: {kind: roadmap, ref: docs/ROADMAP.md}
stack: none
depends_on: []
scope:
  touch: ["groop/**"]
  forbid: []
oracles:
  - id: O1
    observable: "a positive and a negative absolute/percentage delta both compute the correct signed delta and percentage"
    negative: "a delta's sign or percentage is computed incorrectly for either direction"
    gate: groop-suite
  - id: O2
    observable: "a zero baseline against a zero current value produces an explicit typed outcome, not a division"
    negative: "zero against zero triggers a division or an untyped result"
    gate: groop-suite
  - id: O3
    observable: "a zero baseline against a nonzero current value produces an explicit typed outcome instead of an infinite or undefined percentage"
    negative: "zero against nonzero silently produces infinity, NaN, or a coerced value"
    gate: groop-suite
  - id: O4
    observable: "mismatched semantics or units between current and baseline produce an explicit typed refusal"
    negative: "mismatched units or semantics are compared anyway and produce a delta"
    gate: groop-suite
  - id: O5
    observable: "missing or redacted values in either summary produce an explicit typed outcome, never a silent pass"
    negative: "a missing or redacted value is treated as zero or otherwise silently passed through"
    gate: groop-suite
  - id: O6
    observable: "unequal coverage between current and baseline windows produces an explicit typed outcome"
    negative: "unequal coverage is ignored and a delta is still emitted as if coverage matched"
    gate: groop-suite
  - id: O7
    observable: "P61 exit-convention outcomes and P64 delta-breach outcomes combine deterministically, preserving P61's 0/1/2 exit codes"
    negative: "combining a P61 assertion with a P64 breach outcome loses or reorders the original exit code"
    gate: groop-suite
  - id: O8
    observable: "output ordering is deterministic across repeated runs with the same input"
    negative: "the same input produces differently-ordered comparison output on a repeated run"
    gate: groop-suite
  - id: O9
    observable: "the comparison helper never reads or re-profiles frames; a test fails if it attempts frame aggregation"
    negative: "the comparison helper reads or aggregates raw frames instead of consuming only the two P88 summaries"
    gate: groop-suite
gates: [groop-suite, py-compile]
escalate_if: ["comparison requires recomputing metrics outside P88", "comparison would become a mandatory release gate"]
advances: []
---

# P64 - Informational baseline comparison

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** flash-high
> **Depends-on:** P88, P61 (merged)
> **Base:** main after P88
> **Session-hint:** fresh
> **Serialize-with:** P65 (shared query/report CLI)
> **Escalate-if:** comparison requires recomputing metrics outside P88 or would become a mandatory release gate. It is informational by D-007.

## Goal

Add an optional recording/window baseline comparison as a consumer of P88 query
results. D-007 explicitly makes baseline regression non-release-critical: this
package may produce configured pass/breach outcomes for automation, but the
operator-console release oracle does not depend on it.

## Required contracts

- Accept one current and one baseline P88 summary produced with the same query,
  projection, window semantics and registry version. Do not read/re-profile
  frames inside the comparison helper.
- Support finite absolute and percentage comparisons. Zero baseline, absent or
  redacted values, mismatched semantics/units, incomplete coverage and reset
  boundaries produce explicit typed outcomes; never divide, coerce or silently
  pass.
- Emit deterministic JSON containing both values, delta, percentage when
  defined, coverage/source metadata, rule and reason. Preserve P61's 0/1/2 exit
  convention when the optional assertions are used.
- The comparison is available to the human P65 renderer but is not a prerequisite
  for Overview/Explore or the operator scenario release suite.

## Acceptance oracles

Cover positive/negative absolute and percentage deltas, zero/zero, zero/nonzero,
unit/semantic mismatch, missing/redacted values, unequal coverage, mixed P61 and
delta outcomes, deterministic ordering and subprocess exit codes. A test must
fail if the helper attempts frame aggregation.

## Out of scope

N-way trends, historical database selection, automatic “regression” thresholds,
release certification and client-side comparison.

## Gates

Focused report/query tests, zero-skip full suite, compile checks and
`git diff --check`. Write P64-LOG.md/P64-REPORT.md.

## Conversion addendum (nyxloom execution notes)

- **Worktree:** create a git worktree for branch `feat/groop-p64-report-baseline-regression-gate`
  at `.worktrees/groop-p64-report-baseline-regression-gate` (repo-root-relative, per
  `worktree_root` in `groop/.nyxloom/project.toml`) from `main`; do all
  implementation work there, never in the primary checkout.
- **Branch:** `feat/groop-p64-report-baseline-regression-gate`
- **Context to read first:** the Goal, Required contracts, Acceptance oracles and Out
  of scope sections above; `docs/ROADMAP.md`; and any handoff listed as this file's
  frontmatter `depends_on`.
- If a named contract cannot be met as specified, STOP, write `BLOCKED: <reason>` to
  the LOG file, commit, and exit. The declared gates (frontmatter `gates:`) are
  mandatory; never bypass them.
