# B110-P8 — Campaign analysis core (`assay analyze campaign`, B108 phase 1)

*Revised 2026-09-28 after round-1 review (see `REVIEW-2026-09-28-round1.md`; findings P8-1..P8-10, carver decisions C13, C14, C15).*

| Field | Value |
|---|---|
| Backlog | B108 phase 1 of 2. Phase 2, the run-gate automatic post-lane closeout, is **out of scope**. Plan package P8. |
| Branch | `assay-b110-p8-analysis`, cut from the integration line (`assay-b110-integration`, per plan §11.1 / C18) |
| Depends on | **P0 merged**: the candidate event carries `cpu_seconds`, `peak_rss_bytes` and `phase_seconds`. **P7 merged before Work step 9**: `--candidates-file`, `mutation.plan_sha256`, and `"qualifying": false` runs. Read-only tolerance of the v14 fields (plan §5). The v14 fixtures belong to the v14 merge (Work step 11). **Downstream:** P9 depends on P8's `cli.plan_jobs()` (C13), and P11 on P8's exported `project()` (C14). |
| Contract class | 2c: bounded integration. A substantial uncommitted implementation exists and is ported, not redesigned. |
| Implementer | Opus |
| Decisions | A-460 (analysis status/exit policy; P8 deliberately diverges on exit 3, see the status table); A-464 (time, ETA and host load never classify); A-474 / plan D10 (pilot is non-qualifying); A-470 / plan D6 (v14 fields read-only); C13/C14 (round-1 carver decisions) |
| Size | L |

## Why this package exists

Operators have repeatedly written one-off parsers to answer the same campaign questions:
- how many candidates are planned, done and pending;
- the bucket counts;
- which candidates survived or behaved adversely;
- how long the rest will take.

The pilot (plan §7 phase C) and the survivor screen (plan §9.1 step 3) both need one deterministic, verifier-consistent answer to those questions. This package ships that answer as `assay analyze campaign`.

**Two hard rules:**
- The analysis never turns missing evidence into zero or "complete".
- Every duration and estimate it prints is diagnostic only (A-464).

## Context to read first

Paths are relative to `assay/` at HEAD `db85f747` unless marked.

1. **The uncommitted WIP to port.**
   - It lives in the nested worktree `../.worktrees/assay-b107-analysis-30eec294/assay/`, which is based on `30eec294` and dirty. Last modified 2026-09-27 02:29 UTC.
   - Its directory name says "b107" because it was written under an **older backlog numbering**: its "B107" is the main-line **B108**. Its backlog and decisions diffs use colliding IDs; for example, its decisions diff adds an `A-464` row for "B109 cold mutation early-stop", which collides with the main-line A-464. **Do not port those two diffs.**
   - Files to read:
     - `src/assay/campaign.py` (1,295 lines, untracked). Key parts:
       - constants 33-41 (`MIN_ETA_SAMPLE = 5`, `MAX_DETAIL_LIMIT = 500`);
       - `_read_progress` 160-229;
       - `_lane_plan` 231-268 (uses `cli._cmd_plan` so analysis and execution share candidate identity);
       - `_candidate_outcomes` 479-503;
       - `_run_summary` 505-768 (strict milestone ordering, contiguous `candidate_index`, shard re-derivation);
       - `_details` 770-814;
       - `campaign` 816-1233;
       - parser 1235-1254;
       - `run_campaign_command` 1256-1295.
     - `tests/test_campaign.py` (354 lines, untracked): helpers `_repository` :19, `_plan_rows` :71, `_write_progress` :92, `_invoke` :175, `_install_plan` :204. The backlog ID sits in this test module's docstring (`tests/test_campaign.py:1`), not in `campaign.py`'s.
       - **Warning:** `_install_plan` monkeypatches `_lane_plan` with rows taken from the verdict under test (WIP tests :35-47). Tests built only on it share the implementation's assumption. The oracles below therefore add one real-planner fixture (O15).
     - WIP rules to keep:
       - `complete` requires an observed `--command-exit` equal to the verdict exit (`campaign.py:1003`, `:1155`);
       - `measurement_window` (`:1097`).
     - WIP rule to change: without `--coverage`, the WIP reads the lane-declared `.assay` artifact (`campaign.py:318-326`). This contradicts "all paths are explicit inputs" (see Topology).
     - `src/assay/schemas/analysis-campaign.schema.json` (226 lines, untracked).
     - `git -C ../.worktrees/assay-b107-analysis-30eec294 diff -- assay/src/assay/analysis.py`: a 7-line hook that registers the parser and dispatches `campaign`.
2. `src/assay/analysis.py`:
   - `inspect_progress` 326-353;
   - report limits and `_REPORT_EXITS = {"pass": 0, "running": 3, "fail": 1, "evidence_error": 2}` 356-361;
   - `_report_progress` 703-825, whose torn-final-record handling is the pattern to copy;
   - `report` 872-923;
   - `build_analyze_parser` 1023-1061;
   - `cmd_analyze` 1064-1130.
3. `src/assay/cli.py:1820-1845`: the `assay plan` row builder. Today a row carries only `id`, `path`, `operator`, `start_byte`, `end_byte`, `lineno`, `description` (and `reuse`). It does **not** carry `source_sha256` or `mutated_file_sha256`; C13 adds them (Work step 2a). Also read `src/assay/candidate_identity.py:8` (`candidate_id_from_fields`, keyword-only: `path, source_sha256, start_byte, end_byte, mutated_file_sha256, operator`).
4. `src/assay/mutation.py`:
   - `PROGRESS_EVENTS` 846-870;
   - `_progress_event` 998-1021;
   - the `candidate` progress event and state-record payload 2930-2990 (P0 and P4 move these lines; locate them by content). **Ordering fact:** the `candidate` event is written (`:2930`) *before* the state record (`:2966`). A live run observed mid-write can therefore show an event whose record does not exist yet;
   - `_load_validated_state_record` 1273-1402 (key checks at 1313-1348);
   - `_execution_from_state_record` 1450-1497. It raises "execution mode is unknown" for any mode outside `full`/`witness-prefix` (`:1462-1474`) until v14 lands;
   - `select_mutation_shard` 1500-1520;
   - `MUTATION_STATE_RECORD_LIMIT` :206.
5. `src/assay/runner.py`: the coverage baseline's `command_finished` phase is `"baseline"` (`:2835`, `:3744`); the R0-only phase is `"direct"` (`:6344`). P3b adds `"r2-baseline"` (plan §3 D6/A7).
6. `nyxloom-trove/4-backlog.md` `## B108` (desired behavior plus six acceptance boxes). Phase 1 covers boxes 1-4 and the analysis half of box 6. Box 5 (run-gate auto-closeout) is phase 2.
7. `nyxloom-trove/decisions.md`: the A-460 row (:934) and the A-464 row (:943).
8. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §3 (D6/A2, A4 and D10), §5 (the v14 `evidence` and `execution.mode` vocabulary) and §7 phase C (the projection this package supplies). Also `reports/b110/REVIEW-2026-09-28-round1.md` C13/C14.
9. Docs anchors:
   - `README.md:50-60` (the `analyze` examples);
   - `docs/CONSUMERS.md:3992-4060` (`analyze report` and `analyze progress`);
   - `docs/DESIGN-GUIDE.md:3368-3420` (why `analyze report` has its status rules; the section starts at 3368/3370).
10. `tests/test_analysis.py:77` and `:293`, which show how an output is validated against a packaged `analysis-*.schema.json`.
11. The B105 target lists:
   - `assay.toml` `[lanes.self-qualification]` `judge.targets` (~108-165) and `[lanes.self-qualification-preflight]` `judge.targets` (~218-275);
   - `tests/test_self_lane.py:128-135`, which requires the targets to equal every discovered `src/assay/**/*.py`, sorted.

## Implementation packet (normative)

### Port decision (carver)

**Adopt and port the WIP.** Do not write a new reader.

**Why port it:**
- It already implements verdict-bound closeout.
- Its plan reconstruction uses Assay's own planner.
- Its progress-segment validation is strict: milestone order, contiguous `candidate_index`, shard re-derivation and verdict/progress agreement.
- It has bounded paginated details and a schema-validated output.
- A second implementation would duplicate that work and diverge from it.

**How to port:**
- Copy exactly four artifacts into the new branch: `campaign.py`, the schema, `tests/test_campaign.py` and the 7-line `analysis.py` hook.
- Record their sha256 values (pre-port) in the P8 report for provenance.
- Never `git merge` or cherry-pick from that worktree; it is based on `30eec294` and carries colliding backlog/decision edits.
- Before copying, the controller confirms no session is still editing that worktree.

### Plan rows carry candidate identity inputs (C13; owner: `src/assay/cli.py`)

**Why:** a state record can only be checked against the plan if the plan row carries every input of `candidate_id_from_fields`. Today's rows (`cli.py:1820-1828`) lack both digests, so an easy "skip missing keys" check validates nothing (review P8-1, P9-2).

**Change 1: extra row keys.** Add two keys to every `assay plan` row, after `end_byte`. This is additive; no test pins the row key set.
- `source_sha256`: 64 lowercase hex, the sha256 of the candidate file's source bytes, i.e. the same value the executor records.
- `mutated_file_sha256`: 64 lowercase hex, the sha256 of the mutated file bytes.

Take both from the planner's own job objects (the same values `_plan_candidate_id(job)` hashes). Never recompute them from the worktree.

**Change 2: a shared helper.** Extract the job-building part of `_cmd_plan` into a public, pure-ish helper:

```python
def plan_jobs(lane: LaneConfig, *, worktree: Path, request_base: str | None = None) -> list[PlanRow]
```

- `PlanRow` is a `TypedDict`, or a frozen dataclass, with exactly: `id, path, operator, start_byte, end_byte, lineno, description, source_sha256, mutated_file_sha256`.
- `_cmd_plan` calls `plan_jobs`, and the WIP's `_lane_plan` calls it instead of building a `Namespace` by hand.
- The hand-built `Namespace` breaks whenever `_cmd_plan` reads a new argument (P7 and P6 add some). Where a `Namespace` is still needed, build it through the real parser: `build_parser().parse_args([...])`.

**Invariant (oracle O16):** for every row,

```
candidate_id_from_fields(path=row.path, source_sha256=row.source_sha256, start_byte=row.start_byte,
end_byte=row.end_byte, mutated_file_sha256=row.mutated_file_sha256, operator=row.operator) == row.id
```

**Valid row example:**

```json
{"id": "<64hex>", "path": "src/assay/adapters/go.py", "operator": "python:compare-swap",
 "start_byte": 9120, "end_byte": 9122, "lineno": 292, "description": "Eq->NotEq",
 "source_sha256": "<64hex>", "mutated_file_sha256": "<64hex>"}
```

**Invalid for the consumer, each refused by `campaign.py` as `evidence_error`:**
- a row missing `source_sha256`, which means an old planner;
- a row whose recomputed ID ≠ `id`.

**Downstream:** P9 reuses `cli.plan_jobs()`. It is the single plan source for analysis, import and execution.

### CLI (owner: `src/assay/campaign.py`, registered by `analysis.build_analyze_parser`)

```
assay analyze campaign <lane> --file <assay.toml> --expected-commit <40hex|64hex>
    --progress <progress.jsonl>
    [--verdict <verdict.json>] [--state-dir <dir>] [--candidates-file <ids.txt>]
    [--request-base <ref>] [--command-exit <int>] [--log <file>] [--coverage <file>]
    [--outcome <bucket> ...] [--path-prefix <p>] [--offset N] [--limit N]
    [--project --project-jobs N] [--format json|text] [--worktree <dir>]
```

**Changes from the WIP parser:**
- `--verdict` becomes **optional**.
- New flags: `--state-dir`, `--candidates-file`, `--request-base`, `--project` and `--project-jobs`.
- `--project` without `--project-jobs N` (an integer, 1 ≤ N ≤ 64) is refused by argparse (exit 2). `--project-jobs` without `--project` is also refused.
- Everything else keeps the WIP shape.

### Status and exit mapping (replaces the WIP's `0 if complete else 3`)

**Relation to A-460.** Codes 0, 1 and 2 follow A-460's `assay analyze report` convention. **Code 3 deliberately diverges.** In A-460, 3 is a freshness-bounded `running` (≤ 120 s since the last event), and stale input is an evidence error (2). P8 is post-hoc analysis of retained campaign artifacts: interrupted, expired and pilot campaigns are normal inputs, not evidence errors. So 3 means `incomplete` with no freshness bound, and a stale progress stream is `incomplete`, not `evidence_error`. Record this divergence in the P8 report and in DESIGN-GUIDE.

**Rows are evaluated top to bottom; the first match wins.**

| # | Situation | `status` | exit |
|---|---|---|---|
| 1 | Any input refused: malformed, wrong lane, stale commit, disagreement, oversized, a plan the analysis cannot reconstruct, a plan row without identity inputs. | `evidence_error` (the output is still one JSON document with `errors`) | 2 |
| 2 | A pilot (`--candidates-file`, or any progress `candidates` event carrying `selection_sha256`). | `incomplete`, `qualifying: false` | 3 |
| 3 | The verdict's `reason_code` is `LANE_TIMEOUT`, **or** any `budget_exceeded` candidate has no `candidate` progress event in any run at the expected commit, i.e. a never-started leftover masked by expiry. | `incomplete`, plus `unresolved: {matching_total, candidates:[...]}` (capped at `--limit`) listing the never-started or unclassified candidate IDs | 3 |
| 4 | No verdict supplied, or the inventory is not exhausted, or the progress terminal disagrees with the verdict, or `--command-exit` was not supplied, or it differs from the verdict exit, or coverage was not reverified. | `incomplete` | 3 |
| 5 | Verdict supplied, inventory complete, terminal agrees, `--command-exit` supplied and equal to the verdict exit, coverage reverified, verdict outcome `PASS`, and no row above matched. | `complete` | 0 |
| 6 | As row 5, but the verdict outcome is not `PASS` (for example a screen FAIL with survivors). | `complete` | 1 |

**Notes on the rows:**
- **Row 3 follows A-464 and D9.** An expired campaign is an incomplete infrastructure result, never a complete FAIL. The WIP test `test_campaign.py:251` asserts `complete` for never-started `budget_exceeded` leftovers; **invert that test**. This mirrors P7's summary, where an unclassified candidate is "unresolved".
- **Row 4 keeps the WIP rule** that `complete` requires an explicit, matching `--command-exit` (`campaign.py:1003`, `:1155`). B108 says "only the verifier-accepted verdict **plus actual command exit** can establish the gate result". Omitting `--command-exit` gives `incomplete` with `complete_blockers: ["command_exit_not_observed"]`, never `complete`.

**Differences from the WIP:**
- On `evidence_error`, the WIP printed only to stderr and returned 2. P8 prints the JSON document with `status: "evidence_error"` and `errors: [{source, message}]`, bounded to 10 messages with `errors_truncated: bool`. It also keeps the stderr line.
  - The `evidence_error` document shape is: `{schema_version: 1, kind: "assay-campaign-analysis", status: "evidence_error", lane, expected_commit, errors: [{source, message}], errors_truncated}`. No other keys.
- **A pilot can never be `complete`.** `qualifying` is `false` for pilots and `true` for every non-pilot document, including incomplete ones: `true` means "this is not a pilot selection", **not** "this qualifies B105".

### Evidence modes (decision table)

| Inputs | Outcome source per candidate | Can be `complete`? |
|---|---|---|
| progress + verdict (± state-dir) | The verified verdict buckets, via `verify_text` as in the WIP's `_read_verified_verdict`. Progress events must agree with the verdict: disagreement is an `evidence_error`, as in the WIP. State records are reconciled as described below; they never override the verdict. | yes |
| progress + state-dir, no verdict | Counted records (see *Store reconciliation*) plus latest-run events. A candidate with only an event (no record yet) takes the event bucket and is marked `outcome_source: "progress"`. A live run writes the event before the record (`mutation.py:2930` vs `:2966`). | no |
| progress only | The latest event per candidate across all runs at the expected commit. A candidate reported with two different buckets in two runs is listed under `reclassified: [{candidate_id, runs:[{run_id, bucket}]}]` and is not an error. The run with the higher `line_start` wins the count. | no |

**Read-only state-record shape validation.** There is no judge re-derivation: `_load_validated_state_record` needs a live judge and cannot be used here. Checks are applied in this order:
1. The filename matches `^[0-9a-f]{64}\.json$`. Any other file is ignored by name, for example P9's `CAMPAIGN-IDENTITY`, `SHA256SUMS`, `.lock`, P7's `PILOT-STATE` sentinel, and dotfiles.
2. The file is ≤ `MUTATION_STATE_RECORD_LIMIT`.
3. It is UTF-8 JSON and an object.
4. `schema_version == 1`.
5. `candidate_id` equals the filename stem.
6. **Identity:** `candidate_id_from_fields(path=, source_sha256=, start_byte=, end_byte=, mutated_file_sha256=, operator=)` over the record's own fields equals the stem. A record whose identity inputs are missing or malformed fails here.
7. `outcome_bucket ∈ MUTATION_BUCKETS`.
8. `_execution_from_state_record(record)` does not raise. **Before v14,** a `witness-cold` or `ledger` mode raises here, so such records are an `evidence_error` until the v14 merge (see Work step 11).
9. `judge_sha256` is a 64-hex string.

A failure of checks 2–9 is an `evidence_error` naming the file. These are structural defects: a torn or forged file, never a legitimate cross-tree state.

**Store reconciliation** (C14). This replaces the earlier "must agree" and "mixed_judge" rules. It is applied after shape validation:
1. **Foreign records.** A valid record whose `candidate_id` is not in the reconstructed plan, for example a candidate a source fix removed, goes to `state.foreign: {count, sample_ids[≤10]}`. It is **not** an error and not counted. Re-screens reuse the screen state dir (plan §9.1), so foreign records are normal.
2. **The current judge.**
   - It is the `judge_sha256` carried by the latest run's evidence when available: the verdict's `judge_provenance`, or a `resume` event field if present.
   - Otherwise it is **unknown**. Then no record is counted toward completion, and records only enrich rows (`outcome_source: "state"`) for candidates that also have a latest-run event with the same bucket.
3. **Counted records.** A record counts toward `completed_total` only if it is in the selected set (shard, selection, or plan), its `judge_sha256` equals the current judge, and it reconciles with the latest run, meaning either:
   - a latest-run `candidate` event exists for it with the same bucket; or
   - the latest run's `resume` event reports it among `resumed` (the count reconciles: `resume.resumed_total` must equal the number of counted records without a latest-run event, else `evidence_error` "resume count does not reconcile with the store").
4. **Stale-judge records.** Records with another `judge_sha256` are **never counted**. They go to `state.stale_judge: {count, judge_sha256_counts}`. They are not an error: a store is legitimately mixed after a tree change, and the latest run's `resume.rejected_total` accounts for them.
5. **Disagreement.** A counted-eligible record (current judge, in selection) whose bucket disagrees with the latest-run event for the same candidate goes to `reclassified` with `source: "state_vs_progress"`. It is counted once, by the latest-run event, and is not an error. Only a disagreement with a *verified verdict bucket* is an `evidence_error`.
6. **The output block:** `state: {records, counted, foreign, stale_judge, judge_sha256_current, judge_sha256_counts}`.

**Wrong implementations this rules out:**
- An all-stale store (every record at J_X, latest run at X′) must **not** yield `pending_total == 0`, `eta_reason: "no_remaining_work"`, or `survived: 0` built from stale kills. Fixture F2 in O17 pins this.
- `completed_total` is restricted to the selected set, never "all valid records".

### Plan reconstruction without a verdict

- Keep the WIP's `_lane_plan`. When the lane's `judge.base_source == "request"` and there is no verdict, `--request-base` is required; otherwise refuse (`evidence_error`, "plan base cannot be reconstructed without --verdict or --request-base").
- B105 is `whole_target` and needs no base.

### Selection (pilot) and shard

- `--candidates-file` is read with P7's parser, one 64-hex ID per line. P7's refusals apply: duplicates, an empty file, unknown IDs.
- The digest helper is **`mutation.plan_sha256(ids)`** (defined by P6, reused by P7). It computes netstrings over the IDs **in plan order**. **Import it; never duplicate it.**
  - Before hashing, sort the selected IDs into **plan order** (the order of `cli.plan_jobs()` rows), not file order. A candidates file listed in reverse plan order must still match.
- The latest run's `candidates` event must carry `selection_sha256` equal to that value, and `selected_total == len(ids)`.
- **In `_run_summary`,** generalize `selected_ids` to: the shard assignment if a `shard` event exists; else the selection set if `--candidates-file` was given; else the whole plan.
- A run whose `candidates` event carries `selection_sha256` while no `--candidates-file` was supplied is an `evidence_error` ("pilot selection not supplied").

### Per-candidate row (details and adverse lists)

**Row shape.** Extend the WIP `_details` row (`candidate_id`, `outcome`, `path`, `lineno`, `operator`, `description`, `start_byte`, `end_byte`, `execution_mode`) with:

```json
{"elapsed_seconds": 412.7, "cpu_seconds": 380.2, "peak_rss_bytes": 612368384,
 "phase_seconds": {"materialize": 2.1, "command": 409.9, "integrity": 0.7},
 "started_count": null, "evidence_command": "r2", "outcome_source": "verdict",
 "run_id": "progress-line-7:2026-10-02T10:00:00+00:00"}
```

**Where each field comes from:**
- The timing fields come from the progress `candidate` event of the run that executed the candidate.
- `started_count` and `evidence_command` come from the v14 verdict `evidence` object, or the state record's `evidence` in no-verdict mode.
- **Absent always means `null`. Never 0.**
- `execution_mode` is the raw string from the verified verdict, the validated record, or, in progress-only mode, P3b's candidate-event `execution_mode` field when present (else `null`). Count modes generically (`execution_mode_counts: {mode: n}`), so v14's `witness-cold` and `ledger` need no code change in the counting path.
- P0 also emits `startup_seconds`. Keep it in the row as `startup_seconds` (null when absent).

**Adverse lists:**
- `adverse: {survived: {matching_total, candidates:[...]}, hung: ..., crashed: ..., budget_exceeded: ...}` is always emitted.
- Each list is capped at `--limit`, and its `matching_total` is always exact.
- `--outcome`, `--path-prefix` and `--offset` apply only to `candidate_details`, not to `adverse`.
- A truncated list says so through `next_offset`.

### Counts (no double counting)

**The `campaign` object:**
- `planned_total`: the plan size.
- `selected_total`: the shard, selection or plan size.
- `completed_total`: candidates with exactly one resolved outcome, unioned across the verdict, records and latest events. A candidate is never counted twice.
- `pending_total = selected_total - completed_total`. It is `null` when `selected_total` is unknown.
- `resumed_total` and `rejudged_total`: taken from the **latest** run only. The per-run arrays stay as in the WIP.
- `execution_mode_counts`: a map from each mode to its count.
- `outcomes`: a map from bucket to count.

**Appended runs:** summarize each run separately under `runs` (the WIP shape). Never sum per-run candidate events into `completed_total`.

### ETA (diagnostic) — exact sample rule

**The sample** is the latest run's `candidate` events where:
- `outcome_bucket ∈ {killed, survived, equivalent}`;
- `elapsed_seconds > 0`;
- the candidate is not resumed.

Any execution mode counts.

**Threshold:** `MIN_ETA_SAMPLE = 20`. This replaces the WIP's 5.

**When the sample is too small or there is nothing left:** `timing.eta = null` and `timing.eta_reason ∈ {"no_remaining_work", "insufficient_sample", "remaining_work_unknown"}`, with `timing.sample_count` and `timing.minimum_sample_count = 20`.
- `"no_remaining_work"` is only legal when `pending_total == 0` was computed from **counted** evidence (see *Store reconciliation*).
- `"remaining_work_unknown"` applies when `pending_total` is `null`.

**Otherwise:**

```json
"timing": {"diagnostic_only": true, "eta_reason": null, "sample_count": 57, "minimum_sample_count": 20,
  "measurement_window": {"first_emitted_at": "2026-10-02T10:00:00+00:00", "last_emitted_at": "2026-10-02T11:10:00+00:00", "run_id": "progress-line-7:2026-10-02T10:00:00+00:00"},
  "eta": {"jobs": 3, "jobs_source": "lane", "pending": 3703,
          "p50_candidate_s": 11.2, "p90_candidate_s": 38.0,
          "remaining_seconds_p50": 13824.5, "remaining_seconds_p90": 46905.3},
  "excluded_candidate_counts": {"crashed": 0, "hung": 0, "budget_exceeded": 0, "resumed": 0}}
```

**Rules:**
- **`jobs`.** No event carries a `jobs` field today, and none of P3b, P4 or P7 adds one to the `plan` or `candidates` event. So `jobs` comes from:
  - the lane declaration (`jobs_source: "lane"`), or
  - for a pilot, the P7 summary is not an input, so from the `--project-jobs` value when `--project` is given (`jobs_source: "project-jobs"`).

  Never infer it from event concurrency.
- **Percentiles** use the nearest-rank method on the ascending sorted sample: `pX = sorted[ceil(X/100 × n) − 1]`, a 0-based index. Never `statistics.median`: with n = 20, p50 is `sorted[9]`, the 10th value, not the mean of the 10th and 11th.
- **Remaining time:**
  - `remaining_seconds_pX = round(pending × pX_candidate_s / jobs, 1)`;
  - `pX_candidate_s = round(sorted[...], 1)`, rounded after selection;
  - `remaining_*` is computed from the unrounded percentile, then rounded to 1 decimal.
- **`measurement_window`** is the WIP's (`campaign.py:1097`): the first and last `emitted_at` of the sampled events, plus the run ID. It is `null` when there is no sample. B108 requires it.
- Time **never** changes `status` or the exit code.

### Stratified projection (`--project`; the pilot's §7 phase C helper)

**Output.** `projection` is `null` unless `--project` is given. With it:

```json
"projection": {"diagnostic_only": true, "strata_rule": "path×operator→operator→all, n≥20",
  "bases": {
    "killed": {"samples": 58, "candidates_projected": 3760,
               "fallback_counts": {"path_operator": 0, "operator": 1843, "all": 1917},
               "serial_seconds_p50": 41000.0, "serial_seconds_p90": 132000.0},
    "killed_and_survived": { ...same shape... }},
  "jobs": 3, "jobs_source": "project-jobs",
  "fixed_seconds_measured": 1180.4,
  "fixed_components": {"coverage_baseline": 548.3, "r2_baseline": 402.1, "other": 230.0},
  "fixed_components_missing": ["r3"],
  "wall_seconds_p50": 14846.7, "wall_seconds_p90": 45180.4, "wall_basis": "killed"}
```

**How it is computed:**
1. **Per-candidate cost.** For every candidate in the **full plan** (not only the selection), take the cost of its stratum:
   - the stratum `(path, operator)` if that has ≥ 20 samples;
   - else `(operator)` if that has ≥ 20;
   - else `all`, if the whole basis has ≥ 20;
   - otherwise the basis is `null`, with `reason: "insufficient_sample"`.

   With about 70 pilot samples, the `path×operator` strata will rarely reach 20. That is expected, and the fallback counts disclose it.
2. **Serial totals.** For each basis, sum the p50 and the p90 costs, using the same nearest-rank rule as the ETA.
3. **Wall time.** `jobs` is the required `--project-jobs N` (C14). For the pilot, pass the pilot's `--pilot-jobs` value (3). For a qualifying projection, pass the lane's `jobs`. `wall = fixed + serial / jobs`. The `killed` basis is the one used in `wall_*`, because the qualifying run is all-kills (plan §2 item 2).
4. **`fixed_components`** (exact formula, from the latest run of the analyzed progress stream):
   - `coverage_baseline`: `ended − started` of the latest run's `command_finished` event with `phase == "baseline"` (`runner.py:2835`/`:3744`). If absent, use the `direct` phase (`:6344`).
   - `r2_baseline`:
     - `ended − started` of the latest run's `command_finished` event with `phase == "r2-baseline"` (P3b);
     - **only if** that event is absent, the `plan` event's `r2_baseline_s` (plan D6/A7);
     - never both, so there is no double count.
   - `other`: the first `candidate` event's `emitted_at`, minus the `run` header's `started`, minus `coverage_baseline`, minus `r2_baseline`, clamped at ≥ 0. This covers setup, snapshot preparation, R1 parsing and discovery.
   - Any component whose source event is missing is `null` and is listed in `fixed_components_missing`. R3 is always listed for a pilot, because R3 is not run there.
   - `fixed_seconds_measured` is the sum of the non-null components.
5. **The pure core.** Export the computation as a pure function in `campaign.py`:

   ```python
   def project(samples: Sequence[CostSample], rows: Sequence[PlanRow], jobs: int, fixed: Mapping[str, float | None]) -> dict
   ```

   - `CostSample` is `(path, operator, bucket, seconds)`.
   - It returns the `projection` object above. There is no I/O and no clock.
   - P11 imports it to project synthetic unit-mode costs (C14). It is part of P8's public contract, so test it directly.

**Hard rules:**
- The numbers in these examples are illustrative only.
- No test may assert a predicted projection value (see §3b.F). Tests assert the arithmetic on synthetic samples.

### Output schema

- Update `src/assay/schemas/analysis-campaign.schema.json` (`"schema_version": {"const": 1}`, `additionalProperties: false` throughout) for every field above.
- It is packaged by the existing `schemas/*.json` package-data (`pyproject.toml:77`); no pyproject change is needed.
- **One valid example:** the JSON output of the complete-campaign fixture.
- **Two invalid examples, which must fail schema validation:**
  - `timing.eta` present while `sample_count < 20`. Encode this as a JSON-schema `if`/`then`: `if {properties:{sample_count:{maximum:19}}} then {properties:{eta:{const:null}}}`. It is fully expressible, so there is no separate "model check".
  - `status: "complete"` with `qualifying: false`.
- **Other shape rules:**
  - The schema has explicit object shapes, with `additionalProperties: false`, for `timing`, `timing.measurement_window`, `projection`, `projection.fixed_components` and `unresolved`.
  - `fixed_components_missing` is an array of `enum ["coverage_baseline","r2_baseline","other","r3"]`.
  - The `evidence_error` document is its own `oneOf` branch.

### Topology

- **All paths are explicit inputs.** Nothing is inferred from `.assay/`. In particular, without `--coverage` no coverage artifact is read, which changes the WIP's `campaign.py:318-326` default. Coverage status is then `"not_supplied"`, which blocks `complete` exactly like "not reverified".
- The worktree's HEAD must equal `--expected-commit` (the WIP's `analysis._identity`).
- `--file` must be bound to that commit (the WIP's `_bind_lane_file`).
- State-dir, progress and verdict paths may lie outside the worktree.

### Degrees of freedom

- Private helper names and internal decomposition inside `campaign.py`.
- The text-format layout, provided it prints: status, outcome counts, adverse totals, `next_offset`, and ETA/projection marked "diagnostic".

## Work

1. Port the four WIP artifacts listed above. Record their pre-port sha256 values. Commit as `feat(assay): port campaign analysis WIP (B108 phase 1)`, unchanged apart from the backlog ID in the test module's docstring (`tests/test_campaign.py:1`).
2. Add `src/assay/campaign.py` to **both** `judge.targets` lists in `assay.toml`, in sorted position. Confirm `tests/test_self_lane.py` is green. **Note:** `campaign.py` adds R2 mutation targets to the B105 campaign. They must be killed like every other candidate before the plan's qualifying GO, so budget the screen accordingly.
   - **2a (C13).** Add `source_sha256`/`mutated_file_sha256` to the `assay plan` rows, and extract `cli.plan_jobs()` (see *Plan rows carry candidate identity inputs*). Switch the WIP's `_lane_plan` to `plan_jobs()`. Wherever a `Namespace` is still needed, build it with the real parser. Add O16.
3. Replace the exit mapping and emit `evidence_error` documents (status/exit table rows 1–6). Invert the WIP test at `tests/test_campaign.py:251`, so that never-started `budget_exceeded` leftovers are `incomplete` (row 3).
4. Make `--verdict` optional, and implement the three evidence modes, read-only shape validation (identity via `candidate_id_from_fields`), store reconciliation (`foreign`, `stale_judge`, counted records, `reclassified`).
5. Implement plan reconstruction without a verdict (the `--request-base` rule).
6. Extend candidate rows, the `adverse` lists, `unresolved` and `execution_mode_counts`. Tolerate absent P0/v14 fields as `null`.
7. Implement the ETA sample rule (`MIN_ETA_SAMPLE = 20`, the nearest-rank index formula, `measurement_window`, the `jobs` source rules).
8. Implement `--project --project-jobs N`: the pure exported `project()`, hierarchical strata, two bases, and the exact fixed-component formula.
9. **After P7 is merged:** add `--candidates-file`, the `mutation.plan_sha256` (plan-order) selection checks, `qualifying: false` and the never-complete rule (status row 2). If P7 has not landed when you reach this step, write `BLOCKED: P8 step 9 waits for P7` to the LOG, finish steps 10-12 for everything else, and commit.
10. Update the schema, the docs (see Docs sync) and CHANGES. If `campaign.py` or `cli.py` gain or change a dataclass, regenerate `tests/fixtures/dataclass-contract.json` with P1's documented command (C15).
11. **v14 merge step, for the controller: O14 moves here.** Before v14, `_execution_from_state_record` rejects `witness-cold` (`mutation.py:1462-1474`), and `verify_text` rejects a v14 verdict. So O14 cannot pass on P8's own branch. When `assay-b110-v14` merges into the integration line, the merge must:
    - add a v14 verdict fixture with `witness-cold`, `evidence` and `ledger` entries, plus a matching state record, to `tests/test_campaign.py`;
    - run O14 there.

    If P8 merges *after* v14, add those fixtures in P8 itself. On P8's branch, the progress-only-mode half of O14 (an injected `execution_mode` string in a `candidate` event) is tested now.
12. Run the focused tests, then the gates. Write the report.

## Oracles

Each oracle lists its observable, its negative, and the test that checks it. All go in `tests/test_campaign.py`. Most use fast synthetic fixtures built with the WIP helpers `_repository`, `_write_progress` and `_install_plan`, and `tests/fixtures/verdicts/*.json` as verdict seeds.

**Caution:** `_install_plan` feeds the plan from the verdict under test, so it shares the implementation's assumption. **O15 uses the real planner** and is mandatory.

| # | Oracle | Observable | Negative (the wrong implementation it catches) |
|---|---|---|---|
| O1 | A complete campaign with verdict PASS and `--command-exit 0` exits 0. The same campaign with a FAIL verdict and `--command-exit 1` exits 1, `status: complete`. The PASS campaign **without** `--command-exit` gives `incomplete`, exit 3, `complete_blockers: ["command_exit_not_observed"]`. `--command-exit 1` against a PASS verdict gives `evidence_error`, exit 2. | Exit code, `status`, `complete_blockers` | The WIP's "complete → 0" regardless of outcome; ignoring an absent `--command-exit` |
| O2 | Appended `--resume` runs: `completed_total` equals the unique resolved candidates, and `runs[]` keeps per-run counts. | Three runs where run 2 resumes 5 and executes 3 | Summing candidate events across runs |
| O3 | Without a verdict the result is never `complete`, even when every candidate event is present. | `status: incomplete`, exit 3 | Treating event exhaustion as completion |
| O4 | Structural and verdict disagreements. A state record whose bucket disagrees with the **verified verdict** bucket, and a record whose recomputed `candidate_id_from_fields(...)` ≠ its filename stem, each give `evidence_error`, exit 2, with the filename named. A record whose *path* was altered (so its identity no longer matches) is caught the same way. | `errors[].message` | Silently preferring one source; skipping missing keys |
| O5 | Store reconciliation, fixture **F2**: no verdict; every record is at judge J_X; the latest run is at X′ with `resume.rejected_total = N` and 3 candidate events; one extra record is for a candidate absent from the plan. Expected: `pending_total == selected_total − 3`, `state.stale_judge.count == N`, `state.foreign.count == 1`, no error, `eta_reason != "no_remaining_work"`, and survivors counted only from the 3 events. | Output fields | Pending computed from stale records; foreign records treated as an error; `completed_total` counting all valid records |
| O6 | Progress-only mode: the same candidate is `survived` in run 1 and `killed` in run 2. It is listed under `reclassified` and counted once, as killed. | `reclassified`, `outcomes` | Double counting, or an error |
| O7 | ETA. 19 samples → `eta: null`, `eta_reason: "insufficient_sample"`. 20 samples with **distinct** 10th and 11th values (e.g. 1.0…20.0) → `p50_candidate_s == 10.0` (the 10th value, index 9) and `p90_candidate_s == 18.0` (index 17), exactly as `sorted[ceil(X/100×n)−1]` gives. `measurement_window` spans the sampled events. | Values | `statistics.median` (would give 10.5); mean instead of rank; the WIP's threshold of 5 |
| O8 | Changing every timing and resource field (`elapsed_seconds`, `emitted_at`, `cpu_seconds`, `peak_rss_bytes`, `phase_seconds`, `startup_seconds`) in a slower-host replay changes the `timing`/`projection` numbers but **not** `status`, `outcomes`, the exit code, `unresolved` or the `adverse` membership. | Diff of the two outputs, restricted to those keys | Any time- or resource-dependent classification |
| O9 | Projection fallbacks, via the exported pure `project()`: 25 samples of one operator and 3 of another give `fallback_counts` operator/all exactly as specified. A basis with fewer than 20 samples overall is `null` with a reason. `--project` without `--project-jobs` is refused (exit 2). | Output | Projecting from 3 samples; defaulting jobs to 1 |
| O10 | The adverse list with `--limit 2` over 5 survivors shows 2, `matching_total: 5`, and `next_offset: 2`. | Output | Silent truncation |
| O11 | Pilot selection (fixture **F3**). A candidates file in **reverse plan order** whose `selection_sha256` was computed over plan order must match. A `candidates` event whose `selection_sha256` mismatches, and a pilot progress stream with no `--candidates-file`, are each `evidence_error`. A matching selection gives `qualifying: false` and `status: incomplete` even when everything is killed. `verdict_written{destination: null, exit_code: 6}` is accepted. `--project --project-jobs 3` gives `projection.jobs == 3`. | Output | Hashing in file order; a pilot reported complete |
| O12 | A torn final progress record: with no verdict it is `incomplete` and `torn_final_record: true`. With a verdict it is `evidence_error`. | Output | The WIP always refuses, or always accepts |
| O13 | Every emitted document, including the `evidence_error` document, validates against `analysis-campaign.schema.json`. The two invalid examples fail validation, and so does an extra key in `timing` or `projection`. | `Draft202012Validator` | Schema drift; an open `timing` object |
| O14 | **Two halves.** On P8's branch: in progress-only mode, a `candidate` event carrying `execution_mode: "witness-cold"` is counted in `execution_mode_counts` without a code change. **At the v14 merge (Work step 11):** a v14 verdict and record with `witness-cold`/`ledger` modes are counted the same way. | Output | A hard-coded mode list; monkeypatching the execution validator |
| O15 | **Real planner** (a `_seed_pytest_mutation`-style tiny repo, planned by `cli.plan_jobs()` with no `_install_plan`): a real R2 verdict from `runner.run_lane`, analyzed with `--command-exit`, gives `complete`. The rows' `source_sha256`/`mutated_file_sha256` validate the real state records. Add a `--rejudge` second run and a B106 `--reuse-from` replay run, covering B108 box 1: counts are per run, with no double counting. | Output | Tests that only pass because the plan is derived from the verdict |
| O16 | `cli.plan_jobs()` rows: for every row of the real-planner fixture, `candidate_id_from_fields(**identity fields) == row["id"]`. A row with a tampered `source_sha256` makes a matching state record an `evidence_error`. | Values | Rows without digests; skipping missing keys |
| O17 | Expiry (fixture **F1**): the verdict's `reason_code` is `LANE_TIMEOUT`, 200 never-started `budget_exceeded` leftovers have no candidate event, `--command-exit 4`, there is a terminal `end`, and a state dir is supplied. Expected: `status: incomplete`, exit 3, `unresolved.matching_total == 200`. Also a sharded 1/4 PASS verdict with `--command-exit 0` (fixture **F4**) is never `complete`. | Output | LANE_TIMEOUT → `complete`, exit 1; shard PASS → complete |
| O18 | All six buckets (B108 box 2): a fixture verdict with at least one candidate in each of `killed`, `survived`, `equivalent`, `crashed`, `hung` and `budget_exceeded` gives exact `outcomes` counts and exact adverse candidate IDs, paths and operators. Stale-commit and wrong-lane inputs are each `evidence_error`. | Output | Dropped buckets; silent zero on bad input |
| O19 | Coverage (B108 box 4): with `--coverage` pointing at a reverified artifact that has one missing line and one missing arc, the output lists them exactly. **Without** `--coverage`, `coverage.status == "not_supplied"`, `status` is not `complete`, and no `.assay` file is read (assert with a non-existent declared artifact path). | Output | Reading the lane-declared artifact implicitly; a missing artifact shown as zero gaps |

**Boxes not covered.** If any B108 box 1-4 item cannot get an oracle in this package, **do not tick that box**. List it as a residual.

**Gate observable:** registered `tester-unified` PASS, and `self-qualification-preflight` R0/R1 PASS. The new module must be at 100% line and branch coverage under the B105 floor, and must add no new exclusion.

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

- **README (WHAT):** one `assay analyze campaign` example next to the existing `analyze` examples (:50-60). State that it is read-only and that estimates are diagnostic.
- **DESIGN-GUIDE (WHY):** a subsection after the `analyze report` rationale (~:3368-3420) covering:
  - explicit inputs only;
  - why the result is never `complete` without a verdict and a matching command exit, for a pilot, or after an expiry;
  - store reconciliation (`foreign`, `stale_judge`, counted records);
  - the exit mapping and how it aligns with A-460: 0, 1 and 2 are shared; 3 means post-hoc `incomplete`, not A-460's freshness-bounded `running`, and why;
  - the ETA sample rule and the projection strata;
  - that A-464 makes both of them diagnostic;
  - the port provenance (one sentence).
- **CONSUMERS (HOW):** after `### Take one gate snapshot with assay analyze report (B100)` (~:3992), add two pasteable workflows:
  - analyzing a screen verdict and listing its survivors;
  - a pilot projection with `--candidates-file --project`.
- **CHANGES.md:** `## [Unreleased]` → `### Added` bullet.
- **CONSUMERS (HOW), `assay plan`:** one line documenting the two new plan-row keys (`source_sha256`, `mutated_file_sha256`).
- **Backlog:** tick only the B108 acceptance boxes 1-4 that have an oracle above (O15, O18 and O19 cover boxes 1, 2 and 4). Add a phase note to B108's status line. **Do not** tick box 5.

## Scope / forbid

**Touch:**
- `src/assay/campaign.py` (new);
- `src/assay/analysis.py` (the hook only);
- `src/assay/cli.py` (C13 only: extract `plan_jobs()` and add the two row keys; no other behavior change);
- `src/assay/schemas/analysis-campaign.schema.json` (new);
- `tests/test_campaign.py` (new);
- `tests/test_b105_cli_boundaries.py` and any existing test that pins the plan payload, but only to accept the two additive row keys;
- `tests/fixtures/dataclass-contract.json`, regenerated with P1's documented command only if a dataclass is added or changed (C15);
- `assay.toml` (the two target lists only);
- README, DESIGN-GUIDE, CONSUMERS, CHANGES;
- the B108 entry in `nyxloom-trove/4-backlog.md`;
- your report.

**Forbid:**
- any change to verdict, schema, runner, mutation or liveness behavior (the `cli.py` plan-row change is additive only);
- `run-gate-project/`;
- the WIP worktree itself (read only);
- the WIP's backlog/decision diffs;
- run-gate auto-closeout (phase 2);
- any new `ReasonCode`.

**Needing more than two files outside this list is a BLOCKED trigger.**

## Gate

1. Focused tests, serially: `nice -n 19 ionice -c3 python -m pytest tests/test_campaign.py tests/test_analysis.py tests/test_self_lane.py tests/test_b105_cli_boundaries.py -q -p no:cacheprovider`. If P1 has landed, add `tests/test_dataclass_contract.py`.
2. `cd <worktree>/assay && python ./run-gate.py tester-unified`. The worktree must be under `/workspaces/vbpub`. Then, **in a separate step**, read `ASSAY_GATE_CONTAINER_EXIT` and `ASSAY_REGISTERED_GATE_COMPLETE` from the gate log (L4). Never pipe-tail them.
3. `python ./run-gate.py self-qualification-preflight`. The new module adds target lines, so the 100% whole-target floor must hold.

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file: STOP. Write `BLOCKED: <reason>` to the LOG (`nyxloom-trove/reports/assay-B110-P8-REPORT.md`), commit, and exit. Do NOT improvise a workaround.

**Specific triggers:**
- `mutation.plan_sha256` (P6/P7) does not exist, or has a different contract from "netstrings over IDs in plan order" (step 9 only).
- The ported WIP cannot reach 100% branch coverage without a pragma.
- `cli.plan_jobs()` cannot reconstruct the plan for a lane without a verdict, or the planner's job objects do not expose the source and mutated-file digests.
- The current judge cannot be determined from any supplied evidence, and a fixture requires counting records. Report it; do not invent a judge source.

## Report

Write `nyxloom-trove/reports/assay-B110-P8-REPORT.md` with:
- the ported-file sha256 values;
- the port decision;
- a traceability table (`work | owner | oracle | test | controlled break`) with actual test names and failure counts, red-first;
- the gate verdicts, read separately;
- residuals.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`
