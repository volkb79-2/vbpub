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

Checkpoint 3 additions (all green against unchanged source):
- `6abbd8c4` `test_w10_characterization_verdict_policy_presence.py` (25): I3 producer side, exact texts for R1..R4 orphan AND missing (claim absent / payload-free / policy absent from a judgment / no judgment at all), R1's BRANCH_UNAVAILABLE/TARGET_NOT_MEASURED and R2's MUTATION_UNSUPPORTED "attempted" terminals plus a non-attempting reason (EMPTY_COVERAGE), and runner `assemble_verdict`'s R1 twin (exact `AssayError` text, outcome ERROR, reason BAD_LANE_CONFIG; runner `claim_for`/`claim_carries` site). Verdict-side `_check_operator_language_agrees` / `_check_ingested_operators_only` (`claim_for` sites 4990/5019) are cited, not re-pinned: their raises are exercised through `Verdict` reconstruction by `test_verify_layer_independence.py` (`test_model_clause_operator_language_the_PER_OUTCOME_half_alone`, the `judgment.r2.operators names` test) and `test_verify_ingested_r2.py::test_a_native_operator_on_an_ingested_document_is_caught`; their silent-return branch (no R2 claim / `mutation is None`) is exercised by `test_verdict_interval_and_unsupported.py::test_the_unsupported_terminal_still_records_the_policy_it_applied`. Note: an empty `Judgment` (no rN) and R3/R4-only judgments carrying a base are refused earlier by their own constructors, so the "policy absent from a judgment" cases use a judgment holding another tier.
- `93afecbd` `test_w10_characterization_verify_policy_presence.py` (31): I3 verify side, `verify._check_judgment_matches_claims` R1/R2/R3 orphan and missing, exact `failures` lists; raw operands (key presence counts as judged even for a `None` payload; non-dict claim entries skipped; first claim of a rigor wins; non-list `claims` short-circuits); the other `_raw_claim` sites (915, 1043, 1527, 1625) are the identical lookup and are covered by these operand pins plus their functions' existing suites; `_check_rN_rederivation` lookup misses (the `_claim_of` sites) pinned with a claim-less verdict.
- `test_w10_characterization_verify_scalars.py` (62) and `test_w10_characterization_verify_mutation_ints.py` (18, partial): verify's `_is_int`/`_is_text` sites: `_raw_mutant_identity` (392), `_check_snapshot_policy` link paths and omissions (478, 511; exact messages), `_is_bounded_node_id` (1826), `_check_mutation_payload_shapes` (1559, 1577, 1579, 1594). STILL TO PIN in the mutation_ints file: verify 1186 (`_check_ingested_r2_agrees_with_its_payload`, `total`/`candidate_count` strict ints) and 1663 (`_check_b106_mutation_provenance` sentinel: `candidate_count`/`max_mutants` strict ints, `candidate_count == max_mutants + 1`, reason `MUTANT_LIMIT_EXCEEDED`).
- I4 sites E1-E10 are NOT yet pinned (next checkpoint).
- Editing-rule note: one no-op `cat >> <test file> <<EOF` with an empty heredoc was issued against a repo file by mistake (it changed nothing; `git diff` showed no effect). All real edits used Edit/Write.

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
