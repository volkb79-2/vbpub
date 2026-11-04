# Live test host (Netcup v1001) handoff, 2026-10-27

## Goal
Validate/fix/iterate the boot-pool relaxation patch (Path A `boot-pool/user-data`,
Path B `boot-disk-data` on a reserved partition) for TrueNAS v27 and v28,
through the middleware API and the web UI, on the hosted Netcup test VM instead
of local QEMU. Find which UI actions remain blocked and why; fix/extend the
patch where feasible. Final: automated lanes pass end to end on the host.
Allowlist: ONLY server id 804027 (`v2202511209318406253`, `v1001.vxxu.de`).
Never nano1 (710696) or r1002 (799611). No local VMs.

## Current state of the host (safe, no provider task running)
- Fresh STOCK TrueNAS 27.0.0-RC.1 installed this session from a rebuilt
  `unattended` 16 GiB `poweroff` ISO (not relaxed; not golden: no golden raw
  disk/restore ISO exists, only the protected sidecar). ISO:
  `scripts/truenas/vanilla-iso-patcher/artifacts/v1001/TrueNAS-27.0.0-RC.1-unattended-16GiB-poweroff.iso`
  (sha256 12bf424d338cc1adf757a5d6ca378ecbbe8c1d6df820edacb3deaa336cd9ca08),
  built as root in `python:3.14-trixie` (`docker run -u 0`, PYTHONPATH to
  `libraries/cli-extended/src`, output chowned back; editable pip install fails there).
  Source ISO copied from `/home/vscode/console-corpus/iso/TrueNAS-27.0.0-RC.1.iso`
  (sha256 verified = compat sidecar 515a0252...). v28 nightly ISO NOT fetched yet.
- `truenas_admin` password rotated (random) and stored mode 0600 in gitignored
  `boot-pool-patch/artifacts/v1001-ui-secrets.toml`. Root Ed25519 key
  (`golden-iso/vm/root_ed25519`) installed via UI lane; SSH enabled+autostart;
  host key pinned (TOFU, throwaway host) in `artifacts/v1001.known_hosts`.
- The v27 relaxation patch IS APPLIED (`/root/v27-boot-pool-patch-install.json`
  exists). `boot-pool/user-data` does not exist yet (Path A root not created);
  no Path B partition/pool yet.
- Host has NO `/dev/kvm` (provider `nestedGuest: no`, 0 vmx flags), so the lane's
  `/dev/kvm absent` refusal rule is correct for this host (TCG is required).
- Uptime was only ~166 s when checked right after the patch lane, i.e. the guest
  rebooted once unexplained (not caused by a lane step I know of); investigate
  (journal) before trusting boot-time behaviour.
- Provider snapshots: exactly ONE, pristine = stock v27 + SSH-ready + rotated pw:
  name `tn-v27rc1-stock-ssh-ready-20261027`, uuid
  `aaffa906-45b0-4715-af99-db55a35093a3` (taken before the patch).
  The two old snapshots (`truenas-v27-rc1-base-20261021`,
  `truenas-v27-path-a-ssh-ready-20261021`, of an unexplained pre-existing v27)
  were deleted this session. Never exceed 2.

## Lanes run (env: see `LIVE-V1001-RUN-LOG.md`; all exit 0)
| lane | result | artifact (under boot-pool-patch/artifacts/) |
|---|---|---|
| install (netcup) | OK | v1001-install-20261027.log, v1001-install-state.json |
| live-ui-auth | PASS 0 | live-ui-auth-v1001-20261027.log |
| live-ui-prepare-ssh | PASS 0 | live-ui-prepare-ssh-v1001-20261027.log |
| live-inspect (stock) | PASS 0 | live-inspect-v1001-stock-20261027.log |
| live-v27-patch | PASS 0 | live-v27-patch-v1001-stock-20261027.log (bundle v27-fresh-patch-bundle-path-a-d7385ccb87.tar.gz) |
Not yet run on this host: live-ui (Path A), live-ui-workloads, live-path-b-provision,
live-path-b-partition, Path B UI, anything v28, KVM-present check.
Earlier (2026-10-21) findings still open: the `NameError: mountpoint` in
`pool_/dataset.py:117` was seen on the OLD unexplained host state; recheck on the
freshly patched stock host. Path B: middleware/UI do not list the reserved
partition (`disk.query` omits vda4; wizard offers no disks) per HANDOFF.md; the
`preprovisioned` UI mode exists but has no fresh live acceptance.

## Commands to continue
```bash
source /tmp/.../scratchpad/env.sh   # recreate: values below
cd scripts/truenas
export TRUENAS_TEST_HOST=v1001.vxxu.de TRUENAS_TEST_PORT=22 \
  TRUENAS_TEST_KEY=$PWD/golden-iso/vm/root_ed25519 \
  TRUENAS_TEST_KNOWN_HOSTS=$PWD/boot-pool-patch/artifacts/v1001.known_hosts \
  TRUENAS_STORAGE_PROFILE=path-a TRUENAS_EXPECTED_VERSION=27.0.0-RC.1 \
  TRUENAS_TEST_ALLOW_MUTATIONS=1 TRUENAS_UI_URL=https://v1001.vxxu.de \
  TRUENAS_UI_SECRET_FILE=$PWD/boot-pool-patch/artifacts/v1001-ui-secrets.toml \
  TRUENAS_ROOT_SSH_PUBLIC_KEY_FILE=$PWD/golden-iso/vm/root_ed25519.pub \
  PWMCP_WS_URL=ws://pwmcp:3000/ PWMCP_PLAYWRIGHT_VERSION=1.64.0
/home/vscode/.venv/bin/python boot-pool-patch/run-live-gate.py live-ui \
  --docker-network dstdns-hox0ju-network --allow-high-psi --allow-dirty
```
Next order: live-ui (Path A; set `TRUENAS_TEST_ALLOW_PATH_A_ROOT_PROVISION=1` only
if the lane asks) -> live-ui-workloads (needs `TRUENAS_TEST_PRESERVE_UI_WORKLOADS`,
`TRUENAS_UI_WORKLOAD_MANIFEST`) -> rollback -> Path B (`TRUENAS_STORAGE_PROFILE=path-b`,
`TRUENAS_TEST_ALLOW_DISK_PROVISION=1`, `TRUENAS_DISK_DEVICE=/dev/vda`,
`TRUENAS_BOOT_POOL_PARTITION=/dev/vda3`, `TRUENAS_PATH_B_SETUP`) -> v28 (build/fetch the
28.0.0-MASTER+20261014-230204 ISO; reinstall via `install-restore-iso.py install`;
`live-v28-patch` is outside external mode per docs, extend wrapper if needed).
Docker network `dstdns-hox0ju-network` contains pwmcp + this devcontainer.

## Rollback to pristine (stock v27, SSH-ready, unpatched)
```bash
cd scripts/netcup
python3 scp-api.py snapshots 804027 revert tn-v27rc1-stock-ssh-ready-20261027 --dry-run
python3 scp-api.py snapshots 804027 revert tn-v27rc1-stock-ssh-ready-20261027 --yes
# then follow with: python3 monitor-task.py watch <task-uuid>; power on if SHUTOFF
```
Full reinstall: `install-restore-iso.py install 804027 --iso <ISO> --state-file <file> --yes`.

## Risks / notes
- Snapshot stops the host briefly; restore leaves it possibly SHUTOFF (power on via `scp-api.py power`).
- The ISO install wipes the whole disk without asking; admin pw in the ISO is public
  `truenas` until rotated (rotation done post-install via JSON-RPC `user.update`
  with a scratch client; consider adding a registered lane/CLI for this).
- Host is on a public IP; TLS verify off; SSH key-only.
- `scripts/truenas/TODO.md` has an unrelated uncommitted operator edit; not committed.
- No independent review was done yet for any code change (none made so far; only
  docs/logs). Reviews (fresh Sonnet, read-only, max 3 rounds) apply to future fixes.
