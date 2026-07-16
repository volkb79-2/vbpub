# P36 - CPU Sparkline Surface

## Goal

Add compact CPU trend sparklines to the UI surfaces where the spec expects quick
trend recognition, without changing the collector/model contract.

This targets the remaining `TUI-SPEC.md` §3.0/§3.5 CPU trend polish: the system
banner already shows verdict/load/PSI and P34 adds device-rate lines; this
package should make CPU trend visible in the TUI from existing history data.

## Workflow

Follow `topos/README.md` "Workflow protocol" exactly.

- Branch: `feat/topos-p36-cpu-sparklines`
- Worktree: `.worktrees/-topos-p36-cpu-sparklines`
- Branch from local `main`
- Touch only `topos/**`
- Keep `topos/handoff/reports/P36-LOG.md` updated while working
- Finish with `topos/handoff/reports/P36-REPORT.md` and a focused commit

## Required Context

Read before coding:

- `topos/README.md`
- `topos/CONTRACTS.md`
- `topos/TUI-SPEC.md` §3.0, §3.5, §6.1, §6.4
- `topos/src/topos/record/ring.py`
- `topos/src/topos/ui/app.py`
- `topos/src/topos/ui/banner.py`
- `topos/src/topos/ui/table.py`
- `topos/src/topos/ui/aliases.py`
- `topos/tests/test_ui_app.py`
- `topos/tests/test_ui_banner.py`
- `topos/tests/test_ui_table.py`

## Functional Requirements

Add a small, deterministic sparkline helper under `topos/src/topos/ui/`.

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
- Do not import Textual outside `src/topos/ui/`.

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

- `topos/docs/STATUS.md` banner/UI polish notes as appropriate.
- `topos/docs/OPERATIONS.md` only if a user-facing key/profile changes.

Do not update merge evidence in `docs/STATUS.md`; the controller does that after
review and merge.

## Out Of Scope

- New collector metrics.
- Full graph panels.
- GPU/ZFS sparklines.
- Terminal color/theme redesign.
