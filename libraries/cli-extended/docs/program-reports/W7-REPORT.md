# W7 report: adoption checklist and `cli-extended audit` (CLI-EXT-15)

Branch `cli-ext-w7-audit`. W6 is not in this base.

## Oracle evidence

All in `tests/test_audit.py` unless noted. Full suite (serial, nice/ionice) passed before the gate.

- **O1 each check, exact status per case.** One group per check:
  `test_version_source_cases` (12 parametrised), `test_version_source_failure_evidence_is_relative_and_ordered`,
  `test_synopsis_redundant_is_warn_and_overrides_are_manual`, `test_synopsis_override_alone_is_manual`,
  `test_shadowed_controls_fail_names_verb_flag_and_replacement`, `test_shadowed_controls_see_global_and_delegated_options`,
  `test_mutation_safety_lists_only_verbs_with_neither_protection`, `test_mutation_safety_passes_when_protected`,
  `test_configure_callbacks_and_hidden_options`, `test_clean_configure_and_hidden_pass`,
  `test_exception_policy_report_passes_and_raise_warns`, `test_surface_configured_pass_and_fail_variants`,
  `test_surface_check_and_complete_on_a_synced_project`, `test_surface_check_reports_stale_files_as_evidence`,
  `test_surface_check_passes_the_findings_path`, `test_surface_check_and_complete_fail_on_a_broken_catalog`,
  `test_surface_complete_lists_incomplete_entries_and_routes`, `test_surface_complete_reports_a_limit_error_as_failure`,
  `test_skills_packaged_*` (pass, invalid skill, `.claude/skills` tree, not registered, library-group detection x2),
  `test_doctor_pass_and_manual`, `test_pytest_plugin_cases` (8) and `test_pytest_plugin_manual_without_a_catalog`,
  `test_dependency_declared_*`, `test_no_path_hacks_*`, `test_baseline_statuses_for_a_minimal_project`.
- **O2 checklist and code agree.** `test_o2_checklist_audit_checks_match_code_exactly`: ids unique, `AC-\d\d`,
  ascending; the set of `audit:<name>` equals `CHECKLIST_IDS` and `_CHECKS`; each row's id equals the code's
  `checklist_id`; row order equals report order. `test_o2_every_area_table_exists_and_rows_link_to_docs`.
  `tests/test_docs.py` now also link-checks `docs/ADOPTION-CHECKLIST.md`.
- **O3 verb.** `test_o3_text_rendering_is_exact`, `test_o3_json_rendering_is_exact`, `test_o3_exit_zero_without_failures`,
  `test_o3_real_audit_end_to_end`, `test_o3_selects_cli_and_config_and_rejects_bad_input`,
  `test_o3_factory_import_errors_exit_two`, `test_o3_audit_is_read_only_and_listed_in_help`.
- **O4 skill.** `test_o4_adoption_skill_validates_and_is_listed`; `tests/test_workflow.py` skills tests updated for two
  packaged skills and the `audit` verb.
- **O5.** See the gate verdict below.

Hand mutation planted and killed: in `audit._mutation_safety`, `... and not verb.dry_run` changed to
`... or not verb.dry_run`; `test_mutation_safety_lists_only_verbs_with_neither_protection` failed with
`'5 mutating verb(s)...' == '1 mutating verb(s)...'`. Reverted.

## Docs disposition

| File | Change |
| --- | --- |
| `docs/ADOPTION-CHECKLIST.md` | new: 25 rows (`AC-01`..`AC-25`) in 10 area tables; 15 `audit:` rows |
| `README.md` | "Audit your adoption" section |
| `docs/CONSUMERS.md` | "Adopting cli-extended end to end" (11 ordered steps with snippets) |
| `docs/DESIGN-GUIDE.md` | "Audit mechanically, judge with a skill" |
| `SPEC.md` | rule 11 under "Project configuration, findings and the `cli-extended` command" (verb, item schema, output, exit codes) |

## Deviations and decisions

- **No `parser.py` change.** The derived synopsis is computed with `dataclasses.replace(verb, synopsis=None).display_synopsis`.
- **Skills detection without a registry attribute.** `_cli_extended_skills` lives on `CliRegistry`, not the built
  `RegisteredCli`. The audit recognises the library group by a `skills` verb whose delegate has `install`, `uninstall`,
  `check` and `list`, then runs `skills list --json --dest <tmp dir>` through the app. That validates every packaged skill
  with the library's own loader; a failure is one `fail` item whose evidence is the stderr lines naming the skill
  (not one item per skill).
- **W6 links.** AC-22 (pytest plugin) and AC-23 (`invoke_script`) link to `../README.md#generated-documentation-and-contract-tests`,
  which exists. The controller retargets them at merge to `### Linking review cases with the pytest plugin` and
  `### Test helpers: invoke_script` in CONSUMERS. The `pytest-plugin` check matches the literal string
  `cli_extended.pytest_plugin` in `*.py`, `*.toml`, `*.ini`, `*.cfg` (the extra suffixes are deliberate:
  `pytest.ini`/`setup.cfg` addopts).
- **Checklist order.** Report order is checklist (area) order, not the order of the brief's list.
- **Synopsis precedence.** A redundant override makes the item `warn` even when other overrides are `manual`;
  all are listed as evidence.
- **Delegated CLIs are walked.** Verbs and options of delegate groups are audited with a `group verb` label.
- `tests/test_workflow.py` edited only where the new `audit` verb and second packaged skill changed existing exact assertions.

## Gate verdict

`run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (run after the last source and test edit; 100% statement and branch
coverage of `audit.py` seen in a local `--cov` run).
