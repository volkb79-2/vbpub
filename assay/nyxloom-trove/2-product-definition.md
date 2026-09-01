---
kind: product-definition
schema_version: 1
product_version: 1
features:
- id: F001
  title: The declared evidence ladder (R0-R3)
  acceptance:
  - id: F001-A1
    text: A lane declares rigor as a prefix-closed ladder from R0; any non-prefix declaration is refused at config load, before any measurement boundary is reached.
    status: proven
    evidence:
    - tests/test_config_rigor_grammar.py::test_an_invalid_declaration_is_refused_at_load_before_any_boundary
    - tests/test_config_rigor.py::test_r0_only_lane_with_no_judge_table_loads_clean
  - id: F001-A2
    text: Each declared level requires exactly the judge fields it needs, and a lane missing any of them is refused with the field named.
    status: proven
    evidence:
    - tests/test_config_rigor.py::test_r1_lane_with_all_six_loads
    - tests/test_config_rigor.py::test_r1_lane_missing_any_of_the_six_is_rejected
    - tests/test_config_rigor.py::test_r2_additionally_requires_mutation
  - id: F001-A3
    text: What actually ran is recorded on every outcome where a lane resolved, and omitted rather than emptied for a lane that never loaded.
    status: proven
    evidence:
    - tests/test_verdict_transparency.py::test_what_ran_is_recorded_on_every_outcome_where_a_lane_resolved
    - tests/test_verdict_transparency.py::test_a_verdict_for_a_lane_that_never_loaded_omits_them_rather_than_emptying
  status: shipped
  milestone: M1
- id: F002
  title: The verdict artifact and its independent verifier
  acceptance:
  - id: F002-A1
    text: The rolled-up outcome must agree with the rollup of its own claims, and a document whose outcome disagrees is rejected.
    status: proven
    evidence:
    - tests/test_verdict_conformance.py::test_verify_rejects_an_outcome_that_disagrees_with_the_rollup_of_its_claims
    - tests/test_verdict_claims.py::test_a_rollup_over_no_claims_is_not_a_pass
  - id: F002-A2
    text: Each claim's status is re-derived from its own payload and recorded policy, so a status that disagrees with its own evidence is rejected rather than trusted.
    status: proven
    evidence:
    - tests/test_verdict_conformance.py::test_verify_rejects_an_r2_claim_that_misreports_its_own_failed_prerequisite
    - tests/test_verify_layer_independence.py::test_a_post_baseline_only_terminal_beside_a_failing_baseline_is_rejected
    - tests/test_verify_layer_independence.py::test_the_same_terminal_beside_a_passing_baseline_stays_accepted
  - id: F002-A3
    text: An artifact from another schema version is rejected on its version alone, as the single actionable sentence, and never upgraded in place.
    status: proven
    evidence:
    - tests/test_verdict_schema_is_packaged.py::test_the_installed_schema_still_rejects_a_malformed_verdict
    - tests/test_verdict_schema_is_packaged.py::test_the_shipped_schema_enumerates_exactly_the_vocabulary_module_declares
  - id: F002-A4
    text: Every cross-object rule has an independently reachable witness at the raw-verifier layer, not only through model reconstruction.
    status: proven
    evidence:
    - tests/test_verify_layer_independence.py::test_raw_layer_clause_operator_prefix_must_equal_the_resolved_language
    - tests/test_verify_layer_independence.py::test_raw_layer_clause_declared_attribution_requires_its_artifact
    - tests/test_verify_layer_independence.py::test_raw_layer_clause_a_helper_entry_needs_a_correspondingly_judged_claim
  status: shipped
  milestone: M1
- id: F003
  title: Changed-line coverage judgment (R1)
  acceptance:
  - id: F003-A1
    text: The judgment is over the four-way union of changed, executable, executed and excluded lines, so all four members combine in one evaluation.
    status: proven
    evidence:
    - tests/test_evaluate_four_way_union.py::test_all_four_members_combine_in_one_evaluation
    - tests/test_evaluate_four_way_union.py::test_a_changed_missing_line_fails_the_union
    - tests/test_evaluate_four_way_union.py::test_every_changed_line_executed_passes
  - id: F003-A2
    text: A pre-existing uncovered line outside the diff never affects the verdict.
    status: proven
    evidence:
    - tests/test_evaluate_four_way_union.py::test_a_pre_existing_uncovered_line_outside_the_diff_is_invisible
  - id: F003-A3
    text: A changed line that is excluded from coverage fails the lane even at 100 percent reported coverage, unless the lane declares allow_excluded.
    status: proven
    evidence:
    - tests/test_evaluate_four_way_union.py::test_a_changed_excluded_line_fails_even_at_100_percent
    - tests/test_evaluate_four_way_union.py::test_allow_excluded_opts_a_lane_back_into_passing
  - id: F003-A4
    text: A format that cannot express exclusions at all is recorded as unavailable, distinctly from a format that truthfully reported none.
    status: proven
    evidence:
    - tests/test_coverage_exclusion_capability.py::test_a_format_that_cannot_express_exclusions_is_unavailable
    - tests/test_coverage_exclusion_capability.py::test_a_format_that_reports_exclusions_is_reported_even_when_it_found_none
    - tests/test_coverage_exclusion_capability.py::test_capability_is_not_inferred_from_the_format_name
  - id: F003-A5
    text: A whole-target lane (judge.mode = "whole_target") measures the declared files' real content from the coverage artifact, passing or failing on that content, with no base and no diff resolved.
    status: proven
    evidence:
    - tests/test_runner_evaluate_r1_wave1.py::test_whole_target_mode_passes_without_ever_resolving_a_base_or_diff
    - tests/test_runner_evaluate_r1_wave1.py::test_whole_target_mode_fails_uncovered_lines_from_the_real_target_content
    - tests/test_evaluate_whole_target.py::test_a_fully_covered_target_passes
  - id: F003-A6
    text: A declared whole-target that is absent from the coverage artifact, or present with zero executable lines, refuses NO_MEASUREMENT/TARGET_NOT_MEASURED rather than passing vacuously on 0/0.
    status: proven
    evidence:
    - tests/test_evaluate_whole_target.py::test_a_target_absent_from_the_artifact_refuses_target_not_measured
    - tests/test_evaluate_whole_target.py::test_a_target_with_zero_executable_lines_refuses_target_not_measured
    - tests/test_runner_evaluate_r1_wave1.py::test_a_target_absent_from_the_artifact_renders_target_not_measured
  - id: F003-A7
    text: A whole-target must name a regular source file; a directory or a symlink is refused at load, never silently expanded into every file beneath it.
    status: proven
    evidence:
    - tests/test_evaluate_whole_target.py::test_resolve_whole_target_refuses_a_directory
    - tests/test_evaluate_whole_target.py::test_resolve_whole_target_refuses_a_symlink
    - tests/test_evaluate_whole_target.py::test_resolve_whole_target_refuses_an_excluded_directory
  - id: F003-A8
    text: Branch coverage is judged whenever the coverage artifact reports it, and pct becomes the combined line-plus-branch percentage; a floor missed purely on branches fails as UNCOVERED_BRANCHES, never UNCOVERED_LINES.
    status: proven
    evidence:
    - tests/test_evaluate_branch_coverage.py::test_full_line_and_branch_coverage_passes_at_the_combined_percentage
    - tests/test_evaluate_branch_coverage.py::test_a_branch_deficit_alone_fails_as_uncovered_branches_not_uncovered_lines
    - tests/test_evaluate_branch_coverage.py::test_a_missing_line_takes_precedence_over_a_branch_deficit
  - id: F003-A9
    text: judge.require_branch = true refuses NO_MEASUREMENT/BRANCH_UNAVAILABLE before any evaluation when the artifact's branch capability is unavailable; require_branch = false is unaffected by the same artifact.
    status: proven
    evidence:
    - tests/test_runner_evaluate_r1_wave1.py::test_require_branch_renders_branch_unavailable_before_mode_dispatch
    - tests/test_runner_evaluate_r1_wave1.py::test_require_branch_false_is_unaffected_by_an_unavailable_artifact
  status: shipped
  milestone: M1
- id: F004
  title: Changed-line mutation with per-mutant isolation (R2)
  acceptance:
  - id: F004-A1
    text: Mutation is baseline-gated - a baseline that did not pass stops the lane before any mutant is generated, and the R2 claim reuses the baseline's own outcome and reason code verbatim.
    status: proven
    evidence:
    - tests/test_mutation_baseline_gate.py::test_a_failing_baseline_stops_before_any_mutant_and_renders_fail
    - tests/test_mutation_baseline_gate.py::test_no_scratch_directory_is_created_for_a_red_baseline
    - tests/test_mutation_baseline_gate.py::test_the_r2_claim_reuses_the_baselines_own_outcome_and_reason_code_verbatim
  - id: F004-A2
    text: A mutant run lands in exactly one of five identity buckets - killed, survived, crashed, budget-exceeded, equivalent - and the total accounts for every one; a kill, a crash and a hang are never collapsed.
    status: proven
    evidence:
    - tests/test_mutation_judge.py::test_run_mutation_reaches_all_four_buckets_and_total_accounts_for_every_one
    - tests/test_mutation_judge.py::test_crashed_outranks_survived_and_budget_exceeded_when_all_three_are_present
    - tests/test_mutation_judge.py::test_every_bucket_empty_with_a_positive_total_is_pass
  - id: F004-A3
    text: Zero discovered mutants is INCONCLUSIVE/NO_MUTANTS, never a PASS.
    status: proven
    evidence:
    - tests/test_mutation_judge.py::test_zero_total_is_inconclusive_no_mutants
  - id: F004-A4
    text: Discovery is bounded by a declared max_mutants and refuses before submission rather than silently sampling; the executor's fan-out comes from a declared jobs count, never a machine-derived heuristic.
    status: proven
    evidence:
    - tests/test_mutation_executor_bound.py::test_the_executor_factory_receives_exactly_jobs_not_mutant_count
    - tests/test_mutation_executor_bound.py::test_jobs_1_and_jobs_3_produce_identical_ordered_records
    - tests/test_runner_p23_cleanup_and_budget.py::test_every_identity_after_an_expiry_is_budget_stopped_not_only_the_next
  status: shipped
  milestone: M2
- id: F005
  title: The canary - proving the gate can still fail (R3)
  acceptance:
  - id: F005-A1
    text: A declared canary transform is applied, run, and judged, so a lane proves its own suite still catches a planted defect.
    status: proven
    evidence:
    - tests/test_runner_run_lane_r3.py::test_r3_alone_proves_the_declared_canary_through_run_lane
  - id: F005-A2
    text: A canary the suite never actually caught is CANARY_SURVIVED, and a canary that failed for some OTHER reason is also CANARY_SURVIVED - proving nothing about the defect it was built to catch is not a pass.
    status: proven
    evidence:
    - tests/test_runner_run_lane_r3.py::test_r3_reports_canary_survived_when_the_transform_is_never_actually_caught
    - tests/test_runner_run_lane_r3.py::test_r3_reports_a_real_wrong_cause_as_survived_with_the_unmocked_adapter
    - tests/test_verdict_conformance.py::test_verify_rejects_an_r3_pass_whose_canary_failed_for_the_wrong_cause
  - id: F005-A3
    text: The uncovered-line canary is proved for its own reason when R1 is declared, rather than for any adverse outcome.
    status: proven
    evidence:
    - tests/test_runner_run_lane_r3.py::test_r3_proves_the_uncovered_line_canary_for_its_own_reason_when_r1_is_declared
  status: shipped
  milestone: M2
- id: F006
  title: Committed-object snapshot isolation
  acceptance:
  - id: F006-A1
    text: A higher-rigor unit runs against a snapshot materialized from the commit's own reachable object closure, with every literal reproduced exactly.
    status: proven
    evidence:
    - tests/test_isolation.py::test_repository_root_project_materializes_every_literal_exactly
    - tests/test_isolation.py::test_replacement_identity_is_stable_across_independent_preparations
  - id: F006-A2
    text: Snapshot work is bounded by declared ceilings, and an incoherent bound is refused rather than clamped.
    status: proven
    evidence:
    - tests/test_isolation.py::test_limits_reject_incoherent_and_non_integer_bounds
    - tests/test_isolation.py::test_an_injected_budget_expiry_is_a_lane_timeout
  - id: F006-A3
    text: A path escaping the snapshot - an absolute, empty or traversing symlink target - is refused, and a replacement naming a symlink is a stale mutation site rather than a plausible-looking Git error.
    status: proven
    evidence:
    - tests/test_isolation.py::test_absolute_empty_or_escaping_symlink_targets_are_refused
    - tests/test_isolation.py::test_a_replacement_naming_a_symlink_is_a_stale_mutation_site
    - tests/test_isolation.py::test_read_regular_file_refuses_a_symlink_and_an_absent_path
  - id: F006-A4
    text: A unit that leaves Git-visible state behind stops the lane; a cleanup failure after a real result replaces only the highest higher-rigor claim and never masks a genuine programmer error.
    status: proven
    evidence:
    - tests/test_runner_p23_cleanup_and_budget.py::test_a_cleanup_assay_error_replaces_only_the_highest_higher_rigor_claim
    - tests/test_runner_p23_cleanup_and_budget.py::test_a_runtime_error_before_any_result_propagates_and_is_never_laundered
    - tests/test_runner_p23_cleanup_and_budget.py::test_a_cleanup_failure_after_a_FAILING_baseline_is_still_verify_clean
  - id: F006-A5
    text: Every R1/R2/R3 lane must declare isolation.snapshot_selection from a closed two-value vocabulary, and an R0-only lane must not declare it; there is no default.
    status: proven
    evidence:
    - tests/test_config_snapshot_selection.py::test_snapshot_selection_closed_matrix
    - tests/test_config_snapshot_selection.py::test_snapshot_selections_public_constant_is_exactly_the_closed_pair
  - id: F006-A6
    text: Under repository-minus-unsafe-symlinks, every declared, commit-validated unsafe symlink leaf is absent from the materialised worktree while every other tracked path, including safe symlinks and sibling projects, remains present.
    status: proven
    evidence:
    - tests/test_isolation_unsafe_symlink_omissions.py::test_complete_symlink_matrix_and_index_invariants
    - tests/test_runner_snapshot_selection.py::test_live_command_observes_exact_policy_in_every_unit
  - id: F006-A7
    text: An unsafe symlink not named in unsafe_symlink_omissions still refuses the lane, and the refusal names the exact declarable repo-top-relative spelling rather than requiring the operator to derive it.
    status: proven
    evidence:
    - tests/test_isolation_unsafe_symlink_omissions.py::test_an_undeclared_unsafe_link_still_refuses_and_names_the_declarable_spelling
    - tests/test_isolation_unsafe_symlink_omissions.py::test_declaring_a_safe_symlink_is_a_configuration_error_not_an_exclusion
  - id: F006-A8
    text: The coverage artifact's missing parent directory chain is created only inside the lane's own ephemeral snapshot; an R0/in-place path with a missing parent still refuses and creates nothing.
    status: proven
    evidence:
    - tests/test_runner_run_lane.py::test_run_lane_creates_the_coverage_artifacts_missing_parent_only_inside_the_snapshot
    - tests/test_safeio.py::test_reserve_output_default_refuses_a_missing_parent_and_creates_nothing
    - tests/test_safeio.py::test_reserve_output_create_missing_parents_refuses_a_symlinked_component_never_follows_it
  - id: F006-A9
    text: The verdict's snapshot_policy records the effective selection and, under omission mode, the exact declared omissions, and is absent for an R0-only lane.
    status: proven
    evidence:
    - tests/test_verify_snapshot_policy.py::test_higher_rigor_with_a_well_formed_repository_policy_is_clean
    - tests/test_verify_snapshot_policy.py::test_r0_only_with_no_snapshot_policy_is_clean
    - tests/test_verdict_wave1_new_fields.py::test_snapshot_policy_selection_must_be_a_known_value
  status: shipped
  milestone: M2
- id: F007
  title: The Python adapter, fully qualified against a real external project
  acceptance:
  - id: F007-A1
    text: The adapter registers under its own declared name and is independently addressable through the registry.
    status: proven
    evidence:
    - tests/test_adapters_python_registration.py::test_the_python_adapter_registers_under_its_own_declared_name
    - tests/test_adapters_go_registration.py::test_go_and_python_adapters_coexist_in_one_registry_each_independently_addressable
  - id: F007-A2
    text: Statement spans are resolved from the real syntax tree, so an interior line of a multi-line statement is attributed rather than left unclassified.
    status: proven
    evidence:
    - tests/test_adapters_python_statement_spans.py::test_a_multiline_dict_literal_spans_every_physical_line
    - tests/test_adapters_python_statement_spans.py::test_a_compound_statement_header_stops_before_its_first_body_line
    - tests/test_adapters_python_statement_spans.py::test_a_bare_module_docstring_is_never_a_span_anchor
  - id: F007-A3
    text: Mutation sites are generated from real syntax under a declared operator policy, and a test path is never a mutation or canary target.
    status: proven
    evidence:
    - tests/test_adapters_python_generate_mutants.py::test_every_eligible_site_produces_the_hand_derived_exact_mutated_text
    - tests/test_adapters_python_generate_mutants.py::test_every_generated_mutant_parses_with_ast_parse
    - tests/test_adapters_python_generate_mutants.py::test_unparseable_source_raises_the_typed_discovery_failure
    - tests/test_runner_run_lane_r3.py::test_r3_refuses_a_test_path_target_as_a_payload_free_claim
  status: shipped
  milestone: M1
- id: F008
  title: The Go adapter
  acceptance:
  - id: F008-A1
    text: The Go adapter registers under its own declared name, declares the expected protocol surface, and evaluates coverage identically whether built directly or through the registry.
    status: proven
    evidence:
    - tests/test_adapters_go_registration.py::test_the_go_adapter_registers_under_its_own_declared_name
    - tests/test_adapters_go_registration.py::test_the_go_adapter_declares_the_expected_protocol_surface
    - tests/test_adapters_go_registration.py::test_a_registry_built_go_adapter_evaluates_coverage_identically_to_a_direct_one
  - id: F008-A2
    text: An unknown Go region is proved through a fail-closed has_executable_code rather than through span attribution, which Go's block format does not need.
    status: proven
    evidence:
    - tests/test_adapters_go_has_executable_code.py::test_a_declarations_only_file_has_no_executable_code
    - tests/test_adapters_go_has_executable_code.py::test_a_line_comment_containing_func_and_braces_is_not_misclassified
    - tests/test_adapters_go_registration.py::test_the_go_adapters_statement_spans_returns_none_unconditionally
  - id: F008-A3
    text: A Go R1 line claim is statement-granular - a cover block's positional extent is not read as statement truth. BLOCKED on A-217's source-side statement-position oracle; A-239 records the accepted seam, which is designed but not carved.
    status: absent
  - id: F008-A4
    text: The committed Go coverage fixtures are real toolchain output. Currently they are hand-authored and wrong in BOTH coordinates (A-234); real bytes are captured at carve-assets/P27/witness/coverage-hello-fixture-REAL.out, and regeneration is deliberately sequenced behind A-217's oracle so a real profile is not read as statement truth.
    status: absent
  - id: F008-A5
    text: Qualified end to end on srdm's own tree - a real statement-granular Go R1 verdict produced by the shipped CLI inside tester-unified-go at a real srdm commit range, and every line on which srdm's covergate disagrees at the same commits classified as extent-expansion (assay correct, A-217/B056) or file-absence (covergate's NoCode/Unmeasured split), with the independent hand manifest as the neutral third party where one exists. Reworded by A-401 - the previous "union fidelity" wording was unattainable by construction.
    status: absent
  status: building
  milestone: M6
- id: F009
  title: Attested evidence, bound to a commit and checked for staleness
  acceptance:
  - id: F009-A1
    text: An attestation naming HEAD or an ancestor with unchanged reviewed paths is current; a changed reviewed path renders STALE_ATTESTATION and a change outside every reviewed path does not.
    status: proven
    evidence:
    - tests/test_attestation_evaluate.py::test_an_attestation_naming_head_itself_is_current
    - tests/test_attestation_evaluate.py::test_an_ancestor_attestation_with_unchanged_reviewed_paths_is_current
    - tests/test_attestation_evaluate.py::test_a_changed_reviewed_path_renders_stale
    - tests/test_attestation_evaluate.py::test_a_change_outside_every_reviewed_path_remains_current
  - id: F009-A2
    text: A missing attestation is NO_MEASUREMENT/MISSING_ATTESTATION and a descendant attested commit is refused - assay never verifies attested content, only its shape, its commit binding and its freshness.
    status: proven
    evidence:
    - tests/test_attestation_evaluate.py::test_a_missing_attestation_renders_no_measurement_missing_attestation
    - tests/test_attestation_evaluate.py::test_a_descendant_attested_commit_renders_unreadable_artifact
  - id: F009-A3
    text: External evidence participates in the rollup, so a green R0-R2 table cannot read as "this change is fine" while the only method that could have caught the defect never ran.
    status: proven
    evidence:
    - tests/test_verdict_claims.py::test_external_evidence_participates_in_rollup
  - id: F009-A4
    text: Tier 2 (adjudicated) evidence - assay invoking a declared third-party tool and applying a declared threshold to its structured output. The schema carries the declared_evidence/evidence sibling shape for it; no adjudicator registry exists until a real integration does (A-078).
    status: absent
  status: shipped
  milestone: M3
- id: F010
  title: Zero runtime dependencies, proved through a real install
  acceptance:
  - id: F010-A1
    text: The package imports nothing outside the stdlib, checked mechanically over every scanned file rather than by convention - and the check itself is proved to catch every import shape in a deliberately tainted copy.
    status: proven
    evidence:
    - tests/test_dependency_purity.py::test_the_package_imports_nothing_outside_the_stdlib
    - tests/test_dependency_purity.py::test_the_check_catches_every_import_shape_in_a_tainted_copy
    - tests/test_dependency_purity.py::test_every_scanned_file_parses_and_declares_at_least_one_import
  - id: F010-A2
    text: The packaged schema is inside the built wheel and resolves from inside an installed venv through importlib.resources, not from a path derived from __file__.
    status: proven
    evidence:
    - tests/test_verdict_schema_is_packaged.py::test_the_schema_is_inside_the_built_wheel
    - tests/test_verdict_schema_is_packaged.py::test_the_installed_package_resolves_the_schema_from_inside_the_venv
    - tests/test_verdict_schema_is_packaged.py::test_the_resource_path_the_code_uses_matches_where_the_file_is
  - id: F010-A3
    text: assay gates itself through its own real installed wheel, and the self-hosting oracle is differential - a producer mutation that verify alone wrongly accepts is caught when the same scenario runs through the unmutated wheel.
    status: proven
    evidence:
    - tests/test_self_hosting.py::test_a_universal_pass_producer_mutation_is_wrongly_accepted_by_verify_alone
    - tests/test_self_hosting.py::test_the_same_scenario_through_the_real_unmutated_wheel_correctly_reports_fail
    - tests/test_self_lane.py::test_lane_name_matches_the_gate_id_p11_requires
  status: shipped
  milestone: M1
- id: F011
  title: One bounded budget per lane, started once
  acceptance:
  - id: F011-A1
    text: The declared budget is what is passed as the command timeout, and its expiry is BUDGET_EXCEEDED/LANE_TIMEOUT rather than an error or a failure.
    status: proven
    evidence:
    - tests/test_runner_execute.py::test_the_declared_budget_seconds_is_what_is_passed_as_the_timeout
    - tests/test_runner_execute.py::test_budget_expiry_is_budget_exceeded_lane_timeout_via_injection
  - id: F011-A2
    text: One deadline governs the whole lane - evidence loading and attestation work spend from the same budget as the command, and an expiry mid-evaluation is never remapped to some other reason.
    status: proven
    evidence:
    - tests/test_attestation_evaluate.py::test_the_lane_deadline_expiring_mid_evaluation_is_never_remapped
    - tests/test_attestation_load_declared.py::test_the_lane_deadline_expiring_before_any_record_is_read_is_never_remapped
    - tests/test_cli_run.py::test_an_attestation_timeout_outranks_an_adapter_that_would_refuse
  - id: F011-A3
    text: When the budget expires mid-wave, every later mutant identity is budget-stopped, not only the next one.
    status: proven
    evidence:
    - tests/test_runner_p23_cleanup_and_budget.py::test_every_identity_after_an_expiry_is_budget_stopped_not_only_the_next
  status: shipped
  milestone: M1
- id: F012
  title: A hash-bound, reproducible release contract
  acceptance:
  - id: F012-A1
    text: The build closure is exactly five hash-bound wheels declared in build-system.requires and installed from a committed wheelhouse with pip --require-hashes before --no-build-isolation.
    status: proven
    evidence:
    - tests/test_distribution_gate.py::test_pyproject_build_system_matches_the_locked_five_pin_closure
    - tests/test_distribution_gate.py::test_gate_script_preserves_required_markers_and_hardens_the_build
  - id: F012-A2
    text: The gate builds from a private clone at the exact reviewed OID that excludes ignored residue, so source selection is an observable fact rather than an ambient one.
    status: proven
    evidence:
    - tests/test_distribution_gate.py::test_exact_oid_clone_excludes_ignored_residue
    - tests/test_distribution_gate.py::test_production_distribution_assets_are_byte_identical_to_locked_carve_assets
  - id: F012-A3
    text: A consumable release is bound by a closed manifest whose successful verification emits one PEP 508 hash requirement, so pip rechecks the bytes it actually opens rather than trusting a prior check.
    status: proven
    evidence:
    - tests/test_distribution_release_wheel.py::test_verify_emits_exact_hash_requirement_for_the_real_positive_fixture
    - tests/test_distribution_release_wheel.py::test_manifest_command_recreates_the_locked_document_and_refuses_a_second_write
    - tests/test_distribution_release_wheel.py::test_verify_refuses_each_hostile_manifest_shape
  - id: F012-A4
    text: The release helper is standalone and stdlib-only, so a consumer can verify a release before assay is installed.
    status: proven
    evidence:
    - tests/test_distribution_release_wheel.py::test_helper_exists_and_is_the_expected_stdlib_only_module
  status: shipped
  milestone: M3
- id: F013
  title: A SQL/DDL source-mutation adapter
  acceptance:
  - A tracked DDL change yields bounded MutationSite byte spans from a real SQL parser, mutated through assay's existing immutable replacement snapshots and judged by the unchanged project-declared argv.
  - The lane is R0,R2 - no SQL R3 space exists by construction, because R3's uncovered-line canary needs R1 coverage and DDL has no coverage tool.
  - Qualified against a real PostgreSQL project rather than a synthetic fixture.
  - The five adapter methods SQL cannot answer raise rather than return a plausible value (A-242), and a helper that fails during discovery can still record its provenance (A-243).
  status: shipped
  milestone: M5
- id: F014
  title: Release ergonomics - cmru adoption and a parallel zipapp artifact
  acceptance:
  - id: F014-A1
    text: assay releases through cmru's orchestration (per-product prefix, tag, GitHub Release, latest.json, isolated release worktree) while keeping its own hash-bound build via an explicit build-step override, so A-198/A-199's closure is not silently downgraded.
    status: proven
    evidence:
    - tests/test_distribution_build_release.py::test_the_builder_is_a_standalone_stdlib_only_module
    - tests/test_distribution_build_release.py::test_locked_pins_agree_with_the_gate_scripts_independent_transcription
    - tests/test_distribution_build_release.py::test_the_tag_glob_is_the_same_one_pyproject_and_cmru_use
  - id: F014-A2
    text: A zipapp is built FROM the released wheel, so it reports the wheel's own version rather than the 0+unknown source-tree fallback, and reads its packaged schema from inside the archive.
    status: proven
    evidence:
    - tests/test_distribution_build_release.py::test_the_zipapp_reports_the_wheels_version_and_never_the_source_fallback
    - tests/test_distribution_build_release.py::test_the_zipapp_reads_its_packaged_schema_from_inside_the_archive
    - tests/test_distribution_build_release.py::test_the_zipapp_verifies_a_real_artifact_and_refuses_a_foreign_version
  - id: F014-A3
    text: The zipapp propagates a non-zero exit code, so a FAIL, ERROR or NO_MEASUREMENT verdict is not read as success by a consumer.
    status: proven
    evidence:
    - tests/test_distribution_build_release.py::test_the_zipapp_propagates_a_nonzero_exit_from_a_failing_lane
    - tests/test_distribution_build_release.py::test_the_generated_zipapp_entry_point_propagates_the_exit_code
    - tests/test_distribution_build_release.py::test_zipapps_own_generated_main_really_does_drop_the_return_value
  - id: F014-A4
    text: Two builds of one commit produce byte-identical artifacts, and no builder-specific path enters the archive.
    status: proven
    evidence:
    - tests/test_distribution_build_release.py::test_two_builds_of_one_commit_are_byte_identical
    - tests/test_distribution_build_release.py::test_the_archive_carries_no_builder_specific_paths
    - tests/test_distribution_build_release.py::test_stripping_removes_direct_url_and_prunes_record_to_what_exists
  - id: F014-A5
    text: A release manifest is emitted only for a build whose HEAD carries the project's own assay-v* tag, so an SCM development identity cannot be published as a release.
    status: proven
    evidence:
    - tests/test_distribution_build_release.py::test_an_untagged_build_emits_no_release_manifest
    - tests/test_distribution_build_release.py::test_head_release_tag_refuses_a_tag_that_is_merely_REACHABLE
    - tests/test_distribution_build_release.py::test_head_release_tag_ignores_another_products_tag_on_head
    - tests/test_distribution_build_release.py::test_the_fallback_version_is_refused_on_an_untagged_build
  - id: F014-A6
    text: The wheel, the zipapp, both .sha256 sidecars and the release manifest all appear as assets on one GitHub Release, with the manifest documented as authoritative because only it can feed pip's hash mode.
    status: proven
    evidence:
    - github-release:assay-v2.2.0 assets assay-2.2.0-py3-none-any.whl, assay-2.2.0-py3-none-any.whl.sha256, assay-2.2.0.pyz, assay-2.2.0.pyz.sha256, release-manifest.json, release-manifest.json.sha256
  status: shipped
  milestone: M4
- id: F015
  title: fail-before/pass-after as a computed method
  acceptance:
  - A lane can assert that a new test would have caught the bug it was written for - the canary machinery inverted, requiring the test to fail at the pre-fix commit and pass at HEAD.
  - The claim is Tier 1 (computed in-process, fully deterministic), reusing the existing build-a-variant-commit path rather than new substrate.
  status: planned
  milestone: M7
non_goals:
- A test runner or test framework - assay judges what a project's own declared command produced.
- A policy engine with a rule language. Tier 2 applies a declared threshold to structured output; if a lane needs a rule language, that rule belongs in the tool being adjudicated.
- An LLM-mediated reviewer. A model dependency makes the gate non-deterministic, and a non-deterministic gate is not a gate; Tier 3 exists precisely so this stays out.
- An orchestrator or CI system. assay does not choose what to run or where to run.
- Remediation. Adoption declares and verifies; it does not fix unrelated test debt it finds.
- In-place artifact upgrades. A schema version is a consumer migration (A-138/A-170).
- Whole-file or whole-project coverage as the unit of judgement.
- SQL "coverage" as a rigor level - recorded as a named exclusion so it is not re-proposed.
---

# assay — product definition

The frontmatter above is the machine-diffed target; this body is narrative for
a human reader. Every `proven` acceptance criterion cites real pytest node ids
that pass in the current suite — collected from `pytest --collect-only` and
checked against a captured pass list, not written from memory of what a test is
probably called. Criteria that are not proven say `absent` and name what blocks
them.

## How to read this against the package queue

The features are the *product* view; `handoffs/README.md` is the *delivery*
view, and the two are not in the same order. F001–F012 correspond to work
merged across P00–P26 and P33; F013 is P34; F008's absent criteria are the P27
re-carve through P32; F014 is B002/B003; F015 is A-O06 under A-244. Package
numbers are identity, not sequence (A-153/A-167/A-219) — read
`handoffs/README.md` for order.

## The one feature that is deliberately honest rather than flattering

**F008 (the Go adapter) is `building`, not `shipped`**, and three of its five
criteria are `absent` on purpose:

- The registration, protocol surface and fail-closed `has_executable_code` are
  real and tested (F008-A1, F008-A2).
- But a Go R1 *line* claim is not statement-granular today. A cover block
  carries a positional extent plus a statement *count*, never the statements'
  own positions, so the shipped parser over-approximates. A-217 ruled that
  statement positions must come from a source-side oracle; A-239 records the
  accepted seam. Designed, not carved.
- And the committed Go coverage fixtures are hand-authored and wrong in both
  coordinates (A-234). The real bytes are captured as evidence. They are
  deliberately *not* regenerated yet, because swapping a wrong profile for a
  real one while the parser still over-approximates would replace a wrong
  profile with a real profile whose block extents are then read as statement
  truth — the exact conflation the oracle exists to remove.

Recording that as `shipped` would be the hollow-proven claim the spine schema
exists to make impossible.

## What "proven" costs here

The `proven`/`absent` vocabulary is narrower than the charter's four states,
and this document uses it literally. For computed claims, `proven` means at
least one real, passing, named test drives the shipped entry point at that
criterion. `F014-A6` is deliberately different: it claims the published asset
set itself, so its evidence names the complete `assay-v2.2.0` GitHub Release
assets rather than a test that would only rehearse publication. Two habits
behind that, both bought expensively:

- **A stated pass/fail count is not evidence** (A-232). A criterion's evidence
  is node ids someone can run, not a number someone reported.
- **Review by writing real inputs to disk and running the shipped entry point**
  — not by reading the diff, and not by re-running the implementation's own
  fixtures. Every defect the reviews of this project have found came out of the
  former.
