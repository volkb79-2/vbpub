# B118 implementation report — bounded pilot pending

## Current integration status — 2026-10-10

P7a/P7b/P7c are integrated in local `main`; the final integrated
`tester-unified` gate passed on `b8713d5672c92ff323a832ae8de65d567493cf2e`
and B114's MDT `smoke` passed (**119 passed, 6 skipped, 6 subtests**). The
B105 preflight refused before launching its child at **1,280,720,896
bytes** on `b8713d56`. A fresh retry on documentation-only descendant
`595b4069` again refused before launch with **1,375,588,352 bytes** of headroom
against the 2 GiB requirement
(`/tmp/run-gate/lanes/self-qualification-preflight/7fef021b8aab38ee42bc9317471f9cad.log`).
No B110 pilot or B105 R2 campaign ran. Retry after capacity returns; preserve
all `.assay` state and do not treat the gate pass as pilot or qualification
evidence.

## Controller integration follow-up — interrupted resume checker (2026-10-10)

The B131 integration's third exact-tree review found two B110 report-checker
refusals after valid interrupted resumes: a resumed attempt may end before
`resume_merged`, and concurrent workers may persist a sparse set of candidate
indexes before interruption. The checker now allows both forms only when the
prior segment has no completed `end`; a completed segment still needs the
merge marker and full pending-index coverage. A three-attempt regression uses
producer-shaped progress and verifies that the final resumed report is
accepted. The combined B131/B110 focused suite passes 341 tests and the docs
contract passes 56. Final exact-tree review, same-tip gates, and the B105
bounded pilot remain pending.

**Base:** `3589a57f1cf462f82aab0683a05dc0bda6be2c3c`  
**P7a branch:** `assay-b118-p7-pilot`  
**P7a commit:** `7b7143d68`

**P7b branch:** `assay-b110-p7b-gate-modes`

**P7b base:** `d3821b5fab3163abfea15e8b54be9245fc6d3e5a`

**P7b commit:** `f0814096c`
**P7b merge to main:** `cbd605445`

**P7c base:** `5bf6db82bdd61a5b9a46b6035ee4c76de944e87d`
**P7c worktree:** `assay-b118-p7c-artifact-attestation-20261009`

## Status

**Current integration summary (2026-10-09 21:41 UTC):** P7a and P7b are
merged. Their prior registered `tester-unified` gate passed at `5bf6db82`
(`/tmp/run-gate/lanes/tester-unified/16a4517e7b7f0817e13e48b4e9959690.log`,
`ASSAY_GATE_CONTAINER_EXIT=0`, `ASSAY_REGISTERED_GATE_COMPLETE=1`), before P7c.
The P7c diff now includes the round-29 repairs: the report checker consumes the
inherited `.assay` directory descriptor and writes logical `.assay/...`
manifest names; campaign initialization receives the pinned deadline path;
gate logs use no-follow descriptor-relative creation and reading; and final
snapshot/archive checks revalidate path identity and child permissions.

Validation after these fixes: the B110 container-test module passed **100
tests in 154.61 seconds**; `tests/core/test_pilot_candidates_file.py` passed 44;
the post-helper B110 shell/report-check selection passed 9. The earlier
combined self-lane/report-check run passed 111 before the safe log-reader
regression was added. Shell syntax, Python compilation, and `git diff --check`
pass. A fresh exact-diff review and same-tip registered gates remain pending.

The resumed RG-89 campaign ended with an R0 failure in
`tests/test_serve_socket_carrier.py::test_each_watch_line_shape_is_frozen_on_its_own`
before any mutation candidate ran; it is not B118 evidence. No registered gate
is currently active. The last B105 preflight attempt refused before container launch at
1,321,574,400 bytes of free dev-gates RAM against its 2 GiB admission
requirement (`/tmp/run-gate/lanes/self-qualification-preflight/7e06a9ce7e8604ff6200d95867176ffd.log`;
no preflight success marker). No B110 pilot or new B105 R2 run is claimed.

The review history and implementation notes below preserve earlier decisions;
their pending statements are superseded by this summary and the latest P7c
controller follow-up below.

P7a is implemented on its isolated worktree. Round 6 rejected an incomplete
budget-cache record and source/scanner binding gaps; those code findings and
the older unsupported log-marker claims were fixed. Round 7 returned
**ACCEPT WITH FIXES**. Round 8 returned **REJECT** because the selector did not
bind the plan's commit/tree to the current clean checkout and three report
details were inaccurate. The selector now checks exact Git identity and a
clean worktree, reports plan provenance, and has stale-plan/dirty-tree tests.
The impossible summary example, preflight timeout bound, and this report's
review status are corrected. Round 9 returned **REJECT** because output paths
could alias and overwrite the plan, and because the P7b screen contract could
mistake an R0 failure for a completed screen. The selector now refuses all
resolved path collisions; P7b now requires a verified report bound to the
current plan, successful R0/R1, and a complete R2 inventory. The round-9
reviewer ran no tests or gates. P7b implementation started on the branch above
on 2026-10-09. Its first Sol xhigh review found that screen verification errors
could be reported as complete, the verdict exit was not compared with the
producer status, stale verdicts survived plan failures, the 90-minute pilot cap
was missing, and the positive checker fixture did not pass `assay verify`.
Those findings were addressed in code, tests and docs. A second Sol xhigh
review found three remaining issues: plan/selection time escaped the
90-minute invocation cap, launcher admission could leave an old screen verdict
advertised, and the build allowance was described as measured before a
registered gate had measured it. The current diff applies all three fixes:
campaign init, planning, selection and Assay execution share the 90-minute cap;
the screen launcher clears stale verdicts before host admission; and the
10-minute build allowance is marked planned until measured by the gate report.
The later review-5 follow-up and its corrections are recorded below.

The updated P7b focused suite passed 93 tests
(`/tmp/assay-p7b-focused-r4.log`), the docs contract suite passed 55
(`/tmp/assay-p7b-docs-r3.log`), and shell syntax, Python compilation,
`git diff --check` and `./run-gate.py --list` passed. The registered P7b gate and
`self-qualification-preflight`
have not run. At 2026-10-09 06:05 UTC, a separate registered RG-89 R2 campaign
was active in `.worktrees/rg89-p1-r2-20261009` at 294/1,386 candidates; keep
all registered gates serial until that run ends. No P7b gate evidence is
claimed here.

## Controller follow-up — 2026-10-09

A later read-only Sol xhigh review (`/tmp/assay-p7b-review3.md`) found four
additional P7b defects: Assay execution could run 120 seconds beyond the
90-minute pilot cap; screen cleanup happened before cross-caller lock admission
and the shared-host refusal could leave an old verdict; production dispatch
could mask `assay plan` failure inside `run_b110_screen || exit $?`; and an
early pilot refusal could leave prior attempt artifacts at registered paths.
The fixes now use the remaining hard cap and enforce it again after Assay
returns, clear screen/pilot outputs only after acquiring the Git-common-dir
lock, preserve the actual planning failure status, and clear pilot attempt
outputs before host/Docker admission while preserving deadline, state and
progress. README, DESIGN-GUIDE, CONSUMERS, run-gate comments and the P7 brief
now describe the hard cap and measured-versus-planned timing consistently.

The final focused P7b suite passed 98 tests
(`/tmp/assay-p7b-focused-final-r3.log`, `TEST_EXIT=0`); the docs contract suite
passed 55 (`/tmp/assay-p7b-docs-final.log`, `TEST_EXIT=0`). Shell syntax,
Python compilation, `git diff --check` and `./run-gate.py --list` passed after
the changes. The prior RG-89 R2 campaign is no longer active; it ended with
`ERROR/EXEC_FAILED` at 327/1,386 because the required cgroup observer could not
read peer thread status, so it is not valid R2 evidence. P7b still needs a
fresh independent review and registered `tester-unified` acceptance; no
registered P7b gate result is claimed yet.

## Controller follow-up after review 4 — 2026-10-09

The final read-only Sol xhigh review (`/tmp/assay-p7b-review-final.md`) found
four more issues. The launcher now acquires its Git-common-dir lock and clears
the current B110 attempt outputs before source-cleanliness and Docker checks;
cleanup still preserves the pilot state directory, deadline and progress
stream. Every screen completion write now fails the function on an output
error, including the optional reuse marker. The manual consumer example checks
the same 90-minute boundary after Assay returns, and the P7 brief no longer
shows the removed 120-second grace. A behavioral harness now runs a successful
Assay status 6 with an exhausted post-run cap and proves the gate emits timeout
instead of completion. The container tests exercise stale-artifact cleanup
when the Docker executable is unavailable and preserve pilot state under
refusal.

After these corrections, the focused B110/P7b and documentation tests passed
201 tests (`/tmp/assay-p7b-focused-final-r5.log`, `TEST_EXIT=0`), including the
55-test docs contract suite. The checkout has not yet had a second independent
review or registered gate run; no merge is claimed.

## Controller follow-up after review 5 — 2026-10-09

The independent Sol xhigh review (`/tmp/assay-p7b-review-final2.md`) found four
remaining issues. The screen checker now refuses verifier-valid R2 `ERROR`,
`BUDGET_EXCEEDED` and `INCONCLUSIVE` outcomes while still accepting complete
`PASS` and survivor `FAIL` results; a regression fixture proves an
`ERROR/EXEC_FAILED` crashed-mutant verdict passes `assay verify` but emits no
screen marker. The pilot now checks the persisted campaign deadline after
Assay returns as well as the invocation cap, and both late-expiry axes have a
behavioral status-6 oracle. Screen launcher cleanup now removes the old plan,
verdict and run log under the shared lock while preserving resume state and
progress. The P7 brief replaces stale executable bodies with the current
bounded sequencing and marker contract.

The final focused P7b suite passed 203 tests in 54.70 seconds
(`/tmp/assay-p7b-focused-review5-final.log`, `TEST_EXIT=0`). Shell syntax,
Python compilation, `git diff --check` and `./run-gate.py --list` also passed.
A fresh independent review and registered P7b gates remain pending; no merge
or pilot result is claimed.

## Test traceability

| Scope and actual test names | Earlier outcome | Recorded result |
|---|---:|---:|
| P7a behavior tests in `tests/core/test_pilot_candidates_file.py`: `test_T1_candidate_file_grammar_refuses_before_progress_header`; `test_T2_pilot_flag_conflicts_refuse_before_execution`; `test_T3_T5_single_killed_candidate_is_completed_and_persisted`; `test_T4_unknown_id_refuses_after_baseline_without_a_candidate_event`; `test_T6_pilot_skips_r3_canary`; `test_T7_selection_digest_uses_plan_order`; `test_T8_killed_and_surviving_buckets_still_exit_six`; `test_T9_resume_reuses_the_selected_state_record`; `test_T10_sentinel_rejects_a_different_selection_without_running`; `test_T10_malformed_sentinel_is_refused_without_progress`; `test_T10_mutation_discovery_refusal_keeps_r2_reason_and_pilot_terminal`; `test_T10d_nonpilot_records_cannot_seed_a_pilot_state_dir`; `test_T10e_qualifying_run_cannot_reuse_pilot_state`; `test_T10f_qualifying_resume_checks_the_implicit_default_state_root`; `test_T10_malformed_or_wrong_lane_sentinels_are_refused`; `test_T11_head_timeout_still_prints_pilot_summary_and_terminal_event`; `test_T12_one_verdict_write_site_remains_inside_finish`; `test_T13_campaign_digest_binds_full_plan_before_pilot_selection`; `test_T14_noncompleted_pass_is_explicitly_reported_as_error`; `test_T15_invalid_budget_record_keeps_pilot_incomplete`; `test_pilot_state_sentinel_creation_and_same_selection_resume`; `test_pilot_state_refuses_existing_record_without_sentinel`; `test_state_store_lock_is_held_through_reserved_run` (qualifying-resume and pilot parameters); `test_state_store_lock_is_nonblocking_and_survives_directory_replacement`; `test_replaced_state_directory_refuses_run_certification`; `test_consumer_pilot_example_binds_the_full_plan_and_uses_cold_witness`; `test_pilot_budget_record_rejects_a_relabelled_killed_record`; `test_real_candidate_timeout_record_is_valid_but_not_a_completed_pilot` | Initial focused run: 43 passed, 1 failed because `Mapping` was not imported by the new budget-record check | Combined focused run: 45 passed in 40.86 s; `/tmp/b118-p7a-focused.log` has a pytest summary but did not retain the shell exit marker; superseded by the later verified run below |
| Selector tests in `tests/core/test_b110_pilot_select.py`: `test_S1_S7_selector_is_deterministic_and_hashes_plan_order`; `test_selector_rejects_control_characters_in_seed_before_writing`; `test_selector_rejects_seed_over_bound_before_writing`; `test_selector_refuses_output_larger_than_assay_candidate_input_limit`; `test_S8_selector_refuses_size_below_per_file_plus_missing_operator`; `test_S3_selector_fills_exact_size_and_reports_all_rows_for_smaller_plans`; `test_S2_selector_adds_an_operator_that_loses_the_per_file_pick`; `test_S4_selector_refuses_scanner_span_that_does_not_point_at_equality`; `test_S4_selector_refuses_duplicate_scanner_site_matches`; `test_S4_selector_refuses_hard_site_inside_differently_named_nested_function`; `test_S5_selector_uses_utf8_byte_spans_and_excludes_function_bodies`; `test_S6_selector_rejects_noncanonical_plan_documents`; `test_S4_S5_selector_refuses_missing_known_hard_import_time_sites`; `test_selector_accepts_real_assay_plan_and_loader_accepts_its_output`; `test_selector_rejects_empty_plan` | Included in the initial focused run | Included in the combined focused run above: 45 passed total |
| Selector identity and path tests: `test_selector_rejects_plan_from_another_source_checkout`; `test_selector_rejects_stale_plan_when_unlisted_tree_content_changes`; `test_selector_rejects_commit_or_tree_mismatch` (two cases); `test_selector_rejects_missing_or_malformed_plan_identity` (two cases); `test_selector_rejects_plan_when_worktree_is_dirty`; `test_selector_refuses_output_path_that_aliases_plan` (four direct/symlink cases) | 0 failures | Passed in `/tmp/b118-p7a-focused-r15.log` |
| Round-11 state-store and selector regressions: parent replacement remains locked; independent clones contend on a shared external store; mutation records stay on the admitted directory descriptor; replaced parent refuses certification; ambient `GIT_DIR`/`GIT_WORK_TREE` cannot redirect identity; selector output cannot target a planned source (direct or symlink) | First rerun found only a fixture setup error; corrected rerun green | 70 passed in 47.26 s; `/tmp/assay-b118-round12-focused-r3.log`, `TEST_EXIT=0` |
| Seven-file regression: both P7a test modules, `tests/core/test_cli_run.py`, `tests/core/test_cli_run_failure_summary.py`, `tests/core/test_mutation_resume_sharding.py`, `tests/core/test_state_dir_resume.py`, and `tests/core/test_campaign_deadline.py` | 0 failures | 174 passed in 61.57 s; `/tmp/b118-p7a-regression-final.log` has a pytest summary but did not retain the shell exit marker; superseded by the later verified run below |
| Combined selector, pilot CLI, resume, timeout, and docs regression: the seven files above plus `tests/core/test_docs_examples_and_vocabulary.py` | 0 failures | 244 passed in 61.26 s; `/tmp/b118-p7a-regression-r15.log`, `PYTEST_EXIT=0` |

The focused test command was
`nice -n 19 ionice -c3 python -m pytest tests/core/test_pilot_candidates_file.py tests/core/test_b110_pilot_select.py -q -p no:cacheprovider`.
The regression command used the seven files listed above, also under
`nice -n 19 ionice -c3`, with `-q -p no:cacheprovider`.

**Controller reruns after review round 6:** the focused P7a files passed 50
tests in 61.29 s (`/tmp/b118-p7a-focused-r9.log`, `PYTEST_EXIT=0`). The
seven-file regression passed 179 tests in 70.64 s
(`/tmp/b118-p7a-regression-r10.log`, `PYTEST_EXIT=0`). The new behavioral
oracles are `test_resume_reexecutes_incomplete_budget_record` (three missing
field cases), `test_selector_rejects_plan_from_another_source_checkout`, and
`test_S4_selector_refuses_scanner_span_on_a_different_source_line`. The
additional B117 fixture oracle `test_default_runner_timeout_kills_and_reaps_its_inherited_pipe_group`
passed separately in 0.46 s (`/tmp/b118-b117-timeout-oracle.log`,
`PYTEST_EXIT=0`). The first updated regression run had one fixture race; that
oracle now waits for a complete JSON record and passed in the seven-file run.
The documentation contract suite passed 55 tests in 0.38 s
(`/tmp/b118-p7a-docs-r1.log`, `PYTEST_EXIT=0`).

**Controller rerun after round 8:** the selector module and updated consumer
example passed 23 tests in 34.11 s (`/tmp/b118-p7a-focused-r12.log`,
`PYTEST_EXIT=0`). The first attempt (`r11`) exposed that Git `rev-parse --verify`
accepts one revision per call; the identity reader now queries commit and tree
separately. The green run includes the stale-plan case where only an unlisted
plan input changed in a new commit, a dirty-worktree refusal, the current-plan
integration fixture, and report checks for commit/tree, raw plan digest,
selected IDs, and candidate-file digest.

After adding explicit malformed/missing identity and independent commit/tree
mismatch cases, the focused suite passed 27 tests in 39.57 s
(`/tmp/b118-p7a-focused-r13.log`, `PYTEST_EXIT=0`). The combined selector,
pilot CLI, resume, timeout, and documentation regression passed 240 tests in
72.96 s (`/tmp/b118-p7a-regression-r13.log`, `PYTEST_EXIT=0`).

**Controller rerun after round 9:** the selector module and consumer example
passed 29 tests in 36.01 s (`/tmp/b118-p7a-focused-r14.log`,
`PYTEST_EXIT=0`), including both output-to-plan alias cases. The combined
selector, pilot CLI, resume, timeout, and docs regression passed 242 tests in
63.69 s (`/tmp/b118-p7a-regression-r14.log`, `PYTEST_EXIT=0`).

After adding direct and symlink output-to-plan alias cases, the selector module
and consumer example passed 31 tests in 36.21 s
(`/tmp/b118-p7a-focused-r15.log`, `PYTEST_EXIT=0`). The combined regression
passed 244 tests in 61.26 s (`/tmp/b118-p7a-regression-r15.log`,
`PYTEST_EXIT=0`).

Round 10 initially exposed that a sibling lock file made an otherwise ignored
custom state directory appear as an uncommitted source change: 3 failures and
243 passes in 64.17 s (`/tmp/assay-b118-p7a-regression-r16.log`,
`PYTEST_EXIT=1`). The lock now lives under Git's shared common directory,
outside the judged tree, and is keyed by the state path's parent-directory
device/inode and final name. The lock is nonblocking; replacing the state
directory does not split it, and the run refuses certification if the path
does not name the admitted inode. Added bounded contention and replacement
tests, plus an end-to-end refusal test. The corrected combined regression
passed 246 tests in 62.41 s (`/tmp/assay-b118-p7a-regression-r17.log`,
`PYTEST_EXIT=0`).

## Review

The first post-implementation review is recorded at
`/tmp/assay-b118-p7a-review-round4.md`. Its budget-record, state-store race,
oversized worker-count, unknown-ID assertion, and B110 consumer-example
findings have been addressed in code, tests, and the P7 contract.

A post-fix Sol xhigh review was started as `/tmp/assay-b118-p7a-review-round5`
with `CODEX_HOME=$HOME/.codex2` and the required read-only invocation. It exited
1 after the CLI reported a usage limit, before producing a review report. The
pre/post HEADs both equal `3589a57f1cf462f82aab0683a05dc0bda6be2c3c`, and the
pre/post status snapshots match. This attempt has no review verdict; retry it
after the reported usage reset.

The round-6 Sol xhigh review ran from `.codex2`, read-only, with identical
pre/post HEAD (`3589a57f1cf462f82aab0683a05dc0bda6be2c3c`) and status snapshots.
Its report is `/tmp/assay-b118-p7a-review-round6.md`; it returned REJECT. It
found an incomplete budget-cache record could suppress retry, the selector did
not bind plan rows to source hashes or scanner spans to the claimed line, and
the previous report overstated exit markers in its cited logs. The current
changes reject and re-execute incomplete budget records, verify the mutated
file digest, bind source hashes and scanner spans, and retain explicit status
markers in the newer logs. Round-7 Sol xhigh review returned ACCEPT WITH FIXES;
its timeout-contract and historical-log findings are corrected. Round 8 ran
read-only from `.codex2`, returned REJECT, and left identical HEAD and status
snapshots at `3589a57f1cf462f82aab0683a05dc0bda6be2c3c`; its report is
`/tmp/assay-b118-p7a-review-round8.md`. The selector now rejects plan commit or
tree mismatches and dirty worktrees, and records plan identity/digest and
selected IDs. The B110 summary example has internally consistent survivor
counts; the preflight bound and report status are corrected.

Round 9 ran read-only from `.codex2`, returned REJECT, and left identical HEAD
and status snapshots at `3589a57f1cf462f82aab0683a05dc0bda6be2c3c`; its report
is `/tmp/assay-b118-p7a-review-round9.md`. The selector now refuses resolved
output paths that alias the plan. The P7b screen contract requires
`assay verify` plus a report checker that binds commit/tree and complete R2
inventory to the current plan, and refuses R0/R1 failure or lane timeout. The
Round 10 ran read-only from `.codex2`, returned REJECT, and left identical HEAD
and status snapshots at `3589a57f1cf462f82aab0683a05dc0bda6be2c3c`; its report is
`/tmp/assay-b118-p7a-review-round10.md`. It found four issues: stale screen
verdict reuse, directory-inode locking that could be split by path replacement,
unbounded lock waiting, and a P7b static oracle that rejected the quoted
verifier command prescribed by its own sample. P7a now uses a nonblocking
path-stable lock outside the judged tree and refuses certification after a
directory replacement. The P7b brief removes the prior output before every
screen run and requires tests proving the quoted verifier executes before the
report checker, including a stale-verdict/early-failure case. The first
post-fix regression found the lock-file dirty-tree issue described above; the
corrected 246-test run is green.

Round 11 ran read-only from `.codex2`, returned REJECT, and left identical
HEAD/status snapshots at `3589a57f1cf462f82aab0683a05dc0bda6be2c3c`; report:
`/tmp/assay-b118-p7a-review-round11.md`. It found four remaining issues:
parent-directory replacement could split the lock, independent clones could
write a shared external store concurrently, ambient Git variables could make
the selector inspect a different checkout, and selector outputs could target
planned source files. The implementation now uses path and parent-inode locks
in shared roots, writes mutation and sentinel records through the admitted
directory descriptor, isolates Git identity commands from `GIT_*` variables,
and rejects outputs that resolve to any planned source. New regression tests
cover each behavior; the focused run passed 70 tests. A fresh round-12 review
and the registered gate remain pending. The gate still requires a passing host
cgroup reserve check.

## Files touched so far

- `CHANGES.md`
- `README.md`
- `docs/CONSUMERS.md`
- `docs/DESIGN-GUIDE.md`
- `nyxloom-trove/reports/b110/P7-pilot-tooling.md`
- `nyxloom-trove/reports/assay-B118-REPORT.md`
- `src/assay/cli.py`
- `src/assay/mutation.py`
- `src/assay/runner.py`
- `tests/core/test_b110_pilot_select.py`
- `tests/core/test_pilot_candidates_file.py`
- `tests/core/test_campaign_deadline.py` (make the B117 child-process fixture wait for a complete JSON record)
- `tools/b110_pilot_select.py`

## Controller sequence update (2026-10-08)

The P8/W9 campaign-analysis phase 1 was already merged and gate-qualified on
main; its controller handoff records both `tester-unified` and
`self-qualification-preflight` green before release v7.2.0. B108 phase 2
remains open. The B118 P7b brief now matches the current `bare-host` run-gate
topology, routes through `self-qualification-container.sh`, checks the
verified cgroup-visible tester-unified child, and includes `--resume` on the
pilot invocation.

B131 remains later in the sequence. Its backlog entry does not set an R2
acceptance threshold, a time budget, or release-gate status. Do not reuse the
B105 pilot's 3,760-candidate count or GO thresholds for the analysis package;
measure B131's actual plan and a bounded analysis-package pilot before setting
its acceptance policy.

## Controller sequence update (2026-10-09)

Round 16 Sol xhigh review returned REJECT because concurrent selectors could
interleave candidate and report replacement. P7a now takes nonblocking locks
for both normalized requested paths and pinned parent-inode/name identities,
holding them through paired publication. The lock files are in a private
per-user temp directory. README, DESIGN-GUIDE, CONSUMERS, and the P7 contract
describe the conflict refusal and the paired-publication oracle was added.

The focused selector suite passed 42 tests in 32.57 s. The six-file P7a
regression passed 374 tests in 61.10 s. After moving each newly opened lock fd
into the cleanup set before calling `flock` (so an unexpected lock syscall
error also closes that descriptor), the concurrent-publication test passed
again in 1.96 s. `git diff --check` passed before the last cleanup-only move.

Round 17 was invoked read-only with `CODEX_HOME=$HOME/.codex2`, GPT-6-Sol
xhigh, and the prescribed pre/post HEAD and status snapshots. It exited 1 with
the CLI usage-limit error before producing a review report; both snapshots
matched (`HEAD 3589a57f1cf462f82aab0683a05dc0bda6be2c3c`, identical status).
There is no round-17 verdict. The lock-fd cleanup adjustment and its focused
test ran after that attempt, so the next review must cover the current tree.
No registered Assay gate or B105 R2 campaign has started. The work remains
uncommitted in P7a; P7b, the bounded B110 pilot/screen, and B131 remain later
in sequence.

## Controller sequence update (2026-10-09, review round 21 follow-up)

Round 21 Sol xhigh review returned ACCEPT WITH FIXES with one remaining LOW
finding: after starting the independent selector process, a controller-side
failure while reading the published candidates or writing the release marker
could leave the child blocked and unreaped. The process test now puts all
post-start orchestration in `try/finally` and always calls the bounded
release/terminate/reap helper. `test_selector_process_cleanup_runs_after_controller_failure`
adds two injected cases (release succeeds; release-marker write fails), and
`test_independent_selector_process_refuses_symlink_alias_during_publication`
continues to check cross-process alias contention.

The new cleanup oracles passed 3/3 cases in 0.73 s; the selector module passed
52/52 tests in 40.34 s; and the six-file B105/B110/docs regression passed
384/384 tests in 64.53 s. `git diff --check` and `python -m compileall -q
src/assay tools/b110_pilot_select.py` passed. No pre-fix red test run was
recorded for this review finding. Round 22 is pending and must review the
current diff. The P7a work remains uncommitted; no registered gate or R2
campaign has started.

## Controller sequence update (2026-10-09, review round 22 follow-up)

Round 22 Sol xhigh review returned **REJECT** with two findings. Its report is
`/tmp/assay-b118-p7a-round22/review.md`; it ran read-only from `.codex` with
`CODEX_EXIT=0`, and pre/post snapshots matched at HEAD
`3589a57f1cf462f82aab0683a05dc0bda6be2c3c` with identical status. The review
found that `_acquire_state_path_lock` used `tempfile.gettempdir()`, allowing
processes with different `TMPDIR` values to split locks if the state parent
was replaced. It also found remaining post-`Popen` setup and unbounded
`communicate()` calls in the selector concurrency tests.

The path lock now uses a private per-user lock directory under canonical
`/tmp`, independent of `TMPDIR`. The P7 contract and README, DESIGN-GUIDE, and
CONSUMERS describe that path lock and the descriptor lock's separate inode
role. `test_state_path_lock_parent_replacement_is_shared_across_tmpdirs`
starts separate processes with different `TMPDIR` values, replaces the
requested path's parent, and requires the second process to refuse. The
selector cleanup tests put all post-start work inside `try/finally`, use
bounded release/terminate/reap, and no longer call `communicate()` without a
timeout. `test_selector_process_cleanup_runs_after_controller_failure` still
injects both controller failure and release-marker failure after the child is
ready; `test_independent_selector_process_refuses_symlink_alias_during_publication`
continues to cover publication contention.

The four targeted process/lock cases passed 4/4 in 1.35 s. The full six-file
B105/B110/docs regression passed 385/385 in 66.20 s. `git diff --check` and
`python -m compileall -q src/assay tools/b110_pilot_select.py` passed before
that suite. No gate or R2 campaign was started by this worktree. Round 23 is
pending against the updated diff. After the changelog wording was aligned with
the lock behavior, the documentation example/vocabulary suite passed 55/55 in
0.34 s and `git diff --check` passed again.

Round 23 Sol xhigh review returned **ACCEPT — no findings**. The report is
`/tmp/assay-b118-p7a-round23/review.md`; it ran read-only from `.codex` with
`CODEX_EXIT=0`. Pre/post HEAD matched at
`3589a57f1cf462f82aab0683a05dc0bda6be2c3c`, and the status snapshots were
identical. The reviewer confirmed the TMPDIR-independent path lock and
two-process parent-replacement oracle, consistent docs, and bounded cleanup
for both selector subprocess tests. The P7a implementation is committed as
`7b7143d68`. The registered gate remains pending while the separate
cgroup-profiler R2 container runs.

## Controller sequence update (2026-10-09, B118 P7b final review)

The current P7b diff received a read-only GPT-6-Sol xhigh **ACCEPT** from
`CODEX_HOME=$HOME/.codex2` after the preferred `.codex` home reported its
usage limit. The report is `/tmp/assay-p7b-review-final9-codex2.md`; the
before/after HEAD snapshots both equal `d3821b5fab3163abfea15e8b54be9245fc6d3e5a`
and the status snapshots match. The reviewer found no actionable correctness
or evidence-handling issues and confirmed that the manual campaign paths are
separate from registered pilot paths, while the operator runbook archives
state and progress together and analyzes matching pairs.

After those fixes, the focused regression suite passed 151 tests in 54.17s;
the two directly changed documentation oracles passed, `bash -n` passed for
both B110 shell drivers, and `git diff --check` passed. No registered gate,
B105 R2 campaign, or B110 pilot has started from this worktree. P7b is ready
for merge; same-tip `tester-unified` and registered-gate acceptance remain
pending.

## Controller follow-up — 2026-10-09 (P7c artifact attestation)

P7b is now merged to main as `cbd605445`, and its code is included in the
registered `tester-unified` PASS at `5bf6db82`. That gate does not include this
P7c repair. A fresh read-only Sol xhigh review of the P7b integration found
that exit 6 and a completion marker could certify a malformed or incomplete
pilot summary, selection, state or deadline, and that the 90-minute cap ended
before report checking and final marker writes.

The P7c diff adds `tools/b110_pilot_report_check.py`. It parses bounded
no-follow files, recomputes the deterministic selection against the exact
clean commit/tree and source bytes, checks the complete R0/R1/R2 summary,
candidate dispositions, terminal state and no-hit resource evidence, current
two-hour campaign deadline, wheel digest, progress tail and full-plan identity.
It re-reads each validated file before creating a SHA-256 manifest. The host
launcher requires the exact expected artifact paths, verifies the manifest
digest marker, and checks every retained file against it. Pilot report
checking, final source identity and campaign/cap checks run inside the
90-minute subprocess timeout. The post-marker host check described below
controls the outer gate result; marker text alone cannot turn a failed check
into success.

An earlier focused suite passed 137 tests in 60.23 seconds, with
`P7C_TEST_EXIT=0` in `/tmp/assay-b118-p7c-focused-resume-2026-10-09.log`. It covers
`test_b110_pilot_checker_attests_a_complete_source_bound_run`, the
`test_b110_pilot_checker_rejects_exit_six_without_complete_evidence` damage
cases, the pilot shell ordering/expiry oracles, outer host-manifest omission
and tampering, and the three-document examples/vocabulary/anchor contract.
After the final timing-oracle update, `bash -n` passed for both gate drivers,
Python compilation passed, `git diff --check` passed, and `./run-gate.py --list`
listed the expected registered lanes.

The final read-only P7c review and same-tip registered `tester-unified` gate are
pending. The last B105 preflight refused before launching a container because
the dev-gates cgroup had 1.321 GiB free against a 2 GiB admission reserve. No
B110 pilot or full B105 R2 campaign ran in this follow-up.

## Controller follow-up — 2026-10-09 (P7c host snapshot and final check)

The host now copies the verified manifest-listed artifacts by content into
`.assay/b110-pilot-evidence/`, verifies the copied inventory and digests, and
atomically publishes the directory with files and directories made read-only.
The host emits the snapshot-index digest with the pilot markers, then reopens
the published snapshot and rechecks its contents, path identities and both
deadlines. All completion marker text is provisional: a refusal after marker
output leaves the registered command failed. A retry archives a prior
published snapshot with its original index receipt before starting a new
attempt. The original resumable files remain unchanged and are not the
authoritative retained evidence after snapshot publication. The
`b110-pilot-evidence` and `.assay/b110-pilot-evidence-archive` paths are
registered run-gate artifacts.

## Controller follow-up — 2026-10-09 (review round 6)

The read-only Sol xhigh review found that the host checker required the
run-gate evidence root and `lanes/` parent to be private, although run-gate
creates those as owner-controlled mode `0755` directories. The checker now
accepts those standard non-group/world-writable parents and continues to
require the lane directory and transcript to be private. A second finding was
that unavailable history returned ordinary exit 1, which made run-gate replace
the previous successful history record. Such retries now return exit 3, mapped
to infrastructure `ERROR`; the prior success remains eligible and the live
snapshot/receipt stay untouched. Tests construct the normal `0755/0755/0700`
directory layout and assert exit 3 for missing or corrupt retry evidence.

The focused final regression passed 129 tests in 60.29 seconds, recorded in
`/tmp/assay-b118-p7c-focused-final-2026-10-09.log` with
`P7C_TEST_EXIT=0`. Direct verifier tests prove that replacing the canonical
project path or adding a late state record is refused, that a changed published
snapshot fails re-verification, that a same-UID write racing between host hash
passes is detected, and that changing live resumable files after publication
does not alter the snapshot evidence. Shell tests inject final host-check
failure after marker output and retry that failed completion, verifying the
prior receipt is archived before the new snapshot is published. Fresh Sol
xhigh review and same-tip `tester-unified` remain pending; no registered gate
is running.

## Controller follow-up — 2026-10-09 (review round 10)

The fresh read-only Sol xhigh review found two issues. First, the newly added
selected-size progress test referenced `result` and `fixture` from another
test; those assertions now live with the complete-run fixture that creates
them. Second, the state-record validator used Assay's general legacy decoder,
which supplies `full` when `execution` is absent. Pilot state now requires an
explicit execution object, with a refusal oracle for a missing field.

The review snapshots stayed at `5bf6db82` with identical worktree status. The
same-tip focused suite and `tester-unified` acceptance remain pending; the
controller is waiting for the shared Assay gate slot to become available.

## Controller follow-up — 2026-10-09 (review round 11)

The read-only Sol xhigh review found two issues. First, a readable run-gate
history with no successful transcript was treated as unavailable evidence. That
could strand a snapshot left by a failed first publication, because every retry
preserved it and stopped before launching. The host checker now distinguishes
that state from unreadable or malformed history and quarantines the unattested
publication, preserving it under the incomplete-evidence directory so a retry
can proceed. A launcher oracle starts with a failed final host check,
records a valid failed lane history, and confirms the next attempt quarantines
and replaces the incomplete snapshot. Corrupt history and missing transcript
cases still preserve their existing evidence.

Second, the attempt deadline check ran before three final Git checks. Those
checks now run before final host verification, so the last deadline read follows
them. A delayed final status oracle crosses the attempt expiry before the last
host check and confirms the gate does not emit its host-verified marker.

The full `test_self_qualification_container.py` module passed 58 tests in
91.57 seconds. `bash -n`, Python compilation, and `git diff --check` passed.
A fresh read-only Sol xhigh review and the same-tip registered gate remain
pending; the registered gate is waiting for the active shared R2 campaign.

## Controller follow-up — 2026-10-09 (review round 12)

The read-only Sol xhigh review found that the history-store preflight checked
the top-level schema but not the nested `b110-pilot.history` entries. The
run-gate history query filters non-dictionary entries, so a malformed prior
entry could disappear and make the host classify the remaining failed record
as proof that no successful attestation existed. The preflight now validates
the pilot lane object and every stored history entry before querying; malformed
nested history returns infrastructure-inconclusive exit 3 and preserves the
live snapshot and receipt. A new launcher oracle covers a prior publication
with a valid latest failure and `history: [null]`, asserting no child run,
quarantine, archive, or snapshot replacement occurs.

The review's timing-comment observation was checked against the P7c contract:
the lane comment correctly distinguishes the 90-minute per-attempt cap from
the persisted two-hour campaign deadline and 2h20m outer timeout. The
round-12 review snapshots matched at `5bf6db82` with identical worktree
status. The regression test and focused same-tip gate remain pending.

## Controller follow-up — 2026-10-09 (review round 13)

The read-only Sol xhigh review found two history-attestation defects. A stored
`pass` with a non-integer or nonzero exit code could pass preflight and then be
silently skipped by the history query, making an older success disappear. The
host now validates every pilot history record's outcome, exact integer exit
code, eligibility, commit/worktree binding and successful transcript binding;
malformed latest records also refuse as unavailable. A second read of
`history.json` through `run-gate history` could observe bytes replaced after
preflight, and run-gate would report corrupt telemetry as an empty store. The
host now consumes the validated in-memory snapshot directly, so the records
used for attestation are the exact bytes that passed preflight. Controller
inspection also caught an explicit null `b110-pilot` lane being mistaken for
an absent lane; it now fails as malformed. Non-string outcomes fail closed
without leaking a `TypeError`.

The full focused suite passed 216 tests in 121.72 seconds. After the final
additional malformed-outcome case, the four malformed-history variants and
history-replacement regression passed 5 tests in 11.13 seconds. Compilation,
shell syntax, `./run-gate.py --list`, and `git diff --check` passed. The
round-13 snapshots were unchanged at `5bf6db82`; the independent review's two
findings and the controller's null-slot finding are covered by these new
oracles. A fresh read-only Sol xhigh review and same-tip registered gate are
still pending.

## Controller follow-up — 2026-10-09 (round 12 test closeout)

The full `test_self_qualification_container.py` module passed 59 tests in
95.22 seconds, including the malformed nested-history retry oracle. The first
`test_b110_pilot_report_check.py` run found an existing test helper returning
three values while one test treated it as a dictionary. The helper annotation
now states its tuple contract and that test reads the validated record map
from the third value. The complete report-checker module then passed 42 tests
in 12.58 seconds. `py_compile`, `bash -n`, and `git diff --check` passed.
The fresh read-only review and registered same-tip gate remain outstanding.

## Controller follow-up — 2026-10-09 (review round 14)

The read-only Sol xhigh review found that non-pass latest records did not
validate `exit_code`. A latest `error` with `exit_code: "3"` could therefore
look like a readable store with no successful attestation and quarantine the
live snapshot. Non-pass records now allow only an integer or absent exit code.
The retry oracle covers the malformed latest record and asserts exit 3 with
the snapshot and receipt preserved. A paired legitimate-state oracle covers
`error` with no exit code and no earlier success; that case still quarantines
the unattested snapshot and starts a new attempt. The review also found that
the DESIGN-GUIDE still described the removed `run-gate history` subprocess. It
now describes the single validated `history.json` snapshot and why the host
does not reopen the mutable store.

The review snapshots matched at `5bf6db82`, with identical worktree status.
The malformed-history, valid-error retry, and history replacement regressions
passed 9 tests in 24.37 seconds. Python compilation and `git diff --check`
passed. The full focused suite after round-14 fixes and a fresh read-only
review are pending; no registered gate has run on P7c.

## Controller follow-up — 2026-10-09 (round 14 test closeout)

After the round-14 fixes, the complete focused suite passed 220 tests in
185.09 seconds across `test_self_qualification_container.py`,
`test_b110_pilot_report_check.py`, `test_self_lane.py`, and
`test_docs_examples_and_vocabulary.py`. `git diff --check`, Python compilation,
shell syntax checks, and `./run-gate.py --list` also passed. The final fresh
read-only review and registered same-tip gate remain pending.

## Controller follow-up — 2026-10-09 (review round 16)

The independent read-only Sol xhigh review found three remaining cases. A
completed same-commit retry could fail the host source-status check before
archiving the preceding pass; the launcher now archives prior evidence before
source cleanliness and later host admission, and a regression proves that an
early status failure cannot erase that snapshot when run-gate records the
failure. A documented ineligible `not_run` latest record with `commit: null`
could reject an earlier eligible pass; the history validator now accepts that
producer shape while still requiring every eligible record to bind a valid
commit. Finally, quarantine now checks the moved device and inode for files and
symlinks as well as directories. A replacement-race regression fails closed.

The complete focused suite passed 228 tests in 147.92 seconds. The targeted
round-16 history and quarantine regressions passed, as did `git diff --check`,
Python compilation, shell syntax checks and `./run-gate.py --list`. The fresh
read-only review and same-tip `tester-unified` gate remain pending; no B110
pilot ran from P7c.

## Controller follow-up — 2026-10-09 (review round 17)

The Sol xhigh review used `.codex2` after the preferred `.codex` route hit its
usage limit. Before/after HEAD and status snapshots matched. It found that
missing `stat`/`realpath`, lock failures, or early project refusals could
still return an eligible failure before prior evidence was archived. The
pilot launcher now returns exit 3 for every refusal until prior pilot evidence
is archived or quarantined; `run-gate.toml` maps 3 to `ERROR`, so that latest
record cannot displace an earlier eligible pass. A regression injects a lock
mode prerequisite failure, records the resulting ineligible latest `ERROR`,
then proves the next retry can archive the earlier pass and launch.

The review also caught an inaccurate changelog statement. The live snapshot
uses a pending receipt while the snapshot and final receipt are published in
separate steps; only the verified archive bundle is published with one
directory rename. The changelog now distinguishes those two boundaries.

The five round-17 focused cases passed, followed by the complete focused suite:
229 passed in 154.25 seconds. `git diff --check`, Python compilation, shell
syntax checks and `./run-gate.py --list` pass. A fresh review and the same-tip
registered `tester-unified` gate remain pending; no B110 pilot ran from P7c.

## Controller follow-up — review round 22 (2026-10-09)

The exact-diff Sol xhigh review used `.codex2`; its before/after HEAD and
worktree-status snapshots matched. It found two remaining withdrawal races and
one stale P7 brief oracle. A `FileNotFoundError` from rename could name the
quarantine destination, yet the handler treated it as proof that the archive
source had disappeared. Also, `os.rename` could replace an empty directory
created at the generated quarantine destination between the existence check
and move. The P7 brief still assigned planning and campaign checks to
`run_b110_pilot`, while the implementation owns them in
`run_b110_pilot_inner`.

Archive publication and withdrawal now use Linux `renameat2` with
`RENAME_NOREPLACE`. A withdrawal collision retries with a fresh random name.
On `ENOENT`, the host checks whether the visible source is gone; if it remains,
the host reopens or recreates the private incomplete directory and retries the
move. A focused test removes that directory while its descriptor remains open,
then proves the unverified entry is moved into a newly verified directory. A
parameterized source-absent/source-present oracle checks both `ENOENT` cases.
The archive-boundary race test now creates an empty directory at the actual
generated quarantine name immediately before the move and verifies the
existing directory and sentinel survive while withdrawal retries successfully.
The P7 brief and its static test oracle now distinguish the outer worker
wrapper from the inner pilot implementation.

The targeted archive race set passed **9 tests in 13.92 seconds**. The full
focused suite passed **242 tests in 171.44 seconds**; Python compilation,
`bash -n` for both B110 scripts, `git diff --check`, and `./run-gate.py --list`
also pass. A fresh exact-diff review and the registered same-tip gates remain
pending; no B110 pilot has run from this worktree.

## Controller follow-up — review round 19 (2026-10-09)

The read-only Sol xhigh review used `.codex2` after the preferred `.codex`
route hit its usage limit. HEAD and status snapshots matched. It found three
remaining transcript cases: a same-user path replacement after the final
live-log check but before archive rename; an in-place write to the opened log
after its bytes were cached; and a failed first publication that could remain
stuck when valid history had no successful candidate and the transcript root
was absent.

The host now rehashes the opened transcript descriptor after marker validation
both when accepting history and when checking a persisted attestation. The
version-2 attestation names a read-only transcript copy inside the staged
archive and binds its digest. The host verifies that copy's digest and source,
tree, snapshot and final host markers immediately before the one-rename
publication point; the archive no longer depends on the mutable source path.
History is filtered for an eligible successful candidate before the host
requires that transcript root, so readable failed-only history can quarantine
an unattested first publication and proceed. README, DESIGN-GUIDE, CONSUMERS,
CHANGES and the P7 brief describe the retained transcript copy.

Eight focused race and retry cases passed. The full focused suite passed
**234 tests in 165.66 seconds** (`/tmp/assay-b118-round20-focused.log`,
`TEST_EXIT=0`). `git diff --check`, Python compilation, shell syntax, and
`./run-gate.py --list` pass. The next exact-diff read-only review and registered
same-tip gates remain pending; no B110 pilot ran from P7c.

## Controller follow-up — review round 20 (2026-10-09)

The exact-diff read-only Sol xhigh review used `.codex2`; before and after
HEAD/status snapshots matched. It found that a same-user replacement of the
staging directory at the archive rename boundary could be moved to the visible
archive name and only rejected by the subsequent inode check, leaving the
unverified entry published. The host now withdraws an archive entry into the
incomplete area when any post-rename identity check fails, including checks of
the archived transcript, archive directory and project path. It performs the
archive-entry inode check last. New boundary tests replace both the staging
directory and the staged transcript; each must fail with the visible archive
empty and untrusted content under the incomplete path.

The four archive publication/race cases passed. The full focused suite passed
**236 tests in 165.03 seconds** (`/tmp/assay-b118-round24-focused.log`,
`TEST_EXIT=0`). `git diff --check`, Python compilation, shell syntax, and
`./run-gate.py --list` pass. A fresh exact-diff review and the same-tip
registered gates remain pending; no B110 pilot ran from P7c.

## Controller follow-up — review round 21 (2026-10-09)

The next exact-diff Sol xhigh review used `.codex2` and recorded matching
before/after HEAD and status snapshots. It confirmed the directory/transcript
replacement fix, then found two adjacent publication races: the predictable
withdrawal path could already exist, and same-inode writes to the transcript
or another bundle file after its last pre-rename check could evade path-only
post-rename checks.

Withdrawal now selects a high-entropy destination, checks for collisions and
retries on collision. After the archive rename, the host rechecks the snapshot
inventory and digest, receipt, attestation and transcript content and paths,
archive parent and project path before returning success. Any failed check
withdraws the entry to the incomplete area. The staging replacement oracle
also occupies the former predictable destination; parameterized boundary tests
mutate the transcript, receipt, attestation or snapshot file in place and
prove the visible archive remains empty.

Eight focused publication-race cases passed. The full focused suite passed
**240 tests in 174.51 seconds** (`/tmp/assay-b118-round26-focused.log`,
`TEST_EXIT=0`). `git diff --check`, Python compilation, shell syntax, and
`./run-gate.py --list` pass. A fresh exact-diff review and same-tip registered
gates remain pending; no B110 pilot ran from P7c.

## Controller follow-up — 2026-10-09 (review round 18)

The read-only Sol xhigh review used `.codex2` after the preferred `.codex`
route hit its usage limit. HEAD and worktree status snapshots matched. It found
that persisted-attestation verification checked the transcript pathname before
reading but did not recheck it after digest and marker validation. The host now
keeps the run-gate root, `lanes`, and `b110-pilot` descriptors open through
validation and confirms that the absolute root, both parent entries, and log
entry still name the opened inodes. Archival repeats that validation after the
archive bundle is staged and immediately before its atomic publish rename. Two
race oracles replace the transcript path during marker validation and confirm
that direct verification refuses and a staged bundle remains incomplete rather
than being published.

The review also found that a directory replaced between `stat` and `open`
could receive the old directory's mode in the cleanup path. Quarantine now
restores mode only after this invocation successfully changed the verified
opened inode, using that inode's captured mode. A replacement race proves the
replacement keeps its own permissions; existing move and identity-check failure
tests still prove mode restoration for the original inode.

The five transcript/quarantine regressions passed. The full focused suite then
passed **232 tests in 153.01 seconds** (`/tmp/assay-b118-round19-final-focused.log`,
`TEST_EXIT=0`). `git diff --check`, Python compilation, shell syntax, and
`./run-gate.py --list` pass. A fresh read-only review and the same-tip
registered `tester-unified` gate remain pending; no B110 pilot ran from P7c.

## Controller follow-up — P7c review round 23 (2026-10-09)

The exact-diff Sol xhigh review report is `/tmp/assay-b118-round23-review.md`.
It found that the final archive return path did not recheck the published
snapshot name, withdrawal could leave an unverified archive entry visible if
the incomplete directory was replaced unsafely, quarantine used an
overwrite-capable rename, the collision oracle used a nonempty directory, and
the P7 brief assigned wrapper work to the inner worker.

Archive verification now repeats both snapshot content verification and
snapshot-path identity checking at the return boundary. Withdrawal falls back
to a private, descriptor-verified `.assay/b110-pilot-evidence-unverified`
directory (or a private random sibling) if the incomplete destination cannot
be reopened safely. Quarantine and archive movement use atomic no-replace
renames. The collision oracle now places an empty directory at the generated
destination and verifies that it survives; the P7 brief and static oracle
separate launcher, outer-wrapper, and inner-worker responsibilities.

The first combined focused rerun found two test failures: the exact registered
artifact list omitted the new fallback path, and one fake-Docker cgroup probe
identity case did not reproduce when isolated. The artifact oracle now names
the fallback, and that Docker case passed both in isolation and in the clean
full rerun. The final combined focused suite passed **245 tests in 184.42
seconds** (`/tmp/assay-b118-round24-focused-rerun.log`, `TEST_EXIT=0`). Python
compilation, `bash -n` for both B110 shell drivers, `git diff --check`, and
`./run-gate.py --list` pass. A fresh exact-diff review and same-tip registered
gates remain pending; the separate RG-89 campaign is still active, so no
registered gate or B110 pilot was started from this worktree.


## Controller follow-up — P7c round-24 review fixes and round-25 regression

The read-only Sol xhigh review report is `/tmp/assay-b118-round24-review-codex2.md`;
HEAD and status remained unchanged during review. It found two P2 issues. First,
the checker previously trusted agreement between progress and candidate state
without independently binding both to the current sweep's judge identity.
`src/assay/cli.py` now captures that digest from the in-memory `candidates`
event and includes it in the pilot summary. The checker requires the progress
event and every state record to match the summary value. The coherent-tampering
oracle rewrites progress and all state records to the same wrong digest.

Second, prior-evidence quarantine previously stopped if the incomplete path was
unsafe. It now uses the same verified private unverified-evidence fallback as
archive withdrawal, selecting a random private sibling if the fixed fallback is
unsafe or occupied. The quarantine marker records its destination, and the
container marker parser accepts both destination roots. Symlink and public-
directory regressions prove the retry can proceed without changing the unsafe
path or its external target.

The first round-25 full run logged **288 passed, 4 failed** in
`/tmp/assay-b118-round25-focused.log`. The failures were the direct progress
helper missing its new expected-judge argument, a stale expected refusal string,
a stale fallback constant in the withdrawal test, and an identity-check test
injecting failure during fallback-root setup rather than at the moved snapshot.
After those corrections, the four failures plus the paired rename-stage case
passed in `/tmp/assay-b118-round25-failures-rerun.log` (**5 passed**,
`TEST_EXIT=0`). The coherent-wrong-judge regression also passed in the full
focused suite, which passed **292 tests in 198.53 seconds** at
`/tmp/assay-b118-round25-focused-rerun.log` (`TEST_EXIT=0`). Static checks also
passed. Exact test names are in the round-25 traceability row below.

Files changed for these round-24/25 repairs: `src/assay/cli.py`,
`tools/b110_pilot_report_check.py`, `tools/b110_pilot_host_check.py`,
`tools/self-qualification-container.sh`, `gate/tests/test_b110_pilot_report_check.py`,
`gate/tests/test_self_qualification_container.py`,
`tests/core/test_pilot_candidates_file.py`, README, DESIGN-GUIDE, CONSUMERS,
CHANGES, the P7 brief, this report and the B118 backlog entry.
The final P7c Sol xhigh exact-diff review, `tester-unified`, and
`self-qualification-preflight` on the integrated tip remain pending. The
pre-P7c gate markers and preflight refusal are recorded in the current summary;
no B110 pilot was run.

### Round-25 test traceability

| Scope and actual tests | First round-25 result | Final result |
|---|---:|---:|
| `test_progress_candidate_total_is_the_selected_pilot_size_not_full_plan`; `test_b110_pilot_checker_rejects_exit_six_without_complete_evidence` (`progress-wrong-judge`, `coherent-wrong-judge` cases); `test_archive_withdraw_falls_back_when_incomplete_directory_is_replaced_unsafe`; `test_quarantine_restores_snapshot_mode_when_move_or_identity_check_fails` (`rename`, `identity-check` cases) | 4 failed among 292 total; see `/tmp/assay-b118-round25-focused.log` | Four failing cases plus the paired rename-stage case: 5 passed in `/tmp/assay-b118-round25-failures-rerun.log`; coherent-wrong-judge and the full suite: 292 passed in 198.53s, `TEST_EXIT=0` |

## Controller follow-up — review round 27 (2026-10-09)

A fresh read-only GPT-6-Sol xhigh review used the preferred `.codex` route on
HEAD `5bf6db82`; before/after HEAD and status snapshots matched. It found
three remaining races: prior-attempt cleanup used path-based `rm` after the
`.assay` symlink check; the archive entry name was not rechecked after the
final snapshot verification; and later identity checks did not revalidate
read-only evidence or private archive/quarantine directory modes.

Cleanup now captures the expected `.assay` identity and asks the source-bound
host checker to open it without following links and unlink only the declared
prior-attempt names relative to that descriptor. Archive publication checks
the receipt, attestation, transcript and snapshot again before the final
archive-entry identity check. Snapshot paths require owner-only read-only modes
at their last checks; archive, incomplete and quarantine directories require
their captured private writable modes again before reporting a path. New
regressions cover a replaced `.assay` symlink, archive-name substitution after
final verification, and mode changes to snapshot files plus archive/quarantine
roots.

Static checks pass (`git diff --check`, Python AST parsing, and `bash -n` for
both B110 shell drivers). The new race regressions have not yet run; the
registered gate slot is occupied by the separate resumed RG89 R2 campaign.
This round's fixes need a fresh exact-diff review and same-tip integrated gate
before B118 can close. No B110 pilot ran from this worktree.

## Controller follow-up — review round 28 fixes (2026-10-09)

The fresh Sol xhigh review report is `/tmp/assay-b118-round28-review-codex.md`.
HEAD and working-tree snapshots matched. It found two remaining races: the
inner B110 shell still removed attempt outputs through `.assay` by name after
host admission, and snapshot child modes could change after the content pass
but before final verification returned.

The host launcher now creates a missing `.assay` directory with a private
temporary directory and atomic no-replace rename, returns its device/inode,
and passes that identity into the tester container. The inner B110 script opens
and verifies that exact directory, then routes cleanup, output, state, and
deadline paths through the pinned descriptor. Attempt-window creation uses the
inherited descriptor with `dir_fd`; host archival, publication, and final
verification also require the admitted `.assay` identity. New regressions
cover first-run state creation, a competing `.assay` appearance during atomic
installation, and replacement by a symlink between attempt-window admission
and file creation.

Snapshot verification now repeats the full child-file verification at the
completion boundary, after other receipt, transcript, and root checks. The
archive path keeps its final visible-entry identity check last. New tests inject
a child-mode change after the earlier verification pass in both published
snapshot verification and archive publication.

`bash -n` for both B110 shell drivers, Python compilation, and `git diff
--check` pass. The new regressions have not run yet. Round 28 is not acceptable
until its fixes receive a fresh exact-diff review and the registered
`tester-unified` gate. No B110 pilot ran from this worktree.

## Controller follow-up — review round 29 fixes (2026-10-09)

The read-only Sol xhigh review report is `/tmp/assay-b118-round29-review-codex.md`.
It rejected the diff for four findings: the report checker rejected the
worker's proc-fd paths and generated invalid manifest names; `assay campaign
init` still used its default deadline destination; shell redirections could
follow same-user leaf symlinks; and the final child/path checks could certify a
detached or writable snapshot/archive.

The checker now accepts the inherited `.assay` descriptor plus its admitted
device/inode, pins `b110-pilot-state` separately, compares the summary with the
worker's original state-dir spelling, and emits manifest paths under `.assay/`
while reading through pinned descriptors. Campaign init passes `--out` with
the pinned deadline path. `b110_pilot_safe_output.py` creates outputs using
`O_EXCL|O_NOFOLLOW` relative to the admitted directory and safely reads the
attempt log without following a replaced leaf. Snapshot verification checks
the visible `.assay` identity after its final child pass; archive publication
rechecks the archive root and complete child permissions after boundary
checks. The archive failure message now describes failed final verification
rather than claiming every refusal proves an identity change.

The checker, safe-output, B110 worker and host-boundary regressions pass. The
full B110 container-test module passed 100 tests; the candidate-file suite
passed 44; and the post-helper targeted shell/report-check selection passed 9.
An initial full container-module run exposed one stale assertion after the
archive check was moved; the corrected boundary tests now pass. Fresh Sol
xhigh review and registered `tester-unified`/preflight evidence are still
pending. No B110 pilot ran.

## Controller follow-up — review round 30 fixes (2026-10-09)

The round-30 Sol xhigh review report is `/tmp/assay-b118-round30-review.md`.
It found three completion-boundary gaps: snapshot-name replacement during the
last child verification, a mismatch between the proc-fd state path and the
canonical path Assay writes into its summary, and archive root/entry/snapshot
replacement during the final permission sweep.

The checker now resolves the pinned state descriptor to the canonical path
Assay records. Published-snapshot verification rechecks the visible snapshot
name after its final child pass. Archive completion rechecks the visible
archive root, archive entry, snapshot path, `.assay`, and project path after
the final permission sweep. The pinned-fd test now uses the actual canonical
summary path without rewriting its fixture. Four focused race/path tests pass
in 6.00 seconds (`python -m pytest -q ...`, exit 0), and `git diff --check`
passes. A fresh exact-diff Sol xhigh review and registered gate evidence are
still pending; no B110 pilot ran.

## Controller follow-up — review round 31 fixes (2026-10-09)

The exact-diff Sol xhigh review report is `/tmp/assay-b118-round31-review.md`;
the invocation used `.codex`, GPT-6-Sol xhigh, and unchanged before/after HEAD
and status. It found three completion gaps: archive permission verification
did not repeat artifact digests, the final host check did not recheck Git
source identity, and the deadlines were not checked after the final path
checks.

Archive completion now repeats the full snapshot content, identity, and mode
verification. The final published-snapshot verifier rechecks commit, tree, and
cleanliness after its child/path checks, rechecks the admitted directory path,
then checks both deadlines immediately before returning. The verifier itself
emits `B110_PILOT_HOST_VERIFIED_SHA256` only after those checks; the shell no
longer writes an independent marker. New regressions cover a changed archived
child, source mutation during final verification, and expiry after the final
child pass. The first full focused run had 262 passes and one stale expected
message; after correcting that assertion, the suite passed **263 tests in
206.46 seconds** (`/tmp/assay-b118-round33-focused.log`, `TEST_EXIT=0`). Shell
syntax, Python compilation, `git diff --check`, and `./run-gate.py --list`
pass. Fresh exact-diff review and registered gate evidence remain pending; no
B110 pilot ran.

## Controller follow-up — review round 32 fixes (2026-10-09)

The exact-diff Sol xhigh review report is `/tmp/assay-b118-round32-review.md`;
the `.codex` invocation's before/after HEAD and worktree status matched. It
found two remaining races: the published snapshot or receipt could be replaced
during final Git identity checks, and the archive receipt, attestation, or
transcript could change during the snapshot sweep.

Final host verification now repeats the complete snapshot digest/deadline pass
after Git identity/cleanliness checks, then rehashes the receipt and rechecks
the visible snapshot, receipt, pending-receipt, `.assay`, and project paths
before its last deadline check. Archive completion rehashes and revalidates
the receipt, attestation, and archived run-gate transcript after the final
snapshot pass, then checks the public archive names. Six targeted path/content
race cases pass. The full focused suite passes **268 tests in 213.92 seconds**
(`/tmp/assay-b118-round35-focused.log`, `TEST_EXIT=0`); `git diff --check`
passes. A fresh exact-diff review and registered gate evidence remain pending;
no B110 pilot ran.

## Controller follow-up — review round 33 acceptance (2026-10-09)

The independent exact-diff Sol xhigh review report is
`/tmp/assay-b118-round33-review.md`. It reviewed the complete uncommitted
change set, including untracked files, and returned **accept with no
actionable findings**. The `.codex` GPT-6-Sol xhigh invocation recorded the
same HEAD (`5bf6db82`) and worktree status before and after. The focused suite
and static checks are green; registered `tester-unified` at the committed
P7c tip remains pending. No B110 pilot ran.

## Controller follow-up — P7c gate and round-34/38 integration reviews (2026-10-09)

Registered `./run-gate.py tester-unified` passed at committed P7c repair
`68f8a477b761fe650fdae6268cbe77e46ae148a0` (`GATE_EXIT=0`, tree
`14d9211c804c5e84d4414fbecd49b019f05b407e`). The run log is
`/tmp/run-gate/lanes/tester-unified/43f4b6c3b248e4e6b4e075379a654c9f.log`;
the B145 probes, wheel build/install, self-hosted suite, analysis lane,
independent self-hosting, pyflakes and SQL qualification passed. Peak observed
footprint was 5,094 MiB, 600 MiB over the 4,494 MiB baseline. The self-hosted
container exited 0; the SQL containers were removed after their qualified run.
The CIU-managed checkout remains ready, attached to its recorded branch, and
clean. No B105 pilot ran.

The three exact staged B118-to-B131 Sol xhigh reviews found seven concrete follow-ups:
the B110 checker omitted `ProgressStream` timing fields from its expected
baseline event; B110 state records did not compare mutation site spans and
descriptions against the plan; B131 accepted a self-consistent but incomplete
supplied plan without rederiving the full analysis inventory; duplicate
terminal verdict events were not refused; and `emitted_at` was not parsed as
an aware timestamp. The B110 checker also did not validate `end` timing, and
B131 accepted malformed timestamps on its `end` and terminal events. The
integration now accepts and validates the producer's timestamped baseline and
end shapes, parses asserted timestamps as aware instants, requires one
terminal verdict, compares all state site metadata, and rebuilds the
complete committed analysis-lane candidate inventory before selection or
verification. The two affected suites pass **104 tests in 29.84 seconds**; the shipped-source
pyflakes oracle, Python compilation, and diff checks pass. A final exact-tree
review and integrated registered gate remain pending, so the 68f8a477 gate
does not cover these later integration-only corrections.

The fourth exact-tree B118-to-B131 Sol xhigh review was run with `.codex2` on
HEAD `d4d2cb285b60dff9d32db9a09bc73fed9ab113fb` and staged diff SHA256
`46ea971aa9175121846a997f656779959ec25047b472eb5bb21aa23503fa9ce5`;
the review record is `/tmp/assay-b131-b118-final-review3-codex2.md`. It found
two more gaps: B131 accepted state without the resume-required
`replacement_sha256`, and the B131 candidate plus B110 candidate/resume
progress paths accepted missing producer timing. The selectors now derive
replacement-byte hashes from committed mutation sites and bind them into
selection and state checks. The checkers require valid producer timing on the
progress records they use. New refusal cases and producer-shaped fixtures
bring the focused B131/B110 suites to **111 passed in 35.40 seconds**. Python
compilation, pyflakes, and diff checks remain to rerun, followed by another
exact-tree Sol xhigh review and same-tip gates.

The fifth exact-tree B118-to-B131 Sol xhigh review used `.codex2` on HEAD
`d4d2cb285b60dff9d32db9a09bc73fed9ab113fb`, staged diff SHA256
`9328a27edb0e6d32c4ed31600672a90cf6520662cfd43fc29eca8ce81cb998fc`;
its record is `/tmp/assay-b131-b118-final-review4-codex2.md`. It found three
further gaps: B110 did not require collection evidence for full kills; B131
did not reconstruct the typed execution receipt or explicitly enforce its
kill-specific witness rules; and B131 could certify a completed summary that
also carried a refusal. B110 now requires collection evidence for every kill.
B131 validates the mutation execution model, requires a witness for full
kills, refuses kill-only receipts on non-kills, and closes the completed
summary shape. The existing terminal matcher already rejected a completely
missing kill witness; the typed check also enforces the receipt's non-empty,
bounded node identity and closed field shape. New refusal cases bring the
focused B131/B110 suites to **116 passed in 31.58 seconds**. Compilation,
pyflakes, and diff checks remain to rerun, followed by exact-tree re-review and
same-tip gates.

## Controller follow-up — exact-tree review round 39 (2026-10-10)

The sixth exact-tree B118-to-B131 Sol xhigh review used `.codex2` and is
recorded in `/tmp/assay-b131-b118-final-review5-codex2.md`. It found two more
gaps. B110's campaign, candidate, deadline, state and progress paths use the
launcher-pinned `.assay` descriptor, but Assay's no-follow input loader
rejected the proc-fd spelling before planning. The safe reader now follows
only the exact kernel-owned descriptor link, then walks suffix components
with `O_NOFOLLOW`; pilot state locking preserves that descriptor root rather
than resolving it to a replaceable pathname. The review also found that a
full kill could carry `command="r2"`; B110 now requires declared-command
collection evidence for a full kill and R2 evidence for witness executions.
Behavioral oracles cover campaign creation/deadline reading, candidate and
progress I/O, symlink refusal below the descriptor, state writes after visible
`.assay` replacement, and evidence-command mismatch. The focused B131/B110
checker suites pass **117 tests in 43.65 seconds**; the safe-I/O, campaign and
pilot-path core suites pass **124 tests in 20.96 seconds**; docs and pyflakes
pass **57 tests in 10.57 seconds**. The seventh exact-tree review and same-tip
registered gates remain pending; no B110 pilot has run.

## Controller follow-up — exact-tree reviews rounds 40–42 (2026-10-10)

Round 40 found three integration gaps: the B110 checker compared the pilot
summary's state path to a resolved filesystem path instead of the launcher
proc-fd spelling; non-kill state could carry a witness-cold execution and a
witness-cold kill did not require an R2 failed-call prefix; and B131 allowed
`witness-prefix` despite the selected-candidate pilot forbidding reuse. The
checker now verifies that the supplied spelling names the state directory
opened under the admitted `.assay` descriptor before comparing the summary,
binds state mode to outcome and collection evidence, and permits only `full`
or `witness-cold` kills in the analysis pilot.

Round 41 requested stronger oracles for a state-directory replacement after
descriptor open and for a full-mode survivor carrying a valid failure witness.
The tests now perform that path swap and assert refusal before manifest
publication, and cover both non-kill witness contradictions. The final
round-42 Sol xhigh review accepted the remediation delta with no findings. Its
record is `/tmp/assay-b131-b118-review9-codex.md`; HEAD, status, staged diff
SHA256 `41d8c8810f22ebf4761343eb0359d8a7466da39edfe1e054b3f3a5366c12c7c0`,
and repair-delta SHA256
`631507361fe0eec4378411514f20c1792c8dbc0b8c597193a0d51bbd7a2971d8` were
unchanged by review. The focused B131/B110 checker suites pass **123 tests**;
Python compilation and `git diff --check` pass. Same-tip registered gates and
both bounded pilot measurements remain pending; a separate RG89 `r1` gate was
active at this checkpoint, so no B131 gate was launched concurrently.

## Cross-project integration follow-up — B131 reviews seven–nine (2026-10-10)

The later B131 integration reviews found that incomplete attempts could claim
unavailable resumed candidate dispositions, B110 candidate indexes could be
swapped between identities, and the registered B131 run log was not included
in the host attestation. The shared resume-queue validator now binds sparse
and prefix indexes to candidate identity and prior dispositions for every
attempt, including interrupted attempts. B131 attestation schema 2 includes a
snapshot and digest for `analysis-r2-pilot-run.log`, which the host rechecks
after source verification. Producer-shaped rejection oracles cover these
cases. Round-nine Sol xhigh review of the complete Assay diff accepted with no
findings; static checks pass. The updated registered suite and B110 fixed
pilot are still pending on this exact tree.
