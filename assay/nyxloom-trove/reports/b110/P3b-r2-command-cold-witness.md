# B110-P3b — R2 command transform, no-coverage R2 baseline, cold witness producer

| Field | Value |
|---|---|
| Backlog | **B114** (B110 umbrella) |
| Branch | `assay-b110-p3b-cold` off the current `assay-b110-v14` tip. The v14 integration-branch protocol is in `P3a-v14-schema-verify.md`. |
| Depends on | **P3a** merged into `assay-b110-v14` (the model, schema, verify and `r2_command.py`). **P1** merged into the integration line and then into `assay-b110-v14` (the B105 lanes no longer carry `--override-ini=pythonpath=src`). |
| Contract class | **2b**. The public behaviour and all shapes are fixed; the private construction is yours. |
| Implementer | Opus (fresh session) |
| Decisions | A-470 (D6: A1–A9), A-471 (runtime fingerprint in the judge identity), A-468 (override dropped) |
| Size | L |

**What this package is.** It is the producer side of v14.
- With the explicit `assay run --cold-witness` opt-in, a native Python R2 lane gets an R2-only command. That command is the declared argv minus the three recognized pytest-cov forms, plus `-p no:pytest_cov`.
- Two runtime-proven baselines come first: the coverage baseline and a new no-cov R2 baseline, each with an ordered collection manifest, a hook fingerprint and a runtime fingerprint.
- Then per-candidate cold attempts run. They stop at the first verified call failure (`witness-cold`), or a completed pass is the survivor's full run. On any uncertainty, one declared-command full attempt is authoritative.

Without `--cold-witness` the behaviour is byte-identical to today, apart from the v14 defaults P3a emits.

---

## Context to read first

Paths are relative to `assay/`, verified at `db85f747`.

**Plan and sibling brief:**
- `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md` §3 (D6 A1–A9), §5 (the wire contract) and §6 (the distributed-foresight invariants).
- `nyxloom-trove/reports/b110/P3a-v14-schema-verify.md`, the packet: the `R2Command`/`R2BaselineFacts`/`MutantEvidence` shapes and X1–X11.

**`src/assay/mutation_witness.py` (all 493 lines):**
- 18-23: the constants.
- 33-74: `supports_sequential_pytest`. Its config `addopts` helpers are at 86-143.
- 146-192: `inject_witness_plugin` (**trap:** it recomputes `argv_effective` from `argv_declared` at ~186).
- 195-204: `make_attempt_plan`.
- 207-285: `read_internal_receipt`, `witness_from_receipt` (the exact key set is at 232), `replay_witness_from_receipt`.
- 308-321: `_INTERNAL_RECEIPT_KEYS`.
- 324-493: the plugin source:
  - `_only_builtin_hook_impls`/`trusted` at 348-395;
  - `_write` at 398-419 (the 16384 cap is at 416);
  - `pytest_collection_finish` at 422-461;
  - `pytest_runtest_logreport` at 464-484;
  - `pytest_sessionfinish` at 487-488.

**`src/assay/mutation.py`:**
- 126-131: witness imports.
- 220: `MUTATION_STATE_SCHEMA_VERSION = 1`. **Never bump it.**
- 223-225: `_JUDGE_DIGEST_LABEL`.
- 1052-1160: `judge_sha256`.
- 1273-1402: `_load_validated_state_record`.
- 1431-1497: `_outcome_from_record` and `_execution_from_state_record` (the per-mode key sets are at 1462-1476).
- 1644-1666: `_MutantRun`.
- 1668-1700: `_classify_mutant_result`.
- 1800-1840: `_outcome_of`.
- 1877-1889: `baseline_wall_seconds`.
- 1912-2557: `run_mutation`. Its parameters are at 1947-2036, the `plan` progress event at ~2220-2260 and the judge-identity call at 2353-2366.
- 2559-3035: `_execute_mutation_jobs`. The parts that matter:
  - `witness_supported` at 2605;
  - plugin dir and injection at 2609-2624;
  - `_classified_bucket` at 2628-2644;
  - `_run_attempt` at 2646-2855, which materializes a **fresh snapshot per attempt** at ~2678; the receipt read is at ~2816, the prefix branch at 2821-2839 and the full branch at 2840-2854;
  - `_run_one` at 2857-2888;
  - the candidate event at ~2930-2955;
  - the state record at ~2957-2990.
- 3626-3770: `judge_mutation`, for ordering only (crashed → `ERROR/EXEC_FAILED`).

**`src/assay/runner.py`:**
- 687-741: `CommandPlan`. `cli_argv_appended` is at 709; the append-consent refusal is at 1160-1182.
- 916-930: `resolve_run_cwd`.
- 2800-2830: `_execute_snapshot_unit`.
- 3561-3662: `_run_prepared_lane`, the head and the liveness injection.
- 3700-3800: the coverage baseline. `baseline_plan = plan` at 3705; the snapshot `with` at ~3728; the unit at ~3748; `result = unit.result` at 3778; calibration at 3796.
- 4099: the R2 diff gate.
- 4295-4560: the native R2 branch:
  - `LivenessRunner` at 4420;
  - reuse witnesses at ~4431-4447;
  - the `run_mutation` call at 4449;
  - `except InvalidRejudgeIdError` at ~4532;
  - `except AssayError` (B053/A-409: this renders a **payload-free R2 claim** and refuses R3) at ~4543.
- 4569-4583 and 4964-5041: `_build_judgment_r2`.
- 5517-5572 and 5983-6050: `run_lane` parameters and the reuse validation (`_refuse_reuse` at 5985).
- 5164 and 5351-5378: the `_run_higher_rigor_lane` plumbing.

**`src/assay/liveness.py`:** 469-494 (the injection precedent: `cli_argv_appended` freezing).

**`src/assay/cli.py`:**
- the `run` parser at 255-328 (`--reuse-from` at 291-300);
- `_cmd_run` at 725 and the `run_lane` call at ~1490-1512;
- `_resolve_state_dir` at 845-878 (the visibility-refusal pattern);
- `_cmd_plan` at 1571-1886 (preview cwd at 1664-1680; `sequential_pytest_supported` at 1804-1817; the payload at 1839-1883).

**`src/assay/isolation.py`:** 670 (`materialize(timeout=)`) and 230-240 (`Snapshot.root` / `project_root`).

**`tests/conftest.py`:** 59-80. This is the B105 archive hook. Its four variables are `ASSAY_B105_COVERAGE_SOURCE`, `ASSAY_B105_COVERAGE_ARCHIVE_DIR`, `ASSAY_B105_SOURCE_COMMIT` and `ASSAY_B105_SOURCE_TREE`. It returns immediately when all four are unset.

**Tests to model on:**
- `tests/test_b106_reuse_and_witness.py`:
  - 501-548: argv support;
  - 661-677: `_seed_pytest_mutation`;
  - 680-784: a real `run_lane` with `tracked_process_runner`;
  - 787-817: liveness coexistence;
  - 820-924: the lookalike `pytest_sessionfinish` adversary;
  - 926-998: resume.
- `tests/test_mutation_witness_unit.py`: 25-53 (the `_plan`, `_receipt` helpers; `_receipt` builds the exact key set) and 139-315.
- `tests/test_b105_cli_boundaries.py`: 30-41 (the exact `Namespace` → read new flags with `getattr`).
- `tests/test_b105_mutation_boundaries.py`: 243-279.

---

## Implementation packet (normative)

### Owned interfaces

**`src/assay/mutation_witness.py` (owner)**

```python
WITNESS_COLD_ENV = "ASSAY_MUTATION_WITNESS_COLD"                # "1" or absent
WITNESS_MANIFEST_FILE_ENV = "ASSAY_MUTATION_WITNESS_MANIFEST_FILE"  # sidecar path or absent
B105_ARCHIVE_ENV = ("ASSAY_B105_COVERAGE_SOURCE", "ASSAY_B105_COVERAGE_ARCHIVE_DIR",
                    "ASSAY_B105_SOURCE_COMMIT", "ASSAY_B105_SOURCE_TREE")
HOOK_FINGERPRINT_HOOKS = ("pytest_runtestloop", "pytest_runtest_protocol",
    "pytest_runtest_logstart", "pytest_runtest_logreport", "pytest_runtest_call",
    "pytest_runtest_setup", "pytest_runtest_teardown", "pytest_collectreport",
    "pytest_collection_modifyitems", "pytest_sessionfinish")

def make_attempt_plan(plan, *, receipt_path: Path, target_node_id: str | None,
                      cold: bool = False, manifest_path: Path | None = None) -> CommandPlan
    # sets/pops WITNESS_FILE_ENV, WITNESS_TARGET_ENV, WITNESS_COLD_ENV ("1" or pop),
    # WITNESS_MANIFEST_FILE_ENV (path or pop). cold=True with target_node_id != None -> ValueError.

def cold_shape_refusal(argv: Sequence[str], env: Mapping[str, str]) -> str | None
    # static; see the refusal table. Returns a one-line reason or None.

@dataclass(frozen=True)
class ReceiptFacts:
    collection_count: int; collection_sha256: str; duplicates: int
    hook_fingerprint_sha256: str; hook_count: int
    runtime_fingerprint_sha256: str | None; config_sha256: str | None
    started_count: int; collection_error: bool

def receipt_facts(receipt: Mapping[str, Any]) -> ReceiptFacts | None
    # None unless manifest_supported is True and every collection/hook field is well-typed
    # (int-not-bool, 64-hex).

def cold_witness_from_receipt(receipt, *, process_exit_status: int,
        expected: ReceiptFacts) -> tuple[dict[str, Any], int, int] | None
    # returns (five-field witness dict, started_count, failed_call_index) or None.
    # Requires ALL of:
    #   cold_requested; stopped_cold; not unsupported; target_node_id is None;
    #   not earlier_failure; not auxiliary_failure; not collection_error;
    #   started_prefix_ok; witness_when == "call"; witness_outcome == "failed";
    #   bounded witness_node_id; session_exit_status == 1 and process_exit_status == 1
    #   (int, not bool);
    #   failed_call_index == started_count - 1;
    #   receipt_facts(receipt) matches expected on collection_count, collection_sha256,
    #   duplicates == 0, hook_fingerprint_sha256 and runtime_fingerprint_sha256.
```

**The receipt gains these keys, written by the plugin's `_write` AND added to `_INTERNAL_RECEIPT_KEYS` in the same commit (trap 1).** `witness_from_receipt` rejects any other key set, so every B106 test helper `_receipt()` must add them.

| Key | Type | Meaning |
|---|---|---|
| `cold_requested` | bool | `WITNESS_COLD_ENV == "1"` |
| `stopped_cold` | bool | the cold stop fired on a clean call failure |
| `collection_error` | bool | any `pytest_collectreport` with `report.failed` |
| `manifest_supported` | bool | every node ID encodes as UTF-8, contains no `\n`/`\r`, and is ≤ 4096 bytes |
| `collection_count` | int or null | `len(session.items)` after deselection; null if `pytest_collection_finish` never ran |
| `collection_sha256` | hex or null | `r2_command.collection_digest` semantics, computed inline in the plugin (the plugin cannot import assay) |
| `collection_duplicates` | int or null | count − number of distinct IDs |
| `started_count` | int | `pytest_runtest_logstart` calls |
| `started_prefix_ok` | bool | every logstart k matched `session.items[k-1].nodeid` |
| `failed_call_index` | int or null | set only with `stopped_cold` |
| `hook_fingerprint_sha256` | hex or null | see §5 of the plan and the normalization below |
| `hook_count` | int or null | the number of fingerprint lines |
| `runtime_fingerprint_sha256` | hex or null | plan §5 canonical JSON |
| `config_sha256` | hex or null | sha256 of `config.inipath` bytes; null when there is no inipath |
| `archive_hook_exception_used` | bool | the B105 conftest `pytest_sessionfinish` was trusted via the exception |

The whole receipt stays ≤ 16 KiB (`MAX_INTERNAL_RECEIPT_BYTES`, mirrored by `16384` in the plugin). Node lists are **never** placed in the receipt. The ordered list goes only to the sidecar file (`WITNESS_MANIFEST_FILE_ENV`): one node ID per line, each followed by `\n`, UTF-8, written only when `manifest_supported`.

**Plugin normalization rules for `hook_fingerprint_sha256`.** For each hook in `HOOK_FINGERPRINT_HOOKS` and each impl, the line is `hook|plugin_token|module|relpath|flags`:
- `plugin_token`: `impl.plugin_name` if it contains neither `/` nor `\`; else the literal `<path>`.
- `module`: `impl.function.__module__`.
- `relpath` is the resolved `co_filename`, normalized in this order:
  1. the witness or liveness plugin module → the module name;
  2. under `config.rootpath` → a POSIX path relative to it;
  3. under the `_pytest` package root → `_pytest/<relative>`;
  4. otherwise → the absolute resolved POSIX path. That path is stable within one environment; the runtime fingerprint covers cross-environment differences.
- `flags`: comma-joined, sorted subset of `hookwrapper`, `tryfirst`, `trylast`, `wrapper` (true attributes), or `-`.

Sort the lines, join them with `\n` and take the SHA-256. `hook_count` is the number of lines. Per-candidate snapshot paths differ, so rule 2 is what makes candidates comparable. It must be tested with two different rootpaths.

**B105 archive-hook exception.** `trusted()` becomes `trusted(impl, hook_name)`. An impl is additionally trusted **iff** all of the following hold:
- `hook_name == "pytest_sessionfinish"`;
- `module_name == "conftest"`;
- the resolved module file, relative to `config.rootpath`, is exactly `tests/conftest.py`;
- `module_file == code_file`;
- **none** of the four `B105_ARCHIVE_ENV` names is present in `os.environ`.

When it is used, set `_ARCHIVE_EXCEPTION_USED = True`. `test_custom_sessionfinish_hook_forces_full_suite_fallback` (820-924) must still force fallback, because its lookalike is not `tests/conftest.py` under the rootpath with the variables absent.

**Plugin cold stop.** In `pytest_runtest_logreport`, when `_COLD and _TARGET is None`, on the **first failed report of any phase**:
- if `report.when == "call"`, `_STANDARD_LOOP` holds, there is no earlier/auxiliary/collection failure, `_PREFIX_OK` holds and the node ID is bounded: set `_WITNESS`, `_STOPPED_COLD = True`, `_FAILED_CALL_INDEX = _STARTED - 1`;
- in **every** case: `_SESSION.shouldfail = "assay stopped at the first failure (cold witness)"`.

A teardown failure after the stop still sets `_AUXILIARY_FAILURE` before `_write`, so it disqualifies the receipt. If `_COLD` and `_TARGET` are both set, `unsupported` is true.

**`src/assay/mutation.py` (owner)**
- `judge_sha256(*, tree_sha256, plan, link_paths=(), tool_version=None, cold_witness_kills: bool = False, r2_transform: str | None = None, r2_collection_sha256: str | None = None, runtime_fingerprint_sha256: str | None = None, equivalence_ledger_sha256: str | None = None) -> str`.
  - The label becomes `"assay-judge-identity/3"`.
  - After the existing link section, append in this order: `netstring("1" if cold_witness_kills else "0")`, then `netstring("" if x is None else x)` for each of the four optional values.
  - The docstring at 223-225 already says a label bump is the invalidation mechanism.
- `run_mutation(..., cold_witness: bool = False, declared_plan: CommandPlan | None = None, r2_facts: ReceiptFacts | None = None, coverage_facts: ReceiptFacts | None = None, r2_baseline_s: float | None = None, equivalence_ledger_sha256: str | None = None)`.
  - When `cold_witness`: `plan` **is the R2 plan**, and `declared_plan`, `r2_facts` and `coverage_facts` are required (`ValueError` otherwise).
  - The judge identity is computed with `plan=<R2 plan>` and the new inputs.
- `_MutantRun` gains `evidence: MutantEvidence | None = None`. `_outcome_of(job, *, kill_signal=None, execution=None, evidence=None)`.
- The state record gains the optional top-level key `"evidence"` (`MutantEvidence.to_dict()` or null). The loader validates it through `MutantEvidence(**raw)` with an exact six-key set, and **rejects the record** (the existing rejected-record path, which re-executes) on any mismatch. When the current campaign has `cold_witness`, a `survived` or `witness-cold` record without evidence is rejected. `_execution_from_state_record` accepts `witness-cold` with keys exactly `{"mode","witness"}`.
- The `candidate` progress event gains `"execution_mode"`. The `plan` event gains `"cold_witness": bool` and `"r2_baseline_s": float | null` (A7). The event-name vocabulary is unchanged.

**`src/assay/runner.py` / `cli.py`**
- `run_lane(..., cold_witness: bool = False, r2_manifest: Path | None = None)`, threaded through `_run_higher_rigor_lane` into `_run_prepared_lane`.
- CLI: `assay run` gains `--cold-witness` (store_true) and `--r2-manifest PATH`.
  - `--r2-manifest` without `--cold-witness`, or naming a visible tracked path, is a whole-lane `BAD_LANE_CONFIG` refusal. Use the `_resolve_state_dir` visibility pattern: gitignored or outside the tree.
- `assay plan` gains `--cold-witness`. With it, the payload gains `"cold_witness": {"eligible": bool, "refusal": str | null, "argv_transformed": [..] | null}`. It is computed statically:
  1. `transform_argv`;
  2. then `cold_shape_refusal`;
  3. then `supports_sequential_pytest(transformed + R2_APPENDED, env=…, cwd=<preview cwd from 1664-1680>)`.

  Without the flag the payload is byte-identical to today.

### Static refusal table (`cold_shape_refusal`, applied to `transform_argv(lane argv)` and `env_effective`)

| Condition | Reason (substring tests assert) |
|---|---|
| `transform_argv` raises `UnrecognizedCoverageOption` | `unrecognized coverage option <token>` |
| not `pytest …` / `python -m pytest …` per `supports_sequential_pytest`'s parser | `not a sequential pytest command` |
| any `-o`, `--override-ini`, `--override-ini=*` | `pytest override` |
| `-c`, `--config-file*`, `--rootdir*`, `--confcutdir*` | `pytest configuration override` |
| `-x`, `--exitfirst`, `--maxfail`, `--maxfail=*`, or a single-dash cluster containing `x` (not starting `-r`, `-k`, `-m`, `-p`, `-o`, `-c`, `-W`) | `fail-fast option` |
| `--lf`, `--last-failed`, `--ff`, `--failed-first`, `--nf`, `--new-first`, `--sw`, `--stepwise`, `--stepwise-skip`, `--sw-skip` | `order-changing option` |
| a token starting `--randomly` or `--random-order`; `-p randomly`, `-p random_order`, `-p pytest_randomly` (the enabling forms only; `-p no:randomly` and other `-p no:*` stay allowed) | `order-changing option` |
| `-p pytest_cov` or `-p pytest-cov` (it would re-enable the plugin the transform disables) | `coverage plugin re-enabled` |
| `-n*`, `--numprocesses*`, `--dist*`, `-p xdist` | `parallel option` |
| `PYTEST_ADDOPTS` present with a non-empty value, or `PYTEST_PLUGINS` present | `pytest environment option` |
| lane has no R2, R2 is ingested, or the adapter is not python | `cold witness needs a native python R2 lane` |

Any static refusal is a **whole-lane `ERROR`/`BAD_LANE_CONFIG`** before any execution. Raise it through the same helper shape as `_refuse_reuse` (runner.py:5985).

### Required flow (`_run_prepared_lane`, `cold_witness=True`)

1. **Static checks.** In `run_lane`, beside the `--reuse-from` validation (~5983-6050): the static refusal table. `--cold-witness` with `--shard` is **allowed**; `--reuse-from` with `--shard` stays refused, unchanged.
2. **Build the R2 plan** right after the liveness injection (~3662). Keep `plan` (coverage) for R0/R1.
   ```python
   frozen = plan.argv_appended if plan.cli_argv_appended is None else plan.cli_argv_appended
   new_declared = transform_argv(plan.argv_declared)
   new_appended = plan.argv_appended + R2_APPENDED
   r2_plan = dataclasses.replace(plan, argv_declared=new_declared, argv_appended=new_appended,
                                 argv_effective=new_declared + new_appended,
                                 cli_argv_appended=frozen)
   ```
   **Trap 2:** the transformed argv lives in `argv_declared` of `r2_plan`, because `inject_witness_plugin` recomputes `argv_effective` from `argv_declared`. **Trap 3:** `-p no:pytest_cov` goes in `argv_appended` with `cli_argv_appended` frozen, otherwise `allow_argv_append = false` (the B105 lane) gives `EXEC_FAILED`.
3. **Witness infrastructure (A6).** Create one lane-run temp plugin dir (`tempfile.mkdtemp(prefix="assay-witness-")`, removed in `finally`).
   - `baseline_plan` (~3705) becomes `make_attempt_plan(inject_witness_plugin(<baseline plan after its liveness env>, …).plan, receipt_path=<dir>/coverage-baseline.json, target_node_id=None)`.
   - R0's recorded `argv_appended`/`env_effective` then show the witness plugin. This is accepted infrastructure injection (A6, the RW-36 precedent). Document it.
4. **Coverage baseline.** The unit runs as today. Afterwards read `coverage_facts = receipt_facts(read_internal_receipt(...))`.
5. **Enter the native R2 branch** (~4295-4449). **Inside the existing `try` that wraps `mutation.run_mutation`**, so a raised `AssayError` is rendered payload-free by the B053/A-409 `except AssayError` and R3 is refused:
   1. If `coverage_facts is None` or `coverage_facts.duplicates != 0`: raise `AssayError("cold witness: the coverage baseline's collection could not be proven: <why>", outcome=ERROR, reason_code=BAD_LANE_CONFIG)`.
   2. Open `with prepared.materialize(timeout=deadline.remaining()) as snap:`, with `cwd = resolve_run_cwd(snap.project_root, r2_plan)`.
   3. If `not supports_sequential_pytest(r2_plan.argv_effective, env=r2_plan.env_effective, cwd=cwd)` (config `addopts`): refuse as above.
   4. Run the R2 baseline unit through `_execute_snapshot_unit`, mirroring the coverage call at ~3748, with:
      - `plan = make_attempt_plan(inject_witness_plugin(r2_plan, …).plan, receipt_path=<dir>/r2-baseline.json, target_node_id=None, manifest_path=<dir>/r2-manifest.txt)`;
      - `wants_coverage=False` and the coverage fields `None`;
      - the plain `process_runner`, not `LivenessRunner`;
      - `timeout = deadline.remaining()`.

      Its progress `command_finished` phase is `"r2-baseline"`. Add a keyword if the phase is hard-coded.
   5. Refuse, as above, if:
      - the outcome is not PASS (`R2 no-coverage baseline did not pass`);
      - `r2_facts = receipt_facts(...)` is None;
      - the R2 receipt says `unsupported` (a non-builtin loop, protocol, report or sessionfinish, other than the archive exception);
      - `runtime_fingerprint_sha256` is None;
      - `duplicates != 0`;
      - `(collection_count, collection_sha256)` differs from `coverage_facts`;
      - the sidecar's recomputed `collection_digest(lines)` differs from `r2_facts.collection_sha256`.
   6. If `r2_manifest` was given, copy the sidecar there atomically (temp + `os.replace`).
   7. Build `R2Command`:
      - `cwd` is `cwd` relative to `snap.root`, as POSIX;
      - `config_sha256` comes from the R2 receipt;
      - `wall_s = baseline_wall_seconds(r2 result)`;
      - `coverage_baseline` gets the `runtime_fingerprint_sha256` from the coverage receipt, and no `wall_s`. The manifest-only plugin computes the fingerprint with the same function. The carver decided on 2026-09-28 to include it, for P10 OC12. If the coverage receipt's fingerprint is None, refuse exactly like step 5.
   8. Call `run_mutation(plan=r2_plan, cold_witness=True, declared_plan=<the coverage plan, liveness-injected, as candidates use today>, r2_facts=…, coverage_facts=…, r2_baseline_s=…)`.
6. **`_build_judgment_r2(..., cold_witness_kills=True, r2_command=<R2Command or None if the R2 claim is payload-free>)`**. X3/X4 in P3a.
7. **A7:** the auto per-candidate budget and liveness calibration keep using the coverage baseline, unchanged.

### Per-candidate flow (`_execute_mutation_jobs._run_one`, `cold_witness=True`)

Two witness injections share one plugin dir:
- `inj_r2 = inject_witness_plugin(plan)`, where `plan` is the R2 plan;
- `inj_declared = inject_witness_plugin(declared_plan)`.

`_run_attempt(index, *, target_node_id, prior_verdict_sha256, attempt_name, cold=False, variant="r2"|"declared")` picks the injected plan by `variant`. Every attempt still materializes a **fresh snapshot** in a new process (~2678).

The attempts run in this order:
1. **B106 prefix replay**, if `reuse_witnesses` has the candidate. This is the existing path, run on the **r2** variant. The declared variant carries pytest-cov's `pytest_runtestloop` wrapper, so it is never replay-eligible. The runner's reuse eligibility check at ~3729-3739 must therefore evaluate `r2_plan.argv_effective` when `cold_witness` is set. A replayed kill records `witness-prefix` plus `evidence` (`command:"r2"`, facts if readable, started/failed null; X7). If it returns a run, done.
2. **Cold attempt:** `variant="r2"`, `cold=True`, `attempt_name="cold"`.
3. **Declared full attempt**, only when the decision table says so: `variant="declared"`, `attempt_name="full"`.

**Decision table (cold attempt → result).** "Facts match r2" means `receipt_facts` matches `r2_facts` on collection count and sha, duplicates 0, hook sha, runtime sha, and `not collection_error`.

| Cold attempt outcome | Receipt condition | Bucket | `execution` | `evidence` | Further attempt |
|---|---|---|---|---|---|
| FAIL | `cold_witness_from_receipt(...)` is not None | killed | `witness-cold` + witness | `command:"r2"`, facts, `started_count`, `failed_call_index` | none |
| FAIL | anything else (setup/teardown/collection failure, prefix broken, fingerprint mismatch, unreadable receipt) | — | — | — | **declared full attempt** |
| PASS | facts match r2 **and** `started_count == collection_count` | survived | `full` | `command:"r2"`, facts, started/failed null | none (A2) |
| PASS | anything else | — | — | — | **declared full attempt** |
| BUDGET_EXCEEDED / hung / ERROR | any | per `_classify_mutant_result` | `full` | `command:"r2"` facts if `receipt_facts` is readable, else null | none. These buckets never pass R2 and are visible; re-running would only re-roll a hang |

**Declared full attempt → result:**

| Outcome | Receipt | Bucket | `execution` | `evidence` |
|---|---|---|---|---|
| FAIL | any | killed | `full` (plus the witness exactly as the existing full branch at 2840-2854 records it) | `command:"declared"` facts if readable, else null |
| PASS | `receipt_facts` matches `coverage_facts` (collection count/sha, dup 0, hook sha) and `started_count == collection_count` | survived | `full` | `command:"declared"`, facts |
| PASS | anything else | **crashed** | `full` | `command:"declared"` facts if readable, else null |
| BUDGET_EXCEEDED / hung / ERROR | any | per classifier | `full` | facts if readable, else null |

For the "PASS but unproven" crashed row, build the bucket directly. **Do not** fake a `CommandResult`: add a branch in `_classified_bucket` that takes the attempt's proof status. `judge_mutation` then yields `ERROR/EXEC_FAILED`. It is never a PASS and never a survivor without proof (X6).

**Never-started budget leftovers** keep `_outcome_of(job)`, i.e. `execution: {"mode":"full"}` with **no** evidence (X7 allows this).

**Without `cold_witness`:** no second injection, no `variant`; the flow and outputs are unchanged, including `evidence` always None.

### Topology

```
consumer checkout (repo_top/assay)    --(P22 seed)-->  per-attempt snapshot  snap.root/assay  (= snap.project_root)
lane-run temp plugin dir  /tmp/assay-witness-XXXX/{assay_mutation_witness_plugin.py, *.json, r2-manifest.txt}
_execute_mutation_jobs plugin dir     (its own, 2609-2613; receipts named f"{index}-{attempt_name}.json")
--r2-manifest PATH                    (consumer-side, gitignored/outside tree; the gate passes .assay/r2-manifest-<lane>.txt)
```
- Hook `relpath`s are relative to each attempt's `config.rootpath`, which is inside that attempt's snapshot. This is why the fingerprint is comparable across candidates.
- The coverage plan (`plan`) is what R0/R1 and the verdict's top-level `argv_*` record.
- `r2_plan` is what `run_mutation`, the judge identity and cold attempts use.
- `declared_plan` is the coverage plan used only for fallback attempts.

### Bounds and provenance

| Value | Source | Bound / refusal |
|---|---|---|
| receipt size | plugin `_write` | ≤ 16 KiB, else not written, which is treated as unreadable (uncertain) |
| node ID | pytest | ≤ 4096 UTF-8 bytes, else `manifest_supported=false` or the witness is unbounded (existing `_bounded`) |
| collection digest | plugin inline, equal to `r2_command.collection_digest` | a test asserts equality on non-ASCII IDs |
| runtime fingerprint | plugin | null → R2 baseline refusal |
| transform id | `r2_command.R2_TRANSFORM_ID` | constant |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| static refusals | `mutation_witness.cold_shape_refusal`, `run_lane` | one parametrized case per table row → verdict `ERROR/BAD_LANE_CONFIG`, whole lane, no snapshot materialized (a counting `process_runner` sees 0 calls). B105's current argv (after P1) → eligible | `tests/test_b110_cold_witness.py` (new) | drop the `-x` cluster check → `-qx` is accepted → red |
| R2 plan construction | runner | the recorded R0 `argv_effective` still has `--cov*`; the R2 baseline's argv (read back from a `process_runner` spy) has no `--cov*`, ends with `-p no:pytest_cov` and the witness plugin, **and** `allow_argv_append=false` does not refuse | same | put the transform in `argv_effective` only → the spy sees `--cov` → red |
| cold stop before a marker | plugin + `_run_one` | real pytest project: `test_a` passes, `test_b` fails (killed by the mutant), `test_c` writes a marker file. Cold kill → `witness-cold`, `started_count==2`, `failed_call_index==1`, **marker absent** | `_seed_pytest_mutation`-style fixture with 3 tests | disable the `shouldfail` → marker present → red |
| survivor completes as full | same | an unkilled candidate: marker present; `mode:"full"`, `evidence.command:"r2"`, started == collection; **exactly 1 attempt** (counting `process_runner`: baselines + 1) | same | always run a declared attempt → the count is 2 → red |
| setup/teardown/collection failures never cold | same | three fixtures: the killing failure happens in a fixture's setup; in teardown after a call failure; at import (collection error). Each ends `mode:"full"`, `evidence.command:"declared"`, with 2 attempts | same | accept setup failures as cold → the setup case records `witness-cold` → red |
| lookalike sessionfinish | plugin trust | the existing 820-924 adversary still forces fallback. **Plus:** a `tests/conftest.py` `pytest_sessionfinish` under rootpath with `ASSAY_B105_COVERAGE_SOURCE` set in the child env → `unsupported` → R2 baseline refusal; with all four absent → trusted, `archive_hook_exception_used: true` | same, plus the B106 test | match by basename only → a `sub/tests/conftest.py` lookalike is trusted → red |
| path normalization | plugin | two baselines of the same project materialized under two different temp roots give an equal `hook_fingerprint_sha256` | `tests/test_mutation_witness_unit.py` | use absolute paths → unequal → red |
| collection digest parity | plugin vs `r2_command` | the plugin's `collection_sha256` over parametrized IDs including `test_x[é]` equals `collection_digest` of the sidecar lines | same | char-length netstring → red |
| baseline refusal A5 | runner | (a) a test that passes only with coverage active (`import coverage; assert coverage.Coverage.current()`) → R2 claim `ERROR/BAD_LANE_CONFIG`, payload-free, R3 refused, and R0/R1 claims still present; (b) a `conftest.py` `pytest_collection_modifyitems` that reverses the order only when `pytest_cov` is not loaded → collection mismatch → the same refusal | `tests/test_b110_cold_witness.py` | fall back to a declared campaign → the claim has a payload → red |
| judge identity | mutation | the digest changes when any of `cold_witness_kills`, the transform id, `r2_collection_sha256`, `runtime_fingerprint_sha256` or the ledger sha changes, and the label is `/3`. The existing equality tests (`tests/test_mutation_judge_identity.py:272-462`) stay green | `tests/test_mutation_judge_identity.py` | omit the runtime fingerprint → red |
| state/resume | mutation | a cold campaign resumed with `--resume` re-reads `witness-cold` + evidence and does not re-execute (count 0). A record with the evidence key deleted is rejected and re-executed | `tests/test_b105_mutation_boundaries.py`-style | skip evidence validation → the stale record is replayed → red |
| A8 order | `_run_one` | `--reuse-from` (a v14 prior) + `--cold-witness`: an eligible prior kill replays (1 attempt, `witness-prefix`); an ineligible one goes cold | `tests/test_b106_reuse_and_witness.py` extended | — |
| verdict validity | all | every verdict produced by the new tests passes `assay verify` (`_verify_with_assay_cli` pattern) | all new tests | — |
| plan preview | cli | `assay plan --cold-witness` on the B105 lane → `eligible: true`, `argv_transformed` without `--cov*`. With a `-x` lane → `eligible:false`, refusal `fail-fast option` | `tests/test_b105_cli_boundaries.py` | — |

**Luna's adversarial set must all be present:**
- the exact `pythonpath` override is **not** eligible any more; P1 dropped it and every `-o` is refused;
- first unique call failure stops before the marker;
- a passing candidate reaches the marker;
- setup/teardown/collection failures never become cold kills;
- a lookalike sessionfinish forces fallback;
- duplicate node IDs (parametrize with duplicate IDs via `ids=["a","a"]`) → `duplicates > 0` → baseline refusal;
- mismatched exit statuses: the child exits 2 while the session status is 1, via a conftest `pytest_unconfigure` calling `os._exit(2)` → not cold → declared attempt.

### Degrees of freedom

- Private helper names, and how `_run_attempt` selects the variant.
- The temp-dir layout inside the lane-run plugin dir.
- The wording of refusal messages beyond the asserted substrings.

The following are **not** yours to change: receipt keys, the evidence shape, the decision tables, and the order of judge-identity inputs.

---

## Work

1. **Branch.** Create a worktree under `/workspaces/vbpub/.worktrees/` on `assay-b110-p3b-cold` from `assay-b110-v14`. Confirm that P3a's `r2_command.py` and v14 model exist, and that `grep -n "override-ini" assay.toml` shows nothing in the B105 lanes (P1). If either is missing → BLOCKED.
2. **Red first.** Write `tests/test_b110_cold_witness.py` with the traceability rows. Extend `tests/test_mutation_witness_unit.py` (the `_receipt()` helper gets the new keys) and `tests/test_b106_reuse_and_witness.py`. Run them and record the failure counts.
3. **Plugin.** Add the constants, `_COLD`, `_STARTED`, `_PREFIX_OK`, the collection facts, the hook/runtime fingerprints, `pytest_collectreport` and `pytest_runtest_logstart`, the cold stop, the sidecar writer and the archive exception. Then add the new keys to `_write` **and** `_INTERNAL_RECEIPT_KEYS` together.
4. **Host helpers.** Add `make_attempt_plan(cold=, manifest_path=)`, `ReceiptFacts`, `receipt_facts` and `cold_witness_from_receipt` to `mutation_witness.py`.
5. **`cold_shape_refusal`** with its table.
6. **mutation.py:**
   - the judge identity `/3` plus the new inputs;
   - `run_mutation` kwargs and validation;
   - the two injections;
   - `_run_attempt(cold=, variant=)`;
   - the `_run_one` decision tables;
   - the `_classified_bucket` "unproven PASS → crashed" branch;
   - `_MutantRun.evidence`, `_outcome_of(evidence=)`;
   - the state-record `evidence` write and load, and `_execution_from_state_record` accepting `witness-cold`;
   - the progress additions.

   Import `MutantEvidence` from `verdict`.
7. **runner.py:** the flow steps 1–7. Put the R2 baseline and its refusals **inside** the existing `try` around `run_mutation`, and verify with a test that R3 is refused and R0/R1 survive.
8. **cli.py:** `--cold-witness` and `--r2-manifest` on `run`; `--cold-witness` on `plan`; read both with `getattr(args, …, default)`.
9. **Coverage floor.** Keep 100% line+branch for every touched module under the B105 whole-target lane (the plugin source string is not measured; the host helpers are). Add no `pragma: no cover`; `tests/fixtures/b105-coverage-exclusions.json` must stay byte-identical. Adding a new module requires putting it in **both** `assay.toml` target lists.
10. **DESIGN-GUIDE §7 exception.** The `docs/DESIGN-GUIDE.md:1871` row "Flags … are never *derived* by assay" gains one explicit exception sentence: "Exception (B110, A-470): with the caller's explicit `--cold-witness`, assay derives one R2-only command by removing exactly the recognized pytest-cov options and appending `-p no:pytest_cov`; the transform is versioned, recorded in `judgment.r2.r2_command`, and re-derived by the verifier."
11. Docs sync (below), CHANGES.
12. Focused tests, then the gate, then the report.

## Oracles

Every row of the traceability table is an oracle. Each has its observable, its controlled break as the negative, and the gate (tester-unified). The oracles that must be demonstrated red first are:
- marker-absent on a cold kill;
- attempt count 1 for a survivor;
- declared fallback for setup, teardown and collection failures;
- the payload-free A5 refusal with R0/R1 retained;
- path-normalized hook equality across two roots.

Paste the counts into the report.

### Forbidden oracle patterns (AUTHORING.md §3b, verbatim)

### 3b. What an oracle must NOT contain — paste this into any handoff that asks for tests

Every rule below is the residue of a real incident; the `L`/`PL` refs are the
write-ups in `reference/LESSONS.md`. **If a handoff asks an agent to write
tests, copy this list into it** — an implementation agent has no access to our
incident history and will otherwise reproduce these by default.

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**F. No predicted measurements.** (distilled 2026-09-17 from an incident in a
consuming project's own decision ledger — the specific entry isn't cited here
since a canonical doc shouldn't hard-reference a consumer's private,
renumberable ledger; see that project's own decisions.md around the same
date for the full incident writeup if useful.)
- ✗ A carve or oracle asserting a specific coverage/mutation number, a "missing
  lines" list, or a "this branch is permanently uncoverable" claim computed by
  reasoning about a tool's rendered report instead of running the tool.
- ✗ Trusting `coverage.py`'s rendered "Missing" column as a complete branch-arc
  list — it silently suppresses an arc whose destination line is already
  reported missing elsewhere, so a hand-derived read of the report undercounts
  by exactly that arc. This exact mistake recurred three times independently
  in one wave before being traced to this display artifact.
- ✓ Assert the POLICY requirement instead — the project's coverage target, its
  R0-R3 (or equivalent) testing tier, the design decision — as the oracle.
  Never a predicted number; the number does not exist until the implementer's
  own gate run produces it.
- ✓ If a carve must justify "this is achievable" or "this line is
  unreachable" before dispatch, PROVE it by executing the tool
  (`coverage.py`/`runpy.run_module(mod, run_name="__main__")`, or the
  project's own judge) against real or synthetic stand-in code — never by
  reading a report and reasoning about what it would show.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

## Docs sync

Verify each anchor with `grep -n`.

- **README.md:**
  - :152-160: the judge-identity inputs gain the cold policy, the effective R2 command, the transform, the R2 collection digest, the runtime fingerprint and the ledger digest;
  - :981-988: the B110 "not shipped yet" paragraph becomes the cold-witness explanation, **including its limitation**: a cold kill does not run later tests, which could independently fail, hang or crash, so it is an existential kill witness, not a full-suite receipt.
- **docs/DESIGN-GUIDE.md:**
  - a new "### Cold witness kills and the R2-only command (B110)" section after the B106 section (the B106 section starts at :1015; insert before the next `###`). It explains why one verified call failure is sufficient, why survivors pay the full R2 command, the declared-command fallback, the A5 refusal without fallback, and the fingerprints;
  - the §7 exception (Work step 10);
  - the B105 section (~:1959-1981): replace "proposes" with what shipped.
- **docs/CONSUMERS.md:**
  - :70-92 (the B105/B110 note);
  - add a pasteable `assay run <lane> --cold-witness --resume --state-dir … --r2-manifest …` example beside the B106 worked example (:2757-2800);
  - show how a cold kill, a fully executed survivor and a declared-fallback kill differ in the verdict (`execution.mode`, `evidence.command`).
- **CHANGES.md** `## [Unreleased]` `### Added`.

## Scope / forbid

- **Touch:** `src/assay/{mutation_witness.py,mutation.py,runner.py,cli.py}`, `docs/*`, `README.md`, `CHANGES.md`, and the `tests/**` named above plus new test files. `assay.toml` only if a new module is created (targets lists).
- **Forbid:**
  - `verdict.py`, `verify.py` and the schema (P3a owns them; if a shape is insufficient → BLOCKED);
  - `liveness.py` (P3c);
  - `tools/*` and `run-gate.toml` (P3d);
  - `MUTATION_STATE_SCHEMA_VERSION`;
  - the `ReasonCode` enum;
  - lane schema keys;
  - any change to non-cold behaviour other than the judge-identity label (label `/3` applies to all lanes; resumed v2-label records are re-executed, which is the intended invalidation).

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Run the focused suites serially:
   `nice -n 19 ionice -c3 python -m pytest tests/test_b110_cold_witness.py tests/test_mutation_witness_unit.py tests/test_b106_reuse_and_witness.py tests/test_mutation_judge_identity.py tests/test_b105_mutation_boundaries.py tests/test_b105_cli_boundaries.py tests/test_self_lane.py -q -p no:cacheprovider`
2. Run the gate:
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p3b-gate.log 2>&1; echo EXIT=$?`
3. In a separate step, read `ASSAY_GATE_CONTAINER_EXIT=0` and `ASSAY_REGISTERED_GATE_COMPLETE=1`.
4. Run `python ./run-gate.py self-qualification-preflight`: R0/R1 must still pass at 100% line+branch over every target.
5. **Never** run the `self-qualification` lane. The first real cold campaign is the B110 pilot (plan §7), run by the controller.

## BLOCKED rule

If a named contract cannot be met as specified, STOP. This applies when:
- the P3a model cannot represent a row of the decision tables;
- pytest 9.1.1 does not call `pytest_runtest_logstart` for skipped items;
- `list_plugin_distinfo` lacks `project_name`/`version`;
- a trap cannot be avoided without touching a forbidden file.

Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B110-P3b-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

## Report

Write `nyxloom-trove/reports/assay-B110-P3b-REPORT.md` with:
- the commits;
- the traceability table with actual tests and red-first counts;
- the gate log path and markers;
- the preflight result;
- the measured wall time of one local no-cov baseline versus the coverage baseline, as information only and never an oracle;
- residuals.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`. Do not merge; an independent fresh-session review (never a fork) precedes the controller's `--no-ff` merge into `assay-b110-v14`.
