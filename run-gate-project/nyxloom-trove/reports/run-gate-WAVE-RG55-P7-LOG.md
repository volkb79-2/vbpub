# run-gate-WAVE-RG55-P7 — assay B091 — LOG

One entry per commit on the `assay-liveness` branch, self-hash rule: each
entry names the hash of the commit it documents (recorded once that commit
exists, in a small follow-up entry-only commit).

## Entries

### `de32bb91` — A1: `budget_per_candidate = "auto"` default (D-23)

- Files: `src/assay/{config,mutation,verdict,runner,cli}.py`,
  `src/assay/schemas/verdict.schema.json`, `docs/CONSUMERS.md`,
  `CHANGES.md`, `nyxloom-trove/4-backlog.md`, 6 test files.
- Tests-first: yes (`test_mutation_progress_budget_plan.py`'s new cases
  written against `run_mutation`'s new `budget_per_candidate_auto` kwarg
  before the kwarg existed; the two config-loader tests rewritten to name
  the new expected behavior before the loader changed).
  Full oracle → test mapping: REPORT.md, A1 section.
- Gate: not yet run (targeted diff-coverage self-check only — see
  REPORT.md). Full gate deferred to A6 per the handoff's own "mutation
  lane last, one at a time" ordering.
- Self-review found and fixed one real bug before this commit (WARN/
  "none"-detection conflation crashing `parse_duration("none")` when
  `diagnostics=None`) — see REPORT.md for detail and the regression test.

### `f649a249` — test fix: stray leftover assertion in the A1 regression test

- `tests/test_runner_run_lane_r2.py` only. The new `diagnostics=None`
  regression test (added in `de32bb91`) inherited a trailing
  `assert "B090" in warning` line from the test above it (an imprecise
  Edit match) — `NameError`, not a real failure; caught by actually running
  the file, not by trusting the diff. Production code (already committed)
  needed no change.

### `f4fa1788` — A2 (session 2): materialized pytest liveness plugin + candidate `os._exit` wrapper (D-23/RW-33)

- Files: new `src/assay/liveness.py`, new `tests/test_liveness.py`,
  `src/assay/runner.py` (import + two call sites inside
  `_run_prepared_lane`), `CHANGES.md`.
- SPIKE run FIRST, in a scratch dir outside the repo (never committed):
  proved (1) `-p <module>` + `PYTHONPATH` resolves a plugin from a venv
  where `assay` is not importable; (2) WITHOUT the plugin, a test that
  leaks a non-daemon `threading.Thread(daemon=False)` prints its summary
  (`1 passed...`) then hangs — reproduced live, `timeout 8` killed it
  (exit 124); (3) WITH the plugin + `ASSAY_LIVENESS_EXIT=1`, the same test
  exits in ~1.9s, exit 0; (4) a REAL BUG the spike caught before it
  shipped: `os._exit` from `pytest_unconfigure` silently DROPPED the
  terminal summary line and pytest-cov's printed report table (though the
  underlying `.coverage` DATA file was written correctly either way)
  unless `sys.stdout.flush()`/`sys.stderr.flush()` run immediately before
  `os._exit` — fixed in the shipped plugin source; re-verified after the
  fix (full summary + coverage table present, exit 0).
- A real architectural correction to BRIEF-1's own assumption: there is
  **no existing "R1 coverage argv-append call site"** to reuse — grepped
  and confirmed assay never injects `--cov`/coverage flags itself; a lane
  declares them in its OWN argv and assay only READS the coverage artifact
  the lane's own command produced. `CommandPlan.argv_appended` is a
  CLI-only passthrough feature (`assay run ... -- <extra args>`), gated by
  `lane.allow_argv_append`, refused by `execute_plan` when the lane did not
  opt in (A-095). Using it for liveness injection would either require
  every native R2 python lane to declare `allow_argv_append: true`
  (defeating "automatic infra, not lane opt-in") or bypass a gate whose
  entire meaning is lane consent. **Resolution (BLOCKED-protocol default,
  not re-opening RW-33 itself):** liveness injection extends
  `argv_declared` directly (via `dataclasses.replace` on the already-
  resolved plan), leaving `argv_appended`/`allow_argv_append` completely
  untouched — mirrors how `env_effective` already extends beyond
  `env_declared` for passthrough/infrastructure facts. This is a
  DOCUMENTED, DELIBERATE exception to `mutation.py`'s own stated A-036
  principle ("flags ... never derived by assay"); reasoned through at
  length in `liveness.py`'s own module docstring. **Flagged for the
  reviewer explicitly** — this is a genuine judgment call A-036's original
  author did not anticipate, not settled by any existing ruling.
- A second real bug caught by RUNNING the tests, not by review: the first
  cut gated injection on `adapter.language == "python"` (BRIEF-1's own
  wording) — `LanguageAdapter`'s real attribute is `.name`, not
  `.language` (`AttributeError`, caught immediately by
  `test_runner_run_lane_r2.py`'s existing suite, 24/42 failing). Fixed to
  `adapter.name == "python"`; docstring corrected to match.
- `LivenessRunner` shipped at v1 scope ONLY: env-stamping
  (`ASSAY_LIVENESS_EVENTS` keyed off the child's own `cwd` — already
  unique per candidate, no signature change needed anywhere in
  `mutation.py`; `ASSAY_LIVENESS_EXIT=1`), delegates to `inner` (today's
  blocking `subprocess.run` via `default_process_runner`). The ACTIVE
  Popen + `/proc` CPU-sampling monitoring loop, the `hung` classification,
  and the `MUTATION_BUCKETS` addition are **NOT implemented** — open
  work, see BRIEF-2.
- Tests: 28 new (`tests/test_liveness.py`), pure unit tests against
  `liveness.py` directly (no real subprocess — the real-subprocess proof
  is the spike transcript above, plus the real end-to-end regression test
  named below). `coverage run --branch --source=assay.liveness -m pytest
  tests/test_liveness.py`: **100% line and branch** on
  `src/assay/liveness.py` (59 stmts, 14 branches, 0 missing either).
- Regression check (targeted, NOT the full gate): `tests/test_mutation_judge.py`
  + `tests/test_runner_run_lane_r2.py` (42 passed) and the real end-to-end
  `tests/test_cli_run.py::test_run_evaluates_a_real_r2_pass_end_to_end`
  (1 passed, real subprocess, real pytest, plugin actually injected and
  exercised). `runner.py`'s own two changed call sites are NOT separately
  diff-coverage-checked against the full gate's judge (budget; see
  BRIEF-2) — this targeted run is the honest bound on what was verified
  this session.
- Mutation-check (self, per the quality bar — 3 planted mutants,
  `liveness.py` only, each reverted immediately after its run):
  (1) `argv_invokes_pytest`'s `-m`/`pytest` adjacency check
  (`tokens[index + 1] == "pytest"`) replaced with `True` (always match
  after any `-m`) → caught by `test_argv_invokes_pytest_false_cases`
  (the `("-m", "unittest")` and `("python", "-m")` cases both failed as
  expected); (2) `materialize_liveness_plugin`'s
  `if existing == _PLUGIN_SOURCE: return target` early-return replaced
  with `if False: ...` (always rewrite) → caught by
  `test_materialize_skips_write_when_content_already_matches` (its
  forbidden-write monkeypatch fired); (3) dropped
  `stamped_env[ASSAY_LIVENESS_EXIT_ENV] = "1"` from `LivenessRunner.
  __call__` → caught by
  `test_liveness_runner_stamps_env_and_forwards_everything_else`
  (`KeyError` on the missing env key). All three caught by an EXISTING
  test — no gap found, no new test needed.

### `e27b107b` — session 3: RW-36 correction (argv_appended channel, `judge.mutation.liveness` gate, `liveness` wire record)

- **First commit of session 3** (fresh Sonnet successor, seeded from
  BRIEF-2). Implements RW-36 verbatim, per the handoff's own "do this FIRST"
  instruction, before starting A3.
- Files: `src/assay/liveness.py` (rewritten: `inject_liveness_plugin` now
  extends `argv_appended`, never `argv_declared`; returns a
  `LivenessInjection` NamedTuple — `plan`, `active`, `reason`, `plugin` —
  instead of a bare 2-tuple; new `liveness_policy` parameter and the
  `LIVENESS_AUTO`/`LIVENESS_TRUE`/`LIVENESS_FALSE` vocabulary),
  `src/assay/runner.py` (new `CommandPlan.cli_argv_appended` field —
  `None` means "identical to `argv_appended`", the byte-identical-to-before
  default for every plan liveness never touches; `execute_plan`'s
  `allow_argv_append` refusal now tests `cli_argv_appended` when set, so a
  liveness-injected plan's own unconditional `-p` append is never caught by
  a consent gate whose entire meaning is "the lane permitted THIS"; the two
  `_run_prepared_lane` call sites updated to the new `LivenessInjection`
  shape and to thread `lane.judge.mutation.liveness`/the three
  `liveness_*` fields into `run_mutation` and `_build_judgment_r2`),
  `src/assay/mutation.py` (`run_mutation` gains `liveness_active`/
  `liveness_reason`/`liveness_plugin` kwargs; the `plan` progress event
  gains `liveness: {active, reason, plugin}`), `src/assay/verdict.py`
  (`JudgmentR2.liveness: Mapping[str, Any] | None`, native-only/optional on
  `budget_per_candidate_derived_s`'s own footing — added to
  `_NATIVE_ONLY_FIELDS`, the `[:-2]`→`[:-3]` optional-trailing slice
  updated, shape validated in `_check_native_policy`, serialized in
  `to_dict`), `src/assay/schemas/verdict.schema.json` (`judgment_r2.
  liveness` object, forbidden under `producer = "ingested"`),
  `src/assay/config.py` (`MutationConfig.liveness: str | None`; `argv`
  threaded through `_load_lane` → `_load_judge` → `_load_mutation` so
  `liveness = true` on a non-pytest/non-python lane refuses AT LOAD, in
  writing, rather than only WARNing at run time; `liveness` added to the
  ingested lane's `orchestration_only` forbidden-key set), `src/assay/
  verify.py` (`_reconstruct_judgment_r2` gains `liveness=raw.get(...)`).
- **A real, pre-existing bug found and fixed while wiring `liveness`
  beside it**: `_reconstruct_judgment_r2` (`verify.py`) never read
  `budget_per_candidate_derived_s` back off the raw document at all (A1's
  own field, `de32bb91` — `verify.py` was not in A1's "Files touched"
  list). `assay verify` on any real document A1's `"auto"` default
  produces would raise `ValueError: unknown judgment.r2 field(s):
  ['budget_per_candidate_derived_s']`, since the reconstructed object's own
  `to_dict()` omitted a field it was never given. Fixed by adding the
  missing `.get()` read alongside `liveness`'s own.
- **Design decision made explicit (not a BLOCKED-protocol default — this
  is straightforward mechanics once RW-36's ruling is read literally):**
  RW-36 says liveness argv must go through `argv_appended`, "the same
  channel R1 coverage flags use", but session 2 had already found (LOG
  `f4fa1788`) that no such R1 coverage-argv-append call site exists —
  `argv_appended` is ONLY ever the CLI's own `--` passthrough today,
  gated end-to-end by `execute_plan`'s `if plan.argv_appended and not
  plan.allow_argv_append: refuse`. Routing liveness through the SAME field
  unconditionally would therefore refuse every liveness-active R2 run
  whose lane does not separately declare `allow_argv_append = true` (the
  common case) — exactly backwards from RW-36's own "NOT gated by
  allow_argv_append" requirement. Resolution: `CommandPlan.cli_argv_
  appended` freezes the CLI-only subset so the refusal check can keep
  testing only that, while `argv_appended` (and therefore `argv_effective`,
  and the verdict's own transparency field) carries the full,
  liveness-augmented view RW-36 asks a reader to see. Two integration
  tests pin both directions: a liveness-only append runs through
  `execute_plan` despite `allow_argv_append = False`
  (`test_liveness_injected_plan_runs_through_execute_plan_despite_no_
  consent`), and a genuine unconsented CLI append is STILL refused on a
  plan liveness never touched
  (`test_a_plan_with_real_unconsented_cli_appended_argv_is_still_refused`).
- Tests: `tests/test_liveness.py` rewritten/extended to 38 tests (was 28);
  `coverage run --branch --source=assay.liveness`: **100% line+branch**
  (82 stmts, 22 branches, 0 missing) after one added test closed a single
  missed branch (`diagnostics=None` on the `liveness=true`+non-pytest-argv
  defensive path). New tests: `tests/test_config_mutation.py` (+7,
  `judge.mutation.liveness` load-time acceptance/normalization/refusal),
  `tests/test_config_ingested_mutation.py` (extended the existing
  `orchestration_only` parametrize with 3 `liveness` spellings),
  `tests/test_verdict_judgment.py` (+7, `JudgmentR2.liveness` shape
  validation + ingested-forbidden), `tests/test_cli_run.py` (extended the
  existing real end-to-end R2 assertion with the new `liveness` key — this
  fixture's own lane argv is `/bin/sh -c "grep ..."`, so the real,
  honest answer is `{active: false, reason:
  "argv-does-not-invoke-pytest", plugin: null}`, not a mocked one).
- Regression, GREEN (406 passed): `test_liveness.py`,
  `test_config_mutation.py`, `test_config_ingested_mutation.py`,
  `test_verdict_judgment.py`, `test_mutation_judge.py`,
  `test_runner_run_lane_r2.py`, `test_mutation_progress_budget_plan.py`,
  `test_cli_run.py` (full file), `test_verify_layer_independence.py`; plus
  an earlier, broader sweep of every test file grepped for `argv_appended`/
  `allow_argv_append` touching (`test_runner_plan_env.py`,
  `test_infrastructure_injection.py`, `test_refusal_announcement.py`,
  `test_environment_preflight.py`, `test_dependency_purity.py`,
  `test_docs_examples_and_vocabulary.py`, `test_runner_run_lane.py`,
  `test_runner_assemble_verdict_mutation.py`,
  `test_verdict_mutation_artifacts.py`) — all green, no other file in the
  `argv_appended`/`allow_argv_append` grep was touched by this commit's
  behaviour in a way any of them noticed.
- Full gate deferred to A6 per the handoff's own ordering (targeted tests
  + a self-check only). HOST LOAD: `/proc/pressure/memory` `full avg10`
  ranged 0.3–5.1 across this commit's test runs (one reading at 5.1,
  immediately after the heaviest batch finished, not sustained — `avg300`
  stayed ≤2.8 throughout); two other tracks' R2 mutation containers
  (`run-gate-vbpub-r2-2315801-1789214565`, watcher PID 2415767) were
  confirmed still alive before AND after this commit's work — no
  whole-suite run and no assay R2 lane attempted, per the binding rule.

### `44dd12ca` — A3: active `LivenessRunner` monitoring loop, `hung` bucket (session 4)

- **First and only substantive commit of session 4** (fresh Sonnet
  successor, seeded from BRIEF-3). Implements BRIEF-3's decided A3
  mechanism in full: the active Popen-based monitoring loop, the
  `LivenessHungExpired`/`CANDIDATE_HUNG` classification path, and the
  `hung` bucket's full threading through every closed vocabulary this
  codebase has. A3 is functionally COMPLETE and tested; the real
  end-to-end fixture-project test and the planted-mutant table BRIEF-3
  itself asks for are NOT done this session (see "What's OPEN" below and
  BRIEF-4).
- Files: `src/assay/liveness.py` (rewritten `LivenessRunner`: `inner=`
  dropped, replaced with `expect_next_event_within_s=` [required] and
  `monotonic=`/`sleep=`/`poll_interval_s=`/`cpu_reader=`/`popen=`
  [all injectable, real defaults] — `__call__` now launches
  `Popen(start_new_session=True, stdout=<file>, stderr=<file>)` and hands
  off to a new `_monitor` loop; new module-level `LivenessHungExpired`
  [a `subprocess.TimeoutExpired` subclass], `tree_cpu_seconds` +
  `_pid_cpu_ticks`/`_pid_children_via_task`/`_pid_children_via_ppid_scan`
  [the `/proc` tree-CPU sampler, task-API primary + ppid-scan fallback,
  per-pid not just at the root], `compute_expect_next_event_within_s`
  [reads a baseline events NDJSON for the slowest `test` event's
  `duration_s`, `max(3x, 15s)`; falls back to `max(60s,
  baseline_s/4)`], `_read_events_progress`/`_safe_size`/`_read_bytes`
  [small side-file helpers, each individually unit-tested for its own
  `OSError`/torn-line tolerance]), `src/assay/errors.py`
  (`ReasonCode.CANDIDATE_HUNG` under `Outcome.BUDGET_EXCEEDED`, beside
  `LANE_TIMEOUT`), `src/assay/runner.py` (`_execute_plan_inner`'s
  `except subprocess.TimeoutExpired` gains a one-line `isinstance(exc,
  liveness.LivenessHungExpired)` → `reason_code` selection; the baseline
  call site gains `ASSAY_LIVENESS_EVENTS` stamping [never `_EXIT`] via a
  plan COPY used only for that one `_execute_snapshot_unit` call, never
  mutating the shared `plan` the R2 dispatch reads further down;
  `expect_next_event_within_s` computed once, right beside `result = unit.
  result`, from `mutation.baseline_wall_seconds(result)` — the SAME
  function A1 already uses, never a second derivation; `LivenessRunner`
  construction updated to the new keyword-only contract), `src/assay/
  mutation.py` (`_classify_mutant_result`/`_classify_mutant_result_with_
  equivalence` gain the `reason_code is CANDIDATE_HUNG` → `"hung"` branch;
  `_execute_mutation_jobs`'s bucket-dict literal and final `Mutation(...)`
  construction converted from five hand-written keys to a
  `MUTATION_BUCKETS`-driven comprehension [A-228's own "reaches some
  layers and not others" lesson — this WAS a five-entry literal that would
  have `KeyError`'d the moment `hung` was returned]; **`judge_mutation`
  gains a `mutation.hung` branch between `budget_exceeded` and `survived`
  — this is the real gap this session found**: without it, a `hung`-only
  candidate's OVERALL R2 claim status fell all the way through to `PASS`,
  silently losing the fact that a candidate never finished, even though
  the PER-candidate bucket was correctly `hung`), `src/assay/verdict.py`
  (`MUTATION_BUCKETS` gains `"hung"`, sixth entry; `Mutation.hung: tuple[
  MutantOutcome, ...] = ()` — every generic `MUTATION_BUCKETS`-driven site
  in this file [`to_dict`, `__post_init__`'s per-bucket checks,
  `_check_identities_are_unique`, `_check_kill_signal_is_killed_only`,
  `_check_arithmetic`] picked it up with NO further edit), `src/assay/
  schemas/verdict.schema.json` (`mutation.hung` property added [array of
  `mutant_outcome`, same shape as `budget_exceeded`] — deliberately NOT in
  `mutation`'s `required` list, so a document produced before this bucket
  existed still validates; the `MUTANT_LIMIT_EXCEEDED` pre-submission
  sentinel's own conditional gains `"hung": {"maxItems": 0}`; the flat
  `reason_code` enum and the `reason_codes.BUDGET_EXCEEDED` cross-check
  both gain `"CANDIDATE_HUNG"` — found by a real test failure, not by
  inspection), `src/assay/verify.py` (`_mutation_of` normalizes a missing
  `"hung"` key to `[]` ONCE, so every raw check downstream
  [`_mutant_entries`, the arithmetic size check, `_check_identities_are_
  unique`] sees the same shape a native post-this-session document always
  has; `_reconstruct_mutation` special-cases `"hung"` to `raw.get(name,
  [])` while the five original buckets keep strict `raw[name]` — a
  document missing one of THOSE is genuinely malformed, not merely old).
- **Design decision NOT re-litigated, implemented exactly as BRIEF-3
  wrote it down**: `LivenessHungExpired` + `ReasonCode.CANDIDATE_HUNG` +
  the one-line `isinstance` check, reusing 100% of the existing
  tail-decoding/truncation logic (`_bounded_tail`/`_decode_timeout_stream`
  in `runner.py`) with ZERO changes to either — `_execute_plan_inner`'s
  generic post-processing already truncates whatever ANY `process_runner`
  returns (the normal-completion path) or raises (the `TimeoutExpired`
  except-clause), so `LivenessRunner` never needed to import
  `_bounded_tail` at all (BRIEF-3's own "ask/flag if this feels wrong"
  concern about reaching across a private name — resolved by finding it
  was never actually necessary, not by asking).
- **Two real, pre-existing gaps found and fixed this session, both by a
  test failure rather than by inspection:**
  1. `mutation.judge_mutation`'s outcome-precedence chain (documented
     above) — the single most important functional finding this session:
     without it, `hung` would have been a real, threaded, TESTED bucket
     that nonetheless never actually changed a verdict's overall outcome
     for the one shape B091 exists to fix (a candidate that hangs and
     nothing else goes wrong). Caught by a dedicated fixture
     (`r2_budget_exceeded_candidate_hung.json`) failing
     `test_verdict_conformance.py`'s own re-derivation check, not by
     reading the function.
  2. Every hand-written oracle transcription of the reason-code/mutation-
     bucket vocabulary needed its own update once `errors.py`/`verdict.py`
     changed: `docs/DESIGN-GUIDE.md` §6's table, `tests/test_errors.py`'s
     `EXPECTED_REASON_CODES`, `tests/test_verdict_conformance.py`'s
     `VOCABULARY` (plus its own completeness check, which required the new
     fixture above), `tests/test_verdict_reason_codes.py`'s hardcoded `32`
     counts (now `33`). Each was found by running the FULL targeted
     regression sweep, not by grepping for every occurrence up front —
     three separate rounds of "run the suite, read the failure, fix the
     one hand-written table it names" were needed before the sweep came
     back clean.
- Every pre-existing hand-written verdict fixture carrying a `mutation`
  payload (`inconclusive.json`, `r2_budget_exceeded_lane_timeout.json`,
  `r2_budget_exceeded_mutant_limit_exceeded.json`,
  `r2_error_exec_failed_mutant_crashed.json`,
  `r2_fail_mutants_survived.json`,
  `r2_inconclusive_all_mutants_equivalent.json`,
  `r2_inconclusive_no_mutants.json`, `r2_pass.json`,
  `r2_pass_with_judgment.json`) gained `"hung": []` — `Mutation.to_dict()`
  now emits it unconditionally, so a strict-equality
  "matches-the-hand-written-fixture" test (A-041/A-067's own independent-
  oracle discipline) needs the oracle updated, not the code relaxed;
  patched mechanically (a small script asserting the exact bucket-key
  shape before inserting, never a blind sed) then verified by re-running
  every fixture-equality test green.
- Tests: `tests/test_liveness.py` rewritten for the new `LivenessRunner`
  launch contract (`_FakePopen`/`_FakeProc`, no more `_FakeInner`) — 38
  tests, all still green, same count (the v1 blocking-delegate tests were
  REWRITTEN for the new contract, not deleted or added-to). New:
  `tests/test_liveness_runner_monitor.py` (8 tests: idle+flat-CPU→hung,
  CPU-growing-prevents-hung [BRIEF-1's "other optional parameter at its
  default" lesson, the direct regression for RW-33's "a CPU-spinning
  mutant is NOT hung"], `/proc`-failure-never-hung,
  session_finish+30s-alive→hung [never consults CPU], normal completion,
  `timeout=None` never expires on budget alone, plus TWO tests using a
  REAL `subprocess.Popen` [`sleep 300` → real `killpg`-and-reap; a real
  `printf` → real stdout capture] with only the clock/sleep faked — every
  other test uses a fully fake `popen`/`monotonic`/`sleep`/`cpu_reader`,
  so none waits out a real 15s/30s/60s threshold). New:
  `tests/test_liveness_proc_helpers.py` (21 tests: direct unit tests for
  `tree_cpu_seconds`'s task-API/ppid-scan fallback [both at the root AND
  per-child], a duplicate-pid-already-visited skip, a fabricated-dead-
  child skip, `compute_expect_next_event_within_s`'s slowest-test/torn-
  line/malformed-duration/missing-file cases, `_read_events_progress`'s
  blank/torn-line/non-session-finish cases, `_safe_size`/`_read_bytes`'s
  missing-file cases, `LivenessRunner._kill`'s two independent `except`
  clauses via a fake proc + monkeypatched `os.killpg`). New:
  `tests/test_mutation_hung_bucket.py` (5, the per-candidate classify
  split, both classifier functions, the `LANE_TIMEOUT`-still-
  `budget_exceeded` regression). New: `tests/test_verify_hung_bucket.py`
  (4: a document missing `hung` still verifies; a document WITH a `hung`
  entry verifies AND flips the overall status to `BUDGET_EXCEEDED`/
  `CANDIDATE_HUNG`; an uncounted `hung` entry is refused by the arithmetic
  check; a `hung` entry duplicating another bucket's identity is refused
  by the uniqueness check). `tests/test_mutation_judge.py` +2 (the
  `judge_mutation` gap, and its own regression: `budget_exceeded` still
  outranks `hung` when both buckets are non-empty). `tests/
  test_runner_execute.py` +1 (`LivenessHungExpired` → `CANDIDATE_HUNG`,
  with the existing plain-`TimeoutExpired`→`LANE_TIMEOUT` test right above
  it as the negative). `tests/test_verdict_reason_codes.py`/`tests/
  test_verdict_conformance.py`/`tests/test_errors.py` updated (see "gaps
  found" above).
- **Coverage self-check: `src/assay/liveness.py` — 100% line+branch** (285
  statements, 78 branches, 0 missing — `coverage run --branch
  --source=assay.liveness -m pytest tests/test_liveness.py tests/
  test_liveness_runner_monitor.py tests/test_liveness_proc_helpers.py`,
  67 tests). No diff-coverage self-check run on `mutation.py`/`runner.py`/
  `verdict.py`/`verify.py`/`errors.py` this session — only `liveness.py`
  carried BRIEF-3's explicit 100% requirement; the other files' new lines
  are each exercised by at least one dedicated test (see above) but not
  measured for branch completeness.
- Regression, GREEN: every `mutation`/`runner`/`verdict`/`verify`/
  `liveness`/`errors`-named test file in one serial sweep (**2107
  passed**), plus `test_cli_run.py` (full file, real end-to-end
  subprocess) + `test_config_mutation.py` + `test_config_ingested_
  mutation.py` + `test_docs_examples_and_vocabulary.py` (148 passed) — the
  latter run specifically because `DESIGN-GUIDE.md` was touched. Full
  gate (`tools/tester-unified-gate.sh`) still deferred to A6 per the
  handoff's own ordering.
- **What's OPEN (this session's own honest gaps, not previously flagged):**
  the real end-to-end fixture-project test BRIEF-3 names explicitly (a
  thread-join-style hang → `hung` within ~45s through the REAL `assay run`
  CLI on a tiny real pytest project; a busy-loop → `budget_exceeded`,
  NOT `hung`) — NOT attempted this session, purely a checkpoint-budget
  decision (session was already well past the checkpoint clause's ~90-call
  ceiling by the time A3's threading work was done and green); the
  ≥3-planted-mutant table for the monitoring loop BRIEF-3 asks for,
  recorded with its catching test — NOT done (the 100% line+branch
  self-check is real and done, but a planted-mutant table is a DIFFERENT,
  additional proof BRIEF-3 asks for on top of it, and it is not done).
  Both are BRIEF-4's own first items.
- HOST LOAD: `/proc/pressure/memory` `full avg10` stayed ≤4.5 throughout
  (one `some avg10` reading at 4.46, `full` itself never approached the
  5.0 back-off threshold); `nice -n 19 ionice -c 3` used for every pytest
  invocation; every run serial, targeted files only (three sweeps did
  spill past the Bash tool's own 120s foreground timeout and were moved
  to background, per the harness's own mechanism — not a second
  concurrent invocation). Two other wave tracks' own mutation-lane
  containers/launchers were confirmed alive at both the start
  (`run-gate.py --base main assay-r2`, watcher PID 2415767/launcher
  2415766) and later in the session (P1's `cgroup-profiler` R2 resume,
  PID 680903/680904 joined partway through) — this session never ran
  the shared assay R2 mutation-lane gate itself, only targeted pytest,
  which does not conflict with either per the handoff's own binding rule.

### `99463ae5` — A3 remainder: real e2e liveness fixture tests + a real plugin JSON bug they found (session 5)

- BRIEF-4's own first item, done: `test_run_liveness_classifies_a_thread_
  join_hang_as_hung` and `test_run_liveness_classifies_a_busy_loop_as_
  budget_exceeded_not_hung`, both in `tests/test_cli_run.py`, through the
  REAL installed `assay run` CLI. Fixture design exactly per BRIEF-4: a
  base commit with `def guard(x): return False`, a second commit
  introducing `def guard(x): return x <= 0` (the compare-swap site, on a
  changed line), a `tests/test_mod.py` whose test starts a background
  thread blocked on `threading.Event().wait()`, calls `guard(0)` (baseline
  `True`) to `.set()` the event before `t.join()` with NO timeout, inside
  the test body — so a mutant's `x < 0` (compare-swap `LtE`→`Lt`, `False`
  at `x=0`) never signals the event and `t.join()` blocks forever at ~0%
  CPU, entirely inside the `call` phase (never reaching
  `pytest_sessionfinish`, so A2's `os._exit` cure cannot mask it). The
  busy-loop sibling reuses the identical fixture shape but the
  mutant-only branch spins `while True: total += 1` instead of blocking.
  Both lanes: `rigor = ["R0", "R2"]`, `argv` invoking `-m pytest` (so
  liveness auto-activates), `source_roots = ["pkg"]`,
  `operators = ["python:compare-swap"]`, `jobs = 1`. Per-candidate budgets
  chosen deliberately relative to `LivenessRunner`'s own two fixed
  thresholds (`_HUNG_CPU_WINDOW_S=30s`, the 15s idle floor from a fast
  baseline): the hang lane's `budget_per_candidate = "50s"` sits well
  above the ~31s a CORRECT loop needs to reach `hung` on its own (so only
  a regressed loop would ever wait out the budget); the busy-loop lane's
  `"35s"` is deliberately PAST the 30s CPU-growth window (an 8-10s budget
  would prove nothing about the CPU-growth branch at all, since `hung`
  structurally cannot fire before 30s regardless of what the candidate is
  doing — this session's own first draft used a short budget and had to
  be corrected once the reasoning was checked against the code, not
  merely against the brief's prose).
- **A real, previously-undetected bug, found by the FIRST test's own
  failure, not by inspection.** First run: the hang test got
  `LANE_TIMEOUT` (plain elapsed-budget expiry) instead of the expected
  `CANDIDATE_HUNG` — the candidate genuinely hung (confirmed: it ran the
  full 50s budget), but `LivenessRunner._monitor` never classified it
  `hung`. Isolated by driving `LivenessRunner` directly (no CLI, no
  pytest) against a bare `threading.Event().wait()` subprocess with the
  REAL default `cpu_reader`/`monotonic`/`sleep` — this DID correctly
  classify `hung` at `t=30.0s`, proving the loop's own decision logic is
  correct in isolation. Then reproduced the exact CLI scenario standalone
  (bypassing pytest's own test harness so the working directory survives
  inspection) and read `.assay/liveness/baseline.ndjson` directly: every
  line was **not valid JSON** —
  `{"event": "test", "nodeid": 'tests/test_mod.py::...', "outcome":
  'passed', ...}` — single-quoted `nodeid`/`outcome` values. Root cause:
  `_PLUGIN_SOURCE`'s `pytest_runtest_logreport`/`pytest_sessionfinish`
  built each line with Python's `%r` (`repr()`) on `nodeid`/`outcome`/
  `duration_s` — `repr()` of a string is SINGLE-quoted (JSON requires
  double quotes) and `repr(None)` is the bare token `None` (JSON's `null`
  is a different token). `compute_expect_next_event_within_s`/
  `_read_events_progress` both catch `json.loads`'s `ValueError` and skip
  the line as a "tolerated torn line" (BY DESIGN, for a genuinely torn
  LAST line from a plugin still writing) — so this was silently swallowed
  rather than raised, on EVERY line, always. Effect: `slowest_test_s` was
  NEVER found (`compute_expect_next_event_within_s` always fell to the
  coarse `max(60s, baseline_s/4)` fallback, never the tight
  measurement-based `max(3×slowest, 15s)` bound — 60s instead of 15s in
  this fixture's own case, which is exactly why the 50s hang-lane budget
  elapsed first) and `saw_session_finish` was NEVER `True` (the
  "`session_finish` seen, still alive 30s later" `hung` branch RW-33
  names in `LivenessRunner`'s own docstring was live, tested-in-isolation
  code that could never actually fire against a real candidate — every
  existing test for that branch feeds `_read_events_progress` a
  HAND-CONSTRUCTED, already-valid-JSON fixture, never the real plugin's
  own output). No existing unit test caught this because every one of
  them (`test_liveness_proc_helpers.py`'s
  `compute_expect_next_event_within_s`/`_read_events_progress` tests)
  hand-constructs its own valid-JSON event lines rather than running the
  plugin's own code — exactly the independent-oracle gap a real
  end-to-end test exists to close, and exactly what BRIEF-3/BRIEF-4 asked
  this session to attempt for this reason.
- **Fix**: `_PLUGIN_SOURCE`'s two hooks now build a real `dict` and call
  `json.dumps` (stdlib — `import json` added to the plugin's own tiny
  import block) instead of hand-rolling a JSON-shaped string with `%r`.
  Files: `src/assay/liveness.py` (the plugin source string + a new
  docstring paragraph recording the bug for future maintainers, right
  above `_PLUGIN_SOURCE`), `tests/test_cli_run.py` (the two new e2e
  tests), `tests/test_liveness.py` (new:
  `test_materialized_plugin_writes_valid_json_events` — materializes the
  plugin, imports it via `importlib.util` [no real pytest subprocess,
  matching this file's own stated "no real subprocess" scope, see its
  module docstring], calls both hooks directly with a fake `report`/
  `exitstatus`, asserts every emitted line is valid, correctly-typed JSON,
  including the `None`-duration → JSON `null` edge case and an explicit
  `"'...'" not in line` / `'"..."' in line` pin of the exact regression
  symptom — catches the bug in <1s rather than only via the ~70s real CLI
  test).
- `liveness.py`: **100% line+branch, unchanged shape** (285 stmts/78
  branches) — the fix changes only the plugin's own DATA string, not
  `liveness.py`'s own executable surface.
- Regression, GREEN: `test_liveness.py` (39), `test_liveness_runner_
  monitor.py` + `test_liveness_proc_helpers.py` + `test_mutation_hung_
  bucket.py` (68 combined, with the coverage run above), `test_runner_
  execute.py`/`test_mutation_judge.py`/`test_verify_hung_bucket.py`/
  `test_verdict_reason_codes.py`/`test_verdict_conformance.py`/
  `test_errors.py` (768) — plus the two new e2e tests themselves (71s
  combined wall-clock; each independently well under BRIEF-3's own ~90s-
  per-test bound). No full `mutation`/`runner`/`verdict`-named sweep this
  session (see REPORT's own call-budget note) — the targeted set above is
  the same surface A3 touched, chosen to catch a regression in exactly
  the areas this commit's fix reaches.
- HOST LOAD: `/proc/pressure/memory` `full avg10` was 6.92–9.43 (ABOVE the
  5.0 back-off threshold) immediately before the real e2e run was due;
  backed off via a bounded `Monitor` poll (8×15s ceiling) rather than a
  raw sleep, until it read 4.32, then proceeded. `nice -n19`/`ionice -c3`
  for every pytest invocation; serial; targeted files throughout. The
  busy-loop mutant's own ~100%-of-one-core spin runs at normal (non-
  niced) priority for ~35s by construction (`LivenessRunner` launches the
  CANDIDATE directly via `Popen`, not through this session's own
  `nice`-prefixed shell) — bounded and brief by the lane's own small
  `budget_per_candidate`, not left running.

### `d1540eda` — A3: ≥3 planted-mutant table for the monitoring loop (session 5)

- BRIEF-4's own second item, done. Four candidate mutants BRIEF-4
  sketched, each hand-planted with a `cp`-backup/restore cycle (never
  `git checkout --`), targeted tests run, restored before the next:
  1. `idle_for >= self._expect_next_event_within_s` → `>`: **NOT caught**
     by the existing suite (`test_idle_with_flat_cpu_is_hung` only pins a
     LOWER bound, `clock.t >= 30.0` — with the default 30s CPU window
     dominating a 15s idle floor, the off-by-one just fires one tick
     later and still satisfies that assertion). This is a real gap in the
     session 4 test suite's own precision, not a `liveness.py` defect —
     fixed by a NEW test,
     `test_idle_threshold_is_inclusive_at_the_exact_boundary`
     (`tests/test_liveness_runner_monitor.py`), which monkeypatches
     `_HUNG_CPU_WINDOW_S` down to 2.0s so the idle threshold (5.0s) is the
     LAST-satisfied condition, then pins `hung` firing at the exact tick
     (`clock.t == 5.0`) — the mutant now fires at `6.0` and the test
     fails as designed.
  2. `cpu_growing = (cpu_now - baseline_cpu) >= _HUNG_CPU_GROWTH_FLOOR_S`
     → `>`: **also NOT caught** (the two existing CPU tests use deltas of
     `0.0` and `2.0`/sample, never exactly the `1.0` floor). Fixed by a
     second new test, `test_cpu_growth_floor_is_inclusive_at_the_exact_
     boundary`, a stepped `cpu_reader` (`0.0` for 30 samples, then
     exactly `1.0`) holding the observed delta at precisely the floor
     value across several ticks — correct `>=` reads that as "growing"
     (plain `TimeoutExpired` at `clock.t == 35.0`); the mutant reads the
     same delta as flat and raises the `hung` subclass at `t == 30.0`.
  3. Removing the `session_finish_at is not None and ...` disjunct: caught
     as BRIEF-4 predicted, by
     `test_session_finish_then_still_alive_is_hung_regardless_of_cpu`. No
     new test needed.
  4. Swapping `LivenessHungExpired` for plain `subprocess.TimeoutExpired`
     in the hung-raise branch: caught as BRIEF-4 predicted, by FOUR tests
     (`test_idle_with_flat_cpu_is_hung`, both new boundary tests above,
     and `test_real_subprocess_thread_join_style_hang_is_killed_and_
     classified_hung`) — broader than BRIEF-4's single-test prediction.
     No new test needed.
- Per BRIEF-4's own explicit rule ("a mutant NOT caught by an existing
  test is a real bug in this session's own test suite — fix the test...
  do not just record it as uncaught, known gap"): mutants 1 and 2 each
  got a real fix (a new, precision-targeted test), not a "known gap"
  note. Zero production-code changes this commit — both fixes are tests.
- `liveness.py` stays 100% line+branch (285 stmts/78 branches) with the
  two new tests added (70 tests total across the three liveness-runner-
  monitor-adjacent files, up from 68). One transient false-99%/1-missing
  coverage reading occurred mid-session after several mutate-restore
  cycles reused the same `.coverage`/`__pycache__` state across different
  file CONTENTS in the same process — a clean re-run after `rm -f
  .coverage` and clearing `__pycache__` confirmed 100%; recorded here so
  a future session does not mistake stale coverage state for a real
  regression.
- File: `tests/test_liveness_runner_monitor.py` only (+87 lines, two new
  tests).

### `5baf2670` — A4: progress stream gains `test` events, `plan.slowest_test_s`/`expect_next_event_within_s`, `candidate.tests_completed` (session 6)

- Fresh successor (session 6), continuing from BRIEF-5. `"test"` added to
  `mutation.PROGRESS_EVENTS`.
- `liveness.py`: extracted the parse loop `compute_expect_next_event_
  within_s` already ran into one generator, `_iter_test_events`, then
  built three new public functions on it — `baseline_slowest_test_s`
  (the SAME figure `compute_expect_next_event_within_s` already computed
  internally, now surfaced so the `plan` event can read it back rather
  than re-derive it, per BRIEF-5's own retention prompt naming this
  exact instruction), `baseline_test_events` (the `{nodeid, outcome,
  duration_s}` triples forwarded to the progress stream), `count_test_
  events` (a candidate's own `tests_completed`). Also extracted
  `LivenessRunner._events_path_for_cwd`'s hashing into a free function,
  `candidate_events_path`, so the WRITER (`LivenessRunner`) and the NEW
  READER (`mutation._run_one`, this session) can never compute two
  different paths for the same `cwd`.
- `mutation.py`: `run_mutation` gains four new optional kwargs
  (`liveness_baseline_events_path`, `liveness_slowest_test_s`,
  `liveness_expect_next_event_within_s`, `liveness_events_dir`), all
  `None` for every non-liveness lane, matching the three pre-existing
  `liveness_active`/`liveness_reason`/`liveness_plugin` kwargs' own shape.
  `plan` gains `slowest_test_s`/`expect_next_event_within_s`. Immediately
  after `plan`, every baseline `test` event is forwarded verbatim
  (`phase: "baseline"`) — RW-33's own "BASELINE only" rule; no candidate
  ever emits one. `_run_one` reads `tests_completed` back from its own
  candidate's events file WHILE `snapshot.project_root` (the path's own
  hash key) is still in scope — computed there, carried on `_MutantRun`,
  read back onto the `candidate` progress event outside the `with` block.
- `runner.py`: `liveness_candidates_dir` named once, shared between the
  `LivenessRunner` construction and the new `run_mutation` kwarg (was two
  separately-typed-out paths before this session). `liveness_slowest_
  test_s` computed via the same `baseline_slowest_test_s` this session
  added, passed through beside the pre-existing `liveness_expect_next_
  event_within_s`.
- Tests-first: yes — every new function in `test_liveness_proc_helpers.py`
  (`baseline_slowest_test_s`/`baseline_test_events`/`count_test_events`/
  `candidate_events_path`, each with `None`/missing-file/torn-last-line/
  non-test-event cases) and every new `run_mutation` behavior in
  `test_mutation_progress_budget_plan.py` (the two new `plan` fields; the
  baseline-forwarding test, incl. `session_finish` exclusion and a torn
  last line; the `tests_completed` test, whose fake `process_runner`
  writes to the EXACT path `candidate_events_path` names — proving
  reader/writer path agreement, not just the counting logic alone) were
  written and run red before the corresponding production line existed.
  Three PRE-EXISTING tests were extended with an explicit assertion on
  the branch they already silently exercised but never checked (`plan`
  event's two new fields `None`, no `test` event, `tests_completed`
  `None`) — the "every new conditional tested with the OTHER optional
  parameter at its default" quality-bar item, made explicit rather than
  left as an implicit side effect of an unrelated assertion.
- Coverage: `liveness.py` **100% line+branch** (297 stmts/80 branches, up
  from 285/78 — self-check via `test_liveness.py` + `test_liveness_proc_
  helpers.py` + `test_liveness_runner_monitor.py`, 84 tests). `mutation.py`
  and `runner.py`: every individual new/changed line confirmed covered
  (none appear in either file's own `missing_lines`) against a 920-test
  combined run (the files above plus `test_progress_phase_stream.py`,
  `test_mutation_hung_bucket.py`, `test_runner_execute.py`, `test_
  mutation_judge.py`, `test_verify_hung_bucket.py`, `test_verdict_reason_
  codes.py`, `test_verdict_conformance.py`, `test_errors.py`, and BOTH
  real e2e liveness CLI tests from session 5 — the only lines either
  file's own coverage report still names as missing are PRE-EXISTING
  equivalence-artifact/kill-signal-artifact/dirt-handling paths this
  commit never touched). Branch coverage specifically confirmed for the
  new `liveness_slowest_test_s = ... if liveness_injected else None`
  ternary in `runner.py`: `True` via the two real e2e tests (liveness
  genuinely active), `False` via every other lane in the same 920-test
  batch — the "OTHER optional parameter at its default" bar, satisfied
  for a `runner.py`-level conditional, not only a `mutation.py`/
  `liveness.py` one.
- Regression, GREEN: 920 tests across the twelve files/two e2e tests named
  above (two separate runs: 128 on the narrow liveness+progress set
  first, then the full 920-test combined run for the coverage cross-
  check — both green, no new failures introduced).
- HOST LOAD: `/proc/pressure/memory` `full avg10` read 0.01–0.22 throughout
  this session's own checks — well under the 5.0 back-off threshold, no
  waiting needed. `nice -n19`/`ionice -c3` for every pytest invocation;
  the 920-test combined run (real e2e subprocesses included) ran ~97s,
  well within the "whole suite at most once per commit that needs it"
  rule (run twice total: once narrow, once combined for coverage).

### `c15f6040` — A5: `--rejudge`/`--rejudge-outcome` (session 6)

- `mutation.py`: `run_mutation` gains `rejudge_ids: frozenset[str]` and
  `rejudge_outcomes: frozenset[str]`, both empty by default (byte-
  identical resume behaviour when neither given). Requires `resume=True`
  (`ValueError` otherwise); `rejudge_outcomes` validated against the real
  `MUTATION_BUCKETS` vocabulary. Inside the resume loop, BEFORE any
  record loads: refuses (`MutationStateError`) an unknown `--rejudge` id
  — one that does not match any of THIS run's own current candidate
  identities, cross-referencing B088 (ids fold in the mutant's own
  source bytes by construction, B066, so a changed source makes the old
  id simply disappear from the current set — indistinguishable from
  "never existed" at the id level). A record matching either selection
  (a union) is dropped before reaching `resumed_records`, falling
  straight through to `pending_jobs` — the same effect as a candidate
  with no record at all, no special code path. `"resume"` progress event
  gains `rejudged_total`.
- `runner.py`: `run_lane` gains `rejudge`/`rejudge_outcome` (raw
  comma-separated strings, unparsed — mirrors `shard`'s own
  `"INDEX/COUNT"` string exactly). Parsed and refused at the SAME point
  `shard`'s own malformed-string check fires — before the R0/higher-
  rigor dispatch, so even an R0-only lane hits it, no command execution
  needed. `"error"` accepted as a documented CLI-level alias for the
  real bucket name `"crashed"` (`Outcome.ERROR` → that bucket via
  `_classify_mutant_result`) — `run_mutation` itself only ever sees
  canonical `MUTATION_BUCKETS` names. Threaded through
  `_run_higher_rigor_lane` → `_run_prepared_lane` → `run_mutation`
  exactly like `resume`/`shard_index`/`shard_count` already are,
  including `run_lane`'s own progress-stream re-entry call.
- `cli.py`: `--rejudge`/`--rejudge-outcome` added to the `run`
  subparser, passed straight through as raw strings.
- Tests-first: yes. 7 new `run_mutation`-level tests in
  `test_mutation_progress_budget_plan.py`: drop-by-id + genuine
  re-execution against a strengthened suite (a `process_runner` that now
  kills what the FIRST run recorded `survived`, proving real execution
  rather than a replayed stale verdict); drop-by-outcome; both selections
  together as a union (not double-executed); unknown id refuses before
  ANY execution (a raising `process_runner` proves nothing ran); the
  `"resume"` event's `rejudged_total`; both `ValueError` paths. 5 new
  `run_lane`-level tests in `test_runner_run_lane.py`, all via the
  file's existing minimal-R0-lane pattern (no real command execution for
  any of them): malformed `--rejudge`, empty `--rejudge-outcome`, an
  unknown bucket name, missing `--resume`, the `"error"`→`"crashed"`
  alias accepted without refusing. One real bug found while writing the
  first rejudge test: `MutantOutcome.identity` is a DIFFERENT,
  tuple-shaped identity (path/span/hash/operator), not the digest string
  `candidate_id()`/`--rejudge` actually take — a test helper
  (`_candidate_id_by_outcome_bucket`) reads the real digest back off the
  PERSISTED state record instead of guessing a conversion between the
  two; flagged here since a future `--rejudge` consumer reading
  `judgment.r2`/`Mutation.survived` for an id to pass back in would hit
  the identical confusion (A6's docs pass should say this explicitly).
- Coverage: every new/changed line in both files confirmed present in
  neither file's own coverage-report `missing_lines` (direct line-number
  cross-reference), against a 144-test combined run (the two new tests'
  own files plus `test_state_dir_resume.py`/`test_mutation_judge_
  identity.py` for the full-CLI threading path — those two exercise the
  new `rejudge_ids=`/`rejudge_outcomes=` argument-passing lines at their
  DEFAULT empty value on every resume-capable run, even though neither
  file passes `--rejudge` itself, which is exactly the "OTHER optional
  parameter at its default" bar for the plumbing layer).
- Regression, GREEN: 228 tests (`test_mutation_progress_budget_plan.py`,
  `test_state_dir_resume.py`, `test_mutation_judge_identity.py`,
  `test_runner_run_lane.py`, `test_liveness.py`, `test_liveness_proc_
  helpers.py`, `test_liveness_runner_monitor.py`).
- HOST LOAD: `/proc/pressure/memory` `full avg10` briefly read 2.91 mid-
  session (the `some avg10` companion figure spiked to 6.26, but the
  binding rule is `full avg10 > 5` — stayed under it throughout);
  `nice -n19`/`ionice -c3` for every invocation; targeted files, run
  twice (once at 228, once for the coverage cross-check).

### `afda58fd` — A6 docs + backlog close-out (session 7)

- Fresh successor (session 7), continuing from BRIEF-6. Single pass over
  ALL FIVE prior deliverables (A2, A3 session 4, A3 session 5, A4, A5) per
  BRIEF-6's own explicit instruction, not incremental.
- `docs/CONSUMERS.md`: progress-stream table gains `plan.liveness`/
  `slowest_test_s`/`expect_next_event_within_s`, a new `test` event row,
  `candidate.tests_completed`, `resume.rejudged_total`, `hung` noted in
  `candidate.outcome_bucket`/`end.buckets`. New section "Liveness for a
  native R2 python/pytest lane" (the `judge.mutation.liveness` policy
  table, `argv_invokes_pytest` rule verbatim from `liveness.py`, the
  `hung` bucket's two detection branches, native-only scope). New section
  "`--rejudge <id>[,...]` / `--rejudge-outcome BUCKET[,...]`" (union/
  drop-once semantics, the `MUTATION_BUCKETS` vocabulary + `"error"`→
  `"crashed"` CLI-only alias, the unknown-id refusal cross-referencing
  B088, and the `candidate_id()`-vs-`MutantOutcome.identity` caveat A5's
  own REPORT section flagged as a judgment call for A6 to resolve —
  resolved here as a documentation caveat, not a new backlog row, per
  that section's own instruction to decide one way).
- `README.md`: one paragraph near the existing B073 discussion noting
  B091 delivered a SCOPED per-test liveness capability (native R2
  python/pytest only) without resolving B073 itself (the general,
  per-language live-progress-parsing feature) — B073 stays open.
- `CHANGES.md` `[Unreleased]`: three new entries (A3, A4, A5) under the
  existing `### Fixed (detail)` header, matching A1/A2's own already-
  present entries' voice and level of detail; checked first per BRIEF-6's
  instruction — A1/A2/RW-36 entries were already present, untouched.
- `docs/DESIGN-GUIDE.md`: read in full (the "Six outcomes" vocabulary
  table and its `BUDGET_EXCEEDED`/`CANDIDATE_HUNG` row, added by session
  4's `44dd12ca`); no gap found — already current, no edit made.
- `assay/nyxloom-trove/4-backlog.md`: **B091 → FIXED 2026-09-12**, all 5
  contract items with their commit hashes (`de32bb91`, `f4fa1788`,
  `e27b107b`, `44dd12ca`, `99463ae5`, `d1540eda`, `5baf2670`, `c15f6040`)
  and a pointer to this LOG/REPORT for the oracle → test mapping.
  **B090 → mitigated-by-B091 note**, explaining how each of its two
  original observations (no default bound; SIGKILLed-candidate
  reclassification/re-judging) is now addressed by which B091 item.
  **B092 filed** (new, end of file) per controller ruling RW-41: the
  `--resume` per-tree identity (B088) is invalidated by a commit to a
  path the lane never judges — the live RG-55 P2 incident (a
  records-only LOG commit at `647a2cc6` rejected all 174 of P2's judged
  candidates) reconstructed in full from RW-41's own text on `main`,
  plus the P1 `5ce232d1` companion instance from the same ruling.
  Mechanism proposed: `judge.mutation.identity_exclude` (path-glob list,
  opt-in, excluded from the tree-content half of `judge_sha256` only —
  never from `argv`/`env`/`cwd`/`link_paths`). Oracles and a severity
  rating (medium) included; NOT implemented, row only, per BRIEF-6's own
  instruction.
- Tests-first: n/a (docs/backlog only, no production code touched this
  commit).
- Gate: not yet run this session — deferred to after the regression
  sweep, per the handoff's own ordering (docs/backlog/sweep committed
  BEFORE the gate starts, so a checkpoint cut stays clean either way).
- HOST LOAD: `/proc/pressure/memory` checked immediately before this
  commit's own work and again before staging: `full avg10` 3.79 (`some
  avg10` 5.24) — under the 5.0 `full` back-off threshold throughout; no
  heavy command run this commit (docs/backlog edits + `git commit` only).

### Deferred full regression sweep (no commit — all green, nothing to fix)

- Ran the handoff's own deferred sweep (BRIEF-6: "no session in this
  package has yet run in full"): `tests/test_mutation*.py`,
  `tests/test_runner*.py`, `tests/test_verdict*.py`, `tests/test_verify*.py`,
  `tests/test_cli*.py` (68 files, including every `test_cli*.py` file, not
  only `test_cli_run.py` as session 4's own 2107-test sweep covered).
  `nice -n 19 ionice -c 3`, serial, single pytest invocation, launched
  backgrounded (task exceeded the 120s foreground limit) with a cheap
  watcher; result read in a separate step from the launch, never a pipe
  tail.
- **Result: 2077 passed, 1 warning, 403.63s (0:06:43).** The one warning is
  a pre-existing `schemathesis`/`jsonschema` `RefResolutionError`
  deprecation notice (a third-party dependency chain, unrelated to this
  package's own changes) — not a new warning this session introduced.
  Nothing red; nothing to fix.
- HOST LOAD: `/proc/pressure/memory` `full avg10` touched 6.38 briefly
  right after the sweep started (other estate sessions' own concurrent
  gate work visible via `ps`: P1/P6-project pytest+coverage runs), settled
  back to 4.24 and then lower within a few checks; never re-checked as a
  reason to abort an already-running sweep (the back-off rule gates
  STARTING new heavy work, not an in-flight one) — no new heavy command
  was started while `full avg10` was above 5.

### `b3f31506` — A6 gate finding fix: W7's locked v11 schema copy re-synced (session 7)

- The real registered gate (`./run-gate.py tester-unified`), launched
  after docs/backlog/sweep were committed, PSI checked (`full avg10`
  3.85, under 5.0), **FAILED, exit 1** on its first run:
  `nyxloom-trove/carve-assets/W7/test_acceptance_v11.py` (2 failed, 108
  passed). A real, previously-shipped gap: A1's `budget_per_candidate_
  derived_s`/`liveness` fields and A3's `hung` bucket each moved
  `src/assay/schemas/verdict.schema.json` without moving W7's own locked
  byte-identical copy (`nyxloom-trove/carve-assets/W7/verdict.schema.
  v11.json`) in the same commit — the discipline that file's own test
  docstring names explicitly ("the guard this project has been bitten by
  TWICE").
- Fix: `cp src/assay/schemas/verdict.schema.json` over the W7 asset.
  Verified locally first (`test_acceptance_v11.py` alone: 110 passed)
  before committing, since the gate builds from an exact-OID clone of
  HEAD (`clean_tree = false` in `run-gate.toml`, with that reason stated)
  — an uncommitted fix would not be picked up by a re-run.
- Checked W1/W2/W4/W5/W6/P33's own locked schema copies (v4-v10): each is
  a historical snapshot of ITS OWN, older schema version, not a
  live-tracking copy of the current v11 schema — confirmed unaffected,
  not touched.
- Tests-first: n/a (a documentation/fixture-asset fix responding to a
  real gate failure, not new production behavior).
- Gate: relaunched after this commit (PSI re-checked, `full avg10` 1.07).
  W7's own suite now passes inside the gate (110 passed, confirmed live
  in the gate's own output at `ASSAY_GATE_PHASE=verdict-v11-successors-
  verified`). Full gate verdict pending — see BRIEF-7 (this session ran
  past its own call budget waiting on the self-hosted container phase's
  wall time, which the checkpoint clause does not count against the call
  budget itself, but the WAITING/polling calls do).
- HOST LOAD: PSI checked before each of the two gate launches (3.85, then
  1.07 — both under the 5.0 threshold). `docker update --cpus=3` applied
  to the gate's own container (`flamboyant_nobel`) immediately after it
  appeared in `docker ps`. Gate containers estate-wide at the time: 2
  (`flamboyant_nobel` + P1's own long-running `run-gate-vbpub-r2-680904-
  ...`) — at, not over, the `<= 2` limit.

### `95d02f50` — session 8 gate finding fix: pyflakes unused imports + RecursionError guard (liveness.py's 2 untrusted JSON parse sites)

- Files: `src/assay/cli.py`, `src/assay/liveness.py`,
  `tests/test_verify_hung_bucket.py`, `tests/test_mutation_hung_bucket.py`.
- The real gate's first full run (captured by session 7, read by session 8
  in a separate step from any launch) ended RED: `5 failed, 4681 passed,
  20 skipped`. This commit fixes 2 of the 5, both genuinely new gaps this
  package's own B091 work introduced, neither previously exercised by A6's
  deferred sweep (`mutation*/runner*/verdict*/verify*/cli*` only):
  `test_distribution_gate.py::test_the_shipped_source_tree_is_pyflakes_clean`
  and
  `test_untrusted_json_parse_sweep.py::test_every_untrusted_json_parse_site_catches_RecursionError`.
- pyflakes: 5 unused imports, each confirmed zero call sites before
  removal (`grep` first) — `cli.py`'s `MUTATION_BUCKETS` (the runner-side
  validation in `mutation.py` is the one live consumer), `liveness.py`'s
  `TYPE_CHECKING`-only `ProcessRunner` (named only in a Sphinx `:class:`
  docstring cross-reference, which pyflakes does not parse), `pytest` in
  both `test_verify_hung_bucket.py` and `test_mutation_hung_bucket.py`,
  and `pathlib.PurePosixPath` in the latter.
- RecursionError sweep: `liveness.py`'s `_iter_test_events` and
  `_read_events_progress` each parse the materialized pytest plugin's own
  NDJSON side-file events, written by a process running inside the
  CANDIDATE's (a consumer project's) own interpreter — untrusted per this
  project's own bar (B072/B074/`provenance.py`: assay's own bytes vs a
  consumer's). Both caught only `ValueError`; `json.loads`'s recursive-
  descent parser raises `RecursionError` (a `RuntimeError` subclass, not a
  `ValueError`) on a deeply-nested but well-formed document. Widened both
  to `except (ValueError, RecursionError):`, matching the one-line shape
  already used at every other guarded site in this codebase.
- Verified: `python3 -m pyflakes src/assay <tests, fixtures/ excluded>`
  clean (matches `run_lint_phase`'s own scope exactly, confirmed by
  reading `tools/tester-unified-gate.sh` first); both previously-red tests
  green alone; `test_liveness.py` + `test_liveness_proc_helpers.py` +
  `test_liveness_runner_monitor.py` + `test_mutation_hung_bucket.py` +
  `test_verify_hung_bucket.py` + `test_runner_run_lane_r2.py` +
  `test_runner_execute.py` + `test_mutation_progress_budget_plan.py` +
  `test_config_mutation.py` + `test_verdict_conformance.py` +
  `test_errors.py` (410 tests) green with `liveness.py` at 100%
  line+branch coverage (`--cov=assay.liveness --cov-branch`);
  `test_cli_run.py` + `test_cli_lanes(_json).py` +
  `test_cli_provenance_and_request_base.py` (106 tests) green.
- Tests-first: n/a (findings from the real gate's own first run; fixes are
  a lint cleanup and a guard widening, not new production behavior).
- HOST LOAD: `/proc/pressure/memory` `full avg10` 0.08-2.13 throughout
  this commit's own work (well under 5.0); `nice -n19`/`ionice -c3` for
  every pytest invocation; serial.

### `ee24ced6` — session 8 gate finding fix: test_standalone.py's real-wheel expected-artifact drift (3 R2 tests)

- Files: `tests/test_standalone.py`.
- The remaining 3 of the gate's 5 failures, all in the same file, all the
  same shape: `_expected_r2_artifact`'s hand-built expected documents were
  stale against two of this package's own additive fields, because none
  of `test_standalone.py`'s real-wheel R2 tests were ever run (by any
  session, in or out of the gate) since A1/A3 shipped them.
  1. A3's `hung` bucket — added `"hung": []` to the 3 affected
     `r2_claim["mutation"]` literals (nothing in these fixtures ever
     hangs).
  2. RW-36's `judgment.r2.liveness` record (`{active, reason, plugin}`,
     present unconditionally) — added `liveness_active`/`liveness_reason`/
     `liveness_plugin` parameters to `_expected_r2_artifact`, defaulted to
     the injection rule's own "off" shape (`argv-does-not-invoke-pytest`),
     since every lane this module's real-wheel tests run is a plain shell
     script (`grep`, `exit 0`, …), never pytest.
  3. A1's `judgment.r2.budget_per_candidate_derived_s` — measured from
     THIS run's own real baseline wall-clock time, so no fixture can
     hand-inject it (same class as the pre-existing top-level `volatile`
     set: `assay_version`/`started`/`ended`). `_assert_complete` now pops
     it from a COPY of the filtered real document (never mutating the
     caller's own `real`/nested dicts) after sanity-checking it
     (`is not None` implied by presence, `> 0`, `math.isfinite`) when
     present — matching `test_runner_run_lane_r2.py`'s own existing
     `is not None`/`> 0` precedent for the same field — and leaves it
     genuinely absent (no check) when `candidate_count == 0`, since no
     baseline-derived budget is ever computed in that case.
  `test_a_real_r2_lane_propagates_an_adverse_baseline_verbatim` (the
  module's 4th `_expected_r2_artifact` caller) needed no change — it
  passes `operators=None`, so `_expected_r2_artifact` never builds a
  `judgment` at all for it, which is also why it was never one of the
  five failures.
- Verified: all three previously-red tests green alone (`-k "... or ... or
  ..."`, `-vv`, full diffs read directly — the `-q`/`-vv` combination
  otherwise truncates; the CI log's own truncation is why this session
  reproduced locally rather than trusting the captured log's shortened
  diff); the whole `test_standalone.py` file green (20 passed, 1 skipped),
  run both before this commit (reproducing the exact 3 CI failures) and
  after (all green).
- Tests-first: n/a (test-fixture-only fix responding to a real gate
  failure against genuinely additive production fields, not new
  production behavior).
- HOST LOAD: `/proc/pressure/memory` `full avg10` stayed under 2.2
  throughout; `nice -n19`/`ionice -c3`; serial; each `standalone` fixture
  build (session-scoped, one wheel per pytest process) completed in
  seconds against the offline `--no-index` closure already present.

### `802f0855` — session 9: round-1 repair B1 -- os._exit sentinel prevents false SURVIVOR

- Files: `src/assay/liveness.py` (`_EXIT_STATUS = None` sentinel;
  `pytest_unconfigure` returns without exiting when unassigned; docstring
  wording fix), `docs/CONSUMERS.md` (same wording fix), `tests/
  test_liveness.py` (3 new plugin-module-level unit tests: both branches
  of the new conditional, plus the `ASSAY_LIVENESS_EXIT` unset case),
  `tests/test_cli_run.py` (1 new real-subprocess end-to-end CLI test).
- Round-1 review finding: `run-gate-WAVE-RG55-P7-REVIEW-round1.md` B1.
  `pytest_unconfigure` used to `os._exit(_EXIT_STATUS)` unconditionally
  once `ASSAY_LIVENESS_EXIT=1`, but `_EXIT_STATUS` started at `0` and was
  only ever reassigned by `pytest_sessionfinish` -- which pytest does NOT
  call when the session never started (a raising `conftest.
  pytest_configure`/`pytest_sessionstart`), while `pytest_unconfigure`
  DOES still fire (`Config._ensure_unconfigure()`). That turned a genuine
  crash into a false `os._exit(0)` -- PASS -- and
  `mutation._classify_mutant_result` then reported `survived` for a
  mutant the suite actually killed.
- Fix: three-line prescription from the review, applied verbatim.
  `_EXIT_STATUS = None` sentinel; `pytest_unconfigure` checks
  `if _EXIT_STATUS is None: return` before flushing/exiting, letting
  pytest's own exit status/path stand on the diverging path.
- Verified:
  - `python3 -m pytest tests/test_liveness.py -k unconfigure -q` -- 3
    passed (both branches of the new conditional exercised directly
    against the materialized plugin module, no real pytest subprocess).
  - `python3 -m pytest tests/test_cli_run.py -k configure_time_raise -q`
    -- 1 passed: the reviewer's own reproduction end to end through the
    real CLI (a `tests/conftest.py` `pytest_configure` hook that calls
    `guard(0)` -- `True` at baseline's `x <= 0`, `False` under the
    `python:compare-swap` mutant `x < 0` -- raising `RuntimeError` before
    any test runs). Real `assay run`, real git repo, real `python -m
    pytest` subprocess twice (R0 baseline + 1 R2 candidate). Asserted
    `mutation["killed"] == [<1 entry>]`, `mutation["survived"] == []`,
    `document["outcome"] == "PASS"` (the R2 claim itself is PASS because
    the ONE candidate was killed, not because the crash was swallowed).
  - Full existing liveness suite (`test_liveness.py` +
    `test_liveness_runner_monitor.py` + `test_liveness_proc_helpers.py`,
    87 tests) green, unaffected.
  - `python3 -m pyflakes src/assay/liveness.py` clean.
- Tests-first: yes for the plugin-module unit tests (written against the
  sentinel's two branches before generalizing); the e2e CLI test is a
  direct reproduction of the review's own measured table, written to
  fail pre-fix (not re-run pre-fix to confirm, given the checkpoint
  clause's time budget -- the plugin-module unit tests above DO directly
  exercise the pre-fix `_EXIT_STATUS = 0` shape's absence of the new
  early-return, which is the regression surface B1 names).
- HOST LOAD: `/proc/pressure/memory` `full avg10` 2.69 before this
  commit's test runs (well under 5.0, no gate container launched this
  session yet); `nice -n19`/`ionice -c3` for every pytest invocation;
  serial.
- CHECKPOINT: session 9 stops here (ARM'd at the checkpoint clause's
  ~60-tool-call threshold, right at this commit's own green boundary --
  B2/B3/B4/B5 and the gate run remain unstarted). See BRIEF-9.
