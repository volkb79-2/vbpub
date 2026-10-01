---
schema_version: 1
id: topos-P149-empty-row-highlight-guard
project: topos
title: "Make empty-table cursor movement a safe no-op"
tier: luna-low
input_revision: "8cdc4e0e"
depends_on: [topos-P147-snapshot-frame-invariant]
session: resume topos-ui-app-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/src/topos/ui/app.py", "topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P149-empty-row-highlight-guard.md"]
  forbid: ["topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "In a mounted app whose frame source is empty, public Up/Down cursor input leaves the base screen running and the waiting status visible."
    negative: "The table emits a null row key and the row-highlight handler crashes while dereferencing it."
    gate: topos-suite
  - id: O2
    observable: "A normal non-empty row highlight still records the selected entity key and permits the existing selection workflow."
    negative: "The null-key guard accidentally disables ordinary table selection."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the null row key comes from a different widget event than MouseTable.RowHighlighted", "repair requires files outside scope"]
---

# P149 — Make empty-table cursor movement a safe no-op

## Context to read first

1. `topos/src/topos/ui/app.py`: `on_data_table_row_highlighted` and the adjacent
   row-selection handler only.
2. `topos/tests/test_ui_app.py`: `_make_app`, public mounted pilot tests, and
   the existing ordinary Up/Down selection test.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add the smallest source guard so a `MouseTable.RowHighlighted` event with no
   row key is a harmless no-op; keep normal non-empty key selection unchanged.
2. Add one mounted public empty-source regression test. It must press Up and
   Down, assert the base screen remains present and the public waiting status
   remains visible. Do not construct or invoke widget events directly.
3. Retain or add a behavioural assertion that ordinary non-empty navigation
   still selects a real entity key.
4. Run `topos/tests/test_ui_app.py -q` in `tester-unified`, then self-review
   the actual diff against both oracles. Do not merge to main.

## BLOCKED rule

If a null-key highlight cannot be repaired in the named handler without
changing the widget contract or touching a forbidden file, write
`BLOCKED: <specific reason>` and stop.
