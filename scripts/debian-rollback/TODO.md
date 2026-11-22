# Current work order — 2026-11-22

Finish the systemd-boot target path first, integrate it into the M2 Live ISO as
a separate build-time variant, then continue with the ZFS PoC. The layers and
media distinction are documented in the
[installation architecture](docs/INSTALLATION-ARCHITECTURE.md).

## 1. Finish systemd-boot PoC

- Earlier v1001 runs proved the builder, boot check, ESP entries, rollback,
  default selection, and one-shot consumption on a disk built from
  `b94af9533`. They are historical evidence, not certification of the current
  candidate. The current guest matrix must be rebuilt from committed source
  and rerun. Two kernel-upgrade guest attempts timed out on a generated
  command ending in `; ;`; a later guest run against the `c749db1` source
  passed 21 checks. Rerun it against the integrated current candidate.
- Guest transcripts also record apt metadata/rollback and recovery after
  kernel and dynamic-loader damage. Their injected source hashes are not
  available in Git, so treat those results as historical until repeated on
  the exact candidate. The acceptance contract must cover a real dpkg
  dependency break from a harmful manual package operation. Every recovery
  run must select the older entry manually, verify post-snapshot persistent
  data, make the recovered state the default, and delete the failed state.
  Automatic failed-boot fallback is not part of this PoC.
- The current `origin/main` M2 changes are integrated in this worktree. Finish
  the review fixes, rerun the registered offline lane, then rebuild the seed
  and both M2 ISO variants from the exact candidate.
- New state IDs use a monotonic six-digit sequence, UTC timestamp, and
  origin/reason slug (install, apt, or manual reason). The high-water counter
  is outside all OS states; deletion does not reuse numbers, and a missing or
  corrupt counter refuses new snapshots. Fresh installs initialize
  `000001-install`; historical `@os-0001` PoC disks are not migrated and no
  adoption command is planned. Keep those disks on their existing runtime; do
  not copy the new runtime onto them, because its APT snapshot hook correctly
  refuses to make package changes without the required counter. This schema
  applies to fresh installs and snapshots on both bootloaders.
- Use a fresh installed systemd-boot target for the current-source matrix:
  builder, boot check, canonical ESP ownership, apt snapshot/rollback,
  one-shot consumption, kernel upgrade, package-inconsistency recovery, and
  destructive recovery after kernel/init/userspace damage. Then boot both M2
  target ISO variants in clean UEFI guests and verify disk layout, first boot,
  rollback, and boot-menu behavior.

## 2. Validate both M2 target ISO variants

- `iso/build-iso.sh --bootloader grub|systemd-boot` creates separate
  artifacts and stores the fixed choice in `/dr/target-bootloader`.
- `dr-autoinstall` validates that marker and passes it to `install-btrfs.sh`;
  install configuration and optional config media cannot override it.
- Keep both Live environments booting through Debian Live's GRUB path; only
  the target disk's bootloader changes.
- Build both artifacts from the verified Debian Live base and test both in
  clean UEFI guests on r1002 or v1001: disk layout, first boot, boot-menu
  behavior, and rollback. Preserve the existing GRUB-target behavior, and
  document hashes, source revisions, scenarios, and limits.
- Run an independent DeepSeek review against the exact candidate commit after
  gates and guest evidence are complete. Resolve findings and obtain a clean
  final review before merging the feature into `main`.

## 3. ZFS PoC — deferred

Start the ZFS work only after systemd-boot and the M2 target-bootloader
variants are proven. Use the existing experiment definitions as a starting
point, update their old controller and host-scheduling assumptions, then
evaluate the chosen ZFS boot environment in disposable guests. The old
comparison plan is not itself evidence or current authorization.

## Host and repository constraints

- r1002 and v1001 are available for VMs without another approval request.
- Keep using this existing worktree. Do not invoke the unavailable Claude
  controller or its subcontrollers.
- Keep guest disks and run data out of Git. Resolve shared ISO and VM paths
  through `cowtree`; preserve existing host evidence and artifacts.
- Run project tests only through the registered `run-gate` lane. The cockpit
  is for inspection and orchestration.
