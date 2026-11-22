# debian-rollback (working name)

Debian rollback is a safety layer for a normal Debian system managed with
`apt`: it snapshots OS state before package changes and lets an operator boot
an earlier state while keeping user and service data outside that rollback.
It is built from Debian's installer components and existing bootloaders; it is
not a separate Debian distribution or an image-based update system.

## Current status — 2026-11-22

- The btrfs/GRUB M2 unattended Debian Live installer is merged to `main`.
- The systemd-boot target variant has now been installed from a fresh M2 Live
  ISO in a UEFI guest. Its ISO payload was built from `fe2a9afe4`; guest
  scenario tooling came from `f9f55e1aa`. Current-candidate evidence is
  recorded in [the 2026-11-22 results](docs/RESULTS-SDBOOT-M2-2026-11-22.md):
  boot-check 12 pass; sdboot-esp 16; s2-s3 23; one-shot consumption 8;
  dependency-break recovery 23; kernel-damage recovery 27; and dynamic-loader
  recovery 25.
- Each destructive recovery guest manually selected the older boot entry,
  preserved `/home` data written after the recovery snapshot, made the
  recovered state default, proved a plain reboot, and deleted the damaged
  state. Automatic failed-boot fallback remains outside this PoC.
- The routine Trixie/security repositories offered no kernel newer than the
  installed `6.12.117-1`, so that availability check stopped without changing
  the guest. With Trixie backports enabled, kernel-upgrade, booting the new
  kernel, one-shot rollback to the older kernel, and return to the new default
  passed 21 checks on `7.3.4+deb13-amd64`.
- Fresh installs use state IDs with a six-digit creation sequence, UTC
  timestamp, and origin/reason slug, beginning with
  `@os-000001-YYYYMMDD-HHMMSSZ-install`. The sequence high-water mark lives
  outside rollback states; deletion never renumbers or reuses an ID. This
  change does not migrate historical `@os-0001` PoC disks. Manual snapshots
  accept `--reason` and an optional free-text `--note`; apt snapshots record
  the package plan. `dr-state list`, `list --long`, and `show STATE` provide
  compact and detailed state reports. Metadata is stored outside OS states
  and is removed when its state is deleted or pruned.
- M2 has separate build-time Live ISO variants. The GRUB-target installer is
  merged to `main`; its fresh comparison install passed boot-check (9) and
  s2-s3 (21). The
  systemd-boot target variant has fresh install and rollback evidence. Both
  Live media continue to boot the Debian Live environment through GRUB; the
  ISO's fixed target-bootloader marker chooses the installed backend. The
  systemd-boot branch remains unmerged; its offline gate passed, and the
  exact-commit review remains before merge.
- The separate NoCloud builder route completed from current branch source:
  cloud-init installed a systemd-boot target disk, which booted on its own
  and passed `boot-check` (12) and `sdboot-esp` (16). Its seed is required by
  that automated builder route, but is optional for the M2 product install.
- ZFS is the next PoC after the systemd-boot branch passes its final gate and
  review and is merged.

Earlier guest runs used older source and remain historical evidence. See the
[current work order](TODO.md), [systemd-boot implementation note](docs/SDBOOT-VARIANT-2026-11-18.md),
and [fresh guest results](docs/RESULTS-SDBOOT-M2-2026-11-22.md).

For the exact current layers, media, and consumer/install flows, see the
[installation architecture](docs/INSTALLATION-ARCHITECTURE.md). It answers
which ISO is bootable, which media are optional, and how each path installs
the target system. The [recovery contract](docs/ROLLBACK-RECOVERY.md) defines
manual boot-menu recovery, state deletion and pruning, persistent-data limits,
and the destructive acceptance scenarios.

## Documents

| Document | Purpose |
|---|---|
| [docs/INSTALLATION-ARCHITECTURE.md](docs/INSTALLATION-ARCHITECTURE.md) | Canonical current architecture, ISO variants, Debian changes, and install flows |
| [docs/ROLLBACK-RECOVERY.md](docs/ROLLBACK-RECOVERY.md) | Recovery boundary, boot-menu operator flow, state management, and destructive acceptance |
| [TODO.md](TODO.md) | Current work order: finish systemd-boot, then defer to ZFS PoC |
| [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) | Product goals, design decisions, research questions, and design rationale; its original planning status is historical |
| [docs/RESULTS-M2-UNATTENDED-ISO-2026-11-18.md](docs/RESULTS-M2-UNATTENDED-ISO-2026-11-18.md) | Dated guest evidence and known limits for the M2 GRUB-target ISO |
| [docs/SDBOOT-VARIANT-2026-11-18.md](docs/SDBOOT-VARIANT-2026-11-18.md) | systemd-boot backend details, guest finding, and offline coverage |
| [docs/RESULTS-SDBOOT-M2-2026-11-22.md](docs/RESULTS-SDBOOT-M2-2026-11-22.md) | Fresh systemd-boot M2 ISO install, rollback and recovery evidence |
| [docs/RESEARCH-2026-11.md](docs/RESEARCH-2026-11.md) | November 2026 research sources and verification notes |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | Scenario definitions and historic filesystem comparison plan |

The dated results are evidence records for the builds they name. Use the
architecture and TODO documents for current design/status.
