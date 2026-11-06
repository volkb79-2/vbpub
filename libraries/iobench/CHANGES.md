# Changelog

All notable changes to iobench are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. The hand-written section carries what a consumer must know.

## [Unreleased]

### Added
- **`veryquick` calibration profile** (`calibrate --profile veryquick`, `IOB-PRF-03`): about 3 minutes (173 s planned, 210 s hard budget) for an installer that wants a binding and a plausibility check. S0, S1, a 20 s fill, E-cold (QD32, QD1 histogram, and a cache-hit reference on the last GiB written), one 30 s write series, four 12 s cells (4 KiB random and 128 KiB sequential, read and write). **Every cap and model value is `unproven: veryquick_profile`**; the model is a coarse formula estimate (`metering: coarse`, also `formula_only`); QoS latencies and writeback are `absent: stage_skipped`; the run adds nothing to the ceiling history. New vocabulary: unproven reason `veryquick_profile`, limit `very-quick-profile`, metering `coarse`. `calibration.veryquick` records whether a write burst ended inside the series.
- **`quality.plausibility = {verdict, cache_state}`** on every calibration (`pass`, `fail`, `inconclusive`), and `IOB-PLA-19` (veryquick: >20x through 100x is `info`/inconclusive without a hit-like cold probe; a hit-like probe makes both the check and verdict `fail`; >100x fails on its own). Both ratio thresholds are inferred and await IOB-ACC-01 calibration (`IOB-BL-23`).
- **Disk-scope identity and history key** (`IOB-CEI-06`, host-tuning review B2): `identity.disk_scope` (`model|serial|wwn|size_bytes` of the whole disk; a weak disk is bound to its host only when a machine-id hash was stored). QEMU `QM\d{5}`, `QEMU HARDDISK`, VirtualBox `VB` serials with two eight-hex groups, `vdisk` plus a decimal index, and common placeholders are weak. A weak disk lookup after target removal requires the remembered name, equal model and size, and a weak candidate; a known stored machine-id must match the live non-null id, while a null stored hash binds no host. Weak-to-strong changes are stale. Consumers use the key stored in the identity because chroot-created records can have a different live key after first boot.
- **Evidence classes** (`IOB-CEI-07`): every observation has a `kind` (`raw`, `file`, `probe`); writes are stable-confirmed by raw/file observations, reads by raw/probe observations, and raw rows alone supply model/cap values. `ceilings add --kind` validates source/kind pairs; `ceilings summary --kind` and `by_kind` report each class apart. `summary`, `check.history` and `recalibrate_recommended` use stability-eligible rows. Benchmark observations also record the measured partition's `devno` and `partuuid`.
- `target_changed` check warning: the measured partition or file is gone, formatted, replaced or resized.

### Changed
- **Result validity is the disk, not the partition** (`IOB-RES-03`): `check` is `stale` when the whole-disk scope differs or the disk cannot be found. A weak scope becoming strong is stale; with no target path, a weak disk requires its remembered name, model, size and weak identity. A known stored machine-id must still match a live non-null id; a null stored hash binds no host. A throwaway partition deleted after the run, including after an installer chroot's first boot, does not make the result stale when its weak disk can still be identified. A work dir removed after the run is not `workdir_fs_changed`.
- **Breaking, schema minor 1 (nothing of iobench has run on a real host yet):** a calibration or result written before the disk scope (`schema_minor` 0) is `stale`, `ceilings add` refuses its identity (`identity_legacy`) and its history is moved aside by the next calibration. Observations require a `kind`; `ceilings add --source governor` requires `--kind` (a governor measures on a scratch file or a raw device, it has to say which).
- `ceilings summary` adds `by_kind`, and its aggregate (like the one in `show --ceilings`) counts only stability-eligible observations; a file target's history now shares the disk's key instead of being kept apart by the target kind (`IOB-TGT-09`).
- `FakeHost` lists `/sys/block` (whole disks) and symlinks in `listdir`; `standard_host` has an `/etc/machine-id`.

### Fixed
- Weak disk scopes no longer resolve by size alone; replaced weak disks and weak-to-strong changes are stale. Virtual placeholder serial patterns are weak, null stored machine-id hashes remain unbound across first boot, and identical cloned-template machine-ids remain tracked in IOB-BL-25.
- File/probe history can confirm stability only and cannot lower model/cap proposal values; `check.history` uses the same filtered stability rows as `ceilings summary`.
- `ceilings add` rejects impossible `--source`/`--kind` combinations. `veryquick` preserves existing fuller calibration/result files and requires a separate `--state-dir` when needed. Mismatched history is named `.superseded-<timestamp>`.

<!-- cmru: release history -->

## [0.1.1] - 2026-11-04
<!-- cmru: generated -->
<!-- cmru: source-end=fc3db7ddfda84e38e662bccd4da708645ee69b48 -->

### Added
- feat(run-gate): enable native R2 jobs (36d7b97b5)

### Fixed
- fix(iobench): per-test limit counts CPU time not wall-clock; procfs-unreadable child check warns (delta review I1-I4) (511fc0c12)
- fix(run-gate): address PR3 capacity review (5e1a2aae2)
- fix: repair merged R2 gate failures (80e3d81fd)
- fix: repair whole-source R2 start failures (5b042b60b)
- fix(assay): repair main's red assay tests: v16 fixture evidence, empty-shard verdict fixture, frozen schema digest, state-dir missing refusal, iobench lane config; fix doc anchors; document schema v2 of analyze campaign (154983976)

### Changed
- iobench: IOB-R2-01 group C oracles (result, ceilings, devio, stages, sweep, ladder, proposals, plan, extents); BL-11/13/14/16 docs; IOB-BL-19 (7be7bb993)
- iobench: IOB-BL-11/13/14/16 (file-target refusal, exact dry-run argv, close kill() temp files, diskstats RunFailed) (7df306657)
- Merge branch 'fan-iob-b-20261103' (6350a31b4)
- iobench: IOB-R2-01 group B status (per-file triage, equivalence arguments, budget-exceeded diagnosis) (7e84620be)
- iobench R2 (IOB-R2-01 B): plausibility threshold oracles (bd28b08c2)
- iobench R2 (IOB-R2-01 B): derive boundary oracles (785ed3484)
- iobench: IOB-R2-01 group A status (calibrate/cache/dryrun) and the budget and bytes-check oracles (7ebfc174e)
- iobench R2 (IOB-R2-01 B): matrix oracles (420f8dc3c)
- iobench R2 (IOB-R2-01 B): restore oracles; drop the dead prior.locked reset in recover_previous (8cb335c81)
- iobench R2 (IOB-R2-01 B): guard oracles (0b0476f40)
- iobench: calibration oracles for IOB-R2-01 (fill, ladder densification, analysis, document assembly) (ffe80b9b6)
- iobench R2 (IOB-R2-01 B): shape and cli oracles (a6a5f6f46)
- iobench R2 (IOB-R2-01 B): FakeHost and series oracles; per-test time limit turns non-terminating mutants into fast kills (f1309a5d9)
- iobench: cache estimator boundary oracles for IOB-R2-01 (37 of 44 cache.py survivors killed; 4 equivalent, 3 float-boundary open) (b425d1b8f)
- iobench: dry-run plan oracles for IOB-R2-01 (13 of 14 dryrun.py survivors killed; line 54 is dead in the plan path) (906eb4d2c)
- Add combined resource settings and IO sync delivery (da11e76b7)
- Fix iobench test child cleanup (fbecef180)
- Merge assay 10.0.0 candidate: R2-free provisional lane, CMRU release-version binding, review fixes (reasonix-reviewed) (f6ad91128)

### Documentation
- docs(iobench): IOB-R2-01 triage plan with scheduling and profile hooks (6b146cc91)
- docs(iobench): file IOB-R2-01 (first native R2: 318 survivors, 5 budget-exceeded) (df2e3ef3d)

### Testing
- test(assay): cover socket-vs-directory group mismatch in the observer gid guard; iobench lane enforces branch coverage (f372310c2)

<!-- cmru: pending release notes begin -->
### Changed
- Run the registered R2 lane through native Run-Gate Assay dispatch with `jobs = "auto"`; raise the Assay lane ceiling from the legacy `jobs = 1` alias to canonical `jobs_max = 4`.
- This command-to-native lane switch changes campaign storage: the old `.assay/mutation-state-r2` state directory and `.assay/progress-r2.jsonl` progress file are not reused. The native lane uses Run-Gate's durable state area under `.run-gate/` and `.assay/progress-iobench.jsonl`; a campaign already running with the old configuration does not carry over.

### Fixed
- `calibrate` with a file target on a filesystem with no single block device behind it (overlay, multi-device, network) refuses cleanly (`target_fs_disk_unknown`, exit 2) instead of a traceback (IOB-BL-11).
- `matrix` ends with a failed run, not a `StopIteration` traceback, when the work dir's `/proc/diskstats` row disappears mid-run (IOB-BL-16).
- `--dry-run` for `matrix` prints the argv a real run builds (file names, log prefixes, queue depth), built from the same job constructors; names carry the plan's run id (IOB-BL-13).
- The temporary stdout/stderr files of a killed fio are closed in `kill()` (IOB-BL-14).
- New oracles for the R2 survivors in result, ceilings, devio, stages, sweep, ladder, proposals, plan and extents (IOB-R2-01).
- Keep the Assay whole-target lane measurable: exclude the docstring-only package initializer, let pytest finish the coverage run after a failure, and reap both detached subprocesses created by the bounded-reaping test.
- Test infrastructure (`tests/conftest.py`, not shipped in the wheel): every test now runs under a CPU-time limit (`ITIMER_PROF`, 100 CPU seconds, 10x the slowest measured test, `@pytest.mark.time_limit(cpu_seconds)` overrides) so a mutant's non-terminating loop is killed while host load cannot fail a slow-but-correct test; the session registers as child subreaper to check for unreaped children, and degrades to a warning when `/proc/<pid>/task/<pid>/children` is unreadable.
<!-- cmru: pending release notes end -->

## [0.1.0] - 2026-10-28
<!-- cmru: generated -->
<!-- cmru: source-end=8cbb0352f075d37601143d79512f7a8b80d41b13 -->

### Fixed
- fix(iobench): init_file revokes only the scratch root it added, on every exit but success; a None/symlink stat after fallocate is path_unclean (fdd7b6a6a)
- fix(iobench): round-4 review lows: TOCTOU limit stated, target-init directory authorised only after the checks (revoked on refusal), pseudo filesystems refused, fio .N.log cleanup (43eaa540a)
- fix(iobench): review round 4 fold-ins: contained preallocate, scratch cleanup that only removes iobench's fio logs and never raises, typed fio --name (ac7938bd3)
- fix(iobench): review round 3: every non-fio argv pinned to its builder, fallocate only through Host.preallocate/discard_range, caps, run-dir cleanup, release-note caveats (9da4531f5)
- fix(iobench): review round 2: scratch-root containment for every argv path, the probe script pinned byte for byte, typed fio values (61b85b430)
- fix(iobench): pre-release review: Host-level argv bindings, per-cell load-file removal, guard-failure abort, preflight input refusals (7b485d7f8)
- fix(iobench): review r2 for IOB-BL-06: sweep repeat cells no longer put the cell name (/ * +) into the probe unit name; unknown unit state is not 'gone'; FakeHost validates unit names (caca5f042)
- fix(iobench): review r1 for IOB-BL-06: exact fio key allowlist, abort path never raises, ActiveState wait, plain probe-unit stop names (2982c1ff9)
- fix(iobench): IOB-BL-06 N-r3-1..4 abort stops the probe unit, bounded reap after killpg, exclusive device writers, tighter fio allowlist (b24167e69)

### Changed
- tests/docs: clarify init_file scratch-root revocation (9b7f442df)
- iobench: wind-down backlog (r3 SHOULDs, E2 multi-tier decision) and review record (8b0e5b33f)
- iobench: notes for the coordinator rulings (c7d28cea3)
- iobench: two-tier end-to-end test, SPEC 18 reconciled with code, docs for the rulings, backlog (53ae0533f)
- iobench: ruling 3 (write BPS unproven), NEW-1 schema-derived check, S13 exclusion, 150 s fresh observation, fresh exclusion reason (0a1f7d3b5)
- iobench: coordinator rulings 1-2, A-S1, A-S9, N2, N-r2-1..3 (2bfd8935a)
- iobench: review wave docs triad, notes, file-target link recheck, drain_rate guard (201903edf)
- iobench: review A should-fixes (holders, file targets, orphans, partial spawns, drain warning) (dc2a92ccb)
- iobench: review B tests (run-level oracles per finding), direction-aware reference movement (9f5cef56d)
- iobench: review B should-fixes (fresh reference before S6, budgets, QoS inheritance, matrix, history rotation) (4eb5f123c)
- iobench: review B estimator and derivation fixes (burst proof, cold state, write basis, check, model, PLA wiring) (795bfe774)
- iobench: review A safety fixes (journal ownership, coupled sysctls, containers, probe codes) (4c86fde8c)
- iobench: no_cache_observed is unproven (cache_not_excluded); corrected R-11 (30a847beb)
- iobench: coordinator rulings - wheel-only REL-01, CAL-01 numbers, SHP-04/PLA-11, no_cache_observed (3c6860c1c)
- iobench: reviewed CLI cases, audit test, notes, cleanup (6668d5342)
- iobench: 100% branch coverage, corner-case tests, CCH-08/EVD/DOC/GATE, CLI tests (859f6fd25)
- iobench: fixtures, EVD replays, docs-sync and gate tests, PLA-15 end-of-run identity check (265eafca2)
- iobench: CLI shell, packaging, generated CLI surface (review cases pending) (bc62742d4)
- iobench: audit, safety/arch/lat/cei/cch integration tests and fixes (44a06167a)
- iobench: check/show/result tests, implementation notes (8bd1b1777)
- iobench: matrix and file-target stage tests (eb7ec563f)
- iobench: dry-run planner, slo/prf stage tests, fixes (ac9a04f40)
- iobench: calibration and matrix runners (first end-to-end on FakeHost) (fc1d1af23)
- iobench: safety journal, ceilings history, result/check, plausibility, proposals status model (a5a45c021)
- iobench: sweep decisions and derivation rules (5d4397be4)
- iobench: ladder, shape and metering cores (91b570389)
- iobench: target guard, errors, file targets, work-dir rules (c57eb3d2b)
- iobench: cold probe record test (746607aa6)
- iobench: planner (allotments, rungs) and cache-state estimators (83c693fdd)
- iobench: foundations (series, extents, host protocol, devio, FakeHost/FakeFio with content-based cache) (63e022dc8)
- iobench: revision 4 after controller re-review R2-1..R2-4 (651a99ea4)
- iobench: revision 3 after controller review O-1..O-8 (31358d690)
- iobench: revision 2 of README, DESIGN-GUIDE, CONSUMERS, SPEC; assay lane (IOB-03 P1/P2 review fixes) (50492cbbb)
- iobench: README, DESIGN-GUIDE, CONSUMERS, SPEC and run-gate lane (IOB-03 P1/P2) (b9d1f2178)

<!-- cmru: pending release notes begin -->
First release candidate (IOB-03 P3 plus the review waves). Everything below was verified on the fake host (`FakeHost`/`FakeFio`, 100 % branch coverage over `src/iobench` except `host.py` and `__main__.py`); `RealHost` has never run against a real device, fio or systemd (IOB-ACC-01 pending).

### Added
- The measurement core (calibration: fill, cold-region probe, ladder, fresh vs rewrite, time-axis write proof, cost-shape grid, latency curve; matrix: drain rate, interference sweep, read cost), the proof model (`proven`/`unproven`/`absent` per value, `check` exit 7 when any requested value is not proven), the ceiling history, and the `iobench` CLI over cli-extended (`target init|show`, `calibrate`, `matrix`, `run`, `check`, `show`, `ceilings add|summary`, `restore`).
- Safety: dedicated targets with a marker (device or preallocated file), namespace proof, restore journal with the coupled dirty-ratio knobs, io.max watch, abort guard, single instance, orphan-fio refusal.

### Changed (pre-release review, 2026-10-27)
- The Host binds argv independently of the caller (`check_argv`, RealHost and FakeHost): `blkdiscard`/`fallocate -p` only through `discard_range`; a fio writer through a plain `run`/`spawn` only to an `iobench-*`-named load file inside a scratch root the run authorised (`Host.allow_scratch`: the run's log directory and the verified work dir; symlinks resolved, never a device node or a symlink, `:`/`,`/whitespace/control characters/`..` refused); every fio log prefix is bound to the same roots, bound or not; numeric fio values are non-negative integers; `--rw` limited to `read|write|randread|randwrite` (`trim*` would discard); the `systemd-run` argv is exactly the probe-unit shape with the probe script compared byte for byte (`iobench.probe_source`, a new leaf module) and its directory/output bound to the roots.
- `s0` creates a new work dir (previously a "new" work dir had to exist for fio to create its load files).
- Round 3: every non-fio argv is pinned to its builder in length and slots (`fallocate -l 1 -p /dev/sda` and every `-d`/`-z`/`--punch-hole` form refused); `fallocate -l` runs only through the new bound `Host.preallocate` (a plain `run` refuses every fallocate); `RuntimeMaxSec` positive and <= 600 s; slice names never start with `-`; fio magnitudes capped; `/run/iobench/<run_id>/` is cleared after every run (IOB-BL-09 closed); the cmru release notes carry the caveats.
- Round 4: `Host.preallocate` is contained (inside the directory `target init` verified and authorised; absent or a regular file, never a device node or symlink; `path_unclean` refusal); the scratch cleanup removes only the fio log files iobench named and can never raise out of `cleanup` (a failure is `restore.warnings[]: scratch_not_cleared`, the lock is always released, the verdict never changes); a fio `--name` is one clean token. `target init --file` authorises its directory only after every check passed (revoked on refusal), refuses pseudo/volatile filesystems (`devtmpfs`, `devpts`, `proc`, `sysfs`) before the UUID check, and the scratch cleanup accepts fio's `.N.log` numbering. The containment is check-then-exec (stated in SPEC ARC-05).
- Round 6 review nits: test KeyboardInterrupt/SystemExit propagation and root revocation; clarify IOB-ARC-05 and implementation notes.
- The matrix removes a cell's load files behind the cell (the work-dir check sizes one cell), and refuses a work dir on a filesystem with no single block device behind it (`workdir_disk_unknown`) instead of failing mid-run.
- A protected cgroup whose `io.pressure` becomes unreadable mid-run aborts the run (exit 8, restored) instead of ending it with a traceback.
- `probe_failed` also covers a probe binary that is missing; `production_pattern_invalid` for a malformed `--production-pattern`; the orphan-fio scan tolerates a non-UTF-8 `/proc/<pid>/cmdline`; the restore journal lists each file once.
- RealHost: the parent-death `prctl` is resolved before the fork (not inside `preexec_fn`); a file target's marker is opened `O_NOFOLLOW`.

### Known limitations
- `check` exit 7 is the expected answer for `caps` and `iocost_model` until a `seq1m` ceiling history exists (IOB-BL-03, IOB-BL-07 R3-2); the abort guard watches IO pressure only (IOB-BL-12); cross-direction ceiling movement is a blind spot (IOB-BL-04). See `BACKLOG.md`.
<!-- cmru: pending release notes end -->
