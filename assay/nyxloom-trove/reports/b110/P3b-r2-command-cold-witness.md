# B110-P3b — R2 command transform, no-coverage R2 baseline, cold witness producer

*Revised 2026-09-28 after round-1, round-2 and round-3 reviews (see REVIEW-2026-09-28-round{1,2,3}.md). Round 3: P3B3-1 (every `LANE_TIMEOUT` source in steps 2–4, including the snapshot-preparation timer, maps to `R2BaselineTimeoutError`), P3B3-2 (the timeout oracle's fake runner raises `TimeoutExpired`, keyed on the manifest env; `deadline=`/`progress_phase=`), and the C22 trust-widening residual. Round 1: P3B-1..P3B-13 and carver decisions C1, C2, C3 (consistency with P6), C10 and C15. Round 2: P3B2-1..P3B2-9 and carver decisions C21 (the R2-baseline timeout is whole-lane), C22 (declared-survivor proof), C23 (the pinned `run_mutation` order) and C30 (P4 in the v14 base; tests found by name after P1).*

| Field | Value |
|---|---|
| Backlog | **B114** (B110 umbrella) |
| Branch | `assay-b110-p3b-cold` off the current `assay-b110-v14` tip. The v14 integration-branch protocol is in `P3a-v14-schema-verify.md`. |
| Depends on | **P3a** merged into `assay-b110-v14` (the model, schema, verify and `r2_command.py`). **P1** in the base: the B105 lanes no longer carry `--override-ini=pythonpath=src`. **P6** merged into the integration line and then into `assay-b110-v14` (plan §11.6 order P6 → v14): C3's unclassified-attempt path (`termination_requested()`, lane-remainder timeouts → `LANE_TIMEOUT`, no record) is P6's, and this package routes through it. **P4 in the v14 base (C30, round-2 P3B2-6).** P4 rewrites the executor loop in `_execute_mutation_jobs` (waves → work queue), which is where this package adds `evidence` to results, state records and `candidate` events. `assay-b110-v14` is never rebased, so "whichever merges second rebases" is impossible. The order is fixed instead: P4 merges into the integration line, and the controller merges the integration line into `assay-b110-v14` **before** this package touches `_execute_mutation_jobs`. Work step 1 checks this and stops with BLOCKED otherwise. The `evidence`/`execution_mode` additions go through P4's reorder buffer unchanged. |
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
- 290-300: `InvalidRejudgeIdError(AssayError)` (the class is at 292). Define beside it, with the same shape:
  - `R2CommandProofError` (`ERROR`/`BAD_LANE_CONFIG`, C1);
  - `R2ManifestWriteError` (`ERROR`/`OUTPUT_WRITE_FAILED`);
  - `R2BaselineTimeoutError` (`BUDGET_EXCEEDED`/`LANE_TIMEOUT`, C21).
- 1052-1160: `judge_sha256`.
- 1273-1402: `_load_validated_state_record`. Malformed execution provenance raises **`MutationStateError`** (1356-1363, pinned by `tests/test_b105_mutation_boundaries.py:225-240`). Only a judge-identity mismatch is the "rejected → re-execute" path, and it is deliberately checked **last** (1364-1401).
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
  - `except mutation.InvalidRejudgeIdError: raise` at **4532** (B094/A-458). The error propagates to the outer handler in `_run_higher_rigor_lane` (`except AssayError` at **5385** → `refuse_all(...)` at 5412), which renders a **whole-lane** refusal with the identical pair on every declared level. `cli.py:1527` renders the same shape for a CLI-level refusal. **P6 has already extended this clause with `CampaignPlanMismatchError`** (it is in this package's base). This package **extends that existing tuple**; it never rewrites it as a literal that would drop P6's entry (round-2 P3B2-7);
  - `except AssayError` (B053/A-409: this renders a **payload-free R2 claim** and refuses R3) at ~4542. **Any** payload-free R2 claim other than `_INDEPENDENT_R2_TERMINALS` placed beside an R0 PASS fails `assay verify`'s `_check_r2_rederivation`. That includes `BUDGET_EXCEEDED/LANE_TIMEOUT`: the round-2 reviewer ran `verify_document` on `r2_budget_exceeded_lane_timeout.json` with the R2 payload and judgment removed, and got "R2 claim status (BUDGET_EXCEEDED, LANE_TIMEOUT) disagrees with … (PASS, None)". So nothing this package raises may reach this handler (P3B2-1, C21).
- 4569-4583 and 4964-5041: `_build_judgment_r2`.
- 5199-5215: `_run_higher_rigor_lane` resolves the `CommandPlan` (`resolve_command_plan`) and so **`env_effective`**. Its `except AssayError` refuses the whole lane before any snapshot. The static refusal table runs here (P3B-5), not in `run_lane`, because `env_effective` does not exist in `run_lane`.
- 5517-5572 and 5983-6050: `run_lane` parameters and the reuse validation (`_refuse_reuse` at 5985).
- 5164 and 5351-5378: the `_run_higher_rigor_lane` plumbing.
- verify.py 2455-2480 (read only): `_INDEPENDENT_R2_TERMINALS` deliberately excludes `BAD_LANE_CONFIG`. A payload-free R2 `ERROR/BAD_LANE_CONFIG` beside a passing R0 **fails `assay verify`**, which is why the A5 refusal must be whole-lane (C1).

**`src/assay/liveness.py`:** 469-494 (the injection precedent: `cli_argv_appended` freezing).

**`src/assay/cli.py`:**
- the `run` parser at 255-328 (`--reuse-from` at 291-300);
- `_cmd_run` at 725 and the `run_lane` call at ~1490-1512;
- `_resolve_state_dir` at 845-878 (the visibility-refusal pattern);
- `_cmd_plan` at 1571-1886 (preview cwd at 1664-1680; `sequential_pytest_supported` at 1804-1817; the payload at 1839-1883).

**`src/assay/isolation.py`:** 670 (`materialize(timeout=)`) and 230-240 (`Snapshot.root` / `project_root`).

**`tests/conftest.py`:** 59-80. This is the B105 archive hook. Its four variables are `ASSAY_B105_COVERAGE_SOURCE`, `ASSAY_B105_COVERAGE_ARCHIVE_DIR`, `ASSAY_B105_SOURCE_COMMIT` and `ASSAY_B105_SOURCE_TREE`. It returns immediately when all four are unset.

**Tests to model on:**
- `tests/test_b106_reuse_and_witness.py` (line numbers at `db85f747`, before P1):
  - 501-548: argv support (stays in this file);
  - 661-677: `_seed_pytest_mutation`.
- **After P1 (C30, round-2 P3B2-5)**, the real-run B106 tests live in `tests/zz_slow/test_b106_witness_real_runs.py`, together with `_seed_pytest_mutation`. Find them by name:
  - `test_replay_requires_a_current_kill_and_falls_back_to_a_full_run`: a real `run_lane` with `tracked_process_runner`;
  - `test_witness_capture_works_with_the_existing_liveness_plugin`: liveness coexistence;
  - `test_custom_sessionfinish_hook_forces_full_suite_fallback`: the lookalike `pytest_sessionfinish` adversary;
  - `test_resume_preserves_witness_prefix_execution_provenance`: resume.

  Model on them; do not add to that file.
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
    # INPUT (P3B-5): argv = transform_argv(plan.argv_declared) + plan.argv_appended,
    # evaluated right after resolve_command_plan (runner.py ~5202), where
    # plan.argv_appended is still exactly the CLI-appended (passthrough) argv; env =
    # that plan's RESOLVED env_effective. So CLI passthrough cannot bypass the table.

@dataclass(frozen=True)
class ReceiptFacts:
    collection_count: int; collection_sha256: str; duplicates: int
    hook_fingerprint_sha256: str; hook_count: int
    runtime_fingerprint_sha256: str | None; config_sha256: str | None
    started_count: int; collection_error: bool

def receipt_facts(receipt: Mapping[str, Any]) -> ReceiptFacts | None
    # None unless manifest_supported is True and every collection/hook field is well-typed
    # (int-not-bool, 64-hex).

def survivor_proof_ok(receipt, *, process_exit_status: int, expected: ReceiptFacts,
                      command: Literal["r2", "declared"]) -> bool
    # P3B-7, revised by C22 (round-2 P3B2-2). True only if ALL hold:
    #   not auxiliary_failure; not earlier_failure; not collection_error;
    #   started_prefix_ok; session_exit_status == 0 and process_exit_status == 0
    #   (int, not bool); started_count == collection_count; receipt_facts(receipt)
    #   matches expected on collection_count, collection_sha256, duplicates == 0,
    #   hook_fingerprint_sha256 and runtime_fingerprint_sha256;
    #   AND, only when command == "r2": not unsupported.
    # command == "declared" (expected = coverage_facts) does NOT require "not
    # unsupported": the declared command carries pytest-cov, whose
    # pytest_runtestloop(wrapper=True) makes the receipt's `unsupported` true on every
    # declared run (the plugin's trust check accepts only _pytest/witness/liveness
    # impls). Hook-fingerprint equality with the coverage baseline, which carries the
    # same wrapper, pins the hook set instead.
    # command == "r2" (expected = r2_facts) keeps "not unsupported".

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

**The full receipt is exactly 25 keys: the existing 10 at `mutation_witness.py:308-321` plus the 15 above (P3B-9).**

Valid, a cold kill:
```json
{"unsupported":false,"target_node_id":null,"target_count":0,"earlier_failure":false,
 "auxiliary_failure":false,"witness_node_id":"tests/test_x.py::test_b","witness_when":"call",
 "witness_outcome":"failed","session_exit_status":1,"stopped_at_target":false,
 "cold_requested":true,"stopped_cold":true,"collection_error":false,"manifest_supported":true,
 "collection_count":3,"collection_sha256":"<64 hex>","collection_duplicates":0,
 "started_count":2,"started_prefix_ok":true,"failed_call_index":1,
 "hook_fingerprint_sha256":"<64 hex>","hook_count":7,"runtime_fingerprint_sha256":"<64 hex>",
 "config_sha256":"<64 hex>","archive_hook_exception_used":false}
```

Invalid:
- the same with `"stopped_cold":true,"failed_call_index":null` → `cold_witness_from_receipt` returns None;
- the same with an extra key `"note":"x"` or with `"collection_duplicates"` deleted → `witness_from_receipt` rejects the key set, so the receipt is unreadable, i.e. uncertain → declared attempt;
- the same with `"session_exit_status":true` (bool) → None.

**Sample hook-fingerprint line** (one per impl; the file is sorted lines joined by `\n`):
`pytest_runtestloop|main|_pytest.main.pytest_runtestloop|_pytest/main.py|-`

**Runtime fingerprint (canonical; computed in `pytest_collection_finish`, P3B-9).** The plugin computes:
```python
import sys, platform, json, hashlib, pytest
dists = sorted({f"{dist.project_name}=={dist.version}"
                for _plugin, dist in config.pluginmanager.list_plugin_distinfo()})  # set: xdist registers 2 entry points from 1 dist
doc = {"python": sys.version,
       "implementation": f"{sys.implementation.name}-{'.'.join(map(str, sys.implementation.version[:3]))}",
       "machine": platform.machine(),
       "pytest": pytest.__version__,
       "plugins": dists}
runtime_fingerprint_sha256 = hashlib.sha256(
    json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
```
- pluggy 1.6's `DistFacade` exposes `project_name` and delegates `version` to the underlying distribution. If either is missing, that is the BLOCKED trigger below.
- With `-p no:pytest_cov`, the blocked pytest-cov distribution is **absent** from `list_plugin_distinfo()`. So the R2 and coverage fingerprints differ by design, and there is no equality rule between them.

**Plugin normalization rules for `hook_fingerprint_sha256` (P3B-4, revised).** For each hook in `HOOK_FINGERPRINT_HOOKS` and each impl, the line is `hook|plugin_token|module|relpath|flags`:
- `plugin_token`, in this order:
  1. if `impl.plugin_name` consists only of digits (pluggy's `str(id(obj))` for an unnamed plugin), the literal `<anon>`, since the id varies per process;
  2. else if it contains `/` or `\` (a conftest registered by path), the literal `<path>`;
  3. else `impl.plugin_name`.
- `module`: `impl.function.__module__`, plus `.` plus `impl.function.__qualname__`, so an `<anon>` token is still distinguished.
- `relpath` is the resolved `co_filename`, normalized in this order:
  1. the witness or liveness plugin module → the module name;
  2. under `config.rootpath` → a POSIX path relative to it;
  3. under the `_pytest` package root → `_pytest/<relative>`;
  4. under `sysconfig.get_paths()["purelib"]` → `purelib/<relative>`; under `"platlib"` → `platlib/<relative>`; under `"stdlib"` → `stdlib/<relative>`. The plugin may import `sysconfig`; it cannot import assay.
  5. otherwise → the absolute resolved POSIX path.
- `flags`: comma-joined, sorted subset of `hookwrapper`, `tryfirst`, `trylast`, `wrapper` (true attributes), or `-`.

Sort the lines, join them with `\n` and take the SHA-256. `hook_count` is the number of lines.
- Per-candidate snapshot paths differ, so rule 2 makes candidates comparable.
- Rule 4 makes the fingerprint stable across two venvs of the same interpreter and packages. The gate builds its run-venv in a fresh `mktemp` directory per invocation, so resumed or imported evidence from an earlier invocation must still match.
- Test it with two different rootpaths **and** with a venv path substituted: monkeypatch `sysconfig.get_paths` in the unit test.

**B105 archive-hook exception.** `trusted()` becomes `trusted(impl, hook_name)`. An impl is additionally trusted **iff** all of the following hold:
- `hook_name == "pytest_sessionfinish"`;
- `module_name == "conftest"`;
- the resolved module file, relative to `config.rootpath`, is exactly `tests/conftest.py`;
- `module_file == code_file`;
- **none** of the four `B105_ARCHIVE_ENV` names is present in `os.environ`.

When it is used, set `_ARCHIVE_EXCEPTION_USED = True`. `test_custom_sessionfinish_hook_forces_full_suite_fallback` (at `db85f747` in `test_b106_reuse_and_witness.py:820-924`; after P1 in `tests/zz_slow/test_b106_witness_real_runs.py`) must still force fallback, because its lookalike is not `tests/conftest.py` under the rootpath with the variables absent.

**Trust widening, to be documented (P3B-12).** The exception is not B105-specific in code. **Any** consumer's `tests/conftest.py` `pytest_sessionfinish`, with those four variables absent, becomes trusted for cold stops **and** for B106 prefix replay. DESIGN-GUIDE (the new cold-witness section) and CONSUMERS must state this, and must state that a consumer whose `tests/conftest.py` `pytest_sessionfinish` changes the session exit status defeats the proof. The mismatched-exit fixture (Luna set) shows the receipt check still refuses the obvious form of that.

**Second trust-widening residual, to be documented with the one above (round-3 C22 note).**
- The coverage baseline's hook set is **pinned but never vetted**. A declared survivor's proof is hook-fingerprint *equality* with the coverage baseline (C22), and that baseline's set legitimately contains pytest-cov's wrapper.
- So a hook implementation that is registered only when pytest-cov (`_cov`) is loaded escapes both the R2 trust check and the fingerprint-equality proof.
- In a qualifying run this can only produce a false **survivor**, which is the safe direction.
- Through P10's declared-command fallback in the ledger audit, it could let a killable mutant be accepted into the ledger.
- DESIGN-GUIDE (cold-witness section) and CONSUMERS state this. P10's audit documentation cross-references it.

**Plugin cold stop.** In `pytest_runtest_logreport`, when `_COLD and _TARGET is None`, on the **first failed report of any phase**:
- if `report.when == "call"`, `_STANDARD_LOOP` holds, there is no earlier/auxiliary/collection failure, `_PREFIX_OK` holds and the node ID is bounded: set `_WITNESS`, `_STOPPED_COLD = True`, `_FAILED_CALL_INDEX = _STARTED - 1`;
- in **every** case: `_SESSION.shouldfail = "assay stopped at the first failure (cold witness)"`.

A teardown failure after the stop still sets `_AUXILIARY_FAILURE` before `_write`, so it disqualifies the receipt. If `_COLD` and `_TARGET` are both set, `unsupported` is true.

**`src/assay/mutation.py` (owner)**
- `judge_sha256(*, tree_sha256, plan, link_paths=(), tool_version=None, cold_witness_kills: bool = False, r2_transform: str | None = None, r2_collection_sha256: str | None = None, r2_hook_fingerprint_sha256: str | None = None, runtime_fingerprint_sha256: str | None = None, coverage_hook_fingerprint_sha256: str | None = None, coverage_runtime_fingerprint_sha256: str | None = None, equivalence_ledger_sha256: str | None = None) -> str`.
  - The current label is `"assay-judge-identity/6"` (A-483/B145 review hardening advanced it to cold-start `/4` and `/5` resource-monitor state).
  - After the existing link section, append in this order: `netstring("1" if cold_witness_kills else "0")`, then `netstring("" if x is None else x)` for each of the **seven** optional values in the signature order above.
  - **Why the hook and coverage facts are folded in (P3B-4).** X5 and X6 compare resumed or imported `evidence` against the **current** invocation's baselines. If a baseline fact the verifier compares against were outside the identity, a changed hook set would leave resumable records that the verifier then rejects. Folding every compared fact into the identity turns that into a judge mismatch, i.e. re-execution, which is the existing safe path. The facts are: the R2 collection sha, the R2 hook sha, the coverage hook sha, and both runtime fingerprints. The coverage collection sha equals the R2 one by the baseline equality refusal.
  - The docstring at 223-225 already says a label bump is the invalidation mechanism.
- `run_mutation(..., cold_witness: bool = False, declared_plan: CommandPlan | None = None, r2_facts: ReceiptFacts | None = None, coverage_facts: ReceiptFacts | None = None, r2_baseline_s: float | None = None, equivalence_ledger_sha256: str | None = None)`.
  - When `cold_witness`: `plan` **is the R2 plan**, and `declared_plan`, `r2_facts` and `coverage_facts` are required (`ValueError` otherwise).
  - The judge identity is computed with `plan=<R2 plan>` and the new inputs.
- `_MutantRun` gains `evidence: MutantEvidence | None = None`. `_outcome_of(job, *, kill_signal=None, execution=None, evidence=None)`.
- The state record gains the optional top-level key `"evidence"` (`MutantEvidence.to_dict()` or null).
  - **Shape errors are corruption (P3B-3).** The loader validates `evidence` through `MutantEvidence(**raw)` with an exact six-key set, inside the same `try` that wraps `_execution_from_state_record` (1356-1363). Any `TypeError`/`ValueError` raises **`MutationStateError`**, exactly like malformed execution provenance today; it is **not** the rejected-record path.
  - **Evidence-requiredness runs last (round-2 P3B2-3).** The judge-identity check is literally the last check in `_load_validated_state_record` (1364-1401), and P6 adds its deadline-binding check. So the rule "under a `cold_witness` campaign, a `survived` or `witness-cold` record whose `evidence` is null or absent raises `MutationStateError`" runs **only after** the judge-identity check (and P6's deadline check) have **passed**.
    - Such a record's identity says it came from a cold campaign, which always writes evidence, so a missing key means corruption.
    - Records from a non-cold campaign, or `/2`-labelled ones, fail the judge check first and re-execute. So do P6-unbound records (P6's rule). Placing the requiredness check among the early shape checks would wrongly turn them into `MutationStateError`.
    - The shape check (`MutantEvidence(**raw)` when the key is present) stays with the early shape checks.
  - `_execution_from_state_record` accepts `witness-cold` with keys exactly `{"mode","witness"}`.
- The `candidate` progress event gains `"execution_mode"`. The `plan` event gains `"cold_witness": bool` and `"r2_baseline_s": float | null` (A7). The event-name vocabulary is unchanged.

**`src/assay/runner.py` / `cli.py`**
- `run_lane(..., cold_witness: bool = False, r2_manifest: Path | None = None)`, threaded through `_run_higher_rigor_lane` into `_run_prepared_lane`.
- CLI: `assay run` gains `--cold-witness` (store_true) and `--r2-manifest PATH`.
  - `--r2-manifest` without `--cold-witness`, or naming a visible tracked path, is refused **exactly like `--state-dir`** (round-2 P3B2-9): a **pre-run `LaneConfigError`** from a `_resolve_r2_manifest` helper beside `_resolve_state_dir` (`cli.py:845-878`). It is `BAD_LANE_CONFIG`, exits 2, **writes no verdict**, and runs nothing. The path must be gitignored or outside the tree.
  - (Only a **write failure during the run** is the whole-lane `R2ManifestWriteError`; see Required flow step 6.)
- `assay plan` gains `--cold-witness`. With it, the payload gains `"cold_witness": {"eligible": bool, "refusal": str | null, "argv_transformed": [..] | null}`. It is computed statically:
  1. `transform_argv`;
  2. then `cold_shape_refusal`;
  3. then `supports_sequential_pytest(transformed + R2_APPENDED, env=…, cwd=<preview cwd from 1664-1680>)`.

  Without the flag the payload is byte-identical to today.

### Static refusal table (`cold_shape_refusal`)

The input is defined in the signature comment above: `transform_argv(plan.argv_declared) + plan.argv_appended` (the CLI passthrough) and the **resolved** `env_effective`, at runner.py ~5202. `-p NAME` is matched in **both** the separate form (`-p`, `NAME`) and the joined form (`-pNAME`), and likewise `-o`/`-c`.

**Single-dash clusters are parsed, not substring-matched (round-2 P3B2-8).** Before the rows below apply, `cold_shape_refusal` expands every token matching `^-[^-]` into `(option, value)` pairs, so `-qprandomly` becomes `-q` plus `-p randomly`, and `-qoaddopts=-n4` becomes `-q` plus `-o addopts=-n4`. The rows are then evaluated on the expanded pairs.

The letter classes are taken from pytest 9.1.1 plus pytest-xdist 3.8.0's parser, as inspected on the host at carve time:
- **Flag letters (no value):** `q`, `v`, `s`, `l`, `x`, `d`, `f`, `V`, `h`.
- **Value letters:** `W`, `c`, `k`, `m`, `n`, `o`, `p`, `r`. The rest of the token is the value; if the rest is empty, the next argv token is.

Walk the letters left to right: a flag letter emits `(-<letter>, None)`; a value letter emits `(-<letter>, value)` and ends the token. Then:
- **Any other letter** refuses with `unrecognized short option cluster <token>`. This is conservative: a new plugin letter can never slip through.
- `-x` → the fail-fast row.
- `-d` / `-f` (xdist dist / looponfail) → `parallel option`.
- `-h` / `-V` → `not a sequential pytest command`.
- `-k`, `-m`, `-r` and `-W` values are allowed. They are selection/report options that the declared lane already carries and that the transform preserves; the collection-manifest equality proves the selection at runtime.

Add a unit test that reads the installed pytest's short options (`_pytest.config.get_config()` parser `_actions`, as the carver did) and asserts the two letter sets above are exactly its flag and value short options. Letter-set drift after a pytest upgrade then goes red instead of silently weakening the table.

| Condition | Reason (substring tests assert) |
|---|---|
| `transform_argv` raises `UnrecognizedCoverageOption` | `unrecognized coverage option <token>` |
| not `pytest …` / `python -m pytest …` per `supports_sequential_pytest`'s parser | `not a sequential pytest command` |
| any `-o`, a joined `-o<anything>` (e.g. `-oaddopts=-x`), `--override-ini`, `--override-ini=*` | `pytest override` |
| `-c`, a joined `-c<file>` (e.g. `-cfoo.ini`), `--config-file*`, `--rootdir*`, `--confcutdir*` | `pytest configuration override` |
| `-x` (including an `x` flag letter inside an expanded cluster), `--exitfirst`, `--maxfail`, `--maxfail=*` | `fail-fast option` |
| a single-dash token with an unknown letter (see the cluster rule above) | `unrecognized short option cluster` |
| `--lf`, `--last-failed`, `--ff`, `--failed-first`, `--nf`, `--new-first`, `--sw`, `--stepwise`, `--stepwise-skip`, `--sw-skip` | `order-changing option` |
| a token starting `--randomly` or `--random-order`; `-p randomly`/`-prandomly`, `-p random_order`/`-prandom_order`, `-p pytest_randomly`/`-ppytest_randomly` (the enabling forms only; `-p no:randomly`, `-pno:randomly` and other `no:` forms stay allowed) | `order-changing option` |
| `-p pytest_cov`, `-ppytest_cov`, `-p pytest-cov`, `-ppytest-cov` (it would re-enable the plugin the transform disables) | `coverage plugin re-enabled` |
| `-n` (any value, separate or joined), `-d`, `-f`, `--numprocesses*`, `--dist*`, `--looponfail*`, `-p xdist`, `-pxdist` | `parallel option` |
| `PYTEST_ADDOPTS` present with a non-empty value, or `PYTEST_PLUGINS` present | `pytest environment option` |
| `COVERAGE_PROCESS_START` or `COVERAGE_PROCESS_CONFIG` present with a non-empty value. The tester image installs `a1_coverage.pth`, so either would re-enable coverage in the child despite `-p no:pytest_cov` | `coverage re-enabled by environment` |
| lane has no R2, R2 is ingested, or the adapter is not python (checked in `run_lane`, lane-level) | `cold witness needs a native python R2 lane` |

Any static refusal is a **whole-lane `ERROR`/`BAD_LANE_CONFIG`** before any execution. The lane-level row is raised in `run_lane` beside `_refuse_reuse` (runner.py:5985). The argv/env rows are raised as an `AssayError(ERROR, BAD_LANE_CONFIG)` immediately after `resolve_command_plan` inside the `try` at ~5202-5210, whose existing `except AssayError` refuses the whole lane before any snapshot.

**C10 note, for P10b (not this package), aligned with plan §3/§11.7 (round-2 P3B2-9):** only **`--equivalence-audit` combined with `--shard`** (or with `--candidates-file`, or without `--cold-witness`) is refused. A lane that merely **declares** `judge.mutation.equivalence_ledger` may still shard, so screens stay shardable. P10b owns that check; nothing here may make it impossible.

**Pinned `run_mutation` order (C23), which this package must preserve.** P6, P7 and P10 all edit the same block of `run_mutation`, in this order:
1. discovery;
2. P6's plan-digest check over the **full** discovered list;
3. P10 ledger placement (only with `--equivalence-audit`);
4. P7 selection or shard;
5. resume lookup.

This package adds only the cold parameters and the judge-identity inputs (step 5's identity comparison). It does not reorder the block.

### Required flow (`_run_prepared_lane`, `cold_witness=True`)

1. **Static checks.**
   - The lane-level row runs in `run_lane`, beside the `--reuse-from` validation (~5983-6050).
   - The argv/env rows run in `_run_higher_rigor_lane` right after `resolve_command_plan` (~5202), inside its `try`, so `refuse_lane` renders the whole-lane refusal.
   - `--cold-witness` with `--shard` is **allowed**. `--reuse-from` with `--shard` stays refused, unchanged.
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
   - `baseline_plan` (~3705) becomes `make_attempt_plan(inject_witness_plugin(<baseline plan after its liveness env>, plugin_dir=<dir>, liveness_plugin_path=<the liveness plugin path injected at ~3648-3662, or None when liveness is inactive>).plan, receipt_path=<dir>/coverage-baseline.json, target_node_id=None)`.
   - **Pin `liveness_plugin_path=` on every `inject_witness_plugin` call** in this package: the coverage baseline, the R2 baseline, `inj_r2` and `inj_declared` (round-2 P3B2-4). The liveness plugin implements `pytest_runtest_logreport` and `pytest_sessionfinish`, and the witness plugin trusts them **only** when `ASSAY_MUTATION_WITNESS_LIVENESS_PLUGIN_PATH` names them. On B105, with liveness auto and therefore active, omitting it would make every R2 baseline `unsupported`, i.e. a whole-lane refusal.
   - R0's recorded `argv_appended`/`env_effective` then show the witness plugin. This is accepted infrastructure injection (A6, the RW-36 precedent). Document it.
4. **Coverage baseline.** The unit runs as today. Afterwards read `coverage_facts = receipt_facts(read_internal_receipt(...))`.
5. **Enter the native R2 branch** (~4295-4449), **inside the existing `try` that wraps `mutation.run_mutation`**. Two kinds of error are raised there, and they route differently:
   - **Transform-proof failures (C1)** raise `R2CommandProofError(message)`: an `AssayError` with `ERROR`/`BAD_LANE_CONFIG`, defined in `mutation.py` beside `InvalidRejudgeIdError`.
     - **Extend** the existing re-raise clause at 4532, which in this base already includes P6's `CampaignPlanMismatchError`, with `mutation.R2CommandProofError`, `mutation.R2ManifestWriteError` and `mutation.R2BaselineTimeoutError`. Result: `except (mutation.InvalidRejudgeIdError, <P6's entry>, mutation.R2CommandProofError, mutation.R2ManifestWriteError, mutation.R2BaselineTimeoutError): raise`. Do not drop P6's entry (P3B2-7).
     - The error then reaches the outer `except AssayError` at 5385, and `refuse_all` renders a **whole-lane** refusal: every declared level (R0…R3) carries `ERROR/BAD_LANE_CONFIG`, and the already-measured R0/R1 results are **discarded**. This is the B094/A-458 precedent.
     - A payload-free R2 `ERROR/BAD_LANE_CONFIG` beside a passing R0 would fail `assay verify` (`_INDEPENDENT_R2_TERMINALS`), and this package must not produce it.
     - **There is no fallback to a declared-command campaign.**
   - **Deadline expiry or termination during the R2 baseline (P3B-6, revised by C21 / round-2 P3B2-1)** is **not** a proof failure, and it is also **whole-lane**.
     - Raise **`R2BaselineTimeoutError(message)`** (`BUDGET_EXCEEDED`/`LANE_TIMEOUT`), chained from the original error, in any of these cases:
       - the R2 baseline unit returns `BUDGET_EXCEEDED`/`LANE_TIMEOUT`;
       - `deadline.remaining()` raises `LANE_TIMEOUT`, including P6's termination path, whose `remaining()` raises on SIGTERM;
       - **any other `AssayError` whose `reason_code is ReasonCode.LANE_TIMEOUT`** is raised inside steps 2–4. This includes the snapshot-preparation timer of `prepared.materialize(timeout=…)` (`isolation.py:699`), which raises `git._p22_timeout` → `AssayError(BUDGET_EXCEEDED, LANE_TIMEOUT)` from `git.py:1168` (raised at :1210/:1310/:1349). Round-3 P3B3-1.
     - **Implementation shape (normative):** wrap steps 2–4 in **one** `try`. Map every `LANE_TIMEOUT` source listed above to `R2BaselineTimeoutError` in a single place. Every other `AssayError` propagates unchanged.
     - It goes through the same extended 4532 tuple, so `refuse_all` renders a whole-lane `BUDGET_EXCEEDED/LANE_TIMEOUT` on every declared level, the shape `cli.py:1527` already produces for a pre-run deadline refusal.
     - **Never** let it reach the `except AssayError` at ~4542, whose payload-free R2 `LANE_TIMEOUT` beside R0 PASS fails verify. A-432 is about R3 and does not accept that shape.
     - Never map a timeout to `BAD_LANE_CONFIG`.

   The steps:
   1. If `coverage_facts is None`, `coverage_facts.duplicates != 0` or `coverage_facts.runtime_fingerprint_sha256 is None`: raise `R2CommandProofError("cold witness: the coverage baseline's collection could not be proven: <why>")`.
   2. Open `with prepared.materialize(timeout=deadline.remaining()) as snap:`, with `cwd = resolve_run_cwd(snap.project_root, r2_plan)`.
   3. If `not supports_sequential_pytest(r2_plan.argv_effective, env=r2_plan.env_effective, cwd=cwd)` (config `addopts`): raise `R2CommandProofError`.
   4. Run the R2 baseline unit through `_execute_snapshot_unit`, mirroring the coverage call at ~3748, with:
      - `plan = make_attempt_plan(inject_witness_plugin(r2_plan, …).plan, receipt_path=<dir>/r2-baseline.json, target_node_id=None, manifest_path=<dir>/r2-manifest.txt)`;
      - `wants_coverage=False` and the coverage fields `None`;
      - the plain `process_runner`, not `LivenessRunner`;
      - `deadline=deadline, progress_phase="r2-baseline"`. `_execute_snapshot_unit` takes `deadline=`, not `timeout=` (`runner.py:2804`), and `progress_phase=` already exists (:2835).

      Its progress `command_finished` phase is `"r2-baseline"`. Only this call passes `manifest_path=`, so only the R2 baseline's env carries `ASSAY_MUTATION_WITNESS_MANIFEST_FILE`; the timeout oracle's fake runner relies on that.
      - A `BUDGET_EXCEEDED`/`LANE_TIMEOUT` result raises `R2BaselineTimeoutError`, the whole-lane route above (C21).
      - Steps 2–4 sit in the single `try` that maps every `LANE_TIMEOUT` source (round-3 P3B3-1).
   5. Raise `R2CommandProofError` if:
      - the outcome is `FAIL` or `ERROR` (`R2 no-coverage baseline did not pass`);
      - `r2_facts = receipt_facts(...)` is None;
      - the R2 receipt says `unsupported` (a non-builtin loop, protocol, report or sessionfinish, other than the archive exception);
      - `runtime_fingerprint_sha256` is None;
      - `duplicates != 0`;
      - `collection_count` differs from `coverage_facts`;
      - `collection_sha256` differs from `coverage_facts` (compared separately);
      - the sidecar's recomputed `collection_digest(lines)` differs from `r2_facts.collection_sha256`.
   6. If `r2_manifest` was given, copy the sidecar there atomically (temp in the destination directory + `os.replace`).
      - If the copy fails, raise `R2ManifestWriteError`, an `AssayError` with `ERROR`/`OUTPUT_WRITE_FAILED`, defined beside `R2CommandProofError`. It is in the same extended re-raise clause at 4532, so it is also a **whole-lane** refusal. A payload-free R2 `OUTPUT_WRITE_FAILED` beside a passing R0 would be unverifiable for the same reason as C1.
      - This is the only `--r2-manifest` failure that happens during the run. Argument refusals (without `--cold-witness`, or a visible tracked path) are pre-run `LaneConfigError`s with no verdict (see the CLI section).
      - Any existing file at the destination is replaced only by the successful `os.replace`. A stale sidecar is never left looking current.
   7. Build `R2Command`:
      - `cwd` is `cwd` relative to `snap.root`, as POSIX;
      - `config_sha256` comes from the R2 receipt;
      - `wall_s = baseline_wall_seconds(r2 result)`;
      - `coverage_baseline` gets the `runtime_fingerprint_sha256` from the coverage receipt, and no `wall_s`. The manifest-only plugin computes the fingerprint with the same function. The carver decided on 2026-09-28 to include it, for P10 OC12. If the coverage receipt's fingerprint is None, refuse exactly like step 5.
   8. Call `run_mutation(plan=r2_plan, cold_witness=True, declared_plan=<the coverage plan, liveness-injected, as candidates use today>, r2_facts=…, coverage_facts=…, r2_baseline_s=…)`.
6. **`_build_judgment_r2(..., cold_witness_kills=True, r2_command=<R2Command or None if the R2 claim is payload-free>)`**. X3/X4 in P3a.
   - **P3B-11:** on the `except AssayError` path (~4542-4583), no `judgment.r2` is built at all today. X4 is therefore practically about the payload-free `INCONCLUSIVE/MUTATION_UNSUPPORTED` claim, which does build a judgment. Pass `r2_command=None` there.
   - The whole-lane refusals (C1, `R2ManifestWriteError`) produce no `judgment.r2` either.
7. **A7:** the auto per-candidate budget and liveness calibration keep using the coverage baseline, unchanged.

### Per-candidate flow (`_execute_mutation_jobs._run_one`, `cold_witness=True`)

Two witness injections share one plugin dir:
- `inj_r2 = inject_witness_plugin(plan, …, liveness_plugin_path=<the candidate liveness plugin path, or None>)`, where `plan` is the R2 plan;
- `inj_declared = inject_witness_plugin(declared_plan, …, liveness_plugin_path=<same>)`.

Pin `liveness_plugin_path=` on both, exactly as on the baselines (round-2 P3B2-4).

`_run_attempt(index, *, target_node_id, prior_verdict_sha256, attempt_name, cold=False, variant="r2"|"declared")` picks the injected plan by `variant`. Every attempt still materializes a **fresh snapshot** in a new process (~2678).

The attempts run in this order:
1. **B106 prefix replay**, if `reuse_witnesses` has the candidate. This is the existing path, run on the **r2** variant. The declared variant carries pytest-cov's `pytest_runtestloop` wrapper, so it is never replay-eligible. The runner's reuse eligibility check at ~3729-3739 must therefore evaluate `r2_plan.argv_effective` when `cold_witness` is set. A replayed kill records `witness-prefix` plus `evidence` (`command:"r2"`, facts if readable, started/failed null; X7). If it returns a run, done.
2. **Cold attempt:** `variant="r2"`, `cold=True`, `attempt_name="cold"`.
3. **Declared full attempt**, only when the decision table says so: `variant="declared"`, `attempt_name="full"`.

**Row 0, which applies before either table (C3, owned by P6, and must be preserved here).** If an attempt is **unclassified**, P6's `_run_attempt` result handling raises `AssayError(BUDGET_EXCEEDED, LANE_TIMEOUT)`. An attempt is unclassified when any of these holds:
- its timeout came from the lane or campaign remainder rather than from `budget_per_candidate`;
- `termination_requested()` is true on **any** exit path, including the liveness hung/timeout paths;
- it ended by −SIGTERM/−SIGINT while termination was requested.

The executor masks the position like an unsubmitted leftover and writes **no state record**; `--resume` re-executes it. This package must not classify, record or retry such an attempt; it lets P6's exception propagate unchanged from both the cold and the declared attempt.

**Row S (C2, cold mode only).** A cold **or** declared attempt that ends with `returncode < 0` (the process was killed by a signal) and is **not** unclassified per row 0 is **never `killed`**. It is `crashed` (`execution: full`; `evidence` with the attempt's `command` if its receipt is readable, else null). There is no further attempt. This row is checked before the FAIL rows below. The existing classifier maps any non-zero return code to FAIL → killed (`runner.py:1301-1340`, `mutation.py:1683-1686`), so an OOM kill or a stray signal would otherwise be a false kill. **Without `--cold-witness`, classification stays byte-identical** apart from P6's row 0.

**Decision table (cold attempt → result).** "Facts match r2" means `receipt_facts` matches `r2_facts` on collection count and sha, duplicates 0, hook sha, runtime sha, and `not collection_error`.

| Cold attempt outcome | Receipt condition | Bucket | `execution` | `evidence` | Further attempt |
|---|---|---|---|---|---|
| FAIL, `returncode > 0` | `cold_witness_from_receipt(...)` is not None | killed | `witness-cold` + witness | `command:"r2"`, facts, `started_count`, `failed_call_index` | none |
| FAIL, `returncode > 0` | anything else (setup/teardown/collection failure, prefix broken, fingerprint mismatch, unreadable receipt, exit statuses not both 1) | — | — | — | **declared full attempt** |
| PASS | `survivor_proof_ok(receipt, process_exit_status=0, expected=r2_facts, command="r2")`: facts match r2, `started_prefix_ok`, not `unsupported`, no auxiliary/earlier failure, session status 0, `started_count == collection_count` | survived | `full` | `command:"r2"`, facts, started/failed null | none (A2) |
| PASS | anything else (e.g. `pytest.exit(returncode=0)` mid-run, `os._exit(0)` leaving no receipt, a prefix break) | — | — | — | **declared full attempt** |
| BUDGET_EXCEEDED (`budget_per_candidate`) / hung / ERROR | any (not row 0) | per `_classify_mutant_result` | `full` | `command:"r2"` facts if `receipt_facts` is readable, else null | none. **Final:** these buckets never pass R2 and are visible, and re-running would only re-roll a hang |

**Declared full attempt → result:**

| Outcome | Receipt | Bucket | `execution` | `evidence` |
|---|---|---|---|---|
| FAIL, `returncode > 0` | any | killed | `full` (plus the witness exactly as the existing full branch at 2840-2854 records it) | `command:"declared"` facts if readable, else null |
| PASS | `survivor_proof_ok(receipt, process_exit_status=0, expected=coverage_facts, command="declared")`: collection count/sha, dup 0, **hook sha and runtime sha equal to the coverage baseline's**, prefix ok, session status 0, `started_count == collection_count`. **`unsupported` is NOT required to be false** (C22, round-2 P3B2-2): pytest-cov's `pytest_runtestloop(wrapper=True)` always sets it on a `--cov` lane, and the hook-fingerprint equality pins the hook set. | survived | `full` | `command:"declared"`, facts |
| PASS | anything else | **crashed** | `full` | `command:"declared"` facts if readable, else null |
| BUDGET_EXCEEDED (`budget_per_candidate`) / hung / ERROR | any (not row 0) | per classifier | `full` | facts if readable, else null |

For the "PASS but unproven" crashed row and row S, build the bucket directly. **Do not** fake a `CommandResult`: add a branch in `_classified_bucket` that takes the attempt's proof status and return code. `judge_mutation` then yields `ERROR/EXEC_FAILED`. It is never a PASS and never a survivor without proof (X6).

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
| static refusals | `mutation_witness.cold_shape_refusal`, `_run_higher_rigor_lane`, `run_lane` | one parametrized case per table row, **including the joined forms** `-oaddopts=-x`, `-cfoo.ini`, `-pxdist`, `-prandomly`, `-ppytest_cov`, the env rows `PYTEST_ADDOPTS=-x` and `COVERAGE_PROCESS_START=.coveragerc`, and a **CLI passthrough** case: `allow_argv_append=true` with `-- -pxdist`. Each → verdict `ERROR/BAD_LANE_CONFIG`, whole lane, no snapshot materialized (a counting `process_runner` sees 0 calls). B105's current argv (after P1) → eligible. `-pno:randomly` → eligible | `tests/test_b110_cold_witness.py` (new) | drop the `-x` cluster check → `-qx` is accepted → red; match `-p` only as a separate token → `-pxdist` accepted → red; check `argv_declared` only → the passthrough case accepted → red |
| R2 plan construction | runner | the recorded R0 `argv_effective` still has `--cov*`; the R2 baseline's argv (read back from a `process_runner` spy) has no `--cov*`, ends with `-p no:pytest_cov` and the witness plugin, **and** `allow_argv_append=false` does not refuse | same | put the transform in `argv_effective` only → the spy sees `--cov` → red |
| cold stop before a marker | plugin + `_run_one` | real pytest project: `test_a` passes, `test_b` fails (killed by the mutant), `test_c` writes a marker file. The marker path is **outside every snapshot**: a `tmp_path` directory passed to the test through a lane `env` entry (P3B-10). Cold kill → `witness-cold`, `started_count==2`, `failed_call_index==1`, **marker absent** | `_seed_pytest_mutation`-style fixture with 3 tests; lane `liveness="false"` so the counting `process_runner` sees every attempt (P3B-10) | disable the `shouldfail` → marker present → red |
| survivor completes as full | same | an unkilled candidate: marker present; `mode:"full"`, `evidence.command:"r2"`, started == collection; **exactly 1 attempt** (counting `process_runner`: baselines + 1) | same | always run a declared attempt → the count is 2 → red |
| survivor proof is complete (P3B-7, C22) | `survivor_proof_ok` | unit, `command="r2"`: a PASS receipt with, in turn, `started_prefix_ok:false`, `unsupported:true`, `session_exit_status:1`, `auxiliary_failure:true`, `started_count == collection_count - 1`. Each → not a survivor. Unit, `command="declared"`: the same list **except** `unsupported:true`, which → **still a survivor** when the hook/runtime facts equal the coverage baseline's; a declared receipt whose `hook_fingerprint_sha256` differs from the coverage baseline's → not a survivor | `tests/test_mutation_witness_unit.py` | check only the collection facts → each case accepted → red; require "not unsupported" for declared → the declared `unsupported:true` case rejected → red |
| declared survivor on a `--cov` lane (C22, round-2 P3B2-2) | `_run_one` + plugin, real run | a lane whose argv carries `--cov=<pkg> --cov-branch` (pytest-cov really loaded). An unkilled candidate whose **cold** attempt is forced uncertain (a conftest makes the cold receipt's prefix break only when `ASSAY_MUTATION_WITNESS_COLD=1`, so the cold attempt is not provable) → the declared attempt PASSes with `unsupported:true` in its receipt → bucket **`survived`**, `evidence.command:"declared"` matching the coverage baseline, 2 attempts, R2 `FAIL/MUTANTS_SURVIVED`; the verdict verifies `[]` | `tests/zz_slow/test_b110_cold_witness_real_runs.py` | require "not unsupported" for declared → the candidate becomes `crashed` → R2 `ERROR/EXEC_FAILED` → red |
| liveness-active cold run (round-2 P3B2-4) | runner + plugin | a lane with `liveness = true` (plugin active) and `--cold-witness`: both baselines prove (no whole-lane refusal), and a killable candidate records `witness-cold` | same | omit `liveness_plugin_path=` on the R2-baseline injection → the liveness hooks are untrusted → `unsupported` → whole-lane refusal → red |
| unproven PASS → crashed (P3B-8) | `_run_one`, `_classified_bucket` | (a) under the mutant, a test calls `pytest.exit("stop", returncode=0)` after the first test; (b) under the mutant, a test calls `os._exit(0)`, so there is no receipt. Each: cold attempt PASS but unproven → declared attempt → PASS but unproven → **crashed**, 2 attempts, R2 `ERROR/EXEC_FAILED`, and the verdict verifies | `tests/test_b110_cold_witness.py` | treat exit 0 as survived → `survived` recorded → red |
| signal → never killed (C2, row S) | `_run_one` | under the mutant, a test sends itself `SIGKILL` (`os.kill(os.getpid(), signal.SIGKILL)`). With `--cold-witness`: `crashed`, **1** attempt, `evidence` null. Without `--cold-witness`: `killed`, byte-identical to today | same | map `returncode < 0` through the FAIL rows → `killed` in cold mode → red |
| cold hung/budget is final (P3B-8) | `_run_one` | a busy-loop mutant with `budget_per_candidate = "3s"` and `liveness="false"` → `budget_exceeded` after exactly **1** cold attempt (no declared attempt) | same | re-run on budget → count 2 → red |
| setup/teardown/collection failures never cold | same | three fixtures: the killing failure happens in a fixture's setup; in teardown after a call failure; at import (collection error). Each ends `mode:"full"`, `evidence.command:"declared"`, with 2 attempts | same | accept setup failures as cold → the setup case records `witness-cold` → red |
| lookalike sessionfinish | plugin trust | the existing adversary `test_custom_sessionfinish_hook_forces_full_suite_fallback` still forces fallback. It lived at `test_b106_reuse_and_witness.py:820-924` at `db85f747` and is **now in `tests/zz_slow/test_b106_witness_real_runs.py`** (P1); find it by name and do not edit it. **Plus:** a `tests/conftest.py` `pytest_sessionfinish` under rootpath with `ASSAY_B105_COVERAGE_SOURCE` set in the child env → `unsupported` → R2 baseline refusal; with all four absent → trusted, `archive_hook_exception_used: true` | `tests/zz_slow/test_b110_cold_witness_real_runs.py`, plus the unchanged B106 adversary | match by basename only → a `sub/tests/conftest.py` lookalike is trusted → red |
| path normalization | plugin | (a) two baselines of the same project materialized under two different temp roots give an equal `hook_fingerprint_sha256`; (b) the same with `sysconfig.get_paths()` monkeypatched to a different `purelib` prefix (a relocated venv) → equal; (c) an unnamed plugin registered twice in two processes → equal (the `<anon>` token) | `tests/test_mutation_witness_unit.py` | use absolute paths → (a)/(b) unequal → red; use the raw pluggy name → (c) unequal → red |
| collection digest parity | plugin vs `r2_command` | the plugin's `collection_sha256` equals `collection_digest` of the sidecar lines on a project with a **non-ASCII function name** `def test_é(): ...` (node ID `tests/test_u.py::test_é`, byte length ≠ character length). Parametrize IDs are ASCII-escaped by pytest 9.1.1 (`_ascii_escaped_by_config`), so `ids=["é"]` would not exercise this (P3B-2) | same | char-length netstring → red |
| baseline refusal A5 (C1) | runner | (a) a test that passes only with coverage active (`import coverage; assert coverage.Coverage.current()`); (b) a `conftest.py` `pytest_collection_modifyitems` that reverses the order only when `pytest_cov` is not loaded → collection mismatch; (c) **duplicates:** the lane argv names the same test file twice with `--keep-duplicates` (pytest 9.1.1 renames duplicate parametrize IDs to `a0`/`a1`, so `ids=["a","a"]` cannot produce duplicates, P3B-2) → `collection_duplicates > 0`. Each → a **whole-lane** `ERROR/BAD_LANE_CONFIG` verdict: every declared claim (R0, R1 if declared, R2, R3 if declared) carries that pair, no R2 payload, and the document passes `assay verify` (`[]`) | `tests/test_b110_cold_witness.py` | render it payload-free on R2 beside a passing R0 → `assay verify` fails → red; fall back to a declared campaign → an R2 payload exists → red |
| R2 baseline timeout (P3B-6, C21, round-2 P3B2-1, round-3 P3B3-1/P3B3-2) | runner | **No wall-clock race** (§3b-A). A `ProcessRunner` is `(argv, *, env, cwd, timeout) -> subprocess.CompletedProcess` (`runner.py:321-323`). It never sees the progress phase and cannot return a `CommandResult`, because `BUDGET_EXCEEDED/LANE_TIMEOUT` comes only from a raised `subprocess.TimeoutExpired` (`runner.py:1255-1263`). So:<br>• **(a)** the fake `process_runner` raises `subprocess.TimeoutExpired(argv, timeout)` for the call whose `env` carries the R2 baseline's manifest-file variable (`ASSAY_MUTATION_WITNESS_MANIFEST_FILE`; only the R2 baseline sets it). It hands every other call, including R0, to the real runner, so R0 still runs a real pytest;<br>• **(b)** monkeypatch the deadline so that `remaining()` raises the `LaneDeadline` `LANE_TIMEOUT` `AssayError` once the R2 baseline begins, which is what P6's termination path does;<br>• **(c)** patch the **second** `prepared.materialize` call, the R2 baseline's, to raise `git._p22_timeout(...)`, i.e. `AssayError(BUDGET_EXCEEDED, LANE_TIMEOUT)` from the snapshot-preparation timer.<br>The R2 baseline unit is called with `deadline=deadline, progress_phase="r2-baseline"` (`_execute_snapshot_unit` takes `deadline=`, not `timeout=`, :2804; `progress_phase=` exists, :2835).<br>Each case gives a **whole-lane** `BUDGET_EXCEEDED/LANE_TIMEOUT` verdict: every declared level carries that pair, there is no R2 payload and no `judgment.r2`, and `assay verify` returns `[]`.<br>Because R0 runs a real pytest, place this test by P1's tier rules: add it to `tests/zz_slow/test_b110_cold_witness_real_runs.py`. | `tests/zz_slow/test_b110_cold_witness_real_runs.py` | let it reach `except AssayError` at ~4542 → payload-free R2 `LANE_TIMEOUT` beside R0 PASS → `assay verify` fails ("disagrees with … (PASS, None)") → red; route it through `R2CommandProofError` → `BAD_LANE_CONFIG` → red |
| `--r2-manifest` (P3B-8, round-2 P3B2-9) | cli/runner | without `--cold-witness` → **pre-run `LaneConfigError`**: exit 2, **no verdict file written**, 0 `process_runner` calls; a tracked visible path → the same pre-run refusal; a gitignored path → the file equals the sidecar byte for byte; an unwritable destination directory at write time → whole-lane `ERROR/OUTPUT_WRITE_FAILED` that verifies `[]` | same | write non-atomically / skip the visibility check / emit a verdict for the argument refusal → red |
| `--cold-witness` + `--shard` (P3B-8) | runner | `--shard 0/2 --cold-witness` → in-shard kills are `witness-cold` with `evidence`, and the verdict verifies | same | refuse the combination → red |
| A6 recorded R0 (P3B-8) | runner | with `--cold-witness`, the verdict's top-level `argv_appended` contains `-p assay_mutation_witness_plugin`, `env_effective` shows the witness plugin dir on `PYTHONPATH`, and the verdict verifies | same | inject into a copy that R0 never records → red |
| judge identity | mutation | the digest changes when any of `cold_witness_kills`, the transform id, `r2_collection_sha256`, `r2_hook_fingerprint_sha256`, `runtime_fingerprint_sha256`, `coverage_hook_fingerprint_sha256`, `coverage_runtime_fingerprint_sha256` or the ledger sha changes, and the combined v14 label is `/6` (A-482 passthrough fingerprints and A-483/B145 resource-evidence compatibility are included in that version; the original `/4` target was superseded). The existing equality tests (`tests/test_mutation_judge_identity.py:272-462`) stay green | `tests/test_mutation_judge_identity.py` | omit the hook sha (P3B-4) → a changed hook set leaves resumable records → red |
| state/resume | mutation | a cold campaign resumed with `--resume` re-reads `witness-cold` + evidence and does not re-execute (count 0). A record with the `evidence` key deleted, or with a 5-key evidence object, raises **`MutationStateError`** (P3B-3; like `tests/test_b105_mutation_boundaries.py:225-240`). A record from a non-cold campaign is judge-rejected and re-executes | `tests/test_b105_mutation_boundaries.py`-style | route shape errors to "rejected → re-execute" → the corruption is silently re-run → red |
| resume across a relocated venv (P3B-4) | plugin + mutation | combined axis: `--resume` with `sysconfig` paths relocated between the two invocations **and** an unnamed conftest plugin **and** a declared survivor → count 0 re-executions, and the consolidated verdict verifies `[]` | same | absolute site-packages paths → every record judge-mismatches (or the verdict fails X6) → red |
| A8 order | `_run_one` | `--reuse-from` (a v14 prior) + `--cold-witness`: an eligible prior kill replays (1 attempt, `witness-prefix`); an ineligible one goes cold | `tests/zz_slow/test_b110_cold_witness_real_runs.py` (a real run). P1's exact-name pin forbids adding it to `zz_slow/test_b106_witness_real_runs.py`. | — |
| static cluster parsing (round-2 P3B2-8) | `cold_shape_refusal` | `-qprandomly` → `order-changing option`; `-qoaddopts=-n4` → `pytest override`; `-qx` → `fail-fast option`; `-qn4` → `parallel option`; `-qZ` → `unrecognized short option cluster`; `-qk expr` / `-qkexpr` → eligible; plus the letter-set drift test against the installed pytest parser | `tests/test_b110_cold_witness.py` | substring-match clusters → `-qprandomly` accepted → red |
| verdict validity | all | every verdict produced by the new tests passes `assay verify` (`_verify_with_assay_cli` pattern) | all new tests | — |
| plan preview | cli | `assay plan --cold-witness` on the B105 lane → `eligible: true`, `argv_transformed` without `--cov*`. With a `-x` lane → `eligible:false`, refusal `fail-fast option` | `tests/test_b105_cli_boundaries.py` | — |

**Luna's adversarial set must all be present:**
- the exact `pythonpath` override is **not** eligible any more; P1 dropped it and every `-o` is refused;
- first unique call failure stops before the marker;
- a passing candidate reaches the marker;
- setup/teardown/collection failures never become cold kills;
- a lookalike sessionfinish forces fallback;
- duplicate node IDs, produced with `--keep-duplicates` and the same test path given twice (not `ids=["a","a"]`, which pytest renames) → `duplicates > 0` → whole-lane refusal;
- mismatched exit statuses: the child exits 2 while the session status is 1. A conftest records `exitstatus` in `pytest_sessionfinish` and calls `os._exit(2)` in `pytest_unconfigure` **only when the recorded status is 1**, so the passing baselines are unaffected (P3B-10) → not cold → declared attempt.

### Degrees of freedom

- Private helper names, and how `_run_attempt` selects the variant.
- The temp-dir layout inside the lane-run plugin dir.
- The wording of refusal messages beyond the asserted substrings.

The following are **not** yours to change: receipt keys, the evidence shape, the decision tables, and the order of judge-identity inputs.

---

## Work

1. **Branch.** Create a worktree under `/workspaces/vbpub/.worktrees/` on `assay-b110-p3b-cold` from `assay-b110-v14`. Confirm all of the following:
   - P3a's `r2_command.py` and the v14 model exist;
   - `grep -n "override-ini" assay.toml` shows nothing in the B105 lanes (P1), and `tests/zz_slow/` plus `tests/test_suite_layout.py` exist (P1);
   - P6's `termination_requested()` and the unclassified-attempt `LANE_TIMEOUT` path exist in `mutation._run_attempt` (C3), and P6's `CampaignPlanMismatchError` is in the `runner.py` ~4532 re-raise tuple;
   - **P4 is in the base (C30, round-2 P3B2-6):** `_execute_mutation_jobs` is P4's bounded work queue with its reorder buffer, not the fully joined waves (`mutation.py:2889-2902` at `db85f747`). Check by `git log --oneline -- src/assay/mutation.py` for P4's merge and by the absence of the `wave = list(range(index, min(index + jobs, total)))` line.

   If any is missing → BLOCKED. Do **not** touch `_execute_mutation_jobs` before P4 is in the base.
1a. **Tracer bullet (P3B-9)**, before any plugin code. In a scratch directory **outside** the repository, run a 3-test pytest project serially under `nice -n 19 ionice -c3`: test 2 fails, test 3 writes a marker. Use the installed pytest 9.1.1 and pytest-cov, with a throwaway `conftest.py` plugin that sets `session.shouldfail` on the first failed call report. Confirm and record in the report:
    - (i) `python -m pytest -p no:pytest_cov …` has pytest-cov absent from `config.pluginmanager.list_plugin_distinfo()` and no `pytest_cov` impl on `pytest_runtestloop`;
    - (ii) `shouldfail` stops the session before test 3 (the marker is absent), and the session exit status is 1;
    - (iii) `pytest_runtest_logstart` is called for a skipped item;
    - (iv) `DistFacade` exposes `project_name` and `version`.

   Any "no" is the matching BLOCKED trigger. This is not a gate and needs no container.
2. **Red first.** Test placement follows P1's tier rules (round-2 P3B2-5):
   - **Fast tier:** `tests/test_b110_cold_witness.py`. It holds the rows that use a fake or counting `process_runner` or no subprocess: static refusals, cluster parsing, the R2-baseline timeout via the fake runner, the whole-lane A5 shape where it can be driven by a fake runner, the judge identity, and state/resume shapes.
   - **Slow tier:** the rows that start real pytest subprocesses per attempt go into a **new** `tests/zz_slow/test_b110_cold_witness_real_runs.py`:
     - the cold stop before a marker;
     - the survivor as a full run;
     - the declared survivor on a `--cov` lane;
     - liveness-active;
     - the unproven-PASS and SIGKILL rows;
     - setup/teardown/collection;
     - lookalike sessionfinish;
     - path normalization across real roots;
     - collection-digest parity;
     - the A5 real baselines;
     - `--r2-manifest` writes;
     - `--shard`;
     - A6;
     - resume across a relocated venv;
     - A8.

     Add its basename to P1's `EXPECTED_SLOW_FILES` in `tests/test_suite_layout.py` (the only edit to that file), and keep basenames unique. **Do not add tests to** `tests/zz_slow/test_b106_witness_real_runs.py`: P1's `EXPECTED_SPLIT_TESTS` pins its exact test names.
   - Extend `tests/test_mutation_witness_unit.py`: the `_receipt()` helper gets the new keys. Extend the fast `tests/test_b106_reuse_and_witness.py` only for unit-level receipt/argv cases.
   - Run them all and record the failure counts.
3. **Plugin.** Add the constants, `_COLD`, `_STARTED`, `_PREFIX_OK`, the collection facts, the hook/runtime fingerprints, `pytest_collectreport` and `pytest_runtest_logstart`, the cold stop, the sidecar writer and the archive exception. Then add the new keys to `_write` **and** `_INTERNAL_RECEIPT_KEYS` together.
4. **Host helpers.** Add `make_attempt_plan(cold=, manifest_path=)`, `ReceiptFacts`, `receipt_facts` and `cold_witness_from_receipt` to `mutation_witness.py`.
5. **`cold_shape_refusal`** with its table.
6. **mutation.py:**
   - `R2CommandProofError`, `R2ManifestWriteError` and `R2BaselineTimeoutError` beside `InvalidRejudgeIdError`;
  - the judge identity `/6` plus the seven new optional inputs; native candidate records also carry the current B145 cgroup event evidence;
   - `run_mutation` kwargs and validation;
   - `survivor_proof_ok`, and row S (C2) in the attempt classification;
   - the two injections;
   - `_run_attempt(cold=, variant=)`;
   - the `_run_one` decision tables;
   - the `_classified_bucket` "unproven PASS → crashed" branch;
   - `_MutantRun.evidence`, `_outcome_of(evidence=)`;
   - the state-record `evidence` write and load, and `_execution_from_state_record` accepting `witness-cold`;
   - the progress additions.

   Import `MutantEvidence` from `verdict`.
7. **runner.py:** the flow steps 1–7.
   - Place the argv/env static check inside the `resolve_command_plan` `try` (~5202).
   - Put the R2 baseline and its refusals **inside** the existing `try` around `run_mutation`. **Extend** (do not rewrite) the 4532 re-raise tuple, which already holds P6's `CampaignPlanMismatchError`, with `R2CommandProofError`, `R2ManifestWriteError` and `R2BaselineTimeoutError`.
   - Verify with tests:
     - a proof failure is a **whole-lane** `ERROR/BAD_LANE_CONFIG` whose verdict passes `assay verify`;
     - an R2-baseline timeout or termination is a **whole-lane** `BUDGET_EXCEEDED/LANE_TIMEOUT` whose verdict passes `assay verify` (C1, C21);
     - P6's plan-mismatch refusal still works (its existing test stays green).
8. **cli.py:**
   - `--cold-witness` and `--r2-manifest` on `run`, and `--cold-witness` on `plan`. Read both with `getattr(args, …, default)`.
   - `_resolve_r2_manifest` goes beside `_resolve_state_dir`. It is the pre-run `LaneConfigError` refusal for the argument (no verdict).
9. **Coverage floor.** Keep 100% line+branch for every touched module under the B105 whole-target lane (the plugin source string is not measured; the host helpers are). Add no `pragma: no cover`; `tests/fixtures/b105-coverage-exclusions.json` must stay byte-identical. Adding a new module requires putting it in **both** `assay.toml` target lists. **Line-shift trap:** that fixture pins `mutation.py` TYPE_CHECKING lines (148/155/156 at `db85f747`, possibly moved by earlier packages). Add no line above them; put new imports and constants below.
9a. **Dataclass contract (C15).** `ReceiptFacts` is new and `_MutantRun` gains `evidence`, so the dataclass contract changes. Regenerate `tests/fixtures/dataclass-contract.json` in the same commit with P1's command, `cd assay && PYTHONPATH=src:tests python tests/test_dataclass_contract.py > tests/fixtures/dataclass-contract.json`, and review the diff: only these classes may change.
10. **DESIGN-GUIDE §7 exception.** The `docs/DESIGN-GUIDE.md:1871` row "Flags … are never *derived* by assay" gains one explicit exception sentence: "Exception (B110, A-470): with the caller's explicit `--cold-witness`, assay derives one R2-only command by removing exactly the recognized pytest-cov options and appending `-p no:pytest_cov`; the transform is versioned, recorded in `judgment.r2.r2_command`, and re-derived by the verifier."
11. Docs sync (below), CHANGES.
12. Focused tests, then the gate, then the report.

## Oracles

Every row of the traceability table is an oracle. Each has its observable, its controlled break as the negative, and the gate (tester-unified). The oracles that must be demonstrated red first are:
- marker-absent on a cold kill;
- attempt count 1 for a survivor;
- declared fallback for setup, teardown and collection failures;
- the **whole-lane** A5 refusal that verifies `[]` (C1);
- the **whole-lane** R2-baseline timeout that verifies `[]` (C21);
- the declared survivor on a `--cov` lane (C22);
- the unproven-PASS → `crashed` cases and the SIGKILL → `crashed` case (C2);
- path-normalized hook equality across two roots and a relocated venv.

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
  - :152-160: the judge-identity inputs gain exactly A-470's canonical list:
    - the effective R2 command (`plan=r2_plan`);
    - `cold_witness_kills`;
    - the transform id;
    - the R2 baseline's `collection_sha256`, `hook_fingerprint_sha256` and `runtime_fingerprint_sha256`;
    - the coverage baseline's `hook_fingerprint_sha256` and `runtime_fingerprint_sha256`;
    - the ledger sha256, whenever a ledger is declared;
  - :981-988: the B110 "not shipped yet" paragraph becomes the cold-witness explanation, **including its limitation**: a cold kill does not run later tests, which could independently fail, hang or crash, so it is an existential kill witness, not a full-suite receipt.
- **docs/DESIGN-GUIDE.md:**
  - a new "### Cold witness kills and the R2-only command (B110)" section after the B106 section (the B106 section starts at :1015; insert before the next `###`). It explains:
    - why one verified call failure is sufficient;
    - why survivors pay the full R2 command and what the survivor proof requires;
    - the declared-command fallback;
    - the A5 proof refusal is **whole-lane**, with no fallback and discarded R0/R1 results (C1, A-458);
    - an R2-baseline timeout or termination is a **whole-lane** `BUDGET_EXCEEDED/LANE_TIMEOUT`, not a config error, because an R2-only timeout beside R0 PASS is unverifiable (C21);
    - a declared survivor is proven by fingerprint equality with the coverage baseline, not by "not unsupported", because of pytest-cov's loop wrapper (C22);
    - in cold mode a signal-terminated attempt is `crashed`, never `killed` (C2);
    - unclassified attempts are left to P6 (C3);
    - the hook and runtime fingerprints with their normalization;
    - which baseline facts are in the judge identity;
    - the archive-hook trust widening (P3B-12);
  - the §7 exception (Work step 10);
  - the B105 section (~:1959-1981): replace "proposes" with what shipped.
- **docs/CONSUMERS.md:**
  - :70-92 (the B105/B110 note);
  - add a pasteable `assay run <lane> --cold-witness --resume --state-dir … --r2-manifest …` example beside the B106 worked example (:2757-2800);
  - show how a cold kill, a fully executed survivor and a declared-fallback kill differ in the verdict (`execution.mode`, `evidence.command`).
- **CHANGES.md** `## [Unreleased]` `### Added`.

## Scope / forbid

- **Touch:**
  - `src/assay/{mutation_witness.py,mutation.py,runner.py,cli.py}`, `docs/*`, `README.md`, `CHANGES.md`;
  - the `tests/**` named above plus new test files: `tests/test_b110_cold_witness.py` and `tests/zz_slow/test_b110_cold_witness_real_runs.py`;
  - `tests/fixtures/dataclass-contract.json`, regenerated with P1's command (C15);
  - `tests/test_suite_layout.py`, **only** to add the new zz_slow basename to `EXPECTED_SLOW_FILES` (round-2 P3B2-5);
  - `assay.toml` only if a new module is created (the targets lists).
  - Do **not** edit `tests/zz_slow/test_b106_witness_real_runs.py`, whose names P1 pins.
- **Forbid:**
  - `verdict.py`, `verify.py` and the schema (P3a owns them; if a shape is insufficient → BLOCKED);
  - `liveness.py` (P3c);
  - `tools/*` and `run-gate.toml` (P3d);
  - `MUTATION_STATE_SCHEMA_VERSION`;
  - the `ReasonCode` enum;
  - lane schema keys;
  - any change to non-cold behaviour other than the judge-identity label (the current label `/6` applies to all lanes; resumed older-label records are re-executed, which is the intended invalidation). Row S (C2) is cold-mode only.
  - P6's unclassified-attempt logic in `_run_attempt` (C3): route through it, do not re-implement or bypass it.
  - `verify.py`'s terminal sets (C1).

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Run the focused suites serially:
   `nice -n 19 ionice -c3 python -m pytest tests/test_b110_cold_witness.py tests/test_mutation_witness_unit.py tests/test_b106_reuse_and_witness.py tests/test_mutation_judge_identity.py tests/test_b105_mutation_boundaries.py tests/test_b105_cli_boundaries.py tests/test_dataclass_contract.py tests/test_suite_layout.py tests/test_self_lane.py -q -p no:cacheprovider`
   Then run the slow tier files once, serially: `nice -n 19 ionice -c3 python -m pytest tests/zz_slow/test_b110_cold_witness_real_runs.py tests/zz_slow/test_b106_witness_real_runs.py -q -p no:cacheprovider`. The second file holds the unchanged B106 lookalike adversary.
2. Run the gate:
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p3b-gate.log 2>&1; echo EXIT=$?`
3. In a separate step, read `ASSAY_GATE_CONTAINER_EXIT=0` and `ASSAY_REGISTERED_GATE_COMPLETE=1`.
4. Run `python ./run-gate.py self-qualification-preflight`: R0/R1 must still pass at 100% line+branch over every target.
5. **Never** run the `self-qualification` lane. The first real cold campaign is the B110 pilot (plan §7), run by the controller.

## BLOCKED rule

If a named contract cannot be met as specified, STOP. This applies when:
- the P3a model cannot represent a row of the decision tables;
- the step-1a tracer bullet contradicts a premise:
  - `-p no:pytest_cov` does not remove pytest-cov's hooks and distribution;
  - `shouldfail` does not stop before the marker;
  - pytest 9.1.1 does not call `pytest_runtest_logstart` for skipped items;
  - `list_plugin_distinfo`'s entries lack `project_name`/`version`;
- P6's C3 path, or its `CampaignPlanMismatchError` tuple entry, is not in the base;
- P4's work-queue executor is not in the base when you reach `_execute_mutation_jobs` (C30);
- a test the brief names is not where P1's move list puts it (a missing P1 move);
- the cluster-parsing letter-set test disagrees with the installed pytest parser. Record the observed sets; do not widen the table by guess;
- a trap cannot be avoided without touching a forbidden file.

Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B110-P3b-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

## Report

Write `nyxloom-trove/reports/assay-B110-P3b-REPORT.md` with:
- the commits;
- the traceability table with actual tests and red-first counts;
- the gate log path and markers;
- the preflight result;
- the step-1a tracer-bullet results;
- **no** measured full-suite wall times (P3B-13). Do not run the full no-cov suite on this shared host to measure it. The first no-cov baseline time comes from the controller's B110 pilot gate log;
- residuals.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`. Do not merge; an independent fresh-session review (never a fork) precedes the controller's `--no-ff` merge into `assay-b110-v14`.
