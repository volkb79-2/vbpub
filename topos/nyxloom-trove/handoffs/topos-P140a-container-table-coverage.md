---
schema_version: 1
id: topos-P140a-container-table-coverage
project: topos
title: "Cover public container table rendering"
tier: luna-low
input_revision: "1ded6f90"
depends_on: [topos-P139-drill-invariant-coverage]
session: resume topos-ui-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_table.py", "topos/nyxloom-trove/handoffs/topos-P140a-container-table-coverage.md"]
  forbid: ["topos/src/topos/ui/table.py", "topos/src/topos/ui/app.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "The public container-table renderer includes only Docker-backed entities, orders/filter rows as requested, and exposes the selected entity in its rendered cell."
    negative: "A non-container leaks into the view, filtering/sorting lies, or selection is not visible."
    gate: topos-suite
  - id: O2
    observable: "The public renderer provides a visible no-container placeholder when its filter removes every eligible row; its public snapshot remains an exact row/cell representation for populated input."
    negative: "An empty filtered container view is blank or a snapshot diverges from rendered row ordering/cells."
    gate: topos-suite
  - id: O3
    observable: "Coverage JSON covers the executable render_container_table and snapshot_container_table paths, including populated and empty input paths."
    negative: "A public container-table rendering path remains uncovered."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the observable contract needs a production-code change", "the named public APIs cannot expose the asserted behavior"]
---

# P140a — Cover public container table rendering

## Context to read first

1. `topos/src/topos/ui/table.py`: `render_container_table` and
   `snapshot_container_table` only (lines 119–174).
2. `topos/tests/test_ui_table.py`: existing direct public-API test style.
3. `topos/tests/test_ui_drill.py`: the small real `Frame` fixture pattern.
4. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Extend `test_ui_table.py` with a real `Frame` containing at least two
   Docker-backed entities and one non-container entity. Through the public
   `render_container_table` and `snapshot_container_table` APIs, assert that
   only containers appear, name sorting/filtering has the requested observable
   outcome, and the selected row has its visible marker.
2. Exercise a filter that removes all eligible containers through the public
   renderer. Assert the returned row keys are empty and the Rich table contains
   the user-visible `no container rows` placeholder. Do not inspect or call
   private helpers.
3. Keep the assertions behavioral: inspect public return fields and Rich table
   content, not coverage artifacts or mocked calls. Do not change source code.

## Oracle

Run the declared tester-unified `topos-suite` gate. The public populated and
empty paths for the two named APIs must be covered and remain observable.

## Scope / forbid

Touch only this test and handoff. Do not edit production, configuration, gate,
or dependency files; do not use coverage suppression or mock the renderer.

## BLOCKED rule

If either asserted behavior cannot be observed through the named public APIs
without a forbidden file, STOP. Write `BLOCKED: <specific reason>` to this
handoff's LOG, commit that log-only change, and exit.
