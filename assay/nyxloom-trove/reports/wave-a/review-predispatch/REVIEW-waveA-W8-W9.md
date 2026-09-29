# Pre-dispatch adversarial review: Wave A W8 (B111) and W9 (B108 phase 1)

**Reviewer:** a fresh session (Claude Opus 5.5). I did not write any of the text under review, and this is not a fork.
**Worktree:** `.worktrees/assay-b110-landing`, HEAD `03cd67f3`. Paths are relative to `assay/`.
**Baseline fact:** `f27fbdea..HEAD` changes no file under `src/`, `tests/`, `tools/` or `assay.toml`. Every anchor "at `f27fbdea`" is therefore also an anchor at HEAD.
**What I ran:** reads, `grep`/`sed`, and one `python3 -c` (`Path.with_suffix(".resources.json")`). No pytest, no gate, no container, no edits.
**Scale (the same as round 1):**
- **BLOCKER:** a contradiction between normative sources that the Sonnet implementer cannot resolve without making a design choice.
- **MAJOR:** an open design choice, an unsound or hollow oracle, or a red gate the brief does not prepare for.
- **MINOR:** accuracy or clarity.

---

## 0. Anchor verification (61 checked, all at HEAD)

| Brief | Anchors | Result |
|---|---|---|
| W8 | `cli.py` :1571, :1651 (`commit = git.head_rev`), :482 (`main` dispatch); the four `cli._cmd_plan(..., io.StringIO())` calls at `test_b105_cli_boundaries.py` :409/:416/:433/:443; `liveness.py` :94 (pragma), :1394-1398 (stale tuple), :1411 (`try:` before `_popen`), :1484 (`_monitor`), :1536 (`return`), :1705 (`self._sleep`); `runner.py` :4427 (the only production `LivenessRunner`); `mutation.py` :148 (pragma), :2906 (`started_monotonic`), :2999-3027 (integrity `try`), :3058 (`run = _MutantRun(`), :3071-3082 (non-killing replay returns `None`); `test_liveness_runner_monitor.py` :100 (`_clear_resource_snapshot`), :119-150 (`_runner`), 28 `cpu_reader=`; `test_b106_reuse_and_witness.py` :642-655 (UNSUPPORTED pattern); `test_cli_run.py` :316 (R0+R2 `/bin/sh` lane); `test_liveness.py` :581/:601/:656/:803; CONSUMERS :2307-2312, :2648 (`test` row), :2652 (`candidate` row); README :52-58; DESIGN-GUIDE :484, :1972, :3406 | All correct. The pragma *check* that cites them is wrong (W8-2). |
| W9 | `cli.py` :136 `__all__`, :683 `_resolve_declared_adapters`, :1571-1887 `_cmd_plan`, :1821-1829 row dict; `mutation.py` :158 `__all__`, :202 `UNSUPPORTED = "UNSUPPORTED"`, :1023 `candidate_id`, :1273 `_valid_hung_resource_evidence`, :1644 (the call, after the judge check at :1642), :1696 `_execution_from_state_record`, :2602-2615 `judge` (None iff no state root), :2694-2700 `candidates` event; `runner.py` :160 `__all__`, :3540 `_resolve_declared_base` (`remaining` defaults to None), :4523-4530 (state root only when `resume or shard`); draft `campaign.py` :22/:1101/:1116 (`median`), :1032-1035 (modes), :233-268 (`_lane_plan`), :260 (`runner._resolve_declared_base`), :376 (`assay_cli._resolve_declared_adapters`); draft test :251; CONSUMERS :2649 (`candidates` row), :4030 (B100) | All correct. |

**Re-resolution after W2/W3/W4.** Both briefs say how to find their anchors after files move: W8 :13 says "resolve each by file name, then by function", and W9 :14 says "resolve by name". W3's `expected_dir()` puts every new judge test file the two briefs name under `tests/core/`, which matches the paths they give. None of those file names contains a language word.

**The CD20 question (byte-identical plan output vs. W8's `commit`/`tree`).** There is no contradiction in the text. W8 runs first and adds `commit`, `tree` and the stderr hint. W9's extraction must preserve all three and adds the two row digests. I grepped every existing plan test (`test_mutation_progress_budget_plan.py` :1544/:1653/:1740/:1822/:1859/:1870/:1884/:1950, `test_b106…` :605-655, `test_cli_provenance…` :680, `test_cli_run.py` :984, `test_b105_cli_boundaries.py` :409-443). None pins the payload key set, a row key set or an empty stderr on an `ok` plan. The `candidates`-event pin at `test_mutation_progress_budget_plan.py:130` is `{**events[2], …}`, which tolerates extra keys. So the existing tests do pass unmodified under W8 and then W9. The real problem is that "unmodified tests pass" is too weak to prove the extraction (W9-2).

**Round-1 findings.** F-R0-1/2/3/8 and F-P8-1 through F-P8-7 and F-P8-9 are fixed in these briefs, and none is reintroduced. F-P8-8 is only partly applied (X-2).

---

## 1. W8: `W8-measurement.md`

### W8-1 MAJOR: the B105 branch floor for the new judge helpers has no covering oracle, and the `_pid_cpu_ticks` refactor can break an existing test

**Where:** R (`tree_sample`, `read_resource_sidecar`, `first_event_times`), S (`baseline_phase_durations`), and Work step 1.

**Evidence:**
- W8 lists three tests for `tree_sample`: `os.getpid()`, a dead pid, and O7b.
  - O7b's helper reaps its child before it prints `ready`, so the helper has no child when it is sampled.
  - The pytest process has no guaranteed child.
  - So no listed test enters a separate walker's `while stack:` body. That leaves uncovered: the task-API fallback, the per-child `except OSError: continue`, the `if pid in visited: continue` branch, and the per-child fallback to the ppid scan (the four branches of `liveness.py:606-645`).
- `read_resource_sidecar` has five `None` branches. Only "absent" is reached, via O7's raising-`popen` call.
- `first_event_times` has "missing" and "not finite" branches, and `baseline_phase_durations` has "missing, non-numeric, bool, negative or non-finite → None" branches (P0 packet). No oracle feeds any of these inputs, and O9's fixture has only valid durations.
- The preflight's R1 floor is 100% line and branch. It will go red, and the brief gives no test list for these cases.
- A separate hazard: `test_liveness_proc_helpers.py:152` monkeypatches `liveness._pid_cpu_ticks` with `lambda pid: 7` and asserts exactly `14 / CLK_TCK`. If the "one `/proc` read shared with `_pid_cpu_ticks`" refactor makes `tree_cpu_seconds` call a new reader instead of the module-global `_pid_cpu_ticks`, that test fails even though "`tree_cpu_seconds` is unchanged".

**Fix text (replace the `tree_sample` bullet under R, and append to Work step 1):**
> - `_pid_stat(pid: int) -> _PidStat` (a private `NamedTuple` of `ticks`, `child_ticks` and `rss_pages`) reads `/proc/<pid>/stat` once, using the `:560-561` split idiom and fields `[11]+[12]`, `[13]+[14]` and `[21]`.
> - `_pid_cpu_ticks(pid)` becomes `return _pid_stat(pid).ticks`. Its name, signature and raise contract are unchanged.
> - Extract the body of `tree_cpu_seconds` verbatim into `_walk_tree(root_pid: int, read: Callable[[int], T]) -> list[T]`. It returns the per-pid `read` values in visit order: the root first (and allowed to raise), children read before their own children are listed, `visited`, and both fallbacks.
> - `tree_cpu_seconds(root_pid)` becomes `return sum(_walk_tree(root_pid, _pid_cpu_ticks)) / os.sysconf("SC_CLK_TCK")`. It names `_pid_cpu_ticks` at call time, so `test_liveness_proc_helpers.py:152`'s monkeypatch still applies.
> - `tree_sample(root_pid)` becomes `stats = _walk_tree(root_pid, _pid_stat)`, returning `TreeSample(sum(s.ticks + s.child_ticks for s in stats) / CLK_TCK, sum(s.rss_pages for s in stats) * os.sysconf("SC_PAGE_SIZE"))`.
> - The existing walker tests (`test_liveness_proc_helpers.py:62-190`) then cover the shared walk. The whole `test_liveness_proc_helpers.py` file must pass unmodified.
>
> **Additional unit tests in `tests/core/test_mutation_resource_evidence.py`, each asserting a returned value:**
> - `read_resource_sidecar` returns `None` for: an absent file; the bytes `b"\xff"`; `"[]"`; `{"format": 2}`. It returns the dict for a valid 5-key file.
> - `first_event_times` returns `(None, None)` for `None` and for an absent path. It returns `(t0, None)` when the owner wrote no `test` record. It returns `None` for a `t` that is `true`, `"1"` or `1e999`.
> - O9's fixture also carries a setup `duration_s: true`, a teardown `-1.0` and a final call with no teardown. Each of these gives `None`.

### W8-2 MAJOR: the pragma check cannot pass as written, and P0's rule on where imports go was dropped while `first_event_times` needs a new one

**Where:** Scope, "Pragma lines", and the BLOCKED trigger "a pragma line shifts".

**Evidence:**
- `grep -n "pragma: no cover"` prints only `liveness.py:94` and `mutation.py:148`. `[94, 95]` and `[148, 155, 156]` are the *excluded line* lists in `tests/fixtures/b105-coverage-exclusions.json`, not grep hits. A literal implementer sees a mismatch at the first commit.
- `liveness.py` imports no `math` (see :70-90). A finiteness check (R: "not finite") written with `math.isfinite` invites an alphabetical `import math` at :72. That moves the pragma to :95 and makes the forbidden fixture stale, which is BLOCKED or a red preflight.
- P0's Scope told the implementer where new imports go. W8 voids that text ("the rest of P0 is void") and does not restate it.

**Fix text (replace the "Pragma lines" bullet):**
> **Pragma lines.** Before each commit:
> 1. `grep -n "pragma: no cover" src/assay/liveness.py src/assay/mutation.py` prints exactly two hits: `liveness.py:94:` and `mutation.py:148:`.
> 2. `sed -n 95p src/assay/liveness.py` prints `    from .runner import CommandPlan`.
> 3. `sed -n '155,156p' src/assay/mutation.py` prints the two `from .adapters.base …` / `from .runner …` lines.
>
> Add no line above those: `NamedTuple`, `Callable` and `Mapping` are already imported in both files. A new stdlib import for W8 (`math` in `liveness.py`) is a function-level `import math` as the first statement of the function that needs it.

### W8-3 MAJOR: O14's harness contradicts itself

**Where:** C, "Tests", O14 (imported from P0 W5 step 5).

**Evidence:**
- O14 must run `assay plan <lane>` and `assay run <lane>` through `main([...])`, but is also told to use `judge.make_lane`/`make_r2_judge` and a copy of `_seed_pytest_mutation`.
- `make_lane` returns an in-memory `Lane`. `main(["plan", …])` loads a TOML file (`cli.py:1578`, `_resolve_lane_file`), and there is no `--file` for an in-memory lane.
- The `_patch_lane_file` shortcut (`test_b105_cli_boundaries.py:44-46`) yields a `SimpleNamespace` with no `dirty_ignore`/`snapshot_limits`, so discovery raises `AttributeError`.
- The implementer has to invent a harness.

**Fix text (replace the O14 bullet):**
> O14 goes in `gate/tests/test_b105_report_check_real_plan.py`.
>
> **Setup.** Build a repository with `gate.tests.support.judge.git_repo`:
> - `.gitignore` contains `__pycache__/`, `.pytest_cache/` and `.assay/`.
> - `src/mod.py` and `tests/test_behavior.py` are copied from `_seed_pytest_mutation` (`tests/core/test_b106_reuse_and_witness.py:661-677`), with one more compare-swap site (`return value > 0 and value < 10`) so that there are two candidates.
> - A committed `assay.toml` holds one lane `package`:
>   - `rigor = ["R0","R2"]`;
>   - `argv = [sys.executable,"-m","pytest","tests","-q","-p","no:cacheprovider"]`;
>   - `judge.language = "python"`, `source_roots = ["src"]`, `base = "<seed base>"`;
>   - `[judge.mutation]` `jobs = 1`, `max_mutants = 10`, `operators = ["python:compare-swap"]`.
>
> **Steps.** Run through `assay.cli.main`: `plan package --file <toml>` (stdout captured and parsed), `run package --file <toml> --verdict-json <tmp>/v.json`, and `run package --file <toml> --shard 0/2 --verdict-json <tmp>/s.json`.
>
> **Assertions.** `check_campaign_scope(v, plan)` does not raise, and `check_campaign_scope(s, plan)` raises `ValueError` (refusal 5).
>
> Do not use `make_lane`, `make_r2_judge` or `_seed_pytest_mutation` itself.

### W8-4 MAJOR: O8 is hollow unless the first run writes state records, and "state `resources` equal the event" has no rounding rule

**Where:** O8 and R, "`mutation.py`" (the state record).

**Evidence:**
- `runner.py:4523-4530` sets a state root only when `resume or shard_index is not None`. A `runner.run_lane(..., state_dir=S)` without `resume=True` writes no record, so "each state record has the same values under `resources`" iterates over nothing and passes.
- Separately, the event rounds `cpu_seconds` and the `phase_seconds` values to 3 decimals, but "`resources`: {…same four}" does not say whether the record stores the rounded or the raw values. A raw record then fails the equality assertion.

**Fix text:**
> - O8's first `runner.run_lane` call passes `resume=True`, `state_dir=tmp_path / "state"` and `progress_artifact=tmp_path / "progress.jsonl"`, all outside the repository.
> - Before comparing, assert that `len(list(state_dir.glob("*.json")))` equals the number of `candidate` events, and that the number is 2.
> - The worker loop builds the four-key dict once (rounded exactly as the event requires) and uses the same object for the event keys and for the record's `resources`.

### W8-5 MINOR: `plan_estimate` details left to the implementer

**Where:** P, steps 1-5.

**Evidence:**
- `evidence._read` returns `(text, artifact)` (`analysis.py:58-63`), not a string.
- The read order of the two files decides "first failure wins", and it is not stated.
- `_OBJECT_ID` is written with a trailing `\Z` and no rule for how to call it.
- A malformed plan JSON and non-string `started`/`ended` values have no message.
- The line-number base is unstated, and it is not said whether a kept tail is validated.

**Fix text (append to P):**
> - Step 1 reads the plan, then the progress. It uses `text, _ = evidence._read(path)`.
> - `_OBJECT_ID.fullmatch(value)`.
> - A plan whose JSON fails `evidence._json` → `plan: malformed JSON`.
> - Progress line numbers `n` and `L` are 1-based.
> - A kept (parseable) tail is validated like any other line.
> - A `started` or `ended` that is not a `str` gives the "invalid interval" message.

### W8-6 MINOR: two oracles are not discriminating enough

**Where:** O-P2 and O-P5.

**Evidence:**
- O-P2's negative, "the FAIL record", passes if the FAIL record also spans 40 s.
- O-P5's "progress at its commit" can be written by hand, which leaves the real plan→progress seam untested (CD25: "reads `assay plan`'s JSON … and a progress stream").

**Fix text:**
> - O-P2: the FAIL `baseline` record spans 25 s and the later PASS record spans 40 s.
> - O-P5: the progress comes from `main(["run", "package", "--file", toml, "--progress", p])` on the same lane. Assert exit 0, `candidates == candidate_count` and `baseline_s > 0`, and no other value.

### W8-7 MINOR: the hint is wrong for lanes that declare a duration

**Where:** H, `PLAN_ESTIMATE_HINT`.

**Evidence:** `cli.py:1791-1797` uses the *declared* `budget_per_candidate` when it is a duration. Only omitted, `"auto"` and `"none"` fall back to 60 s. The hint says "a placeholder per-candidate figure" for every `ok` plan, and O-H1 pins that wording.

**Fix text:**
> `PLAN_ESTIMATE_HINT = "assay plan: estimated_serial_seconds and estimated_wall_seconds come from the declared budget_per_candidate (a 60 s placeholder when it is omitted, auto or none), not a measurement; for a measured projection run: assay analyze plan-estimate --plan-json PLAN --progress PROGRESS [--workers N]"`

### W8-8 MINOR (combined-axis attack: segment selection × a resumed run × a stream shared by two lanes)

**Input:**
- One `--progress` file holds:
  1. a `self-qualification-preflight` run at C (baseline PASS, 548 s);
  2. a `unit` run at C (baseline PASS, 4 s);
  3. a resumed preflight run at C whose baseline FAILs.
- The plan is the self-qualification plan at C with 3,760 candidates.

**Result:** step 4 selects segment 2 (the last segment with a baseline) and prints `baseline_s: 4.0` and `projected_worker_hours: 4.178`. It exits 0, and its output names no lane or source (CD35), so the error cannot be seen.

**Why:** P0 selected by lane. CD25 dropped the flags, and the brief keeps no lane check. A cross-lane measurement (a preflight baseline for the R2 plan) is intended, so the lane cannot be matched to the plan. It can be required to be *single*.

**Fix text (step 3):**
> The distinct `lane` values of all `run` records (null ignored) must number at most one, else `progress: runs of more than one lane ({sorted lanes}); pass a single-lane progress file`.

Add this to O-P4. No output key changes.

### W8-9 MINOR: the sidecar `finally` catches too little

**Evidence:** `except OSError: pass` does not catch a `TypeError`/`ValueError` from serializing a value an injected sampler returned. Such an exception raised inside `finally` replaces the in-flight `LivenessHungExpired`/`TimeoutExpired` or the return value, which changes the bucket. P0 said "Diagnostics never fail a candidate".

**Fix text:** `except (OSError, TypeError, ValueError): pass`. It is one clause, so O7's raising `os.replace` still covers its arc.

### W8-10 MINOR: the O6/O7 harness mechanics are unstated

**Evidence:**
- `_runner` hard-wires `sleep=clock.advance` and builds the `cwd` with `mkdir()`, so calling it twice fails.
- O6's spy must log `sleep`.
- O7's second call needs a `popen` that raises.
- O6's "normal exit" row, scripted as `_ScriptedProc(returncode=0)`, returns on tick 0 and never calls the sampler, so that row cannot discriminate.

**Fix text:**
> - `_runner(...)` gains `sampler=None` and `sleep=None` (default `clock.advance`), both passed through.
> - O7's second call constructs `liveness.LivenessRunner` directly, with the same `events_dir`, a `popen` that raises `OSError`, and the same `cwd`.
> - O6's normal-exit row finishes on the third tick (`_scripted_events(..., finish_at=(2.0, 0))`), so the sampler runs at least twice.

### W8-11 MINOR: the header omits W7

**Evidence:** W7 and W8 both edit `gate/tests/test_self_lane.py` and `assay.toml`. The plan (§2) and REBASE-P0-P2 say "Stage 3: W7, then W8 → W9". The W8 header cuts after "W2, W3, W4 and W6".

**Fix text:** "cut … after W2, W3, W4, W6 and W7 have merged".

### W8-12 MINOR: the docs text contradicts "the rest of P0 is void"

**Evidence:** Work 3 says "P0's 'Docs sync' content". That text adds `--baseline-from`, `estimate_provenance` and `full_suite_central_*` to CONSUMERS and CHANGES. Also, the README line is parsed by `shlex.split(line)[2:]`, so a usage-style `[--workers N]` would fail the docs test.

**Fix text (Work 3):**
> **Exact lines:**
> - README block: `assay analyze plan-estimate --plan-json plan.json --progress .assay/progress-self-qualification-preflight.jsonl --workers 3`.
> - CHANGES Added: "`assay analyze plan-estimate`; `assay plan` JSON `commit`/`tree` and a stderr hint; `candidate` progress `cpu_seconds`/`peak_rss_bytes`/`phase_seconds`/`startup_seconds` and state `resources`; baseline `test` `setup_s`/`teardown_s`."
> - CHANGES Fixed: "liveness test leak; CONSUMERS 'upper bound' claim".
> - CHANGES Testing: "G1–G5".
>
> Never mention `--baseline-from`, `estimate_provenance` or `full_suite_central_*`.

### W8-13 MINOR: `first_event_times` leaves the owner rule open

**Evidence:**
- "Using `_session_owner_pid`" does not say what happens when the owner is `None` or `test` records lack a pid.
- `_selected_test_events` (`liveness.py:725-750`) then keeps the merged legacy view.
- A separate rule would make `to_first_test` disagree with `tests_completed`.

**Fix text:**
> `first_event_times` returns:
> - the `t` of the first `session_start` record from `_iter_events`;
> - the `t` of `_selected_test_events(events_path)[0]` when that list is non-empty.
>
> Each is `None` when missing or not a finite int/float (bool excluded).

### W8-14 MINOR (optional, carver): the checker could bind the plan's new `commit`/`tree`

**Evidence:**
- P0's W5 rationale says "The plan payload has no lane or commit field". After H, that is false.
- A two-line refusal would bind the plan to the report without relying only on the gate script's ordering (O12).

**Fix text (refusal 9 addition):** `plan["commit"] != --expected-commit or plan["tree"] != --expected-tree` → `ValueError("plan commit/tree differ from the expected source")`. Add one O11 case.

---

## 2. W9: `W9-campaign-analysis.md`

### W9-1 BLOCKER: the output document shape is not closed; the draft schema contradicts P8's optional verdict

**Where:** A (port), "Imported from P8: Output schema/Counts/ETA/Status", and Work step 4.

**Evidence:**
- The draft schema's top-level `required` includes `verdict` (`verified: {"const": true}`), `evidence.verdict` (a required fingerprint) and `coverage.status ∈ {not_judged, from_verified_verdict}`. P8 makes `--verdict` optional and adds two modes with no verdict, but never says what those three fields become.
- P8 names `qualifying`, `complete_blockers`, `torn_final_record`, `unresolved`, `adverse`, `reclassified`, `state` and `projection` without placing them.
- It defines exactly one `complete_blockers` string (`command_exit_not_observed`). W9 adds a second. The other row-4 conditions (no verdict, inventory not exhausted, terminal disagreement, coverage not reverified or `not_supplied`, `unreconciled > 0`) have no string.
- The draft's `timing` (`status`, `sample`, `remaining_seconds`, `parallel_jobs`) conflicts with P8's `timing` (`eta_reason`, `sample_count`, `eta{…}`, `measurement_window`).
- The draft's `excluded_candidate_counts` keys (`non_full_execution`, `resumed_without_current_duration`, `not_yet_reported`) conflict with P8's four keys.
- With `additionalProperties: false`, the Sonnet implementer must choose a consumer-visible JSON shape.

**Fix text (new subsection "Output shape (closed)", carver to record as CD39):**
> - **Non-error document.** Top-level keys are exactly the draft's 15 plus `qualifying` (`true`), `complete_blockers` (array, `[]` iff `status == "complete"`), `torn_final_record` (bool), `adverse`, `unresolved` (object or `null`), `reclassified` (array), `state` (object, or `null` without `--state-dir`) and `projection` (object or `null`). `execution_mode_counts` goes under `campaign` and replaces `freshly_witness_replayed` and `fully_executed`.
> - **Without `--verdict`:**
>   - `verdict` is `null`;
>   - `evidence.verdict` is `null`;
>   - `coverage` is the draft object with `status: "not_judged"`, `r1: null`, `r2_floor: null`, `artifact_status: "not_supplied"`, `branch_arc_detail_status: "unavailable_without_artifact"` and `missing_branch_arcs: null`.
> - **`complete_blockers` vocabulary.** It is sorted, and each string is added when its row-4 or row-3 condition holds:
>   - `no_verdict`
>   - `command_exit_not_observed`
>   - `inventory_not_exhausted`
>   - `terminal_disagrees`
>   - `coverage_not_reverified`
>   - `state_unreconciled`
>   - `unverified_hung_records`
>   - `lane_timeout_or_unstarted`
> - **`timing`** is exactly P8's ETA object: `diagnostic_only`, `eta_reason`, `sample_count`, `minimum_sample_count`, `measurement_window{first,last,run_id}`, `eta` and `excluded_candidate_counts{crashed,hung,budget_exceeded,resumed}`. The draft's other `timing` keys are deleted.

### W9-2 MAJOR: the combined-axis attack (B107 hung evidence × resume × counting) exposes an unspecified rule order

**Input A (verdict mode):**
- The plan is `{a, b}` at C.
- `--state-dir S` holds:
  - `a`: judge J, `hung`, no `liveness_resource_evidence`. It is pre-B107, written by an earlier `--resume` run whose version string is the same (`_tool_version()` goes into J).
  - `b`: judge J, `killed`.
- The latest run is a non-resume full run. It has no state root, so no record is written and there is no `judge_sha256` in `candidates`. Its events are a `killed` and b `killed`, the verdict is PASS `killed:[a,b]`, and `--command-exit 0`.
- The C25 fallback pairs b with its event, so the current judge is J.
- Record a is then "at the current judge, in selection, hung, invalid", so W9 B says it goes to `unverified_hung`, not counted.
- P8's O4 says "a state record whose bucket disagrees with the verified verdict bucket → `evidence_error`".
- The brief orders neither. One implementation exits 2, another exits 0, and both pass O4 and O22.
- CD37 ("never stricter than the judge") requires exit 0: the judge's loader (`mutation.py:1642-1647`) would reject a and never use it.

**Input B (no verdict, the event is written before the record):**
- The latest `--resume` run has `resume{resumed_total:0, rejected_total:1}`, `candidates{judge_sha256:J}`, and an event for a that is `hung` *with* valid evidence.
- Record a on disk is still the old invalid one.
- "A selected candidate left unresolved this way … `complete_blockers` gains" is the only guard. It is unclear whether a is in `unverified_hung`, whether it is counted through its event, and whether the blocker fires.

**Fix text (append to B):**
> **Order, in every evidence mode:**
> 1. Shape validation.
> 2. The C25 current-judge rule over all shape-valid records.
> 3. Unverified-hung removal: the record leaves the store view.
> 4. Only then P8's O4 verdict comparison and reconciliation steps 1-5.
>
> **Consequences:**
> - A removed record is never an `evidence_error` and never `reclassified`.
> - `unverified_hung` lists it whether or not its candidate is resolved elsewhere.
> - The candidate still counts through its verdict bucket or its latest-run event.
> - `unverified_hung_records` enters `complete_blockers` only when a listed candidate has neither a verdict bucket nor a latest-run event.
>
> **O22 gains Inputs A and B:**
> - A: `complete`, exit 0, `unverified_hung.count == 1`, no blocker.
> - B: a counted as `hung` with `outcome_source == "progress"`, `liveness_evidence_status == "valid"`, `unverified_hung.count == 1`, and `unverified_hung_records` not in `complete_blockers`.

### W9-3 MAJOR: O15 is hollow and inconsistent with its own lane shape

**Where:** the O15 override and P8's O15.

**Evidence:**
- The override names `--state-dir` and `--progress` but not `--resume`. With no `--resume` and no shard, `runner.py:4523-4530` sets no state root, so the rows "validate the real state records" of an empty store.
- P8's O15 also requires `--coverage <artifact>`. For this R0+R2 lane (no R1), the draft raises "coverage artifact was supplied for a lane with no R1 coverage declaration" (draft :310-312), which is `evidence_error`.
- P8's B106 `--reuse-from` replay run cannot replay on the `/bin/sh` lane: `supports_sequential_pytest` accepts pytest argv only (`mutation_witness.py`).
- Artifacts inside the repository make the next run `DIRTY_TREE` (`cli.py:845-877`).

**Fix text (replace the O15 override):**
> **O15 fixture.**
> - A subprocess-git repository with the `tests/core/test_cli_run.py:316` lane.
> - `src/mod.py` is `def f(x, y):\n    return x > 0 and y < 1\n` against a base where it is `return 0`.
> - `argv = ["/bin/sh","-c","grep -q 'x > 0' src/mod.py && grep -q 'y < 1' src/mod.py"]`, giving two candidates.
> - Every artifact is under `tmp_path / "ev"`, outside the repository.
>
> **Run 1:** `main(["run","package","--file",T,"--resume","--state-dir",S,"--progress",P,"--verdict-json",V1])` exits 0, and `S` holds exactly 2 `*.json`.
>
> **Run 2:** the same with `--rejudge <first plan row id>` and `--verdict-json V2` (it appends to P).
>
> **Analysis:** `campaign package --file T --expected-commit HEAD --progress P --verdict V2 --state-dir S --command-exit 0` without `--coverage`. Assert:
> - `status == "complete"`, exit 0, and `coverage.artifact_status == "not_applicable"`;
> - `campaign.completed_total == 2` and `campaign.rejudged_total == 1`;
> - `runs[0]` has 2 candidate events, and `runs[1]` has 1 candidate event with `resumed_total == 1`;
> - `state.judge_sha256_source == "candidates-event"` and `state.counted == 2`.
>
> The `--reuse-from` replay half is dropped from O15. Record it as a residual: it needs a pytest lane.

### W9-4 MAJOR: O21 cannot detect a wrong J1 extraction

**Where:** J1 and O21.

**Evidence:**
- J1 makes exactly two substitutions: `allow_dirty`, and `resolve_reuse_command` replacing `reuse_source is not None`.
- No existing test runs `assay plan --allow-dirty`: `grep '"--allow-dirty"' tests/` finds nothing. So a `_cmd_plan` that passes `allow_dirty=False` passes O21.
- The only `plan --reuse-from` tests (`test_b106…:605-655`) assert `sequential_pytest_supported is False`, because the fixture's `pytest.ini` has `-n 2`. An inverted `if not resolve_reuse_command:` resolves the command on the wrong branch and still yields `False` there. It passes O21, and both branch arcs remain covered, so R1 stays 100%.

**Fix text (add to O16a in `tests/core/test_cli_plan_jobs.py`):**
> **(vii)**
> - Setup: a tracked non-lane file edited and uncommitted.
> - `main(["plan", lane, "--file", T, "--allow-dirty"])` gives `payload["worktree_integrity"]["overridden_dirty_paths"] == [that path]`.
> - Negative: a hard-coded `allow_dirty=False` raises `DIRTY_TREE`.
>
> **(viii)**
> - On a sequential pytest lane (the `_seed_pytest_mutation` shape, no `-n`, clean tree), `main(["plan", lane, "--file", T, "--reuse-from", prior_v13])` gives `payload["reuse_from"]["sequential_pytest_supported"] is True`.
> - Negative: an inverted or dropped `resolve_reuse_command` gives `False`.
>
> Add `tests/core/test_cli_plan_estimate_hint.py` (W8 O-H1, which pins `commit`/`tree` and the hint) to the O21 list and to Gate step 1.

### W9-5 MAJOR: O16b has no mechanism

**Where:** O16b.

**Evidence:**
- "A plan row with a tampered `source_sha256` makes a matching state record an `evidence_error`, naming the file."
- No imported P8 check compares a record's fields with its plan row. Checks 1-9 validate the record against itself, and membership goes by `candidate_id`.
- The only rule that sees a tampered row is P8's "a row whose recomputed ID ≠ `id`", and it is not about a state file. The implementer must invent the check and its message.

**Fix text:**
> **Plan-row identity check.** It runs right after `_lane_plan` and before any progress or state is read. For every row:
> - all six identity keys are present;
> - both digests match `^[0-9a-f]{64}$`;
> - `candidate_id_from_fields(**{k: row[k] for k in IDENTITY_KEYS}) == row["id"]`.
>
> Otherwise → `evidence_error`, message `plan row {id}: identity inputs do not reproduce its id`.
>
> **O16b.** `_install_plan` with one row's `source_sha256` flipped gives that exact message and exit 2, with no state dir needed. Delete "a matching state record … naming the file".

### W9-6 MAJOR: W9 does not plan how the ported draft reaches the `[lanes.analysis]` 100% floor

**Where:** A, Gate step 2, and the BLOCKED trigger "100% coverage of `campaign.py` … needs a pragma".

**Evidence:**
- `[lanes.analysis]` copies the preflight lane: `fail_under = 100.0` and `require_branch = true` (`assay.toml:165-167`, W2 step 6).
- The draft has 132 `raise ValueError` sites and 236 `if`/`elif` lines, and only 5 tests. P8 steps 3-8 add more.
- W9's oracle table names about 20 behaviours. Nothing tells the implementer to reach each refusal, or how.
- This is a count of source sites, not a coverage prediction (§3b F). The gap will show only at the gate, after hours of work, and the result is either BLOCKED or an unbounded grind.

**Fix text (Work step 3a, before the schema step):**
> Add `test_every_campaign_refusal_is_reachable`, parametrized over a literal table `(case_id, fixture_mutation, expected_message_fragment)`:
> - One row per `raise ValueError` in `campaign.py`.
> - Each row's mutation is applied to the complete-campaign fixture.
> - The LOG maps each raise line to its `case_id`.
>
> Measure coverage locally before the registered gate: `nice -n 19 ionice -c3 python -m pytest analysis/tests -q -p no:cacheprovider --cov=analysis/src/assay_analysis --cov-branch --cov-report=json:<tmp>`. Read the arcs with coverage's API, not the report's Missing column.
>
> A branch reachable only by a filesystem race (for example the draft's `fstat` mismatch at :90-94) is reached by monkeypatching `campaign.os.fstat`, not by a pragma.

### W9-7 MINOR: `phase_seconds` must be nullable

**Evidence:**
- Override 3 says "exactly 4 keys".
- But P8's "Absent always means `null`" applies to rows with no event: a resumed candidate in verdict mode, or a stream written before W8.
- A schema without `null` fails O13, and a Sonnet "fix" of `{}` or zeros violates "never 0".

**Fix text:** "`phase_seconds`: an object with exactly the 4 keys, each a number, **or `null`**. `startup_seconds`: a 2-key object whose values are a number or `null`, **or `null`**."

### W9-8 MINOR: two O16a clauses are fragile or ambiguous

**Evidence:**
- (iii) "`assay plan --shard 0/2` lists fewer": `select_mutation_shard` assigns by `blake2b(id) % count` (`mutation.py:1746-1766`). A fixture whose ids all fall in shard 0 makes (iii) red forever. For 2 candidates that happens with probability 25%.
- (vi) "the committed blob's sha256" can be read as git's blob object id.

**Fix text:**
> - (iii) Pick `i` so that `len(select_mutation_shard(ids, index=i, count=2)) < len(ids)`, where `ids` are the unsharded row ids. Assert `len(_discover_plan_jobs(...).jobs) == len(ids)`, and assert that `plan --shard i/2`'s `candidate_count` equals that shorter length.
> - (vi) `source_sha256 == hashlib.sha256(subprocess.check_output(["git","-C",repo,"show","HEAD:src/mod.py"])).hexdigest()`.

### W9-9 MINOR: which coverage status comes first

**Evidence:** P8's Topology says "without `--coverage` … `not_supplied`, which blocks `complete`". For a lane with no R1, the draft (:310-317) returns `not_applicable` first. Only O15's override implies which order wins.

**Fix text:** "A lane with no R1 coverage declaration always has `coverage.artifact_status == "not_applicable"`, which never blocks. Supplying `--coverage` for it is `evidence_error` (the draft's rule). `not_supplied` applies only to R1 lanes."

### W9-10 MINOR: the Work order and the green-per-step rule

**Evidence:**
- Step 1 (port) already calls `assay_cli.plan_jobs`, `resolve_declared_adapters`, `mutation.execution_from_state_record` and `valid_hung_resource_evidence`, which step 2 (J3/J4) creates.
- It stays green only because every draft test goes through `_install_plan` with R0+R2 lanes.
- Gate step 1 also misses the plan tests in `test_b106_reuse_and_witness.py` and `test_cli_provenance_and_request_base.py`.

**Fix text:** swap Work steps 1 and 2 (judge J1-J5 first, then the port), and add both files at their W3/W4 paths to Gate step 1.

### W9-11 MINOR: two port details

**Evidence:**
- Port edit 3 returns `{"status": "unsupported"}`. P8's status row 1 makes an `"UNSUPPORTED"` plan `evidence_error` ("lane has no mutation plan"), but the brief never says where that refusal is raised.
- J1 never says that `_discover_plan_jobs` returns after both `with` blocks close.

**Fix text:**
> - `campaign()` raises `ValueError("lane has no mutation plan")` right after `_lane_plan` when `plan["status"] == "unsupported"`.
> - `_discover_plan_jobs` ends, after both `with` blocks, with `return _PlanDiscovery(commit=commit, tree=tree, jobs=jobs, worktree_integrity=worktree_integrity, reuse_command_plan=reuse_command_plan, reuse_command_cwd=reuse_command_cwd)`.

---

## 3. Cross-package (X)

### X-1 MINOR: CD38's consumer-visible list is incomplete

**Evidence:** CD38 lists the plan's `commit`/`tree`, the hint, the two subcommands and four aliases. Several additive but documented surfaces are missing:
- **Progress events:**
  - `candidate` gains `cpu_seconds`, `peak_rss_bytes`, `phase_seconds` and `startup_seconds` (W8);
  - baseline `test` gains `setup_s` and `teardown_s` (W8);
  - `candidates` gains `judge_sha256` (W9).
- **Plan and state:**
  - the state record gains `resources` (W8);
  - plan rows gain `source_sha256` and `mutated_file_sha256` (W9).
- **Files and CLI:**
  - a new `<events>.resources.json` file in the liveness candidates directory (W8);
  - public `cli.plan_jobs` and `mutation.candidate_identity_fields` (W9).

All are additive, so 7.2.0 stands, but the release list should name them.

**Fix text (append to CD38):** "Also additive: the progress keys listed above, state `resources`, the plan-row digests, the `.resources.json` sidecar, and `cli.plan_jobs` / `mutation.candidate_identity_fields`."

### X-2 MINOR: F-P8-8 is only partly applied

**Evidence:**
- `P6-campaign-deadline.md:382` is annotated, but :354-355 still lists "How the plan-discovery block is extracted" as a P6 degree of freedom.
- `P9-distributed-evidence.md` :9/:91/:95/:198/:713 still point at `src/assay/campaign.py`.

**Fix text (carver, before v14):**
- Delete P6 :354-355.
- In P9, replace `src/assay/campaign.py` with `analysis/src/assay_analysis/campaign.py`.
- Note in P9 that `validate_state_record_shape` must be a public judge name (CD18).

**Checked with no finding:**
- No double extraction: W8 adds lines inside the region, and W9 extracts it once.
- Merge order W8 → W9 is stated in both headers, and W9 blocks when W8's `commit`/`tree` is absent.
- W9's hook reads W8's parser order (`campaign` after `plan-estimate`).
- W9's `__init__.__all__` and targets lists extend W8's.
- CD18: all four aliases are one-line module assignments that run at import time, so B105 R1 covers them trivially, and O16a(v) pins identity with `is`.
- CD4 placement and helper: correct against `liveness.py:1484-1705` and `test_liveness_runner_monitor.py:100-150`.
- CD25's nine keys and exit codes: correct.
- CD34, CD35, CD36 and CD37: consistent, apart from W9-2's ordering gap.

---

## 4. Verdicts

| Brief | Verdict | Must fix before dispatch |
|---|---|---|
| `W8-measurement.md` | **READY-WITH-FIXES** | W8-1 to W8-4 (MAJOR); W8-5 to W8-13 are paste-ready MINORs; W8-14 is optional |
| `W9-campaign-analysis.md` | **READY-WITH-FIXES** | W9-1 (BLOCKER; the carver records the closed output shape as a CD); W9-2 to W9-6 (MAJOR); W9-7 to W9-11 (MINOR) |

**Counts:** BLOCKER 1 (W9-1); MAJOR 9 (W8-1 to W8-4, W9-2 to W9-6); MINOR 17 (W8-5 to W8-14, W9-7 to W9-11, X-1, X-2).
