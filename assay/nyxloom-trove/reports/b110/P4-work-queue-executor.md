# B110-P4 — Bounded work-queue executor for native mutation candidates

| Field | Value |
|---|---|
| Backlog | **B115** (split from B110) |
| Branch | `assay-b110-p4-queue`, from the integration line `assay-b105-evidence-integrity` (after the plan §11.1 reconciliation) |
| Depends on | **P0** (B111), which adds per-candidate `cpu_seconds` / `peak_rss_bytes` / `phase_seconds` fields to the `candidate` progress event and the state record. This package carries whatever P0 added, unchanged. |
| Contract class | **2b**: the public behavior is fixed; the private construction of the queue loop is left to you |
| Implementer | Opus (fresh session) |
| Decisions | **A-467 (plan D3)**: the lane may run a bounded concurrent work queue whose aggregate RSS stays inside the 2 GiB cap; concurrency is scheduling and never changes the inventory or a classification. It amends A-462's "serially". Binding earlier decisions, unchanged: A-082, A-113, A-122, A-160, A-193, A-195. |
| Size | M: one function body in `mutation.py`, one docstring, one DESIGN-GUIDE paragraph, one test rewrite, three new tests |

**Not in scope:** the B105 lane's `judge.mutation.jobs` value stays `1` here. Plan §9.3 step 1 changes it after the pilot returns GO.

---

## Context to read first

Paths are relative to `assay/`. Line numbers were checked at HEAD `db85f747`.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §0 (host-load rule), §3 D3, §4 and §10.
2. `src/assay/mutation.py`:
   - `:755-766`: `ExecutorFactory` and `_default_executor_factory` (`ThreadPoolExecutor(max_workers=jobs)`).
   - `:2072-2085`: the `run_mutation` docstring paragraph that describes **WAVES**. You rewrite it.
   - `:2099-2102`: the `jobs` validation, which stays unchanged.
   - `:2857-2888`: `_run_one(index)`, the unit of work. Not changed.
   - `:2889-2994`: **the wave loop you replace.** The fields are `results`, `budget_exceeded_mask` and `fatal`; then come the `candidate` progress event at `:2929-2955` and the state record at `:2956-2989`.
   - `:2999-3000`: `raise fatal` after the loop.
   - `:3009-3035`: position-aligned bucket construction, which stays unchanged.
3. `docs/DESIGN-GUIDE.md:1324-1334`: the peak pack-space paragraph. It contains the sentence "since P23 submits mutation work in waves of size `jobs` and awaits each wave before starting the next". You rewrite that sentence.
4. `nyxloom-trove/decisions.md`: the rows A-082 (`:280`), A-113 (`:361`), A-122 (`:375`), A-160 (`:453`), A-193 (`:516`) and A-195 (`:518`). Read only these rows.
5. The tests that pin the current semantics. They must stay green **without edits**, except the one named in Work step 5:
   - `tests/test_mutation_executor_bound.py`. `_RecordingExecutor` at `:52-70` wraps a real `ThreadPoolExecutor`; the tests are at `:122-276`.
   - `tests/test_mutation_isolation.py`: `_SynchronousExecutor` at `:81-107` returns real, already-resolved `Future`s; `_make_decide_by_mutated_content` is at `:110`; the tests are at `:136`, `:172` and `:358`.
   - `tests/test_mutation_classification.py:92`: a second `_SynchronousExecutor`.
   - `tests/test_runner_p23_cleanup_and_budget.py`. Line `:295`: every identity after an expiry is budget-stopped, and with `jobs=1` exactly `["baseline", "mutant"]` units run. Lines `:365` and `:471` cover the P22 policy refusal and the absorbed integrity-check expiry.
   - `tests/test_b105_mutation_boundaries.py`. Line `:390` checks that a direct `_execute_mutation_jobs` Git failure propagates. **`:432`** is the duck-typed fake executor that this package converts.
   - `tests/test_mutation_progress_budget_plan.py:58-160`: candidate indices `[-1, 0, 1]` appear in order.
6. The external consumers of `candidate_index` order. Read only:
   - `../run-gate-project/run-gate.py:6421-6470` and `:6565`. The RG-36 watcher takes the **newest** event's `candidate_index` as the completed count.
   - `src/assay/analysis.py:703-760`. `_report_progress` keeps the last-seen `candidate_index` (`count_keys` at `:716`).

## Implementation packet (normative)

### Interfaces

- Owner: `src/assay/mutation.py::_execute_mutation_jobs`. Its signature is **unchanged**. `run_mutation`'s signature is unchanged.
- `executor_factory(jobs)` is called **exactly once**, with the caller's `jobs`, and only when `total > 0` (the existing early return at `:2316-2322` stays).
- Work is submitted as `pool.submit(_run_one, position)`, with exactly one positional argument. The test fakes depend on that shape.
- Completion is observed with `concurrent.futures.wait(in_flight, return_when=FIRST_COMPLETED)`. Every executor used in production or tests must therefore return real `concurrent.futures.Future` objects.

### Required flow

This replaces `:2893-2994`. The pseudocode fixes the semantics, not the syntax.

```
results = [None] * total
budget_exceeded_mask = [False] * total
resolved = [False] * total          # result recorded, masked, or fatal-errored
fatal = None
stop_submitting = False
next_to_submit = 0
next_to_emit = 0                    # reorder-buffer cursor for `candidate` events
in_flight: dict[Future, int] = {}

with executor_factory(jobs) as pool:
    while True:
        # 1. Fill: in position order, never more than `jobs` outstanding.
        while (not stop_submitting and fatal is None
               and next_to_submit < total and len(in_flight) < jobs):
            in_flight[pool.submit(_run_one, next_to_submit)] = next_to_submit
            next_to_submit += 1
        if not in_flight:
            break
        # 2. Observe at least one completion.
        done, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
        # 3. Handle the done batch in ascending POSITION order (ties/determinism).
        for future in sorted(done, key=in_flight.__getitem__):
            position = in_flight.pop(future)
            try:
                results[position] = future.result()
            except AssayError as exc:
                if exc.outcome is BUDGET_EXCEEDED and exc.reason_code is LANE_TIMEOUT:
                    budget_exceeded_mask[position] = True
                    stop_submitting = True
                elif fatal is None:
                    fatal = exc               # first fatal = lowest position in the
                                              # earliest done batch that carried one
            resolved[position] = True
            run = results[position]
            if run is not None:
                bucket = _classified_bucket(run)
                write the STATE RECORD now (unchanged payload, :2956-2989)
                stage the `candidate` PROGRESS event for `position` (unchanged payload, :2929-2955)
        # 4. Flush the reorder buffer: emit staged events for next_to_emit, next_to_emit+1, …
        #    while resolved[next_to_emit]; a resolved position with no staged event
        #    (masked or fatal) is skipped silently, exactly as today.
        flush()
        if fatal is not None:
            stop_submitting = True
    # 5. After the pool context exits (every in-flight future has closed its snapshot):
    if stop_submitting:
        for leftover in range(next_to_submit, total):
            budget_exceeded_mask[leftover] = True
            resolved[leftover] = True
    flush()          # emits any remaining staged events in position order
if fatal is not None:
    raise fatal      # unchanged (:2999-3000)
```

**Invariants that the flow must keep.** They are the same as today; only the barrier goes.

| # | Invariant | Where it is proven today |
|---|---|---|
| I1 | The factory is built once, with exactly `jobs` (A-082/A-122) | `test_mutation_executor_bound.py:122,131` |
| I2 | Each position is submitted exactly once, in position order | `:141` (`submitted == total`) |
| I3 | At most `jobs` futures are outstanding at any moment | new test T1 |
| I4 | Results are position-aligned; buckets are identity-ordered with no re-sort (A-113) | `:189`; `test_mutation_isolation.py:172,358` |
| I5 | Only `BUDGET_EXCEEDED/LANE_TIMEOUT` counts as expiry. The expired position and every **unsubmitted** position are masked `budget_exceeded`. In-flight positions finish and remain evidence (A-160/A-193). | `test_runner_p23_cleanup_and_budget.py:295,471` |
| I6 | Any other `AssayError` is fatal: submission stops, in-flight futures are consumed, their progress and state are still written, and the first fatal is re-raised unchanged (A-195) | `test_b105_mutation_boundaries.py:390,432`; `test_runner_p23_cleanup_and_budget.py:365` |
| I7 | `candidate` progress events are emitted in ascending `candidate_index` order, whatever the completion order | new test T3; `test_mutation_progress_budget_plan.py:58` |
| I8 | State records are written on completion, in any order. The files are per-candidate and order-free. | unchanged loader |
| I9 | A budget-stopped or fatal position emits no `candidate` event and no state record (same as today) | `:295` |
| I10 | Peak pack space is still `(1 + max(1, jobs)) * max_pack_bytes`, because at most `jobs` children are live | doc sentence (Work step 4) |

### Topology and bounds

- One thread (the caller's) owns `results`, `budget_exceeded_mask`, `resolved`, the reorder buffer, and every `write_progress` / state-record call. Workers only run `_run_one`. **No lock is added.** A worker never writes progress or state.
- The reorder buffer holds at most `jobs - 1` staged events beyond the cursor in steady state. No other bound is needed.

### Decision table

| State at handling time | results[p] | mask[p] | Event for p | State record | Effect on submission |
|---|---|---|---|---|---|
| future returned a run | run | False | staged, emitted in order | written now | none |
| raised `BUDGET_EXCEEDED/LANE_TIMEOUT` | None | True | none | none | stop; mask everything unsubmitted at loop exit |
| raised another `AssayError`, first seen | None | False | none | none | stop; re-raise after the loop |
| raised another `AssayError`, later | None | False | none | none | ignored (the first fatal wins) |
| never submitted, and submission stopped | None | True | none | none | — |
| raised a non-`AssayError` exception | propagates out of `future.result()` exactly as today (not caught) | | | | |

**"First fatal"** is defined as the lowest position among the fatals observed in the earliest done batch that contained any. It is deterministic when `jobs == 1` and when the executor resolves futures synchronously. The only thing it can affect is which of DIRTY_TREE, HEAD_CHANGED or GIT_FAILED the terminal reports. Document this in the docstring.

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| Queue replaces waves | `mutation.py::_execute_mutation_jobs` | T2 no-barrier | Event-gated fake runner | Re-insert the wave barrier: T2 fails with `gate_released is False` after the 60 s failsafe |
| ≤ jobs in flight | same | T1 | outstanding-count recording executor | Submit `jobs + 1` before waiting: T1's `max_outstanding == jobs + 1` |
| In-order events | same | T3 | out-of-order completion via events | Emit on completion: T3 sees indices `[1, 0, 2]` |
| First fatal | same | converted `:432` test | real Futures with `set_exception` | Take the last error: `caught.value is errors[1]` |
| Expiry / fatal semantics | same | existing I5/I6 tests | unchanged | — |
| Docs | docstring, DESIGN-GUIDE | `rg -n "waves" src/assay/mutation.py docs/DESIGN-GUIDE.md` returns no hit describing the executor | — | — |

### Degrees of freedom

Private helper names, how the reorder buffer is stored (dict or list), and whether steps 1–4 are split into local functions. You may **not**: change any payload key or value; change the order of state-record keys; add a lock; call `executor_factory` more than once; or read `os.cpu_count()`.

## Work

1. **Add T1, T2 and T3** (see Oracles) to `tests/test_mutation_executor_bound.py`. Reuse `_seed_repo` (`:73`) and `_run_with_recording_factory` (`:84`), or add a sibling helper there. For the content-keyed fake runner, reuse `tests/test_mutation_isolation.py::_make_decide_by_mutated_content` (`:110`) via `from test_mutation_isolation import …`. If that import creates a `sys.modules` clash, copy it into a private helper.
   - **T2 is the red-first test.** Run it on the unchanged wave code and record in your REPORT that it fails by failsafe expiry, not by hanging.
   - **T1 and T3 are guards.** The wave loop already satisfies them, and they must stay green through the rewrite. Record that they pass before and after.
2. **Replace the loop** at `mutation.py:2889-2994` following the Required flow. Keep the progress payload (`:2929-2955`) and the state-record payload (`:2956-2989`) byte-identical. Move them into two local closures (`_stage_progress(position, run, bucket)` and `_write_state(position, run, bucket)`) so that both are called from exactly one place. Extend the existing import at `mutation.py:112` to `from concurrent.futures import FIRST_COMPLETED, Executor, ThreadPoolExecutor, wait`.
3. **Rewrite the docstring paragraph** at `mutation.py:2072-2085`. Replace "WAVES … fully joined … before the next wave" with: a bounded work queue; at most *jobs* outstanding; in-order submission; expiry and fatal semantics; the reorder buffer; and the first-fatal definition. Cite A-467.
4. **Rewrite the DESIGN-GUIDE sentence** at `docs/DESIGN-GUIDE.md:1331-1333`: "…since P23 submits mutation work through a bounded queue with at most `jobs` outstanding units (A-467)…". Keep the formula unchanged.
5. **Convert `tests/test_b105_mutation_boundaries.py:432`.** `Executor.submit` returns `concurrent.futures.Future()` objects on which `set_exception(errors[position])` has already been called. `_function` is still never called. Keep `assert caught.value is errors[0]` unchanged.
6. Run the focused files serially, under nice/ionice:
   - `tests/test_mutation_executor_bound.py`
   - `tests/test_mutation_isolation.py`
   - `tests/test_mutation_classification.py`
   - `tests/test_runner_p23_cleanup_and_budget.py`
   - `tests/test_b105_mutation_boundaries.py`
   - `tests/test_mutation_progress_budget_plan.py`
   - `tests/test_mutation_judge.py`
   - `tests/test_mutation_state_crash_tails.py`
   - `tests/test_mutation_resume_sharding.py`
   - `tests/test_b106_reuse_and_witness.py`
7. **CHANGES.md** `## [Unreleased]` → `### Changed`: "Native mutation candidates run through a bounded work queue (at most `jobs` outstanding) instead of fully joined waves; progress `candidate` events stay in candidate order (B115, A-467)."
8. Write `nyxloom-trove/reports/assay-B115-REPORT.md` with the traceability table (actual test names and red→green counts), then commit.

## Oracles

- **T1: at most `jobs` outstanding.** Wrap a real `ThreadPoolExecutor(max_workers=jobs)`. `submit` increments `outstanding` and records `max_outstanding`; each returned future's `add_done_callback` decrements it. Run 5 candidates with `jobs=2`.
  - *Observable:* `max_outstanding == 2` and `submitted == 5`.
  - *Negative:* an implementation that submits everything up front gives `max_outstanding == 5`.
  - *Gate:* tester-unified.
- **T2: no wave barrier.** Use `jobs=2` and 3 candidates. The fake `process_runner` identifies candidate 0 by its mutated content (`_make_decide_by_mutated_content`). For candidate 0 it calls `gate.wait(timeout=60)`, records the boolean in `released`, then returns PASS. The recording executor's `submit` calls `gate.set()` when it sees submission #3. Candidate 1 returns immediately.
  - *Observable:* `released == [True]`. The third submission happened while candidate 0 was still running.
  - *Negative:* the current wave loop only submits #3 after candidate 0 finishes, so `released == [False]` after the 60 s failsafe. The failsafe only ends a broken run; it never decides a correct one.
  - *Gate:* tester-unified.
- **T3: in-order `candidate` events under out-of-order completion.** Use `jobs=3` and 3 candidates. Candidate 0 waits on `gate0`, which candidate 1's runner sets after it returns. Candidate 2 waits on `gate2`, which candidate 0's runner sets. Collect `write_progress` records.
  - *Observable:* the `candidate_index` values of the `event == "candidate"` records are `[0, 1, 2]`, and each index appears once. The state records are written for all three (files exist).
  - *Negative:* emitting on completion gives `[1, 0, 2]`.
  - *Gate:* tester-unified.
- **T4: first fatal with real Futures.** This is the converted `:432` test.
  - *Observable:* `caught.value is errors[0]`.
  - *Negative:* a "last fatal wins" variant raises `errors[1]`.
- **T5: existing semantics unchanged.** All tests listed in Context 5 pass with no edits apart from Work step 5.
  - *Negative:* pre-submitting beyond `jobs` makes `test_runner_p23_cleanup_and_budget.py:295` see a third unit.

### Anti-pattern list (verbatim from `nyxloom/reference/AUTHORING.md` §3b)

#### 3b. What an oracle must NOT contain — paste this into any handoff that asks for tests

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

- **README:** no change. The executor is internal, and `jobs` semantics are unchanged.
- **DESIGN-GUIDE:** Work step 4, plus any other sentence found by `rg -n "wave" docs/DESIGN-GUIDE.md` that describes the P23 executor.
- **CONSUMERS:** `rg -n "wave" docs/CONSUMERS.md`. If a sentence tells consumers that candidates run in waves, reword it to "at most `jobs` candidates run at once". Otherwise leave the file alone.
- **CHANGES:** Work step 7.

## Scope / forbid

- **Touch:**
  - `src/assay/mutation.py`: the imports, the docstring at `:2072-2085`, and the body of `_execute_mutation_jobs` at `:2889-2994` only;
  - `tests/test_mutation_executor_bound.py`;
  - `tests/test_b105_mutation_boundaries.py` (the `:432` test only);
  - `docs/DESIGN-GUIDE.md`, and `docs/CONSUMERS.md` only as described in Docs sync;
  - `CHANGES.md`;
  - `nyxloom-trove/reports/assay-B115-REPORT.md`.
- **Forbid:**
  - `assay.toml`: the B105 lane's `jobs` stays `1`.
  - `run-gate.toml`.
  - `verify.py` and `verdict.py`.
  - Any payload key.
  - `analysis.py` and `../run-gate-project/`. Their consumers stay correct because of I7; do not "fix" them.
  - `nyxloom-trove/decisions.md` (the controller records A-467).

  Needing to touch another file is a BLOCKED trigger.

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Focused tests (Work step 6), serially: `nice -n 19 ionice -c3 python -m pytest <files> -q -p no:cacheprovider`.
2. `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b115-gate.log 2>&1; echo "exit=$?"`.
3. In a **separate** step, read `grep -E "ASSAY_GATE_CONTAINER_EXIT|ASSAY_REGISTERED_GATE_COMPLETE" /tmp/b115-gate.log`. Both must show success (`=0` and `=1`). Never pipe-tail the gate.
4. This package does not change the B105 lanes, so the self-qualification preflight is not required.

## BLOCKED rule

If a named contract cannot be met as specified, or the scope requires a forbidden file, STOP. Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B115-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

Specific triggers:
- an existing test in Context 5 needs an edit other than `:432`;
- the reorder buffer cannot preserve I7 without a lock;
- a consumer outside `mutation.py` breaks.

## Report

`assay-B115-REPORT.md` must contain:
- the traceability table with the actual test names and red/green counts;
- the gate log path plus the two marker lines;
- the files touched;
- any residual risk.

Commit with a precise message and the trailer:

```
Co-Authored-By: Claude Sonnet <noreply@anthropic.com>
```

Do not merge. The controller merges after an independent adversarial review.
