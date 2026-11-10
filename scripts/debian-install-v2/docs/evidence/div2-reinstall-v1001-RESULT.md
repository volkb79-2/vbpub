<!-- origin: 2026-11-05; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/div2-reinstall-v1001-RESULT.md -->
<!-- relation: reinstall result; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# v1001 data-area-v2 reinstall: RESULT (2026-11-08)

Host agent (Sonnet 5.5). Server 804027 (v1001, <host-address>). Times UTC. Status: **STOPPED after post-install check 5.2 (benchmark step warned)**; no runner provisioning started.

## Reinstall run
- Pin: REPO_COMMIT `72ae0ddc0e57400732d7e716e75bec3670bf8c2b`; bundle `v1001/debian-install-v2-customscript-20261108-swap64.json` sha256 `96c004edf8e932c3660bf8e196ed4bbc946760832bb73a2c596dc424346add26`; config `v1001/debian-install-v2-20261108-swap64.json` (swap_disk_total_gb 64 x4).
- `--yes` started 17:08:41Z, wrapper `WRAPPER_RC=0`, `EXIT=0`, both digest lines present (customScript sha256 `b885fba5...b62d` len 2741; sent `3ae998a5...fb7f` len 2896), completion marker `stage2_done` seen at 17:20:29Z. Run dir `v1001/reinstall-20261108-swap64/`, output `v1001/run-yes-20261108-swap64.out`.

## Host key pin (TOFU)
- Obtained by `ssh-keyscan` from the controller network path, NOT verified out of band (operator-accepted TOFU). Two scans 67 s apart identical: `SHA256:SnsbyZDkCHs5ehkzEDjtPIwlWtCI7YWGob8wleFm1u0` (old pinned key was `SHA256:NOoEzjkMSmwicI3zV5I3LsCrgCk21aFWy96J6GjOyo8`).
- Old pin file kept as `v1001/ssh_known_hosts-v1001-20261105.pre-tofu-20261108`. `update-host-pin.sh v1001 --pin-file ... --fingerprint ...` updated `v1001/ssh_known_hosts-v1001-20261105`.
- DEVIATION / tool finding: the script refused ("expected exactly one scanned ed25519 host key") because the controller's OpenSSH `ssh-keyscan` now prints the `# host:22 SSH-2.0-...` banner comment to STDOUT, so its `grep -c '[^[:space:]]'` counts 2 lines. I ran the unmodified script with a PATH shim (`scratchpad/shim/ssh-keyscan`, only drops `#` lines); the script's own rescan + fingerprint equality check still applied. To be filed in `scripts/netcup` backlog (fix: `grep -vc '^#'`).

## Post-install checks (runbook 5.1-5.4)
Layout matches the v1001 plan exactly (identity ssh via the new pin, guard hostname/static OK; `/etc/vbpub/server-id` absent as expected until provision-runner).

| Check | Expected | Live | OK |
|---|---|---|---|
| vda3 root | start 2500608, 335544320 sectors, ext4 160 GiB | same; `/` ext4 on vda3, df 158G | yes |
| vda4 MDT_COW | start 338044928, 584699904 sectors, 278.81 GiB, btrfs | same; btrfs UUID `1a79a23b-6d5e-4488-b7e6-6fc1a68d0fbd`, `findmnt /srv/cow` = /dev/vda4 btrfs, fstab `UUID=1a79a23b... /srv/cow btrfs noatime,user_subvol_rm_allowed,nofail` | yes |
| vda5 iobench-raw | start 922744832, 16777216 sectors, 8 GiB, unformatted, unmounted | same; blkid shows only PARTLABEL/PARTUUID (`8a2f9aaa-e7bc-4b0d-9ac5-7f449e037ed9`), not in /proc/mounts or /proc/swaps | yes |
| vda6-vda9 swap | 4 x 16 GiB at 939522048, 973076480, 1006630912, 1040185344 | same; `swapon --show` four 16G pri 10 used 0B; fstab four `PARTUUID=... none swap sw,pri=10,discard=once` lines (da6a941f..., 16bb52f3..., 1485ce11..., 14f2db56...) | yes |
| GPT end | ends 2048 sectors before disk end | last-lba 1073741790, vda9 ends 1073739776 | yes |
| installer state | status success, health_gate 4 swaps verified | state.json `status=success`, `phase=done`, `health_gate = 4 swaps verified (0 retained before root)`, run_id 13752c99b7347946 | yes |
| io benchmark | measurement recorded, io.cost configured | **`steps.io_benchmark = warned: "iobench failed (advisory): action failed (1): run iobench veryquick on /dev/vda5"`; `steps.iocost = skipped: "io.cost not configured: no valid io benchmark result"`** | **NO (mismatch)** |

Other state: OpenSSH, Docker 29.9.3 active, kernel 7.2.12, cgroup2 flags `memory_recursiveprot+nsdelegate`, zswap zstd, oomd, `augeas-tools` installed (DIV2-AUG). `/etc/host-endpoints.env` and user `runner` do not exist yet (provisioning pending). Cpu benchmark success.

Swap partition naming (relevant to `swap-bench-window.sh`, NOT run): the swap partitions have EMPTY PARTLABEL; the vbpub-swap1..4 names are filesystem LABELs (vda6..vda9). The runbook table says "PARTLABEL `vbpub-swap6..9`" and the wrapper checks PARTLABEL `^vbpub-swap[0-9]+$`: it would refuse on this host. Only matters if the raw iobench window is ever run.

## Benchmark failure: what is known
- The iobench step runs `/opt/vbpub-debian-install-v2/tools/iobench-install-runner` (exit 1). No log of the reason survives: `/var/log/debian-install` does not exist, `<home>` holds only the two `[INFO]` lines, `/var/lib/vbpub/bootstrap/iobench` was never created (no iobench state), and the journal has no iobench lines.
- Read-only checks: `fio` and `fuser` installed; the runner's imports (`iobench.guard/calibrate/host/stages`) succeed under `python3 -I` from the installed source; the partition still carries no signature (installer wiped it, `guard.init_device` marker, if written, sits in the last MiB).
- Not re-run: a manual `iobench` run on vda5 ends the "still unformatted" property and the runbook forbids it without an explicit OK (R4).
- Consequence: no installer benchmark result and `io.cost` NOT configured on v1001 (host-setup may still apply its own io.cost policy; unverified).

## Benchmark diagnostic (coordinator decision 1, vda5 only, one run, 17:25Z)
`/opt/vbpub-debian-install-v2/tools/iobench-install-runner --target /dev/vda5 --expect-size 8589934592 --expect-partuuid 8a2f9aaa-... --state-dir /var/lib/vbpub/bootstrap/iobench` as root: RC=1 in under a second, `iobench.errors.Refusal: fio_option_unsupported: the installed fio does not document the option 'exclusive_open'`. Debian 13 ships fio 3.39-1 which has no `exclusive_open` (`fio --cmdhelp=exclusive_open`: "No such command"; a `--exclusive_open=1` job on /dev/null: "unrecognized option"). iobench (`devio.require_fio_option` called from `stages.s0`) hard-requires it, the iobench docs claim fio >= 3.30. So the installer's veryquick can never pass on this OS image. Logs: `v1001/iobench-diag-20261108/{run.log,fio-probe.txt}`. vda5 untouched (unformatted, no iobench state). Decision 1: iocost step NOT run; io.cost stays unconfigured (known issue, re-measure with IB-VQ later). vda5 not touched again.

## Runner provisioning (decision 2) and smoke
- `provision-runner.sh` (pinned 72ae0ddc scripts + runner.env pin 25.0.2, COCKPIT_IMAGE by digest) EXIT 0: server-id 804027, runner uid 1003, CoW = /dev/vda4 resolved by PARTLABEL MDT_COW (no mkfs/partition call), /srv/cow/mdt-cow, ~runner dirs, uv + python 3.14.
- Tester image loaded (ID d38b3324...6fb6) and tagged `tester-unified:local`; cockpit image pulled by digest afd6dc2c...; shard export, campaigns, jobs, checkout c7d07240... and host-only `run-*-r2502.sh` restored (hashes verified).
- Cockpit: pinned `start-cockpit-runner.sh` refuses without `/etc/host-endpoints.env`; used the local variant `v1001/runner-scripts-local/start-cockpit-runner-nohostsetup.sh` (raw docker.sock bound at /run/docker-api/docker.sock, no cgprofile/buildkit mounts). `setup-cockpit-run-gate.sh 25.0.2` EXIT 0 (gate-tools2502 = 25.0.2), `setup-cockpit-tools.sh` EXIT 0 (default gate-tools run-gate 25.0.2, assay 12.0.1.dev258).
- Runner README steps skipped: step 5 (mdt host-setup `install.sh`, cgprofile observer, endpoint record `/etc/host-endpoints.env`) and the pinned `start-cockpit-runner.sh` (needs the record); steps 3-4 (sync-from-devcontainer bundle, setup-assay-venvs, setup-gate-tools, build-tester-*) replaced by the restored checkout + loaded image. What needs host-setup later: dev-gates/dev-interactive slices with limits (admission warning "no derivable memory ceiling for slice dev-gates.slice"), `run-cowtree-assay-smokes.sh` (`require_gates_slice` needs a slice FragmentPath), tree cache, io.cost/governance, the endpoint record for run-gate >= 25.4.0, and the cgprofile daemon (see below).
- Smoke: cowtree smoke NOT runnable (slice has no FragmentPath without host-setup). Remote lane `selftest` (run-gate-project) via remote-gate, id `sat-smoke-reinstall-20261108-v1001`, sha 72ae0ddc0e57: finished exit 0, verdict PASS (17:33-17:39Z).

## s13 resume: REFUSED before any work (STOP)
`run-r2-campaign-r2502.sh cmru-r2c-s13 ... --jobs 2` via run-job `cmru-r2c-s13-resume-20261108` (old campaign dir moved to `campaigns/cmru-r2c-s13.pre-reinstall-20261108`): attempt 1 ended at 17:39:45Z with `exit 2`: `cgprofile resource-observer preflight failed: cgprofile control socket /run/cgprofile/ctl.sock unreachable (socket-not-mounted: the socket directory does not exist here ...)`. Lane `assay-r2-s13` declares `resource_observer = "cgprofile"`, so run-gate 25.0.2 DOES need the cgprofile control socket (the pre-reinstall cockpit mounted /run/cgprofile from the host daemon); `RUN_GATE_PROFILE=off` in the wrapper does not waive it. No baseline ran, no judged state changed (progress events 65377 -> 65377, s13 progress file unchanged at 6556 lines, 151 candidates done of 277). The old wrapper `final-exit` = 2 now sits in the new campaign dir; supervisor ended; no container.
Options: (a) install the cgprofile daemon on v1001 (what the old host had; `cgprofiled install`/ciu, version question: repo main is cgprofile 3.0.0 after the rename, the old host ran 2.x with `/run/cgprofile/ctl.sock`), mount `/run/cgprofile` into the cockpit (variant start script), rerun the same command (resume identity unchanged); (b) change the lane declaration (breaks resume identity: fresh 277-candidate run). Not done: needs your decision.

## Queue
Smoke passed, so `host-sat-queue.disabled.v1001` removed and the queue started (loop pid 525504) at ~17:41Z. Note: lanes that need cgprofile/slices may record INFRA verdicts on v1001 until host-setup/cgprofile exist (5 INFRA in a row disables the queue itself). r1002 queue untouched (own state: alive, idx 94, first INFRA result #1 on cgroup-profiler focused at 17:40Z).
