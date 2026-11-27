# fleetd specification

## Scope

The package contains strict versioned contracts, an injectable read-only facts
collector, a deterministic RAM-first planner, and a one-shot lite campaign
controller. The campaign controller uses the existing identity-checked hx
wrapper and runner jobs; the separate P-fd-05 C3a host-agent entry point serves
the host protocol described below.

## Host admission core (P-fd-05 C2)

`fleetd.admission` is a standard-library-only host admission library. Package
C3a wires its book to the host protocol, but does not create containers or ship
a real executor. The current planner and runners do not call it yet.

`HostCapacity` is the typed projection of the admission-relevant `host-facts/2`
fields. Each input is a `Fact[T]`: a known value has no reason; an unavailable
value must carry a non-empty reason. A required unknown fact keeps that reason
and returns 75; the library never substitutes a guessed capacity, policy, or
guard value. The capacity provider supplies fresh warm samples for live jobs
and leases for each admission decision; a missing sample is `current_j = 0`,
so the full charge remains reserved. Stored observations never reduce a
reservation. Policy is host-owned and includes footprint charges, planned RAM
and CPU caps, PSI and disk floors, allowed priorities, and required `aging_s`.
`legacy_charge_bytes` and `legacy_planned_cores` are required when the legacy
job provider lists any job. The closed `facts.toml.policy` schema accepts all
three as optional positive inputs: seconds, bytes, and cores. `/2` always
carries `aging_s`, `legacy_charge_bytes`, and `legacy_planned_cores`; an
unconfigured value is null with a reason, while a configured value carries its
unit and source. `/1` is unchanged.
Host config must gain these keys only after installing a fleetd release that
accepts them. The current policy values for legacy jobs are required when used;
`legacy_planned_cores` is positive.

`AdmissionBook(state_dir, clock, waiter)` makes one serialized decision at a
time. The caller supplies the state directory (production uses
`/var/lib/fleetd-agent/`). Under `queue.lock`, it reloads admitted records and
waiters, calls the required `legacy_jobs_provider` and capacity provider,
checks live facts, writes the admitted record atomically, and holds the
executor flock before releasing the lock. The executor holds that job's flock
for its lifetime; `resume_job` reacquires it after a dead holder, and `handoff`
closes only the parent descriptor after an executor inherits it. A corrupt or
unreadable charged record refuses further admission with 75 and keeps its
charge. Dead waiting entries are removed only after their waiter flock can be
acquired; they cannot reserve capacity.

The RAM fit is the minimum of physical RAM after host reserve and existing
charges, gate-slice ceiling after warm use and unconsumed reservations, and the
per-host planned-byte cap after existing charges. Static eligibility refuses
an impossible class or planned-core request with 2. Live admission also checks
load, memory PSI, required checkout and Docker disk floors, cgroup v2, and the
freeze rule. Each listed legacy job without an `EXIT` marker is charged
`legacy_charge_bytes` and `legacy_planned_cores`; its directory remains charged
until an `EXIT` marker removes it from the required provider's list. Unknown
needed policy values return 75 with the key named in the reason; an expired wait
appends its last refusal reason. `legacy_planned_cores` must be positive when
configured. A known but invalid policy value (for example `aging_s` of 0)
refuses with 2. A capacity
provider that raises returns 75.

The capacity provider, the legacy job provider and the inspector run under
`queue.lock`, whose lock has no timeout. C3a bounds each callback with the
required positive `agent.toml.query_timeout_s`: capacity and legacy-list
timeouts become 75, and inspector timeouts become `ERROR`. One callback can
time out without retaining `queue.lock` or creating an unbounded worker backlog
(FD-054).

The host agent's legacy-job provider scans the required absolute,
fully-resolved `agent.toml.legacy_jobs_dir`. It opens the path from `/` one
component at a time with `O_DIRECTORY | O_NOFOLLOW`; a symlink or non-directory
component refuses admission. Every entry is charged unless it safely contains
a regular `EXIT` file. Symlinks, non-directories, unreadable entries, and odd
names remain charged. IDs are `legacy-` plus the first 40 lowercase hex digits
of SHA-256 over `os.fsencode(name)`, so another entry's insertion or removal
does not change an existing ID. The provider lists the pinned directory twice,
keeps entries judged live in the first pass, evaluates names found only in the
second pass, and reopens the configured path to require the same device and
inode. A scan error, changed root, or more than `MAX_LEGACY_JOB_ENTRIES`
(65,536) entries refuses admission with 75. This is a point-in-time observation,
not a writer protocol: `run-job` does not take `queue.lock`, a job created after
the final listing is not seen, and the jobs-directory owner is trusted to write
its own `EXIT` markers. Completed directories therefore accumulate and must be
pruned before the entry limit. The legacy `run-job` writer creates `EXIT` only
after the command exits; a host reboot can therefore leave a directory charged
until someone removes it manually (FD-064).

The lease object inspector invokes exactly `/usr/bin/docker --host
unix:///var/run/docker.sock inspect --type=container <target>` with argv, never
a shell, cwd `/`, and environment `PATH=/usr/bin:/bin`,
`HOME=/nonexistent`, `LC_ALL=C`. It accepts an exact container name or full
lowercase 64-character ID; a lowercase hex target of length 12 through 63 is
refused at lease request validation with `container_name must not be 12-63
lowercase hexadecimal characters` and returns
`ERROR` if passed directly to the inspector. Loading existing lease state
does not apply the new-name refusal. It reports `PRESENT` only when one complete
object matches that exact name or ID. `NO_SUCH_OBJECT` requires a non-zero
Docker exit, no object output, and Docker's explicit no-such-object or
no-such-container response for that target. Missing Docker, timeout, output
over 4 MiB on either stream, invalid UTF-8, partial or unparseable output,
ambiguous output, and mismatched identities are `ERROR`. The bind identity
inspector accepts a full ID only, never an ID prefix, and returns Docker's
name, full ID, and labels only when that exact ID is inspected. The admission
call's outer bounded wait uses `query_timeout_s` to bound its answer. The
provider's monotonic deadline starts before process creation and bounds Docker
output collection and direct-child reaping after creation returns. Process
creation itself cannot be cancelled: it may finish after the outer call has
refused, in which case the provider immediately attempts to signal the new
child process group with `SIGKILL` and makes a bounded reap attempt before it
returns `ERROR` (FD-066). Each output stream is drained with a 4 MiB cap;
expiry or a cap breach also attempts group cleanup before reaping. On the
normal result path, after both pipes reach EOF, the provider waits for the
leader's pidfd to become readable without reaping it. Cleanup runs in
`finally`: after any child-created outcome, including `Exception` and
`BaseException` paths before EOF, it attempts `SIGKILL` on the process group
while the leader is unreaped, then attempts the bounded reap even if signaling
raises. `ProcessLookupError` means the group is already empty; another signal
or reap failure returns `ERROR`. The pidfd, selector, stdout pipe, and stderr
pipe are each closed independently; a close failure is reported as `ERROR`
after the remaining closes are attempted. Any non-`Exception` interruption
from the body or cleanup is re-raised without wrapping; if several occur, the
first in occurrence order takes precedence over ordinary failures from the
body, signal, deadline calculation, reap, post-reap check, or closes. When no
interruption occurs, ordinary errors keep the existing precedence. A success
requires both pipes at EOF, exit observed before the deadline, and an exit
status obtained by that reap.

Every `host-facts/2` policy object carries `aging_s`,
`legacy_charge_bytes`, and `legacy_planned_cores`. An unconfigured key is
`null` with a reason; a configured key carries its unit and source. These are
required producer keys even when the host does not configure them. `/1` is
unchanged, and host config must add these keys only after a release accepts
them.

The shared wait queue clamps priority to the authenticated group's host policy
and orders by priority, submission time, then group-prefixed job id. The first
aged, statically eligible waiter in that order holds the reservation, whether it
fits now or not. While one exists, nothing else is admitted except the holder
itself and a strictly higher-priority `merge-gate`; the holder passes
earlier-sorting waiters that are not aged and yields only to an earlier
fitting `merge-gate`. Without a holder, a candidate waits only for an
earlier-sorting waiter that fits. A request that cannot ever fit refuses with
2. Expiry withdraws the waiter and confirms that it never started before
returning 75. The injected clock controls queue aging and wait expiry; there is
no run wall-clock limit.

Job ids use `^[A-Za-z0-9][A-Za-z0-9._-]*$`, with a maximum length of 128
(`MAX_JOB_ID_LENGTH`), and the authenticated group must match the id prefix.
Status and cancellation compare that group with the stored record. The /1
request digest covers its contract fields except `job_id` and
`requested_by.version`. A non-null `shard` or `resources.cpu_set` is refused
with 2; both remain reserved in /1.

The textual group-prefix check would be ambiguous if group `ops` and group
`ops-ci` both existed. C3a's closed agent policy refuses a set of identities
with overlapping `-`-separated group prefixes (FD-053).

`complete` writes a durable terminal record containing `job_id`, group, digest,
and completed state, with no charge, before removing the admitted record. If a
crash occurs between those writes, the terminal record wins and the job is
completed. Replaying the same id and digest returns that terminal state without
a handle; another digest or group is refusal (2), and an unreadable terminal
record returns 75 for that id. An admission decision reads a terminal record
only while the same id still has an active record, so unrelated finished history
cannot block admission. This package does not prune terminal records; package C's
host agent owns their retention policy.

Lease records use the same queue and capacity charges. They persist pending and
bound state and bind once to an immutable container id. The caller supplies the
group-prefixed `lease_id`. Explicit revoke tombstones the lease but keeps its
charge. Reconcile is an observation: it tombstones and releases only for an
explicit no-such-object answer; a pending lease also requires an acquirable
flock. `PRESENT`, `ERROR`, other answers, and inspector exceptions leave the
record unchanged. Released lease names remain tombstoned and cannot be reused.
A pre-bind disconnect first tombstones the lease, releases the connection flock,
then inspects the exact container name. A delayed create cannot bind to that
tombstone. When an inspector is configured, the agent reconciles pending and
bound records at startup and at the required positive
`agent.toml.lease_reconcile_interval_s`; bound records are inspected by exact
container ID. The outer bounded inspector call uses `query_timeout_s`; Docker
output collection and reaping use the provider deadline described above, while
synchronous process creation cannot be cancelled and may finish after the
outer call refuses (FD-066). An unknown/error result retains the charge. The
required positive `agent.toml.lease_bind_timeout_s` bounds the complete bind
frame from the grant on the monotonic clock; trickled bytes do not extend it. Expiry uses the same
tombstone, exact-name inspection and charge-release rules as a pre-bind
disconnect. The client receives an error reply and the connection ends. This
deadline does not apply after binding.
A fitting lease waiter whose inspector keeps answering `ERROR` can block every
waiter that sorts after it, in lower priority classes too, until its admission
wait expires; a waiter holds no charge. This is a known limit (FD-052). The package does
not perform Docker operations; the host agent must enforce bind/start and
reconciliation around this accounting API.

Only an enforce guard requires `frozen_units`: a known positive count holds
admission with 75, unknown count returns 75, and zero permits admission. An
absent guard (`guard_present` false), `off`, and `report` do not block; an
unknown guard state returns 75. This applies to jobs and leases.

The closed exit-code table assigns 79 to prerequisite-missing; 73 is reserved
by fleetd's runner. Code 75 is a missing required fact, a held admission, or a
wait withdrawn before start; code 2 is a request or static-eligibility refusal.

## Host agent protocol and admission providers (P-fd-05 C3b-2a)

`fleetd.agent_policy`, `fleetd.agent_protocol`, `fleetd.agent_verbs`,
`fleetd.agent_admission`, `fleetd.agent_providers`, `fleetd.agent_server`,
`fleetd.agent_client` and `fleetd.agent_entry` use the Python standard library
only. The `fleetd-agent`
entry point reads the closed host-owned `agent.toml` policy and serves a Unix
socket path, an inherited listening descriptor, or one ssh forced-command
request on stdin/stdout. It does not execute a shell command. C3b-2a wires the
legacy-job and Docker inspection providers into the entry point; it does not
include the separate in-flight executor work. This package does not install a
systemd unit or socket group, which belong to C3c.

Local socket peers are authenticated from `SO_PEERCRED`'s UID and primary GID;
the configured supplementary socket group only grants connect permission. The
forced-command group is supplied by its trusted command line. Request fields
never set identity or group. Unknown peer/group and transport failures use
exit 3. Policy loading rejects overlapping authenticated group prefixes so a
group cannot claim another group's `job_id` namespace.

Every connection begins with a four-byte big-endian length and one UTF-8 JSON
object, capped at 64 KiB. Requests are read completely before dispatch; response
frames use the same cap. `put` streams its declared body after the request and
`fetch`/`progress` may stream a body after the response frame. The idle
connection deadline applies while the first request is being read, not while a
submit waits for admission or after a lease is granted. The same framing is
used on the socket and forced-command stdin/stdout. A server error response
carries `error: {exit_code, reason}` inside the normal envelope; `error` has
exactly those fields, with an integer exit code and string reason. Error
responses omit top-level `exit_code` and `reason`. Success responses omit
`error`. The client discriminates only on `error`, and maps a malformed error
object to exit 70. A successful status may carry a non-zero top-level
`exit_code`; it remains job data. The formal envelope schema is
[`agent-response.schema.json`](agent-response.schema.json).

Every ordinary verb uses exactly one request frame and the client half-closes
its write side before dispatch. `put` instead reads exactly its declared body
and then requires EOF. A second frame or trailing request bytes is a transport
refusal. `lease` keeps its write side open for bind and the held connection
lifetime; §2.2 does not require ordinary submits to keep it open while waiting
for admission.

Every answer also carries the `fleet-host-agent/1` schema and the agent's
SemVer `agent_version`. The `facts` answer places the validated
`host-facts/2` document under its `facts` field, preserving that document's
own schema and facts metadata.

`probe` advertises the supported verb field sets and a SemVer `agent_version`.
`AgentClient` refuses a major mismatch while naming both versions. For a
minor/patch/build mismatch it sends the requested verb only if the host's probe
advertises that verb and every request field. A missing or refusing local
socket is exit 3; `AgentClient` has no SSH or local-admission fallback. Use
`fleetd-agent serve --socket-path PATH` for a directly bound socket or
`fleetd-agent serve --listen-fd FD` for socket activation. The path form
requires an existing real parent directory; restart may remove a stale socket
file but never removes the directory. `fleetd-agent forced-command --group
GROUP` applies the identical framing to ssh-provided stdin/stdout.

The CLI omits `lease` from `probe` unless exact-object inspection, the
legacy-job list and container-identity inspection are configured. C3b-2a wires
those runtime providers from the required `agent.toml.legacy_jobs_dir` and the
configured `query_timeout_s`. A direct service call without them refuses with
exit 2.

The closed verbs are `probe`, `facts`, `have`, `put`, `fetch`, `progress`,
`submit`, `status`, `cancel`, and controller-socket-only `lease`. Every field,
repo, path, argv, environment value, artifact and upload name is checked before
side effects. `candidates` is a host-created content-addressed `put` object;
`--only-candidates` is accepted only when it names that authenticated group's
file. Assay progress reads `.assay/progress-<assay_lane>.jsonl` from the job
checkout, resolving `assay_lane` through `hostside.resolve_assay_lane`, and
streams from a byte offset. Lane declarations and progress files are opened
from trusted directory descriptors with no-follow traversal of every path
component. Each progress response is capped at 8 MiB and includes the matching
`next_offset`; clients refuse any response body larger than that cap. Assay
candidate counting reads at most the opened file's size up to the named
`MAX_ASSAY_PROGRESS_COUNT_BYTES` limit of 8 MiB. A larger file returns
`done: null` with a non-empty `done_reason`; smaller files omit `done_reason`.
The read uses the opened-size snapshot, so appends during counting do not extend
the scan. Incremental counting remains FD-063.

`put` requires configured positive `policy.max_upload_bytes` and
`policy.max_upload_store_bytes` facts and a known non-empty `policy.disk_floors`
map; any unavailable input refuses with exit 2. A cross-process flock
serializes the stored-byte scan and reservation. The scan counts regular files
across all upload groups without following symlinks; an unreadable stored file
refuses. The aggregate check is `stored + in_flight + new <= policy.max_upload_store_bytes`,
including re-uploads of an existing object. Before accepting a reservation,
the upload filesystem must also satisfy `available - in_flight - new
>= max(policy.disk_floors.values())`. Each in-flight reservation holds its full
declared size from admission through durable commit (file fsync, rename, and
directory fsync) or, after abandonment, until its partial is removed. Bytes
written or buffered do not reduce the reservation; during streaming they may
also consume disk space, which is conservatively counted twice. The accounting
lock is not held while the body streams. The writer holds a stable owner-lock
file before publishing the reservation record. It writes the record to a
temporary file, fsyncs it, renames it atomically, and fsyncs the incoming
directory. Recovery derives record, temporary-record, and partial names from
the validated token and never from record contents. It removes torn records,
leftover record temporaries, and partials only after acquiring the owner's
lock; a live owner keeps its partial. If the owner lock is missing, inaccessible,
or busy while its record is invalid, `put` refuses with the offending filename
and an operator check. Retention and garbage collection are not implemented
(FD-062).

`agent.toml.max_connections` is a required positive integer with no default. It
bounds ordinary local connections; an excess connection is refused with exit
3. Once a lease has an admission handle, its connection leaves that ordinary
count and is bounded by the lease charge. Server shutdown closes ordinary
connections and returns without waiting for their workers; held lease charges
remain durable for startup reconciliation. Response frame and body writes use
the configured idle deadline after each successful write progress, not a total
transfer limit. A held lease connection and a submit waiting for admission do
not use that deadline.

The executor is an injected `AgentExecutor` Protocol (`start`, `request_stop`,
`read_state`), not a C3a implementation. With no executor, `submit` refuses
with exit 2 and a named `executor unavailable` reason before writing
`request.json` or entering the admission book. The admission and lease verbs
still use the durable C2 book. A live lease connection holds its lease flock
until disconnect; bind/reconnect verify the immutable container ID and
host-inspected lease label. No run or lane wall-clock limit is added.

Submits for the same job ID serialize the `request.json` write, admission,
failure cleanup, and executor start under `jobs/<job_id>/submit.lock`. The lock
is a private regular file opened without following symlinks. Status and cancel
do not acquire it, so an admission waiter cannot block those verbs behind the
submit transaction.

Status of a durable `fleet-admission-terminal/1` record returns only its
recorded `job_id`, `state: completed` and `completed_at`; it does not invent a
verdict, exit code or other executor result. Cancel of a finished job refuses
with exit 2. Executor-backed terminal details remain for C3b (FD-061).

## Contracts

- `host-facts/2` carries CPU, load, Geekbench, memory, PSI, gate-slice, disks,
  cgroup v2, KSM, kernel-interface, control-plane, guard, freeze, policy,
  queue, job, and tool facts. Measured and configured values are wrapped as
  `{"v": value}` or `{"v": null, "reason": "..."}`; `reason` appears only
  with a null value. `schema`, `agent_version`, and `collected_utc` are plain
  metadata. `fleet facts validate` accepts `/1` during this one-release
  migration, but collection and every planner/campaign consumer use `/2` only.
- `work-request/1` carries the lane, commit and image identity, floors, demand,
  planned and pending candidate IDs, and optional carry-over provenance.
- `assay-merge-manifest/1` is owned by assay (B544, and B565's
  `owner-manifest` policy). It maps each planned candidate ID to one owner
  store; fleetd no longer validates it.
- `host-facts/2.guard` carries `guard_present`, the digest of the guard
  configuration, the guard's own runtime `guard_state`, and `frozen_units`.
  Guard rules are owned by thrashguard (TG-1); fleetd does not validate or
  apply them. An unavailable freeze-event source remains null with a reason.

Unknown fields are rejected at every object boundary. Validators identify the
field path and the missing, extra, or invalid value. See the [consumer guide](CONSUMERS.md)
for examples and the [design guide](DESIGN-GUIDE.md) for invariants.
Every candidate ID in candidate tables and work requests is the lowercase
64-character SHA-256 digest emitted by native Assay R2.

### Lane demand

A TOML file passed to `--demand` carries a `[lanes.<lane>.demand]` table (released
Run-Gate does not yet accept that key in its own file; see RG-276) with these
required keys: `unit`, `instance_memory`, `unit_memory`, `unit_seconds_ref`,
and `efficient_upper_bound`. `unit_memory_high` and `baseline_seconds` are
optional. `baseline_seconds` is a host-independent estimate of the Assay
baseline wall time used to extend a batch's first progress window; when absent,
fleetd uses measured baselines from completed batches on that host. Units are
the closed set `candidate`, `worker`, and `run`; the CLI converts memory strings
using binary k/m/g suffixes. This R2 candidate planner requires
`unit = "candidate"` and refuses the other valid schema units because their
placement meaning is not defined yet. The high estimate cannot be lower than
the median estimate. Unknown keys are refused.

### Merge ownership

`assay-merge-manifest/1` has `planned_ids` and an `owners` object. Assay owns
the contract and validates it as B565's `owner-manifest` policy (folding B544):
the planned ID set must exactly equal the owner keys, and only the named
owner's record is authoritative. Fleetd no longer parses or validates the
manifest.

## Facts collection

`fleet facts collect [--root DIR]` reads `/proc` and `/sys` beneath the
selected root, reads configured filesystem capacity with `statvfs`, reads the
fixed control-plane unit list's `MemoryMin` properties with
`systemctl show -p MemoryMin`, reads required versioned configuration at
`/etc/fleetd/facts.toml`, reads the Geekbench record from its configured
host-owned path, and emits `host-facts/2` JSON. The config declares
`overcommit`, `oversubscribe_max`, PSI refusal threshold, control-plane slice,
optional per-profile `profile_reserve_bytes`, and host source paths; missing
required policy fails. Older config without `profile_reserve_bytes` uses the
768 MiB compatibility profile constant. `HOST_ROLE` is read from
the configured host-setup environment file, an existing host-owned source, and
is not shadowed in fleetd policy. `host-facts/2` accepts `dedicated-dev` and
`shared-prod` as host roles; missing, duplicate, empty, or unknown values are
represented as `null` with a reason. Optional facts whose source is absent are
also represented as `null` with a reason. Collection is read-only: it never
invokes Geekbench or writes sysfs/cgroup files.

New host-owned settings are optional so rollout does not invent policy:
`[gate_slice].slice`, `[paths].checkout_root`, `[paths].docker_root`, and
`[policy]` keys `host_reserve_bytes`, `planning_cap`, `max_footprint`,
`max_planned_bytes`, `footprint_charges_bytes`,
`priority_classes_by_principal`, `disk_floors`, `max_upload_bytes`,
`max_upload_store_bytes`, and `admission`. Missing settings become null facts
with a source-specific reason.
`policy.host_reserve_bytes` is emitted as `memory.host_reserve_bytes` so this
quantity has one name. These additions do not change the current planner's
inputs or placement formulas: it does not require `memory.host_reserve_bytes`
or `disks`; later host admission owns those checks.

The collector exports only the benchmark's matching model and online-CPU
count. It accepts measured Geekbench 6/7 records with their matching reference
score, and never uses a table fallback. Guard configuration is merged lexically
from the main TOML file and its drop-ins. `guard_present` is the measured
presence of the configured guard file; it is a known boolean when that path can
be checked. `config_digest` is the SHA-256 digest of the configuration bytes,
including drop-ins. The guard runtime state is read from the owner's configured
runtime-state source, never inferred from rule validation. The freeze-event
source is not available yet, so `frozen_units` is null with a reason
(`thrashguard-absent` if no guard config is present, otherwise
`not-collected`). KSM opt-in shim facts stay null unless their paths are
configured. `memory.total_bytes` is raw `MemTotal`; load averages come from
`/proc/loadavg`. Gate-slice warm memory is `memory.current` minus
`memory.stat[inactive_file]`. The collector reads the parent control-plane
slice's `memory.min`, and `/2` carries it as `control_plane.memory_min_bytes`
(null with a reason when the slice is absent or the value cannot be read). The
planner ignores that field, in `/2` as in older `/1` inputs: no reserve term
comes from the parent slice. The bounded
`CONTROL_PLANE_SERVICE_UNITS` list in `fleetd.contracts` supplies one
`service_memory_min` entry per service, and each entry may carry the unit's
systemd `load_state` (`null` with a reason when it was not queried). `0`,
`infinity`, unset, and failed queries are not reported positive values. A
fixture root with no injected systemd readers still emits all six service rows;
each unavailable `memory_min_bytes` and `load_state` is null with its own
reason. `/2` does not emit `control_plane.service_memory_min_reason`; that
optional-table shape is retained only by the `/1` compatibility validator.

The planner resolves ONE term per listed service and sums those terms:

- `reported` — the service's own positive `MemoryMin`, at or below its
  documented per-service sanity ceiling;
- `default` — that service's documented floor from
  `CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS` (the measured table the Debian
  installer writes, see below), used when the service reports no positive value
  or a value above its ceiling;
- `absent-optional` — `0`, only for a service in
  `CONTROL_PLANE_SERVICE_OPTIONAL_UNITS` whose `load_state` is `not-found` or
  `masked`. An unknown or unqueried load state is never treated as absent.

The fixed list, with the documented default each service falls back to and
whether the Debian installer declares the unit optional:

| Unit | Default | Optional |
|---|---:|---|
| `ssh.service` | 10 MiB | no |
| `docker.service` | 210 MiB | no |
| `containerd.service` | 92 MiB | no |
| `systemd-journald.service` | 24 MiB | no |
| `cgprofiled.service` | 4 MiB | yes |
| `mdt-psi-guard.service` | 5 MiB | yes |

`cgprofiled.service` and `mdt-psi-guard.service` are the optional units; the
other four are mandatory. The total therefore never falls below the sum of the
values actually reported, and a missing mandatory fact is replaced by that
service's documented floor rather than by zero.

By design (FD-035), host-facts validates the TYPE of a service's
`memory_min_bytes` and not its magnitude, because the contract describes what a
host reported and the sanity bound is planning policy: planning refuses an
implausible value (above 4 x that service's documented default, or a total above
twice the documented floors) with `reserve_reason`, so `fleet facts collect` on a
misconfigured host can still emit a valid-looking document that `fleet plan`
then refuses.

`reserve_source` is `service-memory-min` when every term was reported and
`per-service-default` otherwise (with `reserve_missing_services` naming the
units that were not reported). The fixed profile constant
(`profile_reserve_bytes`, 768 MiB when the config has no override) is used
**only** when no per-service fact exists at all — every row is a failed query
(no positive `memory_min_bytes` and no `load_state` in any row), which is the
failed-collection case — and `reserve_source` then says `profile-constant`. One answering service (a positive
value or a load state) is enough for per-service terms, so the other rows
contribute their defaults. A reported value refused above its ceiling counts as
`default` and is listed in `reserve_missing_services`. A per-host plan carries `reserve_services`: one
`{unit, source, bytes}` row per listed service, in the fixed unit order, so the
plan evidence shows exactly which term was used for which service. It is
omitted in the `profile-constant` case, where no per-service term exists.
Every plan host row also carries `psi_refuse_some_avg10`, the host's refusal
threshold (a number from 0 through 100, copied from `host-facts/2.policy`), which
the campaign supervisor compares with the probe's pressure readings before it
starts a batch (FD-022). The field is additive to `fleet-plan/1`.

A reported value above its per-service ceiling (4 x that service's documented
default) or a total above twice the documented floors is refused, with the
reason on the host plan (`reserve_reason` and `reason`); it is never clamped,
quietly replaced, or summed. The documented floors total 345 MiB. A test parses
the installer's source and its documented table and fails when either drifts
from fleetd's copy.

Reserve selection does not depend on `MemTotal`, `MemAvailable`, measured RSS,
or any legacy parent-slice `memory_min_bytes` value. Missing
`memory.total_bytes` still refuses planning because physical capacity
cannot be derived. Calibration of the
profile constant from measured p95 under load remains open as
[FD-007b](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-007b-calibrate-profile-reserve-from-measured-p95-under-load).
`fleet facts validate FILE`
validates a serialized record without contacting the host.
The collector and planner do not read `dev-gates.slice/memory.max`; the
planner uses the design's physical-RAM budget and leaves slice `MemoryMax` as a
downstream safety net. The missing host-owned ceiling fact is tracked under
[FD-004](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-004-host-slice-memory-ceiling-is-not-part-of-the-planner-facts).

## Planning

`fleet plan` consumes a candidate table, host facts plus per-host correction
factors, and a Run-Gate lane demand table. RAM determines eligibility and
candidate routing. Expected charges are compared with the overcommitted sum
budget; individual candidates are always bounded by physical RAM after the
control-plane reserve. Lists prefer a low/high mix and batch around four times
the host worker capacity. Retry candidates with recorded memory evidence are
assigned alone to the host with the largest physical alone-capacity.

The planner uses eligible hosts' Geekbench single-core scores for normalized
time and ETA, never multi-core scores. A refused host does not change the ETA
reference. One bounded correction factor is read per host, with
the design default `1.0`; factors are not learned or pooled by lane in this
package. The host-level EWMA and self-predicted-sample rule are tracked under
[FD-005](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-005-learn-the-per-host-correction-factor).
Identical inputs produce byte-identical JSON and stable command
ordering.
When no host is eligible, `time_reference.reason` says so separately from the
case where eligible hosts have no Geekbench records.

The planner's host-set input requires explicit other-running rows and rejects
hosts with unknown role, shared-prod role, PSI above its configured threshold,
or unknown physical lane memory, CPU, or PSI facts. Each service's reserve term
is resolved per service (reported, documented default, or zero for an absent
optional unit), so a partial fact set names the units and still plans; an
absent control-plane slice does not refuse the host. Worker
capacity is the CPU-derived worker count
after other work, bounded by the lane's efficient upper bound. MVP-B-lite uses
one campaign slot per host because concurrent selected runs would share a
Run-Gate lane instance and Assay store; lifting this accepted capacity limit is
tracked under
[FD-006](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-006-lift-the-manual-one-slot-per-host-and-j-cap).
It computes:

- reserve_h = sum of one term per listed service (reported value, that service's
  documented default, or 0 for an absent optional unit);
- reserve_h = control_plane.profile_reserve_bytes (768 MiB when absent) only when
  no per-service fact exists at all;
- P = memory.total_bytes - reserve_h;
- O = other running expected charges + campaign_slots × instance_memory;
- B = P × overcommit - O;
- b = B / W for W worker slots;
- cap = min(B - (W - 1) × ram_low, P - O - (W - 1) × ram_low);
- cap_alone = P - O.

Each wave's expected charge must be no greater than B. Each individual
candidate must fit cap_alone, including alone and retry batches. Retry goes
only to the host with the largest cap_alone and must also fit that host's B.
Overcommit applies to expected sums and never enlarges the physical cap.

Candidate and host inputs use candidate-table/1 and fleet-host-set/1. Candidate
measurements resolve by own id, same-file median, lane median, then declared
demand. Candidate timing follows the same precedence after normalization to
the common Geekbench major, exact version, and baseline. If timing is
incomparable, placement still uses RAM and the output includes an ETA reason.
When no comparable ETA exists, assignment balances normalized reference work
per worker as a placement tie-break; it does not claim that the hosts have
comparable speeds. An old measurement whose host is absent from the host set is
ignored for timing and recorded with a fallback reason.
The batch target defaults to four times W and is configurable with
--batch-multiplier. The command renderer emits one invocation per host batch,
with `--jobs` no greater than W, and runs batches serially within each copied
host section. Selected-only runs normally return Run-Gate exit 4
(`BUDGET_EXCEEDED`) because unselected candidates remain pending. A batch that
actually exhausts its budget uses the same exit code; the generated script
accepts 0 or 4 and stops on other codes, and the whole-lane closeout runs any
candidates still pending. `efficient_upper_bound`
must not exceed the lane's Assay `jobs_max`; the standalone planner cannot
derive that value until the Run-Gate campaign client is implemented.

This package deliberately omits the Run-Gate campaign-plan client command;
the integration contract is tracked under
[RG-276](../../run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md#rg-276-the-run-gate-campaign-plan-client-is-not-implemented).

## Campaign execution

`fleet plan` adds an optional `execution` object only when it is run from a
clean Git project that has a Run-Gate configuration:

```json
{
  "schema": "fleet-plan-execution/1",
  "commit": "<full lowercase Git commit>",
  "project": "<relative project path or .>"
}
```

`fleet run PLAN.json --transport hx --campaign ID --state-dir DIR` requires
this object. It validates the plan, creates a `fleet-campaign-state/2` file,
probes the planned hosts, and starts available batches up to the plan's
per-host campaign slot limit. A subsequent `fleet run` with the same campaign
and plan re-adopts existing jobs by name. The same campaign ID is bound to the
original commit, project, and lane. A changed plan stops jobs whose names are
no longer wanted and creates names from the replacement candidate lists.
The optional `execution.image_digest` names the tester image (`sha256:` plus
64 lowercase hex digits) used for measurement evidence; shard-store merging
and its image-identity policy are assay-owned.

Transport is limited to `hx` in this MVP. `Transport.run(host, argv_or_script,
timeout)` returns `(rc, stdout, stderr)`; `put` and `get` handle small
home-relative files. `HxTransport` invokes the existing
`scripts/controller/hx.sh` wrapper, which resolves host aliases from local
configuration. The wrapper path is `--hx PATH` first, then `FLEETD_HX`, then the
source checkout's own `scripts/controller/hx.sh` (always made absolute, so the
launch never searches `PATH`); an installed fleetd with none of them exits 3 with
one line naming `--hx` and `FLEETD_HX` (FD-023). Exit 3 on a campaign verb therefore
means "not installed or no hx wrapper"; it is a different condition from the
stale-cursor exit 3 of `fleet scope`. The probe and `FakeTransport` call the
same stdlib-only `fleetd.hostside` functions; the fake materializes its files
in a temporary host home. No SSH keys, addresses, provider credentials, or
host aliases are stored in fleetd source or tests.

The runner job name is
`<campaign>.<host>.s<slot>.b<n>.<list8>`. `list8` is the first eight hex
characters of SHA-256 over the batch's sorted candidate IDs with one newline
per ID. Start probes the job table and full list digest before starting a new
runner job. The uploaded ID file lives below the selected project in the
persistent checkout. The first detached command writes `list.sha256` before it
invokes `run-r2-campaign.sh`; if the controller or session ends before that
write, probe recomputes the digest from the uploaded ID file. An existing
matching name is a no-op; a mismatched digest refuses. Probe sends a
compressed table of job names, one entry per (campaign, host, commit, project,
lane) with an ID-file path template the host fills from each name, never
candidate arrays or per-job records. One probe is a single remote shell
argument, which the kernel caps at 128 KiB; the table costs a few bytes per job
(a 150-batch host probes with about 6 KB; FD-014). The table still grows
linearly, by about 11 bytes per non-settled job, so one host's probe reaches the
128 KiB argument ceiling at about 11 000 non-settled jobs. A job is settled
(`probe_settled`) only when a probe row reported it `EXIT` with its progress
window closed, so its verdict records and baseline are already stored; a state
file written before the flag existed keeps the earlier rule (ended with
`progress_end_offset` recorded). A host that becomes unreachable or errors drops
its stored pressure reading, so a start never rests on a stale one (FD-014,
FD-022). Settled jobs are not probed again. The probe also returns the host's `/proc/pressure/{memory,io}` `some avg10`. It resolves the Assay lane from the checkout's Run-Gate
declaration and reads the plain run's `progress-<assay-lane>.jsonl` under the
project `.assay` directory; it does not require the optional instance
`progress-file` pointer. Each runner job records the progress file's byte size
at its own start and end. Probe attributes events only when their candidate ID
is in that job and their byte range falls between those markers. The job's own
verdict supplies candidate outcomes resumed from Assay state, including cases
with no new progress event. Probe returns running/EXIT/missing state, list
digest, candidate progress count, progress file modification time, per-job
progress bounds, verdict outcomes, and artifact paths. Each measured event also
carries `profile.metrics.memory_peak_bytes`; collection merges that value into
the first-owned record when it sees the same event twice.

`fleet status ID` refreshes state and reports per-host reserve bytes, reserve
source, any services without a positive `MemoryMin`, per-batch state, done/total
counts, candidates per second, blended ETA, and `STALLED`/`LOST` markers. ETA
blends the planner's Geekbench estimate with the observed rate
when both exist. `fleet tick ID` performs one scheduling step: adopt current
jobs, detect exits and stalls, handle host loss, and start the next available
batch. Neither command runs a polling loop.

The stall threshold is `max(900 seconds, 3 × p95 predicted candidate time for
this batch on this host, 3 × p95 observed candidate time on this host)`. The
prediction uses each candidate's `time_ref_seconds`, the host's matching
Geekbench baseline and single-core score, and that host's `f_h`; when scores
cannot be compared, fleetd uses the candidate's reference time, widened (FD-025):
while such a host has observed no finished candidate its predicted candidate
time is 3 × the unscaled reference (`NONCOMPARABLE_FIRST_WINDOW_FACTOR`), so the
first stall window is `max(900 seconds, 9 × reference)` plus the baseline; the
first observed candidate time on the host replaces the widening. Observations
and predictions are never pooled across hosts. The declared `baseline_seconds`
or p95 of completed baseline measurements on this host is added to the first
window, before any candidate has finished in the batch. Later windows measure
silence since the last candidate finished without adding the baseline again.
The threshold never acts as a campaign wall budget. The stall clock pauses
while the host is unreachable or slow. The loss clock starts at the first failed
contact this controller observed (`unreachable_since`, persisted), not at the last
successful contact (FD-020): more than 1800 seconds of continuous failed contact
marks the host lost, and one failed probe after a long gap does not. A probe that
times out (exit 124) is reported as `SLOW` instead of `UNREACHABLE`; it feeds the
same clock and counts toward `LOST` only after the same 1800 seconds. ssh exit 255
is `UNREACHABLE`. A recovery before the threshold keeps every batch. Any answer
from the host ends the episode, including an `ERROR` one (a garbled probe, a
digest mismatch): the silent time is added to the stall pause and the clock is
cleared, so the next failed contact starts a fresh one and never inherits an old
instant. A host that is already `UNREACHABLE` when this clock takes over a
campaign state keeps its old `last_contact`-based clock and may go `LOST` on the
first new tick, as before.
All work owned by the lost host, including batches that were queued but never
started, is marked lost and considered for re-planning. Only unfinished
candidate IDs are sent to the reachable planned host with the largest
`cap_alone_bytes`, using the existing planner placement functions. If no
target is available the work is deferred. A candidate that does not fit the
target (larger than its alone cap or sum budget) never drops the others (FD-017):
the placeable ones are queued and the unplaceable ids stay in the campaign's
`deferred_requeues` with the planner's reason and a state warning until a larger
host is online; `collect` reports them `missing` with that reason otherwise.
`replan_pending` returns `(batches, unplaceable)` with `{id, reason}` rows. Idle planned hosts are probed even
when they had no initial batch, so they can become requeue targets. A host
the planner refused (shared-prod, unknown role, PSI, missing facts) that owns
no job of the campaign is never contacted by `run`, `status`, `tick`, or
`collect`. Dispatch re-checks PSI on every probe
(FD-022): a host whose memory or IO `some avg10` is above the plan host row's
`psi_refuse_some_avg10` (strictly greater, as in the planner), whose PSI cannot
be read, or whose plan records no threshold starts no new batch; `status` names
the reason in the host row's `start_deferred` (null otherwise). Running batches
are never stopped for pressure, and starts resume on the next `tick` once the
reading falls. An `EXIT`, `missing_job`,
or stalled batch with no finished candidate from its own start becomes
`FAILED`, is not re-queued, and appears in status and collection with its
failure reason and exit code when available. Re-queue remains unlimited for batches that made progress. A stalled
batch that made progress keeps its `STALLED` marker and creates one replacement
batch. A returning host's still-running duplicate may finish too; collection
chooses one owner. Once a complete refresh proves every unfinished candidate
finished elsewhere, fleetd stops the duplicate. An incomplete retry or failed
stop keeps that return fact pending for a later complete refresh. No lease or
renewal is used.
Known limit: if the lost source host returns and re-adopts a deferred candidate
(FD-017), and a third, larger host then comes online, the retry excludes only the
source host's in-flight jobs and may start the candidate there as well; the
returning host may finish it too. The collector counts the second result as an
ignored duplicate; no logic prevents the second start.
Transport or contract errors mark only the affected host `ERROR`; a tick still
supervises the other hosts and reports the error in status. A host that stays
in `ERROR` is aged, never lost (FD-026): the host row carries `error_since` (UTC
seconds), `error_seconds` and, from 1800 seconds, a `warning`; the campaign state
records one warning per episode. There is no automatic `LOST` and no re-queue,
because an `ERROR` host (contract or identity refusal) may have live batches and
re-queueing would duplicate work: the operator decides. The clock is decided once
per command from the host's final status: `run` and `tick` after the start step
(so an error raised after a good probe, such as a digest mismatch, an invalid
record or a refused start, cannot restart it), `status`, `collect` and
`measurement` at the end of their refresh. An `ERROR` host without a clock gets
one, an `ERROR` host keeps its first instant, any other status clears it. Known
limit: a refused start is only detected by `run` and `tick`; a `status` between
two ticks probes the host successfully and so ends that episode, and the next
refused start begins a new clock.

Before uploading a batch's ID file, start reads the probe's `checkout_ready`
flag for the missing job: the persistent checkout must be a non-symlink
directory with `.git` and the selected project directory. Otherwise start
refuses and the batch stays queued; the upload would otherwise create
`checkouts/<commit>/` as a plain directory that the supervisor refuses and the
runner's checkout sync cannot replace. Probe reports a home-relative
progress path computed lexically, so a symlinked `checkouts` directory works;
if the path cannot be expressed below the remote home it reports `null`
instead of failing the probe.

Every `run`, `status`, `tick`, and `collect` holds an exclusive non-blocking
lock on `<campaign>.lock` in the state directory for its whole
read/modify/write interval. An overlapping command fails with "campaign ...
is busy" and changes nothing.

The only persistent controller state is plain JSON under `--state-dir`,
written to a temporary file, fsynced, and atomically renamed. The
`fleet-campaign-state/2` record stores monotonic and UTC timestamps plus the
controller boot ID. On the same boot elapsed time uses the monotonic clock;
after reboot it is rebased from UTC. A backward UTC step is clamped to zero,
which delays a detector; a forward UTC step counts as downtime and can advance
detection after reboot. A restarted controller reconstructs remote state by
probing deterministic job names.

## The default R2 campaign

Multi-host, multi-instance R2 is the default: one lane, several worker
instances per host, shards sized from measured units, and a declared scope.
Three commands implement it (strict shard merging is assay's, B565's
`strict-shard` policy); each reads and writes versioned documents and none
contacts a host. See [the R2 default page](R2-DEFAULT.md)
for the worked examples.

`fleet scope` produces the candidate-id set. Its default mode
(`changed-lines`) selects candidates whose start line is an added line of
`git diff -U0 <previous release tag>..<commit>`; a renamed file is judged
whole because candidate identity folds in its path. An empty selection is
`nothing-to-judge` with a reason, exit 4, and `--out` refuses to write an empty
file. The opt-in `rolling-whole-target` mode judges, within one `slice_size`
budget, the candidates of files changed since they were last judged and then
the next slice of a persisted sweep whose state document is
`fleet-r2-scope-cursor/2`; it is keyed by **file content** (git blob id) and by
a location-stable candidate key `(path, operator, ordinal)`, never by Assay's
candidate id. A release that edits a target file judges that file whole and
leaves every other file's marks intact, so the sweep covers the target across
releases instead of restarting per edit; an unrecorded plan shape for a path,
abandoned marks and left paths are accounted as due again or retired, never
dropped. A cursor for another lane is `stale-cursor` (exit 3), a hand-edited
cursor fails its `state_digest`, and a `fleet-r2-scope-cursor/1` cursor is
refused with its migration (`--rebaseline`, one new pass from the current
content). A project declares its mode in a committed
`r2-scope.toml` (`schema_version = 1`, `[r2]` with `mode`, optional
`slice_size`, `cursor`, `note`, `reserve_percent`). `reserve_percent` (an
integer 0 to 100, default 25) reserves that share of every slice, rounded up and
capped at `slice_size - 1`, for the non-changed remainder in path order
(never-judged and the pending candidates of a half-judged file), so a
target edited every release by more than `slice_size` candidates still reaches
its remainder; `0` restores the changed-first slice. The scope document's
`slice` object reports `reserve_percent`, `reserved_candidates`,
`deferred_changed_candidates` and `deferred_changed_files` (every changed file
whose candidates did not all fit, with `candidates` and `deferred_candidates`)
so nothing deferred is silent (FD-038). No scope file means the default, so an
uncommitted project keeps its current behaviour. The producer refuses (exit 2)
a checkout whose HEAD is not the named commit or whose tracked files carry
uncommitted changes, because the plan describes the working tree and the diff
the committed one. A rolling plan row whose path the judged commit does not
describe is refused (exit 2) rather than keyed to content the producer cannot
read (FD-033).

`fleet sizing` derives instances per host, shard count, each shard's budget and
the outer bound from the plan's own host budgets, the plan demand's per-worker
unit (`worker_memory`, `worker_cpu`) and fixed per-instance charge
(`instance_cpu`, `instance_memory`), and the per-candidate seconds and memory.
Those units are optional in the demand contract so existing files keep
validating; sizing refuses a plan that does not declare them, by name. Every
number records its `basis` (`measured` or `declared`). A measured shard budget
is derived from the per-candidate MEAN (a shard's wall time is the sum of its
candidates) and a per-shard margin `max(1.10, 1 + 1.645 x cv / sqrt(n))` from
the measured spread; `--margin` is the explicit override, and the document
states `seconds_statistic` (`mean`, `median` or `declared`) and `margin_source`
(`explicit`, `derived` or `default`; `derived` is also set for an older
median-only measurement, whose margin is then just the 1.10 floor). The derived
margin has no upper bound, and the top-level `margin` reports only the floor,
the per-shard `margin` is what applied. The budget is an ESTIMATE: an instance that
reaches it stops, and its remainder continues in a later instance (FD-031).

`fleet measurement ID` writes `fleet-r2-measurement/1` from the same
owner-attributed records `collect` selected: one row per planned candidate with
its `outcome_bucket`, `elapsed_seconds` and `memory_peak_bytes`, and a summary
of medians over `killed` and `survived` candidates plus the additive
per-candidate sum fields (`cost_candidates`, `cost_seconds_sum`,
`cost_seconds_mean`, `cost_seconds_stdev`, `cost_seconds_max`, over killed,
survived, `budget_exceeded` and `hung` candidates; the document stays
`fleet-r2-measurement/1` and an older document without them still loads and
sizes from its median). The validator cross-checks the fields when
`cost_candidates` is present: `cost_candidates` is 0 exactly when the four
values are `null`, the mean and the standard deviation do not exceed the
maximum, and the sum equals the mean times `cost_candidates` within 1e-6 s per
candidate plus 1e-6 s (the recorder's six-decimal rounding). A missing number is
`null`, never zero. `memory_peak_bytes` is the cgprofile observer's
`resource-profile/1` metric; Assay 12 removed its own sampled
`peak_rss_bytes`.

Strict shard merging is assay-owned (`--merge-shard-store`, driven by
run-gate's `--merge-shards N`; B565's `strict-shard` policy). Fleetd no longer compares shard identities or emits a
merge document.

## Collection

`fleet collect ID` fetches known host progress streams and per-batch verdicts,
then writes `fleet-campaign-result/1` JSON plus a Markdown report. Candidate
ownership is selected by earliest valid finished-record timestamp; stable
host/job order breaks ties or missing timestamps. Event timestamps come from
host clocks, so clock skew can change which duplicate is selected, while the
one-owner rule and candidate outcome remain unchanged. Other finished copies are
ignored and counted. The result includes one candidate row each, killed,
survived, timeout, crashed, equivalent, and missing totals, duplicate count,
per-host wall time/rate, failed batches, and missing artifact details. Assay
`budget_exceeded` and `hung` outcomes map to `timeout` only when process-tree
evidence does not say the candidate never started. Never-started candidates
remain `missing` with a distinct reason. Zero-progress failed batches list
their failure reason and exit code when available for missing candidates. Older records in
the append-only progress file are excluded by each runner job's own byte-range
markers; resumed outcomes come from that job's verdict.

MVP-B-lite reports candidate outcomes but does not merge Assay state stores or
create a strict whole-lane verdict. Assay owns `assay-merge-manifest/1` and
strict shard combination (B544/B565); Run-Gate's pass-through is RG-277, and
the baseline cache is tracked by Assay B542. The restricted forced-command host entry is CIU-218;
the fleetd daemon is FD-011; campaign-progress registration is thrashguard's
TG-1 / FD-012 work. Those are explicit later work, not implicit behavior of
these commands.

The campaign client passes only the generated `--jobs` and
`--only-candidates` arguments to Run-Gate; it does not forward arbitrary
options such as `--base` or `--carry-over-main`. Project paths must match
`run-r2-campaign.sh`'s relative-path contract and cannot contain `..` anywhere
in a component. hx timeout/unreachable results mark a host unreachable;
identity-check and guard refusals (exit 97/98) remain fail-closed transport
errors and do not silently become host-loss signals.

## Command line interface

The `fleet` command is registered with the shared `cli-extended` library
(unified-adoption CX-D1): the grammar is declared once in `fleetd.cli.build_cli`
and the library owns help, the identity banner, the shared output controls, the
exception boundary, the JSON-mode result stream and the `skills` verbs.

- **Entry point.** The console script is `fleetd.entry:main`, which imports only
  the standard library before it imports the CLI. A host without the declared
  dependency therefore gets one actionable line and exit 3 instead of a
  `ModuleNotFoundError` traceback; any other missing module is still an error.
  `python -m fleetd` remains an entry point for source checkouts.
- **Version.** `CliIdentity.resolve(distribution="fleetd")` is the only version
  source; `fleetd.__version__` reads the same resolver. There is no literal
  fallback, so an uninstalled source checkout reports "cannot determine the
  fleetd version" (exit 3) rather than inventing a number. The version itself is
  derived at build time from the `fleetd-v*` tag.
- **Exit status.** `0` success; `2` a refused input (a contract or transport
  refusal is mapped to `CliFailure(exit_code=2)` in exactly one place,
  `fleetd.cli._domain`); `1` an unexpected failure, printed as one
  `[ERROR] unexpected ...` line with `--traceback` for the stack; `3` the CLI's
  own dependency or version source is missing.
- **Output.** Results go to stdout, diagnostics to stderr. Without `--json`
  each command prints its documented, indented and key-sorted document; `--json`
  prints one compact JSON document instead. The documents and their schemas are
  unchanged by the registration (`fleet-plan/1`, `fleet-campaign-status/1`,
  `fleet-campaign-collection/1`, `host-facts/2`).
- **Agent skills.** `fleet skills install|check|list|uninstall` come from the
  library. The skill source is packaged inside the wheel at
  `fleetd/skills/fleetd-campaigns/SKILL.md`, the one source of truth; there is no
  checked-in `.claude/skills` tree. `install` writes a stamped copy plus an
  integrity sidecar, so `check` can distinguish *current*, *modified* and
  *absent* without guessing.

### Host install and the wheel layout

`fleet facts collect` is the command a managed host runs, so the wheel — not a
source checkout — is what a host needs. `fleetd`
declares `cli-extended>=0.5.0` as a real wheel dependency (never vendored, never
put on `sys.path`); 0.5.0 is the library revision the registered gate image
installs, so the host and the gate run the same contract.
Collection also requires installed `fleetd` distribution metadata from that
wheel. In a source-only checkout without `.dist-info`, `fleet` itself refuses
with exit 3 and `cannot determine the fleetd version`; no agent version is
invented.

`cli-extended` is published on GitHub Releases only (the bare name is unclaimed
on PyPI), and the fleetd wheel does not vendor it, so both wheels come from the
same directory.

On a managed vbpub host, use the runner's install script, which does this in a
venv under a documented prefix and never touches the system Python:

```sh
wheel_dir=/var/cache/host-wheels
fleetd_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/fleetd-<version>-py3-none-any.whl.sha256")
cli_extended_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/cli_extended-0.5.0-py3-none-any.whl.sha256")
sudo bash "$HOME/runner-scripts/install-fleetd.sh" \
  --wheel-dir "$wheel_dir" \
  --fleetd-version <version> --fleetd-sha256 "$fleetd_sha256" \
  --cli-extended-version 0.5.0 --cli-extended-sha256 "$cli_extended_sha256" \
  --check
sudo bash "$HOME/runner-scripts/install-fleetd.sh" \
  --wheel-dir "$wheel_dir" \
  --fleetd-version <version> --fleetd-sha256 "$fleetd_sha256" \
  --cli-extended-version 0.5.0 --cli-extended-sha256 "$cli_extended_sha256"
fleet --version
fleet facts collect > host-facts.json
```

Both wheels are verified against the SHA-256 values you pass before anything is
written, the venv lives at `/opt/fleetd/<version>/venv`, and
`/usr/local/bin/fleet` is a symlink to that venv's console script. `--check`
verifies the inputs and reports whether the install is already current without
writing (exit 0 current, exit 1 not), so it is safe to run first. A rerun with
the same wheel hashes is idempotent. The script refuses a symlinked install
path, an incomplete existing venv, and a non-symlink file at the command path,
and it never installs into the system Python.

The same wheels can be installed by hand into a venv of your own:

```sh
python3 -m venv /some/venv
/some/venv/bin/python -m pip install --no-index --no-deps cli_extended-0.5.0-py3-none-any.whl
/some/venv/bin/python -m pip install --no-index --no-deps fleetd-<version>-py3-none-any.whl
/some/venv/bin/fleet --version
```

A single `pip install --find-links <wheelhouse> fleetd-<version>-py3-none-any.whl`
gives the same result when the wheelhouse holds both files. Debian 13 refuses
`pip install` into the system Python (PEP 668), which is why the script and the
manual recipe both install into a venv. The host does not
need the repository: the packaged `facts.toml` remains host-owned config at
`/etc/fleetd/facts.toml` (see [the consumer guide](CONSUMERS.md#collect-and-validate-host-facts)),
and every host path the collector reads is absolute.

`fleet run`, `status`, `tick` and `collect` are the *controller's* commands. They
need the identity-checked `hx` wrapper next to the repository checkout (FD-023
tracks the installed-wheel path), so run them from a checkout or a machine that
has one; a host that only collects facts needs neither the wrapper nor a
checkout.

### Verb semantics beyond the catalog

The generated surface region below is produced by `cli-extended surface sync`
and checked by `cli-extended surface check`; every reviewed case in
`docs/cli-review.toml` runs through the real registry in
`tests/cli_review_harness.py`, a hermetic runner (throw-away HOME, private
non-repository working directory, fixture inputs) that pins the grammar and the
refusals that happen before any host is contacted. The catalog is a
characterization, not a specification of behaviour: the behaviour behind a verb
is pinned by its own test module.

| Verb | Behaviour tests |
|---|---|
| `facts collect`, `facts validate` | `tests/test_facts.py`, `tests/test_cli.py`, `tests/test_cli_characterization.py` |
| `plan` | `tests/test_plan.py`, `tests/test_cli_characterization.py` |
| `run`, `status`, `tick`, `collect` | `tests/test_campaign.py`, `tests/test_cli.py` |
| `skills *` | the cli-extended library's own suite; fleetd pins packaging in `tests/test_cli_skills.py` |
| exit-code boundary, entry point | `tests/test_cli_boundary.py`, `tests/test_cli_entry.py` |

<!-- cli-extended-surface:start -->
## Generated CLI surface and semantic review

Executable: `fleet` via `fleet`; built-ins: `help`, `help <verb>`, `version`, `--help`, `--version`. Empty argv: shows help.

Surface schema: `7`; review catalog schema: `1`; library contract: `cli-extended` v1.

The manifest records registered syntax. The review catalog owns expected behavior, effects, rationale, and test references.

### Command routes

| Surface ID | Route kind | Invocation path | Invocation mode | Nested commands | Help summary | Description | Group | Behavior | Confirmation | Synopsis/usage overrides | Delegated metadata | Parser settings | Parser callback | Opaque fields | Parser completeness |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| route:entrypoint:fleet/collect | invocation | collect | command route; parser handles remaining tokens |  | fetch available progress streams and per-batch verdicts and write fleet-campaign-result/1 JSON plus a Markdown report | fetch available progress streams and per-batch verdicts and write fleet-campaign-result/1 JSON plus a Markdown report | MIXED OPERATIONS |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; collect: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:fleet/collect/--hx.type", "option:route:entrypoint:fleet/collect/--state-dir.type"] | complete |
| route:entrypoint:fleet/facts | delegate-group | facts | delegated group; child CLI parses remaining tokens | delegated: route:entrypoint:fleet/facts/collect, route:entrypoint:fleet/facts/validate | collect or validate host facts | collect or validate host facts | EXPLORATION |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | complete |
| route:entrypoint:fleet/facts/collect | invocation | facts collect | command route; parser handles remaining tokens |  | collect host-facts/2 from /proc, /sys, the configured control-plane service MemoryMin properties and the Geekbench record; --root maps the host's absolute config and source paths beneath a fixture root | collect host-facts/2 from /proc, /sys, the configured control-plane service MemoryMin properties and the Geekbench record; --root maps the host's absolute config and source paths beneath a fixture root | EXPLORATION |  | false | {} | [{"behavior": [], "confirmation": false, "description": "collect or validate host facts", "group": "EXPLORATION", "id": null, "name": "facts", "summary": "collect or validate host facts", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; facts: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; facts collect: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:fleet/facts/collect/--root.type"] | complete |
| route:entrypoint:fleet/facts/validate | invocation | facts validate | command route; parser handles remaining tokens |  | validate a serialized host-facts/1 or host-facts/2 JSON file without contacting the host | validate a serialized host-facts/1 or host-facts/2 JSON file without contacting the host | EXPLORATION |  | false | {} | [{"behavior": [], "confirmation": false, "description": "collect or validate host facts", "group": "EXPLORATION", "id": null, "name": "facts", "summary": "collect or validate host facts", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; facts: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; facts validate: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["argument:route:entrypoint:fleet/facts/validate/file.type"] | complete |
| route:entrypoint:fleet/measurement | invocation | measurement | command route; parser handles remaining tokens |  | write fleet-r2-measurement/1 evidence (per-candidate seconds and memory peaks, medians over killed and survived candidates) beside the campaign state | write fleet-r2-measurement/1 evidence (per-candidate seconds and memory peaks, medians over killed and survived candidates) beside the campaign state | MIXED OPERATIONS |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; measurement: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:fleet/measurement/--hx.type", "option:route:entrypoint:fleet/measurement/--state-dir.type"] | complete |
| route:entrypoint:fleet/plan | invocation | plan | command route; parser handles remaining tokens |  | create a deterministic RAM-first campaign plan; from a clean Git project the plan also pins the commit and project path that `fleet run` requires | create a deterministic RAM-first campaign plan; from a clean Git project the plan also pins the commit and project path that `fleet run` requires | EXPLORATION |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; plan: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:fleet/plan/--candidates.type", "option:route:entrypoint:fleet/plan/--demand.type", "option:route:entrypoint:fleet/plan/--hosts.type"] | complete |
| route:entrypoint:fleet/run | invocation | run | command route; parser handles remaining tokens |  | start or re-adopt the named jobs in a fleet plan [mutating; potentially expensive] | start or re-adopt the named jobs in a fleet plan | MODIFICATION | mutating, potentially expensive | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; run: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["argument:route:entrypoint:fleet/run/plan.type", "option:route:entrypoint:fleet/run/--hx.type", "option:route:entrypoint:fleet/run/--state-dir.type"] | complete |
| route:entrypoint:fleet/scope | invocation | scope | command route; parser handles remaining tokens |  | produce the R2 candidate-id scope: the candidates on lines changed since the previous release tag by default, or the next slice of the opt-in rolling whole-target baseline; exit 3 for a stale cursor, 4 for nothing to judge | produce the R2 candidate-id scope: the candidates on lines changed since the previous release tag by default, or the next slice of the opt-in rolling whole-target baseline; exit 3 for a stale cursor, 4 for nothing to judge | EXPLORATION |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; scope: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:fleet/scope/--cursor.type", "option:route:entrypoint:fleet/scope/--out.type", "option:route:entrypoint:fleet/scope/--plan.type", "option:route:entrypoint:fleet/scope/--repo.type", "option:route:entrypoint:fleet/scope/--scope-file.type", "option:route:entrypoint:fleet/scope/--write-cursor.type"] | complete |
| route:entrypoint:fleet/sizing | invocation | sizing | command route; parser handles remaining tokens |  | derive shards, instances per host and budgets from measured units | derive shards, instances per host and budgets from measured units | EXPLORATION |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; sizing: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["argument:route:entrypoint:fleet/sizing/plan.type", "option:route:entrypoint:fleet/sizing/--measurement.type"] | complete |
| route:entrypoint:fleet/skills | delegate-group | skills | delegated group; child CLI parses remaining tokens | delegated: route:entrypoint:fleet/skills/install, route:entrypoint:fleet/skills/uninstall, route:entrypoint:fleet/skills/check, route:entrypoint:fleet/skills/list | install, check, or remove this tool's packaged agent skills | install, check, or remove this tool's packaged agent skills | MAINTENANCE |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | complete |
| route:entrypoint:fleet/skills/check | invocation | skills check | command route; parser handles remaining tokens |  | exit 1 unless every packaged skill is current | exit 1 unless every packaged skill is current | EXPLORATION |  | false | {} | [{"behavior": [], "confirmation": false, "description": "install, check, or remove this tool's packaged agent skills", "group": "MAINTENANCE", "id": null, "name": "skills", "summary": "install, check, or remove this tool's packaged agent skills", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills check: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | complete |
| route:entrypoint:fleet/skills/install | invocation | skills install | command route; parser handles remaining tokens |  | install or update the packaged skills, removing orphans [mutating; dry-run] | install or update the packaged skills, removing orphans | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": [], "confirmation": false, "description": "install, check, or remove this tool's packaged agent skills", "group": "MAINTENANCE", "id": null, "name": "skills", "summary": "install, check, or remove this tool's packaged agent skills", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills install: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | complete |
| route:entrypoint:fleet/skills/list | invocation | skills list | command route; parser handles remaining tokens |  | list packaged skills and their installed state | list packaged skills and their installed state | EXPLORATION |  | false | {} | [{"behavior": [], "confirmation": false, "description": "install, check, or remove this tool's packaged agent skills", "group": "MAINTENANCE", "id": null, "name": "skills", "summary": "install, check, or remove this tool's packaged agent skills", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills list: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | complete |
| route:entrypoint:fleet/skills/uninstall | invocation | skills uninstall | command route; parser handles remaining tokens |  | remove the skills this tool installed [mutating; dry-run] | remove the skills this tool installed | MODIFICATION | mutating, dry-run | false | {} | [{"behavior": [], "confirmation": false, "description": "install, check, or remove this tool's packaged agent skills", "group": "MAINTENANCE", "id": null, "name": "skills", "summary": "install, check, or remove this tool's packaged agent skills", "synopsis": null}] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; skills uninstall: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | [] | complete |
| route:entrypoint:fleet/status | invocation | status | command route; parser handles remaining tokens |  | probe each planned host, refresh the local state and report per-host progress and ETA | probe each planned host, refresh the local state and report per-host progress and ETA | EXPLORATION |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; status: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:fleet/status/--hx.type", "option:route:entrypoint:fleet/status/--state-dir.type"] | complete |
| route:entrypoint:fleet/tick | invocation | tick | command route; parser handles remaining tokens |  | perform one scheduling step: adopt jobs, detect lost or stalled hosts, re-queue work and return | perform one scheduling step: adopt jobs, detect lost or stalled hosts, re-queue work and return | MIXED OPERATIONS |  | false | {} | [] | <entrypoint>: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no; tick: allow_abbrev=no, prefix_chars='-', fromfile_prefix_chars=None, negative_number_matcher={'pattern': '-\\.?\\d', 'flags': 32}, has_negative_number_optionals=no, negative_number_matcher_custom=no | false | ["option:route:entrypoint:fleet/tick/--hx.type", "option:route:entrypoint:fleet/tick/--state-dir.type"] | complete |

### Arguments and options

| Surface ID | Kind | Spelling/name | Description | Grammar shape | Action/type/const | Required | Choices/default/exclusive rule | Scope/placement | Visibility/help group |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| argument:route:entrypoint:fleet/collect/campaign | argument | campaign | campaign ID | {"metavar": "ID", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["collect"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:fleet/collect/--transport | option | --transport | host transport; only the identity-checked hx wrapper is supported | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["hx"], "declared_default": "hx", "default": "hx", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["collect"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/collect/--hx | option | --hx | path of the identity-checked hx wrapper (default: $FLEETD_HX, else the source checkout's scripts/controller/hx.sh; an installed fleetd needs one of them) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["collect"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/collect/--state-dir | option | --state-dir | campaign state directory (default: $XDG_STATE_HOME/fleetd/campaigns, or ~/.local/state/fleetd/campaigns when unset) | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["collect"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "STATE", "hidden": false} |
| option:route:entrypoint:fleet/facts/collect/--root | option | --root | prefix for the host's absolute config and fact paths (a fixture root queries no systemd) | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": "/", "default": "/", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["facts", "collect"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "INPUT", "hidden": false} |
| argument:route:entrypoint:fleet/facts/validate/file | argument | file | host-facts/1 or host-facts/2 JSON file to validate | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["facts", "validate"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| argument:route:entrypoint:fleet/measurement/campaign | argument | campaign | campaign ID | {"metavar": "ID", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["measurement"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:fleet/measurement/--transport | option | --transport | host transport; only the identity-checked hx wrapper is supported | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["hx"], "declared_default": "hx", "default": "hx", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["measurement"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/measurement/--hx | option | --hx | path of the identity-checked hx wrapper (default: $FLEETD_HX, else the source checkout's scripts/controller/hx.sh; an installed fleetd needs one of them) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["measurement"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/measurement/--state-dir | option | --state-dir | campaign state directory (default: $XDG_STATE_HOME/fleetd/campaigns, or ~/.local/state/fleetd/campaigns when unset) | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["measurement"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "STATE", "hidden": false} |
| argument:route:entrypoint:fleet/plan/lane | argument | lane | Run-Gate lane name | {"metavar": "LANE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["plan"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:fleet/plan/--candidates | option | --candidates | candidate-table/1 JSON file | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["plan"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "INPUT", "hidden": false} |
| option:route:entrypoint:fleet/plan/--hosts | option | --hosts | fleet-host-set/1 JSON file | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["plan"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "INPUT", "hidden": false} |
| option:route:entrypoint:fleet/plan/--demand | option | --demand | TOML file containing [lanes.<lane>.demand] (release Run-Gate does not accept that key yet; see RG-276) | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["plan"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "INPUT", "hidden": false} |
| option:route:entrypoint:fleet/plan/--commands | option | --commands | print Run-Gate --only-candidates commands instead of plan JSON | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["plan"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OUTPUT", "hidden": false} |
| option:route:entrypoint:fleet/plan/--batch-multiplier | option | --batch-multiplier | candidate count per batch as a multiple of host worker slots (default: 4) | {"metavar": "FACTOR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "builtins.float"}} | false | {"choices": null, "declared_default": 4.0, "default": 4.0, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["plan"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "PLANNING", "hidden": false} |
| argument:route:entrypoint:fleet/run/plan | argument | plan | fleet-plan/1 JSON file with an execution object | {"metavar": "PLAN", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:fleet/run/--campaign | option | --campaign | campaign ID; the same ID re-adopts existing jobs by their deterministic names | {"metavar": "ID", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "CAMPAIGN", "hidden": false} |
| option:route:entrypoint:fleet/run/--transport | option | --transport | host transport; only the identity-checked hx wrapper is supported | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["hx"], "declared_default": "hx", "default": "hx", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/run/--hx | option | --hx | path of the identity-checked hx wrapper (default: $FLEETD_HX, else the source checkout's scripts/controller/hx.sh; an installed fleetd needs one of them) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/run/--state-dir | option | --state-dir | campaign state directory (default: $XDG_STATE_HOME/fleetd/campaigns, or ~/.local/state/fleetd/campaigns when unset) | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["run"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "STATE", "hidden": false} |
| argument:route:entrypoint:fleet/scope/lane | argument | lane | Run-Gate lane whose Assay candidates form the whole-target list | {"metavar": "LANE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:fleet/scope/--plan | option | --plan | output of `assay plan <lane> --json` | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "INPUT", "hidden": false} |
| option:route:entrypoint:fleet/scope/--repo | option | --repo | the Git checkout the plan describes | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": ".", "default": ".", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "INPUT", "hidden": false} |
| option:route:entrypoint:fleet/scope/--from-tag | option | --from-tag | previous release tag (required by the changed-lines scope) | {"metavar": "TAG", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "SCOPE", "hidden": false} |
| option:route:entrypoint:fleet/scope/--commit | option | --commit | commit to judge (default: HEAD of --repo) | {"metavar": "SHA", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "SCOPE", "hidden": false} |
| option:route:entrypoint:fleet/scope/--mode | option | --mode | scope mode (default: changed-lines) | {"metavar": "MODE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["changed-lines", "rolling-whole-target"], "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "SCOPE", "hidden": false} |
| option:route:entrypoint:fleet/scope/--scope-file | option | --scope-file | project r2 scope file (default: <repo>/r2-scope.toml when present) | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "SCOPE", "hidden": false} |
| option:route:entrypoint:fleet/scope/--slice-size | option | --slice-size | rolling-whole-target slice size | {"metavar": "N", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "builtins.int"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "SCOPE", "hidden": false} |
| option:route:entrypoint:fleet/scope/--cursor | option | --cursor | rolling cursor file to resume from | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "SCOPE", "hidden": false} |
| option:route:entrypoint:fleet/scope/--write-cursor | option | --write-cursor | write the advanced rolling cursor here | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OUTPUT", "hidden": false} |
| option:route:entrypoint:fleet/scope/--rebaseline | option | --rebaseline | start one fresh sweep pass from the current file content, discarding the cursor's marks (the explicit migration for an older cursor schema) | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "SCOPE", "hidden": false} |
| option:route:entrypoint:fleet/scope/--out | option | --out | write the candidate-id file here (refused for an empty scope) | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["scope"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "OUTPUT", "hidden": false} |
| argument:route:entrypoint:fleet/sizing/plan | argument | plan | fleet-plan/1 JSON file | {"metavar": "PLAN", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["sizing"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:fleet/sizing/--measurement | option | --measurement | fleet-r2-measurement/1 from a finished campaign | {"metavar": "FILE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["sizing"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "INPUT", "hidden": false} |
| option:route:entrypoint:fleet/sizing/--per-candidate-seconds | option | --per-candidate-seconds | declared per-candidate wall seconds | {"metavar": "SECONDS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "builtins.float"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["sizing"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "INPUT", "hidden": false} |
| option:route:entrypoint:fleet/sizing/--per-candidate-memory | option | --per-candidate-memory | declared per-candidate memory charge | {"metavar": "SIZE", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["sizing"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "INPUT", "hidden": false} |
| option:route:entrypoint:fleet/sizing/--margin | option | --margin | explicit budget margin over the estimate (default: derived from the measured spread, at least 1.10) | {"metavar": "FACTOR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "builtins.float"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["sizing"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "BUDGET", "hidden": false} |
| option:route:entrypoint:fleet/sizing/--minimum-budget | option | --minimum-budget | shortest per-instance budget (default: 15m) | {"metavar": "DURATION", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": "15m", "default": "15m", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["sizing"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "BUDGET", "hidden": false} |
| option:route:entrypoint:fleet/sizing/--startup-allowance | option | --startup-allowance | outer-bound allowance beside each instance budget (default: 15m) | {"metavar": "DURATION", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": "15m", "default": "15m", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["sizing"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "BUDGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/check/--harness | option | --harness | target harness: claude, agents or all (default: all) | {"metavar": "HARNESS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["claude", "agents", "all"], "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "check"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/check/--dest | option | --dest | use exactly this directory instead of a harness directory | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "check"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/install/--harness | option | --harness | target harness: claude, agents or all (default: all) | {"metavar": "HARNESS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["claude", "agents", "all"], "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "install"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/install/--dest | option | --dest | use exactly this directory instead of a harness directory | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "install"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/install/--overwrite-modified | option | --overwrite-modified | replace skills you modified locally | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["skills", "install"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/list/--harness | option | --harness | target harness: claude, agents or all (default: all) | {"metavar": "HARNESS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["claude", "agents", "all"], "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "list"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/list/--dest | option | --dest | use exactly this directory instead of a harness directory | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "list"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/uninstall/--harness | option | --harness | target harness: claude, agents or all (default: all) | {"metavar": "HARNESS", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["claude", "agents", "all"], "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "uninstall"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/uninstall/--dest | option | --dest | use exactly this directory instead of a harness directory | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": "target", "exclusive_required": false} | {"parser_path": ["skills", "uninstall"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| option:route:entrypoint:fleet/skills/uninstall/--overwrite-modified | option | --overwrite-modified | replace skills you modified locally | {"metavar": null, "minimum_values": 0, "nargs": 0} | {"action": "argparse._StoreTrueAction", "const": true, "type": null} | false | {"choices": null, "declared_default": false, "default": false, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["skills", "uninstall"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TARGET", "hidden": false} |
| argument:route:entrypoint:fleet/status/campaign | argument | campaign | campaign ID | {"metavar": "ID", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["status"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:fleet/status/--transport | option | --transport | host transport; only the identity-checked hx wrapper is supported | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["hx"], "declared_default": "hx", "default": "hx", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["status"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/status/--hx | option | --hx | path of the identity-checked hx wrapper (default: $FLEETD_HX, else the source checkout's scripts/controller/hx.sh; an installed fleetd needs one of them) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["status"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/status/--state-dir | option | --state-dir | campaign state directory (default: $XDG_STATE_HOME/fleetd/campaigns, or ~/.local/state/fleetd/campaigns when unset) | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["status"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "STATE", "hidden": false} |
| argument:route:entrypoint:fleet/tick/campaign | argument | campaign | campaign ID | {"metavar": "ID", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | true | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tick"], "scope": "positional"} | {"help_group": "positional arguments", "hidden": false} |
| option:route:entrypoint:fleet/tick/--transport | option | --transport | host transport; only the identity-checked hx wrapper is supported | {"metavar": "NAME", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": null} | false | {"choices": ["hx"], "declared_default": "hx", "default": "hx", "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tick"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/tick/--hx | option | --hx | path of the identity-checked hx wrapper (default: $FLEETD_HX, else the source checkout's scripts/controller/hx.sh; an installed fleetd needs one of them) | {"metavar": "PATH", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tick"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "TRANSPORT", "hidden": false} |
| option:route:entrypoint:fleet/tick/--state-dir | option | --state-dir | campaign state directory (default: $XDG_STATE_HOME/fleetd/campaigns, or ~/.local/state/fleetd/campaigns when unset) | {"metavar": "DIR", "minimum_values": 1} | {"action": "argparse._StoreAction", "const": null, "type": {"callable": "pathlib.Path"}} | false | {"choices": null, "declared_default": null, "default": null, "exclusive_group": null, "exclusive_required": false} | {"parser_path": ["tick"], "placement": {"after_verb": true, "before_verb": false, "single_command_invocation": false}, "scope": "verb-local"} | {"help_group": "STATE", "hidden": false} |

### Library common controls

- `route:entrypoint:fleet/collect`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:fleet/facts`: Common controls: none
- `route:entrypoint:fleet/facts/collect`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:fleet/facts/validate`: Common controls: --color, --debug, --debug-raw, --help, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:fleet/measurement`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:fleet/plan`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:fleet/run`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --progress, --quiet, --traceback, --version
- `route:entrypoint:fleet/scope`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:fleet/sizing`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:fleet/skills`: Common controls: none
- `route:entrypoint:fleet/skills/check`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --progress, --quiet, --traceback, --version
- `route:entrypoint:fleet/skills/install`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --progress, --quiet, --traceback, --version
- `route:entrypoint:fleet/skills/list`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --progress, --quiet, --traceback, --version
- `route:entrypoint:fleet/skills/uninstall`: Common controls: --color, --debug, --debug-raw, --dry-run, --help, --log-level, --no-color, --progress, --quiet, --traceback, --version
- `route:entrypoint:fleet/status`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version
- `route:entrypoint:fleet/tick`: Common controls: --color, --debug, --debug-raw, --help, --json, --log-level, --no-color, --quiet, --traceback, --version

### Semantic case review

The invocation is an argv token list passed to the registered CLI, excluding the executable name. Test IDs prove collection/linkage only; the normal gate proves execution and assertions.

| Case ID | Dimension | Surface IDs / shape | Decision | Invocation | Expected status/output | Effects | Test IDs | Rationale/state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| case:route:entrypoint:fleet/collect/argument-shape/argument:route:entrypoint:fleet/collect/campaign | argument-shape | {"members": ["argument:route:entrypoint:fleet/collect/campaign"], "shape": {"argument_id": "argument:route:entrypoint:fleet/collect/campaign"}} | REFUSE | collect x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[collect-argument-shape-00ba1375] | The positional is accepted in the shape the registration declares. |
| case:route:entrypoint:fleet/collect/minimum | minimum | {"members": ["argument:route:entrypoint:fleet/collect/campaign", "option:route:entrypoint:fleet/collect/--transport"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/collect/--transport"], "required_argument_values": {"argument:route:entrypoint:fleet/collect/campaign": 1}, "required_arguments": ["argument:route:entrypoint:fleet/collect/campaign"], "required_exclusive_groups": {}, "required_options": []}} | REFUSE | collect x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[collect-minimum-512c3cba] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/collect/option-choice/option:route:entrypoint:fleet/collect/--transport/ad3632c423 | option-choice | {"members": ["option:route:entrypoint:fleet/collect/--transport"], "shape": {"choice": "hx", "option_id": "option:route:entrypoint:fleet/collect/--transport"}} | REFUSE | collect x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[collect-option-choice-57622cd2] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/collect/option-spelling/option:route:entrypoint:fleet/collect/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/collect/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/collect/--debug-raw", "spelling": "--debug-raw"}} | REFUSE | collect x --debug-raw | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[collect-option-spelling-d691fade] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/collect/option-spelling/option:route:entrypoint:fleet/collect/--hx/--hx | option-spelling | {"members": ["option:route:entrypoint:fleet/collect/--hx"], "shape": {"option_id": "option:route:entrypoint:fleet/collect/--hx", "spelling": "--hx"}} | REFUSE | collect x --hx x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[collect-option-spelling-cb361013] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/collect/option-spelling/option:route:entrypoint:fleet/collect/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/collect/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/collect/--json", "spelling": "--json"}} | REFUSE | collect x --json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[collect-option-spelling-842738d3] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/collect/option-spelling/option:route:entrypoint:fleet/collect/--state-dir/--state-dir | option-spelling | {"members": ["option:route:entrypoint:fleet/collect/--state-dir"], "shape": {"option_id": "option:route:entrypoint:fleet/collect/--state-dir", "spelling": "--state-dir"}} | REFUSE | collect x --state-dir x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[collect-option-spelling-0ce5a899] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/collect/option-spelling/option:route:entrypoint:fleet/collect/--transport/--transport | option-spelling | {"members": ["option:route:entrypoint:fleet/collect/--transport"], "shape": {"option_id": "option:route:entrypoint:fleet/collect/--transport", "spelling": "--transport"}} | REFUSE | collect x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[collect-option-spelling-1d2e9b66] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/facts/collect/minimum | minimum | {"members": ["option:route:entrypoint:fleet/facts/collect/--root"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/facts/collect/--root"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | facts collect --root @facts-root | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[facts-collect-minimum-6a6489f0] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/facts/collect/option-spelling/option:route:entrypoint:fleet/facts/collect/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/facts/collect/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/facts/collect/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | facts collect --root @facts-root --debug-raw | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[facts-collect-option-spelling-946d2063] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/facts/collect/option-spelling/option:route:entrypoint:fleet/facts/collect/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/facts/collect/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/facts/collect/--json", "spelling": "--json"}} | ACCEPT | facts collect --root @facts-root --json | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[facts-collect-option-spelling-5d11e8af] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/facts/collect/option-spelling/option:route:entrypoint:fleet/facts/collect/--root/--root | option-spelling | {"members": ["option:route:entrypoint:fleet/facts/collect/--root"], "shape": {"option_id": "option:route:entrypoint:fleet/facts/collect/--root", "spelling": "--root"}} | ACCEPT | facts collect --root @facts-root | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[facts-collect-option-spelling-54dbff23] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/facts/validate/argument-shape/argument:route:entrypoint:fleet/facts/validate/file | argument-shape | {"members": ["argument:route:entrypoint:fleet/facts/validate/file"], "shape": {"argument_id": "argument:route:entrypoint:fleet/facts/validate/file"}} | ACCEPT | facts validate @host-facts.valid.json | {"status": 0, "stderr contains": "", "stdout contains": "valid host-facts/1"} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[facts-validate-argument-shape-ad0ef5dc] | The positional is accepted in the shape the registration declares. |
| case:route:entrypoint:fleet/facts/validate/minimum | minimum | {"members": ["argument:route:entrypoint:fleet/facts/validate/file"], "shape": {"defaulted_options": [], "required_argument_values": {"argument:route:entrypoint:fleet/facts/validate/file": 1}, "required_arguments": ["argument:route:entrypoint:fleet/facts/validate/file"], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | facts validate @host-facts.valid.json | {"status": 0, "stderr contains": "", "stdout contains": "valid host-facts/1"} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[facts-validate-minimum-23944fa9] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/facts/validate/option-spelling/option:route:entrypoint:fleet/facts/validate/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/facts/validate/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/facts/validate/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | facts validate @host-facts.valid.json --debug-raw | {"status": 0, "stderr contains": "", "stdout contains": "valid host-facts/1"} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[facts-validate-option-spelling-ba5fd61a] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/measurement/argument-shape/argument:route:entrypoint:fleet/measurement/campaign | argument-shape | {"members": ["argument:route:entrypoint:fleet/measurement/campaign"], "shape": {"argument_id": "argument:route:entrypoint:fleet/measurement/campaign"}} | REFUSE | measurement x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[measurement-argument-shape-0dac1e18] | The positional is accepted in the shape the registration declares. |
| case:route:entrypoint:fleet/measurement/minimum | minimum | {"members": ["argument:route:entrypoint:fleet/measurement/campaign", "option:route:entrypoint:fleet/measurement/--transport"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/measurement/--transport"], "required_argument_values": {"argument:route:entrypoint:fleet/measurement/campaign": 1}, "required_arguments": ["argument:route:entrypoint:fleet/measurement/campaign"], "required_exclusive_groups": {}, "required_options": []}} | REFUSE | measurement x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[measurement-minimum-d6e529de] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/measurement/option-choice/option:route:entrypoint:fleet/measurement/--transport/ad3632c423 | option-choice | {"members": ["option:route:entrypoint:fleet/measurement/--transport"], "shape": {"choice": "hx", "option_id": "option:route:entrypoint:fleet/measurement/--transport"}} | REFUSE | measurement x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[measurement-option-choice-f2015e50] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/measurement/option-spelling/option:route:entrypoint:fleet/measurement/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/measurement/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/measurement/--debug-raw", "spelling": "--debug-raw"}} | REFUSE | measurement x --debug-raw | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[measurement-option-spelling-9bc58895] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/measurement/option-spelling/option:route:entrypoint:fleet/measurement/--hx/--hx | option-spelling | {"members": ["option:route:entrypoint:fleet/measurement/--hx"], "shape": {"option_id": "option:route:entrypoint:fleet/measurement/--hx", "spelling": "--hx"}} | REFUSE | measurement x --hx x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[measurement-option-spelling-35b8d12f] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/measurement/option-spelling/option:route:entrypoint:fleet/measurement/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/measurement/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/measurement/--json", "spelling": "--json"}} | REFUSE | measurement x --json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[measurement-option-spelling-05cc96f3] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/measurement/option-spelling/option:route:entrypoint:fleet/measurement/--state-dir/--state-dir | option-spelling | {"members": ["option:route:entrypoint:fleet/measurement/--state-dir"], "shape": {"option_id": "option:route:entrypoint:fleet/measurement/--state-dir", "spelling": "--state-dir"}} | REFUSE | measurement x --state-dir x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[measurement-option-spelling-120f9484] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/measurement/option-spelling/option:route:entrypoint:fleet/measurement/--transport/--transport | option-spelling | {"members": ["option:route:entrypoint:fleet/measurement/--transport"], "shape": {"option_id": "option:route:entrypoint:fleet/measurement/--transport", "spelling": "--transport"}} | REFUSE | measurement x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[measurement-option-spelling-ff2f68cf] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/plan/argument-shape/argument:route:entrypoint:fleet/plan/lane | argument-shape | {"members": ["argument:route:entrypoint:fleet/plan/lane"], "shape": {"argument_id": "argument:route:entrypoint:fleet/plan/lane"}} | REFUSE | plan x --candidates @candidate-table.valid.json --hosts @fleet-host-set.valid.json --demand @lane-demand.r2.toml | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[plan-argument-shape-26753ec5] | The positional is accepted in the shape the registration declares. |
| case:route:entrypoint:fleet/plan/minimum | minimum | {"members": ["argument:route:entrypoint:fleet/plan/lane", "option:route:entrypoint:fleet/plan/--candidates", "option:route:entrypoint:fleet/plan/--hosts", "option:route:entrypoint:fleet/plan/--demand", "option:route:entrypoint:fleet/plan/--commands", "option:route:entrypoint:fleet/plan/--batch-multiplier"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/plan/--commands", "option:route:entrypoint:fleet/plan/--batch-multiplier"], "required_argument_values": {"argument:route:entrypoint:fleet/plan/lane": 1}, "required_arguments": ["argument:route:entrypoint:fleet/plan/lane"], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:fleet/plan/--candidates", "option:route:entrypoint:fleet/plan/--hosts", "option:route:entrypoint:fleet/plan/--demand"]}} | REFUSE | plan x --candidates @candidate-table.valid.json --hosts @fleet-host-set.valid.json --demand @lane-demand.r2.toml | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[plan-minimum-bf6e9d25] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/plan/option-spelling/option:route:entrypoint:fleet/plan/--batch-multiplier/--batch-multiplier | option-spelling | {"members": ["option:route:entrypoint:fleet/plan/--batch-multiplier"], "shape": {"option_id": "option:route:entrypoint:fleet/plan/--batch-multiplier", "spelling": "--batch-multiplier"}} | REFUSE | plan x --candidates @candidate-table.valid.json --hosts @fleet-host-set.valid.json --demand @lane-demand.r2.toml --batch-multiplier 1 | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[plan-option-spelling-9d73dae9] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/plan/option-spelling/option:route:entrypoint:fleet/plan/--candidates/--candidates | option-spelling | {"members": ["option:route:entrypoint:fleet/plan/--candidates"], "shape": {"option_id": "option:route:entrypoint:fleet/plan/--candidates", "spelling": "--candidates"}} | REFUSE | plan x --hosts @fleet-host-set.valid.json --demand @lane-demand.r2.toml --candidates @candidate-table.valid.json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[plan-option-spelling-013946d2] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/plan/option-spelling/option:route:entrypoint:fleet/plan/--commands/--commands | option-spelling | {"members": ["option:route:entrypoint:fleet/plan/--commands"], "shape": {"option_id": "option:route:entrypoint:fleet/plan/--commands", "spelling": "--commands"}} | REFUSE | plan x --candidates @candidate-table.valid.json --hosts @fleet-host-set.valid.json --demand @lane-demand.r2.toml --commands | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[plan-option-spelling-0f79a1c1] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/plan/option-spelling/option:route:entrypoint:fleet/plan/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/plan/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/plan/--debug-raw", "spelling": "--debug-raw"}} | REFUSE | plan x --candidates @candidate-table.valid.json --hosts @fleet-host-set.valid.json --demand @lane-demand.r2.toml --debug-raw | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[plan-option-spelling-704020bf] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/plan/option-spelling/option:route:entrypoint:fleet/plan/--demand/--demand | option-spelling | {"members": ["option:route:entrypoint:fleet/plan/--demand"], "shape": {"option_id": "option:route:entrypoint:fleet/plan/--demand", "spelling": "--demand"}} | REFUSE | plan x --candidates @candidate-table.valid.json --hosts @fleet-host-set.valid.json --demand @lane-demand.r2.toml | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[plan-option-spelling-dbaefd37] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/plan/option-spelling/option:route:entrypoint:fleet/plan/--hosts/--hosts | option-spelling | {"members": ["option:route:entrypoint:fleet/plan/--hosts"], "shape": {"option_id": "option:route:entrypoint:fleet/plan/--hosts", "spelling": "--hosts"}} | REFUSE | plan x --candidates @candidate-table.valid.json --demand @lane-demand.r2.toml --hosts @fleet-host-set.valid.json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[plan-option-spelling-a9960149] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/plan/option-spelling/option:route:entrypoint:fleet/plan/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/plan/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/plan/--json", "spelling": "--json"}} | REFUSE | plan x --candidates @candidate-table.valid.json --hosts @fleet-host-set.valid.json --demand @lane-demand.r2.toml --json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[plan-option-spelling-e6595bcb] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/run/argument-shape/argument:route:entrypoint:fleet/run/plan | argument-shape | {"members": ["argument:route:entrypoint:fleet/run/plan"], "shape": {"argument_id": "argument:route:entrypoint:fleet/run/plan"}} | REFUSE | run @invalid-plan.json --campaign x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[run-argument-shape-74887c35] | The positional is accepted in the shape the registration declares. |
| case:route:entrypoint:fleet/run/minimum | minimum | {"members": ["argument:route:entrypoint:fleet/run/plan", "option:route:entrypoint:fleet/run/--campaign", "option:route:entrypoint:fleet/run/--transport"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/run/--transport"], "required_argument_values": {"argument:route:entrypoint:fleet/run/plan": 1}, "required_arguments": ["argument:route:entrypoint:fleet/run/plan"], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:fleet/run/--campaign"]}} | REFUSE | run @invalid-plan.json --campaign x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[run-minimum-17146830] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/run/option-choice/option:route:entrypoint:fleet/run/--transport/ad3632c423 | option-choice | {"members": ["option:route:entrypoint:fleet/run/--transport"], "shape": {"choice": "hx", "option_id": "option:route:entrypoint:fleet/run/--transport"}} | REFUSE | run @invalid-plan.json --campaign x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[run-option-choice-35560895] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/run/option-spelling/option:route:entrypoint:fleet/run/--campaign/--campaign | option-spelling | {"members": ["option:route:entrypoint:fleet/run/--campaign"], "shape": {"option_id": "option:route:entrypoint:fleet/run/--campaign", "spelling": "--campaign"}} | REFUSE | run @invalid-plan.json --campaign x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[run-option-spelling-d791a9a5] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/run/option-spelling/option:route:entrypoint:fleet/run/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/run/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/run/--debug-raw", "spelling": "--debug-raw"}} | REFUSE | run @invalid-plan.json --campaign x --debug-raw | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[run-option-spelling-5213ba9b] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/run/option-spelling/option:route:entrypoint:fleet/run/--hx/--hx | option-spelling | {"members": ["option:route:entrypoint:fleet/run/--hx"], "shape": {"option_id": "option:route:entrypoint:fleet/run/--hx", "spelling": "--hx"}} | REFUSE | run @invalid-plan.json --campaign x --hx x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[run-option-spelling-2b2f3fbe] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/run/option-spelling/option:route:entrypoint:fleet/run/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/run/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/run/--json", "spelling": "--json"}} | REFUSE | run @invalid-plan.json --campaign x --json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[run-option-spelling-29e4cca4] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/run/option-spelling/option:route:entrypoint:fleet/run/--state-dir/--state-dir | option-spelling | {"members": ["option:route:entrypoint:fleet/run/--state-dir"], "shape": {"option_id": "option:route:entrypoint:fleet/run/--state-dir", "spelling": "--state-dir"}} | REFUSE | run @invalid-plan.json --campaign x --state-dir x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[run-option-spelling-e60d83d3] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/run/option-spelling/option:route:entrypoint:fleet/run/--transport/--transport | option-spelling | {"members": ["option:route:entrypoint:fleet/run/--transport"], "shape": {"option_id": "option:route:entrypoint:fleet/run/--transport", "spelling": "--transport"}} | REFUSE | run @invalid-plan.json --campaign x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[run-option-spelling-6db8b52a] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/argument-shape/argument:route:entrypoint:fleet/scope/lane | argument-shape | {"members": ["argument:route:entrypoint:fleet/scope/lane"], "shape": {"argument_id": "argument:route:entrypoint:fleet/scope/lane"}} | REFUSE | scope x --plan x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-argument-shape-1ac30d8b] | The positional is accepted in the shape the registration declares. |
| case:route:entrypoint:fleet/scope/minimum | minimum | {"members": ["argument:route:entrypoint:fleet/scope/lane", "option:route:entrypoint:fleet/scope/--plan", "option:route:entrypoint:fleet/scope/--repo", "option:route:entrypoint:fleet/scope/--rebaseline"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/scope/--repo", "option:route:entrypoint:fleet/scope/--rebaseline"], "required_argument_values": {"argument:route:entrypoint:fleet/scope/lane": 1}, "required_arguments": ["argument:route:entrypoint:fleet/scope/lane"], "required_exclusive_groups": {}, "required_options": ["option:route:entrypoint:fleet/scope/--plan"]}} | REFUSE | scope x --plan x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-minimum-a4e87f4e] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/scope/option-choice/option:route:entrypoint:fleet/scope/--mode/8d72853750 | option-choice | {"members": ["option:route:entrypoint:fleet/scope/--mode"], "shape": {"choice": "changed-lines", "option_id": "option:route:entrypoint:fleet/scope/--mode"}} | REFUSE | scope x --plan x --mode changed-lines | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-choice-1b6b2bfb] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/scope/option-choice/option:route:entrypoint:fleet/scope/--mode/91b36406eb | option-choice | {"members": ["option:route:entrypoint:fleet/scope/--mode"], "shape": {"choice": "rolling-whole-target", "option_id": "option:route:entrypoint:fleet/scope/--mode"}} | REFUSE | scope x --plan x --mode rolling-whole-target | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-choice-d55f4993] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--commit/--commit | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--commit"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--commit", "spelling": "--commit"}} | REFUSE | scope x --plan x --commit x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-f0e6ded4] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--cursor/--cursor | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--cursor"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--cursor", "spelling": "--cursor"}} | REFUSE | scope x --plan x --cursor x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-4d808de4] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--debug-raw", "spelling": "--debug-raw"}} | REFUSE | scope x --plan x --debug-raw | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-a4fc96b2] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--from-tag/--from-tag | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--from-tag"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--from-tag", "spelling": "--from-tag"}} | REFUSE | scope x --plan x --from-tag x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-0735bcf9] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--json", "spelling": "--json"}} | REFUSE | scope x --plan x --json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-fb935c6a] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--mode/--mode | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--mode"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--mode", "spelling": "--mode"}} | REFUSE | scope x --plan x --mode changed-lines | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-82e79437] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--out/--out | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--out"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--out", "spelling": "--out"}} | REFUSE | scope x --plan x --out x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-5305fa4c] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--plan/--plan | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--plan"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--plan", "spelling": "--plan"}} | REFUSE | scope x --plan x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-11e1eefc] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--rebaseline/--rebaseline | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--rebaseline"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--rebaseline", "spelling": "--rebaseline"}} | REFUSE | scope x --plan x --rebaseline | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-0b9ede5b] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--repo/--repo | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--repo"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--repo", "spelling": "--repo"}} | REFUSE | scope x --plan x --repo x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-a86db352] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--scope-file/--scope-file | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--scope-file"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--scope-file", "spelling": "--scope-file"}} | REFUSE | scope x --plan x --scope-file x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-39953ffb] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--slice-size/--slice-size | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--slice-size"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--slice-size", "spelling": "--slice-size"}} | REFUSE | scope x --plan x --slice-size 1 | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-61bb9635] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/scope/option-spelling/option:route:entrypoint:fleet/scope/--write-cursor/--write-cursor | option-spelling | {"members": ["option:route:entrypoint:fleet/scope/--write-cursor"], "shape": {"option_id": "option:route:entrypoint:fleet/scope/--write-cursor", "spelling": "--write-cursor"}} | REFUSE | scope x --plan x --write-cursor x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[scope-option-spelling-13c58a3b] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/sizing/argument-shape/argument:route:entrypoint:fleet/sizing/plan | argument-shape | {"members": ["argument:route:entrypoint:fleet/sizing/plan"], "shape": {"argument_id": "argument:route:entrypoint:fleet/sizing/plan"}} | REFUSE | sizing x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-argument-shape-323d71b5] | The positional is accepted in the shape the registration declares. |
| case:route:entrypoint:fleet/sizing/minimum | minimum | {"members": ["argument:route:entrypoint:fleet/sizing/plan", "option:route:entrypoint:fleet/sizing/--minimum-budget", "option:route:entrypoint:fleet/sizing/--startup-allowance"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/sizing/--minimum-budget", "option:route:entrypoint:fleet/sizing/--startup-allowance"], "required_argument_values": {"argument:route:entrypoint:fleet/sizing/plan": 1}, "required_arguments": ["argument:route:entrypoint:fleet/sizing/plan"], "required_exclusive_groups": {}, "required_options": []}} | REFUSE | sizing x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-minimum-ef6243e2] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/sizing/option-spelling/option:route:entrypoint:fleet/sizing/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/sizing/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/sizing/--debug-raw", "spelling": "--debug-raw"}} | REFUSE | sizing x --debug-raw | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-option-spelling-9d5563bc] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/sizing/option-spelling/option:route:entrypoint:fleet/sizing/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/sizing/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/sizing/--json", "spelling": "--json"}} | REFUSE | sizing x --json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-option-spelling-637fc6c0] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/sizing/option-spelling/option:route:entrypoint:fleet/sizing/--margin/--margin | option-spelling | {"members": ["option:route:entrypoint:fleet/sizing/--margin"], "shape": {"option_id": "option:route:entrypoint:fleet/sizing/--margin", "spelling": "--margin"}} | REFUSE | sizing x --margin 1 | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-option-spelling-289a795a] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/sizing/option-spelling/option:route:entrypoint:fleet/sizing/--measurement/--measurement | option-spelling | {"members": ["option:route:entrypoint:fleet/sizing/--measurement"], "shape": {"option_id": "option:route:entrypoint:fleet/sizing/--measurement", "spelling": "--measurement"}} | REFUSE | sizing x --measurement x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-option-spelling-c026004a] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/sizing/option-spelling/option:route:entrypoint:fleet/sizing/--minimum-budget/--minimum-budget | option-spelling | {"members": ["option:route:entrypoint:fleet/sizing/--minimum-budget"], "shape": {"option_id": "option:route:entrypoint:fleet/sizing/--minimum-budget", "spelling": "--minimum-budget"}} | REFUSE | sizing x --minimum-budget x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-option-spelling-883a5bb4] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/sizing/option-spelling/option:route:entrypoint:fleet/sizing/--per-candidate-memory/--per-candidate-memory | option-spelling | {"members": ["option:route:entrypoint:fleet/sizing/--per-candidate-memory"], "shape": {"option_id": "option:route:entrypoint:fleet/sizing/--per-candidate-memory", "spelling": "--per-candidate-memory"}} | REFUSE | sizing x --per-candidate-memory x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-option-spelling-c53e52cc] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/sizing/option-spelling/option:route:entrypoint:fleet/sizing/--per-candidate-seconds/--per-candidate-seconds | option-spelling | {"members": ["option:route:entrypoint:fleet/sizing/--per-candidate-seconds"], "shape": {"option_id": "option:route:entrypoint:fleet/sizing/--per-candidate-seconds", "spelling": "--per-candidate-seconds"}} | REFUSE | sizing x --per-candidate-seconds 1 | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-option-spelling-5d1aac2d] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/sizing/option-spelling/option:route:entrypoint:fleet/sizing/--startup-allowance/--startup-allowance | option-spelling | {"members": ["option:route:entrypoint:fleet/sizing/--startup-allowance"], "shape": {"option_id": "option:route:entrypoint:fleet/sizing/--startup-allowance", "spelling": "--startup-allowance"}} | REFUSE | sizing x --startup-allowance x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[sizing-option-spelling-ab90f7ac] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/check/exclusive-conflict/target/option:route:entrypoint:fleet/skills/check/--dest/option:route:entrypoint:fleet/skills/check/--harness | exclusive-conflict | {"members": ["option:route:entrypoint:fleet/skills/check/--dest", "option:route:entrypoint:fleet/skills/check/--harness"], "shape": {"group_id": "target", "options": ["option:route:entrypoint:fleet/skills/check/--dest", "option:route:entrypoint:fleet/skills/check/--harness"]}} | REFUSE | skills check --dest x --harness claude | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-exclusive-conflict-b65ac960] | Two members of one exclusive group are refused by the shared parser before any handler runs. |
| case:route:entrypoint:fleet/skills/check/exclusive-member/target/option:route:entrypoint:fleet/skills/check/--dest | exclusive-member | {"members": ["option:route:entrypoint:fleet/skills/check/--dest"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:fleet/skills/check/--dest"}} | ACCEPT | skills check --dest x | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-exclusive-member-fc4d5a91] | One member of a mutually exclusive group is accepted alone. |
| case:route:entrypoint:fleet/skills/check/exclusive-member/target/option:route:entrypoint:fleet/skills/check/--harness | exclusive-member | {"members": ["option:route:entrypoint:fleet/skills/check/--harness"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:fleet/skills/check/--harness"}} | ACCEPT | skills check --harness claude | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-exclusive-member-e75e7416] | One member of a mutually exclusive group is accepted alone. |
| case:route:entrypoint:fleet/skills/check/minimum | minimum | {"members": [], "shape": {"defaulted_options": [], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | skills check | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-minimum-d47e37fa] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/skills/check/option-choice/option:route:entrypoint:fleet/skills/check/--harness/389432cbf8 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/check/--harness"], "shape": {"choice": "claude", "option_id": "option:route:entrypoint:fleet/skills/check/--harness"}} | ACCEPT | skills check --harness claude | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-option-choice-a6b5c333] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/check/option-choice/option:route:entrypoint:fleet/skills/check/--harness/5cf7504d97 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/check/--harness"], "shape": {"choice": "all", "option_id": "option:route:entrypoint:fleet/skills/check/--harness"}} | ACCEPT | skills check --harness all | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-option-choice-d10ebcab] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/check/option-choice/option:route:entrypoint:fleet/skills/check/--harness/bc42532430 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/check/--harness"], "shape": {"choice": "agents", "option_id": "option:route:entrypoint:fleet/skills/check/--harness"}} | ACCEPT | skills check --harness agents | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-option-choice-9f97ca0b] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/check/option-spelling/option:route:entrypoint:fleet/skills/check/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/check/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/check/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | skills check --debug-raw | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-option-spelling-8149dcb5] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/check/option-spelling/option:route:entrypoint:fleet/skills/check/--dest/--dest | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/check/--dest"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/check/--dest", "spelling": "--dest"}} | ACCEPT | skills check --dest x | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-option-spelling-36592e5c] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/check/option-spelling/option:route:entrypoint:fleet/skills/check/--harness/--harness | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/check/--harness"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/check/--harness", "spelling": "--harness"}} | ACCEPT | skills check --harness claude | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-option-spelling-4bd0e303] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/check/option-spelling/option:route:entrypoint:fleet/skills/check/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/check/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/check/--json", "spelling": "--json"}} | ACCEPT | skills check --json | {"status": 1, "stderr contains": "[ERROR]", "stdout contains": ""} | The library-owned skills check runs for real in the sandbox and reports that the throw-away HOME holds no current skill (exit 1); nothing outside the sandbox is touched. | tests/test_cli_review_cases.py::test_reviewed_case[skills-check-option-spelling-adf6c7c9] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/install/exclusive-conflict/target/option:route:entrypoint:fleet/skills/install/--dest/option:route:entrypoint:fleet/skills/install/--harness | exclusive-conflict | {"members": ["option:route:entrypoint:fleet/skills/install/--dest", "option:route:entrypoint:fleet/skills/install/--harness"], "shape": {"group_id": "target", "options": ["option:route:entrypoint:fleet/skills/install/--dest", "option:route:entrypoint:fleet/skills/install/--harness"]}} | REFUSE | skills install --dest x --harness claude | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-exclusive-conflict-fa5f6439] | Two members of one exclusive group are refused by the shared parser before any handler runs. |
| case:route:entrypoint:fleet/skills/install/exclusive-member/target/option:route:entrypoint:fleet/skills/install/--dest | exclusive-member | {"members": ["option:route:entrypoint:fleet/skills/install/--dest"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:fleet/skills/install/--dest"}} | ACCEPT | skills install --dest x | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-exclusive-member-65d7514f] | One member of a mutually exclusive group is accepted alone. |
| case:route:entrypoint:fleet/skills/install/exclusive-member/target/option:route:entrypoint:fleet/skills/install/--harness | exclusive-member | {"members": ["option:route:entrypoint:fleet/skills/install/--harness"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:fleet/skills/install/--harness"}} | ACCEPT | skills install --harness claude | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-exclusive-member-fbbd5f97] | One member of a mutually exclusive group is accepted alone. |
| case:route:entrypoint:fleet/skills/install/minimum | minimum | {"members": ["option:route:entrypoint:fleet/skills/install/--overwrite-modified"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/skills/install/--overwrite-modified"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | skills install | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-minimum-9803b757] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/skills/install/option-choice/option:route:entrypoint:fleet/skills/install/--harness/389432cbf8 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/install/--harness"], "shape": {"choice": "claude", "option_id": "option:route:entrypoint:fleet/skills/install/--harness"}} | ACCEPT | skills install --harness claude | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-option-choice-be0e5cb1] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/install/option-choice/option:route:entrypoint:fleet/skills/install/--harness/5cf7504d97 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/install/--harness"], "shape": {"choice": "all", "option_id": "option:route:entrypoint:fleet/skills/install/--harness"}} | ACCEPT | skills install --harness all | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-option-choice-ca64436d] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/install/option-choice/option:route:entrypoint:fleet/skills/install/--harness/bc42532430 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/install/--harness"], "shape": {"choice": "agents", "option_id": "option:route:entrypoint:fleet/skills/install/--harness"}} | ACCEPT | skills install --harness agents | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-option-choice-325b11d8] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/install/option-spelling/option:route:entrypoint:fleet/skills/install/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/install/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/install/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | skills install --debug-raw | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-option-spelling-29675389] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/install/option-spelling/option:route:entrypoint:fleet/skills/install/--dest/--dest | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/install/--dest"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/install/--dest", "spelling": "--dest"}} | ACCEPT | skills install --dest x | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-option-spelling-936869fc] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/install/option-spelling/option:route:entrypoint:fleet/skills/install/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/install/--dry-run"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/install/--dry-run", "spelling": "--dry-run"}} | ACCEPT | skills install --dry-run | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-option-spelling-d35194ba] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/install/option-spelling/option:route:entrypoint:fleet/skills/install/--harness/--harness | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/install/--harness"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/install/--harness", "spelling": "--harness"}} | ACCEPT | skills install --harness claude | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-option-spelling-d4fb4c12] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/install/option-spelling/option:route:entrypoint:fleet/skills/install/--overwrite-modified/--overwrite-modified | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/install/--overwrite-modified"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/install/--overwrite-modified", "spelling": "--overwrite-modified"}} | ACCEPT | skills install --overwrite-modified | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-install-option-spelling-ed70e002] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/list/exclusive-conflict/target/option:route:entrypoint:fleet/skills/list/--dest/option:route:entrypoint:fleet/skills/list/--harness | exclusive-conflict | {"members": ["option:route:entrypoint:fleet/skills/list/--dest", "option:route:entrypoint:fleet/skills/list/--harness"], "shape": {"group_id": "target", "options": ["option:route:entrypoint:fleet/skills/list/--dest", "option:route:entrypoint:fleet/skills/list/--harness"]}} | REFUSE | skills list --dest x --harness claude | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-exclusive-conflict-8e95f0a2] | Two members of one exclusive group are refused by the shared parser before any handler runs. |
| case:route:entrypoint:fleet/skills/list/exclusive-member/target/option:route:entrypoint:fleet/skills/list/--dest | exclusive-member | {"members": ["option:route:entrypoint:fleet/skills/list/--dest"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:fleet/skills/list/--dest"}} | ACCEPT | skills list --dest x | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-exclusive-member-e5b9bd04] | One member of a mutually exclusive group is accepted alone. |
| case:route:entrypoint:fleet/skills/list/exclusive-member/target/option:route:entrypoint:fleet/skills/list/--harness | exclusive-member | {"members": ["option:route:entrypoint:fleet/skills/list/--harness"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:fleet/skills/list/--harness"}} | ACCEPT | skills list --harness claude | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-exclusive-member-7b4d89cb] | One member of a mutually exclusive group is accepted alone. |
| case:route:entrypoint:fleet/skills/list/minimum | minimum | {"members": [], "shape": {"defaulted_options": [], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | skills list | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-minimum-f2b8e381] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/skills/list/option-choice/option:route:entrypoint:fleet/skills/list/--harness/389432cbf8 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/list/--harness"], "shape": {"choice": "claude", "option_id": "option:route:entrypoint:fleet/skills/list/--harness"}} | ACCEPT | skills list --harness claude | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-option-choice-d33797f1] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/list/option-choice/option:route:entrypoint:fleet/skills/list/--harness/5cf7504d97 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/list/--harness"], "shape": {"choice": "all", "option_id": "option:route:entrypoint:fleet/skills/list/--harness"}} | ACCEPT | skills list --harness all | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-option-choice-3f198f55] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/list/option-choice/option:route:entrypoint:fleet/skills/list/--harness/bc42532430 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/list/--harness"], "shape": {"choice": "agents", "option_id": "option:route:entrypoint:fleet/skills/list/--harness"}} | ACCEPT | skills list --harness agents | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-option-choice-d2eeb984] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/list/option-spelling/option:route:entrypoint:fleet/skills/list/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/list/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/list/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | skills list --debug-raw | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-option-spelling-d94762c5] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/list/option-spelling/option:route:entrypoint:fleet/skills/list/--dest/--dest | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/list/--dest"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/list/--dest", "spelling": "--dest"}} | ACCEPT | skills list --dest x | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-option-spelling-c1d652b2] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/list/option-spelling/option:route:entrypoint:fleet/skills/list/--harness/--harness | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/list/--harness"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/list/--harness", "spelling": "--harness"}} | ACCEPT | skills list --harness claude | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-option-spelling-39573521] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/list/option-spelling/option:route:entrypoint:fleet/skills/list/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/list/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/list/--json", "spelling": "--json"}} | ACCEPT | skills list --json | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-list-option-spelling-ec25cfe0] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/uninstall/exclusive-conflict/target/option:route:entrypoint:fleet/skills/uninstall/--dest/option:route:entrypoint:fleet/skills/uninstall/--harness | exclusive-conflict | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--dest", "option:route:entrypoint:fleet/skills/uninstall/--harness"], "shape": {"group_id": "target", "options": ["option:route:entrypoint:fleet/skills/uninstall/--dest", "option:route:entrypoint:fleet/skills/uninstall/--harness"]}} | REFUSE | skills uninstall --dest x --harness claude | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-exclusive-conflict-0587b8b9] | Two members of one exclusive group are refused by the shared parser before any handler runs. |
| case:route:entrypoint:fleet/skills/uninstall/exclusive-member/target/option:route:entrypoint:fleet/skills/uninstall/--dest | exclusive-member | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--dest"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:fleet/skills/uninstall/--dest"}} | ACCEPT | skills uninstall --dest x | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-exclusive-member-5d1d96a0] | One member of a mutually exclusive group is accepted alone. |
| case:route:entrypoint:fleet/skills/uninstall/exclusive-member/target/option:route:entrypoint:fleet/skills/uninstall/--harness | exclusive-member | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--harness"], "shape": {"group_id": "target", "required": false, "selected_option": "option:route:entrypoint:fleet/skills/uninstall/--harness"}} | ACCEPT | skills uninstall --harness claude | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-exclusive-member-c4d629cc] | One member of a mutually exclusive group is accepted alone. |
| case:route:entrypoint:fleet/skills/uninstall/minimum | minimum | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--overwrite-modified"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/skills/uninstall/--overwrite-modified"], "required_argument_values": {}, "required_arguments": [], "required_exclusive_groups": {}, "required_options": []}} | ACCEPT | skills uninstall | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-minimum-8c2c4a74] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/skills/uninstall/option-choice/option:route:entrypoint:fleet/skills/uninstall/--harness/389432cbf8 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--harness"], "shape": {"choice": "claude", "option_id": "option:route:entrypoint:fleet/skills/uninstall/--harness"}} | ACCEPT | skills uninstall --harness claude | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-option-choice-a5325e5d] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/uninstall/option-choice/option:route:entrypoint:fleet/skills/uninstall/--harness/5cf7504d97 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--harness"], "shape": {"choice": "all", "option_id": "option:route:entrypoint:fleet/skills/uninstall/--harness"}} | ACCEPT | skills uninstall --harness all | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-option-choice-0dcfa2b5] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/uninstall/option-choice/option:route:entrypoint:fleet/skills/uninstall/--harness/bc42532430 | option-choice | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--harness"], "shape": {"choice": "agents", "option_id": "option:route:entrypoint:fleet/skills/uninstall/--harness"}} | ACCEPT | skills uninstall --harness agents | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-option-choice-2d9b24e5] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/skills/uninstall/option-spelling/option:route:entrypoint:fleet/skills/uninstall/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/uninstall/--debug-raw", "spelling": "--debug-raw"}} | ACCEPT | skills uninstall --debug-raw | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-option-spelling-c6c6793e] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/uninstall/option-spelling/option:route:entrypoint:fleet/skills/uninstall/--dest/--dest | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--dest"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/uninstall/--dest", "spelling": "--dest"}} | ACCEPT | skills uninstall --dest x | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-option-spelling-943ef21e] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/uninstall/option-spelling/option:route:entrypoint:fleet/skills/uninstall/--dry-run/--dry-run | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--dry-run"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/uninstall/--dry-run", "spelling": "--dry-run"}} | ACCEPT | skills uninstall --dry-run | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-option-spelling-78ca6d82] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/uninstall/option-spelling/option:route:entrypoint:fleet/skills/uninstall/--harness/--harness | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--harness"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/uninstall/--harness", "spelling": "--harness"}} | ACCEPT | skills uninstall --harness claude | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-option-spelling-214ab876] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/skills/uninstall/option-spelling/option:route:entrypoint:fleet/skills/uninstall/--overwrite-modified/--overwrite-modified | option-spelling | {"members": ["option:route:entrypoint:fleet/skills/uninstall/--overwrite-modified"], "shape": {"option_id": "option:route:entrypoint:fleet/skills/uninstall/--overwrite-modified", "spelling": "--overwrite-modified"}} | ACCEPT | skills uninstall --overwrite-modified | {"status": 0, "stderr contains": "", "stdout contains": ""} | Completed in the sandbox against its throw-away HOME and the fixture inputs named in the invocation; no host was contacted and nothing outside the sandbox was changed. | tests/test_cli_review_cases.py::test_reviewed_case[skills-uninstall-option-spelling-f2d8c1f2] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/status/argument-shape/argument:route:entrypoint:fleet/status/campaign | argument-shape | {"members": ["argument:route:entrypoint:fleet/status/campaign"], "shape": {"argument_id": "argument:route:entrypoint:fleet/status/campaign"}} | REFUSE | status x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[status-argument-shape-150476f4] | The positional is accepted in the shape the registration declares. |
| case:route:entrypoint:fleet/status/minimum | minimum | {"members": ["argument:route:entrypoint:fleet/status/campaign", "option:route:entrypoint:fleet/status/--transport"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/status/--transport"], "required_argument_values": {"argument:route:entrypoint:fleet/status/campaign": 1}, "required_arguments": ["argument:route:entrypoint:fleet/status/campaign"], "required_exclusive_groups": {}, "required_options": []}} | REFUSE | status x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[status-minimum-02d02e11] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/status/option-choice/option:route:entrypoint:fleet/status/--transport/ad3632c423 | option-choice | {"members": ["option:route:entrypoint:fleet/status/--transport"], "shape": {"choice": "hx", "option_id": "option:route:entrypoint:fleet/status/--transport"}} | REFUSE | status x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[status-option-choice-6a9602cb] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/status/option-spelling/option:route:entrypoint:fleet/status/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/status/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/status/--debug-raw", "spelling": "--debug-raw"}} | REFUSE | status x --debug-raw | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[status-option-spelling-bc614cbf] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/status/option-spelling/option:route:entrypoint:fleet/status/--hx/--hx | option-spelling | {"members": ["option:route:entrypoint:fleet/status/--hx"], "shape": {"option_id": "option:route:entrypoint:fleet/status/--hx", "spelling": "--hx"}} | REFUSE | status x --hx x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[status-option-spelling-515e6c91] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/status/option-spelling/option:route:entrypoint:fleet/status/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/status/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/status/--json", "spelling": "--json"}} | REFUSE | status x --json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[status-option-spelling-938b3cca] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/status/option-spelling/option:route:entrypoint:fleet/status/--state-dir/--state-dir | option-spelling | {"members": ["option:route:entrypoint:fleet/status/--state-dir"], "shape": {"option_id": "option:route:entrypoint:fleet/status/--state-dir", "spelling": "--state-dir"}} | REFUSE | status x --state-dir x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[status-option-spelling-46706cf4] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/status/option-spelling/option:route:entrypoint:fleet/status/--transport/--transport | option-spelling | {"members": ["option:route:entrypoint:fleet/status/--transport"], "shape": {"option_id": "option:route:entrypoint:fleet/status/--transport", "spelling": "--transport"}} | REFUSE | status x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[status-option-spelling-909b9cea] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/tick/argument-shape/argument:route:entrypoint:fleet/tick/campaign | argument-shape | {"members": ["argument:route:entrypoint:fleet/tick/campaign"], "shape": {"argument_id": "argument:route:entrypoint:fleet/tick/campaign"}} | REFUSE | tick x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[tick-argument-shape-e4042b08] | The positional is accepted in the shape the registration declares. |
| case:route:entrypoint:fleet/tick/minimum | minimum | {"members": ["argument:route:entrypoint:fleet/tick/campaign", "option:route:entrypoint:fleet/tick/--transport"], "shape": {"defaulted_options": ["option:route:entrypoint:fleet/tick/--transport"], "required_argument_values": {"argument:route:entrypoint:fleet/tick/campaign": 1}, "required_arguments": ["argument:route:entrypoint:fleet/tick/campaign"], "required_exclusive_groups": {}, "required_options": []}} | REFUSE | tick x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[tick-minimum-24ae052f] | The route's minimum executable syntax is accepted and reaches its handler or its own input validation. |
| case:route:entrypoint:fleet/tick/option-choice/option:route:entrypoint:fleet/tick/--transport/ad3632c423 | option-choice | {"members": ["option:route:entrypoint:fleet/tick/--transport"], "shape": {"choice": "hx", "option_id": "option:route:entrypoint:fleet/tick/--transport"}} | REFUSE | tick x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[tick-option-choice-3a1e447f] | A declared choice of the option is accepted. |
| case:route:entrypoint:fleet/tick/option-spelling/option:route:entrypoint:fleet/tick/--debug-raw/--debug-raw | option-spelling | {"members": ["option:route:entrypoint:fleet/tick/--debug-raw"], "shape": {"option_id": "option:route:entrypoint:fleet/tick/--debug-raw", "spelling": "--debug-raw"}} | REFUSE | tick x --debug-raw | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[tick-option-spelling-ccaa8c64] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/tick/option-spelling/option:route:entrypoint:fleet/tick/--hx/--hx | option-spelling | {"members": ["option:route:entrypoint:fleet/tick/--hx"], "shape": {"option_id": "option:route:entrypoint:fleet/tick/--hx", "spelling": "--hx"}} | REFUSE | tick x --hx x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[tick-option-spelling-581fedea] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/tick/option-spelling/option:route:entrypoint:fleet/tick/--json/--json | option-spelling | {"members": ["option:route:entrypoint:fleet/tick/--json"], "shape": {"option_id": "option:route:entrypoint:fleet/tick/--json", "spelling": "--json"}} | REFUSE | tick x --json | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[tick-option-spelling-17ca8459] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/tick/option-spelling/option:route:entrypoint:fleet/tick/--state-dir/--state-dir | option-spelling | {"members": ["option:route:entrypoint:fleet/tick/--state-dir"], "shape": {"option_id": "option:route:entrypoint:fleet/tick/--state-dir", "spelling": "--state-dir"}} | REFUSE | tick x --state-dir x | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[tick-option-spelling-2a38b9b7] | The option is accepted in exactly this spelling (no prefix abbreviation). |
| case:route:entrypoint:fleet/tick/option-spelling/option:route:entrypoint:fleet/tick/--transport/--transport | option-spelling | {"members": ["option:route:entrypoint:fleet/tick/--transport"], "shape": {"option_id": "option:route:entrypoint:fleet/tick/--transport", "spelling": "--transport"}} | REFUSE | tick x --transport hx | {"status": 2, "stderr contains": "[ERROR]", "stdout contains": ""} | Refused by the shared parser, a declared constraint or the verb's own input validation before any host is contacted; no campaign state and no file outside the sandbox is changed. | tests/test_cli_review_cases.py::test_reviewed_case[tick-option-spelling-4f689510] | The option is accepted in exactly this spelling (no prefix abbreviation). |

### Open review findings

None.
<!-- cli-extended-surface:end -->

### The campaign state directory

`--state-dir` is a verb-local option of `run`, `status`, `tick` and `collect`. Its
default is not a declarative option default: the CLI derives
`$XDG_STATE_HOME/fleetd/campaigns`, or `~/.local/state/fleetd/campaigns` when
`XDG_STATE_HOME` is unset, from the environment of the invocation that runs the
command. A declarative default would be resolved while the registry is built and
would therefore bake one machine's home directory into the exported surface
manifest, the generated grammar region below and every reviewed-case signature;
see [the design guide](DESIGN-GUIDE.md#why-state-dir-has-no-declarative-default).
Keep that local state outside Git and pass the same value to every command of one
campaign.
