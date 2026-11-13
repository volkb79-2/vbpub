<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/DSTDNS-R2-PILOT-CONTINUATION-BRIEF-2026-11-04.md -->

# dstdns R2 pilot: continuation brief (written 2026-11-04 ~00:50Z)

Controller-only Dstdns scratch contains identity helpers, runner support, lane plans and raw evidence. The identity configuration was not copied; see the `dstdns-r2/` disposition in the [persistence inventory](../persistence/PERSISTENCE-INVENTORY-2026-11-12.md#purge-readiness).

Pilot lane: `inf_events_obs_r2` (assay.toml:5326ff; 32 candidates; libs/common redis_events + skywalking_native; whole_target; tests tests/unit 4 files).
Recipe: dstdns commit c25c53d139eae2c8656c3bfa676ffd3ddcdcf061 (main) via bundle -> `~/checkouts/<sha>` on runner host, scratch overlay COMMIT on top (run-gate.toml replaced by gen-overlay.py output: ephemeral `test-runner` env, image dstdns/test-runner:latest loaded via docker save/load, `user="invoker"`, assay 11.0.0 pyz + sha sidecar, `resource_observer="cgprofile"`, memory 2g, pids 8192). Run: `run-r2-campaign.sh <name> <sha> . inf_events_obs_r2 --jobs N` (runner-scripts, exit-4 resume loop).
Results so far:
- v1001 jobs 1 (contended lightly by another agent's proof): exit 1 FAIL MUTANTS_SURVIVED, 32 cand, 29 killed / 3 survived / 0 other, wall 15m37s (5 attempts, assay lane budget 3m per attempt), per-cand median 17.5 s (materialize 9.2 s + command 7.7 s), peak RSS 363 MiB; raw candidate records remain in the controller-only Dstdns scratch described in the [persistence inventory](../persistence/PERSISTENCE-INVENTORY-2026-11-12.md#purge-readiness).
  Survivors: skywalking_native.py:203 bool-const-flip True->False; :601 boolop-swap Or->And; :636 boolop-swap Or->And.
- jobs 3 needs `jobs_max>=3`: run-gate refuses ("jobs value 3 exceeds Assay lane mutation.jobs_max (2)") -> second overlay checkout on r1002 `~/checkouts/bfc5c6b6089e8153170dc8f0081edda44139f4e5` (assay.toml `jobs = 2` -> `jobs_max = 6` for this lane).
- v1001 is busy with another agent's timing-sensitive `jobs-window` queue (hours): do NOT run there. Local host is under heavy PSI (cpu 45): do not run there.
- r1002: chain job `dstdns-pilot-r1002-chain` (j3 refused, then j1 running; campaign dirs `~/campaigns/dstdns-pilot-r1002-j1`). PSI rule: wait if some avg10 > 25.
NEXT: (1) when r1002 j1 done (`final-exit`), run `bash ~/runner-scripts/run-r2-campaign.sh dstdns-pilot-r1002-j3b bfc5c6b6089e8153170dc8f0081edda44139f4e5 . inf_events_obs_r2 --jobs 3` as `run-job dstdns-pilot-r1002-j3b -- ...` (API check first); (2) pull `.assay/` + campaigns, summarize both progress streams, diff buckets by candidate_id; (3) write backlog entries on worktree `<local-path>` (branch ops-dstdns-r2-20261104; ids RG-154.., B213.., CIU-159..; `git commit --only`, trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; no push); (4) write the project-owned pilot report (lane inventory: 80 R2 lanes, 69 whole_target = 1615 candidates incl. cw2b_schema 201; 11 changed_lines; 16 lanes with 0 candidates); (5) final message.
Filed-defect candidates: RG-154 (lane header budget vs assay lane budget), B213 (plan warns when budget < estimate; per-attempt baseline overhead), materialize 9 s/cand under strict io_sync (compare with CoW jobs 3), doc recipe for consumer-image ephemeral env.
