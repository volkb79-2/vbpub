---
name: cgprofile
description: Profile a container/cgroup/slice's real resource use over time (CPU, memory, IO, swap, pressure), resolve its EFFECTIVE limits (whole ancestor chain), and check for collateral damage to a neighbour (e.g. "does the production game server lose pages when my gate runs?"). Use for host-contention diagnosis, capacity questions before capping a container, or explaining an unexplained slowdown/OOM — never guess these numbers by eyeballing `docker stats`.
---

> **Tool versions as of last verified update (2026-09-17):** this is a
> vbpub-local script (`scripts/cgroup-profiler/cgprofile`), not a pip
> package — re-verify against `./cgprofile --help` / `./cgprofile <verb>
> --help` if a flag below drifts; it can change without an upstream release
> to track. Requires `./setup.sh` once (builds the analysis venv:
> pandas/plotly/matplotlib/ruptures/scipy).

> **MANDATE.** Never answer "why is my gate slow" or "is my container
> memory-starved" by reading `docker stats`/`free`/`cat
> /sys/fs/cgroup/.../memory.current` by hand and reasoning about it — a
> cgroup's own `memory.max` is rarely the number that actually binds it
> (the ancestor chain matters, and `memory_recursiverot` silently changes
> what `memory.min`/`memory.low` mean). Run `cgprofile` and read its
> `manifest.json`/report instead of hand-deriving effective limits.

# cgroup-profiler (cgprofile)

## What it's for

Not "how much memory does my gate use" alone — **"when my gate runs, does
[some other thing sharing this host] lose its anonymous pages?"** Profiling
a subject while simultaneously observing a victim/neighbour is a first-class
mode, not a workaround. This is the tool for host-contention diagnosis on
ANY host shared between a gate/build workload and something else that
matters (a production service, another team's CI) — e.g. dstdns's own host
shares production game-server load (see its `dstdns-host-shared-with-prod-
game-server` memory/decisions D-338/D-373), which is the concrete scenario
that motivated this tool's `--observe` mode in the first place.

## Quick start

```bash
cd scripts/cgroup-profiler
./setup.sh              # once
./cgprofile doctor       # what can this process reach? (helper image, cgroup access)
```

## Profile a command, optionally watching a neighbour

```bash
./cgprofile run \
  --target  slice:dev-background.slice@follow \
  --observe container:<production-container-id> \
  -- ./gate.sh
```

- `--target`/`-t` (repeatable) — what to profile: `cgroup:/path`, `slice:NAME`,
  `container:NAME`, `label:k=v`, `pid:N`, or `self`. Suffix options after
  `@`: `follow`/`nofollow`, `metrics=mem+io`, `as=LABEL`, `role=...`.
- `--observe`/`-o` (repeatable, same spec syntax) — watch but do NOT treat as
  the subject; this is the "victim" side of a collateral-damage question.
- `--follow-children` — also sample cgroups that APPEAR under a target
  mid-run (a gate that itself spawns containers needs this to catch them).
- `--damon` — add DAMON working-set hot/cold breakdown (reuses
  `scripts/damon-analysis/`'s `SysfsInterface`/`Classifier`).
- `--cap CG:FILE=VALUE` — temporarily set a cgroup limit for the run and
  restore it after, e.g. `--cap /dev.slice/dev-background.slice:memory.max=2G`
  — use this to answer "what happens if I cap this tier tighter" without
  permanently changing host config.
- `--phase PHASE` — name for the wrapped command's own phase.
- `--log-tail container:NAME[@as=LABEL]` + `--log-match NAME=PATTERN` (or
  `--log-match-file`) — turn matching log lines into phase marks
  automatically, for a gate that emits its own phase boundaries in its logs
  but knows nothing about cgprofile itself.

## Profile something already running (no command to wrap)

```bash
./cgprofile attach --target container:<name> --duration 300
# or: --until-file <path>  -- stop when that file appears, for an external
# process to signal "done" without cgprofile needing to know its lifecycle
```

Same target/observe/cap flags as `run`, but there is no wrapped command —
use this to profile a long-lived service instead of a one-shot command.

## Re-render a report from a finished run

```bash
./cgprofile report --run-dir <path> [--html-only|--md-only]
```

Useful after a `run`/`attach` invoked with `--no-report` (defer rendering),
or to regenerate in a different format without re-collecting samples.

## Just resolve limits, no profiling run

```bash
./cgprofile targets --target container:<name>
```
Same target syntax as `run`, but only resolves and prints the effective
limit chain — use this for a quick "what's this container's REAL ceiling"
question without a full timed profiling run.

## Mark a phase boundary from inside the profiled command

```bash
cgprofile mark "restoring db" [--kind phase|event] [--meta '{"k":"v"}']
```
Callable from inside a gate container itself (needs the `--run-dir` the
wrapping `run` invocation set up, or discovers it via environment) — use
this in a multi-step gate script to get precise phase boundaries in the
report instead of relying only on changepoint detection over the raw series.

## Output

`report.html` (interactive, self-contained, zoom/pan/hover across every
series, phase bands, event markers) is the one to open first. `report.md` +
`charts/` is the static twin for pasting into a ledger/PR. `samples.jsonl` /
`events.jsonl` are raw data for your own analysis. `manifest.json` carries
targets, host facts, and the effective-limit snapshot — read this
programmatically rather than re-deriving effective limits from raw
`/sys/fs/cgroup` reads.

## Gotchas

- **Runs from a devcontainer transparently.** `/sys/fs/cgroup` inside one is
  a namespaced read-only view of its OWN subtree — the host's slices simply
  aren't there. cgprofile re-executes itself inside a privileged helper
  container with the host cgroup namespace automatically; you do not need a
  host install, and the sampling code is identical either way.
- **Nothing installs at run time** — dependencies are pinned and built once
  by `./setup.sh`; a profiling run that needed network/install access would
  add exactly the load the tool exists to measure.
- **`--helper-cgroup-parent`** defaults to `$CGPROFILE_HELPER_CGROUP_PARENT`
  or `$CGROUP_PARENT_DEV_INTERACTIVE` — deliberately NOT the daemon default
  (which, on a shared host, is the same tier gate runs are profiled IN —
  putting the helper there would contaminate the measurement).
