# B110-P3c — Liveness window lane keys with guardrails, and their v15 disclosure

## Current contract reconciliation — 2026-10-07

The disclosure target is verdict v15; v14 shipped in Assay 8.0.0. This work is
part of the single CIU-managed `assay-b114-cold-witness` branch. The old P1
test-move map is stale after Wave A W1/W4. B113's scanner guards shipped in
Assay 7.2.0; W6 explicitly dropped its watchdog item after the two named
real-child tests disappeared. B112's remaining slow-tier decision is pending
current timing evidence and does not block P3c. See
`../assay-B114-PLAN-2026-10-07.md`.

The current P3c scope is limited to the two lane settings, their validation,
runtime use, v15 disclosure, and deterministic tests. Do not restore the old
P1 test moves, shorten the deleted real-child tests, or add a watchdog for
them. The test paths and branch protocol in the historical package text below
are not current-tree instructions.

**Revised 2026-09-28 after round-1 and round-2 reviews (see REVIEW-2026-09-28-round1.md, REVIEW-2026-09-28-round2.md).** Round 1 applied P3C-1..P3C-8, the wrong anchors, and carver decision C17. Round 2 applied P3C2-1, P3C2-2 and C30:
- the properties return `float | None`;
- `LivenessRunner` exposes public `cpu_window_s`/`cpu_growth_floor_s`;
- `LivenessCalibration` gains `idle_floor_s`;
- the impossible runtime-inactive fixture is dropped;
- tests are found by name after P1.

| Field | Value |
|---|---|
| Backlog | **B114** (B110 umbrella) |
| Branch | CIU-managed `assay-b114-cold-witness`; the historical v14 integration-branch protocol is superseded for this serial implementation. |
| Depends on | P3a's v15 model/schema/verify in this same worktree. B111/P0 and B113/P2 already shipped in Assay 7.2.0; the obsolete P1 test moves are not required. |
| Contract class | **2c**: bounded integration across config → runner → liveness → verdict against fixed contracts. |
| Implementer | Sonnet (fresh session) |
| Decisions | **A-469** (D5), A-470 (the v14 cut), and carver decision **C17** (`REVIEW-2026-09-28-round1.md`) |
| Size | M |

**What this package is.** Two optional lane keys let a native Python R2 lane set the liveness monitor's CPU window and idle floor:
- `judge.mutation.liveness_cpu_window` (duration);
- `judge.mutation.liveness_idle_floor` (duration).

They come with guardrails. The effective values are disclosed in v15 `judgment.r2.liveness`.

**Omitting both keys reproduces today's behaviour exactly (30 s / 15 s), and the B105 lanes do not declare them.** A drift pin enforces this (O-B105 below).

The only in-repo users are test lanes. This is how the two slow liveness **smoke tests** stop costing about 66.7 s per full-suite run. After P1 they live in `tests/zz_slow/test_cli_run_real_campaigns.py`: `test_run_liveness_classifies_a_thread_join_hang_as_hung` and `test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung`. Locate them **by test name**, never by line.

**What the oracles are.** The deterministic oracles of this package are the fake-clock monitor unit tests, the calibration unit test and the config and disclosure tests. The two real-child tests are smoke tests of end-to-end wiring. Their wall-clock behaviour is never the proof.

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
  - `_monitor` starts at **1441**. `_CpuSampleHistory(_HUNG_CPU_WINDOW_S)` is at 1458, growth at 1494-1498 (`>= _HUNG_CPU_GROWTH_FLOOR_S`), the hung predicate at ~1516-1520 and the timeout at ~1530.
- `src/assay/config.py`:
  - 384-403: `parse_duration`. It refuses `<= 0` only; a very long digit string parses to `inf` (**not** refused today);
  - 348-351: `_DURATION_RE`;
  - 438-450: `_MUTATION_OPTIONAL_FIELDS`;
  - 556-567 and 617-645: the `MutationConfig.liveness` field and `as_declared` (`payload["liveness"]` at 641);
  - 3144-3185: liveness tri-state parsing. The RW-36 load-time refusal at **3165-3185** uses the predicate `language == "python" and argv_invokes_pytest(argv)` (3172). That predicate decides whether liveness *can ever* activate on the lane;
  - 3218-3229: the `MutationConfig(...)` construction;
  - 3277-3290: the ingested `orchestration_only` refusal set.
- `src/assay/runner.py`:
  - 3796-3800: the `compute_liveness_calibration` call;
  - 4418-4430: `LivenessRunner(...)`;
  - 4964-5041: `_build_judgment_r2`. The liveness dict is at 5036-5040, extended by P3a with `cpu_window_s`/`idle_floor_s` defaults.
- `src/assay/mutation.py`: ~2220-2260, the `plan` progress event. Event names are closed; keys are free. `worst_gap_s` and `expect_next_event_within_s` are already emitted at 2251-2254.
- `tests/test_liveness_runner_monitor.py`:
  - 195-230: the monkeypatch precedent (`_HUNG_CPU_WINDOW_S` → 2.0 at 209; pin `clock.t == 5.0` at 227);
  - 259-350: the exact-boundary growth test, which references `liveness._HUNG_CPU_GROWTH_FLOOR_S` at 343/348.

  All must stay green **unchanged**. See the interface below: the `None` default is resolved at construction time, so the monkeypatch still takes effect.
- **The two real-child smoke tests**, found by name. After P1 they are in `tests/zz_slow/test_cli_run_real_campaigns.py`; at `db85f747` they were in `tests/test_cli_run.py` at 463 and 586:
  - `test_run_liveness_classifies_a_thread_join_hang_as_hung`: its lane TOML has `budget_per_candidate = "50s"`. Its exact `judgment.r2.liveness` dict pin was at `test_cli_run.py:577-582` (active, `reason`, `plugin`).
  - `test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung`: its lane TOML has `"35s"`, and its docstring explains why the budget sits above the 30 s window. It has **no** exact liveness-dict pin; it asserts `active`/`reason` fields at `test_cli_run.py:673-674`.
  - The dict pin at `test_cli_run.py:443` belongs to a **different** test, with liveness inactive. P3a already migrated it to the 5-key inactive shape. Do not touch it.
  - `test_cli_run.py:127-130`: in-process `main`. P1 duplicates this `run` helper into the split file.
- `tests/test_cli_lanes_json.py`: the `assay lanes --json` echo shapes.
- The docs:
  - `docs/CONSUMERS.md` 2440-2490 ("trailing 30 s window", "never … sooner than 30 s");
  - `docs/DESIGN-GUIDE.md` 470-500;
  - `README.md` **654-660**, the mutation-lane keys paragraph ("Every mutation lane may declare optional `budget_per_candidate` …"). `README.md:364` is the post-finish grace text, which stays 30 s and must **not** change.

---

## Implementation packet (normative)

### Interfaces and grammar

**Lane keys.** Both are optional TOML strings in `parse_duration` grammar, under `[lanes.<name>.judge.mutation]`:

```toml
liveness_cpu_window = "6s"     # default when omitted: 30s (liveness._HUNG_CPU_WINDOW_S)
liveness_idle_floor = "5s"     # default when omitted: 15s (liveness.LIVENESS_IDLE_FLOOR_S)
```

**Validation** (`config.py`, right after the liveness block ends at ~3185). The error type is `LaneConfigError`, which renders `BAD_LANE_CONFIG`. Checks run in the order below, per key (window first, then idle floor), and the first failure refuses. Tests assert these message substrings (`<key>` is the quoted key name):

| # | Input | Refusal substring |
|---|---|---|
| 1 | either key on an **ingested** lane | add both names to the `orchestration_only` set at 3279; the existing message applies |
| 2 | not a string, or `parse_duration` raises | `'judge.mutation.<key>' ` + the parser message |
| 3 | parsed value not finite (`not math.isfinite(v)`, e.g. a 400-digit `"…s"` that parses to `inf`) | `'judge.mutation.<key>' must be a finite duration` |
| 4 | value < 5.0 s | `'judge.mutation.<key>' must be at least 5s` |
| 5 | value > 3600.0 s (`"1h"`) | `'judge.mutation.<key>' must be at most 1h` |
| 6 | either key present while `liveness` is `false` (`LIVENESS_FALSE`) | `'judge.mutation.<key>' has no effect when judge.mutation.liveness = false`. Dead config is refused, following B026's lesson. |
| 7 | either key present on a lane where liveness **can never activate**, i.e. the RW-36 predicate `language == "python" and argv_invokes_pytest(argv)` (config.py:3172) is false, whatever `liveness` says | `'judge.mutation.<key>' has no effect: liveness can never activate on this lane`. `liveness = true` on such a lane is already refused by RW-36 before this row. |

- An omitted `liveness` (auto) or `true`, on a python lane whose argv invokes pytest, accepts the keys.
- The minimum of 5 s is 5 × the 1 s poll interval and gives a growth floor of 5/30 ≈ 0.167 CPU-seconds per window.
- The maximum of 1 h keeps the disclosure finite and the window shorter than any realistic `budget_per_candidate`.

**Runtime-inactive lanes.** `inject_liveness_plugin` (`liveness.py:~430-470`) goes inactive only in three cases:
- `liveness = false` (`declared-false`);
- `true` with a non-pytest argv;
- auto with a non-pytest argv (`argv-does-not-invoke-pytest`).

Rows 6 and 7 (plus RW-36) refuse the keys at load in exactly those cases, so a lane that **declares** the keys can never run with liveness inactive; that combination is impossible (round-2 P3C2-2). The inactive disclosure (`cpu_window_s: null`, `idle_floor_s: null`, P3a's rule) therefore applies only to lanes that do **not** declare the keys, for example a non-python lane (`language-not-python`) or `liveness = false`. That case is pinned by the "Valid, inactive" example and the existing inactive exact-dict pin at `test_cli_run.py` ~409-443.

**Fallback path.** When the calibration falls back to `max(60, baseline/4)` (no plugin events; `liveness.py:996-1002`), the idle floor is **not** applied. The disclosed `idle_floor_s` is the **declared/effective configuration value** passed to calibration, not a claim that it bounded this run. DESIGN-GUIDE states this.

**`MutationConfig` (`config.py`)** gains:
- `liveness_cpu_window: str | None = None`;
- `liveness_idle_floor: str | None = None`.

These are the **declared strings**. The parsed seconds are derived at use: add the properties `liveness_cpu_window_s -> float | None` and `liveness_idle_floor_s -> float | None`. They return the parsed declared value, or **`None` when undeclared**. They never return a liveness-module default; that resolution belongs to `liveness.py` (round-2 P3C2-1). `as_declared()` echoes each key **only when declared**, as the verbatim string, like the `liveness` key at 641. `_MUTATION_OPTIONAL_FIELDS` gains both names.

**`liveness.py`** (carver decision C17):
- New module constant, directly under 524:

  ```python
  _HUNG_CPU_GROWTH_FRACTION = 1.0 / 30.0  # growth floor per second of CPU window
  ```

  `_HUNG_CPU_GROWTH_FLOOR_S = 1.0` and `_HUNG_CPU_WINDOW_S = 30.0` **stay**, because tests reference them (`test_liveness_runner_monitor.py:209, 343, 348`). A new unit test pins `_HUNG_CPU_WINDOW_S * _HUNG_CPU_GROWTH_FRACTION == _HUNG_CPU_GROWTH_FLOOR_S` (exactly `1.0`, verified in float).
- `compute_liveness_calibration(..., idle_floor_s: float | None = None)`. `None` resolves **at call time** to the module attribute `LIVENESS_IDLE_FLOOR_S`. It replaces `LIVENESS_IDLE_FLOOR_S` at 1006 and 1009 with the resolved value. `LIVENESS_FALLBACK_FLOOR_S` (60 s) is **unchanged**.
- **`LivenessCalibration` (the NamedTuple at `liveness.py:844`; constructed only at 998 and 1004 in `src/`) gains a trailing field `idle_floor_s: float`** (round-2 P3C2-1). It is the resolved floor that calibration used: the declared value, or `LIVENESS_IDLE_FLOOR_S` at call time.
  - It is set on **both** return paths. The fallback path also records the resolved value, even though that path does not apply it; see "Fallback path" above.
  - Append it last, so positional construction and unpacking elsewhere keep working. Grep `LivenessCalibration(` and fix every constructor in `src/` and `tests/`.
- `LivenessRunner.__init__(..., cpu_window_s: float | None = None)`. In `__init__`:

  ```python
  window = _HUNG_CPU_WINDOW_S if cpu_window_s is None else cpu_window_s   # module lookup at construction
  self._cpu_window_s = window
  self._cpu_growth_floor_s = window * _HUNG_CPU_GROWTH_FRACTION           # the ONE formula; never window / 30
  ```

  Add **public read-only properties** `LivenessRunner.cpu_window_s -> float` and `LivenessRunner.cpu_growth_floor_s -> float` (round-2 P3C2-1). The runner and the tests read the effective window and floor through them, never through the private attributes (§3b-C).

  The `None` default is **not** `= _HUNG_CPU_WINDOW_S` in the signature, because that binds at definition time and would ignore the existing monkeypatch at `test_liveness_runner_monitor.py:209`. With construction-time lookup, that test sees window 2.0 and floor 2.0 × (1/30). Its flat CPU still never grows, so its `clock.t == 5.0` pin holds unchanged.
- `_monitor` uses `_CpuSampleHistory(self._cpu_window_s)` at 1458 and `>= self._cpu_growth_floor_s` at 1498.
- `_HUNG_SESSION_FINISH_GRACE_S` (30 s) is **not** configurable in this package.

**`runner.py`:**
- At 3796: `compute_liveness_calibration(..., idle_floor_s=lane.judge.mutation.liveness_idle_floor_s)`.
- At 4420: `LivenessRunner(..., cpu_window_s=lane.judge.mutation.liveness_cpu_window_s)`.
- `_build_judgment_r2` gains the kwargs `liveness_cpu_window_s: float | None` and `liveness_idle_floor_s: float | None`. The liveness dict emits them when `liveness_active` is true and `None` when inactive, matching the P3a model rule.
- The `plan` progress event gains **top-level** keys `"cpu_window_s"` and `"idle_floor_s"`, beside the existing `worst_gap_s` and `expect_next_event_within_s` (`mutation.py:2251-2254`). They are **not** nested inside a `liveness` sub-object, and both are `null` when liveness is inactive. The values reach `run_mutation` through a new kwarg, `liveness_windows: tuple[float, float] | None = None`, in the fixed order **`(cpu_window_s, idle_floor_s)`**.

`MutationConfig` properties `liveness_cpu_window_s` and `liveness_idle_floor_s` return the parsed declared value, or `None` when undeclared. The `None` then resolves in `liveness.py` as above. The runner passes the **effective resolved values** to the plan event and the disclosure:
- `candidate_runner.cpu_window_s`, the public property on the constructed `LivenessRunner`;
- `calibration.idle_floor_s`, the new `LivenessCalibration` field.

It **never** passes a re-derived default, and never a private attribute (round-2 P3C2-1).

### Serialized examples

**Valid `judgment.r2.liveness`, declared test lane** (window 6s, floor 5s):
```json
{"active": true, "reason": "auto-pytest-argv", "plugin": "<copy from the existing pin>", "cpu_window_s": 6.0, "idle_floor_s": 5.0}
```
- `reason` is `"auto-pytest-argv"` for an omitted/auto `liveness` and `"declared-true"` for `liveness = true` (`liveness.py:449, 468`). It is **never** `null` while active: `verdict.py` requires a non-empty string.
- Use the current `plugin` value, copied from an existing pin.

**Valid, keys omitted:**
```json
{"active": true, "reason": "auto-pytest-argv", "plugin": "<…>", "cpu_window_s": 30.0, "idle_floor_s": 15.0}
```

**Valid, inactive:**
```json
{"active": false, "reason": "language-not-python", "plugin": null, "cpu_window_s": null, "idle_floor_s": null}
```

**Invalid lane configurations** (one test each):
- `liveness_cpu_window = "4s"` (row 4);
- `liveness_idle_floor = "2h"` (row 5);
- `liveness_cpu_window` set to a 400-digit seconds string that parses to `inf` (row 3);
- `liveness = false` + `liveness_idle_floor = "10s"` (row 6);
- a python lane whose argv is `["/bin/sh", "-c", "true"]`, with omitted `liveness` and `liveness_cpu_window = "6s"` (row 7);
- `liveness_cpu_window = 5`, not a string (row 2);
- either key on an ingested lane (row 1).

**`assay lanes --json`**, for a lane that declares both keys, echoes the declared strings **verbatim** inside `judge.mutation`. Use a non-canonical spelling in the fixture, e.g. `"liveness_cpu_window": "0m6s"`, so that re-formatting would be caught. Omitted keys are absent.

### Required flow

1. Load: parse, validate, construct `MutationConfig` with the declared strings.
2. Runner: calibrate with `idle_floor_s`; construct `LivenessRunner` with `cpu_window_s`; pass both to `run_mutation` (the plan event) and to `_build_judgment_r2` (the disclosure).
3. Everything else in the liveness state machine is unchanged, including the `hung` predicate `idle_for >= bound and not cpu_growing`, the session-finish grace, and the timeout.

### Decision table

| `liveness` | lane can activate (RW-36 predicate) | keys | Load | Monitor | Disclosure |
|---|---|---|---|---|---|
| omitted / `"auto"` / `true` | yes | none | ok | 30 s window, growth floor `30 × (1/30) = 1.0`, idle floor 15 | 30.0 / 15.0 if active at run time, else null |
| omitted / `"auto"` / `true` | yes | declared, 5 s ≤ v ≤ 1 h, finite | ok | declared window, floor = `window × _HUNG_CPU_GROWTH_FRACTION`, declared idle floor | the effective seconds if active at run time, else null |
| any | any | a declared value that is non-finite, < 5 s or > 1 h | `BAD_LANE_CONFIG` (rows 3–5) | — | — |
| `false` | any | any declared | `BAD_LANE_CONFIG` (row 6) | — | — |
| omitted / `"auto"` | **no** | any declared | `BAD_LANE_CONFIG` (row 7) | — | — |
| `true` | no | any | `BAD_LANE_CONFIG` (existing RW-36, before row 7) | — | — |
| ingested lane | — | any declared | `BAD_LANE_CONFIG` (row 1, `orchestration_only`) | — | — |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| grammar and refusals | config | one test per refusal row 1–7, asserting the substrings. Rows 3 (`inf`), 5 (> 1 h) and 7 (non-pytest argv) each have their own case | `tests/test_liveness_lane_keys.py` (new) | drop the `isfinite` check → the 400-digit value loads → red; drop row 7 → the `/bin/sh` lane loads → red |
| `as_declared` echo | config / cli | `assay lanes --json` has both keys **verbatim** when declared (fixture spelling `"0m6s"`) and neither when omitted | `tests/test_cli_lanes_json.py` | re-format to `"6s"` → red; always echo defaults → the omitted case shows keys → red |
| window used by the monitor | liveness | `LivenessRunner(cpu_window_s=6.0)` with no progress events, an injected `cpu_reader` and an injected `monotonic`/`sleep` (**no real time**; the `_FakeClock`/`_ScriptedProc` harness of `test_liveness_runner_monitor.py`). Growth floor = `6 × (1/30) = 0.2`. A CPU sequence growing 0.05 s per 1 s poll (0.30 per 6 s window, above 0.2) → **not** hung. A flat sequence with `expect_next_event_within_s = 8.0` → `LivenessHungExpired`, fired **exactly** at the pinned tick `clock.t == 8.0` (idle reaches the bound after the 6 s window already decided not-growing) | new cases in `tests/test_liveness_runner_monitor.py` (additive) | keep the absolute 1.0 floor → the 0.05-per-poll case is hung → red; an off-by-one 7 s window changes nothing at 8.0, but the companion case below catches it |
| window length pinned | liveness | flat CPU, `cpu_window_s=6.0`, `expect_next_event_within_s=3.0`, so idle crosses first and CPU is still undecided → hung fires exactly at **`clock.t == 6.0`**, the first tick with a full 6 s history | same file | a 7 s window → fires at 7.0 → red; a 5 s window → fires at 5.0 → red |
| formula pin | liveness | `_HUNG_CPU_WINDOW_S * _HUNG_CPU_GROWTH_FRACTION == _HUNG_CPU_GROWTH_FLOOR_S`. For `cpu_window_s=5.75` the instance floor equals `5.75 * _HUNG_CPU_GROWTH_FRACTION` bit-for-bit | `tests/test_liveness_lane_keys.py` | use `window / 30` → differs in the last bit at 5.75 → red |
| default exactness | liveness | `LivenessRunner()` with no kwarg: the **public** `cpu_window_s == 30.0` and `cpu_growth_floor_s == 1.0`. With `_HUNG_CPU_WINDOW_S` monkeypatched to 2.0 before construction: `cpu_window_s == 2.0`. The existing monitor tests, including `:209`'s monkeypatch and `:259-350`'s boundary test, stay green **unchanged**. No private attribute is read (§3b-C, round-2 P3C2-1) | existing plus one new case | a signature default `= _HUNG_CPU_WINDOW_S` → the monkeypatched construction reports 30.0 and `:209` ignores the monkeypatch → red |
| idle floor in calibration | liveness | `compute_liveness_calibration(..., idle_floor_s=5.0)` with a tiny `worst_gap` → a bound of 5.0, and **`calibration.idle_floor_s == 5.0`**. The default call → 15.0 and `idle_floor_s == 15.0`. The fallback path (no events) → bound `max(60, baseline/4)`, with `idle_floor_s` still the resolved value (5.0 / 15.0), recorded but not applied | unit, `tests/test_liveness.py` style | ignore the kwarg → 15.0 → red; omit the field on the fallback path → `AttributeError`/`TypeError` → red |
| idle floor threaded by the runner | runner | on the declared-keys smoke lane (window 6s, floor 5s), the `plan` progress event satisfies `expect_next_event_within_s == max(3.0 * worst_gap_s, idle_floor_s)` and `idle_floor_s == 5.0`, `cpu_window_s == 6.0`. This compares two disclosed values from the same event and involves **no timing** | the hang smoke test's progress stream (add `--progress` to its invocation) | the runner forgets to pass `idle_floor_s` → the relation uses 15 → red whenever `3 × worst_gap < 15`. The fixture must assert `3.0 * worst_gap_s < 15.0` first and skip-fail loudly (pytest.fail with a message, **not** a skip) if not, so the oracle is never vacuous |
| disclosure | runner / verdict | the declared-keys lane → `judgment.r2.liveness == {"active": True, "reason": "auto-pytest-argv", "plugin": <pin>, "cpu_window_s": 6.0, "idle_floor_s": 5.0}` and `assay verify` gives `[]`. Without keys → 30.0/15.0 | the hang smoke test's exact dict pin (was `test_cli_run.py:577-582`) | swap window and floor → 5.0/6.0 → red; emit the lane strings instead of floats → a verify/model failure → red |
| B105 defaults pinned | tests | `tests/test_self_lane.py` asserts that neither `self-qualification` nor `self-qualification-preflight` declares `liveness_cpu_window` or `liveness_idle_floor` (O-B105) | `tests/test_self_lane.py` (additive) | add a key to a B105 lane → red |
| smoke tests shortened | tests | the two real-child smoke tests declare `liveness_cpu_window = "6s"` and `liveness_idle_floor = "5s"` in their **test lane** TOML. Budgets: hang test `"25s"` (was `"50s"`), busy loop `"14s"` (was `"35s"`; still > window). Assertions unchanged: `hung` and `budget_exceeded` | `tests/zz_slow/test_cli_run_real_campaigns.py`, found by name | — (P2's watchdog stays and is not modified) |

**What the smoke tests prove, and what they do not.** They are **smoke tests of the wiring**. They show that the declared keys reach the real monitor, the real plugin and the real verdict on a real child. They are not the classification oracle; the fake-clock monitor tests above are.

- **Hang smoke test.** The child joins a thread that never ends, so CPU stays flat. `hung` needs `idle ≥ max(3 × worst_gap, 5)` and a full 6 s window of no growth.
  - The budget is `"25s"`, deliberately **below 30 s**. If the window were silently not threaded (still 30 s), the flat-CPU history could never fill before the budget. The test would then get `budget_exceeded`, not `hung`, and go red. So the smoke test still guards the wiring.
  - 25 s tolerates a baseline `worst_gap` of up to about 6.3 s: `3 × worst_gap` must stay under `25 − 6`.
- **Busy-loop smoke test.** The child grows about 1 CPU-second per wall second, far above 0.2 per 6 s window, so it is never `hung`. It runs to the `"14s"` budget, and `budget_exceeded` is asserted.
- **Host contention.** A-464 and the operator's contention-agnostic rule apply: host load must never *produce* a classification.
  - A starved host could still make the busy-loop smoke test read `hung`, below about 3.3% of a core for a whole 6 s window. That is recorded here as a **known limit of a real-child smoke test**, not a TRUE red to be fixed by widening, and not a product defect.
  - The deterministic classification contract is carried by the fake-clock oracles.
  - If the smoke tests flake on the gate host, the remedy is BLOCKED plus a controller decision. Never a longer window in the B105 lanes, and never a weakened assertion.

### Degrees of freedom

Property names on `MutationConfig` beyond those given, and private helper structure. Everything else is fixed: the lane key names, the minimum/maximum/finite rules, the row order, the scaling constant and formula, the `None`-default resolution, the plan-event key placement and tuple order, and the disclosure keys.

### Carver tracer bullet (done before dispatch; the implementer re-runs step 2 red-first)

- `python3 -c "print(30.0*(1.0/30.0), 5.75*(1.0/30.0), 5.75/30)"` printed `1.0 0.19166666666666665 0.19166666666666668`. This confirms the default is exact and that `window/30` differs in the last bit, so one formula is pinned.
- `_DURATION_RE` (`config.py:348-351`) accepts `"0m6s"` (6.0 s), which is the verbatim-echo fixture spelling.
- A 400-digit seconds string passes `_DURATION_RE` and `float()` gives `inf`, which the `seconds <= 0` check (`config.py:401`) does not refuse. Hence row 3.

---

## Work

1. Create a worktree under `/workspaces/vbpub/.worktrees/`, on `assay-b110-p3c-liveness` from `assay-b110-v14`. Check all of these, and stop with BLOCKED if any fails:
   - `grep -n "cpu_window_s" src/assay/verdict.py` shows P3a's model;
   - `grep -n "def test_run_liveness_classifies_a_thread_join_hang_as_hung\|def test_run_liveness_classifies_a_busy_loop" tests/zz_slow/test_cli_run_real_campaigns.py` finds both smoke tests. If they are not there: "P1 move missing" → BLOCKED;
   - P2's watchdog is present in that file.
2. **Red first.** Write `tests/test_liveness_lane_keys.py` (config rows 1–7, the formula pin, `as_declared`) and the new monitor cases in `tests/test_liveness_runner_monitor.py` (additive: window, window-length pin, floor scaling). Extend the `test_cli_lanes_json.py` cases and add the B105 no-keys pin to `tests/test_self_lane.py`. Run them and record the failure counts.
3. `config.py`:
   - add both names to `_MUTATION_OPTIONAL_FIELDS`;
   - parse and validate after 3185 in the row order above (`import math` for `isfinite`);
   - add the `MutationConfig` fields and properties;
   - extend `as_declared`;
   - extend the ingested `orchestration_only` set at 3279.
4. `liveness.py`:
   - add `_HUNG_CPU_GROWTH_FRACTION`;
   - add the calibration `idle_floor_s: float | None = None` kwarg;
   - add the `LivenessRunner` `cpu_window_s: float | None = None` kwarg, resolved in `__init__`, with the one formula;
   - `_monitor` uses the instance values.
5. `runner.py` / `mutation.py`: thread the values into calibration, `LivenessRunner`, the plan event (top-level keys) and `_build_judgment_r2`.
6. In `tests/zz_slow/test_cli_run_real_campaigns.py`, found by name:
   - declare the keys (6s / 5s) and the budgets (25s / 14s) in the two smoke tests' lane TOML;
   - make the hang smoke test pass `--progress` and assert the plan-event threading relation;
   - update its exact liveness-dict pin.

   Do not touch the watchdog. Update any other pins P3a set to 30.0/15.0 only where a lane now declares keys.
7. Docs sync and CHANGES.
8. Run the focused tests, the preflight, the gate, and write the report.

## Oracles

The traceability rows are the oracles. Each has its observable, the controlled break as its negative, and the gate. The oracles that must be demonstrated **red first** are:
- refusal rows 3, 6 and 7;
- the floor scaling (the 0.05-per-poll case);
- the window-length pin;
- the formula pin;
- the runner-threading relation;
- the disclosure floats with distinct 6/5 values.

**O-B105.** `tests/test_self_lane.py` asserts that the B105 lanes declare no liveness-window key, and their `assay lanes --json` echo is unchanged. Everything else in that file stays green unchanged.

**Combined-axis fixtures** (each is a test):
1. `liveness` omitted, a `/bin/sh` argv on a python lane, and both keys declared → row 7 refusal. This path had no test before.
2. Window 6s with idle floor 5s → catches a swapped disclosure or wrong threading.
3. The existing `:209` monkeypatched test run under the new signature → still `clock.t == 5.0`.

(Round-1 fixture 4, "keys declared and the plugin inactive at run time", was **dropped** in round 2 (P3C2-2). Rows 6 and 7 plus RW-36 refuse the keys in every case where `inject_liveness_plugin` would go inactive, so the combination cannot be constructed. Fixture 1 covers the refusal side, and the existing inactive pin covers the undeclared-inactive disclosure.)

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

- **README.md 654-660** (the mutation-lane keys paragraph, "Every mutation lane may declare optional `budget_per_candidate` …"). Add both keys: their defaults (30 s / 15 s), the 5 s–1 h range, and that they are refused when liveness is `false` or can never activate. **Do not** edit `README.md:364`; the post-finish grace stays 30 s and is not configurable.
- **docs/DESIGN-GUIDE.md ~470-500.** Explain:
  - why the window is configurable;
  - why the growth floor scales (`window × 1/30`; a fixed 1.0 s floor over a short window would demand 33% of a core, which the contention-agnostic rule forbids);
  - why the minimum is 5 s and the maximum 1 h;
  - that short windows are for tests and fast suites, never a speed knob for a contended campaign;
  - that on the fallback calibration path the disclosed idle floor is the configured value, not a bound that applied;
  - that A-464 is unchanged: time never produces `killed` or `survived`. The monitor only distinguishes "no progress while able to run" (`hung`) from "still running" (`budget_exceeded` at the budget).
- **docs/CONSUMERS.md 2440-2490.**
  - "less than 1.0 s over the trailing 30 s window" → "less than window/30 CPU-seconds over the trailing window (default 30 s → 1.0 s)".
  - "never … sooner than 30 s" → "never sooner than the lane's idle floor and CPU window (defaults 15 s and 30 s)".
  - Add a pasteable TOML example.
- **CHANGES.md** `## [Unreleased]` `### Added`.

## Scope / forbid

- **Touch:**
  - `src/assay/{config.py,liveness.py,runner.py,mutation.py}`;
  - `tests/test_liveness_lane_keys.py` (new);
  - `tests/zz_slow/test_cli_run_real_campaigns.py` (only the two smoke tests, their lane TOML and pins);
  - `tests/test_cli_lanes_json.py`;
  - `tests/test_liveness_runner_monitor.py` (additive only);
  - `tests/test_self_lane.py` (the additive O-B105 pin only);
  - README / DESIGN-GUIDE / CONSUMERS / CHANGES.
- **`tests/fixtures/dataclass-contract.json`** (C15). `MutationConfig` gains two fields, so regenerate the fixture with P1's documented command. Review the diff: only `MutationConfig` entries may change. The diff may be empty if the fixture records only non-default fields.
- **Forbid:**
  - `verdict.py`, `verify.py` and the schema (P3a owns them; if the model rejects your floats → BLOCKED);
  - `mutation_witness.py`;
  - `assay.toml` (the B105 lanes must not declare the keys);
  - `_HUNG_SESSION_FINISH_GRACE_S`;
  - `LIVENESS_FALLBACK_FLOOR_S`;
  - any change to the `hung` predicate's shape;
  - P2's watchdog;
  - `tests/test_cli_run.py` (the tests moved out of it in P1).

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Run the focused suites serially:
   `nice -n 19 ionice -c3 python -m pytest tests/test_liveness_lane_keys.py tests/test_liveness_runner_monitor.py tests/test_liveness.py tests/test_cli_lanes_json.py tests/zz_slow/test_cli_run_real_campaigns.py tests/test_self_lane.py tests/test_dataclass_contract.py -q -p no:cacheprovider`
2. Run the gate:
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p3c-gate.log 2>&1; echo EXIT=$?`
3. In a separate step, read `ASSAY_GATE_CONTAINER_EXIT=0` and `ASSAY_REGISTERED_GATE_COMPLETE=1`.
4. **Preflight** (plan §10.3; this package changes `src/assay`): `cd <worktree>/assay && python ./run-gate.py self-qualification-preflight > /tmp/b110-p3c-preflight.log 2>&1; echo EXIT=$?`. Read the verdict and the preflight markers in a separate step; 100% line+branch and the exact exclusions inventory must hold. Only one gate container at a time.
5. Never run the `self-qualification` lane.

## BLOCKED rule

If a named contract cannot be met as specified, STOP. This applies when:
- P3a's liveness model is absent or rejects the specified shape;
- P2's watchdog is not in the branch;
- **P1's move is missing**, i.e. the smoke tests are not in `tests/zz_slow/test_cli_run_real_campaigns.py`;
- an existing `test_liveness_runner_monitor.py` test goes red under the specified signature;
- the smoke tests are unstable on the gate host (see "What the smoke tests prove");
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
