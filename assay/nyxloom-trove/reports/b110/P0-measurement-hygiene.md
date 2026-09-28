# B110-P0 — Measurement evidence and campaign hygiene

| Field | Value |
|---|---|
| Backlog | **B111** (split from B110) |
| Branch | `assay-b110-p0-measure`, cut from the integration line `assay-b105-evidence-integrity` after the plan §11.1 reconciliation |
| Depends on | nothing |
| Unblocks | P1 (needs the `tests/test_liveness.py` leak fix), P4 and P7 (carry the new candidate fields), P5 (needs the G1–G5 guards), P3d (extends the report-checker refusals) |
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
   - `src/assay/liveness.py:529-544`: `_pid_cpu_ticks`, which reads `/proc/<pid>/stat`.
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
   - `src/assay/liveness.py:1020-1057`: how `session_finish` is read.
   - `src/assay/liveness.py:1475-1476` and `:1516-1519`: the 30 s post-finish `hung` branch.
7. **W5, report checker:**
   - `tools/b105_report_check.py:14-147`, the whole file.
   - `tools/self-qualification-gate.sh:175-215`: `run_and_verify_lane`.
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
    commit: str          # the selected run's commit
    baseline_s: float    # > 0, finite
    source_event: str    # "plan" | "command_finished"

def measured_baseline_from_progress(path: Path, *, lane: str) -> MeasuredBaseline: ...
```

- **Selection:** the **last** `run` segment in the file whose `run.lane == lane`.
- **Measurement, first match wins:**
  1. That segment's `plan` event with a finite `baseline_s > 0`. `source_event = "plan"`.
  2. Otherwise, the segment's **last** `command_finished` event with `phase` in `{"baseline", "direct"}` and `outcome == "PASS"`. It gives `baseline_s = (ended − started)` in seconds, parsed as ISO-8601 UTC. `source_event = "command_finished"`.

**Refusals.** Each raises `LaneConfigError(f"--baseline-from {path}: <reason>")` and exits ERROR/BAD_LANE_CONFIG:
- the file is unreadable;
- a line is malformed JSON;
- an event precedes the first `run`;
- no `run` has `lane == lane`;
- the selected segment has no measurement;
- the measurement is non-finite or ≤ 0.

**CLI:**
- `assay plan <lane> --baseline-from PATH [--baseline-lane NAME]`.
- `--baseline-lane` defaults to `<lane>`. B105 uses `--baseline-lane self-qualification-preflight`, because the preflight lane's progress carries the same declared command.
- `--baseline-lane` without `--baseline-from` is refused (`LaneConfigError`).

**Payload rules:**
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

**RSS** is field 24 of `/proc/<pid>/stat`, i.e. `fields[21]` after the `raw[close_paren + 2:].split()` idiom at `liveness.py:538-540`, times `os.sysconf("SC_PAGE_SIZE")`. Refactor `_pid_cpu_ticks` so one read yields both ticks and RSS pages; `tree_cpu_seconds` must keep its exact behavior.

**`LivenessRunner.__init__`** gains `sampler: Callable[[int], TreeSample] | None = None`. It does not change `cpu_reader`: the 17 existing `cpu_reader=` injections stay byte-identical. When `sampler` is not `None`, `_monitor` calls it once per poll tick, **after** the classification inputs for that tick are computed:
- A sampler exception is swallowed; that tick contributes no sample.
- The sampler value **never** enters `cpu_growing`, `hung` or `timeout`.

**Monitor bookkeeping and sidecar.** `_monitor` keeps `samples` (count), `last_cpu_seconds` and `peak_rss_bytes`. On **every** exit path it writes `events_path.with_suffix(RESOURCE_SIDECAR_SUFFIX)` atomically (tmp + `os.replace`) with exactly:

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
phase_seconds: Mapping[str, float] | None = None    # {"materialize": s, "command": s, "integrity": s}
startup_seconds: Mapping[str, float | None] | None = None  # {"to_session_start": s|None, "to_first_test": s|None}
```

**`_run_attempt` measures `phase_seconds`** with `time.monotonic()` in the worker:
- `materialize` runs from `started_monotonic` (`:2657`) to entry into the snapshot `with` body;
- `command` covers the `execute_plan(...)` call;
- `integrity` covers `_snapshot_left_dirt(...)`.

It measures these even when liveness is off. When `liveness_events_dir is not None`, it reads the sidecar and `first_event_times` from the same `candidate_events_path(...)` key that the `tests_completed` read uses (`:2742-2749`). Then:
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

**W3, baseline setup/teardown durations.** A new helper, `liveness.baseline_phase_durations(path: Path | None) -> dict[str, dict[str, float | None]]`:
- It maps each nodeid to `{"setup_s": Σ setup durations, "teardown_s": Σ teardown durations}` over the `phase` records.
- It applies the same owner-pid rule as `_selected_test_events`, so do not write a second rule; factor the owner selection.
- A missing or ill-typed duration contributes nothing. A key with no well-typed record is `None`.

At `mutation.py:2269-2272`, each forwarded baseline `test` event becomes `{"event": "test", "phase": "baseline", **test_event, "setup_s": ..., "teardown_s": ...}`. `baseline_test_events` itself stays the exact triple. No new event name.

**W5, report checker.** `tools/b105_report_check.py` gains `--plan-json PATH`. It is **required iff `"R2"` is in `--expected-rigor`**, and refused otherwise. It is **source-bound**: the gate script produces it by running the *installed exact wheel's* `assay plan "$lane" --file assay.toml` inside the exact-OID source clone, just before `assay run`. `verify_report_document` gains the keyword `plan: dict | None`.

**Refusals** (each a `ValueError` whose message names the check):
1. `plan` is missing while R2 is expected.
2. `plan["status"] != "ok"`.
3. `plan["shard"] is not None`.
4. `plan["candidate_count"] != len(plan["candidates"])`.
5. `document["judgment"]["r2"]` contains a non-null `shard_index` or `shard_count`.
6. The R2 claim's `mutation.candidate_ids` is missing, not a list, contains a non-64-hex string, or contains duplicates.
7. `set(candidate_ids) != {row["id"] for row in plan["candidates"]}`, or `len(candidate_ids) != plan["candidate_count"]`.

**W6, snapshot guards.** New file `tests/test_isolation_guards.py`. These are **test-only**; no source change. All five pass on today's code.

### Required flow (W2, the only multi-step flow)

1. `__call__` does the stale cleanup (now including the sidecar), sets `spawned_at = time.time()`, then calls `popen`.
2. `_monitor` loops. Each tick:
   1. Classification exactly as today.
   2. If `self._sampler`: `try: s = self._sampler(proc.pid)`. Update `samples`, `last_cpu_seconds = s.cpu_seconds` and `peak_rss_bytes = max(...)`. `except Exception: pass`.
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
| Liveness lane, normal exit | last sample | max sample | measured | from events | none |
| `hung` / `budget_exceeded` (killed) | last sample before kill | max | measured | from events or `null` | none; bucket decided exactly as today |
| Sidecar unreadable or malformed | `null` | `null` | measured | from events | none |
| Resumed candidate (state record reused) | not re-measured; no new `candidate` event, as today | | | | none |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| W1 fallback unchanged | cli.py | O1 | existing `_write_plan_fixture` tests | drop the `estimate_provenance` key → O1 fails; change 60.0 → pinned tests fail |
| W1 measured, `auto` | cli.py + analysis.py | O2 | progress JSONL with a `run` (lane L) + `plan.baseline_s = 100.0`, lane `budget_per_candidate = "auto"`, 1 candidate | compute `count × baseline_s` instead of `auto_budget(...)` → O2 fails (expects 300.0 for b=100: max(300,160)) |
| W1 selection | analysis.py | O3 | two `run` segments for L (first 50 s, second 100 s) + one for another lane (999 s) | pick first segment or ignore lane → O3 fails |
| W1 command_finished fallback | analysis.py | O4 | segment without `plan`, with `command_finished` phase `baseline` PASS `started`/`ended` 40 s apart, plus a FAIL one | accept FAIL or first instead of last → O4 fails |
| W1 refusals | analysis.py | O5 | 6 malformed files (table above) | silently return fallback → O5 fails |
| W2 sampler never classifies | liveness.py | O6 | `LivenessRunner` with fake `popen`/`monotonic`/`cpu_reader` (pattern: `tests/test_liveness_runner_monitor.py`), sampler returning huge RSS/CPU or raising | feed sampler CPU into `cpu_growing` → O6 fails |
| W2 sidecar on every exit | liveness.py | O7 | same harness, three runs: normal exit, hung, timeout | write only on normal exit → O7 fails |
| W2 wiring | mutation.py/runner.py | O8 | real-pytest R2 lane (`tests/test_b106_reuse_and_witness.py:661-677` seed pattern) | forget `sampler=` at runner.py:4420 → `cpu_seconds` is `null` → O8 fails |
| W3 forwarding | liveness.py/mutation.py | O9 | baseline events file with setup/call/teardown for two nodeids (+ a foreign-pid record) | forward setup as a `test` event, or ignore pid rule → O9 fails |
| W4 leak | tests/test_liveness.py | O10 | nested pytest over `tests/test_liveness.py` with a live events file | revert one `monkeypatch.context()` → O10 fails |
| W5 refusals | tools/b105_report_check.py | O11 | `_verifier_valid_report` + generated plan JSON | skip the set-equality check → O11 fails |
| W5 wiring | gate script | O12 | `tests/test_self_lane.py` substring pins | omit `--plan-json` → O12 fails |
| W6 guards | tests only | O13 | tiny repo, 2 commits | (future P5 hardlink / tree reuse / checkStat) → O13 fails |

### Degrees of freedom

Private helper names and the decomposition inside `liveness.py`, `analysis.py` and the checker are yours. So is whether `tree_sample` and `tree_cpu_seconds` share a private walker, provided `tree_cpu_seconds`'s behavior and exceptions are unchanged. Every key name, JSON shape, refusal and wiring point above is fixed.

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
5. Tests (new file `tests/test_plan_baseline_from.py`): O1–O5. Build progress files by hand as JSONL. Never run a real campaign.

### W2. Per-candidate resource, phase and startup evidence

1. Implement `TreeSample`, `tree_sample`, the refactor of `_pid_cpu_ticks` (one read → ticks + RSS pages), `RESOURCE_SIDECAR_SUFFIX`, `read_resource_sidecar` and `first_event_times` (owner-pid rule, reusing `_session_owner_pid`) in `liveness.py`.
2. Add the `sampler` seam and the `spawned_at` capture. Write the sidecar in a `finally:` in `_monitor` (or around its call in `__call__`), and add it to stale cleanup. Keep the sampler call **after** the tick's `hung`/`timeout` decisions have been computed and acted upon, so that a slow sampler cannot delay a kill decision within the same tick.
3. Wire `sampler=liveness.tree_sample` at `runner.py:4420`.
4. Extend `_MutantRun`, `_run_attempt` (three monotonic phase stamps, then the sidecar and event-time read next to `tests_completed`), `_run_one` (combination rules), the `candidate` event and the state record (`resources`).
5. Tests:
   - O6 and O7 in `tests/test_liveness_runner_monitor.py`, reusing its fake-process harness;
   - O8 in a new `tests/test_mutation_resource_evidence.py`, with one real-pytest R2 lane of two candidates (copy the `_seed_pytest_mutation` pattern), asserting field presence and types, not values;
   - `tree_sample` against `os.getpid()`: `cpu_seconds > 0` and `rss_bytes > 0` are allowed, because the numbers are properties of a live process, not timing; plus a nonexistent pid raising `OSError`.

### W3. Forward setup/teardown durations with each baseline `test` event

1. Add `baseline_phase_durations` to `liveness.py`, factoring the owner-pid selection so there is one rule.
2. Merge `setup_s`/`teardown_s` at `mutation.py:2269-2272`.
3. Test O9: extend `tests/test_mutation_progress_budget_plan.py:212` or add a sibling. `test_liveness_proc_helpers.py:667-695` must stay green unchanged.

### W5. Source-bound campaign-scope refusals in the B105 report checker

1. Implement `--plan-json` and the refusals in `tools/b105_report_check.py`. The error-to-exit mapping stays `B105_REPORT_REJECTED=… / exit 2`.
2. In `tools/self-qualification-gate.sh` `run_and_verify_lane`:
   - when the lane's `expected_rigor` contains `R2`, run `"$assay_bin" plan "$lane" --file assay.toml > "$plan_path"` **before** `assay run`, with `local plan_path=".assay/plan-$lane.json"`;
   - fail the lane (`return 2`) if that command fails;
   - pass `--plan-json "$plan_path"` to the checker.

   The preflight lane (R0,R1) passes no `--plan-json`.
3. In `tests/test_self_lane.py:176-200`, add pins for `'"$assay_bin" plan "$lane" --file assay.toml'` and `'--plan-json "$plan_path"'`.
4. Tests (O11) in `tests/test_b105_report_check.py`:
   - build the plan dict in-test with `{"status": "ok", "shard": None, "candidate_count": n, "candidates": [{"id": ...}, ...]}`, taking IDs from the fixture's `candidate_ids`;
   - add one case per refusal 1–7, plus one acceptance case.

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
  1. Materialize the replacement `a = 1 != 1` (same size). Run `[sys.executable, "m.py"]` in it with env `{"PATH": ...}` and without `PYTHONDONTWRITEBYTECODE`, so a `.pyc` may be written. Stdout is `False`.
  2. Then materialize the base and run the same command. Stdout is `True`.

  This pins fresh-per-candidate trees against any future reuse design. The stale-pyc reuse was reproduced on Python 3.14.7 in the analysis.
- **G4, fixed metadata on the replaced file.** In a replacement materialization, the replaced file's `st_mtime == isolation._FIXED_MTIME` and its mode equals the committed mode. A committed symlink is still a symlink (`is_symlink()`).
- **G5, content proof survives stat caching.** Monkeypatch `isolation._write_worktree` with a wrapper that calls the real function, then overwrites one materialized regular file with **same-size different bytes** and restores `os.utime(path, (_FIXED_MTIME, _FIXED_MTIME))`. Entering `materialize()` must raise `AssayError` with `reason_code is ReasonCode.GIT_FAILED`, because `_verify`'s status finds the file modified. Also assert the scratch root is empty afterwards.

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
  - Obs: each malformed input gives exit 2 with the message naming `--baseline-from` and the reason. `--baseline-lane` alone is refused.
  - Neg: a silent fallback to the 60 s figure.
- **O6. The sampler never changes classification.**
  - Obs: an identical scripted fake-process run (same `monotonic` ticks, same `cpu_reader` values) yields the **same** outcome, and the same raised exception type, with `sampler=None`, with a sampler returning `TreeSample(1e9, 10**12)`, and with a sampler raising `OSError` every tick.
  - Neg: routing sampler CPU into `cpu_growing`.
- **O7. The sidecar exists on every exit path.**
  - Obs: after normal exit, `LivenessHungExpired` and `TimeoutExpired`, `read_resource_sidecar(events_path)` returns a dict with exactly `{"format", "samples", "cpu_seconds", "peak_rss_bytes", "spawned_at"}`; `samples` equals the number of sampler calls that returned. A second call to the same cwd removes the stale sidecar first.
  - Neg: writing only on normal exit.
- **O8. End-to-end wiring.**
  - Obs: in a real-pytest R2 run through `runner.run_lane`, each `candidate` event has `cpu_seconds` (float ≥ 0), `peak_rss_bytes` (int > 0), `phase_seconds` with exactly the keys `{"materialize", "command", "integrity"}` (floats ≥ 0), and `startup_seconds` with exactly `{"to_session_start", "to_first_test"}`. Each state record has the same values under `resources`. A resumed second run over the same state dir still classifies identically.
  - Neg: forgetting the runner wiring; resume rejecting the new key.
- **O9. Baseline phase forwarding.**
  - Obs: the forwarded `test` events carry `setup_s`/`teardown_s` equal to the sums of the owner's records; a foreign-pid `phase` record is ignored; the number of `test` events is unchanged; there is no new event name.
  - Neg: emitting `phase` records as events.
- **O10. The outer liveness stream stays intact.**
  - Obs: see W4 step 3.
  - Neg: fixing only `:581` → a second `session_finish` remains → fails.
- **O11. The B105 checker refuses shards and partial or foreign inventories.**
  - Obs: refusals 1–7 each give `B105_REPORT_REJECTED` and exit 2; the exact-match plan is accepted; a preflight (R0,R1) invocation with `--plan-json` is refused.
  - Neg: a count-only comparison accepts a same-size foreign ID set → the set-equality case fails.
- **O12. The gate script produces the source-bound plan.**
  - Obs: the substring pins in `tests/test_self_lane.py`.
  - Neg: dropping the `plan` step.
- **O13. Snapshot guards G1–G5 pass on today's code.**
  - Obs: each guard is green; each names in its docstring the P5 shortcut it forbids (hardlinked packs, tree reuse, `core.checkStat=minimal`/`trustctime=false`, skipping the hash pass).
  - Neg: temporarily replacing `shutil.copyfile` with `os.link` in `_copy_objects` must make G1 fail. Run this controlled break locally and do not commit it.

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

Keep `tests/test_docs_examples_and_vocabulary.py` green. Every fenced example must be marked or executable exactly as that test requires; read its `:659-699` before adding one.

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
   `cd <worktree>/assay && nice -n 19 ionice -c3 python -m pytest tests/test_liveness.py tests/test_liveness_outer_stream.py tests/test_plan_baseline_from.py tests/test_liveness_runner_monitor.py tests/test_mutation_resource_evidence.py tests/test_mutation_progress_budget_plan.py tests/test_liveness_proc_helpers.py tests/test_b105_report_check.py tests/test_self_lane.py tests/test_isolation_guards.py -q -p no:cacheprovider`
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
