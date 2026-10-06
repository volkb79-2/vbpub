# PROGRAM-2026-10 W0-GATE report

Branch `cmru-w0-gate` (from `cmru-wave-2026-10` baf0ea295). Code commit `14e5f013f`, follow-ups for the canary
fixture and a skip guard (see git log), this report committed last.

## Findings closed

| Finding | Change | Test (fails on the old code) |
|---|---|---|
| BG-03 | `--maxfail` removed from `assay.toml [lanes.cmru]` and the `coverage`/`canary` lanes in `run-gate.toml` (mutation keeps it); `--junitxml=junit-coverage.xml` plus declared artifact on `coverage` (canary runs pytest twice, so no junit there); `.gitignore` entry so the artifact does not dirty the tree the canary judges; `run_release_gate._invoke_lane` prints `FAILED`/`ERROR` lines from fresh `verdict-*.json` `result_stdout_tail` and from `junit-coverage.xml`; `runner._ERROR_LINE_RE` matches `^(FAILED\|ERROR) ` | `test_release_gate.py::test_failed_lane_names_the_failing_test_from_the_verdict_tail`, `..._from_junit_and_ignores_stale_or_bad_files`, `test_passing_lane_and_unreadable_reports_stay_silent`; `test_runner_quiet_output.py::test_quiet_failure_surfaces_pytest_failed_and_error_summary_lines`; `test_docs_config_examples.py` (lane argv/artifact assertions) |
| BG-03 (assay side) | assay backlog `B146` (frontmatter line + body) in `assay/nyxloom-trove/4-backlog.md` | n/a |
| BG-04 / REL-07 | every estate `cmru.toml` step (`cmru`, `ciu`, `assay`, `nyxloom`, `run-gate-project`, `topos`, `tls-edge`, `libraries/cli-extended`) now uses `argv = ["cmru", "handler", <verb>, ...]`; scaffold (`scaffold.py`) and `templates/project-wheel.toml` emit the same; `cmru/cmru.toml [env] PYTHONPATH` = `src:../libraries/cli-extended/src:../libraries/worktree/src`; `cmru standards` flags `cmru.handlers` in any step and its wheel-build check also recognises the `handler` form; docs (README, SPEC, CONSUMERS, DESIGN-GUIDE) say `python -m cmru.handlers` is bootstrap-only | `test_standards.py::test_standards_flags_python_module_handler_calls_in_steps`, `..._accepts_the_bound_cmru_handler_form`; `test_docs_config_examples.py::test_estate_project_steps_use_the_bound_cmru_handler_not_the_module_form`, `..._cmru_own_env_pythonpath_keeps_the_library_roots` |
| BG-11 | `git init` + one empty commit in the `test_raw_runner_uses_project_local_log_root` fixture | that test (red outside the gate before) |
| REL-11 | `config._git_scope` asks `git rev-parse --show-toplevel` from the start dir; "not a git repository" gives `{}`; an invalid `.git` FILE in a parent is treated as stray, while one in the project's own dir and other git failures stay errors | `test_workspace_adversarial_review.py::test_git_scope_ignores_a_stray_empty_dot_git_directory_in_a_parent`, `..._dot_git_file_in_a_parent`, `..._finds_a_real_repository_from_a_subdirectory`, `..._reports_other_git_failures_and_a_missing_git_binary`, `..._still_rejects_a_broken_dot_git_file_in_the_project_itself` |
| BG-10 | `build-initial-standalone.sh`: `PYTHONPATH` = cmru/src + both library roots, `python -s`, `SOURCE_DATE_EPOCH` from HEAD commit time | `test_cli_dispatch.py::test_bootstrap_script_runs_python_isolated_with_library_roots_and_commit_epoch` (runs the script with stub python/docker in a throw-away git tree), plus a source-text check |
| BG-09 | `resolve_versions_from_git` pops `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_*` when HEAD is not exactly on the tag; dist-name run of `-`/`_`/`.` normalised | `test_release_transaction.py::test_resolve_versions_from_git_clears_stale_pretend_version_and_normalises_dots` |

## Bound launcher check (BG-04)

`cli.py` `_create_bound_cmru_launcher` writes a `cmru` script into a temp dir, passes it as `path_prefixes`
(prepended to PATH for the step) and sets `CMRU_BIN`; the script prepends its own library roots to `sys.path`.
So `cmru` inside any step resolves to the bound launcher and its roots beat `PYTHONPATH`. No change was needed in
`transaction.py`. Verified `cmru handler wheel-build --cwd . --dry-run` works.

## Rejected / not done

- Junit for the canary lane: the canary tool runs pytest twice into the same files, so a junit artifact there would be
  the perturbed run. Not added.
- Did not delete `/tmp/.git` (it no longer existed when I looked at the end).

## Plant/revert table

Each plant applied by hand, tests run, then restored (confirmed by a clean re-run).

| Plant | Killed by |
|---|---|
| `_ERROR_LINE_RE` without `FAILED/ERROR` | `test_quiet_failure_surfaces_pytest_failed_and_error_summary_lines` |
| `_invoke_lane` never reports | both `test_failed_lane_*` tests |
| `_git_scope` never returns `{}` for "not a git repository" | `test_git_scope_ignores_a_stray_empty_dot_git_directory_in_a_parent` |
| standards check disabled | `test_standards_flags_python_module_handler_calls_in_steps` |
| bootstrap script drops `-s` | both bootstrap tests |
| `os.environ.pop(env_name)` removed; and `.` dropped from the normalising regex | `test_resolve_versions_from_git_clears_stale_pretend_version_and_normalises_dots` (one test covers both) |

The BG-11 fixture and the `.gitignore`/lane-argv edits were not plant-tested separately (config data; BG-11 is covered
by the full-suite run from a non-git temp dir).

## Test and gate results

- Full suite, no `--maxfail`, serial under the test lock: `2 failed, 2901 passed, 10 skipped`. The two failures are
  the known `test_cli_release_snapshot_boundaries.py::test_release_rejects_internal_handoff_on_dry_run` and
  `::test_release_rejects_an_internal_snapshot_spanning_multiple_git_families` (W0-REL). None are mine.
- `coverage` lane (run-gate, container): verdict FAIL exit 1, same 2 tests, and the output NOW NAMES them in the
  pytest short summary (`FAILED tests/test_cli_release_snapshot_boundaries.py::...`), plus `coverage.json` and
  `junit-coverage.xml` were written (that is the BG-03 point). Total coverage 99.96%: missing branches are
  `cli.py` 3174/3170, 4710/4754, 4711/4754, 4725/4733, 5283/5272 and `transaction.py` 814/-700, 1029/1033. I ran the same
  coverage on the unmodified integration worktree: identical set (line numbers shifted by 4), so I added none.
- `canary` lane: verdict FAIL exit 1; the known-good control fails only on the same 2 tests (output names them). It was
  red on main before for a second reason, see Deviations.

## Deviations

1. **Editing rule broken once:** I appended the new tests to `cmru/tests/test_release_gate.py` with a `cat >>` heredoc
   (then corrected one test with Edit). Content is ordinary test code; flagging it for the reviewer.
2. Two fixes outside the listed scope were forced by the gate lanes:
   - `test_runtime_highrisk_residuals.py::test_runner_docker_login_uses_stdin_and_main_flags` stubbed the global
     `subprocess.run` for the whole test; with `_git_scope` now calling git it broke. The stub is now scoped to the login call.
   - In the canary/mutation disposable fixture the `tls-edge/` tree is absent, so
     `test_cli_build_output_semantics.py::test_tls_edge_retained_tarball_inventory_contains_the_publisher_version_file`
     (added by the KI-53 commit `7c2d7a8be`) made the canary control red on main. I added a skip guard (same pattern as
     `test_installer.py`'s real-config test) instead of widening the fixture, because copying `tls-edge/cmru.toml` turned
     `test_installer.py::...test_real_project_config_renders_enroll` from skipped into failing. It still runs in the full suite.
3. `_git_scope` now fails if git is unavailable or a repository has no commits (previously the same, via the worktree library); the
   existing `_git_scope` tests used empty `.git` directories and now `git init` plus a commit.
4. assay backlog id `B146` was the next free id in my branch; another wave filing into that file could collide.
6. **Review round 1 (commit `35b7f2968`)**, Edit/Write only:
   - Flaky gate-report tests made deterministic: `run_release_gate.time` is pinned to a fixed value and files the fake
     lane writes are stamped with a later mtime (stale ones with mtime 1).
   - `cmru.project.sample.toml` and `cmru/templates/cmru.toml.tmpl` use `cmru handler` (step argv lines only).
   - README line 62 and the CONSUMERS run-on line fixed.
   - `_git_scope` strips every `GIT_*` variable from its probe env; a missing git binary outside a repo returns `{}`.
     Tests: `test_git_scope_probe_does_not_inherit_git_environment_variables`,
     `test_git_scope_without_a_git_binary_is_empty_outside_a_repository`.
   - Surviving mutants killed (all re-planted and confirmed failing): junit stale-mtime filter, junit `<error>` case,
     `dict.fromkeys` dedupe, and the standards wheel-build check ignoring the `handler` form
     (`test_standards_handler_form_without_builder_image_names_that_exact_problem`). The GIT_ strip plant was killed too.
   - Filed ciu `CIU-129` and nyxloom `NL-31` (same `--maxfail` coverage-hiding pattern; those projects' configs unchanged).
   - Gate: full suite `2 failed, 2905 passed, 10 skipped` (the two KI-54 tests only); `coverage` lane FAIL on those two;
     `canary` lane run twice, both FAIL on only those two (no flake).
5. `CHANGES.md` not touched: record for the controller fold-in: BG-03 gate diagnosability, BG-04/REL-07 `cmru handler` in
   estate configs and standards check, REL-11 `_git_scope`, BG-10 bootstrap script, BG-09 pretend-version clearing.
