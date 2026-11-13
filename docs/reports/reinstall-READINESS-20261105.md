<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/reinstall-READINESS-20261105.md -->

# Reinstall readiness: v1001 then r1002 (read-only, 2026-11-05 ~13:40Z)

Identity: `r1002_api_check` and `v1001_api_check` both passed; `hostname -f` + `/etc/vbpub/server-id` re-confirmed over ssh (r1002 799611, v1001 804027). Only read-only ssh commands were run. <host-address> untouched. No repo edits.

## Recommendation

**v1001: GO now** (after the 6 preconditions below, none of which is a long wait). **r1002: WAIT**, go after v1001 passes section 7 AND pwmcp-r4 R2 finishes (~20:20Z) or the operator accepts abandoning it; kill the queued chain job first. Nothing depends on the assay tmpfs/provider ladder.

### Preconditions for v1001 (all cheap)
1. Bundle `v1001/debian-install-v2-customscript-20261105.json` created from the 20261104 one (it still has `root_max_size_gb: 40`; only the 20261104 file exists): remove that key, add `"disk_layout":"dev-host"`, `"data_area":"partition"`; keep `swap_policy on`, `root_resize_policy resize`, `swap_partition_count 4`, `swap_disk_total_gb null` (16 GiB) unless the operator picks 32 (REVIEW F5; decide BEFORE building the bundle, enlarging later means recreating the cow). Validate with an offline `show_plan`: root 335544320 sectors, data 168 GiB directly before swap.
2. Rollback path (runbook 1a): `debian-install-v2-pre-nc12` is NOT on origin (ls-remote shows only main; `6ce37f282` exists locally). Either push that branch, or prepare and offline-validate the `disk_layout: full-root` fallback bundle. Required by the runbook, not yet done.
3. Code reachable: GitHub reachable now (`git ls-remote` returns main `7c2b62902`, equals local origin/main). All three branches are already in origin/main: div2-disk-layout `6c9c2b543`, div2-data-area `6a1444314`, mdt-dedicated-mem `4e663da8c`. The customScript must name `main` (or a branch containing these). Precondition 0 satisfied; re-run ls-remote right before the window.
4. VM lane evidence is in: `r1-vm-real-commit` PASS (da-vm5, da-vm6: 79 passed incl. both data-area VM tests), `r0-r1` PASS. (An earlier da-vm2 FAIL was on an older commit.) REVIEW verdict: MERGE/GO; F1-F4 are not blockers for a fresh install.
5. Export set below collected and sha256-verified.
6. Survivor check on r1002 (see "r1002 as survivor" below); remove v1001 from `remote-gate-hosts.json` while it is down.

## 1. What runs now

### v1001 (up 1d12h, load 1.4, root 40G 83% / 6.6G free, cow 1.8G of 455G)
- `cmru-v722-r2` (job `cmru-v722-r2-sup`, cmru-v7.2.2 `fa48b351`, lane `assay-first-release-r2`, `--jobs 1`, image r25-d38): started 08:59Z, **candidate 20/4739 at ~0.1/min, ETA ~46800 min (about 32 days). Unfinishable as configured.** Progress is real but tiny (6427 progress events). Resumable: assay state lives in `~runner/checkouts/fa48b351.../cmru/.assay/` (progress-cmru-first-release-r2.jsonl) and `.run-gate/assay-state/` in the checkout, but a reinstall deletes both unless exported. Controller decision needed anyway (shard/sample the lane, or higher parallelism); it must not block the reinstall.
- dstdns r2m queues qA and qB: both finished (EXIT 0, "queue finished" 11:10Z). Nothing else running. Containers: only the cmru lane, `mdt-cockpit-runner`, `mdt-buildkitd`.
- Old cmru R2 `ba601c9e` (cancelled earlier at 25/4517): checkout + `.assay` kept, campaign has no final-exit.
- vda6/vda7 are ZFS `zp` (2.52M used, `zp/work` 96K): empty, nothing worth exporting beyond `zpool status/history` text. Currently only 2 swap partitions (8 GiB).

### r1002 (up 1d19h, load 4.8, root 40G **86% / 5.5G free**, cow 3.1G)
- `ciu-v7230-r2` (ciu 7.23.0 `b9df2475`, base ciu-v7.22.0 `df317057`, `--jobs 1`): candidate 138/191, 0.6/min, **ETA ~86 min (~15:05Z)**. Progress file 6029 events.
- `pwmcp-r4-r2` (pwmcp 1.64.1-r4 `b1659836`, lane r2, `--jobs 3`): candidate 183/493, 0.8/min, **ETA ~400 min (~20:20Z)**.
- chain job `r2-r1002-chain2`: only a wait loop (`until ciu-v7230-r2-sup/EXIT`); starts at ~15:05Z run-gate 25.1.2 `assay-r2` (`e6578816`, jobs 2) then 12 dstdns 2-job lanes. Not started yet, so killing it now loses nothing.
- Four `debian-install-vm-harness-*` containers up 11 h (older VM lane leftovers, not mine; stop by exact name only if the controller confirms).
- Resumability: same mechanism (assay-state + progress in the checkouts); a restore at the identical `~runner/checkouts/<sha>` path should let `run-r2-campaign` resume, but that restore-then-resume path is UNTESTED on a freshly installed host. Treat it as a bonus, not a plan.

## 2. What a reinstall destroys and what to export

Everything on vda (provider image reinstall). No Netcup snapshots survive (2026-11-04 finding). Host keys change. Controller key is retained via `--local-controller-key retain`.

Export per host to `<controller-home>/reinstall-20261105/<host>/` (rsync -aH as runner, read-only), then sha256 manifest compare:
- `~runner/campaigns/` (6.8M r1002, 9.7M v1001), `~runner/jobs/` (2.8M r1002; 93 job dirs on v1001), `rg`, `locks`, `runner-scripts` (run-lane-r25d38.sh, run-r2-campaign-r25d38.sh, dstdns-r2m-queue.sh, build-tester-*.sh, provision-runner.sh, runner.env, run-job; these only exist on the hosts, v1001 got copies from r1002 today), `bin` (run-job, sync-from-devcontainer), `deploy` (cgprofile deploy), `cache`, `src`, `venvs` can be rebuilt, `p3-resolve-cmru`, `mdt-cockpit-runner-config-20261104`, v1001 `ciu-remote-test`, `agent-wt`, `overlay-dns.sh`, r1002 `pwmcp-probe.sh pwmcp-durations.sh clone-empty.sh diag`.
- Verdict and state dirs from checkouts: `<checkout>/<project>/.assay/` and `<checkout>/.run-gate/` for at least: v1001 `fa48b351` (cmru-v722) and `ba601c9e`; r1002 `b9df2475` (ciu), `b1659836` (pwmcp), plus failed-baseline evidence `a4f28bdf`, `db0355df7`, `b0d031c7` (dstdns). Checkouts total 4.7G r1002 / 2.2G v1001: copy only these `.assay`/`.run-gate` dirs, not the trees (trees are recreated from bundles).
- Unpushed commits: all r1002 checkouts report 0 commits ahead of their `origin/main`, but origin in those clones is a local path/bundle, so this proves nothing about GitHub. Compare each checkout HEAD with `git merge-base --is-ancestor <sha> origin/main` on the controller. Runbook's examples `7db77fd32` (r1002, c79beeb79) and `76853c209` (v1001) exist; `7db77fd32` is still present. Bundle any HEAD not an ancestor of controller origin/main.
- `~runner/incoming/` (1.4G r1002 / 2.2G v1001): bundles and wheels; keep sha256 list, re-upload from the controller (bundle `r2-20261105b.bundle`, `dstdns-b0d031c7.bundle`, `cmru-v7.2.2.bundle`).
- Root-owned config: `/etc/mdt/` whole directory (host-setup.env + 8 `.pre-*` backups, buildkitd.toml, host-capacity.json, buildx), `/etc/vbpub/`, `/etc/docker/`, `/etc/fstab`, `/etc/profile.d/mdt-cow-root.sh`, `/etc/systemd/system*` units from mdt/cgpd, `/var/lib/vbpub/bootstrap`, `/var/log/debian-install`, `<local-path>`, nft/iptables dumps, `/etc/sysctl.d`, `/etc/udev/rules.d`.
[SSH key path and credential-file details redacted.]
- Docker volumes (small, caches): v1001 `vbpub-cgprofile-bc1e0d5ac`, `cgprofile-host-cgroup-profiler_cgprofile-sessions`, `mdt-buildkitd-cache`; r1002 `vbpub-cgprofile-53f7a7349`, same two. Only `cgprofile-sessions` could hold data worth a look; the rest are caches.
- Image rebuild list (do not copy; ids recorded for comparison):
  - r1002: `tester-unified:r25-d38b3324fba5` (run-gate 25.1.1, the one the campaigns use), `tester-unified:r25-e92d43b28b00`, `tester-unified:local` 2ee6fee4458c, `tester-base:local` ab4e04347a40, `dstdns/test-runner:{local 337b913ee1a5, latest 03864b31623c, orig 08f4f50e06dd, pilot-uid-78be31c3e1bd}`, cockpit devcontainer `ghcr.io/volkb79-2/mdt-cockpit-devcontainer:trixie-py3.14-php8.5-latest` da31899bcda7, buildkit rootless, `debian-install-vm:*` (VM harness).
  - v1001: `tester-unified:r25-d38b3324fba5`, `tester-unified:local` e92d43b28b00 (run-gate 25.0.0), `tester-base:local` e73a0d76fb84, `dstdns/test-runner:local`, cockpit image, 5 `debian-install-vm:*`, `traefik/whoami`.
  - Note r25-d38 was produced on r1002 and streamed to v1001 (`docker save|load` via the controller). After reinstall build one on the freshly installed host with `build-tester-unified-*.sh`, then stream to the other, or pull the promoted run-gate image if published; the run-gate chain from run-gate 25.1.2 should be built from the wheel the controller has.
- `<local-path>` is disposable (1.8 / 3.1 GB); state it, do not copy.
- Runner cockpit/run-gate rebuild: `build-tester-base`, `build-tester-unified`, `setup-assay-venvs`, `setup-gate-tools`, `setup-cockpit-tools.sh`, `start-cockpit-runner`, cockpit image pull (new release, see 3), cgpd via `ciu up --profile host` (cgprofile 2.0.0 native, check `ctl version/host --json`).

## 3. New mdt-cockpit release and runbook currency

- Release: `rel-mdt-cockpit-20261105c.log` and `-retry1.log` both end `Released: mdt-cockpit (no git tag)`, EXIT=0 (image push `release-bake.sh push`; run-gate lanes oci-push and image-runtime-built passed). Content on main: `6589c010b` (topos `/run/topos` read-only bind via `runArgs -v`, tmpfiles.d `d /run/topos 0750 root docker`, `units/mdt-topos*.conf`, install.sh:1316) and `49f8b8889` (MDT-075 test-render fix: tree-cache units via `mdt_tree_cache.py render`). Consumable by the runbook: step 5.3 runs host-setup from the runner checkout (synced from controller main, which contains both commits) and step 5.4 pulls the cockpit image. Neither host has `/run/topos` today (checked: absent), so the re-run host-setup on the fresh hosts is what creates it; devcontainers still start without it (Docker auto-creates). Because the release has no git tag, pull by the `trixie-py3.14-php8.5-latest` tag and record the digest; hosts currently have da31899bcda7, compare after the pull.
- **No dependency on the assay tmpfs/template ladder.** Evidence: grep of the installer path, `scripts/netcup/runner/provision-runner.sh` and runner scripts shows no tree-cache/tmpfs/ladder references; the tree cache (`<local-path>`, `<local-path>`, `dev-cache.slice`, GC timer) is created entirely by `mdt-cockpit/host-setup` (`mdt_tree_cache.py`, install.sh:1056/1165/1509) for `HOST_ROLE=dedicated-dev`, already live on both hosts (backup `host-setup.env.pre-treecache-20261105`), and no consumer is wired yet (AD2-5 not done). Reinstall should not wait. Add to the verification list only: tree-cache mount units active, GC timer, `host-capacity.json` has a `tree_cache` block.
- Runbook updates still needed (it says DESIGN ONLY, written before today's work):
  1. Section 0/1: mark precondition 0 satisfied by hashes above (data-area and mem branches merged); note installer branch for customScript = main.
  2. Section 1a: rollback branch not yet pushed; record a concrete command and who does it.
  3. Section 2/4: bundle 20261105 not created yet (give exact edit; also add F5 swap decision).
  4. Section 3: replace "state seen 2026-11-05" with the current job table (cmru-v722-r2 unfinishable, ciu done ~15:05Z, pwmcp ~20:20Z, chain2 to be killed), delete the cmru-r2-20261105 reference, and remove the "ask the operator about ZFS" gate (pool is empty; export `zpool status/history` only).
[SSH key path and credential-file details redacted.]
  6. Section 5.3: add that host-setup must be run from a checkout containing `6589c010b`/`49f8b8889` and that `/run/topos` and the tree-cache units appear after it; list expected tree-cache units in section 7.
  7. Section 5.4: image rebuild with the r25 tag scheme (`tester-unified:r25-<id>`), plus the cockpit digest compare; mention that the lane runner scripts (`run-lane-r25d38.sh`) must be restored from the export.
  8. Section 9 decisions: swap total 16 vs 32 GiB (F5); root 160 vs 192 GiB; pre-reinstall decision for cmru R2 (shard or drop).
[SSH key path and credential-file details redacted.]
  10. Add the REVIEW follow-ups F1-F4 as non-blocking, and keep REVIEW F3 check: `findmnt <local-path>` after reboot (nofail hides a failed mount).
  11. Stale fact: runbook says root cap forced "40 G root"; hosts show `vda3` 40G with swap right after; v1001 has only two swap partitions today (vda4/5) since vda6/7 are ZFS; new layout gives 4 swaps.

## 4. Schedule

### r1002 as survivor while v1001 is down (about half a day)
v1001 currently carries nothing worth keeping alive: its only job is the unfinishable cmru R2, dstdns queues are done. r1002 will keep ciu and pwmcp; **do not queue new lanes on r1002**: root is 86% (5.5G free) and an earlier ciu R2 already died with ENOSPC. Pre-step (controller decision, exact paths only): free r1002 disk first (old `checkouts/<sha>` evidence dirs already exported; `docker builder prune` on the host daemon is host-wide, so name what is removed). CPU PSI is not a stop signal (operator 2026-11-04), memory/io PSI is: read both before the window.

### Sequence
1. Now: collect v1001 export, kill or park `cmru-v722-r2` (kill wrapper pids then `docker stop` the exact container `run-gate-fa48b351...-90601-1793869170`, as in r2 board section 7), confirm `jobs/*/EXIT`, mark v1001 DRAINING and drop it from `remote-gate-hosts.json`. Also kill `r2-r1002-chain2` on r1002 (wait loop only) so it does not start assay-r2 after ciu.
2. Prepare bundle + rollback branch (preconditions 1-2); `v1001_api_check`; run `reinstall-v2-v1001-<date>.sh` shape (about 4 min install + reboot); new pin; section 7 checks (lsblk order, ~166 GiB gap, one MDT_COW btrfs, `findmnt <local-path>` before and after a reboot, swap 4 active, root ~157G).
3. Bring-up per runbook section 5 (provision-runner, sync-from-devcontainer, host-setup `HOST_ROLE=dedicated-dev` with the saved `host-setup.env` values, docker images, venvs, cockpit runner, cgpd, Claude credential). Proof: cowtree smoke job and one registered lane via remote-gate. Estimated 3-5 h total.
4. R2 queues after v1001 is back: restart `cmru` R2 with a feasible plan (not the 4739-candidate single worker), re-run any dstdns lanes needed, move the chain2 content (run-gate 25.1.2 `assay-r2`, 12 dstdns 2-job lanes) to v1001 so r1002 is free to drain. ciu result (~15:05Z) and pwmcp verdict files must be collected from r1002 first.
5. r1002: after v1001 passes and operator OK, and pwmcp-r4 verdict is collected (~20:20Z) or explicitly abandoned: same procedure; v1001 carries all gates during that window. Realistic r1002 window: tonight after ~20:30Z, or the next morning.

### Lost-work summary if forced earlier
v1001 now: only cmru R2 progress (20 of 4739 candidates, about 5 h of compute, restartable). r1002 now: ciu R2 (about 3.5 h done) and pwmcp R2 (about 4 h done), both restartable from scratch in ~1.5 h and ~7 h; the reinstall would cost the same again.

Files read: div2-reinstall-RUNBOOK.md, div2-data-area-REVIEW.md, HANDOFF-STATE-20261105.md, r2-board-20261105b-REPORT.md, plus live read-only host and repo checks.
