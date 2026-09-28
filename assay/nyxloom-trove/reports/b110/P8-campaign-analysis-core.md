# B110-P8 — Campaign analysis core (`assay analyze campaign`, B108 phase 1)

| Field | Value |
|---|---|
| Backlog | B108 phase 1 of 2. Phase 2, the run-gate automatic post-lane closeout, is **out of scope**. Plan package P8. |
| Branch | `assay-b110-p8-analysis`, cut from the integration line `assay-b105-evidence-integrity` after the plan §11.1 reconciliation |
| Depends on | **P0 merged**: the candidate event carries `cpu_seconds`, `peak_rss_bytes` and `phase_seconds`. **P7 merged before Work step 9**: `--candidates-file`, the `selection_sha256` helper, and `"qualifying": false` runs. Read-only tolerance of the v14 fields (plan §5); the v14 integration rebases this code (Work step 11). |
| Contract class | 2c: bounded integration. A substantial uncommitted implementation exists and is ported, not redesigned. |
| Implementer | Opus |
| Decisions | A-460 (analysis status/exit policy); A-464 (time, ETA and host load never classify); A-474 / plan D10 (pilot is non-qualifying); A-470 / plan D6 (v14 fields read-only) |
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
     - `tests/test_campaign.py` (354 lines, untracked): helpers `_repository` :19, `_plan_rows` :71, `_write_progress` :92, `_invoke` :175, `_install_plan` :204.
     - `src/assay/schemas/analysis-campaign.schema.json` (226 lines, untracked).
     - `git -C ../.worktrees/assay-b107-analysis-30eec294 diff -- assay/src/assay/analysis.py`: a 7-line hook that registers the parser and dispatches `campaign`.
2. `src/assay/analysis.py`:
   - `inspect_progress` 326-353;
   - report limits and `_REPORT_EXITS = {"pass": 0, "running": 3, "fail": 1, "evidence_error": 2}` 356-361;
   - `_report_progress` 703-825, whose torn-final-record handling is the pattern to copy;
   - `report` 872-923;
   - `build_analyze_parser` 1023-1061;
   - `cmd_analyze` 1064-1130.
3. `src/assay/mutation.py`:
   - `PROGRESS_EVENTS` 846-870;
   - `_progress_event` 998-1021;
   - the `candidate` progress event and state-record payload 2930-2990 (P0 and P4 move these lines; locate them by content);
   - `_load_validated_state_record` 1273-1402 (key checks at 1313-1348);
   - `_execution_from_state_record` 1450-1497;
   - `select_mutation_shard` 1500-1520;
   - `MUTATION_STATE_RECORD_LIMIT` :206.
4. `nyxloom-trove/4-backlog.md` `## B108` (desired behavior plus six acceptance boxes). Phase 1 covers boxes 1-4 and the analysis half of box 6. Box 5 (run-gate auto-closeout) is phase 2.
5. `nyxloom-trove/decisions.md`: the A-460 row (:934) and the A-464 row (:943).
6. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §3 (D6/A2, A4 and D10), §5 (the v14 `evidence` and `execution.mode` vocabulary) and §7 phase C (the projection this package supplies).
7. Docs anchors:
   - `README.md:50-60` (the `analyze` examples);
   - `docs/CONSUMERS.md:3992-4060` (`analyze report` and `analyze progress`);
   - `docs/DESIGN-GUIDE.md:3372-3420` (why `analyze report` has its status rules).
8. `tests/test_analysis.py:77` and `:293`, which show how an output is validated against a packaged `analysis-*.schema.json`.
9. The B105 target lists:
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

### CLI (owner: `src/assay/campaign.py`, registered by `analysis.build_analyze_parser`)

```
assay analyze campaign <lane> --file <assay.toml> --expected-commit <40hex|64hex>
    --progress <progress.jsonl>
    [--verdict <verdict.json>] [--state-dir <dir>] [--candidates-file <ids.txt>]
    [--request-base <ref>] [--command-exit <int>] [--log <file>] [--coverage <file>]
    [--outcome <bucket> ...] [--path-prefix <p>] [--offset N] [--limit N]
    [--project] [--format json|text] [--worktree <dir>]
```

**Changes from the WIP parser:**
- `--verdict` becomes **optional**.
- New flags: `--state-dir`, `--candidates-file`, `--request-base` and `--project`.
- Everything else keeps the WIP shape.

### Status and exit mapping (A-460-aligned; replaces the WIP's `0 if complete else 3`)

| Situation | `status` | exit |
|---|---|---|
| Any input refused: malformed, wrong lane, stale commit, disagreement, oversized, a plan the analysis cannot reconstruct | `evidence_error` (the output is still one JSON document with `errors`) | 2 |
| Verdict supplied, inventory complete, progress terminal agrees, observed `--command-exit` (if given) equals the verdict exit, coverage reverified. Verdict outcome `PASS` | `complete` | 0 |
| Same as above, but verdict outcome is not `PASS` (for example a screen FAIL with survivors) | `complete` | 1 |
| No verdict; interrupted, still running, or a pilot (`--candidates-file`); or the inventory is not exhausted | `incomplete` | 3 |

**Differences from the WIP:**
- On `evidence_error`, the WIP printed only to stderr and returned 2. P8 prints the JSON document with `status: "evidence_error"` and `errors: [{source, message}]`, bounded to 10 messages with `errors_truncated`. It also keeps the stderr line.
- **A pilot can never be `complete`.** When `--candidates-file` is given, `status` is at best `incomplete`, and the output carries `"qualifying": false`.

### Evidence modes (decision table)

| Inputs | Outcome source per candidate | Can be `complete`? |
|---|---|---|
| progress + verdict (± state-dir) | The verified verdict buckets, via `verify_text` as in the WIP's `_read_verified_verdict`. Progress events must agree with the verdict: disagreement is an `evidence_error`, as in the WIP. State records, if supplied, must agree for every candidate they contain; disagreement is an `evidence_error`. | yes |
| progress + state-dir, no verdict | Each state record is validated read-only (next section). For candidates that have both a record and an event in the latest run at the expected commit, they must agree. A candidate with only an event (no record) takes the event bucket and is marked `outcome_source: "progress"`. | no |
| progress only | The latest event per candidate across all runs at the expected commit. A candidate reported with two different buckets in two runs is listed under `reclassified: [{candidate_id, runs:[{run_id, bucket}]}]` and is not an error. The run with the higher `line_start` wins the count. | no |

**Read-only state-record validation** (no judge re-derivation; `_load_validated_state_record` needs a live judge and cannot be used here):
1. The filename matches `^[0-9a-f]{64}\.json$`.
2. The file is ≤ `MUTATION_STATE_RECORD_LIMIT`.
3. It is UTF-8 JSON and an object.
4. `schema_version == 1`.
5. `candidate_id` equals the filename stem.
6. The candidate is in the reconstructed plan.
7. `path`, `operator`, `source_sha256` and `replacement_sha256` equal the plan row.
8. `outcome_bucket ∈ MUTATION_BUCKETS`.
9. `_execution_from_state_record(record)` does not raise.
10. `judge_sha256` is a 64-hex string.

Any failure is an `evidence_error` naming the file. Non-record files, such as P9's `campaign-identity.json`, are ignored by name.

The output adds `state: {records: n, judge_sha256_counts: {<hex>: n, ...}}`. **More than one distinct `judge_sha256` does not raise an error**, because a mixed store is legitimate after a tree change. It sets `state.mixed_judge: true` and forces `status` to be no better than `incomplete`.

### Plan reconstruction without a verdict

- Keep the WIP's `_lane_plan`. When the lane's `judge.base_source == "request"` and there is no verdict, `--request-base` is required; otherwise refuse (`evidence_error`, "plan base cannot be reconstructed without --verdict or --request-base").
- B105 is `whole_target` and needs no base.

### Selection (pilot) and shard

- `--candidates-file` is read with P7's parser, one 64-hex ID per line, and P7's refusals apply (duplicates, empty file, unknown IDs). Call `mutation.candidate_selection_sha256(ids)`. **Import it; never duplicate it.**
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
- `execution_mode` is the raw string from the verified verdict or the validated record. Count modes generically (`execution_mode_counts: {mode: n}`), so v14's `witness-cold` and `ledger` need no code change.

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

**Otherwise:**

```json
"timing": {"diagnostic_only": true, "eta_reason": null, "sample_count": 57, "minimum_sample_count": 20,
  "eta": {"jobs": 3, "jobs_source": "progress", "pending": 3703,
          "p50_candidate_s": 11.2, "p90_candidate_s": 38.0,
          "remaining_seconds_p50": 13824.5, "remaining_seconds_p90": 46905.3},
  "excluded_candidate_counts": {"crashed": 0, "hung": 0, "budget_exceeded": 0, "resumed": 0}}
```

**Rules:**
- `jobs` comes from the latest `plan` event's `jobs` field if P4 or P7 emit it (`jobs_source: "progress"`). Otherwise it comes from the lane declaration (`"lane"`).
- Percentiles use the nearest-rank method on the sorted sample; `p90` is the value at rank `ceil(0.9 × n)`.
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
  "jobs": 3, "fixed_seconds_measured": 1180.4, "fixed_components": {"coverage_baseline": 548.3, "r2_baseline": 402.1, "other": 230.0},
  "wall_seconds_p50": 14846.7, "wall_seconds_p90": 45180.4, "wall_basis": "killed"}
```

**How it is computed:**
1. For every candidate in the **full plan** (not only the selection), take the cost of its stratum:
   - the stratum `(path, operator)` if that has ≥ 20 samples;
   - else `(operator)` if that has ≥ 20;
   - else `all`, if the whole basis has ≥ 20;
   - otherwise the basis is `null`, with `reason: "insufficient_sample"`.
2. For each basis, sum the p50 and the p90 costs.
3. `wall = fixed + serial / jobs`. The `killed` basis is the one used in `wall_*`, because the qualifying run is all-kills (plan §2 item 2).
4. `fixed_seconds_measured` sums the latest run's `command_finished` durations (`ended - started`) whose `phase` names a baseline, plus the `r2_baseline_s` field of the `plan` event when present (plan D6/A7). R3 is not run in a pilot, so its absence is listed as `fixed_components_missing: ["r3"]`.

**Hard rules:**
- The numbers in these examples are illustrative only.
- No test may assert a predicted projection value (see §3b.F). Tests assert the arithmetic on synthetic samples.

### Output schema

- Update `src/assay/schemas/analysis-campaign.schema.json` (`"schema_version": {"const": 1}`, `additionalProperties: false` throughout) for every field above.
- It is packaged by the existing `schemas/*.json` package-data (`pyproject.toml:77`); no pyproject change is needed.
- **One valid example:** the JSON output of the complete-campaign fixture.
- **Two invalid examples, which must fail schema validation:**
  - `timing.eta` present while `sample_count < 20`. Encode this as an `if`/`then` on `sample_count`, or leave it to the model check if the schema cannot express it; the model check must raise either way.
  - `status: "complete"` with `qualifying: false`.

### Topology

- **All paths are explicit inputs.** Nothing is inferred from `.assay/`.
- The worktree's HEAD must equal `--expected-commit` (the WIP's `analysis._identity`).
- `--file` must be bound to that commit (the WIP's `_bind_lane_file`).
- State-dir, progress and verdict paths may lie outside the worktree.

### Degrees of freedom

- Private helper names and internal decomposition inside `campaign.py`.
- The text-format layout, provided it prints: status, outcome counts, adverse totals, `next_offset`, and ETA/projection marked "diagnostic".

## Work

1. Port the four WIP artifacts listed above. Record their pre-port sha256 values. Commit as `feat(assay): port campaign analysis WIP (B108 phase 1)`, unchanged apart from the module docstring's backlog ID.
2. Add `src/assay/campaign.py` to **both** `judge.targets` lists in `assay.toml`, in sorted position. Confirm `tests/test_self_lane.py` is green.
3. Replace the exit mapping and emit `evidence_error` documents (status/exit table).
4. Make `--verdict` optional and implement the three evidence modes, including read-only state-record validation and `reclassified`.
5. Implement plan reconstruction without a verdict (`--request-base` rule).
6. Extend candidate rows, the `adverse` lists and `execution_mode_counts`. Tolerate absent P0/v14 fields as `null`.
7. Implement the ETA sample rule (`MIN_ETA_SAMPLE = 20`, nearest-rank percentiles).
8. Implement `--project`: hierarchical strata, two bases, fixed components.
9. **After P7 is merged:** add `--candidates-file`, `selection_sha256` checks, `qualifying: false` and the never-complete rule. If P7 has not landed when you reach this step, write `BLOCKED: P8 step 9 waits for P7` to the LOG, finish steps 10-12 for everything else, and commit.
10. Update the schema, the docs (see Docs sync) and CHANGES.
11. **v14 coordination note, for the controller and not implemented here:** when `assay-b110-v14` merges into the integration line, its merge must run `tests/test_campaign.py`, including against a v14 fixture with `witness-cold`, `evidence` and `ledger` entries. If P8 merges after v14, add those fixtures here.
12. Run the focused tests, then the gates. Write the report.

## Oracles

Each oracle lists its observable, its negative, and the test that checks it. All go in `tests/test_campaign.py`, using fast synthetic fixtures built with the WIP helpers `_repository`, `_write_progress` and `_install_plan`, and `tests/fixtures/verdicts/*.json` as verdict seeds.

| # | Oracle | Observable | Negative (the wrong implementation it catches) |
|---|---|---|---|
| O1 | A complete campaign with verdict PASS exits 0. The same campaign with a FAIL verdict exits 1, `status: complete`. | The exit code and `status` | The WIP's "complete → 0" regardless of outcome |
| O2 | Appended `--resume` runs: `completed_total` equals the unique resolved candidates, and `runs[]` keeps per-run counts. | Three runs where run 2 resumes 5 and executes 3 | Summing candidate events across runs |
| O3 | Without a verdict the result is never `complete`, even when every candidate event is present. | `status: incomplete`, exit 3 | Treating event exhaustion as completion |
| O4 | A state record that disagrees with the verdict bucket, and a record whose `path` differs from the plan row, each give `evidence_error`, exit 2, with the filename named. | `errors[].message` | Silently preferring one source |
| O5 | Two distinct `judge_sha256` values in the state dir give `state.mixed_judge: true`, and status is not `complete`. | Output fields | Collapsing to one judge |
| O6 | Progress-only mode: the same candidate is `survived` in run 1 and `killed` in run 2. It is listed under `reclassified` and counted once, as killed. | `reclassified`, `outcomes` | Double counting, or an error |
| O7 | ETA: 19 samples → `eta: null`, `eta_reason: "insufficient_sample"`. 20 samples → nearest-rank p50/p90 exactly as hand-computed on the fixture's durations. | Values | Mean instead of rank; the WIP's threshold of 5; `eta` shown with too few samples |
| O8 | Changing every `elapsed_seconds` and `emitted_at` (a slower-host replay) changes the `timing`/`projection` numbers but **not** `status`, `outcomes`, the exit code or the `adverse` membership. | Diff of the two outputs, restricted to those keys | Any time-dependent classification |
| O9 | Projection fallbacks: a fixture with 25 samples of one operator and 3 of another gives `fallback_counts` operator/all, exactly as specified. A basis with fewer than 20 samples overall is `null` with a reason. | Output | Projecting from 3 samples |
| O10 | The adverse list with `--limit 2` over 5 survivors shows 2, `matching_total: 5`, and `next_offset: 2`. | Output | Silent truncation |
| O11 | Pilot selection: a `candidates` event whose `selection_sha256` mismatches the supplied file, and a pilot progress stream with no `--candidates-file`, are each `evidence_error`. A matching selection gives `qualifying: false` and `status: incomplete`, even when everything is killed. | Output | Pilot reported complete |
| O12 | A torn final progress record: with no verdict it is `incomplete` and `torn_final_record: true`. With a verdict it is `evidence_error`. | Output | The WIP always refuses, or always accepts |
| O13 | Every emitted document validates against `analysis-campaign.schema.json`, and the two invalid examples fail validation. | `Draft202012Validator` | Schema drift |
| O14 | A v14-shaped fixture, or an injected mode string via the no-verdict record path: `execution_mode_counts` includes `witness-cold` without a code change. | Output | A hard-coded mode list |

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
- **DESIGN-GUIDE (WHY):** a subsection after the `analyze report` rationale (~:3372-3420) covering:
  - explicit inputs only;
  - why the result is never `complete` without a verdict, or for a pilot;
  - the exit mapping and how it aligns with A-460;
  - the ETA sample rule and the projection strata;
  - that A-464 makes both of them diagnostic;
  - the port provenance (one sentence).
- **CONSUMERS (HOW):** after `### Take one gate snapshot with assay analyze report (B100)` (~:3992), add two pasteable workflows:
  - analyzing a screen verdict and listing its survivors;
  - a pilot projection with `--candidates-file --project`.
- **CHANGES.md:** `## [Unreleased]` → `### Added` bullet.
- **Backlog:** tick B108 acceptance boxes 1-4 and add a phase note to B108's status line. **Do not** tick box 5.

## Scope / forbid

**Touch:**
- `src/assay/campaign.py` (new);
- `src/assay/analysis.py` (the hook only);
- `src/assay/schemas/analysis-campaign.schema.json` (new);
- `tests/test_campaign.py` (new);
- `assay.toml` (the two target lists only);
- README, DESIGN-GUIDE, CONSUMERS, CHANGES;
- the B108 entry in `nyxloom-trove/4-backlog.md`;
- your report.

**Forbid:**
- any change to verdict, schema, runner, mutation or liveness behavior;
- `run-gate-project/`;
- the WIP worktree itself (read only);
- the WIP's backlog/decision diffs;
- run-gate auto-closeout (phase 2);
- any new `ReasonCode`.

**Needing more than two files outside this list is a BLOCKED trigger.**

## Gate

1. Focused tests, serially: `nice -n 19 ionice -c3 python -m pytest tests/test_campaign.py tests/test_analysis.py tests/test_self_lane.py -q -p no:cacheprovider`.
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
- P7's `candidate_selection_sha256` helper does not exist or has a different contract (step 9 only).
- The ported WIP cannot reach 100% branch coverage without a pragma.
- The planner-sharing approach (`cli._cmd_plan`) cannot reconstruct the plan for a lane without a verdict.

## Report

Write `nyxloom-trove/reports/assay-B110-P8-REPORT.md` with:
- the ported-file sha256 values;
- the port decision;
- a traceability table (`work | owner | oracle | test | controlled break`) with actual test names and failure counts, red-first;
- the gate verdicts, read separately;
- residuals.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`
