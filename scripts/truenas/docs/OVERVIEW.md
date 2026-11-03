# TrueNAS boot-disk storage patch

This experimental middleware patch relaxes selected boot-pool protections so a
single-disk TrueNAS system can use bounded space for user data, VM disks, and
container storage. It keeps the boot-pool partition and TrueNAS boot-environment
layout intact. The current exact-build registry is
[`../boot-pool-patch/supported-builds.json`](../boot-pool-patch/supported-builds.json).

## Goals and paths

| Path | Storage layout | What the patch enables |
|---|---|---|
| **Path A** | `boot-pool/user-data`, a bounded dataset tree inside the boot pool. | Managed datasets, zvols, VM definitions, container storage, share records, and a boot hook that mounts eligible descendants. The partition table does not change. |
| **Path B** | `boot-disk-data`, a separate ZFS pool on an explicitly reserved free-tail partition. | Partition-only pool creation through the API and pool wizard, followed by managed datasets, zvols, VMs, containers, and share records. The boot-pool partition does not change. |

Both paths use the same physical disk. Path B separates ZFS pool identity but
does not add disk-failure redundancy. A separate middleware adapter allows VM
creation and process start through QEMU TCG when `/dev/kvm` is absent; TCG is
slow and does not provide hardware virtualization.

## Supported builds and observed behavior

Compatibility is exact-build only. The adapters verify every target's SHA-256
preimage and run every patch with GNU `patch --dry-run --fuzz=0`. A version
prefix or a nearby nightly is not accepted.

| TrueNAS build | Support and results | Known limits |
|---|---|---|
| `27.0.0-RC.1` | Registered. Path A and Path B API/UI acceptance passed. | Guest OS boot under TCG, SMB/NFS client access, and real second-disk cache behavior are not covered. |
| `27.0.0-MASTER+20261029-173417` | Registered as the one v27 nightly bridge while RC.2 is absent. Exact source verification, Path A update/reapply survival, patch checks, and API acceptance passed. | The latest UI lane hit the fresh-target guard and refused the survival run's existing VM record before making UI changes. Its Playwright pin now matches PWMCP. A clean-target UI run remains open; the Oct 27 UI pass is historical evidence only. |
| `28.0.0-MASTER+20261029-175210` | Registered as the sole rolling v28 nightly. Its 19 exact source fingerprints and four patch dry-runs pass. The v28 adapter live run remains unverified; the RC.1→v28 update stopped before BE activation because the boot pool lacked 0.53 GiB of required free space. | A later nightly replaces this entry only after retargeting and acceptance. Older v28 nightlies are refused. |
| v27 RC.2, GA, and later `27.0.x` | Not registered until an exact build passes the registration process. | Release-prefix matching does not extend support. |

RC.1 remains the v27 release base. We keep one rolling v27 nightly in scope as a
pre-RC.2 bridge because the updater currently advertises it and the v27-to-v28
update path needs a tested v27 source. Retire that bridge when RC.2 is
registered. v28 has a different policy: only its latest nightly is supported.
To move the v28 base, capture the new build, run `patch_drift.py diagnose`,
adapt and review every changed hunk, run exact-source and live acceptance, then
replace the single v28 row in the registry. A host on a retired v28 nightly is
refused with the current build and update instructions; do not install the
current adapter against another build's source.

## Update effects by source state

An update creates a new boot environment (BE) with stock `/usr`. The previous
BE keeps its patched `/usr` and its own configuration database. The v27
install record lives in `/root` inside each BE; the updater copies it to the
new BE, where its old version and bundle digest are stale. Path A data lives
outside the BE and survives, but its middleware mount hook is absent until the
matching adapter is reapplied. A Path B pool stays on its reserved partition
and retains its GUID and data; the active BE still needs the matching adapter
for managed behavior.

| Source state | Update to a registered v27 build | Update to the registered v28 rolling build |
|---|---|---|
| Vanilla | New BE has stock `/usr`; there is no patch record or managed root. | New BE has stock `/usr`; there is no patch record or managed root. |
| Path A patched | New BE has stock `/usr`; copied v27 `/root` record is stale. Path A data remains but is unmounted until v27 reapply. | New BE has stock `/usr`; copied v27 `/root` record is stale. Path A data remains but is unmounted until the v28 adapter is applied. |
| Path B patched | New BE has stock `/usr`; copied v27 record is stale. Path B pool and data stay present. Use the digest-bound `reapply-after-update` action; fresh install refuses because `boot-disk-data` already exists. | New BE has stock `/usr`; Path B pool and data stay present. Apply the exact registered v28 adapter using the Path B profile. |
| Path A + Path B patched | New BE has stock `/usr`; copied v27 record is stale. Path A is unmounted pending reapply; Path B stays. Reapply only after both roots and the old record are verified. | New BE has stock `/usr`; Path A stays unmounted until the v28 adapter is applied, while Path B stays present. Verify both roots and their mount state afterward. |

The v27 fresh-install refusal that blocked an earlier Path B acceptance was
correct: a boot-disk-data pool is an existing target, so the appropriate v27
operation is `reapply-after-update` with the exact prior bundle digest. The
parametrized runner selects that action from the inspected source record. The
matrix records each source state and target build; an unrun cell remains
unverified. Restore the previous boot environment for rollback; it retains its
matching patched `/usr` and database.

## What remains vanilla

The patch leaves the update engine, BE creation and selection, boot-pool
partition size, and internal system datasets under TrueNAS control. It does
not resize the boot partition, assign the whole boot disk to user data, or
mount arbitrary boot-pool descendants. Path A only exposes its managed
namespace. Path B only accepts the explicitly reserved partition. Destructive
operations that could erase the parent boot disk continue to refuse. The
adapters patch middleware used by the UI; they do not replace frontend
bundles or systemd units. Keeping these components vanilla avoids changing
upgrade, boot, and whole-disk safety behavior outside the storage relaxation.

The patch does not claim production readiness. TCG guest boot, real KVM,
second-disk L2ARC/SLOG/special-vdev behavior, share-client connections,
performance, endurance, and disk-failure tolerance remain unverified.

## User workflow: update, reapply, verify, or roll back

1. Confirm the target is in `supported-builds.json`. Check the updater feed
   with `patch_drift.py upstream --channel 27-Nightlies` or
   `--channel 28-Nightlies`. If it advertises a newer build than the
   registered adapter, stop: that build is not supported yet. Record the
   active version, Path A/B pool identities, and a restorable system snapshot.
2. Apply the update through **System Settings → Update** to the registered
   exact build, then reboot into the new BE. Do not treat a copied marker as
   proof that the new `/usr` is patched.
3. Run `live-inspect` and check the active version, BE, pool state, and
   read-only `/usr`. On a captured or extracted package tree, verify stock
   preimages and patch anchors:

   ```bash
   cd scripts/truenas
   python3 boot-pool-patch/acceptance/patch_drift.py verify \
     --version 28.0.0-MASTER+20261029-175210 \
     --root /path/to/extracted/usr/lib/python3/dist-packages
   ```

   For an unregistered candidate, `diagnose --candidate-version VERSION
   --reference-build REGISTERED_BUILD --root PATH` reports drift only; it does
   not authorize installation.
4. Reapply through the registered live lane. For v27 use
   `reapply-after-update` when the copied `/root` record's bundle digest is an
   allowed prior digest. Never use the fresh-install action when Path A or
   `boot-disk-data` already exists. For v28 use `live-v28-patch` with the
   exact registered build and the selected `path-a` or `path-b` profile.
5. Run the matching API, UI, and reboot/import checks. Verify target and
   helper hashes, install record, `/usr` read-only state, Path A mounts, and
   Path B GUID/PARTUUID. An install record alone is not evidence.
6. If an update or reapply fails, use the TrueNAS Boot Environments UI or
   console to activate the previous BE. It retains its matching patched
   `/usr` and database. Verify the restored version and pools before deleting
   either BE or touching pool labels.

For repeatable scenarios, use
[`acceptance/accept.py`](../boot-pool-patch/acceptance/accept.py) with
`--from <exact build, ISO, or catalogued snapshot>`, `--start-state
{vanilla,path-a,path-b,path-a+b}`, `--to <registered build>`,
`--patched-target {none,reapply}`, and optional
`--steps plant,update,verify,ui,api`. The `matrix` command regenerates the
[acceptance index](ACCEPTANCE-RESULTS.md).

When continuing UI/API checks from a completed update, pass the exact source
version (for example `--from 27.0.0-RC.1`) and the planted manifest from the
earlier run. A catalogued snapshot name means restore that snapshot first; it
is for a fresh source-state run, not a continuation on the target BE.

## Design decisions and evidence

The exact-build registry, loud refusal, and digest-bound adapters prevent a
nearby middleware source tree from receiving a plausible but misplaced hunk.
`master` source comparison is advisory: a nightly update manifest does not
publish the middleware Git commit used to build it. The authoritative check is
the complete SHA-256 set captured from that exact build plus zero-fuzz dry-run
and installed-file verification.

See the [design guide](../boot-pool-patch/docs/DESIGN-GUIDE.md#support-policy-and-update-state)
for rationale and the [consumer guide](../boot-pool-patch/docs/CONSUMERS.md#update-and-reapply-workflow)
for runnable commands. The [update/drift evidence](UPDATE-AND-DRIFT.md#update-mechanics-q1)
records what the updater changed, source-drift findings, and PWMCP diagnostics;
its [update-survival acceptance section](UPDATE-AND-DRIFT.md#update-survival-acceptance-q4)
records the survival comparison.
