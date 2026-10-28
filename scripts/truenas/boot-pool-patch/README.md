# TrueNAS boot-pool storage patch

This experimental patch work explores exposing carefully bounded boot-disk
space for user data, VM disks, and container storage while keeping TrueNAS's
own boot datasets protected. Path A creates a user-owned namespace below
`boot-pool`; Path B uses a separate ZFS pool on a partition reserved before the
boot-pool partition filled the disk. A release-specific TCG adapter also lets
TrueNAS advertise and start VMs under QEMU software emulation when KVM is not
available.

## Current status

| Release | Evidence | Open acceptance |
|---|---|---|
| 25.10.4 / 25.10.7 | The retained 25.10.4 patch proposal dry-runs on 25.10.7. | Its reported UI flow failed; PWMCP reproduction remains pending. It does not apply to v27+. |
| 27.0.0-RC.1 | Registered gates pass existing-container destroy/snapshot refusal and survival on Path A and B. Fresh Path A UI acceptance created/wrote a filesystem, created zvols, selected `boot-pool` for a container, and submitted a VM wizard using an existing boot-pool zvol. A separate UI workload gate started/stopped the container and blank VM; after a controlled v27 reboot it verified the root remounted, hashes persisted, the VM autostarted, and UI Start/Stop worked again. After the 2026-10-21 host restart, the resumed guest passed a second read-only inventory and another UI lifecycle cycle with the same filesystem/zvol hashes; the blank VM required its Power Off control to stop. Session Timeout was visible in the signed-in account menu → Preferences (300 seconds). The 2026-10-18 Path B UI refusal predates the current shim. On 2026-10-28, the registered `live-api-probe` and Path B `live-ui` review reruns passed: unsupported create options and destructive export were explicitly refused, partition and boot-pool state remained unchanged, and the wizard's fresh create job used only the reserved partition. The unit lane passed with 230 tests and 45 subtests. The topology diagnostic showed Cache/Log/Special UI steps, omitted the boot-pool zvols, and safely refused their API paths before ZFS attachment. | Retention and natural post-update accessibility of these pre-existing Path A workloads through v27→v28 and v28 patch/reboot remain untested. The current container test does not prove a user filesystem mounted inside the container, and the VM has no guest OS. Downstream UI acceptance in the `preprovisioned` mode, accepted auxiliary-vdev roles through middleware/UI, existing-pool expansion, a disk-backed cache target, share-client access, VM guest boot, and actual live KVM selection remain open. |
| 28.0.0-MASTER+20261014-230204 | Exact target checks and guarded replacement passed. Path A and Path B provision/storage gates refuse destroy/snapshot against existing managed container data and verify survival. Path B provision passed on a reserved tail; Path A reboot/remount passed. A marker survived the v27-to-v28 update after manual mounting. A fresh post-restart Path B COW guest booted, but admission refused its registered inventory; it was then cleanly stopped with its overlay preserved. | Path B reboot/import, automatic remount and retention of pre-existing data/container/VM records through update and v28 patch reapplication, full UI, share-client access, guest OS boot, actual KVM selection, and a real second-disk cache/import-order check remain open. |

The prior v28 Path B `live-storage` attempt did not survive the host restart;
its October 17 PASS is historical evidence only. The fresh guest
`truenas-v28-path-b-clean-20261021` booted from a clean COW overlay over the
verified installed base, with no ISO attached. It reached the TrueNAS console;
SSH was inactive and was started temporarily on the disposable guest. The
registered `live-inspect` attempt was refused before a tester started because
host memory PSI exceeded admission limits. The guest was then gracefully
stopped through `tcg_vm.py`; its overlay and firmware state remain available
for a later managed resume. After the host reboot, four supported resume
attempts were also refused at `/dev.slice` memory PSI limits before creating a
container. The guest remains cleanly stopped; no inventory or storage test has
run on this fresh guest. See the current [handoff](HANDOFF.md) for the exact
state, refusal logs, resource checkpoint, and next safe steps.

The public v1001 target has a separate exploratory UI path. Its current
`27.0.0-RC.1` middleware source does not match the release adapter's expected
fingerprints (14 of 15 adapter targets differ), although two custom modules
match files in this worktree; the patch install record is absent. The guarded
[`live-ui-observed-v1001` lane](docs/CONSUMERS.md#exploratory-ui-check-on-v1001)
pins all 17 observed file hashes before exercising UI storage operations. When
the managed root is absent, an explicit guard provisions only the empty
`boot-pool/user-data` root with a 2 GiB quota and its required mountpoint by
direct ZFS command; the UI test then covers child storage and forms. This does
not test root creation through the UI, certify the current v27 adapter, or
establish how the custom files were installed. A Netcup rollback snapshot was
taken before these UI mutations; see the
[shared TrueNAS checkpoint](../TODO.md#2026-10-21-v1001-public-host-access-check).

The 2026-10-21 live attempt verified the observed fingerprint and bootstrapped
the empty root, then failed on its first child dataset create with
`NameError: name 'mountpoint' is not defined` in middleware. It did not reach
the remaining UI forms and does not certify the patch. The pre-patch provider
snapshot was restored, leaving v1001 shut off. Before another live run, rotate
the public default admin password to a random per-host value and keep it in a
protected, gitignored host-secret TOML. See the paused
[handoff](HANDOFF.md#paused-stop-point-2026-10-21).

The v27 migration VM resume was also refused after the host reboot: although
one initial PSI sample was briefly below limits, `tcg_vm.py` measured
`/dev.slice` memory PSI some avg10 at 26.29% and created no container. A later
sample remained above both admission limits. A second attempt rose from a low
sample to 18.3% some avg10 and was refused too. The guest is still stopped and
no update ran; see the
[saved admission record](artifacts/tcg-vm-start-admission-v27-golden-ui-migration-20261021-post-reboot.log).

A read-only inventory after the original v28 Path B failure is saved at
[`guest-inspection-v28-path-b-20261017.log`](artifacts/guest-inspection-v28-path-b-20261017.log).
It found no test-specific container dataset, middleware VM or container
records, or libvirt domains; the managed roots and Alpine image cache remained,
and both pools were ONLINE. Since the gate's `finally` cleanup also ran, that
inventory does not distinguish the destroy call from cleanup as the cause of
the missing test dataset. Later Path A and Path B protection gates passed with
direct existence checks after destroy and snapshot refusal.

The latest registered Path B UI diagnostic is recorded at
[`live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log`](artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log): kernel `lsblk` sees the 4 GiB `/dev/vda4`, while middleware and browser `disk.query` return only `/dev/vda`, assigned to `boot-pool`. The Disks screen has no partition creation controls. The wizard reports zero raw capacity and no disks; it stops before submitting `pool.create`. A registered post-attempt inventory confirms no `boot-disk-data` pool or test data was created:
[`live-inspect-v27-path-b-r1-after-disks-screen-20261018.log`](artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).
That 2026-10-18 diagnostic predates the v27 partition shim. The current shim
offers the eligible reserved partition to `pool.create` and adopts the new
pool. The 2026-10-28 review reruns passed: the API probe verified option
refusals and destroy protection, and the UI wizard created `boot-disk-data`
using only the reserved partition. The separate
`preprovisioned` route is for downstream UI checks: the registered
Path B provisioner creates and registers the pool first, then `live-ui` checks
user dataset, zvol, container, and VM operations through the UI. That route
passed its registered unit gate (170 tests and 45 subtests; see the
[unit log](artifacts/unit-boot-pool-patch-path-b-ui-preprovision-mode-20261021.log)).
That route does not prove UI pool creation.
See the [Path B pool-wizard safety design](docs/DESIGN-GUIDE.md#path-b-pool-wizard-safety)
for the rationale behind the reserved-partition limits and destroy guard.
Do not select the whole boot disk. Use only a disposable managed guest for
either route. See the [consumer workflow](docs/CONSUMERS.md#run-pwmcp-ui-storage-acceptance)
for the two explicit setup modes.

The required Path B baseline remains: run the guarded partition-only lane
first, then have the TrueNAS pool wizard create and register
`boot-disk-data`. The Disks screen does not create `/dev/vda4`; partition
reservation remains a separate guarded setup step.

Do not treat these adapters as ready for general installation. A successful
API operation does not prove that every UI path, reboot, update, share client,
or VM guest works. The v28 ISO does not identify its complete middleware Git
commit, so every adapter uses target fingerprints tied to the exact inspected
build. The 25.10.4 patch remains a historical proposal and is not compatible
with v27 or v28.

On the tested TrueNAS 27.0.0-RC.1 build, Session Timeout is visible under the
signed-in account menu → **Preferences**; the UI showed 300 seconds. The
setting is per-user and activity resets the idle timer. The official
Preferences guide gives a 300-second default and a 2,147,482-second maximum.
The Advanced Settings guide also documents **System → Advanced Settings →
Access → Configure**, but the tested UI exposed the preference directly. See
the official [Preferences guide](https://www.truenas.com/docs/scale/27/toptoolbar/settings/)
and [Advanced Settings guide](https://www.truenas.com/docs/scale/27/systemsettings/advanced/advancedsettings/).

### v27 Path A API and UI boundary

The current evidence does not show that users can do everything through the
UI. Registered API acceptance covers dataset create/query/update, zvol
create/grow, VM definition and zvol attachment/start under QEMU TCG, a
container with a persistent dataset mount and marker, NFS/SMB share-record
CRUD, and refusal of destructive operations against protected container data.
Cache-vdev add/status/remove was exercised with direct `zpool` commands on a
temporary file-backed target pool for both Path A and Path B. This shows a
zvol from either selected system-disk pool can serve as an L2ARC device for a
different imported pool in that setup. A separate disk-backed target and its
import ordering have not been tested. Middleware refused boot-pool zvol role
paths before attachment.

The registered UI lane created the Path A root and child filesystem, wrote
data to that filesystem, created VM/cache zvols, selected `boot-pool` for a
container and checked its ZFS backing, and submitted a VM using an existing
boot-pool zvol. The separate `live-ui-workloads` lane uses the UI to start and
stop preserved container and VM records and checks their backing data after a
reboot or update. It does not prove VM guest-OS boot. The UI lanes do not
attach a user dataset to a container, grow a zvol, create shares, or test
NFS/SMB clients. Auxiliary-vdev role selection is not accepted through the UI
or middleware. The UI wizard still cannot create the Path B partition or pool;
the new `preprovisioned` mode for testing downstream Path B UI operations has
passed its registered unit gate (170 tests and 45 subtests), but its fresh live
UI acceptance remains open. Do not report it as complete until the live gate
passes.
The latest clean run is
[`live-ui-v27-path-a-clean-20261019.log`](artifacts/live-ui-v27-path-a-clean-20261019.log);
see the [design guide](docs/DESIGN-GUIDE.md#pwmcp-storage-ui-acceptance) for
the acceptance boundary and [consumer guide](docs/CONSUMERS.md#run-pwmcp-ui-storage-acceptance)
for the `live-ui` and `live-ui-workloads` workflow.

## Project files

- `v27/` and `v28-nightly-20261014/` contain release adapters and fingerprints.
- `25.10.4/` preserves the earlier proposal and its source documents.
- `shared/` contains policy helpers installed by release adapters.
- [`provision_path_b.py`](provision_path_b.py) plans or creates a Path B
  partition and pool after strict layout checks; its registered destructive
  live lane passed on v27 and v28 disposable guests with reserved disk tails.
- `acceptance/` and `tests/` contain scripted gates and their artifact checks.
- The registered `live-ui` lane uses PWMCP, reads the HTTPS forwarding port
  from the managed guest, and verifies the selected pool's actual ZFS mount
  and allocation after UI actions. Its fresh v27 Path A setup creates
  `boot-pool/user-data` through the UI. Path B requires an explicit
  `TRUENAS_PATH_B_SETUP`: `ui-wizard` tests partition-only setup followed by
  UI pool creation (currently a safe refusal because `disk.query` omits
  `/dev/vda4`), while `preprovisioned` requires the registered provisioner to
  create and register `boot-disk-data` before UI dataset/zvol/container/VM
  checks. The latter passed the registered unit gate (170 tests and 45
  subtests; see the [unit log](artifacts/unit-boot-pool-patch-path-b-ui-preprovision-mode-20261021.log));
  fresh live acceptance remains open. The browser gate rejects any
  `pool.create` request in this mode; it does not establish UI pool creation.
  Path A requires the UI's **Quota for this dataset and all children** field;
  the neighboring dataset-only quota sets `refquota`, not the guarded `quota`
  property. The adapter pins the managed root's exact mountpoint. Path B's
  guarded partition-only lane reserves just the free-tail partition before the
  UI pool wizard uses it.
  The container form selects its pool in the UI, and the VM wizard selects an
  existing zvol created in the UI. Both resulting device/dataset paths are
  checked against direct ZFS inventory. See the
  [design guide](docs/DESIGN-GUIDE.md#pwmcp-storage-ui-acceptance) for scope and
  the [consumer guide](docs/CONSUMERS.md#run-pwmcp-ui-storage-acceptance) for
  the fresh guest sequence.
- `TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1` is an optional Path A `live-ui`
  recovery setting for a root created by an interrupted UI run. If that root
  contains a prior UI test filesystem, `TRUENAS_TEST_RESUME_EXISTING_UI_DATASET`
  must name its exact generated path; the gate then permits only that
  filesystem and its two known zvols after checking their direct ZFS
  properties. The middleware image cache is allowed only for its pinned image
  dataset and exact `@image` snapshot; other user descendants and snapshots
  still refuse. See the [design guide](docs/DESIGN-GUIDE.md#pwmcp-storage-ui-acceptance)
  for the narrow allowlist and the [consumer guide](docs/CONSUMERS.md#resume-a-partial-path-a-ui-run)
  for the r4 resume command.
- The registered `live-v27-patch` lane has guarded `repair-root-create`,
  `repair-root-parent-mountpoint`, and `repair-zvol-blocksize` actions. The
  one-time r4 zvol repair was bound to the old installed bundle and original
  `dataset_info.py` fingerprint; it preserved the root and placed source/marker
  backups under the persistent boot environment. It is installed on r4 and is
  not a general update path. The last successful r4 inventory found the
  UI-created user data and zvols, so the empty-root repair guard should refuse
  another application. Treat that inventory as historical; run the registered
  read-only inventory before any new mutation on a selected guest.
  See the [design guide](docs/DESIGN-GUIDE.md#pwmcp-storage-ui-acceptance)
  and [consumer status](docs/CONSUMERS.md#repair-the-r4-zvol-form-default).
- The r4 PWMCP UI lane passed after resuming the guest. It
  verified UI-created user data and zvols, container storage selection and
  direct ZFS backing, VM wizard submission with a boot-pool zvol, and
  cache-zvol use on a temporary file-backed pool. It cleaned its per-run test
  tree. A successful registered inventory at a later checkpoint found the
  prior filesystem and zvols; treat it as historical, not a current state
  check. SMB/NFS pages were opened, but no share was created and no client was
  tested. See the
  [UI run](artifacts/live-ui-v27-path-a-r4-resume-existing-user-dataset-vm-nic-retry-20261018.log),
  [successful historical inventory](artifacts/live-inspect-v27-path-a-r4-after-current-host-reboot-20261018.log),
  and [handoff](HANDOFF.md).
- The registered `live-inspect` lane records guest datasets, snapshots,
  container and VM records, and pool health without mutation.
- The registered `live-vdev-topology` lane is an exploratory API/UI diagnostic.
  It created the bounded Path A root and 128 MiB zvols through PWMCP, then
  created sparse single-disk and RAIDZ1 target pools. The UI showed Cache,
  Log, and Special steps but omitted boot-pool zvols. Middleware refused
  `pool.update` for those `/dev/zvol/...` paths with “disks were not found in
  system,” before ZFS attachment. Direct checks found both target pools
  ONLINE with empty auxiliary-role arrays; cleanup passed. This is safe-refusal
  evidence, not feature acceptance. UI role submission, accepted middleware
  paths, same-device reuse, partitions, Path B, and a separate source pool
  remain open. A mirrored special vdev backed by two zvols on one boot disk
  would still share one physical failure domain. See the
  [design rationale](docs/DESIGN-GUIDE.md#auxiliary-vdevs-on-boot-disk-storage),
  [consumer command](docs/CONSUMERS.md#test-auxiliary-vdev-roles), and current
  [handoff](HANDOFF.md).
- `run-live-gate.py --recover-stale-tester` recovers an interrupted gate only
  when the existing tester is confirmed exited, uses `tester-unified:local`,
  and carries a valid live-runner ownership label. It pins cleanup to the
  inspected container ID and refuses running, foreign, or indeterminate
  containers. See the [design guide](docs/DESIGN-GUIDE.md#recover-an-interrupted-live-tester)
  and [consumer steps](docs/CONSUMERS.md#recover-an-interrupted-live-tester).
- The live remote helper retries at most twice for known SSH failures before
  command startup, including a key-exchange reset; middleware or shell command
  failures are returned without retrying.
- Managed guests resume from their saved `tcg_vm.py` state after a host
  restart. An OVMF source device-number change is accepted only when its path,
  inode, size, modification time, and SHA-256 still match; see the parent
  [runner design](../docs/DESIGN-GUIDE.md#isolated-local-qemu-guests).
- Run the registered `unit-boot-pool-patch` lane for patch policy, adapter,
  and live-gate contract tests before live acceptance.
- `artifacts/` is the ignored home for live logs and test credentials.
- [`run-live-gate.py`](run-live-gate.py) places a bounded acceptance tester on
  the selected disposable guest's Docker network and delegates its verdict to
  the installed Run-Gate CLI. With `--docker-network`, it can also target an
  explicitly named external TrueNAS host on a verified custom Docker bridge
  for the restricted live lanes; see the
  [consumer guide](docs/CONSUMERS.md#use-an-external-truenas-host). Its
  guarded `live-ui-prepare-ssh` lane adds the configured public key to TrueNAS's
  built-in root user, verifies root and global SSH password login are disabled,
  then starts SSH through the UI. Its
  `--allow-high-psi` switch is a one-run external-host override for PSI
  thresholds only; CPU, hard-memory, and tester caps remain enforced.
  Run `unit-boot-pool-patch` with `run-gate` from `scripts/truenas`.
- [`ACTION-MATRIX.md`](ACTION-MATRIX.md) is the canonical Path A/B web-UI action
  matrix (works / patch hunk / lane per release); a unit test keeps it in sync
  with the patch files and live lanes.
- [`HANDOFF.md`](HANDOFF.md) is the current release/path test matrix and
  resume checklist for ongoing work.

Read the [design guide](docs/DESIGN-GUIDE.md) for source changes, Path A/B
tradeoffs, release differences, and evidence details. Follow the
[consumer guide](docs/CONSUMERS.md) for registered local live gates and their
guards. Shared TCG guest setup lives in
[`../docs/CONSUMERS.md`](../docs/CONSUMERS.md#run-a-local-truenas-tcg-guest).
