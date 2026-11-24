# Spike: real `bootctl` as an offline loader-contract oracle (2026-11-24)

Question (review finding H3a, DR-87): can systemd's own boot-spec parser judge what `dr-state gen-menu` writes, offline, without EFI variables? In particular, does it resolve the `loader.conf` default, and does it tell a correct default from the `default os-X` bug (no `.conf`)?

**Answer: yes.** Debian 13's `bootctl` resolves the default without EFI variables and without `/sys/firmware/efi`. It reports a different default for case (a) and case (b), so an offline test kills a mutant that writes `default os-X`.

## Setup
- Debian 13 (trixie) container, systemd `257.13-1~deb13u1`. No network, no `/sys/firmware/efi`, capped at 2 CPUs, 2 GiB and 512 pids.
- `bootctl` comes from the package **`systemd-boot-tools`** (`dpkg -S /usr/bin/bootctl`), not from `systemd`.
- Fixture: three states `@os-000001-…-install`, `@os-000002-…-apt` and `@os-000003-…-apt`, written by main's real `poc/dr-state gen-menu --default @os-000002-20261118-120100Z-apt` in test mode (the `workflow_fixture` shape of `test-dr-state-sdboot.sh`). Cases (b)-(d) copy that tree and edit only the `default` line.
- Command for each case:
  `SYSTEMD_RELAX_ESP_CHECKS=1 SYSTEMD_RELAX_XBOOTLDR_CHECKS=1 bootctl --esp-path=<esp> --boot-path=<esp> --no-variables --no-pager list --json=short`
  The JSON gives `id` and `isDefault` for every entry.

## Results
| Case | `loader.conf` `default` | Entry bootctl marks `isDefault` |
|---|---|---|
| (a) gen-menu output | `os-000002-20261118-120100Z-apt.conf` | `os-000002-…-apt.conf` (correct) |
| (b) id without `.conf` | `os-000002-20261118-120100Z-apt` | `os-000001-…-install.conf`: the value matches nothing, and bootctl falls back to the first entry |
| (c) glob, one match | `os-000001-*` | `os-000001-…-install.conf` |
| (d) glob, two matches | `os-00000*-apt.conf` | `os-000002-…-apt.conf` (the first match in list order) |

All four runs exit 0.

## Conditions the test must meet
1. **The ESP path must be the root of a filesystem.** On a plain directory, bootctl refuses with `Directory "<esp>" is not the root of the file system.` A tmpfs mount per fixture works. In a lane container without mount rights, the fixture needs a tmpfs mount declared by the lane (or a FAT image with mtools plus `--image`, not measured).
2. **`SYSTEMD_RELAX_ESP_CHECKS=1` is required.** Without it, bootctl refuses with `File system "<esp>" is not a FAT EFI System Partition (ESP) file system.`
3. `--no-variables` alone is enough to skip EFI variables. No `/sys/firmware/efi` is needed.

## Needed image change
The shared tester-unified image installs `systemd` but not `systemd-boot-tools`. The offline lane needs `systemd-boot-tools` (Debian 13 version), plus a tmpfs mount point for the fixture ESP. The image is owned by the estate core libraries topic, so the change goes through it (P-rollback-24).

## Consequence
DR-87's cheap tier is real. P-rollback-24 adds an offline test that runs `gen-menu` in test mode, then real `bootctl … list --json=short`, and asserts that `isDefault` names the intended entry. The boot tier (VML-BL-09, P-rollback-32) remains for one-shot and firmware ordering, which bootctl `--no-variables` cannot show.
