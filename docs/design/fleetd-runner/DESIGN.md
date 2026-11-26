<!-- status: design; scope: fleetd + remote-gate + dev + campaigns; package P-fd-05 (CTL-51); input 2026-11-25/26 -->
# Design: one shared host runner and fleetd placement (P-fd-05 = CTL-51)

## 0. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | One API (`fleet-host-agent/1`); modes differ only in who picks the host | callers share lifecycle and result semantics |
| D2 | Placement reads host agent `host-facts/2`, never `docker stats` | host facts expose the admitted capacity |
| D3 | Planned cores may reach `cores + 1`, capped by host `planning_cap` | leave room for IO overlap within the worker slice |
| D4 | Start warm-set charges at 0.5/1.5/4/12 GiB; exclude swap; live memory PSI is a hard stop | warm working set predicts RAM; PSI catches actual pressure |
| D5 | One host admission book for every caller; waiting returns 75 | the host sees every job that consumes its capacity |
| D6 | Progress decides a stall; no run wall-clock budget | duration alone does not show lack of progress |
| D7 | Stop the lane before collecting; retain checkout and charge if stop fails | collection must include all writes flushed before exit |
| D8 | Fleetd credentials cover only fleetd-owned hosts; records carry no secret | ciu keeps its own access in ciu-direct mode |
| D9 | Controller-host planned total is at most 3 GB; max class is `small`, set in `/etc` | a host-owned cap protects the constrained gate slice |
| D10 | One fair fleetd queue; merge-unblocking gates lead; aged jobs reserve capacity | prevent avoidable merge delay and large-job starvation |
| D11 | A known freeze blocks new jobs; running jobs remain untouched | avoid adding work while thrashguard holds units |
| D12 | ciu uses an installed runner queue; otherwise it keeps its own admission | avoid two schedulers on an enrolled host |
| D13 | Remove dev's `max_s`; use progress to judge a run | fixed duration conflicts with the no-time-budget rule |
| D14 | Controller jobs use host fleetd over a mounted local socket; peer creds map one shared devcontainer principal | host cgroup visibility and queue survival |

## 1. Scope, non-goals, current state

**Scope.** Job API/host agent, fleetd placement, remote-gate executor/result transport, and four client migrations. Closes
CTL-51, CTL-48, FD-042, FD-003 and FD-004. **Non-goals:** netcup API, shard ranges (P-fd-06), fleetd docs/facts (P-fd-07),
thrashguard itself, agctl's snapshot contract.

**The three transports today.**

1. `scripts/controller/remote-gate.sh` checks identity and admits by MiB into controller flock slots
   (`scripts/controller/remote-gate.sh:109-180`), then makes a worktree/snapshot, runs `remote-gate.py` and collects
   results plus whole-slice OOM delta (`scripts/controller/remote-gate.sh:232-309`).
2. `scripts/netcup/runner/remote-gate.py` renders/uploads `JOB_TEMPLATE`, starts `run-job`, exposes lifecycle verbs
   (`scripts/netcup/runner/remote-gate.py:560-790,1086,1279-1320`), waits on PSI, checks out a bundle, admits from
   `MemAvailable`, runs in the cockpit and collects (`scripts/netcup/runner/remote-gate.py:647-765`). TERM exits before
   collection and `finish` removes the checkout (`scripts/netcup/runner/remote-gate.py:605-634,765`; `docs/design/no-time-budgets/DESIGN.md:330-345`).
3. `dev` runs capped compile/test containers through `hx.sh` (`scripts/controller/hx.sh:1-18`); host-admission/1 waits
   when memory full avg10 is `>= 3` and returns 75 (`scripts/controller/dev:256-280`). Remote-gate shares `dev-gates.slice`
   without taking the ticket (`scripts/netcup/runner/README.md:1053-1073`); this design closes that gap.

**The measurement behind it.** At 03:59Z, two 4-core gate hosts had load ~14/CPU some ~90%; the 8-core controller host had
load 3/CPU some 4% (`.controller-state/core/notes/P-fd-05-refresh-input.md:17-28`). Calls chose hosts by hand; fleetd
already plans RAM-first (`.controller-state/operator-decisions.md:668-671`; `fleetd/src/fleetd/plan.py:478-585`).

**Two modes, one API shape** (operator 2026-11-26 00:2xZ). Both submit the identical job record
(§2) and read the identical status/fetch answer.

| mode | who picks the host | host access | admission |
|---|---|---|---|
| **ciu-direct** (v8 ciu/run-gate) | the caller | the caller's own keys | the host agent's queue if installed, else ciu's own |
| **fleetd-placed** | fleetd, from `host-facts/2` | fleetd's credentials, fleetd-owned hosts only | fleetd's cross-host queue, then the host agent |

Same in both: the record, the status answer, the exit codes, the progress stream. Programmatic
job construction in fleetd today goes through `JobSpec`/`RunnerJobs`
(`fleetd/src/fleetd/runner.py:30-60`); that module becomes the client of the agent.

**Controller host as a worker (D14).** Fleetd runs there as a host service, outside the devcontainer. Local `run-gate`
uses its Unix socket and the same admission book as fleetd-placed work. The agent sees `dev-gates.slice` and host
pressure; the devcontainer's private cgroup view cannot. Its host-owned queue and executor survive devcontainer restart.

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

B4 names `probe|start|stop|fetch|facts`; v1 uses `probe|facts|have|put|submit|status|progress|cancel|fetch`.
No start/stop aliases: the current CLI has a facts collect/validate group, while host-agent verbs
remain P-fd-05 (`fleetd/src/fleetd/cli.py:396-440`; `fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:854-857`).

**submit** takes one record:

| field | type | meaning |
|---|---|---|
| `job_id` | string | group-prefixed idempotency key; the host rejects ids outside its grammar |
| `repo` | string | repo name (`REPO_NAME_RE`, `scripts/netcup/runner/remote-gate.py:81`) |
| `sha`, `base` | full hex, hex or null | the commit to judge; the base (already resolved by the client) |
| `project`, `lane` | string | as `--project-dir`/lane today |
| `shard` | `{index,total}` or null | reserved for P-fd-06; `/1` refuses a non-null value |
| `resources.planned_cores` | positive number | derived from the lane declaration or measured job shape; no default |
| `resources.cpu_set` | CPU ids or null | exact host CPU set to use; null lets placement choose an allowed set |
| `resources.footprint` | `tiny|small|medium|large` | derived by the client from its warm-set table; §3.3 |
| `resources.declared_peak_bytes` | int or null | lane declaration; a hint, not a cgroup limit |
| `requested_by` | `{client, version, run}` | audit labels only; the host derives group and priority from the credential |
| `stores.collect`, `stores.seed` | paths; `{path,sha256,bytes}` | today's options (`scripts/netcup/runner/remote-gate.py:1031-1032`) |
| `extra_argv`, `env` | list, map | argv and named profile values, each checked by the host against allow-lists |
| `dev` | object or null | tracked patch, untracked archive (each hash and size), verb argv, tty flag and worktree key; only for `kind: dev` |
| `kind` | `lane|dev` | selects the executor mode |

**Idempotence.** The client prefixes the id with its authenticated group. The digest covers
`repo`, `sha`, `base`, `project`, `lane`, `shard`, `kind`, all resource fields, store descriptors,
`extra_argv`, `env`, `dev`, and `requested_by.client`/`.run`; it excludes `job_id` and
`requested_by.version`. The agent binds
the authenticated group separately. Same id and digest returns existing state; same id with a
different digest is refusal (2). This preserves today's run-dir and `run-job` duplicate refusals
(`scripts/netcup/runner/remote-gate.py:1048-1050`; `scripts/netcup/runner/run-job:14`).

For `kind: dev`, the host derives a persistent workspace from authenticated group, repo and
`worktree_key`, and takes one lock per key. The patches restore the dirty tracked and untracked
files; the key is not a path. The host runs only the validated verb argv. `tty` selects a pty.
The payload has no `max_s`, removing today's timeout wrapper (`scripts/controller/dev:159`); `dev stop` remains available (`scripts/controller/dev:550`).

**have** returns the refs the host already has. **put** takes `{name, sha256, bytes}` plus a
streamed body; only `in.bundle`, indexed seed archives and the two dev patch archives are valid
names. The agent checks length and checksum and creates the destination itself.

Local framing: one request per connection, 4-byte big-endian byte length + UTF-8 JSON object, max 64 KiB.
For `put`, exactly `bytes` follows, streamed and capped by required `policy.max_upload_bytes`; client half-closes write
after the full request and server rejects truncation/extra input. `fetch` ranges are at most 8 MiB; responses use same frame + body.

**Several clients, several ciu roots.** The record names `repo` and `requested_by`, and the host
keeps `jobs/<job_id>/request.json`. Two clients may hold one repo in one checkout root (that is
what the bundle's have-list is for, §4.2); a ciu root that is not this repo is just another
`repo` name — the agent has no notion of "our monorepo". The only shared host namespaces are
`jobs/` (ids) and the admission queue (§3.4); nothing else is per-client.

**status** → `{schema, agent_version, job_id, state, started_utc, ended_utc, exit_code,
verdict, reason, progress:{done,total,unit,last_utc}, container:{id,name,image}, resources, host}`,
`state` ∈ `queued|running|collecting|done|failed|cancelled|lost`. `verdict` is run-gate's
anchored line parsed exactly as `scripts/controller/remote-gate.sh:291-294` does (prefix at line start, never a
substring).

**progress** for assay lanes is read from `.assay/progress-<lane>.jsonl` in the job checkout and
streamed by byte offset (`run-gate-project/SPEC.md:2321-2342`). Command lanes have no candidates
(`run-gate-project/SPEC.md:416-436`): `done` and `total` are null, with
`unit: log_bytes` from the agent's `lane.log` offset. The executor records start, stage and end
for `dev`. Answers include `{done,total,unit,last_progress_utc}`; `jobs[]` uses
`progress_unit` and `last_progress_utc` to match the draft (`.controller-state/platform/design/agctl-estate-snapshot-draft.md:44-45`).

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
this job's containers, never a whole-slice counter. Record preflight `4`→`2` and prerequisite
`76`→`73` in `fleetd/CHANGES.md`.

**The event run-gate emits (its container id).** run-gate already writes the container it
started into `.run-gate/inflight/<lane>.json` (`run-gate-project/run_gate.py:7350-7399`) and its
id is readable with one inspect (`run-gate-project/run_gate.py:7601-7665`; a name taken by another
container is detected at `run-gate-project/run_gate.py:17980-17990`). P-fd-05 adds one machine-readable event on a separate
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
  (`.controller-state/operator-decisions.md:680-684`). The cap is a host fact, never a client number;
- if `resources.cpu_set` is present, every selected CPU must be online and allowed by that host's
  worker slice; the executor applies that set to the lane container. Otherwise the scheduler
  chooses from the host's allowed CPUs;
- several low-core jobs may exceed the core count in number: 5 jobs of 1 planned core on a 4-core
  host is legitimate *if their RAM fits*;
- **RAM is the scarce dimension**: plan each job's warm working set, not its hot set or peak RSS.
  Target physical RAM minus the per-host `/etc` reserve; reserve 0.5–1 GB for services (about 7 GB
  plannable on an 8 GB host). Swap is never counted; cold pages may page. An estimate is not a cap:
  live `memory full avg10 < 3` remains the hard stop (`.controller-state/operator-decisions.md:689-694`);
- **live admission check**, re-read at submit (never from a cache): `load1 <= cores + 1` and
  memory `full` avg10 `< 3`; `>= 3` is the hard stop whatever the plan says.

Rejected: main's 04:00Z "load > 2x cores" hold — the operator superseded it at 05:02Z; it holds
a spiky-but-idle host and says nothing about RAM, the scarce dimension.

### 3.2 `host-facts/2` — every field, its unit, its source, and when it is unavailable

Base: the `## core` answer of `.controller-state/platform/design/agctl-estate-snapshot-draft.md:35-51`.
**Rule for every field:** a missing value is `{"v": null, "reason": "<why>"}`; a placement that
needs a null value waits (75) or refuses, and never substitutes a default (`AGENTS.md`, "Defaults
and fallbacks are hazards"). The agent answers `/2` only; `fleet facts validate` also accepts
`/1` for one release. Rejected: emitting both versions from one agent — two truths on one host.

| field | unit | source on the host | when unavailable |
|---|---|---|---|
| `schema`, `agent_version` | literal, SemVer | agent | not enrolled → client refuses (major mismatch) |
| `boot_id` | string | `/proc/sys/kernel/random/boot_id` | — |
| `cpu.online` | int | `/sys/devices/system/cpu/online` (`fleetd/src/fleetd/facts.py:257`) | refuse (capacity unknown) |
| `cpu.planning_cap` | number | worker slice `cpu.max`, capped at `cpu.online + 1` | refuse; never default to `cores+1` |
| `load1`,`load5`,`load15` | number | `/proc/loadavg` | `null`+reason; the host is not placed (never "assume idle") |
| `memory.total_bytes`, `memory.available_bytes` | bytes | `/proc/meminfo` (`fleetd/src/fleetd/facts.py:267-290`) | `null`+reason; placement refuses |
| `memory.host_reserve_bytes` | bytes | `/etc/fleetd/facts.toml` | `null`+reason; placement refuses |
| `memory.swap_total_bytes`, `memory.swap_free_bytes` | bytes | `/proc/meminfo` | report only; never counted as capacity |
| `psi.{cpu,memory,io}.{some,full}_avg10_pct` | percent | `/proc/pressure/*` (`fleetd/src/fleetd/facts.py:306-324`) | `null`+reason; planner refuses unknown (`fleetd/src/fleetd/plan.py:485-490`), remote-gate refuses unreadable (`scripts/netcup/runner/remote-gate.py:248-255`) |
| `psi.*.avg60`, `psi.*.avg300` | percent | host-facts/1 reads avg10 only (`fleetd/src/fleetd/facts.py:306-324`) | `not-collected`; /2 uses avg10 for admission |
| `gate_slice.memory_current_bytes`, `.memory_high_bytes`, `.memory_max_bytes`, `.cpu_max` | bytes, quota/period | cgroup v2 files for the configured worker slice | `null`+reason; refuse placement |
| `disks[]` | `{name,mount,total_bytes,free_bytes,fs,floor_bytes}` | `statvfs`; checkout and Docker roots plus per-host `/etc` floors | `null`+reason; refuse that host |
| `cgroup_v2.mounted`, `cgroup_v2.controllers` | bool, list | mountinfo | refuse (admission and the guard both need it) |
| `kernel_interfaces.*`, `ksm.*` | bools, ints | kept from /1 | `null`+reason, never a synthetic 0 |
| `control_plane.slice`, `.present`, `.cpu_weight`, `.memory_min_bytes`, `.service_memory_min` | as /1 (`fleetd/src/fleetd/contracts.py:464-506`) | `systemctl show`, `/etc/fleetd/facts.toml` | defaults (`fleetd/docs/SPEC.md:100-160`); 768 MiB profile only with no service fact |
| `guard.guard_present` | bool | config path present | `false` + reason `thrashguard-absent` (`docs/design/thrashguard/DESIGN.md:375-381`) |
| `guard.guard_state` | `off|report|enforce|unknown` | guard runtime file (`fleetd/src/fleetd/facts.py:517-522`) | omitted with reason; never `off` by default |
| `guard.config_digest` | sha256 hex | `/etc` bytes incl. drop-ins (`fleetd/src/fleetd/facts.py:440-514`) | `null`+reason |
| `guard.frozen_units` | int | **hostobs** freeze/thaw events (§6) | `null`+reason; `frozen = frozen_units > 0` is then `null`, not `false` |
| `freeze.since_utc`, `freeze.by`, `freeze_reason` | time, strings | hostobs events | `not-collected` until thrashguard P4 |
| `freeze.queued` | int | none (agctl draft has no source, `agctl-estate-snapshot-draft.md:38-39`) | `not-collected` |
| `policy.host_role`, `.psi_refuse_some_avg10`, `.max_lane_containers` | as /1 (`fleetd/src/fleetd/contracts.py:510-524`) | `/etc/fleetd/facts.toml` | role null → refuse (`fleetd/src/fleetd/plan.py:480-483`) |
| `policy.planning_cap`, `.max_footprint`, `.max_planned_bytes`, `.host_reserve_bytes`, `.disk_floors` | number, class, bytes | `/etc/fleetd/facts.toml`; controller sets `small` and ~3 GB | null+reason; refuse placement |
| `policy.max_upload_bytes` | bytes | required `/etc/fleetd/facts.toml` | null+reason; refuse `put` |
| `policy.admission` | `{queue_path, wait_s, settle_s}` | host config | refuse; admission is not optional |
| `queue.planned_cores`, `.charged_bytes`, `.waiting` | number, bytes, count | host agent's admission book | `null`+reason |
| `jobs[]` | `{id,state,requested_by,resources,started_utc,progress_done,progress_total,progress_unit,last_progress_utc}` | `jobs/` dir | `null`+reason (today's `jobs_reason`, `fleetd/src/fleetd/facts.py:735-736`) |
| `tools[]` | as /1 | probes | `null`+reason |

**Changes this design asks of the draft's rows** (`.controller-state/platform/design/agctl-estate-snapshot-draft.md`), named for the agctl owner: the draft's
`queue.*` row ("slots stay `source: remote-gate`/`run-gate`") is replaced by the agent's queue;
`busy_pct`/`steal_pct`/`window_s` stay `not-collected` — load1/5/15 carry the decision (§3.1) and
a busy percentage is a second opinion nothing acts on; the draft's `jobs[].id` row ("the
producer's own id, opaque") is kept and **is** the §2.2 `job_id`, so the draft's "no join table
today" stays true: the one id *is* the join (`.controller-state/platform/design/agctl-estate-snapshot-draft.md:41-45`).

### 3.3 Footprint class and fit

A class estimates the warm working set; it is not a peak-RSS limit. The client derives it from a local warm-set table.
Today `scripts/controller/remote-gate.sh` reads a controller-owned peak table (`scripts/controller/remote-gate.sh:143-180`); migration replaces it.

| class | charge | typical |
|---|---:|---|
| `tiny` | 0.5 GiB | lint, a parse |
| `small` | 1.5 GiB | a pytest lane, a cmru build |
| `medium` | 4 GiB | a gate-full lane |
| `large` | 12 GiB | an R2 shard with a mutation judge |

After startup, sample cgroup `memory.current` at each progress event (for command lanes, when the log-byte offset advances).
Use the 90th percentile for that lane/version and assign the smallest class at least that large. If it exceeds `large`,
refuse until policy adds a class. With no measurement, the caller supplies an explicit class; the agent invents no
default. Estimates are not container caps. Host policy refuses above `policy.max_footprint`; the controller permits only
`small` and caps planned totals at about 3 GB through `/etc/fleetd/facts.toml` (D9).

For each admitted job `j`, let `charge_j` be its warm-set estimate, `current_j` its cgroup use, and
`reserved = sum(max(0, charge_j - current_j))`. One fit value decides admission:

```
fit_bytes = min(
  memory.total_bytes - host_reserve - sum(charge_j),
  min(gate_slice.memory_high, gate_slice.memory_max) - gate_slice.memory_current - reserved,
  policy.max_planned_bytes - sum(charge_j)
)
admit iff fit_bytes >= charge_new
```

The first term plans warm sets against physical RAM minus the host-services reserve. The slice term subtracts observed
use plus only unconsumed job reservations, so actual use is not counted twice and a job above estimate still consumes
its real slice budget. `max_planned_bytes` is the per-host `/etc` cap. Swap is never added, and fleetd's campaign
overcommit factor (`fleetd/src/fleetd/plan.py:563-585`) is not used. If a job exceeds its estimate, `memory full
avg10 >= 3` stops new admission but never kills a running job.

### 3.4 One admission mechanism on the host (B3), exit-75 parity

The agent keeps one host book for all local and direct callers. Fleetd keeps one fair global queue
and asks candidate hosts for immediate admission; a rejected host request leaves no host waiter.
The controller host joins the same book through its host agent (D14); its state survives devcontainer restart.

```
/var/lib/fleetd-agent/       # systemd StateDirectory; root:root 0700, survives agent/devcontainer restart
  admission/
    admitted/<job_id>.json   # charge and CPUs; executor holds flock while alive
    queue.lock              # one admission decision at a time
    waiting/<job_id>        # ciu-direct waiters; fleetd waits in its global queue
```

- **Host decision** (inside `queue.lock`): reread `host-facts/2`, the gate-slice ceiling, disk
  floors and the active book. `ciu-direct` may wait on this host. `fleetd-placed` sends `wait: 0`:
  admit now or return 75 with the facts that refused it, then fleetd tries another host.
- **Disk floor.** Below either configured checkout-root or Docker-root free-space floor, ciu-direct
  waits in its host queue and fleetd gets an immediate 75 to try another host.
- **Fleetd placement.** A candidate must pass load, memory PSI, warm-set RAM, gate-slice, CPU-set
  and disk-floor checks. Choose greatest remaining warm-set RAM fit, then planned-core headroom,
  lowest memory-full avg10, greatest disk headroom and stable `host_id`. `cpu_set` restricts eligible
  hosts and the executor applies it.
- **Queue order and reservation** (N3): the authenticated key sets the class; merge-unblocking
  gates lead, then `dev`, `remote-gate`/`fleetd`, and campaigns. Within a class use submit time and
  group-prefixed id. When an aged head cannot fit, reserve its RAM and cores on its best statically
  eligible host using the placement ranking but ignoring active jobs. Backfill there only below that reservation; higher-priority merge gates may pass.
  Recompute the target when facts or eligibility change. The head starts before later arrivals as
  soon as it fits, so small jobs cannot consume its reserved capacity.
- **Reservation parity.** With continuous small arrivals, a large head job starts in the first
  admission cycle after it becomes feasible; later jobs cannot consume its reserved RAM or cores.
- **Wait expiry.** On timeout, withdraw a fleetd waiter from its global queue or a ciu-direct waiter under `queue.lock`.
  The agent confirms a submitted job never started before returning 75; if started, the client follows it. Network error is 3.
- **Charge lifetime.** The executor holds the per-job flock for the whole job. Kernel process
  death releases it; the reaper still stops and collects any surviving lane before deleting the
  record. No heartbeat, `lost_after_s` or probe-before-reclaim is needed.
- **Relation to `run-job` and today's slots.** Keep the legacy controller slots, workroot and
  runner snapshot while any client uses the legacy path. The agent charges every legacy host job
  without `EXIT`; a later flag-removal change deletes those old mechanisms. `dev` also moves into
  this host book, so its runs and remote-gate lanes no longer meet only in PSI
  (`scripts/netcup/runner/README.md:1072-1073`; `scripts/netcup/runner/run-job:13-26`).
  Rejected: keep local flocks *and* add host admission — two disagreeing mechanisms is FD-042 by
  construction.
- **Two schedulers on one host (FD-042).** Every installed runner client, including local
  controller-host `run-gate`, submits to the same agent book. A host without an installed runner
  remains ciu-direct's own-admission case (D12). A hand-run container outside the runner is not
  admitted by this contract.
- **OOM attribution and protection.** Use this job's `State.OOMKilled` or `memory.events`, never the shared slice counter
  (`scripts/controller/remote-gate.sh:218-222,287-309`). Set and inspect `oom_score_adj=500` on lane and dev containers;
  run-gate's RG-321 implementation is UNVERIFIED on this tree and must merge first. Global OOM uses the cockpit's negative
  adjustment (`scripts/netcup/runner/start-cockpit-runner.sh:29-31`; `mdt-cockpit/host-setup/README.md:1176-1178`).
  Memcg OOM selects containers in `dev-gates.slice`; oomd may kill that whole slice, separate from the cockpit's
  `dev-interactive.slice` (`.controller-state/core/notes/P-fd-05-refresh-input.md:30-36`; `mdt-cockpit/host-setup/README.md:1167-1186`).
  MemoryLow=6G was runtime-only (`.controller-state/core/notes/P-fd-05-refresh-input.md:32`); persist it before controller
  jobs ship. Refuse exec-mode lanes because they
  have no per-job container for OOM attribution or adjustment.

### 3.5 No new wall-clock budget

Nothing in §3 sets a run wall-clock limit. Wait limits cover admission; progress judges stalls, pausing during
thrashguard freezes. D13 removes today's timeout (`scripts/controller/dev:159`; operator 2026-11-26);
`--wait` is CTL-52's 900-second admission limit (`scripts/controller/dev:278`); `aging_s` only orders queue; `LOST_AFTER_SECONDS` is controller liveness, not a run budget (`fleetd/src/fleetd/campaign.py:25-29`).

## 4. Host executor and results

### 4.1 What ports, what is re-homed, what is deleted

**Ports unchanged** from `JOB_TEMPLATE`: PSI wait, bundle/tag checkout, UID/prerequisite checks, `docker exec`, artifact tar
and `pid`/`rc`/`EXIT` markers (`scripts/netcup/runner/remote-gate.py:560-790,608-632,647-762`). **Re-homed:** admission and store collection.
During overlap the agent charges legacy `~/jobs/rg-*` without EXIT at class charge; final flag removal deletes
controller slots, dry-run holder and origin/main snapshot (`scripts/controller/remote-gate.sh:137-215,242-257`).

The stdlib executor ships like `fleetd/src/fleetd/hostside.py` under FD-039's one-implementation rule
(`fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:854-875`). Each job gets a transient scope outside the agent cgroup, so agent
restart/downgrade cannot kill it (§4.5).

### 4.2 Result transport

- **Bundle:** `have` returns refs; the client builds the minimal bundle (`build_bundle`, `scripts/netcup/runner/remote-gate.py:436-490`)
  and uploads via `put`. The host verifies the closed name, declared bytes and sha256 (`scripts/netcup/runner/remote-gate.py:1068-1070`); no destination path comes from the client.
- **Fetch:** byte ranges limit retry cost (§2.2); client verifies whole-file sha256 and rejects a mismatch (`scripts/netcup/runner/remote-gate.py:1213-1219`).
- **Store:** keep one `assay-store.tgz` plus checksum for all rels (`scripts/netcup/runner/remote-gate.py:494-527,1193-1222`). Only the §2.2 event and `result.json` carry structured results.

### 4.3 Stop, collect, then teardown (D7)

On normal completion or lane failure, deregister the exited container, run `collect_once`, verify
the archive, remove this job's containers by labels and checkout, then release its charge. On
cancel or TERM, deregister and thaw it if frozen, send TERM to the lane, and wait for its process
or container to stop. Only then collect once and verify the archive. Collection is idempotent;
store errors do not rewrite the lane verdict.

If deregister/thaw fails, retry without teardown. If TERM fails or the lane remains alive, collect
no artifacts or store; keep the checkout, containers and charge, report `stopping`, and retry.
When it exits, collect every flushed record. There is no wall-clock limit on this wait: stopping a
run never silently discards its last writes.

### 4.4 Bind the verdict to the commit

The agent verifies four facts: request `sha`; a host-written checkout line after `rev-parse HEAD`
equals that sha (`scripts/netcup/runner/remote-gate.py:682-685`); the anchored verdict line for the exact lane; and the
separate start event's container id, confirmed by `docker inspect` labels and checkout path. It
writes `result.json` from those facts only. A missing or mismatched fact is transport/refusal,
never PASS. Rejected: binding to the client's `--commit` echo (`scripts/controller/remote-gate.sh:283-285`).

### 4.5 Host loss, agent restart and reboot

Today `run-job` detaches with `nohup` and writes `pid`/`EXIT` (`scripts/netcup/runner/run-job:16-26`); remote-gate has `--detach` and `collect` (`scripts/netcup/runner/remote-gate.py:1090-1093,1304`).

- **Controller lost, host alive:** the separate executor scope and its job id remain live; the
  client reattaches by `job_id`. `run-gate` already writes an in-flight container record
  (`run-gate-project/run_gate.py:7350-7399`), but the agent owns the job lifecycle.
- **Agent restart or downgrade:** the executor is outside the agent cgroup and continues. On
  startup the reaper scans every job without an `EXIT` marker and reconnects to its transient
  scope. It does not signal or restart a live executor.
- **Dead executor, lane still alive:** the reaper finds containers by this job's labels, stops
  the lane, waits for exit, collects from the surviving checkout, then removes those containers
  and checkout. An inspect error other than Docker's explicit "no such object" leaves the job
  and charge untouched (`run-gate-project/run_gate.py:7601-7645`).
- **Host reboot:** `boot_id` changed, so no old pid or executor is trusted. Before marking the job
  `lost`, the reaper checks job-labeled containers; if one is running, it stops and waits for it.
  It then collects the surviving checkout and artifacts, removes only that job's labeled
  containers, and records `lost`. If stop or collection fails, it keeps the checkout and reports
  the failure for retry; it does not claim the progress was collected.
- **Host unreachable:** `3`; fleetd pauses that host and never reads unreachable as gone
  (`scripts/controller/remote-gate.sh:17-20`; `fleetd/src/fleetd/campaign.py:931-989`).

### 4.6 R2 store carry (RG-298)

The carry is §2.2's `stores.collect`/`stores.seed` pair with one added rule: a seed travels with
its own sha256 and size, is unpacked only into the checkout this `job_id` created, must contain no
tracked files and no members outside its target (today's `seed_stores` checks,
`scripts/netcup/runner/remote-gate.py:530-560`), and a failed seed is exit `78` with `STORE-SEED-*` in the job log —
today's behaviour verbatim. The carry's *format* belongs to assay's `assay-merge-manifest/1`
(B544/B565); the runner carries bytes and never interprets them, which is RG-298's whole scope
(`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md:9647-9652`).

## 5. Security

- **Credentials cover fleetd-owned hosts only** (operator: "host credentials in fleetd applies to
  fleetd-owned hosts only"). ciu-direct keeps its own keys and shares only the agent's queue;
  fleetd never sees ciu's credentials and ciu never sees fleetd's.
- **Remote identity.** Keep the provider check (`scripts/controller/remote-gate.sh:117-135`,
  `scripts/netcup/runner/remote-gate.py:229-234`), pinned host key and `probe` host/boot-id comparison.
  Remote workers keep SSH and the forced command.
- **Controller host socket (D14; operator ruling `.controller-state/operator-decisions.md:696-697`).** Root `fleetd-agent.service` runs on the host. Host setup creates
  `fleetd-clients`, adds its matching GID to the devcontainer, and uses `systemd-tmpfiles` for
  `/run/fleetd-agent` at host boot (`root:fleetd-clients`, `0750`). The agent creates `agent.sock` there as
  `root:fleetd-clients`, `0660`. Devcontainer config read-write bind-mounts `/run/fleetd-agent` to the same path,
  not the socket file; the service leaves the tmpfiles directory in place on restart, exposing its new socket.
  Agent uses Linux `SO_PEERCRED` UID/GID; host policy maps the configured pair to one `controller-devcontainer`
  principal, auth group and queue class; unknown peers are denied. The socket GID only grants connect permission.
  Sessions share UID/GID, so cannot be distinguished:
  they share repo/job scope and can act on one another's jobs. Check `requested_by.client` against
  configured labels; version/run are audit only. No request field grants identity, group or priority;
  apply the same group/repo/job checks as SSH. Accept only v1 agent verbs, no shell. No SSH key exists
  for this host. Missing/refusing socket returns 3, with no SSH or local-admission fallback; retry using
  job id and digest.
- **Forced command (FD-009).** Authorized keys map to group and priority on the host
  (`fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:895-905`; enrollment belongs to CIU-218). Replace today's
  client `guarded()` wrapper (`scripts/netcup/runner/remote-gate.py:237-246`) with server-side checks:
  derive identity from the key/peer group, never `requested_by`; validate schema, repo, relative
  project/store paths inside the checkout, resources and argv. Use a server-side run-gate allow-list,
  a host per-repo env profile (RG-319), and §2.2's closed fetch names. No shell string is executed.
  Forced command narrows SSH access; it does not sandbox submitted code.
- **Trust boundary.** Submit runs arbitrary commit code beside the host Docker socket; submit access
  is root-equivalent, and neither socket permissions nor forced command sandbox the job.
- **Scope and records.** Facts, jobs, submit, cancel and fetch stay within the authenticated group's
  repos. `requested_by` is checked as an audit label, never authorization. Records/logs contain no
  secret, key path, host address or token; job dirs use `umask 077` (`scripts/netcup/runner/run-job:3-15`).

## 6. thrashguard and hostobs

The thrashguard design sends `thrashguard.freeze`/`thrashguard.thaw` through hostobs, not its
socket; P-fd-05 reads that state through hostobs (`docs/design/thrashguard/DESIGN.md:206-230,280-286`).
Hostobs currently has the event store but does not serve `annotate` or `events`; those APIs and a
freeze sample are prerequisites to guarded-host admission (`docs/design/thrashguard/DESIGN.md:230-241`).

1. **The agent reports freeze state** in `host-facts/2` as §3.2's four names (`guard_present`,
   `guard_state`, `config_digest`, `frozen_units`), `frozen_units` from hostobs and
   `guard_state` from the guard's runtime file (`fleetd/src/fleetd/facts.py:440-522`). Freeze timing, owner, reason
   and `freeze.queued` remain not collected until thrashguard P4.
2. **The agent registers each started job** with thrashguard's registrant socket when present: one
   `register {unit: <job_id>, container: <id from §2.2's event>, score, priority,
   owner: "fleetd", run: <job_id>}`, heartbeat on its tick, and deregister before removal
   (`docs/design/thrashguard/DESIGN.md:287-301,357-381`). There is one registrant: run-gate owns jobs before this
   package; the agent owns them after migration. Run-gate stops registering an agent-owned job.

**Freeze rule [N4].** If `frozen_units > 0`, hold new admission; do not pause or kill running jobs.
Exclude the frozen interval from progress-stall accounting. If hostobs cannot report state while
thrashguard is present, treat it as unknown and return 75; never treat null as zero. If the guard
is absent, report that fact and there can be no guard-owned freeze. This also works without fleetd:
ciu-direct uses the same host queue.

## 7. Migration, packages, parity

**Migration order.** Ship run-gate's separate-fd event, anchored verdict, OOM score and freeze-aware
stall watch (RG-321 first), then hostobs event APIs and persistent cockpit `MemoryLow`. Ship the host
agent as a service with StateDirectory, tmpfiles socket directory, client group and devcontainer mount;
enable its controller worker after the socket lifecycle probe. Then switch `remote-gate.py`, `dev`,
fleetd campaigns and the dr runner in order. Keep legacy paths for rollback; remove slots/workroot/
snapshot after every client migrates. ciu-direct uses runner queue when installed, else own admission (D12).

**Parity rows.** Each row must be able to fail independently: (a) unique exit codes, including
preflight 4→2, prerequisite 76→73, budget 4, OOM 76, store 77/78, and queue 75; (b) anchored,
lane-specific verdict and `result.json`, rejecting a substring from test output; (c) cancel stops
then archives a checksum-valid store with every flushed record; (d) wait expiry withdraws a queued
job and it never starts later; (e) a reserved large head starts on the first admission after it
fits despite continuous small arrivals; (f) unreadable PSI and meminfo refuse
(`scripts/netcup/tests/test_remote_gate_repos.py:597-600`; `scripts/netcup/tests/test_remote_gate_memory.py:91`); (g) no job exceeds host footprint, slice or disk floors; (h)
the separate-fd event id matches Docker labels and this checkout; (i) OOM is attributed per job
and all lane/dev containers carry adjustment 500; (j) group identity, repo/path/argv validation
and closed artifact names reject hostile inputs; (k) bundle/tag replay and checkout sha binding
(`scripts/netcup/tests/test_remote_gate.py:227,322`); (l) agent restart, dead executor and reboot preserve the charge
until stop, collection and cleanup finish; (m) the socket has no shell, shared UID maps to one group,
missing/refused socket returns 3 without fallback, and its directory mount exposes a recreated socket while queue state
survives devcontainer restart.
Contract tests also cover same-digest idempotence and major-version refusal.

**Rollback.** Each client owns one `legacy|agent` flag. The agent keeps legacy jobs in its charge
book during overlap. Downgrade within a major is safe because executors run in separate scopes;
major mismatches refuse and minor additions are capability-advertised. Flag and legacy-path
removal is a later, separately gated package.

**Package split, size and gate.**

| package | size | gate |
|---|---|---|
| A. run-gate event fd, anchored verdict, OOM adjustment, frozen-time stall watch (RG-321 first) | M | run-gate lane contract gate |
| B. hostobs event APIs + host-setup persistent `MemoryLow` | M | hostobs contract gate; host setup render gate |
| C. `host-facts/2`, host service/state, socket group/mount, scopes/book, forced command, `have`/`put` | M-L | fleetd gate + socket lifecycle probe; enrollment depends on CIU-218 |
| D. result ranges and remote-gate client switch; retain legacy slots/snapshot | M | remote-gate parity rows + live lane |
| E. `dev` payload, pty, workspace lock, OOM adjustment; remove `max_s` | S-M | dev contract gate + live dev job |
| F. fleetd campaigns and dr runner; R2 store carry (RG-298) | S-M | campaign gate + live batch |
| G. remove legacy flags, slots and runner snapshot after all migrations | S | compatibility gate |

Sizes are effort estimates, not lane time limits. No package adds a wall-clock limit to a run.

## 8. N2 decision

No P-fd-05 questions remain open: Q1, Q2, Q6, N1 and N3–N5 are in §§0 and 3; Q3–Q5 are in §§2–3.

| # | decision | reason |
|---|---|---|
| N2 | Host fleetd serves the same API on a mounted Unix socket; peer credentials map the shared devcontainer UID/GID to one controller principal. Remote hosts keep SSH and forced commands. | Host-side facts and persistent admission state must survive devcontainer restart. |
