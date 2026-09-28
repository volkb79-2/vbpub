# B110-P9 — Distributed / async evidence: campaign identity, `assay state import`, audit sample, consolidation, worker runbook

*Revised 2026-09-28 after round-1, round-2 and round-3 reviews (see REVIEW-2026-09-28-round{1,2,3}.md). Round 3 adds:<br>• P9R3-1: `assay state discard-source` and the `audit-evidence-missing` completeness rule (O23);<br>• P9R3-2: audit-check accepts a matching `candidate` event from any run segment started after the receipt's `created_at` (O22);<br>• P9R3-3: held records are written before root records, and `journal-pending` recovery is a byte-matched `rollback-journal` (O24);<br>• O3's `len(S1) ≥ 8` fixture precondition. Round-1: findings P9-1..P9-10 and carver decisions C7, C8, C12, C13, C15. Round-2: findings P9R2-1..P9R2-11 and plan N-2, N-3, N-7, N-8; carver decision C27.*

| Field | Value |
|---|---|
| Backlog | B119, plan package P9 |
| Branch | `assay-b110-p9-distributed`, cut from the integration line (`assay-b110-integration`, per plan §11.1 / C18) after these have merged: `assay-b110-v14` (P3a–P3d), P6, P7 + P7b, P8 and P1. **P10c and P9's gate arms go in either order** (plan §11.6, round-2 N-1): whichever merges second rebases onto the other's `self-qualification-gate.sh` / `run-gate.toml` / `tests/test_self_lane.py` edits. P9 never waits on P10c. |
| Depends on | **P3b**: judge identity `/3` over A-470's canonical list: the cold policy, the transform id, the R2 collection digest, the R2 hook and runtime fingerprints, and the coverage hook and runtime fingerprints (7 facts), plus the ledger sha when declared. The hook-fingerprint paths must be normalized relative to site-packages/purelib and plugin tokens must be id-free (P3B-4); otherwise resumed evidence fails verification across venvs. **P6**: the campaign deadline file, and state records carrying `campaign_deadline_sha256` when written under `--campaign-deadline` (C8). **P7**: pilot runs write the `PILOT-STATE` sentinel into their state dir and record `selection_sha256`. P7's C5 test asserts the pilot state dir through `glob("*.json")` (C27), which tolerates P9's `.lock` and `CAMPAIGN-IDENTITY`. **P8**: `cli.plan_jobs()`, whose plan rows carry `source_sha256`/`mutated_file_sha256` (C13), and `campaign.py`'s local record-shape checks, which P9 replaces with the shared validator. **P1**: the `tests/zz_slow/` tier. **Plan §11.2**: D7 defaults to **NO** until the operator answers (C8). A "yes" **also requires a P6 change**: P6's loader rejects records without `campaign_deadline_sha256` under `--campaign-deadline --resume`, so an unbound record imported with `--accept-unbound-records` would still be rejected and re-executed at consolidation (P9R2-8, N-8). |
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

**Mitigation (C12, C27): the audit sample.**
- For each source, a sample of the records that would be accepted is **held back** from the store. The sample size is `k = min(n, max(5, ceil(0.02 × n)))`, where `n` is the number of that source's records that would be written.
- The consolidating run therefore re-executes those candidates locally.
- `assay state audit-check` then compares each local outcome with the held-back worker record. Any bucket disagreement refuses **that whole source** and fails the gate.
- **The sample must be unpredictable to workers (C27, round-2 N-3 / P9R2-2).**
  - It is seeded from the campaign name **plus a nonce** that `state import` draws **after every source's `SHA256SUMS` has verified**: 32 bytes from `secrets.token_bytes(32)`.
  - The nonce is recorded in the receipt as `audit_nonce` (64 hex), so the sample is reproducible from the receipt.
  - It is drawn through one module-level seam, `state_import._draw_audit_nonce() -> bytes`, which tests monkeypatch.
  - The campaign name alone is public (`b105-<commit12>`), so a worker holding the deadline file could otherwise compute the sample and lie only outside it.

**Residual risk (state it honestly).**
- For **non-adaptive** faults (bugs, nondeterminism, or a worker that cannot predict the nonce), a worker whose records are false with fraction f is caught with probability 1 − (1 − f)^k. A worker with a single false record out of 1,000 has about a 2% chance of being caught per campaign.
- The formula covers only non-adaptive faults. It relies on the nonce being drawn after the worker's bytes are fixed and verified. A worker that could influence or observe the nonce before its records are fixed would defeat it; the import order (verify all sources, then draw) is what prevents that.
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
   - `_resolve_state_dir` 845-878 (git-visibility refusal), and its pre-run `LaneConfigError` → `BAD_LANE_CONFIG` with **no verdict** (`cli.py:984/994`, `errors.py:311-323`), which is the precedent for the store-lock refusal;
   - the `run` parser 255-328;
   - `_cmd_plan` 1571-1886;
   - after P8: `plan_jobs()` and the rows that carry `source_sha256`/`mutated_file_sha256`;
   - `src/assay/candidate_identity.py:8` `candidate_id_from_fields`.
6. `src/assay/analysis.py:1006-1020` `_write_new`: publish without overwriting (the pattern for the receipt).
   - `src/assay/isolation.py:177-185` `SnapshotSpec`: it carries only the commit. `run_mutation` has neither the lane name nor the tree, so `runner.py` must thread `lane` and `git_tree` into `run_mutation` (threading only; P9R2-9). `git_tree` is `git rev-parse HEAD^{tree}`, computed exactly as P6 does. **The verdict does not record `git_tree`.**
   - After P8: `src/assay/campaign.py`'s local record-shape checks, which P9 replaces with `validate_state_record_shape`.
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

Record the commands and results in the P9 carve log, `nyxloom-trove/reports/assay-B110-P9-CARVE-LOG.md`. If step 4 executes any candidate, the consolidation contract is wrong, and P9 goes back to the carver.

## Design packet (2a → 2b; the carver's choice for each)

| # | Open choice | Admissible options | Invariant | Carver's choice and deciding evidence |
|---|---|---|---|---|
| OC1 | Where import gets "this campaign's identity" | (a) recompute from `assay plan`; (b) an identity file written by a real baseline run; (c) trust each worker | "A check is only as strong as what it compares." The identity includes `tree_sha256`, the R2 collection digest and the runtime fingerprint, none of which can be derived without running the baselines. | **(b).** (a) cannot produce the runtime fingerprint; (c) is self-attestation. The store identity comes from the coordinator's own run (the `b119-worker` arm, shard 0 into STORE). |
| OC2 | Identity file name and write time | any | It must never collide with record globs (P9-1), and must describe the records beside it. | **`<state-dir>/CAMPAIGN-IDENTITY`** (JSON content, no extension). It is written by every native-R2 run with `state_root is not None`, right after `judge` is computed (mutation.py ~2353) and before any candidate executes. The write is atomic and replaces any older file. It happens inside the process that holds the store lock (OC14); `run_mutation` itself never takes the lock. |
| OC3 | Duplicate vs. conflict | byte equality; a semantic signature | Timing differences are not nondeterminism. A different bucket is. | **Conflict signature = (`judge_sha256`, `outcome_bucket`).** The same signature is `duplicate_consistent`. Two `killed` records that differ only in `execution.mode`/witness node are **not** a conflict; they are reported in `mode_differences` (P9-9). **Counting (P9R2-11):** `counts.duplicate_consistent` counts **records**, i.e. every non-kept copy: Σ over groups of `len(sources) − 1`. Each list entry is one group with all its `sources`, the kept one first. In a `refused` import, duplicates are still counted in `duplicate_consistent`, never in `not_written`. |
| OC4 | Partial acceptance | all-or-nothing; per record | A conflict is campaign-level; a foreign, stale or non-final record is a per-record fact. | **Per-record refusals** (the rest are accepted, exit 1). **Any conflict** → the whole import is refused, nothing is written to the store root, exit 2. |
| OC5 | Transfer integrity | none; `SHA256SUMS`; signed | The bytes must equal what the worker wrote. | **Mandatory `SHA256SUMS`** with a fixed grammar (see *SHA256SUMS grammar*). Signing is deferred. |
| OC6 | Where "accepted" lives | a record field; store membership plus a receipt | A worker must not forge acceptance. | **Store membership + a retained import receipt + a passing audit-check.** No field in the record. |
| OC7 | Imported provenance on the verdict wire | v15 field; none | No schema change in P9 | **None.** Receipts and audit-check results are retained gate evidence. |
| OC8 | Records not bound to the current campaign deadline (screen, pre-init workers) | accept; refuse; flag-gated | D7 is pending and defaults to NO (C8) | **Refuse by default:** a record whose `campaign_deadline_sha256` ≠ sha256(`--require-campaign-deadline` FILE) is refused `unbound-record`. `--accept-unbound-records` accepts only records that **lack** the field, never a *different* deadline. The runbook forbids that flag unless the operator answers D7 yes. **A "yes" also needs a P6 change** (P9R2-8): P6's loader rejects unbound records under `--campaign-deadline --resume`, so without that change imported unbound records would still be re-executed at consolidation. P9 does not make that P6 change. |
| OC9 | An existing store record with a different judge (stale) | keep; replace; refuse | Resume treats it as absent | **Replace it atomically.** It is listed in `replaced_stale`, which is a subset of `accepted`. |
| OC10 | Worker clock skew | ignore; measure | The deadline is absolute UTC | **Measure at enrolment**: NTP offset ≤ 2 s. A runbook rule, not code. |
| OC11 | Worker honesty | trust; re-execute everything; audit sample | Complete-inventory claim; bounded extra cost | **Audit sample, nonce-seeded (C27; see Trust model).**<br>• Per source, `k = min(n, max(5, ceil(0.02 × n)))`.<br>• Selection: the `k` lowest values of `blake2b((campaign + ":" + audit_nonce_hex + ":" + candidate_id).encode("utf-8"), digest_size=32).hexdigest()`. `campaign` is the deadline file's `campaign` field, and `audit_nonce_hex` is the receipt's `audit_nonce`.<br>• Held-back records go to `STORE/.import-audit/<receipt-stem>/<LABEL>/<id>.json`, outside the root glob and namespaced by receipt stem. **A held record never becomes a root record** (P9R2-3).<br>• `assay state audit-check` verifies them after consolidation. |
| OC12 | Non-final buckets | accept; refuse | A-464: host load must not decide a campaign | **Refuse `hung`, `budget_exceeded` and `crashed` per record** (`non-final-bucket`), so consolidation re-executes them locally (P9-9). Otherwise a load-induced worker result could be replayed or become a conflict. |
| OC13 | Pilot state | ignore; refuse | A-474: pilot state is never imported | **Refuse the whole source** (`source-pilot`, exit 2) if it contains `PILOT-STATE` or its `CAMPAIGN-IDENTITY.selection_sha256` is non-null (P9-6). |
| OC14 | Concurrency | none; lock | No time-of-check/time-of-use gap between a running shard and an import (P9-8) | **An exclusive `fcntl.flock(LOCK_EX \| LOCK_NB)` on `STORE/.lock`**, taken **once per process** (P9R2-5):<br>• `assay run` takes it in `_cmd_run`, right after `_resolve_state_dir`, whenever a state dir is set. It holds it through that open file descriptor until the process exits. `runner.run_lane` and `run_mutation` **never** call `flock` again: a second `flock` on a separate open file description would conflict with the first and make the run refuse itself.<br>• `assay state import` and `assay state audit-check` each take it once, in their own process.<br>• **The gate script never locks.** Its arms run `assay run`, `import` and `audit-check` as separate, sequential processes.<br>• **A held lock is a refusal.** Import and audit-check exit 2 `store-locked`. `assay run` raises a pre-run `LaneConfigError` "state dir is locked by another assay process". That is `ERROR/BAD_LANE_CONFIG` with **no verdict written**, the `_resolve_state_dir` precedent (`cli.py:984/994`). |
| OC15 | Host vs. container | host import; container import | `assay_version` must equal the exact-OID wheel (P9-4) | **Import runs only inside tester-unified** through the gate arm `b119-import`, which does `campaign init` (when absent) and then `state import`. Shard 0 runs into STORE through `b119-worker`. STORE is the gate's `.assay/mutation-state-self-qualification`. There is never a host-side `campaign init` or import. |
| OC16 | Ledger candidates (P9R2-6) | sample them; exclude from the sample; refuse | Ledger placement ignores stored records for placed candidates (plan D6 addenda), so a held ledger candidate would never be executed and audit-check would fail deterministically. | **Refuse per record** with reason `ledger-candidate` whenever the lane declares `judge.mutation.equivalence_ledger` and the record's candidate resolves to a ledger entry. Resolve it with P10's resolver when present; before P10b lands no lane declares a ledger, so the rule is inert. Refused ledger records are never sampled or written. |

**No D-decision beyond A-471 is needed.** OC8's default (NO) holds until the operator answers plan §11.2.

**Carve log (P9-10).** The tracer-bullet probe's commands and results go in `nyxloom-trove/reports/assay-B110-P9-CARVE-LOG.md`. The controller writes it before dispatch; the implementer reads it and cites it in the report.

## Implementation packet (normative, after the design is accepted)

### Owned interfaces

**Owner `src/assay/mutation.py`.** There is one identity derivation; do **not** duplicate it.

```python
# Keyword set and names copied VERBATIM from P3b's judge_sha256 (P3b brief, "Owned interfaces";
# authority: A-470's canonical list of 7 campaign-level facts + the ledger sha; P9R2-1, N-7).
def judge_identity_inputs(*, tree_sha256: str, plan: CommandPlan, link_paths: Sequence[str] = (),
                          tool_version: str | None = None,
                          cold_witness_kills: bool = False,
                          r2_transform: str | None = None,
                          r2_collection_sha256: str | None = None,
                          r2_hook_fingerprint_sha256: str | None = None,
                          runtime_fingerprint_sha256: str | None = None,
                          coverage_hook_fingerprint_sha256: str | None = None,
                          coverage_runtime_fingerprint_sha256: str | None = None,
                          equivalence_ledger_sha256: str | None = None) -> dict: ...
def judge_sha256_from_inputs(inputs: Mapping[str, Any]) -> str: ...   # the ONLY digest implementation
def judge_sha256(**same_kwargs) -> str:                               # == judge_sha256_from_inputs(judge_identity_inputs(...))
def validate_state_record_shape(payload: object, *, stem: str, plan_row: Mapping[str, Any] | None,
                                cold_witness_kills: bool) -> str | None:
    """Return None if valid, else the refusal reason (closed set below). Shared by
    `_load_validated_state_record` (which maps a non-None reason to MutationStateError),
    `state import`, and `campaign.py` (P8), so the validators cannot drift (P9-10, P9R2-11).
    `cold_witness_kills` selects P3b's evidence-shape rules (P3b: the evidence checks apply
    only to cold campaigns)."""
```

**`judge_identity_inputs`:**
- **The keyword set and names are exactly P3b's `judge_sha256` signature above**, and A-470 is the authority. This brief deliberately fixes no other shape. If P3b's signature as merged differs from it, stop: `BLOCKED: P9 identity keys differ from P3b/A-470` (P9R2-1).
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
  - `r2_transform`;
  - `r2_collection_sha256`;
  - `r2_hook_fingerprint_sha256`;
  - `runtime_fingerprint_sha256` (the **R2** baseline's runtime fingerprint, under P3b's name);
  - `coverage_hook_fingerprint_sha256`;
  - `coverage_runtime_fingerprint_sha256`;
  - `equivalence_ledger_sha256`.
- `judge_sha256_from_inputs` reproduces **byte-for-byte** the netstring/count construction of `judge_sha256` (mutation.py 1137-1156, plus P3b's additions). Unknown or missing keys raise `ValueError`.

**`_load_validated_state_record` refactor:**
- It calls `validate_state_record_shape` for the structural checks.
- Existing behavior is unchanged: a structural failure raises `MutationStateError` (B021), and a judge mismatch returns `_RECORD_REJECTED` (B088), still checked **last**.

**`campaign.py` (P8) switch.** Replace P8's local record-shape checks with a call to `validate_state_record_shape`. The rule list is identical by construction, and P8's tests must stay green unchanged. After this there are exactly two callers of one validator, never three validators (P9R2-11).

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
- **`lane`, `commit` and `git_tree` provenance (P9R2-9).**
  - `lane` is the lane name, threaded from `runner.py` into `run_mutation` as a new keyword (threading only).
  - `commit` is `prepared.spec.commit`.
  - `git_tree` is `git rev-parse <commit>^{tree}`, computed once in `runner.py` exactly as P6 computes it for the deadline, and threaded in.
  - The verdict does **not** record `git_tree`. Never read it from the verdict.
- **Invalid examples:**
  - `judge_sha256` ≠ `judge_sha256_from_inputs(judge_inputs)` → `identity-inconsistent`;
  - `judge_inputs` has an extra key → `identity-inconsistent`;
  - a missing `selection_sha256` key → `identity-inconsistent`.
- The name has no `.json`, so no record glob and no existing test sees it (P9-1). P8 ignores it by name.

**New store files, none named `*.json` at the store root:**
- `STORE/.lock`: the flock target, empty. Every CLI run that sets a state dir leaves it behind, including on workers.
- `STORE/.import-journal/<receipt-basename>.pending`: journal.
- `STORE/.import-audit/<receipt-stem>/<LABEL>/<id>.json`: held-back audit sample, namespaced by the import receipt's stem (P9R2-3). This is in a subdirectory, so the root globs never see it.

**New CLI** (owner: new module `src/assay/state_import.py`, registered in `cli.py`):

```
assay state import <lane> --file <assay.toml> --state-dir <STORE>
    --from <LABEL>=<DIR> [--from <LABEL>=<DIR> ...] --receipt <PATH>
    --require-campaign-deadline <DEADLINE.json> [--accept-unbound-records] [--worktree <dir>]
assay state audit-check <lane> --file <assay.toml> --state-dir <STORE>
    --progress <consolidating-run progress.jsonl> --require-campaign-deadline <DEADLINE.json>
    --out <PATH> [--worktree <dir>]
assay state discard-source <lane> --file <assay.toml> --state-dir <STORE>
    --receipt-stem <S> --label <L> --out <discard-receipt PATH> [--worktree <dir>]
assay state rollback-journal <lane> --file <assay.toml> --state-dir <STORE>
    --out <rollback-receipt PATH> [--worktree <dir>]
```

**`discard-source` (round-3 P9R3-1).** This is the only way to drop a source whose audit sample disagreed.
- "Re-import without it" is **not** an operation: that source's unsampled accepted records are already root records, and re-importing never removes them.
- It runs under the store lock (OC14) and reads receipt `.assay/<S>.json`.
- It deletes every root record `STORE/<id>.json` listed in that receipt's `accepted` for label `L` **whose on-disk sha256 equals the receipt's `record_sha256`**. A record whose bytes changed since import (for example, re-executed by consolidation) is left alone and listed as `kept_changed`.
- It removes `STORE/.import-audit/<S>/<L>/`.
- It writes the **discard receipt** `--out` (`.assay/discard-<S>-<L>-<UTC>.json`): `{schema_version:1, kind:"assay-state-discard", receipt_stem, label, deleted:[{candidate_id, record_sha256}], kept_changed:[ids], held_removed:n, created_at}`.
- Exit 0, or exit 2 on a missing receipt, an unknown label, or a held lock.
- Consolidation then re-executes the deleted candidates.

**`rollback-journal` (round-3 P9R3-3).** Recovery from `journal-pending`, under the lock. For every journaled entry:
- delete the destination **only if its bytes' sha256 equals the journaled `sha256`**;
- restore nothing. A replaced stale record is simply gone, and consolidation re-executes the candidate.

Then remove the journal and write a rollback receipt. Entries whose destination bytes differ from the journal are listed and left alone. Exit 0; exit 2 if the lock is held.

- `LABEL` matches `^[A-Za-z0-9._-]{1,64}$` and is unique across `--from`.
- `--receipt` and `--out` must not exist; publish with the `_write_new` pattern. The gate names both with a UTC timestamp, so a re-invocation never collides (P9R2-11).
- `audit-check` takes **no** receipt arguments. It discovers every held record under `STORE/.import-audit/` and reads each one's receipt, `<receipt-stem>.json` in `.assay/`, by stem. So deleting a receipt cannot skip the audit: a held directory without its receipt is `audit-receipt-missing` (P9R2-3).
- `--state-dir` and every `--from` pass the **same** `_resolve_state_dir` visibility refusal.
- `--require-campaign-deadline` is **required**. The file must pass P6's deadline validation for this commit, tree, lane and version. If it has expired, the import still runs: import is not execution, and the deadline bounds the consolidating run.
- Add `src/assay/state_import.py` to **both** `judge.targets` lists in `assay.toml` (`tests/test_self_lane.py:128-135`).

### SHA256SUMS grammar (OC5)

- UTF-8, LF line endings only (no CR), and a final LF.
- Exactly one line per listed file: `<64 lowercase hex>` + two spaces + `<name>`. This is GNU text mode; the `*` binary marker is refused.
- `<name>` is a bare filename: no `/`, no leading `.`, not `SHA256SUMS`.
- Lines are sorted by `<name>` (bytewise), with no duplicates.
- The listed set must equal **exactly**: every `^[0-9a-f]{64}\.json$` file in the directory, plus `CAMPAIGN-IDENTITY`.
- **Ignored entries (P9R2-4):** exactly `.lock`, which every CLI run leaves behind, and `SHA256SUMS` itself. Nothing else is ignored.
- An unlisted record, a listed missing file, or any other entry in the directory (a regular file, a directory, a symlink) → `source-integrity`. A `PILOT-STATE` file is caught earlier as `source-pilot`.
- Size ≤ 4 MiB.
- **Gate recipe (bytewise order):** the `b119-worker` arm writes it with the C locale pinned. In bytewise order `0`-`9` sort before `C`, which sorts before `a`-`f`:

  ```bash
  ( cd "$state" && export LC_ALL=C && \
    ls -1 | grep -E '^([0-9a-f]{64}\.json|CAMPAIGN-IDENTITY)$' | sort | \
    xargs -r sha256sum > SHA256SUMS.tmp && mv SHA256SUMS.tmp SHA256SUMS )
  ```

  `SHA256SUMS.tmp` is never left behind: `mv` is atomic, and a failure leaves no `SHA256SUMS`, which import refuses.

### Required flow (order is normative)

1. **Arguments.**
   - `--receipt` must not exist.
   - `--state-dir` and every `--from` pass `_resolve_state_dir`.
   - Each `--from` is a directory.
   - Take the **store lock** (OC14); if it is held, exit 2 `store-locked`.
   - If any `STORE/.import-journal/*.pending` exists, exit 2 `journal-pending`, naming it. The **only** recovery is `assay state rollback-journal` (below). Never "delete the journal and re-import": root records written before a crash would then be treated as store duplicates, excluded from the audit sample, and never audited (round-3 P9R3-3). Resume validation checks identity, not outcomes, so it is **not** a substitute.
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
5. **The current plan:** `cli.plan_jobs(lane_file, lane)` (P8, C13/C29) gives `plan_by_id`. If it returns `"UNSUPPORTED"`, exit 2. If the lane declares a ledger, resolve the ledger candidate set with P10's resolver (OC16).
   - **Held records.** Load every existing held record under `STORE/.import-audit/*/*/<id>.json` as **existing evidence**, alongside the store root records. The set of held IDs is `held_ids`.
6. **For each source**, in `--from` order:
   1. `PILOT-STATE` present → exit 2 `source-pilot` (OC13).
   2. `SHA256SUMS` passes the grammar, and every listed digest matches the bytes → else exit 2 `source-integrity`, naming the label.
   3. `CAMPAIGN-IDENTITY` validates as in step 2 → else exit 2 `source-identity-inconsistent`. Its `selection_sha256 != null` → exit 2 `source-pilot`.
   4. Record `identity_matches` (its judge equals the store's) and `differing_components` (the sorted `judge_inputs` keys that differ). A mismatching source is **not** refused wholesale; step 7 refuses its records individually, so the receipt shows why (for example, a different `runtime_fingerprint_sha256` means a different host environment).
7. **Per-record checks.** For each `<64hex>.json` record, keep only `(path, sha256, parsed signature fields)` in memory; never hold all payload bytes (P9-8). The first failing check is the refusal reason:

   | # | Check | Refusal reason |
   |---|---|---|
   | 1 | `validate_state_record_shape(payload, stem=…, plan_row=None, cold_witness_kills=<the store identity's judge_inputs.cold_witness_kills>)` passes: size ≤ `MUTATION_STATE_RECORD_LIMIT`, UTF-8 JSON object, `schema_version == 1`, `candidate_id == stem`, `outcome_bucket ∈ MUTATION_BUCKETS`, `_execution_from_state_record` succeeds, and `candidate_id_from_fields(record identity fields) == stem` | the shape reason: `oversized`, `malformed`, `schema-version` or `identity-fields` |
   | 2 | The candidate is in `plan_by_id` | `not-in-plan` |
   | 3 | The record's `path`, `operator`, `start_byte`, `end_byte`, `source_sha256` and `mutated_file_sha256` equal the plan row | `identity-fields` |
   | 4 | `outcome_bucket ∈ {killed, survived, equivalent}` | `non-final-bucket` (OC12) |
   | 5 | `judge_sha256` is a string equal to the store identity's | `judge-mismatch` |
   | 6 | `campaign_deadline_sha256` equals sha256(DEADLINE). A record without the key is accepted only with `--accept-unbound-records`. | `unbound-record` |
   | 7 | The lane declares no ledger, or the candidate is not a ledger candidate (OC16) | `ledger-candidate` |

   Records that pass are *candidates for acceptance*.
8. **Conflict detection.** Consider all candidates for acceptance from all sources, plus every existing store root record whose judge equals the store identity, **plus every existing held record** (step 5; P9R2-3). Group them by candidate ID and compare conflict signatures (OC3):
   - **Same signature:** keep one, and mark the rest `duplicate_consistent`. The kept copy is the held record if the ID is in `held_ids`, else the store root copy if present, else the first source. `killed` records whose modes differ are also listed in `mode_differences`.
   - **Different signature:** a conflict. **Any conflict:** publish only the receipt (`result: "refused"`), exit 2, and leave the store root and `.import-audit/` byte-for-byte unchanged.
   - **A candidate in `held_ids` is never written to the store root by any later import.** Its new copies are duplicates or conflicts. It stays held until the consolidating run executes it and writes its own root record.
9. **The audit sample (OC11, C27).** Per source, over its records that would be written (not duplicates, not `held_ids`), the sample is selected **after** every source's `SHA256SUMS` has verified (step 6):
   - Draw `audit_nonce = state_import._draw_audit_nonce()` (32 bytes, `secrets.token_bytes(32)`).
   - Select the `k = min(n, max(5, ceil(0.02 × n)))` lowest `blake2b((campaign + ":" + audit_nonce.hex() + ":" + candidate_id).encode("utf-8"), digest_size=32).hexdigest()`.
   - The selected records are **held back**: written to `STORE/.import-audit/<receipt-stem>/<LABEL>/<id>.json` instead of the store root, and listed as `audit_held`. `audit_nonce` (64 hex) goes into the receipt.
10. **The journal.** Write `STORE/.import-journal/<receipt-basename>.pending`: a JSON list of `{candidate_id, source, sha256, destination, replaced_stale_sha256}` for every intended write. `replaced_stale_sha256` is the sha256 of the stale record that the write will replace, or `null`.
    - Write the journal atomically (temp + `os.replace`) and `fsync` it **before** any record write.
11. **Write (order is normative; round-3 P9R3-3).**
    - **First write all held records** (every `.import-audit/<stem>/<LABEL>/<id>.json`). **Only then** write any root record.
    - A crash therefore never leaves unaudited root records from a source whose sample was not yet held.
    - For each record, in that order:
      - **re-read** the source file;
      - recompute its sha256, which must equal both the `SHA256SUMS` digest and the step-7 digest (else abort: exit 2 `source-changed`, leaving the journal in place for `rollback-journal`);
      - write with `_write_mutation_state_record(STORE, payload)` (root) or the same canonical atomic writer into the held path.

    A replaced stale store record is listed in `replaced_stale`. `record_sha256` is the sha256 of the bytes written.
12. **Finish.** Publish the receipt, remove the journal, and release the lock. Exit 0 if nothing was refused per record, else exit 1.

**`assay state audit-check`.** This runs after the consolidating `assay run` has **exited**, as a separate process in the same gate arm. It takes the store lock itself (OC14); the gate never locks.
- **When it runs (P9R2-3):** whenever `STORE/.import-audit/` contains at least one held record, regardless of which receipt files exist. The gate arm tests the directory, not the receipts.
- **Inputs:**
  - every held record under `STORE/.import-audit/<receipt-stem>/<LABEL>/<id>.json`;
  - **every** import receipt `.assay/import-*.json` (and the receipt `.assay/<receipt-stem>.json` for each held stem);
  - every discard receipt `.assay/discard-*.json`;
  - `.assay/import-audit-done/`;
  - the consolidating run's `--progress` stream;
  - `--require-campaign-deadline`.
- **Evidence completeness (round-3 P9R3-1).** For every import receipt bound to this deadline whose `audit_held` is non-empty, each `(receipt_stem, label)` in `audit_held` must have one of:
  - its held records present under `.import-audit/<stem>/<label>/`;
  - a pass record under `.assay/import-audit-done/<stem>/<label>/`;
  - a discard receipt naming that `(stem, label)`.

  Otherwise the result is `audit-evidence-missing`. Deleting or moving a held directory by hand can therefore no longer skip the audit. This check runs even when `.import-audit/` is empty: the gate arm also runs audit-check whenever any import receipt with a non-empty `audit_held` exists.
- **For each held record:**
  1. Its receipt must exist; if not, `audit-receipt-missing`.
  2. The receipt's `campaign_deadline_sha256` must equal sha256(DEADLINE); if not, `audit-receipt-deadline-mismatch`.
  3. **Execution evidence (round-3 P9R3-2).** `--progress` must contain a `candidate` event for the ID in **any run segment whose `run` header `started` is later than the receipt's `created_at`**, with `outcome_bucket` equal to both the root record's and the held record's. If there is none, the result is `audit-not-executed`. A root record alone is **not** proof.
     - This is sound because, at import time, a held ID never has a root record at the store judge (step 8 dedup). So any root record for it after the receipt was written by a consolidating run, and that run emitted the event.
     - A consolidation that takes several invocations (deadline cut, C3 unclassified attempts, the C28 OOM rule) therefore still passes. An ID executed in invocation 1 is resumed without an event in invocation 2, but invocation 1's event is found.
     - The gate appends every invocation to the same progress file (`mutation.py:797`, append mode).
  4. The root record `STORE/<id>.json` must exist with the current store judge, and its `outcome_bucket` must equal both the held record's and the matching event's. If not, `audit-disagreed`, naming the source.
  5. For `killed`, differing modes are informational.
- **Closed refusal set:** `store-locked, audit-receipt-missing, audit-receipt-deadline-mismatch, audit-evidence-missing, audit-not-executed, audit-disagreed, progress-unreadable`.
- **Output:** writes `--out` `{schema_version:1, kind:"assay-state-audit-check", store_judge_sha256, audit_nonces:{<receipt-stem>: <64hex>}, sources:[{receipt_stem, label, held, agreed, disagreed:[ids], not_executed:[ids]}], refusals:[{receipt_stem, reason}], result}`.
- **Exit codes:** 0 when every held record agreed and evidence is complete; 2 on any refusal, disagreement, not-executed or missing evidence. The gate fails.
- **A disagreeing source** is dropped with `assay state discard-source --receipt-stem S --label L` (round-3 P9R3-1), never by "re-importing without it". Consolidation is then re-run.
- **Clearing held records:** after an exit-0 audit-check, the gate arm moves `STORE/.import-audit/<receipt-stem>/` to `.assay/import-audit-done/<receipt-stem>/`, so the next consolidation is not audited twice. That directory is the pass record the evidence-completeness rule accepts. The root records now exist.

### Import receipt

Owner `state_import.py`; schema `src/assay/schemas/state-import-receipt.schema.json`, with `"schema_version": {"const": 1}`.

**Valid example:**

```json
{"schema_version": 1, "kind": "assay-state-import", "lane": "self-qualification",
 "commit": "<40hex>", "store": "/abs/.assay/mutation-state-self-qualification", "store_judge_sha256": "<64hex>",
 "campaign": "b105-1a2b3c4d5e6f", "campaign_deadline_sha256": "<64hex>", "accept_unbound_records": false,
 "audit_nonce": "<64hex>",
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

**Receipt rules (P9-3, P9R2-11):**
- `examined` counts every record read from every source.
- `examined == accepted + audit_held + duplicate_consistent + refused + not_written`, where each term is the count.
- `replaced_stale` is a **subset** of `accepted`: every entry's `candidate_id` appears in `accepted`, and it is not added to the sum.
- `counts.duplicate_consistent` counts **records**: Σ over the list's groups of `len(sources) − 1`, where `sources` lists the kept copy first (`"store"`, `"held:<stem>"` or a label). Only source records are counted; the kept store or held copy was not examined.
- Every other count equals the length of its list.
- `audit_nonce` is 64 lowercase hex. It is `null` only when `result == "refused"`, because no sample is drawn when nothing is written.
- **`result`:**
  - `accepted` ⇔ `refused == []` and `conflicts == []`;
  - `partial` ⇔ `refused != []` and `conflicts == []`;
  - `refused` ⇔ `conflicts != []`, `accepted == []`, `audit_held == []` and `replaced_stale == []`. In that case `not_written` lists every record that passed the per-record checks but was not written.
- `reason` is from the closed set: `oversized, malformed, schema-version, identity-fields, not-in-plan, non-final-bucket, judge-mismatch, unbound-record, ledger-candidate`.
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
                                → assay state audit-check (whenever STORE/.import-audit/ holds a record OR any import receipt has a non-empty audit_held) → assay verify → report check
```

- Receipt paths are absolute inside the container.
- `.assay/` is gitignored, so the inbox is covered by the `_resolve_state_dir` visibility refusal.

### Gate arms (owner P9; `tools/self-qualification-gate.sh` + `run-gate.toml`; after P7b; in either order with P10c, per plan §11.6)

**The gate script never takes the store lock (OC14).** Each `assay` invocation below is its own process and takes the lock itself.

Add two arms. They use the same shared clone/build/venv steps and the same lane shape as P7b's `b110-screen` (tester-unified, 3 CPU / 2g / 8g):
- **`b119-worker`:**
  - reads `.assay/b119-shard`, which contains exactly `k/N` (a regex-validated single line);
  - requires `.assay/campaign-deadline-b105-<commit12>.json`, copied from the coordinator (absent → exit 2 before any run; this arm **never** runs `campaign init`);
  - runs `assay run self-qualification --shard k/N --cold-witness --resume --require-judge-provenance --state-dir .assay/mutation-state-self-qualification --campaign-deadline <D> --progress .assay/progress-b119-worker.jsonl --verdict-json .assay/verdict-b119-worker.json`;
  - then writes `SHA256SUMS` over the records and `CAMPAIGN-IDENTITY`, using exactly the `LC_ALL=C` recipe in *SHA256SUMS grammar*;
  - prints `B119_WORKER_EXIT=` and `B119_WORKER_STATE=`;
  - the worker verdict is diagnostic only.
- **`b119-import`:**
  - if `.assay/campaign-deadline-b105-<commit12>.json` is absent, runs P6's `campaign init` exactly as the qualifying arm would (campaign `b105-<commit12>`, 8 h, both lanes). This is the only place besides the qualifying arm that creates it, and it runs in the container, never on the host (OC15);
  - if `.assay/inbox/` has subdirectories, runs one `assay state import` with every subdirectory as `--from <dirname>=<path>` in sorted order, `--receipt .assay/import-<commit12>-<UTC yyyymmddThhmmssZ>.json` and `--require-campaign-deadline <D>`;
  - on exit 0 or 1, moves the imported inbox dirs to `.assay/inbox-imported/<receipt-stem>/`;
  - prints `B119_IMPORT_EXIT=` and `B119_IMPORT_RECEIPT=`.

**The qualifying `self-qualification` arm** (a P9 edit, after P3d, P6 and P7b; in either order with P10c):
- after `assay run` has exited and **before** `assay verify`, run `assay state audit-check … --progress <that run's progress file> --require-campaign-deadline <D> --out .assay/audit-check-<commit12>-<UTC yyyymmddThhmmssZ>.json` if **either** of these holds:
  - `.assay/mutation-state-self-qualification/.import-audit/` contains any `*.json` at any depth (P9R2-3); or
  - any `.assay/import-*.json` receipt has a non-empty `audit_held` (round-3 P9R3-1).

  The trigger never depends on held **receipt files alone**. Deleting a held directory is caught by the evidence-completeness rule;
- a non-zero exit fails the lane (exit 2), with the marker `B119_AUDIT_CHECK_EXIT=`;
- on exit 0, move each audited `<receipt-stem>/` to `.assay/import-audit-done/<receipt-stem>/`.

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
- executes anything missing, rejected or held back (the audit sample). Held records live only under `.import-audit/`, so resume sees no root record for them and executes them;
- writes one ordinary verdict.

`audit-check` then checks that the consolidating run emitted a `candidate` event for every held ID, and that the buckets agree.

**`state import` is an early filter; the consolidating run plus audit-check is the authority.** For example, if the consolidating host's runtime fingerprint differs from the store identity, every record is rejected and re-executed; the P9 report states this.

**Consolidation cost (C7, a B119 acceptance measurement; diagnostic, never an oracle):**
- The P9 report measures the wall time of `state import` over 3,760 synthetic shape-valid records bound to a fixture store identity. The synthetic records need a **matching plan**, or every one is refused `not-in-plan`: generate the records from synthetic plan rows, and monkeypatch `cli.plan_jobs` to return exactly those rows for the measurement only (P9R2-11).
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
8. Consolidate with the qualifying `self-qualification` arm (plan §9.3). It runs `audit-check` whenever held records or held-bearing receipts exist, and any refusal, disagreement or missing evidence fails the gate. Retain every import receipt, its `audit_nonce`, the audit-check result, and every discard or rollback receipt with the gate evidence.
9. **If audit-check reports `audit-disagreed` for a source:**
   - run `assay state discard-source … --receipt-stem <S> --label <L>` inside tester-unified, in a `b119-import`-style arm invocation;
   - retain the discard receipt;
   - re-run the consolidating arm, which re-executes the deleted candidates;
   - never delete or move `.import-audit/` by hand.
10. **If `state import` exits 2 `journal-pending`** (a crash mid-import): run `assay state rollback-journal` inside tester-unified, retain its receipt, then re-run `b119-import` from the still-present inbox. Never delete the journal by hand.

**Never:**
- share a state directory between hosts (NFS, etc.) or between concurrently running invocations; the lock refuses the latter;
- hand-edit a record; import refuses it and resume treats it as corruption (B021);
- use `--accept-unbound-records` unless the operator has answered D7 "yes" (plan §11.2) **and** P6's loader has been changed to accept unbound records under `--campaign-deadline`. Without that change, imported unbound records are re-executed anyway;
- hand a worker the audit nonce, or run `state import` before every source has arrived and verified. The nonce is drawn only after verification, by design.

## Work

1. Refactor to `judge_identity_inputs` / `judge_sha256_from_inputs`. Prove equality with the existing digest (O1) before anything else. Commit.
2. Extract `validate_state_record_shape` (with its `cold_witness_kills` flag). `_load_validated_state_record` uses it, and so does P8's `campaign.py` in place of its local copy. All existing tests, P8's included, stay green. Commit.
3. Thread `lane` and `git_tree` (`HEAD^{tree}`, computed as P6 does) from `runner.py` into `run_mutation` (threading only). Write `CAMPAIGN-IDENTITY` from `run_mutation` (OC2). Add the store lock (OC14), taken **once per process** in `_cmd_run` and in the new commands, never in `run_lane`/`run_mutation`. Commit.
4. Implement `src/assay/state_import.py`: `import` (flow steps 1-12) and `audit-check`, the receipt model and schema, and CLI registration. Add the module to both target lists.
5. Write fast unit tests on synthetic stores in `tests/test_state_import.py`.
6. Write real-run tests in `tests/zz_slow/test_state_import_real_runs.py`: the `_seed_pytest_mutation`-style tiny project, real `runner.run_lane`, `--shard 0/2` / `1/2`, and a fixture deadline file (use P6's `campaign init` in-process).
7. Add the gate arms `b119-worker` and `b119-import`, the qualifying-arm `audit-check` step (triggered by a non-empty `.import-audit/` **or** any receipt with non-empty `audit_held`), the `LC_ALL=C` `SHA256SUMS` recipe, and the run-gate lanes. The `b119-import` arm also accepts a `discard-source` sub-mode and a `rollback-journal` sub-mode (round-3 P9R3-1, P9R3-3). Extend the `tests/test_self_lane.py` pins with `b119-worker`, `b119-import`, `B119_IMPORT_EXIT=`, `B119_AUDIT_CHECK_EXIT=`, `--require-campaign-deadline`, `LC_ALL=C` and `.import-audit`. Also pin that the gate script contains no `flock`.
   - **P7 test adjustment (C27, P9R2-10).** If P7's C5 test in `tests/test_pilot_candidates_file.py` asserts the pilot state directory's **exact** listing, change that one assertion to `sorted(p.name for p in state_dir.glob("*.json"))` plus a separate `PILOT-STATE` existence check. It now tolerates `.lock` and `CAMPAIGN-IDENTITY`. Change nothing else in that file.
8. Measure consolidation cost (C7), and record it in the report as diagnostic.
9. Docs and runbook (Docs sync), the B119 backlog entry, and CHANGES.
   - The B119 entry's acceptance boxes and dependencies must name: the audit sample and `audit-check`, the store lock, the `b119-worker`/`b119-import` arms, the C7 consolidation measurement, and the dependency on B118/P7 (P9R2-11).
   - If a dataclass is added or changed, regenerate `tests/fixtures/dataclass-contract.json` with P1's documented command (C15).
10. Run the focused tests, then the gates. Write the report.

## Oracles

| # | Oracle (observable) | Negative it distinguishes |
|---|---|---|
| O1 | For 5 existing judge-identity test vectors, `judge_sha256_from_inputs(judge_identity_inputs(x)) == judge_sha256(x)`. The expected hex values are **captured from the pre-refactor function at HEAD and committed as literals** before the refactor. All `tests/test_mutation_judge_identity*.py` tests stay green unchanged. | Both digests built from the same new dict builder, so they drift together |
| O2 | After a native-R2 run with a store, `CAMPAIGN-IDENTITY` exists, recomputes to its own `judge_sha256`, and that equals every record's `judge_sha256`. `sorted(state_dir.glob("*.json"))` returns exactly the records, so the existing glob tests stay green. | `campaign-identity.json` breaking `len(glob) == 2` (P9-1) |
| O3 | **Two shard stores** (real runs). The fixture has **≥ 16 killable sites**. **Fixture precondition (round-3):** assert `len(S1) ≥ 8` before importing. Shard assignment is a hash mod N (`mutation.py:1500`), not an even split, so the site count alone does not guarantee this; if the precondition fails, add sites until it holds. Then `k = 5` are held, so ≥ 3 are accepted. Shard `0/2` goes into STORE and shard `1/2` into S1. Add `SHA256SUMS` and import S1 with an injected nonce. The receipt is `accepted` and the counts reconcile. The **consolidating `--resume`**'s `resume` event reports `resumed_total == |STORE shard-0 records| + |S1 accepted|`. Its `candidate` events are **exactly** the held-back IDs, and the tracked process runner sees the baselines plus those candidates only. `audit-check` gives 0. The verdict passes `assay verify` with `[]`, and `candidate_ids` equals the full plan. A **runtime-fingerprint change** (a monkeypatched fingerprint function) before consolidation forces every record to re-execute (`resumed_total == 0`). | Consolidation that re-executes everything (caught by `resumed_total`, which a 2-site fixture could not show); skipping validation when the records are genuine |
| O4 | **Conflicts.** (a) A second source whose copy of an S1 record has `outcome_bucket` flipped, with `SHA256SUMS` regenerated, gives exit 2, `conflicts` naming both sources, and the store root unchanged (directory hash before and after). (b) A **store-vs-source** conflict (the store already holds `killed`; the source says `survived`) gives the same result. (c) Two `killed` records differing only in mode are **not** a conflict; they are listed in `mode_differences`. | "Last writer wins"; checking conflicts only between sources; mode-only conflicts |
| O5 | **Foreign identity:** a record from a run whose lane `env` differs is refused `judge-mismatch`, the rest are accepted, exit 1, `result: partial`, and `differing_components` contains `env_declared`. The check uses **each record's** judge, not the source's `CAMPAIGN-IDENTITY`. A source identity that matches, carrying one record with a different judge, refuses that record. | Trusting the source identity file instead of each record |
| O6 | **Stale tree:** records produced at commit X, then a test-only commit X′. At X′, run shard `0/2` into STORE, which rewrites `CAMPAIGN-IDENTITY` for X′ before executing. Import X's shard-1 records: every one is refused `judge-mismatch`, and a later `--resume` executes them all. | Trusting unchanged mutant bytes after a test change (B088) |
| O7 | **Integrity:** a missing `SHA256SUMS`, an extra unlisted record, a CRLF line, a `*` binary marker, an unsorted file, and one flipped byte in a record each give exit 2 `source-integrity` with nothing written. A source file modified **between validation and write** (a test hook between steps 7 and 11) gives exit 2 `source-changed`, and the journal is left. | Importing corrupted transfers; verify-then-reread |
| O8 | **Identity binding:** a tampered store `CAMPAIGN-IDENTITY` (changed `judge_inputs.r2_transform`) gives `identity-inconsistent`. A store identity at another commit, **or** another `git_tree`, **or** another `assay_version`, **or** a different deadline sha, gives `store-identity-stale`. | Binding only the commit |
| O9 | **Stale store record:** a store record with an older judge plus a matching source record → replaced atomically; its ID is listed in both `replaced_stale` and `accepted`. | Keeping stale records, or treating stale as conflict |
| O10 | **Receipt schema:** the receipt and the audit-check output validate against their schemas; the three invalid examples fail. | Receipt drift |
| O11 | **Destination guards:** `--receipt`/`--out` pointing at an existing file are refused before any write. A git-visible `--state-dir` or `--from` inside the tree is refused. | Clobbering evidence, or a dirty tree |
| O12 | **Non-final buckets:** a `budget_exceeded`, a `hung` and a `crashed` record are each refused `non-final-bucket`, and consolidation re-executes them. | Replaying load-induced worker outcomes |
| O13 | **Pilot:** a source containing `PILOT-STATE`, and a source whose identity has a non-null `selection_sha256`, each give exit 2 `source-pilot`. | Importing pilot state (A-474) |
| O14 | **Deadline binding (D7 = NO):** a record without `campaign_deadline_sha256` is refused `unbound-record`, but accepted with `--accept-unbound-records`. A record with a **different** deadline sha is refused even with the flag. | Accepting screen records by default |
| O15 | **Lock (P9R2-5).** (a) A `STORE/.lock` held by a **separate** helper process (a subprocess holding `flock` until told to exit) gives `state import` exit 2 `store-locked`, and `assay run --state-dir STORE` (via `main([...])`) a pre-run `BAD_LANE_CONFIG`, with **no verdict file written** and no candidate executed. (b) A normal `assay run --state-dir STORE` via `main([...])` with no competing holder completes, proving it never locks itself twice. (c) A leftover `.pending` journal gives `journal-pending`. | A TOCTOU race between a shard run and import; a second in-process `flock` that makes the run refuse itself; a verdict written on refusal |
| O16 | **Audit sample (C27).** S1 has **≥ 8 records**, and the nonce is injected by monkeypatching `state_import._draw_audit_nonce`. A forged sole-copy S1 record (bucket flipped from `survived` to `killed`, judge copied, `SHA256SUMS` regenerated) is placed **inside** the sample computed from that nonce. Consolidation re-executes it locally as `survived`, and `audit-check` exits 2 naming S1 with `audit-disagreed`. A forged record **outside** the sample is not caught; the test asserts this documented residual, so the trust-model statement stays honest. **Nonce dependence:** the test first computes, with the published formula, the held sets for two fixed injected nonces over the same S1. It picks two nonces whose computed sets differ; with fixed nonces that choice is deterministic. It then asserts that each import holds exactly its computed set: the `k` lowest `blake2b(campaign:nonce:id)` ranks. The receipt records the nonce. | "Audit" that re-validates only identities; a sample a worker could predict (seeded by the campaign name alone) |
| O18 | **Held records are never root records (P9R2-3).** Import S1 (some IDs held). Then import a second source S2 that carries the same held IDs with the same bucket. They are `duplicate_consistent` with the kept copy `held:<stem>`, and **no** root record is written for them. The same IDs with a different bucket give a conflict, exit 2. Deleting the import receipt file does not skip the audit: `audit-check` still runs, because the gate keys on `.import-audit/`, and exits 2 `audit-receipt-missing`. A consolidating run that **resumed** a held ID from a planted root record, with no `candidate` event for it in any segment after the receipt's `created_at`, gives `audit-not-executed`. | Writing a held ID to root on a later import; keying the audit on receipt presence; treating "a root record exists" as "executed" |
| O22 | **Two-invocation consolidation (round-3 P9R3-2).** Consolidation invocation 1 executes the held IDs (records plus `candidate` events), then stops through an injected deadline expiry: an incomplete verdict. Invocation 2 (`--resume`, appending to the same progress file) resumes them with **no** event. `audit-check` gives 0: it finds invocation 1's events, in a segment started after the receipt's `created_at`, with matching buckets. The planted-root-record negative from O18 still gives `audit-not-executed`. | Requiring the event in the **latest** segment, which makes every multi-invocation consolidation fail forever |
| O23 | **Discard and evidence completeness (round-3 P9R3-1).** (a) After an `audit-disagreed` source `L`, `discard-source --receipt-stem S --label L`:<br>• deletes exactly the root records listed for `L` whose bytes match their `record_sha256`;<br>• keeps a byte-changed one, listed as `kept_changed`;<br>• removes `.import-audit/S/L/`;<br>• writes the discard receipt.<br>The next consolidation re-executes those candidates, and `audit-check` gives 0.<br>(b) Deleting `.import-audit/S/L/` by hand, with no discard receipt and no `import-audit-done` entry, makes `audit-check` exit 2 `audit-evidence-missing`, even though `.import-audit/` is now empty, because the receipt's `audit_held` names `(S, L)`. | Treating "re-import without it" as removal; triggering audit-check only on a non-empty `.import-audit/`; a discard that deletes byte-changed records |
| O24 | **Crash mid-import (round-3 P9R3-3).** Inject a crash (a monkeypatched writer raises) after all held records and some root records are written. The next `state import` exits 2 `journal-pending`. `rollback-journal` deletes exactly the journaled destinations whose bytes match, including the held records, and removes the journal. Re-importing the same inbox then samples and holds afresh, and no record from the crashed import stays at root unaudited. Also assert the write order: when the crash is injected at the first root write, every held record already exists. | Writing root records before held records; "delete the journal and re-import"; rollback that deletes byte-changed files |
| O19 | **Ledger candidates (OC16, P9R2-6).** With a lane that declares a ledger (a fixture ledger once P10b lands; before that, a monkeypatched resolver), a source record for a ledger candidate is refused `ledger-candidate`, is never sampled, and `audit-check` does not report it `audit-not-executed`. | Sampling a ledger candidate that consolidation never executes |
| O20 | **Transfer ignores `.lock` (P9R2-4).** A worker store produced by a real CLI `assay run` (so `.lock` exists), with `SHA256SUMS` generated by the gate's `LC_ALL=C` recipe, imports cleanly. The same store with an extra `notes.txt` gives `source-integrity`. A `SHA256SUMS` sorted under a non-C locale (with `CAMPAIGN-IDENTITY` out of bytewise order) gives `source-integrity`. | Treating `.lock` as an unlisted file; locale-dependent sort |
| O21 | **Identity keys (P9R2-1).** The `CAMPAIGN-IDENTITY.judge_inputs` key set equals exactly the list in *Owned interfaces*, including `r2_hook_fingerprint_sha256`, `coverage_hook_fingerprint_sha256` and `coverage_runtime_fingerprint_sha256`, with P3b's names (`r2_transform`, not `transform`). Changing any one of the 7 facts changes `judge_sha256`. | A 4-fact identity that lets a hook-set or coverage-environment change resume stale records |
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
  - why the audit sample is nonce-seeded and drawn only after verification, and that the detection formula covers only non-adaptive faults;
  - why held records never become root records, and why `audit-check` keys on `.import-audit/` and on execution events, not on receipts or root records;
  - why a conflict is refusal-worthy nondeterminism;
  - why non-final buckets are refused;
  - why import is a filter and consolidation plus audit-check is the authority;
  - why "accepted" is store membership plus receipt plus audit-check;
  - the lock, taken once per process, never by the gate;
  - shards are scheduling (A-464);
  - the D7 default (NO) and what a "yes" would change, including the P6 loader change it also requires.
- **CONSUMERS (HOW):** the worker runbook (pasteable), the receipt and audit-check fields, the gate arms, and the exit codes.
- **CHANGES.md:** `### Added`.
- **Backlog:** update B119's status.

## Scope / forbid

**Touch:**
- `src/assay/mutation.py`: the identity refactor, `validate_state_record_shape`, the `lane`/`git_tree` keywords on `run_mutation`, and the identity-file write only;
- `src/assay/runner.py`: **threading only**: pass `lane` and `git_tree` (`HEAD^{tree}`) into `run_mutation`; no behavior change (P9R2-9);
- `src/assay/cli.py`: registration, plumbing, and the once-per-process store lock in `_cmd_run` after `_resolve_state_dir`;
- `src/assay/campaign.py` (P8): replace its local record-shape checks with `validate_state_record_shape`; no behavior change;
- `tests/test_pilot_candidates_file.py` (P7): the one directory-listing assertion only, changed to `glob("*.json")` (C27);
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
- any host-side `campaign init` or import path;
- `flock` anywhere in `tools/self-qualification-gate.sh`, or a second `flock` inside `run_lane`/`run_mutation`;
- P6's unbound-record loader rule (the D7 "yes" change is not P9's).

## Gate

1. Run the focused tests, serially: `nice -n 19 ionice -c3 python -m pytest tests/test_state_import.py tests/zz_slow/test_state_import_real_runs.py tests/test_mutation_judge_identity.py tests/test_mutation_progress_budget_plan.py tests/test_state_dir_resume.py tests/test_mutation_state_crash_tails.py tests/test_mutation_resume_sharding.py tests/test_self_lane.py tests/test_campaign.py tests/test_pilot_candidates_file.py -q -p no:cacheprovider`. The last two prove the shared validator swap and the P7 assertion change. Find any file P1 moved into `tests/zz_slow/` by name. If P1 moved `test_mutation_judge_identity_properties.py` into `tests/zz_slow/`, add it at its new path.
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
- P3b's merged `judge_sha256` signature differs from the key list in *Owned interfaces* / A-470 (P9R2-1).
- P8's `campaign.py` validator cannot be replaced by `validate_state_record_shape` without changing P8's test outcomes.
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
