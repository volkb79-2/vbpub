# W0-REL REPORT (program 2026-10)

Branch/worktree: `cmru-w0-rel` at `/workspaces/vbpub/.worktrees/cmru-w0-rel` (from `cmru-wave-2026-10` @ baf0ea295).
Work was done by a predecessor implementer (commits `58bc7c0c3`..`f1228d288`, from the CONTINUATION file and git log)
and by this successor (everything after `f1228d288`). Evidence statements say who ran what.

## Findings in scope

| Id | Evidence (review) | Fix | Test(s) | Plant/revert |
|---|---|---|---|---|
| REL-12 | `cli.py` release/status was one ~435-line block with dead reassignments | `58bc7c0c3`: split into `_release_or_status`, `_status`, `_release_launcher`, `_release_child`; dead code dropped; behaviour identical | existing suite unchanged (only the two REL-01 tests were red before and after) | n/a (pure refactor; predecessor ran the suite before/after) |
| REL-01 / KI-54 | two handoff guards raised `RuntimeError` instead of exit 1 | `06791b345`: both guards `log_error` + `sys.exit(FAILURE)`; KI-54 marked fixed | `tests/test_cli_release_snapshot_boundaries.py::test_release_rejects_internal_handoff_on_dry_run`, `::test_release_rejects_an_internal_snapshot_spanning_multiple_git_families` (red on the old code before the fix, per predecessor) | red-before/green-after observed by predecessor, not hand-planted |
| REL-02 | stale frozen `[5.6.0]` generated section shipped by the next release | `68a03b4fe`: data (stale block removed) + `changelog._project_commits_after_cursor` regenerates a stale generated section in place; a current one stays byte-identical | `tests/test_changelog.py::test_rel02_stale_generated_section_is_regenerated_in_place`, `::test_rel02_current_generated_section_stays_byte_identical_on_resume`, `::test_rel02_stale_section_keeps_older_sections_intact` | predecessor reported plant/revert verified (not re-run by successor) |
| REL-10 / KI-30 | non-empty `[Unreleased]` in a tagged release | `68a03b4fe`: `[Unreleased]` body emptied (comment kept), `generate_release_changelog` refuses a non-empty plain `[Unreleased]`; KI-30 marked fixed | `tests/test_changelog.py::test_rel10_tagged_release_refuses_a_nonempty_plain_unreleased_section`, `::test_rel10_cleared_unreleased_section_with_a_comment_is_accepted` | predecessor reported plant/revert verified |
| REL-03 | legacy abandon refused when a release tag was only an ancestor of the base | `2b7ea2ae5`: a tag counts as new only if reachable from the candidate AND not a strict ancestor of `base_commit` | `tests/test_cli_abandon.py::test_rel03_legacy_abandon_ignores_an_earlier_release_tag_below_the_base`, `::test_rel03_legacy_abandon_keeps_a_tag_exactly_at_the_base_suspicious`, `::test_rel03_legacy_abandon_refuses_a_tag_on_a_candidate_only_commit`, `::test_abandon_legacy_snapshot_refuses_when_a_remote_tag_cannot_be_inspected` (real git) | predecessor's real-git tests; not hand-planted by successor |
| REL-14 | sidecars/abandon accepted only 40-hex ids | `2b7ea2ae5`: `transaction.COMMIT_ID_RE` (40 or 64 hex) used by sidecars, abandon, cli oid checks; build-output ids and `source_commit` deliberately left at 40-hex (output-id grammar) | `tests/test_release_tag_absence_proofs.py::test_rel14_sidecars_accept_sha256_object_ids_like_sha1`, `::test_rel14_sidecars_still_reject_malformed_object_ids` | predecessor planted a temporary `COMMIT_ID_RE` edit, restored |
| REL-04 | main moving during the gate lost the release's promotion (fast-forward only) | `f1228d288`: `transaction.promote_workspace` merge-promotes (`_merge_origin_main_into_candidate`, `--no-ff` named `Merge origin/main into release candidate <tag>`, max 3 attempts, never force/rebase; stops on conflict, on origin/main touching the project paths, on unknown paths; non-race push failures fail at once) | `tests/test_release_end_to_end_real_git.py::test_rel04_main_advancing_during_the_gate_is_merged_not_lost`, `::test_rel04_main_advance_touching_the_project_path_stops_with_recovery`, `::test_rel04_merge_conflict_is_aborted_and_the_candidate_is_left_clean`, `::test_rel04_promotion_gives_up_after_bounded_attempts` | successor: plant `if True:` at the non-fast-forward marker check in `promote_workspace` -> all 4 FAIL; restored |
| REL-05 | post-tag build failure left the tag, blocking resume; recovery text promised inspection/resume | `f1228d288`: `_run_tagged_build_and_publish` runs build and push as separate steps; a build failure runs `_rollback_unpublished_release_tag` (pinned remote+local delete, absence proof, resume command); once `push` began, `_report_publication_started` prints tag, object id, exact recovery, no rollback; launcher message reworded | `test_release_end_to_end_real_git.py::test_rel05_failed_build_rolls_the_tag_back_and_resume_proceeds`, `::test_rel05_failed_publish_keeps_the_tag_and_prints_recovery` | successor: replace the rollback call with a bare `raise` -> first test FAILS; restored |
| REL-06 | a fetch failure in the best-effort local-main sync escaped and hid the retained-candidate path | `f1228d288`: `_sync_local_main_result` catches fetch failure (false result + warning); the "retained <path>" line is printed before the sync | `test_release_end_to_end_real_git.py::test_rel06_sync_fetch_failure_still_reports_the_retained_candidate` (real subprocess run, origin made unreachable by the gate) | successor: narrow the `except` to `KeyboardInterrupt` -> FAILS (WARN missing, `[ERROR] Command ... git fetch` instead); restored |
| REL-13 | the sync guard counted ignored files, so every vbpub release warned | `f1228d288`: no `--ignored`; ignored files block only where origin/main would add a file at an ignored path (`_ignored_paths_origin_would_overwrite`). Successor also corrected the dirty-reason text, docs and README that still claimed ignored files block | `tests/test_release_transaction.py::test_rel13_sync_local_main_ignores_ordinary_ignored_files`; end-to-end `test_rel15_fresh_release_end_to_end` (the seed project's `logs/`, `artifacts/`, `dist/` are ignored outputs and local main must sync) | successor: re-add `--ignored` -> both FAIL; restored |
| REL-15 | no release test used real git end to end | new `tests/test_release_end_to_end_real_git.py` (11 tests): real launcher + real child as subprocesses via `CMRU_BIN`, local bare origin, fake fast gate/build/publish steps, scriptable gate hook. Cases: fresh release, main advancing (merge-promote), project-path stop, conflict, bounded attempts, build failure + rollback + `--resume`, publish failure keeps the tag, sync fetch failure, plus CLI-01, REL-08, m24 | see the file | table below |
| m24 | no test noticed if the launcher stopped writing the pre-attempt `.tags.json` snapshot | `tests/test_release_end_to_end_real_git.py::test_m24_launcher_snapshots_origin_tags_before_the_attempt` (real run, asserts the sidecar content equals origin's tags) | successor: gate the `write_release_tag_snapshot` call with `and False` -> FAILS; restored |
| CLI-01 | `status` called `_configure_native_release_logging`: truncated `cmru.release.log`, dup2'd stderr | `53584bb0a`: only `verb == "release"` configures native logging; `--log-append`/`--show-run-details` removed from `status` (SPEC table, README updated) | `test_release_end_to_end_real_git.py::test_cli01_status_does_not_touch_the_release_log_or_redirect_output` (sentinel text survives; both flags rejected with exit 2); `tests/test_release_coverage_gaps.py::test_cli_status_from_console_entrypoint_does_not_configure_native_logging` (the old test asserted the bug; inverted) | successor: restore the unconditional call -> FAILS (log truncated, replaced by the status table); restored |
| REL-08 | the docs never warned against merging the candidate branch into main (root cause of REL-02) | `53584bb0a`: warning in `docs/RELEASE-TRANSACTIONS.md`; the generated release-inputs commit carries a `Cmru-Release-Candidate: <tag>` trailer (`changelog.pending_release_tag`, falls back to the project name for no-tag projects) | `test_release_end_to_end_real_git.py::test_rel08_release_inputs_commit_carries_the_candidate_trailer` | successor: disable the trailer -> FAILS; restored |

### Plant/revert table (successor, hand-planted, each restored and the diff re-checked clean)

| Fix | Plant | Tests that failed | After restore |
|---|---|---|---|
| REL-04 | `if True:` replacing the `_NON_FAST_FORWARD_MARKERS` check in `promote_workspace` | 4/4 `rel04` tests | 9/9 pass |
| REL-05 | rollback call replaced by bare `raise` in `_run_tagged_build_and_publish` | `test_rel05_failed_build_rolls_the_tag_back_and_resume_proceeds` | pass |
| REL-06 | `except KeyboardInterrupt` in `_sync_local_main_result` | `test_rel06_...` | pass |
| REL-13 | `--ignored` added to the sync status call | `test_rel13_...` and `test_rel15_fresh_release_end_to_end` | pass |
| m24 | `write_release_tag_snapshot` call skipped | `test_m24_...` | pass |
| CLI-01 | unconditional native logging for status | `test_cli01_...` | pass |
| REL-08 | trailer disabled | `test_rel08_...` | pass |

REL-04's conflict and project-path-touch stop cases: the path-touch case runs end to end through `cmru release`. A conflict
cannot be produced end to end in a single-project transaction (the candidate only changes files under the project path, and a
path touch stops first), so the conflict and bounded-attempt cases drive real `transaction.promote_workspace` against a real bare
origin with `project_paths=("demo",)`. Note: the pre-existing `test_promote_workspace_does_not_start_a_rebase_on_a_concurrent_conflict`
passes `project_paths=()`, so it now stops at "paths unknown", not at a conflict; the new conflict test is the real one.

## Not in scope of this package (left untouched)
REL-07 (`cmru.toml` resolution), REL-09 (KI-35 retire/sweep), REL-11 (`config.py`): other packages per the program plan.

## Test and gate results
- Full suite, no `--maxfail`, under the test lock: **2913 passed, 10 skipped, 20 subtests passed** (last run after the final edit,
  before the REPORT commit; the REPORT adds no code).
- Gates (from `cmru/`, through the gate lock, verdict read in a separate step): see the section at the end of this file.

## Deviations
- **Predecessor, disclosed:** the REL-02/REL-10 tests were appended to `tests/test_changelog.py` with a shell heredoc
  (`cat >> ...`), against the Edit/Write-only rule. The content is plain test code; reviewer, please check it. The predecessor's
  `COMMIT_ID_RE` plant used a temporary Edit (restored, verified).
- Successor: all repository files were changed with Edit/Write. Scratch repositories used to prototype the end-to-end harness were
  built with shell commands under the session scratchpad (outside the repository).
- Successor's first commit (`9eaafed42`) carries the trailer `Co-Authored-By: Claude Sonnet 5.5` (from the harness's attribution
  reminder); later commits use the repo's `Claude Sonnet` trailer. No amend was done.
- The end-to-end harness uses a project in a subdirectory (`demo/cmru.toml`). A project rooted at the repository root fails
  `_commit_prepared_generated` ("prepare changed undeclared paths: CHANGES.md") because the declared path becomes `./CHANGES.md`
  and the changed path is `CHANGES.md` (`_is_declared_generated` compares strings). Not fixed here; a root-level project with a
  generated changelog is unusual, but the controller may want to file it in the cmru backlog.
- Existing tests were edited to match the new behaviour: `test_cli_sequential_no_build_adversarial.py` (build and push are two calls),
  `test_noncli_final_branches.py::test_promote_fails_closed_on_a_non_fast_forward`, `test_release_candidate_changed_line_gaps.py`
  (63-hex is the malformed id), `test_release_transaction_final_adversarial.py` (a fetch failure is a false result),
  `test_release_transaction.py` (dirty-reason wording, `test_rel13_...`), `test_release_coverage_gaps.py` (status no longer configures logging).

## Carry-forward for the controller (REL-10 / 6.0.0 notes)
The hand-written `[Unreleased]` prose was emptied by `68a03b4fe` (KI-30 refuses a non-empty `[Unreleased]` in a tagged release), so
it is NOT in the working-tree `cmru/CHANGES.md`. Recover it with `git show 68a03b4fe^:cmru/CHANGES.md` (lines 5-55):
Added (versions init/resolve/check, shipped/all discovery + extras, rolling OCI tag checks), Changed (`[versions]` config), Fixed
(about 25 bullets: KI-53 wheel-builder worktree-root mount, sibling cli-extended/worktree source roots, mutation base = previous
tag, Go pseudo-versions, registry auth scoping, cleanup/abandon/tag-recheck hardening, retained-publication binding,
enrollment-lane prerequisites), Testing (about 7 bullets). The regenerated `[6.0.0]` section lists most of those commits only as
subjects, so the controller should fold the prose bullets into the 6.0.0 release notes.

Per the implementer rules, W0-REL does not add lines to `CHANGES.md` (the `[Unreleased]` section is now empty by design). Lines for
the controller to fold into 6.0.0 for this package: merge-promote when main advances (REL-04); automatic tag rollback after a failed
build and exact post-publication recovery text (REL-05); fetch failure in the local-main sync is a warning (REL-06); ignored files no
longer make the sync warn (REL-13); `cmru status` no longer touches `cmru.release.log` and drops `--log-append`/`--show-run-details`
(CLI-01, a user-visible flag removal); release-inputs commits carry `Cmru-Release-Candidate: <tag>` (REL-08); legacy abandon and
64-hex object ids (REL-03, REL-14); stale generated changelog section regenerated and non-empty `[Unreleased]` refused (REL-02, REL-10).
