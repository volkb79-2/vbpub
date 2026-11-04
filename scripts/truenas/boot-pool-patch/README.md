# TrueNAS boot-pool storage patch

This experimental, exact-build middleware patch exposes carefully bounded
boot-disk capacity for user data and workloads while preserving TrueNAS's
boot-pool protections. It supports two layouts: **Path A** stores a bounded
`boot-pool/user-data` namespace inside the boot pool; **Path B** creates
`boot-disk-data` on a partition reserved in the disk's free tail. Both keep
the boot-pool partition unchanged and share the same physical failure domain.
The adapters also enable VM definitions and QEMU TCG behavior when KVM is
absent. API and UI coverage includes datasets, zvols, containers, VMs, share
records, Path A mounting, and the guarded Path B wizard.

## Supported builds

| Build | Support status | Evidence and limits |
|---|---|---|
| `27.0.0-RC.1` | Supported, exact build registered. | Path A/B API and UI acceptance passed. Update creates a stock `/usr` in a new BE; the old BE retains its patch. |
| `27.0.0-RC.2`, GA and `27.0.x` | Register each exact build as it appears and passes acceptance. | No unregistered build is accepted by a version-prefix match. |
| v27 Nightlies | One rolling pre-RC.2 bridge; retire when RC.2 is registered. | `27.0.0-MASTER+20261029-173417` is registered. Exact-source, update/reapply survival, patch checks, and API acceptance passed; the latest UI lane refused on its fresh-target guard. |
| v28 Nightlies | One rolling base, always the latest nightly. | `28.0.0-MASTER+20261029-175210` is the sole registered v28 nightly. Older builds are refused after retarget. |

For each new build, capture its ISO/update metadata and exact target hashes,
run `patch_drift.py diagnose`, review and adapt the patch hunks, run registered
API/UI and update acceptance, then register the adapter and digests in
[`supported-builds.json`](supported-builds.json). A rolling-base update changes
the one accepted nightly; a host on an older v28 nightly must update to the
registered latest build before applying the patch. The exact-build lane prints
the supported target and the required update/recheck steps when it sees an
older build.

## Patch series

Each registered adapter stores one DEP-3-style Git patch per logical change in
its `series/` directory. `supported-builds.json` remains the exact-build
registry; its ordered `patches` list and `series_dir` select the files that the
installer packages. The generated [patch-series outline](PATCH-SERIES.md)
shows each `Change-Id`, capability, role, risk, dependency, target file, hunk
count, and diffstat. The [design guide](docs/DESIGN-GUIDE.md#patch-series-and-bundle-identity)
explains the split and compatibility contract. The [consumer guide](docs/CONSUMERS.md#inspect-and-prove-a-series)
shows how to validate and adopt a series.

The legacy grouped patch files are retained under each adapter's `legacy/`
directory as byte-level proof inputs and as the frozen-build hunk bridge. They
are not read by installers. The migration proof in
[`reports/series-equivalence-2026-10-30.json`](reports/series-equivalence-2026-10-30.json)
compares a non-empty, exact set of patched files for all three registered
builds, identifies fingerprinted targets left unpatched, and confirms the
legacy and series output bytes match. The [consumer guide](docs/CONSUMERS.md#inspect-and-prove-a-series)
shows how to rebuild the stock roots and rerun the proof. Bundle members now
use `series/`, and adapter manifests and bundled readmes were updated. Those
archive byte changes alter bundle digests while installed middleware bytes
remain identical; the [design guide](docs/DESIGN-GUIDE.md#patch-series-and-bundle-identity)
records the digest history and verification command.

## Update and rollback

An update creates a new boot environment with stock `/usr`. The updater copies
the v27 per-BE record into the new `/root`, where its old digest is stale.
Path A data persists but is unmounted until its matching adapter is reapplied.
Path B pool identity and data persist. A fresh-install action refuses an
existing Path A root or `boot-disk-data` pool. The acceptance runner selects
digest-bound `reapply-after-update` when it sees the prior v27 record; it does
not take the fresh-install path on an existing Path B pool. The v28 adapter
uses the exact current build and the verified existing Path A or Path B root.
The previous BE remains available for rollback with its own patched `/usr` and
database.

Use the [TrueNAS overview](../docs/OVERVIEW.md) for the source-state/update
matrix and user workflow. The [design guide](docs/DESIGN-GUIDE.md#support-policy-and-update-state)
explains the registry, refusal, and adapter decisions. The
[consumer guide](docs/CONSUMERS.md#update-and-reapply-workflow) has pasteable
verify, dry-run, apply, post-check, and rollback commands. Runtime update
evidence and the complete acceptance matrix are in
[`UPDATE-AND-DRIFT.md`](../docs/UPDATE-AND-DRIFT.md#update-mechanics-q1) and
[its update-survival acceptance section](../docs/UPDATE-AND-DRIFT.md#update-survival-acceptance-q4), plus
[`ACCEPTANCE-RESULTS.md`](../docs/ACCEPTANCE-RESULTS.md).

The action matrix is the inventory for supported storage operations and patch
coverage: [`ACTION-MATRIX.md`](ACTION-MATRIX.md). Reclaiming an exported Path B
pool uses `pool.export` with `destroy=false`, then the guarded helper verifies
the pool GUID and partition PARTUUID, clears only that partition with
`zpool labelclear`, and confirms that `disk.details` offers it again.

The read-only `live-inspect` lane accepts `TRUENAS_STORAGE_PROFILE=path-a`,
`path-b`, or `both` and compares the selected profile with direct ZFS dataset
and pool inventory before reporting PASS. The [design guide](docs/DESIGN-GUIDE.md#read-only-storage-profile-checks)
explains the presence contract; the [consumer guide](docs/CONSUMERS.md#inspect-and-prove-a-series)
shows how to inspect a combined Path A and Path B guest.

For v27-to-v28 survival acceptance from a combined Path A and Path B source,
the runner stores and applies the v28 adapter through Path A so the new boot
environment restores Path A's automatic mount integration. Path B-only runs
keep the adapter backup on Path B. See the [design rationale](docs/DESIGN-GUIDE.md#combined-layout-update-routing)
and [consumer workflow](docs/CONSUMERS.md#update-and-reapply-workflow).

The UI path values are `ui-wizard` and `preprovisioned`. Patch actions include
`install`, `replace`, `repair-root-create`, `repair-root-parent-mountpoint`,
`repair-zvol-blocksize`, `reapply-after-update`, `install-after-update`, and
`uninstall-stale-record`; use only the action valid for the inspected BE and
storage state. The external `live-boot` lane requires
`TRUENAS_TEST_ALLOW_REBOOT=1`. The registered live-boot gate and registered
live-update gate are destructive acceptance flows; read the consumer guide
before any live mutation. To resume an already UI-created Path A root, the UI
gate uses `TRUENAS_TEST_RESUME_EXISTING_UI_ROOT` and the exact
`TRUENAS_TEST_RESUME_EXISTING_UI_DATASET`; these cannot be combined with root
provisioning.
QEMU TCG process start was verified;
guest OS boot, actual KVM execution, SMB/NFS client access, real second-disk
auxiliary-vdev behavior, and production fault tolerance remain unverified.

## Test gate

Run the registered unit gate before changing adapters:

```bash
cd scripts/truenas
run-gate unit --worktree "$(git rev-parse --show-toplevel)" --allow-dirty
```

The gate runs in `tester-unified`. Host acceptance is separate and mutating;
use the registered scenario runner only against its configured disposable
target. See [`accept.py`](acceptance/accept.py). External TrueNAS host runs
require an explicit `--docker-network` for the verified tester bridge; use
`--allow-high-psi` only under the documented external-run guard.
