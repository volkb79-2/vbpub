# Changelog

All notable changes to iobench are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. The hand-written section carries what a consumer must know.

## [Unreleased]

First release candidate (IOB-03 P3 plus the review waves). Everything below was verified on the fake host (`FakeHost`/`FakeFio`, 100 % branch coverage over `src/iobench` except `host.py` and `__main__.py`); `RealHost` has never run against a real device, fio or systemd (IOB-ACC-01 pending).

### Added
- The measurement core (calibration: fill, cold-region probe, ladder, fresh vs rewrite, time-axis write proof, cost-shape grid, latency curve; matrix: drain rate, interference sweep, read cost), the proof model (`proven`/`unproven`/`absent` per value, `check` exit 7 when any requested value is not proven), the ceiling history, and the `iobench` CLI over cli-extended (`target init|show`, `calibrate`, `matrix`, `run`, `check`, `show`, `ceilings add|summary`, `restore`).
- Safety: dedicated targets with a marker (device or preallocated file), namespace proof, restore journal with the coupled dirty-ratio knobs, io.max watch, abort guard, single instance, orphan-fio refusal.

### Changed (pre-release review, 2026-10-27)
- The Host binds argv independently of the caller (`check_argv`, RealHost and FakeHost): `blkdiscard`/`fallocate -p` only through `discard_range`; a fio writer through a plain `run`/`spawn` only to an `iobench-*` load file (a target writer exists only through `write_range`); `--rw` limited to `read|write|randread|randwrite` (`trim*` would discard); the `systemd-run` argv is exactly the probe-unit shape (no other property or command).
- The matrix removes a cell's load files behind the cell (the work-dir check sizes one cell), and refuses a work dir on a filesystem with no single block device behind it (`workdir_disk_unknown`) instead of failing mid-run.
- A protected cgroup whose `io.pressure` becomes unreadable mid-run aborts the run (exit 8, restored) instead of ending it with a traceback.
- `probe_failed` also covers a probe binary that is missing; `production_pattern_invalid` for a malformed `--production-pattern`; the orphan-fio scan tolerates a non-UTF-8 `/proc/<pid>/cmdline`; the restore journal lists each file once.
- RealHost: the parent-death `prctl` is resolved before the fork (not inside `preexec_fn`); a file target's marker is opened `O_NOFOLLOW`.

### Known limitations
- `check` exit 7 is the expected answer for `caps` and `iocost_model` until a `seq1m` ceiling history exists (IOB-BL-03, IOB-BL-07 R3-2); the abort guard watches IO pressure only (IOB-BL-12); cross-direction ceiling movement is a blind spot (IOB-BL-04). See `BACKLOG.md`.

<!-- cmru: release history -->
