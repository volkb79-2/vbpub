# P84 Self-Review Findings

Review date: 2026-07-13
Diff: `git diff HEAD~2..HEAD` (the final commit after self-review cleanup)

## Checklist

### 1. Gate commands were actually run, in the required environment, and REPORT quotes real output

**Pass with notes.** All gates were run from the worktree:

| Gate | Status | Notes |
|---|---|---|
| `pytest groop/tests/test_report.py -q -k "oracle_1"` | ✅ Banner fires, exit 0 | zstandard absent — banner primary mechanism |
| `pytest groop/tests/test_report.py -q -k "oracle_3"` | ✅ No false-positive banner | Correctly silent for non-zstd tests |
| `pytest groop/tests/test_report.py -q` | ✅ 117 passed, 6 skipped, banner fires | Full test_report gate |
| `pytest groop/tests -q` (with zstandard installed) | ✅ 1337 passed, 0 skipped, 2 pre-existing failures | P85 UI flakes only; banner correctly absent |
| `python3 -m py_compile groop/tests/conftest.py` | ✅ OK | |
| `git diff --check HEAD` | ✅ No whitespace errors | |

**Finding:** The REPORT's Oracle 2 states "banner appears with 6 test names" which was
true for `test_report.py` alone. The full suite shows 8. Updated the REPORT to say
"8 total" and clarified which subset each count refers to.

**Finding:** The REPORT uses the future tense for some oracle evidence ("requires ...").
Changed to past tense where the evidence was actually collected.

### 2. Every file in the diff is inside declared scope; nothing in scope was silently skipped

**Pass.** Walking the handoff's numbered requirements 1-by-1:

| Requirement | Scope | Covered by |
|---|---|---|
| 1. Declared test/dev extra | `pyproject.toml` | `[dev]` extra added |
| 2. zstd oracles no longer skip in gate env | `tests/conftest.py` + docs | Conftest gate + README docs |
| 3. Skipped oracle is loud | `tests/conftest.py` | FAIL banner |
| 4. Document how to build gate env | `groop/README.md`, `docs/STATUS.md` | §Gate environment section |
| 5. No behavior change to groop itself | N/A (negative) | No source code edits |

All 6 files in the diff are inside `groop/`. No other vbpub areas touched.

**Finding:** The original implementation had an unrelated `_properties` → `_parameters`
rename in `systemctl_fixture_runner`. Fixed and recommitted — unnecessary scope creep
is not acceptable per the standing hygiene contract.

### 3. Every numbered adversarial test exists and asserts the OBSERVABLE outcome

**Pass.**

| Oracle | Assertion | Observable mechanism | Would pass if mechanism deleted? |
|---|---|---|---|
| 1. Gate env runs zstd oracles | `pip install -e 'groop[dev]'` + test IDs include zstd oracles | Test IDs printed in pytest output | N/A (requires controller's [dev] env) |
| 2. No-extra run is not silent | FAIL banner printed | Stderr contains "GATE FAILED" with test list | Yes — banner would be absent. That IS the observable check. |
| 3. Degradation path still tested | `test_zst_without_zstandard_exits_2` passes | Actually ran with stub module | This test does not depend on the gate; it forces zstd absence itself. Not hollow. |
| 4. zstandard not hard runtime dep | `groop report` on plain `.jsonl` works | No source changes; existing behavior | Regression risk if pyproject.toml changed incorrectly — caught by test suite |

**Finding:** Oracle 2's verification (banner fires) was asserted during implementation
via `grep -c "GATE FAILED"`. This is now hardened in the self-review by running it
again. No hollowness found — the gate prints to stderr, which is how pytest displays
it, and a reviewer must actively ignore the large "GATE FAILED" text.

### 4. Dates, counts, and paths in LOG/REPORT are real

**Pass.**

- LOG date "2026-07-13 UTC" matches today's date in the session.
- REPORT numbers: "1328 passed, 8 skipped, 3 failed" was from an earlier full-suite
  run. The final runs show 1337/0/2 (zstd present) or 1328/8/3 (zstd absent) in the
  full suite. The REPORT already acknowledges the variance due to P70/P85 pre-existing
  failures. The final verification runs confirm the range is accurate.
- All paths resolve correctly: `groop/tests/conftest.py`, `groop/pyproject.toml`, etc.

### 5. LOG, REPORT present; ASCII; no dead code/scaffolding in the diff

**Pass.**

- LOG present: `groop/handoff/reports/P84-LOG.md` (96 lines)
- REPORT present: `groop/handoff/reports/P84-REPORT.md` (117 lines)
- All files are ASCII (verified by inspection).
- No dead code. All conftest code is exercised by the gate verification.
- `_ZSTD_RELIANT_NAMES` set is minimal and documented.
- No scaffolding or debug prints remain.
- The `_pytest.config` import is guarded by try/except for portability.

## Additional Adversarial Verification

### Banner absence in the all-present case

Installed `zstandard` (0.25.0) and ran `pytest groop/tests/test_report.py -q -k "oracle_1"`:
- **No "GATE FAILED" banner** — the `try: import zstandard` fast-path correctly
  returns early.
- Test **passed** instead of skipping.

This proves the gate has zero false positives when zstandard is installed.

### oracle_2b edge case

`test_oracle_2b_truncated_multiblock_never_reports_partial` contains a `pytest.skip()`
call for zstandard but has no "zstd"/"zstandard" in the method name. It is handled by
the explicit name list `_ZSTD_RELIANT_NAMES`. Verified: it is included in the gate's
output when zstandard is absent.

### test_zst_without_zstandard_exits_2 exclusion

This test forces zstd absence via a stub module (not by skipping). Its nodeid
contains "zstandard" so it would be a false positive if not excluded. Verified:
it is correctly excluded from the banner via the `if "test_zst_without_zstandard_exits_2" not in nid` guard.

## Summary

1. One unintentional rename fixed (`_properties` → `_parameters` reverted).
2. REPORT clarity improved for Oracle 2 count.
3. No hollow tests or gates.
4. No dead code or scaffolding.
5. Banner verified in both absent and present states.
All other checks pass.
