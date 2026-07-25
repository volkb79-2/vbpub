# P97-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P97-coverage-quickwins
**Range:** 68ead1d0..2de9bec2
**Verdict:** **CHANGES_REQUIRED**

## Summary

The handoff O1 requires every targeted source module at exactly 100% statements
and 100% branches in the full parallel gate. The gate coverage JSON shows only
**6 of 16** targets meet this bar. The report overclaims 8 closed (including
one that is verifiably not at 100%) and there are ineffective tests that do not
cover the branches they claim to target. The BLOCKED rule was not invoked, but
the completion condition is objectively unmet.

---

## O1: Target coverage — **FAILED**

Source: full-gate `pytest -n auto --cov=topos/src/topos --cov-branch`
inside tester-unified at HEAD 2de9bec2 (1807 passed, exit 0).

```
CLOSED  collect/zswapmath.py       stmts= 13/ 13 (100.0%)  br=  6/  6 (100.0%)
GAP     collect/dockerjoin.py      stmts=106/107 ( 99.1%)  br= 43/ 44 ( 97.7%)
GAP     collect/collector.py       stmts=242/245 ( 98.8%)  br= 94/ 98 ( 95.9%)
GAP     model.py                   stmts=135/136 ( 99.3%)  br= 31/ 32 ( 96.9%)
GAP     registry.py                stmts= 50/ 51 ( 98.0%)  br= 19/ 20 ( 95.0%)
CLOSED  procs/identity.py          stmts= 18/ 18 (100.0%)  br=  0/  0 (100.0%)
CLOSED  procs/sensitivity.py       stmts= 17/ 17 (100.0%)  br=  8/  8 (100.0%)
CLOSED  procs/owners.py            stmts= 41/ 41 (100.0%)  br=  8/  8 (100.0%)
CLOSED  ui/keys.py                 stmts=  5/  5 (100.0%)  br=  0/  0 (100.0%)
GAP     ui/damon_control.py        stmts= 32/ 33 ( 97.0%)  br=  0/  0 (100.0%)
GAP     ui/sparkline.py            stmts= 45/ 46 ( 97.8%)  br= 21/ 22 ( 95.5%)
GAP     record/ring.py             stmts= 98/ 98 (100.0%)  br= 25/ 26 ( 96.2%)
CLOSED  inspect_files/plan.py      stmts= 49/ 49 (100.0%)  br=  8/  8 (100.0%)
GAP     damon/paddr.py             stmts= 48/ 50 ( 96.0%)  br= 10/ 12 ( 83.3%)
GAP     actions/preview.py         stmts= 38/ 38 (100.0%)  br=  9/ 10 ( 90.0%)
GAP     daemon/component_health.py stmts=145/146 ( 99.3%)  br= 25/ 26 ( 96.2%)
```

**6 targets CLOSED** (not 8 as the report claims). **10 targets have gaps.**

### Remaining gaps by target

| Target | Missing lines | Missing branches | Notes |
|--------|:---:|:---:|-------|
| dockerjoin.py | [41] | [[39,41]] | `default_docker_inspect` return-None branch |
| collector.py | [315, 316, 386] | [[165,167], [314,315], [354,386], [427,429]] | 4 branch pairs uncovered |
| model.py | [113] | [[112,113]] | `metric_from_jsonable` invalid-type raise |
| registry.py | [279] | [[278,279]] | `parse_metrics_selector` empty-kept raise |
| damon_control.py | [52] | — | Zero tests added for this target |
| sparkline.py | [73] | [[72,73]] | Truncation branch (result > width) |
| ring.py | — | [[63, -60]] | early-exit branch in `last()` |
| paddr.py | [82, 85] | [[81,82], [84,85]] | Sysfs path branches |
| preview.py | — | [[112,121]] | Admin-path branch |
| component_health.py | [56] | [[51,56]] | `_truncate_utf8` fallback decode-error |

---

## Findings

### F1 — O1 contract violated (BLOCKER)
**Severity: BLOCKER**
**File:** P97-REPORT.md:12–29, gate coverage JSON

The handoff states: "The package is complete only when every named target
reaches exact 100% statement and branch coverage in the full xdist gate."
O1: "every targeted source module is reported at exactly 100% statements
and 100% branches."

Only 6 of 16 targets meet this. The handoff's escalate_if triggers do not
fire (the branches are reachable, just infrastructure-dependent), but the
plain completion condition is mechanically false. The implementer treated
"partial" as acceptable without a valid BLOCKED reason.

**Repair oracle:** Either (a) close all remaining gaps on all 16 targets,
or (b) invoke BLOCKED with a valid escalate_if reason for each remaining
gap, or (c) file a D-NNN decision to split the handoff into achievable
and infrastructure-dependent sub-packages.

### F2 — Report overclaims closed targets (BLOCKER)
**Severity: BLOCKER**
**File:** P97-REPORT.md:31–33

The report lists 8 targets as CLOSED. Ground truth from the full gate
coverage JSON shows 6. Two issues:

- `ui/sparkline.py` is listed as CLOSED with an asterisk claiming "at 100%
  in focused invocation; coverage aggregation edge in full xdist run."
  The full gate coverage shows it at 97.8%/95.5% — NOT 100%. This directly
  violates O1's negative: "a target is accepted from a focused test
  invocation that omits the complete suite."

- `collect/zswapmath` appears TWICE in the closed list (once as
  `collect/zswapmath.py` and once as `collect/zswapmath*`). There is one
  zswapmath.py.

The report is inaccurate and overstates completion by 33% (8 claimed vs
6 actual).

### F3 — Ineffective test: wrong function targeted (BLOCKER)
**Severity: BLOCKER**
**File:** topos/tests/test_p97_quickwins.py:240–247

`test_model_frame_from_jsonable_host_meta_not_none` in `TestExtraGaps`
tests `frame_from_jsonable` (model.py line 263+), but the missing gap is
in `metric_from_jsonable` (model.py line 113). These are different
functions with different validation logic. The test is irrelevant to the
gap it claims to close. `metric_from_jsonable` line 113 checks
`isinstance(value, list)` — the gap remains because no test passes a
non-list value to `metric_from_jsonable`.

**Repair oracle:** Write a test that calls `metric_from_jsonable` with a
non-list argument (e.g. `"not_a_list"` or `123`) and asserts
`ValueError` with match "compact form".

### F4 — Ineffective test: wrong branch targeted
**Severity: HIGH**
**File:** topos/tests/test_p97_quickwins.py:300–306

`test_sparkline_padding` in `TestFinalGaps` tests the `len(result) < width`
padding branch. The missing gap is `len(result) > width` truncation
(line 73): `result = result[:width]`. The test passes a single data point
with width=8 → short result → padding exercised, but truncation is not.

**Repair oracle:** Add a test that passes more data points than width
(e.g. `render_sparkline([1,2,3,4,5,6,7,8,9,10], width=3)`) and asserts
`len(result) == 3`.

### F5 — Missing test for declared target
**Severity: BLOCKER**
**File:** topos/src/topos/ui/damon_control.py

`ui/damon_control.py` is a declared target (handoff line 102) with 1
missing line [52] but has **zero tests** in the entire
`test_p97_quickwins.py`. The file is imported nowhere in the test file.
The gap was not even attempted.

**Repair oracle:** Add a test for the uncovered code path in
`damon_control.py`.

### F6 — Duplicate test
**Severity: LOW**
**File:** topos/tests/test_p97_quickwins.py:249–253

`test_registry_parse_selector_empty_kept` in `TestExtraGaps` tests
`parse_metrics_selector(" , ")` with the same input and same expected
exception as `test_parse_metrics_selector_rejects_empty_after_strip`
in `TestRegistryQuickwins`. Two tests covering the same line add noise
without additional coverage.

### F7 — Premature `asserts` declaration (O5)
**Severity: HIGH**
**File:** topos/nyxloom-trove/nyxloom.toml:67

The added line `asserts = ["tests-pass", "changed-line-coverage",
"canary-verified"]` declares `canary-verified` without evidence that
`nyxloom gate verify topos` returned `TRUSTWORTHY`. The gate-adoption
assessment (STANDARD.md and gate-adoption-assessment.md) requires
verification before declaring. P96 never ran `gate verify` — its handoff
explicitly said "Do not add canary-verified in P96." P97 adds it on the
claim that "P96's post-merge control-path result was TRUSTWORTHY" but
no such evidence exists in P96-REPORT.md or P96-LOG.md.

**Repair oracle:** Either (a) run `exec-nyxloom gate verify topos` and
confirm TRUSTWORTHY before keeping the assert, or (b) remove
`canary-verified` from the asserts list and defer to a follow-up.

### F8 — Test count inaccuracy
**Severity: LOW**
**File:** P97-REPORT.md:42

The report claims 48 tests added. The full suite collects 49 tests from
`test_p97_quickwins.py`. Minor discrepancy but consistent with the pattern
of inaccurate reporting.

### F9 — Weak assertions (quality concern)
**Severity: INFO**
**File:** topos/tests/test_p97_quickwins.py

Several tests assert shape/convention rather than behavioral contract:
- `test_key_help_returns_tuple` (line 156): asserts `isinstance(h, tuple)
  and len(h) > 0` — doesn't verify content
- `test_redact_process_row_applies` (line 135): asserts
  `result["comm"] != "visible_proc"` — doesn't verify the redacted value
- `test_build_admin_preview_with_property_values` (line 207): asserts
  `r is not None and r.mode != "disabled"` — doesn't verify what mode IS
- `test_series_storage_bytes` (line 180): asserts `s.storage_bytes > 0`
  — doesn't verify the expected size
- `test_marker_path_resolves` (line 196): asserts `"42" in str(path)` —
  only substring check

These are not individually blocking, but they indicate a pattern of
covering lines for coverage's sake rather than proving behavioral
correctness (O2 negative: "coverage is obtained by calling lines
without behavioral assertions").

---

## Checks passed

- **No product source edits**: Diff confirms 0 changes under
  `src/topos/**`. All changes are in tests, config, and reports.
- **tools/__init__.py** created (empty) — O5 requirement met.
- **No scope violations**: All files changed are within the handoff's
  `scope.touch` set.
- **No `# pragma: no cover`** added — the handoff prohibition is honored.
- **zswapmath.py fully closed**: 13 tests with exact-value assertions;
  all 13 statements and 6 branch pairs covered. Best-practice example
  within the package.
- **Two gate runs parity**: LOG claims two runs identical. (Not
  independently re-verified; accepted at face value given P96 parity
  precedent.)
- **`git diff --check`**: No whitespace errors (verified).

---

## Verdict

**CHANGES_REQUIRED.** The handoff completion condition (all 16 targets at
exact 100% in the full gate) is objectively unmet. The report overstates
completion (8 claimed vs 6 actual), includes ineffective tests (F3, F4),
omits a declared target entirely (F5), and declares a canary-verified
assert without the required control-path evidence (F7).

The F1–F7 blockers have concrete, mechanical repair oracles documented
above. The same persistent session can resume after the implementer
closes the gaps or files a BLOCKED for the infrastructure-dependent
targets.


---

# Repair re-review — 2026-07-25 (commit f978acbb)

**Re-reviewer:** Reasonix (same persistent adversarial session)
**Re-review range:** 6290f277..f978acbb
**Verdict:** **CHANGES_REQUIRED**

## Changes since prior review

The implementer addressed all 9 findings from the original review (F1–F9).
The report is now accurate (9/16 closed, 7 gap), `canary-verified` was
removed from asserts, ineffective tests were fixed, duplicates removed, and
assertions strengthened. The diff adds 2 dockerjoin tests, fixes model.py
to test `metric_from_jsonable` with non-list input, adds sparkline
truncation test, ring `storage_bytes` exact-value assert, and a
component_health multi-byte split test.

## Independent gate verification

Ran the exact declared `topos-suite` gate at f978acbb: **1819 passed,
exit 0** in 73s. Extracted per-target branch-aware coverage JSON:

```
CLOSED  collect/zswapmath.py       stmts= 13/ 13 (100.0%)  br=  6/  6 (100.0%)
CLOSED  collect/dockerjoin.py      stmts=107/107 (100.0%)  br= 44/ 44 (100.0%)
GAP     collect/collector.py       stmts=245/245 (100.0%)  br= 97/ 98 ( 99.0%)  miss_br=[[165,167]]
CLOSED  model.py                   stmts=136/136 (100.0%)  br= 32/ 32 (100.0%)
GAP     registry.py                stmts= 50/ 51 ( 98.0%)  br= 19/ 20 ( 95.0%)  miss_lines=[279] miss_br=[[278,279]]
CLOSED  procs/identity.py          stmts= 18/ 18 (100.0%)  br=  0/  0 (100.0%)
CLOSED  procs/sensitivity.py       stmts= 17/ 17 (100.0%)  br=  8/  8 (100.0%)
CLOSED  procs/owners.py            stmts= 41/ 41 (100.0%)  br=  8/  8 (100.0%)
CLOSED  ui/keys.py                 stmts=  5/  5 (100.0%)  br=  0/  0 (100.0%)
GAP     ui/damon_control.py        stmts= 32/ 33 ( 97.0%)  br=  0/  0 (100.0%)  miss_lines=[52]
GAP     ui/sparkline.py            stmts= 45/ 46 ( 97.8%)  br= 21/ 22 ( 95.5%)  miss_lines=[73] miss_br=[[72,73]]
GAP     record/ring.py             stmts= 98/ 98 (100.0%)  br= 25/ 26 ( 96.2%)  miss_br=[[63,-60]]
CLOSED  inspect_files/plan.py      stmts= 49/ 49 (100.0%)  br=  8/  8 (100.0%)
GAP     damon/paddr.py             stmts= 48/ 50 ( 96.0%)  br= 10/ 12 ( 83.3%)  miss_lines=[82,85] miss_br=[[81,82],[84,85]]
CLOSED  actions/preview.py         stmts= 38/ 38 (100.0%)  br= 10/ 10 (100.0%)
GAP     daemon/component_health.py stmts=145/146 ( 99.3%)  br= 25/ 26 ( 96.2%)  miss_lines=[56] miss_br=[[51,56]]
```

9 closed, 7 gap — report is accurate. (F2 fixed.)

## New findings

### F10 — sparkline.py line 73 is PROVABLY DEAD CODE (BLOCKER)
**File:** topos/src/topos/ui/sparkline.py:72-73

The report labels this gap as "Padding path — coverage aggregation edge."
This is **false**. Independent testing proves line 73 is unreachable under
ALL inputs, even serial (no-xdist) coverage:

- The `render_sparkline` algorithm downsamples to exactly `width` chars
  when `n > width` (`for i in range(width)` at lines ~44-51).
- When `n <= width`, it produces exactly `n` chars, and `n <= width`.
- Therefore `len(result)` is ALWAYS `<= width` for every possible input.
- The branch `len(result) > width` at line 72 can NEVER be taken.

The truncation test `test_render_sparkline_truncation` was written to cover
this branch but the branch is structurally unreachable — the test passes
because the function behaves correctly, but it exercises the
`len(result) == width` path (no-op), not the truncation path. This is a
hollow test per O2 negative.

The escalate_if trigger fires: "a targeted branch is mechanically
unreachable under Python 3.14." The implementer did NOT invoke BLOCKED.

**Repair oracle:** Remove the dead code (lines 72-73) since the truncation
guard is provably unnecessary. The `elif len(result) < width` padding
branch at line 74 suffices.

### F11 — registry.py line 279 BLOCKED rationale is incomplete (BLOCKER)
**File:** topos/src/topos/registry.py:256-260, 278-279

The BLOCKED claim says "Every valid token adds metrics." The real mechanism
making line 279 unreachable is the **redundant early guard** at lines
259-260: `if not all(tokens): raise ValueError("empty selector")`. This
guard catches every empty-token case, pre-empting the identical check at
line 278-279. The guard and line 279 are functionally redundant.

Cleanest product-code correction (no pragma needed): **remove the
redundant guard** at lines 259-260, and filter empty tokens instead:

```python
tokens = [t for t in tokens if t]          # filter empty tokens
if not tokens:                              # guard for all-empty
    raise ValueError("empty selector")
```

After this change, `parse_metrics_selector(",")` produces `tokens=[]`,
the `for` loop is skipped, `kept_metrics` is empty, and line 279 raises
`ValueError("empty selector")` — **covered**. The existing test
`test_parse_metrics_selector_rejects_empty_after_strip` covers it.
Behavior-preserving: same error for all four cases ("", ",", " , ", "ram,").

### F12 — component_health.py line 56 is trivially testable (HIGH)
**File:** topos/src/topos/daemon/component_health.py:56

Line 56 `return suffix[:limit]` is the decode-fallback when `kept` is
empty. Reachable with any input where `limit <= len(suffix)` i.e.
`limit <= 3`. Not an aggregation artifact — just not tested.

**Repair oracle:** `assert _truncate_utf8("abc", limit=0) == ""`

### F13 — ring.py branch [63,-60] is the count-capped path in append() (HIGH)
**File:** topos/src/topos/record/ring.py:63

The missing branch is `if self.count < len(self.samples):` with the
**False** path — when the ring buffer is full and count stops incrementing.
The report labels this "Early exit in last()" but it's in `append()`.
Reachable by filling the ring buffer to capacity.

**Repair oracle:** Append 5 values to a capacity-3 buffer, assert `count == 3`.

### F14 — sparkline truncation test is hollow (HIGH)
**File:** topos/tests/test_p97_quickwins.py:259-264

Designed to cover line 73, but line 73 is unreachable (F10). The assertion
`len(r) == 3` passes because the algorithm produces exactly `width` chars,
not because truncation occurred. Remediated by F10.

### F15 — damon/paddr.py (genuine infra) / F16 — damon_control.py (genuine infra) / F17 — collector.py (block-family filter)
**Acceptable as documented gaps.** Recommend D-NNN decisions for deferral.

## Prior findings status

| F1 | IMPROVED (9/16 honest) | F2 | FIXED | F3 | FIXED | F4 | NOT FIXED (F10) | F5 | UNCHANGED | F6 | FIXED | F7 | FIXED | F8 | FIXED | F9 | IMPROVED |

## Canary fact

Controller confirms `gate verify topos` at main 025fb843 returned
`TRUSTWORTHY`. `canary-verified` correctly removed from asserts. ✓

## Verdict

**CHANGES_REQUIRED.** Three gaps mischaracterized as "infrastructure" or
"aggregation edge" are actually dead code (F10), structurally redundant
guards (F11), or trivially testable edge cases (F12, F13). The sparkline
truncation test is hollow (F14). Repair oracles for F10-F13 are concrete
and mechanical (≤5 lines each). After closing, expected: **12/16 targets
at 100%**, with 4 deferred as genuine infrastructure gaps.
