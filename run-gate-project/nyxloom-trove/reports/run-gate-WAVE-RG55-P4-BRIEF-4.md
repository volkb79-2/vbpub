# run-gate-WAVE-RG55-P4 — BRIEF-4 (return: B1–B4 done, gates blocked by a host-wide environmental issue)

Return reason: checkpoint clause exceeded (extensive gate-verification
cycle: 7 selftest attempts diagnosing a genuine, pre-existing, host-wide
`/tmp` lock-contention hazard unrelated to this package's own commits).
B1–B4 (the four blockers) are all repaired, committed, and individually
verified GREEN via targeted tests. The full-suite gates (`selftest`,
`assay-r1`) are currently RED for reasons established to be environmental,
not a regression — full evidence in the REPORT's "Session 4" section and
the LOG's own entry. Non-blocking items S1–S5 were NOT reached.

## State

Tip: **`50684f2c`** on branch `rg55-followups-run-gate`, worktree
`/workspaces/vbpub/.worktrees/rg55-followups-run-gate`. Working tree
CLEAN. Commits this session, in order: `a1cebacf` (B1), `8c5af489` (B2),
`05193f44` (B3), `9489bb6d` (B4), `50684f2c` (B1's own coverage-gap fix,
found by selftest itself).

## What the next session must do, IN ORDER

1. **Re-verify host contention has actually subsided** before touching
   `selftest`/`assay-r1` again:
   ```
   ps aux | grep -E 'run-gate\.py|pytest' | grep -v grep
   cat /proc/pressure/memory
   find /tmp -maxdepth 1 -name "run-gate-exec-*-runner.lock" -type d | wc -l
   ```
   If other `run-gate.py`/`pytest` processes are still running (check
   pid/cwd — this host runs P1/P2/P6/P7 concurrently per the controller
   log), the SAME `TestExecModeMutex` contention is likely to recur
   (`SHARED_LOCK_DIR = "/tmp"` is host-wide, not worktree-scoped — see the
   REPORT's own finding). Clean any stale `-type d` lock paths found
   (they are NEVER legitimate — the code only ever `os.open()`s a plain
   FILE there) with `rmdir`, never `rm -rf` (if `rmdir` fails, something
   is actually inside it — stop and look, do not force-remove).
2. **Re-run `selftest` (bare)** once host-quiet is confirmed. If GREEN:
   proceed to step 3. If RED again on `TestExecModeMutex` specifically
   with the SAME `IsADirectoryError` signature: this is the SAME
   pre-existing hazard, not a new regression — do not spend another 7
   attempts on it; consider filing the backlog entry named below FIRST,
   then retry once more after that, then report status honestly if still
   blocked (the same standard this session held: claim only what runs
   clean).
3. **Re-run `assay-r1` (`--base rg55-run-gate-client`)**. Same root cause
   as selftest (its baseline snapshot runs the identical `pytest tests
   -q` command) — should clear once selftest does.
4. **Regenerate `run-gate.footprint.json` + the `CONSUMERS.md` transcript**
   from the now-clean `selftest` PASS + a real `footprint --write` (the
   dispatch prompt's own explicit B1 requirement — NOT done this session
   because it requires a clean selftest first, per contract Sec 1.7's
   honesty rule: regenerating from a RED run would bake in contaminated
   numbers). The CURRENTLY-tracked manifest still reflects PRE-B1
   (buggy) numbers.
5. **The non-blocking commit (S1, S2/RG-59, S3, S4, S5)**, all reachable
   without re-running any gate first — safe to do BEFORE step 2 if host
   contention is the blocker (they are ordinary code+test edits):
   - S1: print the `[source: rusage-maxrss]` caveat on the live
     `footprint` per-run line (`print_footprint_line`) and next to any
     rusage median in `history`, matching what `_fmt_footprint_row`/
     `doctor` already do.
   - S2/RG-59: narrow `_stderr_names_daemon_not_running` to match ONLY
     docker's own exec failure (exit status 125/126/127, or a stderr
     starting `docker:`/`Error response from daemon:`), never the
     daemon's OWN crash output merely containing "is not running" in a
     traceback. Test both the true-negative (a genuinely-crashing-but-
     running daemon) and the true-positive cases.
   - S3: `test_killed_client_leaves_the_record_on_disk`
     (`TestExecLaneInflightRecord`, ~line 15222 as of this tip) is
     circular — it hand-writes a payload and reads it back without ever
     calling `run_exec_lane`, so it stays green with RG-60 fully
     reverted. Mirror the container lane's own
     `TestReattachAcrossADeadClient::test_a_killed_client_leaves_a_
     container_the_next_run_re_attaches_to` precedent: spawn a REAL
     client subprocess, `client.kill()` it, assert the record survives on
     disk.
   - S4: the stale comment near `run-gate.py:8136` ("even though a
     bare-host lane never uses the token: RG-57 makes bare-host
     categorically unprofiled regardless of `[profile]`") states the
     OPPOSITE of RG-57's own actual behavior (bare-host lanes ARE
     profiled since RG-57). Fix the comment; verify no test pins the
     stale wording.
   - S5: recount `TestBareHostStallTimeoutWarning`,
     `TestBareHostProfilingWiring`, `TestExecLaneInflightRecord`,
     `TestResolveSelfContainerIdDirectBranches` at the CURRENT tip
     (counts have shifted further since B2/B3 added tests to two of
     these classes) and correct every stale count in
     `KNOWN_ISSUES_TODO_BACKLOG.md`'s RG-57/RG-58 entries and the LOG.
6. **File a backlog entry** (this package's own
   `KNOWN_ISSUES_TODO_BACKLOG.md`, next `RG-NN`) for the `/tmp`
   lock-contention finding: `SHARED_LOCK_DIR = "/tmp"` (`run-gate.py:168`)
   is host-wide, not scoped per worktree/project, and several tests use
   FIXED, non-pid-qualified container-name fixtures
   (`"myproj-dev1-runner"` and similar) — when two copies of this same
   test suite run concurrently on one shared host (which this estate now
   does routinely per RW-39/RW-42), they can collide on the SAME lock
   path. 493 stale lock-path DIRECTORIES (never legitimate — always a
   corruption signature) were found dated back to Sep 3 and cleaned this
   session; the underlying hazard that PRODUCES them was not fixed (out
   of P4's B1–B4 scope) — only documented. This is a real, reproducible,
   estate-wide test-isolation gap, not a one-off.
7. Once selftest/assay-r1 are GREEN and the non-blocking commit + footprint
   regeneration + backlog filing are done: this package is DONE (modulo
   `assay-r2`, which the controller schedules separately per RW-42/RW-43
   — do not attempt it without confirming the mutation lane is free,
   same rule BRIEF-3 established).

## Self-authored retention prompt (paste into the successor's context)

> KEEP: this BRIEF-4 in full; tip hash `50684f2c`; B1–B4 are DONE and
> VERIFIED — do not re-litigate them, only re-verify the WHOLE-SUITE
> gates and do the non-blocking commit; the `/tmp` host-wide lock
> contention finding (`SHARED_LOCK_DIR="/tmp"`, `TestExecModeMutex`,
> the 493-stale-directories evidence) — check `ps aux` for concurrent
> sibling packages BEFORE assuming a `TestExecModeMutex` failure is a new
> regression; the residual rusage-floor finding (a short-lived bare-host
> lane's reported peak is bounded below by run-gate's OWN resident memory
> at fork time — proven via `Popen`/`posix_spawn`/raw `fork+exec`, all
> three identical) is flagged for the controller/round-2 reviewer, NOT
> something this session fixed further — do not unilaterally redesign
> past RW-43's explicit `os.wait4()` mechanism without a new ruling.
> DROP: the tool-by-tool trace of the B1–B4 implementation (in the
> commits/LOG already); the moment-by-moment narrative of the 7 selftest
> attempts (the REPORT's own condensed account is sufficient — the
> ACTIONABLE takeaway is "check for contention before assuming
> regression," not the blow-by-blow).

## Host load at cut time

`ps aux` showed multiple concurrent `run-gate.py`/`pytest` processes from
OTHER RG-55 packages (P1's `r2`, P2's `assay-r2` resume, at least two
independent `python3 -m pytest` processes) — re-check fresh, do not trust
this note's own snapshot. PSI `full avg10` was consistently low (< 2.5%)
throughout this session's gate attempts — the RED verdicts are a lock-path
contention artifact, NOT a memory-pressure one; do not gate re-attempts on
PSI alone.
