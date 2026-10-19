# CGProfile sampler-stop handoff — Opus adversarial review

- Reviewer: Claude Opus 5.5, high effort
- Persistent session: `810a8086-bbea-40e9-936f-9450666b214b`
- Review run id: `e1121dc7-5f0d-4b7e-bbd8-adb888824a8c`
- Review target source: `8f5e5eae24c47f4eb58aa39447726b8b2d67760b` plus review-preservation commits
- Handoff: `/tmp/vbpub-cgprofile-sampler-stop-handoff-20261015.md`
- Verdict: **NOT READY**
- Full machine result: `/tmp/vbpub-cgprofile-sampler-stop-handoff-review-20261015.json`

**Verdict: NOT READY.** The packet tells the implementer to block the daemon's single-threaded control loop for 20 seconds, and nothing in it handles a client that times out during that wait. Its claim that Run-Gate stays verdict-neutral is false in the kill paths. Six externally visible behaviours are left for the implementer to invent.

## (1) Blocking ambiguities

1. **Who finalizes after a `stop-pending`, and when, is undefined.**
   - Step 4 says the stop handler finalizes. The pending path says "when the sampler becomes quiescent, finalize once" but names no owner, and it forbids new per-request threads.
   - Run-Gate calls `stop` exactly once and never retries (`run_gate.py:3552-3556`). If finalization depends on a retry, every pending session stays live forever: it keeps its slot, its placement leaf and its DAMON slot.
   - The packet must fix three things:
     - the sampler thread finalizes itself at loop exit;
     - one compare-and-set claim, taken under the session lock, decides between that and a waiting stop handler;
     - retention runs on that path.
2. **"Quiescent" is not defined.**
   - It could mean the thread has exited, or that the loop is between ticks with stop already observed.
   - The real sleeps are not interruptible (`serve.py:1437`, `sampler.py:327`, intervals up to 30 s). Under "thread exited", every stop on a session with an interval over about 20 s returns pending.
   - Tests inject an instant `sampler_sleep`, which hides this (a masked default).
   - The contract must require stop to interrupt sampler sleeps, and must say exactly which state counts as quiescent.
3. **The 20 s wait conflicts with the serial accept loop.**
   - `_handle_connection` runs inline in `_accept_loop` (`serve.py:2967-2975`). A 20 s wait therefore blocks every verb for every lane.
   - Run-Gate's timeouts are `version` 5 s, `status` 5 s and `start` 10 s (`run_gate.py:669-670`), so other lanes lose profiling.
   - Worse, a `start` that is already queued but whose client has given up still gets dispatched later. That creates an unattended session that may apply placement and on-stall kill.
   - "Do not hold the registry lock" does not prevent this, and "no new thread per request" rules out the obvious fix. A decision is needed: either a single persistent stop-completion worker, or a wait that does not occupy the accept loop.
4. **What happens when the client times out is undefined, and today it crashes the daemon.**
   - `conn.sendall` at `serve.py:2828` is not guarded, and `_accept_loop` has no `except` around `_handle_connection`.
   - So a client that disconnects before the response turns into `BrokenPipeError`. That exits the serve loop, runs `_stop_all_sessions(daemon-stopped)`, and aborts every live session.
   - The handoff makes this likely:
     - The server needs 20 s of wait plus finalization, and finalization has no bound. Placement restore can take up to 20 s per PID: three systemd calls plus one attach, each with a 5 s timeout (`placement.py:128,145,208`).
     - The server's request-read deadline is 25 s, and both clients measure their 25 s and 30 s per call (`cgprofile.py:1153`, `run_gate.py:2542`).
     - The `docker exec` startup also counts against Run-Gate's 30 s total.
   - This must be specified, and a send failure must have a defined durable outcome.
5. **"20 s is below 25 s and 30 s" counts only the wait.**
   - The total response time is: any queue ahead in the serial loop, plus the 20 s wait, plus finalization (placement release with 3 retries, DAMON close, writes).
   - The handoff must either bound finalization explicitly, or exclude it from the response path and say so.
   - It must also amend contract §1.5 ("MUST answer `stop` within 30 s", lines 64-68). Its 30 s is a total limit on the exec carrier and a per-call limit on the socket carrier.
6. **A kill after the stop request is allowed, and that changes Run-Gate verdicts.**
   - The decision-table row "liveness kill fires during current tick … final success summary includes kill" permits issuing `cgroup.kill` after the consumer has said the lane ended.
   - Run-Gate then turns that summary into `fail_infra STALLED` (`run_gate.py:13192-13194` → `terminal_daemon_kill`, lines 11619-11632). A lane that passed becomes STALLED.
   - The packet must state that once stop is requested, no new kill may be issued. The check and the kill write must be ordered under the session lock. Only kills issued earlier appear in the final verdict.
7. **"Pending never changes the verdict" is false when a kill has already happened.**
   - Scenario: an enforced kill happens, the watch stream misses the verdict, finalization is still running, and the client's stop gets `stop-pending`.
   - Run-Gate receives `resources=None`, so `terminal_daemon_kill` returns `None`. The kill is reclassified as an ordinary lane failure (137), not infra STALLED.
   - Either a session with a recorded kill must never return pending, or Run-Gate needs a retry or reconcile change. That second option is a Run-Gate dependency this handoff forbids, so it needs its own D-decision.
8. **The response when a stop races a sampler failure or a kill-finalization is undefined.**
   - It could be `ok:true` with an aborted summary, or `ok:false`.
   - The semantics of `already_stopped` are also undefined. Oracle 5 says "no false `already_stopped`" but never defines the correct value.
   - Proposed rule: `already_stopped` is true if and only if `finished` was already set before this request observed or set the stop request.
9. **The "missing/malformed worker state" error code is invented by the implementer.** Neither a code nor a shape is given.
10. **The watch terminal state is not specified.**
    - `_stream_watch` ends with `"stopped"` as soon as `stop_event` is set (`serve.py:2903-2909`), before finalization.
    - Under pending, the final verdict can differ from that `end`, which breaks contract §8.2's "exactly one end".
11. **`start` with the same token as a stopping session is not specified.** It currently returns `reused:true` for a session that is no longer sampling (`serve.py:872-891`).
12. **The shutdown wait has no bound.** The fallback state is also missing: leave the manifest `live` for orphan recovery. And `damon_pool.close()` (`serve.py:2991`) still runs while an unfinalized session may be mid-`collect`.

## (2) False-PASS attacks (one wrong implementation per oracle)

1. **Oracle 1:** a test seam such as `if self._force_pending: return pending` returns the pending response. Meanwhile the real wait waits on `stop_event` (which the handler set itself) and always reports quiescent. Oracle 1 passes; production never returns pending and finalizes mid-tick.
2. **Oracle 2:** the second `stop` call finalizes. With no retry, the session stays live forever. The oracle always retries, so it never sees this.
3. **Oracle 3:**
   - The event log records only the hooks the implementer wired in. A direct `rundir.append("events"/"host")` call, or a `_persist` journal write from `migrate`, still runs after finalization and is never logged.
   - Also, the oracle checks for side effects only after the response. Under self-finalization (no response at all), the relevant boundary is the `summary.json` write.
4. **Oracle 4:** the kill path keeps finalizing inside the callback (today's behaviour), and the test triggers the kill at the end of the tick. That passes. A kill triggered mid-tick, followed by sample/event appends, would expose it.
5. **Oracle 5:**
   - The handler holds `sess.lock`, or the registry lock, while waiting. That can't deadlock in a test with only one caller.
   - Also, concurrent socket `stop`s cannot happen in production: the accept loop is serial. So the oracle exercises an impossible topology while the real one (blocked accept loop) is never tested.
6. **The 20 s bound:** no oracle checks it, so a 2 s or 200 s bound passes. "Policy limit, not a test oracle" leaves the fixed bound unproven.
7. **Oracle 6:** both contract copies get the same wrong text. Byte parity plus "is documented" passes without §1.3's exit-code meaning or §1.5's latency clause being updated.

## (3) Missing implementation-packet content

The handoff does not meet AUTHORING §2b/§3b:

- **Two or more invalid examples for the new shape**, for instance:
  - `terminal:false` together with `ok:true`;
  - `stop-pending` without `session`.
- **The meaning of `terminal`:** state whether it appears only on this response.
- **A session-state diagram** covering `live → stopping → finalizing → finished/aborted`. It needs:
  - who may move between states, and what triggers each move;
  - which side effects are legal in each state: kill, migrate attach, DAMON commit/collect, journal write, series append;
  - lock order between the registry lock, `sess.lock` and placement, plus a rule that nothing is held while waiting.
- **Concrete fake topology:** placement on a fake systemd (gated attach), DAMON pool (gated collect), killer, RunDir.
- **Carver-authored material:**
  - a golden `stop-pending` response;
  - a compiling skeleton;
  - tests the carver ran and saw fail first, as AUTHORING's 2b packet requires (a probed construction path and a hostile case);
  - a tracer-bullet record.
- **§3b pasted in full.** It is currently summarized, and the following are missing:
  - patching `__getattr__`-backed objects;
  - teardown must restore state, not destroy it;
  - the excluded-except-body off-by-one;
  - "no predicted measurements".
- **Exact gate commands:** the working directory and the Run-Gate binary path.
- **D-numbers** for each decision in (1).
- **nyxloom frontmatter.**
- **Run-Gate impact table** per outcome: `ok`, pending, timeout, disconnect, and kill-recorded.

## (4) Scope and dependency defects

- **Out-of-scope files the work needs:**
  - `tests/test_docs.py`, to add `stop-pending` to the closed-vocabulary oracle (an AGENTS requirement);
  - `tests/test_serve_watch.py`, for the watch end semantics;
  - `tests/fixtures/rg55/socket/` stop-pending goldens (PROTOCOL §7);
  - `CHANGES.md` `[Unreleased]`, for the protocol change.
- **Placement work is out of scope:** stopping promptly between per-PID systemd calls in `migrate` requires `lib/placement.py`. Without it, slow systemd produces pending far more often.
- **Contract sections the work must amend but the packet doesn't list:** §1.3 (exit 2 currently means "bad request"), §1.5 (the stop latency limit), §2.5 (idempotence and retention), §8.2 (watch `end`), §8.8 (error vocabulary).
- **Run-Gate dependency:**
  - The handoff asserts Run-Gate safety while forbidding Run-Gate edits.
  - Items (1)6 and (1)7 show the claim fails. They should trigger the BLOCKED clause, or a decision that removes those states.
  - Run-Gate also discards the session reference on pending, so the eventual summary is orphaned. That's acceptable only if it's disclosed.
- **Daemon-crash fix not in scope:** the `sendall`/`_accept_loop` crash is in `serve.py`, which is in scope, but the work items never mention it. It is a precondition for safety under this handoff.

## (5) Corrected oracle and fixture matrix

Pairwise axes:
- **Stop source:** client stop, retry, sampler exception, enforced kill, shutdown.
- **Sampler phase:** sleeping (interval ≤20 s or >20 s), in `migrate` (gated systemd), in DAMON collect, between kill decision and kill write, exited.
- **Client:** waits, or disconnects/times out.
- **Concurrent verb:** none, `status`, `start` with the same token, `start` for another lane, `watch`.
- **Prior kill:** none, or issued but missed by the watch.

| # | Fixture (combined axes) | Required observable | Wrong implementation it kills |
|---|---|---|---|
| X1 | Placed session with `on-stall kill`; tick gated inside `migrate` attach; the tracker would request a kill later in the same tick; client stop; real wait primitive injected to report expiry | Exact pending response. After the gate opens: no `cgroup.kill` write; release happens after the last attach; journal ends `complete`; exactly one finalize, on the sampler thread, with no retry; watch `end` reason equals the summary verdict | Forced-branch seam; retry-driven finalization; post-stop kill; watch ending before finalization |
| X2 | Socket client closes during the wait, plus a queued `status` and a queued `start` from another lane | Daemon survives (`serve_forever` still running, other sessions live); `status` served; pending or final state matches the durable files | Unguarded `sendall` crashing the daemon; registry lock held while waiting |
| X3 | Enforced kill issued; watch reader missed it; finalization gated (placement release); client stop arrives | Never pending without the kill verdict, or stop waits until finalized. Run-Gate's `finish_lane_profiling`, fed the recorded responses, still yields STALLED | Pending that drops the kill reconciliation |
| X4 | Interval 30 s with production sleep wiring (event-based clock, not an instant sleep) | Stop returns final within one wait call, because the sleep is interruptible | Masked instant-sleep seam; "thread exited" quiescence with `time.sleep` |
| X5 | Shutdown with one gated (pending) session and one idle session | Idle session finalized; gated session's manifest still `live` after a bounded shutdown wait; restart runs orphan recovery once; DAMON pool close does not race the in-flight collect (slot quarantined or closed once) | Unbounded shutdown; a finished summary written while the worker is still active |
| X6 | Stop races a sampler exception in the same tick | One aborted record; the stop response follows the decided shape and `already_stopped` rule | Double finalize; a false `already_stopped:true` |
| X7 | `start` with the same `(container, token)` while stopping | The decided refusal, not `reused:true` | Silent reuse |
| X8 | Wait primitive recorder | Called with the real quiescence event and a timeout of exactly 20.0; registry lock and `sess.lock` both free during the call | Wrong bound; waiting while holding a lock |
| X9 | Series bytes and mtimes snapshot taken when `summary.json` is written | No appends to series, events, journal, or the DAMON slot afterwards (checked at the RunDir, placement-fake, DAMON-pool and killer boundaries, not only through implementer-chosen hooks) | Narrow event log |
| X10 | Run-Gate parse of the golden pending response, over both exec (exit 2) and socket carriers | `profile_error` set; `resources` and `profile_ref` `None`; lane verdict depends only on the exit code (no kill was issued) | Run-Gate mis-parsing the response or treating it as a fault |

## (6) Readiness

**NOT READY.**

- Items (1)1, (1)2, (1)8, (1)9 and (1)11 leave externally visible behaviour to the implementer.
- Items (1)3–(1)7 contradict the stated safety goal:
  - the serial-loop block;
  - the daemon crash on client disconnect;
  - a time budget that ignores finalization and per-call timeouts;
  - a kill after stop that turns a passing lane into STALLED;
  - lost kill reconciliation on pending.
- Required test, golden, changelog and contract surfaces are outside the touch list.

To make it dispatchable:
- Record decisions for: finalizer ownership and self-finalization; the quiescence definition plus interruptible sleep; how the stop wait coexists with the serial loop; client-disconnect handling; banning kills after stop and never returning pending once a kill is recorded (or a separately carved Run-Gate dependency); the stop-versus-failure response and `already_stopped` rule; reuse of a stopping session; watch end timing; the shutdown bound.
- Bound finalization, or remove it from the response path, and amend contract §1.3, §1.5, §2.5, §8.2 and §8.8 to match.
- Widen the scope to cover the files in (4).
- Replace oracles 1–5 with matrix fixtures X1–X10 as carver-witnessed failing tests.
