import ast, sys, pathlib
def analyze(path, moving):
    src=pathlib.Path(path).read_text(); tree=ast.parse(src)
    defs={}
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.ClassDef,ast.AsyncFunctionDef)): defs[n.name]=n
        elif isinstance(n,(ast.Assign,ast.AnnAssign)):
            targets=n.targets if isinstance(n,ast.Assign) else [n.target]
            for t in targets:
                for x in ast.walk(t):
                    if isinstance(x,ast.Name): defs[x.id]=n
    imported=set()
    for n in tree.body:
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            for a in n.names: imported.add((a.asname or a.name).split('.')[0])
    def used(node):
        s=set()
        for x in ast.walk(node):
            if isinstance(x,ast.Name): s.add(x.id)
            if isinstance(x,ast.arg): s.add(x.arg)  # fixture params
        return s
    closure=set(); stack=list(moving)
    while stack:
        name=stack.pop()
        if name in closure or name not in defs: continue
        closure.add(name)
        for u in used(defs[name]):
            if u in defs and u not in closure: stack.append(u)
    helpers=sorted(closure-set(moving))
    # which helpers are also used by tests that stay
    stay=[n for n in defs if n.startswith('test_') and n not in moving and isinstance(defs[n],ast.FunctionDef)]
    staying_use=set()
    for s in stay:
        stack=[s]; seen=set()
        while stack:
            nm=stack.pop()
            if nm in seen or nm not in defs: continue
            seen.add(nm)
            for u in used(defs[nm]):
                if u in defs: stack.append(u)
        staying_use|=seen
    shared=[h for h in helpers if h in staying_use]
    exclusive=[h for h in helpers if h not in staying_use]
    print(f'== {path}: moving {len(moving)}; helpers shared-with-staying={shared}; exclusive={exclusive}')
plans={
 'tests/test_cli_run.py':['test_run_liveness_classifies_a_thread_join_hang_as_hung','test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung','test_run_liveness_does_not_turn_a_configure_time_raise_into_a_false_survivor','test_run_evaluates_a_real_r3_pass_end_to_end','test_unknown_rejudge_id_is_a_verified_whole_lane_refusal_including_r3','test_stale_source_rejudge_id_is_bad_lane_config_and_verifiable','test_rejudge_id_excluded_by_shard_is_a_verified_bad_lane_config','test_stale_rejudge_id_refuses_when_source_edit_removes_all_candidates','test_corrupt_rejudge_store_keeps_unreadable_artifact_classification','test_valid_rejudge_id_reexecutes_only_its_selected_record'],
 'tests/test_b106_reuse_and_witness.py':['test_replay_requires_a_current_kill_and_falls_back_to_a_full_run','test_witness_capture_works_with_the_existing_liveness_plugin','test_custom_sessionfinish_hook_forces_full_suite_fallback','test_resume_preserves_witness_prefix_execution_provenance','test_rejudge_outcome_disables_prior_witness_replay'],
 'tests/test_distribution_gate.py':['test_closure_build_produces_a_real_non_placeholder_dev_identity','test_ambient_only_build_without_the_closure_is_refused_as_a_placeholder','test_the_shipped_source_tree_is_pyflakes_clean'],
 'tests/test_mutation_judge_identity.py':['test_cli_writes_structured_verdict_for_non_object_resume_record','test_a_strengthened_test_file_re_executes_instead_of_replaying_survived','test_filtered_identity_resumes_across_an_excluded_report_commit','test_a_touched_but_unchanged_test_file_still_resumes','test_the_same_tree_at_a_different_commit_still_resumes','test_a_relocated_test_file_re_executes','test_a_lane_whose_argv_names_no_path_still_notices_a_changed_suite','test_two_worktrees_of_one_commit_still_share_one_state_dir','test_two_instances_with_different_passthrough_values_still_resume','test_dropping_a_passthrough_declaration_re_executes','test_a_rejected_store_says_so_in_the_progress_stream'],
 'tests/test_progress_phase_stream.py':['test_a_real_run_emits_a_real_tick_through_the_real_flag'],
 'tests/test_environment_preflight.py':['test_a_probe_that_exhausts_its_budget_reports_a_timeout_not_a_config_error','test_the_probe_cap_is_enforced_where_execute_plan_actually_reads_it'],
 'tests/test_distribution_release_wheel.py':['test_pip_require_hashes_rechecks_bytes_mutated_after_a_successful_verify'],
 'tests/test_runner_lane_cwd.py':['test_both_r3_canary_halves_run_in_the_declared_cwd'],
 'tests/test_liveness.py':['test_materialized_plugin_records_pid_and_optional_xdist_worker_in_a_subprocess'],
 'tests/test_runner_result_report.py':['test_a_declared_report_does_not_rescue_an_expired_budget'],
 'tests/test_distribution_build_release.py':['test_the_zipapp_reports_the_wheels_version_and_never_the_source_fallback','test_the_zipapp_verifies_a_real_artifact_and_refuses_a_foreign_version','test_the_zipapp_propagates_a_nonzero_exit_from_a_failing_lane'],
}
for p,m in plans.items(): analyze(p,m)
