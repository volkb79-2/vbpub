<!-- Verbatim round-1 pre-dispatch review output (fresh-session, read-only reviewer), 2026-09-28, against a3b68800. Dispositions: ../REVIEW-2026-09-28-round1.md -->

**Both briefs: NOT READY.** Four findings block dispatch outright:

1. **A5 refusal fails `assay verify`.** When the no-cov R2 baseline can't be proven, the resulting verdict is rejected by the verifier.
2. **W3 must migrate but is forbidden.** P3a forbids touching the W3 carve asset, yet the v13 cut changed it and a test still pins it at 13.
3. **Invalid liveness example.** The "valid" example (and the carver fixture built from it) sets `reason: null`, which the current model and schema reject.
4. **Two P3b fixtures can't be built as written.** pytest 9.1.1 renames duplicate IDs and ASCII-escapes explicit IDs.

Everything was read-only: no tests, gates or docker were run. I only inspected installed pytest/pluggy/pytest-cov through Python one-liners.

## P3a — v14 schema, verifier, fixtures

**Traceability (compressed):**

| Requirement | Oracle as written | Gap |
|---|---|---|
| `transform_argv`, `collection_digest` | 12-case table; digest vectors | the 12 cases aren't listed (P3A-8) |
| witness-cold is killed-only | model + `assay verify` pairs | a raw-only omission is masked (P3A-6) |
| Evidence X5–X7 | one test per X-row | a hook-only comparison passes (P3A-8) |
| r2_command rules and key sets | invalid examples | 5-vs-6 key conflict; missing keys pass (P3A-4, P3A-7) |
| Ledger X8–X10 | ledger fixture | X8 contradicts the pairing rule (P3A-5) |
| Liveness 5 keys | examples | valid example is invalid (P3A-2) |
| Hard cut, reuse cold start | oracles 1 and 8 | OK |
| Fixtures, carve assets, gate | conformance, markers | W3 and P25 files missed (P3A-1, P3A-9) |

**Findings:**

- **P3A-1 BLOCKING (brief L563–571, L73–78).** `carve-assets/W3/expected/dstdns-sql-r2-v6-witness.json` is native SQL at v13 with a 3-key liveness block. It is "migrated by every cut", and `tests/test_gate_qualify_dstdns_sql.py:554` asserts `== 13`. The v13 merge `e5e9b95c` changed both files. P3a omits both, and its Forbid line ("carve-assets/W1–W9 content") rules W3 out.
  - Fix: add the W3 migration (14, three nulls, liveness nulls) plus the test literal, and carve W3 out of the forbid.
- **P3A-2 BLOCKING (L234, L319).** `"reason": null` with `active: true` is invalid.
  - The model requires a non-empty string (`verdict.py:~2940`); the schema requires `minLength: 1` (`verdict.schema.json:1740–1743`).
  - An active reason is `auto-pytest-argv` or `declared-true` (`liveness.py:497`).
  - Plan §5 has the same error, so the normative `r2_pass_cold_witness.json` cannot verify `[]`.
  - Fix: use `"auto-pytest-argv"` in both places.
- **P3A-3 BLOCKING (cross-package).** A payload-free R2 `ERROR/BAD_LANE_CONFIG` next to an R0 PASS fails `_check_r2_rederivation`.
  - It is not in `_INDEPENDENT_R2_TERMINALS` (`verify.py:2472`); `BAD_LANE_CONFIG` is left out on purpose (`verify.py:2458`), and `runner.py:4532–4540` explains why for B094/A-458.
  - P3b's A5 path produces exactly this pair. P3a owns `verify.py` but doesn't widen the set.
  - Fix: add `BAD_LANE_CONFIG` to both terminal sets, with a decision-table row, a raw-layer test and a fixture. Or the carver re-decides A5.
- **P3A-4 MAJOR (L271).** "`coverage_baseline` has 5 keys" contradicts L121, L133, L225 and plan §5, which all give it 6 keys (`runtime_fingerprint_sha256` is required).
- **P3A-5 MAJOR (L253 against L258).** X8 as written requires a ledger whenever a native `equivalent` bucket is non-empty. That breaks the native-SQL `r2_inconclusive_all_mutants_equivalent.json` and W3, and contradicts the "either artifact or ledger" rule at L258. The P10 brief's V2 instead splits by language.
  - Fix: "ledger set → every equivalent is `ledger`, no evidence, count equal; artifact set → no `ledger` modes."
- **P3A-6 MAJOR (L310, L313).** `verify_document` rebuilds the document through the model (`verify.py:3077`), and `R2Command.__post_init__` re-applies the transform. So a raw-only omission still fails `assay verify`, and the stated controlled breaks never go red.
  - Fix: require raw-layer tests that call `verify._check_*` directly, following `test_verify_layer_independence.py`.
- **P3A-7 MAJOR.** `_reject_unknown_keys` (`verify.py:1868`) only catches extra keys. A native document that omits `r2_command` or `equivalence_ledger` entirely passes, and `assay verify` never runs the JSON schema.
  - Fix: a raw presence check plus a decision-table row.
- **P3A-8 MAJOR (L308, L311).** The "12 argv cases" aren't enumerated.
  - The baselines' collection facts are equal by design, so an implementation that compares only the hook digest passes every X6 test.
  - Fix: list the cases (`--cov=`, `--cov-config=x`, `--cov-append`, `--no-cov-on-fail`, `--covx`, a duplicate `--cov-branch`, …). Add per-field negatives: count, sha and hook each mutated alone.
- **P3A-9 MAJOR (checklist against v13).** Missing from the checklist:
  - `tests/test_python_qualification.py:399–420` (`P25_V13_EXPECTED_ROOT`, which should move to W10);
  - the `reuse.py:~136` "v12 cold start" message;
  - `qualify_topos.py:1030` wording;
  - a `test_distribution_gate` guard that `verdict-v13-successors-verified` is absent, and `("W9", 13)`.
  - `test_b106…:96` is a `{"schema_version":14}` → "unsupported" case that must become 15. It is not a literal 13.
- **P3A-10 MINOR.** "The model has no document-level argv in scope" is false: `Verdict.argv_declared` exists. Enforce X11 in the model too.
- **P3A-11 MINOR.** Serialization choices left open:
  - `config_sha256: None` — emitted as null or omitted?
  - Is `wall_s` omitted on the coverage baseline?
  - Does a repo-root `cwd` serialize as `"."`?
  - Raw verify must type-check the argv lists and catch `UnrecognizedCoverageOption`; raw checks have no try block, so otherwise `assay verify` crashes.
- **P3A-12 MINOR.** The call site of `_check_v14_r2_command` isn't pinned. Inside `_check_b106_mutation_provenance` it would skip the sentinel (early return at `1655–1676`) and UNSUPPORTED (payload not a dict), so X3/X4 would go unchecked.
- **P3A-13 MINOR (process).**
  - No tracer bullet or skeleton was run (AUTHORING packet item 6), and the "carver-authored" fixtures are recipes, not files.
  - "Depends on P10a accepted" conflicts with the BLOCKED-PARTIAL escape.
  - "Never rebase" conflicts with plan §11.6, which says P3a is rebased onto `assay-b110-v14`.

**Combined-axis fixtures:**
- **(a)** Native SQL (artifact, equivalents) with cold false, ledger null and inactive liveness nulls → `[]`. This catches literal X8 and the W3 omission.
- **(b)** cold true with the `MUTANT_LIMIT_EXCEEDED` sentinel and `r2_command: null` → must fail in raw and in the model. This catches the sentinel early return.
- **(c)** Ingested judgment with `"cold_witness_kills": null` present, and a native one with the `r2_command` key absent → both must fail.
- **(d)** Forged `argv_declared: ["pytest", 1, "--cov"]` → failures, not a crash.

**One plausible wrong implementation per oracle:**
- **O1** (hard cut): `version < 14` cold-starts everything → caught only because a v11 case is tested.
- **O2** (witness-cold killed-only): bucket check only in the model → passes.
- **O3** (evidence binds to baseline): hook-only comparison → passes.
- **O4** (transform re-derivation): no raw re-derivation → passes.
- **O5** (ledger pairing): `entry_count >= len` → passes if only the smaller count is tested.
- **O6** (ingested unaffected): ingested cold key rejected only in the model → passes.
- **O7** (producer fork): keep `[:-3]` and reorder the tuple → passes.
- **O8** (reuse cold start): v13 message left unchanged → passes.

## P3b — R2 command, cold witness producer

**Traceability (compressed):**

| Requirement | Oracle as written | Gap |
|---|---|---|
| Static refusals | one case per table row | joined forms and passthrough (P3B-5) |
| R2 plan and traps 2–3 | spy on the child argv | OK |
| A5 runtime refusals | (a), (b), duplicate IDs | verdict fails verify; dup fixture impossible (P3B-1, P3B-2) |
| Cold stop | marker absent | OK |
| Survivor is the full run | 1 attempt | prefix not required (P3B-7) |
| Non-call failures | 2 attempts | OK |
| Fingerprint and digest | two roots; `é` | é is ASCII-escaped (P3B-2) |
| Resume | evidence key deleted | disposition conflict (P3B-3, P3B-4) |
| "Unproven PASS → crashed", `--r2-manifest`, `--shard`, A6 | none | missing (P3B-8) |

**Findings:**

- **P3B-1 BLOCKING (L266–283, L367, L371).** The A5 payload-free `ERROR/BAD_LANE_CONFIG` verdict fails `assay verify` (see P3A-3), and `verify.py` is forbidden to P3b. The oracles "verdict validity" and "A5 (a) with R0/R1 retained" can't both pass.
- **P3B-2 MAJOR (L380, L366). Two proof sources are wrong for pytest 9.1.1:**
  - `ids=["a","a"]` becomes `a0` and `a1` (`IdMaker.make_unique_parameterset_ids`), so no duplicates arise.
  - Explicit IDs are ASCII-escaped (`_resolve_ids` → `_ascii_escaped_by_config`). `test_x[é]` becomes `test_x[\xe9]`, so a character-length netstring passes the parity check.
  - Fix: produce duplicates with `--keep-duplicates` and a repeated path, or a fixture-only `modifyitems`. Get non-ASCII IDs from a `def test_é()` name or the escaping ini option.
- **P3B-3 MAJOR (L216, L369).** A malformed execution record raises `MutationStateError` (`mutation.py:1358–1363`; pinned by `test_b105_mutation_boundaries.py:225–240`). `_RECORD_REJECTED` (re-execute) is only for a judge mismatch and is deliberately checked last (`1364–1401`). The brief mislabels "re-execute" as the existing path.
  - Fix: evidence shape errors raise `MutationStateError`, stated explicitly.
- **P3B-4 MAJOR (stale evidence).** The judge identity (L208) leaves out the R2 hook sha and every coverage-baseline fact. Yet X5/X6 compare resumed or imported evidence against the current invocation's baselines.
  - Relpath rule 4 uses absolute paths (L187). pluggy 1.6 names unnamed plugins `str(id(obj))`, which varies per process.
  - Result: verifier-rejected consolidated verdicts, or every survivor ends up "crashed".
  - Fix: normalize id-named tokens to `module.qualname`; make site-packages paths purelib-relative; either fold those facts into the identity or re-validate at load (mismatch → re-execute).
- **P3B-5 MAJOR (L230–250, declared vs effective).**
  - `env_effective` isn't resolved in `run_lane`; it only exists at `runner.py:5202`.
  - CLI passthrough bypasses both the transform and the table.
  - Joined forms `-oaddopts=-x`, `-prandomly`, `-pxdist`, `-ppytest_cov` and `-cfoo.ini` aren't covered; the existing `-o` check is exact-token only (`mutation_witness.py:60`).
  - `COVERAGE_PROCESS_START`/`COVERAGE_PROCESS_CONFIG` still re-enable coverage: `a1_coverage.pth` is installed.
  - Fix: define the input as `argv_declared + cli_argv_appended` plus `env_effective` after resolution, list the joined forms, and add an env row.
- **P3B-6 MAJOR.** An R2 baseline that runs out of budget or hits the deadline is "not PASS" and becomes `BAD_LANE_CONFIG`, which misreports a timeout (A-464). This terminal state needs its own row.
- **P3B-7 MAJOR.** A survivor (cold or declared) doesn't require `started_prefix_ok`, `not unsupported` or session status 0; count equality is weaker than the stated proof. Fix: add those checks.
- **P3B-8 MAJOR.** No oracle covers:
  - "declared PASS unproven → crashed" (e.g. a mutant that calls `os._exit(0)` or `pytest.exit(returncode=0)`);
  - the `--r2-manifest` refusals and atomic copy;
  - `--cold-witness` together with `--shard`;
  - a cold hung or budget result being final;
  - A6's recorded R0 argv.
- **P3B-9 MAJOR (2b packet).** Missing:
  - a 25-key receipt example (one valid, at least two invalid);
  - a sample fingerprint line;
  - canonical runtime-fingerprint JSON (which `implementation`/`machine` expressions, `dumps` parameters, and how xdist's duplicate distinfo entries are handled);
  - when the fingerprint is taken (it should be `collection_finish`);
  - a tracer bullet for `-p no:pytest_cov` plus `shouldfail`.
- **P3B-10 MINOR.** Fixture details:
  - `os._exit(2)` in `pytest_unconfigure` must be conditional on failures, or the baseline fails.
  - The marker file must live outside the snapshot.
  - Attempt counting needs `liveness="false"`, as the existing tests use.
- **P3B-11 MINOR.** No `judgment.r2` is built on the `except AssayError` path (`runner.py:4543–4583`), so X4 is effectively only about UNSUPPORTED.
- **P3B-12 MINOR.** The archive exception widens B106 replay trust to every consumer's `tests/conftest.py`; document it.
- **P3B-13 MINOR.** The report's "local no-cov baseline wall time" means running the full suite on the shared host. Say niced and serial, or take it from the gate log.

**Combined-axis fixtures:**
- **(a)** `allow_argv_append=true` with `-- -pxdist` plus `--cold-witness` → static refusal, 0 runner calls.
- **(b)** `--resume` across two invocations with a relocated venv or an unnamed conftest plugin, and a declared survivor → the verdict must verify `[]`.
- **(c)** A5 coverage-only test with R3 declared and `--verdict-json` → must verify.
- **(d)** Liveness on, cold on, first failure in setup → 2 attempts, `evidence.command:"declared"`.

**Wrong implementation per oracle:**
- **Static refusals:** exact-token matching passes.
- **R2 plan:** judge identity computed over the coverage plan passes; only unit digests are tested.
- **Cold stop:** stopping only on call failures passes.
- **Survivor:** skipping the hook/runtime match passes.
- **Lookalike sessionfinish:** caught.
- **Path normalization:** id-named plugins pass.
- **Digest parity:** a character-length netstring passes.
- **Judge identity:** omitting the hook sha passes.
- **Resume:** a shape-only check passes.
- **A8 (replay order):** caught.
- **Plan preview:** ignoring config `addopts` passes.

## Other checks and wrong anchors

- **Anchors:** ~60 sampled; three are wrong.
  - P3a L47: `_check_equivalence_pairing` is called at `verdict.py:4858`, not ~4805 (`_check_discarded_disposition` is at 4863).
  - P3a L363: the ingested not-required list is at schema `1900–1907`, not `1897–1904`.
  - P3a L408: `test_b106…:96` is the future-version case (see P3A-9).
  - All P3b anchors I sampled were accurate.
- **pytest-cov removal:** the `pytest11` entry point is named `pytest_cov` (pytest-cov 7.1.0, pytest 9.1.1). `-p no:pytest_cov` blocks it and unregisters it if already loaded. It comes last in argv, so an earlier `-p pytest_cov` can't re-enable it. It is not proven empirically, and coverage can still start through env (P3B-5).
- **Decision table soundness:** `killed` needs a failed call, session and process exit 1, and matching facts, so it is sound. Declared FAIL → killed is the same as today. `survived` is under-proven (P3B-7) and stale evidence is possible (P3B-4).
- **Traps from the implementation map:** receipt keys in both places, argv in `argv_declared`, `-p no:pytest_cov` with `cli_argv_appended` frozen, fork defaults of None, no `MUTATION_STATE_SCHEMA_VERSION` bump, the DESIGN-GUIDE §7 exception, the 100% floor with both target lists, and the `test_analysis.py:823` replace trap are all handled.
- **Consistent across P3a, P3b and plan §5:** the digest encoding (byte length, trailing comma), `witness-cold` = `{mode, witness}`, `ledger` = `{mode, anchor}`, evidence always has six keys, and the runtime fingerprint is required on both baselines (apart from the P3a L271 key count).
- **Gate:** host-load rule present, focused tests niced, markers read in a separate step, BLOCKED rule present, and §3b pasted verbatim in both briefs.