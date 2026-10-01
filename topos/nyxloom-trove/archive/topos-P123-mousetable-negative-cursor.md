---
schema_version: 1
id: topos-P123-mousetable-negative-cursor
project: topos
title: "Prove MouseTable rejects a malformed negative cursor row"
tier: provisional-haiku-high
input_revision: "4aa4b567"
source: {kind: product-goal, ref: "global-coverage-healing"}
stack: none
depends_on: [topos-P122-mousetable-safety-capsule]
session: "resume:topos-ui-coverage"
scope:
  touch: ["topos/tests/test_ui_ciu_grouped.py", "topos/nyxloom-trove/handoffs/topos-P123-mousetable-negative-cursor.md"]
  forbid: ["topos/src/topos/ui/data_table.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "a malformed negative cursor cannot select the final entity row"
    negative: "Python's negative indexing returns the final stable key instead of None"
    gate: topos-suite
gates: [topos-suite]
review_focus: ["test must exercise MouseTable.row_key_at_cursor directly with a real Textual Coordinate, not mock the method"]
escalate_if: ["the supplied seam is not constructible as specified", "a listed path needs an out-of-scope edit"]
advances: []
---

# P123 — MouseTable negative cursor safety

Read only this handoff and `topos/tests/test_ui_ciu_grouped.py`; append exactly
one test to that file and add only the import it needs. Do not alter existing
tests or any other file. No shell, network, gate, commit, source edit,
search/listing, or new file is authorized. The controller executes all tests
and gates.

Import `Coordinate` from `textual.coordinate`. Construct a bare `MouseTable`,
set `mt._row_keys = ("real",)`, then set
`mt.cursor_coordinate = Coordinate(-1, 0)`. Assert
`mt.row_key_at_cursor() is None`. This must directly prove the negative guard:
without it Python would index `-1` and return `"real"`. Do not mock
`row_key_at_cursor`, `cursor_coordinate`, or the table.

Stop after the edit and reply with the exact changed file. Do not claim a
test/gate ran. If impossible, reply `BLOCKED: <one sentence>` and make no edit.

## Frozen source capsule

```python
153 def row_key_at_cursor(self):
155     cursor_row, _ = self.cursor_coordinate
156     if cursor_row is None or cursor_row < 0 or cursor_row >= len(self._row_keys):
157         return None
158     rk = self._row_keys[cursor_row]
159     return None if rk.startswith("__empty__") else rk
```
