# cgroup-profiler consumers

The supported operator front door is the `cgpctl` console script of the wheel:

```bash
cgpctl --version
# cgpctl <installed-version>
```

The probe prints one identity line and exits 0; it needs neither the daemon nor
the `report` extra. The version is the installed wheel's (an untagged build is
`0.0.0-dev`). Help, usage and error output at every depth begin with the
matching identity headline (cli-extended contract, `docs/CLI-SPEC.md`). Use
[`ATTACH-GUIDE.md`](../ATTACH-GUIDE.md) for the complete gate integration
recipe, [`CGPCTL-SPEC.md`](CGPCTL-SPEC.md) for every verb, and the
[migration table](#migration-table-1x-command-to-cgpctl-verb) when an older
command or script needs translating.

## Install and run the native service

The daemon is a native systemd service installed from pinned wheels by
`cgpd install`. Phase-1 releases are not deployed to cockpit hosts until
phase 2 updates mdt host-setup; keep `CGPROFILE_WHEEL` below `2.2.0` until the
operator's drained cutover. Old and new clients do not interoperate.

For the first install, bootstrap the CLI from the two wheel files. Set the
filenames and version-plus-SHA pins to the release artifacts you verified:

```bash
CGPROFILE_WHEEL_FILE='./cgprofile-X.Y.Z-py3-none-any.whl'
CLI_EXTENDED_WHEEL_FILE='./cli_extended-X.Y.Z-py3-none-any.whl'
CGPROFILE_PIN='X.Y.Z@sha256:<cgprofile-sha256>'
CLI_EXTENDED_PIN='X.Y.Z@sha256:<cli-extended-sha256>'

PYTHONPATH="$CGPROFILE_WHEEL_FILE:$CLI_EXTENDED_WHEEL_FILE" \
  python3 -m cgprofile.cli.cgpd install \
  --wheel "$CGPROFILE_PIN" \
  --cli-extended-wheel "$CLI_EXTENDED_PIN" \
  --wheel-source ./wheels \
  --control-group cgprofile-control \
  --observer-group cgprofile-observer \
  --add-user "control=$USER" \
  --enable --yes
```

`--wheel-source` may name a directory containing both wheels or an HTTPS
release base. The installer verifies each SHA-256 before unpacking. A TTY
operator can omit required flags and use the wizard to enter the pins, create
or map groups, select users, and enable the service. A non-interactive run
must provide the pins, groups, and either `--enable` or `--no-enable`. Repeat
`--add-user LEVEL=USER` for each existing account to add, for example
`--add-user control=alice --add-user observer=runner`. The installer uses
`gpasswd -a`, records these users, and only adds memberships; later installs
retain them and uninstall never removes them. A user named with
`--add-user` must exist. On reinstall, recorded users whose accounts no longer
exist are dropped and reported; the install continues. Dry-run output lists
each requested addition and any dropped recorded users.

The control and observer groups must be distinct dedicated groups: they cannot
share a name or GID. `cgpd install` and `cgpd config check` refuse a group named
`docker`, a group resolving to GID 0 or the docker GID, or two levels resolving
to the same GID. The refusal applies to flags, saved records, existing config,
and wizard answers, without an override. Cross-service group uniqueness is
checked by host-setup. See the [group design rationale](DESIGN-GUIDE.md#dedicated-groups-for-each-socket-level).

After installation, these checks should pass:

```bash
sudo /opt/cgprofile/current/venv/bin/cgpd doctor --json
sudo cgpctl doctor --json
sudo cgpctl status --json
```

The service CLI stays in the active versioned venv at
`/opt/cgprofile/current/venv/bin/cgpd`; use that path for `install`, `doctor`,
and `uninstall`. The installer links only the client command `cgpctl` into
`/usr/local/bin`.

The control socket is `/run/cgprofile/control/cgprofile.sock`, owned by
`root:cgprofile-control` mode `0660`. The observer socket is
`/run/cgprofile/observer/cgprofile.sock`, owned by
`root:cgprofile-observer` mode `0660`. Their parent directories are
`root:root` mode `0755`; the daemon never widens or adopts directory access.
Only the observer group can use the read-only endpoint. `cgpd doctor` lists
`config-valid`, `units-current`, `groups`, `socket-units`, `socket-nodes`,
`socket-dirs`, `state-dir`, `service`, `connect`, `foreign-rules`, and
`caller-access` checks.

`cgpd install` refuses an old `ctl_socket` / `observer_socket` config, foreign
tmpfiles rules or service drop-ins, a mismatched `/run/cgprofile` tree, an mdt
installer `MANIFEST`, and leftover `PENDING` or `current.new` markers. It
prints the manual cleanup steps. It does not migrate, chmod, chown or remove
old installation state. See
[`NATIVE-DEPLOYMENT.md`](NATIVE-DEPLOYMENT.md#old-installation-refusal-and-manual-removal)
for the exact list.

For an upgrade, `cgpd install` compares the pinned version, config, and each
rendered unit. If a restart is needed, it waits for live sessions to drain.
A drain timeout exits 3 and leaves `PENDING`; once the sessions have ended,
rerun `cgpd install` (with an install record it re-applies). It leaves an
existing `cgpd.toml` as written (a group passed as a flag is applied, with no
flag the file's value wins) and merges only what a flag changes. `--force-restart` skips the drain, proceeds with a warning when the
session list is unavailable, and reports the
sessions it may abort. `--allow-downgrade` is required for an older pin;
rolling back below the socket-unit release removes the socket units first.

`cgpd uninstall` removes service resources and socket nodes, but only on a host
that `cgpd install` owns (`install.toml` or `PENDING`); on an old host-setup
install it refuses before changing anything. It stops `cgprofile.slice`, and
keeps the ownership markers until cleanup finishes. If interrupted while the
running version tree still exists, rerun uninstall from it; if that tree was
already removed, use the manual cleanup steps in the deployment contract.
Groups remain unless `--remove-groups` is requested; `/var/lib/cgprofile`
remains unless `--purge-data` is requested. See the
[deployment contract](NATIVE-DEPLOYMENT.md#4-uninstall-and-doctor).

### Preview the GitHub Release body

From 2.0.0 a CMRU release publishes the wheel only (the OCI image is no longer built or
pushed; CP-54). The GitHub Release for `cgprofile-v<version>` is written by the single
`wheel-publish` handler: the wheel, its sha256 sidecar and `latest.json`, with that version's
dated section of `CHANGES.md` as the body. Preview the exact text before releasing, from the
repository root:

```bash
cmru release cgroup-profiler --config cmru.orchestration.toml --set-version 2.0.0 --dry-run
```

The preview prints the complete body between `----- begin release body -----` and
`----- end release body -----`. It leaves `CHANGES.md` unchanged. The release body is written by the only command of
`[steps.push]` in `cmru.toml`:

```text
cmru handler wheel-publish --prefix cgprofile --cwd .
```

Do not add a second release-body handler (`oci-image-release`, `tarball-publish`): each
writes the whole body and CMRU refuses such a plan before any side effect. Never run the
handler by hand; it needs the protected release identity that only `cmru release` supplies.
If a release reports that the GitHub Release "was written" but could not be verified,
inspect the `cgprofile-v<version>` release and its tag before retrying (see the
[design note](DESIGN-GUIDE.md#the-github-release-body-for-the-published-wheel)).

## One-shot collector behavior

If `cgpctl run` requests DAMON while the host's `nr_kdamonds` registry is
nonempty, the collector refuses to resize it, even when its existing monitors
are stopped, because Linux rebuilds every kdamond entry on a count write. The
collector logs DAMON as unavailable, signals readiness, and continues ordinary
cgroup, CPU and memory sampling. A later DAMON collection or cleanup failure
also disables only DAMON and still writes the normal finished manifest.
`cgpctl run` still launches the wrapped command; a DAMON problem must not
change the command's exit status (R-36h). Confirm actual DAMON collection from
a persisted `damon.jsonl` series, not from `cgpctl doctor` or a visible DAMON
sysfs directory alone. Independent one-shot processes coordinate with the
daemon through the host-shared `/run/cgprofile/state/damon.lock`; if another
cgprofile writer holds the lock, the optional DAMON series is refused rather
than waiting or racing, and ordinary profiling proceeds.

## Daemon adoption

This is the adoption guide: commands here are intended to be copied by an
operator or a run-gate integration. Verbs are `cgpctl` verbs; the wire
underneath is [`PROTOCOL.md`](PROTOCOL.md). Summaries returned by `cgpctl stop`
use schema 1. For the rationale, see
[`DESIGN-GUIDE.md`](DESIGN-GUIDE.md#daemon-safety-and-placement).

The daemon runs natively in the host PID and cgroup namespaces, so DAMON sysfs
resolves host `pid_target` values and no `/hostproc` view exists. It has no
network use beyond its Unix sockets and no Docker socket. Host PID visibility
adds process-table and PID-operation authority; the validation and write
guards are not a containment boundary against arbitrary code execution in this
privileged daemon (see the
[trust boundary](DESIGN-GUIDE.md#daemon-safety-and-placement)). Grant the ctl
group only to operators already trusted with host-administrator access.

Host PID mode makes host PIDs addressable to DAMON but does not guarantee the
kernel accepts every DAMON context. Unsupported DAMON remains
`unavailable:<reason>` and must not fail or alter the workload verdict.

Placement asks systemd to create a transient delegated scope under the
verified gates slice, registers the initial PIDs with that scope, then uses
`AttachProcessesToUnit` to move them into the cgprofile-owned leaf. The scope
path is read back from systemd rather than inferred from the unit name. At stop
the daemon restores only identity-verified survivors to their recorded original
units, removes the leaf, and stops the empty scope. Both directions confirm
resulting membership. Successful moves are written to the session's
`events.jsonl`; an unresolvable or unrestored survivor leaves the scope, leaf
and recovery record intact and appears as `placement.error` in the stop
summary. If that happens, keep the sessions directory and inspect the returned
`session_dir`'s `placement-state.json`; a daemon restart retries the recorded
restoration. `cgpctl prune` preserves an incomplete journal, including when the
normal count or age limit has been reached.

If daemon restart recovery finds an unfinished session, inspect the manifest's
`aborted_reason`: `daemon-restarted` means no placement recovery failure was
recorded; `daemon-restarted-placement-recovery-failed` means placement
restoration failed. In either case, the daemon does not resume liveness clocks.

Placement can also be refused before moving any PID when moving the selected
set would empty its original systemd unit. The session may still start for
ordinary observation with `placement.leaf: null` and
`placement.error: "place-refused:origin-scope-would-empty"`; a one-process
container scope is the common example. Unknown source membership, or a selected
live process that is no longer in its recorded source unit, is refused as
`place-refused:identity-unavailable`. If exact restoration cannot be proved,
the session directory and recovery journal are retained; the daemon never falls
back to a parent cgroup.

Before relying on `host.gates_slice` or requesting `start --place`, install
the host's authored gates slice unit (`dev-gates.slice` by default,
`gates_slice` in `cgpd.toml`). The daemon verifies the loaded, non-transient
unit and finite positive memory and CPU ceilings; an existing cgroup directory
without that evidence is reported as `present:false`, and placement is refused
while profiling continues.

For a raw socket client, send one complete newline-terminated JSON request no
larger than 1 MiB within 25 seconds of connecting. The deadline is absolute:
periodic trickle bytes do not reset it. The daemon closes an incomplete line
at the deadline or EOF without dispatch, and returns `bad-argument` for an
oversized line. On `start`, omit `args.damon` to use the daemon's configured
default; a present `args.damon: null` is invalid. After a `watch` request is
accepted, its streaming connection has no server timeout.

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

The `cgprofile-observer.socket` unit owns
`/run/cgprofile/observer/cgprofile.sock` (`root:cgprofile-observer 0660`) in a
`root:root 0755` directory. Run-Gate validates the directory and socket, then
bind-mounts only the observer level directory read-only into `tester-unified`.
The directory bind lets an already-running container see a new socket after a
socket-unit restart; binding the socket file would pin the old inode.
Run-Gate grants only the dedicated observer GID and sets
`ASSAY_RESOURCE_OBSERVER_SOCKET` to the `cgprofile.sock` path inside the
container. Observer-backed lanes also declare per-container memory and PID
ceilings and refuse arbitrary extra mounts. A missing daemon/socket or unsafe
ownership/mode is an infrastructure refusal, never a silent fallback. The
selected container retains private PID, cgroup, and network namespaces; do not
mount the control level directory or the Docker socket for this feature.
The observer accepts one newline-terminated JSON request of at most 4096 bytes
including the newline and applies a five-second absolute deadline. Consumers
must send one complete line; oversized or incomplete requests are not
dispatched.

Assay uses its local B145 reader first and contacts the observer only when the
local view specifically proves that ancestors are hidden. The daemon derives
the peer's cgroup from `SO_PEERCRED` plus the supplied native TID, limits
queries to the configured gates slice, and returns only that peer's ancestry
counters. The observer requires contract 2. A contract-1 request receives an
explicit unsupported-contract refusal. Any incomplete identity
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
counter grew. Record the daemon's wheel version next to the four outputs. The
probe never substitutes for Assay's own fail-closed attribution; it is evidence
that the live daemon reports the distinction at all.

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

A cockpit or devcontainer runs `cgpctl` in its own private namespaces. Resolve
a target without starting anything:

```bash
cgpctl start --target "container:$NAME" --dry-run    # prints the resolved request, starts nothing
```

`cgpctl` resolves `container:NAME` through the docker CLI client-side;
`containerid:<64hex>`, `cgroup:` and `slice:` need no docker call. `self` means
the container that invoked the CLI. Container identity comes from the ID
lookup, never from a namespace-local PID: host processes appear as PID `0` in
`cgroup.procs` when read from a private PID namespace.
For `pid:N`, the client verifies that the process shares its cgroup namespace
and carries the PID/cgroup namespace identities, namespace-local PID, process
start time, and relative cgroup path under the caller's container ID. The
daemon resolves exactly one matching process in that container subpath and
uses its host PID for proc sampling and DAMON. Missing, changed, or ambiguous
identity is refused; pass an explicit container or cgroup target instead.

The version response has the current wire shape:

```json
{
  "ok": true,
  "contract": 1,
  "cgprofile": "2.0.0",
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
    "path": "/run/cgprofile/observer/cgprofile.sock",
    "listening": true
  }
}
```

Check `resource_observer.contract` and `listening` before running an opted-in
native R2 gate. The field describes the separate observer endpoint; it does
not change the control protocol's contract negotiation
([`PROTOCOL.md`](PROTOCOL.md#10-ctl-contract-2-cgprofile-200)).

The version response's `daemon.damon: "available"` only means the library
and admin sysfs interface are visible; it does not prove that the kernel will
accept a configured context. Check the `damon` field in each `cgpctl start`
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
own pool reservation and teardown against one-shot collectors. It cannot govern
unrelated privileged tools: the kernel exposes no atomic ownership lease for
this global sysfs registry, so do not run `damo` or another non-cooperating
privileged DAMON configurator concurrently. Ordinary profiling continues with
DAMON marked unavailable when reservation is refused. Treat `damon: "on"` plus a
persisted `damon.jsonl` series as the evidence that DAMON actually ran.

`stop` answers `stop-incomplete` while a session's sampling worker is still
running and the session keeps its resources; retry. After the third
consecutive incomplete attempt the daemon finalizes anyway and the Summary
carries `stop_forced: "sampler-still-running"` (manifest `aborted`), which is
the operator recovery for a wedged worker; a daemon restart recovers a session
left `running` as an orphan.

`cgpd run` refuses to start unless its own cgroup is under a bounded top-level
`cgprofile.slice` (see `cgpd unit print`).

**When `cgpctl` cannot reach the daemon**, the `could not reach the daemon`
line on stderr (exit 3) ends in a `[cause: ...]` that names which situation
applies, using only what the calling process can see:

- `socket-not-mounted`: the socket's directory does not exist in this
  environment (a nested or sandboxed lane without the `/run/cgprofile` bind).
- `socket-missing`: the directory exists but the socket node is gone (the
  service is not running, or the node was removed).
- `daemon-refusing`: the node exists and nothing listens (the service exited or is
  restarting; a daemon that refuses to start, for example on the
  `cgprofile.slice` guard above, restarts and leaves exactly this).
- `socket-permission`: the node exists but this user may not connect.

A running daemon also checks every few seconds that its socket path still holds
its own listener. It rebinds when the node was removed or replaced, and exits 3
(so the unit's restart policy recreates it) when it cannot. At start it never
deletes a non-socket node and never replaces a socket another daemon still
answers on: `cgpd run` exits 2 and names the path (CP-34).

**Concurrency and `busy`** (CP-33). Every connection is served on its own
thread, so a client that sends its request line slowly, or a long verb (`export`,
a `stop` against a wedged worker), no longer delays other verbs. Admission is
bounded: 32 connections reading a request line, 16 verbs running, 32 open
`watch` streams (`max_watchers`), and a separate pool of running exports
(`max_exports`) so a heavy export cannot starve `status` or `stop`. A request
over a bound is answered `{"ok": false, "error": {"code": "busy", ...}}`; the
code is additive and the right reaction is to retry shortly. `version`, `host`,
`status` and `gc` are read-only snapshots; `start`, `stop` and `export` are
guarded by the registry and session locks.

**Stopping the daemon.** On SIGTERM the daemon waits up to 4 s for running
verbs, up to 4 s for the sampling workers, then finalizes the live sessions
(forced, with a 1 s session-lock wait), so a normal stop ends in about 9 s.
Live sessions are finalized as `aborted: "daemon-stopped"`; a daemon that exits
3 after losing its control socket aborts them the same way. The unit sets a
stop timeout of 30 s ([`NATIVE-DEPLOYMENT.md`](NATIVE-DEPLOYMENT.md) section
4.2) so a wedged session cannot be killed mid-finalize by the default grace. A
session that could not be finalized in time is recovered as an orphan at the
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
`DAMON_STAT` can be enabled while `nr_kdamonds` is `0`; the kernel documents
special-purpose DAMON modules as exclusive, so another start request can fail
with `EBUSY`. This does not make the control socket or ordinary resource
sampling unusable. `version`'s `damon: available` is only a capability probe;
the start response may record DAMON as `unavailable:OSError: [Errno 16] ...`,
and the session continues without DAMON under R-36h. Confirm the actual `start`
result and persisted `damon.jsonl` series before claiming DAMON samples or
overhead. See the [kernel DAMON design](https://www.kernel.org/doc/html/v7.1/mm/damon/design.html).

Check the host-visible state without changing it:

```bash
if [ -r /sys/module/damon_stat/parameters/enabled ]; then
  printf "damon_stat.enabled="
  cat /sys/module/damon_stat/parameters/enabled
else
  echo "damon_stat.enabled=not-exposed"
fi
printf "nr_kdamonds="
cat /sys/kernel/mm/damon/admin/kdamonds/nr_kdamonds
```

`damon_stat.enabled=Y` with `nr_kdamonds=0` and a reproduced `EBUSY` is
consistent with DAMON_STAT exclusivity, not a failed daemon launch. Whether the
module is enabled by default depends on the installed kernel build
(`CONFIG_DAMON_STAT_ENABLED_DEFAULT`). Do not disable a host-wide monitor as an
automatic diagnostic or from cgprofile; coordinate any temporary change with
the host operator, then verify an actual session start and persisted
`damon.jsonl` series before claiming DAMON data. Ordinary resource profiling
remains usable either way under R-36h.

## Attach a run-gate lane

Run-gate must start the lane first, inspect its immutable 64-hex container ID,
then ask the daemon to profile it over `/run/cgprofile/control/cgprofile.sock`. Generate a
fresh token per lane attempt and pass it through the lane as
`RUN_GATE_PROFILE_SESSION`; the token makes a retry-safe `(container_id, token)`
start idempotent. The `meta` object is required and its `kind` is either
`command` or `assay`.

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

SESSION_ID="$(cgpctl start \
  --target "containerid:${CONTAINER_ID}" \
  --scope container --token "$PROFILE_TOKEN" --damon on --interval 1.0 \
  --idle-bound 30 --on-stall kill \
  --meta "$META" --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["session"])')"

docker exec "$LANE_NAME" ./run-lane.sh
LANE_STATUS=$?

cgpctl stop "$SESSION_ID" --json
exit "$LANE_STATUS"
```

In production code, put the stop call in the lane's `finally`/cleanup path so
it runs when the lane fails. Read the `cgpctl` exit status directly: `0` is a
valid response, `2` is a usage or daemon-declared contract error, and `3` is
unreachable or malformed-daemon state
([`CGPCTL-SPEC.md`](CGPCTL-SPEC.md) section 1). Do not pipe the command through
a pager when deciding whether the stop succeeded.

For `cgpctl start`, an exit-2 `target-not-found` means the daemon searched a
readable host cgroup tree and found no scope for that full container ID.
An exit 3 can also mean the host cgroup tree became unreadable during lookup;
record profiling as unavailable and inspect the daemon's journal. Do not
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
restore. The host system bus is used only by the daemon, never by the
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
When consuming `cgpctl watch`, require its terminal `end` line. `cgpctl watch`
exits 3 if the socket closes before that line; treat it as profiling
unavailable and reconcile the session through `cgpctl show` and `cgpctl stop`.

### Interpreting liveness monitor health

`unknown` means the monitor cannot support a stall conclusion. Preserve the
lane's own wall timeout as the completion backstop. `degraded-monitor` means
the daemon reports the liveness verdict but cannot use that observation to
authorize a kill. `monitor-gap` is also the pause reason for the full missing
span; it does not advance either the idle or progress-cadence clock.

Use this handling for a `watch` verdict block:

```python
def liveness_result(watch):
    if watch["state"] == "unknown":
        return "undetermined; keep the lane wall timeout active"
    if watch["monitor_state"] == "degraded-monitor":
        return "reported only; do not treat this as kill authorization"
    if watch["state"] == "runaway":
        return "busy but below the ceiling; keep the lane running"
    return watch["verdict"]
```

For a lane started with `--on-stall kill`, `over_ceiling` may stop active work
once the daemon clock is past the ceiling and the monitor is fresh and healthy.
That state does not need no-growth evidence. A `runaway` below the ceiling is
not a kill state. Stalled and hung kills do need no CPU, I/O, or configured
stream growth. If the lane has no I/O controller, a missing or empty
`io.stat` is treated as zero growth; an I/O read error refuses the kill. A
configured progress stream that has not appeared gets 120 seconds from session
start; before then, confirmation is refused, and after then stable absence is
treated as no stream growth.

Run-Gate watch consumers treat `unknown` as informational and continue reading
the stream. It does not authorize action or produce a “lane stalled” warning;
a later `killed` verdict remains deliverable.

The complete fields and kill conditions are in the
[`liveness contract`](RG55-INTERFACE-CONTRACT.md#84-liveness-and-policy-d-17-d-22-d-27).

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

The R2 comparison base is always supplied by the caller (`base_source = "request"`
in `assay.toml`). **Use the previous release tag as the base and refresh it at
every release** (`git tag --list 'cgprofile-v*' --sort=-v:refname`, take the
newest tag that is an ancestor of the release candidate). The P1 acceptance
campaign's recorded pre-wave commit `eb3af770eb16362cff4c39388193c0180c2d370c`
is retired as a base: its diff to the current tree exceeds Assay's 64 MiB git
output bound (`GIT_FAILED`), so it can no longer plan. Measured 2026-11-05:
base `cgprofile-v1.2.0` (`543d8e5e3`) plans 557 candidates (2.4 MB diff),
well under `max_mutants = 2000`. From the attached candidate worktree,
pass the base explicitly to both registered entrypoints:

```bash
P1_BASE=cgprofile-v1.2.0   # previous release tag; refresh per release
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
long full-source R2 campaign to be deferred, so **there is no completed
full-source R2 verdict for a release that has not run it**; a release does not
claim one. Run `run-gate --base "$P1_BASE" gate` when that evidence is wanted; see
[release qualification](DESIGN-GUIDE.md#release-qualification-and-the-full-r2-gate).
Run-gate can derive a different base when `--base` is omitted, so an omitted
flag is not the P1 acceptance command. The rationale is in the
[design guide](DESIGN-GUIDE.md#mutation-comparison-base).

## Migration table: 1.x command to cgpctl verb

The command migration table below covers the 1.x CLI vocabulary; it does not describe transport compatibility. cgprofile 2.0.0 replaced the `cgprofile` command with
`cgpctl` (client) and `cgpd` (daemon), the daemon is a native systemd service
installed by mdt host-setup, and the control socket speaks ctl contract 2. cgprofile 2.2.0 also moves service access to per-level systemd sockets at the new paths
([`CGPCTL-SPEC.md`](CGPCTL-SPEC.md), [`PROTOCOL.md`](PROTOCOL.md) section 10,
[`NATIVE-DEPLOYMENT.md`](NATIVE-DEPLOYMENT.md)). There is no alias.

| 1.x | 2.0 |
|---|---|
| `./cgprofile run -- CMD` | `cgpctl run -- CMD` |
| `./cgprofile attach --target T` | `cgpctl start --target T` then `cgpctl stop ID` |
| `./cgprofile mark NAME` | `cgpctl mark NAME` |
| `./cgprofile report --run-dir D` | `cgpctl report --run-dir D` |
| `./cgprofile targets --target T` | `cgpctl start --target T --dry-run` |
| `./cgprofile doctor` | `cgpctl doctor` |
| `./cgprofile serve ...` | `cgpd run` under systemd socket activation, configured by `/etc/cgprofile/cgpd.toml` |
| `docker exec cgprofile-host-daemon cgprofile ctl version` | `cgpctl status` |
| `... ctl host` | `cgpctl host` |
| `... ctl status` | `cgpctl sessions` / `cgpctl show ID` |
| `... ctl start` / `stop` / `watch` | `cgpctl start` / `stop` / `watch` |
| `... ctl report ID` | `cgpctl report ID` (export, rendered on the client) |
| `... ctl gc` | `cgpctl prune` |
| `./setup.sh` | `pip install cgprofile[report]` for the report extra only |
| `CGPROFILE_ALLOW_UIDS`, `serve` flags | keys of `/etc/cgprofile/cgpd.toml`; runtime change by `cgpctl config set` |
| helper mode, placement probe image | removed |
| `--log-tail`, `--log-match` | removed |

Consumer notes:

| Consumer | What to do |
|---|---|
| run-gate | Socket carrier only on `/run/cgprofile/control/cgprofile.sock`; no `exec`, `auto`, or old-path fallback. It keeps speaking contract 1, which the daemon serves for `version`, `host`, `status`, `start`, `stop`, `watch` and `gc`, and renders reports with `cgpctl report` or skips them. |
| assay | Nothing: it uses only the observer socket (contracts 2 and 3). |
| mdt cockpit image | Ships the `cgprofile` wheel (core only) so `cgpctl` is on `PATH`; the finalize step runs `cgpctl skills check`. |
| mdt host-setup | Phase 2 will call the install verbs, render its declared config, and run the service-side doctor. Do not deploy cgprofile 2.2.0 before that consumer update and the host cutover. |
| Devcontainers and runners | After host cutover, mount only `/run/cgprofile/control` or `/run/cgprofile/observer` as required; run `cgpctl --socket PATH` (or set `CGPCTL_SOCKET`) for control. |

A contract-1 `report` request to a 2.x daemon is answered with the error code
`report-removed`, naming `cgpctl report`. Ownership rules apply to `stop` and
`prune`: a caller stops its own sessions, and only uid 0 stops or exports
another uid's. Control and observer groups are separate; observer membership
does not grant control operations.
