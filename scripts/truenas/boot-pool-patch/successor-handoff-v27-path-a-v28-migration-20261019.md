# Successor prompt: finish v27 Path A to v28 workload acceptance

Continue the TrueNAS boot-pool patch work in this worktree. The immediate task
is to complete the v27-to-v28 migration proof for Path A workloads created
through the TrueNAS UI, then continue closing the UI-operation gaps listed
below. The user has authorized fresh VM setup, removal of obsolete VM overlays,
fixes along the way, and commits in this worktree.

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
| `artifacts/unit-boot-pool-patch-v27-progress-dialog-20261019.log` | Registered unit lane passed: 156 tests and 45 subtests. | Live KVM selection; live v28 update. |

The manifest is
`scripts/truenas/boot-pool-patch/artifacts/v27-golden-ui-migration-20261019/ui-workloads.json`
(SHA-256 `4c0d8958516bb4b1b2a9b198fb05e7dacd1d529da982f6c0525c059c2b225ac8`).
It identifies the UI-created dataset, container id/name, VM id/name, VM zvol,
and filesystem/zvol probe hashes. Use this same manifest for this migration
unless a fresh VM must be built; if rebuilt, use its newly emitted manifest and
re-record its hash.

The latest v27 guest is stopped. Its managed container is
`truenas-v27-golden-ui-migration-20261019`, and its state is at
`scripts/truenas/boot-pool-patch/artifacts/v27-golden-ui-migration-20261019/vm-state`.
The overlay remains on disk, and `runner.json` records the attached read-only
v28 ISO with SHA-256
`9312f77d0a94f54dc0396050bd26bb31ea0e81d62b124e30a94075a006e7bc9a`.
No managed TrueNAS guest container was running when this handoff was written.
The resumed guest has not yet passed a new read-only inventory; do that before
any further mutation. If it is not reliably reachable, create a fresh guest
from the golden image and regenerate the UI workload manifest. Fresh evidence
takes priority over keeping this overlay.

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
8. If an operation or migration check fails, fix the patch or acceptance
   harness along the way. Add behavioral regression coverage and update
   `README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, `TODO.md`, and
   `HANDOFF.md` when the user-facing behavior or adopter workflow changes.
   Re-run the registered unit lane and relevant live gates after code changes.
9. Do not claim the whole Path A UI surface is complete while operations are
   missing. In particular, test whether the UI can attach the user filesystem
   as a persistent container volume and write/read data there. The current
   container test checks the managed container dataset and its UI lifecycle;
   it does not prove an application can access the separate probe filesystem.
   The current VM has no guest OS. Track guest boot and auxiliary Cache/Log/
   Special role acceptance separately. Path B UI still cannot see the reserved
   `/dev/vda4` partition or create its pool.
10. Commit only the TrueNAS task's explicit paths from this worktree. Leave all
    Netcup paths unstaged.

## Registered gates and task oracles

Use `scripts/truenas/boot-pool-patch/run-live-gate.py` for networked live
acceptance and the repository's `./run-gate.py unit-boot-pool-patch` for code
changes. The live wrapper launches the bounded `tester-unified` runner. Source
mounts inside that tester are read-only by design.

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
