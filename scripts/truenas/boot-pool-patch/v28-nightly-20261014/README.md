# TrueNAS 28 nightly TCG middleware adapter

**Status: guarded patch application/replacement, Path A reboot, Path A and
Path B storage acceptance, and Path B partition/pool provisioning passed. Both
storage profiles now pass adversarial destroy/snapshot checks against an
existing container-managed dataset and verify that it survives.** This
adapter targets nightly
`28.0.0-MASTER+20261014-230204`. The ISO does not
identify the middleware Git commit. It has three adapters: `storage.patch`
changes eleven storage/container targets, `middleware.patch` changes three VM TCG
gates, and `boot-mount.patch` adds the reboot mount hook to
`pool_/import_pool.py`. All 15 patch targets were fingerprinted against the
ISO's embedded rootfs. The registered guarded replacement passed its zero-fuzz
dry-run, applied all three patches, compiled the changed modules, restarted
middleware, and restored `/usr` to read-only. The shared storage and
virtualization helpers are installed separately. The latest registered unit
gate passed with 106 tests and 45 subtests.

A registered `live-v28-patch` lane passed on the updated v28 Path A guest on
2026-10-17. It verified the exact installed target hashes, saved original
files on `boot-pool/user-data`, stopped middleware, applied the adapter with
zero fuzz, compiled the changed modules, restored `/usr` to read-only, and
checked the patched API. The successful output is saved at
[`live-v28-patch-20261017.log`](../artifacts/live-v28-patch-20261017.log).
The updated bundle also passed guarded replacement on 2026-10-17; its log is
[`live-v28-patch-replacement-20261017.log`](../artifacts/live-v28-patch-replacement-20261017.log).
The v27-to-v28 update itself completed on that guest, and the user-data marker
bytes survived; the update gate mounted the saved datasets manually before
hashing them, so that result does not test v28 automatic remount. The first
post-reboot storage run found `container.pool_choices` empty. Middleware logs
showed the boot-mount helper called `ZFSResourceService.query_impl`, which v28
does not expose; the service's method is `list_impl`. The helper and storage
validator now use `list_impl`. Guarded replacement passed on 2026-10-17 and
verified the patched API/TCG checks; its latest log is
[`live-v28-patch-replacement-list-impl-20261017.log`](../artifacts/live-v28-patch-replacement-list-impl-20261017.log).
The first reboot-gate attempt then stopped before reboot because the test used
unsupported `zfs unmount -r`; it cleaned up its probe dataset. The gate now
unmounts the mounted dataset list one child at a time, deepest first. That
corrected reboot path passed on 2026-10-17: the user-data root and marker child
remounted, the marker hash and boot-pool health matched, `container.pool_choices`
included `boot-pool`, and cleanup removed both temporary datasets. The passing
log is
[`live-boot-v28-path-a-20261017.log`](../artifacts/live-boot-v28-path-a-20261017.log).
The failed attempt is
[`live-boot-v28-unmount-option-20261017.log`](../artifacts/live-boot-v28-unmount-option-20261017.log).

### Path B partition and pool provisioning

The registered `live-path-b-provision` lane passed on a separate v28 guest
with a 20 GiB system disk and a 16 GiB boot-pool partition. It preserved
partitions 1–3, created `/dev/vda4` in the 3.5 GiB free tail, and registered
`boot-disk-data` ONLINE through middleware. A repeat invocation confirmed the
pool was already configured and made no changes. Its log is
[`live-path-b-provision-v28-path-b-20261017.log`](../artifacts/live-path-b-provision-v28-path-b-20261017.log).

The first full patched Path B `live-storage` run passed dataset create/query/
update, zvol create/grow and VM disk-choice checks, temporary file-backed
L2ARC add/remove, and TrueNAS VM startup with the QEMU TCG backend. It then
found that `zfs.resource.destroy` returned JSON `null` for an existing dataset
under `boot-disk-data/.truenas_containers/containers/`. The original failure
is preserved at
[`live-storage-v28-path-b-protection-refusal-failure-20261017.log`](../artifacts/live-storage-v28-path-b-protection-refusal-failure-20261017.log).
A read-only inventory is at
[`guest-inspection-v28-path-b-20261017.log`](../artifacts/guest-inspection-v28-path-b-20261017.log).
It found no test-specific container dataset, middleware VM or container
records, or libvirt domains; the managed roots and Alpine image cache remained,
and both pools were ONLINE. Since `finally` cleanup also ran, that inventory
does not determine whether destroy or cleanup removed the test dataset.

Tracing showed `.truenas_containers` was missing from v28's non-boot-pool
internal path list. The adapter now uses the shared path policy. The registered
Path B rerun refused destroy and snapshot creation against the existing
managed dataset, checked that it survived and no snapshot appeared, then
passed public clone refusal, container persistence, shares, and cleanup. Its
log is
[`live-storage-v28-path-b-existing-container-protection-pass-20261017.log`](../artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log).

The first attempt refused before disk mutation because v28 formats `sgdisk`
sector size as `logical/physical`, while the parser expected only `logical`.
The parser and a blank partition-name case now have unit coverage. The
corrected unit result is
[`unit-boot-pool-patch-path-b-sgdisk-format-20261017.log`](../artifacts/unit-boot-pool-patch-path-b-sgdisk-format-20261017.log);
the original refusal is recorded at
[`live-path-b-provision-v28-sgdisk-parse-failure-20261017.log`](../artifacts/live-path-b-provision-v28-sgdisk-parse-failure-20261017.log).

## Change from v27

The inspected v28 source already chooses a QEMU domain when `/dev/kvm` is
absent. It does not need the v27 `truenas_pylibvirt/domain/vm/xml.py` change.
The v28 middleware has three KVM-only gates instead of v27's two:

- `vm.virtualization_details()` in `vm/info.py` controls the Web UI capability
  result.
- `vm.supports_virtualization()` in `vm/__init__.py` reports the same gate.
- `vm/crud.py` calls `kvm_supported()` directly during VM validation.

The patch redirects those decisions through the shared
[`virtualization_support.py`](../shared/virtualization_support.py). KVM remains
preferred when TrueNAS detects it. Otherwise TCG is advertised only if the
host-architecture QEMU executable is found. The hardware CPU flags in
`vm/info.py` remain KVM-only.

The v28 source has the same boot-pool remount gap found and fixed on v27:
`pool.import_on_boot` imports pools registered in `storage.volume`, while the
boot pool is not registered there, and then runs `pool.post_import`. The
candidate `boot-mount.patch` mounts only a quota-bounded
`boot-pool/user-data` root and safe descendants before that hook. It uses
v28's `INTERNAL_PATHS`, `CONTAINER_DS_NAME`, and boot-pool import path. The
registered v28 Path A reboot gate verified the adapter on a running guest: the
root and marker child remounted, and the marker hash matched. The extracted
rootfs contains `zfs-mount.service` but has no
`/etc/systemd/system/zfs.target.wants/zfs-mount.service` enablement link; the
custom boot-pool hook supplies the tested mount path independently of that
disabled generic unit.

The ISO's `truenas_pylibvirt/utils/__init__.py` and
`truenas_pylibvirt/domain/vm/xml.py` match upstream commit
`ebd20df3cc2ae15fe08e7943478f54175d172382`. The first checks `/dev/kvm`; the
second selects `domain type="qemu"` when KVM is unavailable. The ISO also
contains `/usr/bin/qemu-system-x86_64` and `/usr/bin/qemu-system-aarch64`. This
confirms the source-level backend and binary expected by the helper. Registered
v28 Path A and Path B storage gates started middleware VMs with TCG. Selecting
KVM when `/dev/kvm` is available still needs a separate host test.

## ISO target fingerprints

These hashes were computed from the selected ISO's embedded rootfs. They also
match the candidate source snapshot used to generate the patch. They identify
these exact patch targets, not a generic v28 source revision or the complete
middleware checkout.

| Source file | SHA-256 |
|---|---|
| `middlewared/plugins/vm/__init__.py` | `0a3e809977bdbb3f98e259394b919f2448f2482564e54a2af76ad626552ba7ac` |
| `middlewared/plugins/vm/info.py` | `866d683be9c87058d99a5dba7a8323cbc8c95ed3540433e0bcd2cec4138e2c65` |
| `middlewared/plugins/vm/crud.py` | `68928d3d9bef7d9bd30b622f2ea181dd0565531f4289057778d7ae23f0cc04aa` |

The table lists representative fingerprints. The complete list of all 15
storage, VM, and boot-mount patch targets is in
[`targets.sha256`](targets.sha256).

## Patch check against extracted ISO targets

For source validation only, from the middleware source root containing
`middlewared/`:

```sh
sha256sum -c /workspaces/vbpub/scripts/truenas/boot-pool-patch/v28-nightly-20261014/targets.sha256
patch --dry-run --batch -p1 < /workspaces/vbpub/scripts/truenas/boot-pool-patch/v28-nightly-20261014/storage.patch
patch --dry-run --batch -p1 \
  < /workspaces/vbpub/scripts/truenas/boot-pool-patch/v28-nightly-20261014/middleware.patch
patch --dry-run --batch -p1 \
  < /workspaces/vbpub/scripts/truenas/boot-pool-patch/v28-nightly-20261014/boot-mount.patch
```

The shared helpers are `shared/boot_pool_user_data.py` (installed as
`middlewared/plugins/boot_pool_user_data.py`) and
`shared/virtualization_support.py` (installed as
`middlewared/plugins/vm/virtualization_support.py`). Install the release-specific
`boot_pool_mount.py` beside `import_pool.py` as
`middlewared/plugins/pool_/boot_pool_mount.py`. Apply the patches and compile
the changed files only in a disposable source copy. This validates extracted
source targets; it is not a live installation recipe. Registered live gates
have passed for guarded patching, Path A reboot remount, Path A and Path B
storage, and Path B provisioning. KVM selection, automatic remount and broader
data survival through update, full UI workflows, client-side shares, and guest
OS boot remain open.

To repeat the extraction and fingerprint check, with the ISO tools installed:

```sh
ISO=/path/to/TrueNAS-28.0.0-MASTER+20261014-230204.iso
xorriso -osirrox on -indev "$ISO" -extract /TrueNAS.update /tmp/truenas28.update
unsquashfs -cat /tmp/truenas28.update rootfs.squashfs \
  > /tmp/truenas28-rootfs.squashfs
for file in middlewared/plugins/vm/__init__.py \
            middlewared/plugins/vm/info.py \
            middlewared/plugins/vm/crud.py; do
  unsquashfs -cat /tmp/truenas28-rootfs.squashfs \
    "usr/lib/python3/dist-packages/$file" | sha256sum
done
```

Compare these hashes with the table above. `TrueNAS.update` is a SquashFS
container with a nested `rootfs.squashfs`; the outer filesystem is not the
installed middleware filesystem.

## Required next checks

The update test is complete. A fresh v27 COW clone ran `update.manual` job 63
to success, then rebooted into v28 build
`28.0.0-MASTER+20261014-230204`; `boot-pool` remained healthy and the
user-data marker hash matched. Job 65 is only the earlier interrupted attempt
at 49% extraction and remains associated with its separate preserved COW state.
The update guest currently runs in the configured background container at the
200% CPU, 3.5 GiB memory/swap cap with 3 GiB guest RAM. The v28 boot briefly
reached its memory cap and caused reclaim events, but it was not OOM-killed.
Run each live gate against one target at a time and verify host and slice
headroom before it starts. The local runner can host up to three guests when
its per-start cgroup preflight admits them. The full update record is
[`live-update-v27-to-v28-20261017.log`](../artifacts/live-update-v27-to-v28-20261017.log).

1. Add a second virtual disk and test the Path A or Path B zvol against a
   disk-backed target pool. Check cache add, status, remove, and reboot/import
   ordering against that separate target; the existing tests use file-backed
   target pools.
2. Use PWMCP to verify the v28 UI's pool choices, dataset paths, container
   selector, VM wizard submission, and share actions.
3. Verify KVM is still preferred on a separate host with `/dev/kvm`; this TCG
   guest cannot establish that path.
4. Test automatic remount and patched-data survival through update. The
   completed v27-to-v28 update proves the marker bytes survived, but the gate
   mounted the data manually before verifying it.
5. Run Path B reboot/import acceptance on the reserved-tail guest. Its
   provision and storage gates passed, but Path B import after reboot has not
   been verified on this nightly.

The first v28 `live-storage` attempt stopped before mutation because the API
query omitted the managed Path A namespace root despite its valid ZFS mount
configuration and container pool choice. A later create failed with `ENOENT`:
the v28 query walker stopped at `boot-pool` because it was classified as
internal. The patch now makes only the exact pool root traversable, while
system and container-managed descendants remain hidden and mutation guards
remain active. The storage preflight now requires the Path A namespace root
and new user-owned descendants to be queryable; Path B's ordinary pool root is
also required. Protected-path refusal is checked against direct ZFS inventory
so an empty API response cannot certify that behavior.

The full v28 Path A `live-storage` lane passed on 2026-10-17. It verified
dataset/zvol operations, L2ARC add/remove on a temporary file-backed target,
TCG VM startup, container image clone and persistent dataset, public clone
refusal, NFS/SMB share-record CRUD, and cleanup. The public clone test proves
the new internal bypass did not make `pool.snapshot.clone` accept a
container-managed destination. The lane did not use a second disk-backed target.
Its log is
[`live-storage-v28-path-a-container-clone-pass-20261017.log`](../artifacts/live-storage-v28-path-a-container-clone-pass-20261017.log).

The narrow same-pool clone adapter restores v27's private
`snapshot.clone_impl(..., bypass=True)` and explicit mount only at the internal
container call site. Its guarded replacement migrated from 14 to 15 targets
and passed with bundle SHA-256
`2a4fa86979d7234c5aa16f1b03e0f93225c35ae10ddc3a114d1bb9e7ac680a06`.
Middleware restarted and `/usr` returned to read-only. The replacement log is
[`live-v28-patch-replacement-container-clone-bypass-20261017.log`](../artifacts/live-v28-patch-replacement-container-clone-bypass-20261017.log).
The unit log is
[`unit-boot-pool-patch-v28-container-clone-20261017.log`](../artifacts/unit-boot-pool-patch-v28-container-clone-20261017.log).
The earlier three-call bundle passed guarded replacement with SHA-256
`6925688f93ba9af31afc3609cd8a93c47fb9fbd19c31866d85dc6ea44436ba25`;
its recorded replacement is
[`live-v28-patch-replacement-three-bypasses-20261017.log`](../artifacts/live-v28-patch-replacement-three-bypasses-20261017.log).

Recheck all target hashes on every v28 build; do not infer compatibility from
the version string alone. The registered patch, storage, reboot, and Path B
provisioning gates pass for this inspected nightly. Keep the adapter
experimental until the remaining update, UI, KVM, client-sharing, guest-boot,
and separate-disk cache checks have direct evidence.
