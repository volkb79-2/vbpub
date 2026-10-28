# tnt changes

## [Unreleased]

- M0 scaffold: `tnt --version`, `tnt doctor`, `tnt profile list|show`, `tnt disks`, `tnt tui` (Storage > Disks).
- Transport over `truenas_api_client` (pinned by sha of tag TS-27.0.0-RC.1): wss only, TLS verification on by
  default, API key from a mode-0600 file or the environment, never printed.
- Per-version adapters v27 and v28 (v28 is a stub) with a method manifest.
- Default-deny `GuardedRpc`: write sessions run only the exact calls of the plan being applied; refusal list with live pool/disk state; disk.wipe protection computed from live membership.
- Re-review hardening: lazy membership with real topologies (disk key, partuuid, nvme, nested/aux vdevs), thread/task-bound grant and call lock, kind tied to destructive methods, normalised paths and strict pool-id forms, default-deny boot family, single-use session-bound plans.
- Guard review round 3: normalise boot-pool paths before prefix removal; refuse unmanifested writes and require destructive kind for additional data-changing methods; resolve boot-pool ids in nested references; block disk wipes when pool topology is unknown or disks report `exported_zpool`; union vdev references; register issued plan nonces against their fingerprints; document concurrent-read locking and the same-process trust boundary.
- Free space is an upper bound (`free_upper_bound_bytes`, `<= x` in the CLI/TUI) and unknown whenever a size or the partition data is missing.
- Plan/apply action model with confirm gates and a refusal list.
- Fake JSON-RPC websocket server (`tnt.testing`) used by the tests.
