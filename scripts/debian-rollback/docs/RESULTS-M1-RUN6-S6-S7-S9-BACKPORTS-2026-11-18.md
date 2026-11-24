# M1 run 6: S6, S7, S9 and the backports kernel (2026-11-18)

Result: **S6, S9 (narrowly) and the backports kernel pass; S7 failed its own check and found a
real defect, which is fixed in the tool but has not yet been re-run.**

Same variant (btrfs, own snapshots, ESP GRUB menu; snapper not used) and guest setup as
[run 2](RESULTS-M1-RUN2-2026-11-18.md) to [run 4](RESULTS-M1-RUN4-S2S3-S5-2026-11-18.md):
v1001, `poc/dr-qemu.sh`, vm-lab not used, TCG, 2 vCPUs, 2 GiB, each scenario in its own fresh
guest on the disk installed in run 2a (kernel 6.12.107, no updates or security suites, `dr-state`
without the dedupe window). The scenarios `s6`, `s7`, `s9` and `backports` of `poc/dr-serial.py`
(commit `9c7270623`) ran **before any review**; the series ran 13:20:07Z to 13:50:51Z on the host
clock. Transcripts are in `artifacts/debian-rollback/m1-run6/` (local).

## S6, removing the kernel an earlier state needs: 9 checks PASS (8.8 min)

After `apt dist-upgrade` (kernel 6.12.107 to 6.12.117, one snapshot) and a reboot into the new
kernel, `apt-get remove linux-image-6.12.107+deb13-amd64` in the running state succeeded and
removed the old kernel files there. The menu still named the old kernel for the earlier state;
`boot-once` into the earlier state booted **its own copy of 6.12.107** and its modules loaded.
Kernels live inside each state, so removing a kernel package in one state cannot break another.

## S7, unattended upgrade: FAIL on the first check set, and a defect found

`unattended-upgrade` ran with rc 0 and logged one "Packages that will be upgraded" set, and
snapshots were taken first, so the `DPkg::Pre-Invoke` hook does run for it. **But one run took 18 snapshots**: unattended-upgrade
calls dpkg repeatedly and the hook snapshots before each call. That floods the menu and the
states to prune (S8). My check expected exactly one, hence the FAIL.

Fix (commit `e4d212117`; **re-run in [run 7](RESULTS-M1-RUN7-FRESH-INSTALL-S7-2026-11-18.md): one snapshot instead of 18**; the window was later made parent-aware, see run 7): a dedupe window in `dr-state snapshot`:
for `--reason apt`, no new snapshot while the newest `@os-pre-*` is younger than
`DR_SNAPSHOT_MIN_INTERVAL` seconds (default 900; 0 disables; manual snapshots ignore it). The
rollback target stays the state from before the whole upgrade; intermediate states of one
window are not kept. S7 now installs `unattended-upgrades` with `DR_STATE=none` and expects
exactly one snapshot for the run.

## S9, full filesystem before an apt run: 9 checks PASS, but narrower than the design question

`/srv` was filled with `fallocate` until 728 KB were free. `apt-get install hello` then failed
by itself (rc 100: `Write error - write (28: No space left on device)`, `IO Error saving source
cache`) before it reached the snapshot hook. So: no half state (`dpkg --audit` empty, `hello`
not installed), the space was freed, `apt install hello` worked again (with its snapshot), every
state still had its menu entry and kernel, and the system still booted. **Not exercised: the
hook's own failure path** (snapshot refused because of no space while apt itself can still run).
That needs a margin of free space tuned so that apt works and the snapshot does not; not tried.

## Backports kernel (S11 first part), 15 checks PASS (7.5 min)

With `trixie-updates`, `trixie-security` and `trixie-backports`, `apt install -t trixie-backports
linux-image-amd64` brought kernel **7.2.12+deb13-amd64** next to 6.12.107 (download 347,524 KB,
about 339 MiB). The menu named 7.2.12 for the running state and 6.12.107 for the snapshot; the
default boot ran 7.2.12; `boot-once` ran 6.12.107 with its own modules; the next reboot returned
to 7.2.12; the ESP boot files were unchanged. Not done for the backports kernel: S5 (broken
initramfs) with it.

## Not yet verified

S8 (30 states and pruning, now more relevant), the hook's own ENOSPC path (S9b), the dedupe
window's negative lineage case in a guest (the window itself and its positive case ran in run 7), S12 (phase 2), Secure Boot, snapper on the flat layout, systemd-boot, ZFS,
real hardware. `s6`, `s7`, `s9` and `backports` have had no independent review yet.
