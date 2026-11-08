# Changelog — Augeaswrap

## [Unreleased]

- Add the guarded applier core with strict TOML requests, `file` and `check`
  resources, plan/apply/revert/journal commands, pre-image journals, stale-plan
  checks, and explicit revert.
- Move the shared resource protocol and value types into `augeaswrap`; resource
  work for HC-1b-u/-s builds against that frozen contract.
- Tighten protocol input checks for NUL-bearing writers and non-string change
  actions without changing resource method signatures or dataclass fields.
- Keep the standalone endpoint publisher and its paths; label the generated
  environment block with `augeaswrap.endpoints`.
- Emit one JSON document per CLI invocation, with plan and result together for
  apply and revert.
- Mark an unobservable failed-commit post-image in the journal and refuse an
  unsafe explicit revert.
- Preserve transaction allocation order for same-second and clock-rollback
  cases; bind each journal to its selected root.
- Record address-bound post-actions and replay recorded actions during revert.

<!-- cmru: release history -->

## [0.1.0] - 2026-11-08
<!-- cmru: generated -->
<!-- cmru: source-end=f454c38d8dd64f67eb9caac925961451819ba2c7 -->

### Added
- feat(augeaswrap): add standalone endpoint publisher (8754cacb0)

### Fixed
- fix(augeaswrap): show surface-drift findings in test_docs, trim copied cmru/assay comments, note frozen marker text in SPEC (53e1c55f9)

### Changed
- Merge origin/main into aw-0-20261108 (cgprofile rename, cgprofiled writer text ported to augeaswrap) (0518a3bb8)

<!-- cmru: pending release notes begin -->
- Add the standalone `augeaswrap` project and move the host endpoint record
  API and CLI from cli-extended. The endpoint paths and formats are unchanged.
<!-- cmru: pending release notes end -->
