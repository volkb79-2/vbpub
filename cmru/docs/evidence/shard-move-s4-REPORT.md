<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/shard-move-s4-REPORT.md -->
<!-- relation: CMRU R2 shard report; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Shard move pilot: cmru R2 shard s4, <host> -> controller devcontainer host

Date 2026-11-08, 02:08Z-03:10Z. Host agent (Sonnet 5.5). Authorised: stop of THIS ONE shard (s4). s12 was not touched.
No credentials printed. <protected-host> and <host> not contacted. Files: `<controller-program-dir>/shard-move-s4/`.

## Verdict

- **The resume was accepted, it is NOT a silent fresh restart.** `resume` event on the controller (02:39:56Z):
  `resumed_total 209, rejected_total 0, rejudged_total 0`, `candidates pending_total 96 of candidate_total 305`.
  Before the move on <host>: 209 unique candidates done, 96 remaining. The `judge_sha256` of the controller run
  (`d67eb58adf846ca707c58b997db5fd381a158a1c390f84f02b6bd850078315b9`) is byte-identical to the one <host>'s attempt 3 wrote.
  Identical image ID, run-gate 25.0.2, same tree, same assay version `12.0.1.dev257+g37ea9ad87` gave an identical resume
  identity although checkout path and host differ.
- **Throughput on the controller is poor right now**: 2 candidates (both `killed`) in the first 29 min after the resume
  versus 12-25 candidates/h on <host>. Cause observed (not a configuration error): the lane workers sit in
  `balance_dirty_pages` (kernel writeback throttling) because the controller's disk is saturated (io PSI full avg10 mean
  24.5, max 48.9 over the window; other gate lanes and agents share it), plus the first 20 min of the attempt are the two
  baseline passes (about 10 min each on <host>, 27 min here). At the observed ~4 candidates/h the remaining 94 would need
  ~23 h instead of ~8 h on <host>. Memory is fine (below).
- No stop signal fired: controller memory PSI some avg10 max 21.4 (>25 never), MemAvailable min 3.9 GiB, swap used
  18.2-19.3 GiB (about flat, +0.2 GiB), game server container unaffected (4.22-4.30 GiB, 67-90% CPU the whole time).

## What was done (commands and evidence)

1. **Identity**: `v1001_api_check` + on-host guard (`hostname -f`, `hostnamectl --static`, `/etc/vbpub/server-id` = 804027)
   before every <host> command, cwd=/tmp.
2. **Image, exact copy.** <host> `tester-unified:r25-d38b3324fba5` = `tester-unified:local` =
   `sha256:d38b3324fba56ecc4c09bd50eca4c0a9268d10c04d75dd3c73b0e5241fe56fb6` (2971342753 bytes, 52 layers). Saved ONLY the
   `r25-d38b3324fba5` tag (so the controller's own `tester-unified:local` = `09fa2f5e9846...` could not be clobbered by the
   load), `nice -n 19 ionice -c3 docker save | gzip -1` streamed over the pinned ssh to
   `<host>/images-20261108/tester-unified-d38b3324fba5.tar.gz` (1139192488 bytes, 4 min). sha256 host =
   controller = `8acb4689a3dc7d93b11636f4aabf5a790e900f378bf97dc238d9d4313f521cd5`. `gunzip | docker load` on the controller:
   `Loaded image: tester-unified:r25-d38b3324fba5`, ID `sha256:d38b3324fba56ecc4c09bd50eca4c0a9268d10c04d75dd3c73b0e5241fe56fb6`
   = **IDENTICAL**, `:local` still `09fa2f5e9846...` (untouched). Nothing built, nothing retagged. (The controller already
   carried that ID as `prev-20261105d`/`wave-20261105c`, so the load was idempotent; run-gate prints
   "image ID sha256:d38b3324..." at lane start as a second confirmation.) The lane is pointed at it with
   `RUN_GATE_TESTER_UNIFIED_IMAGE=tester-unified:r25-d38b3324fba5` (the lane declares `tester-unified:local`).
3. **run-gate pin**: <host>'s shards run `gate-tools2502` = run-gate 25.0.2. Built a private venv
   `<controller-home>/.venvs/run-gate-25.0.2` from the release wheel (sha256
   `42435a7337b88b4853dfdacf310cf695c91e02f0b2c6876ec070367e05f958b1`, verified) and used it for the lane
   (`run-gate: 25.0.2 | lane assay-r2-s4`), not the controller's 25.2.1. Cockpit is not needed: <host>'s wrapper only
   `docker exec`s run-gate in the cockpit; on the controller run-gate runs directly with
   `RUN_GATE_ALLOW_MAIN_CHECKOUT=1 RUN_GATE_PROFILE=off` (the same env the <host> wrapper sets).
4. **Stop (02:11:14Z), per board recipe, graceful**: (a) qA supervisor loop `3650112` (`for i in 2..8`) killed FIRST so
   s5-s8 can never auto-start; its `runner-run-job` wrapper `3650110` exited with `EXIT=143`; (b) s4 campaign wrapper pids
   `84346`, `84334` killed (each verified by cmdline match `run-r2-campaign-r2502.sh cmru-r2c-s4 ` before the kill);
   (c) `docker stop -t 90 run-gate-37ea9ad87a029a1cca086bc5debeccf34b077226-assay-r2-s4-16340-1794094697` (exited in 1.4 s);
   (d) verified: no s4 process left, no `final-exit` for s4, qB (`cmru-r2c-qB-s9-s14`), its wrappers, the s12 container
   `...c7d07240825e...-assay-r2-s12-16179-1794087874` and its run-gate pid `3082696` untouched and still running (s12 progress
   file kept growing: 238 candidate events at 03:09Z).
5. **Export (step 0 of the runbook)**: `tar` of `~runner/checkouts/37ea9ad87a029a1cca086bc5debeccf34b077226` (with `.git`,
   `cmru/.assay`, `.run-gate/assay-state`), `~runner/campaigns/cmru-r2c-s4`, `~runner/jobs/cmru-r2c-qA-s2-s8` ->
   `<host>/r2-handover-s4-20261108/s4-handover.tar.gz` (135077821 bytes), sha256 host = controller =
   `771de086ca8f92898a023cbf7d4fb734516b2c895e63e29991fe31e083173851`. Extracted to `.../tree/`, runbook manifest block run:
   9161 files, `sha256sum -c SHA256SUMS` all OK (0 failures), `SHA256SUMS` kept in `tree/`.
6. **Import**: copied the checkout (`git rev-parse HEAD` = `37ea9ad87a029a1cca086bc5debeccf34b077226`, clean tree, progress
   file 209 candidate events) to `<worktree>` (a standalone clone, not a vbpub
   worktree; `/workspaces/<sha>` cannot be reproduced because `/workspaces/*` entries are per-repo bind mounts and the docker
   daemon needs host-translatable paths). `c7d07240` (empty commit on the same tree) was not needed: s4's own commit is
   `37ea9ad87`. Campaign dir copy: `shard-move-s4/<host>-campaign-copy/`.
7. **Supervisor and guard on the controller** (`shard-move-s4/run-s4-controller.sh`, mirrors `run-r2-campaign-r2502.sh`:
   re-run while exit 4 with growing progress, resume automatic, max 12 attempts, final-exit file):
   `cd <checkout>/cmru && nice -n 19 ionice -c2 -n7 run-gate assay-r2-s4 --jobs 2` (jobs as the original shard setting).
   Started 02:12:29Z (supervisor pid 75424). Container
   `run-gate-cmru-r2-s4-move-20261108-assay-r2-s4-75452-1794103952`, image ID confirmed, cgroup parent `dev-gates.slice`.
   **Limits** (no `--memory` knob in run-gate; applied with `docker update` on the exact name, like the repo's existing
   `docker update --cpus` practice): `--memory 3g --memory-swap 16g --cpus 2` (02:13:28Z). I first also set `--cpu-shares 128`
   (low weight); after 18 min the workers had used 1.5 CPU-minutes, so at 02:58Z I restored `--cpu-shares 1024` and kept the
   hard cap `--cpus 2` (operator allowed "cpu weight low OR --cpus 2"). The workers were still in `balance_dirty_pages`
   afterwards, so the shares were not the main cause. A second, name-based guard `guard-s4.sh` runs because the supervisor's
   own guard looks the container up by label `run-gate.lane`, which run-gate 25.0.2 does not set (its `limit_container`
   message "no lane container appeared" is that, harmless); guard: memory PSI some avg10 > 25 or MemAvailable < 1 GiB for
   3 consecutive 20 s samples -> `PAUSED` flag, stop supervisor, `docker stop` the exact container (resumable).
8. **Resume check** (progress file `.../cmru-r2-s4-move-20261108/cmru/.assay/progress-cmru-r2-s4.jsonl`): attempt runs
   baseline (finished 02:22:15Z, `PASS`), a second ~17 min phase (plan at 02:39:50Z, `baseline_s 571.473`), then
   `resume` 02:39:56Z as quoted in the verdict. First two new candidates at 03:02:55Z, both `killed`.
9. **Flag**: `rm <controller-program-dir>/host-sat-queue.disabled.<host>` after the accepted resume
   (02:41Z). The <host> sat queue immediately launched lanes (`sat-selftest...` PASS 277 s, `sat-suite` started 03:09Z);
   <host> load 1.4, s12 healthy. The <host> flag is untouched. **Re-pause the <host> queue at the reinstall signal.**

## Counters

| Moment | Done (unique) | Remaining | Source |
|---|---:|---:|---|
| <host> 01:12Z (earlier survey) | 197 | 108 | progress file |
| <host> at stop 02:11Z | 209 | 96 | progress file, 209 candidate events |
| controller `resume` 02:39:56Z | 209 resumed | 96 pending | `resume`/`candidates` events (rejected 0, rejudged 0) |
| controller 03:09Z | 211 | 94 | 2 new, both killed |

## Monitoring (02:17Z-03:10Z, 60 s samples in `monitor-part1.csv`, `monitor.csv`)

- Memory PSI some avg10: max 21.44, mean 2.9, never > 25; full avg10 max ~20.
- MemAvailable 3.9-6.0 GiB; swap used 18.22 -> 18.46 GiB (max 19.29, transient); no growth trend.
- io PSI full avg10: mean 24.5, max 48.9 (not a stop signal per the operator ruling; it is the cause of the slowness).
- Shard container: 0.5-0.9 GiB of its 3 GiB, 3-195% CPU (cap 200%).
- Game server `b87c0a5b-2387-4a1c-8863-ff23e6800a1d` (stats of that container only): 4.22-4.30 GiB, CPU 67-90%, steady.
- Load average 5-16 on 8 cores (mostly other agents).

## State now and what to know

- Running on the controller: supervisor `75424`, guard `77076`, shard container above; remaining 94 candidates. If the
  guard fires it writes `shard-move-s4/PAUSED` and stops the container; remove the flag and rerun
  `run-s4-controller.sh` to resume (loss: in-flight candidates plus the ~25-30 min double baseline).
- <host>: qA (s2-s8 loop) is dead by design: **s5-s8 are not scheduled anywhere now.** qB (s9-s14) and s12 untouched.
  For the reinstall only s12 (+qB) still has to be stopped and exported.
- `budget_exceeded` risk: per-candidate budget is ~1714 s wall. With the workers throttled, a slow survivor could end as
  `budget_exceeded` (1 of 188 earlier on <host>, none seen yet on the controller). Check
  `python3 shard-move-s4/progress-s4.py` for outcome buckets; a rise in `budget_exceeded` means the controller disk is too
  contended for valid evidence.
- Recommendation: keep it running (it costs nothing on <host> and survives the reinstall), but expect ~4 candidates/h while
  the controller disk is this busy; if throughput stays that low, or `budget_exceeded` appears, move s4 back (below) or park
  it until the controller is quieter. Ending the other gate lanes on the controller during the night would raise the rate.

## Move back to <host> (not needed now; recipe)

1. `kill 75424` (supervisor), `docker stop -t 90 run-gate-cmru-r2-s4-move-20261108-assay-r2-s4-75452-1794103952`.
2. `tar -C <worktree> -cf - . | gzip -1` -> <host>
   `~runner/checkouts/37ea9ad87a029a1cca086bc5debeccf34b077226` (move the old dir aside first), plus the campaign dir.
3. <host>: `run-job cmru-r2c-s4-back -- bash -c 'cd ~/runner-scripts; bash run-r2-campaign-r2502.sh cmru-r2c-s4 37ea9ad87a029a1cca086bc5debeccf34b077226 cmru assay-r2-s4 --jobs 2'`
   (identity checks first; the original state is also kept intact in `r2-handover-s4-20261108/`).

## Files

`shard-move-s4/`: `run-s4-controller.sh`, `guard-s4.sh`, `monitor-s4.sh`/`monitor-s4b.sh`, `progress-s4.py`, `attempt-01.log`,
`guard.log`, `samples.csv`, `monitor*.csv`, `metadata.txt`, `<host>-campaign-copy/`. Export: `<host>/r2-handover-s4-20261108/`
(tar + `tree/` + `SHA256SUMS`), image: `<host>/images-20261108/` (tar.gz + sha256).
