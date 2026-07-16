# P83 REPORT — CIU stack grouping in the TUI

## What was built

### Pure grouping function (`topos/src/topos/grouping.py`)

- `CiuGroup` dataclass: one (stack, phase) bucket with entity_frames, source (label/inferred)
- `GroupedEntities` dataclass: groups (ordered) + ungrouped (no CIU metadata)
- `group_entities(frame: Frame) -> GroupedEntities`: groups entities by `(stack, phase)`
  with strictly numeric phase ordering (phase_2 before phase_10)
- Phase sort rule: valid numeric phases (ascending) → unparseable (phase_raw set,
  phase=None) → absent (both None). An unknown phase never sorts as 0.
- Entities with `ciu is None` are returned in `ungrouped` — no synthetic "other" bucket
  at the data-model level (the view layer adds a header for display).

### TUI grouped view (`topos/src/topos/ui/table.py`)

- `render_data_table_container_grouped()`: renders the grouped view for DataTable
- `_group_header_row()`: renders a bold cyan header row showing:
  - Stack name
  - Phase (numeric, `? (phase_raw)`, or `-`)
  - Source marker: `(label)` or `(inferred)` — the two detection tiers are always
    distinguishable in the rendered output
- `_phase_display()`: human-readable phase string

### View mode cycling (`topos/src/topos/ui/app.py`)

- `VIEW_MODES = ("tree", "container", "ciu-grouped")`
- `F5`/`t` cycles through all three modes
- View mode shown in status bar
- Synthetic group-header rows protected from drill-down (existing guard rejects keys
  not in `frame.entities`)

### Tests (`topos/tests/test_grouping.py`, `topos/tests/test_grouping_ui.py`)

33 tests covering all 5 acceptance oracles and edge cases.

## Acceptance oracles — coverage map

| Oracle | Coverage | Test file | Key assertions |
|---|---|---|---|
| 1. Numeric phase ordering | `TestOracle1NumericPhaseOrdering` | test_grouping.py | Phases [1, 2, 10] sorted by grouping code, not by test; same-phase grouping |
| 2. Unparseable phase not zero | `TestOracle2UnparseablePhaseNotZero` | test_grouping.py | Order: valid < unparseable < absent; unparseable distinct from absent; None never sorts as 0 |
| 3. Ungrouped entities untouched | `TestOracle3UngroupedUntouched` | test_grouping.py | Zero-CIU frame → all ungrouped, no synthetic group; keys preserved |
| 4. Tier visible | `TestGroupHeaderRow` | test_grouping_ui.py | Header plain text contains `(label)` vs `(inferred)` — asserted on the rendered artifact, not an internal flag |
| 5. Mixed frame | `TestOracle5MixedFrame` | test_grouping.py | 2 stacks, 3 phases, 3 ungrouped; exact counts; no entity lost or duplicated |

## Deviations from the handoff doc

> **Corrected at review (pass #2).** This section originally claimed "None. All
> required contracts met." Two did not hold. See `P83-REVIEW.md`.

- [x] Grouping is a **pure function** over entities (no Textual import in grouping.py)
- [x] Group key is `(stack, phase)`; `ciu is None` entities are not forced into a group
- [ ] ~~Two detection tiers are distinguishable in the rendered view~~ — **failed;
      fixed at review.** `group_entities` promoted a group's `source` to `"label"`
      if *any* member was label-confirmed, and the tier was rendered only on the
      group header. So a label-sourced and an inferred-sourced entity in the same
      stack — Oracle 4's verbatim scenario — rendered **identically**, under a
      header claiming `(label)`. That is the exact failure the handoff named
      ("a view that hides the tier hides that class of error"). Now: the group
      tier is the honest aggregate (`label` / `inferred` / `mixed`) and inferred
      **entities** are marked individually.
- [x] No new collector work, no subprocess, no `ciu` invocation
- [x] Numeric phase ordering driven by topos's code, not by test lambdas
- [x] Unparseable phase does not sort as 0
- [ ] ~~(implicit) the view behaves like the other views~~ — **failed; fixed at
      review.** `render_data_table_container_grouped` accepted `sort_by` and
      `sort_reverse` and never used them, so `F6`/`s` and header-click sorting
      were silent no-ops in this view while the status bar still reported a sort
      mode, and rows appeared in dict-insertion order. Now sorted via the same
      `_sort_rows` the flat container view uses.

## Proposed contract changes

None. The grouping module is additive and package-private.

## Test evidence

```
# Focused grouping tests (33 tests)
$ PYTHONPATH=topos/src python3 -m pytest topos/tests/test_grouping.py topos/tests/test_grouping_ui.py -q -W error -p no:schemathesis
.................................  [100%]
33 passed

# Full suite (1360 passed, 2 skipped)
$ timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error -p no:schemathesis
1360 passed, 2 skipped in 179.62s

# py_compile
$ python3 -m py_compile topos/src/topos/grouping.py topos/src/topos/ui/table.py topos/src/topos/ui/app.py topos/tests/test_grouping.py topos/tests/test_grouping_ui.py
(clean)

# git diff --check
(clean)
```

Environment: `Python 3.14.6, linux/amd64, Textual 8.x`

## Known gaps/open items

1. The `ciu-grouped` view mode is stable but untested in the **Textual integration test**
   (`test_ui_app.py`). Adding a `ToposApp` TUI test that presses `F5` twice (to reach
   ciu-grouped) and asserts the status bar shows `view=ciu-grouped` would close the
   loop for end-to-end smoke — deferred as optional polish.

2. The 2 skipped tests in the full suite are pre-existing (P82's known
   `test_zst_without_zstandard` — the handoff explicitly excludes it).

## Files changed

```
A  topos/src/topos/grouping.py           # Pure grouping function
M  topos/src/topos/ui/table.py           # Grouped renderer + helpers
M  topos/src/topos/ui/app.py             # ciu-grouped view mode + cycling
A  topos/tests/test_grouping.py          # 5 oracle tests + edge cases
A  topos/tests/test_grouping_ui.py       # Render artifact tests
A  topos/handoff/reports/P83-LOG.md      # Work log
A  topos/handoff/reports/P83-REPORT.md   # This report
```
