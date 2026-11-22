# Current work order — 2026-11-22

Complete the systemd-boot target path first, then continue with the ZFS PoC.
The separate M2 Live ISO variant and guest evidence are complete; the offline
gate passed, and the exact-commit review remains before merge. The layers and
media distinction are documented in the
[installation architecture](docs/INSTALLATION-ARCHITECTURE.md).

## 1. Finish systemd-boot PoC

- The current systemd-boot M2 ISO guest matrix passed on v1001. ISO payload
  source: `fe2a9afe4`; guest driver: `f9f55e1aa`. Boot-check, ESP, apt
  rollback, one-shot consumption, dependency-break recovery, kernel-damage
  recovery, dynamic-loader recovery, and backports kernel-upgrade results are
  in [the 2026-11-22 report](docs/RESULTS-SDBOOT-M2-2026-11-22.md). Each
  recovery selected an older boot-menu entry after the default became unusable,
  preserved post-snapshot persistent data, set the recovered state default,
  proved a plain reboot, and deleted the damaged state.
- The routine Trixie/security repository had no newer kernel than the installed
  `6.12.117-1`; the scenario stopped before changing the guest. With Trixie
  backports, the upgrade and both new/old kernel boot paths passed 21 checks.
- The fresh GRUB-target M2 comparison passed first-boot checks (9) and
  apt-rollback/default-selection checks (21). The separate NoCloud
  systemd-boot builder also completed from current source; its installed disk
  booted without the seed or cloud image and passed boot-check (12) and
  sdboot-esp (16). This builder path is not required by the M2 product install.
- Run the registered offline lane on the final documented candidate and obtain
  an independent DeepSeek review of that exact commit before merging. Use only
  the approved `deepseek-v4.1-flash-eco` route.
- Automatic failed-boot fallback is not part of this PoC.
- New state IDs use a monotonic six-digit sequence, UTC timestamp, and
  origin/reason slug (install, apt, or manual reason). The high-water counter
  is outside all OS states; deletion does not reuse numbers, and a missing or
  corrupt counter refuses new snapshots. Fresh installs initialize
  `000001-install`; historical `@os-0001` PoC disks are not migrated and no
  adoption command is planned. Keep those disks on their existing runtime; do
  not copy the new runtime onto them, because its APT snapshot hook correctly
  refuses to make package changes without the required counter. This schema
  applies to fresh installs and snapshots on both bootloaders.
- The systemd-boot M2 ISO has fresh install, first boot, rollback and
  destructive-recovery evidence. The fresh GRUB-target comparison also passed
  install, first boot and rollback/default-selection checks. Merge only after
  the final gate and exact-commit review.

## 2. Validate both M2 target ISO variants

- `iso/build-iso.sh --bootloader grub|systemd-boot` creates separate
  artifacts and stores the fixed choice in `/dr/target-bootloader`.
- `dr-autoinstall` validates that marker and passes it to `install-btrfs.sh`;
  install configuration and optional config media cannot override it.
- Keep both Live environments booting through Debian Live's GRUB path; only
  the target disk's bootloader changes.
- Both artifacts were built from the verified Debian Live base. The
  systemd-boot target passed clean UEFI install and rollback scenarios; the
  GRUB-target comparison passed disk layout, first boot, boot-menu and
  rollback/default-selection checks. Results and hashes are in the dated
  report.

## 3. ZFS PoC — deferred

Start the ZFS PoC after the systemd-boot branch passes its final gate and
review and is merged. Use the existing experiment definitions as a starting
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
