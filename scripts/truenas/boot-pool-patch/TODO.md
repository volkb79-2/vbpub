# Boot-pool patch follow-up work

For the current release/path test matrix, active gate checkpoint, and exact
resume instructions, see [`HANDOFF.md`](HANDOFF.md).

## Current checkpoint: Path B UI cannot see the reserved partition; auxiliary zvols are refused

## Live validation status (2026-10-19)

The earlier Path A r4, Path B r1, and topology guests were retired through
`tcg_vm.py`; their saved state directories remain as artifacts. A clean Path A
guest from the golden image passed registered patch, inventory, API storage,
and UI acceptance. The UI created/wrote a filesystem, created zvols, selected
`boot-pool` for a container, submitted a VM wizard with an existing boot-pool
zvol, and exposed Session Timeout under the signed-in account menu →
Preferences (300 seconds). The final registered inventory found no test
datasets, containers, or VM records. See
[`live-ui-v27-path-a-clean-20261019.log`](artifacts/live-ui-v27-path-a-clean-20261019.log)
and
[`live-inspect-v27-path-a-clean-20261019-post-ui.log`](artifacts/live-inspect-v27-path-a-clean-20261019-post-ui.log).
Run read-only inventory before any future mutation on a selected guest.
Existing v27→v28 marker evidence does not establish retention of pre-existing
container/VM records or automatic data access after reapplying the v28 patch.

The fresh patched Path B guest had already passed `live-v27-patch`,
post-patch inventory, and guarded `live-path-b-partition`, which reserved
`/dev/vda4` without creating a pool. After host recovery, registered
`live-inspect` passed before the UI run. Registered PWMCP `live-ui` then
confirmed kernel `lsblk` sees the 4 GiB `/dev/vda4`, while middleware and
browser `disk.query` return only `/dev/vda`. The Disks screen showed only the
20 GiB `vda` boot-pool row; it had no partition mentions or partition-creation
controls. The pool wizard showed `Total Raw Capacity: 0 B` and `No disks
available`, then stopped before submitting `pool.create`. The run did not
select `/dev/vda` and did not use the CLI provisioner. The expected UI refusal
is saved in
[`live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log`](artifacts/live-ui-v27-path-b-r1-after-cleanup-with-disks-screen-20261018.log).
A post-attempt registered read-only inventory passed at
[`live-inspect-v27-path-b-r1-after-disks-screen-20261018.log`](artifacts/live-inspect-v27-path-b-r1-after-disks-screen-20261018.log).
This is not Path B UI acceptance: creation of `/dev/vda4`, creation and
registration of `boot-disk-data`, and downstream storage checks remain open.

The latest clean Path A PWMCP run passed the UI workflows above; root creation
and its 2 GiB quota, exact mountpoint, `canmount=on`, and live mount had passed
on this guest in the preceding registered UI setup. Cache-zvol add/status/
remove was exercised on a temporary file-backed pool. The UI gate did not
create shares, test NFS/SMB clients, start a VM through the UI, boot a guest
OS, or use a real second-disk cache target. These v27 results do not establish
retention of pre-existing workloads through v27→v28.

The registered v27 Path A `live-vdev-topology` diagnostic created seven UI
zvols of 128 MiB, a 4 GiB sparse single-disk target, and a three-disk 1 GiB
RAIDZ1 target. Its UI showed Cache, Log, and Special steps but none of the
boot-pool zvols. Middleware rejected all tested zvol paths for cache, log, and
special roles with `[EINVAL] ... disks were not found in system`, before ZFS
attachment. Direct `pool.query` and `zpool status -P` showed both targets
ONLINE with empty role arrays and no errors. The gate exported both target
pools and deleted only its generated UI tree. It passed as a safe-refusal
diagnostic, not as feature acceptance. No SLOG-write or metadata-allocation
probe ran. See
[`live-vdev-topology-v27-path-a-r1-ui-role-steps-retry3-20261018.log`](artifacts/live-vdev-topology-v27-path-a-r1-ui-role-steps-retry3-20261018.log)
and the post-run inventory
[`live-inspect-v27-topology-a-r1-after-role-ui-cleanup-20261018.log`](artifacts/live-inspect-v27-topology-a-r1-after-role-ui-cleanup-20261018.log).

Source review of the v27 middleware commit shows the boundary to change:
`PoolService._process_topology()` sends every vdev path to
`disk.check_disks_availability()`, and pool create/update pass the resulting
device map to `pool.format_disks()` before ZFS creates or extends a pool. A
supported patch needs a type-aware, allowlisted path for existing zvols in the
managed user namespace, a guard against using a source pool as its own target,
and a separate physical-disk list so zvols never reach disk formatting. The
UI also needs an explicit auxiliary-device selector; do not fabricate zvols as
ordinary `disk.query` rows or bypass middleware with raw `zpool` commands.
See the release-pinned [pool service source](https://github.com/truenas/middleware/blob/d83c3bf89aa4226c8fd1ef25dd273bdf447eb777/src/middlewared/middlewared/plugins/pool_/pool.py#L280-L335)
and its [create](https://github.com/truenas/middleware/blob/d83c3bf89aa4226c8fd1ef25dd273bdf447eb777/src/middlewared/middlewared/plugins/pool_/pool.py#L526-L552)
and [existing-pool update path](https://github.com/truenas/middleware/blob/d83c3bf89aa4226c8fd1ef25dd273bdf447eb777/src/middlewared/middlewared/plugins/pool_/pool.py#L711-L719).

The latest registered patch unit lane passed 149 tests + 45 subtests:
[`unit-boot-pool-patch-v27-ui-session-timeout-20261019.log`](artifacts/unit-boot-pool-patch-v27-ui-session-timeout-20261019.log).

TrueNAS 27 sessions expire after the configured per-user idle duration, and
activity resets the timer. The live 27.0.0-RC.1 UI showed **Session Timeout**
under the signed-in account menu → **Preferences**, with a value of 300
seconds. The official Preferences guide documents a 300-second default and a
2,147,482-second maximum; the Advanced Settings guide also describes
**System → Advanced Settings → Access → Configure**. The observed Preferences
route is recorded in the clean [UI run](artifacts/live-ui-v27-path-a-clean-20261019.log).
See the official [Preferences guide](https://www.truenas.com/docs/scale/27/toptoolbar/settings/)
and [Advanced Settings guide](https://www.truenas.com/docs/scale/27/systemsettings/advanced/advancedsettings/).

Open checks:

- [ ] Verify retention and continued use of a pre-existing Path A filesystem,
  persistent container dataset/record, and VM record/zvol across a v27→v28
  update and reapplying the exact v28 patch. The current update test manually
  mounted a marker before hashing and had no pre-existing workload records.
- [ ] Find a supported UI/API inventory path for Path B that lets the UI create
  `/dev/vda4` and then create/register `boot-disk-data`. Keep the guarded
  partition-only lane first in the baseline sequence; do not select `/dev/vda`
  or run `live-path-b-provision`.
- [ ] Run Path B downstream dataset, container, VM-zvol, cache configuration,
  reboot/import, and update checks only after the UI pool wizard creates the
  pool on a fresh patched guest.
- [ ] Implement a type-aware, allowlisted middleware and UI path for eligible
  boot-pool/Path B zvols or dedicated partitions as cache, log, and special
  vdevs. Preserve physical-disk availability checks, prevent formatting zvols,
  reject a target pool backed by its own source zvols, and do not bypass disk
  inventory or the middleware refusal guard.
- [ ] Once devices are accepted, test each role on its own and in combinations
  through pool creation and existing-pool VDEV expansion in the UI and API; the
  current UI probe inspected only the creation wizard. Test distinct devices
  and one identical device reused across roles. Measure actual SLOG writes and
  special metadata allocation.
- [ ] Test devices backed by a separate source pool on another disk, Path B
  `boot-disk-data`, and partitions carved from disks used by a target pool.
  Verify pool import ordering and behavior if a source pool is unavailable.
- [ ] Keep a real second-disk cache target, controlled reboot/update and
  automatic remount, share-client access, VM guest boot, actual KVM selection,
  physical fault tolerance, endurance, latency, and performance open until
  direct evidence closes each item.
- [ ] Build a retention gate with UI-created Path A data, a persistent container,
  and a VM record backed by a zvol; update v27 to v28, reapply the v28 patch,
  then verify the same records, IDs, disks, mounts, and data through the API and
  UI, and exercise container/VM restartability. Existing update evidence used
  a standalone marker and manual mount, while post-update storage tests created
  fresh objects.

## Earlier checkpoint: all four existing-container protection gates passed

The v27 Path A/B and v28 Path A/B registered `live-storage` gates now prove
that a real middleware-managed container dataset survives refused public
destroy and snapshot attempts. Each also verifies that snapshot refusal leaves
no snapshot behind. The v27 Path B gate completed its remaining VM, container,
share-record, and cleanup checks. Its log is
[`live-storage-v27-path-b-existing-container-protection-pass-20261017.log`](artifacts/live-storage-v27-path-b-existing-container-protection-pass-20261017.log).
Path B provision ran on the same COW guest after Path A; see
[`live-path-b-provision-v27-path-a-cow-20261017.log`](artifacts/live-path-b-provision-v27-path-a-cow-20261017.log).

At that earlier checkpoint, the registered unit gate passed after adding a bounded SSH retry for the exact
temporary DNS resolution failure that occurred before the remote command could
start. Permanent hostname errors still fail immediately. The result is 106
tests and 45 subtests in
[`unit-boot-pool-patch-v27-dns-retry-20261017.log`](artifacts/unit-boot-pool-patch-v27-dns-retry-20261017.log).
Two DNS-failed live attempts, a stale-tester refusal, and a Docker tester-start
timeout are retained as separate artifacts; none reached the protection
assertions. Read-only inspection after those attempts confirmed no temporary
VM, container, or test dataset remained. The final post-pass inspection and
graceful guest stop are also recorded under `artifacts/`.

The real second-disk cache target, Path B reboot/import, automatic remount and
broader update survival, full UI submission, share-client access, guest OS
boot, and actual KVM selection remain open. See [`HANDOFF.md`](HANDOFF.md) for
the current matrix.

## 2026-10-18 v27 Path A UI root-create retries (resolved on r4)

The UI now fills **Quota for this dataset and all children**, blurs it, and
captures the request. It sends `quota: 2147483648`; see
[`live-ui-v27-path-a-r4-quota-request-capture-20261018.log`](artifacts/live-ui-v27-path-a-r4-quota-request-capture-20261018.log).
The first middleware refusal was the root readiness self-check. A registered
`repair-root-create` pass installed the narrow root-CREATE exemption while
preserving quota/type, fixed mountpoint, `canmount=on`, root-update, and
descendant guards. The next registered UI run reached middleware and failed
with `[EINVAL] none: path must be absolute`:
[`live-ui-v27-path-a-r4-after-root-create-guard-repair-20261018.log`](artifacts/live-ui-v27-path-a-r4-after-root-create-guard-repair-20261018.log).

The registered direct inventory
[`live-inspect-v27-path-a-r4-middleware-create-source-20261018.log`](artifacts/live-inspect-v27-path-a-r4-middleware-create-source-20261018.log)
shows no `boot-pool/user-data` root, descendant, or mountpoint, and no
`boot-disk-data`, container, VM, or probe dataset. It also captured the
installed source: `pool.dataset.do_create()` reads `boot-pool`'s
`mountpoint=none`, then treats the truthy string as a path and calls
`filesystem.stat('none')` before ZFS creation. The local fix skips this stat
only for the exact root create. A new guarded `repair-root-parent-mountpoint`
mode is bound to the current r4 bundle SHA and installed `dataset.py` SHA, and
preserves the pre-repair source and install marker for rollback. Its patch and
The later registered r4 PWMCP run created and verified the UI root, wrote a
user dataset, and created both zvols. The current r4 root is in use; do not
rerun the empty-root repair. The passed UI run and current inventory are linked
in `HANDOFF.md`. Storage creation remains UI-only; do not create this dataset
by shell, CLI, or direct API.

## 2026-10-17 v28 Path B initial existing-container destroy refusal failed (resolved)

The latest registered `live-storage` run on
`28.0.0-MASTER+20261014-230204` passed Path B dataset create/query/update,
zvol create/grow and VM disk-choice checks, temporary file-backed L2ARC
add/remove, and TrueNAS VM startup with QEMU TCG confirmed by XML and process
arguments.

After the container API created a test container, the gate verified that
`boot-disk-data/.truenas_containers/containers/<test-container>` existed.
Calling `zfs.resource.destroy` on that existing middleware-managed dataset
returned JSON `null` with a successful exit instead of a protected-path
error. The gate stopped before its post-destroy existence check. This is a
probable protection bypass, not a passing refusal test. The test entered its
`finally` cleanup and emitted no cleanup-error, but the failure log does not
affirmatively confirm cleanup. Inspect the guest read-only before reuse.

Preserved original failure log:
[`live-storage-v28-path-b-protection-refusal-failure-20261017.log`](artifacts/live-storage-v28-path-b-protection-refusal-failure-20261017.log).
The read-only inventory is
[`guest-inspection-v28-path-b-20261017.log`](artifacts/guest-inspection-v28-path-b-20261017.log).
It found no middleware container or VM records, no libvirt domains, and no
test-specific container dataset; the managed roots and Alpine image cache
remained and both pools were ONLINE. The gate's `finally` cleanup also ran, so
the post-run absence does not determine whether the destroy call or cleanup
removed the test dataset.

The guard was traced to the v28 non-boot-pool policy omitting
`.truenas_containers`. The shared helper and v28 release adapter were corrected,
and the regression now tests a live middleware-managed dataset, verifies
survival after public destroy, refuses snapshot creation, and confirms no
snapshot was created. The subsequent v28 Path B and Path A storage gates
passed; v27 Path A and Path B were then run against the same adversarial
contract and also passed. The correction and test details are summarized in
the current checkpoint above and in `HANDOFF.md`.

## 2026-10-17 v28 container protected-path regressions

The v28 Path A storage run first found three missing internal-only
`bypass=True` arguments in `container/query_pull_images.py` for orphan destroy,
image snapshot creation, and exception cleanup. Restoring those allowed the
test to reach `container/crud.py`, where v28 had replaced v27's private
`snapshot.clone_impl(..., bypass=True)` plus mount with public
`pool.snapshot.clone`. The public route correctly refused the protected
container destination.

The narrow v28 adapter now restores the internal clone call and mount only at
the middleware-owned same-pool container path. The public clone API remains
guarded. The new target's exact source hunk applied and compiled; the registered
unit lane passed with 97 tests and 45 subtests. Guarded replacement migrated
the installed 14-target adapter to 15 targets and passed with bundle SHA-256
`2a4fa86979d7234c5aa16f1b03e0f93225c35ae10ddc3a114d1bb9e7ac680a06`. Its log
is [`live-v28-patch-replacement-container-clone-bypass-20261017.log`](artifacts/live-v28-patch-replacement-container-clone-bypass-20261017.log).
The unit log is
[`unit-boot-pool-patch-v28-container-clone-20261017.log`](artifacts/unit-boot-pool-patch-v28-container-clone-20261017.log).

The full v28 Path A `live-storage` lane now passes: public clone refusal,
dataset/zvol CRUD, temporary file-backed target L2ARC add/remove, TCG VM
startup, container image clone and persistent dataset, NFS/SMB share-record
CRUD, and cleanup. The log is
[`live-storage-v28-path-a-container-clone-pass-20261017.log`](artifacts/live-storage-v28-path-a-container-clone-pass-20261017.log).
The L2ARC target was still file-backed. A separate disk-backed target pool,
full patched Path B storage acceptance on v28, UI behavior, client-side
shares, guest boot, and adapter update survival remain open.

## 2026-10-17 v28 patch gate bring-up and live pass

The first live `live-v28-patch` attempt safely refused before mutation because
the active `/usr` mount source had to be selected from `findmnt` when both v27
and v28 boot environments advertise `mountpoint=/usr`. After fixing that
selector, the second run staged a deterministic 14-file bundle (SHA-256
`a509b8ebafbb0732999848c9385abff7edb9d9e05468d8a40fcff56a6cd384f1`) and
applied the v28 storage changes, but `middlewared` crashed during import. The
trace showed `async_validators -> pool_.dataset_query_utils -> zfs.encryption_info
-> container.utils -> container.__init__ -> container_device -> async_validators`.
The v28 `dataset_query_utils` module imports `is_internal_dataset_name`; it does
not expose the `INTERNAL_DATASETS` symbol expected by the initial adapter.

The attempt was interrupted to stop the middleware restart loop. The gate
reported failure, so its rollback result is not counted as a pass. Read-only
recovery inspection then verified all 14 original target hashes, confirmed the
three adapter helper files and install manifest were absent, and confirmed
middleware was active. `/usr` had been left writable by the interrupted
transaction; it was explicitly restored to read-only and verified. The durable
pre-change archive remains under
`boot-pool/user-data/.truenas-boot-pool-patch-backups/v28.son3iF5S/`.

The v28 adapter now reads `INTERNAL_PATHS` from the lightweight
`middlewared.plugins.zfs.utils` module, which is the source-defined release
policy and avoids importing `dataset_query_utils` into `async_validators`.
Added a regression assertion for this dependency boundary. The next live run
loaded middleware, then failed the `container.pool_choices` API check because
v28 returns Pydantic `FilesystemStatfsData` objects from internal
`filesystem.statfs` calls. Direct `.get('source')` raised `AttributeError`.
The gate then rolled back, verified every original target, restarted
middleware, and restored `/usr` read-only. The same dictionary assumption
existed in the path validator, container pool listing, dataset root check, and
boot-mount hook, so the v28 call sites now use a shared mapping-or-model field
accessor with regression coverage. The registered unit lane passes with 99
tests and 45 subtests. The third live run passed on
`28.0.0-MASTER+20261014-230204`, checking the exact target fingerprints,
middleware restart, TCG support, unchanged hardware flags, boot-pool container
choice, and `/usr` read-only. Its output is saved at
`artifacts/live-v28-patch-20261017.log`; the installed-source backup
is under `boot-pool/user-data/.truenas-boot-pool-patch-backups/v28.0TAuGPQn/`.
The first v28 `live-storage` attempt stopped at read-only preflight: ZFS
confirmed `boot-pool/user-data` had the configured quota, mountpoint, and
`canmount=on`, and `container.pool_choices` exposed `boot-pool`, while
`pool.dataset.query` omitted that managed root. The next attempt reached
`pool.dataset.create` but returned `ENOENT` because v28's default query walker
pruned traversal at the internal `boot-pool` root before it could find the
newly created dataset. The earlier change that made root visibility diagnostic
was insufficient. The storage patch now makes the exact boot-pool root
traversable while keeping system and container-managed descendants hidden and
the existing mutation guards in place. The live-storage preflight again
requires Path A root visibility and still requires a newly created user-owned
descendant; Path B's ordinary pool root remains required. The protected
system-path probe also checks `zfs list` directly.

The updated bundle passed `live-v28-patch` in guarded replacement mode. The
replacement verified the exact previous bundle, installed files, and original
archive; it saved the current patch, restored stock files, and applied the new
bundle. Its log is
`artifacts/live-v28-patch-replacement-20261017.log`. A later post-reboot
`live-storage` preflight failed before mutations because the boot-mount helper
used a v28 service method that does not exist; the root remained unmounted.
The current local bundle uses `list_impl` at each v28 call site, and the boot
gate now has a recovery path for an initially unmounted root. Guarded
replacement and recovery verification remain open. Do not claim full storage
or automatic remount until those lanes pass; UI and KVM-path acceptance also
remain open.

The local QEMU runner now permits up to three managed guests by default, with
`--max-concurrent-guests 1` or `2` to lower the ceiling. An advisory lock
serializes starts across repository worktrees. Each start still compares
measured `memory.current` plus the next guest's 3.5 GiB cap against finite
`memory.high` and `memory.max` at the selected slice and every ancestor; the
measured usage includes running guests, without reserving their unused caps a
second time. The registered unit lane passes with 129 tests and 45 subtests.

## 2026-10-16 v27-to-v28 update attempt (interrupted)

The original Path A guest remains stopped cleanly with its COW state preserved
at `.vm/v27-path-a-20261016-r3`. A fresh clone was started as
`truenas-v27-path-a-20261016`, with state at
`.vm/v27-path-a-update-cdrom`. It uses the separate `dev-background.slice`
container, capped at 200% CPU and 3.5 GiB memory/swap with 3 GiB guest RAM.
QMP confirms the selected v28 ISO is attached as read-only `ide2-cd0`, while
the TrueNAS system disk remains `virtio0`. The serial console reached the
installed v27 GRUB/kernel and disk enumeration; `middlewared` has loaded all
131 plugins and started. ZFS import and network setup completed; TrueNAS has
reached user sessions, saved its SSH keys, and started SSH. The first tester
connected on the guest network and verified v27 plus the read-only CD-ROM.
Update job 65 ran `update.manual`; its last reported state was 49%, extracting
the update payload. The gate created
`boot-pool/user-data/update-proof-202610161707214f5c9eaa` with marker SHA-256
`56584b92ba4180a23551f7eb81958e062ef6a40dcbd41d921283f76d3fa82bfb` for
survival verification. The live-update lane then failed while polling job 65:
the next SSH connection timed out during banner exchange, after which run-gate
could not get another `core.get_jobs` response. It did not report update
success or request a reboot. The network tester exited with code 143; the
wrapper's stop command timed out, but inspection confirmed it is now stopped.
The VM container subsequently exited with code 137 (`OOMKilled=false`), and
the network tester exited 143. No update success, controlled reboot, or data
survival was recorded. The job-65 VM is stopped; do not invoke `update.manual` again
on this state unless a read-only query after boot confirms whether job 65
still exists. Its disk overlay and EFI state are preserved. Host-side
`qemu-img check` could not resolve the overlay's `/base/disk.qcow2` backing
path, available only inside the VM runner, so no integrity conclusion was
reached.

The guest serial log recorded RCU stalls and delayed scheduler replenishment
under TCG before it stopped. Current host profiler sample
(`2026-10-17T00:43:32Z`): host memory available 5.74 GiB; background slice
0.48/5/8 GiB current/high/max with negligible CPU pressure; interactive slice
4.88/5/8 GiB current/high/max; parent `dev.slice` 5.5/11.5/15.5 GiB
current/high/max. At that time, only one guest was admitted at once. The
current runner supports up to three managed guests, but each new start must
pass `tcg_vm.py`'s measured-memory headroom checks. Preserve this interrupted
state; a clean retry would use a separate COW clone of the known-good r3
state.
Live acceptance testers use a separate bounded `dev-gates.slice` container.

## 2026-10-17 clean update retry startup and QMP path fix

A fresh state was copied from the known-good Path A snapshot into
`.vm/v27-path-a-update-cdrom-rerun1`. `tcg_vm.py start` launched its VM under `$CGROUP_PARENT_DEV_BACKGROUND`
with the configured 200% CPU and 3.5 GiB memory/swap caps, but the host-side
QMP path was 108 bytes. The runner returned `AF_UNIX path too long` after
launch, leaving the owned container running because the raw `OSError` escaped
its startup cleanup handler. Docker inspection confirmed the managed-runner
label and resource caps. A QMP ACPI powerdown was sent through the container's
short `/state/qmp.sock`; the guest completed startup and reached the TrueNAS
27 console. Its SSH banner is open; an initial HTTPS probe timed out. The
registered live-update API preflight later succeeded over SSH/midclt before any
mutation. It verified 11,347,279,872 bytes boot-pool free against the
10,025,572,761-byte manifest requirement, confirmed read-only `/dev/sr0`, and
created marker dataset
`boot-pool/user-data/update-proof-202610170055195860778c` with SHA-256
`9bb35e28a211e93036d1ead8d66b07cfc4e7fb837704efc1fdde571ad3718cf3`. It
started `update.manual` job 63 on this fresh COW state. It passed 59% in
`Extracting`, reached 68% in `Migrating configuration database`, then advanced
to 81% in `Updating initramfs` and 88% in the final GRUB check. A read-only
middleware query reported job 63 `SUCCESS` at `2026-10-17T01:12:30Z`; the
registered gate requested a controlled reboot. The serial log records failed
unmounts for `var-log-journal.mount`, `var-log.mount`, and `var.mount`, followed
by watchdog-stop warnings, then a successful firmware restart. OVMF selected
the new default TrueNAS 28 GRUB entry, which began loading kernel
`6.18.55-production+truenas`. The v28 boot took about five minutes to return
middleware. Guest memory reached the 3.5 GiB container cap during startup and
the container reported memory-max reclaim events, but no OOM kill occurred;
host memory stayed above 3.6 GiB available. The registered live-update gate
then passed: `system.info` reported `28.0.0-MASTER+20261014-230204`,
`zpool status -x boot-pool` reported healthy, and the update-proof marker
survived with its original SHA-256. The gate manually mounted the saved
user-data datasets because the v28 boot-mount adapter was not applied yet, so
this proves data survives the update but does not prove automatic remount. Full
progress and readiness output is preserved in
[`artifacts/live-update-v27-to-v28-20261017.log`](artifacts/live-update-v27-to-v28-20261017.log).

The clean update and v28 boot were completed without starting another guest.
The v28 adapter is still unapplied. Next, add and run the registered
`live-v28-patch` lane to verify exact live target hashes, transactionally apply
the three adapters, restart middlewared, and check the v28 TCG/API capability;
then run the v28 `live-storage` and `live-boot` lanes. The v28 boot used nearly
all of the guest container's 3.5 GiB memory cap, so keep the one-guest limit
and check host and slice pressure before each subsequent gate.

The runner now preflights a short-lived QMP alias under `/tmp`, follows that
alias for host-side status/stop operations on long paths, and routes post-start
`OSError`/`ValueError` failures through graceful cleanup. A registered unit
gate passed with 89 tests and 45 subtests, including a QMP protocol test that
connects through a long state path. At `2026-10-17T00:53:58Z`, Docker reported
the guest at 18% CPU and 2.084 GiB / 3.5 GiB; the background cgroup was
2.62/5/8 GiB current/high/max, CPU PSI 0.43%, and host memory available was
5.15 GiB. At the latest job poll the background cgroup was 3.94/5 GiB with
0% memory PSI and 2.71% CPU PSI; host memory available was 3.76 GiB. The
interactive slice was 3.39/5 GiB at the earlier sample. The separate
background container remains the only local TrueNAS guest.

The official update metadata redirects `TrueNAS-27-Nightlies` to
`TrueNAS-28-Nightlies`, but the 28 train's `releases.json` endpoint currently
returns 404. The selected ISO embeds the exact 28 update file, so the live test
can use `update.manual` instead. Its manifest requires 10,025,572,761 bytes of
boot-pool free space. The retained 16 GiB guest reports 11,348,213,760 bytes
available before staging; copying the 2,327,560,192-byte update file onto that
pool would consume the headroom needed by the installer. QEMU's default empty
IDE CD-ROM is not hotpluggable on this `q35` machine, so media must be attached
at VM start.

The first media attempt exposed a boot-order issue: the hybrid ISO was
attached as a virtio hard disk and OVMF selected its EFI partition. Starting
that EFI image opened the TrueNAS installer. The update was not started and
the target disk was not selected; QMP reported the COW system disk clean and
the ISO read-only. The guest eventually shut down cleanly after ACPI powerdown;
the runner did not force-stop it. The failed COW and firmware state is kept at
`.vm/v27-path-a-update-virtio-boot-failure` for inspection. The
runner now attaches an ISO with QEMU's `-cdrom` option, preserving hard-disk
boot priority while exposing `/dev/sr0` read-only. Live verification confirms
TrueNAS 27 booted from `virtio0`, not the ISO installer; QMP and guest
`blockdev --getro /dev/sr0` both confirmed read-only media.

The guarded `live-update` lane and exact ISO/update metadata are implemented.
The registered unit gate most recently passed with 81 tests and 45 subtests.
An initial live-gate attempt exposed a host/container namespace error: the
runner called `Path.resolve()` on Docker's host-side ISO source inside the
devcontainer. It now compares normalized Docker host-path strings without
resolving them locally, and the unit gate covers that boundary. The live
update attempt is interrupted as described above. After a future update
completes, the v28 source adapter must be reapplied, followed by boot-remount
and API acceptance.

## 2026-10-16 v28 boot-mount adapter source check

The selected v28 nightly has the same source-level reboot gap seen in v27:
`pool.import_on_boot` imports only pools registered in `storage.volume`, then
runs `pool.post_import`; the boot pool is not in that registration loop. Its
rootfs contains `zfs-mount.service` but no enablement link under
`/etc/systemd/system/zfs.target.wants`; live v28 service state remains to be
confirmed. Added a separate v28 `boot-mount.patch` that imports and calls a
release-specific [`boot_pool_mount.py`](v28-nightly-20261014/boot_pool_mount.py)
helper before `pool.post_import`. The helper uses v28's `INTERNAL_PATHS`,
`CONTAINER_DS_NAME`, and boot-pool import path while preserving the shared
namespace, quota, mountpoint, symlink, ownership, and hidden-dataset guards.

All 14 v28 patch-target fingerprints matched the selected ISO rootfs. The
storage, TCG, and boot-mount patches dry-ran and applied together in a
temporary copy; all 14 patched targets and all three helper modules compiled.
The new artifact test asserts the release-specific adapter boundaries. This
remains source-only evidence: no v28 host has booted the candidate patch, and
reboot, API, UI, KVM/TCG, data-survival, and update checks remain open.
The retained v27 guest's live API registry exposes `update.download` and
`update.run` with explicit `train` and `version` arguments; `update.run` also
accepts a no-reboot option, allowing the update job to finish before a
controlled reboot. A read-only `update.available_versions` query on its
current `TrueNAS-27-RC` profile returned an empty list. At the time of this
source-only check no update job had started; a later attempt started job 65 as
recorded above. Any retry must use the selected artifact and a separate
copy-on-write state.

## Develop a v27+ single-disk storage patch

The source comparison is recorded in
[`README.md`](README.md). The 25.10.4 patch
applies to the 25.10.7 versions of its seven target files, but not to v27 or
v28. v27 has separate `vm.*` and `container.*` middleware and the v28 nightly
changes Docker/container paths again.

- [x] Read the v28 ISO's embedded build metadata and compare its packaged
  TCG domain-type and KVM-detection files with upstream. The ISO gives no
  middleware Git commit, so the candidate source mapping remains unverified.
- [x] Check the installed v27 RC VM middleware files against its source tree
  and inspect its `truenas_pylibvirt` package. Middleware files matched the RC
  source; the VM XML generator hardcodes KVM, unlike the v28 TCG fallback.
- [x] On a local TrueNAS 27 RC1 VM, create a ZFS pool from a dedicated free
  partition and confirm middleware registration, dataset/zvol creation, and
  normal API choice results. The 20 GiB test disk had an unallocated 4 GiB tail
  after the 16 GiB boot-pool image, unlike a stock install that assigns the
  whole disk to partition 3. Reboot auto-import and a marker file survived.
- [x] Compare that with a boundary-aware boot-pool user-data namespace while
  keeping `.system`, `ROOT`, `grub`, and service datasets protected. The
  boot-pool root has `mountpoint=none`; the Path A adapter sets an explicit
  mountpoint and finite quota, and the earlier API and exploratory UI checks
  passed. The fresh patched v27 Path A/B PWMCP storage gate is separate and
  remains open; see the current checkpoint above. The first
  reboot test found that `zfs-mount.service` is disabled and
  `pool.import_on_boot` does not import the boot pool from `storage.volume`.
  The new v27 `boot-mount.patch` mounts the safe namespace after regular pool
  imports; registered live-boot acceptance now checks root/child mount and
  marker persistence after reboot. TrueNAS update survival remains open.
- [x] Implement a fail-closed Path B provisioner and registered destructive
  lane. It checks the single-disk GPT, confirms partition 3 is the sole
  boot-pool vdev, uses only an aligned free tail, stores a GPT backup, verifies
  all existing extents, creates partition 4 without force, and registers the
  pool through middleware. Unit coverage checks refusal and completed-state
  idempotency. It deliberately leaves partial state for inspection rather
  than attempting a potentially destructive rollback.
- [x] Run `live-path-b-provision` on the v28 nightly guest with a reserved
  tail. It created `/dev/vda4`, registered `boot-disk-data` ONLINE through
  middleware, preserved partitions 1–3, and passed a no-change idempotency
  rerun. The live provisioner log is
  [`live-path-b-provision-v28-path-b-20261017.log`](artifacts/live-path-b-provision-v28-path-b-20261017.log).
- [x] Run the provisioner live on v27 and complete registered Path B storage
  acceptance on v27 and v28. The v27 provision and storage lanes reused the
  Path A COW guest after provisioning; see the current checkpoint above.
- [ ] Run Path B reboot/import acceptance on v27 and v28. Path A reboot
  remount is proven; Path B reboot/import and update survival remain open.
- [x] Apply the complete test-only v27 patch to disposable overlays and verify
  patched API capability, false hardware flags, VM create, zvol attachment,
  QEMU XML, and QEMU TCG process startup with `/dev/kvm` absent. Also start a
  `HOST-MODEL` VM and a `CUSTOM`/null-model CPU payload; verify filesystem
  dataset CRUD and sparse zvol create/grow/delete. Container lifecycle,
  persistent user-dataset mounting inside a container, and NFS/SMB share-record
  create/query/delete passed on both Path A and Path B. The Path A acceptance
  log is recorded above. The test VMs had no guest OS, so guest boot is not
  proven. NFS/SMB client access remains untested.
- [x] Complete the earlier exploratory PWMCP UI pass on Path A and Path B:
  user-dataset selection and creation with protected system datasets hidden,
  container pool selection, VM wizard through CPU/memory fields, and SMB/NFS
  sections and Add actions. The VM wizard was not submitted, and no share was
  created through the UI. That exploratory pass did not close fresh patched
  storage acceptance at the time. The later r4 Path A `live-ui` gate passed
  the dataset, container storage selection, VM wizard, and cache-zvol checks;
  fresh Path B UI remains open in the current checkpoint above.
- [x] Draft the v28 TCG/storage adapters and separate boot-mount adapter;
  extract and fingerprint all 14 targets from the nightly ISO, then
  dry-run/apply/compile all three patch files with their helpers. The full
  middleware Git commit is still unknown.
- [x] Verify a disposable TrueNAS QEMU runner can be placed under
  `$CGROUP_PARENT_DEV_BACKGROUND`, that Docker applies the requested leaf
  CPU/memory limits, and that a clean v27 RC1 guest boots and answers SSH,
  `midclt`, and HTTPS checks. The live background slice is capped at 500% CPU
  and 5/8 GiB memory high/max; its parent `dev.slice` is capped at 700% CPU
  and 11.5/15.5 GiB. At the time, starts were limited to one guest. The current
  runner allows up to three, subject to 200% CPU, 3.5 GiB container
  memory/swap caps, 3 GiB guest RAM, and measured cgroup headroom per start.
  The registered unit gate
  passed with 70 tests and 45 subtests; both Path A and Path B live-storage
  acceptances passed. Recheck current usage before each start.
- [x] Apply the v28 adapters to the disposable updated guest and verify the
  guarded 15-target patch, reboot remount, and TCG VM path with `/dev/kvm`
  absent. Full Path A storage acceptance now passes with container image clone
  and public protected-path refusal.
- [ ] Verify KVM remains selected when `/dev/kvm` is available; the TCG guest
  cannot establish that path.
- [ ] Test SMB/NFS client access, VM guest boot, and update survival with
  custom data present.
- [ ] Test user data and pool survival through updates from the latest 25.10
  maintenance release to v27 and the chosen v28 nightly.
- [x] Extend PWMCP Path A acceptance to submit the VM wizard with an existing
  boot-pool zvol and verify the resulting VM device record. The fresh patched
  r4 run passed; see the current checkpoint above.
- [ ] Extend PWMCP acceptance to create/query/delete dataset-backed shares in
  the UI, then verify NFS/SMB client access. The r4 run only opened the share
  pages and Add actions. Keep the 25.10.4 failed UI report separate; it does
  not negate the limited v27 Path A/B checks that passed.



## 2026-10-17 Updated guest CPU observation

The retained `truenas-v27-path-a-20261016` VM completed the v27-to-v28 update
and currently reports `28.0.0-MASTER+20261014-230204`. `middlewared` is active,
`boot-pool` is healthy, SSH works, and HTTPS responds. Its guest load average was
about 0.4 and the guest was mostly idle, while its QEMU TCG container used
roughly 20% host CPU with little contemporaneous disk activity. This is not
evidence that a live acceptance lane is progressing; it is the cost of keeping
the TCG guest running. The guest remains available for the next controlled
patch/storage test.

## 2026-10-17 Cache target coverage

The v27 and v28 Path A and Path B storage acceptances create a zvol in the
selected boot-disk-backed pool and add it as an L2ARC vdev to a temporary
file-backed pool. All four gates passed that step. The live lane can target a
different imported pool through `TRUENAS_TEST_CACHE_TARGET`, but no
separate-disk target has been tested. The operation uses the OpenZFS CLI and
does not establish TrueNAS UI or middleware support for choosing a zvol as a
cache device.

- [ ] Add a second virtual disk to the managed QEMU guest and create a
  disposable ordinary data pool on it.
- [ ] For Path A and Path B, use a zvol from the boot-disk-backed pool as the
  cache device for that separate data pool; verify add, status, remove, and
  pool health through middleware-backed acceptance.
- [ ] Reboot and check import ordering when the backing zvol is on boot-pool
  (Path A) or boot-disk-data (Path B), including the target pool's behavior
  when the cache device is unavailable.
- [ ] Inspect whether TrueNAS API and UI can manage the intended cache-vdev
  operation; retain a CLI-only workflow if the product does not expose it.

## 2026-10-17 v28 storage preflight after reboot

The v28 Path A `live-storage` gate reached preflight and stopped before creating
test datasets or other resources: `container.pool_choices` returned `{}`. The
root dataset was visible through `pool.dataset.query` and `zfs.resource.list`,
but `filesystem.statfs /mnt/boot-pool/user-data` resolved to the parent `/mnt`
filesystem, confirming that the ZFS dataset was not mounted. Middleware logs
identified the cause: both root and descendant queries in the v28 boot-mount
helper called `ZFSResourceService.query_impl`, which does not exist in this
release. The v28 service exposes `list_impl`. The storage validator used the
same nonexistent method. All three call sites now use `list_impl`, and artifact
tests assert that the v28 service-method contract.

The `live-v28-patch` gate now allows a correctly unmounted root after patch
replacement while confirming the dataset query and deferring the mounted-pool
choice check to reboot acceptance. Guarded replacement passed on 2026-10-17
after verifying the prior installed bundle and backups. The first `live-boot`
attempt created a unique marker dataset, then stopped before reboot because
TrueNAS's `zfs unmount` does not accept `-r`; its cleanup removed that dataset
and read-only inspection confirmed the root remained mounted. The gate now
lists mounted filesystems and unmounts them deepest first, verifying each
unmount without force. The corrected reboot test passed on 2026-10-17: root
and child remount, marker persistence, boot-pool health, container pool
choices, and cleanup all passed. Its log is
[`live-boot-v28-path-a-20261017.log`](artifacts/live-boot-v28-path-a-20261017.log).
The preflight failure made no test mutations. After the boot-mount fix, the
full v28 Path A `live-storage` lane passed; its complete scope and log are
recorded in the current status section at the top of this file.

## Auxiliary vdev roles and session timeout UI check (2026-10-19)

The new registered `live-vdev-topology` lane is intended to test all three
auxiliary roles against two disposable target topologies: cache, log, and
special vdevs on a one-disk pool and a RAIDZ1 pool. It uses UI-created 128 MiB
zvols stored in the Path A `boot-pool/user-data` tree and API `pool.create` /
`pool.update` calls. A 4 GiB sparse disk backs the single-disk target and three
1 GiB sparse disks back RAIDZ1. The RAIDZ1 special vdev is a mirror made from
two zvols; because both zvols live on the same physical boot disk, this tests
the API redundancy rule but not physical redundancy. The live lane passed as a
safe-refusal diagnostic: middleware rejected the zvol devices for every role,
and direct post-attempt checks confirmed both target pools remained ONLINE.
This is not auxiliary-vdev feature acceptance.

PWMCP created the seven 128 MiB zvols under the UI-created root. The UI wizard
visited Cache, Log, and Special, but listed no boot-pool zvols by name. Both
`pool.update` attempts included cache, log, and special roles and failed with
`[EINVAL] ... disks were not found in system` for the `/dev/zvol/...` paths.
`pool.query` and `zpool status -P` confirmed empty role arrays and healthy
single-disk and RAIDZ1 pools. The lane cleaned both pools and its UI test tree;
it did not reach `zpool iostat` writes or metadata-allocation checks. See
[`live-vdev-topology-v27-path-a-r1-ui-role-steps-retry3-20261018.log`](artifacts/live-vdev-topology-v27-path-a-r1-ui-role-steps-retry3-20261018.log)
and
[`live-inspect-v27-topology-a-r1-after-role-ui-cleanup-20261018.log`](artifacts/live-inspect-v27-topology-a-r1-after-role-ui-cleanup-20261018.log).

The first topology guest stopped at the UEFI shell: extra PCI disks took the
boot disk's previous address. `tcg_vm.py` now pins extra-disk addresses above
the boot disk, with argv coverage in `tests/test_tcg_vm.py`. The successful
topology run used fresh r1 after patch installation and registered read-only
inventory. The UI probe visited all role steps but found no boot-pool zvols by
name; it did not submit role assignments. Its initial failed live-inspect log is
`artifacts/live-inspect-v27-topology-a-fresh-r1-uefi-device-order-failure-20261018.log`.

Topology guest r2 had the corrected PCI map and reached middleware startup after
manually selecting its verified `EFI/BOOT/BOOTX64.EFI` loader. It did not pass
registered prepatch inventory or patch gates and has been retired. Use a fresh
guest, install the patch through the registered lane, and pass read-only
inventory before another API/UI topology test.

If middleware accepts the role devices, the lane checks schema acceptance,
ZFS topology and health, a `sync=always` write/fsync probe, and per-device
`zpool iostat` writes to the configured log and
special vdev devices, and UI pool visibility. It opens the UI wizard without
submitting to record role-selector visibility and whether zvols appear. Sparse
disks suffice for those control-plane checks, but not performance, endurance,
power-loss safety, physical redundancy, import ordering, or production sizing.
The gate creates zvols in the UI and assigns them by API; testing whether the
TrueNAS UI wizard can submit zvols as Cache, Log, or Metadata remains open. OpenZFS
special vdevs are critical pool storage: keep special redundancy at least as
strong as the data vdev and destroy disposable RAIDZ1 test pools during
cleanup because their special vdev cannot be removed as a whole.

The failed Path A root attempt came from filling the dataset-only **Quota for
this dataset** field, which submitted `refquota` instead of the guarded
`quota`. The UI script now targets **Quota for this dataset and all
children**, checks the field's `input-quota` binding, and records the browser
RPC payload. The preserved registered UI log
`artifacts/live-ui-v27-path-a-r4-root-created-later-vm-zvol-failure-20261018.log`
shows `quota=2147483648`; the UI-created root then passed direct quota,
mountpoint, canmount, and live-mount checks before that run failed later in
the VM zvol step. Earlier wrong-field and mountpoint attempts remain in the
neighboring `live-ui-v27-path-a-r4-root-*-20261018.log` artifacts. Keep the
quota guard intact.

The user-facing timeout question is resolved on the tested 27.0.0-RC.1 build:
open the signed-in account menu → **Preferences** to find **Session Timeout**.
The live UI showed 300 seconds. Activity resets the per-user idle timer. The
official Preferences guide documents a 300-second default and a 2,147,482-
second maximum; the Advanced Settings guide also describes **System → Advanced
Settings → Access → Configure**. The topology test reauthenticates before final
UI inventory if the session expires. No inventory of UI-missing settings is
needed for this question because this setting is exposed in Preferences.

- [x] Run the registered v27 Path A topology diagnostic on a fresh patched
  guest. Sparse single-disk and RAIDZ1 pools were created, the UI role steps
  were visited, API refusal was recorded, and post-attempt pool health and
  cleanup were directly verified. This is a safe-refusal result only.
- [ ] Determine a supported TrueNAS route to expose boot-pool zvols or
  partitions as auxiliary-vdev devices in both the UI and middleware API.
  Do not bypass disk inventory or the current refusal guard.
- [ ] Once devices are accepted, test cache, log, and special roles
  individually and concurrently, including one identical block device reused
  across roles; then verify actual SLOG writes and special metadata allocation.
- [ ] Repeat the source-pool test with Path B `boot-disk-data` only after the
  UI can create/register it. Separately test whether the UI can create both
  `/dev/vda4` and the pool; retain the guarded partition-only lane as the
  baseline and do not substitute whole `/dev/vda` or the CLI provisioner.
- [ ] Test a zvol auxiliary device from a separate-disk source pool against a
  different target pool. The current lane only attempted distinct zvols from
  the same physical boot disk, and middleware rejected them before ZFS use.
- [ ] Keep real-disk cache, reboot/import ordering, pool behavior when the
  backing pool is unavailable, physical fault tolerance, and representative
  performance tests open.
