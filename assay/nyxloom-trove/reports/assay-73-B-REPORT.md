# assay 7.3.0 package B report (B136, B137, B146, B138, B139)

Branch `assay-73-B` from `b2c7522b6`. Not pushed, not released. A fresh reviewer must verify; this report claims only what was run.

## Key finding: four of five items were already on `main`

`git log` shows commit `9ae442998` ("bundle dstdns backlog fixes for v14", 2026-10-01) is an ancestor of `b2c7522b6`. It already implements B136, B137, B138 and B139 (and B140-B142, package A's items), and it moved the verdict schema to **v14** (`VERDICT_SCHEMA_VERSION = 14`). Only the backlog statuses and the `plan-estimate` text in the task description were stale. Consequences for the controller:

* The "v14 / 8.0.0 wave" notes in the backlog refer to a larger plan, but v14 as a verdict schema is already on `main`. A 7.3.0 release from `main` would therefore ship schema v14 (a hard cut, per the `9ae442998` BREAKING CHANGE footer), not a v13-compatible patch. Decide whether that is intended.
* Package A (B140/B141/B142) appears to be in the same state; check before dispatching more work for it.

## Per item

| Item | State found | What I did |
|---|---|---|
| B136 | Implemented in `9ae442998`: `assay plan` JSON carries `lane`; `plan_estimate` refuses a lane mismatch by name and a plan without a lane | Verified by running `analysis/tests/test_analysis_plan_estimate.py`; plant (`if False and run["lane"] != plan["lane"]`) failed 2 tests, reverted; status set IMPLEMENTED |
| B137 | Implemented as contract (b): unsupported plan carries lane + named reason, `plan-estimate` forwards it, CONSUMERS "Size an ingested JavaScript R2 campaign (B137)" | Plant (`"reason": "unsupported"` in `_cmd_plan`) failed `test_assay_plan_names_why_an_ingested_lane_cannot_be_planned` and one plan-estimate test, reverted; status IMPLEMENTED. Contract (a) (estimate from a Stryker report count) deliberately not built; the report count is not established to equal the run's candidate count |
| B138 | CONSUMERS has "Migration notes (v12 to v13)" and "(v13 to v14)"; `test_consumers_covers_the_latest_two_verdict_schema_cuts` plus must-fail control exist | Ran those tests (pass); status IMPLEMENTED |
| B139 | Canonical skill `assay/.claude/skills/assay-cli/SKILL.md`, README and DESIGN-GUIDE already state JS R2 as ingestion only; test with must-fail control exists | Ran (pass); status IMPLEMENTED. Real `~/.claude/skills` not touched |
| B146 | Not implemented | Implemented (below) |

## B146 (new code)

* New `src/assay/failure_summary.py`: `first_failing_test(*texts)` reads the retained stdout/stderr tail. Producers: pytest short-summary `FAILED`/`ERROR` node ids (fallback `____ name ____` header), `go test` text `--- FAIL:`, `go test -json` `Action: fail` + `Test`, vitest ` FAIL  a > b`, jest `● a › b`. Earliest hit across producers wins; names are single-line and capped at 200 chars; deeply nested JSON lines are skipped (`RecursionError` caught, required by `test_untrusted_json_parse_sweep`).
* `cli._print_run_summary` adds `  R0: FAIL (first failing test: NAME)` after the headline when the verdict is not PASS, no R0 claim is PASS, and a name is found.
* **Design decision to review:** the headline is NOT rewritten. The headline is the verdict's own `outcome/reason` pair and the exit code; the real `NO_MEASUREMENT` case behind B146 is a failing suite that also dirties the tree (`runner.py` keeps the R0 command result as FAIL/COMMAND_FAILED but the claim is `NO_MEASUREMENT/DIRTY_TREE`). The extra line states the measured failure. No verdict field or schema change. I could not find a captured reproduction of the original dstdns observation, so this interpretation of "reports FAIL instead of NO_MEASUREMENT" is inferred from the code paths.
* Limits: the name is the first failure inside the bounded tail the verdict retains (not the true first if the tail was cut); SQL R0 output and `result_report` JSON files are not read.
* Tests: `tests/core/test_cli_run_failure_summary.py` (end to end through `main` with a real child process: first-failure order, dirty-tree case, genuine `NO_MEASUREMENT` unchanged, unrecognised output adds nothing, PASS adds nothing, passed-R0-claim guard; plus per-producer reader cases). Coverage of `failure_summary.py`: 100% line and branch (measured with `--cov-branch`).
* Plant: `min` to `max` in the earliest-hit selection failed `test_the_earliest_recogniser_hit_wins_across_producers`; reverted.
* Docs: CONSUMERS paragraph at the "Where it goes" bullet; CHANGES `[Unreleased]` / Fixed entry; backlog B146 IMPLEMENTED.

## Gate evidence

Host PSI cpu some avg10 was about 4-5 before runs; all runs serial under the flock with `nice -n 19 ionice -c 3`, foreground.

* Full unit suite (`python -m pytest tests analysis/tests -q`): first run 137 failed, 7493 passed. 136 of the 137 fail identically on an untouched checkout of `b2c7522b6` (checked by rerunning exactly those ids in a temporary worktree, since removed); the cause visible in the logs is `cannot observe cgroup v2 process and memory limit events ... the cgroup namespace hides the process's parent cgroups` (B145 fail-closed on this devcontainer) plus the judge-provenance notice for an uninstalled source tree. The 137th was mine (`test_untrusted_json_parse_sweep`, an unguarded `json.loads`); fixed with `RecursionError` and a test. After the fix I reran only the focused set (failure-summary, sweep, import contracts, docs checks, plan-estimate): all pass except the one baseline cgroup failure `test_op5_...`. **I did not rerun the whole suite after the fix**; the controller's registered gate covers that.
* So this devcontainer cannot execute native R2 tests; those 136 are unverified here either way. `tester-unified` and self-qualification lanes were not run (per instructions).

## Review round 1

1. `test_a_passed_test_is_never_named` added. Plants `PASSED` in `_PYTEST_SUMMARY` and `"pass"` accepted in `_go_json` each failed that test; both reverted.
2. `test_a_passthrough_secret_in_a_failing_id_is_masked_in_the_summary` (real `main` run, `X_PASSWORD` passthrough): secret absent from stdout and stderr. Plant `safe = raw` in `runner._bounded_tail` (removing the source masking) failed it; reverted.
3. CONSUMERS reworded. The coordinator asked for "up to four lines"; the real maximum is five (headline, optional `R0: FAIL`, commit, argv, optional appended-argv line, the last already conditional before B146), so the text says three to five and warns against fixed line positions.
4. Parser: pytest ids now run to ` - ` or end of line (`t.py::test_x[a b-c]` kept whole); ANSI escapes are stripped before parsing; the old `[0m` pin is now `a > b`; tests added for spaces and for coloured pytest/go/vitest/jest output.
5. `test_a_real_file_root_admits_only_that_file_not_its_package_siblings` (real files, parametrized over `source_root_files` of `("pkg/owned.zzz",)` and `()`) in `tests/core/test_evaluate_language_free.py`. Plant treating a file root as its parent directory failed 4 tests including both new cases; reverted.

`failure_summary.py` still 100% line and branch (focused run). Full `tests` plus `analysis/tests` run: 136 failed, 7507 passed; the failing ids are a subset of the baseline set (zero new ids by `comm`), all cgroup-related as before.

## Files

`assay/src/assay/failure_summary.py` (new), `assay/src/assay/cli.py`, `assay/tests/core/test_cli_run_failure_summary.py` (new), `assay/docs/CONSUMERS.md`, `assay/CHANGES.md`, `assay/nyxloom-trove/4-backlog.md`, this report.
