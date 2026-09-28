# B110-P6 — A persisted campaign deadline, and termination that leaves no candidate behind

| Field | Value |
|---|---|
| Backlog | **B117** (split from B110) |
| Branch | `assay-b110-p6-deadline` from the integration line `assay-b105-evidence-integrity` (after the plan §11.1 reconciliation) |
| Depends on | nothing. It can run in parallel with P0, P2 and P10a. **Merge note:** P3d also edits `tools/self-qualification-gate.sh`. Serial `--no-ff` merges apply; whichever merges second rebases its gate-script hunk. |
| Contract class | **2b.** The file format, CLI, refusals and termination semantics are fixed; the private construction is yours. |
| Implementer | Opus (fresh session) |
| Decisions | **A-473 (plan D9).** Also binding: A-464 (8 h hard stop; an expiry is incomplete infrastructure evidence, never a candidate outcome), A-160, A-193 (monotonic lane clock, remainder sampled at each boundary), A-195. |
| Size | M: `cli.py` (new subcommand, one flag, signal wrapper), `runner.py` (`LaneDeadline` helper, termination flag, `execute_plan` check), `liveness.py` (live process-group registry), `mutation.py` (one kwarg and one check), gate script, run-gate.toml comment, tests, docs |

B110's rule is that a resume, retry or replacement worker consumes the **same** remaining time. None of them resets the campaign clock. Today every `assay run` starts a fresh `LaneDeadline` from the lane budget (`cli.py:1169`). A SIGTERM from the gate's `timeout` also leaves candidate process groups (`start_new_session=True`) running as orphans.

---

## Context to read first

Paths are relative to `assay/`. Line numbers were checked at HEAD `db85f747`.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §0, §3 D9, §7 (the pilot uses `campaign init --campaign pilot … --hours 2`), §9.3, §10.
2. `src/assay/runner.py`:
   - `:210-308` `LaneDeadline` (`start`, `tightened`, `remaining`, `unbounded`).
   - `:321-347` `default_process_runner`: plain `subprocess.run` with no new session, so these children share assay's process group.
   - `:1094-1110` `execute_plan` signature: the single boundary every R0 baseline, R2 candidate, R3 half and probe goes through.
   - `:4526-4541`: the `except mutation.InvalidRejudgeIdError: raise` pattern.
   - `:5385-5410`: the outer `except AssayError` that renders the whole-lane refusal (`refuse_all`).
3. `src/assay/cli.py`:
   - `:255-328`: the `run` parser. Add your flag after `--reuse-from`.
   - `:330-367`: the `plan` parser.
   - `:456-495`: `main`, dispatch and the `except AssayError` handler.
   - `:725-812`: `_cmd_run`, which calls `_resolve_state_dir` at `:804`; see also `_resolve_state_dir` at `:845-878`.
   - `:1124-1171`: `_run_reserved` up to `LaneDeadline.start`.
   - `:1224-1325`: the HEAD read and the **pre-`run_lane` LANE_TIMEOUT refusal path**, including the label-grace `LaneDeadline(expires_at=…)` direct construction at `:1279-1282`.
   - `:1326-1331`: the run header.
   - `:1379-1414`: the refusal shape (`runner.refuse_lane(...)`, `write_verdict`, `_emit_verdict_written`, `_print_run_summary`).
   - `:1490-1540`: the `run_lane` call and its LANE_TIMEOUT handler.
   - `:1571-1790`: `_cmd_plan`, whose discovery (`:1648-1765`) you will extract.
4. `src/assay/mutation.py`:
   - `:1023` `candidate_id`.
   - `:2318-2343`: the selection block, where the plan-digest check goes before shard selection.
   - `:2700-2720`: `_run_attempt`, calling `execute_plan` with `timeout=command_deadline`.
   - `:1244-1270`: the atomic temp-and-replace write pattern for state records.
5. `src/assay/liveness.py`:
   - `:1299-1340`: `LivenessRunner.__init__`, including the `_process_group_killer` injection at `:1332`.
   - `:1360-1440`: spawn with `start_new_session=True` at `:1375`, and the group kill at `:1405-1439`.
6. `tools/self-qualification-gate.sh:11-60, :150-248` and `run-gate.toml:33-62`.
7. `nyxloom-trove/decisions.md`, rows A-160 (`:453`) and A-193 (`:516`).
8. Test precedents:
   - `tests/test_lane_timeout_writes_a_verdict.py`: the refusal verdict for an expired deadline.
   - `tests/test_cli_run.py:586-625`: a real child spinning under `LivenessRunner` via in-process `main()`.
   - `tests/test_state_dir_resume.py`.

## Implementation packet (normative)

### Interfaces and grammar

**1. `assay campaign init`.** This is a new top-level subcommand, `campaign`, with sub-action `init`:

```
assay campaign init --campaign NAME --lane LANE [--lane LANE ...]
                    (--hours H | --expires-at ISO8601Z)
                    [--file assay.toml] [--state-dir DIR ...] [--wheel-sha256 HEX]
                    [--out PATH]
```

- `NAME` matches `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`.
- `H` is a decimal in `(0, 24]`.
- `--expires-at` must be a UTC `YYYY-MM-DDTHH:MM:SSZ` value strictly in the future. `--hours` and `--expires-at` are mutually exclusive, and exactly one is required.
- The default for `--out` is `<project_root>/.assay/campaign-deadline-<NAME>.json`.
- Every `--lane` must exist in the lane file.

The command writes this document atomically (temp file plus `os.replace` in the same directory, mirroring `mutation.py:1244-1270`), with `sort_keys=True, indent=2`:

```json
{
  "schema": "assay-campaign-deadline/1",
  "campaign": "b105-3f391d6e0c1a",
  "commit": "<40-hex HEAD>",
  "git_tree": "<40-hex HEAD^{tree}>",
  "lanes": ["self-qualification", "self-qualification-preflight"],
  "assay_version": "7.2.0.dev3+g3f391d6",
  "wheel_sha256": "<64-hex or null>",
  "plan_sha256": {"self-qualification": "<64-hex>", "self-qualification-preflight": null},
  "created_at_utc": "2026-10-02T08:00:00Z",
  "expires_at_utc": "2026-10-02T16:00:00Z"
}
```

- `lanes` is sorted and duplicate-free.
- `plan_sha256[lane]` is `null` for a lane whose rigor lacks R2. Otherwise it is `mutation.plan_sha256(ids)`, where `ids` is the ordered full candidate-ID list from the **same discovery code path** that `assay plan` uses, with no shard applied.
- `wheel_sha256` is **recorded, not validated**: wheel builds are not guaranteed to be byte-reproducible, and `assay_version` already carries the source identity. Document this in DESIGN-GUIDE.

Invalid documents that `assay run` must refuse, each with `ERROR`/`BAD_LANE_CONFIG`:
- `"schema": "assay-campaign-deadline/2"`: unknown schema.
- `"expires_at_utc": "2026-10-02T16:00:00+02:00"`: not the `Z` form.
- a document missing `plan_sha256`, or one with an extra top-level key, or one with `lanes` not containing the running lane.
- a duplicate JSON key. Parse with an `object_pairs_hook` that refuses duplicates, the same approach as `reuse.py`'s bounded parse.

`init` refusals (exit 2, `ERROR`/`BAD_LANE_CONFIG`, message on stderr, nothing written):
- the output file already exists with a **different** identity (`commit`, `git_tree`, `lanes`, `assay_version` or `plan_sha256` differ). If it exists with the **same** identity, init prints the existing file's path and exits 0 **without changing `expires_at_utc`**. Re-running init never extends a deadline.
- any `--state-dir` already contains a `*.json` record while no deadline file exists. That is the backlog's "state without a matching deadline cannot silently start a new clock". To start a new campaign after an expiry, the operator must move the old state dir aside deliberately.
- a dirty worktree, using the same integrity check `_cmd_plan` uses (`runner._resolve_snapshot_worktree_integrity`), without `--allow-dirty`. `init` has no `--allow-dirty` flag.

**2. `assay run --campaign-deadline PATH`.** Optional. When given, the flow below applies.

**3. `mutation.plan_sha256(candidate_ids: Sequence[str]) -> str`.** SHA-256 over `b"".join(f"{len(i)}:{i},".encode("ascii") for i in candidate_ids)`, which is netstrings in plan order.

**4. `runner.campaign_bounded_deadline(lane_deadline: LaneDeadline, *, expires_at_utc: datetime, wall_now: datetime, monotonic_now: float) -> LaneDeadline`.**
- Pure.
- Returns `LaneDeadline(expires_at=min(lane_deadline.expires_at, monotonic_now + (expires_at_utc - wall_now).total_seconds()), monotonic=lane_deadline.monotonic)`.
- The result may lie **in the past**. That is deliberate: the existing HEAD read then raises LANE_TIMEOUT, and the existing refusal path writes the verdict.
- `wall_now` and `expires_at_utc` must be timezone-aware UTC; anything else is a `ValueError`.

**5. Termination.**
- `runner.request_termination()` sets a module-level `threading.Event`, `_TERMINATION`.
- `runner.termination_requested() -> bool`.
- `runner._reset_termination_for_tests()` exists for the test fixture only.
- `LaneDeadline.remaining()` raises the existing `BUDGET_EXCEEDED`/`LANE_TIMEOUT` `AssayError` when `_TERMINATION` is set, even for an unbounded lane.
- `execute_plan` raises the same error **after** its process runner returns, if `_TERMINATION` is set. So no result that finished during a termination is ever classified.
- `liveness.terminate_live_process_groups(sig=signal.SIGKILL) -> int` kills every registered live candidate process group and returns the count. `LivenessRunner` registers `proc.pid` (the pgid) right after `Popen` at `:1375` and unregisters it in the `finally` that reaps the process. The registry is a module-level `set[int]` under a `threading.Lock`.

### Required flow

**`assay run … --campaign-deadline PATH`**
1. `_cmd_run` resolves and parses PATH before any work, next to `_resolve_state_dir` at `:804`. Unreadable, invalid, or a lane not in `lanes` → `BAD_LANE_CONFIG` through `main()`'s handler. There is no verdict yet, exactly like other pre-run config refusals.
2. In `_run_reserved`, immediately after `LaneDeadline.start` (`:1169-1171`):
   `deadline = runner.campaign_bounded_deadline(deadline, expires_at_utc=…, wall_now=datetime.now(timezone.utc), monotonic_now=time.monotonic())`.
   This is the **only** UTC→monotonic conversion in the process.
3. HEAD read (`:1224`). If the campaign has already expired, this raises LANE_TIMEOUT, and the **existing** refusal path (`:1239-1325`) writes the `BUDGET_EXCEEDED`/`LANE_TIMEOUT` verdict. Add no new code for this case.
4. Right after `_emit_run_header(commit)` (`:1331`), validate: `commit == HEAD`, `git_tree == HEAD^{tree}` (read through `git.run` with `remaining=deadline.remaining`), and `assay_version == __version__`. On a mismatch, refuse with `ERROR`/`BAD_LANE_CONFIG` through the exact `runner.refuse_lane(...)` / `write_verdict` / `_emit_verdict_written` / `_print_run_summary` shape at `:1394-1414` (with `evidence=()` if it has not been loaded yet), and return the exit code.
5. Pass `expected_plan_sha256=doc["plan_sha256"][lane.name]` through `run_lane` → `_run_higher_rigor_lane` → `_run_prepared_lane` → `run_mutation`, as a keyword with default `None`. Mirror how `reuse_from` is threaded at `runner.py:5565`, `:5164`, `:5351-5378` and `:3585`.
6. In `run_mutation`, once `job_list` and `total` are known and **before** the shard selection at `:2324`: if `expected_plan_sha256` is not `None` and `plan_sha256([candidate_id(j) for j in job_list]) != expected_plan_sha256`, raise `CampaignPlanMismatchError`. This is a new `AssayError` subclass in `mutation.py` next to `InvalidRejudgeIdError` (`:292`), with `ERROR`/`BAD_LANE_CONFIG`; export it in `__all__`.
   - At `runner.py:4532`, extend the except to `except (mutation.InvalidRejudgeIdError, mutation.CampaignPlanMismatchError): raise`. The outer handler at `:5385` then renders the whole-lane refusal. Nothing runs after the mismatch.

**Signals (only in `assay run`, main thread)**
7. `_cmd_run` installs handlers for SIGTERM and SIGINT with `signal.signal`, saves the previous handlers, and restores them in `finally`. The handler is idempotent and **never raises**. It:
   - calls `runner.request_termination()`;
   - calls `liveness.terminate_live_process_groups()`;
   - writes one line, `assay: termination requested (signal N); stopping and writing an incomplete verdict`, to the diagnostics stream.
8. The run then unwinds normally:
   - In-flight candidates have been killed. `execute_plan` raises LANE_TIMEOUT when their runners return. The executor masks them as `budget_exceeded`, together with every unsubmitted candidate, which is the existing expiry path.
   - The baseline and non-liveness children in assay's own process group received the signal directly, and are converted the same way.
   - The verdict is written through the existing LANE_TIMEOUT handlers, and the process exits `4`. A killed candidate can never become `killed`, `crashed` or `hung`.
9. On a natural lane-deadline expiry, the existing per-attempt `timeout=` already kills the candidate's group (`liveness.py:1405-1439`). No new mechanism is added; oracle O6 pins it.

**Gate integration (`tools/self-qualification-gate.sh`)**
10. After `wheel_digest` (`:151`) and `assay_bin` (`:154`):
    - For a full run (`requested_lane == self-qualification`): `campaign="b105-${source_commit:0:12}"` and `deadline=".assay/campaign-deadline-$campaign.json"`. If the file is absent, run `"$assay_bin" campaign init --file assay.toml --campaign "$campaign" --lane self-qualification --lane self-qualification-preflight --hours 8 --state-dir .assay/mutation-state-self-qualification --state-dir .assay/mutation-state-self-qualification-preflight --wheel-sha256 "$wheel_digest"`.
    - For a standalone preflight run: `campaign="b105-pre-${source_commit:0:12}"`, with `--lane self-qualification-preflight --hours 1` and only the preflight state dir.
    - Print `B105_CAMPAIGN_DEADLINE=$deadline`.
11. In `run_and_verify_lane`, compute `remaining_s` from the file with the run-venv python (`int(expires_at - now)`, floored at 1), and wrap the run:
    `timeout --verbose --signal=TERM --kill-after=30s "$((remaining_s + 120))s" "$assay_bin" run "$lane" … --campaign-deadline "$deadline"`.
    - The 120 s grace lets assay's own expiry write the verdict first. The `timeout` is only a failsafe.
    - If `remaining_s` is ≤ 0, still invoke assay. It writes the LANE_TIMEOUT verdict itself, which is then verified like any other.
12. `run-gate.toml`:
    - Keep the outer `timeout … 7h30m` on `self-qualification` as the outermost failsafe.
    - Rewrite the comment block at `:38-44`: "the persisted campaign deadline (B117, A-473) bounds preflight + full lane together; this outer timeout and nyxloom's 8 h watchdog are failsafes only".
    - Add a one-line comment on the preflight lane saying it creates its own `b105-pre-*` campaign.

### Topology and bounds

- The deadline file lives in the **worktree's** `.assay/` directory. The repository-root `.gitignore:343` ignores `.assay/`. It is created inside the tester-unified container by the gate's own wheel, so `assay_version` matches the binary that validates it.
- Bounds: `--hours` ≤ 24. There is exactly one UTC→monotonic conversion per process. The live-group registry holds at most `jobs` entries.
- Termination is cooperative only after all live groups have received SIGKILL. There is no timing assumption; the 30 s `--kill-after` failsafe exists only for a wedged assay.

### Decision table

| Input / state | Result | Verdict | Exit |
|---|---|---|---|
| no `--campaign-deadline` | today's behaviour, byte-identical | unchanged | unchanged |
| valid file, time remaining, identity matches | lane runs under `min(lane budget, campaign remainder)` | normal | normal |
| valid file, already expired | existing pre-`run_lane` timeout refusal | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` | 4 |
| file unreadable, invalid, or lane not listed | refusal before HEAD | none (`main()` handler prints) | 2 |
| commit, tree or version mismatch | refusal after the run header | `ERROR`/`BAD_LANE_CONFIG`, whole lane | 2 |
| plan digest mismatch | refusal before any candidate | `ERROR`/`BAD_LANE_CONFIG`, whole lane | 2 |
| SIGTERM or SIGINT during R2 | live groups killed; in-flight and unsubmitted candidates `budget_exceeded` | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` | 4 |
| SIGTERM during the baseline | baseline converted to LANE_TIMEOUT | existing refusal/claim shape for a timed-out baseline | 4 |
| `init` with the same identity, file present | prints path; deadline unchanged | — | 0 |
| `init` with a different identity, file present | refusal | — | 2 |
| `init` with no file but state records present | refusal | — | 2 |

### Prepared proof and traceability

| Work | Owner | Oracle | Fixture | Controlled break |
|---|---|---|---|---|
| file format, init | `cli._cmd_campaign_init` | O1, O2 | toy lane repo | re-init extends `expires_at_utc` → O2 red |
| single conversion | `runner.campaign_bounded_deadline` | O3 | injected datetimes / monotonic | using `wall_now` twice → O3 remainder differs |
| inherit on resume | `_run_reserved` | O4 | two runs, injected expired file | ignoring the file → second run gets a fresh budget and runs |
| identity and plan binding | cli + `run_mutation` | O5 | toy lane, edited source | skipping the digest check → candidates run |
| natural expiry kills group | existing liveness | O6 | real spinning child | — (pin) |
| SIGTERM leaves nothing alive | cli signals + registry | O7 | real subprocess assay | no handler → candidate pid alive after assay exit |
| gate script | `self-qualification-gate.sh` | O8 | `tests/test_self_lane.py` substrings | missing `--campaign-deadline` → O8 red |

### Degrees of freedom

- Private names and the internal decomposition of `_cmd_campaign_init`.
- How the plan-discovery block is extracted from `_cmd_plan`. The `assay plan` output must stay byte-identical, and a test must prove it.
- Where the signal wrapper lives inside `_cmd_run`.

**Not free:** file fields, CLI spellings, reason codes, exit codes, the conversion formula, and the order of the validation steps.

## Work

1. **Red first.** Add `tests/test_campaign_deadline.py` (O1–O5, O8) and `tests/test_campaign_termination_real.py` (O6, O7). If `tests/zz_slow/` exists on your base (P1 merged), put the real-subprocess file there. Otherwise put it at the top level and note in the REPORT that P1's tier move must include it. Record the red state in `assay-B117-REPORT.md`.
2. Add `mutation.plan_sha256` and `CampaignPlanMismatchError`, and the check in `run_mutation` (Flow 6). Add the `runner.py:4532` except tuple.
3. Add `runner.campaign_bounded_deadline`, the termination event, the `LaneDeadline.remaining` check, and the post-runner check in `execute_plan`.
4. Add the `liveness` live-group registry and `terminate_live_process_groups`.
5. Extract `_cmd_plan`'s discovery (`cli.py:1648-1765`) into `_discover_plan_jobs(...)`. Prove `assay plan` output is unchanged: the existing plan tests must pass unmodified.
6. Add the `campaign init` subcommand, `--campaign-deadline`, Flow 1–5, and the signal wrapper (Flow 7).
7. Gate script and run-gate.toml (Flow 10–12). Extend `tests/test_self_lane.py`'s gate-script substring pins (`:176-200`) with `campaign init`, `--campaign-deadline` and `B105_CAMPAIGN_DEADLINE=`.
8. Run the focused tests, then the docs (see Docs sync), CHANGES, REPORT, and commit.

## Oracles

- **O1: init writes exactly the specified document.** Use a toy lane repository with R0 and R2 (the `tests/test_cli_run.py:586` shape) and run `main(["campaign", "init", ...])`.
  - *Observable:*
    - the key set equals the ten keys listed above;
    - `commit` and `git_tree` equal real `git rev-parse`;
    - `plan_sha256[lane]` equals `mutation.plan_sha256([row["id"] for row in <assay plan JSON>["candidates"]])` from `main(["plan", lane])` on the same tree;
    - the value is `null` for an R0/R1 lane.
  - *Negative:* a digest computed over shard-filtered or sorted IDs differs.
- **O2: re-init never extends.** Init once with `--hours 1`, then again with `--hours 5` and the same identity. `expires_at_utc` is unchanged and the exit is 0. After a new commit, a different identity gives exit 2 and the file is unchanged. With the file deleted and a state dir holding `x.json`, init gives exit 2 and nothing is written.
- **O3: one conversion, pure.**
  - `campaign_bounded_deadline(LaneDeadline(expires_at=1000.0, monotonic=lambda: 100.0), expires_at_utc=T0+300s, wall_now=T0, monotonic_now=100.0).expires_at == 400.0`;
  - with a lane budget of 200 s instead, it gives `300.0`;
  - `expires_at_utc=T0-5s` gives `95.0`, a past value that is not refused here;
  - a naive datetime raises `ValueError`.
  - *Negative:* recomputing from `datetime.now()` inside `remaining()` makes the result depend on the call time.
- **O4: resume inherits the clock.** Write a deadline file whose `expires_at_utc` is `2000-01-01T00:00:00Z`, matching the toy repo's identity. `main(["run", lane, "--campaign-deadline", f, "--verdict-json", v])` writes a verdict with status `BUDGET_EXCEEDED`, reason `LANE_TIMEOUT`, exit 4, and no candidate executed (use a counting fake process runner, or the progress stream having no `candidate` event).
  - *Negative:* without the flag, the same run executes candidates.
- **O5: identity and plan binding.**
  - With a valid, unexpired file, change a mutable source line and commit. The run gives `ERROR`/`BAD_LANE_CONFIG`, exit 2, and the diagnostics name "commit".
  - Keep the commit but hand-edit `plan_sha256` in the file to another 64-hex value. The run gives `ERROR`/`BAD_LANE_CONFIG`, whole-lane, and no `candidate` progress event.
  - Invalid-file cases (schema/2, a `+02:00` timestamp, a duplicate key, a missing lane) each give exit 2 before HEAD is read.
- **O6: natural expiry kills the group.** This pins existing behaviour. With `LivenessRunner` and an injected `_process_group_killer` recorder, a candidate reaching its `timeout` has its pgid killed. Reuse the monitor test seam at `tests/test_liveness_runner_monitor.py`.
  - *Negative:* removing the killpg call leaves the recorder empty.
- **O7: SIGTERM leaves no candidate alive, and no candidate is classified by the termination.**
  - Toy repository:
    - `pkg/mod.py` holds `def guard(x):\n    return x <= 0\n`, a real compare-swap site.
    - The test file has `if guard(0): pass`. In the else-branch it writes `os.getpid()` to an absolute path embedded in the test source (`tmp_path / "candidate.pid"`), then runs `while True: pass`. Spinning keeps CPU growing, so liveness never calls it `hung`.
    - The lane has `rigor = ["R0","R2"]`, `liveness = true`, `budget_per_candidate = "600s"` and `budget = "15m"`.
  - Start assay as a real subprocess: `[sys.executable, "-c", "import sys; from assay.cli import main; sys.exit(main(sys.argv[1:]))", "run", "package", "--file", "assay.toml", "--verdict-json", str(v)]`, with `env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}` and `cwd=repo`.
  - Wait for the pid file, polling at 0.2 s with a **failsafe of 180 s** that fails the test. Then send `os.kill(assay.pid, signal.SIGTERM)` and `assay.wait(timeout=120)` as a failsafe.
  - *Observable:*
    - exit code 4;
    - the verdict status is `BUDGET_EXCEEDED` with reason `LANE_TIMEOUT`;
    - the candidate's ID is in R2 `budget_exceeded`, and not in `killed`, `crashed` or `hung`;
    - `/proc/<candidate pid>/stat` is absent or shows state `Z`.
  - *Negative:* without the handler, assay dies on the default SIGTERM action with exit `-15`, writes no verdict, and the spinning candidate is still in state `R`.
  - **Cleanup (always, in `finally`):** `os.killpg(candidate_pid, SIGKILL)`, ignoring `ProcessLookupError`, so a red run never leaks a spinning process onto the shared host.
- **O8: the gate script is wired.** `tests/test_self_lane.py` asserts the substrings `campaign init`, `--campaign-deadline "$deadline"` and `B105_CAMPAIGN_DEADLINE=` in `tools/self-qualification-gate.sh`, and asserts that the `timeout` wrapper wraps the `assay run` invocation.

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

- **README (WHAT):** a short "Campaign deadline" subsection. `assay campaign init`, plus `--campaign-deadline`: resumes inherit the clock, and an expiry is an incomplete `BUDGET_EXCEEDED`/`LANE_TIMEOUT` verdict, never a candidate outcome.
- **DESIGN-GUIDE (WHY):**
  - there is one UTC instant, converted once per process (A-193 preserved);
  - why re-init never extends the deadline;
  - why state without a deadline refuses;
  - why `wheel_sha256` is recorded but not validated;
  - SIGTERM semantics: kill the live groups, then never classify a result that finished during termination.

  Cite A-473.
- **CONSUMERS (HOW):** a pasteable sequence: init, run, run again (resume), and reading the refusal. State the exit codes 4 and 2 from the decision table.
- **CHANGES:** `### Added` and `### Changed` bullets for B117.

## Scope / forbid

- **Touch:**
  - `src/assay/cli.py`, `src/assay/runner.py` (`LaneDeadline`, the termination helpers, `execute_plan`, the threading of `expected_plan_sha256`, the except tuple at `:4532`);
  - `src/assay/mutation.py` (`plan_sha256`, `CampaignPlanMismatchError`, the `run_mutation` kwarg and check only);
  - `src/assay/liveness.py` (the registry only);
  - `tools/self-qualification-gate.sh`, `run-gate.toml` (comments only);
  - `tests/test_campaign_deadline.py`, `tests/test_campaign_termination_real.py`, `tests/test_self_lane.py` (substring pins);
  - README, DESIGN-GUIDE, CONSUMERS, CHANGES, `nyxloom-trove/reports/assay-B117-REPORT.md`.
- **Forbid:**
  - the verdict schema, `verdict.py`, `verify.py`, `ReasonCode`, `EXIT_CODES`;
  - `assay.toml` lane keys;
  - `MUTATION_STATE_SCHEMA_VERSION`;
  - the executor loop (that is P4);
  - `decisions.md`.

  Needing any of them is a BLOCKED trigger. **Do not add a new module**: B105's `judge.targets` must equal the discovered files. Keep the 100% line and branch floor for every new line and branch, with no `pragma: no cover`.

## Gate

**Host-load rule. Paste it into every agent prompt; it is not optional.**
- The host is shared with a production game server.
- Run light commands and focused tests serially, under `nice -n 19 ionice -c3`.
- Run at most ONE gate container at a time on this host, and never start one while another session's gate is running (`docker ps` first).
- Never launch the `self-qualification` lane (the full R2 campaign) except as an explicit step of the §7 pilot or the §9 runbooks, and only with controller approval.
- The `self-qualification-preflight` lane (R0/R1, ~10 min) may be used when a brief says so.
- Remove containers by exact name only.

1. Focused tests, run serially:
   - `tests/test_campaign_deadline.py`
   - `tests/test_campaign_termination_real.py`
   - `tests/test_lane_timeout_writes_a_verdict.py`
   - `tests/test_liveness_runner_monitor.py`
   - `tests/test_cli_run.py`
   - `tests/test_self_lane.py`
   - `tests/test_mutation_progress_budget_plan.py`
   - `tests/test_b105_cli_boundaries.py`
   - `tests/test_state_dir_resume.py`
2. `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b117-gate.log 2>&1; echo "exit=$?"`. Then, in a **separate** step, `grep -E "ASSAY_GATE_CONTAINER_EXIT|ASSAY_REGISTERED_GATE_COMPLETE" /tmp/b117-gate.log`.
3. The gate script and B105-collected source changed, so also run `python ./run-gate.py self-qualification-preflight > /tmp/b117-preflight.log 2>&1`, only when no other gate is running. In a separate step, check that `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1` and `B105_CAMPAIGN_DEADLINE=` both appear.

## BLOCKED rule

If a named contract cannot be met as specified, or the scope needs a forbidden file, STOP. Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B117-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

Specific triggers:
- `execute_plan`'s post-runner check changes an existing test's classification. That would mean a test depended on a termination-time result.
- the `_cmd_plan` extraction changes `assay plan` output.
- O7 cannot be made to pass without a timing assertion.

## Report

`assay-B117-REPORT.md` must contain:
- the traceability table with the actual test names and red/green counts;
- the log paths and marker lines for the gate and the preflight;
- the files touched;
- whether P1's tier move must include `test_campaign_termination_real.py`.

Commit with this trailer:

```
Co-Authored-By: Claude Sonnet <noreply@anthropic.com>
```

Do not merge.
