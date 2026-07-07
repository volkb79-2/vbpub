# TUI-SPEC — `gts-top` (placeholder name): a cgroup/container governance TUI

Status: **SPEC ONLY** — nothing implemented. Authored 2026-07-07 for hand-off to a
developer who has not seen the design discussion. Every section is meant to stand on
its own; where a decision depends on this host's specific setup, the concrete file
paths and live values are given so the spec is checkable against reality.

Project name **`gts-top` is a placeholder.** The real name is chosen separately —
see Appendix A. Do not invent a name while implementing; use `gts-top` as the
package/binary name until told otherwise.

Companion docs in this repo (read for the mechanism reasoning this spec assumes):
`CGROUP-MONITORING.md` (every cgroup-v2 metric explained against live data),
`MEASUREMENTS.md` (SLOs and measurement procedures this tool's thresholds encode),
`MEMORY-ARCHITECTURE.md` (zswap/sysctl/cgroup reasoning, §5.0b knob-ownership rule),
`plan-host-resource-governance.md` (tiering policy, Findings A–D), and
`files/usr/local/sbin/soulmask-zswap-monitor.py` (the single-purpose predecessor this
tool generalizes — read it before writing the collector; several formulas below are
lifted verbatim from it).

---

## 0. TL;DR

The host runs one protected game server, one interactive devcontainer, and ~18
best-effort containers, plus systemd services and slices with no container at all
(the pak tmpfs ramdisk lives in a bare slice, `soulmask-paks.slice`). Today,
observability is one narrow script per concern: `soulmask-zswap-monitor.py` (game +
pak only), `htop` (per-process, no cgroup/zswap/PSI awareness), `docker stats`
(container-only, no tree, no zswap split), and manual `damo`/`vmtouch` runs for
DAMON hot/warm/cold work. Nobody has a single view of "what is every cgroup on this
box doing right now, including the ones with no container in them, and is any of my
governance config actually the config that's really applied."

`gts-top` is that single view: a per-cgroup + per-container TUI that generalizes the
zswap-split math (`rf_z`/`rf_d`/`rf_f`) proven in the Soulmask monitor to every
cgroup on the host, adds CPU/PSI/IO/net/limits columns from a systematic sweep of
cgroup-v2 + `/proc`, shows the *entire* cgroup tree (not just containers) so
container-less slices are visible, detects when a limit's live value has drifted
from what systemd or an admin last set it to (the class of bug that bit this host in
Finding D), and integrates DAMON for hot/warm/cold working-set classification —
passively ingesting any session already running, and optionally starting one from
the UI. No prior tool (see §2) combines all four of: zswap compression-ratio
splitting, full non-container cgroup tree visibility, governance-origin drift
detection, and DAMON integration.

---

## 1. Motivation

Concretely, the operator cannot currently answer these questions with one tool and
one glance:

1. **"Is any cgroup's memory.min/high actually the value I set, or did a
   `daemon-reload` silently wipe it?"** (Finding D, `plan-host-resource-governance.md`
   §1.5: `apt install systemd-oomd` triggered a daemon-reload that reset a
   raw-written scope's `memory.min/low/high/writeback/cpu.weight` back to docker
   defaults, ~45 minutes after the watcher had applied and verified them.) No
   existing tool on this host — or, per the prior-art research in §2, any tool found
   — shows a limit's *origin* (unit file vs. `set-property` drop-in vs. unmanaged raw
   write) next to its live value.
2. **"Which cgroup is refaulting from real disk right now, not just from zswap?"**
   `soulmask-zswap-monitor.py` answers this for exactly one game container. Every
   other cgroup on the host (devcontainer, 18 best-effort containers, the pak slice,
   system services) has the identical `workingset_refault_anon`/`zswpin` split
   available and nobody is watching it.
3. **"What is running in `soulmask-paks.slice` and is it protected?"** — a slice with
   **no docker container in it at all** (§1.4/§4 of the plan: the pak tmpfs `cp` runs
   as root, charged to the slice, not to any container). `docker stats`/`ctop`/
   `lazydocker` cannot show this row because there is no container to show. Any tool
   whose primary key is "container" is structurally blind to this and to plain
   systemd services and to processes an admin manually placed into a slice with
   `systemctl set-property`.
4. **"Is the pak's working set 150 MB or 1.7 GB right now, and is it shrinking under
   pressure?"** — currently answered by manually running `vmtouch -v` every 10
   minutes for a play session (`MEASUREMENTS.md` M2). DAMON's paddr/vaddr region
   classification (hot/warm/cold, age-thresholded) is the kernel-native way to answer
   this continuously, and the `damon-analysis` toolkit already has working
   classification code (`analyze_process.py`'s `Classifier`) — nobody has wired it
   into a live dashboard column.
5. **"What did the host look like ten minutes ago, across all these cgroups, when
   the game's login stalled?"** No history exists beyond scrollback: the current
   monitor prints a table you can `tee` to a file, but there's no time-series ring
   buffer, no sparkline, and no way to overlay `rf_d/s` for two containers on one
   chart to compare.

`gts-top` is scoped to solve exactly these five things for one operator on one host,
without adding a Prometheus/Grafana/cAdvisor stack (rejected in the plan's §6 for the
same RAM-budget reason this spec inherits: the host is memory-constrained, and a
~15–50 MB Python TUI process costs far less than a monitoring stack).

---

## 2. Prior art

### 2.1 DAMON userland tooling and existing DAMON TUIs

*(Prior-art research in progress at the time of writing — see the note this section
carries once the research agent returns; if the note below is still generic, the
implementer should re-run the same research pass before finalizing DAMON UI design.)*

**`damo`** (the official DAMON userspace tool, this repo's `../damon-analysis/venv`
vendors it) provides `damo start/stop/tune/record/report`. Report formats include
`access` (text snapshot, used by our own `damon_cli.py monitor-pid` — clears the
screen and reprints `damo report access` every 5s: this *is* a crude "DAMON TUI" but
it is a fixed-refresh dump with no interactivity, no sorting, no history, and no
notion of "container" at all), `heatmap` (renders a PNG, not a terminal artifact),
`wss` (working-set-size distribution over time), `damon` (scheme stats), `trace`
(live tracepoints), and `footprints`/`profile`. None of these are an interactive
TUI in the htop sense — no keybindings, no live table with sort/filter, no
drill-down. `damo`'s own `monitor-pid`-style dashboard (as reimplemented in
`damon_cli.py::cmd_monitor_pid`) is the closest thing to a "DAMON TUI" in this
toolkit and it is exactly what `gts-top`'s DAMON columns/detail-page generalize:
same sysfs tree, same `Classifier` thresholds, wrapped in a real TUI with
sort/filter/history instead of a 5-second `clear && print` loop.

DAMON's sysfs interface (`/sys/kernel/mm/damon/admin/kdamonds/`) natively supports
**multiple concurrent kdamond contexts** (`DAMON-GUIDE.md` §14: `nr_kdamonds`,
each with its own context/targets/schemes) — this is the architectural fact that
makes "DAMON columns for several monitored containers at once" feasible without a
custom multiplexer; `gts-top`'s control stage (§3.6c) just needs to track which
kdamond indices it allocated, exactly as `damon_analysis.py::Monitor(kdamond_idx=N)`
already does in this repo.

One conflict to design around: `CONFIG_DAMON_STAT_ENABLED_DEFAULT=y` kernels run a
`damon_stat` module at boot that occupies a kdamond slot; `damon_cli.py` disables it
before starting a manual session and restores it on exit (`disable_damon_stat()` /
atexit). `gts-top` must do the same (§3.6c) — and must NOT do it silently in the
*passive* stage, only when the operator explicitly starts a control-stage recording.

### 2.2 Meta's `below`

`below` (`facebookincubator/below`) is the closest prior art overall: a Rust TUI that
records cgroup2 + process + system stats to a local store and lets you scrub back
through time in the same UI used for live viewing (`below record` as a background
daemon writing a rolling store, `below replay --time "..."` / arrow-key time
navigation in the live view). Relevant design points this spec borrows or
deliberately diverges from (to be corroborated/expanded with citations once the
research pass completes):
- **Borrow:** the record daemon and the live viewer share one data model, so
  replay is "the same UI, different data source" rather than a second code path —
  this is exactly `gts-top`'s collector/model/UI split (§6.1) and its
  `--record`/`--replay` requirement (§3.8).
- **Borrow:** cgroup-tree-first navigation (not container-first) with per-cgroup
  CPU/memory/IO panels — validates this spec's tree-view-is-the-primary-model
  decision (§3.1).
- **Gap `below` does not fill (this tool's unique angle):** no zswap
  compression-ratio split (`below` predates or does not focus on zswap-heavy
  hosts), no DAMON integration, and — per this host's Finding D — no notion of
  "who owns this limit and did it drift" (systemd unit vs. drop-in vs. raw write).
  `below` also has no first-class Docker-metadata join (it is cgroup-native, not
  container-native, which is the right primary key, but it doesn't add the
  image/container-name/UUID enrichment this host's operator needs for the Wings
  containers).

### 2.3 atop / ctop / dtop / lazydocker / glances

- **atop**: C, curses. Per-process AND per-cgroup-ish resource accounting with
  color-coded "critical resource" highlighting, and — most relevant — **binary log
  record/replay** (`atop -w file`, then `atop -r file` scrubs through it,
  `atopsar` reports on it). **Borrow:** the record/replay UX pattern (same
  interactive UI reading a live source or a log file) predates `below` and is a
  second confirmation this is the right shape for `gts-top --replay`.
- **ctop**: Go, per-container mini dashboards with inline sparkline graphs (CPU/mem)
  in the list view and a single-container detail toggle. **Borrow:** sparklines
  embedded directly in table cells (this spec's §3.5 requirement) and the
  list→single-entity-detail toggle pattern (this spec's Enter-for-drill-down, §3.4).
  ctop has no cgroup-tree view (container-only) and no zswap/PSI/DAMON awareness.
- **dtop**: name is ambiguous (several small/abandoned projects share it; none
  found to be an established cgroup/Docker monitoring tool comparable to the
  others here) — flagged for the implementer to re-verify in the full research
  pass rather than assumed.
- **lazydocker**: Go + `gocui`, multi-pane keybinding-driven layout (containers /
  images / volumes / logs panes), with **gated destructive actions** requiring an
  explicit keypress and showing what will happen. **Borrow directly:** this is the
  template for `gts-top`'s v2 `--admin`-gated actions (§4) — show the exact command,
  require confirmation, never default to destructive.
- **glances**: Python (psutil-based), curses/Textual-ish, plugin architecture with
  history and export backends (InfluxDB, Prometheus, CSV, …). **Borrow:** the
  plugin/exporter idea validates keeping the collector cleanly separable from the
  UI (§6.1) so a future exporter could reuse it without touching the TUI.

### 2.4 Textual (UI framework choice)

Textual (Textualize) is an actively maintained Python TUI framework (CSS-like
styling, reactive data binding, async event loop, built-in `DataTable` widget with
sortable columns and cell-level styling, and a widget ecosystem including
sparkline/plot widgets) — it directly supports the two hardest UI requirements here:
a `DataTable` that re-renders efficiently on a 5-second tick without a full-screen
redraw, and compositing a full-screen detail "screen" (Textual's `Screen` stack) for
the Enter-drill-down requirement. This justifies it as the v1 UI framework; a Go/Rust
port (§6.4) would use an equivalent (Bubble Tea + Bubbles table, or ratatui) against
the same collector data model.

### 2.5 Synthesis — what to borrow, what nobody has

| Source | What `gts-top` borrows |
|---|---|
| `damo`/`damon_cli.py` | sysfs protocol, `Classifier` hot/warm/cold thresholds, multi-kdamond pattern, damon_stat-conflict handling |
| `below` | collector/model/UI split enabling record+replay as one codebase, cgroup-tree-first model |
| `atop` | record/replay UX precedent, critical-resource highlighting |
| `ctop` | in-cell sparklines, list→detail toggle |
| `lazydocker` | gated destructive-action pattern (show command, confirm) for v2 |
| `glances` | plugin/exporter separation validating the collector boundary |

**What nobody combines (this tool's angle):** the zswap compression-ratio /
disk-vs-zswap refault split (`rf_z`/`rf_d`/`rf_f`) generalized to every cgroup, full
non-container cgroup-tree visibility (bare slices, manually-placed processes),
governance-origin drift detection (unit file / drop-in / raw write, catching
Finding-D-class wipes), and DAMON hot/warm/cold integration — in one tool, over one
cgroup-v2 data model.

> **Implementer note:** the paragraph-level detail above was compiled from this
> repo's own toolkit (`damon-analysis/`) plus general knowledge of `below`/`atop`/
> `ctop`/`lazydocker`/`glances`/Textual. Before finalizing the DAMON UI and the v2
> action-gating UX, re-run a web research pass (search: "DAMON TUI", "damo monitor",
> "facebookincubator/below architecture", "ctop sparkline", "lazydocker confirm
> action") and replace this note with direct citations (repo URLs, doc anchors).

---

## 3. Functional specification — v1

### 3.1 Row model: container view and tree view (hot-toggleable)

Two views over the **same underlying entity list**, toggled with a single hotkey
(`t`, htop's tree-toggle convention, §8):

**(a) Container-centric view.** One row per running Docker/Podman container. This
is a *filtered projection* of the tree view: only cgroup rows that have a docker
JOIN (below) are shown, flattened (no parent slices). This is the "docker stats,
but with everything else" view.

**(b) Full cgroup-tree view.** One row per cgroup directory under
`/sys/fs/cgroup/` (bounded depth, default unlimited — a 16 GB single-host cgroup
tree is a few dozen to low-hundreds of nodes, not a scaling concern), rendered as
an expand/collapse tree (`+`/`-` or Enter on a branch node toggles; Enter on a leaf
opens the drill-down, §3.4). This view is the only one that shows:
- **Slices with no container**, e.g. `soulmask.slice/soulmask-paks.slice` — the
  pak tmpfs pages live here, charged to a `cp` that ran as root, with zero docker
  metadata to join.
- **Plain systemd services** (`docker.service`, `wings.service`, `sshd.service`,
  anything under `system.slice`).
- **Processes an admin manually placed in a slice** via `systemctl set-property` or
  by writing a PID into `cgroup.procs` directly — these show as a leaf cgroup with
  a process list (drill-down, §3.4) and no docker JOIN.
- Aggregate rows for branch nodes (`soulmask.slice`, `besteffort.slice`, …), whose
  numeric columns are **sums of additive children metrics** (RAM, anon, io rates,
  net rates, pids.current) but whose **limit and PSI columns are the branch
  cgroup's own values**, never a sum (PSI/pressure/limits are not additive across
  children — summing them would be meaningless and is a common monitoring-tool
  bug this spec explicitly avoids).

**Discovery algorithm (collector, §6):**
1. Walk `/sys/fs/cgroup/` recursively; every directory containing a
   `cgroup.controllers` file is a node. Record parent/child relationships from the
   filesystem hierarchy directly (no separate index needed).
2. For each running container (`docker ps -q`, or the configured runtime), resolve
   `docker inspect -f '{{.State.Pid}}' <cid>` → read `/proc/<pid>/cgroup`'s
   `0::<path>` line → the absolute cgroup path. This is the exact resolution
   `soulmask-zswap-monitor.py::container_cgroup_path()` and
   `setup-cgroups.sh` already use — reuse it verbatim, don't reinvent it.
3. JOIN: attach `{container_id, name, image, labels, state}` from `docker inspect`
   onto the tree node whose path matched in step 2. A node with no match keeps
   `docker: null` and is still a first-class row (this is what makes non-container
   slices visible — see the motivation in §1.3).
4. Container **name** is read directly (Wings names Soulmask containers by server
   UUID per `soulmask-zswap-monitor.py`'s own docstring — no extra convention
   needed); if a `pterodactyl.io`-style label is present on the container it is
   surfaced too, but name is the primary UUID source and must not be assumed absent
   just because the label is.

### 3.2 Columns

Column set = the existing monitor's proven set, generalized from "game + pak" to
"every entity", plus the operator-mandated additions, plus a systematic-sweep
proposal set. Every column has an explicit **source** (see the full table in §5)
and a **priority tier** used for adaptive width (§3.3).

**Core set (generalized from `soulmask-zswap-monitor.py`) — tier T1:**

| Column | Meaning | Formula |
|---|---|---|
| `RAM` | physical RAM in this cgroup (incl. its own zswap pool) | `memory.current` |
| `anon` | resident anonymous pages | `memory.stat:anon` |
| `file` | resident file-backed pages (incl. shmem) | `memory.stat:file` |
| `z_pool` | compressed bytes in zswap | `memory.zswap.current` |
| `z_eq` | uncompressed-equivalent bytes in zswap | `memory.stat:zswapped` |
| `ratio` | compression ratio | `z_eq / z_pool` |
| `swap_disk` | bytes actually on real disk swap (generalized per-entity, not just pak) | `memory.swap.current − zswapped − swapcached`, clamp ≥0 (identical formula to the existing monitor's pak `p_disk`, now applied to every entity, not only the pak slice) |
| `rf_z/s` | zswap refault rate (µs-scale, healthy) | `Δ memory.stat:zswpin / Δt` |
| `rf_d/s` | disk refault rate (ms-scale, THE lag predictor) | `max(0, Δ workingset_refault_anon − Δ zswpin) / Δt` |
| `rf_f/s` | file-cache refault rate (always a disk read) | `Δ memory.stat:workingset_refault_file / Δt` |

**Operator-mandated additions — tier T1/T2:**

| Column | Meaning | Formula |
|---|---|---|
| `CPU%` | CPU consumption | `Δ cpu.stat:usage_usec / Δt / 1e6 × 100` |
| `PSI(mem)` | memory pressure, some/full | `memory.pressure` `some`/`full` `avg10` |
| `PSI(io)` | io pressure, some/full | `io.pressure` `some`/`full` `avg10` |
| `PSI(cpu)` | cpu pressure, some/full | `cpu.pressure` `some`/`full` `avg10` (kernel 5.13+; `full` may be absent on older kernels — degrade to `some` only) |
| `io_r`, `io_w` | disk IO rate, MB/s **and** IOPS, per backing device | `Δ io.stat[maj:min]:{rbytes,wbytes,rios,wios} / Δt` |
| `net_tx`, `net_rx` | network rate, bytes/s **and** packets/s | see §3.2 network caveat below |
| `mem_min/low/high/max` | memory limits | `memory.min/low/high/max` |
| `io_max` | per-device IO cap | `io.max` |
| `cpu.weight` | CPU proportional weight | `cpu.weight` (+ `cpu.max` quota/period) |
| `headroom%` | how close to the ceiling | `memory.current / memory.max` and `/ memory.high` (both shown; `max` takes priority when both are near) |
| `pids.current` | process count in the cgroup | `pids.current` (+ `.max`, `.peak` in drill-down) |

**Network caveat (must be documented, not hidden):** cgroup v2 has **no native
per-cgroup network-accounting controller** (net_cls/net_prio are cgroup v1 legacy
and not mounted here). Per-entity network rates are read from
`/proc/<pid>/net/dev` for a representative PID in the cgroup, deduplicated by
network-namespace inode (`/proc/<pid>/ns/net`) so containers sharing a netns are
not double-counted, and are only meaningful when the entity has its **own** network
namespace (true for ordinary `docker run` containers; false for `--net=host`
containers and for bare slices/services sharing the host netns). When no private
netns is detected, the row shows `net_tx`/`net_rx` = `n/a (host netns)` rather than
a misleading host-wide number. This is a fundamental cgroup-v2 limitation, not a
missing feature — document it in the glossary (§3.10) so operators don't file a bug
against it.

**Systematic-sweep proposed columns (memory.stat/cpu.stat/memory.events/io.stat/
pids — tier T4 / `--wide` / extended-profile only, per §3.3):**

| Column | Meaning | Source |
|---|---|---|
| `pgscan/s`, `pgsteal/s` | reclaim activity rate (scan vs. actually-evicted) | `Δ memory.stat:pgscan`, `Δ memory.stat:pgsteal` |
| `restore/s` | pages that refaulted mid-eviction (raced, never fully left RAM) | `Δ memory.stat:workingset_restore_anon` |
| `thp` | transparent-hugepage bytes (file + anon) | `memory.stat:file_thp + anon_thp` (drill-down detail, not a sparkline column) |
| `sock` | socket-buffer kernel memory charged to this cgroup | `memory.stat:sock` |
| `kstack`, `slab` | kernel-stack and slab-cache memory | `memory.stat:kernel_stack`, `slab_reclaimable + slab_unreclaimable` |
| `throttled%` | fraction of wall time this cgroup's CPU was throttled by `cpu.max` | `Δ cpu.stat:throttled_usec / Δt`; `nr_throttled` rate shown in drill-down |
| `mem.evt` | memory.events counters — **oom_kill is a hard alert regardless of column visibility** | `memory.events:{low,high,max,oom,oom_kill}` deltas |
| `io_discard` | discard (TRIM) bandwidth | `Δ io.stat[dev]:dbytes / Δt` |
| `pids.evt` | fork-refused-due-to-limit rate | `Δ pids.events:max` |

Every proposed column above degrades gracefully (§6.3) when its source file is
absent on a given kernel/cgroup (older kernel missing a `memory.stat` field, a
non-leaf cgroup with no `pids` controller enabled, etc.) — show `—`, never crash,
never silently zero-fill (zero and "unavailable" must be visually distinct).

**DAMON columns** (present only for entities with an active DAMON target — see
§3.6b): `hot%`, `warm%`, `cold%`, `idle%` — the classified working-set split for
that entity's monitored PID(s), refreshed on each DAMON aggregation interval (not
necessarily every table tick — DAMON's own `aggr_us` governs update cadence,
typically 1–2s, decoupled from the table's 5s default).

### 3.3 Adaptive width — column priority order

Every column has a priority tier `T0`–`T4` (lower = more essential = shown at
narrower terminal widths) and a minimum rendered width in characters. The UI picks
the highest tier that fits the current terminal width, always including all lower
tiers. Recompute on every terminal resize (Textual delivers resize events natively).

| Tier | Min. terminal width | Columns added |
|---|---|---|
| T0 | 80 cols (always shown) | `name`/tree-glyph (20), `RAM` (6), `rf_d/s` (7), `PSI(mem) full` (6), `CPU%` (5) |
| T1 | 100 cols | + `anon` (6), `z_pool` (6), `z_eq` (6), `ratio` (5), `rf_z/s` (7), `rf_f/s` (7) |
| T2 | 120 cols | + `swap_disk` (7), `headroom%` (6), `tier` (11), `pids.current` (5) |
| T3 | 160 cols | + `io_r/io_w MB/s+IOPS` (11+9), `net_tx/net_rx` (11) |
| T4 | 200 cols, or `--wide`, or `w` hotkey | + `file` (6), `PSI(io)`/`PSI(cpu)` (12), limits summary (10), DAMON `hot/warm/cold%` (14) when active |

Column **profiles** in config (§3.7, `[columns]`) let an operator define a named
custom column list (e.g. a "pak-focused" profile showing only memory+DAMON columns)
independent of the tier system; profiles override tiering when selected explicitly,
tiering is the *default* ("auto") profile's behavior.

### 3.4 Drill-down: full-screen detail page

`Enter` on any row (container or bare cgroup, tree or container view) pushes a
full-screen Textual `Screen` for that one entity. Contents:

1. **Header:** entity name, cgroup path, tier, docker metadata if joined
   (container id/name/image/state; the Wings UUID *is* the container name per
   §3.1 point 4), DAMON state (inactive / passive-detected external session /
   TUI-controlled session, with kdamond index).
2. **Complete `memory.stat`** rendered as a table, every field the kernel exposes
   on this host (not just the columns from §3.2), each row annotated with a
   one-line explanation drawn from a static glossary the tool ships (content
   equivalent to `CGROUP-MONITORING.md` §4's per-field explanations — this is
   static metadata compiled into the tool, not fetched from the doc at runtime).
   Grouped exactly as `CGROUP-MONITORING.md` groups them: current page inventory /
   workingset tracking / page reclaim / fault counters / swap flow counters / THP
   counters.
3. **Per-device IO:** one row per backing block device from `io.stat`
   (`rbytes/wbytes/rios/wios/dbytes/dios`), plus that device's `io.max` and
   `io.weight`/`io.bfq.weight` (with the graceful-degradation note if BFQ isn't
   the active scheduler, §6.3).
4. **All limits, WITH ORIGIN** — see the algorithm below.
5. **PSI** — full `memory.pressure`/`io.pressure`/`cpu.pressure`, all three
   windows (`avg10`/`avg60`/`avg300`) and `total`, both `some` and `full`.
6. **Processes inside the cgroup** — `cgroup.procs` → for each PID: `comm`,
   `RSS` (`/proc/<pid>/status` `VmRSS`), CPU% (jiffies delta, same technique as
   `damon_cli.py::_read_cpu_jiffies`), sorted by RSS descending. This is what
   makes an admin-manually-placed process visible and identifiable (§1.3 point 3
   in the motivation) even with no docker metadata at all.
7. **Docker metadata** (when joined): full `docker inspect` summary — image,
   digest, created time, restart count, mounts (path only, not full detail),
   labels.
8. **Time-series charts** of selected metrics for this entity, driven by the
   ring buffer (§3.5) — the operator picks which metrics chart via a hotkey
   (`Space` to tag a metric row for charting, mirroring the row-tagging hotkey
   used for cross-entity overlays, §3.5).

**Origin-detection algorithm (limits, item 4 above)** — this is the feature that
would have caught Finding D live:
1. Split the cgroup's absolute path into systemd unit-name components (every path
   segment ending in `.slice`/`.scope`/`.service` is a unit name — no reverse
   lookup needed, this is the same trivial mapping `setup-cgroups.sh` already
   relies on).
2. For the leaf unit (and each ancestor slice, since `memory.min`/`low` are
   hierarchical — Finding A), run `systemctl show <unit> -p FragmentPath
   -p DropInPaths -p ControlGroup -p MemoryMin -p MemoryLow -p MemoryHigh
   -p MemoryMax -p CPUWeight -p IOWeight` (batch all `-p` in one call per unit;
   root required, already assumed).
3. Classify origin per attribute:
   - `FragmentPath` non-empty, `DropInPaths` empty → **"unit file"**.
   - `DropInPaths` contains a path under `/etc/systemd/system.control/` or
     `/etc/systemd/system/<unit>.d/` → **"persistent set-property"** (survives
     reboot).
   - `DropInPaths` contains a path under `/run/systemd/system.control/` or
     `/run/systemd/transient/` → **"runtime set-property (--runtime)"** (dies
     with the scope, by design for docker scopes — this is the *correct*
     Finding-D-safe mechanism `setup-cgroups.sh` now uses for docker scopes).
   - No systemd record at all for that attribute, OR systemd's recorded value
     **disagrees with the live sysfs value** → **"raw write / drifted — will be
     reverted on the next daemon-reload or unit restart"** — render this state in
     the alert color (config `[colors]`, §3.7) unconditionally, this is exactly
     the Finding-D bug class.
4. Show both the live value (from the sysfs file directly) and systemd's recorded
   value side by side when they differ; identical values render as one value with
   the origin tag.

### 3.5 History: ring buffer, sparklines, chart overlays

**Tiered ring buffer**, cost bounded by `entities × tracked_metrics × samples`:

| Tier | Resolution | Retention | Samples/series |
|---|---|---|---|
| Full | sampling interval (default 5s) | 15 min | 180 |
| Downsampled | 1-minute rollup (mean; max kept alongside for spike visibility) | 4 h | 240 |

**Tracked metrics for history** (24 numeric series per entity — a deliberate
subset of §3.2's full column set; static/limit columns like `mem_max` or
`cpu.weight` are not time-series, they're looked up fresh from the model's latest
sample): `ram, anon, file, z_pool, z_eq, swap_disk, rf_z, rf_d, rf_f, cpu_pct,
psi_mem_some, psi_mem_full, psi_io_some, psi_io_full, psi_cpu_some, io_r_bps,
io_w_bps, io_r_iops, io_w_iops, net_tx_bps, net_rx_bps, pids_current,
headroom_mem_pct, hot_pct` (last one DAMON-conditional, blank series when inactive).

**Memory budget** (the concrete estimate the operator asked for): with 40 entities
(headroom above this host's ~30 live cgroups), 24 tracked series, 420 total samples
per series (180 + 240), stored as **fixed-size `float32` arrays** (`array.array('f')`
or a small numpy ring — explicitly NOT Python lists of floats, whose per-element
object overhead would be ~7× larger):

```
40 entities × 24 metrics × 420 samples × 4 bytes/float32 ≈ 1.6 MB
```

This is why the acceptance criterion (§9) budgets generously for CPU, not memory —
the ring buffer itself is nearly free; Textual + Python's own baseline RSS (~30–40 MB)
dominates the footprint.

**Sparklines in table cells:** each row renders a compact sparkline (Textual's
sparkline widget or an equivalent Unicode-block renderer) for `rf_d/s` by default
(the single most important "is this cgroup in trouble" signal), reading the last
N points of the full-resolution ring directly — no separate storage.

**Chart overlays — multiple entities, one metric, one diagram:** `Space` tags/
untags the current row for overlay; a chart hotkey (`c`, §8) opens a full chart
screen plotting the **same metric** (operator-selected, e.g. `rf_d/s`) for every
tagged entity as separate lines/series in one plot — this is the concrete
"rf_d/s of game1 vs game2 vs devcontainer" requirement. Untagging/re-tagging while
the chart screen is open updates it live.

### 3.6 DAMON integration — all three stages, in v1 scope

**(a) Passive — detect and ingest an already-running session.**
On each collector sweep (independent of the table's 5s tick — DAMON state changes
are polled at the same cadence for simplicity in v1), read
`/sys/kernel/mm/damon/admin/kdamonds/nr_kdamonds`; for each existing kdamond index,
read `state`/`pid`, and for each of its contexts/targets, read `targets/N/pid_target`
and the `tried_regions/` snapshot (after issuing `update_schemes_tried_regions` via
the `state` file — same mechanism `damon_analysis.py::Monitor.collect()` already
uses). Map each `pid_target` back to a cgroup (via `/proc/<pid>/cgroup`) to attach
the resulting hot/warm/cold split to the right tree row. **Never write to, stop, or
otherwise mutate a kdamond the tool did not itself create** — passive means
read-only, full stop; this is what lets an operator's own manual `damo`/`damon_cli.py`
session coexist safely with the TUI running at the same time. If `damon_stat` is
enabled (occupying the DAMON mechanism at boot per `DAMON-GUIDE.md` §5.3), show a
banner noting hot/warm/cold columns and control-stage recording are unavailable
until it's disabled — but do **not** disable it automatically in the passive stage
(that's a mutation, reserved for the explicit control-stage action below).

**(b) Columns — hot/warm/cold split per monitored target, configurable ages.**
For any entity with an active target (from (a) or (c)), compute the classified
split using the same `Classifier` logic as `analyze_process.py`/`damon_cli.py`:
`hot` ≥ `hot_rate`% access frequency (default 50%), `warm` ≥ `warm_rate`% (default
5%), `cold` ≥ `cold_age` seconds since last access (default 30s), `idle` ≥
`idle_age` seconds (default 120s) — all four thresholds configurable per-profile in
`[damon]` config (§3.7), matching `analyze_process.py`'s own CLI defaults exactly
so operators moving between `damon_cli.py` and `gts-top` get identical
classification. Columns refresh on the DAMON aggregation cadence (`aggr_us`,
typically 1–2s), independent of the table's own sampling interval.

**(c) Control — start/stop a DAMON recording on a selected container's PID(s) from
the TUI.** Root required (already assumed globally). Flow:
1. Operator selects a row (container or bare cgroup with processes), presses the
   DAMON-start hotkey (`d`, held or a two-key sequence to avoid accidental
   activation — see §8).
2. TUI reads `cgroup.procs` for the entity, picks the largest-RSS PID as the
   default target (mirrors `damon_cli.py::cmd_timeseries_container`'s "largest
   process in container" heuristic) — with an option to add specific PIDs from
   the process list shown in the drill-down (§3.4 item 6).
3. **Vaddr operations set** is used (not paddr) because the requirement is
   per-process/per-container attribution; the tool documents the known
   `vaddr` overstatement caveat from `DAMON-GUIDE.md` §12.1 (85–95% of reported
   bytes are unmapped virtual-address gaps) directly in the DAMON detail panel —
   consistent with `plan-host-resource-governance.md` M1's own caveat ("DAMON
   vaddr overstates (mmap) — use as *shape*, calibrate absolute via mempress
   stepping").
4. If `damon_stat` is enabled, the confirmation dialog explicitly says it will be
   disabled to free the kdamond mechanism, and the tool restores it on stop
   (matching `damon_cli.py`'s disable/restore pattern) — including on crash
   (`atexit`), and on ordinary TUI exit while a control-stage session is active.
5. Allocate the next free `kdamond_idx` (never assume index 0 is free — read
   `nr_kdamonds` and each existing kdamond's `state` first, exactly as the passive
   stage does, so a TUI-started session can never collide with an operator's own
   manual one).
6. **No shell-out to `damo` and no dependency on `../damon-analysis`'s venv.**
   `gts-top` vendors its own minimal sysfs writer (a slimmed reimplementation of
   `damon_analysis.py::SysfsInterface`'s create/configure/start/stop calls) so the
   pipx package has no dependency on a host-specific external toolkit. Passive
   reading (stage a) already requires no such dependency since it only reads the
   same sysfs tree regardless of who created the session.
7. Concurrent control-stage targets are capped (`[damon] max_concurrent_targets`,
   default 4) to bound kdamond kernel-thread and sampling overhead — architecturally
   more are possible (§14 of `DAMON-GUIDE.md`), the cap is a deliberate v1 safety
   default, not a hard architectural limit.
8. Stopping (same hotkey, toggle) tears down only the kdamond(s) this session
   allocated, restores `damon_stat` if the tool disabled it, and leaves any
   externally-detected session untouched.

DAMON snapshots captured while control-stage-active are recorded into the
`--record` stream identically to every other sample (§3.8) — a replay of a
recording that included a DAMON session shows the same hot/warm/cold columns
during scrub as it did live.

### 3.7 Config: TOML file

One file, XDG-conventional location (`$XDG_CONFIG_HOME/gts-top/config.toml`,
falling back to `~/.config/gts-top/config.toml`; `--config <path>` override). Full
worked example in §7. Sections:

- `[general]` — sampling interval (default 5.0s), whether to require root (always
  true in v1, kept as a named setting for forward-compat), default view (`tree` or
  `container`), default column profile.
- `[colors]` — per-tier row coloring (prod/interactive/besteffort/unmanaged) and
  per-severity thresholds' colors (warn/critical), independent of the thresholds
  themselves.
- `[thresholds]` — SLO defaults **taken directly from `MEASUREMENTS.md`**
  (`rf_d_per_s`: warn 1, crit 20 — "game rf_d/s ≤ 20/s sustained during play";
  `psi_full_avg10`: warn 1, crit 2 — "PSI full avg10 < 2"; `psi_some_avg10`: warn 5
  — ">5% noticeable latency" per `CGROUP-MONITORING.md` §6), overridable **per
  tier** (prod tier strict, besteffort tier lenient — a besteffort container
  hitting `rf_d/s=50` is not news, the same number on the game is a page-worthy
  event).
- `[columns]` — named profiles (`default`, `wide`, `minimal`, custom lists), and
  the tier-priority table from §3.3 (overridable, in case an operator's terminal/
  SSH setup needs different breakpoints than the shipped defaults).
- `[damon]` — `hot_rate`, `warm_rate`, `cold_age`, `idle_age` defaults (mirroring
  `analyze_process.py`'s CLI defaults exactly), `max_concurrent_targets`.
- `[history]` — full-resolution window seconds, downsample interval, downsample
  retention hours (the tiered scheme from §3.5, overridable).
- `[hotkeys]` — profile selection (`gts-top` native / `htop` / `top` / `custom`)
  and individual overrides (§3.9/§8).

### 3.8 Record & replay

`--record <path.jsonl>` runs the collector (with or without the TUI attached —
headless recording is a legitimate mode, since the collector has no UI
dependency, §6.1) and appends one JSON object per sampling tick. The schema
**extends** `soulmask-zswap-monitor.py --json`'s existing per-sample object
(same key names/units where concepts overlap — `rf_z_per_s`, `rf_d_per_s`,
`rf_f_per_s`, `ram_bytes`, etc. — so a human diffing the old and new JSON
recognizes the lineage) to a multi-entity frame:

```json
{
  "ts": "2026-07-07T14:32:05+02:00",
  "epoch": 1751895125.0,
  "interval_s": 5.0,
  "entities": [
    {
      "cgroup_path": "/system.slice/docker-b87c0a5b….scope",
      "docker": {"container_id": "b87c0a5b…", "name": "b87c0a5b-…", "image": "...", "state": "running"},
      "tier": "prod",
      "ram_bytes": 6442450944, "anon_bytes": 6029312000, "file_bytes": 204800000,
      "zpool_bytes": 1806000000, "zeq_bytes": 5742000000,
      "swap_disk_bytes": 12345678,
      "rf_z_per_s": 4.2, "rf_d_per_s": 0.0, "rf_f_per_s": 0.1,
      "cpu_pct": 38.5,
      "psi": {"mem": {"some_avg10": 0.0, "full_avg10": 0.0}, "io": {...}, "cpu": {...}},
      "io": [{"device": "254:0", "r_bps": 1200, "w_bps": 34000, "r_iops": 2, "w_iops": 8}],
      "net": {"tx_bps": null, "rx_bps": null, "reason": "host netns"},
      "limits": {"memory_min": {"value": "6442450944", "origin": "runtime set-property"}, "...": "..."},
      "pids_current": 34
    }
  ],
  "damon": [
    {"cgroup_path": "...", "kdamond_idx": 0, "source": "external|controlled",
     "hot_pct": 12.4, "warm_pct": 30.1, "cold_pct": 40.0, "idle_pct": 17.5}
  ]
}
```

After the first frame, subsequent frames MAY be delta-encoded (only entities/fields
that changed) to reduce file size for long recordings — this is an implementation
optimization, not a schema requirement; a v1 implementation writing full frames
every tick is acceptable and simpler, revisit if recording file size becomes a
problem in practice (§10).

`--replay <path.jsonl>` loads the file into the identical in-memory model used by
live mode (§6.1 — this is the entire point of the collector/model/UI split: replay
is "the model gets fed from a file iterator instead of a live sweep", the UI layer
is unmodified) and drives the same table/tree/detail/chart screens, with transport
controls: play/pause (`Space`, context-sensitive — this hotkey means "tag row" in
live mode and "play/pause" in replay mode, since the two modes are mutually
exclusive and the meaning is unambiguous from context), step forward/back one
sample (`.`/`,`), jump to a timestamp, and speed multiplier (1×/2×/4×/8×). DAMON
snapshots embedded in the recording (from a control-stage session that was active
during recording) replay identically to live DAMON columns.

### 3.9 Hotkeys and profiles

Full table in §8. Summary of the design: a **`gts-top` native default** keymap
followed by htop/top conventions where they exist (`F6`/`<`/`>` cycle sort, `t`
tree toggle, `/` filter, `Space` tag-for-overlay in live mode), plus two
**alternate profiles** (`htop`, `top`) that remap the same *actions* onto each
tool's actual muscle-memory keys, selected via `[hotkeys] profile = "htop"` in
config or the `--hotkeys` CLI flag. Profiles remap keys to actions; they never add
or remove actions — v1's action set is fixed, only the binding changes.

`k` (kill) is **explicitly reserved and unbound in v1** (`k kill NO (v2)` per the
design brief) — pressing it shows "not available in this build; requires --admin
(v2)" rather than doing nothing silently, so operators aren't left wondering if
the keypress registered.

### 3.10 Glossary (embedded in the tool's help screen, `F1`/`?`)

- **PSI (Pressure Stall Information):** percentage of wall-clock time tasks spent
  stalled waiting for a resource (memory reclaim, IO completion, CPU scheduling).
  `some` = at least one task stalled while others kept running; `full` = every
  runnable task in the cgroup was stalled simultaneously (the more severe signal).
  `avg10`/`avg60`/`avg300` are exponential moving averages over the last 10s/60s/
  5min; `total` is cumulative stall microseconds since the cgroup was created.
- **zswap vs. disk swap:** zswap compresses cold pages into a RAM-resident pool
  (`memory.zswap.current`, decompression costs ~2–5µs/page); if the pool is full
  or `memory.zswap.writeback=1`, cold zswap pages can be written through to real
  disk swap (`pswpout`/`zswpwb`, costing milliseconds/page). `rf_z/s` vs. `rf_d/s`
  is exactly this split, generalized from the original monitor.
- **DAMON hot/warm/cold:** DAMON (Data Access MONitor, in-kernel since 5.15)
  samples a process's virtual (or physical) address space at an interval,
  splitting it into regions by observed access frequency and age. "Hot" = accessed
  often and recently; "cold"/"idle" = not accessed for a configurable duration.
  Used here to answer "how much of this container's memory is actually in active
  use" without needing a hard eviction test to find out.
- **Origin (of a limit):** whether a cgroup attribute's live value came from a
  systemd unit file, a `systemctl set-property` drop-in (persistent or
  `--runtime`), or an unmanaged raw write — the last one is at risk of being
  silently reverted on the next `daemon-reload` (see §3.4's origin algorithm and
  `plan-host-resource-governance.md` Finding D).

---

## 4. Functional specification — v2 (explicitly OUT of v1 scope)

Everything below is specified now so the v1 architecture doesn't foreclose it, but
**none of it ships in v1**. All v2 actions are gated behind a global `--admin` CLI
flag (refusing to even show the action's hotkey as available otherwise) and require
an explicit confirmation dialog that **displays the exact command about to run**
before executing it — directly modeled on `lazydocker`'s gated-action pattern
(§2.3).

- **Update container** (docker pull + recreate) — shows the exact
  `docker pull <image> && docker stop <cid> && docker run <reconstructed args>…`
  sequence for confirmation; reconstructing `run` args from `docker inspect`
  faithfully (including `cgroup_parent`, mounts, env, restart policy) is the hard
  part — flag as an open question (§10) whether to shell out to `docker-compose`/
  `ciu` instead of reconstructing a bare `docker run` where a compose file is the
  source of truth.
- **Start / stop / restart** a container or a systemd-managed slice/service —
  `docker start|stop|restart <cid>` or `systemctl start|stop|restart <unit>`,
  shown before running, same confirmation pattern.
- **Live band adjustment** — step `memory.high` up/down live (à la
  `soulmask-mempress.sh`'s calibration squeeze), via `systemctl set-property
  --runtime` (never a raw write, per the ownership rule in
  `MEMORY-ARCHITECTURE.md` §5.0b) so the adjustment is Finding-D-safe by
  construction; shows the exact `set-property` command and the before/after value.
- **Set-property edits** — a generic "edit this limit" action for any attribute
  the origin-detection algorithm (§3.4) already knows how to read, always writing
  via `systemctl set-property` (persistent or `--runtime`, operator's choice,
  defaulting to `--runtime` for transient docker scopes and persistent for slice
  units) — never a raw cgroupfs write, so v2 cannot itself introduce a new
  Finding-D-class bug.

---

## 5. Data sources reference table (exact file → column mapping)

| Column / concept | Exact source | Notes |
|---|---|---|
| `RAM` | `<cg>/memory.current` | |
| `anon`, `file`, `shmem`, `kernel`, `kernel_stack`, `pagetables`, `sec_pagetables`, `percpu`, `sock`, `vmalloc`, `slab_reclaimable`, `slab_unreclaimable`, `slab`, `zswap`, `zswapped`, `swapcached`, `file_mapped`, `file_dirty`, `file_writeback`, `anon_thp`, `file_thp`, `shmem_thp`, `{in,}active_{anon,file}`, `unevictable` | `<cg>/memory.stat` (flat key-value, bytes) | full field list per `CGROUP-MONITORING.md` §4A; absent fields on older kernels degrade to `—` |
| `workingset_refault_anon`, `workingset_refault_file`, `workingset_activate_{anon,file}`, `workingset_restore_{anon,file}`, `workingset_nodereclaim` | `<cg>/memory.stat` | §4B |
| `pgscan`, `pgsteal`, `pgscan_{kswapd,direct,khugepaged}`, `pgsteal_{kswapd,direct,khugepaged}`, `pgdemote_*`, `pgpromote_success` | `<cg>/memory.stat` | §4C |
| `pgfault`, `pgmajfault`, `pgrefill`, `pgactivate`, `pgdeactivate`, `pglazyfree`, `pglazyfreed` | `<cg>/memory.stat` | §4D |
| `pswpin`, `pswpout`, `zswpin`, `zswpout`, `zswpwb`, `swpin_zero`, `swpout_zero` | `<cg>/memory.stat` | §4E |
| `thp_fault_alloc`, `thp_collapse_alloc`, `thp_swpout`, `thp_swpout_fallback` | `<cg>/memory.stat` | §4F |
| `z_pool` | `<cg>/memory.zswap.current` | compressed bytes |
| `swap_disk` | derived: `<cg>/memory.swap.current − memory.stat:zswapped − memory.stat:swapcached`, clamp ≥0 | generalized `p_disk` formula |
| `memory.min/low/high/max` | `<cg>/memory.{min,low,high,max}` | hierarchical — Finding A: effective protection capped by every ancestor's value; drill-down must walk ancestors |
| `memory.events` (`low/high/max/oom/oom_kill/oom_group_kill`) | `<cg>/memory.events` (+ `memory.events.local` for non-hierarchical) | `oom_kill>0` = hard alert |
| `memory.pressure` | `<cg>/memory.pressure` | `some`/`full` × `avg10/avg60/avg300/total` |
| `memory.peak`, `memory.swap.peak` | `<cg>/memory.peak`, `<cg>/memory.swap.peak` | historical max, drill-down only |
| `memory.oom.group` | `<cg>/memory.oom.group` | drill-down only |
| `cpu.weight`, `cpu.max` | `<cg>/cpu.weight`, `<cg>/cpu.max` | quota/period, µs |
| `cpu.stat` (`usage_usec`, `user_usec`, `system_usec`, `nr_periods`, `nr_throttled`, `throttled_usec`, `nr_bursts`, `burst_usec`) | `<cg>/cpu.stat` | last two fields kernel-version-dependent, degrade gracefully |
| `cpu.pressure` | `<cg>/cpu.pressure` | `some` always present; `full` on 5.13+ |
| `io.weight`, `io.bfq.weight` | `<cg>/io.weight`, `<cg>/io.bfq.weight` | `io.bfq.weight` only meaningful with BFQ scheduler active (`/sys/block/<dev>/queue/scheduler`) |
| `io.max` | `<cg>/io.max` | per-device `rbps/wbps/riops/wiops` |
| `io.stat` (`rbytes/wbytes/rios/wios/dbytes/dios`) | `<cg>/io.stat` | per-device (`maj:min`), `dbytes/dios` = discard |
| `io.pressure` | `<cg>/io.pressure` | `some`/`full` |
| `pids.current/.max/.peak/.events` | `<cg>/pids.{current,max,peak,events}` | `.events` only has a `max` counter |
| `hugetlb.*` | `<cg>/hugetlb.{1GB,2MB}.{current,max,events}` | drill-down only, near-always zero on this host class |
| `misc.*`, `rdma.*` | `<cg>/misc.*`, `<cg>/rdma.*` | drill-down only; empty on hosts with no GPU/RDMA hardware |
| `memory.numa_stat` | `<cg>/memory.numa_stat` | drill-down only, per-NUMA-node breakdown of `memory.stat` |
| `cgroup.procs`, `cgroup.threads` | `<cg>/cgroup.{procs,threads}` | process-list for drill-down item 6 |
| `cgroup.events`, `cgroup.stat`, `cgroup.type` | `<cg>/cgroup.{events,stat,type}` | populated/frozen flag, descendant counts |
| `CPU%` per process | `/proc/<pid>/stat` jiffies delta / `_SC_CLK_TCK` | same technique as `damon_cli.py::_read_cpu_jiffies` |
| `net_tx/rx bytes/s+packets/s` | `/proc/<pid>/net/dev` for a representative PID, deduped by `/proc/<pid>/ns/net` inode | `n/a` when no private netns (§3.2 caveat) |
| Origin of a limit | `systemctl show <unit> -p FragmentPath -p DropInPaths -p ControlGroup -p Memory{Min,Low,High,Max} -p CPUWeight -p IOWeight` | unit name = path segment ending in `.slice/.scope/.service` (§3.4 algorithm) |
| zswap module config | `/sys/module/zswap/parameters/{enabled,compressor,max_pool_percent,accept_threshold_percent,shrinker_enabled}` | system-wide, header banner |
| zswap runtime stats | `/sys/kernel/debug/zswap/{stored_pages,pool_total_size,written_back_pages,reject_*,decompress_fail,pool_limit_hit,stored_incompressible_pages}` | **root + debugfs required**; degrade (drop from header, mark estimate with `*`) if unreadable — identical to the existing monitor's `disk_sw` `*` marker |
| `/proc/vmstat` | system-wide `workingset_*`, `pswpin/out`, `nr_anon_pages`, `nr_file_pages`, `nr_shmem`, `nr_swapcached`, `pgscan_kswapd`, `pgsteal_kswapd` | header/system-wide row |
| `/proc/pressure/{memory,cpu,io}` | system-wide PSI | header/system-wide row |
| `/proc/sys/vm/{swappiness,watermark_scale_factor,min_free_kbytes,vfs_cache_pressure,dirty_ratio,overcommit_*}` | header, read-only display | |
| `/proc/meminfo` | `MemTotal/MemAvailable/MemFree/SwapTotal/SwapFree/SwapCached/Buffers/Cached/Shmem` | header |
| Docker metadata | `docker ps`, `docker inspect` (`-f '{{.State.Pid}}'`, `.Config.Image`, `.Name`, `.Config.Labels`, `.State.Status`) | JOIN key = cgroup path via `/proc/<pid>/cgroup` `0::` line |
| DAMON sysfs tree | `/sys/kernel/mm/damon/admin/kdamonds/{nr_kdamonds,N/state,N/pid,N/contexts/.../targets/.../pid_target,N/contexts/.../schemes/.../tried_regions/}` | full tree diagram in `DAMON-GUIDE.md` §5.1; reused verbatim |
| DAMON conflict detection | `/sys/module/damon_stat/parameters/enabled` | disable/restore only in control stage (§3.6c) |

---

## 6. Architecture

### 6.1 Layering: collector / model / UI

```
┌─────────────────────────────────────────────────────────────┐
│ UI layer (Textual)                                           │
│  - DataTable / tree widget, drill-down Screens, chart Screens│
│  - reads ONLY from the model layer; never touches sysfs/proc │
│  - swappable: a future Go/Rust port re-implements only this  │
│    layer + the collector, against the same data contract     │
└───────────────────────────▲───────────────────────────────────┘
                             │ Sample / Frame objects (§3.8 schema)
┌───────────────────────────┴───────────────────────────────────┐
│ Model layer                                                   │
│  - per-entity tiered ring buffers (§3.5)                      │
│  - generalized RateTracker: per (entity_id, metric_key) prior- │
│    sample state + reset detection, lifting                    │
│    soulmask-zswap-monitor.py's RateTracker from "2 trackers,   │
│    fixed keys" to "N entities × arbitrary metric keys"         │
│  - derived-field computation (ratio, headroom%, rf_z/rf_d split)│
│  - feeds from EITHER a live collector sweep OR a --replay      │
│    JSONL iterator — identical downstream code path             │
└───────────────────────────▲───────────────────────────────────┘
                             │ raw counters, one sweep per tick
┌───────────────────────────┴───────────────────────────────────┐
│ Collector layer — PURE DATA, NO UI IMPORTS                    │
│  - cgroup tree walk + docker JOIN (§3.1 discovery algorithm)   │
│  - per-cgroup file reads (§5 table)                            │
│  - DAMON sysfs reads (passive) and writes (control)            │
│  - systemctl show calls for origin detection                   │
│  - --record: same layer, written straight to JSONL, no Textual │
│    import anywhere in this file/module                         │
└─────────────────────────────────────────────────────────────┘
```

The collector/UI boundary is **not** a compiled ABI or RPC protocol in v1 — that
would be over-engineering for a single-host tool. The boundary is *documented*: the
data-source table (§5) plus the `--record` JSON schema (§3.8) together constitute
the language-agnostic contract. A future Go/Rust port re-implements the collector
against §5 faithfully and can validate itself by producing byte-for-byte
comparable `--record` output against the Python implementation on the same host at
the same tick — this is the concrete test for "did the port preserve semantics."

### 6.2 Sampling loop, root requirement

- Single scheduler tick, default **5s** (matches `soulmask-zswap-monitor.py`'s
  default), configurable (`[general] interval` in TOML, `--interval` CLI override).
  One sweep collects **all** discovered entities in the same tick — not
  independent per-entity timers — so rate calculations across entities are
  comparable (same `Δt`) and so the tiered ring buffer's "one sample per tick"
  invariant holds without per-entity clock skew.
- **Root required, unconditionally, in v1** (same as the existing monitor):
  `memory.stat`/zswap debugfs/other-users'-cgroup `io.stat` reads, and any DAMON
  sysfs write, all need root or root-equivalent capabilities in practice on a
  stock Debian host. Document but do not attempt an unprivileged mode in v1
  (deferred, §10).
- DAMON passive-detection polling can piggyback on the same tick (state files are
  cheap reads); DAMON columns' own refresh cadence is governed by DAMON's `aggr_us`
  independently (§3.6b) — the table tick and the DAMON aggregation interval are
  deliberately decoupled, don't force DAMON's interval to match the table's.

### 6.3 Graceful degradation matrix

| Condition | Behavior |
|---|---|
| BFQ scheduler not active on a device (`io.bfq.weight` file present but inert, or scheduler ≠ `bfq`) | Column shows the raw value with a footnote "BFQ inactive — weight has no effect"; never hide the column, hiding would mask a misconfiguration |
| `/sys/kernel/debug/zswap/` unreadable (non-root or `CONFIG_ZSWAP_DEBUGFS` off) | System-wide zswap header stats drop; **per-cgroup** `z_pool`/`z_eq` are unaffected (they come from cgroup files, not debugfs) — mirrors the existing monitor's `disk_sw` `*`-marked degraded estimate |
| No `docker` binary / daemon not running | Container-centric view (§3.1a) shows an empty-state message and disables its hotkey; tree view (§3.1b) is fully functional (this is precisely why tree-view-primary was chosen, §2.2) |
| `/sys/kernel/mm/damon/admin/` absent (`CONFIG_DAMON_SYSFS` not built) | DAMON columns and hotkeys are **removed from the column/action registry entirely**, not merely blank — the help screen and config validator say why |
| A `memory.stat`/`cpu.stat`/etc. field absent (older kernel) | That one field shows `—`; does not affect sibling fields or crash the sweep |
| A cgroup disappears mid-sweep (container restarted) | Same reset-detection as `soulmask-zswap-monitor.py`'s `RateTracker`: one sample of `—` rates, silent resync to the new baseline, never a bogus negative/huge rate |
| `pids`/`hugetlb`/`rdma`/`misc` controller not enabled on a given cgroup | Corresponding drill-down section shows "controller not enabled here" rather than empty-looking zeros |
| Entity has no private network namespace | `net_tx/rx` = `n/a (host netns)` (§3.2) |

### 6.4 Go/Rust port boundary (v1 design constraint, not a v1 deliverable)

v1 ships as pure Python (stdlib + Textual only — no dependency on `damo`, no
dependency on this repo's `damon-analysis` venv, per §3.6c point 6). The two
things a later compiled port needs, both already produced by v1:
1. **§5's data-source table** — the exact file-to-field mapping is language-neutral
   by construction (it's just "read this path, parse this format").
2. **§3.8's JSONL schema** — a Go/Rust collector emitting the same schema is
   drop-in compatible with the existing `--replay` mode of *either* implementation,
   so a partial port (collector only, keep the Python UI reading its JSONL via a
   pipe) is a viable intermediate step, not just "rewrite everything at once."

No plugin ABI, no gRPC, no shared library — keep this cheap until real profiling
data (from v1's own `--record` mode, naturally) shows which layer is actually worth
porting first (§10).

---

## 7. Config format example (`config.toml`)

```toml
# gts-top config — $XDG_CONFIG_HOME/gts-top/config.toml
# All values shown are the shipped defaults; delete a key to fall back to it.

[general]
interval = 5.0                # seconds between collector sweeps
require_root = true           # v1: always true; kept named for forward-compat
default_view = "tree"         # "tree" | "container"
default_column_profile = "auto"  # "auto" (adaptive-width, §3.3) | named profile below

[colors]
# hex or Textual color names; per-tier row background/foreground accents
prod        = { fg = "#e6e6e6", accent = "#2ecc71" }
interactive = { fg = "#e6e6e6", accent = "#3498db" }
besteffort  = { fg = "#a0a0a0", accent = "#7f8c8d" }
unmanaged   = { fg = "#e6e6e6", accent = "#f39c12" }   # bare slices/services, no docker join
warn        = "#f1c40f"
critical    = "#e74c3c"

[thresholds]
# Per-tier overrides of the same metric; "default" applies when a tier has none.
[thresholds.default]
rf_d_per_s      = { warn = 1,  crit = 20 }   # MEASUREMENTS.md: game rf_d/s <=20/s sustained
psi_full_avg10  = { warn = 1,  crit = 2 }    # MEASUREMENTS.md / CGROUP-MONITORING.md §6
psi_some_avg10  = { warn = 5,  crit = 15 }
mem_events_oom_kill = { warn = 1, crit = 1 } # any oom_kill is critical, no tolerance

[thresholds.besteffort]
rf_d_per_s      = { warn = 50, crit = 200 }  # lenient — best-effort is expected to page

[columns]
[columns.profiles.default]
tiers = ["T0", "T1", "T2"]        # adaptive up to T2 unless terminal is wider

[columns.profiles.wide]
tiers = ["T0", "T1", "T2", "T3", "T4"]

[columns.profiles.minimal]
list = ["name", "ram", "rf_d", "psi_mem_full", "cpu_pct"]   # fixed list, ignores tiering

[columns.profiles.pak_focus]
list = ["name", "ram", "z_pool", "z_eq", "hot_pct", "warm_pct", "cold_pct"]

[damon]
hot_rate = 50.0          # percent access frequency
warm_rate = 5.0
cold_age = 30.0          # seconds
idle_age = 120.0         # seconds
max_concurrent_targets = 4
default_ops = "vaddr"    # "vaddr" | "paddr" (paddr not exposed as a per-entity target in v1 UI)

[history]
full_resolution_seconds = 900     # 15 min ring, at [general].interval granularity
downsample_interval_seconds = 60  # 1-minute rollup buckets
downsample_retention_hours = 4

[hotkeys]
profile = "gts-top"       # "gts-top" | "htop" | "top" | "custom"
# overrides only consulted when profile = "custom"; see §8 for the base tables
[hotkeys.overrides]
# action = "key"
# tree_toggle = "F5"
```

---

## 8. Hotkey table

**Base action set** (fixed across all profiles — profiles remap the *key*, never
add/remove an *action*):

| Action | `gts-top` (default) | `htop` profile | `top` profile |
|---|---|---|---|
| Move selection | `↑`/`↓`, `j`/`k` | `↑`/`↓` | `↑`/`↓` |
| Expand/collapse tree node | `←`/`→`, `h`/`l` | `←`/`→` | `←`/`→` |
| Page up/down | `PgUp`/`PgDn` | `PgUp`/`PgDn` | `PgUp`/`PgDn` |
| Jump top/bottom | `Home`/`End` | `Home`/`End` | `Home`/`End` |
| Drill-down (full detail screen) | `Enter` | `Enter` | `Enter` |
| Back / close screen | `Esc` | `Esc` | `Esc` |
| Toggle tree ⇄ container view | `t` | `F5` (htop's own tree-toggle key) | `t` |
| Toggle wide columns (`--wide`/T4) | `w` | `w` | `1` (top's "toggle detail" slot) |
| Cycle sort column | `F6`, `<`/`>` | `F6` | (n/a — top uses single-letter sort below) |
| Sort by memory | — | — | `M` |
| Sort by CPU | — | — | `P` |
| Sort by name | — | — | `N` |
| Reverse current sort | `r` | `I` (htop's invert-sort key) | `R` |
| Incremental filter | `/` | `F4` | `o` (top's "other filter" slot) |
| Search / jump to match | `F3`, then `n`/`N` next/prev | `F3` | `/` (unused otherwise in top profile) |
| Tag/untag row for chart overlay (live) — play/pause (replay) | `Space` | `Space` (htop's own mark-for-multi-select key, repurposed) | `Space` |
| Open chart-overlay screen | `c` | `c` | `c` |
| Toggle DAMON columns / request control-stage start on selected row | `d` (two-key confirm: `d` then `y`) | `d` | `d` |
| Open DAMON detail panel (inside drill-down) | `D` | `D` | `D` |
| Step replay back/forward one sample | `,` / `.` | `,` / `.` | `,` / `.` |
| Replay speed cycle | `+`/`-` | `+`/`-` | `+`/`-` |
| Pause/resume live auto-refresh | `p` | `p` | `p` |
| Force immediate resample | `F5` (native profile only — htop profile uses F5 for tree-toggle instead, no conflict since profiles are exclusive) | (bound to tree-toggle instead) | `s` |
| Export current view snapshot to JSON | `e` | `e` | `e` |
| Help | `F1`, `?` | `F1`, `h` | `h` |
| Quit | `F10`, `q` | `F10`, `q` | `q` |
| **Kill / restart / update (v2, `--admin` only)** | `k` — **unbound/disabled in v1**, shows "requires --admin (v2)" | `F9` (htop's kill key — same v1 disabled behavior) | `k` (top's kill key — same v1 disabled behavior) |

---

## 9. Acceptance criteria

1. **Performance:** renders 30 discovered cgroups (mixed tree depth, ~10 with a
   docker JOIN) at the default 5s interval using **<5% of one CPU core**, measured
   over a 5-minute steady-state run (`pidstat -p $(pgrep -f gts-top) 5` or
   equivalent), on hardware comparable to this host (8 vCPU, thin-provisioned
   virtio disk).
2. **Memory:** total process RSS stays **under ~60 MB** at 40 entities with default
   history settings (§3.5's ~1.6 MB ring-buffer estimate + Python/Textual baseline).
3. **Correctness — reset handling:** killing and restarting a monitored container
   mid-run produces exactly one `—`/blank-rate sample for that entity, then a
   silent resync to correct rates — never a negative or absurdly large rate
   (same guarantee as `soulmask-zswap-monitor.py`'s `RateTracker`, generalized).
4. **Correctness — Finding-D detection:** in a controlled test (raw-write a
   monitored scope's `memory.min` directly via `echo > .../memory.min`, bypassing
   systemd), the tool flags that attribute as drifted/raw-write-origin within one
   sampling interval of the write, and correctly reflects it reverting to the
   systemd-recorded value after a `daemon-reload`.
5. **Correctness — non-container visibility:** with `soulmask-paks.slice` (or an
   equivalent bare slice with no container) present, the tree view shows it as a
   first-class row with a non-null process list and no docker metadata, without
   requiring any special-casing in config.
6. **Graceful degradation:** the tool starts and runs (tree view functional) with
   `docker` absent entirely, with `/sys/kernel/debug/zswap/` unreadable, and with
   `/sys/kernel/mm/damon/` absent — each individually and in combination — without
   crashing, and with the specific degraded behavior matching §6.3's matrix.
7. **DAMON control safety:** starting and then stopping a control-stage DAMON
   session (including via Ctrl-C / crash) restores `damon_stat` to its prior state
   if and only if the tool itself disabled it, and never touches a kdamond index it
   did not allocate.
8. **Record/replay fidelity:** a `--record` run followed by `--replay` of the same
   file reproduces an identical rendered table for every recorded tick (byte-for-
   byte identical formatted cell values, modulo terminal-width-dependent layout).
9. **Packaging:** `pipx install gts-top` (from a local sdist/wheel in absence of a
   published package) succeeds cleanly and registers a `gts-top` console-script
   entry point; running it with no config file present uses documented defaults
   without error.
10. **v2 gating:** with `--admin` absent, every v2 action's hotkey is either unbound
    or shows a "requires --admin" message; with `--admin` present, every v2 action
    shows the exact command it will run and requires an explicit confirmation
    keypress before executing it.

---

## 10. Open questions

1. **Wings has no `CgroupParent` support** (source-verified, `plan-host-resource-
   governance.md` §9 #14) — the game container is permanently under `system.slice`
   in this host's reality, not under `soulmask.slice` as the tiering design
   intends. Should the tool's "tier" concept be (a) purely path-derived (in which
   case the game shows as `unmanaged`/`system.slice` tier, technically correct but
   operator-confusing), or (b) config-mappable (an explicit `[tiers]` table
   mapping a container-name/image pattern or cgroup-path glob to a declared tier,
   overriding path-derived tier)? Leaning (b) but not decided — affects `[colors]`/
   `[thresholds]` keying in §7.
2. **Per-cgroup network accounting has no cgroup-v2-native controller** (§3.2). Is
   the netns-`/proc/net/dev` approximation acceptable indefinitely, or worth an
   eBPF cgroup-id-tagged socket-accounting follow-up? Deferred — no eBPF in v1.
3. **DAMON overhead at scale:** with `max_concurrent_targets` default 4, is that
   the right cap once a second Soulmask instance + several dev containers are
   simultaneously interesting? Needs a measurement pass in the style of
   `MEASUREMENTS.md` (kdamond CPU cost, sampling interference) before raising the
   default.
4. **Unprivileged "viewer" mode:** could a reduced column set (skip debugfs,
   skip other-cgroups'-io.stat if permissions ever tighten, skip DAMON) run without
   root via a granted capability set? Deferred entirely from v1 (root required,
   full stop, §6.2) — revisit only if a non-admin "just let me look" use case
   emerges.
5. **Column-tier width breakpoints** (§3.3) are provisional, sized off this host's
   typical SSH/tmux usage; validate against real operator terminal-width
   distribution before locking them in as unconfigurable defaults (they are
   already config-overridable, §7, so this is a "tune the shipped defaults" task,
   not a design blocker).
6. **Record file size/compression:** v1 writes plain JSONL (§3.8); is gzip-on-the-
   fly (`.jsonl.gz`) worth doing in v1 given long-running recordings, or a trivial
   post-v1 addition? Leaning "post-v1," flag if a recording session is expected to
   run for days.
7. **v2 "update container" command reconstruction** (§4): reconstruct a bare
   `docker run` from `docker inspect`, or shell out to `docker compose`/the
   repo's `ciu` tool when a compose file is the source of truth for that
   container? No decision — depends entirely on which containers an operator
   actually wants this action for for (Soulmask via Wings never goes through
   compose; the dstdns stack always does).
8. **Go/Rust port trigger condition:** §6.4 deliberately avoids committing to
   *when* to port, or which layer first. Suggest revisiting only once `--record`
   from real v1 usage shows where CPU/memory actually goes — don't pre-optimize.
9. **Naming:** see Appendix A — not an engineering question, but blocks the pipx
   package name / console-script name choice, which the developer will need
   before publishing.

---

## Appendix A — Naming (placeholder)

`gts-top` used throughout this spec is a **placeholder only**. The final package
name, console-script name, and any branding are being chosen separately and are
**out of scope for this document**. Do not invent, guess, or bikeshed a name while
implementing v1 — wire the config path, package metadata, and console-script entry
point to read from a single constant so renaming later is a one-line change, and
leave it at `gts-top` until instructed otherwise.
