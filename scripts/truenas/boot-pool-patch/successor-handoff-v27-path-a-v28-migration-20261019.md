# Successor prompt: v28 Path B rerun, v27-to-v28 workloads, and Path B UI

Continue the TrueNAS boot-pool patch work in this worktree. First resume the
fresh v28 Path B COW guest state already created from the verified installed
base. Its registered read-only `live-inspect` attempt was refused before
tester creation because host memory PSI exceeded admission limits; the guest
was then cleanly stopped to return memory. Wait for TCG start admission to
pass, resume it, run inventory after live-gate admission passes, and only then
run the requested v28 Path B `live-storage` gate. Then complete the
v27-to-v28 migration proof for Path A workloads created through the UI, and run
the explicit Path B `preprovisioned` UI route on a fresh v27 guest. Keep UI
pool creation and downstream UI operations as distinct claims. The user has
authorized fresh VM setup, removal of obsolete VM overlays, fixes along the
way, and commits in this worktree.

## Resume facts after the latest host restart

- Run-Gate has no active job. The expected newer v28 Path B `live-storage` run
  left no durable verdict or artifact; record it as interrupted/lost. The
  October 17 v28 Path B PASS is historical and remains valid only for that
  earlier run.
- A fresh guest, `truenas-v28-path-b-clean-20261021`, was started in
  `artifacts/v28-path-b-clean-20261021/` from the verified
  `artifacts/v28-path-b-base.qcow2` base, with a clean COW overlay and no ISO.
  It booted to the TrueNAS console and was then cleanly stopped with
  `tcg_vm.py stop`; its overlay and firmware state remain. The stop returned
  exit code 0 and `oom_killed=false`. It ran with 2 CPUs and a 3.5-GiB
  container memory cap on `dev-background.slice`.
- The base's root SSH key is configured, but `systemctl is-active ssh` returned
  `inactive`. SSH was started temporarily from the disposable guest console;
  the fresh guest host key is pinned in its `known_hosts`. A reboot may require
  starting the SSH unit again.
- Registered read-only `live-inspect` was attempted and refused before tester
  creation. The log reports 7.12% full memory PSI in `dev-gates.slice` against
  the 1% limit. Later direct reads also refused on ancestor `/dev.slice` and
  host PSI. After the guest stopped, TCG resume admission still refused at
  3.68% full in `dev-background.slice`; live-gate admission refused at 39.94%
  full in `dev-gates.slice`, 14.75% in `/dev.slice`, and 12.52% at `/`. No
  inventory or storage mutation has run on this new guest. Details are in
  `artifacts/tcg-vm-v28-path-b-clean-20261021-resume-checkpoint.log`.
  At approximately 04:03 UTC the guest slice had recovered to 0% PSI, but
  `/dev.slice` was still 88.23% some / 74.47% full, `/` was 81.99% / 63.68%,
  and `dev-gates.slice` was 25.53% / 25.53%. Do not resume the VM or launch a
  tester until both managed admission checks pass.
- After the host reboot, four supported `tcg_vm.py start --resume` attempts
  again refused before container creation: `/dev.slice` full PSI avg10 was
  5.19% and 6.54% against the 1% limit; some PSI avg10 was 51.7% and 10.63%
  against the 10% limit. Final read-only status is `exited`, exit code 0,
  `oom_killed=false`; Run-Gate has no inflight job. No post-reboot inventory,
  Path B provisioning, or `live-storage` result exists. Exact outputs are in
  `artifacts/tcg-vm-v28-path-b-clean-20261021-resume-after-host-reboot.log`.
- The old v28 Path B container is absent. Its `runner.json`, overlay, firmware,
  and base disk remain; the base identity matches the manifest and QEMU reports
  a healthy 20-GiB virtual disk. Host-side `qemu-img info --backing-chain` on
  the overlay cannot resolve `/base/disk.qcow2` because that is the container
  mount path, not a host path. This did not prove the disk was missing.
- The recorded boot-pool installer ISO is absent, with expected SHA-256
  `3fcb9bcdee4b597f1498f019890c1735fb4c68b55fcc94ed1d98fc40d9fd09d1`. The
  available final7 image is 645,120 bytes larger and is not an exact
  replacement. The old runner manifest does not permit resuming without its
  recorded media. The verified installed base disk can be used directly for a
  fresh managed COW guest without attaching media; create a new empty state
  directory and omit `--readonly-media`. Pass registered read-only inventory
  before mutation.
- The earlier v27 migration-resume checkpoint was refused by TCG admission;
  that guest was not started and no update ran. The saved v27 migration overlay
  and update ISO remain. Current v28 gate admission is tracked separately in
  the resume checkpoint log above.

### Current v28 guest: status and supported resume command

The guest and state directory below already exist and the guest is currently
stopped. Do not run the command if `tcg_vm.py status` shows it running. This
command resumes its existing overlay only after the runner's live preflight
admits it. Do not remove or replace its state files.

```bash
cd /workspaces/vbpub/.worktrees/truenas-restore-iso
source /home/vscode/.venv/bin/activate
: "${DOCKER_HOST:?must be provided by the environment}"
: "${CGROUP_PARENT_DEV_BACKGROUND:?must be provided by the host}"
: "${DOCKER_NETWORK_INTERNAL:?must be provided by the host}"
state_dir="$PWD/scripts/truenas/boot-pool-patch/artifacts/v28-path-b-clean-20261021"
python3 scripts/truenas/tcg_vm.py start \
  --name truenas-v28-path-b-clean-20261021 \
  --base-disk "$PWD/scripts/truenas/boot-pool-patch/artifacts/v28-path-b-base.qcow2" \
  --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --ovmf-vars "$PWD/scripts/truenas/vanilla-iso-patcher/artifacts/qemu-test/v28-install/OVMF_VARS_4M.fd" \
  --state-dir "$state_dir" \
  --image sha256:33c7ffe1dfa6dc9fb906b139c772bfda8d4e776637e2f20cea71a813ef65f41a \
  --network "$DOCKER_NETWORK_INTERNAL" \
  --cgroup-parent "$CGROUP_PARENT_DEV_BACKGROUND" \
  --ssh-port 2225 --https-port 8446 \
  --resume
```

For the current guest, SSH was inactive until temporarily started from the
TrueNAS console. Its strict host-key file is already present. After the
resource preflight passes, run registered `live-inspect` read-only with
`TRUENAS_STORAGE_PROFILE=path-b`, expected version
`28.0.0-MASTER+20261014-230204`, and port 2225. Confirm `boot-pool` ONLINE,
the reserved tail and `boot-disk-data` baseline, and no test objects before
mutation. Run `live-path-b-provision` and `live-storage` with the same guest
only after that inventory passes. Keep all generated state under the patch
artifact directory.

## Goal contract

Prove, with fresh registered live evidence, whether the same UI-created
filesystem, container, VM record, and VM zvol survive a v27-to-v28 update and
work before and after the v28 adapter is applied. Keep these outcomes separate:

1. The v27 update installs and boots v28 successfully.
2. Existing records and stored bytes survive the update.
3. Before the v28 patch, data and UI workload operations work in the guest's
   natural post-update state, without a manual mount masking a boot problem.
4. After applying the exact v28 patch and rebooting, the same data remains
   accessible and the container and VM can be cycled through the UI.

Do not call data preservation, automatic mounting, UI operation, or guest OS
boot the same result. The current VM is a small test fixture; its VM has a blank
disk and no installed guest OS.

Keep TrueNAS's KVM-first policy: use hardware virtualization when available,
and QEMU TCG when KVM is unavailable. Current live evidence exercises TCG only.

## Context to read first

- `scripts/truenas/boot-pool-patch/TODO.md` — current checkpoint and open
  acceptance list.
- `scripts/truenas/boot-pool-patch/README.md` — Current status and “v27 Path A
  API and UI boundary”.
- `scripts/truenas/boot-pool-patch/docs/DESIGN-GUIDE.md` — “TCG VM path”,
  “PWMCP storage UI acceptance”, “Preserving UI-created workloads for reboot
  and update checks”, “Required validation before calling this a patch”, and
  “Read-only update media”.
- `scripts/truenas/boot-pool-patch/docs/CONSUMERS.md` — “Run PWMCP UI storage
  acceptance”, “Run live update acceptance”, and “Apply and verify the v28
  adapter”.
- `scripts/truenas/boot-pool-patch/acceptance/live_update.py`,
  `live_ui_workloads.py`, `workload_manifest.py`, `live_inspect.py`,
  `live_v28_patch.py`, and `live_boot.py`.
- `scripts/truenas/boot-pool-patch/run-live-gate.py`,
  `scripts/truenas/run-gate.toml`, and `scripts/truenas/tcg_vm.py`.
- `scripts/truenas/boot-pool-patch/shared/virtualization_support.py` and
  `scripts/truenas/boot-pool-patch/tests/test_virtualization_support.py`.
- `AGENTS.md` for worktree, gate, environment, and commit rules.

## Proven baseline and boundaries

Fresh guest `truenas-v27-golden-ui-migration-20261019` ran TrueNAS
`27.0.0-RC.1` with v27 patch bundle SHA-256
`1681d625e14f980fb488e8d6a01a447de7962b15bb2772309609d17d2d1192e9`.
`/dev/kvm` was absent in the guest; the patched VM backend selected TCG.

| Evidence | What it proves | What it does not prove |
|---|---|---|
| `artifacts/live-ui-v27-path-a-golden-ui-migration-20261019.log` | UI created Path A root/filesystem, wrote a marker, created zvols, selected `boot-pool` for a container, and submitted a VM wizard using a boot-pool zvol. | Container access to a user-mounted data volume; guest OS boot; update migration. |
| `artifacts/live-ui-workloads-v27-golden-ui-migration-20261019.log` | TrueNAS UI Start/Stop for the container and blank VM; backing data hashes remained stable. | Guest OS boot or a container app reading a mounted user-data volume. |
| `artifacts/live-boot-v27-golden-ui-migration-20261019.log` and `artifacts/live-inspect-v27-golden-ui-migration-20261019-post-reboot.log` | v27 reboot remounted the Path A root and child filesystem; recorded data remained readable; pool was healthy. | v28 update behavior. |
| `artifacts/live-ui-workloads-v27-golden-ui-migration-20261019-post-reboot.log` | VM autostarted after the v27 reboot, and UI Start/Stop actions worked for both records; data hashes remained stable. | v28 update behavior, VM guest OS boot, or a user data volume mounted into the container. |
| `artifacts/unit-boot-pool-patch-installed-run-gate-delegation-20261021.log` | Registered unit lane passed: 158 tests and 45 subtests after the live-gate wrapper switched to the installed Run-Gate CLI. | Live KVM selection; live v28 update. |
| `artifacts/unit-boot-pool-patch-path-b-ui-preprovision-mode-20261021.log` | Registered unit lane passed after the candidate Path B `preprovisioned` UI mode was added: 170 tests and 45 subtests. | Fresh live Path B downstream UI acceptance; UI pool creation. |

The manifest is
`scripts/truenas/boot-pool-patch/artifacts/v27-golden-ui-migration-20261019/ui-workloads.json`
(SHA-256 `4c0d8958516bb4b1b2a9b198fb05e7dacd1d529da982f6c0525c059c2b225ac8`).
It identifies the UI-created dataset, container id/name, VM id/name, VM zvol,
and filesystem/zvol probe hashes. Use this same manifest for this migration
unless a fresh VM must be built; if rebuilt, use its newly emitted manifest and
re-record its hash.

The latest v27 guest was cleanly stopped after a registered read-only
inventory and UI workload cycle passed on October 21. Its managed container is
`truenas-v27-golden-ui-migration-20261019`, and its state is at
`scripts/truenas/boot-pool-patch/artifacts/v27-golden-ui-migration-20261019/vm-state`.
The overlay remains on disk, and `runner.json` records the attached read-only
v28 ISO with SHA-256
`9312f77d0a94f54dc0396050bd26bb31ea0e81d62b124e30a94075a006e7bc9a`.
Two early SSH inventory attempts timed out during TrueNAS startup; a later
inventory passed. The serial log then showed a second kernel-start sequence
about nine minutes later, with no explicit shutdown reason. A second inventory
and the UI workload cycle passed after that boot. Preserve the double boot as
unexplained; do not attribute it to the patch or provider. Before the update
mutation, run registered read-only `live-inspect` again. If the guest is not
reliably reachable, create a fresh guest from the golden image and regenerate
the UI workload manifest. Fresh evidence takes priority over keeping this
overlay.

An earlier same-day `run-gate doctor` sample reported memory PSI full avg10 of
2.21% in `dev.slice` and 3.2% on the host. A later sample reached 19.9% in
`dev.slice` and 16.66% on the host; the TCG runner's full-PSI limit is 1%. No
VM was started on this resume. Earlier samples and two managed admission
refusals remain recorded in
`artifacts/tcg-vm-start-admission-v27-golden-ui-migration-20261021-post-reboot.log`.
These values are snapshots; use only the managed runner's live preflight for
admission, and do not bypass or raise its limits.

Run-Gate currently has no job in flight. Its latest `live-storage` entry is
the 2026-10-19 v27 Path A PASS; no new v28 Path B run is active. The durable
v28 Path B existing-container-protection PASS remains from October 17. After
the host restart, the expected newer v28 Path B run left no durable completion
record or new artifact; treat it as interrupted/lost, not passed or failed.
`run-gate status` confirms there is no in-flight job.

The v28 target is
`28.0.0-MASTER+20261014-230204`, from
`scripts/truenas/boot-pool-patch/artifacts/TrueNAS-28.0.0-MASTER-20261014-230204.iso`.
Its ISO SHA-256 is
`9312f77d0a94f54dc0396050bd26bb31ea0e81d62b124e30a94075a006e7bc9a`;
the matching metadata is
`scripts/truenas/boot-pool-patch/v28-nightly-20261014/update-artifact.json`.
The metadata's `update_sha256` is
`96f0694c5541cc25d7c3b981943faf3f5b6bddfb28f72204e0370d185283a02c`.
Use these pinned artifacts for this run.

The v28 Path B existing-container protection gate passed previously at
`artifacts/live-storage-v28-path-b-existing-container-protection-pass-20261017.log`.
After the October 21 host restart, no newer Path B gate was active. The old
guest container is absent. Its `runner.json`, overlay, firmware, and base disk
`artifacts/v28-path-b-base.qcow2` remain; the base identity matches
`runner.json` and QEMU reports a healthy 20-GiB virtual disk. The exact
read-only ISO recorded in `runner.json` is missing (expected SHA-256
`3fcb9bcdee4b597f1498f019890c1735fb4c68b55fcc94ed1d98fc40d9fd09d1`). The
different final7 image is 645,120 bytes larger and must not be substituted.
Host-side qemu-img cannot resolve `/base/disk.qcow2` because that is the
container mount path. Do not resume the old overlay with missing or different
media. Use the fresh COW guest recipe above, which starts from the verified
installed base without attaching media, and pass registered read-only
inventory before mutation.

The same restart left `truenas-v27-golden-ui-migration-20261019` cleanly
stopped (`Exited (0)`, `oom_killed=false`) with its overlay and firmware state
intact. The managed guest start was previously refused twice by its live
`/dev.slice` PSI admission check after external readings briefly appeared low.
The migration guest's saved overlay and firmware are present. The latest
`run-gate doctor` snapshot is above the TCG runner's 1% full-PSI limit, so no
VM was started on this resume. Earlier same-day measurements are historical;
use only `tcg_vm.py` live admission before attempting another start.

PWMCP is healthy on the internal network: `pwmcp doctor` reports release
1.64.0-r2 and Playwright 1.64.0. The v27 migration guest remains `Exited (0)`,
`oom_killed=false`; the old v27 Path B diagnostic guest container is absent.
Use a fresh managed guest for Path B acceptance. After the final doc edits,
the registered unit lane passed with 170 tests and 45 subtests; no Run-Gate
invocation remains in flight.

The current uncommitted worktree also adds a candidate Path B UI setup resolver
and `TRUENAS_PATH_B_SETUP` forwarding. Its `preprovisioned` mode runs the
registered strict provisioner first, then checks downstream UI storage
operations without submitting `pool.create`. Its registered unit gate passed with 170 tests and 45 subtests; fresh live UI
acceptance remains open. The existing `ui-wizard` mode remains the test of
UI partition/pool creation and still safely stops when the wizard cannot see
`/dev/vda4`.

Cache answer from existing evidence: v27 and v28 Path A and Path B storage
gates each attached a source zvol to a different pool as L2ARC and removed it
successfully. Those target pools were temporary and file-backed. A separate
disk-backed target, source/target import ordering, and TrueNAS UI/middleware
cache-device management remain untested.

## Work sequence

1. Check `git status --short` and branch. This worktree also contains unrelated
   `scripts/netcup/` changes; never stage or commit them. Read the TrueNAS
   source diff before changing it.
2. Activate `/home/vscode/.venv/bin/activate` for host-side Python and set
   `DOCKER_HOST=unix:///var/run/docker.sock`. Read
   `$CGROUP_PARENT_DEV_BACKGROUND` and `$CGROUP_PARENT_DEV_GATES`; if required
   facts are absent, stop at the runner's preflight. Do not hardcode a slice.
3. Resume the managed guest using `tcg_vm.py` and its existing state. From the
   worktree root, use the recorded paths and require the declared cgroup fact:

   ```bash
   cd /workspaces/vbpub/.worktrees/truenas-restore-iso
   source /home/vscode/.venv/bin/activate
   export DOCKER_HOST=unix:///var/run/docker.sock
   : "${CGROUP_PARENT_DEV_BACKGROUND:?must be provided by the host}"
   python3 scripts/truenas/tcg_vm.py status \
     --name truenas-v27-golden-ui-migration-20261019 \
     --state-dir scripts/truenas/boot-pool-patch/artifacts/v27-golden-ui-migration-20261019/vm-state
   ```

   If the stopped container name blocks resume, remove that stopped container
   with `tcg_vm.py destroy`; the disk and firmware state files remain. Resume
   with the exact base, firmware, bridge, cgroup parent, and read-only ISO:

   ```bash
   python3 scripts/truenas/tcg_vm.py start \
     --name truenas-v27-golden-ui-migration-20261019 \
     --base-disk /workspaces/vbpub/scripts/truenas/golden-iso/vm/truenas-27.qcow2 \
     --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd \
     --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd \
     --state-dir scripts/truenas/boot-pool-patch/artifacts/v27-golden-ui-migration-20261019/vm-state \
     --image debian-install-vm:ab8643b97cc8 \
     --network dstdns-hox0ju-network \
     --cgroup-parent "$CGROUP_PARENT_DEV_BACKGROUND" \
     --ssh-port 2222 --https-port 8443 \
     --readonly-media scripts/truenas/boot-pool-patch/artifacts/TrueNAS-28.0.0-MASTER-20261014-230204.iso \
     --resume
   ```

   If the old guest does not pass read-only inspection, create a fresh guest
   and repeat the v27 patch/UI setup using `docs/CONSUMERS.md` and the golden
   ISO instructions. Fresh evidence takes priority over keeping the old
   overlay. Never use raw QEMU/Docker setup to bypass the managed runner.
4. Run registered `live-inspect` before mutation. Confirm exact v27 version,
   `boot-pool` health, root mount, dataset names, container/VM ids and states,
   VM zvol path, and direct filesystem/zvol marker hashes against the manifest.
   Use the current host key and guest known-hosts file:

   ```bash
   export TRUENAS_STORAGE_PROFILE=path-a
   export TRUENAS_EXPECTED_VERSION=27.0.0-RC.1
   export TRUENAS_TEST_PORT=2222
   export TRUENAS_TEST_KEY="$PWD/scripts/truenas/boot-pool-patch/artifacts/.live-test-root-key"
   export TRUENAS_TEST_KNOWN_HOSTS="$PWD/scripts/truenas/boot-pool-patch/artifacts/v27-golden-ui-migration-20261019/vm-state/known_hosts"
   python3 scripts/truenas/boot-pool-patch/run-live-gate.py live-inspect \
     --vm-container truenas-v27-golden-ui-migration-20261019 --allow-dirty
   ```

   Ensure container and VM are stopped before update. If an id, mount, or hash
   differs, stop and resolve it before starting `update.manual`.
5. Run registered `live-update` with all three guards enabled and the manifest
   path set. It must verify the ISO and update metadata, installed version,
   read-only media, free space, stopped workloads, and pre-update records/data.
   Set `TRUENAS_UPDATE_EXPECTED_VERSION=28.0.0-MASTER+20261014-230204`,
   `TRUENAS_UPDATE_ISO` to the pinned ISO path above,
   `TRUENAS_UPDATE_METADATA` to
   `scripts/truenas/boot-pool-patch/v28-nightly-20261014/update-artifact.json`,
   and `TRUENAS_UI_WORKLOAD_MANIFEST` to the existing manifest. Set
   `TRUENAS_TEST_ALLOW_MUTATIONS=1`, `TRUENAS_TEST_ALLOW_UPDATE=1`, and
   `TRUENAS_TEST_ALLOW_REBOOT=1`. Then run:

   ```bash
   python3 scripts/truenas/boot-pool-patch/run-live-gate.py live-update \
     --vm-container truenas-v27-golden-ui-migration-20261019 --allow-dirty
   ```

   Preserve its post-reboot `post-update-auto-mount` observation. The current
   lane manually mounts missing datasets later in its flow; a final PASS after
   that repair does not prove automatic remount. If necessary, refine the
   acceptance flow so the v28 UI workload cycle runs against the natural
   post-update state before any manual mount. Keep the automatic result and
   repaired result separate in logs and docs.
6. Before applying the v28 patch, attempt the v28 `live-ui-workloads` cycle on
   the same records and manifest. Record whether the container can start/stop,
   whether the VM can start/stop, whether the filesystem is naturally mounted,
   and whether the filesystem/zvol hashes match. If the normal update gate had
   to repair mounts before returning, do not report the later UI cycle as
   proof that the workloads worked automatically after update.
7. Apply the exact v28 adapter with registered `live-v28-patch` in install
   mode (use guarded replacement only if an exact prior v28 bundle is already
   recorded on that guest). Then run registered `live-boot` with reboot guard
   to test automatic mount after the patched reboot. Run read-only inventory
   and the UI workload cycle again against the same manifest. Confirm record
   ids/names, VM zvol attachment, root and child mount source, pool health,
   data hashes, and UI Start/Stop outcomes.
8. If an operation or migration check fails, preserve its evidence and fix
   only within the named TrueNAS scope. Keep the README, design guide, consumer
   guide, TODO, and handoff in sync with any user-facing behavior or evidence
   change. Do not claim an unproven behavior; record a missing oracle as open
   work and stop that dependent acceptance.
9. Do not claim the whole Path A UI surface is complete while operations are
   missing. In particular, test whether the UI can attach the user filesystem
   as a persistent container volume and write/read data there. The current
   container test checks the managed container dataset and its UI lifecycle;
   it does not prove an application can access the separate probe filesystem.
   The current VM has no guest OS. Track guest boot and auxiliary Cache/Log/
   Special role acceptance separately. Path B UI still cannot see the reserved
   `/dev/vda4` partition or create its pool. For downstream Path B UI checks,
   the registered unit lane has passed; use a fresh disposable managed v27
   guest: pass registered `live-inspect`, run
   `live-path-b-provision`, set `TRUENAS_PATH_B_SETUP=preprovisioned`, and run
   registered `live-ui`. This route does not prove UI pool creation. Keep the
   old `ui-wizard` refusal as separate evidence.
10. Commit only the TrueNAS task's explicit paths from this worktree. Leave all
    Netcup paths unstaged.

## Registered gates and task oracles

Use the registered `live-*` lanes through
`scripts/truenas/boot-pool-patch/run-live-gate.py` for networked live
acceptance. Run the installed `run-gate unit-boot-pool-patch --allow-dirty`
from `scripts/truenas`; it uses the declared `tester-unified` environment and
allows the current uncommitted TrueNAS changes.
Never run tests directly in the devcontainer cockpit. The live wrapper
delegates its verdict to installed Run-Gate, and tester source mounts are
read-only by design.

- **Update oracle:** `system.info` reports the pinned v28 target; `boot-pool` is
  ONLINE; the same middleware container/VM records and VM zvol path remain;
  manifest filesystem/zvol hashes and the update proof hash match. A missing
  record, hash mismatch, unhealthy pool, or mismatched version fails.
- **Natural post-update oracle:** before any repair, record mount state and
  directly verify all available hashes. Run UI Start/Stop before reapplying
  v28. A manual `zfs mount` before this observation invalidates the automatic
  accessibility claim.
- **Post-patch reboot oracle:** the registered v28 patch and `live-boot` pass;
  after reboot the same datasets mount from their expected ZFS sources and
  marker hashes match; the same container and VM records can be started and
  stopped through the UI; the same zvol stays attached and its probe hash
  matches.
- **Virtualization oracle:** unit tests continue to prove KVM is selected when
  available and TCG is selected only when KVM is unavailable and the
  architecture-specific QEMU binary exists. State clearly that live KVM was
  not exercised here.

Every oracle must be backed by a registered gate and a saved artifact. Do not
infer success from a guest version string alone or from a test that repairs a
mount before checking the natural post-reboot state.

## PWMCP and tester notes

Do not run `pip install -e` against the read-only PWMCP source mount. Editable
install invokes the build backend to prepare local metadata and may write
`.egg-info`/build files into the source tree. The live UI wrapper needs imports,
so its `PYTHONPATH` points to `pwmcp/client/src`. Keep generated artifacts under
`/tmp` in the tester and export them through the wrapper. The read-only tester
worktree is intentional source protection; writing the preservation manifest
inside it was the harness bug and has been fixed by exporting the manifest to
the host artifact directory.

PWMCP did not show a browser-control defect in this work. A useful upstream
contribution would be a documented read-only source-import workflow and/or a
supported external build-metadata/artifact directory for editable installs.
This is optional and must not delay the TrueNAS migration acceptance.

## Deferred research: display our patch level

Investigate whether the TrueNAS UI can show our patch level alongside the
release version without changing the canonical version consumed by update
checks, installers, compatibility checks, and `system.info`. Inspect those
callers before recommending an implementation. Prefer evaluating a distinct
patch-level property or UI badge first. Do not modify the version string as
part of the v27-to-v28 acceptance run.

## Scope and BLOCKED rule

Keep changes under `scripts/truenas/` and the named patch files. Do not touch
`scripts/netcup/`, publish or install a TrueNAS image outside the disposable
VM, or change the version identifier during migration testing. If a named
acceptance cannot be met because the UI/middleware contract is unsupported or
an external artifact is unavailable, record `BLOCKED: <specific condition>` in
`TODO.md`, preserve the failed evidence, commit only the TrueNAS paths, and
stop that dependent work. Record product-policy choices as a numbered
`D-<NNN>` decision and continue independent checks.
