# One store-combine: `combine(sources)` with five named policies (P-assay-12)

Status: design, 2026-11-24 (P-assay-12; closes B565 with P-assay-10, folds B544). Decision D-A10, design review H5. Base `ba15e8dda`. All line references are to that base.

## 0. Facts that change the brief

- **`--carry-forward` already composes with `--shard`.** `fddaa3bfe` ("compose carry-forward across campaigns") removed the refusal H5 cites. Each shard carries forward its own `selected_jobs` (`mutation.py:3183-3192`). This is pinned by `test_f17_carry_forward_shards_merge_with_provenance_intact` (`tests/core/test_mutation_carry_forward.py:1454`) and documented at `CONSUMERS.md:2120-2123`. `--carry-forward` also composes with `--reuse-from` (`:1556`) and `--candidates-file` (`:1579`). So `DESIGN-SUBFILE-CANDIDATE-IDENTITY.md:224` ("mutually exclusive") is stale and gets fixed here.
- **Only two WP3a refusals remain:** `--carry-forward`+`--carry-over` and `--carry-forward`+`--carry-over-reason` (`cli.py:2388-2395`). §3 lists the full set.
- **Retiring the B106 prefix path cannot keep CLI behaviour unchanged.** It is the only producer of `execution.mode = "witness-prefix"` (`mutation.py:4153-4186`, `4222-4245`), and 11 existing tests assert that this replay happens (§4). The plan's two acceptance lines therefore conflict. §8 D1 resolves it: P-assay-12 adds the hint adapter, and P-assay-13 deletes the prefix path along with its tests.
- **`capabilities --json` pins the policy map exactly** (`tests/core/test_capabilities.py:81-87`), and `--carry-forward` is missing from it (`cli.py:183-189`). Listing the policies changes that one test by design (§6).
- **The combinators are already in one place.** P-assay-11 moved them into `mutation_store.py`: `_validate_shard_stores` `:3206`, `_carry_over_records` `:3361`, `_carry_forward_records` `:2933`. Their caller is `mutation.py:3008-3230`.

## 1. Policies

Fixed precedence = row order. For each candidate, the first policy that yields a record wins. Bucket counts are always re-derived from the run's final record set (`merge_mutations`, `mutation_store.py:2110`; `assay verify`). The per-policy numbers in the last column are progress telemetry only.

| Policy | Sources | Winner per candidate | Judge requirement | Provenance written | Counters (event) | `--shard` |
|---|---|---|---|---|---|---|
| `same-judge` | own `--state-dir` (`--resume`), then `--merge-state` dirs in CLI order. Each dir: flat record, then `judges/*` (`:3344-3358`) | own flat record, else own current-judge generation, promoted (`mutation.py:3056-3093`); then the first merge-source record that resumes (`:3448-3462`). Rejudge-named and resource-changed sensitive buckets re-execute | effective judge = run judge (`record_effective_judge`, `:601`), same campaign deadline | none; copied verbatim | `resume`; `carry_over.copied_total` | yes |
| `strict-shard` | exactly N `--merge-shard-store` dirs, each with a `shard.manifest` (`:3127-3203`) | the proved owner shard. Copied through the same-judge path after own-store resume (`:3405-3409`) | manifest judge and every record = run judge (`:3264-3313`) | none | `shard_merge.records_per_store` | **no** |
| `owner-manifest` (new, B544) | one `--merge-manifest FILE` naming owner stores (§2) | **only** the designated owner's flat record. It is materialized into the target before own-store resume, so it also replaces a differing same-judge record already in the target (D3) | manifest `judge_sha256` and every owner record = run judge | none | new `merge_manifest` event: `owners`, `non_owner_duplicates`, `unplanned_records`, `target_replaced` | **no** |
| `import-unverified` | `--carry-over` dirs, with `--carry-over-reason` | first valid `killed`/`survived` record of any judge, in source order (`:3331-3341`, `:3463-3465`). A same-judge record found here is copied, not imported (`:3476-3482`) | none. The operator reason is required | `carry_over` v2 + `provenance{kind: imported, match: exact-id, reason}`; `judge_sha256` kept (`:640-667`, `:3483-3497`) | `carry_over.imported_total`, `from_judge_counts`, `refused_*` | yes (own slice) |
| `carry-forward` | `--carry-forward` dirs inside the checkout | across all sources: any kill claim (exact-id, locus or window) → requeued for a full run, with no record (`:3021-3031`). Else the best exact-id/locus survivor by rank (`:2696`, `:3034-3042`). Same-judge exact-id → left to resume (`:3002-3012`) | earlier judge | survivors: `carry_over` v2 + `provenance{kind: carried, match, reason: "carry-forward"}`; a chain keeps the original executed record (`:3061-3084`) | `carry_forward.*` (`mutation.py:3207-3229`) | yes (`selected_jobs`) |

`--reuse-from` is **not** a combine policy. It is a hint source (capability value `hint`, unchanged): it never writes a record (§4).

## 2. Flags → policies (final surface)

| Flag (`assay run`) | Policy | Arity / pairing |
|---|---|---|
| `--resume` | `same-judge` (own store) | switch; required by every row below |
| `--merge-state DIR` | `same-judge` | repeatable |
| `--merge-shard-store DIR` | `strict-shard` | repeatable, one per shard 0..N-1 |
| run-gate `--merge-shards N` | `strict-shard` | run-gate flag only: it expands to N assay `--merge-shard-store` (CONSUMERS.md:2170). Assay has no such flag |
| **`--merge-manifest FILE`** (new) | `owner-manifest` | single. The name matches run-gate's design (`DESIGN-compute-hosts-and-campaigns-2026-11.md:758`) |
| `--carry-over DIR` + `--carry-over-reason TEXT` | `import-unverified` | repeatable dirs; the reason is required, ≤1024 bytes (`mutation_store.py:409-424`) |
| `--carry-forward DIR` | `carry-forward` | repeatable; must resolve inside the checkout (`cli.py:2336-2357`) |
| `--reuse-from VERDICT` | hint source | single |

There is no generic `--combine POLICY` flag: the aliases are the surface. `assay plan` is unchanged: `--list-pending`, `--state-dir`, `--judge`, `--carry-forward`, `--reuse-from`.

**`assay-merge-manifest/1`.** It is a closed JSON object, bounded to 16 MiB, and duplicate JSON keys are refused:

```json
{"schema": "assay-merge-manifest/1",
 "judge_sha256": "<64 hex>",
 "stores": [{"path": "<dir, relative to the manifest's directory or absolute>",
             "candidates": ["<candidate_id>", "..."]}]}
```

Ownership: each listed id is owned by its store. Every planned id must be owned exactly once.

- The owner record is the store's flat `<id>.json`. Generations are not consulted. It is validated with `_load_validated_state_record` and the campaign-deadline check, like `_strict_record` (`mutation.py:3011-3018`).
- A record for an id that the store does not own is a non-owner duplicate. It is ignored and counted, whatever its timestamps.
- A record for an unplanned id is ignored and counted.
- Written bytes are those of the owner records re-serialized by `_write_mutation_state_record`. Nothing is time-stamped, so reruns are byte-identical.

Refusal tokens. The message form is `merge manifest refused: <token>: <detail>`, up to 20 problems, sorted by (token, id). Every refusal is `ERROR`/`BAD_LANE_CONFIG`, exit 2. No new `ReasonCode` is added (D-A12).

- At parse time, before any work (`LaneConfigError`): `malformed-manifest`, `missing-store`, `duplicate-store`, `duplicate-owner` (an id listed twice in one store or across stores).
- At plan and judge time, where strict-shard validates today (`mutation.py:3008`; `MergeManifestError(InvalidCandidateSelectionError)`, already rendered whole-lane by `runner.py:6459-6475`): `unknown-candidate`, `unowned-candidate`, `judge-mismatch` (the manifest's judge, or an owner record that does not resume under the run judge), `missing-owner-record`, `invalid-owner-record`.

## 3. Refusals → preconditions

**Rule for the implementer:** no existing message string changes, no existing check is reordered, and new checks are appended after the existing ones. That keeps every existing combination hitting the same first refusal, byte for byte, with exit 2. Tests match substrings (`in err` / `match=`), but byte identity is the contract.

| Today (file:line) | Replaced by precondition | Pinned by |
|---|---|---|
| T1 any flex flag on a non-native-R2 lane `cli.py:2381-2387`; `--reuse-from` ditto `runner.py:8373-8384`, plan `cli.py:4621-4622` | all policies: native R2 lane | `test_flex_r2_edges.py:584`; `test_mutation_carry_forward.py:1310`; `test_b105_runner_boundaries.py:1058` |
| T5 a store flag without `--resume` `cli.py:2408-2416`; library `mutation.py:2585-2588` | all store policies: `--resume` (every policy writes into the run's own store) | `test_mutation_carry_forward.py:1239`; `test_flex_r2_shard_merge.py:233`; `test_flex_r2_edges.py:530` |
| T7/T8/T9 dir missing `cli.py:2427-2454`, outside the checkout `cli.py:2350-2355` | per source: exists; carry-forward: inside the checkout | `test_flex_r2_edges.py:603`; `test_flex_r2_shard_merge.py:98`; `test_mutation_carry_forward.py:1049-1055,1313` |
| T10/T11 reason pairing and bounds `mutation_store.py:409-424` | `import-unverified`: requires the reason; the reason requires an `import-unverified` source | `test_flex_r2_edges.py:85-107` |
| T3 `--carry-forward`+`--carry-over-reason` `cli.py:2392-2395` | same precondition (the reason belongs to `import-unverified` only); kept as its own message | `test_mutation_carry_forward.py:1242` |
| T17 strict-shard content `mutation_store.py:3206-3328` | `strict-shard`: the N-store proof (unchanged) | `test_flex_r2_shard_merge.py:98-359` |
| T6 `--merge-shard-store`+`--shard`/`--only`/`--exclude` `cli.py:2417-2426`; T12 `mutation_store.py:425-428`; `mutation.py:2599-2603` | **conflict C2** | `test_flex_r2_shard_merge.py:326,333` |
| T2 `--carry-forward`+`--carry-over` `cli.py:2388-2391` | **conflict C1** | `test_mutation_carry_forward.py:1240` |
| T4 `--candidates-file`+{carry-over, merge-state, merge-shard-store, reason, only, exclude} `cli.py:2396-2407`; T13 +`--reuse-from` `cli.py:2564` | **conflict C4** | `test_flex_r2_edges.py:588`; `test_pilot_candidates_file.py` |
| T14 `--reuse-from`+`--shard` `runner.py:8364-8372`, plan `cli.py:4616-4620` | **conflict C5** (until P-assay-13) | `test_b105_runner_boundaries.py:1009` |
| plan `--carry-forward` needs `--list-pending --judge`; `--list-pending`+`--json`/`--reuse-from` `cli.py:4589-4601` | `plan` read-only listing preconditions (not combine) | `test_mutation_carry_forward.py:1276`; `test_flex_r2_edges.py:629,698` |

These combinations stay genuinely conflicting:

- **C1 `import-unverified` × `carry-forward`.** The two rules contradict each other on prior kills: one trusts them, the other refuses to. The operator's reason would also mislabel the carried records.
- **C2 whole-lane merge (`strict-shard`/`owner-manifest`) × `--shard`/`--only`/`--exclude`.** Proving that the whole plan is owned is meaningless for a slice.
- **C3 `strict-shard` × `owner-manifest`** (new). These are two ownership proofs for one plan, and only one can be the truth.
- **C4 pilot `--candidates-file` × the exact-id policies.** The B118 pilot measures its own execution. Carry-forward stays allowed (`fddaa3bfe`), because carried records are provenance-marked and never PASS.
- **C5 `--reuse-from` × `--shard`.** The prefix replay binds kills to one complete unsharded prior verdict. This is lifted when hints feed P-assay-13.

New manifest preconditions are appended with their own messages: `--merge-manifest` requires `--resume`, a native R2 lane, no C2/C3/C4 partner, and an existing file.

## 4. `--reuse-from` → hints; the B106 prefix path

**Adapter (P-assay-12).** `reuse.hints_from_verdict(source, *, sequential_pytest_supported) -> tuple[Hint, ...]`. `eligible_witnesses` (`reuse.py:163`) keeps its name and output as a projection of it, because `test_b105_runner_boundaries.py:905,947` monkeypatch that name. The runner path `runner.py:6067-6083` is unchanged. Carry-forward also returns its requeued kills as hints (it counts them today, `mutation_store.py:3021-3031`). Nothing consumes hints in P-assay-12, so behaviour is unchanged.

```python
@record
class Hint:                       # mutation_store.py; consumed by P-assay-13 ranking
    candidate_id: str             # current candidate
    node_id: str                  # prior call-phase failing node (execution.witness.node_id)
    source: Literal["verdict", "store"]
    source_ref: str               # verdict sha256 | store path
    from_candidate_id: str
    from_judge_sha256: str | None # store record's judge; None for a verdict source
    match: Literal["exact-id", "locus", "window"]
```

**Deleted with the prefix path (in P-assay-13, D1):**

- `mutation.py`: the `reuse_witnesses` parameters (`2364`, `3687`), `candidate_reuse` (`3748`), the `target_node_id` branch of `_run_attempt` (`4153-4186`), the replay branch of `_run_one` (`4222-4245`), the `prior_verdict_sha256` plumbing (`3831`, `4252`, `4289`, `4356`), and the import at `207`.
- `mutation_witness.replay_witness_from_receipt` and the B106 replay-loop boundary (`mutation_witness.py:2522+`).
- `runner.py:5253-5260` and `6067-6083`.
- `WITNESS_TARGET_ENV` targeting (`mutation_witness.py:1219-1223`, `2487-2490`). It is deleted only if P-assay-13's isolated primitive does not reuse it.

**Kept read-only (D-A12):** the `witness-prefix` mode in `verdict.py:2196-2290,6458`, `verify.py:1972-2104` and the record reader (`mutation_store.py:2199-2228`), so old verdicts and stores stay valid.

**CLI change when deleted.** `run --reuse-from` stops producing `witness-prefix` kills, and the `plan` label `witness-replay` (`reuse.py:157`) changes meaning. The tests that break are `test_b106_reuse_and_witness.py:726,852,973,1080,1156`, `test_b114_cold_witness_real_runs.py:185,290` and `test_mutation_coverage_closure.py:2053,2070,2094`.

## 5. `combine` (in `mutation_store.py`, after `_carry_over_records`)

```python
CombinePolicy = Literal["same-judge", "strict-shard", "owner-manifest", "import-unverified", "carry-forward"]
COMBINE_POLICIES: tuple[CombinePolicy, ...] = (...)        # precedence; capabilities order
STORE_FLAG_POLICIES: Mapping[str, str]                      # flag -> policy | "hint"; cli._MUTATION_STORE_POLICIES reads it

@record
class StoreSource: path: Path; policy: CombinePolicy        # FlexOptions.store_sources() builds these

def combine(sources: Sequence[StoreSource], request: CombineRequest) -> CombineResult: ...
```

The plan's `combine(sources, policy)` becomes a policy per source. One run applies several policies with per-candidate precedence. Today's single exact-id pass also has to stay one call, with one `imported_at` clock read, one refusal list in job order and one `carry_over` event (`mutation.py:3137-3181`).

`CombineRequest` holds what `run_mutation` already holds at `mutation.py:3008`: `jobs` (= `selected_jobs`), `judge`, the state root, fd and guard, the campaign deadline, `context: RecordContext`, `resume`, the rejudge ids and outcomes, `resource_sha256`, the carry-over reason, `clock`, `deadline_check`, `write_progress` and `total`. `CombineResult` returns `records` (today's `resumed_records` order), the resume counters (rejected, rejudged, resource-rejudged, promoted, imported, `locus_unavailable`, rejudged ids) and `hints`.

The stages run in fixed order. Each emits its existing event at the same point it does today, so stream order and clock-call order are unchanged:

1. **Proofs.** `_validate_shard_stores` (unchanged) → `shard_merge`. Then the new `validate_merge_manifest` plus owner materialization → `merge_manifest`.
2. **Own-store resume.** A verbatim move of `mutation.py:3044-3135` into `_resume_own_store` (D5).
3. **Exact-id chain.** `_carry_over_records`, unchanged: same-judge, strict-shard copy, import-unverified.
4. **Carry-forward.** `_carry_forward_records`, unchanged except that it now returns hints.

Thin callers:

- `mutation.py:3008-3230` becomes one `combine(...)` call. The `resume` event (`:3237+`) stays in `mutation.py`.
- `FlexOptions` gains `merge_manifest: Path | None = None`. Defaults keep every direct constructor call in the tests valid, and `reads_other_stores` includes the manifest. `reads_carry_over_stores` is unchanged.
- `_resolve_flex_options` keeps its signature (tests call it directly with `SimpleNamespace`, `test_flex_r2_edges.py:566-600`).
- Every private symbol the tests import stays: `_carry_forward_lookup`, `_carry_forward_source_index`, `_CarryForward*`, `_carry_over_importable`, `_carry_over_candidates`, `_validate_shard_stores`, `_CarryOverResult`, and the monkeypatch seams `_load_validated_state_record`, `_write_mutation_state_record` and `_locus_for_site`.

## 6. Capabilities delta and the final surface for RG-297

Bump to `assay-capabilities/2` (D2). New and changed values:

- `mutation_store_policies` gains `"--carry-forward": "carry-forward"` and `"--merge-manifest": "owner-manifest"`.
- A new key `combine_policies` lists `["same-judge", "strict-shard", "owner-manifest", "import-unverified", "carry-forward"]` in precedence order.

The map is derived from `STORE_FLAG_POLICIES` ∩ the live `run` flags (`cli.py:242-245`). `commands.run` gains `--merge-manifest`.

**Final assay flag surface (RG-297 forwards it after `--`):**

- Store-combine flags: `--resume`, `--merge-state`, `--merge-shard-store`, `--merge-manifest`, `--carry-over`, `--carry-over-reason`, `--carry-forward`, `--reuse-from`.
- Flags they combine with: `--shard`, `--only-candidates`, `--exclude-candidates`, `--candidates-file`, `--rejudge`, `--rejudge-outcome`, `--state-dir`.

Run-gate validates only its own mounts and its shard instance names. Manifest store paths resolve relative to the manifest file, so run-gate mounts the manifest's directory tree.

## 7. Test plan

**Must pass unchanged:**

- `tests/core/test_flex_r2_campaigns.py`, `test_flex_r2_edges.py`, `test_flex_r2_shard_merge.py`, `test_mutation_carry_forward.py`, `test_mutation_locus_records.py`, `test_mutation_coverage_closure.py`.
- `test_b106_reuse_and_witness.py`, `test_reuse_coverage_controls.py`, `test_b105_runner_boundaries.py`, `test_b105_cli_boundaries.py`, `test_b114_cold_witness_real_runs.py`.
- `test_cli_plan_jobs.py`, `test_cli_coverage_closure.py`, `test_pilot_candidates_file.py`.
- `analysis/tests/test_analysis_campaign_state_refusals.py`.

**Changes by design:** `test_capabilities.py:36-44,81-87` (schema /2, two entries and one new key).

**New tests:**

- **`tests/core/test_store_combine.py`:**
  - one oracle per policy through `combine`;
  - precedence (own store > merge-state > strict-shard copy > import; same-judge > carry-forward);
  - `STORE_FLAG_POLICIES` equals the capabilities map;
  - `FlexOptions.store_sources()`;
  - hints: verdict hints project to `eligible_witnesses`, carry-forward kills yield hints, and the run is unchanged with hints present.
- **`tests/core/test_merge_manifest.py` (the B544 oracles):**
  - a two-store campaign gives the same final record bytes as a same-judge serial run;
  - each of the nine refusal tokens, CLI and library;
  - a non-owner duplicate that finished earlier, or with changed mtimes or timestamps, is ignored and counted;
  - two merges into fresh targets give identical bytes and an identical `merge_manifest` event apart from `emitted_at`;
  - a target holding a differing same-judge record → owner wins, `target_replaced = 1`;
  - conflicts C2/C3/C4, no `--resume`, non-R2 lane.
- B544's own closing conditions: mutation proofs and 100% line and branch coverage on the registered gate.

**Docs:**

- CONSUMERS: a §merge-manifest section, the capabilities /2 text, and line 2170.
- DESIGN-GUIDE: one paragraph under §1712.
- Fix `DESIGN-SUBFILE-CANDIDATE-IDENTITY.md:224`.
- `run --help` for `--merge-manifest`.
- CHANGES.md.

## 8. Open decisions (recommendations)

- **D1. Prefix retirement.** P-assay-12 ships the hint adapter only. P-assay-13 deletes the prefix path (§4) and lifts C5 when its isolated primitive consumes hints. Deleting the path now breaks 11 tests and leaves `--reuse-from` without any effect until P-assay-13. *Recommend: defer.*
- **D2. Capabilities schema.** The policy value vocabulary grows by two, and CONSUMERS says a changed meaning needs a new `schema` (`CONSUMERS.md:29-30`). No code consumes /1 yet (no `run_gate.py` reference). *Recommend: `/2` now, once, before RG-297.*
- **D3. Owner versus target.** The owner record replaces a differing same-judge record already in the target store. This makes the merge a pure function of the manifest; strict-shard instead lets the target win, which is kept unchanged. *Recommend: owner wins, counted.*
- **D4. New progress event `merge_manifest`.** It is added to `PROGRESS_EVENTS` (`mutation.py:1260`). It is not a verdict, `ReasonCode` or `vocabulary.py` change, so it is outside D-A12. *Recommend: add it.*
- **D5. Own-store resume moves into `combine`** verbatim, so that "which record stands" has one owner. *Recommend: move it. The fallback is to keep it in `mutation.py` and have `combine` start at stage 3, at the cost of a two-entry API.*

## 9. Controller decisions (vbpub-assay, 2026-11-24)

The assay sub-controller read this note and checked its key citations against `ba15e8dda`: `cli.py:2388-2395`, `test_capabilities.py:81-87`, `cli.py:183-189` and `mutation.py:4166-4178`. All five recommendations are accepted:

- **D1: defer.** P-assay-12 ships only the hint adapter. P-assay-13 retires the B106 prefix path along with its 11 tests and lifts C5. This moves the plan's P-assay-12 acceptance line "the B106 prefix path is retired" to P-assay-13.
- **D2:** bump to `assay-capabilities/2` now.
- **D3:** the owner record wins over the target and is counted.
- **D4:** add the `merge_manifest` progress event.
- **D5:** own-store resume moves into `combine`.

This note is the implementation contract for P-assay-12.
