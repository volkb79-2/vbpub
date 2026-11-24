# M1 run 7: fresh install from the current installer, S7 with the dedupe window, regression (2026-11-18)

Result: **the fresh install succeeds, S7 passes with exactly one snapshot per unattended run
(it was 18 in [run 6](RESULTS-M1-RUN6-S6-S7-S9-BACKPORTS-2026-11-18.md)), and S2/S3 and the kernel upgrade
still pass on the new disk.**

Same variant, guest setup and limits as the earlier runs (v1001, historical direct
launcher now represented by `vm-lab start --plain-host`; vm-lab was not used at the time,
TCG, 2 vCPUs, 2 GiB, a fresh guest per scenario). Transcripts:
`artifacts/debian-rollback/m1-run7/` (local).

## Install (13:53:39Z to 14:17Z, `DR-BUILD-DONE rc=0`, 1397 s of guest time)

A new disk from the installer at `80fda2d60` (seed built by `poc/build-seed.sh`): ifupdown + isc-dhcp-client,
`net.ifnames=0`, the `trixie-updates` and `trixie-security` sources, the dedupe window **as first written**
(parent-agnostic; the parent-aware version came later, see below). It is the base `installed-btrfs-3.qcow2` of
the three scenarios. Not separately verified in the guest: that the installer's own `dr-updates.list` is correct
(the kernel scenario writes the same file itself before using it). The initramfs build warns `No zstd, using gzip`.

## S7, unattended upgrade (14:17:49Z, 11.3 min): 5 checks PASS

The guest first replaced `/usr/local/sbin/dr-state` by the final version from the seed medium (attached as read-only
media; the driver checked that the parent-aware window code is in place), installed `unattended-upgrades` with
`DR_STATE=none` (no snapshot), and ran `unattended-upgrade -v` with `DR_SNAPSHOT_MIN_INTERVAL=7200`. It logged one
"Packages that will be upgraded" set, rc 0, and the run took **exactly one** snapshot,
`@os-pre-20261118142234-2955-apt`, although dpkg is called many times.

## Regression on the new disk

- `s2-s3` (14:29:08Z, 4.9 min): 15 checks PASS, same values as [run 4](RESULTS-M1-RUN4-S2S3-S5-2026-11-18.md).
- `kernel-upgrade` (14:34:05Z, 7.7 min): 15 checks PASS; 6.12.107 to 6.12.117, download 293,232 KB, same
  values as [run 3](RESULTS-M1-RUN3-KERNEL-2026-11-18.md).
These two ran with the `dr-state` that the installer put on the disk (the first, parent-agnostic window), not
with the final one.

## Not verified

- The `dr-state` on the seed medium for S7 was the one of commit `7284e55f8` (parent-aware window). So the
  **positive** lineage case ran in a guest: the snapshot of the running state, found through its `btrfs
  subvolume show` Parent UUID, suppressed the later dpkg calls of the unattended run. The **negative** case (an apt
  run in a rolled-back state must NOT be suppressed by a snapshot of another state) has not run in a guest; the
  time decision alone has an offline test (`poc/test-dr-state-window.sh`).
- S8 (30 states and pruning), the hook's own no-space path (S9b), S5 with the backports kernel, S12, snapper,
  systemd-boot, ZFS, Secure Boot, real hardware.
- A freshly installed system still has the kernel from the base suite (6.12.107): the installer does not run an
  upgrade at the end, so the first `apt upgrade` brings the kernel update (and its snapshot).
