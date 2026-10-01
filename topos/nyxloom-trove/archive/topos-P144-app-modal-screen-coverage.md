---
schema_version: 1
id: topos-P144-app-modal-screen-coverage
project: topos
title: "Cover public app modal screens"
tier: luna-low
input_revision: "86ab35b6"
depends_on: [topos-P143-profile-iteration-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P144-app-modal-screen-coverage.md"]
  forbid: ["topos/src/topos/ui/app.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "The mounted public filter screen focuses its input, returns submitted text, and Escape returns no filter."
    negative: "Filter interaction loses input or Escape applies a value."
    gate: topos-suite
  - id: O2
    observable: "The mounted public jump screen returns submitted input and Escape cancels; glossary mounting renders glossary text and Escape dismisses."
    negative: "Modal controls crash, fail to focus/render, or dismiss with the wrong result."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the public modal flow requires calling a private callback", "a required behavior needs a forbidden source edit"]
---

# P144 — Cover public app modal screens

## Context to read first

1. `topos/src/topos/ui/app.py`: `FilterScreen`, `JumpScreen`, and
   `GlossaryScreen` only.
2. `topos/tests/test_ui_app.py`: its mounted `ToposApp.run_test` style,
   especially existing jump prompt tests.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Add mounted public-screen tests. Drive submit/Escape by user-visible input and
key actions, capture the screen dismissal callback result, and assert focused
inputs or rendered glossary text through public widgets. Do not call a private
screen callback or mock Textual internals.

## BLOCKED rule

If dismissal results cannot be observed without a private callback or forbidden
source edit, write `BLOCKED: <specific reason>` to this handoff and stop.
