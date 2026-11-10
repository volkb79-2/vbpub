<!-- origin: 2026-10-31; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/ASSAY-OPUS-REVIEW-AND-PLAN-2026-10-31.md -->
<!-- relation: review of Assay design and package plan -->

# Assay: review of the post-10.0.1 work, design critique, and package plan

Author: Opus reviewer/planner for assay (read-only pass), written 2026-10-30/31.
Inputs: `ASSAY-DESIGN-OPUS-2026-10-30.md` ("the design"), `COW-PLAN-OPUS-2026-10-30.md` ("COW plan"), `HANDOFF-STATE.md` (MASTER PLAN + addenda to 22:18).

Code read at:
- `origin/main` = `608d05dbc`, which contains WP1. Since `assay-v10.0.1`, assay changed in WP1 (`aa3adbcf4`..`391c71931`), B181 and the Go rationale fix.
- `assay-r2fix-20261030` @ `3c678368a`. Merge-base with main is `51307a0be`; it merged main at `f29d10373`.
- `assay-wp2-20261030` @ `702b8cc30` and `assay-wp3-20261030` @ `76ac1371e`. Both are based on `608d05dbc`.

Paths below are relative to `assay/` unless absolute. `file:line` refers to the branch named in the row.

Nothing in the repo was modified. I ran no gates, containers or tests.

---

## 0. Summary

| Branch | Verdict | Why (one line) |
|---|---|---|
| WP1 (merged on main) | keep; **amend via AP-1** | The classifier's precedence lets a report-backed FAIL outrank an observed limit event. That gives false credit in R2/R3, and it is the root of the r2fix P0 below |
| `assay-r2fix-20261030` | **fix first** (AP-1) | P0: a witnessed kill that has a limit event is produced and accepted by the verifier, but the verdict model refuses it. The result is a lane crash at verdict assembly plus a poisoned resumable state. The tip is also ungated (gates ran at an earlier commit) |
| `assay-wp3-20261030` | **fix first** (AP-3, small) | The receipt design is sound. Needed: the snippet should stay one line, the control's FAIL diagnostic is lost, the legacy receipt directory sits next to the repo, there is no real Python end-to-end test, CHANGES/README are missing, and canary.py conflicts with WP2 |
| `assay-wp2-20261030` | **fix first** (AP-4, small) | The R0/R1 wiring is correct. Before merge: drop its canary.py hunk (WP3 owns that file), adopt AP-1's classifier sets, and restore the WP1 CHANGES entry |

Recommended order:
1. AP-1 on r2fix, then merge.
2. Optionally release **10.0.2** (AP-2) so the PWMCP/IOBench campaigns can restart. The waiver window `<10.1.0` already covers 10.0.2.
3. AP-3 (WP3), then AP-4 (WP2). In parallel: AP-5 (WP7 serial retry) and AP-6 (PR0).
4. Optional AP-7 (WP6).
5. AP-8 (WP5 consolidation + waiver `<10.2.0`), then AP-9 (consumer canary smoke).
6. **Release 10.1.0** (AP-10).
7. 10.2.0 = CW8/CW9 (+IO2), waiver `<10.3.0`.
8. 10.3.0 = RS1/RS2 (campaign + state merge, v16 replay merge).
9. **11.0.0** = PR1 + WP8 (+B171), verdict v17, waiver `<11.1.0`.

---

## 1. Review findings (P0 = must fix before merge/release; P3 = cleanup)

### 1.1 WP1 (merged, `src/assay/termination.py`, `tests/core/termination_table.py`)

| ID | Sev | Where | Finding / failure scenario | Fix |
|---|---|---|---|---|
| W1-1 | **P1** (design) | `termination.py:69-70` (report-FAILED returns before limits `:84-85`); table row `report-fail-beats-limit` (`termination_table.py:288`) | **A complete report beats an observed limit event.** The reason given ("assertions already failed", design §2) does not hold for the limit events that matter. A `pids.max` hit makes `fork()` fail with EAGAIN inside a test. A memory.max OOM kills a child that a test spawned. Both show up as an ordinary in-call failure with a complete report, exit 1 and a clean leader. In R2 this produces a **false kill**: the suite is credited, the mutant counted as caught. In R3 it produces a false "caught". With PR1 (N workers in one cgroup, COW plan §4.8.1), a *sibling* can cause the event, so this becomes systematic. COW plan §4.8.1 already assumes the opposite ("every overlapping FAIL → crashed"). | AP-1: `LIMIT_EVENT` before report-`FAILED`. Keep report-`FAILED` above `KILLED_EXTERNALLY` and `UNOBSERVED` |
| W1-2 | P2 | `termination.py:71-77` + r2fix `mutation.py:3096` `_non_exited_judgeable_terminal`; table row `self-signal-beats-tree-signal-status` (`:354`), `sigsegv-is-a-functional-failure` | The table says a SIGSEGV FAIL is `killed` in R2. The producer overrides that with a hidden rule (a signaled leader becomes `crashed`). The state revalidation and verifier (`verify.py` "leader must have exited for killed/survived") agree with the override, not with the table. So the table no longer describes R2. | AP-1: new `Termination.SELF_SIGNALED`. R0 gets FAIL, R2 gets crashed, R3 gets INCONCLUSIVE. The table becomes truthful |
| W1-3 | P3 | callers | Each caller maps `ContentionObservation.status` to the classifier's `observation` literal by hand (`"unobserved"→"not_requested"`). There are 3 copies in WP2/WP3 and 2 in r2fix. | AP-1: add a helper `contention.observation_input(obs)` |
| W1-4 | P3 | `termination.py` docstring | The input conventions per call site are undocumented: what counts as a "report" in R0 (B078 summary), R2 (pytest failed-call witness) and R3 (none). | AP-1: add a docstring table |

### 1.2 `assay-r2fix-20261030` @ `3c678368a`

Purpose: keep native R2 campaigns running when a post-preflight candidate resource window is unavailable or not comparable. Both whole-source campaigns aborted with `cannot compare cgroup resource-limit observations` at `mutation.py:2951` on main (`logs/r2-pwmcp-retry2.log`, `logs/r2-iobench-retry5.log`; both ~18:58, the same host-wide event).

| ID | Sev | Where | Finding / failure scenario | Fix |
|---|---|---|---|---|
| R2-1 | **P0** | producer `mutation.py:4341-4378` (`_classified_bucket`: `decided_by_report` → `FAILED` beats `limits`); model `verdict.py:2244-2253` (refuses `killed` with `limit_hit`, no exception); verifier `verify.py:2266-2275` + `:2278` (accepts `killed` with `limit_hit` when a clean failed-call witness exists); schema description `verdict.schema.json:1531` ("cannot accompany a killed outcome") | **Three components disagree.** Scenario: a candidate's test fails in-call (EAGAIN from pids.max, or OOM of a child), exits 1, and the witness is captured. `_classified_bucket` → `killed`. The state record is written as `killed` with limit evidence. At verdict assembly `Mutation.__post_init__` raises `ValueError` and the lane dies after hours of work. On `--resume`, `_terminal_result_matches_bucket` (`:2235-2257`) re-derives `killed` too, so the record is **reused and the crash repeats** until the state is deleted by hand. No test runs this path end to end. `test_raw_verifier_report_precedence_accepts_a_positive_limit_event` asserts the verifier half, `test_mutation_model_rejects_limit_hit_kill_but_allows_survivor_telemetry` asserts the opposite model half. | AP-1 (W1-1 resolves it): limit beats report. Remove the verifier exemption. Keep the model rule. Add the alignment test (R2-4) |
| R2-2 | P1 (robustness) | `resource_limits.py:264` compares the whole `limit_signature`. Its fields (`:49-62`) include the **configured values** `pids.max`/`memory.max` | Any change to an ancestor's limit value makes a window "incomparable": `docker update --memory`, slice drop-ins, mdt governance, iogov. In practice that is the most likely cause of the 18:58 double abort. The r2fix branch turns this into an *unobserved* window. Under host governance, most windows then become unobserved, and bare failures become `crashed` → R2 claim `ERROR/EXEC_FAILED` (`mutation.py` `judge_mutation`: any crashed → ERROR). The campaign "survives" but ends red. Event counters stay valid when a limit value changes; only an identity change (path, mount, which files exist) invalidates them. | AP-1: split the signature. Identity mismatch → unobserved. Limit-value change → still compared, recorded as advisory telemetry (stderr line + progress `resource_limit_changes`). The implementer must confirm `_candidate_counter_deltas` does not depend on the limits being finite |
| R2-3 | P2 (policy) | `schemas/verdict.schema.json:1719` (drops `resource_limit_evidence` from `required`); `tests/core/test_verdict_conformance.py:1399-1404` (re-pins the v16 digest to `90ede446…`) | **The bytes of the v16 schema change without a version bump.** That breaks A-477 ("an edit … without a VERDICT_SCHEMA_VERSION bump fails here"). It also breaks design §1.7 / §6.9 ("old `assay verify` binaries can still verify new documents"). A 10.0.x verifier refuses a 10.1 verdict that contains an unobserved candidate. Practical consumer impact is about zero: run-gate, cmru and nyxloom do not run older verifiers on newer verdicts, and dstdns pins v15. | **Operator decision D2.** Recommend keeping the relaxation, recorded as an explicit A-477 exception: B182 text, CHANGES "Compatibility" line, test comment (already present). After R2-2, unobserved windows become rare. The alternative (frozen schema + inline retry + lane ERROR) is more code and aborts more often |
| R2-4 | P2 (test gap) | tests | The WP4 acceptance in the design (§7: "producer buckets, revalidation and verifier agree on the table") is missing. `termination_table.py` drives only `test_termination.py` and the WP3 canary tests. R2-1 would have been caught by it. | AP-1: add `tests/core/test_termination_alignment.py` (see the AP-1 brief) |
| R2-5 | P2 (survive load) | `mutation.py:4670-4685` | A limit event during a **witness-prefix replay** (`--reuse-from`) still raises lane `ERROR/EXEC_FAILED` ("refusing to retry"), so one host event aborts the campaign. The replay is an optimization. The fallback full command measures with its own window, and that window decides. | AP-1: discard the replay and fall back to the full command. Print one advisory line. Do not raise |
| R2-6 | P3 | `mutation.py:3026-3040` called at `:4369` | After the classifier has already said "judgeable", `_classify_mutant_result` classifies the same result again (`limits=None`, `decided_by_report=False`). This duplication can drift. | AP-1: map PASSED → survived and FAILED → killed directly. Leave the equivalence split in `_classify_mutant_result_with_equivalence`. Delete `_classify_mutant_result` if it has no other caller (only docstrings reference it) |
| R2-7 | P3 | `tests/fixtures/b105-coverage-exclusions.json` (`mutation.py` lines `[189,196,197]`) | Coverage exclusions are pinned by line number. Every import change in `mutation.py` shifts them, which makes this a merge hot spot for AP-1/5/6, PR0, IO2 and PR1. | Backlog item: key them by the `TYPE_CHECKING` block symbol. Until then each package brief says "regenerate after merging main" |
| R2-8 | P3 | docs `DESIGN-GUIDE.md:715-717` ("precedence over a **later** event"), `CONSUMERS.md:474`, `README.md:286,346` | The window cannot tell whether the event came before or after the failing call, so "later" is not provable. | AP-1 docs |
| R2-9 | info | gates | Gates passed at an earlier tip: `logs/r2fix-tu.log` 22:14 PASS and `logs/r2fix-pre2.log` 22:25 PASS. `da3fe963c` (23:09) and `3c678368a` (23:29) are ungated. | Re-gate after AP-1 |

Good in r2fix, keep:
- the preflight probe stays fail-closed;
- unobserved windows become telemetry plus an advisory line;
- report beats *unobserved* (sound, because absence of observation is weaker than a positive event);
- records with no evidence form are rejected → re-run;
- crashed-PASS-with-limit is rejected → re-run;
- `NO_MEASUREMENT` terminal records are rejected;
- the signature diff detail;
- the cold/declared paths route through the classifier.

### 1.3 `assay-wp3-20261030` @ `76ac1371e` (receipt canary)

| ID | Sev | Where | Finding / failure scenario | Fix |
|---|---|---|---|---|
| W3-1 | P2 | `adapters/python.py:350` `_import_break_statement` (4 lines) | The old injection was one line. With 4 lines, when there is no docstring (insert at index 0), a PEP 263 encoding cookie on line 1 moves to line 5 and stops working. The file is then read as UTF-8, a SyntaxError follows, no receipt is written, and the canary is INCONCLUSIVE. Line numbers in tracebacks also shift by 3 instead of 1. | One line: `_assay_canary_receipt = __import__("os").environ.get("ASSAY_CANARY_RECEIPT"); _assay_canary_receipt and __import__("pathlib").Path(_assay_canary_receipt).write_text("<nonce>", encoding="ascii"); raise AssertionError("assay-canary-import-break")` |
| W3-2 | P2 | `adapters/javascript.py:215` | The multi-line IIFE references `globalThis.process`, `getBuiltinModule` and `require`. In a TS lane whose command type-checks (`tsc --noEmit`, `vitest --typecheck`) without `@types/node`, or with strict lint, the transform fails *before* tests run. No receipt is written, so the canary is INCONCLUSIVE. 10.0.x counted that as caught (falsely). The new result is honest, but a lane that is green today turns red. | Put the IIFE on one line preceded by `// @ts-ignore` (a comment in JS). Document in README (R3 row) that lanes which lint or type-check before tests should use `uncovered-line` |
| W3-3 | P2 | `canary.py:185` `_attribute_control` | A control that **cleanly FAILED** (the lane is red at baseline) is now *recorded* as `control_outcome=INCONCLUSIVE`. That loses the "your suite fails without the transform" diagnostic. The attempt result is INCONCLUSIVE either way. | Record the raw `FAIL` when the termination is judgeable; record `INCONCLUSIVE` only for non-judgeable terminations |
| W3-4 | P2 | `canary.py:508` legacy `run_python_canary`: `_create_receipt(repo.resolve().parent)` | The receipt directory is created in the *parent of the repo*. If the repo is nested in another worktree, that is untracked dirt in the outer repo, and permissions there are unknown. The design said system temp. | `tempfile.mkdtemp(prefix="assay-canary-")` with the default directory for the legacy path. The isolated path (`:828`, snapshot parent under `scratch_root`, which is validated to be outside the repo) is fine |
| W3-5 | P2 | `tests/core/test_canary_receipt.py:487-596` | The Python end-to-end tests use a **fake** process runner that writes the receipt itself. The injected Python statement never runs in a real interpreter or pytest, although the design (§7 WP3) and B175 require it. The Node ESM test (`:598`) is real. CJS and Vitest are not covered. | Add: a real pytest subprocess through `run_isolated_canary` with `default_process_runner` and with a `ProcessTreeProcessRunner`; a real Node CJS test; Vitest if the tester image has it, otherwise record the gap in B175 |
| W3-6 | P2 | docs | No CHANGES entry and no README R3 row update for a consumer-visible behaviour change: import-break now needs a receipt. | AP-3 adds the CHANGES lines now; the full docs go in AP-8 |
| W3-7 | P3 | `canary.py:192` `_receipt_runner` + `runner.py:1477` | The wrapper hides `isinstance(process_runner, ProcessTreeProcessRunner)`. A stray `OSError` from the process-tree runner on the transform half then becomes a candidate `ERROR` instead of a lane `AssayError`. | Forward a marker attribute such as `is_process_tree_runner`, and test it in runner.py |
| W3-8 | P3 | `canary.py:154-177` | `decided_by_report=False` for both halves. **Keep it**: it is the right rule in the credit direction. Note that WP2's competing helper (`wp2 canary.py:486-504`) passes `result.decided_by_report`. | Merge resolution: WP3's helper wins |
| W3-9 | P3 | R1-derived transform (`uncovered-line`) | The classifier gets `outcome=FAIL` (R1), `rc=0`. A limit event or required-unobserved window during a *passing* command therefore makes the uncovered-line canary INCONCLUSIVE, although R-11 says arithmetic on a complete artifact is final. This is fail-safe and unchanged versus main. | Optional: for `uncovered-line`, classify the command (`unit.result.outcome`) rather than the R1 outcome |

Good, keep:
- the env var is set only on the transform half;
- the receipt is outside the snapshot, mode 0700, removed in `finally`;
- the bounded 65-byte read;
- deterministic description phrases, with no nonce in the verdict;
- no output parsing;
- Go keeps the nonce in the panic only (R3 is unregistered for Go);
- SQL keeps `NotImplementedError`;
- the table-driven attribution test.

The verifier (`verify.py:3432-3458`) needs no change, which matches the design.

Consumers that use import-break R3 today: cmru (`src/cmru/config.py`), `libraries/worktree`, mdt-cockpit and `modern-debian-tools-python-debug` (`host-setup/mdt-host-setup-wizard.py`). All run pytest in-process with the environment inherited, so receipts should arrive. AP-9 verifies this before the tester-image bump.

### 1.4 `assay-wp2-20261030` @ `702b8cc30` (R0/R1 wiring)

| ID | Sev | Where | Finding | Fix |
|---|---|---|---|---|
| W2-1 | P2 (merge) | `canary.py:486-504` `_canary_failure_is_incomplete` | This is a semantic and textual conflict with WP3 `_classify_result` on the same call sites. WP2 passes `decided_by_report` from the result, which is the credit direction and therefore wrong for R3. | Merge WP3 first. In AP-4, drop WP2's canary.py hunk completely, except `_run_pipeline(..., classify_r0_termination=False)` (`canary.py:~295`), which WP3's legacy path still needs |
| W2-2 | P2 | `runner.py:1634` `_classify_r0_result` | Under AP-1's new `SELF_SIGNALED`, `term not in JUDGEABLE` would turn SIGSEGV into ERROR, which the R0 design (R-5) forbids. | Use AP-1's `BLOCKING_JUDGEABLE = {PASSED, FAILED, SELF_SIGNALED}` |
| W2-3 | P3 | `runner.py:1768` `_r1_after_r0` | The inherited R1 `ERROR/EXEC_FAILED` has no `detail`. The R1 profile-error refusal is no longer announced. | `detail=refusal_detail("inherited from R0: " + <R0 termination detail>)`, which is deterministic |
| W2-4 | P3 (behaviour, documented) | `tests/core/test_runner_run_lane.py:946-975` | When R0 never started (`NOT_RUN`), R1 changes from `NO_MEASUREMENT/EMPTY_COVERAGE` to `ERROR/EXEC_FAILED`. The rollup is ERROR either way. This goes beyond design R-10 (which listed only infrastructure terminations), but CHANGES records it. | Accept, with one CHANGES sentence naming the NOT_RUN case |
| W2-5 | P3 | `CHANGES.md` | WP2 replaced the WP1 "Changed" entry instead of amending it, so the record that the classifier was added (B173) is gone. | Restore it as part of AP-4; AP-8 consolidates |
| W2-6 | P3 | `runner.py:1799` `execute_command(classify_r0_termination=True)` default | Library callers of `execute_command` now get the external-signal → ERROR mapping by default. This is intended for R0. | Mention it in the CHANGES "library API" line |

Good, keep:
- the `withhold_*` helpers are deleted;
- `decided_by_report` comes from the B078 summary;
- PASS stays final;
- `command_finished.resources` is additive;
- the advisory line goes only to diagnostics, never to tails or the verdict (`test_command_tail_is_never_appended_by_assay`);
- external SIGKILL → `killed-by-signal:9`.

Zero-delta pressure is filtered upstream (`liveness_resources.py:270-288`), so the advisory line does not appear without real pressure.

### 1.5 Cross-cutting

- **The judge identity includes the assay version** (`mutation.py` `judge_sha256(tool_version=_tool_version())`). Every record from the aborted 10.0.1 campaigns will be a cache miss under any new build, including dev builds. Old and new records can never mix. The restarted campaigns re-execute everything: PWMCP 39 candidates, IOBench about 190+ (~6 h). That is correct, because classification changed. Do not engineer cross-version reuse.
- **A crashed candidate makes R2 `ERROR/EXEC_FAILED`** (`judge_mutation`). Until WP7 exists, recovery is `assay run … --resume --rejudge crashed` (alias `error`). The 10.0.2/10.1.0 notes must say so.
- **Docs hot spots** for every branch: `CHANGES.md [Unreleased]`, `nyxloom-trove/4-backlog.md` B173-B182, `docs/DESIGN-GUIDE.md` host-load sections, `README.md` load and R3 rows, `docs/CONSUMERS.md`. Merge order resolves the textual conflicts; AP-8 resolves the semantics.

---

## 2. Design critique (where I disagree with, or simplify, the 10-30 design)

1. **The real axis is credit versus blocking, not one precedence list.**
   - The policy "failure counts only when the process died cleanly or the framework reported" is safe for verdicts that only *block* (R0/R1). A false FAIL is still red.
   - It is unsafe for verdicts that *grant credit*: R2 `killed` raises the score, and an R3 "caught" certifies the gate. There, any positive evidence of a non-clean death must win.
   - Rule: **a positive limit event, a self-inflicted signal or a leak always denies credit. A complete report outranks only an *unobserved* window or an *external signal that arrived after the report*.** This needs only three explicit sets in `termination.py`:
     - `BLOCKING_JUDGEABLE = {PASSED, FAILED, SELF_SIGNALED}` (R0/R1);
     - `CREDIT_JUDGEABLE = {PASSED, FAILED}` (R2/R3);
     - `JUDGEABLE` kept as an alias of `CREDIT_JUDGEABLE`.
   - The table gains an explicit column per projection. The hidden overrides in `mutation.py` (`_non_exited_judgeable_terminal`) and `canary.py` collapse into the table.
2. **Fix unobserved windows at the source; do not cope with them.** The r2fix rule ("unobserved → only a witnessed exit 1 may be killed") is correct but treats a symptom. A changed limit *value* does not invalidate monotonic event counters. Splitting the signature (R2-2) makes unobserved windows rare: observer down, or cgroup migration. Then the conservative rule costs almost nothing.
3. **The shared table must be enforced, not just shared.** The design's DRY claim held only for `test_termination.py`. Producer bucket, state revalidation, verifier check and the verdict model each need a parametrized test over the same rows (AP-1). This is the single highest-value test in the program; it would have caught R2-1.
4. **The receipt canary is the right mechanism; keep it.** I found no simpler alternative: output parsing is forgeable and language-specific, and a file next to the module is Git-visible dirt. Two refinements:
   - one-line injection, for line and cookie parity (W3-1, W3-2);
   - the receipt proves *execution*, not *causation*. A target that swallows the import error and then fails elsewhere would still count as "caught". Document this as a known limit; it is the same trust tier as R1 artifacts.
5. **WP7 is not optional for the operator's goal.** Without it, every host-induced `crashed` turns the campaign ERROR. Ship WP7 in 10.1.0. If it slips, document `--rejudge crashed`.
6. **Fast-track 10.0.2.** r2fix + AP-1 is self-contained in `mutation.py`/`verify.py`/`verdict.py`/`resource_limits.py`/`termination.py`. R0/R3 behaviour does not change, because WP2/WP3 are not merged. Releasing it as 10.0.2 under the existing `<10.1.0` waiver unblocks the two campaigns about 2-4 days earlier than waiting for WP2/WP3/WP5 fix rounds and the consumer smoke. The alternative is to fold it into 10.1.0 and keep the campaigns parked.
7. **For 11.0.0, PR1 is fine as planned in the COW plan, with two preconditions.**
   - Point 1 above is mandatory, otherwise overlap means false kills.
   - Each overlapping window's limit delta (LimitLedger) has to be fed as `limits`.

   The simpler alternative to the in-process window host is "N local `assay run --shard i/N` processes + RS2 merge". It needs no scheduler, but each shard re-runs the baseline (60 min for assay's own suite). Keep it only as a fallback if PR1 slips. RS1/RS2 are useful on their own and can ship in 10.3.0, because v16 replay-merge works.
8. **v17 scope (WP8)** gathers every wire change in one place:
   - `canary_attempt.cause` (`receipt|absent|null`);
   - remove `process_tree_evidence.cleanup_seconds` (and the normalizer `gate/python/qualify_sql.py:1010-1026`);
   - process-tree `signal`;
   - `judgment.r2.jobs ≥ 1` + `attribution ∈ {serial, overlap-conservative}`;
   - shard/campaign fields;
   - B171 `underlying_outcome` + descendant identity on a leak;
   - `resource_observation` (`unobserved` + reason) in the verdict, if the operator rejects D2.
9. **Do not add sub-cgroups or host PSI.** The COW plan rejects these too (read-only cgroup rule, Q-D); I agree.

---

## 3. Package plan

### 3.0 Rules every luna brief must include (copy verbatim)

- Read first: this report §1 rows for your package, the design §§ named in the brief, `assay/AGENTS.md` / `CLAUDE.md` if present, and `assay/nyxloom-trove/4-backlog.md` for your B-items.
- Host shared with production game servers:
  - focused pytest only, serial, `nice -n19 ionice -c3`;
  - no `run-gate`, no docker, no container builds (the controller runs gates);
  - never commit while a gate runs in your worktree.
- Red first: commit the failing tests (one commit), then the implementation. Every new branch is a row in a parametrized table.
- Delete before add.
- No `pragma: no cover`. Grep for it in the acceptance step.
- No type-guard branches in private pure helpers.
- Keep `self-qualification-preflight` at 100% line+branch over every target. Add new modules to **all three** target inventories in `assay.toml`; `test_self_lane` asserts they are equal.
- No new verdict keys in 10.x. The progress stream may get additive keys only.
- Docs agnostic of host and server names.
- After merging main, regenerate `tests/fixtures/b105-coverage-exclusions.json` line numbers.
- Every Bash call carries a `description` that states its intent.
- Stop and report at ~120k context or ~60 tool calls, at a green-test boundary. Write a continuation brief.
- Commit trailer per the repo rules. Never push. Never touch main.

### 3.1 Dependency graph

```
AP-1 (r2fix fix round, includes WP1.1) ──► merge ──► AP-2 (10.0.2, optional) ──► restart PWMCP/IOBench R2
   │
   ├──► AP-3 (WP3 fix) ──► merge ──► AP-4 (WP2 fix) ──► merge ──► AP-7 (WP6, optional)
   ├──► AP-5 (WP7 serial retry)        ┐  (mutation.py: serialize merges AP-5 → AP-6)
   └──► AP-6 (PR0 per-candidate metrics)┘
AP-3,AP-4,AP-5,(AP-6),(AP-7) ──► AP-8 (WP5 consolidation + waiver <10.2.0) ──► AP-9 (consumer canary smoke) ──► AP-10 release 10.1.0
10.1.0 ──► CW8 ──► CW9 (+IO2) ──► 10.2.0 (waiver <10.3.0)
10.1.0 ──► RS1 ──► RS2 ──► 10.3.0 (or fold into 11.0.0)
WP7 + CW9 + RS1/RS2 ──► PR1 + WP8 (+B171) ──► 11.0.0 (v17, waiver <11.1.0) ──► PR2 measurement; run-gate PR3 enable
```

Parallelism with 3-4 luna agents at once:
- AP-1 alone first.
- Then AP-3 ∥ AP-5 ∥ AP-6 (AP-6 can start before AP-5 merges; it touches different hunks).
- Then AP-4.
- CW8 can be *developed* on its own worktree as soon as AP-1 merges (it touches `isolation.py`, a disjoint file), but it merges after AP-8.

### AP-1: r2fix fix round 2 + classifier precedence (WP1.1). Size M. Branch `assay-r2fix-20261030` (after the running round reports)

**Files/functions**

1. `src/assay/termination.py`:
   - precedence becomes `NOT_RUN → LEAKED → TIMED_OUT/HUNG → PASSED → LIMIT_EVENT → report-FAILED → KILLED_EXTERNALLY → SELF_SIGNALED → UNOBSERVED → INCONCLUSIVE → FAILED`;
   - add `Termination.SELF_SIGNALED`: `returncode < 0` and not external, or leader `signaled` with a self-inflicted rc;
   - add `BLOCKING_JUDGEABLE`, `CREDIT_JUDGEABLE` (= `JUDGEABLE`);
   - `r2_bucket(SELF_SIGNALED) = "crashed"`;
   - `detail(SELF_SIGNALED) = None`;
   - add the docstring table of per-site inputs (W1-4).
2. `src/assay/contention.py`: `observation_input(obs) -> Literal[...]` (W1-3); replace the hand mappings in `mutation.py`.
3. `tests/core/termination_table.py`:
   - flip `report-fail-beats-limit` to `limit-beats-report` (LIMIT_EVENT, R0 ERROR, R2 crashed, R3 INCONCLUSIVE);
   - SIGSEGV rows → SELF_SIGNALED (R0 FAIL/COMMAND_FAILED, R2 crashed, R3 INCONCLUSIVE);
   - add a `r0_outcome` projection helper that honours `BLOCKING_JUDGEABLE`;
   - keep `clean-report-fail-beats-unobserved-window`.
4. `src/assay/resource_limits.py:49-62,264`: split `limit_signature` into an identity part (path, mount_id, every `*.present` flag, localevents flags) and configured values (`pids.max`, `memory.max`).
   - `ResourceLimitEvidence.between` raises only on an identity change.
   - A value change returns evidence plus a `limit_value_changes` tuple, which is in-memory and not serialized to the verdict.
   - The caller prints one advisory line and adds additive progress key `resource_limit_changes` to the `candidate` event.
   - **Verify first** that `_candidate_counter_deltas` selects cgroups independently of limit finiteness. If it does not, select on the union of presence flags.
5. `src/assay/mutation.py`:
   - `_classified_bucket` (`:4341`): use the new classify; delete the second classify via `_classify_mutant_result` (R2-6); keep `_non_exited_judgeable_terminal` only for PASS with a non-`exited`/non-`signaled` leader, as a table row.
   - Refactor `_classified_bucket` into a module-level pure `_bucket_for_run(run, *, equivalence_artifact, baseline_equivalence, kill_signal_artifact)` so the table can drive it.
   - `_terminal_result_matches_bucket` (`:2114`): use the same helper semantics.
   - Witness-prefix replay limit event (`:4670-4685`): fall back to the full command (R2-5).
6. `src/assay/verify.py:2266-2275`: a positive limit delta in `killed` is **always** a failure (remove the clean-report exemption). Keep `:2185-2196` (an unobserved `killed` needs a clean failed-call report).
7. `src/assay/verdict.py:2244-2253`: unchanged (the model rule is the reference). Align the field docs and the schema description (`verdict.schema.json:1531`).
8. Docs: `DESIGN-GUIDE.md:710-722` (drop "later event"), `CONSUMERS.md:466-480`, `README.md:286,346`, CHANGES (Fixed + a "Compatibility: v16 schema relaxation" line if D2 is accepted), B182 status text (record D2 as the A-477 exception).

**Behaviour contract**
- PASS → survived, always (also with a limit event or an unobserved window).
- Observed limit event + FAIL (with or without a report) → crashed.
- Unobserved + a clean witnessed exit 1 → killed. Unobserved + anything else non-PASS → crashed.
- Self-inflicted signal → crashed (R2).
- An ancestor limit-value change during a window → observed (evidence present) + advisory.
- Producer, revalidation, verifier and model agree on every row.

**Tests that must fail first**
- `test_termination_alignment.py::test_producer_revalidation_verifier_model_agree[<row>]`: for every `ALL_CASES` row that has a process tree, build `_MutantRun` → `_bucket_for_run`; the state payload → `_terminal_result_matches_bucket`; a verdict entry dict → `verify._check_b145_resource_limit_evidence` + `_check_b162_*`; and `Mutation(...)` construction. All agree, or all refuse.
- `test_witnessed_kill_with_limit_event_is_crashed_end_to_end` (`run_mutation` with a fake counter reader; must fail on the tip by raising `ValueError` at verdict assembly).
- `test_resume_of_witnessed_limit_kill_record_is_rerun_not_reused`.
- `test_limit_value_change_keeps_window_observed` and `test_identity_change_is_unobserved`.
- `test_witness_prefix_limit_event_falls_back_to_full_command`.
- `test_self_signaled_r2_is_crashed_r0_is_fail`.

**Acceptance**
- focused tests green;
- the controller runs `run-gate tester-unified` and `run-gate self-qualification-preflight` (100%) on the final tip;
- independent review (reasonix/luna read-only), with the reviewer given this report's §1.2 rows;
- grep shows no `pragma: no cover`.

**Hot spots**
- `mutation.py` (everyone);
- `termination_table.py` (WP2/WP3 import it; keep row names stable except the renamed ones listed above);
- the exclusions fixture.

**Brief must say**
- "do not change the verdict schema beyond what D2 allows";
- "the model rule in verdict.py is the reference";
- "do not touch runner.py/canary.py";
- "WP2/WP3 will adapt".

### AP-2: release 10.0.2 (controller; optional, recommended)

- Preconditions: AP-1 merged, tester-unified + preflight + provisional PASS on main.
- Steps:
  - CHANGES `[Unreleased]` reviewed: Fixed = B181, B182 + AP-1. Note the v16 relaxation (D2), the `--rejudge crashed` recovery, and that 10.0.x judge identities change, so campaign state is re-executed.
  - `BUILDX_BUILDER=default nohup sh -c "cmru release assay --set-version 10.0.2 > logs/assay-10.0.2.log; echo EXIT=\$? >> logs/assay-10.0.2.log"`. Trust only `EXIT=`.
  - Install the wheel; deploy wherever the R2 campaign lanes get assay.
  - Write `/workspaces/dstdns/.assay-inbox/release.json`: sha256 from the `.pyz.sha256` sidecar, confirm the `.pyz` asset exists, `landed: []`, and one-line notes.
- B169: the window `>=10.0.0,<10.1.0` already covers it. No edit.
- Then restart PWMCP and IOBench whole-source R2 on fresh worktrees from main. Use the same command. The old state dirs stay as evidence; their records are cache misses anyway.

### AP-3: WP3 fix round. Size S-M. Branch `assay-wp3-20261030` (merge `origin/main` after AP-1)

**Files**
- `adapters/python.py:350`: one-line statement (exact text in §1.3 row W3-1).
- `adapters/javascript.py:215`: one-line IIFE preceded by `// @ts-ignore\n`.
- `canary.py`:
  - `_attribute_control` records the raw FAIL for judgeable terminations (W3-3);
  - the legacy receipt directory goes to system temp (W3-4);
  - `_classify_result` uses `CREDIT_JUDGEABLE` / `SELF_SIGNALED` (→ INCONCLUSIVE) and `contention.observation_input`;
  - optional W3-9 (classify the command outcome for `uncovered-line`).
- `runner.py:1477`: marker attribute instead of `isinstance` (W3-7), forwarded by `_receipt_runner`.
- CHANGES (Changed: the import-break receipt requirement; INCONCLUSIVE for lanes that fail statically before tests).
- README R3 row (JS/TS needs a receipt; static-check lanes should prefer `uncovered-line`).
- B175 status.

**Contract**
- Design R-17…R-21, plus: a SELF_SIGNALED transform → INCONCLUSIVE.
- A control that cleanly failed is recorded as FAIL; its attempt is INCONCLUSIVE.

**Tests (fail first)**
- `test_python_import_break_is_one_line_and_preserves_line2_cookie`
- `test_js_import_break_is_one_line_with_ts_ignore`
- `test_control_clean_fail_is_recorded_as_fail`
- `test_legacy_receipt_dir_is_system_temp`
- `test_real_pytest_import_break_writes_receipt` (real `python -m pytest` subprocess, `default_process_runner`; a second parametrization with `ProcessTreeProcessRunner`)
- `test_node_cjs_import_break_writes_receipt`
- Vitest if available in the image, otherwise record the gap in B175.
- `test_transform_oserror_through_receipt_wrapper_is_lane_error`

**Acceptance**
- focused tests;
- the controller runs tester-unified, preflight, and `self-qualification-provisional` (R3 on `src/assay/cli.py` must PASS with "receipt matched");
- review.

**Hot spot**: canary.py (AP-4 must not touch it after this).

### AP-4: WP2 fix round. Size S. Branch `assay-wp2-20261030` (merge main after AP-3)

**Steps**
- Resolve canary.py by taking main (WP3). Keep only `classify_r0_termination=False` in `_run_pipeline` if main lacks it.
- `_classify_r0_result` uses `BLOCKING_JUDGEABLE`.
- `_r1_after_r0` gets a deterministic detail (W2-3).
- Restore the WP1 CHANGES line and add the NOT_RUN sentence (W2-4/5/6).
- Delete `contention.withhold_*` (if main still has them after AP-3) and their tests.

**Tests (fail first)**
- `test_r0_sigsegv_stays_fail_with_self_signaled_termination`
- `test_r0_report_fail_with_limit_event_is_error` (new precedence)
- `test_inherited_r1_error_carries_r0_detail`

**Acceptance**: tester-unified + preflight + provisional, then review.

### AP-5: WP7 serial tail retry (B179). Size M. New branch from main after AP-1

**Files**: `mutation.py` `_execute_mutation_jobs` sweep end (after the main loop, before bucket assembly ~`:5204`), `run_mutation`.

**Contract**
- After the sweep, while the campaign deadline allows, re-run once, serially, each candidate executed *in this invocation* whose bucket is `crashed`, `budget_exceeded` (per-candidate, not the campaign deadline mask) or `hung`.
- Use the same `_run_one` and the same classifier. The retry result replaces the first.
- Judged buckets (killed/survived/equivalent) are never retried.
- Unattempted (deadline-masked) candidates are not retried.
- The state record is overwritten by the retry's record (same schema).
- Progress gets an additive `candidate_retry` event `{candidate_id, first_bucket, final_bucket}`.
- Resumed crashed records are *not* auto-retried. `--rejudge crashed` stays the explicit path. Document it.

**Tests (fail first)**
- `test_infra_buckets_retried_once_serially`
- `test_retry_classified_by_same_rules`
- `test_judged_buckets_never_retried`
- `test_retry_respects_campaign_deadline`
- `test_retry_overwrites_state_record_and_resume_reuses_it`
- `test_progress_emits_candidate_retry`

**Acceptance**: preflight + tester-unified, review, and an R2 pilot on a small lane (controller).

**Hot spot**: `mutation.py` (merge before AP-6).

### AP-6: PR0 per-candidate resource metrics. Size S. New branch from main after AP-1

**Files**
- the liveness tree sampler (`liveness.py` / `_measured_resources` in `mutation.py:~3000`): sum `/proc/<pid>/io` `write_bytes` over the tree;
- the progress `candidate` event gains additive `resources.peak_rss_bytes`, `resources.write_bytes`, `resources.cpu_seconds`, `judge_rss_bytes`;
- the state record `resources` (already exists; additive keys must pass the strict validator, so check `_load_validated_state_record`).

**Tests**
- `test_candidate_event_has_peak_rss_and_write_bytes`
- `test_missing_proc_io_is_null_not_error`
- `test_state_record_resources_roundtrip`

**Acceptance**: preflight. No verdict change.

**Why now**: the restarted PWMCP/IOBench campaigns feed run-gate history for PR3.

### AP-7: WP6 control reuse (B178). Optional; size S-M. After AP-4

**Files**: `canary.py` `run_isolated_canaries` (gets `baseline_outcome`), `runner.py` R3 dispatch (~`:5565`).

**Contract**: the control is skipped only when the same commit and plan already produced R0 PASS (and R1 PASS when R1 is declared) in this lane run. Otherwise it is re-run.

**Tests**
- `test_control_reuses_baseline_without_second_run`
- `test_control_reruns_when_baseline_not_pass`

**Acceptance**: provisional wall time roughly halved (controller compares logs).

### AP-8: WP5 consolidation + 10.1.0 prep. Size S

- **Docs**:
  - one DESIGN-GUIDE section "Host load and verdicts", which replaces the per-branch sections, plus a credit-versus-blocking table;
  - pointers in README and CONSUMERS;
  - the README language matrix row for JS R3;
  - CHANGES `[Unreleased]` written as one consumer-facing list:
    - R0: external signal or limit event → ERROR; SIGSEGV stays FAIL; NOT_RUN R1 → ERROR;
    - R2: PASS+limit = survived; limit always denies a kill; unobserved rule; witness-prefix fallback; WP7 retry;
    - R3: receipt requirement; static-failure lanes;
    - progress keys added;
    - v16 relaxation (D2);
    - judge identity changes, so campaign state is re-executed.
- **Backlog**: B173-B179 and B182 statuses; close or retarget B177 (the parallel-safety lane is already gone).
- **Waiver `<10.2.0`**:
  - `tools/self-qualification-gate.sh:816` `WINDOW = ((10, 0, 0), (10, 2, 0))`, plus `:887` message and `:930` document window;
  - `DECISION` text gains the operator's extension date;
  - `run-gate.toml:91` comment;
  - `gate/tests/test_self_lane.py` and `tests/core/test_docs_examples_and_vocabulary.py` expectations;
  - B169 text ("valid for `>=10.0.0,<10.2.0`; extended <date> by operator").
- Update the in-repo design copy `nyxloom-trove/reports/ASSAY-DESIGN-OPUS-2026-10-30.md` with an addendum pointing to this report.
- **Acceptance**: doc-anchor tests in tester-unified; provisional PASS on the integrated head; review.

### AP-9: consumer canary smoke (controller, or luna with gate permission). Size S

With the 10.1.0 candidate wheel, run each import-break R3 lane once:
- cmru (`src/cmru/config.py`);
- `libraries/worktree` (`src/worktree/core.py`);
- mdt-cockpit and `modern-debian-tools-python-debug` (`host-setup/mdt-host-setup-wizard.py`).

Expect attempt PASS with "receipt matched".

Run nyxloom's `uncovered-line` lane once as a control.

Any INCONCLUSIVE is a blocker for the *tester-image bump*, not for the assay tag. Fix it lane-side or switch to `uncovered-line`.

### AP-10: release 10.1.0 (controller)

Same procedure as AP-2 with `--set-version 10.1.0`, after AP-8 is merged and tester-unified, preflight and provisional are PASS on main.

dstdns notice:
- `landed: []`;
- notes: "R0 external-kill/limit → ERROR; R3 import-break requires execution receipt; R2 never aborts on unstable resource windows; see CHANGES";
- dstdns pins v15/9.0.0, so the notice is informational.

Then install it, bump the tester image after AP-9, and update the HANDOFF.

### 10.2.0: CW8, CW9, IO2 (COW plan §4.2, §6 rows CW8/CW9/IO2)

- **CW8** (workspace seam + Level A): `src/assay/workspace.py`, `_cow_workspace.py`, hooks in `isolation.py`/`runner.py` as listed in the COW plan §4.2. It can be developed in parallel from the AP-1 merge onward, but merges after AP-8.
  - Hot spot: `runner.py` workspace factory lines also touched by AP-4/AP-7. Merge main before review.
- **CW9** (Level B template clones), with the acceptance "20-candidate pilot identical buckets".
- **IO2**: `_durable.fsync` raw syscall for state, verdict and cli writes. This is a monkeypatch test update list in the COW plan. Small; it can also ride in 10.1.0 if ready early.
- Waiver `<10.3.0`: the same edit set as AP-8.

### 10.3.0 (or fold into 11.0.0): RS1, RS2 (COW plan §4.9)

- RS1: the campaign manifest + `--campaign` refusal on identity mismatch + pinned budget (`cli.py` campaign verbs).
- RS2: `assay state merge`, which revalidates each record through the AP-1 alignment helpers, treats duplicates as idempotent and refuses conflicts, plus the replay merge (`--resume` executes no candidate).
- Acceptance: a local 2-container shard + replay gives the same buckets as serial (controller pilot).
- This gives the operator a working multi-host or multi-container path on v16 before PR1.

### 11.0.0: PR1 + WP8 (+B171), verdict v17

- **PR1** (COW plan §4.8.2):
  - `_window_host.py` (one process per candidate; fixes B165);
  - the scheduler in `_execute_mutation_jobs`;
  - `LimitLedger` (every window gets the union of limit deltas over its interval → `classify(limits=…)`; with AP-1 precedence any overlapping FAIL is crashed and PASS is survived);
  - `--jobs N ≤ jobs_max`;
  - `jobs>1` needs `workspace.cheap_clone`.
  - Prerequisites: AP-1, AP-5, CW9. Size L. Split into PR1a (window host, used at jobs=1, no behaviour change) and PR1b (scheduler + ledger + `--jobs`).
- **WP8 v17**:
  - `judgment.r2.jobs`, `attribution`;
  - shard and campaign fields;
  - `canary_attempt.cause` (verifier re-derives the R3 attempt from cause);
  - remove `cleanup_seconds` (+ the normalizer `qualify_sql.py:1010-1026` and the SQL witness fixture regeneration);
  - process-tree `signal`;
  - B171 `underlying_outcome` + descendant identity;
  - if D2 is rejected, a `resource_observation` field.
  - The verifier accepts v17 only. Tests: the frozen digest for v17 plus "v16 refused with one diagnostic".
- **Release**: waiver `<11.1.0`; PR2 scaling measurement on r1002 afterwards (crashed < 2%, bucket identity versus serial); run-gate PR3 enables `--jobs N`. N comes from `ram_target` / average candidate RAM (PR0 history), the cpu cap, `jobs_max` and the candidates remaining. Assay never derives N.
- Consumer migration notes: run-gate reads only top-level fields (unaffected); cmru reads exit codes; dstdns pins v15.

### Missing items (filed as backlog when the next package touches them)

- M1: SQL native R2: count a present `kill_signal` artifact as the "report" for unobserved windows. Today every unobserved SQL FAIL is crashed (P3).
- M2: key `b105-coverage-exclusions.json` by symbol rather than line (R2-7).
- M3: document in DESIGN-GUIDE that receipts prove execution, not causation (critique 4).
- M4: R2 campaign restart runbook: fresh worktree from main, the old state dir kept as evidence, `--rejudge crashed` until WP7, read progress at most every 20 min. This belongs in the run-gate R2 docs.

---

## 4. Operator decisions needed (recommended default first)

| # | Question | Default |
|---|---|---|
| D1 | Does an observed limit event deny R2 kill credit and R3 "caught" even with a complete failure report (and make R0 ERROR instead of FAIL)? | **Yes**, for all rigors (one rule; R0 stays blocking either way) |
| D2 | v16 schema relaxation (`resource_limit_evidence` optional) inside v16, as an A-477 exception? | **Accept** (no practical consumer breaks; avoids aborts). The alternative is a frozen schema + inline retry + lane ERROR |
| D3 | Release r2fix+AP-1 as **10.0.2** now to restart the PWMCP/IOBench R2 campaigns early? | **Yes** |
| D4 | WP7 (serial retry) inside 10.1.0? | **Yes** |
| D5 | B169 waiver extension to `<10.2.0` for 10.1.0, later `<10.3.0` (10.2.0) and `<11.1.0` (11.0.0) | Yes, per plan |
| D6 | Witness-prefix replay limit event: fall back to the full command (no lane abort)? | **Yes** |
| D7 | WP6 control reuse in 10.1.0? | Only if AP-3/AP-4 land quickly; otherwise 10.2.0 |
| D8 | Restart the aborted campaigns only on a released build (records are invalidated by every version change)? | **Yes**; do not run them on dev builds |

---

## 5. Resume notes (for SendMessage follow-ups and the pre-release delta review)

- Branch tips reviewed:
  - r2fix `3c678368a` (ungated: the PASS logs `logs/r2fix-tu.log` 22:14 and `logs/r2fix-pre2.log` 22:25 predate `da3fe963c` 23:09 and `3c678368a` 23:29);
  - wp2 `702b8cc30`;
  - wp3 `76ac1371e`;
  - main `608d05dbc`.
- For a delta review: `git diff <tip-above>..<new tip>` per branch, then check every §1 row ID: closed, or still open with reason.
- The key invariants to re-check in any later R2 change:
  1. the model rule `verdict.py` "limit_hit cannot be killed";
  2. `_bucket_for_run` == `_terminal_result_matches_bucket` == `verify._check_b145/_b162` over `termination_table.ALL_CASES`;
  3. PASS → survived always;
  4. no verdict key from host measurements.
- Waiver edit set: `tools/self-qualification-gate.sh:816,887,930` + DECISION text, `run-gate.toml:91`, `gate/tests/test_self_lane.py`, `tests/core/test_docs_examples_and_vocabulary.py`, the B169 text.
- dstdns notice: `/workspaces/dstdns/.assay-inbox/release.json` (CONTRACT.md in the same dir; sha256 from the release sidecar; `.pyz` must exist).
- Consumers with import-break R3: cmru, `libraries/worktree`, mdt-cockpit, `modern-debian-tools-python-debug`; nyxloom uses `uncovered-line`.
