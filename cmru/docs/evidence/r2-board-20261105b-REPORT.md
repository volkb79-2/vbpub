<!-- origin: 2026-11-05; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/r2-board-20261105b-REPORT.md -->
<!-- relation: R2 board status; verbatim source text follows -->

# R2 board 2026-11-05b, r1002 + v1001 (host-compute operator, host clock 08:45-08:55Z)

Identity checks (`r1002_api_check; v1001_api_check` plus server-id guard) passed before every write. <protected-host>, firewall, live-edge untouched. No repo edits, no controller checkout actions (checkouts are clones of tagged commits on the hosts; the branch/commit requirement did not apply because nothing in the repo was changed).

## 1. State found
- r1002: completely idle (load 0.1, no run-gate container). The 4 `debian-install-vm-harness-*` containers (3ae23bf6ce64, 28c5d296013b, fc8324313087, c74a9dc2fa4a, image debian-install-vm, "Up 7 hours") were NOT touched; listed here for the controller: they are not yet shown idle >1h by any evidence I collected (they only report uptime), so no removal.
- v1001: running only the OLD cmru R2 `cmru-r2-20261105` (commit ba601c9e5, lane assay-first-release-r2, 1 worker, 19006 progress events, started 2026-11-04 07:38Z). Load ~1.2.
- Last ciu R2 on r1002 (`ciu-main-r2-20261105b`, 70/191) died with ENOSPC (root disk), not a product result.
- Images: r1002 has `tester-unified:r25-d38b3324fba5` (run-gate 25.1.1) so lanes needing >= 25.1.1 start there. v1001 has only `tester-unified:local` = run-gate 25.0.0 (e92d43b28b00), BELOW 25.1.1 (RG-164 breaks ciu; pwmcp/cmru unverified). Per instructions I did not load a newer image there: no tester-unified lane was started on v1001, only dstdns lanes (image dstdns/test-runner). REPORT: to put tnt-free lanes (cmru 7.2.2, run-gate 25.1.2, ciu) on v1001, load `tester-unified:r25-d38b3324fba5` (3 GB; 8.7 GB free on v1001) with `docker save | docker load` and copy `run-lane-r25d38.sh`/`run-r2-campaign-r25d38.sh` from r1002.

## 2. Housekeeping on r1002 (disk was 90%, 3.9 GB free; now 84%, 6.2 GB free)
Removed by exact path (my own finished artifacts, closeouts already in `r1002/r2-closeouts-20261105/`):
`incoming/tester-unified-r25-tag.tar.gz`, `incoming/tester-unified-e92d43b28b00.tar.gz` (images already loaded), `checkouts/5e669f9dbc2d13991ae955ab8e0087e4e1cdc48a`, `checkouts/60bb71a1cd360a85b6ab7e00d688f815d27c0f8b`, `checkouts/b0a3a0aab1d277954f312a38a3e26d25360ec1d8`. Kept: pwmcp failed-baseline evidence checkouts a4f28bdf/fb1fddaf, db0355df7 (aborted ciu R2).

## 3. What runs now (latest releases only)
| Host | Job (`~runner/jobs/...`) | What | Commit | Base | jobs | Status / ETA |
| --- | --- | --- | --- | --- | --- | --- |
| r1002 | `ciu-v7230-r2-sup` (campaign `ciu-v7230-r2`) | ciu 7.23.0, lane `ciu`, run-gate 25.0.2 gate-tools + image r25-d38 (run-gate 25.1.1) | b9df2475c (ciu-v7.23.0) | ciu-v7.22.0 (df317057f) | 1 (lane ceiling) | baseline then 191 candidates at ~0.8/min, ETA ~4 h |
| r1002 | `pwmcp-r4-r2-sup` (campaign `pwmcp-r4-r2`) | pwmcp 1.64.1-r4, lane `r2` (whole source). R0 baseline passed (no leaked-process-tree; candidates running: 493 total) | b1659836e (pwmcp-v1.64.1-r4) | - | 3 (jobs_max) | 8/493 at 1.3/min, ETA ~6 h. r4 leak fix VERIFIED at the R0 baseline |
| r1002 | `r2-r1002-chain2` (waits for ciu EXIT) | then run-gate 25.1.2 lane `assay-r2` (jobs 2), then dstdns 2-job lanes (12, jobs 2) | run-gate: e65788162 (run-gate-v25.1.2); dstdns b0d031c7 | run-gate-v25.1.1 (b0a3a0aab) | 2 | queued, starts after ciu |
| v1001 | `cmru-r2-20261105-sup` (pre-existing, not mine) | cmru old commit R2, 1 worker | ba601c9e5 (STALE: cmru 7.2.2 changed ~1000 lines of src since) | - | 1 | running 22 h; left alone (no replacement possible on v1001, see image note). Controller decision: stop it and run cmru-v7.2.2 `assay-first-release-r2` on r1002 or on v1001 after image load |
| v1001 | `r2m-v1001-b0d031c7-qA` | dstdns whole-target lanes, first 24 of the 3-job group, main b0d031c7 | b0d031c7ec737dce0bc | - | 3 | lanes 1-5 min each, all exit 0 so far; ETA ~1-1.5 h |
| v1001 | `r2m-v1001-b0d031c7-qB` (waits for qA EXIT) | remaining 24 3-job lanes (jobs 3), then the 12 2-job lanes (jobs 2) | b0d031c7 | - | 3 then 2 | queued, ETA +2 h |
| not started | tnt 0.3.0 | no assay/R2 lane in truenas/tnt/run-gate.toml (only r0-r1, ux-focused) | - | - | - | n/a |
| not started | assay | 12.1.0 not released | - | - | - | skipped as instructed |
| not started | dstdns changed-line lanes (p166, p167, p194, p214, p221) and 9 lanes of the old list that no longer exist at b0d031c7 | need release base / renamed | - | - | - | controller |

Lanes list for dstdns: whole_target lanes from `dstdns-r2/work/r2lanes.txt` filtered to those present in b0d031c7 `run-gate.toml` (60; 12 with judge.mutation.jobs_max 2, 48 with 3). Queue script `~runner/runner-scripts/dstdns-r2m-queue.sh <commit> <jobs> <lane>...` (memory/IO PSI gate only); queue logs `~runner/campaigns/dstdns-r2m-queue-<host>-b0d031c7.log`.

## 4. Monitoring (host clock; CPU of 400, PSI avg10)
| UTC | host | CPU | cpu some | mem some | io full | swap |
| --- | --- | --- | --- | --- | --- | --- |
| 08:49 | r1002 (ciu+pwmcp baselines) | 392% | 13.8 | 0.0 | 0.0 | 101 MB |
| 08:52 | r1002 | ~400% (top, 2 cores 100%) | 15.8 (full io 0.0) | 0.0 | 0.0 | ~100 MB |
| 08:49 | v1001 (cmru + qA) | 352% | 17.2 | 0.1 | 3.0 | 161 MB |
| 08:52 | v1001 | ~350% | 13.4 | 0.0 | 1.3 | ~160 MB |
Disk: r1002 root 84% (6.2 GB free), v1001 78%. Watch r1002 disk: ENOSPC killed an earlier ciu R2. No stop signal active.

## 5. Deviations / notes
- No Edit/Write on repos; host-side steps used ssh heredocs (clones, bundle copies, run-job). Bundles: `~runner/incoming/r2-20261105b.bundle` (ciu/pwmcp/run-gate/cmru tags), `~runner/incoming/dstdns-b0d031c7.bundle` on both hosts (also copied to v1001 incoming). My first qA launch had an empty lane list (stdin mistake); it exited immediately, job dir and log removed, relaunched correctly.
- remote-gate.py is for gate lanes; R2 uses the existing `run-r2-campaign-r25d38.sh` (r1002) / `run-job` scripts, not remote-gate.
- ciu `--base` is passed as the sha of ciu-v7.22.0 (tag not present on host).
- pwmcp r4 R2 on r1002 also uses the old gate-tools 25.0.2 wrapper with image 25.1.1 (as in the earlier campaigns).

## 6. Cancel (exact names, after identity check, as root)
1. `kill $(cat /home/runner/jobs/<job>/pid)` for the job (`ciu-v7230-r2-sup`, `pwmcp-r4-r2-sup`, `r2-r1002-chain2`, v1001 `r2m-v1001-b0d031c7-qA`, `r2m-v1001-b0d031c7-qB`), then the matching processes: `ps -eo pid,cmd | grep "[r]un-r2-campaign-r25d38.sh <campaign> "` (r1002) or `[d]stdns-r2m-queue.sh`.
2. `docker stop <container>` for the exact lane container. Since RG-242 lane containers are named `<repo>-run-gate-<project>-<lane>-<sha8>-<yymmdd-HHMM>-<ns9>` and labelled: `docker ps --filter label=run-gate.lane --filter label=run-gate.project=<project> --format '{{.Names}}'`; containers started by an older run-gate are `run-gate-<sha>-<lane>-<pid>-<epoch>` and have no labels: `docker ps --format '{{.Names}}' | grep '^run-gate-'` (containers are `--rm`).
3. Verify `~runner/jobs/<job>/EXIT` exists and no campaign process remains. State stays resumable in `~runner/checkouts/<sha>/.run-gate/assay-state/`.
Collect when done: verdict `<checkout>/<project>/.assay/verdict-<lane>.json`, campaign dir `~runner/campaigns/<campaign>/` (attempts.log, final-exit).

## 7. Update 2026-11-05 ~09:55-10:10Z (v1001 operator, Sonnet)
Identity checks (r1002_api_check, v1001_api_check, plus /etc/vbpub/server-id = 804027 on v1001) passed before every write.

| Host | Row | Detail |
| --- | --- | --- |
| v1001 | image loaded | `tester-unified:r25-d38b3324fba5` (id d38b3324fba5, run-gate 25.1.1 verified in container) streamed `r1002 docker save \| gzip -1 \| controller pipe \| v1001 gunzip \| docker load`; no tarball on any disk. v1001 root 83% (6.7 GB free) right after the load, 76% (9.1 GB free) later. `tester-unified:local` (25.0.0) left in place. |
| v1001 | OLD cmru R2 `cmru-r2-20261105` CANCELLED | commit ba601c9e5, lane assay-first-release-r2, started 2026-11-04T07:38Z. Progress at cancel: candidate 25 of 4517 (rate 0.1/min, ETA ~41000 min = ~28 days, i.e. unfinishable), 25324 progress events (attempts: 6334, 12671, 19006 events at attempt ends, attempt 4 was mid-run). Stale lane (old cmru + run-gate 25.0.0 image). Checkout `~runner/checkouts/ba601c9e52fb0cae19aa4d78bdb394fb1dce454b` and its `cmru/.assay` progress/state KEPT; campaign dir `~runner/campaigns/cmru-r2-20261105/` kept (attempt-04.log ends exit 143). |
| v1001 | NEW cmru R2 `cmru-v722-r2` (job `cmru-v722-r2-sup`) | cmru-v7.2.2 = fa48b35126e0fcf61b64ff9a10f1927fc5b5a063, lane `assay-first-release-r2`, `--jobs 1`, image r25-d38 (run-gate 25.1.1) via `run-r2-campaign-r25d38.sh`. Started 09:57Z; baseline (R0) running at 10:08Z, no candidate events yet. Container `run-gate-fa48b351...-assay-first-release-r2-90601-1793869170`. Cancel: kill `$(cat ~runner/jobs/cmru-v722-r2-sup/pid)` + its campaign pids, `docker stop` the exact container, then check the leftover `docker exec` / in-cockpit `run-gate` pid. |
| v1001 | extra lane (run-gate 25.1.2 assay-r2 / ciu) NOT started | CPU gate: ~390% busy (load 5.4, dstdns qA 3 jobs + cmru 1 worker; cpu some 9.7, mem some 0.0, io full 1.5, swap 110 MB used), no headroom below the ~400% target. |

Note: v1001 monitoring 10:08Z: cpu some 9.7, mem some 0.0, io full 1.5, swap ~110 MB; no stop signal.

### Deviations
- The old cmru campaign took more than the recipe: the wrapper/campaign pids (465288/465289/465300) were killed first, but the lane `docker exec` (1533323) and its in-cockpit `run-gate` (1533352) survived; `docker stop` of the exact container `run-gate-ba601c9e...-65115-1793853587` ended them (exit 143). Because the run-job wrapper was killed, `~runner/jobs/cmru-r2-20261105-sup/EXIT` was never written (job dir has only job.log, pid) and the campaign has no `final-exit`.
- The existing `r2-20261105b.bundle` is a thin bundle missing prerequisites on v1001; I made a bundle of `cmru-v7.2.2 ^ba601c9e5` on the controller in the session scratchpad (removed afterwards), piped it to `~runner/incoming/cmru-v7.2.2.bundle` on v1001 (2 MB, left there), and created the checkout by `git clone --no-checkout` of the ba601 checkout + fetch of the tag + checkout (origin points at the local ba601 checkout path). `~runner/incoming/r2-20261105b.bundle` (0.7 MB) and the two r25d38 scripts (`run-lane-r25d38.sh`, `run-r2-campaign-r25d38.sh`) were copied from r1002 to v1001 `~runner/` (new files there; r1002 unchanged).
- `--jobs 1` instead of the old `--jobs auto` (auto was 1 worker in practice) to keep the CPU footprint identical.
- No repo edits; <protected-host>, live-edge, r1002 jobs and disk untouched.

## 8. v1001 after reinstall (host-compute operator, Sonnet, 2026-11-05 ~13:45-15:15Z)
Identity (`r1002_api_check; v1001_api_check` + hostname/server-id guard) passed before every write. <protected-host>, live-edge untouched. r1002 only read, plus ONE kill (chain2 wait loop, pid 1369846, exact). Note: r1002 pin file is keyed by IP, so use `runner@<host-address>`; v1001 wrappers work as is. Credentials never printed.

### Prep done
- Images streamed r1002 -> v1001 (no tarball): `dstdns/test-runner:local` (337b913ee1a5), `tester-base:local` (ab4e04347a40); log `v1001/image-stream2-20261105.log`. `tester-unified:r25-d38b3324fba5` (= `:local`) was already loaded.
- Old-key pin: only `v1001/ciu-remote-test/.ciu.hosts.toml` (controller file, not repo) named it; known_host row updated to the new ed25519 key. No repo file names the old key, so no pins branch/commit. The controller's hashed known-hosts file was not touched.
- Checkouts on v1001 (`~runner/checkouts/<sha>`): cmru shard branch `ccd96cda6...` (and `81753544...`), run-gate-v25.1.2 `e6578816...`, dstdns `b0d031c7...`. Scripts `run-lane-r2502.sh`, `run-r2-campaign-r2502.sh` copied from `export-20261105/home/runner-scripts`.
- cmru shard lanes: branch `cmru-r2-shards-20261105` in `/workspaces/vbpub/.worktrees/cmru-r2-shards-20261105` (local, not pushed, NOT for merge, repo tests not run). 15 lanes `cmru-r2-s1..s15` (assay.toml) + `assay-r2-s1..s15` (run-gate.toml), all with `judge.mutation.candidate_fail_fast = true` (assay config.py in the checkout supports it; `assay plan` parses all 15). Candidate counts: s1 304 (config, release_preconditions, record_step), s2 302, s3 305 (gate_snapshot), s4 305 (versions), s5 304 (release), s6 300, s7 302, s8 305, s9 185, s10 526 / s11 432 / s12 421 (cli.py by operator: compare / boolop / const+falsy), s13 277 / s14 260 / s15 211 (transaction.py by operator). Sum = 4739 = full plan. s10-s12 exceed the 300 cap (operator slices cannot split finer without a run-gate `--shard` pass-through).
- cmru-first-release-r2 (single lane) was not changed in the branch beyond adding `candidate_fail_fast = true` there too.

### Baseline R0 result (`cmru-r0-baseline-20261105`, lane `assay-first-release`, run-gate 25.0.2 tools + image 25.1.1)
EXIT 1: verdict FAIL, `NO_MEASUREMENT/TARGET_NOT_MEASURED`: target `src/cmru/__init__.py` has zero executable lines in the coverage artifact (a whole-target floor refuses). This is a pre-existing config defect of cmru v7.2.2's `cmru-first-release` lane (`__init__.py` is in its targets); it is NOT a test failure and NOT in my shard lanes (none lists `__init__.py`, 0 candidates). Should be filed in cmru's backlog (KNOWN_ISSUES_TODO_BACKLOG.md). Because of it the shard queue is not gated on EXIT=0; each shard lane runs its own R0 baseline plus the fail-fast preflight (one extra no-coverage full-suite run, ~8 min) before candidates.

### Job table (v1001, 4 vCPU)
| Job (`~runner/jobs/...`) | What | jobs | Status / ETA |
| --- | --- | --- | --- |
| `r2-v1001-chain-20261105` | run-gate 25.1.2 lane `assay-r2` (campaign `rungate-r2-251-2`, base b0a3a0aab) then 12 dstdns 2-job lanes at b0d031c7 (queue log `~runner/campaigns/dstdns-r2m-queue-v1001-b0d031c7.log`) | 2 | assay-r2 candidate 4/51 at 15:05Z, ETA ~90 min, then dstdns lanes (1-5 min each earlier, ~1 h total) |
| `cmru-r2-shards-20261105b` | campaigns `cmru-r2b-s1..s8` (commit ccd96cda6), sequential, `--jobs 2` | 2 | s1 started 15:11Z; real rate to be measured after 20-30 candidates (est. ~170 s/candidate, ~21/h at 2 workers => ~14 h per 300-candidate shard; s1-s8 ~4.5 days if alone on v1001) |
| (killed, replaced) `cmru-r2-shards-20261105` | first queue on commit 81753544 without fail-fast | - | killed while waiting (EXIT 143), nothing lost |
| `cmru-r0-baseline-20261105` | see above | - | done, EXIT 1 |
| r1002 `r2-r1002-chain2` | CANCELLED (wait loop only, nothing had started; EXIT 143) | - | content moved to the v1001 chain |
CPU: 2 + 2 workers, ~350-400% target (~3.2 load at 15:11Z while only the chain + baseline canary ran).

### Monitoring rows (host clock UTC+1; PSI avg10; mem some / io full)
| UTC | host | load | mem some | io full | note |
| --- | --- | --- | --- | --- | --- |
| 13:51 | v1001 | 2.1 | 0.0 | 1.1 | baseline + assay-r2 starting |
| 13:55-14:03 | v1001 | ~3 | 0.0 | 0.1-2.3 | every 2 min, never above 2.3 |
| 14:11 | v1001 | 2.8 | 0.0 | 0.6 | chain + baseline canary |
| 14:05 | r1002 | 4.4 | 0.0 | 0.3 | ciu 171/191 ETA ~35 min, pwmcp-r4 running; root 86% |
No stop signal (mem some>25, io full>25, swap, OOM). v1001 root 11% used (135G free).

### Start commands for r1002 (queue ONLY after ciu finishes ~14:40Z and if capacity allows; r1002 root disk is 86%, ~5.6 GB free, check `df` first; pwmcp-r4 still uses 3 workers until ~20:20Z)
Suggested split: v1001 runs s1-s8, r1002 runs s9-s15 (cli.py/transaction.py slices, ~2430 candidates), or move the tail of v1001's queue there. From the controller:
```
source <controller-program-dir>/dstdns-r2/env.sh; r1002_api_check
SP=<scratchpad>; git -C /workspaces/vbpub/.worktrees/cmru-r2-shards-20261105 bundle create $SP/sh.bundle cmru-r2-shards-20261105 ^cmru-v7.2.2
r1002_ssh runner@<host-address> 'cat > ~/incoming/cmru-r2-shards.bundle' < $SP/sh.bundle
r1002_ssh runner@<host-address> 'set -e; umask 077; c=ccd96cda646015ed0c4af5b3c757e8ad94fb4688; git clone -q --no-checkout ~/checkouts/<any checkout with cmru-v7.2.2> ~/checkouts/$c; git -C ~/checkouts/$c fetch -q ~/incoming/cmru-r2-shards.bundle cmru-r2-shards-20261105; git -C ~/checkouts/$c checkout -q --detach $c; chmod -R go+rX ~/checkouts/$c'
r1002_ssh runner@<host-address> 'cd ~/runner-scripts; ~/bin/run-job cmru-r2b-shards-r1002 -- bash -c "for i in 9 10 11 12 13 14 15; do bash run-r2-campaign-r25d38.sh cmru-r2b-s\$i ccd96cda646015ed0c4af5b3c757e8ad94fb4688 cmru assay-r2-s\$i --jobs 2; done"'
```
(If the clone source lacks the base objects, make the bundle without `^cmru-v7.2.2`.) With CPU free only ~1 core on r1002 until pwmcp ends, prefer `--jobs 1` there until then.

### Cancel recipe (v1001, after identity check, as root or runner)
1. `kill $(cat ~runner/jobs/<job>/pid)` for the job, then the campaign pids: `pgrep -af "[r]un-r2-campaign-r25d38.sh <campaign> "`; for the chain also `[d]stdns-r2m-queue.sh`.
2. `docker stop` the exact container: `docker ps --filter label=run-gate.lane --format '{{.Names}}'` (RG-242 names/labels) plus, for pre-RG-242 containers, `docker ps --format '{{.Names}}' | grep '^run-gate-'`; then stop by name.
3. A killed wrapper writes no `final-exit`; state stays resumable in `~runner/checkouts/<sha>/.run-gate/assay-state/` (resume = rerun the same campaign name).

### Deviations
- Shards are module/operator lanes in a local branch instead of `assay --shard` (run-gate 25.1.2 has no pass-through); 15 lanes, not ~8; s10-s12 above the 300-candidate cap.
- candidate_fail_fast added per the coordinator finding; not measured yet (rate to be taken from `~runner/checkouts/ccd96cda.../cmru/.assay/progress-*.jsonl` after the first 20-30 candidates; resize the later shards from it). Pytest NOT narrowed per module (assay has no supported mechanism).
- Baseline gate dropped from the shard queue (see above); the queue was restarted on the new commit (old queue killed while waiting).
- Only s1-s8 queued on v1001; s9-s15 await the r1002 decision (commands above).
- dstdns lanes use gate-tools 25.0.2 with the 25.1.1 image (`run-*-r2502.sh` scripts) as in earlier campaigns; the dstdns checkout was built by hand from two bundles (not `sync-from-devcontainer`, so `~/src/vbpub-current` unchanged).
- Claude login on v1001 `runner` expired: ignored per operator decision.

## 9. Update 2026-11-05 ~16:00-17:50Z (r1002 operator, Sonnet): ciu 7.23.0 R2 result, tnt R2 prep, cmru shard defect
Identity (`r1002_api_check; v1001_api_check` + server-id guard) passed before every write. <protected-host> / live-edge untouched; v1001 read-only; no credentials printed.

### ciu 7.23.0 R2 (job `ciu-v7230-r2-sup`, commit b9df2475c, base ciu-v7.22.0): REAL VERDICT, no resume needed
EXIT=1 = `FAIL/MUTANTS_SURVIVED` (R0 PASS, R1 PASS, R3 PASS, R2 FAIL). Not infra: attempt 1 ran to the end (progress events 0->6083, 08:46Z-14:42Z), 191 judged: **153 killed, 38 survived, 0 crashed, 0 hung, 0 budget_exceeded, 0 equivalent**. campaign closeout says "incomplete" only because `coverage_not_reverified` (analysis blocker, lane verdict unchanged); pending=0. Evidence kept on r1002 `checkouts/b9df2475c.../ciu/.assay/verdict-ciu.json`; copy in `r1002/r2-closeouts-20261105/ciu-v7230/closeout.tgz`.
Survivors (file:line, mutation); paths under ciu/src/ciu/ (the line numbers are the verdict's `lineno` field):
- cli_registry.py 618 True->False; 930 True->False
- config_model.py 330 Or->And
- deploy.py 4432 True->False; 4447 False->True; 4464 False->True; 4517 True->False and False->True; 4528 Gt->GtE; 4529 Gt->GtE; 4753 True->False; 4756 None->[]; 4974 True->False and False->True; 4976 NotEq->Eq; 4978 Or->And; 4980 Gt->GtE; 4991 True->False and False->True; 5599 Or->And; 5604 True->False; 6588 True->False
- engine.py 456 True->False; 616 False->True; 643 False->True
- workspace_env.py 1028 True->False; 1029 True->False; 1030 False->True; 1034 Or->And (two candidates)
- worktree.py 6225 False->True; 7785 False->True; 7805 False->True; 8115 And->Or; 8170 Or->And; 8206 True->False; 8216 True->False; 8405 NotEq->Eq
(Clusters: deploy.py 4974-4991 and 4432-4529; worktree.py 8115-8216; workspace_env.py 1028-1034.)

### tnt R2 (tnt-v0.4.0 = a6d377dde5f2aeac40f85a0847d69629fc31f240; r1002 checkout of that commit; image r25-d38 / run-gate 25.1.1)
- Baseline r0-r1 on r1002 (two runs, jobs `tnt-v040-r0r1`, `tnt-v040-r0r1-b`): coverage 97.25% OK but **FAIL both times with ONE DIFFERENT TUI test each** (`tests/test_management_system.py::test_tui_switch_timeout_can_keep_waiting_then_open_requested_host`, then `tests/test_tui_workflow_branches.py::test_workflow_view_input_refusals_loading_and_vm_recovery_messages`; 1086 passed each). Timing/load-dependent flakes at nice 19 on a contended host (tnt-finish saw the same class). A third baseline inside the pilot PASSED (below). Since R2's R0 baseline is the same -x suite, a flake there aborts a campaign: re-run the campaign/resume rather than treat as product failure; the tests want a timing fix in tnt (file in tnt BACKLOG).
- Two real defects of the tagged R2 lane found by the pilot, fixed ONLY in the branch (not merged, not pushed): (1) `src/tnt/__init__.py` and `src/tnt/tui/__init__.py` are docstring-only -> `NO_MEASUREMENT/TARGET_NOT_MEASURED` (same defect class as cmru's `__init__.py`, file in BOTH tnt and cmru backlogs); (2) `tests/test_tui_tmux.py` leaves tmux servers behind -> assay `leaked-process-tree`, `EXEC_FAILED` on the baseline. Branch `tnt-r2-pilot-20261105` (worktree `/workspaces/vbpub/.worktrees/tnt-r2-pilot-20261105`, from tnt-v0.4.0): commits d24daa94f (r2-pilot lane + 30-id `pilot-candidates.txt`), 9f861f344 (drop `__init__` target), **af355cf5e** (drop `tui/__init__.py` target, `--ignore=tests/test_tui_tmux.py` in the lane argv). Plan = 2990 candidates (unchanged ids). NOTE the qualifying campaign must run from a commit carrying these two fixes, i.e. NOT the bare tag; consequence for the release story: the real fix belongs on main (assay.toml targets + the tmux leak in tests/test_tui_tmux.py), then re-tag or run R2 on that commit.
- PILOT running: job `tnt-pilot3` (lane `r2-pilot`, 1 worker, no verdict), checkout `~runner/checkouts/af355cf5e974993f05f9cb0e5cbc2eee3336683a`, progress `truenas/tnt/.assay/progress-pilot.jsonl`. Baseline PASS in 425 s. First 3 candidates: 438 s (survivor-like, full suite), 443 s, 28 s (kill) => bimodal 28 s kill / ~440 s full suite. Provisional estimate with 20-30 % survivors: ~140 s/candidate = ~0.4/min per worker => 2990 candidates ~115 h serial, ~40 h at 3 workers. Pilot ETA ~1-1.5 h total; final rate to be read from the progress file (`assay analyze plan-estimate --plan-json plan.json --progress .assay/progress-pilot.jsonl --workers 3`).
- Full campaign NOT started: CPU is full (pwmcp-r4 3 workers + pilot 1 worker = ~400 %). Start after the pilot completes/pwmcp ends (~20:20Z):
  `~/bin/run-job tnt-r2-sup -- bash run-r2-campaign-r25d38.sh tnt-r2 af355cf5e974993f05f9cb0e5cbc2eee3336683a truenas/tnt r2` (r1002, `cd ~/runner-scripts`). `r2` lane = `--jobs 3 --resume`, wrapper budget 12 h per attempt, the supervisor repeats on exit 4 (budget) while progress is made; 12 h placeholder is fine as a per-attempt bound, expect ~4 attempts at 3 workers. Needs the r2 lane from af355cf5e (already in the branch) -> bundle `tnt-r2-pilot-20261105 ^tnt-v0.4.0` as done for the pilot.
- Disk: r1002 root 85 % (5.7 GB free). Removed by exact path my own failed pilot checkouts d24daa94f and 9f861f344; the ciu b9df2475c checkout is kept until the controller has taken the survivor list.

### cmru shards: DEFECT FOUND on v1001 (read-only observation) and fixed in the shard branch
All 8 queued campaigns `cmru-r2b-s1..s8` (commit ccd96cda6) failed at their R0 baseline within ~10 minutes each (EXIT 1, `COMMAND_FAILED`, 15 progress events each, no candidate ran): `tests/test_docs_config_examples.py::test_assay_and_release_gate_split_rigor_without_empty_release_mutation` pins the `cmru-first-release-r2` mutation block exactly and the branch had added `candidate_fail_fast = true` to it. v1001 shard queue job `cmru-r2-shards-20261105b` is finished (EXIT 1); v1001 is therefore running only the dstdns chain. Fix: branch `cmru-r2-shards-20261105` commit **37ea9ad87** removes that one line (the shard lanes keep their fail-fast; both docs-config and budget tests pass locally with `--cli-case-partial`). The controller/v1001 operator must requeue s1-s8 at 37ea9ad87 (old campaign dirs/checkout ccd96cda6 can be deleted by exact path). Not yet verified: that nothing else fails in the cmru R0 baseline (the cmru-first-release baseline earlier failed for `__init__.py` TARGET_NOT_MEASURED, which no shard lane lists).
- r1002 has the shard checkout ready (`~runner/checkouts/37ea9ad87a029a1cca086bc5debeccf34b077226`, bundle `~/incoming/cmru-r2-shards-37ea.bundle`); s9-s15 NOT started: r1002 CPU is full (pwmcp 3 + tnt pilot 1). Command when CPU is free (--jobs 1 until pwmcp ends, 2 after): `cd ~/runner-scripts; ~/bin/run-job cmru-r2b-shards-r1002 -- bash -c 'for i in 9 10 11 12 13 14 15; do bash run-r2-campaign-r25d38.sh cmru-r2c-s$i 37ea9ad87a029a1cca086bc5debeccf34b077226 cmru assay-r2-s$i --jobs 1; done'`.

### v1001 s1 measured rate / resize
No s1 rate exists: s1..s8 never reached a candidate (baseline defect above), so the ~170 s/candidate estimate is still unmeasured. Recommendation: requeue at 37ea9ad87, run s1 for 20-30 candidates, read `progress-cmru-r2-s1.jsonl`, then size s2-s15; s9 (185) and s13-s15 (211-277) are fine under the 7 h cap at ~21 candidates/h only if the estimate holds; s10-s12 (421-526) will need 2+ attempts (the supervisor resumes on exit 4).

### Monitoring (r1002, host clock UTC+1)
17:31 load 3.7, cpu some ~6, mem some 0.0, io full 0.0, swap 105 MB; no stop signal. Running now on r1002: `pwmcp-r4-r2-sup` (3 workers, ETA ~20:20Z) + `tnt-pilot3` (1 worker).

## 10. Update 2026-11-05 ~16:30-19:05Z (v1001 host-compute operator, Sonnet): cmru shards requeued at 37ea9ad87, s1 rate measured
Identity (`r1002_api_check; v1001_api_check`) passed before the writes. <protected-host>/live-edge untouched; r1002 untouched; no credentials printed.

### Verification of 37ea9ad87 (local worktree)
Diff = one deleted line (`candidate_fail_fast = true` in the `cmru-first-release-r2` block). `tests/test_docs_config_examples.py` + `tests/test_gate_budgets.py` with `--cli-case-partial`: 22 passed. `assay plan` could NOT be run locally: the devcontainer assay is 12.0.0 and rejects `candidate_fail_fast` (BAD_LANE_CONFIG, all shard lanes); the v1001 checkout's assay (from the worktree source) accepts it and s1 planned 304 candidates (matches the report's count). Plan for s2-s15 not re-listed on v1001 (they were counted in section 8; config otherwise unchanged).

### Job table (v1001)
| Job | What | jobs | Status |
| --- | --- | --- | --- |
| `cmru-r2-shards-20261105c` | `run-r2-campaign-r2502.sh cmru-r2c-s{1..8}` at 37ea9ad87a029a1cca086bc5debeccf34b077226 (checkout `~runner/checkouts/37ea9ad8...`, bundle `~runner/incoming/cmru-r2-shards-37ea.bundle`), sequential | 2 | started 16:46Z; s1 candidates from 17:05Z, running |
| `r2-v1001-chain-20261105` | dstdns chain | 2 requested | NOT consuming CPU: every dstdns lane is refused at admission (`no gates limit is published and unreadable_policy=refuse`, reason no-headroom; retry 33 of 40, 3 min apart) since the reinstall; needs `run-gate admission set` on v1001 (not done by me; out of scope) |
Old checkouts ccd96cda6 / 81753544 and campaigns `cmru-r2b-s*` left in place (can be deleted by exact path).

### s1 measured (assay-r2-s1, jobs 2, candidate_fail_fast on), first 19 candidates, 17:05:35-18:00:10Z
- Baseline with coverage ~19 min (16:46-17:05Z), once per shard.
- Rate: 19 judged in 54.6 min = **0.35 candidates/min = ~21/h** at 2 workers = ~172 s wall per candidate (~342 s worker time). Host: load ~2.6, mem PSI some 0.0, io full ~0.4, no stop signal.
- Mix: killed 11 (58%), survived 8 (42%) - early sample, config.py only.
- Per-candidate command time: killed mean 168 s / median 148 s (cluster at ~35 s and ~150 s; one at 434 s); survived 581 s (full suite, same as without fail-fast: the coverage saving is not visible).
- So the planned ~170 s/candidate (wall at jobs 2) holds, but kills cost ~150 s, not the ~30-110 s projected; the speedup comes only from kills, and survivor share decides the shard time.

### Resize recommendation
- 300-candidate shards cost ~14 h at jobs 2 (~21/h); at 42% survivors; if survivors fall to ~27% the rate rises to ~26/h (~11.5 h). s1-s8 serial at jobs 2 = ~4-4.7 days on v1001 alone.
- Keep shard boundaries s2-s9 (<=305); the per-attempt 7 h cap just means 2 attempts via the supervisor resume. Better lever than resizing: jobs 4 on v1001 while the dstdns chain does not run (~85% core each, 4 vCPU; ~340% CPU) = ~2x; do it from s2 on (restart the queue after s1, or run s2.. with `--jobs 4`). Move s9 and s13-s15 (185-277) or s5-s8 to r1002 once its CPU frees, to cut wall time to ~2 days.
- s10-s12 (421-526) = 2-3 attempts each; split them only if a `--shard` pass-through lands; not needed for correctness.
- Re-measure after s2 (different module mix, survivor share unknown).

### Deviations
- Used `--jobs 2` as instructed although the chain is stuck in admission retries and CPU is ~65% idle; did not oversubscribe.
- Local `assay plan` impossible (assay 12.0.0 lacks the key); verified via the v1001 checkout instead; s1 only.
- Measurement at 19 (not 20-30) candidates because my waiter's grep timed out; same trend expected.
- Campaign names `cmru-r2c-s*` (new, to avoid resuming the failed R0 state); script `run-r2-campaign-r2502.sh` (the v1001 copy).

## 11. Update 2026-11-05 ~21:00-21:15Z (v1001 host operator, Sonnet): admission cap published, chain outcome, exec-mode lanes
- Published on v1001: `run-gate admission set --max-concurrent 2 --unreadable-policy refuse` (`ciu-admission-1`, run in the cockpit from the b0d031c7 checkout). r1002 publishes 4; scaled 4 vCPU/8 GB gives 2. Cause of the earlier no-headroom: no host provisioning publishes it (MDT-076, branch `mdt-admission-cap-gap-20261105` @ 5e37e9cf8, vbpub, not pushed).
- Chain `r2-v1001-chain-20261105` result: `inf_runtime_r2` PASS, `inf_landscape_r2` PASS; `p201_controller_main_r2_{compare,boolop}` exit 3 (40 no-headroom retries used up before the cap); 8 lanes exit 2 (see below).
- Requeued the two controller lanes: job `r2-v1001-chain2-20261105` (jobs 2), with the cmru shard (2 workers) about 350-400% CPU.
- 8 exec-mode lanes (`p165_report_access_r2`, `p201_workflow_{service x3,outbox x2,cancel_sched x2}_r2_*`): NOT a setup step. They declare no `environment`, so they inherit `[assay] environment = "test-runner"` (exec, needs a per-checkout CIU stack); working R2 lanes set `test-runner-r2`. Product fix: dstdns backlog DST-185, branch `r2-lane-env-20261105` @ 50794b84 (dstdns, not pushed). Not requeued; they cannot pass at b0d031c7 and would need a different commit.

## 12. Update 2026-11-06 ~10:00-10:35Z (host-compute operator, Sonnet): closeouts, tnt-v0.4.1 R2, cmru shards requeued
Identity (`r1002_api_check; v1001_api_check` + server-id guard) passed before every write. <protected-host>/live-edge untouched, no credentials printed. All times UTC (host `ls` times are UTC+1).

### Finished jobs (closeouts in `r1002/r2-closeouts-20261105/<job>/closeout.tgz`, plus `survivors.txt` where relevant)
- ciu 7.23.0 R2: FAIL/MUTANTS_SURVIVED (153 killed / 38 survived of 191), as in section 9.
- **pwmcp-v1.64.1-r4 R2** (campaign `pwmcp-r4-r2`, 3 attempts 08:46Z-18:31Z, commit b1659836e): lane verdict **ERROR/EXEC_FAILED (exit 2)** after all 493 candidates were judged: **461 killed, 29 survived, 3 crashed, 0 hung, 0 budget_exceeded**. R0 PASS, R1 PASS (100.00% line/branch, 2127 lines, 642 branches). The 3 crashes are cgroup OOM kills of the mutant test run (memory.events oom_kill 1 each) in `containers/ocr/ocr_service.py` -> they make the lane ERROR rather than FAIL; the r4 process-leak fix held (no leaked-process-tree). Survivors (paths under pwmcp/):
  - client/src/pwmcp_client/blocked.py: 95 True->False; 120 LtE->Lt; 126 True->False; 130 LtE->Lt; 166 LtE->Lt; 175 Eq->NotEq
  - client/src/pwmcp_client/cli.py: 143 None->[]; 171 Eq->NotEq; 182 GtE->Gt; 190 GtE->Gt; 200 None->[]; 320 True->False; 356 None->[]; 359 Lt->LtE; 364 Lt->LtE; 369 None->[]; 384 LtE->Lt; 451, 461, 469, 498, 566 True->False
  - client/src/pwmcp_client/ocr.py: 184 Or->And
  - client/src/pwmcp_client/storage_state.py: 27 True->False; 66 Or->And; 67 Is->IsNot; 91 Or->And
  - containers/ocr/ocr_service.py: 372 Gt->GtE; 766 True->False
  - crashed (OOM): containers/ocr/ocr_service.py 606 Or->And; 619 Eq->NotEq; 621 Eq->NotEq
- tnt pilot3 (v0.4.0 + pilot branch): finished 18:40Z, FAIL/EXCLUDED_LINES exit 6 (non-qualifying pilot lane). 30 candidates, 1 worker: 22 killed (mean 172 s, median 84 s), 7 survived (mean 431 s), 1 budget_exceeded then killed on retry; mean 257 s/candidate (pilot ran beside 3 pwmcp workers). Baseline 425 s.
- cmru shard `cmru-r2c-s1` (assay-r2-s1, jobs 2, 304 candidates): FAIL/MUTANTS_SURVIVED, 248 killed / 56 survived (config.py 45, release_preconditions.py 10, record_step.py 1; list in `cmru-r2c-s1/survivors.txt`). Wall 16:46Z-03:06Z (baseline 19 min, then two attempts): measured 31 candidates/h at 2 workers (0.52/min). Shard-1 survivors share is 18%.
- cmru `cmru-r2c-s2`: attempt 1 reached ~254/302 when the 7 h per-attempt budget ended (10:06Z); see deviations for what then went wrong.
- v1001 chain: see section 11 (unchanged).

### What runs now
| Host | Job | What | Workers | ETA |
| --- | --- | --- | --- | --- |
| r1002 | `tnt-r2-run-sup` (campaign `tnt-r2-run`, started 10:27Z) | tnt R2, lane `r2`, checkout `31c8ce648` (branch `tnt-r2-run-20261106` = tnt-v0.4.1 + 2 edits), image r25-d38 | 3 | baseline ~15 min, then 2990 candidates, ~55-70 h (see below), 24 h per attempt, supervisor resumes on exit 4 |
| r1002 | `cmru-r2c-s15-r1002` (campaign `cmru-r2c-s15`, 10:09Z) | cmru shard s15 (211 candidates), commit 37ea9ad87 | 1 | ~10-12 h |
| v1001 | `cmru-r2c-qA-s2-s8` (10:11Z) | s2 (resume, ~48 left) then s3..s8 sequential, commit 37ea9ad87 | 2 | s2 ~1.5 h after its baseline (~11:30Z), then ~9-11 h per shard: s3..s8 ~2.5 days |
| v1001 | `cmru-r2c-qB-s9-s14` (10:12Z) | s9..s14 sequential, commit c7d07240825e (host-local second checkout, same tree as 37ea9ad87) | 2 | s9 185 cand ~6 h, s10-s12 421-526 (2 attempts each, ~14-17 h), s13/s14 ~9 h: ~2.5-3 days |
CPU target: r1002 3+1 = 400%, v1001 2+2 = 400%. PSI at 10:27Z: r1002 cpu some 7.5, mem some 0.3, io full 0.7, swap 106 MB; v1001 cpu some 5.3, mem 0.0, io full 0.8, swap 1 MB. No stop signal. Disk: r1002 root 89% (4.5 GB free, was 5.6 GB; 3 own finished checkouts removed by exact path: b1659836e, af355cf5e, a6d377dde; closeouts saved first) - WATCH, ENOSPC killed an earlier campaign; v1001 root 12%.

### tnt campaign on tnt-v0.4.1: what happened
1. Baseline run-gate `r0-r1` on the tag checkout 5bd79de29 (job `tnt-v041-r0r1`): **PASS**, 1089 passed (3 deselected) in 425 s, coverage 97.25% (floor 97).
2. Campaign on the pure tag (`tnt-r2`, 10:12Z): **R0 baseline ERROR/EXEC_FAILED after 7 min** - assay's leaked-process-tree check: 5 pairs (tmux server + pane python3/sleep, one pair per tmux test), `ppid=7` (assay's subreaper). The tnt-v0.4.1 fixture (`tests/test_tui_tmux.py::_stop_private_server`) kills the private server and waits for the pids, but counts a zombie (state Z) as gone; under assay as child subreaper the orphans stay as unreaped zombies and assay lists them as leaked. This is my inference from the pid pairs/ppid, not a /proc state read. So the TNT-63 tmux fix does NOT satisfy R2 on a real host. The pure-tag campaign was discarded (campaign dir `tnt-r2`, checkout 5bd79de29 kept for evidence).
3. Therefore the campaign runs from branch `tnt-r2-run-20261106` @ **31c8ce648** (worktree `/workspaces/vbpub/.worktrees/tnt-r2-run-20261106`, from tnt-v0.4.1, not pushed/merged/tagged, Edit only, `commit --only`): `assay.toml` argv gets `--ignore=tests/test_tui_tmux.py` (as in the pilot; the real-tmux test stays in `ux-focused`) and the per-attempt budget placeholder 12h -> 24h in assay.toml and run-gate.toml `[lanes.r2]` (no run-gate/campaign-script budget override exists; run-gate 25.0.2 has no `--budget`). NON-QUALIFYING for the release record in the strict sense (not the bare tag, tmux tests excluded from the mutant-killing set); it finds survivors. A qualifying run needs a tnt follow-up (reap the tmux orphans in the fixture - wait for the pids to be truly reaped / double-fork-free teardown - or assay tolerating zombies, assay backlog) and a re-run.
4. Bounded pilot: NOT repeated on 0.4.1 (the lane has no pilot variant; adding one needs another commit and ~2 h of one core). Sizing input = the v0.4.0 pilot above (same suite apart from test flake fixes): 257 s/candidate/worker -> 2990 x 257 / 3 = ~71 h; with the lighter mix a campaign-measured rate (first candidates) is recorded below.
5. Campaign `tnt-r2-run` result so far: R0 baseline PASS (438 s, no leaked-process-tree), plan 2990 candidates; 11 candidates judged by 10:48Z (14 min after the baseline, 3 workers) = ~0.8/min = ~47/h -> **ETA ~64 h (about 2026-11-09 02:00Z)**, i.e. three 24 h attempts at most; early survivor-heavy mix, re-measure after ~100 candidates. Collect: `~runner/checkouts/31c8ce6481283089c4e6e007078212c7bbcb58fb/truenas/tnt/.assay/verdict-r2.json`, campaign dir `~runner/campaigns/tnt-r2-run/`.

### Cancel recipes (exact names, after identity check)
- tnt: `kill $(cat ~runner/jobs/tnt-r2-run-sup/pid)` + `pgrep -af "[r]un-r2-campaign-r25d38.sh tnt-r2-run "`, then `docker stop run-gate-31c8ce6481283089c4e6e007078212c7bbcb58fb-r2-147368-1793960830` (r1002). Resume = rerun the same campaign name on the same checkout.
- s15 (r1002): job `cmru-r2c-s15-r1002`, container `run-gate-37ea9ad87a029a1cca086bc5debeccf34b077226-assay-r2-s15-147080-1793959528`.
- v1001: jobs `cmru-r2c-qA-s2-s8`, `cmru-r2c-qB-s9-s14`; the loops are `bash -c for i in ...` parents, kill those first, then the campaign pids, then `docker ps --filter label=run-gate.lane` names.
- RULE LEARNED: two shard queues may only run in parallel from DIFFERENT checkouts (mutation state dir `.run-gate/assay-state/<project>` is per checkout, "already in use by another assay run" otherwise); queue B therefore uses the host-local commit c7d07240825e (same tree as 37ea9ad87).

### Deviations
- My first launch of s9..s14 on v1001 used the SAME checkout (37ea9ad87) as the running s2 queue. s2's attempt 1 ended on its 7 h budget at 10:06:39Z; the resume attempt of s2 and the whole s3..s8 loop then collided with s9..s12 holding the state dir (exit 2 in seconds, BAD_LANE_CONFIG "state directory already in use"), and s9-s11 had failed the same way earlier. No candidate state was lost (state is per candidate hash); s12 ran ~2 min and was stopped by exact container name. I killed my queue and its s12, moved the junk campaign dirs (`cmru-r2c-s3..s12`, the s2 attempts 1-2) to `~runner/campaigns/_collision-20261106/` (move, not delete), created the second checkout and relaunched both queues cleanly. Net loss: ~4 min of s2 progress time plus a fresh baseline (~19 min) for s2 and s9. Campaign names are unchanged (`cmru-r2c-s2..s14`), so the s2 attempts log is the new one.
- Two s9-s14 checks on v1001 use the host-local commit c7d07240825e (not in any repo; created with `git commit-tree` on the host, same tree, no files changed).
- tnt R2 does NOT run on the bare tag (see 12.2-12.3) and the pilot was not repeated on 0.4.1 (12.4); budget raised by branch commit 31c8ce648 instead of a script override (none exists).
- s15 runs at `--jobs 1` (r1002 shares 400% with the 3-worker tnt campaign); s2..s14 at `--jobs 2`.
- Admission cap on v1001 is 2 (section 11); both shard lanes fit.
- Not done: dstdns chain jobs (finished in section 11; 8 exec-mode lanes need DST-185), pwmcp/ciu survivors have no follow-up yet.

### Findings to file (not filed, no repo edits allowed here)
- tnt: TNT-63 tmux teardown does not satisfy assay's leaked-process-tree under the real R2 baseline (zombie counted as gone in the fixture) - tnt backlog; alternatively assay: tolerate zombie descendants (assay backlog next to B235).
- cmru: `src/cmru/__init__.py` TARGET_NOT_MEASURED in `cmru-first-release*` lanes (already noted in section 8/9).
- pwmcp: 3 OOM-crashed candidates in ocr_service.py (lines 606/619/621) turn an otherwise judged R2 into ERROR; raise the candidate/lane memory or tolerate in the lane.
