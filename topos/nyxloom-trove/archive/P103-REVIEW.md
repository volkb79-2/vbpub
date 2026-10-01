# P103-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P103-query-engine-coverage
**HEAD:** d7dfcd00 (confirmed)
**Verdict:** **CHANGES_REQUIRED**

## Method

Read P103 handoff, REPORT, SELFREVIEW, `engine.py` source through all
remaining lines, existing query tests (P101, P102, test_query.py), and the
new 197-line test file. Ran the exact `topos-suite` gate twice (host bind,
no rebuild), applied both the literal 17-line/19-pair residual checker AND
the complete whole-file engine checker. Compared complete executed/missing
sets for parity. Audited every test against exact controller concerns plus
standard quality checks.

## Independent gate verification (two xdist runs)

Run 1: **2018 passed, exit 0** in 66s. Run 2: **2018 passed, exit 0** in 68s.

```
Literal residual intersection:  lines=[]  arcs=[]
Whole-file missing:            lines=[]  branches=[]
PASS: whole-file 100%, literal residual closed
```

**PARITY CONFIRMED** — identical complete engine.py sets both runs.
O1 and O4 satisfied: whole-file `missing_lines=[]` and `missing_branches=[]`.

## Findings

### F1 — P103-LOG.md missing (MEDIUM)
**Handoff step 9 requires:** "Write P103-LOG.md, P103-REPORT.md, and
P103-SELFREVIEW.md." The LOG file does not exist. Only REPORT and
SELFREVIEW are present.

**Repair:** Create P103-LOG.md with baseline/final sets, gate evidence,
collection arithmetic, and assertion audit per handoff step 9.

### F2 — Report uses symbolic ∅ instead of literal intersections (MEDIUM)
**File:** P103-REPORT.md:20-22

The report shows `∅/∅` for both runs. O3 requires: "prints empty literal
intersections and whole-file missing sets for two runs." The handoff step 9
requires "literal before/after sets." The P102 precedent (settled at 855b5a38)
established that literal intersections must be printed explicitly, e.g.:
```text
run 1 missing target lines: []
run 1 missing target pairs: []
run 2 missing target lines: []
run 2 missing target pairs: []
```

**Repair:** Replace the `∅/∅` row with four explicit empty-list lines plus
the whole-file `missing_lines: []` / `missing_branches: []` for both runs.

### F3 — `_run_current` imported but unused (LOW)
**File:** topos/tests/test_p103_engine_coverage.py:13

`_run_current` is imported from `topos.query.engine` but never called in
any test. Dead import.

**Repair:** Remove from imports.

### F4 — `_in_slice` cycle-safe path untested (MEDIUM)
**File:** topos/tests/test_p103_engine_coverage.py:40-42

The test `test_in_slice_not_found` exercises the "parent chain exhausts"
path (parent=None). The handoff explicitly names "cycle-safe slice
traversal" as a required behavior (handoff §4 line 104). The function
`_in_slice` has built-in cycle detection (`current not in seen`) that is
never exercised by any test. The cycle-detection path (where `seen`
contains `current`, breaking the loop) is unreached coverage.

```python
def _in_slice(key, slice_key, parents):
    seen = set()
    current = key
    while current is not None and current not in seen:  # cycle-safe guard
        seen.add(current)
        parent = parents.get(current)
        if parent == slice_key:
            return True
        current = parent
    return False
```

**Repair:** Add `test_in_slice_cycle_safe`:
```python
def test_in_slice_cycle_safe():
    parents = {"a": "b", "b": "a", "c": "a"}
    assert _in_slice("c", "a", parents) is False  # cycle-safe exit
```

### F5 — Weak assertions across 8 tests (HIGH)

The following tests exercise critical engine behavior but assert only
shallow properties rather than exact observable outputs. O2 requires
"exact public result" and the negative includes "non-None/ranges/calls."

| Test | Line | Current assertion | Missing verification |
|------|------|-------------------|---------------------|
| `test_format_result_pretty` | 46-51 | `"\n" in result` | Pretty JSON should have indentation; assert `result.startswith("{\n  ")` or that every line after the first starts with whitespace |
| `test_summary_cells_available_visibility` | 54-62 | `"ram" in cells["e"]` | Should assert exact cell structure: `cells["e"]["ram"]["value"]`, visibility filtering removing unavailable metrics |
| `test_project_hierarchy_no_sort` | 70-78 | `len(rows) == 3` | Should assert `rows[0]["key"]`, `rows[0]["depth"]`, `rows[0]["children"]` — exact hierarchy structure |
| `test_project_hierarchy_with_sort` | 187-197 | `len(rows) == 3` | Should assert descending order by value: `rows[0]["value"] > rows[1]["value"]` or exact key ordering `["c", "a", "b"]` |
| `test_run_raw_no_entity` | 98-108 | `len(r.rows) >= 1` | Weak range. Should assert exact row count or that the absent-entity frame produces no point for key1 |
| `test_run_raw_point_cap` | 130-137 | `truncation["truncated"] is True` | Should also assert `truncation["reason"] == "max_points"` and `truncation["dropped_points"]` count |
| `test_run_raw_no_points` | 154-163 | `isinstance(r.meta.get("truncation"), dict)` | Should assert `len(r.rows)` for entity with all-None metrics (rows should be 0 or points empty) |
| `test_run_raw_both_caps` | 166-175 | `meta.get("also") == "max_rows"` | Should also assert `truncated is True`, `reason`, `dropped_rows` — the test verifies only the `also` field |

The handoff §5 explicitly forbids "weak ranges" and "non-None-only
assertions." These eight tests collectively have 8 assertions across 8
tests — exactly one assertion each, and most are shallow.

### F6 — `test_run_raw_raw_field` uses `for/else/assert False` pattern (LOW)
**File:** topos/tests/test_p103_engine_coverage.py:140-151

The test iterates points looking for a `raw` field, then uses `assert False`
with a message when not found. This is functional but cleaner to use
`any(p.get("raw") is not None for p in r.rows[0]["points"])` or assert
a specific point index.

**Repair:** Replace with `assert any(p.get("raw") is not None for p in r.rows[0]["points"])`.

## Checks passed

- **Whole-file 100%**: engine.py `missing_lines=[]` and `missing_branches=[]`
  both runs. ✓
- **17/17 lines + 19/19 arcs**: literal residual closed. ✓
- **2018 total, 16 functions = 16 cases**: mechanically verified. ✓
- **No product source edits**: Diff confirms zero changes under
  `src/topos/**`. ✓
- **No duplicates** with P101/P102/test_query.py. ✓
- **No global leaks**, no monkeypatch, no module mutation. ✓
- **No host-proc, sleep, random**: verified. ✓
- **No pragma, omit, gate, dependency changes**: verified. ✓
- **No mutation/fail-before overclaim**: SELFREVIEW makes no such claim. ✓
- **git diff --check**: clean. ✓

## Verdict

**CHANGES_REQUIRED.** The gate is correct — whole-file engine.py at exact
100% with parity across two runs. The failures are in evidence completeness
(F1 missing LOG, F2 symbolic ∅ instead of literal intersections) and test
assertion quality (F3 unused import, F4 missing cycle-safety test, F5 eight
shallow assertions, F6 awkward assertion pattern).

F1 and F2 are mechanical evidence fixes (add LOG, expand report ∅ to
explicit empty lists). F4 requires one additional test for cycle-safe
behavior. F5 requires strengthening 8 of 16 tests with exact structural or
ordering assertions. F3 and F6 are minor cleanup.

Concrete repair oracles provided for all six findings.


---

# Repair re-review — 2026-07-25 (commit 1e347e72)

**Re-reviewer:** Reasonix (same persistent adversarial session)
**Range:** 08030c3a..1e347e72
**Verdict:** **APPROVED** (F1–F6 closed; residual quality notes below)

## Independent gate verification

Full xdist gate: **2019 passed, exit 0** in 69s. Focused tests: 17 passed in 1.50s.

```
Literal: lines=[]  arcs=[]
Whole:   lines=[]  branches=[]
PASS: whole-file 100%
```
Test counts: 17 functions = 17 collected cases, 2019 total (2002 + 17).
`git diff --check`: clean.

## F1–F6 closure verification

| Finding | Status | Evidence |
|---------|--------|----------|
| **F1** (LOG missing) | **FIXED** | P103-LOG.md created with 30 lines: baseline, repairs, gate table, literal residual check |
| **F2** (∅ symbols) | **FIXED** | REPORT now prints explicit `run 1 missing target lines: []` etc. for both runs |
| **F3** (_run_current unused) | **FIXED** | Removed from imports |
| **F4** (cycle-safe untested) | **FIXED** | `test_in_slice_cycle_safe` tests `a→b→a` cycle with `_in_slice("a", "missing", parents)` |
| **F5** (8 weak assertions) | **FIXED** | All 8 strengthened (see detail below) |
| **F6** (for/else/assert False) | **FIXED** | Replaced with `any(p.get("raw") is not None ...)` |

### F5 strengthening detail

| Test | Before | After |
|------|--------|-------|
| `format_result_pretty` | `"\n" in result` | `result == json.dumps(r.to_jsonable(), indent=2)` — exact pretty JSON |
| `summary_cells_available` | `"ram" in cells["e"]` | `semantic=="gauge"`, `count==2`, `min==1.0`, `max==1.0` |
| `project_hierarchy_no_sort` | `len(rows) == 3` | + `key=="a"`, `depth==0`, `path`/`metrics` presence, child `depth==1`/`path==["a"]` |
| `project_hierarchy_with_sort` | `len(rows) == 3` | + descending key order: `["a", "c", "b"]` |
| `run_raw_no_entity` | `len(r.rows) >= 1` | `len==1`, `key=="key1"`, `len(points)==1` |
| `run_raw_point_cap` | `truncated is True` | + `reason=="max_points"`, `len(rows)==1`, `len(points)==3` |
| `run_raw_no_points` | `isinstance(truncation, dict)` | `len(rows)==1`, `len(points)==1` |
| `run_raw_both_caps` | `also=="max_rows"` | + `truncated is True`, `reason=="max_points"`, `len(rows)==1`, `len(points)==3` |

## Residual quality notes (non-blocking)

The following are controller observations that do not block approval (gate is
correct, F1–F6 are closed), but should be addressed in a follow-up cleanup:

### R1 — REPORT "before" table says "17+ lines, 19+ branches"
**File:** P103-REPORT.md:13

The before residual was exactly 17 lines and 19 branch pairs (documented
in the handoff and the "Before residual sets" section of the same report).
The "17+/19+" notation implies there were additional gaps beyond the
literal set, which is inaccurate — P102 closed all validation gaps, leaving
exactly the 17/19 P103 residual.

**Repair:** Change to "17 lines, 19 branches" (exact counts).

### R2 — LOG says "plus other gaps"
**File:** P103-LOG.md:4-5

"engine.py: 17 lines + 19 branch pairs residual (P103 set), plus other
gaps." The "plus other gaps" is inaccurate — P102 closed the validation
tranche completely, leaving exactly the 17/19 residual. There were no
other gaps in engine.py at P103 baseline.

**Repair:** Remove "plus other gaps."

### R3 — `test_run_raw_no_points` is misnamed
**File:** topos/tests/test_p103_engine_coverage.py:155-163

The name implies no points are produced, but the test proves a point EXISTS
(`len(points) == 1`). The comment correctly explains: "entity exists,
metric exists, but value is None -> point with None value." The name
should match.

**Repair:** Rename to `test_run_raw_none_value_point` or similar.

### R4 — `test_enforce_byte_cap_prior_truncation` asserts only 2 of 5 fields
**File:** topos/tests/test_p103_engine_coverage.py:82-88

The actual truncation dict contains 5 keys: `truncated`, `policy`, `reason`,
`dropped_rows`, `also`. The test asserts only `truncated` and `also`.
Missing `policy`, `reason` (which is `"max_bytes"` in this path), and
`dropped_rows`.

**Repair:** Add `assert trunc["reason"] == "max_bytes"` and
`assert trunc["dropped_rows"] == 1`.

### R5 — `test_summary_cells_available_visibility` asserts 4 of 8 cell fields
**File:** topos/tests/test_p103_engine_coverage.py:69-76

The cell dict contains 8 keys: `count`, `max`, `mean`, `min`, `p50`, `p95`,
`sample_count`, `semantic`. The test asserts only `semantic`, `count`,
`min`, `max`. Missing `mean`, `p50`, `p95`, `sample_count`.

**Repair:** Add assertions for the remaining fields or document why they
are intentionally omitted.

## Verdict

**APPROVED.** All six original findings (F1–F6) are closed. Whole-file
engine.py at exact 100% with parity across two runs. 17 tests with
strengthened exact assertions. Five residual quality notes (R1–R5) are
non-blocking documentation and assertion-completeness improvements that
should be addressed in a follow-up cleanup.


---

# Final sign-off — 2026-07-25 (commit 52b49d09)

**Reviewer:** Reasonix (same persistent adversarial session)
**Range:** 6e0de6a7..52b49d09
**Verdict:** **APPROVED**

## Gate verification

Full xdist gate: **2018 passed, exit 0** in 69s. Focused tests: 16 passed in 1.23s.

```
Literal: lines=[]  arcs=[]
Whole:   lines=[]  branches=[]
PASS: whole-file 100%
```
16 functions = 16 collected cases, 2018 total (2002 + 16). `git diff --check`: clean.

## R1–R5 and controller extras — all closed

| Note | Status | Evidence |
|------|--------|----------|
| **R1** (17+ wording) | **FIXED** | REPORT: "17 lines, 19 branches" (exact, no +) |
| **R2** (plus other gaps) | **FIXED** | LOG: "exactly the P103 set: 17 lines and 19 branch pairs" |
| **R3** (misnamed no-points) | **FIXED** | `test_run_raw_no_points` removed entirely |
| **R4** (byte-cap partial) | **FIXED** | Full 5-field dict: `truncated`, `policy`, `reason`, `dropped_rows`, `also` + `result == []` |
| **R5** (summary partial) | **FIXED** | Full 8-field cell dict: `semantic`, `sample_count`, `count`, `min`, `mean`, `p50`, `p95`, `max` + `resets == 0` |
| LOG literal baseline | **FIXED** | Prints exact 17-line/19-pair baseline sets |
| LOG whole-file per-run | **FIXED** | `Run 1 whole file: missing_lines=[] missing_branches=[]` (both runs) |
| REPORT whole-file per-run | **FIXED** | `run 1 whole-file missing_lines: []` / `missing_branches: []` (both runs) |

## Assertion completeness audit

Every test now uses exact structural equality:

| Test | Assertion style |
|------|----------------|
| `test_in_slice_not_found` | Exact boolean |
| `test_in_slice_cycle_safe` | Exact boolean (cycle-safe path) |
| `test_format_result_pretty` | Exact JSON string via `json.dumps(..., indent=2)` |
| `test_summary_cells_available_visibility` | Full 8-field cell dict equality + `resets == 0` |
| `test_cell_stat_none` | Exact None |
| `test_project_hierarchy_no_sort` | Full row list with `key`/`depth`/`path`/`metrics` |
| `test_enforce_byte_cap_prior_truncation` | Full 5-field truncation dict + `result == []` |
| `test_run_current_empty` | `r.rows == []` |
| `test_run_raw_no_entity` | Exact rows list with points + truncation dict |
| `test_run_raw_no_metric` | `r.rows == []` + truncation dict |
| `test_run_raw_hidden_visibility` | `r.rows == []` + truncation dict |
| `test_run_raw_point_cap` | Exact 3-point list + 4-field truncation dict |
| `test_run_raw_raw_field` | Exact point with `raw: 500` + truncation dict |
| `test_run_raw_both_caps` | Exact row/points + 6-field truncation dict with `also` |
| `test_subtree_aggregate_child_none` | Exact float |
| `test_project_hierarchy_with_sort` | Full 3-row list with `subtree` dicts (metric/policy/additive/value) |

## Verdict

**APPROVED.** Whole-file engine.py at exact 100% statements and branches.
All 16 tests use exact structural equality — no weak ranges, no non-None
checks, no len-only assertions. All R1–R5 and controller extra concerns
are closed. LOG and REPORT have literal baseline sets, per-run whole-file
empty lists, and exact 17/19 wording. Evidence package is complete and truthful.
