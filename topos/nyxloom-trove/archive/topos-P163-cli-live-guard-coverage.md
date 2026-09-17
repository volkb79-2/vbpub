---
schema_version: 1
id: topos-P163-cli-live-guard-coverage
project: topos
title: "Specify live CLI incompatible-option guards"
tier: luna-low
input_revision: "5262eeb2"
depends_on: []
session: resume cli
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_cli_live_guards.py", "topos/nyxloom-trove/handoffs/topos-P163-cli-live-guard-coverage.md"]
  forbid: ["topos/src/topos/cli.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "The live CLI rejects headless replay, headless without a record destination, headless attach, and simultaneous record/replay before creating a Collector or UI."
    negative: "An incompatible option combination starts live collection, writes an unintended recording, or produces an ambiguous mode."
    gate: topos-suite
  - id: O2
    observable: "The live CLI rejects simultaneous duration/frame bounds and invalid slice or metric input before reaching collection."
    negative: "Invalid collection constraints reach a runtime boundary or fail with a non-actionable error."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "an oracle needs a live Collector, UI, daemon, product-code edit, or a file outside the two allowed paths"
---

# P163 — Specify live CLI incompatible-option guards

## Context to read first

1. `topos/src/topos/cli.py`: `main` validation block only (roughly lines
   504–558).
2. `topos/tests/test_headless_record.py`: `TestHeadlessCli` only — preserve
   its subprocess/fixture conventions and do not duplicate its end-to-end
   success paths.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add one focused test module which calls `topos.cli.main` only with invalid
   argument combinations that are rejected in its pre-collection validation.
2. Assert return code `2` and the relevant actionable stderr fragment for each
   contract. Patch the Collector/UI boundary to fail loudly if it is reached;
   do not instantiate a live collector or start a UI.
3. Cover the distinct headless/replay, missing-record, headless/attach,
   record/replay, duration/frames, invalid-slice, and invalid-metrics guards
   without asserting incidental parser formatting.
4. Run this focused module in `tester-unified`, self-review all oracles and
   scope, commit only the allowed files, and leave the branch unmerged.

## BLOCKED rule

If an oracle needs a live Collector, UI, daemon, product-code edit, or a
forbidden file, write `BLOCKED: <named oracle and reason>` and stop.
