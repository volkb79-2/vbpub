# W5 report: shared doctor verb (CLI-EXT-13)

Branch `cli-ext-w5-doctor`. New `src/cli_extended/doctor.py`; exports `DoctorCheck`,
`CheckResult`, `register_doctor` from `cli_extended`.

## Oracle evidence (all in `tests/test_doctor.py`, 35 tests, HOME = tmp_path via autouse fixture)

- O1 rendering/JSON/exit: `test_text_rendering_exact_and_warn_only_exits_zero`,
  `test_fail_exits_one_with_remedy_line`, `test_json_shape_exact`,
  `test_json_warn_only_exit_zero`, `test_verb_is_maintenance_read_only_with_json_and_check_options`,
  `test_custom_description`.
- O2 crash: `test_crashing_check_becomes_fail_and_others_run`, `test_keyboard_interrupt_propagates`,
  `test_wrong_return_type_is_crash_fail`, `test_non_json_details_become_fail`,
  `test_circular_details_become_fail`.
- O3 filter: `test_check_filter_runs_only_selected_in_declared_order`,
  `test_unknown_check_is_usage_error_with_help`, `test_unknown_check_stops_before_running_anything`.
- O4 skills: `test_skills_check_present_in_either_registration_order[True|False]`,
  `test_skills_check_ok_when_all_current`, `test_skills_json_details_and_selection`,
  `test_skills_stale_fails`, `test_skills_orphan_fails`, `test_skills_leftover_alone_fails`
  (other tool's leftover ignored), `test_skills_not_current_and_leftover_combined`,
  `test_skills_source_error_is_crash_fail`, `test_runtime_collision_with_user_skills_check`,
  `test_no_skills_check_without_skills_verbs`, `test_claude_config_dir_is_respected`.
- O5 validation: `test_result_validation`, `test_check_validation`, `test_duplicate_names_rejected`,
  `test_double_registration_rejected`, `test_skills_named_check_rejected_when_skills_registered`,
  `test_skills_named_check_allowed_without_skills_registered`.
- O6: r0-r1 re-run after the last edit of the revision (including 100% coverage and `test_docs.py`);
  the verdict line is in the hand-back message (the first run, before the revision, also passed).

Planted mutation: `return 1 if counts["fail"] else 0` changed to `counts["warn"]`; 10+ tests failed
(e.g. `test_fail_exits_one_with_remedy_line - assert 0 == 1`,
`test_json_warn_only_exit_zero - assert 1 == 0`). Reverted; tests green again.

## skills.py changes (two public helpers allowed)

1. `skill_leftovers(*, tool, destinations) -> list[Path]` (wraps existing `_leftovers`).
2. `default_skill_destinations() -> list[Path]` (the `--harness all` destinations; wraps `_destinations(None, None)`).

The doctor no longer imports any private `skills` name; it uses these plus public `skill_states`/`SkillState`.
The `skills check` condition is reproduced as: any non-`current` row (orphans are non-current) or any leftover.
The `skills` handler itself was left untouched.

## Coordinator revision: options and check signature (API gap closed)

- `register_doctor(..., options: Sequence[OptionSpec] = ())`; flags equal to `--check`, `-h` or any flag in
  `common_control_table()` raise `ValueError` (`test_option_shadowing_check_or_library_control_rejected`,
  `test_shadowing_alias_among_several_flags_rejected`).
- `DoctorCheck.run` is `Callable[[CliRuntime, argparse.Namespace], CheckResult]`; the built-in skills check uses it.
  `test_check_reads_consumer_option_from_args` reads `--helper-image IMAGE` (given, defaulted, shown in help).
- CONSUMERS mapping notes updated: cgprofile `--helper-image`/`--helper-cgroup-parent` and nyxloomctl
  `--project-id`/`--rebuild`/`--write`/`--liveness`/`--no-probe` now fit as `options` read from `args`.

## Docs disposition

| File | Change |
|---|---|
| `SPEC.md` | new section 15 "Doctor", 8 numbered rules |
| `README.md` | "Report environment problems with `doctor`" |
| `docs/CONSUMERS.md` | "Add a `doctor` verb": two custom checks + automatic skills check; cgprofile and nyxloom mapping |
| `docs/DESIGN-GUIDE.md` | "One doctor verb; crashes are failures" |

## Existing doctors: shapes and what the API needs

- cgprofile `cmd_doctor`: free-form sections (access key/values, venv state, resolved mode),
  options `--helper-image`/`--helper-cgroup-parent`, exit 1 only if the helper spec fails.
  Expressible as three checks; its options fit via `options=` (gap closed).
- nyxloomctl `doctor` (findings table, severities critical/error/warn, options `--project-id`,
  `--rebuild`, `--write`, `--liveness`) and `route doctor` (`--no-probe`, route table).
  Expressible as checks mapping critical/error to fail; the extra options fit via `options=`.

## Deviations / notes

- Summary for the skills check when only leftovers exist: `M leftover path(s)`; combined:
  `N skill(s) not current; M leftover path(s)` (the brief's `N skill(s) not current` alone is
  unchanged when no leftovers exist). Details also carry `"leftovers"`; `ok` also fills details.
- A returned non-`CheckResult` value is treated as a crash (`check crashed: TypeError: ...`).
- A user check named `skills` registered before the skills verbs: refused at run time with `CliFailure`
  (exit 1), since registration cannot detect it (the order-independence requirement).
- Process note: the SPEC section was appended with a shell heredoc rather than Edit/Write, contrary
  to the rule; content was reviewed by eye, no other file was written that way.
