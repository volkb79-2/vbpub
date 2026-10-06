# cgroup-profiler consumers

The supported operator front door is the repository shell shim:

```bash
./cgprofile --version
# cgprofile 0.1.0
```

The probe prints one identity line and exits 0. In a source checkout it uses
`pyproject.toml`; a built image reports the CMRU release version embedded as
`CGPROFILE_VERSION` (for example, the combined RG-55 first release, `1.0.0`).
The shim routes `--version` directly to `cgprofile.py`, so this check does not
require the analysis/reporting venv. Help, usage, and configuration diagnostics at every subcommand depth
begin with the matching CGPROFILE headline as line 1; normal profiling output
is unchanged. Use
`ATTACH-GUIDE.md` for the complete gate integration recipe.

## Build or publish the daemon image

P1 and P6 were developed as separate work tranches, but RW-434 settles their
combined tree as cgprofile's first 1.0.0 release. After that tree is merged and
its required review and gates pass, preview and release it from the repository
root:

```bash
cmru status cgroup-profiler --config cmru.orchestration.toml --set-version 1.0.0
cmru release cgroup-profiler --config cmru.orchestration.toml --set-version 1.0.0
```

The first 1.0.0 release uses an explicit override because there is no prior
cgprofile tag. CMRU creates the release tag
before image build. `build-push.py` reads that exact tag to set the OCI
tag/label and embedded runtime version, so CMRU releases need no manual
`CGPROFILE_VERSION` export. An untagged local `--build` uses `0.0.0-dev`; a
manual `--push` needs an exact release tag or a validated `CGPROFILE_VERSION`
override.

The local `cgprofile:local` tag is a build-only alias. CMRU's publish step
selects the separate `cgprofile-release` Bake target, which contains only the
versioned `ghcr.io/volkb79-2/cgprofile:<version>` tag; it does not publish the
unqualified local alias to Docker Hub.

## One-shot collector behavior

If `cgprofile run` or `attach` requests DAMON while the host's `nr_kdamonds`
registry is nonempty, the collector refuses to resize it—even when its
existing monitors are stopped—because Linux rebuilds every kdamond entry on a
count write. The collector logs DAMON as unavailable, signals readiness, and
continues ordinary cgroup/CPU/memory sampling. A later DAMON collection or
cleanup failure also disables only DAMON and still writes the normal finished
manifest. `cgprofile run` still launches the wrapped command; a DAMON problem
must not change the command's exit status (R-36h). Confirm actual DAMON
collection from a persisted `damon.jsonl` series, not from `cgprofile doctor`
or a visible DAMON sysfs directory alone. cgprofile's independent one-shot
processes coordinate with the daemon using the host-shared
`/run/cgprofile/damon.lock`; an in-container helper bind-mounts only that file
at `/tmp/cgprofile-damon.lock`, not the control socket. If another cgprofile
writer holds the lock, the optional DAMON series is refused rather than
waiting or racing; ordinary profiling proceeds.

## Deploy a published daemon image with CIU

The default CIU image coordinates intentionally select the local development
image. To deploy the combined RG-55 v1.0.0 daemon release, put this complete
override in the tracked sparse `ciu.toml.j2` at the cgprofile CIU root:

```toml
[cgprofile.image]
registry = "ghcr.io"
namespace = "volkb79-2"
name = "cgprofile"
tag = "1.0.0"
```

The complete coordinates resolve to `ghcr.io/volkb79-2/cgprofile:1.0.0`;
changing only `tag` while leaving the default empty registry and namespace
would still select a local image. Use the normal CIU bring-up and verify the
runtime identity before attaching consumers:

```bash
ciu up --dir .
docker exec cgprofile-host-daemon cgprofile ctl version --json
```

The version in the response must match the pinned image. For local development,
leave the sparse override empty and use `python3 build-push.py --build`; that
path loads only `cgprofile:local` and does not publish it.

## Daemon adoption

This is the adoption guide: commands here are intended to be copied by an
operator or a run-gate integration. The daemon control contract is major
version 1; summaries returned by `ctl stop` use schema 1. For the rationale,
see [`DESIGN-GUIDE.md`](DESIGN-GUIDE.md#daemon-safety-and-placement).

## Start the host daemon

From this directory, build the image and start the one host-scoped daemon:

```bash
python3 build-push.py --build
ciu up --dir .
docker exec cgprofile-host-daemon cgprofile ctl version --json
```

The shipped stack supplies host observation with private PID/cgroup
namespaces: host `/proc` is bind-mounted read-only at `/hostproc`, host
cgroup v2 is mounted read-write at `/sys/fs/cgroup` for observation and
opt-in placement, and `CGPROFILE_PROC_ROOT=/hostproc` selects the host proc
view. The privileged daemon also mounts the host system bus at
`/run/dbus/system_bus_socket`; a read-only bind of that socket would not make
its RPCs read-only. The cgroup mount is writable at the mount boundary so the
daemon can perform placement, while D-25's whitelist is an application-level
guard for ordinary code paths, not containment against arbitrary code
execution in the daemon. It asks systemd to create a transient delegated
scope under the verified gates slice, registering the initial PIDs with that
scope, then uses `AttachProcessesToUnit` to move them into the cgprofile-owned
leaf. The scope path is read back from systemd rather than inferred from the
unit name. At stop the daemon restores only identity-verified survivors to
their recorded original units, removes the leaf, and stops the empty scope.
Both directions require the verified host-proc view and confirm resulting
membership. Successful moves are written to the session's `events.jsonl`; an
unresolvable or unrestored survivor leaves the scope, leaf, and recovery
record intact and appears as `placement.error` in the stop summary.
Do not set host namespace modes. On startup `serve` verifies that PID 1 in
that proc view
belongs to a PID namespace distinct from the daemon's and refuses if either
view is missing. Container targets arrive as full Docker IDs; token-scoped
sessions resolve direct PIDs in that target cgroup, match the exact token in
host `/proc`, then walk those owners' process trees.
The daemon has no Docker socket and does not accept `--cap`; it writes session
data under `/var/lib/cgprofile/sessions` and its own DAMON kdamonds under
sysfs during normal operation. Grant daemon control only to operators already
trusted with host-administrator Docker access. The privileged container's
mounts and Python checks do not confine a compromised daemon; see the
[trust boundary](DESIGN-GUIDE.md#daemon-safety-and-placement). Keep the host
system-bus mount daemon-side, never in the cockpit.

Before relying on `host.gates_slice` or requesting `start --place`, install
the host's authored `dev-gates.slice` unit. The daemon verifies the loaded,
non-transient unit and finite positive memory and CPU ceilings; an existing
cgroup directory without that evidence is reported as `present:false`, and
placement is refused while profiling continues.

For the socket carrier, send one complete newline-terminated JSON request no
larger than 1 MiB within 25 seconds of connecting. The deadline is absolute:
periodic trickle bytes do not reset it. The daemon closes an incomplete line
at the deadline or EOF without dispatch, and returns `bad-argument` for an
oversized line. On `start`, omit `args.damon` to use the daemon's configured
default; a present `args.damon: null` is invalid. After a
`watch` request is accepted, its streaming connection has no server timeout.

When running the one-shot helper from a cockpit, placement is checked before
the collector starts. The default verifier is the local `tester-unified:local`
image; it must contain `systemctl`. If you use another local image with that
client, set `CGPROFILE_PLACEMENT_PROBE_IMAGE` to its image name. The verifier
also needs the injected `CGROUP_PARENT_DEV_INTERACTIVE` value to match the
cockpit's actual Docker parent. Missing systemd or cgroup evidence is a hard
refusal, not permission to rely on Docker's default parent.

## Resolve a target from a cockpit

In helper mode, `self` means the invoking container. The caller resolves its
Docker ID before launching the private-namespace helper, which then finds the
container in the read-only host cgroup tree:

```bash
./cgprofile targets --mode helper --target self
./cgprofile targets --mode helper --target "pid:$$"
```

The helper never interprets its own namespace-local PID as a host PID. This is
important because host processes appear as PID `0` in `cgroup.procs` when read
from a private PID namespace; container identity must come from the ID lookup.
For `pid:N`, the caller verifies that the process shares its cgroup namespace
and carries the PID/cgroup namespace identities, namespace-local PID, process
start time, and relative cgroup path under the caller's container ID. The
helper resolves exactly one matching process in that container subpath and
uses its helper-visible PID for proc sampling and DAMON. Missing, changed, or
ambiguous identity is refused; pass an explicit container or cgroup target
instead.

The version response has the current wire shape:

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

The version response's `daemon.damon: "available"` only means the library
and admin sysfs interface are visible; it does not prove that the kernel will
accept a configured context. Check the `damon` field in each `ctl start`
response (`on` or `unavailable:<reason>`), then check the stop response's
top-level `series.damon` field for `damon.jsonl` before claiming DAMON samples
or overhead. See the
[design rationale](DESIGN-GUIDE.md#damon-availability-is-not-session-readiness).

The daemon reserves its configured DAMON session capacity with one count write
from an empty registry before starting the first kdamond. A nonempty registry
is refused regardless of whether its monitors are on or off: every count
write replaces all entries and can erase foreign staged configuration. The
daemon pins each owned state inode and quarantines any missing or replaced
slot rather than stopping, reusing, or shrinking across an object whose
identity no longer matches. cgprofile's shared advisory lock serializes its
own pool reservation/teardown against one-shot collectors. It cannot govern
unrelated privileged tools: the kernel exposes no atomic ownership lease for
this global sysfs registry, so do not run `damo` or another non-cooperating
privileged DAMON configurator concurrently. Ordinary profiling continues with
DAMON marked unavailable when reservation is refused. Treat `damon: "on"` plus a persisted
`damon.jsonl` series as the evidence that DAMON actually ran, not `ctl version`
alone.

## Attach a run-gate lane

Run-gate must start the lane first, inspect its immutable 64-hex container ID,
then ask the daemon to profile it. Generate a fresh token per lane attempt and
pass it through the lane as `RUN_GATE_PROFILE_SESSION`; the token makes a
retry-safe `(container_id, token)` start idempotent. The `meta` object is
required and its `kind` is either `command` or `assay`.

The following is a minimal shell-shaped integration. Replace the image,
command, and metadata values for the consuming project:

```bash
set -euo pipefail
: "${CGROUP_PARENT_DEV_GATES:?set the real host dev-gates slice}"

PROFILE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
LANE_NAME="rg55-example-lane"
docker run -d --name "$LANE_NAME" \
  --cgroup-parent="$CGROUP_PARENT_DEV_GATES" \
  -e RUN_GATE_PROFILE_SESSION="$PROFILE_TOKEN" \
  example-lane-image:local sleep 3600

CONTAINER_ID="$(docker inspect --format '{{.Id}}' "$LANE_NAME")"
META="$(python3 -c 'import json; print(json.dumps({
  "lane": "rg55-example-lane", "project": "example-project",
  "worktree": "/workspaces/example", "commit": None,
  "run_gate_revision": 1, "kind": "command", "expected": None}))')"

START_JSON="$(docker exec cgprofile-host-daemon cgprofile ctl start \
  --target "containerid:${CONTAINER_ID}" \
  --scope container --token "$PROFILE_TOKEN" --damon on --interval 1.0 \
  --idle-bound 30 --on-stall kill \
  --meta "$META" --json)"
SESSION_ID="$(python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["session"])' <<<"$START_JSON")"

docker exec "$LANE_NAME" ./run-lane.sh
LANE_STATUS=$?

STOP_JSON="$(docker exec cgprofile-host-daemon cgprofile ctl stop "$SESSION_ID" --json)"
printf '%s\n' "$STOP_JSON"
exit "$LANE_STATUS"
```

In production code, put the stop call in the lane's `finally`/cleanup path so
it runs when the lane fails. Read the daemon command's exit status directly:
`0` is a valid response, `2` is a daemon-declared contract error, and `3` is
unreachable or malformed-daemon state. Do not pipe the command through a
pager when deciding whether the stop succeeded.

For `ctl start`, an exit-2 `target-not-found` means the daemon searched a
readable host cgroup tree and found no scope for that full container ID.
An exit 3 can also mean the host cgroup bind became unreadable during lookup;
record profiling as unavailable and inspect the daemon's mount/logs. Do not
turn that indeterminate result into "container absent" or invent a cgroup
from Docker's name alone.

Use `--scope container-shared` when the target cgroup is shared with unrelated
work and the summary must report sampled-max memory plus deltas. Use
`--scope container` for a lane-owned cgroup; its `memory.peak` and absolute
counters have the schema-1 semantics documented in the contract.

### Resource accounting with placement

For a successfully placed shared lane, `target.cgroup` remains the logical
target identity, but sampled CPU, I/O, PID, pressure, and memory counters come
from the profiler-owned leaf under its delegated scope. Memory values are
cgroup charges, not total RSS: cgroup v2 does not transfer charges for pages
that were faulted before a process moved into the leaf. Therefore
`memory.current`, `memory.peak`, `memory.high`, and `memory.max` do not describe
all resident pages of every process in the lane, and the requested leaf
`memory.max` is not a hard cap on that pre-existing resident memory. Treat the
values as useful, directly measured charge accounting. If the requirement is
a whole-container limit and accounting boundary, run the lane in its own
container cgroup (`--scope container`) with its resource limits set before the
workload starts; do not reinterpret a shared-lane leaf counter as RSS.

Placement creates `rg-profile-<token>.scope` beneath the verified gates slice
and `rg-<token>` beneath that scope. The scope is systemd-owned; cgprofile owns
only the child leaf. The daemon preserves its recovery journal and leaves the
scope intact rather than killing or deleting processes it cannot safely
restore. The host system bus is mounted only into the daemon, not into the
cockpit. The complete ownership and security rationale, including the
deferred broker option, is in the
  [`RG-55 placement design`](../../../run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md#a3-placement-ownership-correction-delegated-scope-below-dev-gatesslice-2026-09-30).
For `--progress-stream`, provide a regular NDJSON file path inside the lane;
the daemon ignores a FIFO, device, or unfinished line. A placement request with an existing
`rg-<token>` leaf ordinarily starts unplaced with `placement.error` set. Use a fresh
token for each new lane attempt; a retry of a live session is reused by the
server's `(container_id, token)` lookup.
`--on-stall kill` for this `scope=container` example is limited to that exact
container's `cgroup.kill`, and is accepted only because the lane container is
launched beneath the verified gates slice. It does not change Docker's caps.
For `scope=container-shared`, `kill` additionally requires an explicit
successful `--place` leaf. Do not request placement solely to enable that
policy; when the lane's existing resource plan does not place it, request
`--on-stall report` (or omit the option) and keep the lane-local watchdog as
verdict authority. No path signals host PIDs. A `bad-policy` start refusal
means the requested enforcement boundary was not established; a runtime
`reported` verdict means a requested cgroup kill could not be confirmed.
When consuming `ctl watch`, require its terminal `end` line. The exec carrier
exits 3 if the socket closes before that line; treat it as profiling
unavailable and reconcile the session through `ctl status`/`stop`.

## Closed values and response evidence

Consumers may type these public values:

| field | values |
|---|---|
| `scope` | `container`, `container-shared` |
| start `damon` request | `on`, `off` |
| start response `damon` status | `on`, `off`, `unavailable:<reason>` |
| `meta.kind` | `command`, `assay` |
| response `contract` | `1` |
| summary `schema` | `1` |

Only a non-null token enables idempotent reuse. Omitting it deliberately starts
an independent session, so two no-token starts count separately against
`max_sessions`.

## Run the P1 review gates

For the current P1 acceptance campaign, the controller recorded the
pre-wave comparison commit below. From the attached candidate worktree,
pass that commit explicitly to both registered entrypoints:

```bash
P1_BASE=e5e9b95c5ac8be3452c93f1066f9436347f862fd
./run-gate.py --base "$P1_BASE" --dry-run r2
./run-gate.py --base "$P1_BASE" r2
./run-gate.py --base "$P1_BASE" gate
```

Run these from `scripts/cgroup-profiler/`. The dry run shows the expanded
`--request-base` argument without starting the judged lane. Before the long
R2 run, inspect Assay's plan for the exact commit/tree and the intended
source candidates. The `gate` lane passes the same base to its nested `r2`.
R2 and the full gate are release checks; their results belong to the exact
tree they judge. Run-gate can derive a different base when `--base` is
omitted, so an omitted flag is not the P1 acceptance command. The rationale
is in the [design guide](DESIGN-GUIDE.md#mutation-comparison-base).
