---
schema_version: 1
id: topos-P165-cli-squeeze-dispatch
project: topos
title: "Specify squeeze CLI safety and result contracts"
tier: luna-low
input_revision: "672269d7"
depends_on: []
session: resume cli
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_squeeze_dispatch.py", "topos/nyxloom-trove/handoffs/topos-P165-cli-squeeze-dispatch.md"]
  forbid: ["topos/src/topos/cli.py", "topos/src/topos/actions/squeeze.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "Malformed squeeze size input and a sub-second delay return code 2 with an actionable error before the gated squeeze runner is called."
    negative: "Invalid measurement input reaches a cgroup-mutating runner or fails without an operator-useful cause."
    gate: topos-suite
  - id: O2
    observable: "A gated squeeze result maps error to stderr/2, interruption to its distinct status, and successful JSON/text mode to the corresponding stable rendering boundary."
    negative: "A failed or interrupted measurement is reported as success, or selected output mode is ignored."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a real cgroup write, root access, a live squeeze run, product-code edit, or a file outside the two allowed paths"
---

# P165 — Specify squeeze CLI safety and result contracts

## Context to read first

1. `topos/src/topos/cli.py`: `_main_squeeze` only (roughly lines 446–501).
2. `topos/src/topos/actions/squeeze.py`: `SqueezeConfig`, `SqueezeResult`, and
   `parse_size`/rendering signatures only.
3. `topos/tests/test_squeeze.py`: `TestCliSqueezeArgs` and result factories;
   do not duplicate action-runner tests.
4. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add one focused test module that invokes `_main_squeeze` with safe dummy
   paths and monkeypatches the gated squeeze runner at its action-module
   boundary. No real cgroup, audit path, or action may be touched.
2. Assert malformed size and invalid delay are rejected before the runner.
3. Use a minimal real `SqueezeResult` fixture or factory and assert error,
   interrupted, JSON, and human result exit/output handling. Assert the runner
   receives safety-relevant parsed configuration, not incidental internals.
4. Run this focused module in `tester-unified`, self-review all oracles and
   scope, commit only the allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs a real cgroup write, root access, a live squeeze run,
product-code edit, or a forbidden file, write `BLOCKED: <named oracle and
reason>` and stop.
