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

<!-- cmru: release history -->
