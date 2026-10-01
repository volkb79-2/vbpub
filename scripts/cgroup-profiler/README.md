# cgroup-profiler

Profile what a container or cgroup actually does over time — CPU, memory, IO,
swap, pressure — resolve what its **effective** limits are (the whole ancestor
chain, not just the leaf), timestamp phase boundaries so activity can be
correlated with the numbers, and produce an interactive report at the end.

It also watches *neighbours*. The question that produced this tool was not
"how much memory does my gate use" but **"when my gate runs, does the
production game server lose its anonymous pages?"** — so profiling a subject
while observing a victim is a first-class mode, not a workaround.

```bash
./setup.sh                      # once
./cgprofile --version           # source checkout: cgprofile 0.1.0
./cgprofile doctor              # what can I reach?

./cgprofile run \
  --target  slice:dev-gates.slice@follow \
  --observe container:b87c0a5b-2387-4a1c-8863-ff23e6800a1d \
  -- ./gate.sh
```

`./cgprofile --version` is the documented operator identity probe. In a source
checkout it uses the `pyproject.toml` version; a built image embeds the exact
CMRU release version as `CGPROFILE_VERSION`, so the CLI and daemon identify
the same release (for example, `1.1.0`). An untagged local image is explicitly
identified as `0.0.0-dev`. Help, usage, missing-argument, unknown-argument,
and configuration diagnostics at every subcommand depth begin with the
matching `CGPROFILE <version> — cgroup resource profiler` headline. Normal
profiling output is unchanged. The shell shim and `cgprofile.py` therefore
expose the same top-level compatibility option.

- **`ATTACH-GUIDE.md`** — how to wrap or attach this to a gate in any repo.
  Start there if you want to use it.
- **`DESIGN.md`** — architecture and module contracts. Start there if you want
  to change it.
- **`docs/CONSUMERS.md`** — pasteable operator probes and adoption notes.
- **[`docs/DESIGN-GUIDE.md`](docs/DESIGN-GUIDE.md#daemon-safety-and-placement)**
  — why the always-on daemon owns only its measured state and how its contract
  stays fail-closed.
- **[`docs/CONSUMERS.md`](docs/CONSUMERS.md)** — pasteable daemon and run-gate
  adoption examples.

## Verbs

| verb | mode | what |
|---|---|---|
| `run -- <command>` | wrapper | wrap and profile one command |
| `attach` | attach | profile something already running |
| `mark <name>` | either | record a phase boundary from anywhere sharing the run dir |
| `report [--run-dir D]` | either | re-render `report.html`/`report.md` for a finished run |
| `targets [--target …]` | either | resolve target specs and print, without sampling |
| `doctor` | either | what can this process reach? venv/DAMON/access summary |
| `serve` | daemon | run the RG-55 daemon (PID 1 in `cgprofile-host-daemon`) |
| `ctl <verb>` | daemon | talk to a running `serve` over its control socket — see "Running the daemon" below |

The first six are the collector/report CLI (`ATTACH-GUIDE.md`); `serve`/`ctl`
are RG-55's always-on daemon mode, a separate thing entirely — it never
spawns a collector, and run-gate talks to it instead of to `run`/`attach`.
Every daemon session records sample zero at start. A no-token start is always a
new session (subject to `--max-sessions`); only the same non-null token is
idempotent. A token scopes its roots to exact-token processes directly in the
selected cgroup; their descendants remain attributed if they move elsewhere.
The daemon control contract is major version 1, and `ctl` refuses
to print a response whose object, major, `ok`, or verb-specific shape is not
valid.

## What you get

| file | |
|---|---|
| `report.html` | interactive: zoom, pan, hover crosshair across every series, phase bands, event markers, resolution selector. One self-contained file, no server, no network. |
| `report.md` + `charts/` | static twin for pasting into a ledger or a PR |
| `samples.jsonl` | raw samples, for your own analysis |
| `events.jsonl` | what the profiler flagged, with the numbers behind each one |
| `manifest.json` | targets, host facts, and the effective-limit snapshot |

## The parts worth knowing about

**Effective limits, not declared ones.** A container's `memory.max` is rarely
the number that binds it. The profiler walks the ancestor chain and reports
which cgroup actually imposes each ceiling. It also reports `memory.min` and
`memory.low` twice — once as they hold *without* `memory_recursiveprot`, once
as they would hold with it — because a mount missing that flag silently
discards protection an ancestor declared, and nothing else on the host tells
you.

**Adaptive sampling.** 250 ms while anything is moving, backing off to 2 s when
quiet, snapping back to hot on any event. Designed to be run on a host that is
already short of memory: the collector is standard-library only, holds no
series in memory, and does all analysis after the run.

**Phases three ways.** Explicit marks (`cgprofile mark "restoring db"`, callable
from inside a gate container), wrapper-derived boundaries for gates that know
nothing about the tool, and changepoint detection over the series so unlabelled
regime shifts still get timestamped.

**Runs from a devcontainer.** The profiler re-executes itself inside a
privileged helper with private PID/cgroup namespaces and explicit read-only
bind mounts of host `/proc` and cgroup v2. The helper uses the host proc mount
for process details; it does not join either host namespace, and you do not
need a host install. Container targets are resolved to Docker IDs by the
caller before re-exec; in helper mode `self` means the invoking container,
not the short-lived helper. For `pid:N`, the caller carries the PID- and
cgroup-namespace identities, namespace-local PID, process start time, and
relative cgroup path. The helper matches those facts against processes in
that container subpath and requires exactly one live match; it then uses the
PID visible in its host `/proc` view for process sampling and DAMON. Missing,
changed, or ambiguous identity is a refusal, never a guess from the caller's
numeric PID. This avoids treating caller PIDs as visible in the helper's
private PID namespace, where `cgroup.procs` cannot identify them. Before the
helper starts, a bounded probe
asks host systemd to confirm that both the configured
placement slice and the cockpit's injected interactive slice are loaded, not
transient, and have instantiated cgroups. The probe uses the local
`tester-unified:local` image (override with
`CGPROFILE_PLACEMENT_PROBE_IMAGE` if you provide a compatible local image with
`systemctl`); missing verification evidence refuses helper launch.
See [the design guide](docs/DESIGN-GUIDE.md#private-namespaces-and-host-views)
for the namespace rationale and [the consumer guide](docs/CONSUMERS.md#resolve-a-target-from-a-cockpit)
for a working helper-mode command.

**Nothing at run time.** Dependencies are pinned in `requirements.txt` and
built once by `./setup.sh`. No code path installs, downloads, or fetches while
profiling — that would add exactly the load the tool exists to measure.

## Running the daemon

RG-55 added a second, always-on mode: a host daemon that run-gate (or anyone
else) talks to over a Unix socket instead of spawning a collector per lane.
It keeps PID/cgroup namespaces private and receives a read-only host `/proc`
view plus an explicitly writable host cgroup-v2 view for opt-in P6 placement.
`CgroupWriteGuard` limits cgroup writes to the placement whitelist; the
daemon uses the read-only host system bus only for systemd's narrow
`AttachProcessesToUnit` PID move when private PID translation makes a direct
`cgroup.procs` write impossible. It uses that bridge both to place a lane into
its gates leaf and, at `stop`, to return survivors to their original systemd
scope. Each move is verified through `/hostproc`; if survivors cannot be
enumerated or restored, the daemon reports the exact cgroup path and leaves
the lane leaf in place rather than hiding the stranded processes.
The one-shot helper keeps its cgroup view read-only. It is
`scripts/cgroup-profiler/`'s own **standalone ciu root** —
`RG55-INTERFACE-CONTRACT.md` is the full wire contract.

```bash
python3 build-push.py --build      # -> local-only cgprofile:local (needs buildx)
ciu up --dir .                     # starts cgprofile-host-daemon
docker exec cgprofile-host-daemon cgprofile ctl version --json
docker exec cgprofile-host-daemon cgprofile ctl status --json
ciu down                           # stops it (run from this dir); the volume survives
```

The daemon's version response is contract major 1:

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

- **One daemon per host, deliberately.** `deploy.environment_tag = "host"`
  (not `$INSTANCE_ID` like every other ciu stack in this estate) — the
  container name is the fixed literal `cgprofile-host-daemon`. A second
  worktree running `ciu up --dir .` collides on that name and refuses to
  start a sibling, on purpose: the daemon owns the host's DAMON facility and
  observes host proc/cgroup state through explicit mounts, which cannot be
  meaningfully duplicated. PID and cgroup namespaces remain private.
- **`--network none`, no docker socket inside the container.** The only
  surface is `/run/cgprofile/ctl.sock`, reached with `docker exec
  cgprofile-host-daemon cgprofile ctl <verb> --json` from anywhere with
  docker access to that container. `ctl`'s own client-side timeout is 25 s;
  see the contract §1.3/§1.5 for run-gate's own per-verb timeouts.
- **Two carriers, one protocol** (contract §8.1, design D-30). That same
  socket is also bind-mounted to the host at `/run/cgprofile`, so a
  consumer that cannot (or should not) use the docker socket speaks the
  protocol directly:

  ```bash
  # the exec carrier (default, unchanged, always available)
  docker exec cgprofile-host-daemon cgprofile ctl version --json
  # the socket carrier: one request line, one response line
  printf '{"verb": "version", "args": {}, "contract": 1}\n' \
    | socat - UNIX-CONNECT:/run/cgprofile/ctl.sock
  ```

  Every verb, response, error code and the `contract` field are identical
  on both — `docs/PROTOCOL.md` documents each verb's `args` names, and
  `tests/fixtures/rg55/socket/` freezes a request/response pair per verb.
  On `start`, omitting `--damon` uses the daemon's configured default; a
  socket request must omit `damon` too, because explicit `damon: null` is
  invalid.
  A complete request line including its newline is limited to 1 MiB and
  must arrive within 25 seconds; trickle bytes do not extend the deadline.
  Oversized lines receive `bad-argument`, and an incomplete line is closed
  without dispatch. `watch` has no timeout after its initial request line.
  - **Host prerequisite:** the directory `/run/cgprofile` on the host is
    created by mdt host-setup's tmpfiles.d entry `mdt-cgprofile.conf`
    (`d /run/cgprofile 0770 root docker -`). **That entry decides who may
    use the socket carrier: members of the `docker` group** — the same
    principals who can already `docker exec` into the daemon, which is why
    the two carriers have the same trust boundary. The daemon re-asserts
    `0770` on the directory and `root:<the directory's own gid>` `0660` on
    the socket at every start; it never picks the group itself.
  - **Without host-setup** the directory is `root:root`, the socket ends up
    root-only, and the daemon logs one INFO line saying so (`docker logs
    cgprofile-host-daemon`). Nothing breaks: the exec carrier is unaffected
    and stays the default.
  - **`CGPROFILE_ALLOW_UIDS`** (optional, compose env, comma-separated
    uids) narrows the socket carrier further: `SO_PEERCRED` on every
    connection, uid 0 always allowed (that is how the exec carrier
    arrives), everyone else refused with `{"ok": false, …, "error":
    {"code": "peer-refused", …}}`. Unset = no uid restriction beyond the
    socket's group mode. A malformed value refuses to start the daemon
    rather than silently widening access.

- **Target lookup reports what was established.** A complete cgroup-tree
  search with no matching container returns `target-not-found` (ctl exit 2).
  If the host cgroup bind cannot be searched, `ctl` exits 3 as a daemon
  fault; that result does not establish that the container is absent. See
  [the contract boundary](docs/DESIGN-GUIDE.md#contract-boundary) for why
  the daemon uses the host tree as its identity source.
- **Sessions live in the named volume** `cgprofile-sessions`, mounted at
  `/var/lib/cgprofile/sessions` — `ctl stop <session>`'s response names the
  exact `session_dir` and the series files inside it (`samples.jsonl.gz`,
  `events.jsonl`, `host.jsonl`, `manifest.json`, `summary.json`, plus
  `damon.jsonl` only when actual DAMON samples were persisted). `ciu down`
  preserves the volume; `ciu clean`/`--reset`
  removes it, same as every other named volume in this estate.
- **Rendering a report:** `docker exec cgprofile-host-daemon cgprofile ctl
  report <session> --json` — the same interactive `report.html` a
  `cgprofile run`/`attach` session produces, rendered by the image's own
  venv (the daemon process itself never imports pandas/plotly — see
  `lib/serve.py`'s `handle_report`). May take longer than 30 s on a long
  session; run-gate itself never calls this verb.
- **Retention:** `--keep-sessions 200 --keep-days 14` by default (`serve`
  CLI flags) — the newest N finished sessions are kept, older ones dropped
  on every `stop`, or on demand via `ctl gc --json`. A live session is
  never pruned.
- **`cgprofile.slice` (D-29) — the daemon's own containment.** An OPERATOR
  step, once per host, **before** the first `ciu up` on that host: see
  `infra/README.md` (`sudo cp infra/cgprofile.slice
  /etc/systemd/system/ && sudo systemctl daemon-reload`). Not installed →
  the daemon still starts, under an implicitly-created, unbounded slice;
  `ctl host` reports the actual `daemon_slice.memory_min_bytes` and
  `memory_high_bytes` (a zero or null floor means no memory reservation).
  This is a
  DIFFERENT slice from the gates slice below — one bounds the daemon
  itself, the other is where placed lanes live.
- **Liveness/watch policy (D-27, contract §8.2/§8.4) — `start` options.**
  `--progress-stream <regular file path as the lane sees it>` (read through
  `/proc/<pid>/root/<path>`; FIFOs, devices, and unfinished lines are
  ignored),
  `--idle-bound auto|<seconds>` (`auto` =
  `max(300, 3 x cadence hint)`), `--ceiling auto|<seconds>` (`auto` = `3 x
  meta.expected.duration_s`, else none), `--on-stall kill|report` (default
  `report`). An unparsable value is `bad-policy` — the session is not
  started. `kill` uses only an exact cgroup boundary: `scope=container` may
  kill the target container cgroup when its ID is proven by the runtime leaf
  name beneath the verified gates slice and `cgroup.events` confirms it is
  populated; `scope=container-shared` requires a
  token and an explicit, successfully verified `--place` leaf. The daemon
  never signals numeric PIDs or falls back to them. If the requested boundary
  cannot be established, `start` returns `bad-policy`; an empty or unreadable
  target or a runtime kill refusal is recorded as `reported`, never `killed`.
  `docker exec cgprofile-host-daemon cgprofile ctl watch
  <session> --json` streams `reading`/`verdict`/`end` lines on either
  carrier (the one verb where more than one response crosses the wire per
  connection — `docs/PROTOCOL.md` §1's documented exception) until the
  session ends; `--watch-interval` (default 30 s, clamped [5, 300])
  controls the `reading` cadence. A disconnected watch without its terminal
  `end` exits 3. `ctl status`/`ctl stop` also carry the
  current `liveness`/`watch` blocks for a session that was never watched
  with `ctl watch` directly.
- **Placement (D-20/D-25/D-31, contract §8.3) — `start --place`.** Creates a
  systemd-owned transient `rg-profile-<token>.scope` directly beneath the
  verified gates slice, then a cgprofile-owned `rg-<token>` leaf below that
  scope. The default gates slice is `dev-gates.slice` under `dev.slice`;
  mdt host-setup supplies its authored unit, and `serve --gates-slice <name>`
  selects another one. systemd retains ownership of the slice and scope
  boundary; cgprofile owns only the per-lane leaf. The daemon permits placement
  only when systemd verifies the expected loaded, non-transient slice and the
  slice has finite positive memory and CPU ceilings; a directory alone is not
  proof. It moves the token's processes into the leaf, restores identity-checked
  survivors to their recorded original units on `stop`, removes the leaf, and
  retires the empty transient scope. A failed or ambiguous restore preserves
  the leaf and recovery record. `--memory-high <bytes>`, `--memory-max
  <bytes>` (refused as `place-refused:over-slice` above the gates slice's own
  ceiling), and `--cpu-weight <1-10000>` set leaf controls; applied values are
  always READ BACK from the kernel, never echoed. Host refusals leave profiling
  available but the lane unplaced. A scope-name collision is refused, never
  adopted or cleaned up. This normally leaves profiling available; if the same
  request requires shared-scope `--on-stall kill`, the daemon rejects it before
  creating a session because there is no verified kill boundary. Run-gate
  requests that policy only when its existing resource plan already requested
  placement; otherwise its local watchdog remains verdict authority. A
  malformed cap value is `bad-argument` (exit 2, no session) — the client
  typed it wrong, not the host.
- **Placement memory is charge-based, not total RSS.** After successful
  placement, cgroup memory counters and the leaf's `memory.high`/`memory.max`
  describe charges attributed to that cgroup. Moving an already-running
  process does not transfer charges for pages it faulted earlier, so these
  numbers are not total process RSS and the leaf limit is not a hard cap on all
  memory already resident in the lane. See the detailed accounting notes in
  [`docs/CONSUMERS.md`](docs/CONSUMERS.md#resource-accounting-with-placement).
- **Private namespaces, explicit host views.** The daemon and its helper
  keep PID and cgroup namespaces private. Host `/proc` is explicitly bound
  read-only; the daemon receives a writable host cgroup-v2 bind because
  opt-in placement creates `rg-*` leaves and moves lane pids. D-25's whitelist
  is an application-level guard on normal code paths, not an OS-enforced
  boundary against arbitrary code execution in the privileged daemon. A private-PID
  fallback asks host systemd over the daemon-only mounted system bus to create
  the delegated scope with its initial PIDs, then attach verified PIDs to the
  leaf and return survivors to their recorded original units; the daemon never
  joins a host namespace. Both directions require the host-proc view, verify
  identity and membership afterward, and record successful moves. Unresolvable
  or unrestored survivors keep the leaf and recovery record. The daemon's D-25
  write guard limits intended cgroup writes to the documented whitelist; DAMON retains
  its separately mounted sysfs write surface. The consumer-facing socket does
  not expose systemd D-Bus to the cockpit. `ciu up` is the managed lifecycle;
  there is no host-namespace fallback launcher. This raw manager bridge is an
  operational authority path, not a sandbox against daemon compromise; the
  rationale and deferred broker option are in the
  [RG-55 placement design](../../run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md#a3-placement-ownership-correction-delegated-scope-below-dev-gatesslice-2026-09-30).

## Relationship to the neighbours

- `scripts/damon-analysis/` — DAMON working-set analysis. `--damon` reuses its
  `SysfsInterface` and `Classifier` to add a hot/warm/cold breakdown alongside
  the counters.
- `modern-debian-tools-python-debug/host-setup/` — owns the host's dev-tier
  slice governance (`dev-interactive`, `dev-background`, `dev-gates`,
  `dev-buildkitd`). This
  tool measures those tiers; it does not manage them. Proposals in the report
  are phrased as changes to *that* configuration.
- `/usr/local/sbin/soulmask-zswap-monitor.sh` — live production health. The
  `rfz/s`, `rfd/s` and `rff/s` definitions here are deliberately identical, so
  the two can be read side by side.

## Requirements

Linux with cgroup v2, Python 3.11+, and either root on the host or a Docker
socket to launch the helper. `./setup.sh` builds the venv (pandas, plotly,
matplotlib, ruptures, scipy) used only for analysis and reporting.
