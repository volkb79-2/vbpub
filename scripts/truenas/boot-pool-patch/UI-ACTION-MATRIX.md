# UI action matrix (Path A / Path B)

Canonical list of the storage actions an operator expects from the TrueNAS web
UI on a patched host, what works, what each needs from the relaxation patch,
and which live lane proves it. `tests/test_ui_action_matrix.py` keeps this file
and the code in sync: every patch file and lane named here must exist, every
patch file in `v27/` and `v28-nightly-20261014/` must be listed in the patch
inventory, every `step` must be a `report("...")` tag in the named lane's
source, and every `ui-*` lane step that exercises an action must have a row.

Verdict values: `works` (no change needed), `patch-UI` (middleware patch makes
the stock UI work), `API-TUI` (too large or unsafe to change in the product;
use the API script/TUI fallback, not a UI change), `n/a` (must stay refused).
Columns `v27` and `v28` hold the result for that release: `PASS (lane)`,
`FAIL`, `pending`, or `-` (not applicable). "Measured" results come from the
2026-10-27/28 runs on the hosted Netcup test VM (see `LIVE-V1001-RUN-LOG.md`).

## Actions

| id | path | action | UI today (measured) | patch hunk / file | size | verdict | lane : step | v27 | v28 |
|---|---|---|---|---|---|---|---|---|---|
| A-01 | A | Create the `boot-pool/user-data` root dataset | works | `storage.patch` | done | works | `live-ui` : `path-a-root-ui` | PASS (live-ui) | pending |
| A-02 | A | Create a child filesystem | works | `storage.patch` | done | works | `live-ui` : `dataset-ui` | PASS (live-ui) | pending |
| A-03 | A | Create a zvol | works | `storage.patch`, `zvol-blocksize.patch` | done | works | `live-ui` : `zvol-ui` | PASS (live-ui) | pending |
| A-04 | A | Add a container on `boot-pool` | works | `storage.patch` | done | works | `live-ui` : `container-ui` | PASS (live-ui) | pending |
| A-05 | A | Create a VM using an existing zvol | works | `storage.patch`, `libvirt.patch` | done | works | `live-ui` : `vm-ui` | PASS (live-ui) | pending |
| A-06 | A | Start/stop container and VM | works (earlier local guests) | `libvirt.patch` | done | works | `live-ui-workloads` : `ui-action` | pending on v1001 | pending |
| A-07 | A | Open Shares, add SMB/NFS share | blocked: page gates on `pool.query`, which excludes boot-pool | `ui-pools.patch` | S (one synthetic read-only row) | patch-UI | `live-ui` : `shares-ui` | FAIL before patch; pending after | pending |
| A-08 | A | Export/disconnect/expand boot-pool from the UI | n/a: synthetic row has id 0, mutations refuse | `ui-pools.patch` | - | n/a | `live-ui` : `shares-ui` | pending | pending |
| A-09 | A | Add cache/log/special vdev | UI steps show, API refuses boot-pool zvols | - | L | API-TUI | `live-vdev-topology` : - | refused by design | pending |
| B-01 | B | Reserve the free-tail partition | not possible in UI | - | M | API-TUI | `live-path-b-partition` : - | PASS (live-path-b-partition) | pending |
| B-02 | B | Create `boot-disk-data` from the pool wizard | blocked: `disk.query` lists whole disks, `disk.get_unused` is empty | - | L (partition-aware format/create/replace) | pending decision | `live-ui` : `path-b-partition-ui-inventory` | FAIL (measured) | pending |
| B-03 | B | Create a further pool on free space of a device | blocked (same cause) | - | L | pending decision | `live-ui` : `path-b-partition-ui-inventory` | FAIL (measured) | pending |
| B-04 | B | Datasets, zvols, containers, VM on `boot-disk-data` | works (preprovisioned) | `storage.patch` | done | works | `live-ui` : `container-ui` | PASS (live-ui) | pending |
| B-05 | B | Shares on `boot-disk-data` | works (a real pool exists) | - | done | works | `live-ui` : `shares-ui` | PASS (live-ui) | pending |
| B-06 | B | Provision `boot-disk-data` outside the UI | not a UI action | - | done | API-TUI | `live-path-b-provision` : - | PASS (live-path-b-provision) | pending |

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
| `v28-nightly-20261014/boot-mount.patch` | v28 | no |
| `v28-nightly-20261014/middleware.patch` | v28 | yes |
| `v28-nightly-20261014/storage.patch` | v28 | yes |
