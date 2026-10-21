# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

<!-- cmru: release history -->

## [0.4.2] - 2026-10-21
<!-- cmru: generated -->
<!-- cmru: source-end=8f7425bba9a407c4d2522d0a2c429dd95b93585b -->

### Added
- feat(run-gate): make wheel SemVer the only supported interface (9a0e192ab)

### Changed
- DOCS-FOLD-01 review fixes F-01..F-08: memory.low claim corrected, boost chain labelled, one canonical home per fact, 125 s, stale rows, README:130, MDT-022 citations (fa215e600)
- DOCS-FOLD-01: fold operator-approved facts A-D into the docs (c5b79f9f2)
- INT-01 gate fixes: overlap tests, bootstrap oracle, topos lane, mattermost drift (fbe8720e5)
- Merge mdt-home-persistence-20261018 (daf47a2e9) (INT-01 step 4) (5e2dff706)
- topos: a present systemctl that cannot reach systemd is unavailable too (W4) (44f56b06e)
- topos: 'systemctl not available' is a state of its own, not 'unit absent' (V3) (697103a5f)
- tester-unified: no systemctl stub, node from the official image (D-001, D-004), host lanes off the root environment (D-002), R5, R7 (c3bd7f916)
- topos: a missing systemctl degrades instead of crashing the collector (D-001, TP-SYSTEMCTL) (78e4200d3)
- tester-base/tester-unified: explicit systemctl stub (D-001) and pycache guard (D-002) found by the T8 skip-diff; topos backlog TP-SYSTEMCTL (262cb9c30)

## [0.4.1] - 2026-09-26
<!-- cmru: generated -->
<!-- cmru: source-end=d22feaab860607938505380ef9ddacb521307e66 -->

### Testing
- test(topos): synchronize broker history boundary test (8823bfea)

## [0.4.0] - 2026-09-19
<!-- cmru: generated -->
<!-- cmru: source-end=a2def16c934a888245a0d088ca9e44ddcf31792e -->

### Added
- feat: add estate cli version compatibility (05f373a4)

### Changed
- cli: universalize vbpub parser diagnostics (aa0e69fa)
- chore: land run-gate root and dev-gates migration (41c1cafb)

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
