# W2-PKG1 continuation brief (seed for a FRESH successor)

State: branch `cmru-w2-pkg1` committed and green except two cross-owned tests (see REPORT). Full suite is ~2m45 serial.
Run tests with `scratchpad/pkg1/run.sh <outfile> <pytest args>` (checks PSI, flock test.lock, nice/ionice, PYTHONPATH of the worktree,
drops the editable finder via pt.py). `scratchpad/pkg1/h.py` runs `cmru.cli.main` from the worktree source.
Still true: Edit/Write only; no sed/heredoc/script writes to repo files.

## Remaining work, in order
1. **Section D env vars** (cli.py + transaction.py + output.py; the runner.py readers are PKG-2's, so coordinate or do it after PKG-2 merges):
   - `CMRU_BIN` -> `CMRU_INTERNAL_BIN` (set `cli.py` `run_project_step` `internal_env`; read `cli.py` `_dispatch_independent_git_families`
     and `transaction.run_child`), honoured only when `transaction.is_transaction_child(repo_root)`; tests pin the old name in
     test_transaction_dispatch_adversarial, test_workspace_adversarial_review, test_core_orchestration_adversarial,
     test_release_end_to_end_real_git (its wrapper seam must move to a PATH-resolved `cmru`), test_cli_build_output_semantics (protected env),
     test_git_auth (PKG-4).
   - `CMRU_LOG_PREFIX_TIME_SHORT` -> `CMRU_INTERNAL_LOG_PREFIX_TIME_SHORT` (`output.py:22`, `_TIME_ENV`).
   - `CMRU_RUN_LOG`, `CMRU_SHOW_RUN_DETAILS`, `CMRU_LOG_APPEND` -> `CMRU_INTERNAL_*` (cli.py `_apply_output_options`, `_prepare_native_release_log`;
     runner.py readers).
   - Document `CMRU_RELEASE_LOG` (operator input) and `CMRU_NATIVE_RELEASE_LOGGING=0` as test-only in SPEC (find where the SPEC lists env).
2. **Section C remainder**: review `transaction.py` `except Exception` (lines ~94, 285, 293, 570, 594, 648, 669, 683, 881, 892, 1327, 1922,
   2437, 2649-2664) and the 5 `except BaseException` (~1989, 2032, 2131, 2230, 2380): narrow to domain types or keep with a comment saying it re-raises
   after cleanup. cli.py `_run_tagged_build_and_publish` two `except Exception: ...; raise` sites need the same comment. `_push_tags` is done.
3. **Section E**: write the exit table into SPEC S8 (0 done/declined, 1 failed after start, 2 usage/config, 3 prerequisite missing, 4 refused
   by policy, nothing changed) and note which verbs use 4 (release plan refused, stale tool-deps in release, uncommitted paths, abandon blockers,
   lock held). PKG-2 owns the delegate side.
4. **Docs for the renames** (estate rule): README getting-started (bare `cleanup`, `cleanup --discard-build-worktree`, `run --run-tests`,
   `release --discard-*-on-release`, `--ref`, bare `publish`), `docs/DESIGN-GUIDE.md` (`CMRU_BIN` line ~130), `.claude/skills/cmru-cli/SKILL.md`
   belongs to PKG-4, CHANGES.md only under a hand-written `## [Unreleased]` if W0-REL has not replaced it (else list for the controller).
5. **Mutation plants** (one per scope item, show each killed, restore). Suggested, all with tests already in place:
   `_DOMAIN_ERRORS` -> `Exception` in `_release_launcher` (test_w2_pkg1 KeyError test); `keep_next = False` in `_child_release_args`
   (CLI-19 test); `!= 1` -> `< 1` for `--set-version`; `exit_code=REFUSED` -> `2` for uncommitted paths; manifest cmru version -> `"0.0.0"`;
   re-add `libraries/cli-extended/src` to `run_child`; drop `if args.dry_run` in `_abandon_build_worktree`; make the cleanup mode group
   not required; delete the CLI-09 validation in `_orchestrate`; `ahead_check_ref` alias assignment removed.
6. **Gate**: commit first, then (one at a time, flock gate.lock) `./run-gate.py --worktree <wt> coverage` and `canary` from `cmru/`; read verdicts in a
   separate step. The coverage lane needs 100%: expect uncovered lines in the new code (`_abandon_build_worktree` declined path is covered;
   check `_status` non-JSON branch, `status_records` `counter` strategy branch, `_resolve_ahead_check_ref` both-given branch, `report_not_installed`).
   `status_records` `counter` and `bump_override` branches are now in a new function: make sure existing tests of `status_cmd` still hit them.
7. **Finish the REPORT**: plant table, gate verdicts, final suite counts. Return hash + REPORT path.

## Seams
- `cli.py`: `_build_cli` (~5650), `_dispatch` (~4230; normalisation at its top), `_release_or_status` (~4770), `_status`, `_release_launcher`,
  `_release_child`, `_abandon`, `_abandon_build_worktree`, `_abandon_locked`, `_child_release_args` (~3085), `_value_taking_flags`.
- `version.py`: `status_cmd` / `status_records`.
- Unchanged-by-design: delegates (`init`, `versions`, `handler`, `tester-gate`, `resolve`, `get-py`, `standards`, `tool-deps`) still build via
  `cmru_identity` (works with metadata identity) until PKG-2 moves them to `cmru_registry`. `UNEXPECTED_EXCEPTIONS_POLICY` stays `"raise"`.
