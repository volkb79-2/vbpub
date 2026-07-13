# P86 - The ciu-grouped view is wired but never driven

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P83 (merged), P76 (merged)
> **Base:** main after P83 merge
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** driving the view through Textual reveals that a synthetic row key (`__group__*`, `__ungrouped__`, `__empty__`) can actually be drilled into, selected as an entity, or crashes the app. That is a product bug in P83, not a test gap - say so in the REPORT and fix it in `src/`, do not work around it in the test.

<!--
CARVE SOURCE (controller-workflow-v2 §8): **backlog-derived** (B-004, filed by the
P83 frontier review). Priority: ranked above the remaining report-polish packages
(P64/P65) because a wired-but-untested view is one refactor away from silently
breaking, and P83's review already had to fix two defects in this exact surface -
the area has a demonstrated defect density. Ranked below P73 (standing product
goal), which stays the top of the queue.
-->

## Goal

P83 added a third view mode (`ciu-grouped`) and reached it from
`action_toggle_view`. **Nothing drives it through the app.** Every P83 test calls
`group_entities()` or `render_data_table_container_grouped()` directly. So the
grouping logic is well covered and the *wiring* is not covered at all: no test
presses `F5`, reaches `ciu-grouped`, and asserts the app renders it.

This matters more than a coverage number, because the P83 review found two real
defects in exactly this surface (a tier the view hid, and a sort the view silently
dropped). A view whose app-level integration is unproven is where the third one
lives.

## Context To Read First (bounded)

- `src/groop/ui/app.py` - `action_toggle_view`, `_refresh_view`, `VIEW_MODES`,
  `action_open_drill`, and the `self.selected_key not in frame.entities` guard.
- `src/groop/ui/table.py` - `render_data_table_container_grouped`,
  `_group_header_row`, `_tier_marked_cells`.
- `tests/test_ui_app.py` - the existing Textual pilot patterns. Note
  `_wait_or_timeout` (P85): use it, never a fixed-iteration `pilot.pause()` loop.
- `handoff/reports/P83-REVIEW.md` - what the two P83 defects were.
- Do **not** touch the grouping logic, `CiuMeta`, the collector, or the detection
  heuristics. P83/P76 own those and both have been reviewed.

## Required Contracts

1. **Drive the real app.** Tests construct a `GroopApp` over a frame containing
   ciu-managed entities and press keys through `pilot`. Asserting on the return
   value of `render_data_table_container_grouped` is what P83 already does; it is
   not this package.
2. **The synthetic rows must be inert.** `__group__*`, `__ungrouped__`, and
   `__empty__` are row keys that are *not* entity keys. Selecting one and pressing
   `Enter` must not open a drill-down and must not raise. Prove it by driving the
   keypress, not by reading the guard.
3. **No new production behavior.** If a contract above cannot be satisfied without
   changing `src/`, that is a P83 defect surfacing - fix it in `src/` and say so
   loudly in the REPORT (see `Escalate-if`). Do not add a test-only branch.
4. **No fixed-iteration pause loops.** P85 removed them; do not reintroduce one.

## Acceptance Oracles (numbered, adversarial)

1. **The cycle reaches the view.** From `default_view="tree"`, `F5` three times
   returns to `tree`, and the intermediate states are `container` then
   `ciu-grouped`. Assert on `app.view_mode` *and* on something rendered (the table
   actually shows a group header row) - a test that only asserts the string
   `app.view_mode == "ciu-grouped"` would pass against a view that renders nothing.
2. **A group header is rendered by the app**, with the stack, the phase, and the
   tier - read out of the mounted `DataTable`, not from the renderer's return value.
3. **The inferred tier survives into the app.** A frame with a `label` and an
   `inferred` entity in the same stack renders them distinguishably *in the mounted
   table*. (This is P83's Oracle 4, re-asserted one layer up - it is the contract
   that already failed once.)
4. **Enter on a group header does nothing.** Select the `__group__*` row, press
   `Enter`: no `DrillDownScreen` is pushed, no exception. Same for `__ungrouped__`.
   A test that never actually lands the cursor on a synthetic row proves nothing -
   assert the cursor is on it first.
5. **Sorting works in this view, through the app.** Press the sort key; assert the
   entity rows *within a group* reorder in the mounted table. (P83 shipped this
   parameter dead; the fix is unproven at the app level.)
6. **A zero-ciu frame is unharmed.** In `ciu-grouped` mode with no ciu entities,
   every entity still appears exactly once and no group header is rendered.

## Out Of Scope

- The grouping/ordering logic itself (P83, reviewed).
- `ciu`-gated **actions** - still the other half of the Optional-plugins residue,
  still a separate package; actions keep their root/admin/typed-confirmation/audit
  posture and grouping does not shortcut it.
- `_wait_for_frame`'s fixed-iteration loop (backlog B-002) - if you touch it, you
  are out of scope.
- The `[ciu] known_stacks` config surface.

## Docs

`handoff/reports/P86-REPORT.md`, `P86-LOG.md`. `README.md` only if a hotkey's
documented behavior changes (it should not).

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest <focused P86 tests> -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
python3 -m py_compile <changed files>
git diff --check
```

Build the gate environment with `pip install -e 'groop[dev]'` (P84). A run that
prints `GATE FAILED: missing test extra(s)` is not a gate - fix the env and rerun.
State the environment for each result.
