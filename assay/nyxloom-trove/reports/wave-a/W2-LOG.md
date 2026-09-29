# W2 (B127) implementer log

Base SHA: `4c53f28c` (branch `wave-a-w2-analysis`). Scratch: `/tmp/claude-1003/-workspaces-vbpub/0eeb333f-34bb-4155-942f-6ae260e97f0b/scratchpad` (before/after CLI captures `before-*.out|err`, `after-*`; `collect-before.txt`; not committed).

STATUS: CHECKPOINT reached after commit 2 (context/tool-call budget). See `W2-CONTINUATION.md` for what remains (docs, oracle negatives, full-suite/O6 run, READY-FOR-GATE).

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
