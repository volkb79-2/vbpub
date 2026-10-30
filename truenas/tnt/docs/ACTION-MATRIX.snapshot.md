<!-- Snapshot of scripts/truenas/boot-pool-patch/ACTION-MATRIX.md from branch truenas-restore-iso, taken 2026-10-28 (vbpub@89504a321 base). The canonical copy lives on that branch until it merges; refresh this file from it, do not edit it here. Only host identifiers were genericised. -->

# Storage action matrix (Path A / Path B)

ONE table, one row per operator action, stable ids. It is the canonical action
backlog: the web UI, the middleware API and the patch must all be able to do
each row, or the row's verdict says why a script/TUI towards the API is the
fallback instead. A future TUI project consumes this table as machine-readable
input (a markdown table, `|` separated, ids `A-xx` for Path A and `B-xx` for
Path B, method names in backticks, `-` for "none").

`tests/test_action_matrix.py` keeps this file in sync with the code:

- every patch file and lane named here exists, every `*.patch` in the release
  directories is in the patch inventory, and every UI-affecting v27 patch is
  used by a row;
- every `lane : step` names a `report("step"...)` tag of that lane's source;
- every API method named here appears in the acceptance code, every method that
  `live_api_probe.py` calls is named here, and every storage-exercising
  `live-ui` step has a row.

Columns. `API (stock)`, `UI (stock)` and `UI (patched)` are what was measured
on stock and patched TrueNAS v27.0.0-RC.1 on the hosted throwaway test VM
(2026-10-27/28, see the live run log): `works`, `blocked: <why>`,
`refused`, `n/a`. `size` is the patch size: `done`, `S` (<50 lines), `M`
(<300), `L` (more, with partition-aware disk handling). `verdict`: `works`,
`patch-UI`, `API-TUI` (too large or unsafe for the product; use the API script
or TUI), `n/a` (must stay refused), `pending decision`. `v27` holds the latest
lane result (`PASS (lane)`, `FAIL`, `pending`, `-`).

## Actions

| id | path | action | API methods | API (stock) | UI (stock) | UI (patched) | patch | size | verdict | lane : step | v27 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A-01 | A | Create the `boot-pool/user-data` root dataset | `pool.dataset.create` | refused (boot-pool excluded) | blocked: boot-pool not offered | works | `storage.patch` | done | works | `live-ui` : `path-a-root-ui` | PASS (live-ui) |
| A-02 | A | Create a child filesystem | `pool.dataset.create` | refused | blocked | works | `storage.patch` | done | works | `live-ui` : `dataset-ui` | PASS (live-ui) |
| A-03 | A | Create a zvol | `pool.dataset.create` | refused | blocked | works | `storage.patch`, `zvol-blocksize.patch` | done | works | `live-ui` : `zvol-ui` | PASS (live-ui) |
| A-04 | A | Add a container on `boot-pool` | `container.create`, `container.pool_choices` | refused | blocked | works | `storage.patch` | done | works | `live-ui` : `container-ui` | PASS (live-ui) |
| A-05 | A | Create a VM on an existing zvol | `vm.create`, `vm.device.create` | refused | blocked | works | `storage.patch`, `libvirt.patch` | done | works | `live-ui` : `vm-ui` | PASS (live-ui) |
| A-06 | A | Start/stop a container and a VM (TCG) | `container.start`, `vm.start`, `vm.stop` | refused (KVM required) | blocked | works | `libvirt.patch` | done | works | `live-ui-workloads` : `ui-action` | pending on the hosted VM |
| A-07 | A | Open Shares, add an SMB/NFS share | `pool.query`, `sharing.smb.create`, `sharing.nfs.create` | works | blocked: Shares page gates on `pool.query`, which excludes boot-pool | works | `ui-pools.patch` | S | patch-UI | `live-ui` : `shares-ui` | PASS (live-ui) |
| A-08 | A | Export or modify boot-pool from the UI | `pool.export`, `pool.update` | n/a | n/a | refused (synthetic row id 0 does not exist: ENOENT) | `ui-pools.patch` | S | n/a | `live-api-probe` : - | PASS (live-api-probe) |
| A-09 | A | Add cache/log/special vdev | `pool.update` | refused for boot-pool zvols | UI steps show, no valid target | refused | - | L | API-TUI | `live-vdev-topology` : - | refused by design |
| B-01 | B | Reserve the free-tail partition | - | no method (partitioning is `sgdisk`) | not possible | not possible | - | M | API-TUI | `live-path-b-partition` : - | PASS (live-path-b-partition) |
| B-02 | B | Create `boot-disk-data` from the pool wizard | `pool.create`, `disk.details`, `disk.query` | blocked: `pool.create` takes disk names only (`vda4` -> EINVAL "disks not found in system"); `disk.details UNUSED` is empty | blocked: wizard sees no disks | blocked | - | L | pending decision | `live-ui` : `path-b-partition-ui-inventory` | FAIL (measured) |
| B-03 | B | Create a further pool on free space of a device | `pool.create`, `disk.details` | blocked (same: free space is not a disk) | blocked | blocked | - | L | pending decision | `live-api-probe` : - | FAIL (measured) |
| B-04 | B | Datasets, zvols, containers and a VM on `boot-disk-data` | `pool.dataset.create`, `container.create`, `vm.create` | works | works | works | `storage.patch` | done | works | `live-ui` : `container-ui` | PASS (live-ui) |
| B-05 | B | Shares on `boot-disk-data` | `sharing.smb.create` | works | works (a real pool exists) | works | - | done | works | `live-ui` : `shares-ui` | PASS (live-ui) |
| B-06 | B | Adopt a hand-made pool on the partition | `pool.import_find`, `pool.import_pool` | works: listed and imported ONLINE, shows in `pool.query` | works (Import pool) | works | - | done | API-TUI | `live-api-probe` : - | PASS (live-api-probe) |
| B-07 | B | Mirror the boot pool onto the partition | `boot.attach` | refused: `boot.attach vda4` raises an internal TypeError (partition has no disk size) | n/a | n/a | - | - | n/a | `live-api-probe` : - | PASS (live-api-probe) |

## Patch inventory

Every patch file below must exist and every patch file in the release
directories must appear here.

| file | release | affects UI |
|---|---|---|
| `v27/middleware.patch` | v27 | no |
| `v27/storage.patch` | v27 | yes |
| `v27/libvirt.patch` | v27 | yes |
| `v27/boot-mount.patch` | v27 | no |
| `v27/zvol-blocksize.patch` | v27 | yes |
| `v27/ui-pools.patch` | v27 | yes |
