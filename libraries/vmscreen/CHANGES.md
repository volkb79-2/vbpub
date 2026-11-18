# Changelog

All notable changes to vmscreen are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. The hand-written section carries what a consumer must know.

## [Unreleased]

### Added
- **Release registration (VMS-BL-01):** `cmru.toml` (tag `vmscreen-v*`, wheel only to GitHub Releases, notes in `VMSCREEN_RELEASE_NOTES`), `assay.toml` (judged lane `vmscreen`, coverage floor 95 %, measured 97.4 %) and an entry in `cmru.orchestration.toml` that releases vmscreen after cli-extended and before vm-lab. `run-gate.toml` gains `r2` (assay mutation lane) and `gate`, and `r0-r1` now enforces the coverage floor. The real-QEMU smoke test (VMS-BL-04) has still not been run live.

### Changed
- The key-table duplication with vm-lab is closed as decided (VMS-BL-03): two copies on purpose, the rule lives in `vm-lab/docs/DESIGN-GUIDE.md`; the README points there.
