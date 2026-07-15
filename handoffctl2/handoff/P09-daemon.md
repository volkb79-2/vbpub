# P09 — handoffd: resident reconciler + HTTP/SSE surface

> Tier: sonnet (hardest package: process supervision + threaded HTTP + the
> execution map) · Depends-on: interfaces of reconcile/adapters/wrapper/
> render/notify/decisions/frontmatter/lint — ALL monkeypatched in tests as
> noted · Read first: handoff/STANDING.md, src/handoffctl/daemon.py
> (docstring = normative: run/run_pass/EXECUTION MAP/HTTP), reconcile.py
> docstring (the Action semantics you execute), docs/ARCHITECTURE.md §2 §9.

## Owned files
- `src/handoffctl/daemon.py`
- `tests/test_daemon.py`

## Design constraints (binding)
- One additional public attribute allowed and REQUIRED for tests:
  `Daemon.http_port` (the actually-bound port; support requesting port 0).
- Daemon memory (probe memo, stall two-pass cache, provider pauses,
  decision `seen` map) lives in instance dicts — documented as disposable.
- The reconcile pass NEVER runs concurrently with itself (single loop
  thread); the HTTP thread only READS disk. No other threads.
- All storage writes via append_and_apply, actor Actor(TICK,'handoffd').
- After any pass that appended >=1 event: call render.render_after_event
  and notify.notify_event per event (both monkeypatch-safe seams: call
  through the modules, not via from-imports).

## Test strategy (unit the execution map; integration is reviewer scope)
Monkeypatch `reconcile.plan_project` to return scripted actions per test;
monkeypatch `wrapper.launch_detached` → 4242, `adapters.probe` →
(True,'ok'), `render.render_after_event` → paths.www_dir(), and record
calls. Use `sample_project` + `tmp_state`. Do NOT call Daemon.run() in a
loop — call `run_pass('demo')` directly; for HTTP/SSE tests start the
server thread via a Daemon started with a scripted empty plan and stop()
in a finally.

## Oracles
1. **CreateTask/Transition**: scripted [CreateTask(fm, path),
   Transition(task, QUEUED,...)] (two passes) → statefile exists CARVED
   then QUEUED; events TASK_CREATED/TASK_TRANSITIONED present.
2. **DispatchImplementer**: QUEUED task seeded → after pass: git worktree
   `.worktrees/feat/<task>` exists on branch `feat/<task>`; events
   ATTEMPT_CREATED (route snapshot carries routes_rev 'test-rev') then
   ATTEMPT_PREFLIGHTED (pid 4242); task ACTIVE; wrapper spec.json written
   with argv from adapters.build_dispatch (monkeypatch it → known argv) and
   leases from fm.effective_mutexes. Re-running the same dispatch action
   when the branch already exists must NOT error (worktree add without -b).
3. **EmitAttemptExit healing**, one test per receipt.result:
   'done' → ATTEMPT_EXITED + task AWAITING_REVIEW; 'blocked' → task
   BLOCKED with blocker.type contract; 'limit' → task QUEUED +
   PROVIDER_STATE_CHANGED {'route_id':..., 'state':'limited'} + that
   route's provider_ok False for subsequent input-building (assert the
   next built ReconcileInput, via a captured plan_project call, has it
   False) + a NEEDS_OPERATOR notification event; 'error' with attempts
   left → QUEUED; without → BLOCKED (environment).
4. **MarkInterrupted/ResumeAttempt/InterruptAttempt**: scripted actions →
   correct events; InterruptAttempt sends SIGTERM to the pgid from
   child.pid (spawn a real `sleep 5` child in the test to receive it,
   assert it dies; ESRCH path: stale child.pid → no exception).
5. **OpenWave/LaunchReview**: seed a real branch with one commit in the
   sample repo; scripted OpenWave([t1,t2]) → WAVE_OPENED, statefiles carry
   wave_id; LaunchReview → packet dir contains <t>.diff (non-empty for the
   real branch), packet.md listing handoff paths; a FRONTIER_REVIEW
   attempt CREATED with route from tier 'frontier-review' — add that tier
   to the routes file in-test by rewriting paths.routes_path() (fake cli).
6. **SpecAttention** → SPEC_ATTENTION event with payload.reason.
7. **TICK_ERROR**: plan_project raising RuntimeError → run_pass returns 0,
   TICK_ERROR event appended (bounded message), loop-callable again
   (call run_pass twice).
8. **Input building** (the one non-monkeypatched plan test): capture the
   ReconcileInput passed to a recording plan_project for a project with:
   one handoff file (frontmatters + lint_clean present — monkeypatch
   lint.lint_project → {}), a pause flag (project_paused True), a receipt
   on disk for a RUNNING attempt (receipts[att] is a dict), a dead-pid
   RUNNING attempt (pid_alive False), decisions inbox with an OPEN entry
   (monkeypatch decisions.open_ids → {'D-002'}).
9. **pidfile**: write pidfile with os.getpid() → Daemon(...).run() raises
   RuntimeError mentioning the pid; with pid 999999 (dead) → allowed
   (test via a Daemon whose loop flag is pre-set to stop immediately).
10. **HTTP**: /api/projects lists demo; /api/tasks?project=demo returns
    the seeded statefile; /api/task/demo/<id> 200 and unknown → 404;
    /api/log/demo/<att>?tail=100 returns redacted text ('password=x' in
    log → '[REDACTED]' out, and only the LAST 100 bytes when the log is
    bigger); /www/<x> serves a file written into www_dir; traversal
    '/www/../registry.toml' → 4xx (assert NOT the file content);
    /api/events?project=demo&since=0 returns events; unknown path → 404.
11. **SSE**: open /api/stream?project=demo with a raw socket/http client
    in a thread, append one event via storage, read until a `data:` line
    arrives (timeout 10s) → its JSON has the right type; server stop()
    terminates the connection without hanging the test.
12. **run_once**: no pidfile created, no port bound (connect refused),
    returns action count from a scripted plan.

## Guidance
- HTTP: http.server.ThreadingHTTPServer + BaseHTTPRequestHandler in a
  daemon thread; SSE handler loops with 0.5s poll on _last_sequence and
  writes heartbeat comments (`: hb\n\n`) every 15s (make the intervals
  module constants; tests shrink them).
- Path safety: resolve() the joined www path and require
  `.is_relative_to(www_dir)`.
- git worktree/branch: ['git','-C',root,'worktree','add',...]; detect
  existing branch via `git rev-parse --verify feat/<t>` exit code.
- Attempt dirs: paths.attempt_dir(project, attempt_id); WrapperSpec fields
  per wrapper.py docstring; term_grace default.
- Keep run() trivial: while flag → for project run_pass → sleep interval
  (interval from min policy; a threading.Event.wait so stop() is instant).
