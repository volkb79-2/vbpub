# P65 - Steady-State Report Human-Readable Rendering

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P54 (merged), P61 (merged)
> **Base:** main after P61 merge
> **Session-hint:** fresh (report area; P61 implementer session long past cache TTL)
> **Serialize-with:** P62, P64 (shared file: `src/groop/report.py` + `parse_report_args`/`_main_report` in `cli.py`)
> **Escalate-if:** a named contract cannot be met as specified; a table row cannot be produced from the existing `GroupProfile`/`AssertionResult` fields without recomputing the profile

## Goal

Add a human-readable (fixed-width ASCII table) rendering of the steady-state
profile and, when present, the P61 assertion pass/fail results, selectable via
`--format table` on `groop report`. This is the "human-readable / non-JSON
rendering" consumer that P61 explicitly deferred (P61 Out Of Scope, bullet 4).
`--json` stays the machine contract; `--format table` is an operator-eyes view
of the SAME already-computed figures. No numbers are recomputed or re-derived
for the table — it formats what `report_to_jsonable` already produces.

## Workflow

- Branch: `feat/groop-p65-report-human-readable-render`
- Worktree: `.worktrees/groop-p65-report-human-readable-render`
- Touch only `groop/**`; write P65-LOG.md/P65-REPORT.md; commit, do not merge.

## Context To Read First

- `src/groop/report.py`: `GroupProfile` (fields `key`, `sample_count`,
  `window_start_ts`, `window_end_ts`, `gauges`, `rates`), `report_to_jsonable`
  (the canonical figure source — the table MUST format from this dict or the
  same rounded values, not raw floats), `profile_to_jsonable`, `_round_float`,
  and the P61 `AssertionResult`/`assertion_result_to_jsonable` layer.
- `src/groop/cli.py`: `parse_report_args`/`_main_report` — how the report is
  emitted today (`print(format_report(...))`) and how P61 exit codes work.
- Exemplar to imitate: any existing fixed-width ASCII table renderer already in
  the repo (search `src/groop/` for column-formatting helpers, e.g. the banner
  or CPU-sparkline column code) — reuse an existing formatter idiom rather than
  hand-rolling a new one; if none fits, keep the new formatter pure and local.

## Requirements

1. Add `--format {json,table}` to `parse_report_args`, default `json`. Preserve
   the EXISTING `--json` flag semantics for backward compatibility: `--json` is
   equivalent to `--format json` and remains accepted; specifying both
   `--json` and `--format table` is a usage error (exit 2, clear message).
   Exactly one output form is produced per run.
2. `--format table` renders a deterministic fixed-width ASCII table: one section
   per `GroupProfile` (header line = group key + sample_count + window span),
   then a row per gauge/rate metric with columns `metric | p50 | p95 | max`.
   `null` stats render as a literal `-` (never `None`, never a crash). Numbers
   use the SAME 6-dp rounding as the JSON path (format from
   `report_to_jsonable`'s values so table and JSON never disagree).
3. When assertions (P61 `--assert`) are present, `--format table` appends an
   `ASSERTIONS` section: one row per result with columns
   `group | metric | stat | op | threshold | actual | PASS/FAIL | reason`.
   The exit-code contract is UNCHANGED from P61 (0/1/2) and independent of
   `--format` — a breach still exits 1 whether output is json or table.
4. Table rendering lives in a NEW pure helper in `report.py`
   (e.g. `format_report_table(report_jsonable: dict) -> str`) taking the
   already-built jsonable dict (or the profile/assertion lists) and returning a
   string — no argparse, no file I/O, no profile recomputation. Independently
   unit-testable.
5. ASCII only; deterministic column widths (computed from the rendered cell
   contents, stable across runs for identical input); no ANSI color (this is a
   pipe-safe report, not the TUI). Trailing whitespace is stripped per line
   (must pass `git diff --check` and a test asserting no line has trailing
   spaces).

## Out Of Scope

- Any change to the JSON output shape, the gauge set, percentile method, exit
  codes, or `compute_profile`.
- The P64 `--baseline`/deltas block (that renders its own way under P64; if P64
  is merged first, table rendering of deltas may be a trivial follow-up but is
  NOT required here — scope only profiles + P61 assertions).
- TUI integration, color, paging, or interactive sort.
- Window auto-detection (P62), free-form metrics (P60).

## Acceptance Oracles / Tests (numbered, adversarial; fixture-recording based)

1. `--format table` on the gstammtisch fixture prints a table containing each
   expected group key and the `ram` metric row; assert the p50/p95/max cells
   equal the 6-dp values from the JSON path (parse the JSON run and cross-check
   the same numbers appear in the table) — fails if the table recomputes/rounds
   differently.
2. A `null` rate stat renders as `-` (construct/reuse a single-frame fixture so
   a rate stat is null); assert `-` present and `None` absent.
3. `--format table --assert :ram:max<=1` (a breach) → exit 1 AND the table has
   an `ASSERTIONS` section with a `FAIL` row carrying the actual value; a
   passing assert → exit 0 with a `PASS` row.
4. `--json` and `--format table` together → exit 2 with a clear message.
5. Default (no `--format`, with `--json`) is byte-identical to today's JSON
   output (regression guard: the JSON path is untouched).
6. Column widths are deterministic: two runs on the same fixture produce
   byte-identical table output.
7. No rendered line has trailing whitespace (assert on the produced string).
8. At least one test drives the exact exit code + stdout via a real subprocess.

## Docs

- Update `README.md` (`groop report` paragraph — document `--format table` as
  the operator-eyes view; keep `--json` as the machine contract) and
  `docs/OPERATIONS.md` with a table example. Update `docs/ROADMAP.md`/
  `docs/STATUS.md` package entries.

## Gates

- Run focused tests (`groop/tests/test_report.py`) and the full suite with
  `-W error`; wrap the full-suite command in `timeout`; state in the REPORT
  which environment each result came from (report area needs no `zstandard`;
  note the 2 known zstandard skips if present).
