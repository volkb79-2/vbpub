# CGProfile stop-protocol design reconciliation — GPT-6-Sol xhigh

- Route: GPT-6-Sol, `model_reasoning_effort="xhigh"`
- Context: `CODEX_SQLITE_HOME=$HOME/.codex/sqlite-shared`, `CODEX_HOME=$HOME/.codex`
- Invocation: read-only, `--sandbox danger-full-access --ephemeral`; prompt forbade edits, commits, tests, gates, and deployment.
- Binding record: `/tmp/vbpub-cgprofile-stop-sol-bind-20261015.txt`
- Output preserved verbatim below.

## Source basis

**Source-proven at HEAD `b4ef3b2d646afc8f284f8ba4424c12e3e917d5f5`:** the worktree is clean, and the change since the Opus v2 review’s `8dca2089` revision only records that review. Its code findings therefore still apply. [CGProfile `serve.py`](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/serve.py:2062) finalizes inside `stop`; [Run-Gate `finish_lane_profiling`](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/run-gate-project/run_gate.py:3545) calls stop once and requires a completed Summary. The two actual RG-55 copies are byte-identical: [CGProfile copy](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md) and [Run-Gate trove copy](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md). There is no `run-gate-project/docs/` copy.

**Source-proven deployment constraint:** [CGProfile’s CIU defaults](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/ciu.global.defaults.toml.j2:8) fix `environment_tag = "host"`; its [Compose template](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/ciu.compose.yml.j2:18) fixes the container and socket bind and declares no `stop_grace_period`. The control [bind path](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/serve.py:2405) unlinks an existing socket. A second candidate instance is therefore unsupported. The [declared release order](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/cmru.orchestration.toml:47) places CGProfile before Run-Gate. These are facts; everything below is a **proposed decision**, not current behavior.

## Proposed decision record

Record these as `D-<NNN>` product decisions before carving; CGProfile has no decisions ledger at this HEAD, so the numeric IDs must be allocated when that ledger is created.

| Decision | Normative choice and reason |
|---|---|
| Release and install | **Release the CGProfile artifact first, then release backward-compatible Run-Gate, then install the new CGProfile daemon.** Release is not deployment. During CGProfile’s required live acceptance, replace the singleton only in a supervised, lane-quiesced window and restore the prior daemon afterward. This preserves the fixed CGProfile → Run-Gate → CMRU → CIU/Assay order without exposing old clients to the new pending response. |
| Stop reply | A first stop requests termination, then waits **up to 20 seconds** for the sampler-owned finalizer. Ordinary completed cleanup returns the existing successful Summary shape with `already_stopped:false`. At 20 seconds, return a truthful pending response. The 20-second server budget leaves room inside the existing in-image 25-second socket timeout and Run-Gate’s 30-second outer timeout; neither limit promises completion of blocked host I/O. |
| Kill evidence | Write a durable intent **before** `cgroup.kill`; write an immutable durable `applied` result **after** a successful write and **before** publishing `killed` to any client. A crash between kernel write and result commit is **unknown**, never inferred as killed or refused. |
| Ownership | The sampler thread alone finishes a published session after its callback unwinds. Stop and shutdown request stopping; neither releases placement, closes DAMON, writes Summary, or joins a worker in the request path. |
| Admission | Use bounded control-request workers with **no application queue**; the accept thread does admission only. A saturated request is refused before dispatch. A start that exceeds its client-supplied deadline before publication, or whose reply fails, rolls back and requests cleanup; it cannot leave an unattended kill-enabled session. The later Run-Gate client must supply that start deadline. Older clients lack that guarantee and must not use the candidate daemon during the release gap. |
| Shutdown | Add an explicit **60-second Compose stop grace** in CGProfile’s own template. Give daemon shutdown one **45-second overall** budget, including worker quiescence, observer exit, and bounded pool-close attempt. Incomplete ownership stays journaled for recovery. |

The rejected [prior plan](/tmp/vbpub-cgprofile-stop-design-plan-20261015.md) had the right sampler ownership and pending concept, but “immediate pending” would regress ordinary old-client lanes. The [Opus v2 review](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-sampler-stop-handoff-opus-review-v2-20261015.md) correctly found that the proposed receipt, tick, recovery, lock, and singleton rules were incomplete.

### State and response contract

A termination trigger and a kill verdict are **separate facts**. The first accepted stop cause is retained; a later sampler exception makes the terminal session abnormal but cannot erase an applied kill. A kill attempted after stopping begins is refused before its write. A failed kill writes a `reported` verdict and leaves the session live unless another stop trigger has won.

| State | Owner and permitted effects | `stop` observed in this state |
|---|---|---|
| `preparing` | Start worker holds a capacity/key reservation; placement and sample zero may be prepared, but no published sampler or kill policy is active. | No session ID is public. Failed or expired start rolls back; its journals remain if rollback is incomplete. |
| `live` | Sampler may enter a tick, migrate, use DAMON, and attempt a policy kill. | Persist first stop intent, switch to `stopping`, wake sampler; wait up to 20 seconds. Final response if committed, otherwise `stop-pending`. |
| `stopping` | An operation already entered may finish and journal its outcome. No new tick, migration, liveness judgment, or kill begins. | Same wait and same pending evidence; no second finalizer or new session deadline. |
| `finalizing` | Sampler has unwound; it alone releases placement/DAMON and commits terminal artifacts. | Final response if committed within this request’s wait, otherwise pending. |
| `recovery-required` | No sampling or kill. A background recovery owner retries after the sampler has exited; operator repair/restart may be needed. Capacity and identity remain reserved. | `finalization-failed`, never a fabricated Summary. |
| `finished` / `aborted` | Cleanup and terminal manifest are durable; retention may act. | Stored Summary, `already_stopped:true`. |

`already_stopped` describes the **request’s observation**: it is `true` only if a terminal record existed when that stop inspected the session. A request that observes `live` or `stopping` and receives a final Summary during its wait returns `false`.

| Other operation while stopping/finalizing/recovery is pending | Required result |
|---|---|
| Same `(container_id, token)` `start` | Exit 2 `session-stopping`; no reuse, leaf, or DAMON slot. Another token uses normal admission, counting all reserved capacity. |
| `status` | Include the nonterminal state, committed sample count, and durable kill outcome; never advertise a terminal Summary. |
| New or existing `watch` | Attach/continue; send current reading and any already committed kill verdict, then normal cadence. A terminal `end` waits for the manifest commit. |
| `gc` | Skip these sessions and every incomplete placement/DAMON journal. Report only genuinely removed clean terminal sessions. |
| `version.sessions_live` and `max_sessions` | Count `live`, `stopping`, `finalizing`, and `recovery-required`. Admission additionally reserves capacity for unpublished starts. Never free a slot because a request timed out. |

Use the prior plan’s exact `stop-pending` envelope: `ok:false`, `contract:1`, `session`, `terminal:false`, `session_dir`, `kill:null` or `{"verdict":"killed","reason":"…"}`, and `error.code:"stop-pending"`. `finalization-failed` has the same fields and `terminal:false`. Both carriers carry the same object; the in-image CLI prints it and exits 2. Validate the full verb-specific shape in [cgprofile.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/cgprofile.py:1220), including rejecting `terminal` on other verbs, a missing `kill` key, `kill.verdict:"reported"`, and `ok:false,terminal:true`.

**Old-client outcome, explicitly:** [Run-Gate’s current parser](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/run-gate-project/run_gate.py:2433) treats `ok:false` as profiling unavailable. If cleanup exceeds 20 seconds, it loses `resources` and `profile_ref`; an applied kill may fail to become `STALLED`. If the request itself lasts beyond the in-image 25 seconds or outer 30 seconds, it sees a timeout with the same uncertainty. This is why even non-kill lanes must be quiesced during candidate replacement, and the new daemon must **not remain installed** before Run-Gate is updated.

### Persistence and crash recovery

The proposed authoritative data under each session directory is:

| File | Required durable content |
|---|---|
| `stop-intent.json` | Session ID, first trigger (`client`, `kill`, `shutdown`, or `sampler-error`), accepted timestamp, and generation. Commit before acknowledging pending. |
| `kill.json` | Attempt ID; frozen, nonempty tracker reason plus exact `via` cgroup; scope, target path and identity, decision timestamp; state `intent` or immutable `applied`, or a confirmed `refused` reason. The successful result carries the same reason later used in `summary.watch.reason`. |
| `tick-commits.jsonl` | Monotone sequence and the end offsets of that tick’s `samples`, `host`, and optional `damon` members. This is the authority for replay, not whatever trailing bytes happen to parse. |
| Existing `placement-state.json` | Continue identity-checked restore; incomplete is never equivalent to complete. |
| `summary.json` | Schema 1 Summary from committed ticks, exact applied kill receipt if present, and explicit `kill_outcome:"unknown"` for an intent-only crash. |
| `manifest.json` | `live` until cleanup, series sync, and Summary commit have succeeded; terminal status, cause, committed tick sequence, and Summary digest afterward. |

A tick has one commit boundary. Finish entered discovery/migration and DAMON operations, checking for stop **between** phases and between PIDs. Before entering the persistence phase, check stop again. Once entered, append the tick’s series rows, fsync their files, append/fsync its commit record, **then** update the accumulator and published live snapshot. A stop arriving during that phase lets that one tick finish; no next tick starts. A crash before its commit record leaves a suffix that replay truncates to the last committed offsets. A crash after the record but before the accumulator update replays the tick once. Sample zero follows this rule before the live manifest is published. Placement audit events remain independently journaled; a partial tick must not erase them. This addresses the current accumulator-before-append ordering in [serve.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/serve.py:1541) and the non-fsyncing append in [store.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/store.py:133).

Terminal ordering is: quiesce callback → verify placement/DAMON cleanup → fsync committed series → atomically write/fsync Summary **and its directory** → atomically write/fsync terminal manifest **and its directory** → publish terminal in memory → release capacity and run retention. [Current `write_json`](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/store.py:207) fsyncs its temporary file but not the directory. If a crash leaves `summary.json` beside a `live` manifest, replay validates its session, committed tick prefix, kill result, and digest, then adopts that exact Summary and completes the manifest. It **never overwrites** a valid committed Summary. A mismatch or unreadable evidence becomes `recovery-required`.

For an intent-only kill on restart, replay must not retry `cgroup.kill` or deduce success from an empty cgroup. It cleans up what it can, then records an aborted Summary with `kill_outcome:"unknown"` and no `killed` verdict; a follow-on Run-Gate reports profiling uncertainty without inventing `STALLED`. This is an unavoidable crash window because the kernel write and file commit are not one transaction. An `applied` result, once durable, must appear unchanged in pending stop replies and `summary.watch` after restart. The present [_finalize_orphan](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/serve.py:600) unconditionally rebuilds a live-manifest Summary and lacks that protection.

### Concurrency, watch, and shutdown

**Lock order:** registry lock may be held briefly to reserve/publish/lookup an ID, then released before taking `sess.lock`; never acquire registry while holding session. Prefer no nested acquisition. Start’s systemd/placement work runs outside registry lock under a counted reservation. Placement and DAMON objects have one sampler owner after publication. No lock is held over a join, busctl call, systemd attach/release, DAMON collect/close, or watch wait.

For container kill, do slow systemd slice verification and capacity checks **before** `sess.lock`. Under `sess.lock`, recheck session is `live`, the prepared target’s path/identity, guard, boundedness, and population, then perform only the final `cgroup.kill` write and durable result transition. Shared-leaf kill follows the same state check and exact leaf identity rule. If stop wins during slow preflight, no write occurs; if a successful write wins, stop sees its durable receipt. A blocked final kernel write may still make a request time out; the contract must say timeout means **unknown**, not a completed stop. [TargetContainerKill.kill](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/placement.py:827) currently mixes slow verification with the write.

Use a watch condition/generation separate from `stop_event`. Emit an initial reading, then readings at monotone `watch_interval` deadlines; verdict and terminal changes wake the stream without creating extra readings. A new watcher may attach in `stopping` or `finalizing` and sees an existing killed verdict before `end`. A committed sampler exception ends with new reason `session-error`; committed shutdown uses `daemon-shutdown`; confirmed kill uses `killed` unless an abnormal terminal cause takes precedence, while its killed verdict remains intact. `recovery-required` sends one explicit error event and closes **without** `end`; forced process death likewise produces incomplete EOF. Amend the closed §8.2 vocabulary and both contract copies. The current [watch loop](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/serve.py:2880) waits on the already-set stop event and can spin.

**DAMON hard-stop rule:** [KdamondPool](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/damon.py:375) owns a host-wide registry and its in-process pinned file descriptors die with the daemon. Before reserving slots, persist a pool-generation intent in the named volume; after reservation, persist count, indices, and observed identities. On restart, reconcile this journal **before any new acquisition**. A matching, exclusively owned pool can be stopped and shrunk through a supported CGProfile recovery verb under the shared registry lock; incomplete or changed identity disables DAMON acquisition and raises an operator-visible recovery requirement while non-DAMON profiling remains available. Never call `pool.close()` across an unquiesced collector. The gap after a registry resize but before durable identity, and interference by a privileged writer that ignores the advisory lock, cannot be proven away from sysfs alone. Recovery there requires an operator-confirmed exclusive DAMON maintenance window and an explicit CGProfile recovery action; automatic “assume slot zero is ours” is unsafe.

## Scope by release stage

| Stage | Owned changes |
|---|---|
| **CGProfile, first** | [serve.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/serve.py), [placement.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/placement.py), [store.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/store.py), [damon.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/damon.py), [sampler.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/sampler.py), [liveness.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/liveness.py), [summary.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/lib/summary.py) as needed, in-image [cgprofile.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/cgprofile.py), and CGProfile’s [Compose template](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/ciu.compose.yml.j2). Update README, design guide, consumers guide, protocol, and **both actual** RG-55 copies in this same stage. The Run-Gate trove copy needs an explicit scope exception; no Run-Gate executable changes are implied. |
| **Run-Gate, second** | [run_gate.py](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/run-gate-project/run_gate.py): parse pending on socket and exec exit 2; retain session/ref and exact applied kill receipt through all three finish paths; retry/reconcile within its existing 30-second budget; classify a validated applied kill as `STALLED` even when `resources:null`; preserve unknown as unknown. Update its tests, fixtures, and three user-facing documents. It must still accept old daemons’ ordinary final stop replies. |
| **CMRU, then CIU/Assay** | Keep the operator’s order. No new CIU candidate-instance feature is presumed, and no long R2 campaign is part of this decision. |

For CP-20, render/check the candidate configuration and bind its image revision to the candidate commit. Coordinate a window with **all daemon consumers quiesced**; use the owning checkout’s `ciu down --profile host`, candidate `ciu up --profile host`, and a bounded placed-peer observer probe. Then `ciu down --profile host` for the candidate and `ciu up --profile host` from the prior pinned deployment checkout, verifying its image revision and socket before reopening lanes. Do not use a second daemon, raw `docker run`, `ciu clean`, or a socket-sharing candidate. This is a **recommended procedure**, not evidence that it has run.

## Carver-owned acceptance material

Under [AUTHORING.md](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/nyxloom/reference/AUTHORING.md:198), the carver should supply literal expected JSON and a compiling skeleton whose behavioral tests are witnessed failing **through registered tester-unified lanes**, then freeze the handoff frontmatter in the prescribed two commits. The existing [socket fixture README](/workspaces/vbpub/.worktrees/cgprofile-release-20261015/scripts/cgroup-profiler/tests/fixtures/rg55/socket/README.md) describes producer-generated goldens; those alone are not independent expectations. Use barriers and controlled clocks, with wall-time limits only as hang failsafes.

| Fixture | Combined axes and observable that must distinguish a wrong implementation |
|---|---|
| F1 | Slow slice verifier + concurrent stop/status: both proceed without waiting for busctl; no kill write after stop wins, or every reply contains the prior durable applied receipt. |
| F2 | Applied kill + failed manifest write + restart: identical receipt/reason in pending and recovered Summary; previously committed Summary bytes remain unchanged. |
| F3 | Kill write succeeds + crash before result fsync: replay says `unknown`, never `killed`, and never retries the write. |
| F4 | Two starts, first blocked in placement, second queued past its declared deadline/disconnected: no second published or unattended kill-enabled session; failed first reply rolls back. |
| F5 | Stop between accumulator preparation and each series append, then restart: Summary sample count equals the committed sample/host rows and replay count; no partial tick is reported. |
| F6 | Attached and newly attached watch + delayed release: deadline-counted readings, one durable verdict, then one `end`; exception and `recovery-required` take their distinct end/error paths. |
| F7 | Shutdown with one blocked DAMON collect and one idle session: idle session commits; blocked owner remains journaled; no racing pool close; restart or explicit recovery frees only proved-owned slots. |
| F8 | Duplicate stop + same-token start + `gc`: one finalizer, stable `already_stopped` semantics, refused reuse, preserved capacity/journals, then retention only after clean terminal commit. |
| F9 | Literal pending-without-kill, pending-with-kill, final, failure, and malformed wire goldens for both carriers; old Run-Gate harness demonstrates its documented unavailable result, while follow-on client retains kill evidence. |
| F10 | Verb-aware documentation examples, closed vocab and anchors, both contract copies, and a controlled negative for each check; no regeneration from the implementation serializer as the sole oracle. |

The handoff must include the complete AUTHORING §3b test rules, a requirement→owner→oracle→fixture→controlled-break traceability table, the actual registered gate commands, and a mechanical BLOCKED rule for a required forbidden file. Exact-tree gates and independent reviews remain post-implementation work. **No tests, gates, deployment, edits, or commits were performed in this read-only pass.**

**Remaining human decisions:** schedule the singleton replacement window and identify who can attest exclusive DAMON ownership if the persistent journal cannot prove it. Without that exclusivity, automatic cleanup after a forced kill cannot be guaranteed; keep DAMON disabled for the candidate window or defer installation until a supported recovery path is reviewed. The fixed release order itself needs no change.