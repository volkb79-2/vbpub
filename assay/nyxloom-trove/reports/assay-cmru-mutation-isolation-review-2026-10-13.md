# Mutation isolation design review (Claude Opus 5.5, high)

Review session: `28227ce4-cbdc-471a-8b69-a430af63fb67` (persistent).
The review response was recovered from the saved Claude session transcript at
2026-10-13 04:30 UTC and preserved below for follow-up. The reviewer was
restricted to file-reading tools. It did not confirm the exact HEAD or inspect
the separate Sol report. Treat its code observations as a design review of the
then-visible source, not as final approval of the later uncommitted diff.

---

I recommend making the campaign process a child subreaper and running each mutant with file-based output, then killing and reaping every remaining descendant before the after-window read. Process groups alone are not enough, and per-candidate cgroups are not usable in this release.

I could only read files this session, so two things are unconfirmed: that HEAD is `3f3012fc`, and what the other Sol blockers say. No Sol report for the current candidate is in the checkout. The line numbers below are from the current `cmru/tools/mutation_campaign.py`.

## What the current code does wrong

`_run` is at lines 100-126.

1. **The hang Sol found.** A descendant that calls `setsid()` leaves the command's process group, so `os.killpg(process.pid, SIGKILL)` at line 117 misses it. It still holds the write end of the stdout pipe, so the `communicate()` at line 120, which has no timeout, waits forever.
2. **A leak Sol did not flag.** Nothing cleans up when the command finishes before the timeout. The `killpg` only runs in the timeout branch (lines 115-125). A background descendant that redirects or closes stdout, such as `nohup … >/dev/null &` or a double-forked daemon, keeps running after `_run` returns. That is true even if it stayed in the same process group. It then runs during the next mutant's window (counters read at lines 614 and 638).
3. **Why that matters for results.** The counters cover the whole tester container cgroup, so a leftover process is charged to the next mutant. Worse, a leftover process holding a port, file lock or CPU can make the next mutant's tests fail. That is a false **kill**, not just an inconclusive result.
4. **Error handling in the loop.** The loop at line 631 only catches `TimeoutExpired`. Any other failure from `_run` escapes without writing `infrastructure_error` to the evidence file.

## The guarantee you can actually get

> When `_run` returns, every process that descends from the mutant's command has been killed and reaped. That includes processes that called `setsid`/`setpgid`, double-forked, or started their own PID namespace. The whole output file has also been read. If cleanup cannot finish within a fixed limit, the campaign stops with an infrastructure error and records no outcome for that mutant.

This rests on one kernel rule. When a process exits, its orphaned children are re-parented to the nearest ancestor marked as a subreaper (`PR_SET_CHILD_SUBREAPER`). Calling `setsid` or double-forking cannot get around that. So while the campaign process is alive and marked as a subreaper, no mutant descendant can escape to PID 1. Once `waitpid(-1, WNOHANG)` raises `ChildProcessError`, the campaign has no descendants left, alive or zombie.

Things this does **not** cover:
- Processes started by something outside the tree on the mutant's request, such as the Docker daemon if a socket is mounted. Inside the container there is nothing else that spawns processes: the init process does not fork. Anything started outside the container is in another cgroup. It would show up as a shared-ancestor event and abort the lane, not cause a false kill.
- A process stuck in an uninterruptible kernel wait (state `D`), which ignores SIGKILL. The cleanup limit turns this into an infrastructure error.
- Leftover filesystem state outside the mutant's temporary directory, for example in `/tmp`. This is a separate isolation question. A cheap mitigation is to set `TMPDIR` to the mutant's temporary directory.

PID reuse is not a practical risk. The container has its own PID namespace containing only the init process, the campaign and the mutant's tree, so a recycled PID can only belong to our own tree. Direct children cannot be recycled until we reap them. Plain `os.kill` is safe, and pidfds are not needed.

## Options considered

| Option | Verdict |
|---|---|
| **Process groups only** (current code) | **Not enough.** `setsid`/`setpgid` escape it, it does nothing on normal exit, and it does not fix waiting on the pipe. Keep `start_new_session=True` and one `killpg` as a fast first step only. |
| **Output to a temporary file instead of a pipe** | **Required**, but only fixes the hang. Waiting on the direct child no longer depends on descendants closing the pipe. On its own it turns the hang into a silent leak. |
| **Campaign process as subreaper, plus a sweep that kills and reaps the tree** | **Recommended.** Smallest robust fix. The campaign is single-threaded. `copy_project_fixture` starts no processes (`tools/project_fixture.py` contains no subprocess calls). Only the mutant runs during a window. |
| **Separate per-mutant helper process acting as subreaper** | Works, but adds startup cost, a channel to report results back, and a gap: if the helper is killed, its descendants escape to the campaign or to the init process. Then the campaign has to be a subreaper anyway. Worth it only if the campaign ever runs mutants concurrently, which B157 rules out. |
| **A child cgroup per mutant, killed with `cgroup.kill`** | Strongest isolation, but **not usable in this release**. In an unprivileged container the cgroup filesystem is normally mounted read-only (inferred; I have not checked it in this container). It also conflicts with contract 2: the observer refuses when it uses local event counters and the candidate cgroup has a child cgroup (`scripts/cgroup-profiler/lib/resource_observer.py:456-468`, called at `:92-96`). The refusal is correct: events inside a child cgroup do not appear in the parent's local counters, so they would be missed and could produce false kills. This belongs in a future observer contract 3. |

## Recommended implementation (all in `cmru/tools/mutation_campaign.py`)

**1. At campaign start, before the baseline run (before line 509):**
- Mark the process as a subreaper with `ctypes` `prctl(36, 1, 0, 0, 0)`, and confirm it with `prctl(37, …)`.
- Fail before running anything if that does not succeed.
- Record the set of PIDs in `/proc` at that point (a process census).

**2. Replace `_run`.** Return a small result object instead of raising `TimeoutExpired`:

```python
class CandidateIsolationError(RuntimeError): ...

def _run(argv, *, cwd, timeout_seconds, output_dir, cleanup_seconds=10.0):
    _require_no_children()                  # waitpid(-1, WNOHANG) must raise ChildProcessError
    with tempfile.TemporaryFile(dir=output_dir) as out:
        proc = subprocess.Popen(list(argv), cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            returncode, timed_out = proc.wait(timeout=timeout_seconds), False
        except subprocess.TimeoutExpired:
            _killpg_quiet(proc.pid)          # fast path for the common case
            returncode, timed_out = proc.wait(), True   # reap the leader BY PID first
        killed = _sweep_descendants(deadline=time.monotonic() + cleanup_seconds)
        out.seek(max(0, os.fstat(out.fileno()).st_size - 1 << 20)); tail = out.read()
    return CandidateRun(returncode=None if timed_out else returncode, timed_out=timed_out,
                        output=tail.decode("utf-8", "replace"), descendants_killed=killed)
```

Notes on the snippet:
- The output-file seek is written loosely. `1 << 20` binds after the subtraction, so add parentheses when implementing it (keep the last 1 MiB).
- `_sweep_descendants` repeats until done: list our descendants, send SIGKILL to each, then reap with `waitpid(-1, WNOHANG)` in a loop. It stops when `waitpid` raises `ChildProcessError`. It raises `CandidateIsolationError` if the time limit passes.
  - To list descendants, reuse `_pid_children_via_task` from `assay/src/assay/liveness.py:679-715` (its fallback scans parent IDs in `/proc`).
  - It returns how many processes it killed.
- **Order matters.** Reap the main child by PID before any `waitpid(-1)`. Otherwise Python's `Popen` sees `ECHILD` and silently sets `returncode = 0`, which would turn a kill into "survived". Add a test for this.
- Optionally, after the sweep, check that `/proc` (zombies excluded) matches the census taken at start. It is cheap, and it catches implementation bugs or a missing subreaper mark.

**3. Call sites.**
- **Baseline (lines 517-529):** pass the baseline temporary directory as `output_dir`. Map `timed_out` to the existing "exceeded" error. Give `CandidateIsolationError` its own message, because today it would be reported as a counter failure. Record how many descendants the baseline left behind; this is useful diagnostically.
- **Loop (lines 627-665):** branch on `run.timed_out`.
  - Catch `CandidateIsolationError` and handle it like the counter-failure path at lines 615-626: set the infrastructure-error status, write the evidence, emit the `complete` event, then raise.
  - The sweep finishes inside `_run`, so the after-read at line 638 covers the full lifetime of the mutant including cleanup. That is the correct window. SIGKILL does not raise `pids.events` or `memory.events`.
- **Results:** add `descendants_killed` to `Result` (line 52). The classification in `_candidate_result` (lines 147-182) stays the same: a timeout remains inconclusive, and exit 1 with no resource events remains a kill. Leftover descendants killed after the main command exited do not change its exit status.

**4. Resume format.** A kill recorded before this change may have been caused by a process leaked from an earlier mutant.
- Add `"candidate_isolation": "subreaper/1"` to the evidence document.
- Make `_resume_results` (lines 336-341 and 425) refuse to reuse kills from evidence that lacks it. Bumping to `schema_version` 3 would do the same.
- Name the file to delete in the refusal message.

## Tests

Use the existing file `cmru/tools/test_mutation_campaign.py`. Each test should call `_run` inside an outer `subprocess.run(..., timeout=20)`, so a regression fails the gate instead of hanging it.

1. **`setsid` escape on timeout.** The grandchild calls `os.setsid()`, keeps the inherited output fd, writes its PID to a file, then sleeps. Expect `_run` to return promptly with `timed_out=True`, and `/proc/<grandchild>` to be **gone**. Tighten the current test at lines 132-157, which accepts a zombie reaped by the init process; with the subreaper, the campaign reaps it and it must be absent.
2. **Escape on normal exit.** The child starts a `setsid` grandchild writing to `/dev/null` and exits 1. Expect `returncode == 1`, which also covers the `ECHILD` → 0 trap, and the grandchild dead when `_run` returns.
3. **Double-forked daemon** (fork, `setsid`, fork, parent exits): it is killed.
4. **Output.** Output from both the child and the grandchild is captured. More than 1 MiB of output is cut to the tail.
5. **Fork storm** (a loop forking `setsid` sleepers): cleanup finishes within the limit.
6. **Cleanup cannot finish.** Patch the kill function to do nothing. Expect `CandidateIsolationError` within the limit, the evidence status `infrastructure-error`, and no result recorded for that mutant.
7. **Subreaper mark fails.** Patch `prctl` to fail. The campaign refuses before the baseline runs.
8. **Resume.** Evidence without the isolation marker cannot have its kills reused.

These need a real `/proc`, so run them in tester-unified, not the cockpit.

## Effect on other release work

- **Contract 2:** compatible. No child cgroups are created, so the observer's rule at `resource_observer.py:456-468` is unaffected.
- **Run-Gate `--init`:** still needed as a backstop if the campaign itself dies. Its orphans go to the init process and the container is removed. That aborts the lane rather than producing a classification.
- **No `--pids-limit` on Run-Gate container lanes** (earlier finding): a fork storm is still bounded by the ancestor `pids.max`. Hitting it registers as an ancestor event and aborts the lane, not a false result.
- **Same weakness in Assay's native R2.** `LivenessRunner` and `runner.py` already use output files and clean up the process group on every exit path, but they rely on process groups only (`assay/src/assay/liveness.py:1700-1754`, `assay/src/assay/runner.py:413-432`). A `setsid` descendant can leak into the next mutant's B145/B155 window there too. File it as an Assay backlog item that reuses the same primitive, ideally moved into Assay later. Do not widen this release's CMRU fix to cover it.
