# TrueNAS 27 TCG patch experiment

**Status: experimental, test-only.** The complete 15-target patch set has
been applied to disposable `27.0.0-RC.1` QEMU overlays. Registered Path A and
Path B storage gates now pass destroy/snapshot refusal against an existing
middleware-managed container dataset and verify it survives. The Path B
provisioner also passed on a reserved disk tail; its storage run used the same
COW guest after Path A, not a fresh isolated Path B guest. The Path A reboot
remount and limited PWMCP checks passed. Path B reboot/import, guest OS boot,
KVM selection, patched-data update survival, full submitted VM/share UI
workflows, and another v27 build remain untested. Check target fingerprints
before applying it elsewhere.

Path B API cleanup uses `disk.details` to confirm the reserved partition is
available again after `pool.export` with `destroy=false` and the explicit
PARTUUID/GUID reclaim procedure described in the [consumer guide](../docs/CONSUMERS.md#reclaim-an-exported-path-b-pool).

## Why v27 needs two changes

The installed RC reports KVM availability from `/dev/kvm`, and its VM
validation calls `vm.supports_virtualization`. Its
`truenas_pylibvirt/domain/vm/xml.py` nevertheless hardcodes
`domain type="kvm"`. Removing the middleware refusal alone cannot start a VM
under TCG. The second patch selects `domain type="qemu"` only when KVM is
unavailable. The v28 nightly already has a QEMU fallback in that generator,
but its middleware create gate differs; see the parent investigation.

The shared [`virtualization_support.py`](../shared/virtualization_support.py)
reports KVM when TrueNAS detects `/dev/kvm`; otherwise it reports TCG only when
the architecture-specific QEMU system binary is executable. The v27 patch
keeps `vm.flags` tied to KVM so TCG is not reported as having hardware
virtualization flags. With KVM present, this path retains the upstream KVM
domain type and capability result.

## VM source files inspected

The following files in the running test guest matched the checked source or
were copied directly from that guest:

| File | SHA-256 before patch | Source |
|---|---|---|
| `middlewared/plugins/vm/info.py` | `472746556b11b512da0bcd29e925ac625870627d5b4d224401f55fc5147b34d5` | TrueNAS middleware tag `TS-27.0.0-RC.1`, commit `d83c3bf89aa4226c8fd1ef25dd273bdf447eb777` |
| `middlewared/plugins/vm/vms.py` | `b8bbd49a34e981698f92bd0264b3227d0005babea8534fc9e4c74fc3a1d7ca0b` | Same tag and commit; inspected for the create gate |
| `truenas_pylibvirt/utils/__init__.py` | `9bac2610c68046aba602cf931a8d248d25c73e81d7ad54b3054f2ee46b7aa534` | Copied from the installed guest |
| `truenas_pylibvirt/domain/vm/xml.py` | `91e30f121fb65992445b54cf05383db7a4d9e6348a281cff3ec6dea17df3a40d` | Copied from the installed guest |

The middleware source hashes were compared with the checked-out RC source.
The pylibvirt XML file did not match the v28 source snapshot; its installed
contents were copied and used as the patch base. The complete patch set has
five patch files: `middleware.patch`, `storage.patch`, `libvirt.patch`,
`boot-mount.patch`, and `zvol-blocksize.patch`. `targets.sha256` lists all 15
release-specific target files. The zvol helper returns the existing 16K
recommendation only for `boot-pool`, which is excluded from `pool.query`; it
keeps normal data-pool topology calculations unchanged. The helpers
`virtualization_support.py` and `boot_pool_user_data.py` are copied separately.

## Disposable test procedure

The test uses a QEMU QCOW2 overlay over the unmodified 27 RC test disk. Start
QEMU with TCG and mask both virtualization flags:

```sh
-machine q35,accel=tcg -cpu max,-vmx,-svm
```

The storage test target was 20 GiB. Its boot-pool partition (`/dev/vda3`)
ended at sector `33554398`, just below the 16 GiB image boundary. The new
`/dev/vda4` started at sector `33554432` and was 4 GiB. `zpool list` showed
`boot-pool` at 15 GiB and `boot-disk-data` at 3.75 GiB. Partition 3 was not
resized. This reserved tail is available when the bounded golden image is
restored to a larger target; it would not exist on a vanilla installation
that allocated the entire disk to partition 3.

Inside TrueNAS, verify the negative condition before patching:

```sh
test ! -e /dev/kvm
grep -m1 '^flags' /proc/cpuinfo
midclt call vm.virtualization_details
```

The stock `vm.virtualization_details` result was `supported: false`. Copy the
five patch files, `targets.sha256`, and both helpers into `/tmp/v27`. Verify
every target hash before modifying the read-only `/usr` dataset. Stop
`middlewared` while replacing its modules, compile the changed Python files,
then restart it and restore the read-only mount:

```sh
zfs set readonly=off boot-pool/ROOT/27.0.0-RC.1/usr
mount -o remount,rw /usr
cd /usr/lib/python3/dist-packages
sha256sum --check /tmp/v27/targets.sha256
patch --dry-run --batch -p1 < /tmp/v27/middleware.patch
patch --dry-run --batch -p1 < /tmp/v27/storage.patch
patch --dry-run --batch -p1 < /tmp/v27/libvirt.patch
patch --dry-run --batch -p1 < /tmp/v27/boot-mount.patch
patch --dry-run --batch --fuzz=0 -p1 < /tmp/v27/zvol-blocksize.patch
systemctl stop middlewared.service
patch --batch -p1 < /tmp/v27/middleware.patch
patch --batch -p1 < /tmp/v27/storage.patch
patch --batch -p1 < /tmp/v27/libvirt.patch
patch --batch -p1 < /tmp/v27/boot-mount.patch
patch --batch --fuzz=0 -p1 < /tmp/v27/zvol-blocksize.patch
install -m 0644 /tmp/v27/virtualization_support.py middlewared/plugins/vm/
install -m 0644 /tmp/v27/boot_pool_user_data.py middlewared/plugins/
python3 -m py_compile middlewared/plugins/vm/info.py \
  middlewared/plugins/vm/virtualization_support.py \
  middlewared/plugins/boot_pool_user_data.py \
  middlewared/async_validators.py \
  middlewared/plugins/pool_/dataset_query_utils.py \
  middlewared/plugins/pool_/dataset.py \
  middlewared/plugins/pool_/dataset_info.py \
  middlewared/plugins/container/container.py \
  middlewared/plugins/container/dataset.py \
  middlewared/plugins/container/utils.py \
  middlewared/plugins/container/image.py \
  middlewared/plugins/container/lifecycle.py \
  middlewared/plugins/pool_/import_pool.py \
  middlewared/plugins/zfs/utils.py \
  middlewared/plugins/vm/vm_devices.py \
  middlewared/utils/libvirt/storage_devices.py \
  truenas_pylibvirt/domain/vm/xml.py
systemctl start middlewared.service
systemctl is-active middlewared.service
midclt call vm.virtualization_details
midclt call vm.flags
zfs set readonly=on boot-pool/ROOT/27.0.0-RC.1/usr
mount -o remount,ro /usr
```

The target file hashes matched, all five patches applied without fuzz, and
all changed Python files compiled. After middleware restart, the patched API
reported virtualization as supported, while `/dev/kvm` remained absent and
all hardware virtualization flags remained false. The test created a VM
definition through `vm.create`, attached the existing
`boot-disk-data/my-vms/test-vol` zvol, and started it through `vm.start`.
`vm.status` reported `RUNNING`; the domain XML used `<domain type='qemu'>`, and
the QEMU process used `-accel tcg`. The VM was shut down cleanly after
inspection. A second VM using `HOST-MODEL` CPU mode also reached `RUNNING`
under TCG and was deleted after inspection. A third probe used `CUSTOM` CPU
mode with `cpu_model: null`; that payload also reached `RUNNING` and was
deleted. This API test does not certify the submitted Web UI wizard; a
separate PWMCP check passed the VM wizard through its CPU/memory step but did
not submit it. The pool stayed ONLINE and its marker file remained present.

The test VM had no guest OS, so it proves TrueNAS API validation, zvol
attachment, domain generation, and QEMU process startup only. The PWMCP UI
test separately proved that the VM page and create wizard are available and
that CPU/memory fields render; it did not submit the wizard. Neither check
proves that a guest OS boots. The correct libvirt
socket for inspection in this test image was:

```sh
virsh -c 'qemu+unix:///system?socket=/run/truenas_libvirt/libvirt-sock' dumpxml 1
```

The normal `qemu:///system` URI is not the socket used by this TrueNAS build.
Nested TCG made the management UI and middleware slow; one status call had a
transient handshake timeout under load, then succeeded on retry. This was not
a VM-start failure. The test used `-cpu max,-vmx,-svm`; omitting those masks
can expose a virtualization flag and create `/dev/kvm`, invalidating the
no-KVM test.

## Path A mount persistence after reboot

Path A initially passed create/query/mount checks but failed persistence: after
reboot, the root and its child datasets remained in ZFS but were unmounted.
The host's `zfs-mount.service` is disabled. `ix-zfs.service` runs
`pool.import_on_boot`, which imports configured storage pools and does not
process the boot pool because it is not an entry in `storage.volume`. The
boot-pool root uses `mountpoint=none`, so no other boot unit mounted the
custom user-data namespace.

`boot-mount.patch` adds a guarded `PoolService.mount_boot_pool_user_data()`
call after configured data-pool imports and before `pool.post_import`. It only
mounts an exact `boot-pool/user-data` root with positive quota,
`canmount=on`, and mountpoint `/mnt/boot-pool/user-data`. It verifies that
`/mnt` is still on the expected TrueNAS root filesystem and checks the source
and destination after each mount. Descendants must have `canmount=on`, remain
under the root in dataset and filesystem path terms, and avoid protected
internal datasets; `.truenas_containers` is allowed for the container manager.
Unsafe properties, symlink escapes, occupied mountpoints, failed queries, and
mount errors are logged and skipped so a custom data mount cannot abort host
boot.

The registered `live-boot` lane passed twice on the disposable v27 Path A VM.
It checks that root and child mounts return, the marker SHA-256 is unchanged,
`boot-pool` is healthy, and middleware dataset query/create still works. The
lane runs through `run-live-gate.py`: its short-lived tester is on
the guest bridge in `$CGROUP_PARENT_DEV_GATES`, with 0.5 CPU and 512 MiB caps;
the QEMU guest stays under `$CGROUP_PARENT_DEV_BACKGROUND`. The latest log is
`../artifacts/live-boot-v27-path-a-20261016-r2.log`; the first is
`../artifacts/live-boot-v27-path-a-20261016.log`. The repeat needed
about six minutes for middleware to return; the lane reported periodic SSH
and API readiness failures until recovery, then passed and cleaned up. This
proves reboot behavior on `27.0.0-RC.1`, not survival through an OS update.

Filesystem dataset and sparse-zvol operations on the test pool also passed
through middleware. The disposable dataset accepted a compression update to
`LZ4`; the sparse zvol grew from 64 MiB to 128 MiB. Both were queried after
the change and deleted. The zvol-delete response itself is void, so its
absence was checked with a follow-up `pool.dataset.query`.

Representative calls (use fresh, unique dataset names):

```sh
midclt call pool.dataset.create \
  '{"name":"boot-disk-data/patch-fs-probe","type":"FILESYSTEM"}'
midclt call pool.dataset.update boot-disk-data/patch-fs-probe \
  '{"compression":"LZ4"}'
midclt call pool.dataset.query \
  '[["id","=","boot-disk-data/patch-fs-probe"]]'
midclt call pool.dataset.delete boot-disk-data/patch-fs-probe \
  '{"recursive":false}'

midclt call pool.dataset.create \
  '{"name":"boot-disk-data/my-vms/patch-zvol-probe","type":"VOLUME","volsize":67108864,"sparse":true,"create_ancestors":false}'
midclt call pool.dataset.update boot-disk-data/my-vms/patch-zvol-probe \
  '{"volsize":134217728}'
midclt call pool.dataset.query \
  '[["id","=","boot-disk-data/my-vms/patch-zvol-probe"]]'
midclt call pool.dataset.delete boot-disk-data/my-vms/patch-zvol-probe \
  '{"recursive":false}'
midclt call pool.dataset.query \
  '[["id","=","boot-disk-data/my-vms/patch-zvol-probe"]]'
```

The final query returned `[]`. Additional API probes exercised container
lifecycle, a persistent user-dataset mount inside a container, and NFS/SMB
share-record CRUD on the separate pool:

1. `container.create` pulled `alpine:3.24:amd64:default` version
   `20261015_13:00`, listed by the guest's registry query, and created its
   managed root dataset below
   `boot-disk-data/.truenas_containers`. The create call returned a job id;
   its successful job result contained the container id.
2. `container.device.create` attached
   `/mnt/boot-disk-data/container-probe-data` at `/data`. `container.start`
   reached `RUNNING`. Using the returned container PID with host `nsenter`, a
   marker was written to `/data`; reading the same file from the host dataset
   mount returned the marker. This verifies the API-configured mount and data
   path, not container UI behavior.
3. `container.stop` and `container.delete` both completed successfully. Under
   the outer QEMU TCG VM, graceful stop took about 48 seconds; allow for slow
   job completion when testing this environment.
4. `sharing.nfs.create/query/delete` and
   `sharing.smb.create/query/delete` succeeded for datasets in
   `boot-disk-data`.

The following are the tested API payloads. Run them only on a disposable host
and use fresh dataset/share/container names. Replace `JOB_ID`, `CONTAINER_ID`,
`CONTAINER_PID`, `NFS_SHARE_ID`, and `SMB_SHARE_ID` with the numeric values
returned by the preceding calls. In the clean test database,
`container.create` returned job `102`, whose successful result contained
container id `1`; these IDs are not stable. Poll `core.get_jobs` until a job's
state is `SUCCESS` before continuing, and use the `result.id` value from the
container-create job:

```sh
midclt call container.image.query_registry
midclt call pool.dataset.create \
  '{"name":"boot-disk-data/container-probe-data","type":"FILESYSTEM"}'
midclt call container.create \
  '{"name":"patch-container-probe","pool":"boot-disk-data","image":{"name":"alpine:3.24:amd64:default","version":"20261015_13:00"},"autostart":false,"time":"LOCAL","shutdown_timeout":30,"capabilities_policy":"DEFAULT","idmap":{"type":"DEFAULT"}}'
midclt call core.get_jobs '[["id","=",JOB_ID]]'
midclt call container.device.create \
  '{"container":CONTAINER_ID,"attributes":{"dtype":"FILESYSTEM","source":"/mnt/boot-disk-data/container-probe-data","target":"/data"}}'
midclt call container.start CONTAINER_ID
midclt call container.query '[["id","=",CONTAINER_ID]]'
# Substitute the returned status.pid for CONTAINER_PID.
nsenter -t CONTAINER_PID -m -u -i -n -p -- /bin/sh -c \
  'printf persist-through-zfs-dataset > /data/patch-probe.txt'
cat /mnt/boot-disk-data/container-probe-data/patch-probe.txt
midclt call container.stop CONTAINER_ID \
  '{"force":false,"force_after_timeout":true}'
midclt call core.get_jobs '[["id","=",JOB_ID]]'
midclt call container.delete CONTAINER_ID \
  '{"force":false,"recursive":false}'
midclt call core.get_jobs '[["id","=",JOB_ID]]'
midclt call pool.dataset.delete boot-disk-data/container-probe-data \
  '{"recursive":false}'

midclt call pool.dataset.create \
  '{"name":"boot-disk-data/nfs-probe","type":"FILESYSTEM"}'
midclt call sharing.nfs.create \
  '{"path":"/mnt/boot-disk-data/nfs-probe","comment":"disposable patch probe","enabled":true,"ro":false,"maproot_user":"","maproot_group":"","mapall_user":"","mapall_group":"","networks":[],"hosts":[],"security":[]}'
midclt call sharing.nfs.query '[["id","=",NFS_SHARE_ID]]'
midclt call sharing.nfs.delete NFS_SHARE_ID
midclt call pool.dataset.delete boot-disk-data/nfs-probe '{"recursive":false}'

midclt call pool.dataset.create \
  '{"name":"boot-disk-data/smb-probe","type":"FILESYSTEM"}'
midclt call sharing.smb.create \
  '{"purpose":"DEFAULT_SHARE","name":"patch-smb-probe","path":"/mnt/boot-disk-data/smb-probe","enabled":true,"comment":"disposable patch probe","readonly":false,"browsable":true,"access_based_share_enumeration":false,"audit":{"enable":false,"watch_list":[],"ignore_list":[]},"options":{"aapl_name_mangling":false,"hostsallow":[],"hostsdeny":[]}}'
midclt call sharing.smb.query '[["id","=",SMB_SHARE_ID]]'
midclt call sharing.smb.delete SMB_SHARE_ID
midclt call pool.dataset.delete boot-disk-data/smb-probe '{"recursive":false}'
```

These are middleware/API tests. They do not prove an NFS or SMB client can
mount and use the exported share, that the submitted Web UI share workflow
works, or that a TrueNAS update preserves the resulting configuration. The
separate PWMCP check verified the share sections and Add actions only. The temporary
container, shares, and probe datasets were deleted. The image-registry cache
under `.truenas_containers/images` is middleware-managed and was left in the
disposable overlay.

## Fresh isolated Path B acceptance (2026-10-16)

The complete patch set was freshly applied to a copy-on-write overlay over the
unmodified 20 GiB test disk. All 12 hashes in `targets.sha256` matched before
patching. The live guest had `/dev/kvm` absent; after middleware restarted,
`vm.virtualization_details` reported support while `vm.flags` remained free of
hardware virtualization flags. The ZFS system dataset was read-only again
before the acceptance run.

The free tail was partitioned without changing `vda3`: partition 4 spans
sectors `33554432`–`41943006` and hosts `boot-disk-data`. The manual setup was:

```sh
sgdisk -n 4:0:0 -t 4:BF01 -c 4:boot-disk-data /dev/vda
partprobe /dev/vda
udevadm settle
zpool create -f -o cachefile=none -O mountpoint=none boot-disk-data /dev/vda4
zpool export boot-disk-data
```

Then `pool.import_find` found the exported pool; after its job succeeded,
`pool.import_pool` with the returned GUID registered/imported it through
middleware. After that job succeeded, `pool.query` returned the pool ONLINE on
`/dev/vda4`. The detached `tester-unified` `live_storage.py` run passed Path B
preflight, dataset create/update, zvol create/grow/VM disk choice, cache-vdev
add/remove, VM create and start with QEMU TCG confirmed in XML and process
arguments, container lifecycle with a persistent dataset mount, NFS/SMB
share-record CRUD, and cleanup. The preserved output is the ignored artifact
`scripts/truenas/boot-pool-patch/artifacts/live-storage-v27-path-b-20261016.log`.
The fresh Path A API acceptance also passed the storage/VM/container/share
scenarios, and its COW guest shut down through QMP/ACPI using `tcg_vm.py`. Its
log is `scripts/truenas/boot-pool-patch/artifacts/live-storage-v27-path-a-20261016.log`.
Both tester containers used the dev-gates slice because the registered
`run-gate.py` lane cannot currently attach the target guest's Docker network;
each was capped at 25% CPU and 256 MiB memory/swap. These runs do not certify
guest OS boot, UI submission, share client access, KVM selection, or update
survival.

After the experiment, restore the test guest's `/usr` dataset to read-only:

```sh
zfs set readonly=on boot-pool/ROOT/27.0.0-RC.1/usr
mount -o remount,ro /usr
```

This only restores the dataset property and mount mode; it does not remove
the test patch from the disposable overlay.

## Registered Path B provision and existing-container protection (2026-10-17)

The registered `live-path-b-provision` lane passed on the v27 COW guest after
its Path A acceptance. It created `/dev/vda4` in the reserved aligned tail,
left the existing boot-pool partition extents unchanged, registered
`boot-disk-data` ONLINE, and confirmed an identical repeat was a no-op. The
live-storage gate then passed on Path B on that same guest. It confirmed an
existing middleware-managed dataset below
`boot-disk-data/.truenas_containers/containers/`, refused public destroy and
snapshot creation, checked direct ZFS inventory to prove the dataset survived
and no snapshot appeared, then passed public clone refusal, VM/container
lifecycle, persistent container data, share-record CRUD, and cleanup.

The gate log is
[`live-storage-v27-path-b-existing-container-protection-pass-20261017.log`](../artifacts/live-storage-v27-path-b-existing-container-protection-pass-20261017.log);
provisioning is logged at
[`live-path-b-provision-v27-path-a-cow-20261017.log`](../artifacts/live-path-b-provision-v27-path-a-cow-20261017.log).
The guest was stopped through `tcg_vm.py` after a read-only post-pass
inspection. Its COW overlay remains available under
`scripts/truenas/.vm/v27-path-a-20261016-r3`.

## Path B pool-wizard shim safety

`ui-pools.patch` also adapts the v27 pool wizard for one reserved partition.
The helper offers only an unused `boot-disk-data` ZFS GPT partition numbered
4 or later on a disk that contains a `boot-pool` leaf. Pool creation is limited
to one Stripe data vdev, uses `/dev/disk/by-partuuid/<PARTUUID>`, and copies
the stock v27 pool properties. Encryption, non-default encryption options,
deduplication, dedup-table quota, checksum, duplicate-serial allowance, SED
mode, and forced topology are refused with option-specific errors.

Stock v27 destructive export resolves a partition vdev to its whole parent
disk before calling `disk.wipe` with `QUICK`; that path overwrites the first
and last 32 MiB. The adapter therefore refuses `pool.export` with
`destroy=true` for a pool on the reserved boot-disk partition. The API probe
checks that refusal alongside unchanged boot-pool status and GPT layout, and
exercises replace, offline/online, detach, and remove against the adopted pool.
Creation and adoption failures also run a guarded rollback. It captures the
new pool GUID after `zpool create`, requires that GUID and the exact reserved
PARTUUID before destroy, and stops for manual recovery when create's outcome
is ambiguous.

An operator who intentionally exports a pool with `destroy=false` must clear
its labels before the reserved partition is offered again. Use the v27
[`shared/reclaim_path_b.py`](../shared/reclaim_path_b.py) helper with the
explicit pool name, recorded PARTUUID, and expected pool GUID. It resumes after
export, checks the pool is the sole leaf on that PARTUUID, verifies mount and
boot-disk parent state before `zpool labelclear`, and accepts an already-clear
partition idempotently. See the [consumer procedure](../docs/CONSUMERS.md#reclaim-an-exported-path-b-pool).
The accepted v27 `pool.create` fields and defaults are recorded in the
[design guide](../docs/DESIGN-GUIDE.md#v27-pool-create-schema); the live probe
refuses schema drift before it starts storage mutations.

See the rationale in the [design guide](../docs/DESIGN-GUIDE.md#path-b-pool-wizard-safety)
and the exact registered live sequence in the
[consumer guide](../docs/CONSUMERS.md#path-b-v27-pool-wizard-safety).
