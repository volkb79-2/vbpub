# W2 (B127) implementer log

Base SHA: `4c53f28c` (branch `wave-a-w2-analysis`). Scratch: `/tmp/claude-1003/-workspaces-vbpub/0eeb333f-34bb-4155-942f-6ae260e97f0b/scratchpad` (before/after CLI captures `before-*.out|err`, `after-*`; `collect-before.txt`; not committed).

STATUS: COMPLETE, ready for the controller's gate run (see the end of this file). The successor implementer finished docs, oracle negatives and the local full run; `W2-CONTINUATION.md` is superseded.

## Oracle table (final)

Negatives are local deliberate breaks, applied with the Edit tool, observed red, reverted with `git checkout -- <file>`; `git status` clean after each; none committed. Outputs are in the scratchpad (`o1.out` ... `o5b.out`).

| # | Positive (HEAD, green) | Negative (break -> red) |
|---|---|---|
| O1 CLI unchanged | 9 CLI invocations byte-identical base vs moved (`cmp`); T1 214 moved tests pass | `return _run_analyze(...)` replaced by `pass` in `cli.main`: `analysis/tests/test_analysis.py` + `tests/core/test_cli_analyze_seam.py` 113 failed, 51 passed (`assay: error: unrecognized arguments: collect ...`) |
| O2 seam lazy/one-way | T3 (5), T5 (2), T6, T7 green | (a) eager `import assay_analysis` in `cli.py`: `test_only_cli_run_analyze_imports_assay_analysis` red (1 failed, 13 passed). (b) `raw[:1] != ["analyze"]`: both T5 tests red (`test_analyze_forwards_argv_and_streams_to_the_analysis_package`, `test_the_judge_needs_no_analysis_package_except_for_analyze`). (c) `from assay.mutation import _x` in `analysis/src/assay_analysis/cli.py`: `test_analysis_reaches_no_private_judge_name` red (1 failed, 4 passed) |
| O3 one wheel/zipapp | T7 + T8 green (wheel build offline) | `where = ["src"]` in pyproject (working tree): `test_pyproject_ships_both_packages_from_one_distribution` and `test_the_installed_analyze_command_runs_from_the_one_wheel` red (2 failed). NOT run for the T8 `built` tests: `build_release.build` builds the COMMITTED HEAD, and a break must never be committed, so T8's own negative is untested by a working-tree break (its assertions on wheel contents are the same as the purity test's) |
| O4 own lane/rigor | `analysis/tests` with `--cov=analysis/src/assay_analysis --cov-branch`: 930 stmts, 492 branches, 100% | `test_digest_refuses_a_directory_as_an_artifact` renamed `xtest_` (not collected): 220 passed, `evidence.py` 99% (1 stmt, 1 branch missed, line 47), TOTAL 99% |
| O5 B105 scope named | T9 (20 tests) and T10 (30 tests) green | `verify_scope` returns immediately: 7 T9 tests red (analysis target, missing tracked source, whole-`src`, r3 outside, unclassified package, stale out-of-scope, missing-from-targets). Gate script without `"${analysis_sources[@]}"` in the pyflakes call: `test_a_planted_unused_import_in_the_analysis_package_reddens_the_lint_phase` red (1 failed, 29 passed) |
| O6 judge R1 without analysis tests | preflight argv, `tests` only: 5754 passed, 20 skipped, 2 deselected, 1 failed (environmental, below). Coverage of `src/assay`: 12571 stmts, 5554 branches, only `git.py:457` missed | n/a (positive-only; an analysis-only-covered judge line would show up here, none does) |

O6 detail. The one failure, `tests/core/test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused`, is environmental: a stray `/tmp/.git` (dir, from Sep 28) exists on this host. It fails identically on the base `4c53f28c` in a temporary `git worktree add --detach` (`DID NOT RAISE AssayError`; worktree removed). The single missed line `git.py:457` (`no .git marker found in any ancestor`) is exactly the line that test covers, so the 99% is the same environmental effect, not a gap left by moving the analysis tests. `git.ignore_rule_source` is covered by the W3-placed `tests/core/test_b105_git_process_boundaries.py`; no judge test was needed and no pragma was added.

## Collect-only (final, HEAD)
- `tests`: 5783 (base 5975). `analysis/tests`: 221. Default run (both testpaths): 6004.
- Final run of `analysis/tests`: 221 passed. Docs tests (`tests/core/test_docs_examples_and_vocabulary.py`, `test_documented_analysis_commands_parse_with_shipped_cli`, 53 selected): green after the docs commit; the full `tests` run above ran on the docs commit `d816dbf4`.

## Commits (final)
1. `1cd16baf`, 2. `4f2fd892` (code, last code commit), 3. `5e763cb2` (checkpoint log), 4. `d816dbf4` (docs: README, DESIGN-GUIDE `### Package boundary (A-478)`, CONSUMERS, INTERNAL-CONSUMERS, CHANGES `[Unreleased]` Changed, B127 backlog status DONE), 5. this LOG.

## Deviations (successor)
- D6 The docs put the `### Package boundary (A-478)` section at the end of DESIGN-GUIDE (last subsection of "Review evidence analysis") rather than near :3406, because that Appendix is a rejected-arguments table and the analysis section is the file's last one.
- D7 O3's T8 negative was not exercised (see the O3 row).
- No editing slips in this session: every file change used the Edit tool.

## Commits
1. `1cd16baf` layout, `evidence.py`/`cli.py`, judge seam, pyproject, `assay.toml` lane, T1, T2, T4, T5, T6, T7, contract-test edit, `tests/conftest.py` standalone copy of `analysis/src`.
2. `4f2fd892` `b105_report_check.verify_scope` (+T9), gate script `run_analysis_lane` + lint scope (+T10), T3, T8.

## Collect-only
- Before (base): `tests` 5975 (`analysis/tests` absent). Full default run 5975.
- After (HEAD `4f2fd892`): `tests` 5783, `analysis/tests` 221, default run (both testpaths) 6004. `tests` lost the 214 moved tests and gained 22 (T4 0 new, T5 2, T6 2, T7 3, T8 2, T9 9, T10 4); `analysis/tests` = 214 moved + T2 2 + T3 5.

## Oracles measured so far
- O1 CLI unchanged: `assay --help`, `analyze --help`, `analyze {collect,report,verdict,record} --help`, `analyze collect` (exit 2), bare `analyze` (exit 2), `analyze report --expected-commit a*40 --verdict l /nonexistent` (exit 2) captured on the base and after the move: stdout and stderr byte-identical (`cmp`) for all 9 invocations (source tree, `PYTHONPATH=src:analysis/src`). T1 (214 moved tests, `analysis/tests`): 214 passed. Negative NOT YET RECORDED (remove the `_run_analyze` call).
- O2: T3 (5), T5 (2), T6 (`test_only_cli_run_analyze_imports_assay_analysis` + tainted checker test), T7 pass. Negatives NOT YET RECORDED.
- O3: T7 `test_the_installed_analyze_command_runs_from_the_one_wheel` and T8 (zipapp `analyze --help`, exit 2 report, wheel contents) pass locally without Docker (`build_release.build` runs offline). Negative (drop `analysis/src` from `where`) NOT YET RECORDED.
- O4: local `pytest analysis/tests --cov=analysis/src/assay_analysis --cov-branch`: 100% lines and branches on all 3 files (930 stmts, 492 branches, 0 miss). Gate marker `analysis-lane-passed` is for the controller. Negative NOT YET RECORDED.
- O5: T9 (20 tests in `test_b105_report_check.py`, hermetic tmp-repo cases included), `test_self_lane.py`, T10 (`test_distribution_gate.py` 30 passed) green. Negatives are the T9/T10 tests themselves (they assert the refusals); deliberate break of the checker NOT YET RECORDED.
- O6: NOT YET RUN (see continuation: run the full `tests` suite once with the preflight's `--override-ini=pythonpath=src --cov=src/assay --cov-branch` flags, locally under nice/ionice).

## Deviations / notes
- D1 `analysis/tests/conftest.py`: the brief's loader alone breaks collection. When one run collects both `tests` and `analysis/tests`, pytest leaves `sys.modules["conftest"]` bound to the analysis conftest, so 141 judge test modules failed at `from conftest import ...`. The loader therefore serves the judge conftest's names lazily through a module `__getattr__` (not a copy into globals, so no hook is registered twice and no fixture leaks into `dir()`). No fixture is re-exported as such; the moved tests need none.
- D2 `tests/conftest.py` (outside the Touch list): the `standalone` fixture also copies `analysis/src` into its scratch source tree, otherwise `where = ["src","analysis/src"]` builds a wheel without `assay_analysis` (T7 needs it). File count outside the Touch list so far: `tests/conftest.py`, `tests/test_self_lane.py` (named in brief step 8, so not counted), `tests/core/test_cli_lanes.py` (T4, listed). Only `tests/conftest.py` is truly extra: within the allowance of 2.
- D3 `verify_scope` message details the brief left open: (b) reports the non-`assay` names, or all names if only `assay` is missing; (a), (d), (e) messages are `judgment.resolved.source_roots ... != ['src/assay']`, `judgment.<tier>.targets names <t>, out of B105 scope by decision A-478` (r3 outside `src/assay/`: `... not under src/assay/`), and `judgment.<tier>.targets differ from the tracked src/assay sources: missing [...], extra [...]` (`, or are not sorted` when the sets are equal). The r1 targets are read from `judgment.r1.targets`, r2 from `judgment.r2.targets` (skipped when there is no r2), r3 from `judgment.r3.targets`. The T9 positive fixture takes its targets from `assay.toml`.
- D4 T10 additions beyond the brief: two behavioural tests for `run_analysis_lane` (failure never laundered; marker needs both the lane and `assay verify`).
- D5 Process slips (no effect on content): two files (`tests/core/test_import_contracts.py` T6.3 tail, `tests/test_b105_report_check.py` T9 tail) were appended with a shell heredoc `cat >>` instead of the Edit tool; the content is what git shows. One stray `sed -i` on `/dev/null` (no-op, failed).
- No consumer-visible change beyond CD30's list found so far: `assay analyze` help/exit codes byte-identical (O1); `import assay.analysis` gone (CD19).

## For the controller's gate run (so far)
- `tester-unified`: `ASSAY_GATE_PHASE=analysis-lane-passed` (new), `pyflakes-clean` (now also lints `analysis/`), `ASSAY_GATE_CONTAINER_EXIT=0`, `ASSAY_REGISTERED_GATE_COMPLETE=1`.
- `self-qualification-preflight` (O6 and the B105 checker's `ACCEPTED ... scope=src/assay out_of_scope=analysis/src/assay_analysis:A-478` line): judge R1 100% without the analysis tests. Any gap found is fixed with a judge test in the W3-placed file of that module, never a pragma.
- Expect on this host only: the stray `/tmp/.git` failure of `test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` (and its `git.py:457` coverage miss); a gate container without that stray dir should be fully green.
- The `analysis` lane (`assay.toml`) needs `pythonpath = ["src","analysis/src"]` from pyproject; the `tester-unified` lane uses `--override-ini=pythonpath=` (installed wheel).
- Consumer-visible changes (CD30): none to a documented CLI, schema path or lane key. Removed undocumented `import assay.analysis` (CD19); second top-level package `assay_analysis` in the wheel/zipapp. 7.2.0 stands.

READY-FOR-GATE 4f2fd892
