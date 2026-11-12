# scripts/truenas - known issues, TODO and backlog

Created 2026-11-04 when the operator's `truenas-restore-iso` work was integrated.
IDs are `TN-NN`, allocated sequentially in this file. Entries are forward-looking:
the evidence history stays in `boot-pool-patch/TODO.md`, `golden-iso/TODO.md`,
`TODO.md` and `docs/ACCEPTANCE-RESULTS.md`; each entry below names where the
detail lives. A FIXED entry means code, tests and docs landed together.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| TN-01 | `vanilla-iso-patcher create` needs root and leaves root-owned work trees; run it as the invoking user | Major | OPEN |
| TN-02 | Fold the generic API client and VM lifecycle CLIs (`truenas-api.py`, `install-vm.py`, `truenas_api_client.py`) into `truenas/tnt` (TNT-60 device verbs, TNT-61 disk-image import), then delete them here | Major | OPEN |
| TN-03 | Golden-image credentials are dynamic (random admin password and API key per capture, 0600 sidecar) and the baked-in secrets plus the post-restore rotation checklist are documented; each capture has an 8-hex ID in the image and sidecar names | Major | FIXED |
| TN-14 | Optional: first-boot rotation helper script that implements the post-restore checklist (golden-iso/README.md); restore itself stays unattended and unrotated by design | Minor (low priority) | OPEN |
| TN-04 | Exercise golden source/restore on a stable TrueNAS 27 release and run a live smoke test of the restore runtime on TrueNAS 28 | Minor | OPEN |
| TN-05 | Later v28 nightly: new guarded adapter plus the full API/UI matrix; never widen the exact-build fingerprint set | Minor | OPEN |
| TN-06 | Clean-target UI acceptance for the v27 `20261029-173417` bridge build | Minor | OPEN |
| TN-07 | Update/reapply retention of existing Path A container and VM records and UI Start/Stop; v28 natural update/reinstall with patched data present | Minor | OPEN |
| TN-08 | Open acceptance boundaries: guest OS boot and real KVM selection, NFS/SMB client access, real second-disk cache target and import order, UI cache controls, real SLOG/special behaviour, Path B auxiliary roles | Minor | OPEN |
| TN-09 | Legacy TrueNAS 24.10 API adapter (WebSocket/auth/job) for the shared client; moot once TN-02 lands on tnt | Minor | OPEN |
| TN-10 | Committed docs link to ignored evidence logs under `boot-pool-patch/artifacts/` and name host-specific absolute paths (`/home/vscode/...`); move durable evidence into `docs/acceptance-results/` and make references host-agnostic | Minor | OPEN |
| TN-11 | `vanilla-iso-patcher` upstream builds re-download the ISO into a per-run work directory; reuse the checksum-verified copy in the repo `downloads/` folder | Minor | FIXED (19f71f7ff) |
| TN-12 | Registered gate lanes: confirm `unit` budget and the CoW/fsync-off lane switch (run-gate RG-149) once main's gate policy applies; keep live lanes manual | Minor | OPEN |
| TN-13 | Storage layout migration (vm-lab, downloads, cache modes, requirements naming) | Minor | FIXED (finish-truenas-iso-20261104) |
| TN-15 | `unit` and `unit-boot-pool-patch` lanes are red on main: the live-UI Playwright pin (1.64.0) no longer matches the pwmcp contract (1.64.1) | Major | OPEN |
| TN-16 | `unit` and `unit-boot-pool-patch` lanes are red on main: the registered v27 RC.1 install bundle digest differs from the digest recomputed from the archive | Major | OPEN |

## Entries

### TN-01 vanilla-iso-patcher must run as root

Cause: unsquashfs/mksquashfs need root to keep ownership, device nodes and xattrs
(`patch_iso.py`, `cli.py`). A killed run leaves a root-owned `.vanilla-iso-patcher-*`
work directory next to the output (230-280 MB), which breaks `git worktree remove`
and `ciu worktree rm`. Options and acceptance criteria: `TODO.md`, section
"vanilla-iso-patcher must run as root". Interim mitigation: a `--work-dir` outside
the worktree and a root cleanup helper. Closes when a full `create` runs as the
invoking user, leaves nothing root-owned, and `unsquashfs -ll` of the result equals
the root-built image except for the intended changes.

### TN-02 fold the shared API and VM CLIs into tnt

`truenas/tnt` targets vanilla v27 and detects patch-only features at runtime. The
branch's `install-vm.py` (disk-image import, VM create/start/stop/delete) maps to
TNT-60/TNT-61; `truenas-api.py` and the client to tnt's API layer. Port the tests with
each verb, keep the `rebind`/`--yes` safety behaviour, then remove the scripts and their
docs here. `tcg_vm.py` (local QEMU guests) and `accept-update-survival.py` stay: they
drive guests and patch acceptance, not the TrueNAS API. Neither `truenas-api.py` nor
`install-vm.py` has a reviewed `cli-extended` surface (no `cli-extended.toml`), which is
the reason not to invest in them further; `cli-extended surface check` passes for
`vanilla-iso-patcher` and for `scripts/netcup` `install-restore-iso`.

### TN-03 credential rotation (fixed)

Rule and detail: `golden-iso/TODO.md`; code in `golden-iso/credentials.py`, step 1
and step 2; tests `golden-iso/tests/test_credentials.py`. Step 1 replaces the sidecar
password and any earlier capture's API key at every capture. The restore is unattended
and keeps the baked-in secrets by design; the user rotates them afterwards with the
checklist in `golden-iso/README.md` ("Secrets baked into a golden ISO / what to change
after a restore"), which the Netcup restore controller points to.

### TN-14 first-boot rotation helper (optional)

A script that runs the post-restore checklist (admin password, API key, root
authorized key, SSH host keys, machine identity) on a restored host. Low priority; the
documented manual checklist is the supported path.

### TN-04..TN-09

Detail lives in `boot-pool-patch/TODO.md` ("Current checkpoint" list), `golden-iso/TODO.md`,
and `TODO.md` ("Shared API compatibility"). Live items need a TrueNAS test host or the VM
harness; they are not runnable in the shared devcontainer (no KVM, swap and loop-device
tests are forbidden on the shared host).

### TN-13 storage layout (fixed)

VM state defaults to `artifacts/vm-lab/<vm>/` (`tcg_vm.py`, golden step 1), inputs go to the
repository `downloads/`, disposable overlays use `cache=unsafe`, golden source disks
`cache=writeback`, `requirements-api.txt`. Tests: `tests/test_tcg_vm.py`,
`golden-iso/tests/test_storage_layout.py`.

### TN-15 live-UI Playwright pin behind the pwmcp contract

Found 2026-11-12 at main `5358386c1`. `test_repository_live_ui_playwright_pin_matches_pwmcp_contract`
fails in both unit lanes: `requirements-live-ui.txt` pins Playwright 1.64.0, while
`pwmcp/pwmcp.contract.json` says 1.64.1 since the pwmcp 1.64.1 release coordinates. Fix: align
the pin to the contract. Better: derive the expected version from the contract in one place, so
a pwmcp release cannot leave the pin behind unnoticed.

### TN-16 v27 RC.1 bundle digest differs from the registry

Found 2026-11-12 at main `5358386c1`. `test_registered_install_bundle_digests_are_recomputed_from_archives`
and `test_v27_bundle_is_deterministic_and_contains_only_reviewed_members` fail in both unit lanes.
The digest recomputed from a `git archive` export of that commit is
`0e670257f4fcba1ef1bc135695413b298df4ae743fbee69a15fbf706710b03b2`, which is not the registered
value. First find out which side is stale: a bundle member changed without re-registration, or
the registered value was never updated. Do not re-register before that is known; the registry is
the closed compatibility boundary for the boot-pool patch.
