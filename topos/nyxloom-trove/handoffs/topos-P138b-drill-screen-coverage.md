---
schema_version: 1
id: topos-P138b-drill-screen-coverage
project: topos
title: "Cover public drill screen control degradation"
tier: luna-low
input_revision: "15ef7498"
depends_on: [topos-P138a-drill-render-coverage]
session: resume:topos-ui-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_drill.py", "topos/nyxloom-trove/handoffs/topos-P138b-drill-screen-coverage.md"]
  forbid: ["topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml", "topos/src/topos/ui/drill.py", "topos/src/topos/ui/app.py"]
oracles:
  - id: O1
    observable: "A real mounted DrillDownScreen presents a user-visible DAMON start/stop-unavailable notice and remains usable when control planning/stopping cannot run."
    negative: "An unavailable DAMON control action crashes the drill screen or silently loses the reason."
    gate: topos-suite
  - id: O2
    observable: "Cancelling the public DAMON confirmation flow displays `start cancelled` in the mounted drill screen."
    negative: "A cancelled confirmation is presented as a successful start or leaves stale UI state."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified coverage JSON has no remaining executable DrillDownScreen action line/branch hole."
    negative: "Error/cancel interaction paths in the mounted UI remain untested."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the contract requires calling a private screen callback", "scope requires a forbidden file"]
---

# P138b — Cover public drill screen control degradation

## Context to read first

1. `topos/src/topos/ui/drill.py`: `DrillDownScreen` only.
2. `topos/tests/test_ui_app.py`: its existing `ToposApp.run_test()`/screen
   interaction style.
3. `topos/tests/test_ui_drill.py`: P138a frame helpers (reuse rather than
   duplicate).
4. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Extend `topos/tests/test_ui_drill.py` with public mounted-screen interactions.
Use real temporary invalid DAMON/cgroup paths to make public control planning
and stopping safely unavailable; observe the rendered `#drill-body` text after
the public actions. Drive cancellation through the public confirmation screen
flow, not `_on_control_result` directly. Do not mock the screen under test,
call its private callbacks, or edit production code.

Do not pursue P138a's known caller-impossible renderer branches here
(`_sparkline` empty existing series and pressure-threshold non-dict); those are
an invariant-cleanup package, not screen behavior.

## Oracle

Run the declared tester-unified gate. The screen control lines/branches in
`ui/drill.py` must be covered, with no crash and visible unavailable/cancelled
notices.

## Scope / forbid

Touch only the named test and handoff. Do not use private callbacks, coverage
suppression, or production edits.

## BLOCKED rule

If the public confirmation flow cannot be driven without a private callback or
a forbidden file, STOP. Write `BLOCKED: <specific reason>` to the handoff LOG,
commit that log-only change, and exit.
