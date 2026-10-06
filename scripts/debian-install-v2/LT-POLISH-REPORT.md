# LT-POLISH report

Branch `lt-polish`, base `a5fca69dc`. Code commit `f26d3ec91` (previous implementer), plant-driven test
additions `4890e1252` and `f7f5c0422` (this successor). Dispositions below were checked against the diff
`a5fca69dc..HEAD`.

## Per-item disposition

| # | Finding | Disposition | Where | Tests (`debian_install_v2/tests/test_lt_polish.py` unless noted) |
|---|---------|-------------|-------|------|
| 1 | LT-F-v1001-11 apt `Debug::pkgPolicy` noise | Debug block removed from `APT_CUSTOM`; comment explains why | `templates.py` | `test_apt_custom_template_has_no_debug_key_but_keeps_the_settings`, `test_generated_custom_conf_has_no_debug_key` |
| 2 | `#clear` lines must be first in the unattended-upgrades file | The two `#clear` lines are now lines 1-2 | `templates.py` | `test_lt_upg_fix1.py::test_the_clear_lines_come_first` (asserts `splitlines()[:2]`) |
| 3 | LT-F-r1002-03 provider files 0600 before anything can fail | `restrict_provider_files()` first in `main()` of `bootstrap-remote.py`; launcher prelude chmods before the download; `Installer.install()` calls `_secure_bootstrap_files()` first | `bootstrap-remote.py`, `customscript.py`, `installer.py` | `test_restrict_provider_files_*`, `test_early_bootstrap_failure_leaves_no_loose_files`, `test_config_parse_failure_still_leaves_0600_files`, `test_launcher_chmods_before_a_download_failure`, `test_install_with_the_lt05_config_failing_early_leaves_0600_files` |
| 4 | LT-F-v1001-08 `log_dir` never created | `log_dir` kept as a LEGACY key (saved configs / `LOG_DIR` env must still load); v2 never wrote there; config comment, wizard label and READMEs say so; `status()` additionally lists `stage2_output` and `custom_script.output*` | `config.py`, `wizard.py`, `installer.py`, READMEs | `test_status_lists_the_real_install_logs` (docs/wizard-label wording is not test-pinned) |
| 5 | `io_benchmark_cleanup` step | recorded `success` (`planned` in dry run) after teardown and after leftover removal; `warned` on cleanup failure (both paths) | `installer.py` | `test_io_benchmark_cleanup_success_is_recorded`, `..._failure_is_recorded_as_warned`, `..._step_absent_when_the_benchmark_is_off`, `test_leftover_partition_removal_records_its_own_cleanup_success` (new) |
| 6a | recipe hazard: retain key with no key/placeholder | `build_customscript_bundle` raises `ValueError`; netcup `install-host.py` `_customscript_key_recipe_problem()` refuses such a built script in `_expand_payload_placeholders`. No opt-out flag. | `customscript.py`, `scripts/netcup/install-host.py` | `test_retain_key_*`, `test_no_retention_and_no_key_still_builds`, `test_cli_build_fails_clearly_for_retain_without_placeholder`, `test_controller_key_retention.py` (updated), netcup `test_expand_refuses_retain_key_script_without_key_or_marker` |
| 6b | recipe hazard: default branch `main` silently runs old code | `describe_fetch_source()` prints branch + remote commit (stderr) and WARNS on branch mismatch or HEAD != remote; default unchanged; conftest autouse stub of `customscript._git_output` (no network in tests) | `customscript.py`, `bootstrap.py`, `conftest.py` | `test_fetch_source_*` (4 incl. new same-branch case), `test_cli_build_prints_the_fetch_source_and_warning_to_stderr` |
| 7 | docs: what `never_reboot` does not hold back | docs only (new README section + netcup README paragraph) | READMEs | n/a |
| 8 | LT-F-v1001-13 journald persistence earlier | `_configure_journald` moved in `_stage1` to right after `_configure_controller_ssh_key` | `installer.py` | `test_journald_precedes_every_other_stage1_step` |

Also: a `debug =bool(` spacing slip in `bootstrap-remote.py` `main()` was fixed (in `f7f5c0422`).

## Controller rulings

- 6a (no opt-out flag): accepted.
- Item 4 (keep `log_dir` as a legacy key, docs fixed): accepted.
- Item 8 (journald moved earlier): accepted, pending live verification in the next run.

## Plant table

Each plant was applied with Edit, the tests run, then reverted with `git checkout -- <file>` (the one
allowed exception); `git status --short` was clean (apart from the intended test edits) after every revert.
Full suite = `pytest -q` in `scripts/debian-install-v2` (1051-1053 tests).

| Plant | Product change | Suite run | Killed by |
|-------|----------------|-----------|-----------|
| 1 | re-add `Debug { pkgPolicy "true"; };` to `APT_CUSTOM` | test_lt_polish.py | `test_apt_custom_template_has_no_debug_key_but_keeps_the_settings`, `test_generated_custom_conf_has_no_debug_key` |
| 2 | `#clear` lines moved below the comment header | test_lt_upg_fix1.py + test_lt_polish.py | `test_the_clear_lines_come_first[full]`, `[security-only]` |
| 3a | remove `restrict_provider_files()` call from `main()` | FULL | `test_early_bootstrap_failure_leaves_no_loose_files`, `test_config_parse_failure_still_leaves_0600_files` |
| 3b | remove launcher chmod prelude | FULL | `test_launcher_chmods_before_a_download_failure` |
| 3c | remove `_secure_bootstrap_files()` from `install()` | FULL | `test_install_with_the_lt05_config_failing_early_leaves_0600_files` |
| 4 | `status()` real-log list emptied | test_lt_polish.py | `test_status_lists_the_real_install_logs` |
| 5a | drop success mark after normal teardown | test_lt_polish.py | `test_io_benchmark_cleanup_success_is_recorded` |
| 5b | drop `warned` mark on cleanup failure | test_lt_polish.py | `test_io_benchmark_cleanup_failure_is_recorded_as_warned` |
| 5c | drop success mark on leftover-removal path | FULL | SURVIVED (1051 passed). Added `test_leftover_partition_removal_records_its_own_cleanup_success`; re-plant killed by it (1 failed) |
| 6a-i | disable the `ValueError` branch in `build_customscript_bundle` (`elif False and ...`) | FULL | `test_retain_loads_from_json_and_bundle`, `test_retain_key_without_key_or_placeholder_fails_the_build`, `test_cli_build_fails_clearly_for_retain_without_placeholder` |
| 6a-ii | neutralize `_customscript_key_recipe_problem` call in netcup `_expand_payload_placeholders` | netcup `tests/test_install_host.py` | `test_expand_refuses_retain_key_script_without_key_or_marker` |
| 6b-i | disable branch-mismatch warning | test_lt_polish.py | `test_fetch_source_warns_loudly_when_the_checkout_is_on_another_branch`, `test_cli_build_prints_the_fetch_source_and_warning_to_stderr` |
| 6b-ii | disable HEAD-differs-from-remote warning | test_lt_polish.py | SURVIVED (22 passed). Added `test_fetch_source_warns_when_same_branch_but_local_head_differs_from_remote`; re-plant killed by it |
| 6b-iii | drop warning-printing loop in `bootstrap._build_customscript` | test_lt_polish.py | `test_cli_build_prints_the_fetch_source_and_warning_to_stderr` |
| 8 | journald call moved back after `_configure_users` (two edits) | FULL | `test_journald_precedes_every_other_stage1_step` |

Not planted: item 7 (docs only), and the item-4 docs/wizard-label wording and netcup "malformed
JSON / non-v2 script is left alone" tolerance branches of `_customscript_key_recipe_problem`.

## Gate verdicts (worktree HEAD `f7f5c0422`, run under `flock` + nice/ionice, one at a time)

- `scripts/debian-install-v2/run-gate.py --worktree .../lt-polish r0-r1`: lane `r0-r1` verdict PASS, exit 0
  (1053 passed, 11 skipped; total coverage 96%).
- `scripts/netcup/run-gate.py --worktree .../lt-polish suite`: lane `suite` verdict PASS, exit 0 (719 passed).

Both verdicts were read in a separate step from the run.

## Process deviations

- The previous implementer issued two Edit calls in one message (the journald move: add + remove),
  violating the one-Edit-per-message rule. Disclosed here; the resulting code is the reviewed diff.
- Plant reverts were done with `git checkout -- <file>` (allowed exception), not Edit.
- Successor: plant 8 and the `debug =` fix involved no rule deviation; the plant-8 move was done as two
  separate single-Edit messages.
