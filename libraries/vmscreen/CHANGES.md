# Changelog

All notable changes to vmscreen are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. The hand-written section carries what a consumer must know.

## [Unreleased]

<!-- cmru: release history -->

## [0.1.0] - 2026-11-18
<!-- cmru: generated -->
<!-- cmru: source-end=43ee2f73bb6766aa8b5583a19c6337232ab926ae -->

### Added
- feat(vmscreen,vm-lab): register both projects for release (cmru, assay, run-gate lanes, wheel lane) (caf11e2c6)
- feat(vmscreen): --qemu-dump-dir for QEMU that sees the dump directory under another path (VML-03a) (40fa92d4c)
- feat(vmscreen): library+CLI for exact QEMU console text (VGA text buffer, serial) and OCR ground-truth pairs (3125187e7)

### Fixed
- fix(vmscreen): an empty VMSCREEN_QEMU_DUMP_DIR counts as unset (7fee4cdc9)
- fix(vmscreen): --qemu-dump-dir fix round 1 (VML-03a review) (2b4862a26)
- fix(vmscreen): third-review fixes (uncertain cursor cells are partial, tail-window marks, empty serial falls through, resume ordering and pending signals, strict marks, cell budget, link/rollback handling) (cf51bbca4)
- fix(vmscreen): re-review fixes F1-F6 (resume retries reconnects, unverified VGA falls through, cursor tolerance, bounded serial rendering, atomic force/exclusive commit, verified serial marks) (f588b7017)
- fix(vmscreen): review fixes H1 M1-M5 L1-L8 (never leave the guest paused, verified exact text, atomic ocr-pair outputs, serial marks) (7fe93bf0d)

### Changed
- vm-lab: repoint every reference to the old scripts/vm-lab location; Pillow as the optional record extra (0f3c54385)

### Documentation
- docs(vm-lab,vmscreen): close VML-BL-01/02 and VMS-BL-01/03 with evidence, CHANGES [Unreleased] (235c1b907)
- docs(debian-rollback, vmscreen): VML-02 merged; vmscreen found, Y3/Y5 plan corrected (df36bafe2)

<!-- cmru: pending release notes begin -->
### Added
- **Release registration (VMS-BL-01):** `cmru.toml` (tag `vmscreen-v*`, wheel only to GitHub Releases, notes in `VMSCREEN_RELEASE_NOTES`), `assay.toml` (judged lane `vmscreen`, coverage floor 95 %, measured 97.4 %) and an entry in `cmru.orchestration.toml` that releases vmscreen after cli-extended and before vm-lab. `run-gate.toml` gains `r2` (assay mutation lane) and `gate`, and `r0-r1` now enforces the coverage floor. The real-QEMU smoke test (VMS-BL-04) has still not been run live.

### Changed
- The key-table duplication with vm-lab is closed as decided (VMS-BL-03): two copies on purpose, the rule lives in `vm-lab/docs/DESIGN-GUIDE.md`; the README points there.
<!-- cmru: pending release notes end -->
