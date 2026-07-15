# P04 — attempt wrapper (detached supervision boundary)

> Tier: haiku · Depends-on: interfaces of adapters (P03, monkeypatch in
> tests where noted) · Read first: handoff/STANDING.md,
> src/handoffctl/wrapper.py (docstring = normative, steps 1–10),
> src/handoffctl/{storage,leases,types}.py.

## Owned files
- `src/handoffctl/wrapper.py`
- `tests/test_wrapper.py`

## Test setup snippet (use it; the wrapper requires an existing attempt)
```python
from handoffctl import storage
from handoffctl.types import (Actor, ActorKind, Attempt, AttemptState,
    EventType, Role, Route, TaskState, TaskStateFile, utc_now)

def seed(project='demo', task='demo-P01-sample', att='att-1'):
    states = {}
    tsf = TaskStateFile(schema_version=1, task_id=task, project=project,
                        state=TaskState.ACTIVE, since=utc_now())
    storage.append_and_apply(project, states,
        actor=Actor(ActorKind.TICK, 'test'), type=EventType.TASK_CREATED,
        payload={'statefile': tsf.to_dict()}, task_id=task)
    a = Attempt(attempt_id=att, role=Role.IMPLEMENTER,
                state=AttemptState.CREATED,
                route=Route(route_id='fake-cli', cli='fake', model='fake-model'),
                started=utc_now())
    storage.append_and_apply(project, states,
        actor=Actor(ActorKind.TICK, 'test'), type=EventType.ATTEMPT_CREATED,
        payload={'attempt': a.to_dict()}, task_id=task, attempt_id=att)
    return states
```
Fake CLI legs are tiny `#!/bin/sh` scripts written into tmp_path by a local
fixture (echo lines; exit codes; `sleep 30` for signal tests).

## Oracles (each a named test; use `tmp_state`)
1. WrapperSpec to_dict/from_dict round-trip.
2. **happy path (in-process)**: call `wrapper_main(spec_path)` directly
   (no fork) with argv = script printing 2 lines, exit 0; monkeypatch
   `adapters.extract_usage` → Usage(UNKNOWN) and
   `adapters.capture_session` → 'sess-42', `adapters.classify_log_tail` →
   None. Assert: receipt.json exists, result 'done', exit_code 0; log file
   contains both lines; events include ATTEMPT_STARTED (state RUNNING,
   pid set) and ATTEMPT_EXITED (state EXITED, ended set, usage present,
   session_handle 'sess-42'); statefile attempt state EXITED; return 0.
3. **blocked**: script printing 'BLOCKED: contract 2 unmeetable', exit 0,
   real classify_log_tail → receipt result 'blocked', blocked_reason
   startswith 'BLOCKED: contract 2'.
4. **limit**: script printing 'rate limit exceeded', exit 1 → result
   'limit'.
5. **error**: clean output, exit 3 → result 'error', exit_code 3, event
   ATTEMPT_EXITED (not FAILED — state carries completion, result carries
   failure; assert docstring step 9 exactly).
6. **lease race**: pre-acquire lease 'demo.stack' in the test; spec.leases
   [{'name':'demo.stack','capacity':1}] → wrapper_main returns 75, receipt
   result 'error', blocked_reason 'lease-lost-race', ATTEMPT_FAILED event,
   and the PRE-HELD lease still held by the test (release it, assert free).
7. **lease lifecycle**: unheld lease in spec → during run (script that
   writes a marker file then sleeps 1) holder_info shows held; after
   wrapper_main returns, free; LEASE_ACQUIRED and LEASE_RELEASED events
   present with payload {'lease': 'demo.stack'}.
8. **detach**: `launch_detached(spec)` with a 0.5s script → wrapper.pid
   file appears within 10s, pid alive then exits; `os.waitpid(pid, WNOHANG)`
   raises ChildProcessError (not our child — reparented); receipt appears;
   wrapper.log exists.
9. **SIGTERM**: launch_detached with `sleep 30` script; SIGTERM the wrapper
   pid; within term_grace: receipt result 'error', blocked_reason
   'interrupted', ATTEMPT_INTERRUPTED event, child pid (child.pid file)
   dead (`os.kill` → ProcessLookupError).
10. **kill -9 drill**: launch_detached (sleep 30), SIGKILL the wrapper pid;
    assert: NO receipt.json, lease in spec is FREE afterwards (kernel
    release — poll holder_info up to 3s), child.pid file exists. (Healing
    is the daemon's job — out of scope here.)

## Guidance
- In-process `wrapper_main` tests must not leak signal handlers: install/
  restore via try/finally.
- Tee: read child stdout via the log file the child writes into directly
  (pass the fd as stdout) — do NOT proxy lines through Python (simpler and
  survives wrapper death, which test 10 relies on).
- The 5s session-capture delay: make it a module constant
  SESSION_CAPTURE_DELAY, monkeypatched to 0 in tests.
- events: load statefile fresh before each append (docstring last bullet).
