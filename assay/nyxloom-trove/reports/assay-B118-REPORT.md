# B118 implementation report — in progress

**Base:** `3589a57f1cf462f82aab0683a05dc0bda6be2c3c`  
**P7a branch:** `assay-b118-p7-pilot`  
**P7a commit:** pending  
**P7b commit:** pending

## Status

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
reviewer ran no tests or gates. P7b implementation has not started.

The registered gate and `self-qualification-preflight` have not run. At the
2026-10-08 22:26 UTC process inventory, no `tester-unified` or run-gate process
was active. The B087 qualification service stack was still running and was
left untouched. The last gate cgroup reserve probe reported 1,567,969,280
bytes available against the required 2 GiB; do not start resource-heavy gates
until it passes. Gate/preflight log paths and marker lines will be filled in
from their actual runs; none are claimed here.

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
for both selector subprocess tests. P7a is ready to commit; the registered
gate remains pending while the separate cgroup-profiler R2 container runs.
