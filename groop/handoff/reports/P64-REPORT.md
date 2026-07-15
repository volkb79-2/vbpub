# P64-REPORT — Informational Baseline Comparison

## What Was Built

A new `groop compare` subcommand and its backing `groop.compare` module that
diff two P88 `shape="summary"` query results (a *current* and a *baseline*) into
deterministic per-`(key, metric)` deltas. Per D-007 the comparison is purely
informational: it can emit configured pass/breach outcomes for automation but is
not a release gate and is not required by the operator scenario suite.

The module is a **pure consumer** of two already-computed `groop query
--shape summary --json` results. It never reads a recording, re-selects
entities, or re-aggregates frames — it operates only on the `rows`/`metrics`
cells already present in each JSON result.

### Files Changed

| File | Change |
|------|--------|
| `groop/src/groop/compare.py` | New. `Delta` outcome dataclass + `OUTCOME_*` constants; `compare_summaries` (pure entry point); `format_compare`/`compare_to_jsonable`/`delta_to_jsonable` (deterministic JSON); `CompareRule`/`parse_compare_rule`/`evaluate_compare_rules`/`compare_assertion_result_to_jsonable`; `compare_exit_code`/`combine_exit_codes` (P61 0/1/2 convention). |
| `groop/src/groop/cli.py` | Added `compare` dispatch, `parse_compare_args`, and `_main_compare` (loads two JSON files, runs comparison, evaluates `--assert` rules, returns 0/1/2). |
| `groop/tests/test_compare.py` | New. 48 tests mapped to oracles O1–O9 plus CLI usage/exit-code coverage and a real-P88-engine integration test. |
| `groop/handoff/reports/P64-LOG.md` | New — work log. |

### Typed Outcomes (never a division, coercion, or silent pass)

`ok`, `zero_zero`, `zero_baseline`, `missing`, `missing_current`,
`missing_baseline`, `redacted`, `semantic_mismatch`, `unsupported_semantic`,
`incomplete_coverage`, `reset_boundary`. `delta`/`pct` are only populated where
they are well-defined (`ok`, `zero_zero`, `zero_baseline` — and `pct` only when
the baseline is nonzero).

### Comparison Rule Format & Exit Codes

```
--assert KEY:METRIC:delta<=VALUE
--assert KEY:METRIC:pct>=VALUE
```

| Code | Meaning |
|------|---------|
| 0 | All rules pass (or none given) |
| 1 | Any breach — including a rule against a refused/undefined comparison, or a missing key/metric |
| 2 | Usage error — malformed rule/JSON, missing `--json`, unreadable file, incompatible `shape`/`projection`/`visibility` |

`combine_exit_codes` composes P61 and P64 codes with `2 > 1 > 0` precedence,
order-independently, so a P61 report assertion and a P64 baseline breach never
lose or reorder either gate's outcome.

## Oracle Coverage

| Oracle | Test class / test |
|--------|-------------------|
| O1 positive/negative absolute & pct delta | `TestDeltaMath` |
| O2 zero/zero typed, not divided | `TestZeroBaseline::test_zero_zero_is_typed_not_divided` |
| O3 zero baseline / nonzero current typed, not infinite | `TestZeroBaseline::test_zero_baseline_nonzero_current_is_typed_not_infinite` |
| O4 mismatched semantics/units refused | `TestSemanticMismatch` |
| O5 missing/redacted typed, never silent pass | `TestMissingAndRedacted` |
| O6 unequal coverage typed | `TestCoverage` |
| O7 P61/P64 exit codes combine deterministically | `TestExitCodeCombination` |
| O8 deterministic ordering across runs | `TestDeterminism` |
| O9 helper never reads/re-profiles frames | `TestNeverReadsFrames` |

## Deviations from Handoff

None. All required contracts met.

## Contract Changes

None. Additive, package-private code. No changes to `CONTRACTS.md`.

## Test Evidence

### Focused

```
PYTHONPATH=groop/src python -m pytest groop/tests/test_compare.py -q
→ 48 passed
```

### Full Suite (declared gate)

```
pip install -e 'groop[dev]'
python -m pytest groop/tests -q
→ 1666 passed in 193.69s (0 skipped)
```

### Compilation & Whitespace

```
python -m py_compile groop/src/groop/compare.py groop/src/groop/cli.py groop/tests/test_compare.py   # OK
git diff --check   # clean
```

## Known Gaps / Open Items

- `state_duration` is intentionally unsupported for delta math (no single
  comparable scalar) and returns `OUTCOME_UNSUPPORTED_SEMANTIC`.
- Human-readable rendering of the comparison is deferred to P65 (the shared
  query/report renderer); P64 emits deterministic JSON only.

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/whitespace recorded.
- [x] Known gaps documented.
- [x] Work committed on branch.
