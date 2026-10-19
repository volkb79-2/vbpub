# Boot-pool patch handoff

Updated: 2026-10-19 UTC

This is the resume point for the TrueNAS boot-pool patch work. Patch files,
tests, evidence, and follow-up notes stay under this directory.

## Working checkout

- Worktree: `/workspaces/vbpub/.worktrees/truenas-restore-iso`
- Branch: `truenas-restore-iso`
- Subproject: `scripts/truenas/boot-pool-patch`
- The worktree also contains separate Netcup work. Keep Netcup out of any
  TrueNAS commit.
- TrueNAS guest disks and logs are ignored artifacts beneath `artifacts/`.

## Live guest checkpoint (2026-10-19)

The earlier Path A r4, Path B r1, and topology guests were retired through
`tcg_vm.py`; their saved VM state directories remain available as artifacts.
A clean Path A guest from the golden image passed the registered patch,
inventory, API storage, and UI lanes. Its UI run wrote to a UI-created
filesystem, created zvols, selected `boot-pool` for a container, and submitted
a VM wizard with an existing boot-pool zvol. The same run found Session Timeout
under the signed-in account menu → Preferences, showing 300 seconds. The
follow-up inventory passed and found no test datasets, containers, or VM
records. See the [UI log](artifacts/live-ui-v27-path-a-clean-20261019.log) and
[post-run inventory](artifacts/live-inspect-v27-path-a-clean-20261019-post-ui.log).
The clean guest was then stopped through `tcg_vm.py`; its saved state remains
at `artifacts/v27-path-a-clean-20261019/vm-state` for a controlled follow-up.
Before any new mutation, inspect the selected managed guest and pass registered
read-only `live-inspect`; historical gate results remain evidence only for the
exact run that produced them.

## Current checkpoint: fresh v27 Path B UI cannot select the reserved partition

Fresh guest `truenas-v27-ui-b-fresh-r1-20261018` was restored from the golden
base and patched with the registered `live-v27-patch` lane before Path B
validation. The registered post-patch inventory passed. The guarded
`live-path-b-partition` lane then reserved `/dev/vda4`, preserving the boot
partition and creating no ZFS pool. The gate and inventory records are
[`live-v27-patch-path-b-fresh-r1-20261018.log`](artifacts/live-v27-patch-path-b-fresh-r1-20261018.log),
[`live-inspect-v27-path-b-fresh-r1-after-patch-before-partition-20261018.log`](artifacts/live-inspect-v27-path-b-fresh-r1-after-patch-before-partition-20261018.log),
[`live-path-b-partition-v27-path-b-fresh-r1-20261018.log`](artifacts/live-path-b-partition-v27-path-b-fresh-r1-20261018.log), and
[`live-inspect-v27-path-b-fresh-r1-after-partition-20261018.log`](artifacts/live-inspect-v27-path-b-fresh-r1-after-partition-20261018.log).

The fresh patched guest `truenas-v27-ui-b-fresh-r1-20261018` first passed the
guarded partition-only lane, which reserved `/dev/vda4` and created no pool.
The registered PWMCP UI retry after host recovery confirmed `lsblk` sees the
4 GiB partition, but middleware and browser `disk.query` list only `/dev/vda`
assigned to `boot-pool`. The Disks screen showed only the 20 GiB `vda` row,
with no partition mentions or partition-creation controls. The pool wizard
reported `Total Raw Capacity: 0 B` and `No disks available`; the gate stopped
before submitting `pool.create`. It did not select the whole boot disk.
Evidence:
[`live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log`](artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log).

The registered post-attempt inventory passed at
[`live-inspect-v27-path-b-r1-after-disks-screen-20261018.log`](artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).
It confirms `boot-pool` ONLINE, the patch installed, no `boot-disk-data` or
user-data root, and no probe datasets/snapshots, containers, or VMs. Path B
pool creation and creation of `/dev/vda4` through the UI remain open. Keep the
guarded partition-only lane first in the Path B baseline, then require the UI
pool wizard to create/register `boot-disk-data`; keep the separate UI
partition route as an explicit unanswered requirement. Do not select
`/dev/vda` or run `live-path-b-provision`.

After the latest UI helper change, the registered patch unit lane passed 149
tests + 45 subtests:
[`unit-boot-pool-patch-v27-ui-session-timeout-20261019.log`](artifacts/unit-boot-pool-patch-v27-ui-session-timeout-20261019.log).
The full unit lane last passed 362 tests + 45 subtests before this helper-only
change and was not rerun.

## 2026-10-18 auxiliary-vdev topology exploration

The registered `live-vdev-topology` lane creates the Path A root and distinct
128 MiB cache, log, and special zvols through PWMCP, then attempts through the TrueNAS API
to add those zvols to disposable single-disk and RAIDZ1 pools. The single-disk
target uses a 4 GiB sparse disk; the RAIDZ1 target uses three 1 GiB sparse
disks. Its RAIDZ1 special vdev is mirrored to satisfy topology policy, though
both zvol members share the same physical boot disk. If the API accepts the
role devices, the lane checks
`pool.query`, `zpool status`, pool health, per-device `zpool iostat` writes to
the intended log and special vdevs during a `sync=always` write/fsync probe,
and UI pool inventory before API cleanup of both pools and the generated UI
dataset. It also opens the pool wizard without submitting, requires all three
Cache/Log/Metadata selectors, and records whether those selectors expose the
zvols. It does not submit UI role assignments.

On fresh patched r1, the UI exposed all three role steps but none of the
boot-pool zvols appeared in its device list. `pool.update` requests assigning
cache, log, and special roles together were refused for both target pools with
`[EINVAL] ... disks were not found in system` for each `/dev/zvol/...` path.
Direct post-attempt `pool.query` and `zpool status -P` showed both pools still
ONLINE with empty auxiliary-role arrays and no errors. The gate exported both
temporary pools, removed its UI-created test tree, and passed as a diagnostic
safe-refusal check. It did not establish auxiliary-vdev support, and no
`zpool iostat` or metadata-allocation probe ran. Evidence:
[`live-vdev-topology-v27-path-a-r1-ui-role-steps-retry3-20261018.log`](artifacts/live-vdev-topology-v27-path-a-r1-ui-role-steps-retry3-20261018.log)
and registered post-run inventory
[`live-inspect-v27-topology-a-r1-after-role-ui-cleanup-20261018.log`](artifacts/live-inspect-v27-topology-a-r1-after-role-ui-cleanup-20261018.log).

The v27 source review explains the refusal boundary. `PoolService._process_topology()`
passes all vdev paths through `disk.check_disks_availability()`, and pool
create/update pass the returned device map to `pool.format_disks()` before ZFS
creates or extends the pool. An implementation must validate eligible zvols
as a separate device type, keep physical disk availability and formatting
limited to actual disks, reject same-pool source/target dependencies, and add
an explicit UI selector. Do not synthesize zvols as ordinary `disk.query`
records or bypass the middleware guard. See the release-pinned
[pool service source](https://github.com/truenas/middleware/blob/d83c3bf89aa4226c8fd1ef25dd273bdf447eb777/src/middlewared/middlewared/plugins/pool_/pool.py#L280-L335)
and [pool create path](https://github.com/truenas/middleware/blob/d83c3bf89aa4226c8fd1ef25dd273bdf447eb777/src/middlewared/middlewared/plugins/pool_/pool.py#L526-L552). The existing-pool formatting call is at
[pool.py lines 711–719](https://github.com/truenas/middleware/blob/d83c3bf89aa4226c8fd1ef25dd273bdf447eb777/src/middlewared/middlewared/plugins/pool_/pool.py#L711-L719).

The first managed VM did not boot TrueNAS:
extra PCI disks occupied addresses that shifted the boot disk away from its
saved UEFI address. The preserved serial log is
[`live-inspect-v27-topology-a-fresh-r1-uefi-device-order-failure-20261018.log`](artifacts/live-inspect-v27-topology-a-fresh-r1-uefi-device-order-failure-20261018.log).
The runner now pins extra disk PCI addresses above the boot disk and the argv
unit test covers that order. Fresh r2 confirmed the corrected PCI map, but its
firmware still chose the internal shell. Manually selecting the verified
`EFI/BOOT/BOOTX64.EFI` loader reached the kernel and middleware startup; r2 did
not pass registered prepatch inventory or patch gates. Use a fresh guest for
another topology run, install the relaxation patch through the registered
lane, and pass read-only inventory before API/UI role testing.

Sparse topology checks cannot establish device latency, endurance, power-loss
guarantees, import ordering, physical fault tolerance, or production capacity.
The lane only inspected the pool-creation wizard; it did not submit auxiliary
roles or test VDEV expansion on an existing pool.
Its Path A zvols depend on `boot-pool`; Path B use remains open until a fresh
Path B UI wizard can create and register `boot-disk-data`. See the
[design notes](docs/DESIGN-GUIDE.md#auxiliary-vdevs-on-boot-disk-storage) and
[consumer command](docs/CONSUMERS.md#test-auxiliary-vdev-roles).

The lane attempted distinct zvol block devices backed by one physical boot
disk for cache, log, and special roles on another pool. TrueNAS 27 middleware
rejected the zvol paths before ZFS attachment. It did not try one identical
device in multiple roles, a separate source pool on another disk, or raw
partitions as auxiliary vdevs. Those possibilities remain unproven at both the
middleware and OpenZFS layers.

Path B's latest registered UI run visited the Disks screen: it showed the
whole `vda` boot-pool disk and no partition controls, while the pool wizard's
`disk.query` still omitted `/dev/vda4`. UI creation of the partition and of
`boot-disk-data` remains open.

## Historical result: v27 Path B live storage passed on the shared Path A guest

The registered `live-storage` gate passed on `27.0.0-RC.1` with Path B.
The final gate log is
[`live-storage-v27-path-b-existing-container-protection-pass-20261017.log`](artifacts/live-storage-v27-path-b-existing-container-protection-pass-20261017.log).
It verified dataset and zvol operations, file-backed L2ARC add/remove, TCG VM
startup, refusal of public destroy and snapshot creation against an existing
middleware-managed container dataset with direct ZFS inventory confirming the
dataset survived, public clone refusal, persistent container data, NFS/SMB
share-record CRUD, and cleanup.

The v27 Path B partition/pool provisioner passed in
[`live-path-b-provision-v27-path-a-cow-20261017.log`](artifacts/live-path-b-provision-v27-path-a-cow-20261017.log).
Path B storage and provisioning used the same disposable COW guest as the Path
A run, `truenas-v27-path-a-20261016`, after provisioning its reserved tail.
Do not describe this as an isolated fresh Path B guest.

The final guest inspection is
[`guest-inspection-v27-path-b-after-protection-pass-20261017.log`](artifacts/guest-inspection-v27-path-b-after-protection-pass-20261017.log):
middleware was active, both pools were ONLINE, and there were no middleware VM
or container records, test datasets, or libvirt domains. The guest was stopped
cleanly with `tcg_vm.py`; its COW state remains at
`scripts/truenas/.vm/v27-path-a-20261016-r3`. The stop record is
[`guest-stop-v27-path-a-20261017.log`](artifacts/guest-stop-v27-path-a-20261017.log).

At that earlier checkpoint, the first two v27 Path B storage attempts reached VM startup and then failed on
a transient SSH DNS lookup before protection checks. A bounded retry for the
specific pre-command-start DNS error was added to `acceptance/remote.py`;
permanent hostname errors are still reported immediately. The registered unit
gate passed after this change: 106 tests and 45 subtests, logged at
[`unit-boot-pool-patch-v27-dns-retry-20261017.log`](artifacts/unit-boot-pool-patch-v27-dns-retry-20261017.log).
The failed DNS attempts, stale-tester refusal, and Docker start timeout remain
preserved as separate artifacts. No failed attempt is counted as protection
evidence.

## v27 Path A r4 UI validation passed

The managed guest `truenas-v27-ui-a-fresh-r4-20261017` (SSH 2223, HTTPS
8444) was created from the fresh golden base and patched before UI validation.
The registered PWMCP `live-ui` lane passed after resuming the UI-created
`boot-pool/user-data` root. It created a filesystem through the Datasets UI,
wrote 64 KiB and observed increased ZFS allocation; it created 128 MiB
`vm-disk` and `cache-device` zvols with 16K block size. The cache zvol passed
add/status/remove/health/export checks as an L2ARC vdev on a temporary
file-backed pool.

The UI selected `boot-pool` for a container and the direct ZFS inventory
matched its middleware-managed container dataset and storage root; that dataset
referenced 8,003,584 bytes. The VM wizard submitted a VM with the existing
`vm-disk` zvol, and the VM record referenced its `/dev/zvol/boot-pool/user-data`
device. The gate cleaned up its per-run container, VM, and test tree. SMB/NFS
sections were opened, but no share or client access was tested. The exact
registered UI evidence is
[`live-ui-v27-path-a-r4-resume-existing-user-dataset-vm-nic-retry-20261018.log`](artifacts/live-ui-v27-path-a-r4-resume-existing-user-dataset-vm-nic-retry-20261018.log).

The earlier quota refusal was traced to the scripted locator filling the
dataset-only **Quota for this dataset** field, which sends `refquota`. The
current scripted locator fills **Quota for this dataset and all children**
and checks its `data-test="input-quota"` binding. The captured browser RPC
payload sent integer `quota=2147483648`, and the UI-created root passed direct
quota, mountpoint `/mnt/boot-pool/user-data`, `canmount=on`, and live mount
source checks. That run later failed during VM-zvol creation, so it is evidence
for the quota/root workflow, not a full new `live-ui` pass. See
[`live-ui-v27-path-a-r4-root-created-later-vm-zvol-failure-20261018.log`](artifacts/live-ui-v27-path-a-r4-root-created-later-vm-zvol-failure-20261018.log).
The rejected `refquota` and earlier mountpoint attempts are also preserved in
the adjacent `live-ui-v27-path-a-r4-root-*-20261018.log` artifacts. The
middleware quota guard remains unchanged.

At the earlier host-resume checkpoint, the registered read-only inventory
passed at
[`live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log`](artifacts/live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log).
It confirmed the UI-created root had quota 2 GiB (`2147483648`), mountpoint
`/mnt/boot-pool/user-data`, `canmount=on`, and live mount source
`boot-pool/user-data`; the earlier user filesystem and both zvols remained;
`boot-pool` was ONLINE; and there were no container/VM records, libvirt
domains, or probe snapshots. Treat this as evidence for that checkpoint, not a
current storage check. The middleware Alpine image cache
retains
`boot-pool/user-data/.truenas_containers/images/alpine:3.24:amd64:default:20261016_13:00@image`;
the resume guard permits only that exact cache snapshot.
This confirms survival across the host interruption, not controlled reboot,
update, or automatic-remount acceptance.

The registered unit lanes also passed after the latest UI interaction fixes:
135 patch tests plus 45 subtests in
[`unit-boot-pool-patch-ui-vm-nic-option-20261018.log`](artifacts/unit-boot-pool-patch-ui-vm-nic-option-20261018.log)
and 334 full TrueNAS tests plus 45 subtests in
[`unit-truenas-ui-vm-nic-option-20261018.log`](artifacts/unit-truenas-ui-vm-nic-option-20261018.log).

The v28 Path B destroy/snapshot regression is resolved by a direct
before/after comparison in the registered storage gate: it verifies the
middleware-managed dataset exists before destroy, remains after the destroy
attempt, remains after the snapshot attempt, and that the attempted snapshot
does not exist. The Path B gate passed at
[`live-storage-v28-path-b-existing-container-protection-pass-20261017.log`](artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log).
The earlier JSON-null failure remains preserved. The current managed v28 Path B
guest is stopped with `oom_killed=false`; inspect its existing state and probe
data read-only before any future v28 mutation.

Do not repeat the Path B UI attempt on r1 until a supported UI workflow can
select the existing `/dev/vda4`; its middleware/browser disk query currently
returns only the whole `/dev/vda` boot disk. Preserve the r1 guest and its
partition state. If the UI or supported workflow changes, inspect r1 read-only
first, then resume on the same patched guest. Never use
`live-path-b-provision` for this validation.

Keep Path B reboot/import and update survival, controlled v27 reboot/update
survival and automatic remount through update, a real second-disk cache target,
share-client access, VM guest boot, and actual KVM selection open until their
own direct evidence passes.

The latest patch-unit and full-unit results before the zvol-form change were
123 tests plus 45 subtests and 321 tests plus 45 subtests, respectively. They
do not cover the new adapter. The prior passing logs are
[`unit-boot-pool-patch-v27-ui-zvol-rpc-response-fix-20261018.log`](artifacts/unit-boot-pool-patch-v27-ui-zvol-rpc-response-fix-20261018.log)
and
[`unit-truenas-v27-ui-zvol-rpc-response-fix-20261018.log`](artifacts/unit-truenas-v27-ui-zvol-rpc-response-fix-20261018.log).
Use the registered runners with `DOCKER_HOST=unix:///var/run/docker.sock`;
testers belong in `$CGROUP_PARENT_DEV_GATES` and guests in
`$CGROUP_PARENT_DEV_BACKGROUND`.

## Original v28 Path B failure and read-only inspection

The preserved post-run inspection below is historical, not a fresh inventory
of the current managed guest. Its latest `tcg_vm.py status` check showed
`truenas-v28-path-b-20261017` exited with `oom_killed=false`; a registered
read-only `live-inspect` is still required before any mutation. Resume only
with the exact identity in
[`artifacts/v28-path-b-20261017/runner.json`](artifacts/v28-path-b-20261017/runner.json)
and the managed `tcg_vm.py --resume` flow after host admission recovers.

The original v28 Path B `live-storage` gate returned JSON `null` from
`zfs.resource.destroy` after confirming the target dataset existed under
`boot-disk-data/.truenas_containers/containers/`. Its preserved failure log
is
[`live-storage-v28-path-b-protection-refusal-failure-20261017.log`](artifacts/live-storage-v28-path-b-protection-refusal-failure-20261017.log).

Before reusing the guest, the read-only inspection was saved at
[`guest-inspection-v28-path-b-20261017.log`](artifacts/guest-inspection-v28-path-b-20261017.log).
It found no middleware container or VM records, no libvirt domains, and no
test-specific dataset below `.truenas_containers/containers`; the image cache
and its managed roots remained, and both pools were ONLINE. Because the gate's
`finally` cleanup also ran, this post-run absence does not identify whether
the destroy call or cleanup removed the test dataset. The preserved failure
alone did not prove survival.

## Test matrix

| Target | Passed evidence | Still open |
|---|---|---|
| v27 Path A | Registered live storage refuses destroy and snapshot attempts against an existing middleware-managed dataset and verifies survival; dataset/zvol, cache-vdev add/remove, TCG VM, persistent container data, share-record CRUD, cleanup; Path A reboot remount passed. The clean registered PWMCP UI run created/wrote a filesystem, created zvols, selected `boot-pool` for a container, and submitted a VM wizard with an existing boot-pool zvol. It also found Session Timeout in the account menu → Preferences, set to 300 seconds. | Auxiliary-vdev acceptance, including selecting boot-pool zvols as cache/log/special devices through the UI/API; a real second-disk cache target, TrueNAS UI/middleware cache configuration, automatic remount after update, retention of pre-existing data/container/VM objects through v27→v28 update and v28 patch reapplication, client-side NFS/SMB access, VM guest boot, and actual KVM selection remain open. |
| v27 Path B | Registered partition-only lane reserved `/dev/vda4` on a fresh patched guest. After host recovery, the registered PWMCP lane reached the Disks screen and Pool Creation Wizard; `lsblk` sees `vda4`, but middleware and browser `disk.query` omit it. The Disks screen showed no partition controls, and the wizard reported no disks and submitted no pool creation. A post-attempt read-only inventory passed with `boot-pool` ONLINE and no probe/user data or `boot-disk-data`. | UI creation of `/dev/vda4` and `boot-disk-data`; fresh Path B UI storage checks; reboot/import and update survival; second-disk cache target; client-side shares, guest OS boot, and actual KVM selection remain open. |
| v27 auxiliary-vdev topology | Registered diagnostic created sparse single-disk and RAIDZ1 target pools and UI-created boot-pool zvols. The UI showed Cache, Log, and Special steps but no boot-pool zvols; middleware refused all tested zvol device paths before ZFS attachment. Direct checks found both target pools ONLINE with empty auxiliary-role arrays, and cleanup passed. | OpenZFS attachment acceptance, API/UI selection, each role independently, identical-device reuse, partitioned devices, separate source pool, Path B source pool, SLOG writes, metadata allocation, import ordering, and physical-disk tests. |
| v28 Path A | Guarded adapter install/replacement, live storage with existing-container destroy/snapshot refusal and survival, public clone refusal, and reboot/remount passed. The v27-to-v28 marker survived update, but data was mounted manually for verification. | Automatic remount through update; retention and continued operation of pre-existing container/VM records and data through update and v28 patch reapplication; full UI, client-side shares, guest OS boot, actual KVM selection, and second-disk cache target. |
| v28 Path B | Registered Path B partition/pool provisioner, guarded adapter install, and live storage with existing-container destroy/snapshot refusal and direct survival checks passed. | Reboot/import and update survival on Path B; second-disk cache target; full UI, client-side shares, guest OS boot, and actual KVM selection. |
| Separate-disk target pool using a system-disk zvol as L2ARC | Not tested. Existing cache checks use a temporary file-backed target pool. | Attach a real second virtual disk, create the target pool there, add/remove the Path A or B zvol as L2ARC, inspect `zpool status -P`, and test import/reboot ordering. |

All storage acceptance cache results so far are file-backed-target results. They
do not prove separate-disk import behavior, cache reliability or performance,
or UI/middleware configuration of L2ARC. Keep those claims open.

## Guard and regression status

The v28 failure came from the non-boot-pool protected-path policy omitting
`.truenas_containers`. The release adapter now uses the lightweight shared
path policy and protects middleware-managed container datasets on Path A and
Path B. The v27 adapter uses the same path-aware rule: ordinary descendants of
`boot-pool/user-data` are allowed while system paths and internal
`.truenas_containers` paths remain protected.

The registered storage gate creates a real middleware-managed container
dataset, confirms it exists with direct ZFS inventory, attempts public destroy,
checks that the dataset still exists, attempts public snapshot creation, then
checks that no snapshot appeared and the dataset still exists. Public clone
refusal is checked separately. v27 Path A/B and v28 Path A/B live gates now
pass these assertions.

## Next work

1. Add a migration-retention acceptance on a clean patched v27 Path A guest.
   Create a user dataset with a marker, a stopped container record with a
   persistent dataset mount, and a stopped VM record with an attached zvol.
   Update to v28, reapply the exact v28 patch, then verify the same records,
   device assignments, mountpoints, and marker hashes through API/UI. Start
   the container and VM after the patch and verify container data access and
   QEMU TCG startup. Keep guest-OS boot, share-client access, and automatic
   remount through update as separate checks.
2. Preserve the fresh patched Path B guest `truenas-v27-ui-b-fresh-r1-20261018`
   and its `/dev/vda4` partition. The latest registered UI run shows no
   partition controls on the Disks screen; the pool wizard still omits `vda4`
   and creates no pool. Investigate a supported UI route and preserve the
   baseline sequence: guarded partition-only lane first, then UI creation and
   registration of `boot-disk-data`. Never select `/dev/vda` or use
   `live-path-b-provision`.
3. Before any future v28 Path B mutation, resume the saved state only
   through `tcg_vm.py` and run registered read-only `live-inspect`. The
   existing v28 storage gate has direct pre/post ZFS checks for both destroy
   and snapshot refusals, but its current guest state still needs inspection.
4. Implement the narrowly allowlisted vdev-device path in middleware and the
   UI before another feature-acceptance run. On a fresh patched v27 guest,
   pass registered read-only inventory, then test cache, log, and special
   roles separately and in combinations through API and UI. Keep same-device
   reuse, non-overlapping partitions, a separate source pool, and Path B
   source-pool use open until directly tested. Keep the real second-disk cache
   target, reboot/import ordering, update survival, share-client access, VM
   guest boot, and actual KVM selection open until direct evidence closes
   them. Keep all logs and artifacts under this directory; no files were
   committed.

Read `TODO.md` for the chronological failure and repair record,
`docs/DESIGN-GUIDE.md` for rationale, and `docs/CONSUMERS.md` for supported
gate commands and worked examples.
