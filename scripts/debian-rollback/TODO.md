# debian-rollback status

This is the project's only status page. Architecture and operating details are
in [INSTALLATION-ARCHITECTURE.md](docs/INSTALLATION-ARCHITECTURE.md),
[ROLLBACK-RECOVERY.md](docs/ROLLBACK-RECOVERY.md), and
[DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md). Decisions are recorded in the design
guide; work packages are tracked below.

## Done

- **systemd-boot target variant:** merged in `9bd4e5dcd`; evidence is in the
  [M2 results](docs/RESULTS-SDBOOT-M2-2026-11-22.md) and the
  [NoCloud builder results](docs/RESULTS-SDBOOT-NOCLOUD-2026-11-23.md).
- **M2 target ISO variants:** the GRUB-target and systemd-boot-target variants
  are done; evidence is in the
  [GRUB-target results](docs/RESULTS-M2-UNATTENDED-ISO-2026-11-18.md) and the
  [systemd-boot results](docs/RESULTS-SDBOOT-M2-2026-11-22.md).

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

- **DR-82** F11: stdlib Python, runtime outside states, format versions — OPEN — P-rollback-10 — review H2/M2
- **DR-83** one state-ID grammar; foreign `@os-*` unmanaged; legacy/.conf code removed — OPEN — P-rollback-20 — review H1
- **DR-84** core/backends split (storage btrfs|zfs, boot grub|sdboot|zbm) before ZFS — OPEN — P-rollback-20/21 — review H2, L1, L2
- **DR-85** snapshot never fails on menu capacity; degraded menu; capacity-aware retention; ESP-exhaustion guest scenario — OPEN — P-rollback-22/40 — review H5, F13
- **DR-86** bootloader from ESP marker only — OPEN — P-rollback-20 — review M1
- **DR-87** loader-contract tier: bootctl offline + vm-lab ESP boot — OPEN — P-rollback-24/32 — review H3
- **DR-88** `check` + `--json`; dr-serial observable-only; mirror parsers removed — OPEN — P-rollback-24/26 — review H4, L4
- **DR-89** one APT hook — OPEN — P-rollback-22 — review M3
- **DR-90** metadata `kind` + `pinned` — OPEN — P-rollback-22 — review M4
- **DR-91** kernel hash cache — OPEN — P-rollback-23 — review M5
- **DR-92** counter floor from names + sidecars — OPEN — P-rollback-22 — review M6
- **DR-93** boot-counting suffix rule defined — OPEN — P-rollback-10 — review M8
- **DR-94** payload as files; hot-patch dev-only; snapper removed — OPEN — P-rollback-25 — review M10, L6
- **DR-95** matrix runner committed; dr-qemu.sh retired; evidence archived — OPEN — P-rollback-30/33 — review H6
- **DR-96** ZFS snapshot checkpoints + clone/promote; name = ID; no send/receive — OPEN — P-rollback-12/42 — review M7
- **DR-97** all disk-wiping guards linted; release matrix on ISO-built disk — OPEN — P-rollback-04/40 — review L3, L5
- **DR-98** kernel-install plugin instead of dpkg-divert — OPEN — P-rollback-41 — review plans-review
- Parked (no ID): E2; phase 2 fallback; Secure Boot on sdboot; D25 RAM experiment; restore-image ISO + licence audit; ZFS restricted-GRUB comparison.
