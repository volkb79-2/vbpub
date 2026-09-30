# W8 - Measurement evidence and campaign hygiene (B111)

| Field | Value |
|---|---|
| Backlog | B111 (B110-P0), Wave A stage 3 |
| Branch | `wave-a-w8-measurement`, cut from `assay-b110-landing` after W2, W3, W4, W6 and W7 have merged. It merges `--no-ff` before W9, then the branch is deleted. |
| Depends on | W2 (analysis package, CD1), W3 (layout), W4 (`gate/tests/`, receipt), W6, W7 (both edit `gate/tests/test_self_lane.py` and `assay.toml`; stage 3 order W7, W8, W9; CD42) |
| Class / implementer | 2c / Sonnet; nothing is left open |
| Decisions | CD1, CD4, CD25, CD26, CD29; A-464, A-472, A-474, A-478 |

This brief replaces the P0 half of `REBASE-P0-P2.md`.
- From `b110/P0-measurement-hygiene.md` ("P0"), read **only** the subsections listed under "Imported from P0". The rest of P0 is void.
- Anchors are at `f27fbdea`, relative to `assay/`. W2-W6 move them, so resolve each by file name, then by function.
- **No classification, verdict or verdict-schema change.**

## Disposition of the P0 items
| P0 item | Disposition | W8 item |
|---|---|---|
| W1 `--baseline-from` | **Moved** (CD1, CD25). Dropped: both flags, `MeasuredBaseline`, the `estimate_*` and `full_suite_central_*` keys, and the auto-budget table. The CONSUMERS "upper bound" fix is kept. | **P** `assay analyze plan-estimate` (analysis); **H** `assay plan` commit/tree/hint (judge) |
| W2 resources | **Kept**, re-carved by CD4. Dropped: the `_run_one` sum/max (a non-killing replay returns `None`, `mutation.py:3071-3082`) and the `finally` teardown stamp (a raising attempt yields no `_MutantRun`). | **R** (judge) |
| W3 forwarding | **Kept** | **S** (judge) |
| W4 leak | **Kept**: the leak now marks a quiet test past 30 s as hung (REBASE-P0-P2 (d)) | **L** (judge tests) |
| W5 checker | **Kept**, on top of W2's scope check and W4's receipt | **C** (tooling) |
| W6 G1-G5 | **Kept** (A-472) | **G** (judge tests) |
| "Opus for W2" | **Dropped** (Sonnet only) | - |

- H, R and S are judge code in B105 scope (100% R1, no pragma).
- P is analysis code, held to 100% by `[lanes.analysis]`.

## Context to read first
- `CARVER-DECISIONS.md`: CD1, CD4, CD25, CD26, CD29, CD38 (amended), CD39-CD42.
- `W2-analysis-package.md`: steps 1-3, step 6, T3.
- The code at the anchors cited below.
- `tests/test_liveness_runner_monitor.py:100-150`: `_clear_resource_snapshot` and `_runner`, B107's load-independent helper (CD4).
- **Imported from P0 (normative):**
  - "TreeSample semantics" and its field-index table;
  - W3 "Owner rule" and "Pairing rule";
  - W4 steps 1-3;
  - W5 refusals 1-9 and step 5 (O14);
  - W6 G1-G5;
  - oracles O7b and O9-O14.

## Implementation packet (normative)

### P. `assay analyze plan-estimate` (CD25), new `analysis/src/assay_analysis/plan_estimate.py`
**Imports** (exactly these; no judge import): `from __future__ import annotations`, `import math`, `import re`, `from datetime import datetime`, `from pathlib import Path`, `from typing import Any`, `from assay_analysis import evidence`.

**Constants:** `SCHEMA_VERSION = 1`, `MAX_PLAN_BYTES = 16 * 1024 * 1024`, `MAX_PROGRESS_BYTES = 64 * 1024 * 1024`, `_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")`.

**Signature:** `plan_estimate(plan_path: Path, progress_path: Path, *, workers: int = 1) -> dict[str, Any]`. It raises `ValueError(<quoted message>)`; the first failure wins.

1. **Read** each file, with `label` set to `plan` or `progress`:
   - not `is_file()` → `"{label} {path}: not a regular file"`;
   - `st_size` over the limit → `"{label} {path}: exceeds {limit} bytes"`;
   - otherwise read it with `evidence._read(path)`.
2. **Plan**, parsed with `evidence._json`:
   - not a dict → `plan: top level is not an object`;
   - `status != "ok"` → `plan: status is {status!r}, not 'ok'`;
   - `commit`, then `tree`, not a str matching `_OBJECT_ID` → `plan: {key} is missing or not a full object id (a plan from before assay 7.2.0?)`;
   - `candidate_count` not `type() is int` and ≥ 0 → `plan: candidate_count is not a non-negative integer`;
   - `candidates` not a list of that length → `plan: candidate_count does not match candidates`.
3. **Progress.** Split on `"\n"`.
   - If the text ends with `"\n"`, drop the empty last element.
   - Otherwise the last element is a tail: ignore it if `evidence._json` fails on it (torn), keep it if it parses.
   - Any other line fails as `progress line {n}:` followed by one of `malformed JSON`, `not an object`, `event is not a string`, or `event precedes the first run header`.
   - Each `run` opens a segment.
4. **Selection.** A segment has a baseline iff it holds either:
   - a `plan` record whose `baseline_s` is present and not `None`; or
   - a `command_finished` with `phase` in `("baseline", "direct")` and `outcome == "PASS"`.

   The **last** such segment is selected. If there is none → `progress: no completed baseline`.
5. **Measure.** `L` is the selected segment's `run` line.
   - More than one `plan` → `progress run at line {L}: multiple plan events`.
   - With a `plan` whose `baseline_s` is not `None`: the value must be an int or float, not bool, finite and `> 0`, else `progress run at line {L}: plan.baseline_s is not a finite positive number`.
   - Otherwise take the **last** PASS `baseline`/`direct` `command_finished`. `started` and `ended` must both parse with `datetime.fromisoformat`, be timezone-aware, and satisfy `ended > started`, else `progress run at line {L}: the selected command_finished record has an invalid interval`. The value is `(ended - started).total_seconds()`.
6. **Commit.** The segment's `run.commit` must equal the plan's `commit`, else `commit mismatch: progress run at line {L} is at {run_commit!r}, plan is at {plan_commit!r}`. Progress records no tree, so the tree is bound through the commit (carver Q1).
7. **Return exactly this object:**
   ```json
   {"schema_version": 1, "commit": "<plan commit>", "tree": "<plan tree>", "candidates": 4, "baseline_s": 100.0,
    "per_candidate_s": 100.0, "workers": 1, "projected_worker_hours": 0.111, "projected_wall_hours": 0.111}
   ```
   - With `m` the unrounded value and `wh = candidate_count * m / 3600`: `baseline_s = per_candidate_s = round(m, 3)`, `projected_worker_hours = round(wh, 3)`, and `projected_wall_hours = round(wh / workers, 3)`.
   - It never reads `estimated_*`, `jobs` or `budget_per_candidate`, and never classifies.

**CLI** (`assay_analysis/cli.py` imports `evidence, plan_estimate`):
- After the `progress` subparser, add `add_parser("plan-estimate", help="project campaign hours from an assay plan and a measured baseline (diagnostic)")` with:
  - `--plan-json` (Path, required, metavar `PLAN`);
  - `--progress` (Path, required, metavar `PROGRESS`);
  - `--workers` (`type=_workers`, default 1, metavar `N`). `_workers` raises `argparse.ArgumentTypeError("--workers must be an integer from 1 to 64")` outside 1-64.
- In `cmd_analyze`, before the final `else:`, add a `plan-estimate` branch: `result = plan_estimate.plan_estimate(args.plan_json, args.progress, workers=args.workers)`.
- The except branch returns `2 if args.analysis_command in ("report", "plan-estimate") else 1`. A refusal prints nothing on stdout and `assay analyze: <message>` on stderr.
- `__init__.__all__ = ["cli", "evidence", "plan_estimate"]`.
- Add the new module, in sorted position, to `[lanes.analysis]` `judge.targets`.

**Details (closed):**
- Step 1 reads the plan, then the progress. It uses `text, _ = evidence._read(path)`.
- `_OBJECT_ID.fullmatch(value)`.
- A plan whose JSON fails `evidence._json` → `plan: malformed JSON`.
- Progress line numbers `n` and `L` are 1-based.
- A kept (parseable) tail is validated like any other line.
- A `started` or `ended` that is not a `str` gives the "invalid interval" message.
- Step 3 addition: the distinct `lane` values of all `run` records (null ignored) must number at most one, else `progress: runs of more than one lane ({sorted lanes}); pass a single-lane progress file`. No output key changes.

### H. `assay plan` (judge, `cli.py`)
- After `commit = git.head_rev(...)` (:1651), add `tree = git.run(lane_file.project_root, "rev-parse", f"{commit}^{{tree}}", remaining=deadline.remaining).strip()`.
- The `ok` payload gains `commit` and `tree`. `unsupported` is unchanged.
- Add a constant above `_cmd_plan`: `PLAN_ESTIMATE_HINT = "assay plan: estimated_serial_seconds and estimated_wall_seconds come from the declared budget_per_candidate (a 60 s placeholder when it is omitted, auto or none), not a measurement; for a measured projection run: assay analyze plan-estimate --plan-json PLAN --progress PROGRESS [--workers N]"`.
- `_cmd_plan(args, out, err: TextIO | None = None)`; `main` passes `err`.
- After the payload print: `if payload["status"] == "ok" and err is not None: print(PLAN_ESTIMATE_HINT, file=err)`.
- Stdout stays one JSON document. The four `cli._cmd_plan(..., io.StringIO())` calls in `test_b105_cli_boundaries.py` stay unmodified.

### R. Resource, phase and startup evidence (judge, CD4)
**`liveness.py`:**
- `TreeSample(NamedTuple)` with `cpu_seconds: float` and `rss_bytes: int`.
- `tree_sample(root_pid)` follows P0's semantics: pre-order walk; CPU is `utime+stime+cutime+cstime` over live processes; memory is RSS × `SC_PAGE_SIZE`; one `/proc` read is shared with `_pid_cpu_ticks`. Build it as follows:
  - `_pid_stat(pid: int) -> _PidStat` (a private `NamedTuple` of `ticks`, `child_ticks` and `rss_pages`) reads `/proc/<pid>/stat` once, using the `:560-561` split idiom and fields `[11]+[12]`, `[13]+[14]` and `[21]`.
  - `_pid_cpu_ticks(pid)` becomes `return _pid_stat(pid).ticks`. Its name, signature and raise contract are unchanged.
  - Extract the body of `tree_cpu_seconds` verbatim into `_walk_tree(root_pid: int, read: Callable[[int], T]) -> list[T]`. It returns the per-pid `read` values in visit order: the root first (and allowed to raise), children read before their own children are listed, `visited`, and both fallbacks.
  - `tree_cpu_seconds(root_pid)` becomes `return sum(_walk_tree(root_pid, _pid_cpu_ticks)) / os.sysconf("SC_CLK_TCK")`. It names `_pid_cpu_ticks` at call time, so `test_liveness_proc_helpers.py:152`'s monkeypatch still applies. `tree_cpu_seconds` is unchanged, exceptions included.
  - `tree_sample(root_pid)` becomes `stats = _walk_tree(root_pid, _pid_stat)`, returning `TreeSample(sum(s.ticks + s.child_ticks for s in stats) / CLK_TCK, sum(s.rss_pages for s in stats) * os.sysconf("SC_PAGE_SIZE"))`.
  - The existing walker tests (`test_liveness_proc_helpers.py:62-190`) then cover the shared walk. The whole `test_liveness_proc_helpers.py` file must pass unmodified.
- `RESOURCE_SIDECAR_SUFFIX = ".resources.json"`.
- `read_resource_sidecar(events_path) -> dict | None` returns `None` when the sidecar is absent, unreadable, malformed, not a dict, or has `format != 1`.
- `first_event_times(events_path: Path | None) -> tuple[float | None, float | None]` returns:
  - the `t` of the first `session_start` record from `_iter_events`;
  - the `t` of `_selected_test_events(events_path)[0]` when that list is non-empty.

  Each is `None` when missing or not a finite int/float (bool excluded).

**`LivenessRunner`:**
- **`__init__`** gains `sampler: Callable[[int], TreeSample] | None = None`, after `resource_reader`. The 28 `cpu_reader=` injections are untouched.
- **`__call__`:**
  - adds `events_path.with_suffix(RESOURCE_SIDECAR_SUFFIX)` to the stale tuple (:1394-1398);
  - sets `spawned_at = time.time()` on the line before the `try:` around `self._popen` (:1411);
  - passes `spawned_at` to `_monitor`.
- **`_monitor(..., spawned_at: float)`:**
  - locals `samples = 0`, `max_cpu = None`, `peak_rss = None`;
  - a `try:`/`finally:` wraps the whole `while True:`, including the `return` at :1536;
  - the `finally` writes `{"format": 1, "samples": samples, "cpu_seconds": max_cpu, "peak_rss_bytes": peak_rss, "spawned_at": spawned_at}` to `<sidecar>.tmp`, then `os.replace`s it onto the sidecar; `except (OSError, TypeError, ValueError): pass` (one clause; diagnostics never fail a candidate).
- **CD4 placement (verbatim):** the sampler call sits immediately before `self._sleep(self._poll_interval_s)` (:1705), after the hung and timeout checks. It never reads or writes `resource_trace`, `previous_resources` or `cpu_samples`. A tick that raises hung or timeout takes no sample.
  - A sampler exception leaves the tick unsampled.
  - Success does `samples += 1` and updates the maxima.
- **`runner.py:4427`** passes `sampler=liveness.tree_sample`; it is the only production caller.

**`mutation.py`:**
- `_MutantRun` gains `cpu_seconds: float | None`, `peak_rss_bytes: int | None`, `phase_seconds: Mapping[str, float] | None` and `startup_seconds: Mapping[str, float | None] | None`, all defaulting to `None`.
- `_run_attempt` records four `phase_seconds` keys with `time.monotonic()`:
  - `materialize`: from `started_monotonic` (:2906) to the first line of the `with` body;
  - `command`: around `execute_plan`;
  - `integrity`: around the `_snapshot_left_dirt` `try` (:2999-3027);
  - `teardown`: from the end of that `try` to the line after the `with` (:3058).
- When `liveness_events_dir is not None`, call `read_resource_sidecar` and `first_event_times` next to `tests_completed`, with the same `candidate_events_path(...)`:
  - `cpu_seconds` and `peak_rss_bytes` come from the sidecar;
  - `startup_seconds = {"to_session_start": t0 - spawned_at, "to_first_test": t1 - spawned_at}`, where `spawned_at` is read from the sidecar. A missing operand, including a missing sidecar, gives `None`.

  Otherwise all three fields are `None`.
- `_run_one` is unchanged.
- The `candidate` event gains the four keys after `tests_completed`. `cpu_seconds` and the `phase_seconds` values are rounded to 3 decimals. `startup_seconds` is a plain dict or `None`.
- The state record gains `"resources": {…same four}`. `MUTATION_STATE_SCHEMA_VERSION` stays 1. The worker loop builds the four-key dict once (rounded exactly as the event requires) and uses the same object for the event keys and for the record's `resources`.
- A resumed candidate emits nothing new. Nothing here changes a bucket.

### S, L, G, C
- **S.** P0 W3 steps 1-3.
- **L.** P0 W4 steps 1-3. The new file is `tests/core/test_liveness_outer_stream.py`; its nested target is `tests/core/test_liveness.py`.
- **G.** P0 W6 in `tests/core/test_isolation_guards.py`, duplicating the W3-placed `test_isolation.py` helpers.
- **C.** P0 W5 in `tools/b105_report_check.py`.
  - **Refusal order (F-R0-3), first failure wins:**
    1. argparse (P0 #8, W4's receipt flags);
    2. W4's receipt;
    3. producer exit, root, commit, lane, rigor, tree;
    4. W2's `verify_scope`;
    5. outcome, exit, claims, version, provenance;
    6. `check_campaign_scope`: 9 → 1-4 → 5-7.

    Every refusal prints `B105_REPORT_REJECTED=` and exits 2.
  - **`run_and_verify_lane`.** Add `local plan_path=".assay/plan-$lane.json"`. For a rigor that contains `R2`, run `"$assay_bin" plan "$lane" --file assay.toml > "$plan_path" || return 2` before `"$assay_bin" run "$lane"`, then pass `--plan-json "$plan_path"`.
  - **Tests:**
    - O11 goes in `gate/tests/test_b105_report_check.py`;
    - O12 goes in `gate/tests/test_self_lane.py`;
    - O14 goes in the new `gate/tests/test_b105_report_check_real_plan.py`:
      - **Setup.** Build a repository with `gate.tests.support.judge.git_repo`:
        - `.gitignore` contains `__pycache__/`, `.pytest_cache/` and `.assay/`.
        - `src/mod.py` and `tests/test_behavior.py` are copied from `_seed_pytest_mutation` (`tests/core/test_b106_reuse_and_witness.py:661-677`), with one more compare-swap site (`return value > 0 and value < 10`) so that there are two candidates.
        - A committed `assay.toml` holds one lane `package`: `rigor = ["R0","R2"]`; `argv = [sys.executable,"-m","pytest","tests","-q","-p","no:cacheprovider"]`; `judge.language = "python"`, `source_roots = ["src"]`, `base = "<seed base>"`; `[judge.mutation]` `jobs = 1`, `max_mutants = 10`, `operators = ["python:compare-swap"]`.
      - **Steps.** Run through `assay.cli.main`: `plan package --file <toml>` (stdout captured and parsed), `run package --file <toml> --verdict-json <tmp>/v.json`, and `run package --file <toml> --shard 0/2 --verdict-json <tmp>/s.json`.
      - **Assertions.** `check_campaign_scope(v, plan)` does not raise, and `check_campaign_scope(s, plan)` raises `ValueError` (refusal 5).
      - Do not use `make_lane`, `make_r2_judge` or `_seed_pytest_mutation` itself.
  - **Refusal 9 addition (CD41, W8-14).** `plan["commit"] != --expected-commit or plan["tree"] != --expected-tree` → `ValueError("plan commit/tree differ from the expected source")`. P0's rationale "the plan payload has no lane or commit field" is false after H. Add one O11 case in `gate/tests/test_b105_report_check.py`: a plan whose `commit` (or `tree`) differs from the expected value is refused with exactly that message.

**Degrees of freedom:** private helper names and local decomposition only.

## Work
Use editor tools only. Commit per item, each with a green focused run, in the order L, S, R, P+H, C, G, Docs.
1. **R tests.**
   - O6 and O7 go in the W3-placed `test_liveness_runner_monitor.py`:
     - `_runner(...)` gains `sampler=None` and `sleep=None` (default `clock.advance`), both passed through.
     - O7's second call constructs `liveness.LivenessRunner` directly, with the same `events_dir`, a `popen` that raises `OSError`, and the same `cwd`.
     - O6's normal-exit row finishes on the third tick (`_scripted_events(..., finish_at=(2.0, 0))`), so the sampler runs at least twice.
   - O7b, O8 and `tree_sample` go in the new `tests/core/test_mutation_resource_evidence.py`. For `tree_sample(os.getpid())` both values are `> 0`; a dead pid raises `OSError`.
     - Additional unit tests there, each asserting a returned value:
       - `read_resource_sidecar` returns `None` for: an absent file; the bytes `b"\xff"`; `"[]"`; `{"format": 2}`. It returns the dict for a valid 5-key file.
       - `first_event_times` returns `(None, None)` for `None` and for an absent path. It returns `(t0, None)` when the owner wrote no `test` record. It returns `None` for a `t` that is `true`, `"1"` or `1e999`.
       - O9's fixture also carries a setup `duration_s: true`, a teardown `-1.0` and a final call with no teardown. Each of these gives `None`.
2. **P+H tests.**
   - O-P1..O-P5 go in `analysis/tests/test_analysis_plan_estimate.py`.
   - O-H1 goes in `tests/core/test_cli_plan_estimate_hint.py`.
   - If W2's T8 wheel list is exact, add the new module to it.
3. **Docs.** P0's "Docs sync" text is void; use the exact lines below. W1's upper-bound fix goes into CONSUMERS :2307-2312, together with pasteable `assay plan … > plan.json` and `assay analyze plan-estimate --plan-json plan.json --progress .assay/progress-self-qualification-preflight.jsonl --workers 3` examples and the `commit`/`tree` keys. **Exact lines:**
   - README block: `assay analyze plan-estimate --plan-json plan.json --progress .assay/progress-self-qualification-preflight.jsonl --workers 3` (no usage-style `[--workers N]`: the docs test parses it with `shlex.split(line)[2:]`).
   - CHANGES Added: "`assay analyze plan-estimate`; `assay plan` JSON `commit`/`tree` and a stderr hint; `candidate` progress `cpu_seconds`/`peak_rss_bytes`/`phase_seconds`/`startup_seconds` and state `resources`; baseline `test` `setup_s`/`teardown_s`."
   - CHANGES Fixed: "liveness test leak; CONSUMERS 'upper bound' claim".
   - CHANGES Testing: "G1–G5".
   - Never mention `--baseline-from`, `estimate_provenance` or `full_suite_central_*`.
   - **Additive surfaces (CD38 amended, X-1).** The docs/CHANGES step names these W8 additions: the `candidate` progress keys `cpu_seconds`, `peak_rss_bytes`, `phase_seconds`, `startup_seconds`; the baseline `test` keys `setup_s`/`teardown_s`; state `resources`; the `<events>.resources.json` sidecar; and the plan's `commit`/`tree`. All are additive; 7.2.0 stands.

   The rest is re-anchored as follows:
   - the progress table goes to :2648/:2652;
   - the README analysis block (:52-58) gets the `plan-estimate` line, which the docs test parses;
   - the README B105 text is at :975;
   - the DESIGN-GUIDE text goes to :484 (B107) and :1972 (§B105), plus :3406 (why analysis, CD1);
   - update CHANGES.

## Oracles
Record each controlled break in the LOG, red then green.

| # | Observable | Negative (plausible wrong implementation) |
|---|---|---|
| O-P1 | Plan (C, T, 4 rows) with a run at C and `baseline_s=100.0` gives exactly the example. `--workers 4` gives a wall of `0.028`. Exit 0, stderr empty | Using `estimated_serial_seconds` (0.067); dividing by the plan's `jobs` |
| O-P2 | Three segments at C: (1) `baseline_s=50`; (2) no `plan`, a FAIL `baseline` spanning 25 s then a later PASS `baseline` spanning 40 s; (3) only `candidates`. The result is `40.0` | First segment; the FAIL record; last segment regardless |
| O-P3 | A baseline at C, then a later one at C′, gives exit 2 with `commit mismatch` | Filtering by commit (exit 0) |
| O-P4 | Every refusal in steps 1-6 gives exit 2, empty stdout and one stderr line. Cover `baseline_s` values `0`, `"5"` and `true`, the multi-lane refusal (step 3 addition), and oversize (monkeypatch `MAX_PROGRESS_BYTES = 10`). A torn tail exits 0. `--workers 0` and `--workers 65` exit 2 | Silent fallback; skipping `0`; refusing a torn tail; exit 1 |
| O-P5 | Via `assay.cli.main(["analyze","plan-estimate",…])`: the same result. A real `main(["plan",…])` on the lane from `test_cli_run.py:316` (git through `subprocess`), plus progress from `main(["run", "package", "--file", toml, "--progress", p])` on the same lane (not hand-written), exits 0; assert exit 0, `candidates == candidate_count` and `baseline_s > 0`, and no other value | H unwired, so the plan has no `commit` |
| O-H1 | On a real repo, `commit` and `tree` equal `rev-parse HEAD` and `HEAD^{tree}`, and stderr is exactly the hint plus `\n`. An unsupported lane (the `test_b106_reuse_and_witness.py:655` pattern) gives stderr `""` and no `commit`/`tree` | Hint on stdout; `tree = commit`; hint when unsupported |
| O6 | P0's 3×3 matrix via `_runner(sampler=…)`. CD4 spy: `cpu_reader`, `resource_reader`, `sampler` and `sleep` log into one list. A sampled tick is exactly `cpu, resource, sampler, sleep`; a hung or timeout tick has no `sampler` | Sampler CPU fed into `cpu_growing`; sampler before `resource_reader` |
| O7 | A 5-key sidecar after a normal exit, a hung and a timeout. `samples` equals the successful calls. A second `__call__` whose `popen` raises leaves no sidecar. A raising `os.replace` (monkeypatched) leaves the outcome unchanged; so does a sampler returning a non-serializable value | Written only on normal exit; no stale cleanup; the error escapes |
| O8 | P0's O8 on a `liveness="true"` lane: presence and types; state `resources` equal the event. The first `runner.run_lane` call passes `resume=True`, `state_dir=tmp_path / "state"` and `progress_artifact=tmp_path / "progress.jsonl"`, all outside the repository; before comparing, assert that `len(list(state_dir.glob("*.json")))` equals the number of `candidate` events, and that the number is 2. A constructor spy sees the production `sampler=liveness.tree_sample` | Wiring missing; resume rejects `resources` |
| O7b, O9-O14 | As in P0, at the W3/W4 paths | As in P0 |

**§3b (pasted). An oracle must not contain any of the following.**
- **A (L20).** An assert after a `monotonic()+N` deadline or a `sleep(N)`, or on elapsed time or iteration counts. Wait on join/Event/queue, or remove the wait. A timeout is only a generous failsafe and never decides. A slow-host failure is a true red: fix the test; never widen the timeout or add CPU.
- **B.** An unrestored global (logging, `environ`, module attributes, singletons; PL7 §5). A monkeypatch on a `__getattr__` proxy; patch its owner (L19). A teardown that destroys instead of restoring. Use a fresh `tmp_path`. For a full-suite-only failure, ask what an earlier test left behind.
- **C.** A `pass` body, a "nothing raised" test, trivia (call counts, private attributes, logs) in place of the contract, or a weakened or deleted assertion.
- **D (L11).** A no-cover pragma; the token matches anywhere on a line. An excluded `except` body does not cover its clause. Restructure instead.
- **E.** Real network access, or `now()`/`time()` in an asserted value. Inject the boundary.
- **F.** Predicted coverage or mutation numbers, or missing lines read from a report (its Missing column hides arcs). Assert the policy and run the tool.
- **Check:** could the result flip on a slower host, in another worker, or in another order?

## Scope
- **Touch:**
  - `src/assay/{cli,liveness,mutation,runner}.py`, for items H, R and S;
  - `analysis/src/assay_analysis/{__init__,cli,plan_estimate}.py`;
  - the `[lanes.analysis]` targets line in `assay.toml`;
  - `tools/b105_report_check.py` and `tools/self-qualification-gate.sh`;
  - the named tests and T8;
  - the 4 docs, the B111 status line, and `reports/wave-a/W8-LOG.md`.
- **Forbid:**
  - `verdict.py`, `verify.py` and the verdict schema;
  - `MUTATION_STATE_SCHEMA_VERSION`, `ReasonCode` and the `judge_sha256` inputs;
  - `isolation.py`, `run-gate.toml` and the exclusions fixture;
  - every classification rule.
- **Pragma lines.** Before each commit:
  1. `grep -n "pragma: no cover" src/assay/liveness.py src/assay/mutation.py` prints exactly two hits: `liveness.py:94:` and `mutation.py:148:`.
  2. `sed -n 95p src/assay/liveness.py` prints `    from .runner import CommandPlan`.
  3. `sed -n '155,156p' src/assay/mutation.py` prints the two `from .adapters.base …` / `from .runner …` lines.

  Add no line above those: `NamedTuple`, `Callable` and `Mapping` are already imported in both files. A new stdlib import for W8 (`math` in `liveness.py`) is a function-level `import math` as the first statement of the function that needs it.
- More than 2 files outside this list is a BLOCKED trigger.

## Gate
1. Serial: `nice -n 19 ionice -c3 python -m pytest analysis/tests <the named judge and gate tests> -q -p no:cacheprovider`.
2. `cd <worktree>/assay && python ./run-gate.py tester-unified > <log> 2>&1`. In a separate step, read `ASSAY_GATE_PHASE=analysis-lane-passed`, `ASSAY_GATE_CONTAINER_EXIT=0` and `ASSAY_REGISTERED_GATE_COMPLETE=1`. `ASSAY_GATE_INCONCLUSIVE=host busy` (exit 3, CD32) is not a result: rerun later.
3. Mandatory, afterwards: `python ./run-gate.py self-qualification-preflight`, then read `B105_VERIFIED_LANE=` in a separate step. If the host forbids it: BLOCKED.

There is no known red (CD29).

**Host-load rule** (paste into every agent prompt): the host is shared with a production game server; nice/ionice for everything; at most one gate container, and none while another session's gate runs (`docker ps` first); never the full `self-qualification` lane; remove containers by exact name only.

Use editor tools only. Keep README, DESIGN-GUIDE and CONSUMERS in sync. Trailer: `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.

## BLOCKED rule
If a named contract cannot be met, or a forbidden file is needed: STOP, write `BLOCKED: <reason>` to `W8-LOG.md`, commit, and exit. BLOCKED counts as success. Triggers:
- W2's `cmd_analyze` or `[lanes.analysis]` differs from the Context;
- 100% coverage would need a pragma;
- a pragma line shifts;
- O6 would need a classification change;
- W4's receipt is absent.

## Review and report
- **Review:** a fresh-session adversarial review, never a fork. It adds one combined-axis attack, for example a parseable torn tail that is a `run` at another commit, placed after a valid baseline.
- **`W8-LOG.md`:** the commits, `git diff --stat`, the table `item | owner | oracle | test id | controlled break | failures`, the gate markers (read separately), and residuals.
- **Checkpoint:** ARM at ~120k tokens or ~60 calls. CUT only at a green, committed item.
