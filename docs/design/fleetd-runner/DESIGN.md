<!-- status: design; scope: fleetd + remote-gate + dev + campaigns; package P-fd-05 (CTL-51); input 2026-11-25/26 -->
# Design: one shared host runner and fleetd placement (P-fd-05 = CTL-51)

## 0. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | One API (`fleet-host-agent/1`); modes differ only in who picks the host | callers share lifecycle and result semantics |
| D2 | Placement reads host agent `host-facts/2`, never `docker stats` | host facts expose the admitted capacity |
| D3 | Planned cores may reach `cores + 1`, capped by host `planning_cap` | leave room for IO overlap within the worker slice |
| D4 | Each host sets warm-set charges in `/etc`; exclude swap; live memory PSI is a hard stop | warm working set predicts RAM; PSI catches actual pressure |
| D5 | One host admission book for every caller; waiting returns 75 | the host sees every job that consumes its capacity |
| D6 | Progress decides a stall; no run wall-clock budget | duration alone does not show lack of progress |
| D7 | Stop the lane before collecting; retain checkout and charge if stop fails | collection must include all writes flushed before exit |
| D8 | Fleetd credentials cover only fleetd-owned hosts; records carry no secret | ciu keeps its own access in ciu-direct mode |
| D9 | Controller-host planned total is at most 3 GB; max class is `small`, set in `/etc` | a host-owned cap protects the constrained gate slice |
| D10 | One fair fleetd queue ordered by clamped `priority`; aged jobs reserve capacity | prevent avoidable merge delay and large-job starvation |
| D11 | A known freeze blocks new jobs; running jobs remain untouched | avoid adding work while thrashguard holds units |
| D12 | ciu uses an installed runner queue; otherwise it keeps its own admission | avoid two schedulers on an enrolled host |
| D13 | Remove dev's `max_s`; use progress to judge a run | fixed duration conflicts with the no-time-budget rule |
| D14 | Local controller run-gate leases through host fleetd over a mounted socket; fleetd placement waits for a host cockpit runner | host cgroup visibility and queue survival |

## 1. Scope, non-goals, current state

**Scope.** Job API/host agent, fleetd placement, remote-gate executor/result transport, and fleetd/client migrations including the controller lease. Closes
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
| **fleetd-placed** | fleetd, from `host-facts/2` | fleetd-owned hosts; controller host excluded pending cockpit runner | fleetd's cross-host queue, then the host agent |

Same in both: the record, the status answer, the exit codes, the progress stream. Programmatic
job construction in fleetd today goes through `JobSpec`/`RunnerJobs`
(`fleetd/src/fleetd/runner.py:30-60`); that module becomes the client of the agent.

**Controller host service (D14).** Fleetd-agent runs there as a host service, outside the devcontainer. Local `run-gate`
uses a socket lease against the host admission book; its lane stays in the devcontainer. The agent sees `dev-gates.slice`
and host pressure, which the devcontainer's private cgroup view cannot. Fleetd-placed execution there waits for a host
cockpit runner. The queue and live lease records survive devcontainer and agent restarts.

## 2. The job API

### 2.1 Version

`schema: "fleet-host-agent/1"` on every answer, plus `agent_version` (SemVer). A client
**refuses the host on a MAJOR mismatch** before any job work, naming both versions; a
MINOR/PATCH mismatch is accepted only when the requested verb and its fields appear in the
host's advertised `capabilities`. B4 already promises this and FD-039 records why the agent
must be a shipped module, not a string program (`fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:854-860`).
Rejected: "try it and see" — an unknown grammar on the far side of an ssh call produces a wrong
answer rather than no answer.

Every answer uses the normal `{schema, agent_version, ...}` envelope. Errors
put exactly `{exit_code, reason}` in the nested `error` object and omit
top-level `exit_code` and `reason`; successes omit `error`. The client checks
only for `error`, so a non-zero `exit_code` in a successful status remains the
lane result. A malformed `error` is a protocol failure (70). The same rule
applies to each frame on the held lease stream.

### 2.2 Verbs and the record

B4 names `probe|start|stop|fetch|facts`; v1 uses `probe|facts|have|put|submit|status|progress|cancel|fetch|lease`.
No start/stop aliases: the current CLI has a facts collect/validate group, while host-agent verbs
remain P-fd-05 (`fleetd/src/fleetd/cli.py:396-440`; `fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:854-857`).

**submit** takes one record:

| field | type | meaning |
|---|---|---|
| `job_id` | string | idempotency key, prefixed by the authenticated group; `/1` grammar is `^[A-Za-z0-9][A-Za-z0-9._-]*$`, maximum 128 characters, and the host rejects ids outside it |
| `repo` | string | repo name (`REPO_NAME_RE`, `scripts/netcup/runner/remote-gate.py:81`) |
| `sha`, `base` | full hex, hex or null | the commit to judge; the base (already resolved by the client) |
| `project`, `lane` | string | as `--project-dir`/lane today |
| `shard` | `{index,total}` or null | reserved for P-fd-06; `/1` refuses a non-null value |
| `resources.planned_cores` | positive number | derived from the lane declaration or measured job shape; no default |
| `resources.cpu_set` | CPU ids or null | `/1` refuses a non-null value with 2, reserved like `shard`; absent lets the executor choose an allowed set |
| `resources.footprint` | `tiny|small|medium|large` | derived by the client from its warm-set table; §3.3 |
| `resources.declared_peak_bytes` | int or null | lane declaration; a hint, not a cgroup limit |
| `priority` | `merge-gate|dev|gate|campaign` | requested queue class; host clamps it to the authenticated principal's `/etc` allow-list |
| `requested_by` | `{client, version, run}` | audit labels only; identity and group come from credentials |
| `stores.collect`, `stores.seed` | paths; `{path,sha256,bytes}` | today's options (`scripts/netcup/runner/remote-gate.py:1031-1032`) |
| `extra_argv`, `env` | list, map | argv and named profile values, each checked by the host against allow-lists |
| `dev` | object or null | tracked patch, untracked archive (each hash and size), verb argv, tty flag and worktree key; only for `kind: dev` |
| `kind` | `lane|dev` | selects the executor mode |

**Idempotence.** The client prefixes the id with its authenticated group. The digest covers
`repo`, `sha`, `base`, `project`, `lane`, `shard`, `kind`, `priority`, all resource fields, store descriptors,
`extra_argv`, `env`, `dev`, and `requested_by.client`/`.run`; it excludes `job_id` and
`requested_by.version`. The agent binds
the authenticated group separately. Same id and digest returns existing state; same id with a
different digest is refusal (2). This preserves today's run-dir and `run-job` duplicate refusals
(`scripts/netcup/runner/remote-gate.py:1048-1050`; `scripts/netcup/runner/run-job:14`).
After completion, a durable terminal record retains the group and digest without a charge. Replay
of the same id and digest returns that terminal state without a handle; another digest or group
is refusal (2).

For `kind: dev`, the host derives a persistent workspace from authenticated group, repo and
`worktree_key`, and takes one lock per key. The patches restore the dirty tracked and untracked
files; the key is not a path. The host runs only the validated verb argv. `tty` selects a pty.
The payload has no `max_s`, removing today's timeout wrapper (`scripts/controller/dev:159`); `dev stop` remains available (`scripts/controller/dev:550`).

**have** returns the refs the host already has. **put** takes `{name, sha256, bytes}` plus a
streamed body; only `in.bundle`, indexed `assay-seed-<n>.tgz` archives and their checksum files,
the two dev patch archives, and `candidates` are valid names. The agent checks length and checksum
and creates the destination itself. A submit may use `--only-candidates` only when its path is the
host-created `candidates` file uploaded by the same authenticated group; an arbitrary client path
is refused.

Controller-only **lease** takes `{lease_id, container_name, footprint, planned_cores, priority}`. The client supplies a group-prefixed
idempotency key; the host reserves that id and the client binds the created container ID on that connection. A reconnect names the
existing `lease_id` and immutable container ID.

Local framing: ordinary verbs use one request per connection, 4-byte big-endian byte length + UTF-8 JSON object, max 64 KiB.
`lease` is controller-socket-only and keeps its connection open for the full gate-container lifetime; grant/bind/ack messages use the same frame size, then Run-Gate closes after removal.
For `put`, exactly `bytes` follows, streamed and capped by required
`policy.max_upload_bytes`. Before reading the body, the agent reserves the
declared size under a cross-process upload-store lock and requires
`stored + in_flight + new <= policy.max_upload_store_bytes` and
`upload_fs_available - in_flight - new >= max(policy.disk_floors)`.
Both facts are required; unavailable facts refuse. The stored scan counts
regular files across all groups without following symlinks and refuses an
unreadable file. Each reservation holds its full declared size from admission
through durable commit (file fsync, rename, and directory fsync) or, after
abandonment, until its partial is removed. Buffered and written bytes do not
reduce that reservation; bytes already on disk are counted twice during
streaming, conservatively. The lock is not held while the body streams. Client
half-closes after the request and server rejects truncation/extra input. A
stable owner lock is held before the atomic reservation record is published
(temporary file, fsync, rename, incoming-directory fsync). Recovery derives
artifact names from the validated token and reclaims torn records or leftover
record temporaries only after it acquires the owner lock; a partial with a live
owner is retained. Missing or inaccessible owner locks and invalid live records
refuse with the offending filename and an operator check. Retention and garbage
collection are C3b follow-up FD-062.
`fetch` ranges are at most 8 MiB; responses use the same frame + body.

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

**progress** for assay lanes is read from `.assay/progress-<assay_lane>.jsonl` in the job checkout,
where `assay_lane` comes from `hostside.resolve_assay_lane(checkout, project, lane)`, and is streamed
by byte offset (`run-gate-project/SPEC.md:2321-2342`). Command lanes have no candidates
(`run-gate-project/SPEC.md:416-436`): `done` and `total` are null, with
`unit: log_bytes` from the agent's `lane.log` offset. The executor records start, stage and end
for `dev`. Assay done-counting reads at most the opened file size up to
`MAX_ASSAY_PROGRESS_COUNT_BYTES` (8 MiB). Larger files return `done: null` and
a non-empty `done_reason`; otherwise `done_reason` is absent. Appends after the
opened-size snapshot do not extend the scan; incremental counting remains
FD-063. Answers include `{done,total,unit,last_progress_utc}`; `jobs[]` uses
`progress_unit` and `last_progress_utc` to match the draft (`.controller-state/platform/design/agctl-estate-snapshot-draft.md:44-45`).

**cancel** takes `{job_id, reason}`. A queued job is atomically removed and needs no collector;
a running job follows §4.3. An unknown or finished id is a refusal (2).

**fetch** takes `{job_id, artifact, offset, length}` for the closed set `job.log`, `lane.log`,
`lane-detail.log`, `artifacts.tgz`, `assay-store.tgz`, `assay-store.tgz.sha256`, `result.json`, and answers a byte range
plus `{size, sha256, next_offset}`.

**Exit codes.** `0` PASS; `1` lane FAIL; `2` request, path or preflight refusal;
`3` identity or transport failure; `4` run-gate `BUDGET_EXCEEDED`; `70` unexpected runner
failure; `71` checkout failure; `72` missing base; `73` reserved by fleetd's runner; `75` queued job
withdrawn before it started; `76` INCONCLUSIVE-OOM; `77` store collection failed; `78` store seed
failed; `79` prerequisite missing (moved from today's 76); `143` cancelled. Store collection failure does not change the
lane verdict. The former preflight exit 4 maps to 2 so 4 has one meaning. OOM is attributed to
this job's containers, never a whole-slice counter. A non-null `resources.cpu_set` is also refused
with 2 in `/1`. Record preflight `4`→`2` and prerequisite `76`→`79` in `fleetd/CHANGES.md`.

**The host-owned event file run-gate emits.** run-gate already writes the container it started
into `.run-gate/inflight/<lane>.json` (`run-gate-project/run_gate.py:7350-7399`) and its id is
readable with one inspect (`run-gate-project/run_gate.py:7601-7665`; a name taken by another
container is detected at `run-gate-project/run_gate.py:17980-17990`). P-fd-05 passes the host
process two explicit, paired flags: `--event-file <absolute-path>` and `--job-id <id>`. There is
no default or environment fallback. The job id matches
`^[A-Za-z0-9][A-Za-z0-9._-]*$`, at most 128 characters. Its parent directory must already exist
and be outside the judged checkout, repository, and every host path mounted into the lane or its
environment probes;
the final component is not a symlink and, if it exists, is a regular single-link file. Run-Gate
opens it append-only, without following the final symlink, at mode `0600`. The file is never mounted into a
lane container. An inherited descriptor is not used: it cannot cross `docker exec`.

The host gives each Run-Gate invocation a fresh event path; the file is append-only, so a refusal
after opening may leave it empty. Each line is one compact JSON object written until complete and
flushed to disk. Every object has `api: "fleet-host-agent/1"`, `event`, `job_id`, and `lane`.
`lane-started` adds Docker's full lowercase 64-hex `container_id` when recognized, full
`image_id`, and `adopted`; an unrecognised ID is null with reason `container-id-unrecognised`.
It is written before lane output is relayed, once
per container used to execute the judged lane. Read-only preflight probes are not lane starts,
but their mounts are included in the event-path exclusion check. A mount-bearing token whose
host source cannot be derived causes a named refusal before the lane starts. A lane or environment
that has no container writes null IDs and a `reason`. A reused name whose container id does not match the
inflight record is not reported as that run's container. Nested composite members, retries, and
worker dispatches receive no event flags or file; their start facts are forwarded to the
parent-owned event sink, which writes their `lane-started` lines.

```jsonl
{"api":"fleet-host-agent/1","event":"lane-started","job_id":"fleetd-42","lane":"suite","container_id":"<full Docker id>","image_id":"sha256:<image digest>","adopted":false}
{"api":"fleet-host-agent/1","event":"lane-verdict","job_id":"fleetd-42","lane":"suite","verdict":"FAIL","exit_code":1,"lane_exit_code":7}
```

The single normal `finish` path writes `lane-verdict` after printing the anchored
`run-gate: lane '<lane>' verdict ...` line. It carries `verdict`, `exit_code` (the Run-Gate
process status), and `lane_exit_code` (the raw lane status printed in the summary, or null). With
`--json`, the result object stays on stdout and the anchored summary is printed to stderr before
the event. A run that never reaches `finish` has no verdict event. A write error after a partial
write can leave an unparseable final line; the consumer ignores that last line and treats a
missing verdict as not passed. Any other unparseable line means not passed. A write failure
after lane start emits one warning without changing the verdict or process status. Without both
flags, lane behavior and output are byte-identical.

The agent accepts a verdict only when the file contains exactly one `lane-verdict` for its
`job_id`; zero or multiple matching verdicts are not passed. The event, the last lane-log line
beginning with the exact anchored prefix, and the process status agree: event `exit_code` equals
the process status, event
verdict equals the anchored verdict, event `lane_exit_code` equals the summary's raw `exit_code`
(or both are null), and the process status matches Run-Gate's closed verdict table. Missing
verdict events are never PASS. The consumer selects the container inspection rule from its own
request's resolved lane environment mode and expected runner name, matched to the event's `lane`;
`adopted` alone does not select the rule. For `mode = "exec"`, inspect the requested persistent
runner name, require its inspected full ID to equal the event ID, and confirm the checkout mount;
the external runner does not need `run-gate.*` labels. For a Run-Gate-started container lane,
including an adopted container lane, require both the `run-gate.*` labels and the checkout mount.
The checkout-mount check applies in both modes. A null ID has no container to inspect. The start
event identifies the container for thrashguard registration. Rejected: parsing shared lane output or matching
`docker ps` by name.

## 3. Placement and admission

### 3.1 The operator's rule, exactly

Per worker host `H`:
- **planned core use**: total planned use is at most `cores(H) + 1`;
- and bounded by `planning_cap(H)`, read from the host's worker slice `cpu.max` and capped at
  `cores(H)+1`. On the controller host this is 6 planned cores, the `dev-gates.slice` CPU limit
  (`.controller-state/operator-decisions.md:680-684`). The cap is a host fact, never a client number;
- `/1` refuses a non-null `resources.cpu_set` with 2, reserved like `shard`. With it absent, the
  executor chooses from the host's allowed CPUs; selecting a CPU set is deferred to a later API;
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
| `cpu.planning_cap` | number | worker slice `cpu.max`, capped at `cpu.online + 1` | `null`+reason; never default to `cores+1`; later host admission refuses on null, today's planner does not read it |
| `load1`,`load5`,`load15` | number | `/proc/loadavg` | `null`+reason; later host admission does not place the host (never "assume idle"), today's planner does not read load |
| `memory.total_bytes`, `memory.available_bytes` | bytes | `/proc/meminfo` (`fleetd/src/fleetd/facts.py:267-290`) | `null`+reason; today's planner refuses on a null `total_bytes` and does not read `available_bytes` (later host admission does) |
| `memory.host_reserve_bytes` | bytes | `/etc/fleetd/facts.toml` | `null`+reason; later host admission may require it, but today's planner does not read it |
| `memory.swap_total_bytes`, `memory.swap_free_bytes` | bytes | `/proc/meminfo` | report only; never counted as capacity |
| `psi.{cpu,memory,io}.{some,full}_avg{10,60,300}_pct` | percent | `/proc/pressure/{cpu,memory,io}` | missing line, malformed value, or value outside 0–100 → `null`+reason; admission uses avg10 only, and today's planner requires only memory/io `some_avg10` |
| `gate_slice.memory_current_bytes`, `.memory_warm_bytes`, `.memory_high_bytes`, `.memory_max_bytes`, `.cpu_max` | bytes, quota/period | cgroup v2; warm is current minus `memory.stat[inactive_file]` | missing inputs → `null`+reason; later host admission refuses placement, today's planner does not read the gate slice |
| `disks[]` | `{name,mount,total_bytes,free_bytes,fs,floor_bytes}` | `statvfs`; checkout and Docker roots plus per-host `/etc` floors | `null`+reason; later host admission applies disk policy, but today's planner does not read disks |
| `cgroup_v2.mounted`, `cgroup_v2.controllers` | bool, list | mountinfo | refuse (admission and the guard both need it) |
| `kernel_interfaces.*`, `ksm.*` | bools, ints | kept from /1 | `null`+reason, never a synthetic 0 |
| `control_plane.slice`, `.present`, `.cpu_weight`, `.memory_min_bytes`, `.service_memory_min` | as /1 (`fleetd/src/fleetd/contracts.py:464-506`) | `systemctl show`, `/etc/fleetd/facts.toml` | defaults (`fleetd/docs/SPEC.md:100-160`); 768 MiB profile only with no service fact |
| `guard.guard_present` | bool | config path present | `{"v": false}` without a reason (an absent config is a known fact); `guard.frozen_units` then carries reason `thrashguard-absent` (`docs/design/thrashguard/DESIGN.md` section 6a) |
| `guard.guard_state` | `off|report|enforce|unknown` | guard runtime file (`fleetd/src/fleetd/facts.py:517-522`) | omitted with reason; never `off` by default |
| `guard.config_digest` | sha256 hex | `/etc` bytes incl. drop-ins (`fleetd/src/fleetd/facts.py:440-514`) | `null`+reason |
| `guard.frozen_units` | int | **hostobs** freeze/thaw events (§6) | `null`+reason; `frozen = frozen_units > 0` is then `null`, not `false` |
| `freeze.since_utc`, `freeze.by`, `freeze_reason` | time, strings | hostobs events | `null`+reason `not-collected until thrashguard P4` |
| `freeze.queued` | int | none (agctl draft has no source, `agctl-estate-snapshot-draft.md:38-39`) | `null`+reason `not-collected` |
| `policy.host_role`, `.psi_refuse_some_avg10`, `.max_lane_containers` | as /1 (`fleetd/src/fleetd/contracts.py:510-524`) | `/etc/fleetd/facts.toml` | role null → refuse (`fleetd/src/fleetd/plan.py:480-483`) |
| `policy.planning_cap`, `.max_footprint`, `.max_planned_bytes`, `.footprint_charges_bytes`, `.priority_classes_by_principal`, `.disk_floors`, `.max_upload_bytes`, `.max_upload_store_bytes`, `.admission` | number, class, bytes, admission settings | optional `/etc/fleetd/facts.toml` host policy | missing value → `null`+reason; current planner does not require them; `put` refuses (2) when either upload cap or disk floors is unavailable |
| `policy.aging_s`, `.legacy_charge_bytes`, `.legacy_planned_cores` | seconds, bytes per legacy job, planned cores per legacy job | `/etc/fleetd/facts.toml` host policy added by design package C | missing value → `null`+reason; `aging_s` is required for queue decisions; the two legacy values are required when a legacy job is listed; unknown needed values return 75 |
| `queue.planned_cores`, `.charged_bytes`, `.waiting` | number, bytes, count | host agent's admission book | `null`+reason |
| `jobs[]` | `{id,state,requested_by,resources,started_utc,progress_done,progress_total,progress_unit,last_progress_utc}` | `jobs/` dir | `null`+reason `not-collected` |
| `tools[]` | as /1 | probes | `null`+reason |

`facts.toml.policy` currently has a closed key set (`fleetd/src/fleetd/facts.py`, `_object`). Install a release that accepts an optional
policy key before any deployment writes it; writing an unknown key first breaks
fact collection. `aging_s`, `legacy_charge_bytes`, `legacy_planned_cores`, and
`max_upload_store_bytes` have no default and emit null with a reason when
unconfigured. A host must configure `max_upload_store_bytes` before relying on
`put`. This library requires `policy.legacy_planned_cores` when the legacy
provider lists a job and returns 75 with that key in the reason when it is
unknown.

**Changes this design asks of the draft's rows** (`.controller-state/platform/design/agctl-estate-snapshot-draft.md`), named for the agctl owner: the draft's
`queue.*` row ("slots stay `source: remote-gate`/`run-gate`") is replaced by the agent's queue;
`busy_pct`/`steal_pct`/`window_s` stay `not-collected` — load1/5/15 carry the decision (§3.1) and
a busy percentage is a second opinion nothing acts on; the draft's `jobs[].id` row ("the
producer's own id, opaque") is kept and **is** the §2.2 `job_id`, so the draft's "no join table
today" stays true: the one id *is* the join (`.controller-state/platform/design/agctl-estate-snapshot-draft.md:41-45`).

### 3.3 Footprint class and fit

A class estimates the warm working set; it is not a peak-RSS limit. Each host sets its class charges in `/etc/fleetd/facts.toml`;
the client derives an explicit class from its warm-set table. Today `scripts/controller/remote-gate.sh` reads a controller-owned
peak table (`scripts/controller/remote-gate.sh:143-180`); migration replaces it. Run-Gate already collects `memory.current`
and parsed `memory.stat` (`run-gate-project/run_gate.py:4131-4146`); P-fd-05 makes the host agent read those same cgroup inputs.

| class | example charge on an 8-GiB worker | typical |
|---|---:|---|
| `tiny` | 0.5 GiB | lint, a parse |
| `small` | 1.5 GiB | a pytest lane, a cmru build |
| `medium` | 4 GiB | a gate-full lane |
| `large` | 6 GiB | a warm-set estimate for a large gate |

`warm(cgroup) = memory.current - memory.stat[inactive_file]`. Use this exact per-cgroup value for each `current_j`, the slice
term below, and samples after startup at each progress event (command lanes sample when their log-byte offset advances).
Use the p90 sample for that lane/version and assign the smallest configured class at least that large. With no measurement,
the caller supplies an explicit class. Missing/invalid class is refusal (2), never an invented default.
These estimates are not container caps. On an 8-GiB worker with a 0.5–1-GB host reserve, the example charges fit about 7 GiB;
all charges remain per-host `/etc` values. The controller permits only `small` and caps planned totals near 3 GB (D9).

For each admitted job `j`, let `charge_j` be its selected host's class charge, `current_j = warm(job_cgroup_j)`,
`slice_warm_current = warm(gate_slice)`, and `reserved = sum(max(0, charge_j - current_j))`. One fit value decides admission:
(Every live lease (§3.4) is an admitted job `j` for RAM and planned cores; its cgroup is the bound container's, and `current_j = 0` before the bind.)

```
fit_bytes = min(
  memory.total_bytes - host_reserve - sum(charge_j),
  min(gate_slice.memory_high, gate_slice.memory_max) - slice_warm_current - reserved,
  policy.max_planned_bytes - sum(charge_j)
)
admit iff fit_bytes >= charge_new
```

At each admission decision the capacity provider supplies current warm samples for live jobs and leases. The book never uses a stored
sample to lower a reservation. A missing sample is `current_j = 0`, so the full charge remains reserved; this is the conservative
direction.

The first term plans warm sets against physical RAM minus the host-services reserve. The slice term subtracts warm observed
use plus only unconsumed reservations, so actual use is not counted twice. `max_planned_bytes` is the per-host `/etc` cap.
For each eligible host, static plannable RAM is the minimum of total RAM minus host reserve, slice ceiling and planned-byte cap.
If the class charge exceeds that value on every eligible host, `submit` refuses with 2; transient load still queues or returns 75.
Swap is never added; fleetd's campaign overcommit factor (`fleetd/src/fleetd/plan.py:563-585`) is not used. A job may exceed its
estimate; live `memory full avg10 >= 3` stops new admission. That admission gate does not choose a freeze target. The design-only
freeze policy in §3.3a can freeze one selected job, but does not kill or automatically reschedule it.

### 3.3a PSI-aware admission and freeze response (design only; not implemented, FD-059)

Add a sustained-pressure gate at the target host: do not admit while
`psi.memory.full_avg60_pct >= 10`. An unreadable, malformed, or null
`psi.memory.full_avg60_pct` is unknown capacity and refuses with 75; it is never
treated as zero. This rule reads the kernel `full avg60` line from
`/proc/pressure/memory`. The `avg60` window is the kernel field named by the
contract; the proposed 10% threshold is operator-specified and has no supporting
measurement yet. Short `avg10` spikes do not trip this sustained gate. Existing
checks still apply independently: `policy.psi_refuse_some_avg10` is a configured
threshold against the different `psi.memory.some_avg10_pct` metric, and the
shipped full-`avg10` admission check remains in force. Neither is replaced by
the new full-`avg60` rule, so an `avg10` spike can still be refused by an
existing check.

The predictive part should estimate whether a candidate's added RAM demand
would move the host's memory `full avg60` across the refusal line. Compare the
job's declared or measured RAM need against `memory.available_bytes` (MemAvailable)
plus a separately defined, non-overlapping reclaimable-memory amount, then
estimate the resulting PSI. Once calibrated, refuse a job predicted to push
full `avg60` to or above 10%; if a required input is unknown, return 75 before
starting rather than manufacturing a prediction. Its proposed inputs are:

| input | source and unit | present state |
|---|---|---|
| `psi.memory.full_avg60_pct` | `/proc/pressure/memory`, percent | Collected in `host-facts/2`; the sustained refusal reads the current value. |
| `memory.available_bytes` (MemAvailable) | `/proc/meminfo:MemAvailable`, bytes | Collected in `host-facts/2`. |
| `memory.reclaimable_bytes` | A separately defined host kernel memory source, bytes | Not collected today. Its source and a non-overlapping definition are unresolved; MemAvailable already estimates reclaimable headroom, so blindly adding another reclaimable total could double-count it. |
| `resources.declared_peak_bytes` | Submitter's lane declaration, bytes | Optional request hint; it may be null and is not a cgroup limit. |
| measured job warm use | cgroup v2 `memory.current - memory.stat[inactive_file]`, bytes | The existing §3.3 sample; it can provide a lane/version p90 after measurements exist, but no PSI prediction currently consumes it. |

Today the host has no `memory.reclaimable_bytes` fact and no fitted mapping
from declared or measured RAM need to host PSI. The class charge and warm
samples feed the current fit calculation, not a prediction of PSI. Therefore
the predictive rule cannot refuse work yet; a missing input or model remains
unknown and must not be synthesized.

Before enforcement, run the predictor in observation-only mode. For each
candidate admission, record the available-memory and PSI inputs, the declared
need and available measured warm-use estimate, plus the host/job identity and
concurrent admitted work. Compare its predicted PSI change with the actual
`psi.memory.full_avg60_pct`, `memory.available_bytes`, and measured job warm use
observed as the job starts, progresses, and completes. Evaluate the relationship
against those real outcomes before allowing it to refuse; retain the observations
so a model revision can be compared on the same data. No model form, coefficient,
safety margin, sample count, or acceptable error bound is selected: each would
need measurement before it could become a constant.

Open design questions are the non-overlapping meaning and source of reclaimable
memory alongside MemAvailable; whether declared peak or measured warm p90 should
drive a prediction when both exist or either is missing; how to separate one
job's effect from host-wide PSI under concurrent work; and what observations and
prediction error justify enabling refusal. Until those are answered from host
measurements, only the explicit sustained full-`avg60` rule above is proposed,
and neither it nor the predictor is implemented by C3a.

**Freeze, thaw and move response (design only; not implemented).** Estimate the
actual host RAM shortfall from the current memory PSI and refault evidence,
`memory.available_bytes` (MemAvailable), and the working sets of admitted jobs.
The facts currently available are `psi.memory.{some,full}_avg{10,60}_pct` from
`/proc/pressure/memory` (percent), `memory.available_bytes` from
`/proc/meminfo:MemAvailable` (bytes), and each job's warm working set from its
cgroup v2 `memory.current - memory.stat[inactive_file]` (bytes), as sampled in
§3.3. `host-facts/2` does not collect refault counters, and it does not carry a
per-job working-set fact; the counter source and its fact shape remain open.
Use measured changes in PSI, refaults and available memory against those
working sets to identify a concrete shortfall; no formula or threshold is
selected. This is not a policy to freeze all dev work or every job believed to
cause pressure: conditions were acceptable before the last job was admitted.
Freeze one eligible job only when its freeze is estimated to cover the
shortfall. If it covers the shortfall, prefer the most recently admitted job.
`wings` and the game server are never freeze candidates.

Thaw only after the cause changes: a job finishes, or measured freed memory
covers the frozen job's working set. A pressure value dropping below a number
does not trigger thaw. Thaw one job at a time and reassess before thawing another.

A freeze preserves the job and its admission charge. It does not automatically
stop, tear down, or reschedule the job. A move depends on its progress, whether
its partial results can be resumed, and whether another host is idle; fleetd
decides whether to move it. If every other host is busy, keep it in a
`frozen-queued` state on its current host until fleetd can decide again.

This policy refines §3.3's sentence that `memory full avg10 >= 3` stops new
admission without killing a running job, and §3.5's sentence that progress
judges stalls while pausing during thrashguard freezes. A pressure freeze is a
separate, selected-job action; a verified frozen interval remains excluded
from stall accounting. It also clarifies §4.3's `On cancel or TERM` thaw and
stop sequence: that sequence is for an explicit cancel or TERM, not an
automatic consequence of a pressure freeze. The §3.4 `frozen_units > 0`
admission refusal remains an admission gate and does not choose a freeze or
thaw target.

### 3.4 One admission mechanism on the host (B3), exit-75 parity

The agent keeps one host book for all local and direct callers. Fleetd keeps one fair global queue
and asks candidate hosts for immediate admission; a rejected host request leaves no host waiter.
The controller host joins the same book through its host agent (D14); its state survives devcontainer restart. Local `run-gate`
uses the `lease` verb for accounting, while fleetd-placed execution there is excluded until a host cockpit runner exists.

```
/var/lib/fleetd-agent/       # systemd StateDirectory; root:root 0700, survives agent/devcontainer restart
  admission/
    admitted/<job_id>.json   # charge and CPUs; executor holds flock while alive
    completed/<job_id>.json  # terminal group/digest, no charge; written before admitted removal
    leases/<lease_id>.json   # local gate charge and immutable container identity
    queue.lock              # one admission decision at a time
    waiting/<job_id>.json    # durable waiter state
    waiting/<job_id>.lock    # waiter liveness flock
    locks/<id>.lock          # executor flock for an admitted job or lease
```

- **Host decision** (inside `queue.lock`): reread `host-facts/2`, the gate-slice ceiling, disk
  floors and the active book; the capacity provider supplies fresh warm samples for live jobs and leases. A missing warm sample is zero,
  keeping the full charge reserved. `legacy_jobs_provider` lists jobs without an `EXIT` marker on each decision under this same lock;
  each listed job carries `legacy_charge_bytes` and `legacy_planned_cores`. A directory without `EXIT` remains charged. `ciu-direct` may wait on this host. `fleetd-placed` sends `wait: 0`:
  admit now or return 75 with the facts that refused it, then fleetd tries another host.
- **Disk floor.** Below either configured checkout-root or Docker-root free-space floor, ciu-direct
  waits in its host queue and fleetd gets an immediate 75 to try another host.
- **Fleetd placement.** A candidate must pass load, memory PSI, warm-set RAM, gate-slice and
  disk-floor checks. `/1` rejects every non-null `cpu_set` with 2, so this version does no CPU-set
  placement; a later API may use one to restrict eligible hosts and executor affinity. The
  controller host is not a fleetd execution candidate yet. Choose greatest remaining warm-set RAM
  fit, then planned-core headroom, lowest memory-full avg10, greatest disk headroom and stable
  `host_id`.
- **Queue order and reservation** (N3/MF1): order `merge-gate`, `dev`, `gate`, `campaign`; within a class use submit time and
  group-prefixed id. The reservation holder is the first aged, statically eligible waiter in that order, whether it fits or not. While one
  exists, only the holder itself and a `merge-gate` with strictly higher priority may be admitted. Recompute the holder when facts or
  eligibility change. The holder starts as soon as it fits: it passes earlier-sorting waiters that are not aged and yields only to an
  earlier fitting `merge-gate`.
- **Bounded calls (package C, FD-054).** Capacity, legacy-job and Docker inspection calls run under `queue.lock`, which has no timeout.
  Package C bounds each call and maps a timeout to 75 or `ERROR`.
- **Priority authorization.** Host `/etc` policy lists allowed priorities for each credential principal. Missing policy or absent/unknown priority refuses (2).
  A valid disallowed value clamps only down, to the most urgent allowed class that is no more urgent than requested; if none exists, refuse (2). Fleetd clamps for its caller before forwarding; the host clamps again for fleetd's credential. `requested_by` never selects priority.
- **Authenticated group prefixes.** Package C's host policy must prevent overlapping group prefixes: with only a textual prefix check,
  group `ops` can claim an id such as `ops-ci-run-1` that also appears prefixed by group `ops-ci`.
- **Local run-gate lease (MF3).** On the controller host, `lease` uses this same queue lock, warm charge, planned-core and priority
  checks; `frozen_units > 0` refuses with 75 (N4). Run-Gate supplies a fresh `container_name` in `{container_name, footprint, planned_cores, priority}`; a class
  impossible on this host or a name collision refuses with 2. The host waits in the same priority/reservation queue under `queue.lock`; disconnect before grant withdraws the waiter.
  On grant it writes a pending lease under `queue.lock` using the requested `lease_id`. Run-Gate creates the named container stopped with label `fleetd.lease=<lease_id>`, sends its immutable Docker ID on the same connection, and starts it only after the agent
  verifies name, ID and label and acknowledges the bind. The client persists lease/container identity before start; write or bind
  failure means no start. Today Run-Gate starts with `docker run -d --name` and writes a best-effort in-flight record afterwards
  (`run-gate-project/run_gate.py:18424-18437,18566-18589,18644,7350-7389`); package H replaces that order. The devcontainer remains the
  executor; fleetd does not run this lease as a host job. Explicit revoke may tombstone a lease before its container disappears but keeps its charge. Reconcile is an observation: it releases only on `NO_SUCH_OBJECT` (and a pending lease also needs an acquirable flock); `PRESENT`, `ERROR`, other answers and exceptions leave the record unchanged. A fitting lease waiter whose inspector keeps returning `ERROR` can block every later-sorting waiter until its admission wait expires; this is a known limit (FD-052).
- **Reservation parity.** With continuous small arrivals, a large head job starts in the first
  admission cycle after it becomes feasible; later jobs cannot consume its reserved RAM or cores.
- **Wait expiry.** On timeout, withdraw a fleetd waiter from its global queue or a ciu-direct waiter under `queue.lock`.
  The agent confirms a submitted job never started before returning 75; if started, the client follows it. Network error is 3.
- **Charge lifetime.** The executor holds the per-job flock for the whole job. Kernel process
  death releases it; the reaper still stops and collects any surviving lane before deleting the
  record. Completion first writes a terminal group/digest record, then removes the admitted record; a crash between those writes resolves as completed. Terminal retention belongs to the host agent package C. No heartbeat, `lost_after_s` or probe-before-reclaim is needed.
- **Relation to `run-job` and today's slots.** Keep the legacy controller slots, workroot and
  runner snapshot while any client uses the legacy path. The agent charges every legacy host job
  without `EXIT`; a later flag-removal change deletes those old mechanisms. `dev` also moves into
  this host book, so its runs and remote-gate lanes no longer meet only in PSI
  (`scripts/netcup/runner/README.md:1072-1073`; `scripts/netcup/runner/run-job:13-26`).
  Rejected: keep local flocks *and* add host admission — two disagreeing mechanisms is FD-042 by
  construction.
- **Two schedulers on one host (FD-042).** Every installed remote runner submits to the host book; local controller-host
  `run-gate` leases against that same book. A host without an installed runner
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

Nothing in §3 sets a run wall-clock limit. Wait limits cover admission; progress judges stalls and excludes a verified frozen
interval from stall accounting. A pressure freeze preserves its job and charge and does not itself stop, move or reschedule it.
D13 removes today's timeout (`scripts/controller/dev:159`; operator 2026-11-26);
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
explicit cancel or TERM, deregister and thaw it if frozen, send TERM to the lane, and wait for its process
or container to stop. Only then collect once and verify the archive. Collection is idempotent;
store errors do not rewrite the lane verdict.

If deregister/thaw fails, retry without teardown. If TERM fails or the lane remains alive, collect
no artifacts or store; keep the checkout, containers and charge, report `stopping`, and retry.
When it exits, collect every flushed record. There is no wall-clock limit on this wait: stopping a
run never silently discards its last writes.

### 4.4 Bind the verdict to the commit

The agent verifies request `sha`; a host-written checkout line after `rev-parse HEAD` equals that
sha (`scripts/netcup/runner/remote-gate.py:682-685`); exactly one `lane-verdict` for the job id,
the exact-lane anchored summary, and Run-Gate process status agree; and every non-null
`lane-started` container id matches the inspected full ID and checkout mount. Require
`run-gate.*` labels for a Run-Gate-started container lane; for `mode = "exec"`, the request's
persistent runner name selects the check and labels are not required. The checkout-mount check
applies in both modes. It writes `result.json` from those facts only. A
missing or mismatched fact is transport/refusal, never PASS. Rejected: binding to the client's
`--commit` echo (`scripts/controller/remote-gate.sh:283-285`).

### 4.5 Host loss, agent restart and reboot

Today `run-job` detaches with `nohup` and writes `pid`/`EXIT` (`scripts/netcup/runner/run-job:16-26`); remote-gate has `--detach` and `collect` (`scripts/netcup/runner/remote-gate.py:1090-1093,1304`).

- **Controller lost, host alive:** the separate executor scope and its job id remain live; the
  client reattaches by `job_id`. `run-gate` already writes an in-flight container record
  (`run-gate-project/run_gate.py:7350-7399`), but the agent owns the job lifecycle.
- **Agent restart or downgrade:** the executor is outside the agent cgroup and continues. On
  startup the reaper scans every job without an `EXIT` marker and reconnects to its transient
  scope. It does not signal or restart a live executor.
**Local lease failure table:**

| event | required behavior |
|---|---|
| socket absent/refuses; bad policy/impossible class; frozen/wait expiry | no create or start; exit 3, 2, or 75 respectively |
| client dies before grant | remove its connection-bound waiter; no lease or container |
| client dies after grant, before create/bind | tombstone first, release the connection flock, then inspect the exact name; only confirmed absence releases the charge; a present container remains charged but cannot bind or start after tombstoning |
| complete bind frame does not arrive before `lease_bind_timeout_s` | use the monotonic deadline from grant; trickled bytes do not extend it; tombstone first, release the connection flock, then inspect the exact name; return an error and close; only confirmed absence releases the charge |
| delayed create after revoke | unique name and lease id stay tombstoned; stopped container has no bind ack, is removed when observed, and never starts |
| EOF after bind or agent restart | keep charge while ID exists; startup and periodic reconciliation inspect the exact ID; reload StateDirectory; no start until same lease ID/ID reconnects, then continue or reattach |
| container gone / Docker inspect error | events and periodic reconciliation inspect exact ID; only explicit no-such-object releases; `PRESENT`, `ERROR`, exceptions, and other answers retain charge (current Run-Gate rule: `run-gate-project/run_gate.py:7601-7645`) |
| name collision or reuse | refuse collision; bind/release use lease ID plus immutable ID, so a reused name cannot steal or release a prior lease |

The periodic reconciliation interval is required host policy with no default;
each exact inspector call is bounded by `query_timeout_s`. There is no §4.5
`agent.toml.lease_bind_timeout_s` is required positive host policy with no
default. It starts as the grant is sent and bounds receipt of the complete bind
frame, including its header and body. Expiry runs the same tombstone and exact
name reconciliation as a pre-bind disconnect: confirmed absence releases the
charge, while a present object or inspector error retains it. A delayed create
after tombstoning cannot bind. After the bind frame completes, the deadline no
longer applies; EOF after bind retains the charge until exact-ID reconciliation.
This closes FD-060.

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
  principal and auth group; unknown peers are denied. `/etc` policy gives that principal its allowed priorities. The socket GID
  only grants connect permission.
  Sessions share UID/GID, so cannot be distinguished:
  they share repo/job scope and can act on one another's jobs. Check `requested_by.client` against
  configured labels; version/run are audit only. No request field grants identity or group; `priority` is only a request and is
  clamped by host policy. Apply the same group/repo/job checks as SSH. Accept only v1 agent verbs, including `lease`, no shell. No SSH key exists
  for this host. Missing/refusing socket returns 3, with no SSH or local-admission fallback; retry using
  job id and digest.
- **Forced command (FD-009).** Authorized keys map to group and allowed priorities on the host
  (`fleetd/KNOWN_ISSUES_TODO_BACKLOG.md:895-905`; enrollment belongs to CIU-218). Replace today's
  client `guarded()` wrapper (`scripts/netcup/runner/remote-gate.py:237-246`) with server-side checks:
  derive identity from the key/peer group, never `requested_by`; validate schema, repo, relative
  project/store paths inside the checkout, resources and argv. The remote forced-command allow-list rejects local-only `lease`.
  Use a server-side run-gate allow-list,
  a host per-repo env profile (RG-319), and §2.2's closed fetch names. No shell string is executed.
  Forced command narrows SSH access; it does not sandbox submitted code.
- **Trust boundary.** Submit runs arbitrary commit code beside the host Docker socket; submit access
  is root-equivalent, and neither socket permissions nor forced command sandbox the job.
- **Scope and records.** Facts, jobs, submit, cancel and fetch stay within the authenticated group's
  repos. `requested_by` is checked as an audit label, never authorization. Records/logs contain no
  secret, key path, host address or token; job dirs use `umask 077` (`scripts/netcup/runner/run-job:3-15`).

## 6. thrashguard and hostobs

Thrashguard emits `thrashguard.freeze`/`thrashguard.thaw` to hostobs, not to its own reader socket
(`docs/design/thrashguard/DESIGN.md` §6a, “Frozen-time accounting — one path: thrashguard → hostobs →
consumers”). Under hostobs M3, `annotate` is served on the OBSERVER socket at
`/run/hostobs/observer/hostobs.sock`, group `hostobs-observer`; the control socket answers
`unknown-verb` until hostobs M6. The `events` API is hostobs M3 work. The admission package
supplies only the library; the host agent must connect to hostobs and enforce this intake when it
is wired (`.controller-state/hostobs/PLAN-M3-carve.md` §6.5).

Each event's issuer is exactly `uid:<n>` from the accepted peer credentials. The associated pid,
comm and cgroup are an unverified peer label, not an issuer identity; no pidfd attribution is used
(`.controller-state/hostobs/PLAN-M3-carve.md` §6.5). The pidfd wording in hostobs WIRE is pending
alignment with that plan. Count `thrashguard.*` events only when that uid is 0 or the configured
thrashguard uid. Never authorize from the peer label or event subject fields.

1. **The agent reports freeze state** in `host-facts/2` as §3.2's four names (`guard_present`,
   `guard_state`, `config_digest`, `frozen_units`), `frozen_units` from a complete hostobs event
   window and `guard_state` from the guard's runtime file (`fleetd/src/fleetd/facts.py`). The M3
   event ring is RAM-only: when the requested window is not provably complete, report
   `frozen_units` as unknown, never 0. Freeze timing, owner, reason and `freeze.queued` remain not
   collected until thrashguard P4 (`docs/design/thrashguard/DESIGN.md` §6b, “fleetd: freeze state
   for the estate snapshot”).
2. **The agent registers each started job** with thrashguard's registrant socket when present: one
   `register {unit: <job_id>, container: <id from §2.2's event>, score, priority,
   owner: "fleetd", run: <job_id>}`, heartbeat on its tick, and deregister before removal
   (`docs/design/thrashguard/DESIGN.md` §6c, “fleetd: the cross-host placer and registrant”). There is
   one registrant: run-gate owns jobs before this package; the agent owns them after migration.
   Run-gate stops registering an agent-owned job.

**Freeze rule [N4].** Only `guard_state: enforce` requires `frozen_units`: a positive count holds
new admission; an unknown count returns 75; a known zero permits admission. An unknown guard
state also returns 75. An absent guard (`guard_present` false), or state `off` or `report`, does not block. Do not pause or kill
running jobs. Exclude a proven frozen interval from progress-stall accounting; an incomplete
hostobs window is unknown and never zero. This also works without fleetd: ciu-direct uses the
same host queue.

## 7. Migration, packages, parity

**Migration order.** Ship run-gate's host-owned event file, verdict agreement, OOM score and freeze-aware stall watch (RG-321 first), then hostobs event APIs and persistent cockpit `MemoryLow`. Ship the host agent with StateDirectory, tmpfiles socket directory, client group and devcontainer mount; enable after the socket lifecycle probe. Then package H switches local `run-gate` to lease admission; next switch `remote-gate.py`, `dev`, fleetd campaigns and the dr runner. Keep legacy paths for rollback; remove slots/workroot/snapshot after every client migrates. ciu-direct uses runner queue when installed, else own admission (D12).

**Parity rows.** Each must fail independently: (a) unique exit codes (preflight 4→2, prerequisite 76→79, budget 4, OOM 76, store 77/78, queue 75; 73 remains reserved by fleetd's runner); (b) forge a perfect anchored PASS line in lane output and prove the event/process still report the real FAIL; separately exit before `finish` with a forged line and prove no `lane-verdict` event exists; (c) cancel stops before collecting every flushed record; (d) expired wait withdraws and confirms never-started before 75, and cannot start later; (e) an aged reservation holder reserves RAM and planned cores, fitting or not; nothing else is admitted before it except a strictly higher-priority merge gate; (f) unreadable PSI/meminfo refuse (`scripts/netcup/tests/test_remote_gate_repos.py:597-600`; `scripts/netcup/tests/test_remote_gate_memory.py:91`); (g) footprint/slice/disk limits; (h) the emitted container and image ids equal Docker's report for this run's container (the labeled one Run-Gate started, or the requested persistent runner for `mode = "exec"`), and a same-named foreign container is never emitted; consumer acceptance also checks the checkout mount, and `run-gate.*` labels for a Run-Gate-started container; (i) per-job OOM and adjustment 500; (j) identity/path/argv/artifact validation; (k) bundle replay and sha binding (`scripts/netcup/tests/test_remote_gate.py:227,322`); (l) admission, terminal and lease records survive reload; a dead executor's released flock does not release its charge, and the reaper preserves charge through cleanup after restart or reboot; (m) socket identity/no shell/restart/missing-socket behavior; (n) a lease reserves under `queue.lock`, enforces N4 freeze refusal, binds immutably, and tombstones without releasing until explicit no-such-object reconciliation. The host-agent integration still must prove stopped-container bind-before-start, pending-client death, collision handling, reconnect and release on confirmed disappearance.
Contract gates also cover same-digest idempotence and major-version refusal.

**C3b-1 executor behavior for parity rows (b), (c), and (k).** A PASS is
refused unless the request sha matches the host-authored checkout line, one
closed-shape `lane-verdict` event agrees with the exact-lane anchored summary
and Run-Gate process status, and every reported container id is inspected as a
full id with the job checkout mount (plus Run-Gate labels where required).
Cancellation on a host with the guard absent sends TERM and waits for the lane
and container to stop before collection; when guard presence is known but no
merged deregister/thaw callable exists, cancellation stays in `stopping` with
a named refusal. Bundle size and sha256 are checked before checkout, then the
host checks out the requested sha and records `rev-parse HEAD` in its own log.
Event binding, archive checks, bundle verification, stop-signal dispatch and
container waiting have injected-interface unit coverage; live
systemd/Docker/cancellation acceptance remains a host probe.

**Rollback.** Each client owns one `legacy|agent` flag. The agent keeps legacy jobs charged during overlap. Downgrade within a major is safe because executors run in separate scopes; major mismatches refuse and minor additions are capability-advertised. Flag and legacy-path removal is separate.

**Package split, size and gate.**

| package | size | gate |
|---|---|---|
| A. run-gate host-owned event file and verdict agreement, OOM adjustment, frozen-time stall watch (RG-321 first) | M | run-gate lane contract gate |
| B. hostobs event APIs + host-setup persistent `MemoryLow` | M | hostobs contract gate; host setup render gate |
| C. `host-facts/2`, host service/state, socket group/mount, scopes/book, forced command, `have`/`put` | M-L | fleetd gate + socket lifecycle probe; enrollment depends on CIU-218 |
| D. result ranges and remote-gate client switch; retain legacy slots/snapshot | M | remote-gate parity rows + live lane |
| E. `dev` payload, pty, workspace lock, OOM adjustment; remove `max_s` | S-M | dev contract gate + live dev job |
| F. fleetd campaigns and dr runner; R2 store carry (RG-298) | S-M | campaign gate + live batch |
| G. remove legacy flags, slots and runner snapshot after all migrations | S | compatibility gate |
| H. local run-gate lease client, stopped-container bind/start, durable reattach record | M | lease protocol and restart parity gate; depends on A and C |

Sizes are effort estimates, not lane time limits. No package adds a wall-clock limit to a run.

## 8. N2 decision

No P-fd-05 questions remain open: Q1, Q2, Q6, N1 and N3–N5 are in §§0 and 3; Q3–Q5 are in §§2–3.

| # | decision | reason |
|---|---|---|
| N2 | Host fleetd serves the same API on a mounted Unix socket; peer credentials map the shared devcontainer UID/GID to one controller principal. Remote hosts keep SSH and forced commands. | Host-side facts and persistent admission state must survive devcontainer restart. |

## Known follow-ups

- **FU1/FU2 closed by package A2.** The host-owned file crosses the `docker exec` boundary;
  verdict acceptance now requires agreement between Run-Gate's event, anchored summary, and
  process status, including the no-`finish` case.
- FU3 (A): keep thrashguard registration with one owner.
- FU5 (C+F): carry fleetd campaign candidate lists.
- FU6 (C+F): persist global queue and share aged reservations.
- FU7 (F): resolve unknown submit outcomes on the same host.
- FU8 (C): define remote executor user and privileges.
- FU9 (C): give host waiters connection liveness.
- FU10 (C): refine socket activation, mount mode and peer GID.
- FU11 (C): frame SSH requests and bound idle connections.
- FU12 (C): define job-id grammar and stored-group checks.
- FU13 (F): define aging threshold and pin reservation target.
- FU14 (C): configure legacy-job charge.
- FU15 (C): distinguish guard enforce mode from report/off.
- FU16 (C): key progress by assay lane.
- FU17 (C): define CPU-set and per-CPU accounting.
- FU18 (C/G): reconcile remaining documentation contradictions.
- Follow-up (C/F): fleetd-placed controller-host jobs wait for a host cockpit runner.
