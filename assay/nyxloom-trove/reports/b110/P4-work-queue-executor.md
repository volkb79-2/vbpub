# B110-P4 — Bounded work-queue executor for native mutation candidates

*Revised 2026-09-28 after round-1 review (see REVIEW-2026-09-28-round1.md).* Round-1 findings P4-1..P4-8 are applied, together with carver decisions C3 and C15.

| Field | Value |
|---|---|
| Backlog | **B115** (split from B110) |
| Branch | `assay-b110-p4-queue`, from the integration line (the plan §11.1 / C18 reconciliation branch `assay-b110-integration`) |
| Depends on | **P0** (B111), which adds per-candidate `cpu_seconds` / `peak_rss_bytes` / `phase_seconds` fields to the `candidate` progress event and the state record. This package carries whatever P0 added, unchanged.<br>**P6** (B117, carver decision C3). P6 changes `_run_attempt` so that an attempt timed out by the lane or campaign remainder, or ended while termination was requested, **raises** `BUDGET_EXCEEDED/LANE_TIMEOUT` instead of returning a result. The queue must treat that raise exactly like any other expiry (I5: masked, no state record). If P6 has not merged when you start, rebase onto it before the gate. P6 edits only `_run_attempt`'s result handling; you edit only the loop. |
| Contract class | **2b**: the public behavior is fixed; the private construction of the queue loop is left to you |
| Implementer | Opus (fresh session) |
| Decisions | **A-467 (plan D3)**: the lane may run a bounded concurrent work queue whose aggregate RSS stays inside the 2 GiB cap; concurrency is scheduling and never changes the inventory or a classification. It amends A-462's "serially". Binding earlier decisions, unchanged: A-082, A-113, A-122, A-160, A-193, A-195. Round-1 carver decisions: **C3** (lane-remainder timeouts are unclassified, owned by P6) and **C15** (dataclass-contract fixture). |
| Size | M: one function body in `mutation.py` (plus one small private reorder-buffer helper), one docstring, one DESIGN-GUIDE paragraph, one test rewrite, and seven new tests (T1, T2, T3a, T3b, T6, T7, T8) |

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
   - `tests/test_mutation_isolation.py`: `_SynchronousExecutor` at `:81-107` returns real, already-resolved `Future`s; `_make_decide_by_mutated_content` is at `:110-133`; the tests are at `:136`, `:172` and `:358`. **Warning:** that helper only knows the contents `a`/`b`/`c`/`d = False` and raises `AssertionError` on anything else (`:129`), and `test_mutation_executor_bound.py`'s `_TARGETS` yields **five** candidates (`a`..`e`, `:34-45`). The new tests therefore use their own 3-candidate fixture (Work step 1).
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
- `executor_factory(jobs)` is called **exactly once per `_execute_mutation_jobs` invocation**, with the caller's `jobs`.
  - The early return at `run_mutation:2316-2322` guards only a zero-candidate *inventory* and stays.
  - The executor's own `total` is `len(pending_jobs)` (`mutation.py:2483`). It is `0` after a full `--resume`, and today's loop still builds the executor then (`:2893`).
  - **Preserve that behaviour.** With `total == 0`, the factory is called once and nothing is submitted. Do not add a new early return, because `test_mutation_executor_bound.py` pins the construction count.
- Work is submitted as `pool.submit(_run_one, position)`, with exactly one positional argument. The test fakes depend on that shape.
- Completion is observed with `concurrent.futures.wait(in_flight, return_when=FIRST_COMPLETED)`. Every executor used in production or tests must therefore return real `concurrent.futures.Future` objects.

### Required flow

This replaces `:2893-2994`. The pseudocode fixes the semantics, not the syntax.

```
results = [None] * total
budget_exceeded_mask = [False] * total
fatals: dict[int, AssayError] = {}   # EVERY observed fatal, keyed by position
stop_submitting = False
next_to_submit = 0
buffer = _ReorderBuffer(total)       # private helper, see "Reorder buffer" below
in_flight: dict[Future, int] = {}

try:
    with executor_factory(jobs) as pool:
        while True:
            # 1. Fill: in position order, never more than `jobs` outstanding.
            while (not stop_submitting and next_to_submit < total
                   and len(in_flight) < jobs):
                in_flight[pool.submit(_run_one, next_to_submit)] = next_to_submit
                next_to_submit += 1
            if not in_flight:
                break
            # 2. Observe at least one completion.
            done, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
            # 3. Handle the done batch in ascending POSITION order.
            for future in sorted(done, key=in_flight.__getitem__):
                position = in_flight.pop(future)
                try:
                    results[position] = future.result()
                except AssayError as exc:
                    if exc.outcome is BUDGET_EXCEEDED and exc.reason_code is LANE_TIMEOUT:
                        budget_exceeded_mask[position] = True      # I5 (and C3 via P6)
                    else:
                        fatals[position] = exc                     # I6: keep ALL
                    stop_submitting = True
                run = results[position]
                if run is None:
                    buffer.resolve_without_event(position)
                else:
                    bucket = _classified_bucket(run)
                    write the STATE RECORD now (unchanged payload, :2956-2989)
                    buffer.stage(position, <the `candidate` PROGRESS payload, unchanged, :2929-2955>)
            # 4. Emit every staged event that is now contiguous from the cursor.
            for event in buffer.drain_contiguous():
                write_progress(event)
        # the loop only exits with in_flight empty: every submitted future was
        # consumed (drained), including after the first fatal
    # 5. After the pool context exits (every in-flight future has closed its snapshot):
    if stop_submitting:
        for leftover in range(next_to_submit, total):
            budget_exceeded_mask[leftover] = True
            buffer.resolve_without_event(leftover)
    for event in buffer.drain_contiguous():
        write_progress(event)
finally:
    # 6. Abnormal exit only (a non-AssayError exception propagating, KeyboardInterrupt,
    #    SystemExit from a signal handler): every state record already written must
    #    still have its `candidate` event. Emit the remaining staged events in
    #    ascending position order, skipping unresolved gaps. The indices stay
    #    monotonic, and a jump over a gap is acceptable only on this path.
    for event in buffer.drain_all_ascending():
        write_progress(event)
if fatals:
    raise fatals[min(fatals)]        # I6: the LOWEST-POSITION fatal among ALL observed
```

**Reorder buffer.** This is a private helper in `mutation.py` (the name is free). It is a plain class, not a `@dataclass`; if you make it a dataclass, C15 applies. It has three operations, all called only on the main thread:
- `stage(position, event)` stores the event and marks the position resolved.
- `resolve_without_event(position)` marks the position resolved without an event (masked, fatal, or never submitted).
- `drain_contiguous()` returns, in ascending order, the staged events for `cursor, cursor+1, …` while each position is resolved, and advances the cursor past resolved positions that have no event.
- `drain_all_ascending()` returns every remaining staged event in ascending order and empties the buffer.

It holds at most `total - cursor` staged events. Behind one long-running position, the other workers can complete any number of later candidates. So the bound is `total`, not `jobs - 1`.

**Why "lowest position among all observed" (P4-4).** Fatals are not limited to DIRTY_TREE, HEAD_CHANGED and GIT_FAILED. They include P22's `BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED`, `MUTATION_DISCOVERY_FAILED`, and kill-signal decode errors (`mutation.py:2798-2808`). So the lane's *outcome*, not just its reason, depends on which fatal is raised. Draining every in-flight future before raising, and then picking the lowest position among **all** observed fatals, makes the choice independent of completion order and batching. It depends only on which positions were in flight when submission stopped.

**Invariants that the flow must keep.** They are the same as today; only the barrier goes.

| # | Invariant | Where it is proven today |
|---|---|---|
| I1 | The factory is built once, with exactly `jobs` (A-082/A-122) | `test_mutation_executor_bound.py:122,131` |
| I2 | Each position is submitted exactly once, in position order | `:141` (`submitted == total`) |
| I3 | At most `jobs` futures are outstanding at any moment | new test T1 |
| I4 | Results are position-aligned; buckets are identity-ordered with no re-sort (A-113) | `:189`; `test_mutation_isolation.py:172,358` |
| I5 | Only `BUDGET_EXCEEDED/LANE_TIMEOUT` counts as expiry. The expired position and every **unsubmitted** position are masked `budget_exceeded`. In-flight positions finish and remain evidence (A-160/A-193). After P6 (C3), an in-flight position whose attempt the lane remainder cut off raises LANE_TIMEOUT itself, so it is masked with no record. | `test_runner_p23_cleanup_and_budget.py:295,471` (all `jobs=1`); new **T6** at `jobs=3` |
| I6 | Any other `AssayError` is fatal: submission stops, every in-flight future is drained, their progress and state are still written, and the **lowest-position** fatal among all observed is re-raised unchanged (A-195) | `test_b105_mutation_boundaries.py:390,432`; `test_runner_p23_cleanup_and_budget.py:365`; new T4 (fatals in separate batches) and T7 |
| I7 | `candidate` progress events are emitted in ascending `candidate_index` order, whatever the completion order. The index counts over the **pending** list (`total = len(pending_jobs)`), so it stays monotonic across a partial resume too. | new tests T3a/T3b and T8; `test_mutation_progress_budget_plan.py:58` |
| I8 | State records are written on completion, in any order. The files are per-candidate and order-free. | unchanged loader |
| I9 | A budget-stopped or fatal position emits no `candidate` event and no state record (same as today) | `:295` |
| I10 | Peak pack space is still `(1 + max(1, jobs)) * max_pack_bytes`, because at most `jobs` children are live | doc sentence (Work step 4) |

### Topology and bounds

- One thread (the caller's) owns `results`, `budget_exceeded_mask`, `fatals`, the reorder buffer, every `candidate` progress event, and every state-record write. **No lock is added.**
  - Workers run `_run_one`. Workers and the B064 heartbeat thread may still write *other* progress records through `ProgressStream`, which already serialises its `write` under its own lock (`mutation.py:878-887`). That is unchanged.
  - Only `candidate` events and state records are main-thread-only.
- The reorder buffer holds at most `total - cursor` staged events (bounded by `total`; see "Reorder buffer"). No other bound is needed.

### Decision table

| State at handling time | results[p] | mask[p] | Event for p | State record | Effect on submission |
|---|---|---|---|---|---|
| future returned a run | run | False | staged, emitted in order | written now | none |
| raised `BUDGET_EXCEEDED/LANE_TIMEOUT` (including P6's C3 raise for a lane-remainder cut-off) | None | True | none | none | stop; mask everything unsubmitted at loop exit |
| raised another `AssayError` | None | False | none | none | stop; recorded in `fatals`; draining continues |
| never submitted, and submission stopped | None | True | none | none | — |
| raised a non-`AssayError` exception | propagates out of `future.result()` exactly as today (not caught). The `finally` block flushes the staged events. | | | | |

**The raised fatal** is the lowest-position fatal among **all** fatals observed after every in-flight future has been drained. Put this in the docstring, and say that the lane's outcome (not just its reason) can depend on it; see "Why lowest position among all observed" above. When `jobs == 1` this is identical to today's behaviour.

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| Queue replaces waves | `mutation.py::_execute_mutation_jobs` | T2 no-barrier | Event-gated fake runner | Re-insert the wave barrier: T2 fails with `gate_released is False` after the 60 s failsafe |
| ≤ jobs in flight, in-order submission | same | T1 | outstanding-count executor with the decrement **inside the wrapped fn** | Submit `jobs + 1` before waiting → `max_outstanding == jobs + 1`; submit in reverse order → `positions != list(range(5))` |
| In-order events (pure) | reorder buffer | T3a | direct calls, no threads | `drain_contiguous` emits out of order or skips a staged event → assertion |
| In-order events (integration) | `_execute_mutation_jobs` | T3b | release gated on the main-thread state write of position 1 | Emit on completion → `[1, 0, 2]` |
| First fatal across batches | same | T4 (converted `:432`) + T4b | real Futures with `set_exception`; batches split | "First observed wins" → T4b raises position 2's error |
| Expiry at `jobs ≥ 2` | same | T6 | Event-held positions 0 and 2, position 1 raises LANE_TIMEOUT | Mask all in-flight → no records for 0/2; keep submitting → `submitted > 3` |
| Fatal at `jobs ≥ 2` | same | T7 | Event-held positions 0 and 2, position 1 raises GIT_FAILED | Stop draining → missing records for 0/2 |
| Resume monotonicity | same | T8 | 2 of 5 records pre-seeded, reversed completion | Index over the full list, or emitted on completion → non-monotonic |
| Expiry / fatal semantics at `jobs = 1` | same | existing I5/I6 tests | unchanged | — |
| Abnormal-exit flush | same | T3a (`drain_all_ascending`) | direct call | Missing `finally` → staged events are lost (reviewed by inspection; T3a pins the helper) |
| Docs | docstring, DESIGN-GUIDE | `rg -n "waves" src/assay/mutation.py docs/DESIGN-GUIDE.md` returns no hit describing the executor | — | — |

### Degrees of freedom

Private helper names, how the reorder buffer is stored internally (dict or list), and whether steps 1–4 are split into local functions. You may **not**:
- change any payload key or value;
- change the order of state-record keys;
- add a lock;
- call `executor_factory` more than once per invocation, or skip it when `total == 0`;
- read `os.cpu_count()`;
- raise any fatal other than the lowest-position one.

## Work

1. **Add a 3-candidate fixture and the new tests** to `tests/test_mutation_executor_bound.py`.
   - The fixture: a private `_TEXT3` with three independent sites (`a = True`, `b = True`, `c = True`), `_TARGETS3`, and a private content-keyed runner factory `_decide3(repo_path, behaviours)`. It maps mutated content `"a = False"`/`"b = False"`/`"c = False"` to a callable, keeps the baseline as PASS, and raises `AssertionError` on anything else.
   - **Do not** import `_make_decide_by_mutated_content`: it knows only `a`–`d`, and its `c`/`d` branches raise.
   - Reuse `_seed_repo`'s pattern (`:73`) with `_TEXT3`, and `_run_with_recording_factory`'s shape (`:84`) through a sibling helper that takes `targets`, `jobs`, `process_runner` and `state_root`.
   - **T2 is red-first.** Run it on the unchanged wave code and record in your REPORT that it fails by failsafe expiry, not by hanging. T6 and T7 must also be run on the wave code; record their result (at least one is expected red).
   - **T1, T3a/T3b, T4b and T8 are guards or new-helper tests.** Record their status before and after the rewrite.
2. **Replace the loop** at `mutation.py:2889-2994` following the Required flow.
   - Keep the progress payload (`:2929-2955`) and the state-record payload (`:2956-2989`) byte-identical. Move them into two local closures (`_stage_progress(position, run, bucket)` and `_write_state(position, run, bucket)`) so that both are called from exactly one place.
   - Add the private reorder-buffer helper.
   - Extend the existing import at `mutation.py:112` to `from concurrent.futures import FIRST_COMPLETED, Executor, ThreadPoolExecutor, wait`.
   - If P6 has merged, the LANE_TIMEOUT raise from `_run_attempt` (C3) already flows through the `except AssayError` expiry branch. Do not special-case it.
3. **Rewrite the docstring paragraph** at `mutation.py:2072-2085`. Replace "WAVES … fully joined … before the next wave" with:
   - a bounded work queue, with at most *jobs* outstanding;
   - in-order submission;
   - the expiry and fatal semantics, including drain-then-lowest-position;
   - the reorder buffer and the abnormal-exit flush.

   Cite A-467.
4. **Rewrite the DESIGN-GUIDE sentence** at `docs/DESIGN-GUIDE.md:1331-1333`: "…since P23 submits mutation work through a bounded queue with at most `jobs` outstanding units (A-467)…". Keep the formula unchanged.
5. **Convert `tests/test_b105_mutation_boundaries.py:432`.** `Executor.submit` returns `concurrent.futures.Future()` objects on which `set_exception(errors[position])` has already been called. `_function` is still never called. Keep `assert caught.value is errors[0]` unchanged.
   - Add **T4b** beside it, with `job_list` of three `_job()`s and `jobs=3`. The fake executor returns real `concurrent.futures.Future()` objects, already resolved with `set_exception`:
     - position 0: `AssayError(outcome=BUDGET_EXCEEDED, reason_code=LANE_TIMEOUT)` (an expiry, not a fatal);
     - position 1: `GIT_FAILED`;
     - position 2: `DIRTY_TREE` (`Outcome.NO_MEASUREMENT`).

     Force **separate batches** by monkeypatching the name `mutation.wait` (imported by Work step 2) with a scripted function. It returns `({f2}, {f0, f1})`, then `({f1}, {f0})`, then `({f0}, set())`. That is deterministic, with no threads.
     - *Observable:* `caught.value is errors[1]`.
     - *Negative:* "first observed wins" raises the DIRTY_TREE error; treating the expiry as fatal raises position 0's error.
5b. **C15.** If you add or change any `@dataclass` in `src/assay` (the reorder buffer is specified as a plain class, so normally none), regenerate `tests/fixtures/dataclass-contract.json` with the command P1 (B112) documents in DESIGN-GUIDE, and commit it in the same change.
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

- **T1: at most `jobs` outstanding, submitted in order.** Wrap a real `ThreadPoolExecutor(max_workers=jobs)`.
  - `submit(fn, position)` appends `position` to `positions`, increments `outstanding` under a `threading.Lock`, updates `max_outstanding`, and submits a **wrapper** that runs `fn(position)` inside `try: … finally: decrement outstanding under the lock`.
  - The decrement therefore happens *before* the future completes. CPython's `Future.set_result` wakes `wait()` before `add_done_callback` callbacks run, so a callback-based decrement can record `jobs + 1` under a correct implementation (P4-1).
  - Run the 5-candidate `_TARGETS` with `jobs=2`.
  - *Observable:* `max_outstanding <= 2` and `positions == [0, 1, 2, 3, 4]`.
  - *Negative:* submitting everything up front gives `max_outstanding == 5`; reverse-order submission changes `positions`.
  - *Gate:* tester-unified.
- **T2: no wave barrier.** Use `jobs=2` and the 3-candidate fixture.
  - The `_decide3` runner, for candidate `a`, calls `gate.wait(timeout=60)`, records the boolean in `released`, then returns PASS. The recording executor's `submit` calls `gate.set()` when it sees submission #3. Candidate `b` returns immediately.
  - *Observable:* `released == [True]`. The third submission happened while candidate 0 was still running.
  - *Negative:* the current wave loop only submits #3 after candidate 0 finishes, so `released == [False]` after the 60 s failsafe. The failsafe only ends a broken run; it never decides a correct one.
  - *Gate:* tester-unified.
- **T3a: reorder buffer, pure (the primary ordering oracle).** Call the helper directly, with no threads:
  - `stage(1, e1)` → `drain_contiguous() == []`;
  - `stage(0, e0)` → `== [e0, e1]`;
  - `resolve_without_event(2)`; `stage(3, e3)` → `== [e3]`;
  - separately, `stage(5, e5)`, `stage(7, e7)` → `drain_all_ascending() == [e5, e7]` and the buffer is empty.
  - *Negative:* emitting on stage, dropping events past a gap, or a non-ascending `drain_all_ascending`.
  - *Gate:* tester-unified.
- **T3b: in-order `candidate` events, integration.** Use `jobs=3`, the 3-candidate fixture, and a `state_root`.
  - Candidate `a` (position 0) blocks on `gate0.wait(timeout=60)`.
  - Monkeypatch `mutation._write_mutation_state_record` with a wrapper that calls the real function and then, **when the record is for position 1's candidate id**, calls `gate0.set()`.
  - The state write happens on the main thread when position 1 is *handled*. So position 0 is still unresolved when position 1's event is staged, whatever the batching. This fixes P4-2: gating on the runner returning left the dirt check and teardown racing.
  - Collect the `write_progress` records.
  - *Observable:* the `candidate_index` values of `event == "candidate"` records are `[0, 1, 2]`, each once, and state files exist for all three.
  - *Negative:* emitting on completion gives `[1, 0, 2]`.
  - *Gate:* tester-unified.
- **T4: first fatal with real Futures.** This is the converted `:432` test.
  - *Observable:* `caught.value is errors[0]`.
  - *Negative:* a "last fatal wins" variant raises `errors[1]`.
  - **T4b** (fatals in separate batches) is specified in Work step 5.
- **T5: existing semantics unchanged.** All tests listed in Context 5 pass with no edits apart from Work step 5.
  - *Negative:* pre-submitting beyond `jobs` makes `test_runner_p23_cleanup_and_budget.py:295` see a third unit.
- **T6: expiry at `jobs = 3`.** Use the 3-candidate fixture with a `state_root`.
  - Positions 0 and 2 block on `hold.wait(timeout=60)`.
  - Position 1's runner raises `subprocess.TimeoutExpired`. Alternatively, inject through the `execute_plan` seam an `AssayError(BUDGET_EXCEEDED, LANE_TIMEOUT)` for position 1's cwd, then call `hold.set()`.
  - *Observable:*
    - state records exist for positions 0 and 2, and not for 1;
    - `candidate` events are `[0, 2]`;
    - `submitted == 3`;
    - `mutation.budget_exceeded` contains exactly position 1's identity.
  - *Negative:* masking every in-flight position loses 0 and 2; pre-submitting beyond `jobs` shows up in `submitted`.
  - *Gate:* tester-unified.
- **T7: fatal at `jobs = 3`.** The same setup, but position 1 raises `AssayError(ERROR, GIT_FAILED)`.
  - *Observable:* the raised error is position 1's; state records exist for 0 and 2; `submitted == 3`.
  - *Negative:* not draining in-flight futures loses the records for 0 and 2.
  - *Gate:* tester-unified.
- **T8: resume keeps indices monotonic.** Use the 5-candidate `_TARGETS`, a `state_root` pre-seeded (via a first `jobs=1` run over a 2-candidate selection, or directly written valid records) so that 2 of 5 candidates resume. Then run with `jobs=2`, completion order reversed through Event gates keyed on content.
  - *Observable:* the emitted `candidate_index` values are strictly increasing, cover `0..2` (the 3 pending candidates), and `candidate_total == 3`.
  - *Negative:* indexing over the full job list, or emitting on completion.
  - *Gate:* tester-unified.

All `hold`/`gate` waits use `timeout=60` as a **failsafe only**; a test that reaches it fails with an explicit message. No assertion depends on elapsed time.

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
  - `src/assay/mutation.py`: the imports, the docstring at `:2072-2085`, the body of `_execute_mutation_jobs` at `:2889-2994`, and one new private reorder-buffer helper beside it;
  - `tests/test_mutation_executor_bound.py`;
  - `tests/test_b105_mutation_boundaries.py` (the `:432` test and the new T4b beside it);
  - `tests/fixtures/dataclass-contract.json`, only under C15 (Work step 5b);
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
4. This package changes `src/assay`, so per plan §10.3 (revised) also run `python ./run-gate.py self-qualification-preflight`, in a separate invocation after tester-unified, with no other gate running. Read its exit in a separate step. The new helper's lines must be covered: 100% line+branch, and no new exclusions.

## BLOCKED rule

If a named contract cannot be met as specified, or the scope requires a forbidden file, STOP. Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B115-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

Specific triggers:
- an existing test in Context 5 needs an edit other than `:432`;
- the reorder buffer cannot preserve I7 without a lock;
- a consumer outside `mutation.py` breaks;
- P6's C3 change is not yet merged and your rebase would have to re-implement it.

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
