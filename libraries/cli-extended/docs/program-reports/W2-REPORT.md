# W2 REPORT: declarative constraints (CLI-EXT-02) and SelectorList (CLI-EXT-12)

Branch `cli-ext-w2-constraints` (on top of W1 + W3a). Surface schema stays 7.

## Gate

`run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (first run; 100% statement and branch: 3869 stmts / 1882 branches; `constraints.py` and `values.py` 100%).

## Oracle evidence

Tests are in `tests/test_w2_constraints.py` (C) and `tests/test_w2_selector.py` (S).

- O1: `C::test_violation_exits_2_with_exact_message_and_verb_help` (9 params: exit 2, empty stdout, first stderr line `[ERROR] <exact message>`, verb help with `CONSTRAINTS` section, handler not run; options before and after the verb; declaration order), `C::test_satisfied_invocation_runs_the_handler` (11 params), `C::test_empty_default_list_is_not_presence`, `C::test_conflicts_message_lists_present_options_in_declared_order`, `C::test_global_option_constraints_before_and_after_the_verb`, `C::test_global_default_lookup_uses_the_root_parser_and_suppressed_defaults`, `C::test_single_command_cli_enforces_and_shows_constraints_in_help`, `C::test_nested_verb_constraints_apply_before_the_nested_route`, `C::test_help_command_and_markdown_render_the_constraint_lines`.
- O2: `C::test_library_controls_in_constraints_json_and_dry_run`; also the `--json`/`--dry-run` params of O1; `C::test_build_refuses_unknown_flags_naming_verb_and_flag` (a library control the verb did not enable is refused).
- O3: `C::test_constraint_dataclass_validation` (27 params), `C::test_valid_constraints_expose_flags_and_records`, `C::test_verbspec_constraints_must_be_a_tuple_of_constraints`, `C::test_build_refuses_unknown_flags_naming_verb_and_flag`, `C::test_build_refuses_non_empty_defaults` (5 params, flag as option and as any_of member), `C::test_build_accepts_none_false_and_empty_sequence_defaults`, `C::test_build_requires_choices_on_the_target_and_values_within_them`, `C::test_positional_argument_cannot_be_referenced_as_a_flag`.
- O4: `C::test_route_records_constraints_with_canonical_library_flags`, `C::test_three_candidate_kinds_with_literal_ids_members_and_shape` (literal ids `case:route:entrypoint:tool/sync/constraint-requires/1`, `.../constraint-conflict/2`, `.../constraint-choice/3`), `C::test_nested_leaf_route_inherits_constraints_but_prefix_has_no_candidate`, `C::test_changing_a_reason_changes_only_that_routes_signatures`, `C::test_unconstrained_route_signatures_do_not_carry_a_constraints_key`, `C::test_library_syntax_change_does_not_move_constraint_signatures` (the CX-D5 test the controller asked for: patches the control table's nargs, choices and flags; every signature and the manifest JSON are byte-identical, the record names `--json` canonically), `C::test_unresolvable_constraint_flag_in_a_surface_is_a_surface_error`, `C::test_checker_accepts_constraint_violating_invocations_and_never_evaluates`, `C::test_markdown_lists_constraints_per_route_only_when_declared`, `C::test_surface_json_is_deterministic_with_constraints`.
- O5: `S::test_all_alone_returns_choices_or_the_sentinel`, `S::test_names_come_back_in_the_order_given_and_are_stripped`, `S::test_rejections_use_exact_messages`, `S::test_constructor_exposes_normalized_fields`, `S::test_constructor_validation` (16 params), `S::test_a_choice_may_equal_the_default_token_when_another_token_is_used`, `S::test_option_and_positional_values_reach_the_handler_converted`, `S::test_bad_input_is_an_argparse_usage_error_with_exit_2` (5 params, exit 2), `S::test_surface_records_the_label_and_fields_and_is_not_opaque`, `S::test_surface_signature_changes_with_the_selector_settings`, `S::test_subclass_and_colliding_converters_stay_opaque`, `S::test_checker_models_the_selector_exactly`, `S::test_checker_treats_a_malformed_selector_record_as_opaque`.
- O6: verdict above.

Hand-planted mutation: `constraints.py` `first_violation`, `len(active) > 1` changed to `> 2`. Killed by 7 tests (3 params of `test_violation_exits_2_with_exact_message_and_verb_help`, `test_conflicts_message_lists_present_options_in_declared_order`, `test_library_controls_in_constraints_json_and_dry_run`, `test_nested_verb_constraints_apply_before_the_nested_route`, `test_build_accepts_none_false_and_empty_sequence_defaults`). Reverted; the new test files pass again.

## Comparison with cmru `parse_target_names` (also in `docs/CONSUMERS.md`)

1. Returns a tuple, not a list; an absent argument stays `None` (argparse default).
2. `all` returns `SelectorList.ALL` or the full `choices` tuple, not `["all"]`.
3. Raises `argparse.ArgumentTypeError` (usage error, exit 2 with help), not `TargetSelectionError`; messages differ.
4. Check order is empty, then `all`-mixed, then duplicate; cmru does empty, duplicate, then `all`. `all,all` is "duplicate" in cmru, "cannot be combined" here.
5. Unknown names are rejected at parse time only when `choices` is given; cmru checks against the loaded registry in `select_target_names`.
6. No reordering to declared project order, no context-project/estate-scope defaults.
7. Extra constructor refusals (surrounding whitespace in a choice or token, token containing the separator).

## Docs disposition

| File | Change |
| --- | --- |
| `SPEC.md` | new §5 subsections "Declared option constraints" (7 numbered rules incl. presence rule and refusal format) and "Selector lists" (5 rules); §13 subsection "Constraints and selector lists in the surface" (5 rules) |
| `README.md` | section "Declared option constraints and selector lists" with one example each (both run against the shipped code) |
| `docs/CONSUMERS.md` | "Replacing handler-side option checks and name-list parsing": pasteable CMRU `tool-deps` before/after, `SelectorList` replacing `parse_target_names`, difference list |
| `docs/DESIGN-GUIDE.md` | "Declare option constraints structurally" (presence = non-default with registration-time refusal; constraints stay structural; the checker never evaluates them; rejected alternatives) and "Selector lists as a value type" |
| `BACKLOG.md` | CLI-EXT-02 status "Implemented (W2)" with pointers; CLI-EXT-12 row unchanged |

## Deviations and decisions beyond the brief

1. **Delegating verbs cannot declare constraints** (`ValueError` in `VerbSpec`): they have no parser of their own to enforce them on; the child CLI carries its own.
2. **Constraint on a nested route:** a verb's constraints are also recorded on its nested routes (they share the verb parser's actions), and candidates are generated for every invocable route, never for a `route-prefix`. A flag that resolves to no route option raises `SurfaceError`.
3. **Signature context key is omitted when the list is empty**, so unconstrained routes keep their W3a signatures. Every manifest route does gain `"constraints": []`.
4. **Constraint candidates skip the "members must appear" check** in the checker (the case may break the rule on purpose); present options still get the general value checks. This was needed so a Requires-violating invocation passes.
5. Markdown for a verb that uses `configure` shows the constraints inside its parser-help text block (the `CONSTRAINTS` epilog) rather than under a separate `#### Constraints` heading. Verbs without `configure` get the heading. The route Markdown region uses `### Constraints`.
6. `RequiresChoice.values` must be strings (matches the brief's `tuple[str, ...]`), so int-valued choices cannot be targets.
7. SelectorList constructor adds refusals for whitespace-padded names/token and a token containing the separator (a name that could never match).
8. Surface: positional `parser_kwargs["type"]` now goes through `_normalize_action_type` so a `SelectorList` positional is not marked opaque (other keys unchanged).
9. **Process slip:** one test-file edit (ordering of the candidate-id assertion and `required=True` on a nested subparser in `tests/test_w2_constraints.py`) was made with a one-shot Python string replace instead of Edit. Content was then re-run and is unchanged since. Everything else used Edit/Write.

## Review round 1

Gate after fixes: `run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (100% statement and branch, 3880 statements / 1890 branches).

1. **Shared dest.** `build()` raises `ValueError` ("whose destination 'x' is shared with another option") when a referenced flag's action shares its `dest` with a different action of that parser. Test `test_build_refuses_options_that_share_a_destination` uses the library `--color`/`--no-color` pair and a consumer `store_true`/`store_false` pair; aliases of one action (`--debug`/`--verbose`) are accepted.
2. **Hollow candidates.** The checker now requires the trigger: members[0] for `constraint-requires`/`constraint-choice`, at least one member for `constraint-conflict`; finding `invocation for <id> does not exercise its constraint`. `test_checker_requires_the_trigger_so_cases_are_not_hollow` (plain `sync`, non-trigger-only cases) and the updated `test_checker_accepts_constraint_violating_invocations_and_never_evaluates`. Other member checks stay relaxed.
3. **SelectorList case.** `test_matching_is_case_sensitive`: `ALL`/`All` are unknown with choices and plain names without; `A` rejected for choices `a,b`; custom token case.
4. **RequiresChoice.** `test_requires_choice_values_match_choices_by_equality_not_substring` (`fa` vs `fast`/`slow`). For the member-resolution order I did not keep "deepest wins": the scan is now forward, so the verb parser's own option (the one `build()` validated) wins over a same-named nested-parser option; `test_member_resolution_prefers_the_verb_parsers_option_over_a_nested_one` fails if the order is reversed. The redundant `kind == "option"` guard was removed.
5. **Undetectable presence.** `build()` refuses `nargs="*"` and `nargs="?"` whose `const` equals the default (`test_build_refuses_options_whose_presence_is_undetectable`, plus `test_optional_value_option_with_a_distinct_const_is_detectable`). SPEC rule 2 documents it. The earlier `nargs="*"` accept case in the build test became a plain `default=()` option.
6. **List-valued target.** `append`/`extend` action or `nargs` not in (None, "?") raises ("is list-valued"): `test_build_refuses_a_list_valued_requires_choice_target`; `nargs="?"` stays allowed.
7. **CONSUMERS** notes the one-time re-sync caused by the per-route `"constraints": []` key, the trigger requirement, and the new build refusals.
8. **Equivalent mutants.** I was not given what Q3, Q9 and R5 are, so I removed every guard I could identify as redundant under the four operators, and cannot claim those three IDs by name: `_is_present` lost its `default is False` branch (equal to `bool(value)`); the single-command epilog conditional became an unconditional `parser.epilog = ...` in the single-command branch (a multi-verb root's epilog was unobservable); the selector converter lookup is `if label == SELECTOR_LIST_LABEL ... else builtin` (the old `converter is None and` was redundant); `_constraint_records` lost its `name in common_controls` filter (the only alias, `--verbose`, belongs to an always-present control; `test_library_alias_is_recorded_canonically_and_enforced_by_spelling` pins the canonical record). Hand probes, all killed and reverted: `_is_present` `is None` to `is not None`; selector label `==` to `!=`; epilog set to `None`. The controller should re-run the real probes.
