# cmru KI-53 REPORT

Branch `cmru-ki53-nested-wheel` (base `ce1696562`).

## Diff summary
- `src/cmru/handlers.py`: new `_git_toplevel`, `_wheel_builder_mount_root` (git top-level when it contains `cwd.parent`, else `cwd.parent`), `_wheel_builder_env_args` (sorted name-only `-e` for `SOURCE_DATE_EPOCH` and `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_*`). `cmd_wheel_build` mounts the mount root and passes it as `mount_root`; `-w cwd.parent` and positional source unchanged.
- Tests (`tests/test_builtin_handlers.py`): `test_wheel_build_nested_project_mounts_the_worktree_root` (real linked worktree), `..._top_level_project_argv_is_unchanged` (full argv equality), `..._copied_one_project_repo_mounts_the_parent`, `..._forwards_build_env_by_name_only`, `..._forwards_no_env_when_unset`, `test_git_toplevel_is_none_outside_a_repo_and_mount_root_falls_back`, `test_wheel_builder_mount_root_ignores_a_toplevel_not_containing_the_parent`. Two existing container tests and one in `test_runtime_deep_adversarial.py` now stub `_git_toplevel` (they stub `subprocess.run` globally, which broke real git discovery).
- `KNOWN_ISSUES_TODO_BACKLOG.md` KI-53 (fixed); `CHANGES.md` Unreleased/Fixed line.

## Other builder launches (audit)
`handlers.py` docker buildx bake (build/push), `docker login` (handlers, runner), `tester_gate.py` docker runs: none use a `cwd.parent`-only mount; none are setuptools-scm wheel builds. No change.

## Plant / revert
| Plant | Result |
|---|---|
| `_wheel_builder_mount_root` returns `cwd.parent` | `test_wheel_build_nested_project_mounts_the_worktree_root` FAILED; restored |
| drop `*_wheel_builder_env_args()` from the argv | `test_wheel_build_forwards_build_env_by_name_only` FAILED; restored |

## Verdicts
- `tests/test_builtin_handlers.py` + `test_runtime_deep_adversarial.py`: 64 passed; new handler lines all covered (branch cov, host run).
- Full host suite (no assay module on host): 37 failures, identical set with and without this change (pre-existing).
- Gate lane `coverage` (container): FAIL exit 1 at `--maxfail=1`: 962 passed, 1 failed `tests/test_cli_release_snapshot_boundaries.py::test_release_rejects_internal_handoff_on_dry_run` (raises RuntimeError at `cli.py:4583` instead of returning 1). This fails identically on base main without my change, so it is pre-existing and unrelated; not fixed here. `canary` (same pytest, maxfail=1) and `installed-wheel` were therefore not run: canary would hit the same failure.
