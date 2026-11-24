# debian-rollback (working name)

Debian rollback is a safety layer for a normal Debian system managed with
`apt`: it snapshots OS state before package changes and lets an operator boot
an earlier state while keeping user and service data outside that rollback.
It is built from Debian's installer components and existing bootloaders; it is
not a separate Debian distribution or an image-based update system.

## Status

[TODO.md](TODO.md) is the only current status page and work order. This
README describes the product and indexes its durable documents. Dated
RESULTS files are the evidence records for the builds and guests they name;
they are not current status.

## Documents

| Document | Purpose |
|---|---|
| [TODO.md](TODO.md) | Single current status page and work order |
| [KNOWN_ISSUES_TODO_BACKLOG.md](KNOWN_ISSUES_TODO_BACKLOG.md) | Open items (DR-NN); closed items are in [archive/](archive/) |
| [docs/INSTALLATION-ARCHITECTURE.md](docs/INSTALLATION-ARCHITECTURE.md) | Canonical architecture, ISO variants, Debian changes, and install flows |
| [docs/ROLLBACK-RECOVERY.md](docs/ROLLBACK-RECOVERY.md) | Recovery contract, boot-menu operator flow, state management, and acceptance scenarios |
| [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) | Product goals, decisions, and design rationale |
| [docs/WORK-PACKAGES.md](docs/WORK-PACKAGES.md) | Historical package plan and decision resolutions |
| [docs/SDBOOT-VARIANT-2026-11-18.md](docs/SDBOOT-VARIANT-2026-11-18.md) | systemd-boot implementation details and the `.conf` finding |
| [docs/ZFS-POC-PLAN.md](docs/ZFS-POC-PLAN.md) | Historical ZFS PoC plan, with superseded sections marked |
| [docs/RESEARCH-2026-11.md](docs/RESEARCH-2026-11.md) | November 2026 research sources and verification notes |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | Historical scenario definitions and filesystem comparison plan |
| [docs/RESULTS-M2-UNATTENDED-ISO-2026-11-18.md](docs/RESULTS-M2-UNATTENDED-ISO-2026-11-18.md) | GRUB-target M2 ISO evidence |
| [docs/RESULTS-SDBOOT-M2-2026-11-22.md](docs/RESULTS-SDBOOT-M2-2026-11-22.md) | systemd-boot M2 Live ISO evidence |
| [docs/RESULTS-SDBOOT-NOCLOUD-2026-11-23.md](docs/RESULTS-SDBOOT-NOCLOUD-2026-11-23.md) | NoCloud builder install and systemd-boot guest evidence |
| [docs/RESULTS-ZFSBOOTMENU-POC-2026-11-23.md](docs/RESULTS-ZFSBOOTMENU-POC-2026-11-23.md) | ZFSBootMenu installer evidence, 2026-11-23 |
| [docs/RESULTS-ZFSBOOTMENU-POC-2026-11-24.md](docs/RESULTS-ZFSBOOTMENU-POC-2026-11-24.md) | ZFSBootMenu installer evidence and final-install outcome, 2026-11-24 |
