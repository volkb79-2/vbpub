# Assay 10.2.0 CW8 / IO2 continuation

Work is on branch `assay-cw8`, based on `main`. The implementation commit is
`ed0872495` (`feat(assay): add CoW workspaces and raw fsync`); the follow-up on
this branch repairs the sparse release/test build inputs discovered by the
artifact acceptance checks. Continue from the current branch HEAD.

## Completed

- CW8 adds workspace detection and the Level A CoW snapshot path. It lazily
  uses the same-repository `cowtree` package, accepts only btrfs reflinks, and
  retains the existing copy path with cowtree's single INFO notice when the
  optional area is absent. Refusals use the existing `ERROR/EXEC_FAILED`
  behavior. Plain and CoW R0/R1 runs are checked for identical result buckets.
- IO2 routes state, verdict and CLI durability writes through
  `assay._durable.fsync`, which invokes the raw Linux fsync syscall on the
  supported architectures and handles the preload fallback/refusal cases.
- `_cow_workspace.py`, `_durable.py` and `workspace.py` are in all three Assay
  target inventories. Verdict schema remains v16; waiver windows are unchanged.
- Release and tester build clones include `libraries/cowtree/` alongside
  `assay/`; B189 records the sparse-clone omission found by the wheel/zipapp
  checks and closed here.
- `assay/CHANGES.md` has the `[Unreleased]` entries. No CW8/IO2 backlog debt
  remains open.

## Focused evidence already collected

- Workspace/fake-cowtree and snapshot parity selection: 25 passed; both new
  workspace modules reached 100% line and branch coverage.
- IO2 durable, state, verdict, CLI and monkeypatch regression selection: 304
  passed, 1 skipped; `_durable` reached 100% line and branch coverage. The
  skip is the live `libeatmydata` check because that preload library is not
  installed in this environment.
- Isolation and B105 boundary selection: 119 passed.
- Runner R0/R1/R2/R3 and cleanup/budget selections: 85 passed.
- Sparse clone and real wheel/zipapp package acceptance: 3 passed.

These were focused `pytest` invocations as requested; `run-gate` and Docker
were not used. No live btrfs mount acceptance was available, so the storage
behavior is covered by the cowtree adapter and snapshot parity tests rather
than a host-specific mount run.

## Scope boundary

CW9, RS1/RS2, PR1 and WP8 remain untouched. No verdict-schema change or waiver
window change is part of this work. The CW8/IO2 branch is ready for the
requested branch-only push; registered gate acceptance remains outside this
task's explicit no-`run-gate` boundary.
