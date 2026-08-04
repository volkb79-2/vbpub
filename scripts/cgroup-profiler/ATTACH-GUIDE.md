# Attaching cgprofile to a gate

How to profile a gate, build, or validation run in any repo on this host, and
how to make its phases legible in the report. Nothing here requires changing
the gate itself — the wrapper form works on a gate that knows nothing about
this tool. Phase marks are the optional upgrade.

There is deliberately **no gate integration code** in this package: no exit
codes to interpret, no policy file, no CI hooks. `cgprofile` observes and
reports; whether a run was acceptable stays the calling gate's decision.

---

## 0. Once per host

```bash
cd /workspaces/vbpub/scripts/cgroup-profiler
./setup.sh          # builds venv/ from pinned requirements
./cgprofile doctor  # what this process can reach
```

`doctor` tells you whether the profiler can see the host cgroup tree directly
or will re-execute itself inside a privileged helper container. From a
devcontainer it is always the latter, and that is fine — it is the normal path.

---

## 1. Wrap the gate (no gate changes)

The simplest useful form. Profile the gate, and watch a production container
for collateral damage while it runs:

```bash
./cgprofile run \
  --target  slice:dev-background.slice@follow \
  --observe container:b87c0a5b-2387-4a1c-8863-ff23e6800a1d \
  -- ../../shared-ramdisk-depot-manager/tools/gate.sh
```

- `--target` is what you are profiling. `@follow` matters here: a gate spawns
  its containers *after* the profiler starts, and without it you would sample
  the slice but not the containers that appear inside it.
- `--observe` is the neighbour you are checking for damage. Observers are
  sampled and correlated but never treated as the subject, and they are what
  drives the `victim_pressure` events.
- The wrapped command's exit code becomes `cgprofile`'s exit code, so this
  drops into an existing script without changing its semantics.

Where the gates on this host actually live:

| repo | gate | lands in |
|---|---|---|
| `shared-ramdisk-depot-manager` | `tools/gate.sh` | `$CGROUP_PARENT_DEV_BACKGROUND` |
| `cmru` | `src/cmru/tester_gate.py` | `CMRU_TESTER_CGROUP_PARENT` → same |
| `ciu` | `src/ciu/governance.py` | `[<root>.governance].cgroup_parent` → same |

All three resolve to `dev-background.slice` unless overridden, so
`--target slice:dev-background.slice@follow` covers them without knowing which
one you are about to run.

---

## 2. Attach to something already running

```bash
./cgprofile attach --target container:my-service -d 300
./cgprofile attach --target slice:dev-background.slice@follow --until-file /tmp/gate.done
```

`--until-file` is the clean way to bound a run whose length you do not know:
the gate touches the file when it finishes, and the profiler stops.

---

## 3. Phase marks — the optional upgrade

Marks are what turn a flat timeline into "startup / warm-up / job A / idle".
`cgprofile run` exports two variables into the wrapped command's environment:

| variable | meaning |
|---|---|
| `CGPROFILE_RUN_DIR` | the run directory; the mark channel lives here |
| `CGPROFILE_MARK` | absolute path to the `cgprofile` entry point |

So a gate that wants to label its own phases does:

```bash
"$CGPROFILE_MARK" mark "restoring fixture db"
"$CGPROFILE_MARK" mark "running suite"
"$CGPROFILE_MARK" mark "teardown complete" --kind event
```

`--kind phase` (the default) opens a new phase and closes the previous one.
`--kind event` is a point annotation that does not change the phase.

Guard it so the gate still runs unprofiled:

```bash
mark() { [ -n "${CGPROFILE_MARK:-}" ] && "$CGPROFILE_MARK" mark "$@" || true; }
mark "building image"
```

`cgprofile mark` needs neither the venv nor the host cgroup tree — it appends
one line to a file. That is why it works from inside a gate container.

### Marking from inside a container

The mark channel is a file in the run directory, so a container only needs that
directory bind-mounted and the profiler's own path visible:

```bash
docker run --rm \
  --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
  -v "$CGPROFILE_RUN_DIR:$CGPROFILE_RUN_DIR" \
  -e CGPROFILE_RUN_DIR \
  -v /workspaces/vbpub/scripts/cgroup-profiler:/cgprofile:ro \
  my-gate-image \
  sh -c '/cgprofile/cgprofile mark "suite start"; run-the-suite'
```

Appends are a single `write()` under `O_APPEND`, so several containers marking
at once interleave cleanly rather than corrupting each other.

---

## 4. Reading the report

```
runs/run-YYYYmmdd-HHMMSS-xxxx/
  report.html    interactive — zoom, pan, hover; open it in a browser
  report.md      static twin with PNGs, for pasting into a ledger or PR
  samples.jsonl  raw data, if you want to do your own analysis
  events.jsonl   what the profiler thought was worth flagging
```

The interactive report is one self-contained file and needs no server and no
network. plotly.js alone is 4.7 MiB of that; a measured 20-second attach to
`dev-background.slice@follow` (29 cgroups) came to 6.9 MiB total.
`--plotlyjs directory` writes `plotly.min.js` beside the report instead,
which cut the same report to 44 KiB plus one shared sibling file — worth using
when you are keeping many runs.

Read it in this order:

1. **The effective-limits table.** This is usually the surprise. It shows both
   what the cgroup declares and what its ancestors actually impose — "your
   container says `memory.max=12G` but `dev-interactive.slice` says 8G" is the
   sort of thing that explains a whole class of mystery.
2. **The phase bands.** Line up the memory/IO shape against what the workload
   was doing.
3. **The events.** Every one carries the numbers that triggered it.
4. **The proposals.** Each carries its evidence and a confidence of `observed`,
   `inferred`, or `speculative`. Treat `speculative` as a question, not a
   recommendation.

---

## 5. Testing a limit without editing host units

`--cap` sets a cgroup limit for the duration of the run and restores it after,
including on `SIGINT`/`SIGTERM`:

```bash
./cgprofile run \
  --target slice:dev-background.slice@follow \
  --cap /dev.slice/dev-background.slice:memory.max=2G \
  --observe container:b87c0a5b-2387-4a1c-8863-ff23e6800a1d \
  -- ./gate.sh
```

This is how you A/B a gate under a tighter ceiling before proposing it as a
permanent change. **It writes to a live host** — the restore is guaranteed on
every exit path, but do not point it at production cgroups casually.

---

## 6. DAMON

`--damon` adds working-set hot/warm/cold breakdown alongside the counters,
reusing `scripts/damon-analysis`. It takes the host's single DAMON facility for
the duration of the run and always releases it, so do not run two DAMON
consumers at once.

---

## 7. Cost

The profiler is meant to be run on a host that is already under pressure, so
its own footprint is part of the design:

- Sampling is adaptive: 250 ms while anything is moving, backing off to 2 s
  when quiet. **Read that literally — it backs off when *nothing* in the
  sampled set is moving.** Measured on this host: attaching to
  `dev-background.slice@follow` never backed off at all across 20 seconds,
  because that tier also holds a 26-container service stack and something in it
  is always busy. Point `--target` at what you actually care about, and reach
  for `@follow` deliberately; a narrower target is both cheaper and easier to
  read.
- Data volume follows directly from that. The same capture was 89 KiB per
  sample across 29 cgroups. Samples are gzipped on the way to disk (5.9x
  measured), which turns a ~600 MB half-hour run into ~100 MB — still a real
  number to budget for on a full disk.
- The collector is standard-library only and holds no series in memory — every
  sample is appended and forgotten.
- Analysis and rendering happen **after** the run, never during it.
- Nothing is installed, downloaded, or built at run time. If `setup.sh` has not
  been run, collection still works and only the report step complains.
