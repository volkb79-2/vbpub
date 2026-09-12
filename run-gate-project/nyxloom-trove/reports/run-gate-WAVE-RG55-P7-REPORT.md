# run-gate-WAVE-RG55-P7 — assay B091 — REPORT

Implementer records for package P7 (assay-liveness worktree/branch), RG-55
wave, design D-17/D-23. One section per deliverable (A1-A6), each with the
B091 oracle it satisfies mapped to the tests that prove it, and evidence for
any mutation-check transcript run against it.

## A1 — `budget_per_candidate = "auto"` default

**Contract item satisfied:** B091 contract item 1 — `judge.mutation.
budget_per_candidate` accepts `"auto"` (the default when unset): `max(3 x
measured baseline wall time, baseline + 60s)`, printed in the plan line and
recorded as `budget_per_candidate_derived_s` in the verdict; `"none"`
disables with a WARN; explicit durations keep working.

**Oracle → test mapping:**

| Oracle | Test(s) |
| --- | --- |
| Omitted `budget_per_candidate` derives `max(3x baseline, baseline+60s)` from a REAL measured baseline, not a guess | `tests/test_mutation_progress_budget_plan.py::test_run_mutation_auto_budget_is_derived_and_drives_enforcement`, `::test_auto_budget_per_candidate_seconds_formula`, `::test_baseline_wall_seconds_reads_started_and_ended` |
| The derived value is printed in the `plan` progress event (new event, first record of the mutation sweep) | `tests/test_mutation_progress_budget_plan.py::test_run_mutation_auto_budget_is_derived_and_drives_enforcement` (asserts `plan_event["derived"] is True`, `budget_per_candidate_s == expected`), `::test_plan_event_reports_none_derived_false_when_budget_per_candidate_is_unset` |
| The derived value is recorded in the verdict as `judgment.r2.budget_per_candidate_derived_s`, additive, absent for explicit/`"none"` | `tests/test_runner_run_lane_r2.py::test_judgment_r2_records_the_lanes_own_declared_policy_verbatim` (extended), `::test_judgment_r2_explicit_budget_per_candidate_is_declared_not_derived`, `tests/test_verdict_judgment.py::test_judgment_r2_budget_per_candidate_derived_s_round_trips_when_present`, `::test_judgment_r2_refuses_a_malformed_budget_per_candidate_derived_s`, `::test_judgment_r2_forbids_budget_per_candidate_derived_s_under_ingested` |
| `"none"` disables with a WARN, and still genuinely runs unbounded, with or without a `diagnostics` stream to print the WARN to | `tests/test_runner_run_lane_r2.py::test_judgment_r2_budget_per_candidate_none_warns_and_derives_nothing`, `::test_judgment_r2_budget_per_candidate_none_without_diagnostics_still_runs_unbounded` (regression test for the self-caught bug below) |
| A real end-to-end `assay run` (installed CLI, real subprocess) with no `budget_per_candidate` declared records a real positive `judgment.r2.budget_per_candidate_derived_s` | `tests/test_cli_run.py::test_run_evaluates_a_real_r2_pass_end_to_end` (pre-existing test, extended) |
| Explicit durations unchanged | `tests/test_config_unbounded_budget.py` (existing explicit-duration cases untouched), `tests/test_runner_run_lane_r2.py::test_judgment_r2_explicit_budget_per_candidate_is_declared_not_derived` |
| `budget = "unbounded"` admission: an omitted/`"auto"` `budget_per_candidate` now satisfies "every unit is bounded"; explicit `"none"` still refuses | `tests/test_config_unbounded_budget.py::test_a_native_R2_lane_without_budget_per_candidate_now_loads`, `::test_a_native_R2_lane_with_budget_per_candidate_none_still_names_the_missing_bound`, `::test_an_R3_lane_without_budget_per_attempt_names_the_missing_bound` (updated) |
| `assay plan`'s estimate never crashes on `"auto"`/`"none"`/omitted, keeps the 60s/candidate fallback | `tests/test_mutation_progress_budget_plan.py::test_plan_estimates_with_the_60s_fallback_for_every_non_duration_spelling` |
| `runner._declared_budget_per_candidate_seconds` (the `run` header's own field) treats auto/none as honestly unknown pre-baseline | `tests/test_mutation_progress_budget_plan.py::test_declared_budget_per_candidate_seconds_treats_auto_and_none_as_unknown` |
| `run_mutation`'s new `budget_per_candidate_auto` kwarg is mutually exclusive with an explicit `budget_per_candidate_seconds`, and type-checked | `tests/test_mutation_progress_budget_plan.py::test_run_mutation_refuses_auto_and_an_explicit_seconds_together`, `::test_run_mutation_refuses_a_non_boolean_auto_flag` |

**Files touched:** `src/assay/config.py`, `src/assay/mutation.py`,
`src/assay/verdict.py`, `src/assay/runner.py`, `src/assay/cli.py`,
`src/assay/schemas/verdict.schema.json`, `docs/CONSUMERS.md`, `CHANGES.md`,
`nyxloom-trove/4-backlog.md` (B091 progress note); tests as tabulated above.

**Design decisions made without stopping (RW-9):**
- The auto-derivation formula is computed ONCE, inside `assay.mutation.
  run_mutation` (the only place both the measured baseline and the
  per-candidate enforcement live together), and threaded back to the
  caller via `Mutation.budget_per_candidate_derived_s` (an internal,
  non-wire carrier field) rather than recomputed a second time in
  `runner._build_judgment_r2` — B088's own "two independent derivations is
  how a reader and a writer drift apart" reasoning applied here.
- `budget_per_candidate_derived_s` is placed on `judgment.r2` (a POLICY
  fact, alongside `jobs`/`max_mutants`/`operators`), not on the `mutation`
  RESULT payload — consistent with how this codebase already separates
  "what was declared/derived" from "what happened".
- `_refuse_unbounded_without_unit_bounds` (`config.py`, B067's `budget =
  "unbounded"` admission rule) is loosened: an omitted/`"auto"`
  `budget_per_candidate` now satisfies "every unit of the lane's work
  carries its own bound" (a real, derived bound, just not a number yet);
  only the explicit `"none"` still trips the refusal. This is a genuine,
  intentional behavior change from pre-B091 (an omitted key on an
  unbounded lane used to be refused at load; it now loads) and is called
  out explicitly in `CHANGES.md` and the docstring.
- The `"none"` WARN prints to the `diagnostics` stream using the existing
  `announce_refusal`-adjacent convention: `diagnostics is None` means the
  caller asked for no diagnosis, never a silent fallback to `sys.stderr`.

**A real bug found and fixed by self-review before this landed:** the first
cut folded "should I print the WARN" and "is this declaration `'none'` at
all" into ONE `if diagnostics is not None and declared == 'none': ...` /
`elif not budget_per_candidate_auto: parse_duration(declared)`. A caller
passing `diagnostics=None` (the ordinary library default, e.g. `runner.
run_lane`'s own default) with `budget_per_candidate = "none"` fell straight
through the `if` into the `elif` and crashed calling `parse_duration("none")`
— exactly the shape `run_lane`'s own callers hit whenever they don't pass a
`diagnostics=` stream. Caught by re-reading the diff before committing, not
by a failing test (none of the tests written first happened to pass
`diagnostics=None` with `"none"` declared) — fixed by splitting "is this
none" into its own branch, independent of whether there is anywhere to WARN,
and a regression test (`test_judgment_r2_budget_per_candidate_none_without_
diagnostics_still_runs_unbounded`) was added specifically for the
`diagnostics=None` case a reviewer should re-probe.

**Coverage self-check (targeted, not the full gate):** `coverage run
--branch --source=assay -m pytest tests/test_config_unbounded_budget.py
tests/test_mutation_progress_budget_plan.py tests/test_runner_run_lane_r2.py
tests/test_verdict_judgment.py tests/test_verdict_mutation_payload.py` (264
passed) then diffed against `git diff HEAD -- <the 5 changed src files>`:
**0 missing lines, 0 missing branches among changed/added lines** in all five
files. This is a self-check ahead of the real gate (below), not a
substitute for it.
