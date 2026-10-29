# Changelog

All notable changes to iobench are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. The hand-written section carries what a consumer must know.

## [Unreleased]

<!-- cmru: release history -->

### Fixed
- Keep the Assay whole-target lane measurable: exclude the docstring-only package initializer, let pytest finish the coverage run after a failure, and reap both detached subprocesses created by the bounded-reaping test.

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
