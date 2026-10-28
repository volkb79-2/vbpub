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
on stock and patched TrueNAS v27.0.0-RC.1 on the hosted Netcup test VM
(2026-10-27/28, see `LIVE-V1001-RUN-LOG.md`): `works`, `blocked: <why>`,
`refused`, `n/a`. `size` is the patch size: `done`, `S` (<50 lines), `M`
(<300), `L` (more, with partition-aware disk handling). `verdict`: `works`,
`patch-UI`, `API-TUI` (too large or unsafe for the product; use the API script
or TUI), `n/a` (must stay refused), `pending decision`. `v27`/`v28` hold the
latest lane result for that release (`PASS (lane)`, `FAIL`, `pending`, `-`).

## Actions

| id | path | action | API methods | API (stock) | UI (stock) | UI (patched) | patch | size | verdict | lane : step | v27 | v28 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A-01 | A | Create the `boot-pool/user-data` root dataset | `pool.dataset.create` | refused (boot-pool excluded) | blocked: boot-pool not offered | works | `storage.patch` | done | works | `live-ui` : `path-a-root-ui` | PASS | pending |
| A-02 | A | Create a child filesystem | `pool.dataset.create` | refused | blocked | works | `storage.patch` | done | works | `live-ui` : `dataset-ui` | PASS | pending |
| A-03 | A | Create a zvol | `pool.dataset.create` | refused | blocked | works | `storage.patch`, `zvol-blocksize.patch` | done | works | `live-ui` : `zvol-ui` | PASS | pending |
| A-04 | A | Add a container on `boot-pool` | `container.create`, `container.pool_choices` | refused | blocked | works | `storage.patch` | done | works | `live-ui` : `container-ui` | PASS | pending |
| A-05 | A | Create a VM on an existing zvol | `vm.create`, `vm.device.create` | refused | blocked | works | `storage.patch`, `libvirt.patch` | done | works | `live-ui` : `vm-ui` | PASS | pending |
| A-06 | A | Start/stop a container and a VM (TCG) | `container.start`, `vm.start`, `vm.stop` | refused (KVM required) | blocked | works | `libvirt.patch` | done | works | `live-ui-workloads` : `ui-action` | pending | pending |
| A-07 | A | Open Shares, add an SMB/NFS share | `pool.query`, `sharing.smb.create`, `sharing.nfs.create` | works | blocked: Shares page gates on `pool.query`, which excludes boot-pool | works | `ui-pools.patch` | S | patch-UI | `live-ui` : `shares-ui` | PASS | pending |
| A-08 | A | Export, modify, expand, replace, attach or detach boot-pool from the UI | `pool.export`, `pool.update`, `pool.expand`, `pool.upgrade`, `pool.replace`, `pool.attach`, `pool.detach`, `pool.offline`, `pool.online`, `pool.remove` | n/a | n/a | refused (synthetic row id 0 does not exist) | `ui-pools.patch` | S | n/a | `live-api-probe` : `boot-pool-unchanged` | PASS | pending |
| A-09 | A | Add cache/log/special vdev | `pool.update` | refused for boot-pool zvols | UI steps show, no valid target | refused | - | L | API-TUI | `live-vdev-topology` : - | refused by design | pending |
| B-01 | B | Reserve the free-tail partition (label `boot-disk-data`, ZFS type) | - | no method (partitioning is `sgdisk`) | not possible | not possible | - | M | API-TUI | `live-path-b-partition` : `result` | PASS | pending |
| B-02 | B | Create a pool on the reserved partition from the pool wizard | `pool.create`, `disk.details` | `pool.create` takes disk names only (`vda4` -> EINVAL "disks not found in system"); `disk.details UNUSED` empty | blocked: wizard sees no disks | works: `disk.details` offers the partition, `pool.create` runs `zpool create` on it and adopts it | `ui-pools.patch` | M | patch-UI | `live-ui` : `path-b-pool-ui`; `live-api-probe` : `shim-create-on-partition` | PASS | pending |
| B-03 | B | Create a further pool on more free space of a device | `pool.create`, `disk.details` | blocked (free space is not a disk) | blocked | works once B-01 reserved another partition; carving free space from the UI is not offered | `ui-pools.patch` | L (carving) | API-TUI | `live-api-probe` : `details-lists-reserved-partition` | pending | pending |
| B-04 | B | Datasets, zvols, containers and a VM on `boot-disk-data` | `pool.dataset.create`, `container.create`, `vm.create` | works | works | works | `storage.patch` | done | works | `live-ui` : `container-ui` | PASS | pending |
| B-05 | B | Shares on `boot-disk-data` | `sharing.smb.create` | works | works (a real pool exists) | works | - | done | works | `live-ui` : `shares-ui` | PASS | pending |
| B-06 | B | Adopt a hand-made pool on the partition | `pool.import_find`, `pool.import_pool` | works: listed and imported ONLINE, shows in `pool.query` | works (Import pool) | works | - | done | API-TUI | `live-api-probe` : `import-hand-made-pool` | PASS | pending |
| B-07 | B | Mirror the boot pool onto the partition | `boot.attach` | refused: internal TypeError (partition has no disk size) | n/a | refused (the boot pool's own partitions are never offered) | `ui-pools.patch` | - | n/a | `live-api-probe` : `boot-attach-refused-unchanged` | PASS | pending |

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

## Notes and evidence

- **Synthetic boot-pool row (A-07/A-08).** `pool.query` appends one read-only
  row (`id` 0) only for externally originated calls (`app` is set: browser,
  REST, `midclt`) while `/mnt/boot-pool/user-data` is mounted. Read on the real
  v27.0.0-RC.1 middleware source: `CRUDService.get_instance` reaches
  `<namespace>.query` through `middleware.call` without `app`; the only calls
  that forward `app=` are `system.reboot` (config/update plugins),
  `replication.create` and the websocket dispatcher itself, and none of the 26
  `pool.query`/`pool.get_instance` call sites in `plugins/` passes it. So internal
  logic never sees the row. The row is not part of `pool.query` event
  subscriptions and is cached for 5 s because the UI polls.
- **Reserved partition shim (B-02, B-07).** Only a partition with number >= 4,
  GPT name exactly `boot-disk-data`, ZFS GPT type, no filesystem signature, not
  mounted and not in any pool is offered by `disk.details`. `pool.create` accepts
  it only as the single disk of a one-disk Stripe data vdev; mixtures with other
  disks or vdev types are refused. `zpool create` never uses `-f`.
- **Alternative recorded (not built): partition-aware disk model (L).**
  Teach `disk.get_disks`, `disk.check_disks_availability`, `format_disks`,
  `pool.replace/attach/expand` and the topology validators about partitions so
  mirrors, raidz and later replacement work on partitions. Estimate: several
  hundred lines across five or more plugins plus a new live replacement lane.
- **Scrub by name.** `pool.scrub.run` takes a pool name and, measured on stock
  v27, accepts `boot-pool` regardless of the patch: a scrub is non-destructive
  and `boot.scrub` offers the same, so it is intentionally not refused. Every
  id-based and destructive mutation on the synthetic row is refused (A-08).
- **Wizard flow (B-02).** The Data step is used in its automated mode (Layout
  Stripe, the single offered disk, width 1, one VDEV); the Review step's
  preview and the stock confirmation dialog ("The contents of all added disks
  will be erased", Confirm checkbox, Continue) are driven by `live-ui`. The stock
  warning text is generic: it does not name the device, the Review step shows the
  VDEV size instead. The browser frame observer can miss the wizard's job call, so
  `live-ui` falls back to the middleware `pool.create` job record (the lane never
  calls `pool.create` itself) and asserts it names exactly `vda4`.
- **Evidence logs.** The raw live logs for these results are under the ignored
  `artifacts/` directory and are intentionally not committed (they contain host
  names and job output); the lane steps named in the table are the reproducible
  evidence.
