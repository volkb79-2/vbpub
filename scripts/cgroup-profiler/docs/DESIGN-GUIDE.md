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
systemd-owned gates slice or origin cgroup is used. The daemon's host cgroup-v2
bind is therefore writable: Linux cgroupfs placement cannot work through a
read-only bind. This is a deliberate, bounded application-level exception to
observation-only operation, not a kernel sandbox or general host-control
surface.

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
[`RG-55 placement design`](../../../run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md#a3--placement-ownership-correction-delegated-scope-below-dev-gatesslice-2026-09-30).

The daemon's host system bus is separate from the consumer control socket. The
cockpit receives only the cgprofile ctl surface; it does not need the system
bus. The deployed design deliberately has no broker: cgprofile calls systemd
directly, and the daemon's path checks, PID validation, and RPC allowlist
constrain normal behavior but do not contain arbitrary code execution in the
daemon. A read-only bind of a D-Bus socket is not read-only RPC authority.
Moving these calls into a broker would improve isolation only if the broker
has a genuinely narrower OS authority, a small authenticated request surface,
and its own tested identity/path validation. A broker that can perform the
same host-root operations and trusts the daemon's claims may merely move the
compromise point. The detailed no-broker decision and future broker tradeoffs
are recorded in the
[`RG-55 placement design`](../../../run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md#a3--placement-ownership-correction-delegated-scope-below-dev-gatesslice-2026-09-30).

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
fails part-way through setup.

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

## Contract boundary

The wire contract is major version 1 and summary documents use schema 1. A
reachable Unix socket is not enough: `ctl` validates that the response is an
object with a boolean `ok`, contract major 1, and the shape for the requested
verb before printing it. Valid daemon errors remain exit 2; malformed or
incompatible peer responses are daemon faults (exit 3).

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

## Decisions intentionally left outside this repair

This repair does not invent policy for the S1–S5 follow-up surfaces. Those
decisions remain with the controller and their existing design records.
