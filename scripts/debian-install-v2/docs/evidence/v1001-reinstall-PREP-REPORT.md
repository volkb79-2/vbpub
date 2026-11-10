<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/v1001-reinstall-PREP-REPORT.md -->
<!-- relation: v1001 reinstall preparation; verbatim source text follows -->

# v1001 data-area-v2 reinstall: PREP report (no destructive action taken)

Date: 2026-11-08, written ~01:20Z. Host agent (Sonnet 5.5) for the vbpub controller.
Scope: everything up to, NOT including, the destructive reinstall. Nothing was stopped, killed, restarted or
reinstalled on v1001. r1002 and <protected-host> were never contacted. No credentials were printed or stored.

Reviewed commit (pin): `REVIEWED_MAIN_COMMIT=7345dcb93c8b980114abfad3cfeb51211ca810c4` (= origin/main head at
01:03Z, fetched fresh and re-checked via GitHub at dry-run time).
Prep worktree (pristine, detached at the pin, `scripts/netcup/.env` is a symlink to the token file, gitignored):
`/workspaces/vbpub/.worktrees/v1001-reinstall-prep-20261108`. The runbook's worktree
`.worktrees/div2-data-area-v2-20261105` no longer exists (removed after merge): every `cd` in the runbook must use the prep
worktree instead.

## 0. Bottom line

1. The reinstall is READY except for decisions D1-D4 below. Bundle, plan, dry-run, export, pins are all done and recorded.
2. Shards: s4 ~8 h (range 5-11 h) and s12 ~24 h (range 19-29 h) from now; the supervisors then auto-start s5-s8 and s13-s14
   (another ~2.5 days). Recommendation: **(b) stop + export now**, resume on **v1001 after the reinstall** (not r1002).
   This deviates from runbook step 0 (resume on r1002 first), so it needs the controller's/operator's yes (D1).
3. `r1-vm-real-commit` on main as merged: **FAIL (stale verifier name, not a product failure)**. With the one-line fix in a
   scratch copy: **PASS**. All real-disk tests passed in both runs (D3).
4. The real GitHub pinned-SHA fetch check (the thing the reviews demanded) is **PASS**, including a negative case.
5. Open decision that changes the plan: raw benchmark partition size (8 GiB default) if the operator wants meaningful
   post-reinstall raw `standard` runs (D4).

## 1. Decisions needed before the destructive step

| ID | Decision | My recommendation |
|---|---|---|
| D1 | R2 shards: (a) wait vs (b) stop+export now; resume where | (b) now; resume on v1001 after reinstall with identical pins. r1002 cannot host the resume now (section 3.3). Runbook step 0 literally says hand over to r1002 first: this is a deviation. |
| D2 | Hold pushes/merges to `origin/main` for the window | Yes, or `--yes` refuses (section 6, risk R1). |
| D3 | Accept `r1-vm-real-commit` evidence (FAIL as-is on a stale name, PASS with the fix) and merge the one-line fix AFTER the reinstall | Accept; merging first would move the pin and force a new bundle hash + dry-run + independent review. |
| D4 | Raw benchmark partition size and whether raw `standard` runs follow the reinstall | Decide before `--yes`; changing it means new config, bundle hash, dry-run (section 8). |

## 2. Identity and queue state

- `v1001_api_check` OK (server 804027, name v2202511209318406253, v1001, <host-address>, RUNNING). On-host guard
  (hostname -f, hostnamectl --static, /etc/vbpub/server-id = 804027) OK before every host command. Note: the earlier
  `/workspaces/vbpub/.env` shadow does not exist right now; the helper worked from any cwd. The wizard, however, reads the
  token from `<its own scripts/netcup>/.env` (cwd is forced to that directory), see runbook gap G1.
- Host-sat queue for v1001 paused: `touch <controller-program-dir>/host-sat-queue.disabled.v1001`.
  The queue log showed only `skip+wait: load1 ~4 >= 2.5` since 00:26Z and no in-flight `sat-*` lane (last v1001 sat job
  `rg-sat-cliext-r0r1-20261107T2346Z` EXIT=0). **Remove the flag after the reinstall/resume** (the r1002 flag is not mine).
- Live host facts (from the export): disk 512 GiB (last-lba 1073741790), `vda3` root start 2500608, 335544320 sectors
  (160 GiB), exactly the runbook plan. Current (old) layout: vda1/2 ESP+boot, vda3 root, vda8 `MDT_COW` btrfs 168 GiB at
  sector 687863808, vda4-vda7 4 GiB swap at the disk end. Load 4-5 on 4 cores, 8 GB RAM, CPU PSI some ~11, root 21 GB used.

## 3. R2 shards on v1001 (survey, nothing stopped)

### 3.1 What runs (as of 01:12Z; re-derive pids at stop time)

Two cmru R2 campaigns, each `run-r2-campaign-r2502.sh` -> `run-gate assay-r2-sNN --jobs 2` (run-gate 25.0.2 from
`gate-tools2502`, image `tester-unified:local`) inside `mdt-cockpit-runner`:

| Shard | Campaign | Checkout | Plan | Done | Left | Started | Attempt now | Container |
|---|---|---|---|---|---|---|---|---|
| s4 | cmru-r2c-s4 | `~runner/checkouts/37ea9ad87a02...` | 305 | 197 | 108 | 2026-11-07 09:37Z | 3 (since 23:57Z, budget 7 h) | `run-gate-37ea9ad87a029a1cca086bc5debeccf34b077226-assay-r2-s4-16340-1794094697` |
| s12 | cmru-r2c-s12 | `~runner/checkouts/c7d07240825e...` | 421 | 220 | 201 | 2026-11-07 07:43Z | 3 (since 22:05Z, budget 7 h) | `run-gate-c7d07240825e5ca28c45194556444d17e910ba92-assay-r2-s12-16179-1794087874` |

Three supervisor jobs (`~runner/jobs/<job>/`, no EXIT file):
- `cmru-r2c-qA-s2-s8` (wrapper pid 3650110, loop pid 3650112): loops `for i in 2..8` on commit 37ea9ad8. s2, s3 done
  (exit 1 = survivors); **s4 running**; s5, s6, s7, s8 not started.
- `cmru-r2c-qB-s9-s14` (wrapper 3651149, loop 3651150): loops `for i in 9..14` on commit c7d07240. s9, s10 done (exit 1);
  **s11 = exit 2 BAD_LANE_CONFIG ("R1 verdict policy differs from the named lane declaration"), ran 20 min, produced no
  evidence, was skipped by the loop and still needs a rerun after the lane is fixed**; **s12 running**; s13, s14 not started.
- `cmru-r2c-shards-s9-s14-v1001` (recorded pid 3618246): **dead, no process, no EXIT file, empty log**: a stale job record
  (superseded by qB). Nothing to stop; just note it on the board.
- s1 also finished (exit 1). Exit 1 = FAIL with survivors is the normal judged outcome for these shards.

Each attempt ends with exit 4 at the 7 h budget; `run-r2-campaign-r2502.sh` re-runs the lane (`--resume` automatic,
max 12 attempts, stalls twice -> stop). Both campaigns already resumed twice in place (s4 pending 305 -> 219 -> 142;
s12 421 -> 340 -> 227), each resume costing a ~10 min baseline re-run. That is direct evidence that the resume mechanism
works from the state dir after the process dies.

### 3.2 ETA

Rates (candidates/h from the progress files): s4 13 (6 h) / 15 (3 h) / 25 (last hour); s12 10.7 (6 h) / 8.3 (3 h) / 7 (1 h).
Shard sizes seen so far: 185-526 candidates, 7.5-13.7 h each when they ran alone with another shard.

| Item | ETA (UTC) | Note |
|---|---|---|
| s4 finishes | ~09:30Z today (range 06:00-11:00Z), ~8 h | needs attempt 4 after the 07:00Z budget stop |
| s12 finishes | ~02:00Z 2026-11-09 (range 20:00Z 11-08 to 06:00Z 11-09), ~24 h | 3 more attempts |
| qA tail s5-s8 | +4 shards x ~12-25 h | auto-starts the moment s4 ends |
| qB tail s13-s14 (+ s11 rerun) | +2-3 shards x ~12-25 h | auto-starts the moment s12 ends |
| everything queued | ~2.5-4 days | not an option for a reinstall window |

### 3.3 Recommendation: (b) stop + export now, resume on v1001 after the reinstall

Why not (a): even a partial wait needs the two supervisor loops killed first (otherwise s5/s13 start at once), and s12
alone holds the host ~24 h. Why (b) is cheap: killing loses only in-flight candidates (2 workers; observed candidate wall
times up to 440 s) plus one ~10 min baseline per shard on resume, i.e. under ~30 min of compute per shard; the progress
file and the assay state dir persist everything judged. The downside risk is a resume refusal; then both shards restart
fresh at a cost of about 34 compute-hours (s4 16 h + s12 18 h so far).

Why v1001 and not r1002 for the resume (needs your OK, runbook step 0 says r1002):
- r1002 runs the qualifying tnt R2 on 3 workers of 4 cores until ~2026-11-09 11:30Z plus the iobench rerun; its root disk
  is ~90% (about 6.4 GB free) while each shard writes ~2.5 GB of pytest basetemp into the container layer.
- The image is identical (r1002 tag `tester-unified:r25-d38b3324fba5` == v1001 `tester-unified:local`, both
  `sha256:d38b3324fba56ecc4c09bd50eca4c0a9268d10c04d75dd3c73b0e5241fe56fb6`), so r1002 would be possible technically, but
  the wrapper `lib.sh identity_check` and the paths are per host; the reinstalled v1001 keeps server id 804027 and the same
  `/home/runner/checkouts/<sha>` paths, which is the lowest-risk resume target.
- The host-only wrappers `run-r2-campaign-r2502.sh` and `run-lane-r2502.sh` (not in the repo; the lane wrapper hard-codes
  `RUN_GATE_TESTER_UNIFIED_IMAGE=tester-unified:local` and `PATH=.../gate-tools2502/bin`) exist ONLY on the hosts. I
  snapshotted the whole host `~/runner-scripts` (24 files, 120 KB) to
  `<controller-program-dir>/v1001/runner-scripts-host-snapshot-20261108/` (read-only copy). They must be
  restored after the reinstall; the in-repo `provision-runner.sh` will not install them.

If r1002 is nevertheless required by the operator, a resume there should wait for tnt R2 closeout and a disk cleanup, and
needs `run-r2-campaign-r25d38.sh` (already on r1002 per the design).

### 3.4 Exact stop recipe (not run; per board recipe `r2-board-20261105b-REPORT.md:112-115`)

Order matters (supervisors first, so nothing restarts), after `v1001_api_check` + guard:
1. `kill 3650112 3650110 3651150 3651149` (the two loop shells, then their `runner-run-job` wrappers).
   Do not rely on the numbers: derive with `pgrep -af '[r]unner-run-job /home/runner/jobs/cmru-r2c-q'`.
2. `pkill -f '[r]un-r2-campaign-r2502.sh cmru-r2c-s4 '`, same for `cmru-r2c-s12 ` (campaign wrapper + its sampler).
3. `docker stop` the two exact container names in the table, by exact name only (never `ancestor=` filters).
4. Any surviving in-cockpit `run-gate assay-r2-s4|s12` pid (now 704803 and 3082696): `kill`, then re-check `ps`.
5. Confirm: `docker ps --format '{{.Names}}'` shows no `run-gate-*`, no `assay.cli` process, `~/campaigns/cmru-r2c-s4|s12`
   have no `final-exit` (a killed wrapper writes none; state stays resumable in
   `~runner/checkouts/<sha>/.run-gate/assay-state/`).
6. Export AFTER the stop (consistent files): `tar` of `~runner/campaigns`, `~runner/jobs`, the two checkouts
   (`37ea9ad87a02...` 307 MB, `c7d07240825e...` 277 MB, both with `.git`: the c7d07 commit is a host-local commit that exists
   nowhere else), plus `.run-gate/` and `cmru/.assay/` inside them. Total about 600 MB. Extract into
   `$R/v1001/r2-handover-20261108/` (mode 0700), run the runbook step-0 manifest block, keep the tar sha256, update
   `r1002/R2-BOARD-2026-11-05.md` (stop/handover record).
7. Save images (section 5), verify sha256.

## 4. `r1-vm-real-commit` and the real GitHub SHA fetch

Where: the lane is `environment = "bare-host"`, needs the cockpit/devcontainer docker socket, `CGROUP_PARENT_DEV_GATES`,
`CGROUP_PARENT_DEV_INTERACTIVE`, `BUILDX_BUILDER` (all set here) and runs a QEMU/**TCG** guest (no `/dev/kvm` on the
controller and none needed). It does **not** need v1001 (remote-gate refuses bare-host lanes anyway), so it cannot disturb
the shards. It ran on the controller devcontainer under `nice -n 10`: 1.4 cores avg, ~5.1 GiB peak, ~15 min per run.

| Run | Tree | Verdict |
|---|---|---|
| 1 (as merged) | detached `7345dcb93` | **FAIL**, exit 1. Pytest in the guest: `81 passed` (loop/swap/data-area/dev-host-layout stage two/root growth tests) and `1 passed, 80 deselected` (systemd-tmpfiles os_integration). The lane's own verifier then said `missing=['test_bounded_run_returns_output_and_exit_status']`. Log: `logs/v1001-prep-r1-vm-real-commit-20261108.log` |
| 2 (scratch copy, one line removed) | `7345dcb93` + `reports/v1001-prep-vm-lane-stale-name.patch` | **PASS**, exit 0, both `verified:` lines printed. Log: `logs/v1001-prep-r1-vm-real-commit-rerun-20261108.log` |

Cause: `testing/vm/run-vm-tests.sh` still lists `test_bounded_run_returns_output_and_exit_status` in `expected_os`, but that
test no longer exists in `test_io_benchmark.py` after the data-area-v2 rewrite (it was added by 64aa2adc1 / 1625eacf7).
Lane-script defect (stale expectation), product tests all green. Fix (1 line) is the patch file above; I did not touch main
(a main change moves the pin). Scratch worktree and my VM runner containers were removed/stopped (only exact own names).
File as a backlog item, merge after the reinstall (D3).

Real pinned-SHA fetch (the reviews' demand; note `r1-vm-real-commit` does not itself do this): ran the production
`bootstrap-remote.py:fetch_subtree` against the real `https://github.com/volkb79-2/vbpub` (python -I, temp dirs, removed):
- pin `7345dcb93c8b980114abfad3cfeb51211ca810c4`: **OK** (27 sparse runtime files + iobench, 1.6 s, `HEAD` verified equal).
- older ancestor `f2e11927818d951ef0cecee7e5c328c1dcaa7224` (branch has moved past it): **OK**.
- bogus SHA: refused (`upload-pack: not our ref`), as designed.
So a pin keeps working after `main` advances; no GitHub-side obstacle.

## 5. Pins to record, image save plan

| Pin | Value | Source/verification |
|---|---|---|
| Reviewed commit | `7345dcb93c8b980114abfad3cfeb51211ca810c4` | origin/main via GitHub; bundle exports `REPO_COMMIT` |
| run-gate | 25.0.2, wheel sha256 `42435a7337b88b4853dfdacf310cf695c91e02f0b2c6876ec070367e05f958b1` | release `run-gate-v25.0.2` asset, downloaded and re-hashed (matches `.sha256` asset). Host `gate-tools2502` = 25.0.2 (what the shards run); host cockpit default is 25.1.2 |
| runner.env | `RUN_GATE_VERSION=25.0.2` + `RUN_GATE_WHEEL_SHA256=<above>`; remove `COW_DEVICE`; keep `COW_MOUNT=/srv/cow`, `COW_PARENT_DISK=/dev/vda` | live host file says 25.1.2 (+ its sha), the operator copy `v1001/runner-scripts/runner.env` still says 24.3.0: both stale vs the pin. The unapplied patch only drops `COW_DEVICE` (it applies cleanly: `patch -p1 --dry-run` OK) and does NOT set the version, so set it by hand |
| Cockpit image | `ghcr.io/volkb79-2/mdt-cockpit-devcontainer@sha256:afd6dc2c17a97689aa007d1423e4890edfa85a16c634049cc03d9dc300c03a5a` (created 2026-11-05T08:20Z, image id `sha256:30aeb67b2c7e...`) | host RepoDigests; `imagetools inspect` shows it is pullable and is what `-latest` resolves to today (a newer 20261107-3 build is in the working-tree manifests, not yet `latest`). Put the digest form into `COCKPIT_IMAGE` |
| Tester image | `sha256:d38b3324fba56ecc4c09bd50eca4c0a9268d10c04d75dd3c73b0e5241fe56fb6` (tags `tester-unified:local`, `tester-unified:r25-d38b3324fba5`; label run-gate 25.1.1; 2.97 GB, 52 layers, no RepoDigest so it cannot be re-pulled) | host `docker images`; it only survives via `docker save` |
| Bundle | `debian-install-v2-customscript-20261107.json` sha256 `fc64a5cb0625db50aa986d2b1f84ca2fcfabd6a838d8f5fa3a1f919166f0b662` | deterministic (rebuilt twice, same hash) |
| Account SSH key id | 22556 (the only account key; seen live in the wizard) | `--ssh-key-id 22556` |

Image save plan (not run; do it after the stop so the host is idle, ~3-10 min, one at a time):
`docker save tester-unified:local tester-unified:r25-d38b3324fba5 | gzip -1 > $R/v1001/images-20261108/tester-unified-d38b3324fba5.tar.gz`
(one stream, both tags, ~1 GB), `sha256sum` it, then after reinstall `gunzip | docker load` (the image id is preserved) and
verify the id equals `sha256:d38b3324...`; the lane wrapper needs the tag `tester-unified:local`. Optional, saves rebuilds:
`dstdns/test-runner:local` (1.24 GB, `337b913ee1a5`) and `tester-base:local` (1.18 GB). Controller has 167 GB free.
Also rebuild `~/.venvs/gate-tools2502` after reinstall with `setup-cockpit-run-gate.sh 25.0.2 <sha>` (the venv lives in the
cockpit home, which is lost).

## 6. Pre-reinstall steps executed (all read-only or reversible)

1. Export: `pre-reinstall-export.py v1001 --date 20261108` -> `<controller-program-dir>/v1001/pre-reinstall-20261108/`
   (mode 0700; the date is today's, the runbook text says 20261107). `sha256sum -c SHA256SUMS`: all OK, 0 failures.
   `SHA256SUMS` itself sha256 `1a3858f94c70df4b61a2db7ca2809aee22a6545bdeba16909db0bc30bcdbe189`.
2. Stale-payload quarantine for **v1001 only** (8 `mv` into `v1001/stale-20261107/`, nothing deleted). The r1002 half of
   the runbook block was NOT done (r1002 is off limits; those are local files but belong to its own window).
3. Offline plan with `--disk-size 512GiB --root-start-sector 2500608 --root-number 3`: matches runbook section 1 (root
   160.00, MDT_COW 326.81, iobench-raw 8.00, four 4.00 swaps, vda3..vda9).
4. `build-customscript --repo-branch main --repo-commit 7345dcb9...` -> `v1001/debian-install-v2-customscript-20261107.json`
   (mode 0600). Inspected: `config.partitioning` as planned, `run_io_benchmark: true`, `swap_policy: on`,
   `completionMarker /var/lib/vbpub/bootstrap/stage2_done`, `REPO_BRANCH=main`, `REPO_COMMIT=7345dcb9...`.
5. Wizard dry-run via `pre-reinstall.py v1001 ... --dry-run` into the separate run dir
   `v1001/reinstall-prep-20261108/` (`reinstall.log`, `EXIT=0`): `REMOTE_COMMIT=7345dcb9...`,
   `BUNDLE_SHA256=fc64a5cb...b662` (= `V1001_REVIEWED_BUNDLE_SHA256`), `imageFlavourId 128` (Debian 13.7.0 UEFI), `diskName vda`
   (512 GiB), hostname v1001, server 804027, sshKeyIds [22556], customScript sha256 `96cd1254...c259a` len 2741,
   sent-text sha256 `1f788cf2...b6a3` len 2896, "Preflight OK. NOT calling POST". NO `--yes` run.
   `reinstall.log` is appended, so it also contains my first attempt's line `missing NETCUP_SCP_API_REFRESH_TOKEN` (see G1).

### Export contents (no secrets printed)

643 files, 7.5 MB, 10 top-level dirs; allowlist roots only (docker, etc, home, installer, logs, meta, partition, runner).
Files by dir: etc 67 (236 KB), home 559 (4.8 MB), installer 7, logs 2, meta 2 (`collection.txt` 246 KB), partition 3, runner 1,
docker 1 (volume names: only `mdt-buildkitd-cache`). Real evidence is ~84 files; 559 are the cockpit-home tree (479 files of
third-party `gate-tools` site-packages, 26 `gate-tools2502`, uv/pip caches, skills): noise, but in scope per the runbook.
Notes: 636 `exported:` and 557 `skipped` lines: 433 binary/NUL (pyc), 70 unreadable symlink loops, 23 "unredacted
secret-like value remains" (third-party venv sources), 20 lines >4096 B, 5 sensitive filenames (`.docker/.token_seed*`),
3 symlink dirs. Absent on this host: `/etc/mdt-cow-root.sh`, `/var/log/debian-install`, `/var/lib/iobench`,
`/var/lib/iogovmon` (the host predates them), so no previous iobench/iogov state exists to preserve.
Secret check beyond the exporter's own two passes (independent grep): no PEM, JWT, `ghp_/glpat/xox/AKIA`, URL userinfo,
40+ char base64 tokens or secret-named key with a plain value; no docker auth config exported; `runner.env`,
`bootstrap.env` carry only names/paths/pins. The key matcher over-redacts harmless values (`PrivateNetwork`/`PrivateTmp` in
`cgpd.service`, `credential_mode` in `installer/state/state.json`, two comment lines in `host-setup.env`): accepted loss
per the runbook. Cosmetic: `home/` and `installer/` came out mode 0755 inside the 0700 export root (files are 0600).

Redaction known gaps (runbook 4.2, unchanged and still true): Slack/Discord webhook secrets in URL paths outside
`/hooks/<token>`; Kubernetes/Compose `name:`/`value:` pairs incl. JSON name/value objects; prose like "the password is X";
INI `[section]` names; bare `key`/`encryption_key`/`signing_key`/`tls_key`; bare `auth`/`cookie` fields; raw JWTs; PGP
private-key blocks; multiline or encoded forms; `-p <path|1-5 digits>` kept by design. Review the export before it leaves
the controller (it does not leave it: it stays local). The export is a config snapshot taken while workloads run; the
campaign/checkout data is NOT in it (step 0 export covers that). If config changes before the window, re-export under a
new `--date` or `mv` the old dir aside first (the exporter refuses an existing dir).

## 7. Remaining command sequence and stop-points

Legend: **[TELL OPERATOR]** = inform/get the go; **[STOP]** = abort and report if it does not hold.
All host commands after `source $R/dstdns-r2/env.sh; v1001_api_check` and the on-host guard. `R=<controller-program-dir>`,
`P=/workspaces/vbpub/.worktrees/v1001-reinstall-prep-20261108`.

1. **[TELL OPERATOR]** D1-D4 answered; stop of the shards announced (fast-path ruling authorises it, "still inform").
2. Mark DRAINING (sat flag already set); stop shards per 3.4 steps 1-5; **[STOP]** if a pid/container cannot be stopped.
3. Step-0 export per 3.4 step 6 + manifest block + board update; **[STOP]** if `sha256sum -c` fails.
4. Image save per section 5; verify sha256 and size.
5. Apply the operator-scripts patch locally (`cd $R && patch -p1 < reports/div2-operator-scripts-v2.patch`, dry-run already
   clean), then set `RUN_GATE_VERSION=25.0.2` and its sha256 in `v1001/runner-scripts/runner.env` (the patch does not).
6. Re-read live disk (`sfdisk -d`, `lsblk -b`): unchanged vs this export (vda3 start 2500608, 512 GiB). **[STOP]** if different.
7. Confirm `git ls-remote origin refs/heads/main` still equals `7345dcb9...` (hold merges, D2). If it moved: rebuild the bundle
   with the new full SHA, new `V1001_REVIEWED_BUNDLE_SHA256`, repeat the dry-run.
8. Final dry-run (3 min, same command as the runbook with `--run-dir $R/v1001/reinstall-20261107 --dry-run`, run from any cwd
   as long as `$P/scripts/netcup/.env` exists): confirm `EXIT=0` and the same `BUNDLE_SHA256`.
9. **[TELL OPERATOR] this is the destructive step**: `pre-reinstall.py v1001 ... --expect-bundle-sha256 fc64a5cb0625db50aa986d2b1f84ca2fcfabd6a838d8f5fa3a1f919166f0b662 --ssh-key-id 22556 --run-dir $R/v1001/reinstall-20261107 --yes`
   (exactly the runbook block with `--yes`; `ACCOUNT_SSH_KEY_ID=22556`, `REVIEWED_MAIN_COMMIT=7345dcb93c8b980114abfad3cfeb51211ca810c4`).
   The wizard monitors up to 1800 s for `stage2_done`. If it ends without both digest lines the wrapper says the install
   may already have been submitted: **[STOP]**, read the task state file, do not rerun.
10. **[TELL OPERATOR]** the new ED25519 host-key fingerprint must come from the provider console or another verified channel
    (the controller cannot obtain it by itself); then `$P/scripts/netcup/update-host-pin.sh v1001 --pin-file $R/v1001/ssh_known_hosts-v1001-20261105 --fingerprint SHA256:...`.
    No SSH post-install check before this.
11. Post-install checks (runbook 5.1-5.4) into `reports/div2-reinstall-v1001-RESULT.md`: lsblk/sfdisk vs the table (section 8),
    installer state (benchmark before io.cost), `findmnt /srv/cow` btrfs on vda4 by UUID, 4 swaps active, `/` ext4 160 GiB,
    vda5 unformatted and unmounted. **[STOP]** on any mismatch (fix forward only; never `wipefs` by hand).
12. Runner provisioning: sync the complete `scripts/netcup/runner/` dir from the pinned commit, run `provision-runner.sh` (must
    say it selected `MDT_COW` by PARTLABEL and did not partition), `COCKPIT_IMAGE` by digest, then `docker load` the tester
    image + tag `tester-unified:local`, `setup-cockpit-run-gate.sh 25.0.2 42435a73...`, restore the host-only `run-*-r2502.sh`
    scripts from the snapshot, restore the two checkouts + campaigns from the step-0 export to the same paths.
13. If raw benchmark runs are wanted (D4): do them NOW, before resuming shards (section 8).
14. **[TELL OPERATOR]** resume: re-run the supervisors with the same campaign names (`cmru-r2c-s4`, `cmru-r2c-s12`) through
    `run-job`; the first attempt log must show `resume` with `pending_total` near 108 / 201, not a new baseline with the full
    plan. If it re-baselines or refuses, record it and restart fresh (design 4.2). Then s5-s8, s13, s14 and the s11 rerun
    (after its BAD_LANE_CONFIG is fixed) run fresh on the new stack.
15. Remove `host-sat-queue.disabled.v1001`, run the accepted runner smoke + one remote lane, update the board, then (separate
    window, after tnt R2) r1002.

Expected shard downtime: about 2-3 h (export 10-20 min, image save 5-10, wizard up to 30 min + stage two incl. the
veryquick benchmark, post checks, cockpit pull 8.5 GB, tester load, venv, smoke).

## 8. Risks, data-loss scope, raw benchmark partition

Data loss on provider reinstall (everything on vda): root fs (all of `/home/runner`: checkouts 1.6 GB incl. the host-local
c7d07 commit, campaigns, jobs, caches, venvs, incoming bundles, cockpit home/config), all docker state (cockpit image 8.5 GB,
tester-unified 2.97 GB with no registry copy, dstdns/test-runner 1.24 GB, tester-base 1.18 GB, volume `mdt-buildkitd-cache`,
container layers incl. ~2.5 GB/shard of pytest basetemp), `/srv/cow` (btrfs, 168 GiB device, 2.5 GB used: `mdt-cow`
tree cache/uppers, 6.6 GB listed by du on the subtree, all rebuildable cache), the swap partitions and the host SSH key.
No Netcup snapshot exists as a rollback. **Exported/preserved after the steps above:** system config evidence (7.5 MB),
partition tables, shard state + checkouts + campaigns + job logs (~600 MB), host-only runner scripts (done), tester image
(tar), pins. **Not preserved:** buildkit cache, cow cache, all non-shard checkouts (dstdns `b0d031c7`, etc.; results of those
runs already live in `dstdns-r2/`), anything else under `/root` or `/var/lib` outside the exporter allowlist.

Risks:
- R1 `--expect-commit` equals the CURRENT GitHub head of `main` at preflight and `--yes` time (`pre-reinstall.py`,
  `_current_remote_commit`). `main` moves often (origin/main is 190 commits ahead of the controller's local main). A merge
  between dry-run and `--yes` makes the wrapper refuse (fail closed, safe, but it costs a rebuild). Hold merges for the window
  (D2) or use a dedicated pushed branch for the reinstall.
- R2 Resume across a reinstall was never done (design 4.2 accepts fresh restart): worst case ~34 compute-hours lost.
- R3 io.cost/iobench interplay: the installer runs `veryquick` on vda5 (270 s outer timeout) in stage two before io.cost.
  `IO-BENCHMARK-DESIGN.md` says the result is advisory and the installer does not write `io.cost.model/qos`, while the runbook
  (section 1 and 5.2) says io.cost consumes it: contradictory, verify at post-install step 5.2 (do not assume). IB-VQ found
  veryquick usable only as identity/sanity, not for tuning values. Resumed shards plus a benchmark share the same virtual
  disk and io.cost: results would be polluted, so benchmark first, resume after (adds ~1-2 h of shard downtime).
- R4 Runbook 5.4 says "do not rerun the benchmark manually". A raw `quick`/`standard` run writes vda5 and ends the
  "still unformatted" property, so it must follow the 5.4 verification and needs an explicit OK.
- R5 The host-only `r2502` wrappers, the `tester-unified:local` tag convention and `gate-tools2502` are undocumented in the
  repo: the plan depends on my snapshot and the saved image.
- R6 Provider console access is needed for the new host-key fingerprint (step 10).

Partition layout of data-area-v2 (512 GiB, verified offline plan; identical on r1002):
vda3 root ext4 160 GiB (start 2500608); vda4 `MDT_COW` btrfs `/srv/cow` 326.81 GiB (338044928, 685363200 sectors);
**vda5 `iobench-raw`, 8 GiB, unformatted, start 1023408128, 16777216 sectors (488.00-496.00 GiB): the raw benchmark
partition**; vda6-vda9 swap `vbpub-swap6..9` 4 GiB each. No reserve.

If the operator wants raw-partition benchmark runs right after the reinstall:
- Target is `/dev/vda5` only (root, initial PID namespace, `iobench target init --device /dev/vda5 --i-own-this-device`;
  the installer already created an iobench guard state and one veryquick result, `io-benchmark.json`, under the installer
  state dir; copy it out first). Raw removes the btrfs F1/F2 problems and the file-layer noise IB-VQ identified; a PARTUUID
  guard binds the device, so it will not touch vda4.
- Size: 8 GiB may be too small for a meaningful `standard` run (`device_smaller_than_cache_margin` fires when the largest
  rung is below 1.2x the cache cliff; IB-VQ measured a ~110 MB burst and a 212 s knee on a file, the raw cliff is unknown).
  If the operator wants a safe `standard` on raw, change `partitioning.iobench_raw_gb` (allowed 2-10240) to e.g. 32 or 64
  BEFORE `--yes`: MDT_COW shrinks by the same amount, every later partition start moves, the runbook table and post-install
  checks must be re-resolved, the bundle hash changes, so rebuild and rerun the dry-run (plus the usual review). Otherwise
  keep 8 GiB and run veryquick/quick (3/11 min planned) there; planned `standard` was ~47 min on a file.
- Order: after post-install checks 5.1-5.4, before runner provisioning load and before resuming shards; keep the experiment
  guards (`--confirm-quiet-window --protect-cgroup dev.slice --abort-psi 25`; operator ruled io-full PSI is not a stop signal,
  stop only on memory PSI some > 25, swap/OOM, disk floors).

## 9. Runbook gaps and contradictions found (report-only, nothing improvised)

- G1 The wizard (`install-host.py`) is spawned with cwd = its own `scripts/netcup` and finds the Netcup token only in
  `cwd/.env`, `scripts/netcup/.env` or the repo root: a fresh worktree has none, so the runbook commands fail with
  `missing NETCUP_SCP_API_REFRESH_TOKEN`. Fixed in the prep worktree with a gitignored symlink to the main checkout's token
  file (no copy, no print). Any other worktree needs the same. `/workspaces/vbpub/.env` shadowing was not observed today.
- G2 Runbook worktree path no longer exists (use the prep worktree).
- G3 Runbook says resume on r1002 before reinstall; r1002 cannot host it now (D1).
- G4 `r1-vm-real-commit` FAILs on the merged tree due to a stale expected test name (section 4).
- G5 Runbook date 20261107 in export/run-dir names vs today 20261108 (I used 20261108 for the export and a separate
  `reinstall-prep-20261108` dry-run dir; `--yes` will use `reinstall-20261107` as written; reinstall.log appends).
- G6 `runner.env` pin set is incomplete in the patch (no `RUN_GATE_VERSION`/sha); operator copy 24.3.0, live 25.1.2,
  requested pin 25.0.2.
- G7 IO-BENCHMARK-DESIGN vs runbook disagree on whether io.cost consumes the installer benchmark (R3).
- G8 Shard `s11` is an unrecorded failure (BAD_LANE_CONFIG), and a dead third supervisor record exists.

## 10. Artifacts (all under `<controller-program-dir>/`)

`v1001/pre-reinstall-20261108/` (export + SHA256SUMS), `v1001/debian-install-v2-customscript-20261107.json`,
`v1001/reinstall-prep-20261108/` (dry-run log, bundle snapshot), `v1001/stale-20261107/` (quarantine),
`v1001/runner-scripts-host-snapshot-20261108/`, `logs/v1001-prep-r1-vm-real-commit-20261108.log`,
`logs/v1001-prep-r1-vm-real-commit-rerun-20261108.log`, `reports/v1001-prep-vm-lane-stale-name.patch`,
`host-sat-queue.disabled.v1001` (flag), worktree `/workspaces/vbpub/.worktrees/v1001-reinstall-prep-20261108` (keep for the window, remove afterwards).

---

# ADDENDUM 2026-11-08 (after the coordinator's rulings; still no destructive action, nothing stopped on v1001)

Rulings applied: (T) the reinstall starts only on the coordinator's signal; until then nothing is stopped and the bundle
stays UNBUILT; (D1) stop + export + resume on v1001 approved (deviation from runbook step 0); (D4) `iobench-raw` is not
enlarged, swap becomes 64 GiB at the end of the disk; (D-console) TOFU host-key pin; (D3) the VM-lane verifier fix is merged
after the reinstall.

## A1. State changes made for the rulings

- **Bundle unbuilt again.** My earlier bundle and dry-run dir were moved (not deleted) to
  `v1001/superseded-prep-20261108/` (`debian-install-v2-customscript-20261107.json`, `reinstall-prep-20261108/`). They pin
  `7345dcb93` and the 16 GiB layout and must NOT be used. `v1001/` now holds no `*customscript*` file.
  The bundle is built at the signal against the then-current `origin/main` head (`--repo-commit <full sha>`), then dry-run,
  then `--yes`; merges freeze only from the dry-run to `--yes` (D2 was my risk R1; this is the coordinator's design).
- **New v1001 config (not a bundle):** `v1001/debian-install-v2-20261108-swap64.json` = the 20261107 config with
  `swap_disk_total_gb: 64` (one-line diff, `swap_partition_count` stays 4). The 20261107 file stays for r1002-style use.
- Nothing else touched; the v1001 queue flag, the export and the host-script snapshot stay valid (the export is a config
  snapshot; the swap change does not alter any file in it).
- The runbook itself is unchanged. Runbook text changes are in `reports/div2-runbook-swap-benchmark.patch` (apply from the
  release-program root: `cd $R && patch -p1 < reports/div2-runbook-swap-benchmark.patch`; dry-run clean).

## A2. TOFU host-key pin (D-console) replaces runbook step "provider console fingerprint"

After the provider reinstall AND the completion marker `stage2_done` (a scan earlier would pin the OLD pre-reinstall key
that the host still presents while the image is being written):
```
R=<controller-program-dir>
FP=$(ssh-keyscan -T 10 -t ed25519 <host-address> 2>/dev/null | ssh-keygen -lf - -E sha256 | awk 'NR==1{print $2}')
echo "$FP"      # record in the RESULT file, marked TOFU
$P/scripts/netcup/update-host-pin.sh v1001 --pin-file $R/v1001/ssh_known_hosts-v1001-20261105 --fingerprint "$FP"
```
`update-host-pin.sh` rescans itself and refuses unless its scan equals `$FP`; with a fingerprint taken from the same
network path that is a consistency check, not authentication. **This is trust on first use, accepted by the operator**; I will
note "TOFU, key obtained by ssh-keyscan from the controller network path, not verified out of band" in
`reports/div2-reinstall-v1001-RESULT.md`. A cheap extra: scan twice ~60 s apart and require identical output before pinning.

## A3. Controller as a third lane during v1001's downtime: concrete proposal (NOT started)

Facts measured now (01:38Z):
- Controller RAM 15.99 GB: used 10.99 GB, **available ~5.0 GB**, buff/cache 5.2 GB; swap 70.6 GB total, **18.4 GB in use**
  (devcontainer ~9.6 GB of it). Memory PSI some avg10 2.0 / avg300 1.2 (can spike: 59 during the iobench run), io PSI some
  19.8 / **full 16.7 avg10 already**, 8 cores at load ~4-5, production Wings container on the same host, a `pwmcp r1` gate
  lane running right now. CPU is not the constraint (3-5 cores free), **memory and io are**.
- One shard lane container on v1001: image `tester-unified`, user 1003, cap `memory 4g / memory_swap 16g / cpus 3 /
  pids 8192`, `candidate_ram 1536m`, `--jobs 2`; measured RSS **1.1-1.5 GiB** (s12 1.14, s4 1.53) at 80-170% CPU. So
  one shard = 2 workers ~ 1.5-2 GiB plus page cache, peak up to the 4 GiB cap.

Portability of the exported state (resume semantics):
- Resume key = assay `judge_sha256` (mutation.py `judge_sha256`): judged tree content hash (git objects, portable), the
  command argv, lane `env` names and declared values, **fingerprints of env_passthrough values**, cwd/project prefix,
  link paths, assay tool version, and the **pytest runtime fingerprint (Python version string, implementation, machine,
  pytest version, plugin versions)** from inside the tester image. A drifted identity "fails in the safe direction (never
  resumes)": the candidates are re-judged, not corrupted.
- Therefore in-flight s4/s12 state is portable only to a host that runs the **identical image** (`sha256:d38b3324...`)
  with the same env names/paths. The controller's `tester-unified:local` is `09fa2f5e9846` (wave-20261106a), a different
  build: **resuming s4/s12 on the controller would silently become a fresh run (up to ~34 h of work re-done).** Loading the
  saved d38b image on the controller under its own tag (`tester-unified:r25-d38b3324fba5`, never retag `:local`) would fix
  the image part, but paths/env/observer socket remain unproven, so I do not recommend it. Not worth a probe.
- Fresh shards are fully portable: they carry no state. Plan: s5-s8 on commit `37ea9ad87` which already exists on the
  controller as the clean worktree `.worktrees/cmru-r2-shards-20261105` (same SHA, `run-gate admission show`: disabled,
  no cap on the controller). s13/s14 are defined only on host-local commit `c7d07240` which is an **empty commit on
  37ea9ad87 (same tree)**, so the controller worktree is equivalent. s11 needs its BAD_LANE_CONFIG fixed first.
- Host-bound pieces on v1001 that do not exist on the controller: bind mounts of the checkout at both
  `/workspaces/<sha>` and `/home/runner/checkouts/<sha>`, `/srv/cow/mdt-cow`, the `/run/cgprofile/observer` socket
  (`resource_observer = "cgprofile"`; the host wrappers pass `RUN_GATE_PROFILE=off`, how the controller's run-gate 25.2.1
  treats that lane declaration is unverified), cgroup parent `dev-gates.slice`.

Safe? **Conditionally yes, for ONE shard container with `--jobs 2` (2 workers; 3 only with `--jobs 3`, which changes the
lane and is unverified).** Not two shard containers. Useful? **Yes, but not as a stopgap**: a shard takes ~12-25 h, v1001's
downtime is ~2-3 h, so a controller shard is a permanent third slot that keeps running after v1001 returns and takes tail
shards off v1001's critical path (the tail s5-s8, s13, s14, s11 is 7 shards, ~3 days on v1001 alone).

Proposal when you say go:
1. Controller runs `assay-r2-s5` fresh (then s6, one at a time) from the clean shard worktree:
   `cd /workspaces/vbpub/.worktrees/cmru-r2-shards-20261105/cmru && nice -n 19 ionice -c3 run-gate assay-r2-s5 --jobs 2`
   under the supervisor pattern (resume loop, exit-4 budget restarts), log under `$R/r2-session/`; nothing else heavy at the
   same time (the running pwmcp gate must have ended; no builds).
2. v1001 resumes s4 and s12 after the reinstall, then s7, s8, s13, s14; s11 after the fix. r1002 after its tnt closeout.
3. Guardrails checked every ~10 min by the supervisor: `MemAvailable >= 3 GiB`, memory PSI some avg10 <= 25, swap used not
   growing >= 256 MB per probe, no OOM; on breach `docker stop` the exact container (the shard resumes later from its own
   progress file on the same host; never move a half-done shard to another host). Production Wings is the priority.
4. Keep every shard on one host and one image for its whole life.
Decision needed: go/no-go and whether to start it now (it does not depend on the v1001 stop).

## A4. Layout change: total swap 64 GiB (4 x 16 GiB) at the end of the disk

Knobs (config.py): `swap_disk_total_gb` integer 1-10240 (null = auto: min(2 x RAM, 20% of disk) = 16 GiB here),
`swap_partition_count` 1-64 (default 4). So **`swap_disk_total_gb: 64` + `swap_partition_count: 4`** gives four 16 GiB
partitions; `swap_policy` stays `on`. No code change: the installer sizes tail swap from these two values, the health gate
counts swaps from the plan (not a hard 4 x 4 GiB), `_activate_swap_partitions` does `mkswap -L vbpub-swap<k>` +
`swapon -p 10` and registers each in fstab as `PARTUUID=<id> none swap sw,pri=10,discard=once 0 0`.

Offline plan (`plan --disk-size 512GiB --root-start-sector 2500608 --root-number 3`, config swap64):

| Partition | PARTLABEL | Start sector | Size (sectors) | GiB |
|---|---|---:|---:|---:|
| vda3 | root ext4 | 2500608 | 335544320 | 160.00 |
| vda4 | `MDT_COW` btrfs `/srv/cow` | 338044928 | 584699904 | 278.81 |
| vda5 | `iobench-raw` (unformatted) | 922744832 | 16777216 | 8.00 |
| vda6 | `vbpub-swap6` | 939522048 | 33554432 | 16.00 |
| vda7 | `vbpub-swap7` | 973076480 | 33554432 | 16.00 |
| vda8 | `vbpub-swap8` | 1006630912 | 33554432 | 16.00 |
| vda9 | `vbpub-swap9` | 1040185344 | 33554432 | 16.00 |

Ends at sector 1073739776 (2048 sectors before the disk end, as before). `MDT_COW` shrinks 326.81 -> 278.81 GiB (it is
168 GiB today, still +110 GiB). r1002 keeps 4 x 4 GiB unless the ruling is extended (repo examples and the netcup suite
assert 16 GiB; do not touch them).

**`iobench-raw` must stay.** The installer's stage-two `veryquick` (`_run_io_benchmark`) resolves the partition with
PARTLABEL `iobench-raw` and `run_io_benchmark: true` is refused without it. Order in stage two (installer.py 4530-4536):
benchmark -> root growth -> swap activation: at benchmark time the swap partitions exist in the table but are blank and not
active, so the installer cannot benchmark a swap partition without a code change. It could be dropped only with
`iobench_raw_gb: false` AND `run_io_benchmark: false` (saves 8 GiB, loses the install-time identity/sanity result):
not recommended.

Changed bundle hash and what to re-review:
- The bundle embeds the config, so the hash changes; it also pins the commit. Preview only (nothing written): config swap64 +
  pin `7345dcb93` -> `ffda4ffd46b630299f9fbc6a3815ee81e27bf9e9697bb4c2d646c68cf8828417` (the old 16 GiB bundle was
  `fc64a5cb...`). The real value is produced at the signal for the then-current head; `--expect-bundle-sha256` always
  comes from that dry-run.
- Re-review (cheap, no code change): (1) config diff = one line (`swap_disk_total_gb 16 -> 64`); (2) the resolved table above
  by an independent `plan` run and sector arithmetic (ends 2048 sectors before the end, MDT_COW 584699904 sectors);
  (3) runbook patch; (4) the new `swap-bench-window` script (below) once, independently, before first use; (5) bundle hash
  at the signal. Downstream effects to check after install, no code change: host-setup `DEV_SWAP_MAX=auto` and the
  `*_MEMORY_SWAP_MAX=auto` values derive from the host's total swap (80% cascade, "swap stays AMPLE"): they will be ~4x
  larger, bounded by the IO caps; the lane declarations (`memory_swap = 16g`) are explicit and unchanged.
- Post-install checks that count swaps: runbook 5.1 and 5.3 only (patched: four swaps, 16 GiB each on v1001, four fstab
  PARTUUID lines). The installer's own health gate is plan-driven.

## A5. Complete procedure: raw iobench on a freed swap partition (rehearsed in a disposable QEMU guest)

What iobench requires of a raw device (guard.py, verified by execution):
1. A **swap-active** device is a hard refusal that no flag overrides (`init` and every later `verify`). Also refused: mounted,
   root, holders (md/dm/LVM). So the swap must be off first.
2. After `swapoff` the partition still carries `TYPE=swap`. `iobench target init` then refuses with a "signature" violation
   unless `--i-own-this-device --expect-size BYTES --expect-partuuid ID` are given; with them it writes only a marker into the
   **last MiB**, it does NOT clear the swap signature. `iobench calibrate`/`verify_target` then refuses with
   `foreign_signature` ("do not run iobench against it; restore the device"). **So the swap signature has to be wiped**; this
   is exactly what the installer does to `iobench-raw` (`wipefs -a <dev>` guarded by not-mounted and not-swap-active, then
   PARTUUID read, then runner with `--expect-size/--expect-partuuid`).
3. The device must have a PARTUUID (GPT partitions do), exact size and PARTUUID are bound in the marker; a changed PARTUUID or
   size refuses the next run.
4. Root and the host's initial PID namespace are required (`/proc/1/ns/pid`); the CLI needs the `iobench` and
   `cli-extended` distributions installed (a bare source tree fails with `VersionLookupError: distribution 'iobench' is not
   installed`): use the IB-VQ recipe (wheels built from the pinned archive with `SETUPTOOLS_SCM_PRETEND_VERSION`, installed
   `--no-deps` into a throwaway root venv).

Sanctioned procedure (the runbook ban is on hand-run `wipefs`/bypassing guards; this reuses the installer's own wipe under
the installer's own preconditions, wrapped so no check can be skipped): draft script
`reports/swap-bench-window.draft.sh` (not in any repo; needs one independent review before its first use on a host, then it
is copied to the host as root-owned 0755; the repo copy can follow after the reinstall with the D3 fix).

```
# 1. window open (dry-run first: prints checks + exact commands; nothing changes without --commit)
swap-bench-window.sh begin /dev/vda9 --expect-server-id 804027            # dry-run
swap-bench-window.sh begin /dev/vda9 --expect-server-id 804027 --commit
#    guards: root; /etc/vbpub/server-id == 804027; partition path; PARTLABEL ^vbpub-swap[0-9]+$; GPT type Linux swap;
#    TYPE=swap now; exactly ONE `PARTUUID=<id> none swap` line in /etc/fstab; not mounted; no holders; swap-active;
#    the other swaps have free room > pages in use + margin; MemAvailable > pages in use + margin
#    records /var/lib/vbpub/swapbench/vda9.env (label, UUID, PARTUUID, size, PARTLABEL); swapoff (verified); wipefs -a
#    (this device only, verified signature-free)
# 2. benchmark (root, initial PID ns, pinned venv)
iobench target init /dev/vda9 --i-own-this-device --expect-size $(blockdev --getsize64 /dev/vda9) --expect-partuuid $(blkid -s PARTUUID -o value /dev/vda9)
iobench calibrate --target /dev/vda9 --state-dir <dir> --work-dir <dir> --profile veryquick|quick|standard \
        --confirm-quiet-window --protect-cgroup dev.slice --abort-psi 25
# 3. window close, ALWAYS (also after a failed run); refuses while the device is still open
swap-bench-window.sh end /dev/vda9 --expect-server-id 804027              # dry-run
swap-bench-window.sh end /dev/vda9 --expect-server-id 804027 --commit
#    verifies PARTUUID/size/PARTLABEL unchanged, nothing holds the device, zeroes the marker MiB,
#    mkswap -L <same label> -U <same UUID>, swapon -a (fstab supplies pri=10,discard=once), verifies PARTUUID/UUID/LABEL and
#    swap-active; archives the state file
```
Why this restores "the same": fstab names the **PARTUUID**, which is a GPT property that `mkswap` does not change, so no
fstab edit or systemd unit change is needed; the label and the swap UUID are restored with `-L`/`-U` (the installer sets
`-L vbpub-swap<k>`; the UUID is cosmetic but kept). `swapon -a` takes priority and `discard=once` from fstab. systemd's
`dev-disk-by\x2dpartuuid-....swap` units were active again after the cycle.

Rehearsal evidence (logs under `$R/logs/`, disposable QEMU/TCG guest, loop-device GPT with swap-typed partitions, real
`iobench` from the pinned tree built into wheels):
- `v1001-prep-swapbench-rehearsal-output-20261108.log`: T1 swap-active -> hard refusal (rc 2); T2 swapoff only -> signature
  refusal without `--i-own-this-device`, marker written with it; T3 calibrate with the swap signature left ->
  `foreign_signature` refusal; T4 after the wipe `init` and `calibrate --dry-run` (veryquick, planned 201 s on 1 GiB) succeed;
  T5 `mkswap -L -U`, PARTUUID identical before/after, fstab line unchanged, `swapon -a`, systemd swap units active:
  `RESTORE-OK`.
- `v1001-prep-swapbench-window-test-20261108.log`: the script itself: refuses wrong server id, refuses a non-swap partition,
  dry-run changes nothing, `begin --commit` frees the device, a second `begin` refuses (window open), `iobench` init +
  calibrate dry-run pass, `end` refuses while a process holds the device, `end --commit` restores label/UUID/PARTUUID and
  the swap is active again, state file archived. This test caught a bug in my first draft (partition regex rejected
  `loop0p2`), fixed. Not tested: a real fio run and swap with real pages in use (the guard `used + margin` path was only
  exercised with 0 KiB used); on v1001 swap in use is ~100 MB so a 16 GiB partition frees instantly.
- Helper for the rehearsal: `v1001/swapbench-rehearsal-20261108.sh` (refuses to run outside a QEMU guest). The guest and the
  loop devices are gone; no controller loop device or swap was ever used.

Risks specific to the procedure: (1) while one 16 GiB partition is out the host has 48 GiB swap (fine, ~0.1 GB used);
(2) `mdt-dev-governance-reconcile` (timer) may recompute the `auto` swap caps from `/proc/swaps` (not verified): a window of
3-50 minutes could briefly lower them, then they return (check `mdt-host-check` afterwards); (3) a crash between begin and end leaves a
partition out of swap with a state file: `end` is idempotent from the state file and is the documented recovery, and
`vbpub-swap-health`/`swapon --show` makes it visible; (4) run windows only with the quiet-host rule (before shards resume),
because io.cost and shard IO would pollute the measurement.

## A6. Updated remaining sequence (changes vs section 7 of this report)

1. WAIT for the coordinator's signal (merge queue ~3 h). Until then: nothing stopped, no bundle. Optional meanwhile (all
   read-only/reversible and approved by the decision list): review of the draft `swap-bench-window` script by a fresh Sonnet.
2. At the signal: identity checks; stop shards (3.4), export + manifest, image save, apply the operator-scripts patch and set
   the runner.env pin (RUN_GATE 25.0.2 + sha), re-read the live disk.
3. Build the bundle for the then-current head with `debian-install-v2-20261108-swap64.json`; dry-run (EXIT 0 and the printed
   `BUNDLE_SHA256`), freeze merges; **[TELL OPERATOR]**; `--yes` with that digest.
4. After `stage2_done`: TOFU pin (A2), post-install checks 5.1-5.4 with the 16 GiB swap numbers, host-setup/pins, then the
   raw benchmark windows (A5) while the host is still quiet, then runner provisioning, restore, resume s4/s12 (controller
   lane per A3 can start independently at any time you choose).
5. After the reinstall: merge the VM-lane verifier fix (D3) and the swap-bench-window script via the normal reviewed path.
