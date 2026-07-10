# P50-LOG — Mouse Table Interactions

## Summary

Replaced the non-interactive Rich-table `Static` body of the Textual TUI with a
`MouseTable(DataTable)` subclass that supports clickable column headers
(sort/toggle direction) and row click drill-down, while preserving full keyboard
parity and P41 rendered-replay fidelity.

## Changes

### Core widget (`groop/src/groop/ui/data_table.py`) — new file
- `MouseTable(DataTable, inherit_bindings=False)` with row-mode cursor.
- Explicit BINDINGS covering only enter, up, down, pageup, pagedown (leaving
  left/right for tree collapse/expand, home/end for replay navigation).
- `populate()`, `update_cursor_from_key()`, `row_key_at_cursor()`, and
  `action_select_cursor()` (delegates to `super()`).
- `action_cursor_left()` / `action_cursor_right()` override Textual's
  screen-level focus-navigation consumption; they delegate to the app's tree
  collapse/expand actions.

### App layer (`groop/src/groop/ui/app.py`)
- Replaced `Static(id="body")` with `MouseTable(id="body-table")`.
- Removed `rich.console.Group` import and Rich `Table` body rendering.
- `_refresh_view()` now calls `_populate_table()` which builds DataTable
  content via the production `format_metric_value()` path.
- Added `sort_reverse: bool` attribute (default: True for pressure descending).
- `on_data_table_header_selected()` handler: same-column click toggles
  direction; new-column click sets direction to default (name asc, others desc).
- `on_data_table_row_highlighted()` handler: updates `selected_key` from
  DataTable cursor movement.
- `on_data_table_row_selected()` handler: opens drill-down for real entity rows;
  empty placeholder rows (prefixed `__empty__`) are blocked.
- Column labels show `^` (ascending) or `v` (descending) on active sort column.
- Status line shows sort direction: `vpressure`, `^name`, etc.
- `action_cycle_sort()` resets `sort_reverse` to default.
- Left/right keys for tree collapse/expand now work via `action_cursor_left`/
  `action_cursor_right` delegation (bypassing Textual focus navigation).

### Table extraction helpers (`groop/src/groop/ui/table.py`)
- `_sort_rows()` now accepts optional `reverse` parameter (default None = name
  asc, others desc).
- `render_data_table_container()` accepts `sort_reverse` and forwards it to
  `_sort_rows()`.

### Tree extraction helpers (`groop/src/groop/ui/tree.py`)
- `_ordered_rows()` and `_sort_branch()` accept optional `sort_reverse`.
- `render_data_table_tree()` accepts `sort_reverse` and forwards it.

### Key bindings (`groop/src/groop/ui/keys.py`)
- Removed up/down/enter bindings (handled natively by DataTable).
- Left/right/h remain for tree collapse/expand (via app bindings).

### Tests (`groop/tests/test_ui_app.py`)
- 10 new P50-focused pilot tests:
  - `test_p50_header_click_sorts_by_column`
  - `test_p50_header_click_toggles_direction`
  - `test_p50_row_highlight_updates_selected_key`
  - `test_p50_row_click_drilldown`
  - `test_p50_empty_placeholder_does_not_open_drill`
  - `test_p50_refresh_preserves_cursor`
  - `test_p50_keyboard_parity_up_down_native`
  - `test_p50_keyboard_parity_enter_drilldown`
  - `test_p50_keyboard_parity_left_right_tree`
  - `test_p50_container_view_keys_work`

### Documentation
- `groop/README.md`: P50 changed from Queued to Done.
- `groop/docs/ROADMAP.md`: P50 marked as done in mermaid diagram and near-term
  section.
- `groop/docs/STATUS.md`: P50 entry updated (queued → done), interactive table
  added to Implemented list, quality gate updated from 623→633.
- `groop/docs/OPERATIONS.md`: Added Mouse Interactions table, updated TUI Keys
  description.
- `groop/MEASUREMENTS.md`: Added P50 evidence section.

## Files Changed

```
M  groop/src/groop/ui/app.py
M  groop/src/groop/ui/keys.py
M  groop/src/groop/ui/table.py
M  groop/src/groop/ui/tree.py
A  groop/src/groop/ui/data_table.py
M  groop/tests/test_ui_app.py
M  groop/README.md
M  groop/docs/ROADMAP.md
M  groop/docs/STATUS.md
M  groop/docs/OPERATIONS.md
M  groop/MEASUREMENTS.md
A  groop/handoff/P50-LOG.md
A  groop/handoff/reports/P50-REPORT.md
```

## Quality Gate

- 633 passed, 1 skipped in 53.20s (full suite)
- 10 P50-focused tests pass in 4.37s
- All 23 pre-P50 tests pass unchanged
- P41 rendered replay fidelity remains green
- `py_compile` clean on all changed/new files
