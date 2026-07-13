# P64 - Steady-State Report Baseline Regression Gate

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P54 (merged), P61 (merged)
> **Base:** main after P61 merge
> **Session-hint:** fresh (report area; P61 implementer session long past cache TTL)
> **Serialize-with:** P62, P65 (shared file: `src/groop/report.py` + `parse_report_args`/`_main_report` in `cli.py`)
> **Escalate-if:** a named contract cannot be met as specified; regression comparison requires changing `compute_profile`'s output shape rather than consuming two of its outputs

## Goal

Add a baseline-vs-current regression gate to `groop report`: compute the
steady-state profile for the report's own recording AND for a separate
`--baseline` recording, then let an operator assert bounded *relative* or
*absolute* deltas between the two (exit 1 on regression). This is the
"multi-run trend comparison" consumer that P61 explicitly deferred (P61 Out Of
Scope, bullet 2). It turns `groop report` from a single-recording pass/fail
gate (P61) into a "did this release regress vs a known-good baseline" gate for
the gstammtisch stack measurement program.

## Workflow

- Branch: `feat/groop-p64-report-baseline-regression-gate`
- Worktree: `.worktrees/groop-p64-report-baseline-regression-gate`
- Touch only `groop/**`; write P64-LOG.md/P64-REPORT.md; commit, do not merge.

## Context To Read First

- `src/groop/report.py`: `compute_profile`/`compute_report`, `GroupProfile`
  (fields: `key`, `sample_count`, `window_start_ts`, `window_end_ts`,
  `gauges`, `rates`), `report_to_jsonable`/`format_report`, `_round_float`,
  and the P61 assertion layer (`Assertion`, `AssertionResult`,
  `parse_assert_spec`, `evaluate_assertions`, `assertion_result_to_jsonable`).
- `src/groop/cli.py`: `parse_report_args`/`_main_report` — how P61 wires
  `--assert` parsing, evaluation, exit codes, and the RecordReader/window
  handling for the primary file.
- Exemplar to imitate: P61's `evaluate_assertions` (pure helper consuming the
  profile list) and its exit-code/JSON-block wiring in `_main_report`.

## Requirements

1. Add `--baseline BASE_FILE` (single value, default `None`) to
   `parse_report_args`. When given, the same `--window`/`--group-by` settings
   are applied to BOTH recordings (one profile computation each — the baseline
   is read and profiled with the identical code path, NOT a stored pre-computed
   artifact). `--baseline` without any `--assert-delta` is a usage error
   (exit 2): a baseline with nothing asserted against it is meaningless.
2. Add repeatable `--assert-delta GROUP:METRIC:STAT:OP:VALUE` where `OP` is one
   of `pct<=` (current is at most VALUE percent above baseline),
   `pct>=`, `abs<=` (current minus baseline is at most VALUE),
   or `abs>=`. `VALUE` is finite. `--assert-delta` requires `--baseline`
   (exit 2 otherwise). Delta = `current_stat - baseline_stat`; percent =
   `100 * delta / baseline_stat` (baseline_stat == 0 with a nonzero current is
   an infinite-regression breach with a distinct reason, NOT a divide crash;
   baseline_stat == 0 and current == 0 is 0% delta).
3. Threshold logic lives in a NEW pure helper in `report.py`
   (e.g. `evaluate_deltas(current_profiles, baseline_profiles, delta_assertions)
   -> list[DeltaAssertionResult]`) that consumes the two already-computed
   profile lists — do NOT recompute inside it, do NOT re-read frames there, do
   NOT change `compute_profile`. Independently unit-testable without argparse.
4. A GROUP/METRIC/STAT absent from EITHER the current or the baseline profile is
   a **breach** with a clear reason naming which side is missing (exit 1) — not
   a silent pass, not a usage error. A `null` STAT on either side is likewise a
   breach with a distinct reason.
5. Exit codes: `0` all deltas pass (or none given); `1` at least one delta (or
   plain P61 `--assert`) breached; `2` malformed `--assert-delta`, unknown
   OP/STAT, `--assert-delta`/`--baseline` misuse, unreadable baseline file, or
   the existing usage errors. `--assert` (P61) and `--assert-delta` (P64) may
   both appear in one invocation and are ANDed; any breach on either → exit 1.
6. Delta outcomes appear in `--json` under a deterministic top-level key
   `"deltas": [{group, metric, stat, op, threshold, baseline, actual, delta,
   pct, passed, reason}]` (sorted by group, metric, stat, op), preserving the
   byte-determinism contract (sorted keys, 6-dp `_round_float`). Emitting the
   report JSON stays unconditional; deltas only add the block + change exit code.
   The baseline's own profile is NOT emitted (only the current report's
   `"profiles"` block appears) — the deltas block carries the baseline figures
   it used.

## Out Of Scope

- Steady-state window auto-detection (P62) and free-form metrics (P60).
- More than one baseline / N-way trend series / historical DB — exactly one
  baseline recording.
- Hysteresis, smoothing, or per-sample time-series alignment.
- Changing `compute_profile`, the gauge set, the percentile method, or the
  recording/reader path.
- Human-readable rendering of deltas beyond the exit code and JSON block
  (that rendering is P65).

## Acceptance Oracles / Tests (numbered, adversarial; fixture-recording based)

1. `pct<=` within bound (current 10% over baseline, threshold 20) → exit 0,
   delta/pct in JSON.
2. `pct<=` breached (current 30% over, threshold 20) → exit 1 + the breaching
   `pct` value in JSON.
3. `abs>=` breach and `abs<=` breach each covered (assert the sign of `delta`).
4. Baseline-side absent group/metric → exit 1 with a reason naming the baseline
   side; current-side absent → exit 1 naming the current side (two tests).
5. `baseline_stat == 0, current > 0` → exit 1 infinite-regression reason (no
   ZeroDivisionError); `baseline_stat == 0, current == 0` → 0% pass.
6. Null STAT on baseline and on current each → breach with distinct reason.
7. `--assert-delta` without `--baseline` → exit 2; `--baseline` without any
   `--assert-delta` → exit 2; malformed `--assert-delta` → exit 2; unknown OP → exit 2.
8. Mixed `--assert` (P61) + `--assert-delta` in one run where the P61 assert
   passes but a delta breaches → exit 1, both blocks present in JSON.
9. Byte-determinism of the `"deltas"` block across two runs.
10. At least one test asserts the exact exit code via a real subprocess, not
    just the helper return value. Reuse the gstammtisch fixture recording as
    both current and baseline where a deterministic delta is constructible
    (e.g. same file → all deltas 0%, all `pct<=` bounds pass).

## Docs

- Update `README.md` (`groop report` paragraph) and `docs/OPERATIONS.md` with a
  baseline-regression example. Note in `report.py`'s module docstring that
  delta evaluation consumes two already-computed profile lists and never
  recomputes. Update `docs/ROADMAP.md`/`docs/STATUS.md` package entries.

## Gates

- Run focused tests (`groop/tests/test_report.py`) and the full suite with
  `-W error`; wrap the full-suite command in `timeout`; state in the REPORT
  which environment each result came from (report area needs no `zstandard`;
  note the 2 known zstandard skips if present).
