# W6 (B113, P2 loop guards) implementer log

Base SHA: `20846716cddc1299f02fc76eccfdffe9edcffeb5` (branch `wave-a-w6-guards`).
Collect-only before (base, without my files): 5975 tests.

## Layout (CD2, CD27, W3 layout test)
- `tests/scanner_progress_support.py` (non-test support: StepLimit, line budget, mutant builder, component and ordinary-input helpers).
- `tests/adapters/go/test_adapters_go_scanner_progress_guards.py`: go-292/321/323/330 and gomod-393.
- `tests/adapters/javascript/test_adapters_javascript_scanner_progress_guards.py`: js-243/245/249.
- `tests/adapters/sql/test_adapters_sql_scanner_progress_guards.py`: sql-193-eq/or1/or2/195/270/273.
- `tests/core/test_isolation_scanner_progress_guards.py`: iso-1388/1392, plus O4 and O4b (git.py drain; both are core; the rebase note lists four files and no separate git file, so the core file carries them: "one for core").
- `test_errors.py` stays at `tests/`; O5b in `tests/core/test_b105_source_coverage_controls.py`.
- Every adapter file imports only `assay.errors`/`assay.mutation`/`assay.adapters.*` (its own component) and the test-only support module; no new `src/assay` module.

## Step 1: red commit
Red run per case (focused files): 16/16 guard cases fail with `StepLimit` (go-292, go-321, go-323, go-330, gomod-393, js-243, js-245, js-249, sql-193-eq, sql-193-or1, sql-193-or2, sql-195, sql-270, sql-273, iso-1388, iso-1392). O4 (`test_the_git_drain_loop_offers_no_mutation_site_in_its_exit_test`) fails because the anchor `if overflowed:` is missing today (ValueError from the unpack), NOT because sites are present. `tests/test_errors.py` fails on import (`require_advance`). Passing already in the red state (as expected, they hold the pre-existing behaviour): component checks, the ordinary-input tests, O4b, O5b.
