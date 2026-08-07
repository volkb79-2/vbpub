# assay-P04 — runner, CLI, and verdict emission — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P04-runner-cli-verdict-emission`
**Commit:** `352cab50` (the implementation commit this LOG describes; this LOG
itself lands in a small follow-up commit that only fills in this hash).
**Base:** `main` at `bfc467b8` ("rule(assay): P04 readiness findings -- A-094/A-095, land before dispatch").

## Gate

`tester-unified`, run in the FOREGROUND against HEAD with the container-side
path substituted for the host bind mount:

```
$ cgroup_parent="dev-background.slice"
$ docker run --rm --cgroup-parent="$cgroup_parent" \
    -w /workspaces/vbpub/.worktrees/assay-P04-runner-cli-verdict-emission/assay \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q --cov=src/assay --cov-branch --cov-report=term-missing'
........................................................................ [  8%]
........................................................................ [ 16%]
........................................................................ [ 24%]
........................................................................ [ 32%]
........................................................................ [ 40%]
........................................................................ [ 48%]
........................................................................ [ 56%]
........................................................................ [ 64%]
........................................................................ [ 72%]
........................................................................ [ 80%]
........................................................................ [ 88%]
........................................................................ [ 96%]
..............................                                          [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/assay/__init__.py                               10      0      0      0   100%
src/assay/cli.py                                    78      0     18      0   100%
src/assay/config.py                                294      0    146      0   100%
src/assay/coverage.py                               32      0      6      0   100%
src/assay/coverage_parsers/__init__.py               1      0      0      0   100%
src/assay/coverage_parsers/cobertura.py             44      0     16      0   100%
src/assay/coverage_parsers/coverage_py_json.py      44      0     18      0   100%
src/assay/coverage_parsers/go_cover.py              69      0     32      0   100%
src/assay/coverage_parsers/lcov.py                  61      0     26      0   100%
src/assay/coverage_parsers/model.py                 16      0      0      0   100%
src/assay/diff.py                                   36      0     16      0   100%
src/assay/errors.py                                 56      0      4      0   100%
src/assay/git.py                                    28      0      8      0   100%
src/assay/measurability.py                          23      0      4      0   100%
src/assay/runner.py                                 82      0     12      0   100%
src/assay/verdict.py                               293      0    146      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             1167      0    452      0   100%
894 passed in 9.62s
GATE_EXIT=0
```

Baseline before this package: 848 passed, 1047 stmts / 426 branches, 100%.
This package adds 46 tests, 120 statements, 26 branches — all still 100%
statement and branch coverage.

`git status --porcelain --ignored` after the run shows only intended changes
(`src/assay/cli.py`, `src/assay/runner.py` new, `tests/conftest.py`,
`tests/test_cli_lanes.py`, four new fixture files, five new test modules)
plus ignored caches (`.coverage`, `.pytest_cache`, `.hypothesis`,
`__pycache__`) — nothing else left in the worktree.

## Delivered

| Work item | File | Notes |
|---|---|---|
| 1, 2 | `src/assay/runner.py` (new) | `ProcessRunner` (Protocol, the injectable process seam), `default_process_runner` (the real `subprocess.run` boundary, exported so tests can drive it directly), `CommandPlan`/`CommandResult` (frozen `kw_only`), `resolve_command_plan` (pure), `execute_command` (the discrete R0 step, A-094), `build_r0_claim` |
| 2 | `src/assay/runner.py` | `assemble_verdict` — the discrete final-verdict-assembly step (A-094), separable from `execute_command`, taking the WHOLE `claims` tuple so P05 can pass `(r0_claim, r1_claim)` without restructuring |
| 3 | `src/assay/runner.py` | `write_verdict` — atomic (temp file + `os.replace`, injectable `replace`), `"-"` for stdout (A-028) |
| — | `src/assay/verdict.py`, `src/assay/schemas/verdict.schema.json` | **Untouched.** R0 claims (`rigor="R0"`, no coverage payload, `COMMAND_FAILED`/`EXEC_FAILED`/`LANE_TIMEOUT` already in the closed reason-code sets per A-073/A-050) were already fully representable by P01b's model and schema — confirmed empirically by reusing `budget_exceeded.json` verbatim as one of O4's fixtures (see below). Nothing to add. |
| 2, 3 | `src/assay/cli.py` | `run` subcommand: `lane` positional, `--file`, `--verdict-json PATH\|-`; `_split_appended_argv` (the `--` convention); `_cmd_run` (wiring: rigor gate, `git.head_rev`, `execute_command`→`build_r0_claim`→`assemble_verdict`→optional `write_verdict`); `_print_run_summary` |
| 4 | `tests/fixtures/verdicts/r0_*.json` (4 new files) | hand-written, independent of `assay.verdict`/`assay.runner`, per A-041/A-080 |
| 4 | `tests/fixtures/verdicts/budget_exceeded.json` | **reused, not duplicated** — P01b's own fixture, already an R0-only single-claim `BUDGET_EXCEEDED` verdict |
| — | `tests/conftest.py` | `make_lane` (a `Lane` built directly, bypassing `assay.toml`/`tomllib`, for runner-level unit tests), `fixed_clock`, `RUNNER_VERDICT_FIXTURES`/`runner_verdict_fixture` |
| — | `tests/test_cli_lanes.py` | one stale test updated (see "Interpretation decisions" below) |
| — | 5 new test modules, 46 tests | `test_runner_execute.py` (O1), `test_runner_plan_env.py` (O2), `test_runner_artifact_write.py` (O3), `test_runner_verdict_fixtures.py` (O4), `test_cli_run.py` (CLI wiring) |

## The discrete R0 step (A-094) — exact shape

```python
# assay/runner.py
def execute_command(
    lane: Lane, *, argv_append: Sequence[str] = (), cwd: Path,
    passthrough_source: Mapping[str, str] | None = None,
    process_runner: ProcessRunner = default_process_runner,
    clock: Clock = _utc_now,
) -> CommandResult: ...

def build_r0_claim(result: CommandResult) -> Claim: ...

def assemble_verdict(
    *, lane: Lane, commit: str, result: CommandResult,
    claims: tuple[Claim, ...], assay_version: str,
) -> Verdict: ...
```

`execute_command` never raises for an ordinary R0 outcome (append rejected,
missing executable, budget expired, command failed, command passed) — it
always returns a `CommandResult`. `assemble_verdict` takes the WHOLE `claims`
tuple, not just R0's, so P05 does not restructure anything: it calls
`execute_command`/`build_r0_claim`, inserts its own R1 evaluation, and calls
`assemble_verdict(claims=(r0_claim, r1_claim), ...)`.

## Per-oracle evidence

Every mutation below was applied to the file, its presence confirmed with
`grep -c` before the test run, then reverted and re-verified clean (a second
`grep -c` returning 0 and `git status --porcelain -- <files>` showing no diff)
before moving to the next (A-067). All mutation runs used the local
interpreter (`PYTHONPATH=src python3 -m pytest tests -q`) for iteration
speed, verified identical to `tester-unified` at baseline (848→894, matching
counts) and at the final green run above.

### O1 — exit 0 is PASS; nonzero is FAIL/COMMAND_FAILED; missing executable is ERROR/EXEC_FAILED; expired budget is BUDGET_EXCEEDED/LANE_TIMEOUT

* **Mutation 1 (nonzero exit mapped to EXEC_FAILED instead of COMMAND_FAILED)**
  — changed the final `return CommandResult(...)` branch's
  `outcome=Outcome.FAIL, reason_code=ReasonCode.COMMAND_FAILED` to
  `outcome=Outcome.ERROR, reason_code=ReasonCode.EXEC_FAILED`.
  `grep -c "outcome=Outcome.ERROR,\n        reason_code=ReasonCode.EXEC_FAILED,\n        returncode=proc.returncode,"` confirmed via direct file inspection (multi-line grep counting was unreliable; visual `sed -n` confirmed the single landing site).
  **Real result: 8 failed** — `test_cli_run.py::test_run_executes_a_failing_lane_and_exits_one`,
  `test_cli_run.py::test_run_writes_verdict_json_to_stdout_when_dash`,
  `test_runner_execute.py::test_nonzero_exit_is_fail_command_failed[1,2,7,42]` (4 parametrizations),
  `test_runner_execute.py::test_build_r0_claim_reflects_a_fail_result`,
  `test_runner_verdict_fixtures.py::test_r0_only_fail_command_failed_matches_the_hand_written_fixture`.
* **Mutation 2 (universal PASS regardless of returncode)** — changed
  `if proc.returncode == 0:` to `if True:`.
  `grep -c "MUTATION: universal PASS"` → 1.
  **Real result: 9 failed** — the same 8 as above, plus (as a side effect)
  `test_a_hard_coded_constant_pass_would_fail_the_fail_fixture_comparison`
  itself errored with `KeyError: 'reason_code'`, because the document it
  tries to force into looking like a constant-PASS bug was *already* a real
  PASS under the mutation, so the key it expected to delete was never there
  — a second-order confirmation, not a clean assertion failure, still a real
  detection.
* **Mutation 3 (budget expiry mapped to EXEC_FAILED instead of LANE_TIMEOUT)**
  — changed the `except subprocess.TimeoutExpired:` branch's outcome/reason
  to `ERROR`/`EXEC_FAILED`. `grep -n` confirmed the single landing site.
  **Real result: 2 failed** —
  `test_runner_execute.py::test_budget_expiry_is_budget_exceeded_lane_timeout_via_injection`,
  `test_runner_verdict_fixtures.py::test_r0_only_budget_exceeded_matches_p01bs_own_hand_written_fixture`.

### O2 — the child receives exactly lane env plus declared passthrough, no ambient sentinel; argv is exact; append without permission never runs

* **Mutation 1 (merge the whole passthrough source instead of only declared
  names)** — changed `env_effective: dict[str, str] = dict(lane.env)` +
  the `for name in lane.env_passthrough: if name in source: ...` loop to
  `dict(source)` then `.update(lane.env)`. `grep -c "MUTATION: merge whole
  source"` → 1. **Real result: 9 failed** — 4 in `test_runner_plan_env.py`
  (including the REAL-subprocess `/usr/bin/env` proof), 5 in
  `test_runner_verdict_fixtures.py` (every producer test whose `env_declared`
  fixture is non-trivial or whose CI environment leaked real host variables
  into `env_effective`, changing the JSON).
* **Mutation 2 (omit append bookkeeping — `argv_effective` drops the
  appended tokens)** — changed `argv_effective=argv_declared + argv_appended`
  to `argv_effective=argv_declared`. `grep -c "MUTATION: omit append
  bookkeeping"` → 1. **Real result: 6 failed**, including
  `Verdict.__post_init__`'s OWN `ValueError` firing for
  `test_run_permits_an_append_when_allowed` (the model's cross-field check
  catches this independently of any test-level assertion — a genuine
  second line of defense, not double-counting the same failure).
* **Mutation 3 (disable the append-permission gate)** — changed
  `if plan.argv_appended and not lane.allow_argv_append:` to
  `if False and plan.argv_appended and not lane.allow_argv_append:`.
  `grep -c "MUTATION: gate disabled"` → 1. **Real result: 3 failed** —
  critically, `test_cli_run.py::test_run_rejects_an_append_without_allow_argv_append`
  failed at `assert code == 2` with `assert 0 == 2`: exit code 0 means the
  sentinel command actually RAN (a real `/bin/sh -c 'touch "$0"'` process,
  not a mock), which is the literal, observable proof of O2's negative
  ("ignoring the permission runs the sentinel command") rather than an
  inference from internal state.

### O3 — atomic writes on every outcome; no artifact without `--verdict-json`; an injected replacement failure preserves the old artifact

* **Mutation 1 (write in place, no temp file + `os.replace`)** — collapsed
  `write_verdict`'s body to a direct `path.write_text(...)`, deleting the
  temp-file/`replace` machinery entirely. `grep -c "MUTATION: write in
  place"` → 1. **Real result: 2 failed** —
  `test_an_injected_replacement_failure_preserves_the_old_artifact` and
  `test_an_injected_replacement_failure_with_no_prior_artifact_leaves_none`
  (the second failed with `DID NOT RAISE OSError`, since the injected
  `replace` was never even called — writing in place bypasses the injected
  failure boundary entirely, exactly the defect O3 exists to catch).
* **Mutation 2 (write only on PASS, in `cli.py`)** — changed
  `if args.verdict_json is not None:` to
  `if args.verdict_json is not None and verdict.outcome is Outcome.PASS:`.
  `grep -c "MUTATION: success-only"` → 1. **Real result initially only 1
  failed** (`test_run_writes_verdict_json_to_stdout_when_dash`) — this
  surfaced a genuine gap: my original CLI tests proved artifact-on-non-PASS
  only via `--verdict-json -`, never via a real file PATH for a non-PASS
  outcome. I added
  `test_run_writes_the_verdict_to_a_file_path_on_a_non_pass_outcome_too`
  before reverting, re-ran with the mutation still active, and got
  **2 failed** confirming the new test closes the gap; the mutation was then
  reverted and the full suite re-confirmed green (894 passed) with the new
  test in place. This is recorded here rather than silently fixed, because
  it is exactly the kind of coverage hole self-review exists to find.
* **Mutation 3 (always write, even without `--verdict-json`)** — changed
  the write gate to `if True:`, inventing a path when the flag was omitted.
  `grep -c "MUTATION: always write"` → 1. **Real result: 1 failed** —
  `test_run_without_verdict_json_creates_no_artifact` (the directory
  snapshot picked up the invented file).

### O4 — every produced branch matches an independent hand-written fixture; a constant-PASS or field-dropping producer fails the comparison

* **Mutation 1 (constant PASS from `assemble_verdict`)** — changed
  `outcome = rollup(statuses)` to `outcome = Outcome.PASS`.
  `grep -c "MUTATION: assemble_verdict emits a constant PASS"` → 1.
  **Real result: 9 failed** — `Verdict.__post_init__`'s own rollup-agreement
  check raised `ValueError` first (a second, independent line of defense at
  the model layer), which is what four of the CLI/runner-fixture tests
  actually observed as their failure; the remaining fixture-comparison tests
  and `test_a_hard_coded_constant_pass_would_fail_the_fail_fixture_comparison`
  also failed for real.
* **Mutation 2 (omit the claims array entirely)** — changed
  `claims=claims,` to `claims=(),` in `assemble_verdict`.
  `grep -c "MUTATION: omit the claims entirely"` → 1.
  **Real result: 15 failed** — the broadest of any mutation in this
  package, spanning every CLI-run test and every O4 fixture-comparison test,
  again via `Verdict`'s own "claims must cover declared_rigor" guard firing
  first in most cases.

## Self-review

### Would each oracle's test fail if the behaviour were removed?

Yes for all four, demonstrated above by 11 mutations (not estimated), each
with its presence confirmed before the run and its absence confirmed after
revert. Every mutation produced at least one real failure; several (O2
mutation 1, O3 mutations 1–3, O4 both mutations) produced failures spanning
multiple independent test modules, meaning the coverage is not concentrated
in one brittle assertion.

### What is MISSING from the diff the handoff asked for

Nothing in `## Work`. Items 1–5 are honoured as written:

1. **Injectable process/budget boundary** — `ProcessRunner` (a `Protocol`)
   and `Clock`; the real `subprocess`/`datetime.now` are the defaults
   (`default_process_runner`, `_utc_now`); `EXEC_FAILED` and
   `BUDGET_EXCEEDED` are proven in `test_runner_execute.py` purely through
   injected replacements raising `FileNotFoundError`/`PermissionError`/
   `subprocess.TimeoutExpired` — no real missing binary, no real wait.
2. **R0 execution, discrete from verdict assembly (A-094)** —
   `execute_command`/`build_r0_claim` vs. `assemble_verdict`, as detailed
   above; append-without-permission is `ERROR`/`EXEC_FAILED` before the
   process starts (A-095), proven both by an injected spy (`called == []`)
   and by a REAL marker-file execution proof at the CLI level.
3. **Verdicts emitted atomically, only on `--verdict-json`** — `write_verdict`
   covers all six outcomes structurally (parametrized over a directly-built
   PASS and a directly-built `NO_MEASUREMENT`, matching
   `build_no_measurement()`'s own construction style, never driven through
   the runner, per the handoff's explicit instruction).
4. **Hand-written complete fixtures for every new branch** — four new files
   plus P01b's `budget_exceeded.json` reused verbatim, all validated
   independently against the packaged schema (`validator` fixture, never a
   dict literal).
5. **Mutation evidence for result mapping, environment isolation, and
   atomicity** — the eleven mutations above.

### What I implemented that the handoff did not ask for, with justification

* **The `assay run` rigor gate** (`lane.rigor != ("R0",)` → `ERROR`/
  `BAD_LANE_CONFIG` before anything runs). Not named by any oracle. Without
  it, running `assay run` against a lane declaring `rigor = ["R0", "R1"]`
  (legal per `config.py`'s own loader) would reach `assemble_verdict` with
  only an R0 claim, and `Verdict.__post_init__`'s "claims must cover
  declared_rigor exactly" check would raise an uncaught `ValueError` —
  correct in the sense that it is honest (no invented R1 judgement), but a
  raw traceback instead of a clean exit code, which the whole verdict
  contract (§6: "the exit code IS the verdict") exists to prevent. This is
  the one decision I had to interpret beyond A-092/A-094/A-095; see below.
* **`default_process_runner` exported publicly** (not `_`-prefixed). Needed
  so `test_runner_plan_env.py`'s real-subprocess env-isolation proof can
  call it directly with a controlled env, rather than only reaching it
  indirectly through `execute_command`.
* **`_split_appended_argv`'s `--` convention** for CLI argv append. Not
  specified by the handoff (which only fixed the flag name, `--verdict-json`,
  per A-028 — it says nothing about how appended args reach the CLI). I
  chose the `docker run`/`kubectl exec`/`npm run --` convention: everything
  after the first literal `--` token is the caller's payload, verbatim,
  never reinterpreted by argparse.
* **`--verdict-json -` for stdout**, per A-028's own text ("or `-` for
  stdout") which O3's oracle text does not mention explicitly. Included
  because A-028 is a cited, binding decision and the cost was small; when
  `-` is given the human summary is suppressed so stdout carries pure JSON
  for a scripted consumer (e.g. `assay run lane --verdict-json - | jq
  .outcome`).
* **Updated `tests/test_cli_lanes.py::test_cli_exposes_only_the_lanes_subcommand`**,
  renamed to `test_cli_still_rejects_subcommands_not_yet_shipped` and
  re-pointed at `verify` instead of `run`. The original test's own comment
  ("`run`, `verify` and `mutate` are P07's and later") became false the
  moment this package shipped `run`; its assertion (`main(["run"])` raises
  `SystemExit`) would have kept passing anyway — for the wrong reason
  (missing positional argument, not "unknown subcommand") — which is exactly
  the "green test masking a behaviour change" hazard AUTHORING.md §3b.C
  warns about. Left unfixed, it would have been a landmine for whoever next
  touched the CLI's subcommand list.

### Known-weak spots, stated plainly

* **The R0-only rigor gate is a CLI-level choice, not something any oracle
  named.** A different implementer could reasonably have chosen to let the
  `ValueError` propagate (arguing it is "not yet supported, full stop") or
  to invent a different mechanism. I chose the gate because an uncaught
  `ValueError` crash contradicts §6's own "the exit code is the verdict"
  claim more directly than a clean, typed refusal does. Flagged for the
  controller in case a different resolution is preferred; nothing else in
  this package depends on this specific choice — `assemble_verdict` accepts
  any `claims` tuple, so removing the gate would not require touching
  `runner.py`.
* **`write_verdict` does not create parent directories** for
  `--verdict-json some/new/dir/verdict.json`; a missing parent directory
  raises `FileNotFoundError` uncaught (not converted to an `AssayError`,
  since no closed reason code fits "cannot write my own output"). Untested
  directly — the one CLI test that writes to a real path uses `tmp_path`
  itself as the parent, avoiding this. Not a defect against any A-092/A-094/
  A-095 requirement, but a real gap if a consumer names a path whose
  directory does not yet exist.
* **A failed atomic replace, and any other unexpected write failure, is not
  mapped to a closed `(Outcome, ReasonCode)` pair** — it propagates as a raw
  `OSError`/exception from `main()`. Deliberate: none of `ERROR`'s five
  reason codes fits "assay's own output write failed" (`UNREADABLE_ARTIFACT`
  is for *reading* an artifact, not writing the verdict itself), and A-092's
  own escalate_if language is "an outcome cannot be represented by the
  closed reason vocabulary — stop and ask" rather than "invent one anyway."
  I judged this too rare and too clearly still "assay crashed" (rather than
  "assay rendered an outcome") to warrant escalation, but it is worth a
  second opinion if a future package finds this path reachable in practice.
* **The `run` subcommand's human-readable stdout summary is not tested for
  its exact wording**, only for the substrings the tests need (outcome/
  reason label, commit). No oracle specifies its format, and DESIGN-GUIDE
  §6 only says "stdout is for humans" without prescribing a shape.

### Decision ids I could not honour as written

None. A-092 (frozen `kw_only` dataclasses, `AssayError` raised directly, no
locally-defined exception type) is honoured throughout `runner.py` — `grep -n
"^class.*Error" src/assay/runner.py` finds nothing, and every rejection
(`resolve_command_plan`'s append gate lives inside `execute_command`, which
never raises for an ordinary R0 outcome — see the module docstring) returns a
typed `CommandResult` rather than raising, which is a stronger, not weaker,
form of the same discipline: no exception at all for a judged outcome, only
for the two genuinely structural failures (`lane.rigor` gate, `git.head_rev`
failure) that happen before any verdict could exist. A-094 and A-095 are
discharged as detailed above.
