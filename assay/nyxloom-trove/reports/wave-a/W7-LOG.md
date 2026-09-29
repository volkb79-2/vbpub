# W7 LOG: shallow snapshot for both B105 lanes (B128)

Branch `wave-a-w7-shallow`, project dir `assay/`. Base `08d059ad` (W4 head `21a7b1f0` merged with landing).

## Commits

| Commit | Hash | Content |
|---|---|---|
| Step 0 | `a4a0010a` | `tests/core/test_import_contracts.py`: root layout check allows `__init__`-free `*_support.py` helpers (CD23 amended; W4+W6 integration) |
| **FULL** | `c1c0e817` | `tests/core/test_snapshot_history_shallow.py`; lanes still `snapshot_history = "full"` |
| **SHALLOW** | `a3a6d733` | `assay.toml` both B105 lanes to `"shallow"` plus rationale comment; `gate/tests/test_self_lane.py` pin to `"shallow"` |
| Docs | `157421f2` | `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, `CHANGES.md` `[Unreleased]`, `4-backlog.md` B128 status |
| LOG | (this file's commit) | LOG only, no code |

## Oracles

| Oracle | Positive result | Deliberate break | Break result |
|---|---|---|---|
| Layout check (step 0) | `test_the_tests_root_holds_only_conftest_and_fixtures` passes | plant `tests/test_x_support.py` | red; plant removed |
| Layout check (step 0) | same | plant `tests/helper.py` | red; plant removed |
| Shallow negative test | `test_shallow_snapshot_has_no_parent_of_head` passes (also asserts the source repo has `HEAD~1` and the snapshot's `HEAD` equals the judged commit) | force `HISTORY = "full"` in the test | red (`AssertionError`, `HEAD~1` resolved); reverted |
| Lane pin | `gate/tests/test_self_lane.py` 25 passed | flip `[lanes.self-qualification-preflight.isolation]` back to `"full"` | red (`test_preflight_measures_the_same_complete_source_inventory_before_r2`, identical-isolation check); reverted |
| Lane pin | same | flip both lanes back to `"full"` | red (that test plus `test_self_qualification_is_full_source_r0_through_r3`); reverted |

Focused set at FULL and at SHALLOW: `gate/tests/test_self_lane.py`, `tests/core/test_snapshot_history_shallow.py`, `tests/core/test_isolation*.py`, `tests/core/test_import_contracts.py`: 182 passed each time.

`git status --short --ignored assay` before the FULL commit: the new test showed `??`, no `!!`.

## Collect counts

- Before (base + step 0): `tests` 5437 (5438 minus the one new test).
- After (FULL, SHALLOW, docs): `tests` 5438; `gate/tests` 350.
- One full serial run `tests gate/tests` at the docs commit: 5776 passed, 12 skipped, 1 failed. The failure is the known environmental `tests/core/test_git_boundary.py::test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused` (stray `/tmp/.git`, B134). Not W7's.

## For the controller's gate run

`FULL=c1c0e817 SHALLOW=a3a6d733`

Run `self-qualification-preflight` at each commit and compare collected, passed and skipped counts. They must be identical. Any test that is newly skipped or failed at SHALLOW is a finding, not a pass. (The only difference between the two commits is the two `snapshot_history` values, the comment and the pin.)

No `run-gate.py` was run and no container was started.

## Deviations

- README was not edited: it describes only the generic shallow default and never the B105 lanes' snapshot mode.
- Step 0's test text was applied exactly as specified.

## BLOCKED

None.

READY-FOR-GATE a3a6d733
