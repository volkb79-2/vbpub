# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

<!-- cmru: release history -->

## [0.3.2] - 2026-09-17
<!-- cmru: generated -->
<!-- cmru: source-end=fba1be96e9ec571e6567800869e0c6cd740e2f02 -->

### Changed
- chore(nyxloom): archive historical handoffs and reports (28801a95)

## [0.3.1] - 2026-09-16
<!-- cmru: generated -->
<!-- cmru: source-end=54dfca2f0d6e075f23941afbcfe7836fca4bdda0 -->

### Documentation
- docs: propagate MDT governance ownership and names (2e5f6463)

## [0.3.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=94917b55f7274f8c3665f706fd2550cdbfb9435f -->

### Added
- feat(topos): P93 lifecycle owner-chain protocol and action migration (port-forward) (864674d7)

### Changed
- pwmcp,topos: adopt $INSTANCE_ID/$DOCKER_NETWORK_INTERNAL naming (CIU-104 mitigation) (28a70b24)
- merge(nyxloom): make the AGENTS.md pointer block a REQUIRED adoption step (950046d9)
- merge(topos): P93 lifecycle owner-chain protocol — port-forward + independent review APPROVE (3f156379)
- merge(topos): P91 persistent capped history — port-forward + independent review APPROVE (d82c35af)
- topos-P91: close final diff-coverage gaps (2 lines) (c9d70520)
- topos-P91: close remaining diff-coverage gaps (recovery-internals branches) (301c7909)
- topos-P91: close diff-coverage gate on the ported wiring; fix a latent COMPONENT_NAMES gap (c5bd5a1a)
- topos-P91: port forward persistent capped daemon history (groop->topos rescue) (ecd671fe)

### Documentation
- docs(topos): update backlog ideas (59c2dd3f)
- docs(topos-P91): LOG/REPORT/REVIEW for the groop->topos port-forward (5519eb87)
- docs(topos/P93): record official gate results in LOG/REPORT port-forward addenda (995b57d7)
- docs(topos/backlog): B-046/B-047 -- 4 hardcoded-"0.1.0" tests break topos-suite on main (e9f8c4d6)
- docs(topos): update backlog entries (9f8e4929)
- docs(run-gate): RG-13 adoption hygiene + estate budget↔timeout sweep (df5c9c10)
- docs(nyxloom): make the AGENTS.md pointer block a REQUIRED adoption step (97324f46)

## [0.2.1] - 2026-08-22
<!-- cmru: generated -->
<!-- cmru: source-end=f330d9a17940787edcfcec957c1268f765aa5af6 -->

### Changed
- run-gate: estate-wide adoption as SSOT test definition (CIU-40 adoption half) (4c6eb2b6)
- topos: make subprocess tests project-root portable (5681b42b)
- handoff(nyxloom): P90 -- extract the testing mechanics into a standalone library (b22fe999)
- topos: add backlog entry (77e0b63c)

### Testing
- test(topos): py-compile argv meta-tests parse run-gate.toml [lanes.py-compile] — the SSOT since adoption; nyxloom.toml holds only the pointer (b2f01c0c)
