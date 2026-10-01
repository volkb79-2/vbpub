# B110 cold-witness implementation map (assay @ db85f747)

Root: `/workspaces/vbpub/.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy/assay/`. All paths below are relative to it. This was read-only: no files changed and no tests or gates ran.

## 0. Traps an implementer will hit

1. **Every current B106 witness breaks if new receipt keys go in only one place.** `witness_from_receipt` rejects any receipt whose keys differ from `_INTERNAL_RECEIPT_KEYS` (`src/assay/mutation_witness.py:232`, set defined at `:308-321`). A new key must be added to both the plugin's `_write` payload (`:402-413`) and `_INTERNAL_RECEIPT_KEYS` together. The unit helper `_receipt()` in `tests/test_mutation_witness_unit.py:39-53` builds the exact old key set and also needs the new keys.
2. **Witness injection puts the coverage flags back.** `inject_witness_plugin` recomputes `argv_effective = plan.argv_declared + appended` (`mutation_witness.py:186`). If the R2 plan keeps the original `argv_declared` and only rewrites `argv_effective`, the `--cov*` options return on every candidate. The transformed argv has to live in `argv_declared` of the R2-only `CommandPlan`, which also keeps the `CommandPlan` invariant documented at `runner.py:690`.
3. **`-p no:pytest_cov` must not trip the argv-append consent check.** `execute_plan` refuses appended argv unless it is excluded via `cli_argv_appended` (`runner.py:1168-1182`). Follow the liveness pattern (`liveness.py:486-494`): add `-p no:pytest_cov` to `argv_appended` and freeze `cli_argv_appended` to the pre-transform value. Otherwise `allow_argv_append = false` (the B105 lane, `assay.toml:97`) returns `EXEC_FAILED`.
4. **Two mechanical traps in `JudgmentR2`'s producer fork:**
   - Native requiredness is a slice: `required = self._NATIVE_ONLY_FIELDS[:-3]` (`verdict.py:2855`). New fields change what the slice selects.
   - The ingested branch forbids any native-only field that `is not None` (`:2859`). A `cold_witness_kills: bool = False` default would therefore refuse every ingested judgment. The dataclass default must be `None`, or the fork must be special-cased.
5. **B105 cannot capture witnesses at all today.** Its argv contains `--override-ini=pythonpath=src` (`assay.toml:87`), and `supports_sequential_pytest` rejects any `-o`/`--override-ini` (`mutation_witness.py:60-63`). pytest-cov is also a problem: the installed version is 7.1.0 (checked). Its `pytest_runtestloop` is a `wrapper=True` hook implementation. That makes the plugin's `standard_loop` check (`:440-442`) false, so the receipt says `unsupported: true`. Also, the lane's `tests/conftest.py::pytest_sessionfinish` (`tests/conftest.py:59`) fails the `session_finish` check (`mutation_witness.py:449`). Both refusals must be narrowed.
6. **The B105 coverage floor applies to the new code.** The self-qualification lane requires 100% line and branch coverage of every `src/assay/*.py`. `targets` must equal the discovered files (`tests/test_self_lane.py:128-135`), and raw excluded lines must equal `tests/fixtures/b105-coverage-exclusions.json` exactly (`tests/conftest.py:107-110`, A-463). So:
   - A new module must be added to both target lists in `assay.toml`: self-qualification (`:108-165`) and preflight (`:218-275`).
   - New `pragma: no cover` lines need that fixture updated.
   - The plugin source is a string run in a subprocess (`mutation_witness.py:324-493`), so it is not measured. Its host-side projection helpers are.
7. **Do not bump `MUTATION_STATE_SCHEMA_VERSION`** (`mutation.py:220`). It is shared with the shard-summary document (`:1555`) and pinned by `tests/test_mutation_judge_identity.py:711`. Invalidate stale records through `judge_sha256` instead (section 4).
8. **The design guide forbids derived argv today.** DESIGN-GUIDE §7 says flags "are never derived by assay" (`docs/DESIGN-GUIDE.md:1871`). The R2 command transform contradicts this, so the rule needs an explicit, documented exception.

---

## 1. verdict.py

**Schema version**
- Constant: `VERDICT_SCHEMA_VERSION = 13` at `src/assay/verdict.py:350`. It is preceded by one history paragraph per version; the v12→13 paragraph is `:337-349`. Add a matching "Bumped 13 → 14 (B110)" paragraph there.
- `SCHEMA_RESOURCE = "schemas/verdict.schema.json"` at `:426`. `schema_text()`/`load_schema()` are at `:556-569`.
- `Verdict.schema_version` defaults to the constant (`:4432`); `__post_init__` refuses any other value (`:4448-4452`). It is emitted in `to_dict` (`:5395`).

**`MutationWitnessReceipt`** (`:1473-1509`)
- Fields: `node_id`, `when`, `outcome`, `session_exit_status`, `process_exit_status`.
- Validation: `node_id` non-empty, valid UTF-8, at most 4096 bytes; `when == "call"`; `outcome == "failed"`; both exit fields must be `int` (bool rejected) and equal 1 (`:1482-1499`).
- This is already exactly the `witness-cold` receipt shape; reuse it unchanged.

**`MutationExecution`** (`:1511-1583`)
- Fields: `mode`, `witness`, `prior_verdict_sha256`, `prior_node_id`, `current_node_id` (`:1515-1519`).
- Closed mode check: `if self.mode not in ("full", "witness-prefix")` (`:1522-1525`). The error message must keep the substring "mode must be", which `tests/test_b106_reuse_and_witness.py:340` matches.
- `full`: the `prior_*` fields must be `None`; a witness is optional (`:1530-1540`).
- `witness-prefix`: witness required, SHA-256 digest, bounded node IDs, `prior == current == witness.node_id` (`:1541-1571`).
- `to_dict` emits the `prior_*` trio only for `witness-prefix` (`:1573-1583`).
- **Where `witness-cold` goes:**
  - Add the literal at `:1522`.
  - Add a branch after the `full` return (`:1540`) that requires a witness, forbids all `prior_*` fields, and validates any new cold-only fields (started-prefix length, failed-call index; see §11 A1).
  - Extend `to_dict` at `:1576` for those fields.
  - Mode literals are hard-coded in five places and there is no constant: `verdict.py:1522`, `:1573`, `:1979`; `verify.py:1745`/`:1758`; `mutation.py:1463-1476`; schema `:1300-1322`.

**`MutantOutcome`** (`:1586-1775`)
- B106 fields `candidate_id`, `source_sha256`, `mutated_file_sha256`, `execution` (`:1647-1650`) are all-or-none (`:1705-1740`).
- `to_dict` emits `execution` at `:1768-1773`.

**`Mutation`** (`:1804-2010`)
- `candidate_ids` (`:1868`) is the wire inventory. `budget_per_candidate_derived_s` (`:1869-1884`) is an **internal carrier, not emitted on the wire**: the runner reads it back into `judgment.r2`. That is the precedent to follow if per-candidate evidence is carried from `run_mutation` to `_build_judgment_r2`.
- Non-killed buckets are refused if they carry a witness or `witness-prefix` (`:1978-1983`). Add `witness-cold` to this condition.

**`JudgmentR2`** (`:2523-3111`)
- Fields: `producer` `:2544`, `producer_tool`, `survived_uncovered` `:2561`, `discarded` `:2592`, `fail_under` `:2610`, `lines_without_candidates` `:2614`, `jobs` `:2619`, `max_mutants` `:2627`, `operators` `:2637`, `kill_attribution` `:2648`, `kill_signal_artifact` `:2654`, `equivalence_artifact` `:2661`, `mode` `:2671`, `targets` `:2678`, `shard_index`/`shard_count` `:2681-2683`, `budget_per_candidate_derived_s` `:2695`, `liveness` `:2706`.
- `_NATIVE_ONLY_FIELDS` (`:2811-2823`) and `_INGESTED_ONLY_FIELDS` (`:2826-2837`); `_check_producer_fork` (`:2843-2881`; see trap 4).
- `_check_native_policy` (`:2883-2971`). Its `liveness` mapping validation (`:2934-2971`: exact key set, typed values) is the nearest pattern for nested v14 objects (transform, argv triple, manifests, fingerprints).
- `to_dict` (`:3061-3111`) emits native policy at `:3075-3078`. Add the new always-explicit `cold_witness_kills` beside `jobs`/`max_mutants`/`operators`. The backlog names this field: `nyxloom-trove/4-backlog.md:11101-11104`.

**Cross-object check location.** `Verdict._check_judgment_matches_claims` R2 block (`:4800-4861`). Add a `_check_cold_witness_policy(r2_claim.mutation, judgment_r2)` call after `_check_discarded_disposition` (`:4861`). It should:
- refuse `witness-cold` unless `cold_witness_kills is True`;
- require that the evidence IDs equal the bucket candidate IDs;
- require transform and manifest coherence.

**No `from_dict` in verdict.py.** Parsing is done only by `verify.py`'s `_reconstruct_*` functions (section 2) and by `mutation._execution_from_state_record` (section 4).

**Verdict JSON schema: `src/assay/schemas/verdict.schema.json`.** It is the only verdict schema; the three `analysis-*.schema.json` files are separate and each pins `const: 1`.
- `$id` `"urn:assay:schema:verdict:13"` at line 3; `schema_version.const: 13` at `:20-24`.
- `$defs.mutation_candidate_ids` `:1277-1286`; `$defs.mutation_witness_receipt` `:1287-1298` (reuse as-is).
- `$defs.mutation_execution` `:1300-1323` is a two-branch `oneOf`. Add a third branch: `{"required":["mode","witness"], "properties":{"mode":{"const":"witness-cold"}, "witness":{$ref}, …cold fields}, "additionalProperties":false}`.
- `$defs.mutant_outcome.execution` `:1380`.
- Per-bucket rules (`:1407-1480`) do **not** enforce killed-only witnesses. The model and verifier own that; keep the same layering.
- `$defs.judgment_r2` starts at `:1631`. `liveness` object is at `:1730-1748`. The native/ingested fork (`:1862-1909`) needs the new cold fields added to the ingested `not required` list (`:1897-1904`) and, if they are explicit, to the native `required` list (`:1876-1880`).

**How the version is pinned in tests and docs:** see §3 and §9.

---

## 2. verify.py

- **Version gate:** `verify_document` (`src/assay/verify.py:3012-3091`) returns the single version diagnostic at `:3027-3042`. Tests pin that text with the literal 13 (`test_verdict_conformance.py:1323`) and through the gate scripts.
- **Check order:** `:3060-3075`. `_check_b106_mutation_provenance` runs at `:3072`. Reconstruction, then `_check_r2_rederivation`, run at `:3077-3089`.
- **`_check_b106_mutation_provenance`** (`:1619-1738`):
  - It already reads `policy = judgment.r2` (`:1632`) and branches on producer (`:1633-1653`).
  - Handles the limit sentinel (`:1655-1676`).
  - Inventory checks (`:1678-1692`); per-entry identity checks (`:1694-1729`).
  - Calls `_check_b106_execution(bucket, entry.get("execution"), failures)` (`:1730`).
  - Inventory equality with bucket IDs (`:1732-1737`).
  - **v14 additions here:**
    - read `policy.get("cold_witness_kills")`;
    - check native requiredness and type (`type(x) is bool`);
    - check that the per-candidate evidence IDs equal the outcome IDs, reusing the `:1732-1737` pattern;
    - pass the cold policy into `_check_b106_execution`.
- **`_check_b106_execution`** (`:1741-1790`, the raw execution-record checks):
  - `full` (`:1746-1757`): only `{mode, witness}`; witness only on killed.
  - Unknown mode refused at `:1758-1760` ("unknown execution mode"). **This is where `witness-cold` must be accepted:** insert a branch before `:1758` that:
    - requires exact keys `{"mode","witness"}` plus the cold fields;
    - refuses when `bucket != "killed"`;
    - refuses when the cold policy is not `True` (the backlog acceptance at `4-backlog.md:11199-11201` says "rejects the mode when cold policy is false or absent");
    - refuses any `prior_*` key;
    - calls `_check_b106_receipt`;
    - checks `failed_call_index + 1 == started_prefix_length` if those live here.
  - `witness-prefix` (`:1761-1790`) is unchanged.
  - Signature: tests call `raw_verify._check_b106_execution(bucket, execution, failures)` positionally (`tests/test_b106_reuse_and_witness.py:143-202`). Add the cold policy as a keyword-only parameter with a default.
- **`_check_b106_receipt`** (`:1793-1814`) is reusable unchanged. It already rejects bool exit fields (`type(...) is not int`, `:1809-1811`).
- **Reconstruction (must register every new wire field in the same commit, per the A-323 lesson; `_reject_unknown_keys` at `:1868-1871` is top-level only):**
  - `_reconstruct_judgment_r2` (`:2011-2082`): add `cold_witness_kills=raw.get(...)` and the other v14 fields beside `liveness=raw.get("liveness")` (`:2079`).
  - `_reconstruct_mutant_outcome` (`:2163-2196`): add any new cold execution fields to the `MutationExecution(...)` call (`:2173-2179`). Nested extra keys are **not** caught by `_reject_unknown_keys`; only the raw `_check_b106_execution` set checks catch them.
- **R2 re-derivation** (`_check_r2_rederivation`, `:2592-2745`) re-judges status from bucket membership through `judge_mutation`. It is independent of execution mode, so a cold kill in `killed` needs **no change** there.

---

## 3. How the v12→v13 cut was done (B106, A-461): a checklist for v14

The merge `e5e9b95c` contains `d43167c6` (feature, the complete v13 cut), then `8aa1d61d`, `222c8cb8` and `c28fbbb9` (gate/test repairs). 100 files, +7,433/−342.

**Source**
- `src/assay/verdict.py` (+253): constant, history paragraph, `MutationWitnessReceipt`, `MutationExecution`, `MutantOutcome` B106 fields, `Mutation` checks.
- `src/assay/schemas/verdict.schema.json` (+55): `$id`, const, defs.
- `src/assay/verify.py` (+241).
- `src/assay/mutation.py` (+301), `src/assay/mutation_witness.py` (new, 493), `src/assay/reuse.py` (new, 199), `src/assay/candidate_identity.py` (new), `src/assay/runner.py` (+93), `src/assay/cli.py` (+131).

**Golden fixtures to regenerate**
- All 50 files in `tests/fixtures/verdicts/*.json` pin `"schema_version": 13`.
- These carry native `judgment.r2` and need the new v14 fields: `inconclusive.json`, `r2_budget_exceeded_candidate_hung.json`, `r2_budget_exceeded_lane_timeout.json`, `r2_budget_exceeded_mutant_limit_exceeded.json`, `r2_error_exec_failed_mutant_crashed.json`, `r2_fail_mutants_survived.json`, `r2_inconclusive_all_mutants_equivalent.json`, `r2_inconclusive_mutation_unsupported.json` (policy with no payload), `r2_inconclusive_no_mutants.json`, `r2_pass.json`, `r2_pass_with_judgment.json`.
- They are validated by `tests/test_verdict_conformance.py:389-413`: every fixture must pass both the schema and `verify`.

**Carve assets (nyxloom-trove)**
- Precedent: B106 created `carve-assets/W9/` containing `verdict.schema.v13.json` (a byte copy), `expected/p25-{pass,missing}-v13-template.json`, `test_acceptance_v13.py` and `MANIFEST.md`.
- `W9/test_acceptance_v13.py:26-30` asserts the shipped schema is byte-identical to the v13 copy, so it goes red at v14.
- For v14: freeze W9 and add `W10/` with `verdict.schema.v14.json`, the P25 v14 templates and `test_acceptance_v14.py`.
- `carve-assets/W3/expected/dstdns-sql-r2-v6-witness.json` is deliberately **not** frozen and migrates on every cut. It is pinned `== 13` at `tests/test_gate_qualify_dstdns_sql.py:554` and regenerated by `gate/python/qualify_dstdns_sql.py`.

**Gate harnesses**
- `gate/python/qualify_topos.py`: `_EXPECTED_ROOT` → W9 (`:107-111`); literal 13 at `:964-965` and `:1021-1022`; template names at `:1287`, `:1324`, `:1372-1373`.
- `tests/test_gate_harness_version_pins.py` fails if a harness `schema_version` literal differs from `VERDICT_SCHEMA_VERSION`, or if a harness reads anything but the newest `W<n>`. It will fail first, which is useful for red-first work.
- `tools/tester-unified-gate.sh`:
  - historical collect-only loop `:576-588`: add W9's `test_acceptance_v13.py`;
  - `assert VERDICT_SCHEMA_VERSION == 13` at `:599`;
  - hard-cut probe list `:602-610`: add `("W9", 13)`;
  - marker `verdict-v6-v12-hard-cut-verified` `:624`;
  - W9 run and marker `:629-636`;
  - successors `:638-645`;
  - the B106 suite and `verdict-v13-successors-verified` `:647-654`;
  - add the B110 test file here the same way.

**Tests that pin the version, or that B106 edited**
- `tests/test_verdict_schema_is_packaged.py:267` (`"urn:assay:schema:verdict:13"`).
- `tests/test_distribution_gate.py:215-256`: marker names, W9 file names, `("W7", 11)`/`("W8", 12)` ordering, `"assert VERDICT_SCHEMA_VERSION == 13"`.
- `tests/test_verdict_v13_successors.py`: lifts W8 v12 controls to 13 (`:30-37`). The v14 successor would lift W8/W9 to 14.
- `tests/test_python_qualification.py:399-420` (`P25_V13_EXPECTED_ROOT`).
- Literal `"schema_version": 13` in:
  - `tests/test_standalone.py:343, 702, 1231, 1600`;
  - `tests/test_verify_layer_independence.py:111, 515`;
  - `tests/test_verdict_conformance.py:1209`, plus the message at `:1323`;
  - `tests/test_reuse_coverage_controls.py:19, 93`;
  - `tests/test_b106_reuse_and_witness.py:96` and the `_v13_killed_source` helpers.
- `tests/test_analysis.py:823` does `text.replace('"schema_version": 13', '"schema_version": 11')`. At v14 this replace silently does nothing and the test's `code == 1` assertion fails.
- Constant-driven (no edit needed): `tests/test_self_hosting.py:167`, `test_verdict_serialises.py:410`, `test_distribution_build_release.py:466-478`, `test_gate_harness_version_pins.py`.
- Others B106 edited: `tests/conftest.py` (added `native_outcome` `:218-256` and `native_mutation` `:259-273`), `test_cli_run.py`, `test_mutation_argv_fidelity.py`, `test_result_report_wiring_sweep.py`, `test_runner_assemble_verdict_mutation.py`, `test_verdict_claims.py`, `test_verdict_judgment.py`, `test_verdict_mutation_artifacts.py`, `test_verify_hung_bucket.py`.

**Hard-cut consumers in source**
- `src/assay/reuse.py:14` (`V12_COLD_START`), `:57-85` (message "current v13 verifier").
- `runner.py:6033-6038` ("is a v12 cold start").
- `cli.py:296-298` (help text "native v13 verdict; v12 starts cold").

**Docs:** README, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md` (section 10).

**CHANGES.md:** hand-written `### Added`, `### Documentation` and `### Testing` bullets under `## [Unreleased]`, above `<!-- cmru: release history -->` (`CHANGES.md:5-7`). The diff pattern is `git diff e5e9b95c^1 e5e9b95c -- CHANGES.md`.

**Records:** `nyxloom-trove/decisions.md` (A-461 row at `:940`; the next free ID is A-465, since A-464 is at `:943`), `nyxloom-trove/4-backlog.md` (B110 at `:11028-11285`), and a wave report.

---

## 4. mutation.py

**Imports** (`src/assay/mutation.py:126-131`): `inject_witness_plugin as _inject_witness_plugin`, `make_attempt_plan as _make_witness_attempt_plan`, `read_internal_receipt as _read_witness_receipt`, `replay_witness_from_receipt as _replay_witness_from_receipt`, `supports_sequential_pytest`, `witness_from_receipt as _witness_from_receipt`. Add a cold projection import here.

**`judge_sha256`** (`:1052-1156`)
- Current inputs, in order (`:1135-1156`): `_JUDGE_DIGEST_LABEL` (`"assay-judge-identity/2"`, `:225`), `tree_sha256`, `tool_version`, `plan.argv_effective` (counted and netstring-encoded), declared env name/value pairs, ambient env **names**, `cwd_declared`, `project_prefix`, `link_paths`.
- It has a single production caller: `run_mutation` `:2353-2366`, using the shared `plan`.
- For B110:
  - Pass the **R2-derived plan** as `plan`, so the effective command is folded automatically.
  - Add keyword-only inputs for the cold policy, transform version and baseline collection-manifest digest (each netstring-encoded with a count, keeping it injective).
  - Bump the label to `/3`; the docstring at `:223-225` says this is exactly what the label is for.
  - Tests only compare digests for equality or inequality (`tests/test_mutation_judge_identity.py:272-462`, `…_properties.py:169-189`), so defaulted kwargs keep them green.

**`run_mutation`** (`:1912-2557`)
- `reuse_witnesses` parameter (`:2036`); baseline PASS gate (`:2174`).
- The auto budget comes from the passed `baseline` (`:2184-2189`); the `plan` progress event is at `:2220-2260`; judge at `:2353-2366`.
- Hand-off to `_execute_mutation_jobs` at `:2467-2497`.
- `candidate_ids` stamped at `:2513-2523`.
- New kwargs to thread: cold policy, expected manifest and fingerprint, transform version.

**`_execute_mutation_jobs`** (`:2559-3035`)
- `witness_supported = supports_sequential_pytest(plan.argv_effective, env=…)` at `:2605-2608`. It has **no `cwd`**, so no config check happens at execution time.
- Temporary plugin directory `:2609-2613`; injection `:2614-2621`; `witness_capture_active` `:2622-2624`; `candidate_reuse` `:2626`.
- `_classified_bucket` (`:2628-2644`) is the single classifier.
- `_run_attempt(index, *, target_node_id, prior_verdict_sha256, attempt_name)` (`:2646-2855`):
  - receipt path `f"{index}-{attempt_name}.json"` (`:2664-2668`);
  - `attempt_plan = _make_witness_attempt_plan(witness_injection.plan, receipt_path=…, target_node_id=…)` (`:2669-2677`);
  - **every attempt materializes a fresh snapshot** through `prepared.materialize_replacement` (`:2678`), so a fallback attempt is automatically a fresh snapshot in a new process;
  - `execute_plan(attempt_plan, …)` `:2720-2726`;
  - receipt read `:2816-2820`;
  - prefix branch `:2821-2839` builds `MutationExecution(mode="witness-prefix", …)` and returns `None` when uncertain;
  - full branch `:2840-2854`: witness only when the outcome is FAIL and the bucket is killed.
- **Cold insertion points:**
  1. **Where the stop is requested.** Add a `cold: bool` parameter to `_run_attempt` and pass it to `_make_witness_attempt_plan(..., cold=True)`. That sets the new cold env var (section 5a). Use `attempt_name="cold"` so the receipt file is distinct.
  2. **Where the receipt is validated and becomes `MutationExecution(mode="witness-cold")`.** Add a branch after `:2839`. Call a new `cold_witness_from_receipt(receipt, process_exit_status=result.returncode, expected_…)`. Require `_classified_bucket(run) == "killed"`. Build `MutationExecution(mode="witness-cold", witness=MutationWitnessReceipt(**witness), …cold fields)`. Return `None` when uncertain, following the prefix branch at `:2826-2832`.
  3. **Where the "uncertain → fresh full rerun" fallback belongs.** In `_run_one` (`:2857-2888`), which already runs "replay attempt, then `full = _run_attempt(..., attempt_name="full")`". Insert the cold attempt before `full` (after the prefix replay if one applies), and return early when it gives a cold kill. See §11 A2 for whether a cold attempt that ran to completion without failing can stand as the survivor's full run.
- **State record write** (`:2957-2990`): `"execution": run.execution.to_dict()` at `:2986` serializes `witness-cold` automatically. Per-candidate v14 evidence (command variant, collection digest, hook fingerprint) needs a new record key. The reader does not reject unknown top-level keys (`:1310-1348`).
- `_MutantRun` (`:1644-1665`) needs an evidence field. The final `Mutation` build (`:3016-3035`) and `_outcome_of` (`:1800-1840`) are where evidence joins outcomes.
- Budget-stopped leftovers are marked through `budget_exceeded_mask` (`:2903-2915`, `:2950-2955`, `:3024-3026`). They never run and have **no evidence** (see §11 A4).

**Resume**
- `_load_validated_state_record` (`:1273-1402`) validates identity keys (`:1313-1348`), bucket (`:1349-1355`), then execution through `_execution_from_state_record` (`:1356-1361`), then the judge digest last (`:1399-1402`).
- `_execution_from_state_record` (`:1450-1497`): allowed-key sets per mode are at `:1462-1476`, with "execution mode is unknown" at `:1475`. Add `witness-cold` with `{"mode","witness", …cold fields}`.
- `_outcome_from_record` (`:1431-1447`) must restore evidence for resumed candidates.
- Tests: `tests/test_b105_mutation_boundaries.py:243-279`.

---

## 5. mutation_witness.py

**Env vars** (`src/assay/mutation_witness.py:19-23`): `WITNESS_PLUGIN_MODULE = "assay_mutation_witness_plugin"`, `ASSAY_MUTATION_WITNESS_FILE`, `…_TARGET`, `…_PLUGIN_PATH`, `…_LIVENESS_PLUGIN_PATH`. `MAX_INTERNAL_RECEIPT_BYTES = 16 * 1024` (`:18`), mirrored by the literal `16384` in the plugin (`:416`). An oversized receipt is **silently not written**, which makes it look uncertain.

**`supports_sequential_pytest`** (`:33-74`)
- Accepts direct `pytest` or `python -m pytest` (`:48-57`); rejects xdist (`:58`); rejects any `-o`/`--override-ini` (`:60-63`); rejects `PYTEST_PLUGINS` (`:65`) and xdist in `PYTEST_ADDOPTS` (`:67-71`).
- Config-file `addopts` check only when `cwd` is given (`:72`, helpers `:86-143`).
- It has three callers: `mutation.py:2605` (no cwd), `runner.py:3735` (snapshot cwd, reuse only) and `cli.py:1814` (plan preview).
- It does **not** reject order-changing or fail-fast options (`-x`, `--maxfail`, `--lf`/`--ff`/`--sw`, `-p randomly`), which B110 requires (`4-backlog.md:11116-11120`). Changing this function also changes B106 eligibility.
- The narrow `--override-ini=pythonpath=src` allowance belongs either here or in a separate cold-transform validator. It must validate the snapshot's pyproject: `[tool.pytest.ini_options] pythonpath = ["src"]` (`pyproject.toml`, checked).

**`inject_witness_plugin`** (`:146-192`)
- Writes the plugin once, sets `WITNESS_PLUGIN_PATH_ENV` and the liveness path env, prepends `PYTHONPATH`.
- Appends `-p assay_mutation_witness_plugin` and recomputes `argv_effective` from `argv_declared` (trap 2); sets `cli_argv_appended`.

**`make_attempt_plan`** (`:195-204`)
- Sets `WITNESS_FILE_ENV` and sets or pops `WITNESS_TARGET_ENV`.
- **(a) Cold-stop env var:** add `WITNESS_COLD_ENV = "ASSAY_MUTATION_WITNESS_COLD"` next to `:21`. Add a `cold: bool = False` parameter that sets it to `"1"` or pops it; popping matters because `plan.env_effective` is shared across attempts.

**Projection functions**
- `read_internal_receipt` (`:207-225`); `witness_from_receipt` (`:228-263`, exact key set at `:232`); `replay_witness_from_receipt` (`:266-285`, requires `target_count == 1`, `stopped_at_target`, no `earlier_failure`).
- Add `cold_witness_from_receipt` alongside them. It should require:
  - `cold_requested` and `stopped_cold` true;
  - `target_node_id` null;
  - no auxiliary failure and supported true;
  - session status 1 and process status 1;
  - the ordered prefix is valid, `failed_call_index == started_count - 1`;
  - collection count, digest and duplicates equal the expected baseline values;
  - hook-fingerprint digest equals the no-cov baseline's.
- Add a sibling `manifest_from_receipt` for baselines (exit status 0, no witness).

**Plugin source `_PLUGIN_SOURCE`** (`:324-493`)
- Globals `:330-338`.
- `_only_builtin_hook_impls` (`:348-395`): the `trusted()` closure (`:355-384`) accepts only the witness and liveness plugins by exact env-path match, or `_pytest.*` modules under the `_pytest` root. The `primary` checks are at `:388-395`.
- `_write` (`:398-419`); `pytest_collection_finish` (`:422-461`), whose `_STANDARD_LOOP` requires loop, protocol, reports and `session_finish` all builtin, with no xdist; `pytest_runtest_logreport` (`:464-484`); `pytest_sessionfinish` → `_write` (`:487-488`).

What to add:
- **(a) Cold mode.** In `pytest_collection_finish`, read `_COLD = os.environ.get("ASSAY_MUTATION_WITNESS_COLD") == "1"`. Treat `_COLD` together with `_TARGET` as unsupported. In `pytest_runtest_logreport`, after `_FIRST_CALL_FAILURE` is set (`:472-473`) and while `_TARGET is None`: if `_COLD` and `_STANDARD_LOOP` and not `_AUXILIARY_FAILURE` and the prefix is valid and the node ID is bounded, record `_WITNESS`, `_STOPPED_COLD = True`, `_FAILED_CALL_INDEX = _STARTED - 1`, and set `_SESSION.shouldfail` (mirroring `:481-484`). A teardown failure after the stop still sets `_AUXILIARY_FAILURE` (`:466-469`) before `_write` runs at session finish, so it disqualifies the receipt automatically.
- **(b) Collection manifest.** In `pytest_collection_finish`, `session.items` is post-deselection. Compute `count`, `duplicates = count - len(set(ids))`, and a SHA-256 over a canonical ordered encoding (for example netstrings of UTF-8 node IDs). If any node ID fails to encode, mark the manifest unsupported rather than raising. Emit only count, digest and duplicates to stay under 16 KiB.
- **(c) Started-prefix and failed index.** Add `pytest_runtest_logstart(nodeid, location)`: `_STARTED += 1`, and set `_PREFIX_OK = False` unless `session.items[_STARTED-1].nodeid == nodeid`. Emit `started_count`, `started_prefix_ok` and `failed_call_index`.
- **(d) Hook fingerprint.** At `pytest_collection_finish`, after conftests load, walk a fixed lifecycle hook list: at least `pytest_runtestloop`, `pytest_runtest_protocol`, `pytest_runtest_logstart`, `pytest_runtest_logreport`, `pytest_collectreport` and `pytest_sessionfinish`. For each implementation collect `(hook, impl.plugin_name, function.__module__, normalized file, wrapper/hookwrapper/tryfirst/trylast)`. Paths **must be normalized**:
  - each candidate runs in a different snapshot directory, so make files under `config.rootpath` relative;
  - `_pytest` files relative to the `_pytest` root;
  - the witness and liveness plugins by module token, since their temporary paths vary per run.
  Emit only `hook_fingerprint_sha256` and `hook_count` in the receipt.
- **B105 archive-hook exception.** Add a narrow branch in `trusted()` (`:355-384`) for the `pytest_sessionfinish` hook in `tests/conftest.py`, matched by resolved file relative to rootpath (the module name may be just `conftest`), and only when all four `ASSAY_B105_*` variables (`tests/conftest.py:71-74`) are absent. `tests/test_b106_reuse_and_witness.py:820-924` pins that a lookalike custom `pytest_sessionfinish` still forces full fallback.
- **Every new receipt key must be added to `_INTERNAL_RECEIPT_KEYS`** (trap 1).

---

## 6. runner.py

- **`CommandPlan`** (`src/assay/runner.py:687-741`): `argv_declared`, `argv_appended`, `argv_effective` (always declared plus appended), `cli_argv_appended` (`:707`), `env_declared`, `env_effective`, `env_passthrough`, `allow_argv_append`, `budget_seconds`, `project_prefix`, `cwd_declared`. Built by `resolve_command_plan` (`:780-913`).
- **`_run_prepared_lane`** (`:3561-~4760`; parameters `:3561-3591`, `reuse_source` at `:3585`):
  - Liveness is injected into the shared `plan` at `:3648-3662`, so the baseline and all candidates run with `-p assay_liveness_plugin`.
  - `baseline_plan = plan` (`:3705`) gets the liveness events env (`:3706-3726`).
  - Baseline snapshot `with` block starts `:3728`. The reuse sequential check with snapshot cwd is `:3729-3739` and uses `plan.argv_effective` (the coverage plan).
  - Coverage baseline: `unit = _execute_snapshot_unit(plan=baseline_plan, …)` at `:3748`. `result = unit.result`.
  - Liveness calibration from that baseline (`:3795-3800`).
  - R2 diff gate `if r2_declared and result.outcome is Outcome.PASS and r2_early_claim is None:` (`:4099`).
  - R2 dispatch `if r2_declared:` (`:4295`); ingested `:4306`; native branch starts `:4365`; candidate `LivenessRunner` `:4418-4430`; reuse witnesses `:4431-4447`.
  - `mutation.run_mutation(… plan=plan …)` `:4449-4531`; claim `:4562`; `judgment_r2 = _build_judgment_r2(…)` `:4569-4583`.
- **Insertion points:**
  1. **R2-only derived plan:** right after liveness injection (`:3662`), when the cold flag is set and the lane is native Python R2. Transform the post-liveness `plan`: take `argv_declared` minus exactly `--cov=*`, `--cov-branch`, `--cov-report=*`; set `argv_appended = plan.argv_appended + ("-p","no:pytest_cov")` with `cli_argv_appended` frozen to its prior value; recompute `argv_effective`. Validate `--override-ini=pythonpath=src` against the snapshot config inside the baseline `with` block, next to `:3729-3739` (resolve the cwd with `resolve_run_cwd(baseline_snapshot.project_root, plan)`).
  2. **Coverage-baseline manifest:** capturing it means injecting the witness plugin into `baseline_plan` (`:3705-3726`, used at `:3748`). The verdict's top-level argv and env come from `result.plan` (`assemble_verdict`, `:2220-2236`), so this becomes visible in R0's recorded `argv_appended`/`env_effective`, including a temporary PYTHONPATH entry (see §11 A6).
  3. **Uninstrumented R2 baseline:** in the native branch after `:4430` and before `:4449`. Open a fresh `prepared.materialize(timeout=deadline.remaining())` (API at `isolation.py:670`) and call `execute_plan(r2_plan + witness plugin + receipt env, cwd=snapshot.project_root, timeout=…, process_runner=process_runner, clock=clock)` (`:1094`). Require PASS. Read the manifest and fingerprint and compare with the coverage baseline's. If proof fails, run the campaign with the original plan and cold disabled, or refuse (§11 A5).
  4. Pass `plan=r2_plan`, the cold policy, expected manifest and fingerprint, and transform version into `run_mutation` (`:4449`).
  5. Pass the v14 evidence into `_build_judgment_r2` (`:4569`). Its definition (`:4964-5041`) builds `JudgmentR2(producer="native", …, liveness={…})`.
- **Flag plumbing:** `run_lane` parameters (`:5517-5572`, `reuse_from` at `:5565`); reuse validation and refusal (`:5983-6050`, `_refuse_reuse` helper, native-R2 requirement, `--shard` exclusion); `_run_higher_rigor_lane` parameter `:5164`, calls `_run_prepared_lane` at `:5351-5378`; `run_lane` → `_run_higher_rigor_lane` at `:6149-6160`.

---

## 7. cli.py

- **`assay run`** parser `:255-328`: `--resume` `:277`, `--shard` `:289`, `--reuse-from` `:290-300`, `--rejudge*` `:301-327`. Add `--cold-witness` (`action="store_true"`) after `:300`.
- Plumbing: `runner.run_lane(... reuse_from=getattr(args, "reuse_from", None), ...)` at `:1512`, inside `_cmd_run` (`:725`). Add `cold_witness=getattr(args, "cold_witness", False)`.
- **`assay plan`** parser `:330-367`, with `--reuse-from` at `:351-360`. `_cmd_plan` (`:1571-1886`):
  - reuse validation `:1583-1593`;
  - preview plan and cwd resolution `:1664-1680`;
  - `sequential_pytest_supported` computed at `:1804-1817`;
  - payload `:1839-1853`; `reuse_from` block `:1854-1883`.
- A cold-eligibility preview would be a sibling key computed alongside `:1804-1817`: transform validity, `supports_sequential_pytest` on the transformed argv, config check. No test pins the full payload key set.
- `tests/test_b105_cli_boundaries.py:30-41` `_plan_args` builds an exact `Namespace`; read any new plan flag with `getattr(args, …, default)`.
- Gate caller: `tools/self-qualification-gate.sh:183-189` runs `assay run "$lane" … --resume --state-dir …`. Add `--cold-witness` for the `self-qualification` lane only. `tests/test_self_lane.py:176-200` pins script substrings.

---

## 8. tools/b105_report_check.py

`verify_report_document` (`tools/b105_report_check.py:14-107`) checks today:
- producer exit 0 (`:27-28`);
- `commit`/`lane`/`declared_rigor` equality (`:32-44`);
- `git rev-parse <commit>^{tree}` equals the expected tree (`:46-61`);
- `outcome == PASS`, `exit_code == 0` (`:63-67`);
- every claim PASS and claim rigor order (`:69-85`);
- `assay_version` (`:87-91`);
- the five `judge_provenance` fields (`:92-107`).

CLI arguments are at `:110-147`. It does not re-run `assay verify`; the gate script does that separately (`self-qualification-gate.sh:195`).

For B110 it must additionally bind:
- `argv_declared` against the lane's argv read from `git show <commit>:assay/assay.toml` in `--repo-root` (source-bound, "a self-reported argv hash is not source proof", `4-backlog.md:11140-11143`);
- `judgment.r2.cold_witness_kills is True` for the self-qualification lane;
- the transform version equals an expected constant, and transformed/effective argv equal the transform applied to that source argv;
- recorded cwd/config identity;
- the no-cov and coverage manifest digests are equal;
- that the archive-hook exception was used only with the `ASSAY_B105_*` variables absent.

Tests: `tests/test_b105_report_check.py`. The `_verifier_valid_report` builder (`:34-75`) splices `r2_pass.json` into `pass.json` and must stay verifier-valid at v14.

---

## 9. Tests

**Real-pytest-subprocess witness tests to model red-first `witness-cold` tests on** (`tests/test_b106_reuse_and_witness.py`):
- `_seed_pytest_mutation(repo)` (`:661-677`): one compare-swap site killed by `tests/test_behavior.py::test_behavior`.
- `test_replay_requires_a_current_kill_and_falls_back_to_a_full_run` (`:680-784`): `runner.run_lane` on a `make_lane(argv=(sys.executable,"-m","pytest","tests","-q","-p","no:cacheprovider"), judge=make_r2_judge(...))`, with a `tracked_process_runner` counting calls.
- `test_witness_capture_works_with_the_existing_liveness_plugin` (`:787-817`).
- `test_custom_sessionfinish_hook_forces_full_suite_fallback` (`:820-924`): the lookalike-plugin adversary.
- `test_resume_preserves_witness_prefix_execution_provenance` (`:926-998`).
- `test_rejudge_outcome_disables_prior_witness_replay` (`:1001-1068`).
- CLI plan preview through `main([...])`: `:550-658`.

**Fixtures and helpers:** `git_repo` fixture (`tests/conftest.py:530`), `make_lane` (`:952`), `make_r2_judge` (`:1084`), `make_plan` (`:679`), `native_outcome`/`native_mutation` (`:218`/`:259`), `schema`/`validator`/`why_invalid` (`:1356-1386`), `standalone` (`:1431`).

**Unit-level**
- `tests/test_mutation_witness_unit.py` (`_plan` `:25`, `_receipt` `:39`, injection `:139-199`, projection `:227-315`).
- In `test_b106_reuse_and_witness.py`: raw verifier mode tests `:149-224`, model constructors `:303-445`, receipt bounds `:465-499`, argv support `:501-548`.
- `tests/test_verdict_b105_constructors.py:981-1010`; `tests/test_b105_mutation_boundaries.py:243-279`; `tests/test_b105_runner_boundaries.py:689-800`.

**Will need updates for v14**
- Everything in §3.
- Exact `judgment.r2` dict pins in `tests/test_cli_run.py:409-4xx`, `:576`, `:672`, `:760`.
- Hand-built native `JudgmentR2(...)` or raw native r2 dicts: `test_verdict_judgment.py` (27 constructors), `test_verdict_mutation_artifacts.py` (6), `test_runner_assemble_verdict_mutation.py`, `test_verdict_b105_constructors.py`, `test_verdict_interval_and_unsupported.py`, `test_verdict_claims.py`, `test_verdict_serialises.py`, `test_verify_layer_independence.py` (5 raw), `test_reuse_coverage_controls.py` (5), `test_standalone.py`, `test_verdict_conformance.py`, `test_verify_raw_b105.py`.
- `tests/test_self_lane.py:176-200` (gate script).
- Docs-anchor sweeps: `tests/test_docs_examples_and_vocabulary.py:659-699`.

---

## 10. Docs to extend

- **README.md:**
  - `:71` "current v13 verdict schema" (analysis receipts);
  - `:152-160` B092 bullet listing judge-identity inputs (add cold policy, effective command, transform, manifest);
  - `:161-167` B106 bullet;
  - `:981-988` B110 "not shipped yet" paragraph, to become the cold-witness explanation and its omitted-test limitation.
- **docs/DESIGN-GUIDE.md:**
  - §6 verdict contract `:402`; stale "`schema_version: 8`" at `:1446`; hard-cut policy `:1477-1485`;
  - B106 section `:1015-1054` (add a sibling "Cold witness kills (B110)" section after it, before `:1056`);
  - §7 non-goal table `:1871` (trap 8);
  - B105 section `:1934-1985` (B110 text `:1959-1981`).
- **docs/CONSUMERS.md:**
  - B105/B110 note `:70-92`;
  - `candidate_id` v13 bullet `:2745-2752`;
  - B106 worked example `:2757-2800` (add a pasteable `--cold-witness` example beside it);
  - "Adopting a v2-capable release" `:2883-2892` ("Verdict schema v13 … hard cuts").
- **CHANGES.md:** `## [Unreleased]` (`:5-7`).

---

## 11. Ambiguities to resolve before implementing

- **A1 – where the extra cold facts live.** The started-prefix length and failed-call index could go on `MutationExecution` or in a separate per-candidate evidence list. The backlog says "evidence IDs equal the mutation bucket IDs" (`4-backlog.md:11197`), which suggests an ID-keyed list, possibly carried like `Mutation.budget_per_candidate_derived_s` and emitted by `_build_judgment_r2`. Placement is not decided anywhere.
- **A2 – a cold attempt that passes.** If the no-cov suite runs to completion without failing, is that the survivor's full run (recorded `full`), or must it be rerun under the **original declared command** ("falls back to the original declared command", `4-backlog.md:11133-11134`)? This decides whether survivor cost doubles, and survivors dominate the runtime (`:11046-11056`).
- **A3 – is `cold_witness_kills` required?** "Explicit" versus "false or absent" (`:11200`): is the field required on every native `judgment.r2` (which breaks the exact-dict pins in `test_cli_run.py`) or optional?
- **A4 – candidates that never ran.** Budget-exceeded leftovers never run, and resumed records predate the evidence. "One evidence item per candidate ID" needs a defined shape for both.
- **A5 – when the uninstrumented baseline or collection proof fails.** Should the run silently fall back to a full campaign with cold off, or produce an R2 refusal? No new `ReasonCode` is allowed (`:11102-11104`).
- **A6 – instrumenting the coverage baseline.** Adding the manifest plugin changes R0's recorded argv and env; the witness plugin directory is a random temporary path (`mutation.py:2609-2613`).
- **A7 – which baseline calibrates timing.** The auto per-candidate budget (`mutation.py:2184-2189`) and the liveness calibration (`runner.py:3795`) come from the slower coverage baseline. Should the no-cov baseline feed them instead?
- **A8 – combinations with other flags.** `--cold-witness` with `--reuse-from` (order: prefix replay, then cold, then full?) and with `--shard` (`--reuse-from` refuses shards, `runner.py:5999-6007`).
- **A9 – the previous version under reuse.** `reuse.py` treats only v12 as a cold start (`:14`, `:57`). Should v13 become the cold start under v14? And should a prior `witness-cold` kill be eligible for B106 replay? It would be as written, since `classify_candidate` only needs `execution.witness` (`reuse.py:147-156`).