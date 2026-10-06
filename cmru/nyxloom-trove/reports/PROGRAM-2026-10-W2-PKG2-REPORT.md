# W2-PKG2 REPORT: the delegate registries

Worktree `.worktrees/cmru-w2-pkg2`, branch `cmru-w2-pkg2`, base `287813c9d`.
Commits: `d1318694b` (feature), then two test-only follow-ups for `tests/test_cli_dispatch.py`
(see "Gate verdicts"); the final head is named in the hand-back.

## What was done
All nine delegate builders (init, versions, run-step, handler, tester-gate, resolve, get-py, standards,
tool-deps) build on `cmru_registry()`. New `cmru/src/cmru/delegate_targets.py`
(`resolve_target`, `current_project`) replaces the five copied context blocks (B13). Library controls
(`dry_run`, D4 `Requires`/`Conflicts`, `standards --json`, tool-deps `--json` via `runtime.output`),
the `versions` exit ladder, B8-B12, the exit-code taxonomy (`exit_codes.POLICY_REFUSED = 4`), the §C
narrowing, and SPEC S-CLI.9 + CHANGES `[Unreleased]` lines are in `d1318694b`.

## Findings closed (tests in `tests/test_w2_pkg2_delegates.py` unless noted)
- CLI-10 init non-interactive when facts complete / decline exits 0:
  `test_init_scaffolding.py::test_complete_options_with_yes_write_the_validated_contract`,
  `::test_declined_confirmation_exits_zero_and_writes_nothing`, `test_init_has_the_library_dry_run_and_yes_controls`.
- CLI-12 get-py multi-project stdout refused: `test_get_py_refuses_multi_project_stdout_and_writes_one_project_to_stdout`.
- CLI-13 resolve shape follows selector syntax + config-free `--repo/--prefix`:
  `test_resolve_json_shape_follows_the_selector_syntax`, `test_resolve_config_free_mode_*`.
- CLI-14 handler `--repack` removed: CHANGES line; `test_oci_handlers_take_bake_target_and_refuse_the_old_spelling`.
- CLI-17 one env-fallback rule: `test_tester_gate_reads_every_env_fallback_at_run_time_not_at_parser_build`,
  `test_every_tester_gate_env_fallback_is_named_as_a_default_in_its_help`, `test_tester_gate_old_forward_flag_names_are_gone`.
- CLI-20 scaffold side: init scaffolding tests (templates side is PKG-4).
- B11 renames, §E codes (tester-gate 3, standards 4, tool-deps 4, versions 3/2/1):
  `test_tester_gate_missing_configuration_exits_3`, `test_standards_json_prints_one_document_and_exit_4_names_the_issues`,
  `tests/test_tool_deps.py::test_tool_deps_main_blocks_on_a_stale_pin_by_default`,
  `tests/test_versions.py::test_versions_main_reports_text_and_maps_domain_failures`.
- Ruling: handler entry exits 3 without a distribution, one line:
  `test_handler_module_entry_exits_3_without_a_traceback_when_cmru_is_not_installed`;
  both entries one builder: `test_python_dash_m_handlers_and_cmru_handler_share_one_builder`.
- D1 factory: `test_every_delegate_builder_takes_its_policy_from_the_shared_factory[<9 builders>]`.

## Decisions applied
D1 (shared factory, `python -m cmru.handlers` alias), D4 (conditionally mutating verbs get
`mutating=True, dry_run=True` + `Requires("--dry-run", ...)`), D5 (partial output depth; `output.py` and
`--log-prefix-time-short` kept), D6 (init on the library prompt driver, validate-before-write).
`unexpected_exceptions` stays `"raise"` (via the factory constant).

## Plant mutations (each applied by Edit, the targeted suite run, then `git checkout` restore)
Suite per plant: the delegate/init/standards/tool-deps/versions/runner/handler/ki12/ki17/coverage-gaps
test files (440 tests, 440 pass on the clean tree). Tree clean after every restore.

| # | Scope item | Plant | Killed by |
|---|---|---|---|
| 1 | factory | getpy builder built with a bare `CliRegistry(... "raise")` | `test_every_delegate_builder_takes_its_policy...[getpy]` |
| 2 | B9 bake target | oci-image-build option `--bake-target` -> `--target` | `test_oci_handlers_take_bake_target...` |
| 3a | CLI-17 | `--memory` default read from env at build time, runtime read kept | SURVIVED: equivalent mutant (same observable behaviour) |
| 3b | CLI-17 | `--forward-background-slice` default frozen at build, runtime read removed | `test_tester_gate_reads_every_env_fallback_at_run_time...` |
| 4 | CLI-12 | get-py multi-stdout check disabled | `test_get_py_refuses_multi_project_stdout...`, `test_release_coverage_gaps::test_getpy_context_outputs_and_rejections` |
| 5 | CLI-13 | resolve `single` = `len(names)==1` | `test_resolve_json_shape...[argv3-...-map]` |
| 6 | §C | `resolve_via_latest_json` `except (OSError, ValueError)` -> `except Exception` | `test_latest_json_does_not_disguise_a_programming_error` |
| 7 | §E standards | exit 4 -> 1 | standards json test + 6 in `test_standards.py` |
| 8 | §E tool-deps | exit 4 -> 1 | `test_tool_deps_main_blocks_on_a_stale_pin_by_default` |
| 9 | versions ladder | prerequisite exit 3 -> 2 | `test_versions_main_reports_text_and_maps_domain_failures` |
| 10 | init B12 | non-interactive gate `_missing_facts` removed | 5 init tests (`test_complete_options_*`, `test_accepted_confirmation_writes`, ...) |
| 11 | init decline | decline -> `SystemExit(1)` | `test_declined_confirmation_exits_zero_and_writes_nothing` |
| 12 | handler ruling | `except VersionLookupError` -> `KeyError` | `test_handler_module_entry_exits_3_without_a_traceback...` |
| 13 | CLI-D2 | resolve `Conflicts(--repo, --config)` removed | `test_resolve_config_free_mode_refuses_conflicting...[argv0]` |
| 14 | D4 get-py | `Requires("--dry-run", output)` removed | `test_get_py_dry_run_is_a_declared_constraint...` |
| 15 | B13 | `resolve_target` ignores the invocation context | `test_resolve_target_reads_the_invocation_context...`, `test_resolve_json_shape...[argv6-...]` |
| 16 | standards --json | `include_json=False` | 3 json tests, `test_standards.py`, a monorepo-wizard init test |
| 17 | handler dry-run | `dry_run=False` on mutating handlers | `test_mutating_handlers_have_the_library_dry_run...`, `test_oci_handlers_take_bake_target...` |

Not planted separately: tool-deps `--json` (`runtime.output.primary`) and the run-step delegate's shared
helper use (covered by `test_run_step_delegate_still_resolves_one_project_through_the_shared_helper`
and the factory param, but no dedicated plant was run).

## Gate verdicts (each read in a separate step after the run)
- `coverage` lane: first run FAIL (3321 passed, 1 failed, coverage 100%). Cause:
  `tests/test_cli_dispatch.py::test_source_module_invocation_works_from_the_cmru_project_directory` ran
  `python -m cmru.handlers --help` in the gate container, where `src/` is on PYTHONPATH but the wheel is not
  installed; under D2 that is exit 3 by design. The test now follows the child's own view (exit 3 + the
  one-line message, no traceback; or exit 0 + help when installed). A first fix keyed on the parent's
  `importlib.metadata` view failed again for the same reason, so the child's exit code decides.
  Final run: PASS, 3322 passed, 2 skipped, coverage 100.00% (`/tmp/run-gate/lanes/coverage/e774afd173ee12ca684761f05a139db6.log`).
- `canary` lane: PASS, exit 0 (`/tmp/run-gate/lanes/canary/21454ab258bc02c980cbe582bb059d16.log`).
- No coverage gaps appeared beyond that test; none needed new tests.
- Earlier cut-point full suite via pt.py: 3322 passed, 2 skipped.
- The affirmative branch of the dispatch test (installed distribution -> help) was run under pt.py only;
  the exit-3 branch was run only inside the gate container.

## Deviations
- `runner.runner_cli` kept as a converted compatibility export (root `cli.py` still mounts `run-step`).
- `init` project-level options are `--folder/--id/--kind/...`, not `--project-*` (argparse prefix matching
  would make `--project` ambiguous and an old test pins its rejection); a monorepo always prompts.
- A non-integer `SystemExit` in `versions` is rendered by the library as exit 1 (was 2); nothing raises one.
- `versions check --json` keeps its sorted indented `print` (stable-order contract); other prints stay (D5).
- `--dry-run` help is the generic library sentence (D4/CLI-EXT-21).
- Root-side tests edited only where my delegates changed their output: `test_cli_delegated_dispatch_adversarial`,
  `test_cli_adversarial_contracts`, `test_cli_review_decisions`, and `test_cli_dispatch` (above).
- `exit_codes.py` gained `POLICY_REFUSED`; PKG-1 may add the same line (trivial merge).
- Edit/Write tools only for repository files. Two read-only `python3 -` heredocs by the first
  implementer wrote no repository file. Mutation restores used `git checkout -- <file>`.
- Plant 3a survived because it was an equivalent mutant; replaced by 3b.

## Handoff to W2-INTEG (seams I know of; not touched here)
1. `cmru/src/cmru/runner.py:633` `runner_cli()` and `:670` `_run_step_cli`: delete both once root `run --step`
   is merged (keep `run_step`). Mount to remove: `cmru/src/cmru/cli.py:5720` (import) and `:5730`
   (`VerbSpec("run-step", ... delegate=runner_cli() ...)`). The same edit changes
   `tests/test_w2_pkg2_delegates.py::test_run_step_delegate_still_resolves_one_project_through_the_shared_helper`
   and the `runner.runner_cli` entry in the factory parametrisation (`test_w2_pkg2_delegates.py:35`).
2. §D `CMRU_INTERNAL_*` env renames: `runner.py:355` `CMRU_RUN_LOG`, `:514` `CMRU_SHOW_RUN_DETAILS`, `:515`
   `CMRU_LOG_APPEND`, with writers at `cli.py:176`, `:178`, `:2469`, `:2472`, `:2473` (and any in
   `transaction.py`). Rename both sides in one commit.
3. `cmru/src/cmru/cli.py:5725-5726,5735-5736` mount `standards_cli` / `tool_deps_cli`; their exit codes are now 4
   (policy refused). Check `cli.py:4057-4074` (release-time tool-deps blocking path) and SPEC S8 exit table
   against code 4 for stale/standards refusals; `exit_codes.POLICY_REFUSED` is defined in `exit_codes.py:7`.
4. SPEC S8 exit table: needs rows for 4 (standards issues, tool-deps blocking) and tester-gate missing
   configuration = 3 (S-CLI.9 rows for my verbs are already in `cmru/docs/SPEC.md` at ~457-471 and 553-555).
5. Rename sweeps in docs outside my files: `--bake-target` for `oci-image-build`/`oci-image-push`
   (`run-gate-project/REMOTE-LANES-BUILDKITE.md:408` mentions the handlers; check templates/README/DESIGN-GUIDE and
   `cmru.toml` of the six projects calling `python -m cmru.handlers oci-image-*` with `--target`);
   `--forward-cgroup-parent-var` / `--forward-cgroup-parent-gates-var` to `--forward-background-slice` /
   `--forward-gates-slice` in callers (orchestration files, `cli.py`/`transaction.py` tester-gate argv builders;
   my grep found none in tracked non-SPEC/CHANGES files of this worktree, but PKG-1/PKG-4 files were not edited
   or fully swept).
6. Gate PYTHONPATH: `cmru/run-gate.toml:58` (and the canary argv) put `src` on PYTHONPATH without installing the
   wheel, so `python -m cmru.handlers` exits 3 there; `test_cli_dispatch.py::test_source_module_invocation_...`
   was relaxed to cover both outcomes. When PKG-4 installs the wheel in the lane, tighten it to exit 0 + help.
7. `unexpected_exceptions` policy flip to `"report"` (`cli_support.UNEXPECTED_EXCEPTIONS_POLICY`):
   `test_every_delegate_builder_takes_its_policy_from_the_shared_factory` already monkeypatches it and
   will hold after the flip.
8. `exit_codes.py` merge: both PKG-1 and PKG-2 may add `POLICY_REFUSED = 4`; keep one.
