---
schema_version: 1
id: topos-P143-profile-iteration-coverage
project: topos
title: "Cover multiple custom profile discovery"
tier: controller
input_revision: "661a6527"
depends_on: [topos-P142-grouped-table-filter-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_table.py", "topos/nyxloom-trove/handoffs/topos-P143-profile-iteration-coverage.md"]
  forbid: ["topos/src/topos/ui/table.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "Public available_profiles preserves each of two configured custom profile names."
    negative: "Later configured profiles are silently skipped."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the public profile API cannot enumerate a second custom profile"]
---

# P143 — Cover multiple custom profile discovery

## Work

Extend the existing P140b profile-discovery test with a second valid configured
custom profile and assert both names appear in public `available_profiles`.
Do not edit source.

## BLOCKED rule

If the public API cannot enumerate both configured names, write `BLOCKED` to
this handoff and stop.
