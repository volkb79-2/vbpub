> Sections 'ZFS state identity and apt flow' and 'ZFS state-manager CLI', and every send/receive-duplicate, `rpool/DR-STAGING`, transaction-registry, orphan and `dr-zfs-state` reference elsewhere (layers table, install step 5, pool layout, acceptance rows), are superseded by D28 (native snapshot checkpoints, clone/promote, name = state ID); rewrite in P-rollback-12.

# ZFS boot-environment PoC

**Status, 2026-11-23:** active after the btrfs/systemd-boot variant merged to
main in 9bd4e5d. The first ZFS target is a fresh Debian 13 UEFI install with
one ZFS pool and ZFSBootMenu. The pinned EFI input has been authenticated and a
separate ZFS ISO has been built and booted in a UEFI guest. The Live kernel's
ZFS module built and loaded. Three installer attempts reached target setup but
did not complete. The first exposed a missing `/etc/dkms` directory, now fixed;
the second exposed an older debootstrap kernel without a ZFS module, so the
target kernel, matching headers, and ZFS packages are now installed together
after refreshing target APT. A fresh guest proved that this reaches kernel
6.12.118 and generates its initramfs. It then exposed a false BE mountpoint
failure: during install, `zpool create -R /mnt/drzfs-target` makes ZFS report
the effective root mountpoint as `/mnt/drzfs-target`. Read-only guest imports
of the preserved pool confirmed that it reports `/` after import without an
altroot. Source now validates the effective root against the expected
altroot; the offline lane passes, and the rebuilt candidate is under fresh
guest validation. This plan separates intended behavior from evidence already
proven by the btrfs PoCs.
See the [dated ZFS guest results](RESULTS-ZFSBOOTMENU-POC-2026-11-23.md) for
artifact identities and the current retest evidence.

## Goal and scope

Prove that a user can recover an installed Debian system from a deliberately
unusable package state by selecting an earlier ZFS boot environment in
ZFSBootMenu. The first functional failure is a dependency break, as selected
for the btrfs acceptance. Recovery is operator-selected; automatic fallback
after a failed boot is not required.

Use disposable UEFI guests on leased test hosts. The guest experiment proves
installation, state management, and boot behavior; TCG results do not measure
filesystem performance. The real-host E2 comparison in
[EXPERIMENTS.md](EXPERIMENTS.md) remains separate and is not authorized by the
VM allowance.

## Planned layers

| Layer | First ZFS variant | Work and proof |
|---|---|---|
| Source ISO | Verified official Debian Live ISO | Record source hash; keep the existing GRUB and systemd-boot btrfs variants unchanged |
| ZFS target ISO | Builder and first output exercised | Separate build-time ZFS variant; Live itself continues to boot through Debian Live GRUB. The remaster carries a fixed `/dr/target-filesystem` marker and separate ZFS hook/entry point. At install time, enable Debian `contrib`, install the headers matching the running Live kernel, build/load the matching ZFS DKMS module, and fail before disk writes if that fails; do not rebuild the Live squashfs for the first pass |
| Live installer | Booted in a UEFI guest; target install not yet complete | Use `dr-plan` only for target selection and config validation, then run a separate fail-closed ZFS-existing-data guard before any wipe and again immediately before pool creation; `REINSTALL=yes` is the explicit override. Do not route through `dr-autoinstall` or `install-btrfs.sh` |
| Target storage | One pool; each BE is one writable root filesystem with its own `/boot`; persistent-data and rollback metadata datasets are outside every BE | Set `mountpoint=/` and local `canmount=noauto` on every BE; keep `/boot` inside each BE; prove mount points and state boundaries against [ROLLBACK-RECOVERY.md](ROLLBACK-RECOVERY.md) |
| Boot manager | Version-pinned ZFSBootMenu EFI artifact on the ESP | ZBM owns BE discovery, its interactive boot menu, and the persistent pool default via `bootfs`; prove boot and default selection on plain reboot |
| Rollback runtime | Initial `dr-zfs-state` source and APT protocol hook added; offline and guest proof pending | Preserve the modern monotonic state-ID contract, metadata/reporting, delete/prune safety, and informative refusal when required state metadata is missing |
| Optional seed media | Not part of the M2 product install | A NoCloud builder may be added later for repeatable construction; it is not a prerequisite for the ISO acceptance |

The ZFS build choice is a filesystem/backend choice, not a bootloader value.
The ZFS artifact carries `/dr/target-filesystem` with the exact value `zfs` and
has its own Live hook and `dr-zfs-autoinstall` entry point. It does not carry
`/dr/target-bootloader` and does not enter the existing `dr-autoinstall` guard
or `install-btrfs.sh` path. Keep `/dr/target-bootloader` and `--bootloader`
closed to `grub|systemd-boot`; the existing `--bootloader zfs` refusal must
continue to pass. `dr-plan` is a pure disk-selection/config planner, not the
existing-system wipe guard. When `TARGET_DISK` is explicit, the ZFS installer
probes it before running the planner; when the planner must select a disk, it
probes that candidate immediately after selection and before confirmation or
package setup. It scans the disk and its partitions for an existing ZFS
member/pool signature, refuses if found unless `REINSTALL=yes` was explicitly
supplied, and fails closed if the read-only probe cannot establish that the
disk is clear. After slow package setup, re-plan and repeat the guard
immediately before the first destructive write. This prevents a still-attached
install ISO from silently wiping the healthy ZFS system it just installed.

### ZFSBootMenu owns boot mechanics

ZFSBootMenu discovers a boot environment from a ZFS filesystem with
`mountpoint=/` that is not hidden with `org.zfsbootmenu:active=off` and that
contains a paired kernel and initramfs under its own `/boot`. Each BE is one
root filesystem; `/boot` is inside it, not a shared or separate dataset. Set
`canmount=noauto` locally on **every** BE, including each duplicate. This
property is not inherited; omitting it can make multiple root datasets compete
to automount at `/`. The installer and state manager verify these properties
and the kernel/initramfs pair locally before advertising a BE as bootable. They
do not ask the ZBM UI to validate it during an apt hook.

ZBM owns environment discovery, boot-menu selection, and the normal persistent
default. The pool's `bootfs` property identifies that default; the product's
`set-default ID` resolves the ID through sidecar metadata and sets
`zpool set bootfs=<BE-dataset> <pool>`. `list` reports the BE named by `bootfs`.
The pinned ZFSBootMenu v3.1.0 documentation specifies a 10-second countdown;
the PoC does not pass `zbm.timeout=0` or `zbm.skip`. Guest evidence records the
countdown shown by the pinned artifact, and acceptance verifies that the
operator can interrupt it to select an older BE. `zbm.timeout` is a command-line
setting for the ZBM EFI image; it is distinct from
`org.zfsbootmenu:commandline`, which supplies arguments to the selected Debian
kernel. The target's ifupdown DHCP stanza uses the IPv4 default-route interface
detected in the Live system, rather than assuming the legacy name `eth0`. This
preserves networking on predictable names such as `enp0s1` after the installed
kernel boots. The first ZFS PoC has no `boot-once` command or separate
one-shot selector. Selecting an older BE in the ZBM menu is temporary; after
recovery the operator uses `set-default` to make it persistent. There is no
custom boot-selector file or menu generator.

### ZFS state identity and apt flow

A user-visible state ID names an **independent writable duplicate boot
environment**, not a read-only snapshot or an origin-dependent clone. One
exclusive `/run/dr-zfs-state.lock` serializes the apt hook and mutating state
commands. The apt pre-change hook waits at most 600 seconds for the lock; on
timeout it aborts before dpkg with an actionable retry message. Manual state
mutations use a nonblocking lock attempt and fail with “a checkpoint or state
operation is running; retry after it completes.” In particular,
`delete --orphan` is deliberately refused during a checkpoint rather than
queued behind a potentially long copy.

Before creating ZFS objects, the hook allocates one opaque `TOKEN` and
durably records the transaction phase, state ID, source BE name and GUID, exact
source snapshot, staging dataset, exact received snapshot, final dataset,
temporary and final sidecar paths, and last error outside every BE. As each new
ZFS object is created, record its GUID and durably update the journal before
proceeding.
The same token is used in `rpool/DR-STAGING/<TOKEN>`,
`<source-BE>@dr-pending-<TOKEN>`, and
`rpool/DR-STAGING/<TOKEN>@dr-pending-<TOKEN>`. Commands never reconstruct
authority by parsing a dataset or snapshot name. Before destroying a dataset or
snapshot during rollback, orphan cleanup, delete, or prune, compare its GUID
with the recorded GUID. A name that exists without a recorded matching GUID
is an orphan for manual inspection; automated cleanup refuses to destroy it.

The installer sets `org.zfsbootmenu:commandline` locally on every BE. The
hook creates the exact temporary source snapshot and sends one full root
filesystem with `zfs send -p` (without `-R`). The `-p` preserves properties
set locally or received on the BE, but it does not transmit inherited
properties; the runtime verifies the local source value and received value
match. Managed BEs must not have local `quota`, `reservation`,
`refquota`, or `refreservation` properties. The installer sets none, and
the runtime refuses to checkpoint a BE if any of those properties is locally
set, so a checkpoint cannot acquire an unbudgeted limit or reservation.
Omitting `-R` keeps child datasets and source snapshot history out of the
duplicate. It receives to the exact
snapshot path under the staging dataset with `-u` and receive-time overrides
that force `mountpoint=none`, `canmount=off`, and a **local**
`org.zfsbootmenu:active=off`, even if those properties are present in the
stream. OpenZFS receive creates the named destination snapshot; the
transaction record tracks it separately from the source snapshot. The
`rpool/DR-STAGING` parent has only `mountpoint=none` and `canmount=off`; it
does not carry an `active=off` property that a BE could inherit later.

The receive is not mounted automatically. While it remains hidden, the hook
sets and verifies local `canmount=noauto`, then checks the kernel/initramfs
pair and other contents using the explicit ZFS mount form
`mount -o zfsutil,ro -t zfs DATASET TEMPORARY-MOUNTPOINT`. The `zfsutil`
option permits this explicit mount while the persistent `mountpoint` remains
`none`; the hook unmounts the dataset after validation and verifies the
property remains unchanged. It prepares and validates the sidecar, including
the dataset GUID, in a temporary file; this does not publish the state-ID
mapping yet. A failed metadata write never exposes partial sidecar metadata.

Publish is this ordered operation while holding the lock:

1. Remove the exact received destination snapshot and exact source snapshot.
   The apt hook runs before the first package-changing dpkg call, so the source
   BE is still unchanged; no package change has begun, so removing its
   temporary source snapshot is safe. Keeping either temporary snapshot after
   validation is unnecessary. The independent duplicate now has no associated
   snapshots.
2. Rename the hidden staging dataset to its reserved final name under
   `rpool/ROOT`. Verify its local `canmount=noauto`, effective
   `mountpoint=none`, temporary sidecar-to-dataset mapping, kernel/initramfs
   pair, and other BE facts.
3. Set the child's local `org.zfsbootmenu:active=on` explicitly while its
   mountpoint is still `none`. Never clear this property with `zfs inherit`:
   that could restore a parent's `active=off` value.
4. Set the child's local `mountpoint=/` as the final visibility change. Then
   re-read the **effective** `org.zfsbootmenu:active`, `mountpoint`, and
   `canmount` values, and re-check bootability. Require `active=on`,
   `mountpoint=/`, and `canmount=noauto` before continuing.
5. Atomically publish the sidecar to its final path only after post-publish
   verification. Remove the transaction record only after that succeeds; apt
   may proceed only when both the visible BE and sidecar are committed.

If any step fails, abort apt. Before cleanup destroys any recorded dataset or
snapshot, it verifies that the object's current GUID equals the GUID captured
in the durable transaction record. The received snapshot GUID must also match
the source snapshot GUID. If an object exists but the journal has no matching
GUID (for example, a process died immediately after receive), leave it
registry-backed as an orphan and refuse automated destruction for manual
inspection. Cleanup first attempts to hide the exact
candidate by setting its local `active=off`, `mountpoint=none`, and
`canmount=off`, then re-reads their effective values. When it is safe, rename
the exact final candidate back to staging and destroy only that recorded
dataset. Cleanup also removes either exact temporary snapshot if it remains,
and removes the sidecar whether it is still temporary or was already
published. It removes the transaction record only after all cleanup succeeds;
it never decrements the high-water ID. If cleanup fails, keep the durable
record as an operator-actionable orphan with the exact remaining objects,
effective visibility properties, and error. A visible orphan must be reported
as visible; if it cannot be proved not to be running or the pool default, the
cleanup command refuses to destroy it.

The transaction registry distinguishes `in-progress` from `orphan`. `list`
shows locked work as in-progress. A registry entry always takes precedence
over sidecar or dataset discovery: its objects are never mislabeled
unmanaged, and `delete ID` refuses any candidate claimed by a transaction.
After a process dies, a lock-free recovery pass revalidates the exact recorded
objects before changing an in-progress record to orphan. If an orphan's
recorded ZFS objects are already absent, `list` reports it as stale-with-error
and names the missing objects; it remains registry-backed, not unmanaged.
Prune never removes in-progress or orphan records implicitly.

Only a token already marked orphan in the registry is accepted by
`delete --orphan TOKEN`. It derives every object path from that record, takes
the nonblocking state lock, and refuses if another operation holds it. It
checks that a candidate dataset is not running or the pool default, has no
unexpected snapshots or child datasets, and has the recorded GUID. The source
BE itself may be the running/default system: cleanup may remove the recorded
temporary snapshot
from it only when that snapshot's GUID matches the journal, never the source
dataset. An unregistered token and a hand-created
lookalike dataset in `rpool/DR-STAGING` confer no authority. On successful
cleanup, the command removes the registry record and any recorded sidecar,
including for a stale orphan whose ZFS objects are already absent. A failed
cleanup keeps the orphan record and updated error for retry.

The duplicate has no clone-origin dependency, so deleting that BE does not
require promote bookkeeping or retain an internal snapshot indefinitely. The
runtime creates no persistent snapshots. ZFSBootMenu can create snapshots
through its own UI; those remain visible and are not managed by
`dr-zfs-state`. If a managed BE has snapshots, `delete` refuses to remove it
and names each snapshot plus the exact `zfs destroy BE@SNAPSHOT` command the
operator can use after reviewing it. The refusal must be actionable and
clearable; the runtime never deletes an unowned snapshot automatically. The
ZFSBootMenu snapshot menu is empty on a fresh BE until an operator creates a
snapshot. The product's rollback unit remains the whole writable BE.

This deliberate first-PoC choice trades pool space and copy I/O for simple,
independent retention: ZFSBootMenu documents that a send/receive duplicate
has no associated snapshots and consumes pool space equal to the source
snapshot's `REFER` value. Before apt continues, the runtime must prove the
pool can hold the duplicate and refuse safely when it cannot. Record the
duplicate size and remaining pool space in guest evidence. If snapshot,
send/receive, either temporary-snapshot removal, BE-property, metadata, or
bootability validation fails, apt must not proceed with the first
package-changing dpkg call. Inject failures at each operation through
transaction-record removal; cleanup must use the exact recorded objects. The
behavioral pass condition is that no corresponding staging dataset,
incomplete final dataset, temporary source or received snapshot, sidecar, or
transaction record remains. Report post-cleanup `zpool list` free space as
diagnostic data, not as a thresholded pass condition.

The full send/receive reads and writes data proportional to the source BE's
`REFER` for every checkpoint; the dedupe window limits repeated checkpoints,
not the I/O cost of one checkpoint. This can delay an apt transaction and is
accepted for the small retained set in this functional PoC. It makes no
performance claim. Revisit the tradeoff if retention or target capacity grows.

The apt transaction continues in the currently booted environment; creating
the duplicate does not switch the running root. The new ID identifies the
pre-change duplicate. The environment receiving package changes remains
bootable under its existing dataset identity, matching the current btrfs
hook's snapshot-before-dpkg behavior.

The apt hook is called before each dpkg invocation, so apply the existing
parent-aware `DR_SNAPSHOT_MIN_INTERVAL` contract: create at most one automatic
checkpoint per currently running BE during the configurable window (default
900 seconds; 0 disables the window). A later invocation in the window updates
that checkpoint's recorded package actions instead of creating another BE.
Manual snapshots always create their own checkpoint. Key the window to the
source BE dataset so booting a different environment starts a different
window. Test the window with an injected clock; do not sleep for real time.
If updating checkpoint metadata fails on a later dpkg call, abort that call;
earlier package calls may already have changed files, but the verified
pre-change duplicate remains available for recovery.

The display state ID remains a creation sequence, UTC timestamp, and origin or
reason slug, beginning with @os-000001-YYYYMMDD-HHMMSSZ-install. The dataset
component uses a separate safe name; sidecar metadata is the authority that
maps a display ID to its dataset and stores the dataset GUID. Keep the
high-water counter and state metadata outside every boot-environment dataset
so reverting an OS state
cannot reuse a deleted number. ZFSBootMenu's displayed dataset name should
remain useful for visual comparison, but the runtime must not infer an ID by
parsing that name.

### ZFS state-manager CLI

Use a separate `dr-zfs-state` command; do not make the Btrfs `dr-state`
operations appear to support ZFS. Preserve the consumer-facing state commands:

| Command | First-PoC behavior |
|---|---|
| `dr-zfs-state list [--long]` | List managed BEs with ID, UTC creation time, origin/reason, note, source, package plan, running/default labels, and kernel; `--long` includes effective `mountpoint`, `canmount`, and `org.zfsbootmenu:active` values plus snapshot names; registry-backed in-progress/orphan transactions take precedence over sidecar and dataset discovery and are never called unmanaged; show unrelated discovered BEs lacking sidecars as unmanaged and never prune them; show missing-object orphans as stale-with-error |
| `dr-zfs-state show ID` | Report one exact ID and its dataset GUID, metadata, effective bootability properties, snapshot names, kernel, and package summary |
| `dr-zfs-state snapshot [--reason WORD] [--note TEXT]` | Allocate the next ID and make an independent writable duplicate; manual checkpoints always bypass the apt dedupe window |
| `dr-zfs-state set-default ID` | Resolve the ID through sidecar metadata, validate the BE, and set the pool's `bootfs` property |
| `dr-zfs-state delete ID` | Use `--dry-run` to preview; require `--yes` to remove one exact inactive managed BE; refuse the running BE, the `bootfs` BE, any snapshot, any transaction-claimed candidate, and unexpected child datasets. For snapshots, name each exact snapshot and give the reviewed `zfs destroy BE@SNAPSHOT` remedy; verify the BE GUID before destruction |
| `dr-zfs-state delete --orphan TOKEN [--dry-run] [--yes]` | Accept only a registry-backed orphan token; take the nonblocking state lock and refuse immediately if another operation holds it. Preview by default; require `--yes`; remove only exact recorded datasets, snapshots, and sidecars after checking the candidate is not running/default, has the expected GUID, and has no unexpected snapshots or children. The source BE may be running/default, but only its exact recorded temporary snapshot may be removed after its GUID matches. Successful cleanup removes the orphan record; a stale orphan can be retired after its absent objects are verified. Never derive authority by parsing a name; unregistered tokens and lookalike datasets are refused |
| `dr-zfs-state prune [--keep N] [--dry-run] [--yes]` | Default to keeping the initial install and newest 2 timestamped checkpoints; `--keep N` overrides 2. Preview exact eligible candidates by default; report every skipped state and the exact snapshot/child reason instead of silently omitting it; require `--yes` before removing any; verify each candidate's recorded GUID before destruction |

The first ZFS PoC has no `boot-once` command: ZBM menu selection is the
operator's temporary choice, and `bootfs` is the persistent default. Deleting
or pruning therefore protects the running BE and `bootfs`; there is no
separate pending one-shot selector to protect.

The btrfs dr-state implementation operates on Btrfs subvolumes and is not a
ZFS backend. Reuse the user-visible state contract and pure schema logic where
it is sound; implement and test ZFS dataset, snapshot, duplicate, default, and
cleanup operations explicitly. Do not copy the btrfs runtime onto old
@os-0001 installs; the product remains greenfield and uses the modern schema.

## Installer and boot flow

1. Build a separate ZFS target ISO from the verified Debian Live base. Its
   build-time choice is immutable in the artifact, just as the existing M2
   GRUB/systemd-boot target choice is.
2. Boot the Live environment through Debian Live's GRUB menu. The ZFS tools
   and module must work with that exact Live kernel before the installer
   starts.
3. Select the ZFS unattended install entry or start its separate
   `dr-zfs-autoinstall` entry point manually. It validates the exact
   `/dr/target-filesystem=zfs` marker, creates the pool and datasets,
   bootstraps the target without a kernel image, refreshes target APT, then
   installs `linux-image-amd64`, matching `linux-headers-amd64`, DKMS, and the
   ZFS packages together. This avoids retaining a debootstrap kernel for which
   the refreshed repository no longer has matching headers or a ZFS module.
   It then installs Debian into the initial boot environment, installs the
   rollback runtime and apt hook, and places the ZFSBootMenu EFI artifact on
   the ESP. Install it at a stable `EFI/ZBM/` path and copy the same verified
   artifact to `EFI/BOOT/BOOTX64.EFI`, the portable UEFI fallback path. The
   installed disk must boot from fresh firmware variables without requiring a
   writable EFI NVRAM entry; `efibootmgr` may add a convenience entry but is
   not required. Pin `zfsbootmenu-recovery-x86_64-v3.1.0-linux6.12.EFI` and its
   upstream SHA-256 (`9910458dfe7fc2895214922e22b6ae2513cd0f5a43d4d3c5526eee26e69a2198`),
   not a mutable latest URL. Verify the release's `sha256.sig` with `signify`
   and the pinned `zfsbootmenu.pub`; independently verify the public key's
   inline-signed OpenPGP message `zfsbootmenu.pub.gpg` with GPG and record its
   full signer fingerprint and key SHA-256. Keep the upstream EFI artifact
   unmodified, install a backup copy alongside it, and record the exact asset
   hash in guest evidence.
   Create the initial dataset with `mountpoint=/`, local `canmount=noauto`,
   `/boot` inside the BE, and set `org.zfsbootmenu:commandline` locally
   with kernel-console options (do not set `root=`; ZBM supplies the selected
   root). Set the pool's `bootfs` to that dataset. Every duplicate must retain
   the local commandline value; changing a parent must not rewrite older BEs'
   boot arguments.
4. Reboot with only the installed target disk attached. Select the initial
   environment in ZFSBootMenu and verify that Debian reaches the expected
   multi-user state.
5. Before an apt transaction, capture the pre-change environment and package
   plan. Create the independent writable duplicate carrying the new state ID;
   validate the same BE properties and paired `/boot` files that ZBM scans.
   If snapshot, duplicate, metadata, capacity, or bootability checks fail, the
   first package-changing dpkg call must not proceed.
6. After the dependency-break injection, reboot and select the known-good
   earlier environment from ZFSBootMenu. Verify recovery from inside that
   booted state; then make it the default, perform a plain reboot, and remove
   the unusable environment through the supported state-management command.

Use one pool with a root container `rpool/ROOT`, a reserved receive namespace
`rpool/DR-STAGING` (`mountpoint=none`, `canmount=off`, with no inherited
`org.zfsbootmenu:active=off` property), a single root dataset per BE, and
separate persistent-data and
`/var/lib/debian-rollback` metadata datasets. Temporary source snapshots use
the reserved `@dr-pending-<token>` suffix so list and orphan cleanup can identify
only snapshots created by an interrupted checkpoint. Each staged child gets a
local `active=off`; each published BE gets a local `active=on`.
Use `/home`, `/root`, `/srv`, `/var/www`, `/var/log`, configured service data,
and `/usr/local` outside each BE as specified by D18. Keep `/etc`, `/usr`,
`/var/lib/dpkg`, `/opt`, and `/boot` inside each BE. Use GPT with an ESP and
one pool partition; configure no swap on the target and do not create swap on
the pool or a zvol. Use a 512 MiB ESP; the first guest fixture uses a 64 GiB
target disk, which is an experiment size, not a claimed minimum supported disk.
Use the 2048 MiB guest-RAM baseline for installed-system boot and recovery,
do not cap the ARC for this functional PoC, and record guest RAM, disk size,
and ARC policy in the report. The current Live installer run uses 3072 MiB
under TCG for its package/DKMS phase; that is a run setting, not a measured
minimum RAM requirement. This acceptance pins the installed guest
configuration without making a performance claim; ARC sizing remains a
separate design question. A later 1–2 GiB experiment is specified below.
The pinned recovery EFI asset is 85,742,592 bytes according to the v3.1.0
release metadata. The ESP stores three identical copies (canonical, backup,
and firmware fallback); preflight the measured asset size and require at least
128 MiB of free space after all three copies on the formatted 512 MiB ESP.
Re-measure the asset size and actual ESP free space during the build/install;
do not rely only on this recorded release size.
The pool takes the remaining target space. Create it with
`compatibility=openzfs-2.3-linux`, matching the conservative setting in the
versioned ZFSBootMenu 3.1 Debian guide and Debian Trixie's OpenZFS 2.3 package
line. Record the exact Debian ZFS module and ZBM OpenZFS versions. Do not run
`zpool upgrade` in this PoC. A future pool-feature upgrade requires updating
and verifying the ZBM artifact first.

The default prune policy keeps two timestamped checkpoints plus the initial
install. Derive the guest pool-space budget from measured values rather than a
fixed disk-size guess: let `R` be the largest measured root-BE `REFER` across
the fresh install and representative package update, `D` the declared
persistent-data capacity budget, and `M` measured metadata plus package-update
growth reserve. For the normal lineage where the running BE is the install
dataset, reserve at least `(keep + 2) * R + D + M`: one active BE, the
retained checkpoints, and one new full duplicate while an apt checkpoint is
being created. With `keep=2`, that is four measured BE references before data
and reserve. If the initial, running, or default BE is a separate protected
dataset, sum the measured `REFER` for each protected/retained BE and add the
new duplicate, `D`, and `M`; do not use the four-BE estimate as a hard-coded
limit. The installer reports whether the 64 GiB guest fixture satisfies the
derived budget; if it does not, adjust the fixture or sizing before claiming
acceptance. Before each duplicate, the runtime checks actual available pool
space against the new source `REFER` plus required reserve, then refuses
before dpkg if the copy cannot safely fit. Manual snapshots also refuse on
insufficient space; they never trigger implicit deletion.

The Live installer enables Debian `contrib`, installs
`linux-headers-$(uname -r)`, `dkms`, `zfs-dkms`, and `zfsutils-linux`, then
requires the module to build and load for the exact running Live kernel before
writing the target. The target receives Debian's kernel, headers,
`zfs-dkms`, `zfsutils-linux`, and `zfs-initramfs`; configure
`/etc/dkms/zfs.conf` with `REMAKE_INITRD=yes`, rebuild its initramfs after the
ZFS module is installed, and create/copy a unique nonzero `/etc/hostid` with
`zgenhostid`. Record resolved package versions. Missing matching Live headers,
DKMS build failure, or module-load failure is a pre-write refusal.

The remaining implementation decisions are pool tuning beyond the chosen
compatibility set, the persistent-data budget used in the first guest, and the
concrete installer/runtime file layout. Resolve these from current primary
documentation and prove them in disposable guests before treating them as
product behavior.

## Acceptance matrix

| Case | Required result |
|---|---|
| Installer re-run safety | Boot the ZFS install medium a second time against its already-installed target; the read-only ZFS signature/pool guard refuses before any write unless the config explicitly has `REINSTALL=yes`. A failed signature probe also refuses. Repeat the guard after package setup immediately before the first destructive disk write |
| Fresh install and first boot | With fresh firmware variables and no NVRAM entry, the target boots through `EFI/BOOT/BOOTX64.EFI` on its ESP into ZFSBootMenu; the initial state is reported as 000001-install and its ZFS dataset is selectable |
| Snapshot/report | An apt run records the pre-change source and an independent writable BE duplicate, package plan, timestamp, reason, and state metadata; list/show identify the duplicate by its display ID, and its temporary source snapshot is removed |
| Repeated dpkg calls | An unattended-upgrade run with multiple dpkg pre-invoke calls creates one duplicate for the running BE in the configured window; later calls update package-action metadata instead of increasing menu entries or pool usage |
| Dependency break and manual recovery | Reuse the real local app/library dependency failure from the btrfs recovery acceptance; the damaging operation leaves the default unusable; the operator selects the known-good environment in ZFSBootMenu; the damaged package state is absent after recovery |
| Persistent-data boundary | A witness written after the snapshot to /home and a service-data path survives recovery; /etc, /usr, /var/lib/dpkg, and other OS-owned content return with the selected environment |
| Writable recovery and follow-up apt | Boot the recovered environment, write a marker into its OS dataset and verify it survives reboot; its display ID remains stable; a later apt transaction creates and reports the next writable checkpoint |
| Default and cleanup | Set the recovered environment as default by updating `bootfs`, plain-reboot to it through the displayed countdown, then delete the now-inactive failed environment; unrelated environments and data remain intact |
| Countdown and operator selection | Record the initial countdown displayed by the pinned ZBM image and verify it matches the documented 10 seconds; interrupting it exposes the BE menu and permits selection of an older environment. `set-default` persists that choice through `bootfs` |
| ZFSBootMenu snapshot interaction | Create a snapshot of a managed BE through the ZBM UI. `list --long` and `show` report its exact name. `delete` and `prune` refuse to remove that BE, name the snapshot, and give the exact reviewed `zfs destroy BE@SNAPSHOT` remedy; `prune --dry-run` lists it as skipped with the same reason. After the operator reviews and removes that snapshot, retrying the product command succeeds |
| Retention and prune | `delete ID --dry-run` previews exactly one BE; `prune --dry-run` defaults to keeping the initial install and newest 2 checkpoints, and `--keep N` changes that count. Execution requires `--yes`, protects the running BE and `bootfs` default, reports every skipped state and exact snapshot/child reason, removes only independent managed duplicate datasets and sidecars whose GUID matches the registry, and never reuses IDs |
| Kernel coverage | Boot the old and new kernels after an update; include stable and backports where the packaged ZFS module supports them; a missing module must fail clearly before an unbootable default is selected |
| Counter and metadata safety | Deletion does not reuse sequence numbers; absent or corrupt high-water metadata refuses new snapshots; failed snapshot creation prevents apt changes |
| Checkpoint failure cleanup | Inject one failure at each checkpoint operation: source snapshot creation, receive, staged-property/content verification, removal of either exact temporary snapshot, metadata preparation, dataset rename, setting local `active=on`, final `mountpoint=/` publication, effective-property/bootability re-read, sidecar publication, and transaction-record removal. Each aborts dpkg; successful cleanup removes the exact source and received snapshots, staging/final dataset, temporary or published sidecar, and transaction record, leaves no incomplete BE under `rpool/ROOT`, preserves the non-reusable high-water ID, and leaves no transaction object. Inject cleanup failure separately and verify it leaves a registry-backed orphan for the recovery case below. Report post-cleanup free space after `zpool sync` as diagnostic data, not a pass threshold |
| Orphan cleanup path | Force cleanup to fail after an injected checkpoint error; after the lock is free, `list` reports the registry-backed candidate as an orphan, with effective `mountpoint`/`active` values, recorded/current GUIDs, and any missing objects. A token absent from the registry and a hand-created lookalike under `rpool/DR-STAGING` are refused. `delete --orphan TOKEN --dry-run` previews only recorded objects and sidecars; it refuses immediately while another state operation holds the lock. Recreate an object at a recorded name with a different GUID and verify cleanup refuses to destroy it. `prune --dry-run` does not list or remove the orphan. `--yes` cleanup removes only exact remaining objects whose GUIDs match the journal, sidecar, and registry record; repeating cleanup of a stale orphan retires only its record, leaving managed BEs and user data intact |
| State lock behavior | A second apt pre-change hook waits up to 600 seconds, then aborts before dpkg with a retry message if the lock remains held. Manual mutations, including `delete --orphan`, fail immediately with an actionable retry message while a checkpoint or other mutation holds the lock; they are not queued |
| Pool sizing and capacity refusal | Record `R`, `D`, `M`, the normal-lineage `(keep + 2) * R + D + M` estimate, any additional actual protected-BE `REFER`, target pool size, and free space. Verify managed BEs have no local quota/reservation/refquota/refreservation properties. If the pool cannot hold the full independent duplicate and required reserve, the apt hook refuses before its first package-changing dpkg call; it does not silently fall back to a clone or read-only snapshot |
| Boot-manager integrity | Verify the pinned upstream EFI asset with signify and the independently authenticated key before install; measure its bytes and prove `3 × asset bytes + 128 MiB slack` fits on the formatted 512 MiB ESP; verify all three ESP copies have the pinned SHA-256; the ISO marker cannot be overridden by install config; every fresh BE has effective `mountpoint=/`, local `canmount=noauto`, effective `org.zfsbootmenu:active=on`, no product-owned snapshots, a local `org.zfsbootmenu:commandline`, and a kernel/initramfs pair; ZBM discovers it without a custom menu; `set-default` updates `bootfs`; delete removes only that independent BE and sidecar after its GUID matches |
| Pool/ZBM feature ordering | The report records `compatibility=openzfs-2.3-linux`, active pool features, Debian OpenZFS module version, and ZBM release/OpenZFS version; the PoC does not upgrade pool features before updating and rechecking ZBM compatibility |
| Swap boundary | The installed target has no swap partition, file, ZFS volume, or zvol; report `swapon --show` empty after first boot |

The recovery and persistence requirements in
[ROLLBACK-RECOVERY.md](ROLLBACK-RECOVERY.md) remain the authority for what
must not be destroyed. Add ZFS-specific cases there only after the guest run
proves them.

## Test and evidence path

- Add or use a registered Run-Gate lane for the ZFS source checks and automated
  guest acceptance. Run project tests through tester-unified, not from the
  cockpit. The existing offline lane covers current source checks; it does not
  by itself prove a ZFS guest boot.
- Run guest scenarios only against named disposable VM disks. Resolve
  vm-lab, vm-images, and ISO output paths through cowtree; keep raw run disks
  under scripts/debian-rollback/vm-lab and generated ISOs under the resolved
  ISO directory.
- Record exact source and ISO hashes, build inputs, guest disk provenance,
  commands, scenario counts, and retained logs in a dated report. Identify the
  ZFSBootMenu release/commit, upstream EFI input SHA-256, signify verification
  result, verified public-key SHA-256 and independently checked signer
  fingerprint, ESP artifact SHA-256 and byte size, formatted ESP capacity and
  post-copy free space, Debian OpenZFS/DKMS package and module versions, target
  kernel and headers, guest RAM/target-disk sizing, displayed ZBM countdown,
  ESP fallback-path evidence, and measured duplicate `REFER` plus copy wall
  time as descriptive data only.
- Before merging a ZFS candidate, pass the registered lane and obtain an
  exact-head independent review using only
  opendesign_volker/deepseek-v4.1-flash-eco.

## Explicit non-goals for this PoC

- Automatic fallback after a failed default boot.
- Secure Boot, encryption, conversion of existing btrfs installations, or
  migration of historical @os-0001 disks.
- Performance conclusions from QEMU guests.
- Physical hardware, host power-loss, and storage-durability behavior. The
  kexec boot path is proven only on the named QEMU guest configuration; no
  claim is made about other hypervisors or physical machines.
- Swap on the target disk for this PoC. The installed target has no swap;
  external host-managed swap is not part of guest acceptance.
- Reinstalling or modifying the leased test-host filesystems. The
  operator's VM authorization does not include the destructive E2 comparison.
- A ZFS-with-GRUB variant in this first ZFSBootMenu pass. Reconsider the
  single-pool restricted-GRUB design after the first report if it answers a
  question ZFSBootMenu leaves open; the two-pool layout stays excluded unless
  the one-pool design fails.
- The extra pool space used by independent duplicated checkpoints is accepted
  for this first functional PoC; benchmark and space-optimization comparisons
  are deferred. The runtime still refuses a checkpoint when the full duplicate
  cannot safely fit.

## Later experiments (outside current PoC acceptance)

### Low-memory ZFS operation

After the 2048 MiB installed boot and recovery acceptance passes, separately
measure whether the installed system remains useful at 1024, 1536, and 2048
MiB. Reuse a copy of the accepted installed base so the experiment measures
runtime rather than repeating the current TCG installer phase. Compare these
configurations at each RAM point:

| Configuration | Backing | Purpose |
|---|---|---|
| No swap | None | Current PoC baseline |
| ZRAM swap | Compressed RAM-backed block device | Test whether compression trades CPU for useful headroom |
| Zswap plus disk swap | Dedicated GPT swap partition outside the ZFS pool | Test compressed swap caching with a persistent backing device |

ZRAM stores compressed pages in RAM and can be enabled as swap. Zswap compresses
pages being swapped and evicts them to its backing swap device when its pool is
full; it is not a substitute for that backing device. The experiment must keep
swap separate from ZFS and must not place a swapfile or zvol in the boot pool.
If the dedicated partition is placed at the end of the disk, include the effect
on pool sizing and later pool growth in the layout assessment.

At each RAM/configuration point, record successful cold boot, ZFS module and
ARC memory, idle use, package metadata/update and a representative install,
checkpoint duplicate completion, and dependency-break recovery. Capture peak
memory, compressed and disk swap use, guest memory PSI, and whether operations
finish correctly. This is a usefulness and feasibility study, not a speed
benchmark. Do not change the current no-swap design until this evidence is
reviewed; do not infer the minimum from the 3072 MiB installer run.

### Prebuilt restore-image ISO

Evaluate separately whether to ship an ISO that writes a prebuilt Debian/ZFS
system instead of running debootstrap, package installation and DKMS on the
target. Compare a compressed whole-disk image with a ZFS stream applied into a
freshly created target pool. A whole-disk clone needs first-boot handling for
machine identity, SSH host keys, `/etc/hostid`, and duplicated ZFS pool GUIDs;
a stream into a new pool may avoid copying the pool identity but needs its own
install and recovery proof. Either route must make the destructive target-disk
write clear, reject undersized targets, and preserve the same reinstall guard.

Public distribution is a separate release review, not an assumed blocker or
an automatic clearance. Debian's FAQ permits redistributing Debian while
requiring distributors to honor each component's license; Debian currently
lists `zfs-dkms` in `contrib`. OpenZFS says its code is CDDL-licensed and that
binary kernel-module distribution is permitted. ZFSBootMenu's source
repository reports MIT and OFL-1.1 licenses. These are evidence that a
prebuilt image is not categorically barred, not blanket clearance for our
particular bundle: the EFI image and full OS image contain more components
than those projects alone. Before publishing, inventory each shipped binary,
kernel/module, boot image, firmware, and document; preserve required copyright
and license texts; provide corresponding source or another source method where
the specific license requires it; and confirm redistribution rights for every
non-free component and signature asset. Record package/source versions and the
audit with the release artifact. A prebuilt binary does not bypass its
component licenses; seek a project-specific legal review if the inventory
leaves a material ambiguity.

Primary references for this follow-up: [Debian redistribution FAQ](https://www.debian.org/doc/manuals/debian-faq/redistributing.en.html),
[Debian license information](https://www.debian.org/legal/licenses/),
[Debian's Trixie package list for `zfs-dkms`](https://packages.debian.org/trixie/zfs-dkms),
[OpenZFS licensing](https://github.com/openzfs/openzfs-docs/blob/master/docs/License.rst),
[Linux ZRAM documentation](https://docs.kernel.org/admin-guide/blockdev/zram.html),
and [Linux Zswap documentation](https://docs.kernel.org/admin-guide/mm/zswap.html).

## Primary implementation references

- [ZFSBootMenu Debian UEFI guide](https://docs.zfsbootmenu.org/en/v3.1.x/guides/debian/uefi.html)
- [ZFSBootMenu boot-environment guidance](https://docs.zfsbootmenu.org/en/v3.1.x/general/bootenvs-and-you.html)
- [ZFSBootMenu snapshot management](https://docs.zfsbootmenu.org/en/v3.1.x/online/snapshot-management.html)
- [ZFSBootMenu main-screen default selection](https://docs.zfsbootmenu.org/en/v3.1.x/online/main-screen.html)
- [ZFSBootMenu binary releases and signify verification](https://docs.zfsbootmenu.org/en/v3.1.x/general/binary-releases.html)
- [ZFSBootMenu command-line and timeout settings](https://docs.zfsbootmenu.org/en/v3.1.x/man/zfsbootmenu.7.html)
- [OpenZFS Debian Trixie Root on ZFS guide](https://openzfs.github.io/openzfs-docs/Getting%20Started/Debian/Debian%20Trixie%20Root%20on%20ZFS.html)
- [OpenZFS dataset properties](https://openzfs.github.io/openzfs-docs/man/master/7/zfsprops.7.html)
- [Debian ZFS package and installation notes](https://wiki.debian.org/ZFS)
- [Debian Trixie `zfs-dkms` package](https://packages.debian.org/trixie/amd64/zfs-dkms)
