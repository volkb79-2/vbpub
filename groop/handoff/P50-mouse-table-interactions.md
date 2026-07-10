# P50 - Mouse Table Interactions

## Goal

Make the Textual console useful with a mouse while preserving the complete
keyboard workflow: click visible column headers to sort, click entity rows to
select/open drill-down, and keep live/replay refresh stable.

## Workflow

- Branch: `feat/groop-p50-mouse-table-interactions`
- Worktree: `.worktrees/-groop-p50-mouse-table-interactions`
- Touch only `groop/**`; write P50-LOG.md/P50-REPORT.md; commit, do not merge.

## Requirements

- Replace or augment the non-interactive Rich-table `Static` body with a
  Textual-native interactive table model. Do not use fragile terminal x/y
  coordinate guessing against rendered Rich output.
- Reuse the production cell-formatting path and stable entity keys. P41
  byte-identical record/replay formatted-cell fidelity must remain green.
- A header click sorts by that visible column. Repeated clicks toggle direction;
  the title/status shows the active key and direction. Name defaults ascending,
  numeric pressure/usage metrics default descending. Alias columns resolve to
  canonical metrics.
- A row highlight updates `selected_key`; row selection/click opens the same
  drill-down screen as Enter. Empty placeholder rows never open a drill-down.
- Preserve keyboard up/down, Enter, tree left/right collapse/expand, filtering,
  profile/view switching, live refresh, replay refresh, and selection retention.
  Avoid double-opening drill-down when Enter is handled by the table.
- Keep row/column keys stable across refreshes; update in place or restore cursor
  without visible focus jumps. Mouse support must degrade harmlessly when the
  terminal sends no mouse events.
- Add Textual pilot tests for header clicks, direction toggles, alias sorting,
  row click/drill-down, empty rows, refresh retention, and keyboard parity.
- Update README, ROADMAP, STATUS, OPERATIONS/help, and measurements. Run focused
  UI tests, acceptance/TUI smoke, full suite, rendered fidelity, and py_compile.

## Out Of Scope

- Touch gestures, context menus, drag selection, multi-select, wheel-controlled
  value adjustment, or enabling any privileged action by mouse.
