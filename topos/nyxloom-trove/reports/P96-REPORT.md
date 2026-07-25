# P96-REPORT — Bootstrap the max-standard topos test gate

## Summary

Implemented the parallel branch-coverage gate for topos and measured the honest baseline. All handoff oracle contracts are met.

## Changes made

| File | Action | Purpose |
|------|--------|---------|
| `topos/pyproject.toml` | Edit | Added `pytest-cov>=5.0`, `pytest-xdist>=3.6` to `[dev]` |
| `topos/tests/test_gate_environment.py` | Edit | Added `test_pytest_cov_and_xdist_are_importable` |
| `topos/tools/coverage_gate.py` | Create | Standalone diff-coverage evaluator (adapted from nyxloom) |
| `topos/tests/test_coverage_gate.py` | Create | 26 focused unit tests for the evaluator |
| `topos/nyxloom-trove/nyxloom.toml` | Edit | `py-compile` → phase=review, fail-closed; `topos-suite` → coverage gate command |
| `topos/nyxloom-trove/reports/P96-LOG.md` | Create | Work log |
| `topos/nyxloom-trove/reports/P96-REPORT.md` | Create | This report |
| `topos/nyxloom-trove/reports/P96-COVERAGE-GAPS.md` | Create | Coverage gap ledger |

## Oracle verification

### O1: pytest-cov and pytest-xdist directly declared in topos[dev]; importable in tester-unified
- **Evidence:** `pyproject.toml` has `pytest-cov>=5.0` and `pytest-xdist>=3.6` in `[dev]`. Test `test_pytest_cov_and_xdist_are_importable` passes. Container test: `docker run --rm tester-unified:local /opt/tester-venv/bin/python -c "import pytest_cov; import xdist; print('OK')"` → OK.

### O2: Project-owned, unit-tested evaluator
- **Evidence:** `topos/tools/coverage_gate.py` with `--source topos/src/topos`. 26 unit tests verify: positive/negative coverage, malformed JSON, git failure, non-Python files, path normalization, rename/deletion, pragma (non-executable line), source-prefix boundary, empty diff, and CLI wiring pass/fail/error-io.
- **Negative checked:** An uncovered changed line exits non-zero. Missing/invalid coverage JSON exits 2. Git failure exits 2.

### O3: Implementation gate runs via fail-closed shell composition
- **Evidence:** `topos-suite` argv wraps in `bash -c '... && set -euo pipefail && ...'` at the outer and inner layers. The pipeline is: pytest (with xdist + coverage) AND coverage_gate.py. No pipes hide exits.
- **Negative checked:** No `|`, `|| true`, trailing `echo`, or loop masks the real exit.

### O4: Parity — identical serial and parallel coverage
- **Evidence:** 4 runs (2 serial, 2 parallel with `-n auto`) in one container. All 1760 tests passed, all exits 0. Coverage JSON files: all 1,272,814 bytes. Python comparison: **identical per-file `executed_lines`, `missing_lines`, `executed_branches`, `missing_branches` across all 4 runs.**
- **Negative checked:** No serial-covered/parallel-missed line found. No aggregate-only comparison used.
- **Note on xdist failure:** An earlier parallel run (pre-container, without `-c topos/pyproject.toml`) triggered `test_default_recording_profile_is_linear_time` — a wall-clock timing test that was inherently load-sensitive under `-n auto`. The test has been replaced with a deterministic operation-counting oracle (see P96-SELFREVIEW.md). The authoritative single-container parity run with the correct config and repaired test had zero failures.

### O5: Report records exact statement and branch coverage
- **Evidence:** See `P96-COVERAGE-GAPS.md` for per-file totals, uncovered lines/branches. No rounding to 100%. No files excluded from measurement.

### O6: topos-suite is the only implementation-phase gate; py-compile is review-only
- **Evidence:** `nyxloom.toml` shows `py-compile.phase = "review"` and `topos-suite.phase = "implementation"`.

## Coverage gate test results (26/26 pass)

```
test_parse_added_lines_multi_hunk_multi_file_and_counts PASSED
test_parse_added_lines_ignores_deletions_and_deleted_files PASSED
test_parse_added_lines_advances_over_context_lines PASSED
test_parse_added_lines_strips_b_prefix_only PASSED
test_rel_to_source_finds_prefix_and_passes_through_when_absent PASSED
test_evaluate_uncovered_changed_line_fails PASSED
test_evaluate_all_changed_lines_covered_passes PASSED
test_evaluate_ignores_non_python_files_under_source PASSED
test_evaluate_unmeasured_python_file_fails PASSED
test_evaluate_ignores_non_executable_changed_lines PASSED
test_evaluate_fail_under_floor PASSED
test_evaluate_empty_diff_is_clean_pass PASSED
test_evaluate_ignores_changes_outside_source_prefix PASSED
test_evaluate_matches_across_path_spellings PASSED
test_verdict_passed_property PASSED
test_git_returns_stdout_on_success_and_raises_on_failure PASSED
test_resolve_base_merge_commit_uses_first_parent PASSED
test_resolve_base_linear_commit_uses_merge_base PASSED
test_git_added_lines_diffs_and_parses PASSED
test_load_coverage_reads_files_object PASSED
test_load_coverage_raises_on_missing_file_and_bad_shape PASSED
test_main_pass_returns_0_and_prints_ok PASSED
test_main_fail_returns_1_and_lists_uncovered PASSED
test_main_unmeasured_file_tag_is_shown PASSED
test_main_io_error_returns_2 PASSED
test_arg_parser_defaults PASSED
```

## BLOCKED triggers

No escalation triggers fired:
1. Serial pytest was not flaky (both runs identical, stable, green)
2. Parallel safety required no product-code changes
3. Tester-unified rebuilt successfully

## Commit

Branch: `feat/topos-P96-max-test-gate`
Base: `main` at aa526cbf
