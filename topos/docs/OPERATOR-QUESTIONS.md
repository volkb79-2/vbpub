# Operator questions and the 95% product target

Status: product acceptance draft, 2026-07-15

The "95%" claim is a product direction, not a literal promise to replace every
Linux diagnostic utility. Topos succeeds when it answers the common first-triage
and ownership questions in one coherent model, with fewer command chains and
without hiding source, privilege, freshness, or history gaps. Specialist tools
remain appropriate for flame graphs, packet capture, filesystem forensics,
database internals, and unbounded log search.

For release acceptance, a named scenario below should be answerable in one main
view plus at most two drill-downs, or one bounded CLI/MCP query. Every historical
answer must state its observed window and coverage; every live answer must state
its source and freshness.

## Evidence from real investigations

### Authentik OOM and restart diagnosis

The dstdns investigation combined host `ps`, `docker stats`, `docker inspect`,
`docker top`, recent `docker logs`, `docker events`, restart/OOM/exit state,
health-check configuration, and cgroup memory limits. It had to distinguish a
bounded OOM recycle from a continuing leak and from overlapping expensive health
checks.

Topos should answer this as one lifecycle incident:

- stable workload identity (compose/CIU service) and concrete container
  incarnation ID;
- last observed memory/CPU/PSI/refault trend before exit;
- OOM, exit code, restart count, health state, and current replacement;
- effective memory/swap limits and owning slice beside observed use;
- bounded, redacted event/log evidence around the transition;
- a Previous instance/Recent exit link when the old cgroup/container has
  disappeared, never a fabricated current row or a lost incident.

### gstammtisch sizing and cgroup squeeze

The host-governance work combined cgroup files, `systemctl show`, Docker
identity, `free`, `/proc/meminfo`, `/proc/vmstat`, PSI, zswap/debugfs, swap
devices, `iostat`, device scheduler/caps, DAMON, and stepped `memory.high`
measurements. The decisive questions were not merely "how much RAM?", but:

- which workload owns resident, compressed, and disk-backed memory;
- whether `memory.min/low/high/max` are effective through every ancestor;
- whether `daemon-reload` changed live governance or its provenance;
- where refaults came from and where the squeeze cliff occurred;
- whether best-effort IO/CPU work harmed the protected game workload;
- whether an apparent high PSI/refault rate was expected tier isolation.

Topos's target is a saved sizing preset and report that pairs each squeeze step
with workload and protected-neighbor evidence, then recommends nothing without
showing the measured boundary and restore result.

### Build/repack interference

The release investigation repeatedly polled `ps` for CPU/RSS/VSZ/elapsed time,
resolved process cgroups, inspected container CPU/memory/block IO, checked slice
governance, and correlated device pressure. Topos should replace that polling
chain with a process projection that identifies the process, its container and
slice owner, its CPU/memory/IO rates, and the device/slice cap it is approaching.

## Coverage checklist

| Operator question / familiar tool | Topos target |
|---|---|
| `top`, `htop`: what is hurting now? | Verdict-first host summary, active findings, and ranked responsible branches/entities. |
| `mpstat`: is one CPU, steal, iowait, IRQ, or softirq the bottleneck? | Per-CPU busy/user/system/iowait/steal/irq/softirq, imbalance finding, and history; retain the aggregate banner. |
| `ps`, `pidstat`: which process is responsible? | PID/PPID/user/state/elapsed/threads, CPU, RSS/VSZ/swap, faults, I/O, voluntary/involuntary context switches, cgroup/unit/container/CIU ownership, and bounded history over the union of CPU-hot and I/O-hot candidates. |
| `systemd-cgtop`, `systemd-cgls`: which branch owns it? | Canonical cgroup hierarchy, explicit branch semantics, effective limits, origin/drift, and sibling-local ranking. |
| `docker stats`, `docker ps`, `docker top` | Flat container projection plus decorated hierarchy nodes and process ownership, without double-counting. |
| `docker inspect`, `docker events` | Structured lifecycle, health, restart/exit/OOM, limits, mounts and stable logical identity; recent exited incarnations remain discoverable as Previous instance/Recent exit links. |
| `free`, `vmstat`, `swapon` | Available/cache/swap decomposition, paging/reclaim/run-queue/context-switch rates, backend-aware zswap/zram/disk behavior. |
| `df -h`, `df -i`, `mount` | Mount byte/inode capacity, read-only/error state, backing device and recent growth; hand off to `du`/filesystem tooling for unbounded path attribution. |
| `iostat`, cgroup IO files | Per-device throughput/IOPS/utilization/queue/await where derivable, cgroup I/O rates/PSI/cap saturation, and lightweight per-process read/write/cancelled-write rates. Exact process-to-device attribution is an optional privileged capability. |
| `sar`, `atop` | Immediate daemon-history series and window summaries with coverage, gaps, resets, source and persistence state. |
| `ss`, `lsof -i` | On-demand listening socket inventory mapped to process/cgroup/container; address, protocol, port, namespace, queue and connection count. |
| `lsof`, `/proc/*/fd`, file limits | On-demand per-process file-descriptor use versus limit, host file-table pressure, owning workload and coverage/permission limits; no arbitrary content browsing. |
| `ps` state/wchan, blocked-task checks | D-state/blocked-process inventory, elapsed blocked time where available, cgroup owner and correlated device I/O pressure; do not claim an exact root cause from state alone. |
| `ip -s`, `tc`, `ethtool` | Interface rates/errors/drops/backlog plus source-labelled per-entity attribution where a provider can prove it. |
| `journalctl`, `dmesg`, container logs | Findings-driven, time-aligned, strictly bounded and redacted evidence; no continuous arbitrary-log ingestion by default. |
| `systemctl status`, Docker health | Failed/unhealthy/restarting state and duration as lifecycle facts, correlated with resource findings. |

Per-listening-port byte/packet attribution is not available from ordinary
procfs socket inventory. It requires an optional privileged network provider
(normally eBPF) and must retain direction, namespace, protocol, and source
limitations. The first port feature should therefore be on-demand listener and
connection ownership; traffic per port is a separately measured follow-up.

## Release scenario set

1. Identify current host pressure and its owning cgroup branch.
2. Detect per-CPU imbalance, sustained steal/iowait, or IRQ/softirq saturation.
3. Identify the hottest CPU or I/O process, its unit/container/CIU owner, and
   when the burst occurred.
4. Explain an OOM-killed container after Docker has recreated it.
5. Detect a restart/health-check loop and show bounded evidence.
6. Show effective cgroup protection and a live-vs-systemd drift.
7. Locate a `memory.high` squeeze/refault cliff without harming a protected neighbor.
8. Distinguish zswap refault activity from disk-backed swap pressure.
9. Attribute device saturation to a cgroup and, when enabled, a process.
10. List a workload's listening ports and owning processes/namespaces.
11. Answer mean/p95/max/delta/integral over a completed, partially covered, and evicted window.
12. Start with no daemon and clearly show local degraded source/permissions.
13. Attach to a daemon and immediately backfill history rather than waiting for new samples.
14. Preserve a recently exited entity as a Previous instance/Recent exit link while keeping current totals honest.
15. Produce the same bounded answer through TUI, CLI JSON, and MCP without divergent aggregation logic.
16. Identify sustained memory growth, distinguish anonymous/file/cache/swap
    behavior, and name the responsible process and workload.
17. Detect a filesystem approaching byte or inode exhaustion—or becoming read-
    only—name its mount/backing device and recent I/O owners, then provide an
    explicit handoff boundary for directory-level forensics.
18. Detect cgroup `pids.max`, host PID/file-table, or per-process file-descriptor
    exhaustion before allocation failures and identify the owner.
19. Find processes stuck in uninterruptible/block-I/O wait, show how long and
    where they are owned, and correlate rather than conflate them with device
    pressure.
20. Detect interface saturation, drops, errors or retransmits and identify the
    responsible namespace/workload where the active provider can prove it.
21. Diagnose a failed or flapping systemd service with no container, including
    unit lifecycle, recent resource history and bounded journal evidence.
22. Compare bounded windows immediately before and after a deployment, restart
    or configuration event for CPU, memory, I/O, pressure and health regressions.
23. Detect that observation itself is incomplete—stale daemon data, permission
    loss, provider failure, sampling gaps, candidate eviction or expired
    history—instead of reporting a false healthy state.

This list is deliberately versioned. Add a scenario when a real investigation
requires a repeated command chain; do not inflate the target with every flag of
every utility.
