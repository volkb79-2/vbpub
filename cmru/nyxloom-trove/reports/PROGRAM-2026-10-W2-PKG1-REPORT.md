# W2-PKG1 report: root `cmru` CLI adoption and redesign (PARTIAL, checkpointed)

Branch `cmru-w2-pkg1`, base `287813c9d`. First implementer session cut at a green boundary
(far past the ~60 call / 120k checkpoint, because the grammar change touched ~35 test files).
Remaining work is in `PROGRAM-2026-10-W2-PKG1-CONTINUATION.md`. Edit discipline: every file
change was made with the Edit/Write tools (no sed, heredoc or script wrote a repository file).
The only heredoc wrote the scratch runner `scratchpad/pkg1/run.sh` and `h.py` (outside the repo).

## Claims (all from runs in this session)
- Baseline before any edit: `3250 passed, 2 skipped` (the full suite, serial, nice/ionice, test.lock).
- Current head: `3262 passed, 2 skipped, 2 failed`. The 2 failures are tests that pin things this
  package had to change but that belong to other packages (see "Cross-package test debt").
- NOT run: the `coverage` and `canary` lanes, any plant/revert mutation table, `cli-extended audit`.
  Nothing below claims them.

## Scope items done
| Item | State | Where |
|---|---|---|
| 1 registry via `cmru_registry()`, identity from metadata (D2), `_source_tree_version`/`_dev_version_from_describe`/`_cmru_version` deleted | DONE | `cli.py` `_build_cli`, `cli_support.py` `cmru_version/cmru_identity` |
| 1 `target_argument()` on every root verb | DONE | `_build_cli` (one shared spec) |
| 1 exit 3 + one line when cmru is not installed | DONE for `cli.main` (console entry) | `cli.main`, `cli_support.report_not_installed`; test `test_a_missing_distribution_is_exit_3...` |
| 2 `dry_run=True` on run/dependencies/build/publish/changelog/release/cleanup/abandon | DONE | `_build_cli`; `_dispatch` normalises `args.dry_run/yes` |
| 2 library `--yes` (cleanup), `--json` (worktrees, dependencies, status) via `runtime.output.primary` | DONE | |
| 2 `dependencies` `Requires("--dry-run", ("--write",))` | DONE (D4) | `_build_cli` |
| 3 B1 `run --step` (absorbs `run-step`; verb and 4 flags removed) | DONE | `_orchestrate`; CLI-09 closed (usage error naming declared steps) |
| 3 B2 `status` read-only + `--json` records | DONE | `version.status_records`, `cli._status` |
| 3 B3 release: `--ahead-check-ref` (+ deprecated `--ref` alias with warning), `--discard {logs,artifacts,evidence}`, `--set-version` exactly one project (CLI-11, also status) | DONE | `_resolve_ahead_check_ref`, `_release_or_status` |
| 3 B4 `publish` required exclusive `--build-output ID | --from-checkout` | DONE | |
| 3 B5 `cleanup` required mode group incl. `--policy`; `--remove-assets` refuses a target; library `--yes` | DONE | |
| 3 B6 `abandon [BRANCH|PATH]` takes over build-worktree discard; `cleanup --discard-build-worktree` removed; `worktrees` hint updated | DONE | `_abandon_build_worktree` |
| 4 section C narrowing | PARTIAL: `_DOMAIN_ERRORS` applied to the 3 boundaries (build, release preflight, release parent), abandon sites (git root, listing, config load, per-candidate classifier + 3 decorations, post-confirm recheck), `_push_tags` x2, rollback, `_transaction_workspace_from_env`. Kept `except Exception ... raise` at the two rollback-then-reraise sites (`_run_tagged_build_and_publish`). `transaction.py` untouched (17 `Exception` + 5 `BaseException` sites NOT reviewed) | `cli.py` |
| 5 section E taxonomy | PARTIAL: `exit_codes.REFUSED = 4`; release plan refused, stale tool-deps inside release, uncommitted paths, abandon blockers/recheck/lock-held now 4. NOT done: `init` decline 0, `tool-deps` stale 4, `standards` issues 4, tester-gate missing config 3 (PKG-2 files); SPEC S8 table not written | |
| 6 section D env vars | NOT DONE (see continuation) | |
| 7 D10 | DONE in my files: launcher (`_create_bound_cmru_launcher`) and `transaction.run_child` no longer add the cli-extended source | |
| 8 CLI-NN | CLI-09, 11, 19 (structural child argv, `_child_release_args`), 06/22 (cli.py sites) closed. CLI-15 closed (no git describe at all). CLI-12/13 (getpy/resolve), 16 (taxonomy, partial) and 21 (env) not closed/belong to PKG-2 | |
| 9 D3 `manifest.py` | DONE: both versions from the wheel names; malformed name raises (no `0.0.0`) | `manifest._version_from_wheel_name` |
| PKG-0 test nits | DONE: literal legacy expectations, `ALL` with project outside the order, non-default `target_argument` name, non-vacuous policy test | `test_cli_support_registry.py` |

## Tests added/changed
New `tests/test_w2_pkg1_root_cli.py` (usage-error targets, status JSON keys, `--ref` alias, `--set-version`,
`--discard` choices, exit 4 for plan/tool-deps/uncommitted/lock, publish source group, remove-assets+`all`,
KeyError propagates vs RuntimeError reported, abandon translation, D10 run_child PYTHONPATH, manifest D3,
launcher version). New/changed in `test_cli_abandon.py` (build worktree by branch and path), `test_cleanup_dry_run_yes.py`
(bare cleanup refused, removed flag), `test_workspace_adversarial_review.py` (CLI-19), `test_cli_dispatch.py` (D2, exit 3),
`test_smaller_modules_adversarial.py` (D3). Version-describe tests that pinned the deleted git reader were deleted
(8 tests in 8 files); the many `--run-tests/--discard-*-on-release/--ref/--discard-build-worktree/bare publish|cleanup`
callers were migrated.

## Cross-package test debt (do NOT fix here; list for the controller)
- `tests/test_init_scaffolding.py:150-154` (PKG-2): imports `_cmru_version` from `cmru.cli`; switch to `cmru.cli_support.cmru_version`.
- `tests/test_git_auth.py:435-470` (PKG-4): `test_run_child_self_release_imports_candidate_cmru_source` expects the candidate
  `libraries/cli-extended/src` as the 3rd PYTHONPATH entry; after D10 the list is `[candidate/cmru/src, candidate/libraries/worktree/src, <inherited>]`.
  `tests/test_project_fixture.py:34,48` also pin cli-extended (not yet failing; PKG-4 owns them).
- `tests/test_cli_extended_semantics.py:317` and `tests/test_module_entrypoints.py:42` still expect the runner module refusal text naming `cmru run-step` (PKG-2 owns `runner.py`; they pass today).
- `runner.py` (PKG-2) still defines `runner_cli`; the root no longer mounts it. PKG-2 decides to delete it or keep `cmru.runner.run_step` only.
- `tool_deps.py:704` stale exit 2 -> 4 and `standards.py` issues exit 2 -> 4, `scaffold.py` init decline -> 0, `tester_gate.py:690` missing config -> 3 (all PKG-2).
- `runner.py:357,516,517,672,674` read/write `CMRU_RUN_LOG`, `CMRU_SHOW_RUN_DETAILS`, `CMRU_LOG_APPEND`; the section D rename to `CMRU_INTERNAL_*` needs those in the same commit.

## Deviations / honest notes
- `args.dry_run`/`args.yes` are `None` or absent unless given (library defaults), so `_dispatch` normalises them to bools at entry;
  `_orchestrate` reads `getattr(args, "dry_run", False)`.
- `status --json` lists only CHANGED projects (that is all `detect_changed_projects` returns); `changed` is therefore always true.
- `release --ref` is a visible deprecated option (not hidden): the audit rule AC-08 forbids hidden options.
- The `except Exception` sites in `transaction.py` and `controller/`/`agent/` were not touched.
- `SPEC.md` S-CLI.9 grammar and semantic rows for run/publish/release/status/cleanup/abandon/run-step were updated so
  `test_cli_spec_inventory` passes; S8 exit table, SKILL.md and README are NOT updated.
