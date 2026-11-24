# ZFSBootMenu PoC guest results — 2026-11-24

Host names replaced by 'the test host' and host paths made relative to the job dir (2026-11-24); VM and run-dir names are verbatim evidence identifiers.

Continuation of the [2026-11-23 progress report](RESULTS-ZFSBOOTMENU-POC-2026-11-23.md).
This report distinguishes the earlier completed install from the current
regression-fixed install, which is still running.

## Candidate and registered gates

The current candidate is
debian-rollback-zbm-v3.1.0-findmnt-securehandoff-20261124.iso (2,103,967,744
bytes, SHA-256
4752a79c441a312bab374ecdcc98d91ff890791ad269bbb603caecf2336bde51). The
registered build-zfs-iso lane passed on the current source; log:
  /tmp/run-gate/lanes/build-zfs-iso/29e96526d1e5219b681829d1a9c024f8.log
The candidate checksum matched on the test host. Because the embedded installer
configuration contains a password hash, the config file and config-bearing
ZFS ISOs are owner-only (mode 0600).

The registered offline lane passed after the root-dataset and credential-file
changes; log:
  /tmp/run-gate/lanes/offline/6d7b869caf877f0cfd97e56406e87d6a.log
This included the 22 ZFS state-manager unit tests and the serial-harness tests.
These gate runs used the dirty worktree; final clean-commit reruns remain
pending. The earlier registered vm-lab r0-r1 pass (466 tests, 75.79%
coverage) is recorded in the prior report and will be repeated for final
verification.

The verified ZFSBootMenu EFI input is pinned in the 2026-11-23 report. Its
source was staged in the active worktree's resolved downloads path so the
tester-unified build could read it through the worktree mount.

## Earlier install and first-boot finding

The earlier config-bearing candidate
debian-rollback-zbm-v3.1.0-altroot-normalize-config-20261124.iso completed
installation on the test host with installer marker DR-ZFS-AUTOINSTALL-DONE rc=0.
Its live ZFS preflight and target ZFS DKMS build succeeded, and the installed
disk was preserved as a read-only image.

The first-boot recovery acceptance reached its initial checks but stopped
before the package-damage fixture. It confirmed the rollback metadata dataset,
separate /home and /srv ZFS datasets, no swap, the state-manager digest,
and the pinned EFI copies. State listing and running-root checks failed
because dr-zfs-state assumed ZFSBootMenu put root=ZFS=... in
/proc/cmdline; this guest does not. The root source is now derived from
findmnt -n -o SOURCE --target /, with tests for the mounted source,
non-boot-environment sources, and ambiguous output. The serial harness uses
the same source for its ZFS root checks. The failed attempt did not create the
package-damage fixture or mutate the installed guest.

The earlier install's shutdown emitted a warning while trying to unmount the
read-only Live ISO, then powered off. This was not an installer failure.

## Current clean install

- Host: the test host, authenticated using the operator-pinned ED25519 host key.
- VM: debrollback-zbm-findmnt-secure-install-20261124.
- Run directory (relative to the job dir):
  `vm-lab/zbm-20261123/runs/install-findmnt-securehandoff-20261124/`.
- Blank qcow2 base, read-only candidate ISO, fresh UEFI variables, 3072 MiB
  guest RAM, four TCG vCPUs. The test host has no /dev/kvm.
- The 3072 MiB value is the current slow-TCG install-run setting, not evidence
  that ZFS requires 3 GiB. The installed-system boot and recovery acceptance
  is planned at 2048 MiB. A separate 1–2 GiB experiment comparing no swap,
  ZRAM, and Zswap with a dedicated swap partition is deferred; see D25 and the
  later-experiments section of `ZFS-POC-PLAN.md`.
- The embedded config was accepted and selected /dev/vda. The guest reached
  the live ZFS preflight. At 03:14 UTC it was still installing the matching
  Live-kernel headers and ZFS dependencies; target confirmation had not yet
  occurred and the target disk overlay remained at its blank-base size.
- This phase is expected to be long under TCG: the previous successful guest
  spent about 32 minutes in the same live preflight before target confirmation.
  The current VM was running with OOMKilled=false; the test host memory PSI avg10
  remained well below the host stop threshold.
- At 03:28 UTC, about 2,000.5 seconds after the autoinstaller started, it
  emitted TARGET-CONFIRMED and INSTALL for /dev/vda. The target installer has
  begun; the overlay had grown to 11,206,656 bytes. Target kernel/DKMS,
  initramfs, state initialization, and final success remain unproven. The
  earlier successful candidate spent about 48 minutes in target installation
  after its target-confirmation marker.
- At about 03:39 UTC, debootstrap completed at guest uptime 2,646.4 seconds,
  10.8 minutes after target confirmation. The overlay was 554,303,488 bytes.
  The target package, kernel, ZFS DKMS, and initramfs phase remains in progress.
- At 03:56:56 UTC, the installer was still running without a completion or
  failure marker. The disk overlay had grown to 1,530,658,816 bytes; the guest
  was not OOM-killed and the test host memory PSI remained at 0.00 in the monitor
  sample. The prior successful candidate took about 48 minutes after target
  confirmation, so this run remains within that observed TCG interval.
- The installer now hands the configured password through a private temporary
  file, not a process argument. The generated-password marker is value-free.
  The config file and config-bearing ISO copies are mode 0600. A safe scan of
  seven saved serial logs found zero generated-password markers, password
  argv values, or crypt-hash tokens; the current serial log also had zero of
  each at the latest check.

## Next proof points

Superseded: the state manager these steps test is replaced by D28; see TODO.md.

1. Wait for the Live ZFS preflight, target installation, target DKMS/initramfs,
   BE registration, and DR-ZFS-AUTOINSTALL-DONE rc=0 or an explicit failure.
2. Preserve the serial log and runner metadata after repeating the safe secret
   scan. Flatten the stopped installed disk into a read-only base and boot a
   new overlay without the installer ISO or reused UEFI variables. Use the
   planned 2048 MiB baseline for this installed-system boot and recovery run.
3. Run the registered zfs-recovery-acceptance lane. It must break a real
   dpkg dependency, let the operator choose the pre-change BE in ZFSBootMenu,
   confirm restored package/service health and /home plus /srv data, set the
   recovered BE as the default, verify a plain reboot, and delete the
   inactive failed BE without resetting the monotonic state sequence.
4. Update the architecture and ZFS recovery-boundary documentation only with
   behavior demonstrated by the guest. Then run final registered gates on the
   committed head and request the authorized DeepSeek ECO review of that exact
   head before merging.

## Outcome of the final install

2026-11-24: the last PoC candidate ISO (`findmnt-securehandoff`) installed in a TCG guest (3072 MiB, 4 vCPUs): `DR-ZFS-AUTOINSTALL-DONE rc=0` after 4,978 s, clean power-off. First-boot acceptance was not run: it tests the superseded state manager. The installed disk is kept read-only as the base for the ZFS guest acceptance.
