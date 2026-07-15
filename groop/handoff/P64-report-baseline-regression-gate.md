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
