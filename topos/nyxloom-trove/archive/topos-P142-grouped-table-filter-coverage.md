---
schema_version: 1
id: topos-P142-grouped-table-filter-coverage
project: topos
title: "Cover grouped-table filter empty behavior"
tier: luna-low
input_revision: "8b082282"
depends_on: [topos-P141-table-byte-invariant]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_ciu_grouped.py", "topos/nyxloom-trove/handoffs/topos-P142-grouped-table-filter-coverage.md"]
  forbid: ["topos/src/topos/ui/table.py", "topos/src/topos/grouping.py", "topos/src/topos/ui/app.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "Public render_data_table_container_grouped with an empty filter emits group and ungrouped headers alongside entity rows."
    negative: "The normal grouped table hides valid entities or ungrouped context."
    gate: topos-suite
  - id: O2
    observable: "A filter with no matching entity emits only the public __empty__ row and visible no-container placeholder, not an empty group header."
    negative: "A filtered-away group is shown empty or the view becomes blank."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the existing CIU frame fixtures cannot exercise both outcomes without source changes"]
---

# P142 — Cover grouped-table filter empty behavior

## Context to read first

1. `topos/src/topos/ui/table.py`: `render_data_table_container_grouped` only.
2. `topos/tests/test_ui_ciu_grouped.py`: its existing direct grouped-renderer
   fixtures and assertions.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Add behavioral direct public-renderer tests using existing realistic CIU frame
fixtures. Assert an unfiltered result includes group/ungrouped context and rows;
assert a no-match filter produces exactly `__empty__` and a visible
`no container rows` cell, with no group key. Do not mock grouping or edit source.

## BLOCKED rule

If existing fixture contracts cannot expose both public outcomes without a
forbidden file, STOP. Write `BLOCKED: <specific reason>` to this handoff's LOG,
commit that log-only change, and exit.
