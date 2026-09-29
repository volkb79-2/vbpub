# W3 (B130) implementer log

## Part 1 (research)
- Scratch dir `/tmp/w3-0OY6` (database, junit, logs; not committed). Base `3daf62a7`.
- `pytest --collect-only`: 5965 tests (saved `collect-before.txt`).
- Step 4 run per brief (ignore list of 12 paths): exit 1, 5626 passed / 1 failed / 1 skipped, wall 288 s. The failure `test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` is environmental (`/tmp/.git` exists on this host); recorded as data (A-040), baseline for O5.
- Scripts in `w3-scripts/`; `graph.py` found 176 edges (equals the brief's 176) after fixing my own first-draft bug (TYPE_CHECKING-body imports were skipped, 173).
- `coupling.py` rerun is byte-identical (`cmp`). Union check equal for all 10 components.
- Report: `W3-REPORT-component-boundaries.md`. Committed `be0357de`.

## Part 2
- `tests/core/test_import_contracts.py` written per 2a/2b (7 tests); `TESTS_ROOT` added to `tests/conftest.py`.
- 221 files moved with `git mv` from `W3-test-moves.tsv` (core 156, parsers/coverage 24, go 15, python 12, js 5, sql 5, parsers/mutation 2, result_reports 2; 24 stay pinned at root, matches the carver's counts).
- 2c: 30 `__file__` path expressions in 27 files replaced with `TESTS_ROOT`/`PROJECT_ROOT`; 14 now-unused `Path` imports removed (pyflakes). `tests/conftest.py` prose and DESIGN-GUIDE/CONSUMERS path mentions updated; DESIGN-GUIDE section, README sentences, backlog B130 status done.
- Own slip found and fixed before commit: a first pass dropped a space (`TESTS_ROOT /"fixtures"`) in 6 files; corrected.
- Commit `f2c249fe` (contract test + layout + docs in one commit: the contract test's layout check is red without the moves).

## Oracles
- O1: `from .adapters import python` in `runner.py` (temporary) -> red `assay.runner -> assay.adapters.python`; reverted (`git diff -- src` empty). Green without it.
- O2: `git mv tests/core/test_cli_run.py tests/` -> red `test_cli_run.py should be in tests/core`; restored.
- O3: collect-only before 5965 ids; after 5972 = +7 ids of `test_import_contracts.py`, none removed (compared with the directory prefix stripped, before the docs edit). Negative: `FIXTURES` in `test_isolation.py` reverted to `Path(__file__)...` -> collect-only `FileNotFoundError ... tests/core/fixtures/isolation/...`; restored.
- O4: `git grep -nE "Path\(__file__\)" -- tests/core tests/adapters tests/parsers` empty (the only mentions are none); renames only, non-100% only for the 27 2c files plus conftest/docs.
- O5: rerun of step 4 into `$SCR/after.*`: exit 1, 5633 passed / 1 failed / 1 skipped (5628 + 7 new tests = 5635). The same `test_git_boundary` `/tmp/.git` failure as baseline. Keys `(basename, testcase)` equal, all outcomes equal, except two id-only differences: (1) the module-level skipped record's name carries the module path (`tests.test_mutation_judge_identity_properties` -> `tests.core....`); (2) the docs TOML-example test id `test_every_live_toml_example_parses_with_the_shipped_loader[DESIGN-GUIDE.md:2974]` became `...:3011` because the new DESIGN-GUIDE section (37 lines, required by Work step 6) shifts the line number in the id. Both pass/skip identically; no separate base-vs-HEAD rerun needed.
- O6: pyflakes clean (exit 0) after removing the unused imports.

## Gate (O7): NOT RUN
Waited the full 3 hours (polling `docker ps` every 2 minutes) for the host to have no `run-gate*` container. Another session's `run-gate-vbpub-mutation-3747550-1790644029` (5+ hours old) was still running at the timeout (`run-gate-rg55-p1-r2-isolated-r2-3806635-1790646112` ended earlier). Per the host-load rule one gate container only, so I did not start `tester-unified`. Next step for whoever continues: when `docker ps --format '{{.Names}}' | grep '^run-gate'` is empty, run
`cd .../wave-a-w3-boundaries/assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified > <log> 2>&1`, then read the markers separately. Everything else (O1-O6) is done and committed.
