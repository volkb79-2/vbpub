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
