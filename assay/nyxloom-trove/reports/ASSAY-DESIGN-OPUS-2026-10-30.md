# Assay: load-independent verdict design (pressure is telemetry, death mode decides)

Author: independent design pass (Opus, read-only), 2026-10-30.
Code read at: in-flight branch `assay-r3-canary-20261029` @ `4a16139e1` ("tip") and `origin/main` @ `54b63a7e7` ("main"); released tag `assay-v10.0.0`.
All `file:line` refer to the tip unless marked `main:` or `v10.0.0:`. Paths are relative to `assay/`.
"(guess)" marks a claim I did not verify by reading code or running it.

---

## 0. Diagnosis in 12 lines (why the B105/B172 patches kept failing)

1. The PSI signal measures the lane's **own** cgroup (`contention.py:80` `pid=os.getpid()`), which contains the test suite. PSI `some` counts stalls of any task in it, so the suite's own parallelism is counted as "pressure".
2. The B105 child container is capped at 3 CPUs (`run-gate.toml:15-17`). Every burst above 3 cores throttles, and tip treats **any** throttle as pressure (`contention.py:196-199,210`). This is self-inflicted.
3. Measured windows were 0.6-1.5 % CPU-some (`docs/DESIGN-GUIDE.md:2732-2735`). Yet the 1 % rule withheld green R1 claims in 4 of 17 provisional runs (`/tmp/run-gate/lanes/self-qualification-provisional/{1f75…,3449…,11b5…,f0cf…}.log`). The threshold was raised to 5 % (`contention.py:24-25`). That cutoff is a number with no causal model behind it.
4. Withholding a FAIL as "infrastructure for retry" retries nothing. Run-Gate never re-runs an Assay ERROR (`run-gate-project/run_gate.py:277-352`, no retry loop; consumer survey). The comment at `tools/self-qualification-container.sh:13` ("which is retried") is false.
5. The PSI exemption for canaries needed a cause proof. The fixed marker could be forged (luna-b5). The replacement nonce+stack-frame regex is per-language (`canary.py:318-408`) and fails closed whenever output is truncated (`canary.py:367`). It is also tuned to V8 `at …` frames, not Vitest's reporter output (guess: Vitest prints `❯ file:line:col`; the only real-JS test runs plain `node`, `tests/core/test_canary_import_break_proof.py:311-335`).
6. Telemetry was written into the verdict. It went into `result_stderr_tail` on PASS (`runner.py:1356-1371`), which breaks the CONSUMERS contract "a PASS carrying a tail is a report-overridden exit" (`docs/CONSUMERS.md:1047-1052`). It forced a normalizer special case (`gate/python/qualify_sql.py:985-1003`). It also went into the canary `description` (`canary.py:840-846,974-987`): the verdict at `.worktrees/assay-r3-canary-20261029/assay/.assay/verdict-self-qualification-provisional.json` carries PSI microseconds in `claims[R3].canary.attempts[0].description`.
7. Each patch added branches (type guards in three `withhold_*` functions, `contention.py:245-252,276-281,303-306`). That pushed R1 below 100 % (b6fix report). The fix was more tests, not less code.
8. Root error: **load was used as a proxy for "did this result die for the wrong reason"**. The direct evidence of how the process died (exit status, signal, report, limit events, process tree, receipts) is already collected. It is just not used uniformly.

---

## 1. Executive decision (the new policy, plain rules)

1. **PASS is final.** A completed PASS at any rigor is never withheld for host conditions: not PSI, not throttling, not an unavailable sample, not a limit event. R2 `survived` counts as a PASS.
2. **A non-PASS counts as a judgement only if the process died cleanly.** Clean means a normal non-zero exit or a complete structured report with failures. It must also have no limit event (pids.max / oom / oom_kill) in the command's own cgroup subtree, no fatal signal from outside the process, no leaked process tree, and an observation when the gate requires one. Anything else is infrastructure: R0/R1 `ERROR/EXEC_FAILED`, R2 `crashed`, R3 `INCONCLUSIVE`.
3. **Timeouts and hangs are never kills or FAILs.** They are already incomplete (`BUDGET_EXCEEDED`, `budget_exceeded`, `hung`), and that stays.
4. **PSI and throttling never change a verdict.** They are advisory telemetry: progress stream plus Assay's stderr, never the verdict. They still pause the R2 liveness clock (`liveness.py:1563-1571`), because that use can only produce "incomplete", never a judgement.
5. **One classifier decides how a command died:** the new `termination.classify()`. R0, R1 (inherited from R0), R2 buckets, R2 state revalidation and R3 halves all call it. `verify.py` and `qualify_sql.py` stay independent re-derivations (A-182) but are tested against the same decision table.
6. **R3 canary:** a transform counts as "caught" only if the gate rejected it for the expected reason **and** (for import-break) the injected statement left a per-run receipt file. "Survived" only if the gate accepted it. Everything else is INCONCLUSIVE. Assay no longer parses output for this, and no marker exists that a test could echo.
7. **No verdict wire change in 10.0.x/10.1.** Schema stays v16. Host-varying measurements leave the verdict; fields that still carry them move out in v17, bundled with B171.
8. **Retries:** Assay retries nothing at R0/R1/R3, since one lane run is one measurement. Optionally, R2 re-runs each infrastructure-bucket candidate once, serially, at the end of the sweep (WP7). Run-Gate's retry policy is a Run-Gate decision (Q6).
9. **Expected gate result with a peer lane running:** green stays green. It turns non-green only if the suite really fails (FAIL), the lane's wall budget expires (BUDGET_EXCEEDED, exit 3), or a limit event / external kill hits a failing run (ERROR, exit 3).

---

## 2. Rules table

| # | Situation | Verdict / action | Evidence required (and where it is read) |
|---|---|---|---|
| R-1 | R0 command exits 0, or a complete structured report shows 0 failures | **PASS** | exit status / B078 report (`runner.py:1581`). Telemetry: progress only |
| R-2 | R0 normal non-zero exit, no limit event, observation clear or not requested | **FAIL/COMMAND_FAILED** (unchanged meaning) | exit status. If the window shows PSI/throttle, Assay prints an advisory hint on stderr; the verdict is unaffected |
| R-3 | R0 non-zero with a limit event in the own subtree (pids.events max, memory.events oom/oom_kill/oom_group_kill delta > 0) | **ERROR/EXEC_FAILED**, `Claim.detail` names the event | ContentionWindow limit deltas (`contention.py:142-190`); ancestor-only events are not attributed (`DESIGN-GUIDE.md:2722-2725`) |
| R-4 | R0 leader killed by an external signal (rc ∈ {-9,-15,-2,-1}, or tree `leader_status == "signaled"` without a self-inflicted signal) | **ERROR/EXEC_FAILED**, detail `killed-by-signal:N` | `CommandResult.returncode` / `ProcessTreeEvidence.leader_status` (`process_tree.py:91-94`) |
| R-5 | R0 leader died from any signal outside `EXTERNAL_SIGNALS` (self-inflicted), with no higher-precedence evidence | **FAIL/COMMAND_FAILED** | rc < 0 with a signal outside `EXTERNAL_SIGNALS`; the process died from a signal not classified as external |
| R-6 | R0 non-zero in gate mode (`ASSAY_GATE_REQUIRE_RESOURCE_OBSERVATION=1`) with the observation unavailable | **ERROR/EXEC_FAILED**, detail names the missing sample | ContentionWindow availability. Applies only to non-PASS |
| R-7 | R0 PASS with a limit event or unavailable sample | **PASS** (change vs tip `contention.py:282-283`) | rule 1. Event goes to progress `resources` |
| R-8 | R0 Assay wall budget expires | **BUDGET_EXCEEDED/LANE_TIMEOUT** (unchanged) | `runner.py:1451-1497` |
| R-9 | R0 process tree leaks descendants (native-R2 lanes only) | **ERROR/EXEC_FAILED** (unchanged; B171 later keeps the suite result as a diagnostic) | `runner.py:1543-1567` |
| R-10 | R1 when R0 was infrastructure (R-3/4/6/9) | **R1 ERROR/EXEC_FAILED** (inherited) | `CommandResult.termination` |
| R-11 | R1 when R0 was judgeable | judged on the artifact only. Missing/unreadable artifact keeps its existing codes | `evaluate_r1`; host load cannot change arithmetic on a complete artifact |
| R-12 | R2 candidate PASS | **survived**, even with a limit event (change vs `mutation.py:3080-3086`) | rule 1 |
| R-13 | R2 candidate clean FAIL | **killed**, then the existing equivalence / kill_signal / cold-witness refinements (`mutation.py:3036-3077,4302-4307`, `2275-2306`) | exit + process tree + limits |
| R-14 | R2 candidate FAIL plus limit event / signal / leak | **crashed** | same classifier |
| R-15 | R2 Assay budget / liveness-certified hang | **budget_exceeded / hung** (unchanged). PSI pauses the liveness clock and never certifies hung | `liveness.py:1563-1593` |
| R-16 | R2 ingested (Stryker) `Timeout` | **budget_exceeded** (unchanged, `mutation.py:5185-5191`) | report |
| R-17 | R3 control not a judgeable PASS | attempt **INCONCLUSIVE** (unchanged) | classifier on the control half |
| R-18 | R3 transform not judgeable (R-3/4/6/8/9) | attempt **INCONCLUSIVE** (`transformed_outcome=INCONCLUSIVE`, `observed=CANARY_INCONCLUSIVE`) | classifier on the transform half |
| R-19 | R3 transform PASS (the gate accepted the bad input) | **FAIL/CANARY_SURVIVED**. The description says whether the receipt was written ("defect executed but suite passed" vs "target never imported") | receipt file |
| R-20 | R3 transform clean FAIL, reason == expected, and (import-break → receipt == nonce) | attempt **PASS** | receipt file (§3) / R1 reason code |
| R-21 | R3 transform clean FAIL, otherwise (no receipt, or a different reason such as R0 FAIL on uncovered-line) | attempt **INCONCLUSIVE** (was CANARY_SURVIVED at tip `canary.py:946-958`) | the injected defect cannot have caused it, so this says nothing about gate sensitivity |
| R-22 | Host PSI, load average, peer containers, Docker names | **never consulted** | — |
| R-23 | B105 admission refusal (2 GiB headroom, observer gid) | no verdict; wrapper exit 3 → Run-Gate ERROR (unchanged) | `tools/cgroup-parent.sh:392`, `tools/self-qualification-container.sh:353` |
| R-24 | The command outcome is already `INCONCLUSIVE`, with no higher-precedence death or observation evidence | R0/R1 **ERROR/EXEC_FAILED**; R2 **crashed**; R3 attempt **INCONCLUSIVE** | the recorded `Outcome.INCONCLUSIVE` |

Precedence inside `classify()` (top wins): NOT_RUN (ERROR/NO_MEASUREMENT without a leak) → LEAKED → TIMED_OUT/HUNG → **PASSED** → FAILED decided by a complete structured report → KILLED_EXTERNALLY → LIMIT_EVENT → UNOBSERVED → INCONCLUSIVE → FAILED.

Why a FAIL decided by a complete report beats a later signal or limit event: the assertions already failed and were recorded, and that is stronger evidence than the death mode.

A signal outside `EXTERNAL_SIGNALS` is treated as self-inflicted by this
classifier, so it is a functional failure only after higher-precedence
infrastructure evidence is excluded. In particular, a direct limit event wins
when it accompanies any such signal; without that event, the signal remains
R-5's `FAIL/COMMAND_FAILED`.

Known limit, documented and not engineered: a shell wrapper that turns a child's death into `128+N` is indistinguishable from a normal exit unless the observer sees the limit event.

---

## 3. Canary design (R3)

**Goal.** Prove "the gate rejects this known-bad input *because of* the injected defect". The proof must work for every adapter, need no output parsing, and offer nothing an unrelated failure could echo.

**Mechanism: execution receipt.** It is the pattern Assay already uses for SQL kills (`judge.mutation.kill_signal_artifact`, `mutation.py:3114-3140,4302-4307`), applied to the canary.

1. Per attempt, `canary.py` creates `receipt_dir = tempfile.mkdtemp(prefix="assay-canary-")`, mode 0700, **outside** the snapshot so it can never be Git-visible dirt. It also creates `nonce = secrets.token_hex(16)`. The directory is removed in `finally`.
2. Adapter protocol change: `inject_import_break(self, text: str, *, nonce: str) -> tuple[str, str]`. The injected code (a) reads `ASSAY_CANARY_RECEIPT` from the environment, (b) writes `nonce` to that path if it is set, and (c) raises/throws exactly as today.
   - **Python** (`adapters/python.py:348,399-409`): insert three lines at the existing insertion point:
     `_assay_canary_receipt = __import__("os").environ.get("ASSAY_CANARY_RECEIPT")`
     `if _assay_canary_receipt: __import__("pathlib").Path(_assay_canary_receipt).write_text("<nonce>")`
     `raise AssertionError("assay-canary-import-break")`
   - **JavaScript/TypeScript** (`adapters/javascript.py:213-215,459-467`): append
     `;(() => { const p = globalThis.process; const f = p && p.env && p.env.ASSAY_CANARY_RECEIPT; const fs = f && (typeof p.getBuiltinModule === "function" ? p.getBuiltinModule("node:fs") : (typeof require === "function" ? require("node:fs") : null)); if (fs) fs.writeFileSync(f, "<nonce>") })()`
     followed by the existing `throw new Error("assay-canary-import-break")`.
     The code is valid ESM, CJS and TS-as-transpiled. `process.getBuiltinModule` exists in Node ≥ 20.16/22.3; the devcontainer has Node 26.10.
   - **Go** (`adapters/go.py:481-516`): accept `nonce`, embed it in the panic text only. Go is not registered for R3 (`cli.py:1621`), so no receipt is needed; see §5.
   - **SQL** (`adapters/sql.py:5492`): unchanged (`NotImplementedError`).
3. Only the **transform** half gets the environment variable, by wrapping its `process_runner`: `functools.partial`-style, `env={**env, CANARY_RECEIPT_ENV: str(path)}`. This follows the liveness env-stamp precedent (`liveness.py:1688-1690`). The `CommandPlan` and its serialized `argv`/`env` names stay untouched.
4. Judgement is one helper, `_attribute_transform(...)`, used by **both** `run_isolated_canary` (`canary.py:679`) and the legacy `run_python_canary` (`canary.py:484`). It implements rules R-17…R-21. The receipt check is `path.is_file() and path.read_bytes()[:65] == nonce.encode()` (bounded read).
5. The verdict is unchanged (v16). The producer encodes the result through the existing fields (`transformed_outcome`, `observed_reason_code`). `verify.py:_judge_one_attempt` (`verify.py:3432-3456`) needs **no change**: the producer never records a wrong-reason FAIL any more, and old documents still re-derive. `description` stays deterministic: mechanism text plus one of "receipt matched" / "no receipt" / "defect executed, suite passed". No PSI numbers and no nonce.

**Threat model, stated.** The receipt proves the injected statement executed inside this run's process tree and then raised. A suite would have to deliberately read Assay's transformed source to forge it. That is the same trust tier as the R1 coverage artifact, which the lane's own process also writes. Echoed output is irrelevant because stdout/stderr are not consulted.

**Limits, documented.** Lanes whose test process cannot inherit the variable or write to the directory get INCONCLUSIVE for import-break, never a false PASS or a false SURVIVED. Examples: tests run via `docker run`, or a test harness that scrubs the environment before importing the target.

**uncovered-line.** No receipt is needed: `UNCOVERED_LINES` from R1 is already cause-specific. An R0 FAIL on the transform cannot be caused by an inert, never-called function, so it is INCONCLUSIVE (R-21).

**Deleted by this design:**
- `canary.py:249-279` `_bind_import_break_nonce` (difflib)
- `:282-289` `_import_break_nonce_line`
- `:292-315` `_diagnostic_path_matches`
- `:318-408` `_has_import_break_diagnostic` (three language regex branches)
- `:173-190` the contention note helpers
- the nonce/cause blocks at `:543,:589-607` and `:860,:880,:936-967`
- the `expected_failure` exemption (`contention.py:227,249-250,255`)
- `tests/core/test_canary_import_break_proof.py` (399 lines)

---

## 4. Telemetry placement

| Datum | Verdict | Progress stream (`--progress`, always on in gates per A-429) | Assay stderr (Run-Gate log) | R2 state record |
|---|---|---|---|---|
| cgroup PSI deltas, cpu.stat throttling for the R0/R3 windows | **never** | `command_finished.resources` (additive): `{window_s, observation: "clear"\|"unavailable"\|"not_requested", psi_us:{…}, throttled_us, limit_events:{…}}` | one line per non-PASS window: deltas plus % of window, "advisory" | — |
| Limit event that **caused** an infrastructure verdict | as `Claim.detail` text only (deterministic names and counts) on the ERROR claim | yes | yes | yes (existing B145) |
| Observation unavailable | `Claim.detail` only when it caused R-6 | yes | yes | — |
| R2 per-candidate cpu/rss/phase/startup, liveness traces | never (unchanged, B111) | `candidate` (unchanged) | — | `resources` (unchanged) |
| R3 receipt result | deterministic phrase in `attempt.description` | `command_finished` for `phase: "canary-control"/"canary-transform"` (additive) | — | — |
| Host PSI / peer gates | never | never | Run-Gate's own footprint line already prints it | — |

Consequences:
- Delete the stderr-tail telemetry (`runner.py:1356-1371`) and the "withheld" text appended into the command's own tail (`runner.py:1376-1398`); it moves to `Claim.detail`.
- Delete the SQL normalizer special case (`gate/python/qualify_sql.py:985-1003`) and its three tests (`gate/tests/test_qualify_sql.py:715-750`).
- Keep `_differing_json_paths` (`qualify_sql.py:1044-1076`); it is only a diagnostic.
- The frozen witness `gate/python/fixtures/sql/expected/sql-r2-witness.json` is untouched.

Remaining host-varying fields already **inside** v16, to remove in v17 (WP8):
- `process_tree_evidence.cleanup_seconds`, normalized at `qualify_sql.py:1010-1026`.
- `judgment.r2.budget_per_candidate_derived_s` (`qualify_sql.py:1006`) is a judge input, so it is legitimate evidence: keep it, keep its normalization.

Rule for future fields: **the verdict carries only facts that decide or justify the judgement; measurements of the host go to progress or state.**

---

## 5. Language parity matrix

Registry today: python {R1,R2,R3}, sql {R2}, javascript {R1,R2,R3}, go {R1} (`cli.py:1608-1621`). R0 is language-agnostic.

| Capability | Generic (shell/any) | Python | JS/TS | Go | SQL/DDL |
|---|---|---|---|---|---|
| R0 | ✅ | ✅ | ✅ | ✅ | ✅ |
| R1 line | — (lcov/cobertura parsers exist, `coverage.py:110-111`, no adapter) | ✅ coverage.py JSON (reuse) | ✅ istanbul JSON; v8/c8 refused (`vocabulary.py:230,368-380`) | ✅ go cover + toolchain helper (`adapters/go.py:565`) | ❌ not meaningful |
| R1 branch | — | ✅ arcs | ✅ istanbul arcs only (`vocabulary.py:343`) | ❌ format has no branches | — |
| R2 | — | ✅ native, 4 ops (`vocabulary.py:150-155`) | ✅ **ingested** Stryker report (`mutation_parsers/mutation_report_json.py`) | ❌ ops declared, generator `UNSUPPORTED` (`adapters/go.py:711-735`) | ✅ native, 7 DDL ops + kill_signal/equivalence artifacts |
| R3 import-break | — | ✅ → receipt | ✅ → receipt (tip regex proof likely fails under Vitest output, guess) | ❌ unregistered | ❌ n/a |
| R3 uncovered-line | — | ✅ | ✅ | library-only R1 canary from committed profiles (`canary.py:1202`) | ❌ n/a |
| Liveness / hung | — | ✅ pytest plugin (`liveness.py:1-30`) | owned by Stryker (timeout → budget_exceeded) | — | via process tree only |
| Process-tree evidence | only if the lane declares native R2 (`runner.py:4160-4166`) | native R2 lanes | ❌ (Stryker in-process) | — | native R2 lanes |
| Per-candidate limit events | — | ✅ | ❌ (whole-lane R0 window only) | — | ✅ |
| Pressure handling after this design | uniform classifier | uniform | uniform at lane level; per-candidate by Stryker | uniform (R0/R1) | uniform |

**Target state and build-vs-reuse decisions:**
- **Uniform without per-language code:** the termination classifier (§2), the telemetry placement (§4), and receipt *verification* (one file check). Per-language code is limited to a 1-3 line injection snippet per adapter, which already exists today.
- **Python R2: keep the native engine (build is already sunk).** mutmut and cosmic-ray do not provide Assay's identity, cold-witness, liveness, resumable-state and rejudge contracts (B091/B114/B120). Switching would mean rewriting those, not reusing a tool. No new operators in this wave.
- **JS/TS R2: keep reusing Stryker.** It owns sandboxing, timeouts and retries; the mutation-testing-report-schema is the reuse seam. Do not build a native JS engine. Per-candidate resource attribution is impossible inside Stryker; accept and document it.
- **Go R2/R3: not worth it now.** R2 would need either a native generator (M-L) or a parser for a non-standard engine format. Gremlins and go-mutesting do not emit mutation-testing-report-schema (guess), so that is a new parser (M). R3 needs a Go toolchain in the tester image, which is absent (`registry.py` docstring, A-042/A-043), plus an aliased `os` import for the receipt (S). Revisit when a consumer asks.
- **SQL R3: not applicable.** SQL R2's kill_signal artifact already gives cause-specific kills; there is no import concept.
- **Shell / new languages: cheapest parity path is reuse.**
  - A 4-method adapter (`is_test_path`, `has_executable_code`, `normalize_coverage_key`, `statement_spans → None`).
  - Plus the existing lcov/cobertura parsers: bashcov/kcov → cobertura; llvm-cov → lcov; coverlet → cobertura.
  - Plus the ingested R2 path for engines that emit mutation-testing-report-schema: Stryker.NET, Stryker4s, Infection; PIT via its plugin (guess for the last two).
  - Not in this wave.
- **Do not extend process-tree isolation to all R0 lanes now.** It would turn consumer R0 lanes that leave daemons into ERROR; that is B171's territory and a v17 decision. R0 signal detection uses `returncode < 0` instead.

---

## 6. Concrete change list (per file/function), data model, compatibility

### 6.1 New: `src/assay/termination.py` (initial estimate ~90 LOC; current implementation 132 LOC)

```python
class Termination(enum.Enum):
    PASSED, FAILED, TIMED_OUT, HUNG, NOT_RUN, KILLED_EXTERNALLY, LEAKED, LIMIT_EVENT, UNOBSERVED, INCONCLUSIVE
JUDGEABLE = frozenset({Termination.PASSED, Termination.FAILED})
EXTERNAL_SIGNALS = frozenset({signal.SIGKILL, signal.SIGTERM, signal.SIGINT, signal.SIGHUP})

def classify(*, outcome: Outcome, reason_code: ReasonCode | None, returncode: int | None,
             decided_by_report: bool, process_tree: ProcessTreeEvidence | None,
             limits: ResourceLimitEvidence | None,
             observation: Literal["clear", "unavailable", "not_requested"],
             observation_required: bool) -> Termination        # precedence as in §2

def r2_bucket(t: Termination) -> str   # PASSED→survived, FAILED→killed, TIMED_OUT→budget_exceeded, HUNG→hung, else crashed
def detail(t: Termination, limits, observation_reasons, returncode=None) -> str | None  # deterministic Claim.detail text; returncode renders a signal number
```

Tests import one shared table, `tests/core/termination_table.py` (rows: inputs → Termination, R0 status, R2 bucket, R3 attempt). The same table drives:
- `test_termination.py`
- the mutation bucket tests (WP4)
- the canary attribution tests (WP3)
- a verifier consistency test that builds v16 `MutantOutcome` dicts for the process-tree rows and asserts `verify.py:2320-2360` agrees.

That gives DRY behaviour across producer, state revalidation, verifier and canary while each keeps its code independent (A-182).

`gate/python/qualify_sql.py:derive_bucket` (`:410-430`) stays independent. It is a probe-artifact oracle for a different question and is not unified.

### 6.2 `src/assay/contention.py` (shrinks to the sampler only)

- `ContentionObservation`:
  - `status` ∈ {`unobserved`, `clear`, `unavailable`}; **delete** `affected`/`unknown`.
  - Add `limits: ResourceLimitEvidence | None` and `pressure_us: Mapping[str, int]` as telemetry.
  - Keep `reasons` for unavailability.
- `ContentionWindow.__exit__`:
  - Keep the per-sample error handling (`:106-171`, a tip refinement worth keeping).
  - **Delete** the throttle / PSI-threshold logic (`:196-219`) and the constants (`:24-25`).
  - Delete `monotonic_ns` (`:64,72-73,79,86,91,200-201`); the window duration comes from `CommandResult.started/ended`.
- **Delete** `withhold_canary_result` (`:223-259`), `withhold_r0_result` (`:262-288`) and `withhold_r1_result` (`:291-313`), with their type-guard branches.

WP1 retained those three compatibility helpers and their type guards because
live R0/R1/R3 callers still use them until the WP2 and WP3 wiring lands. The
current sampler also filters `cgroup_cpu.nr_throttled` from `pressure_us`; the
throttle count is not exported, while duration telemetry such as
`throttled_usec` remains. These are implementation deviations from the list
above, not completed deletions.

### 6.3 `src/assay/runner.py`

- `CommandResult` (`:908-911`): replace `resource_contention` with private `observation: ContentionObservation | None` and `termination: Termination | None`. Add `decided_by_report: bool = False`, set at `:1581` when `summary is not None`. None of these is serialized.
- `execute_plan` (`:1228`, block `:1338-1398`):
  - After `_execute_plan_inner`, call `termination.classify(...)`.
  - When the result is `FAIL` and the termination ∉ JUDGEABLE, return `ERROR/EXEC_FAILED`.
  - **Delete** the stderr-tail telemetry append (`:1356-1371`) and the withheld-text append (`:1376-1398`).
  - Fix the docstrings that still say 1 % (`:1283-1290`, `:1750-1756`).
- `build_r0_claim` (`:1803-1830`): when `result.termination` is set and is not judgeable, put `termination.detail(...)` into `Claim.detail` via `refusal_detail`.
- **Delete** `_withhold_r1_claim_for_contention` (`:1659-1700`). At its call site (`:4603-4607`), add a 6-line `_r1_after_r0(claim, termination)` that returns an `ERROR/EXEC_FAILED` claim when R0 was infrastructure (R-10).
- `_execute_snapshot_unit` progress emit (`:3522-3537`) and the direct branch (`:7433-7445`): add `"resources": observation.to_progress()` when an observation exists (additive).
- Advisory stderr hint, ≤ 1 line, printed through the existing `diagnostics` stream in `_run_prepared_lane`/`run_lane` when R0 is FAIL and `pressure_us` is non-empty. No threshold constant.
- Keep `_gate_contention_attribution_enabled` (`:1650-1656`) unchanged.

### 6.4 `src/assay/canary.py` and the adapters

As in §3:
- New `_attribute_transform()` and `_receipt_runner(process_runner, path)`.
- Delete the regex proof and the nonce binding; remove imports `difflib`, `posixpath`, `re`, `urllib.parse`, `PureWindowsPath`.
- Both halves: replace the `contention.withhold_canary_result` calls (`:831-846`, `:959-980`) with `classify()`.
- `adapters/base.py:451` protocol signature gains `*, nonce: str`. Add `CANARY_RECEIPT_ENV = "ASSAY_CANARY_RECEIPT"` beside `_IMPORT_BREAK_MARKER` (`:77-80`) and rewrite that comment: the marker is no longer evidence.
- `adapters/python.py:348,399-409`, `adapters/javascript.py:213-215,459-467,560`, `adapters/go.py:481-516,705`: snippets as in §3. `adapters/sql.py:5492`: signature only.

### 6.5 `src/assay/mutation.py`

- `_classified_bucket` (`:4278-4307`): `bucket = termination.r2_bucket(classify(...))`. Then apply the existing `forced_bucket`, equivalence and kill_signal refinements only when the classifier says FAILED/PASSED.
- **Delete** `_resource_limit_bucket` (`:3080-3086`).
- `_classify_mutant_result*` (`:3002-3077`) collapse onto `r2_bucket` plus the equivalence split.
- `_terminal_result_matches_bucket` (`:2112-2316`): replace its private re-derivation (`:2228-2253`, `:2307-2312`) with the same classifier. That puts producer and state revalidation on one function.
- Old state records holding `crashed` for a PASS under a limit event fail revalidation; `return False` refuses the record, so the candidate is re-run (guess: refusal leads to re-execution, per the `rejected_total` progress semantics in CONSUMERS.md:3809).

### 6.6 `src/assay/verify.py`

- Keep the tip fix at `:3444-3450` (incomplete transformed outcome → INCONCLUSIVE).
- No other change. Add the table-driven consistency test.

### 6.7 Gate / tools

- `gate/python/qualify_sql.py:985-1003`: delete (WP0).
- `tools/self-qualification-container.sh:13,16`: fix "which is retried". Run-Gate does not retry; a human or agent re-runs.
- `tools/parallel-safety-regression.sh`, plus `run-gate.toml [lanes.parallel-safety-regression]` (`run-gate.toml:47-55`) and its references (`gate/tests/test_distribution_gate.py`, README, CONSUMERS, DESIGN-GUIDE, `nyxloom-trove/nyxloom.toml`): **delete in WP5**. It is a one-off red-first oracle against `merge-base origin/main` for B172. After merge its baseline contains the change, so it proves nothing.

### 6.8 Docs

One section in DESIGN-GUIDE, "Host load and verdicts", replaces three:
- `DESIGN-GUIDE.md:2712-2758` "Shared-host pressure attribution"
- `:2760-2795` "R3 canary cause evidence under PSI"
- `:4122-4127` the SQL telemetry note

README `:1505-1530` and CONSUMERS `:105-139` become a 10-line pointer each. Fix README `:451` (JS R3 row: receipt requirement). Update B172 with the new contract, and add a new B-item for this redesign.

### 6.9 Data model and compatibility summary

- **Verdict:** v16 unchanged. No new key, so old `assay verify` binaries can still verify new documents (`verify.py:2778` rejects unknown keys).
- **Verifier semantics:**
  - The tip fix (`:3444-3450`) makes a 10.0.0 document whose transformed half was `ERROR`/`BUDGET_EXCEEDED` re-derive as INCONCLUSIVE instead of SURVIVED. Such a document was recorded SURVIVED by `v10.0.0:canary.py:982` → mismatch. Rare; listed in CHANGES as a verifier correction.
- **Behaviour vs 10.0.0** (main's contention code was never released):
  - (a) External-signal, limit-event or unobserved FAILs become ERROR at R0/R3.
  - (b) import-break needs a receipt (10.1).
  - (c) R2 `survived`+limit is no longer `crashed` (10.1).
  - PSI never changes anything, which is the same as 10.0.0.
- **Consumers** (survey):
  - Run-Gate reads only `outcome`, `exit_code`, `reason_code`, `claims[].rigor/status/reason_code/source/verified_by_assay`, `judge_provenance`, `commit` (`run_gate.py:15909-15946,205-212,355-419`); no break.
  - cmru only exit codes (`tools/run_release_gate.py:1257-1259`) plus `result_stdout_tail` on failure (`:1003-1033`); no break.
  - nyxloom only exit codes; no break.
  - dstdns pins schema `verdict:15` via vendored 9.0.0 (`tests/config/test_assay_pin_integrity.py:24,284-286`); unaffected until it re-pins.
- **Progress stream:** additive keys only (CONSUMERS `:3794-3797` already promises additive tolerance). Run-Gate's watchdog reads only event names and a few counters (`run_gate.py:11725-11790`).

---

## 7. Work packages (dependency order)

Gate rules for every WP:
- Host load rule: serial, `nice`/`ionice`.
- One registered gate at a time per implementer.
- Never commit while a gate runs (the HEAD_CHANGED lesson, handoff-assay.md).
- Read verdicts in a separate step.
- Coverage discipline:
  - **Delete before add.**
  - No `pragma: no cover`, checked with a grep in the acceptance step.
  - No type-guard branches in private pure helpers.
  - Every new branch is a row in a parametrized table.
  - `self-qualification-preflight` (full-source R0+R1 100 % line+branch) must be green before review.

| WP | Scope | Tests that must fail first (behaviours) | Acceptance | Size | Parallel? |
|---|---|---|---|---|---|
| **WP0 — 10.0.1 cut** (on the in-flight branch, forward edit) | (1) PSI/throttle no longer set `affected`: `contention.py:196-215` → `affected ⇔ resource_limit_hit`; delete `:24-25` and `monotonic_ns`. (2) Delete stderr-tail telemetry `runner.py:1356-1371` and canary description telemetry `canary.py:181-190,840-846,974-980`. (3) Delete the import-break cause proof (`canary.py:249-408` + call sites `:543,:589-607,:860,:880,:936-967`) and the `expected_failure` exemption; import-break goes back to 10.0.0 semantics (clean `COMMAND_FAILED` = expected). (4) Delete `qualify_sql.py:985-1003` + `test_qualify_sql.py:715-750`. (5) Delete `tests/core/test_canary_import_break_proof.py`; prune proof/PSI cases in `test_contention.py:149-254`, `test_canary_p23_isolated_edges.py`, `test_canary_multi_target.py`; invert `test_runner_execute.py:187`. (6) Docstrings `runner.py:1283-1290,1750-1756`; docs README `:1505-1530`, CONSUMERS `:105-139`, DESIGN-GUIDE `:2712-2795,:4122-4127`; CHANGES [Unreleased]; B172 text. Keep `verify.py:3444-3450` and `qualify_sql._differing_json_paths`. | `test_psi_and_throttling_never_withhold_a_failed_r0`; `test_gate_mode_pass_verdict_has_no_stderr_tail` (CONSUMERS:1047 contract); `test_canary_description_has_no_host_telemetry`; `test_import_break_command_failed_is_expected_under_psi`; kept: `test_limit_event_withholds_failed_r0`, `test_unavailable_required_sample_withholds_failed_r0` | focused tests green; `run-gate tester-unified` PASS (SQL witness compares with no special case); `run-gate self-qualification-provisional` PASS (R0, R1 100 %, R3) — under whatever peer load exists; one independent review | S (src ≈ −350/+20; tests ≈ −550/+80); 1 implementer-day | alone (release blocker) |
| **WP1 — termination classifier** | new `termination.py` + `tests/core/termination_table.py` + `test_termination.py`; reshape `ContentionObservation`; delete `withhold_*` | one row per `Termination`, including `INCONCLUSIVE`; precedence rows: leak beats PASS, report-FAIL beats signal, PASS beats limit, PASS beats unavailable, self-signal plus limit→LIMIT_EVENT, SIGSEGV→FAILED without limit, SIGKILL→KILLED_EXTERNALLY | assert R0 and R3 table projections; 100 % line+branch on `termination.py`/`contention.py` in a focused run; preflight green | S (+110/−130) | first in 10.1 |
| **WP2 — R0/R1 wiring + telemetry** | §6.3 | `test_r0_external_sigkill_is_exec_failed_with_detail`; `test_r0_sigsegv_is_fail`; `test_r0_pass_with_limit_event_is_pass`; `test_r1_inherits_r0_infrastructure`; `test_command_finished_carries_resources_when_observed`; `test_fail_under_pressure_prints_one_advisory_line_and_stays_fail`; `test_command_tail_is_never_appended_by_assay` | tester-unified PASS; preflight green | M (+90/−120) | parallel with WP3, WP4 after WP1 (`runner.py` only) |
| **WP3 — receipt canary** | §3, §6.4 | `test_import_break_fail_with_receipt_is_pass`; `test_import_break_fail_without_receipt_is_inconclusive`; `test_import_break_pass_with_receipt_is_survived_with_note`; `test_uncovered_line_transform_r0_fail_is_inconclusive`; `test_receipt_env_only_on_transform_half`; `test_receipt_dir_is_outside_snapshot_and_removed`; `test_echoed_nonce_in_output_is_irrelevant`; real end-to-end: pytest subprocess (python) and `node` + a minimal Vitest fixture if Vitest is in the tester image (else node-only, recorded as a gap) | provisional PASS (R3 on `src/assay/cli.py`); preflight green | M (+100/−270 src; −399/+150 tests) | parallel (`canary.py` + adapters) |
| **WP4 — R2 alignment** | §6.5 | `test_survived_with_limit_event_stays_survived`; `test_killed_with_limit_event_is_crashed`; `test_bucket_and_state_revalidation_agree_on_table`; `test_old_state_record_crashed_pass_under_limit_is_rerun`; verifier table test | tester-unified PASS (SQL witness unchanged); preflight green | S-M (+40/−90) | parallel (`mutation.py`) |
| **WP5 — consolidation** | §6.7-6.8; delete the parallel-safety lane; single DESIGN-GUIDE section; README matrix; CHANGES; backlog (close B172 → new id) | doc-anchor tests already in gate (README/DESIGN-GUIDE anchors) | tester-unified + provisional PASS on the integrated head; one review | S | after WP2-4 |
| WP6 (optional) — R3 control reuses the lane's baseline unit when the same commit+plan already passed R0 (+R1) | `run_isolated_canaries` takes `baseline_outcome`; skip the control execution | `test_control_reuses_baseline_without_second_run`; `test_control_reruns_when_baseline_not_pass` | provisional wall time roughly halved | S-M | after WP3 |
| WP7 (optional) — R2 serial tail retry of `crashed`(infra)/`budget_exceeded`/uncertified candidates, once, `jobs=1` | `run_mutation` (`mutation.py:3282`) | `test_infra_bucket_candidates_retried_once_serially`; `test_retry_result_classified_by_same_rules`; `test_judged_buckets_never_retried` | R2 pilot completes under peer load | M | after WP4 |
| WP8 (v17, with B171) | `canary_attempt.cause` (`"receipt"\|"absent"\|null`) so the verifier can re-derive it; drop `process_tree_evidence.cleanup_seconds` from the verdict (and its normalizer, `qualify_sql.py:1010-1026`); process-tree `signal` field | schema + verifier + model tests, old-v16 compatibility tests | B171 acceptance | M | later |

Order: **WP0 → release 10.0.1**; then WP1 → (WP2 ∥ WP3 ∥ WP4) → WP5 → release; WP6/WP7 when convenient; WP8 with B171.

Merge conflicts between WP2/3/4 are limited to `contention.py` imports and docs. Docs land only in WP5.

---

## 8. What to do with `assay-r3-canary-20261029`

**Recommendation: release 10.0.1 from the branch tip plus WP0 (a forward edit). Do not merge the tip as-is. Do not restart from main.**

- **Why not as-is.** Tip writes host telemetry into PASS verdicts, violating `CONSUMERS.md:1047-1052`. It ships a 5 % PSI cutoff we are about to delete. It ships a regex cause proof that likely turns JS/Vitest import-break canaries red: a regression against 10.0.0 for the JS R3 path that README advertises (`README.md:451`) and dstdns plans to adopt (`dstdns/docs/summaries/deep-review-2026-10-15/reports/C02-assay-migration.md:400-436`). Its final commits (b5fix–b7fix) have no independent review.
- **Why not restart from main.** Main's `contention.py` withholds PASS on an unavailable sample and treats any stall as `affected` (`main:contention.py:135-142,196,218`). Tip's per-sample error handling and "PASS retained without telemetry" are the parts worth keeping.

**Keep from the branch:**
- `d2ab21578` (verifier/producer alignment; this was the 10.0.1 release blocker, `logs/assay-10.0.1.log`).
- `11f095d46` (witness mismatch paths).
- From `c7aff7bf7`: the ContentionWindow per-sample error handling and the PASS-retention rules.
- From `b59228e8c`: the coverage tests for code that survives.

**Remove via WP0:**
- `9c6545f92` (PSI exemption)
- `af5567bca` (marker)
- `67741f929` (nonce frame)
- `2010d675f` (5 % cutoff)
- `c9432a840` (normalizer special case)
- the telemetry parts of `c7aff7bf7`
- `4a16139e1` and the proof tests in `b59228e8c`

**Release steps for 10.0.1:**
- WP0 → tester-unified → provisional → one review → merge.
- Then `cmru release assay --set-version 10.0.1`. The B169 waiver window `>=10.0.0,<10.1.0` covers it (`tools/self-qualification-gate.sh:816`).
- Expected: 1-2 days.

**Versioning of the redesign:**
- WP1-5 change consumer-visible behaviour (receipt requirement; FAIL→ERROR on external kill/limit; R2 survived+limit), so semver-wise it is **10.1.0**.
- But the B169 waiver gate refuses a 10.1.0 provisional release (`self-qualification-gate.sh:816,887`). A 10.1.0 therefore needs either the R2 campaign or an extended waiver. See Q5.

---

## 9. Risks and open questions

**Risks**
1. *Fake kills from in-suite timing assertions* (a test asserts `elapsed < N`, or uses pytest-timeout): under load these produce a clean FAIL, which counts as `killed`. They are not detectable from death mode.
   - Mitigation: these are suite defects under the contention-agnostic testing rule.
   - Possible later addition: the Python witness already records the failing node; record `exception_class` (Run-Gate already reads it from `test` events, `run_gate.py:9877-9937`) as advisory, and flag `Timeout`-class kills. This does not change classification now.
2. *Receipt not inheritable* (docker-wrapped tests, environment-scrubbing harness) → import-break stays INCONCLUSIVE. This fails safe and is documented. Lane authors can switch to `uncovered-line`.
3. *R1 100 % floor plus timing-dependent branches in `src/`* would make R1 load-dependent. The codebase injects clocks; keep it that way. A reviewer checklist item: "no wall-clock branch in `src/` without an injected clock".
4. *R0 FAILs that tip turned into ERROR are now red.* An agent sees "your test fails" under peer load where tip would have shown "infra". That is the intended effect; communicate it in CHANGES.
5. The `128+N` shell exit convention is not interpreted (§2). Limit events cover the OOM case when the observer is present.

**Open questions (recommended default first)**
1. **PASS final even with a limit event, including R2 `survived`?** Default **yes**: one rule, "a pass is a pass". The alternative keeps 10.0.0's "limit event ⇒ crashed for any bucket" (`mutation.py:3080-3086`).
2. **R3 failure not attributable to the defect: INCONCLUSIVE or SURVIVED?** Default **INCONCLUSIVE**: it gives neither a false red nor a false green, and the verifier is unchanged.
3. **Does 10.0.1 restore 10.0.0's import-break judgement (any clean `COMMAND_FAILED`) and defer the receipt to 10.1?** Default **yes**: smallest safe release, with no JS regression.
4. **R3 control reuses the baseline unit (WP6)?** This partly overrides the old "independent control" rationale (O2, `canary.py:716-725`). Default **yes, in 10.1**: it halves the provisional lane's runtime and its exposure to load.
5. **Version and waiver for the redesign.** Default: **10.1.0 with the B169 window extended to `<10.2.0`** (an operator decision recorded in B169 plus `self-qualification-gate.sh:816`). The alternative is running the full R2 campaign first, which is infeasible soon (B105/B110).
6. **Should Run-Gate auto-retry Assay ERROR/exit-3 once?** Default **no automatic retry now**. Fix the false "retried" comment, and file an opt-in `retries` key for command lanes in the Run-Gate backlog (CIU/RG convention), so infrastructure retries are explicit and counted.
