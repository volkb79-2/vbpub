# B110-P9 — Distributed / async evidence: campaign identity, `assay state import`, audit sample, consolidation, worker runbook

*Revised 2026-09-28 after round-1 review (see `REVIEW-2026-09-28-round1.md`). This revision covers findings P9-1..P9-10 and carver decisions C7, C8, C12, C13 and C15.*

| Field | Value |
|---|---|
| Backlog | B119, plan package P9 |
| Branch | `assay-b110-p9-distributed`, cut from the integration line (`assay-b110-integration`, per plan §11.1 / C18) after these have merged: `assay-b110-v14` (P3a–P3d), P6, P7 + P7b, P8 and P1 |
| Depends on | **P3b**: judge identity `/3` with the cold policy, transform, R2 collection digest and runtime fingerprint. The hook-fingerprint paths must be normalized relative to site-packages/purelib and plugin tokens must be id-free (P3B-4); otherwise resumed evidence fails verification across venvs. **P6**: the campaign deadline file, and state records carrying `campaign_deadline_sha256` when written under `--campaign-deadline` (C8). **P7**: pilot runs write the `PILOT-STATE` sentinel into their state dir and record `selection_sha256`. **P8**: `cli.plan_jobs()`, whose plan rows carry `source_sha256`/`mutated_file_sha256` (C13). **P1**: the `tests/zz_slow/` tier. **Plan §11.2**: D7 defaults to **NO** until the operator answers (C8). |
| Contract class | 2a→2b. The design choices are enumerated below, each with the carver's chosen option. Once the controller accepts them at dispatch, the package executes as 2b. |
| Implementer | Opus |
| Decisions | A-471 (plan D7; C8 default NO); A-473 (P6 deadline); A-474 (pilot state is never imported); A-464 (shards are scheduling, not proof; time never classifies); A-462 as amended by A-467 |
| Size | L |

## Why this package exists

The operator wants expensive R2 work to run asynchronously on several hosts while development continues.

That is safe only under three conditions:
- A result produced elsewhere must be provably bound to *this* campaign's exact judging identity **and** to its persisted deadline.
- A disagreement must be surfaced as nondeterminism, never averaged away.
- The final claim must still be one ordinary, complete, verifier-accepted verdict (A-464: "shards are scheduling, not proof").

This package adds:
- the store identity file;
- a conflict-refusing, deadline-bound import;
- an audit-sample re-execution that tests worker honesty;
- the consolidation contract;
- the gate modes;
- a host-agnostic worker runbook.

## Trust model (state it verbatim in DESIGN-GUIDE and the report)

**What a record is.** A state record is **worker-authored**. Its `judge_sha256`, `outcome_bucket`, `execution`, `evidence` and `campaign_deadline_sha256` are all written by the process that ran the candidate.

**What `SHA256SUMS` proves.** Only that the bytes arrived as the worker wrote them.

**What import proves.** Identity binding proves a record *claims* the right campaign; it cannot prove the claimed outcome is true.
- A buggy or malicious worker can write `killed` for a survivor, copy the correct judge, and regenerate `SHA256SUMS`.
- Without the mitigation below, such a record would be accepted, resumed without execution by the consolidating run, and pass `assay verify`.
- Conflict detection catches a lie only when the same candidate also ran on another host.

**Mitigation (C12): the audit sample.**
- For each source, a deterministic sample of the records that would be accepted is **held back** from the store. The sample is 2%, minimum 5, or all records if fewer than 5.
- The consolidating run therefore re-executes those candidates locally.
- `assay state audit-check` then compares each local outcome with the held-back worker record. Any bucket disagreement refuses **that whole source** and fails the gate.
- The sample is seeded by the campaign name, so it is reproducible and cannot be chosen by the worker.

**Residual risk (state it honestly).**
- A dishonest worker is caught with probability 1 − (1 − f)^k, where f is the fraction of its records that are false and k is the sample size. A worker with a single false record out of 1,000 has about a 2% chance of being caught per campaign.
- The audit sample tests worker *honesty*, not every record.
- Enrol only hosts you control. Remote capacity is "measured" (A-464), not "trusted".
- This limit is part of the operator's D7 question (plan §11.2).

## Context to read first

Paths are relative to `assay/` at HEAD `db85f747`. Line numbers move with P3, P4, P6, P7 and P8, so locate code by symbol.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §3 D7 and D10; §5 (the judge identity inputs, `runtime_fingerprint_sha256`, label `assay-judge-identity/3`); §6 (the P9 contract); §9.3; §11.2 and §11.6.
2. `nyxloom-trove/reports/b110/REVIEW-2026-09-28-round1.md`: C7, C8, C12, C13.
3. `src/assay/mutation.py`:
   - `judge_sha256` **1137-1156** (construction; its docstring starts at 1052);
   - the one production caller in `run_mutation` 2345-2366 ("ONE judge identity for this whole sweep");
   - `mutation_state_record_name` 1159-1175;
   - `default_state_root` 1187-1189;
   - `_write_mutation_state_record` 1244-1270 (atomic temp + `os.replace`);
   - `_load_validated_state_record` 1273-1402. Read its whole docstring: the three-way return; B021 corruption (`MutationStateError`) versus B088 judge mismatch (`_RECORD_REJECTED`, compared from the record itself at 1398-1400); the fail-open `isinstance` lesson;
   - the resume loop 2385-2412, which re-validates every record against the recomputed judge;
   - `_execution_from_state_record` 1450-1497;
   - `select_mutation_shard` 1500-1520 (blake2b-4 mod N; `MAX_SHARD_COUNT`);
   - `merge_mutation_shards` 1529-1641, which is library-only and **not** a certificate (Sol review);
   - the state-record payload 2956-2990.
4. **Tests that glob `*.json` in a state dir.** Every file P9 adds to a store must avoid the `.json` extension (P9-1):
   - `tests/test_mutation_progress_budget_plan.py:547` (`len == 2`), `:638`, `:967`, `:1034`;
   - `tests/test_cli_run.py:864/902/1059`;
   - `tests/test_mutation_judge_identity.py:546/598/813`;
   - `tests/test_state_dir_resume.py:142/182/203`;
   - `tests/test_mutation_state_crash_tails.py:86`;
   - `tests/test_b106_reuse_and_witness.py:1034`;
   - `tests/test_mutation_resume_sharding.py:136`.
5. `src/assay/cli.py`:
   - the `--state-dir` flag :418;
   - `_resolve_state_dir` 845-878 (git-visibility refusal);
   - the `run` parser 255-328;
   - `_cmd_plan` 1571-1886;
   - after P8: `plan_jobs()` and the rows that carry `source_sha256`/`mutated_file_sha256`;
   - `src/assay/candidate_identity.py:8` `candidate_id_from_fields`.
6. `src/assay/analysis.py:1006-1020` `_write_new`: publish without overwriting (the pattern for the receipt).
7. `tools/self-qualification-gate.sh`:
   - the requested-lane `case` at :16-19;
   - the exact-OID clone, build-venv/run-venv and wheel (:49-154);
   - `run_and_verify_lane` 156-214, whose state path `.assay/mutation-state-$lane` at :160 is **STORE**.
   - After P6, P7b and P10c: their arms and the deadline wiring.
8. `run-gate.toml` `[lanes.self-qualification]`, whose `resources = {cpus="3", memory="2g", memory_swap="8g"}` is the per-worker envelope. After P7b: `[lanes.b110-screen]`, the shape to copy.
9. `nyxloom-trove/decisions.md`: A-464 (:943), A-462 (:941), A-471 (end of file).
10. `tests/test_b106_reuse_and_witness.py:661-784`: `_seed_pytest_mutation` plus a real `runner.run_lane` R2 on a tiny pytest project. This is the real-run fixture pattern.
11. `tests/conftest.py`: `git_repo` (:530), `make_lane` (:952), `make_r2_judge` (:1084).

## Tracer-bullet probe (carver/controller, before dispatch; no gates, no containers)

This probes the dangerous seam: consolidation by `--resume` over records copied between stores. Run it at the current HEAD with the devcontainer `assay`, on a scratch tiny pytest repo (two killable sites, one test file), serially under `nice -n 19 ionice -c3`.

1. `assay run L --shard 0/2 --resume --state-dir S0`, then `assay run L --shard 1/2 --resume --state-dir S1`.
2. `cp S1/*.json S0/`.
3. `assay run L --resume --state-dir S0 --progress P.jsonl --verdict-json V.json`.
4. Expect the `resume` progress event to report `resumed_total == 2` and **no** `candidate` events, and `assay verify V.json` exits 0.

Record the commands and results in the P9 carve log. If step 4 executes any candidate, the consolidation contract is wrong, and P9 goes back to the carver.

## Design packet (2a → 2b; the carver's choice for each)

| # | Open choice | Admissible options | Invariant | Carver's choice and deciding evidence |
|---|---|---|---|---|
| OC1 | Where import gets "this campaign's identity" | (a) recompute from `assay plan`; (b) an identity file written by a real baseline run; (c) trust each worker | "A check is only as strong as what it compares." The identity includes `tree_sha256`, the R2 collection digest and the runtime fingerprint, none of which can be derived without running the baselines. | **(b).** (a) cannot produce the runtime fingerprint; (c) is self-attestation. The store identity comes from the coordinator's own run (the `b119-worker` arm, shard 0 into STORE). |
| OC2 | Identity file name and write time | any | It must never collide with record globs (P9-1), and must describe the records beside it. | **`<state-dir>/CAMPAIGN-IDENTITY`** (JSON content, no extension). It is written by every native-R2 run with `state_root is not None`, right after `judge` is computed (mutation.py ~2353) and before any candidate executes. The write is atomic and replaces any older file, under the store lock (OC14). |
| OC3 | Duplicate vs. conflict | byte equality; a semantic signature | Timing differences are not nondeterminism. A different bucket is. | **Conflict signature = (`judge_sha256`, `outcome_bucket`).** The same signature is `duplicate_consistent`. Two `killed` records that differ only in `execution.mode`/witness node are **not** a conflict; they are reported in `mode_differences` (P9-9). |
| OC4 | Partial acceptance | all-or-nothing; per record | A conflict is campaign-level; a foreign, stale or non-final record is a per-record fact. | **Per-record refusals** (the rest are accepted, exit 1). **Any conflict** → the whole import is refused, nothing is written to the store root, exit 2. |
| OC5 | Transfer integrity | none; `SHA256SUMS`; signed | The bytes must equal what the worker wrote. | **Mandatory `SHA256SUMS`** with a fixed grammar (see *SHA256SUMS grammar*). Signing is deferred. |
| OC6 | Where "accepted" lives | a record field; store membership plus a receipt | A worker must not forge acceptance. | **Store membership + a retained import receipt + a passing audit-check.** No field in the record. |
| OC7 | Imported provenance on the verdict wire | v15 field; none | No schema change in P9 | **None.** Receipts and audit-check results are retained gate evidence. |
| OC8 | Records not bound to the current campaign deadline (screen, pre-init workers) | accept; refuse; flag-gated | D7 is pending and defaults to NO (C8) | **Refuse by default:** a record whose `campaign_deadline_sha256` ≠ sha256(`--require-campaign-deadline` FILE) is refused `unbound-record`. `--accept-unbound-records` accepts only records that **lack** the field, never a *different* deadline. The runbook forbids that flag unless the operator answers D7 yes. |
| OC9 | An existing store record with a different judge (stale) | keep; replace; refuse | Resume treats it as absent | **Replace it atomically.** It is listed in `replaced_stale`, which is a subset of `accepted`. |
| OC10 | Worker clock skew | ignore; measure | The deadline is absolute UTC | **Measure at enrolment**: NTP offset ≤ 2 s. A runbook rule, not code. |
| OC11 | Worker honesty | trust; re-execute everything; audit sample | Complete-inventory claim; bounded extra cost | **Audit sample** (see Trust model): 2% per source, min 5 (or all if fewer), selected by lowest `blake2b(campaign + ":" + candidate_id)`, where `campaign` is the deadline file's `campaign` field. Held-back records go to `STORE/.import-audit/<LABEL>/<id>.json` (outside the root glob), and `assay state audit-check` compares them after consolidation. |
| OC12 | Non-final buckets | accept; refuse | A-464: host load must not decide a campaign | **Refuse `hung`, `budget_exceeded` and `crashed` per record** (`non-final-bucket`), so consolidation re-executes them locally (P9-9). Otherwise a load-induced worker result could be replayed or become a conflict. |
| OC13 | Pilot state | ignore; refuse | A-474: pilot state is never imported | **Refuse the whole source** (`source-pilot`, exit 2) if it contains `PILOT-STATE` or its `CAMPAIGN-IDENTITY.selection_sha256` is non-null (P9-6). |
| OC14 | Concurrency | none; lock | No time-of-check/time-of-use gap between a running shard and an import (P9-8) | **An exclusive `fcntl.flock(LOCK_EX \| LOCK_NB)` on `STORE/.lock`**, honored by `assay run` (whenever a state dir is set) and by `assay state import`/`audit-check`. A held lock is a refusal: import exits 2 `store-locked`; run gives a whole-lane `ERROR/BAD_LANE_CONFIG` "state dir is locked by another assay process" before any execution. |
| OC15 | Host vs. container | host import; container import | `assay_version` must equal the exact-OID wheel (P9-4) | **Import runs only inside tester-unified** through the gate arm `b119-import`. STORE is the gate's `.assay/mutation-state-self-qualification`. There is never a host-side `campaign init` or import. |

**No D-decision beyond A-471 is needed.** OC8's default (NO) holds until the operator answers plan §11.2.

## Implementation packet (normative, after the design is accepted)

### Owned interfaces

**Owner `src/assay/mutation.py`.** There is one identity derivation; do **not** duplicate it.

```python
def judge_identity_inputs(*, tree_sha256: str, plan: CommandPlan, link_paths: Sequence[str] = (),
                          tool_version: str | None, cold_witness_kills: bool, transform: str | None,
                          r2_collection_sha256: str | None, runtime_fingerprint_sha256: str | None,
                          equivalence_ledger_sha256: str | None) -> dict: ...
def judge_sha256_from_inputs(inputs: Mapping[str, Any]) -> str: ...   # the ONLY digest implementation
def judge_sha256(**same_kwargs) -> str:                               # == judge_sha256_from_inputs(judge_identity_inputs(...))
def validate_state_record_shape(payload: object, *, stem: str, plan_row: Mapping[str, Any] | None) -> str | None:
    """Return None if valid, else the refusal reason (closed set below). Shared by
    `_load_validated_state_record` (which maps a non-None reason to MutationStateError)
    and `state import`, so the two validators cannot drift (P9-10)."""
```

**`judge_identity_inputs`:**
- The keyword set equals P3b's `judge_sha256`. If P3b's signature differs from the list above, **use P3b's**, keep this shape, and note it in the report.
- The inputs dict is JSON-safe with exactly these keys:
  - `label` (`"assay-judge-identity/3"`);
  - `tree_sha256`;
  - `tool_version` (`""` for None);
  - `argv_effective` (list);
  - `env_declared` (object, sorted keys);
  - `env_ambient_names` (sorted list);
  - `cwd_declared`;
  - `project_prefix`;
  - `link_paths` (sorted list);
  - `cold_witness_kills`;
  - `transform`;
  - `r2_collection_sha256`;
  - `runtime_fingerprint_sha256`;
  - `equivalence_ledger_sha256`.
- `judge_sha256_from_inputs` reproduces **byte-for-byte** the netstring/count construction of `judge_sha256` (mutation.py 1137-1156, plus P3b's additions). Unknown or missing keys raise `ValueError`.

**`_load_validated_state_record` refactor:**
- It calls `validate_state_record_shape` for the structural checks.
- Existing behavior is unchanged: a structural failure raises `MutationStateError` (B021), and a judge mismatch returns `_RECORD_REJECTED` (B088), still checked **last**.

**New file `<state-dir>/CAMPAIGN-IDENTITY`** (JSON; written by `run_mutation`; owner `mutation.py`):

```json
{"schema_version": 1, "kind": "assay-campaign-identity", "lane": "self-qualification",
 "commit": "<40hex>", "git_tree": "<40hex>", "assay_version": "7.2.0.dev12+g1a2b3c4",
 "judge_sha256": "<64hex>", "judge_inputs": { ...exactly the dict above... },
 "shard": {"index": 0, "count": 4}, "selection_sha256": null,
 "campaign_deadline_sha256": "<64hex>", "written_at": "2026-10-03T11:02:07Z"}
```

- `shard` is `null` when unsharded; it is informational, because the judge excludes the shard.
- `selection_sha256` is P7's selection digest for a `--candidates-file` run, else `null`.
- `campaign_deadline_sha256` is the sha256 of the `--campaign-deadline` file bytes when given, else `null`.
- `commit` and `git_tree` are the values the verdict records.
- **Invalid examples:**
  - `judge_sha256` ≠ `judge_sha256_from_inputs(judge_inputs)` → `identity-inconsistent`;
  - `judge_inputs` has an extra key → `identity-inconsistent`;
  - a missing `selection_sha256` key → `identity-inconsistent`.
- The name has no `.json`, so no record glob and no existing test sees it (P9-1). P8 ignores it by name.

**New store files, none named `*.json`:**
- `STORE/.lock`: the flock target, empty.
- `STORE/.import-journal/<receipt-basename>.pending`: journal.
- `STORE/.import-audit/<LABEL>/<id>.json`: held-back audit sample. This is in a subdirectory, so the root globs never see it.

**New CLI** (owner: new module `src/assay/state_import.py`, registered in `cli.py`):

```
assay state import <lane> --file <assay.toml> --state-dir <STORE>
    --from <LABEL>=<DIR> [--from <LABEL>=<DIR> ...] --receipt <PATH>
    --require-campaign-deadline <DEADLINE.json> [--accept-unbound-records] [--worktree <dir>]
assay state audit-check <lane> --file <assay.toml> --state-dir <STORE>
    --import-receipt <PATH> [--import-receipt <PATH> ...] --out <PATH> [--worktree <dir>]
```

- `LABEL` matches `^[A-Za-z0-9._-]{1,64}$` and is unique across `--from`.
- `--receipt` and `--out` must not exist; publish with the `_write_new` pattern.
- `--state-dir` and every `--from` pass the **same** `_resolve_state_dir` visibility refusal.
- `--require-campaign-deadline` is **required**. The file must pass P6's deadline validation for this commit, tree, lane and version. If it has expired, the import still runs: import is not execution, and the deadline bounds the consolidating run.
- Add `src/assay/state_import.py` to **both** `judge.targets` lists in `assay.toml` (`tests/test_self_lane.py:128-135`).

### SHA256SUMS grammar (OC5)

- UTF-8, LF line endings only (no CR), and a final LF.
- Exactly one line per listed file: `<64 lowercase hex>` + two spaces + `<name>`. This is GNU text mode; the `*` binary marker is refused.
- `<name>` is a bare filename: no `/`, no leading `.`, not `SHA256SUMS`.
- Lines are sorted by `<name>` (bytewise), with no duplicates.
- The listed set must equal **exactly**: every `^[0-9a-f]{64}\.json$` file in the directory, plus `CAMPAIGN-IDENTITY`.
- An unlisted such file, a listed missing file, or any other regular file in the directory (a `PILOT-STATE` file is caught earlier as `source-pilot`) → `source-integrity`.
- Size ≤ 4 MiB.

### Required flow (order is normative)

1. **Arguments.**
   - `--receipt` must not exist.
   - `--state-dir` and every `--from` pass `_resolve_state_dir`.
   - Each `--from` is a directory.
   - Take the **store lock** (OC14); if it is held, exit 2 `store-locked`.
   - If any `STORE/.import-journal/*.pending` exists, exit 2 `journal-pending`, naming it. The operator inspects it; resume validation remains authoritative.
2. **The store identity.**
   - Load `STORE/CAMPAIGN-IDENTITY`. It is required; if absent, exit 2 `store-identity-missing`.
   - Validate its shape, and recompute `judge_sha256_from_inputs(judge_inputs)`. A mismatch is exit 2 `identity-inconsistent`.
   - Its `selection_sha256` must be `null` (else exit 2 `store-is-pilot`).
   - Its `campaign_deadline_sha256` must equal sha256(`--require-campaign-deadline`); a mismatch is exit 2 `store-identity-stale`.
3. **Bind the store identity** to the current state:
   - `lane`;
   - `commit == HEAD`;
   - `git_tree == HEAD^{tree}`;
   - `assay_version == assay.__version__`;
   - `judge_inputs.tool_version == assay.__version__`.

   Any mismatch is exit 2 `store-identity-stale`. On the host, this always fails, by design (OC15).
4. **Existing store records.**
   - Validate every existing `STORE/<64hex>.json` with `validate_state_record_shape`. A failure is exit 2 `store-corrupt`, naming the file, with nothing written.
   - Records with a different judge are stale (OC9).
5. **The current plan:** `cli.plan_jobs(lane, worktree=…)` (P8, C13) gives `plan_by_id`. If the plan is unsupported, exit 2.
6. **For each source**, in `--from` order:
   1. `PILOT-STATE` present → exit 2 `source-pilot` (OC13).
   2. `SHA256SUMS` passes the grammar, and every listed digest matches the bytes → else exit 2 `source-integrity`, naming the label.
   3. `CAMPAIGN-IDENTITY` validates as in step 2 → else exit 2 `source-identity-inconsistent`. Its `selection_sha256 != null` → exit 2 `source-pilot`.
   4. Record `identity_matches` (its judge equals the store's) and `differing_components` (the sorted `judge_inputs` keys that differ). A mismatching source is **not** refused wholesale; step 7 refuses its records individually, so the receipt shows why (for example, a different `runtime_fingerprint_sha256` means a different host environment).
7. **Per-record checks.** For each `<64hex>.json` record, keep only `(path, sha256, parsed signature fields)` in memory; never hold all payload bytes (P9-8). The first failing check is the refusal reason:

   | # | Check | Refusal reason |
   |---|---|---|
   | 1 | `validate_state_record_shape(payload, stem=…, plan_row=None)` passes: size ≤ `MUTATION_STATE_RECORD_LIMIT`, UTF-8 JSON object, `schema_version == 1`, `candidate_id == stem`, `outcome_bucket ∈ MUTATION_BUCKETS`, `_execution_from_state_record` succeeds, and `candidate_id_from_fields(record identity fields) == stem` | the shape reason: `oversized`, `malformed`, `schema-version` or `identity-fields` |
   | 2 | The candidate is in `plan_by_id` | `not-in-plan` |
   | 3 | The record's `path`, `operator`, `start_byte`, `end_byte`, `source_sha256` and `mutated_file_sha256` equal the plan row | `identity-fields` |
   | 4 | `outcome_bucket ∈ {killed, survived, equivalent}` | `non-final-bucket` (OC12) |
   | 5 | `judge_sha256` is a string equal to the store identity's | `judge-mismatch` |
   | 6 | `campaign_deadline_sha256` equals sha256(DEADLINE). A record without the key is accepted only with `--accept-unbound-records`. | `unbound-record` |

   Records that pass are *candidates for acceptance*.
8. **Conflict detection.** Consider all candidates for acceptance from all sources, plus every existing store record whose judge equals the store identity. Group them by candidate ID and compare conflict signatures (OC3):
   - The same signature: keep one (the store copy if present, else the first source), and mark the rest `duplicate_consistent`. `killed` records whose modes differ are also listed in `mode_differences`.
   - A different signature: a conflict. **Any conflict:** publish only the receipt (`result: "refused"`), exit 2, and leave the store root byte-for-byte unchanged.
9. **The audit sample (OC11).** Per source, over its records that would be written (not duplicates), select the audit sample. Those records are **held back**: they are written to `STORE/.import-audit/<LABEL>/<id>.json` instead of the store root, and listed as `audit_held`.
10. **The journal.** Write `STORE/.import-journal/<receipt-basename>.pending`: a JSON list of `{candidate_id, source, sha256, destination}` for every intended write.
11. **Write.** For each record to write:
    - **re-read** the source file;
    - recompute its sha256, which must equal both the `SHA256SUMS` digest and the step-7 digest (else abort: exit 2 `source-changed`, leaving the journal in place);
    - write with `_write_mutation_state_record(STORE, payload)`, canonical and atomic.

    A replaced stale store record is listed in `replaced_stale`. `record_sha256` is the sha256 of the bytes written.
12. **Finish.** Publish the receipt, remove the journal, and release the lock. Exit 0 if nothing was refused per record, else exit 1.

**`assay state audit-check`.** This runs after the consolidating `assay run`, inside the same gate arm, still under the lock.
- For each `audit_held` entry in each supplied import receipt:
  - the root record `STORE/<id>.json` must exist with the current store judge. That means consolidation executed it; if not, the result is `audit-not-executed`.
  - Its `outcome_bucket` must equal the held record's. If not, the result is `audit-disagreed`, and the source is named.
  - For `killed`, differing modes are informational.
- **Output:** writes `--out` `{schema_version:1, kind:"assay-state-audit-check", store_judge_sha256, sources:[{label, held, agreed, disagreed:[ids], not_executed:[ids]}], result}`.
- **Exit codes:** 0 when every held record agreed; 2 on any disagreement or not-executed. The disagreeing **source** must be discarded (re-import without it) and consolidation re-run. The gate fails.

### Import receipt

Owner `state_import.py`; schema `src/assay/schemas/state-import-receipt.schema.json`, with `"schema_version": {"const": 1}`.

**Valid example:**

```json
{"schema_version": 1, "kind": "assay-state-import", "lane": "self-qualification",
 "commit": "<40hex>", "store": "/abs/.assay/mutation-state-self-qualification", "store_judge_sha256": "<64hex>",
 "campaign": "b105-1a2b3c4d5e6f", "campaign_deadline_sha256": "<64hex>", "accept_unbound_records": false,
 "assay_version": "7.2.0.dev12+g1a2b3c4", "created_at": "2026-10-03T12:00:00Z",
 "result": "partial",
 "sources": [{"label": "hostb-s1", "path": "/abs/.assay/inbox/hostb-s1", "sha256sums_sha256": "<64hex>",
              "identity_judge_sha256": "<64hex>", "identity_matches": true, "differing_components": []}],
 "counts": {"examined": 941, "accepted": 920, "audit_held": 19, "duplicate_consistent": 1,
            "refused": 1, "not_written": 0, "replaced_stale": 0},
 "accepted": [{"candidate_id": "<64hex>", "source": "hostb-s1", "record_sha256": "<64hex>"}],
 "audit_held": [{"candidate_id": "<64hex>", "source": "hostb-s1", "record_sha256": "<64hex>", "outcome_bucket": "killed"}],
 "duplicate_consistent": [{"candidate_id": "<64hex>", "sources": ["store", "hostb-s1"]}],
 "mode_differences": [],
 "replaced_stale": [],
 "refused": [{"candidate_id": "<64hex>", "source": "hostb-s1", "reason": "judge-mismatch"}],
 "not_written": [],
 "conflicts": []}
```

**Receipt rules (P9-3):**
- `examined == accepted + audit_held + duplicate_consistent + refused + not_written`, where each term is the count.
- `replaced_stale` is a **subset** of `accepted`: every entry's `candidate_id` appears in `accepted`, and it is not added to the sum.
- Every other count equals the length of its list.
- **`result`:**
  - `accepted` ⇔ `refused == []` and `conflicts == []`;
  - `partial` ⇔ `refused != []` and `conflicts == []`;
  - `refused` ⇔ `conflicts != []`, `accepted == []`, `audit_held == []` and `replaced_stale == []`. In that case `not_written` lists every record that passed the per-record checks but was not written.
- `reason` is from the closed set: `oversized, malformed, schema-version, identity-fields, not-in-plan, non-final-bucket, judge-mismatch, unbound-record`.
- A conflict entry is `{"candidate_id", "a": {"source", "outcome_bucket", "mode", "node_id"}, "b": {...}}`.
- **Whole-import failure reasons** (stderr + exit 2, no receipt unless a conflict): `store-locked, journal-pending, store-identity-missing, identity-inconsistent, store-is-pilot, store-identity-stale, store-corrupt, source-pilot, source-integrity, source-identity-inconsistent, source-changed`.

**Invalid examples** (the schema or model must reject each):
1. `result: "accepted"` with a non-empty `refused`.
2. `counts.accepted: 920` with 919 `accepted` entries.
3. A `replaced_stale` entry whose `candidate_id` is not in `accepted`.

### Topology and namespaces

```
worker host W_k (inside tester-unified, gate arm b119-worker):
    exact-OID clone → run-venv → STATE_k = <project>/.assay/mutation-state-self-qualification (private, never shared)
    STATE_k/<id>.json + CAMPAIGN-IDENTITY + SHA256SUMS
        │ transfer (rsync/scp, any transport) — bytes only; `sha256sum -c SHA256SUMS` on arrival
        ▼
coordinator host (inside tester-unified):
    <project>/.assay/inbox/<LABEL>/                  ← PROVISIONAL (never read by `assay run`)
    gate arm b119-import  → assay state import … --state-dir STORE --require-campaign-deadline D
    STORE = <project>/.assay/mutation-state-self-qualification  ← ACCEPTED (+ .lock, .import-audit/, .import-journal/)
    gate arm self-qualification → assay run --resume --state-dir STORE --cold-witness --campaign-deadline D  (no --shard)
                                → assay state audit-check (if import receipts exist) → assay verify → report check
```

- Receipt paths are absolute inside the container.
- `.assay/` is gitignored, so the inbox is covered by the `_resolve_state_dir` visibility refusal.

### Gate arms (owner P9; `tools/self-qualification-gate.sh` + `run-gate.toml`; merge after P7b and P10c, per plan §11.6)

Add two arms. They use the same shared clone/build/venv steps and the same lane shape as P7b's `b110-screen` (tester-unified, 3 CPU / 2g / 8g):
- **`b119-worker`:**
  - reads `.assay/b119-shard`, which contains exactly `k/N` (a regex-validated single line);
  - requires `.assay/campaign-deadline-b105-<commit12>.json`, copied from the coordinator (absent → exit 2 before any run; this arm **never** runs `campaign init`);
  - runs `assay run self-qualification --shard k/N --cold-witness --resume --require-judge-provenance --state-dir .assay/mutation-state-self-qualification --campaign-deadline <D> --progress .assay/progress-b119-worker.jsonl --verdict-json .assay/verdict-b119-worker.json`;
  - then writes `SHA256SUMS` over the records and `CAMPAIGN-IDENTITY`;
  - prints `B119_WORKER_EXIT=` and `B119_WORKER_STATE=`;
  - the worker verdict is diagnostic only.
- **`b119-import`:**
  - if `.assay/campaign-deadline-b105-<commit12>.json` is absent, runs P6's `campaign init` exactly as the qualifying arm would (campaign `b105-<commit12>`, 8 h, both lanes). This is the only place besides the qualifying arm that creates it, and it runs in the container, never on the host (OC15);
  - if `.assay/inbox/` has subdirectories, runs one `assay state import` with every subdirectory as `--from <dirname>=<path>` in sorted order, `--receipt .assay/import-<commit12>-<UTC yyyymmddThhmmssZ>.json` and `--require-campaign-deadline <D>`;
  - on exit 0 or 1, moves the imported inbox dirs to `.assay/inbox-imported/<receipt-stem>/`;
  - prints `B119_IMPORT_EXIT=` and `B119_IMPORT_RECEIPT=`.

**The qualifying `self-qualification` arm** (a P9 edit, after P3d, P6, P7b and P10c):
- after `assay run` and **before** `assay verify`, if any `.assay/import-<commit12>-*.json` exists, run `assay state audit-check … --import-receipt <each> --out .assay/audit-check-<commit12>.json`;
- a non-zero exit fails the lane (exit 2), with the marker `B119_AUDIT_CHECK_EXIT=`.

### Bounds

- ≤ 64 `--from` sources.
- Records per source ≤ the lane's `max_mutants` (≤ 10,000).
- Each record ≤ 1 MiB.
- `SHA256SUMS` ≤ 4 MiB.
- In memory: at most `(path, sha256, signature)` per record.
- All reads go through `safeio.read_bounded_input` / `read_bounded_file`.

### Decision table (import)

| State | Exit | Store root written? | Receipt |
|---|---|---|---|
| Lock held, journal pending, store identity missing / inconsistent / pilot / stale, or store corrupt | 2 | no | not written (stderr names the reason) |
| A source is pilot, fails integrity, has an inconsistent identity, or changes during the write | 2 | no (a `source-changed` abort leaves the journal) | not written |
| At least one conflict | 2 | no | written, `refused` |
| Some per-record refusals, no conflicts | 1 | accepted records only (audit sample held back) | written, `partial` |
| All accepted or duplicate | 0 | yes (audit sample held back) | written, `accepted` |

### Consolidation contract (the existing resume path; tests prove it)

`assay run <lane> --resume --state-dir STORE --cold-witness …`, **without `--shard`**:
- re-runs both baselines;
- recomputes the judge;
- resumes every accepted root record through `_load_validated_state_record`;
- executes anything missing, rejected or held back (the audit sample);
- writes one ordinary verdict.

`audit-check` then compares the held-back records with the locally re-executed ones.

**`state import` is an early filter; the consolidating run plus audit-check is the authority.** For example, if the consolidating host's runtime fingerprint differs from the store identity, every record is rejected and re-executed; the P9 report states this.

**Consolidation cost (C7, a B119 acceptance measurement; diagnostic, never an oracle):**
- The P9 report measures the wall time of `state import` over 3,760 synthetic shape-valid records bound to a fixture store identity.
- It also measures the zero-execution consolidating `--resume` on the O3 fixture, which is dominated by the two baselines.
- It gives the formula: consolidation ≈ coverage baseline + no-cov baseline + per-record validation × records + audit sample × per-candidate cost.
- Plan §8 uses this in place of the dropped pilot phase-C measurement.

### Degrees of freedom

- Private helper names inside `state_import.py`.
- The order of per-record checks **within** one refusal reason.
- The stderr rendering.

## Worker runbook (host-agnostic; becomes `docs/CONSUMERS.md` "Distributed R2 workers" and the plan §9 appendix)

**Enrolment** (per host, by measurement, recorded in the campaign log):
- `nproc`;
- the effective `cpu.max`, `memory.max` and `memory.swap.max`, which must be ≥ the gate's 3 CPU / 2 GiB / 8 GiB, or the host's own documented cap;
- the tester-unified image digest (`docker image inspect … --format '{{.Id}}'`), which must equal the coordinator's;
- `assay --version` inside the image, which must equal the coordinator's;
- the NTP offset, ≤ 2 s (OC10).

After a first worker run, compare its `CAMPAIGN-IDENTITY.judge_inputs.runtime_fingerprint_sha256` with the coordinator's; they must be equal (import reports `differing_components`). Remote capacity counts as **zero until measured** (A-464), and it is never *trusted* beyond the audit sample (Trust model).

**Run:**
1. The coordinator fixes N (the shard count) once per campaign. **Never change N mid-campaign.**
2. The coordinator runs the gate arm **`b119-import`** once with an empty inbox. This creates the campaign deadline inside the container (OC15).
3. The coordinator writes `.assay/b119-shard` = `0/N` and runs **`b119-worker`**. This is shard 0 directly into STORE, and it writes STORE's `CAMPAIGN-IDENTITY`, which every import requires.
4. The coordinator copies `.assay/campaign-deadline-b105-<commit12>.json` to every worker.
5. Each worker W_k, on its own checkout of the exact commit, writes `.assay/b119-shard` = `k/N` and runs `b119-worker`. The per-host cgroup limits apply.
6. Transfer `STATE_k` (records, `CAMPAIGN-IDENTITY`, `SHA256SUMS`) to the coordinator's `.assay/inbox/<host>-s<k>/`, then `sha256sum -c SHA256SUMS` on arrival.
7. On the coordinator, run **`b119-import`**, once per arriving batch or batches.
   - Exit 1: inspect the `refused` reasons. `judge-mismatch` means that host's environment differs; stop using it.
   - Exit 2 with conflicts: **stop the campaign.** It is a nondeterminism finding; file it and do not pick a side.
8. Consolidate with the qualifying `self-qualification` arm (plan §9.3). It runs `audit-check`, and any disagreement discards that source and fails the gate. Retain every import receipt and the audit-check result with the gate evidence.

**Never:**
- share a state directory between hosts (NFS, etc.) or between concurrently running invocations; the lock refuses the latter;
- hand-edit a record; import refuses it and resume treats it as corruption (B021);
- use `--accept-unbound-records` unless the operator has answered D7 "yes" (plan §11.2).

## Work

1. Refactor to `judge_identity_inputs` / `judge_sha256_from_inputs`. Prove equality with the existing digest (O1) before anything else. Commit.
2. Extract `validate_state_record_shape`; `_load_validated_state_record` uses it, and all existing tests stay green. Commit.
3. Write `CAMPAIGN-IDENTITY` from `run_mutation` (OC2), and add the store lock (OC14) to `assay run` and the new commands. Commit.
4. Implement `src/assay/state_import.py`: `import` (flow steps 1-12) and `audit-check`, the receipt model and schema, and CLI registration. Add the module to both target lists.
5. Write fast unit tests on synthetic stores in `tests/test_state_import.py`.
6. Write real-run tests in `tests/zz_slow/test_state_import_real_runs.py`: the `_seed_pytest_mutation`-style tiny project, real `runner.run_lane`, `--shard 0/2` / `1/2`, and a fixture deadline file (use P6's `campaign init` in-process).
7. Add the gate arms `b119-worker` and `b119-import`, the qualifying-arm `audit-check` step, and the run-gate lanes. Extend the `tests/test_self_lane.py` pins with `b119-worker`, `b119-import`, `B119_IMPORT_EXIT=`, `B119_AUDIT_CHECK_EXIT=` and `--require-campaign-deadline`.
8. Measure consolidation cost (C7), and record it in the report as diagnostic.
9. Docs and runbook (Docs sync), the B119 backlog entry, and CHANGES. If a dataclass is added or changed, regenerate `tests/fixtures/dataclass-contract.json` with P1's documented command (C15).
10. Run the focused tests, then the gates. Write the report.

## Oracles

| # | Oracle (observable) | Negative it distinguishes |
|---|---|---|
| O1 | For 5 existing judge-identity test vectors, `judge_sha256_from_inputs(judge_identity_inputs(x)) == judge_sha256(x)`. The expected hex values are **captured from the pre-refactor function at HEAD and committed as literals** before the refactor. All `tests/test_mutation_judge_identity*.py` tests stay green unchanged. | Both digests built from the same new dict builder, so they drift together |
| O2 | After a native-R2 run with a store, `CAMPAIGN-IDENTITY` exists, recomputes to its own `judge_sha256`, and that equals every record's `judge_sha256`. `sorted(state_dir.glob("*.json"))` returns exactly the records, so the existing glob tests stay green. | `campaign-identity.json` breaking `len(glob) == 2` (P9-1) |
| O3 | **Two shard stores** (real runs): shard `0/2` into STORE, shard `1/2` into S1. Add `SHA256SUMS` and import S1. The receipt is `accepted` and the counts reconcile. The **consolidating `--resume`** executes exactly the held-back audit sample of S1: the tracked process runner sees the baselines plus those candidates only. `audit-check` gives 0. The verdict passes `assay verify` with `[]`, and `candidate_ids` equals the full plan. A **runtime-fingerprint change** (a monkeypatched fingerprint function) before consolidation forces every record to re-execute. | Consolidation that re-executes everything; skipping validation when the records are genuine |
| O4 | **Conflicts.** (a) A second source whose copy of an S1 record has `outcome_bucket` flipped, with `SHA256SUMS` regenerated, gives exit 2, `conflicts` naming both sources, and the store root unchanged (directory hash before and after). (b) A **store-vs-source** conflict (the store already holds `killed`; the source says `survived`) gives the same result. (c) Two `killed` records differing only in mode are **not** a conflict; they are listed in `mode_differences`. | "Last writer wins"; checking conflicts only between sources; mode-only conflicts |
| O5 | **Foreign identity:** a record from a run whose lane `env` differs is refused `judge-mismatch`, the rest are accepted, exit 1, `result: partial`, and `differing_components` contains `env_declared`. The check uses **each record's** judge, not the source's `CAMPAIGN-IDENTITY`. A source identity that matches, carrying one record with a different judge, refuses that record. | Trusting the source identity file instead of each record |
| O6 | **Stale tree:** records produced at commit X, then a test-only commit X′. At X′, run shard `0/2` into STORE, which rewrites `CAMPAIGN-IDENTITY` for X′ before executing. Import X's shard-1 records: every one is refused `judge-mismatch`, and a later `--resume` executes them all. | Trusting unchanged mutant bytes after a test change (B088) |
| O7 | **Integrity:** a missing `SHA256SUMS`, an extra unlisted record, a CRLF line, a `*` binary marker, an unsorted file, and one flipped byte in a record each give exit 2 `source-integrity` with nothing written. A source file modified **between validation and write** (a test hook between steps 7 and 11) gives exit 2 `source-changed`, and the journal is left. | Importing corrupted transfers; verify-then-reread |
| O8 | **Identity binding:** a tampered store `CAMPAIGN-IDENTITY` (changed `judge_inputs.transform`) gives `identity-inconsistent`. A store identity at another commit, **or** another `git_tree`, **or** another `assay_version`, **or** a different deadline sha, gives `store-identity-stale`. | Binding only the commit |
| O9 | **Stale store record:** a store record with an older judge plus a matching source record → replaced atomically; its ID is listed in both `replaced_stale` and `accepted`. | Keeping stale records, or treating stale as conflict |
| O10 | **Receipt schema:** the receipt and the audit-check output validate against their schemas; the three invalid examples fail. | Receipt drift |
| O11 | **Destination guards:** `--receipt`/`--out` pointing at an existing file are refused before any write. A git-visible `--state-dir` or `--from` inside the tree is refused. | Clobbering evidence, or a dirty tree |
| O12 | **Non-final buckets:** a `budget_exceeded`, a `hung` and a `crashed` record are each refused `non-final-bucket`, and consolidation re-executes them. | Replaying load-induced worker outcomes |
| O13 | **Pilot:** a source containing `PILOT-STATE`, and a source whose identity has a non-null `selection_sha256`, each give exit 2 `source-pilot`. | Importing pilot state (A-474) |
| O14 | **Deadline binding (D7 = NO):** a record without `campaign_deadline_sha256` is refused `unbound-record`, but accepted with `--accept-unbound-records`. A record with a **different** deadline sha is refused even with the flag. | Accepting screen records by default |
| O15 | **Lock:** a held `STORE/.lock` (taken by the test in-process) gives `state import` exit 2 `store-locked`, and `assay run --state-dir STORE` a whole-lane `BAD_LANE_CONFIG` with no candidate executed. A leftover `.pending` journal gives `journal-pending`. | A time-of-check/time-of-use race between a shard run and import |
| O16 | **Audit sample:** a forged sole-copy S1 record (bucket flipped from `survived` to `killed`, judge copied, `SHA256SUMS` regenerated) inside the deterministic sample. Consolidation re-executes it locally as `survived`, and `audit-check` exits 2 naming S1 with `audit-disagreed`. A forged record **outside** the sample is not caught; the test asserts this documented residual, so the trust-model statement stays honest. The sample IDs are the lowest `blake2b(campaign:id)` ranks. | "Audit" that re-validates only identities; a sample the worker could choose |
| O17 | **Corrupt store:** an existing malformed `STORE/<id>.json` gives exit 2 `store-corrupt`, naming it, with nothing written. | Silently overwriting evidence |

**Gate observable:** `tester-unified` PASS and `self-qualification-preflight` PASS. The new module must reach 100% line and branch coverage and add no exclusions.

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

- **README (WHAT):** "Distributed R2 evidence". Records are identity- and deadline-bound; `assay state import` refuses conflicts, non-final buckets, pilot state and unbound records; an audit sample is re-executed; the final claim is always one consolidating `--resume` verdict plus a passing `audit-check`.
- **DESIGN-GUIDE (WHY):**
  - the **trust model** verbatim, including the residual-risk formula;
  - why the identity file exists and why it has no `.json` extension;
  - why a conflict is refusal-worthy nondeterminism;
  - why non-final buckets are refused;
  - why import is a filter and consolidation plus audit-check is the authority;
  - why "accepted" is store membership plus receipt plus audit-check;
  - the lock;
  - shards are scheduling (A-464);
  - the D7 default (NO) and what a "yes" would change.
- **CONSUMERS (HOW):** the worker runbook (pasteable), the receipt and audit-check fields, the gate arms, and the exit codes.
- **CHANGES.md:** `### Added`.
- **Backlog:** update B119's status.

## Scope / forbid

**Touch:**
- `src/assay/mutation.py`: the identity refactor, `validate_state_record_shape`, and the identity-file write only;
- `src/assay/cli.py`: registration, plumbing, and the store lock in `_resolve_state_dir`'s caller;
- `src/assay/state_import.py` (new);
- `src/assay/schemas/state-import-receipt.schema.json` and `…/state-audit-check.schema.json` (new);
- `assay.toml` (the two target lists);
- `tools/self-qualification-gate.sh` (the `b119-worker` and `b119-import` arms, and the audit-check step in the qualifying arm);
- `run-gate.toml` (the two lanes);
- `tests/test_self_lane.py` (pins only);
- `tests/test_state_import.py` and `tests/zz_slow/test_state_import_real_runs.py` (new);
- the existing judge-identity tests, **only** by adding O1 vectors;
- `tests/fixtures/dataclass-contract.json` (regeneration only, C15);
- README, DESIGN-GUIDE, CONSUMERS, CHANGES, the backlog and the report.

**Forbid:**
- any verdict/schema wire change (OC7);
- `MUTATION_STATE_SCHEMA_VERSION` (stays 1);
- `merge_mutation_shards` semantics;
- run-gate or Buildkite **code**;
- actually enrolling or using remote hosts (runbook only);
- any new `ReasonCode`;
- any host-side `campaign init` or import path.

## Gate

1. Run the focused tests, serially: `nice -n 19 ionice -c3 python -m pytest tests/test_state_import.py tests/zz_slow/test_state_import_real_runs.py tests/test_mutation_judge_identity.py tests/test_mutation_progress_budget_plan.py tests/test_state_dir_resume.py tests/test_mutation_state_crash_tails.py tests/test_mutation_resume_sharding.py tests/test_self_lane.py -q -p no:cacheprovider`. If P1 moved `test_mutation_judge_identity_properties.py` into `tests/zz_slow/`, add it at its new path.
2. Run `cd <worktree>/assay && python ./run-gate.py tester-unified`. Then, **in a separate step**, read `ASSAY_GATE_CONTAINER_EXIT` and `ASSAY_REGISTERED_GATE_COMPLETE` from the log (L4).
3. Run `python ./run-gate.py self-qualification-preflight`. Never run the new `b119-*` arms against the real campaign here; the real-run tests use the tiny fixture.

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
- `cli.plan_jobs()` (P8) is absent, or its rows lack the identity digests.
- P6 state records do not carry `campaign_deadline_sha256`, or P7 does not write `PILOT-STATE`/`selection_sha256`.
- O3 requires a verdict/schema change.
- The tracer-bullet probe (carve log) showed consolidation re-executing copied records.

## Report

Write `nyxloom-trove/reports/assay-B110-P9-REPORT.md` with:
- the accepted design choices (OC1–OC15, noting any controller override);
- a traceability table (`work | owner | oracle | test | controlled break`) with real test names and red-first counts;
- the gate verdicts, read separately;
- the consolidation-cost measurement (C7, diagnostic);
- the explicit statements "import is a filter; consolidation plus audit-check is the authority" and the trust model's residual risk;
- residuals: signed bundles (deferred), the verdict provenance field (v15, deferred), and the audit sample's detection limit.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`
