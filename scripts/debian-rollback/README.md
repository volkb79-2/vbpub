# debian-rollback (working name)

Debian rollback is a safety layer for a normal Debian system managed with
`apt`: it snapshots OS state before package changes and lets an operator boot
an earlier state while keeping user and service data outside that rollback.
It is built from Debian's installer components and existing bootloaders; it is
not a separate Debian distribution or an image-based update system.

## Current status — 2026-11-22

- The btrfs/GRUB M2 unattended Debian Live installer is merged to `main`.
- Systemd-boot is the active PoC. Its target installer and `dr-state` backend
  are implemented in the current unmerged worktree. Earlier v1001 guests
  proved the builder, boot check, ESP entries, rollback/default selection,
  and one-shot consumption on a disk built from `b94af9533`. They do not
  certify the timestamped-ID, metadata, delete/prune, kernel-upgrade, or M2
  ISO-variant changes on the current candidate. Two earlier kernel-upgrade
  attempts timed out after a generated command ended in `; ;`; a later guest
  run against the `c749db1` source passed 21 checks. The integrated current
  candidate still needs a fresh guest run. Automatic failed-boot fallback is
  out of scope for this PoC.
- Fresh installs use state IDs with a six-digit creation sequence, UTC
  timestamp, and origin/reason slug, beginning with
  `@os-000001-YYYYMMDD-HHMMSSZ-install`. The sequence high-water mark lives
  outside rollback states; deletion never renumbers or reuses an ID. This
  change does not migrate historical `@os-0001` PoC disks. Manual snapshots
  accept `--reason` and an optional free-text `--note`; apt snapshots record
  the package plan. `dr-state list`, `list --long`, and `show STATE` provide
  compact and detailed state reports. Metadata is stored outside OS states
  and is removed when its state is deleted or pruned.
- M2 currently installs GRUB on the target disk. The builder produces two
  separate build-time Live ISO variants, one installing GRUB and one
  installing systemd-boot. The target-bootloader marker and installer
  enforcement are implemented; fresh UEFI guest validation of both variants
  is pending. Both Live media continue to boot the Debian Live environment
  through its GRUB menu.
- ZFS work is deferred until the systemd-boot PoC and its installer flow are
  complete.

The earlier systemd-boot guest runs used a disk built from `b94af9533` and do
not certify the current candidate. See the
[current work order](TODO.md) and [systemd-boot evidence](docs/SDBOOT-VARIANT-2026-11-18.md).

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
| [docs/RESEARCH-2026-11.md](docs/RESEARCH-2026-11.md) | November 2026 research sources and verification notes |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | Scenario definitions and historic filesystem comparison plan |

The dated results are evidence records for the builds they name. Use the
architecture and TODO documents for current design/status.
