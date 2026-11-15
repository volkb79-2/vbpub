# Changelog

## [Unreleased]

### Fixed

- `build-image.sh` passes the `cmru tester-gate` bounds through `CMRU_TESTER_*` variables instead of shell-expanded option values, so the estate argv check in cmru parses the invocation (KI-173).

### Added

- Added the dedicated Rust gate image with a lock-bound offline crate cache, cache precheck, and registered selftest lane.
