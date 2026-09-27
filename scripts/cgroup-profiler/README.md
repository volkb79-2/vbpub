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
the same release (for example, `1.0.0`). An untagged local image is explicitly
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
It keeps PID/cgroup namespaces private and receives explicit read-only host
`/proc` and cgroup-v2 mounts; only its DAMON interface and session storage are
writable. It is `scripts/cgroup-profiler/`'s own **standalone ciu root** —
`RG55-INTERFACE-CONTRACT.md` is the full wire contract.

```bash
python3 build-push.py --build      # -> cgprofile:local (needs docker buildx)
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
- The daemon keeps PID and cgroup namespaces private. Its host observation
  comes from explicit read-only `/proc` and cgroup-v2 binds; DAMON retains
  only its separately mounted sysfs write surface. `ciu up` is the managed
  lifecycle and must preserve those settings—there is no host-namespace
  fallback launcher.

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
