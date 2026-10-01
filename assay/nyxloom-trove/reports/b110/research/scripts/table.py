import json, collections, re, ast, pathlib
F='/workspaces/vbpub/.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy/.worktrees/assay-b105-gate-copy-30eec294/assay/.assay/progress-self-qualification.jsonl'
t=[json.loads(l) for l in open(F)]; t=[r for r in t if r.get('event')=='test']
allby=collections.defaultdict(list)
for r in t: allby[r['nodeid'].split('::')[0]].append(r)
h=[r for r in t if r['duration_s']>=0.5]
# category map: (file, funcprefix) -> (cat, src_exec, note)
C={}
def c(f, names, cat, ex, note):
    for n in names: C[(f,n)]=(cat,ex,note)
PQ='tests/test_python_qualification.py'
c(PQ,['test_materialize_scenario_leaves_the_real_checkout_byte_for_byte_untouched','test_materialize_scenario_baseline_is_identical_across_different_scenarios','test_materialize_scenario_writes_a_v1_lane_for_the_locked_release_and_v2_for_everyone_else','test_a_topos_pass_alone_cannot_witness_agreement'],'e','none','git export+commit of pinned Topos tree (966 entries) from REPO_ROOT history')
c(PQ,['test_run_scenario_reproduces_the_locked_missing_line_terminal','test_run_scenario_records_the_exclusion_capability_asymmetry','test_integrity_matrix_negatives_produce_their_frozen_terminals','test_universal_pass_mutation_is_rejected_by_the_whole_document_comparator','test_a_scenario_that_must_compare_with_topos_refuses_a_missing_witness','test_a_scenario_that_measured_nothing_is_refused','test_the_wrong_source_root_decoy_is_rejected_because_of_the_root'],'d (+e)','PATH wheel','`installed_assay` (module) -> run-venv `assay run topos-qualification` over materialized Topos repo')
c(PQ,['test_install_locked_release_produces_a_pure_hash_bound_venv'],'b','locked 1.2.5','venv + pip --require-hashes of committed 1.2.5 wheel')
c(PQ,['test_release_smoke_scenario_matches_the_current_full_pass_shape'],'b (+e)','locked 1.2.5','installs 1.2.5 wheel then runs it on a materialized scenario')
CR='tests/test_cli_run.py'
c(CR,['test_run_liveness_classifies_a_thread_join_hang_as_hung'],'a (+c)','in-proc','liveness hung window: _HUNG_CPU_WINDOW_S=30.0 (liveness.py:523) + LIVENESS_IDLE_FLOOR_S=15.0 (:641); budget_per_candidate="50s"')
c(CR,['test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung'],'a (+c)','in-proc','budget_per_candidate="35s" deliberately > 30 s CPU window; spins until budget')
c(CR,['test_run_liveness_does_not_turn_a_configure_time_raise_into_a_false_survivor','test_run_evaluates_a_real_r3_pass_end_to_end'],'c','in-proc','in-process `assay run` R2/R3 on toy pytest project')
c(CR,['test_unknown_rejudge_id_is_a_verified_whole_lane_refusal_including_r3','test_stale_source_rejudge_id_is_bad_lane_config_and_verifiable','test_rejudge_id_excluded_by_shard_is_a_verified_bad_lane_config','test_stale_rejudge_id_refuses_when_source_edit_removes_all_candidates','test_corrupt_rejudge_store_keeps_unreadable_artifact_classification','test_valid_rejudge_id_reexecutes_only_its_selected_record'],'e (+c)','in-proc','in-process CLI R2 with /bin/sh grep judge; cost = snapshot/mutant-commit materialization x2')
ST='tests/test_standalone.py'
c(ST,['test_a_real_python_fixture_passes_through_the_installed_wheel','test_a_real_r1_lane_passes_through_the_installed_wheel','test_a_real_r2_lane_kills_one_mutant_and_lets_another_survive_through_the_wheel','test_a_real_r2_lane_diffs_the_resolved_merge_base_not_the_declared_ref','test_a_real_r3_lane_proves_the_canary_and_passes_through_the_wheel','test_a_real_r3_lane_with_a_broken_control_is_inconclusive_through_the_wheel','test_a_real_r3_lane_whose_bad_case_unexpectedly_passes_survives_through_the_wheel','test_a_real_r3_lane_proves_the_uncovered_line_canary_through_the_wheel','test_a_real_r3_lane_whose_bad_case_fails_for_the_wrong_cause_survives'],'c','wheel(snap)','`standalone` (session) venv `assay run` subprocess; wheel built from snapshot src')
c(ST,['test_a_real_r2_mutant_that_outlives_the_lane_budget_is_its_own_bucket'],'a (+c)','wheel(snap)','lane budget="5s" vs `sleep 300` mutant')
c(ST,['test_removing_package_data_ships_a_wheel_whose_schema_cannot_load','test_removing_the_console_script_ships_a_wheel_with_no_invocable_binary','test_declaring_a_runtime_dependency_breaks_the_offline_scratch_install'],'b','wheel(snap)','builds + installs its own modified wheel (venv, pip wheel, pip install)')
DG='tests/test_distribution_gate.py'
c(DG,['test_closure_build_produces_a_real_non_placeholder_dev_identity','test_ambient_only_build_without_the_closure_is_refused_as_a_placeholder'],'b','none*','gate bash fns: closure venvs + pip wheel of synthetic repo (copied src); asserts version only')
c(DG,['test_the_shipped_source_tree_is_pyflakes_clean'],'b','none*','`lint_venv` (session) + pyflakes over src/assay+tests')
B6='tests/test_b106_reuse_and_witness.py'
c(B6,['test_plan_refuses_to_preview_witness_reuse_when_pytest_ini_enables_xdist'],'c','in-proc','in-process `assay plan`')
c(B6,['test_replay_requires_a_current_kill_and_falls_back_to_a_full_run','test_witness_capture_works_with_the_existing_liveness_plugin','test_custom_sessionfinish_hook_forces_full_suite_fallback','test_resume_preserves_witness_prefix_execution_provenance','test_rejudge_outcome_disables_prior_witness_replay'],'c','in-proc','run_lane R2 with real `python -m pytest` per mutant (+resume reruns)')
R3='tests/test_runner_run_lane_r3.py'
for f,ns,cat,ex,note in [
 (R3,['test_r3_alone_proves_the_declared_canary_through_run_lane','test_r3_proves_the_uncovered_line_canary_for_its_own_reason_when_r1_is_declared','test_r3_canary_halves_consume_carried_bases_in_a_history_cut_snapshot','test_r3_reports_a_real_wrong_cause_as_survived_with_the_unmocked_adapter','test_r3_proves_a_canary_for_a_project_in_a_subdirectory_of_its_repo','test_r1_r2_and_r3_together_each_render_their_own_independent_claim'],'c','in-proc','run_lane R3 canary halves = 2+ real pytest subprocesses'),
 ('tests/test_canary_python_pipeline.py',None,'c','in-proc','in-process canary orchestration, real pytest subprocess per half'),
 ('tests/test_canary_python_pipeline_nested_project.py',None,'c','in-proc','same, nested project'),
 ('tests/test_mutation_judge_identity.py',None,'e (+c)','in-proc','in-process `assay run --resume` twice, /bin/sh judge; git snapshot/mutant commits'),
 ('tests/test_progress_phase_stream.py',['test_a_real_run_emits_a_real_tick_through_the_real_flag'],'a','in-proc','`sleep 7` command vs --progress-heartbeat 5 (PROGRESS_HEARTBEAT_FLOOR_SECONDS=5.0, runner.py:936)'),
 ('tests/test_progress_phase_stream.py',['test_the_heartbeat_ticks_while_a_command_runs_and_stops_when_it_returns'],'a','in-proc','time.sleep(0.35)+time.sleep(0.15), 0.02 s interval'),
 ('tests/test_environment_preflight.py',['test_a_probe_that_exhausts_its_budget_reports_a_timeout_not_a_config_error'],'a','in-proc','lane budget="2s" vs probe `sleep 45`'),
 ('tests/test_environment_preflight.py',['test_the_probe_cap_is_enforced_where_execute_plan_actually_reads_it'],'a','in-proc','PROBE_BUDGET_SECONDS patched 30.0->3.0 (runner.py:360) vs `sleep 20`'),
 ('tests/test_environment_preflight.py',['test_an_r2_lane_run_twice_with_nothing_changed_passes_both_times'],'e (+c)','in-proc','two in-process R2 runs'),
 ('tests/test_lane_timeout_writes_a_verdict.py',None,'a','in-proc','BUDGET="1s" vs SLOW_COMMAND="sleep 30" (file :50-51)'),
 ('tests/test_distribution_release_wheel.py',None,'b','none','venv + pip --require-hashes refusal (gate/distribution/release_wheel.py)'),
 ('tests/test_distribution_build_release.py',None,'b','wheel(snap HEAD)','zipapp from `built` (module) fixture: 2 full release builds in SETUP (not in call time)'),
 ('tests/test_mutation_classification.py',None,'e','in-proc','8 mutant snapshots via prepared_snapshot; fake process_runner'),
 ('tests/test_runner_lane_cwd.py',None,'c','in-proc','R3 canary halves with real pytest in declared cwd'),
 ('tests/test_mutation_python_pipeline.py',None,'c','in-proc','run_mutation with real `python -m pytest` per mutant'),
 ('tests/test_r3_canary_sees_infrastructure.py',None,'c','in-proc','in-process `assay run` R3 with real pytest'),
 ('tests/test_mutation_judge_identity_properties.py',None,'f','in-proc','hypothesis max_examples=200 (file :46-47)'),
 ('tests/test_gate_qualify_cmru_b006a.py',None,'c','none','harness runs real pytest on a synthetic module (gate/python/qualify_cmru_b006a.py)'),
 ('tests/test_state_dir_resume.py',None,'e (+c)','in-proc','two in-process runs incl. `git worktree add`'),
 ('tests/test_untrusted_json_parse_sweep.py',None,'f','static src read','re-parses every src/assay file per parametrized case (_collect_sites)'),
 ('tests/test_liveness.py',None,'c','in-proc + subprocess','materialized liveness plugin under a real pytest subprocess'),
 ('tests/test_runner_result_report.py',None,'a','in-proc','budget="1s" vs `sleep 30`'),
 ('tests/test_mutation_executor_bound.py',None,'e','in-proc','jobs=1 vs jobs=3 mutant snapshots, fake runner'),
 ('tests/test_mutation_progress_budget_plan.py',None,'e','in-proc','resume run over prepared snapshot, fake runner'),
 ('tests/test_mutation_isolation.py',None,'e','in-proc','jobs=1 vs jobs=3 real executor snapshots'),
 ('tests/test_dependency_purity.py',None,'f','static src read','AST scan of every src file'),
 ('tests/test_result_report_wiring_sweep.py',None,'f','static src read','AST sweep of src/assay'),
 ('tests/test_canary_multi_target.py',None,'e','in-proc','run_lane R3 5 snapshot materializations, fake gate'),
 ('tests/test_cli_provenance_and_request_base.py',None,'e','in-proc','symbolic ref resolution run over git_repo'),
]:
    if ns is None: C[(f,'*')]=(cat,ex,note)
    else: c(f,ns,cat,ex,note)
scoped={'standalone':'standalone[session]','installed_assay':'installed_assay[module]','built':'built[module]','gate_functions':'gate_functions[session]','lint_venv':'lint_venv[session]','validator':'validator[session]'}
hf=collections.defaultdict(list)
for r in h: hf[r['nodeid'].split('::')[0]].append(r)
out=[]; catsum=collections.Counter(); exsum=collections.Counter()
for f,rs in sorted(hf.items(), key=lambda kv:-sum(r['duration_s'] for r in kv[1])):
    src=pathlib.Path(f).read_text(); tree=ast.parse(src)
    funcs={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    tot=sum(r['duration_s'] for r in allby[f]); hs=sum(r['duration_s'] for r in rs)
    out.append(f"\n**`{f}`** — heavy {len(rs)}/{len(allby[f])} tests, {hs:.2f} s of {tot:.2f} s file call-time\n")
    out.append("| test (line) | call s | cat | executes snapshot `src/assay`? | scoped fixtures | dominant cost |")
    out.append("|---|---:|---|---|---|---|")
    for r in rs:
        name=r['nodeid'].split('::',1)[1]; base=re.sub(r'\[.*','',name)
        fn=funcs[base]; args=[a.arg for a in fn.args.args]
        sc=', '.join(scoped[a] for a in args if a in scoped) or '—'
        cat,ex,note=C.get((f,base)) or C.get((f,'*'))
        catsum[cat.split()[0]]+=r['duration_s']; exsum[ex]+=r['duration_s']
        out.append(f"| `{name}` (:{fn.lineno}) | {r['duration_s']:.2f} | {cat} | {ex} | {sc} | {note} |")
print('\n'.join(out))
print('\nCATSUM', {k:round(v,1) for k,v in catsum.items()}); print('EXSUM', {k:round(v,1) for k,v in exsum.items()})
