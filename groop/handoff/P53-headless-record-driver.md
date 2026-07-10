# P53 - Headless Record Driver

## Goal

Add a headless CLI record path (`groop --record FILE --headless ...`) that
drives the existing collector loop and `RecordWriter` without importing
`textual`, so unattended recording works in environments without UI
dependencies installed.

## Workflow

- Branch: `feat/groop-p53-headless-record-driver`
- Worktree: `.worktrees/-groop-p53-headless-record-driver`
- Touch only `groop/**`; write P53-LOG.md/P53-REPORT.md; commit, do not merge.

## Requirements

- Add a `--headless` flag valid only together with `--record FILE`; reject it
  combined with `--attach`, `--replay`, or other UI-selection flags the same
  way existing `--record`/`--attach`/`--replay` combinations are already
  rejected. When `--record FILE --headless` is given without `--once`, drive
  `groop.record.live.live_frame_stream(collector, writer=writer,
  stop_event=...)` directly to completion — no `_run_ui()` call and no
  `textual` import anywhere on this code path. Today `--record FILE` without
  `--once` always calls `_run_ui()`, which requires `textual` and exits 2 when
  it is not installed (`src/groop/cli.py`, the `if args.record is not None:`
  block); `--headless` closes that gap.
- Add `--interval N` (per-run collector interval override) and mutually
  exclusive `--duration S` / `--frames K` bounds; reject giving both
  `--duration` and `--frames` together with exit 2. Omitting both means run
  until signaled.
- Install a clean-shutdown path for `SIGINT`/`SIGTERM` that sets the
  `stop_event` already accepted by `live_frame_stream()` so the current
  in-flight sweep finishes, its frame is written, and
  `RecordWriter.flush(force=True)`/`close()` run before process exit — no
  truncated or unflushed final frame, for both plain JSONL and `.zst` output.
  Use an injectable signal-registration seam so the default test suite does
  not depend on real OS signal delivery.
- Exit codes: `0` on clean completion (duration/frame-count reached, or clean
  signal shutdown); non-zero on I/O or collector-startup failure before any
  frame was written.
- Emit a bounded, human-readable progress line (frame count, elapsed time) to
  stderr at a low fixed cadence; stdout stays reserved for a future `--json`
  summary and must not be corrupted by progress output.
- Document (in this handoff and the doc updates below) the concrete
  motivating advantage over an externally looped `groop --once`: because a
  single headless process keeps the collector's prior-sweep raw counters in
  memory across consecutive in-process sweeps, every `_per_s`-style
  `MetricValue` (e.g. `rf_z_per_s`, `mem_events_*_per_s`, io/net rates) is
  live (`v` populated, `src="derived"`) from frame 1 onward. An externally
  looped `groop --once` instead starts a fresh, cold `Collector` on every
  invocation, so every recorded frame has `v=None, src="derived"` for those
  fields and only the embedded `raw` counter is populated — a reader (see
  P54) must derive rates itself across consecutive frames in that case.
- Motivating use case: unattended per-slice/per-cgroup recording on
  gstammtisch while a container stack settles across tiers. See
  `scripts/gstammtisch-guide/plan-stack-resource-tuning.md` PKG-3, which
  currently plans an interim systemd service looping
  `/root/groop-venv/bin/groop --once --json` every 10s specifically because
  headless record does not exist yet; once this lands, that interim service
  is retired in favor of one long-lived `groop --record ... --headless`
  process with live rates.
- Add CLI-parsing, signal-handling (via the injected seam), duration/frame-
  count boundary, textual-import-absence, and `RecordWriter`-finalization
  tests. The textual-import-absence check must be structural (e.g. assert no
  `textual` entry lands in `sys.modules` after an in-process headless run, or
  an import-graph check on the headless code path) rather than an
  environment assumption.
- Update `README.md` quickstart/CLI docs and `CONTRACTS.md` §5 (JSONL
  recording format) to describe the new flags; headless mode reuses the
  existing P2 file format unchanged, so no schema section changes.

## Out Of Scope

- Any new frame/file schema — headless mode reuses the existing P2
  `RecordWriter`/JSONL/zst format unchanged.
- `--attach`/daemon-side headless recording (this covers only the local
  `Collector` path, matching today's `--record` behavior).
- The `report` reader/aggregator (Package B / P54, separate spec).
- Steady-state auto-detection or any recording-time analysis.
