# W9 LOG (B108 phase 1, `assay analyze campaign`)

Base: `40317e29` (W8 reviewed head). Branch `wave-a-w9-campaign`. STATUS: checkpoint 3 after step 3a (`c71bd31b`); see `W9-CONTINUATION.md` for the remaining P8 behaviour steps.

## Draft sha256 (pre-edit, `reports/wave-a/b108-draft-20260927/`)
```
98b891d0dbb91f6ba7f1be24c4959d439202135c14f2d644cc3cafc526a62065  campaign.py
f93556bf906011a7ba40c23ed1203887d420b8d0364c0d708a98cd019a3f10f2  test_campaign.py
ae17a52a07ded8092fd0304890a65c9ce41eed2a6f723db50a2ad2a4a424ff9b  analysis-campaign.schema.json
eb5585414886feba6b05668a708dae12c53907bd5e713d38238abc6e853239cb  tracked-changes.patch
```

## Commits
| step | commit | content |
|---|---|---|
| J | `814494ac` | J1-J5: `_discover_plan_jobs`/`_PlanDiscovery`, `_plan_rows_from_jobs`, `PlanRow`, `plan_jobs`, `candidate_identity_fields`, four public aliases, `candidates`-event `judge_sha256`; O16a, O20 tests |
| port | `e251f6ef` | draft ported (edits 1-9); analysis 290 passed; deviations: `ALLOWED_JUDGE_MODULES` +6 modules; draft test :251 inversion deferred to step 3 (needs status row 3); dead code removed from `_run_summary` |
| 3a | `c71bd31b` | exit mapping 0/1/3, closed 7-key `evidence_error` document on stdout (+ stderr line, exit 2), `qualifying: true`, schema `oneOf` document/evidenceError; analysis 293 passed. The :251 test now expects exit 1 (BUDGET_EXCEEDED verdict, still `complete`) until step d inverts it |
| Q1 | `94e1f616` | CD51 Q1: `errors[].source` is the refused input (`_staged` decorator + `stage.name` markers in `campaign()`); `test_cd51_an_error_source_names_the_refused_input` pins 7 vocabulary words (all but `state`) |
| 3b-3f code | `70ab9239` | optional `--verdict`; `--state-dir`, `--request-base`, `--project`/`--project-jobs`; section C identity check; `EvidenceErrors`; CD40-ordered `_reconcile_state`; 20-key rows, `adverse`, `unresolved`, `complete_blockers`, `reclassified`, `campaign.selected_total`; `MIN_ETA_SAMPLE = 20` ETA object; pure `project()`; closed schema (`analysis-campaign.schema.json` rewritten); analysis/tests 300 passed. Deviation: sub-steps b-f went in ONE code commit (the closed document shape and the schema are shared, so a partial state cannot validate). Oracle tests for O2-O10, O12-O14a, O17-O19, O22-O26 are NOT written yet |

## QUESTIONS (numbered; each took the conservative reading)
1. (SUPERSEDED by CD51 Q1: `errors[].source` is the refused input from a fixed vocabulary.)
2. (CLOSED by CD51 Q2: `reclassified` item is `{candidate_id, source, runs: [{run_id, bucket}], state_bucket}`.)
3. With an UNKNOWN current judge (C25 yields none) no record counts; in-plan, in-selection records that do not pair with a same-bucket latest-run event are reported as `state.unreconciled` (they block `complete`). Paired records only enrich rows.
4. The v14 `evidence` object's key names are not defined before v14: `started_count` reads `evidence.started_count` (non-negative int) and `evidence_command` reads `evidence.command` (str); anything else is `null`.
5. Blocker `terminal_disagrees` is also raised in verdict mode when the latest run has NO terminal event (a present-but-different terminal stays an `evidence_error`).
6. No verdict plus `--coverage` on an R1 lane is an `evidence_error` (the artifact cannot be re-verified without a verdict R1 claim). No verdict plus R1 lane without `--coverage`: `not_supplied` (blocks complete).
7. `campaign.selected_total` is added (P8 "Counts"; CD39 names only `execution_mode_counts` inside `campaign`). `unresolved.candidates` is a sorted list of candidate id strings capped at `--limit`.
8. "Never-started" `budget_exceeded` (status row 3) is read literally: no `candidate` event in the latest run, even when a counted state record exists for it.

9. Never-started `budget_exceeded` candidates (status row 3): `outcomes`, `adverse` and `candidate_details` keep the verdict's bucket for them, but `completed_total` excludes them and `pending_total` includes them, so `no_remaining_work` is never claimed for a timed-out campaign.
10. `projection.bases.<basis>` with fewer than 20 samples is an object whose `fallback_counts`, `serial_seconds_p50/p90` are `null` and `reason` is `"insufficient_sample"` (a bare `null` cannot carry the reason); a basis that computed has `reason: null`. `wall_seconds_*` are `null` when the `killed` basis is insufficient.
11. Stage tags for CD51 Q1: root/`--expected-commit`/scalar checks and the `--command-exit` mismatch -> `arguments`; lane file/lane lookup -> `lane`; verdict read/verify/policy/inventory checks (including verdict-vs-plan inventory) -> `verdict`; plan build, plan-row identity -> `plan`; per-run stream checks (including progress-vs-verdict per-candidate disagreement) -> `progress`; latest-run-vs-verdict scope/terminal checks, `--log` -> `input`; state dir/records -> `state`; `--coverage` -> `coverage`.
12. (SUPERSEDED by CD55 Q12: `--command-exit` without `--verdict` is an `evidence_error`, `source: "arguments"`, message `--command-exit requires --verdict`; `command_exit_not_observed` is present exactly when `--command-exit` is absent.)
13. `projection.fixed_components.other` subtracts a missing coverage/r2 source as 0 (the missing source stays listed in `fixed_components_missing`); every duration is clamped at >= 0.
14. Without a verdict, a candidate event outside the latest run's selection is refused; the message is now the same for verdict mode (`latest progress run includes candidates outside its selected scope`).
15. Candidate-event `cpu_seconds`, `peak_rss_bytes`, `phase_seconds`, `startup_seconds` that break W8's shapes are refused as a progress `evidence_error` instead of being copied verbatim.
16. `--project`/`--project-jobs` pairing is enforced in `run_campaign_command` with `parser.error` (SystemExit 2, argparse usage text), the `--project-jobs` range by an argparse `type`.
17. The hung-evidence fallback to a state record (row `liveness_*`) reads only current-judge, in-selection records (counted, unreconciled or unverified-hung); stale-judge records are never a source.
18. In verdict mode the ETA can say `no_remaining_work` while the status is `incomplete` (progress stream cut, verdict already resolves every candidate).

19. **Real bug found by O19 and fixed (not a design choice):** `_check_verdict_against_lane` read `lane.judge.coverage.fail_under`, but `CoverageConfig` has only `format`/`artifact`/`producer` (`fail_under` lives on `JudgeConfig`), so any R1 lane raised `AttributeError` before the coverage check. Fixed to `lane.judge.fail_under`; the `--coverage`-free path never reached it in mocked tests.
20. **Real bug found by O19 and fixed:** `_coverage_artifact_summary` called `read_bounded_file(...)` without the keyword-only `limit`, so `--coverage` on any real R1 lane raised `TypeError`. Fixed with the judge's own `coverage.MAX_COVERAGE_ARTIFACT_BYTES` (16 MiB), the bound `assay.coverage` already applies to the same artifact.
21. O19 residual: the lane command writes `cov.json` inside the run's isolated snapshot, so it does not exist in the worktree after `assay run`. The O19 test writes the same bytes into the worktree before the analysis (the analysis reads only from the worktree and never the snapshot). No design change made; the closeout workflow needs the artifact at the declared path. Recorded for CONSUMERS.

## Judge oracles (J step)
Positive: `tests/core/test_cli_plan_jobs.py` 11 passed; `tests/core/test_mutation_candidates_event_judge.py` 2 passed; O21 set (`test_b105_cli_boundaries`, `test_cli_plan_estimate_hint`, `test_mutation_judge_identity*`, `test_mutation_progress_budget_plan`, `test_b106_reuse_and_witness`, `test_cli_provenance_and_request_base`, `test_import_contracts`, `test_cli_run`) 360 passed, 1 skipped, all unmodified.

| oracle | test | controlled break (Edit, run red, Edit back, run green) | result |
|---|---|---|---|
| O20 | `test_o20_the_candidates_event_carries_the_judge_every_record_carries` | `**({"judge_sha256": judge} ...)` removed from the `candidates` event | red, reverted, green |
| O16a (vii) | `test_o16a_vii_allow_dirty_reaches_the_integrity_probe` | `allow_dirty=False` hard-coded in `_cmd_plan`'s discovery call | red, reverted, green |
| O16a (viii) | `test_o16a_viii_the_reuse_command_is_resolved_only_when_reuse_is_requested` | `resolve_reuse_command=reuse_source is None` (inverted) | red, reverted, green |

## Campaign oracle traceability (work | owner | oracle | test id | controlled break | failures)
Every break is an Edit on `campaign.py` (or the named file), observed red, reverted by Edit, observed green. Nothing broken is committed. Test ids are in `analysis/tests/test_analysis_campaign.py` unless named.

| work | owner | oracle | test id | controlled break | failures |
|---|---|---|---|---|---|
| CD55 Q12 | W9 | CD55 | `test_cd55_q12_command_exit_without_a_verdict_is_an_evidence_error` | the `--command-exit requires --verdict` guard disabled (`if False and ...`) | 1 failed |
| group 1 | W9 | O1 (no `--command-exit`) | `test_analysis_campaign_oracles.py::test_o1_a_pass_campaign_without_command_exit_is_incomplete` | `("command_exit_not_observed", False)` in the blocker set | red (also breaks the other command-exit blocker checks) |
| group 1 | W9 | O3 | `..._oracles.py::test_o3_without_a_verdict_the_result_is_never_complete` | `("no_verdict", False)` | red |
| group 1 | W9 | O2 | `..._oracles.py::test_o2_appended_resume_runs_count_each_candidate_once` | `completed_total = sum(run["fresh_candidate_events"] ...)` (summing candidate events across runs: 9, not 8) | red |
| group 1 | W9 | O6 | `..._oracles.py::test_o6_a_candidate_reclassified_between_runs_is_counted_once_as_its_latest_bucket` | `reclassified` guard `> 1` -> `> 5` | red |
| group 1 | W9 | O7 (19 samples) | `..._oracles.py::test_o7_nineteen_samples_give_no_eta` | `MIN_ETA_SAMPLE = 5` (the WIP threshold) | red (also fails the schema `const 20`) |
| group 1 | W9 | O7 (20 samples) | `..._oracles.py::test_o7_twenty_samples_use_nearest_rank_percentiles` | `_nearest_rank` replaced by the median (p50 10.5) | red, sole break |
| group 1 | W9 | O10 | `..._oracles.py::test_o10_the_adverse_page_reports_its_total_and_next_offset` | `_page` `next_offset` hard-coded `None` (silent truncation) | red |
| group 1 | W9 | O12 (no verdict) | `..._oracles.py::test_o12_a_torn_final_record_is_incomplete_without_a_verdict` | `tolerate_torn=False` (the WIP always refuses) | red |
| group 1 | W9 | O12 (verdict) | `..._oracles.py::test_o12_a_torn_final_record_is_an_evidence_error_with_a_verdict` | `tolerate_torn=True` (always accepts) | red, sole break |
| group 1 | W9 | O13 | `..._oracles.py::test_o13_the_schema_rejects_documents_that_break_its_closed_shape` | `"additionalProperties": false` removed from the `timing` schema | red, sole break |
| group 1 | W9 | O16b | `..._oracles.py::test_o16b_a_plan_row_whose_identity_inputs_do_not_reproduce_its_id_is_refused` | `_identity_reproduces` returns `True` | red |
| group 1 | W9 | O24 (option) | `..._oracles.py::test_o24_the_pilot_candidates_file_option_does_not_exist` | `--candidates-file` added to the parser | red, sole break |
| group 1 | W9 | O24 (event) | `..._oracles.py::test_o24_a_pilot_selection_in_the_progress_stream_is_an_evidence_error` | the `selection_sha256` refusal disabled | red |
| group 1 | W9 | O26 | `..._oracles.py::test_o26_verdict_and_no_verdict_documents_validate_against_the_schema` | `{"type": "null"}` deleted from the schema's `verdict` | red, sole break |
| group 2 | W9 | O4 (bucket) | `test_analysis_campaign_state.py::test_o4_a_state_record_bucket_that_disagrees_with_the_verdict_names_the_file` (this file is `..._state.py` below) | `if False and disagreements:` (silently prefer one source) | red; also pins the `state` error-source word (CD51 Q1) |
| group 2 | W9 | O4 (identity) | `..._state.py::test_o4_a_state_record_whose_identity_does_not_match_its_file_name_is_refused` | `_identity_reproduces` check skipped in `_state_entry`; separately the `candidate_id != stem` check disabled | red for each, sole break in the second run |
| group 2 | W9 | O5 F2 | `..._state.py::test_o5_f2_a_resumed_run_at_a_new_judge_counts_only_its_own_events` | the `candidates`-event judge ignored (`if False:`) | red |
| group 2 | W9 | O5 F2b | `..._state.py::test_o5_f2b_current_judge_records_without_an_event_are_unreconciled` | `elif True:` (eventless records always counted) | red |
| group 2 | W9 | O5 F2c (paired) | `..._state.py::test_o5_f2c_without_a_judge_in_the_event_it_is_derived_from_paired_records` | `elif False:` on the paired-judge branch | red |
| group 2 | W9 | O5 F2c (two judges) | `..._state.py::test_o5_f2c_two_paired_judges_leave_the_judge_unknown_and_count_nothing` | `len(paired_judges) >= 1` (raises on two judges) | red, sole break |
| group 2 | W9 | O22 (unverified) | `..._state.py::test_o22_an_old_hung_record_without_evidence_is_not_counted` | predicate replaced by `lambda value: True` (the brief's negative) | red (also the resumed-valid and input-B tests) |
| group 2 | W9 | O22 (resumed valid) | `..._state.py::test_o22_a_hung_record_with_valid_evidence_is_counted_when_resumed` | same predicate break and `elif True:` | red (shared break) |
| group 2 | W9 | O22 input A | `..._state.py::test_o22_input_a_a_verdict_resolved_hung_record_without_evidence_does_not_block` | blocker condition replaced by `True` (also red under the paired-judge break) | red |
| group 2 | W9 | O22 input B | `..._state.py::test_o22_input_b_an_event_with_valid_evidence_beats_an_older_invalid_record` | `_liveness` reads the record before the event | red, sole break (after the fixture's record was given invalid evidence) |
| group 2 | W9 | O23 (absent/None) | `..._state.py::test_o23_a_hung_row_without_evidence_is_absent_and_a_killed_row_has_none` | `_liveness` returns `(None, None)` for missing evidence | red |
| group 2 | W9 | O23 (event before record) | `..._state.py::test_o23_a_hung_row_reads_its_event_evidence_before_its_record` | `_liveness` reads the record first | red |
| group 2 | W9 | O14a | `..._state.py::test_o14a_a_witness_cold_event_is_counted_without_a_code_change` | mode counter filtered to `("full", "witness-prefix")` (hard-coded list) | red |
| group 2 | W9 | O8 | `..._state.py::test_o8_timing_and_resources_never_change_classification` | verdict-mode bucket forced to `hung` when `elapsed_seconds > 1000` | red |
| group 2 | W9 | O9 (size class) | `..._state.py::test_o9_the_size_class_is_taken_from_plan_rows_at_the_boundaries` | `_size_class` `<= 10` -> `< 10` | red |
| group 2 | W9 | O9 (fallbacks) | `..._state.py::test_o9_fallbacks_step_from_the_stratum_to_the_operator_to_everything` | pool threshold `>= 3` | red |
| group 2 | W9 | O9 (under 20) | `..._state.py::test_o9_a_basis_under_twenty_samples_is_an_object_with_its_reason` | `len(samples) < 3` | red |
| group 2 | W9 | O9 (`--project` pairing) | `..._state.py::test_o9_project_needs_a_jobs_count_in_range[extra0,extra1]` | pairing check `if False:` | red |
| group 3 | W9 | O17 F1 (200 unstarted, `LANE_TIMEOUT`) | `..._verdicts.py::test_o17_f1_a_lane_timeout_with_two_hundred_unstarted_candidates_is_incomplete` | `("lane_timeout_or_unstarted", lane_timeout_row)` -> `False` | red (run singly under the batch) |
| group 3 | W9 | O17 F4 (sharded PASS never complete) | `..._verdicts.py::test_o17_f4_a_sharded_pass_verdict_is_never_a_complete_campaign` | drop `and expected_ids == plan_id_set` from `complete_inventory` | red (singly) |
| group 3 | W9 | O18 (six buckets, exact counts, adverse ids/paths/operators) | `..._verdicts.py::test_o18_all_six_buckets_are_counted_and_the_adverse_candidates_named` | `ADVERSE_BUCKETS` drops `"hung"` | red (singly) |
| group 3 | W9 | O18 (stale commit) | `..._verdicts.py::test_o18_a_stale_commit_verdict_is_an_evidence_error` | `_read_verified_verdict` commit check -> `if False:` (the later lane check words it differently, so the exact message goes red) | red (singly) |
| group 3 | W9 | O18 (wrong lane) | `..._verdicts.py::test_o18_a_verdict_for_another_lane_is_an_evidence_error` | `_check_verdict_against_lane` lane check -> `if False:` | red (singly) |
| group 3 | W9 | O19 (real R1 run, exact missing line + arc) | `..._real.py::test_o19_coverage_reverifies_the_artifact_and_lists_the_gaps_exactly` | `arcs.extend(rows)` -> `arcs.extend(rows[:0])`; also reverting either Q19/Q20 fix | red for each |
| group 3 | W9 | O19 (no `--coverage`: `not_supplied`, declared artifact absent and never read) | `..._real.py::test_o19_without_coverage_the_declared_artifact_is_never_read` | `not_supplied` branch returns `missing_branch_arcs: []` (gap shown as zero) | red (singly) |
| group 2 | W9 | O9 (jobs range) | `..._state.py::test_o9_project_needs_a_jobs_count_in_range[extra2,extra3,extra4]` and `test_o9_the_library_entry_refuses_a_jobs_count_out_of_range` | `_project_jobs` `1 <=` -> `0 <=`; `<= 100`; `except` value 1; library `0 <=` | red for each |
