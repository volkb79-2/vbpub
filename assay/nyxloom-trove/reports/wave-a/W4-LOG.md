# W4 (B123) implementer log

Base SHA: `11ace522` (branch `wave-a-w4-test-split`, worktree `.worktrees/wave-a-w4-test-split`, project dir `assay/`). Scratch: `/tmp/claude-1003/-workspaces-vbpub/0eeb333f-34bb-4155-942f-6ae260e97f0b/scratchpad` (collect lists, run outputs; not committed).

STATUS: COMPLETE locally. The registered gate was NOT run by the implementer (CD44); see "For the controller's gate run". One item needs a carver ruling (D6, P5 stand-in for `go_stmtpos.py`), recorded below; it is not a product gap.

## Commits
1. `8a8dc69e` code: P1 moves and split, P2 `gate/tests` package (`support.py`, `conftest.py`), the layout finished (`ROOT_PINNED` deleted), P3 lanes (`assay.toml`), pyproject comment, gate-script lint over `gate/tests`, P4 receipt (gate script functions, checker CLI, `self-qualification-gate.sh` driver), CD32 host-busy check, O2 AST check, O5/O6/O7/O7a tests, `test_self_lane` pins.
2. `da355733` docs and prose: README, DESIGN-GUIDE, CONSUMERS, CHANGES, decisions notes (A-468, A-130, A-350), backlog (B123 DONE, S1 bullet, CD15 value), plan §3 path, `nyxloom.toml` prose, CD43 moved-path prose in `tests/`/`gate/tests`/`pyproject.toml`.
3. `730f2db7` code (last code commit): `test_the_shipped_source_tree_is_pyflakes_clean` links `gate/tests` into its scratch clone (found by the full `gate/tests` run; the focused runs before did not cover it).
4. this LOG and `W4-REPORT.md` (docs only).

## Oracle table (negatives applied locally with the Edit tool, observed red, reverted with `git checkout -- <file>` or file removal; `git status` clean after each; nothing committed)

| # | Positive (HEAD) | Negative (break -> red) |
|---|---|---|
| O1 | collect-only: `tests gate/tests` 5749, `gate/tests tests` 5749, `gate/tests/test_self_hosting.py` 7 | `gate/tests/__init__.py` moved away: `tests gate/tests` = `2177 collected, 132 errors` (Interrupted); restored |
| O2 | `tests/core/test_import_contracts.py::test_no_judge_test_reaches_into_the_tooling_tree` green over every `tests/**/test_*.py` outside `fixtures/`; `git grep -nw standalone -- analysis/` empty; the brief's P2 grep over `tests/` only finds `test_import_contracts.py`'s own checker and its synthetic strings | planted `tests/core/test_zz_plant.py` with `def test_x(standalone)`: red, `{'core/test_zz_plant.py': ['parameter standalone of test_x']}`; file removed |
| O3 | `gate/tests/test_self_lane.py` 22 passed (exact argv per lane, no `-o/--override-ini*/--ignore*/--deselect*/-p*`, exact ini keys, no second config file, driver text, O9) | `--override-ini=pythonpath=src` re-added to the self-qualification argv: 4 red (`test_self_qualification_is_full_source_r0_through_r3`, `test_each_lane_declares_exactly_its_pinned_argv`, `test_the_b105_lanes_carry_no_pytest_switch...`, `test_preflight_measures_the_same_complete_source_inventory_before_r2`); `addopts = "-q"` added to pyproject: `test_pytest_ini_options_pin_the_source_paths_and_test_trees` red; `pythonpath = ["src"]`: same test red |
| O4 | the lint tests over synthetic clones each holding a `gate/tests` file: planted unused import in `gate/tests` reddens (`'os' imported but unused`, names the file), clean tree passes, marker once; missing `gate/tests` tree refused (`lint phase found no gate/tests sources to lint`); the real tree is pyflakes clean (`test_the_shipped_source_tree_is_pyflakes_clean`) | `"${gate_test_sources[@]}"` dropped from the pyflakes call: `test_a_planted_unused_import_in_a_gate_tests_module_reddens_the_lint_phase` red |
| O5 | receipt-only accepts the exact receipt (`B105_TESTER_UNIFIED_PASS=commit=... tree=...`) and sha256-length names; refused with `B105_REPORT_REJECTED=`, exit 2, empty stdout: other commit, other tree, uppercase hex of the right commit, right commit +24 hex digits, right tree +24 digits, lane `self-qualification`, `schema_version` `True`/`2`, extra key, missing key, list, `null`, missing file, non-JSON text; `--receipt-only` with another report flag is a usage error; full mode names each missing flag | commit comparison removed from `verify_tester_unified_receipt`: 4 red (other commit, uppercase, +24 digits, S1 other-commit) |
| O6 | a valid full `self-qualification` report (the same one the accepting test uses) plus another commit's receipt, another tree's receipt, no receipt flag, or a missing receipt file: exit 2 with `B105_REPORT_REJECTED=`; the receipt is checked before the report; the preflight lane refuses a receipt flag | `_read_receipt` returns immediately (receipt check returns early): 17 red |
| O7 | `run_bash` tests: exact JSON and mode 0644 with no stray temp file; malformed ids (uppercase, 39 chars, 65 chars, empty, `main`) write nothing and create no `assay/` dir; `finish_registered_gate` after HEAD moved, or a different tree at the same HEAD, fails with no receipt and no COMPLETE; `run_registered_tester_container() { return 7; }` gives exit 7, no receipt (an earlier stale one is cleared), no COMPLETE, no RECEIPT line; a green stub gives the receipt and exactly one COMPLETE (order: stub output, RECEIPT, COMPLETE); the entry section's last line is `run_registered_gate ...` and only `finish_registered_gate` echoes COMPLETE | receipt written before the HEAD check: 2 red (head moved, other tree); `... || true` after the run call: `test_a_red_container_leaves_no_receipt_even_when_a_stale_one_existed` red |
| O7a | PATH stub `docker` whose `ps` prints `run-gate-x`, `run-gate-y`, `some-other-container`: exit 3, stderr `ASSAY_GATE_INCONCLUSIVE=host busy — rerun: run-gate-x,run-gate-y`, a pre-existing receipt byte-identical, the stub log has exactly one call and it is `ps`, the stubbed tester never launches; `ps` printing `other-container` / `not-run-gate-x` proceeds to the (stubbed) tester and writes the receipt | check disabled (`[[ -n "$names" && -z "$names" ]]`): red; check moved after `clear_registered_gate_receipt`: red (receipt gone) |
| O8 | local part: `git grep -nw standalone -- analysis/` empty; P5 (below). In-gate part: controller | n/a |
| O9 | `test_the_option_files_the_opt_in_qualification_tests_read_exist` green | `_PROBE_JS` restored to `Path(__file__).resolve().parents[1] / "fixtures" / ...`: red |
| O10 | controller (preflight) | n/a |

## P5 stand-ins (local pytest runs)
- `provenance.py`: `PYTHONPATH=src pytest tests/core/test_cli_provenance_and_request_base.py tests/core/test_b105_provenance_boundaries.py --cov=assay.provenance --cov-branch`: 46 passed, 92 stmts, 42 branches, 0 miss, 100%.
- `__init__.py:42-45` (`except PackageNotFoundError`): `tests/core/test_b105_source_coverage_controls.py` alone: `src/assay/__init__.py` 10 stmts, 0 miss, 100%.
- `go_stmtpos.py` `_staged_helper`: **the stand-in as written FAILS**: `test_b105_go_stmtpos_boundaries.py` alone (`--cov=assay.adapters.go_stmtpos --cov-branch`): 15 passed, 87%, missing `151, 195-196, 219, 294-303, 316, 324, 343, 353, 404, 412, 418`. Line 151 is inside 124-171 (`raise _refuse(_MISSING_HELPER...)` for an explicit `helper_dir` with no `stmtpos.go`). Adding the neighbouring judge file `tests/adapters/go/test_adapters_go_stmtpos_invoker.py` (its `helper_dir=tmp_path / "no-helper-here"` case, `:229`) gives 30 passed, 124 stmts, 42 branches, 0 miss, 100%. That file stays in `tests/` and is collected by both B105 lanes, so R1 whole-target coverage is not affected by the move; I read the brief's "alone" as too narrow, did not touch `src`, and did not stop the rest of the work. Carver ruling requested (D6).

## Collect-only counts
| Selection | Before (base `11ace522`) | After (HEAD `730f2db7`) |
|---|---|---|
| `tests` | 5693 | 5405 |
| `analysis/tests` | 224 | 224 |
| `gate/tests` | (absent) | 344 |
| `tests gate/tests` | (n/a) | 5749 |
| `gate/tests tests` | (n/a) | 5749 |
| default `pytest` (both testpaths) | 5917 | 5629 |

`tests` 5693 -> 5405 = -291 moved to `gate/tests` +3 new (`test_import_contracts` part 4). `gate/tests` 344 = 291 moved + 53 new (checker +24, gate script +20, `test_self_lane` +9, ids counted per parametrized case). Id comparison (basename plus test id, before list of `tests` vs after list of `tests gate/tests`): no id disappeared except the 6 wheel tests of `test_verdict_schema_is_packaged.py`, which reappear in `test_verdict_schema_wheel.py`, and 12 docs-example ids whose number is a DESIGN-GUIDE/CONSUMERS line (`[CONSUMERS.md:1173]` etc.) and moved with the docs edit (the same 12 tests, new line-number ids).

Local full runs (nice/ionice, serial): default `pytest`: 5628 passed, 1 skipped, 1 failed (`tests/core/test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused`, the known `/tmp/.git` B134 failure); `pytest tests` (that one deselected): 5404 passed, 1 skipped; `pytest gate/tests`: 333 passed, 11 skipped after the last fix (the run at `730f2db7`^ had one failure, fixed in `730f2db7` and re-run alone: passed).

## Deviations (with reasons)
- D1 `gate/tests/support.py` re-implements W2's loader (same module name `assay_judge_conftest`, `sys.modules` check first, `spec_from_file_location`) instead of importing `analysis.tests.conftest.load_judge_conftest`: `analysis/**` is forbidden to touch and a gate test must not depend on the analysis tree. Whichever loads first, the other gets the same module. Reversed in the review fix pass (W4R-4): support.py now imports W2's loader.
- D2 `--tester-unified-receipt` missing or forbidden in full mode is raised inside the checker's `try` (stderr `B105_REPORT_REJECTED=...`, exit 2), because "every refusal" is `B105_REPORT_REJECTED`. Usage errors (missing full-mode flags, another flag with `--receipt-only`, a missing receipt-only flag) use argparse's `parser.error` (exit 2, argparse text), as the brief says for those.
- D3 `tests/core/test_verdict_schema_is_packaged.py` (the kept half, `git mv`) has a rewritten docstring; the wheel half `gate/tests/test_verdict_schema_wheel.py` carries the old docstring text.
- D4 The layout test's `_tests_files` no longer skips `qualification/` (that folder left `tests/`) and now excludes only `fixtures/`; I added `test_the_tests_root_holds_only_conftest_and_fixtures` (the brief's "afterwards `tests/` root holds only ...").
- D5 `nyxloom.toml`: "four inner phase markers" is now "seven" (the script emits seven `ASSAY_GATE_PHASE=` markers: pyflakes-clean, judge-provenance-bound-to-the-installed-wheel, self-hosted-lane-passed, analysis-lane-passed, independent-self-hosting-passed, wheel-installed, attestation-hardened). I also added a short S1/CD32 paragraph there and the `pythonpath` comment fix (W2 D10).
- D6 P5 go_stmtpos stand-in: see above.
- D7 Process, honestly: no shell edits of repo files. Two harmless shell slips: one `cat >> /dev/null <<EOF` no-op, and scratch analysis files (`moves.tsv`, `prose-hits.txt`) in the scratchpad were written with a shell heredoc (never repo files). Every repo change used Edit/Write, `git mv`, or `git checkout --` (reverts of my own breaks).
- D8 `decisions.md` "notes only": I appended one `**Note (W4, B123):**` sentence to the A-468, A-130 and A-350 rows.
- D9 Not done because outside W4's touch list (recorded in REPORT for W10): the `src/assay` comments that name moved test paths.

## For the controller's gate run
Run at the FINAL HEAD of the branch: the receipt binds the commit and tree, so any later commit (even docs) makes an earlier receipt not match; the full lane's check compares against its own `HEAD`.

1. `docker ps` shows no `run-gate-*` container (the gate now exits 3 itself: `ASSAY_GATE_INCONCLUSIVE=host busy — rerun: <names>` means rerun).
2. `cd assay && nice -n 19 ionice -c3 python ./run-gate.py tester-unified > ../W4-gate.log 2>&1; echo exit=$?`, then read separately: `ASSAY_GATE_PHASE=` lines (wheel-installed, attestation-hardened, self-hosted-lane-passed, analysis-lane-passed, independent-self-hosting-passed, pyflakes-clean), `ASSAY_GATE_CONTAINER_EXIT=0`, then `ASSAY_REGISTERED_GATE_RECEIPT=<path>` and `ASSAY_REGISTERED_GATE_COMPLETE=1` last, and no `FAILED` line (CD29; the container has no `/tmp/.git`, so `test_git_boundary` should pass there).
3. The receipt: `cat <path>` must be exactly `{"schema_version": 1, "lane": "tester-unified", "commit": "<HEAD>", "tree": "<HEAD^{tree}>"}`, mode 0644. Cheap check without a lane: `python tools/b105_report_check.py --receipt-only --tester-unified-receipt <path> --expected-commit $(git rev-parse HEAD) --expected-tree $(git rev-parse 'HEAD^{tree}')` prints `B105_TESTER_UNIFIED_PASS=commit=... tree=...`.
4. Expected wheel-lane collection: `tests` 5405 + `gate/tests` 344 = 5749, minus the 7 tests of the ignored `gate/tests/test_self_hosting.py` = 5742 collected.
5. `python ./run-gate.py self-qualification-preflight` once (O10; no receipt needed, CD9): the checker line `B105_REPORT_ACCEPTED=self-qualification-preflight ... scope=src/assay out_of_scope=analysis/src/assay_analysis:A-478`, R1 100% line and branch. Record collected/passed/skipped for W7's baseline; the lane collects `tests` only: expect 5405 collected (5404 passed and 1 skipped in the local `tests` run, `test_git_boundary` passing in the container). The preflight argv now has no `--override-ini`: confirm the run imported `src/assay` from the snapshot (R1 coverage is non-empty and at 100%).
6. Decide D6 (the `go_stmtpos.py` stand-in).

## Review fixes (REVIEW-W4, CD48: W4R-1 to W4R-8 all applied)
Each break below was applied locally with Edit, observed red, and reverted (diff against a scratch backup showed the file restored); no break was committed.

| Finding | Commit | Positive | Deliberate-break negative |
|---|---|---|---|
| W4R-1 | `3d51d412` | new `test_a_red_container_that_wrote_a_valid_receipt_itself_leaves_none` green; O7/O7a and the other receipt tests green (15 passed). A container-forged receipt plus exit 7 leaves no receipt, so `--receipt-only` refuses (absent file) | deleted `trap cleanup_assay_gate_container EXIT` in `run_registered_gate`: the new test red (receipt remained) |
| W4R-8 | `210b45dd` | `test_the_inner_run_refuses_a_head_other_than_the_captured_commit` and the extended docker-argv test green | removed the `-e ASSAY_GATE_EXPECTED_COMMIT` argv line: the argv test red; turned `|| die` into `|| true die`: the new head test red |
| W4R-2 | `353f8431` | `test_a_failing_docker_ps_is_inconclusive_and_leaves_the_receipt` green; busy and other-containers tests green | wrapped `docker ps` in `|| true`: the new test red |
| W4R-3 | `737b7dd3` | `test_self_lane.py` 24 passed | created an empty `assay/pytest.toml`: `test_no_second_pytest_configuration_file_can_shadow_pyproject[pytest.toml]` red (file removed again, never committed) |
| W4R-4 | `91979dda` | new one-loader test green; `test_self_lane.py`, `tests/core/test_import_contracts.py` and `analysis/tests/test_analysis_package_boundary.py` 48 passed; collect-only `gate/tests` 350, `tests gate/tests` 5755, `gate/tests analysis/tests tests` 5979, `test_self_hosting.py` under `--override-ini=pythonpath=` 7 | made `support.judge` a separate module object: the one-loader test red (the first try, a `copy.copy`, failed at collection only, so I redid it with a fresh module) |
| W4R-7 | `5aaa9e4c` | 4 parametrized cases green | changed the checker's message `tester-unified receipt commit` to another string: `[other-commit]` red |
| W4R-5 | `fb784413` | docs-example tests in `tests/` (56 passed, 1 skipped) | docs-only, no oracle in the review |
| W4R-6 | `f9ba8212` | docs-only | none |

Other checks after the fixes: `bash -n` and `shellcheck` clean on `tools/tester-unified-gate.sh` and `tools/self-qualification-gate.sh`; collect-only `tests` 5405, `gate/tests` 350 (344 + 6 new: W4R-1, -2, -4, -8 one each, W4R-3 two parametrized cases), `analysis/tests` 224, default 5629. One full `gate/tests` run (`nice -n 19 ionice -c3`, serial): **339 passed, 11 skipped, 0 failed** (the skips are the opt-in Go/Node qualification tests, the `ASSAY_SELF_HOSTING_VERDICT` witness check and the A-069 fallback).

The wheel-lane collection is now `tests` 5405 + `gate/tests` 350 = 5755, minus the 7 tests of `test_self_hosting.py` = 5748 (the "For the controller's gate run" item 4 above says 5749/5742; that is superseded). The gate must run at the FINAL HEAD: the receipt binds the commit and tree.

READY-FOR-GATE 5aaa9e4c
