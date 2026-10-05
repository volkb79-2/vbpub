# cgroup-profiler design guide

This document records why the RG-55 daemon behaves as it does. The user-facing
feature list is in [`README.md`](../README.md); worked adoption belongs in
[`CONSUMERS.md`](CONSUMERS.md).

## Daemon safety and placement

The daemon never joins host PID, cgroup, or network namespaces and has no
Docker socket. D-15's no-hidden-mutation boundary remains: cap and migration
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
operator repair.

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
bus. Version 1.0.0 did not mount the system bus or a writable host cgroup
tree; v1.1.0 adds both for explicit placement. An operator with unrestricted
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

Stall enforcement never signals a numeric PID: the daemon keeps a private PID
namespace, so host PIDs from its read-only proc view are observations, not
signal handles. For `scope=container`, enforcement also requires
`cgroup.events` to report `populated 1` for the exact target container
cgroup; missing, malformed, or empty state is a refusal, not a successful
kill. A shared-scope kill requires the caller's explicit placement request
and a verified leaf; placement is never invented solely to enable kill. If
that boundary is unavailable before start, the daemon refuses the requested
policy as `bad-policy`; if a cgroup kill later fails, it reports `reported`
rather than claiming success. For consumers such as run-gate that do not
already plan placement, the client requests `report` and its own watchdog
remains the verdict authority (RW-379/RW-380).

PID movement has one namespace-specific seam. A PID read from `/hostproc` is
not necessarily addressable by a writer in the daemon's private PID namespace;
the kernel returns `ESRCH` when that PID is written to `cgroup.procs`. The
daemon uses the host systemd manager to create the delegated scope, place PIDs
into its leaf, and restore survivors to each recorded original unit/subgroup.
Both move directions require a verified host-proc view and post-move identity
and cgroup membership. A successful systemd-mediated move is recorded as a
`cgroup_write` event. If a survivor cannot be enumerated or restored, the
daemon reports the precise scope path and retains the lane leaf rather than
concealing a stranded task. This preserves private PID/cgroup/network
namespaces while making placement and cleanup real.

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

Host visibility does not require joining host namespaces. The daemon and
one-shot helper keep private PID/cgroup namespaces; host `/proc` is explicitly
bound read-only at `/hostproc` and selected through `CGPROFILE_PROC_ROOT`. The
one-shot helper also keeps its host cgroup bind read-only because it does not
perform daemon placement. The daemon checks that PID 1 in its host-proc view
belongs to a PID namespace distinct from its own before serving. Host-visible
PIDs from `/hostproc` are useful for reading process details, but they cannot
be matched against `cgroup.procs` from the private PID namespace: the kernel
translates host tasks to PID 0 there. Accordingly, container names, labels,
and helper-mode `self` are resolved on the Docker-aware caller to full
container IDs; the daemon locates those IDs in its explicit host cgroup view.
The daemon is not a general capability-changing tool: it has no capability
mutation option, imports no `TempCaps`, and has no Docker socket. It writes its
session volume and the DAMON admin sysfs state required for observation, in
addition to the v1.1 placement authority described above. A consumer that
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

The DAMON pool treats the `nr_kdamonds` value read at pool creation as an
ownership boundary. Indices below that baseline are foreign. New sessions use
only indices proven to have been created after the baseline, and teardown
touches only indices that the pool actually handed out. If the baseline cannot
be read, acquisition refuses; index zero is never a placeholder default.

The same rule applies to a bare session: it must prove that its requested
index is the first slot beyond the observed count before it configures or turns
the kdamond on. This keeps a pre-existing monitor on even when a new session
fails part-way through setup. Before `state=on`, startup writes the exact
`nr_targets` count and then every target PID. DAMON's sysfs count write
recreates the input directories; this is required because a pooled kdamond
index can carry the previous session's target array even after it is stopped.

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
builds and starts the context from those initial inputs.

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
  "cgprofile": "1.1.0",
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

The daemon image version is the exact `cgprofile-v<version>` CMRU tag at
`HEAD`. The build script reads the prefix from `cmru.toml`, rejects ambiguous
or malformed matching tags, and supplies the resolved version to the OCI tag,
image label, CLI identity, and daemon self-description. This keeps build
metadata and live probes on one release fact rather than asking operators to
type a second version. Untagged local images are marked `0.0.0-dev`; publish
without an exact tag or explicit manual version refuses. No version lookup
depends on network access at runtime.

The local `cgprofile:local` alias is not a release coordinate: because it has
no registry hostname, Docker resolves it to Docker Hub when used as a publish
output. The Bake file therefore separates a local-only target from the
versioned GHCR release target, and CMRU's push step selects only the latter.
This keeps developer convenience from widening the release's external writes.
The pasteable CIU pin and verification command live in
[`CONSUMERS.md`](CONSUMERS.md#deploy-a-published-daemon-image-with-ciu).

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
