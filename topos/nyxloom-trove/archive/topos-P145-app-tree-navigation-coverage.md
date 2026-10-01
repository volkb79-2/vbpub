---
schema_version: 1
id: topos-P145-app-tree-navigation-coverage
project: topos
title: "Cover app tree navigation interactions"
tier: luna-low
input_revision: "be0f7c21"
depends_on: [topos-P144-app-modal-screen-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P145-app-tree-navigation-coverage.md"]
  forbid: ["topos/src/topos/ui/app.py", "topos/src/topos/ui/tree.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "Mounted app keys toggle banner state, move selection cyclically, and render the matching selected row."
    negative: "Navigation loses selection or banner state has no visible effect."
    gate: topos-suite
  - id: O2
    observable: "Tree left collapses a selected parent with children, right re-expands it, and left on an uncollapsed child moves to its parent."
    negative: "Tree navigation hides the wrong branch or cannot return to a parent."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the contract needs private app methods or direct widget-state mutation", "fixture data cannot provide a parent and child through public keys"]
---

# P145 — Cover app tree navigation interactions

## Context to read first

1. `topos/src/topos/ui/app.py`: `action_toggle_banner`, selection movement,
   collapse/expand tree, `_move_selection`, and `_child_keys` only.
2. `topos/tests/test_ui_app.py`: existing mounted tree collapse test and
   `_make_app`/`_wait_for_frame` helpers.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Extend mounted app key-interaction tests. Assert public observable selection
and visible rows before/after banner/navigation/tree actions. Use only key
presses and public widgets, not private action/callback calls or mocks.

## BLOCKED rule

If a named navigation behavior cannot be driven via public mounted keys, write
`BLOCKED: <specific reason>` to this handoff and stop.
