# B110 implementation map (A–F): wave executor, liveness windows, planner, deadline, pilot subset, resource evidence

Checkout: `/workspaces/vbpub/.worktrees/.assay-b105-ciu-root-20260926-30eec294-copy/assay`, HEAD `db85f747`. All paths below are relative to `assay/` unless marked. I only read files: no tests, gates or docker were run.

The governing decision is **A-464** (`nyxloom-trove/decisions.md:943`): a measured plan must project completion within 6 h, with an 8 h hard stop and no rise in the 2 GiB RAM cap. The deadline is persisted and bound to source, lane, tool and plan. Shards are scheduling, not proof. Timeouts and host pressure never change a candidate's classification. **A-462** (`decisions.md:941`) says the B105 lane runs "every native Python operator **serially** and unsharded". That is why `assay.toml:173` declares `jobs = 1`.

---

## A. Executor: replace fully-joined waves with a bounded work queue

### Current code

The docstring that describes the waves is at `src/assay/mutation.py:2072-2085`. Here is the loop itself (`mutation.py:2889-2902`, `2914-2929`, `2990-2994`), verbatim, with the long comments and the progress/state payload bodies elided:

```python
    results: list[_MutantRun | None] = [None] * total
    budget_exceeded_mask = [False] * total
    fatal: AssayError | None = None

    with executor_factory(jobs) as pool:
        index = 0
        while index < total and fatal is None:
            wave = list(range(index, min(index + jobs, total)))
            futures = {pool.submit(_run_one, position): position for position in wave}
            wave_stopped = False
            for future, position in futures.items():
                try:
                    results[position] = future.result()
                except AssayError as exc:
                    # ... (comment 2903-2913 elided)
                    if (
                        exc.outcome is Outcome.BUDGET_EXCEEDED
                        and exc.reason_code is ReasonCode.LANE_TIMEOUT
                    ):
                        budget_exceeded_mask[position] = True
                        wave_stopped = True
                    elif fatal is None:
                        fatal = exc
                run = results[position]
                if run is None:
                    continue
                # ...
                outcome_bucket = _classified_bucket(run)
                if write_progress is not None:
                    write_progress({ "event": "candidate", ... })   # 2930-2955
                if state_root is not None:
                    ...
                    _write_mutation_state_record(Path(state_root), {...})  # 2956-2989
            index = wave[-1] + 1
            if fatal is not None or wave_stopped:
                for leftover in range(index, total):
                    budget_exceeded_mask[leftover] = True
                break
```

After the loop: `raise fatal` (`2999-3000`), then buckets are built position-aligned from `job_list` (`3009-3035`).

### Invariants the queue must keep, and where each is enforced today

1. **Exact `jobs`, one construction.** `executor_factory(jobs)` is called once, with the caller's `jobs` (`2893`). The type is `ExecutorFactory = Callable[[int], Executor]` (`759`), and the default is `ThreadPoolExecutor(max_workers=jobs)` (`764-765`). `jobs` is validated before this point (`2099-2102`).
2. **Every candidate is submitted exactly once** through `pool.submit(_run_one, position)`, which takes one positional argument. Some test fakes accept exactly `(fn, position)`.
3. **Results are position-aligned.** They live in `results[position]`. Buckets are built by iterating `job_list` in order (`3010-3029`), so buckets stay identity-ordered without a sort (A-113 below).
4. **Expiry.** Only `BUDGET_EXCEEDED`/`LANE_TIMEOUT` counts as expiry. It is raised by `deadline.remaining()` inside `_run_attempt`: materialize at `2656`, command at `2708`, or by `execute_plan`. That candidate is masked, no further submissions happen, and every unsubmitted position is masked `budget_exceeded` (`2990-2994`).
   - Positions already in flight (wave-mates) finish and remain evidence.
   - An expiry seen only at the post-run dirt check is absorbed. It never reclassifies a completed candidate (`2761-2778`).
5. **Fatal.** Any other `AssayError` (dirt/HEAD, A-195; P22 `SNAPSHOT_LIMIT_EXCEEDED`; a Git failure) stops new submissions. In-flight futures are still consumed, and their progress and state are still written. The *first* fatal wins (`elif fatal is None`). After the loop it is re-raised unchanged (`2999-3000`).
6. **Progress/state writes** happen on the main thread, per completed candidate, in position order. That is because futures are awaited in dict-insertion order (`2899`). The `candidate` event carries `candidate_index=position` and `candidate_total=total` (the pending count). Budget-stopped positions emit no `candidate` event.
7. **Peak pack space.** At most `jobs` live children means `(1 + max(1, jobs)) * max_pack_bytes`. The text at `docs/DESIGN-GUIDE.md:1324-1334` justifies this by "submits mutation work in waves of size `jobs` and awaits each wave". A queue with at most `jobs` in flight keeps the bound, but that sentence must be rewritten.

### Decisions, one line each

- **A-082** (`decisions.md:280`): prove the `jobs` bound at the executor boundary, without timing. The factory must receive `max_workers=jobs` and all work must go through it.
- **A-122** (`decisions.md:375`): `max_workers` comes from caller `jobs`, never `os.cpu_count()`. Each mutant runs with a timeout and produces distinct kill/crash/hang/budget buckets.
- **A-160** (`decisions.md:453`): `lane.budget` bounds the whole lane. "The next unit is not started when none remains." `jobs` bounds concurrency, not work.
- **A-193** (`decisions.md:516`): one shared injected `LaneDeadline`. A fresh positive remainder is sampled immediately before every P22/process boundary, and monotonic time is kept separate from the UTC evidence clock.
- **A-195** (`decisions.md:518`): `DIRTY_TREE`/`HEAD_CHANGED` stop later work. Every child/future closes before the prepared seed. An earlier P22 `AssayError` is never replaced by a cleanup `RuntimeError`.
- **A-113** (`decisions.md:361`, not in your list but binding): "deterministic result ordering independent of completion order."

### Tests that pin this behavior

**Must stay green, unchanged,** if the queue keeps invariants 1-5:

| Test | What it pins |
|---|---|
| `tests/test_mutation_executor_bound.py` (whole file) | factory gets exactly `jobs` (`seen[0].jobs == 2`/`4`); `len(seen) == 1`; `submitted == mutation.total == 5` (no re-submission); never constructed for zero mutants (via the `run_mutation` early return at `mutation.py:2316-2322`); `jobs=1` and `jobs=3` give identical `to_dict()`; jobs validation. `_RecordingExecutor.submit` returns real `ThreadPoolExecutor` futures, so `concurrent.futures.wait` works. |
| `tests/test_mutation_isolation.py:136` | `_SynchronousExecutor` (`:81-107`: real, already-resolved `Future`s), `jobs=2`, content-keyed attribution |
| `tests/test_mutation_isolation.py:172, :358` | jobs 1 vs 3 identical; identity-ordered buckets across files with `jobs=3` |
| `tests/test_mutation_classification.py:92,187,365` | `_SynchronousExecutor`, `jobs=1` |
| `tests/test_runner_p23_cleanup_and_budget.py:295` | `jobs=1`: `units == ["baseline", "mutant"]` ("no third or fourth process"); 1 killed + 2 `budget_exceeded`, sorted. This is the "every later unsubmitted identity" pin. The queue must never pre-submit beyond `jobs` in flight. |
| `tests/test_runner_p23_cleanup_and_budget.py:365` | P22 policy refusal propagates with its own pair; `prepared.calls == 1` |
| `tests/test_runner_p23_cleanup_and_budget.py:471` | integrity-check expiry is absorbed; 1 killed + 2 `budget_exceeded` |
| `tests/test_b105_mutation_boundaries.py:390` | direct `_execute_mutation_jobs`, `jobs=1`, a non-timeout Git failure propagates (`caught.value is failure`) |
| `tests/test_mutation_progress_budget_plan.py:141` | `jobs=1`: candidate indices `[-1, 0, 1]` in order |
| `tests/test_mutation_judge.py:409` (`jobs=2`), `tests/test_mutation_state_crash_tails.py` (`jobs=1`), `tests/test_mutation_resume_sharding.py` (`jobs=1`) | order-insensitive or serial |

**Will break, depending on the design:**

- `tests/test_b105_mutation_boundaries.py:432` (`test_mutation_worker_keeps_the_first_fatal_error_when_another_worker_fails`). Its fake `Executor.submit(self, _function, position)` **never calls the function**. It returns a duck-typed `Future` that has only `.result()`.
  - It breaks any queue built on `concurrent.futures.wait`/`as_completed`/`add_done_callback` (no `_condition`/`_waiters`).
  - It also breaks one built on a completion queue fed by the worker (it would hang).
  - Change the fake to real `concurrent.futures.Future` objects with `set_exception(...)`. Keep `caught.value is errors[0]` only if the implementation walks each `done` batch **in position order**: `wait()` returns an unordered set, and both futures are already done.

**New tests to add** (boundary-level, no timing):

- **At most `jobs` in flight.** Use a recording executor that counts outstanding futures.
- **No wave barrier.** Use a `threading.Event`-gated fake `process_runner`, keyed on mutated content as in `_make_decide_by_mutated_content`. Candidate 0 blocks until the recording executor has seen submit #`jobs+1`. Under waves this deadlocks. Guard it with a join timeout that fails the test rather than a timing assertion.
- **Progress still in position order** under out-of-order completion.

### Hazards

1. **Progress ordering has an external consumer.** run-gate's RG-36 watcher treats the newest event's `candidate_index` as the completed count: `rate = index / elapsed_s * 60`, `ETA = (total - index) / rate` (`../run-gate-project/run-gate.py:6462-6471, 6474-6492, 6565`). `assay analyze report` also keeps the *last-seen* `candidate_index` (`src/assay/analysis.py:715-716, 747-752`).
   - Completion-order emission makes `candidate_index` non-monotonic, which breaks both.
   - Recommendation: write the state record immediately on completion (it is per-file and order-free, and better for crash-resume). Hold `candidate` progress events in a reorder buffer and emit position `i` only once every `j < i` is resolved. A budget-stopped or fatal position counts as resolved-without-event.
   - Visible latency is then no worse than waves, where a slow position 0 already delays its wave-mates' events.
2. **Semantics are equivalent to waves.** With in-order submission, every position below an expired `p` was already submitted. "Unsubmitted" means everything from the next-to-submit index onward. In-flight candidates finish and count, exactly as wave-mates do today.
3. **"First" fatal** becomes first-observed, with ties broken by position. It is deterministic only for synchronous or `jobs=1` runs. Only the reported reason code (DIRTY_TREE/HEAD_CHANGED/GIT_FAILED) depends on it.
4. **B105 gains nothing at `jobs=1`.** Going above 1 contradicts A-462's "serially" wording, and extra workers share the 2 GiB cap (A-464).
5. **Documentation to update:** the docstring at `mutation.py:2072-2085`, `docs/DESIGN-GUIDE.md:1332`, and CHANGES.md. Consider a new A-number that records the wave-to-queue change, citing A-113 and A-194.

---

## B. Liveness windows

### Constants and where they are read (all module-level, none configurable per lane)

- `src/assay/liveness.py:523-526`: `_HUNG_CPU_WINDOW_S = 30.0`, `_HUNG_CPU_GROWTH_FLOOR_S = 1.0`, `_HUNG_SESSION_FINISH_GRACE_S = 30.0`, `_LIVENESS_POLL_INTERVAL_S = 1.0`.
- `liveness.py:641` `LIVENESS_IDLE_FLOOR_S = 15.0`; `:646` `LIVENESS_FALLBACK_FLOOR_S = 60.0`.
- The window is read at `_monitor` time: `cpu_samples = _CpuSampleHistory(_HUNG_CPU_WINDOW_S)` (`liveness.py:1458`). The growth test is at `:1494-1498`, the hung predicate at `:1516-1520`, and the elapsed-budget kill at `:1530-1537`.
- The floors are read inside `compute_liveness_calibration` (`liveness.py:950-1013`): `max(3*worst_gap, 15)` and `max(3*leading_gap, 15)`, with fallback `max(60, baseline_s/4)`.
- `LivenessRunner.__init__` (`liveness.py:1299-1333`) already accepts `poll_interval_s` and `cpu_reader`. It has **no** window, growth-floor or grace parameter.

### How config reaches the runner today

- `judge.mutation.liveness` is a tri-state only (`true`/`false`/`"auto"`). The field is at `src/assay/config.py:556-567`, the optional-key list at `:438-450`, parsing at `:3144-3185`, and construction at `:3218-3229`. Ingested lanes refuse it at `:3277-3290`.
- `runner.py:3647-3661` passes `liveness_policy=lane.judge.mutation.liveness` to `inject_liveness_plugin`.
- `runner.py:3795-3801` computes the calibration.
- `runner.py:4418-4428` constructs `LivenessRunner(events_dir=…, expect_next_event_within_s=…, pre_first_event_within_s=…)`. Nothing else is passed.

**Answer: no.** The CPU window is not configurable per lane. Only unit tests monkeypatch it: `tests/test_liveness_runner_monitor.py:209` does `monkeypatch.setattr(liveness, "_HUNG_CPU_WINDOW_S", 2.0)`.

### Why a window key alone is not enough

`hung` requires `idle_for >= bound AND not cpu_growing`, and `bound` is at least the 15 s idle floor.
- **Hang test** (`tests/test_cli_run.py:463`, 30.8 s): with a 3 s window it would still take about 15 s. It needs an **idle-floor** override as well.
- **Busy-loop test** (`tests/test_cli_run.py:586`, 35.9 s): it waits out `budget_per_candidate = "35s"`, which is set above the 30 s window on purpose (docstring). With a 3 s window, a `"6s"` budget still exercises "still growing after the window was checked".
- Both tests call `main()` in-process (`test_cli_run.py:127-130`). So a zero-product-change option exists: monkeypatch `liveness._HUNG_CPU_WINDOW_S` and `liveness.LIVENESS_IDLE_FLOOR_S`, both read at call time. The precedent is `test_liveness_runner_monitor.py:209`.

### Proposal if the key is lane-configurable

Two flat duration strings, named like `budget_per_candidate` and `budget_per_attempt`:
- `judge.mutation.liveness_cpu_window = "30s"`
- `judge.mutation.liveness_idle_floor = "15s"`

Do not turn `liveness` into a table: that is a breaking change to the lane grammar.

**Loader** (`config.py`):
- Add both names to `_MUTATION_OPTIONAL_FIELDS` (`:438-450`) and the ingested `orchestration_only` set (`:3279`).
- Parse with `parse_duration` after the liveness block (`:3185`): positive and finite; window ≥ 2 × the 1 s poll interval.
- Refuse either key when `liveness == false`. B026's lesson is that dead config must not be accepted silently.
- Add `MutationConfig` fields that stay `None` when omitted, plus `as_declared` echo (`:630-645`). `assay lanes --json` echoes that dict (`cli.py:2009`); `tests/test_cli_lanes_json.py` pins shapes.

**Threading:**
- `compute_liveness_calibration(..., idle_floor_s=LIVENESS_IDLE_FLOOR_S)` uses it at `liveness.py:1005-1010`; call it from `runner.py:3796`.
- `LivenessRunner(..., cpu_window_s=_HUNG_CPU_WINDOW_S)` stores it; `_monitor:1458` uses `self._cpu_window_s`. Call it from `runner.py:4420`.
- `run_mutation` gains `liveness_cpu_window_s` (beside the params at `mutation.py:1947-1995`) for the `plan` event.

**Disclosure:**
- The `plan` progress event (`mutation.py:2223-2257`) can add `cpu_window_s`/`idle_floor_s` freely: only event names are a closed vocabulary (`mutation.py:846-870`).
- **`judgment.r2.liveness` is closed.** The schema has `additionalProperties: false`, requires exactly `{active, reason, plugin}`, and the constant is `"const": 13` (`src/assay/schemas/verdict.schema.json` `$defs/judgment_r2/properties/liveness`). `verdict.py:2934-2970` enforces `set(self.liveness) != {"active", "reason", "plugin"}`. The builder is at `runner.py:5036-5040`; verify reconstructs it.
- Adding `cpu_window_s` therefore means a verdict-schema bump (v14; A-170: one active schema). Fold it into B110's planned v14 hard cut.
- Exact-dict pins to update: `tests/test_cli_run.py:443, 577`; `tests/test_standalone.py:775`; `tests/test_verdict_judgment.py:549`.

**Docs:** `docs/CONSUMERS.md:2440-2490` ("less than 1.0 s over the trailing 30 s window", "never … sooner than 30 s"), `docs/DESIGN-GUIDE.md:470-500`, `README.md:364`.

**Flag:** `_HUNG_CPU_GROWTH_FLOOR_S = 1.0` is absolute. Over a 3 s window it demands 33 % of a core, so a contended idle-but-alive candidate could be classed `hung`. That conflicts with the contention-agnostic rule and A-464. Either scale the floor with the window (`window/30`), or document that short windows are test-only.

### Other real-time-bound slow tests

| Test | What it waits on | Parameterizable today? |
|---|---|---|
| `tests/test_progress_phase_stream.py:456` (7.05 s) | CLI floor `runner.PROGRESS_HEARTBEAT_FLOOR_SECONDS = 5.0` (`runner.py:936`, refused below it at `cli.py:1083`), plus the lane argv `sleep 7` (test line 465). The same constant bounds `thread.join` (`runner.py:1002`). | `_command_heartbeat(interval_seconds=…)` is (unit tests use 0.02). The CLI path is floored; the test could monkeypatch the floor (read through `runner.`) and shorten `sleep`. `:500` separately pins 60.0/5.0. |
| `tests/test_lane_timeout_writes_a_verdict.py` (16 tests, 6.9 s) | Lane `budget = "1s"` against `sleep 30` (`BUDGET`/`SLOW_COMMAND` at `:50-51`). About 5-6 real 1 s waits (`:120, :152, :303`×2, `:326`); the others use `"0.001s"` or a monkeypatched `run_lane`. | Yes (a lane value, no production constant). Lowering it risks the deadline expiring *before* the command under load, which would exercise a different catch than the one the `:120` docstring targets. |
| `tests/test_environment_preflight.py:246` (3.0 s) | `runner.PROBE_BUDGET_SECONDS` (`runner.py:360`, 30.0), already monkeypatched to 3.0 (`:271`); rendered with `:g` (`runner.py:597-598`). | Yes; patch to 1.0 and update the "3s" string assertions (`:299-300`). |
| `tests/test_environment_preflight.py:128` (2.0 s) | Lane `budget = "2s"` (`:160`); asserts `isclose(rendered, 2.0, abs_tol=1.0)` (`:184`). | Yes (lane value); change the tolerance along with it. |

---

## C. Evidence-based estimate for `assay plan`

### Current fallback (`src/assay/cli.py:1786-1803`, verbatim)

```python
        per_candidate = lane.judge.mutation.budget_per_candidate
        # (B091/D-23) `assay plan` never executes anything, so it cannot
        # measure the baseline "auto" would derive from -- an omitted key,
        # an explicit "auto", and the explicit "none" opt-out all fall back
        # to the same 60s-per-candidate estimate an undeclared bound always
        # used, which is honestly an upper-bound GUESS either way (this
        # function's own docstring already says so). Only an explicit
        # duration is a real number to multiply by.
        if per_candidate in (
            None,
            MUTATION_BUDGET_PER_CANDIDATE_AUTO,
            MUTATION_BUDGET_PER_CANDIDATE_NONE,
        ):
            per_candidate_seconds = 60.0
        else:
            per_candidate_seconds = parse_duration(per_candidate)
        serial_estimate = len(jobs) * per_candidate_seconds
        wall_estimate = serial_estimate / max(1, lane.judge.mutation.jobs)
```

It is emitted as `estimated_serial_seconds`/`estimated_wall_seconds` (`cli.py:1848-1849`). The parser is at `cli.py:330-371`.

- **Flag: the documentation is wrong for `auto`.** `docs/CONSUMERS.md:2307-2312` calls the figure an upper bound ("no longer than"). But `auto` resolves to `max(3b, b+60) >= 60` (`mutation.py:1892-1909`), so the 60 s fallback is a **lower** bound on the auto per-candidate ceiling. For B105 that is 3,760 × 60 s, against a measured mean of about 560 s.
- **Pinned values:** `tests/test_mutation_progress_budget_plan.py:1559-1560` (30/15, explicit `"30s"`) and `:1955-1956` (60/30 fallback, parametrized). No test pins the plan payload's key set, so adding keys is safe.

### Data already in existing artifacts

**Progress JSONL:**
- `run` header: `lane`, `commit`, `budget_s`, `budget_per_candidate_s`, `started` (`mutation.py:951-998`; the lane call is at `cli.py:1188-1204`).
- `plan`: `baseline_s` (`round(baseline_wall_seconds(R0 baseline), 3)`), `budget_per_candidate_s`, `derived`, `liveness`, `slowest_test_s`, `worst_gap_s`, `expect_next_event_within_s`, `pre_first_event_within_s` (`mutation.py:2223-2257`). It is emitted only when an R2 sweep starts.
- `command_finished`: `phase` (`"baseline"` for snapshot lanes, `runner.py:2835/3744`; `"direct"` for R0-only, `:6344`), `outcome`, `returncode`, `started`, `ended` (`runner.py:3088-3100`). The preflight lane's progress already yields a measured baseline wall as `ended − started`, which is the same formula as `baseline_wall_seconds` (`mutation.py:1877-1889`).
- `candidates`: `candidate_total`/`selected_total`/`pending_total`/`commit` (`2445-2453`).
- `candidate`: `candidate_id`, `candidate_index`, `path`, `operator`, `outcome_bucket`, `elapsed_seconds`, `tests_completed` (`2930-2955`).
- `end`: bucket counts (`2292-2303`).
- Every record also carries `emitted_at` and `elapsed_s` (`mutation.py:917-940`).

**Verdict:**
- There is no baseline timing field. There are only the lane-level top-level `started`/`ended`.
- Under `auto`, `judgment.r2.budget_per_candidate_derived_s` is present, and it could be inverted (≥90 means b = d/3, otherwise b = d−60). That is fragile; prefer progress.
- Claims carry no timing (claim keys: `canary, coverage, detail, …, mutation, reason_code, red_first, rigor, source, status, verified_by_assay`).

### Parsers to reuse

- `analysis.inspect_progress` (`src/assay/analysis.py:326-353`) segments by `run`, checks commits, and keeps only `candidates/resume/end/verdict_written` milestones. Extend or reuse its segmentation to also retain `plan.baseline_s` and the `candidate` samples.
- `_report_progress` (`analysis.py:703-825`) has bounded framing and torn-record handling worth reusing.
- **Overlap flag:** an **uncommitted** B107 `campaign.py` exists in the nested worktree `../.worktrees/assay-b107-analysis-30eec294/assay/src/assay/campaign.py`, with `_read_progress` (`:160`) and sample collection filtered to full-mode killed/survived/equivalent (`:1055-1080`). It duplicates much of what C needs. Coordinate before building a second reader.

### Specification sketch

- New flag `assay plan --baseline-from PATH` (progress JSONL; optionally a verdict).
- Select the latest `run` segment whose `lane == args.lane`.
- Baseline: `plan.baseline_s`; failing that, the newest `command_finished` with phase `baseline`/`direct`.
- Optional per-bucket candidate samples from `candidate.elapsed_seconds`.
- Output adds `estimate_provenance: "measured"|"fallback"` and `estimate_source: {path, sha256, run_line, commit, commit_matches_head, baseline_s, samples_by_bucket}`.
- For `auto`: the upper bound is `count × auto_budget_per_candidate_seconds(baseline_s) / jobs`. Reuse that function; don't recompute it.
- A separately labelled "full-suite central" figure is `count × baseline_s / jobs`.
- With no flag the output stays byte-identical plus `estimate_provenance: "fallback"`.

**Flags:**
- `baseline_s` measures the **coverage-instrumented** R0 command. B110's uninstrumented R2 command would be faster, so provenance must name which command was measured.
- "/jobs" assumes perfect balance, which is truer after A than under waves.

---

## D. Persisted campaign deadline (B110)

### What exists

- **`LaneDeadline`** is at `src/assay/runner.py:210-308`. It is monotonic-only: `start()` at `:234-262`; `tightened(seconds)` at `:268-294` (never widens, and raises `ValueError` for seconds ≤ 0); `remaining()` at `:296-308` raises `BUDGET_EXCEEDED`/`LANE_TIMEOUT`.
- **Start sites:** `cli.py:1169-1171` in `_run_reserved`, before HEAD; `cli.py:1648` (plan); the library fallback at `runner.py:5753-5758`. The label-grace pattern at `cli.py:1279-1282` builds a `LaneDeadline(expires_at=…)` directly, which is the model for injecting an externally computed expiry.
- **Order of work:** HEAD (`cli.py:1225`) → `run_lane` → environment probe (`runner.py:5764-5830`, `min(PROBE_BUDGET_SECONDS, remaining)`) → snapshot → R0/R1 → R2 (`collect_mutation_sites` at `mutation.py:2281`).
- **State store:**
  - `--state-dir` flag at `cli.py:418`, resolved and refused at `cli.py:845-878` (called at `:804`); it must be gitignored or outside the tree.
  - `default_state_root` = `<project>/.assay/mutation-state` (`mutation.py:1187-1189`). Record name is `<64hex>.json` (`:1164-1184`); atomic write at `:1244-1270`. Nothing scans the directory, so a non-hex filename cannot collide.
  - `MUTATION_STATE_SCHEMA_VERSION = 1` is shared with shard manifests (`:207-220`).
  - Judge identity `judge_sha256(tree_sha256, plan, link_paths, tool_version)` (`mutation.py:1052`) is computed once at `2353-2366`. It does **not** include lane name or candidate set.

### Where the deadline would plug in

- **Create before preflight.** The gate driver knows commit/tree (`tools/self-qualification-gate.sh:26-27`) and version/wheel digest (`:100-123, :151`) before it first calls `assay run` (`:231`).
  - A plan identity can also be computed without executing, from `assay plan self-qualification` (the ordered candidate IDs → sha256).
  - Write e.g. `.assay/campaign-deadline-self-qualification.json` = `{commit, git_tree, lanes, assay_version, wheel_sha256, plan_sha256, expires_at_utc, created_at_utc}`, atomically, with the same temp+replace pattern as `mutation.py:1256-1263`.
- **Consume in assay.**
  - Add a new `--campaign-deadline PATH`, resolved beside `_resolve_state_dir` (`cli.py:804`) with the same visibility refusal.
  - In `_run_reserved`, after HEAD (`cli.py:1225`), validate commit, lane ∈ lanes and `assay.__version__`.
  - Then `deadline = deadline.tightened(expires_at_utc − now_utc)`. Remaining ≤ 0 must produce the existing pre-`run_lane` `LANE_TIMEOUT` refusal (`cli.py:1240-1300`), since `tightened` rejects ≤ 0.
  - `plan_sha256` is checked in `run_mutation` right after selection (`mutation.py:2343`), before any submission. A mismatch means refusal.
- **Pass it to both** `assay run` invocations (gate script `:183-188`), and derive the `timeout` value from the same absolute instant plus a collection grace.

### Current time bounds

- `run-gate.toml:36-51` (self-qualification): `argv = ["timeout", "--verbose", "--signal=TERM", "--kill-after=30s", "7h30m", "bash", …]`; `budget = "8h"` is advisory (comment `:41-46`); `resources = {cpus = "3", memory = "2g", memory_swap = "8g"}`.
- `run-gate.toml:53-62` (preflight): `budget = "60m"` and **no** `timeout` wrapper.
- `assay.toml:96` full lane `budget = "5h"`; `:206` preflight `"60m"`; `:182` canary `budget_per_attempt = "30m"`; `:172-177` `jobs = 1`, `max_mutants = 10000`, `budget_per_candidate = "auto"`, `liveness = true`.
- The driver runs preflight (fresh 60 m clock), then the full R0-R3 lane (fresh 5 h clock, R0/R1 re-executed), each with `--resume --state-dir .assay/mutation-state-$lane`. Every re-invocation gets a fresh clock, which is exactly what B110 prohibits.
- Nyxloom has an 8 h outer watchdog.

### D ambiguities

- **Wall clock vs A-193.** A deadline that survives restarts must be wall-clock UTC. That reintroduces the clock-adjustment exposure A-193 separated out. Convert once per process (`expires_mono = monotonic() + (expires_utc − time())`) and record the decision.
- **Binding the preflight lane.** Preflight is a different lane with a different state dir. Either bind a campaign `lanes` list, or accept that preflight is bounded only by the outer timer.
- **Init vs inherit.** "Absent/mismatched refuses", but a new commit legitimately needs a new campaign. So there must be an explicit init operation (driver or subcommand), and the resume path must refuse to create a new clock.
- **SIGTERM, needs verification.** Assay installs no signal handler, and `LivenessRunner` launches candidates with `start_new_session=True` (`liveness.py:1375`). If I read GNU `timeout` correctly (it signals its own process group), a TERM from `timeout 7h30m` would kill assay but not an in-flight pytest candidate, which then keeps running until container teardown. That is directly relevant to B110's "leaves no running gate container behind".

---

## E. Running a chosen subset of candidates (pilot)

### Existing mechanisms: none selects explicit IDs

- **`--shard I/N`** (`cli.py:289`, parsed at `runner.py:5934-5980`): a blake2b-4 hash mod N partition (`mutation.py:1500-1520`), applied at `mutation.py:2324-2343`.
- **`--operators`** (`cli.py:288`, `_cmd_run:726-762`): rewrites the lane's `operators`, which the verdict then records as `judgment.r2.operators`.
- **`--rejudge`/`--rejudge-outcome`** (`cli.py:301-327`): require `--resume` (`mutation.py:2158-2164`) and only drop resume records. Everything unresumed still runs.
- **`--reuse-from`**: witness replay; refuses `--shard`; still covers the full plan.
- **`assay plan --shard`**: preview only.

### How shards are treated

- A sharded run records `judgment.r2.shard_index/shard_count` and `mutation.candidate_ids` = the shard's slice (`mutation.py:2503-2522`; A-461 at `decisions.md:940`).
- Verify requires `candidate_ids` to equal the bucket IDs (`verify.py:1678-1736`, `verdict.py:5288-5322`), and **nothing else**.
- `judge_mutation` (`mutation.py:3700-3745`) ignores shard fields, so **a sharded verdict can be PASS and `assay verify` accepts it** as an ordinary verdict.
- `merge_mutation_shards` (`mutation.py:1529-1641`) is library-only with no CLI caller. B023 (`4-backlog.md:2390`) and B026 (`:2746`) are DEFERRED.
- **Gap:** `tools/b105_report_check.py:14-107` checks commit, lane, rigor, PASS and provenance, but **does not refuse `judgment.r2.shard_index` or a partial scope**. Add that refusal whatever the pilot design.

### Recommended minimal mechanism

**Plumbing:**
- `assay run <lane> --candidates-file PATH` takes one 64-hex ID per line, built by a deterministic selector over `assay plan` JSON rows (`cli.py:1820-1840`: id/path/operator).
- Path: `cli.py` run parser (beside `:289`) → `runner.run_lane(..., candidates=…)` (the call at `cli.py:1490-1512`) → `_run_prepared_lane` (`runner.py:5160/5368`) → `_run_higher_rigor_lane` (`:3579`) → `run_mutation` (`runner.py:4450-4531`) → selection block at `mutation.py:2324-2343`.
- Selection: `selected_indices = [i for i, j in enumerate(job_list) if candidate_id(j) in requested]`, which keeps plan order.
- Unknown IDs reuse the `_reject_unknown_rejudge_ids` → `InvalidRejudgeIdError` → whole-lane BAD_LANE_CONFIG path (`runner.py:4533-4542`). Refuse duplicates, an empty list, and combination with `--shard` or `--reuse-from`.
- Require `--state-dir`: the state root is required when a scope is selected (`mutation.py:2145-2149`).
- The `candidates` event already carries `selected_total` < `candidate_total`. Add `selection_sha256` to it; no new event name is needed.

**Never PASS.** Two options:
1. **No verdict (minimal, no schema change; recommended for the pilot).** Refuse `--verdict-json` together with `--candidates-file`, print a pilot summary marked `qualifying: false`, and exit with a fixed non-zero code even if every candidate was killed. The pilot's deliverable under B110 is measurement: progress, state and resource samples.
2. **Explicit partial verdict (durable).** Add `judgment.r2.selection` and a verifier rule "selection present means status ≠ PASS". This needs a new ReasonCode (closed enum, `errors.py`) and a schema bump, so fold it into v14. It would also let shards become partial and never-PASS.

**E ambiguities:**
- A pilot on `self-qualification` also runs R1 (a full coverage run) and R3 (a canary up to 30 m); no runtime rigor override exists. A separate `R0,R2` pilot lane changes the lane name.
- `judge_sha256` omits the lane name. So pilot state records could be resumed by the full campaign if the `CommandPlan` is identical. Decide whether that is wanted, given B110's plan binding.

---

## F. Per-candidate resource evidence

### What exists

- The liveness monitor already samples **tree CPU** every 1 s poll: `cpu_now = self._cpu_reader(proc.pid)` (`liveness.py:1490-1498`) through `tree_cpu_seconds` (`:586-625`). The value is only used for the hung decision and then discarded.
- **Nothing samples RSS.**
- This only applies to liveness lanes (`runner.py:4418-4430`). Other lanes use `subprocess.run` (`runner.py:321-347`) with no sampling.

### Cheapest path

- **RSS for free.** `_pid_cpu_ticks` (`liveness.py:529-544`) already reads `/proc/<pid>/stat`. Field 24, RSS in pages, is `fields[21]` in the same split. So a `tree_sample(pid) -> (cpu_seconds, rss_bytes)` costs no extra syscalls.
- **In the monitor:** track the last successful CPU value (a lower bound, up to 1 s stale at exit) and the sampled peak tree RSS (1 Hz; a lower bound on the true peak). Per-process VmHWM from `/proc/<pid>/status` costs one extra read per pid.
- **Sidecar file.** Write a sidecar `events_path.with_suffix(".resources.json")` on every monitor exit: normal, hung and timeout.
  - Add it to the stale-file cleanup (`liveness.py:1351-1359`).
  - Read it in `_run_attempt` next to `tests_completed` (`mutation.py:2742-2749`) with the same `candidate_events_path(liveness_events_dir, resolve_run_cwd(...))` key, which is per-snapshot-cwd and therefore safe at `jobs > 1`.
- **Carry the values through:**
  - Add `cpu_seconds`/`peak_rss_bytes` to `_MutantRun` (`mutation.py:1644-1666`).
  - In `_run_one` (`2857-2887`), sum CPU and take the max RSS across the replay and full attempts.
  - Emit them in the `candidate` event (`2930-2955`, beside `tests_completed`, `None` when not a liveness lane) and in the state record (`2967-2988`).
  - The state-record loader checks only its required keys (`mutation.py:1313-1325`), so extra fields are backward-compatible with no schema bump.
- **Injection-seam blast radius:** 17 `cpu_reader=` injections in `tests/test_liveness_runner_monitor.py` and `tests/test_cli_run.py`. Add a separate optional `sampler` rather than changing `cpu_reader`'s return type.
- **Lane-level alternative:** cgroup v2 `memory.peak` at lane end covers the whole gate container, not a single candidate. For non-liveness lanes, a `resource.getrusage(RUSAGE_CHILDREN)` delta is valid only at `jobs=1` (it is process-wide).

---

## Ambiguities to decide before implementing

1. (A) The `candidate` progress order policy: a reorder buffer (recommended; keeps run-gate/analysis consumers correct) or completion order with a new count field that run-gate would adopt.
2. (A) How "first fatal" is defined under concurrency.
3. (A) Whether B105 moves off `jobs=1` (A-462 says "serially").
4. (B) Monkeypatch the tests only, or add lane keys. If keys: v14 disclosure, and whether the growth floor scales with the window.
5. (D) Wall clock vs A-193; binding the preflight lane; who initializes the deadline; `timeout` signals not reaching `start_new_session` candidates.
6. (E) A non-verdict pilot vs a v14 partial verdict; whether pilot state records may seed the full campaign.
7. (C) Coordinate with the uncommitted B107 `campaign.py`; name which command `baseline_s` measured.