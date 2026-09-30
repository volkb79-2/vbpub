# W9 LOG (B108 phase 1, `assay analyze campaign`)

Base: `40317e29` (W8 reviewed head). Branch `wave-a-w9-campaign`. STATUS: checkpoint 3 after step 3a (`c71bd31b`); see `W9-CONTINUATION.md` for the remaining P8 behaviour steps.

## Draft sha256 (pre-edit, `reports/wave-a/b108-draft-20260927/`)
```
98b891d0dbb91f6ba7f1be24c4959d439202135c14f2d644cc3cafc526a62065  campaign.py
f93556bf906011a7ba40c23ed1203887d420b8d0364c0d708a98cd019a3f10f2  test_campaign.py
ae17a52a07ded8092fd0304890a65c9ce41eed2a6f723db50a2ad2a4a424ff9b  analysis-campaign.schema.json
eb5585414886feba6b05668a708dae12c53907bd5e713d38238abc6e853239cb  tracked-changes.patch
```

## Commits
| step | commit | content |
|---|---|---|
| J | `814494ac` | J1-J5: `_discover_plan_jobs`/`_PlanDiscovery`, `_plan_rows_from_jobs`, `PlanRow`, `plan_jobs`, `candidate_identity_fields`, four public aliases, `candidates`-event `judge_sha256`; O16a, O20 tests |
| port | `e251f6ef` | draft ported (edits 1-9); analysis 290 passed; deviations: `ALLOWED_JUDGE_MODULES` +6 modules; draft test :251 inversion deferred to step 3 (needs status row 3); dead code removed from `_run_summary` |
| 3a | `c71bd31b` | exit mapping 0/1/3, closed 7-key `evidence_error` document on stdout (+ stderr line, exit 2), `qualifying: true`, schema `oneOf` document/evidenceError; analysis 293 passed. The :251 test now expects exit 1 (BUDGET_EXCEEDED verdict, still `complete`) until step d inverts it |
| Q1 | `94e1f616` | CD51 Q1: `errors[].source` is the refused input (`_staged` decorator + `stage.name` markers in `campaign()`); `test_cd51_an_error_source_names_the_refused_input` pins 7 vocabulary words (all but `state`) |
| 3b-3f code | `70ab9239` | optional `--verdict`; `--state-dir`, `--request-base`, `--project`/`--project-jobs`; section C identity check; `EvidenceErrors`; CD40-ordered `_reconcile_state`; 20-key rows, `adverse`, `unresolved`, `complete_blockers`, `reclassified`, `campaign.selected_total`; `MIN_ETA_SAMPLE = 20` ETA object; pure `project()`; closed schema (`analysis-campaign.schema.json` rewritten); analysis/tests 300 passed. Deviation: sub-steps b-f went in ONE code commit (the closed document shape and the schema are shared, so a partial state cannot validate). Oracle tests for O2-O10, O12-O14a, O17-O19, O22-O26 are NOT written yet |

## QUESTIONS (numbered; each took the conservative reading)
1. (SUPERSEDED by CD51 Q1: `errors[].source` is the refused input from a fixed vocabulary.)
2. (CLOSED by CD51 Q2: `reclassified` item is `{candidate_id, source, runs: [{run_id, bucket}], state_bucket}`.)
3. With an UNKNOWN current judge (C25 yields none) no record counts; in-plan, in-selection records that do not pair with a same-bucket latest-run event are reported as `state.unreconciled` (they block `complete`). Paired records only enrich rows.
4. The v14 `evidence` object's key names are not defined before v14: `started_count` reads `evidence.started_count` (non-negative int) and `evidence_command` reads `evidence.command` (str); anything else is `null`.
5. Blocker `terminal_disagrees` is also raised in verdict mode when the latest run has NO terminal event (a present-but-different terminal stays an `evidence_error`).
6. No verdict plus `--coverage` on an R1 lane is an `evidence_error` (the artifact cannot be re-verified without a verdict R1 claim). No verdict plus R1 lane without `--coverage`: `not_supplied` (blocks complete).
7. `campaign.selected_total` is added (P8 "Counts"; CD39 names only `execution_mode_counts` inside `campaign`). `unresolved.candidates` is a sorted list of candidate id strings capped at `--limit`.
8. "Never-started" `budget_exceeded` (status row 3) is read literally: no `candidate` event in the latest run, even when a counted state record exists for it.

9. Never-started `budget_exceeded` candidates (status row 3): `outcomes`, `adverse` and `candidate_details` keep the verdict's bucket for them, but `completed_total` excludes them and `pending_total` includes them, so `no_remaining_work` is never claimed for a timed-out campaign.
10. `projection.bases.<basis>` with fewer than 20 samples is an object whose `fallback_counts`, `serial_seconds_p50/p90` are `null` and `reason` is `"insufficient_sample"` (a bare `null` cannot carry the reason); a basis that computed has `reason: null`. `wall_seconds_*` are `null` when the `killed` basis is insufficient.
11. Stage tags for CD51 Q1: root/`--expected-commit`/scalar checks and the `--command-exit` mismatch -> `arguments`; lane file/lane lookup -> `lane`; verdict read/verify/policy/inventory checks (including verdict-vs-plan inventory) -> `verdict`; plan build, plan-row identity -> `plan`; per-run stream checks (including progress-vs-verdict per-candidate disagreement) -> `progress`; latest-run-vs-verdict scope/terminal checks, `--log` -> `input`; state dir/records -> `state`; `--coverage` -> `coverage`.
12. (SUPERSEDED by CD55 Q12: `--command-exit` without `--verdict` is an `evidence_error`, `source: "arguments"`, message `--command-exit requires --verdict`; `command_exit_not_observed` is present exactly when `--command-exit` is absent.)
13. `projection.fixed_components.other` subtracts a missing coverage/r2 source as 0 (the missing source stays listed in `fixed_components_missing`); every duration is clamped at >= 0.
14. Without a verdict, a candidate event outside the latest run's selection is refused; the message is now the same for verdict mode (`latest progress run includes candidates outside its selected scope`).
15. Candidate-event `cpu_seconds`, `peak_rss_bytes`, `phase_seconds`, `startup_seconds` that break W8's shapes are refused as a progress `evidence_error` instead of being copied verbatim.
16. `--project`/`--project-jobs` pairing is enforced in `run_campaign_command` with `parser.error` (SystemExit 2, argparse usage text), the `--project-jobs` range by an argparse `type`.
17. The hung-evidence fallback to a state record (row `liveness_*`) reads only current-judge, in-selection records (counted, unreconciled or unverified-hung); stale-judge records are never a source.
18. In verdict mode the ETA can say `no_remaining_work` while the status is `incomplete` (progress stream cut, verdict already resolves every candidate).

## Judge oracles (J step)
Positive: `tests/core/test_cli_plan_jobs.py` 11 passed; `tests/core/test_mutation_candidates_event_judge.py` 2 passed; O21 set (`test_b105_cli_boundaries`, `test_cli_plan_estimate_hint`, `test_mutation_judge_identity*`, `test_mutation_progress_budget_plan`, `test_b106_reuse_and_witness`, `test_cli_provenance_and_request_base`, `test_import_contracts`, `test_cli_run`) 360 passed, 1 skipped, all unmodified.

| oracle | test | controlled break (Edit, run red, Edit back, run green) | result |
|---|---|---|---|
| O20 | `test_o20_the_candidates_event_carries_the_judge_every_record_carries` | `**({"judge_sha256": judge} ...)` removed from the `candidates` event | red, reverted, green |
| O16a (vii) | `test_o16a_vii_allow_dirty_reaches_the_integrity_probe` | `allow_dirty=False` hard-coded in `_cmd_plan`'s discovery call | red, reverted, green |
| O16a (viii) | `test_o16a_viii_the_reuse_command_is_resolved_only_when_reuse_is_requested` | `resolve_reuse_command=reuse_source is None` (inverted) | red, reverted, green |

## Campaign oracle traceability (work | owner | oracle | test id | controlled break | failures)
Every break is an Edit on `campaign.py` (or the named file), observed red, reverted by Edit, observed green. Nothing broken is committed. Test ids are in `analysis/tests/test_analysis_campaign.py` unless named.

| work | owner | oracle | test id | controlled break | failures |
|---|---|---|---|---|---|
| CD55 Q12 | W9 | CD55 | `test_cd55_q12_command_exit_without_a_verdict_is_an_evidence_error` | the `--command-exit requires --verdict` guard disabled (`if False and ...`) | 1 failed |
