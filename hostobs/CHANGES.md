# Changelog

## [Unreleased]

### Added

- Added the minimal hostobs Cargo workspace skeleton for the planned sampler crate split.
- Added offline Cargo settings and a lock-bound launcher that checks the baked crate cache before invoking Cargo with `--locked`.
- Added zero-retry nextest configuration, cargo-deny policy, source lint tooling, and per-crate coverage-floor checks at 90% line and 85% region.
- Added the `lint` and `selftest` Run-Gate lanes using the `tester-unified-rust` image; both run without network access.
