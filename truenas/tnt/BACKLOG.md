# tnt backlog (TNT-NN)

Seeded 2026-10-28 from `docs/ACTION-MATRIX.snapshot.md` (rows whose verdict is `API-TUI` or `pending decision`; a
test keeps every such row id mentioned here) and from the M0 scaffold's known gaps. Milestones are defined in
`docs/DESIGN.md` section 10. M1, M2, It1, It2, and It3 are complete; It4 remains active, with vanilla-v27 live
acceptance recorded on 2026-10-29. `Later` means no iteration is assigned. Sizes: S < 1 day, M 1-3 days, L > 3 days
of focused work. `[DONE M1]`, `[DONE M2]`, and `[DONE It4]` mark implemented or accepted work.

## Matrix rows: the TUI is the product path (API-TUI first, as the operator asked)

| id | matrix | what | size | milestone | notes |
|---|---|---|---|---|---|
| TNT-01 | B-01 (input) | SPIKE: partition data source. What do `disk.details` (`join_partitions`?) and `pool.get_disks` return for a disk with partitions and a free tail; is SSH `sgdisk -p` / `lsblk --json` needed? Verify the `disk.query extra.pools` and `disk.details` params used by the M0 adapter on the throwaway host | S | Later | Gate for TNT-02/03/04. Until closed the manifest says `verified = "reference"` and the `disks` command shows `?` for a disk without partition data |
| TNT-02 | disk free space | Storage > Free space and partitions: per-disk extents (start/size/gap), alignment, backup GPT; upgrade `Disk.free_bytes` from an upper bound to exact extents | M | Later | Depends on TNT-01 |
| TNT-03 | B-01 | Reserve the free-tail partition (plan/apply). The API has no partitioning method, so this is a separate SSH-based storage operation, not It3's interactive SSH hand-off | M | Later | Design decided (DESIGN section 8.1): fixed command list, `StrictHostKeyChecking=yes`, key file, no password; not implemented |
| TNT-04 | B-06 | Import Pool screen: `pool.import_find` then `pool.import_pool` (adopt a hand-made pool on a partition). Measured PASS via API on v27 | S | Later | First write action; builds the plan/apply UI around `tnt.actions` |
| TNT-05 | B-02, B-03 | Create a pool on a partition or on free space. Stock `pool.create` takes disk names only (measured: EINVAL). Path: partition + `zpool create` + TNT-04 adoption. Decided 2026-10-28: (a), `zpool create` on the partition then TNT-04 adoption (matches the TrueNAS agent's shim); depends on TNT-03 and TNT-04 | L | Later | Matrix verdict is "pending decision" |
| TNT-06 | B-01, B-06 | Boot pool data management (`boot-pool-data` / `boot-disk-data` layout): create, inspect, export-safe, reboot persistence | L | Later | Depends on TNT-03/04/05 and the live reboot test |
| TNT-07 | A-09 | Cache/log/special vdev management through `pool.update` for ordinary pools; boot-pool zvol targets stay refused by design | L | Later | Refusal rows B-07, A-08 are already on the refusal list in `tnt.actions` |
| TNT-08 | B-07, A-08 | Keep the refusal list in step with the matrix (a test already checks B-07's methods); extend when new `n/a` rows appear | S | all | Extended in M1 and hardened before It1; the exact v27 manifest and cross-family boot-pool/pool-reference guards are explicit and tested |

## Screens mirroring the web UI menu (read views first, writes behind plan/apply)

| id | screen | milestone | size |
|---|---|---|---|
| TNT-10 | [DONE It2] Dashboard (`system.info`, CPU/memory history, alerts, pool and disk health) | It2 | M |
| TNT-11 | Storage > Pools (`pool.query`, vdev tree, scrub status; scrub as the first MUTATE action) | Later | M |
| TNT-12 | [DONE M1] Datasets (`pool.dataset.query`, tree, usage; create/update/delete behind gates; matrix A-01..A-03) | M1 | L |
| TNT-13 | System > Boot (`boot.get_state`, `boot.environment.*`; clone/activate/keep) | Later | M |
| TNT-14 | [DONE M2] System > General configuration export, Network, Update, reboot, and shutdown | M2 | L |
| TNT-15 | [DONE M2] Shares: SMB, NFS, remote-path guard, ACL read, typed configuration deletion | M2 | M |
| TNT-16 | [DONE M1] Containers and Virtual Machines (matrix A-04..A-06, B-04; v27 `container.*` and `vm.*`). Current image, CPU-count and configured memory-limit fields are unavailable in the verified container schema; see TNT-36/37. | M1 | L |
| TNT-17 | [DONE M2] Apps: installed app state, upgrade-available flag, start/stop/restart, destructive uninstall with opt-in volume deletion | M2 | M |
| TNT-18 | [DONE M2] Data Protection periodic snapshot, replication, and cloud-sync tasks (scrub/SMART tasks deferred) | M2 | L |
| TNT-19 | Remaining Credentials and Enclosure; Reporting beyond It2's CPU/memory read history | Later | L |
| TNT-38 | [DONE M1] Snapshots: list/create/clone/hold/release/rollback/delete through CLI and TUI, with drift probes and typed destructive confirmation | M1 | M |
| TNT-39 | [DONE M1] Containers: list/show/create/update/start/stop/restart/delete through CLI and TUI, including explicit one-shot CPU/memory usage status from `container.metrics`; v27 omits image identity, CPU count, configured memory limit | M1 | L |
| TNT-40 | [DONE M1] Virtual machines: list/show/create/update/lifecycle/device/console workflows through CLI and TUI; create and device plans are staged because v27 excludes devices from `vm.create` | M1 | L |
| TNT-41 | [DONE M1] Local users: list/create/basic update/delete, separately guarded group/authentication access changes, SSH public-key management and password changes through CLI and TUI | M1 | L |
| TNT-42 | VM create recovery: compensate or guide recovery when a separately reviewed `vm.device.create` plan fails after `vm.create` | Later | M |
| TNT-43 | [DONE M2] System > Network: read-only interface/address/route/DNS/default-gateway view through verified v27 methods | M2 | S |
| TNT-45 | [DONE M2] Audit viewer: filtered, paginated v27 `audit.query` with secret scrubbing; general middleware/syslog tail deferred because v27 exposes no such method | M2 | M |
| TNT-46 | [DONE M2] API key list/create/rename/revoke with one-time key display, explicit mode-0600 file option, safe verifier projection and typed revoke | M2 | M |
| TNT-47 | [DONE M2] System update/reboot/shutdown destructive plans, complete preflight, update job progress, bounded TUI reconnect, and private config export; a lost power-action reply is never retried and is reported as uncertain unless reboot is observed | M2 | L |
| TNT-48 | [DONE It2] System > Services read/status; service lifecycle actions remain separate future work | It2 | M |

## Platform and quality

| id | what | size | milestone |
|---|---|---|---|
| TNT-20 | [DONE It4] Namespaced v27 live acceptance lane, requiring explicit profile/pool/group/image/VM-size inputs and cleaning up only `tnt-live-` resources | M | It4 |
| TNT-21 | [DONE M1] SCRAM fake-server handshake and v27 password login (both auth flows are exercised through the real client) | M | M1 |
| TNT-23 | Manifest drift test: compare each adapter manifest with the fetched `https://api.truenas.com/v27.0` method index (opt-in network test, not in the gate) | S | It4 |
| TNT-24 | Audit log (no secrets) of every executed action under the XDG state dir | S | Later |
| TNT-25 | [DONE It2] Non-blocking job engine: generation-bound handles, progress/log/error records, applied job IDs, partial results, and reconnect reconciliation | M | It2 |
| TNT-26 | Plan/apply CLI verbs (`tnt plan`, `tnt apply`) once the first action exists, with `--dry-run` / `--yes` from cli-extended | M | Later |
| TNT-27 | [DONE It1] TUI plan review and write-session control, including host-generation invalidation and the It1 host switcher | M | It1 |
| TNT-28 | [DONE It1] Paired multi-host and secrets file support, safe host-edit verbs, and session controller before the host picker | L | It1 |
| TNT-29 | cli-extended surface review (AC-17): the review catalog `docs/cli-review.toml` is empty; run `surface pack`, review, link `cli_case` tests, then `surface check` must pass | M | It4 |
| TNT-30 | Mutation lane (assay R2) for `tnt`, `assay.toml` + gate lane; coverage threshold (iobench holds 100%) | M | It4 |
| TNT-31 | Re-pin `truenas_api_client` from the TS-27.0.0-RC.1 sha to the TS-27.0.0 GA tag once it is 14 days old | S | GA+14d |
| TNT-32 | Register `tnt` in `cmru.orchestration.toml` and the root run-gate registry (see DESIGN section 12); this checkout currently declares only `r0-r1` and `live-v27`, not `live-inspect` | S | It4 |
| TNT-33 | [DONE It4] Gate lanes install the editable `tnt` project in the tester venv with an explicit test-only SCM version before cli-extended's installed-version lookup | S | It4 |
| TNT-35 | Detect per host which remaining conditional menu entries (Apps, KMIP, Enclosure) the web UI would show, and hide unsupported entries | S | Later |
| TNT-36 | Revisit container memory controls if a future v27 schema exposes a supported memory resource field; current `ContainerCreate`/`ContainerUpdate` schema exposes `cpuset` but no memory limit (deferred by verified v27 schema) | S | Later |
| TNT-37 | Expose a current container image identity if a future v27 API supplies it; `ContainerEntry` has no image field and create consumes image metadata without retaining it in the returned record (deferred by the verified schema; see `docs/API-METHODS-V27.md`) | S | Later |
| TNT-49 | [DONE It2] Read-only core views: Dashboard, Alerts, Jobs, Services, Storage health, exact v27 contracts, and unavailable-state behavior | L | It2 |
| TNT-50 | [DONE It3] Hardened interactive SSH hand-off with pinned host-key sources, suspend/restore, and no arbitrary remote command mode | M | It3 |
| TNT-51 | [IN PROGRESS It4] Vanilla-v27 acceptance, review hardening, project docs/CLI consistency, estate registration, and release readiness; live acceptance recorded 2026-10-29. The latest registered `r0-r1` run passed 930 tests, with 0 failures and 2 deselected; combined coverage was 98.36% and branch coverage was 97.27% (2492/2562), above the 97% branch floor. Consistency, registration, and release-readiness work remains | L | It4 |
| TNT-52 | [DONE It4] Close milestone-review findings: worker-bound lookups/preflight, endpoint-bound credentials, bounded transition drain, load-bearing boot-pool assertion, lockout-shaped confirmations, and whitespace cleanup | M | It4 |
| TNT-53 | [FIXED FOR TNT; GENERAL TOOL FINDING RG-110] Prevent pytest-cov's rounded `--cov-fail-under` comparison from accepting branch coverage below 97%. TNT sets `[tool.coverage.report] precision = 2` in `pyproject.toml`, aligning pytest-cov's exit comparison with its two-digit report. The general pytest-cov/Run-Gate command-lane false green remains tracked as RG-110 in `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`. | M | It4 |
