---
schema_version: 1
id: topos-P139-drill-invariant-coverage
project: topos
title: "Close drill renderer invariant coverage"
tier: luna-low
input_revision: "717fe78c"
depends_on: [topos-P138b-drill-screen-coverage]
session: resume topos-ui-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/src/topos/ui/drill.py", "topos/tests/test_ui_drill.py", "topos/nyxloom-trove/handoffs/topos-P139-drill-invariant-coverage.md"]
  forbid: ["topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml", "topos/src/topos/record/ring.py", "topos/src/topos/diag/score.py"]
oracles:
  - id: O1
    observable: "A threshold-less score input rendered through the public drill renderer omits warn/crit text without crashing or fabricating a threshold."
    negative: "A supported threshold-less score contribution crashes rendering or claims a warn/crit threshold it does not have."
    gate: topos-suite
  - id: O2
    observable: "Drill history and byte display preserve their existing visible output, including no-history for an all-missing recorded series and TiB capping for a 1024^5 byte value."
    negative: "The invariant cleanup changes a visible drill rendering result."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified coverage JSON has no uncovered executable line or branch in ui/drill.py."
    negative: "A drill renderer defensive branch remains unverified or unreachable."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the contract requires a forbidden file", "the current ring or score contracts do not establish the claimed invariant"]
---

# P139 — Close drill renderer invariant coverage

## Context to read first

1. `topos/src/topos/ui/drill.py`: `_pressure_block`, `_history_block`,
   `_sparkline`, and `_fmt_bytes` only.
2. `topos/src/topos/record/ring.py`: `append_frame`, `last`, and `has_series`.
3. `topos/src/topos/diag/score.py`: `ScoreInput`, `score_entity`, and
   `pressure_breakdown`.
4. `topos/tests/test_ui_drill.py`: its P138 public renderer fixtures.
5. `topos/tests/test_p100_diag_coverage.py`: the existing threshold-less
   `ScoreInput` construction pattern.

## Work

1. Add a public-renderer regression test for a supported `ScoreInput` with
   `default_band=None`. The test may temporarily replace the module-level input
   tuple using pytest's `monkeypatch`, as the existing score contract test does.
   It must call `render_drill_text` and assert that the rendered contribution
   has no invented `warn=` or `crit=` text.
2. Make only the two established internal invariant cleanups in `ui/drill.py`:
   remove `_sparkline`'s empty-list guard, because `_history_block` calls it
   only after `HistoryRing.has_series` and normal `append_frame` creation gives
   that series at least one sample; rewrite `_fmt_bytes` so the TiB cap reaches
   its return by normal loop completion rather than an impossible final-loop
   `break`. Preserve all externally visible formatting.
3. Do not delete the `thresholds` type guard: a threshold-less score input is a
   supported score contract and the new regression must exercise its false
   path. Do not add coverage exclusions or direct private renderer-helper tests.

## Oracle

Run the declared tester-unified `topos-suite` gate. `ui/drill.py` must show no
uncovered executable line or branch in its coverage JSON, and existing renderer
output must stay stable.

## Scope / forbid

Touch only the three named paths. Do not change ring, score, gate, configuration,
or dependencies. Do not call a private screen callback or add a hollow helper
test.

## BLOCKED rule

If either source cleanup is not proved by the named contracts, or the public
renderer regression needs a forbidden file, STOP. Write `BLOCKED: <specific
reason>` to this handoff's LOG, commit that log-only change, and exit.
