# P64-LOG — Informational baseline comparison

## Work log

- Read handoff `topos-P64-report-baseline-regression-gate.md`, `docs/ROADMAP.md`,
  and the P88 (`topos query`) / P61 (`topos report --assert`) contracts it
  depends on. Confirmed D-007: baseline regression is informational, not a
  release gate.
- Added `topos/src/topos/compare.py` — a pure consumer of two P88
  `shape="summary"` query results. No recording read, no entity re-selection,
  no frame re-aggregation. Every degenerate case (zero baseline, absent/redacted
  value, semantic mismatch, incomplete coverage, counter reset) returns an
  explicit typed `OUTCOME_*` rather than dividing, coercing, or silently passing.
- Reused P61's 0/1/2 exit convention via `evaluate_compare_rules` /
  `compare_exit_code` / `combine_exit_codes`. A refused comparison or an
  undefined percentage is a **breach**, never a silent pass.
- Wired the `topos compare CURRENT BASELINE --json [--metric ...] [--assert ...]`
  subcommand into `cli.py` (dispatch + `parse_compare_args` + `_main_compare`).
- Added `topos/tests/test_compare.py` — 48 tests mapped to oracles O1–O9 plus
  CLI exit-code coverage and an integration test against a genuine P88 query
  engine `Result.to_jsonable()`.

## Gate evidence

- Focused: `PYTHONPATH=src python -m pytest tests/test_compare.py -q` → 48 passed.
- Full suite (declared gate): `pip install -e topos[dev]` then
  `python -m pytest topos/tests -q` → **1666 passed, 0 skipped** in 193.7s.
- `py_compile` on compare.py / cli.py / test_compare.py → OK.
- `git diff --check` on the P64 diff → clean.

## Deviations / blocks

None. All named contracts met; no `BLOCKED` conditions and no escalation
triggers hit.
