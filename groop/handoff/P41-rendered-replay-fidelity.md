# P41 - Rendered Replay Fidelity

## Goal

Close `TUI-SPEC.md` section 9 item 10 with an automated acceptance test proving
that every recorded tick and its replayed frame produce byte-identical
formatted table cell values at a fixed profile and width.

## Workflow

- Branch: `feat/groop-p41-rendered-replay-fidelity`
- Worktree: `.worktrees/-groop-p41-rendered-replay-fidelity`
- Touch only `groop/**`
- Keep `groop/handoff/reports/P41-LOG.md` current
- Finish with `groop/handoff/reports/P41-REPORT.md` and focused commits

## Requirements

- Build a multi-tick frame sequence with values that exercise production
  formatting: numeric rates/bytes/percentages, unavailable values, unlimited
  limits, source/network labels, row identity/order, and at least one value
  change per tick.
- Write the frames through `RecordWriter`, read them through `RecordReader` or
  `ReplayDriver`, and format both original and replayed frames using production
  table/profile helpers.
- Compare deterministic row keys, column identities, and plain formatted cell
  text for every tick byte-for-byte. Fix width/profile/sort/filter inputs so
  terminal layout is explicitly outside the comparison, matching the spec's
  terminal-width qualification.
- Do not create a parallel formatter or assert only model equality.
- Prefer a small public test seam for formatted row snapshots only if existing
  production helpers cannot expose the required cells without private access.
- Cover JSONL and, when the optional dependency is available, compressed JSONL
  without making zstandard mandatory.
- Keep existing record/replay/schema behavior compatible.
- Update README/ROADMAP/STATUS/MEASUREMENTS and release-readiness state only
  after the strict test passes.

## Validation

- New focused rendered-fidelity tests.
- Existing record and UI table tests.
- Full suite using `PYTHONPATH=groop/src /home/vscode/.venv/bin/python`.
- Focused acceptance tests and P38 `tui-smoke`.
- `py_compile` for touched Python files.

## Out Of Scope

- Pixel/ANSI/terminal-layout snapshot testing.
- Changing recording schema or display formatting.
- Live-host acceptance gates.
