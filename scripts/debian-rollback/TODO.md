# Current work order — 2026-11-23

Complete the systemd-boot target path first, then continue with the ZFS PoC.
The separate M2 Live ISO variants and guest evidence are complete. The latest
NoCloud builder matrix also passed; run the offline gate on this final
documented candidate, then obtain an exact-commit DeepSeek ECO review before
merge. The layers and media distinction are documented in the
[installation architecture](docs/INSTALLATION-ARCHITECTURE.md).

## 1. Finish systemd-boot PoC

- The systemd-boot M2 Live ISO guest matrix remains recorded against its named
  artifact in [the 2026-11-22 report](docs/RESULTS-SDBOOT-M2-2026-11-22.md).
  A separate current-source NoCloud builder run passed nine fresh-overlay
  scenarios: boot-check (12), ESP (16), apt rollback (23), one-shot (9),
  dependency recovery (23), kernel recovery (27), loader recovery (25), stable
  kernel update (21), and backports update (21). The full report is
  [here](docs/RESULTS-SDBOOT-NOCLOUD-2026-11-23.md).
- The stable run upgraded from `6.12.117` to `6.12.118`; the backports run
  upgraded from `6.12.117` to `7.3.4`. Both old and new kernel states booted
  with matching modules, and one-shot rollback returned to the new default.
  Each destructive recovery selected the recovery entry, preserved data
  written after its snapshot, set the recovered state as default, proved a
  plain reboot, and deleted the failed state.
- The fresh GRUB-target M2 comparison passed first-boot checks (9) and
  apt-rollback/default-selection checks (21). The separate NoCloud
  systemd-boot builder matrix is recorded in the linked report. The installed
  disk booted without its seed or cloud image; this builder path is not
  required by the M2 product install.
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
