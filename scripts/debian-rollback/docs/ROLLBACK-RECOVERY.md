# Rollback recovery and state management

**Scope:** the recovery contract for the btrfs/systemd-boot target. This page
defines the operator flow, state-management behavior, data boundary, and
acceptance scenarios. Current status and the work order are in
[TODO.md](../TODO.md); hashes, counts, and logs stay in the dated RESULTS
reports.

## Recovery contract

For this PoC, recovery means the operator can open the systemd-boot menu and
choose a known-good OS state captured before the damaging change. Automatic
fallback after a failed boot is not implemented or required by this acceptance.

The operator selects the recovery snapshot entry before the damaged default
boots. If a broken kernel or userspace has already failed, reset or power-cycle
the guest, then select that snapshot at the next boot. The systemd-boot menu
can be opened during its configured timeout; when the timeout is hidden or
zero, systemd documents holding Space before the boot manager starts. The menu
uses the arrow keys to choose an entry and Enter to boot it. See the
[systemd-boot key bindings and menu behavior](https://www.freedesktop.org/software/systemd/man/latest/systemd-boot.html).

After reaching the known-good state, make it the permanent default, verify a
plain reboot, and remove the failed state if desired:

```sh
dr-state list
dr-state show @os-000003-<YYYYMMDD>-<HHMMSS>Z-manual-before-upgrade
dr-state set-default @os-000003-<YYYYMMDD>-<HHMMSS>Z-manual-before-upgrade
reboot
dr-state delete @os-000004-<YYYYMMDD>-<HHMMSS>Z-apt --dry-run
dr-state delete @os-000004-<YYYYMMDD>-<HHMMSS>Z-apt --yes
```

Use the actual names printed by `dr-state list`. New installs and snapshots on
both bootloaders use `@os-<six-digit-sequence>-<YYYYMMDD>-<HHMMSS>Z-<slug>`.
For example, the first install is
`@os-000001-20261121-120000Z-install`, an APT snapshot is
`@os-000002-20261121-121500Z-apt`, and a manual snapshot can be
`@os-000003-20261121-123000Z-manual-before-upgrade`. The sequence provides
stable creation ordering; timestamps remain readable, and the slug shows the
origin or short reason. Deleted sequence numbers are not reused. The initial
install also records its OS, kernel, and package metadata.

The high-water counter is `/.top/.dr-state/last-sequence`. Keep it with the
top-level Btrfs filesystem; it is outside rollback states and is required to
allocate future IDs safely. If it is missing or malformed, `snapshot` stops
instead of guessing or reusing a deleted ID. The new format applies to fresh
installs and their snapshots. Existing `@os-0001` PoC disks are left as they
are; this change does not migrate them.

`boot-once STATE` remains useful when the running system is healthy and an
operator wants one test boot; it does not provide recovery from an already
unusable running system.

## Managing OS states

| Command | Purpose | Safety behavior |
|---|---|---|
| `dr-state list` | Show OS states, running/default labels, boot kernel, and snapshot summary | Read-only |
| `dr-state list --long` | Show the detailed report for every state | Read-only |
| `dr-state show STATE` | Report one state's origin, note, OS/kernel/package facts, and APT actions | Read-only |
| `dr-state set-default STATE` | Make a known-good state the next and permanent default | Rebuilds the boot menu and refuses a state without a bootable kernel |
| `dr-state boot-once STATE` | Select a state for one boot from a healthy running system | Writes the bootloader's one-shot selector |
| `dr-state delete STATE --dry-run` | Inspect one exact deletion target | Does not change the menu or Btrfs state |
| `dr-state delete STATE --yes` | Remove one exact inactive `@os-*` state | Refuses the running, configured-default, and pending one-shot states; validates selectors and publishes a menu without the target before deleting its Btrfs subvolume |
| `dr-state prune --keep N --dry-run` | Preview retention cleanup | Reports excess timestamped snapshot states without deleting |
| `dr-state prune --keep N` | Keep the newest N snapshots | Uses the sequence, protects the running, configured-default, and pending one-shot states, leaves the `install` state alone, and removes every doomed state from the boot menu before deleting any subvolume |

Deletion requires one exact state name and `--yes`; it is never a wildcard
operation. The menu must still have at least one bootable `dr-state` entry
after the target is excluded. An unreadable or malformed loader configuration,
unknown selector, uninspectable one-shot, or failed menu write stops deletion.
The running and selected default must be changed first when they refer to the
failed environment. If the subvolume deletion fails after the menu was
published, `dr-state` attempts to restore the entry from the surviving state;
the subvolume remains available for diagnosis.

Deleting or pruning an OS state removes only its top-level `@os-*` subvolume
and boot files that no remaining BLS entry references. It does not remove the
separate data subvolumes such as `/home`, `/srv`, `/var/lib/postgresql`, or
`/usr/local`.

## Snapshot notes and inventory

Create a manual snapshot before the change and include a short reason plus an
optional free-text note:

```sh
dr-state snapshot --reason before-upgrade \
  --note "Before testing the new graphics stack; current display works"
dr-state list
dr-state show @os-000003-<YYYYMMDD>-<HHMMSS>Z-manual-before-upgrade
```

The reason defaults to `manual`; the normalized reason becomes the readable
slug after `manual-`, while the full reason remains in the report. Use `--note`
for descriptive text, including spaces or multiple lines. The apt hook creates
snapshots with slug `apt` before dpkg changes files and records the planned
package operations, such as `install hello - -> 2.10-5` or an
upgrade/downgrade. Those automatic snapshots do not have an operator note.

For a new snapshot, `show` and `list` report its UTC creation time, source,
reason, parent state, operator note, OS name/version, running kernel, and
selected core package versions (`apt`, `base-files`, `dpkg`, `libc6`, systemd,
and Linux image packages). APT-created snapshots also show the package actions
from the pre-install plan. If package inventory cannot be read, the report
marks it unavailable; it does not invent package versions. Older snapshots
created before metadata capture remain bootable and are reported as having no
recorded metadata.

The report is stored as a mode-0600 sidecar under
`/.top/.dr-state/metadata/`, outside each OS subvolume. Rolling back one OS
state therefore does not roll back the inventory. The sidecar helps an
operator identify a state; it is not needed to boot or recover it. Deleting or
pruning a state also removes its sidecar after the Btrfs subvolume is removed.

## What recovery and future snapshots depend on

The older OS state is bootable only while each link in this chain remains
usable:

1. **UEFI boot path and ESP:** firmware can start systemd-boot from the EFI
   System Partition. Keep the ESP, systemd-boot EFI binary, loader configuration,
   and the selected BLS entry intact.
2. **Recovery kernel pair:** keep the selected entry's copied kernel and initrd
   in `/boot/efi/EFI/dr/kernels/<kernel-id>/`. The BLS entry points to those
   files; it does not load the kernel from Btrfs. `dr-state` deduplicates these
   pairs and removes only pairs no remaining BLS entry references.
3. **Btrfs root filesystem:** keep the Btrfs partition, filesystem metadata,
   UUID, and the selected recovery `@os-*` subvolume intact. The BLS entry
   passes the filesystem UUID and `rootflags=subvol=...` to the kernel.
4. **At least one known-good state:** the older state must still contain a
   kernel/initrd pair and a usable userspace. A snapshot cannot recover itself
   if it was also damaged or deleted.
5. **Sequence counter for future snapshots:** keep
   `/.top/.dr-state/last-sequence` intact. Recovery boots do not read it, but
   new snapshots require it to avoid reusing IDs after deletion or rollback.
6. **Operator input:** the boot menu must be reachable and the operator must
   be able to use the firmware's keyboard input path to choose an entry.

Do not manually erase the high-water counter, the ESP, `loader/entries`, the systemd-boot EFI binary,
the recovery kernel/initrd copy, the Btrfs filesystem, or every older
`@os-*` state. `dr-state delete` and `prune` are the supported cleanup paths;
they preserve configured boot targets and check that a bootable entry remains.

If the ESP or Btrfs filesystem is damaged, firmware cannot start the boot
manager, or every available OS state is unusable, this PoC cannot recover the
machine by selecting another state. Such failures need separate installation
media, filesystem repair, or an external backup.

## Data boundary

Rollback replaces the OS root with a selected OS subvolume. It deliberately
does not roll back the separately mounted user and service data. The installer
currently places `/home`, `/root`, `/srv`, `/var/www`, `/var/log`,
`/var/lib/docker`, `/var/lib/containers`, `/var/lib/postgresql`,
`/var/lib/mysql`, and `/usr/local` outside the OS state.

That separation preserves those files during OS rollback, including writes
made after the recovery snapshot. It also means package/database schema changes
and service data remain at their current versions. A restored older OS may be
incompatible with newer service data. This rollback mechanism is not a backup
or a transaction for application data; protect and migrate that data using
service-specific procedures.

## Destructive acceptance scenarios

Each scenario requires a fresh installed systemd-boot guest whose running
state is its configured default. The driver creates a recovery snapshot, writes
a witness to the persistent `/home` subvolume after the snapshot, makes the
default unusable, then drives the systemd-boot menu using QEMU's virtual
keyboard. A successful login must report a new boot ID and the recovery
subvolume in `rootflags=subvol=`. The scenario then sets that recovered state
as default, verifies an ordinary reboot, deletes the failed OS state, and
checks that the witness remains and the failed BLS entry is gone.

| Scenario | Damage | Required result |
|---|---|---|
| `recovery-kernel` | Replace the running state's kernel and initrd with invalid payloads, then regenerate the ESP entries. The pre-change snapshot keeps a different, intact ESP kernel pair. | The operator selects the recovery snapshot BLS entry from the boot menu; it reaches userspace with its kernel modules working. The post-snapshot `/home` witness survives. The repaired default boots normally and the unusable OS state can be deleted. |
| `recovery-library` | Remove the target of the package-owned `/lib64/ld-linux-x86-64.so.2` symlink with an atomic rename and parent-directory fsync. The damaged root cannot start init. | The serial log records the kernel's `No working init found` failure. After a reset, the operator selects the recovery snapshot BLS entry; the exact loader symlink and target are restored and the `/home` witness survives. The repaired default boots normally and the unusable OS state can be deleted. |
| `recovery-dependency` | Install a local app package that depends on an exact version of a local library package, start its systemd service, create the recovery snapshot, then manually remove the library with `dpkg --force-depends`. | `apt-get check` names the broken dependency, the app and its service fail, and the app remains installed. The operator selects the recovery snapshot BLS entry; dpkg consistency, the library, app, and service return, post-snapshot `/home` data survives, the recovered state boots as the default, and the damaged state can be deleted. |

Run each on a fresh disposable VM. Do not reuse a damaged guest. These
scenarios deliberately do not stage `dr-state boot-once`: they test selection
after the default has become unusable. Pruning is covered by the offline
`dr-state prune` workflow suite; live prune and ESP cleanup are not
guest-verified (DR-85, DR-97).

## Evidence and implementation

| Item | Location |
|---|---|
| Guest acceptance driver | `poc/dr-serial.py`, scenarios `s2-s3`, `recovery-kernel`, `recovery-library`, and `recovery-dependency` |
| State deletion, pruning, and boot-menu generation | `files/usr/local/sbin/dr-state` and `files/usr/local/lib/dr-state/dr_state/` |
| Offline deletion and selector safety coverage | `poc/test-dr-state-sdboot.sh` |
| Serial/QMP and scenario contract coverage | `poc/test-dr-serial.py` |
| Registered gate | `run-gate offline` from `scripts/debian-rollback/` |

Dated guest results for the current source are in the
[systemd-boot M2 report](RESULTS-SDBOOT-M2-2026-11-22.md) and the
[NoCloud builder report](RESULTS-SDBOOT-NOCLOUD-2026-11-23.md).

Historical recovery evidence used `installed-sdboot-c749-r10.qcow2` (SHA-256
`3b13b57d6d8f8a918e5cd9dbae548e48f2acd9d4505cc67df97e7bbc7d7d284c`),
`dr-state` hash
`ab7cf555eba00c96de24edb7afdba8437b83b506ee0f9c14fe4fc17a6bac3e52`, driver
hash `5f8988855a3eadbf6d2e54b7e2a55a3e79867a2643ff82d9d4ffd92c2d5bef74`, and
installer hash `ca8848f773392aaf5eac8e90e2b89885849dbd8fac4372a58cf2ad3312e3f0a0`.
Exact counts, hashes and log locations of these historical runs are in git at
`313ae9dcb` (this file); host evidence is in the rollback evidence archive
(controller state, P-rollback-01 index).
