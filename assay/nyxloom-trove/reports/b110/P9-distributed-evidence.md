# B110-P9 — Distributed / async evidence: campaign identity, `assay state import`, consolidation, worker runbook

| Field | Value |
|---|---|
| Backlog | B119, plan package P9 |
| Branch | `assay-b110-p9-distributed`, cut from the integration line after `assay-b110-v14` (P3a–P3d) and P6 are merged |
| Depends on | **P3b** (judge identity `/3` with the cold policy, transform, R2 collection digest and runtime fingerprint); **P6** (persisted campaign deadline file); **P1** (the `tests/zz_slow/` tier exists); plan §11.2 (D7 operator confirmation: this changes only the runbook step, not the code) |
| Contract class | 2a→2b. The design choices are enumerated below, each with the carver's chosen option. Once the controller accepts the §"Design packet" choices at dispatch, the package executes as 2b. |
| Implementer | Opus |
| Decisions | A-471 (plan D7); A-473 (P6 deadline); A-464 (shards are scheduling, not proof; time never classifies); A-462 as amended by A-467 |
| Size | L |

## Why this package exists

The operator wants two things:
- expensive R2 work runs asynchronously on several hosts while development continues;
- a provisionally accepted first step whose evidence counts later.

That is safe only if a result produced elsewhere can be proven to belong to *this* campaign's exact judging identity. A disagreement must be surfaced as nondeterminism, never averaged away. The final claim must still be one ordinary, complete, verifier-accepted verdict (A-464: "shards are scheduling, not proof").

This package adds:
- the identity file;
- a conflict-refusing import;
- the consolidation contract;
- a host-agnostic worker runbook.

## Context to read first

Paths are relative to `assay/` at HEAD `db85f747`. Line numbers move with P3/P4/P6; locate code by symbol.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`:
   - §3 D7 and D10;
   - §5 (the judge identity inputs, `runtime_fingerprint_sha256` and the label `assay-judge-identity/3`);
   - §6 (the P9 contract);
   - §9.3;
   - §11.2.
2. `src/assay/mutation.py`:
   - `judge_sha256` 1052-1156, whose docstring states the injective netstring construction;
   - the one production caller in `run_mutation` 2345-2366 ("ONE judge identity for this whole sweep");
   - `mutation_state_record_name` 1159-1175;
   - `default_state_root` 1187-1189;
   - `_write_mutation_state_record` 1244-1270 (atomic temp + `os.replace`);
   - `_load_validated_state_record` 1273-1402. Read its whole docstring: the three-way return; B021 corruption versus a B088 judge mismatch; the fail-open `isinstance` lesson.
   - `_execution_from_state_record` 1450-1497;
   - `select_mutation_shard` 1500-1520 (blake2b-4 mod N; `MAX_SHARD_COUNT`);
   - `merge_mutation_shards` 1529-1641. This library-only function is **not** a certificate, because it never compares against a fresh plan (Sol review).
   - The state-record payload written at 2956-2990.
3. `src/assay/isolation.py:595-624`: `tree_sha256` and `tree_sha256_for_identity_exclude`. These are content digests of the prepared manifest, not the Git tree ID.
4. `src/assay/cli.py`:
   - the `--state-dir` flag :418;
   - `_resolve_state_dir` 845-878 (the git-visibility refusal);
   - the `run` parser 255-328;
   - the `plan` parser 330-367;
   - `_cmd_plan` 1571-1886;
   - the subparser registration around :219-451.
5. `src/assay/analysis.py:1006-1020` `_write_new`: publish without overwriting, which is the pattern for the receipt.
6. `tools/self-qualification-gate.sh`: the exact-OID private clone, build-venv/run-venv, and the `assay run … --resume --state-dir` invocation (~:183-189). A worker reproduces this.
7. `run-gate.toml` `[lanes.self-qualification]`: `resources = {cpus="3", memory="2g", memory_swap="8g"}`. This is the per-worker envelope.
8. `nyxloom-trove/decisions.md` A-464 (:943) and A-462 (:941).
9. `tests/test_b106_reuse_and_witness.py:661-784`: `_seed_pytest_mutation` plus a real `runner.run_lane` R2 on a tiny pytest project. This is the real-run fixture pattern.
10. `tests/conftest.py`: `git_repo` (:530), `make_lane` (:952), `make_r2_judge` (:1084).

## Design packet (2a → 2b; the carver's choice for each)

| # | Open choice | Admissible options | Invariant it must keep | Carver's choice and deciding evidence |
|---|---|---|---|---|
| OC1 | Where the import gets "this campaign's identity" | (a) Recompute from `assay plan` without execution. (b) An identity file written by a real baseline run. (c) Trust each worker's own claim. | "A check is only as strong as what it compares." Identity inputs include `tree_sha256` (a prepared-manifest digest), the R2 collection digest and the runtime fingerprint, and none of those is derivable without preparing a snapshot and running the baselines. | **(b).** (a) cannot produce the runtime fingerprint or the collection digest. (c) is self-attestation. |
| OC2 | When the identity file is written | Every native-R2 run with a store, or only an explicit subcommand | It must never describe a campaign other than the one whose records sit next to it. | **Every native-R2 run with `state_root is not None`**, immediately after `judge` is computed (mutation.py ~2353) and before any candidate executes. Written atomically, **replacing** any older file: it describes the most recent run. |
| OC3 | What counts as an identical duplicate versus a conflict | Byte equality; a semantic signature | Natural timing differences (`elapsed_seconds`, `cpu_seconds`) are not nondeterminism. A different bucket, mode or witness node is. | **Signature = (`judge_sha256`, `outcome_bucket`, `execution.mode`, `execution.witness.node_id` or `null`).** Same signature means `duplicate_consistent` (skip). Different signature means a conflict. |
| OC4 | Partial acceptance | All-or-nothing; per record | A conflict is a campaign-level signal. A foreign or stale record is a per-record fact. | **Per-record refusals** (the rest are accepted, exit 1). **Any conflict** means the whole import is refused, **nothing is written**, and the exit is 2. |
| OC5 | Transfer integrity | Nothing; `SHA256SUMS`; signed bundles | Bytes must equal what the worker wrote. | **A mandatory `SHA256SUMS`** (GNU `sha256sum` format) covering exactly the `*.json` files in the source directory, no more and no fewer. Signing is deferred. |
| OC6 | Where "accepted" lives | A `status` field inside records; store membership plus a receipt | A worker must not be able to forge acceptance. | **Store membership plus a retained import receipt.** No field in the record (a worker could write it). |
| OC7 | Imported provenance on the verdict wire | A new v14/v15 field; none | No schema change in P9 | **None.** Receipts are retained gate evidence, and the §9.3 runbook requires them. A wire field would be v15 and is deferred. |
| OC8 | Records produced before the campaign deadline init (the screen, early workers) | Accept; refuse | Identity-bound validity (A-471) | **The code accepts them** (identity is the authority). Whether §9.3 step 4 imports them is the **operator's pending D7 confirmation** (plan §11.2). If the answer is no, the runbook omits the step; no code change. |
| OC9 | An existing store record with a *different* judge (stale) | Keep; replace; refuse | Resume already treats it as absent (`_RECORD_REJECTED`). | **Replace it atomically** and list it under `replaced_stale` in the receipt. |
| OC10 | Worker clock skew against the absolute UTC deadline (P6) | Ignore; measure at enrolment | The deadline is absolute UTC (plan D9). | **Measure at enrolment.** A host with an absolute NTP offset over 2 s is not enrolled. This is a runbook rule, not code. |

**No D-decision beyond A-471 is needed.** OC8 is already routed to the operator (plan §11.2).

## Implementation packet (normative, after the design is accepted)

### Owned interfaces

**Owner `src/assay/mutation.py`.** Refactor the identity into one derivation; do **not** duplicate it.

```python
def judge_identity_inputs(*, tree_sha256: str, plan: CommandPlan, link_paths: Sequence[str] = (),
                          tool_version: str | None, cold_witness_kills: bool, transform: str | None,
                          r2_collection_sha256: str | None, runtime_fingerprint_sha256: str | None,
                          equivalence_ledger_sha256: str | None) -> dict: ...
def judge_sha256_from_inputs(inputs: Mapping[str, Any]) -> str: ...   # the ONLY digest implementation
def judge_sha256(**same_kwargs) -> str:                               # == judge_sha256_from_inputs(judge_identity_inputs(...))
```

- The keyword set equals whatever P3b gave `judge_sha256`. If P3b's signature differs from the list above, **use P3b's**, keep this shape, and note the difference in the report.
- The inputs dict is JSON-safe with exactly these keys:
  - `label` (`"assay-judge-identity/3"`)
  - `tree_sha256`
  - `tool_version` (a string; `""` for None, matching today's netstring rule)
  - `argv_effective` (a list)
  - `env_declared` (an object, keys sorted)
  - `env_ambient_names` (a sorted list)
  - `cwd_declared` (a string or `""`)
  - `project_prefix` (a string or `""`)
  - `link_paths` (a sorted list)
  - `cold_witness_kills`
  - `transform`
  - `r2_collection_sha256`
  - `runtime_fingerprint_sha256`
  - `equivalence_ledger_sha256`
- `judge_sha256_from_inputs` reproduces **byte-for-byte** the netstring/count construction of `judge_sha256` (mutation.py 1135-1156, plus P3b's additions). It refuses unknown or missing keys with `ValueError`.

**New file `<state-dir>/campaign-identity.json`** (written by `run_mutation`, owner mutation.py):

```json
{"schema_version": 1, "kind": "assay-campaign-identity", "lane": "self-qualification",
 "commit": "<40hex>", "git_tree": "<40hex>", "assay_version": "7.2.0.dev12+g1a2b3c4",
 "judge_sha256": "<64hex>", "judge_inputs": { ...exactly the dict above... },
 "shard": {"index": 1, "count": 4}, "written_at": "2026-10-03T11:02:07Z"}
```

- `shard` is `null` when unsharded. It is informational only, because the judge identity excludes the shard.
- `commit` and `git_tree` come from the lane run's HEAD and the snapshot spec: the same values the verdict records.
- **Invalid examples:**
  - `judge_sha256` ≠ `judge_sha256_from_inputs(judge_inputs)` → `identity-inconsistent`.
  - `judge_inputs` has an extra key → `identity-inconsistent`.
- The filename does not match `^[0-9a-f]{64}\.json$`, so it never collides with a record and nothing that scans records reads it. P8 ignores it by name.

**New CLI (owner: new module `src/assay/state_import.py`, registered in `cli.py`):**

```
assay state import <lane> --file <assay.toml> --state-dir <STORE>
    --from <LABEL>=<DIR> [--from <LABEL>=<DIR> ...] --receipt <PATH> [--worktree <dir>]
```

- `LABEL` matches `^[A-Za-z0-9._-]{1,64}$` and is unique across `--from`.
- `--receipt` must not exist. Publish it with the `_write_new` link-without-overwrite pattern.
- `--state-dir` is resolved and refused by the **same** `_resolve_state_dir`.
- Add `src/assay/state_import.py` to **both** `judge.targets` lists in `assay.toml` (`tests/test_self_lane.py:128-135`).

### Required flow (the order is normative; nothing is written before step 8)

1. Resolve arguments. `--receipt` must not exist. `--state-dir` passes `_resolve_state_dir`. Each `--from` must be a directory.
2. Load `STORE/campaign-identity.json`. It is **required**; if it is absent, exit 2 with `store-identity-missing`.
   - Validate its shape.
   - Recompute `judge_sha256_from_inputs(judge_inputs)` and compare with the stored value; on mismatch, exit 2 with `identity-inconsistent`.
3. Bind the store identity to the current state:
   - `lane == <lane>`;
   - `commit == git rev-parse HEAD` of the worktree;
   - `git_tree == HEAD^{tree}`;
   - `assay_version == assay.__version__`;
   - `judge_inputs.tool_version == assay.__version__`.

   Any mismatch → exit 2, `store-identity-stale`.
4. Reconstruct the current plan with the same planner code as `assay plan`. Reuse the `_lane_plan` helper that P8 ported, or call the underlying functions; do not shell out. This yields `plan_by_id`. If the plan is unsupported, exit 2.
5. For each source, in `--from` order:
   - `SHA256SUMS` must exist and list exactly every `*.json` in the directory, no extras and no missing; every digest must match. Otherwise exit 2 with `source-integrity`, naming the label.
   - `campaign-identity.json` must exist. Validate it as in step 2 (failure → exit 2, `source-identity-inconsistent`).
   - Record `identity_matches` = (its `judge_sha256` equals the store's), and `differing_components` = the sorted `judge_inputs` keys whose values differ.

     A mismatching source is **not** refused wholesale: its records are refused per record in step 6, so the receipt shows the cause. For example, `runtime_fingerprint_sha256` differs when the host environment differs.
6. For each `<64hex>.json` record in each source (other names are ignored, except `SHA256SUMS` and `campaign-identity.json`), run the checks below in order. The first failing check is the refusal reason:

   | # | Check | Refusal reason |
   |---|---|---|
   | 1 | Size ≤ `MUTATION_STATE_RECORD_LIMIT` | `oversized` |
   | 2 | UTF-8 JSON object | `malformed` |
   | 3 | `schema_version == 1` | `schema-version` |
   | 4 | `candidate_id` == filename stem | `malformed` |
   | 5 | Candidate is in `plan_by_id` | `not-in-plan` |
   | 6 | `path`, `operator`, `source_sha256` and `replacement_sha256` equal the plan row | `identity-fields` |
   | 7 | `outcome_bucket ∈ MUTATION_BUCKETS` | `malformed` |
   | 8 | `_execution_from_state_record` succeeds | `malformed` |
   | 9 | `judge_sha256` is a string equal to the store identity's | `judge-mismatch` |

   Records that pass are *candidates for acceptance*.
7. **Conflict detection.** Across all candidates-for-acceptance from all sources, plus each existing `STORE/<id>.json` whose `judge_sha256` equals the store identity:
   - group by candidate ID and compare signatures (OC3);
   - the same signature → keep one (the store's copy if present, else the first source by `--from` order); the others are `duplicate_consistent`;
   - a different signature → conflict.

   **Any conflict:** write only the receipt (`result: "refused"`), exit 2, and leave the store byte-for-byte unchanged. An existing store record with a different judge is stale (OC9) and does not take part in conflicts.
8. Write each accepted record with `_write_mutation_state_record(STORE, payload)`, which gives canonical JSON and an atomic write. A stale store record is replaced; list it under `replaced_stale`. `record_sha256` in the receipt is the SHA-256 of the bytes written.
9. Publish the receipt. Exit 0 if nothing was refused; exit 1 if at least one record was refused per record.

### Import receipt (owner `state_import.py`; schema `src/assay/schemas/state-import-receipt.schema.json`, `"schema_version": {"const": 1}`)

**Valid example:**
```json
{"schema_version": 1, "kind": "assay-state-import", "lane": "self-qualification",
 "commit": "<40hex>", "store": "/abs/.assay/campaign-X", "store_judge_sha256": "<64hex>",
 "assay_version": "7.2.0.dev12+g1a2b3c4", "created_at": "2026-10-03T12:00:00Z",
 "result": "partial",
 "sources": [{"label": "hostb-s1", "path": "/abs/inbox/hostb-s1", "sha256sums_sha256": "<64hex>",
              "identity_judge_sha256": "<64hex>", "identity_matches": true, "differing_components": []}],
 "counts": {"examined": 941, "accepted": 939, "duplicate_consistent": 1, "replaced_stale": 0, "refused": 1},
 "accepted": [{"candidate_id": "<64hex>", "source": "hostb-s1", "record_sha256": "<64hex>"}],
 "duplicate_consistent": [{"candidate_id": "<64hex>", "sources": ["store", "hostb-s1"]}],
 "replaced_stale": [],
 "refused": [{"candidate_id": "<64hex>", "source": "hostb-s1", "reason": "judge-mismatch"}],
 "conflicts": []}
```

**Receipt rules:**
- `result` ∈ {`accepted`, `partial`, `refused`}.
- `accepted` means `refused == []` and `conflicts == []`.
- `partial` means `refused` is non-empty and `conflicts == []`.
- `refused` means `conflicts` is non-empty, `accepted == []` and `replaced_stale == []`.
- Every count equals the length of its list, and `examined` equals the sum of the other four counts.
- `reason` comes from the closed set of step 6.
- A conflict entry has the shape `{"candidate_id", "a": {"source", "outcome_bucket", "mode", "node_id"}, "b": {...}}`.

**Invalid examples**, which the model check or schema must reject:
1. `result: "accepted"` with a non-empty `refused` list.
2. `counts.accepted: 939` with `accepted` holding 938 entries.

### Topology and namespaces

```
worker host W_k:  exact-OID clone → run-venv → STATE_k (private, never shared)
                  STATE_k/<id>.json + STATE_k/campaign-identity.json + SHA256SUMS
        │ transfer (rsync/scp, any transport) — bytes only
        ▼
coordinator:      <project>/.assay/inbox/<LABEL>/        ← PROVISIONAL (never read by `assay run`)
                  assay state import … --from LABEL=… --state-dir STORE --receipt …
                  STORE = <project>/.assay/campaign-<id>/ ← ACCEPTED (gitignored path)
                  assay run <lane> --resume --state-dir STORE --cold-witness [--campaign-deadline …]
                  (no --shard) → ONE ordinary verdict
```

- Paths in the receipt are absolute on the coordinator.
- The inbox must be gitignored or outside the tree. Reuse `_resolve_state_dir`'s visibility refusal for `--from` directories inside the project, so the next run cannot go `DIRTY_TREE`.

### Bounds

- At most 64 `--from` sources.
- Records per source ≤ the lane's `max_mutants` (≤ 10,000).
- Each record ≤ 1 MiB (existing limit).
- `SHA256SUMS` ≤ 4 MiB.
- All reads go through `safeio.read_bounded_input` / `read_bounded_file`.

### Decision table

| State | Exit | Store written? | Receipt |
|---|---|---|---|
| Store identity missing, inconsistent or stale | 2 | no | not written (stderr names the cause) |
| A source fails integrity or its identity file is inconsistent | 2 | no | not written |
| At least one conflict | 2 | no | written, `refused` |
| Some per-record refusals, no conflicts | 1 | accepted records only | written, `partial` |
| Everything accepted or duplicate | 0 | yes | written, `accepted` |

### Consolidation contract (no new code; tests prove it)

`assay run <lane> --resume --state-dir STORE --cold-witness …` **without `--shard`**:
- re-runs both baselines;
- recomputes the judge;
- resumes every accepted record through the unchanged `_load_validated_state_record`;
- executes anything missing or rejected;
- writes one ordinary verdict.

**`state import` is an early filter; the consolidating run is the authority.** For example, a record accepted because the store identity matched is still re-executed if the consolidating host's runtime fingerprint differs from the store identity. This is expected, and the P9 report states it.

### Degrees of freedom

- Private helper names inside `state_import.py`.
- The order of per-record checks **within** one refusal reason.
- The receipt's text rendering on stderr.

## Worker runbook (host-agnostic; this becomes `docs/CONSUMERS.md` "Distributed R2 workers" and the plan §9 appendix)

**Enrolment (per host, by measurement, recorded in the campaign log):**
- `nproc`;
- the container's effective `cpu.max` and `memory.max` / `memory.swap.max`, which must be at least the gate's 3 CPU / 2 GiB / 8 GiB, or the host's own documented cap;
- the tester-unified image digest;
- the NTP offset, which must be ≤ 2 s (OC10).

Remote capacity counts as **zero until measured** (A-464).

**Run:**
1. The coordinator fixes N (shard count) once per campaign. **Never change N mid-campaign.**
2. The coordinator runs `assay campaign init` (P6) and copies the deadline file to every worker. **The coordinator is worker 0:** it runs shard `0/N` directly into STORE. That run writes `STORE/campaign-identity.json`, which every import requires; until it exists, imports exit 2 with `store-identity-missing`.
3. On each worker W_k, inside the tester-unified image with the per-host cgroup limits:
   1. Make the exact-OID clone and build/install the wheel exactly as `tools/self-qualification-gate.sh` does.
   2. Run `assay run self-qualification --shard k/N --cold-witness --resume --state-dir $STATE_k --campaign-deadline $DEADLINE --progress $P_k --verdict-json $V_k`. The worker verdict is diagnostic only.
4. On the worker: `cd $STATE_k && sha256sum *.json > SHA256SUMS`. Transfer the directory to the coordinator's `.assay/inbox/<host>-s<k>/` with any transport, then verify with `sha256sum -c` on arrival.
5. On the coordinator: `assay state import … --from <host>-s<k>=… --receipt .assay/import-<host>-s<k>.json`, one import per arriving batch.
   - Exit 1 → inspect the `refused` reasons. `judge-mismatch` usually means that host's environment differs; stop using that host.
   - Exit 2 with conflicts → **stop the campaign.** A conflict is a nondeterminism finding: file it, and do not "pick one".
6. Consolidate on the coordinator (§9.3). Retain every import receipt with the gate evidence.

**Never** share a state directory between hosts (NFS etc.) or between concurrently running invocations. **Never** hand-edit a record; import refuses it and resume treats it as corruption (B021).

## Work

1. Refactor to `judge_identity_inputs` / `judge_sha256_from_inputs`. Prove equality with the existing digest (O1) before anything else. Commit.
2. Write `campaign-identity.json` from `run_mutation` (OC2). Commit.
3. Implement `src/assay/state_import.py` (flow steps 1-9), the receipt model and the schema. Register the CLI. Add the module to both target lists.
4. Write the fast unit tests on synthetic stores (`tests/test_state_import.py`).
5. Write the real-run consolidation tests in `tests/zz_slow/test_state_import_real_runs.py`. They use the `_seed_pytest_mutation`-style tiny project, real `runner.run_lane`, and `--shard 0/2` and `1/2` into separate state dirs.
6. Docs and runbook (Docs sync). Update the B119 backlog entry and CHANGES.
7. Run the focused tests, then the gates. Write the report.

## Oracles

| # | Oracle (observable) | Negative it distinguishes |
|---|---|---|
| O1 | For 5 existing judge-identity test vectors, `judge_sha256_from_inputs(judge_identity_inputs(x)) == judge_sha256(x)`, byte-equal. All existing `tests/test_mutation_judge_identity*.py` tests stay green unchanged. | A second derivation that drifts |
| O2 | After a native-R2 run with a store, `campaign-identity.json` exists, recomputes to its own `judge_sha256`, and that equals the `judge_sha256` in every record written by that run. | An identity file describing a different plan |
| O3 | **Two shard stores** (real runs, `0/2` and `1/2`): run shard `0/2` directly into STORE, which acts as the coordinator's own worker and writes STORE's identity file. Run shard `1/2` into a separate dir S1, add `SHA256SUMS`, then import S1 into STORE. The receipt is `accepted` and the counts reconcile. A consolidating `--resume` run then executes **zero** candidates: the tracked process runner sees only the baseline invocations. Its verdict passes `assay verify`, and `candidate_ids` equals the full plan. | Consolidation that re-executes, or a verdict missing the shard 1 IDs |
| O4 | **A conflicting record:** a copy of an accepted shard-1 record with `outcome_bucket` flipped (and a valid SHA256SUMS regenerated) in a second source → exit 2, `conflicts` names both sources, and the store bytes are unchanged (snapshot with a directory hash before and after). | "Last writer wins", or partial writes |
| O5 | **A foreign-identity record:** a record produced by a run whose lane `env` value differs (a different `judge_sha256`) → refused `judge-mismatch`, the others are accepted, exit 1, `result: partial`, and `differing_components` contains `env_declared`. | Accepting on candidate ID alone |
| O6 | **A stale-tree record:** records produced at commit X. Commit a test-only change (X'). Run the baseline at X' to rewrite the store identity. Import X's records → every one is refused `judge-mismatch`, and a later `--resume` executes them all. | Trusting unchanged mutant bytes after a test change (the B088 defect class) |
| O7 | Missing `SHA256SUMS`, an extra unlisted `*.json`, and one byte flipped in a record → each exits 2 `source-integrity`, and nothing is written. | Importing corrupted transfers |
| O8 | Tampered `campaign-identity.json` in the store (a changed `judge_inputs.transform`) → exit 2 `identity-inconsistent`. A store identity at a commit different from HEAD → exit 2 `store-identity-stale`. | Trusting an identity file's self-reported digest |
| O9 | A store record with an older judge plus a matching source record → replaced atomically and listed in `replaced_stale`. The same judge with a different bucket is a conflict (O4). | Keeping stale records, or treating stale as a conflict |
| O10 | The receipt validates against its schema, and the two invalid examples fail the model/schema check. | Receipt drift |
| O11 | `--receipt` pointing at an existing file → refused before any write. `--state-dir` or `--from` inside the tree and git-visible → refused, as with `assay run`. | Clobbering evidence, or making the tree dirty |

**Gate observable:** `tester-unified` PASS and `self-qualification-preflight` PASS. The new module must be at 100% line and branch coverage and add no exclusions.

### Oracle anti-patterns (AUTHORING.md §3b, pasted verbatim)

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

- **README (WHAT):** "Distributed R2 evidence". Explain that records are identity-bound, that `assay state import` refuses conflicts, and that the final claim is always one consolidating `--resume` verdict.
- **DESIGN-GUIDE (WHY):**
  - why the identity file exists;
  - why a conflict is refusal-worthy nondeterminism;
  - why import is a filter and consolidation is the authority;
  - why "accepted" is store membership, not a record field;
  - that shards are scheduling (A-464);
  - the D7 pre-deadline reading and its pending operator confirmation.
- **CONSUMERS (HOW):** the worker runbook above (pasteable), the receipt fields, and the exit codes.
- **CHANGES.md:** `### Added`.
- **Backlog:** update B119's status.

## Scope / forbid

**Touch:**
- `src/assay/mutation.py` (the identity refactor and the identity-file write only);
- `src/assay/state_import.py` (new);
- `src/assay/cli.py` (registration and plumbing);
- `src/assay/schemas/state-import-receipt.schema.json` (new);
- `assay.toml` (the two target lists);
- `tests/test_state_import.py` and `tests/zz_slow/test_state_import_real_runs.py` (new);
- the existing judge-identity tests, **only** by adding O1 vectors;
- README, DESIGN-GUIDE, CONSUMERS, CHANGES, the backlog and the report.

**Forbid:**
- any verdict/schema wire change (OC7);
- `MUTATION_STATE_SCHEMA_VERSION` (it stays 1);
- `merge_mutation_shards` semantics;
- run-gate or Buildkite code;
- actually enrolling or using remote hosts (the runbook only);
- any new `ReasonCode`.

## Gate

1. Focused tests, serially: `nice -n 19 ionice -c3 python -m pytest tests/test_state_import.py tests/zz_slow/test_state_import_real_runs.py tests/test_mutation_judge_identity.py tests/test_mutation_judge_identity_properties.py tests/test_self_lane.py -q -p no:cacheprovider`. Adjust the path if P1 moved `test_mutation_judge_identity_properties.py` into `tests/zz_slow/`.
2. `cd <worktree>/assay && python ./run-gate.py tester-unified`. Then, **in a separate step**, read `ASSAY_GATE_CONTAINER_EXIT` and `ASSAY_REGISTERED_GATE_COMPLETE` from the log (L4).
3. `python ./run-gate.py self-qualification-preflight`.

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file: STOP. Write `BLOCKED: <reason>` to the LOG (`nyxloom-trove/reports/assay-B110-P9-REPORT.md`), commit, and exit. Do NOT improvise a workaround.

**Specific triggers:**
- P3b's identity inputs cannot be expressed as a JSON-safe dict that reproduces the digest byte-for-byte.
- The planner cannot be reused in-process.
- O3 requires a verdict/schema change.

## Report

Write `nyxloom-trove/reports/assay-B110-P9-REPORT.md` with:
- the accepted design choices (OC1–OC10, noting any controller override);
- a traceability table (`work | owner | oracle | test | controlled break`) with real test names and red-first counts;
- the gate verdicts, read separately;
- the explicit statement "import is a filter; consolidation is the authority";
- residuals, including signed bundles (deferred) and the verdict provenance field (v15, deferred).

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`
