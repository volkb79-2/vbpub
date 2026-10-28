# Boot-pool patch design and evidence

**Status: experimental. No v27+ patch is ready for general installation.**

This work investigates using the unused space on a TrueNAS boot disk for
VM/container storage and user data, and allowing VMs to run with QEMU TCG when
KVM is unavailable. The existing [25.10.4 patch](../25.10.4/) is a proposal, not a
working implementation. Its reported UI workflow failed and remains pending
PWMCP-assisted reproduction.

## Test gates

The registered `unit-boot-pool-patch` lane runs the patch's policy, artifact,
and live-gate contract tests in `tester-unified`, without starting a TrueNAS
guest. Runtime acceptance stays in separate guarded lanes because it needs a
disposable guest and may create storage, start VMs or containers, or reboot.
Use the installed Run-Gate CLI for every verdict so the declared lane,
resource limits, environment, and recorded result remain authoritative. The
small `run-live-gate.py` helper only provisions the network-attached tester
that the ordinary ephemeral environment cannot provide; it then delegates
back to Run-Gate. This keeps quick source-contract feedback distinct from
evidence that an adapter works against one exact TrueNAS build.

External-host mode exists for a disposable physical or provider VM when local
TCG admission is unavailable. It requires an explicit `--docker-network`, a
verified custom bridge, pinned SSH host keys, and an HTTPS UI origin. It only
allows read-only inventory, the v27 patch, guarded Path B partition/pool setup,
the read-only `live-ui-auth` gate, the guarded `live-ui-prepare-ssh` lane, and
the existing UI acceptance lane. SSH preparation adds the supplied public key
to TrueNAS's built-in root user, verifies that root password login and global
SSH password authentication are disabled, then starts the service. It keeps
other root authorized keys and preserves the automatic-start setting when
already on. Following SSH lanes can then exercise the configured key-only
account without enabling password logins.
Reboot/update and the wider workload and vdev lanes remain managed-guest only
because the wrapper has no provider lifecycle or rollback contract. The
networked tester is still resource-capped in the gates slice; those limits do
not constrain the external host. The explicit `--allow-high-psi` flag is
restricted to external targets and one invocation. It skips only `some/full`
PSI thresholds; readable PSI, finite memory limits, current usage below every
hard limit, CPU quota, and the 0.5 CPU / 512 MiB tester caps remain required.

The `live-ui-observed-v1001` lane is a narrow exception for UI exploration on
v1001's already-customized v27 RC.1 installation. The normal `live-v27-patch`
lane correctly refuses it because 14 of 15 current adapter targets do not
match the release fingerprints, and no install manifest identifies the custom
state. Rather than overwrite unknown code, the exploratory lane pins the exact
hashes of all 15 adapter targets plus the two matching custom modules, checks
that the patch-install marker is absent, and then runs the existing bounded
PWMCP UI workflow. If the Path A root is absent, a second explicit guard lets
the lane create only the empty `boot-pool/user-data` root with a 2 GiB quota,
fixed mountpoint, and `canmount=on` through ZFS. It verifies the mount and
empty tree before the UI workflow. This setup is required because the observed
middleware refuses root creation through dataset CRUD; it means this lane does
not test UI creation of the root. Any changed source hash stops the lane
before storage mutations.
This provides an evidence path when a disposable host is available but no
local TCG guest can be admitted. It does not validate the adapter bundle,
attribute the custom files to the golden image, or generalize beyond this
specific v1001 fingerprint. On the 2026-10-21 live run, the root passed
verification but the first child dataset create failed with
`NameError: name 'mountpoint' is not defined` in middleware at
`pool_/dataset.py:117`. No child dataset was created, and the remaining UI
workflow did not run. This is a failed exploratory acceptance and a middleware
defect to diagnose, not evidence for the release adapter. The pre-patch
provider snapshot was restored afterward; v1001 is shut off. The lane does not
start a VM guest or test provider reinstall behavior.

## Source comparison

| Image/release | Middleware source used | Finding |
|---|---|---|
| 25.10.4 | `e36f4f8318` | Baseline for the retained patch. |
| 25.10.7 | `8ede398839` | All seven files touched by the 25.10.4 patch match the 25.10.4 versions; the patch dry-run applies. |
| 27.0.0-RC.1 | tag `TS-27.0.0-RC.1`, commit `d83c3bf89aa4226c8fd1ef25dd273bdf447eb777` | The old patch fails on changed Docker hunks, and `virt/global.py` no longer exists. v27 uses separate `vm.*` and `container.*` middleware. |
| 28 nightly, `28.0.0-MASTER+20261014-230204` | candidate middleware source commit `95807c2309548ec72fc09742f323aadec7f6532e` | The old patch fails. Several Docker modules changed substantially; `docker/update.py` and `virt/global.py` are absent. All three v28 TCG adapter targets match the extracted ISO payload. |

The v28 ISO's SHA-256 is
`9312f77d0a94f54dc0396050bd26bb31ea0e81d62b124e30a94075a006e7bc9a`. Its
embedded `TrueNAS.update` manifest confirms the exact version, build time
(`2026-10-15T06:04:44Z`), kernel (`6.18.55-production+truenas`), and rootfs
checksum. Neither that manifest nor the ISO contains a middleware Git commit
or `GITMANIFEST`. Therefore `95807c2…` is a candidate source snapshot, not a
verified full ISO-to-source mapping. Eight middleware modules extracted from
the nightly rootfs are byte-identical to the candidate snapshot:
`container/info.py`, `pool_/dataset.py`, `pool_/dataset_query_utils.py`,
`vm/__init__.py`, `vm/crud.py`, `vm/info.py`, `zfs/utils.py`, and
`zfs_/utils.py`. All three files targeted by the v28 TCG adapter match the ISO
payload exactly, and the patch dry-run, application, and syntax check pass
against those extracted files. This verifies the inspected code paths and
patch targets, but does not identify the full middleware checkout or every
packaged module. Continue to fingerprint patch targets and fail closed on an
unknown source shape.

The patch dry-run was performed against source downloaded from the commits
above, outside the repository. It passed for both 25.10.4 and 25.10.7. It
failed for v27 and v28. Do not use the 25.10.4 patch on either release.

## What v27+ protects

The 27 RC source has several distinct protections, so permitting one API call
does not make boot-pool storage generally usable:

- `pool.dataset.query` excludes boot-pool and other internal datasets by
  default. It has an `exclude_internal_datasets` query option, but turning that
  off exposes system datasets too.
- Dataset create/update validation treats boot-pool paths as internal.
- `zfs.resource.create`, destroy, and snapshot creation use the
  `has_internal_path` guard. Public clone validation also protects internal
  destinations.
- VM disk validation rejects zvols under an internal path.
- `container.pool_choices` filters boot-pool out before container creation.
- The v27 Web UI hides the VM page when `vm.virtualization_details` reports
  virtualization unsupported.

These checks are separate layers. Turning off the dataset query's internal
filter would expose the boot pool's `ROOT`, `.system`, `grub`, and other
service datasets. The shared component-aware policy permits ordinary user
descendants below `boot-pool/user-data` while continuing to protect the root
and internal paths such as `.truenas_containers`. It applies to both Path A
and the separate Path B pool. Query visibility and container pool choices
remain separate release-specific integrations around the same policy.

The live regression creates a real middleware-managed container dataset,
confirms its existence with direct ZFS inventory, calls public destroy and
snapshot creation, checks that the dataset survived and no snapshot appeared,
then separately verifies public clone refusal. The registered Path A and Path
B storage gates pass this sequence on v27 and v28. The original v28 Path B
failure is preserved as a failure artifact; its post-run read-only inventory
alone could not distinguish the destroy call from the gate's `finally`
cleanup as the cause of the missing test dataset. The later v28 Path B
regression gate directly verified dataset survival after both destroy and
snapshot attempts and verified that the attempted snapshot did not appear:
[v28 Path B protection gate](../artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log).

The v28 nightly retains these broad boot-pool protections. It also reorganizes
the container service: `container.pool_choices` moved from
`container/container.py` to `container/info.py`, while Docker's dataset
configuration changed shape. The patch must be adapted to each release's
current middleware and UI call paths, not applied by relaxing a single shared
filter.

## Candidate storage designs

### A. Let users create data below boot-pool

This requires a narrow, path-component-aware namespace such as
`boot-pool/user-data/`. The query, dataset create/update/delete, zvol validation,
VM disk validation, and UI pickers must allow that namespace while keeping
`boot-pool/.system`, `boot-pool/ROOT`, `boot-pool/grub`, and service-managed
datasets protected. In the v27 test guest, `boot-pool` has
`mountpoint=none,canmount=off`; a directly created child also inherited
`mountpoint=none`. The v27 dataset UI exposes a quota control but not a
mountpoint control. Its patch adapter therefore accepts creation of the exact
`boot-pool/user-data` filesystem root only when the UI supplies an explicit
quota of at least 1 GiB, then pins `mountpoint=/mnt/boot-pool/user-data` and
`canmount=on`. Root updates and direct `zfs.resource.create` remain refused;
ordinary descendants are available only after the root is present and
mounted with those properties. The v27 API acceptance passed dataset and zvol
operations, VM disk selection and TCG VM process start, container lifecycle
with persistent dataset storage, and share-record CRUD. PWMCP selected
`boot-pool/user-data`, created and queried a dataset there, and confirmed
protected system datasets remained hidden. Upgrade survival and guest OS boot
remain untested. Broadly removing boot-pool filters remains unsafe and would
not satisfy the user-data boundary.

### B. Create a separate pool on a new partition

A pool such as `boot-disk-data` would use the ordinary data-pool middleware and
UI paths, avoiding most boot-pool exceptions. This currently needs a dedicated
provisioning step: the v27 `pool.create` workflow formats selected whole disks,
so it cannot create a pool from a new partition on the already-used boot disk.
The candidate path is to validate and create a free GPT partition, create a ZFS
pool on that partition, then register/import it with `pool.import_find` and
`pool.import_pool`. A local 27.0.0-RC.1 QEMU test has now confirmed this path
through middleware registration, filesystem dataset creation, sparse zvol
creation, and the normal filesystem, volume, and container pool choice APIs.
The test used a 20 GiB target whose boot-pool partition ended just below the
16 GiB image boundary, leaving about 4 GiB unallocated; it created the new
partition in that tail. It left the existing boot-pool partition extents
unchanged. After a reboot, `boot-disk-data` came back ONLINE and a marker file
remained present. This matches a golden image restored onto a larger disk
without expanding partition 3. A stock install that already gave partition 3
the whole disk has no such tail; Path B must reserve space during image or
install preparation, or it must use the separate boot-pool namespace approach
instead.
The same guest used a test-only v27 TCG patch to create a VM definition, attach
the zvol, and start a QEMU process. Filesystem dataset create/query/update/
delete and sparse zvol create/query/grow/delete all passed through middleware.
Container create/start/stop/delete also passed through middleware on this
pool. A persistent user dataset mounted into an Alpine container at `/data`
accepted a marker written inside the container and returned the same marker
from the host dataset path. NFS and SMB share create/query/delete also passed
through their APIs. These share checks did not mount the exports from a client
or verify protocol access. In this historical run, where `boot-disk-data` had
already been created outside the UI wizard, PWMCP selected its dataset tree,
created and queried a user dataset, checked the container pool selector,
opened the VM wizard through its CPU/memory step, and confirmed the SMB/NFS
sections and Add actions. It did not submit the VM wizard or create a share
through the UI. Upgrade survival and guest OS boot remain untested, so this is
still a proof of concept rather than an install procedure.
The manual experiment is now represented by
[`provision_path_b.py`](../provision_path_b.py). It requires an exact disk and
boot-pool partition, checks the whole-disk/GPT/ZFS-vdev relationship, accepts
only partitions 1–3 with the boot pool on partition 3, and requires the largest
aligned free extent to be a tail after partition 3. It backs up the GPT,
creates only partition 4, verifies partitions 1–3 did not change, creates the
pool without `zpool -f`, then uses `pool.import_find` and `pool.import_pool` to
register it. A repeated successful run is a no-op. The registered destructive
lane passed on v27 and v28 disposable guests with reserved disk tails. The
v27 Path B provision and storage gates ran on the same COW guest after Path A;
that evidence is not a fresh isolated Path B guest.

The tool intentionally does not delete a new partition or destroy a pool when
a later step fails. Those actions can destroy data if the observed state is
not the state the tool created. It retains the GPT backup and reports the
partial state for inspection. Partition 4 already present without one healthy,
middleware-registered `boot-disk-data` pool is a refusal, not an automatic
resume or cleanup.

The separate-pool design currently looks less invasive for VM disks, container
storage, and user datasets when unallocated disk space was reserved before
partition 3 filled the disk. It does not let the same partition serve as both
a data pool and a cache vdev.

### Cache vdevs

OpenZFS L2ARC is an optional read-cache vdev attached to one pool. It does not
cache writes, may reduce performance on RAM-constrained systems, and is useful
only for some read-heavy workloads. A partition assigned as a pool's data vdev
cannot also be a separate cache vdev. A zvol stored in that pool is a distinct
block device and can be used as a candidate cache vdev for another pool.

The existing `live_storage.py` acceptance creates a sparse zvol under the
selected Path A or Path B user-data pool, then uses `zpool add ... cache` to
attach it to a temporary file-backed pool. It verifies the vdev appears in
`zpool status` and removes it again. This passed on v27 and v28 for both
paths. The
lane also accepts `TRUENAS_TEST_CACHE_TARGET` to use a different already
imported target pool, but that separate-disk case has not yet been run. The
test currently uses the OpenZFS CLI directly; it does not verify TrueNAS UI or
middleware management of cache vdevs.

Still untested are a target pool backed by a second disk, cache and pool import
ordering after reboot, behavior if the boot-disk pool that owns the zvol is not
available, and whether L2ARC improves a representative workload. These matter
because a cache zvol on the system disk depends on that pool being imported and
the zvol device being present. L2ARC contents are a disposable read cache, not
the target pool's user data.

See the [OpenZFS caching guide](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Pool%20Structure/Caching.html)
and [zvol guide](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Datasets/ZVOLs.html).
The inference that a zvol can back another pool's cache device is consistent
with the [OpenZFS Linux zvol implementation](https://github.com/openzfs/zfs/blob/master/module/os/linux/zfs/zvol_os.c),
which includes locking for zvols used as vdevs by another pool. The exact
TrueNAS UI/API flow and real disk import ordering are still untested.

### Auxiliary vdevs on boot-disk storage

The registered `live-vdev-topology` lane explores Path A zvols as the three
auxiliary roles of a separate pool: L2ARC cache, ZIL/SLOG log, and the special
metadata allocation class. OpenZFS defines a zvol as a block device that can
be consumed by a separate pool ([zvol guide](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Datasets/ZVOLs.html));
that does not establish that TrueNAS middleware or its UI accepts a zvol as a
pool-vdev input.

The registered v27 Path A diagnostic used seven 128 MiB zvols created through
PWMCP and two disposable target pools: a 4 GiB sparse single-disk pool and a
three-disk RAIDZ1 pool with 1 GiB per disk. The RAIDZ1 special-vdev layout was
specified as a mirror of two zvols to satisfy the logical redundancy policy.
The UI showed Cache, Log, and Special wizard steps but did not list the
boot-pool zvols. API `pool.update` requests to assign the three roles were
refused for both target pools with `[EINVAL] ... disks were not found in
system` for `/dev/zvol/...` paths. This refusal occurred before ZFS attachment.
Direct `pool.query` and `zpool status -P` checks showed both targets ONLINE
with empty auxiliary-role arrays and no errors. The registered diagnostic
passed its safe-refusal and cleanup checks; it did not pass auxiliary-vdev
feature acceptance. No SLOG-write or metadata-allocation probe ran. Evidence:
the [topology run](../artifacts/live-vdev-topology-v27-path-a-r1-ui-role-steps-retry3-20261018.log)
and [post-run inventory](../artifacts/live-inspect-v27-topology-a-r1-after-role-ui-cleanup-20261018.log).

This evidence says TrueNAS 27's current disk inventory path rejects the tested
zvol device paths. It does not prove OpenZFS itself rejects them. The UI wizard
has the documented Cache, Log, and Metadata steps, but its selectors omitted
the zvols; no UI role assignment was submitted, and the existing-pool VDEV
expansion screen was not exercised. The inspected v27 `PoolService._process_topology()` sends every topology
path to `disk.check_disks_availability()`, then pool create/update pass the
returned disk map to `pool.format_disks()` before ZFS creates or extends the
pool. A safe patch therefore needs a type-aware auxiliary-device path: verify
an exact existing zvol in the managed user namespace, verify its source pool
is not the target pool, preserve normal physical-disk availability checks, and
exclude zvols from physical disk formatting. It must also expose those devices
to a dedicated UI selector; synthesizing zvols as ordinary `disk.query` rows
would misrepresent them to other disk consumers. See the release-pinned
[pool service source](https://github.com/truenas/middleware/blob/d83c3bf89aa4226c8fd1ef25dd273bdf447eb777/src/middlewared/middlewared/plugins/pool_/pool.py#L280-L335)
and its [pool create call](https://github.com/truenas/middleware/blob/d83c3bf89aa4226c8fd1ef25dd273bdf447eb777/src/middlewared/middlewared/plugins/pool_/pool.py#L526-L552). The [existing-pool update formatting path](https://github.com/truenas/middleware/blob/d83c3bf89aa4226c8fd1ef25dd273bdf447eb777/src/middlewared/middlewared/plugins/pool_/pool.py#L711-L719) must also keep zvols out of physical disk formatting.
Do not bypass that inventory/refusal guard by calling raw `zpool` commands.

The test used distinct zvol block devices. It did not test reusing one
identical zvol for cache, log, and special roles, nor API acceptance of each
role independently. Multiple zvols or partitions on one source disk may be
separate block devices, but they remain in one physical failure domain. Two
special-vdev zvols on the same boot-pool disk do not protect against loss of
that disk. OpenZFS requires special-vdev redundancy to be at least as strong
as the data vdev; special storage holds critical pool data and its loss can
make the pool inaccessible. A special vdev on a RAIDZ pool is not removable
as a whole. Cache is optional read state; SLOG is for synchronous-write
durability, not a general write cache. See the OpenZFS [special-vdev guide](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Pool%20Structure/Special%20vdev.html)
and [caching guide](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Pool%20Structure/Caching.html).

A zvol can only supply a separate target pool while its source pool is
imported. Using a zvol as a vdev of the same pool that must supply its blocks
would make the vdev depend on the pool it is modifying. A separate source pool
on another disk, Path B `boot-disk-data`, partitions from the boot disk, and
partitions from disks already used by the target pool all remain untested.
Import order and behavior when the source pool is unavailable are also open.
OpenZFS supports partition devices as vdev inputs in general, but TrueNAS UI
and middleware eligibility for these specific layouts has not been established
([VDEV guide](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Pool%20Structure/VDEVs.html)).

Small sparse disks were sufficient to create the disposable target pools and
exercise schema, UI visibility, API refusal, pool-health inspection, and
cleanup. They do not establish accepted auxiliary topology, write behavior,
device latency, endurance, power-loss protection, physical fault tolerance,
cache effectiveness, or production capacity. A future accepted RAIDZ1 special
vdev requires removal by destroying its disposable target pool.

Path B's required baseline still runs the guarded partition-only lane first,
then uses the TrueNAS UI wizard to create and register `boot-disk-data`. On the
fresh patched v27 guest, `lsblk` saw the 4 GiB `/dev/vda4`, but middleware and
browser `disk.query` returned only `/dev/vda`. The Disks screen showed only
the whole boot-pool disk and no partition controls; the pool wizard reported
zero raw capacity and no disks, then stopped before `pool.create`. A registered
post-run inventory confirmed no pool or probe data was created. UI creation of
`/dev/vda4` itself and UI creation of `boot-disk-data` remain open. See the
[UI run](../artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log)
and [post-run inventory](../artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).

The topology lane is currently Path A only because it requires a UI-created
`boot-pool/user-data` root and zvols. Do not claim Path B role testing until
the UI creates/registers `boot-disk-data` on a fresh patched guest. The
consumer guide records the current lane evidence and the required fresh-guest
sequence.

## TCG VM path

In both the v27 RC and the v28 nightly source, the Web UI hides the VM page
based on `vm.virtualization_details.supported`; making that capability report
software emulation available exposes the existing page, so a UI source change
is not currently indicated just to make VMs selectable. PWMCP acceptance
confirmed the VM route and wizard through the CPU/memory step, but did not
submit it; TCG messaging remains open. The middleware create gate differs by
release: v27 calls `vm.supports_virtualization`, while the
inspected v28 nightly checks `kvm_supported()` directly in `vm/crud.py`. A
shared patch therefore needs small release-specific adapters for both the
capability report and create validation.

The v28 ISO's packaged `truenas_pylibvirt` KVM-detection and VM-domain-type
files are byte-identical to upstream commit
`ebd20df3cc2ae15fe08e7943478f54175d172382`: KVM detection is based on
`/dev/kvm`, and VM XML selects the `qemu` domain type when that device is
absent. The same ISO rootfs contains `/usr/bin/qemu-system-x86_64` and
`/usr/bin/qemu-system-aarch64`, so the helper's executable check can succeed
for these architectures. The installed v27 RC has the same `/dev/kvm` KVM
check, but its VM XML generator hardcodes `domain type="kvm"`; v27 therefore needs a backend XML
change in addition to the middleware gates. The installed v27 QEMU binary is
present. A test patch has been applied to the disposable v27 guest. With
`/dev/kvm` absent, middleware reported support, kept hardware CPU flags false,
and accepted a VM backed by the new pool's zvol. The QEMU domain started and
reported `RUNNING` with `-accel tcg`, then was shut down. `HOST-MODEL` and
`CUSTOM` with `cpu_model: null` also reached `RUNNING` through API payloads,
then were deleted. Filesystem dataset
create/query/update/delete and sparse zvol create/query/grow/delete were
verified through middleware. These checks prove backend process startup and
storage API operations, not a guest OS boot. The v28 middleware adapter is a
separate candidate patch in [`v28-nightly-20261014/`](../v28-nightly-20261014/);
its source-to-ISO mapping and live checks remain pending. The v27 API tests
also covered container lifecycle and a user dataset mounted inside a
container, plus NFS/SMB share-record CRUD. They did not verify NFS/SMB client
access. Keep KVM selection when available, actual guest boot, protocol-level
sharing, full submitted VM/share UI flows, and upgrade survival as open tests.
TCG should be visibly marked as software emulated because it can be very slow.

## PWMCP storage UI acceptance

The registered `live-ui` lane is built around fresh, unconfigured v27 guests
after the relaxation patch is installed. On Path A, it opens the `boot-pool`
Datasets page, creates `user-data`, and sets **Quota for this dataset and all
children** to 2 GiB. TrueNAS exposes a similarly named **Quota for this
dataset** field, but that UI control submits ZFS `refquota`; the namespace
guard requires ZFS `quota`, which limits the root and its descendants. The
script checks the UI's outgoing `pool.dataset.create` request for an explicit
finite `quota` before it accepts the root. The adapter requires at least 1 GiB
and assigns the root's fixed mountpoint and `canmount=on`, because this UI form
has no mountpoint control. The gate verifies those properties and the live
mount source. Root creation skips the descendant readiness check because that
check queries an existing root; applying it to the root being created would
make UI provisioning refuse itself. Descendant operations still require the
existing root to pass the quota, mountpoint, and canmount checks. Path B has
two explicit setup modes. `ui-wizard` reserves the free tail with the guarded
partition-only lane and then asks the TrueNAS wizard to create
`boot-disk-data`; this tests the full UI pool-creation route. `preprovisioned`
uses the registered Path B provisioner to create and register the pool before
the browser tests dataset, zvol, container, and VM operations. The latter
isolates downstream UI coverage from the wizard's missing partition
inventory; it does not prove UI pool creation. Both modes require an explicit
`TRUENAS_PATH_B_SETUP` value and refuse selection of the whole boot disk. The
preprovisioned path passed its registered unit gate on 2026-10-21 (170 tests
and 45 subtests; see the
[unit log](../artifacts/unit-boot-pool-patch-path-b-ui-preprovision-mode-20261021.log)).
The browser observer rejects any `pool.create` request in preprovisioned mode,
including a request for a pool other than `boot-disk-data`, and checks again
after the remaining UI workflow. Fresh live UI acceptance remains open.

The fresh patched Path B attempt on 2026-10-18 did not meet that UI
precondition. Kernel `lsblk` showed `/dev/vda4` as a 4 GiB ZFS partition, but
middleware `disk.query` and the browser wizard's own `disk.query` response
listed only `/dev/vda`, already assigned to `boot-pool`. The registered Disks
screen showed no partition controls; the wizard displayed zero raw capacity
and no available disks, and the gate stopped before pool creation. A
post-attempt inventory passed and confirmed no test pool or probe data. See
the [latest UI log](../artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log)
and [read-only inventory](../artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).
This is a safe refusal for the `ui-wizard` mode, not a test of downstream
Path B storage forms. Keep UI creation of `/dev/vda4` and `boot-disk-data`
open. The new `preprovisioned` mode can exercise downstream UI operations
after the registered provisioner creates the exact pool. Its registered unit
gate passed on 2026-10-21; fresh live acceptance remains open. Do not select the whole disk;
do not present the preprovisioner as UI pool-creation evidence.

On fresh patched r4, the registered UI created the root with its 2 GiB
`quota`, pinned mountpoint, `canmount=on`, and live ZFS mount source. It also
created a user filesystem, wrote 64 KiB, and observed increased ZFS allocation.
The UI created 128 MiB `vm-disk` and `cache-device` zvols with 16K block size.
The cache zvol passed add/status/remove/health/export checks as an L2ARC vdev
on a temporary file-backed pool. The narrow root-create fixes exempt only the
exact root from descendant readiness and skip only the parent's literal
`mountpoint=none` stat. The quota/type checks, fixed mountpoint, `canmount=on`,
root update refusal, and descendant readiness remain active. The separate
16K recommendation hunk handles only exact `boot-pool` requests before v27's
hidden `pool.query`; ordinary pool topology recommendations stay on the
original path.
The registered patch lane retains `repair-root-create` and
`repair-root-parent-mountpoint` as one-time fixes for the earlier empty-root
failures, plus the digest-bound `repair-zvol-blocksize` action for the r4 UI
root. These actions are scoped recovery paths, not general update mechanisms.

The registered PWMCP UI run passed: it wrote to a UI-created user filesystem,
created the VM/cache zvols, selected `boot-pool` for a container, verified its
direct ZFS backing, and submitted the VM wizard with the existing boot-pool
zvol. The gate removed its per-run container, VM, and test tree. A later
registered read-only inventory confirmed the UI filesystem and zvols, root
quota, pinned mountpoint, `canmount=on`, and ONLINE `boot-pool`. The middleware
Alpine image cache retains
`boot-pool/user-data/.truenas_containers/images/alpine:3.24:amd64:default:20261016_13:00@image`;
the resume guard admits only that image-cache snapshot. Evidence: the [passed UI run](../artifacts/live-ui-v27-path-a-r4-resume-existing-user-dataset-vm-nic-retry-20261018.log),
[successful historical inventory](../artifacts/live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log)
and earlier [interrupted run](../artifacts/live-ui-v27-path-a-r4-host-reboot-aborted-20261018.log).
This records survival at that checkpoint; retention through update and
automatic remount after update remain untested.

An interrupted UI run may already have left test-owned storage below the
otherwise valid root. `TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1` admits the
verified Path A root after checking quota and mount policy. When the root also
contains a previous UI test dataset, the operator must provide its exact
generated path as `TRUENAS_TEST_RESUME_EXISTING_UI_DATASET`. The preflight
allows only that filesystem and its `vm-disk` and `cache-device` zvols, checks
their types, sizes, block size, mountpoint, and mount source, and rejects
unlisted user datasets and writable container descendants. It also allows only
the pinned middleware image-cache datasets and their exact `@image` snapshot;
other snapshots are refused. These settings are valid only for Path A and do
not create or delete storage. Path B setup is selected separately with
`TRUENAS_PATH_B_SETUP=ui-wizard` or
`TRUENAS_PATH_B_SETUP=preprovisioned`; the latter requires a pool already
created and registered by the guarded provisioner.

On the tested TrueNAS 27.0.0-RC.1 UI, open the signed-in account menu →
**Preferences** to find **Session Timeout**. The live UI showed 300 seconds.
The timer is per user and activity resets it. The official Preferences guide
documents a 300-second default and a maximum of 2,147,482 seconds; the Advanced
Settings guide also documents **System → Advanced Settings → Access →
Configure**. See the official [Preferences guide](https://www.truenas.com/docs/scale/27/toptoolbar/settings/)
and [Advanced Settings guide](https://www.truenas.com/docs/scale/27/systemsettings/advanced/advancedsettings/).

The registered patch unit lane later passed 156 tests plus 45 subtests. The
full unit lane last passed 362 tests plus 45 subtests before this UI-helper-only
change and was not rerun. The clean registered Path A UI run passed dataset
writes, zvol creation, container pool selection/creation, VM wizard submission
with an existing boot-pool zvol, and found Session Timeout in Preferences. Its
log is
[`live-ui-v27-path-a-clean-20261019.log`](../artifacts/live-ui-v27-path-a-clean-20261019.log).
A `pool.query` empty list is not evidence that `boot-pool` is absent: direct
`zpool list` establishes pool health, while patched `container.pool_choices`
and the UI form establish whether the pool can be selected.

The separate fresh Path B guest `truenas-v27-ui-b-fresh-r1-20261018` passed the
registered patch, read-only inventory, and partition-only gates. The latest
PWMCP UI run opened the Disks screen and pool wizard. `lsblk` sees the 4 GiB
`/dev/vda4`, but middleware and browser `disk.query` return only `/dev/vda`;
the Disks screen has no partition controls, and the wizard reported zero raw
capacity and no disks before stopping without `pool.create`. Post-attempt
registered inventory confirms no `boot-disk-data` pool or probe data. See the
[UI run](../artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log)
and [post-run inventory](../artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).
Creation of `/dev/vda4` and `boot-disk-data` through the UI remains open. The
new `preprovisioned` Path B downstream UI route passed its registered unit
gate, but has not yet been live tested. Real
second-disk cache use, TrueNAS UI/middleware cache configuration, share-client
access, VM guest boot, automatic remount through update, controlled
reboot/update survival, and actual KVM selection remain open until direct
evidence passes. See the [consumer instructions](CONSUMERS.md#run-pwmcp-ui-storage-acceptance)
and [resume procedure](CONSUMERS.md#resume-a-partial-path-a-ui-run).

### Preserving UI-created workloads for reboot and update checks

The ordinary `live-ui` lane cleans up its objects even when they are useful as
update fixtures. Migration acceptance opts into preservation and records a
manifest containing the UI-created dataset, container and VM IDs, zvol paths,
and hashes for data written to the filesystem and block devices. The
networked tester mounts the checkout read-only, so the wrapper exports the
manifest from tester temporary storage into the requested worktree artifact
path before removing the tester. A separate `live-ui-workloads` lane uses the
TrueNAS UI to submit container and VM Start/Stop actions, confirms their
middleware state transitions, and rechecks the recorded data hashes. This
separates workload creation evidence from lifecycle evidence and makes the
same records available to controlled reboot and update gates. These checks
establish record retention and service start/stop, not VM guest-OS boot.

On 2026-10-19, a fresh patched v27 Path A guest passed UI creation and a
separate workload cycle before and after a controlled v27 reboot. The second
cycle observed the VM autostart, then completed UI Start/Stop for both records
with the recorded filesystem and zvol hashes unchanged. This is the v27
baseline only; the same manifest has not yet been carried through a v27-to-v28
update and v28 patch reboot. The container has not yet been tested with the
user probe filesystem mounted into it.

After the October 21 host restart, the resumed guest passed two read-only
inventories and another registered UI workload cycle; all recorded filesystem
and zvol hashes remained unchanged. The UI returned the container and blank
VM to STOPPED, using Power Off when the VM's graceful Stop did not complete.
Serial output showed a second kernel-start sequence about nine minutes after
the first, without an explicit shutdown reason. This is an unexplained boot
observation, not evidence of a patch defect or provider action. The guest was
cleanly stopped after the run. See the [second inventory](../artifacts/live-inspect-v27-golden-ui-migration-20261021-second-boot-pass.log),
[UI cycle](../artifacts/live-ui-workloads-v27-golden-ui-migration-20261021-second-boot-pass.log),
and [serial log](../artifacts/v27-golden-ui-migration-20261019/vm-state/serial.log).
No v27-to-v28 update has run against this manifest yet.

### Recover an interrupted live tester

The live wrapper owns its fixed-name networked tester and normally removes it
after a registered gate. A host restart can stop the wrapper before its cleanup
runs, leaving an exited tester that blocks later lanes. Recovery stays in the
registered wrapper so it can verify the object before removing it: the
container must be exited, use the declared `tester-unified:local` image, and
carry a valid run ownership label. Cleanup is pinned to the inspected container
ID, so a replacement container under the same name is not the removal target.
Running containers, foreign images or labels, malformed state, and Docker
inspection failures all refuse recovery. The opt-in command is documented in
the [consumer guide](CONSUMERS.md#recover-an-interrupted-live-tester).

Remote SSH commands also have a bounded retry for classified transport
failures that occur before the remote command starts, including
`kex_exchange_identification` resets seen while middleware services are
starting. Command-level failures are not retried, so a mutation cannot be
silently issued twice.

## Local VM resource boundary

The v27/v28 QEMU guests were launched directly from the devcontainer. The
operator's `docker stats` snapshot showed `dstdns-devcontainer-vb` at 525.15%
CPU and 5.262 GiB / 12 GiB; Docker placed it in `dev-interactive.slice` with no
per-container CPU quota. The operator stopped both guests after the load
stalled VS Code. The first interrupted v27-to-v28 update transfer never
reached `update.manual`; a later isolated attempt started job 65 and reached
49% extraction before its QEMU container was stopped. Update survival remains
untested.

The host's live cgroup-v2 values were read through the host cgroup profiler:

| cgroup | CPU ceiling | CPU weight | memory current | memory high / max |
|---|---:|---:|---:|---:|
| `dev-interactive.slice` | 500% | 200 | about 5.0 GiB | 5 / 8 GiB |
| `dev-background.slice` | 500% | 20 | about 0.6 GiB | 5 / 8 GiB |
| parent `dev.slice` | 700% | — | about 5.8 GiB | 11.5 / 15.5 GiB |

A disposable probe container placed under
`dev.slice/dev-background.slice` and confirmed Docker applied its child
`cpu.max` and `memory.max`. At the time, starts were limited to one guest.
The current runner permits up to three under `$CGROUP_PARENT_DEV_BACKGROUND`
when each new start passes PSI-based memory admission, with a 200% CPU ceiling,
3.5 GiB container memory and swap caps, and 3 GiB guest RAM per guest. The
historical 5 CPU / 8 GiB slice limits and 7 CPU / 15.5 GiB parent limits were
later changed by the operator. Current starts sample `memory.pressure` at each
finite cgroup: admission refuses at `some avg10 >= 10%` or `full avg10 >= 1%`,
and requires a finite hard `memory.max`. `memory.high` is advisory and nominal
guest caps may overcommit while recent PSI remains low. Moving the runner
changes accounting and scheduling priority but does not remove shared-parent
or host contention. The v27-to-v28 transfer remains untested because no update
was started.

On 2026-10-16, the retained Path A v27 guest was running in its own container
under `dev-background.slice` while the registered `live-boot` lane ran from a
separate `tester-unified` container in `dev-gates.slice`. The guest stayed
below about 2.4 GiB of its 3.5 GiB cap in sampled readings; the live tester was
capped at 50% CPU and 512 MiB, attached to the guest's custom bridge, and
removed after the gate. During those samples the interactive devcontainer
reported roughly 34–100% CPU and 4.4–5.0 GiB of its 12 GiB cap. This is an
observed run, not a host-isolation guarantee: both child slices still share
the `dev.slice` and host. The former cap-projection admission rule treated a
VM's entire 3.5 GiB limit as reserved at `memory.high`, which rejected
intentional overcommit. It has been replaced with live PSI admission; the
kernel's hard `memory.max` remains enforced and can still OOM-kill workloads.

The three interrupted qcow2 overlays remain in `/tmp`:
`truenas27-patha-full-overlay.qcow2`, `truenas27-pathb-overlay.qcow2`, and
`truenas27-storagepatch-overlay.qcow2`. Read-only `qemu-img info` reports each
as a 20 GiB virtual qcow2 with `corrupt` and `dirty` unset. This is metadata
inspection, not a full cluster check. Any resumed guest must use a new CoW
overlay so these originals remain intact.

## Required validation before calling this a patch

1. Preserve the v28 ISO's embedded version/build metadata and recheck each
   patch target's fingerprint on the exact host build. The three v28 adapter
   targets match the selected ISO, but the ISO does not publish its middleware
   commit. Verify the installed `truenas_pylibvirt` behavior and patch targets
   before modifying another v27 or v28 build.
2. Test the supported upgrade path from the current 25.10 maintenance release
   through the 27 RC to the selected 28 nightly. TrueNAS currently labels 27
   RC as Early Release and 28 nightlies as Experimental; the official upgrade
   guidance says to start from the latest maintenance release of the current
   major version. See [software status and upgrade paths](https://www.truenas.com/docs/softwarereleases/).
3. On disposable TrueNAS VMs, complete the separate-partition pool tests and
   compare with a boundary-aware boot-pool user-data namespace. Reboot import,
   dataset/zvol create, query, update, and delete, normal pool-choice APIs,
   v27 VM create/start against a zvol, container lifecycle, a persistent
   container filesystem mount, and NFS/SMB share-record CRUD have passed.
   Still check NFS/SMB client access, submitted VM/share UI workflows, guest
   boot, and upgrade survival. PWMCP has already passed the limited Path A/B
   UI checks described above.
4. The v27 TCG API and process-start path passed with `/dev/kvm` absent. Still
   confirm that the KVM path remains selected when `/dev/kvm` is available.
   QEMU TCG does not by itself prove
   this condition: the first test used `-cpu max`, exposed `svm` in the guest,
   and created `/dev/kvm`. The masked run uses `-cpu max,-vmx,-svm` and must
   verify the device is absent inside TrueNAS. Test the candidate v28 adapter
   separately against the exact installed middleware files.
5. Upgrade with custom data present. Confirm the pool, user datasets and
   zvols survive and remain queryable; verify TrueNAS internal datasets remain
   hidden and unmanageable.
6. Extend PWMCP acceptance to submit a VM wizard and create/query/delete
   dataset-backed shares through the UI. Current tests cover navigation,
   dataset creation, pool selection, VM wizard fields, and share sections/Add
   actions. The earlier 25.10.4 report records a separate failed UI workflow
   and should not be treated as v27/v28 evidence.
7. Keep release-specific middleware hunks small and fail closed on unknown
   source revisions. Put shared path-policy logic in patch-owned files where
   possible, then test the boundary cases against every supported release.

Registered patch, boot, provisioning, and storage gates now pass on the
inspected v27 RC and v28 nightly. Keep the adapters experimental until the
remaining KVM, update/remount, UI, client-sharing, guest-boot, and separate-disk
checks have direct evidence.


## Single-disk storage patch status

The ordinary VM installer targets data pools. Separate experimental patches
now test two ways to use free space on a TrueNAS system disk: a bounded
`boot-pool/user-data` namespace (Path A), and a separate `boot-disk-data` pool
on a reserved GPT tail (Path B). Historical acceptance exercised both paths
with scripted API checks and limited PWMCP UI navigation, using a Path B pool
created outside the current fresh UI workflow. Fresh patched Path A submitted
UI acceptance passed. Path B UI pool creation remains open; the new
preprovisioned downstream UI route passed its registered unit gate and still
needs fresh live acceptance. A fresh Path A live-storage
acceptance also passed on 2026-10-16 from a COW guest in the separate
`dev-background.slice` container. It verified dataset and zvol CRUD, cache-vdev
add/remove, VM start with QEMU XML and process arguments confirming TCG,
container dataset persistence, NFS/SMB share-record CRUD, and cleanup. The
detached tester ran under `dev-gates.slice` with 25% CPU and 256 MiB memory/swap
caps. The Path A guest shut down cleanly through QMP/ACPI; its COW disk and EFI
state were retained. The 25.10.4 proposal changes
Docker and Virt middleware in ways that no longer apply cleanly to v27; v27
also replaces the Virt middleware module with separate VM and container
services. The v28 nightly changes those paths again, so each release needs
small adapters around shared policy and exact source fingerprints.

The selected v28 ISO identifies its build version and time but not the
middleware Git commit. A patch therefore cannot safely rely on the ISO's
version string alone: it must compare the exact target files or their
fingerprints and refuse an unknown source shape. All 15 current middleware files used
by the candidate v28 storage, TCG, and boot-remount adapters have been
extracted from this ISO and match the recorded fingerprints; the patches apply
and compile against them. This binds the inspected patch targets to this ISO,
but does not identify its full middleware checkout. The v28
`truenas_pylibvirt` code selects QEMU when `/dev/kvm` is absent. The installed
v27 RC's VM XML generator always selects KVM, so v27 also needs a backend
change, in addition to middleware validation.

Path A also needs a boot-time remount. The v27 reboot gate showed that the
generic ZFS mount service is disabled and the boot pool is not registered in
`storage.volume`, which is what `pool.import_on_boot` iterates. Custom
`boot-pool/user-data` descendants stayed unmounted until the adapter mounted
them. The v28 `pool.import_on_boot` has the same control flow; its extracted
rootfs contains `zfs-mount.service` but no enablement link under
`/etc/systemd/system/zfs.target.wants`. Keep the mount hook as a
release-specific adapter around the shared path policy, and mount only after
validating the root quota, mountpoint, `canmount`, resolved paths, and current
mount ownership. The registered v28 Path A reboot gate passed root/child
remount and marker persistence on the selected nightly. Automatic remount
through a v27-to-v28 update remains open because that update's marker was
mounted manually before verification.

Path B uses a separately partitioned `boot-disk-data` pool because it follows
normal data-pool API and UI paths. A local 27.0.0-RC.1 test created
the pool from a new GPT partition and registered it through
`pool.import_find`/`pool.import_pool`; middleware then created a normal
filesystem dataset and sparse zvol, and returned the pool through dataset,
volume, and container choice APIs. The test target was 20 GiB; its boot-pool
partition ended just below the 16 GiB image boundary, leaving about 4 GiB
unallocated for the new partition. This matches restoring a bounded golden
image to a larger disk without expanding partition 3. A normal full-disk
vanilla install has no such free tail, so Path B needs space reserved during
image/install preparation. The existing boot-pool partition extents were
unchanged. The pool auto-imported after reboot and a marker file
survived. With a test-only v27 TCG patch, the same guest also created a VM
definition, attached that zvol, and started a QEMU domain without `/dev/kvm`.
The probe VM had no guest OS, so no guest boot was verified. Middleware
dataset create/query/update/delete and sparse zvol create/query/grow/delete
also passed on the separate pool. A second TCG VM used `HOST-MODEL`; a third
used `CUSTOM` with `cpu_model: null`. Both started through the API. PWMCP
acceptance also passed on both storage paths: it created a user dataset from
the selected pool/tree, confirmed the container pool selection, loaded the VM
wizard through CPU/memory fields, and showed the SMB and NFS sections and Add
actions. It did not submit the VM wizard or create a share. Container
create/start/stop/delete also passed through middleware. A user dataset mounted
into an Alpine container accepted a marker written from inside the container
and retained it at the host dataset path. NFS and SMB share-record
create/query/delete succeeded, but client protocol access was not tested.
The separate v27-to-v28 update gate later verified that marker bytes survived
the upgrade, though it manually mounted the dataset before reading them.
Automatic remount across that updated boot and a guest OS boot remain open.
Path B partition/pool setup in the earlier acceptance was manual. The later
registered idempotent provisioner passed on both v27 and v28; v27 Path B
provisioning and storage used the same COW guest after its Path A run.

### Path B pool-wizard safety

The v27 adapter models a reserved partition as an available pool-create target
only when it is an unused ZFS GPT partition labeled `boot-disk-data`, numbered
4 or later, and its parent disk contains a `boot-pool` leaf. The create path
accepts exactly one Stripe data vdev and opens the partition by PARTUUID. This
keeps selection bound to the GPT identity that was inspected and limits the
adapter to the topology it can prove safe. Mirror, mixed-disk, and auxiliary
vdev requests receive a validation refusal.

The shim also refuses every non-default create option it does not implement,
including encryption, encryption parameters, deduplication, dedup-table quota,
checksum, duplicate-serial allowance, SED mode, and forced topology. Returning
before stock validation would silently discard those settings. An
option-specific refusal makes that boundary visible to the wizard caller.

Creation follows the installed v27 `pool.create` properties: ZFS options
`feature@lz4_compress=enabled`, `altroot=/mnt`, the middleware cachefile,
`failmode=continue`, `autoexpand=on`, and `ashift=12`; root filesystem options
`atime=off`, `acltype=posix`, `aclinherit=discard`, `aclmode=discard`,
`compression=lz4`, `xattr=sa`, and `mountpoint=/<pool>`. Keeping this property
set avoids a fixed-sector-size mismatch and makes datasets inherit the same
root properties as a stock pool.

Destructive export needs a separate guard. Installed v27 `pool_/export.py`
calls `pool.get_disks`, then invokes `disk.wipe(..., 'QUICK', False)` for the
resolved devices. `pool.get_disks` returns whole disks; the installed
`plugins/zpool/get_zpool_disks_impl.py` resolves a partition leaf to its parent
device through `/sys/class/block/<partition>/partition`. The quick wipe writes
the first and last 32 MiB of that parent, which includes the boot disk's GPT
and boot area. The adapter refuses `pool.export` with `destroy=true` for a
reserved-partition pool and leaves ordinary non-destructive export available.
The live API probe checks the exact refusal, `boot-pool` GUID/health/status,
the unchanged `lsblk`/`sgdisk` partition table, and the adopted pool's ONLINE
state. It also exercises replace, offline/online, detach, and remove while
checking the other partitions remain unchanged.

After `zpool create`, adoption can fail at export, import discovery, or import.
Rollback re-reads the partition's PARTUUID, label, ZFS type, parent, and boot
pool leaves before destructive steps. It destroys only a pool whose status
shows the exact reserved partition, or clears labels only on the verified
PARTUUID after confirming the pool is neither imported nor exported there.
Ambiguous state or failed cleanup raises a `CallError` that reports the
remaining state instead of presenting a successful creation.

The Web UI gates the VM page on `vm.virtualization_details.supported`, so the
middleware capability update exposes the existing page without a UI source
change. PWMCP checks passed for the VM page and the wizard through its CPU and
memory step; the wizard was not submitted, and a visible TCG warning remains
open. The create gate is release-specific: v27 uses
`vm.supports_virtualization`, while the inspected v28 nightly checks
`kvm_supported()` directly in `vm/crud.py`.
TCG support therefore needs a shared capability rule plus a small per-release
create-gate adapter, and a v27-only backend XML change.

The adapters remain experimental rather than release-ready. Registered
storage gates now pass on v27 and v28 for both Path A and Path B. Each proves
that an existing middleware-managed container dataset survives refused public
destroy and snapshot attempts, that no snapshot appears, and that public clone
refusal remains active. Path B provisioning passed on both releases; the v27
Path B storage run reused the Path A COW guest after provisioning. The v28
Path A reboot gate also passed root/child remount and marker persistence. The
four storage results are:

- [v27 Path A](../artifacts/live-storage-v27-path-a-existing-container-protection-pass-20261017.log)
- [v27 Path B](../artifacts/live-storage-v27-path-b-existing-container-protection-pass-20261017.log)
- [v28 Path A](../artifacts/live-storage-v28-path-a-existing-container-protection-pass-20261017.log)
- [v28 Path B](../artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log)

At the earlier checkpoint, the patch unit lane passed with 106 tests and 45
subtests; see the
[checkpoint log](../artifacts/unit-boot-pool-patch-v27-dns-retry-20261017.log).
The v27-to-v28 update preserved marker bytes, but verification manually mounted the
data, so automatic remount through update is open. The original v28 Path B
destroy-refusal failure remains preserved; a later registered gate directly
verified that the middleware-managed dataset survived both destroy and
snapshot attempts and that no attempted snapshot appeared. See the passing
[v28 Path B protection gate](../artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log).

The earlier registered patch unit lane passed with 121 tests and 45 subtests
in [`unit-boot-pool-patch-root-parent-mountpoint-direct-engine-20261018.log`](../artifacts/unit-boot-pool-patch-root-parent-mountpoint-direct-engine-20261018.log);
the full unit lane passed with 319 tests and 45 subtests in
[`unit-truenas-root-parent-mountpoint-20261018.log`](../artifacts/unit-truenas-root-parent-mountpoint-20261018.log).
The pre-repair r4 inventory at
[`live-inspect-v27-path-a-r4-before-parent-mountpoint-repair-retry3-20261018.log`](../artifacts/live-inspect-v27-path-a-r4-before-parent-mountpoint-repair-retry3-20261018.log)
is historical: it correctly found no root before the guarded repair and UI
creation. A later successful registered inventory confirmed the UI-created
root and prior user dataset/zvols after a host interruption:
[`live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log`](../artifacts/live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log).
The historical inventory is not a current-state check. The clean registered
Path A `live-ui` run on 2026-10-19 resumed the verified UI-created root/cache,
wrote to a UI-created filesystem, created zvols, selected `boot-pool` for a
container, and submitted a VM wizard with an existing boot-pool zvol. It also
found the per-user Session Timeout under the signed-in account menu →
Preferences, with a value of 300 seconds. Its post-run inventory found no
probe datasets, containers, or VM records. See the
[UI run](../artifacts/live-ui-v27-path-a-clean-20261019.log) and
[post-run inventory](../artifacts/live-inspect-v27-path-a-clean-20261019-post-ui.log).
The current patch unit lane passed with 156 tests and 45 subtests; the full
unit lane last passed with 362 tests and 45 subtests before this UI-helper-only
change and was not rerun. The cache exercise used a temporary file-backed
target pool on both Path A and Path B. This demonstrates cross-pool L2ARC
attachment in that setup, not a separate disk-backed pool, import ordering, or
TrueNAS UI/middleware cache-device configuration. The current preprovisioned
Path B UI helper passed its registered unit gate with 170 tests and 45
subtests; fresh live acceptance remains open. See the
[unit log](../artifacts/unit-boot-pool-patch-path-b-ui-preprovision-mode-20261021.log).
Actual KVM selection, guest OS boot, NFS/SMB client access, Path B UI pool
creation, Path B reboot/import, automatic remount through update, and broader
patched-update survival remain open. The latest fresh Path B UI attempt found
that the kernel sees `/dev/vda4`, while middleware and browser `disk.query`
omit it. The Disks screen had no partition controls, the wizard reported no
disks, and no pool was created. Evidence:
[registered UI diagnostic](../artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log)
and [post-attempt inventory](../artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).
The latest patch unit lane passed 156 tests and 45 subtests after the UI helper
update; its log is
[here](../artifacts/unit-boot-pool-patch-v27-progress-dialog-20261019.log).
Local QEMU tests use a dedicated Docker runner under `$CGROUP_PARENT_DEV_BACKGROUND`,
with detached testers in `$CGROUP_PARENT_DEV_GATES`; see the
[patch investigation and evidence](../README.md).

The complete v27 patch set was re-applied to fresh `27.0.0-RC.1` COW guests on
2026-10-16 after all 13 target fingerprints matched. `middlewared` was stopped
during patching; modified modules compiled; it restarted successfully; and
`/usr` was restored to read-only. Detached `tester-unified` live-storage runs
passed on both Path A and Path B, including TCG VM startup, container dataset
persistence, share-record CRUD, and cleanup. Path B used a 20 GiB guest disk
with partition 3 unchanged and a 4 GiB `vda4` pool imported through middleware.
The Path A run is logged at
`../artifacts/live-storage-v27-path-a-20261016.log`; Path B is at
`../artifacts/live-storage-v27-path-b-20261016.log`. These API runs
do not prove guest OS boot, KVM selection, update survival, or share client
access.

Path A exposed a boot-time lifecycle gap that the API CRUD test could not
catch. `zfs-mount.service` is disabled on TrueNAS, while `ix-zfs.service` calls
`pool.import_on_boot`; that method imports only pools listed in
`storage.volume`, and the boot pool is intentionally absent. The custom
`boot-pool/user-data` root therefore appeared mounted before reboot but was
not mounted afterward. The v27 `boot-mount.patch` calls a guarded mount helper
after ordinary pool imports and before `pool.post_import`. It mounts only a
recognized root with its exact `/mnt/boot-pool/user-data` mountpoint, positive
quota, and `canmount=on`; verifies the expected boot filesystem at `/mnt`;
then mounts eligible descendants one by one inside that namespace. It checks
the resulting `filesystem.statfs` source and destination and skips unsafe,
occupied, protected, or invalid paths without failing system boot. The
registered live-boot gate passed twice, verifying the root and child mount
across a reboot, marker contents, pool health, and API dataset operations. The
latest log is
[`live-boot-v27-path-a-20261016-r2.log`](../artifacts/live-boot-v27-path-a-20261016-r2.log).
The update marker bytes survived a v27-to-v28 upgrade, but the gate mounted
the saved data manually before reading it. The first v28 boot-mount hook also
failed because it called `query_impl`, absent from the v28 service; the local
fix uses `list_impl`. The registered v28 Path A reboot/remount gate passed
after that fix. Automatic remount through the update and broader patched-data
update survival remain open.

The reboot gate now lists mounted filesystems in the user-data dataset tree
and unmounts them one at a time, deepest first and without force, after writing
its marker. This avoids relying on `zfs unmount -r`, which the TrueNAS guest's
ZFS command does not support. The boot hook must mount the root and eligible
descendants again after reboot. If a run begins with the root already
unmounted, it performs one recovery reboot before writing any marker, then
unmounts and reboots a second time to check persistence. This avoids writing
into the boot environment's ordinary `/mnt` directory when the expected ZFS
mount is absent.

The first v28 `live-boot` attempt exposed the unsupported recursive-unmount
option before it requested a reboot. Its `finally` cleanup removed the unique
probe dataset, and read-only inspection confirmed the root stayed mounted.
The gate now uses the explicit child-first unmount procedure. Its 2026-10-17
rerun passed: the root and marker child remounted, the marker bytes matched,
`container.pool_choices` included `boot-pool`, and the gate removed its
temporary datasets. The v28 storage gate still needs to be rerun.

Live storage and reboot gates use a temporary `tester-unified` exec target
because run-gate's regular ephemeral containers intentionally have no network.
The wrapper attaches that runner to the managed guest's custom bridge, mounts
the checkout read-only, uses the private-key owner UID, places the runner in
`$CGROUP_PARENT_DEV_GATES`, and verifies 0.5 CPU / 512 MiB limits before the
registered lane starts. Each guest remains a separate QEMU container in
`$CGROUP_PARENT_DEV_BACKGROUND`, capped at 200% CPU and 3.5 GiB. The local
runner supports up to three guests when the per-start cgroup memory check
admits them.

### V28 adapter application

Keep the shared policy helpers in separate files and limit release-specific
patches to the upstream call sites that need to delegate into those helpers.
This makes a new release adapter smaller to refresh when middleware moves,
while exact source fingerprints and zero-fuzz application turn upstream drift
into a refusal that names the unreviewed build. The current lane accepts only
the selected v28 build and its 15 target hashes; it is not an automatic
cross-version patcher.

The live lane stages a closed, deterministic bundle, confirms the version,
Path A layout, `/dev/kvm` absence, persistent backup mount, and stopped guest
states, then archives every original target before changing `/usr`. A nullable
VM query status is resolved with `vm.status`; an unresolved state refuses the
middleware restart. The guest stops middleware, applies each patch with zero
fuzz, installs the helpers, compiles the modified modules, restarts middleware,
checks TCG and storage API results, and restores the ZFS dataset and mount to
read-only. It records hashes in an atomic manifest only after those checks
pass. On failure, the trap restores from the durable archive and verifies the
original hashes, service state, and `/usr` protection; a failed rollback exits
with a distinct failure and points to the backup. The registered artifact and
live gates make the exact bundle and live outcome reproducible. The live lane
passed on `28.0.0-MASTER+20261014-230204`: middleware restarted, the API
advertised TCG, hardware flags stayed clear, `boot-pool` appeared in container
pool choices, and `/usr` returned to read-only. The saved output is
[`live-v28-patch-20261017.log`](../artifacts/live-v28-patch-20261017.log).

Two live failures refined the adapter before that pass. Importing v28's
`INTERNAL_PATHS` through `pool_.dataset_query_utils` re-entered the container
plugin import and caused a circular import in `async_validators`; the adapter
now imports the same release policy from the lightweight `zfs.utils` module.
Internal v28 calls to `filesystem.statfs` return Pydantic models rather than
mappings, so the shared helper reads both mapping and typed-model fields and
the release call sites use that accessor. The import-cycle run was interrupted;
recovery inspection verified the original hashes and absent helpers, then
`/usr` was explicitly restored to read-only. The next failure exercised the
rollback trap, which verified the original source hashes, middleware, and
read-only protection.

The live-storage attempts showed that v28's default `pool.dataset.query`
walker pruned recursion at the internal `boot-pool` root and hid
`boot-pool/user-data` and its descendants. The patch now treats only the exact
boot-pool root as traversable. It continues to hide `.system`, `ROOT`, `grub`,
and `.truenas_containers`, while mutation guards keep the pool and managed
namespace roots non-editable. That change let the latest run pass dataset and
zvol operations.

After reboot, the v28 storage gate passed zvol cache add/remove on a temporary
file-backed target and started a VM with TCG confirmed in the process. Its
first container attempt found three missing internal-only `bypass=True`
arguments in `container/query_pull_images.py`: orphan destroy, image snapshot
creation, and exception-cleanup destroy. The local adapter restores those
flags only at the private calls. Earlier public destroy and snapshot probes
used synthetic paths that did not yet name an existing container dataset. The
first Path B run against an existing dataset returned JSON `null` from
`zfs.resource.destroy`. A later read-only inventory found no test-specific
dataset, but the gate's `finally` cleanup also ran, so that inventory could not
attribute the removal. Source tracing found `.truenas_containers` missing from
v28's non-boot-pool internal path list. The shared path policy now protects it
on both profiles and both releases. The corrected three-call
bundle passed guarded replacement with SHA-256
`6925688f93ba9af31afc3609cd8a93c47fb9fbd19c31866d85dc6ea44436ba25`;
middleware is active, the API responds, and `/usr` is read-only. The replacement
record is [`live-v28-patch-replacement-three-bypasses-20261017.log`](../artifacts/live-v28-patch-replacement-three-bypasses-20261017.log).

That rerun then exposed a second container regression. v28 changed same-pool
image cloning from v27's private `snapshot.clone_impl` call with
`bypass=True` and explicit mount to the public `pool.snapshot.clone` route.
That API correctly rejects the protected destination. The v28 adapter
restores the private call and mount only at middleware-owned container
cloning, and the live refusal probe confirms the public API remains guarded.
The exact source hunk applied and compiled; the unit lane passed with 97 tests
and 45 subtests. Guarded replacement migrated the prior 14-target adapter to
15 targets and passed with bundle SHA-256
`2a4fa86979d7234c5aa16f1b03e0f93225c35ae10ddc3a114d1bb9e7ac680a06`. Its log
is [`live-v28-patch-replacement-container-clone-bypass-20261017.log`](../artifacts/live-v28-patch-replacement-container-clone-bypass-20261017.log).
The unit log is
[`unit-boot-pool-patch-v28-container-clone-20261017.log`](../artifacts/unit-boot-pool-patch-v28-container-clone-20261017.log).
The complete Path A `live-storage` rerun passed; its log is
[`live-storage-v28-path-a-container-clone-pass-20261017.log`](../artifacts/live-storage-v28-path-a-container-clone-pass-20261017.log).
The earlier failed attempt remains at
[`live-storage-v28-path-a-image-clone-failure-20261017.log`](../artifacts/live-storage-v28-path-a-image-clone-failure-20261017.log).

The replacement transaction verified the installed 14-target adapter, kept a
rollback snapshot including the previously unpatched `crud.py`, restored the
stock files, and applied the 15-target bundle. On success it committed a new
manifest and left `/usr` read-only. Registered live-storage gates now pass the
existing-container destroy/snapshot survival checks on v28 Path A and Path B;
v27 Path A and Path B also pass. The final v28 Path B result is recorded at
[`live-storage-v28-path-b-existing-container-protection-pass-20261017.log`](../artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log).
The real second-disk cache target, full UI workflows, share-client access,
guest OS boot, actual KVM selection, automatic remount through update, and
broader patched-update survival remain open.

## Read-only update media

The v27 nightly update API redirects its train to v28, but the selected v28
release-list endpoint currently returns 404. `update.manual` can install the
`TrueNAS.update` payload embedded in the official ISO. That manifest requires
10,025,572,761 bytes free in the boot pool. The tested 16 GiB boot pool has
11,348,213,760 bytes free before staging; copying the 2.3 GiB update member to
that pool would reduce availability below the installer's strict size check.

The registered live-update gate therefore keeps the full ISO on the host and
exposes it to QEMU through a read-only bind mount as CD-ROM media. The guest
mounts the ISO9660 filesystem and passes the update file directly to
`update.manual` with cleanup disabled, so middleware does not try to unlink a
file on read-only media. The runner fingerprints the media in its state
manifest. Adding immutable media to an existing stopped state is allowed only
when every prior runner identity field still matches; changing or removing
media on later resume is refused. The guest and live tester keep their normal
background and gates slice limits.

The empty IDE CD-ROM QEMU creates by default is not hotpluggable on this
`q35` machine. The ISO CD-ROM must be present when QEMU starts. The first
attempt exposed the hybrid ISO as a virtio hard disk, so OVMF treated its EFI
partition as a boot disk and opened the installer. The runner now attaches the
ISO with `-cdrom`, while the VM's boot order remains the existing system disk;
live verification confirmed that TrueNAS booted from the system disk and saw
the ISO as read-only `/dev/sr0`. This keeps boot-pool usage out of the
update-payload staging path, but still requires the update manifest's full
boot-pool free-space requirement. The registered
[`live-update` lane](CONSUMERS.md#run-live-update-acceptance) passed on a fresh
COW clone: job 63 completed, the guest rebooted into v28, and both boot-pool
health and the update-marker hash were verified. The marker bytes survived the
update. The gate manually mounted the saved user-data datasets before hashing
the marker because the v28 mount adapter was not installed, so automatic
remount was not tested. Job 65 is a historical interrupted attempt at 49%
extraction; its preserved COW state is not the successful job-63 state and
must not be resumed as a new update. The gate retries only SSH startup errors
known to occur before command dispatch and accepts an existing job ID plus
proof-dataset hash for read-only polling without launching a second update.
Ambiguous replies to mutating calls are never retried automatically.
