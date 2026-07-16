# P86 Work Log

## Context

- Branch: `feat/topos-p86-ciu-grouped-end-to-end`
- Worktree: `/workspaces/vbpub/.worktrees/topos-p86-ciu-grouped-end-to-end`
- Base commit: `bf74607` (2026-07-15 reconciliation, on `main`)
- Package: P86 — CIU-grouped TUI end-to-end gate
- Current objective: drive the real `ToposApp` through Textual pilot
  keypresses into the `ciu-grouped` view mode and prove the 6 numbered
  acceptance oracles against the MOUNTED `DataTable`, not renderer return
  values.

## Timeline

```text
2026-07-15 (session start)
- Action: read handoff/P86-ciu-grouped-view-end-to-end.md, README.md,
  src/topos/ui/app.py, src/topos/ui/table.py, tests/test_ui_app.py,
  handoff/reports/P83-REVIEW.md, src/topos/grouping.py, tests/test_grouping_ui.py.
- Result: understood the wiring: action_toggle_view cycles VIEW_MODES
  ("tree","container","ciu-grouped"); render_data_table_container_grouped
  emits synthetic row keys __group__<stack>__<phase_raw>, __ungrouped__,
  __empty__; on_data_table_row_selected guards __empty__ only, but
  action_open_drill's `self.selected_key not in self.current_frame.entities`
  guard independently rejects any synthetic key (they are never entity
  keys), so __group__*/__ungrouped__ never reach DrillDownScreen even
  though they are assigned to self.selected_key. This looked correct by
  inspection; P86's job is to prove it by driving real keypresses, per the
  Escalate-if clause.
- Follow-up: build the gate venv, then write the tests.

- Action: built the gate environment per README's "Gate environment"
  section.
- Commands:
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -e './topos[dev]'
- Result: topos 0.1.0, textual 8.2.8, pytest 9.1.1, zstandard 0.25.0,
  mcp 1.28.1 (matches the declared [dev] extra; no GATE FAILED banner
  possible with these installed).

- Action: probed the MouseTable/DataTable API in a scratch script (not
  part of the suite) to confirm assumptions before writing real tests:
  MouseTable.ordered_rows (list[Row], .key.value gives the row key string
  in MOUNTED display order), get_cell(row_key, column_key) (accepts plain
  str keys), get_row_index(row_key), update_cursor_from_key(key) (already
  a public MouseTable method used in production by _refresh_view).
- Commands: .venv/bin/python <scratch probe scripts in scratchpad dir>
- Result: confirmed a synthetic frame with 3 ciu-managed entities
  (label/inferred/mixed group + one plain non-ciu entity) renders
  correctly through tree -> container -> ciu-grouped with no exceptions;
  cursor can be explicitly landed on a __group__*/__ungrouped__ row via
  update_cursor_from_key and Enter is provably inert (screen_stack stays
  at 1, no DrillDownScreen). Confirmed F6 (cycle_sort) reorders entity
  rows within a group in the mounted table via ordered_rows. Confirmed
  the zero-ciu real fixture frame (tests/fixtures/frames/gstammtisch-once.jsonl,
  8 entities, no `ciu` field) renders every entity exactly once with no
  __group__* row.
- Decision: no src/ change is needed — the existing guard already holds
  under real pilot-driven keypresses. No P83 defect surfaced. (See
  Escalate-if in the handoff header — it did not fire.)

- Action: wrote topos/tests/test_ui_ciu_grouped.py — 7 pilot tests
  covering the 6 numbered oracles (oracle 4 gets two tests: __group__* and
  __ungrouped__ separately, since each is an independently-named synthetic
  key in Required Contract 2).
- Files changed: A topos/tests/test_ui_ciu_grouped.py
- Result: `PYTHONPATH=topos/src .venv/bin/python -m pytest
  topos/tests/test_ui_ciu_grouped.py -q -W error -p no:schemathesis`
  -> 7 passed.

- Action: ran the focused set (new file + the P83 grouping suites + the
  full existing test_ui_app.py, to confirm zero regression on the pilot
  patterns this package imitates but does not modify).
- Commands: PYTHONPATH=topos/src .venv/bin/python -m pytest
  topos/tests/test_ui_ciu_grouped.py topos/tests/test_grouping.py
  topos/tests/test_grouping_ui.py topos/tests/test_ui_app.py -q -W error
  -p no:schemathesis
- Result: 80 passed.

- Action: py_compile and git diff --check on the changed file.
- Commands: .venv/bin/python -m py_compile topos/tests/test_ui_ciu_grouped.py
  ; git diff --check
- Result: clean.

- Action: full suite gate.
- Commands: timeout 900 env PYTHONPATH=topos/src .venv/bin/python -m
  pytest topos/tests -q -W error -p no:schemathesis
- Result: 1458 passed in ~175-178s, 0 skipped, exit 0. No "GATE FAILED"
  banner — the [dev] extra venv makes every optional-extra oracle
  (zstandard, mcp) reachable, so a zero-skip result is the expected
  P84-gated outcome on this base.

- Action: wrote REPORT and this LOG; committed to the feature branch.
```

## Decisions

- Decision: reproduce `_wait_for_frame` verbatim in the new test file
  instead of importing it from `tests/test_ui_app.py`.
  Reason: no test module in this suite imports helpers from a sibling
  test module (test_grouping_ui.py duplicates its own `_make_entity_frame`/
  `_make_frame` rather than reaching into test_ui_app.py); the handoff
  also explicitly puts `_wait_for_frame`'s fixed-iteration loop out of
  scope ("if you touch it, you are out of scope") — duplicating it
  unmodified keeps `test_ui_app.py` untouched (0 lines changed there) and
  keeps this package's diff self-contained.
  Impact: `test_ui_app.py` has zero diff; the new file is standalone.

- Decision: use `MouseTable.ordered_rows` / `get_cell` / `get_row_index`
  (real DataTable widget API) instead of `app._visible_row_keys` for every
  oracle assertion.
  Reason: `app._visible_row_keys` is the renderer's return value cached on
  the app; Required Contract 1 explicitly forbids asserting on that
  ("Asserting on the return value of render_data_table_container_grouped
  is what P83 already does; it is not this package"). `ordered_rows` /
  `get_cell` read the actually-mounted `DataTable` widget state.
  Impact: every oracle test reads only from `mt` (the mounted MouseTable),
  never from `app._visible_row_keys`, except where `app.selected_key` /
  `app.view_mode` / `app.sort_by` are the state under test by name (those
  ARE the app-level contract, not renderer output).

- Decision: give distinct `pressure` metric values to the Oracle 5 sort
  fixture in addition to `ram`.
  Reason: `sort_by` defaults to `SORT_ORDER[0] == "pressure"` on app init;
  without a distinct pressure value per entity the initial order would tie
  and (being a stable sort) coincide with insertion order, making the
  "press F6, order changes" assertion accidentally pass even if sorting
  were broken.
  Impact: the test asserts two DIFFERENT concrete orders (by pressure,
  then by ram) rather than just "not equal to insertion order".

- Decision: use `Entity(parent=None, ...)` for all synthetic ciu test
  entities rather than a `parent="system.slice"` string with no matching
  entity in the frame (test_grouping_ui.py's pattern, fine for renderer-only
  tests).
  Reason: at the app level, `_apply_frame` unconditionally renders
  `view_mode == "tree"` first (the default), which walks the tree from
  `parent=None`; a dangling parent reference would silently drop the
  entity from the tree view's `__ordered_rows` (no crash, but a
  misleading zero-row intermediate state) before the test ever reaches
  `ciu-grouped`.
  Impact: entities behave as top-level tree nodes; verified via probe that
  tree -> container -> ciu-grouped cycling renders all of them at every
  step, matching the real fixture's shape more closely.

## Blockers

None.

## Validation

```bash
# Focused P86 tests
$ PYTHONPATH=topos/src .venv/bin/python -m pytest topos/tests/test_ui_ciu_grouped.py -q -W error -p no:schemathesis
.......                                                                  [100%]
7 passed in 3.04s

# Focused set: P86 + P83 grouping suites + full test_ui_app.py (regression check)
$ PYTHONPATH=topos/src .venv/bin/python -m pytest topos/tests/test_ui_ciu_grouped.py topos/tests/test_grouping.py topos/tests/test_grouping_ui.py topos/tests/test_ui_app.py -q -W error -p no:schemathesis
........................................................................ [ 90%]
........                                                                 [100%]
80 passed in 19.77s

# py_compile
$ .venv/bin/python -m py_compile topos/tests/test_ui_ciu_grouped.py
(clean)

# git diff --check
(clean)

# Full suite (P84 zero-skip gate)
$ timeout 900 env PYTHONPATH=topos/src .venv/bin/python -m pytest topos/tests -q -W error -p no:schemathesis
1458 passed in 178.31s (0:02:58)
```

Environment: Python 3.14.6, linux/amd64 (devcontainer), `.venv` built via
`pip install -e './topos[dev]'` — textual 8.2.8, pytest 9.1.1, zstandard
0.25.0, mcp 1.28.1.

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
