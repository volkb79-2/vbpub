# W3b report — project config, `cli-extended` CLI, review workflow (CLI-EXT-10, CLI-EXT-11)

Gate: `run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (100.00% statement and branch coverage, `tests/test_docs.py` green).

## Oracles

| Oracle | Node IDs |
|---|---|
| O1 | `tests/test_config.py::test_o1_*` (standalone/pyproject load, absolute paths, nested discovery, cwd default, both-files ambiguity, none-found directory list, unknown keys, schema_version, manifest-without-spec, duplicate id, `select()`, explicit pyproject without table; parametrized `test_o1_invalid_configurations_are_refused[*]`) |
| O2 | `test_o2_load_cli_checks_the_executable_name_against_the_id`, `test_o2_load_cli_wraps_factory_shape_errors`, `test_o2_file_factory_resolves_against_root_not_cwd`; the moved loader tests `test_factory_loader_*` |
| O3 | `tests/test_findings.py` (schema), `tests/test_workflow.py::test_o3_*` (open major fails, minor/note do not, stale route, cli_id mismatch, deterministic findings section, "None.", omitted without a file, spec stale when findings change) |
| O4 | `test_o4_surface_check_end_to_end_then_stale_after_a_help_edit`, `test_o4_bad_or_missing_config_is_a_usage_level_refusal`, `test_o4_load_errors_exit_two`, `test_o4_unconfigured_paths_are_named`, `test_o4_cli_selection_with_several_clis`, `test_o4_max_candidates_*`, `test_o4_findings_flow_through_the_script`, `test_o4_template_*` |
| O5 | `test_o5_pack_contains_rubric_help_for_every_route_and_each_pending_case`, `test_o5_output_flag_writes_the_same_bytes_and_nothing_to_stdout`, `test_o5_pack_shows_catalog_rows_stale_cases_and_open_findings`, `test_o5_pack_with_no_cases_to_review_says_none`, `test_o5_pack_of_a_single_command_cli_*`, `test_fences_grow_*`, `test_case_rows_render_as_toml_that_round_trips` |
| O6 | `test_o6_report_*` (exact text, all four section kinds, "None.") |
| O7 | `test_o7_deprecated_entrypoint_warns_first_and_still_works` (+ the existing surface_cli tests, adapted to `load_factory`) |
| O8 | `test_o8_review_skill_validates_and_installs_into_a_tmp_destination` (tmp `--dest` only; HOME and CLAUDE_CONFIG_DIR point at tmp) |
| O9 | `test_o9_contract_holds_for_the_real_executable` (subprocess, HOME=tmp, PYTHONPATH=src, `assert_cli_contract`), `test_o9_an_uninstalled_distribution_fails_cleanly` (exit 2, `[ERROR] cli-extended: ...`), `test_o9_main_reports_a_missing_distribution_in_process`, `test_o9_in_process_help_version_and_registry_policy`, `test_o9_module_entrypoint_runs_main` |
| O10 | gate lane r0-r1 PASS (above) |

## Hand mutation

In `review.py::_findings_results` changed `item.severity in BLOCKING_SEVERITIES` to `not in`. Four tests failed (`test_o3_open_blocker_and_major_fail_check_and_minor_note_do_not`, `test_o3_stale_route_findings_fail_for_any_status`, `test_o3_spec_goes_stale_when_findings_change`, `test_o4_findings_flow_through_the_script`). Restored.

## Docs disposition

| File | Change |
|---|---|
| `SPEC.md` | new subsection "Project configuration, findings and the `cli-extended` command" (8 numbered rules: config schema, discovery, findings schema, check semantics, commands/exit codes, report, pack, deprecation) |
| `README.md` | the six-step workflow, console script, deprecation note; old four-flag example replaced |
| `docs/CONSUMERS.md` | config examples (`[tool.cli-extended]` and `cli-extended.toml`), `cli-extended surface ...` commands, "The review loop", findings example; Netcup pilot command |
| `docs/DESIGN-GUIDE.md` | "Review the surface with the agent harness and keep findings separate" |
| `tests/test_docs.py` | now loads doc TOML blocks with `clis`/`tool.cli-extended` via `load_project_config` and blocks with `findings` via `load_review_findings` |

## Deviations and notes

- `sync`/`check`/`template` are not marked `mutating` (generated artifacts only, idempotent); no `--dry-run` was added since the brief did not specify one.
- Non-failing findings notes print as `[NOTE] ...` on stderr (the brief left the print form open).
- `load_cli` wraps `AttributeError/TypeError/ValueError` from the factory loader in `ConfigError` so a bad factory is exit 2; `ImportError`/`OSError` propagate to the handler guard (also exit 2).
- `surface_cli._load_factory` was removed (now `config.load_factory`); its tests moved to `tests/test_config.py` and `tests/test_review.py`/`tests/test_surface_contract_edges.py` import the new name.
- `surface_cli` shares `cli.template_text` with the new CLI.
- One mechanical file edit (removing `_load_factory` from `surface_cli.py`) was done with a short Python script instead of Edit, contrary to the rules; the result is identical to a manual edit.
- review.py edits are additive: `SurfaceReport.notes`, `findings_file` param on `_prepare`/`render_cli_surface_markdown`, `findings_path` on sync/check, helpers `_load_findings_file` and `_findings_results`, and the findings section at the end of the Markdown region.

## W8 must add (not edited here, `pyproject.toml` is out of scope)

- `[project.scripts] cli-extended = "cli_extended.cli:main"`.
- Package data: `cli_extended/skills/**` and `cli_extended/review_rubric.md` (the gate runs from the source tree, so tests do not prove the wheel contains them).
- `CHANGES.md` mention of the deprecation of `python -m cli_extended.surface_cli`.

## Review round 1

Verdict was REJECT; all items fixed.

| # | Finding | Fix | Test |
|---|---|---|---|
| 1 | pack help depended on `COLUMNS`/tty | `parser.fixed_help_width(columns)` context manager (a `ContextVar` read by `help_columns()`, used by the formatter and the help catalog); no env or global mutation. `surface pack` pins 100 columns (`cli.PACK_HELP_COLUMNS`) | `test_pack_is_identical_for_any_terminal_width` (COLUMNS=40 vs 200 identical; unpinned help differs, so the test is meaningful) |
| 2 | argparse (3.14) coloured `usage:`/headings under `FORCE_COLOR`, ignoring `--no-color`; a library-wide bug | `ExtendedArgumentParser` passes `color=False` when `ArgumentParser.__init__` accepts it (detected with `inspect.signature`); the `--no-color` argv hack in `_route_help` is removed | `test_pack_help_has_no_colour_even_when_argparse_would_force_it`, `test_help_with_no_color_is_plain_under_force_color_and_color_still_works` (library `--color` colour still present), `test_the_parser_never_lets_argparse_colour_on_its_own`; removing the fix fails 3 of them |
| 3 | `fullmatch` to `match` survived | rejection cases `"a b!"`, `"ab!"` | `test_o3_invalid_findings_files_are_refused[*]` |
| 4 C7 | backslash clause in `_is_file_target` | removed; SPEC states Windows is unsupported | `test_backslash_alone_does_not_make_a_factory_a_file_path` |
| 4 C8 | symlinked cwd | test only (discovery already resolves) | `test_discovery_from_a_symlinked_directory_finds_the_physical_parents_config` |
| 4 V6 | pack shape key order | fixture with unsorted keys, exact `json.dumps(sort_keys=True)` | `test_pack_shape_json_is_key_sorted` |
| 4 V18 | stale-only pack | | `test_pack_with_only_a_stale_case_lists_it_and_does_not_say_none` |
| 5 | docs decisions | non-mutating exemption in DESIGN-GUIDE and the rubric's mutating item; loud discovery failure on a malformed config in SPEC and CONSUMERS (remedy `--config`); SPEC rules 9 (colour) and the 100-column pack width | `tests/test_docs.py` |

`parser.py` touched only for the fixed width and `color=False`. Gate after the last edit: `run-gate: lane 'r0-r1' verdict PASS; exit_code 0`.

## Review round 2

Three surviving mutants killed and one equivalent mutant removed: `fixed_help_width` now validates `columns` (int, not bool, at least `MIN_HELP_COLUMNS` = 60, else `ValueError`); `test_fixed_help_width_pins_exactly_nests_and_restores_on_error` (exact 87/60, nesting 100/70/100/unpinned, restore after an exception), `test_fixed_help_width_rejects_values_below_the_floor_or_not_ints`, `test_pack_help_width_is_100_and_no_help_line_exceeds_it`. SPEC rule 9 notes the native argparse palette is disabled on Pythons that colour it. Gate: `run-gate: lane 'r0-r1' verdict PASS; exit_code 0`.
