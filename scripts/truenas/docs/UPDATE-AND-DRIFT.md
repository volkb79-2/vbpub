# TrueNAS update, drift, and survival record

This record answers the update mechanics, compatibility, patch-size, survival,
and automation questions for the boot-pool patch. Runtime evidence is text only
under `/home/vscode/vbpub-release-program-20261027/evidence/truenas-live-20261030/`.
The point-in-time host and official-feed evidence is
`update-and-drift-20261030.txt` in that directory.

## Update mechanics (Q1)

The installer changes middleware files inside the active boot environment's
`/usr` dataset. On v1001, `/usr` was mounted from
`boot-pool/ROOT/<active-BE>/usr`, filesystem type `zfs`, with mount option `ro`;
the adapter scripts temporarily clear the dataset's `readonly` property and
restore it after their transactional apply. The patched Python targets are
under `/usr/lib/python3/dist-packages/middlewared/...`; adapter helpers are
installed under that same package root.

The update did **not** overwrite those patched files in place. TrueNAS verified
`rootfs.squashfs`, extracted the update, migrated the configuration database,
and updated initramfs/GRUB. It created and activated a new boot environment
with a separate `usr` dataset from the shipped image. The previous BE remained
available and retained the patch. I activated the previous v27 BE, booted it,
verified all 19 v27 target files and its install record, then reactivated v28.
`boot.environment.query` reported the old BE `can_activate=true`; the actual
rollback boot confirmed it.

The v27 install marker is `/root/v27-boot-pool-patch-install.json`, inside the
BE-specific `root` dataset. The update copied that dataset snapshot into the
new BE, so the marker survived with the old v27 version and bundle digest. It
was stale because it described the previous `/usr`, not the active new BE's
stock `/usr`. The v28 install manifest is stored on the selected Path A or
Path B data root instead. Both records are claims that must be checked against
their exact target and helper hashes.

`/root`, `/data`, and `/usr` are separate datasets under each BE. The updater
clones/snapshots the old BE's `/root` and `/data` into the new BE and migrates
the new copy of `/data/freenas-v1.db`; it does not make one database live
across all BEs. The v27 and v28 BEs had different database hashes after
migration. Rolling back returns to the older BE's own database state. ZFS
user-data pools such as `boot-pool/user-data` and `boot-disk-data` are outside
`boot-pool/ROOT`, so their datasets, properties, snapshots, clones, zvols, and
bytes persist across the BE switch. Their mount behavior is still controlled
by the active BE's middleware hooks: Path A was present but unmounted after an
update until the matching adapter was applied.

| Fact | Evidence |
|---|---|
| `/usr` is per-BE read-only ZFS, not a live SquashFS mount | `v27-rc1-patched-be-before-update-20261030.txt` and `v27-new-be-before-reapply-20261030.txt` (dataset sources, mount properties, and per-BE MD5/SHA-256 inventories) |
| Updater verifies/extracts `rootfs.squashfs` and creates a new BE | `v1001-rc1-to-v27-nightly-update-r2b.log`, `v1001-v27-to-v28-update-reboot-r2.log` |
| Old v27 BE still boots with its patch and can be reactivated | `v1001-rc1-bootenv-rollback-readiness-r2.log` and `v1001-activate-rc1-and-reboot-r2.log` in `truenas-live-20261029/`; per-BE hashes in `v27-new-be-before-reapply-20261030.txt` |
| `/root` marker is stale in the new BE; data DB is copied/migrated per BE | `v27-new-be-before-reapply-20261030.txt` and `v27-nightly-patched-be-after-reapply-20261030.txt` |
| Path A/B pools survive but auto-mount depends on the active adapter | prior v27/v28 update logs; `live-boot` and reapply logs in the same evidence directory |

## Drift guards and unknown builds (Q2)

The explicit supported set is [`supported-builds.json`](../boot-pool-patch/supported-builds.json):

| Build | Adapter directory | Fingerprinted targets |
|---|---|---:|
| `27.0.0-RC.1` | `v27` | 18 |
| `27.0.0-MASTER+20261027-173422` | `v27-nightly-20261027` | 19 |
| `28.0.0-MASTER+20261014-230204` | `v28-nightly-20261014` | 15 |
| `28.0.0-MASTER+20261027-175229` | `v28-nightly-20261027` | 19 |

The v27 entries also pin the installed bundle digests: RC.1
`23e7667f4893f26f4861b17edc20e73f00ae2f8b797f9ff95cde446af1f25de1`, and
Oct 27 Nightly
`489be0c005e4e64cff1cf81077418546b8c9a03cd77bfafa2711048a52a1c0fe`.

There is no v27 RC.2, v27 GA, or later nightly adapter in that registry. Version
prefix matching is not compatibility. Before apply, the remote installer
checks the exact build and adapter metadata, checks the complete target list
against SHA-256 preimages, and runs every patch through GNU `patch` with
`--dry-run --fuzz=0`. It refuses missing paths, symlinks, hash changes, a
manifest digest/count mismatch, or a patch anchor failure. Post-apply checks
include compiled Python, API smoke calls, installed target/helper hashes, and
reverse zero-fuzz patch dry-runs; the `/usr` dataset returns to read-only.
These checks prevent GNU patch from silently applying a hunk at a nearby
location in an unregistered source tree.

Local read-only commands:

```bash
cd scripts/truenas
python3 boot-pool-patch/acceptance/patch_drift.py verify \
  --version 27.0.0-MASTER+20261027-173422 \
  --root /path/to/extracted/usr/lib/python3/dist-packages

python3 boot-pool-patch/acceptance/patch_drift.py diagnose \
  --candidate-version 27.0.0-RC.2 \
  --reference-build 27.0.0-MASTER+20261027-173422 \
  --root /path/to/rc2/usr/lib/python3/dist-packages

python3 boot-pool-patch/acceptance/patch_drift.py upstream \
  --channel 27-Nightlies \
  --reference-build 27.0.0-MASTER+20261027-173422
```

`verify` accepts only an exact registered build. `diagnose` is for a candidate
tree: it reports every `MISSING`/`HASH` target and each zero-fuzz anchor result,
returns review-required for an unregistered version, and never edits the tree.
`upstream` checks the official nightly/RC listing and compares middleware
sources with the closest exact adapter. The TrueNAS download host renders its
Storj listing in JavaScript; the checker uses PWMCP only when raw HTML contains
no ISO names. It also checks `truenas_pylibvirt` in its own official source
repository. These recurring checks do not install on v1001 or automatically
register a new adapter.

On 2026-10-30 the official 27 nightly listing later reported
`TrueNAS-27.0.0-MASTER+20261029-173417.iso`, newer than the Oct 27 adapter and
therefore unsupported. The TrueNAS v27 updater API advertised
`28.0.0-MASTER+20261029-175210` as its latest v28 manifest; the live lane
rejected the stale Oct 27 request and selected the Oct 29 build. Neither build
is in the exact-build registry. PWMCP could not render the current v28
Nightlies listing (503) or v27 RC listing (selector timeout), so the recurring
check reports an error instead of inferring that either feed is empty. Prior
feed evidence from earlier on Oct 30 listed v27 RC.1 only; RC.2/GA remain
unregistered and were not re-inspected against a new image. The current
middleware `master` and `truenas_pylibvirt` refs differ from the adapter
fingerprints and produce anchor failures, so the correct result is
`REVIEW_REQUIRED`; it is not evidence that the published image has that source
commit.

## Patch inventory and smaller variants (Q3)

Patch statistics below count unified-diff hunks and added/deleted source lines;
helper files are listed separately. Each patch's `+++ b/...` entries and `@@`
headers are the exact file and source-line anchors. There are no React/Angular
webui files and no systemd unit edits: `ui-pools.patch` changes middleware API
handlers used by the UI.

| Build | Patch area | Files / hunks | Lines | What it changes and risk |
|---|---|---:|---:|---|
| v27 RC.1 | boot mount (`boot-mount.patch`) | 1 / 3 | +181/-0 | `pool_/import_pool.py`; Path A dataset mount discovery. High data availability; depends on `boot_pool_user_data.py`. |
| v27 nightly | boot mount | 1 / 3 | +181/-0 | Same Path A hook for the nightly's changed source. |
| v28 Oct14 / Oct27 | boot mount | 1 / 2 each | +8/-0 each | `pool_/import_pool.py`; separate `boot_pool_mount.py` helper owns v28 mount logic. |
| v27 RC.1 | middleware/VM (`middleware.patch`) | 1 / 4 | +6/-5 | `vm/info.py`; advertise QEMU TCG support. Medium VM behavior risk; depends on libvirt/storage device patch. |
| v27 nightly | middleware/VM and boot attach | 2 / 5 | +7/-5 | Adds `boot.py` reserved-partition attach refusal plus `vm/info.py` TCG behavior. High boot-pool safety and medium VM risk. |
| v28 Oct14 / Oct27 | middleware/VM | 3 / 6 each | +8/-8 each | `vm/__init__.py`, `vm/crud.py`, `vm/info.py`; v28 TCG validation. Medium VM risk. |
| v27 RC.1 | storage/API (`storage.patch`) | 11 / 40 | +322/-52 | `async_validators.py`; `container/{container,dataset,image,lifecycle,utils}.py`; `pool_/dataset.py`, `pool_/dataset_query_utils.py`; `vm/vm_devices.py`; `zfs/utils.py`; `utils/libvirt/storage_devices.py`. High data-boundary risk, medium container/VM risk. |
| v27 nightly | storage/API | 11 / 37 | +320/-51 | Same safety areas, with nightly `container/container.py` and its exact anchors. |
| v28 Oct14 | storage/API | 11 / 35 | +300/-45 | `async_validators.py`; `container/{crud,dataset,info,lifecycle,query_pull_images,utils}.py`; `pool_/dataset.py`; `zfs/{encryption_info,utils}.py`; `utils/libvirt/storage_devices.py`. High data-boundary risk. |
| v28 Oct27 | storage/API | 11 / 31 | +289/-39 | `async_validators.py`; `container/{crud,dataset,info,lifecycle,query_pull_images,utils}.py`; `pool_/{dataset,dataset_query_utils}.py`; `zfs/utils.py`; `utils/libvirt/storage_devices.py`. High data-boundary risk. |
| v27 RC.1 / nightly | pool API/UI shim (`ui-pools.patch`) | 3 / 6 each | +81/-1 each | `pool_/pool.py`, `disk_/availability.py`, `pool_/export.py`. Path A synthetic pool row and Path B partition chooser; refuses destructive boot-disk wipe. High Path B safety, medium UI availability. |
| v28 Oct27 | pool API/UI shim | 3 / 6 | +83/-1 | Same three middleware handlers, on exact v28 source. |
| v27 RC.1 / nightly | libvirt (`libvirt.patch`) | 1 / 2 each | +2/-1 each | `truenas_pylibvirt/domain/vm/xml.py`; TCG VM XML behavior. Medium virtualization risk. |
| v27 RC.1 / nightly | zvol default (`zvol-blocksize.patch`) | 1 / 1 each | +5/-0 each | `pool_/dataset_info.py`; boot-pool zvol block size. Low data-layout risk; remove if no boot-pool zvol use. |
| v28 Oct14 / Oct27 | all patch files | 15 / 43; 18 / 45 | +316/-53; +388/-48 | Latest v28 manifest fingerprints 19 files; `zfs/encryption_info.py` is hash-only on Oct27, not patch-touched. |

Totals: v27 RC.1 is 18 files, 56 hunks, +597/-59; v27 Oct27 is 19 files,
54 hunks, +596/-58; v28 Oct14 is 15 files, 43 hunks, +316/-53; v28 Oct27
patches 18 files while fingerprints 19, 45 hunks, +388/-48. Adapter helpers
add 1,007 lines for v27 (`boot_pool_user_data.py` 165,
`boot_disk_partition.py` 747, `virtualization_support.py` 24,
`pool_create_schema_v27.json` 71); 374 lines for v28 Oct14 (165 + 24 +
`boot_pool_mount.py` 185); and 1,163 lines for v28 Oct27 (those v28 helpers
plus `boot_disk_partition.py` 718 and `pool_create_schema_v28.json` 71).

The inventory is anchored to each version's unified diff. A `patch:line`
below is the physical `+++ b/<target>` line; the `@@` headers in that same
file give the upstream source-line coordinates. This keeps v27 and v28
locations separate rather than implying that source line numbers carry across
releases.

| Build | Changed-file locations and patch evidence (`patch:line`) |
|---|---|
| v27 RC.1 | Boot mount: `v27/boot-mount.patch:2` → `pool_/import_pool.py`; VM: `v27/middleware.patch:2` → `vm/info.py`; storage: `v27/storage.patch:2,111,145,331,397,448,505,525,562,610,685` → `async_validators.py`, `pool_/dataset_query_utils.py`, `pool_/dataset.py`, `container/{container,dataset,utils,image,lifecycle}.py`, `vm/vm_devices.py`, `utils/libvirt/storage_devices.py`, `zfs/utils.py`; UI: `v27/ui-pools.patch:2,103,116` → `pool_/pool.py`, `disk_/availability.py`, `pool_/export.py`; libvirt: `v27/libvirt.patch:2`; zvol: `v27/zvol-blocksize.patch:2`. Hunk counts by patch: 3, 4, 40, 6, 2, 1. |
| v27 Oct 27 | Boot mount: `v27-nightly-20261027/boot-mount.patch:2`; VM and boot attach: `middleware.patch:2,44` → `vm/info.py`, `boot.py`; storage: `storage.patch:2,109,175,226,246,283,338,514,548,596,616` → `async_validators.py`, `container/{container,dataset,image,lifecycle,utils}.py`, `pool_/{dataset,dataset_query_utils}.py`, `vm/vm_devices.py`, `zfs/utils.py`, `utils/libvirt/storage_devices.py`; UI: `ui-pools.patch:2,103,116`; libvirt: `libvirt.patch:2`; zvol: `zvol-blocksize.patch:2`. Hunk counts by patch: 3, 5, 37, 6, 2, 1. |
| v28 Oct 14 | Boot mount: `v28-nightly-20261014/boot-mount.patch:2`; VM: `middleware.patch:2,22,46` → `vm/{__init__,info,crud}.py`; storage: `storage.patch:2,101,155,229,266,312,342,403,562,585,615` → `async_validators.py`, `container/{dataset,info,lifecycle,query_pull_images,crud,utils}.py`, `pool_/dataset.py`, `zfs/{encryption_info,utils}.py`, `utils/libvirt/storage_devices.py`. Hunk counts by patch: 2, 6, 35. |
| v28 Oct 27 | Boot mount: `v28-nightly-20261027/boot-mount.patch:2`; VM: `middleware.patch:2,22,46`; storage: `storage.patch:4,108,120,176,252,291,313,376,534,567,595` → `async_validators.py`, `container/{crud,dataset,info,lifecycle,query_pull_images,utils}.py`, `pool_/{dataset,dataset_query_utils}.py`, `zfs/utils.py`, `utils/libvirt/storage_devices.py`; UI: `ui-pools.patch:4,19,38` → `disk_/availability.py`, `pool_/export.py`, `pool_/pool.py`. Hunk counts by patch: 2, 6, 31, 6. `zfs/encryption_info.py` is the nineteenth fingerprint target, not a patched file. |

| Goal | Smallest safe component set and exact touched files | What is omitted or lost |
|---|---|---|
| Path A, API only, filesystem datasets/shares | `boot-mount.patch` → `middlewared/plugins/pool_/import_pool.py`; helper `middlewared/plugins/boot_pool_user_data.py`; storage hunks in `middlewared/async_validators.py`, `middlewared/plugins/pool_/dataset.py`, `middlewared/plugins/pool_/dataset_query_utils.py`, `middlewared/plugins/zfs/utils.py`. | No VM/container support, no boot-pool zvol tuning, and no UI pool row. If zvols are required, add `pool_/dataset_info.py`; if VMs are required, add the exact VM/libvirt/storage-device components. |
| Path B, API only, create/adopt reserved partition pool | `middlewared/plugins/boot.py` attach refusal; the reserved-partition `pool.create` hunk in `middlewared/plugins/pool_/pool.py`; destructive-export refusal in `middlewared/plugins/pool_/export.py`; validation/dataset guards in `middlewared/async_validators.py`, `middlewared/plugins/pool_/dataset.py`, `middlewared/plugins/zfs/utils.py`; helper `middlewared/plugins/boot_disk_partition.py`; exact `pool_create_schema_v27.json` or `pool_create_schema_v28.json`. | No Path A boot-mount hook. Direct API calls pass validated PARTUUID; dropping `disk_/availability.py` means the UI wizard cannot discover the reserved partition. |
| Path A UI | Add only the synthetic boot-pool `pool.query` hunk in `middlewared/plugins/pool_/pool.py` to Path A API set. | No frontend build files are required; the UI consumes the middleware row. |
| Path B UI wizard | Add `middlewared/plugins/disk_/availability.py` pseudo-disk hunk and reserved-partition `pool.create` hunk in `pool_/pool.py`; keep `pool_/export.py` safety refusal. | This is optional if pool creation is API-only or a guarded provisioner creates the pool first. |
| VM/container/zvol goals | Keep the matching version's `container/*`, `vm/*`, `vm_devices.py`, `storage_devices.py`, `virtualization_support.py`, and, for v27, `truenas_pylibvirt/domain/vm/xml.py`; add `dataset_info.py` for v27 zvol blocksize. | Dropping these removes VM/TCG, container, or boot-pool zvol behavior as the corresponding files are removed. |

These are carve proposals, not installable partial bundles. `ui-pools.patch`
mixes a Path A synthetic row with Path B wizard and wipe-safety hunks; create a
new split adapter, exact target manifest, and new gate evidence before shipping
any smaller set.

## Update-survival acceptance (Q4)

[`accept-update-survival.py`](../accept-update-survival.py) is a three-step
registered workflow. `plant` creates bounded test objects and stores a
canonical `midclt` baseline; `update` selects the exact newest update manifest
on one train and performs `update.run`; `verify` compares the new canonical
dump, account password hashes, and file/zvol SHA-256 values. Host-free tests
cover redaction, root-pool selection, expected differences, manifest safety,
update-feed selection, and report comparison. The unit gate result is recorded
with the live evidence.

| Data/config category | Plant or comparison | Coverage rule |
|---|---|---|
| Users, groups, password, SSH key, API key | Create unique group/user, store only a password-hash digest and API-key digest; verify public key and record IDs through `midclt`. | Password hash is compared from `/etc/shadow`; raw one-time API key is never written to the manifest. |
| Path A, Path B, non-reserved pool | Create unique child datasets under each selected root when present. | Path A/B are explicit profile values. Non-reserved pool is planted only if imported; absence is reported, never substituted. |
| Nested datasets and files | Parent/nested filesystem with quota/refquota, compression, recordsize, marker files, snapshot, and clone. | Compare dataset query properties and SHA-256. |
| Zvol and VM record/device | Create a 64 MiB zvol, write a probe, create stopped VM and attach that zvol. | Compare zvol bytes, VM record, and device record; KVM/guest boot is not required. |
| SMB/NFS shares | Create disabled records pointing at the fixture path. | Middleware record survival only; no client protocol connection. |
| Scheduled tasks | Create disabled cron, periodic snapshot, and local replication records. | Cloud-sync definitions/credentials are inventoried if available, but no remote task/credentials are created. |
| Certificates, apps, containers | Create a short-lived local certificate; plant a stopped container only if registry image and eligible pool exist; capture app records. | App records are captured; no app is deployed. Container absence is an explicit coverage result. |
| Network/system settings | Capture `network.configuration.config`, `system.general.config`, `system.advanced.config`, and NTP records before and after. | They are not changed because doing so could sever host access; this proves unchanged configuration, not a separately edited network setting. |
| Encryption | Not created. | A disposable encryption key escrow is not configured; do not create encryption without a recoverable test key. |

Permitted canonical differences are `system.version`, active BE name, selected
Path A/B auto-mount state, the v27 install-record version/bundle when the
matching v27 adapter is reapplied, and the VM `video_model` default when TCG
support changes it to `VGA`. For the observed patched RC.1→Oct27 nightly only,
the Path A fixture zvol may lose its default 67 MiB `refreservation` while its
64 MiB size, 16 KiB block size and data checksum remain fixed; the verifier
binds that exception to the exact source and target versions, dataset identity,
and baseline geometry. It accepts either middleware query representation of
the same fixture zvol, and no other dataset/property. Runtime DNS server order
is canonicalized as a set; dataset accounting counters and reservation source
provenance are omitted while effective property values are still compared.
The update controller also switches `update.config.profile` from
`EARLY_ADOPTER` to `DEVELOPER` when moving from an RC to a Nightly; this is a
controller-driven change recorded and verified in
`update-profile-transition-20261030.txt`, outside the user-setting canonical
dump. On a v27→v28 stock update, Path A's middleware dataset rows are absent
because the new BE does not have the v27 namespace shim. The verifier drops
only those rows from both sides of that cross-series comparison and still
compares their direct ZFS properties and all file/zvol checksums. The container
API also changes the v27 `default_network` field to a generated v28 NIC device;
the normalizer retains and compares `nic_attach` while excluding its new
generated id/MAC. Other container devices remain compared. Any other canonical
path, missing record, or data checksum change fails.

Acceptance outcomes:

| Scenario | Result | Evidence |
|---|---|---|
| Vanilla RC.1 → Oct 27 v27 Nightly | PASS; 7 file/zvol checksums; 4 expected differences. Path A plus ordinary `survival-data`; Path B unavailable. | `vanilla-plant-20261030.log`, `vanilla-update-20261030.log`, `vanilla-verify-20261030.log` |
| Patched RC.1 → Oct 27 v27 Nightly → guarded reapply | PASS; 7 checksums; 9 expected differences; no unexpected changes. Path A + Path B. | `patched-survival-verify-20261030.log`, per-BE inventories listed in `update-and-drift-20261030.txt` |
| Patched Oct 27 v27 Nightly → latest v28 Nightly `28.0.0-MASTER+20261029-175210` | PASS; 7 checksums; 3 expected differences; no unexpected changes. Path A rows hidden from middleware query but present in direct ZFS; Path A unmounted, Path B mounted, v27 marker remains stale. | `v27-to-v28-oct29-update-20261030.log`, `v28-survival-plant-20261030.log`, `v28-survival-verify-20261030.log`, `v28-survival-report-20261030.txt` |

The Oct 27 v28 update request was rejected because the update API advertised a
newer Oct 29 build; see `v28-rejected-stale-oct27-target-20261030.log`. The
Oct 29 v27 Nightly is also newer than the registered v27 adapter, so it has not
been patched or run through a full survival fixture.

The combined Path A + Path B + ordinary non-reserved pool case was not
physically possible on this one-disk v1001: Path B consumes the remaining disk
tail. Non-reserved pool coverage came from the separate vanilla run. Encryption
escrow was unavailable; no VM guest was booted; SMB/NFS were records only.
Full logs and feed evidence are under
`/home/vscode/vbpub-release-program-20261027/evidence/truenas-live-20261030/`.

## Automation inventory (Q5)

| Acceptance check | Runner and interface | Determinism | Remaining judgement and automation plan |
|---|---|---|---|
| `unit-boot-pool-patch` | `./run-gate.py` → `tester-unified` → pytest | Deterministic assertions, no host mutation. | None at run time; keep mutation/refusal oracles in unit tests. |
| `patch_drift.py verify` / `diagnose` | Python hash/manifest checks plus GNU `patch --dry-run --fuzz=0` on an extracted source tree | Deterministic for the exact bytes. | None to report drift. Human must review semantic changes before creating a new registered adapter. |
| `patch_drift.py upstream` | Python fetches official feed and source files; PWMCP renders Storj listings when raw HTML is only a shell | Deterministic for the fetched listing/commit; feed and `master` move over time. | Human decides whether source diffs affect the patch contract; the command reports each hash/missing target and anchor. |
| `live-inspect`, `live-api-probe`, `live-storage`, `live-boot`, `live-vdev-topology` | Python/SSH, `midclt` WebSocket JSON-RPC, ZFS/partition commands, explicit guards | Script assertions and exact inventory; host availability is external. | Guest OS boot/KVM and share-client checks need suitable hardware/network, not interpretation. Add deterministic lanes when those resources exist. |
| `live-v27-patch`, `live-v28-patch`, Path B provision/partition lanes | Python/shell patch drivers, exact build/method schema/hash checks, direct ZFS evidence | Exact version, hashes, topology, and API records; host mutations guarded. | Unknown-build semantics still require source review. Never let review judgement add a build to the registry implicitly. |
| `live-update-survival-{plant,update,verify}` | Python/SSH, `midclt`, `update.available_versions`/`update.run`, ZFS and SHA-256 | Exact latest train build, canonical snapshot paths, per-object hashes, explicit allowed-diff set. Update profile is selected from middleware choices by train. | Human only chooses which train to test. Missing pools, key escrow, or unavailable APIs are reported as coverage gaps. v27→v28 stock middleware omits Path A from `pool.dataset.query`; direct ZFS properties and checksums remain deterministic. |
| `live-ui-auth`, `live-ui-prepare-ssh`, `live-ui`, `live-ui-observed-v1001`, `live-ui-workloads` | PWMCP browser selectors/ARIA snapshots plus captured middleware requests and direct storage checks | Selector counts, field values, payloads, and ZFS outcomes are asserted by scripts; no LLM participates in the lane. | When an upstream DOM changes, judgement selects the new semantic locator. Improve this by capturing accessible names/DOM context, requiring exactly one match, pinning target build, and comparing the UI request payload with the middleware schema; fail closed when ambiguous. |

The full automation list and current coverage are also linked from the
[README feature surface](../boot-pool-patch/README.md#current-status), the
[design rationale](../boot-pool-patch/docs/DESIGN-GUIDE.md#v27-nightly-update-and-reapply),
and the [consumer workflow](../boot-pool-patch/docs/CONSUMERS.md#run-update-survival-acceptance).
