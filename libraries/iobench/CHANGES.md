# Changelog

All notable changes to iobench are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. The hand-written section carries what a consumer must know.

## [Unreleased]

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

<!-- cmru: release history -->
