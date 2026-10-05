# W6 report: consumer test helpers (CLI-EXT-14)

## Oracle evidence (tests/test_w6_testing.py)
- O1 env: `test_invoke_script_builds_the_hermetic_environment`,
  `..._without_inherited_pythonpath_and_default_scrub`,
  `..._env_overrides_apply_last_and_none_deletes`, `..._runs_in_cwd`,
  `test_home_is_required_and_must_not_be_empty`,
  `test_invocation_shape_python_override_and_failsafe_timeout`.
- O2: `test_make_invoker_script_drives_assert_cli_contract`,
  `..._module_drives_...`, `..._rejects_a_cli_whose_version_goes_to_stderr`,
  `test_make_invoker_defaults_to_script_mode`.
- O3 plugin (pytester, in-process): `test_strict_mode_fails_...`,
  `test_partial_mode_passes_the_same_focused_run`,
  `test_unknown_marker_case_fails_in_both_modes`,
  `test_partial_mode_still_enforces_errors_about_collected_tests`,
  `test_marker_is_registered_for_strict_markers`,
  `test_no_review_configured_is_a_no_op`, ini/config/catalog error tests,
  `test_plugin_imports_without_pytest_installed`,
  `test_plugin_is_not_registered_as_an_entry_point`.
- Hand mutation: `NO_COLOR` "1" -> "0" in `_child_environment`; killed by
  `test_invoke_script_builds_the_hermetic_environment` (`assert '0' == '1'`).
  Reverted.
- `invoke_script` removes `FORCE_COLOR`/`CLICOLOR_FORCE` and sets `NO_COLOR=1`
  (covers Python 3.14 argparse colour per W3b).

## Docs disposition
| file | change |
|---|---|
| SPEC.md | section 9 helper rules; section 13 config item 11 (plugin) |
| README.md | "Consumer test helpers" section |
| docs/CONSUMERS.md | `### Test helpers: invoke_script`, `### Linking review cases with the pytest plugin` (headings fixed by controller for W7) |
| docs/DESIGN-GUIDE.md | "Keep consumer tests hermetic and the plugin opt-in" |

## Code
`testing.py` (helpers), `pytest_plugin.py` (new), `review.py`
(`assert_cli_case_tests(..., partial=False)`), `__init__.py` exports,
`tests/conftest.py` (enables `pytester`).

## Gate
`run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (run after the last edit).

## Deviations
- pytester has no `runpytest_inline` in this version; tests use
  `runpytest_inprocess` (also keeps the plugin under coverage).
- `invoke_script`/`invoke_module` take an extra `pythonpath` argument per brief.
