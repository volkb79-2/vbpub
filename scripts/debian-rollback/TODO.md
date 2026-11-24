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

2026-11-24: `poc/dr-qemu.sh` retired in favour of `vm-lab start --plain-host` (vm-lab VML-BL-07); source at `a5802b837:scripts/debian-rollback/poc/dr-qemu.sh`

## ZFS

ZFS is paused for the core split (D28, DR-84, DR-96). PoC evidence is archived
outside git (it contains a password hash); index in the rollback controller
state. The install and runtime history remains in the
[ZFS PoC plan](docs/ZFS-POC-PLAN.md) and its dated reports.

2026-11-24: the last PoC candidate ISO (`findmnt-securehandoff`) installed in a TCG guest (3072 MiB, 4 vCPUs): `DR-ZFS-AUTOINSTALL-DONE rc=0` after 4,978 s, clean power-off. First-boot acceptance was not run: it tests the superseded state manager. The installed disk is kept read-only as the base for the ZFS guest acceptance.

## Work order

- Done:
  - Status documents (P-rollback-02).
  - vm-lab inspector (P-rollback-03).
  - Lint of the disk-wiping guards (P-rollback-04).
  - Core-split design (P-rollback-10, [DESIGN-CORE-SPLIT.md](docs/DESIGN-CORE-SPLIT.md)).
  - bootctl spike (P-rollback-11, [SPIKE-BOOTCTL-ORACLE.md](docs/SPIKE-BOOTCTL-ORACLE.md)).
  - ZFS plan rewrite (P-rollback-12, [ZFS-POC-PLAN.md](docs/ZFS-POC-PLAN.md)).
- In progress:
  - Stdlib Python core, first part (P-rollback-20a).
  - vm-lab start on a plain Docker host (P-rollback-30).
  - Backlog migration (P-rollback-05).
- Next:
  - Python core with the btrfs and GRUB backends (P-rollback-20b), then
    systemd-boot (P-rollback-21) and the apt hook and retention work
    (P-rollback-22).
  - Guest session in `vm-lab` (P-rollback-31).
- Later:
  - Loader-contract tier.
  - Release matrix on ISO-built disks.
  - ZFS rebuilt on the core.

## Open items

Open items are in [KNOWN_ISSUES_TODO_BACKLOG.md](KNOWN_ISSUES_TODO_BACKLOG.md) (DR-82 and up); closed ones are in [archive/](archive/).
