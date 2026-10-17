# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

<!-- cmru: release history -->

## [1.64.0-r2] - 2026-10-17
<!-- cmru: generated -->
<!-- cmru: source-end=0859edf9b96489ef032f6f6b4d9cb1cd1147246f -->

### Fixed
- fix(pwmcp): keep fetch skill install agnostic (948080489)

## [1.64.0-r1] - 2026-10-17
<!-- cmru: generated -->
<!-- cmru: source-end=0f9b1cc6a3fb7d74018d3f5f7cf8c5fddaf5c69d -->

### Added
- feat(pwmcp): add opt-in inspect TLS bypass (849129bfc)

### Fixed
- fix(pwmcp): use cached pinned images during deployment (df42e0602)
- fix(pwmcp): preserve instance override across installer updates (29280b8a2)
- fix(pwmcp): make standalone instance start with ciu up (9833e8386)
- fix(pwmcp): include all offline installer wheels (504c0e7ef)

### Changed
- pwmcp: guide agents through late page hydration (6b5b57382)
- pwmcp: update CLI review browser mock (20246a50c)
- pwmcp: wait for repeated visible content selectors (0c879d420)
- PWMCP select Playwright 1.64.0 with two-day age window (fb34492cc)
- chore(pwmcp): select release version r4 (8e88d50bf)
- pwmcp: clear idle timer during gateway shutdown (f335b26b1)
- pwmcp: allow remote CLI through run-server gateway (94fbb9e13)

### Documentation
- docs(pwmcp): document MDT CLI launcher path (ceb57e542)
- docs(pwmcp): record inspect TLS acceptance (81c5fcd36)
- docs(pwmcp): record local inspection acceptance (b4610ee4e)

### Testing
- test(pwmcp): parse annotated wheel projection (93d3c016a)

## [1.63.0-r3] - 2026-10-16
<!-- cmru: generated -->
<!-- cmru: source-end=8d372647e02c88e0213c55daa191a7a9896e2f86 -->

### Added
- feat(pwmcp): reviewed CLI surface, pytest plugin, docs and orchestration edge (PWMCP-02) (7609d7078)
- feat(pwmcp): release-step scripts on the installed cli-extended and cmru (PWMCP-02) (d51d44482)
- feat(pwmcp): pwmcp client CLI on cli-extended (PWMCP-02) (eb2505586)
- feat(pwmcp): modernize pinned MCP stack and projection checks (553f00d8a)
- feat: add estate cli version compatibility (05f373a40)

### Fixed
- fix(cmru): W2-INTEG review round 1 (C1-C7, run_step removal) (508bc83d6)
- fix(pwmcp): enforce stream-only MCP gateway (c488571a8)
- fix(pwmcp): use managed remote BuildKit service (0d2fb1326)
- fix(pwmcp): enforce Playwright base image digest pin (0198a5237)
- fix(pwmcp): make prepared vars loader report missing files (adff180b0)
- fix(pwmcp): fail closed on resolver template drift (f020f8040)
- fix(gates): correct release and VM lane contexts (a4bba60db)

### Changed
- pwmcp: record R1 gate evidence (bc33901ba)
- pwmcp: cover missing Dockerfile version arg (d7caee83c)
- pwmcp: provide archive test temp path (4c284a9d9)
- pwmcp: isolate bundle archive test (75191985b)
- pwmcp: verify page inspection and version projections (cd0e34041)
- Add standalone PWMCP instance and inspect CLI (dc4072cf2)
- chore(pwmcp): clear smoke script lint warning (6cb99c810)
- cli: universalize vbpub parser diagnostics (aa0e69fad)
- chore: land run-gate root and dev-gates migration (41c1cafb3)

### Documentation
- docs(pwmcp): record second r1 gate run at the report tip (e76070db3)
- docs(pwmcp): PWMCP-02 report, backlog FIXED, program row 5; file cmru KI-70 and CLI-EXT-32 (194f01e1d)
- docs(cli-extended): file planned adoptions in each tool's backlog (W10) (c8a56ad8f)
- docs(pwmcp): add PWMCP-01 (665246456)
- docs(pwmcp): record final external gate evidence (79e2efd7e)
- docs(pwmcp): document stream-only MCP gateway (12334e05d)
- docs(pwmcp): record managed builder review (9915628bb)
- docs(pwmcp): align release commands with cmru (de6fbddef)
- docs(pwmcp): update modernization completion criteria (159074769)
- docs(pwmcp): record digest and external gate status (b45992a5f)
- docs(pwmcp): adopt project-scoped version targets (9bd64ff0a)
- docs: formalize version target schema and backlog name (99d460ec1)
- docs(pwmcp): centralize modernization and disable legacy SSE (afe9f7a9b)
- docs(pwmcp): record devtools MCP security changes (ffb41179d)
- docs(pwmcp): note npm dependency reproducibility gap (2d9283b03)
- docs(pwmcp): record resolver hardening findings (204803505)
- docs(pwmcp): review coverage and upstream compatibility (10e08b8c8)

### Testing
- test(pwmcp): assert builder diagnostics under mutation (d770556cc)
- test(pwmcp): cover ignored builder inspection lines (8131dbabd)
- test(pwmcp): match managed builder endpoint in entrypoint fixture (aefa29917)
- test(pwmcp): close remaining mutation gaps (0f3f04067)
- test(pwmcp): correct external mutation oracles (784457397)
- test(pwmcp): harden mutation oracles (87c79ea90)
- test(pwmcp): restore projection after refusal probe (7baef4502)
- test(pwmcp): cover digest projection safeguards (fd5ff337b)
- test(pwmcp): close resolver defensive branch (da7b517f9)
- test(pwmcp): cover resolver age and projection refusals (f68061e45)
- test(pwmcp): cover script entrypoint and refusal paths (43a45714b)
- test(pwmcp): repair resolver refusal fixtures (a6480ccf1)
- test(pwmcp): cover remaining release branches (b5f4472fb)
- test(pwmcp): cover release and client branches (c257109ce)
- test(pwmcp): adopt assay R1 and R2 gates (f23a42237)

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
