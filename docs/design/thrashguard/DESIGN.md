<!-- status: design (rev 1); scope: new standalone host service TG-1; operator decision 2026-11-25 -->
# Design: thrashguard (TG-1) — PSI-driven freeze/thaw of dev-tier work

Nothing of thrashguard exists: no code, no unit, no host service. `grep -rln
thrashguard` over `*.py *.rs *.sh *.toml *.service *.socket` finds prose only,
and every other tool already points here (fleetd `docs/SPEC.md:24`, `:533-534`,
`KNOWN_ISSUES_TODO_BACKLOG.md:20`, `:318`, `:967-974`; hostobs
`KNOWN_ISSUES_TODO_BACKLOG.md:1163-1164`; agctl `docs/design/agctl/DESIGN.md:7`).
This note is normative for the first implementation. Operator inputs are quoted
with line numbers from `.controller-state/operator-decisions.md`; later lines
win over earlier ones.

## 1. Scope and boundary

**Thrashguard is the actuator for "freeze".** Operator 2026-11-25: "thrashguard
(TG-1) is the important missing piece; it does not exist yet. mdt-cockpit does
not own freeze; it may only offer to deploy thrashguard as a service" (:615); the
programme still holds fleetd's freeze for this
(`.controller-state/PROGRAM-2026-11-24/PROGRAM.md:448-449`). Overcommit is
intended and "only PSI is the signal to freeze things" (:497); "stop line = memory
PSI `full` avg10 >= 3 only (heavy swapping); no stricter waits" (:533); the act
is "freeze+`memory.reclaim` ... park one thing ... until something else finished
or RAM requirements changed" (:207).

It does: poll host memory PSI; decide the host is thrashing; choose one unit from
a **registered** candidate set (registrants push shed scores, :353, :355) by a
configured victim rule (:365-368); freeze that unit's cgroup and optionally
`memory.reclaim` it; thaw it on pressure release, aging or shutdown, never
leaving anything frozen; and record every frozen interval so assay can correct
wall time (:352) and readers can see state.

It does not:

- compute progress, RAM value or shed scores: "reading per-job progress and RAM
  rather belongs in `run-gate` or assay then, this is high level, wrong boundry
  for thrashguard" (:355). The registrant computes the score (:353).
- rank by progress-per-RAM; that rule (CP-64) is superseded
  (`.controller-state/design-review-2026-11-24/fleetd.md:101`).
- kill: OOM stays with memcg/systemd-oomd, aimed at the load tiers on purpose
  (`mdt-cockpit/host-setup/docs/DESIGN-DEDICATED-DEV-MEMORY.md:13-17`, `:29-34`).
- write CPU/IO weights or `memory.high`: that is `mdt-psi-guard`
  (`mdt-cockpit/host-setup/scripts/mdt-psi-guard.py:21-28`, `:71-72`).
- touch host services, `system.slice`, `user.slice`, the cockpit
  (`dev-interactive`) or `/root/tls-edge-LIVE` ("it shoud not be touched at this
  point", :496): only registered or configured dev-tier cgroups.

`mdt-psi-guard` runs *outside* `dev.slice`, watches PSI and weights, and "never
freezes or kills anything — a wall-clock budget that expired while frozen would
change a gate's result" (`units/mdt-psi-guard.service:2-4`,
`scripts/mdt-psi-guard.py:30-31`); its test forbids the very syscalls thrashguard
needs (`tests/test_psi_guard.py:214`), and run-gate reads its events as
contamination (`run_gate.py:6065`, `:6107`, `:6157`). The jobs stay separate:
psi-guard protects the host from the dev tier; thrashguard parks dev-tier work so
the dev tier stops thrashing itself and the host.

| Tool | Relation |
|---|---|
| `mdt-psi-guard` | independent: it never freezes, thrashguard never re-weights; same PSI counters, no shared state. |
| `fleetd` | a *client*: FD-012 became "job registration on the thrashguard socket, owner TG-1" (`KNOWN_ISSUES_TODO_BACKLOG.md:318`, direction `design-review-2026-11-24/fleetd.md:102-103`); FD-049 reports `guard_present` + owner state (`:967-974`). Never validates thrashguard's config. |
| `run-gate`, `assay` | registrants: they own progress/RAM facts, so they compute the shed score; they consume frozen intervals (§4). |
| `hostobs` | read-only telemetry, no registrant role; its own backlog item lands first (`hostobs/KNOWN_ISSUES_TODO_BACKLOG.md:1163-1164`). |
| `agctl` | reads status and events by command (§6b); never ssh. |

## 2. Control loop

One tick thread (default 5 s) plus a socket loop.

**Trigger.** Host memory PSI `full` avg10 >= `trigger_avg10` (default **3**, :533)
held `hold_s` (default 10, psi-guard's number, `mdt-psi-guard.py:55`). Only the
host-wide `full` row: it measures time lost to reclaim/swap, i.e. "heavy swapping".
CPU and IO pressure never trigger; per-cgroup `memory.pressure` is reported, never
acted on. Thaw at `full avg10 <= trigger_avg10 / 2` held `release_s` (default 60,
`mdt-psi-guard.py:56`); that factor-of-two hysteresis is the shipped guard's shape
and stops act/revert flapping. **Unreadable PSI is not zero pressure**: missing,
unparseable or reset counters set `unknown` — freeze nothing, thaw what this guard
froze, report `unknown`, never a synthetic 0 (AGENTS.md "absence for emptiness").

**Candidates.** Only units registered over the registrant socket, plus an optional
static `/etc` list (§3). No default scan of `dev.slice`: an unregistered cgroup
cannot be ranked, and a scan would reach the cockpit.

**Freeze target (D1).** The cgroup holding *all* processes of one unit — the docker
scope the container started in, e.g. `/dev.slice/dev-gates.slice/docker-<id>.scope`,
which run-gate's `--cgroup-parent` produced (`run_gate.py:529` `CGROUP_ENV_VAR =
"CGROUP_PARENT_DEV_GATES"`, `:9776`; refused when unset `:5257-5277`,
`verify_slice_loaded` `:5283`). Not the placement leaf `/rg-<token>`: it "only holds
migrated pids", so `docker exec`/runc children escape it
(`artifacts/release-program-20261027/reports/compute-design-REVIEW.md:94-98`). Not
an ancestor tier: that parks lanes nobody chose. Freeze is hierarchical, so apply
it at the highest cgroup that is still one unit. `docker pause` on the same
container is forbidden (runc's own freezer state), and a frozen scope is thawed
before `docker stop`/`rm` (the registrant deregisters first; the guard also thaws a
unit whose scope file disappeared).

**Reclaim (D2).** After a freeze, `memory.reclaim` in chunks (default 256 MiB, at
most 8 per pass — the reconcile script's shape, `mdt-cockpit/host-setup/README.md:1193`).
It "never OOMs; it returns `-EAGAIN` when it falls short"
(`DESIGN-DEDICATED-DEV-MEMORY.md:58-59`), so `-EAGAIN` ends a pass; lowering
`memory.max` is rejected because it "loops reclaim and then calls
`mem_cgroup_out_of_memory()`" (same lines) and can kill. Reclaim frees RAM only if
swap has room; what it achieved is read back from `memory.current`, never assumed.
`vm.swappiness` stays host policy (`compute-design-REVIEW.md:99`); a guard whose
reclaim achieves nothing escalates by the victim rule.

**Victim rules (D3).** Three, configurable (:365-368):

| Rule | Chooses | Why |
|---|---|---|
| `newest` (default) | most recently registered valid candidate | least invested work lost (:367) |
| `refault` | highest measured refault rate of that cgroup | the thrashing unit holds the fewest useful pages |
| `smallest-enough` | smallest unit whose reclaim estimate covers the deficit; if that freeze does not relieve the trigger, freeze the next bigger and **thaw the smaller** | (:368) literally |

"Newest" is registration order: the registrant knows when its job started, and
registration is what the guard can see. For `smallest-enough` the estimate derives
from cgroupfs (`memory.current` net of `memory.low/min`), never from a claimed RAM
value (§1). Rejected: progress-per-RAM (superseded); "biggest consumer" (may be the
oldest job or a protected unit).

**Thaw order.** A unit thaws when pressure has released for `release_s`, or
`max_park_s` expired, or the guard is stopping, or its cgroup vanished. Release and
expiry thaw **oldest-parked first**, so the longest park gets its turn instead of
the newest being cycled again.

**Starvation (D4).** "dont build any (complex) logic for non-starve guarantees, doc
only, wait for issues in real life" (:369). So knobs and actuator invariants only:
`never_park_last` (at most `candidates - 1` units frozen, so something can always
progress); `max_parked` (default 2); `max_park_s` (Q1); `thaw_grace_s` (a just-thawed
unit is not re-chosen for `n` ticks); aging (`last_parked_at` ordering), so a long
run cannot be passed over forever by churning short ones. Documented known limits
(README, not code): two units can ping-pong under `refault`; a frozen job simply
takes longer; a memcg OOM can hit a parked unit if another job oversubscribes; and
the guarantee is "some registered work always runs", not "every job finishes in
bounded time". Selection is deterministic for a given (PSI state, candidate set), and
the `candidates` verb prints the order the next tick would use.

**Death (D5): nothing stays frozen.** (1) `ExecStopPost=... thaw --all --force` on
stop, failure and restart; (2) the same sweep at start (`ExecStartPre`), reading
cgroupfs (`cgroup.freeze`/`cgroup.events` under the allowed ancestors) **and** the
state file, so a lost state file cannot hide a frozen scope; (3) `thrashguard thaw
<cgroup>` works without the daemon; (4) `Restart=on-failure` with a short `RestartSec`
(psi-guard uses 5, `units/mdt-psi-guard.service:12-13`). cgroupfs is the authority,
not a lock file: every act reads `cgroup.events` back and a mismatch is reported as
`desync`, never "assumed done".

**Report-only (D6).** `mode = off|report|enforce`, the shipped vocabulary
(`mdt-psi-guard.py:73`, `host-setup.env.example:165-166`). `off` exits; `report` writes
would-freeze/would-thaw to `events.jsonl` and changes nothing, and is the shipped
default until the VM test has run; `enforce` acts.

## 3. Interfaces

**Config `/etc/thrashguard/thrashguard.toml` (0640 root:thrashguard)** plus
`thrashguard.toml.d/*.toml` merged lexically (the shape fleetd already reads,
`fleetd/src/fleetd/facts.py:439-470`). Keys, no literals in code (:605): `mode`,
`tick_s`, `trigger_avg10`, `hold_s`, `release_avg10_factor`, `release_s`, `victim_rule`,
`max_parked`, `max_park_s`, `never_park_last`, `thaw_grace_s`, `reclaim_chunk_bytes`,
`reclaim_max_chunks`, `boot_grace_s`, `allowed_ancestors` (default `dev-gates.slice`,
`dev-background.slice`, `dev-buildkitd.slice`), `protected` (default
`dev-interactive.slice`, `system.slice`, `user.slice`), `state_dir` (`/run/thrashguard`),
`registrant` (bool) and `[[static_unit]]` (`{name, cgroup}`) for standalone hosts.
Unknown keys are errors.

**Sockets (D7).** Two, in separate groups, in the estate's common writer/reader shape
(`/run/mdtd/{observe,control,root}/mdtd.sock 0660 root:mdt-{observe,control,root}`,
`docs/design/mdtd/DESIGN-rev4-2026-11-07.md:190`):

```
/run/thrashguard                              root:root 0755
/run/thrashguard/registrant/thrashguard.sock  root:thrashguard-registrant 0660 (dir 0750)
/run/thrashguard/reader/thrashguard.sock      root:thrashguard-reader     0660 (dir 0750)
```

systemd `.socket` units own them (`LISTEN_FDS`, `Accept=no`), so a daemon restart does
not refuse clients (the hostobs reason, `docs/LIVE-DEMO.md:89-95`,
`hostobs/docs/WIRE.md:12-16`). Group gating is **advisory**: "container root bypasses
socket DAC, and container root can `setgroups()` any GID"
(`mdtd/DESIGN-rev4-2026-11-07.md:109`); containment is `allowed_ancestors` /
`protected`, re-checked on every act. Rejected: one read/write socket (readers would
gain the right to park jobs); TCP (nothing leaves the host).

Wire: newline-delimited JSON, versioned (`thrashguard-registration/1`, `-event/1`,
`-state/1`), one request one reply, unknown keys rejected (`fleetd/docs/SPEC.md:26`).
Not hostobs' binary codec (`hostobs/docs/WIRE.md:24-40`): a handful of clients per
tick, and legibility matters more than bytes.

*Registrant verbs:* `register {id, unit, cgroup, score, priority, kind, owner, run,
ttl_s}`, `update {id, score, priority}`, `deregister {id}`, `heartbeat {id}`. `score` is
the registrant's own ranking fact; `priority` is `freezable|last-resort`. The guard
stores both, uses them only as victim-rule input, never computes them. A registration
past `ttl_s` stops being a candidate but is not thawed (Q4). `cgroup` comes from the
registrant deliberately: a client inside its own private cgroup namespace cannot read
the host path and says so rather than guessing (run-gate's measured note,
`run_gate.py:18396-18404`, sets `cgroup = None`). The guard has no private namespace,
so it validates every named cgroup before acting — path exists, ancestor of nothing in
`protected`, strictly under one of `allowed_ancestors`, `cgroup.events` readable.
Anything else is refused with a reason and never stored: the socket group is advisory
(D7), so that path check is the real boundary.

*Reader verbs:* `status` (`mode`, `state`, `psi`, `frozen[]`, `parked_s`, `desync[]`),
`candidates`, `events {since_seq}`, `version`.

**Event stream.** Append-only `state_dir/events.jsonl`, one object per line (`{ts,
boot_id, seq, event, unit, cgroup, cgroup_id, frozen_at, thawed_at, seconds, mode,
reason}`), rotated at 1 MiB to `events.jsonl.1` because `/run` is tmpfs
(`mdt-psi-guard.py:61`, `:179-186`). `seq` is monotonic per boot, so a reader pages
without a clock. Same file shape run-gate already parses (`run_gate.py:6107-6133`), so a
second consumer needs no new parser.

**Standalone (hard requirement, :353).** The binary is a host service with its own CLI
and `/etc` config and works with no fleetd, no agctl, no registrant: `[[static_unit]]`
plus `mode = enforce` freezes/thaws by the same rules, and `thrashguard
status|candidates|events|freeze|thaw` are local verbs over the same sockets. Absent
clients degrade nothing (the static list is just another candidate source); absent
fleetd means only that no `host-facts` exist.

## 4. Frozen-time accounting

Per frozen interval, `events.jsonl` and `state.json` carry: `unit`, `run`, `owner`
(registrant-supplied); `cgroup` (absolute scope path, D1); `cgroup_id` (inode from
`cgroup.stat`, so a recycled path is detectable); `boot_id`
(`/proc/sys/kernel/random/boot_id` — intervals never span boots); `frozen_at`,
`thawed_at`, `frozen_seconds` on **`CLOCK_MONOTONIC`**, not wall time; `reason`
(`pressure|aged_out|guard_stop|deregistered|unreadable_psi|manual`); `reclaim_bytes`
(read-back, or `null`). Monotonic, because the kernel keeps it running while the
cgroup is frozen (`compute-design-REVIEW.md:100`): guard and client intervals subtract
cleanly and no NTP step can stretch one. Any wall-clock field is derived, never
authoritative.

**assay.** Per candidate/test, `frozen_seconds` = the union of intervals where `boot_id`
matches and `cgroup` equals that candidate's own visible cgroup (assay already derives
it from procfs: `assay/src/assay/liveness_resources.py:39-50`, `_read_cgroup_path`; its
watchdog is told never to read a resource stall as a functional `hung`, `:1-9`). Then
`effective_wall = wall - frozen_seconds`, and the same subtraction applies to assay's
liveness/stall clock, so a parked candidate is judged on the time it actually ran.
Rejected: "invalidates any candidate frozen mid-run" — "why? only if it turns out
error? freeze should be harmless" (:344). The later refinement stands: re-run only
candidates that overlapped a freeze **and** ended killed/timeout/hung.

**run-gate, twice.** It already reads guard events as contamination (`run_gate.py:6065`,
`:6157`, `classify_contamination` `:6134-6174`); a freeze must not become a contamination
flag by itself (it is not a host fault). Its stall watch already pauses while host PSI
`full` is high (`ProgressWatch._pressure_high` `:16409-16420`, `pressure_reader`
`:16940-16948`, "> 5") but caps that pause at `PRESSURE_PAUSE_MAX_SECONDS = 1800.0`
(`:706`), so a freeze longer than 30 min still reads as silence. Decision: the pause
**and** the frozen-time subtraction are both armed and share one bound (Q1). Rejected:
dropping the pause and relying only on events, because pressure slows a lane without
thrashguard freezing anything (`docs/design/no-time-budgets/DESIGN.md:1-40`: a freeze
must not become a hidden wall cap). If the event store is unreadable, both consumers
report the correction as `unknown` and neither flag nor clear a run — the rule run-gate
already states for an unknown ancestor event (`run_gate.py:6142-6147`) — never `0`.

## 5. Packaging, privileges, deploy, tests, MVP

**Package.** New repo project `thrashguard/`, cmru-managed, Rust (the operator's own
picture: "a very small rust monitor for PSI ... seems a valid alternative", :345).
Modules `psi`, `config`, `socket`, `units`, `victims`, `actuator`, `clock`, `events`,
`cli`; `tests/`; `deploy/systemd/` (service plus the two `.socket` units);
`deploy/tmpfiles.d/`; README, DESIGN-GUIDE, CONSUMERS, CHANGES, backlog `TG-NN`; lanes
`r0-r1` and async `r2`.

**Privileges (D8).** Root with a minimal set, mirroring `hostobs-act`
(`docs/design/hostobs/PLAN-v4-2026-11-08.md:47-48`): `ProtectSystem=strict`,
`ReadWritePaths=/sys/fs/cgroup /var/lib/thrashguard /run/thrashguard`, `PrivateNetwork`,
`RestrictAddressFamilies=AF_UNIX`, `NoNewPrivileges`, `ProtectKernelTunables=yes`,
`ProtectControlGroups` **unset** (writes are the point), empty `CapabilityBoundingSet`
if cgroupfs writes need no capability (hostobs-act's finding, PLAN-v4:47) — verified in
the VM, not assumed. Rejected: a non-root user plus setuid helper (two authorities where
one will do). Placement: its own `thrashguard.slice` **outside** `dev.slice`, so it keeps
running while the tiers are starved (`units/mdt-psi-guard.service:2-3`).

**Deploy (6d).** mdt-cockpit may **offer** it (:615): a host-setup key
`MDT_THRASHGUARD=off|report|enforce` beside `MDT_PSI_GUARD`
(`host-setup.env.example:165-166`, `install.sh:1489-1494`) that installs the released
binary, units and groups and nothing else; mdt-cockpit owns no threshold, no victim rule,
no decision. Thrashguard installs identically from its own installer, and a host without
mdt-cockpit runs it standalone.

**Tests that never freeze anything in the devcontainer (D9).** Testability is psi-guard's
trick: injectable roots. `tests/test_psi_guard.py:1-40` builds a `FakeHost` — a temp tree
with fake `cg/` and fake pressure — and injects pressure; freeze writes become file writes
there. So every unit test takes `--cgroup-root` and `--pressure-path`; the suite asserts
(like `test_psi_guard.py:214`) that no module hard-codes `/sys/fs/cgroup` or calls
kill/signal; sockets are driven over `socketpair`; `docker`/`systemctl` are never invoked;
a fake clock covers hysteresis, aging, `max_park_s`, `never_park_last` and the death sweep.
Live acceptance runs in a `vm-lab` Debian guest under real systemd (AGENTS.md VM rules):
a real container scope frozen/thawed, `cgroup.events` read back, `ExecStopPost` thaw after
`systemctl stop` **and** after `kill -9` of the daemon, a memcg OOM on a frozen scope, and
an unreadable-PSI run.

**MVP split** (each its own review; no freezing in the devcontainer):

| Cut | Content | Proof |
|---|---|---|
| P1 `observe` | config, PSI poll, hysteresis, sockets, candidate store, `events.jsonl`, `mode=report`, CLI, static units | unit tests on fake cgroupfs; no act path compiled |
| P2 `act` | freeze/thaw of one scope, reclaim, read-back, death sweep, `enforce` | VM live acceptance (§5) |
| P3 `victims` | `newest`, `refault`, `smallest-enough`, aging, invariants | fake-clock tests |
| P4 `estate` | fleetd registration + `host-facts/2`, agctl read, mdt-cockpit deploy offer | consumer contract tests (§6) |

## 6. Required alignment

### 6a. fleetd (FD-012 / FD-049)

FD-012 is re-scoped: "fleet host start registers each job with thrashguard's registrant
socket when present" (`KNOWN_ISSUES_TODO_BACKLOG.md:318`;
`design-review-2026-11-24/fleetd.md:102-103`). FD-049 replaces `host-facts/1`'s guard
block with `guard_present` plus "the owner's own runtime state" (`:967-974`,
`fleetd/docs/SPEC.md:22-24`).

Contract: on `fleet host start`, one `register` per job (`{unit: <container>, cgroup:
</dev.slice/.../docker-<id>.scope>, score, priority, owner: "fleetd", run:
<campaign/job id>, ttl_s: <job bound + slack>}`), `heartbeat` on each `tick`,
`deregister` on collect; the score input is campaign progress fleetd already owns.
`host-facts/2` carries `guard_present: bool`, `guard_state: off|report|enforce|unknown`,
`config_digest` (SHA-256 of the `/etc` bytes incl. drop-ins — the existing read,
`fleetd/src/fleetd/facts.py:439-513`), and `frozen_units: int`; unknown fields stay
rejected (`SPEC.md:26`).

Absent thrashguard: `guard_present: false`, `guard_state` omitted with its `_reason`
(fleetd's nullable-plus-reason discipline), `host start` unchanged, no registration
attempted, nothing downgraded. Absent fleetd: standalone mode, no facts, nothing else
changes.

### 6b. agctl

agctl already declares "host protection (thrashguard)" not-owned
(`docs/design/agctl/DESIGN.md:7`), and archived rev 3 already says `agctl status` "reads
its read socket" (`archive/DESIGN-rev3.md:85`). Contract: one `status` plus `events
{since_seq}` per `agctl status`/`agctl estate` tick, mapped into the estate row as
`frozen_units`, `parked_s`, `mode`, `candidate_order` — read at no LLM cost, no ssh, no
host probe (agctl's rule, `DESIGN.md` §4a "agctl reaches no host directly").

Absent thrashguard (no socket): the row shows `thrashguard: absent`, every other field
stays, agctl never refuses and never invents a zero. Absent agctl: thrashguard's CLI
answers the same questions.

### 6c. Standalone is a hard requirement

The service, CLI and `/etc` config are complete without fleetd and without agctl (:353,
and :615 "mdt-cockpit does not own freeze"). Concretely: `[[static_unit]]` supplies the
candidates, `thrashguard status|candidates|events|freeze|thaw` are local verbs, the
config validates alone, and the sockets are optional (`registrant = false`). Acceptance:
a guest VM with no fleetd, no agctl and no registrant reaches an enforce-mode
freeze/thaw cycle from `/etc` alone — part of P2's exit criteria.

## 7. Decisions and rejected alternatives

| # | Decision | Rejected because |
|---|---|---|
| D1 | freeze the one-unit docker scope | the placement leaf holds only migrated pids (`compute-design-REVIEW.md:94-98`); a tier freeze parks unchosen lanes |
| D2 | freeze, then chunked `memory.reclaim`, `-EAGAIN` = done | lowering `memory.max` can OOM-kill (`DESIGN-DEDICATED-DEV-MEMORY.md:58-59`) |
| D3 | three victim rules, `newest` default | progress-per-RAM superseded; "biggest" can hit the cockpit or the oldest job |
| D4 | knobs plus documented limits, no scheduler | operator :369 |
| D5 | death sweep from cgroupfs and `ExecStopPost` | a state file alone hides a frozen scope after a lost write |
| D6 | `off|report|enforce`, report first | an untested freeze in `enforce` is the hazard psi-guard forbids (`mdt-psi-guard.py:30-31`) |
| D7 | two sockets, two groups, systemd-owned | one socket gives readers the right to park jobs; TCP exposes a host actuator |
| D8 | root, minimal privilege set, outside `dev.slice` | a setuid helper adds a second authority; inside `dev.slice` it starves with its own victims |
| D9 | injectable cgroup root and pressure path in tests | anything else freezes the cockpit's own work here |
| D10 | monotonic clocks plus `boot_id` per interval | wall-clock corrections over NTP steps are unfalsifiable |

## 8. Open operator questions

1. **`max_park_s`.** Run caps were removed (:614) — but a freeze is not a run cap (wall
   time is corrected, §4) and a parked unit makes no progress either. (a) bound it,
   default 30 min, thaw on expiry; (b) no bound, PSI release only; (c) no bound but
   report when > 30 min. Recommended (a), configurable. It also settles run-gate's
   `PRESSURE_PAUSE_MAX_SECONDS = 1800.0` (`:706`): read the same policy number?
2. **Freeze granularity under `docker exec`.** Parking the container scope stops every
   process in it, including a `docker exec` a runner started and whose client would
   otherwise report a stall. Confirm parking the whole container is intended.
3. **Score direction.** Higher = more valuable (so a `last-resort` job keeps a high
   score and is never chosen) with `priority` as a separate flag, or one signed number?
   Recommended: score higher = keep, flag for "never unless nothing else exists".
4. **Deregistration.** Should an expired registration thaw its unit? Recommended no:
   thaw only on pressure release, aging or a guard restart — a dead registrant's job
   unfrozen into a still-full host gains nothing.
5. **Groups.** `thrashguard-registrant` and `thrashguard-reader` must differ from 0 and
   the docker GID (`docs/LIVE-DEMO.md:89-95`). Which existing groups join by default
   (run-gate's host group, assay's)?
6. **Docs obligation.** thrashguard adds closed values (`mode`, `victim_rule`,
   `priority`, reason strings) a consumer must type; per AGENTS.md they belong in README
   + DESIGN-GUIDE + CONSUMERS.md with loader-parsed examples, so P1's file list carries
   all three.

UNVERIFIED, all VM-test items rather than assumptions: the memcg-OOM versus frozen-scope
interleaving; `ExecStopPost` after an OOM-killed daemon; whether `cgroup.freeze` writes
need no capability; whether `docker stop` on a frozen scope hangs.
