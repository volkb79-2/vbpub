---
schema_version: 1
id: topos-P155-selection-action-coverage
project: topos
title: "Cover public selection actions on empty and populated tables"
tier: luna-low
input_revision: "ebd9e326"
depends_on: [topos-P149-empty-row-highlight-guard]
session: resume topos-ui-replay-coverage
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_app.py", "topos/nyxloom-trove/handoffs/topos-P155-selection-action-coverage.md"]
  forbid: ["topos/src/topos/ui/app.py", "topos/src/topos/ui/keys.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py"]
oracles:
  - id: O1
    observable: "The public `select_prev`/`select_next` Textual actions are harmless in a mounted empty-source app."
    negative: "An empty table causes selection action to index a nonexistent row or crash."
    gate: topos-suite
  - id: O2
    observable: "On a mounted populated app, public previous/next actions wrap selection across the visible rows and move the displayed cursor coherently."
    negative: "Selection gets stuck, selects a nonexistent row, or fails to wrap from first to last."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the public Textual action dispatch cannot invoke select_prev/select_next without adding a binding", "selection correctness requires private state mutation"]
---

# P155 — Cover public selection actions on empty and populated tables

## Context to read first

1. `topos/src/topos/ui/app.py`: `action_select_prev`, `action_select_next`,
   and `_move_selection` only.
2. `topos/tests/test_ui_app.py`: P149 empty-source test, mounted fixture app,
   and existing cursor/navigation assertions only.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add a mounted empty-source check that dispatches the public Textual
   `select_prev` and `select_next` actions (they intentionally have no
   user-key binding) and proves the base screen/status remain safe.
2. Add/extend a mounted populated-table check that dispatches those same public
   actions and asserts selection wraps from first to last and back through real
   visible keys. Use public action dispatch, not direct private state mutation.
3. Do not modify bindings or product source merely to make these actions
   keyboard-reachable.
4. Run `topos/tests/test_ui_app.py -q` in `tester-unified`, self-review scope
   and oracles, and leave work unmerged.

## BLOCKED rule

If Textual exposes no public way to dispatch these existing actions without
adding a forbidden binding or directly mutating private state, write
`BLOCKED: <specific API limitation>` and stop.
