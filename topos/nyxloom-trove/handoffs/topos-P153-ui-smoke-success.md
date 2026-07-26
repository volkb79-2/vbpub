---
schema_version: 1
id: topos-P153-ui-smoke-success
project: topos
title: "Cover the public UI smoke success path"
tier: luna-low
input_revision: "381160ef"
depends_on: [topos-P152-replay-minimum-delay]
session: resume topos-ui-replay-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P153-ui-smoke-success.md"]
  forbid: ["topos/src/topos/ui/app.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "The public `run_ui(..., smoke=True)` entry point consumes an available frame and returns its documented smoke-success summary."
    negative: "Smoke mode skips the mounted UI lifecycle, never consumes the frame, or reports success without frame count/view/profile evidence."
    gate: topos-suite
  - id: O2
    observable: "The test uses a finite fixture iterable and real UI smoke mode, not a mock of `ToposApp` or `_run_ui_smoke`."
    negative: "A mocked helper test misses the public entry-point wiring."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the public smoke entry point cannot complete against the existing fixture without a source edit", "success output is not stable enough for a behavioural assertion"]
---

# P153 — Cover the public UI smoke success path

## Context to read first

1. `topos/src/topos/ui/app.py`: `run_ui` and `_run_ui_smoke` only.
2. `topos/tests/test_ui_app.py`: app fixture construction and public mounted
   UI test conventions only.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add one direct public `run_ui(..., smoke=True)` regression test with the
   existing fixture frame/config/filesystem roots.
2. Assert the returned string reports smoke success plus frame count, view, and
   profile. Do not mock `ToposApp`, call `_run_ui_smoke`, or use a wall-clock
   sleep.
3. Run `topos/tests/test_ui_app.py -q` in `tester-unified`, self-review the
   diff against both oracles/scope, and leave work unmerged.

## BLOCKED rule

If the public smoke call cannot complete using the normal fixture iterable
without any source change, write `BLOCKED: <specific reason>` and stop.
