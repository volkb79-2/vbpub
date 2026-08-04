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
./cgprofile doctor              # what can I reach?

./cgprofile run \
  --target  slice:dev-background.slice@follow \
  --observe container:b87c0a5b-2387-4a1c-8863-ff23e6800a1d \
  -- ./gate.sh
```

- **`ATTACH-GUIDE.md`** — how to wrap or attach this to a gate in any repo.
  Start there if you want to use it.
- **`DESIGN.md`** — architecture and module contracts. Start there if you want
  to change it.

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

## Relationship to the neighbours

- `scripts/damon-analysis/` — DAMON working-set analysis. `--damon` reuses its
  `SysfsInterface` and `Classifier` to add a hot/warm/cold breakdown alongside
  the counters.
- `modern-debian-tools-python-debug/host-setup/` — owns the host's dev-tier
  slice governance (`dev-interactive`, `dev-background`, `dev-buildkitd`). This
  tool measures those tiers; it does not manage them. Proposals in the report
  are phrased as changes to *that* configuration.
- `/usr/local/sbin/soulmask-zswap-monitor.sh` — live production health. The
  `rfz/s`, `rfd/s` and `rff/s` definitions here are deliberately identical, so
  the two can be read side by side.

## Requirements

Linux with cgroup v2, Python 3.11+, and either root on the host or a Docker
socket to launch the helper. `./setup.sh` builds the venv (pandas, plotly,
matplotlib, ruptures, scipy) used only for analysis and reporting.
