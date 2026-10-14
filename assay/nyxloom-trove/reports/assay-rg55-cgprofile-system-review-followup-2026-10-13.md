# Assay, Run-Gate, CMRU and cgprofile design follow-up — 2026-10-13

- Reviewer: Claude Opus 5.5, high effort, read-only continuation
- Persistent Claude session: `28227ce4-cbdc-471a-8b69-a430af63fb67`
- Bound checkout: `/workspaces/vbpub/.worktrees/cgprofile-cp20-contract2-20261012`
- Bound HEAD: `3f3012fcd2cdf1474b7a50d916fe38960e205507`
- Bound tracked diff SHA-256: `b2a6c5601bd1e2a537e919a8a01ceaa5a9cbefa392a3e49abd4a0caa01067113`
- The reviewer made no code edits and ran no tests or gates.

---

I've bound this follow-up to the same source state as the previous review, re-read the relevant source, and found two earlier claims that were wrong and two that were overstated. The core proposal is one shared process-tree window, owned by Assay and imported by CMRU. Read-only: no files edited, no tests or gates run.

**Binding.** HEAD is `3f3012fcd2cdf1474b7a50d916fe38960e205507`. The tracked `git diff` hashes to sha256 `b2a6c5601bd1e2a537e919a8a01ceaa5a9cbefa392a3e49abd4a0caa01067113` (25 files, +966/−180). One thing differs from the previous review: there is a third untracked file, `assay/nyxloom-trove/reports/assay-rg55-cgprofile-system-review-2026-10-13.md`. It looks like the persisted copy of the previous review and contains no source.

## 1. Corrections to the earlier review

- **H2's example mutant was wrong.** I said `and`→`or` at `if not descendants and not direct_children` would leak a process. It doesn't: `liveness.py:214-249` sends SIGKILL to every descendant before that check runs.
  - The real leak paths are mutants that skip the kill-or-reap step. One plausible but unproven example is a mutant of the deadline comparison: it raises after the kill but before the reap. The killed process stays a zombie of the inner pytest, which is itself a subreaper. When pytest exits, the zombie passes to the outer Assay, which reaps and counts it, and the lane aborts.
  - H2 still stands, because it never depended on that example. It rests on two facts:
    - an orphan that already exited and was only reaped is still counted (`killed.add(child)` after `waitpid`);
    - a leftover after a normal exit raises `AssayError`, so the lane aborts with no candidate record, and resume re-runs the same mutant.
- **M1's trigger was wrong.** I said the witness drain thread exits when the candidate closes its pipe. In fact the parent holds `write_fd` until `ReceiptCapture.finish()` (`mutation_witness.py:383-389`). That runs after the process runner returns and after the after-counters are read (`mutation.py:4299-4305`), so it can't race the cleanup scan.
  - The defect itself is real, with a narrower trigger. Any Assay thread exiting during a `/proc/self/task` scan raises `FileNotFoundError`, for example a lingering `assay-progress-heartbeat` thread whose join timed out (`runner.py:1116-1122`).
  - That `OSError` reaches `runner.py:1406`, which records the candidate as ERROR/EXEC_FAILED, i.e. `crashed`, and the subreaper stays on.
- **The PGID-reuse low finding was overstated.** After `communicate()` reaps the leader, `killpg(leader)` (`runner.py:430`) can only hit an unrelated process if all of these hold:
  - every member of the group is gone, since any live member keeps the group number from being reused;
  - the PID counter wraps around to that number;
  - the new owner made itself a group leader.

  Inside an ephemeral container's PID namespace, the new owner could only be the same candidate tree. In practice the risk is nil.
- **M3's false-kill wording was overstated.** Cold witness re-runs the failing test, which makes a single flake much less likely to become a kill. The risk remains for a timing test that fails again under the same load.
- **Claims that still hold as stated:**
  - H1: isolation applies only to liveness candidates, and every baseline uses the plain runner (`runner.py:4139`, `4929`, `4831-4852`).
  - H2's abort-and-rerun loop.
  - M1's leftover subreaper and wrong classification.
  - M2: CMRU's `_pid_children` treats a missing `children` file as empty (`mutation_campaign.py:159`). It is latent: Debian kernels ship that file.
  - The M3 timing races.
  - M6: resume doesn't check `descendants_killed`.

## 2. What the source does today

| Native R2 run | Process runner | Cleanup of detached processes | Counter window |
|---|---|---|---|
| R0/R1 coverage baseline (`runner.py:~4130`) | `default_process_runner` (output pipes, `communicate`) | process group only; `killpg` runs after the leader is reaped | **none** |
| Cold-witness r2-baseline (`runner.py:4929`) | `default_process_runner` | same | **none** |
| Candidate, liveness on (`runner.py:4843`) | `LivenessRunner` (file output) | subreaper sweep; a leftover aborts the lane | `mutation.py:4276/4301` |
| Candidate, liveness off (pwmcp `r2`, mdt `host_setup`, non-pytest commands) | `default_process_runner` | process group only; a `setsid` child holding the pipe blocks `communicate` until timeout | `mutation.py:4276/4301`, but the after-sample runs while such children may still be alive |
| Witness attempts | same runner as the candidate | same | per attempt |

## 3. Proposed design: one process-tree window for every native execution

### What it guarantees

When a native-R2 command returns, for any outcome including a timeout, two things are true:
1. the command's whole process tree is dead and reaped, and the leader's own exit status is preserved;
2. the after-counter sample is taken after that point, and the next before-sample is taken before the next spawn.

If either can't be guaranteed, the result is a lane-fatal `ProcessTreeError` and no result is recorded for that command.

### How it proves the tree is empty without trusting procfs

Assay is a child subreaper during the window, so every orphan is re-parented to Assay. Therefore "Assay has no children" means "there are no descendants".

The proof is `os.waitid(P_ALL, 0, WEXITED|WNOHANG|WNOWAIT)` raising `ChildProcessError` (ECHILD). The kernel answers that call, it covers all of Assay's threads, and with `WNOWAIT` it reaps nothing.

Procfs is used only as a hint for which live children to signal. A blind or unsupported procfs can therefore cost availability (the deadline expires), but it can never certify an empty tree.

### Rules for signalling, which make PID reuse impossible

- `killpg(leader)` is sent **only while the leader is unreaped**. An unreaped child's PID and group number can't be reused.
- After the leader is reaped, SIGKILL goes only to Assay's **own unreaped direct children**. A dead child stays a zombie until Assay reaps it, so its PID is held.
- Grandchildren are not signalled by number. They are re-parented to Assay as their parents die, and the loop collapses the tree one level per round.
- Optional hardening for deep trees whose middle process is stuck in uninterruptible sleep: `os.pidfd_open(pid)`, re-read `/proc/<pid>/stat` and require the start time seen at scan, then `signal.pidfd_send_signal`.

### New module: `assay/src/assay/process_tree.py` (about 250 lines)

```python
class ProcessTreeError(AssayError):        # ERROR/EXEC_FAILED; always fatal to the lane
    phase: Literal["precondition", "subreaper", "spawn", "signal", "reap", "deadline", "restore"]
    remaining_hint: tuple[int, ...]        # procfs hint only, for diagnostics

@record  # frozen; strict to_dict/from_dict
class ProcessTreeEvidence:
    schema: Literal["assay-process-tree/1"]
    leader: Literal["exited", "signaled", "timeout-killed"]
    group_signalled_before_reap: bool
    outliving_descendants: int   # processes Assay reaped, excluding the leader (live or already exited)
    signalled: int               # of those, how many Assay SIGKILLed while alive
    cleanup_seconds: float
    @property
    def clean(self) -> bool: return self.outliving_descendants == 0

class ProcfsHints:                          # injectable for tests
    def direct_children(self, pid: int) -> frozenset[int]:
        # /proc/<pid>/task/*/children, tolerating per-thread ENOENT/ESRCH;
        # if the children file is unsupported, fall back to a ppid scan of /proc/*/stat;
        # if /proc is unreadable, return an empty set (a hint, never a proof)

class ProcessTreeWindow:                    # context manager; exactly one leader per window
    def __enter__(self):                    # take module lock; turn subreaper on (remember prior state);
                                            # require waitid(P_ALL) -> ECHILD, else ProcessTreeError("precondition")
    def spawn(self, argv, *, env, cwd, stdout, stderr, pass_fds=()) -> subprocess.Popen
                                            # start_new_session=True, stdin=DEVNULL, output to files only
    def kill_leader_group(self) -> None     # no-op unless proc.returncode is None
    def close(self) -> ProcessTreeEvidence  # kill_leader_group(); proc.wait(5 s); collapse(deadline);
                                            # restore subreaper only once empty is proven
    def __exit__(self, *exc):               # always close(); wrap ANY exception from cleanup
                                            # as ProcessTreeError (fixes M1)

def run_isolated(argv, *, env, cwd, timeout, stdout_path, stderr_path, pass_fds=()) -> IsolatedRun
    # IsolatedRun(returncode, process_tree); on timeout raises TimeoutExpired with .process_tree set
```

**The collapse loop:**
1. **Reap whatever has exited.** Repeatedly peek with `waitid(P_ALL, WEXITED|WNOHANG|WNOWAIT)`:
   - `ChildProcessError` means the tree is proven empty: return the evidence;
   - `None` means children exist but none have exited: go to step 2;
   - otherwise reap exactly that child with `waitpid(info.si_pid, 0)`, recording whether its status was `WIFSIGNALED == SIGKILL` and whether Assay had signalled it.

   The leader has already been reaped by `Popen.wait`, so `Popen` keeps its real exit status (this avoids the ECHILD → returncode 0 trap).
2. **Kill.** Send SIGKILL to each PID from `hints.direct_children(os.getpid())`.
3. **Check the deadline.** Past it, raise `ProcessTreeError("deadline")` and leave the subreaper on. Otherwise sleep 5 ms and repeat.

Every child seen inside the window belongs to the candidate. This rests on the lock, the empty-children precondition, `jobs = 1` (B156; assert it when a window opens), and on Assay never spawning anything else during a window. Git integrity checks run after the runner returns.

### Changes in Assay

- **`liveness.py`:**
  - delete `_isolate_candidate_process_tree`, `_require_empty_child_set`, `_candidate_descendant_pids`, `_reap_candidate_descendants`, `_restore_candidate_subreaper` and `_cleanup_candidate_process_tree`;
  - `LivenessRunner.__call__` becomes: open a window, spawn through it, run `_monitor`, then `close()`;
  - on `TimeoutExpired`, attach `exc.process_tree` and re-raise; on a normal return, attach `completed.process_tree`;
  - drop the "leaving descendant processes" `AssayError` (`liveness.py:1896-1903`).
- **`runner.py`:**
  - add `isolated_process_runner`, the same protocol as `default_process_runner` but file-backed and windowed;
  - `run_lane` uses it for the **whole lane run** of any lane with native R2: R0/R1 baseline, r2-baseline, non-liveness candidates and the canary;
  - `CommandResult` gains `process_tree: ProcessTreeEvidence | None`;
  - `_execute_plan_inner` copies it from the result or the exception, as it already does for `liveness_resource_evidence` (`runner.py:1398-1400`), and lets `ProcessTreeError` propagate before reaching `except OSError`;
  - a **baseline guard**: if a coverage baseline or r2-baseline result has `process_tree` not clean, refuse with ERROR/EXEC_FAILED naming the baseline before any candidate runs (cheap, deterministic, and it points at the test suite);
  - optionally bracket baselines with counters as well; an event during a baseline means refusal, matching CMRU.
- **`mutation.py`:**
  - when the result's `process_tree` is not clean and the bucket is not `budget_exceeded`/`hung`, set `forced_bucket = "crashed"`, the existing mechanism (`mutation.py:2169`, `4585`);
  - if `execution_error` is a `ProcessTreeError`, raise it directly without the after-read, so the ancestor-attribution error doesn't hide the cause;
  - the state record writes `process_tree_evidence`.

## 4. Result semantics

### Assay

| Case | Bucket | Record written? | Effect on R2 | On resume |
|---|---|---|---|---|
| Clean tree, exit 1 (witnessed) | killed | yes | — | reused |
| Clean tree, exit 0 | survived | yes | FAIL | reused |
| **Leader exit (any code) with outliving descendants ≥1, cleanup succeeded** | **crashed**, `forced_bucket`, with evidence | yes | ERROR/EXEC_FAILED (`judge_mutation`: any crashed → ERROR) | reused; `--rejudge-outcome crashed` re-runs it (`mutation.py:3892`) |
| Timeout or hung, any orphans, cleanup succeeded | budget_exceeded / hung | yes, with evidence | as today | as today |
| Event in the candidate's own cgroup | crashed (existing) | yes | ERROR | reused |
| Event only in a shared parent cgroup | — (B155 lane abort, unchanged) | no | lane ERROR | re-run |
| **Cleanup failure** (precondition, prctl, EPERM, deadline, restore) | — | **no** | lane ERROR, `ProcessTreeError` | re-run |
| Spawn failure (ENOENT) after the window proves empty | crashed (existing) | yes | ERROR | reused |
| Baseline leaves descendants | — | — | refusal before any candidate | — |

**Decision: yes, a cleaned detached descendant becomes a resumable crashed candidate.** The after-sample is taken after cleanup, so the window is sound. The process can't contaminate the next candidate. Recording the candidate as crashed keeps the claim non-kill without making the lane impossible to finish.

Both live orphans and already-exited orphans count. Whether a short-lived helper is still alive at sweep time depends on timing; counting both makes the classification deterministic, and it is always non-kill.

### CMRU

- A leak after a normal exit becomes a `crashed / descendant-leak` record. The branch at `mutation_campaign.py:373-375` becomes reachable. The campaign continues and ends `failed`, listing the candidate.
- Resume reuses only kills, so it re-runs the candidate; it no longer aborts.
- A cleanup failure is an `infrastructure-error` abort with evidence written (the existing path).
- A baseline leak is still refused.
- Reusing a kill on resume additionally requires `process_tree.outliving_descendants == 0` (fixes M6).
- `_run` becomes a thin wrapper over `assay.process_tree.run_isolated`, with temp files and the 1 MiB tail. Delete `_enable_child_subreaper`, `_pid_children`, `_candidate_descendants`, `_require_no_candidate_children`, `_sweep_candidate_descendants` and `_terminate_candidate`. The tool already imports Assay internals and binds `assay_source_commit` on resume, so a change to the shared code invalidates old evidence automatically.

## 5. PID reuse and procfs visibility

**Can the pending diff signal a reused, unrelated PID?** Only for **grandchildren**.
- `liveness.py:220-223` and `mutation_campaign.py:202-210` send SIGKILL to every scanned descendant by number. A grandchild can be reaped by its live parent between the scan and the kill, and the PID counter could wrap to that number.
- In an ephemeral container's PID namespace, any process that receives the reused number belongs to the same tree, or is a later `docker exec` session.
- In a shared namespace, such as unit tests in the devcontainer, an unrelated process could in principle be hit. It would need the counter to wrap within milliseconds.
- The leader before it is reaped, and direct children, are both safe.
- The proposed rules (section 3) remove the risk by design. The remaining low PGID note is `runner.py:430` for non-native lanes, which can stay.

**How procfs failures should be handled:**
- **Hints only.** Tolerate a vanished thread (ENOENT/ESRCH); fall back to the ppid scan when the `children` file is unsupported; return no hints if `/proc` is unreadable.
- **The proof is always waitid ECHILD.** A blind procfs makes the window time out into `ProcessTreeError`, which is fail-closed and fatal to the lane.
- This also fixes M2 by construction, and M1 through the wrapping in `__exit__`.

## 6. Identity and evidence changes

- **Assay judge label.** The diff's `assay-judge-identity/11` has never been committed or shipped. Define `/11` to include the process-tree semantics. Move to `/12` only if any `/11` state directory exists from local or pilot runs.
- **Assay state records.** `MUTATION_STATE_SCHEMA_VERSION` stays 1, under the B088 convention that the label plus the tool version invalidate old records. Two validator rules are added to the expected-bucket logic (`mutation.py:2213-2260`):
  - a native executed record must carry `process_tree_evidence`;
  - a record whose evidence is not clean must have bucket `crashed` (or `budget_exceeded`/`hung`).

  Records that fail validation are treated as "not evidence for this run" and re-executed.
- **Verdict.** Schema v16 is unreleased, so fold this in without a v17:
  - add a `process_tree_evidence` definition to `MutantOutcome`, required wherever `resource_limit_evidence` is (`verdict.py:1928-2040`, `verdict.schema.json:1530/1648`);
  - `verify.py` refuses killed, survived or equivalent outcomes whose evidence is not clean.
- **CMRU evidence.** Keep `schema_version` 3 but set `candidate_isolation = "process-tree/1"`; it never shipped, but local runs may have written `subreaper/1`. Refuse `subreaper/1`, and require a per-result `process_tree` object.
- **Decisions and docs.**
  - Record A-493 superseding A-492's "abort the lane" rule.
  - Amend B162 and KI-70.
  - Update the three Assay docs: README, DESIGN-GUIDE, and CONSUMERS (including the claim at `:3416`).
  - Update the three CMRU docs: README `:729`, DESIGN-GUIDE `:559`, CONSUMERS `:727`.

## 7. Behavioural test matrix

Every real-process test runs in a **fresh helper process** that is itself the subreaper, inside an outer `subprocess.run(timeout=…)`, with `finally` cleanup from a PID file. A mutated cleanup can then only leak into the helper, which the outer candidate now records as crashed rather than aborting. No assertion depends on a sub-second timer: the timeout path is driven by readiness files plus an explicit `kill_leader_group()`/`close()`, and the timer path gets its own test with no tree.

**Window tests (Assay; CMRU uses them unchanged):**

| # | Setup | Expected |
|---|---|---|
| W1 | Exit 0, and exit 1, clean | evidence clean; returncode preserved |
| W2 | Same-group child alive after the leader exits | outliving 1, signalled 1; `/proc/<pid>` absent (not a zombie) |
| W3 | `setsid` child alive | same as W2 |
| W4 | Double fork, middle process exits | the re-parented grandchild is reaped and counted |
| W5 | Orphan exits before the leader does | outliving 1, signalled 0 (still not clean) |
| W6 | Live chain of depth 5 | collapses in several rounds; all gone |
| W7 | Leader exits 3 while orphans exist | returncode 3 (ECHILD/0 regression) |
| W8 | Fork-hopper loop | either proven empty or `ProcessTreeError`; never clean with a marked process left (scan `/proc/*/environ` for a marker) |
| W9 | Inject `waitid` always returning `None` | deadline → `ProcessTreeError`; subreaper left on; no evidence returned |
| W10 | Inject empty procfs hints while a live child exists | deadline → `ProcessTreeError` (procfs is not the proof) |
| W11 | Inject ENOENT on a thread's `children` file; then "unsupported" | tolerated; ppid fallback signals the child |
| W12 | Child already exists before `__enter__` | `ProcessTreeError("precondition")`; nothing spawned |
| W13 | prctl set/get failure injected | `ProcessTreeError("subreaper")` before spawn |
| W14 | Leader alive at timeout (spy on `killpg`) | `killpg` before reap; `TimeoutExpired.process_tree` set |
| W15 | Normal exit (spy) | `killpg` never called after reap; only direct children signalled by number |
| W16 | Detached child holds stdout | returns within the cleanup bound, not the command timeout; tail bounded |
| W17 | `KeyboardInterrupt` injected during wait | cleanup still runs; interrupt re-raised |
| W18 | Clean close; failed close | subreaper restored; left on after failure |

**Assay integration:**

| # | Setup | Expected |
|---|---|---|
| A1 | Non-liveness native lane; candidate leaves a `setsid` child | crashed with evidence; next candidate runs; R2 ERROR |
| A2 | A1 resumed; then `--rejudge-outcome crashed` | resume reuses without executing; rejudge re-executes |
| A3 | Liveness lane, same leak | crashed |
| A4 | Exit 1 with an outliving descendant | crashed, never killed |
| A5 | Timeout or hung with an orphan | budget_exceeded / hung; evidence present |
| A6 | Candidate cleanup fails | lane ERROR; no state record; no after-read; next candidate not started |
| A7 | Ordering spy | before-read < spawn < cleanup end < after-read < next before-read |
| A8 | Coverage baseline leaks | refusal; zero candidates run |
| A9 | Cold-witness r2-baseline leaks | refusal |
| A10 | Witness attempt with a descendant holding the receipt fd | cleanup runs before `finish()`; receipt is trusted or crashed, never ambiguous |
| A11 | State record killed with outliving > 0; native record missing evidence | not reused |
| A12 | `verify.py` | v16 round-trip; killed with unclean evidence refused |

**CMRU:**

| # | Setup | Expected |
|---|---|---|
| C1 | Normal-exit leak | `crashed/descendant-leak` record; campaign continues and ends `failed` |
| C2 | C1 resumed | the candidate re-executes, no abort |
| C3 | Orphan exited before the leader | crashed |
| C4 | Cleanup failure | infrastructure-error with evidence; no record |
| C5 | Baseline leak | refused before any candidate |
| C6 | Resume a killed record with outliving > 0, or with `subreaper/1` | refused |
| C7 | Timeout tests | readiness-driven, no 0.2 s or 0.5 s waits (fixes M3) |

## 8. What this still won't cover

These remain documented gaps, for future design such as CP-23:
- **Processes started through a daemon** (`docker run`, D-Bus activation, `systemd-run`) are not descendants. No subreaper or process group can see them; only per-candidate cgroups or a container per candidate could. They also don't touch the leaf counters, but they can interfere through shared ports or `/tmp`.
- **Uninterruptible sleep**: a process stuck in D state ends as a cleanup failure, which is correct and fail-closed.
- **Leftovers on the filesystem**: set `TMPDIR` per candidate.
- **Other lanes**: non-native lanes keep `default_process_runner`, including its low-risk `killpg` after reap.
