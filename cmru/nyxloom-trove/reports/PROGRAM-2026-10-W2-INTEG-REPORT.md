# W2-INTEG REPORT (parts A and B; part C pending the controller's image)

Branch `cmru-w2-integ`, base `d8ac6b66d`. All edits by Edit/Write. Nothing pushed, no image built.

## Evidence (what I ran)
- Full cmru suite via `pt.py` (serial, flock, nice/ionice, PSI full avg60 0.00 before): **3512 passed, 6 skipped, 0 failed, 0 xfail**, with
  `--cov=cmru --cov-branch`: TOTAL 11875 stmts / 5130 branches, 0 missed, **100%** (log `scratchpad/integ-run4.log`).
- The controller baseline's one failure (the strict XPASS `test_run_child_never_puts_a_candidate_cli_extended_source_root_on_pythonpath`)
  had its marker removed; it passes.
- NOT run: the `coverage` and `canary` run-gate lanes, the installed-wheel/gate-image paths, any image build (part C).

## Per item
**A1 env renames.** `CMRU_BIN/RUN_LOG/SHOW_RUN_DETAILS/LOG_APPEND/LOG_PREFIX_TIME_SHORT` -> `CMRU_INTERNAL_*` (writers and readers, one change:
`cli.py`, `runner.py`, `output.py`, `transaction.py`). `CMRU_LOG_PREFIX_TIME_SHORT` is internal (set only by `--log-prefix-time-short`; no
doc names it as an operator input). New `transaction.internal_launcher(repo_root)` + `INTERNAL_BIN_ENV`: `CMRU_INTERNAL_BIN` is honoured only
inside a verified transaction child (invalid child context fails closed -> ignored); used by `run_child` and `_dispatch_independent_git_families`.
**Deviation:** the four log/presentation variables are NOT child-gated: they are set and read by the same process from its own
`--show-run-details/--log-append/--log-prefix-time-short` flags (gating would break `cmru run --show-run-details` outside a transaction). Only the
executable-selecting variable is gated. SPEC S8.1 documents this, plus `CMRU_RELEASE_LOG` (operator input) and `CMRU_NATIVE_RELEASE_LOGGING=0` (test-only).
Tests moved: `test_workspace_adversarial_review`, `test_transaction_dispatch_adversarial`, `test_core_orchestration_adversarial`,
`test_cli_build_output_semantics`, `test_version_runner_contracts`, `test_git_auth`, `test_high_residual_boundaries`, `test_cli_config_final_adversarial`,
`test_config_runtime_exhaustive`, `test_runtime_contracts_final`, `test_runner_quiet_output`, `test_release_native_logging`.
`test_release_end_to_end_real_git` now uses a `bin/cmru` wrapper on PATH (no env seam).

**A2 run-step.** `runner_cli` and `_run_step_cli` deleted (root mount was already gone on the merged tree), unused imports removed. Removed/rewrote the tests
that exercised them (`test_w2_pkg2_delegates` test + factory entry, `test_release_coverage_gaps`, `test_runtime_highrisk_residuals`,
`test_version_runner_contracts`, `test_cli_adversarial_contracts`, `test_semantic_boundary_contracts`, `test_cli_extended_semantics`; the render-plan
validity assertion was kept as `test_runner_step_plan_rejects_an_invalid_command`). Refusal text is now `Use the installed 'cmru run --step' command`
(`test_cli_extended_semantics`, `test_module_entrypoints`). The `run --step` behaviours are covered by PKG-1's tests.

**A3.** `test_init_scaffolding` already imported nothing from `cmru.cli` on the merged tree (headline test uses `importlib.metadata`): no change.
`test_git_auth` post-D10 test already asserted the post-D10 PYTHONPATH; the strict-xfail marker was on the sibling test and is removed.
`test_project_fixture.py:34,48` already asserts cli-extended is NOT copied (CX-D1-correct): no change.

**A4.** `POLICY_REFUSED` alias deleted; `standards.py`, `tool_deps.py`, `tests/test_standards.py` use `REFUSED`.

**A5 SPEC.** S8 rewritten: five-value table, verbs using 4 (release plan/uncommitted, abandon blockers/recheck/lock, standards, tool-deps), S12.2d
sentence (plan refusal is 4, not 1), S8.1 environment. CIU S10.3 stops at 0-3 (verified in `ciu/docs/SPEC.md`), so the claim "identical" was dropped:
cmru is a superset (adds 4).

**A6 sweep** (table below). **A7** `interactive = ["cli-extended[interactive]>=0.2.0"]` in `pyproject.toml`; `Provides-Extra: interactive` /
`Requires-Dist: questionary>=2.1.1; extra == "interactive"` verified in `libraries/cli-extended/artifacts/cli-extended-v0.2.0/dist/*.whl` METADATA.
Hint now `pip install 'cmru[interactive]'` via `cli_support.INTERACTIVE_EXTRA`, passed as `interactive_extra=` at both `init` run sites (root `main`,
`scaffold.init_main`); tested through both entries.
**A8** not done: the gate image does not yet bake cmru, so both branches of the module-entry test in `test_installed_wheel_subprocess.py` stay.

**B.** N8 literal digest pinned (`test_tester_unified_image`); N16 probe asserts `metadata['Name'] == 'cmru'`; nit 7: the `cmru.cli` alias test matches the
PKG-1 stderr (`Use the installed 'cmru' command; python -m cmru.cli is not supported.`), verified by the passing suite; `forward_from` is now a required
keyword in `_child_release_args` and `_dispatch_independent_git_families` (all 25 test call sites pass `forward_from=None`); PKG-2 handoff 5 = A6.

## Sweep table (outside `.worktrees/`, CHANGES history, archived reports, KNOWN_ISSUES history)
| Hit | Disposition |
|---|---|
| `cmru/README.md` publish/cleanup/discard/run-step (x7) | fixed (`--from-checkout`, `abandon PATH`, `release --discard X`, `run --step`) |
| `README.md:126` bare `publish` | fixed (`--build-output ID`) |
| `docs/RELEASE-TOOLING.md:36`, `ciu/docs/README.md:59`, `game_stuff/empyrion/README.md` (x2, also stale `--project`) | fixed |
| `cmru/docs/{CONSUMERS,DESIGN-GUIDE,RELEASE-TRANSACTIONS,SPEC,plan-contextual-config}.md` | fixed |
| `src/cmru/skills/cmru-cli/SKILL.md` (cleanup modes, `--abandon`, `--ref`, bare publish, `get`) | fixed |
| `delegate_targets.py` docstring | fixed |
| all estate `cmru.toml`/orchestration/`run-gate*.toml`/templates (handler, tester-gate argv) | NO stale callers found; guarded by `test_every_estate_cmru_argv_parses_against_the_registry` and `test_no_estate_contract_uses_a_removed_cmru_spelling` |
| `run-gate-project/REMOTE-LANES-BUILDKITE.md:408` | names the handlers only, no flags: no change |
| `docs/plan-cmru-*.md`, `assay/nyxloom-trove/*`, `cmru/KNOWN_ISSUES_*`, `ciu/nyxloom-trove/archive/*` | historical records, intentionally untouched |
| `--forward-cgroup-parent*` | no callers in tracked non-history files |
| `get-py` multi-project stdout | `plan-contextual-config.md` already corrected by PKG-2; nothing else describes it |

## Plants (each by Edit, test run, reverted; `git diff` of `cmru.toml`/`exit_codes.py` clean afterwards)
| Plant | Killed by |
|---|---|
| (a) `internal_launcher` returns the variable without the child check | `test_the_launcher_variable_is_ignored_outside_a_transaction_child`, `test_an_inconsistent_child_context_fails_closed`, `test_run_child_ignores_the_variable_and_the_old_name_outside_a_child`, `test_the_family_dispatcher_ignores_the_variable_outside_a_child` |
| (b) `_apply_output_options` writes old `CMRU_SHOW_RUN_DETAILS` | `test_writers_set_only_the_internal_names`, `test_output_options_export_only_explicit_flags` |
| (c) `--target` in `cmru/cmru.toml`'s wheel-build argv | `test_every_estate_cmru_argv_parses_against_the_registry` |
| (d) `POLICY_REFUSED = REFUSED` re-added | `test_refused_is_the_only_exit_code_name_for_four` |

## For part C
`cmru/run-gate.toml` still points at `:local`; the controller names the image tag, then I commit the switch with `# TODO(cmru-6.0 landing)`.
