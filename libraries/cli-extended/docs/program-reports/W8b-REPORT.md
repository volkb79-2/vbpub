# W8b report: release blockers and mutation survivors

Branch `cli-ext-w8b-blockers`. Gate: `run-gate: lane 'r0-r1' verdict PASS; exit_code 0`
(100% statement and branch, 5251 statements, 2404 branches), run after the last edit.
r2 was not run (controller's campaign is on the host).

## Per-item evidence

1. **Skills filesystem errors are domain failures.** `skills.py` `_execute` wraps only
   the mkdir / `_write_atomic` / `rmtree` actions in `except OSError` and raises
   `CliFailure("<skill> -> <dest>: <exc>")` (exit 1). The leftover removal in the
   handler is wrapped the same way (named by the leftover's directory name). The
   handler as a whole is not wrapped. Tests (`tests/test_skills.py`):
   `test_w8b_install_into_read_only_destination_is_exit_one`,
   `test_w8b_install_under_a_regular_file_is_exit_one`,
   `test_w8b_uninstall_where_removal_fails_is_exit_one`,
   `test_w8b_leftover_removal_failure_is_exit_one`,
   `test_w8b_non_filesystem_bugs_still_report_as_unexpected`. The read-only tests skip
   when `os.geteuid() == 0`. Two older tests expected a raw `OSError` to escape
   (`test_failed_write_leaves_no_temp_dir...`, `test_failed_first_install...`); they
   now assert exit 1 plus the `[ERROR] alpha -> <dest>: ...` line, with the same
   no-leftover assertions.
2. **Factory loading errors are exit 2.** The single boundary is `config.load_cli`
   (the one place both `surface *` and `audit` load the factory; `cli.py` and
   `audit.py` call it). It now catches `Exception` and raises
   `ConfigError("cannot load CLI factory '<spec>' for '<id>': <ExcType>: <msg>")`,
   which `_guarded` maps to exit 2. Deviation from the brief's "in `cli.py`": the code
   lives in `config.py` because that is the shared boundary. Tests
   (`tests/test_config.py`): `test_w8b_load_cli_turns_any_factory_exception_into_a_config_error`
   (import raises RuntimeError; factory callable raises; attribute missing),
   `test_w8b_load_cli_missing_factory_file_names_file_and_spec`; the old wrap test's
   message pin was updated. SPEC rule 5 of the project-configuration section gained the
   sentence. The `cli-extended-adoption` SKILL.md exit-code wording was unchanged.
3. **Doctor skills severity.** `doctor._skills_check`: any non-CURRENT/non-ABSENT row or
   leftover is `fail` (message unchanged); only ABSENT rows is `warn`
   `N skill(s) not installed`, same remedy; else `ok`. Tests (`tests/test_doctor.py`):
   the two existing absent-only tests and the details-order test updated to warn/exit 0;
   `test_skills_absent_and_stale_mix_fails`, `test_skills_absent_with_a_leftover_fails_not_warns`,
   `test_skills_modified_install_fails`; the existing stale/orphan/leftover/combined tests
   still pass unchanged. SPEC §15 rule 8, README, CONSUMERS and DESIGN-GUIDE updated.
4. **Ordering.** One key `skills._row_key` (destination path, then name) is used by
   `_report` and by the install/uninstall loop. Test:
   `test_w8b_dry_run_install_and_list_share_one_order` (two destinations, three skills);
   the two existing dry-run tests had an old orphan-last order pinned and were updated.
5. **Survivors** (tests below, each planted by hand on a scratch copy):
   `test_w8b_audit_item_is_immutable`;
   `test_w8b_surface_check_is_manual_for_each_valid_partial_config` and
   `test_w8b_surface_check_is_manual_when_any_single_path_is_missing`
   (calls `_surface_check` directly, since the config loader refuses manifest without
   spec, so single-missing manifest/spec states are only reachable via `CliConfig`);
   `test_w8b_surface_complete_tolerates_an_invalid_interaction` (tests/test_audit.py);
   `test_w8b_template_refuses_broken_interaction_references` (tests/test_workflow.py;
   also asserts `surface report` on the same catalog still exits 0).
6. **Dependency floor.** `questionary>=2.1.1` with a comment: the version `prompts.py`
   was verified against, using `text/password/confirm/select/checkbox` with `input=`,
   `output=` and dict checkbox choices with `checked`. I did not install other versions
   to test the floor.
7. **Docs nits.** Done as listed (table below). README relative links untouched.

## Mutation table (planted by hand on scratch copies under the scratchpad; focused test files run with PYTHONPATH pointing at the copy)

| # | Mutation | Result | Killed by |
|---|----------|--------|-----------|
| 1 | `audit.py` `frozen=True` -> `False` | killed | `test_w8b_audit_item_is_immutable` |
| 2 | `audit.py` `_surface_check` `or` -> `and` | killed | `test_surface_configured_pass_and_fail_variants` (existing); with `-k w8b` also all five new `surface_check` cases |
| 3 | `audit.py` `_tolerate_invalid_interactions=True` -> `False` | killed | `test_w8b_surface_complete_tolerates_an_invalid_interaction` |
| 4 | `cli.py` `template_text` `tolerate=False` -> `True` | killed | `test_w8b_template_refuses_broken_interaction_references` |
| 5 | skills `_execute` `except OSError` -> other type | killed | `test_failed_write_leaves_no_temp_dir...` (first failure, `-x`) |
| 6 | skills leftover `except OSError` -> other type | killed | `test_w8b_leftover_removal_failure_is_exit_one` |
| 7 | `load_cli` `except Exception` -> `(AttributeError, TypeError, ValueError)` | killed | `test_w8b_load_cli_turns_any_factory_exception_into_a_config_error[RuntimeError]` |
| 8 | doctor `not broken and not leftovers` -> `or` | killed | `test_skills_absent_and_stale_mix_fails` |
| 9 | doctor `is not ABSENT` -> `is ABSENT` | killed | `test_skills_check_present_in_either_registration_order[True]` |
| 10 | `_row_key` -> `(name, destination)` | killed | `test_harness_agents_and_all_defaults` |

No equivalent mutants were found, so no code was restructured. Mutants 5-10 are my own
additions, not from the campaign; I did not run assay's operators, only these hand plants.

## Docs disposition

| File | Change |
|------|--------|
| `SPEC.md` | factory failure = exit 2 with message; skills filesystem failure = exit 1; skills order; doctor `warn`/`fail` rule |
| `README.md` | doctor skills check warn/fail; new "Project, audit and findings API" subsection |
| `docs/CONSUMERS.md` | `surface pack` in the command list; doctor skills severity |
| `docs/DESIGN-GUIDE.md` | why absent skills are a warn |
| `docs/ADOPTION-CHECKLIST.md` | AC-22 "is configured" |
| `skills/cli-extended-review/SKILL.md` | "the CLI's configured `review` catalog" (no `findings.toml` was hardcoded) |
| `skills/cli-extended-adoption/SKILL.md` | `pytest-plugin` bullet "is configured" (consistency with AC-22) |
| `pyproject.toml` | `questionary>=2.1.1` with reason |

## Deviations

- The factory boundary is `config.load_cli`, not `cli.py` (see item 2); the audit
  command shares it.
- No Edit/Write rule deviations: all repository files changed with Edit/Write; `cp` was used only to make scratch copies of `src/` outside the repository.
