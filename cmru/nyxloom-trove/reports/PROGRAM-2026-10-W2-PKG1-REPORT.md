# W2-PKG1 report: root `cmru` CLI adoption and redesign

Branch `cmru-w2-pkg1`, base `287813c9d`. First implementer cut `e4b8d0017`; this fresh successor
finished the package in the files PKG-1 owns, under the controller RE-SCOPE (seams moved to W2-INTEG,
coverage/canary lanes not run here). Every file change was made with Edit/Write (no sed, heredoc or
script wrote a repository file); mutation plants were reverted with `git checkout -- <file>`.

## Claims (all from runs in this session)
- Baseline before any edit: `3250 passed, 2 skipped`.
- Final head, full suite, serial, nice/ionice, test.lock: **`3274 passed, 2 skipped, 2 failed`**. The only
  failures are the two allowed seam tests, both listed under "Handoff to W2-INTEG":
  `tests/test_git_auth.py::test_run_child_self_release_imports_candidate_cmru_source` and
  `tests/test_init_scaffolding.py::test_cli_init_help_has_version_headline`.
- Focused coverage (`--cov=cmru.cli --cov=cmru.manifest --cov=cmru.transaction --cov=cmru.cli_support
  --cov=cmru.version --cov-branch`, whole suite): **100% statements and branches, 0 uncovered lines** in all five
  (cli.py 2720 stmts/1204 branches, transaction.py 1785/726, version.py 323/152, cli_support.py 84/26, manifest.py 83/30).
- NOT run (by controller direction): the `coverage` and `canary` lanes, `cli-extended audit`.

## Scope items
| Item | State |
|---|---|
| 1 registry via `cmru_registry()`, identity from metadata (D2), `_source_tree_version`/git-describe readers deleted, shared `target_argument()` | DONE (first cut) |
| 1 exit 3 + one line when cmru is not installed (`cli.main`) | DONE |
| 2 `dry_run=True`, library `--yes`/`--json`, `dependencies` `Requires` (D4) | DONE |
| 3 B1 `run --step`; B2 `status` read-only + `--json`; B3 `--ahead-check-ref` (+ deprecated `--ref`), `--discard`, `--set-version` exactly one; B4 `publish` source group; B5 `cleanup` mode group, `--remove-assets` refuses target; B6 `abandon` takes build worktrees | DONE |
| 4 section C narrowing | DONE in my files (see below) |
| 5 section E taxonomy | DONE for root-verb sites (4 = refused: plan refused, stale tool-deps in release, uncommitted paths, abandon blockers/recheck/lock). SPEC S8 table and delegate-side codes -> W2-INTEG |
| 6 section D env renames | MOVED to W2-INTEG (span runner.py) |
| 7 D10 | DONE in my files (`cli.py` launcher, `transaction.run_child`) |
| 8 CLI-NN | CLI-06, 09, 11, 15, 19, 22 closed in cli.py/transaction.py/manifest.py. CLI-12/13 are PKG-2's; CLI-16 (taxonomy) and CLI-21 (env) finish in W2-INTEG |
| 9 D3 manifest | DONE: `manifest._version_from_wheel_name`, malformed name raises |
| PKG-0 test nits | DONE |

## Decisions made in this session
- **Exception review (`transaction.py`).** Added `transaction._DOMAIN_ERRORS = (RuntimeError, OSError, ValueError,
  subprocess.SubprocessError)` (the worktree library's `WorkspaceError` is a `RuntimeError`). Narrowed to it: the 11 sites
  that translate a library/OS failure to a clean `RuntimeError` (child worktree validation, family grouping x2,
  resume_workspace discover, retained-release adopt x3, remove_workspace x2, resume-scope discover, build-workspace
  reopen) and the best-effort cleanup swallow after a failed `git reset`. A `KeyError`/`TypeError` now keeps its type and
  traceback (tests below). Kept broad, each with a comment saying it cleans up and re-raises: the artifact-rename
  rollback `except Exception` (re-raise), the retention rollback `except Exception` (its inner `shutil.move/rmtree`
  handlers narrowed to `OSError`), and the 5 `except BaseException` sites (fd/stage cleanup, always re-raised, deliberately
  including KeyboardInterrupt). `cli.py`: the two `except Exception ...; raise` sites in `_run_tagged_build_and_publish`
  are commented as deliberately broad (tag rollback / publication-started report, both re-raise).
- **`status --json` lists EVERY selected project** with a `changed` field (redesign B2, "a stable record per project"); this
  was feasible: `status_records` iterates the selected projects, fills `changed: false`, `last_tag` from
  `_latest_tag_for_prefix`, and `bump/next_version/note = None` for unchanged ones. The human text table (`status_cmd`)
  still shows only changed projects, as before.
- **Test-isolation fix:** `test_delegate_built_with_the_factory_inherits_the_global_option` used `delenv(raising=False)`, so
  the `CMRU_LOG_PREFIX_TIME_SHORT=1` the code under test writes leaked into later tests (made two `test_cli_dispatch`
  tests fail when the registry file ran first). It now uses `setenv` so teardown restores the original state.

## Tests added/changed this session
`tests/test_w2_pkg1_transaction_exceptions.py` (new: tuple pin; operational errors translated, KeyError propagates, at
resume_workspace / read_release_scope_for_path / project_git_family_groups x2 / remove_workspace);
`tests/test_w2_pkg1_root_cli.py` (status records for unchanged projects, text table only-changed, `_value_taking_flags`
incl. unknown verb); `tests/test_workspace_adversarial_review.py` (CLI-19 asymmetric case `--set-version 1.2.3 demo`,
which the earlier symmetric assertion could not distinguish: the plant below survived until it was added);
`tests/test_version_runner_contracts.py` (status test passes the projects); `tests/test_cli_support_registry.py` (env leak).

## Plant/revert mutation table
Focused set: the PKG-1 test files, abandon/cleanup/registry/dispatch/workspace/smaller-modules/release-flow, version contracts
(`-x`, so the first killer is shown). Each plant applied by Edit, reverted by `git checkout`.
| # | Scope item | Plant | Result |
|---|---|---|---|
| M1 | 4 (cli.py narrowing) | `cli._DOMAIN_ERRORS = (Exception,)` | KILLED `test_uncommitted_paths_refuse_with_exit_4` |
| M2 | 4 (transaction narrowing) | `transaction._DOMAIN_ERRORS = (Exception,)` | KILLED `test_domain_errors_tuple_is_the_operational_set` (+ the KeyError tests) |
| M3 | 8 CLI-19 | `keep_next = False` (option values no longer skipped) | first run SURVIVED; test strengthened; re-run KILLED `test_child_release_args_finds_the_target_structurally` |
| M4 | 3 B3 / CLI-11 | `--set-version` `!= 1` -> `< 1` | KILLED `test_set_version_needs_exactly_one_project_for_release_and_status` |
| M5 | 5 exit taxonomy | plan-refused `sys.exit(REFUSED)` -> `sys.exit(2)` | KILLED `test_a_refused_release_plan_exits_4_and_marks_the_plan` |
| M6 | 9 D3 | wheel-name version -> `"0.0.0"` | KILLED `test_manifest_versions_come_from_the_wheel_names...` |
| M7 | 7 D10 | re-add `libraries/cli-extended/src` to `run_child` | KILLED `test_run_child_self_release_never_prefers_a_candidate_cli_extended` |
| M8 | 3 B6 | `if args.dry_run:` -> `if False:` in `_abandon_build_worktree` | KILLED `test_abandon_discards_a_retained_build_worktree_by_branch_or_path[branch]` |
| M9 | 3 B5 | cleanup mode group not required (all four options) | KILLED `test_a_bare_cleanup_names_the_required_modes_and_changes_nothing` |
| M10 | 8 CLI-09 | undeclared-step validation `if absent:` -> `if False:` | KILLED `test_run_dry_run_respects_step_first_project_order_and_rejects_bad_plans` (`test_cli_extended_semantics.py`, not in the default focused set) |
| M11 | 3 B3 alias | `vargs.ahead_check_ref = legacy` -> `pass` | KILLED `test_release_ref_is_a_deprecated_alias_of_ahead_check_ref` |
| M12 | 3 B2 status | unchanged projects not listed (`if name not in changed_by_name` -> `if False`) | KILLED `test_status_records_list_every_selected_project_with_a_changed_field` |
| M13a | 1 D2 | `cli_support.cmru_version()` returns `"0.0.0"` | KILLED `test_the_bound_launcher_reports_the_installed_metadata_version` |
| M13b | 3 B4 | publish source group not required | KILLED `test_publish_requires_exactly_one_explicit_source` |
(A first M9 attempt that made only `--policy` non-required hit the library's "inconsistent required metadata" ValueError and
was discarded as an invalid plant; the table row is the valid all-four plant.)

## Handoff to W2-INTEG
All of these need files owned by PKG-2/PKG-4 or span them. Line numbers are at head of `cmru-w2-pkg1`.

**D. Environment renames to `CMRU_INTERNAL_*`** (honoured only when `transaction.is_transaction_child(repo_root)`):
- `CMRU_BIN` -> `CMRU_INTERNAL_BIN`: set at `cli.py:335` (`run_project_step` `internal_env`); read at `cli.py:3132`
  (`_dispatch_independent_git_families`) and `transaction.py:3455` (`run_child`). Tests pinning the old name:
  `test_workspace_adversarial_review.py:774,780,809,839`, `test_version_runner_contracts.py:191`,
  `test_cli_build_output_semantics.py:1537,1542,1561,1563,1574`, plus `test_transaction_dispatch_adversarial`,
  `test_core_orchestration_adversarial`, `test_release_end_to_end_real_git` (its wrapper seam must move to a PATH-resolved `cmru`),
  `test_git_auth` (PKG-4). Doc: `docs/DESIGN-GUIDE.md:130`.
- `CMRU_LOG_PREFIX_TIME_SHORT`: `output.py:22` (`_TIME_ENV`); tests read it via `output._TIME_ENV`
  (`test_cli_support_registry.py` pins `"1"`, not the name).
- `CMRU_RUN_LOG`, `CMRU_SHOW_RUN_DETAILS`, `CMRU_LOG_APPEND`: writers `cli.py:183,185,2411,2414,2415`; readers/writers in PKG-2's
  `runner.py:357,516,517,672,674`. Must change in one commit.
- Document `CMRU_RELEASE_LOG` (operator input, `cli.py:2405`) and `CMRU_NATIVE_RELEASE_LOGGING=0` (test-only, `cli.py:2393`) in `docs/SPEC.md`.

**The two seam test failures:**
- `tests/test_init_scaffolding.py:150-154` imports `_cmru_version` from `cmru.cli` (removed): use `cmru.cli_support.cmru_version`.
- `tests/test_git_auth.py:435-470` (`test_run_child_self_release_imports_candidate_cmru_source`) expects the candidate
  `libraries/cli-extended/src` as 3rd PYTHONPATH entry; after D10 the list is `[candidate/cmru/src, candidate/libraries/worktree/src, <inherited>]`.
  `tests/test_project_fixture.py:34,48` also pin cli-extended (not yet failing).

**SPEC S8 exit table** (`docs/SPEC.md:1689`, currently "four-value scheme identical to CIU S10.3"; also prose at ~1927-1930 and S2.1/S6 mentions at
781, 1204): write 0 done/declined, 1 failed after start, 2 usage/config, 3 prerequisite missing, 4 refused by policy with nothing changed. Verbs
using 4 in the root CLI: release plan refused (`cli.py:5187`), stale tool-deps in release (`cli.py:5225`), uncommitted paths
(`cli.py:4969`), abandon blockers/recheck/lock held (`cli.py:5294,5656,5661,5706`).

**Delegate-side exit codes (PKG-2 files):** `tool_deps.py:704` stale 2 -> 4; `standards.py` issues 2 -> 4; `scaffold.py` init decline -> 0;
`tester_gate.py:690` missing config -> 3. Also `runner.py` still defines `runner_cli` (root no longer mounts it); refusal text naming
`cmru run-step` is pinned at `test_cli_extended_semantics.py:317` and `test_module_entrypoints.py:42`.

**README / DESIGN-GUIDE rename sweep:** `README.md:130` and `:481` (`cleanup --discard-build-worktree` -> `abandon <path>`),
`:330-331` and `docs/DESIGN-GUIDE.md:482-483` (`--discard-*-on-release` -> `release --discard {logs,artifacts,evidence}`),
`README.md:599,607` and `docs/DESIGN-GUIDE.md:260` (`run-step` -> `run --step`); also sweep bare `cleanup`, `run --run-tests`, `--ref`
(now `--ahead-check-ref`), bare `publish` (now needs `--build-output ID | --from-checkout`). `.claude/skills/cmru-cli/SKILL.md` is PKG-4's.
CHANGES.md: nothing added here; the controller/W0-REL owns the `[Unreleased]` entry.

## Deviations / notes
- `args.dry_run`/`args.yes` are `None`/absent unless given (library defaults); `_dispatch` normalises them at entry.
- `release --ref` is a visible deprecated option (audit rule AC-08 forbids hidden options).
- `docs/SPEC.md` S-CLI.9 verb grammar rows were updated in the first cut so `test_cli_spec_inventory` passes; the S8 table is not written (above).
- The `controller/` and `agent/` trees named in the first report no longer exist (W0-RETIRE).

## Review fix round 1
Fix commit `6aa9964e1` on `cmru-w2-pkg1` (base for the round `ee8228814`). Full suite after the round: **3336 passed, 6 skipped,
2 failed** (exactly the two seam tests); focused branch coverage of cli, cli_support, manifest, transaction, version stays 100%.
Lanes not run.

1. **CRITICAL, pre-verb `--dry-run` dropped for the child: fixed.** `cli._forwarded_global_args(parsed, rest)` re-serialises every global
   the parent PARSED (`--dry-run`, `--log-level`/`--quiet`/`--debug` (one verbosity family), `--debug-raw`, `--color`/`--no-color`,
   `--log-prefix-time-short`) from the namespace; a flag already in `rest` is never doubled, and a verbosity/colour family is skipped
   whole when `rest` already chose one. `_child_release_args(..., forward_from=)` appends it, and the three spawn paths pass the parsed
   namespace: the release launcher, the isolated build, and `_dispatch_independent_git_families(..., forward_from=)` (multi-family
   subprocess). `run_child` only receives the argv built by `_child_release_args`, so no other spawn rebuilds a command. Grep guard:
   `runtime.command_argv` is read once (`_dispatch`), `sys.argv` once (`main`); a test pins both counts.
   Tests (`tests/test_w2_pkg1_review_round1.py`): `cli.main(["--dry-run","release",...])` and `["release",...,"--dry-run"]` give a child
   argv with exactly one `--dry-run`; a parametrised matrix over {release, build} x {pre, post} x {single family via `run_child`,
   two families via `subprocess.run`} x {dry-run, log-level, quiet, debug, no-color, log-prefix-time-short} asserts each flag appears once
   (a dry-run build never spawns a child, so those 4 cells are skipped); a plain release invents no globals; unit test of dedup/families.
2. **HIGH, empty mode values: fixed.** Cleanup dispatch uses `is not None` for `--delete-unmanaged-release-tag`, `--delete-build-output`,
   `--remove-assets`, then an explicit `elif vargs.policy:` and a final usage-error `else` (no implicit policy default). Publish
   validates on `build_output is not None` and fails closed (usage error) when neither `--build-output` nor `--from-checkout` is set.
   Defence in depth: `cli._non_empty` is the argparse `type` of `--build-output`, `--remove-assets`, `--delete-build-output`,
   `--delete-unmanaged-release-tag`, so `''`/whitespace exits 2 with "the value must not be empty". Tests: the three cleanup options x
   `''`/`'   '` exit 2 and never reach policy/assets cleanup; the dispatch-level (parser bypassed) variants; `publish --build-output ''`
   exits 2 and never runs a step; `--remove-assets 0` still means assets cleanup.
3. **MEDIUM, `run --step`: tests added.** `run --step lint --step test` executes exactly `[(demo, lint), (demo, test)]` in order even
   when `default_steps = [build]`; without `--step`, the defaults run.
4. **Nits:** SPEC S-CLI.9 status row says "every selected project, changed or not"; `cli.py` recovery text names `cleanup --policy` /
   `--remove-assets AGE`; CHANGES `[Unreleased]` states the compact `--json` change (and the global forwarding);
   `test_cleanup_no_longer_discards_build_worktrees` now supplies a valid mode and asserts "unrecognized arguments" naming the flag
   (argparse reports the missing mode group first, which is why the old assertion could pass for the wrong reason); EOF blank lines removed
   from the three test files; relative `abandon` paths stay refused, message now says "pass the absolute path shown by `cmru worktrees`"
   (test in `test_cli_abandon.py`).
5. Rulings applied: the section D renames, S8 table and non-SPEC-S-CLI.9 status sentences stay with W2-INTEG (handoff section kept);
   the `cmru.toml` PYTHONPATH item is PKG-4's and ignored.

Re-plants (focused: the new test file), each reverted by `git checkout`:
| # | Plant | Result |
|---|---|---|
| M16 | `steps = list(default_steps)` (`--step` ignored) | KILLED `test_run_step_list_is_executed_in_the_given_order_and_overrides_default_steps` |
| M21 | child drops `--dry-run` (pre-verb position is the only one this affects) | KILLED: `...forwards_each_parsed_global_once[dry-run-release-pre-single]`, `[...-pre-multi]`, `test_release_with_a_root_dry_run_is_a_dry_run_child`, the forwarding unit test; post-verb cells correctly stay green |
| M39 | cleanup back to truthiness + implicit `else` default (`elif True:`) | KILLED: `test_the_cleanup_dispatch_fails_closed_without_any_mode` and the three `..._slips_past_the_parser_never_reaches_the_policy_cleanup[*]` |
| M39b | publish validation back to truthiness (`if build_output_id and ...`) | KILLED `test_an_empty_id_that_slips_past_the_parser_still_fails_validation` |
