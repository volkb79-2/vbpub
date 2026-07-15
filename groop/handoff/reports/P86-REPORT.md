# P86 REPORT — CIU-grouped view end-to-end gate

## What was built

One new test file, `groop/tests/test_ui_ciu_grouped.py` (7 tests), that
drives a real `GroopApp` through Textual `pilot` keypresses into the
`ciu-grouped` view mode and asserts on the **mounted `DataTable`**
(`MouseTable.ordered_rows` / `get_cell` / `get_row_index`), never on
`render_data_table_container_grouped()`'s return value or on
`app._visible_row_keys` (the renderer's cached output) — per Required
Contract 1. No production code was changed.

No `src/` changes. Driving the view did **not** surface a P83 defect (see
"Escalate-if outcome" below).

## Acceptance oracles — coverage map

| # | Oracle | Test(s) | Key assertion |
|---|---|---|---|
| 1 | Cycle reaches the view, with something rendered | `test_pilot_oracle1_f5_cycle_reaches_ciu_grouped_with_rendered_header` | `F5` x3 from `tree`: `tree` -> `container` -> `ciu-grouped` -> `tree`; at `ciu-grouped`, a `__group__*` row is present in `mt.ordered_rows` with non-blank mounted cell text |
| 2 | Group header shows stack + phase + tier, from the mounted table | `test_pilot_oracle2_group_header_shows_stack_phase_and_tier` | `mt.get_cell("__group__infra/redis-core__phase_3", "name").plain` contains `"infra/redis-core"`, `"phase 3"`, `"(label)"` |
| 3 | Label vs inferred tier survives into the app, distinguishably | `test_pilot_oracle3_label_and_inferred_entities_are_distinguishable` | mounted cells for a label entity and an inferred entity **in the same stack/phase** differ; `"(inferred)"` appears only on the inferred row; the mixed group's mounted header shows `"(mixed)"`, never `"(label)"` |
| 4 | Enter on a synthetic row is inert | `test_pilot_oracle4_enter_on_group_header_is_inert`, `test_pilot_oracle4_enter_on_ungrouped_header_is_inert` | cursor explicitly landed on `__group__*` / `__ungrouped__` via `update_cursor_from_key`, proven via `mt.cursor_coordinate.row == mt.get_row_index(key)` **before** pressing Enter; after Enter, `len(app.screen_stack) == 1` and `not isinstance(app.screen, DrillDownScreen)` |
| 5 | Sort reorders entity rows within a group | `test_pilot_oracle5_sort_key_reorders_entity_rows_within_a_group` | pressing `F6` moves `app.sort_by` from `pressure` to `ram`; the mounted entity row order changes from `[c-c, c-b, c-a]` (pressure desc: 25/15/5) to `[c-b, c-c, c-a]` (ram desc: 30/20/10) |
| 6 | Zero-ciu frame unharmed | `test_pilot_oracle6_zero_ciu_frame_every_entity_once_no_group_header` | using the real fixture frame (`gstammtisch-once.jsonl`, 8 entities, no `ciu` field): no mounted row key starts with `__group__`; every entity key appears in the mounted table exactly once, set-equal to `frame.entities.keys()` |

## Escalate-if outcome — no P83 defect found

The handoff's `Escalate-if` fires if driving the view through Textual shows
a synthetic row key can be drilled into, selected as an entity, or crashes
the app. It did **not** fire. Mechanism, confirmed by both an exploratory
probe and the committed Oracle-4 tests:

- `on_data_table_row_selected` (`app.py`) only special-cases
  `__empty__*` — a `__group__*` or `__ungrouped__` row activation still
  sets `self.selected_key = rk` and calls `self.action_open_drill()`.
- `action_open_drill()` independently guards with
  `self.selected_key not in self.current_frame.entities` — since synthetic
  keys are never entity keys, this guard rejects them unconditionally and
  returns before `push_screen(DrillDownScreen(...))` is reached.

`self.selected_key` is left holding the synthetic key after Enter (a
harmless side effect — the next real frame apply resets it via `_apply_frame`'s
`if self.selected_key not in frame.entities: self.selected_key = None`),
but no drill-down opens and no exception is raised. This was proven by
actually landing the cursor on both `__group__*` and `__ungrouped__` rows
and pressing Enter through `pilot`, not by reading the guard.

## Deviations from the handoff doc

None. All 6 numbered oracles are covered by dedicated tests (oracle 4 got
two tests — one per named synthetic key — since Required Contract 2 names
`__group__*` and `__ungrouped__` as two independent claims). No `src/`
change was required or made. `_wait_for_frame`'s fixed-iteration loop
(backlog B-002) was reproduced verbatim, not modified. `tests/test_ui_app.py`
has zero diff.

## Proposed contract changes

None.

## Test evidence

Environment: Python 3.14.6, linux/amd64, `.venv` built via
`pip install -e './groop[dev]'` (textual 8.2.8, pytest 9.1.1, zstandard
0.25.0, mcp 1.28.1).

```
# Focused P86 tests
$ PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests/test_ui_ciu_grouped.py -q -W error -p no:schemathesis
.......                                                                  [100%]
7 passed in 3.04s

# Focused set: P86 + P83 grouping suites + full existing test_ui_app.py (regression check)
$ PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests/test_ui_ciu_grouped.py groop/tests/test_grouping.py groop/tests/test_grouping_ui.py groop/tests/test_ui_app.py -q -W error -p no:schemathesis
........................................................................ [ 90%]
........                                                                 [100%]
80 passed in 19.77s

# py_compile
$ .venv/bin/python -m py_compile groop/tests/test_ui_ciu_grouped.py
(clean)

# git diff --check
(clean)

# Full suite (P84 zero-skip gate)
$ timeout 900 env PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests -q -W error -p no:schemathesis
1458 passed in 178.31s (0:02:58)
```

Zero skips, zero failures, exit 0 — no `GATE FAILED` banner.

## Known gaps/open items

1. Oracle 6 exercises the real `gstammtisch-once.jsonl` fixture (8
   entities, no `ciu` field) rather than a synthetic zero-ciu frame, to
   also incidentally confirm the grouped view is safe against the
   project's canonical fixture shape (root entity with key `""`, nested
   slices/scopes). A synthetic-frame variant was not added since it would
   duplicate `tests/test_grouping_ui.py::TestRenderGroupedNoCIU` at the
   renderer layer; this package's job was the app layer.
2. Out of scope per the handoff and not touched: grouping/ordering logic
   (P83), ciu-gated actions, `_wait_for_frame`'s fixed-iteration loop
   (backlog B-002), `[ciu] known_stacks` config surface.

## Files changed

```
A  groop/tests/test_ui_ciu_grouped.py    # 7 pilot tests, 6 numbered oracles
A  groop/handoff/reports/P86-LOG.md      # Work log
A  groop/handoff/reports/P86-REPORT.md   # This report
```
