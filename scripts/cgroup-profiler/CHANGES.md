# Changelog — cgroup-profiler

All notable changes to this project are recorded here. Entries marked
`cmru: generated` are produced from the project-scoped release range before
the release gate runs. Normative daemon behavior lives in `DESIGN.md` and
`docs/PROTOCOL.md`; entry-by-entry rationale lives in
`nyxloom-trove/backlog/` and git history.

## [Unreleased]

### Added
- First SemVer release of the `cgprofile` CLI and host daemon, including the
  separate read-only contract-2 observer for native Assay R2 lanes. The
  observer reports event counters for the candidate cgroup and its enforcing
  ancestors without exposing the daemon control socket.

### Changed
- Documentation: identify the installed Run-Gate SemVer wheel as the supported
  consumer of the CGProfile interface; copied `run-gate.py` scripts are retired.
- Gate R3 now declares memory and swap ceilings, so Run-Gate admits it against
  the gates-slice budget and Docker enforces its per-container limit.
- The native R2 resource observer refuses a host cgroup2 mount using
  `pids_localevents`: its fork-failure counter records the cgroup where the
  fork was attempted, so it cannot identify which ancestor's `pids.max`
  rejected it. Successful responses always report
  `pids_events_scope = "hierarchical"`; `memory_localevents` remains supported.
- Documentation: the native-R2 lane example declares the required
  `resources.memory` and `resources.pids`.
- Release qualification: CMRU's release gate runs `run-gate r0-r1` and
  `run-gate r3` only. The complete Run-Gate `gate` lane additionally runs the
  24-hour `r2` mutation campaign. The initial 1.0.0 release has no completed
  full-source R2 verdict; the long R2 campaign was deferred under the
  operator's release policy.

<!-- cmru: release history -->
