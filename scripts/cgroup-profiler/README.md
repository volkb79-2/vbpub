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
./cgprofile --version           # prints cgprofile 0.1.0
./cgprofile doctor              # what can I reach?

./cgprofile run \
  --target  slice:dev-gates.slice@follow \
  --observe container:b87c0a5b-2387-4a1c-8863-ff23e6800a1d \
  -- ./gate.sh
```

`./cgprofile --version` is the documented operator identity probe. It prints
exactly one `cgprofile 0.1.0` line to stdout, exits 0, and emits no stderr; the
value comes from the project version in `pyproject.toml`. Help, usage, missing-
argument, unknown-argument, and configuration diagnostics at every subcommand
depth begin with `CGPROFILE 0.1.0 — cgroup resource profiler` as line 1.
Normal profiling output is unchanged. The shell shim and `cgprofile.py`
therefore expose the same top-level compatibility option.

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
idempotent. The daemon control contract is major version 1, and `ctl` refuses
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

**Runs from a devcontainer.** `/sys/fs/cgroup` inside one is a namespaced
read-only view of its own subtree — the host's slices are simply not there. The
profiler re-executes itself inside a privileged helper container with the host
cgroup namespace, so the sampling code is identical either way and you do not
need a host install.

**Nothing at run time.** Dependencies are pinned in `requirements.txt` and
built once by `./setup.sh`. No code path installs, downloads, or fetches while
profiling — that would add exactly the load the tool exists to measure.

## Running the daemon

RG-55 added a second, always-on mode: a privileged host daemon that
run-gate (or anyone else) talks to over a Unix socket instead of spawning a
collector per lane. It is `scripts/cgroup-profiler/`'s own **standalone ciu
root** — `RG55-INTERFACE-CONTRACT.md` is the full wire contract.

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
  start a sibling, on purpose: the daemon owns the whole host's DAMON
  facility and cgroup v2 view (`--privileged --pid=host --cgroupns=host`),
  which cannot be meaningfully duplicated.
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
- **If `ciu up` ever refuses** `privileged`/`pid`/`cgroupns` (it does not,
  as of this writing — verified with `ciu up --dir . --dry-run`, see the
  P1 REPORT's C7 section for the exact governance overlay observed),
  `tools/daemon-run.sh` would be the documented `docker run` fallback; it
  is not shipped because the refusal has not (yet) happened.

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
