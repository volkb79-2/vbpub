# TUI-SPEC: `topos`, a host state and cgroup governance console

Status: target design from the operator interview of 2026-11-05. The PoCs in
`docs/PLAN-2026-11-CONSOLE.md` can still change items marked **OPEN** and the shipped
column list.
`docs/STATUS.md` says what exists today. Kernel-mechanism detail that this
document does not repeat (column formulas, friendly-name resolver, DAMON
integration, network provider tiers, BPF design, the degradation matrix) stays
valid in `nyxloom-trove/archive/TUI-SPEC-2026-07.md`; older `§` references point
there.

## 1. What it is for

One screen that answers: **what is this host doing, which cgroup and process is
responsible, and is my governance actually in effect?**

Existing tools each hold part of the answer. The operator today combines `top`,
`mpstat`, `iostat`, `docker stats`, `systemd-cgls`, `systemctl show`, zswap
debugfs and `/sys/fs/cgroup` greps. `topos` puts those facts in one tree.

## 2. Prior art and the real gap

Verified from primary sources on 2026-11-05 (source URLs in the review report
preserved under `docs/PLAN-2026-11-CONSOLE.md`, Evidence).

| Tool | Strong at | Falls short on |
|---|---|---|
| `below` | Cgroup tree, per-cgroup zswap, refault split, PSI, a Properties tab with raw limits, live and replay in one UI, `z` zoom, `f` fold | Raw limit values only; config and stats on different tabs; no effective values; no drift; no verdict |
| `atop` | PSI, per-process disk, `A` autofocus on the busiest resource, cgroup view with depth keys, inactive cgroups hidden | `io.weight` only, no `io.max`; config and processes are not on one row |
| `htop`, `btop`, `bottom` | Process view, meters, graph symbol fallback | No cgroup config; at most a cgroup name column |
| `systemd-cgtop`, `systemd-cgls` | One-letter sorts, tree layout, hides empty groups | No config, no process columns |
| `ctop`, `glances`, `zswapmon` | Container mini-graphs (`ctop` is effectively dead); zswap reject reasons and parameters | Narrow scope |

The earlier claim that "no prior tool combines these" was too strong: `below`
has most of the raw data. What **nobody** does:

- compute effective `memory.min`/`memory.low` through ancestors;
- detect drift between live values and systemd intent;
- show `io.max`, or `io.cost` vrate and per-cgroup usage;
- put config, live stats, PSI and per-process attribution on one row;
- say why the host is stalling.

Ideas adopted: `below` zoom and fold, sort by selected column and step through
samples; `atop` autofocus by PSI and the hide-inactive default; `systemd-cgtop`
per-metric sort keys; `btop` symbol fallback; `ctop` save-view; `zswapmon` reject
and pool-limit stats; `metop` shared time axis.

## 3. Main screen

Meters on top, the tree below, and a side pane following the cursor. The pane
hides below about 140 columns or with `Tab`.

```text
r1002 4c 7.7G  daemon● 1s                                      HOST OK    frozen -
CPU ▇▅▂▆ us32 sy9 io5 st2 si1   LOAD 1.2 1.0 1.5   RUN 3 BLK 2   ctx 12k/s
MEM anon▓▓file▒▒▒▒▒shm░zsw░free·· avail 6.4G  dirty 12M  major 0/s  scan 0/s
ZSW 31M→8M 3.8x pool 1%/25% in0 out0 wb0 rej0   SWAP 52M/16G in0 out0   KSM 159M
PSI some/full  cpu 2.3▁▂▁   mem 0/0▁▁▁   io 1.1/0.9▂▅▃
vda r4.1M w6.9M 25+87 iops await 2+8ms util12% vrate 50%▁   eth0 ↓452 ↑342 drop0
WHY  io full 0.9 ← run-gate 96% CPU 4.6M/s · 2 procs in D on vda
─────────────────────────────────────────────────────────┬──────────────────────
NAME        CPU THR RAM/CEIL   PROT ZSW RF d/f PSI m/i/c │ dev-gates.slice
▾dev.slice   97   0 4.1G/6.7G▅ 768M 72K  0/0   0/.1/.2   │ mem.low 0    →eff 0
 ▾gates      96   0 3.9G/6.0G▆    ·   0  0/0   0/.1/.2   │ mem.high 6.0G
  ▾run-gate  96   0 741M/2.0G▃    ·   0  0/0   0/.1/.2   │ mem.max 6.7G
   D pytest  71     210M                                 │ swap.max 6.2G
   python3   24      95M                                 │ cpu.w 20 ← dev 100
  interact   .2   0 190M       512M 60K  0/0   0/0/0     │ io.max 30k/18k iops
▾system      10   0 990M       1.0G 24M  0/0   0/0/0     │ origin runtime-dropin
/ filter  s sort  a rows:middle(3 hidden)  Tab pane  space freeze  , . step  ? help
```

### 3.1 Tree and processes

- The cgroup tree is the spine of a single view; there are no lenses or tabs.
- Containers decorate their cgroup node with a display name; no duplicate row.
- Hot (CPU or IO) and blocked (D state) processes appear under their owning
  cgroup automatically, capped per cgroup. Expand shows all. One key flips to a
  global flat process ranking with owner columns.
- Noise levels, one key steps through: **all** / **middle** (default: hides rows
  with no processes, no configured limit or protection, and no activity) /
  **hot only**. The status line counts hidden rows.
- Unset config renders as a dim `·`, never `0B`. Unavailable data looks different
  from zero. Numeric cells are coloured by threshold band.

### 3.2 Width

A single view, planned for a maximum of 260 columns. **Never clip the right side.**
Column order and priority both come from configuration. On resize, the highest
priority number disappears first and the remaining columns keep their order. When
the side pane is shown it takes its width first, and dropping applies to the rest.

### 3.3 Columns

Shipped order, with width and priority (1 is kept longest); all configuration:

| Group | Columns |
|---|---|
| Identity | NAME 30 p1 |
| CPU | CPU 5 p1, THR 4 p3, CPU.W 5 p5, QUOTA 6 p6 |
| Memory | RAM/CEIL 12 p1, MIN 6 p5, LOW 6 p3, LOW.EFF 6 p4, HIGH 6 p6, MAX 6 p6, SWAP 6 p4, SWAP.MAX 6 p7, ZSW 6 p3, RATIO 5 p6, ANON 6 p6, FILE 6 p6 |
| Refaults | RF_D 6 p2, RF_Z 6 p5, RF_F 6 p4 |
| Pressure | PSI_C 5 p3, PSI_M 5 p2, PSI_I 5 p2 |
| IO | IO_R 7 p2, IO_W 7 p2, IOPS_R 6 p4, IOPS_W 6 p4, STALL 5 p3, IO.MAX 9 p6, IOCOST 6 p7 |
| Stall and tasks | BLK 4 p1, MAJ 5 p4, PIDS 5 p5 |
| Network | NET_RX 7 p5, NET_TX 7 p5 |
| Provenance | ORIGIN 7 p7, DRIFT 5 p2 |

Deep DAMON, per-device and interface detail lives in the side pane and drill-down.

### 3.4 Config: the differentiator

Shown in the row where it fits and always in the side pane. For each knob: the
configured value, the effective value, and where it came from.

- **Memory:** `memory.min`, `low`, `high`, `max`, `swap.max`, `zswap.max`.
  Effective `min`/`low` follow the kernel's `effective_protection()`: ancestor
  clamp, sibling oversubscription scaling, and unclaimed distribution under
  `memory_recursiveprot`, read from the cgroup2 mount options. Protection is
  usage-dependent, so a value that cannot be exactly derived is labelled as an
  upper bound, never `exact`.
- **CPU:** `cpu.weight` with share among siblings, `cpu.max`, `cpu.idle`.
- **IO:** `io.max` per device, `io.weight`, `io.cost` QoS and model, vrate.
- **Warnings:** a parent with no protection under a protected child, limits that
  cannot take effect, drift from systemd intent.

### 3.5 Time

Freeze (`space`) and step (`,` `.`) work everywhere. Scrubbing reads the same
tiers as live, so live and history share one UI. A shared time axis applies to
all sparklines. Gaps and resets are drawn, never interpolated.

### 3.6 Actions

The TUI ships read-only first. Actions come later as a control submenu on a process
or container: allowed and gated rather than refused by default (D-033). This needs
more design before any work starts.

## 4. Metrics and honest sources

| Question | Source | Label |
|---|---|---|
| Per-CPU us/sy/io/st/irq/si | `/proc/stat` | exact |
| Run queue, blocked count, context switches | `/proc/stat` | exact |
| Blocked processes | `/proc/PID/stat` state D, `wchan` | exact state; never a claimed root cause |
| Device latency | `/proc/diskstats` await | exact |
| Cgroup IO stall | `io.pressure` | exact |
| Device overload | `io.cost` vrate from root `io.stat` | exact where enabled, typed unavailable otherwise |
| Cgroup IO share | `cost.usage` from `io.stat` | exact where enabled |
| Process run-queue delay | `/proc/PID/schedstat` | exact |
| Process IO delay | `delayacct_blkio_ticks` | needs `kernel.task_delayacct=1`; the UI hints when off |
| Per-cgroup IO latency | eBPF on block tracepoints (parallel track) | absent until enabled; not provided by the kernel otherwise |
| IO totals | `io.stat`, bottom-level devices only | stacked `dm`/`md` lines are excluded to avoid double counting |
| KSM | `/sys/kernel/mm/ksm` | exact |
| zswap | debugfs, then `/proc/meminfo` fallback | labelled by source |

Host config is taken as given; `topos` adapts to what it finds. `io.cost` and
`io.latency` are not assumed: on 2026-11-05 `io.cost` was on with vrate at its
floor on the controller, off on r1002 and v1001, and `io.latency` was absent on
all three. `kernel.task_delayacct` is off on all three. The install wizard may
offer to enable it; that is the operator's choice.

## 5. Architecture

Layers split by activity:

| Layer | Runs | Language | Job |
|---|---|---|---|
| Sampler | 24/7 on the host | Rust (PoC gate) | Reads, rates, tiers, hard events, socket; no process spawns on the hot path |
| Slow enrichment | Sampler, slower cadence | Rust | Docker identity, systemd origin with TTL or batching; cached |
| Query, findings, report | On demand | Python | Interpretive findings at read time, windows, compare, squeeze, actions |
| TUI | Only while open | Python/Textual line widget, **OPEN** vs ratatui PoC | Paints visible rows only |
| MCP | On demand | Python | Same query core |

One collector: `topos --once` calls the sampler, so Python and Rust cannot drift.
Python keeps a fixture-only reader for tests until parity. The web UI is parked:
the read API stays clean, with no web work until the TUI is right.

**Hard events** are recorded by the sampler with exact timestamps so they survive
rollups: OOM kill, cgroup vanished, limit changed, PSI threshold crossed.

### 5.1 History

All values are settings with no hard-coded defaults; these are the shipped ones.

| Tier | Resolution | Span | Where | Stored |
|---|---|---|---|---|
| Hot | 1 s | 60 s | tmpfs | samples |
| Warm | 5 s | 5 min | tmpfs | avg, max, min, p95; deltas |
| Cold | 1 min | 24 h | disk, zstd | avg, max, min, p95; deltas |

- Tmpfs avoids disk writes and survives a daemon restart but not a reboot. It is
  still charged to the daemon's cgroup, so it counts against the memory envelope.
- Disk cap defaults to 1 GiB, oldest data points deleted first.
- PSI keeps total stall time so a short stall survives every rollup.

### 5.2 Self-protection

Host service in its own slice, not a privileged container:
`MemoryMin=48M`, `MemoryHigh=96M`, `MemoryMax=128M`, `MemorySwapMax=0`,
`CPUQuota=25%`, low `CPUWeight`, `IOWeight=20`, `TasksMax=32`. The daemon reports
its own throttling and gaps, so collection problems are visible rather than
reading as a quiet host. Collected data is limited to a bounded metric set plus a
bounded process union (top 20 CPU, top 20 IO, blocked, pinned; hard cap 64).

### 5.3 Reaching a host

- **Daemon per host** is the normal mode: the TUI attaches and backfills history.
- **Cockpit:** `mdt-cockpit` bind-mounts `/run/topos/observe` into the container, and
  zero-argument `topos` finds it. Inside a container `topos` otherwise sees only
  its own cgroup namespace and must say so rather than show a one-row tree.
- **Remote:** later.

## 6. Drawing

**OPEN**, decided after the side-by-side PoC (`tools/poc/terminal_graphics_poc.py`).
Text drawing (block, eighth-block, braille, half-block) is the required baseline
because PuTTY, tmux and GNOME Terminal must work. Sixel, iTerm and kitty images
can only be optional extras on the history screen.

## 7. Safety

Read-only by default. Every mutating path goes through root, typed confirmation
and a durable audit, and refuses owner-managed or protected targets. Details in
`docs/LIFECYCLE-ADAPTERS.md`, `docs/OPERATIONS.md` and `CONTRACTS.md`.

## 8. Acceptance

`docs/OPERATOR-QUESTIONS.md` is the scenario oracle. Performance gates are CPU per
frame and per tick, RSS and bytes written, measured on real hosts and recorded in
`MEASUREMENTS.md`. Functional verdicts must not depend on host load. Kernel
semantics are also tested on real kernels in VM lanes (`TP-VMLANE`), not only
against fixtures written by the same authors as the parsers.
