# B110-P6 — A persisted campaign deadline, and termination that leaves no candidate behind

*Revised 2026-09-28 after round-1 and round-2 reviews (see REVIEW-2026-09-28-round1.md, REVIEW-2026-09-28-round2.md).*
- Round 1 applied findings P6-1..P6-11 and carver decisions C3, C8, C15 and C16.
- Round 2 applies findings P6R2-1..P6R2-10 and carver decisions C23, C28, C29, C31 and C32.

| Field | Value |
|---|---|
| Backlog | **B117** (split from B110) |
| Branch | `assay-b110-p6-deadline` from the integration line (the plan §11.1 / C18 reconciliation branch `assay-b110-integration`) |
| Depends on | Nothing to **start**; it can be developed in parallel with P0, P2 and P10a.<br>**Merge order (plan §11.6):**<br>• **P0 merges before P6.** Both edit `run_and_verify_lane`, the `tests/test_self_lane.py` pins and the `LivenessRunner` Popen region (`liveness.py:1336-1380`, P0's sampler). P6 rebases onto P0 and keeps both edits.<br>• P6 merges **before** v14 (P3d) and before P7b. P3d and P7b rebase onto P6's gate-script wiring and its pins.<br>• P4 rebases onto P6's `_run_attempt` change (C3).<br>• P7 and P10 edit the same `run_mutation` block. The order in it is pinned by C23 (Flow 7). |
| Contract class | **2b.** The file format, CLI, refusals and termination semantics are fixed; the private construction is yours. |
| Implementer | Opus (fresh session) |
| Decisions | **A-473 (plan D9).** Also binding: A-464 (8 h hard stop; an expiry is incomplete infrastructure evidence, never a candidate outcome), A-160, A-193 (monotonic lane clock, remainder sampled at each boundary), A-195.<br>Round-1 carver decisions:<br>• **C3:** unclassified attempts are owned by P6.<br>• **C8:** state records are bound to the deadline; D7 defaults to NO.<br>• **C15:** dataclass-contract fixture.<br>• **C16:** signal handlers only on the main thread.<br>Round-2 carver decisions:<br>• **C23:** the pinned `run_mutation` order. The digest is taken over the **full** discovered list.<br>• **C28:** P6 owns the OOM rule.<br>• **C29:** `_discover_plan_jobs` is the single planner-jobs extraction, and P8 wraps it.<br>• **C31:** any `timeout` exit ≥ 124 means "failsafe fired, no verdict".<br>• **C32:** process-group lifecycle. |
| Size | L:<br>• `cli.py`: new subcommand, one flag, signal wrapper.<br>• `runner.py`: `LaneDeadline` helper and field, termination flag, `execute_plan` post-check, `default_process_runner` session and registry.<br>• `liveness.py`: live process-group registry.<br>• `mutation.py`: `plan_sha256`, one kwarg and check, `_run_attempt` result handling (C3), and the state-record deadline key and loader check (C8).<br>• Gate script, run-gate.toml comment, tests, docs. |

B110's rule is that a resume, retry or replacement worker consumes the **same** remaining time. None of them resets the campaign clock. Today every `assay run` starts a fresh `LaneDeadline` from the lane budget (`cli.py:1169`). A SIGTERM from the gate's `timeout` also leaves candidate process groups (`start_new_session=True`) running as orphans.

**Round-1 finding P6-1 (C3).** A candidate still running when the lane or campaign deadline expires is capped at `deadline.remaining()` (`mutation.py:2708-2713`). Today its timeout comes back as a *returned* `BUDGET_EXCEEDED/LANE_TIMEOUT` result (the `TimeoutExpired` handler at `runner.py:1239`; `OSError` at `:1276-1286`) and is **recorded** as `budget_exceeded`. On `--resume` that record is replayed (`mutation.py:1126`). So after the first invocation's budget, a resumed campaign could never PASS. P6 therefore also makes such attempts **unclassified**: `_run_attempt` raises LANE_TIMEOUT, the executor masks the position, no record is written, and resume re-executes it.

---

## Context to read first

Paths are relative to `assay/`. Line numbers were checked at HEAD `db85f747`.

1. `nyxloom-trove/reports/assay-B110-PLAN-2026-09-28.md`: §0, §3 D9, §7, §9.3 and §10.
   - The pilot's deadline is created by P7b's gate mode as campaign `b110-pilot-<commit12>`, 2 h.
   - The qualifying deadline is created by this package's gate wiring as campaign `b105-<commit12>`.
   - Never run `campaign init` on the host (plan §9.3 step 2).
2. `src/assay/runner.py`:
   - `:209-308`: `LaneDeadline`, a frozen `kw_only` dataclass (`start`, `tightened`, `remaining`, `unbounded`).
   - `:321-347`: `default_process_runner`. It is plain `subprocess.run` with no new session, so these children share assay's process group. **You change this** (Interfaces 6).
   - `:1094-1110`: the `execute_plan` signature. It is the single boundary that every R0 baseline, R2 candidate, R3 half and probe goes through.
   - `:1180-1290`: `execute_plan` → `_execute_plan_inner`, which turns `TimeoutExpired` and `LivenessHungExpired` (handler at **`:1239`**) and `OSError` (**`:1276-1286`**) into **returned** results.
   - `:649`: `_decode_timeout_stream`, which already decodes partial timeout bytes.
   - `:2800`: `_execute_snapshot_unit`.
   - `:4526-4541`: the `except mutation.InvalidRejudgeIdError: raise` pattern (the tuple at `:4532`).
   - `:5385-5410`: the outer `except AssayError` that renders the whole-lane refusal (`refuse_all`).
3. `src/assay/cli.py`:
   - `:255-328`: the `run` parser. Add your flag after `--reuse-from`; P7 also adds flags there.
   - `:330-367`: the `plan` parser.
   - `:456-495`: `main`, dispatch and the `except AssayError` handler.
   - `:725-812`: `_cmd_run`, which calls `_resolve_state_dir` at `:804`; see also `_resolve_state_dir` at `:845-878`.
   - `:1124-1171`: `_run_reserved` up to `LaneDeadline.start`.
   - `:1236-1325`: the HEAD read (`git.head_rev` at **`:1238`**) and the **pre-`run_lane` LANE_TIMEOUT refusal path**, including the label-grace `LaneDeadline(expires_at=…)` direct construction at `:1279-1282`.
   - `:1326-1331`: the run header.
   - `:1379-1414`: the refusal shape (`runner.refuse_lane(...)`, `write_verdict`, `_emit_verdict_written`, `_print_run_summary`).
   - `:1481`: the `run_lane` call; `:1527`: its LANE_TIMEOUT handler.
   - `:1571-1790`: `_cmd_plan`, whose discovery (`:1648-1765`) you will extract.
4. `src/assay/mutation.py`:
   - `:292`: `InvalidRejudgeIdError`.
   - `:1023`: `candidate_id`.
   - `:1244-1270`: the atomic temp-and-replace write pattern for state records.
   - `:1273-1402`: `_load_validated_state_record` (C8's loader check goes here, next to the judge check at `:1399-1402`).
   - `:2318-2343`: the selection block, where the plan-digest check goes before shard selection.
   - `:2690-2707`: `_run_attempt` arms `equivalence_reservation` and `kill_signal_reservation`. They are **only** closed in the `finally` at `:2800-2804`, which belongs to a later `try` (`:2789`). `execute_plan` at `:2714` is **not** inside it.
   - **`:2708-2713`: `_run_attempt`'s `command_deadline`.** It is `deadline.remaining()`, lowered to `budget_per_candidate_seconds` only when that is strictly smaller. That comparison is exactly the C3 "timeout source" fact.
   - `:2956-2989`: the state-record payload (C8 adds one optional key).
5. `src/assay/liveness.py`:
   - `:1299-1340`: `LivenessRunner.__init__`, including the `_process_group_killer` injection at **`:1333`**.
   - `:1360-1440`: spawn with `start_new_session=True` at `:1375`; `_cleanup_process_group` at **`:1402`**, which the default runner's `finally` mirrors (C32); and the group kill at `:1405-1439`. P0 also edits `:1336-1380`.
   - `:1328-1330`: the warning about killing a pgid you do not own. O13 must never call a real `os.killpg` on a synthetic pgid.
6. `tools/self-qualification-gate.sh:11-60, :150-248` (`run_and_verify_lane` is at `:156-214`) and `run-gate.toml:33-62`.
7. `nyxloom-trove/decisions.md`, rows A-160 (`:453`) and A-193 (`:516`).
8. Test precedents:
   - `tests/test_lane_timeout_writes_a_verdict.py`: the refusal verdict for an expired deadline.
   - `tests/test_cli_run.py` (`test_run_liveness_classifies_a_busy_loop_as_budget_exceeded_not_hung`; find it **by name**, because P1 may move it to `tests/zz_slow/test_cli_run_real_campaigns.py`): a real child spinning under `LivenessRunner` via in-process `main()`.
   - `tests/test_state_dir_resume.py`.
   - `tests/test_config_unbounded_budget.py:300-338`: it records `subprocess.run`'s `timeout`. Interfaces 6 moves its recorder.
   - `tests/test_config_rigor_grammar.py:98`: `subprocess.run`/`Popen` sentinels (unchanged).
   - `tests/test_runner_execute.py:220`: undecodable child output (must stay green).

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
- a document whose `plan_sha256` key set is not exactly equal to `lanes` (P6R2-10).
- a duplicate JSON key. Parse with an `object_pairs_hook` that refuses duplicates, the same approach as `reuse.py`'s bounded parse.

`init` refusals (exit 2, `ERROR`/`BAD_LANE_CONFIG`, message on stderr, nothing written):
- the output file already exists with a **different** identity (`commit`, `git_tree`, `lanes`, `assay_version` or `plan_sha256` differ). If it exists with the **same** identity, init prints the existing file's path and exits 0 **without changing `expires_at_utc`**. Re-running init never extends a deadline.
- any `--state-dir` contains a state record (a `<64hex>.json` file) whose top-level `campaign_deadline_sha256` (C8, Interfaces 8) is **absent or different** from the SHA-256 of the deadline file being created or reused. That covers the backlog's "state without a matching deadline cannot silently start a new clock".
  - A same-identity re-init over its own campaign's records passes, because those records carry this file's digest.
  - To start a new campaign after an expiry, or to reuse a state dir that holds unbound records (a screen, the pilot, imports), the operator must move the old state dir aside deliberately.
  - Importing unbound records is P9's job (`assay state import`, C8), not `init`'s.
- a dirty worktree, using the same integrity check `_cmd_plan` uses (`runner._resolve_snapshot_worktree_integrity`), without `--allow-dirty`. `init` has no `--allow-dirty` flag.

**2. `assay run --campaign-deadline PATH`.** Optional. When given, the flow below applies.

**3. `mutation.plan_sha256(candidate_ids: Sequence[str]) -> str`.** SHA-256 over `b"".join(f"{len(i)}:{i},".encode("ascii") for i in candidate_ids)`, which is netstrings in **plan order**. **P6 is the single owner of this helper** (P7-7). P7 (`selection_sha256`), P8 and P9 import it, and nobody defines a second one. If P7 runs in parallel and needs it first, P7 imports it from P6's merged base or stops with BLOCKED; it never copies it.

**4. `runner.campaign_bounded_deadline(lane_deadline: LaneDeadline, *, expires_at_utc: datetime, wall_now: datetime, monotonic_now: float) -> LaneDeadline`.**
- Pure.
- Returns `LaneDeadline(expires_at=min(lane_deadline.expires_at, monotonic_now + (expires_at_utc - wall_now).total_seconds()), monotonic=lane_deadline.monotonic)`.
- The result may lie **in the past**. That is deliberate: the existing HEAD read then raises LANE_TIMEOUT, and the existing refusal path writes the verdict.
- `wall_now` and `expires_at_utc` must be timezone-aware UTC; anything else is a `ValueError`.

**5. Termination.**
- `runner.request_termination()` sets a module-level `threading.Event`, `_TERMINATION`. It is defined in `liveness` (as `liveness._TERMINATION`) so the registry can read it without an import cycle; `runner` only delegates.
- `runner.termination_requested() -> bool`.
- `runner._reset_termination_for_tests()` exists for the test fixture only.
- `LaneDeadline` gains a field `honors_termination: bool = True`. This changes a dataclass, so C15 applies.
  - `LaneDeadline.remaining()` raises the existing `BUDGET_EXCEEDED`/`LANE_TIMEOUT` `AssayError` when `_TERMINATION` is set **and** `honors_termination` is true. This also applies to an unbounded lane.
  - The label-grace deadline at `cli.py:1279-1282` is constructed with `honors_termination=False` (P6-8). A SIGTERM before or during the HEAD read therefore still gets its commit label read within the grace, and writes the `BUDGET_EXCEEDED`/`LANE_TIMEOUT` verdict (exit 4) instead of exiting 2 with no verdict.
  - `tightened()` and `campaign_bounded_deadline` copy the field.
- **`execute_plan` checks termination on every path, before and after (P6-2, P6R2-9, C32):**
  - **Before** calling the runner: if `_TERMINATION` is set, raise LANE_TIMEOUT without spawning anything.
  - **After**, on every exit path. Wrap the `_execute_plan_inner` call in a `try`:
    - on a normal return, if `_TERMINATION` is set, raise the same LANE_TIMEOUT error, whatever the result. That covers the returned `TimeoutExpired` / `LivenessHungExpired` conversions (handler `runner.py:1239`) and the `OSError` conversion (`:1276-1286`);
    - on **any exception** raised out of `_execute_plan_inner` (including an `AssayError`), if `_TERMINATION` is set, raise LANE_TIMEOUT `from` it; otherwise re-raise unchanged.
  - Put these checks in `execute_plan`, **not** inside `_execute_plan_inner`'s branches. So no result that finished or failed during a termination is ever classified.
- `liveness.terminate_live_process_groups(sig=signal.SIGKILL) -> int` kills every registered live process group and returns the count.
  - `liveness.register_live_group(pgid)` and `unregister_live_group(pgid)` are the only mutators.
  - **Kill-on-register (P6R2-1, C32).** `register_live_group(pgid)` adds `pgid` under the lock, then, **still under the lock**, checks `runner.termination_requested()`. If it is set, it kills that group at once through the killer seam. The handler sets `_TERMINATION` **before** it sweeps, so every child is either swept by the handler or kills itself on registration. A child spawned after the sweep can never survive.
    - `liveness` must not import `runner` (import cycle). Put the termination `threading.Event` itself in `liveness` as `liveness._TERMINATION`, and have `runner.request_termination()` / `termination_requested()` / `_reset_termination_for_tests()` delegate to it.
  - **Killer seam (P6R2-4).** Every kill in the registry goes through the module-level function `liveness._killpg_group(pgid: int, sig: int) -> None`. It calls `os.killpg(pgid, sig)` and swallows `OSError`, which includes `ProcessLookupError` and `PermissionError`, so the handler **never raises**. Tests monkeypatch `liveness._killpg_group`, and never call a real `os.killpg` on a synthetic pgid (see the warning at `liveness.py:1328-1330`).
  - `LivenessRunner` registers `proc.pid` (the pgid) right after `Popen` at `:1375`, and unregisters it in the `finally` that reaps the process.
  - `runner.default_process_runner` does the same (Interfaces 6).
  - **Handler safety (P6-4).** The registry is a module-level `set[int]` guarded by a `threading.RLock`. The signal handler runs on the main thread and must not deadlock if the main thread already holds the lock (the baseline `LivenessRunner` runs on it), so it takes the lock with `acquire(blocking=False)`.
    - If the lock is held by **another** thread, the handler snapshots the set without the lock: `list(_LIVE_GROUPS)` is atomic under the GIL for a set of ints. It kills that snapshot. It also sets a flag, so the next `unregister_live_group` call re-runs `terminate_live_process_groups()` under the lock. Kill-on-register covers any group registered after the snapshot.
    - The handler never calls `print`, because `print` can raise "reentrant call". It writes its one diagnostics line with `os.write(2, b"...")`.
  - **Pinned names (P6R2-4).** The names are not degrees of freedom, because O13 calls them directly:
    - the handler is `cli._termination_signal_handler(signum: int, frame: object) -> None`;
    - the installer is `cli._install_termination_handlers() -> Callable[[], None]`. It returns a function that restores the previous handlers, and returns a no-op restore off the main thread (C16).

**6. `runner.default_process_runner` runs every child in its own session and registers it (P6-3).** Today its children share assay's process group, so GNU `timeout`'s group SIGTERM can kill a non-liveness candidate (exit −15) **before** the main-thread handler sets `_TERMINATION`. That candidate would then be classified `killed` and recorded.

Because the qualifying lane mutates Assay's own signal-handling code, a mutant can genuinely make a candidate kill itself. So "treat −15 as unclassifiable" is rejected: it would make such a mutant stop the whole lane on every resume. The fix is to order the events instead. New body, same signature and the same observable result shape:
1. `proc = subprocess.Popen(list(argv), env=dict(env), cwd=cwd, stdout=PIPE, stderr=PIPE, text=True, errors="replace", start_new_session=True)`, then `liveness.register_live_group(proc.pid)`. Registration kills it at once if termination was already requested (Interfaces 5).
2. `stdout, stderr = _wait_child(proc, timeout)`, where the module-level seam `_wait_child(proc, timeout)` returns `proc.communicate(timeout=timeout)`. On `subprocess.TimeoutExpired as exc` (P6R2-2, C32), mirror `subprocess.run` on POSIX:
   - `liveness._killpg_group(proc.pid, SIGKILL)`;
   - `proc.wait()`. **Not** `communicate()`: a descendant that left the group, for example a suite's own `start_new_session` child that inherited stdout, can hold the pipe open forever;
   - `raise` the original `exc`. It carries the partial **bytes** `Popen._communicate` had buffered, and `_decode_timeout_stream` (`runner.py:649`) already decodes them. `_execute_plan_inner` handles it unchanged.
3. `finally`:
   - make a best-effort group kill through `liveness._killpg_group(proc.pid, SIGKILL)` **before** unregistering. Mirror `LivenessRunner._cleanup_process_group` (`liveness.py:1402`) exactly, including its guard against killing a reused pgid. This stops descendants surviving a normal exit, a KeyboardInterrupt or a SIGHUP;
   - then `liveness.unregister_live_group(proc.pid)`.
4. Return `subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)`.

The children no longer receive `timeout`'s group signal directly. Only assay does. The handler sets `_TERMINATION` **first** and then SIGKILLs every registered group, so by the time any child's death is observed, termination is already requested, and Interfaces 5 and 7 make it unclassified.

A mutant that signals its own candidate is still classified normally, because termination was never requested. Legacy mode classifies it `killed`; `--cold-witness` mode classifies it `crashed`, per P3b and C2.

**Test impact:**
- `tests/test_config_unbounded_budget.py:311-317` recorded `subprocess.run`'s `timeout` kwarg. Move its recorder to wrap `runner.default_process_runner`'s internal `communicate` timeout. Expose a module-level helper `runner._wait_child(proc, timeout)` that the test monkeypatches.
- Keep `assert None not in recorded` and **add** `assert recorded`, so the test can no longer pass vacuously.

**7. C3: unclassified attempts, in `mutation._run_attempt` (P6-1, P6-2).** The attempt's outcome is decided by `execute_plan`. After it returns, in this order:
1. `lane_bound = budget_per_candidate_seconds is None or budget_per_candidate_seconds >= <the remainder sampled for command_deadline>`. Capture that sampled remainder in a local where `command_deadline` is computed (`:2698-2703`).
2. If the result is `BUDGET_EXCEEDED` with reason `LANE_TIMEOUT` **and** `lane_bound`, raise `AssayError(<message naming the lane/campaign deadline>, outcome=BUDGET_EXCEEDED, reason_code=LANE_TIMEOUT)`. The executor's existing expiry branch then masks the position, writes no record, and stops submitting (I5 in P4). `--resume` re-executes it.
   - **Close the reservations first (P6R2-8).** `equivalence_reservation` and `kill_signal_reservation` are armed at `:2690-2707` and otherwise closed only in the `finally` at `:2800-2804`, which the raise would skip. Before **every** raise this package adds in `_run_attempt` (this step, and the OOM rule in Interfaces 9), close both if they are not `None`.
   - Equivalently, wrap `execute_plan` and the new checks in a `try/except` that closes them and re-raises. A raise out of `execute_plan` itself (termination, Interfaces 5) takes the same path.
   - Oracle O10's fixture declares an `equivalence_artifact` and asserts the reservation was closed.
   - A per-candidate budget timeout (`not lane_bound`) keeps today's `budget_exceeded` classification and record. That is a genuine candidate outcome, the spinning-mutant case.
   - A `CANDIDATE_HUNG` result keeps today's `hung` classification unless termination was requested (step 3).
3. Termination is already covered, because `execute_plan` raises when `_TERMINATION` is set (Interfaces 5). No extra check is needed here.
4. The post-command snapshot integrity check is part of the classification boundary. If the lane or campaign deadline, or a termination request, interrupts `_snapshot_left_dirt`, propagate `LANE_TIMEOUT`; leave the candidate unclassified and write no candidate state or progress event. A completed mutant command is not classified until snapshot HEAD, index, and worktree integrity are proven. This supersedes the earlier timeout absorption at this call site; a per-candidate budget expiry remains a genuine `budget_exceeded` result.

**8. C8: state records are bound to the deadline.**
- When `assay run` receives `--campaign-deadline`, every state record it writes gains the optional top-level key `campaign_deadline_sha256`: the SHA-256 of the deadline file's bytes as read in Flow 1. The loader tolerates extra top-level keys (`mutation.py:1310-1348`), and `MUTATION_STATE_SCHEMA_VERSION` stays 1.
- Under `--campaign-deadline --resume`, `_load_validated_state_record` treats a record whose `campaign_deadline_sha256` is absent or different as **rejected**. It is re-executed and counted in the `resume` progress event's rejected total, exactly like a judge mismatch. Import unbound records only through P9's `assay state import`, never implicitly.
- Without `--campaign-deadline`, the key is neither written nor checked. Today's behaviour is byte-identical.

**9. C28: OOM kills are never classified (owner P6).**
- Add `liveness.oom_kill_count(path: Path = Path("/sys/fs/cgroup/memory.events")) -> int | None`. It reads the cgroup-v2 `memory.events` file and returns the integer on its `oom_kill <n>` line. It returns `None` if the file is missing or unreadable, or has no such line.
- `run_mutation` takes an injectable `oom_counter: Callable[[], int | None] = liveness.oom_kill_count` keyword, threaded like `expected_plan_sha256`, and probes it once at the start.
  - If the probe returns `None`, the `plan` progress event carries `"oom_guard": "unavailable"`, and the rule below is inert for the whole run.
  - Otherwise the event carries `"oom_guard": "active"`. The `plan` event's key set is open (only event *names* are a closed vocabulary), so this is additive.
- In `_run_attempt`, sample `before = oom_counter()` immediately before `execute_plan` and `after = oom_counter()` immediately after it returns. If both are integers and `after > before`:
  - close the reservations (Interfaces 7);
  - then raise `AssayError(<message naming the cgroup OOM kill>, outcome=BUDGET_EXCEEDED, reason_code=LANE_TIMEOUT)`.
- The attempt is unclassified through the C3 path: masked, no record, submission stops, and the invocation ends as an incomplete `BUDGET_EXCEEDED/LANE_TIMEOUT` verdict. `--resume` re-executes it later.
- Each in-flight attempt samples its own window, so **every** attempt whose window contains the OOM is unclassified. A load-induced OOM kill of a grandchild can therefore never become a `killed` or `crashed` classification (A-464).
- This check runs **after** C3 step 2 and **before** the result is classified. It applies to every attempt, not only lane-bound ones.

### Required flow

**`assay run … --campaign-deadline PATH`**
1. `_cmd_run` resolves and parses PATH before any work, next to `_resolve_state_dir` at `:804`. Unreadable, invalid, or a lane not in `lanes` → `BAD_LANE_CONFIG` through `main()`'s handler. There is no verdict yet, exactly like other pre-run config refusals. Keep the file's raw bytes to compute `campaign_deadline_sha256` (C8).
2. `LaneDeadline.start` (`:1169-1171`) runs unchanged, on the **lane** budget.
3. HEAD read (`:1238`), unchanged, under the lane deadline.
4. Right after `_emit_run_header(commit)` (`:1331`), validate the **identity**:
   - `commit == HEAD`;
   - `git_tree == HEAD^{tree}`, read through `git.run` with `remaining=deadline.remaining`;
   - `assay_version == __version__`.

   On a mismatch, refuse with `ERROR`/`BAD_LANE_CONFIG` through the exact `runner.refuse_lane(...)` / `write_verdict` / `_emit_verdict_written` / `_print_run_summary` shape at `:1394-1414` (with `evidence=()` if it has not been loaded yet), and return the exit code.
5. **Only now** convert: `deadline = runner.campaign_bounded_deadline(deadline, expires_at_utc=…, wall_now=datetime.now(timezone.utc), monotonic_now=time.monotonic())`. This is the **only** UTC→monotonic conversion in the process.
   - If the campaign has already expired, the result lies in the past. The first `deadline.remaining()` inside `run_lane` then raises LANE_TIMEOUT, and the **existing** `run_lane` LANE_TIMEOUT handler (`cli.py:1527`) writes the `BUDGET_EXCEEDED`/`LANE_TIMEOUT` verdict (exit 4). Add no new code for this case.
   - **Precedence (P6-7), pinned:**
     1. file structure (step 1, exit 2, no verdict);
     2. identity (step 4, exit 2, a `BAD_LANE_CONFIG` verdict);
     3. campaign expiry (exit 4, a `LANE_TIMEOUT` verdict);
     4. plan digest (step 7).

     So an **expired** file for a **different** commit reports the identity mismatch, never a timeout.
6. Pass `expected_plan_sha256=doc["plan_sha256"][lane.name]` and `campaign_deadline_sha256=<digest>` through `run_lane` → `_run_higher_rigor_lane` → `_run_prepared_lane` → `run_mutation`, as keywords with default `None`. Mirror how `reuse_from` is threaded at `runner.py:5565`, `:5164`, `:5351-5378` and `:3585`.
7. In `run_mutation`, once `job_list` and `total` are known: if `expected_plan_sha256` is not `None` and `plan_sha256([candidate_id(j) for j in job_list]) != expected_plan_sha256`, raise `CampaignPlanMismatchError`.
   - **The C23 order is pinned.** P6, P7 and P10 all edit this block:
     1. discovery;
     2. **this digest check, over the FULL discovered `job_list`**;
     3. P10's ledger placement (only with `--equivalence-audit`);
     4. P7's `--candidates-file` selection, or the shard selection at `:2324`;
     5. resume lookup.
   - The digest is never taken over a selected, shard-filtered or ledger-reduced list. P7's T13 and P10's O7c prove the combined order, each with a deadline file.
   - `CampaignPlanMismatchError` is a new `AssayError` subclass in `mutation.py`, next to `InvalidRejudgeIdError` (`:292`), with `ERROR`/`BAD_LANE_CONFIG`. Export it in `__all__`.
   - At `runner.py:4532`, extend the except to `except (mutation.InvalidRejudgeIdError, mutation.CampaignPlanMismatchError): raise`. The outer handler at `:5385` then renders the whole-lane refusal. Nothing runs after the mismatch.
   - P3b adds `R2CommandProofError` to the same tuple (C1). P7 does **not** add to it; its unknown-ID refusal reuses `InvalidRejudgeIdError`'s shape. Whichever of P6 and P3b merges second rebases the tuple.

**Signals (only in `assay run`, and only on the main thread: C16)**
8. `_cmd_run` calls `restore = cli._install_termination_handlers()` and runs `restore()` in `finally`.
   - The installer registers `cli._termination_signal_handler` for **SIGTERM, SIGINT and SIGHUP** (C32) with `signal.signal`, **only if** `threading.current_thread() is threading.main_thread()`.
   - Off the main thread, `signal.signal` raises `ValueError`, so there it installs nothing and returns a no-op restore. That is the library/threaded-caller case.
   - The handler is idempotent and **never raises**: every call it makes is wrapped, and `liveness._killpg_group` swallows `OSError`. It:
     - calls `runner.request_termination()` **first**;
     - then calls `liveness.terminate_live_process_groups()`;
     - then writes one line, `assay: termination requested (signal N); stopping and writing an incomplete verdict`, with `os.write(2, …)`.
9. The run then unwinds normally:
   - Every live group, whether liveness candidates or default-runner children (Interfaces 6), has been killed **after** `_TERMINATION` was set. `execute_plan` raises LANE_TIMEOUT whatever the runner returned (Interfaces 5). The executor masks them as `budget_exceeded` with **no state record**, together with every unsubmitted candidate. That is the existing expiry path.
   - The verdict is written through the existing LANE_TIMEOUT handlers, and the process exits `4`. A terminated candidate can never become `killed`, `crashed`, `hung` or a recorded `budget_exceeded`.
10. **Natural lane or campaign expiry (C3).** The per-attempt `timeout=` kills the candidate's group (`liveness.py:1405-1439` for liveness runners; Interfaces 6 for the default runner). `_run_attempt` step 2 (Interfaces 7) then turns the lane-bound timeout into an unclassified LANE_TIMEOUT: no record, and re-executed on resume. Oracles O6 and O10 pin this.

**Gate integration (`tools/self-qualification-gate.sh`)**
11. After `wheel_digest` (`:151`) and `assay_bin` (`:154`):
    - For a full run (`requested_lane == self-qualification`): `campaign="b105-${source_commit:0:12}"` and `deadline=".assay/campaign-deadline-$campaign.json"`. If the file is absent, run `"$assay_bin" campaign init --file assay.toml --campaign "$campaign" --lane self-qualification --lane self-qualification-preflight --hours 8 --state-dir .assay/mutation-state-self-qualification --state-dir .assay/mutation-state-self-qualification-preflight --wheel-sha256 "$wheel_digest"`.
    - For a standalone preflight run: `campaign="b105-pre-${source_commit:0:12}"`, with `--lane self-qualification-preflight --hours 1` and only the preflight state dir.
    - Print `B105_CAMPAIGN_DEADLINE=$deadline`.
    - If `campaign init` exits non-zero, echo `B105_CAMPAIGN_INIT_REFUSED=1` and exit non-zero **before** any `assay run` (P6R2-10).
    - **Restarting a campaign on the same commit** (P6R2-10). The gate skips `init` whenever the fixed-name deadline file exists, so after an expiry every re-invocation would exit 4. To start a new campaign on the same commit, the operator deliberately moves **both** `.assay/campaign-deadline-b105-<commit12>.json` and the bound state dirs aside. Document this in CONSUMERS and in the script's header comment.
12. In `run_and_verify_lane` (`:156-214`), define `remaining_s` exactly (P6-9). Compute it with the run-venv python:
    ```
    remaining_s = max(0, floor(expires_at_utc - now_utc))
    ```
    There is no "floored at 1". Then wrap the run:
    `timeout --verbose --signal=TERM --kill-after=30s "$((remaining_s + 120))s" "$assay_bin" run "$lane" … --campaign-deadline "$deadline"`.
    - Assay is **always** invoked, even when `remaining_s == 0`. It then writes the LANE_TIMEOUT verdict itself (Flow 5), and that verdict is verified like any other.
    - The +120 s lets assay's own expiry write the verdict first. The `timeout` is only a failsafe.
    - When the `timeout` failsafe **fires**, GNU `timeout` exits with **124**, or **137** after `--kill-after` sends SIGKILL, or **125–127** when `timeout` itself fails (`man timeout`). None of these is assay's 4. **C31:** the script treats **any status ≥ 124** from the `timeout` wrapper as "failsafe fired; any verdict file is untrusted". It echoes `B105_TIMEOUT_FAILSAFE=1` and exits non-zero **without** running `assay verify` or the report checker on it.
13. `run-gate.toml`:
    - Keep the outer `timeout … 7h30m` on `self-qualification` as the outermost failsafe.
    - Rewrite the comment block at `:38-44` to say:
      - The persisted campaign deadline (B117, A-473) bounds preflight plus the full lane together.
      - This outer timeout and nyxloom's 8 h watchdog are failsafes only. Any failsafe firing is a no-verdict failure, never evidence.
    - Add a one-line comment on the preflight lane saying it creates its own `b105-pre-*` campaign.
    - **Model the real process tree before writing any forwarding claim (P6R2-6), with no docker.**
      - The real lane runs outer `timeout` → `bash` → the gate script → inner `timeout` → assay (`run-gate.toml:36`). GNU `timeout` signals only its child pid and its own process group, and the inner `timeout` runs in a process group of its own.
      - Probe exactly that shape, niced:
        ```
        timeout --signal=TERM 3 bash -c 'timeout 60 python3 -c "import signal,sys,time; signal.signal(signal.SIGTERM, lambda *a: (open(\"/tmp/<unique>\",\"w\").write(\"got\"), sys.exit(42))); time.sleep(30)"'; echo "outer=$?"
        ```
        Afterwards, check whether the marker was written, and use `pgrep -f "<unique>"` / `ps` to confirm whether the inner python is still alive. Kill it by exact pid if so, and clean up the marker.
      - Record the observation in the REPORT. The comment must match it.
      - The expected result is that the outer signal reaches `bash`, but **not** the inner `timeout` or assay. In that case the comment says that when the outer failsafe fires, assay and its candidates end only at **container teardown**: run-gate starts the container with `docker run --rm --init` (`run-gate-project/run-gate.py:4732`, relative to the repository root). That is why the persisted deadline, and not the outer timeout, is the real bound.

### Topology and bounds

- The deadline file lives in the **worktree's** `.assay/` directory. The repository-root `.gitignore:343` ignores `.assay/`. It is created inside the tester-unified container by the gate's own wheel, so `assay_version` matches the binary that validates it.
- Bounds: `--hours` ≤ 24. There is exactly one UTC→monotonic conversion per process.
- The live-group registry holds at most `jobs` candidate groups, plus the baseline, probe or canary child that the main thread may be running. That is `jobs + 1`.
- Termination is cooperative only after all live groups have received SIGKILL, and `_TERMINATION` is set **before** any kill. There is no timing assumption. The 30 s `--kill-after` failsafe exists only for a wedged assay.

### Decision table

| Input / state | Result | Verdict | Exit |
|---|---|---|---|
| no `--campaign-deadline` | today's behaviour, byte-identical | unchanged | unchanged |
| valid file, time remaining, identity matches | lane runs under `min(lane budget, campaign remainder)` | normal | normal |
| valid file, identity matches, already expired | `run_lane`'s first `remaining()` raises; existing `run_lane` LANE_TIMEOUT handler | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` | 4 |
| file unreadable, invalid, or lane not listed | refusal before HEAD | none (`main()` handler prints) | 2 |
| commit, tree or version mismatch (**including an expired file**; identity wins, P6-7) | refusal after the run header | `ERROR`/`BAD_LANE_CONFIG`, whole lane | 2 |
| plan digest mismatch | refusal before any candidate | `ERROR`/`BAD_LANE_CONFIG`, whole lane | 2 |
| SIGTERM or SIGINT during R2 (liveness or non-liveness lane) | `_TERMINATION` set, then live groups killed; in-flight and unsubmitted candidates masked `budget_exceeded`, **no state records** | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` | 4 |
| SIGTERM during the baseline | baseline converted to LANE_TIMEOUT | existing refusal/claim shape for a timed-out baseline | 4 |
| SIGTERM before or during the HEAD read | label read under the non-terminating grace deadline; verdict written | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` | 4 |
| a candidate attempt timed out by the **lane/campaign remainder** (C3) | `_run_attempt` raises LANE_TIMEOUT: masked, **no record**, submission stops; resume re-executes it | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` (lane) | 4 |
| a candidate attempt timed out by **`budget_per_candidate`** | unchanged: recorded `budget_exceeded` | unchanged | unchanged |
| termination requested while the liveness monitor reports hung / timeout / OSError | `execute_plan` raises LANE_TIMEOUT: unclassified, no record | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` | 4 |
| a candidate whose own code signals itself (no termination requested) | classified as today (legacy `killed`; cold mode `crashed` per P3b/C2) | normal | normal |
| `--resume` under a deadline over a record lacking or mismatching `campaign_deadline_sha256` | record rejected, candidate re-executed | normal | normal |
| gate: inner `timeout` failsafe fired (any exit ≥ 124: 124, 125–127, 137; C31) | `B105_TIMEOUT_FAILSAFE=1`; no verify, no checker | none trusted | non-zero |
| gate: `campaign init` refused | `B105_CAMPAIGN_INIT_REFUSED=1`; no `assay run` | none | non-zero |
| termination already requested when `execute_plan` is entered, or when a child group registers | no spawn / group killed on registration; LANE_TIMEOUT, unclassified | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` | 4 |
| cgroup `oom_kill` counter increased during an attempt (C28) | reservations closed; LANE_TIMEOUT: unclassified, no record, submission stops; resume re-executes | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` | 4 |
| cgroup `memory.events` unreadable at run start | `plan` event `oom_guard: "unavailable"`; the OOM rule is inert | normal | normal |
| default-runner child times out | group killed, `wait()`, original `TimeoutExpired` re-raised with partial bytes; group killed again best-effort in `finally` | as today (`budget_exceeded` or C3) | as today |
| `init` with the same identity, file present, own records only | prints path; deadline unchanged | — | 0 |
| `init` with a different identity, file present | refusal | — | 2 |
| `init` where a state dir holds a record lacking or mismatching this file's `campaign_deadline_sha256` | refusal | — | 2 |

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
| C3 lane-bound timeout unclassified | `mutation._run_attempt` | O10 | fake runner + injected deadline + state root | return the result as today → a `budget_exceeded` record exists and resume does not re-execute |
| termination on every exit path | `runner.execute_plan` | O11 | `_TERMINATION` set + runner raising `LivenessHungExpired` / `TimeoutExpired` / `OSError` | check only on normal return → `hung` classified |
| non-liveness group SIGTERM | `default_process_runner` session + registry | O12 | real subprocess assay, `liveness = false`, `os.killpg(assay_pgid, SIGTERM)` | old `subprocess.run` runner → candidate `killed` with a record |
| handler / registry / main-thread guard, in-process (coverage) | cli + liveness | O13 | direct calls through the pinned names, Event reset fixture, monkeypatched `liveness._killpg_group` | blocking `Lock` → handler deadlocks when main thread holds it (O13 runs it with a failsafe join) |
| kill-on-register (P6R2-1) | `liveness.register_live_group` | O13(d) | termination set, then a **real** sleeper group registered | register without the check → sleeper alive after registration |
| default-runner timeout branch (P6R2-2) | `runner.default_process_runner` | O16 | real sleeper child + `_wait_child` monkeypatched to raise `TimeoutExpired` with partial bytes | `communicate()` after the kill, or no `finally` killpg → the failsafe join fires / a grandchild survives |
| OOM rule (C28) | `mutation._run_attempt` + `liveness.oom_kill_count` | O17 | injected `oom_counter` returning a rising sequence | no before/after check → the candidate is classified and recorded |
| label grace unaffected by termination | `LaneDeadline.honors_termination` | O14 | `_TERMINATION` set before `main(["run", …])` | grace honoring termination → exit 2, no verdict |
| C8 record binding | writer + loader + init | O15 | two deadline files, one state dir | unbound record accepted → not re-executed |

### Degrees of freedom

- Private names and the internal decomposition of `_cmd_campaign_init`.

**Not free:**
- file fields, CLI spellings, reason codes, exit codes, the conversion formula, and the order of the validation steps;
- the C23 order in `run_mutation`;
- the pinned names `cli._termination_signal_handler`, `cli._install_termination_handlers`, `liveness._killpg_group`, `liveness.oom_kill_count`, `runner._wait_child` and `liveness._TERMINATION`;
- the `plan` event key `oom_guard`.

## Work

1. **Red first.**
   - Add `tests/test_campaign_deadline.py`, holding O1–O5, O8, O10, O11 and O13–O17. These are in-process and give the coverage the floor needs for the handler, registry, termination, runner-timeout and OOM branches (P6-5).
   - Add `tests/test_campaign_termination_real.py`, holding O6, O7 and O12. These are real subprocesses; they are behavioural proof only, because subprocesses are not coverage-measured.
   - If `tests/zz_slow/` exists on your base (P1 merged), put the real-subprocess file there. Otherwise put it at the top level and note in the REPORT that P1's tier move must include it.
   - Record the red state in `assay-B117-REPORT.md`.
2. Add `mutation.plan_sha256` and `CampaignPlanMismatchError`, and the check in `run_mutation` (Flow 7). Add the `runner.py:4532` except tuple.
3. Add `runner.campaign_bounded_deadline`, the termination event (it lives in `liveness._TERMINATION`; `runner` delegates to it), the `LaneDeadline.honors_termination` field and `remaining` check, and the before-and-after, every-exit-path checks in `execute_plan` (Interfaces 5).
4. Add the `liveness` live-group registry:
   - an `RLock`, with a non-blocking acquire in the handler path;
   - `register_live_group` with kill-on-register, `unregister_live_group` and `terminate_live_process_groups`;
   - the `_killpg_group` killer seam, which swallows `OSError`;
   - `oom_kill_count`;
   - the `LivenessRunner` registration.
5. Rewrite `runner.default_process_runner` (Interfaces 6), with the `_wait_child` seam: on timeout, kill the group, `wait()`, re-raise, and always kill the group in `finally`. Update `tests/test_config_unbounded_budget.py`'s recorder, adding the non-empty assertion.
6. Add the C3 handling in `mutation._run_attempt` (Interfaces 7), with reservations closed before every raise. Add the C28 OOM check (Interfaces 9) and the `oom_guard` key in the `plan` event.
7. Add C8: the record key on write, the loader check, and the `init` refusal (Interfaces 8).
8. Extract `_cmd_plan`'s discovery (`cli.py:1648-1765`) into `_discover_plan_jobs(...)`. This is the single planner-jobs extraction (C29). Prove `assay plan` output is unchanged: the existing plan tests must pass unmodified.
   Wave A W9 extracts `_discover_plan_jobs` (CD20/CD26); P6 consumes the extracted helper.
9. Add the `campaign init` subcommand, `--campaign-deadline`, Flow 1–7 (with the C23 comment at the digest check), the main-thread-only signal wrapper with the pinned names and SIGHUP (Flow 8), and the label-grace `honors_termination=False`.
10. Gate script and run-gate.toml (Flow 11–13), including the process-tree probe. Extend `tests/test_self_lane.py`'s gate-script substring pins (`:176-200`) with `campaign init`, `--campaign-deadline`, `B105_CAMPAIGN_DEADLINE=`, `B105_CAMPAIGN_INIT_REFUSED`, `B105_TIMEOUT_FAILSAFE`, and the ≥ 124 status test.
11. **C15.** `LaneDeadline` gains a field, so regenerate `tests/fixtures/dataclass-contract.json` with the command P1 (B112) documents in DESIGN-GUIDE. If P1 has not merged yet, the fixture does not exist; note that in the REPORT, because P1 then generates it including this field. Commit the regenerated fixture in the same change.
12. Run the focused tests, then the docs (see Docs sync), CHANGES, REPORT, and commit.

## Oracles

- **O1: init writes exactly the specified document.** Use a toy lane repository with R0 and R2 (the shape of the busy-loop test in `tests/test_cli_run.py`, found by name) and run `main(["campaign", "init", ...])`.
  - *Observable:*
    - the key set equals the ten keys listed above;
    - `commit` and `git_tree` equal real `git rev-parse`;
    - `plan_sha256[lane]` equals `mutation.plan_sha256([row["id"] for row in <assay plan JSON>["candidates"]])` from `main(["plan", lane])` on the same tree;
    - the value is `null` for an R0/R1 lane.
  - *Negative:* a digest computed over shard-filtered or sorted IDs differs.
- **O2: re-init never extends.** Init once with `--hours 1`, then again with `--hours 5` and the same identity. `expires_at_utc` is unchanged and the exit is 0. After a new commit, a different identity gives exit 2 and the file is unchanged. With the file deleted and a state dir holding a **real unbound state record** (P6R2-7), init gives exit 2 and nothing is written. The record is a `<64hex>.json` written by a prior `assay run --resume --state-dir` without `--campaign-deadline`, so it has no `campaign_deadline_sha256` key.
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
    - The test file has `if guard(0): pass`. In the else-branch it writes `os.getpid()` and its `/proc/self/stat` start time (field 22) to an absolute path embedded in the test source (`tmp_path / "candidate.pid"`).
    - It then spins with `parent = os.getppid()` and `while os.getppid() == parent: pass` (P6-10, host safety). If assay, or the whole test process, is killed for any reason, the candidate is re-parented and the loop ends by itself; it never burns a CPU forever on the shared host. Spinning keeps CPU growing, so liveness never calls it `hung`.
    - The lane has `rigor = ["R0","R2"]`, `liveness = true`, `budget_per_candidate = "600s"`, `budget = "15m"`, **`jobs = 1`**, and **`operators = ["python:compare-swap"]`** (P6R2-3).
    - `return x <= 0` would otherwise also yield a falsy-swap spinner, which would make "the candidate's ID" ambiguous. With compare-swap only, there is exactly one candidate.
  - Start assay as a real subprocess, **in its own session** (P6R2-3): `subprocess.Popen([sys.executable, "-c", "import sys; from assay.cli import main; sys.exit(main(sys.argv[1:]))", "run", "package", "--file", "assay.toml", "--verdict-json", str(v)], env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}, cwd=repo, start_new_session=True)`.
    - Assert `os.getpgid(assay.pid) != os.getpgid(0)` before sending any signal. A group signal then can never reach pytest, its xdist siblings, or, inside a B105 candidate, the candidate suite itself.
  - Wait for the pid file, polling at 0.2 s with a **failsafe of 180 s** that fails the test. Then send `os.kill(assay.pid, signal.SIGTERM)` and `assay.wait(timeout=120)` as a failsafe.
  - *Observable:*
    - exit code 4;
    - the verdict status is `BUDGET_EXCEEDED` with reason `LANE_TIMEOUT`;
    - the candidate's ID is in R2 `budget_exceeded`, and not in `killed`, `crashed` or `hung`;
    - no state record exists for it (run with `--resume --state-dir`);
    - `/proc/<candidate pid>/stat` is absent, or shows state `Z`, **or** shows a different start time. The last case is pid reuse; compare against the recorded start time, never the pid alone.
  - *Negative:* without the handler, assay dies on the default SIGTERM action with exit `-15`, writes no verdict, and the spinning candidate is still in state `R`, with its start time matching.
  - **Cleanup (always, in `finally`):**
    - `os.killpg(candidate_pid, SIGKILL)` only if `/proc/<pid>/stat`'s start time still matches, ignoring `ProcessLookupError`;
    - then `os.killpg(assay_pgid, SIGKILL)`, where `assay_pgid` was recorded right after spawn, **only if** `assay.poll() is None`, ignoring `ProcessLookupError`.

    A red run then never leaks a spinning process onto the shared host, and never kills a reused pid.
- **O8: the gate script is wired.** `tests/test_self_lane.py` asserts, in `tools/self-qualification-gate.sh`:
  - the substrings `campaign init`, `--campaign-deadline "$deadline"`, `B105_CAMPAIGN_DEADLINE=`, `B105_CAMPAIGN_INIT_REFUSED` and `B105_TIMEOUT_FAILSAFE`;
  - that the `timeout` wrapper wraps the `assay run` invocation;
  - that the failsafe test covers **every status ≥ 124**, not only 124 (C31), and runs before `assay verify`. For example, pin the substring `-ge 124`.
- **O10: C3, a lane-bound timeout is unclassified and re-executed on resume.** In-process, with a toy R2 lane of 2 candidates, a `state_root`, `jobs=1`, and `budget_per_candidate` omitted, so every attempt is lane-bound.
  - Use an injected `LaneDeadline` whose `remaining()` returns a finite value. The fake `process_runner` raises `subprocess.TimeoutExpired` for candidate A (the path taken when the lane remainder expires) and passes for B.
  - The lane also declares an `equivalence_artifact` (a SQL-style fixture is not required: monkeypatch `mutation._arm_artifact_reservation` to return a recorder whose `close()` is observed) (P6R2-8).
  - *Observable:* A is in `budget_exceeded`, **no state record exists for A**, B was never submitted (it is masked after the stop), and A's reservation recorder saw exactly one `close()`.
  - A second run with `--resume` and a fresh deadline, where the fake passes A and B, executes A (the runner is called for A) and B.
  - *Contrast:* with `budget_per_candidate = "1s"` and the lane remainder larger, the same `TimeoutExpired` is **recorded** as `budget_exceeded`, and resume does **not** re-execute A.
  - *Negative:* today's code writes A's record, and the resume replays it.
- **O11: termination is checked on every path of `execute_plan`.**
  - (a) **After the runner.** Call `runner._reset_termination_for_tests()`. Use fake runners that call `runner.request_termination()` and then, in turn:
    - return exit 0;
    - raise `liveness.LivenessHungExpired`;
    - raise `subprocess.TimeoutExpired`;
    - raise `OSError`;
    - raise an `AssayError(ERROR, GIT_FAILED)` (P6R2-9).
    - *Observable:* every call raises `AssayError(BUDGET_EXCEEDED, LANE_TIMEOUT)`. For the last case, `__cause__` is the original error.
  - (b) **Before the runner.** With termination already requested, `execute_plan` raises LANE_TIMEOUT and the fake runner is **never called**.
  - *Negative:* a check only after a normal return lets the hung, timeout, OSError and AssayError results through. With no pre-check, (b) calls the runner.
- **O12: non-liveness group SIGTERM (P6-3).** O7's shape, with `liveness = false` so the default runner is used. Assay is spawned with `start_new_session=True`, as in O7. Assert `os.getpgid(assay.pid) != os.getpgid(0)`, then send `os.killpg(os.getpgid(assay.pid), signal.SIGTERM)` to simulate GNU `timeout`'s group signal. That group contains only assay (P6R2-3).
  - *Observable:* exit 4; the candidate is in `budget_exceeded` with no record, and never in `killed`; the candidate is not alive (start-time check).
  - *Negative:* with the old `subprocess.run` runner, the candidate dies with −15 in assay's group, can be classified `killed`, and gets a record.
- **O13: in-process handler, registry and main-thread guard (P6-4, P6-5, C16, P6R2-4).**
  - **Host safety.** Every O13 case monkeypatches `liveness._killpg_group` with a recorder, and registers only **synthetic** pgids, for example `10**9 + n`, which are never passed to a real `os.killpg`. The one exception is (d), which uses a real sleeper it owns.
  - Every helper thread is `daemon=True` and joined with a 60 s failsafe.
  - The module has an autouse fixture that calls `runner._reset_termination_for_tests()` and clears the registry and the deferred flag before and after each test.
  - (a) Calling `cli._termination_signal_handler(signal.SIGTERM, None)` directly:
    - sets the termination event;
    - makes the recorder see every registered synthetic pgid with `SIGKILL`;
    - writes exactly one line to fd 2 (capture with `capfd`).

    With the recorder patched to raise `OSError`, the handler still returns and still writes its line.
  - (b1) **The lock is held by the same thread.** In a daemon helper thread, acquire the registry lock, then call the handler in that same thread. This simulates a signal arriving while the main thread holds the lock. It must return, which the `RLock` guarantees, and the recorder must see the registered groups. A plain `Lock` deadlocks there, and the failsafe join fails the test.
  - (b2) **The lock is held by another thread.** A daemon helper thread acquires the lock and waits on an Event. The main thread calls the handler, which must return without blocking, pass the snapshot to the recorder, and set the deferred flag. Then release the helper. The next `unregister_live_group` call re-runs termination under the lock, and the recorder sees it.
  - (c) Calling `cli._install_termination_handlers()` from a daemon `threading.Thread` installs nothing and raises nothing, `signal.getsignal(SIGTERM)` is unchanged, and the returned restore is a no-op. Called on the main thread, it installs the handler for SIGTERM, SIGINT **and SIGHUP**, and `restore()` puts back the previous handlers.
  - (d) **Kill-on-register (P6R2-1).**
    - Start a real sleeper, `subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)`. Leave `_killpg_group` **unpatched** for this case, because the sleeper's own pgid is owned by the test.
    - Call `runner.request_termination()`, then `liveness.register_live_group(sleeper.pid)`.
    - *Observable:* `sleeper.wait(timeout=60)`, which is a failsafe, returns `-signal.SIGKILL`.
    - *Negative:* a register without the check leaves the sleeper alive, and the failsafe fails the test.
    - Cleanup kills the sleeper's group if `sleeper.poll() is None`.
- **O14: termination before the HEAD read still writes a verdict (P6-8).** Call `runner.request_termination()`, then `main(["run", lane, "--verdict-json", v, …])` in-process.
  - *Observable:* exit 4; `v` exists with `BUDGET_EXCEEDED`/`LANE_TIMEOUT`; the commit label equals HEAD.
  - *Negative:* a grace deadline that honours termination gives exit 2 and no verdict.
- **O15: C8 binding.** Init deadline file F1 and run R2 with `--resume --state-dir S`: the records carry `campaign_deadline_sha256 == sha256(F1)`.
  - (a) Write a second valid file F2 for the same identity at a different path. `--resume` with F2 re-executes every candidate: the records are rejected, and the `resume` event's rejected count equals the record count.
  - (b) Delete F1, then `init` with a new campaign name over S. Exit 2, and nothing is written.
  - *Negative:* a loader that ignores the key replays the F1 records under F2.
- **O16: the default-runner timeout branch, in-process (P6R2-2, C32).**
  - Start a real child through `runner.default_process_runner` with `argv=[sys.executable, "-c", <spawn a grandchild that sleeps 600 s and inherits stdout, then sleep 600 s>]`.
  - Monkeypatch `runner._wait_child` so that its **first** call raises `subprocess.TimeoutExpired(cmd, 5, output=b"partial-out", stderr=b"partial-err")`. This models the timeout firing, with no wall-clock wait.
  - *Observable:*
    - the call re-raises `TimeoutExpired`, with `output == b"partial-out"`;
    - the child **and** the grandchild are dead: poll their pids, recorded by the child to a tmp file, with a 60 s failsafe, and compare `/proc` start times;
    - the registry no longer holds the child's pgid.
  - *Negative:* a `communicate()` after the kill blocks on the grandchild's inherited pipe, and the failsafe fails the test. A missing group kill leaves the grandchild alive.
  - A second case, on a normal exit where the child spawned a grandchild in the same group, shows the `finally` group kill leaves no grandchild alive.
  - Cleanup kills every recorded pid only if its `/proc` start time matches.
- **O17: the OOM rule (C28).** In-process, with a toy R2 lane of 2 candidates, `jobs=1`, a state root, and an injected `oom_counter` returning `0, 0` for A's window and `0, 1` for B's.
  - *Observable:*
    - A is classified and recorded as today;
    - B is in `budget_exceeded` with **no state record**, its reservations are closed, and the verdict is `BUDGET_EXCEEDED/LANE_TIMEOUT`;
    - `--resume` with a flat counter re-executes B;
    - with an injected counter that returns `None`, the `plan` event carries `"oom_guard": "unavailable"` and B is classified normally.
  - `liveness.oom_kill_count` is unit-tested against tmp files containing `oom_kill 3`, no `oom_kill` line, and a missing file, which give `3`, `None` and `None`.
  - *Negative:* no before/after check classifies and records B.

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
  - SIGTERM, SIGINT and SIGHUP semantics:
    - set the flag first, then kill every live group;
    - every child runs in its own session and is registered, and a group registered after the sweep kills itself on registration;
    - a result that finished or failed during termination is never classified;
    - the default runner kills its group on timeout and in `finally`;
  - why the outer `timeout` failsafe reaches only `bash`, so assay ends at container teardown, and why the persisted deadline, not the failsafe, is the real bound (the P6R2-6 probe result);
  - C3: a candidate cut off by the lane or campaign remainder is unclassified and re-executed on resume, while a per-candidate budget exhaustion is a recorded outcome;
  - C28: a cgroup OOM kill during an attempt makes it unclassified. `oom_guard: "unavailable"` means the rule is inert on this host;
  - C8: records are bound to their deadline file, and D7's import path is P9's, not `--resume`'s.

  Cite A-473.
- **CONSUMERS (HOW):** a pasteable sequence: init, run, run again (resume), and reading the refusal. State the exit codes 4 and 2 from the decision table, and that any gate status ≥ 124 is a failsafe with no trusted verdict.
  - Also document how to restart a campaign on the same commit: move the deadline file **and** the bound state dirs aside.
- **CHANGES:** `### Added` and `### Changed` bullets for B117.

## Scope / forbid

- **Touch:**
  - `src/assay/cli.py`;
  - `src/assay/runner.py`: `LaneDeadline` (the new field), the termination helpers (delegating to `liveness._TERMINATION`), `execute_plan`, `default_process_runner` and `_wait_child`, the threading of `expected_plan_sha256`/`campaign_deadline_sha256`/`oom_counter`, and the except tuple at `:4532`;
  - `src/assay/mutation.py`: `plan_sha256`, `CampaignPlanMismatchError`, the `run_mutation` kwargs and check (at the C23 position), `_run_attempt`'s result handling (C3, Interfaces 7, reservations closed before every raise), the C28 OOM check and the `plan` event's `oom_guard` key, the state-record write (one optional key), and `_load_validated_state_record` (the C8 check) only;
  - `src/assay/liveness.py`: the registry (with kill-on-register), `_killpg_group`, `_TERMINATION`, `oom_kill_count`, and `LivenessRunner`'s register/unregister only;
  - `tools/self-qualification-gate.sh`, `run-gate.toml` (comments only);
  - `tests/test_campaign_deadline.py`, `tests/test_campaign_termination_real.py`, `tests/test_self_lane.py` (substring pins), and `tests/test_config_unbounded_budget.py` (the recorder move plus the non-empty assertion only);
  - `tests/fixtures/dataclass-contract.json` (C15, regenerated);
  - README, DESIGN-GUIDE, CONSUMERS, CHANGES, `nyxloom-trove/reports/assay-B117-REPORT.md`.
- **Forbid:**
  - the verdict schema, `verdict.py`, `verify.py`, `ReasonCode`, `EXIT_CODES`;
  - `assay.toml` lane keys;
  - `MUTATION_STATE_SCHEMA_VERSION`;
  - the executor loop (that is P4; C3 lives only in `_run_attempt`);
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
   - `tests/test_runner_execute.py`, `tests/test_config_unbounded_budget.py`, `tests/test_config_rigor_grammar.py` (the runner rewrite)
   - `tests/test_runner_p23_cleanup_and_budget.py`, `tests/test_mutation_resume_sharding.py`, `tests/test_b105_mutation_boundaries.py` (C3 / C8)
   - `tests/test_cli_run.py`, or its `tests/zz_slow/` split if P1 has merged
   - `tests/test_self_lane.py`
   - `tests/test_mutation_progress_budget_plan.py`
   - `tests/test_b105_cli_boundaries.py`
   - `tests/test_state_dir_resume.py`
2. `cd <worktree>/assay && python ./run-gate.py tester-unified > /tmp/b117-gate.log 2>&1; echo "exit=$?"`. Then, in a **separate** step, `grep -E "ASSAY_GATE_CONTAINER_EXIT|ASSAY_REGISTERED_GATE_COMPLETE" /tmp/b117-gate.log`.
3. The gate script and B105-collected source changed, so also run `python ./run-gate.py self-qualification-preflight > /tmp/b117-preflight.log 2>&1`, only when no other gate is running. In a separate step, check that `ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1` and `B105_CAMPAIGN_DEADLINE=` both appear.

## BLOCKED rule

If a named contract cannot be met as specified, or the scope needs a forbidden file, STOP. Write `BLOCKED: <reason>` to `nyxloom-trove/reports/assay-B117-REPORT.md`, commit, and exit. Do NOT improvise a workaround.

Specific triggers:
- `execute_plan`'s termination check changes an existing test's classification. That would mean a test depended on a termination-time result.
- the `_cmd_plan` extraction changes `assay plan` output.
- O7 or O12 cannot be made to pass without a timing assertion.
- the process-tree probe shows `timeout` forwarding in a way that contradicts both documented outcomes. Record it, and do not guess.
- mirroring `LivenessRunner._cleanup_process_group` in the default runner's `finally` changes a liveness test's result.
- the `default_process_runner` rewrite changes any existing test's observable result (exit code, output tails, decoding), other than the named recorder move;
- C3 changes an existing test's classification of a **per-candidate-budget** timeout.

## Report

`assay-B117-REPORT.md` must contain:
- the traceability table with the actual test names and red/green counts;
- the log paths and marker lines for the gate and the preflight;
- the files touched;
- whether P1's tier move must include `test_campaign_termination_real.py`;
- the process-tree probe's observation (P6R2-6);
- whether `/sys/fs/cgroup/memory.events` is readable inside tester-unified (`oom_guard` value).

Commit with this trailer:

```
Co-Authored-By: Claude Sonnet <noreply@anthropic.com>
```

Do not merge.
