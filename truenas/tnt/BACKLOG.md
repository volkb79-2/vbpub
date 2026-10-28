# tnt backlog (TNT-NN)

Seeded 2026-10-28 from `docs/ACTION-MATRIX.snapshot.md` (rows whose verdict is `API-TUI` or `pending decision`; a
test keeps every such row id mentioned here) and from the M0 scaffold's known gaps. Milestones are defined in
`docs/DESIGN.md` section 10. Sizes: S < 1 day, M 1-3 days, L > 3 days of focused work.
`[DONE M1]` marks work implemented in this branch. `[AUTHORED, NOT RUN]` is intentional for the live lane per its
non-execution requirement.

## Matrix rows: the TUI is the product path (API-TUI first, as the operator asked)

| id | matrix | what | size | milestone | notes |
|---|---|---|---|---|---|
| TNT-01 | B-01 (input) | SPIKE: partition data source. What do `disk.details` (`join_partitions`?) and `pool.get_disks` return for a disk with partitions and a free tail; is SSH `sgdisk -p` / `lsblk --json` needed? Verify the `disk.query extra.pools` and `disk.details` params used by the M0 adapter on the throwaway host | S | M1 | Gate for TNT-02/03/04. Until closed the manifest says `verified = "reference"` and the `disks` command shows `?` for a disk without partition data |
| TNT-02 | disk free space | Storage > Free space and partitions: per-disk extents (start/size/gap), alignment, backup GPT; upgrade `Disk.free_bytes` from an upper bound to exact extents | M | M2 | Depends on TNT-01 |
| TNT-03 | B-01 | Reserve the free-tail partition (plan/apply). The API has no partitioning method, so this is the SSH fallback (`sgdisk`) behind a typed-confirmation gate and a second, explicit `--allow-ssh` switch | M | M2 | Design decided (DESIGN section 8.1): second switch `--allow-ssh`, fixed command list, `StrictHostKeyChecking=yes`, key file, no password; not implemented |
| TNT-04 | B-06 | Import Pool screen: `pool.import_find` then `pool.import_pool` (adopt a hand-made pool on a partition). Measured PASS via API on v27 | S | M2 | First write action; builds the plan/apply UI around `tnt.actions` |
| TNT-05 | B-02, B-03 | Create a pool on a partition or on free space. Stock `pool.create` takes disk names only (measured: EINVAL). Path: partition + `zpool create` + TNT-04 adoption. Decided 2026-10-28: (a), `zpool create` on the partition then TNT-04 adoption (matches the TrueNAS agent's shim); depends on TNT-03 and TNT-04 | L | M2 | Matrix verdict is "pending decision" |
| TNT-06 | B-01, B-06 | Boot pool data management (`boot-pool-data` / `boot-disk-data` layout): create, inspect, export-safe, reboot persistence | L | M2 | Depends on TNT-03/04/05 and the live reboot test |
| TNT-07 | A-09 | Cache/log/special vdev management through `pool.update` for ordinary pools; boot-pool zvol targets stay refused by design | L | M3 | Refusal rows B-07, A-08 are already on the refusal list in `tnt.actions` |
| TNT-08 | B-07, A-08 | Keep the refusal list in step with the matrix (a test already checks B-07's methods); extend when new `n/a` rows appear | S | all | Extended for M1; the manifest and cross-family boot-pool/pool-reference guards are explicit and tested (step 2 complete) |

## Screens mirroring the web UI menu (read views first, writes behind plan/apply)

| id | screen | milestone | size |
|---|---|---|---|
| TNT-10 | Dashboard (`system.info`, alerts, pool and disk health) | M1 | M |
| TNT-11 | Storage > Pools (`pool.query`, vdev tree, scrub status; scrub as the first MUTATE action) | M1 | M |
| TNT-12 | [DONE M1] Datasets (`pool.dataset.query`, tree, usage; create/update/delete behind gates; matrix A-01..A-03) | M1 | L |
| TNT-13 | System > Boot (`boot.get_state`, `boot.environment.*`; clone/activate/keep) | M1 | M |
| TNT-14 | System: General, Network, Services, Update (read first) | M3 | L |
| TNT-15 | [DONE M2] Shares: SMB, NFS, remote-path guard, ACL read, typed configuration deletion | M2 | M |
| TNT-16 | [DONE M1] Containers and Virtual Machines (matrix A-04..A-06, B-04; v27 `container.*` and `vm.*`). Current image, CPU-count and configured memory-limit fields are unavailable in the verified container schema; see TNT-36/37. | M1 | L |
| TNT-17 | Apps | M3 | M |
| TNT-18 | Data Protection (snapshots, replication, cloud sync, scrub/SMART tasks) | M3 | L |
| TNT-19 | Credentials, Reporting, Audit, Enclosure | M3 | L |
| TNT-38 | [DONE M1] Snapshots: list/create/clone/hold/release/rollback/delete through CLI and TUI, with drift probes and typed destructive confirmation | M1 | M |
| TNT-39 | [DONE M1] Containers: list/show/create/update/start/stop/restart/delete through CLI and TUI, including explicit one-shot CPU/memory usage status from `container.metrics`; v27 omits image identity, CPU count, configured memory limit | M1 | L |
| TNT-40 | [DONE M1] Virtual machines: list/show/create/update/lifecycle/device/console workflows through CLI and TUI; create and device plans are staged because v27 excludes devices from `vm.create` | M1 | L |
| TNT-41 | [DONE M1] Local users: list/create/update/delete, SSH public-key management and password changes through CLI and TUI | M1 | L |
| TNT-42 | VM create recovery: compensate or guide recovery when a separately reviewed `vm.device.create` plan fails after `vm.create` | M2 | M |

## Platform and quality

| id | what | size | milestone |
|---|---|---|---|
| TNT-20 | [AUTHORED, NOT RUN] Namespaced v27 live acceptance lane, requiring explicit profile/pool/group/image/VM-size inputs and cleaning up only `tnt-live-` resources | M | M1 |
| TNT-21 | [DONE M1] SCRAM fake-server handshake and v27 password login (both auth flows are exercised through the real client) | M | M1 |
| TNT-23 | Manifest drift test: compare each adapter manifest with the fetched `https://api.truenas.com/v27.0` method index (opt-in network test, not in the gate) | S | M1 |
| TNT-24 | Audit log (no secrets) of every executed action under the XDG state dir | S | M2 |
| TNT-25 | Continuous live updates: subscribe to jobs/collection updates so tables refresh without `r`; M1 only collects one bounded `container.metrics` sample per read | M | M2 |
| TNT-26 | Plan/apply CLI verbs (`tnt plan`, `tnt apply`) once the first action exists, with `--dry-run` / `--yes` from cli-extended | M | M2 |
| TNT-27 | TUI: confirm-gate modal (shows the exact calls, typed-name entry), in-TUI unlock of a write session, profile switcher | M | M2 |
| TNT-28 | Profile writer verbs (`profile add/remove`) that never accept a key on the command line | S | M1 |
| TNT-29 | cli-extended surface review (AC-17): the review catalog `docs/cli-review.toml` is empty; run `surface pack`, review, link `cli_case` tests, then `surface check` must pass | M | M1 |
| TNT-30 | Mutation lane (assay R2) for `tnt`, `assay.toml` + gate lane; coverage threshold (iobench holds 100%) | M | M1 |
| TNT-31 | Re-pin `truenas_api_client` from the TS-27.0.0-RC.1 sha to the TS-27.0.0 GA tag once it is 14 days old | S | GA+14d |
| TNT-32 | Register `tnt` in `cmru.orchestration.toml` and the root run-gate registry (not done in M0: see DESIGN section 12) | S | M0 close |
| TNT-33 | Gate environment: the r0-r1 lane needs `tnt` installed in the tester venv (the CLI resolves its version from installed metadata, like iobench) plus `textual` and `websockets` from the test extra | S | M0 close |
| TNT-35 | Detect per host which remaining conditional menu entries (Apps, KMIP, Enclosure) the web UI would show, and hide unsupported entries | S | M2 |
| TNT-36 | Revisit container memory controls if a future v27 schema exposes a supported memory resource field; current `ContainerCreate`/`ContainerUpdate` schema exposes `cpuset` but no memory limit (deferred by verified v27 schema) | S | M1 |
| TNT-37 | Expose a current container image identity if a future v27 API supplies it; `ContainerEntry` has no image field and create consumes image metadata without retaining it in the returned record (deferred by the verified schema; see `docs/API-METHODS-V27.md`) | S | M1 |
