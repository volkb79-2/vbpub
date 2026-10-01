---
schema_version: 1
id: topos-P122-mousetable-safety-capsule
project: topos
title: "Close the preflighted MouseTable selection and tree-delegation residual"
tier: low-cost-route-trial
input_revision: "dbe4232e"
source: {kind: product-goal, ref: "controller-preflighted-p122-residual"}
stack: none
depends_on: [topos-P121-bpf-gate-capsule]
session: "resume:topos-ui-coverage"
scope:
  touch: ["topos/tests/test_ui_ciu_grouped.py", "topos/nyxloom-trove/handoffs/topos-P122-mousetable-safety-capsule.md"]
  forbid: ["topos/src/topos/ui/data_table.py", "topos/src/topos/ui/app.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "unknown selections are harmless, synthetic rows do not become entity selections, and the mounted table delegates tree left/right to the real app"
    negative: "an invalid key moves the cursor, a synthetic row returns an entity key, or a key action bypasses the app's tree collapse/expand behavior"
    gate: topos-suite
  - id: O2
    observable: "the unsupported-app delegation branch is harmless"
    negative: "calling either action without a supported app raises or invokes a fake action"
    gate: topos-suite
  - id: O3
    observable: "only the nominated test file changes"
    negative: "rewriting existing tests, changing product/gate files, or creating files is accepted"
    gate: topos-suite
gates: [topos-suite]
review_focus: ["each assertion observes a real user-visible selection/tree contract rather than merely calling the method", "the PropertyMock seam covers only the unsupported-app defensive path"]
escalate_if: ["a supplied expectation contradicts this preflighted capsule", "a listed path needs an out-of-scope source edit"]
advances: []
---

# P122 — preflighted MouseTable safety capability capsule

Read only this handoff and `topos/tests/test_ui_ciu_grouped.py`; append exactly
three tests to that file. Add only the imports those tests need. Do not alter
existing tests or any other file. No shell, network, gate, commit, source edit,
search/listing, or new file is authorized. The controller executes all
tests/gates.

1. Use the existing `_make_ciu_app`, `_wait_for_frame`, and `asyncio.run`
   pattern to mount the real app. With its real `#body-table`, save the initial
   `cursor_coordinate`, call `update_cursor_from_key(None)` and then
   `update_cursor_from_key("missing-key")`, and assert the coordinate is
   unchanged. Then set `app.filter_text` to the frozen exact no-match string
   `"ZZZZ_NONEXISTENT_ZZZZ"`, call `app._refresh_view()`, pause, and
   assert `mt.row_key_at_cursor() is None`. This proves a restored stale key
   cannot move selection and an empty placeholder can never be treated as an
   entity.
2. In a second real mounted app using the same helpers, select the known
   parent key `"besteffort.slice"` with `update_cursor_from_key`, pause, and
   assert the child key
   `"besteffort.slice/docker-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.scope"`
   is initially visible. Call `mt.action_cursor_left()` directly, pause, and
   assert the parent is in `app._collapsed_tree_keys` and its child is absent
   from `app._visible_row_keys`. Call `mt.action_cursor_right()`, pause, and
   assert the parent is no longer collapsed and the child is visible again.
   This must exercise the actual app action through the mounted table, not a
   mock of the app action.
3. Import `MouseTable` and `PropertyMock, patch` from `unittest.mock`. Make a
   bare `MouseTable`; patch the inherited `MouseTable.app` property with a
   `PropertyMock(return_value=object())`; call both `action_cursor_left()` and
   `action_cursor_right()` inside the patch. No assertion is needed beyond
   successful return: the observable contract is that an unsupported/non-App
   owner is a safe no-op, and this preflight has verified that this exact seam
   is constructible in `tester-unified`.

Use exact behavior assertions above. Do not mock `move_cursor`, the real app
tree actions, or any renderer. Stop after the edit and reply with test-name →
behavior mapping and the exact changed file. Do not claim a test/gate ran. If
impossible, reply `BLOCKED: <one sentence>` and make no edit.

## Frozen source capsule

```python
# topos/src/topos/ui/data_table.py
143 def update_cursor_from_key(self, key):
148     if key is None or key not in self._row_keys:
149         return
150     row_index = self._row_keys.index(key)
151     self.move_cursor(row=row_index)

153 def row_key_at_cursor(self):
155     cursor_row, _ = self.cursor_coordinate
156     if cursor_row is None or cursor_row < 0 or cursor_row >= len(self._row_keys):
157         return None
158     rk = self._row_keys[cursor_row]
159     return None if rk.startswith("__empty__") else rk

166 def action_cursor_left(self):
172     app = self.app
173     if isinstance(app, App) and hasattr(app, "action_collapse_tree"):
174         app.action_collapse_tree()

176 def action_cursor_right(self):
182     app = self.app
183     if isinstance(app, App) and hasattr(app, "action_expand_tree"):
184         app.action_expand_tree()
```
