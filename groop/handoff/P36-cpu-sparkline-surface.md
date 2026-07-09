# P36 - CPU Sparkline Surface

## Goal

Add compact CPU trend sparklines to the UI surfaces where the spec expects quick
trend recognition, without changing the collector/model contract.

This targets the remaining `TUI-SPEC.md` §3.0/§3.5 CPU trend polish: the system
banner already shows verdict/load/PSI and P34 adds device-rate lines; this
package should make CPU trend visible in the TUI from existing history data.

## Workflow

Follow `groop/README.md` "Workflow protocol" exactly.

- Branch: `feat/groop-p36-cpu-sparklines`
- Worktree: `.worktrees/-groop-p36-cpu-sparklines`
- Branch from local `main`
- Touch only `groop/**`
- Keep `groop/handoff/reports/P36-LOG.md` updated while working
- Finish with `groop/handoff/reports/P36-REPORT.md` and a focused commit

## Required Context

Read before coding:

- `groop/README.md`
- `groop/CONTRACTS.md`
- `groop/TUI-SPEC.md` §3.0, §3.5, §6.1, §6.4
- `groop/src/groop/record/ring.py`
- `groop/src/groop/ui/app.py`
- `groop/src/groop/ui/banner.py`
- `groop/src/groop/ui/table.py`
- `groop/src/groop/ui/aliases.py`
- `groop/tests/test_ui_app.py`
- `groop/tests/test_ui_banner.py`
- `groop/tests/test_ui_table.py`

## Functional Requirements

Add a small, deterministic sparkline helper under `groop/src/groop/ui/`.

Expected behavior:

- Render an ASCII sparkline for numeric series; keep it stable and width-bounded.
- Missing values should not crash rendering.
- Flat series should render a readable flat line.
- Use existing history/ring data where available; do not add new persistent
  storage or model fields.
- Surface CPU trend in at least one high-value TUI place:
  - preferred: entity table/profile cell for `cpu_pct` when the UI has history;
  - acceptable: banner CPU trend from root/aggregate history if that is the
    least invasive path.
- Keep `--once --json` and replay model behavior unchanged.
- Do not import Textual outside `src/groop/ui/`.

Design constraints:

- ASCII only.
- No color-only meaning.
- Keep fixed widths so rows do not reflow.
- Avoid viewport-dependent font sizing or large decorative UI.

## Tests

Add focused tests covering:

- sparkline helper for rising, falling, flat, missing, and short series;
- table/banner rendering includes a stable CPU trend when history exists;
- no rendering crash when history is absent;
- replay/UI smoke still passes if touched.

## Documentation

Update:

- `groop/docs/STATUS.md` banner/UI polish notes as appropriate.
- `groop/docs/OPERATIONS.md` only if a user-facing key/profile changes.

Do not update merge evidence in `docs/STATUS.md`; the controller does that after
review and merge.

## Out Of Scope

- New collector metrics.
- Full graph panels.
- GPU/ZFS sparklines.
- Terminal color/theme redesign.
