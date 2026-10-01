# W9 - Campaign analysis (`assay analyze campaign`, B108 phase 1)

| Field | Value |
|---|---|
| Backlog | B108 phase 1 (B110-P8), Wave A stage 3. Phase 2 (run-gate auto-closeout) is out of scope. |
| Branch | `wave-a-w9-campaign`, cut from `assay-b110-landing` after W8 merges. It merges `--no-ff`, then the branch is deleted. |
| Depends on | W2 (analysis package), W8 (`assay plan` `commit`/`tree`, the P0 candidate fields), W3/W4 (layout) |
| Class / implementer | 2c / Sonnet; nothing is left open |
| Decisions | CD16, CD18, CD20, CD26, CD29, CD38 (amended), CD39, CD40, CD41, CD42; A-460, A-464, A-474, A-478; P8's C13, C14, C25, C26, C29 |

This brief replaces `REBASE-P8.md`.
- From `b110/P8-campaign-analysis-core.md` ("P8"), read **only** the subsections listed below. Its header, context, Work, scope, gate, BLOCKED and report sections are void.
- The draft to port is `nyxloom-trove/reports/wave-a/b108-draft-20260927/` (base `30eec294`). Read it; never merge or cherry-pick it.
- Anchors are at `f27fbdea`, relative to `assay/`. W2, W3 and W8 move them, so resolve by name.

## Context to read first
- `CARVER-DECISIONS.md`: CD16, CD18, CD20, CD26, CD29, CD38 (amended), CD39, CD40, CD41, CD42.
- `W2-analysis-package.md`: steps 1-3, 6, T3, T8.
- `W8-measurement.md`: items H and R.
- The draft: `campaign.py`, `test_campaign.py`, `analysis-campaign.schema.json`, and the `src/assay/analysis.py` hunk of `tracked-changes.patch`.
- Judge code:
  - `cli.py`: `_cmd_plan` :1571-1887; `_resolve_declared_adapters` :683; `__all__` :136.
  - `mutation.py`:
    - `__all__` :158;
    - `candidate_id` :1023;
    - `_valid_hung_resource_evidence` :1273, called at :1644;
    - `_execution_from_state_record` :1696;
    - `judge` :2602-2615;
    - `candidates` event :2694-2700.
  - `runner.py`: `_resolve_declared_base` :3540; `__all__` :160.
- **Imported from P8 (normative, except where overridden here).**
  - Packet subsections: "Port decision"; "Plan rows carry candidate identity inputs" (row keys and the O16 invariant only); "The `candidates` event carries the mutation judge"; "CLI"; "Status and exit mapping"; "Evidence modes"; "Read-only state-record shape validation"; "Store reconciliation"; "Plan reconstruction without a verdict"; "Per-candidate row"; "Counts"; "ETA"; "Stratified projection"; "Output schema"; "Topology"; "Degrees of freedom".
  - Oracles: O1-O10, O12, O13, O15-O20, and the first half of O14.

## Overrides of P8
1. **The pilot (P8 Work step 9) moves to the v14 wave** (CD20, F-P8-5).
   - Do not add `--candidates-file` (argparse refuses it, exit 2) or any `plan_sha256` use.
   - Status row 2 and O11 are removed.
   - A `candidates` event that carries `selection_sha256` is an `evidence_error` with the message `pilot selection unsupported before P7`.
   - Every non-`evidence_error` document carries `qualifying: true`. The `evidence_error` document keeps its closed 7-key shape.
   - `--project --project-jobs N` stays.
   - P8's selection rule is reduced to this: the shard assignment when a `shard` event exists, otherwise the whole plan.
2. **O14's second half and P8 step 11 move to v14.** Neither is done here.
3. **`phase_seconds` and `startup_seconds`** follow W8's shapes. `phase_seconds`: an object with exactly the 4 keys, each a number, **or `null`**. `startup_seconds`: a 2-key object whose values are a number or `null`, **or `null`**.
4. **No private judge name is used in analysis (CD18).** W2's `ALLOWED_PRIVATE_JUDGE_NAMES` stays `{}`.
5. **C15 (the dataclass contract) is W10's job.** Nothing is regenerated here.
6. **B105 targets are unchanged.** `campaign.py` goes into `[lanes.analysis]` only.

## Implementation packet (normative)

### J. Judge changes (B105 scope, 100% R1, no pragma)
**J1. `_discover_plan_jobs` (C29: this is its one extraction, and P6 reuses it).**

In `src/assay/cli.py`, directly above `_cmd_plan`:
```python
@dataclass(frozen=True, kw_only=True)
class _PlanDiscovery:
    commit: str
    tree: str
    jobs: Any                  # the FULL pre-shard tuple, or mutation.UNSUPPORTED unchanged
    worktree_integrity: Any
    reuse_command_plan: Any
    reuse_command_cwd: Path | None

def _discover_plan_jobs(lane_file: LaneFile, lane: Lane, *, adapter: LanguageAdapter,
                        base_declaration: str | None, operators: tuple[str, ...],
                        allow_dirty: bool, resolve_reuse_command: bool) -> _PlanDiscovery:
    """Single planner-jobs extraction (C29); P6 reuses it."""
```
- **The body** is `_cmd_plan`'s text verbatim, from `deadline = runner.LaneDeadline.start(` through `jobs = mutation.collect_mutation_sites(...)`, W8's `tree` line included. Two substitutions only:
  - `getattr(args, "allow_dirty", False)` becomes `allow_dirty`;
  - the reuse block's `if reuse_source is not None:` becomes `if resolve_reuse_command:`.
- **`_discover_plan_jobs` ends**, after both `with` blocks close, with `return _PlanDiscovery(commit=commit, tree=tree, jobs=jobs, worktree_integrity=worktree_integrity, reuse_command_plan=reuse_command_plan, reuse_command_cwd=reuse_command_cwd)`.
- **`_cmd_plan`** keeps everything up to and including the `--shard` parsing. It then calls `discovered = _discover_plan_jobs(lane_file, lane, adapter=adapter, base_declaration=base_declaration, operators=operators, allow_dirty=getattr(args, "allow_dirty", False), resolve_reuse_command=reuse_source is not None)` and reads `commit`, `tree`, `jobs`, `worktree_integrity`, `reuse_command_plan` and `reuse_command_cwd` from it. The rest is unchanged.
- **Imports.** `from dataclasses import replace` becomes `from dataclasses import dataclass, replace`, and the typing import gains `Literal` and `TypedDict`.
- **Callers.** There are exactly two: `_cmd_plan` and `plan_jobs`. P6 is later.

**J2. Plan rows (C13).**
- **`mutation.py`:** add `candidate_identity_fields(job: MutantJob) -> dict[str, object]`, returning exactly `{path, source_sha256, start_byte, end_byte, mutated_file_sha256, operator}` (the computation now inside `candidate_id`). Then `candidate_id(job)` becomes `return candidate_id_from_fields(**candidate_identity_fields(job))`. Append `"candidate_identity_fields"` to `__all__`.
- **`cli.py`:** add `_plan_rows_from_jobs(jobs) -> list[dict[str, Any]]`. Each row is today's 7-key dict (`cli.py:1821-1829`) plus `source_sha256` and `mutated_file_sha256`, taken from `mutation.candidate_identity_fields(job)`. `_cmd_plan` uses it and then adds `reuse` as today.
- **`PlanRow(TypedDict)`** has exactly those 9 keys.

**J3. `plan_jobs` (C13).**

`plan_jobs(lane_file: LaneFile, lane: Lane, *, request_base: str | None = None, allow_dirty: bool = False) -> list[PlanRow] | Literal["UNSUPPORTED"]`:
1. Raise `_cmd_plan`'s R2 refusal (same `LaneConfigError` message).
2. `adapter = _resolve_declared_adapters(lane)`; if it is `None`, raise `_cmd_plan`'s message.
3. `base_declaration = runner.resolve_base_declaration(lane, request_base)`.
4. `_discover_plan_jobs(..., operators=lane.judge.mutation.operators, allow_dirty=allow_dirty, resolve_reuse_command=False)`.
5. Return `mutation.UNSUPPORTED` unchanged, or `_plan_rows_from_jobs(discovered.jobs)`. That is the full plan, never sharded.

**J4. Public names (CD18).** Each new public name in the table below is one alias line, `public = _private`, placed directly after the function it names.
- `cli.__all__` becomes `["build_parser", "main", "plan_jobs", "resolve_declared_adapters"]`.
- Append the runner and mutation aliases to their module's `__all__`, under `# CD18: public for assay_analysis`.

Every private name the draft (or P8) uses, with its replacement:

| Draft or P8 private use | Replacement |
|---|---|
| `analysis._json`, `analysis._report_commit`, `analysis._identity` | `evidence._json` / `_report_commit` / `_identity` (sibling analysis module; W2 T3 rule 5) |
| `assay_cli._cmd_plan` + the hand-built `Namespace` | `assay_cli.plan_jobs(...)` |
| `assay_cli._resolve_declared_adapters` (:376) | `assay_cli.resolve_declared_adapters` |
| `runner._resolve_declared_base` (:260) | `runner.resolve_declared_base` |
| `mutation._execution_from_state_record` (P8 check 8) | `mutation.execution_from_state_record` |
| `mutation._valid_hung_resource_evidence` (B107) | `mutation.valid_hung_resource_evidence` |

**J5. C25.** The `candidates` event (:2694-2700) gains `**({"judge_sha256": judge} if judge is not None else {})` after `commit`.

### A. Port into the analysis package
- **Modules.**
  - `analysis/src/assay_analysis/campaign.py`: the draft `campaign.py`, including the pure `project()`.
  - Tests: `analysis/tests/test_analysis_campaign.py`.
  - Schema: `src/assay/schemas/analysis-campaign.schema.json` (CD16; package-data already covers it).
  - Record the four draft files' sha256 values in the LOG before editing.
- **Port edits (exact):**
  1. Imports:
     - `from . import analysis, git` becomes `from assay import git, mutation` plus `from assay_analysis import evidence`;
     - every `analysis.X` becomes `evidence.X`;
     - `from . import coverage as coverage_api` becomes `from assay import coverage as coverage_api`;
     - `.config`, `.errors`, `.mutation`, `.safeio` and `.verdict` become `assay.<same>`;
     - the function-level `from . import cli as assay_cli[, runner]` becomes `from assay import cli as assay_cli[, runner]`;
     - add `from assay.candidate_identity import candidate_id_from_fields`.
  2. Delete `from statistics import median` and its uses (:1101, :1116). The ETA uses the nearest rank.
  3. `_lane_plan` keeps its signature. It computes `declared_request` as today and calls `assay_cli.plan_jobs(lane_file, lane, request_base=declared_request)`.
     - For `"UNSUPPORTED"` it returns `{"status": "unsupported"}`.
     - `campaign()` raises `ValueError("lane has no mutation plan")` right after `_lane_plan` when `plan["status"] == "unsupported"`.
     - Otherwise it returns `{"status": "ok", "candidates": rows, "_resolved_base": runner.resolve_declared_base(lane_file.project_root, runner.resolve_base_declaration(lane, declared_request))}`.
     - Delete the `Namespace` and the `io` import if it is unused.
  4. The hard-coded modes (:1032-1035) become generic counting.
  5. Call `mutation.valid_hung_resource_evidence(...)` and `mutation.execution_from_state_record(...)` as module attributes at call time. Never import or copy them by name.
  6. **The hook in `assay_analysis/cli.py`.**
     - `from assay_analysis import campaign, evidence, plan_estimate`.
     - `build_parser()` calls `campaign.build_campaign_parser(commands)` after the `plan-estimate` subparser.
     - `cmd_analyze` starts, before its `try`, with `if args.analysis_command == "campaign": return campaign.run_campaign_command(args, stdout=stdout, stderr=stderr)`.
  7. **Package wiring.**
     - `__init__.__all__ = ["campaign", "cli", "evidence", "plan_estimate"]`.
     - `[lanes.analysis]` `judge.targets` gains `analysis/src/assay_analysis/campaign.py`, sorted.
     - W2's T8 lists gain `campaign.py` and the schema when they pin exactly.
  8. **The test module.**
     - `from assay_analysis import campaign as campaign_api`.
     - Verdict seeds come from `JUDGE_VERDICT_FIXTURES`.
     - The docstring says B108.
     - `_plan_rows` copies `source_sha256` and `mutated_file_sha256` from the verdict outcomes.
     - Invert the draft test at :251 (never-started `budget_exceeded` leftovers are `incomplete`).
     - Write the helpers in the module itself, using `subprocess` git as the draft's `_repository` does. Import nothing from `tests/`.
  9. **The docs test.** Add a `campaign` line, with `lane`, `--file`, `--expected-commit` and `--progress`, inside the README `<!-- assay-analysis-example -->` block (:52-58).

### B. B107 hung evidence (CD20)
- **Row keys.** Every row in `candidate_details` and in each `adverse` list gains two keys; the schema declares both and keeps `additionalProperties: false`.
  - `liveness_decision`: `evidence["decision"]` when it is a str, else `null`.
  - `liveness_evidence_status`: `"absent"` when there is no evidence object; otherwise `"valid"` or `"invalid"`, according to `mutation.valid_hung_resource_evidence(evidence)`.

  Both keys are `null` for rows whose outcome is not `hung`.
- **Evidence source, first hit wins:**
  1. the latest-run `candidate` event's `liveness_resource_evidence`;
  2. that candidate's state record's `liveness_resource_evidence`, whether counted or not;
  3. otherwise absent.
- **Counting mirrors the judge.** The judge re-validates hung evidence only when it loads a state record (`mutation.py:1641-1647`: judge check first, then hung evidence). The analysis does the same:
  - A record at the current judge, in selection, with `outcome_bucket == "hung"` and failing the predicate is **not counted**. It goes to `state.unverified_hung: {count, sample_ids}`, with `sample_ids` sorted and at most 10. Such records are removed **before** the eventless-record reconciliation, because the judge's resume rejects them, so they are never part of `resume.resumed_total`.
  - A record at another judge stays in `stale_judge`.
  - A selected candidate left unresolved this way keeps the status `incomplete` (row 4), and `complete_blockers` gains `"unverified_hung_records"`.
  - A verified verdict's `hung` bucket and latest-run events are never re-judged: they count, and their rows only display the status.
  - It is never an `evidence_error`, because pre-B107 stores are legitimate.

**Order, in every evidence mode:**
1. Shape validation.
2. The C25 current-judge rule over all shape-valid records.
3. Unverified-hung removal: the record leaves the store view.
4. Only then P8's O4 verdict comparison and reconciliation steps 1-5.

**Consequences:**
- A removed record is never an `evidence_error` and never `reclassified`.
- `unverified_hung` lists it whether or not its candidate is resolved elsewhere.
- The candidate still counts through its verdict bucket or its latest-run event.
- `unverified_hung_records` enters `complete_blockers` only when a listed candidate has neither a verdict bucket nor a latest-run event.

### C. Plan-row identity check
Work step 3 implements it together with P8 step 4. In `campaign.py`, define `IDENTITY_KEYS = ("path", "source_sha256", "start_byte", "end_byte", "mutated_file_sha256", "operator")`. These are exactly the keyword parameters of `candidate_identity.candidate_id_from_fields` and the keys of J2's `candidate_identity_fields`. O16a asserts `set(IDENTITY_KEYS) == set(candidate_identity_fields(job))` for one job.

The check runs right after `_lane_plan` and before any progress or state is read. For every row:
- all six identity keys are present;
- both digests match `^[0-9a-f]{64}$`;
- `candidate_id_from_fields(**{k: row[k] for k in IDENTITY_KEYS}) == row["id"]`.

Otherwise → `evidence_error`, message `plan row {id}: identity inputs do not reproduce its id`.

### Output shape (closed)
The ported draft schema (`analysis-campaign.schema.json`) is edited to match this shape exactly: `verdict` and `evidence.verdict` nullable; the `coverage` not-judged form below; `timing` is P8's ETA object; `excluded_candidate_counts` has P8's four keys; the new top-level keys are added.
- **Non-error document.** Top-level keys are exactly the draft's 15 plus `qualifying` (`true`), `complete_blockers` (array, `[]` iff `status == "complete"`), `torn_final_record` (bool), `adverse`, `unresolved` (object or `null`), `reclassified` (array), `state` (object, or `null` without `--state-dir`) and `projection` (object or `null`). `execution_mode_counts` goes under `campaign` and replaces `freshly_witness_replayed` and `fully_executed`.
- **Without `--verdict`:**
  - `verdict` is `null`;
  - `evidence.verdict` is `null`;
  - `coverage` is the draft object with `status: "not_judged"`, `r1: null`, `r2_floor: null`, `artifact_status: "not_supplied"`, `branch_arc_detail_status: "unavailable_without_artifact"` and `missing_branch_arcs: null`.
- **`complete_blockers` vocabulary.** It is sorted, and each string is added when its row-4 or row-3 condition holds:
  - `no_verdict`
  - `command_exit_not_observed`
  - `inventory_not_exhausted`
  - `terminal_disagrees`
  - `coverage_not_reverified`
  - `state_unreconciled`
  - `unverified_hung_records`
  - `lane_timeout_or_unstarted`
- **`timing`** is exactly P8's ETA object: `diagnostic_only`, `eta_reason`, `sample_count`, `minimum_sample_count`, `measurement_window{first,last,run_id}`, `eta` and `excluded_candidate_counts{crashed,hung,budget_exceeded,resumed}`. The draft's other `timing` keys are deleted.
- A lane with no R1 coverage declaration always has `coverage.artifact_status == "not_applicable"`, which never blocks. Supplying `--coverage` for it is `evidence_error` (the draft's rule). `not_supplied` applies only to R1 lanes.

### Degrees of freedom
Private helper names and the decomposition inside `campaign.py`, plus the text-format layout within P8's rule. Everything else is fixed.

## Work
Use editor tools only. Commit per step, each with a green focused run.
1. **Judge.** J1-J5, with the judge tests (O16a, O20, O21).
2. **Port.** Copy the draft files to their new paths and make port edits 1-9. Commit as `feat(assay): port campaign analysis draft (B108 phase 1)`.
3. P8's behaviour, in P8 Work order:
   - step 3 (exit mapping; `evidence_error` documents);
   - step 4 (optional `--verdict`, the evidence modes, shape validation with check 8 via `mutation.execution_from_state_record`, store reconciliation), plus section C (the plan-row identity check) and section B's rule order;
   - step 5 (reconstruction);
   - step 6 (rows, `adverse`, `unresolved`, `execution_mode_counts`), plus B;
   - step 7 (ETA, `MIN_ETA_SAMPLE = 20`);
   - step 8 (`--project`).

   **3a.** Add `test_every_campaign_refusal_is_reachable`, parametrized over a literal table `(case_id, fixture_mutation, expected_message_fragment)`:
   - One row per `raise ValueError` in `campaign.py`.
   - Each row's mutation is applied to the complete-campaign fixture.
   - The LOG maps each raise line to its `case_id`.

   Measure coverage locally before the registered gate: `nice -n 19 ionice -c3 python -m pytest analysis/tests -q -p no:cacheprovider --cov=analysis/src/assay_analysis --cov-branch --cov-report=json:<tmp>`. Read the arcs with coverage's API, not the report's Missing column.

   A branch reachable only by a filesystem race (for example the draft's `fstat` mismatch at :90-94) is reached by monkeypatching `campaign.os.fstat`, not by a pragma.
4. **Schema:** edit the ported draft schema to the "Output shape (closed)" shape, with `additionalProperties: false` kept, then add:
   - every field P8 lists;
   - `state.unverified_hung`;
   - the two row keys;
   - the pilot branch removed except `qualifying`.
5. **Docs:** P8's "Docs sync" without the pilot workflow, re-anchored:
   - **README:** example 9, plus "read-only; estimates are diagnostic".
   - **DESIGN-GUIDE:** a subsection after W2's `### Package boundary (A-478)` in `## Review evidence analysis` (:3406). Add the CD20 counting rule and why code 3 diverges from A-460.
   - **CONSUMERS:** after the B100 subsection (:4030), a screen/survivor workflow and a `--project` example. Also the plan-row keys, and `judge_sha256` in the progress table (:2649).
   - **CHANGES.** Name the additive W9 surfaces (CD38 amended): the `candidates` progress key `judge_sha256`; the plan-row keys `source_sha256` and `mutated_file_sha256`; the public `cli.plan_jobs` and `mutation.candidate_identity_fields`; the four public aliases; the `assay analyze campaign` subcommand.
   - **B108:** tick boxes 1, 2 and 4 only when O15, O18 and O19 are green.

## Oracles
- P8's O1-O10, O12, O13, O14 (first half), O15 and O17-O20 all apply to `analysis/tests/test_analysis_campaign.py`, with these overrides:
  - **O13** also validates the two new row keys and `state.unverified_hung`.
  - **O15 fixture.**
    - A subprocess-git repository with the `tests/core/test_cli_run.py:316` lane.
    - `src/mod.py` is `def f(x, y):\n    return x > 0 and y < 1\n` against a base where it is `return 0`.
    - `argv = ["/bin/sh","-c","grep -q 'x > 0' src/mod.py && grep -q 'y < 1' src/mod.py"]`, giving two candidates.
    - Every artifact is under `tmp_path / "ev"`, outside the repository.

    **Run 1:** `main(["run","package","--file",T,"--resume","--state-dir",S,"--progress",P,"--verdict-json",V1])` exits 0, and `S` holds exactly 2 `*.json`.

    **Run 2:** the same with `--rejudge <first plan row id>` and `--verdict-json V2` (it appends to P).

    **Analysis:** `campaign package --file T --expected-commit HEAD --progress P --verdict V2 --state-dir S --command-exit 0` without `--coverage`. Assert:
    - `status == "complete"`, exit 0, and `coverage.artifact_status == "not_applicable"`;
    - `campaign.completed_total == 2` and `campaign.rejudged_total == 1`;
    - `runs[0]` has 2 candidate events, and `runs[1]` has 1 candidate event with `resumed_total == 1`;
    - `state.judge_sha256_source == "candidates-event"` and `state.counted == 2`.

    The `--reuse-from` replay half is dropped from O15. Record it as a residual: it needs a pytest lane. The lane is planned by `cli.plan_jobs()` with no `_install_plan`. O19 covers coverage.
  - **O16** splits into O16a (judge) and O16b (analysis).
- Record each controlled break in the LOG, red then green.

| # | Observable | Negative (plausible wrong implementation) |
|---|---|---|
| O16a `tests/core/test_cli_plan_jobs.py` | (i) For every row of a real repo, `candidate_id_from_fields(**identity) == row["id"]` and the row has exactly 9 keys. (ii) An unsupported lane returns `"UNSUPPORTED"`. (iii) Pick `i` so that `len(select_mutation_shard(ids, index=i, count=2)) < len(ids)`, where `ids` are the unsharded row ids. Assert `len(_discover_plan_jobs(...).jobs) == len(ids)`, and assert that `plan --shard i/2`'s `candidate_count` equals that shorter length. (iv) A non-R2 lane, and no adapter (monkeypatch `cli._resolve_declared_adapters`), both raise. (v) The four aliases are the judge functions themselves (`is`). (vi) With a dirty, uncommitted edit and `allow_dirty=True`, `source_sha256 == hashlib.sha256(subprocess.check_output(["git","-C",repo,"show","HEAD:src/mod.py"])).hexdigest()`. (vii) Setup: a tracked non-lane file edited and uncommitted. `main(["plan", lane, "--file", T, "--allow-dirty"])` gives `payload["worktree_integrity"]["overridden_dirty_paths"] == [that path]`. Negative: a hard-coded `allow_dirty=False` raises `DIRTY_TREE`. (viii) On a sequential pytest lane (the `_seed_pytest_mutation` shape, no `-n`, clean tree), `main(["plan", lane, "--file", T, "--reuse-from", prior_v13])` gives `payload["reuse_from"]["sequential_pytest_supported"] is True`. Negative: an inverted or dropped `resolve_reuse_command` gives `False` | Rows without digests; a sharded `jobs`; digests recomputed from the worktree (passes (i) on a clean tree, fails (vi)); a copied function under the public name |
| O16b (analysis) | `_install_plan` with one row's `source_sha256` flipped gives the exact message `plan row {id}: identity inputs do not reproduce its id` and exit 2, with no state dir needed | Skipping missing or mismatched keys |
| O20 `tests/core/test_mutation_candidates_event_judge.py` | P8's O20 | P8's O20 |
| O21 plan unchanged | Every existing plan, `test_b105_cli_boundaries.py`, `test_cli_plan_estimate_hint.py` (W8 O-H1, which pins `commit`/`tree` and the hint) and `test_mutation_judge_identity*.py` test passes **unmodified** | The extraction reorders refusals, or drops `commit`/`tree`/the W8 hint |
| O22 hung counting (analysis) | Setup: no verdict; the latest run has `candidates.judge_sha256 = J` and `resume` with `resumed_total = 0`, `rejected_total = 1`; one hung record at J lacks `liveness_resource_evidence` (pre-B107) and has no event. Result: `state.unverified_hung.count == 1`, `state.unreconciled.count == 0`, the record is not counted, `incomplete`, blocker present. Reverse: the same record with a valid evidence object (copy `_evidence()` from `test_mutation_hung_evidence_persistence.py:47` and first assert that the predicate accepts it) plus `resumed_total = 1` is counted. **Input A (verdict mode):** the plan is `{a, b}` at C; `--state-dir S` holds `a` (judge J, `hung`, no `liveness_resource_evidence`, pre-B107) and `b` (judge J, `killed`); the latest run is a non-resume full run with no state root, so `candidates` has no `judge_sha256`; events are a `killed` and b `killed`; the verdict is PASS `killed:[a,b]`; `--command-exit 0`. Result: `complete`, exit 0, `unverified_hung.count == 1`, no blocker. **Input B (no verdict, event written before the record):** the latest `--resume` run has `resume{resumed_total:0, rejected_total:1}`, `candidates{judge_sha256:J}` and an event for a that is `hung` with valid evidence; record a on disk is the old invalid one. Result: a counted as `hung` with `outcome_source == "progress"`, `liveness_evidence_status == "valid"`, `unverified_hung.count == 1`, and `unverified_hung_records` not in `complete_blockers` | A copied predicate. Closed by monkeypatching `assay.mutation.valid_hung_resource_evidence` to `lambda value: True`: then `unverified_hung.count == 0` and `unreconciled.count == 1` |
| O23 rows (analysis) | A verdict-mode `hung` row whose event lacks evidence is counted as hung, with `liveness_evidence_status == "absent"` and `liveness_decision is None`. When the event says `idle-hang` and the record says `session-finish-hang`, the row shows `idle-hang`. A `killed` row has both keys `null` | Re-judging verdict rows (drops the hung count); reading the record before the event |
| O26 output shape (CD39) | A no-verdict document validates against the ported schema, and so does a verdict document | Deleting `null` from `verdict`'s type in the schema turns it red |
| O24 pilot deferred | `--candidates-file x` exits 2. A `candidates` event with `selection_sha256` gives `evidence_error` with the message. Every other document has `qualifying: true` | A half-ported pilot path |
| O25 boundary | W2's T3 stays green with an empty allowlist. A planted `assay_cli._resolve_declared_adapters` in `campaign.py` turns it red (locally, not committed) | An allowlist entry |

**§3b (pasted). An oracle must not contain any of the following.**
- **A (L20).** An assert after a `monotonic()+N` deadline or a `sleep(N)`, or on elapsed time or iteration counts. Wait on join/Event/queue, or remove the wait. A timeout is only a generous failsafe and never decides. A slow-host failure is a true red: fix the test; never widen the timeout or add CPU.
- **B.** An unrestored global (logging, `environ`, module attributes, singletons; PL7 §5). A monkeypatch on a `__getattr__` proxy; patch its owner (L19). A teardown that destroys instead of restoring. Use a fresh `tmp_path`. For a full-suite-only failure, ask what an earlier test left behind.
- **C.** A `pass` body, a "nothing raised" test, trivia (call counts, private attributes, logs) in place of the contract, or a weakened or deleted assertion.
- **D (L11).** A no-cover pragma; the token matches anywhere on a line. An excluded `except` body does not cover its clause. Restructure instead.
- **E.** Real network access, or `now()`/`time()` in an asserted value. Inject the boundary.
- **F.** Predicted coverage or mutation numbers, or missing lines read from a report (its Missing column hides arcs). Assert the policy and run the tool. No test asserts a predicted projection value (P8).
- **Check:** could the result flip on a slower host, in another worker, or in another order?

## Scope
- **Touch:**
  - judge: `src/assay/cli.py` (J1-J4), `src/assay/mutation.py` (J2, J4, J5 only), `src/assay/runner.py` (the J4 alias and its `__all__` entry only);
  - analysis: `analysis/src/assay_analysis/{__init__,cli,campaign}.py`, `src/assay/schemas/analysis-campaign.schema.json`, the `[lanes.analysis]` targets line;
  - tests: the named test files, T8's lists, and any test pinning those three `__all__` lists (additions only);
  - docs and records: the 4 docs, the B108 entry, `reports/wave-a/W9-LOG.md`.
- **Forbid:**
  - allowlist entries;
  - a second discovery extraction;
  - verdict, verify, liveness or classification changes beyond J;
  - `ReasonCode`, `run-gate-project/`, phase 2;
  - the draft's backlog and decisions hunks;
  - `--candidates-file`;
  - the B105 target lists.
- More than 2 files outside this list triggers BLOCKED.

## Gate
1. Serial: `nice -n 19 ionice -c3 python -m pytest analysis/tests tests/core/test_cli_plan_jobs.py tests/core/test_mutation_candidates_event_judge.py <the W3 paths of test_b105_cli_boundaries.py, test_cli_plan_estimate_hint.py, test_mutation_judge_identity*.py, test_mutation_progress_budget_plan.py> <the W3/W4 paths of test_b106_reuse_and_witness.py and test_cli_provenance_and_request_base.py> gate/tests/test_self_lane.py -q -p no:cacheprovider`.
2. `cd <worktree>/assay && python ./run-gate.py tester-unified > <log> 2>&1`. In a separate step, read `ASSAY_GATE_PHASE=analysis-lane-passed`, `ASSAY_GATE_CONTAINER_EXIT=0` and `ASSAY_REGISTERED_GATE_COMPLETE=1`. `ASSAY_GATE_INCONCLUSIVE=host busy` (exit 3, CD32) is not a result: rerun later.
3. Mandatory, afterwards: `python ./run-gate.py self-qualification-preflight`. Read `B105_VERIFIED_LANE=` separately. The judge edits must hold 100%. If the host forbids it, write BLOCKED.

No known red (CD29).

**Host-load rule** (paste into every agent prompt): the host is shared with a production game server; nice/ionice for everything; at most one gate container, and none while another session's gate runs (`docker ps` first); never the full `self-qualification` lane; remove containers by exact name only.

Use editor tools only; keep README, DESIGN-GUIDE and CONSUMERS in sync. Trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

## BLOCKED rule
If a named contract cannot be met, or a forbidden file is needed: STOP. Write `BLOCKED: <reason>` to `W9-LOG.md`, commit and exit; BLOCKED is success. Triggers:
- W8's `commit`/`tree` is absent from the base;
- O21 needs an existing test edited;
- `candidate_identity_fields` is not byte-identical;
- 100% coverage of `campaign.py` or the judge needs a pragma;
- T3 flags a name the port needs;
- a fixture needs counting while the C25 judge is `unknown`. Give its `candidates` event a `judge_sha256`, and never use `judge_provenance`.

## Review and report
- **Review:** a fresh session, never a fork. It adds one combined-axis attack, for example a sharded, resumed run whose shard contains a pre-B107 `hung` record, analyzed with and without a verdict.
- **`W9-LOG.md`:** the draft sha256 values, per-step commits, a traceability table (`work | owner | oracle | test id | controlled break | failures`, red first), the gate markers read separately, and residuals (the v14 items: pilot, O11, O14's second half).
- **Checkpoint:** ARM at ~120k tokens or ~60 calls; CUT only at a green commit.
