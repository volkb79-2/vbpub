# REVIEW-W8: Wave A W8 (B111) measurement evidence and campaign hygiene

- **Reviewer:** fresh session, adversarial. Read-only on the repo. No gate or container was started.
- **Branch:** `wave-a-w8-measurement` (`cd110f2c`); code head `2d3a0e58`; base `bdd9cde7` (W7 head).
- **Scope reviewed:** `git diff bdd9cde7..wave-a-w8-measurement` (31 files). Checked against:
  - the brief `W8-measurement.md`;
  - CD1, CD4, CD18, CD25, CD34–CD38 (amended), CD41, CD42, CD44–CD46;
  - pre-dispatch review §1 (W8-1 to W8-14);
  - the imported P0 sections;
  - `W8-LOG.md` deviations 1–13.
- All paths below are relative to `assay/` in the worktree unless given in full.

## Evidence I produced (scratch: `scratchpad/w8review/`)

| Check | Result |
|---|---|
| **One** full `tests` run: `nice -n 19 ionice -c3 python -m pytest tests -q -p no:cacheprovider --cov=src/assay --cov-branch --cov-report=json:…` (`COVERAGE_FILE` in scratch, so no `.coverage` was written to the worktree) | 5478 passed, 1 skipped, 1 failed. The failure is the known B134 `test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused`. Coverage is 100% line+branch on every file except `src/assay/git.py` 459 and arc 458→459 (B134). |
| Excluded lines | Only `liveness.py [94, 95]` and `mutation.py [148, 155, 156]`, plus the pre-existing config/git/mutation_witness pairs. `grep pragma` gives `liveness.py:94` and `mutation.py:148` only. Line 95 and lines 155–156 are unchanged. No new pragma or `noqa` in the diff. |
| `analysis/tests`, coverage on `analysis/src/assay_analysis` | 284 passed. All four modules are 100% line+branch with no exclusions. |
| Focused judge files: resource evidence, monitor, proc helpers, hint, guards, progress/budget/plan, outer stream, `test_liveness.py` | 236 passed. `test_liveness_proc_helpers.py` passes unmodified. |
| Focused gate files: `test_b105_report_check*.py`, `test_self_lane.py`, `test_dependency_purity.py` | 118 passed |
| Worktree residue after all runs | `git status --short --ignored assay` shows `__pycache__/` only |
| Loop-body identity | Base `_monitor` body vs new `_poll` body: byte-identical except the 9 added sampler lines. See item 2. |
| `_walk_tree` vs base `tree_cpu_seconds` body | Identical except that `total_ticks += _pid_cpu_ticks(pid)` became `values.append(read(pid))` |
| R2 mutants on W8-added judge lines | 36 sites across the 4 operators of `[lanes.self-qualification.judge.mutation]`. 34 are killed. **2 survive every judge test that runs a liveness candidate.** See W8R-1. |

## Findings

### W8R-1 MAJOR: the `startup_seconds` arithmetic is never checked by value; two R2 mutants survive, and a wrong implementation passes the whole suite

**Where:** `src/assay/mutation.py` `_measured_resources` (def at :1925). Tests: `tests/core/test_mutation_resource_evidence.py`.

**Evidence:**
- `mutation.py:1941`: `None if session_start is None or spawned_at is None else session_start - spawned_at`. `:1944` is the same expression for `first_test`.
- The only test that reads `startup_seconds` is O8 (`test_mutation_resource_evidence.py:344-346`). It accepts `None` for both values.
- `grep -rl "startup_seconds\|_measured_resources\|to_session_start" tests analysis/tests gate/tests` returns only that file.
- I applied the self-qualification operators to the W8-added lines in a scratch export.
  - `python:compare-swap` `spawned_at is None` → `spawned_at is not None` **survives** at `:1941` and at `:1944`.
  - I ran it against every judge test file that runs a liveness candidate: `test_b105_config_boundaries.py`, `test_cli_run.py`, `test_config_ingested_mutation.py`, `test_config_mutation.py`, `test_mutation_resource_evidence.py`, `test_mutation_progress_budget_plan.py` and `test_liveness.py`. Result: 312 passed.
  - The four sibling mutants (`session_start is not None`, and `or`→`and`) are killed only by accident: a `TypeError` crash in `test_cli_run.py::test_run_liveness_does_not_turn_a_configure_time_raise_into_a_false_survivor`.
- `[lanes.self-qualification]` runs R2 `whole_target` over `src/assay/mutation.py` with the default `fail_under` of 100 (`mutation.py:4083`). So the next full B105 qualification reports `MUTANTS_SURVIVED`.
- Independently of R2: an implementation that always returns `None`, or that swaps the operands and so gives negative gaps, passes every test.

**Fix (exact):**

1. In `tests/core/test_mutation_resource_evidence.py:22`, replace `from assay import liveness, runner` with `from assay import liveness, mutation, runner`.
2. In the same file, insert this block after `test_first_event_times_rejects_a_t_that_is_not_a_finite_number` (it ends at :179) and before the `# tree_sample (O7b)` banner:

```python
# --------------------------------------------------------------------------
# _measured_resources: the startup gaps are exact differences of recorded
# stamps (hand-written sidecar and events, no clock)
# --------------------------------------------------------------------------


def _startup_fixture(
    tmp_path: Path, *, sidecar: dict[str, Any] | None, events: list[dict[str, Any]] | None
) -> Path:
    events_path = tmp_path / "candidate.ndjson"
    if sidecar is not None:
        events_path.with_suffix(liveness.RESOURCE_SIDECAR_SUFFIX).write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
    if events is not None:
        _write(events_path, events)
    return events_path


_STAMPED_EVENTS = [
    {"event": "session_start", "t": 100.25, "pid": 9},
    {"event": "test", "t": 101.5, "pid": 9, "nodeid": "a"},
]


def test_measured_resources_are_the_sidecar_values_and_the_stamp_differences(tmp_path: Path) -> None:
    path = _startup_fixture(tmp_path, sidecar={**_SIDECAR, "spawned_at": 100.0}, events=_STAMPED_EVENTS)
    assert mutation._measured_resources(path) == (
        1.5,
        1024,
        {"to_session_start": 0.25, "to_first_test": 1.5},
    )


@pytest.mark.parametrize(
    ("sidecar", "events"),
    [
        (None, _STAMPED_EVENTS),  # no sidecar: no spawn stamp
        ({**_SIDECAR, "spawned_at": 100.0}, None),  # no events file: no session or test stamp
        ({**_SIDECAR, "spawned_at": True}, _STAMPED_EVENTS),  # an ill-typed spawn stamp
    ],
)
def test_measured_resources_startup_gaps_are_none_when_an_operand_is_missing(
    tmp_path: Path, sidecar: dict[str, Any] | None, events: list[dict[str, Any]] | None
) -> None:
    _cpu, _rss, startup = mutation._measured_resources(
        _startup_fixture(tmp_path, sidecar=sidecar, events=events)
    )
    assert startup == {"to_session_start": None, "to_first_test": None}


def test_measured_resources_refuse_ill_typed_sidecar_values(tmp_path: Path) -> None:
    path = _startup_fixture(
        tmp_path,
        sidecar={**_SIDECAR, "cpu_seconds": True, "peak_rss_bytes": 1024.0},
        events=_STAMPED_EVENTS,
    )
    cpu, rss, _startup = mutation._measured_resources(path)
    assert (cpu, rss) == (None, None)
```

- **Verified in scratch:** the 5 new cases pass on the branch code, and they kill all 7 `_measured_resources` mutants (`mutation.py:1931`, `:1941` ×3, `:1944` ×3).
- **Controlled break for the LOG:** change `:1941` `spawned_at is None` to `spawned_at is not None`. `test_measured_resources_are_the_sidecar_values_and_the_stamp_differences` goes red; revert.

### W8R-2 MAJOR: O8's resume leg is hollow, so its named negative ("resume rejects `resources`") is not caught

**Where:** `tests/core/test_mutation_resource_evidence.py:354-358`

**Evidence:**
- The resumed run asserts only `outcome is PASS` and `claims ==` the first run's claims. A loader that refuses every record re-executes both candidates and reaches the same claims.
- Controlled break, in the scratch export: in `_load_validated_state_record`, right after the `judge_sha256` check, add `if "resources" in payload: return _RECORD_REJECTED`. O8 stays green (`1 passed`).
- The brief's O8 row names exactly this negative ("Wiring missing; resume rejects `resources`").

**Fix (exact):** replace lines 354-358:

```python
    # A resumed run reuses the state records (the loader accepts `resources`)
    # and classifies identically.
    resumed = judge_lane()
    assert resumed.outcome is Outcome.PASS
    assert resumed.to_dict()["claims"] == verdict.to_dict()["claims"]
```

with:

```python
    # A resumed run reuses the state records (the loader accepts `resources`)
    # and classifies identically.
    resumed_progress = tmp_path / "resumed.jsonl"
    resumed = judge_lane(progress_artifact=resumed_progress)
    assert resumed.outcome is Outcome.PASS
    assert resumed.to_dict()["claims"] == verdict.to_dict()["claims"]
    resumed_events = [
        json.loads(line) for line in resumed_progress.read_text(encoding="utf-8").splitlines()
    ]
    resume = [event for event in resumed_events if event["event"] == "resume"]
    assert [(event["resumed_total"], event["rejected_total"]) for event in resume] == [(2, 0)]
    assert [event for event in resumed_events if event["event"] == "candidate"] == []
```

- **Verified in scratch:** with the controlled break it goes red (`assert [(0, 2)] == [(2, 0)]`). Without the break it is green.
- Add that break, red then green, to the LOG's O8 row.

### W8R-3 MINOR: `plan-estimate` prints non-JSON `Infinity` and exits 0 when the projection overflows

**Where:** `analysis/src/assay_analysis/plan_estimate.py:143-154`

**Evidence:**
- Input: a plan with 3,760 candidates, and progress `{"event":"run","commit":C}` then `{"event":"plan","baseline_s":1e308}`.
- Output: exit 0, with `"projected_worker_hours": Infinity` and `"projected_wall_hours": Infinity` on stdout. That is not RFC JSON.
- The analysis package's own `evidence._json` refuses that literal, so its own reader cannot read the result back.
- `baseline_s` passes step 5 (finite, > 0); only the product overflows. The `command_finished` path cannot overflow, because the datetime range caps the interval at about 3.2e11 s.

**Fix (exact):**

1. In `plan_estimate.py`, directly after `worker_hours = plan["candidate_count"] * seconds / 3600` (:143), insert:

```python
    if not math.isfinite(worker_hours):
        raise ValueError(f"progress run at line {line}: "
                         "plan.baseline_s is not a finite positive number")
```

2. Append to `analysis/tests/test_analysis_plan_estimate.py`:

```python


def test_op4_a_baseline_whose_projection_overflows_is_refused(tmp_path):
    _refused(tmp_path, _plan(), [_run(), {"event": "plan", "baseline_s": 1e308}],
             "progress run at line 1: plan.baseline_s is not a finite positive number")
```

- **Verified in scratch:** red on the branch code, green with the fix. `plan_estimate.py` stays at 100% line+branch.

### W8R-4 MINOR: the CD41 commit/tree check is keyed on `expected_commit` alone, so a caller that gives only `expected_tree` skips it silently

**Where:** `tools/b105_report_check.py:128-131` (`_plan_structure`)

**Evidence:**
- The code is `if expected_commit is not None and (plan.get("commit") != expected_commit or plan.get("tree") != expected_tree):`.
- `check_campaign_scope(document, plan, expected_tree=T)` therefore never compares the tree. `check_campaign_scope(document, plan)` skips CD41 by default (deviation 7).
- **No live bypass today.** The only production path is `main` → `verify_report_document(expected_commit=args.expected_commit, expected_tree=args.expected_tree)`. Both flags are in `_FULL_FLAGS`, so `parser.error` fires when either is missing, and the real driver always runs CD41.
- The default is still fail-open for a future caller.

**Fix (exact):**

1. At `b105_report_check.py:128`, replace `    if expected_commit is not None and (` with `    if (expected_commit is not None or expected_tree is not None) and (`.
2. Append these two lines as the last statements of `test_o14_the_real_plan_agrees_with_the_real_unsharded_run_and_not_with_a_shard` in `gate/tests/test_b105_report_check_real_plan.py` (4-space indent, after the existing `pytest.raises` block):

```python
    with pytest.raises(ValueError, match="plan commit/tree differ from the expected source"):
        checker.check_campaign_scope(verdict, plan, expected_tree="0" * 40)
```

- **Verified in scratch:** green with the fix, red on the branch code.

### W8R-5 MINOR: the consumer docs omit the normative `TreeSample` semantics, and the DESIGN-GUIDE misstates which candidates carry `phase_seconds`

**Where:**
- `docs/CONSUMERS.md:2682` (the `candidate` row);
- `docs/DESIGN-GUIDE.md:520-532`.

**Evidence:**
- The brief imports P0 "TreeSample semantics" as normative (`reports/b110/P0-measurement-hygiene.md:190-204`):
  - :200: "Documentation says all of this and does not claim 'exactly once'";
  - :203: "Documentation calls it a '1 Hz lower bound on peak Σ RSS', and states that Σ RSS itself overcounts shared and copy-on-write pages, so it is not a bound on true peak memory use".
- Neither CONSUMERS nor DESIGN-GUIDE says that `cpu_seconds` and `peak_rss_bytes` are lower bounds, or that Σ RSS overcounts. Only the `liveness.TreeSample` docstring does.
- The DESIGN-GUIDE text "Every liveness candidate additionally records … `phase_seconds`" is wrong: `phase_seconds` is measured on every candidate (`mutation.py:3119-3124`, unconditional).
- It is also undocumented that all four keys cover only the attempt that produced the bucket. After a witness replay that did not kill, `_run_one` returns the full attempt's evidence, while `elapsed_seconds` spans both attempts.

**Fix (exact):**

1. In `docs/CONSUMERS.md:2682`, replace:

   `` `startup_seconds`). The same object is stored as `resources` in the candidate's state record.``

   with:

   `` `startup_seconds`). `cpu_seconds` is the maximum over the monitor's 1 Hz samples of utime+stime+cutime+cstime summed over the live process tree: a lower bound on the candidate's CPU, not an exact total (the samples can dip, and CPU after the last sample is missing). `peak_rss_bytes` is a 1 Hz lower bound on peak Σ RSS; Σ RSS overcounts shared and copy-on-write pages, so it is not a bound on true peak memory use. The four keys describe the attempt that produced `outcome_bucket`: a witness replay that did not kill counts only in `elapsed_seconds`. The same object is stored as `resources` in the candidate's state record.``

2. In `docs/DESIGN-GUIDE.md:520-524`, replace these exact lines:

```
**Per-candidate resource, phase and startup evidence (B111).** Every liveness
candidate additionally records diagnostic measurements: `cpu_seconds` and
`peak_rss_bytes` of its process tree, `phase_seconds` (`materialize`,
`command`, `integrity`, `teardown`) and `startup_seconds` (`to_session_start`,
`to_first_test`). They ride on the `candidate` progress event and, as the
```

with:

```
**Per-candidate resource, phase and startup evidence (B111).** Every candidate
records `phase_seconds` (`materialize`, `command`, `integrity`, `teardown`, the
worker's own monotonic split); a liveness candidate also records `cpu_seconds`
and `peak_rss_bytes` of its process tree and `startup_seconds`
(`to_session_start`, `to_first_test`). `cpu_seconds` is the maximum over 1 Hz
samples of utime+stime+cutime+cstime summed over the live tree, a lower bound
(the series is not monotone, and CPU after the last sample is missing);
`peak_rss_bytes` is a 1 Hz lower bound on peak Σ RSS, and Σ RSS overcounts
shared and copy-on-write pages, so it is not a bound on true peak memory use.
All four describe the attempt that produced the bucket; a witness replay that
did not kill counts only in `elapsed_seconds`. They ride on the `candidate`
progress event and, as the
```

3. Re-run `tests/core/test_docs_examples_and_vocabulary.py` and `analysis/tests/test_analysis.py`.

### W8R-6 MINOR: O-P1's fixture has `jobs: 1`, so it cannot catch the brief's "dividing by the plan's `jobs`" negative

**Where:** `analysis/tests/test_analysis_plan_estimate.py:32` (`_plan`), used by O-P1 at :78-86.

**Evidence:**
- Controlled break, in scratch: `projected_wall_hours = round(worker_hours / workers / plan.get("jobs", 1), 3)`. `test_op1_*` stays green with `jobs: 1`.
- With `jobs: 3` it goes red. The implementation never reads `jobs`, so every other test is unaffected (61/61 green in scratch).

**Fix (exact):**
- At :32, replace `"jobs": 1, "budget_per_candidate": None,` with `"jobs": 3, "budget_per_candidate": None,`.
- Record the break, red then green, in the LOG's O-P1 row.

### W8R-7 MINOR: the two O7 "diagnostics never fail a candidate" tests cover only the normal-exit row; W8-9's risk (the `finally` replacing an in-flight hung or timeout exception) is untested

**Where:** `tests/core/test_liveness_runner_monitor.py:1545-1562`

**Evidence:**
- Both tests call `_row(tmp_path, "normal", …)`.
- The current single `try/finally` handles all three rows alike. But a refactor that writes the sidecar per exit path, or that re-raises from a narrower handler on the hung or timeout path, would pass both tests.
- Parametrized over `_ROWS` in scratch: 6/6 green. Narrowing the clause to `except OSError:` makes all 3 non-serializable rows red.

**Fix (exact):** replace both functions with:

```python
@pytest.mark.parametrize("row", _ROWS)
def test_o7_a_failing_sidecar_write_never_changes_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: str
) -> None:
    def refuse(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(liveness.os, "replace", refuse)
    outcome, _clock, runner, cwd = _row(tmp_path, row, sampler=_constant_sampler)
    assert outcome == _EXPECTED_OUTCOME[row]
    assert liveness.read_resource_sidecar(runner._events_path_for_cwd(cwd)) is None


@pytest.mark.parametrize("row", _ROWS)
def test_o7_a_non_serializable_sample_never_changes_the_outcome(tmp_path: Path, row: str) -> None:
    outcome, _clock, runner, cwd = _row(
        tmp_path, row, sampler=lambda pid: liveness.TreeSample(object(), 1)
    )
    assert outcome == _EXPECTED_OUTCOME[row]
    assert liveness.read_resource_sidecar(runner._events_path_for_cwd(cwd)) is None
```

## Checklist results (no finding unless listed above)

### 1. Brief conformance

**P (CD25, CD34, CD35).**
- Imports are exactly the listed seven, and there is no judge import. Constants, `_OBJECT_ID` and `fullmatch` match.
- Read order is plan, then progress. `text, _ = evidence._read`.
- Every quoted message matches, and the first failure wins.
- The output has exactly the 9 keys (checked on real output). Rounding follows the brief. `estimated_*`, `jobs` and `budget_per_candidate` are never read. `tree` comes from the plan, and the binding is through the commit only (CD34).
- Exit codes: 0 on success, 2 on every refusal (including argparse `--workers` 0, 65, -1, `x` and `1.5`), stdout empty on refusal, one stderr line.
- CLI wiring, `_workers`, `__all__` and the `[lanes.analysis]` target are as specified.

**H (CD36).**
- `tree` follows `head_rev` exactly as specified. `commit` and `tree` appear only in the `ok` payload.
- `PLAN_ESTIMATE_HINT` is byte-equal to the brief (checked programmatically). It goes to stderr only for `ok`, and only when `err` is given.
- `test_b105_cli_boundaries.py` is untouched.

**R (CD4).**
- The sampler call sits immediately before `self._sleep(self._poll_interval_s)` (`liveness.py:1942-1947`). That is after B107's resource read and after the hung and timeout raises.
- It never touches `resource_trace`, `previous_resources` or `cpu_samples`.
- O6 uses B107's `_runner` and `_clear_resource_snapshot`. `_runner` gained `sampler`/`sleep`, as W8-10 requires.
- `_pid_stat`, `_pid_cpu_ticks` and `_walk_tree` are as specified. `tree_cpu_seconds` names `_pid_cpu_ticks` at call time.
- The stale tuple, `spawned_at` placement, the 5-key sidecar and `except (OSError, TypeError, ValueError)` all match.
- `runner.py:4435` is the only production `sampler=`.
- The `_MutantRun` fields, the four `phase_seconds` stamps, the single shared `resources` object, and `MUTATION_STATE_SCHEMA_VERSION` (still 1) all match. `_run_one` is unchanged.

**S.** Owner and pairing rules are as P0 specifies. `_owner_stream` is the single owner rule. `_selected_test_events` semantics are unchanged: owner-filter-then-test-only equals the old test-only-then-owner-filter, and so does the legacy branch.

**C.**
- Refusal order is argparse (8), receipt, steps 3–5, then 9 (structure plus CD41), 1–4, and 5–7.
- CD41's message is exact, and there is an O11 case for `commit` and for `tree`.
- Driver: `plan_path`, and the plan step comes before `run` only under `*R2*`.

**G.** G1–G5 as P0 specifies. G3 imports `m` rather than running it as a script. G5's scope note is present.

**Scope.**
- No forbidden file is touched: `verdict.py`, `verify.py`, schemas, `isolation.py`, `run-gate.toml` and the exclusions fixture all have an empty diff.
- Two files are outside the list (`test_dependency_purity.py` and `test_analysis_package_boundary.py`). That is at the limit, not over it.

### 2. Judge correctness under B107
- The loop body is byte-identical (diffed programmatically). The only additions are the post-decision sampler block and the `_monitor` → `_poll` split, whose `return` and `raise` propagate the same objects.
- A sampler exception is swallowed. The sampler value never reaches `cpu_now`. O6's 3×3 matrix compares both outcome and fake-clock end time.
- The `finally` swallows `OSError`, `TypeError` and `ValueError`. Production `tree_sample` yields only float and int values, so `json.dumps` cannot raise anything else. The `finally` runs before `_cleanup_process_group`, as intended.
- Nulls follow W9-7:
  - `phase_seconds` is always the 4-key dict on a new run;
  - `startup_seconds` is `None` off liveness, and otherwise a 2-key dict of number-or-`None`;
  - `cpu_seconds` and `peak_rss_bytes` are `None` when unmeasured.

### 3. Process slips
- **`liveness.py` (inline-python edit):**
  - `_poll` equals the base `_monitor` body plus the sampler block;
  - `_walk_tree` equals the base walker with one line changed;
  - there are no duplicate top-level definitions (checked with AST);
  - the pragma lines have not moved.
- **`test_mutation_resource_evidence.py` (shell-appended placeholder):**
  - no placeholder remains, and the only `pass` is inside the O7b helper string;
  - the file ends with `\n`;
  - there are no duplicate test names.

  I also checked 7 other edited test files for shadowed duplicates and found none.

### 4. Checker (C)
- Deviations 7–9 are safe on the real path. `main` requires both `--expected-*` flags, so CD41 always runs in the driver (hardening in W8R-4).
- An unreadable plan becomes a refusal at step 9 (`cannot read plan`).
- **No stale plan can be used:**
  - `.assay/` is created at driver line 25;
  - the `>` redirect truncates before `assay plan` runs;
  - a failure returns 2 before the run or the checker;
  - a non-R2 lane passes no `--plan-json`.
- Plan and run share `$project` HEAD. `git rev-parse <commit>^{tree}` on both sides gives the root tree, so CD41 compares like with like.
- Refusal 8 prints argparse usage, not `B105_REPORT_REJECTED=`. That matches W4's argparse precedent and P0's "argument refusal", so it is not a finding.

### 5. `plan-estimate` attacks (run through the real CLI; inputs in `scratchpad/w8review/pe/`)

| Input | Result |
|---|---|
| Parseable torn tail that is a `run` at C′, after a valid baseline at C | exit 0, 100.0 from C (the C′ segment has no baseline, so it is not selected) |
| Parseable torn tail `run` at C′ plus a torn `plan` | exit 2, `commit mismatch … line 3` |
| W8-8: two lanes | exit 2, the multi-lane refusal |
| Resumed same-lane run whose baseline FAILs | exit 0, falls back to the earlier 548 s segment at the same commit (correct) |
| `null` lane together with a named lane | accepted as one lane |
| Duplicate-key plan | `plan: malformed JSON` |
| `Z`-suffixed timestamps; CRLF lines | accepted |
| `1e308` | W8R-3 |
| `1e-320` | prints `baseline_s: 0.0`. Cosmetic, no fix requested. |

### 6. Coverage floor
Confirmed as the implementer reported (numbers in the Evidence table).

### 7. Tests
**O10 (`test_liveness_outer_stream.py`):**
- Deterministic: no timing asserts, and `timeout=600` is a failsafe only.
- The env is exactly `PATH`, `PYTHONPATH` and `ASSAY_LIVENESS_EVENTS`, so nothing leaks from or to the outer stream. `subprocess.run` waits for the child.
- `test_liveness.py` has no skip or xfail, so the `N passed` equals `test`-records equality is exact today.
- `test_liveness.py` is the only judge test file that calls plugin hooks directly, so the leak class is fully covered.

**Other tests.**
- Fake-clock monitor tests. The CPU-tick handshake in O7b is not wall-clock.
- Every monkeypatch is restored.
- The only hollow spots are W8R-1, W8R-2, W8R-6 and W8R-7.

### 8. Docs and CD38 (amended)
- **Present:**
  - the README analysis line (parsed by the docs test);
  - the CONSUMERS upper-bound fix, pasteable examples and progress-table keys;
  - DESIGN-GUIDE B107, §B105 and CD1 text;
  - the CHANGES Added, Fixed and Testing lines (Added also names the sidecar, per X-1);
  - the backlog status.
- No mention of `--baseline-from`, `estimate_provenance` or `full_suite_central_*`.
- Every consumer-visible addition is on CD38's list. Gaps: W8R-5.

### 9. Combined-axis attacks

**(A) FAILS: R state `resources` × resume.**
- A loader that silently rejects any record carrying `resources` still passes O8. The resumed run re-executes both candidates and reproduces the claims.
- This was demonstrated with a controlled break in the scratch export (W8R-2). Fixed by asserting `resumed_total == 2` and `rejected_total == 0`, with no `candidate` events.

**(B) Holds: sampler × hung × resume.**
- The hung-record loader validates only `liveness_resource_evidence`; `resources` is ignored.
- A maximal complete hung trace serializes to 521,085 bytes against the 1 MiB `MUTATION_STATE_RECORD_LIMIT`, so the ~250 bytes of `resources` cannot push a record past the reader.

**(C) Holds, fail-closed: stderr hint × a consumer that captures `assay plan … 2>&1`.**
- `plan-estimate` answers `plan: malformed JSON` with exit 2 (run on real input). The checker answers `cannot read plan` (the O11 case).
- The driver redirects stdout only.

**(D) Holds: plan binding × W7 shallow snapshot.**
- `_cmd_plan` uses the same `_snapshot_policy_for_lane` and `snapshot_limits` as the run.
- `tree` comes from `rev-parse` in the full project checkout, and so does the driver's `source_tree`.
- `whole_target` self-qualification needs no base history.

## Verdict: **MERGE-WITH-FIXES**

- **Counts:** BLOCKER 0, MAJOR 2 (W8R-1, W8R-2), MINOR 5 (W8R-3 to W8R-7).
- Every fix above is exact text and was prototyped in a scratch export: red on the branch where it adds a test, green with the fix.
- Nothing in W8 changes a hung, timeout or classification decision, the verdict schema, or the R1 floor.
- After the fixes, the fixer:
  1. runs the focused files;
  2. runs `analysis/tests` with coverage (it must stay at 100%);
  3. records the new controlled breaks in `W8-LOG.md`;
  4. keeps `READY-FOR-GATE` pointing at the new head for the controller's gate (CD44).
