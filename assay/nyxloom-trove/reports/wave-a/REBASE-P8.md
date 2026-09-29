# REBASE-P8: delta for `b110/P8-campaign-analysis-core.md` (Wave A W9)

Superseded by `W9-campaign-analysis.md` (CD26).

The P8 brief targets `db85f747`. This note lists what changes after W3, W2 and W8 merge, and overrides the brief on conflict. Implementer: Sonnet.

## 1. Base and dependencies
- **Branch:** `wave-a-w9-p8`, from `assay-b110-landing`, after W2 **and** W8 (P0) have merged. P0 is a hard dependency, so run W8 first even though both are listed in stage 3.
- **P6 and P7 are now in the v14 wave** (plan §6):
  - **C29 flips.** P8 performs the single `_discover_plan_jobs` extraction itself, under P6 step 8's contract: byte-identical `assay plan` output, with the existing plan tests unmodified. Drop the trigger "P6 not on base"; P6 reuses P8's extraction later.
  - **Step 9 is deferred to v14.** This covers `--candidates-file`, `plan_sha256`, status row 2 and O11. Until then, a `candidates` event that carries `selection_sha256` is an `evidence_error` ("pilot selection unsupported before P7"). Every document carries `qualifying: true`.
- **P1 is superseded.** Drop C15 (dataclass-contract regeneration); that work moves to W10.

## 2. Paths (W2 layout)
- **Module:** `src/assay/campaign.py` becomes `analysis/src/assay_analysis/campaign.py`.
  - The 7-line hook goes into `assay_analysis/cli.py`, as a `build_parser()` subcommand plus a `cmd_analyze` branch.
  - The WIP's `analysis._json`, `_report_commit` and `_identity` move to `evidence.*`.
- **WIP:** the brief's relative path is wrong. The uncommitted draft (last edited 2026-09-27) was preserved on this branch under `nyxloom-trove/reports/wave-a/b108-draft-20260927/`:
  - `campaign.py` (1,295 lines, formerly `src/assay/campaign.py`);
  - `test_campaign.py` (354 lines);
  - `analysis-campaign.schema.json`;
  - `tracked-changes.patch` (its edits to `analysis.py`, the backlog and decisions);
  - `BASE_COMMIT`.

  It was preserved as a reference, not live code. W9 ports it into the analysis package. It came from `.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy/.worktrees/assay-b107-analysis-30eec294/`, which can be removed after this copy lands.
- **Tests:**
  - Name the campaign test file `analysis/tests/test_analysis_campaign.py`.
  - `tests/conftest.py` helpers and `_seed_pytest_mutation` (`test_b106_reuse_and_witness.py:661`) are not importable. Write local helpers in `analysis_support.py`, and seed verdicts from `JUDGE_VERDICT_FIXTURES`.
  - O16 (plan-row identity), O20 (the `candidates`-event `judge_sha256`) and the `candidate_identity_fields` refactor test `src/assay`. They belong in the judge tree, beside the cli and mutation tests.
- **Schema:** put `analysis-campaign.schema.json` beside the other analysis schemas, wherever W2's Q1 places them. By default that is `src/assay/schemas/`, which package-data already covers.

## 3. Lanes and gate
- **Work step 2 is reversed.** Do **not** add `campaign.py` to the B105 targets. Add it, sorted, to `[lanes.analysis].judge.targets`; W2's T3 checks that list against the discovered files.
- **The judge edits stay in B105 scope and must keep the 100% preflight floor:**
  - `cli.py`: `plan_jobs`, `_discover_plan_jobs`, the row keys;
  - `mutation.py`: `candidate_identity_fields`, the `judge_sha256` key.
- **Gate:** tester-unified (including `analysis-lane-passed`), then `self-qualification-preflight`, then B105 checker ACCEPTED. The plan §4 known red applies.
- **Focused tests:** `analysis/tests/`, plus these judge files, found by filename:
  - `test_self_lane.py`;
  - `test_b105_cli_boundaries.py`;
  - `test_mutation_judge_identity*.py`;
  - `test_mutation_progress_budget_plan.py`.

## 4. Private judge names (W2 T3, Q3)
The code needs these private names:
- `cli._resolve_declared_adapters` and `runner._resolve_declared_base` (from the WIP; `plan_jobs` replaces `cli._cmd_plan`);
- `mutation._execution_from_state_record` (from the brief);
- `mutation._valid_hung_resource_evidence` (from B107).

Default: add each name to `ALLOWED_PRIVATE_JUDGE_NAMES` with a one-line reason. If Q3 chooses public names instead, add public aliases in the judge (at most 4 lines, all in B105 scope).

## 5. Anchor moves
The B107 merge `7d7b0fb9` moved these anchors, and W2 and W8 shift `cli.py` again, so locate each one by content.

| Anchor | db85f747 | 5bbd916e |
|---|---|---|
| `judge` computed | ~2353-2366 | 2602-2615 |
| `resume` event | ~2423-2430 | 2668-2680 |
| `candidates` event (still 4 keys; C25 stands) | ~2445-2452 | 2693-2701 |
| `candidate` event / state record (event still first) | 2930 / 2966 | ~3188 / ~3222 |
| `_load_validated_state_record` | 1273 | 1515 |
| `_execution_from_state_record` | 1450 | 1696 |
| `select_mutation_shard` | 1500 | 1746 |
| runner `command_finished` / `baseline` / `direct` | 3090 / 3744 / 6344 | 3097 / 3751 / 6350 |

- **`mutation.py` rejudge code:** now at 2403-2418 and 2635-2660.
- **Unchanged:**
  - `mutation.py`: `PROGRESS_EVENTS` (846-870), `_progress_event` (998), `candidate_id` (1023), `MUTATION_STATE_RECORD_LIMIT` (206);
  - `cli.py`: `_cmd_plan` (1571), discovery (1648-1765), rows (1820-1828).
- **Analysis anchors:** now in `assay_analysis/evidence.py`, under the same names.
- **Docs:**
  - README: 26-72.
  - CONSUMERS: 4021, with the B100 subsection at 4030.
  - DESIGN-GUIDE: 3406. Put P8's subsection after W2's `### Package boundary`.

## 6. B107 hung evidence: P8 must read it
**Counting (check 10).** `_load_validated_state_record` now rejects a `hung` record unless its `liveness_resource_evidence` passes `_valid_hung_resource_evidence` (`mutation.py:1273`, called at ~1644), and resume re-runs that candidate. P8 must mirror this:
- Such a record is not counted. Report it as `state.unverified_hung: {count, sample_ids[≤10]}`.
- It is not an `evidence_error`: stores written before B107 are legitimate.
- If the candidate is in the selected set, the status stays `incomplete` (row 4).
- Call the judge's predicate; never copy it.
- Fixture: one pre-B107 `hung` record under the current judge gives `count == 1`, and that record is not counted.

**Display (Q-P8b).** The `candidate` event and the state record may carry `liveness_resource_evidence` (~3204 and ~3242); verdicts do not. The option is a `liveness_decision` field on hung adverse rows (`idle-hang`, `session-finish-hang` or null). Default: not added.

## 7. Carver questions
- **Q-P8a:** confirm the C29 flip, and annotate the P6 brief.
- **Q-P8b:** add `liveness_decision`?
- Also depends on W2's Q1 and Q3.
