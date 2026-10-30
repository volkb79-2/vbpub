# Using the boot-pool patch

This guide covers patch-specific API, UI, update, and reboot acceptance. Use the
[shared TCG guest guide](../../docs/CONSUMERS.md#run-a-local-truenas-tcg-guest)
for guest creation and common credentials setup. Live lanes mutate their
selected disposable TrueNAS target; inspect the target and required guards
before running them.

## Supported-build policy

The exact-build registry is [`supported-builds.json`](../supported-builds.json).
Its current scope is v27 RC.1, v27 RC.2/GA/27.0.x as each exact build passes
registration, one rolling v27 nightly as the pre-RC.2 bridge, and one rolling
v28 base equal to the latest registered nightly. The current registered
nightlies are `27.0.0-MASTER+20261029-173417` and
`28.0.0-MASTER+20261029-175210`. Check the registry and
[`ACCEPTANCE-RESULTS.md`](../../docs/ACCEPTANCE-RESULTS.md) before applying.
A feed entry is only a candidate; exact source hashes, zero-fuzz hunks, and
acceptance must pass before registration.

To register a build, capture its update metadata and full middleware target
hashes, run `patch_drift.py diagnose`, adapt and review every changed hunk, run
the registered exact-build acceptance, then add its adapter and target digest
to the registry. v27 keeps one latest nightly only while RC.2 is absent. v28
keeps only the latest nightly; the previous v28 registration is removed when a
new base passes acceptance. An older v28 build is refused with the current
base and update instructions. Never apply a current adapter to an older source
by version-prefix guesswork.

The registered lane names are `unit`, `unit-boot-pool-patch`, `live-storage`,
`live-boot`, `live-update`, `live-update-survival-plant`,
`live-update-survival-update`, `live-update-survival-verify`, `live-v27-patch`,
`live-v28-patch`, `live-path-b-provision`, `live-path-a-provision`,
`live-path-b-partition`, `live-api-probe`, `live-ui`, `live-ui-auth`,
`live-ui-prepare-ssh`, `live-ui-observed-v1001`, `live-ui-workloads`,
`live-vdev-topology`, and `live-inspect`.

## Update and reapply workflow

1. Check `supported-builds.json` and the current update feed. If the feed
   advertises a build newer than the registered target, stop until its adapter
   has been retargeted and accepted. Record the current TrueNAS version, BE,
   Path A dataset, Path B pool GUID/PARTUUID, and a restorable system snapshot.
2. Apply the TrueNAS update in **System Settings → Update** to the registered
   exact build, then reboot into the new BE. The updater creates stock `/usr`;
   it does not copy patched middleware. The previous BE keeps its own patched
   `/usr` and database.
3. Use the registered `live-inspect` lane to confirm the active build, BE,
   pool inventory, and read-only `/usr`. Capture the active middleware package
   tree over the pinned SSH connection and verify the exact stock preimages and
   zero-fuzz patch hunks:

   ```bash
   set -euo pipefail
   cd scripts/truenas
   : "${TRUENAS_TEST_HOST:?set the inspected target address}"
   : "${TRUENAS_TEST_PORT:?set the inspected SSH port}"
   : "${TRUENAS_TEST_USER:?set the inspected SSH user}"
   : "${TRUENAS_TEST_KEY:?set the registered SSH identity path}"
   : "${TRUENAS_TEST_KNOWN_HOSTS:?set the pinned known_hosts path}"
   scratch=$(mktemp -d)
   trap 'rm -rf -- "$scratch"' EXIT
   ssh -i "$TRUENAS_TEST_KEY" -p "$TRUENAS_TEST_PORT" \
     -o UserKnownHostsFile="$TRUENAS_TEST_KNOWN_HOSTS" \
     -o StrictHostKeyChecking=yes \
     "$TRUENAS_TEST_USER@$TRUENAS_TEST_HOST" \
     'tar -C /usr/lib/python3/dist-packages -cf - middlewared truenas_pylibvirt' \
     | tar -C "$scratch" -xf -
   python3 boot-pool-patch/acceptance/patch_drift.py verify \
     --version 28.0.0-MASTER+20261029-175210 --root "$scratch"
   ```

   Substitute the exact registered v27 build when targeting v27. For a new
   candidate, `diagnose --candidate-version VERSION --reference-build BUILD
   --root PATH` is read-only; its `REVIEW_REQUIRED` result blocks installation.
4. Apply the matching adapter through its registered lane. After a v27 update,
   the copied `/root/v27-boot-pool-patch-install.json` may be stale. If its
   digest is an accepted prior bundle, use the guarded
   `reapply-after-update` action. The harness inspects the prior record and
   chooses that action automatically. Do not use fresh v27 `install` when
   `boot-pool/user-data` or `boot-disk-data` already exists: it refuses those
   roots because it has no prior patch-state contract. For an exact v28 build,
   use `live-v28-patch` with `TRUENAS_STORAGE_PROFILE=path-a` or `path-b`.
   Each lane checks the exact preimages, bundle, mounted root, and `/usr`
   protection before changing middleware.

   Example for the current v28 rolling base:

   ```bash
   export TRUENAS_EXPECTED_VERSION=28.0.0-MASTER+20261029-175210
   export TRUENAS_STORAGE_PROFILE=path-a # or path-b when that is the selected root
   export TRUENAS_TEST_ALLOW_MUTATIONS=1
   export TRUENAS_V28_PATCH_ACTION=install
   : "${TRUENAS_DOCKER_NETWORK:?set the verified custom tester bridge}"
   export DOCKER_HOST=unix:///var/run/docker.sock
   python3 boot-pool-patch/run-live-gate.py live-v28-patch \
     --docker-network "$TRUENAS_DOCKER_NETWORK" --allow-dirty
   ```

   For v27 reapply, set `TRUENAS_V27_PATCH_ACTION=reapply-after-update`,
   `TRUENAS_TEST_ALLOW_PATCH_REAPPLY=1`, and derive
   `TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256` from the copied install record.
   Do not type a guessed digest. When the v27 record is absent, use
   `install-after-update` only after the lane verifies the existing root and
   the absence of conflicting patch state.
5. Run the matching API, UI, and reboot/import lanes. Verify target and helper
   hashes, the install record, `/usr` read-only state, Path A mount state, and
   Path B pool GUID/PARTUUID. The record alone does not certify installed code.
6. If update or reapply fails, activate the previous BE through the TrueNAS
   Boot Environments UI or console. That BE retains its own patched `/usr` and
   database. Confirm the restored version and pool state before removing a BE
   or changing pool labels.

Run a complete recorded scenario with the parametrized
[`acceptance/accept.py`](../acceptance/accept.py) entry point.
It restores the selected source snapshot, plants the bounded workload, updates,
chooses the correct v27 post-update operation, reapplies when requested, and
verifies. API and UI checks can be added:

```bash
python3 boot-pool-patch/acceptance/accept.py \
  --from tn-v27rc1-path-a-patched-clean-20261030 --start-state path-a \
  --to 28.0.0-MASTER+20261029-175210 --patched-target reapply \
  --steps plant,update,verify,api
python3 boot-pool-patch/acceptance/accept.py matrix
```

The command takes `--start-state` and `--patched-target`; `--steps` selects
`plant`, `update`, `verify`, `ui`, and `api`. Closed start-state values are
`vanilla`, `path-a`, `path-b`, and `path-a+b`;
patch-target values are `none` and `reapply`; workflow steps are `plant`,
`update`, `verify`, `ui`, and `api`. `--from` accepts an exact build, a
versioned ISO path, or a catalogued snapshot. An ISO path is used only to
identify its exact build; the runner does not install it. `--restore-snapshot`
can pin the same catalogued source explicitly. The runner writes a result JSON
and regenerates [`ACCEPTANCE-RESULTS.md`](../../docs/ACCEPTANCE-RESULTS.md).

The survival plant deliberately creates a stopped VM record and data tree. The
UI lane's fresh-target guard refuses that state before making browser changes;
do not bypass the guard or combine `ui` with those survival steps. Run `live-ui`
as a separate registered lane on a clean, patched target when collecting UI
evidence. The current matrix records the combined UI refusal explicitly; a
clean-target UI execution still needs to be added to the acceptance results.

## Run the patch unit gate

Run the registered patch test lane from this worktree before changing or
applying a release adapter:

```bash
cd scripts/truenas
source "$VENV/bin/activate"
export DOCKER_HOST=unix:///var/run/docker.sock
./run-gate.py unit-boot-pool-patch --worktree "$(git rev-parse --show-toplevel)" --allow-dirty
```

This runs the boot-pool patch policy, adapter artifact, and live-gate contract
tests in `tester-unified`. It does not boot or mutate a TrueNAS guest; use the
separate guarded live lanes for runtime behavior.

## Run live storage acceptance

The live lanes make changes to one selected disposable guest, and
`live-boot` reboots it. Run mutating gates sequentially against that target.
The local runner can host up to three guests when its cgroup PSI preflight
admits each one. The wrapper starts a short-lived
`tester-unified:local` container in `$CGROUP_PARENT_DEV_GATES`, attaches it to
the target VM's verified custom bridge network, mounts this worktree read-only,
and checks its 0.5 CPU / 512 MiB limits. The VM itself remains under
`$CGROUP_PARENT_DEV_BACKGROUND` with 200% CPU and 3.5 GiB memory/swap caps.
The wrapper refuses a non-runner container, default Docker network, missing
gates slice, unreadable credentials, wrong version/profile, or missing
mutation/reboot guards.

For the Path A v27 test guest, configure SSH access and run the registered
reboot lane:

```bash
cd scripts/truenas
export TRUENAS_TEST_PORT=2222
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/.vm/v27-path-a-20261016-r3/known_hosts"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_TEST_ALLOW_REBOOT=1

python3 boot-pool-patch/run-live-gate.py live-boot \
  --vm-container truenas-v27-path-a-20261016 --allow-dirty
```

The `live-boot` lane refuses to run without both guards and Path A, and fails
closed if any VM or container state is unknown or not `STOPPED`. If the root
is mounted, it creates a child dataset and marker before one non-blocking
reboot; it lists mounted filesystems under the user-data root and unmounts them
children first, without force, so the boot hook has to mount them again.
Afterward it verifies root/child mount ownership, marker SHA-256, pool health,
dataset query/create, and cleanup. If the root is safely confirmed unmounted,
it writes nothing before the first reboot; after the boot hook recovers the
mount, it creates the marker, unmounts the tree, and reboots again to verify
persistence. For the
broader dataset, zvol, VM, container, cache-vdev, and share API sequence, use
`live-storage` with the same connection values and mutation guard but omit
`TRUENAS_TEST_ALLOW_REBOOT`. The wrapper delegates to the installed
`run-gate` CLI, so its verdict and resource record use the registered gate
workflow.

## Use an external TrueNAS host

For a dedicated host that can be reset between runs, the wrapper can place its
bounded tester on an explicitly selected custom Docker bridge and connect to
the host by SSH. External mode is limited to `live-ui-auth`, `live-inspect`,
`live-storage`, `live-boot`, `live-v27-patch`, `live-v28-patch`,
`live-path-b-provision`, `live-path-b-partition`, `live-api-probe`,
`live-ui-prepare-ssh`, `live-ui`, and the fingerprint-pinned exploratory
`live-ui-observed-v1001` lane. The three `live-update-survival-*` lanes are
also restricted to the explicitly configured `TRUENAS_TEST_HOST`. The external
`live-boot` lane requires
`TRUENAS_TEST_ALLOW_REBOOT=1`, checks VM/container state before reboot, and
verifies the selected build and storage afterward. The wrapper does not restore
provider snapshots, so use it only for a disposable host whose reboot is
planned. ISO-backed `live-update`, vdev-topology, and workload-resume remain
managed-guest only. The tester remains capped at 0.5 CPU / 512 MiB in
`$CGROUP_PARENT_DEV_GATES`. This does not limit CPU or memory used by the
external TrueNAS host.

The wrapper normally refuses if `memory PSI some/full avg10` reaches its
admission threshold. An operator-authorized one-run `--allow-high-psi` switch
is available only with `--docker-network`; it skips those two PSI threshold
refusals. The wrapper still requires readable PSI, finite memory limits,
current memory below every hard `memory.max`, enough CPU quota, and retains
the tester's 0.5 CPU / 512 MiB limits. Do not use the switch for managed guest
runs.

The selected custom bridge must already exist and include the PWMCP service
under DNS alias `pwmcp` for UI lanes. Set `TRUENAS_TEST_HOST` to the target
host, and use a pinned SSH `known_hosts` entry for SSH lanes. For UI lanes, set
`TRUENAS_UI_URL` to an HTTPS origin and point `TRUENAS_UI_SECRET_FILE` at a
mode-0600 TOML file containing `[truenas_admin]`, `username`, and `password`.
The SSH key and known-hosts file must be inside this worktree and owned by the
same non-root UID. The UI secret must also be inside the worktree; it is owned
by the same UID as the SSH key when one is required. `live-ui-auth` is
read-only and needs no SSH key or host-key file. `live-ui-prepare-ssh` also
needs no private SSH key because it uses the UI; it is mutation-guarded and
must only run on the disposable target. Point
`TRUENAS_ROOT_SSH_PUBLIC_KEY_FILE` at one Ed25519 `.pub` file inside the
worktree. The lane adds that key to the built-in root account while preserving
other authorized keys, disables root password login and global SSH password
authentication, and verifies those settings after reopening the forms. It
starts SSH if stopped and enables automatic start only when the row's switch
is off. An already-on automatic-start setting is preserved. Pin the host key
and use the matching private key for later SSH lanes.

If the generated public-key file is missing, derive it from the protected
private key without copying the private key into the tester:

```bash
ssh-keygen -y -f golden-iso/vm/root_ed25519 > golden-iso/vm/root_ed25519.pub
chmod 0644 golden-iso/vm/root_ed25519.pub
```

Example for a disposable v27 Path A target:

```bash
cd scripts/truenas
: "${TRUENAS_TEST_HOST:?set the inspected disposable target address}"
export TRUENAS_TEST_PORT=22
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
: "${TRUENAS_TEST_KNOWN_HOSTS:?load the pinned known_hosts path}"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
: "${TRUENAS_UI_URL:?set the verified HTTPS origin}"
export TRUENAS_UI_SECRET_FILE="$PWD/boot-pool-patch/artifacts/nas-ui-secrets.toml"
export TRUENAS_ROOT_SSH_PUBLIC_KEY_FILE="$PWD/golden-iso/vm/root_ed25519.pub"
export PWMCP_WS_URL=ws://pwmcp:3000/
export PWMCP_PLAYWRIGHT_VERSION=1.64.0 # use `pwmcp contract`

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-inspect \
  --docker-network custom-test-bridge --allow-dirty
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-v27-patch \
  --docker-network custom-test-bridge --allow-dirty
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-ui \
  --docker-network custom-test-bridge --allow-dirty
```

To confirm UI credentials and browser navigation before preparing SSH, run
the read-only login gate with only the UI settings above:

```bash
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-ui-auth \
  --docker-network custom-test-bridge --allow-high-psi --allow-dirty
```

After confirming the UI login, prepare key-only root SSH through the UI. This
lane writes the public key to the built-in root account and changes SSH
service settings, so it requires the mutation guard:

```bash
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-ui-prepare-ssh \
  --docker-network custom-test-bridge --allow-high-psi --allow-dirty
```

Then capture and verify the expected host key before running an SSH lane. Do
not set `TRUENAS_TEST_KEY` or `TRUENAS_TEST_KNOWN_HOSTS` from a newly scanned
key until its fingerprint has been matched against a trusted provider or
console record.

Run `live-inspect` first and check the inventory before enabling mutations.
Snapshot the host through its provider before disk or pool changes, then
restore that snapshot after testing if a clean baseline is required. The
wrapper does not power off, reboot, reinstall, or restore an external host.

### Exploratory UI check on v1001

Use this lane only for the exact v1001 `27.0.0-RC.1` state captured in
[`observed-v1001-unrecorded-code-20261021.json`](../v27/observed-v1001-unrecorded-code-20261021.json).
The regular patch installer refused this host because its middleware source
does not match the current adapter. This lane verifies all 17 recorded source
hashes and the absent install marker before running UI checks. It tests the
host's current UI behavior; a pass does not certify the current adapter or
identify the provenance of the custom files. If `boot-pool/user-data` is
absent, setting `TRUENAS_TEST_ALLOW_PATH_A_ROOT_PROVISION=1` lets the gate
create only that empty root through ZFS with a 2 GiB quota, fixed mountpoint,
and `canmount=on`; it verifies the mount and empty tree before continuing.
The root itself is intentionally not created through the UI because this
middleware state rejects dataset CRUD for the managed root. The UI then creates
test descendants and zvols, submits a VM definition, and removes its test
objects. Take a provider snapshot first so the host can be reset if cleanup or
the host itself fails.

**Latest acceptance failed.** On 2026-10-21, the pinned source check and
guarded 2 GiB root setup passed, but middleware raised
`NameError: name 'mountpoint' is not defined` in
`pool_/dataset.py:117` on the first child dataset create. No child dataset or
later UI object was created. The pre-patch Netcup snapshot was restored and
v1001 is shut off. These commands document the guarded workflow; do not rerun
them as-is. Before another live attempt, first change `truenas_admin` from the
public default password to a random value and persist it in the gitignored
host-specific secret file. The recorded failure is in
[`live-ui-observed-v1001-child-create-nameerror-20261021.log`](../artifacts/live-ui-observed-v1001-child-create-nameerror-20261021.log).

From the `scripts/truenas` directory, configure the protected v1001 UI secret,
the prepared root key, and pinned SSH host key already stored under
`boot-pool-patch/artifacts/`:

```bash
cd scripts/truenas
: "${TRUENAS_TEST_HOST:?set the inspected disposable target address}"
export TRUENAS_TEST_PORT=22
export TRUENAS_TEST_KEY="$PWD/golden-iso/vm/root_ed25519"
: "${TRUENAS_TEST_KNOWN_HOSTS:?load the pinned known_hosts path}"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_TEST_ALLOW_UNRECORDED_PATCH=1
export TRUENAS_TEST_ALLOW_PATH_A_ROOT_PROVISION=1
export TRUENAS_OBSERVED_PATCH_MANIFEST="$PWD/boot-pool-patch/v27/observed-v1001-unrecorded-code-20261021.json"
: "${TRUENAS_UI_URL:?set the verified HTTPS origin}"
export TRUENAS_UI_SECRET_FILE="$PWD/boot-pool-patch/artifacts/v1001-ui-secrets.toml"
export PWMCP_WS_URL=ws://pwmcp:3000/
export PWMCP_PLAYWRIGHT_VERSION=1.64.0

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-ui-observed-v1001 \
  --docker-network custom-test-bridge --allow-high-psi --allow-dirty
```

On a later run with the verified root already present, omit the provisioning
guard and set `TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1`; the gate rechecks its
quota, mountpoint, canmount mode, mount source, and contents before proceeding.

The bridge must include PWMCP under the `pwmcp` DNS alias. `--allow-high-psi`
is an explicit one-run external-target override: it skips only PSI thresholds,
not tester memory, CPU, or cgroup placement checks. The lane does not apply or
repair middleware code. Re-run `live-inspect` after the UI lane and use the
provider's snapshot restore if the host is not clean or reachable.

The cache check creates a sparse zvol in the selected Path A or Path B pool,
adds it as an L2ARC device to a second temporary pool whose data vdev is a
file, verifies `zpool status`, and removes the cache vdev. This passed on v27
and v28 for both paths. It shows ZFS accepting a zvol from the selected pool
as a cache vdev for a distinct imported pool, but does not reproduce a separate
disk-backed data pool. To aim the cache device
at a different already-imported pool, set
`TRUENAS_TEST_CACHE_TARGET` to that pool name; use a disposable target because
the lane temporarily changes its cache-vdev configuration. No separate
disk-backed target has been tested yet, and the lane uses `zpool` directly
rather than TrueNAS's UI or middleware for this operation.

## Run PWMCP UI storage acceptance

`live-ui` normally starts from a fresh patched v27 COW guest. It inventories
guest storage, middleware container/VM records, and the patch marker before
making changes. Path A creates `boot-pool/user-data` through the Datasets page: choose
**Add Dataset**, name it `user-data`, open **Advanced Options**, and set
**Quota for this dataset and all children** to at least 1 GiB (the registered
run uses 2 GiB). The adjacent **Quota for this dataset** field submits
`refquota`, while the namespace guard requires finite `quota`. The browser
asserts the actual `pool.dataset.create` request contains that quota. The
adapter assigns `/mnt/boot-pool/user-data` and `canmount=on`; registered
inventory verifies these properties and the live mount source. On fresh r4,
the UI created a persistent child dataset, wrote 64 KiB, and observed increased
ZFS allocation. It created 128 MiB `vm-disk` and `cache-device` zvols with 16K
block size through the dataset UI. The cache zvol passed add/status/remove/
health/export checks as an L2ARC vdev on a temporary file-backed pool.

After the host restart, the registered PWMCP UI lane passed its resumed Path A
workflow. It selected `boot-pool` for a container and verified the direct ZFS
container dataset and image storage paths; it submitted the VM wizard with the
existing boot-pool zvol and checked the VM device record. It also repeated the
user-dataset write and zvol checks. The test removed its per-run container, VM,
and dataset tree. SMB/NFS pages opened, but the lane did not create shares or
test client access. The last successful registered read-only inventory at that
checkpoint confirmed the UI-created filesystem and both zvols, with the root
quota, mountpoint, `canmount=on`, and live mount source intact. Treat this as
historical evidence, not a current reachability check. The middleware Alpine image cache retains
`boot-pool/user-data/.truenas_containers/images/alpine:3.24:amd64:default:20261016_13:00@image`;
no probe snapshots remain. This proves survival across the host interruption, not controlled
reboot or update coverage. See the [passed UI run](../artifacts/live-ui-v27-path-a-r4-resume-existing-user-dataset-vm-nic-retry-20261018.log)
and [successful historical inventory](../artifacts/live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log).

A clean `27.0.0-RC.1` Path A guest passed the registered `live-ui` lane on
2026-10-19 after resuming its verified UI-created root and pinned image cache.
That run wrote to a UI-created child filesystem, created VM/cache zvols,
selected `boot-pool` for a container, and submitted a VM wizard using the
existing boot-pool zvol. It also found Session Timeout in the signed-in
account menu → Preferences, with the field showing 300 seconds. The gate
removed its per-run test tree; the follow-up registered inventory found no
probe datasets, containers, or VM records. See the
[UI run](../artifacts/live-ui-v27-path-a-clean-20261019.log) and
[post-run inventory](../artifacts/live-inspect-v27-path-a-clean-20261019-post-ui.log).
This proves the tested UI actions, not retention through a v27→v28 update.

On 2026-10-21, after a host restart, the preserved migration guest passed a
second registered read-only inventory and a registered `live-ui-workloads`
cycle. The container and blank VM each reached RUNNING and STOPPED through the
UI; the VM required the UI Power Off action after graceful Stop did not finish.
The filesystem and zvol probe hashes still matched the manifest. The serial
log showed an unexplained second kernel-start sequence during recovery. See
the [inventory](../artifacts/live-inspect-v27-golden-ui-migration-20261021-second-boot-pass.log),
[UI cycle](../artifacts/live-ui-workloads-v27-golden-ui-migration-20261021-second-boot-pass.log),
and [SSH startup failure before UI actions](../artifacts/live-ui-workloads-v27-golden-ui-migration-20261021-second-boot-ssh-failure.log).
The v27-to-v28 update has not yet run against this manifest.

For reboot and update acceptance, preserve the objects from `live-ui` by
setting `TRUENAS_TEST_PRESERVE_UI_WORKLOADS=1` and a new
`TRUENAS_UI_WORKLOAD_MANIFEST` path inside this worktree. The wrapper exports
the manifest from its read-only tester before cleanup. Then run
`live-ui-workloads` with the same manifest path. That lane submits Start and
Stop for the recorded container and VM through the TrueNAS UI, requires the
matching middleware requests and status transitions, and verifies the
filesystem and both zvol hashes. It can be run again after a controlled
reboot or update; the manifest identifies the original UI-created objects.

```bash
export TRUENAS_UI_WORKLOAD_MANIFEST="$PWD/boot-pool-patch/artifacts/path-a-ui-workloads.json"
export TRUENAS_TEST_PRESERVE_UI_WORKLOADS=1
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-ui \
  --vm-container truenas-v27-ui-a-fresh --allow-dirty
unset TRUENAS_TEST_PRESERVE_UI_WORKLOADS
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-ui-workloads \
  --vm-container truenas-v27-ui-a-fresh --allow-dirty
```

On the tested TrueNAS 27.0.0-RC.1 UI, open the signed-in account menu and
choose **Preferences**. The page shows **Session Timeout**; the registered UI
check saw 300 seconds. This is a per-user idle timeout and activity resets it.
The official Preferences guide documents a 300-second default and a maximum
of 2,147,482 seconds. The Advanced Settings guide also documents **System →
Advanced Settings → Access → Configure**, but the direct UI check confirmed
the Preferences route on this build. The topology gate signs in again if its
API work outlasts the UI session. See the official [Preferences guide](https://www.truenas.com/docs/scale/27/toptoolbar/settings/),
[Advanced Settings guide](https://www.truenas.com/docs/scale/27/systemsettings/advanced/advancedsettings/),
and the [clean Path A UI run](../artifacts/live-ui-v27-path-a-clean-20261019.log).

## Test auxiliary vdev roles

The registered `live-vdev-topology` lane has completed one v27 Path A
API/UI diagnostic on a fresh patched guest. It created seven 128 MiB zvols
through PWMCP, a 4 GiB sparse single-disk target pool, and a three-disk RAIDZ1
target with 1 GiB per disk. The UI displayed Cache, Log, and Special steps but
did not list any boot-pool zvol. API `pool.update` attempts to assign cache,
log, and special roles were refused for both target pools with
`[EINVAL] ... disks were not found in system` for the `/dev/zvol/...` paths,
before ZFS attachment. Direct checks showed both pools ONLINE with empty
auxiliary-role arrays; the lane exported the pools and removed its generated
UI dataset. This is a safe-refusal diagnostic, not auxiliary-vdev acceptance.
The test only inspected the pool-creation wizard; it did not submit UI role
assignments or test adding VDEVs to an existing pool. No write/fsync probe or
per-vdev `zpool iostat` check ran.

The RAIDZ1 special-vdev request used two zvols as a mirror for the logical
redundancy policy. Both zvols were stored on the same boot-pool disk, so this
does not give physical redundancy. The run used distinct devices, not one
device reused across roles, and it does not prove whether OpenZFS accepts such
reuse. It did not test each role separately, partitions, Path B
`boot-disk-data`, or a separate source pool on another disk. A source pool
must be imported before its zvol can be used by another target pool; a zvol
cannot back a vdev of the same pool that supplies it. The special allocation
class is critical pool storage and must be at least as redundant as the data
vdev; cache is optional read state, and SLOG serves synchronous-write
durability rather than general write caching. See the
[OpenZFS special-vdev guide](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Pool%20Structure/Special%20vdev.html),
[caching guide](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Pool%20Structure/Caching.html),
and [zvol guide](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Datasets/ZVOLs.html).

Sparse VM disks were enough to create the target pools and inspect middleware
refusal, UI visibility, pool health, and cleanup. They do not test accepted
role topology, performance, endurance, power-loss behavior, or physical
failure domains. The result and rationale are recorded in the
[topology run](../artifacts/live-vdev-topology-v27-path-a-r1-ui-role-steps-retry3-20261018.log),
[post-run inventory](../artifacts/live-inspect-v27-topology-a-r1-after-role-ui-cleanup-20261018.log),
and [design guide](DESIGN-GUIDE.md#auxiliary-vdevs-on-boot-disk-storage).

The old r2 boot experiment never passed the patch or inventory lanes. For a
future acceptance attempt, start from a fresh patched Path A guest with
the four sparse extra disks, pass registered `live-inspect`, and configure
`TRUENAS_TEST_PORT` and `TRUENAS_TEST_KNOWN_HOSTS` from that managed guest's
verified state before running the registered `live-vdev-topology` lane. Do not
use direct `zpool` commands to bypass middleware's device refusal.

After preparing that fresh guest and filling the required values from its
verified managed state, run the registered lane as follows. With the current
adapter this reproduces the safe middleware refusal described above; it is not
feature acceptance until the patched API and UI selectors accept the zvols.

```bash
cd scripts/truenas
source "$VENV/bin/activate"
: "${TRUENAS_VM_CONTAINER:?set to the fresh managed Path A guest}"
: "${TRUENAS_TEST_PORT:?set from that guest's verified config}"
: "${TRUENAS_TEST_KNOWN_HOSTS:?set from that guest's vm-state}"
export TRUENAS_TEST_PORT TRUENAS_TEST_KNOWN_HOSTS
export DOCKER_HOST=unix:///var/run/docker.sock
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_UI_SECRET_FILE="$PWD/golden-iso/artifacts/truenas-27-16GiB-golden.secrets.toml"
export PWMCP_WS_URL=ws://pwmcp:3000/
export PWMCP_PLAYWRIGHT_VERSION=1.64.0 # use the version returned by pwmcp contract
python3 boot-pool-patch/run-live-gate.py live-vdev-topology \
  --vm-container "$TRUENAS_VM_CONTAINER" --allow-dirty
```


The registered `live-api-probe` lane checks Path B actions on the partition-only
state left by `live-path-b-partition`: reserved-partition visibility, exact
refusal messages for unsupported topology and create options, create/adoption,
the destroy guard, replace/offline/detach/remove isolation, and adoption of a
hand-made pool. It checks `boot-pool` GUID, health, status, and the disk's
`lsblk`/`sgdisk` partition table around destructive-path probes. Results feed
[`ACTION-MATRIX`](../ACTION-MATRIX.md).

### Path B v27 pool-wizard safety

Run these registered lanes in order against one disposable v27 target. The
partition lane reserves only the free tail. `live-api-probe` creates and cleans
throwaway pools on that partition. The final `live-ui` run creates the pool in
the wizard and verifies the observed `pool.create` request uses exactly the
reserved partition. Keep the SSH/UI values from the external-host setup above;
the example below sets the selected disk facts and lane guards.

```bash
cd scripts/truenas
source "$VENV/bin/activate"
export DOCKER_HOST=unix:///var/run/docker.sock
: "${TRUENAS_TEST_HOST:?set to the disposable TrueNAS host}"
: "${TRUENAS_TEST_PORT:?set its SSH port}"
: "${TRUENAS_TEST_KEY:?set the pinned SSH key}"
: "${TRUENAS_TEST_KNOWN_HOSTS:?set the pinned known_hosts file}"
: "${TRUENAS_DOCKER_NETWORK:?set the existing custom bridge containing pwmcp}"
export TRUENAS_STORAGE_PROFILE=path-b
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_TEST_ALLOW_DISK_PROVISION=1
: "${TRUENAS_DISK_DEVICE:?set from read-only inventory to the boot-pool disk}"
: "${TRUENAS_BOOT_POOL_PARTITION:?set from read-only inventory to its boot-pool partition}"

nice -n 10 "$VENV/bin/python" boot-pool-patch/run-live-gate.py \
  live-path-b-partition --docker-network "$TRUENAS_DOCKER_NETWORK" \
  --allow-high-psi --allow-dirty
nice -n 10 "$VENV/bin/python" boot-pool-patch/run-live-gate.py \
  live-api-probe --docker-network "$TRUENAS_DOCKER_NETWORK" \
  --allow-high-psi --allow-dirty

export TRUENAS_PATH_B_SETUP=ui-wizard
nice -n 10 "$VENV/bin/python" boot-pool-patch/run-live-gate.py \
  live-ui --docker-network "$TRUENAS_DOCKER_NETWORK" \
  --allow-high-psi --allow-dirty
```

`live-api-probe` requires both `TRUENAS_DISK_DEVICE` and
`TRUENAS_BOOT_POOL_PARTITION` so it can compare the boot-pool disk before and
after each check. The wizard shim accepts one Stripe data vdev on the reserved
partition. It refuses encryption, non-default encryption options, dedup,
dedup-table quota changes, checksum overrides, duplicate-serial allowance,
SED mode, forced topology, and destructive export with option-specific
messages. See the [design rationale](DESIGN-GUIDE.md#path-b-pool-wizard-safety)
and current [action evidence](../ACTION-MATRIX.md).
The v27 create schema is closed: `name` and the validated single-Stripe
`topology` are the only fields the shim honors; every other installed field
must equal its recorded default, and unknown fields fail. See the
[recorded schema and defaults](DESIGN-GUIDE.md#v27-pool-create-schema).

#### Reclaim an exported Path B pool

The shim accepts pool names other than `boot-disk-data`; that value is the GPT
partition label, not a required pool name. Choose the exact pool name
explicitly. Before export, record that name, the sole leaf's PARTUUID, and the
numeric GUID from `zpool get`. Do not rely on a pool name alone after an
interrupted export.

For an imported pool, run these read-only commands on the TrueNAS host and
confirm each value before proceeding:

```bash
POOL=boot-disk-data  # Explicit operator choice; replace if this pool has another name.
mapfile -t POOL_LEAVES < <(zpool status -P -L "$POOL" \
  | awk '$1 ~ /^\/dev\// && $2 ~ /^(ONLINE|DEGRADED)$/ { print $1 }')
[[ "${#POOL_LEAVES[@]}" -eq 1 ]]
PARTUUID=$(lsblk -dnro PARTUUID "${POOL_LEAVES[0]}")
[[ "$PARTUUID" =~ ^[0-9a-fA-F-]{36}$ ]]
EXPECTED_POOL_GUID=$(zpool get -H -o value guid "$POOL")
[[ "$EXPECTED_POOL_GUID" =~ ^[0-9]+$ ]]
printf 'POOL=%s PARTUUID=%s EXPECTED_POOL_GUID=%s\n' \
  "$POOL" "$PARTUUID" "$EXPECTED_POOL_GUID"
```

Make the v27 source file
[`shared/reclaim_path_b.py`](../shared/reclaim_path_b.py) available on the
TrueNAS host using your approved file-transfer method. Run it as root with the
three values you recorded:

```bash
python3 /tmp/reclaim_path_b.py \
  --pool "$POOL" \
  --partuuid "$PARTUUID" \
  --expected-pool-guid "$EXPECTED_POOL_GUID"
```

The helper exports an imported middleware pool with `destroy=false`, then
rechecks the middleware row, pool GUID, sole ZFS leaf, PARTUUID, GPT marker,
boot-pool parent disk, mount state, and imported/exported pool state before it
runs `zpool labelclear -f` on `/dev/disk/by-partuuid/$PARTUUID`. It verifies
that the pool signature is gone and the same PARTUUID appears in
`disk.details`.

If the procedure stops after export, rerun the same command with the same
explicit `POOL`, `PARTUUID`, and `EXPECTED_POOL_GUID`. The helper recognizes
the exported pool by both PARTUUID and GUID. If the label was already cleared,
it verifies the partition is clear and offered by `disk.details`, then returns
`already_clear` without another write. If those values were not recorded, do
not guess; inspect the partition and the exported pool read-only, then supply
the matching identity explicitly. Any changed identity, mount, leaf, or row
stops the helper before label clearing.

The acceptance probe has its own cleanup checks for its throwaway pool. Those
checks are not evidence for this operator procedure; the helper's imported,
interrupted-export, and already-clear states have separate unit coverage. The
registered `live-ui` Path B `ui-wizard` lane also proves the wizard can use the
partition after it has been reclaimed. That lane uses the partition again;
repeat this procedure with the new pool GUID if it must remain available.

Path B has two explicit setup modes for the registered `live-ui` lane. Both
start from a fresh disposable managed guest with the v27 relaxation patch
installed. The mode is mandatory for Path B; the gate does not infer it from
whether a pool happens to exist.

- **`ui-wizard`** tests the full UI pool-creation route. First run the
  registered `live-path-b-partition` lane, which reserves only the free tail
  as `/dev/vda4`; then run `live-ui` with
  `TRUENAS_PATH_B_SETUP=ui-wizard`. Do not select `/dev/vda`, which contains
  the running boot pool. Do not run `live-path-b-provision` in this mode; it
  would bypass the pool-creation UI being tested.
- **`preprovisioned`** tests downstream dataset, zvol, container, and VM UI
  operations after the registered `live-path-b-provision` lane has created
  and registered `boot-disk-data`. This deliberately does not test UI pool
  creation. Its registered unit gate passed with 170 tests and 45 subtests;
  fresh live UI acceptance remains open. See the
  [unit-gate log](../artifacts/unit-boot-pool-patch-path-b-ui-preprovision-mode-20261021.log).

In the 2026-10-18 pre-shim diagnostic guest, the guarded partition lane passed
and reserved a 4 GiB `/dev/vda4`. The later registered PWMCP UI run opened the
Disks screen and the pool wizard. The Disks
screen showed only the whole `vda` boot-pool disk, with no partition creation
controls; middleware and browser `disk.query` omitted `vda4`. The wizard
reported zero raw capacity and no disks, then stopped before submitting
`pool.create`. A post-attempt `live-inspect` passed and confirmed no
`boot-disk-data` pool or probe objects. Evidence:
[UI run](../artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log)
and [post-attempt inventory](../artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).

That diagnostic's `ui-wizard` result was a safe refusal: that build's
middleware and browser `disk.query` omitted `/dev/vda4`, and the wizard had no
partition controls. The current v27 shim supplies the reserved partition to
the pool wizard; use the guarded sequence above to check it. The separate
`preprovisioned` route remains for downstream Path B UI checks. Its registered
unit gate passed on 2026-10-21; rerun the unit gate after changing the
implementation or its contract tests.

For a disposable managed v27 guest, the preprovisioned sequence is:

```bash
cd scripts/truenas
source "$VENV/bin/activate"
: "${TRUENAS_VM_CONTAINER:?set to a fresh managed Path B guest}"
export TRUENAS_STORAGE_PROFILE=path-b
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_TEST_PORT=2222
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/boot-pool-patch/artifacts/<guest-state>/known_hosts"

# Inspect first. Replace <guest-state> with this guest's recorded vm-state path.
python3 boot-pool-patch/run-live-gate.py live-inspect \
  --vm-container "$TRUENAS_VM_CONTAINER" --allow-dirty

# Set these from the inventory. These are the managed single-disk QEMU example.
export TRUENAS_TEST_ALLOW_DISK_PROVISION=1
export TRUENAS_DISK_DEVICE=/dev/vda
export TRUENAS_BOOT_POOL_PARTITION=/dev/vda3
export TRUENAS_MINIMUM_FREE_BYTES=1073741824
python3 boot-pool-patch/run-live-gate.py live-path-b-provision \
  --vm-container "$TRUENAS_VM_CONTAINER" --allow-dirty

export TRUENAS_PATH_B_SETUP=preprovisioned
export TRUENAS_UI_SECRET_FILE="$PWD/golden-iso/artifacts/truenas-27-16GiB-golden.secrets.toml"
export PWMCP_WS_URL=ws://pwmcp:3000/
export PWMCP_PLAYWRIGHT_VERSION=1.64.0 # use the version from `pwmcp contract`
python3 boot-pool-patch/run-live-gate.py live-ui \
  --vm-container "$TRUENAS_VM_CONTAINER" --allow-dirty
```

Replace `<guest-state>` with the `vm-state` directory recorded for that
managed guest. This sequence mutates the selected disk, so use only a fresh
disposable COW guest and never point these variables at a production host.
The registered UI gate then checks the exact partition-4 layout read-only, the
mounted `boot-disk-data` root, and its middleware container-pool choice before
opening the storage forms. It fails on any observed browser `pool.create`
request, regardless of the requested pool name, and checks again after the UI
workflow. UI pool creation remains outside this mode.

After the pool/root setup, the registered UI workflow creates a persistent user
dataset and verifies its ZFS mount source and allocation after a write; creates
VM and cache zvols through the dataset UI, writes to the VM zvol, and selects
that zvol in the VM wizard; and selects the target pool in the container UI,
then verifies the middleware-managed container dataset and allocated space
under `.truenas_containers`. It also checks that the internal container tree
stays hidden from ordinary dataset queries. The r4 Path A lane passed these
checks. Its cache zvol passed add, `zpool status`, removal, pool health, and
export checks on a temporary file-backed pool; this does not prove TrueNAS
UI/middleware cache-device configuration or real second-disk behavior. Repeat
the storage checks on a fresh Path B UI guest after the wizard can create the
pool. The UI lane does not create a share, verify NFS/SMB client access, or
boot a VM guest OS.

## Recover an interrupted live tester

If a host restart leaves the networked tester exited, resume the managed guest
with its saved `tcg_vm.py` state first. Then run the registered read-only
inventory with the explicit recovery flag. The wrapper removes only an exited
`tester-unified:local` container with its valid ownership label; it refuses a
running or unrecognized container and reports inspection failures instead of
treating them as absence.

```bash
cd scripts/truenas
export DOCKER_HOST=unix:///var/run/docker.sock
export TRUENAS_TEST_PORT=2223
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/boot-pool-patch/artifacts/v27-ui-path-a-fresh-20261017-r4/known_hosts"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-inspect \
  --vm-container truenas-v27-ui-a-fresh-r4-20261017 --allow-dirty \
  --recover-stale-tester
```

Use this option only for an interrupted runner. The wrapper verifies the
container state, image, and ownership label again before removing the exact
inspected container ID, then launches `live-inspect` through the registered
gate workflow. Resume the guest through its saved managed state before using
this command. The parent [TCG guest guide](../../docs/CONSUMERS.md#run-a-local-truenas-tcg-guest)
documents the OVMF source identity check after a host restart. The live remote
helper retries only classified SSH failures before command startup, at most
twice; it does not replay a middleware command that returned an error.

## Repair the legacy v27 root-create guard

Use this only on the earlier fresh Path A guest with the initial v27 install
record and no user-data root, Path B pool, containers, or VMs. Run the
registered read-only `live-inspect` lane first. The repair lane repeats those
checks, requires the exact prior bundle artifact and digest, backs up the
active `dataset.py` and install record under the persistent boot environment,
and rolls them back if source compilation or middleware validation fails.
Set the guest port and `known_hosts` for the selected managed runner before
using the commands below:

```bash
cd scripts/truenas
export DOCKER_HOST=unix:///var/run/docker.sock
export TRUENAS_TEST_PORT=2223
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/boot-pool-patch/artifacts/v27-ui-path-a-fresh-20261017-r4/known_hosts"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_V27_PATCH_ACTION=repair-root-create
export TRUENAS_TEST_ALLOW_PATCH_REPAIR=1
export TRUENAS_V27_PRIOR_PATCH_BUNDLE="$PWD/boot-pool-patch/artifacts/v27-fresh-patch-bundle-path-a-3cd9c02ac2.tar.gz"
export TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256=61331d7a4f2afd02fbe4e91e13436db70c2028639ba8d7b99a5659a2e9974b7e

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-inspect \
  --vm-container truenas-v27-ui-a-fresh-r4-20261017 --allow-dirty
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-v27-patch \
  --vm-container truenas-v27-ui-a-fresh-r4-20261017 --allow-dirty
```

The action values are `install` (the default for a fresh guest),
`repair-root-create` and `repair-root-parent-mountpoint` (one-time repairs for
the earlier empty-root state), and `repair-zvol-blocksize` (the r4 repair for
an already UI-created root). Every repair requires
`TRUENAS_TEST_ALLOW_PATCH_REPAIR=1`, the exact prior bundle artifact and
SHA-256, and its action-specific source digest. The first two repairs require
the root to be absent; `repair-zvol-blocksize` instead requires the exact
empty, mounted r4 root with a 2 GiB quota. Each repair backs up its middleware
source and install record under the persistent boot environment and attempts
to restore them if validation fails. These are not general update paths.
Leave dataset and pool creation to the registered UI gate.

### Repair the r4 parent mountpoint stat error

This exact recovery was bound to the r4 install record and source hashes from
the registered read-only inventory on 2026-10-18 before the UI created the
root. That no-root inventory is historical. A later successful inventory
found the UI-created root and zvols; run a new registered inventory before any
further work on a selected guest.
At that historical repair checkpoint, the patch unit lane passed with 121
tests and 45 subtests and the full unit lane passed with 319 tests and 45
subtests. The one-time live repair then passed; see
[`live-v27-patch-v27-path-a-r4-parent-mountpoint-repair-20261018.log`](../artifacts/live-v27-patch-v27-path-a-r4-parent-mountpoint-repair-20261018.log).
It refuses if the installed bundle or `dataset.py` digest changes, or if the
UI-created root or any workload appears. The patch source fix skips only the
`filesystem.stat()` call on the boot-pool parent's literal `mountpoint=none`;
the UI created the dataset afterward. Do not repeat this repair. Pass registered
`live-inspect` before any further action on a selected guest.

```bash
cd scripts/truenas
export DOCKER_HOST=unix:///var/run/docker.sock
export TRUENAS_TEST_PORT=2223
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/boot-pool-patch/artifacts/v27-ui-path-a-fresh-20261017-r4/known_hosts"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_TEST_ALLOW_PATCH_REPAIR=1
export TRUENAS_V27_PATCH_ACTION=repair-root-parent-mountpoint
export TRUENAS_V27_PRIOR_PATCH_BUNDLE="$PWD/boot-pool-patch/artifacts/v27-fresh-patch-bundle-path-a-8cd89d3add.tar.gz"
export TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256=4c72faac25ce748b197d118e0b036642518bf44d23d06e6f24ac738b3ed8bd05
export TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256=1cd5b385da5175980c96cdf1697888eb8cccd580bf97de935d7cfb889f021232

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-inspect \
  --vm-container truenas-v27-ui-a-fresh-r4-20261017 --allow-dirty
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-v27-patch \
  --vm-container truenas-v27-ui-a-fresh-r4-20261017 --allow-dirty
```

The first command had to pass immediately before that historical repair and
directly show that `boot-pool/user-data`, its mountpoint, `boot-disk-data`,
containers, and VMs were absent. That recovery has completed. Do not reuse its
commands against the current r4 guest; use the root-preserving repair below.

### Repair the r4 zvol form default

The zvol form originally failed because the stock v27 helper asked
`pool.query` for `boot-pool`, which that query excludes. The one-time
`repair-zvol-blocksize` action added the existing 16K recommendation only for
exact `boot-pool` requests; ordinary pool topology recommendations remain on
the stock code path. It was installed on r4 after the registered patch and
full unit gates and a digest-bound read-only inventory. The repair preserved
the UI-created root and stored source/marker backups under the persistent boot
environment. Its completion and installed markers are recorded in the
[handoff](../HANDOFF.md). The last successful inventory found the prior UI
dataset and two zvols, so the empty-root repair precondition no longer held at
that checkpoint; do not rerun it on this guest. Confirm current reachability
with a successful registered inventory before further work. This repair does
not create storage.

The selected TrueNAS guest must be running on a custom Docker bridge that also
contains the PWMCP service alias `pwmcp`. The wrapper checks this network
membership and places the bounded tester in `$CGROUP_PARENT_DEV_GATES`; it does
not start the TrueNAS VM. The browser connects to `ws://pwmcp:3000/`, and the
TrueNAS UI is addressed by the managed VM container name on its forwarded
HTTPS port. The UI credential file must be mode `0600`, inside this worktree,
and owned by the same non-root UID as the SSH key. The `playwright` dependency
is installed in the ephemeral tester from the pinned
[`requirements-live-ui.txt`](../acceptance/requirements-live-ui.txt); set its
version to the `playwright_version` reported by `pwmcp contract`.
The earlier 2026-10-18 Path B `ui-wizard` diagnostic stopped safely because
the unmodified middleware did not offer its reserved partition. On
2026-10-28, the v27 shim passed the stricter registered API and UI reruns: the
wizard evidence was a newly created middleware job, and its vdev named only
the reserved PARTUUID. The API probe also confirmed the exact option refusals,
destroy protection, and unchanged boot-pool and partition state. The
`preprovisioned` route covers downstream forms after registered pool setup
and remains a separate workflow.

Run Path A and Path B on separate fresh COW guests. Use the matching SSH port
and `known_hosts` from each runner manifest. The Path A commands below are an
earlier worked example; current r4 evidence is recorded above.

```bash
cd scripts/truenas
export TRUENAS_TEST_PORT=2222
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/boot-pool-patch/artifacts/v27-ui-path-a-fresh-20261017-r3/known_hosts"
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_UI_SECRET_FILE="$PWD/golden-iso/artifacts/truenas-27-16GiB-golden.secrets.toml"
export PWMCP_WS_URL=ws://pwmcp:3000/
export PWMCP_PLAYWRIGHT_VERSION=1.64.0 # use the version reported by `pwmcp contract`
export TRUENAS_STORAGE_PROFILE=path-a

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-v27-patch \
  --vm-container truenas-v27-ui-a-fresh-20261017 --allow-dirty
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-ui \
  --vm-container truenas-v27-ui-a-fresh-20261017 --allow-dirty
```

The current fresh Path B guest `truenas-v27-ui-b-fresh-r1-20261018` passed the
registered patch, post-patch read-only inventory, and guarded partition-only
lanes. This 2026-10-18 diagnostic predates the current shim. After the
partition lane, its Disks screen showed only the whole
boot-pool disk and no partition controls. Kernel `lsblk` sees the 4 GiB
`/dev/vda4`, but middleware and browser `disk.query` omit it. The pool wizard
reported no available disks and stopped before submitting `pool.create`. A
registered post-attempt inventory passed and confirmed there is no
`boot-disk-data` pool or probe data. See the [patch result](../artifacts/live-v27-patch-path-b-fresh-r1-20261018.log),
[partition result](../artifacts/live-path-b-partition-v27-path-b-fresh-r1-20261018.log),
[latest UI diagnostic](../artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log),
and [post-attempt inventory](../artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).
Preserve this historical diagnostic guest; do not replay the patch,
partition, or wizard lanes on it. The current v27 shim handles the partition
that the earlier build omitted. Never select the whole boot-pool disk. Use the
registered safety sequence above on the explicitly selected disposable target.

Keep each guest on `$CGROUP_PARENT_DEV_BACKGROUND` and the network tester on
`$CGROUP_PARENT_DEV_GATES` through the supported `tcg_vm.py` and
`run-live-gate.py` workflows. The guest cgroup uses the background tier; the
registered tester uses the gates tier.

## Resume a partial Path A UI run

Use this only after an earlier registered `live-ui` run created the root
through the TrueNAS UI and failed later. Run registered `live-inspect` first.
The resume check requires the finite-quota root, pinned mountpoint,
`canmount=on`, live mount source, and no container/VM records or probe
snapshots. It permits the exact pinned middleware image-cache datasets and
their `@image` snapshots only. If the root contains a prior test dataset, set
`TRUENAS_TEST_RESUME_EXISTING_UI_DATASET` to its exact path from the inventory.
The gate then allows only that generated UI filesystem and its two `vm-disk`
and `cache-device` zvols, checking their ZFS types, 128 MiB sizes, 16K block
sizes, and mounted filesystem source. Other user datasets, extra descendants,
and writable container datasets cause refusal. Leaving the dataset variable
unset continues to allow an otherwise clean root with only the verified
middleware image cache. These flags do not create or delete storage. The
dataset and zvols must already have been created through the TrueNAS UI. Path B
cannot use this resume path.

The last successful registered inventory for r4 showed the 2 GiB root and
prior UI dataset on `truenas-v27-ui-a-fresh-r4-20261017` (SSH 2223, HTTPS
8444). This inventory is historical. Pass registered `live-inspect` before
further action on any selected guest. The successful inventory is
[`live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log`](../artifacts/live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log).
The earlier verified path was
`boot-pool/user-data/ui-acceptance-202610180904493e3e9b`; use it only if it is
still present in the new successful inventory.

Run only after resuming r4 through its managed `tcg_vm.py` state, passing a
new registered `live-inspect`, confirming the guest is reachable, the patch and
full registered unit lanes pass, and current host PSI meets admission policy:

```bash
cd scripts/truenas
export DOCKER_HOST=unix:///var/run/docker.sock
export TRUENAS_TEST_PORT=2223
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/boot-pool-patch/artifacts/v27-ui-path-a-fresh-20261017-r4/known_hosts"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_UI_SECRET_FILE="$PWD/golden-iso/artifacts/truenas-27-16GiB-golden.secrets.toml"
export PWMCP_WS_URL=ws://pwmcp:3000/
export PWMCP_PLAYWRIGHT_VERSION=1.64.0
export TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1
export TRUENAS_TEST_RESUME_EXISTING_UI_DATASET=boot-pool/user-data/ui-acceptance-202610180904493e3e9b

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-ui \
  --vm-container truenas-v27-ui-a-fresh-r4-20261017 --allow-dirty
```

Keep both recovery settings unset for a fresh Path A run and every Path B run.
The initial fresh guest remains the basis for root creation, user-dataset,
zvol, and cache-vdev evidence; this command continues the remaining UI checks
on that same patched guest.

## Run live update acceptance

`live-update` changes the target boot environment and reboots it. Use only a
disposable Path A VM whose current version and attached media match the
selected artifact. The gate requires separate mutation, update, and reboot
guards. It also refuses to start if a VM or container is running inside the
TrueNAS guest. The ISO must already be attached read-only as CD-ROM media by
`tcg_vm.py`; use `--readonly-media` when starting or resuming the VM. The
runner uses QEMU's `-cdrom` option and keeps the existing system-disk boot
order. Live verification confirmed the installed v27 system disk boots first,
and the guest sees the ISO as read-only `/dev/sr0`.

The tested candidate is the v28 nightly recorded in
[`update-artifact.json`](../v28-nightly-20261014/update-artifact.json).
The 16 GiB Path A boot pool has only 1.32 GB more free than that update
manifest requires, so keep the update payload on the read-only ISO instead of
copying it into the boot pool. The gate checks the exact ISO SHA-256, media
read-only state and size, boot-pool free space, and update target version. It
creates a marker under `boot-pool/user-data`, starts `update.manual` with
cleanup disabled, reports middleware job state/progress, and requests reboot
only after the job succeeds. With a disposable v27 guest booted from its system
disk and the selected ISO attached as read-only `/dev/sr0`, run the registered
lane with:

```bash
cd scripts/truenas
export TRUENAS_TEST_PORT=2222
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/.vm/v27-path-a-update-cdrom-rerun1/known_hosts"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
export TRUENAS_UPDATE_EXPECTED_VERSION='28.0.0-MASTER+20261014-230204'
export TRUENAS_UPDATE_ISO="$PWD/boot-pool-patch/artifacts/TrueNAS-28.0.0-MASTER-20261014-230204.iso"
export TRUENAS_UPDATE_METADATA="$PWD/boot-pool-patch/v28-nightly-20261014/update-artifact.json"
export TRUENAS_UI_WORKLOAD_MANIFEST="$PWD/boot-pool-patch/artifacts/path-a-ui-workloads.json" # optional; emitted by preserve-enabled live-ui
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_TEST_ALLOW_UPDATE=1
export TRUENAS_TEST_ALLOW_REBOOT=1

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-update \
  --vm-container truenas-v27-path-a-20261016 --allow-dirty
```

After the expected version starts, it mounts the
saved user-data datasets if the old patch has not yet been reapplied and
verifies the marker SHA-256. The clean retry completed `update.manual` job 63 at `2026-10-17T01:12:30Z`,
rebooted from v27 RC1 into `28.0.0-MASTER+20261014-230204`, and passed the
boot-pool health and marker checks. The marker's bytes survived the upgrade.
The gate mounted the saved user-data datasets manually because the v28
boot-mount adapter was not installed yet; automatic remount through an update
is therefore still unverified. The earlier interrupted job 65 reached 49%
extraction and belongs only to its preserved old COW state; do not use it as a
resume command. The complete successful log is
[`live-update-v27-to-v28-20261017.log`](../artifacts/live-update-v27-to-v28-20261017.log).

When `TRUENAS_UI_WORKLOAD_MANIFEST` is set, the gate also checks the original
container/VM records and filesystem/zvol hashes before the update, after the
reboot before mount repair, and after any repair. It reports the natural
post-update mount state before changing it. A final PASS after manual repair
proves stored data remains readable after repair; it does not prove natural
automatic mounting or UI workload operation before repair. Continue with the
ordered checks in the
[successor prompt](../successor-handoff-v27-path-a-v28-migration-20261019.md).

If a transport failure happens after `update.manual` starts, do not invoke the
initial update again on that same COW state. Record the job ID, proof dataset,
and marker hash printed by the lane, then resume read-only polling with those
values:

```bash
export TRUENAS_UPDATE_JOB_ID='<job id printed by the first run>'
export TRUENAS_UPDATE_PROOF_DATASET='<proof dataset printed by the first run>'
export TRUENAS_UPDATE_PROOF_SHA256='<marker hash printed by the first run>'
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-update \
  --vm-container truenas-v27-path-a-20261016 --allow-dirty
```

The lane verifies the existing job and marker, then polls without calling
`update.manual` a second time. If the guest is stopped or rebooted before the
job finishes, use a separate COW clone for a new attempt; the old job's
in-memory state may not survive.

This update took about 17 minutes under TCG, then the v28 guest took about
five minutes to return middleware. Its 3.5 GiB container memory cap was
reached briefly during boot, with reclaim events but no OOM kill. Run update
acceptance against one target at a time and check host and background-slice
headroom before each start. The local runner can host up to three guests when
its per-start preflight admits them. The lane is budgeted for 90 minutes; its tester uses
0.5 CPU and 512 MiB in `$CGROUP_PARENT_DEV_GATES`, while QEMU remains separately
capped in `$CGROUP_PARENT_DEV_BACKGROUND`.

## v28 rolling-nightly acceptance

The only supported v28 build is the latest registered nightly,
`28.0.0-MASTER+20261029-175210`. Its adapter contains 19 exact source
fingerprints, four patch files, and a separate Path B partition shim. Its
current bundle digest is
`5366a8029f34162515e2b6c0935cf9b19abe3c41cc214886428af583d6db069b`. The
captured Oct 29 source passes all 19 hash checks and four zero-fuzz patch
checks. Previous Oct 14 and Oct 27 builds are retired; their logs remain
historical and do not certify this adapter. See the [adapter record](../v28-nightly-20261029/README.md)
and [acceptance results](../../docs/ACCEPTANCE-RESULTS.md).

The update harness reproduces the old Path B refusal condition precisely: the
v27 **fresh `install`** action refuses if `boot-disk-data` already exists,
because that action has no prior-patch-state contract. For a patched v27
source, the correct operation is digest-bound `reapply-after-update`; the
parametrized runner reads the v27 install record and chooses it automatically.
If the record is absent, the runner uses `install-after-update` only after
checking the target pools and the absence of conflicting patch state. For a v28
target, the separate v28 adapter uses its selected persistent Path A or Path B
root and checks the existing pool/profile before applying. It does not infer
compatibility from the source version or storage path.

`accept.py` restores a catalogued start snapshot when one is selected, verifies
the declared source version and Path A/Path B state, plants a bounded workload,
updates through the exact TrueNAS train, applies the appropriate adapter when
requested, and runs the selected checks. For example, this runs the complete
Path A RC.1-to-v28 scenario and records each result:

```bash
cd scripts/truenas
export DOCKER_HOST=unix:///var/run/docker.sock
: "${TRUENAS_TEST_DOCKER_NETWORK:?set the verified existing custom bridge with PWMCP}"
python3 boot-pool-patch/acceptance/accept.py \
  --from tn-v27rc1-path-a-patched-clean-20261030 \
  --start-state path-a \
  --to 28.0.0-MASTER+20261029-175210 \
  --patched-target reapply \
  --steps plant,update,verify,api,ui \
  --restore-snapshot tn-v27rc1-path-a-patched-clean-20261030
python3 boot-pool-patch/acceptance/accept.py matrix
```

Use `--from VERSION` or a versioned ISO path when the target is already in that
state; use a catalogued snapshot to restore a known fixture. `--steps` accepts
`plant,update,verify,ui,api`; `--patched-target` is `none` or `reapply`. A
matrix cell reports survival, patch, API, and UI separately so a survival PASS
does not imply that optional API/UI steps passed. The generated index also
selects the latest attempt, so a later failure cannot be hidden by an older
PASS.

The v27 Oct 29 adapter passed exact source verification and the latest Path A
update/reapply, API, and boot checks. Its full latest-build UI result remains
separate from the Oct 27 historical UI pass. The v28 rolling-base update,
patch, API, and UI outcomes and any incomplete runs are listed in the generated
results index. Path B and combined Path A+B update scenarios remain unverified
until their source snapshots and result cells say PASS. Snapshot names and
observed contents are listed with the results index. No screenshots are
committed.

## Reapply v27 after an update

An update creates and activates a new boot environment whose `/usr` starts
from the shipped update payload; the old BE keeps its patched `/usr` for
rollback. The per-BE `/root/v27-boot-pool-patch-install.json` may be copied
forward with its old version and digest, so it must not be trusted as proof
that the active BE has the patch. The recorded RC.1 adapter must not be
re-applied to a different nightly. After the selected system has booted the
exact `27.0.0-MASTER+20261029-173417` build, use the guarded action below. The
stale-record digest is a closed compatibility fact in
`acceptance/v27_patch_state.py`; it must also match the live marker. The runner
builds and fingerprints the nightly bundle from this checkout.

```bash
cd scripts/truenas
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION='27.0.0-MASTER+20261029-173417'
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_V27_PATCH_ACTION=reapply-after-update
export TRUENAS_TEST_ALLOW_PATCH_REAPPLY=1
export TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256=23e7667f4893f26f4861b17edc20e73f00ae2f8b797f9ff95cde446af1f25de1
: "${TRUENAS_DOCKER_NETWORK:?set the existing custom bridge containing pwmcp}"

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-v27-patch \
  --docker-network "$TRUENAS_DOCKER_NETWORK" --allow-high-psi --allow-dirty
unset TRUENAS_V27_PATCH_ACTION TRUENAS_TEST_ALLOW_PATCH_REAPPLY \
  TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256
```

The action validates the exact stale RC.1 install record and the complete
pristine nightly fingerprint set before changing `/usr`. It archives the old
record, applies the adapter transactionally, checks middleware and restores
`/usr` read-only. It mounts Path A root and eligible descendants. The observed
result was Path A root and eligible descendants mounted at boot.

## Run update-survival acceptance

The registered `live-update-survival-plant`, `live-update-survival-update`,
and `live-update-survival-verify` lanes plant bounded data/config records,
update to one exact newest build advertised by `update.available_versions`,
and compare the post-update middleware dump and data checksums. The updater
uses `update.run` with the exact train and version, then reboots. For patched
v27-to-v28 acceptance, run `live-v28-patch` after the update and before
`live-update-survival-verify`; for patched v27-to-newer-v27, run the guarded
`reapply-after-update` action at that same point.

For a v27-to-v28 data-survival check with `--patched-target none`, the verifier
expects the stock v28 BE to leave Path A/B auto-mount behavior unapplied. With
`--patched-target reapply`, the harness installs the registered v28 adapter
after the update and before verification, and records survival and patch
results separately. Run the v28 boot/import acceptance when that is in scope.

The update lane reads `update.profile_choices` and `update.config` before
selecting a train. It selects `DEVELOPER` for registered v27/v28 Nightlies or
`EARLY_ADOPTER` for registered RC trains, verifies the saved profile, and
prints the transition. Unknown trains and unavailable profiles fail before an
update starts; no profile string needs to be guessed or typed by the operator.

Keep manifests and reports in a private `.update-survival-*` directory inside
the worktree. The live-gate wrapper mounts only that directory writable; the
checkout remains read-only in the capped tester. Use the configured custom
bridge with egress to the disposable TrueNAS system. Hold the controller lock
only around each host-touching lane, and let it release between lane invocations:

```bash
cd scripts/truenas
source "$VENV/bin/activate"
export DOCKER_HOST=unix:///var/run/docker.sock
: "${TRUENAS_TEST_HOST:?set the inspected disposable target address}"
export TRUENAS_TEST_PORT=22
export TRUENAS_TEST_KEY="$PWD/golden-iso/vm/root_ed25519"
: "${TRUENAS_TEST_KNOWN_HOSTS:?load the pinned known_hosts path}"
export TRUENAS_TEST_DOCKER_NETWORK=custom-test-bridge
export TRUENAS_EXPECTED_VERSION='27.0.0-MASTER+20261029-173417'
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_STORAGE_PROFILE=both
export TRUENAS_SURVIVAL_MODE=patched
export TRUENAS_SURVIVAL_PROFILE=both
export TRUENAS_SURVIVAL_SSH_PUBLIC_KEY_FILE="$PWD/golden-iso/vm/root_ed25519.pub"
export TRUENAS_SURVIVAL_ARTIFACT_DIR="$PWD/.update-survival-patched"
mkdir -m 0700 "$TRUENAS_SURVIVAL_ARTIFACT_DIR"
export TRUENAS_UPDATE_EXPECTED_VERSION='28.0.0-MASTER+20261029-175210'
export TRUENAS_UPDATE_TRAIN='TrueNAS-28-Nightlies'
export TRUENAS_SURVIVAL_EXPECTED_AFTER="$TRUENAS_UPDATE_EXPECTED_VERSION"

flock -x -w 45 /home/vscode/vbpub-release-program-20261027/v1001.lock \
  python3 boot-pool-patch/run-live-gate.py live-update-survival-plant \
    --docker-network "$TRUENAS_TEST_DOCKER_NETWORK" --allow-dirty
export TRUENAS_TEST_ALLOW_UPDATE=1
export TRUENAS_TEST_ALLOW_REBOOT=1
flock -x -w 45 /home/vscode/vbpub-release-program-20261027/v1001.lock \
  python3 boot-pool-patch/run-live-gate.py live-update-survival-update \
    --docker-network "$TRUENAS_TEST_DOCKER_NETWORK" --allow-dirty

export TRUENAS_EXPECTED_VERSION="$TRUENAS_SURVIVAL_EXPECTED_AFTER"
export TRUENAS_STORAGE_PROFILE=path-a
flock -x -w 45 /home/vscode/vbpub-release-program-20261027/v1001.lock \
  python3 boot-pool-patch/run-live-gate.py live-v28-patch \
    --docker-network "$TRUENAS_TEST_DOCKER_NETWORK" --allow-dirty
export TRUENAS_STORAGE_PROFILE=both
unset TRUENAS_TEST_ALLOW_UPDATE TRUENAS_TEST_ALLOW_REBOOT
flock -x -w 45 /home/vscode/vbpub-release-program-20261027/v1001.lock \
  python3 boot-pool-patch/run-live-gate.py live-update-survival-verify \
    --docker-network "$TRUENAS_TEST_DOCKER_NETWORK" --allow-dirty
```

Set `TRUENAS_SURVIVAL_MODE=vanilla` and restore the stock v27 snapshot before
the vanilla case. Use `TRUENAS_UPDATE_TRAIN=TrueNAS-27-Nightlies` and the
expected v27 nightly version there. The update lane refuses an older build if
a newer build is advertised on that train. It does not install a patch. The
current v1001 inventory had no non-reserved user pool; the test reports that
coverage as unavailable rather than substituting boot-pool or Path B.
Encryption is skipped unless disposable key escrow is configured. Cloud-sync
records are captured but no remote task or credential is created.

For the supported-build registry, read-only drift commands, expected
differences, report fields, patch inventory, and measured coverage, see the
[update mechanics](../../docs/UPDATE-AND-DRIFT.md#update-mechanics-q1) and
[survival acceptance](../../docs/UPDATE-AND-DRIFT.md#update-survival-acceptance-q4)
record.

If the operator needs to remove the exact stale marker without installing the
adapter, use `uninstall-stale-record` and its separate guard. Do not set both
recovery guards:

```bash
export TRUENAS_V27_PATCH_ACTION=uninstall-stale-record
export TRUENAS_TEST_ALLOW_PATCH_UNINSTALL=1
export TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256=23e7667f4893f26f4861b17edc20e73f00ae2f8b797f9ff95cde446af1f25de1
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-v27-patch \
  --docker-network "$TRUENAS_DOCKER_NETWORK" --allow-high-psi --allow-dirty
unset TRUENAS_V27_PATCH_ACTION TRUENAS_TEST_ALLOW_PATCH_UNINSTALL \
  TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256
```

For a post-update reboot/import check, set the exact disk facts from a
read-only inventory. The external `live-boot` lane has its own explicit reboot
guard and compares boot-pool, Path A mounts, and any registered Path B pool:

```bash
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION='27.0.0-MASTER+20261029-173417'
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_TEST_ALLOW_REBOOT=1
export TRUENAS_DISK_DEVICE=/dev/vda
export TRUENAS_BOOT_POOL_PARTITION=/dev/vda3
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-boot \
  --docker-network "$TRUENAS_DOCKER_NETWORK" --allow-high-psi --allow-dirty
unset TRUENAS_TEST_ALLOW_REBOOT
```

The v27 external reboot lane was exercised on v1001 with Path B present. For
v28 `28.0.0-MASTER+20261029-175210`, the exact 19-target adapter was installed
after the v27-to-v28 update and the same reboot/import proof passed. The updater
now advertises an unregistered Oct 29 v28 nightly; v27-to-v28 user-data
survival against that stock BE passed, but patch installation against it is
refused until its exact targets are reviewed. See the [test rationale](DESIGN-GUIDE.md#v27-nightly-update-and-reapply)
and [current compatibility status](../README.md#supported-builds).

## Historical: apply the v28 2026-10-14 adapter

The registered `live-v28-patch` lane applies the release adapter to a
disposable v28 Path A or Path B guest. It checks the exact expected version and all 15
installed source fingerprints, verifies `/dev/kvm` is absent and each
configured guest is explicitly stopped, and refuses unknown runtime states or
unexpected helper files. For a VM with nullable query status it checks
`vm.status`; it does not assume that `null` means stopped. Before changing
`/usr`, it stores the original target files on the selected persistent storage
root: `boot-pool/user-data` for Path A or `boot-disk-data` for Path B. It stops `middlewared`, applies all three
patches with zero fuzz, installs the shared helpers, compiles Python modules,
restarts middleware, verifies the API, and restores `/usr` to read-only. A
durable hash manifest makes an identical retry idempotent; a failed
transaction attempts to restore and verify the original source files. This is
a disposable acceptance lane, not a production patch installer.

For the v28 Path B guest after the registered partition/pool provisioner has
passed, use its SSH port and strict host-key file:

```bash
cd scripts/truenas
export TRUENAS_TEST_PORT=2225
export TRUENAS_TEST_KEY="$PWD/golden-iso/vm/root_ed25519"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/boot-pool-patch/artifacts/v28-path-b-20261017/known_hosts"
export TRUENAS_STORAGE_PROFILE=path-b
export TRUENAS_EXPECTED_VERSION='28.0.0-MASTER+20261014-230204'
export TRUENAS_TEST_ALLOW_MUTATIONS=1

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-v28-patch \
  --vm-container truenas-v28-path-b-20261017 --allow-dirty
```

Path B preflight requires `boot-pool` ONLINE on `/dev/vda3` and
`boot-disk-data` ONLINE on `/dev/vda4`, plus an exact mounted dataset and
middleware query result. It stores the backup and patch manifest on
`boot-disk-data`.

For the local v28 update guest used in this worktree, run:

```bash
cd scripts/truenas
export TRUENAS_TEST_PORT=2222
export TRUENAS_TEST_KEY="$PWD/boot-pool-patch/artifacts/.live-test-root-key"
export TRUENAS_TEST_KNOWN_HOSTS="$PWD/.vm/v27-path-a-update-cdrom-rerun1/known_hosts"
export TRUENAS_STORAGE_PROFILE=path-a
export TRUENAS_EXPECTED_VERSION='28.0.0-MASTER+20261014-230204'
export TRUENAS_TEST_ALLOW_MUTATIONS=1

"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-v28-patch \
  --vm-container truenas-v27-path-a-20261016 --allow-dirty
```

On 2026-10-17 this lane passed on the updated guest running
`28.0.0-MASTER+20261014-230204`. It verified the installed target hashes,
middleware restart, TCG API result, unchanged hardware flags, `boot-pool` in
container choices, and `/usr` read-only state. The output is saved at
[`live-v28-patch-20261017.log`](../artifacts/live-v28-patch-20261017.log).
Two earlier attempts exposed and fixed an import cycle and typed `statfs`
results. The typed-result failure rolled back and verified the original source;
the interrupted import-cycle attempt needed a separate read-only recovery check.
After the dataset-query fix changed the bundle, the registered lane was rerun
in guarded replacement mode. It verified the old manifest, installed hashes,
and original-file archive, saved the prior patched state, restored stock
source, and applied the updated adapter. The passing run is
[`live-v28-patch-replacement-20261017.log`](../artifacts/live-v28-patch-replacement-20261017.log).

Use the two replacement guards only when the exact prior adapter is already
installed on this disposable guest:

```bash
export TRUENAS_V28_PATCH_ACTION=replace
export TRUENAS_TEST_ALLOW_PATCH_REPLACE=1
export TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256=2a4fa86979d7234c5aa16f1b03e0f93225c35ae10ddc3a114d1bb9e7ac680a06
"$VENV/bin/python" boot-pool-patch/run-live-gate.py live-v28-patch \
  --vm-container truenas-v27-path-a-20261016 --allow-dirty
unset TRUENAS_V28_PATCH_ACTION TRUENAS_TEST_ALLOW_PATCH_REPLACE \
  TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256
```

Replacement requires the exact `bundle_sha256` from that guest's installed
manifest and restores the previous patched state if new application fails.
The original stock files remain backed up on the selected Path A or Path B
storage root.

The first v28 storage run found that `pool.dataset.query` omitted the Path A
root. The next attempt reached `pool.dataset.create` but returned `ENOENT`
because the default query walker pruned recursion at the internal `boot-pool`
root before it could find the created dataset. The patch now makes the exact
pool root traversable while retaining the hidden protected children and
mutation guards. The live-storage preflight now requires the Path A managed
root and a new user-owned descendant to be queryable; Path B's ordinary pool
root remains required. Protected-path refusal is checked against direct ZFS
inventory as well as the middleware error. The post-reboot storage gate then
failed before mutations: the v28 boot-mount helper called
`zfs.resource.query_impl`, which is not a method on v28's `ZFSResourceService`.
The boot log confirmed the helper skipped mounting the root, and
`filesystem.statfs` resolved the user-data path to the parent `/mnt` filesystem.
The v28 helper and storage validator now call `list_impl`; artifact assertions
cover this API difference. Guarded replacement passed on 2026-10-17, restoring
stock files before applying the updated adapter and verifying the API/TCG
result. The first `live-boot` attempt stopped before reboot because the gate
used `zfs unmount -r`, an unsupported option on this system; its `finally`
cleanup removed the uniquely named probe dataset. The gate now enumerates
mounted filesystems and unmounts each child before its parent. Rerun
`live-boot` passed on 2026-10-17: after reboot, the root and marker child were
mounted from their expected datasets, the marker hash and boot-pool health
matched, `container.pool_choices` included `boot-pool`, and temporary datasets
were removed. The run log is
[`live-boot-v28-path-a-20261017.log`](../artifacts/live-boot-v28-path-a-20261017.log).
The registered storage gates now pass the adversarial protection sequence on
v27 and v28 Path A and Path B. Each creates an actual middleware-managed
container dataset, confirms it with direct ZFS inventory, verifies public
destroy refusal and dataset survival, verifies snapshot refusal and both
no-snapshot and dataset-survival state, then checks public clone refusal
separately. The four passing logs are:

- [v27 Path A](../artifacts/live-storage-v27-path-a-existing-container-protection-pass-20261017.log)
- [v27 Path B](../artifacts/live-storage-v27-path-b-existing-container-protection-pass-20261017.log)
- [v28 Path A](../artifacts/live-storage-v28-path-a-existing-container-protection-pass-20261017.log)
- [v28 Path B](../artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log)

The original v28 Path B failure remains preserved at
[`live-storage-v28-path-b-protection-refusal-failure-20261017.log`](../artifacts/live-storage-v28-path-b-protection-refusal-failure-20261017.log).
Its read-only post-run inventory found no test-specific dataset, but the
gate's `finally` cleanup also ran, so it did not establish whether destroy or
cleanup removed it. That historical inventory remains inconclusive by itself;
the later v28 Path B gate directly verified dataset survival after both
attempts and verified that no snapshot appeared:
[`live-storage-v28-path-b-existing-container-protection-pass-20261017.log`](../artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log).
The adapter fix adds `.truenas_containers` to the shared
internal-path policy used by v27 and v28. At the earlier checkpoint, the
registered patch unit lane passed with 106 tests and 45 subtests; its log is
[`unit-boot-pool-patch-v27-dns-retry-20261017.log`](../artifacts/unit-boot-pool-patch-v27-dns-retry-20261017.log).
The updated patch unit lane passes with 121 tests and 45 subtests in
[`unit-boot-pool-patch-root-parent-mountpoint-direct-engine-20261018.log`](../artifacts/unit-boot-pool-patch-root-parent-mountpoint-direct-engine-20261018.log),
and the full unit lane passes with 319 tests and 45 subtests in
[`unit-truenas-root-parent-mountpoint-20261018.log`](../artifacts/unit-truenas-root-parent-mountpoint-20261018.log).
The cache targets in all four earlier live gates were temporary file-backed
pools. Round-two v27 Path B reboot/import and UI pool creation passed. Actual
KVM selection, a real second-disk cache target, share-client access, guest OS
boot, and direct workload-content/record comparison through update remain
open.

## Experimental single-disk storage patch

The earlier v27 RC.1 and 2026-10-18 diagnostics below are historical. On
2026-10-30, the last registered v27 nightly passed the full Path A/B API and PWMCP UI
matrix on v1001. The Path B wizard created `boot-disk-data` on the reserved
partition after the guarded partition lane. Path A and B UI runs created a
dataset, container, and VM using an existing zvol; the API/storage probes also
covered zvol growth, share-record CRUD, and protected-path refusal. Path A and
Path B reboot/import proofs passed. The separate preprovisioned downstream UI
mode has only its registered unit-gate result with 170 tests and 45 subtests;
fresh live acceptance of that mode remains open. A fresh
Path A live-storage run also passed with
the disposable guest placed in a capped background Docker container and the
tester placed separately in the gates slice. It verified dataset/zvol
operations, cache-vdev add/remove, VM start with TCG confirmed in XML and the
QEMU process, container dataset persistence, NFS/SMB share-record CRUD, and
cleanup. The destroy/snapshot refusal probes in these earlier acceptances
targeted synthetic paths rather than an existing middleware-managed dataset,
so those results do not establish protection against direct mutation. The
historical run is logged at
[`live-storage-v27-path-a-20261016.log`](../artifacts/live-storage-v27-path-a-20261016.log);
the later existing-container adversarial gates are listed above. The guest shut
down through QMP/ACPI; its COW disk and EFI state remain under
`scripts/truenas/.vm/v27-path-a-20261016-r3`. Path A uses a finite-quota,
mounted `boot-pool/user-data` namespace on a full-disk layout. Path B creates
`boot-disk-data` on a reserved GPT tail after a 16 GiB image was restored to a
20 GiB disk; the pool auto-imported after reboot and retained a marker. The
Path B partition/pool setup in that historical acceptance was manual. The
registered provisioner later passed on v27 and v28. Its v27 provisioning and
Path B storage run reused the Path A COW guest after provisioning, rather than
a fresh isolated Path B guest; see the [v27 provision log](../artifacts/live-path-b-provision-v27-path-a-cow-20261017.log)
and [v27 Path B storage log](../artifacts/live-storage-v27-path-b-existing-container-protection-pass-20261017.log).
Neither API acceptance boots a guest OS or mounts NFS/SMB shares from a client.

## Registered Path B provisioner for disposable tests

The registered `live-path-b-provision` lane and the `provision_path_b.py`
controller create and register the pool outside the UI. Use the registered
lane for API/storage acceptance and the new `preprovisioned` UI mode. For the
separate `ui-wizard` diagnostic, use `live-path-b-partition` and then let the
TrueNAS wizard attempt pool creation; do not run the provisioner in that mode.
The direct SSH command below is a lower-level legacy example, not the preferred
registered workflow and not authorization to mutate a real host.

Path B uses partition 4 after the existing TrueNAS boot-pool partition. It
requires a single-disk GPT layout with partitions 1, 2, and 3 only, with
partition 3 the sole `boot-pool` vdev and a sufficiently large free tail. It
does not resize partition 3. The script defaults to a read-only plan; apply
requires `--apply` and an exact repeated `--confirm-disk` value.

The controller uses SSH as root and the existing strict `known_hosts` file:

```bash
: "${TRUENAS_TEST_HOST:?set the inspected disposable target address}"
export TRUENAS_TEST_PORT=22
export TRUENAS_TEST_USER=root
export TRUENAS_TEST_KEY=/secure/path/truenas-root
export TRUENAS_TEST_KNOWN_HOSTS=/secure/path/known_hosts

python3 boot-pool-patch/provision_path_b.py \
  --disk /dev/nvme0n1 \
  --boot-pool-partition /dev/nvme0n1p3 \
  --expected-version '27.0.0-RC.1'
```

Review the reported disk, boot-pool vdev, GPT extents, and planned tail. Apply
only after confirming the target disk is disposable or backed up:

```bash
python3 boot-pool-patch/provision_path_b.py \
  --disk /dev/nvme0n1 \
  --boot-pool-partition /dev/nvme0n1p3 \
  --expected-version '27.0.0-RC.1' \
  --apply --confirm-disk /dev/nvme0n1
```

The provisioner backs up the GPT under
`/root/truenas-path-b-backups/`, creates partition 4 without a force option,
checks that all original partition extents are unchanged, creates the empty
pool, then registers it through `pool.import_find` and `pool.import_pool`. A
repeat after success verifies the pool and returns without changing it. If a
partial or ambiguous layout is present, the tool stops and leaves the GPT
backup for inspection; it does not guess at cleanup or destroy a pool.

For a registered destructive test, use only a disposable VM with no running
VMs or containers inside it. In addition to the normal mutation and version
settings for `run-live-gate.py`, set these explicit target facts:

```bash
export TRUENAS_STORAGE_PROFILE=path-b
export TRUENAS_TEST_ALLOW_MUTATIONS=1
export TRUENAS_TEST_ALLOW_DISK_PROVISION=1
export TRUENAS_DISK_DEVICE=/dev/vda
export TRUENAS_BOOT_POOL_PARTITION=/dev/vda3
export TRUENAS_MINIMUM_FREE_BYTES=1073741824

python3 boot-pool-patch/run-live-gate.py live-path-b-provision \
  --vm-container truenas-path-b-disposable --allow-dirty
```

The lane provisions the selected disk and immediately repeats the operation to
verify idempotency. Registered live runs passed on v27 and v28 disposable
guests with reserved disk tails. The v27 provision and Path B storage runs used
the same COW guest after its Path A acceptance, not a fresh isolated Path B
guest. See the [v27 provision result](../artifacts/live-path-b-provision-v27-path-a-cow-20261017.log)
and [v28 provision result](../artifacts/live-path-b-provision-v28-path-b-20261017.log).
This remains a guarded test tool, not authorization to apply it to a real host.

The API acceptance covered dataset and zvol operations, VM disk selection and
a TCG VM process start, container lifecycle with a persistent dataset mount,
and NFS/SMB share-record CRUD. Earlier PWMCP checks covered dataset
selection/creation and protected system-dataset hiding, the container pool
selector, the VM wizard through its CPU/memory step, and SMB/NFS sections and
Add actions. Those checks did not submit the VM wizard or verify backing disk
use for containers, VMs, cache configuration, and persistent user datasets.
These results are proof of concept, not a supported
installation procedure: KVM selection and guest OS boot remain
unverified. The user-data marker bytes survived the v27-to-v28 update, but the
update gate mounted the data manually before checking them, so automatic
remount and patched v28 behavior remain under test. Do not apply the retained
25.10.4 patch to v27 or v28. Follow the
[investigation record](../README.md) for exact scope and open
checks. Local QEMU guests may run up to three at a time through the dedicated
runner in `$CGROUP_PARENT_DEV_BACKGROUND`, subject to its per-start memory PSI
preflight. Each container has a 200% CPU cap, 3.5 GiB container
memory/swap caps, and 3 GiB guest RAM. The Path A acceptance tester used
`dev-gates.slice`, with 25% CPU and 256 MiB memory/swap caps. Recheck the live
background slice before starting each guest; direct devcontainer launches
caused disruptive load.

The historical 12-target v27 patch set was re-applied to fresh local RC1
copy-on-write guests on 2026-10-16. Its fingerprints matched before patching;
`middlewared` was stopped during changes, edited modules compiled, the service
restarted, and `/usr` returned to read-only mode. The detached
`tester-unified` Path A and Path B acceptances passed dataset/zvol/cache
operations, VM start with TCG verified in both XML and process arguments,
container data persistence, NFS/SMB share-record CRUD, and cleanup. The Path A
guest shut down through QMP/ACPI with its COW state retained. These API runs do
not prove guest OS boot, client protocol access, or update survival. The
historical Path B partition and pool provisioning was manual. Later registered
provisioning and storage gates passed on v27; the v27 patch remains
experimental pending the open checks listed above.

The selected v28 ISO exposes its version and build metadata but no middleware
commit ID. The 15 current middleware targets used by the storage, TCG, and
boot-remount adapters have been extracted from that ISO and matched against
their recorded fingerprints; all three patch files apply and compile in a
temporary copy. The boot-remount adapter addresses the same source-level gap
found on v27: `pool.import_on_boot` imports configured data pools but not the
boot pool, then runs `pool.post_import`. The candidate explicitly mounts the
bounded user-data root and safe descendants before that hook. Recheck all
target files on any other v28 build; a version match alone is insufficient.
Its VM
page uses the virtualization capability result, while its create validation
checks KVM directly, so the v27 adapter cannot simply be reused unchanged. The
installed v27 RC's VM XML generator also hardcodes the KVM domain type; v27
needs a backend change that the inspected v28 package already contains. The
v28 candidate matches the selected nightly's patch targets, and the
registered `live-v28-patch` lane has applied it transactionally to the
running guest and verified the patched API. The v28 `live-storage` gate was
rerun after reboot but stopped at preflight because `container.pool_choices`
returned `{}`; it made no test mutations. The boot log identified the reason:
the boot-mount adapter used the absent v28 service method `query_impl`, leaving
the ZFS root unmounted. The local adapter and validator now use `list_impl`,
and guarded replacement passed. A first `live-boot` attempt created and then
cleaned its unique probe dataset, but stopped before reboot on the gate's
unsupported `zfs unmount -r` command. The gate now unmounts the filesystem list
child-first; its 2026-10-17 rerun passed root/child remount and persistence.
Registered v28 Path A and Path B `live-storage` runs now pass, including
existing-container destroy/snapshot refusal with direct dataset-survival
checks. The Path B result is
[`live-storage-v28-path-b-existing-container-protection-pass-20261017.log`](../artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log);
the Path A result is
[`live-storage-v28-path-a-existing-container-protection-pass-20261017.log`](../artifacts/live-storage-v28-path-a-existing-container-protection-pass-20261017.log).
The original Path B refusal failure and ambiguous post-run inspection remain
preserved in the v28 adapter record. PWMCP UI and KVM acceptance remain open. The
v27-to-v28 update did complete successfully, and the marker bytes survived,
as recorded above. The interrupted job 65 is historical and belongs to its
preserved COW state. See the [nightly adapter record](../v28-nightly-20261014/README.md)
for its exact source-validation steps and remaining checks.
