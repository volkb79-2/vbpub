# debian-rollback status

This is the project's only status page. Architecture and operating details are
in [INSTALLATION-ARCHITECTURE.md](docs/INSTALLATION-ARCHITECTURE.md),
[ROLLBACK-RECOVERY.md](docs/ROLLBACK-RECOVERY.md), and
[DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md). Decisions are recorded in the design
guide; work packages are tracked below.

## Done

- **systemd-boot target variant:** merged in `9bd4e5dcd`; evidence is in the
  [M2 results](docs/RESULTS-SDBOOT-M2-2026-11-22.md) and [NoCloud builder results](docs/RESULTS-SDBOOT-NOCLOUD-2026-11-23.md).
- **M2 target ISO variants:** GRUB-target and systemd-boot-target are done;
  both are covered by the [M2 results](docs/RESULTS-SDBOOT-M2-2026-11-22.md).

## ZFS

ZFS is paused for the core split (D28, DR-84, DR-96). PoC evidence is archived
outside git (it contains a password hash); index in the rollback controller
state. The install and runtime history remains in the
[ZFS PoC plan](docs/ZFS-POC-PLAN.md) and its dated reports.

2026-11-24: the last PoC candidate ISO (`findmnt-securehandoff`) installed in a TCG guest (3072 MiB, 4 vCPUs): `DR-ZFS-AUTOINSTALL-DONE rc=0` after 4,978 s, clean power-off. First-boot acceptance was not run: it tests the superseded state manager. The installed disk is kept read-only as the base for the ZFS guest acceptance.

## Work order

- Wave 0: finish these status documents and lint every disk-wiping guard.
- Wave 1: write the core-split design.
- Wave 2: build the stdlib Python core with btrfs and GRUB, then the
  systemd-boot, apt, and retention work.
- Wave 2 tooling: build the loader-contract tier and move guest tooling into
  `vm-lab`.
- Wave 3: run the release matrix on ISO-built disks, then rebuild ZFS on the
  core.

## Open items

Open items are in [KNOWN_ISSUES_TODO_BACKLOG.md](KNOWN_ISSUES_TODO_BACKLOG.md) (DR-82 and up); closed ones are in [archive/](archive/).
