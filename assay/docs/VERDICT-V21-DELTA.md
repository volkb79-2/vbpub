# Verdict v21: the single schema cut (delta list)

P-assay-09 (B567 part 1), 2026-11-24. Binding: D-A3, D-A8, D-A12 (`.controller-state/PROGRAM-2026-11-24/PLAN-assay.md` §2). This list is **closed**: P-assay-17 implements exactly this, and no other package touches the verdict schema, `ReasonCode` or the closed vocabularies. Code references are to main `ec549bbc6`, or to the WP2 branch `assay-20261118_212147-wp2-locus-record` at `4f722514a` (marked "WP2"). Labels: M measured, C computed, E estimate.

## 1. Every v21 change

| id | Change | Source | Reverses tested behaviour? |
|---|---|---|---|
| P4-provenance | Every native outcome in every bucket gets a required `provenance` object (§2), and `judgment.r2.provenance` gets counts. Ingested outcomes carry none. | B563, D-A8 | no (additive) |
| P4-modes | `execution.mode` gains **only** `witness-isolated` (§2.2). Today's set is closed at `full`, `witness-prefix`, `witness-cold`, `ledger` (`verdict.py:2202`; schema `mutation_execution`). `judgment.r2.node_baselines` is added. | B561, B564, review L1 | no. It revises design note §5.2 on paper |
| D-A3 rule | New `ReasonCode.KILLS_UNVERIFIED` (FAIL, mutation-only), plus a `judge_mutation` branch (§3) | B567, D-A3 | **YES**. Today an imported kill counts toward PASS (design note §7 D2 option (a), "as today"). No test that pins this was found; P-assay-17 adds the opposite pin |
| B532 | (1) The per-source import counts move into `judgment.r2.provenance.by_source_judge` (§2.3), so no separate `carry_over` block is added. (2) `judgment.r2.unselected_total` goes on the wire. Today it is internal `r2_telemetry` (`verdict.py:2713-2719`, read at `mutation.py:8449`). (3) New `ReasonCode.SELECTION_PARTIAL` (BUDGET_EXCEEDED, so exit 4 stays). | B532 | **YES**. A selection remainder's reason changes from `LANE_TIMEOUT` to `SELECTION_PARTIAL`. The `PARTIAL:` detail and the non-PASS status stay (`test_flex_r2_campaigns.py:588-589`) |
| B545 | No schema field. Producer: the progress `outcome_bucket` is taken from the same record the verdict projects, with one classifier, `_bucket_for_run` (`mutation.py:4365`). A supervisor-killed candidate is never `killed`: `_has_clean_failure_report` requires FAIL, rc 1 and an exited leader (`mutation.py:4302-4318`). A test that an in-suite timeout fails (rc 1, clean exit) is `killed` on both sides. The closeout keeps refusing a mismatch (`analysis/.../campaign.py:695-698`). The first step is to reproduce RG-278 candidate 16300. | B545 | possibly. Whichever side mislabelled 16300 flips; unknown until reproduced (E) |
| B556-code | New `ReasonCode.SNAPSHOT_IO_FAILED` (ERROR) for a non-git `OSError`. The site is `runner.py:7577-7599`, which today raises `GIT_FAILED`. `GIT_FAILED` becomes git-only. | B556, D-A13 | **YES**. The B053/DA-R8 refusal tests expect `GIT_FAILED` |
| B556-cleanup | The cleanup-failure shape is recommendation (b) in §5.2 | B556 | only the reason code under (b). Option (a) would reverse A-193/A-194 |
| B559 | R2 precedence: survivors outrank started timeouts and hangs (§5.1) | B559 | **YES**. See `test_mutation_judge.py:146`, `test_runner_cargo_mutants_r2.py:178` and the comment at `hostobs/assay.toml:253` |
| B554 (+B234) | **Addition to D-A12's list, to confirm.** The descendant item in the schema (`verdict.schema.json:1739ff`, `additionalProperties: false`, only `pid/ppid/comm`) gains the optional `cmdline` that the writer already emits (`process_tree.py:170`) and verify already accepts (`verify.py:2546`). B234's optional `state` letter is added with it: it requires a complete `pid/ppid/comm` identity and is enforced in the schema `allOf` and in verify. Both are BLOCKED-ON-V20 and missed v20. Under D-A12 they cannot land anywhere else. | B554, B234, B532 note | no. It fixes a live v20 defect: a real leaked-descendant verdict is schema-invalid |
| verify rules | §4 | B567, F15, L1 | no (new refusals for new fields) |
| version | `VERDICT_SCHEMA_VERSION` 20→21 (`verdict.py:443`), with the schema `$id`/`title`/`const`, the pin and `reuse.py` (§6) | M3 | hard cut: verify refuses v20 (A-170) |

## 2. Provenance projection and modes

### 2.1 The per-candidate object

The §3.3.1 object (WP2 design note) is projected **unchanged**: `{kind, from_candidate_id, from_judge_sha256, match, replay_node_id?, reason?}`. Its defining code is WP2 `mutation.py:1912-1913` (closed sets) and `:2098` (`record_provenance`).

| `kind` | Where it comes from | Fields | Legal buckets |
|---|---|---|---|
| `fresh` | Executed by this run. The state record carries no provenance. | exactly `{kind}` | any |
| `resumed` | **Assigned at projection.** This run took a record from its own store, and that record has no stored provenance. | `from_candidate_id` = own id, `from_judge_sha256` = the record's judge, `match: exact-id` | any |
| `imported` | `--carry-over` (stored). A schema-1 `carry_over` block is derived by `record_provenance`. | all except `replay_node_id`. `reason` is required | `killed`, `survived` (`CARRY_OVER_IMPORTABLE_BUCKETS`, `mutation.py:1805`) |
| `carried` | `--carry-forward` survivor (stored) | all except `replay_node_id` | `survived` only |
| `replayed` | A fresh current-judge isolated run whose test was chosen from a prior record (stored). This covers a carried-kill replay (`match` exact-id/locus/window), a hint kill (`match: hint`) and an R-a survivor probe kill (`match: exact-id`). | `replay_node_id` is required | `killed` only |

**Stored kinds win, and resume never launders.** A resumed record that carries stored provenance projects its stored kind. An imported record that a later run resumes therefore still projects `imported`, so an imported kill still trips D-A3. `test_flex_r2_campaigns.py:426-429` resumes an imported survivor this way, and P-assay-17 adds the kill variant.

### 2.2 One vocabulary: `kind` is origin, `mode` is execution

The design note §5.2 proposed modes `carried`, `carried-unverified` and `replayed`. These say the same thing as `kind`, so v21 does **not** add them. The mapping is:

- `carried` becomes `kind = carried`.
- `carried-unverified` becomes `kind = imported`. A "carried-unverified kill" is defined as `killed ∧ kind = imported`.
- `replayed` becomes `kind = replayed`.
- `witness-isolated` is the one new `execution.mode`, because it has fields of its own.
- §5.2's `prior{…}` object is dropped: its fields are the provenance fields, and `node_baseline_sha256` moves into the execution.

A non-fresh outcome keeps the execution that its executing judge (`from_judge_sha256`) recorded. Two invariants link the axes. First, `execution.mode = witness-isolated` holds exactly when `kind = replayed`. Second, `witness.node_id = replay_node_id`.

The `witness-isolated` execution is `{mode, witness, node_baseline_sha256}`:
- `witness` is the failed-call receipt on the mutant (M).
- `node_baseline_sha256` names one entry of `judgment.r2.node_baselines: [{node_id, sha256, outcome: "passed"}]`, where `sha256` is the digest of the canonical receipt of the pristine-tree (P) run.

### 2.3 `judgment.r2.provenance` (native only)

`{fresh, resumed, imported, carried, replayed, unverified_kills, by_source_judge: {<judge_sha256>: n}, replay_fallbacks: {mutant-passed, baseline-failed, node-missing, refused, skipped, timed-out}}`

- The five kind counts sum to `mutation.total`.
- `unverified_kills` = |killed ∧ imported|.
- `by_source_judge` counts `imported` + `carried` + `replayed` by `from_judge_sha256`.
- `replay_fallbacks` is telemetry and is not re-derivable (like `profile`). The per-candidate cause is in the progress stream.

## 3. The D-A3 rule

**Rule.** If `unverified_kills > 0` and nothing ranked above it applies, R2 is **`FAIL` / `KILLS_UNVERIFIED`**. The detail reads: "N of K kills were imported by --carry-over and not re-proved by this judge". The count is always in `judgment.r2.provenance.unverified_kills`, including when another branch wins. In `judge_mutation` (`mutation.py:8265`; precedence at `:8360-8391`) the branch sits **immediately after `survived`**, whichever B559 variant is chosen. No lane key, CLI flag or verdict field carries a claim type.

**Why FAIL and not "PARTIAL".**
- No PARTIAL `Outcome` exists (`errors.py:41-49`). "PARTIAL" is only a detail prefix (`mutation.py:8454`).
- A new Outcome would widen the frozen `EXIT_CODES` (`errors.py:57-67`, A-021) and `ROLLUP_PRECEDENCE` (`verdict.py:805`) for every consumer.

**Why not another existing status.**
- R2 `INCONCLUSIVE` means "no evidence about the tests" (`NO_MUTANTS`, `ALL_MUTANTS_EQUIVALENT`, `MUTATION_UNSUPPORTED`).
- `NO_MEASUREMENT` means the measurement's subject is unknown (`PROVENANCE_UNVERIFIED`, `errors.py:163-172`).
- `BUDGET_EXCEEDED` tells run-gate to resume (`run_gate.py:16524`), but a resume does not help: an imported record is resumed, not re-judged.
- FAIL states exactly what holds: "every candidate killed" is not established.
- The remedy is `--rejudge-outcome killed` (`mutation.py:3962`) or a carry-forward replay (WP4).
- run-gate maps both FAIL and INCONCLUSIVE to FAIL (`run_gate.py:353-360`), so the choice changes only the reason code.

## 4. Verify rules (all new for v21)

1. **Re-derive the counts.** The kind counts, `unverified_kills` and `by_source_judge` are recomputed from the per-candidate objects, and the sum must equal `total`. A disagreement is refused.
2. **Kind/bucket matrix (§2.1).** **F15**: a `killed` outcome with `kind = carried` is refused. So are `replayed` outside `killed`, `imported` outside `killed`/`survived`, and a violation of the `witness-isolated` ⇔ `replayed` biconditional.
3. **Node baseline.** A `witness-isolated` outcome is refused when its `node_baseline_sha256` does not resolve to a `node_baselines` entry with the same `node_id` and `outcome: "passed"`. It is also refused when its witness is not a failed-call receipt.
4. **R2 re-derivation.** It keeps reusing `judge_mutation` (`verify.py:3636`), including the new branches. `KILLS_UNVERIFIED` and `SELECTION_PARTIAL` join the mutation-only reason sets (`verdict.py:794`, `verify.py:180`). `unselected_total` must not exceed the count of `budget_exceeded` entries whose leader status is `not-started` (`verify.py:2499`).
5. **WP0b: a failed isolated run is never a kill.** WP0b (M, `MEASUREMENTS.md` "R2 isolated replay") measured r = 3.81 s median, with 19/20 old kills confirmed. The isolated run is **neither a kill nor a survivor, nor `budget_exceeded`/`hung`** when any of these happens:
   - a project collection guard refuses it (cmru's conftest: 0/20 without `--cli-case-partial`, M);
   - the node is skipped (1 of 20, M) or times out;
   - the node passes on M, fails on P, or is missing.

   What the verdict records then:
   - For a carried kill or a hint, the **full judge** runs, and its outcome is projected as `kind: fresh` with the full judge's own mode (`full`, `witness-prefix` or `witness-cold`). `replay_fallbacks.<cause>` is incremented.
   - For an R-a survivor probe, a non-kill leaves the survivor `kind: carried` (B564). No full judge runs.

## 5. Two operator recommendations (main controller to confirm)

### 5.1 B559: timeouts vs survivors

**Recommended default.** The new order is:
1. limit sentinel, then `NO_MUTANTS`
2. `crashed`
3. **any not-started `budget_exceeded`**: `LANE_TIMEOUT`, or `SELECTION_PARTIAL` when every not-started entry is unselected
4. **`survived`** (FAIL)
5. `KILLS_UNVERIFIED`
6. started `budget_exceeded`
7. `hung`
8. all-equivalent
9. PASS

Not-started work keeps outranking survivors because run-gate resumes on `BUDGET_EXCEEDED` (`run_gate.py:16524`), and a deadline-truncated run must stay resumable. The not-started fact is already on the wire (`ProcessTreeEvidence.not_started()`, `mutation.py:4165-4181`). There is one R2 claim, whose detail names the timeout and hang counts; the bucket lists already name those candidates.

A native producer re-judges started timeouts alone, once, at the end, and only one that still times out stays `budget_exceeded`. Ingested lanes (cargo-mutants) cannot retry, so they get the precedence alone.

**Reverses:** `test_budget_exceeded_outranks_survived_when_both_are_present` (`test_mutation_judge.py:146`), `test_a_timeout_outranks_survivors` (`test_runner_cargo_mutants_r2.py:178`) and the comment at `hostobs/assay.toml:253`. In the hostobs run (21 survivors, 6 timeouts, M), the result goes from `BUDGET_EXCEEDED/LANE_TIMEOUT` (exit 4; run-gate BUDGET_EXCEEDED) to `FAIL/MUTANTS_SURVIVED` (exit 1; run-gate FAIL). If the operator rejects this, today's order stays and D-A3 still sits right after `survived`.

### 5.2 B556 cleanup: a / b / c

**Recommended default: (b).** Keep the A-193/A-194 shape (`runner.py:7108-7120`): the highest higher-rigor claim is replaced, and lower claims stand. Its pair becomes `ERROR/SNAPSHOT_IO_FAILED`. The detail is the D-A13 text (`f"{phase}: {exc}"` plus the fd path), followed by the replaced claim's own judged status and reason, and the scratch path left behind. All of this is text only, with no payload object.

**Reverses:** only the reason code (`GIT_FAILED` becomes `SNAPSHOT_IO_FAILED`).

**Rejected options.**
- (a) would reverse A-193/A-194 and let a PASS coexist with leaked, foreign-owned scratch.
- (c) is an up-front refusal that needs no vocabulary beyond `SNAPSHOT_IO_FAILED`, so it can land later and is not part of v21.
- A separate `SNAPSHOT_CLEANUP_FOREIGN_OWNER` code is rejected: the owner uid is a detail, not a vocabulary member.

## 6. v20 → v21 migration and the pin

- **Pin.** `_VERDICT_SCHEMA_SHA256 = {21: <new digest>}` replaces the entry for 20 (`tests/core/test_verdict_conformance.py:1447-1449`); only the current version ships. The `$id`/`title`/`const` test (`:1457-1466`) follows the constant.
- **Bump note.** A "Bumped 20 -> 21" paragraph goes beside `verdict.py:443`, and a CONSUMERS migration heading is added (the B138 docs oracle requires one per cut).
- **reuse.** `reuse.py:74` refuses any version except the current one. Version 20 joins `LEGACY_COLD_START_VERSIONS` (`reuse.py:14`), so the first v21 run after the upgrade gets no witness-prefix reuse from a v20 verdict (C: a one-run cost). State stores are unaffected, because carry-forward reads the store, not verdicts.
- **What consumers must change.**
  - **run-gate:** three new reason codes. A selection remainder no longer reads `LANE_TIMEOUT`. Under B559, survivors plus timeouts become FAIL.
  - **`assay/analysis`:** `campaign.py` reads `provenance.kind` per outcome, and `rigor_export.py:250` follows the constant.
  - **hostobs/cmru:** comments and any reason-code readers (audit).
  - **fixtures:** `tests/fixtures/verdicts/*.json` are regenerated.
  - **docs:** CONSUMERS (contract), DESIGN-GUIDE (link), CHANGES (1-3 lines); the P4 note is marked historical.

## 7. NOT in v21

- `footprint-proven` and Phase 2 (WP7), and D6 `classifier_version`: parked (D-A5). WP0b measured r = 3.81 s (M), far under the ~20 s re-open line.
- A claim-type axis (old D2), replaced by D-A3.
- §5.2's `prior{}` object and the `fresh_full`/`fresh_prefix`/… count names, replaced by §2.
- WP4's per-lane isolated-run arguments in the judge identity (WP0b requirement 1). This is a lane-config and state matter (P-assay-13), not a verdict field.
- `locus_unavailable` (D-A9) appears in progress and summary only, never in the verdict.
- B556's message formatter (P-assay-01, no vocabulary) and option (c).
- B560, B565 `capabilities --json` (it reports the schema version and adds no verdict field), B568.
- B055 (Go column-granular claims).
