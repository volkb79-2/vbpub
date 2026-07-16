# P14 — stall detection that owns a clock (progress monitoring by default)

> Tier: sonnet · Date: 2026-07-15 · Source: two live incident sets the same
> day (this factory: 3 hung `claude --resume` legs, 0-byte logs, 4.7h
> undetected; the manual dstdns session: DeepSeek stalls detected only
> event-driven at 107/68 min). User directive: the daemon must monitor
> running agents' progress BY DEFAULT, detect no-activity, and
> pause/interrupt-resume — "hang detection can't be an event you wait for,
> it has to be a clock someone owns." Read handoff/STANDING.md first.

## Owned files

- `src/nyxloom/adapters.py`, `src/nyxloom/reconcile.py`,
  `src/nyxloom/daemon.py`, `src/nyxloom/wrapper.py`
- matching test files (`tests/test_adapters.py`, `tests/test_reconcile.py`,
  `tests/test_daemon.py`, `tests/test_wrapper.py`) — extend, keep all
  existing tests green.

## Diagnosed failure classes (fix ALL; encode each as a regression test)

1. **Buffered-CLI blindness**: `claude -p --output-format json` writes its
   ENTIRE output at exit — log mtime is structurally dead as liveness.
   Fix in adapters: claude dispatch/resume argv use
   `--output-format stream-json --verbose` (incremental JSONL to the log =
   heartbeat for free + live dashboard tail); `extract_usage` handles the
   stream-json shape (final `result` line carries usage/total_cost_usd —
   keep the existing array-shape and object-shape fallbacks).
2. **Silent stall pipeline**: StallCheck feeds a daemon-memory cache and
   emits nothing; a confirmed stall was invisible. Fix: on tier-2
   confirmation emit ATTEMPT_STALLED (state STALLED via the normal upsert)
   BEFORE InterruptAttempt next pass; ATTEMPT_STALLED joins the default
   notify push classes (config.py NotifyConfig default list — you MAY edit
   exactly that one list entry in the frozen file).
3. **Tier-2 confirm must be real**: pid alive + log quiet >
   policy.stall_log_quiet_seconds + /proc/<pid>/stat utime+stime delta == 0
   across two consecutive passes + no CPU-active child (scan
   /proc/<pid>/task and children via /proc/<pid>/stat ppid match — a
   cheap best-effort walk) => stalled. A declared long gate (attempt
   currently in a gate run) is exempt per v2 §5.4 — approximate via a
   `gate-running` marker file the wrapper touches around gate execution
   (wrapper change) or skip the exemption with a code comment if wrapper
   gates are not yet wired (they are not — receipts show oracles []).
4. **Silent dead-end**: INTERRUPTED attempts with no resume handle or
   exhausted attempt budget left the task ACTIVE forever with zero events.
   Fix in reconcile: that condition -> Transition(task, BLOCKED) with a
   typed blocker (type environment, unblock 'operator: inspect attempts')
   — visible + notified (TASK_BLOCKED is already a push class).
5. **Resume bookkeeping drift**: resumed processes never updated
   attempt.pid/log_path, so pid_alive watched a dead pid while the live
   hung process went unwatched. Fix: daemon ResumeAttempt execution
   records the NEW wrapper pid + the resume log path on the attempt
   (ATTEMPT_RESUMED payload carries the full updated attempt — it already
   does; ensure pid/log fields are actually refreshed), and _attempt_scan
   must derive pid liveness from the newest wrapper.pid file in the
   attempt dir when attempt.pid is stale (belt and braces).
6. **No wall-clock cap**: an attempt may now run forever. Fix: per-attempt
   cap = fm.budget.max_wall_seconds if set else policy default (add
   `attempt_max_wall_seconds` to Policy, default 10800); reconcile emits
   InterruptAttempt when started+cap < now regardless of liveness; after
   interrupt the normal resume/budget path applies.

## Oracles (headline cases; you design the rest)

1. Simulated hang: dispatch a fake-cli script that writes one line then
   sleeps 600 — within (2 passes + threshold) the plan emits StallCheck,
   then stall confirmation flips the attempt to STALLED (event present),
   then InterruptAttempt; after max_attempts the TASK lands BLOCKED with
   the typed blocker. Drive via run_pass with shrunk thresholds
   (policy.stall_log_quiet_seconds=1, monkeypatched pass cadence).
2. CPU-active-but-quiet-log process (fake script burning CPU in a loop,
   no output) is NOT confirmed stalled (tier-2 negative).
3. claude route argv contains stream-json; extract_usage parses a
   stream-json fixture log (final result line) to ACTUAL usage.
4. Wall-clock cap: attempt started > cap ago -> InterruptAttempt even with
   a fresh log (planner test).
5. Full suite green.

## Rules

STANDING.md applies. types.py/storage.py/config.py remain frozen EXCEPT the
single push-classes default list entry noted in item 2. Do not commit.
REPORT to handoff/reports/P14-REPORT.md; receipt-only final message.
