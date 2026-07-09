# TUI-SPEC — `groop`: a host pressure inspector and cgroup forensics TUI

Status: **SPEC ONLY** — nothing implemented. Authored 2026-07-07 for hand-off to a
developer who has not seen the design discussion. Every section is meant to stand on
its own; where a decision depends on this host's specific setup, the concrete file
paths and live values are given so the spec is checkable against reality.

Revision note: **2026-07-08: folded external review + operator interview —
name groop; v0/v1/v1.5/v2 release cut; v1 read-only; Python+Textual; metric
registry; diagnostics engine in v1; 3-tier network model + provider interface;
daemon as privileged read broker; security model; game-agnostic language.**

Project name is **`groop`**. The package name, binary name, config directory,
daemon socket group, and BPF pin directory all use `groop` unless a later spec
revision explicitly changes them.

Companion docs in this repo (read for the mechanism reasoning this spec assumes):
`../scripts/gstammtisch-guide/CGROUP-MONITORING.md` (every cgroup-v2 metric
explained against live data), `../scripts/gstammtisch-guide/MEASUREMENTS.md`
(SLOs and measurement procedures this tool's thresholds encode),
`../scripts/gstammtisch-guide/MEMORY-ARCHITECTURE.md` (zswap/sysctl/cgroup
reasoning, §5.0b knob-ownership rule),
`../scripts/gstammtisch-guide/plan-host-resource-governance.md` (tiering policy,
Findings A–D), and
`../scripts/gstammtisch-guide/files/usr/local/sbin/soulmask-zswap-monitor.py`
(the single-purpose predecessor this tool generalizes — read it before writing
the collector; several formulas below are lifted verbatim from it).

---

## 0. TL;DR

The reference deployment runs one protected latency-critical game server
(Soulmask), one interactive devcontainer, and ~18 best-effort containers, plus
systemd services and slices with no container at all (the pak tmpfs ramdisk lives
in a bare slice, `soulmask-paks.slice`). Today,
observability is one narrow script per concern: `soulmask-zswap-monitor.py` (game +
pak only), `htop` (per-process, no cgroup/zswap/PSI awareness), `docker stats`
(container-only, no tree, no zswap split), and manual `damo`/`vmtouch` runs for
DAMON hot/warm/cold work. Nobody has a single view of "what is every cgroup on this
box doing right now, including the ones with no container in them, and is any of my
governance config actually the config that's really applied."

`groop` is a host pressure inspector and cgroup forensics TUI, fast enough to be
the first tool you open, with enough process/IO/network/Docker/zswap/DAMON
context that the first diagnosis pass needs no second tool. It replaces the
first 60-90 seconds of `top`, `iostat`, `docker stats`, `ctop`, `ip -s`, `ss`,
and manual cgroup grepping; it does **not** replace their specialist workflows.

The data model generalizes the zswap-split math (`rf_z`/`rf_d`/`rf_f`) proven in
the Soulmask monitor to every cgroup on the host, adds CPU/PSI/IO/net/limits
columns from a systematic sweep of cgroup-v2 + `/proc`, shows the *entire*
cgroup tree (not just containers) so container-less slices are visible, detects
when a limit's live value has drifted from what systemd or an admin last set it
to (the class of bug that bit this host in Finding D), and integrates DAMON for
hot/warm/cold working-set classification in the v1.5 release cut. No prior tool
(see §2) combines all four of: zswap compression-ratio splitting, full
non-container cgroup tree visibility, governance-origin drift detection, and
DAMON integration.

Above the table sits a verdict-first **system banner** (host CPU, memory, swap,
compressed-swap backend state, pressure verdict, per-device network and
per-device disk rates — §3.0). v1 is read-only except record/replay: no DAMON session
starts, no cgroup mutation, no container actions, and no BPF state changes. The
tool runs **with or without root**: a developer in the `docker` group gets a
useful degraded view (§6.2), and a v2 daemon can broker privileged read-only data
over a controlled local socket (§4.5). Architecturally it is a framework-free
collector/model core with the Textual TUI as its *first* frontend — a later
background daemon and web UI attach to the same core without a rewrite (§4.5,
§4.6, §6.1).

---

## 0.1 Release cut — v0/v1/v1.5/v2

**v0 — collector proof.** Framework-free Python, stdlib only. Goal: prove that
the data model is correct before investing in UI polish.

- CLI `--once --json`.
- Cgroup tree walk.
- Docker join.
- Core zswap/refault formulas, reusing the proven split from
  `soulmask-zswap-monitor.py`.
- CPU, PSI, IO, pids.
- Host banner facts.
- Metric registry with source and semantics.
- Reset handling.

**v1 — fast read-only TUI.** Python + Textual UI over the framework-independent
collector/model. Goal: daily replacement for first-pass triage.

- Read-only Textual table/tree.
- Host pressure banner with verdict and top-pressure summary.
- Cgroup tree and container views.
- Core columns and adaptive/job profiles.
- Process drill-down.
- Origin/drift detection.
- Netns-based network columns, source-labelled, plus host/interface truth.
- JSONL record/replay.
- Non-root degraded mode.
- Pressure score and fixed diagnostics rule engine (§3.4a).

v1 is **read-only except record/replay**: it may collect, display, record, and
replay; it must not start DAMON sessions, mutate cgroups, restart or update
containers, alter BPF state, or browse arbitrary host file content.

**v1.5 — DAMON analysis and backend awareness.** Goal: working-set inspection
and correct compressed-swap interpretation without destabilizing v1.

- Passive DAMON session detection.
- DAMON hot/warm/cold columns.
- DAMON detail panel.
- ZRAM/zswap/disk/mixed swap-backend detection and host ZRAM banner metrics.
- Optional controlled `vaddr` session behind root and explicit confirmation.
- Manual `paddr` host session (root + confirmation, §3.6d): system-wide
  hot/warm/cold heat bar in the banner + a host-memory status page; never
  auto-started in this cut.

Only cheap passive DAMON detection may appear in v1. The active-control material
from §3.6 belongs to v1.5 unless explicitly marked otherwise.

**v2 — active governance and exact network provider.** Goal: close the largest
blind spots behind explicit privilege boundaries.

- BPF network provider.
- Privileged daemon or root helper owning BPF/DAMON/root state.
- Docker admin actions.
- File/content browser behind explicit `--inspect-files`.
- `paddr` DAMON auto-start (`[damon] paddr_enabled` persistent kdamond; the
  MANUAL paddr host session is v1.5, §3.6d).
- GPU (§3.11) and ZFS (§3.12) plugins.
- Web UI stays v3 (§4.6).

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

`groop` is scoped to solve exactly these five things for one operator on one host,
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
toolkit and it is exactly what `groop`'s DAMON columns/detail-page generalize:
same sysfs tree, same `Classifier` thresholds, wrapped in a real TUI with
sort/filter/history instead of a 5-second `clear && print` loop.

DAMON's sysfs interface (`/sys/kernel/mm/damon/admin/kdamonds/`) natively supports
**multiple concurrent kdamond contexts** (`DAMON-GUIDE.md` §14: `nr_kdamonds`,
each with its own context/targets/schemes) — this is the architectural fact that
makes "DAMON columns for several monitored containers at once" feasible without a
custom multiplexer; `groop`'s control stage (§3.6c) just needs to track which
kdamond indices it allocated, exactly as `damon_analysis.py::Monitor(kdamond_idx=N)`
already does in this repo.

One conflict to design around: `CONFIG_DAMON_STAT_ENABLED_DEFAULT=y` kernels run a
`damon_stat` module at boot that occupies a kdamond slot; `damon_cli.py` disables it
before starting a manual session and restores it on exit (`disable_damon_stat()` /
atexit). `groop` must do the same (§3.6c) — and must NOT do it silently in the
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
  this is exactly `groop`'s collector/model/UI split (§6.1) and its
  `--record`/`--replay` requirement (§3.8).
- **Borrow:** cgroup-native navigation (rows keyed by cgroup, not by container)
  with per-cgroup CPU/memory/IO panels — validates this spec's row model (§3.1),
  in which the docker JOIN is an enrichment on cgroup rows rather than the
  primary key. (A data-model statement, not a UI-priority one: `groop`'s tree
  and container views are equal peers, §3.1.)
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
  second confirmation this is the right shape for `groop --replay`.
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
  template for `groop`'s v2 `--admin`-gated actions (§4) — show the exact command,
  require confirmation, never default to destructive.
- **glances**: Python (psutil-based), curses/Textual-ish, plugin architecture with
  history and export backends (InfluxDB, Prometheus, CSV, …). **Borrow:** the
  plugin/exporter idea validates keeping the collector cleanly separable from the
  UI (§6.1) so a future exporter could reuse it without touching the TUI.

### 2.4 btop

btop (aristocratos/btop, C++; successor of bashtop/bpytop) is a process/system
monitor with **no cgroup or container awareness** — its relevance here is pure UI
craft. **Borrow (three things):** (1) **always-visible hotkey labels** rendered
into box headers/footers, so every reachable action is discoverable without
opening a help screen — `groop` adopts this as a persistent one-line key-hint
footer (§3.9); (2) **automatic time-graphs**: btop graphs CPU/mem/net/disk
continuously without being asked — validating this spec's always-on ring buffer
feeding in-cell sparklines and banner graphs (§3.0, §3.5) rather than opt-in
charting; (3) the **live filter** (`f`, filters the process list as you type, no
submit step) — `groop`'s `/` filter behaves identically (incremental). btop's
recent GPU panels (NVIDIA/AMD/Intel) are also the UX reference for the optional
GPU plugin (§3.11).

### 2.5 Textual (UI framework choice)

Textual (Textualize) is an actively maintained Python TUI framework (CSS-like
styling, reactive data binding, async event loop, built-in `DataTable` widget with
sortable columns and cell-level styling, and a widget ecosystem including
sparkline/plot widgets) — it directly supports the two hardest UI requirements here:
a `DataTable` that re-renders efficiently on a 5-second tick without a full-screen
redraw, and compositing a full-screen detail "screen" (Textual's `Screen` stack) for
the Enter-drill-down requirement. This justifies it as the v1 UI framework; a Go/Rust
port (§6.4) would use an equivalent (Bubble Tea + Bubbles table, or ratatui) against
the same collector data model. Textualize also ships `textual-serve`/`textual-web`
for exposing a Textual app in a browser — useful as a stopgap, but the future web
UI (§4.6) is specified as a *separate* frontend on the daemon API rather than a
served terminal, so the framework choice does not constrain it.

### 2.6 Synthesis — what to borrow, what nobody has

| Source | What `groop` borrows |
|---|---|
| `damo`/`damon_cli.py` | sysfs protocol, `Classifier` hot/warm/cold thresholds, multi-kdamond pattern, damon_stat-conflict handling |
| `below` | collector/model/UI split enabling record+replay as one codebase, cgroup-tree-first model |
| `atop` | record/replay UX precedent, critical-resource highlighting |
| `ctop` | in-cell sparklines, list→detail toggle |
| `btop` | always-visible key-hint footer, always-on automatic time-graphs, incremental live filter |
| `lazydocker` | gated destructive-action pattern (show command, confirm) for all mutating actions |
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

### 3.0 System banner (host totals)

The first viewport is verdict-first. A top/htop-style banner block sits above the
table on every main screen, collapsible with `B` (§8), and answers "is the host
healthy, what resource is pressuring it, and which entities are the likely
causes?" before the operator reads a full table.

v1 first-screen shape:

```text
HOST  OK|WARN|CRIT
CPU 18% usr 4% sys 0.1% steal | MEM 3.1G avail | PSI mem full 0.0 io full 0.0 cpu some 3.2
SWAP zswap:on zram:1.2G/3.8G disk:8.0G active:mixed | DISK vda 12MB/s 25% util | NET uplink 4.2M down 0.9M up

TOP PRESSURE
1 soulmask game     mem: rf_d 0/s rf_z 18/s psi 0.0  zswap 1.8G  drift none
2 buildkit          io:  31MB/s cap hit          psi io some 8.1
3 authentik-worker  mem: high events +12         headroom 91%
```

The detailed table below this block is supporting detail, not the only product
surface. When a v2 daemon is attached, the banner sparklines prefill roughly one
minute of pre-start history from the daemon ring buffer before live samples arrive
(btop-style).

Contents, each with a compact sparkline fed by the `__host__` pseudo-entity's
history series (§3.5):

- **CPU:** aggregate busy % with user/system/iowait/steal breakdown
  (`/proc/stat` `cpu` line delta), load averages (`/proc/loadavg`), core count.
- **Memory:** `MemTotal` / `MemAvailable` / `MemFree`, buff/cache, `Shmem`
  (`/proc/meminfo`) — rendered as a stacked bar, htop-style.
- **Swap:** `SwapTotal` / used / `SwapCached`, active swap backend
  classification from `/proc/swaps`, and the derived non-zswap swap-device
  usage estimate. Do not label this as physical disk on zram-only or mixed
  hosts.
- **zswap:** pool bytes (compressed), stored bytes (uncompressed equivalent),
  **ratio**, and pool utilization vs. `max_pool_percent`. Primary source is
  debugfs (`stored_pages`/`pool_total_size`, root only); fallback is
  `/proc/meminfo`'s `Zswap`/`Zswapped` fields (present since kernel ~5.19,
  world-readable) so the ratio survives non-root mode (§6.2).
  `written_back_pages` shown when debugfs is readable.
- **zram:** when `/sys/block/zram*` devices exist, show host-level
  uncompressed/compressed/memory-used totals, compression ratio, allocator
  efficiency, failed read/write counters, and writeback bytes if `bd_stat`
  exists. ZRAM is host/device-level only; never invent per-cgroup ZRAM
  compression ratios.
- **Network, PER DEVICE:** one line per interface from the host's
  `/proc/net/dev` — tx/rx bytes/s **and** packets/s. Interfaces matching the
  `[banner] net_device_exclude` globs (default `veth*`, `br-*`, `docker0`) are
  aggregated into a single "containers" line instead of listed, so ~20 veths
  don't drown the uplink.
- **Network health:** host/interface truth from `/proc/net/dev`, `tc -s qdisc`
  (fq_codel backlog/drops/overlimits where available), `/proc/net/softnet_stat`,
  `/proc/net/snmp`, `/proc/net/netstat`, and optional `ethtool -S`; these are v1
  host signals even when per-entity network attribution is only approximate
  (§3.2 network provider model).
- **Disk, PER DEVICE:** one line per block device from `/proc/diskstats` —
  r/w MB/s, r/w IOPS, in-flight, and %util (`io_ticks` delta / Δt). `loop*`,
  `ram*`, `zram*` excluded by default (`[banner] disk_device_exclude`).
- **Host PSI:** `/proc/pressure/{memory,io,cpu}` `some`/`full` `avg10`.
- **DAMON host split (v1.5/v2 conditional, §3.6d):** a stacked
  `hot/warm/cold/idle` bar of MemTotal when a system-wide DAMON source is active
  (passive `damon_stat` readouts, or a controlled `paddr` session).

Banner values are recorded into every `--record` frame under the `host` key
(§3.8) and replay like everything else. Every banner line degrades per-source
(§6.3): a missing file drops that one line, never the banner.

### 3.1 Row model: container view and tree view (hot-toggleable)

Two **equal** views over the same underlying entity table, hot-toggled with a
single hotkey (`t`, htop's tree-toggle convention, §8). Neither view is primary:
sorting, filtering, row-tagging and Enter-drill-down work identically in both —
the toggle changes only which rows are visible and how they are arranged.

**(a) Container-centric view.** One row per running Docker/Podman container — a
flattened projection of the entity table showing only rows that have a docker
JOIN (below), with no parent slices. This is the "docker stats, but with
everything else" view.

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
  numeric behavior is dictated by the metric registry (§3.2), not by ad hoc
  table code. Each branch metric explicitly selects one active mode:
  kernel-subtree-file, local-only file, or userspace aggregate of selected
  descendants. The active mode is visible in the table header (for example
  `RAM[subtree]`, `net_rx[NS-agg]`, `PSI[mem local]`) so operators can tell
  whether a branch value came from the kernel or from user-space aggregation.
  Limits and PSI are never summed; network branch aggregation is allowed only
  when the provider can prove all contributing children have private,
  deduplicated network namespaces (§3.2).

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
   UUID per `soulmask-zswap-monitor.py`'s own docstring). Human-friendly display
   names come from the resolver below.

**Friendly-name resolver.** Wings containers show up as bare UUIDs; the display
name the admin typed into the Pterodactyl panel is resolvable on the node. The
facts below are source-verified against wings (`develop` + v1.11.x) and panel
(`1.0-develop`):

- Wings **does** receive the display name: the panel's
  `ServerConfigurationStructureService` sends a `meta` object (`name`,
  `description`) inside the server-settings payload, unmarshaled into wings'
  `Configuration.Meta` (`server/configuration.go`, `ConfigurationMeta{Name,
  Description}`).
- Wings does **not** persist it anywhere readable: `states.json` holds only
  UUID→power-state, `wings.db` (SQLite) contains only the `Activity` table, and
  the Docker labels wings sets are exactly `Service=Pterodactyl` +
  `ContainerType=server_process` — no name, and the container name is the UUID.
- It **is** exposed by wings' local HTTP API: `GET /api/servers` (default
  `:8080`, `Authorization: Bearer <token>` where the token is the `token:` value
  in root-readable `/etc/pterodactyl/config.yml`) returns every server's
  `configuration.meta.name` keyed by `configuration.uuid`.

Resolver chain (first hit wins; results cached for the session, refreshed on
container churn; every step degrades gracefully to the next):
1. **Wings local API** — one `GET /api/servers` call resolves all UUIDs at once
   (root needed only to read the token from config.yml; endpoint host/port/TLS
   from the same file's `api.*` keys).
2. **Panel remote API** — `GET {remote}/api/remote/servers/{uuid}` using the
   node token pair from the same config.yml (exactly what wings itself calls) →
   `settings.meta.name`. Covers "wings daemon down, panel up".
3. **Compose/ciu labels** — for non-wings containers: `com.docker.compose.
   project`/`service` and the ciu label schema (§4.3) already carry a
   human-meaningful name; used as-is.
4. **Fallback: the raw container name/UUID** — always correct, never blocks
   rendering. The resolver is asynchronous: rows render with UUIDs immediately
   and upgrade in place when a resolution arrives.

### 3.2 Metric semantics registry and columns

The metric registry is the single source of truth for table columns, branch-row
behavior, F1/help glossary text, source/confidence metadata, and JSONL schema
generation. The implementation must not maintain a separate prose glossary or
hand-written JSON schema that can drift from the registry; §3.10's help content
is generated from this registry plus static non-metric concepts.

Every metric entry carries:

- semantic tags: `local`, `subtree`, `counter`, `gauge`, `derived`,
  `aggregatable`, `non_aggregatable`;
- `source_confidence`: `exact`, `estimated`, `netns-approximation`, or
  `unavailable`;
- source path or provider name, unit, reset behavior, and user-visible help text;
- branch-row policy: `kernel-subtree-file`, `local`, `userspace-aggregate`, or
  `not-aggregatable`;
- sensitivity metadata for daemon/API exposure (§4.5, §6.5).

Concrete policy examples:

| Metric family | Tags | Branch-row policy |
|---|---|---|
| `memory.current`, `memory.stat:*`, `memory.swap.current` | `subtree`, `gauge`, `exact`, `aggregatable` only when explicitly using a child aggregate | default to kernel subtree file on the branch cgroup; never add child values on top of that |
| `memory.events`, `cpu.stat`, `io.stat`, `pids.events` | `counter`, `exact`, reset-aware | rate from the branch file when it is hierarchical; otherwise userspace aggregate only when the registry marks it safe |
| PSI | `local`, `gauge`, `exact`, `non_aggregatable` | branch cgroup's own pressure file only |
| limits (`memory.high`, `cpu.weight`, `io.max`) | `local`, `gauge`, `exact`, `non_aggregatable` | branch cgroup's own live value plus origin/drift metadata |
| netns network | `derived`, `counter`, `netns-approximation` | leaf/private-netns only; branch aggregate only when all children are provably private and deduplicated |
| BPF network (v2) | `counter`, `exact`, `aggregatable` | provider counters keyed by cgroup id; userspace maps cgroup id to path and aggregates leaves |

The active branch mode is part of the rendered column header and JSONL field
metadata. A value and the reason it is unavailable are distinct states; the UI
must never silently zero-fill unavailable data.

#### Column tables

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
| `swap_disk` | legacy name for non-zswap swap-device usage estimate; disk only on disk-only hosts, logical zram-backed swap on zram-only hosts, unknown backend on mixed hosts | `memory.swap.current − zswapped − swapcached`, clamp ≥0 (identical formula to the existing monitor's pak `p_disk`, now applied to every entity, not only the pak slice) |
| `rf_z/s` | zswap refault rate (µs-scale, healthy) | `Δ memory.stat:zswpin / Δt` |
| `rf_d/s` | legacy name for non-zswap anonymous refault rate; disk-lag predictor on disk-backed swap, but backend-aware wording is required on zram/mixed hosts | `max(0, Δ workingset_refault_anon − Δ zswpin) / Δt` |
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
| `pressure` | sortable pressure score | registry-backed weighted score from §3.4a, with drill-down breakdown |

**Network provider model (must be documented, not hidden):** cgroup v2 has **no
native per-cgroup network-accounting controller** (net_cls/net_prio are cgroup v1
legacy and not mounted here). `groop` uses a three-tier model:

1. **Tier 1 — host/interface truth (v1):** `/proc/net/dev`, `tc -s qdisc`,
   `/proc/net/softnet_stat`, `/proc/net/snmp`, `/proc/net/netstat`, and optional
   `ethtool -S`. This catches host network trouble even when per-cgroup
   attribution is weak.
2. **Tier 2 — netns approximation (v1):** read `/proc/<pid>/net/dev` for a
   representative PID, deduplicate by `/proc/<pid>/ns/net` inode, and treat the
   result as network-namespace traffic, not process or cgroup traffic. It is only
   meaningful when the entity has its own private network namespace. Host-network
   containers and bare services show `n/a (host netns)`.
3. **Tier 3 — BPF cgroup-skb provider (v2):** exact per-cgroup socket traffic
   through the provider interface below and Appendix B.

Network source labels are visible in drill-down and optionally as a compact table
glyph:

- `net:BPF` — exact cgroup BPF provider;
- `net:NS` — network namespace approximation;
- `net:HOST` — host/interface-only signal;
- `net:N/A` — not attributable, with reason.

The v1 provider interface is defined now so v2 BPF does not change the table,
history schema, or drill-down contract. Each provider emits:

- entity key;
- rx/tx bytes and packets;
- optional protocol split;
- source label;
- confidence;
- aggregation policy;
- reason when unavailable.

Branch aggregation from `net:NS` is forbidden unless every child has a distinct
private network namespace and the aggregation code can prove deduplication by
namespace inode. `IPAccounting=` is an optional provider for systemd-native units
only; it is not the general model for Docker/Wings scopes.

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

Column **profiles** in config (§3.7, `[columns]`) are layered on top of the width
tiers. Width tiers decide what can fit; job profiles decide which operator
question is being answered. Profiles override tiering when selected explicitly;
tiering is the *default* ("auto") profile's behavior.

Shipped job profiles:

| Profile | Columns |
|---|---|
| `triage` | `pressure`, `RAM`, `CPU%`, `PSI(mem)`, `PSI(io)`, `rf_d/s`, `io_r/io_w`, `net_tx/net_rx`, `net_source` |
| `memory` | `RAM`, `anon`, `file`, `shmem`, `z_pool`, `z_eq`, `ratio`, `swap_disk`, `rf_z/s`, `rf_d/s`, `rf_f/s`, `pgscan/s`, `pgsteal/s`, `mem.evt` |
| `network` | `net_tx/net_rx`, packets/s, drops, retransmits, `sock`, `net_source`, host/interface health |
| `governance` | `mem_min/low/high/max`, `io_max`, `cpu.weight`, origin, drift, `memory.events`, `pids.current` |
| `damon` | `hot%`, `warm%`, `cold%`, `idle%`, target PIDs, sample age |

The hotkey profile picker (§3.9/§8) switches among these job profiles as well as
the width-based `auto`/`wide` views.

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
   digest, created time, restart count, labels, and, expanded beyond the v1
   original scope:
   - **Volumes** — every mount, not just the path: host source path,
     container destination, driver (`local`, or a named volume's driver),
     read-only flag, and (for a named volume) the volume's own `docker volume
     inspect` metadata (labels, driver options).
   - **Overlayfs layer hierarchy** — for containers on the `overlay2` storage
     driver (this host's default), `docker inspect`'s `GraphDriver.Data`
     block already carries `LowerDir` (colon-separated, bottom-to-top),
     `UpperDir` (the container's writable layer), `MergedDir` (the unified
     view actually mounted at the container's rootfs), and `WorkDir`; render
     these as an ordered stack (base image layer at the bottom, container's
     own writes on top) with each `LowerDir` segment's directory name mapped
     back to its `/var/lib/docker/overlay2/<id>/` path so an operator can
     tell "which image layer contributed this file" without shelling out to
     `docker history` separately. Degrades gracefully (§6.3) to "storage
     driver: <name>, layer detail not available" on `devicemapper`/`btrfs`/
     `vfs` hosts, where the layer concept doesn't map onto directories the
     same way.
   - **Logs** — the container's log driver and, for `json-file` (this host's
     default), the log file path(s) under
     `/var/lib/docker/containers/<id>/<id>-json.log*` (including rotated
     files) plus their current sizes; other log drivers (`journald`,
     `syslog`, …) show the driver name and, where knowable, the equivalent
     `journalctl -u docker CONTAINER_ID=<id>` lookup instead of a file path.
     v1 discovers and displays log paths/metadata only; log tailing is opt-in
     and belongs with file inspection because logs can contain tokens,
     environment data, and user content.
8. **Content inspection status** — v1 shows source paths, mount metadata,
   overlay layer metadata, log driver/path metadata, and explicit permission or
   provider status. It does **not** browse arbitrary host file content. The
   read-only content browser moves to v2 behind explicit `--inspect-files` (or
   an equally explicit config flag), because read-only root access can still
   expose secrets, tokens, env files, mounted volumes, and logs. When enabled in
   v2 it remains non-mutating: no write, delete, or edit capability at any
   privilege level.
9. **Time-series charts** of selected metrics for this entity, driven by the
   ring buffer (§3.5) — the operator picks which metrics chart via a hotkey
   (`Space` to tag a metric row for charting, mirroring the row-tagging hotkey
   used for cross-entity overlays, §3.5).
10. **Pressure score breakdown and findings** — the diagnostics engine (§3.4a)
    explains why the row is colored, why it sorted high by `pressure`, and which
    raw metrics contributed.

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

Drift severity defaults from the review + operator interview (2026-07-08):
**any drift is visible as a warning**, but red/alert severity is reserved for
drift that changes the effective protection of a protected workload.

### 3.4a Diagnostics engine

The diagnostics engine is v1 scope. It is registry-backed, deterministic, and
small: no ML, no learned thresholds, no opaque scoring. Thresholds live in
`config.toml` and default from `[thresholds]` (§3.7/§7).

**Per-entity `pressure` score.** Every entity gets a sortable `pressure` score
that helps first-pass triage without hiding raw metrics. Inputs:

- memory PSI full/some;
- IO PSI full/some;
- CPU PSI some;
- `rf_d/s`;
- `rf_f/s`;
- memory high events;
- OOM kills;
- IO cap saturation;
- network drops/retransmits if attributable.

The drill-down renders a score-breakdown panel with the weighted contribution of
each input, the source/confidence metadata from the registry (§3.2), and the
threshold profile that produced the result.

**Fixed findings rule engine.** v1 ships a small fixed rule list, approximately
these eight rules from the review, rendered as a "why this row is red" panel:

- `rf_d/s > 0 on protected game`: cold tail is touching disk; check writeback and
  memory.min.
- `rf_f/s sustained on game`: file cache is too small; do not lower swappiness.
- `memory.events high rising`: `memory.high` is actively throttling this cgroup.
- `memory.current > memory.high` with PSI memory full: reclaim is user-visible.
- `io.pressure full high` plus capped `io.max`: expected throttling, not a bug.
- systemd-recorded limit differs from sysfs: drift/raw write risk.
- `sock` memory rising with network pps: socket buffers are material.
- network source is `host-netns`: per-row network number is intentionally absent.

Public UI copy should map these to game-agnostic terms where possible
(`protected service`, `latency-critical workload`) while preserving Soulmask as
the reference deployment in examples and appendices.

### 3.5 History: ring buffer, sparklines, chart overlays

**Configurable local history**, cost bounded by
`entities × tracked_metrics × samples`. Retention, sampling interval,
downsampling, and memory-vs-disk behavior are configurable. The shipped v1
default is a 4-hour in-memory profile at the 5s sampling interval, with
downsampling available for longer views.

| Tier | Resolution | Retention | Samples/series |
|---|---|---|---|
| Full | sampling interval (default 5s) | 4 h | 2880 |
| Downsampled | configurable rollup (mean; max kept alongside for spike visibility) | configurable | configurable |

**Tracked metrics for history** (24 numeric series per entity — a deliberate
subset of §3.2's full column set; static/limit columns like `mem_max` or
`cpu.weight` are not time-series, they're looked up fresh from the model's latest
sample): `ram, anon, file, z_pool, z_eq, swap_disk, rf_z, rf_d, rf_f, cpu_pct,
psi_mem_some, psi_mem_full, psi_io_some, psi_io_full, psi_cpu_some, io_r_bps,
io_w_bps, io_r_iops, io_w_iops, net_tx_bps, net_rx_bps, pids_current,
headroom_mem_pct, hot_pct` (last one DAMON-conditional, blank series when inactive).

**Memory budget** (review + operator interview, 2026-07-08): with 40 entities,
24 tracked series, 4 hours at 5s sampling, stored as **fixed-size `float32`
arrays** (`array.array('f')` or equivalent numeric arrays — explicitly NOT Python
lists of floats, whose per-element object overhead would be far larger):

```
4 h * 3600 / 5 = 2880 samples per series
40 entities * 24 metrics * 2880 samples * 4 bytes ~= 11.1 MB raw samples
```

Allowing for ring-buffer structure, entity indexes, timestamps, and Python object
overhead, a realistic in-memory budget is roughly **20-40 MB beyond the TUI
baseline** if implemented carefully with numeric arrays rather than Python float
lists.

Plain full-frame JSONL on disk is much larger. A rough order-of-magnitude for 40
entities is 60-120 KB per frame, or about **170-350 MB for 4 hours** at 5-second
sampling. Streaming zstd should compress that heavily, likely into tens of MB,
because keys and shapes repeat. Compressed recording is an early follow-up after
v1, not a v1 gate.

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

### 3.6 DAMON integration — v1.5, with cheap passive detection allowed in v1

Most DAMON functionality is v1.5. v1 may include only cheap passive detection
that reads existing state and never writes to DAMON sysfs. No v1 path starts,
stops, tunes, disables, restores, or otherwise mutates a DAMON session.

**(a) Passive — detect and ingest an already-running session (v1 if cheap,
otherwise v1.5).**
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

**(b) Columns — hot/warm/cold split per monitored target, configurable ages
(v1.5).**
For any entity with an active target (from (a) or (c)), compute the classified
split using the same `Classifier` logic as `analyze_process.py`/`damon_cli.py`:
`hot` ≥ `hot_rate`% access frequency (default 50%), `warm` ≥ `warm_rate`% (default
5%), `cold` ≥ `cold_age` seconds since last access (default 30s), `idle` ≥
`idle_age` seconds (default 120s) — all four thresholds configurable per-profile in
`[damon]` config (§3.7), matching `analyze_process.py`'s own CLI defaults exactly
so operators moving between `damon_cli.py` and `groop` get identical
classification. Columns refresh on the DAMON aggregation cadence (`aggr_us`,
typically 1–2s), independent of the table's own sampling interval.

**(c) Controlled vaddr — start/stop a DAMON recording on a selected container's
PID(s) from the TUI (v1.5).** Root required, explicit confirmation required.
Flow:
1. Operator selects a row (container or bare cgroup with processes), presses the
   DAMON-start hotkey (`d`, two-key sequence `d` then `y` to avoid accidental
   activation — see §8), which opens a **confirmation dialog showing the exact
   sysfs writes about to happen** — kdamond index to be allocated, target
   PID(s), ops set (`vaddr`), and the interval/aggregation values that will be
   written — before anything is written. DAMON-start is a mutating action like
   any in §4, and follows the identical show-the-exact-operation-then-confirm
   discipline (§4's framing). It does not ship in v1.
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
   `groop` vendors its own minimal sysfs writer (a slimmed reimplementation of
   `damon_analysis.py::SysfsInterface`'s create/configure/start/stop calls) so the
   pipx package has no dependency on a host-specific external toolkit. Passive
   reading (stage a) already requires no such dependency since it only reads the
   same sysfs tree regardless of who created the session.
7. Concurrent control-stage targets are capped (`[damon] max_concurrent_targets`,
   default 4) to bound kdamond kernel-thread and sampling overhead — architecturally
   more are possible (§14 of `DAMON-GUIDE.md`), the cap is a deliberate v1.5 safety
   default, not a hard architectural limit.
8. Stopping (same hotkey, toggle) tears down only the kdamond(s) this session
   allocated, restores `damon_stat` if the tool disabled it, and leaves any
   externally-detected session untouched.

DAMON snapshots captured while control-stage-active are recorded into the
`--record` stream identically to every other sample (§3.8) — a replay of a
recording that included a DAMON session shows the same hot/warm/cold columns
during scrub as it did live.

**(d) paddr — whole-system mode (v1.5 as a MANUAL host diagnostic — package
P11; persistent auto-start stays v2. Assessed: useful, feeds the banner).**
`vaddr` (used exclusively in (c) above) overstates badly for whole-host
hot/cold classification — `DAMON-GUIDE.md` §12.1's own numbers (85–95% of
reported bytes are unmapped virtual-address gaps) make it unusable as a
system-wide signal without per-process filtering that defeats the point of a
*system*-wide view. `paddr` (`DAMON-GUIDE.md` §12.2) monitors physical DRAM
directly — every reported byte is real, backed RAM, no gaps to filter — which
is exactly the property the banner's host-level hot/warm/cold bar (§3.0) needs
and `vaddr` cannot supply. Verdict: **useful, and specified as an optional
system-wide kdamond**, distinct from and orthogonal to the per-entity `vaddr`
targets in (c):
- Configuration mirrors `DAMON-GUIDE.md` §12.3's own recommendation for
  hot/cold classification: `400ms` sampling / `8s` aggregation, run
  continuously (not a 5-minute one-off) once enabled — `[damon] paddr_enabled
  = false` by default (§3.7/§7) and is not exposed in v1, since it's a second
  permanent kdamond thread and this spec doesn't default to spending that
  overhead unasked.
- **Cannot attribute to a specific entity** (`DAMON-GUIDE.md` §12.2's own
  stated limitation: physical pages aren't attributable to a process/cgroup
  without `page-types`/`pagemap`-level extra work this tool doesn't do) — so
  paddr output feeds **only** the banner's system-wide hot/warm/cold bar
  (§3.0), never a per-row column; per-entity hot/warm/cold stays exclusively
  (c)'s `vaddr`-per-target mechanism. These two DAMON uses coexist as separate
  kdamond contexts (the same multi-kdamond pattern used everywhere else in
  this section, `DAMON-GUIDE.md` §14) and are never conflated in the UI.
- Same `damon_stat` conflict handling, same idx-allocation discipline, and
  same atexit-restore guarantee as (c) — a system-wide paddr session is still
  a *mutation* (it starts a kdamond), so it is gated behind the same
  confirmation-dialog-with-exact-command and root requirement as any (c)
  control-stage action, never silently auto-started.

### 3.7 Config: TOML file

One file, XDG-conventional location (`$XDG_CONFIG_HOME/groop/config.toml`,
falling back to `~/.config/groop/config.toml`; `--config <path>` override). Full
worked example in §7. Sections:

- `[general]` — sampling interval (default 5.0s), permission mode (`auto` by
  default — detects root vs. `docker`-group-unprivileged at startup and runs in
  whichever degraded-or-full mode applies, §6.2; `root`/`unprivileged` force an
  explicit mode for testing), default view (`tree` or `container`), default
  column profile, and `inspect_files = false` (v2-only explicit file/log content
  browser gate).
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
- `[columns]` — named profiles (`triage`, `memory`, `network`, `governance`,
  `damon`, `default`, `wide`, `minimal`, custom lists), and the tier-priority
  table from §3.3 (overridable, in case an operator's terminal/SSH setup needs
  different breakpoints than the shipped defaults).
- `[damon]` — `hot_rate`, `warm_rate`, `cold_age`, `idle_age` defaults (mirroring
  `analyze_process.py`'s CLI defaults exactly), `max_concurrent_targets`, and the
  whole-system `paddr_enabled`/`paddr_interval_us`/`paddr_aggr_us` toggle feeding
  the banner's hot/warm/cold bar (§3.6d).
- `[gpu]` — v2 plugin enable mode (`auto`/`on`/`off`, §3.11).
- `[zfs]` — v2 plugin enable mode (`auto`/`on`/`off`, §3.12).
- `[history]` — full-resolution window seconds, downsample interval, downsample
  retention hours (the configurable scheme from §3.5, overridable) — the v1
  in-process TUI ring buffer only; default profile is 4h at 5s.
- `[history.daemon]` — v2/v3 background daemon only (§4.5): `max_size_mb` and
  `max_age_days` retention caps (both apply simultaneously) and the
  `compression` toggle (`none` today, `zstd` planned).
- `[hotkeys]` — profile selection (`groop` native / `htop` / `top` / `custom`)
  and individual overrides (§3.9/§8).

### 3.8 Record & replay

`--record <path.jsonl>` runs the collector (with or without the TUI attached —
headless recording is a legitimate mode, since the collector has no UI
dependency, §6.1) and appends one JSON object per sampling tick. The schema
is generated from the metric registry (§3.2) and **extends**
`soulmask-zswap-monitor.py --json`'s existing per-sample object
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
      "net": {"tx_bps": null, "rx_bps": null, "source": "net:N/A", "confidence": "unavailable", "reason": "host netns"},
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

### 3.8a Incident snapshots (v1.5)

Hotkey `S` saves a self-contained incident bundle for the selected entity. This is
marked v1.5 because it adds file collection and provider-status packaging beyond
the v1 table/record/replay path. Bundle contents:

- current frame;
- previous N frames from the ring buffer;
- selected entity's relevant cgroup files;
- `systemctl show` output for the entity and ancestors;
- Docker inspect summary when joined;
- provider status, including BPF provider status when enabled.

The bundle is read-only evidence capture. It follows the same source and
sensitivity metadata rules as JSONL and the daemon API.

### 3.9 Hotkeys and profiles

Full table in §8. Summary of the design: a **`groop` native default** keymap
followed by htop/top conventions where they exist (`F6`/`<`/`>` cycle sort, `t`
tree toggle, `/` filter, `Space` tag-for-overlay in live mode), plus two
**alternate profiles** (`htop`, `top`) that remap the same *actions* onto each
tool's actual muscle-memory keys, selected via `[hotkeys] profile = "htop"` in
config or the `--hotkeys` CLI flag. Profiles remap keys to actions; they never add
or remove actions — v1's action set is fixed, only the binding changes.

Column/job profiles are separate from hotkey profiles. The action `cycle column
profile` rotates `auto`, `triage`, `memory`, `network`, `governance`, `damon`,
and `wide`, with the currently active profile shown in the table header.

`k` (kill) is **explicitly reserved and unbound in v1** (`k kill NO (v2)` per the
design brief) — pressing it shows "not available in this build; requires --admin
(v2)" rather than doing nothing silently, so operators aren't left wondering if
the keypress registered.

### 3.10 Glossary (embedded in the tool's help screen, `F1`/`?`)

Metric explanations, units, source paths, semantic tags, branch-row policy, and
JSONL field names are generated from the metric registry (§3.2). The help screen
must not duplicate metric prose by hand.

Static, non-metric help entries cover concepts that span several registry
entries:

- **Origin (of a limit):** whether a cgroup attribute's live value came from a
  systemd unit file, a `systemctl set-property` drop-in (persistent or
  `--runtime`), or an unmanaged raw write; see §3.4's origin algorithm and
  `plan-host-resource-governance.md` Finding D.
- **Network source labels:** `net:BPF`, `net:NS`, `net:HOST`, and `net:N/A`
  describe provider and confidence. BPF means per-cgroup socket traffic, not a
  NIC wire tap (§3.2, Appendix B).
- **DAMON hot/warm/cold:** v1.5 working-set classification from existing or
  controlled DAMON sessions (§3.6).
- **ZFS ARC / per-cgroup I/O gap (if applicable, §3.12):** ZFS's Adaptive
  Replacement Cache is a single host-wide cache with no per-cgroup partition,
  and ZFS I/O largely bypasses the block-cgroup (`io.*`) attribution path this
  tool relies on elsewhere.

### 3.11 GPU per container (v2 optional plugin)

Short, deliberately narrow — this host has no GPU today, so this v2 plugin
the tool auto-detects rather than a core column set (`[gpu] enabled = "auto"`,
§3.7/§7), activated only when both a GPU is present *and* the NVML bindings
are importable, degrading silently (§6.3-style) to "no GPU columns/plugin
disabled" otherwise — never a hard dependency of the package.

**Source:** NVIDIA Management Library via `pynvml` (or `nvidia-ml-py`, same
API) — `nvmlDeviceGetUtilizationRates` (SM/memory busy %),
`nvmlDeviceGetMemoryInfo` (used/total VRAM), and, per-process,
`nvmlDeviceGetComputeRunningProcesses`/`nvmlDeviceGetGraphicsRunningProcesses`
(PID + per-process VRAM). AMD/Intel GPUs are out of scope for v1's plugin;
btop's own multi-vendor GPU panels (§2.4) are the UX/coverage reference if a
later port wants parity, not a v1 commitment here.

**Container attribution.** NVML reports PIDs, not cgroups or containers — the
same `pid → cgroup` mapping technique already used for DAMON (§3.6a: read
`/proc/<pid>/cgroup`'s `0::` line) resolves an NVML PID to its owning entity
row, no new mechanism needed. This works for the normal `docker run --gpus`
case with no extra `--pid=host` requirement (NVML enumerates PIDs from the
driver's own device-open table, which is host-namespace by construction
regardless of the container's own PID namespace). A cheaper, NVML-call-free
signal for "does this entity have GPU access at all" gates whether the tool
even attempts a per-process NVML lookup for that row: cgroup v2's BPF-based
device-cgroup program (what docker's `--gpus` flag installs) exposes whether
`/dev/nvidia*` majors are permitted for that cgroup.

**Columns (T4/`--wide` tier only, §3.3, alongside the other niche columns):**
`gpu_util%` (SM busy) and `gpu_mem` (VRAM used, summed across the entity's
attributed process(es)) in the table; `gpu_procs` (per-process VRAM/util
breakdown) in the drill-down (§3.4) only — consistent with this spec's
existing bias toward keeping niche, hardware-dependent columns out of the
default view.

**Explicitly out of scope for the plugin:** GPU-side cgroup *limits* (MPS/vGPU
partitioning, device-write throttling) — this is an observability column set,
not a GPU governance feature; a future host with GPU workloads worth
governing needs a separate spec, not a v1 add-on here.

### 3.12 ZFS awareness (v2 optional plugin, host-dependent)

This host's storage is not ZFS today (§3.0/§5's disk enumeration is plain
`/proc/diskstats` block devices, thin-provisioned virtio) — like §3.11's GPU
plugin, this v2 plugin is specified for portability to a future host, not implemented
against anything live here, and auto-detects (`[zfs] enabled = "auto"`,
§3.7/§7) by checking for `/proc/spl/kstat/zfs` (or a successful `zpool list`)
at startup rather than shipping unconditionally enabled.

**What to show, if detected:**
- **ARC hit rate** — `/proc/spl/kstat/zfs/arcstats` (`hits`/`misses`/`c`/
  `c_max`/`size` fields; rate = `Δhits / (Δhits + Δmisses)` over the sampling
  tick, the same delta-rate technique as every other rate column in this tool,
  §3.2) — shown in the system banner (§3.0) as a system-wide line, **not** a
  per-entity column, for the honest-limitation reason below.
- **L2ARC hit rate**, if an L2ARC device is configured — same file,
  `l2_hits`/`l2_misses`/`l2_size` fields; blank/absent with no L2ARC vdev
  (graceful degradation, §6.3 pattern).
- **Pool I/O** — `zpool iostat -Hp <pool> <interval> 2` (parsed, machine-
  readable `-H` form) or, where present on the running kernel,
  `/proc/spl/kstat/zfs/<pool>/io` — read/write bandwidth and IOPS per pool,
  banner-level, analogous to the per-block-device disk line (§3.0) but keyed
  by pool name instead of `maj:min`.

**Honest limitation — document this, don't paper over it** (the same posture
this spec already takes for the cgroup-v2 network-accounting gap, §3.2): the
ARC is a single **global** cache, not partitioned per cgroup or per dataset —
there is no per-container "ARC hit rate for *this* container" for this tool
(or any tool) to read; it does not exist. Separately, and more fundamentally
for this tool's per-cgroup model: **ZFS I/O largely bypasses the standard
block-cgroup (`io.*`) attribution path** this spec relies on for every other
filesystem (§3.2's `io.stat` columns, §5's `io.max`/`io.weight` rows) — ZFS
manages its own vdevs and issues I/O through its own scheduler rather than
routing consistently through the block layer's cgroup-aware submission path
the way a device-mapper- or plain-block-device-backed filesystem does, so
**per-cgroup ZFS I/O accounting is not possible** with this tool's existing
`io.stat`-based columns; those columns read zero/absent for ZFS datasets even
while real I/O is happening (glossary entry, §3.10).

---

## 4. Functional specification — v2/v3 (explicitly OUT of v1 scope)

Everything below is specified now so the v1 architecture doesn't foreclose it, but
**none of it ships in v1**. §4.1–§4.5 and §4.7 are v2, gated behind explicit
privilege/inspection flags as applicable. Mutating actions use a global
`--admin` CLI flag (refusing to even show the action's hotkey as available
otherwise) and require an explicit confirmation dialog that **displays the exact
command about to run** before executing it — directly modeled on `lazydocker`'s
gated-action pattern (§2.3). §4.6 is v3. The daemon's collector/history side is
read-only, and any mutating action it exposes reuses the identical
§4.1–§4.4/§4.7 gating, just fronted by a different transport.

### 4.1 Update container — pull/update flow

Two flows, mapped by whether the container belongs to a detected compose/ciu
stack (§4.3) or not — which bucket an as-yet-unclassified container falls into
is still tracked as open (§10 item 7), but for a container that's
unambiguously one or the other, the flow is:

- **Compose/ciu-managed** — the `stop → pull → up` cycle through the stack's
  own orchestration rather than a bare `docker run` reconstruction: `docker
  compose -f <file> stop <service> && docker compose -f <file> pull <service>
  && docker compose -f <file> up -d <service>`, or, when the container is
  ciu-gated (§4.3), the equivalent `ciu` invocation for that stack/phase so
  ciu's own hooks (`pre_compose`/`post_compose`, secret re-materialization)
  run instead of being bypassed — shown for confirmation exactly as it will
  execute, service name and all.
- **Unmanaged (bare `docker run`, e.g. Wings-launched Soulmask containers)** —
  shows the exact `docker pull <image> && docker stop <cid> && docker run
  <reconstructed args>…` sequence for confirmation; reconstructing `run` args
  from `docker inspect` faithfully (including `cgroup_parent`, mounts, env,
  restart policy) is the hard part — this tool has no compose file to fall
  back to for a Wings-managed container the way it does for a ciu stack, which
  is exactly why §10 item 7 stays open rather than being closed by this
  section.

Detection of which flow applies reuses §4.3's stack/phase detection rather
than duplicating it.

### 4.2 Start / stop / restart / kill

- **Start / stop / restart** a container or a systemd-managed slice/service —
  `docker start|stop|restart <cid>` or `systemctl start|stop|restart <unit>`,
  shown before running, same confirmation pattern.
- **Kill** (the `k` hotkey reserved-but-unbound in v1, §3.9/§8) — becomes live
  under `--admin`, following the identical show-command-then-confirm
  discipline as every other action in this section; no exception is carved
  out for kill just because it's destructive — the pattern *is* the safety
  mechanism, not a hotkey-specific gate. Two distinct commands depending on
  entity kind, and the dialog names which one it's about to run rather than
  presenting a single vague "kill":
  - Container: `docker kill <cid>` (SIGKILL, immediate — distinguished in the
    dialog from `docker stop <cid>` above, which is SIGTERM + grace period;
    an operator reaching for `k` on a hung container that isn't responding to
    `stop` needs to see that distinction, not guess which one they pressed).
  - Bare cgroup / systemd-managed entity with no docker JOIN: `systemctl kill
    <unit>` (signal defaults to systemd's own `KillSignal=`, shown) — falling
    back to a raw `kill -9 <pid>` **only** for a leaf process an admin
    manually placed outside any managed unit (the drill-down process list,
    §3.4 item 6, already identifies exactly this case).

### 4.3 CIU-managed stack integration

This host already runs several containers through `ciu` (`ciu/docs/CIU.md`) —
single-stack deploys via `ciu -d <stack>` render `ciu.compose.yml`, and
multi-stack sequencing (`ciu-deploy`) orders stacks into numbered
`[deploy.phases.phase_<N>]` tables (`ciu/docs/CIU-DEPLOY.md` spec item S7.1,
executed in **numeric**, not lexicographic, order — `phase_1` before
`phase_10`). None of this is currently visible to `groop` as anything other
than an ordinary `com.docker.compose.project`/`service`-labelled container
with the anchored `^<project>-<env>-<name>$` naming ciu already enforces
(`CIU-DEPLOY.md` S7.8) — enough to resolve a friendly name (§3.1 point 3) but
not enough to know "these 14 containers are one stack, mid-deploy, in phase
2."

**Detection.** A container is ciu-managed if it carries the proposed label
schema below (once ciu ships it) or, as a fallback usable *today* without any
ciu change, if its `com.docker.compose.project` label matches a directory
name under the repo's known stack roots and its name matches ciu's anchored
pattern — a heuristic, not a guarantee, and flagged as such in the UI (an
"(inferred)" marker vs. a label-confirmed match).

**Grouping/selection.** The container view (§3.1a) gains an optional
group-by-stack mode (own hotkey, or a `[columns]`-style profile) that clusters
rows under a stack header, sub-clustered by phase number when `ciu-deploy`
phase data is available — mirroring `ciu-deploy`'s own numeric phase ordering
so the grouping reads the same way an operator already reasons about deploy
order. A whole-stack or whole-phase multi-select (extending the existing
tag/untag mechanism, §3.5, from "tag for chart overlay" to "tag for a batch
action") lets an operator start/stop/restart every container in a stack or
phase in one confirmed action.

**Gating ops through ciu.** For any v2 mutating action (§4.1, §4.2) on a
ciu-detected container, the shown-and-confirmed command is the `ciu`/`docker
compose` invocation from §4.1's compose/ciu-managed flow, never a bare
`docker` command against an individual container in the stack — bypassing
ciu would desync its rendered `ciu.compose.yml`/secret-store state from what's
actually running, a self-inflicted Finding-D-class drift bug in ciu's own
domain. This is a hard rule, not a preference: the tool refuses, with an
explanation rather than a silent fallback, to offer the bare-docker action on
a ciu-managed container.

**Proposed label schema (suggested `ciu` spec addition, not yet implemented
there — flag for the `ciu` maintainer; this is `groop` asking `ciu` for a
small favor, not `groop` inventing its own parallel labeling scheme):**

| Label | Value | Purpose |
|---|---|---|
| `ciu.managed` | `"true"` | Unambiguous "this container went through the ciu pipeline" marker — replaces today's project/name-pattern heuristic above with a guarantee. |
| `ciu.stack` | stack directory name (e.g. `infra/redis-core`) | Groups containers belonging to one `ciu -d <stack>` invocation, independent of `com.docker.compose.project`'s own derived (not authoritative) naming — `ciu` already knows the canonical stack path. |
| `ciu.phase` | `phase_<N>`, empty when not `ciu-deploy`-managed | The `[deploy.phases.*]` table a stack belongs to (`CIU-DEPLOY.md` S7.1), letting a viewer group/sort by deploy order without re-parsing `ciu.global.toml`. |

Applying these three labels is a small addition at ciu's own compose-render
step (`ciu/docs/CIU.md`'s 17-step pipeline, S8.3 step 13) — set once per
service from data ciu already has in hand (its own `-d <stack>` argument and,
when invoked via `ciu-deploy`, the active phase name), not a `groop`-side
reverse-engineering exercise. Until `ciu` ships it, `groop` uses the
inference heuristic above and marks inferred rows as such.

### 4.4 Live band adjustment

Step `memory.high` up/down live (à la `soulmask-mempress.sh`'s calibration
squeeze), via `systemctl set-property --runtime` (never a raw write, per the
ownership rule in `MEMORY-ARCHITECTURE.md` §5.0b) so the adjustment is
Finding-D-safe by construction; shows the exact `set-property` command and the
before/after value.

### 4.5 Privileged daemon / read broker (v2)

The v2 daemon is a privileged **read broker**, not just a background history
collector. It runs the same collector/model layers (§6.1) once as root, then
exposes a controlled read-only subset to local clients that should not run as root
and should not need direct Docker socket access.

Concretely:

- Runs as a systemd unit (`groop-daemon.service`) with one instance per host.
- Listens on a local Unix socket, not TCP, for v2. The socket group is `groop`,
  mode `0660`.
- Read-only API is available to members of the `groop` group.
- Mutating API is disabled unless the daemon is started with an admin capability
  flag and the client is authorized separately.
- The daemon never exposes arbitrary file reads, arbitrary command execution, or
  arbitrary sysfs writes. The client must be unable to ask for "run this command
  as root."
- Every field in the API carries source and sensitivity metadata from the metric
  registry (§3.2).
- Every mutation is audit-logged with user identity, operation, old value, and
  new value.

Client modes:

- **standalone-degraded:** v1-style direct reads by the local TUI, with root-only
  data missing.
- **attached-full-read:** non-root client attached to the daemon gets the
  daemon-approved read-only subset, including root-only fields the daemon is
  allowed to broker.
- **admin:** authorized client can request the explicitly gated v2 mutating
  actions; confirmation and audit logging still apply.

Root-only or privileged data classes the daemon brokers:

- zswap debugfs counters: `written_back_pages`, reject counters,
  `decompress_fail`, pool limit hits;
- full process metadata that may be hidden by `hidepid`, ptrace restrictions, or
  procfs permissions;
- Docker/container metadata without giving the client direct Docker socket access;
- systemd origin/drift reads and, later, tightly scoped set-property actions;
- DAMON state;
- BPF maps and provider status;
- host file/log metadata if file inspection is explicitly enabled.

The daemon also owns long-lived root state that must not belong to an ephemeral
TUI: BPF programs/maps, optional DAMON control state, and persistent history.
Any frontend attaches later and sees history, not just a live tail: on connect, a
frontend requests a time range (or "live, no backfill") and the daemon serves
ring-buffer frames (§3.5's scheme, now owned by the daemon instead of a TUI
process) followed by a live stream.

Retention caps by max size AND max age are configurable (`[history.daemon]` in
config, §3.7/§7). The daemon prunes the oldest downsampled frames once either the
on-disk store exceeds a byte budget or a frame's age exceeds the configured
retention window. Both caps must hold simultaneously.

zstd compression of recordings is planned, not implemented in v1. The JSONL
per-frame format (§3.8) compresses well; streaming zstd is an early follow-up once
unattended recording or daemon storage needs it.

The Textual TUI gains a `--attach <socket>` mode as the daemon's first consumer,
functionally identical to live mode from the operator's perspective (§6.1's
UI-layer contract doesn't change) but no longer running its own collector sweep.

### 4.6 Web UI (v3, further out than the daemon)

The daemon in §4.5 is what makes this possible without a rewrite: a small HTTP
server — a *third* frontend, alongside the v1 Textual TUI and any future
Go/Rust TUI port (§6.4) — attached to the daemon's socket, serving a
**drill-down web interface at a URL**: the same entity table, tree, and
detail-page data (§3.4, same Sample/Frame contract, §6.1) rendered as HTML/JS
instead of Textual widgets, read-only by default. This is explicitly a
*separate* frontend process on the daemon API, not `textual-serve`/
`textual-web` (§2.5) serving the terminal app in a browser — a served-terminal
approach would tie the web experience to Textual's own layout/interaction
model and couldn't diverge into a genuinely different drill-down-first web IA
(e.g. permalink URLs per entity, `/entity/<cgroup-path-slug>`, shareable for
the exact row a teammate is asking about). Mutating actions (§4.1–§4.4, §4.7)
exposed through the web UI reuse the identical confirm-and-show-command gate
as the TUI, over the same daemon-side authorization the daemon already
enforces for TUI attach — no separate, web-specific permission model to
design or audit. Scope is otherwise deliberately unspecified beyond "possible
without foreclosing it" — no framework choice, no auth scheme, no deployment
story is decided here; this section exists so §6.1's layering decision is
justified by a concrete future consumer, not to spec the web UI itself.

### 4.7 Set-property edits

A generic "edit this limit" action for any attribute the origin-detection
algorithm (§3.4) already knows how to read, always writing via `systemctl
set-property` (persistent or `--runtime`, operator's choice, defaulting to
`--runtime` for transient docker scopes and persistent for slice units) —
never a raw cgroupfs write, so v2 cannot itself introduce a new
Finding-D-class bug.

### 4.8 File/log/content inspection (`--inspect-files`, v2)

The v2 file browser is read-only but still sensitive, so it requires explicit
`--inspect-files` or `[general] inspect_files = true`. It is never enabled by
default and is available only in root/admin or daemon-approved modes.

Content classes:

- volume paths: bind-mount source paths and named-volume `_data` directories;
- overlayfs merged view: `MergedDir` for the container rootfs, with layer
  hierarchy metadata from the drill-down still visible;
- logs: tail/follow the resolved log file or the equivalent journald query.

The browser never writes, deletes, edits, chmods, chowns, or executes. It also
does not provide arbitrary root file reads outside the paths surfaced by the
selected entity's Docker/systemd metadata.

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
| Host/interface network truth | `/proc/net/dev`, `tc -s qdisc`, `/proc/net/softnet_stat`, `/proc/net/snmp`, `/proc/net/netstat`, optional `ethtool -S` | Tier 1 provider; banner and network drill-down |
| `net_tx/rx bytes/s+packets/s` | provider interface (§3.2): v1 `/proc/<pid>/net/dev` for a representative PID, deduped by `/proc/<pid>/ns/net` inode | labels `net:NS`, `net:HOST`, `net:N/A`; `n/a` when no private netns |
| Origin of a limit | `systemctl show <unit> -p FragmentPath -p DropInPaths -p ControlGroup -p Memory{Min,Low,High,Max} -p CPUWeight -p IOWeight` | unit name = path segment ending in `.slice/.scope/.service` (§3.4 algorithm) |
| zswap module config | `/sys/module/zswap/parameters/{enabled,compressor,max_pool_percent,accept_threshold_percent,shrinker_enabled}` | system-wide, header banner |
| zswap runtime stats | `/sys/kernel/debug/zswap/{stored_pages,pool_total_size,written_back_pages,reject_*,decompress_fail,pool_limit_hit,stored_incompressible_pages}` | **root + debugfs required**; degrade (drop from header, mark estimate with `*`) if unreadable — identical to the existing monitor's `disk_sw` `*` marker |
| Swap backend classification | `/proc/swaps`, `/sys/block/zram*/{disksize,initstate,comp_algorithm,mm_stat,io_stat,bd_stat}` | v1.5 host banner/drill-down; classify zswap/zram/disk/mixed and show ZRAM host/device metrics |
| `/proc/vmstat` | system-wide `workingset_*`, `pswpin/out`, `nr_anon_pages`, `nr_file_pages`, `nr_shmem`, `nr_swapcached`, `pgscan_kswapd`, `pgsteal_kswapd` | header/system-wide row |
| `/proc/pressure/{memory,cpu,io}` | system-wide PSI | header/system-wide row |
| `/proc/sys/vm/{swappiness,watermark_scale_factor,min_free_kbytes,vfs_cache_pressure,dirty_ratio,overcommit_*}` | header, read-only display | |
| `/proc/meminfo` | `MemTotal/MemAvailable/MemFree/SwapTotal/SwapFree/SwapCached/Buffers/Cached/Shmem` | header |
| Docker metadata | `docker ps`, `docker inspect` (`-f '{{.State.Pid}}'`, `.Config.Image`, `.Name`, `.Config.Labels`, `.State.Status`) | JOIN key = cgroup path via `/proc/<pid>/cgroup` `0::` line |
| DAMON sysfs tree | `/sys/kernel/mm/damon/admin/kdamonds/{nr_kdamonds,N/state,N/pid,N/contexts/.../targets/.../pid_target,N/contexts/.../schemes/.../tried_regions/}` | full tree diagram in `DAMON-GUIDE.md` §5.1; reused verbatim |
| DAMON conflict detection | `/sys/module/damon_stat/parameters/enabled` | disable/restore only in v1.5 controlled stage (§3.6c) |
| DAMON whole-system paddr split | same sysfs tree as above, `operations = paddr`, no `pid_target` | v1.5 manual (P11) / v2 auto-start; §3.6d; feeds banner only, no per-entity attribution |
| `gpu_util%`, `gpu_mem`, `gpu_procs` (v2 optional plugin) | NVML via `pynvml`: `nvmlDeviceGetUtilizationRates`, `nvmlDeviceGetMemoryInfo`, `nvmlDeviceGetComputeRunningProcesses`/`...GraphicsRunningProcesses` | §3.11; PID→cgroup JOIN reuses the DAMON technique above; `[gpu] enabled="auto"` |
| ZFS ARC/L2ARC hit rate (v2 optional, host-dependent) | `/proc/spl/kstat/zfs/arcstats` (`hits/misses/c/c_max/size`, `l2_hits/l2_misses/l2_size`) | §3.12; system-wide banner line only, never per-entity — ARC has no per-cgroup partition |
| ZFS pool I/O (v2 optional, host-dependent) | `zpool iostat -Hp <pool> <interval> 2`, or `/proc/spl/kstat/zfs/<pool>/io` if present | §3.12; per-pool banner line, `[zfs] enabled="auto"` |
| Container volumes/overlayfs/logs (drill-down metadata) | `docker inspect` `.Mounts`, `.GraphDriver.Data.{LowerDir,UpperDir,MergedDir,WorkDir}`, `.LogPath`/`.HostConfig.LogConfig` | §3.4 items 7–8; content browsing/tailing is v2 `--inspect-files` (§4.8) |
| eBPF per-cgroup network accounting (v2, Appendix B) | custom `cgroup_skb` BPF program + per-CPU maps pinned under `/sys/fs/bpf/groop/` | provider emits same shape as v1 network providers; not in v1 |

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
│  - DAMON sysfs reads (passive); v1.5/v2 writes (control)       │
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

This same boundary is what lets §4.5's background daemon and §4.6's web UI
attach later without a UI-layer rewrite: neither is a v1/v2 deliverable, but
nothing above forecloses them, because the UI layer already only consumes
Sample/Frame objects over a boundary that doesn't care whether they arrive
from an in-process collector call, a `--replay` file iterator, or — the
daemon/web case — a socket. The collector/model split is not decoration for
this spec; it is the single design decision that makes a future daemon and a
future drill-down web interface at a URL (§4.6) additive rather than a
rewrite.

### 6.2 Sampling loop, permission modes (root vs. unprivileged)

- Single scheduler tick, default **5s** (matches `soulmask-zswap-monitor.py`'s
  default), configurable (`[general] interval` in TOML, `--interval` CLI override).
  One sweep collects **all** discovered entities in the same tick — not
  independent per-entity timers — so rate calculations across entities are
  comparable (same `Δt`) and so the tiered ring buffer's "one sample per tick"
  invariant holds without per-entity clock skew.
- DAMON passive-detection polling can piggyback on the same tick (state files are
  cheap reads); DAMON columns' own refresh cadence is governed by DAMON's `aggr_us`
  independently (§3.6b) — the table tick and the DAMON aggregation interval are
  deliberately decoupled, don't force DAMON's interval to match the table's.

**Two permission modes, both in v1 scope — non-root is REQUIRED to work, not an
afterthought (this reverses an earlier "deferred, root-only" draft of this
spec; §10 item 4 records the reversal).** `[general] permission_mode = "auto"`
(§3.7/§7) detects which applies at startup — `EUID == 0` for root mode,
otherwise membership in the `docker` supplementary group for unprivileged
mode, with `root`/`unprivileged` config/CLI overrides for forcing a mode in
testing. Exactly what each mode gets:

- **Root mode (full fidelity)** — everything specified elsewhere in this
  document: `memory.stat`/other-users'-cgroup `io.stat` reads, zswap debugfs
  (`/sys/kernel/debug/zswap/*`), `systemctl show`/`set-property` for origin
  detection and v2 mutating actions, and every v1.5/v2 DAMON sysfs write
  (passive reads don't strictly need root either, but controlled writes do). Same
  as the existing monitor's own root requirement — no regression.
- **Unprivileged mode (`docker`-group, no root) — degraded but useful, not a
  refusal to start:**
  - **Works, full fidelity:** cgroup-tree discovery and every read-only
    metric column — `/sys/fs/cgroup/**/{memory,cpu,io,pids}.{stat,current,
    max,min,low,high,weight}` and `{memory,cpu,io}.pressure` are
    world-readable on a stock cgroup2 mount (kernel-default sysfs permission
    bits on these diagnostic files; verify with `stat -c '%a %U:%G' <path>`
    on this host before shipping, per this spec's own checkable-against-
    reality convention, §0) — plus system-wide `/proc/pressure/*`.
  - **Works, full fidelity:** the docker JOIN (`docker ps`/`docker inspect`)
    — the `docker` group already grants root-equivalent docker-API access
    via the socket, unchanged from root mode; this tool adds no restriction
    on top of what docker-group membership already permits for docker
    operations specifically.
  - **Works, full fidelity:** DAMON passive-stage reads (§3.6a) — the
    `/sys/kernel/mm/damon/admin/**` state/pid/tried_regions attributes are
    kernel-default `0644 root:root`, readable by any local user on a stock
    kernel (verify live, same caveat as above).
  - **Works, read-only:** origin-detection's `systemctl show -p ...` queries
    (§3.4) — D-Bus property *reads* don't require privilege even though the
    corresponding `set-property` *writes* do.
  - **Degrades:** zswap debugfs (`0500`/`0700`, root-only by design) is
    unreadable — the banner's zswap pool/stored/ratio line falls back to the
    already-specified `/proc/meminfo` `Zswap`/`Zswapped` fields (§3.0, "so
    the ratio survives non-root mode"), losing `written_back_pages` and the
    `reject_*`/`decompress_fail`/`pool_limit_hit` drill-down-only counters
    (§5) entirely — marked unavailable, not estimated (§6.3's degradation
    convention: never silently zero-fill).
  - **Disabled, unconditionally:** every mutating action — DAMON
    controlled start/stop (§3.6c/§3.6d, sysfs writes need root), and all
    v2 `--admin` actions (§4) — regardless of what docker-group membership
    would technically permit for docker-specific ops. This is a deliberate
    tool-level policy, not a kernel-permission accident: docker-group is
    already root-equivalent for *docker* mutations, but this tool does not
    treat that as license to expose *non-docker* mutations (raw
    `systemctl set-property`, DAMON sysfs writes) without true root, nor does
    it want to become an ambient-authority menu that makes a docker-group
    compromise strictly worse. Every such hotkey is hidden/disabled with a
    "requires root" message, the same discoverability discipline as the v2
    "requires `--admin`" message (§3.9) — never a silent no-op.
  - Every degradation above follows §6.3's per-source rule: a missing source
    drops only the fields/actions it feeds, never the whole banner, row, or
    screen.

### 6.3 Graceful degradation matrix

| Condition | Behavior |
|---|---|
| BFQ scheduler not active on a device (`io.bfq.weight` file present but inert, or scheduler ≠ `bfq`) | Column shows the raw value with a footnote "BFQ inactive — weight has no effect"; never hide the column, hiding would mask a misconfiguration |
| `/sys/kernel/debug/zswap/` unreadable (non-root or `CONFIG_ZSWAP_DEBUGFS` off) | System-wide zswap header stats drop; **per-cgroup** `z_pool`/`z_eq` are unaffected (they come from cgroup files, not debugfs) — mirrors the existing monitor's `disk_sw` `*`-marked degraded estimate |
| ZRAM devices active | Banner shows zram host/device totals from `/sys/block/zram*`; cgroup rows keep zswap-only `z_pool`/`z_eq` and mark `swap_disk`/`rf_d` wording as non-zswap swap-device estimates rather than physical disk claims |
| ZRAM and non-zram swap devices active together | Banner state is `mixed`; per-cgroup backend attribution is unavailable, so drill-down explains that `memory.swap.current` cannot tell which backend holds a given cgroup's non-zswap pages |
| No `docker` binary / daemon not running | Container-centric view (§3.1a) shows an empty-state message and disables its hotkey; tree view (§3.1b) — which has no docker dependency by construction, since the tree is keyed by cgroup path, not by container (§3.1) — remains fully functional |
| `/sys/kernel/mm/damon/admin/` absent (`CONFIG_DAMON_SYSFS` not built) | DAMON columns and hotkeys are **removed from the column/action registry entirely**, not merely blank — the help screen and config validator say why |
| A `memory.stat`/`cpu.stat`/etc. field absent (older kernel) | That one field shows `—`; does not affect sibling fields or crash the sweep |
| A cgroup disappears mid-sweep (container restarted) | Same reset-detection as `soulmask-zswap-monitor.py`'s `RateTracker`: one sample of `—` rates, silent resync to the new baseline, never a bogus negative/huge rate |
| `pids`/`hugetlb`/`rdma`/`misc` controller not enabled on a given cgroup | Corresponding drill-down section shows "controller not enabled here" rather than empty-looking zeros |
| Entity has no private network namespace | `net_tx/rx` = `n/a (host netns)` (§3.2) |
| Running unprivileged (`docker`-group, no root, §6.2) | zswap debugfs stats drop (banner falls back to `/proc/meminfo` `Zswap`/`Zswapped`, §3.0); every mutating hotkey (DAMON controlled sessions, all v2 `--admin` actions) is hidden/disabled with a "requires root" message; every read-only column, PSI series, and the docker JOIN remain fully populated |
| Host has no ZFS / no GPU (default case, this host) | §3.11/§3.12 plugins stay disabled (`auto`-detected off) — no columns, no banner lines, no startup error |

### 6.4 Go/Rust port boundary (v1 design constraint, not a v1 deliverable)

v0 ships as framework-free Python with stdlib only. v1 ships as Python + Textual
for the UI, while the collector/model/metric registry remain framework-independent
and import no Textual symbols. The alternative lean-binary path is Rust +
ratatui; it is documented as the likely long-term low-overhead distribution path,
not the chosen v1 implementation. No dependency on `damo`, no dependency on this
repo's `damon-analysis` venv, per §3.6c point 6.

The two things a later compiled port needs, both already produced by v1:
1. **§5's data-source table** — the exact file-to-field mapping is language-neutral
   by construction (it's just "read this path, parse this format").
2. **§3.8's JSONL schema** — a Go/Rust collector emitting the same schema is
   drop-in compatible with the existing `--replay` mode of *either* implementation,
   so a partial port (collector only, keep the Python UI reading its JSONL via a
   pipe) is a viable intermediate step, not just "rewrite everything at once."

No plugin ABI, no gRPC, no shared library — keep this cheap until real profiling
data (from v1's own `--record` mode, naturally) shows which layer is actually worth
porting first (§10).

### 6.5 Security model

`groop` is an observability tool first. Privilege boundaries are explicit because
read access alone can expose sensitive host and container state.

- **Docker group implications:** membership in `docker` is already root-equivalent
  for Docker operations. `groop` does not make that worse by exposing v1 mutating
  actions to docker-group users; v1 remains read-only except record/replay.
- **Root read exposure:** root can read procfs metadata, cgroup files, Docker
  metadata, logs, env files, volume contents, and debugfs counters that may expose
  secrets or operational details. Daemon-brokered fields carry source and
  sensitivity metadata (§4.5).
- **File browsing risk:** read-only file browsing can still reveal tokens,
  secrets, env files, mounted volume contents, and logs. It is v2-only behind
  explicit `--inspect-files`; v1 shows paths and metadata only.
- **BPF pinned object lifecycle:** v2 BPF programs/maps are owned by the daemon or
  root helper, not an ephemeral TUI. Objects are pinned under `/sys/fs/bpf/groop/`
  and provider status exposes loaded/attached state, map path, program IDs,
  attach point, last read, packet rate, and estimated overhead.
- **Action confirmation gates:** every mutating action shows the exact command or
  sysfs operation, old value, and new value before execution, requires explicit
  confirmation, and is audit-logged when brokered by the daemon.
- **Daemon socket model:** v2 uses a local Unix socket with group `groop`, mode
  `0660`. Read-only API is available to group members; mutating API requires a
  daemon admin capability flag and separate client authorization. The daemon
  never exposes arbitrary command execution, arbitrary file reads, or arbitrary
  sysfs writes.

Target platform default: Debian 13 on `gstammtisch` is primary. Other
systemd/cgroup-v2 Linux hosts should degrade gracefully, but the project does not
promise a distro compatibility matrix before v2.

---

## 7. Config format example (`config.toml`)

```toml
# groop config — $XDG_CONFIG_HOME/groop/config.toml
# All values shown are the shipped defaults; delete a key to fall back to it.

[general]
interval = 5.0                # seconds between collector sweeps
permission_mode = "auto"      # "auto" | "root" | "unprivileged" — §6.2; auto detects EUID/docker-group
default_view = "tree"         # "tree" | "container"
default_column_profile = "triage"  # "auto" (adaptive-width, §3.3) | named profile below
inspect_files = false         # v2 only; enables explicit file/log content browser

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

[columns.profiles.triage]
list = ["name", "pressure", "ram", "cpu_pct", "psi_mem_full", "psi_io_some", "rf_d", "io_r", "io_w", "net_rx", "net_tx", "net_source"]

[columns.profiles.memory]
list = ["name", "ram", "anon", "file", "shmem", "z_pool", "z_eq", "ratio", "swap_disk", "rf_z", "rf_d", "rf_f", "pgscan", "pgsteal", "mem_events"]

[columns.profiles.network]
list = ["name", "net_rx", "net_tx", "net_pps", "net_drops", "net_retrans", "sock", "net_source"]

[columns.profiles.governance]
list = ["name", "mem_min", "mem_low", "mem_high", "mem_max", "io_max", "cpu_weight", "origin", "drift", "mem_events", "pids_current"]

[columns.profiles.damon]
list = ["name", "hot_pct", "warm_pct", "cold_pct", "idle_pct", "damon_target_pids", "damon_sample_age"]

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
paddr_enabled = false        # v2; §3.6d — whole-system kdamond feeding the banner's hot/warm/cold bar
paddr_interval_us = 400000   # 400ms sampling, DAMON-GUIDE.md §12.3's recommendation
paddr_aggr_us = 8000000      # 8s aggregation

[gpu]
enabled = "auto"    # "auto" | "on" | "off" — §3.11; auto = NVML importable AND a GPU present

[zfs]
enabled = "auto"    # "auto" | "on" | "off" — §3.12; auto = /proc/spl/kstat/zfs present

[history]
full_resolution_seconds = 14400    # 4 h ring, at [general].interval granularity
downsample_interval_seconds = 60  # 1-minute rollup buckets
downsample_retention_hours = 4

[history.daemon]
# v2/v3 background daemon only (§4.5) — no effect on the standalone v1 TUI's
# in-process ring buffer above, which stays seconds/hours-bounded as-is.
max_size_mb = 2048          # prune oldest downsampled frames once the on-disk store exceeds this
max_age_days = 30           # ...or once a frame is older than this — whichever triggers first
compression = "none"        # "none" | "zstd" (planned, not implemented — §4.5)

[hotkeys]
profile = "groop"       # "groop" | "htop" | "top" | "custom"
# overrides only consulted when profile = "custom"; see §8 for the base tables
[hotkeys.overrides]
# action = "key"
# tree_toggle = "F5"
```

---

## 8. Hotkey table

**Base action set** (fixed across all profiles — profiles remap the *key*, never
add/remove an *action*):

| Action | `groop` (default) | `htop` profile | `top` profile |
|---|---|---|---|
| Move selection | `↑`/`↓`, `j`/`k` | `↑`/`↓` | `↑`/`↓` |
| Expand/collapse tree node | `←`/`→`, `h`/`l` | `←`/`→` | `←`/`→` |
| Page up/down | `PgUp`/`PgDn` | `PgUp`/`PgDn` | `PgUp`/`PgDn` |
| Jump top/bottom | `Home`/`End` | `Home`/`End` | `Home`/`End` |
| Drill-down (full detail screen) | `Enter` | `Enter` | `Enter` |
| Back / close screen | `Esc` | `Esc` | `Esc` |
| Toggle tree ⇄ container view | `t` | `F5` (htop's own tree-toggle key) | `t` |
| Toggle wide columns (`--wide`/T4) | `w` | `w` | `1` (top's "toggle detail" slot) |
| Cycle column/job profile | `Tab` | `Tab` | `Tab` |
| Cycle sort column | `F6`, `<`/`>` | `F6` | (n/a — top uses single-letter sort below) |
| Sort by memory | — | — | `M` |
| Sort by CPU | — | — | `P` |
| Sort by name | — | — | `N` |
| Reverse current sort | `r` | `I` (htop's invert-sort key) | `R` |
| Incremental filter | `/` | `F4` | `o` (top's "other filter" slot) |
| Search / jump to match | `F3`, then `n`/`N` next/prev | `F3` | `/` (unused otherwise in top profile) |
| Tag/untag row for chart overlay (live) — play/pause (replay) | `Space` | `Space` (htop's own mark-for-multi-select key, repurposed) | `Space` |
| Open chart-overlay screen | `c` | `c` | `c` |
| Save incident snapshot bundle | `S` | `S` | `S` |
| Toggle DAMON columns / request controlled vaddr session (v1.5) | `d` (two-key confirm: `d` then `y`) — v1 shows "requires v1.5/root" | `d` | `d` |
| Open DAMON detail panel (inside drill-down, v1.5) | `D` | `D` | `D` |
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
   over a 5-minute steady-state run (`pidstat -p $(pgrep -f groop) 5` or
   equivalent), on hardware comparable to this host (8 vCPU, thin-provisioned
   virtio disk). v1 has no controlled DAMON targets and no BPF state.
2. **Memory:** total process RSS stays **under ~60 MB** at 40 entities with default
   history settings if the implementation keeps only the table plus compact recent
   history; with the full 4h numeric-array profile enabled, the accepted budget is
   the Textual/Python baseline plus §3.5's realistic **20-40 MB** history overhead.
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
7. **Metric registry semantics:** branch rows show the active mode in headers,
   never double-count subtree memory, never sum PSI/limits, and carry source
   confidence into JSONL and drill-down.
8. **Diagnostics:** sorting by `pressure` surfaces the expected top-pressure rows
   in a controlled fixture, and the findings panel explains each red row using
   the fixed rule list in §3.4a.
9. **Network labels:** host-network entities show `net:N/A` with reason
   `host netns`; private-netns containers show `net:NS`; branch aggregation is
   absent unless all children are provably private and deduplicated.
10. **Record/replay fidelity:** a `--record` run followed by `--replay` of the same
   file reproduces an identical rendered table for every recorded tick (byte-for-
   byte identical formatted cell values, modulo terminal-width-dependent layout).
11. **Packaging:** `pipx install groop` (from a local sdist/wheel in absence of a
   published package) succeeds cleanly and registers a `groop` console-script
   entry point; running it with no config file present uses documented defaults
   without error.
12. **v2 gating:** with `--admin` absent, every v2 action's hotkey is either unbound
    or shows a "requires --admin" message; with `--admin` present, every v2 action
    shows the exact command it will run and requires an explicit confirmation
    keypress before executing it. File/content browsing additionally requires
    explicit `--inspect-files`.
13. **Unprivileged-mode smoke test (§6.2):** launched by a `docker`-group,
    non-root user (`permission_mode = "auto"`, no `sudo`), the tool starts
    without a password prompt or crash, shows the full cgroup tree with
    populated memory/CPU/IO/PSI columns, correctly JOINs running docker
    containers, and hides/disables every mutating hotkey (DAMON v1.5 control,
    all v2 `--admin` actions) with a "requires root" message — matching §6.2's
    unprivileged-mode specification exactly, not a reduced ad hoc subset of it.
14. **MEASUREMENTS.md gates:** BPF cannot be on by default until the seven-step
    BPF overhead acceptance list in §10 item 2 has been run and recorded in
    `MEASUREMENTS.md`. DAMON defaults cannot be raised or enabled by default until
    the DAMON overhead gate in §10 item 3 has been run and recorded there.

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
   `[thresholds]` keying in §7. **Resolved sub-question — identity vs. display
   name:** the underlying row *identity* (the JOIN key) stays purely path-derived
   regardless of how the tier question above is eventually decided — the cgroup
   path, and for containers the docker/Wings-assigned UUID, remains the canonical
   reference; that was never actually in question. What *was* open — showing an
   operator something more useful than a bare UUID — is now closed by the
   friendly-name resolver chain specified in §3.1 (Wings local API → panel remote
   API → compose/ciu labels → UUID fallback), built from a source-level check of
   wings (`develop`+v1.11.x, `server/configuration.go`'s `Configuration.Meta`) and
   the panel (`1.0-develop`) confirming the display name reaches the node but is
   not persisted anywhere readable on disk — hence the API-call chain rather than
   a local file read. That research is done; nothing further to decide on the
   naming half of this question.
2. **RESOLVED — network release path and BPF gate:** v1 uses Tier 1 host/interface
   truth plus Tier 2 netns approximation with source labels and the provider
   interface; v2 adds the BPF provider under daemon/root-helper ownership
   (Appendix B). BPF must not be enabled by default until a `MEASUREMENTS.md`
   network measurement section records the accepted seven-step gate:
   1. Baseline `iperf3` or equivalent without BPF.
   2. Same traffic with BPF loaded.
   3. Measure throughput, CPU softirq, provider CPU, packet loss, and map read
      cost.
   4. Test UDP-heavy traffic resembling the latency-critical workload.
   5. Test container churn: start/stop 50 short-lived containers and verify stale
      cgroup IDs age out.
   6. Test host-netns services and prove the UI labels them correctly.
   7. Test branch aggregation: several containers under `besteffort.slice` should
      sum correctly.
   Decision source: review + operator interview 2026-07-08.
3. **RESOLVED — DAMON default gate:** active DAMON moves to v1.5; paddr
   auto-start/manual host mode moves to v2. Defaults cannot be raised or enabled
   until a DAMON overhead test is recorded in `MEASUREMENTS.md`. Validation plan:
   1. Baseline: `pidstat`/`ps` CPU% + RSS for `kdamond0` with **zero**
      control-stage targets active (only passive detection, §3.6a) over a
      10-minute idle window.
   2. Ramp: add one control-stage `vaddr` target at a time (1, 2, 3, 4 — today's
      cap) on real containers, each for a 10-minute steady-state window,
      recording per-`kdamond<N>` CPU%, `aggr_us` tick jitter, and any measurable
      added latency on the monitored container's own workload (reuse the game
      server's login-latency harness, `MEASUREMENTS.md` M4, rather than building
      a new one).
   3. Cross-check against `MEASUREMENTS.md` M2/M4's existing DAMON-adjacent
      numbers (`vmtouch`/paddr session cost) — some cost baseline for this host
      already exists.
   4. GO/NO-GO gate for raising `max_concurrent_targets` above 4: total kdamond
      CPU stays under the tool's own acceptance-criteria budget (§9 item 1, <5%
      of one core) **cumulatively with** the table-rendering loop's own cost, not
      as an independent budget.
   Decision source: review + operator interview 2026-07-08.
4. **RESOLVED — non-root mode is REQUIRED in v1, not deferred.** A developer in
   the `docker` group but without root gets a degraded-but-useful view rather
   than a hard refusal to start: full cgroup-tree discovery and every read-only
   metric column (`/sys/fs/cgroup/**` stat/pressure files and `/proc/pressure/*`
   are world-readable on a stock cgroup2 mount), full docker JOIN (the `docker`
   group already grants root-equivalent docker-API access), and DAMON
   passive-detection reads all work unprivileged; zswap debugfs stats and every
   mutating action (DAMON controlled sessions, all v2 `--admin` actions) require true
   root regardless of docker-group membership and are hidden/disabled with a
   "requires root" message rather than silently no-op'ing. Full specification of
   exactly what degrades lives in §6.2 (rewritten for this decision) and the new
   unprivileged-mode row in §6.3's degradation matrix, with §9 item 13 as the
   acceptance test. This closes what an earlier draft deferred entirely; nothing
   further to decide.
5. **Confirmed, not actually open:** column priority order (the T0–T4 tier table,
   §3.3) is fully configurable in TOML today via `[columns]` (§7) — an operator
   can override the tier-to-width mapping or define a fixed-list profile that
   ignores tiering altogether. The only genuinely open part is whether the
   *shipped defaults'* width breakpoints are well-calibrated for real SSH/tmux
   usage, which is a tune-the-defaults task, not a design blocker — downgraded
   from a question to a backlog item, not something implementation needs to
   resolve before shipping v1.
6. **RESOLVED — record file size/compression:** v1 writes plain JSONL (§3.8).
   Streaming zstd compression is an early follow-up after v1 if unattended
   recording or daemon storage needs it; see §3.5 cost math. Decision source:
   review + operator interview 2026-07-08.
7. **v2 "update container" command reconstruction** (§4): reconstruct a bare
   `docker run` from `docker inspect`, or shell out to `docker compose`/the
   repo's `ciu` tool when a compose file is the source of truth for that
   container? No decision — depends entirely on which containers an operator
   actually wants this action for (Soulmask via Wings never goes through
   compose; the dstdns stack always does).
8. **Go/Rust port trigger condition:** §6.4 deliberately avoids committing to
   *when* to port, or which layer first. Suggest revisiting only once `--record`
   from real v1 usage shows where CPU/memory actually goes — don't pre-optimize.
9. **RESOLVED — naming:** the chosen name is `groop`; see Appendix A. Decision
   source: review + operator interview 2026-07-08.
10. **RESOLVED — drift severity:** any drift is visible as a warning; red/alert is
   reserved for drift that changes the effective protection of a protected
   workload (§3.4). Decision source: review + operator interview 2026-07-08.
11. **RESOLVED — target platform:** Debian 13 on `gstammtisch` is primary;
   graceful degradation elsewhere, no distro matrix before v2 (§6.5). Decision
   source: review + operator interview 2026-07-08.

---

## Appendix A — Naming

Chosen name: **`groop`**. Use it for the package name, console-script/binary,
config path (`$XDG_CONFIG_HOME/groop/config.toml`), daemon socket group
(`groop`, mode `0660`), and BPF pin path (`/sys/fs/bpf/groop/`).

Considered alternatives:

- `slicetop` — clear systemd-slice emphasis, but too narrow for containers,
  network, and daemon work.
- `cgsight` — accurate cgroup-forensics direction, but less memorable.
- `ztop` — good zswap hint, but too close to a generic `top` clone.
- `stier` — tier/governance flavor, but less obvious to new operators.

---

## Appendix B — Network provider tiers and BPF design

Referenced from §3.2 and §5. v1 ships Tier 1 host/interface truth and Tier 2
netns approximation. v2 adds Tier 3 BPF without changing table columns, JSONL
schema, or drill-down contracts because all providers implement the §3.2 provider
interface.

### B.1 Tier 1 — host/interface truth (v1)

Always include this in v1:

- `/proc/net/dev`: rx/tx bytes, packets, errors, drops per interface;
- `tc -s qdisc show`: fq_codel backlog, drops, overlimits if available;
- `/proc/net/softnet_stat`: host receive backlog drops/time_squeeze;
- `/proc/net/snmp` and `/proc/net/netstat`: TCP retransmits, resets, UDP errors;
- optional `ethtool -S` where available, never required.

This catches host-level network trouble even when per-cgroup attribution is weak.
The UI may label this `net:HOST` for entity rows and should keep host/interface
health visible in the banner and network drill-down.

### B.2 Tier 2 — netns approximation (v1)

The v1 entity-level approximation is:

- Read `/proc/<pid>/net/dev` for a representative process.
- Deduplicate by `/proc/<pid>/ns/net` inode.
- Treat the value as per-network-namespace, not per-process and not exact
  per-cgroup accounting.
- Show `n/a (host netns)` for host-network containers and bare host services.
- Do not aggregate branch cgroups from this source unless every child has a
  distinct private netns and the aggregation code can prove it.

Source labels:

- `net:BPF` — exact cgroup BPF provider;
- `net:NS` — network namespace approximation;
- `net:HOST` — host interface only;
- `net:N/A` — not attributable.

### B.3 Tier 3 — custom cgroup_skb eBPF provider (v2)

Given the checked host config, a BPF backend is realistic here. Recommended
design:

- A root-owned network provider loads one ingress and one egress `cgroup_skb`
  program.
- Attach as high in the cgroup tree as possible, ideally the unified cgroup root,
  so descendants are covered without per-container attach churn.
- The BPF program increments counters and always returns allow/pass.
- Use per-CPU maps to reduce contention on high packet rates.
- Pin programs and maps under `/sys/fs/bpf/groop/`.
- Userspace maps cgroup IDs back to cgroup paths during the normal cgroup tree
  walk, then aggregates leaf counters to branch rows.
- Do not store path strings in BPF. Path mapping belongs in userspace. Validate
  the exact cgroup ID to path mechanism during implementation.

Minimum map shape:

```text
key:
  cgroup_id: u64
  direction: ingress|egress
  family: ipv4|ipv6|other
  proto: tcp|udp|icmp|other

value:
  bytes: u64
  packets: u64
```

Optional later map dimensions:

- local port bucket;
- remote port bucket;
- TCP retransmit count from tracepoints;
- socket cookie for short-lived flow correlation;
- drop reason where the kernel exposes stable tracepoints.

### B.4 BPF limitations to document in UI/help

The BPF path is better than netns counters, but it is not a wire tap:

- `cgroup_skb` is socket/cgroup oriented, not a universal packet tap;
- it does not replace host interface counters;
- it may not account traffic generated by kernel subsystems in the way an
  operator intuitively expects;
- ARP and some non-IP traffic are outside the useful accounting scope;
- forwarded/bridged traffic not associated with a local socket may need TC/XDP
  instrumentation instead;
- per-packet map updates have real cost and must be benchmarked.

The UI must describe this as **per-cgroup socket traffic**, not all bytes
physically observed on the NIC.

### B.5 systemd IPAccounting provider

`IPAccounting=` is useful for systemd-native services and slices. Setting
`IPAccounting=yes` on a unit makes systemd expose counters through
`systemctl show -p IPIngressBytes -p IPEgressBytes -p IPIngressPackets -p
IPEgressPackets <unit>`. It is an optional provider for systemd units only. It is
not enough for the product's hard cases because Docker/Wings scopes and post-hoc
cgroup discovery are not reliably covered across systemd versions.

### B.6 BPF ownership and provider status

BPF is never owned by the ephemeral TUI. v2 introduces either the root daemon
(preferred, §4.5) or a small root helper with explicit `start`/`stop`/`status`
commands. Multiple TUI sessions read from the same provider.

Provider status is visible in the UI and JSONL metadata:

- loaded;
- attached;
- map path;
- program IDs;
- attach point;
- last read;
- packet rate;
- estimated overhead.

### B.7 Traffic classes

Traffic classes are configuration, not Soulmask-specific code:

- `interactive_admin`;
- `latency_critical`;
- `service_control`;
- `background`.

The TUI observes and explains these classes first. Actual prioritization via
tc/qdisc/nftables/DSCP is future governance work, not v1/v2 core behavior.
