# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

### Changed
- `get.py` downloads only contract-named assets (`<tag><asset_suffix>`), with no legacy-name fallback; a release published under another name, i.e. pwmcp releases <= v1.64.0-r2, is not installable by `get.py` (PWMCP-08).

<!-- cmru: release history -->

## [2.0.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=85c236dd5dae4c212a7e5652798dfd69c61e2f9a -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)
