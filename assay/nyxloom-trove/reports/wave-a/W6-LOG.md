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
Red run per case (focused files): 16/16 guard cases fail with `StepLimit` (go-292, go-321, go-323, go-330, gomod-393, js-243, js-245, js-249, sql-193-eq, sql-193-or1, sql-193-or2, sql-195, sql-270, sql-273, iso-1388, iso-1392). O4 (`test_the_git_drain_loop_offers_no_mutation_site_in_its_exit_test`) fails because its first anchor `while selector.get_map():` is missing today (the old header continues `and overflowed is None:`; ValueError from the unpack at test line 54, before the `if overflowed:` anchor is read), NOT because sites are present. `tests/test_errors.py` fails on import (`require_advance`). Passing already in the red state (as expected, they hold the pre-existing behaviour): component checks, the ordinary-input tests, O4b, O5b.

## Step 2: implementation commit `f13bf1d0`
Helper `errors.require_advance` (+`__all__`), the 8 call sites with imports (go 3, javascript 1, sql_lex 2, go_modfile 1, isolation 1), the `_run_bounded` drain rewrite (`while selector.get_map():` plus `if overflowed: break` after the `for`, at the `while` body's indentation).
`grep -n "pragma: no cover" src/assay/git.py` -> `378`, `1344`; fixture git.py `lines` = `[378, 379, 1344, 1345]`, reason unchanged.
Green: all 16 guard cases raise the guard's `AssertionError` (they no longer hit `StepLimit`); focused set (errors, four guard files, b105 controls, sql lexer, isolation, git boundaries, import contracts, go and javascript adapter folders): 548 passed.

## Step 3 (watchdog), O6: DROPPED per CD3
The two real-child liveness tests no longer exist. `liveness.py` timeout-check hazard (now `:1692`): "test-level hazard removed with its host test". `tests/test_cli_run.py` untouched.

## Oracles (positive / deliberate break, break applied locally, red observed, reverted, never committed)
| Oracle | Positive | Break | Result of break |
|---|---|---|---|
| O1 helper | `test_require_advance_returns_a_strictly_advanced_cursor_and_refuses_the_rest` passes | `<=` -> `<` | that test red, plus stalled-cursor cases (5 failed in the run of errors + go + sql files) |
| O2 16 cases | 16/16 raise the guard | `require_advance` removed from the go `//` branch (old go.py:326) | exactly go-321 and go-323 red (`StepLimit`), other 6 in the file green |
| O2 component check | `test_*_cases_name_only_*` pass (go, javascript, sql, core) | misfiled case added (in `test_a_case_from_another_component_is_refused_by_the_component_check`) | refused with "outside this component" |
| O3 ordinary input | four `test_every_guarded_function_still_terminates_on_ordinary_input` pass; adapters go/javascript, sql lexer, isolation, git boundary suites unchanged green | n/a | n/a |
| O4 drain exit test | passes | old header `while selector.get_map() and overflowed is None:` restored | red, but by the missing anchor (`while selector.get_map():` gone), not by "sites present" |
| O4b drain stops | passes (returns GIT_FAILED naming standard output, group gone) | `if overflowed: break` moved inside the `for` | red after the 120 s failsafe ("the drain did not stop..."), child group killed, no orphan |
| O5b exclusion structure | passes | fixture set back to `[376, 377, 1342, 1343]` | red: `git.py:376 is neither a pragma line nor inside a pragma line's block` |
| O5 preflight | not run (CD44) | | controller |

## Collect-only
Before (base, my files excluded): 5975. After: 6004 (+29: go 8, javascript 5, sql 8, core 6, O5b 1, errors 1). No test removed.

## Coverage (full `tests`, serial, nice/ionice): 1 failed, 5983 passed, 21 skipped
The one failure is the known environmental `test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` (`/tmp/.git`). TOTAL: 1 missed line, 1 missed branch; both `git.py:459`, i.e. the known environmental line (was 457; my two added drain lines shifted it by +2, checked: it is `raise _git_failed("no .git marker found in any ancestor ...")`). Every other file 100% line and branch, no pragmas added.

## For the controller's gate run
- `tester-unified` (CD44), then `self-qualification-preflight` (O5: the session hook validates raw `excluded_lines` against the map `[378, 379, 1344, 1345]`).
- With the `/tmp/.git` host artefact absent the git.py miss disappears.
- CD30: no consumer-visible change (internal helper in `errors.__all__`, tests, docs).

## Deviations
- No `tests/test_scanner_progress_guards.py`; split by component per CD2/REBASE (four files plus `tests/scanner_progress_support.py`). O4 and O4b live in the core file (`tests/core/test_isolation_scanner_progress_guards.py`, "one for core").
- Each component file also has an "ordinary input" test and a component-membership test; the go file has one extra test proving the membership check refuses a misfiled case.
- Two appends (a test to `tests/test_errors.py`, a placeholder line in `src/assay/errors.py` replaced right after by Edit) were made through a shell append instead of the Edit tool; content is what the brief specifies.
- `git.py` has two `while selector.get_map():` lines (`:335` in `_run_bounded`, `:1309` elsewhere); the O4 test scopes its search to `_run_bounded`, so it is unaffected.
- O5b was added in the red commit (it passes there), not in step 2.
- Docs: DESIGN-GUIDE paragraph omits the watchdog bullet (CD3); README/CONSUMERS unchanged (no consumer-visible behaviour change).
- R2 caveat recorded in DESIGN-GUIDE: the guard files kill their own target mutants textually; pilot known-hard set (go.py 292/321/323/330) kills are not a guard measurement.

READY-FOR-GATE f13bf1d0
