# Live test-host run log (Netcup v1001)

Running log of the 2026-10-27 session that validates the boot-pool relaxation
patch on a hosted Netcup test VM instead of local QEMU. No secrets here.
The host is the only mutable target (allowlist: server id 804027 /
`v2202511209318406253`; verified against the SCP API before every mutation).

## Phase 0 - orientation and baseline

- SCP API: `servers` lists the account's three servers; 804027 is the test host
  (name matches the memory-note id). It was RUNNING with an unexplained v27
  RC.1 state (14/15 adapter hashes differed, no install record).
- Provider snapshots found: `truenas-v27-rc1-base-20261021`,
  `truenas-v27-path-a-ssh-ready-20261021` (both of that unexplained state; the
  server record says `snapshotCount: 1`, the list endpoint shows 2).
- No golden raw disk or restore ISO exists (only the protected sidecar), so the
  golden route would need a full source VM (local VM work is out of scope).
- Chosen route: rebuild a stock `unattended` 16 GiB `poweroff` v27.0.0-RC.1
  installer ISO from the checksum-pinned vanilla ISO and install it with
  `install-restore-iso.py install`. A stock install is the right pristine for a
  patch test (the relaxed flavor would pre-apply the adapter). The patcher
  needs root; it ran in a throwaway `python:3.14-trixie` container
  (`docker run -u 0`, output chowned back, PYTHONPATH to `cli-extended`
  because the editable pip install fails in that image).
  - ISO SHA-256 `12bf424d...ca08`, delta 39,485,421 bytes.

## Phase 1 - install, access, snapshot, patch (2026-10-27)

- `install-restore-iso.py install 804027` finished (log `artifacts/v1001-install-20261027.log`);
  ISO attach was slow (~10 min), installer powered off, disk boot restored.
- First action: `truenas_admin` password rotated via JSON-RPC to a random value in a
  mode-0600 gitignored TOML. live-ui-auth PASS, live-ui-prepare-ssh PASS, SSH pinned.
- `/dev/kvm` absent (provider `nestedGuest: no`); the lane's refusal rule stands.
- Deleted the two stale snapshots; created ONE: `tn-v27rc1-stock-ssh-ready-20261027`
  (`aaffa906-45b0-4715-af99-db55a35093a3`).
- live-inspect (stock) PASS; live-v27-patch PASS (patch record present afterwards).
- Guest uptime was ~166 s shortly after the patch lane: unexplained reboot, to investigate.
- Session stopped by the coordinator here; see `LIVE-V1001-HANDOFF.md`.

## Phase 2 - Path A UI, Path B, UI blockers (2026-10-27, resumed)

- Post-patch "reboot" explained: the guest booted once at snapshot time (clean shutdown in the
  previous boot journal, PDT clock), the patch did not reboot it. Only failed unit afterwards:
  `serial-getty@ttyS0` (no serial console on this VM type; harmless).
- live-ui Path A on stock+patch (log `live-ui-v1001-patched-20261027.log`): the UI created the
  `boot-pool/user-data` root, a child filesystem, zvols, a container and a VM wizard entry;
  the earlier `NameError: mountpoint` did NOT recur. FAIL (exit 1) at the shares step:
  /ui/sharing renders "No Shares / Create Pool". Cause (browser RPC trace): the Shares page
  gates on `pool.query`, which excludes boot-pool, so it returns [] even with an NFS share
  present (created via API to prove it). Path A therefore cannot add SMB/NFS shares in the UI.
  Earlier local-guest passes of this step are unexplained (those guests may have had another pool).
- Path B (`path-b` profile, fresh revert + patch): `live-path-b-partition` PASS reserved
  `/dev/vda4` (495.5 GiB tail); `disk.query` lists only `vda`, `disk.get_unused` is empty, so
  the UI pool wizard cannot offer the partition (pool.create works on whole disks only).
  `live-path-b-provision` PASS (boot-disk-data ONLINE on vda4).
- First `live-ui` (preprovisioned) failed a precondition: the middleware moves the system
  dataset to boot-disk-data (`boot-disk-data/.system/*`); fixed the lane to ignore that tree
  (+ source test). Rerun `live-ui-v1001-pathb-3-20261027.log`: PASS exit 0 including the
  Shares page (a real pool exists, so the gate passes).
- Remaining: live-ui-workloads (not allowed in external mode by the wrapper), Path A shares
  decision, v28 (ISO not fetched). Host left in patched Path B state; snapshot unchanged.

## Phase 3 - ui-pools patch, action matrix, Path B API spike (2026-10-28)

- `v27/ui-pools.patch` (pool.py): externally originated `pool.query` (UI/midclt) also returns
  one read-only synthetic `boot-pool` row (id 0) while `/mnt/boot-pool/user-data` is mounted.
  live-ui Path A on stock+patch now PASSES incl. the Shares step
  (`live-ui-v1001-patha-2-20261028.log`). `pool.export 0` / `pool.update 0` fail with
  ENOENT "Pool 0 does not exist" (jobs 32/33; boot-pool stayed ONLINE).
- New lane `live-api-probe` (external-host allowed) recorded on the partition-only Path B state
  (`live-api-probe-v1001-20261028.log`): `disk.query` lists only `vda`; `disk.details UNUSED`
  is empty; `pool.create` with `vda4` -> EINVAL "disks not found in system"; `boot.attach vda4`
  -> internal TypeError (no disk size); a hand-made pool on the partition IS listed by
  `pool.import_find`, imported ONLINE by `pool.import_pool`, and shows in `pool.query`.
- `ACTION-MATRIX.md` is the single canonical action table; `tests/test_action_matrix.py`
  fails on doc/code drift (patch inventory, lanes, steps, API methods both ways).
- Unit lane: 203 tests pass.

## Phase 4 - partition pool shim, review fixes (2026-10-28)

- `ui-pools.patch` now also patches `disk_/availability.py` (reserved partition offered as an
  unused pseudo-disk by `disk.details`) and `pool_/pool.py` (`pool.create` on that partition runs
  `zpool create` and adopts the pool); helper `shared/boot_disk_partition.py`.
- `live-api-probe` now asserts (checks keyed by name): path-b `details-lists-reserved-partition`,
  `create-mixed-refused`, `create-mirror-refused`, `boot-attach-refused-unchanged`,
  `shim-create-on-partition`, `shim-pool-registered`, `import-hand-made-pool`, `cleanup-complete`,
  `boot-pool-unchanged`; path-a `synthetic-row-present`, `refused-pool.*` (id 0), `boot-pool-unchanged`.
  Finding: `pool.scrub.run` takes a name and starts a (non-destructive) scrub on boot-pool in stock
  v27 as well, so it is documented, not refused.
- `live-ui` ui-wizard on Path B: the wizard offers the partition (Unassigned Disks 495.5 GiB x 1);
  the lane uses the automated Data step and the stock erase confirmation (Confirm checkbox,
  Continue). PASS with the pool created through the UI (`live-ui-v1001-wizard4-20261028.log`).
- Logs of these runs (`live-ui-*`, `live-api-probe-*`) stay in the ignored `artifacts/` directory and
  are intentionally not committed.
- Review fixes: synthetic row `info` override order, `zpool get` timeout and 5 s cache,
  narrowed `.system` subtree match, matrix test ties PASS cells to an asserting lane step and
  matches real call sites for every lane; unit tests for the patch (hunk counts, bundle, apply
  script, anchoring drift); source evidence for "no internal path passes app" recorded in
  `ACTION-MATRIX.md`.
