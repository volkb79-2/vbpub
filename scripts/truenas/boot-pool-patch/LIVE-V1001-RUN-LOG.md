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
