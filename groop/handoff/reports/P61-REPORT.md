# P61-REPORT — Steady-State Report Threshold Gating

## What Was Built

Added threshold gating to `groop report`: the new repeatable `--assert` flag
lets an operator assert bounds on the already-computed steady-state profile
figures and get a non-zero exit when a bound is breached. This enables
`groop report` to run as a pass/fail gate in CI-style scripts and the
gstammtisch stack measurement program.

### Files Changed

| File | Change |
|------|--------|
| `groop/src/groop/report.py` | Added `Assertion`, `AssertionResult` dataclasses; `parse_assert_spec` regex parser; `evaluate_assertions` pure function; `assertion_result_to_jsonable`; `_find_profile_metric` helper. Updated `report_to_jsonable` and `format_report` to accept optional assertions list. Updated module docstring. |
| `groop/src/groop/cli.py` | Added `--assert` (action="append", dest="assert_specs") to `parse_report_args`. Wired parsing and evaluation into `_main_report` with exit codes: 0 = all pass, 1 = breach, 2 = malformed/unknown. |
| `groop/tests/test_report.py` | Added `TestParseAssertSpec` (12 tests), `TestEvaluateAssertions` (10 tests), `TestReportAssertionCLI` (11 tests). Updated imports. |
| `groop/README.md` | Added `--assert` documentation and examples to the `groop report` paragraph. |
| `groop/docs/OPERATIONS.md` | Added threshold-gating examples to the report command section. |
| `groop/handoff/reports/P61-LOG.md` | New — work log. |

### Assertion Spec Format

```
--assert GROUP:METRIC:STAT<=VALUE
--assert GROUP:METRIC:STAT>=VALUE
```

- `GROUP` matches a profile `key` exactly (entity key or slice key)
- `METRIC` is a gauge or rate metric name present in the report
- `STAT` is one of `p50`, `p95`, `max`
- `VALUE` is a finite number (integer or float, including scientific notation)

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All assertions pass (or none given) |
| 1 | At least one assertion breached (genuine gate failure) |
| 2 | Malformed `--assert` spec, unknown STAT, missing `--json`, bad `--window`, unreadable file |

### Breach Rules

- Absent GROUP → exit 1, reason: "group not present in report"
- Absent METRIC → exit 1, reason: "metric not present in report"
- Null STAT (single-frame rate) → exit 1, reason: "stat is null (...)"
- Actual value violates threshold → exit 1, reason: "breached: actual op threshold"

### JSON Output

When assertions are given, the JSON output includes a top-level `"assertions"`
key with a sorted list of results (sorted by group, metric, stat, op for
byte-determinism). Floats are rounded to 6 decimal places. The profiles block
is unchanged.

```json
{
  "profiles": [...],
  "assertions": [
    {"group":"", "metric":"ram", "stat":"max", "op":"<=", "threshold":5000000000.0,
     "actual":4096000000.0, "passed":true},
    {"group":"", "metric":"ram", "stat":"max", "op":"<=", "threshold":100.0,
     "actual":4096000000.0, "passed":false, "reason":"breached: 4096000000.0 <= 100.0"}
  ],
  "metrics_version": 1
}
```

## Deviations from Handoff

None. All requirements are met.

## Contract Changes

None. Additive, package-private code only. No changes to CONTRACTS.md.

## Test Evidence

### Focused Tests (test_report.py)

```
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -v --tb=short
→ 91 passed in 2.96s
```

All 91 tests pass — 77 pre-existing + 12 `TestParseAssertSpec` + 10
`TestEvaluateAssertions` + 11 `TestReportAssertionCLI`.

Notable CLI subprocess tests that verify exact exit codes:
- `test_passing_bound_exit_0` — exit 0, assertions block present
- `test_breached_le_exit_1` — exit 1, actual value in JSON
- `test_breached_ge_exit_1` — exit 1, >= breach
- `test_absent_group_exit_1` — exit 1, "not present" reason
- `test_absent_metric_exit_1` — exit 1, absent metric
- `test_null_stat_exit_1` — exit 1, absent rate metric (single-frame fixture)
- `test_malformed_assert_exit_2` — exit 2, bad format
- `test_unknown_stat_exit_2` — exit 2, unknown STAT
- `test_multiple_asserts_one_fails_exit_1` — exit 1, mixed pass/fail
- `test_byte_determinism_two_runs` — identical bytes across runs
- `test_no_assert_no_change` — profiles unchanged when no --assert

### Full Suite

```
timeout 300 python3 -m pytest groop/tests/ -q --tb=short
→ 1037 passed, 2 skipped (zstandard), 1 warning in 122.90s
```

Environment: Python 3.14.6, linux/amd64, pytest 8.4.2, no zstandard extra.
The 2 skipped tests are zstandard-related (expected — the report area needs
no zstandard).

### Compilation

```
python3 -m py_compile groop/src/groop/report.py    # OK
python3 -m py_compile groop/src/groop/cli.py        # OK
```

### Whitespace

```
git diff --check → no issues
```

## Known Gaps / Open Items

- The null-stat CLI test (`test_null_stat_exit_1`) tests the absent-metric
  path (the gstammtisch-once fixture has no rates, so any rate assertion
  hits "metric not present" rather than "stat is null"). The actual null-stat
  scenario is covered by the unit test `TestEvaluateAssertions::test_null_stat_breach`.
- The `-W error` flag triggers a pre-existing pytest-asyncio deprecation
  warning (asyncio_default_fixture_loop_scope unset) that causes an
  INTERNALERROR. This is a pre-existing environment issue, not introduced
  by P61.

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
