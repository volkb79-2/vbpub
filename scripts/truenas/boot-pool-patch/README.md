# TrueNAS boot-pool storage patch

This experimental patch work explores exposing carefully bounded boot-disk
space for user data, VM disks, and container storage while keeping TrueNAS's
own boot datasets protected. Path A creates a user-owned namespace below
`boot-pool`; Path B uses a separate ZFS pool on a partition reserved before the
boot-pool partition filled the disk. A release-specific TCG adapter also lets
TrueNAS advertise and start VMs under QEMU software emulation when KVM is not
available.

## Current status

| Release | Evidence | Open acceptance |
|---|---|---|
| 25.10.4 / 25.10.7 | The retained 25.10.4 patch proposal dry-runs on 25.10.7. | Its reported UI flow failed; PWMCP reproduction remains pending. It does not apply to v27+. |
| 27.0.0-RC.1 | Registered gates pass existing-container destroy/snapshot refusal and survival on Path A and B. Fresh Path A UI acceptance created/wrote a filesystem, created zvols, selected `boot-pool` for a container, and submitted a VM wizard using an existing boot-pool zvol. A separate UI workload gate started/stopped the container and blank VM; after a controlled v27 reboot it verified the root remounted, hashes persisted, the VM autostarted, and UI Start/Stop worked again. Session Timeout was visible in the signed-in account menu → Preferences (300 seconds). Path B reserved `/dev/vda4`, but middleware and the UI omit it; the pool wizard submitted no creation. The topology diagnostic showed Cache/Log/Special UI steps, omitted the boot-pool zvols, and safely refused their API paths before ZFS attachment. | Retention and natural post-update accessibility of these pre-existing Path A workloads through v27→v28 and v28 patch/reboot remain untested. The current container test does not prove a user filesystem mounted inside the container, and the VM has no guest OS. Path B UI partition/pool creation, accepted auxiliary-vdev roles through middleware/UI, existing-pool expansion, second-disk cache, share-client access, VM guest boot, and actual live KVM selection remain open. |
| 28.0.0-MASTER+20261014-230204 | Exact target checks and guarded replacement passed. Path A and Path B provision/storage gates refuse destroy/snapshot against existing managed container data and verify survival. Path B provision passed on a reserved tail; Path A reboot/remount passed. A marker survived the v27-to-v28 update after manual mounting. | Path B reboot/import, automatic remount and retention of pre-existing data/container/VM records through update and v28 patch reapplication, full UI, share-client access, guest OS boot, actual KVM selection, and a real second-disk cache/import-order check remain open. |

A read-only inventory after the original v28 Path B failure is saved at
[`guest-inspection-v28-path-b-20261017.log`](artifacts/guest-inspection-v28-path-b-20261017.log).
It found no test-specific container dataset, middleware VM or container
records, or libvirt domains; the managed roots and Alpine image cache remained,
and both pools were ONLINE. Since the gate's `finally` cleanup also ran, that
inventory does not distinguish the destroy call from cleanup as the cause of
the missing test dataset. Later Path A and Path B protection gates passed with
direct existence checks after destroy and snapshot refusal.

The latest registered Path B UI diagnostic is recorded at
[`live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log`](artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log): kernel `lsblk` sees the 4 GiB `/dev/vda4`, while middleware and browser `disk.query` return only `/dev/vda`, assigned to `boot-pool`. The Disks screen has no partition creation controls. The wizard reports zero raw capacity and no disks; it stops before submitting `pool.create`. A registered post-attempt inventory confirms no `boot-disk-data` pool or test data was created:
[`live-inspect-v27-path-b-r1-after-disks-screen-20261018.log`](artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).
Keep UI creation of both `/dev/vda4` and `boot-disk-data`, plus downstream
Path B storage acceptance, open. Do not select the whole boot disk or run the
CLI pool provisioner.

The required Path B baseline remains: run the guarded partition-only lane
first, then have the TrueNAS pool wizard create and register
`boot-disk-data`. The additional question of creating `/dev/vda4` itself in
the UI remains open. The registered Disks-screen probe found no partition
controls, and the wizard cannot currently see the partition.

Do not treat these adapters as ready for general installation. A successful
API operation does not prove that every UI path, reboot, update, share client,
or VM guest works. The v28 ISO does not identify its complete middleware Git
commit, so every adapter uses target fingerprints tied to the exact inspected
build. The 25.10.4 patch remains a historical proposal and is not compatible
with v27 or v28.

On the tested TrueNAS 27.0.0-RC.1 build, Session Timeout is visible under the
signed-in account menu → **Preferences**; the UI showed 300 seconds. The
setting is per-user and activity resets the idle timer. The official
Preferences guide gives a 300-second default and a 2,147,482-second maximum.
The Advanced Settings guide also documents **System → Advanced Settings →
Access → Configure**, but the tested UI exposed the preference directly. See
the official [Preferences guide](https://www.truenas.com/docs/scale/27/toptoolbar/settings/)
and [Advanced Settings guide](https://www.truenas.com/docs/scale/27/systemsettings/advanced/advancedsettings/).

### v27 Path A API and UI boundary

The current evidence does not show that users can do everything through the
UI. Registered API acceptance covers dataset create/query/update, zvol
create/grow, VM definition and zvol attachment/start under QEMU TCG, a
container with a persistent dataset mount and marker, NFS/SMB share-record
CRUD, and refusal of destructive operations against protected container data.
Cache-vdev add/status/remove was exercised with direct `zpool` commands on a
temporary file-backed target pool; middleware refused boot-pool zvol role
paths before attachment.

The registered UI lane created the Path A root and child filesystem, wrote
data to that filesystem, created VM/cache zvols, selected `boot-pool` for a
container and checked its ZFS backing, and submitted a VM using an existing
boot-pool zvol. The separate `live-ui-workloads` lane uses the UI to start and
stop preserved container and VM records and checks their backing data after a
reboot or update. It does not prove VM guest-OS boot. The UI lanes do not
attach a user dataset to a container, grow a zvol, create shares, or test
NFS/SMB clients. Auxiliary-vdev role selection is not accepted through the UI
or middleware. Path B still cannot be created through the current UI.
The latest clean run is
[`live-ui-v27-path-a-clean-20261019.log`](artifacts/live-ui-v27-path-a-clean-20261019.log);
see the [design guide](docs/DESIGN-GUIDE.md#pwmcp-storage-ui-acceptance) for
the acceptance boundary and [consumer guide](docs/CONSUMERS.md#run-pwmcp-ui-storage-acceptance)
for the `live-ui` and `live-ui-workloads` workflow.

## Project files

- `v27/` and `v28-nightly-20261014/` contain release adapters and fingerprints.
- `25.10.4/` preserves the earlier proposal and its source documents.
- `shared/` contains policy helpers installed by release adapters.
- [`provision_path_b.py`](provision_path_b.py) plans or creates a Path B
  partition and pool after strict layout checks; its registered destructive
  live lane passed on v27 and v28 disposable guests with reserved disk tails.
- `acceptance/` and `tests/` contain scripted gates and their artifact checks.
- The registered `live-ui` lane uses PWMCP, reads the HTTPS forwarding port
  from the managed guest, and verifies the selected pool's
  actual ZFS mount and allocation after UI actions. Its fresh v27 setup
  creates `boot-pool/user-data` through the UI. For Path B, the guarded
  partition-only lane reserves the tail and the TrueNAS UI wizard is required
  to create `boot-disk-data`; current fresh v27 evidence shows the wizard's
  `disk.query` omits the reserved `/dev/vda4`, so that setup is not yet
  validated. The gate does not substitute CLI pool creation.
  Path A requires the UI's **Quota for this dataset and all children** field;
  the neighboring dataset-only quota sets `refquota`, not the guarded `quota`
  property. The adapter pins the managed root's exact mountpoint. Path B's
  guarded partition-only lane reserves just the free-tail partition before the
  UI pool wizard uses it.
  The container form selects its pool in the UI, and the VM wizard selects an
  existing zvol created in the UI. Both resulting device/dataset paths are
  checked against direct ZFS inventory. See the
  [design guide](docs/DESIGN-GUIDE.md#pwmcp-storage-ui-acceptance) for scope and
  the [consumer guide](docs/CONSUMERS.md#run-pwmcp-ui-storage-acceptance) for
  the fresh guest sequence.
- `TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1` is an optional Path A `live-ui`
  recovery setting for a root created by an interrupted UI run. If that root
  contains a prior UI test filesystem, `TRUENAS_TEST_RESUME_EXISTING_UI_DATASET`
  must name its exact generated path; the gate then permits only that
  filesystem and its two known zvols after checking their direct ZFS
  properties. The middleware image cache is allowed only for its pinned image
  dataset and exact `@image` snapshot; other user descendants and snapshots
  still refuse. See the [design guide](docs/DESIGN-GUIDE.md#pwmcp-storage-ui-acceptance)
  for the narrow allowlist and the [consumer guide](docs/CONSUMERS.md#resume-a-partial-path-a-ui-run)
  for the r4 resume command.
- The registered `live-v27-patch` lane has guarded `repair-root-create`,
  `repair-root-parent-mountpoint`, and `repair-zvol-blocksize` actions. The
  one-time r4 zvol repair was bound to the old installed bundle and original
  `dataset_info.py` fingerprint; it preserved the root and placed source/marker
  backups under the persistent boot environment. It is installed on r4 and is
  not a general update path. The last successful r4 inventory found the
  UI-created user data and zvols, so the empty-root repair guard should refuse
  another application. Treat that inventory as historical; run the registered
  read-only inventory before any new mutation on a selected guest.
  See the [design guide](docs/DESIGN-GUIDE.md#pwmcp-storage-ui-acceptance)
  and [consumer status](docs/CONSUMERS.md#repair-the-r4-zvol-form-default).
- The r4 PWMCP UI lane passed after resuming the guest. It
  verified UI-created user data and zvols, container storage selection and
  direct ZFS backing, VM wizard submission with a boot-pool zvol, and
  cache-zvol use on a temporary file-backed pool. It cleaned its per-run test
  tree. A successful registered inventory at a later checkpoint found the
  prior filesystem and zvols; treat it as historical, not a current state
  check. SMB/NFS pages were opened, but no share was created and no client was
  tested. See the
  [UI run](artifacts/live-ui-v27-path-a-r4-resume-existing-user-dataset-vm-nic-retry-20261018.log),
  [successful historical inventory](artifacts/live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log),
  and [handoff](HANDOFF.md).
- The registered `live-inspect` lane records guest datasets, snapshots,
  container and VM records, and pool health without mutation.
- The registered `live-vdev-topology` lane is an exploratory API/UI diagnostic.
  It created the bounded Path A root and 128 MiB zvols through PWMCP, then
  created sparse single-disk and RAIDZ1 target pools. The UI showed Cache,
  Log, and Special steps but omitted boot-pool zvols. Middleware refused
  `pool.update` for those `/dev/zvol/...` paths with “disks were not found in
  system,” before ZFS attachment. Direct checks found both target pools
  ONLINE with empty auxiliary-role arrays; cleanup passed. This is safe-refusal
  evidence, not feature acceptance. UI role submission, accepted middleware
  paths, same-device reuse, partitions, Path B, and a separate source pool
  remain open. A mirrored special vdev backed by two zvols on one boot disk
  would still share one physical failure domain. See the
  [design rationale](docs/DESIGN-GUIDE.md#auxiliary-vdevs-on-boot-disk-storage),
  [consumer command](docs/CONSUMERS.md#test-auxiliary-vdev-roles), and current
  [handoff](HANDOFF.md).
- `run-live-gate.py --recover-stale-tester` recovers an interrupted gate only
  when the existing tester is confirmed exited, uses `tester-unified:local`,
  and carries a valid live-runner ownership label. It pins cleanup to the
  inspected container ID and refuses running, foreign, or indeterminate
  containers. See the [design guide](docs/DESIGN-GUIDE.md#recover-an-interrupted-live-tester)
  and [consumer steps](docs/CONSUMERS.md#recover-an-interrupted-live-tester).
- The live remote helper retries at most twice for known SSH failures before
  command startup, including a key-exchange reset; middleware or shell command
  failures are returned without retrying.
- Managed guests resume from their saved `tcg_vm.py` state after a host
  restart. An OVMF source device-number change is accepted only when its path,
  inode, size, modification time, and SHA-256 still match; see the parent
  [runner design](../docs/DESIGN-GUIDE.md#isolated-local-qemu-guests).
- Run the registered `unit-boot-pool-patch` lane for patch policy, adapter,
  and live-gate contract tests before live acceptance.
- `artifacts/` is the ignored home for live logs and test credentials.
- [`run-live-gate.py`](run-live-gate.py) places a bounded acceptance tester on
  the selected disposable guest's Docker network.
- [`HANDOFF.md`](HANDOFF.md) is the current release/path test matrix and
  resume checklist for ongoing work.

Read the [design guide](docs/DESIGN-GUIDE.md) for source changes, Path A/B
tradeoffs, release differences, and evidence details. Follow the
[consumer guide](docs/CONSUMERS.md) for registered local live gates and their
guards. Shared TCG guest setup lives in
[`../docs/CONSUMERS.md`](../docs/CONSUMERS.md#run-a-local-truenas-tcg-guest).
