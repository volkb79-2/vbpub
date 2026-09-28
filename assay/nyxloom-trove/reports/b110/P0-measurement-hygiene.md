# B110-P0 — Measurement evidence and campaign hygiene

Revised 2026-09-28 after round-1 and round-2 reviews (see REVIEW-2026-09-28-round1.md, REVIEW-2026-09-28-round2.md).

| Field | Value |
|---|---|
| Backlog | **B111** (split from B110) |
| Branch | `assay-b110-p0-measure`, cut from the integration line `assay-b110-integration`, which the plan §11.1 reconciliation creates (C18) |
| Depends on | nothing |
| Unblocks | P1 (needs the `tests/test_liveness.py` leak fix), P4 and P7 (carry the new candidate fields), P5 (needs the G1–G5 guards), P3d (extends the report-checker refusals) |
| Merge order | P0 merges **before P6**. Both edit `run_and_verify_lane` in `tools/self-qualification-gate.sh` and the gate-script pins in `tests/test_self_lane.py`, so P6 rebases onto P0. Plan §11.6 is amended accordingly (round-1 P0-7). |
| Contract class | **2c**: bounded integration. Every public shape below is fixed; you choose only local glue. |
| Implementer | Sonnet for items W1, W3–W6. Opus (fresh session) for W2, the resource sampler, because it touches the liveness monitor loop. One implementer may do all of it if it is Opus. |
| Decisions | **A-472** (plan D8): the snapshot guards land before any snapshot optimization. **A-474** (plan D10): the report checker refuses sharded or partial inventories. **A-464**: timings are diagnostic only and never classify a candidate. |
| Size | M. Six independent work items, each with its own tests. Commit per item. |

**What this package is for.** It adds the measurements that the pilot (plan §7) and the planner need. It also fixes four hygiene defects found in the runtime analysis. **Nothing in this package may change a candidate's classification, a verdict, or the verdict schema.** Every new field is optional progress, state or planner output.

---

## Context to read first

Paths are relative to `assay/`. Line numbers were verified at HEAD `db85f747`.

1. The plan, `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §0 (host-load rule), §3 D8/D10, §7 (what the pilot reads from this package), §10 (gates).
2. The analysis, `nyxloom-trove/reports/assay-B110-RUNTIME-ANALYSIS-2026-09-28.md`: the planner, resource, liveness-leak and snapshot sections (for background; the contract is below).
3. **W1, planner:**
   - `src/assay/cli.py:330-371`: the `plan` parser.
   - `src/assay/cli.py:1571-1600`: `_cmd_plan` entry and the `LaneConfigError` refusal pattern.
   - `src/assay/cli.py:1780-1853`: the 60 s fallback and the payload dict.
   - `src/assay/mutation.py:1877-1909`: `baseline_wall_seconds`, `auto_budget_per_candidate_seconds`.
   - `src/assay/mutation.py:951-998`: the `run` progress header (`lane`, `commit`).
   - `src/assay/mutation.py:2223-2257`: the `plan` event (`baseline_s`).
   - `src/assay/runner.py:3088-3100`: `command_finished` (`phase`, `started`, `ended`).
   - `src/assay/analysis.py:326-353`: `inspect_progress`, the segmentation pattern to mirror.
   - `tests/test_mutation_progress_budget_plan.py:1550-1565` and `:1935-1960`: the pinned fallback figures, which must stay green.
   - `docs/CONSUMERS.md:2296-2320`: the "upper bound" text that W1 corrects.
4. **W2, resources:**
   - `src/assay/liveness.py:529-544`: `_pid_cpu_ticks`, which reads `/proc/<pid>/stat`. The split idiom is at `:540-541`.
   - `src/assay/liveness.py:586-625`: `tree_cpu_seconds`.
   - `src/assay/liveness.py:1299-1333`: `LivenessRunner.__init__`, the injection seams.
   - `src/assay/liveness.py:1336-1380`: `__call__`, the stale side-file cleanup and `popen` with `start_new_session=True`.
   - `src/assay/liveness.py:1441-1541`: `_monitor`.
   - `src/assay/runner.py:4418-4430`: the production `LivenessRunner(...)` construction.
   - `src/assay/mutation.py:1643-1666`: `_MutantRun`.
   - `src/assay/mutation.py:2646-2760`: `_run_attempt`, including its timing points and the `tests_completed` read-back.
   - `src/assay/mutation.py:2857-2888`: `_run_one`.
   - `src/assay/mutation.py:2929-2989`: the `candidate` event and the state record.
   - `src/assay/mutation.py:1305-1330`: the state loader's required-key check. Extra keys are tolerated.
5. **W3, setup/teardown forwarding:**
   - `src/assay/liveness.py:160-236`: the plugin source, which writes `phase` records for setup/teardown.
   - `src/assay/liveness.py:630-720`: event names and `_selected_test_events`.
   - `src/assay/liveness.py:785-800`: `baseline_test_events`.
   - `src/assay/mutation.py:2258-2272`: the forwarding loop.
   - `src/assay/mutation.py:846-870`: `PROGRESS_EVENTS`, a closed vocabulary. Add fields, never events.
   - `tests/test_liveness_proc_helpers.py:656-695`: pins the exact `{nodeid, outcome, duration_s}` triple; keep it green.
   - `tests/test_mutation_progress_budget_plan.py:212-275`.
6. **W4, liveness leak:**
   - `tests/test_liveness.py:532-545`: `_load_materialized_plugin`.
   - The leaking tests at `:570-587` (`:581`), `:590-606` (`:601`), `:629-676` (`:656` setenv, `:675` sessionfinish) and `:798-817` (`:803`).
   - `src/assay/liveness.py:1060-1130`: `_EventProgressReader`, which is how the monitor reads `session_finish`. (`:1016-1057` is `_read_events_progress`, a different reader.)
   - `src/assay/liveness.py:1475-1476` and `:1516-1519`: the 30 s post-finish `hung` branch.
7. **W5, report checker:**
   - `tools/b105_report_check.py:14-147`, the whole file.
   - `tools/self-qualification-gate.sh:156-214`: `run_and_verify_lane`. Note: `assay run` executes in `$project` (the mounted worktree's `assay/`, `cd` at `:24`), whose HEAD is `source_commit` and is re-checked by `ensure_source_unchanged`. Only the checker runs from `$scratch/source`.
   - `tests/test_b105_report_check.py:20-80`: `_verifier_valid_report`.
   - `tests/test_self_lane.py:176-200`: the gate-script substring pins.
   - `tests/fixtures/verdicts/r2_pass.json`: it carries `claims[R2].mutation.candidate_ids`.
8. **W6, snapshot guards:**
   - `src/assay/isolation.py:67` (`_FIXED_MTIME`), `:569-588` (`SnapshotRepository`, `_seed_git_dir`), `:670-700` (`materialize`, `materialize_replacement`), `:762-863` (`_build`), `:1011-1085` (`_verify`, status at `:1040-1044`), `:1154-1166` (`_copy_objects`) and `:1870-1958` (`_write_worktree`).
   - `tests/test_isolation.py:62-160`: the helpers `_git`, `_root_repo`, `_scratch`, `_spec`, `TIMEOUT` and `LITERALS`. Copy their pattern.
   - `tests/test_isolation.py:1324-1345`.
   - `nyxloom-trove/carve-assets/P22/test_acceptance.py:170-175` and `:266-309`: the O2 disjoint-inode check. It is not collected today; W6 ports it.

---

## Implementation packet (normative)

### Interfaces and grammar

**W1, `assay plan --baseline-from`.** It is owned by `src/assay/analysis.py` (reader) and `src/assay/cli.py` (flag and payload).

```python
# src/assay/analysis.py
@dataclass(frozen=True, kw_only=True)
class MeasuredBaseline:
    path: str            # as given on the CLI
    sha256: str          # sha256 of the progress file's bytes
    run_line: int        # 1-based line of the selected `run` event
    lane: str            # the selected run's lane
    commit: str | None   # the selected run's commit; None when the run header carried null (emit_run_header allows it)
    baseline_s: float    # > 0, finite
    source_event: str    # "plan" | "command_finished"

def measured_baseline_from_progress(path: Path, *, lane: str) -> MeasuredBaseline: ...
```

**Framing.**
- Lines are split on `\n`.
- A **torn tail**, meaning a final line with no trailing newline that fails to parse as JSON, is ignored, following the existing torn-record handling in `analysis._report_progress`.
- Every other line that fails to parse, or parses to a non-object, is a refusal.

**Selection.** Use the **last** `run` segment in the file whose `run.lane == lane`. A segment runs from its `run` event up to, but excluding, the next `run` event of any lane.

**Measurement.** The rules are explicit and mutually exclusive:
1. **`plan` events in the selected segment.**
   - More than one → refusal "multiple plan events in the selected segment".
   - Exactly one, whose `baseline_s` key is **absent or `null`** → it is not a measurement; go to rule 2.
   - Exactly one, whose `baseline_s` is present but not a finite number `> 0` (including `0`, a negative number, a string, a bool, NaN or Inf) → refusal "plan.baseline_s is not a finite positive number". It is never skipped.
   - Otherwise → `baseline_s` from it, with `source_event = "plan"`.
2. **Otherwise, the `command_finished` fallback.** Take the segment's **last** `command_finished` event with `phase` in `{"baseline", "direct"}` and `outcome == "PASS"`. Earlier records and FAIL records are ignored.
   - For that one record, `started` and `ended` must both parse as ISO-8601 UTC, and `ended > started`. Otherwise refusal "the selected command_finished record has an invalid interval". The fallback never walks back to an earlier record.
   - `baseline_s = ended − started` in seconds, with `source_event = "command_finished"`.
3. **Otherwise** → refusal "the selected segment has no measurement".

`commit` is copied from the selected `run` header. When it is `null`, `commit_matches_head` is `false`.

**Refusals.** Each raises `LaneConfigError(f"--baseline-from {path}: <reason>")` and exits ERROR/BAD_LANE_CONFIG. They are numbered for the tests:
1. the file is unreadable;
2. a non-tail line is malformed JSON or not an object;
3. an event precedes the first `run`;
4. no `run` has `lane == lane`;
5. multiple `plan` events in the selected segment;
6. `plan.baseline_s` is present but not a finite positive number;
7. the selected `command_finished` record has an invalid interval;
8. the selected segment has no measurement.

**Unsupported lanes.** When the lane's R2 is `unsupported` (`cli.py:1765-1772`), the file is still read and validated, so refusals 1–8 still apply. The payload stays **byte-identical** to today's unsupported payload: it gets no `estimate_provenance` or `estimate_source`, because no estimate exists on that branch.

**CLI:**
- `assay plan <lane> --baseline-from PATH [--baseline-lane NAME]`.
- `--baseline-lane` defaults to `<lane>`. B105 uses `--baseline-lane self-qualification-preflight`, because the preflight lane's progress carries the same declared command.
- `--baseline-lane` without `--baseline-from` is refused (`LaneConfigError`).

**Payload rules** (the `"status": "ok"` branch only):
- **Without `--baseline-from`:** the payload is the current payload plus exactly one new key, `"estimate_provenance": "fallback"`. Nothing else changes.
- **With `--baseline-from`:** `estimate_provenance: "measured"`, plus:

```json
"estimate_source": {"path": "...", "sha256": "<hex>", "run_line": 12, "lane": "self-qualification-preflight",
                    "commit": "<hex>", "commit_matches_head": true, "baseline_s": 548.35,
                    "source_event": "command_finished", "measured_command": "declared-baseline"},
"full_suite_central_serial_seconds": 2061796.0,
"full_suite_central_wall_seconds": 2061796.0
```

- `commit_matches_head` compares with the lane file's repository `HEAD`. Resolve it the same way `_cmd_plan` already resolves the commit it plans.
- `measured_command` is the constant `"declared-baseline"` in this package. P3b adds `"r2-nocov-baseline"`.

`estimated_serial_seconds` in measured mode depends on the lane's `budget_per_candidate`:

| Declared `budget_per_candidate` | `estimated_serial_seconds` | Note |
|---|---|---|
| duration `D` | `count × D` | unchanged |
| omitted or `"auto"` | `count × auto_budget_per_candidate_seconds(baseline_s)` | call the existing function; do not re-derive it |
| `"none"` | `null` | there is no per-candidate bound |

- `estimated_wall_seconds` is the serial figure divided by `max(1, jobs)`, or `null`.
- `full_suite_central_*` is `count × baseline_s` (serial) and that value divided by `max(1, jobs)` (wall). Round to 3 decimals, exactly like the existing keys.

The illustrative numbers above are not oracles.

**W2, per-candidate resources and phases.**

```python
# src/assay/liveness.py
class TreeSample(NamedTuple):
    cpu_seconds: float
    rss_bytes: int

def tree_sample(root_pid: int) -> TreeSample: ...   # same traversal/raise contract as tree_cpu_seconds
RESOURCE_SIDECAR_SUFFIX = ".resources.json"
def read_resource_sidecar(events_path: Path) -> dict[str, Any] | None: ...  # tolerant; None when absent/unreadable/malformed
def first_event_times(events_path: Path | None) -> tuple[float | None, float | None]:
    """(t of the owner's first `session_start`, t of the owner's first `test` record)."""
```

**Field indexing.** After the `raw[close_paren + 2:].split()` idiom at `liveness.py:540-541`, `fields[0]` is field 3. So `fields[i]` is field `i + 3`:

| Field | Index | Meaning |
|---|---|---|
| utime (14) | `fields[11]` | CPU in user mode |
| stime (15) | `fields[12]` | CPU in kernel mode |
| cutime (16) | `fields[13]` | user CPU of waited-for children, recursively |
| cstime (17) | `fields[14]` | kernel CPU of waited-for children, recursively |
| rss (24) | `fields[21]` | resident pages; multiply by `os.sysconf("SC_PAGE_SIZE")` |

Refactor `_pid_cpu_ticks` so one read yields a private record of all five values. `tree_cpu_seconds`, the classification input, must keep its exact behavior (utime+stime of live processes only) and its exceptions.

**`TreeSample` semantics** (round-1 P0-4; these are fixed, diagnostic only):
- **Per-sample value.** A sample's `cpu_seconds` = Σ over every **live** process in the tree at that sample of `(utime + stime + cutime + cstime) / CLK_TCK`.
  - **Pinned walk order (round-2 P0R2-3).** The walker reads each process's own stat line **before** listing its children. This is the pre-order walk that today's `tree_cpu_seconds` already uses (`liveness.py:607-622`). A post-order walker (children first) is forbidden: it can read a child live and then read the parent after that child was reaped into the parent's `cutime`, counting it twice. With pre-order reads, a child reaped mid-walk is **lost** from that sample, never double-counted.
  - When a process is reaped, the kernel folds its own and its children's times into its parent's `cutime`/`cstime`, and it stops being live. So git and pytest children that were already reaped still count, through their live ancestor. A plain live-tree utime+stime sum would lose them.
  - This deliberately counts more than "root cutime/cstime only". A grandchild reaped by a still-live intermediate process appears only in that intermediate's `cutime`.
- **Per-sample guarantee.** Each sample is a **lower bound** on the tree's cumulative CPU at that instant. The series is **not** monotone. It can dip:
  - on a mid-walk reap;
  - on reparenting out of the tree (a double-forked daemon);
  - for children auto-reaped under `SIGCHLD=SIG_IGN`, whose time never reaches any `cutime`.

  CPU spent after the last sample is also missing, up to one poll interval. Documentation says all of this and does **not** claim "exactly once".
- **Recorded value (round-2 P0R2-3).** The recorded `cpu_seconds` is the **maximum over samples**, not the last sample. The maximum is still a valid lower bound on the candidate's total CPU. The last sample can be lower than an earlier one after a dip.
- **Memory.** `rss_bytes` = Σ over live processes of `rss × SC_PAGE_SIZE` at the sample. `peak_rss_bytes` is the maximum over samples.
  - Documentation calls it a "**1 Hz lower bound on peak Σ RSS**", and states that Σ RSS itself overcounts shared and copy-on-write pages, so it is **not** a bound on true peak memory use.
  - Aggregate container peak evidence comes from run-gate's cgroup `memory.peak`, not from this field (plan §8, C19).

**`LivenessRunner.__init__`** gains `sampler: Callable[[int], TreeSample] | None = None`. It does not change `cpu_reader`: the 17 existing `cpu_reader=` injections stay byte-identical. When `sampler` is not `None`, `_monitor` calls it once per poll tick, **after** the classification inputs for that tick are computed:
- A sampler exception is swallowed; that tick contributes no sample.
- The sampler value **never** enters `cpu_growing`, `hung` or `timeout`.

**Monitor bookkeeping and sidecar.** `_monitor` keeps `samples` (count), `max_cpu_seconds` (the maximum over samples, per the semantics above) and `peak_rss_bytes`. The sidecar's `cpu_seconds` is `max_cpu_seconds`. On **every** exit path it writes `events_path.with_suffix(RESOURCE_SIDECAR_SUFFIX)` atomically (tmp + `os.replace`) with exactly:

```json
{"format": 1, "samples": 37, "cpu_seconds": 35.21, "peak_rss_bytes": 212992000, "spawned_at": 1790000000.123}
```

- The exit paths are: normal return, `LivenessHungExpired`, `TimeoutExpired`, and any exception propagating from the loop. Use `try/finally`.
- `cpu_seconds` and `peak_rss_bytes` are `null` when `samples == 0`.
- `spawned_at` is `time.time()` captured immediately before `popen` in `__call__`, passed into `_monitor`.
- A write failure is swallowed. Diagnostics never fail a candidate.
- Add the sidecar to the stale-file cleanup tuple at `liveness.py:1351-1359`.

**Production wiring.** `runner.py:4420` passes `sampler=liveness.tree_sample`. That is the only production caller.

**`_MutantRun`** gains:
```python
cpu_seconds: float | None = None
peak_rss_bytes: int | None = None
phase_seconds: Mapping[str, float] | None = None    # {"materialize": s, "command": s, "integrity": s, "teardown": s}
startup_seconds: Mapping[str, float | None] | None = None  # {"to_session_start": s|None, "to_first_test": s|None}
```

**`_run_attempt` measures `phase_seconds`** with `time.monotonic()` in the worker:
- `materialize` runs from `started_monotonic` (`:2657`) to entry into the snapshot `with` body;
- `command` covers the `execute_plan(...)` call;
- `integrity` covers `_snapshot_left_dirt(...)`;
- `teardown` runs from the end of the integrity check to the moment the snapshot `with` statement has exited. That covers `_remove_owned_tree` (`isolation.py:726-727`) and the context-manager exit. When the attempt raises before the `with` body completes, `teardown` is still stamped in a `finally` if the body was entered; otherwise it is absent.

The key set of `phase_seconds` is therefore exactly `{"materialize", "command", "integrity", "teardown"}` whenever the attempt reached the end of its `with` body. It measures these even when liveness is off. When `liveness_events_dir is not None`, it reads the sidecar and `first_event_times` from the same `candidate_events_path(...)` key that the `tests_completed` read uses (`:2742-2749`). Then:
- `to_session_start = t_session_start − spawned_at`;
- `to_first_test = t_first_test − spawned_at`;
- each is `None` when either operand is missing.

**`_run_one`** combines its attempts (replay, full):
- `cpu_seconds` is the sum over non-`None` values, or `None` if all are `None`;
- `peak_rss_bytes` is the max;
- `phase_seconds` is the per-key sum;
- `startup_seconds` comes from the **last** attempt.

**Wire placement:**
- The `candidate` progress event gains `cpu_seconds`, `peak_rss_bytes`, `phase_seconds` and `startup_seconds`, next to `tests_completed`. `cpu_seconds` and `phase_seconds` values are rounded to 3 decimals.
- The state record gains one optional top-level key: `"resources": {"cpu_seconds": ..., "peak_rss_bytes": ..., "phase_seconds": {...}, "startup_seconds": {...}}`.
- `MUTATION_STATE_SCHEMA_VERSION` stays 1. The loader must keep accepting records without `resources`.

**W3, baseline setup/teardown durations.** A new helper:

`liveness.baseline_phase_durations(path: Path | None) -> list[dict[str, float | None]]`

It returns a list **aligned one-to-one and in order with `baseline_test_events(path)`**. Entry *k* is `{"setup_s": ..., "teardown_s": ...}` for the *k*-th forwarded `test` event.

**Owner rule.** Both lists come from **one** factored owner selection. It is the same rule `_selected_test_events` applies today: the session-owner pid, with foreign-pid records ignored. Do not write a second rule.

**Pairing rule** (round-1 P0-7, revised by round-2 P0R2-4). Node IDs can repeat, e.g. under `--keep-duplicates`. This rule pairs records **positionally in the owner's record stream**, never by summing per nodeid and never by occurrence count:
- For each owner `test` record (a call) with nodeid *N*, walk the owner's ordered record stream:
  - `setup_s` is the duration of the **nearest preceding unconsumed** `phase` record with `when == "setup"` and nodeid *N*. That record is then consumed.
  - `teardown_s` is the duration of the **next following** `phase` record with `when == "teardown"` and nodeid *N* that occurs before the next call record for *N*. That record is then consumed.
- A setup record with no call after it is never paired, because a setup error or skip produces no call. It therefore cannot shift later pairings, which occurrence-counting would do.
- A missing, non-numeric, bool, negative or non-finite duration gives `None` for that key.
- No duration is ever summed across occurrences.

At `mutation.py:2269-2272`, each forwarded baseline `test` event becomes:

`{"event": "test", "phase": "baseline", **test_event, "setup_s": ..., "teardown_s": ...}`

`baseline_test_events` itself stays the exact triple, and no event name is added.

**W5, report checker.** `tools/b105_report_check.py` gains `--plan-json PATH`. It is **required iff `"R2"` is in `--expected-rigor`**, and refused otherwise.

**Where the plan comes from, stated honestly** (round-1 P0-3):
- The gate script runs the **installed exact wheel's** `"$assay_bin" plan "$lane" --file assay.toml` **in `$project`**, immediately **before** `assay run` for the same lane.
- `$project` is the mounted worktree at `source_commit`, the same directory and tree `assay run` uses, and `ensure_source_unchanged` re-checks it. It is **not** the `$scratch/source` clone.
- So the plan is bound to the same tree and wheel as the run. It is not an independent re-derivation: a planner defect shared with the runner would agree with itself.
- The integration oracle O14 below closes the realistic gap, i.e. plan and run disagreeing on real data.
- The plan payload has no lane or commit field (`cli.py:1841-1856`), so the binding comes from the gate script's ordering and path, which O12 pins.

`verify_report_document` gains the keyword `plan: dict | None`. Factor the scope checks into `check_campaign_scope(document: dict, plan: dict) -> None`, which raises `ValueError`. O14 can then drive the scope checks with a real verdict and a real plan without the unrelated provenance checks.

**Refusals** (each a `ValueError` whose message names the check):
1. `plan` is missing while R2 is expected.
2. `plan["status"] != "ok"`.
3. `plan["shard"] is not None`.
4. `plan["candidate_count"] != len(plan["candidates"])`.
5. `document["judgment"]["r2"]` contains a non-null `shard_index` or `shard_count`.
6. The R2 claim's `mutation.candidate_ids` is missing, not a list, contains a non-64-hex string, or contains duplicates.
7. `set(candidate_ids) != {row["id"] for row in plan["candidates"]}`, or `len(candidate_ids) != plan["candidate_count"]`.
8. `--plan-json` given while `"R2"` is not in `--expected-rigor`. This is an argument refusal: exit 2 before the report is read.
9. The plan JSON is **structurally invalid**:
   - the top level is not an object;
   - `status` is not a string;
   - `candidate_count` is not an `int` (a bool is rejected);
   - `candidates` is not a list;
   - a row is not an object, or lacks an `id` that is a 64-lowercase-hex string;
   - `shard` is neither `null` nor a string.

   Each is a named `ValueError`, never an escaping `TypeError` or `KeyError`. The existing caught-exception tuple (`b105_report_check.py:139`) maps it to exit 2.

Check order: 8 (arguments) → 9 (structure) → 1–4 (plan) → 5–7 (report against plan). The first failure wins.

**W6, snapshot guards.** New file `tests/test_isolation_guards.py`. These are **test-only**; no source change. All five pass on today's code. Name the tests `test_g1_…` through `test_g5_…` so P5's search for `G[1-5]` finds them.

### Required flow (W2, the only multi-step flow)

1. `__call__` does the stale cleanup (now including the sidecar), sets `spawned_at = time.time()`, then calls `popen`.
2. `_monitor` loops. Each tick:
   1. Classification exactly as today.
   2. If `self._sampler`: `try: s = self._sampler(proc.pid)`. Update `samples`, `max_cpu_seconds = max(max_cpu_seconds, s.cpu_seconds)` and `peak_rss_bytes = max(...)`. `except Exception: pass`.
   3. Sleep.
3. On any exit, the `finally:` writes the sidecar, then the original return or raise propagates **unchanged**.
4. Back in `_run_attempt`, after `execute_plan` returns or raises through its normal path, read the sidecar and event times. A classification already decided is never revisited.

### Topology

The events file is `candidate_events_path(liveness_events_dir, resolve_run_cwd(snapshot.project_root, plan))`. The sidecar is the same path with suffix `.resources.json`; it is persistent per lane, keyed per snapshot cwd, and so safe at `jobs > 1`. Read it inside the `with` scope, exactly where `tests_completed` is read.

### Decision table (W2)

| State | `cpu_seconds` | `peak_rss_bytes` | `phase_seconds` | `startup_seconds` | Classification effect |
|---|---|---|---|---|---|
| Non-liveness lane | `null` | `null` | measured | `null` | none |
| Liveness lane, sampler raised every tick | `null` | `null` | measured | from events | none |
| Liveness lane, normal exit | max over samples | max sample | measured | from events | none |
| `hung` / `budget_exceeded` (killed) | max over samples before kill | max | measured (incl. `teardown`) | from events or `null` | none; bucket decided exactly as today |
| Sidecar unreadable or malformed | `null` | `null` | measured | from events | none |
| Resumed candidate (state record reused) | not re-measured; no new `candidate` event, as today | | | | none |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| W1 fallback unchanged | cli.py | O1 | existing `_write_plan_fixture` tests | drop the `estimate_provenance` key → O1 fails; change 60.0 → pinned tests fail |
| W1 measured, `auto` | cli.py + analysis.py | O2 | progress JSONL with a `run` (lane L) + `plan.baseline_s = 100.0`, lane `budget_per_candidate = "auto"`, 1 candidate | compute `count × baseline_s` instead of `auto_budget(...)` → O2 fails (expects 300.0 for b=100: max(300,160)) |
| W1 selection | analysis.py | O3 | two `run` segments for L (first 50 s, second 100 s) + one for another lane (999 s) | pick first segment or ignore lane → O3 fails |
| W1 command_finished fallback | analysis.py | O4 | segment without `plan`, with `command_finished` phase `baseline` PASS `started`/`ended` 40 s apart, plus a FAIL one | accept FAIL or first instead of last → O4 fails |
| W1 refusals | analysis.py | O5 | 8 malformed files (refusals 1–8 above) + a torn-tail file that must be accepted | silently return fallback → O5 fails; refuse the torn tail → O5 fails |
| W1 provenance fields | analysis.py + cli.py | O5b | a progress file with known bytes; a `run` header whose `commit` differs from HEAD; one whose `commit` is `null` | hash the path string instead of the bytes, or hard-code `commit_matches_head: true` → O5b fails |
| W2 sampler never classifies | liveness.py | O6 | `LivenessRunner` with fake `popen`/`monotonic`/`cpu_reader` (pattern: `tests/test_liveness_runner_monitor.py`); the full 3×3 matrix {normal exit, hung, busy→timeout} × {`sampler=None`, constant sampler `TreeSample(1e9, 10**12)`, sampler raising `OSError`} | feed sampler CPU into `cpu_growing` → the busy→timeout row with the constant sampler flips; call the sampler before classification → a raising sampler changes the row's exception |
| W2 sidecar on every exit | liveness.py | O7 | same harness, three runs: normal exit, hung, timeout; then a second `__call__` for the same cwd whose `popen` raises | write only on normal exit → O7 fails; skip the stale cleanup → the second call leaves the first call's sidecar → O7 fails |
| W2 CPU semantics | liveness.py | O7b | a parent that forks and **reaps** a child that burns ≥ 50 ticks of its own CPU, then prints `ready`; the test blocks on that line; precondition `cutime+cstime > helper utime+stime` (real processes; the assertion compares kernel-reported values, never a duration) | sum live utime+stime only → the reaped child's CPU is missing → O7b fails; a post-order walker → forbidden by the pinned walk order (review) |
| W2 wiring | mutation.py/runner.py | O8 | real-pytest R2 lane (`tests/test_b106_reuse_and_witness.py:661-677` seed pattern) | forget the event/record plumbing → the keys are absent → O8 fails; forget `sampler=` at runner.py:4420 → caught by a unit test asserting the production `LivenessRunner` construction passes `sampler=liveness.tree_sample` (spy on the constructor), since the sample count itself is host-dependent |
| W3 forwarding | liveness.py/mutation.py | O9 | baseline events file with setup/call/teardown for two nodeids; one nodeid occurring **twice** (duplicate run), preceded by a third occurrence with a setup record but **no** call (setup error); plus a foreign-pid record | forward setup as a `test` event, ignore the pid rule, sum per nodeid (double-counts the duplicate), or pair by occurrence count (mispairs after the call-less setup) → O9 fails |
| W4 leak | tests/test_liveness.py | O10 | nested pytest over `tests/test_liveness.py` with a live events file | revert one `monkeypatch.context()` → O10 fails |
| W5 refusals | tools/b105_report_check.py | O11 | `_verifier_valid_report` + generated plan JSON | skip the set-equality check → O11 fails; never read `plan["status"]` → the refusal-2 case fails; let a list-shaped plan raise `TypeError` → the refusal-9 case exits 1 instead of 2 |
| W5 wiring | gate script | O12 | `tests/test_self_lane.py` substring **and ordering** pins | omit `--plan-json`, or generate the plan after `run` → O12 fails |
| W5 real data | tools/b105_report_check.py | O14 | a real tiny R2 repo: real `assay plan` JSON + real `assay run` verdict (unsharded), and the same with `--shard 0/2` | a planner/runner ID-derivation mismatch, or accepting a shard → O14 fails |
| W6 guards | tests only | O13 | tiny repo, 2 commits | (future P5 hardlink / tree reuse / checkStat) → O13 fails |

### Degrees of freedom

Private helper names and the decomposition inside `liveness.py`, `analysis.py` and the checker are yours. So is whether `tree_sample` and `tree_cpu_seconds` share a private walker, provided that `tree_cpu_seconds`'s behavior and exceptions are unchanged, and that any walker used by `tree_sample` reads each process's stat **before** listing its children (the pinned pre-order walk; see `TreeSample` semantics). Every key name, JSON shape, refusal and wiring point above is fixed.

---

## Work

Do the items in order and commit after each one, with a green focused test run.

### W4. Stop `tests/test_liveness.py` from writing into the outer candidate's liveness stream

Do this first: P1 depends on it.

1. At `tests/test_liveness.py:581` and `:601`, wrap only the `plugin.pytest_sessionfinish(...)` call:
   ```python
   leak_guard = tmp_path / "sessionfinish.ndjson"
   with monkeypatch.context() as scoped:
       scoped.setenv(liveness.ASSAY_LIVENESS_EVENTS_ENV, str(leak_guard))
       plugin.pytest_sessionfinish(session=None, exitstatus=3)
   assert [json.loads(line)["event"] for line in leak_guard.read_text().splitlines()] == ["session_finish"]
   ```
   Use `exitstatus=0` at `:601`. Do not use `delenv`: the call must still write somewhere, so that the assertion proves the redirect.
2. At `:656` and `:803`, replace the whole-body `monkeypatch.setenv(...)` with a `with monkeypatch.context() as scoped:` block that encloses **only** the plugin calls (`pytest_configure` … `pytest_sessionfinish`, and the two `_append` calls). Do the file read and assertions after the block. The `PYTEST_XDIST_WORKER` `delenv` may stay test-scoped.
3. Add `tests/test_liveness_outer_stream.py` with one test (O10):
   1. Materialize the plugin with `liveness.materialize_liveness_plugin(tmp_path / "plugin")`.
   2. Run, via `subprocess.run`, `[sys.executable, "-m", "pytest", "tests/test_liveness.py", "-q", "-p", "assay_liveness_plugin", "-p", "no:cacheprovider"]` with `cwd=PROJECT_ROOT`.
   3. Use exactly this env: `{"PATH": os.environ["PATH"], "PYTHONPATH": f"{plugin_dir}{os.pathsep}{PROJECT_ROOT / 'src'}", "ASSAY_LIVENESS_EVENTS": str(events)}`. No `ASSAY_LIVENESS_EXIT` and no `ASSAY_B105_*`.
   4. Set `timeout=600` as a failsafe only.
   5. Assert:
      - the child exit is 0;
      - **exactly one** record in `events` has `event == "session_finish"`, and it is the **last** record;
      - the number of `event == "test"` records equals the number of passed tests. Parse the summary line `N passed` from stdout with a regex anchored on `passed`.

   Before the fix, the child file contains three `session_finish` records (two leaked) and two fewer `test` records.

### W1. `assay plan --baseline-from`

1. In `analysis.py`, add `MeasuredBaseline` and `measured_baseline_from_progress` exactly as in the packet. Reuse `_read`/`_json` framing, but do **not** require an expected commit (unlike `inspect_progress`).
2. In `cli.py:330-371`, add `--baseline-from` (`type=Path`, `metavar="PROGRESS"`) and `--baseline-lane` (`metavar="LANE"`) to the `plan` parser. The help text must say the figures are estimates from a named measurement, not a promise.
3. In `_cmd_plan` (after `:1799`), branch exactly as in the packet table. Keep the no-flag path byte-identical except for the added `estimate_provenance` key.
4. Resolve `commit_matches_head` with the repository HEAD that `_cmd_plan` already reads for the lane. If none is in scope, compute it with `git rev-parse HEAD` through the existing `git` module helper; do not shell out ad hoc.
5. Tests (new file `tests/test_plan_baseline_from.py`): O1–O5 and O5b. Build progress files by hand as JSONL. Never run a real campaign. O1 also covers the unsupported branch: its payload is byte-identical with and without `--baseline-from` (a valid file), and a malformed file is still refused.

### W2. Per-candidate resource, phase and startup evidence

1. Implement `TreeSample`, `tree_sample`, the refactor of `_pid_cpu_ticks` (one read → ticks + RSS pages), `RESOURCE_SIDECAR_SUFFIX`, `read_resource_sidecar` and `first_event_times` (owner-pid rule, reusing `_session_owner_pid`) in `liveness.py`.
2. Add the `sampler` seam and the `spawned_at` capture. Write the sidecar in a `finally:` in `_monitor` (or around its call in `__call__`), and add it to stale cleanup. Keep the sampler call **after** the tick's `hung`/`timeout` decisions have been computed and acted upon, so that a slow sampler cannot delay a kill decision within the same tick.
3. Wire `sampler=liveness.tree_sample` at `runner.py:4420`.
4. Extend `_MutantRun`, `_run_attempt` (three monotonic phase stamps, then the sidecar and event-time read next to `tests_completed`), `_run_one` (combination rules), the `candidate` event and the state record (`resources`).
5. Tests:
   - O6: the full 3×3 matrix in the packet's traceability row, and O7 including the second-call stale-cleanup case, both in `tests/test_liveness_runner_monitor.py` with its fake-process harness;
   - O8 in a new `tests/test_mutation_resource_evidence.py`, with one real-pytest R2 lane of two candidates, asserting field presence and types, not values. Copy the `_seed_pytest_mutation` pattern, **but build the lane with `liveness="true"`** (round-2 P0R2-1). The seed pattern uses `liveness="false"` (`test_b106_reuse_and_witness.py:686-691`), and under that setting the decision table makes `startup_seconds` null, which would contradict O8;
   - `tree_sample` against `os.getpid()`: `cpu_seconds > 0` and `rss_bytes > 0` are allowed, because the numbers are properties of a live process, not timing; plus a nonexistent pid raising `OSError`;
   - O7b, in the same new file, with a readiness handshake (round-2 P0R2-2):
     1. Start a real helper process (`sys.executable -c ...`, stdout=PIPE, stdin=PIPE) that forks a child.
     2. The child burns CPU in a loop until **its own** `/proc/self/stat` `utime + stime` reaches **at least 50 ticks**. The stop condition is CPU-based, not iteration- or wall-clock-based, so the burned CPU is always enough to discriminate. Then it exits.
     3. The helper `os.waitpid`s the child, **then** writes the line `ready\n` to stdout and flushes, **then** blocks on stdin. The helper stays alive and its child has been reaped.
     4. The test **blocks reading that `ready` line** before sampling. This synchronization point replaces any sleep. A 60 s read failsafe applies only as a hang guard.
     5. Read the helper's own `utime, stime, cutime, cstime` from `/proc/<pid>/stat` directly.
     6. **Precondition** (it makes the fixture discriminating; fail the test, not skip, if it does not hold): `cutime + cstime > utime + stime` of the helper. A live-only sum would then be strictly smaller than the reaped-inclusive sum.
     7. Assert `tree_sample(helper_pid).cpu_seconds >= (cutime + cstime) / CLK_TCK`.

     The oracle compares kernel-reported quantities; it never asserts a duration. Close stdin and `join` the helper at the end, with a 60 s failsafe only.

### W3. Forward setup/teardown durations with each baseline `test` event

1. Add `baseline_phase_durations` to `liveness.py`, factoring the owner-pid selection so there is one rule.
2. Merge `setup_s`/`teardown_s` at `mutation.py:2269-2272`.
3. Test O9: extend `tests/test_mutation_progress_budget_plan.py:212` or add a sibling. `test_liveness_proc_helpers.py:667-695` must stay green unchanged.

### W5. Source-bound campaign-scope refusals in the B105 report checker

1. Implement `--plan-json`, `check_campaign_scope` and refusals 1–9 in `tools/b105_report_check.py`. The error-to-exit mapping stays `B105_REPORT_REJECTED=… / exit 2`.
2. In `tools/self-qualification-gate.sh` `run_and_verify_lane` (`:156-214`):
   - Declare `local plan_path=".assay/plan-$lane.json"`.
   - When the lane's `expected_rigor` contains `R2`, run `"$assay_bin" plan "$lane" --file assay.toml > "$plan_path"` **before** the `"$assay_bin" run "$lane"` line. It runs in `$project` like `assay run`.
   - Fail the lane (`return 2`) if that command fails.
   - Pass `--plan-json "$plan_path"` to the checker.

   The preflight lane (R0,R1) passes no `--plan-json`.
3. In `tests/test_self_lane.py:176-200`, add:
   - substring pins for `'"$assay_bin" plan "$lane" --file assay.toml'` and `'--plan-json "$plan_path"'`;
   - an **ordering pin**: inside the text of the `run_and_verify_lane() {` … `}` function body, `body.index('"$assay_bin" plan "$lane"') < body.index('"$assay_bin" run "$lane"')`.
4. Tests (O11) in `tests/test_b105_report_check.py`:
   - build the plan dict in-test with `{"status": "ok", "shard": None, "candidate_count": n, "candidates": [{"id": ...}, ...]}`, taking IDs from the fixture's `candidate_ids`;
   - add one case per refusal 1–9 (refusal 9 at least: a list-shaped plan, a row without `id`, a bool `candidate_count`), plus one acceptance case.
5. Test O14 goes in a new file, `tests/test_b105_report_check_real_plan.py`. It uses the `git_repo`/`make_lane`/`make_r2_judge` fixtures and the `_seed_pytest_mutation` pattern from `tests/test_b106_reuse_and_witness.py:661-677`, giving a tiny R2 repo with 2–3 candidates and a real pytest suite.
   1. Run `assay plan <lane>` in-process through `main([...])` and capture its JSON.
   2. Run `assay run <lane> --verdict-json v.json` in-process.
   3. Load the checker module by path with `importlib.util.spec_from_file_location("b105_report_check", <repo>/assay/tools/b105_report_check.py)` and `module_from_spec` / `exec_module`. `tests/test_b105_report_check.py` runs the checker as a subprocess (`:24`, `:83`, `:105`) and loads nothing by path, so do not "mirror" it (round-2 P0R2-5). Call `check_campaign_scope(verdict, plan)`; it must not raise.
   4. Repeat step 2 with `--shard 0/2`. `check_campaign_scope` must raise, through refusal 5 or 7.
   5. Assert nothing about timings.

### W6. Snapshot invariant guards G1–G5 (test-only; all pass today)

All go in `tests/test_isolation_guards.py`. Build a tiny repository with two commits, following the `_git`/`_root_repo`/`_spec` helper pattern from `tests/test_isolation.py:82-160` (duplicate the helpers; do not import another test module). Use real `prepare_snapshot`, `materialize` and `materialize_replacement` with `TIMEOUT = 600.0`.

- **G1, disjoint inodes.** Open two live materializations at once (a base and a replacement). Collect `(st_dev, st_ino)` over every regular file under each snapshot's `.git/objects/**` **and** its worktree files, the seed's (`prepared._seed_git_dir / "objects"`), and the source repository's `.git/objects/**`. Assert they are pairwise disjoint. This ports P22 `test_acceptance.py:266-309` and extends it to worktree files.
- **G2, no residue crosses candidates.** Inside materialization A, write:
  - `pkg/__pycache__/m.cpython-314.pyc`;
  - `.pytest_cache/v/x`;
  - `.git/info/exclude` with `*`;
  - a `[filter "trap"] clean = false` section appended to `.git/config`.

  After A's context closes, materialization B has none of these paths or config text, and B's `git status --porcelain=v1` is empty.
- **G3, stale bytecode cannot leak.** The fixture file is `m.py` with `a = 1 == 1\nprint(a)\n`.
  1. Materialize the replacement `a = 1 != 1` (same size).
  2. Run `[sys.executable, "-c", "import m"]` in it, with `cwd` set to the snapshot root holding `m.py`. Use env `{"PATH": ...}`, without `PYTHONDONTWRITEBYTECODE` or `PYTHONPYCACHEPREFIX`, so `__pycache__/m.*.pyc` **is** written. Stdout is `False`.
  3. Then materialize the base and run the same command. Stdout is `True`.

  The mutant must be **imported**, not run as a script. CPython never caches the `__main__` script, so `[sys.executable, "m.py"]` would pass even on a tree-reuse design. That was round-1 finding P0-1.

  This pins fresh-per-candidate trees against any future reuse design. The stale-pyc reuse was reproduced on Python 3.14.7 in the analysis, through import.
- **G4, fixed metadata on the replaced file.** In a replacement materialization, the replaced file's `st_mtime == isolation._FIXED_MTIME` and its mode equals the committed mode. A committed symlink is still a symlink (`is_symlink()`).
- **G5, content proof survives stat caching.** Monkeypatch `isolation._write_worktree` with a wrapper that calls the real function, then overwrites one materialized regular file with **same-size different bytes** and restores `os.utime(path, (_FIXED_MTIME, _FIXED_MTIME))`. Entering `materialize()` must raise `AssayError` with `reason_code is ReasonCode.GIT_FAILED`, because `_verify`'s status finds the file modified. Also assert the scratch root is empty afterwards.
  - Scope note: G5's tamper happens **before** any index refresh, so it cannot detect a later `core.checkStat`/`trustctime` weakening. P5's oracle R4 and its ctime sweep (C4) own that case. G5's docstring says so.

### W7. Docs and changelog

See Docs sync below. Do this in the same branch.

---

## Oracles

Each oracle lists what proves it (**Obs**) and a plausible wrong implementation it catches (**Neg**). The gate for every oracle is the focused test file, then `tester-unified`, then `self-qualification-preflight`.

- **O1. No-flag planner output is unchanged apart from provenance.**
  - Obs: the existing pinned figures (30/15 and 60/30) still hold, the payload has `estimate_provenance == "fallback"`, and it has no `estimate_source` or `full_suite_central_*` keys.
  - Neg: always adding `estimate_source: null` or changing the fallback. The key-set assertion catches both.
- **O2. `auto` measured estimate uses the auto-budget function.**
  - Obs: with `baseline_s = 100.0`, `estimated_serial_seconds == count × 300.0` and `full_suite_central_serial_seconds == count × 100.0`. The same fixture with an explicit `"45s"` budget gives `count × 45.0`. `"none"` gives `null`.
  - Neg: using `baseline_s` for the upper bound.
- **O3. Latest segment of the named lane.**
  - Obs: picks the 100 s segment of lane L; `run_line` points at its `run` line; `--baseline-lane` selects the other lane's segment.
  - Neg: first segment, or ignoring `lane`.
- **O4. `command_finished` fallback.**
  - Obs: 40.0 s from the last PASS `baseline` record; `source_event == "command_finished"`.
  - Neg: using a FAIL record or `started` of one and `ended` of another.
- **O5. Refusals are refusals.**
  - Obs:
    - Each of refusals 1–8 gives exit 2 with the message naming `--baseline-from` and that refusal's reason.
    - A file whose only defect is a torn final line (no trailing newline) is **accepted**.
    - `--baseline-lane` alone is refused.
  - Neg: a silent fallback to the 60 s figure; skipping instead of refusing a `plan` with `baseline_s: 0`; refusing a torn tail.
- **O5b. Provenance fields are real.**
  - Obs:
    - `estimate_source.sha256` equals `hashlib.sha256(path.read_bytes()).hexdigest()`.
    - `commit_matches_head` is `false` for a `run` header naming another commit, and also for a `null` commit.
    - It is `true` only when the header's commit equals HEAD.
  - Neg: hashing the path string; hard-coding `true`.
- **O6. The sampler never changes classification.**
  - Obs: the full 3×3 matrix. For each of {normal exit, hung, busy→timeout}, an identical scripted fake-process run (same `monotonic` ticks, same `cpu_reader` values) yields the **same** outcome and the same raised exception type under all three samplers: `sampler=None`; a sampler always returning `TreeSample(1e9, 10**12)`; a sampler raising `OSError` every tick.
  - Neg:
    - Routing sampler CPU into `cpu_growing` flips the busy→timeout row under the constant sampler.
    - Calling the sampler before the tick's classification, and letting a raise escape, changes the raising-sampler row.
  - **Ordering spy (round-2 P0R2-6).** Once exceptions are swallowed, "sampler after classification" is otherwise unobservable, so add a fourth run.
    - Wrap the fake `cpu_reader` so it records the tick index of each call.
    - Pass a spy `sampler` that asserts, on every call, that `cpu_reader` has **already** been called for the current tick, and appends `True` to a list when so.
    - The assertion failures are collected in a list, not raised, so the swallow cannot hide them.
    - The test asserts the list is non-empty and all `True`.
    - Neg: calling the sampler before `cpu_reader` in a tick.
- **O7. The sidecar exists on every exit path.**
  - Obs:
    - After normal exit, `LivenessHungExpired` and `TimeoutExpired`, `read_resource_sidecar(events_path)` returns a dict with exactly `{"format", "samples", "cpu_seconds", "peak_rss_bytes", "spawned_at"}`.
    - `samples` equals the number of sampler calls that returned.
    - **Stale removal:** after one completed call, a second `__call__` for the same cwd whose injected `popen` raises leaves **no** sidecar. The first call's file was removed by the stale cleanup before `popen`.
  - Neg: writing only on normal exit; skipping the stale cleanup. Without the raising second call, the next write would simply overwrite the stale file and hide that defect.
- **O7b. Reaped-descendant CPU is counted.** See W2 step 5. The sample is at least the helper's own kernel-reported `cutime+cstime`.
  - Neg: a live-only utime+stime sum.
- **O8. End-to-end wiring.**
  - Obs:
    - In a real-pytest R2 run through `runner.run_lane`, on a lane built with `liveness="true"`, each `candidate` event has the keys `cpu_seconds` (`float ≥ 0` or `null`) and `peak_rss_bytes` (`int > 0` or `null`). Both are nullable per the last bullet below.
    - It has `phase_seconds` with exactly the keys `{"materialize", "command", "integrity", "teardown"}`, all floats ≥ 0.
    - It has `startup_seconds` with exactly `{"to_session_start", "to_first_test"}`.
    - Each state record has the same values under `resources`.
    - A resumed second run over the same state dir still classifies identically.
    - Timing *values* are never asserted, and `samples ≥ 1` is **not** required, because the number of 1 Hz ticks depends on the host. So `cpu_seconds`/`peak_rss_bytes` may legitimately be `null` for a sub-second candidate: assert type `float | None` / `int | None`, plus the key presence.
  - Neg: forgetting the runner wiring, where every candidate event lacks the keys entirely; resume rejecting the new key.
- **O9. Baseline phase forwarding.**
  - Obs:
    - The forwarded `test` events carry `setup_s`/`teardown_s` equal to the owner's records, paired by the **nearest preceding unconsumed setup** and the **next teardown**.
    - For the duplicated nodeid, the first and second `test` events carry their own call's values, not a sum.
    - The fixture also has an earlier duplicate whose setup record has **no** call (a setup error). That setup is never paired, and the later call gets its own setup's duration (round-2 P0R2-4).
    - A foreign-pid `phase` record is ignored.
    - The number of `test` events is unchanged, and there is no new event name.
  - Neg: emitting `phase` records as events; summing per nodeid.
- **O10. The outer liveness stream stays intact.**
  - Obs: see W4 step 3.
  - Neg: fixing only `:581` → a second `session_finish` remains → fails.
- **O11. The B105 checker refuses shards and partial or foreign inventories.**
  - Obs:
    - Refusals 1–9 each give `B105_REPORT_REJECTED` and exit 2. That includes the structural cases: a list-shaped plan, a row without `id`, and a bool `candidate_count`. None of them escapes as a traceback with exit 1.
    - The exact-match plan is accepted.
    - A preflight (R0,R1) invocation with `--plan-json` is refused (refusal 8).
  - Neg: a count-only comparison accepts a same-size foreign ID set, which the set-equality case catches; never reading `status` fails the refusal-2 case.
- **O12. The gate script produces the plan in the right place and order.**
  - Obs: the substring pins in `tests/test_self_lane.py`, plus the ordering pin (`plan` before `run` inside `run_and_verify_lane`).
  - Neg: dropping the `plan` step; generating it after `run`.
- **O13. Snapshot guards G1–G5 pass on today's code.**
  - Obs: each guard is green. Each names in its docstring the P5 shortcut it forbids:
    - G1: hardlinked packs;
    - G2: residue across candidates;
    - G3: tree reuse with stale bytecode, via `import m`;
    - G4: replaced-file metadata;
    - G5: skipping the hash pass, where `checkStat` weakening is P5's R4.
  - Neg: controlled breaks run locally, never committed:
    - temporarily replacing `shutil.copyfile` with `os.link` in `_copy_objects` must make G1 fail;
    - temporarily making G3 reuse the replacement's directory for the base run (a test-local simulation, no source change) must make G3 print `False` twice and fail.
- **O14. Real plan agrees with the real run.** See W5 step 5.
  - Obs: an unsharded real verdict passes `check_campaign_scope` against the real plan; the `--shard 0/2` verdict is refused.
  - Neg: planner and runner deriving candidate IDs differently; accepting a shard.

### What an oracle must NOT contain (AUTHORING.md §3b, verbatim)

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

---

## Docs sync

The docs change in this same branch.

- **`docs/CONSUMERS.md:2307-2312`.** Replace the incorrect "declaration-derived upper bound … Treat them as 'no longer than'" text:
  - The fallback figure is a **placeholder**, not a bound. For `auto` it is a *lower* bound on the auto ceiling, because `auto ≥ 60 s`.
  - Add a pasteable `assay plan <lane> --baseline-from .assay/progress-<lane>.jsonl` example and explain `estimate_provenance`, `estimate_source` and `full_suite_central_*`.
- **`docs/CONSUMERS.md:2609-2614` (progress-event table):**
  - the `test` row gains `setup_s` and `teardown_s`;
  - the `candidate` row gains `cpu_seconds`, `peak_rss_bytes`, `phase_seconds` and `startup_seconds`, each `null` when unmeasured and never used for classification.
  - Near `:2630`, add one sentence on the state record's optional `resources` key.
- **`docs/DESIGN-GUIDE.md`, liveness/evidence section (around `:470-500`).** Add one paragraph: resource and phase samples are diagnostic evidence for B107/B110 planning; the sampler is separate from the classification input by construction (A-464).
- **`README.md`, B105 bullet (`:955-975`).** Add one sentence: the B105 gate now binds the report's candidate inventory to a source-bound `assay plan` and refuses sharded or partial reports.
- **`docs/DESIGN-GUIDE.md` §"Full-source self-qualification (B105)" (`:1934`).** Add the same sentence and cite A-474.
- **`CHANGES.md` `## [Unreleased]`:**
  - `### Added`: `--baseline-from`, resource and phase fields, forwarded setup/teardown durations;
  - `### Fixed`: the liveness test leak, the CONSUMERS upper-bound claim;
  - `### Testing`: G1–G5.

Keep `tests/test_docs_examples_and_vocabulary.py` green. Every fenced example must be marked or executable exactly as that test requires. Read its TOML-example rules at `:72-245` before adding one; `:659-699` are the anchor-link tests. Document in CONSUMERS that `cpu_seconds` and `peak_rss_bytes` are 1 Hz lower bounds, and that `cpu_seconds` includes reaped descendants through their live ancestors' `cutime`/`cstime`.

## Scope / forbid

**Touch only these files:**
- `src/assay/{analysis,cli,liveness,mutation,runner}.py`;
- `tools/b105_report_check.py`, `tools/self-qualification-gate.sh`;
- `tests/test_liveness.py`, `tests/test_liveness_runner_monitor.py`, `tests/test_mutation_progress_budget_plan.py`, `tests/test_b105_report_check.py`, `tests/test_self_lane.py`;
- the new test files named above;
- `README.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `CHANGES.md`.

**Forbidden:**
- the verdict schema or `verdict.py`/`verify.py`;
- `MUTATION_STATE_SCHEMA_VERSION`, `ReasonCode`, `judge_sha256` inputs;
- `assay.toml` and `run-gate.toml`;
- `isolation.py` (W6 is test-only);
- any classification rule;
- `nyxloom-trove/4-backlog.md` and `decisions.md` (the controller owns them).

**B105 coverage floor:** every new line and branch in `src/assay` must be covered by the default suite. Adding `pragma: no cover` is forbidden, and `tests/fixtures/b105-coverage-exclusions.json` must not change.

**Exclusion-line trap** (round-1 P0-2). That fixture pins exact line numbers, verified at `db85f747`:
- `src/assay/liveness.py` lines **93, 94** (the `TYPE_CHECKING` block);
- `src/assay/mutation.py` lines **148, 155, 156**.

So **no line may be added to or removed from `liveness.py` above line 93, or `mutation.py` above line 148**. New imports go:
- onto an **existing** import line (e.g. extend `from typing import (...)` inside its existing parenthesized lines without adding a line, or add the name to an existing line within the parentheses), or
- **below** the `TYPE_CHECKING` block.

Before committing, run `grep -n "pragma: no cover" src/assay/liveness.py src/assay/mutation.py` and confirm the hits are still exactly those lines. A shift is a BLOCKED trigger, because the fixture is forbidden to this package.

Any need outside this list is a BLOCKED trigger.

## Gate

**Host-load rule (plan §0, verbatim):**

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. **Focused tests, serially**, after each work item:
   `cd <worktree>/assay && nice -n 19 ionice -c3 python -m pytest tests/test_liveness.py tests/test_liveness_outer_stream.py tests/test_plan_baseline_from.py tests/test_liveness_runner_monitor.py tests/test_mutation_resource_evidence.py tests/test_mutation_progress_budget_plan.py tests/test_liveness_proc_helpers.py tests/test_b105_report_check.py tests/test_b105_report_check_real_plan.py tests/test_self_lane.py tests/test_isolation_guards.py -q -p no:cacheprovider`
2. **Registered gate** (worktree under `/workspaces/vbpub`):
   `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b110-p0-tester-unified.log 2>&1; echo "exit=$?"`.
   Then, **in a separate step**:
   `grep -E 'ASSAY_GATE_CONTAINER_EXIT=|ASSAY_REGISTERED_GATE_COMPLETE=' /tmp/b110-p0-tester-unified.log`.
   Both must show `0` and `1` respectively. Never read them through a pipe tail of the gate command.
3. **B105 preflight.** Source and collected tests changed, so R1 must stay 100%:
   `cd <worktree>/assay && python ./run-gate.py self-qualification-preflight > /tmp/b110-p0-preflight.log 2>&1; echo "exit=$?"`.
   Read its `B105_VERIFIED_LANE=self-qualification-preflight` marker in a separate step. This runs only after step 2 has finished, never concurrently.

**Never run the `self-qualification` lane.**

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP. Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B110-P0-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

A product gap, meaning a name, a contract or a user-facing choice this brief does not settle, is not a BLOCKED. Record it as `D-<NNN>: <question>` in the same report and continue with the rest. Example: you believe `"declared-baseline"` misnames what was measured.

## Report

Write `nyxloom-trove/reports/assay-B110-P0-REPORT.md` and return:
- the branch head commit and one commit per work item, each with trailer `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`;
- the files touched, from `git diff --stat <base>..HEAD`;
- the traceability table above, repeated with the **actual** test node IDs and, for every controlled break you ran, how many tests failed;
- the gate log paths, with `ASSAY_GATE_CONTAINER_EXIT`, `ASSAY_REGISTERED_GATE_COMPLETE` and the preflight marker read in a separate step;
- residuals: anything deferred, every `D-` entry, and any line anchor in this brief that had drifted.

**Checkpointing:** ARM at ~120k context or ~60 tool calls. CUT at a green, committed work-item boundary: write a continuation brief plus a retention prompt into the report, then return.
