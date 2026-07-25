# P96-SELFREVIEW — Adversarial self-review of the max-standard test gate

**Reviewer:** P96 implementer (same session), adversarial pass
**Branch:** feat/topos-P96-max-test-gate
**Commit:** f90b9c34 + self-review repairs (see below)

## Controller findings and repairs

### Finding 1: py-compile was NOT fail-closed
**Status: FIXED**
- **Original defect:** `git diff ... || true` masked git failure; `test && py_compile || echo` turned py_compile failure green; unquoted `$files` was not filename-safe.
- **Fix:** Replaced with true NUL-delimited argv: `git diff --name-only -z main...HEAD -- '*.py' | xargs -0 -r python3 -m py_compile` under `set -o pipefail`. Git failure and py_compile failure both exit nonzero. No .py files changed exits zero (xargs -r skips when input is empty).
- **Tests added:** Three tests in `test_gate_environment.py` that parse the actual TOML argv from `nyxloom.toml`, substitute `{worktree}`, and execute against real temporary git repos:
  - `test_pycompile_syntax_error_exits_nonzero`: syntax error → exit nonzero
  - `test_pycompile_no_changed_files_exits_zero`: no .py changes → exit zero
  - `test_pycompile_filename_with_spaces_succeeds`: filename with spaces → exit zero

### Finding 2: topos-suite PYTHONPATH not exported
**Status: FIXED**
- **Original defect:** `PYTHONPATH=topos/src &&` is a shell assignment without `export`, so subprocesses (pytest children, CLI invocations) never see it.
- **Fix:** Changed to `export PYTHONPATH=topos/src`. Both shell layers now have `set -euo pipefail`.
- **Tests added:** `test_topos_imported_from_worktree` in `test_gate_environment.py` uses `Path(topos.__file__).resolve().relative_to(expected_parent)` to prove the import resolves under the worktree's source tree, not an image-baked `/src/topos` path.

### Finding 3: P96-COVERAGE-GAPS was approximate
**Status: FIXED**
- **Original defect:** The ledger had approximate counts for a subset of files and no exact missing branch pairs.
- **Fix:** Generated complete JSON-derived table for all 93 source files with exact `missing_lines` and `missing_branch_pairs`. Ledger is now exact, complete, and tagged with the 4-run parity confirmation.
- Parity measured via 4 serial+parallel runs in one container, files `docker cp`'d out, and compared with Python. The four temporary JSON files and named container were then explicitly removed; no ignore rule is retained.

### Finding 4: Timing test mischaracterized / false-red risk
**Status: FIXED**
- **Original defect:** `test_default_recording_profile_is_linear_time` used `time.perf_counter()` wall-clock timing that failed intermittently under `-n auto` xdist due to CPU contention. The report mischaracterized the failure.
- **Fix:** Replaced the wall-clock oracle with a deterministic operation-count oracle using `monkeypatch` to count `_finite_gauge_value` calls (same proven pattern as `test_finite_gauge_reads_are_linear`). The test now verifies that frame_count × entity_count gauge reads occur, proving linear O(N) complexity regardless of CPU load.
- The earlier failure: test ran in a full containerized parallel run (1744 tests, `-n auto`, under CPU contention) and the wall-clock assertion `long_elapsed <= short_elapsed * 2.5 + 0.05` failed because `long_elapsed` was disproportionately high. The `-c topos/pyproject.toml` flag was present; failure was purely load-related, not a bug in the product source.

### Additional adversarial fixes

#### 5a. Real subprocess E2E test with temporary git repo
**Status: FIXED**
- Added `test_coverage_gate_e2e_with_real_git_repo` in `test_coverage_gate.py` — creates a real git repo with init/commit/branch, runs `coverage_gate.main()` against it with good and bad coverage JSON, verifying the end-to-end I/O boundary (git diff + coverage loading + verdict).
- Verified: good coverage exits 0, missing line exits 1, missing `files` key exits 2.

#### 5b. Coverage JSON record validation
**Status: FIXED**
- Added `_validate_cov_record()` to `coverage_gate.py` — validates that each coverage record's `executed_lines` and `missing_lines` are lists of ints. Malformed data raises `CoverageGateError`.
- Added 4 unit tests: valid records, missing key, non-list, non-int.
- Added `test_evaluate_malformed_missing_lines_is_error` — malformed records during `evaluate()` raise `CoverageGateError`.
- Added `test_main_malformed_coverage_returns_2` — CLI test proving malformed coverage → exit 2.

#### 5c. Source-prefix hardening
**Status: FIXED**
- `_rel_to_source()` now enforces directory boundary: after the prefix match, the next character must be `/` or end-of-string. A changed file under `topos/src/topos_evil/` is NOT treated as a source file.
- `evaluate()` now uses `npath == prefix or npath.startswith(prefix + "/")` instead of just `npath.startswith(prefix)`.
- Tests added: `test_rel_to_source_rejects_false_prefix_match`, `test_rel_to_source_searches_for_next_prefix_match`, `test_evaluate_ignores_changes_under_false_friend_prefix`.

#### 5d. Stale comments corrected
- `nyxloom.toml`: removed the stale hard-coded test count; current counts live in the evidence reports
- `nyxloom.toml`: py-compile and topos-suite comments updated to reflect current implementation

#### 5e. Overclaims corrected in reports
- P96-REPORT.md timing claim corrected: the failure was a genuine xdist wall-clock false red, now fixed with deterministic oracle.
- P96-COVERAGE-GAPS.md regenerated with exact data.

### BLOCKED triggers
No escalation triggers fired. All repairs were within scope (test files, tools scripts, config, reports — no product source edits under `topos/src/**`).

## Final focused test results
```
test_coverage_gate.py: 36 passed
test_gate_environment.py: 7 passed (3 original + 4 new)
```
The four-run self-review parity suite passed before the final controller cleanup removed three redundant copied-fragment tests and added one malformed-record-shape negative. The post-cleanup focused-test and exact-gate counts below supersede the earlier count.

## Gate command verification
Self-review parity: four repaired-suite runs were identical. After controller
cleanup, the exact declared host-bind `topos-suite` argv passed 1,758 tests in
68.76s, produced branch-aware coverage JSON, and the changed-line evaluator
returned exit 0 at its 100% floor.

## Commit
This self-review and all repairs committed on top of f90b9c34.
