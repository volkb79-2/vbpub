# P41 Work Log

## Context

- Branch: `feat/groop-p41-rendered-replay-fidelity`
- Worktree: `.worktrees/-groop-p41-rendered-replay-fidelity`
- Goal: close spec section 9 item 10 with strict rendered replay fidelity.

## Timeline

### 2026-07-10 - Agent implementation

- Added a multi-tick record/replay fidelity test and updated release evidence.
- Initial focused result: 10 passed, 2 optional zstandard skips.

### 2026-07-10 - Controller review

- Found that the initial comparison read records directly and formatted metric
  values outside the actual rendered-row path. Its `ReplayDriver` coverage was
  metadata-only because raw fixture frames had not followed the live
  diagnostics annotation sequence.
- Added the public `snapshot_container_table` seam, backed by the same
  production `_row_cells` builder as `render_container_table`.
- Replaced the oversized indirect suite with one parametrized end-to-end gate.
  Frames are annotated as `Collector.collect_once()` annotates live frames,
  written by `RecordWriter`, replayed by `ReplayDriver.play(step=True)`, and
  compared cell-for-cell for every tick.
- Kept JSONL mandatory and compressed JSONL conditional on zstandard.

## Validation

- Focused fidelity: 1 passed, 1 skipped in 0.27s.
- Table/record/fidelity: 19 passed, 1 skipped in 9.57s.
- Full suite: 383 passed, 1 skipped in 47.81s.
- Acceptance: 40 passed in 7.28s.
- Fixture TUI smoke: exit 0, `ok: true`, one frame.
- `py_compile`: clean.

### 2026-07-10 - Post-merge validation

- Full suite: 383 passed, 1 skipped in 46.81s.
- Acceptance: 40 passed in 7.26s.
- Fixture TUI smoke: exit 0, `ok: true`, max RSS 48056 KB.
- `py_compile`: clean.
