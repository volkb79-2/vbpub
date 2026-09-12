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
