# W10 LOG — DRY consolidation of repeated judge rules (B129)

Implementer: Sonnet (fresh session). Branch `wave-a-w10-dry`, base `be7f02e673b4e04c77448eff103f3e11e1f7eeca` (CD52: landing `ce86fed4` + W9 judge commit `814494ac`).
CD44/CD52: no `run-gate.py`, no container, no preflight. The session ends with `READY-FOR-GATE`.
Inventories live outside the repo, under the session scratchpad `.../scratchpad/w10/{before,after}/`.

## Step 0 — base inventory (done)

Ran at base SHA, `src/assay` as the root, all under `nice -n 19 ionice -c3`:
`candidates.py`, `dry_estimate_strict.py`, `idioms.py --json`, `dupes.py` at T2 and at T1 (T1 = the `dry_estimate_strict.py` trick, `Normalizer._rn` = identity, via a scratch wrapper).

Base counts (O6 "before"):
- total candidates 3733 = bool-const-flip 493, boolop-swap 973, compare-swap 2128, falsy-swap 139 (47 files with candidates).
- `dry_estimate_strict` (T1): G 109, E 92, W 59, F 3, D 147; union removable 385 (10.3%), without D 238; by file: verdict 107, runner 35, mutation 23, config 22, git 18, isolation 14, sql 12, liveness_resources 12, cli 11, verify 11, coverage_istanbul_json 10, liveness 8, coverage model 8, javascript 8; 13 deliberate cross-boundary.
- `dupes` T1: F 14 clusters / 7 redundant, W 67 clusters / union 59, E(>=2) 29 clusters / 110, E(>=1) 546, W|E 158.
- `idioms`: 117 guard-group occurrences (173 candidates, 34 families); fixed idioms sha256-hex 8, aware-datetime 3.

Site matching (by file and enclosing function; the brief's line numbers are from `5bbd916e`, HEAD has shifted them):
- Every row of I2 matched by (file, function). Shifts only: mutation 351/709/2348/2352/491/2370 -> 355/713/2398/2402/495/2420; runner 243/282/254/865/1157 -> 245/284/256/867/1159; liveness 708 -> 767 (`_valid_pid`); liveness_resources 133 unchanged; provenance 180/186 unchanged.
- **New site of a listed family (in scope)**: `liveness.py:1071` in `baseline_event_gaps` (`isinstance(stamp, bool) or not isinstance(stamp, (int, float))` -> `not is_real(stamp)`, row 5). The brief listed `liveness 798,905`; HEAD has `872` (`baseline_slowest_test_s`), `902` (`_finite_number`) and `1071`.
- No vanished site found in I2.
- The pinned `pragma: no cover` lines at base: config 94, liveness 94, mutation 148, mutation_witness 14, git 378 and 1344 (the fixture already carries these; the brief's git 376/1342 are stale).

(I3, I4, verify-side sites are matched at the steps that touch them; any mismatch will be recorded as BLOCKED below.)

## BLOCKED / QUESTIONS

(none yet)
