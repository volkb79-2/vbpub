# Current work order — 2026-11-21

Finish the systemd-boot target path first, integrate it into the M2 Live ISO as
a separate build-time variant, then continue with the ZFS PoC. The layers and
media distinction are documented in the
[installation architecture](docs/INSTALLATION-ARCHITECTURE.md).

## 1. Finish systemd-boot PoC

- A fresh-source v1001 kernel-upgrade guest passed 21 checks after an earlier
  run exposed the systemd-boot package post-install script's direct
  `kernel-install add` call. The installer diverts Debian's
  `zz-systemd-boot` hooks and masks systemd's stock `90-loaderentry.install`
  writer. The passing guest found only canonical per-state entries, booted
  both the new kernel and the older snapshot kernel with its modules, and
  returned to the new default after a one-shot. See the exact ISO/base hashes
  and transcript in [SDBOOT-VARIANT-2026-11-18.md](docs/SDBOOT-VARIANT-2026-11-18.md).
- Current-source v1001 acceptance has passed for apt metadata and rollback,
  kernel/initrd damage, and dynamic-loader damage that prevents init from
  starting. Each recovery run selected the older entry manually, verified the
  post-snapshot persistent-data witness, made the recovered state the default,
  and deleted the failed state. The manual runs also verified timestamped
  state IDs and snapshot notes/reports. Evidence and retained transcripts are
  in [ROLLBACK-RECOVERY.md](docs/ROLLBACK-RECOVERY.md). Continue to protect
  the running/default/pending-one-shot states in `delete` and `prune`.
  Automatic failed-boot fallback is not part of this PoC.
- Integrate the current `origin/main` M2 changes into this worktree, resolve
  conflicts, and rerun the registered offline lane after the state-ID work.
- New state IDs use a monotonic six-digit sequence, UTC timestamp, and
  origin/reason slug (install, apt, or manual reason). The high-water counter
  is outside all OS states; deletion does not reuse numbers, and a missing or
  corrupt counter refuses new snapshots. Fresh installs initialize
  `000001-install`; historical `@os-0001` PoC disks are not migrated. This
  design applies to both GRUB and systemd-boot state names per the operator's
  naming decision.
- Rebuild the NoCloud seed and fresh installed target after integration, then
  repeat the systemd-boot guest matrix against the exact integrated source:
  builder, boot check, ESP entry ownership, apt snapshot and rollback,
  boot-once consumption, kernel upgrade, and destructive recovery after kernel
  and userspace damage. Evidence already on file uses the older state-name
  schema and source based on `c749db1`; retain it as historical evidence, not
  certification of the new schema.

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
