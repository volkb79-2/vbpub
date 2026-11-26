<!-- status: design rev 2 (main review FIX-FIRST + operator rulings); scope: new standalone host service TG-1; operator decision 2026-11-25 -->
# Design: thrashguard (TG-1) — PSI-driven freeze/thaw of dev-tier work

Nothing of thrashguard exists: no code, no unit, no host service. `grep -rln` over `*.py *.rs *.sh *.toml
*.service *.socket` finds prose only, and every other tool already points here (fleetd `docs/SPEC.md:24`,
`:533-534`, `KNOWN_ISSUES_TODO_BACKLOG.md:20`, `:318`, `:967-974`; hostobs
`KNOWN_ISSUES_TODO_BACKLOG.md:1163-1164`; agctl `docs/design/agctl/DESIGN.md:7`). This note is normative for the
first implementation. Operator inputs are quoted with line numbers from `.controller-state/operator-decisions.md`;
later lines win. The main controller's rev-1 review is answered item by item in the run report.

## 1. Scope and boundary

**Thrashguard is the actuator for "freeze".** Operator 2026-11-25: "thrashguard (TG-1) is the important missing
piece; it does not exist yet. mdt-cockpit does not own freeze; it may only offer to deploy thrashguard as a
service" (:615); the programme still holds fleetd's freeze for this
(`.controller-state/PROGRAM-2026-11-24/PROGRAM.md:448-449`). Overcommit is intended and "only PSI is the signal to
freeze things" (:497); "stop line = memory PSI `full` avg10 >= 3 only (heavy swapping); no stricter waits"
(:533); the act is "freeze+`memory.reclaim` ... park one thing ... until something else finished or RAM
requirements changed" (:207).

It does: poll host memory PSI; decide the host is thrashing; choose one unit from a **registered** candidate set
(registrants push shed scores, :353, :355) by a configured victim rule (:365-368); freeze that unit's cgroup and
optionally `memory.reclaim` it; thaw it on pressure release, aging, expiry or shutdown, never leaving anything
frozen; and record every frozen interval, publishing it as an event through `hostobs` (§4) so consumers correct
wall time without any of them reading a host socket.

It does not:

- compute progress, RAM value or shed scores: "reading per-job progress and RAM rather belongs in `run-gate` or
  assay then, this is high level, wrong boundry for thrashguard" (:355). The registrant computes the score (:353).
- rank by progress-per-RAM; CP-64 is superseded (`.controller-state/design-review-2026-11-24/fleetd.md:101`).
- kill: OOM stays with memcg/systemd-oomd, aimed at the load tiers on purpose
  (`mdt-cockpit/host-setup/docs/DESIGN-DEDICATED-DEV-MEMORY.md:13-17`, `:29-34`).
- write CPU/IO weights or `memory.high`: that is `mdt-psi-guard`
  (`mdt-cockpit/host-setup/scripts/mdt-psi-guard.py:23-28`, `:71-72`).
- touch host services, `system.slice`, `user.slice`, the cockpit (`dev-interactive`) or `/root/tls-edge-LIVE`
  ("it shoud not be touched at this point", :496).

`mdt-psi-guard` runs *outside* `dev.slice` and "never freezes or kills anything — a wall-clock budget that expired
while frozen would change a gate's result" (`units/mdt-psi-guard.service:2-4`, `scripts/mdt-psi-guard.py:30-31`);
its test forbids the very syscalls thrashguard needs (`tests/test_psi_guard.py:214`), and run-gate reads its events
as contamination (`run_gate.py:6065`, `:6107`, `:6157`). The jobs stay separate: psi-guard protects the host from
the dev tier; thrashguard parks dev-tier work.

| Tool | Relation |
|---|---|
| `mdt-psi-guard` | independent: it never freezes, thrashguard never re-weights; no shared state (§2). |
| `fleetd` | the cross-host **placer and registrant** (B3): starts a lane via run-gate, learns its container id, registers it; reads freeze state through hostobs (§6a). FD-012 is "job registration on the thrashguard socket, owner TG-1" (`KNOWN_ISSUES_TODO_BACKLOG.md:318`); FD-049 reports `guard_present` + owner state (`:967-974`). Never validates thrashguard's config. |
| `run-gate` | **registrant on the host** until the shared host runner exists (P-fd-05, §4e); its reader query is an optional extra; it works unchanged when thrashguard is absent. |
| `assay` | **reader only**: cgprofile profiling plus freeze events from hostobs, effective CPU time (§4b). Never speaks thrashguard's socket; no verdict depends on the events. |
| `hostobs` | the host's telemetry and its **event intake** (§6c): thrashguard emits there; fleetd/assay/agctl read there. Not a registrant. |
| `agctl` | reads ONE estate snapshot through fleetd (§6b); never a host socket, never ssh. |

## 2. Control loop

One tick thread (default 5 s) plus a socket loop.

**Trigger.** Host memory PSI `full` avg10 >= `trigger_avg10` (default **3**, :533) held `hold_s` (default 10, the
shipped guard's number, `scripts/mdt-psi-guard.py:55`). Only the host-wide `full` row — time lost to reclaim/swap,
i.e. "heavy swapping". CPU and IO pressure never trigger; per-cgroup `memory.pressure` is reported, never acted on.
Thaw at `full avg10 <= trigger_avg10 / 2` held `release_s` (default 60, `:56`): the factor-of-two hysteresis is the
shipped guard's shape and stops flapping. **Unreadable PSI is not zero pressure**: missing, unparseable or reset
counters set `unknown` — freeze nothing, thaw what this guard froze, report `unknown`, never a synthetic 0.

**Ordering against `mdt-psi-guard` (L1).** Different counters, no fight over one:

| Level | psi-guard (`enforce`) | thrashguard (`enforce`) |
|---|---|---|
| tier memory `some` >= **10%** of wall | stage 1: `cpu.weight`/`io.weight` of the three dev tiers to 1 (`SIGNALS` `:66-70`, `WEIGHT_TIERS` `:71`, `_sources` `:203-208`) | nothing (host-wide `full` only) |
| host memory `full` >= **5%** of wall | stage 1, then 30 s later stage 2: `memory.high` (`ESCALATE_SECONDS` `:58`, `_escalate` `:291-299`) | nothing yet |
| host memory `full` avg10 >= **3** held `hold_s` | — | freeze one unit |
| release: below half its own threshold | reverts weights/lows (`RELEASE_FACTOR` `:57`, `:274-282`) | thaw queue (M1) |

Re-weighting is the first, reversible act; the freeze comes later. Thrashguard's host-full threshold (3) is
deliberately lower than psi-guard's (5): a freeze is what "heavy swapping" justifies (:533). On
`HOST_ROLE=dedicated-dev` psi-guard runs stage 1 only — "no `MemoryHigh` anywhere in that role's dev tree"
(`DESIGN-DEDICATED-DEV-MEMORY.md:24-25`) — so there thrashguard is the only actuator. Neither waits for the other:
if re-weighting relieves the stall, `full` falls below thrashguard's release point and nothing is frozen.

**Candidates.** Only units registered over the registrant socket, plus an optional static `/etc` list (§3). No
default scan of `dev.slice`: an unregistered cgroup cannot be ranked, and a scan would reach the cockpit.

**Freeze target (D1).** The cgroup holding *all* processes of one unit — the docker scope the container started
in, e.g. `/dev.slice/dev-gates.slice/docker-<id>.scope`, which run-gate's `--cgroup-parent` produced
(`run_gate.py:529` `CGROUP_ENV_VAR = "CGROUP_PARENT_DEV_GATES"`, `:9776`; refused when unset `:5271-5280`,
`verify_slice_loaded` `:5283`). Not the placement leaf `/rg-<token>`: it "only holds migrated pids", so
`docker exec`/runc children escape it (`artifacts/release-program-20261027/reports/compute-design-REVIEW.md:94-98`).
Not an ancestor tier: that parks lanes nobody chose. Freeze is hierarchical, so apply it at the highest cgroup that
is still one unit. `docker pause` on the same container is forbidden (runc's own freezer state), and a frozen scope
is thawed before `docker stop`/`rm` (the registrant deregisters first; the guard also thaws a unit whose scope file
vanished).

**Reclaim (D2).** After a freeze, `memory.reclaim` in chunks (default 256 MiB, at most 8 per pass — the reconcile
script's shape, `mdt-cockpit/host-setup/scripts/mdt-dev-governance-reconcile.sh:318-319`, `README.md:1192-1194`).
It "never OOMs; it returns `-EAGAIN` when it falls short" (`DESIGN-DEDICATED-DEV-MEMORY.md:58-59`), so `-EAGAIN`
ends a pass; lowering `memory.max` is rejected because it "loops reclaim and then calls
`mem_cgroup_out_of_memory()`" (same lines) and can kill. Reclaim frees RAM only if swap has room; what it achieved
is read back from `memory.current`, never assumed. `vm.swappiness` stays host policy
(`compute-design-REVIEW.md:99`); a guard whose reclaim achieves nothing escalates by the victim rule.

**Victim rules (D3).** Three, configurable (:365-368):

| Rule | Chooses | Why |
|---|---|---|
| `newest` (default) | most recently registered valid candidate | least invested work lost (:367) |
| `refault` | highest measured refault rate of that cgroup | the thrashing unit holds the fewest useful pages |
| `smallest-enough` | smallest unit whose reclaim estimate covers the deficit; if that freeze does not relieve the trigger, freeze the next bigger and **thaw the smaller** | (:368) literally |

"Newest" is registration order: the registrant knows when its job started, and registration is what the guard can
see. For `smallest-enough` the estimate derives from cgroupfs (`memory.current` net of `memory.low/min`), never
from a claimed RAM value. Rejected: progress-per-RAM (superseded); "biggest consumer" (may be the oldest job or a
protected unit).

**Thaw order (D11, M1).** A unit thaws when pressure released for `release_s`, or `max_park_s` expired, or the
guard is stopping, or its cgroup vanished. Release and expiry thaw **one unit per `release_s`**,
**oldest-parked first**, re-reading host PSI between thaws: a thawed scope refaults its swapped pages, and several
at once can re-trigger the trigger that parked them. If PSI rises again the queue stops where it is; the rest keep
their place.

**Starvation (D4).** "dont build any (complex) logic for non-starve guarantees, doc only, wait for issues in real
life" (:369). Knobs only: `never_park_last` (at most `candidates - 1` frozen, so something always progresses);
`max_parked` (default 2); `max_park_s` (Q1); `thaw_grace_s`; aging (`last_parked_at` ordering). Documented known
limits (README, not code): two units can ping-pong under `refault`; a frozen job simply takes longer; a memcg OOM
can hit a parked unit; and the guarantee is "some registered work always runs", not "every job finishes in bounded
time". Selection is deterministic for a given (PSI state, candidate set); `candidates` prints the next tick's order.

**Death (D5): nothing stays frozen.** (1) `ExecStopPost=... thaw --all --force` on stop, failure and restart; (2)
the same sweep at start (`ExecStartPre`), reading cgroupfs (`cgroup.freeze`/`cgroup.events` under the allowed
ancestors) **and** the state file, so a lost state file cannot hide a frozen scope; (3) `thrashguard thaw <cgroup>`
works without the daemon; (4) `Restart=on-failure` with a short `RestartSec` (psi-guard uses 5,
`units/mdt-psi-guard.service:12-13`). cgroupfs is the authority: every act reads `cgroup.events` back, and a
mismatch is reported as `desync`, never "assumed done".

**Report-only (D6).** `mode = off|report|enforce`, the shipped vocabulary (`scripts/mdt-psi-guard.py:73`,
`host-setup.env.example:165-166`). `off` exits; `report` writes would-freeze/would-thaw to `events.jsonl` and
changes nothing — the shipped default until the VM test has run; `enforce` acts.

## 3. Interfaces

**Config `/etc/thrashguard/thrashguard.toml` (0640 root:thrashguard)** plus `thrashguard.toml.d/*.toml` merged
lexically (the shape fleetd already reads, `fleetd/src/fleetd/facts.py:440-514`). Keys, no literals in code
(:605): `mode`, `tick_s`, `trigger_avg10`, `hold_s`, `release_avg10_factor`, `release_s`, `victim_rule`,
`max_parked`, `max_park_s`, `never_park_last`, `thaw_grace_s`, `heartbeat_s`, `missed_heartbeats`,
`reclaim_chunk_bytes`, `reclaim_max_chunks`, `boot_grace_s`, `allowed_ancestors` (default `dev-gates.slice`,
`dev-background.slice`, `dev-buildkitd.slice`), `protected` (default `dev-interactive.slice`, `system.slice`,
`user.slice`), `state_dir` (`/run/thrashguard`), `registrant` (bool) and `[[static_unit]]` (`{name, cgroup}`) for
standalone hosts. Unknown keys are errors.

**Sockets (D7).** Two, in separate groups, in the estate's common writer/reader shape
(`/run/mdtd/{observe,control,root}/mdtd.sock 0660 root:mdt-{observe,control,root}`,
`docs/design/mdtd/DESIGN-rev4-2026-11-07.md:190`):

```
/run/thrashguard                              root:root 0755
/run/thrashguard/registrant/thrashguard.sock  root:thrashguard-registrant 0660 (dir 0750)
/run/thrashguard/reader/thrashguard.sock      root:thrashguard-reader     0660 (dir 0750)
```

systemd `.socket` units own them (`LISTEN_FDS`, `Accept=no`), so a daemon restart does not refuse clients
(`hostobs/docs/WIRE.md:12-16`). Group gating is **advisory**: "container root bypasses socket DAC, and container
root can `setgroups()` any GID" (`mdtd/DESIGN-rev4-2026-11-07.md:109`); containment is the scope resolution
below, re-checked on every act. Rejected: one read/write socket (readers would gain the right to park jobs); TCP
(nothing leaves the host). Wire is newline-delimited JSON, versioned (`thrashguard-registration/1`,
`-event/1`, `-state/1`), one request one reply, unknown keys rejected (`fleetd/docs/SPEC.md:26`) — not hostobs'
binary codec (`hostobs/docs/WIRE.md:24-40`): a handful of clients per tick, and legibility matters.

*Registrant verbs:* `register {id, container, score, priority, kind, owner, run, heartbeat_s}`,
`update {id, score, priority}`, `deregister {id}`, `heartbeat {id}`. `score` is the registrant's own ranking
fact; `priority` is `freezable|last-resort`. The guard stores both, uses them only as victim-rule input, never
computes them (Q3).

**The client names a container, not a cgroup path (H2/B3).** `container` is a docker container id or a docker
scope name (`docker-<id>.scope`), because the client cannot know a host cgroup path: a client in its own private
cgroup namespace reads `0::/` and nothing else (`run_gate.py:4296-4312` `cgroup_namespace_is_private`, and
`:18405` `cgroup = None` for the same reason). The guard runs with the host view and resolves the scope itself: it
finds exactly one `/sys/fs/cgroup/**/<scope>` under `allowed_ancestors`, takes the *shallowest* match (the docker
scope, D1), and **refuses the registration naming the reason** when it finds none, several, or a match under
`protected`. Nothing is stored on a refusal; that resolution, not the socket group, is the real boundary (D7). A
resolved scope is re-checked on every act: still under `allowed_ancestors`, `cgroup.events` readable, not a
`protected` ancestor, inode unchanged.

**Lifetime is heartbeat liveness, never a job bound (M2).** `ttl_s` is *derived* by the guard as
`missed_heartbeats x heartbeat_s` (defaults 3 x 30 s = 90 s), from config keys only. No job duration is sent and
no job's own length enters the expiry; a frozen job takes as long as it takes.

**Both deregistration cases, same words everywhere (M3, Q4).**

| Case | Frozen unit | Reason recorded |
|---|---|---|
| explicit `deregister {id}` | thawed before the reply | `deregistered`: the registrant is about to stop or `rm`, and a frozen scope may hang `docker stop` |
| silent `ttl_s` expiry (heartbeats missed, no `deregister`) | **stays** frozen until pressure release, aging or a guard restart (the lost-registrant rule) | `expired`: a candidate-set removal, never a thaw |

*Reader verbs:* `status` (`mode`, `state`, `psi`, `frozen[]`, `parked_s`, `desync[]`), `candidates`,
`events {since_seq}`, `version`.

**Event stream (internal, then published).** Append-only `state_dir/events.jsonl`, one object per line
(`{boot_id, seq, event, unit, cgroup, cgroup_id, mono_start_ns, mono_end_ns, seconds, mode, reason}`), rotated
at 1 MiB to `events.jsonl.1` because `/run` is tmpfs (the psi-guard shape, `scripts/mdt-psi-guard.py:61`,
`:179-186`). `seq` is monotonic per boot, so a reader pages without a clock. This file is thrashguard's own
journal and crash record — **not** the consumer path (§4). Every event is also emitted to hostobs (§6c).

**Standalone (hard requirement, :353).** A host service with its own CLI and `/etc` config, working with no
fleetd, no agctl, no registrant: `[[static_unit]]` plus `mode = enforce` freezes/thaws by the same rules, and
`thrashguard status|candidates|events|freeze|thaw` are local verbs. Absent clients degrade nothing.

## 4. Frozen-time accounting — one path: thrashguard -> hostobs -> consumers

Nothing inside a gate container reads thrashguard (H1 replaced by B2). assay runs *in* the frozen container and
the cockpit runs in a private cgroup namespace, so the consumer path is the host's telemetry: **thrashguard
emits, hostobs records, consumers read.**

**What thrashguard emits.** Per frozen interval: `unit`, `run`, `owner` (registrant-supplied); `cgroup` scope
name and `cgroup_id` (inode, so a recycled path is detectable); `boot_id` (intervals never span boots);
`mono_start_ns`, `mono_end_ns` and `seconds` on **`CLOCK_MONOTONIC`**, not wall time — the kernel keeps it
running while the cgroup is frozen (`compute-design-REVIEW.md:100`); `reason`
(`pressure|aged_out|guard_stop|deregistered|unreadable_psi|manual`); `reclaim_bytes` (read-back, or `null`).
Any wall-clock field is derived, never authoritative.

**Where it is recorded.** hostobs' event store, two names in its closed vocabulary, `thrashguard.freeze` and
`thrashguard.thaw`, over the **control** socket's JSON `annotate` request (`hostobs/docs/WIRE.md:492-497`,
`hostobs/docs/CONSUMERS.md:393-397`). The argument budget is the constraint: `ctx` accepts only `run`, `lane`,
`candidate`, `txn`, `session`, each <= 128 B (`WIRE.md:493`, which defers to 4.10 at `:476`), so the run id rides
in `ctx.run` and five factual fields go in `attrs` (8 pairs, key <= 64 B, value <= 256 B,
`hostobs/crates/store/src/events.rs:42`, `:48`, `:50`): `cgroup` (the scope name), `cgroup_id`, `boot_id`, `seq`,
`seconds`. `reason` and `unit`/`owner` go in the event's `name`/`key`/`code` fields; `reclaim_bytes` stays in
thrashguard's own journal, because a ninth attr does not fit. The issuer is the emitting uid plus its cgroup
incarnation from `SO_PEERCRED`/`SO_PEERPIDFD` (`WIRE.md:498-502`), so no separate emitter identity is invented.
Emission is best-effort and idempotent on `(boot_id, seq)` (D19): a lost event is `unknown`, never a wrong number.

### 4a. What exists today in hostobs, and what is missing (B2 check)

| Part | Today |
|---|---|
| event + span store, bounded ring, closed name/attr bounds | **exists**: `hostobs/crates/store/src/events.rs:1-50` |
| `annotate` verb (control, JSON, peer-bound issuer, 50/s per issuer) | in the table, **not served**: `served: false` at `hostobs/crates/serve/src/verbs.rs:76`; planned M3 (`docs/design/hostobs/PLAN-v4-2026-11-08.md:376`) |
| `events` read verb | **planned M3**: `verbs.rs:73`, `hostobs/docs/WIRE.md:476-484` |
| freeze state as a *sampled* fact (`cgroup.freeze`, `cgroup.events`) | **missing**: not registered in `hostobs/crates/model/src/metric.rs`; `crates/kernel/src/cgroup.rs` reads `cpu.stat`, `memory.*`, `io.stat`, `pids.current`, PSI only |

Owner of every missing row: hostobs (M3; the metric registration needs its own backlog item). Until they land
the consumer path is **absent** and every consumer reports the frozen total as `unknown`. They gate P4, not
P1/P2.

### 4b. assay: effective CPU time, and the verdict does not depend on it

Per candidate/test assay combines **cgprofile profiling** of the lane's cgroup with **freeze/thaw events** from
hostobs, matched by `boot_id` + `cgroup` and unioned. Then `effective_cpu_s = cpu_s` (**unchanged**, M4: a frozen
candidate accrues no CPU seconds, so a CPU-time bound needs no correction) and `effective_wall = wall -
frozen_seconds` for the wall-based clocks only. Rejected: "invalidates any candidate frozen mid-run" — "why?
only if it turns out error? freeze should be harmless" (:344); the refinement stands: re-run only candidates
that overlapped a freeze **and** ended killed/timeout/hung.

**Why no verdict depends on it.** assay's per-candidate bound is the CPU-time no-progress bound (P4: "the
candidate process tree's CPU-seconds since its last completed event", `docs/design/no-time-budgets/DESIGN.md:391-406`,
which also states "CPU accrues only when the candidate runs, so pressure needs no pause rule"). Per-test bounds
(P4's item (2)) should likewise run on CPU time where the runtime allows. A frozen candidate simply takes longer
and survives, so **thrashguard is optional**: run-gate and assay work unchanged without it, and with no events
`frozen_seconds` is `unknown`, never `0`.

**Summaries are normalised, not raw.** Time in an assay summary is on the Geekbench single-thread **1500-point**
basis: `x_ref = x * single_core / baseline_score` with the host's `single_core` from `speed`
(`cgprofile/docs/PROTOCOL.md:625-627`; table fallback `cgprofile/docs/SCORES-TABLE.md:1-27`, baseline 2500 for
GB6 and GB7). A host without `speed` has no basis and the summary says so (`speed: null`); majors are never mixed.

### 4c. run-gate: unchanged verdict, one shared bound, optional pause

It already reads the *psi-guard* events as contamination (`run_gate.py:6065`, `:6107-6133`, `classify_contamination`
`:6134-6174`). A thrashguard freeze must **never** become a contamination flag by itself: it is not a host fault,
but the estate's own act.

Its stall clock already pauses while host memory PSI `full` is high (`ProgressWatch._pressure_high`
`:16407-16420`, `pressure_reader` `:16942-16945`, threshold "> 5") and caps that pause at
`PRESSURE_PAUSE_MAX_SECONDS = 1800.0` (`:706`). That constant and Q1's `max_park_s` are **one number with one
owner**: `max_park_s` lives in thrashguard's `/etc` config (default 30 min, configurable, thaw on expiry) and
run-gate's cap reads the same policy key, so a shorter park cannot silently read as silence. This is the only
run-gate coupling (beyond registration, §4e); reading thrashguard's reader socket from the cockpit (mounted only
where present) is at most an optional extra. If the event source is unreadable every consumer reports `unknown`
and neither flags nor clears a run — the rule run-gate already states for an unknown ancestor event
(`run_gate.py:6142-6147`).

### 4d. fleetd: freeze state for the estate snapshot

fleetd reads freeze state **through hostobs**, the same events, never through thrashguard's socket:
`frozen_units`, `parked_s` and the `psi` fields become one estate snapshot (§6b,
`.controller-state/platform/design/agctl-estate-snapshot-draft.md`). With no events those fields are
`{"v": null, "reason": ...}`, never `0`.

### 4e. Interim: who registers today's gate containers (H2)

B3's chain — fleetd places, starts a lane through run-gate, learns the container id, registers it — exists only
once the shared host runner does (P-fd-05, still open: `fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:855`; its frozen-dev
answer "no freeze" is `DESIGN-DEDICATED-DEV-MEMORY.md:11-17`). Until then **run-gate on the host is the
registrant**, with the identical contract (register at container start, heartbeat, deregister before `rm`):

1. `docker run -d` returns the container id; `container_state` already inspects it (`run_gate.py:7601-7628`).
2. Immediately after start, one `register {id, container: <container id>, score, priority, owner: "run-gate",
   run: <lane/run id>}` — no `cgroup`, no `ttl_s`.
3. `heartbeat {id}` on the tick the client already uses for its stall watch.
4. `deregister {id}` **before** every `docker rm -f` (`:17414`, `:17539`, `:17812`, `:18287`) and in the finally
   path, so a frozen scope never reaches `docker stop`.
5. Anything absent, unreadable or refused is a WARN line and **no** verdict change: the gate behaves exactly as
   it does today without thrashguard.

Beyond registration run-gate does exactly two things: (i) read the same `max_park_s` policy key as its
stall-pause bound (§4c); (ii) optionally read thrashguard's reader socket from the cockpit where it is mounted, to
pause a wall-based stall watch — an extra, never a requirement.

This is delivered by **P4 `estate`**, the first cut that needs a registrant at all; fleetd later replaces only the
*caller*, not the contract. run-gate needs the `thrashguard-registrant` group membership (Q5) and nothing else.

## 5. Packaging, privileges, deploy, tests, MVP

**Build image (L2).** Lanes run in `tester-unified-rust` (`run-gate.root.toml:38-42`, image
`tester-unified-rust:rs-20261124b`, `tester-unified-rust/README.md:3-11`). The devcontainer has no Rust toolchain
and no lane runs here (AGENTS.md gate doctrine).

**Package.** New repo project `thrashguard/`, cmru-managed, Rust (the operator's picture: "a very small rust monitor
for PSI ... seems a valid alternative", :345). Modules `psi`, `config`, `socket`, `units`, `victims`, `actuator`,
`clock`, `events`, `cli`; `tests/`; `deploy/systemd/` (service plus the two `.socket` units);
`deploy/tmpfiles.d/`; README, DESIGN-GUIDE, CONSUMERS, CHANGES, backlog `TG-NN` (Q6: with loader-parsed config
examples); lanes `r0-r1` and async `r2`.

**Privileges (D8).** Root with a minimal set, mirroring `hostobs-act` (`docs/design/hostobs/PLAN-v4-2026-11-08.md:48`):
`ProtectSystem=strict`, `ReadWritePaths=/sys/fs/cgroup /var/lib/thrashguard /run/thrashguard`, `PrivateNetwork`,
`RestrictAddressFamilies=AF_UNIX`, `NoNewPrivileges`, `ProtectKernelTunables=yes`, `ProtectControlGroups` **unset**
(writes are the point), empty `CapabilityBoundingSet` if cgroupfs writes need no capability (hostobs-act's finding)
— verified in the VM, not assumed. Rejected: a non-root user plus setuid helper. Placement: own `thrashguard.slice`
**outside** `dev.slice`, so it keeps running while the tiers are starved (`units/mdt-psi-guard.service:2-3`).

**Deploy.** mdt-cockpit may **offer** it (:615): a host-setup key `MDT_THRASHGUARD=off|report|enforce` beside
`MDT_PSI_GUARD` (`host-setup.env.example:165-166`, `install.sh:1489-1494`) that installs the released binary, units
and groups and nothing else; mdt-cockpit owns no threshold, no victim rule, no decision. Thrashguard installs
identically from its own installer, and a host without mdt-cockpit runs it standalone.

**Tests that never freeze anything here (D9).** Testability is psi-guard's trick: injectable roots.
`tests/test_psi_guard.py:21` defines a `FakeHost` — a temp tree with fake `cg/` and fake pressure. So every unit
test takes `--cgroup-root` and `--pressure-path`; the suite asserts (like `test_psi_guard.py:214`) that no module
hard-codes `/sys/fs/cgroup` or calls kill/signal; sockets are driven over `socketpair`; `docker`/`systemctl` are
never invoked; a fake clock covers hysteresis, aging, `max_park_s`, `never_park_last`, the thaw queue and the death
sweep. Live acceptance runs in a `vm-lab` Debian guest under real systemd (AGENTS.md VM rules): a real container
scope frozen/thawed, `cgroup.events` read back, `ExecStopPost` thaw after `systemctl stop` **and** after `kill -9`,
a memcg OOM on a frozen scope, an unreadable-PSI run.

**MVP split** (each its own review; no freezing in the devcontainer):

| Cut | Content | Proof |
|---|---|---|
| P1 `observe` | config, PSI poll, hysteresis, sockets, candidate store + container->scope resolution, `events.jsonl`, `mode=report`, CLI, static units | unit tests on fake cgroupfs; no act path compiled |
| P2 `act` | freeze/thaw of one scope, reclaim, read-back, death sweep, `enforce`, the M1 thaw queue | VM live acceptance (§5) |
| P3 `victims` | `newest`, `refault`, `smallest-enough`, aging, invariants | fake-clock tests |
| P4 `estate` | **run-gate interim registrant (§4e)**, thrashguard->hostobs emission, `host-facts/2`, fleetd place-and-register, agctl estate snapshot, mdt-cockpit deploy offer | consumer contract tests (§6) |

P4 names its consumers in the order it needs them: the run-gate registrant (who today starts the gate containers),
the hostobs emission (the only consumer path), then fleetd/agctl on top of both.

## 6. Required alignment

### 6a. fleetd: the cross-host placer and registrant (B3 / FD-012 / FD-049)

FD-012 is re-scoped: "fleet host start registers each job with thrashguard's registrant socket when present"
(`KNOWN_ISSUES_TODO_BACKLOG.md:318`; `design-review-2026-11-24/fleetd.md:102-103`). FD-049 replaces
`host-facts/1`'s guard block with `guard_present` plus "the owner's own runtime state" (`:967-974`,
`fleetd/docs/SPEC.md:22-24`).

fleetd on each host is **the one cross-host channel and the placer** (B3): it decides the host and the CPUs, starts
the lane through run-gate, learns the gate container id from run-gate's answer, and registers it. It never resolves
a cgroup path — it passes the **container id**, and the guard resolves the scope itself under `allowed_ancestors`
and refuses what it cannot resolve (§3).

Contract: on `fleet host start`, one `register` per job (`{unit: <job name>, container: <container id>, score,
priority, owner: "fleetd", run: <campaign/job id>}`), `heartbeat {id}` on each host tick, `deregister {id}` on
collect **before** run-gate's `rm`; the score input is campaign progress fleetd already owns. No `ttl_s` and no job
duration is ever sent: expiry is heartbeat liveness (§3, M2). fleetd is the `thrashguard-registrant` group's
service user (Q5).

`host-facts/2` carries `guard_present: bool`, `guard_state: off|report|enforce|unknown`, `config_digest` (SHA-256 of
the `/etc` bytes incl. drop-ins — the existing read, `fleetd/src/fleetd/facts.py:440-514`), and `frozen_units: int`
**read through hostobs**, not through a host socket; unknown fields stay rejected (`SPEC.md:26`). Absent
thrashguard: `guard_present: false`, `guard_state` omitted with its `_reason` (fleetd's nullable-plus-reason
discipline), `host start` unchanged, nothing downgraded. Absent fleetd: standalone mode, no facts, nothing else
changes.

### 6b. agctl: ONE snapshot through fleetd (H3/B3)

agctl already declares "host protection (thrashguard)" not-owned (`docs/design/agctl/DESIGN.md:7`). The archived
rev 3 sentence — "`agctl status` reads its read socket" (`archive/DESIGN-rev3.md:85`) — is **withdrawn**: it
contradicts agctl's own rule that it reaches no host directly, and no mount can fix it, because the socket is per
host.

Contract: agctl reads **one `agctl-estate-snapshot/1`** through fleetd
(`.controller-state/platform/design/agctl-estate-snapshot-draft.md`): jobs and progress, host CPU/load, RAM/swap,
PSI, disk free GB, and the `freeze` block (`frozen`, `since`, `by`, `freeze_reason`, `queued`), which fleetd fills
from hostobs' freeze events and samples. agctl never connects to a host socket and never ssh. Absent thrashguard:
the `freeze` fields carry `{"v": null, "reason": "thrashguard-absent"}`, everything else stays, and agctl never
refuses and never invents a zero. Absent agctl: thrashguard's CLI and fleetd's snapshot answer the same questions.

### 6c. hostobs: the event intake (B2)

Thrashguard emits `thrashguard.freeze` / `thrashguard.thaw` into hostobs; §4a lists what exists there today and what
is missing. Emission goes over the **control** socket's JSON `annotate` line (`hostobs/docs/WIRE.md:492-497`,
`hostobs/docs/CONSUMERS.md:393-397`), group `hostobs-control` (§4), with the attribute budget spent as described
there. Missing today (owner: hostobs) is exactly §4a's three rows; until they land consumers report `unknown`. The
`cgroup` attr is exact (`docker-<id>.scope` fits <= 256 B) and readers match on it plus `boot_id`, as §4b does.

### 6d. Standalone is a hard requirement

The service, CLI and `/etc` config are complete without fleetd and without agctl (:353, and :615 "mdt-cockpit does
not own freeze"): `[[static_unit]]` supplies candidates, `thrashguard status|candidates|events|freeze|thaw` are
local verbs, the config validates alone, and the sockets are optional (`registrant = false`). Acceptance: a guest VM
with no fleetd, no agctl and no registrant reaches an enforce-mode freeze/thaw cycle from `/etc` alone — part of
P2's exit criteria.

## 7. Decisions and rejected alternatives

| # | Decision | Rejected because |
|---|---|---|
| D1 | freeze the one-unit docker scope | the placement leaf holds only migrated pids (`compute-design-REVIEW.md:94-98`); a tier freeze parks unchosen lanes |
| D2 | freeze, then chunked `memory.reclaim`, `-EAGAIN` = done | lowering `memory.max` can OOM-kill (`DESIGN-DEDICATED-DEV-MEMORY.md:58-59`) |
| D3 | three victim rules, `newest` default | progress-per-RAM superseded; "biggest" can hit the cockpit or the oldest job |
| D4 | knobs plus documented limits, no scheduler | operator :369 |
| D5 | death sweep from cgroupfs and `ExecStopPost` | a state file alone hides a frozen scope after a lost write |
| D6 | `off|report|enforce`, report first | an untested freeze in `enforce` is the hazard psi-guard forbids (`scripts/mdt-psi-guard.py:30-31`) |
| D7 | two sockets, two groups, systemd-owned | one socket gives readers the right to park jobs; TCP exposes a host actuator |
| D8 | root, minimal privilege set, outside `dev.slice` | a setuid helper adds a second authority; inside `dev.slice` it starves with its own victims |
| D9 | injectable cgroup root and pressure path in tests | anything else freezes the cockpit's own work here |
| D10 | monotonic clocks plus `boot_id` per interval | wall-clock corrections over NTP steps are unfalsifiable |
| D11 | thaw one unit per `release_s`, oldest first, re-read PSI between (M1) | a thaw refaults swapped pages; thawing several at once re-triggers the trigger |
| D12 | the client names a **container id**, the guard resolves the scope (H2/B3) | a namespaced client cannot read the host path (`run_gate.py:4296-4312`); a client-supplied path would let any registrant reach any scope |
| D13 | expiry is `missed_heartbeats x heartbeat_s`, never a job bound (M2) | a job time bound contradicts no-time-budgets and would end a job for taking long |
| D14 | explicit `deregister` thaws; silent expiry does not (M3) | a frozen scope may hang `docker stop`; thawing into a still-full host only re-parks |
| D15 | consumers read hostobs, never a host socket (B2, replaces H1) | assay runs *inside* the frozen container and the cockpit is cgroup-namespaced; a mount needs a per-host path in every container |
| D16 | thrashguard is optional; verdicts are CPU-time based; no events = `unknown` | a wall-time correction must never become a hidden wall cap |
| D17 | `max_park_s` (30 min default, configurable, thaw on expiry) is one policy key shared with run-gate's pause bound (Q1) | two bounds drift and one silently reads as silence |
| D18 | one estate snapshot through fleetd for agctl (H3/B3) | agctl has no host access; a per-host socket read from the cockpit is impossible |
| D19 | emission is idempotent on `(boot_id, seq)` and best-effort | a retried event must not double count a frozen interval |

Rejected alternatives, by topic:

- **H1's two options** (bind-mount `/run/thrashguard/reader` into the gate containers; or let run-gate read the
events and hand the judge a number): both put the correction inside the container being frozen, and neither
survives a multi-host cockpit.
- **assay reading a thrashguard socket**: it would block exactly when it is needed.
- **an expiry that thaws** ("the registrant's job ended"): D14.
- **`ttl_s` sent by the client** ("job bound + slack"): D13; the client may still send its own `heartbeat_s`.
- **one read/write socket, TCP transport, `docker pause`, `memory.max` lowering**: D2, D7, §2.

## 8. Recorded decisions, and what is still open

All six rev-1 questions were answered by the operator on 2026-11-25 23:4xZ
(`.controller-state/operator-decisions.md:625-631`) or by the 00:0xZ/00:2xZ rulings (`:632-637`). This section is
the record, not a list of open questions.

| # | Question | Decision | Who |
|---|---|---|---|
| Q1 | `max_park_s` | 30 min default, configurable, **thaw on expiry**; run-gate's `PRESSURE_PAUSE_MAX` reads the same policy key (§4c) | operator 23:4xZ |
| Q2 | freeze granularity under `docker exec` | park the **whole container scope**; the `docker exec` client's own stall is accepted | operator 23:4xZ |
| Q3 | score direction | higher = more valuable; `priority` is a separate flag `freezable|last-resort` (§3) | main's recommendation, accepted |
| Q4 | does an expired registration thaw? | explicit `deregister` **thaws** (`deregistered`); silent `ttl_s` expiry **does not** (§3, D14) | operator 23:4xZ, with M3's split |
| Q5 | groups | `thrashguard-registrant`: run-gate's host group + fleetd's service user; `thrashguard-reader`: agctl, fleetd, hostobs (§3) | operator 23:4xZ |
| Q6 | docs obligation | README + DESIGN-GUIDE + CONSUMERS.md with loader-parsed examples, in P1's file list (§5) | review, accepted |
| — | consumer path | thrashguard **sends** freeze/thaw events; **hostobs** picks them up; assay gets cgprofile plus those events and recomputes effective CPU time; summaries normalise to Geekbench single-thread 1500 (§4) | operator 00:0xZ |
| — | boundaries | verdicts are CPU-time based and thrashguard is optional; fleetd is the one cross-host channel and the placer; agctl never touches a host socket (§4e, §6) | operator 00:0xZ / 00:2xZ |

**Still open** (product calls, not blockers):

1. **`refault`'s input.** `memory.stat`'s refault counters are intended, but nothing reads them yet; with none
   readable the rule must say `unknown` and fall back to `newest`, never to a zero rate (§2, D3).
2. **Where the shared policy key lives.** One key, two readers (thrashguard's `/etc` and run-gate's constant): the
   file is not decided. It must be a host-owned path both can read; a `scripts/controller/controller-policy.toml`-style
   file was a suggestion, not a decision.

UNVERIFIED, VM-test items rather than assumptions: the memcg-OOM versus frozen-scope interleaving; `ExecStopPost`
after an OOM-killed daemon; whether `cgroup.freeze` writes need no capability; whether `docker stop` on a frozen
scope hangs; whether hostobs' `annotate` rate limit (50/s per issuer, `WIRE.md:495`) is enough for a burst freeze
over many units.
