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
