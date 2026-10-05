# Changelog — cgroup-profiler

All notable changes to this project are recorded here. Entries marked
`cmru: generated` are produced from the project-scoped release range before
the release gate runs. Normative daemon behavior lives in `DESIGN.md` and
`docs/PROTOCOL.md`; entry-by-entry rationale lives in
`nyxloom-trove/backlog/` and git history.

## [Unreleased]

### Changed
- refactor(cgprofile): express insertion-only AVL rotation choices at
  reachable child-balance boundaries and reduce peak values with `max`;
  public summary values and schema are unchanged (RG-55 P1 survivor disposition)

### Fixed
- fix(cgprofile): initialize DAMON from complete sysfs inputs before `state=on`,
  and rebuild the exact target array on startup and online updates so reused
  slots or shrinking subtrees cannot retain former lane PIDs; late DAMON
  recommit/collection failures now disable only DAMON and preserve profiling
  samples and verdict neutrality (RG-55 P3, R-36h)
- fix(cgprofile): D-31 placement ownership — create a systemd-delegated transient scope under the verified gates slice, keep the leaf below it, journal PID identity/origins for safe stop and restart recovery, and document that leaf memory limits/counters are cgroup charges rather than total RSS
- fix(cgprofile): CP-11 — retry identity-checked placement recovery for journal-only startup crashes and finished manifests with incomplete cleanup; retain unknown scopes/leaves for operator attention
- fix(cgprofile): fail closed on non-ESRCH lane-PID migration write errors; refuse placement and remove an empty leaf instead of reporting successful placement
- fix(cgprofile): restore P6 survivors through the verified host-proc/systemd bridge; record successful moves and retain/report a leaf when survivor enumeration or restoration is indeterminate
- fix(cgprofile): RG-55 P6 review round 3 -- bind D-25 writes to the session's exact leaf, refuse an existing token leaf, prevent FIFO progress-stream blocking and partial-line liveness resets, report truncated watch streams as daemon faults; correct the gates-tier adopter example
- fix(cgprofile): CP-4 -- widen `new_run_id`'s random suffix 4->8 hex chars, killing the birthday-paradox collision flake (376bb9cb)
- fix(cgprofile): CP-10 -- decouple the daemon's host-PSI liveness pause check (`host_proc_root`) from the real `/proc` used for subtree/pid resolution, ending a real-host-memory-pressure-driven test flake session 6 misattributed to test order (b50163e9)

### Added
- feat(cgprofile): CP-5 -- daemon sessions write real `events.jsonl` rows via `lib.events.Detector` on the sampler's own cadence (16b01c1c)
- feat(cgprofile): CP-7 -- the session manifest's `limits` table resolves real effective cgroup limits at `start` (907ddd50)
- feat(cgprofile): CP-6 -- DAMON hot/warm/cold/idle series read into `ctl report`'s HTML render (e053276b)
- feat(cgprofile): `cgprofile.slice` (D-29) + `ctl host`'s `gates_slice`/`daemon_slice` blocks (§8.5); the ciu compose template authors `cgroup_parent: cgprofile.slice` (39d43934)
- feat(cgprofile): CP-2 -- the socket carrier (D-30, §8.1/§8.6): a host-visible `/run/cgprofile/ctl.sock`, `SO_PEERCRED` admission, `CGPROFILE_ALLOW_UIDS`, `docs/PROTOCOL.md` (bb575fd4)
- feat(cgprofile): CP-8 -- the watch role (D-27, §8.2/§8.4): liveness policy at `start`, the `ok`/`stalled`/`hung`/`runaway`/`throttled`/`over_ceiling` state machine, `--on-stall kill`, streaming `ctl watch` on both carriers (4fa725dc)
- feat(cgprofile): CP-9 -- placement (D-20/D-25, §8.3): `--place`/`--memory-high`/`--memory-max`/`--cpu-weight`, a profiler-owned leaf under a systemd-delegated per-lane scope below the gates slice, migrate-on-discovery, `cgroup.kill`, D-15 application write-guard whitelist extension (a654bd5d; physical ownership corrected by D-31)

### Documentation
- docs(cgprofile): `docs/PROTOCOL.md` (already complete as of C8), README "Running the daemon", `ATTACH-GUIDE.md` lane section, `DESIGN.md` D-27..D-30 summary, version `1.0.0` -> `1.1.0` sweep, backlog rows CP-2/CP-4..CP-10 -> FIXED, CP-11 filed (478f1443, RG-55 P6 C9 close-out)

<!-- cmru: release history -->
