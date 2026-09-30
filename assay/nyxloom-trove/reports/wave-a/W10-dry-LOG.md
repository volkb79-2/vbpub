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

## Step 1 — characterization tests (in progress; checkpoint 2)

All run green against UNCHANGED source and are never edited afterwards. Commits (branch `wave-a-w10-dry`):
- `0ec04d82` `tests/core/test_dataclass_contract.py` + `tests/fixtures/dataclass-contract.json` (81 classes: 69 frozen+kw_only, 12 frozen positional; the brief said 68, one more exists at base). Fixture generator (P1): `cd assay && PYTHONPATH=src:tests python tests/core/test_dataclass_contract.py > tests/fixtures/dataclass-contract.json`.
- `08b3f996` `test_w10_characterization_verdict_wire.py` (126).
- `8e016971` `test_w10_characterization_scalars.py` (150): isolation, mutation site/target, liveness pid/finite, liveness_resources.
- `cc7e6760` `test_w10_characterization_config.py` (110): config schema_version, infrastructure names/declarations, evidence dir, `_as_float`, `fail_under` percentage (judge + ingested), mutation jobs/max_mutants/shards/budget, canary budget.
- `76907bc8` `test_w10_characterization_boundaries.py` (61): attestation path and dir, provenance wheel digest, adjudication schema_version, shard merge lane/commit, runner derived fact; plus the mutation-report parser tests (49).
- `dd2e17ec` `test_w10_characterization_verdict_policy.py` (198): MutationExecution digest/node ids, MutantOutcome identity digests, Mutation ints/candidate ids/derived budget, JudgmentR1/R2 fail_under, native R2 jobs/max_mutants/derived budget/liveness, Claim detail_dropped_bytes, Verdict identity strings and dropped-byte counters.
- `44c67854` + `2320fcbf` go_stmtpos/StatementSpan, istanbul and coverage.py parsers, vitest count. The layout test (`test_every_test_file_is_in_its_component_folder`) routes by file NAME, so files were renamed: `tests/parsers/coverage/test_coverage_w10_characterization_parsers.py`, `tests/parsers/result_reports/test_result_reports_w10_characterization_vitest_json.py`, `tests/adapters/go/test_w10_characterization_go_stmtpos.py`; the mutation-report file lives in `tests/core/test_w10_characterization_mutation_report.py`.
- `18c2dfaf` `test_w10_characterization_runner_mutation_liveness.py` (139): LaneDeadline.start/tightened, execute_plan timeout, collect_mutation_sites limit, run_mutation jobs/max_mutants/budget, liveness slowest-test and event-gap readers (incl. the new site `liveness.py:1071`). Every finite-positive site also pins `10**400` -> `OverflowError`.

Observations recorded while pinning (behaviour is characterized as-is, nothing changed):
- On a native or ingested `JudgmentR2`, `None` for `jobs`/`max_mutants`/`fail_under` is reported as an ABSENT field ("records producer ... but is missing [...]") before the type guard runs, so the type-guard tests skip `None` for those.
- `istanbul._arm_count`/`_statement_count` have a second, negative-count refusal after the strict-int guard (its own message, pinned).

## CD52 (amended): `_no_ambient_color` autouse fixture (done)

Same fixture, same name and body, in `tests/conftest.py`, `gate/tests/conftest.py`, `analysis/tests/conftest.py` (the last gained `import pytest`). No conflicting autouse fixture; no existing test asserts coloured output. Shell had `FORCE_COLOR=3`.
- Oracles pass with the fixture: `tests/core/test_b105_source_coverage_controls.py::test_cli_module_guard_runs_in_process_under_main_name` and `gate/tests/test_dependency_purity.py::test_the_installed_analyze_command_runs_from_the_one_wheel`.
- Controlled break 1 (`autouse=False` in `tests/conftest.py`): the guard test FAILED under `FORCE_COLOR=3`. Reverted.
- Controlled break 2 (`autouse=False` in `gate/tests/conftest.py`): the wheel test FAILED. Reverted. (`analysis/tests` has no colour-sensitive test; the fixture is there per CD52 amended.)
- `CARVER-DECISIONS.md` gained the `CD52 (amended)` row.

## BLOCKED / QUESTIONS

- **Q1 (controller answered, binding):** row-1-shaped sites (`not isinstance(x, str) or not x`) that `idioms.py` did NOT report stay UNCHANGED in W10. Follow-up list for a later wave: `attestation.py` 141 and 145 (`AttestationRecord` producer/attested_commit), `mutation.py` 387 and 479, `verdict.py` 1301, 1437, 1484 (`MutationWitnessReceipt.node_id`), 1694 (`MutantOutcome.description`), 3727 (`Claim._check_detail`). `liveness.py:1071` (`baseline_event_gaps`, row 5) IS in scope.
