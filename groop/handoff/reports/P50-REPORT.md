# P50-REPORT — Mouse Table Interactions

## State

| Field | Value |
|---|---|
| Package | P50 |
| Title | Mouse Table Interactions |
| Branch | `feat/groop-p50-mouse-table-interactions` |
| Status | **Done** |
| Controller | Self-reviewed |

## Requirement Coverage

| Requirement | Status | Evidence |
|---|---|---|
| Textual-native interactive table replacing Rich Static body | ✅ | `data_table.py` (MouseTable), app.py uses `MouseTable(id="body-table")` in compose |
| Reuse production cell-formatting path and stable entity keys | ✅ | `format_metric_value()`, `header_label()`, `_sort_rows()` reused; DataTable columns use canonical metric keys |
| Header click sorts by column; repeated click toggles direction | ✅ | `on_data_table_header_selected()` handler; `^`/`v` indicators on active column |
| Name defaults ascending; numeric metrics default descending | ✅ | `sort_reverse` default: `False` for name, `True` for others; `_sort_rows()` matches |
| Alias columns resolve to canonical metrics for sort | ✅ | Column keys are already resolved via `resolve_profile()` which uses canonical metrics |
| Row highlight updates `selected_key` | ✅ | `on_data_table_row_highlighted()` |
| Row selection/click opens drill-down (same as Enter) | ✅ | `on_data_table_row_selected()` calls `action_open_drill()` |
| Empty placeholder rows never open drill-down | ✅ | `__empty__` prefix check in RowSelected handler |
| Keyboard up/down preserved | ✅ | DataTable native cursor with up/down bindings |
| Keyboard Enter preserved | ✅ | DataTable `action_select_cursor()` → `RowSelected` → app handler |
| Tree left/right collapse/expand preserved | ✅ | `action_cursor_left/right` delegates to app |
| Filtering, profile/view switching preserved | ✅ | Unchanged — all app action handlers still work |
| Live refresh stable | ✅ | `_refresh_view()` repopulates table and restores cursor via `update_cursor_from_key()` |
| Replay refresh stable | ✅ | Same `_refresh_view()` path |
| No double-opening drill-down | ✅ | `RowSelected.stop()` prevents double-handling |
| Cursor/keys stable across refreshes | ✅ | `test_p50_refresh_preserves_cursor` |
| Mouse degrades harmlessly | ✅ | DataTable falls back to keyboard-only when terminal sends no mouse events; all 23 pre-P50 tests pass with same key presses |
| P41 rendered replay fidelity preserved | ✅ | `format_metric_value()` cell formatting unchanged; `_sort_rows` backward-compatible |
| Textual pilot tests for header clicks | ✅ | `test_p50_header_click_sorts_by_column`, `test_p50_header_click_toggles_direction` |
| Row click/drill-down tests | ✅ | `test_p50_row_click_drilldown`, `test_p50_empty_placeholder_does_not_open_drill` |
| Refresh retention test | ✅ | `test_p50_refresh_preserves_cursor` |
| Keyboard parity tests | ✅ | `test_p50_keyboard_parity_up_down_native`, `test_p50_keyboard_parity_enter_drilldown`, `test_p50_keyboard_parity_left_right_tree` |
| Docs updated | ✅ | README, ROADMAP, STATUS, OPERATIONS, MEASUREMENTS |

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Textual DataTable API changes across versions | `inherit_bindings=False` isolates key bindings; explicit BINDINGS tuple avoids dependency on parent class bindings |
| Left/right key conflict with tree collapse/expand | `action_cursor_left/right` delegates to app; container view is no-op |
| Home/end key conflict with replay navigation | Excluded from MouseTable BINDINGS; app handles via `inherit_bindings=False` |
| P41 rendered fidelity regression | All cell formatting unchanged; `_sort_rows` accepts optional reverse; selection markers removed from DataTable cells but DataTable provides its own cursor |
| Focus management | DataTable receives focus on mount; RowHighlighted messages keep app `selected_key` in sync |

## Test Results

```
cd groop && python3 -m pytest tests/test_ui_app.py -k "p50" -v
========================= 10 passed, 23 deselected in 4.37s =========================

cd groop && python3 -m pytest tests/ -q
# 633 passed, 1 skipped in 53.20s
```

## Blocker

No blockers. Mouse support requires a terminal that sends mouse events (most
modern terminals do). When mouse events are unavailable, all keyboard workflows
continue to work unchanged.
