# W3a REPORT: library contract version

Branch `cli-ext-w3a-contract`. Package scope respected (no `scripts/netcup/*`, `parser.py` touched only in `add_common_options`).

## Gate

`run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (100% statement and branch, TOTAL 3492 stmts / 1676 branches, docs tests green). A first run failed on one uncovered `continue` in `contract.py`; restructured (no pragma) and re-ran green.

## Oracle evidence (tests in `tests/test_contract.py` unless noted)

- O1: `test_library_help_and_metavar_never_reach_the_manifest_or_markdown` (patches `parser._common_option_specs` and wraps `parser.add_common_options` to rewrite help and metavar of every library action, value control `--log-level` and flag control `--quiet` asserted patched; manifest JSON and Markdown byte-identical, includes interaction groups naming library controls).
- O2: `test_consumer_declared_grammar_change_changes_that_routes_signatures` (see Deviation 1: metavar, not help).
- O3: `test_consumer_option_spelled_like_a_library_control_stays_consumer_grammar`; also `tests/test_surface.py::test_surface_action_scope_defaults_and_custom_action_are_described`.
- O4: `test_contract_bump_yields_exactly_one_finding` (v1 sync, monkeypatch `contract.CONTRACT_VERSION = 2`, `check` returns exactly the one specified finding and `passed is False`); `test_manifest_without_a_usable_contract_record_keeps_stale_handling[5 params]`; `test_committed_contract_version_reads_only_integer_versions`.
- O5: `test_reviewed_control_candidates_keep_their_pre_change_ids` (literal IDs; I captured them from the pre-change code via `git archive HEAD` and verified identical for this fixture, for the repo's `_complex_cli` nested fixture (63 IDs), and for Netcup's `monitor-task.py` registry (candidate IDs identical, all interaction groups resolve, `check` against its committed files yields only: manifest stale, spec stale, 13 `signature changed`)).
- O6: `test_invocations_with_library_controls_check_on_enabled_routes`, `test_control_missing_from_a_route_is_an_unrecognized_option`, `test_library_control_values_are_checked_from_the_contract_table`, `test_single_command_cli_names_its_controls_on_the_root_route`.
- O7: `test_common_control_table_keys_are_derived_from_every_include_flag` (discovers `include_*` params via `inspect.signature`); plus `test_common_control_table_entries_describe_arity_and_placement`, `test_common_control_table_returns_independent_copies`, `test_library_actions_are_marked_and_consumer_actions_are_not`.
- O8: gate verdict above.
- Docs: `tests/test_docs.py::test_spec_contract_subsection_names_every_library_control` (every flag of `common_control_table()` named in the SPEC "Contract v1 controls" subsection).
- Other new tests: `test_manifest_names_controls_per_route_and_records_the_contract`, `test_manifest_holds_no_library_control_description`, `test_other_library_controls_get_no_candidates`, `test_interaction_over_library_controls_resolves_by_id`, `test_common_parser_path_falls_back_to_the_route_path`, `test_markdown_*` (3), `test_sync_writes_the_contract_fields`.

Hand-planted mutation: flipped `in` to `not in` for `contract.CONSUMER_REVIEWED_COMMON_FLAGS` in candidate generation (`surface.py`). Killed by 5 tests: `test_reviewed_control_candidates_keep_their_pre_change_ids`, `test_other_library_controls_get_no_candidates`, `test_consumer_declared_grammar_change_changes_that_routes_signatures`, `test_markdown_renders_one_common_controls_line_per_route`, `test_invocations_with_library_controls_check_on_enabled_routes`. Reverted. (The controller's r2 mutation lane was not run, per rules.)

## Behavior notes (as implemented)

- Check, no `library_contract` / unusable record / older `schema_version`: unchanged stale handling. The committed manifest differs from the regenerated text, so `check` reports `generated CLI manifest is stale` (plus the spec-block finding and any per-case findings as before). Only an integer `library_contract.version` differing from `CONTRACT_VERSION` takes the one-finding path, which returns before the file-staleness comparison. It still runs after `_prepare`, so catalog/path errors raise first.
- `route["common_controls"]` is the union over the route's parser chain (nested routes inherit their verb's list, as their `actions` already inherited). To keep candidate IDs byte-stable (`.../parser:<verb>/--json` for nested routes), the owner parser path is recomputed from the route table (`_common_parser_path`: shallowest route at depth >= 1 listing the flag). Known edge: a consumer callback that itself calls `add_common_options` on a deeper nested parser would have its controls attributed to the shallowest listing ancestor.
- Library controls are rebuilt as in-memory option records (`_route_common_actions`, never serialized) for the invocation checker, for interaction option-ID resolution (all enabled controls, so existing catalogs that name e.g. `.../--json` or `.../--quiet` keep resolving), and for the reviewed candidates. In signature context these records contribute only `id`, `kind`, `canonical`, `scope`. Interaction `external_options` payload still carries a foreign library option's flags/nargs/choices (from the contract table), which the checker needs.
- Table entries also carry `choices` (needed to keep rejecting `--log-level loud`); the manifest never contains it. `before_verb`/`after_verb` in the table are `True` as specified; the per-route placement used by the checker additionally applies `not single_command` to `before_verb`, as before.
- Markdown: header line now `Surface schema: ...; review catalog schema: ...; library contract: cli-extended v1.`; new `### Library common controls` section with one line per route: ``- `<route id>`: Common controls (cli-extended contract v1): --color, ...`` (`none` if empty). The renderer raises `SurfaceSpecError` if the surface lacks a valid `library_contract` record.
- The finding text names `cli-extended CHANGES.md contract notes`; the package has no `CHANGES.md` yet (release package W8 should create it with a contract-notes section).

## Changed expectations

- `tests/test_review.py` (7 handcrafted surfaces; `schema_version` 6 -> 7 where it was current, plus `library_contract` record added): the renderer now requires the record.
- `tests/test_review.py::test_sync_is_idempotent_preserves_outside_bytes_and_never_rewrites_catalog`: `schema_version == 6` -> `== 7` (new schema).
- `tests/test_review.py::_candidate_argv` (helper): library controls are no longer in `route["actions"]`; helper now adds them via `_route_common_actions`.
- `tests/test_a_r2_semantic_guards.py` (`root_row` surface): `schema_version` 7 and `library_contract` added (renderer requirement).
- `tests/test_surface_contract_edges.py::test_markdown_rows_preserve_optional_fields_shapes_and_review_dispositions`: `library_contract` added, schema 7.
- `tests/test_surface_contract_edges.py::test_surface_export_records_option_and_argument_edges_and_candidate_boundaries`: `--json/--progress/--yes` scope `"common"` in `actions` -> asserted in `common_controls` and absent from `actions`.
- `tests/test_surface.py::test_surface_ids_delegates_missing_registry_metadata_and_parser_routes`: "an action with scope common" -> `"--json" in common_controls`, no common-scope action in `actions`.
- `tests/test_surface.py::test_surface_action_scope_defaults_and_custom_action_are_described`: a plain argparse action spelled `--quiet`/`--json`/`--progress`/`--yes` is now scope `"custom"` and `_effective_default(..., scope="common")` is gone (ownership is by marker, not spelling).

## Docs disposition

| File | Change |
| --- | --- |
| `SPEC.md` | schema `7`; replaced the common-controls sentence (now incl. `--dry-run`); new subsection "Library contract and contract version" (7 numbered rules) and "Contract v1 controls" list |
| `docs/CONSUMERS.md` | updated common-controls paragraph; new "What a library upgrade does to your surface" with the one-finding example and re-sync steps |
| `docs/DESIGN-GUIDE.md` | new "Version the library's own controls, don't sign them" (CX-D5, rejected re-sign-everything alternative) |
| `README.md` | one paragraph with links |
| `tests/test_docs.py` | SPEC list checked against `common_control_table()` |

## Deviations

1. **O2 as written is false for this codebase.** Consumer `OptionSpec` help text is not part of any candidate signature today (SPEC: "Descriptions and help wording are excluded so copy edits do not force semantic reapproval"; `description` is not in the signed shape keys). I did not change that (it would re-sign every consumer). The negative control uses the consumer-declared `metavar` (which is signed) and also asserts that help changes reach the manifest but not signatures. The controller may want to confirm that reading of O2.
2. **Process slip:** two small repo edits were made by shell script instead of Edit/Write: a one-shot Python rewrite of `surface.py` for the import/schema/`_scope`/`_effective_default` changes, and a `cat >>` append of the SPEC subsection (content reviewed and unchanged since). Everything else used Edit/Write. No other effect.
3. Interaction groups may still reference any enabled library control's option ID (all controls, not just the reviewed ones); this goes slightly beyond the brief but prevents breaking existing catalogs.
4. `contract.py` also exports `canonical_flag` and `is_library_action` (surface imports them; `surface._canonical_flag` remains as an alias).
