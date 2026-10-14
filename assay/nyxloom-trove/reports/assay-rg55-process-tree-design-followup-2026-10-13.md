# Native R2 process-tree design follow-up — Claude Opus 5.5/high

**Claude session:** `28227ce4-cbdc-471a-8b69-a430af63fb67` (resumable with `claude --resume 28227ce4-cbdc-471a-8b69-a430af63fb67`).
**Review date:** 2026-10-13. **Mode:** read-only; no tests, gates, or edits.

# Native R2 process-tree window: release decision for the standalone CLI

**Binding:** HEAD `336336d81b9a…`, tracked diff `9efe2f41…` and status `cfa459c6…` all match your values. The only untracked additions since pass 6 are the review reports. `assay/src/assay/process_tree.py` is still untracked, so its contents are covered only by the status listing, not the diff hash. Nothing was edited or run apart from read-only `git`/`grep`/`sed` and one stdlib source inspection.

**Decision:** ship the subreaper window as the supported design for the standalone CLI. The concurrent-embedding exclusion is already documented in `assay/README.md:542-546`, `assay/docs/CONSUMERS.md:3446-3456` and `assay/docs/DESIGN-GUIDE.md:961-978`, so it is no longer an undocumented gap. Fold in one cheap precondition fix (SIGCHLD, below) and two doc lines. A pidfd or supervisor redesign is a future issue, not a release blocker.

## 1. Is the window safe for the standalone CLI?

Yes. The window relies on three facts, and each holds for the CLI:

- **Only one window runs at a time.**
  - `_WINDOW_LOCK` serialises windows (`process_tree.py:43`, `:288`).
  - Native R2 requires `jobs=1`, enforced in `config.py:3125` and `mutation.py:3268`.
- **No other Assay thread spawns or reaps during a window.** Assay has three helper threads, and none of them starts a process:
  - the progress heartbeat only writes progress records (`runner.py:1124`);
  - the observation preflight thread only talks to the socket, even if it is still running after its join timeout (`resource_limits.py:1318`);
  - the witness drain thread only reads a file descriptor (`mutation_witness.py:256`).
- **Nothing outside a window changes it.** Git, R0/R1 and other subprocesses run serially outside windows, and the CLI's signal handler only `killpg`s registered groups (`cli.py:552-603`).

The supported gate path is `run-gate.py:11930` / `tester-unified/run:228`, which runs Assay as the CLI under `docker run --init`. Concurrent embedding is correctly excluded by documentation, because Assay cannot check it.

## 2. Preconditions Assay can actually check

| Precondition | Checkable? | Status |
|---|---|---|
| No existing children (`waitid` ECHILD) | yes | done, `:294` |
| Subreaper enabled and readable | yes | done, `:291-293` |
| Linux | yes | done, `:286` |
| **SIGCHLD not ignored** | yes: `/proc/self/status` `SigIgn` bit 16 is the kernel's own record, whereas `signal.getsignal` is Python's cache and can be stale | **missing** (grep finds only a docstring at `liveness.py:780`) |
| SA_NOCLDWAIT not set | only through ctypes `sigaction`, whose struct layout differs by architecture | not worth it. The kernel clears signal-action flags on `execve`, so the CLI cannot inherit it; only in-process native code can set it [H: from kernel `flush_signal_handlers`, not re-read]. Document for embedders. |
| Running on the main thread | yes | not needed, because the subreaper setting is process-wide. Only signal-handler installation depends on the thread, and that already happens on the main thread. |
| No other thread will spawn or reap later | **no** | documentation only (already present) |

**SIGCHLD finding: the missing check is proven; the trigger is conditional.**
- A SIGCHLD set to ignore survives `execve`, so a parent that ignores it (a shell `trap '' CHLD`, some daemons) passes that to `assay`.
- CPython then fabricates a successful exit. I confirmed this in the stdlib source: `Popen._try_wait` returns `sts = 0` on `ChildProcessError` ("SIGCLD is set to be ignored…").
- The effect reaches beyond R2: every R0/R1 test command and every git call would report exit 0. A failing test command would then certify PASS, which is a false certification.
- **Not reachable on the supported gate path:** `docker --init` and Run-Gate's Python launcher do not ignore SIGCHLD.

## 3. Is treating ECHILD as "no children" valid?

Yes, under the accepted preconditions: SIGCHLD not ignored, Assay the only waiter, subreaper on. In that state ECHILD means this process has no children at all, neither live nor zombie. `test_waitid_distinguishes_a_live_child_from_kernel_echild` covers the live-child case.

Two refinements:
- **If SIGCHLD is ignored, the emptiness answer itself stays truthful.** Auto-reaped children are simply gone, and live ones still produce a `None` result, not ECHILD [H: from `do_wait` semantics, not probed]. What breaks is elsewhere: exit statuses are fabricated, and PIDs are no longer pinned (see section 5). The precondition belongs in `__enter__` for that reason, not because of the ECHILD logic.
- **With an outside waiter (embedding), ECHILD is still true about emptiness,** but `outliving_descendants` can undercount and the outside waiter's own reaping gets confused. That case is excluded by documentation.

## 4. Leave the subreaper on after an unproven cleanup, or restore it?

Leaving it on is the safer, fail-closed choice:
- Restoring it does not disown children Assay already adopted.
- It only sends *future* orphans of the leaked tree to init, where they become invisible. A later window could then pass its `_has_any_children` check while leaked work is still running.

Leaving it on keeps them visible, so the next window refuses. For the CLI the question is moot anyway: `ProcessTreeError` is fatal to the lane, and the process exits. The container's `--init` is the backstop after that.

Two small defects (proven, both in the library-use path, harmless for the CLI):
- The next window reads the leaked "on" state as the caller's prior state (`_prior_subreaper = True`, `:291`). It will therefore never turn the subreaper off again.
- The refusal it gives is the generic "already has child processes". If an outside waiter has since reaped the leaked children, the refusal disappears altogether.

**Fix:** a module-level `_UNPROVEN_CLEANUP` flag, set in `__exit__` when cleanup failed and `_finished` is false. `__enter__` then refuses permanently with a specific message ("an earlier native R2 window could not prove cleanup; this process remains a subreaper").

## 5. PID reuse, and whether pidfd is needed now

PID reuse is **not a material risk when Assay is the only waiter:**
- Every PID that `finish()` signals is a direct child taken from procfs, either live or a zombie. Its number stays reserved until Assay itself reaps it, and Assay reaps by exact PID only after the kill (`:364-387`). `waitpid(-1)` is never used.
- `killpg` in `finish` runs only while the leader is unreaped (`:344-347`), so the leader keeps the group ID reserved.

It becomes material only when **SIGCHLD is ignored** (children are auto-reaped between the procfs snapshot and `os.kill`) or **an outside waiter reaps first**. The first is closed by the precondition in section 2; the second is excluded by documentation.

**One low-severity residual:** `unregister_live_group` runs in `finally`, after `finish()` and the output-tail reads (`process_tree.py:527-529`, `liveness.py:1757`). If a SIGTERM arrives after the leader has been reaped and every member of its group is dead, the handler signals a group ID that is no longer reserved. Hitting an unrelated process would need a PID wrap inside the gate's PID namespace within roughly 10 seconds, so the risk is negligible. Unregistering right after the leader is reaped closes it.

**pidfd is not the right fix even for the future.** Adopted descendants are found by PID through procfs, so `pidfd_open(pid)` has the same race as `kill(pid)`; pidfd would protect only the leader. An embedding-safe design needs one of:
- a per-candidate cgroup with `cgroup.kill`, which lines up with cgprofile CP-23; or
- a freshly exec'd supervisor process that is the subreaper, so Assay's own process-wide state never changes.

Either is also a prerequisite for parallel R2.

## What to fold into this wave

1. **Source:**
   - In `cli.main`, reset SIGCHLD to `SIG_DFL` before any subprocess. The CLI owns its process, and every child needs a real exit status, so this establishes a required operating condition rather than inventing a value.
   - In `ProcessTreeWindow.__enter__`, refuse when the `SigIgn` bit is set. An embedder's disposition must not be changed, and the refusal must still restore the subreaper and release the lock.
   - CMRU's `mutation_campaign.py:128-138` gets the window check automatically. Whether its own entry point should also reset SIGCHLD is CMRU's decision.
2. **Source (cheap, optional for the CLI):** the `_UNPROVEN_CLEANUP` flag and refusal from section 4, and `unregister_live_group` immediately after the leader is reaped.
3. **Tests:**
   - `SigIgn` bit set → `__enter__` raises, the subreaper is restored and the lock is released.
   - A real child Python process started with SIGCHLD ignored through `preexec_fn` enters the window and is refused.
   - A CLI entry with SIGCHLD inherited as ignored records a failing command as failed.
   - Cleanup failure → a later window refuses with the specific message, and the subreaper is not reset.
4. **Docs** (README, CONSUMERS, DESIGN-GUIDE; extend the existing process-tree boundary paragraphs, adding no new sections):
   - the CLI resets SIGCHLD;
   - embedders must not ignore SIGCHLD, set SA_NOCLDWAIT, or reap with `waitpid(-1)` during a window;
   - after an unproven cleanup the process stays a subreaper and refuses further native R2 windows.

**Future issue** (Assay backlog, next B-id): embedding-safe, parallel-capable native R2 isolation through a per-candidate cgroup with `cgroup.kill` (coordinated with CP-23) or a supervisor process, plus a formal protocol for runners passed through `process_runner`.

## Proven vs conditional

- **Proven:**
  - no SIGCHLD-disposition check anywhere;
  - CPython turns ECHILD into exit 0;
  - `_prior_subreaper` misreads the leaked "on" state;
  - the late `unregister_live_group` ordering.
- **Conditional:**
  - a parent that ignores SIGCHLD (not on the gate path);
  - embedding threads that spawn or reap during a window (documented exclusion);
  - SA_NOCLDWAIT set by a native extension;
  - group-ID reuse after the leader is reaped.

**Residual risks after the fixes above:**
- An embedder that breaks the documented exclusion.
- Assay itself being killed with SIGKILL, which hands leaked descendants to the container's init. The gate's `--pids-limit` bounds this; nothing removes it.

CLAUDE_FOLLOWUP_EXIT=0
