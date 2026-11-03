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

The first 1.0.0 release passes CMRU an explicit `--set-version 1.0.0` because
there is no prior cgprofile tag. CMRU passes the protected
`CMRU_RELEASE_COMMIT` and `CMRU_RELEASE_TAG` to its build and push steps.
`build-push.py` verifies that the checkout's `HEAD` and local tag both point at
that commit, then builds from a temporary worktree at the same commit. The OCI
tag, image label, and embedded runtime version all derive from that checked
identity, so CMRU releases need no manual `CGPROFILE_VERSION` export. An
untagged local `--build` uses `0.0.0-dev` and the checked checkout revision; a
`--push` without CMRU's protected identity refuses before registry login. The
pair binds the source to a commit and tag; it is not an authentication token
or proof of CMRU provenance. Use `cmru release` for publication and do not
hand-write the pair.

For a local source build, use the identity-resolving wrapper. It supplies both
the development version and the Git revision from one checked source state,
requires the cgprofile and sibling DAMON source contexts to be clean, builds
from a temporary worktree at that commit, and loads only the `cgprofile:local`
image. Commit or clean changes in either context before building. Run from
`scripts/cgroup-profiler`:

```bash
python3 build-push.py --build
```

The Bake file has literal development version and tag values, but no Git
revision fallback. A bare `docker buildx bake` refuses before package
installation because the Dockerfile requires both identity fields. Use the
wrapper for local builds; use `cmru release` for publication.

The local `cgprofile:local` tag is a build-only alias. CMRU's publish step
selects the separate `cgprofile-release` Bake target, which contains only the
versioned `ghcr.io/volkb79-2/cgprofile:<version>` tag; it does not publish the
unqualified local alias to Docker Hub.

The managed Compose file declares `create_host_path: false` for required host
binds. If `/run/cgprofile` is missing, CIU refuses a missing source rather
than creating an empty directory; host setup must also provision the observer
subdirectory before the contract-2 listener can start.

### Preview the GitHub Release body

A CMRU release also publishes the GitHub Release body for `cgprofile-v<version>`: the pull
instruction, then that version's dated section of `CHANGES.md` (pending `[Unreleased]` and
`### Migration` notes are folded into it in the release candidate). Preview the exact text
before releasing, from the repository root:

```bash
cmru release cgroup-profiler --config cmru.orchestration.toml --set-version 1.0.0 --dry-run
```

The preview prints the complete body between `----- begin release body -----` and
`----- end release body -----`, beginning with:

```text
**Image:** `ghcr.io/volkb79-2/cgprofile:1.0.0`

Pull: `docker pull ghcr.io/volkb79-2/cgprofile:1.0.0`

## [1.0.0] - <date>
```

It uses the same builder as the release, leaves `CHANGES.md` unchanged, and has no artifact
name or SHA256 because CGProfile uploads no asset. The body is written by the last command of
`[steps.push]` in `cmru.toml`, which must stay after the image push:

```text
build-push.py --push                      # pushes ghcr.io/volkb79-2/cgprofile:<version>
cmru handler oci-image-release --prefix cgprofile --image ghcr.io/volkb79-2/cgprofile --cwd .
```

Do not add a command after it or reorder them: CMRU refuses a release (and the dry run) before
any prepare step, gate, tag or image push when the handler is malformed, not last, names a
prefix other than `cgprofile`, or gives an image without an explicit registry. Never run the
handler by hand; it needs the protected release identity that only `cmru release` supplies.
If a release reports that the GitHub Release "was written" but could not be verified, the
image is already published: inspect the `cgprofile-v<version>` release and its tag before
retrying (see the [design note](DESIGN-GUIDE.md#the-github-release-body-for-the-published-image)).

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

Run this after `cmru release cgroup-profiler --set-version 1.0.0` has
completed and the GHCR manifest is available. Before that first publication,
leave the tracked sparse `ciu.toml.j2` without an image table; CIU then uses
the local `cgprofile:local` development default for pre-release checks.
After publication, commit this sparse override to pin the exact released
image:

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
runtime identity before attaching consumers. Run this from
`scripts/cgroup-profiler/`:

```bash
ciu up --profile host
docker exec cgprofile-host-daemon cgprofile ctl version --json
release_commit=$(git rev-parse --verify 'refs/tags/cgprofile-v1.0.0^{commit}')
daemon_image_id=$(docker inspect cgprofile-host-daemon --format '{{.Image}}')
image_revision=$(docker image inspect "$daemon_image_id" \
  --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')
test "$image_revision" = "$release_commit"
```

The version in the response must match the pin, and the running image's source
revision label must match the peeled release tag. For a candidate deployment,
compare the label with that candidate's commit instead. For local development
after the release pin has been committed, temporarily clear the
`[cgprofile.image]` table in `ciu.toml.j2`. From `scripts/cgroup-profiler/`,
build the local image before starting CIU; that path loads only
`cgprofile:local` and does not publish it. Restore the published-image table
before committing a deployment change:

```bash
python3 build-push.py --build
ciu up --dir .
```

## Daemon adoption

This is the adoption guide: commands here are intended to be copied by an
operator or a run-gate integration. The daemon control contract is major
version 1; summaries returned by `ctl stop` use schema 1. For the rationale,
see [`DESIGN-GUIDE.md`](DESIGN-GUIDE.md#daemon-safety-and-placement).

## Start the host daemon

This recipe starts the local development image. Use it only while
`ciu.toml.j2` has no `[cgprofile.image]` release override; if that pin is
committed, `ciu up` selects the pinned GHCR image instead. Clear the table as
described above before starting a local development image. From this
directory, build and start the one host-scoped daemon:

```bash
python3 build-push.py --build
ciu up --profile host
docker exec cgprofile-host-daemon cgprofile ctl version --json
```

Use `ciu down --profile host` to stop this singleton before a restart. The
named profile selects the project-root stack for both lifecycle verbs, so
operators do not need a raw Docker restart or a destructive `--reset`.

The shipped daemon alone uses the host PID namespace so DAMON sysfs can
resolve host `pid_target` values. It keeps a private cgroup namespace and
network disabled; workload, helper, gate, and test containers remain private.
The Compose service sets `pid: "host"` and
`CGPROFILE_PID_NAMESPACE_MODE=host`. Host `/proc` is still bind-mounted
read-only at `/hostproc`, selected by `CGPROFILE_PROC_ROOT=/hostproc`; host
cgroup v2 is mounted read-write at `/sys/fs/cgroup` for observation and
opt-in placement. The privileged daemon also mounts the host system bus at
`/run/dbus/system_bus_socket`; a read-only bind of that socket would not make
its RPCs read-only.

Host PID mode makes host PIDs addressable to DAMON, but does not guarantee the
kernel will accept every DAMON context. A prior probe on this host, using
host PID and host cgroup namespaces together, still returned `EINVAL` at
`kdamond_commit`; the current host-PID/private-cgroup configuration needs its
own live start/stop probe. Unsupported DAMON remains `unavailable:<reason>`
and must not fail or alter the workload verdict.
The cgroup mount is writable at the mount boundary so the
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
If that happens, keep the sessions volume and inspect the returned
`session_dir`'s `placement-state.json`; a daemon restart retries the recorded
restoration. `ctl gc` preserves an incomplete journal, including when the
normal count or age limit has been reached. Do not delete that session record
to make the retention count look clean.

Placement can also be refused before moving any PID when moving the selected
set would empty its original systemd unit. The session may still start for
ordinary observation with `placement.leaf: null` and
`placement.error: "place-refused:origin-scope-would-empty"`; a one-process
container scope is the common example. Unknown source membership, or a selected
live process that is no longer in its recorded source unit, is refused as
`place-refused:identity-unavailable`. Do not rely on an unrelated process
as a permanent source-unit lease: it can exit between the daemon's check and
the systemd move. If exact restoration cannot be proved, retain the session
directory and recovery journal; the daemon will not fall back to a parent
cgroup.

On startup `serve` requires the daemon-only host-PID mode and verifies that
PID 1 in the configured proc view belongs to the daemon's PID namespace; a
missing or mismatched view is refused. This is a consistency check, not an
independent proof that the procfs bind is the host's: the managed CIU Compose
service supplies the deployment authority by selecting `pid: "host"` and
binding host `/proc` at `/hostproc`. `private` remains the default mode for the
one-shot helper and normal callers; do not enable `host` mode for helpers,
workloads, gates, or tests. The host-PID mode is a deployment choice in this
daemon's Compose service, not a new host-setup unit or cockpit bind mount, so
it does not require rebuilding the devcontainer. Container targets arrive as
full Docker IDs; token-scoped
sessions resolve direct PIDs in that target cgroup, match the exact token in
host `/proc`, then walk those owners' process trees.
Host-PID mode exposes host process identities and adds process-table/PID
operation authority. The privileged daemon's validation and write guards are
not a containment boundary against arbitrary code execution. The daemon has
no Docker socket and does not accept `--cap`; it writes session
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

## Native R2 resource observer

Assay B145 refuses to classify native R2 candidates when private cgroup
namespaces hide an enforcing ancestor. With a cgprofile daemon that implements
resource-observer contract 2 and a Run-Gate build that supports RG-89, opt the lane in.
The host cgroup2 mount must not use `pids_localevents`; cgprofile refuses that
mode because its fork-failure counter identifies the cgroup where a failed fork
was attempted, not the ancestor whose `pids.max` blocked it. A refusal means the
lane has no trustworthy pids-event attribution on that host. `memory_localevents`
is supported and appears in the observer scope metadata; candidate-local memory
events require the candidate cgroup to have no child cgroups.

```toml
[lanes.assay-r2]
kind = "assay"
assay_lane = "r2"
environment = "tester-unified"
resource_observer = "cgprofile"

[lanes.assay-r2.resources]
memory = "2g"
pids = 8192
```

Both ceilings are required: Run-Gate refuses to load an observer-backed lane
without an explicit `resources.memory` and `resources.pids`, because candidate
memory and PID limit events are only attributable against a declared ceiling.

For native R2 in a tester container shared by Assay workers, serialize
candidate windows in that lane's `assay.toml`:

```toml
[lanes.r2.judge.mutation]
jobs = 1
```

The observer samples the tester cgroup, so overlapping workers could count
one worker's limit event against another mutant. A 24-hour budget is a
resumable safety window; rerun the exact `r2` command in
[Run the P1 review gates](#run-the-p1-review-gates) from the same committed
tree after an incomplete window. See the
[resource-observation rationale](DESIGN-GUIDE.md#native-r2-resource-observation-is-data-only-not-a-broker).
If the observer refuses because the peer threads have different capability
states, resolve that peer configuration; the counter query will not guess
which thread state applies.

Host setup provisions `/run/cgprofile/observer` as root-owned mode `0751` in
the dedicated `cgprofile-observer` group. The daemon owns only the socket entry
inside this persistent directory. Run-Gate validates the directory and socket,
then bind-mounts the directory read-only into `tester-unified`. The directory
bind lets an already-running container see a new socket after daemon restart;
binding the socket file would pin the old inode. Run-Gate grants only the
dedicated observer GID and sets `ASSAY_RESOURCE_OBSERVER_SOCKET` inside the
container. Observer-backed lanes also declare per-container memory and PID
ceilings and refuse arbitrary extra mounts. A missing daemon/socket or unsafe
ownership/mode is an infrastructure refusal, never a silent fallback. The
selected container retains private PID, cgroup, and network namespaces; do not
mount `ctl.sock` or the Docker socket for this feature.
The observer accepts one newline-terminated JSON request of at most 4096 bytes
including the newline and applies a five-second absolute deadline. Consumers
must send one complete line; oversized or incomplete requests are not
dispatched.

Assay uses its local B145 reader first and contacts the observer only when the
local view specifically proves that ancestors are hidden. The daemon derives
the peer's cgroup from `SO_PEERCRED` plus the supplied native TID, limits
queries to the configured gates slice, and returns only that peer's ancestry
counters. The observer requires contract 2. A contract-1 request receives an
explicit unsupported-contract refusal, so deploy cgroup-profiler 1.0.0 or
later before enabling this lane. The daemon's separate control socket remains
contract 1.
Any incomplete identity
or ambiguous shared-ancestor local event remains `ERROR/EXEC_FAILED`, not a
candidate kill/survivor. A hierarchical-only change from a sibling does not
invalidate the candidate. See the
[`observer protocol`](PROTOCOL.md#8-native-r2-resource-observer-rg-89) and
[`design rationale`](DESIGN-GUIDE.md#native-r2-resource-observation-is-data-only-not-a-broker).

### Live observer acceptance

Unit tests prove the contract with fake trees; a release additionally needs one
bounded run against the real daemon from a gated peer. `tools/observer-live-acceptance.py`
(stdlib only, read-only, creates no cgroup) is the probe. Run it inside an
opted-in gated container that has `/run/cgprofile/observer` mounted:

```sh
# 1. contract 1 is refused with bad-argument naming the contract
python tools/observer-live-acceptance.py contract1
# 2. baseline reading (well-formed contract 2, local maps present)
python tools/observer-live-acceptance.py read --out /tmp/before.json
# 3. cause a SIBLING-only event: a second gated container under the same
#    gates slice hits ITS OWN pids.max or memory.max (bounded, e.g. a 16-fork
#    burst under --pids-limit 8); the peer above stays untouched
python tools/observer-live-acceptance.py read --out /tmp/after-sibling.json
python tools/observer-live-acceptance.py compare --before /tmp/before.json \
    --after /tmp/after-sibling.json --expect sibling-only
# 4. cause an event ENFORCED AT THE SHARED ANCESTOR (the gates slice's own
#    limit is reached by the peers together), then
python tools/observer-live-acceptance.py read --out /tmp/after-enforced.json
python tools/observer-live-acceptance.py compare --before /tmp/after-sibling.json \
    --after /tmp/after-enforced.json --expect enforced
```

`sibling-only` passes only when a shared ancestor's hierarchical counter grew
while its local counter did not; `enforced` only when the ancestor's local
counter grew. Record the daemon image revision label (it must equal the
peeled release tag) next to the four outputs. The probe never substitutes for
Assay's own fail-closed attribution; it is evidence that the live daemon
reports the distinction at all. Until this has been run against the released
daemon, CP-20 stays `carved`.

## Measurement windows (observer contract 3)

cgprofile 1.2.0 and later also answer `window-open` and `window-close` on the
same observer socket (contract 3; contract 2 and `resource-limits` are
unchanged). A window returns one subject's resource numbers: peak RSS, CPU, I/O
bytes and operations, memory and swap peaks and PSI stall time. The daemon is
the single source of these numbers. A consumer that obtains none records an
explicit `NO_MEASUREMENT` with one reason code and never reads procfs or
cgroupfs to fill the gap. The wire is in
[the observer protocol](PROTOCOL.md#9-observer-contract-3-measurement-windows).

Choose the mode by what the subject shares:

- **`process-tree`** works in a shared container (a pytest worker, an Assay
  candidate at jobs above 1). RSS and I/O bytes are sampled lower bounds and CPU
  is exact when the window is closed after `waitid(WEXITED|WNOWAIT)` and before
  the root is reaped. IOPS, PSI and cgroup peaks are `REQUIRES_CGROUP_MODE`.
- **`cgroup`** is exact but attributable only while nothing else runs in the
  subject's leaf: use it at jobs=1 in a tester container. A foreign process seen
  during the window turns every cgroup-sourced number into `SHARED_CGROUP`.
  Per-file `memory.peak` reset needs Linux 6.12 or later, otherwise the two peak
  metrics are `PEAK_RESET_UNSUPPORTED`.

What a consumer does when no window is available:

| Situation | Record |
|---|---|
| Socket path absent or not mounted | `NO_MEASUREMENT` / `OBSERVER_UNAVAILABLE` |
| `version.resource_observer.contracts` lacks 3, or the verbs get `bad-argument` | `NO_MEASUREMENT` / `OBSERVER_CONTRACT_UNSUPPORTED`; ask once per run |
| `peer-refused` or `observer-refused` | `NO_MEASUREMENT` / `OBSERVER_REFUSED` |
| `window-refused`, `daemon-error`, timeout or a malformed reply | `NO_MEASUREMENT` / `OBSERVER_ERROR`; after 3 consecutive failures stop asking |
| `window-lost` on close (daemon restart, expiry) | `NO_MEASUREMENT` / `WINDOW_LOST` |

A missing measurement never changes a verdict, a bucket or an exit code. A
non-null metric with a `missing` entry elsewhere in the same reply makes the
profile `PARTIAL`, not `NO_MEASUREMENT`.

Windows are not persisted and are released when the daemon stops. Close every
window; an unclosed one counts against the per-process limit of 64 until it
expires after 6 hours or its process exits.

The Summary of a lane session also gains an optional
`ancestors_memory_events_local` key: the increase in the local `high`, `max`,
`oom` and `oom_kill` counters of each ancestor up to `dev.slice`. A positive
`high` or `max` there means reclaim was driven from above the lane, which a
consumer classifies as contamination rather than as the lane's own limit. The
same list is in a window's `context`. Host-wide `/proc/pressure` figures are
context only: on a shared host they are high for reasons unrelated to the lane.

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
  },
  "resource_observer": {
    "contract": 2,
    "contracts": [2, 3],
    "path": "/run/cgprofile/observer/resource-observer.sock",
    "listening": true
  }
}
```

Check `resource_observer.contract` and `listening` before running an opted-in
native R2 gate. The field describes the separate observer endpoint; it does
not change the control protocol's `contract: 1`.

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

`stop` answers `stop-incomplete` while a session's sampling worker is still
running and the session keeps its resources; retry. After the third
consecutive incomplete attempt the daemon finalizes anyway and the Summary
carries `stop_forced: "sampler-still-running"` (manifest `aborted`), which is
the operator recovery for a wedged worker; a daemon restart recovers a session
left `running` as an orphan.

`serve` refuses to run anywhere but inside a container whose cgroup is under a
bounded top-level `cgprofile.slice` (see `infra/README.md`); a non-container or
development `serve` run is refused too. Pre-creation proof is CIU-150.

**When `ctl` cannot reach the daemon** (CP-31), the `could not reach the daemon`
line on stderr (exit 3) ends in a `[cause: ...]` that names which of three
situations applies, using only what the calling process can see:

- `socket-not-mounted`: the socket's directory does not exist in this
  environment (a nested or sandboxed lane without the `/run/cgprofile` bind).
- `socket-missing`: the directory exists but the socket node is gone (the
  daemon container is not running, or the node was removed).
- `daemon-refusing`: the node exists and nothing listens (the daemon exited or is
  restarting; a daemon that refuses to start, for example on the
  `cgprofile.slice` guard above, restarts forever and leaves exactly this).
  `socket-permission` is the fourth, separate case: the node exists but this
  user may not connect.

A running daemon also checks every few seconds that its socket path still holds
its own listener. It rebinds when the node was removed or replaced, and exits 3
(so `restart: unless-stopped` recreates it) when it cannot. At start it never
deletes a non-socket node and never replaces a socket another daemon still
answers on: `serve` exits 2 and names the path (CP-34).

**Concurrency and `busy`** (CP-33). Every connection is served on its own
thread, so a client that sends its request line slowly, or a long verb (`report`,
a `stop` against a wedged worker), no longer delays other verbs. Admission is
bounded: 32 connections reading a request line, 16 verbs running, 32 open
`watch` streams, and a separate pool of 2 running `report` verbs (a report is
a heavy subprocess, so it cannot starve `status` or `stop`). A request over a
bound is answered
`{"ok": false, "error": {"code": "busy", ...}}`; the code is additive (contract
stays 1) and the right reaction is to retry shortly. `version`, `host`,
`status` and `gc` are read-only snapshots; `start`, `stop` and `report` are
guarded by the registry and session locks.

**Stopping the daemon.** On SIGTERM the daemon waits up to 4 s for running
verbs, up to 4 s for the sampling workers, then finalizes the live sessions
(forced, with a 1 s session-lock wait), so a normal stop ends in about 9 s.
Live sessions are finalized as `aborted: "daemon-stopped"`; a daemon that exits
3 after losing its control socket aborts them the same way. Docker's default
stop grace is 10 s, too close for a wedged session, so run the daemon with
`stop_grace_period: 30s` (the ciu project sets it) or `docker stop --time 30`.
A session that could not be finalized in time is recovered as an orphan at the
next start.

A stopped session whose DAMON owner was closed carries
`summary.damon_cleanup: {"confirmed": <bool>, "slot": <int|null>,
"quarantined": <bool>}` (also in `manifest.json`). `confirmed: false` means the
kdamond teardown could not be verified and it may still be running; do not read
that as "DAMON off". `quarantined: true` means the daemon will not reuse the
slot. The workload verdict is unaffected, and `stop` still answers `ok: true`.

### Host DAMON_STAT exclusivity

`DAMON_STAT` and the generic DAMON sysfs interface serve different consumers.
`DAMON_STAT` is a special-purpose kernel module that monitors the whole
physical-memory space and exposes simplified system-wide idle-time percentiles
and estimated memory bandwidth, with auto-tuned monitoring intervals. It does
not select the PIDs belonging to a particular lane. The sysfs interface is a
privileged, programmable control ABI: user space configures kdamonds,
contexts, operations, and targets. cgprofile uses it for per-session virtual
address (`vaddr`) targets bound to the profiled process PIDs, with a
statistics-only scheme, so its output can be attributed to a lane. These are
not two ways to read the same result; Linux makes DAMON special-purpose
consumers exclusive, and starting the sysfs monitor while `DAMON_STAT` is
active can return `EBUSY`. See the kernel's [DAMON sysfs usage
guide](https://docs.kernel.org/7.1/admin-guide/mm/damon/usage.html) for the
programmable interface and its [DAMON_STAT
guide](https://docs.kernel.org/7.1/admin-guide/mm/damon/stat.html) for the
global summary interface.

The DAMON sysfs registry is not a complete inventory of kernel DAMON users.
In particular, Linux's special-purpose `DAMON_STAT` module can be enabled
while `nr_kdamonds` is `0`; the kernel documents special-purpose DAMON
modules as exclusive, so another start request can fail with `EBUSY`. This
does not make the cgprofile daemon's control socket or ordinary resource
sampling unusable. `ctl version`'s `damon: available` is only a capability
probe; it does not mean the kernel accepted a session. The start response may
record DAMON as `unavailable:OSError: [Errno 16] ...`, and the session should
continue without DAMON under R-36h. Confirm the actual `ctl start` result and
persisted `damon.jsonl` series before claiming DAMON samples or overhead. See
the [kernel DAMON design](https://www.kernel.org/doc/html/v7.1/mm/damon/design.html)
and [DAMON_STAT documentation](https://docs.kernel.org/7.1/admin-guide/mm/damon/stat.html).

Check the host-visible state through the daemon's existing read-only sysfs
bind, without changing it:

```bash
docker exec cgprofile-host-daemon sh -c '
  if [ -r /sys/module/damon_stat/parameters/enabled ]; then
    printf "damon_stat.enabled="
    cat /sys/module/damon_stat/parameters/enabled
  else
    echo "damon_stat.enabled=not-exposed"
  fi
  printf "nr_kdamonds="
  cat /sys/kernel/mm/damon/admin/kdamonds/nr_kdamonds
'
```

On the RG-55 host the observed values were `Y` and `0`, respectively, while
the control socket answered `version`, `status`, and `host`, and a live
session's ordinary samples were collected. The `Y` plus a reproduced `EBUSY`
is consistent with DAMON_STAT exclusivity, not a failed daemon launch. Do not
disable a host-wide monitor as an automatic diagnostic or from cgprofile;
coordinate any temporary change with the host operator, then verify an actual
session start and persisted `damon.jsonl` series before claiming DAMON data.

The running RG-55 host uses Debian 13's `7.1.13+deb13-amd64` kernel. Its
`/boot/config-7.1.13+deb13-amd64` reports
`CONFIG_DAMON_STAT_ENABLED_DEFAULT=y`, and the live module parameter was `Y`.
This describes that installed kernel build, not a universal Debian default;
the parameter's build-time default is controlled by
`CONFIG_DAMON_STAT_ENABLED_DEFAULT`. In an operator-approved 2026-10-09
acceptance probe, `DAMON_STAT` was temporarily set to `N`: a real session
started with `damon: "on"`, collected 36 DAMON records for one synthetic
64-MiB process, and stopped cleanly. The host setting was restored to `Y`,
`nr_kdamonds` was `0`, and the daemon remained up with no sessions. This
confirms the exclusivity diagnosis; it does not authorize cgprofile or an
automated gate to change the host-wide setting. To make DAMON available
persistently, the host operator must choose a kernel policy that does not run
`DAMON_STAT` concurrently, then verify the session's status and persisted
series. Ordinary resource profiling remains usable either way under R-36h.

## Attach a run-gate lane

Run-gate must start the lane first, inspect its immutable 64-hex container ID,
then ask the daemon to profile it. Generate a fresh token per lane attempt and
pass it through the lane as `RUN_GATE_PROFILE_SESSION`; the token makes a
retry-safe `(container_id, token)` start idempotent. The `meta` object is
required and its `kind` is either `command` or `assay`.

Use the installed Run-Gate wheel, not a copied project-local `run-gate.py`
script. Check the installed SemVer with `run-gate --version` before adopting a
lane; see [Run-Gate's wheel contract](../../../run-gate-project/CONSUMERS.md#distribution-installed-wheel-and-semver).

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
P1_BASE=eb3af770eb16362cff4c39388193c0180c2d370c
run-gate --base "$P1_BASE" --dry-run r2
run-gate --base "$P1_BASE" r2
run-gate --base "$P1_BASE" gate
```

Run these from `scripts/cgroup-profiler/`. The dry run shows the expanded
`--request-base` argument without starting the judged lane. Before the long
R2 run, inspect Assay's plan for the exact commit/tree and the intended
source candidates. The `gate` lane passes the same base to its nested `r2`.
The composite is the complete Run-Gate lane: `r0-r1`, the 24-hour `r2`, then
`r3`, and its result belongs to the exact tree it judges. CMRU's release gate
(`cmru.toml` `steps.run-tests`) is narrower: it runs `run-gate r0-r1` and
`run-gate r3` only and never runs R2. The operator's release policy allows the
long full-source R2 campaign to be deferred, so **the initial 1.0.0 release
has no completed full-source R2 verdict**; a release does not claim one. Run
`run-gate --base "$P1_BASE" gate` when that evidence is wanted; see
[release qualification](DESIGN-GUIDE.md#release-qualification-and-the-full-r2-gate).
Run-gate can derive a different base when `--base` is omitted, so an omitted
flag is not the P1 acceptance command. The rationale is in the
[design guide](DESIGN-GUIDE.md#mutation-comparison-base).

## cgprofile 2.0: consumer impact and docs sweep

Specification for cgprofile 2.0.0 (the contract is [CGPCTL-SPEC.md](CGPCTL-SPEC.md), the wire is
[PROTOCOL.md](PROTOCOL.md) section 10, packaging is [NATIVE-DEPLOYMENT.md](NATIVE-DEPLOYMENT.md)). Everything
above this section describes 1.x and stays accurate for a 1.x daemon. 2.0.0 is one breaking release: the
`cgprofile` command disappears in favour of `cgpctl` (client) and `cgpd` (daemon), the daemon becomes a native
systemd service installed by mdt host-setup, and the control socket moves to ctl contract 2. There is no alias
and no 1.3.0.

### Consumer impact

| Consumer | Impact | Works against 1.x? | Owner |
|---|---|---|---|
| run-gate | Drops the `docker exec cgprofile-host-daemon cgprofile ctl ...` fallback carrier, the `exec` / `auto` transport values and the `RUN_GATE_PROFILE_TRANSPORT` setting, and goes socket-only on `/run/cgprofile/ctl.sock`. It stops using the daemon's `report` op: it calls `cgpctl report` client-side or skips the report. It keeps speaking contract 1, which a 2.x daemon serves unchanged for `version`, `host`, `status`, `start`, `stop`, `watch` and `gc` | Yes. The change needs only the socket carrier, which every 1.x daemon already has, so it can ship in any run-gate release before the daemon migrates | run-gate package (plan package 4, `rg-cgp-socket-20261104`) |
| assay | None. It uses only the observer socket (contracts 2 and 3), whose paths, framing and answers do not change | Yes, unchanged | none |
| mdt-cockpit image | Gains the `cgprofile` wheel (core only, no `report` extra) in `pip/wheels.list` and the first-party sets, and `cgpctl` in the skills table, so the finalize step runs `cgpctl skills check` and `install` | Not applicable (image content) | MDT-048 |
| mdt host-setup | Installs and pins the native service from a released wheel, renders `/etc/cgprofile/cgpd.toml` and the units, drains before restart, and runs `cgpctl doctor --json` in `check.sh`. Tmpfiles, sysusers and the socket paths are unchanged | Not applicable | MDT-055 |
| Devcontainers and runners that bind `/run/cgprofile` | No change to the mount. They gain the ability to run `cgpctl --socket PATH` (or `CGPCTL_SOCKET`) from the image | Yes | none |
| Operators and agents | Replace `docker exec cgprofile-host-daemon cgprofile ctl <verb>` with `cgpctl <verb>`; `attach`, `targets` and helper mode are gone (use `cgpctl start`, `start --dry-run`, `run`) | Mixed: contract-1 verbs work against a 1.x daemon, `report` and `config show` need 2.x | each user |

What a 1.x consumer sees from a 2.x daemon: `version` answers as before with extra fields; a contract-1
`report` request is answered with the error code `report-removed` and a message naming `cgpctl report`;
ownership rules apply to `stop` and `gc` (run-gate starts and stops as one uid, so it is unaffected).

Access summary for everyone: the ctl group opens `ctl.sock`; callers outside it keep `--version`, `run`, `mark`,
local report rendering and a `doctor` that names the fix; stopping or exporting another uid's session is
root-only. The ctl group is root-equivalent on mdt hosts, so ownership is hygiene and not a security boundary.

### DOCS SWEEP checklist

Produced with `git grep` at `3d7e34cc6`; re-run before the sweep because the lines will have moved:

```bash
git grep -nE "cgprofile ctl|docker exec cgprofile-host-daemon|cgprofile-host-daemon cgprofile"
git grep -lE "cgprofile (run|attach|mark|report|targets|doctor|serve)\b|\./cgprofile |CGPROFILE_(HELPER_IMAGE|PLACEMENT_PROBE_IMAGE|PID_NAMESPACE_MODE|ALLOW_UIDS)"
```

Package 0 edits none of these outside cgprofile. The sweep itself is backlog CP-49 (plan package 5); rows
outside cgprofile land with the owning tool's package or in one docs commit per repo area. Historical files are
not edited: dated `nyxloom-trove/reports/*`, run logs, `CHANGES.md` entries, fixtures that freeze 1.x bytes.

| Area | Files (hits of the first grep in parentheses) | Action | Owner |
|---|---|---|---|
| cgprofile docs | `README.md` (6), `ATTACH-GUIDE.md` (1), `DESIGN.md` (1), `docs/DESIGN-GUIDE.md` (1), `docs/CONSUMERS.md` (5), `docs/PROTOCOL.md` (1) | Rewrite for `cgpctl` and contract 2; delete the exec-carrier text; CP-45 overview and docs-sync test | CP-49 |
| cgprofile code and packaging | `cgprofile`, `cgprofile.py` (1), `lib/serve.py`, `setup.sh`, `Dockerfile`, `docker-bake.hcl`, `ciu.compose.yml.j2`, `ciu.defaults.toml.j2`, `ciu.global.defaults.toml.j2`, `.claude/skills/cgprofile/SKILL.md` | Replaced by the package and wheel (CP-46, CP-47, CP-48); ciu files deleted by CP-44 | CP-46 to CP-48, CP-44 |
| cgprofile mirrored contract | `docs/RG55-INTERFACE-CONTRACT.md` (11), mirrored byte for byte from `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` | Edit the canonical file first, then copy; `test_docs.py` enforces equality | run-gate package |
| run-gate | `run_gate.py` (19), `README.md` (1), `CONSUMERS.md`, `SPEC.md`, `KNOWN_ISSUES_TODO_BACKLOG.md` (3), `tests/test_run_gate.py` (174 cgprofile hits), `tests/fixtures/rg55/*` and `nyxloom-trove/fixtures/rg55/*` (frozen 1.x bytes: keep), `tests/fixtures/rg55-p5-placement-live/*` | Remove the exec carrier, the transport setting and the `report` op; refuse the removed env var by name | run-gate package |
| mdt-cockpit | `host-setup/README.md`, `host-setup/CGROUP-NOTES.md`, `host-setup/install.sh`, `host-setup/scripts/check.sh`, `host-setup/units/mdt-cgprofile*.{conf,sysusers}`, `pip/wheels.list`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `docs/DEVCONTAINER-MECHANISMS.md`, `templates/devcontainer.json` and `templates/host-folder/devcontainer.json` (comments), `KNOWN_ISSUES_TODO_BACKLOG.md` | Native install, group, wheel set, skills entry; remove "out of scope" and ciu-stack statements | MDT-048, MDT-055 |
| mdt legacy mirror | `modern-debian-tools-python-debug/host-setup/{README.md,CGROUP-NOTES.md,install.sh}`, `templates/devcontainer.json`, `docs/*` | Same text as mdt-cockpit while that tree is kept | MDT-055 |
| Workspace | `.devcontainer/devcontainer.json` (comments naming the host daemon) | Comment update only | CP-49 |
| Estate docs | `docs/design/RESOURCE-PROFILES.md`, `docs/BOOTSTRAP-FROM-ZERO.md`, `scripts/LANE-MATRIX-PROGRESS.md` | Rename commands; bootstrap sequence installs the wheel instead of building the image | CP-49 |
| debian-install-v2 | `testing/vm/USAGE-TESTING.md` (3), `docs/DESIGN-GUIDE.md` | `docker exec ... ctl` becomes `cgpctl`; netcup RUNBOOK and backlog entries named in the plan are in the netcup tree (grep there) | CP-49 |
| cmru | `run-gate.toml` lines 66 and 219 (`resource_observer = "cgprofile"`, a lane setting: keep), the bootstrap-sequence names in `docs/BOOTSTRAP-FROM-ZERO.md` and its test `test_bootstrap_from_zero_sequence.py` | Update the names in the bootstrap sequence only | cmru |
| ciu | `docs/SPEC-V8.md` (1), `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` (1) | Replace the command in the two examples | ciu docs |
| Historical (leave) | `assay/nyxloom-trove/reports/*`, `run-gate-project/nyxloom-trove/reports/*`, `run-gate-project/nyxloom-trove/{DESIGN,WAVE-PLAN}-*`, `run-gate-project/CHANGES.md`, `scripts/cgroup-profiler/nyxloom-trove/reports/*`, backlog entries CP-1, CP-2, CP-28, CP-31 | Dated records; a successor note at most | none |

Repositories outside this checkout (for example dstdns and its netcup runbook) are not covered by this grep and
need their own pass when they adopt 2.0.
