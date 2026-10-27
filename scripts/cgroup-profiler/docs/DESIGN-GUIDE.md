# cgroup-profiler design guide

This document records why the RG-55 daemon behaves as it does. The user-facing
feature list is in [`README.md`](../README.md); worked adoption belongs in
[`CONSUMERS.md`](CONSUMERS.md).

## Daemon safety and placement

The managed `cgprofile-host-daemon` is the one deliberate host-PID-namespace
exception: DAMON sysfs resolves `pid_target` in the writer's active PID
namespace, and a read-only `/hostproc` bind alone does not change that
namespace. The daemon still keeps a private cgroup namespace, has no network,
and receives no Docker socket. Workload, helper, gate, and test containers
remain private in every namespace. `serve` requires the explicit
`CGPROFILE_PID_NAMESPACE_MODE=host` setting and verifies that the selected
host-proc PID namespace is the one it runs in; the helper's default remains
`private`.

Host PID mode makes host PID numbers addressable to DAMON sysfs; it is
necessary for this `pid_target` path but not sufficient for a valid DAMON
context. The prior P1 live probe on this host used both host PID and host
cgroup namespaces and still received `EINVAL` at `kdamond_commit`; that did
not isolate the current host-PID/private-cgroup configuration. The new live
probe must demonstrate an actual context start/stop, or retain the exact
kernel refusal as an unresolved availability gap. See the historical
[`P1 acceptance report`](../nyxloom-trove/reports/cgprofile-P1-DAEMON-REPORT.md).

This grants the daemon visibility of host PID identities and additional
process-table/PID-operation authority. It is an incremental exposure, not a
claim that the daemon was otherwise sandboxed: it is already privileged and
has DAMON sysfs, host cgroup, and systemd-manager access. The D-25 write guard
and request validation constrain ordinary code paths, not arbitrary execution
after daemon compromise. No enforcement path signals a numeric PID; stall
enforcement acts only through a verified cgroup boundary. D-15's no-hidden-mutation boundary remains: cap and migration
writes exist only through the explicit, opt-in D-20/D-25 placement request.
Under D-31, that feature asks systemd to create a transient delegated scope
under the verified gates slice, then creates a token-named `rg-*` leaf below
the scope. It applies and reads back leaf controls, moves the lane's processes
into the leaf, and moves identity-verified survivors back on stop; explicit
shared-scope `--on-stall kill` may write that leaf's `cgroup.kill`. For
`scope=container`, kill instead writes only
the exact runtime cgroup's `cgroup.kill`, after its leaf name proves the
requested full container ID and its resolved path is beneath the verified,
bounded gates slice. It never moves or changes the target's Docker limits.
`CgroupWriteGuard` is the program-level allowlist for those operations. It is
bound to the session's exact delegated scope and token leaf or exact target
container cgroup and refuses an existing scope/leaf: an orphan or symlink
might belong to another lane. The guard covers enabling required controllers
only at the delegated scope root and creation/removal of only the session's
leaf. Process moves go through the systemd manager; no raw write to the
systemd-owned gates slice or origin cgroup is used. The daemon's host
cgroup-v2 bind is therefore writable: Linux cgroupfs placement cannot work
through a read-only bind. This is a deliberate, bounded application-level
exception to observation-only operation, not a kernel sandbox or general
host-control surface.

### Why placement uses a delegated scope

The gates slice is the systemd-owned capacity boundary. A slice is not the
delegation boundary: systemd owns its unit tree and does not support handing a
slice's child-management authority to cgprofile. Directly creating
`dev-gates.slice/rg-<token>` would make systemd and the daemon competing
managers, and the live manager refused migration into the non-delegated path.
Setting `Delegate=yes` on the slice does not fix that ownership conflict.

Instead, systemd creates a uniquely named `rg-profile-<token>.scope` beneath
the verified gates slice and delegates the required controllers there. It
remains the owner of the scope unit; cgprofile owns only `rg-<token>` below
that scope. The systemd-reported `ControlGroup` is read back and checked—it is
not reconstructed from the requested unit name.

```text
run-gate / ciu  ── ctl request ──► cgprofile daemon
                                         │ host system D-Bus
                                         │ create scope / move verified PIDs
                                         ▼
systemd Manager (owns unit boundary)
  dev.slice/dev-gates.slice
    └── rg-profile-<token>.scope   systemd owns; Delegate=cpu,memory,pids
          └── rg-<token>           cgprofile owns leaf and limits
                └── samples ──► daemon summary / ctl response
```

### Run-Gate is an installed SemVer consumer

The installed Run-Gate wheel is the daemon's supported consumer, and its
SemVer is the public compatibility identity. A project-local copy of
`run-gate.py` can drift
from that installed package and is no longer a supported integration. Keeping
the contract tied to the installed CLI makes the compatibility check describe
the code that actually drives the lane. See the
[Run-Gate wheel contract](../../../run-gate-project/CONSUMERS.md#distribution-installed-wheel-and-semver).

Scope creation registers initial PIDs with systemd. cgprofile then moves each
validated PID from the scope root into the leaf through
`AttachProcessesToUnit`, verifies membership, enables controllers only after
the scope root is empty (cgroup v2's no-internal-process rule), and applies
leaf controls. Later descendants use the same validated manager path. On stop,
the daemon restores only same-identity survivors to their recorded original
unit/subgroup, verifies the scope and leaf are empty, removes its leaf, then
asks systemd to stop the exact empty scope. It never stops a nonempty scope as
a cleanup shortcut. A durable journal lets restart recovery repeat these
checks; unknown membership or process identity leaves the evidence intact for
operator repair. The same rule applies when start fails after placement: the
daemon attempts identity-checked rollback before discarding the unfinished
session. Retention requires a positively complete placement journal before it
may prune a finished session, since a finished summary alone cannot prove that
the process left the delegated scope.

### Preserve the original unit so stop can restore into it

`StartTransientUnit(PIDs=...)` moves the initial lane processes out of their
source systemd units. If that empties a Docker-created `.scope`, systemd may
retire the source unit immediately; the recorded unit/subgroup is then no
longer a valid exact restoration target. Before the initial transfer,
cgprofile inventories each recorded source unit's full cgroup subtree and
requires at least one non-moving process to remain, and requires every
selected, still-live process to be listed in its own recorded source unit
(a process that left it since capture would otherwise be moved out of an
unrelated unit and restored to a stale origin). It repeats the check
immediately before each later migration from an origin unit. A known final
member produces `place-refused:origin-scope-would-empty`; unavailable or
inconsistent membership produces `place-refused:identity-unavailable`. Both
are placement refusals, not session failures, and they never authorize a move
to a guessed parent cgroup.

This is a bounded precondition, not an atomic lease on the source unit: an
unrelated process can exit after enumeration and before systemd completes a
move. The manager API does not combine the membership observation and PID
transfer into one conditional operation. If such a race removes the origin
unit, cgprofile fails closed, preserves the journal/leaf evidence, and reports
the incomplete restore rather than inventing a destination. Eliminating that
race would require a stronger source-lifetime mechanism (or launching the
workload inside its final leaf); neither is silently claimed by this
placement contract.

### Memory accounting is charge-based

Placement moves already-running processes; it does not move memory charges for
pages faulted before the move. The leaf's `memory.current`, `memory.peak`,
`memory.stat`, pressure, and related counters therefore report cgroup charges
attributed to the leaf, not the total RSS of every process now in it. The
leaf's `memory.high` and `memory.max` likewise constrain charges attributed
there and are not a hard cap on all memory already resident in the workload.
We keep these kernel-native counters because they are useful and directly
verifiable, but label their semantics rather than silently adding an
approximate process-RSS substitute. A future design that starts work inside
the leaf before allocations, or reports a separately named RSS/PSS estimate,
would have different adoption and accuracy costs and needs its own contract.
The detailed metric-source and disclosure rules are in the
[`RG-55 placement design`](../../../run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md#a3-placement-ownership-correction-delegated-scope-below-dev-gatesslice-2026-09-30).

The daemon's host system bus is separate from the consumer control socket. The
cockpit receives only the cgprofile ctl surface; it does not need the system
bus. P1 and P6 introduced the observer and opt-in placement capabilities in
separate implementation tranches. RW-434 settles their combined tree as the
first 1.0.0 release, including the system-bus manager bridge and writable host
cgroup view required for explicit placement; 1.1.0 changes none of those
capabilities (it carries the Sol-review fixes and the daemon slice guard, see
`CHANGES.md`). An operator with unrestricted
Docker access already has host-administrator authority, but that does not
make these daemon mounts irrelevant: a compromised daemon process can exercise
the mounted cgroupfs and systemd manager authority directly, beyond normal
Python request checks. The deployed design deliberately has no broker: cgprofile calls systemd
directly, and the daemon's path checks, PID validation, and RPC allowlist
constrain normal behavior but do not contain arbitrary code execution in the
daemon. A read-only bind of a D-Bus socket is not read-only RPC authority.
Moving these calls into a broker would improve isolation only if the broker
has a genuinely narrower OS authority, a small authenticated request surface,
and its own tested identity/path validation. A broker that can perform the
same host-root operations and trusts the daemon's claims may merely move the
compromise point. The detailed no-broker decision and future broker tradeoffs
are recorded in the
[`RG-55 placement design`](../../../run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md#a3-placement-ownership-correction-delegated-scope-below-dev-gatesslice-2026-09-30).

Stall enforcement never signals a numeric PID, despite the daemon's host PID
namespace. For `scope=container`, enforcement also requires
`cgroup.events` to report `populated 1` for the exact target container
cgroup; missing, malformed, or empty state is a refusal, not a successful
kill. A shared-scope kill requires the caller's explicit placement request
and a verified leaf; placement is never invented solely to enable kill. If
that boundary is unavailable before start, the daemon refuses the requested
policy as `bad-policy`; if a cgroup kill later fails, it reports `reported`
rather than claiming success. For consumers such as run-gate that do not
already plan placement, the client requests `report` and its own watchdog
remains the verdict authority (RW-379/RW-380).

PID movement continues through the host systemd manager. Host PID mode means
the daemon can address those PIDs, but does not change ownership of the
systemd-created delegated scope: systemd attaches each identity-checked PID
to the verified unit/subgroup on placement and restoration. Both move
directions require a verified host-proc view and post-move identity and
cgroup membership. A successful systemd-mediated move is recorded as a
`cgroup_write` event. If a survivor cannot be enumerated or restored, the
daemon reports the precise scope path and retains the lane leaf rather than
concealing a stranded task.

The progress stream is a lane-controlled path reached through its process
root. The watcher opens it nonblocking and reads only a regular file; a FIFO
or device is absent evidence, so it cannot freeze the sampler or its kill
clock. Only complete newline-terminated JSON events reset the progress clock;
appending an unfinished line cannot postpone a stall verdict.
The exec watch bridge treats a closed socket without exactly one terminal
`end` as a daemon fault, so a dropped connection cannot masquerade as a
finished session.

The socket listener is serial until a request is dispatched or `watch` is
handed to its own thread. Each initial request line is therefore bounded to
1 MiB and an absolute 25-second wall-clock deadline, so trickle bytes cannot
monopolize every control verb or grow request memory without limit. After a
`watch` request is accepted, its stream has no server-side timeout; silence
between readings is normal.

The daemon and one-shot helper use different PID modes. The daemon joins only
the host PID namespace; its cgroup namespace stays private. The helper keeps
private PID/cgroup namespaces and binds host `/proc` read-only at `/hostproc`,
selected through `CGPROFILE_PROC_ROOT`; its host cgroup bind is read-only
because it does not perform daemon placement. In daemon mode, the selected
host-proc PID-namespace identity must equal the daemon's. In helper mode, the
existing broader-proc-view check remains: PID 1 in the selected proc view
must belong to a namespace distinct from the helper's. Container names,
labels, and helper-mode `self` are resolved on the Docker-aware caller to full
container IDs; the daemon locates those IDs in its explicit host cgroup view.
The daemon's PID check is a consistency check between its configured proc view
and local namespace, not an independent attestation that the procfs bind is
the host's. The managed CIU Compose service provides the deployment fact with
`pid: "host"` and an explicit host `/proc` bind.
The required `/run/cgprofile` socket directory uses Compose's long bind form
with `create_host_path: false`: if host-setup has not created the source,
deployment must fail instead of Docker manufacturing an empty host directory
that hides the missing prerequisite and leaves the daemon writing sockets to
the wrong place.
The daemon is not a general capability-changing tool: it has no capability
mutation option, imports no `TempCaps`, and has no Docker socket. It writes its
session volume and the DAMON admin sysfs state required for observation, in
addition to the placement authority described above. A consumer that
needs another cap change must use a separate, explicitly authorized tool;
adding a hidden fallback here would make the observer change the workload it
is measuring. These code and deployment choices are not kernel-enforced
containment of a compromised privileged daemon, and the private namespaces do
not make this a least-privilege service.
Token-scoped sessions find the exact token in host `/proc` and walk its
process descendants there, rather than treating a namespace-local PID as a
host PID. Token roots remain constrained to the selected target cgroup; only
their descendants may be attributed after moving elsewhere. DAMON sysfs and
the session directory remain separate intended write surfaces.

### Private namespaces and host views

`self` in helper mode means the container that invoked the CLI, not the
short-lived helper. The driver resolves its own full Docker ID before
re-exec; the helper can then locate that container by its cgroup leaf without
guessing from `/proc/self/cgroup`. A caller-visible `pid:N` is accepted only
when its cgroup namespace matches the caller's. The caller carries the PID-
and cgroup-namespace inodes, namespace-local PID, process start time, and
namespace-relative cgroup path under the caller container ID. In the
helper's host `/proc` view, the resolver matches those facts and exact cgroup
membership, rechecks the process start time, and requires exactly one match
before returning the helper-visible PID for proc sampling and DAMON. A
mismatched namespace, changed or unavailable identity, unsafe path, or
ambiguous match is a refusal, not a guess from a numeric PID. See the worked
command in
[`CONSUMERS.md`](CONSUMERS.md#resolve-a-target-from-a-cockpit).

### Native R2 resource observation is data-only, not a broker

Assay B145 must compare process- and memory-limit event counters through the
candidate's enforcing ancestors. The tester container stays in a private
cgroup namespace; making it host-namespaced would reveal host hierarchy state
and is not needed for this read. Instead, Run-Gate can mount a second,
observer-only socket from the existing daemon:

```text
Assay worker -- {thread_id} --> resource-observer.sock
                                      |
                       SO_PEERCRED + host proc/cgroup view
                                      |
Assay worker <-- own gates-slice ancestry and counters
```

The request supplies only a native TID. The daemon binds it to the connecting
PID/UID, proves the TID belongs to that peer and that its live threads share
one cgroup/credential/capability state, derives the path itself, and returns only that
peer cgroup's complete ancestry beneath `dev-gates.slice`. It does not return
PIDs or sibling data. The daemon's own cgroup namespace is private too, so it
maps outside-peer paths only when the leading `..` count exactly matches the
full-root cgroup2 mount's `mountinfo` root; arbitrary normalization is
refused. The response carries per-path limits, event counters, and mount ID.
The DAC proof checks `cgroup.procs` on every ancestor through `/`. That also
prevents thread-only migration through `cgroup.threads`: Linux's
[cgroup-v2 migration rules](https://docs.kernel.org/admin-guide/cgroup-v2.html#core-interface-files)
require write access to `cgroup.procs` at the source/destination common
ancestor for a TID move, so a peer cannot redirect its accounting to an
unsampled sibling. The observer need not enumerate sibling destinations.

Resource-observer contract 2 adds explicit scope metadata and the
`pids.events.local` and `memory.events.local` values alongside the existing
event files. The regular files can be hierarchical or local depending on the
host mount options. The observer refuses `pids_localevents`: its local fork
failure count records where the process attempted to fork, not which cgroup's
`pids.max` rejected the fork. That loses the distinction between a candidate
limit and a shared ancestor limit. `memory_localevents` remains supported and
is reported explicitly; when local memory counters are active on the candidate,
the observer also proves the candidate has no child cgroups. These checks keep
consumers from mistaking local event scope for local limit enforcement.
Contract 2 is the only supported resource-observer version: accepting the old
shape would let a client treat hierarchical counters as local evidence.
Assay requests contract 2 because its candidate counter and ancestor-local
counter play different roles in mutation classification. The daemon's
separate control listener continues to use contract 1. See the worked request
and response in [`PROTOCOL.md`](PROTOCOL.md#8-native-r2-resource-observer-rg-89).
The control `version` response advertises the observer contract, socket path,
and whether that listener bound successfully. This makes a required contract
upgrade visible before a long mutation lane starts; the control contract number
itself remains 1.

The observer is a distinct listener, not a read-only bind of `ctl.sock`:
filesystem read-only does not restrict the verbs of an AF_UNIX service. The
listener accepts one `resource-limits` query and no start/stop/placement
operation. Run-Gate validates the fixed socket path and bind-mounts its
dedicated `/run/cgprofile/observer` DIRECTORY read-only (so a daemon restart
that replaces the socket inode stays resolvable), forwarding the socket's
observed group ID; the control socket's directory is never mounted. It does not add a Docker socket,
host PID namespace, host cgroup namespace, or a second daemon.

The observer request is one newline-terminated JSON line, at most 4096 bytes
including the newline, with a five-second absolute deadline. The bound
includes the delimiter so an oversized complete line cannot be dispatched
just because a single socket read returns it all at once. A malformed line,
oversize request, or expired deadline does not reach the observer; the exact
wire refusal behavior is pinned in [`PROTOCOL.md`](PROTOCOL.md#8-native-r2-resource-observer-rg-89)
and contract §9.

This gives the gate only the missing read path and keeps its namespace private;
it also avoids another privileged service and duplicated authorization
surface. It does **not** contain arbitrary code execution in cgprofile: the
existing daemon has host-PID visibility, writable host cgroupfs for placement,
and systemd-manager access. The protocol narrows normal requests, not the
daemon's authority after compromise. An operator with unrestricted Docker
access already has host-administrator capability; that fact does not reduce
the daemon's independent compromise impact. A future broker is worthwhile
only if its OS authority is genuinely narrower, its authenticated request
surface is small, and the separation survives compromise of cgprofile; merely
moving equivalent root operations to another process would move, not remove,
the trust problem. The wire and failure behavior are specified in
[`PROTOCOL.md`](PROTOCOL.md#8-native-r2-resource-observer-rg-89) and contract §9.

The observed leaf belongs to the tester container, whose Assay worker threads
share that cgroup. Two concurrent mutant commands could overlap their
before/after counter windows: one mutant's limit event would appear in both
windows and could be attributed to the other. P1 therefore declares
`[lanes.r2.judge.mutation] jobs = 1`. This serializes candidate counter
windows until per-worker cgroups and independent counter bindings exist.
The 24-hour lane budget remains a safety ceiling; `--resume` continues the
same exact-tree campaign after a budget stop. A limit event from any other
process in the tester cgroup still makes a candidate inconclusive, so the
tester lane must not launch unrelated work there during R2.

The short-lived helper-placement probe runs under the injected interactive
slice only after confirming that it matches the cockpit container's actual
Docker parent. It asks host systemd for `LoadState`, `FragmentPath`, and
`ControlGroup` over a read-only DBus socket mount and confirms the host cgroup
directory exists. This is necessary because an unknown slice can be silently
materialized as a transient unit; checking only for a cgroup directory would
certify the unsafe state the probe is meant to catch.

The long-running daemon applies the same trust principle before placement
and before claiming `host.gates_slice.present`: it verifies the expected
loaded, non-transient systemd unit and exact control-group path, then requires
finite positive memory and CPU ceilings. A bare directory is not a capacity
object. If proof is absent, the host snapshot reports the slice absent and
placement refuses it without failing the profiling session.

The DAMON `nr_kdamonds` attribute is a global registry, not an append-only
array. A successful count write removes and rebuilds every kdamond directory,
including when the written value is unchanged. Consequently, treating the
initial count as a foreign-prefix boundary was incorrect: appending one
cgprofile slot could erase a stopped foreign monitor's staged operations,
targets, intervals, and schemes, and shrinking back to the original number
would not restore that configuration. The sysfs interface has no per-slot
owner marker or atomic lease.

RG-55 therefore chooses preservation over partial DAMON availability. Both
the daemon pool and the one-shot collector refuse to write `nr_kdamonds` when
the observed baseline is nonzero, whether those existing monitors are on or
off. DAMON is optional, so the refusal is reported as unavailable while normal
profiling and command execution continue (R-36h). The daemon reserves its
entire configured capacity (`max_sessions`) with a single count write from
zero before any monitor starts; there is no sequence of per-slot growth writes
that could repeatedly rebuild already-reserved entries. Linux also refuses a
resize while any kdamond is running, including a monitor owned by another
program; see upstream v7.1 [`nr_kdamonds_store()` and directory growth
path](https://github.com/torvalds/linux/blob/v7.1/mm/damon/sysfs.c#L1923-L1944).

The sysfs registry is not a complete inventory of every kernel DAMON user.
For example, the kernel's special-purpose `DAMON_STAT` module can run through
the DAMON API outside the sysfs `nr_kdamonds` table. Linux documents these
special-purpose modules as mutually exclusive: while one is active, another
DAMON start can return `-EBUSY` ([design](https://www.kernel.org/doc/html/v7.1/mm/damon/design.html),
[DAMON_STAT](https://docs.kernel.org/admin-guide/mm/damon/stat.html)). On the
RG-55 host, `damon_stat/enabled` read `Y` while `nr_kdamonds` read `0`, and a
real cgprofile start returned `EBUSY`. The zero registry count therefore did
not establish that the host DAMON facility was free. cgprofile does not
disable or reconfigure `DAMON_STAT`: that would silently change host-wide
monitoring outside the lane's authority. It marks DAMON unavailable and
continues ordinary profiling under R-36h. See
[`CONSUMERS.md`'s host troubleshooting note](CONSUMERS.md#host-damon_stat-exclusivity)
for read-only checks and the operator procedure.

This is a deliberate choice of interface, not a preference for sysfs as a
generic convenience. `DAMON_STAT` is designed to expose compact global
statistics: whole-physical-memory idle-time percentiles and estimated
bandwidth, with automatically tuned intervals. It does not give cgprofile a
per-lane target boundary. DAMON sysfs is the generic privileged programming
ABI: it lets cgprofile configure virtual-address contexts and PID targets for
the specific process tree it has resolved, and request statistics without an
action that changes workload memory. That targeting and result shape are what
RG-55 needs. The cost is exclusive ownership of DAMON while the sysfs monitor
runs; when another special-purpose module such as `DAMON_STAT` is active,
cgprofile must preserve it and report DAMON unavailable rather than silently
reconfigure host policy ([DAMON sysfs usage](https://docs.kernel.org/7.1/admin-guide/mm/damon/usage.html),
[DAMON_STAT](https://docs.kernel.org/7.1/admin-guide/mm/damon/stat.html)). The
operator-approved 2026-10-09 live probe confirmed
that relationship: with the host's `DAMON_STAT` setting temporarily `N`, the
real daemon collected 36 per-session DAMON records; after stopping the session,
`DAMON_STAT` was restored to `Y`. The kernel build on this Debian 13 host sets
`CONFIG_DAMON_STAT_ENABLED_DEFAULT=y`; other Debian kernels may carry different
configs.

Each newly reserved `state` node is held open as an identity pin. The kernel's
kernfs identity lets cgprofile detect a later same-count rewrite that leaves
the numeric count and replacement `state=off` unchanged. Before reuse, stop,
or shrink, the current path must still resolve to the pinned inode; a missing
or replaced node is quarantined and cgprofile neither stops it nor shrinks
across it. Teardown additionally verifies that every owned monitor is off and
the registry count exactly matches the reserved pool before restoring zero.
An ambiguous write/readback or failed identity proof is a refusal, not
permission to invent ownership. This favors an observable leftover empty slot
over deleting or reusing a configuration whose owner cannot be proved.

The inode check detects an out-of-band replacement between operations; it is
not a kernel-enforced lease and cannot make a privileged count writer racing
between a check and a sysfs write atomic. cgprofile closes its own
cross-process race with an advisory `flock` on the host-shared
`/run/cgprofile/damon.lock`: a one-shot collector holds the lock for its full
session, while daemon-pool reserve/release/restore operations take it around
the registry transaction. This serializes the daemon and cgprofile helper
processes without making one-shot profiling wait behind another DAMON run.
The privileged helper bind-mounts this single lock file at
`/tmp/cgprofile-damon.lock`; it does not receive `/run/cgprofile/ctl.sock`.
When a daemon pool already occupies the registry, a one-shot collector takes
the lock, observes the nonempty table, and declines DAMON without changing
ordinary sampling.

The advisory lock only works among writers that honor it. It is not a
kernel-enforced lease and cannot protect against `damo` or another privileged
program that ignores the lock, nor eliminate the inode-check/sysfs-write race
against such a writer. Operators must not run unrelated privileged DAMON
configurators concurrently. A future kernel ownership API or broker that
actually owns the DAMON registry could remove that limitation; a broker with
the same raw sysfs access but no exclusive ownership would not.

Before a newly owned monitor is started, cgprofile writes the exact
`nr_targets` count and then every target PID. DAMON recreates target-input
directories when the target count changes; this is safe only after the slot's
identity is still proven as pool-owned. During a session, target recommits
rebuild the exact array, preventing a shrinking subtree or reused slot from
continuing to watch former lane PIDs.

The one-shot `run`/`attach` collector follows the same nonzero-baseline refusal
and optional-evidence rule. A startup or later collection error closes only
DAMON, writes ordinary samples and the normal finished manifest, and lets the
wrapped command run to completion. In particular, `cgprofile run` must not
wait for DAMON before launching the user's command or let a missing optional
series alter its verdict.

### DAMON availability is not session readiness

The `damon` field in `ctl version` answers a narrow capability question:
cgprofile loaded its DAMON analysis library and can see the admin sysfs
interface. It does not create a kdamond, configure an operation, or prove that
the kernel accepts a monitoring context. Initial configuration is written
while the kdamond is off, including an exact target count and every target PID,
then `state=on` creates and starts it. `state=commit` is an online update for
an already-running kdamond; issuing it before the first `on` returns `EINVAL`.
When the discovered PID set changes, cgprofile rebuilds the sysfs target input
array to the exact new count, writes every PID, then commits. DAMON maps that
source array onto the live target list and removes live targets with no source
entry; this prevents a shrinking subtree or reused pool slot from continuing
to monitor departed lane PIDs. An unchanged set does not recommit, and a
temporary empty discovery remains a no-op rather than stopping monitoring.
See the
[kernel DAMON usage documentation](https://docs.kernel.org/6.19/admin-guide/mm/damon/usage.html)
for the interface and commit semantics. The kernel's
[`damon_sysfs_commit_input()`](https://github.com/torvalds/linux/blob/master/mm/damon/sysfs.c#L2008-L2024)
explicitly refuses a stopped kdamond; its
[`state=on` path](https://github.com/torvalds/linux/blob/master/mm/damon/sysfs.c#L2113-L2142)
builds and starts the context from those initial inputs. The same sysfs
implementation rejects `nr_kdamonds` changes while any kdamond is running;
the pool's pre-reservation and verified teardown above are the lifecycle
response to that global constraint.

DAMON remains optional profiling evidence throughout the session, not just at
startup. If a later target recommit or collector read fails, cgprofile stops
and releases only that DAMON session, records `unavailable:<reason>` in the
summary and manifest, and continues the ordinary cgroup/CPU/memory/liveness
samples. That runtime failure must not abort the profiling session or affect
the measured program's verdict (R-36h). Inspect the session's `start` response
for `damon: "on"` versus `unavailable:<reason>`, then inspect the final summary
and persisted `damon.jsonl` series to establish whether DAMON samples were
actually collected. Neither `ctl version` reporting `available` nor a request
with `--damon on` proves that DAMON ran. Do not report a DAMON overhead
measurement unless the compared run actually produced DAMON samples.

## Session identity and start semantics

The non-null token is the opt-in idempotency key `(container_id, token)` used by
run-gate retries. `token = null` has no registry key: two starts represent two
independent observations and compete for `max_sessions`.

Start reads target, host, slice, and attributed PIDs once, then records those
values as sample zero before the sampling thread begins. This preserves the
baseline and makes an immediate stop a valid one-sample summary. Monotonic
sample timestamps are retained; `cores_max` uses each positive adjacent
timestamp delta and becomes null when a needed timestamp or delta is not
usable.

The daemon updates the summary as each tick arrives: cumulative-counter
references, extrema, limit-drift count, host/slice endpoints, PID union, and
CPU rate validity are maintained directly. Exact nearest-rank percentiles use
AVL order-statistic multisets, so insertion and final rank selection are
O(log n); normal `ctl stop` never scans the raw series or recomputes summary
blocks from it. The full sample, host, and DAMON series are persisted separately
for reports and crash recovery. The exact percentile state still grows with
the number of distinct observed values; it is smaller than retaining every
raw sample object, but it is not a constant-memory sketch. If the daemon
restarts, orphan recovery deliberately replays the durable series because the
in-memory accumulator was lost. That recovery path is distinct from normal
stop-time finalization.

## Contract boundary

The wire contract is major version 1 and summary documents use schema 1. A
reachable Unix socket is not enough: `ctl` validates that the response is an
object with a boolean `ok`, contract major 1, and the shape for the requested
verb before printing it. Valid daemon errors remain exit 2; malformed or
incompatible peer responses are daemon faults (exit 3).

Container IDs are resolved against the mounted host cgroup tree, which is
the identity source. A complete search with no matching scope proves
`target-not-found`; an unreadable root or branch does not. The walker
therefore propagates search errors instead of converting them to an empty
result. The server closes only that request's socket, leaving other sessions
alive; `ctl` reports a daemon fault at exit 3. Using Docker metadata as a
fallback would let a name claim a cgroup that the observer could not verify.

The control protocol also preserves the distinction between an omitted value
and an invalid value. For `start`, omitting `damon` asks the daemon to apply
its configured `damon_default`; a caller that explicitly sends `damon: null`
is refused. Treating a present null as absence would silently replace
malformed input with a policy fact owned by the daemon.

```json
{
  "ok": true,
  "contract": 1,
  "cgprofile": "1.0.0",
  "daemon": {
    "name": "cgprofile-host-daemon",
    "started_at": "2026-09-12T10:15:00Z",
    "damon": "available",
    "damon_default": "on",
    "sessions_live": 0,
    "max_sessions": 16
  }
}
```

`ctl stop` reports `series.damon: null` when DAMON was off, unavailable,
restarted before a sample, or otherwise has no persisted classified sample.
It reports `"damon.jsonl"` only when that evidence exists. No response field
turns an unreadable metric into zero.

## Release identity

The daemon image version is the exact `cgprofile-v<version>` tag passed by
CMRU for its release candidate. CMRU also passes protected
`CMRU_RELEASE_COMMIT` and `CMRU_RELEASE_TAG` values to both artifact steps.
Before a build or push, the wrapper checks that the current `HEAD` and the
local tag both peel to that commit, and that the checkout is clean. It creates
a temporary full-repository worktree at the same commit and builds from there,
so version and source cannot come from separate reads of a moving checkout.
Missing, incomplete, or mismatched identity refuses before registry login.
An untagged local build resolves its version and commit together and uses
`0.0.0-dev`. Ambient `CGPROFILE_VERSION` never creates release authority.
CMRU clears ambient release-pair values from non-artifact steps. The pair is
source-consistency metadata, not an authentication token or proof of CMRU
provenance, so the supported publication workflow remains `cmru release`.

Deployment verification checks two identities. `ctl version --json` confirms
the daemon's software version and advertised protocol capabilities; the
running container's `org.opencontainers.image.revision` label binds its image
to source. Compare that label with the commit peeled from the pinned
`cgprofile-v<version>` release tag after CIU brings up the service. The
[consumer recipe](CONSUMERS.md#deploy-a-published-daemon-image-with-ciu) checks
the source label on the image actually used by `cgprofile-host-daemon`.

The Bake file itself uses a literal development version and tag instead of
importing environment variables; it supplies no Git revision default. The
wrapper's explicit target overrides carry the version and revision resolved
together from one checked source state. The Dockerfile has no version or
revision default and checks both arguments before package installation, so a
low-level image build cannot silently label an image when no complete
identity was supplied. The wrapper refuses changes in either local build
context and builds from a temporary worktree at the resolved commit, so an
image label cannot name a commit that omits its build inputs. A bare Bake
invocation therefore refuses early; the supported local build path is
`build-push.py --build`. No version lookup depends on network access at runtime.

The local `cgprofile:local` alias is not a release coordinate: because it has
no registry hostname, Docker resolves it to Docker Hub when used as a publish
output. The Bake file separates the local target from a release target whose
default is still the development tag; the validated wrapper overrides the
release tag and image argument after checking the source tag. A bare Bake
command cannot turn `CGPROFILE_VERSION` from the environment into a release
label. CMRU's push step selects only the release target. This keeps developer
convenience from widening the release's external writes. CMRU owns the
protected tag/commit handoff; consumers must invoke `cmru release` instead of
manually exporting either value.
The pasteable CIU pin and verification command live in
[`CONSUMERS.md`](CONSUMERS.md#deploy-a-published-daemon-image-with-ciu).

## The GitHub Release body for the published image

The image on GHCR is cgprofile's only released artifact: nothing is uploaded to the GitHub
Release, so the wheel-style body (artifact name, SHA256) has nothing true to say, and
without a handler the `cgprofile-v<version>` release would have no body at all. CMRU's
`oci-image-release` handler fills that gap. CGProfile's push step therefore has two commands,
in this order:

1. `build-push.py --push`, which pushes the release image;
2. `cmru handler oci-image-release --prefix cgprofile --image ghcr.io/volkb79-2/cgprofile
   --cwd .`, which writes the release body.

The order is the contract, not a convention: the body announces an image, so it must follow
the push of that image, and CMRU refuses (before any release side effect) a configuration in
which anything follows it. The handler derives `:<version>` from the protected
`CMRU_RELEASE_TAG` and reads the notes from the protected candidate commit's
`cgroup-profiler/CHANGES.md`, so the pull instruction and the notes always describe the tag
that was gated. Its `--image` repeats the registered release coordinate with an explicit
`ghcr.io` registry (a bare `owner/name` would be read by Docker as a Docker Hub namespace),
and it is the image `build-push.py` pushes, not a second source of truth: change one and the
published pull instruction no longer matches the image.

What CMRU checks before any side effect, and what it cannot: it validates the handler's
`--prefix`, `--image` and `--cwd`, and that it is the last command, for the selected project
before any prepare step, gate, tag or image push; `cmru release --dry-run` runs the same check
and prints the complete body. After it writes, it reads the release back and succeeds only if
the release has the same ID and tag, exactly the intended body, and the Git tag is still at
the release commit. GitHub cannot make that atomic with creating the release, so a difference
is reported as "the release was written and not rolled back" for inspection; the workflow
narrows that window and reports it honestly rather than claiming to prevent partial public
publication. The notes are the tagged changelog section, never `[Unreleased]` read later,
so a failed or retried release cannot publish different notes than its tag carries. The
[preview and ordering recipe](CONSUMERS.md#preview-the-github-release-body) is pasteable.

## Decisions intentionally left outside this repair

This repair does not invent policy for the S1–S5 follow-up surfaces. Those
decisions remain with the controller and their existing design records.

## Mutation comparison base

The P1 R2 lane uses Assay's `judge.base_source = "request"`: the acceptance
campaign's comparison commit is a fact recorded by the controller, while a
moving `origin/main` would stop including already integrated daemon code.
The registered `r2` command passes run-gate's resolved `{base}` to Assay as
`--request-base`; the full `gate` passes the same ref to its nested `r2`
invocation. Neither lane should silently drop a caller's `--base` flag.

Run-gate can derive a worktree fork or upstream base when its caller omits
`--base`. That derivation is useful for ordinary changed-line lanes but does
not establish this historical P1 campaign's scope. The acceptance command
therefore supplies the exact recorded ref and checks the Assay plan's tree,
candidate count, and source inventory before starting R2. A zero-candidate
plan cannot certify the P1 changes. After a provisional merge, Assay's
merge-commit base handling can change the resolved scope; the post-merge plan
must be checked again rather than transferring a prior-tree count. The
pasteable invocation is in
[`CONSUMERS.md`](CONSUMERS.md#run-the-p1-review-gates).

## Release qualification and the full R2 gate

Two different things are called "the gate" and must not be conflated. CMRU's
release gate is `cmru.toml` `steps.run-tests`: `run-gate r0-r1` (unit tests with
100% line and branch coverage) and `run-gate r3` (canary rejection). The
complete Run-Gate lane `gate` additionally runs `r2`, the Assay
mutation-of-changed-lines campaign, whose budget is a 24-hour resumable window.
R2 is kept out of the release gate because a campaign that long must not block
every routine release, and because it judges one exact tree for one comparison
base rather than the release range.

The operator's release policy allows the full-source R2 campaign to be
deferred. The consequence is stated plainly rather than implied: the initial
1.0.0 release has no completed full-source R2 verdict, and nothing in this
project's documentation, changelog or release configuration claims one. A
later R2 result applies only to the exact tree and base it judged. Passing
`r0-r1` and `r3` qualifies a release for exactly what those lanes test; it is
not mutation evidence.

## An unverified DAMON teardown is a reported fact, not a log line

DAMON is optional evidence, so a failed or unverifiable kdamond teardown must
not change the workload verdict or turn `stop` into an error. But the old
behavior logged `cleanup_confirmed=False` and then returned the same
successful stop as a session whose DAMON was never on, so a consumer could not
tell "off" from "possibly still running". The daemon therefore reports the
disposition separately and additively: `summary.damon_cleanup` (also in the
manifest) is `{confirmed, slot, quarantined}`. `confirmed` is True only when the
owner affirmatively verified its teardown; an exception from the owner, or no
confirmation property at all, is unconfirmed. `quarantined` states whether the
daemon will refuse to reuse that slot, so "unverified" is never read as a
no-reuse guarantee it did not establish.

## `stop` finalizes only after the sampler has exited

`stop` used to join the sampling thread for ten seconds and then release
placement, close DAMON, write `finished` and answer `ok: true` whether or not
the thread had exited. A blocked sampler could then append samples to a
finalized session. Finalization is now conditional on the worker being gone:
a live worker leaves the session live, with its placement and DAMON resources
held and no `finished` manifest, and `stop` answers `stop-incomplete`. The
caller retries; the same session finalizes once the worker exits. Daemon
shutdown logs the incomplete stop and leaves the manifest `running`, which the
next start recovers as an orphan. This is deliberately NOT the CP-24 stop
protocol (bounded finalization with kill evidence); it only forbids the false
completion.

## The daemon proves the slice it is in, not a slice by name

Docker and systemd accept any `cgroup_parent` and auto-create an uninstalled
slice without limits, so a daemon whose compose file names `cgprofile.slice`
can still run unbounded next to production workloads. `serve` therefore refuses
to start unless the cgroup it is actually running in, derived from the host
proc view (`/proc/<host pid>/cgroup` normalized through the cgroup-namespace
root, the same path derivation placement uses), is under the top-level
`cgprofile.slice` and that slice's `memory.max` is a finite positive integer.
A bounded `cgprofile.slice` that merely exists while the container was created
under another parent, or a nested look-alike, does not pass (name for object).
Zero is refused: it is finite but a slice that can hold no memory is a
misconfiguration, and `read_int` folds `max`, empty, unreadable and garbage
into one "not a number" outcome, so the message names every cause.

Rejected alternatives: reading `LoadState` from inside the container (the
daemon has no view of `/etc/systemd/system` or the host systemd, so it cannot
be proven there); a flag to run unbounded anyway (the point is that unbounded
must not look governed). The pre-creation Docker-host `LoadState=loaded` probe
remains the complete fix and is CIU-150, outside the 1.1.0 window. The
refusal runs after container creation, so on `restart: unless-stopped` a host
without the unit crash-loops; that loud failure is intended.

### A wedged worker has a bounded way out

`stop-incomplete` alone would let a permanently wedged sampler hold placement
and DAMON forever. The third consecutive incomplete `stop` therefore finalizes
anyway (`stop_forced: sampler-still-running`, aborted manifest, resources
released) and daemon shutdown forces the same way; a late sample from the
worker is discarded because the session is finished. If the worker holds the
session lock the forced path refuses rather than deadlock the daemon. The
`finished` flag is set only after release, so finalization first sets a
`closed` flag under the session lock; every sample-side action that touches
placement, DAMON or the records re-checks it under that same lock, which closes
the window in which a late sample could migrate or recommit after release began.
The liveness stall kill and the DAMON `collect()`/disable paths are guarded the
same way, and finalization acquires the session lock once, bounded, so a
worker wedged inside a locked action costs one failed finalize (orphan
recovery on restart) rather than a daemon-wide hang. Known
limit: each attempt joins for up to 10 s while holding the server-wide lock, so
a wedged sampler delays every verb for that long per retry.
