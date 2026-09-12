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


## A2 (session 2) — materialized pytest liveness plugin + candidate `os._exit` wrapper

**Contract item satisfied:** B091 contract item 3 (the python runner exits
via `os._exit(rc)` after the suite finishes, so a leaked non-daemon thread
cannot hang the candidate at interpreter shutdown -- the mutant then
SURVIVES and must be killed honestly). Partial progress toward contract item
2 (the plugin/events plumbing item 4's `hung` detection needs exists, but
the active monitoring loop and the `hung` bucket itself are NOT yet built --
see "What's still open" below).

**Design (RW-33, decided, not re-litigated):** one mechanism, two parts, for
native R2 python/pytest lanes only (`lane.judge.mutation is not None and not
is_ingested`, `adapter.name == "python"`, and the lane's own declared argv
literally invokes pytest -- otherwise liveness is off with one WARN naming
the rule, D-23's derived `budget_per_candidate` remains the only bound).
Part 1: `assay.liveness.materialize_liveness_plugin` writes a stdlib-only
pytest plugin into `<project>/.assay/liveness/assay_liveness_plugin.py`
(rewrite-only-if-content-differs); `assay.liveness.inject_liveness_plugin`
extends the shared `CommandPlan`'s `argv_declared` with `-p
assay_liveness_plugin` and prepends the liveness dir onto `PYTHONPATH` in
`env_effective`. Part 2: `assay.liveness.LivenessRunner`, a `ProcessRunner`
used ONLY at the R2 candidate call site inside `runner._run_prepared_lane`
(the baseline, one function-call earlier in the SAME function, keeps using
`process_runner` unmodified) -- v1 scope stamps
`ASSAY_LIVENESS_EVENTS`/`ASSAY_LIVENESS_EXIT=1` into the child env and
delegates to `inner`.

**Oracle -> test mapping:**

| Oracle | Test(s) |
| --- | --- |
| A lane whose argv does not literally invoke pytest gets liveness OFF, unchanged plan, one WARN naming the rule | `tests/test_liveness.py::test_inject_returns_plan_unchanged_when_argv_is_not_pytest`, `::test_inject_with_diagnostics_none_does_not_raise_and_stays_silent` |
| `argv_invokes_pytest`'s three matching shapes (bare `pytest`, path ending `/pytest`, adjacent `-m pytest`) and their negatives | `tests/test_liveness.py::test_argv_invokes_pytest_true_cases`, `::test_argv_invokes_pytest_false_cases` (parametrized, 6 cases each) |
| The plugin is materialized once and re-materialized only when its content differs (never rewritten when unchanged, even across an unreadable pre-existing file) | `tests/test_liveness.py::test_materialize_creates_dir_and_file_when_absent`, `::test_materialize_overwrites_when_content_differs`, `::test_materialize_skips_write_when_content_already_matches`, `::test_materialize_treats_an_unreadable_existing_file_as_a_rewrite` |
| Injection adds exactly `-p assay_liveness_plugin` to `argv_declared`, prepends (never replaces) an existing `PYTHONPATH`, leaves every other env key untouched, and leaves `argv_appended`/`allow_argv_append` byte-identical (the A-036 exception's own transparency requirement) | `tests/test_liveness.py::test_inject_adds_plugin_flag_and_pythonpath_when_no_existing_pythonpath`, `::test_inject_prepends_to_an_existing_pythonpath`, `::test_inject_preserves_argv_appended_and_recomputes_effective`, `::test_inject_other_env_keys_survive_untouched` |
| `LivenessRunner` stamps both liveness env vars, forwards argv/cwd/timeout unchanged, derives a stable per-candidate events path from `cwd` alone (same cwd -> same path, different cwd -> different path), and creates its events dir eagerly | `tests/test_liveness.py::test_liveness_runner_creates_events_dir_on_init`, `::test_liveness_runner_stamps_env_and_forwards_everything_else`, `::test_liveness_runner_events_path_is_deterministic_per_cwd`, `::test_liveness_runner_none_timeout_passes_through` |
| A real leaked non-daemon thread hangs WITHOUT the plugin and exits cleanly (fast, full summary, coverage report intact) WITH it | SPIKE transcript (LOG entry `f4fa1788`), not a committed test -- a fixture-project end-to-end version of this exact scenario (a tiny project whose mutant blocks on a thread join) is explicitly named in the handoff's own ORDER as part of the FULL A2+A3 cluster and is deferred to the next session alongside the `hung` bucket itself (see below) |
| A real end-to-end R2 mutation run still passes with the plugin injected and the candidate wrapper active | `tests/test_cli_run.py::test_run_evaluates_a_real_r2_pass_end_to_end` (pre-existing, real subprocess, real pytest -- run as a regression check, not extended with new assertions this session) |

**Files touched:** `src/assay/liveness.py` (new), `tests/test_liveness.py`
(new, 28 tests), `src/assay/runner.py` (import + two call sites in
`_run_prepared_lane`), `CHANGES.md`.

**Design decisions made without stopping (BLOCKED-protocol default, per
LOG's `f4fa1788` entry -- both flagged for reviewer attention):**
1. Liveness injection extends `CommandPlan.argv_declared` directly rather
   than `argv_appended`, bypassing the CLI's `allow_argv_append` consent
   gate entirely for assay's own infrastructure -- a documented exception
   to `mutation.py`'s own stated A-036 principle. Reasoned through in
   `liveness.py`'s module docstring; not settled by any existing ruling.
2. `LivenessRunner` derives each candidate's events-file path from the
   child's OWN `cwd` (already unique per candidate via P22's own
   replacement-snapshot mechanism) rather than threading a new
   candidate-id parameter through `_execute_mutation_jobs`/`execute_plan`
   -- keeps `ProcessRunner`'s existing signature and every OTHER call site
   completely unchanged.

**A real bug found and fixed by the spike, before any production code was
written:** `os._exit` from `pytest_unconfigure` silently drops pytest's
buffered terminal summary and pytest-cov's printed report table (though the
underlying `.coverage` data file is written correctly regardless) unless
`sys.stdout`/`sys.stderr` are explicitly flushed immediately beforehand.
Fixed in the shipped plugin source; see LOG `f4fa1788` for the before/after
transcript.

**A real bug found and fixed by running the existing test suite, not by
review:** the first cut gated injection on `adapter.language`, which does
not exist on `LanguageAdapter` (the real attribute is `.name`) --
`AttributeError`, caught immediately by 24/42 failures in
`test_runner_run_lane_r2.py`. Fixed; both files corrected.

**Coverage self-check:** `coverage run --branch --source=assay.liveness -m
pytest tests/test_liveness.py` -- **0 missing lines, 0 missing branches**,
100% on `src/assay/liveness.py` (59 statements, 14 branches). `runner.py`'s
own two changed call sites were NOT run through the diff-coverage self-check
this session (budget) -- covered only by the targeted regression run below,
which exercises both the `liveness_injected = True` path (the real R2 e2e
test, whose lane argv is `pytest`-invoking) and, per
`test_runner_run_lane_r2.py`'s existing fixtures, likely the `False` path
too, but this was not individually confirmed line-by-line against the diff.

**Mutation-check (self, 3 planted mutants in `liveness.py`, each reverted
immediately after its run):**

| # | Mutant | Caught by |
| --- | --- | --- |
| 1 | `argv_invokes_pytest`'s `-m`/`pytest` adjacency check forced to always match | `test_argv_invokes_pytest_false_cases` (`("-m", "unittest")`, `("python", "-m")`) |
| 2 | `materialize_liveness_plugin`'s content-match early-return forced off (always rewrite) | `test_materialize_skips_write_when_content_already_matches` (forbidden-write monkeypatch) |
| 3 | `LivenessRunner.__call__` no longer stamps `ASSAY_LIVENESS_EXIT_ENV` | `test_liveness_runner_stamps_env_and_forwards_everything_else` (`KeyError`) |

All three caught by an existing test; no gap found.

**Regression check (targeted, NOT the full gate; full gate deferred to
A6):** `tests/test_mutation_judge.py` + `tests/test_runner_run_lane_r2.py`
(42 passed) and `tests/test_cli_run.py::test_run_evaluates_a_real_r2_pass_end_to_end`
(1 passed, real subprocess).

**What's still open for A2/A3 (not done this session):** the ACTIVE
Popen-based monitoring loop (non-blocking launch, 1s poll, `/proc/<pid>/stat`
process-TREE CPU sampling including children, the `hung`-vs-`budget_exceeded`
decision rule from RW-33), the `hung` `MUTATION_BUCKETS` member and every
place the closed vocabulary is checked (`verdict.py`, `verdict.schema.json`,
`verify.py`), and the fixture-project end-to-end tests the handoff's ORDER
names explicitly (a mutant that blocks on a non-daemon thread join ->
`hung`; a mutant that busy-loops -> `budget_exceeded`). See BRIEF-2 for the
exact next steps.


## RW-36 (session 3) — liveness via `argv_appended` + `judge.mutation.liveness`

**Ruling satisfied:** RW-36 verbatim (controller log, main). Corrects
session 2's flagged A-036 exception: liveness injection must extend
`CommandPlan.argv_appended`, never `argv_declared`; must be gated by a new
`judge.mutation.liveness = "auto" | true | false` key, never by
`allow_argv_append`; the `plan` progress event and `judgment.r2` must both
gain `liveness: {active, reason, plugin}`.

**Oracle → test mapping:**

| Oracle | Test(s) |
| --- | --- |
| `argv_declared` is byte-for-byte the lane's own words after injection; the `-p` flag lands in `argv_appended` instead | `test_liveness.py::test_inject_adds_plugin_flag_to_argv_appended_never_argv_declared`, `::test_inject_preserves_the_lanes_own_appended_tokens_and_recomputes_effective` |
| A liveness-only append runs through `execute_plan` despite `allow_argv_append = False`; a genuine unconsented CLI append on a plan liveness never touched is still refused | `test_liveness.py::test_liveness_injected_plan_runs_through_execute_plan_despite_no_consent`, `::test_a_plan_with_real_unconsented_cli_appended_argv_is_still_refused` |
| `cli_argv_appended` freezes the pre-injection `argv_appended` exactly | `test_liveness.py::test_inject_sets_cli_argv_appended_to_the_pre_injection_appended_tuple` |
| `judge.mutation.liveness`'s three spellings (`"auto"`/`true`/`false`, plus omission) load and normalize correctly; `true` on a non-pytest/non-python lane refuses AT LOAD; `auto`/omission never refuses at load | `test_config_mutation.py::test_liveness_omitted_stays_none_the_omission_is_the_default`, `::test_liveness_declared_spellings_are_accepted_and_normalized[…]`, `::test_liveness_true_is_refused_at_load_when_argv_does_not_invoke_pytest`, `::test_liveness_auto_on_a_non_pytest_argv_loads_without_refusal`, `::test_liveness_rejects_an_unknown_spelling`, `::test_liveness_declared_value_round_trips_through_as_declared` |
| `liveness` is forbidden on an ingested lane (orchestration-only) | `test_config_ingested_mutation.py::test_orchestration_keys_are_refused_on_an_ingested_lane[liveness = …]` (3 new parametrize cases) |
| `JudgmentR2.liveness`'s wire shape (`active`/`reason`/`plugin`, active⇔plugin-present) validates and round-trips; forbidden under `producer = "ingested"` | `test_verdict_judgment.py::test_judgment_r2_liveness_round_trips_when_present`, `::test_judgment_r2_liveness_inactive_form_round_trips`, `::test_judgment_r2_liveness_absent_by_default`, `::test_judgment_r2_refuses_a_malformed_liveness[…]` (7 cases), `::test_judgment_r2_forbids_liveness_under_ingested` |
| A real end-to-end `assay run` records the real, honest `liveness` value for its own lane | `test_cli_run.py::test_run_evaluates_a_real_r2_pass_end_to_end` (extended: this fixture's argv is `/bin/sh -c "grep …"`, so the real answer is `{active: false, reason: "argv-does-not-invoke-pytest", plugin: null}`) |

**Files touched:** `src/assay/liveness.py` (rewritten), `src/assay/
runner.py`, `src/assay/mutation.py`, `src/assay/verdict.py`, `src/assay/
schemas/verdict.schema.json`, `src/assay/config.py`, `src/assay/verify.py`,
`CHANGES.md`; tests as tabulated above.

**Design decision made explicit (mechanical reading of RW-36, not a
judgment call under the BLOCKED protocol):** RW-36's text says liveness
should use "the same channel R1 coverage flags use" — session 2 already
established (LOG `f4fa1788`) that no such channel exists; `argv_appended`
is ONLY the CLI's own `--` passthrough today, and `execute_plan` refuses
any plan carrying it without `allow_argv_append = true`. Routing liveness
through the literal SAME field would contradict RW-36's own "NOT gated by
allow_argv_append" clause for every lane that does not separately declare
CLI-passthrough consent. Resolution: a new `CommandPlan.cli_argv_appended`
field (`None` = "identical to `argv_appended`", the default, byte-for-byte
unaffecting every existing call site) lets `execute_plan`'s refusal check
keep testing only the CLI-consented subset, while `argv_appended` itself
carries the full liveness-augmented view onto the wire. Two integration
tests pin both directions (see the mapping above).

**A real, pre-existing bug found and fixed by this session, incidental to
its own work:** `verify.py`'s `_reconstruct_judgment_r2` never read
`budget_per_candidate_derived_s` (A1, `de32bb91`) back off the raw
document — `verify.py` was not among A1's own "Files touched". `assay
verify` on any real A1-produced document with this field would raise
`ValueError: unknown judgment.r2 field(s): ['budget_per_candidate_derived_s']`.
Fixed alongside `liveness`'s own reconstruction (same function, same
commit).

**Coverage self-check:** `coverage run --branch --source=assay.liveness -m
pytest tests/test_liveness.py` — **100% line+branch** (82 stmts, 22
branches, 0 missing) after one test closed a single missed branch
(`liveness = true` + non-pytest argv + `diagnostics=None`).

**Regression (targeted, GREEN, 406 passed):** `test_liveness.py` (38),
`test_config_mutation.py`, `test_config_ingested_mutation.py`,
`test_verdict_judgment.py` (117), `test_mutation_judge.py` +
`test_runner_run_lane_r2.py` (42), `test_mutation_progress_budget_plan.py`
(38), `test_cli_run.py` (full file, real subprocess), `test_verify_layer_
independence.py`; plus an earlier broader sweep of every test file
grepped for `argv_appended`/`allow_argv_append` (`test_runner_plan_env.py`,
`test_infrastructure_injection.py`, `test_refusal_announcement.py`,
`test_environment_preflight.py`, `test_dependency_purity.py`,
`test_docs_examples_and_vocabulary.py`, `test_runner_run_lane.py`,
`test_runner_assemble_verdict_mutation.py`,
`test_verdict_mutation_artifacts.py`) — all green (221+63 passed across
those two batches). Full gate still deferred to A6.

**Not run this session:** the real registered gate; A3's own tests
(A3 not started — see BRIEF-3).
