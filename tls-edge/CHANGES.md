# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

### Changed
- fix(calls): `[tls_edge.calls]` now defaults to `enabled = true, public_port = 8443` (was off / 8444), agreeing with the Mattermost project's default media port; `[tls_edge.ports] https_alt` default moves 8443 -> 9443 so public TCP 8443 belongs to Calls (matches the live host); `scripts/install.sh --ports` alt default follows. New test `tests/test_calls_defaults_agree.py`.
- `get.py` re-rendered from the current cmru installer template; download behaviour unchanged (it requests only `<tag><asset_suffix>`).

<!-- cmru: release history -->

## [2.0.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=85c236dd5dae4c212a7e5652798dfd69c61e2f9a -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)
