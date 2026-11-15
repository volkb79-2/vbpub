<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/rigor-merge-prep-REPORT.md -->

# rigor merge-prep report (2026-11-05) — also the CONTINUATION

Nothing pushed, merged into main or tagged. All work is on local branches/worktrees under <local-path>

## Done

1. **a1 / a1a (assay export schema)** — DONE, gate pending.
   - main merged into rigor-a1-20261105 (merge commit 8daf59e6c); backlog conflict (B225/B226 on main) resolved by keeping both. Id B219 -> B227 in backlog, assay/CHANGES.md, assay/docs/CONSUMERS.md.
   - rigor-a1a-20261105: merged rigor-a1 (e0c4d31a2), B224 -> B228 (backlog, CHANGES, CONSUMERS); main already included (merge main = "Already up to date"). CHANGES has one `[Unreleased]`; the duplicated A-1 bullet from the merge was collapsed. Remaining text "B219..B224" in B225/B226 notes refers to the other branches' ids and was left alone.
   - a1a contains a1, so merge a1a only (a1 is a strict ancestor).
   - Gate: assay lane `tester-unified`, remote-gate run id `a1a-prep` on r1002 (sha e0c4d31a2d5d, project dir assay). STILL RUNNING when this was written. The remote-gate host configuration is private and was not copied.
2. **c13 (cmru record_step)** — DONE locally. `git rebase main` on rigor-c13-20261105; the one CHANGES.md conflict (main already released the `local_baseline_facts` bullet) resolved in main's favour; stale 879f520ae skipped. Now 2 commits on main (5b12d429e, cb7769436). Branch ref rewritten locally only (never pushed). cmru gate lane NOT yet run.
3. **c2 (rigor asset)** — DONE code + focused tests; gate NOT yet run. Rebased onto the new c13 (25ec3b93c, 5107a578e), then fix commit 3aca74e64.
   - Findings: `gh api repos/volkb79-2/vbpub/immutable-releases` -> `enabled:false`; all recent releases `immutable:false`, so nothing is broken today. GitHub docs: once published an immutable release's assets cannot be modified or deleted; the recommended order is draft, attach assets, publish.
   - Fix (fail closed, no draft-first rewrite): `publish_release_body` refuses before any write when the repo setting is enabled (`GitHubReleases.immutable_releases_enabled`, unknown/403/404/garbage = not enabled); `_attach_exact_assets` refuses before the first asset delete when the release record reports `immutable: true`. Message names the cause and says no asset was changed. Draft-first publication is NOT implemented (it would rework the read-back machinery of `_write_release_body`); the delete-then-upload window on non-immutable repos remains, documented in cmru CHANGES.
   - Tests: 4 new test cases (5 parametrized) in cmru/tests/test_rigor_publish_c2.py; `tests/test_rigor_publish_c2.py` 25 passed locally (`--cli-case-partial`). 100% coverage of the new lines is by construction (every branch exercised) but was NOT confirmed by the coverage lane yet.
4. **rr2a** — main merged cleanly into rigor-rr2a-20261105 (no conflicts, merge commit on top of 76f99a1f5). Gate re-run NOT yet done (one lane at a time; a1a-prep occupies it).

## FINAL VERDICTS (supersedes "Still to do" below)

| Lane | Branch / sha | Host / run id | Verdict line |
|---|---|---|---|
| cmru coverage | rigor-c2 3aca74e64 (contains c13) | r1002 c2-cov | `lane coverage verdict PASS; exit_code 0` |
| rigor-record r0-r1 | rigor-rr2a 810715078 (main merged) | r1002 rr2a-merge | `lane r0-r1 verdict PASS; exit_code 0` |
| assay tester-unified | rigor-a1a e0c4d31a2 | r1002 a1a-prep | `ERROR; exit_code 2` (inconclusive: only `ASSAY_SQL_INCONCLUSIVE`, image `postgres:18-alpine` absent on r1002; gate container exit 0, i.e. all pytest incl. analysis + pyflakes green) |
| assay tester-unified | rigor-a1a e0c4d31a2 | v1001 a1a-prep2 | `FAIL; exit_code 1`: 11030 passed, 1 failed: `gate/tests/test_self_qualification_container.py::test_outer_runner_launches_bounded_cgroup_visible_container_and_reads_job_status[b110-screen...]` ("missing required environment fact ASSAY_B105_GATE_EXPECTED_TREE"; log shows `worktree-untracked-by-assays-own-query`) |
| assay tester-unified | main 3cc39b32d baseline | v1001 a1a-baseline-main | `NOT_RUN; exit_code 3` (lock-busy: another agent holds assay-self-qualification) |

Reading of the a1a result: the same test file ran green on r1002 (gate container exit 0) at the same sha, and the branch touches no launcher code (export, backlog, docs, tests only), so the v1001 failure looks host/runner-dependent. It is NOT proven (baseline on main could not run: lock-busy). Remaining to be airtight: one clean `tester-unified` PASS needs a host that has `postgres:18-alpine` (pull it on r1002) or a re-run on v1001 when the lock is free, ideally with main as control. No fix was made to the branch (nothing in it implicated).

## Merge order (final)

1. rigor-a1a-20261105 (carries a1) — caveat above; controller's call whether the r1002 green-except-SQL evidence suffices.
2. rigor-c2-20261105 (carries c13) — coverage PASS.
3. rigor-rr2a-20261105 — r0-r1 PASS after main merge; squash on merge.
(rgd already merged; not listed.)

## Superseded: Still to do

- Wait for/collect a1a-prep verdict.
- cmru coverage lane for c2 (contains c13): `remote-gate.py run auto <local-path> <coverage-lane> --project-dir cmru` (lane name: see cmru/run-gate.toml; the brief called it the coverage lane).
- rr2a gate: lane r0-r1, `--project-dir` per rigor-rr2a-CONTINUATION.md CHECKPOINT 8 (libraries/rigor-record), worktree <local-path> (committed, clean).

## Merge order for the controller

1. rigor-a1a-20261105 (carries a1; after its gate is PASS). It also contains the rr1 commit d77742a6f.
2. rigor-c2-20261105 (carries c13; merge after the cmru coverage lane PASS). Do not merge c13 separately unless wanted: c2 contains it.
3. rigor-rr2a-20261105 — squash on merge (review M6: 29 wip commits); contains rr1/rr2/rr3. NOTE a1a also contains rr1's commit d77742a6f; merging a1a first then rr2a is conflict-free per the review's throwaway merge.
(rgd is already merged into main, selftest PASS rgd-selftest-3 — not part of this list.)

## Blockers / notes

- rr2a review findings are already fixed at tip 76f99a1f5 (gate PASS rr2a-fix-1); only the post-merge gate re-run is outstanding.
- Backlog ordering in assay 4-backlog.md: B225, B226 then B227, B228 on a1a; in a1 alone B227 precedes B225 (cosmetic).
- Immutable-release handling is fail-closed only (see c2).
