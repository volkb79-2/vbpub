
**`tests/test_python_qualification.py`** — heavy 13/32 tests, 92.42 s of 93.68 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_materialize_scenario_leaves_the_real_checkout_byte_for_byte_untouched` (:291) | 1.88 | e | none | — | git export+commit of pinned Topos tree (966 entries) from REPO_ROOT history |
| `test_materialize_scenario_baseline_is_identical_across_different_scenarios` (:315) | 1.30 | e | none | — | git export+commit of pinned Topos tree (966 entries) from REPO_ROOT history |
| `test_materialize_scenario_writes_a_v1_lane_for_the_locked_release_and_v2_for_everyone_else` (:342) | 1.22 | e | none | — | git export+commit of pinned Topos tree (966 entries) from REPO_ROOT history |
| `test_a_topos_pass_alone_cannot_witness_agreement` (:527) | 0.57 | e | none | — | git export+commit of pinned Topos tree (966 entries) from REPO_ROOT history |
| `test_run_scenario_reproduces_the_locked_missing_line_terminal` (:563) | 13.91 | d (+e) | PATH wheel | installed_assay[module] | `installed_assay` (module) -> run-venv `assay run topos-qualification` over materialized Topos repo |
| `test_run_scenario_records_the_exclusion_capability_asymmetry` (:583) | 7.24 | d (+e) | PATH wheel | installed_assay[module] | `installed_assay` (module) -> run-venv `assay run topos-qualification` over materialized Topos repo |
| `test_integrity_matrix_negatives_produce_their_frozen_terminals` (:601) | 22.01 | d (+e) | PATH wheel | installed_assay[module] | `installed_assay` (module) -> run-venv `assay run topos-qualification` over materialized Topos repo |
| `test_universal_pass_mutation_is_rejected_by_the_whole_document_comparator` (:621) | 8.08 | d (+e) | PATH wheel | installed_assay[module] | `installed_assay` (module) -> run-venv `assay run topos-qualification` over materialized Topos repo |
| `test_a_scenario_that_must_compare_with_topos_refuses_a_missing_witness` (:636) | 7.66 | d (+e) | PATH wheel | installed_assay[module] | `installed_assay` (module) -> run-venv `assay run topos-qualification` over materialized Topos repo |
| `test_a_scenario_that_measured_nothing_is_refused` (:714) | 7.19 | d (+e) | PATH wheel | installed_assay[module] | `installed_assay` (module) -> run-venv `assay run topos-qualification` over materialized Topos repo |
| `test_the_wrong_source_root_decoy_is_rejected_because_of_the_root` (:752) | 7.52 | d (+e) | PATH wheel | installed_assay[module] | `installed_assay` (module) -> run-venv `assay run topos-qualification` over materialized Topos repo |
| `test_install_locked_release_produces_a_pure_hash_bound_venv` (:771) | 3.33 | b | locked 1.2.5 | — | venv + pip --require-hashes of committed 1.2.5 wheel |
| `test_release_smoke_scenario_matches_the_current_full_pass_shape` (:783) | 10.49 | b (+e) | locked 1.2.5 | — | installs 1.2.5 wheel then runs it on a materialized scenario |

**`tests/test_cli_run.py`** — heavy 10/52 tests, 74.10 s of 77.05 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_run_liveness_classifies_a_thread_join_hang_as_hung` (:463) | 30.83 | a (+c) | in-proc | — | liveness hung window: _HUNG_CPU_WINDOW_S=30.0 (liveness.py:523) + LIVENESS_IDLE_FLOOR_S=15.0 (:641); budget_per_candidate="50s" |
| `test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung` (:586) | 35.90 | a (+c) | in-proc | — | budget_per_candidate="35s" deliberately > 30 s CPU window; spins until budget |
| `test_run_liveness_does_not_turn_a_configure_time_raise_into_a_false_survivor` (:677) | 1.89 | c | in-proc | — | in-process `assay run` R2/R3 on toy pytest project |
| `test_unknown_rejudge_id_is_a_verified_whole_lane_refusal_including_r3` (:880) | 0.61 | e (+c) | in-proc | — | in-process CLI R2 with /bin/sh grep judge; cost = snapshot/mutant-commit materialization x2 |
| `test_stale_source_rejudge_id_is_bad_lane_config_and_verifiable` (:937) | 0.58 | e (+c) | in-proc | — | in-process CLI R2 with /bin/sh grep judge; cost = snapshot/mutant-commit materialization x2 |
| `test_rejudge_id_excluded_by_shard_is_a_verified_bad_lane_config` (:976) | 0.56 | e (+c) | in-proc | — | in-process CLI R2 with /bin/sh grep judge; cost = snapshot/mutant-commit materialization x2 |
| `test_stale_rejudge_id_refuses_when_source_edit_removes_all_candidates` (:1017) | 0.54 | e (+c) | in-proc | — | in-process CLI R2 with /bin/sh grep judge; cost = snapshot/mutant-commit materialization x2 |
| `test_corrupt_rejudge_store_keeps_unreadable_artifact_classification` (:1054) | 0.56 | e (+c) | in-proc | — | in-process CLI R2 with /bin/sh grep judge; cost = snapshot/mutant-commit materialization x2 |
| `test_valid_rejudge_id_reexecutes_only_its_selected_record` (:1087) | 0.65 | e (+c) | in-proc | — | in-process CLI R2 with /bin/sh grep judge; cost = snapshot/mutant-commit materialization x2 |
| `test_run_evaluates_a_real_r3_pass_end_to_end` (:1720) | 1.98 | c | in-proc | — | in-process `assay run` R2/R3 on toy pytest project |

**`tests/test_standalone.py`** — heavy 13/20 tests, 34.81 s of 36.92 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_a_real_python_fixture_passes_through_the_installed_wheel` (:216) | 0.80 | c | wheel(snap) | standalone[session], validator[session] | `standalone` (session) venv `assay run` subprocess; wheel built from snapshot src |
| `test_a_real_r1_lane_passes_through_the_installed_wheel` (:248) | 1.07 | c | wheel(snap) | standalone[session], validator[session] | `standalone` (session) venv `assay run` subprocess; wheel built from snapshot src |
| `test_a_real_r2_lane_kills_one_mutant_and_lets_another_survive_through_the_wheel` (:832) | 0.58 | c | wheel(snap) | standalone[session], validator[session] | `standalone` (session) venv `assay run` subprocess; wheel built from snapshot src |
| `test_a_real_r2_lane_diffs_the_resolved_merge_base_not_the_declared_ref` (:908) | 0.53 | c | wheel(snap) | standalone[session] | `standalone` (session) venv `assay run` subprocess; wheel built from snapshot src |
| `test_a_real_r2_mutant_that_outlives_the_lane_budget_is_its_own_bucket` (:1064) | 5.27 | a (+c) | wheel(snap) | standalone[session], validator[session] | lane budget="5s" vs `sleep 300` mutant |
| `test_a_real_r3_lane_proves_the_canary_and_passes_through_the_wheel` (:1275) | 2.29 | c | wheel(snap) | standalone[session], validator[session] | `standalone` (session) venv `assay run` subprocess; wheel built from snapshot src |
| `test_a_real_r3_lane_with_a_broken_control_is_inconclusive_through_the_wheel` (:1340) | 0.92 | c | wheel(snap) | standalone[session], validator[session] | `standalone` (session) venv `assay run` subprocess; wheel built from snapshot src |
| `test_a_real_r3_lane_whose_bad_case_unexpectedly_passes_survives_through_the_wheel` (:1423) | 0.63 | c | wheel(snap) | standalone[session], validator[session] | `standalone` (session) venv `assay run` subprocess; wheel built from snapshot src |
| `test_a_real_r3_lane_proves_the_uncovered_line_canary_through_the_wheel` (:1657) | 3.04 | c | wheel(snap) | standalone[session], validator[session] | `standalone` (session) venv `assay run` subprocess; wheel built from snapshot src |
| `test_a_real_r3_lane_whose_bad_case_fails_for_the_wrong_cause_survives` (:1756) | 2.90 | c | wheel(snap) | standalone[session], validator[session] | `standalone` (session) venv `assay run` subprocess; wheel built from snapshot src |
| `test_removing_package_data_ships_a_wheel_whose_schema_cannot_load` (:1978) | 6.01 | b | wheel(snap) | — | builds + installs its own modified wheel (venv, pip wheel, pip install) |
| `test_removing_the_console_script_ships_a_wheel_with_no_invocable_binary` (:2011) | 5.45 | b | wheel(snap) | — | builds + installs its own modified wheel (venv, pip wheel, pip install) |
| `test_declaring_a_runtime_dependency_breaks_the_offline_scratch_install` (:2043) | 5.32 | b | wheel(snap) | — | builds + installs its own modified wheel (venv, pip wheel, pip install) |

**`tests/test_distribution_gate.py`** — heavy 3/26 tests, 17.20 s of 20.49 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_closure_build_produces_a_real_non_placeholder_dev_identity` (:370) | 8.52 | b | none* | gate_functions[session] | gate bash fns: closure venvs + pip wheel of synthetic repo (copied src); asserts version only |
| `test_ambient_only_build_without_the_closure_is_refused_as_a_placeholder` (:395) | 5.26 | b | none* | gate_functions[session] | gate bash fns: closure venvs + pip wheel of synthetic repo (copied src); asserts version only |
| `test_the_shipped_source_tree_is_pyflakes_clean` (:920) | 3.42 | b | none* | gate_functions[session], lint_venv[session] | `lint_venv` (session) + pyflakes over src/assay+tests |

**`tests/test_b106_reuse_and_witness.py`** — heavy 6/53 tests, 17.11 s of 17.13 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_plan_refuses_to_preview_witness_reuse_when_pytest_ini_enables_xdist` (:550) | 0.50 | c | in-proc | — | in-process `assay plan` |
| `test_replay_requires_a_current_kill_and_falls_back_to_a_full_run` (:680) | 4.73 | c | in-proc | — | run_lane R2 with real `python -m pytest` per mutant (+resume reruns) |
| `test_witness_capture_works_with_the_existing_liveness_plugin` (:787) | 1.81 | c | in-proc | — | run_lane R2 with real `python -m pytest` per mutant (+resume reruns) |
| `test_custom_sessionfinish_hook_forces_full_suite_fallback` (:820) | 3.49 | c | in-proc | — | run_lane R2 with real `python -m pytest` per mutant (+resume reruns) |
| `test_resume_preserves_witness_prefix_execution_provenance` (:926) | 3.45 | c | in-proc | — | run_lane R2 with real `python -m pytest` per mutant (+resume reruns) |
| `test_rejudge_outcome_disables_prior_witness_replay` (:1001) | 3.12 | c | in-proc | — | run_lane R2 with real `python -m pytest` per mutant (+resume reruns) |

**`tests/test_runner_run_lane_r3.py`** — heavy 6/10 tests, 14.15 s of 15.07 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_r3_alone_proves_the_declared_canary_through_run_lane` (:59) | 2.08 | c | in-proc | — | run_lane R3 canary halves = 2+ real pytest subprocesses |
| `test_r3_proves_the_uncovered_line_canary_for_its_own_reason_when_r1_is_declared` (:193) | 2.67 | c | in-proc | — | run_lane R3 canary halves = 2+ real pytest subprocesses |
| `test_r3_canary_halves_consume_carried_bases_in_a_history_cut_snapshot` (:246) | 2.63 | c | in-proc | — | run_lane R3 canary halves = 2+ real pytest subprocesses |
| `test_r3_reports_a_real_wrong_cause_as_survived_with_the_unmocked_adapter` (:296) | 2.40 | c | in-proc | — | run_lane R3 canary halves = 2+ real pytest subprocesses |
| `test_r3_proves_a_canary_for_a_project_in_a_subdirectory_of_its_repo` (:429) | 1.98 | c | in-proc | — | run_lane R3 canary halves = 2+ real pytest subprocesses |
| `test_r1_r2_and_r3_together_each_render_their_own_independent_claim` (:506) | 2.39 | c | in-proc | — | run_lane R3 canary halves = 2+ real pytest subprocesses |

**`tests/test_canary_python_pipeline.py`** — heavy 8/8 tests, 10.32 s of 10.32 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_import_break_control_passes_and_the_real_transform_fails_command_failed` (:110) | 1.52 | c | in-proc | validator[session] | in-process canary orchestration, real pytest subprocess per half |
| `test_uncovered_line_control_passes_and_the_real_transform_fails_uncovered_lines` (:170) | 1.59 | c | in-proc | — | in-process canary orchestration, real pytest subprocess per half |
| `test_an_r0_only_lane_never_catches_the_uncovered_line_transform_and_survives` (:199) | 1.34 | c | in-proc | — | in-process canary orchestration, real pytest subprocess per half |
| `test_a_mechanism_that_fails_for_the_wrong_reason_survives` (:242) | 1.64 | c | in-proc | — | in-process canary orchestration, real pytest subprocess per half |
| `test_a_broken_control_renders_inconclusive_not_a_silent_pass` (:271) | 1.35 | c | in-proc | — | in-process canary orchestration, real pytest subprocess per half |
| `test_an_unrecognised_mechanism_is_inconclusive_after_a_real_control_run` (:306) | 0.72 | c | in-proc | — | in-process canary orchestration, real pytest subprocess per half |
| `test_a_transform_that_produces_no_change_is_inconclusive` (:341) | 0.74 | c | in-proc | — | in-process canary orchestration, real pytest subprocess per half |
| `test_the_transformed_commit_only_diffs_by_the_injected_lines` (:367) | 1.42 | c | in-proc | — | in-process canary orchestration, real pytest subprocess per half |

**`tests/test_mutation_judge_identity.py`** — heavy 13/67 tests, 8.96 s of 12.15 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_cli_writes_structured_verdict_for_non_object_resume_record[null]` (:578) | 0.89 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_cli_writes_structured_verdict_for_non_object_resume_record[true]` (:578) | 1.21 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_cli_writes_structured_verdict_for_non_object_resume_record[false]` (:578) | 0.65 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_a_strengthened_test_file_re_executes_instead_of_replaying_survived` (:817) | 0.57 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_filtered_identity_resumes_across_an_excluded_report_commit` (:868) | 0.52 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_a_touched_but_unchanged_test_file_still_resumes` (:888) | 0.59 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_the_same_tree_at_a_different_commit_still_resumes` (:909) | 0.52 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_a_relocated_test_file_re_executes` (:935) | 0.71 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_a_lane_whose_argv_names_no_path_still_notices_a_changed_suite` (:952) | 0.71 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_two_worktrees_of_one_commit_still_share_one_state_dir` (:984) | 0.61 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_two_instances_with_different_passthrough_values_still_resume` (:1015) | 0.66 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_dropping_a_passthrough_declaration_re_executes` (:1043) | 0.57 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |
| `test_a_rejected_store_says_so_in_the_progress_stream` (:1063) | 0.77 | e (+c) | in-proc | — | in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits |

**`tests/test_progress_phase_stream.py`** — heavy 2/20 tests, 7.56 s of 8.89 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_the_heartbeat_ticks_while_a_command_runs_and_stops_when_it_returns` (:334) | 0.50 | a | in-proc | — | time.sleep(0.35)+time.sleep(0.15), 0.02 s interval |
| `test_a_real_run_emits_a_real_tick_through_the_real_flag` (:456) | 7.05 | a | in-proc | — | `sleep 7` command vs --progress-heartbeat 5 (PROGRESS_HEARTBEAT_FLOOR_SECONDS=5.0, runner.py:936) |

**`tests/test_environment_preflight.py`** — heavy 3/13 tests, 5.63 s of 6.57 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_a_probe_that_exhausts_its_budget_reports_a_timeout_not_a_config_error` (:128) | 2.02 | a | in-proc | — | lane budget="2s" vs probe `sleep 45` |
| `test_the_probe_cap_is_enforced_where_execute_plan_actually_reads_it` (:246) | 3.03 | a | in-proc | — | PROBE_BUDGET_SECONDS patched 30.0->3.0 (runner.py:360) vs `sleep 20` |
| `test_an_r2_lane_run_twice_with_nothing_changed_passes_both_times` (:343) | 0.58 | e (+c) | in-proc | — | two in-process R2 runs |

**`tests/test_lane_timeout_writes_a_verdict.py`** — heavy 5/16 tests, 5.34 s of 6.88 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_a_direct_r0_lane_that_runs_out_of_time_still_writes_its_verdict` (:120) | 1.06 | a | in-proc | — | BUDGET="1s" vs SLOW_COMMAND="sleep 30" (file :50-51) |
| `test_a_higher_rigor_lane_that_runs_out_of_time_still_writes_its_verdict` (:152) | 1.06 | a | in-proc | — | BUDGET="1s" vs SLOW_COMMAND="sleep 30" (file :50-51) |
| `test_the_timed_out_verdict_is_one_assay_verify_accepts[rigor0]` (:303) | 1.09 | a | in-proc | — | BUDGET="1s" vs SLOW_COMMAND="sleep 30" (file :50-51) |
| `test_the_timed_out_verdict_is_one_assay_verify_accepts[rigor1]` (:303) | 1.08 | a | in-proc | — | BUDGET="1s" vs SLOW_COMMAND="sleep 30" (file :50-51) |
| `test_the_refusal_names_the_deadline_on_the_diagnostics_stream` (:326) | 1.07 | a | in-proc | — | BUDGET="1s" vs SLOW_COMMAND="sleep 30" (file :50-51) |

**`tests/test_distribution_release_wheel.py`** — heavy 1/24 tests, 3.48 s of 6.24 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_pip_require_hashes_rechecks_bytes_mutated_after_a_successful_verify` (:239) | 3.48 | b | none | — | venv + pip --require-hashes refusal (gate/distribution/release_wheel.py) |

**`tests/test_distribution_build_release.py`** — heavy 3/33 tests, 2.88 s of 4.24 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_the_zipapp_reports_the_wheels_version_and_never_the_source_fallback` (:449) | 0.70 | b | wheel(snap HEAD) | built[module] | zipapp from `built` (module) fixture: 2 full release builds in SETUP (not in call time) |
| `test_the_zipapp_verifies_a_real_artifact_and_refuses_a_foreign_version` (:483) | 1.36 | b | wheel(snap HEAD) | built[module] | zipapp from `built` (module) fixture: 2 full release builds in SETUP (not in call time) |
| `test_the_zipapp_propagates_a_nonzero_exit_from_a_failing_lane` (:502) | 0.83 | b | wheel(snap HEAD) | built[module] | zipapp from `built` (module) fixture: 2 full release builds in SETUP (not in call time) |

**`tests/test_canary_python_pipeline_nested_project.py`** — heavy 2/2 tests, 2.81 s of 2.81 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_a_root_level_project_passes_the_real_uncovered_line_canary` (:125) | 1.37 | c | in-proc | — | same, nested project |
| `test_a_nested_project_passes_the_real_uncovered_line_canary_identically` (:141) | 1.44 | c | in-proc | — | same, nested project |

**`tests/test_mutation_classification.py`** — heavy 4/13 tests, 2.50 s of 3.01 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_every_row_of_the_classification_table_when_equivalence_artifact_is_declared` (:201) | 0.64 | e | in-proc | — | 8 mutant snapshots via prepared_snapshot; fake process_runner |
| `test_the_killed_row_carries_its_kill_signal_verbatim_and_a_missing_signal_reclassifies_to_crashed` (:231) | 0.61 | e | in-proc | — | 8 mutant snapshots via prepared_snapshot; fake process_runner |
| `test_a_lane_without_an_equivalence_artifact_is_byte_identical_before_and_after` (:329) | 0.62 | e | in-proc | — | 8 mutant snapshots via prepared_snapshot; fake process_runner |
| `test_run_mutation_accepts_a_declared_artifact_with_real_baseline_bytes_the_control` (:421) | 0.63 | e | in-proc | — | 8 mutant snapshots via prepared_snapshot; fake process_runner |

**`tests/test_runner_lane_cwd.py`** — heavy 1/11 tests, 2.11 s of 3.44 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_both_r3_canary_halves_run_in_the_declared_cwd` (:270) | 2.11 | c | in-proc | — | R3 canary halves with real pytest in declared cwd |

**`tests/test_mutation_python_pipeline.py`** — heavy 2/2 tests, 2.11 s of 2.11 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_a_real_pytest_run_produces_a_genuine_killed_and_a_genuine_survived_mutant` (:71) | 1.52 | c | in-proc | — | run_mutation with real `python -m pytest` per mutant |
| `test_a_real_broken_baseline_stops_before_any_real_mutant_run` (:134) | 0.59 | c | in-proc | — | run_mutation with real `python -m pytest` per mutant |

**`tests/test_r3_canary_sees_infrastructure.py`** — heavy 1/3 tests, 2.04 s of 2.18 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_an_r3_lane_with_a_resolvable_derived_fact_judges_its_canary` (:108) | 2.04 | c | in-proc | — | in-process `assay run` R3 with real pytest |

**`tests/test_mutation_judge_identity_properties.py`** — heavy 1/4 tests, 1.82 s of 2.55 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_two_trees_digest_alike_if_and_only_if_their_content_is_alike` (:94) | 1.82 | f | in-proc | — | hypothesis max_examples=200 (file :46-47) |

**`tests/test_gate_qualify_cmru_b006a.py`** — heavy 3/63 tests, 1.58 s of 4.81 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_run_m20_preflight_reports_success_for_real` (:324) | 0.53 | c | none | — | harness runs real pytest on a synthetic module (gate/python/qualify_cmru_b006a.py) |
| `test_run_m20_preflight_reports_failure_for_real` (:333) | 0.54 | c | none | — | harness runs real pytest on a synthetic module (gate/python/qualify_cmru_b006a.py) |
| `test_run_repaired_node_runs_the_named_node_for_real` (:342) | 0.52 | c | none | — | harness runs real pytest on a synthetic module (gate/python/qualify_cmru_b006a.py) |

**`tests/test_state_dir_resume.py`** — heavy 2/17 tests, 1.22 s of 3.74 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_two_worktrees_of_one_commit_share_one_state_dir_and_the_second_resumes` (:126) | 0.55 | e (+c) | in-proc | — | two in-process runs incl. `git worktree add` |
| `test_a_source_edit_between_the_two_runs_reexecutes_the_touched_files_candidates` (:165) | 0.67 | e (+c) | in-proc | — | two in-process runs incl. `git worktree add` |

**`tests/test_untrusted_json_parse_sweep.py`** — heavy 2/14 tests, 1.13 s of 5.96 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_each_known_untrusted_site_is_still_present_and_guarded[identity0]` (:251) | 0.59 | f | static src read | — | re-parses every src/assay file per parametrized case (_collect_sites) |
| `test_each_known_untrusted_site_is_still_present_and_guarded[identity7]` (:251) | 0.54 | f | static src read | — | re-parses every src/assay file per parametrized case (_collect_sites) |

**`tests/test_liveness.py`** — heavy 1/46 tests, 1.03 s of 1.05 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_materialized_plugin_records_pid_and_optional_xdist_worker_in_a_subprocess` (:728) | 1.03 | c | in-proc + subprocess | — | materialized liveness plugin under a real pytest subprocess |

**`tests/test_runner_result_report.py`** — heavy 1/47 tests, 1.00 s of 1.98 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_a_declared_report_does_not_rescue_an_expired_budget` (:450) | 1.00 | a | in-proc | — | budget="1s" vs `sleep 30` |

**`tests/test_mutation_executor_bound.py`** — heavy 1/12 tests, 0.76 s of 2.00 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_jobs_1_and_jobs_3_produce_identical_ordered_records` (:189) | 0.76 | e | in-proc | — | jobs=1 vs jobs=3 mutant snapshots, fake runner |

**`tests/test_mutation_progress_budget_plan.py`** — heavy 1/50 tests, 0.69 s of 7.97 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_resume_progress_event_gains_rejudged_total` (:819) | 0.69 | e | in-proc | — | resume run over prepared snapshot, fake runner |

**`tests/test_mutation_isolation.py`** — heavy 1/5 tests, 0.66 s of 1.80 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_jobs_1_and_jobs_3_render_identical_records_under_the_real_executor` (:172) | 0.66 | e | in-proc | — | jobs=1 vs jobs=3 real executor snapshots |

**`tests/test_dependency_purity.py`** — heavy 1/14 tests, 0.65 s of 1.78 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_every_scanned_file_parses_and_declares_at_least_one_import` (:150) | 0.65 | f | static src read | — | AST scan of every src file |

**`tests/test_result_report_wiring_sweep.py`** — heavy 1/4 tests, 0.57 s of 1.41 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_every_required_site_still_passes_the_argument` (:143) | 0.57 | f | static src read | — | AST sweep of src/assay |

**`tests/test_canary_multi_target.py`** — heavy 1/17 tests, 0.53 s of 5.34 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_a_lane_declaring_no_per_attempt_bound_is_byte_unchanged` (:644) | 0.53 | e | in-proc | — | run_lane R3 5 snapshot materializations, fake gate |

**`tests/test_cli_provenance_and_request_base.py`** — heavy 1/37 tests, 0.52 s of 1.17 s file call-time

| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |
|---|---:|---|---|---|---|
| `test_a_request_supplied_symbolic_ref_resolves_the_same_way_a_declared_one_does` (:646) | 0.52 | e | in-proc | — | symbolic ref resolution run over git_repo |

CATSUM {'e': 24.9, 'd': 73.6, 'b': 54.2, 'a': 91.0, 'c': 69.9, 'f': 4.2}
EXSUM {'none': 10.0, 'PATH wheel': 73.6, 'locked 1.2.5': 13.8, 'in-proc': 162.0, 'wheel(snap)': 34.8, 'none*': 17.2, 'wheel(snap HEAD)': 2.9, 'static src read': 2.4, 'in-proc + subprocess': 1.0}
