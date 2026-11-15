# Planned experiments

Status 2026-11-12: nothing has been run. Two experiments are planned. The
first needs no host and proves function; the second needs a real host and
measures performance and memory behaviour. Results go into this directory as
dated reports, and the filesystem decision (D14 in the
[design guide](DESIGN-GUIDE.md)) is taken from both.

## E1. Rollback scenarios in the VM lab

**Purpose.** Show that each candidate (btrfs with snapper; ZFS with boot
environments) delivers phase 1 on Debian 13 under UEFI, and find out how much
has to be built for each.

**Where.** Local QEMU guests without KVM, through the general VM tool planned
in `scripts/vm-lab/` (design guide section 9). VM files live in
`scripts/debian-rollback/artifacts/vm-lab/`. Guests are slow without KVM; this
experiment gives no performance numbers.

**Prerequisite.** The VM tool with: selectable RAM (1 to 4 GB), UEFI with a
per-guest variable store, key presses and screenshots, a serial console the
boot menu also prints to, a reset command, and a way to tell a boot loop from
a slow boot.

**Scenarios, for each candidate and each bootloader option under test**

| # | Scenario | Passes when |
|---|---|---|
| S1 | Install unattended from the ISO onto an empty disk | The guest boots and reports the planned layout |
| S2 | Run `apt install` of a small package | A snapshot of the OS state exists from before the change, and none of the "outside" paths are in it |
| S3 | Upgrade, then choose the earlier state in the boot menu | The earlier state boots with its own kernel and modules; the package is absent; a marker file written to `/home` and to a service data directory after the snapshot is still present |
| S4 | Make the earlier state permanent | After another reboot with no menu choice, the earlier state is the default |
| S5 | Install a kernel whose initramfs is deliberately broken | The new state fails to boot; the earlier state boots from the menu |
| S6 | Remove the kernel package the earlier state needs (as `Remove-Unused-Kernel-Packages` would) | The earlier state still boots, or the tool refuses the removal with a clear message |
| S7 | Unattended upgrade | A snapshot is taken for the unattended run too |
| S8 | Many states (30 upgrades) and pruning | The menu stays usable, pruning removes the oldest, disk use is reported |
| S9 | Fill the root filesystem, then upgrade | The snapshot step fails clearly before apt changes anything, or the upgrade completes; no half state |
| S10 | Edit a file in `/etc` after the snapshot, then roll back | The earlier `/etc` is active (decision D6) and the edit can be retrieved from the abandoned state |
| S11 | Backports kernel | S3 and S5 repeated with the backports kernel; for ZFS this includes a DKMS build |
| S12 | Phase 2 trial: new state on probation | A state that fails its health check is not the default at the next boot |

**What to record per candidate.** Which existing packages were used unchanged,
what had to be written, how many lines, and every manual step a user would
have to take.

## E2. Filesystem comparison on a real host

**Purpose.** Answer with our own workload whether root on ZFS behaves
acceptably on a RAM-constrained, memory-overcommitted host, compared with
btrfs and ext4. No published measurement covers this case.

**Host.** r1002 (netcup, UEFI, one 512 GiB virtual disk). The host is
reinstalled once per variant.

**Variants**

| Variant | Root filesystem | Cache setting |
|---|---|---|
| V0 | ext4 | page cache (baseline, today's layout) |
| V1 | btrfs | page cache |
| V2 | ZFS | ARC at its default (adaptive) |
| V3 | ZFS | ARC limited to about a quarter of RAM |
| V4 | ZFS | ARC limited to 256 MB (worst case) |

V2 is the variant the operator expects to use (decision D12). The earlier idea
of testing only small caps (128 to 512 MB) is replaced by this matrix. V3 and
V4 only change a module parameter and need no reinstall after V2.

Swap is on plain partitions with zswap in all variants, about twice RAM, as on
the estate's hosts.

**Workload, identical for every variant, each step run cold and warm**

1. Clone the repository and check out several revisions.
2. Run a pytest lane (serial).
3. Run one gate container.
4. `apt full-upgrade` from a pinned older package state, once with and once
   without the snapshot step.
5. Search the tree (`git grep`, `find`), as metadata-heavy work.
6. Memory pressure: run the workload while a second process holds enough
   memory to overcommit the host, so swap and zswap are in use.
7. Take 100 snapshots, repeat steps 1 and 5.

Whether the repository and containers sit on the root filesystem or on a
separate btrfs or ext4 data area changes the result (decision D11). Each ZFS
variant is run both ways: everything on ZFS, and ZFS root with the workload on
a btrfs data area.

**What to record**

- wall time per step;
- disk reads and writes per step;
- memory pressure (PSI `memory` and `io`, "some" and "full") over the run;
- ARC size over time against application memory and swap use;
- zswap pool size and swap-in rate;
- whether the OOM killer acted (expected: never);
- for mapped files, page cache plus ARC use while a large program and a large
  git repository are in use (the double-caching question).

Per the repository rule, a run disturbed by something outside the test is
inconclusive and repeated, not counted for or against a variant.

**Decision rule, proposed.** ZFS stays a candidate if the adaptive variant V2
keeps memory pressure during step 6 in the same range as btrfs and no step is
slower by more than a margin the operator sets after seeing the baseline
spread. To be confirmed before the run.

### Host lease

Agreed on 2026-11-12 with the Claude session `vbpub-controller-joint-release`,
which coordinates r1002 and v1001.

| Term | Value |
|---|---|
| Host | r1002 only |
| Start | Not before about 2026-11-13 14:00Z, after that host's iobench study day, and **only when the controller session sends "lease start"** |
| Length | Up to 48 hours |
| During the lease | Wipe and reinstall freely; nothing on r1002 is needed by the controller; the `host-sat-queue` pause flag stays set |
| Never | Touch v1001, nano1 or the controller. Pass `--cgroupns=host`, `--pid=host` or `--net=host` to a container. Do a real swap operation without cleanup |
| Hand-back | Reinstall with `debian-install-v2` using the controller's private config `$CONTROLLER_HOME/r1002/debian-install-v2-20261112.json` (`iobench_raw_gb` 32, swap 64, pools /14). Bring up the runner per `scripts/netcup/runner/README.md`. Verify with the runner smoke: identity, docker, tester-unified image, run-gate at least 25.4, cgprofile observer socket owned by root:root, one trivial remote-gate round trip |
| If restore fails | Leave the host in the rescue system and tell the controller exactly what state it is in |
| Records | A log of everything done goes to the program directory. The results go into the repository as a report, so the host plan (btrfs `/srv/cow`, root filesystem) can use them |
| Earlier slot | The controller offered to re-plan if the operator wants r1002 before the study day |

The operator said on 2026-11-12 that no host will be free soon and planning
continues. The lease is not started.

**Preparation that can be done before the lease**

- Install scripts for V1 and V2 that run from the Grml rescue system or from
  the unattended ISO, tested in the VM lab first (E1, scenario S1).
- The workload script, tested on a VM for correctness.
- The hand-back procedure read through once against the controller's config.
