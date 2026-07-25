# P102-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P102-query-validation-coverage
**HEAD:** 446b7a3e (confirmed)
**Verdict:** **CHANGES_REQUIRED**

## Method

Read the P102 handoff (including the literal 22-line and 20-pair acceptance
sets), REPORT, SELFREVIEW, LOG, `engine.py` source with `nl -ba` verification
through line 330, existing `test_query.py`, and the new 141-line test file.
Ran the exact declared `topos-suite` gate twice (host bind, no rebuild),
extracted branch-aware coverage JSON, and applied the handoff's literal
checker: intersection of `missing_lines` with the 22-line set AND intersection
of `missing_branches` with the 20-pair set, both mechanically required empty.
Compared complete engine.py executed/missing sets for parity. Audited every
test for the specific controller suspicions plus standard quality checks.

## Independent gate verification (two xdist runs)

Run 1: **2003 passed, exit 0** in 65s. Run 2: **2003 passed, exit 0** in 66s.

Literal checker (handoff's exact 22-line + 20-pair sets):
```
PASS: All 22 lines and 20 arcs CLOSED
```
Parity confirmed — complete engine.py executed/missing sets identical both
runs. O1 and O4 satisfied.

Remaining engine gaps (all post-validation, line 392+): 17 lines, 19 branches
— correctly belong to P103. No whole-engine-exact claim made. ✓

Test-count arithmetic: 2003 total − 1984 baseline = 19 new. Mechanically
verified via `collect-only` with and without the file. ✓

## Controller suspicion verification

### Suspicion 1 — Arc `148->156` mislabeled as `[155,156]`: CONFIRMED
**File:** P102-REPORT.md:37

The handoff declares arc `148->156` (line 20 of the handoff). The report
labels this as line `156 | [155,156]`. Source verification:

```python
148:             elif isinstance(m, dict):        # False branch → 155
149:                 extra = set(m) - {...}
150:                 if extra:
151:                     raise UnknownFieldError(...)
152:                 if "name" not in m:
153:                     raise InvalidQueryError(...)
154:                 metrics.append(MetricRef(...))
155:             else:                              # → 156
156:                 raise InvalidQueryError(...)
```

The actual coverage arc is `148→156` (False branch of `elif isinstance`).
The report's `[155,156]` is the wrong arc identifier. The literal checker
confirms `(148,156)` is the arc that was closed, not `(155,156)`.

**Repair oracle:** Change report line 37 from `156 | [155,156]` to
`156 | [148,156]`.

### Suspicion 2 — Line 174 expected value "passes": CONFIRMED
**File:** P102-REPORT.md:40

The report says expected output is "passes." The test
`test_from_dict_sort_as_dict` (line 50-54) actually asserts:
```python
assert q.sort is not None
assert q.sort.metric == "ram"
```

"passes" is not an expected value — it describes the test outcome, not
the behavioral assertion. O3 requires "an exact parsed value or typed
error."

**Repair oracle:** Change "passes" to "Query built, sort.metric=='ram'"
or similar exact observable.

### Suspicion 3 — Sort string asserts only `q.sort is not None`: CONFIRMED
**File:** topos/tests/test_p102_query_validation.py:132-135

```python
def test_from_dict_sort_str_branch(self):
    q = Query.from_dict({"shape": "summary", "metrics": ["ram"], "sort": "ram"})
    assert q.sort is not None
```

This test exercises line 172 (sort as str → `_parse_sort_token`) which
is NOT in the handoff's 22-line acceptance set. It's extra coverage.
However, the assertion `q.sort is not None` is a non-None-only check per
O2 negative ("assert only non-None/ranges/calls"). The test should assert
at minimum `q.sort.metric == "ram"` to prove the sort token was parsed
correctly.

**Repair oracle:** Add `assert q.sort.metric == "ram"` to prove the
string was parsed into a SortSpec with the correct metric.

### Suspicion 4 — Two sort-extra-field tests are duplicates: CONFIRMED
**File:** topos/tests/test_p102_query_validation.py:56-59, 137-141

- `test_from_dict_sort_extra_field` (line 56): sort dict with `"unknown": 1`
- `test_from_dict_sort_extra_fields` (line 137): sort dict with `"bad": 1`

Both assert `pytest.raises(UnknownFieldError, match="unknown sort field")`.
Both exercise the same branch (line 175, `if s_extra:`). The second test
adds no additional coverage — it's a redundant duplicate. It also adds
fields `"order": "asc"` which doesn't change behavior.

**Repair oracle:** Remove `test_from_dict_sort_extra_fields` (the duplicate).

### Suspicion 5 — `IncompatibleQueryError` unused: CONFIRMED
**File:** topos/tests/test_p102_query_validation.py:12

`IncompatibleQueryError` is imported but never referenced in any test.
Dead import.

**Repair oracle:** Remove from the import line.

### Suspicion 6 — `Caps` imported at top level but only used locally: CONFIRMED
**File:** topos/tests/test_p102_query_validation.py:13

`Caps` is imported on line 13 from `topos.query` but every use of `Caps`
in tests uses a local `from topos.query.engine import Caps` (lines 82,
110, 117). The top-level import is dead.

**Repair oracle:** Remove `Caps` from the line 13 import.

### Suspicion 7 — Receipts give counts instead of literal sets: CONFIRMED
**File:** P102-REPORT.md:10-13

The report says "Target lines (22): 22 missing → 0 missing" — this is a
count, not the literal set. The handoff O3 requires: "the receipt checks
the literal declared line and branch sets." The report should reproduce
the exact 22-line and 20-pair sets from the handoff, show the before/after
intersection with `missing_lines`/`missing_branches`, and prove each is
empty.

The LOG (P102-LOG.md) is even thinner: "2003 passed, exit 0, parity PASS.
P102 target: 22 lines + 20 branches CLOSED." No literal sets, no
before/after evidence, no `nl -ba` binding. The handoff step 9 requires
"literal before/after sets, exact commands/exits, truthful negative
evidence, collection arithmetic, assertion audit, and parity."

**Repair oracle:** Expand P102-LOG.md and P102-REPORT.md to include the
literal 22-line and 20-pair sets with before/after evidence from the
coverage JSON, as required by O3 and handoff step 9.

## Additional findings

### F8 — Focused P102 tests pass (positive)
19 tests, 19 passed in 1.72s. All test `Query.from_dict` with real inputs.
19 `pytest.raises` or `assert` calls. No assertion-free tests. No sleeps,
no host-proc, no monkeypatch. ✓

### F9 — No semantics.py or gate changes (positive)
Diff confirms zero changes to `query/semantics.py`, `nyxloom.toml`,
`coverage_gate.py`, or `pyproject.toml`. ✓

## Summary of findings

| Finding | Severity | Description |
|---------|----------|-------------|
| **F1** | MEDIUM | Arc `148->156` mislabeled as `[155,156]` in report |
| **F2** | LOW | Line 174 expected value says "passes" instead of exact observable |
| **F3** | LOW | `IncompatibleQueryError` imported but unused |
| **F4** | LOW | `Caps` imported at top level but only used via local import |
| **F5** | LOW | `test_from_dict_sort_extra_fields` duplicates `test_from_dict_sort_extra_field` |
| **F6** | LOW | `test_from_dict_sort_str_branch` has weak `is not None` assertion |
| **F7** | MEDIUM | Report/LOG give counts instead of literal before/after residual sets per O3 |

## Verdict

**CHANGES_REQUIRED.** The gate is correct — all 22 declared lines and 20
declared branch pairs are closed with parity confirmed. The remaining engine
gaps are correctly deferred to P103. The failures are in evidence quality:
the report mislabels an arc (F1), uses "passes" instead of exact observables
(F2), omits literal before/after sets (F7), and the LOG is too thin to meet
handoff step 9. Three dead imports (F3, F4), one duplicate test (F5), and
one weak assertion (F6) are quality findings that should be addressed.

Concrete mechanical repairs provided for all seven findings.


---

# Repair re-review — 2026-07-25 (commit 7fb19b47)

**Re-reviewer:** Reasonix (same persistent adversarial session)
**Range:** 4100b740..7fb19b47
**Verdict:** **CHANGES_REQUIRED** (F2 and F7 remain partially open)

## Independent gate verification

Single full xdist gate run: **2002 passed, exit 0** in 66s.

```
Literal checker: PASS — All 22 lines + 20 arcs CLOSED
Test functions: 18, collected cases: 18, total: 2002 (= 1984 + 18)
Focused tests: 18 passed in 1.47s
git diff --check: clean
Default sort order verified: sort.order == "desc" (correct)
```

## F1–F7 closure status

| Finding | Status | Evidence |
|---------|--------|----------|
| **F1** (arc 148->156 mislabeled) | **FIXED** | Report now `[148,156]`, docstring updated |
| **F2** (line 174 "passes") | **PARTIAL** | Row 173 now says `Query built, sort.metric='ram'` but row 174 still says just `Query built` |
| **F3** (IncompatibleQueryError unused) | **FIXED** | Removed from imports |
| **F4** (Caps top-level unused) | **FIXED** | Moved to `from topos.query.engine import Caps` |
| **F5** (duplicate sort test) | **FIXED** | `test_from_dict_sort_extra_fields` removed |
| **F6** (weak sort assertion) | **FIXED** | Now asserts `sort.metric == "ram"` and `sort.order == "desc"` |
| **F7** (literal sets vs counts) | **PARTIAL** | REPORT has literal before/after sets; LOG still only counts/symbolic ∅ |

### F2 detail — line 174 expected still "Query built"

**File:** P102-REPORT.md row 174

The report row for arc `[173,174]` (sort as dict entry) still says `Query built`
in the Expected column. The test `test_from_dict_sort_as_dict` covers both arcs
`[171,173]` and `[173,174]` simultaneously with assertions `q.sort is not None`
and `q.sort.metric == "ram"`. Row 173 now correctly shows the observable; row
174 should match or cross-reference it.

**Repair:** Change row 174 Expected from `Query built` to
`Query built, sort.metric='ram'` (same observable as row 173, since the same
test covers both arcs).

### F7 detail — LOG still omits literal sets

**File:** P102-LOG.md:7-13

The log says "22 lines + 20 branch pairs all present in baseline gate JSON"
and "Both runs: residual = ∅." The handoff step 9 requires "literal
before/after sets" in the LOG. The REPORT now satisfies this; the LOG should
at minimum reproduce the set declarations or reference the REPORT.

**Repair:** Add the literal 22-line and 20-pair sets to the LOG (can be a
one-line reference to REPORT's before/after tables, e.g. "See REPORT for
literal sets" or inline them).

## Other checks — all passed

- **F3**: `IncompatibleQueryError` removed from imports ✓
- **F4**: `Caps` removed from top-level, used only via `from topos.query.engine` ✓
- **F5**: Duplicate `test_from_dict_sort_extra_fields` removed ✓
- **F6**: Sort-str test now asserts `sort.metric == "ram"` and `sort.order == "desc"` ✓
- **Imports consolidated**: `_validate`, `Caps`, `_parse_metric_token` now imported once at module level instead of locally per-test ✓
- **No whole-engine claim**: Remaining gaps (17 lines, 19 branches, line 392+) correctly deferred to P103 ✓
- **No mutation/fail-before overclaims** ✓
- **No pragma, product edit, sleep, host-proc** ✓
- **2002 total, 18 functions = 18 cases**: mechanically verified ✓

## Verdict

**CHANGES_REQUIRED.** Five of seven findings (F1, F3, F4, F5, F6) are fully
closed. F2 (line 174 expected still says "Query built") and F7 (LOG lacks
literal sets) are partially addressed — each needs one additional line of
correction. The gate is sound — 22/22 lines and 20/20 arcs mechanically
verified with parity.
