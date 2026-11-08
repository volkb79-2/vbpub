# CGProfile sampler stop protocol — Opus pre-dispatch review v2

- Reviewer: Claude Opus 5.5, high effort
- Persistent session: `810a8086-bbea-40e9-936f-9450666b214b`
- Invocation used `--model opus --effort high --resume` with read-only `Read`, `Glob`, and `Grep` tools; no plan mode and no session-disabling flag.
- Reviewed handoff: `/tmp/vbpub-cgprofile-stop-handoff-20261015-v2.md`
- Target revision before and after: `8dca2089afa085d9c74270ff6ccbd3abf31a1469`; both statuses clean.
- Review output: `/tmp/vbpub-cgprofile-stop-handoff-review-20261015-v2.out`
- Verdict: **NOT READY**.

## Review findings

The target worktree was at `8dca2089afa085d9c74270ff6ccbd3abf31a1469` with a clean status, and nothing changed between my first and last check. **Verdict: NOT READY** (section 6).

## (1) Blocking ambiguities

1. **Every first `stop` now returns `stop-pending`, which breaks every lane on the installed Run-Gate, not only kill-enabled ones.**
   - The packet's table at line 49 sends a `live` session straight to `stop-pending`. The design plan's "final response if already committed" branch is gone.
   - The installed Run-Gate calls stop once (`run_gate.py:3552-3556`) and then drops the session reference. So every lane loses `resources`, the footprint line and admission history. The documented shell recipe (`CONSUMERS.md:546-558`, run under `set -euo pipefail`) aborts on the exit-2 reply.
   - The only rollout rule (line 73) covers kill-enabled lanes. That rule can't be enforced either: the daemon is a single container per host serving every lane in every repository, and any lane with `stall_timeout` is kill-enabled (`run_gate.py:3407-3408`).
   - Release order makes it worse: CMRU releases `cgroup-profiler` before `run-gate-project` (`cmru.orchestration.toml:47`).
   - **Decision needed:** ship the Run-Gate change first (it is backward compatible, because an old daemon never sends a pending reply), or add a daemon compatibility mode. Without one of these, the packet knowingly ships a regression.
2. **The kill receipt is lost on restart.**
   - The receipt lives only in memory. A `recovery-required` session needs a daemon restart. The orphan replay that runs on restart (`serve.py:600-668`) rebuilds `summary.json` without the `watch` or liveness blocks.
   - So a killed verdict disappears exactly in the failure case the receipt exists for. The follow-on Run-Gate then cannot classify the lane as STALLED.
   - **Must specify:** where the receipt is stored durably (written at kill time, before the lock is released), and that replay copies it into `summary.watch`.
3. **A committed `summary.json` gets overwritten by recovery.**
   - If the process dies between the summary commit and the terminal manifest commit (line 29), the manifest still says `live`.
   - `_finalize_orphan` then writes `summary.json` unconditionally (`serve.py:665`), replacing the truthful Summary, which carries the kill and placement fields, with a replay that lacks them.
   - The packet never says which record wins.
4. **The kill lock's extent contradicts the promise that `stop` won't wait.**
   - Line 59 says a kill and a stop are ordered by `sess.lock`. The plan says the whole `cgroup.kill` write happens under that lock.
   - But `TargetContainerKill.kill()` (`placement.py:827-848`) also runs the systemd slice check (`busctl` calls with 5-second timeouts) and directory checks.
   - `stop` and `status` take `sess.lock` from inside the single-threaded request loop. Line 67 ("stop must not wait on … I/O") can't hold if systemd I/O happens under that lock.
   - **Must specify:** which checks run before taking the lock, and that only the final populated check and the write happen under it.
5. **Lock order is still not specified.**
   - `handle_status` takes the registry lock, then `sess.lock` (`serve.py:1857-1872` → `1881`).
   - The new sampler-side finalizer publishes terminal state under `sess.lock` and then needs the registry lock for retention and the registry.
   - The packet says only "never hold the server lock while waiting". It doesn't fix the order (registry, then session, never the reverse), and it doesn't say who runs retention now. If `handle_stop` keeps running it, it never runs after finalization.
6. **The single-threaded request loop is still unsafe for `start`.**
   - The plan required that only a bounded number of requests be admitted, with no queue. The packet forbids a request thread pool and offers nothing in its place.
   - `start` still does placement through systemd while holding the registry lock (`serve.py:866-923`), and `report` can block for up to 120 s.
   - A queued `start` whose client already gave up after its 10 s timeout is still dispatched. That creates an unattended placed session with a kill policy, which nobody will ever stop.
   - **Must specify** a mitigation that fits a serial loop, for example: if the `start` reply fails to send, immediately request stop of the session just created and journal it, plus a test.
7. **The `end` reason for an aborted session is undefined.** Contract §8.2 allows `stopped|killed|daemon-shutdown`.
   - A sampler exception commits `aborted:session-error`, which has no mapping.
   - A `recovery-required` session while the daemon is still up has no stated `end` either.
   - Nor is it said whether a new `watch` may attach to a session that is already stopping.
8. **Watch timing during stopping is undefined, and a tight loop is the likely result.**
   - `_stream_watch` waits on `stop_event` (`serve.py:2910`). Once that is set the wait returns immediately, so a loop that "continues while stopping" emits `reading` lines without pause.
   - **Must specify:** readings keep `--watch-interval` timing, and a separate terminal or verdict condition is what wakes the stream.
9. **The unit of a tick is undefined.**
   - Packet line 31 lets an entered operation finish but forbids starting a new phase. It doesn't say whether one tick's accumulator update and its series appends commit together.
   - If they don't, the in-process Summary and a replay from the series disagree.
10. **"Committed" is undefined for durability.**
    - `write_json` fsyncs the file but not the directory (`store.py:207-226`). Series appends are gzip members with no fsync.
    - The plan asked for the directory sync. The packet lists `store.py` in scope but states no rule.
11. **A `recovery-required` session holds its slot forever.**
    - It keeps its slot (`_count_live` counts every unfinished session) and blocks its `(container, token)` with `session-stopping` until someone restarts the daemon.
    - Who restarts it and when is unspecified, and `too-many-sessions` eventually follows.
12. **The receipt's reason string is unpinned.**
    - `record_kill` appends `; via …` to `tracker.reason` (`liveness.py:657-660`), and the tracker can keep observing during the tick in flight.
    - **Must specify** that `receipt.reason` equals `summary.watch.reason` and that both are captured at the kill write.
13. **The successful-stop golden can no longer be produced.**
    - Line 85 says "keep the successful stop golden", whose current value is `already_stopped:false`. Under line 49 that value is unreachable.
    - The goldens are generated by a single real-daemon sequence (`fixtures/rg55/socket/README.md`), in which `report` and `gc` (`kept:2`) depend on stop having finished synchronously.
    - The new sequence and its synchronisation are unspecified.
14. **The CP-20 live-acceptance path doesn't exist** (line 73, line 109).
    - The daemon is a deliberate singleton: `environment_tag="host"` (`ciu.global.defaults.toml.j2:22`), a fixed container name, a fixed `/run/cgprofile` bind, and a fixed observer path that Run-Gate's preflight also hardcodes (`run_gate.py:501-507`, `7462`).
    - A second instance collides with the first. Worse, its `_bind` unconditionally unlinks the production `ctl.sock` (`serve.py:2409-2410`).
    - Making it possible requires the deployment config, which the packet forbids. **Decision needed:** a supported CIU candidate profile (a separate package), or a supervised replacement window for the host daemon with all lanes quiesced.

## (2) False-PASS attacks

**Combined-axis attacks:**

- **A1 — kill, then manifest-write failure, then restart.** The implementation commits the Summary, the manifest write fails (`recovery-required`), and the receipt lives only in memory. Test 7 passes because nothing in-process was falsely marked terminal. After restart, the replay overwrites `summary.json` without the kill verdict, and the next `stop` returns `already_stopped:true` with no kill.
- **A2 — stop during a kill whose slice check is slow.** The implementation checks the state under `sess.lock`, releases it, then calls `killer.kill()`. A stop arriving between those steps is followed by a kill. Test 1 passes because it blocks the attach, not the kill. Holding the lock instead makes the loop hang for about 15 s, and nothing tests that either.
- **A3 — queued `start` behind a slow `start`, client timed out.** Test 1's "another socket request succeeds" passes, while the stale `start` creates a placed, kill-enabled session that nobody owns.
- **A4 — missed kill, slow release, and a watch reader.** The watch keeps waiting on the already-set `stop_event`, so it emits many readings, then the verdict, then one `end:killed`. Test 2 passes; no reading-cadence assertion catches it.
- **A5 — stop between `add_sample` and the series appends, plus later replay.** The appends are gated after the stop, so the Summary counts N+1 samples while `samples.jsonl` holds N. Tests check only that nothing was appended after the Summary, so they pass, and the replayed Summary differs.

**One wrong implementation per stated test:**

| Test | Wrong implementation that still passes |
|---|---|
| 1 | Blocks new attaches but still allows a kill after stop |
| 2 | Writes the receipt in-memory only (see A1) |
| 3 | "Correct `already_stopped`" is undefined, so either value passes |
| 4 | A fake clock makes "returns within 10 s" vacuous. Production shutdown is 10 s grace + 6 s observer join + pool close, which is over Docker's default 10 s before SIGKILL |
| 5 | Leaves `_by_target` stale, so a later new-token start skips the slot accounting; untested |
| 6 | Polls `stop_event.wait(min(interval, 1))` and passes, yet `Sampler.run` (shared with `cmd_collect`) keeps a different sleep |
| 7 | Catches `sendall` errors only on the `stop` reply, not on error responses or `peer-refused` (`serve.py:2772`) |
| Goldens (line 85) | Regenerates them with `CGPROFILE_REGEN_SOCKET_GOLDENS=1`, which shares the implementation's serializer. No carver-authored literal exists despite line 123 |
| Docs | `test_docs` validates every `json` example as a `version` reply (`test_docs.py:38-45`). Any `ok:false` example passes, so a wrong pending shape slips through |

## (3) Missing implementation-packet content

- **Invalid wire examples (§2b).** Needed, each with its expected rejection:
  - `terminal` on any non-stop verb;
  - `stop-pending` without a `kill` key;
  - `kill:{"verdict":"reported"}`;
  - `ok:false` with `terminal:true`.
- **New public error codes:** `session-stopping`, `stop-pending`, `finalization-failed`. Each needs its §8.8 entry, its exit code (2), whether it is valid in `status` or `start`, and how it maps into Run-Gate's closed vocabulary.
- **A state diagram** with trigger, owner and the session-effects allowed in each state, including:
  - kill → `stopping`;
  - exception → `aborted`;
  - shutdown → `aborted:daemon-stopped`;
  - the precedence when several triggers coincide (kill vs. stop vs. exception).
- **Lock order and lock extents:** registry, `sess.lock` and placement; what the kill does under the lock; the rule for releasing locks while waiting.
- **Retention owner,** and whether stopping or `recovery-required` sessions appear in `status`, `version.sessions_live` and `max_sessions`.
- **Durability rules:** fsync for series, file and directory, and the precedence rule when recovery meets an existing `summary.json`.
- **Shutdown time budget against Docker's grace period,** and the operator procedure for DAMON monitors left running when the pool isn't closed. Today that leaves DAMON permanently refused, because the shared monitor count stays nonzero.
- **Carver material:** a carver-authored literal golden response for each new shape, and a compiling skeleton with failing tests the carver ran and saw fail. AUTHORING §2b/§6 requires both; none is attached.
- **§3b pasted in full** (`__getattr__` patching, restore-don't-destroy teardown, excluded-except off-by-one, no predicted measurements), plus nyxloom frontmatter.
- **The gate command, and a ban on cockpit test runs.** Line 108 ("focused foreground checks") permits cockpit pytest, which AGENTS forbids.
- **The REPORT's traceability table** with real test names, as AUTHORING requires.

## (4) Scope and dependency defects

- **The contract copy path doesn't exist.** `run-gate-project/docs/RG55-INTERFACE-CONTRACT.md` is missing. The real copy, which `test_docs` compares against, is `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`, inside the Run-Gate tree that line 105 forbids. The very first doc step therefore triggers BLOCKED or creates a stray file.
- **Files the work needs but scope omits:**
  - `lib/liveness.py`, for an immutable kill reason (the plan names it);
  - `lib/summary.py`, if the receipt or state goes into the Summary;
  - `tests/test_cgprofile.py`, if ctl tests live there (validator tests today are in `test_serve.py` and `test_docs.py`).
- **`sampler.py` is shared with the one-shot collector** (`cgprofile.py` `cmd_collect`), and the packet doesn't say what changes for it.
- **Shutdown depends on deployment config.** It needs `ciu.compose.yml.j2 stop_grace_period`, which is forbidden.
- **CP-20 acceptance depends on a CIU candidate instance** that doesn't exist (item (1)14).
- **Rollout depends on a Run-Gate change and on the declared release order** (item (1)1). Both are acknowledged but neither is gated mechanically.
- **The packet departs from the design plan** on request admission (plan: bounded and not queued; packet: serial, no pool) without a recorded decision or a replacement mitigation.

## (5) Corrected oracle/fixture matrix

**Pairwise axes:**
- **Stop source:** client stop, repeated stop, kill, sampler exception, shutdown.
- **Sampler phase:** waiting, discovery/recommit, attach blocked, DAMON collect blocked, kill write, tick commit, release blocked, Summary write, manifest write.
- **Storage:** ok, Summary fails, manifest fails, restart between the two.
- **Client:** waits, disconnects, queued past its timeout.
- **Concurrent verb:** status, watch (attached or new), start (same or other token), report, gc.
- **Consumer:** old Run-Gate, follow-on Run-Gate, the CONSUMERS shell recipe.

**Requirement-to-oracle traceability** (✗ = no discriminating oracle in the packet):

| Requirement | Packet test | Gap |
|---|---|---|
| Kill admission after stop | 1 (attach only) | ✗ kill path |
| Durable receipt | 2, 7 | ✗ restart |
| One finalizer | 1, 3, 5 | partial |
| Tick atomicity | — | ✗ |
| Interruptible wait | 6 | weak |
| Watch cadence and `end` | 2 | ✗ aborted, ✗ cadence |
| Request-loop safety | 1, 7 | ✗ queued start, ✗ kill lock |
| Shutdown | 4 | ✗ Docker grace |
| Docs and goldens | 85, `test_docs` | ✗ verb-typed validation, wrong path |
| Rollout | — | ✗ |

**Corrected fixtures:**

| # | Fixture | Required observable |
|---|---|---|
| F1 | Kill with the slice check blocked (gated fake verifier); stop arrives; then `status` | `stop` and `status` return without waiting on the slice check. Either the kill is refused because stop won, or the receipt shows in every pending reply. No write after stop. |
| F2 | Kill, then manifest write fails, then a simulated restart (new server on the same directory) | Receipt is durable on disk; recovered `summary.watch.verdict == "killed"` with the same reason; a committed `summary.json` is never overwritten by replay |
| F3 | Two queued connections: a slow `start` (gated placement) and a second `start` whose client closes before dispatch | Second session either never created, or created and immediately stopped and journaled; no unattended kill-enabled session |
| F4 | Watch attached; stop; release gated | Readings keep their interval timing (event-counted, not timed); verdict then exactly one `end`; for an aborted session, the decided reason |
| F5 | Stop placed between `add_sample` and the appends (barrier) | Summary `sample_count` equals rows in `samples.jsonl` equals replay count |
| F6 | Old-Run-Gate harness (real `ProfilerClient` from `run_gate.py`, read-only) against pending without kill, pending with kill, final | Documented outcome per carrier. This proves the stated rollout constraint instead of asserting it. |
| F7 | Shutdown with one session blocked in collect and one idle | Manifests reflect the true state; pool not closed while an owner is unquiesced; a documented recovery for stranded monitors exists and is tested |
| F8 | Golden sequence redefined (start → stop pending → finalization barrier → stop final → report → gc) | Carver-authored literals committed before implementation |
| F9 | `test_docs` validates each `json` example with the verb its section documents | A wrong pending example fails |
| F10 | Validator negatives from (3) | Each rejected, exit 3 |

## (6) READY or NOT READY

**NOT READY.** The implementer would have to invent all of the following:
- the rollout and compatibility behaviour (1);
- durable storage and the restart semantics of the kill receipt (2, 3);
- the extent of the kill lock and the overall lock order (4, 5);
- protection against stale queued `start` requests (6);
- the watch end reason and reading timing (7, 8);
- the tick commit unit and the durability rules (9, 10);
- the lifecycle of a `recovery-required` session (11);
- the reason string in the kill receipt (12);
- the golden sequence (13).

On top of that, one scoped path is wrong and sits in a forbidden tree, the CP-20 live-acceptance path is unsupported, and carver-witnessed failing tests and literal goldens are absent.

The fix is a short decision record covering (1)1, 2, 3, 4, 6, 7, 11 and 14, followed by re-carving the packet with fixtures F1–F10 as carver-authored failing tests.
