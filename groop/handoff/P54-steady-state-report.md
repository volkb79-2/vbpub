# P54 - Steady-State Report Command

## Goal

Add `groop report FILE` to read a recording and compute a machine-readable
steady-state profile (per-entity/per-slice percentiles for key gauges plus
derived rates), reusing the existing P2 reader model with no new frame
schema.

## Workflow

- Branch: `feat/groop-p54-steady-state-report`
- Worktree: `.worktrees/-groop-p54-steady-state-report`
- Touch only `groop/**`; write P54-LOG.md/P54-REPORT.md; commit, do not merge.

## Requirements

- Add a `groop report FILE [--window last:Ns|all] [--group-by slice|entity]
  --json` subcommand dispatched the same way as the existing `groop
  snapshot`/`groop daemon`/`groop action` subcommands (own
  `parse_report_args`/`_main_report` in `cli.py`).
- `FILE` is a JSONL or JSONL.zst recording in the existing P2 header+frame
  format, read via `groop.record.reader` (`RecordReader`/`iter_frames`), the
  same reader `ReplayDriver.from_path()` already uses. No new file parsing.
- `--window last:Ns|all` selects frames by embedded frame timestamp
  (`Frame.ts`) relative to the last frame's timestamp in the file; default is
  `all` when the flag is omitted. Reject malformed window specs with a clear
  message and exit 2.
- `--group-by slice|entity` selects aggregation grain: `entity` emits one
  profile row per `EntityKey`; `slice` rolls entities up under their owning
  `*.slice` ancestor, reusing the existing parent/tree-ancestry logic (do not
  reimplement cgroup path parsing).
- For each group and each of the fixed gauge set — `ram`, `anon`, `z_pool`,
  `z_eq`, `swap_disk`, `psi_mem_some_avg10`, `psi_mem_full_avg10`,
  `psi_io_some_avg10`, `psi_io_full_avg10`, `psi_cpu_some_avg10`,
  `psi_cpu_full_avg10` — compute p50/p95/max over the window from
  `MetricValue.v` samples that are not `None`, using only frames inside the
  window and skipping entities absent from a given frame instead of erroring.
- For `_per_s`-style rate metrics on the same entities (e.g. `rf_z_per_s`,
  `rf_d_per_s`, `rf_f_per_s`, `mem_events_*_per_s`, io/net rate metrics), when
  a frame's `MetricValue.v is None` with `src == "derived"` and a populated
  `raw`, derive the rate from the delta of `raw` counters and the delta of
  `Frame.ts` against the nearest earlier frame that has the same
  entity/metric with a raw counter — do not require strictly consecutive
  frame pairs; tolerate gaps from filtering, entity churn, or the collector's
  own reset handling. When a frame already carries a live `v` (as produced by
  a P53 headless recording), use it as-is instead of re-deriving. This must
  produce equivalent output whether the recording was made by an externally
  looped `groop --once` (every frame cold, all rate `v=None`) or by `groop
  --record --headless` (P53, live `v` from frame 1).
- Output: deterministic JSON (`--json` is required in v1; error with a clear
  message and exit 2 if omitted) containing, per group: the entity/slice key,
  sample count, window bounds actually used, and the p50/p95/max plus
  derived-rate figures above. Key/field ordering must be deterministic
  (sorted) for stable diffing between runs.
- Handle degenerate windows (zero frames selected, or a single-frame window
  where a rate cannot be derived) by omitting/nulling that figure rather than
  raising; a non-zero exit is reserved for genuine usage errors (missing/
  unreadable file, bad `--window`/`--group-by` value) — an empty result set
  for an otherwise valid window is not an error.
- Add fixture-recording-based tests: p50/p95/max correctness on a small
  synthetic frame set, rate derivation across raw-counter gaps, `--window`
  boundary inclusion/exclusion, `--group-by slice` rollup correctness,
  cold-recording (`--once`-style, all rate `v=None`) vs. warm-recording
  (P53 headless-style, live rate `v`) output parity, and malformed-argument
  exit codes.
- Update `README.md` quickstart/CLI docs and the most relevant existing
  architecture/operations doc to describe this new read-only consumer path,
  and note it is the "steady-state profile" input for the gstammtisch stack
  measurement program (`scripts/gstammtisch-guide/plan-stack-resource-tuning.md`
  PKG-3, feeding its `container-memory-profiles.md` deliverable).

## Out Of Scope

- Steady-state window auto-detection (automatically finding when an entity's
  metrics have stabilized) — noted explicitly as future work, not attempted
  in v1; `--window` selection stays a manual, explicit input.
- Any new recording/writer behavior (Package A / P53 covers the writer side;
  this package is read-only).
- Non-JSON/human-readable rendering, live/attach-mode reporting, or
  daemon-side report generation.
- Alerting/threshold gating on the computed profile (a separate future
  consumer of this JSON).
