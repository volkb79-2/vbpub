# Run-Gate R2 mutation triage — 2026-10-29

## Scope and disposition

This report triages the completed R2 campaign against `run-gate-v24.0.1`
(`a24590839fa6d5e3758bd7f7e9362f56d7ca9fba`), run on 2026-10-29. The campaign
accounted for all 193 candidates: 148 killed, 34 survived, 10 crashed, and 1
exceeded the per-candidate budget. Its analysis has `status = complete` and no
analysis blockers. The source evidence is the read-only campaign checkout
`.worktrees/r2-plan-a245-20261028/run-gate-project/.assay/`:
`campaign-r2.json`, `campaign-r2.txt`, `progress-r2.jsonl`, and
`verdict-r2.json`.

The survivor review classifies **8 EQUIVALENT** candidates and **26
UNTESTED-BEHAVIOUR** candidates. The latter each have a behavioral oracle
listed below. The 10 crashes and 1 timeout are **HARNESS** outcomes: their
candidate runs did not yield valid test verdicts, so they are neither kills
nor survivals. Their process-tree and resource-limit evidence is filed as
RG-120 and RG-121 in the backlog.

Here **HARNESS** means the campaign did not produce a valid behavioral
verdict; it does not assert that a mutant could not have contributed to the
cleanup, resource, or time-limit event.

## Current-source rejudge pilot — 2026-10-29

The 11 current-source equivalents for the ten crashed candidates and the one
budget-exceeded candidate were run in a completed but **nonqualifying** pilot.
The candidate map records current main `eb09b59dae80` and the old campaign
`a24590839fa6`; the Run-Gate summary is for commit `4bff48afc07d`. The pilot
recorded **8 KILLED, 2 SURVIVED, and 1 CRASHED**. R0 passed, but R2 ended
`ERROR/EXEC_FAILED`; Assay was imported from the source checkout, so the run
also has no judge provenance. Do not report this as a qualifying R2 pass.

The two survivors were `3c1b59b5b700` (`Or->And`, empty
`base_branch_ref`) and `ec9c79b40e7e` (`True->False`, fallback warning flush).
Commit `f84b1a9b4` adds the specific malformed-input diagnostic and stderr
flush assertions for those behaviors. It was committed after the pilot, so
neither candidate has a post-oracle mutation verdict yet.

The crashed candidate was `2e82d927b973` (`IsNot->Is`,
`run_gate.py:14295`, the `fallback_watch is not None` check). Its intended
stall test failed, but the mutation disabled fallback-watch polling and the
candidate run ended with two outliving descendants signalled during cleanup.
That remains a crash, not a kill. The test lacked a `child_process_scope`
teardown: without a local reaper, orphaned helpers were adopted only by
Assay's outer process-tree window and remained until candidate cleanup. The
test now reaps adopted children at its own boundary and asserts that the
process group was killed. The pilot record does not identify the descendants'
original spawn sites. The bounded scratch mutant probe and branch selftest are
recorded below; they do not replace the missing qualifying Assay candidate
verdict.

The former budget-exceeded candidate `183c6b7fe3d2` maps to
`377c0f5a5777` (`IsNot->Is`, `run_gate.py:14390`) and was **KILLED** with a
clean process tree. This closes RG-121 even though the overall pilot was
nonqualifying due to the separate RG-120 crash. Candidate map, summary,
progress, and run log are under
`.worktrees/r2-rejudge-source-20261029/run-gate-project/.assay/`.

`run-gate-project/assay.toml` has no per-candidate equivalence/exclusion list.
Its mutation block declares jobs, a time budget, a candidate limit, and the
operator set only. No exclusion was added; the documented equivalent list is
the evidence record.

## Survivors

Locations below refer to `run_gate.py` and `run_gate_admission.py` at the
campaign commit. “Existing coverage” names the closest pre-triage oracle;
“new oracle” names the behavioral assertion added on this branch. A test
name in the former column does not imply that it distinguishes the mutation.

| Candidate | Location, operator, mutation | Existing coverage → new oracle | Classification and reason |
|---|---|---|---|
| `1f2479871c89` | `run_gate.py:8406`; compare `>` → `>=` | `TestRecordedWorktreeBaseSchemaV6.test_a_newer_schema_is_refused_not_guessed_at` | **EQUIVALENT.** Equality is returned by the immediately preceding `version == CIU_BASE_BRANCH_SCHEMA_VERSION` branch, so this comparison is reached only for a strictly greater integer. |
| `c6bfe7fa2230` | `run_gate.py:10040`; compare `info.st_size > limit` → `>=` | `TestAssayCampaignCloseout.test_read_closeout_artifact_bounds_and_refuses_non_regular_files` → `test_read_closeout_artifact_accepts_a_file_at_the_size_limit` | **UNTESTED-BEHAVIOUR.** An artifact exactly at the byte limit is allowed; reject only a larger file. |
| `d468698cd03f` | `run_gate.py:10048`; compare `len(data) > limit` → `>=` | `test_read_closeout_artifact_survives_a_file_that_grows_or_fails_to_read` → `test_read_closeout_artifact_accepts_exact_limit_after_short_fstat` | **UNTESTED-BEHAVIOUR.** When `fstat` under-reports size, a read of exactly the limit is still valid. |
| `994709e2dddf` | `run_gate.py:10220`; boolean `or` → `and` | `test_missing_files_are_unavailable_never_an_empty_or_zero_closeout`, `test_an_empty_closeout_is_no_analysis_not_a_zero_campaign` → `test_an_empty_closeout_artifact_is_unavailable_independently` | **UNTESTED-BEHAVIOUR.** Either empty JSON or empty text makes the closeout unavailable, even when the other artifact is non-empty. |
| `1fb844d5ca99` | `run_gate.py:10270`; `flush=True` → `False` | Existing closeout status/report assertions → `test_complete_closeout_disclosure_flushes_its_stdout_line` | **UNTESTED-BEHAVIOUR.** A completed campaign disclosure must reach the invoking gate while the lane is running. |
| `47041b44314e` | `run_gate.py:10275`; `flush=True` → `False` | Existing closeout warning/content assertions → `test_closeout_warning_disclosure_flushes_its_stderr_line` | **UNTESTED-BEHAVIOUR.** A closeout warning must be delivered immediately on stderr. |
| `cc6dba2c64c5` | `run_gate.py:10300`; `flush=True` → `False` | Existing closeout exception containment assertions → `test_closeout_reporter_crash_warning_flushes_its_stderr_line` | **UNTESTED-BEHAVIOUR.** The fallback warning must also be delivered immediately when the reporter itself raises. |
| `db96173b75e9` | `run_gate.py:11658`; default `expects_plan=False` → `True` | `TestProgressWatch.test_make_progress_watch_carries_the_declared_rigor` | **EQUIVALENT.** Every production construction goes through `make_progress_watch`, which passes `expects_plan=lane_declares_r2(lane)` explicitly. The constructor default is not used by shipped call paths. |
| `a0294993cb89` | `run_gate.py:11680`; initial `self._command_active=False` → `True` | `TestProgressWatch` run-boundary, missing-file, and signal-state cases | **EQUIVALENT.** Every successful `_newest()` read overwrites the instance state from that file. On an unreadable file the early return cannot arm a stall before any signal or candidate has been seen; after a prior successful read, the instance values already came from the file. |
| `8002724fa167` | `run_gate.py:11681`; initial `self._baseline_finished=False` → `True` | `TestProgressWatch.test_auto_bound_covers_baseline_finished_before_plan`, `test_verdict_written_ends_the_baseline_bound_for_r0_r1_streams` | **EQUIVALENT.** Same state-refresh invariant as the preceding candidate: `_newest()` replaces this initial value on readable input, and an initial unreadable/no-event stream is disclosure-only. |
| `84a009fafd0b` | `run_gate.py:11722`; `_newest` local `command_active=False` → `True` | Existing baseline heartbeat coverage → `test_empty_or_torn_progress_does_not_arm_the_command_bound` | **UNTESTED-BEHAVIOUR.** A readable empty or half-written file has no observed event and must remain disclosure-only. Initializing `command_active` to true instead arms the generic 300-second bound and falsely stalls it. |
| `16e19017c8e2` | `run_gate.py:11723`; `_newest` local `baseline_finished=False` → `True` | Existing legacy-stream and auto-bound cases → `test_legacy_r2_candidate_keeps_the_minimum_auto_bound` | **UNTESTED-BEHAVIOUR.** A pre-header R2 stream with candidate progress but no observed baseline-finished event uses the ordinary 300-second bound. Initializing this flag true silently extends it to 600 seconds. |
| `3d505b871ab1` | `run_gate.py:11759`; `command_started` sets `baseline_finished=False` → `True` | `TestProgressWatch.test_auto_bound_watches_the_baseline_heartbeat_before_plan` | **EQUIVALENT.** The same event sets `command_active=True`, which prevents the post-command condition from arming until the command actually finishes. |
| `2d8ab75eee02` | `run_gate.py:11765`; `verdict_written` sets `command_active=False` → `True` | `test_verdict_written_ends_the_baseline_bound_for_r0_r1_streams` → `test_final_verdict_does_not_arm_the_r2_post_command_bound` | **UNTESTED-BEHAVIOUR.** A final verdict leaves no command or plan wait to bound. Mutating this flag arms the generic 300-second bound even though `baseline_finished` is false. |
| `727f127acc3c` | `run_gate.py:11766`; `verdict_written` sets `baseline_finished=False` → `True` | Existing R0/R1 final-verdict case → `test_final_verdict_does_not_arm_the_r2_post_command_bound` | **UNTESTED-BEHAVIOUR.** A final verdict ends the wait. Treating it as a baseline-finished signal can arm a spurious 600-second R2 plan wait after the verdict. |
| `0dabaf196ceb` | `run_gate.py:11769`; `plan` sets `command_active=False` → `True` | `TestProgressWatch.test_plan_supersedes_the_baseline_signal_and_uses_its_cadence` | **EQUIVALENT.** Once a plan has been seen, the post-command branch is disabled by `_plan_seen`; this flag does not affect the plan cadence or generic bound. |
| `a0be09854531` | `run_gate.py:11770`; `plan` sets `baseline_finished=False` → `True` | `TestProgressWatch.test_plan_supersedes_the_baseline_signal_and_uses_its_cadence` | **EQUIVALENT.** The plan branch is disabled by `_plan_seen` regardless of this flag; the plan cadence remains authoritative. |
| `08e6f0ecea7b` | `run_gate.py:11787`; `_baseline_finished and not _plan_seen` → `_baseline_finished or not _plan_seen` | Existing R2 baseline/plan cases → `test_final_verdict_does_not_arm_the_r2_post_command_bound`, `test_legacy_r2_candidate_keeps_the_minimum_auto_bound` | **UNTESTED-BEHAVIOUR.** The post-command 600-second bound requires all three states: baseline finished, no plan, and no active command. A candidate-only legacy stream must keep the ordinary 300-second bound. |
| `22e7ae0ed3d3` | `run_gate.py:12273`; disclosure `flush=True` → `False` | `TestProgressWatch.test_unproven_rigor_prints_one_disclosure_line` → `test_unproven_rigor_prints_one_flushed_disclosure_line` | **UNTESTED-BEHAVIOUR.** The operator needs the “R2 not proven” disclosure before waiting on the lane. |
| `bc4dc3566f41` | `run_gate.py:13880`; preserve-warning `or` → `and` | `TestExecLaneProfilingWiring.test_exec_watch_reattach_failure_keeps_lane_exit` | **UNTESTED-BEHAVIOUR.** A later reattach failure must not replace an earlier diagnostic warning; the existing test did not seed a warning. |
| `907e10493de3` | `run_gate.py:13886`; warning `flush=True` → `False` | `TestExecLaneProfilingWiring.test_exec_watch_reattach_failure_keeps_lane_exit` | **UNTESTED-BEHAVIOUR.** The reattach-failure warning must be visible while the command is still running. |
| `e1581086a2a9` | `run_gate.py:14217`; `make_progress_watch(..., auto_bound=True)` → `False` | `TestBareHostProfilingWiring.test_bare_host_rusage_fallback_stall_terminates_group_and_keeps_rusage` | **UNTESTED-BEHAVIOUR.** The bare-host R2 runner must ask the in-process watcher to enforce its automatic liveness bound. |
| `afaade07dddd` | `run_gate.py:14223`; in-process watcher announcement `flush=True` → `False` | `TestBareHostProfilingWiring.test_bare_host_rusage_fallback_stall_terminates_group_and_keeps_rusage` | **UNTESTED-BEHAVIOUR.** The fallback announcement must be visible before the runner waits. |
| `f5b899688d98` | `run_gate.py:14236`; fallback-poll warning `flush=True` → `False` | `TestBareHostProfilingWiring.test_bare_host_watch_failure_degrades_to_unwatched_rusage_wait` | **UNTESTED-BEHAVIOUR.** A watch failure is diagnostic only, but its warning must be delivered immediately. |
| `36ca828e3d90` | `run_gate.py:14277`; poll deadline `now >= next_progress_poll` → `now > next_progress_poll` | `test_bare_host_rusage_fallback_is_polled_at_the_progress_cadence` → `test_bare_host_rusage_polls_at_the_exact_next_poll_deadline` | **UNTESTED-BEHAVIOUR.** At the declared poll deadline the fallback watch must be polled. Strict `>` can skip an exact-deadline poll and defer liveness observation by one loop interval. |
| `6b7d73a66f4a` | `run_gate.py:14286`; `budget_expired=True` → `False` | Existing budget-deadline and rusage cases | **EQUIVALENT.** This assignment is consumed only after the loop. The loop reaches this branch only after `monotonic() >= budget_deadline`; the unconditional post-loop monotonic check sets the same flag before it is consumed. |
| `f8762451ef3c` | `run_gate.py:14332`; daemon reattach warning `flush=True` → `False` | `TestBareHostProfilingWiring.test_bare_host_idle_twice_announces_in_process_watch_exactly_once` | **UNTESTED-BEHAVIOUR.** The first idle/ended-watch warning must reach the operator before the reattach attempt. |
| `5812149c6c7c` | `run_gate.py:14340`; preserve-warning `or` → `and` | `TestBareHostProfilingWiring.test_bare_host_reattach_failure_keeps_lane_exit_via_fallback` | **UNTESTED-BEHAVIOUR.** A failed reattach must preserve the first warning instead of overwriting it; the prior oracle started with no warning. |
| `b3540080883c` | `run_gate.py:14347`; reattach-failure warning `flush=True` → `False` | `TestBareHostProfilingWiring.test_bare_host_reattach_failure_keeps_lane_exit_via_fallback` | **UNTESTED-BEHAVIOUR.** The failure warning must be delivered before switching to fallback liveness. |
| `5ecfdb042aa5` | `run_gate.py:14360`; fallback announcement `flush=True` → `False` | `TestBareHostProfilingWiring.test_bare_host_idle_twice_announces_in_process_watch_exactly_once` | **UNTESTED-BEHAVIOUR.** The fallback announcement must be visible when the daemon watch has been abandoned. |
| `e9e6ee12247c` | `run_gate.py:14368`; daemon-unavailable warning `flush=True` → `False` | `TestBareHostProfilingWiring.test_bare_host_idle_twice_announces_in_process_watch_exactly_once` | **UNTESTED-BEHAVIOUR.** The warning must be delivered when the runner falls back to in-process liveness. |
| `0c8e6d6d0e5f` | `run_gate.py:16074`; pre-await interruption disclosure `flush=True` → `False` | `test_an_interruption_before_the_wait_keeps_the_container_and_says_so` | **UNTESTED-BEHAVIOUR.** This line tells the operator a detached container is still running and needs adoption or removal. |
| `8d13668ba003` | `run_gate.py:16131`; closed-result interruption warning `flush=True` → `False` | `TestLaneDeadlineEnforcement.test_main_converts_dependency_system_exit_to_closed_error` | **UNTESTED-BEHAVIOUR.** The final interruption status must be flushed before returning exit 2. |
| `21d74556716c` | `run_gate_admission.py:311`; race returns `[]` → `None` | `test_containers_are_empty_when_every_listed_id_vanished` → `test_containers_return_an_empty_list_when_inspect_race_has_no_json` | **UNTESTED-BEHAVIOUR.** A raced-away inspect with no surviving JSON still returns the list type promised by `_containers`. |

The EQUIVALENT dispositions are limited to the precise contexts above; no
operator-wide or nearby behavioral change is declared equivalent.

## Crashed candidates — HARNESS

Each row's `tests_completed` is Assay's progress count, not proof that the
listed tests passed. No candidate in this table has a valid final test verdict.
The six `leader_status=exited` runs had one child still alive after the suite
leader exited; cleanup signalled that child but recorded it as outliving. The
four `leader_status=signaled` runs each incremented both cgroup-v2
`memory.events.oom` and `oom_kill` by one under the R2 lane's 2 GiB memory cap.

| Candidate | Location, operator, mutation | Relevant suite coverage and run evidence | Classification |
|---|---|---|---|
| `682672b18035` | `run_gate.py:8525`; boolop `or` → `and` | `TestRecordedWorktreeBaseSchemaV6.test_malformed_or_unsafe_base_branch_ref_is_refused`; progress line 2475, 2,428 tests completed; leader exited with 1 outliving child | **HARNESS.** Process-tree protocol rejected the run as unclean; the candidate was not scored. |
| `7547a1ceebfb` | `run_gate.py:12450`; boolop `and` → `or` | `TestExecLaneProfilingWiring.test_exec_initial_watch_failure_announces_progress_fallback`; progress line 5016, 1,392 tests completed; leader exited with 1 outliving child | **HARNESS.** No valid test result after process-tree cleanup found a child still alive. |
| `803fcbd66db7` | `run_gate.py:12450`; compare `is not` → `is` | Same exec-watch fallback coverage; progress line 5017, 1,392 tests completed; leader exited with 1 outliving child | **HARNESS.** The run is unclean and cannot establish kill or survival. |
| `9a3474bead1f` | `run_gate.py:13911`; print `flush=True` → `False` | `TestExecLaneProfilingWiring.test_exec_progress_fallback_failure_keeps_lane_exit`; progress line 5023, 2,428 tests completed; leader exited with 1 outliving child | **HARNESS.** The process-tree result takes precedence over an apparent suite completion. |
| `d66ca73db681` | `run_gate.py:14227`; compare `is` → `is not` | Bare-host rusage/fallback watcher cases; progress line 5030, 1,849 tests completed, peak RSS 1,899,663,360 bytes; `oom` and `oom_kill` each increased by 1 | **HARNESS.** The 2 GiB cgroup killed the process; no test verdict is attributable. |
| `42641e6c6553` | `run_gate.py:14276`; compare `is` → `is not` | `TestBareHostProfilingWiring` rusage cadence/fallback cases; progress line 5034, 1,849 tests completed, peak RSS 1,892,900,864 bytes; `oom` and `oom_kill` each increased by 1 | **HARNESS.** The cgroup OOM invalidated the candidate run. |
| `83f25251146d` | `run_gate.py:14276`; compare `is not` → `is` | Same bare-host fallback coverage; progress line 5036, 1,849 tests completed, peak RSS 1,815,023,616 bytes; `oom` and `oom_kill` each increased by 1 | **HARNESS.** The candidate did not finish with a valid test outcome. |
| `26100d0e2d15` | `run_gate.py:14283`; boolop `or` → `and` | `TestBareHostProfilingWiring` rusage deadline/fallback cases; progress line 5040, 1,849 tests completed, peak RSS 1,936,281,600 bytes; `oom` and `oom_kill` each increased by 1 | **HARNESS.** The cgroup OOM invalidated the candidate run. |
| `b573c49b8c39` | `run_gate.py:14285`; compare `is` → `is not` | Bare-host rusage budget and fallback cases; progress line 5044, 1,850 tests completed; leader exited with 1 outliving child | **HARNESS.** The run failed Assay's process-tree cleanliness requirement. |
| `dfec68964a8c` | `run_gate.py:14357`; compare `is not` → `is` | `TestBareHostProfilingWiring.test_bare_host_idle_twice_announces_in_process_watch_exactly_once`; progress line 5057, 1,856 tests completed; leader exited with 1 outliving child | **HARNESS.** The candidate did not produce a valid test verdict. |

## Budget-exceeded candidate — HARNESS

| Candidate | Location, operator, mutation | Relevant suite coverage and run evidence | Classification |
|---|---|---|---|
| `183c6b7fe3d2` | `run_gate.py:14371`; compare `is not` → `is` | `TestBareHostProfilingWiring` fallback-watch cases; progress line 7504, 1,852 tests completed, 905.433 s elapsed / 843.05 CPU s, `decision=configured-budget-expired`, `candidate_session_finish_seen=false`, no OOM or outliving-child evidence | **HARNESS.** The configured 900-second candidate bound expired before a result was recorded. Whether the mutation contributed to the long run cannot be determined from this trace, so it is not a kill or survival. |

## Why the lane verdict is ERROR/EXEC_FAILED

This is the expected R2 mapping, not a mutation-score verdict bug. Assay's
`judge_mutation` checks non-empty `crashed` first and maps it to
`ERROR/EXEC_FAILED`; only if there are no crashed candidates does it check
`budget_exceeded` and map that to `BUDGET_EXCEEDED/LANE_TIMEOUT`. Survivors
are considered after incomplete buckets (with Assay's separate `hung` bucket
also classified as a budget-exceeded outcome). The precedence and the
simultaneous-bucket case are pinned in
`assay/src/assay/mutation.py:5791-5809,5891-5906` and
`assay/tests/core/test_mutation_judge.py:test_crashed_outranks_survived_and_budget_exceeded_when_all_three_are_present`.

Here the campaign accounting/analysis completed with no blockers, but the
mutation payload contains ten `crashed` entries. Those incomplete candidate
runs take precedence over the one budget expiry and 34 survivors, so Assay
correctly reports `ERROR/EXEC_FAILED` (exit 2). “Campaign analysis complete”
means the analysis accounted for all candidate records; it does not mean every
candidate produced a valid test judgment or that the lane passed.

## Follow-up fixes on `rg-r2-harness-fix-20261029`

Code inspection confirmed the `docker logs -f` spawn in `await_container` was
outside its cleanup guard, and `follow_container` did not clean up its waiter
if starting the log follower failed. Both paths now reap their helper
processes on exceptional exits. The audit also found the same narrow window in
`spawn_bare_host_placement_barrier` between `Popen` and its readiness guard;
that path now aborts and reaps the wrapper if post-spawn setup fails.

The two-client test now uses `child_process_scope`. The rusage fake fails after
16 `wait4` polls, and the two fallback-watch fakes fail after eight process
waits, so inverted termination conditions surface as bounded assertion
failures instead of unbounded memory growth or a 900 s spin. The existing
crashed bucket remains authoritative for an unclean candidate; no cleanup
failure is reclassified as a kill.

The fixes above do not rewrite the historical a245908 campaign records. The
current-source pilot supplies candidate-level outcomes but is not a qualifying
R2 result. RG-121 is closed by its clean killed candidate rejudge; RG-120 stays
open for the two survivors whose new oracles have not been mutation-rejudged
and the one candidate whose Assay run was crashed by process-tree cleanup. No
R2 campaign was run in this change.

The registered selftest evidence is:

1. Red regression run: 3 expected failures, 2,441 passed, one skipped. The
   failures were the follower waiter leak, the log-watch startup leak, and the
   bare-host placement-barrier post-spawn leak.
2. First follow-up attempt: 2,443 passed, two test-double failures, one skipped.
   The Popen stubs also intercepted `subprocess.run` cleanup; they were
   narrowed to `docker logs`.
3. The next attempt: 2,445 passed, one skipped in 199.70 seconds. Diff coverage
   failed at 45/50 changed executable lines (90%; branches 20/20), identifying
   the placement-barrier close-error handler and follower timeout escalation.
   Targeted coverage was added after this run.
4. Controller-verified final tree: registered lane `selftest` passed on clean
   commit `6650bd3f58f2be4e3ec40ba5315c74fac2e5d805` (2026-10-29; run
   `36c16001a136fad42be6e751e0473ba3`, log
   `/home/vscode/vbpub-release-program-20261027/logs/g-rg.log`): 2,446 passed,
   one skipped in 217.95 seconds; diff coverage 48/48 changed executable lines
   (100.0%) and 20/20 branches; exit 0. The earlier 45/50 result is superseded
   by this green run. No R2 campaign was run for this change.
5. Scratch mutant probe: in a detached copy of `f84b1a9b4`, changed the
   rusage fallback-watch check from `fallback_watch is not None` to
   `fallback_watch is None`, then ran the registered `run-gate selftest
   --allow-dirty`. It failed as expected: 4 failed, 2,442 passed, one skipped
   in 228.63 seconds, exit 1. The stall-termination oracle and the three
   cadence/watch-failure oracles all failed because the mutant never polled
   the watcher. The failing stall test's `child_process_scope` teardown
   completed without error, so no adopted children remained for outer runner
   cleanup. This is a bounded mutant probe, not an Assay R2 verdict.
6. Oracle branch verification: `run-gate selftest --allow-dirty` passed once
   on `rg120-rg121-rejudge-oracles-20261029` (Run-Gate 24.1.2; exit 0): 2,446
   passed, one skipped in 203.48 seconds. Diff coverage was `SKIPPED` because
   there were zero changed executable lines under `run_gate.py`; the registered
   lane passed with that empty source diff.
