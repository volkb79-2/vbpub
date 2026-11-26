# Verdict v21: the single schema cut (delta list)

P-assay-09 (B567 part 1), 2026-11-24. Binding: D-A3, D-A8, D-A12 (`.controller-state/PROGRAM-2026-11-24/PLAN-assay.md` §2). **Amended once, by A2 (§8, 2026-11-26); where §8 and §1-§7 disagree, §8 wins.** This list is **closed**: P-assay-17 implements exactly this, and no other package touches the verdict schema, `ReasonCode` or the closed vocabularies. Code references are to main `ec549bbc6`, or to the WP2 branch `assay-20261118_212147-wp2-locus-record` at `4f722514a` (marked "WP2"). Labels: M measured, C computed, E estimate.

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
| B559 | R2 precedence: survivors outrank started timeouts and hangs (§5.1) | B559 | **YES**. See `test_mutation_judge.py:146` and `test_runner_cargo_mutants_r2.py:178`. The `hostobs/assay.toml:253` comment is left to the hostobs owner (§5.1) |
| B554 (+B234) | **Addition to D-A12's list, to confirm.** The descendant item in the schema (`verdict.schema.json:1739ff`, `additionalProperties: false`, only `pid/ppid/comm`) gains the optional `cmdline` that the writer already emits (`process_tree.py:170`) and verify already accepts (`verify.py:2546`). B234's optional `state` letter is added with it: it requires a complete `pid/ppid/comm` identity and is enforced in the schema `allOf` and in verify. Both are BLOCKED-ON-V20 and missed v20. Under D-A12 they cannot land anywhere else. | B554, B234, B532 note | no. It fixes a live v20 defect: a real leaked-descendant verdict is schema-invalid |
| verify rules | §4 | B567, F15, L1 | no (new refusals for new fields) |
| version | `VERDICT_SCHEMA_VERSION` 20→21 (`verdict.py:443`), with the schema `$id`/`title`/`const`, the pin and `reuse.py` (§6) | M3 | hard cut: verify refuses v20 (A-170) |

## 2. Provenance projection and modes

### 2.1 The per-candidate object

The §3.3.1 object (WP2 design note) is projected **unchanged**: `{kind, from_candidate_id, from_judge_sha256, match, replay_node_id?, reason?}`. Its defining code is WP2 `mutation.py:1912-1913` (closed sets) and `:2098` (`record_provenance`).

| `kind` | Where it comes from | Fields | Legal buckets |
|---|---|---|---|
| `fresh` | Executed by this run, with no stored provenance in the state record. Not-started placeholders are also `fresh`: candidates left out by a selection (`_append_unselected`, `mutation.py:4166`) and the campaign-deadline remainder. | exactly `{kind}` | any |
| `resumed` | **Assigned at projection.** The record has no stored provenance and was not executed by this run. It came from the run's own store, from `--merge-state` or from `--merge-shard-store`. | `from_candidate_id` = own id, `from_judge_sha256` = the record's judge, `match: exact-id` | any |
| `imported` | `--carry-over` (stored). A schema-1 `carry_over` block is derived by `record_provenance`. | all except `replay_node_id`. `reason` is required | `killed`, `survived` (`CARRY_OVER_IMPORTABLE_BUCKETS`, `mutation.py:1805`) |
| `carried` | `--carry-forward` survivor (stored) | all except `replay_node_id` | `survived` only |
| `replayed` | A fresh current-judge isolated run whose test was chosen from a prior record (stored). This covers a carried-kill replay (`match` exact-id/locus/window), a hint kill (`match: hint`) and an R-a survivor probe kill (`match: exact-id`). | `replay_node_id` is required | `killed` only |

**Stored kinds win, and resume never launders.** A resumed record that carries stored provenance projects its stored kind. An imported record that a later run resumes therefore still projects `imported`, so an imported kill still trips D-A3. `test_flex_r2_campaigns.py:426-429` resumes an imported survivor this way, and P-assay-17 adds the kill variant. With these two rows, every outcome has exactly one kind, so the counts in §2.3 sum to `total`.

**Narrowing of §3.3.1.** §3.3.1 says `resumed` "is assigned when a run takes a record from its store". This list narrows that to "only a record with no stored provenance". A later package adds that one-line amendment to §3.3.1; this document does not edit the design note, which lives on the WP2 branch.

### 2.2 One vocabulary: `kind` is origin, `mode` is execution

The design note §5.2 proposed modes `carried`, `carried-unverified` and `replayed`. These say the same thing as `kind`, so v21 does **not** add them. The mapping is:

- `carried` becomes `kind = carried`.
- `carried-unverified` becomes `kind = imported`. A "carried-unverified kill" is defined as `killed ∧ kind = imported`.
- `replayed` becomes `kind = replayed`.
- `witness-isolated` is the one new `execution.mode`, because it has fields of its own.
- §5.2's `prior{…}` object is dropped: its fields are the provenance fields, and `node_baseline_sha256` moves into the execution.

This mapping supersedes the wording "modes `carried`/`carried-unverified`/`witness-isolated`/`replayed`" in backlog B567 and in PLAN P-assay-09/17. P-assay-17's acceptance reads "carried-unverified kill = `killed ∧ kind = imported`". B567 gets an amendment line later; this document does not edit the backlog.

A non-fresh outcome keeps the execution that its executing judge (`from_judge_sha256`) recorded. Two invariants link the axes. First, `execution.mode = witness-isolated` holds exactly when `kind = replayed`. Second, `witness.node_id = replay_node_id`.

The `witness-isolated` execution is `{mode, witness, node_baseline_sha256}`:
- `witness` is the failed-call receipt on the mutant (M).
- `node_baseline_sha256` names one entry of `judgment.r2.node_baselines: [{node_id, sha256, outcome: "passed"}]`, where `sha256` is the digest of the canonical receipt of the pristine-tree (P) run.

**Node-baseline persistence.** The node-baseline cache is per run, so a reference to it alone would not survive a resume or a `--merge-shard-store` run: the later run would have no matching entry, and verify would refuse a legitimate verdict. The node-baseline receipt `{node_id, sha256, outcome}` is therefore persisted **with the `replayed` state record**. Every run that projects that record also projects its receipt into `judgment.r2.node_baselines`, deduplicated by `sha256`. P-assay-13 writes the receipt.

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
- `BUDGET_EXCEEDED` tells run-gate that the lane may resume its assay state (`run_gate.py:5926-5929`), but a resume does not help: an imported record is resumed, not re-judged.
- FAIL states exactly what holds: "every candidate killed" is not established.
- The remedy is `--rejudge-outcome killed` (`mutation.py:3962`) or a carry-forward replay (WP4).
- run-gate maps both FAIL and INCONCLUSIVE to FAIL (`run_gate.py:353-360`), so the choice changes only the reason code.

## 4. Verify rules (all new for v21)

1. **Re-derive the counts.** The kind counts, `unverified_kills` and `by_source_judge` are recomputed from the per-candidate objects, and the sum must equal `total`. A disagreement is refused.
2. **Kind/bucket matrix (§2.1).** **F15**: a `killed` outcome with `kind = carried` is refused. So are `replayed` outside `killed`, `imported` outside `killed`/`survived`, and a violation of the `witness-isolated` ⇔ `replayed` biconditional.
3. **Node baseline.** A `witness-isolated` outcome is refused when its `node_baseline_sha256` does not resolve to a `node_baselines` entry with the same `node_id` and `outcome: "passed"`. It is also refused when its witness is not a failed-call receipt. The entry comes from the receipt stored with the record (§2.2), so a resumed or shard-merged `replayed` kill still resolves.
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

Not-started work keeps outranking survivors because run-gate treats a `BUDGET_EXCEEDED` lane as one that may resume its assay state (`run_gate.py:5926-5929`), and a deadline-truncated run must stay resumable. The not-started fact is already on the wire (`ProcessTreeEvidence.not_started()`, `mutation.py:4165-4181`). There is one R2 claim, whose detail names the timeout and hang counts; the bucket lists already name those candidates.

A native producer re-judges started timeouts alone, once, at the end, and only one that still times out stays `budget_exceeded`. Ingested lanes (cargo-mutants) cannot retry, so they get the precedence alone.

**Reverses:** `test_budget_exceeded_outranks_survived_when_both_are_present` (`test_mutation_judge.py:146`) and `test_a_timeout_outranks_survivors` (`test_runner_cargo_mutants_r2.py:178`). The comment at `hostobs/assay.toml:253` becomes stale, but it is outside the assay program. Its change is coordination with the hostobs owner after the operator confirms B559, not a P-assay-17 edit. In the hostobs run (21 survivors, 6 timeouts, M), the result goes from `BUDGET_EXCEEDED/LANE_TIMEOUT` (exit 4; run-gate BUDGET_EXCEEDED) to `FAIL/MUTANTS_SURVIVED` (exit 1; run-gate FAIL). If the operator rejects this, today's order stays and D-A3 still sits right after `survived`.

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
  - **hostobs:** the `assay.toml:253` comment, changed by the hostobs owner after the operator confirms B559 (coordination, not a P-assay-17 edit).
  - **cmru:** any reason-code readers (audit).
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
- B550 (lcov producer vocabulary): `judgment.r1.coverage_producer` is a bare string in the schema and its closure lives in `vocabulary.COVERAGE_PRODUCERS_BY_FORMAT` (schema text of `judgment_r1.coverage_producer`; backlog B245 "B550 is independent (a vocabulary entry, no schema change)"). It can land in any minor.

## 8. Amendment A2 (2026-11-26)

The one amendment (plan `.controller-state/assay/PLAN-spec-followups.md` §1 rule 1, package A2). It covers plan items 6, 7, 8 (fields), 9, 10, 12, 14-16, the P4 evidence decisions and the A1 controller-read notes. Binding inputs that are not reopened: rule 2 (wall time may stop a run, never decide a candidate or a rung), item 7 (a load-independent timeout is `killed` with `timeout_kind`), P4 ships its wall-derived CPU bound (`-v1`) and A4 switches to `cpu-since-completed-event-v2` with v21, the resource source defaults to `none` and no bound reads it, the cgprofile `profile` block stays unchanged, R4 stays reserved, no `--rejudge-carried`, no legacy or compatibility paths. Citations are to main `23514fa06` unless marked **[P4]** (branch `assay-20261125_230232-p4-stall` @ `325bdf3ca`, all listed in §8.9). **[U]** = not verified.

### 8.1 The three explicit statements

- **(a) `LANE_INVENTORY_SCHEMA_VERSION`: no bump** (stays 1, `cli.py:5095`). 13.0.0 removes two keys from `assay lanes --json`: the top-level `capabilities` flag list (`cli.py:5109-5113`, plan item 10) and the per-lane `enforcement` (`cli.py:5246`, item 14, §8.6). Nothing reads either key. run-gate reads only `inventory_schema` and lane entries (`run_gate.py:9840`), and it reads capabilities from `assay capabilities --json` (RG-297, merged). A repo-wide grep finds no reader of the flag names outside `cli.py`, and no non-assay reader of `enforcement`. Every key that stays keeps its meaning. A bump would make every run-gate inventory read refuse (`run_gate.py:9841-9844`) until run-gate's next release, and it would protect no reader. The P-17 SPEC update turns CON-040's rule into: "a changed meaning, or a dropped key that a consumer reads, bumps `inventory_schema`".
- **(b) The wall-stop non-judgement result is outcome `BUDGET_EXCEEDED` with reason `LANE_TIMEOUT`, exit 4.** There is no `WALL_STOP` code and no `INCONCLUSIVE` mapping. In v21, `LANE_TIMEOUT` means exactly one thing: wall time stopped the run or a candidate before a judgement. That covers the lane budget (TIM-110), the campaign deadline (TIM-120), the git/snapshot deadline (TIM-160), a binding per-candidate budget, and an ingested tool's own timeout. Operator cancel and not-selected get their own reasons (§8.2). Why not `INCONCLUSIVE`:
  - run-gate maps INCONCLUSIVE to FAIL (`run_gate.py:367`). Only `BUDGET_EXCEEDED` is recorded as "may resume its assay state" (`run_gate.py:5934-5936`), and a wall-stopped run must stay resumable.
  - INCONCLUSIVE means "nothing proven", which is final.

  Why not a new code:
  - The store validates the reason in every stored `terminal_result` and raises on an unknown one ("present malformed data is corruption", `mutation_store.py:1708-1711`, `:1733-1739`). A rename would need a legacy branch there.
  - The in-repo readers already key on `LANE_TIMEOUT` (`assay_analysis/campaign.py:2051`, `gate/python/qualify_sql.py:958`, `tools/b110_*`, `tools/qualify_rust_r2_real.py:131`).

  **What readers key on:**
  - run-gate keys on the outcome only: `BUDGET_EXCEEDED` maps to `BUDGET_EXCEEDED` (`run_gate.py:365`) and is recorded as resumable (`:5934`). Its only reason-code key is `CGROUP_OBSERVATION_UNAVAILABLE` (`:369`, `:407`). So no run-gate code change is needed. The texts at `:17060` and `:17082` stay true.
  - cmru: `cmru/src` reads no assay outcome or reason (grep). Nothing to change.
  - Resume and rejudge rules are in §8.4.
- **(c) No v21 field needs P-13.** P-13 *produces* four values: `kind: replayed`, `execution.mode: witness-isolated`, `judgment.r2.node_baselines` entries and non-zero `replay_fallbacks`. It produces no field. The v21 schema and verify carry all four, and P-17 tests the verify rules (§4 rules 2, 3, 5) on hand-built fixtures. Until P-13 ships, a producer emits none of them: no carried-kill replay exists, a carried kill runs the full judge and projects as `fresh`, `node_baselines` is `[]` and every `replay_fallbacks` count is 0. D-A3 needs only `imported`, which exists today. **P-17 drops its dependency on P-13, and P-13 ships in a 13.x minor.** P-14, P-15 and P-18 keep theirs.

### 8.2 Reasons and buckets (items 6, 7)

| Reason (outcome, exit) | Meaning in v21 | Change |
|---|---|---|
| `LANE_TIMEOUT` (BUDGET_EXCEEDED, 4) | wall stop, (b) above | narrowed: cancel and selection move out |
| **`RUN_CANCELLED`** (BUDGET_EXCEEDED, 4) | SIGTERM/SIGINT/SIGHUP during `run` (LIV-090; `cli.py:801`, `runner.py:274`). The caller sent it, and assay cannot tell an operator from a wrapper budget. | **new** |
| `SELECTION_PARTIAL` (BUDGET_EXCEEDED, 4) | every not-started entry is not-selected (B532, §1) | as listed |
| `CANDIDATE_HUNG` (BUDGET_EXCEEDED, 4) | candidate no-progress that is not a detection: the idle and session-finish rules (wall-based, residual answer 2), and the CPU bounds while their policy is `-v1` | narrowed under `-v2` |

- **No never-started bucket.** VER-080's buckets stay closed. A never-started candidate is a `budget_exceeded` entry with `leader_status: not-started` (`ProcessTreeEvidence.not_started()`, `mutation.py:1764`, `:4876`) plus `stop_cause` (F1). A new bucket would widen VER-080, `--rejudge-outcome`, `assay_analysis` and run-gate's progress reader (`run_gate.py:6018`), and it would add no fact that `stop_cause` does not already carry.
- **Timeout kills (item 7).** Under policy `cpu-since-completed-event-v2`, a candidate stopped by the CPU-without-progress bound or the per-test CPU bound goes to `killed`, with `timeout_kind` and `timeout_evidence` (F3). It counts toward `killed/(killed+survived)`, and no new reason is added. Under `-v1` (P4 as shipped, [P4] `liveness.py:838`), the same stops stay `hung`. A `-v1` bound is CPU seconds sized from baseline *wall* seconds ([P4] `liveness.py:884-900`), so it is not load-independent, and rule 2 forbids it from producing a detection. This is also what lets P-17 ship before A4 (§8.8). Mapping: store decision `cpu-without-progress` gives `timeout_kind: cpu-without-progress`, `per-test-cpu-bound` gives `per-test-bound`, and either gives `endless-loop` only when `timeout_evidence.stack` is non-null (residual answer 3). The rule that turns a stack into `endless-loop` belongs to the stack-evidence item ([P4] backlog).
- **B559 precedence (§5.1) with these names.** Step 3 (any not-started `budget_exceeded`) gives `RUN_CANCELLED` if one entry's `stop_cause` is `cancelled`, else `LANE_TIMEOUT` if one is `wall-stop`, else `SELECTION_PARTIAL`. Step 6 (started `budget_exceeded`) gives `RUN_CANCELLED` if one is `cancelled`, else `LANE_TIMEOUT`. A cancelled R0 is `BUDGET_EXCEEDED/RUN_CANCELLED`.

### 8.3 Every field delta

Common rules:
- No new verdict field enters `judge_sha256`, `resource_sha256` or the tree projection; they are outputs. JID-020 already moves `judge_sha256` with every assay version.
- "Native only" means the field is forbidden under `producer: ingested`, like `provenance` (§1).
- A carried, imported or resumed outcome projects the fields that its executing judge stored (§2.2 rule), with no re-derivation.

| id | Path | Type, values, null | Writer / reader | Verify |
|---|---|---|---|---|
| F1 | `mutant_outcome.stop_cause`: required on every native `budget_exceeded` entry and forbidden elsewhere. Mirrored in the store record | enum `wall-stop`, `candidate-wall-budget`, `not-selected`, `cancelled`; never null | P-17 (`_append_unselected` `mutation.py:1749`, the deadline remainder `:4876`, the budget path `:3754` [P4], the signal path). Readers: verify, `assay_analysis` (replaces its `never_started` inference, `campaign.py:2046-2052`) | `not-selected` requires `leader_status: not-started`. `candidate-wall-budget` requires `timeout-killed` and `progress_bound.budget_per_candidate_role = binding` (or no `liveness`). `unselected_total` equals the `not-selected` count; this replaces the ≤ rule of §4 rule 4. The claim reason follows §8.2 |
| F2 | `mutant_outcome.hang_decision`: required on every native `hung` entry | enum `idle-hang`, `session-finish-hang`, `cpu-without-progress`, `per-test-cpu-bound` (the store's decisions, `mutation_store.py:1403` and [P4] `liveness.py:851-860`) | P-17 copies it from the stored evidence. Reader: verify | the two CPU values are legal only when `progress_bound.policy` is `-v1` |
| F3 | `mutant_outcome.timeout_kind` + `timeout_evidence`: both present or both absent; only on native `killed` | `timeout_kind` enum `endless-loop`, `cpu-without-progress`, `per-test-bound`. `timeout_evidence` = `{required_cpu_s: number>0, measured_cpu_s: number, test_nodeid: str\|null, stack: null\|{source: str, frames: [str] (1..64)}}` | P-17 schema and verify; filled from A4 on (needs `-v2`). Readers: verify, R2 score, `assay_analysis` | requires `progress_bound.policy = -v2` and a non-null `bound_policy`; `measured ≥ required`; `per-test-bound` requires `test_nodeid`; `endless-loop` requires a non-null `stack`; `first_failing_test` is null; `kill_signal` absent; `leader_status: timeout-killed` |
| F4 | `judgment.r2.timeout_kills`: required under native | `{endless-loop, cpu-without-progress, per-test-bound}`, integers ≥ 0 | P-17 (all 0 until A4). Readers: verify, CHANGES/report | re-derived from F3 |
| F5 | `judgment.r2.liveness.progress_bound`: required inside `liveness` | `{armed: bool, reason: str, budget_per_candidate_role: advisory\|binding, policy: "cpu-since-completed-event-v1"\|"-v2"\|null, cpu_without_progress_s: number\|null, per_test_baseline_tests: int\|null, bound_policy: object\|null}`. Null fields when `armed` is false | P-17 projects what P4 already puts on the `plan` record ([P4] `mutation.py` `progress_bound`), plus `policy`; `bound_policy` from A4. Readers: verify, consumers judging `budget_exceeded` | `armed` false requires `role = binding` and every number null; `policy` non-null iff `armed` |
| F6 | `bound_policy` (inside F5; mirrored in the store record of every timeout kill) | `{policy, k, floor_s, per_test_k, per_test_floor_s, m_x, basis: same-host\|ref-gb7, host_key: str\|null, baseline_cpu_s, baseline_ref_cpu_s: number\|null, ref_factor: number\|null}` (A1 §6.4, plus the stored B_ref, A1 note 2) | A4. Reader: resume (§8.4), verify | `basis = ref-gb7` requires non-null `ref_factor` and `baseline_ref_cpu_s = baseline_cpu_s × ref_factor` (abs 1e-6); `same-host` allows `ref_factor` null |
| F7 | `judgment.r2.cpu_time`: optional, native only (A1 §7 path fixed here) | A1 §7 run-level fields as listed there; values nullable with their `*_reason`; `source ∈ {none, hostobs}` | A4 (source fields: A9). Readers: run-gate scheduling (later), humans. Hint only: no verify re-derivation from candidates | if present, all keys present; ref fields null iff `benchmark` null; `ref_factor = single_core/baseline_score`; `avg_cores_basis = raw-wall` iff `frozen_s` null; `limited_by` recomputed from A1 §7 thresholds |
| F8 | `judgment.r0`: required when R0 is declared | `{decided_by: exit-status\|result-report, returncode: int\|null}`; null when the command did not complete (timeout, exec failure, cancel) | P-17 (item 12, RIG-110). Readers: verify, humans | `exit-status`: PASS iff `returncode = 0`; `COMMAND_FAILED` requires a non-zero returncode. `result-report` is legal only when the lane declares `result_report` |
| F9 | Removed: verdict top-level `enforcement` (`verdict.py:6909`, schema `$defs.enforcement`), the lane key `enforcement` (`config.py:1782`; it becomes an unknown key), inventory `enforcement` and `capabilities` | — | P-17 | VER-060's group loses `enforcement`; verify refuses the key |

Store-only (not verdict, listed because the resume rules depend on them):
- S1: `stop_cause` (F1) and `bound_policy` (F6) are written on the record. Absent fields mean a pre-13 record; it belongs to another judge and is never same-judge reused (§8.5).
- S2: the store validator accepts policy `cpu-since-completed-event-v2` with P4's field set ([P4] `mutation_store.py` `_valid_cpu_hang_evidence`), with `per_test_baseline_cpu_s` replacing `per_test_baseline_s` under `-v2`.
- S3: per-candidate `cpu_s`, `cpu_quality` and `frozen_s` (A1 §7) go to the store record and the progress `candidate` event only, **not the verdict**. Reason: the schema rule that per-candidate resource numbers are telemetry and never enter the verdict (`$defs.judgment_r2.profile` description). Their scheduling readers read progress (`run_gate.py:6012-6019`). The numbers that justify a kill are in F3.

### 8.4 Resume, carry and rejudge

- **Wall stop and cancel.** A record whose `stop_cause` is `wall-stop`, `cancelled` or `not-selected` never got its own bound. It is not reused: resume re-executes it whatever its `resource_sha256`. Whether such records are persisted today is [U]; P-17 checks this and pins it with a test. A `candidate-wall-budget` record keeps JID-050: it re-executes when `resource_sha256` differs (`mutation_store.py:599`, `:4267`). run-gate resumes any `BUDGET_EXCEEDED` lane as today, whatever its reason.
- **Timeout kill (D8, A1 note 2).** A stored timeout kill is reused (`resumed`), merged, or imported by `--carry-over` only when these fields of its `bound_policy` equal the current run's: `policy`, `k`, `floor_s`, `per_test_k`, `per_test_floor_s`, `m_x`, `basis`. In addition, `same-host` needs an equal, non-null `host_key`, and `ref-gb7` needs both records at major 7. Otherwise it re-executes. Stored but never compared: `baseline_cpu_s`, `baseline_ref_cpu_s`, `ref_factor`, `required_cpu_s`. They are re-measured every run, and under one `judge_sha256` (same tree and suite) they differ only by noise, which `k` absorbs (by design). Carry-forward never carries a kill (§2.1), so it is unaffected. A record without `bound_policy` re-executes.
- **Rejudge.** `--rejudge-outcome killed` re-runs timeout kills with the other kills, and `--rejudge-outcome budget_exceeded` re-runs every stop cause. There is no new flag and no `--rejudge-carried`.

### 8.5 Item 9: `budget_per_candidate`

P-17 adds `judge.mutation.budget_per_candidate` to JID-030's removed keys (`_judge_projection.py:20`, `:65`), so it leaves the tree digest and enters `resource_sha256` (JID-050). The re-judge rule where the value still binds (`role = binding`) then follows from JID-050 with no new code: a changed value moves `resource_sha256`, so `budget_exceeded`, `hung` and `crashed` records re-execute, while `killed`, `survived` and `equivalent` records are reused (each finished inside its old budget, so each stays evidence). Where it is advisory, it decides nothing. This changes the identity, so it waits for B562 (plan rule 3), and the 13.0.0 CHANGES says so. Its removal is A8.

### 8.6 Items 10, 12, 14, 15, 16

- 10: the flag list is removed in 13.0.0 (RG-297 merged, no reader), with no bump (a).
- 12: F8.
- 14: **drop** `enforcement`. Its only readers are assay's own writer, verifier and inventory (`config.py:1442`, `:1782`, `runner.py:3314`, `verdict.py:5667`, `:6909`, `verify.py:3360`, `cli.py:5074`, `:5246`). run-gate, cmru, `assay_analysis`, tools and fleetd read none (grep). It is declared 43 times in 26 `assay.toml` files, all as `"gate"`. P-17 deletes those lines in the same series (the tree digest moves anyway, §8.5).
- 15: R4 stays reserved, unchanged.
- 16: no field. The count is already in the verdict: `coverage.executable`, `considered` and `branches_total` (schema `$defs.coverage`) show a zero denominator next to `pct: 100`. Keep and document.

### 8.7 A1 controller-read notes

1. D1 is restated as: "no judgement (killed, survived, a CPU bound, a rung status other than through the `hung` count) reads the resource source". Freeze data may only (i) take frozen time out of the wall-based idle and session-finish clocks and (ii) turn a `hung` candidate that overlapped a freeze into a re-run. Both act only on non-judgement results.
2. §8.4 names the compared fields, and F6 stores B_ref.
3. P4 ships as is (binding), and `-v2` comes with v21 via A4.
4. (c) closes it.

### 8.8 Shipping without A4 or A9

If A4 or A9 is late, P-17 ships with these values:
- F7 is absent (it is optional).
- F5 is emitted with `policy: -v1` and `bound_policy: null`.
- F3 never occurs (verify requires `-v2`), and F4 is all zeros.
- F2 may carry the CPU decisions.

A4's minor then fills F6 and F7 and switches to `-v2`. That makes CPU stops `killed`, a change in meaning inside v21 that the minor's CHANGES states. No schema edit is needed, because every value it emits is already legal in the v21 schema. A9 only fills `cpu_time.source*`.

**13.0.0 CHANGES (P-17), consumer notes:**
- `RUN_CANCELLED` is a new reason.
- `LANE_TIMEOUT` no longer covers cancel or selection.
- `enforcement` and the inventory flag list are removed: delete `enforcement` from lane files.
- From `-v2` on, CPU stops count as kills, which raises R2 kill counts.

### 8.9 Recheck at P4 merge

Every [P4] citation above is to `325bdf3ca`. At P4's merge, recheck:
- the policy, source and decision names and their values (`liveness.py:838-860`);
- `derived_bound_from_baseline` and `per_test_cpu_bound_from_baseline` (`:862-900`);
- the `progress_bound` keys on the `plan` record and the arming conditions (`mutation.py` run_mutation, `_execute_mutation_jobs` `:3754`, `:4879`);
- `_valid_cpu_hang_evidence`'s field set (S2);
- P4's statement that `hung` is never `killed` and that it makes no verdict schema change;
- the backlog id of the stack-evidence item (F3 `stack`);
- A1's `@14f81bb97` line numbers.

### 8.10 Open questions (default in force unless the operator overrides)

- **Q-A2-1 (operator).** Should item 14 drop `enforcement`? Default: **drop** (no reader). If kept, only F9's `enforcement` part goes, and the 26 files stay untouched.
- **Q-A2-2 (core).** Default: run-gate needs no change for (b). Later and optional: show `RUN_CANCELLED` apart from wall stops in its record text.
