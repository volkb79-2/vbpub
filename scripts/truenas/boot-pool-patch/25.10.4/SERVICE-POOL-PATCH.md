# Service-pool patch: Virt/Incus and Docker

## Scope

This document covers the proposed change that lets the TrueNAS 25.10.4 Virt/Incus and Docker services use boot-pool for their managed storage:

- Virt stores Incus state and instances under POOL/.ix-virt.
- Docker stores application data under POOL/ix-apps and expects the runtime path /mnt/.ix-apps.
- This does not expose a general user dataset such as boot-pool/appdata in every storage picker.

The source host had boot-pool on a single SSD and a four-disk RAIDZ1 pool pv1. The original instruction was not to modify pv1.

## Architecture and observed blockers

Incus manages system containers and VMs. Incus uses QEMU/KVM for VM execution; it is not a replacement for KVM. The newer virt.* middleware path is distinct from the older vm.* path. Docker apps are another independent service.

The investigation identified these boot-pool-specific assumptions:

1. virt.global.pool_choices excluded names in BOOT_POOL_NAME_VALID. Its subsequent pool.dataset.get_instance_quick lookup also failed because the higher-level dataset API hides boot-pool.
2. docker.fs_manage rejected a mounted source whose dataset name began with boot-pool/.
3. Docker's default ix-apps mountpoint assumes the data-pool altroot behavior. A data pool imported under /mnt can use the ZFS mountpoint property /.ix-apps to reach /mnt/.ix-apps; boot-pool has no such altroot.
4. Docker custom dataset properties were keyed by the short dataset name, while some callers supplied a full pool/dataset name.
5. Docker migration validation looked up the destination root through a pool.dataset API that hides boot-pool.
6. Docker backup-target validation also used a pool.dataset query that hides boot-pool.

These findings are based on the 25.10.4 source and observations recorded in the original analysis. Recheck them against the exact middleware build before applying the patch.

## Proposed patch map

| File | Intended change |
|---|---|
| virt/global.py | Permit boot-pool in the Virt choices and inspect its root dataset through zfs.dataset.query for lock state. |
| docker/state_utils.py | Derive the ix-apps mountpoint property from the pool and resolve custom properties by dataset basename. |
| docker/state_setup.py | Apply the pool-aware properties when creating the ix-apps root dataset. |
| docker/fs_manage.py | Stop treating boot-pool sources as unmounted and use the full dataset name consistently. |
| docker/migrate.py | Apply the target pool's custom properties during ix-apps migration. |
| docker/update.py | Query destination-root encryption information through zfs.dataset.query. |
| docker/backup_to_pool.py | Query a backup target root through zfs.dataset.query. |

The proposed unified diff is `truenas-25.10.4-boot-pool-virt-docker.patch`.
The source snapshots used to make it were removed during consolidation; fetch
the exact original files from middleware commit `e36f4f8318` before attempting
to reproduce the patch.

## What the available evidence says

The historical analysis says read-only calls saw both pools through the ZFS layer while pool.dataset lookups hid boot-pool. It also records that the seven original files matched the live files and that local syntax and diff checks passed. These are historical claims, not a fresh verification of the present tree or another TrueNAS installation.

The historical README records a direct docker.update call returning boot-pool/ix-apps and says the Containers Global Settings form accepted boot-pool and created Virt datasets. The latest operator report says all expected UI actions did not work. These observations are not reconciled, and neither establishes that the intended VM/container UI workflows work end to end. The overall UI acceptance status is **failed/unverified** pending a careful reproduction.

The earlier review called the patch ready and low risk. The independent analysis also identified mountpoint concerns during dataset replication and existing-dataset updates. Given those concerns and the reported UI failure, the earlier readiness recommendation is withdrawn.

## Open implementation concerns

- A migration replication may restore the source mountpoint over the intended boot-pool mountpoint. Verify the final target property after migration.
- The update path may skip changes to the mountpoint of an existing ix-apps dataset. Confirm whether the later mount-point correction always runs and succeeds.
- General host-path-to-dataset checks may still reject paths on boot-pool.
- Generic dataset pickers and validators may continue to hide boot-pool. The service-pool patch only changes the listed service code paths.
- API-level acceptance does not prove that the UI offers, saves, and uses the setting correctly.
- No compatibility claim is made for TrueNAS 27 or later.

## Historical application notes

The old README documented remounting /usr read-write, applying the patch from / with patch -p1, restarting middlewared, then returning /usr to read-only. Keep that only as historical procedure. It is not a validated runbook and should not be followed on a production host until a tested backup, rollback, exact-version check, and UI/API acceptance process exist.

A dry-run of the patch and comparisons against the retained source files are appropriate before any controlled reproduction. Preserve a live copy of every target file before changing it. Do not alter pv1.

## Risks

Boot-pool use is unsupported upstream. Workloads can increase SSD writes and consume space shared with boot environments; the recorded boot pool is a single device. Generic filtering intentionally protects system datasets, and broad changes to that filtering could expose internal boot datasets in unrelated UI/API flows. A TrueNAS update may replace the patched middleware.
