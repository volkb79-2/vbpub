# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

<!-- cmru: release history -->

## [1.62.0-r4] - 2026-09-21
<!-- cmru: generated -->

### Changed
- Update eligible Playwright/MCP/Lighthouse dependencies and lock the vendored Lighthouse dependency tree.
- Make release preparation validate the committed version projection; upstream refresh is explicit.
- Require Streamable HTTP `/mcp` endpoints and keep legacy `/sse` disabled.

## [1.63.0-r2] - 2026-09-18
<!-- cmru: generated -->
<!-- cmru: source-end=3565200ecf3ce69ba8845254704b2e86ee75549e -->

### Changed
- Release metadata prepared by CMRU.

## [1.62.0-r3] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=c92b522d30b98cad4813f0c1c119e3e7d18498fd -->

### Fixed
- fix(nyxloom+pwmcp): nyxloom-P103 -- declare [governance] on both ciu roots, retire fictional nyxloom.slice (c703cd04)

### Changed
- pwmcp,topos: adopt $INSTANCE_ID/$DOCKER_NETWORK_INTERNAL naming (CIU-104 mitigation) (28a70b24)

### Documentation
- docs(run-gate): RG-13 adoption hygiene + estate budget↔timeout sweep (df5c9c10)

## [1.62.0-r2] - 2026-08-22
<!-- cmru: generated -->
<!-- cmru: source-end=9b6cd337bf199799fd147a8cc8acad52981018e1 -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)
- feat(cmru)!: enforce strict release framework (8dd0e416)

### Changed
- run-gate: estate-wide adoption as SSOT test definition (CIU-40 adoption half) (4c6eb2b6)
- cmru: centralize estate release policy (2281181a)
