# B110-P3c — Liveness window lane keys with guardrails, and their v14 disclosure

| Field | Value |
|---|---|
| Backlog | **B114** (B110 umbrella) |
| Branch | `assay-b110-p3c-liveness` off the current `assay-b110-v14` tip. The integration protocol is in `P3a-v14-schema-verify.md`. |
| Depends on | **P3a** merged into `assay-b110-v14`: the liveness 5-key model and schema, with defaults emitted. **P2** merged into the integration line and then into `assay-b110-v14`: P2 adds a watchdog to the same two `tests/test_cli_run.py` tests edited here. If P2 is not in `assay-b110-v14` yet, ask the controller to merge the integration line into it first. Never rebase. |
| Contract class | **2c**: bounded integration across config → runner → liveness → verdict against fixed contracts. |
| Implementer | Sonnet (fresh session) |
| Decisions | **A-469** (D5) and A-470 (the v14 cut) |
| Size | M |

**What this package is.** Two optional lane keys let a native Python R2 lane set the liveness monitor's CPU window and idle floor:
- `judge.mutation.liveness_cpu_window` (duration);
- `judge.mutation.liveness_idle_floor` (duration).

They come with guardrails. The effective values are disclosed in v14 `judgment.r2.liveness`. **Omitting both keys reproduces today's behaviour exactly (30 s / 15 s), and the B105 lanes do not declare them.** The only in-repo users are test lanes, which is how the two slow liveness tests in `tests/test_cli_run.py` stop costing 66.7 s per full-suite run.

---

## Context to read first

Paths are relative to `assay/`, verified at `db85f747`.

- `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md` §3 D5 and §5 (the `liveness` block).
- `nyxloom-trove/reports/b110/P3a-v14-schema-verify.md`: the "Changes to existing classes" paragraph about the `liveness` mapping.
- `src/assay/liveness.py`:
  - 523-526: `_HUNG_CPU_WINDOW_S = 30.0`, `_HUNG_CPU_GROWTH_FLOOR_S = 1.0`, `_HUNG_SESSION_FINISH_GRACE_S = 30.0`, `_LIVENESS_POLL_INTERVAL_S = 1.0`;
  - 641: `LIVENESS_IDLE_FLOOR_S = 15.0`; 646: `LIVENESS_FALLBACK_FLOOR_S = 60.0`;
  - 950-1013: `compute_liveness_calibration`, which uses the floor at 1006 and 1009;
  - 1183: `_CpuSampleHistory.__init__(window_s)`;
  - 1237-1335: `LivenessRunner` and its `__init__` (1299-1335; it already takes `poll_interval_s` and `cpu_reader`);
  - 1455-1540: `_monitor`. `_CpuSampleHistory(_HUNG_CPU_WINDOW_S)` is at 1458, growth at 1494-1498 (`>= _HUNG_CPU_GROWTH_FLOOR_S`), the hung predicate at ~1516-1520 and the timeout at ~1530.
- `src/assay/config.py`:
  - 384: `parse_duration`;
  - 438-450: `_MUTATION_OPTIONAL_FIELDS`;
  - 556-567 and 617-645: the `MutationConfig.liveness` field and `as_declared` (`payload["liveness"]` at 641);
  - 3144-3185: liveness tri-state parsing and the RW-36 load-time refusal;
  - 3218-3229: the `MutationConfig(...)` construction;
  - 3277-3290: the ingested `orchestration_only` refusal set.
- `src/assay/runner.py`:
  - 3796-3800: the `compute_liveness_calibration` call;
  - 4418-4430: `LivenessRunner(...)`;
  - 4964-5041: `_build_judgment_r2`. The liveness dict is at 5036-5040, extended by P3a with `cpu_window_s`/`idle_floor_s` defaults.
- `src/assay/mutation.py`: ~2220-2260, the `plan` progress event (event names are closed; keys are free).
- `tests/test_liveness_runner_monitor.py`: 195-215. This is the monkeypatch precedent (`_HUNG_CPU_WINDOW_S` → 2.0).
- `tests/test_cli_run.py`:
  - 463-585: `test_run_liveness_classifies_a_thread_join_hang_as_hung`, whose lane TOML has `budget_per_candidate = "50s"` at 529;
  - 586-676: `test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung`, whose lane TOML has `"35s"` at 645. Its docstring explains why the budget sits above the 30 s window;
  - 127-130: in-process `main`;
  - the exact `judgment.r2.liveness` dict pins at ~443 and ~577.
- `tests/test_cli_lanes_json.py`: the `assay lanes --json` echo shapes.
- The docs: `docs/CONSUMERS.md` 2440-2490 ("trailing 30 s window", "never … sooner than 30 s"), `docs/DESIGN-GUIDE.md` 470-500, `README.md` 364.

---

## Implementation packet (normative)

### Interfaces and grammar

**Lane keys.** Both are optional TOML strings in `parse_duration` grammar, under `[lanes.<name>.judge.mutation]`:

```toml
liveness_cpu_window = "5s"     # default when omitted: 30s (liveness._HUNG_CPU_WINDOW_S)
liveness_idle_floor = "5s"     # default when omitted: 15s (liveness.LIVENESS_IDLE_FLOOR_S)
```

**Validation** (`config.py`, right after the liveness block ends at ~3185). The error type is `LaneConfigError`, which renders `BAD_LANE_CONFIG`. Tests assert these message substrings:

| Input | Refusal substring |
|---|---|
| not a string, or `parse_duration` fails | `'judge.mutation.liveness_cpu_window' ` + the parser message (same for idle floor) |
| value < 5.0 s | `'judge.mutation.liveness_cpu_window' must be at least 5s` / `'judge.mutation.liveness_idle_floor' must be at least 5s` |
| non-finite | the same parser path (`parse_duration` already refuses) |
| either key present while `liveness` is `false` (`LIVENESS_FALSE`) | `'judge.mutation.liveness_cpu_window' has no effect when judge.mutation.liveness = false` (dead config is refused, following B026's lesson) |
| either key on an **ingested** lane | add both names to the `orchestration_only` set at 3279; the existing message applies |

- An omitted `liveness` (auto) or `true` accepts the keys.
- The minimum of 5 s is 5 × the 1 s poll interval, and gives a growth floor of 5/30 ≈ 0.167 CPU-seconds per window.

**`MutationConfig` (`config.py`)** gains:
- `liveness_cpu_window: str | None = None`;
- `liveness_idle_floor: str | None = None`.

These are the **declared strings**. The parsed seconds are derived at use: add the properties `liveness_cpu_window_s -> float` and `liveness_idle_floor_s -> float`, which return the parsed value or the liveness-module default. `as_declared()` echoes each key **only when declared**, as the verbatim string, like the `liveness` key at 641. `_MUTATION_OPTIONAL_FIELDS` gains both names.

**`liveness.py`:**
- `compute_liveness_calibration(..., idle_floor_s: float = LIVENESS_IDLE_FLOOR_S)`. It replaces `LIVENESS_IDLE_FLOOR_S` at 1006 and 1009 with `idle_floor_s`. `LIVENESS_FALLBACK_FLOOR_S` (60 s) is **unchanged**.
- `LivenessRunner.__init__(..., cpu_window_s: float = _HUNG_CPU_WINDOW_S)` stores `self._cpu_window_s` and `self._cpu_growth_floor_s = cpu_window_s * (_HUNG_CPU_GROWTH_FLOOR_S / _HUNG_CPU_WINDOW_S)`. That is `window / 30`, exactly `1.0` at the default.
- `_monitor` uses `_CpuSampleHistory(self._cpu_window_s)` at 1458 and `>= self._cpu_growth_floor_s` at 1498.
- The module constants stay, as the defaults. `_HUNG_SESSION_FINISH_GRACE_S` (30 s) is **not** configurable in this package.

**`runner.py`:**
- At 3796: `compute_liveness_calibration(..., idle_floor_s=lane.judge.mutation.liveness_idle_floor_s)`.
- At 4420: `LivenessRunner(..., cpu_window_s=lane.judge.mutation.liveness_cpu_window_s)`.
- `_build_judgment_r2` gains the kwargs `liveness_cpu_window_s: float | None` and `liveness_idle_floor_s: float | None`. The liveness dict emits them when `liveness_active` is true and `None` when inactive. This matches the P3a model rule.
- The `plan` progress event gains `"cpu_window_s"` and `"idle_floor_s"`, both `null` when liveness is inactive. The value is passed into `run_mutation` as a new kwarg, `liveness_windows: tuple[float, float] | None = None`.

### Serialized examples

**Valid `judgment.r2.liveness`, declared test lane:**
```json
{"active": true, "reason": null, "plugin": "assay_liveness_plugin", "cpu_window_s": 5.0, "idle_floor_s": 5.0}
```
Use the current `plugin` value; copy it from an existing pin.

**Valid, keys omitted:**
```json
{"active": true, …, "cpu_window_s": 30.0, "idle_floor_s": 15.0}
```

**Valid, inactive:**
```json
{"active": false, "reason": "language-not-python", "plugin": null, "cpu_window_s": null, "idle_floor_s": null}
```

**Invalid lane configurations:**
- `liveness_cpu_window = "4s"`;
- `liveness = false` + `liveness_idle_floor = "10s"`;
- `liveness_cpu_window = 5` (not a string);
- on an ingested lane.

**`assay lanes --json`** for a lane that declares both keys echoes `"liveness_cpu_window": "5s", "liveness_idle_floor": "5s"` inside `judge.mutation`. Omitted keys are absent.

### Required flow

1. Load: parse, validate, construct `MutationConfig` with the declared strings.
2. Runner: calibrate with `idle_floor_s`; construct `LivenessRunner` with `cpu_window_s`; pass both to `run_mutation` (the plan event) and to `_build_judgment_r2` (the disclosure).
3. Everything else in the liveness state machine is unchanged, including the `hung` predicate `idle_for >= bound and not cpu_growing`, the session-finish grace, and the timeout.

### Decision table

| `liveness` | keys | Load | Monitor | Disclosure |
|---|---|---|---|---|
| omitted / `"auto"` / `true` | none | ok | 30 s window, growth floor 1.0, idle floor 15 | 30.0 / 15.0 if active, else null |
| omitted / `"auto"` / `true` | declared ≥ 5 s | ok | declared window, floor = window/30, declared idle floor | the declared seconds if active, else null |
| any | a declared value < 5 s | `BAD_LANE_CONFIG` | — | — |
| `false` | any declared | `BAD_LANE_CONFIG` | — | — |
| ingested lane | any declared | `BAD_LANE_CONFIG` (`orchestration_only`) | — | — |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| grammar and refusals | config | one test per decision-table refusal row, asserting the substrings | `tests/test_liveness_lane_keys.py` (new) | drop the `false` refusal → the dead-config lane loads → red |
| `as_declared` echo | config / cli | `assay lanes --json` has both keys verbatim when declared, and neither when omitted | `tests/test_cli_lanes_json.py` | always echo the defaults → the omitted case shows keys → red |
| window used by the monitor | liveness | `LivenessRunner(cpu_window_s=5.0)` with no progress events, an injected `cpu_reader` and an injected `monotonic`/`sleep` (**no real time**). A CPU sequence growing 0.05 s per 1 s poll (0.25 s per 5 s window, above the scaled floor of 0.167) → **not** hung. A sequence growing 0.02 s per poll (0.10 per window, below 0.167) → hung once idle ≥ the bound | `tests/test_liveness_runner_monitor.py` style | keep the absolute 1.0 floor → the 0.05-per-poll case is classified hung → red |
| default exactness | liveness | `LivenessRunner()` with no kwarg has window 30.0 and floor 1.0, and the existing monitor tests stay green unchanged | existing | — |
| idle floor in calibration | liveness | `compute_liveness_calibration(..., idle_floor_s=5.0)` with a tiny `worst_gap` → a bound of 5.0. The default call → 15.0 | `tests/test_liveness.py` style unit | ignore the kwarg → 15.0 → red |
| disclosure | runner / verdict | a real small lane with keys 5s/5s → the verdict's `judgment.r2.liveness` equals the valid example, and `assay verify` gives `[]`. Without keys → 30.0/15.0 | `tests/test_cli_run.py` pins at ~443/~577, updated | emit the lane strings instead of floats → a verify/model failure → red |
| slow tests shortened | tests | the two `test_cli_run.py` tests declare `liveness_cpu_window = "5s"` and `liveness_idle_floor = "5s"` in their **test lane** TOML, with the budgets lowered to `"20s"` (the hang test, previously `"50s"`) and `"12s"` (busy loop, previously `"35s"`; still > window). Assertions are unchanged: `hung` and `budget_exceeded` respectively | `tests/test_cli_run.py` 463-676 | — (P2's watchdog stays) |

**Why the shortened tests still assert the contract.**
- The hang test's child joins a thread that never ends. CPU is flat, so `hung` fires once idle ≥ 5 s and a full 5 s window shows no growth. The 20 s budget leaves ≥ 2 windows before the budget.
- The busy-loop child grows about 1 CPU-second per wall second, far above 5/30, so it is never `hung`. It runs to the 12 s budget, and `budget_exceeded` is asserted.
- Neither assertion depends on elapsed time. Each depends on which predicate fires first, which the window/budget ratio fixes. Under starvation below 3.3% of a core, the busy loop could read as `hung`. That is a TRUE red per §3b (the host could not run it), not a flake to widen.

### Degrees of freedom

Property names on `MutationConfig` beyond those given, and private helper structure. The lane key names, the minima, the scaling formula and the disclosure keys are fixed.

---

## Work

1. Create a worktree under `/workspaces/vbpub/.worktrees/`, on `assay-b110-p3c-liveness` from `assay-b110-v14`. Check that `grep -n "cpu_window_s" src/assay/verdict.py` shows P3a's model. If not → BLOCKED.
2. **Red first.** Write `tests/test_liveness_lane_keys.py` (the config rows, `as_declared`, the monitor window and floor scaling, the calibration floor) and extend the `test_cli_lanes_json.py` cases. Run them and record the failures.
3. `config.py`: add to `_MUTATION_OPTIONAL_FIELDS`; parse and validate after 3185; add the `MutationConfig` fields and properties; extend `as_declared`; extend the ingested `orchestration_only` set at 3279.
4. `liveness.py`: the calibration `idle_floor_s` kwarg; the `LivenessRunner` `cpu_window_s` kwarg with the derived growth floor; `_monitor` uses the instance values.
5. `runner.py` / `mutation.py`: thread the values into calibration, `LivenessRunner`, the plan event and `_build_judgment_r2`.
6. Shorten the two `tests/test_cli_run.py` tests as the table specifies, and update their exact liveness-dict pins to include `cpu_window_s`/`idle_floor_s`. Update any other pins P3a set to 30.0/15.0 where a lane now declares keys.
7. Docs sync and CHANGES.
8. Run the focused tests, the gate, and write the report.

## Oracles

The traceability rows are the oracles. Each has its observable, the controlled break as its negative, and the gate (tester-unified). The oracles that must be demonstrated red first are:
- the `false` + key refusal;
- the floor scaling (the 0.05-per-poll case);
- the disclosure floats.

Additionally, `tests/test_self_lane.py` stays green unchanged. The B105 lanes declare no key, and their `assay lanes --json` echo is unchanged.

### Forbidden oracle patterns (AUTHORING.md §3b, verbatim)

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

Grep each anchor first.

- **README.md :364.** The "30 s" statements become "the lane's CPU window (default 30 s)"; the post-finish grace stays 30 s. List both keys in the lane-key reference, if README has one (`grep -n "budget_per_candidate" README.md`).
- **docs/DESIGN-GUIDE.md ~470-500.** Explain why the window is configurable, why the growth floor scales (a fixed 1.0 s floor over a short window would demand 33% of a core, which the contention-agnostic rule forbids), why the minimum is 5 s, and that short windows are for tests and fast suites, never a speed knob for a contended campaign. State that A-464 is unchanged: time never classifies. The monitor only distinguishes "no progress while able to run" from "still running".
- **docs/CONSUMERS.md 2440-2490.**
  - "less than 1.0 s over the trailing 30 s window" → "less than window/30 CPU-seconds over the trailing window (default 30 s → 1.0 s)".
  - "never … sooner than 30 s" → "never sooner than the lane's idle floor and CPU window (defaults 15 s and 30 s)".
  - Add a pasteable TOML example.
- **CHANGES.md** `## [Unreleased]` `### Added`.

## Scope / forbid

- **Touch:** `src/assay/{config.py,liveness.py,runner.py,mutation.py}`, `tests/test_liveness_lane_keys.py` (new), `tests/test_cli_run.py` (the two tests plus their pins only), `tests/test_cli_lanes_json.py`, `tests/test_liveness_runner_monitor.py` (additive only), README / DESIGN-GUIDE / CONSUMERS / CHANGES.
- **Forbid:**
  - `verdict.py`, `verify.py` and the schema (P3a owns them; if the model rejects your floats → BLOCKED);
  - `mutation_witness.py`;
  - `assay.toml` (the B105 lanes must not declare the keys);
  - `_HUNG_SESSION_FINISH_GRACE_S`;
  - `LIVENESS_FALLBACK_FLOOR_S`;
  - any change to the `hung` predicate's shape.

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Run the focused suites serially:
   `nice -n 19 ionice -c3 python -m pytest tests/test_liveness_lane_keys.py tests/test_liveness_runner_monitor.py tests/test_liveness.py tests/test_cli_lanes_json.py tests/test_cli_run.py tests/test_self_lane.py -q -p no:cacheprovider`
2. Run the gate:
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p3c-gate.log 2>&1; echo EXIT=$?`
3. In a separate step, read `ASSAY_GATE_CONTAINER_EXIT=0` and `ASSAY_REGISTERED_GATE_COMPLETE=1`.
4. Never run the `self-qualification` lane.

## BLOCKED rule

If a named contract cannot be met as specified, STOP. This applies when:
- P3a's liveness model is absent or rejects the specified shape;
- P2's watchdog is not in the branch;
- a forbidden file must change.

Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B110-P3c-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

## Report

Write `nyxloom-trove/reports/assay-B110-P3c-REPORT.md` with:
- the commits;
- the traceability table with actual tests and red-first counts;
- the before/after call durations of the two shortened tests, as information only;
- the gate log path and markers;
- residuals.

Commit trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`. Do not merge; a fresh-session review (never a fork) precedes the controller's `--no-ff` merge into `assay-b110-v14`.
