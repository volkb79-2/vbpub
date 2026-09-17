# P101-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P101-query-coverage
**HEAD:** 7e445f74 (confirmed)
**Verdict:** **CHANGES_REQUIRED**

## Method

Read the P101 handoff, REPORT, SELFREVIEW, `query/semantics.py` source with
`nl -ba` line verification, existing `test_query.py`, the new 184-line test
file, and all referenced doctrine. Ran the exact declared `topos-suite` gate
twice (host bind, no rebuild), extracted branch-aware coverage JSON, and
mechanically verified `missing_lines == []` and `missing_branches == []` for
`query/semantics.py`. Verified all 12 `nl -ba` line mappings in the report
against actual source. Audited imports, helpers, assertions, duplicates,
global leakage, and test-count arithmetic via `collect-only` with and without
the new file.

## Independent gate verification (two xdist runs)

Run 1: **1984 passed, exit 0** in 64s. Run 2: **1984 passed, exit 0** in 45s.

```
CLOSED  query/semantics.py  stmts=180/180  br=74/74  ml=[]  mb=[]
```

**1/1 exact 100%. Parity confirmed.** O1 and O4 satisfied.

## Test quality audit

### Positive findings
- 12 tests (report says 12 — accurate). No parametrization.
- Test-count arithmetic: 1984 total − 1972 baseline = 12 new. Mechanically
  confirmed via `collect-only` with and without the file. ✓
- No duplicate names with `test_query.py`. ✓
- No global leakage: zero `monkeypatch`, `patch`, or module-level mutation. ✓
- No sleeps, no wall-clock timing, no random values, no host-proc reliance. ✓
- No `# pragma: no cover`, no product source edits. ✓
- No gate/dependency/evaluator changes. ✓
- 22 `assert` statements, 19 `==` (exact equality), 7 `is None`. No
  weak range assertions (`<=`, `>=`, `0 <= x <= 100`). ✓
- All 12 `nl -ba` line numbers in the report verified against actual source
  (line 112 → `return None`, line 121 → `return None`, etc.). ✓
- `git diff --check`: clean. ✓
- `pytest` import used only for `pytest.raises`. ✓

### Exact behavioral assertions per test

| Test | Assertions |
|------|-----------|
| `test_round_none` | `assert sem._round(None) is None` |
| `test_finite_number_rejects_non_numeric` | `assert sem._finite_number("not_a_number") is None` |
| `test_rate_pairs_skip_prior_without_raw` | `assert pairs == [(3, 200.0)]`, `assert resets == 0` |
| `test_rate_pairs_break_on_non_positive_ts_delta` | `assert pairs == []`, `assert resets == 0` |
| `test_counter_total_skip_no_raw` | `assert total == 0`, `assert intervals == 0`, `assert resets == 0` |
| `test_integral_empty_series` | `assert (i, s) == (0.0, 0.0)`, `assert (i2, s2) == (0.0, 0.0)` |
| `test_integral_skip_non_positive_dt` | `assert i == 5.0`, `assert s == 2.0` |
| `test_state_duration_skip_none_state` | `assert s.stats["states"] == {"idle": 1.0}`, `assert s.sample_count == 1` |
| `test_state_duration_skip_non_positive_dt` | `assert s.stats["states"] == {"b": 4.0}`, `assert s.sample_count == 1` |
| `test_state_duration_exceeds_max_states` | `pytest.raises(IncompatibleQueryError, match="64 distinct states")` |
| `test_state_key_boolean` | `assert sem._state_key(True) == "true"`, `assert sem._state_key(False) == "false"` |
| `test_state_key_float` | `assert result == "3.141593"`, `assert result2 == "0.0"` |

Every test has exact behavioral assertions. No assertion-free calls, no
non-None checks, no range-only assertions. O2 and O5 satisfied. ✓

## Findings

### F1 — P101-LOG.md missing (MEDIUM)
**Handoff step 10 requires:** "Write P101-LOG.md, P101-REPORT.md, and
P101-SELFREVIEW.md." The LOG file does not exist in
`topos/nyxloom-trove/reports/`. Only REPORT and SELFREVIEW are present.

The LOG is a required receipt per the handoff contract. Its absence is a
mechanical completeness defect.

**Repair oracle:** Create `P101-LOG.md` with the required content: baseline
gap slice, work log, commands/exits, collection arithmetic, and any BLOCKED
assessment.

### F2 — Self-review overclaims universal fail-before evidence (MEDIUM)
**File:** P101-SELFREVIEW.md:10-11

> "Each test has fail-before evidence (removing the target line causes test
> failure)."

This is a universal quantifier with zero receipts. The self-review provides
no mutation commit hashes, no before/after test runs, and no mechanical
evidence that any specific test was verified to fail against a deliberately
broken branch. The handoff O3 negative includes: "universal mutation/
fail-before claims are made without receipts."

This is the same pattern flagged in P100 (F11). The handoff's living PL4
doctrine (updated through P100) requires truthful narrowing when receipts
are absent.

**Repair oracle:** Either (a) narrow the claim to tests where evidence
exists and cite specific receipts, or (b) remove the universal claim and
state honestly that fail-before evidence was not mechanically collected.

### F3 — Unused helper functions and misleading docstring (LOW)
**File:** topos/tests/test_p101_query_coverage.py:5,16-28

The module docstring states: "Uses fixture patterns from test_query.py
(_g, _rr, _frame, collect_points)." However:

- `_g` (line 16) is defined but **never called** in any test
- `_rr` (line 20) is defined but **never called** in any test
- `_frame` (line 25) is defined but **never called** in any test
- `collect_points` is mentioned but **never used**

All 12 tests construct `_Point` objects directly using the `sem._Point`
constructor, bypassing the helper functions entirely. The helpers are dead
code and the docstring is misleading — it claims patterns are used when
none are.

**Repair oracle:** Either (a) remove the unused helpers and correct the
docstring, or (b) use the helpers in the tests they were designed for
(e.g., `_g(1.0)` instead of constructing `MetricValue` directly where
applicable).

### F4 — Report `nl -ba` column "Function" inaccurately lists helper name for `summarize` (LOW)
**File:** P101-REPORT.md:28-39

Lines 329, 333, and 337 are listed under "Function: summarize" but the
actual function is `summarize` calling `_summarize_state_duration`. The
column header says "Function" but the value `summarize` is the public
entry point — the lines are in private `_summarize_state_duration`.
Minor inaccuracy; the `nl -ba` line numbers are correct.

**Repair oracle:** Change "summarize" to "summarize → _summarize_state_duration"
or verify the exact function name from `inspect.getsourcelines`.

## Checks passed

- **1/1 exact 100%**: `query/semantics.py` empty `missing_lines` and
  `missing_branches` in two independent xdist gate runs. Parity confirmed. ✓
- **12 `nl -ba` line mappings**: All verified against actual source at this
  commit. ✓
- **12 tests, 1984 total**: Test-count arithmetic mechanically confirmed
  via `collect-only` with and without the new file. ✓
- **No duplicate names** with `test_query.py`. ✓
- **No global leakage**, no monkeypatch, no module mutation. ✓
- **No host-proc, sleep, random, wall-clock** reliance. ✓
- **No pragma, omit, product source, gate, dependency, evaluator changes**. ✓
- **No `engine.py` or other source changes** — diff confirms scope kept to
  tests and reports only. ✓
- **22 assertions, 19 exact (`==`), 7 `is None`, 0 range-only**. ✓
- **git diff --check**: clean. ✓

## Verdict

**CHANGES_REQUIRED.** The gate is correct — 1/1 exact 100% with parity and
12 well-asserted behavioral tests. The failures are in receipt completeness:
P101-LOG.md is missing (F1, handoff requirement) and the self-review claims
universal fail-before evidence without receipts (F2, same pattern as P100
F11). Two minor quality findings (F3 unused helpers, F4 function-name
inaccuracy) should also be addressed.

Concrete repairs provided for all four findings. The code and gate are
sound; the evidence package needs completion.


---

# Final sign-off — 2026-07-25 (commit 854aba70)

**Reviewer:** Reasonix (same persistent adversarial session)
**Range:** 5e5642cd..854aba70
**Verdict:** **APPROVED**

## Gate verification

Single full xdist gate run: **1984 passed, exit 0** in 64s.

```
PASS: semantics.py empty missing_lines and missing_branches
```

Focused P101 tests: 12 passed in 1.27s. `git diff --check`: clean.

## F1–F4 closure verification

| Finding | Status | Evidence |
|---------|--------|----------|
| **F1** (P101-LOG.md missing) | **FIXED** | LOG created with 31 lines: aborted-engine history, mechanical baseline, gate evidence, truthful "no mutation campaign" statement |
| **F2** (universal fail-before without receipts) | **FIXED** | Self-review now: "No mutation campaign was run, so this receipt makes no mutation or universal fail-before claim" |
| **F3** (unused helpers/imports) | **FIXED** | 16 lines removed: `_g`, `_rr`, `_frame` helpers and `Entity`, `EntityFrame`, `Frame`, `MetricValue` imports all deleted; docstring cleaned |
| **F4** (function name inaccuracy) | **FIXED** | Report rows 329/333/337 now say `_summarize_state_duration` |

## Evidence summary

- **1/1 exact 100%**: `query/semantics.py` — 180/180 stmts, 74/74 branches
- **1984 total, 1972 baseline, 12 new**: mechanically verified via `collect-only`
- **12 `nl -ba` line mappings**: all verified against source at this commit
- **12 tests**: all with exact behavioral assertions, no hollow/weak/duplicate
- **No mutation overclaim**: self-review truthfully admits no campaign was run
- **LOG complete**: documents aborted draft, baseline, gate evidence, discipline
