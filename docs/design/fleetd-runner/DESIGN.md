<!-- status: design; scope: fleetd + remote-gate + dev + campaigns; package P-fd-05 (CTL-51); input 2026-11-25/26 -->
# Design: one shared host runner and fleetd placement (P-fd-05 = CTL-51)

## 0. Decisions

| # | Decision | Rejected alternative |
|---|---|---|
| D1 | One job API (`fleet-host-agent/1`) for every client; the two modes differ only in who picks the host | three transports kept, each with its own admission |
| D2 | Placement reads `host-facts/2`, collected by the host agent, never `docker stats` | comparing per-container docker stats |
| D3 | Capacity = planned core use `<= cores + 1`, capped further by the host's own `planning_cap` | cores - 1, or "load > 2x cores" |
| D4 | RAM is planned by warm working set; swap is never counted, and live memory PSI remains a hard stop | peak-RSS plus swap budget |
| D5 | One admission mechanism, on the host (B3), shared by every caller; waiting is exit 75 | per-caller flocks on the controller (today) |
| D6 | Progress, not wall time, decides a stall | any new wall-clock budget on a run |
| D7 | Stop the lane before collecting; keep its checkout and charge if it cannot be stopped | collect from a live store, then terminate it |
| D8 | Fleetd credentials cover fleetd-owned hosts only; job records carry no secret | one credential store for both modes |
| D9 | Controller-host jobs plan at most 3 GiB total, with no job above `small` | use the host's free memory as the cap |
| D10 | fleetd owns one fair cross-host queue; aged large jobs reserve capacity, and merge-unblocking gates lead | independent host queues for fleetd-placed jobs |
| D11 | A known thrashguard freeze blocks new admission; running jobs are left alone | admit during a freeze |
| D12 | ciu uses an installed host runner's queue; without one it keeps its own admission | give ciu a second queue on an enrolled host |
| D13 | The dev run's `max_s` wall limit is removed; progress judges a run | terminate dev jobs after a fixed duration |

## 1. Scope, non-goals, current state

**Scope.** The job API, the host agent implementing it, the fleetd client, placement and
admission, the port of remote-gate's job script, result transport, and the migration of four
clients. Closes CTL-51, CTL-48, FD-042, FD-003, FD-004. **Non-goals:** the netcup provider API
(`scripts/netcup/` keeps it and nothing else); shard ranges (P-fd-06/FD-043); fleetd docs and
facts (P-fd-07); thrashguard itself; agctl's snapshot contract.

**The three transports today.**

1. `remote-gate.sh` (310 lines; usage and exit codes `:1-20`). Resolves a commit locally, runs a
   provider identity check against the host (`:135`), then admits **locally** by weight in MiB
   into one of N flock slot files *on the controller* (`:137-210`) — the host knows nothing. The
   weight is the lane's declared `resources.memory` at that commit, else a measured peak table
   `+15%`, else a class default (2 slots for R2-shaped names, 1 otherwise) (`:144-180`). Then a
   throwaway detached worktree (`:232-240`), a runner snapshot taken from `origin/main`
   (`:242-257`), and `remote-gate.py run` (`:259-266`). Finally it collects artifacts, binds the
   verdict by counting `checkout .* at <sha>` lines in the job log (`:283-285`), reads the
   gate slice's `oom_kill` delta (`:218-222`, `:287`) and reports INCONCLUSIVE-OOM as exit 76
   unless run-gate itself said `BUDGET_EXCEEDED` (`:303-309`). The verdict is the last line of
   `lane.log` that **starts with** `run-gate: lane '<lane>' verdict ` (`:291-294`).
2. `remote-gate.py` (1374 lines) renders a bash job script (`JOB_TEMPLATE` `:560-790`), uploads
   it and starts it through `run-job` (`:1086`), then `status|logs|collect|cancel`
   (`:1279-1320`). The job script does the host-side work: PSI wait (`:647-652`), checkout from a
   thin bundle with release-tag replay (`:660-700`), **memory admission on `MemAvailable`**
   (`:720-741`), the lane in the cockpit container (`:745-757`), an artifacts tarball
   (`:758-762`), then `collect_stores` (`:765`) — after which the `finish` trap (`:633`) removes
   the throwaway checkout. Cancel is the TERM trap `:634`, which calls `cancel_lane` (`:605-607`)
   and `exit 143` **before** `collect_stores` runs at `:765`; the progress lived in that
   checkout. `docs/design/no-time-budgets/DESIGN.md` :330-345 (b) records exactly this loss.
3. `dev` (552 lines) runs a compile/test verb in a capped container on a gate host through
   `hx.sh`. Its admission is `host-admission/1`: memory PSI `full` avg10 `>= 3` waits, exit 75
   with nothing run, unreadable PSI refuses (`dev:260-275`); the contract is
   `scripts/netcup/runner/README.md` `:1053-1073`. That document states the limit in so many
   words: "remote-gate lanes … share `dev-gates.slice` without taking the ticket; they meet dev
   runs only in the PSI" (`:1072-1073`). **That sentence is the defect this package removes.**

**The measurement behind it.** 2026-11-26 03:59Z: r1002 and v1001 (4 cores) at load ~14, CPU
`some` ~90%; the controller host (8 cores) at load 3, CPU `some` 4% (operator, relayed in the
P-fd-05 input). Every caller picked hosts by hand and nothing read a host's capacity. fleetd
already computes a RAM-first host plan (`fleetd/src/fleetd/plan.py:478-585`) but has no way to
execute its own decision except the same `run-job` path.

**Two modes, one API shape** (operator 2026-11-26 00:2xZ). Both submit the identical job record
(§2) and read the identical status/fetch answer.

| mode | who picks the host | host access | admission |
|---|---|---|---|
| **ciu-direct** (v8 ciu/run-gate) | the caller | the caller's own keys | the host agent's queue if installed, else ciu's own |
| **fleetd-placed** | fleetd, from `host-facts/2` | fleetd's credentials, fleetd-owned hosts only | fleetd's cross-host queue, then the host agent |

Same in both: the record, the status answer, the exit codes, the progress stream. Programmatic
job construction in fleetd today goes through `JobSpec`/`RunnerJobs`
(`fleetd/src/fleetd/runner.py:30-60`); that module becomes the client of the agent.

**Controller host as a worker [N2].** The controller host is in fleetd's candidate set and its
local `run-gate` calls submit to the same local agent and admission book as remote placements.
The candidate transport is a local agent call over a Unix socket, with no SSH hop. The exact
transport and local credential mapping remain open in §5 and §8; until they are settled, the
controller-host worker is not enabled.

## 2. The job API

### 2.1 Version

`schema: "fleet-host-agent/1"` on every answer, plus `agent_version` (SemVer). A client
**refuses the host on a MAJOR mismatch** before any job work, naming both versions; a
MINOR/PATCH mismatch is accepted only when the requested verb and its fields appear in the
host's advertised `capabilities`. B4 already promises this and FD-039 records why the agent
must be a shipped module, not a string program (`fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:854-860`).
Rejected: "try it and see" — an unknown grammar on the far side of an ssh call produces a wrong
answer rather than no answer.

### 2.2 Verbs and the record

B4 names `probe|start|stop|fetch|facts`. The lifecycle is `submit|status|progress|cancel|fetch`
plus `probe|facts`. There are no `start` or `stop` aliases: the agent has not shipped them, so
aliases would protect no deployed client (`fleetd/src/fleetd/cli.py`, current `facts` command).

**submit** takes one record:

| field | type | meaning |
|---|---|---|
| `job_id` | string | group-prefixed idempotency key; the host rejects ids outside its grammar |
| `repo` | string | repo name (`REPO_NAME_RE`, `remote-gate.py:81`) |
| `sha`, `base` | full hex, hex or null | the commit to judge; the base (already resolved by the client) |
| `project`, `lane` | string | as `--project-dir`/lane today |
| `shard` | `{index,total}` or null | reserved for P-fd-06; `/1` refuses a non-null value |
| `resources.planned_cores` | positive number | derived from the lane declaration or measured job shape; no default |
| `resources.cpu_set` | CPU ids or null | exact host CPU set to use; null lets placement choose an allowed set |
| `resources.footprint` | `tiny|small|medium|large` | derived by the client from its measured peak table; §3.3 |
| `resources.declared_peak_bytes` | int or null | lane declaration; a hint, not a cgroup limit |
| `requested_by` | `{client, version, run}` | audit labels only; the host derives group and priority from the credential |
| `stores.collect`, `stores.seed` | paths; `{path,sha256,bytes}` | today's `--collect-store`/`--seed-store` (`remote-gate.py:1031-1032`) |
| `extra_argv`, `env` | list, map | argv and named profile values, each checked by the host against allow-lists |
| `dev` | object or null | required only for `kind: dev`: patch and untracked archive with hashes, verb argv, tty flag and worktree key |
| `kind` | `lane|dev` | selects the executor mode |

**Idempotence.** The client prefixes the id with its authenticated group. The digest covers
`repo`, `sha`, `base`, `project`, `lane`, `shard`, `kind`, all resource fields, store descriptors,
`extra_argv`, `env`, and `dev`; it excludes `job_id` and `requested_by.version`. The agent binds
the authenticated group separately. Same id and digest returns existing state; same id with a
different digest is refusal (2). This preserves today's run-dir and `run-job` duplicate refusals
(`remote-gate.py:1048-1050`; `run-job:14`).

**Several clients, several ciu roots.** The record names `repo` and `requested_by`, and the host
keeps `jobs/<job_id>/request.json`. Two clients may hold one repo in one checkout root (that is
what the bundle's have-list is for, §4.2); a ciu root that is not this repo is just another
`repo` name — the agent has no notion of "our monorepo". The only shared host namespaces are
`jobs/` (ids) and the admission queue (§3.4); nothing else is per-client.

**status** → `{schema, agent_version, job_id, state, started_utc, ended_utc, exit_code,
verdict, reason, progress:{done,total,unit,last_utc}, container:{id,name,image}, resources, host}`,
`state` ∈ `queued|running|collecting|done|failed|cancelled|lost`. `verdict` is run-gate's
anchored line parsed exactly as `remote-gate.sh:291-294` does (prefix at line start, never a
substring).

**progress** is read from run-gate's `.assay/progress-<lane>.jsonl` in the job checkout and
streamed by byte offset; the agent also records start, stage and end for `dev` jobs, which have
no candidate counts. Answers include `{done,total,unit,last_progress_utc}`. The status snapshot
uses `progress_unit` and `last_progress_utc` in `jobs[]` to match the agctl draft.

**cancel** takes `{job_id, reason}`. A queued job is atomically removed and needs no collector;
a running job follows §4.3. An unknown or finished id is a refusal (2).

**fetch** takes `{job_id, artifact, offset, length}` for the closed set `job.log`, `lane.log`,
`lane-detail.log`, `artifacts.tgz`, `assay-store.tgz`, `assay-store.tgz.sha256`, `result.json`, and answers a byte range
plus `{size, sha256, next_offset}`.

**Exit codes.** `0` PASS; `1` lane FAIL; `2` request, path, preflight or prerequisite refusal;
`3` identity or transport failure; `4` run-gate `BUDGET_EXCEEDED`; `70` unexpected runner
failure; `71` checkout failure; `72` missing base; `73` prerequisite missing (moved from today's
76); `75` queued job withdrawn before it started; `76` INCONCLUSIVE-OOM; `77` store collection
failed; `78` store seed failed; `143` cancelled. Store collection failure does not change the
lane verdict. The former preflight exit 4 maps to 2 so 4 has one meaning. OOM is attributed to
this job's containers, never a whole-slice counter.

**The event run-gate emits (its container id).** run-gate already writes the container it
started into `.run-gate/inflight/<lane>.json` (`run-gate-project/run_gate.py:7350-7399`) and its
id is readable with one inspect (`container_state`, `:7601-7665`; a name taken by another
container is detected, `:17980-17990`). P-fd-05 adds one machine-readable event on a separate
inherited file descriptor, before lane output begins; it is written outside the checkout and
cannot be forged by test output:

```
run-gate: event lane-started lane=<lane> container=<id> image=<image id> api=fleet-host-agent/1 job=<job_id>
```

The agent accepts the event only after `docker inspect` confirms the run-gate labels and this
job's checkout path component. The event identifies the container for thrashguard registration;
it is not a verdict. Rejected: parsing shared lane output or matching `docker ps` by name.

## 3. Placement and admission

### 3.1 The operator's rule, exactly

Per worker host `H`:
- **planned core use**: total planned use is at most `cores(H) + 1`;
- and bounded by `planning_cap(H)`, read from the host's worker slice `cpu.max` and capped at
  `cores(H)+1`. On the controller host this is 6 planned cores, the `dev-gates.slice` CPU limit
  (`operator-decisions.md`, 2026-11-26 05:39Z). The cap is a host fact, never a client number;
- if `resources.cpu_set` is present, every selected CPU must be online and allowed by that host's
  worker slice; the executor applies that set to the lane container. Otherwise the scheduler
  chooses from the host's allowed CPUs;
- several low-core jobs may exceed the core count in number: 5 jobs of 1 planned core on a 4-core
  host is legitimate *if their RAM fits*;
- **RAM is the scarce dimension**: plan each job's warm working set, not its hot set or peak RSS.
  The target is physical RAM minus the per-host `/etc` reserve for host services. Swap is never
  counted. Cold pages may be paged by design. A job over its estimate is not killed by a new cap;
  the live `memory full avg10 < 3` check remains the hard stop;
- **live admission check**, re-read at submit (never from a cache): `load1 <= cores + 1` and
  memory `full` avg10 `< 3`; `>= 3` is the hard stop whatever the plan says.

Rejected: main's 04:00Z "load > 2x cores" hold — the operator superseded it at 05:02Z; it holds
a spiky-but-idle host and says nothing about RAM, the scarce dimension.

### 3.2 `host-facts/2` — every field, its unit, its source, and when it is unavailable

Base: the `## core` answer of `.controller-state/platform/design/agctl-estate-snapshot-draft.md`.
**Rule for every field:** a missing value is `{"v": null, "reason": "<why>"}`; a placement that
needs a null value waits (75) or refuses, and never substitutes a default (`AGENTS.md`, "Defaults
and fallbacks are hazards"). The agent answers `/2` only; `fleet facts validate` also accepts
`/1` for one release. Rejected: emitting both versions from one agent — two truths on one host.

| field | unit | source on the host | when unavailable |
|---|---|---|---|
| `schema`, `agent_version` | literal, SemVer | agent | not enrolled → client refuses (major mismatch) |
| `boot_id` | string | `/proc/sys/kernel/random/boot_id` | — |
| `cpu.online` | int | `/sys/devices/system/cpu/online` (`facts.py:257`) | refuse (capacity unknown) |
| `cpu.planning_cap` | number | worker slice `cpu.max`, capped at `cpu.online + 1` | refuse; never default to `cores+1` |
| `load1`,`load5`,`load15` | number | `/proc/loadavg` | `null`+reason; the host is not placed (never "assume idle") |
| `memory.total_bytes`, `memory.available_bytes` | bytes | `/proc/meminfo` (`facts.py:267-290`) | `null`+reason; placement refuses |
| `memory.host_reserve_bytes` | bytes | `/etc/fleetd/facts.toml` | `null`+reason; placement refuses |
| `memory.swap_total_bytes`, `memory.swap_free_bytes` | bytes | `/proc/meminfo` | report only; never counted as capacity |
| `psi.{cpu,memory,io}.{some,full}_avg10_pct` | percent | `/proc/pressure/*` (`facts.py:306-324`) | `null`+reason; **unreadable is a refusal, never an admit** (`remote-gate.py:783-795`, outcome 2) |
| `psi.*.avg60`, `psi.*.avg300` | percent | kernel exposes them; host-facts/1 does not collect them | `not-collected`; /2 uses avg10 for admission |
| `gate_slice.memory_current_bytes`, `.memory_high_bytes`, `.memory_max_bytes`, `.cpu_max` | bytes, quota/period | cgroup v2 files for the configured worker slice | `null`+reason; refuse placement |
| `disks[]` | `{name,mount,total_bytes,free_bytes,fs,floor_bytes}` | `statvfs`; checkout and Docker roots plus per-host `/etc` floors | `null`+reason; refuse that host |
| `cgroup_v2.mounted`, `cgroup_v2.controllers` | bool, list | mountinfo | refuse (admission and the guard both need it) |
| `kernel_interfaces.*`, `ksm.*` | bools, ints | kept from /1 | `null`+reason, never a synthetic 0 |
| `control_plane.slice`, `.present`, `.cpu_weight`, `.memory_min_bytes`, `.service_memory_min` | as /1 (`contracts.py:464-506`) | `systemctl show`, `/etc/fleetd/facts.toml` | per-service defaults as /1 (`SPEC.md`:100-160); the 768 MiB profile constant only when no service fact exists |
| `guard.guard_present` | bool | config path present | `false` + reason `thrashguard-absent` (thrashguard DESIGN `:377-381`) |
| `guard.guard_state` | `off|report|enforce|unknown` | guard's runtime state file (`facts.py:517-522`) | omitted with reason; never `off` by default |
| `guard.config_digest` | sha256 hex | `/etc` bytes incl. drop-ins (`facts.py:440-514`) | `null`+reason |
| `guard.frozen_units` | int | **hostobs** freeze/thaw events (§6) | `null`+reason; `frozen = frozen_units > 0` is then `null`, not `false` |
| `guard.frozen_since_utc`, `frozen_by`, `freeze_reason`, `freeze.queued` | time, strings, int | hostobs events / guard journal | `not-collected` until thrashguard P4 |
| `policy.host_role`, `.psi_refuse_some_avg10`, `.max_lane_containers` | as /1 (`contracts.py:510-524`) | `/etc/fleetd/facts.toml` | role null → refuse (as `plan.py:480-483`) |
| `policy.planning_cap`, `.max_footprint`, `.max_planned_bytes`, `.host_reserve_bytes`, `.disk_floors` | number, class, bytes | `/etc/fleetd/facts.toml`; controller host sets `small` and about 3 GiB | null+reason; refuse placement |
| `policy.admission` | `{queue_path, wait_s, settle_s}` | host config | refuse; admission is not optional |
| `queue.planned_cores`, `.charged_bytes`, `.waiting` | number, bytes, count | host agent's admission book | `null`+reason |
| `jobs[]` | `{id,state,requested_by,resources,started_utc,progress_done,progress_total,progress_unit,last_progress_utc}` | `jobs/` dir | `null`+reason (today's `jobs_reason`, `facts.py:735-736`) |
| `tools[]` | as /1 | probes | `null`+reason |

**Changes this design asks of the draft's rows**, named for the agctl owner: the draft's
`queue.*` row ("slots stay `source: remote-gate`/`run-gate`") is replaced by the agent's queue;
`busy_pct`/`steal_pct`/`window_s` stay `not-collected` — load1/5/15 carry the decision (§3.1) and
a busy percentage is a second opinion nothing acts on; the draft's `jobs[].id` row ("the
producer's own id, opaque") is kept and **is** the §2.2 `job_id`, so the draft's "no join table
today" stays true: the one id *is* the join.

### 3.3 Footprint class and fit [Q2]

A class estimates the warm working set; it is not a peak-RSS limit. The client derives the class
from its local measured peak table (the host cannot read the controller's table,
`remote-gate.sh:143-180`):

| class | charge | typical |
|---|---:|---|
| `tiny` | 0.5 GiB | lint, a parse |
| `small` | 1.5 GiB | a pytest lane, a cmru build |
| `medium` | 4 GiB | a gate-full lane |
| `large` | 12 GiB | an R2 shard with a mutation judge |

These are starting estimates under Q2. Recalibrate them from measured warm-set rows. They never
set a container memory cap. Host policy may refuse any class above `policy.max_footprint`; the
controller host permits only `small` and at most about 3 GiB total planned RAM. A job never runs
alone by exceeding either cap.

For each admitted job `j`, let `charge_j` be its warm-set estimate and `current_j` its current
memory use in the worker slice. Admission requires both:

```
MemAvailable - host_reserve - sum(max(0, charge_j - current_j)) >= charge_new
gate_slice.memory_high - sum(charge_j) >= charge_new
```

The first line corrects for the part of each existing reservation not yet visible in
`MemAvailable`; actual use is already reflected there, so subtracting full charges double-counts
it. Swap is never added. The second line keeps the reservation book below the host-owned slice
`memory.high`; `memory.max` is an additional absolute refusal ceiling. There is no fleetd
overcommit factor in host admission. When actual use exceeds the estimate, `memory full avg10 >=
3` stops new admission, but does not kill a running job.

### 3.4 One admission mechanism on the host (B3), exit-75 parity

The agent keeps one host book for all local and direct callers. Fleetd keeps one fair global queue
and asks candidate hosts for immediate admission; a rejected host request leaves no host waiter.
The controller host joins the same book through its local agent [N2].

```
~/fleetd-admission/
  state.json     # admitted jobs: warm-set charges, planned cores, selected CPUs
  queue.lock     # flock: one admission decision at a time
  waiting/<job_id> # ciu-direct waiters; fleetd keeps its waiters in the global queue
```

- **Host decision** (inside `queue.lock`): reread `host-facts/2`, the gate-slice ceiling, disk
  floors and the active book. `ciu-direct` may wait on this host. `fleetd-placed` sends `wait: 0`:
  admit now or return 75 with the facts that refused it, then fleetd tries another host.
- **Fleetd placement.** A candidate must pass load, memory PSI, warm-set RAM, gate-slice, CPU-set
  and disk-floor checks. Among eligible hosts choose greatest planned-core headroom, then lowest
  memory-full avg10, then greatest disk headroom; ties use stable `host_id` order. The request's
  `resources.cpu_set` restricts the hosts/CPUs eligible to run it.
- **Queue order and reservation** (N3): credential policy assigns the class; a merge-unblocking
  gate leads, followed by `dev`, `remote-gate`/`fleetd`, then campaigns. Within a class use submit
  time and group-prefixed id. If the head job has waited beyond configured `aging_s` and cannot
  fit, reserve its required RAM and planned cores across candidate hosts. Backfill only jobs that
  fit without consuming that reservation. Once the head becomes feasible, it starts before any
  later arrival; a stream of small jobs cannot keep taking its reserved capacity.
- **Reservation parity.** With continuous small arrivals, a large head job starts in the first
  admission cycle after it becomes feasible; later jobs cannot consume its reserved RAM or cores.
- **Wait expiry.** A client timeout atomically withdraws a queued job under `queue.lock`. Exit 75
  is returned only after the host confirms it never started; if it started, the client follows
  that job instead. A network error is 3, never 75.
- **Heartbeat and reclaim.** A running job touches its entry every 30 s. A charge silent longer
  than `lost_after_s` (default 900) is reclaimed **only after** a liveness probe says the job is
  gone (container state `:7601`, the `EXIT` marker, the process table). Reclaiming a live job's
  charge would over-admit the host — the one direction a reclaim must never guess.
- **Relation to `run-job` and today's slots.** `run-job` (27 lines) keeps its job-dir and `EXIT`
  marker duty unchanged. `remote-gate.sh`'s controller-side slot files (`:137-210`) **go away**:
  they are a shadow admission that cannot see another host's load, and B3 puts the budget in the
  host's `/etc`. `dev`'s ticket becomes the same queue, retiring `README.md:1072-1073`'s "known
  limit": remote-gate lanes and dev runs can no longer meet each other only in the PSI.
  Rejected: keep local flocks *and* add host admission — two disagreeing mechanisms is FD-042 by
  construction.
- **Two schedulers on one host (FD-042).** Every installed runner client, including local
  controller-host `run-gate`, submits to the same agent book. A host without an installed runner
  remains ciu-direct's own-admission case (D12). A hand-run container outside the runner is not
  admitted by this contract.
- **OOM preference.** A test container must die before the cockpit does (operator 05:39Z). The
  cockpit already runs with a negative adjustment (`start-cockpit-runner.sh:30`,
  `COCKPIT_OOM_SCORE_ADJ` default `-500`; host-setup publishes `-900`,
  `mdt-cockpit/host-setup/README.md:1178`). run-gate is to set `--oom-score-adj 500` on its
  containers (RG-321 — **UNVERIFIED**: no `RG-321` and no `--oom-score-adj` appear in
  `run-gate-project/` on this tree). P-fd-05 states the obligation and asserts it from the
  agent's side: every lane container carries `oom_score_adj=500`, read back with
  `docker inspect`, and the agent refuses a job whose environment template cannot set it.

### 3.5 No new wall-clock budget

Nothing in §3 postpones or bounds a *run*: the admission wait bounds waiting; `lost_after_s` is
measured from a *dead* job's silence and confirmed by a liveness probe; `aging_s` only reorders a
queue. A running job is judged by progress — this package's progress stream plus run-gate's
existing stall watch (`run_gate.py` rev 34/38: `ProgressWatch`, `LogStreamWatch`). fleetd's
`LOST_AFTER_SECONDS = 30*60` (`campaign.py:28`) is a *host*-liveness threshold for the campaign
controller and must not migrate into the agent: the agent holds its job's process, so it needs no
clock to guess whether that job is alive.

## 4. Host executor and results

### 4.1 What ports, what is re-homed, what is deleted

**Ports unchanged in substance** from `JOB_TEMPLATE` (`remote-gate.py:560-790`): the PSI wait
(`:647-652`), the thin-bundle checkout with tag replay and `--base`/`origin/main` refs
(`:660-700`), the UID/prerequisite probes (`:653`), the `docker exec … nice -n 19` lane
invocation (`:745-757`), the artifacts tarball (`:758-762`) and the `pid`/`rc`/`EXIT` markers
(`:608-632`). **Re-homed**: memory admission to the agent's queue (§3.4); store collection to the
collector (§4.3). **Deleted**: the controller-side slots and the dry-run holder
(`remote-gate.sh:137-215`); the runner snapshot from `origin/main` (`:242-257`) — the agent *is*
the runner and `agent_version` says which; and `remote-gate.py`'s three-command ssh
choreography (`:1068-1086`, `:1096-1320`), which becomes the five agent verbs.

The job script becomes the agent's `executor` module: stdlib, shipped as source like
`fleetd/src/fleetd/hostside.py`, with FD-039's one-implementation-for-probe-and-fake rule
(`fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:854-875`). Each executor runs in its own transient systemd
scope, outside the agent service cgroup; restart or downgrade of the agent cannot kill it (§4.5).

### 4.2 Result transport

- **Bundle with a have-list**: the client sends the commits the host already holds
  (`build_bundle`, `remote-gate.py:436-490`; the job's `fetch` at `:662`), the agent answers with
  the minimal bundle, none when HEAD, the base and every tag are already reachable; the upload is
  checksum-verified (`:1068-1070`). Kept, not redesigned.
- **Byte-range fetch** for every artifact (§2.2), so a dropped connection costs one range, not the
  file. The client verifies the whole-file sha256 at `next_offset == size` and renames a mismatch
  to `.rejected` (today's rule, `remote-gate.py:1213-1219`).
- **Store collection**: keep the existing single `assay-store.tgz` plus checksum for all rels
  (`remote-gate.py:470-503`, `:1193-1222`); `fetch` accepts only that closed name.
- **Nothing result-shaped travels in a log line**: the one machine-readable line is §2.2's event,
  plus `result.json` as an artifact.

### 4.3 Stop, collect, then teardown (D7)

On normal completion, lane failure, cancel or TERM: deregister the container from thrashguard,
request lane termination when it has not exited, and wait for the lane process/container to stop.
Only then run `collect_once`, verify the archive, remove this job's containers by labels, remove
the checkout, and release its admission charge. Collection is idempotent and store errors do not
rewrite the lane verdict.

If termination fails or the lane remains alive, do not collect from its changing store and do
not tear anything down. Keep the checkout, containers and admission charge, report `stopping`,
and let the executor/reaper retry. When the lane exits, collect every flushed record then. There
is no wall-clock limit on this wait: stopping a run never silently discards its last writes.

### 4.4 Identity binding of a verdict to a commit

Four facts, all host-written, none client-asserted: (1) `sha` in `request.json`; (2) the
`checkout <dir> at <sha>` line written after a `rev-parse HEAD` equality (`remote-gate.py:682-683`);
(3) run-gate's anchored verdict line; (4) the `lane-started` event's container id, which
`docker inspect` binds to an image id. The client reports the binding as today
(`remote-gate.sh:283-285`) and **refuses to call it bound** when any of the four is missing — a
missing binding is exit 3, not a pass. Rejected: trusting the client's `--commit` echo — that is
the name-for-object anti-pattern `AGENTS.md` names.

### 4.5 Host loss, agent restart and reboot

- **Controller lost, host alive:** the separate executor scope and its job id remain live; the
  client reattaches by `job_id`. `run-gate` already writes an in-flight container record
  (`run_gate.py:7350-7399`), but the agent owns the job lifecycle.
- **Agent restart or downgrade:** the executor is outside the agent cgroup and continues. On
  startup the reaper scans every job without an `EXIT` marker and reconnects to its transient
  scope. It does not signal or restart a live executor.
- **Dead executor, lane still alive:** the reaper finds containers by this job's labels, stops
  the lane, waits for exit, collects from the surviving checkout, then removes those containers
  and checkout. An inspect error other than Docker's explicit "no such object" leaves the job
  and charge untouched (`run_gate.py:7601-7645`).
- **Host reboot:** `boot_id` changed, so no old pid or executor is trusted. Before marking the job
  `lost`, the reaper checks job-labeled containers; if one is running, it stops and waits for it.
  It then collects the surviving checkout and artifacts, removes only that job's labeled
  containers, and records `lost`. If stop or collection fails, it keeps the checkout and reports
  the failure for retry; it does not claim the progress was collected.
- **Host unreachable:** `3`; fleetd pauses that host and never reads unreachable as gone
  (`remote-gate.sh:17-20`; `campaign.py:931-989`).

### 4.6 R2 store carry (RG-298)

The carry is §2.2's `stores.collect`/`stores.seed` pair with one added rule: a seed travels with
its own sha256 and size, is unpacked only into the checkout this `job_id` created, must contain no
tracked files and no members outside its target (today's `seed_stores` checks,
`remote-gate.py:530-560`), and a failed seed is exit `78` with `STORE-SEED-*` in the job log —
today's behaviour verbatim. The carry's *format* belongs to assay's `assay-merge-manifest/1`
(B544/B565); the runner carries bytes and never interprets them, which is RG-298's whole scope
(`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md:9647-9652`).

## 5. Security

- **Credentials cover fleetd-owned hosts only** (operator: "host credentials in fleetd applies to
  fleetd-owned hosts only"). ciu-direct keeps its own keys and shares only the agent's queue;
  fleetd never sees ciu's credentials and ciu never sees fleetd's.
- **Identity.** For cross-host sessions, check the provider identity before connecting, require
  the pinned host key, then compare the agent's `probe` `host_id` and `boot_id` with the host
  table. On the controller host the candidate is a local agent call over a Unix socket, no SSH;
  its transport and peer-credential mapping are OPEN [N2].
- **Forced command (FD-009).** Each authorized key is bound on the host to a group and priority
  class by its forced command (`fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:895-905`; enrollment belongs
  to CIU-218). The agent derives identity and priority from that key, never `requested_by` or
  another client field. Requests are JSON; the agent validates schema, repo membership, project
  paths, store paths, resource values and every argv on the host. `project` and store paths must
  be relative and resolve inside the job checkout. `extra_argv` uses a server-side allow-list of
  run-gate options; `env` uses the host's per-repo profile table (RG-319). `fetch` accepts only
  §2.2's closed artifact names. No verb runs a client shell string. The forced command narrows
  SSH access; it does not sandbox the job.
- **Trust boundary.** Submit runs arbitrary code from the named commit beside the host Docker
  socket. A key that can submit is root-equivalent on that host; the forced command is not a
  containment boundary.
- **Scope and records.** Read only the authenticated group's facts and jobs; submit, cancel and
  fetch only its own jobs and allowed repos. The client-supplied `requested_by` is audit text,
  not authorization. Records and logs contain no secret, key path, host address or token; job
  directories use `umask 077` (`run-job:4`).

## 6. thrashguard and hostobs

The channel is the one thrashguard's design already fixed: thrashguard emits
`thrashguard.freeze`/`thrashguard.thaw` into hostobs (`docs/design/thrashguard/DESIGN.md`
`:206-230`, `:396-404`), and fleetd reads freeze state **through hostobs**, never through
thrashguard's socket (`:280-286`). This package adds nothing to that path; it adds two consumers:

1. **The agent reports freeze state** in `host-facts/2` as §3.2's four names (`guard_present`,
   `guard_state`, `config_digest`, `frozen_units`), `frozen_units` from hostobs' event store and
   `guard_state` from the guard's runtime file (`facts.py:440-522`). `frozen_since/by/reason/
   queued` stay `not-collected` until P4.
2. **The agent registers each started job** with thrashguard's registrant socket when present: one
   `register {unit: <job_id>, container: <id from §2.2's event>, score, priority,
   owner: "fleetd", run: <job_id>}`, `heartbeat` on its tick, `deregister` **before** the container
   is removed — the contract of thrashguard DESIGN `:357-381` §4e, with the caller now the agent
   instead of run-gate (fleetd later replaces only the caller, not the contract).

**When either is absent.** No thrashguard: `guard_present: false`, `guard_state` omitted with
reason `thrashguard-absent`, `frozen_units` null with reason, and `submit` unchanged — nothing
downgraded, no verdict, admission or cancel change. No hostobs: the same nulls with reason
`hostobs-absent`; the agent does not measure freeze itself. No fleetd (standalone): the agent
works, deeper placement intelligence does not exist, and ciu-direct is the only mode. A placement
never reads an unknown freeze state as "not frozen": the host is placed on by §3.1's live facts and
the unknown is reported to the client.

## 7. Migration, packages, parity

**Client order (B4), each parity-gated before the next starts.** (1) `remote-gate.py`; then
`remote-gate.sh` drops its local worktree and `CONTROLLER_REMOTE_GATE_WORKROOT` (CTL-48). (2) `dev`
as a Python port with the same CLI (B9's ~250 bash lines). (3) fleetd campaigns, with no
pre-synced checkout and facts from the agent's `facts`. (4) the T6 dr runner. ciu-direct (v8)
reuses the record shape and the queue but keeps its own host access; it waits on none of the four.

**Parity tests — the behaviours that must match, named.** (a) the exit-code table of §2.2 against
`remote-gate.sh:17-20`, including 75-because-only-queued and 76-on-OOM-delta, and
`BUDGET_EXCEEDED` never becoming 76; (b) verdict parsing: the last line **starting with** the
prefix, a substring never accepted (`scripts/netcup/tests/test_remote_gate.py:300`); (c) cancel
loses nothing: a cancelled job yields the same `store-<rel>.tgz` bytes a completed one would;
(d) unreadable PSI/meminfo refuses and never admits (`test_remote_gate_memory.py:91`); (e) a job
bigger than the host runs alone (`:71`); (f) idempotent `job_id` (same digest no-op, different
digest refusal); (g) the `lane-started` event names a container that `docker inspect` confirms;
(h) `oom_score_adj=500` read back; (i) tag replay (`test_remote_gate.py:227`) and the
`checkout … at <sha>` binding (`:322`); (j) refusal on an `agent_version` major mismatch, before
any job work. One test per row, each able to go red — a parity test asserting a string both sides
share proves construction, not parity.

**Rollback.** One flag per client, `REMOTE_GATE_TRANSPORT=legacy|agent` and
`AGENT_DEV_TRANSPORT=legacy|agent`, read by the client only; the host has no dual mode. Deleting
the flag is a later change with its own gate. Agent rollback is a wheel downgrade inside its
major, which is why a MAJOR mismatch refuses and a minor bump must be capability-additive.

**Package split, size and gate.**

| package | size | gate |
|---|---|---|
| A. `host-facts/2` + validator + agent `facts`/`probe` | M | fleetd selftest + contract fixtures; no host work |
| B. agent install (forced command, unit, `executor`) + `~/fleetd-admission` queue + registration | M-L | gate-full on the fleetd lane + one live `probe` on a gate host |
| C. client verbs, byte-range fetch, bundle have-list | M | parity rows (a)-(j) minus the host-only ones |
| D. `remote-gate.py` migration, `remote-gate.sh` slot removal (CTL-48) | M | remote-gate's tests + one live lane; workroot removal |
| E. `dev` port | S-M | `dev`'s tests + one `dev test` on a gate host |
| F. campaigns onto the agent, r2-store carry (RG-298) | S-M | fleetd campaign selftest + one live campaign batch |

Each package carries its own lane budget, and no package may add a wall budget to a run (D6).

## 8. Open operator questions, each with a default

| # | question | default taken here |
|---|---|---|
| Q1 | `planning_cap`: 6 on the controller host (the slice) and `cores+1` on hosts without a slice? | yes, both from the host's `/etc` — never a client number |
| Q2 | Are §3.3's four footprint charges right? | keep them; recalibrate from the measured peak table at 20+ measured rows |
| Q3 | Does `dev` keep its 900 s `--wait` default as the 75 bound? | yes, unchanged; the agent never bounds a run |
| Q4 | Is `shard` in the record now, or added at `/2`? | present as null/omitted; a non-null value is refused at `/1` |
| Q5 | Do `start`/`stop` survive as aliases? | one release, then gone, recorded in `fleetd/CHANGES.md` |
| Q6 | May ciu-direct place on a host fleetd does not own, using the agent's queue only? | yes — the queue is host-local, the credentials stay the caller's |
