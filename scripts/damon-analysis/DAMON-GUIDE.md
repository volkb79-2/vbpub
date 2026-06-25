# DAMON & DAMO — Comprehensive Guide

> **Generated for:** Linux 7.0.10+deb13-amd64, damo v3.2.9  
> **Sources:** Kernel docs (docs.kernel.org/7.0/mm/damon), damo USAGE.md, damonitor.github.io, deepwiki  
> **DAMON version on this system:** 7.0

---

## 1. What is DAMON?

**DAMON** (Data Access MONitoring) is a Linux kernel subsystem for efficient,
lightweight, and scalable data access monitoring and access-aware system
operations. It has been in mainline Linux since v5.15 (2021).

Key design properties:
- **Accurate** — DRAM-level memory management precision
- **Lightweight** — usable online in production; overhead is configurable
- **Scalable** — same properties regardless of total memory size
- **Tunable** — user controls the accuracy/overhead trade-off
- **Automated** — DAMOS (DAMON-based Operation Schemes) can auto-apply memory management actions

**damo** (Data Access Monitoring Operator) is the official user-space CLI tool
for controlling DAMON, recording results, and visualizing access patterns.

Real-world adopters include AWS Aurora Serverless, SK hynix HMSDK, Meta, and
multiple academic projects. Most major distros (Debian, Fedora, Arch, Android,
Amazon Linux, CentOS, openSUSE, Oracle) ship DAMON-enabled kernels.

---

## 2. Key Concepts

### 2.1 Execution Model

- A **DAMON context** holds all monitoring parameters (intervals, target
  address ranges, schemes).
- A kernel thread called **kdamond** executes each context.
- Multiple kdamonds can run in parallel for different types of monitoring.

### 2.2 Three-Layer Architecture

```
┌─────────────────────────────────┐
│  Modules (sysfs, reclaim, lru_sort, stat)  │  ← user/kernel interfaces
├─────────────────────────────────┤
│  Core (monitoring, DAMOS)                │  ← core logic
├─────────────────────────────────┤
│  Operations Set (vaddr, fvaddr, paddr)   │  ← address-space implementations
└─────────────────────────────────┘
```

**Operations Sets:**
- `vaddr` — Monitor virtual address spaces of specific processes (auto-detects
  three regions: heap, mmap-ed area, stack, excluding two biggest gaps)
- `fvaddr` — Monitor fixed virtual address ranges (user-specified)
- `paddr` — Monitor the physical address space of the entire system

### 2.3 Access Frequency Monitoring

DAMON checks whether each sampled page was accessed every **sampling interval**
and aggregates the counts. After each **aggregation interval**, it reports
results and resets counters:

```
while monitoring_on:
    for page in monitoring_target:
        if accessed(page):
            nr_accesses[page] += 1
    if time() % aggregation_interval == 0:
        report(monitoring_target, nr_accesses)
        nr_accesses = 0
    sleep(sampling interval)
```

### 2.4 Region-Based Sampling

To keep overhead bounded, DAMON groups adjacent pages with similar access
frequencies into **regions**. Only ONE random page per region is checked each
sampling interval. The overhead is controlled by setting **minimum** and
**maximum number of regions**.

### 2.5 Adaptive Regions Adjustment

Every aggregation interval, DAMON:
1. **Merges** adjacent regions with similar `nr_accesses` (if the sum fits
   within the size budget)
2. **Splits** regions (if total count < max regions) into 2-3 sub-regions

This keeps monitoring quality high as access patterns change dynamically.

### 2.6 Age Tracking

Each region has an `age` counter. Every aggregation interval:
- If the region's size or `nr_accesses` changed significantly → reset age to 0
- Otherwise → increment age

Age captures **how long the current access pattern has persisted** — critical
for distinguishing "cold because never used" from "cold because temporarily
idle".

### 2.7 Access Rate vs Access Hz

- **Access rate** = `nr_accesses / max_possible_nr_accesses` × 100 (percentage)
  - `max_possible_nr_accesses` = `aggregation_interval / sampling_interval`
- **Access hz** = `nr_accesses / aggregation_interval` (hertz)

### 2.8 Access Temperature

A derived metric (used by damo reports) representing hotness:
- Weighted sum of access frequency and age
- If access frequency is 0% → temperature is multiplied by −1 (colder regions
  get more negative as they age longer)
- Sorting by temperature (ascending) puts coldest regions first

---

## 3. Monitoring Parameters & Tuning

### 3.1 Core Parameters

| Parameter | sysfs file | Description | Default |
|-----------|-----------|-------------|---------|
| **Sampling interval** | `sample_us` | Time between access checks per region | 5,000 µs (5 ms) |
| **Aggregation interval** | `aggr_us` | Window for accumulating access counts | 100,000 µs (100 ms) |
| **Update interval** | `update_us` | How often target address space changes are checked | 1,000,000 µs (1 s) |
| **Min regions** | `nr_regions/min` | Lower bound on monitoring regions | 10 |
| **Max regions** | `nr_regions/max` | Upper bound on monitoring regions | 1,000 |

### 3.2 Tuning Guide

The **single most important tuning rule**:

> **Set aggregation interval to capture a meaningful amount of accesses for
> your purpose. Then set sampling interval proportional to aggregation interval
> (1/20 ratio recommended).**

- **Aggregation interval too short** → everything looks cold/rarely accessed (all
  regions show 0% access)
- **Aggregation interval too long** → regions converge slowly, temporal
  resolution lost
- **Sampling interval too large** → all regions look the same (no
  differentiation)
- **Sampling interval too small** → unnecessary overhead without quality gain

From [the kernel's tuning example](https://docs.kernel.org/7.0/mm/damon/monitoring_intervals_tuning_example.html):

| Sampling | Aggregation | Result |
|----------|-------------|--------|
| 5 ms | 100 ms | Poor — everything cold, uniform region sizes |
| 100 ms | 2 s | Better — two 4 KiB hot regions found, meaningful ages |
| 400 ms | 8 s | Good — many differentiated regions, varied sizes |
| 800 ms | 16 s | Hot-biased — good hot detection but cold regions compressed |

### 3.2.1 Real-World Confirmation (This System)

On a Debian 13 VM with 7.7 GiB RAM running VSCode SSH + Node.js workloads:

| Interval | Result |
|----------|--------|
| 100ms / 2s (30s run) | 98.6% of physical RAM shows 0% access. Only 132 MiB (1.4%) has any activity. Stack pages at 60–100% rate detected correctly. |
| 100ms / 2s (per-process vaddr) | Virtual address gaps (multi-TiB) dominate output. 3–5 hot stack pages (4–12 KiB each) found per process. Heap regions show 0% access. |

This confirms the tuning guide: 100ms/2s is a **starting point for stack
profiling** but far too short for heap or physical memory classification.
For hot/cold breakdowns, use at least **400ms/8s for 5+ minutes**.

### 3.3 Intervals Auto-Tuning

DAMON can auto-tune sampling and aggregation intervals. You specify:
- `access_bp` — Target ratio of observed access events to theoretical maximum (in bp = 1/10,000)
- `aggrs` — Number of aggregations to measure over
- `min_sample_us` / `max_sample_us` — Bounds for the tuned sampling interval

**Recommendation from kernel docs:** 4% access samples ratio (per Parreto
principle: 20% of 20% DAMON-observed events → 64% real access events).

```
# Via damo:
damo start --monitoring_intervals_goal 4% 3 5ms 10s
# access_bp=400 (4%), aggrs=3, min_sample=5ms, max_sample=10s
```

---

## 4. DAMOS — Data Access Monitoring-based Operation Schemes

DAMOS lets you specify **what memory regions** to find and **what to do** with
them — DAMON handles the rest automatically.

### 4.1 Scheme Components

```
Scheme
├── Action               ← what to do with matching regions
├── Access Pattern       ← which regions to target (size, access rate, age)
├── Quotas               ← CPU/memory usage limits
│   ├── Weights          ← prioritization tuning
│   └── Goals            ← auto-tuning targets
├── Watermarks           ← conditional activation
├── Filters              ← additional include/exclude rules
├── Destinations         ← for migrate actions
└── Statistics           ← runtime counters
```

### 4.2 Actions

| Action | Description | Supported Ops |
|--------|-------------|---------------|
| `willneed` | `madvise(MADV_WILLNEED)` | vaddr, fvaddr |
| `cold` | `madvise(MADV_COLD)` | vaddr, fvaddr |
| `pageout` | Reclaim pages | vaddr, fvaddr, paddr |
| `hugepage` | `madvise(MADV_HUGEPAGE)` | vaddr, fvaddr |
| `nohugepage` | `madvise(MADV_NOHUGEPAGE)` | vaddr, fvaddr |
| `lru_prio` | Prioritize on LRU lists | paddr |
| `lru_deprio` | Deprioritize on LRU lists | paddr |
| `migrate_hot` | Migrate hot pages to target node | vaddr, fvaddr, paddr |
| `migrate_cold` | Migrate cold pages to target node | vaddr, fvaddr, paddr |
| `stat` | Count only (no action) | all |

### 4.3 Access Pattern (Targeting)

Three closed-interval filters — a region matches if ALL three are satisfied:

- **sz** `[min, max]` — Region size in bytes
- **nr_accesses** `[min, max]` — Access count per aggregation interval
- **age** `[min, max]` — How long the access pattern has persisted

### 4.4 Quotas

Upper-bound overhead control. Prevents a scheme from consuming too much CPU or
IO:

- `ms` — Max milliseconds of action work per reset interval
- `bytes` — Max bytes of memory to act on per reset interval
- `reset_interval_ms` — Window over which quotas reset

Both set to 0 = quotas disabled (unless goals are set).

### 4.5 Prioritization Weights

When quotas limit action, DAMOS prioritizes regions. Weights (in permil ‰)
fine-tune this:

- `sz_permil` — Weight for region size
- `nr_accesses_permil` — Weight for access frequency
- `age_permil` — Weight for age

### 4.6 Watermarks

Conditional activation based on a system metric:

- `metric` — What to measure (e.g., `free_mem_rate` in ‰)
- `interval_us` — Check interval
- `high` / `mid` / `low` — Three thresholds

Logic:
- **Above high** → scheme deactivated (enough resources)
- **Below low** → scheme deactivated (too little — fall back to kernel defaults)
- **Between mid and low** → scheme activated
- All schemes inactive → monitoring itself stops

### 4.7 Filters

Memory-type-based include/exclude rules. Available filter types:

**Core-layer handled:**
- `addr` — Address range match
- `target` — Specific monitoring target match
- `young` — Page was accessed since last check
- `hugepage_size` — Huge page size match

**Ops-layer handled (paddr only):**
- `anon` — Anonymous pages (not file-backed)
- `active` — Active LRU pages
- `memcg` — Specific memory cgroup

Filter chaining:
- If page matches a filter → apply that filter's allow/reject decision
- If page passes all filters → inverted last filter's allow type determines outcome
- `core_filters` are evaluated first, then `ops_filters`

### 4.8 Goals (Auto-Tuning)

Instead of fixed quotas, specify a target metric and let DAMOS auto-tune:

| Goal metric | Description |
|-------------|-------------|
| `user_input` | User-provided feedback value |
| `some_mem_psi_us` | System memory pressure stall time (µs) — self-measured |
| `node_mem_used_bp` | NUMA node used memory ratio (bp) |
| `node_mem_free_bp` | NUMA node free memory ratio (bp) |
| `node_memcg_used_bp` | Per-cgroup per-node used memory ratio (bp) |
| `node_memcg_free_bp` | Per-cgroup per-node free memory ratio (bp) |
| `active_mem_bp` | Active/(Active+Inactive) LRU ratio (bp) |
| `inactive_mem_bp` | Inactive/(Active+Inactive) LRU ratio (bp) |

---

## 5. DAMON Interfaces

### 5.1 sysfs Interface (Primary, Modern)

Root: `/sys/kernel/mm/damon/admin/`

```
admin/
└── kdamonds/
    ├── nr_kdamonds
    └── 0/
        ├── state        ← on/off/commit/update_schemes_stats/...
        ├── pid          ← kdamond thread PID (when running)
        ├── refresh_ms   ← periodic stats update interval
        └── contexts/
            ├── nr_contexts
            └── 0/
                ├── avail_operations    ← list available ops sets
                ├── operations          ← vaddr|fvaddr|paddr
                ├── addr_unit
                ├── monitoring_attrs/
                │   ├── intervals/
                │   │   ├── sample_us
                │   │   ├── aggr_us
                │   │   ├── update_us
                │   │   └── intervals_goal/
                │   │       ├── access_bp
                │   │       ├── aggrs
                │   │       ├── min_sample_us
                │   │       └── max_sample_us
                │   └── nr_regions/
                │       ├── min
                │       └── max
                ├── targets/
                │   ├── nr_targets
                │   └── 0/
                │       ├── pid_target
                │       └── regions/
                └── schemes/
                    ├── nr_schemes
                    └── 0/
                        ├── action
                        ├── target_nid
                        ├── apply_interval_us
                        ├── access_pattern/
                        │   ├── sz/min, sz/max
                        │   ├── nr_accesses/min, nr_accesses/max
                        │   └── age/min, age/max
                        ├── quotas/
                        │   ├── ms, bytes, reset_interval_ms, effective_bytes
                        │   ├── weights/sz_permil, nr_accesses_permil, age_permil
                        │   └── goals/
                        ├── watermarks/
                        │   ├── metric, interval_us, high, mid, low
                        ├── {core_,ops_,}filters/
                        ├── dests/
                        ├── stats/
                        │   ├── nr_tried, sz_tried, nr_applied, sz_applied
                        │   ├── qt_exceeds, nr_snapshots, max_nr_snapshots
                        └── tried_regions/
                            ├── total_bytes
                            └── 0/start, end, nr_accesses, age, sz_filter_passed
```

**Key `state` file commands:**
- `on` — Start kdamond
- `off` — Stop kdamond
- `commit` — Re-read all sysfs parameter files
- `update_schemes_stats` — Refresh stats files
- `update_schemes_tried_regions` — Populate tried_regions directories
- `update_schemes_tried_bytes` — Update only total_bytes
- `clear_schemes_tried_regions` — Remove tried_regions subdirectories
- `update_schemes_effective_quotas` — Update effective_bytes
- `update_tuned_intervals` — Update sample_us/aggr_us with auto-tuned values

### 5.2 damo CLI Tool

```bash
# Core commands
damo start [options] [target]     # Start DAMON monitoring
damo tune [options]               # Update running DAMON parameters
damo stop                         # Stop DAMON
damo record [options] [target]    # Record monitoring results + system info
damo report <format> [options]    # Visualize recorded/live data

# Key report formats
damo report access                # Access pattern snapshot (text-based heatmap)
damo report heatmap               # 3D heatmap visualization (PNG)
damo report wss                   # Working set size distribution
damo report damon                 # DAMON status and scheme stats
damo report sysinfo               # System DAMON capabilities
damo report trace                 # Live tracepoint events
damo report footprints            # Memory footprint over time
damo report profile               # CPU profiling correlation

# Helper commands
damo version                      # Show version
damo args damon [options]         # Show what parameters would be applied
damo help damon_param_options     # Show all parameter options
damo setup_cli_completion         # Tab completion setup
```

### 5.3 Important: DAMON_STAT Conflict

When `CONFIG_DAMON_STAT_ENABLED_DEFAULT=y` (as on this system), the
`damon_stat` kernel module starts automatically at boot and occupies the DAMON
kdamond. You **cannot** use `damo start` or any manual DAMON control until
you disable it:

```bash
# Check status
cat /sys/module/damon_stat/parameters/enabled

# Disable (via Python — shell redirects may be blocked by sandbox)
python3 -c "open('/sys/module/damon_stat/parameters/enabled','w').write('N')"

# Re-enable after manual DAMON use
python3 -c "open('/sys/module/damon_stat/parameters/enabled','w').write('Y')"
```

The analysis scripts in this project handle this automatically.

### 5.4 Tracepoints

Two tracepoints for full recording (used by `damo record`):

- **`damon:damon_aggregated`** — Emitted each aggregation interval with all
  region data: `target_id, nr_regions, start-end: nr_accesses age`
- **`damon:damos_before_apply`** — Emitted before each DAMOS action application:
  `ctx_idx, scheme_idx, target_idx, nr_regions, start-end: nr_accesses age`

Record with `perf` or `trace-cmd`:
```bash
perf record -e damon:damon_aggregated &
```

### 5.4 Kernel Modules

| Module | Config | Purpose |
|--------|--------|---------|
| **DAMON_RECLAIM** | `CONFIG_DAMON_RECLAIM=y` | Proactive cold-page reclamation |
| **DAMON_LRU_SORT** | `CONFIG_DAMON_LRU_SORT=y` | Proactive LRU hot/cold prioritization |
| **DAMON_STAT** | `CONFIG_DAMON_STAT=y` | Monitoring accuracy/overhead statistics |

#### DAMON_RECLAIM Parameters (`/sys/module/damon_reclaim/parameters/`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enabled` | N | Enable/disable |
| `min_age` | 120s | Cold threshold (age in µs) |
| `quota_ms` | 10ms | Max time per reset interval |
| `quota_sz` | 128 MiB | Max bytes per reset interval |
| `quota_reset_interval_ms` | 1s | Quota reset window |
| `wmarks_high/mid/low` | — | Free memory rate watermarks (‰) |
| `skip_anon` | N | Skip anonymous pages |

#### DAMON_LRU_SORT Parameters (`/sys/module/damon_lru_sort/parameters/`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enabled` | N | Enable/disable |
| `hot_thres_access_freq` | 500‰ (50%) | Hot access frequency threshold |
| `cold_min_age` | 120s | Cold age threshold (µs) |
| `quota_ms` | 10ms | Max time per reset interval |
| `quota_reset_interval_ms` | 1s | Quota reset window |
| `wmarks_high/mid/low` | 200/150/50 | Free memory rate watermarks (‰) |

---

## 6. This System's Configuration

| Item | Value |
|------|-------|
| Kernel | 7.0.10+deb13-amd64 |
| DAMON version | 7.0 |
| damo version | 3.2.9 |
| Total RAM | ~8 GiB |
| Total Swap | ~16 GiB |
| Zswap | Not active (0 kB) |
| DAMON config flags | `DAMON=y DAMON_VADDR=y DAMON_PADDR=y DAMON_SYSFS=y DAMON_RECLAIM=y DAMON_LRU_SORT=y DAMON_STAT=y` |
| Available operations | vaddr, fvaddr, paddr (verify: `cat /sys/kernel/mm/damon/admin/kdamonds/0/contexts/0/avail_operations` after creating kdamond) |
| sysfs root | `/sys/kernel/mm/damon/admin/` |

---

## 7. Hot/Warm/Cold Classification Framework

Based on DAMON's monitoring output, we classify memory regions as follows:

### 7.1 Classification Criteria

| Class | Access Rate | Age | Meaning |
|-------|------------|-----|---------|
| **Hot** | ≥ 50% | ≥ 5s | Frequently accessed, stable pattern — keep in fast RAM |
| **Warm** | 5% – 50% | any | Moderately accessed — transitional |
| **Cold** | < 5% | ≥ 30s | Rarely accessed, stable pattern — compress/swap candidate |
| **Idle** | 0% | ≥ 60s | Not accessed at all — evict to disk |

### 7.2 Access Rate Calculation

```
max_nr_accesses = aggregation_interval_us / sampling_interval_us
access_rate_pct = (nr_accesses / max_nr_accesses) × 100
```

### 7.3 Temperature Calculation (damo-compatible)

```
if access_rate == 0:
    temperature = -age_us
else:
    temperature = access_rate × age_us
```

### 7.4 Tuning for Classification Quality

For hot/cold classification, the tuning example from the kernel docs
recommends intervals that give enough resolution:

- **For 8 GiB systems:** Start with `-s 100ms -a 2s`, then refine
- **For hot page detection:** Longer aggregation (4-8s) works better
- **For cold page detection:** Moderate aggregation (2-4s) to capture age diversity

---

## 8. Practical Usage Examples

### 8.1 Quick Process Profiling

```bash
# Profile a running process
sudo damo start --target_pid $(pidof myapp)
sleep 10
sudo damo report access
sudo damo stop
```

### 8.2 Record + Visualize

```bash
# Record access patterns for 5 minutes
sudo damo record -o app.data --target_pid $(pidof myapp) &
sleep 300
sudo damo stop

# Generate heatmap
damo report heatmap --input_file app.data --output heatmap.png

# Working set size distribution
damo report wss --input_file app.data --range 0 101 10
```

### 8.3 Proactive Cold Page Reclaim

```bash
# Page out memory regions not accessed for ≥60s, sized ≥4KiB
sudo damo start \
    --damos_access_rate 0 0 \
    --damos_sz_region 4K max \
    --damos_age 60s max \
    --damos_action pageout \
    --target_pid $(pidof myapp)
```

### 8.4 Memory Tiering (Hot Promotion + Cold Demotion)

```bash
# From damo repo scripts/mem_tier.sh:
# Promote hot pages from node 1 → node 0, demote cold pages from node 0 → node 1
sudo damo start \
    --numa_node 0 --monitoring_intervals_goal 4% 3 5ms 10s \
        --damos_action migrate_cold 1 --damos_access_rate 0% 0% \
        --damos_apply_interval 1s \
        --damos_quota_interval 1s --damos_quota_space 200MB \
        --damos_quota_goal node_mem_free_bp 0.5% 0 \
    --numa_node 1 --monitoring_intervals_goal 4% 3 5ms 10s \
        --damos_action migrate_hot 0 --damos_access_rate 5% max \
        --damos_apply_interval 1s \
        --damos_quota_interval 1s --damos_quota_space 200MB \
        --damos_quota_goal node_mem_used_bp 99.7% 0 \
    --nr_targets 1 1 --nr_schemes 1 1 --nr_ctxs 1 1
```

### 8.5 LRU List Sorting

```bash
# Prioritize hot (≥50% access) and deprioritize cold (≥120s no access)
sudo damo start \
    --damos_action lru_prio --damos_access_rate 50% max --damos_age 5s max \
    --damos_action lru_deprio --damos_access_rate 0% 0% --damos_age 120s max \
    --target_pid $(pidof myapp)
```

### 8.6 Using DAMON_RECLAIM Module

```bash
cd /sys/module/damon_reclaim/parameters
echo 30000000 > min_age          # 30s cold threshold
echo $((1*1024*1024*1024)) > quota_sz  # 1 GiB/s limit
echo 1000 > quota_reset_interval_ms
echo 500 > wmarks_high           # stop if >50% free
echo 400 > wmarks_mid            # start if <40% free
echo 200 > wmarks_low            # stop if <20% free
echo Y > enabled
```

---

## 9. Scripting with DAMON

### 9.1 Direct sysfs Control

```bash
#!/bin/bash
# Minimal: monitor a process via sysfs directly (no damo needed)
PID=$1
SYSFS=/sys/kernel/mm/damon/admin

echo 1 > $SYSFS/kdamonds/nr_kdamonds
echo 1 > $SYSFS/kdamonds/0/contexts/nr_contexts
echo vaddr > $SYSFS/kdamonds/0/contexts/0/operations
echo 1 > $SYSFS/kdamonds/0/contexts/0/targets/nr_targets
echo $PID > $SYSFS/kdamonds/0/contexts/0/targets/0/pid_target
echo on > $SYSFS/kdamonds/0/state
```

### 9.2 Reading Monitoring Results via sysfs

```bash
# After starting monitoring, update and read tried_regions
echo update_schemes_tried_regions > /sys/kernel/mm/damon/admin/kdamonds/0/state
# Now read /sys/kernel/mm/damon/admin/kdamonds/0/contexts/0/schemes/0/tried_regions/
```

### 9.3 damo JSON/Kdamonds Format

```bash
# See full parameter structure
damo args damon --format json --target_pid $(pidof myapp)
```

---

## 10. Key Resources

| Resource | URL |
|----------|-----|
| Kernel docs (design) | https://docs.kernel.org/7.0/mm/damon/design.html |
| Kernel docs (admin guide) | https://docs.kernel.org/7.0/admin-guide/mm/damon/index.html |
| Kernel docs (sysfs usage) | https://docs.kernel.org/7.0/admin-guide/mm/damon/usage.html |
| Kernel docs (tuning example) | https://docs.kernel.org/7.0/mm/damon/monitoring_intervals_tuning_example.html |
| damo repo | https://github.com/damonitor/damo |
| damo USAGE.md | https://github.com/damonitor/damo/blob/next/USAGE.md |
| DAMON project site | https://damonitor.github.io |
| DAMON blog | https://damonitor.github.io/posts/damon/ |
| DAMON news | https://damonitor.github.io/posts/damon_news/ |
| DeepWiki (damo) | https://deepwiki.com/damonitor/damo |
| damon-tests | https://github.com/damonitor/damon-tests |
| masim (test workload) | https://github.com/sjp38/masim |
| DAMON papers | Middleware'19 Industry, HPDC'22 |
| Mailing list | damon@lists.linux.dev |
| LKML lore | https://lore.kernel.org/damon |

---

## 11. Common Pitfalls & Tips

1. **Default intervals (5ms/100ms) are nearly useless** — they produce uniform
   "everything cold" results. Increase to at least 100ms/2s.

2. **Root required** — All DAMON control operations need root. `damo` itself
   runs the target command as root when given a command string.

3. **`perf` or `trace-cmd` needed for full recording** — `damo record` needs one
   of these for tracepoint-based recording. For snapshots (`damo report
   access`), neither is needed.

4. **DAMOS actions reset age** — When a non-`stat` action is applied to a
   region, its age resets to 0 because the action changed the region's
   characteristics.

5. **One context per kdamond** — Currently only 0 or 1 contexts per kdamond.

6. **vaddr auto-detects regions** — With `vaddr` operations, DAMON
   automatically finds mapped regions; you don't need to specify ranges.

7. **paddr needs range** — For physical address monitoring, specify regions or
   DAMON monitors biggest System RAM by default.

8. **DAMOS stat action for querying** — Set action=`stat` with a specific
   access pattern to query "what regions match pattern X" without side effects.

9. **Watermarks stop monitoring** — When all schemes are deactivated by
   watermarks, monitoring itself stops. Kdamond only periodically checks
   watermarks.

10. **`refresh_ms` for automated stat updates** — Instead of manual `update_*`
    writes to `state`, set `refresh_ms` for automatic periodic updates.

11. **`vaddr` monitors virtual address space, not RSS** — The `vaddr` operations
    set tracks the full virtual address space of the target process. This
    includes enormous unmapped gaps between the heap, mmap regions, and stack
    (several TiB each). Total reported size can be 64+ TiB even for a process
    with only 600 MiB RSS. Filter by `access_rate > 0%` to find actually
    accessed pages. For physical memory classification, use `paddr` instead.

12. **`tried_regions` subdirectory names are NOT sequential** — The kernel
    creates them with arbitrary numeric names (e.g., 85, 87, 89… not 0, 1, 2…).
    Always iterate `os.listdir()` rather than assuming index 0, 1, 2, ….

13. **`age` in tried_regions is in aggregation intervals, not µs** — A value of
    6 with `aggr_us=2,000,000` means 6 × 2s = 12 seconds. Multiply by
    `aggr_us` to convert to microseconds before classification.

14. **DAMON_STAT blocks manual DAMON** — If `CONFIG_DAMON_STAT_ENABLED_DEFAULT=y`
    (Debian kernels), `damon_stat` occupies the kdamond at boot. Attempting
    `damo start` fails with "Device or resource busy". Disable it first:
    ```bash
    python3 -c "open('/sys/module/damon_stat/parameters/enabled','w').write('N')"
    ```

15. **Short intervals show everything cold** — At 100ms/2s, expect 95%+ of
    regions to show 0% access. This isn't a bug — the aggregation window is
    simply too narrow for most workloads. For hot/cold classification, use
    **400ms/8s or 800ms/16s** and run for at least 5 minutes so age counters
    can meaningfully differentiate cold from idle.

---

## 12. vaddr vs paddr — Choosing the Right Operations Set

### 12.1 vaddr (Virtual Address Space)

**What it monitors:** The full virtual address space of a specific process.
DAMON's vaddr ops automatically identifies mapped regions and excludes the
two largest unmapped gaps (typically heap→mmap and mmap→stack).

**What you'll see:**
- 10–20 regions spanning 10–100 TiB of virtual address space
- 85–95% of reported bytes are unmapped gaps (0% access, large but fake)
- 2–5 small regions (4–128 KiB) near 0x7ffe… are thread stacks — the only
  regions with non-zero access rates in short runs
- Heap/mmap regions (1–30 GiB) typically show 0% access unless the workload is
  actively scanning memory

**Best for:** Understanding which *code paths* are active (stack = executing
threads), detecting memory leaks (unaccessed heap that never shrinks), and
profiling a single known process.

**Limitation:** The multi-TiB gaps dominate the output. Always filter by
`access_rate_pct > 0` to find the real data. The `ReportFormatter` in our
library does this automatically in the "Active regions" section.

### 12.2 paddr (Physical Address Space)

**What it monitors:** The system's physical RAM (or a subset you specify).

**What you'll see:**
- Usually 10–20 regions covering the full monitored physical range
- Every byte represents actual DRAM — no unmapped gaps
- Access rates directly reflect CPU memory traffic
- Age counters show how stable access patterns are

**Best for:** Hot/cold classification for swap/ZRAM/ZSWAP decisions,
system-wide memory pressure analysis, NUMA tiering decisions.

**Limitation:** Cannot attribute physical pages to specific processes without
additional tooling (`page-types`, `/proc/pid/pagemap`).

### 12.3 Recommendation for Hot/Cold Classification

Use **paddr** with **400ms/8s intervals** for at least **5 minutes**:

```bash
sudo damo start paddr -s 400ms -a 8s \
    --damos_action stat \
    --damos_access_rate '0%' max \
    --damos_sz_region 0 max \
    --damos_age 0 max
sleep 300
# ... collect tried_regions and classify ...
sudo damo stop
```

At 8s aggregation, regions with 0% access that persist for 4+ aggregation
windows (32s) have meaningful age. After 5 minutes, you'll see clear hot/warm/
cold/idle stratification.

---

## 13. Region Granularity Control

DAMON groups adjacent pages with similar access patterns into regions. The
number of regions directly controls the **overhead vs. resolution trade-off**.

| Parameter | Default | Effect of Increasing |
|-----------|---------|---------------------|
| `min_regions` | 10 | Higher floor on monitoring quality; more CPU overhead |
| `max_regions` | 1,000 | Higher ceiling lets DAMON track more distinct access patterns |

**How it works:**
- DAMON adaptively merges and splits regions every aggregation interval
- If regions > `max_regions`, it merges the most similar adjacent regions
- If regions < `max_regions`, it splits the largest region into 2–3
- It stops merging when regions would drop below `min_regions`

**Tuning guidance:**
- **For 8 GiB systems:** `min=10, max=1000` (default) is fine
- **For 64+ GiB systems:** consider `max=2000` to capture more diversity
- **For profiling:** higher max (2000–5000) gives finer granularity at the cost of more CPU
- **For production DAMOS:** lower max (100–500) keeps overhead minimal

In damo:
```bash
damo start --min_nr_regions 10 --max_nr_regions 2000 --target_pid <PID>
```

Via sysfs:
```bash
echo 10 > .../monitoring_attrs/nr_regions/min
echo 2000 > .../monitoring_attrs/nr_regions/max
```

> Our `Monitor` class forwards `min_regions`/`max_regions` to damo via
> `--min_nr_regions`/`--max_nr_regions`. Leave them unset (`None`) to keep
> the kernel defaults (min=10, max=1000).

---

## 14. Parallel kdamonds — Running Multiple Analyses

DAMON supports multiple kdamond kernel threads running simultaneously. Each
kdamond has its own context, target, and schemes — fully independent.

### 14.1 Via sysfs

```bash
# Create 2 kdamonds
echo 2 > /sys/kernel/mm/damon/admin/kdamonds/nr_kdamonds

# Configure kdamond 0 (vaddr on process X)
echo 1 > .../kdamonds/0/contexts/nr_contexts
echo vaddr > .../kdamonds/0/contexts/0/operations
echo <PID> > .../kdamonds/0/contexts/0/targets/0/pid_target

# Configure kdamond 1 (paddr system-wide)
echo 1 > .../kdamonds/1/contexts/nr_contexts
echo paddr > .../kdamonds/1/contexts/0/operations

# Start both
echo on > .../kdamonds/0/state
echo on > .../kdamonds/1/state
```

### 14.2 Via damo

```bash
sudo damo start \
    --ops vaddr --target_pid 12345 \
    --ops paddr \
    --nr_targets 1 1 --nr_ctxs 1 1
```

### 14.3 Using parallel kdamonds from Python

Our `Monitor` class accepts a `kdamond_idx` parameter:

```python
from damon_analysis import Monitor

m1 = Monitor(kdamond_idx=0)
m2 = Monitor(kdamond_idx=1)

m1.configure_vaddr(pid=1234)
m2.configure_paddr()

# Create both kdamonds first:
m1.sysfs.create_kdamond(0)
m1.sysfs.create_kdamond(1)

m1.start()   # kdamond 0 — vaddr on PID 1234
m2.start()   # kdamond 1 — paddr system-wide (runs in parallel)

# Collect from both
regions1 = m1.collect()
regions2 = m2.collect()

m1.stop()
m2.stop()
```

The `start()` method passes `--nr_kdamonds` to damo when `kdamond_idx > 0`.

---

## 15. Container Analysis

### 15.1 Approach

`analyze_container.py` identifies a container's processes and profiles each one:

1. **Resolve container → init PID** via `docker inspect` or `podman inspect`
2. **Walk child processes** recursively via `/proc/<pid>/stat` (read ppid field)
3. **Profile each process** sequentially using `vaddr` monitoring
4. **Aggregate results** across all processes

### 15.2 Usage

```bash
# Per-process profiling (default mode)
sudo ./analyze_container.py my-container --duration 60 --output json

# Physical memory with memcg filter (requires --cgroup-path)
sudo ./analyze_container.py my-container --mode physical \
    --cgroup-path /docker/<container_id>

# Via the CLI
sudo ./damon_cli.py profile-container my-container --duration 120
```

### 15.3 Limitations

- **Sequential only** — processes are profiled one at a time. Parallel kdamond
  support would allow simultaneous profiling.
- **Docker/Podman only** — other container runtimes need custom PID resolution.
- **Requires `docker` or `podman` CLI** on the host.
- The memcg filter mode (`--mode physical`) requires knowing the cgroup path,
  which varies by container runtime and version.

---

## 16. tried_regions Internals

### 16.1 Directory Structure

When you write `update_schemes_tried_regions` to the kdamond's `state` file,
the kernel populates `tried_regions/` with subdirectories for each region
that the scheme's action was *tried* on (i.e., matched the access pattern):

```
tried_regions/
├── total_bytes          ← sum of all region sizes
├── 85/                  ← region subdirectory (NON-sequential!)
│   ├── start
│   ├── end
│   ├── nr_accesses      ← access count THIS aggregation interval
│   ├── age              ← IN AGGREGATION INTERVALS, not µs
│   └── sz_filter_passed
├── 87/
├── 89/
...
```

### 16.2 Age Units

The `age` file returns the number of **aggregation intervals** the current
access pattern has persisted, NOT microseconds. To convert:

```python
age_us = raw_age * aggr_us
age_sec = age_us / 1_000_000
```

Example: `age=6` with `aggr_us=2,000,000` → 12 seconds.

Our `Classifier.classify_regions()` handles this conversion automatically.

### 16.3 Non-Sequential Indices

The kernel assigns arbitrary numeric names to region subdirectories (e.g.,
85, 87, 89 rather than 0, 1, 2). Our `SysfsInterface.read_tried_regions()`
iterates `os.listdir()` and filters for directories to handle this.

### 16.4 When Data Appears

tried_regions is NOT populated automatically. You must explicitly request it:

```bash
echo update_schemes_tried_regions > .../kdamonds/0/state
```

Or set `refresh_ms` for periodic automatic updates. Without this, `total_bytes`
may show stale data and subdirectories may be absent.







---





# original reply in UI 

given links to read up on fresh information 
https://docs.kernel.org/7.0/admin-guide/mm/zswap.html
https://docs.kernel.org/7.0/admin-guide/blockdev/zram.html
https://man7.org/linux/man-pages/man8/zramctl.8.html
https://github.com/VHSgunzo/zswapmon
https://wiki.ubuntuusers.de/zRam/
https://chrisdown.name/2026/03/24/zswap-vs-zram-when-to-use-what.html
https://www.ibm.com/support/pages/new-linux-zswap-compression-functionality
https://en.wikipedia.org/wiki/Zswap
https://en.wikipedia.org/wiki/Zram
https://docs.kernel.org/7.0/admin-guide/cgroup-v2.html




This is a full system design problem. Let me research the key resources you linked, then synthesize an architecture.

I have what I need. Let me synthesize the architecture.

---

## 1. The Core Decision: zswap, not zram

Chris Down (Meta kernel MM engineer, works on swap) is unambiguous:

> *"If in doubt, prefer to use zswap. Only use zram if you have a highly specific reason to."*
>
> *"Do not run zram alongside disk swap wherever possible. In such setups, zram fills fast RAM with cold, stale pages while pushing your active working set onto slow disk, making things actively worse than if you had no compressed swap at all."*

The mechanism is **LRU inversion**: zram is a block device with a hard capacity limit. Whatever pages swap out *first* (typically cold init data) permanently occupy the fast compressed RAM. Pages swapped *later* (like Soulmask's active working set during a dev build spike) spill to slow disk. Your fastest tier calcifies with the coldest data.

**zswap** integrates into the kernel's MM reclaim path. It:
- Intercepts pages before they hit disk
- Compresses them in a RAM pool (default 20% of RAM)
- When the pool fills, **automatically evicts the coldest pages to disk on an LRU basis**
- Responds to memory pressure in real time, not on a cron timer

```
zswap:   RAM → [compress] → zswap pool → [LRU evict cold] → disk swap
zram:    RAM → [compress] → zram device [FULL] → spill to disk (LRU inverted)
```

**Your intuition is correct:** higher compression is worth more than slow disk swap, but *only if the right pages stay compressed*. zswap gives you that. zram gives you fast access to the wrong pages.

---

## 2. Swap Device Layout

Your VM has a single disk (vda). Multiple partitions on the same physical device don't provide parallel access — the bottleneck is the underlying storage, not the partition table. However, **two swap partitions with different priorities** still makes sense for zswap tiering:

```
/dev/vda5  (root LVM)
  ├── root_lv       ~40GB  system root
  ├── swap_fast_lv   4GB   priority=100  ← zswap backing, small/fast
  └── swap_cold_lv  16GB   priority=10   ← cold overflow
```

**Why two swap partitions:**
1. `swap_fast_lv` (4 GB, high priority): zswap writes its LRU evictions here first. Keep it small so the disk head stays in a tight region.
2. `swap_cold_lv` (16 GB, low priority): when the fast swap fills, cold pages spill here. Large enough to absorb Docker build spikes.

The kernel allocates swap from highest-priority device first. zswap evictions naturally land on the fast partition. Only when that's full does cold data hit the large partition.

**Alternative — single swap with zswap doing all the work:**
If you don't want to repartition, a single 16-20 GB swap partition works. zswap's LRU eviction will still prioritize evicting the coldest pages — the benefit of two partitions is marginal on a single-disk VM.

---

## 3. Compression Configuration

### Algorithm choice

```bash
# Check available compressors
cat /sys/module/zswap/parameters/compressor

# Modern kernels support: lzo, lz4, lz4hc, zstd, deflate
```

| Algorithm | Ratio | Speed | CPU Cost | Best For |
|-----------|-------|-------|----------|----------|
| **lz4** | ~2:1 | Very fast | Negligible | Default, safe choice |
| **zstd** | ~3:1 | Fast | Low | Better compression, still fast |
| lzo | ~2:1 | Fast | Low | Legacy, lz4 is better |
| deflate | ~4:1 | Slow | High | Maximum density, CPU tradeoff |
| lz4hc | ~2.5:1 | Moderate | Low-Medium | Balance |

**Recommendation: `zstd`.** On your 8-core system with 80% of one core used by Soulmask, you have plenty of CPU headroom. zstd gives ~3:1 compression (turning 4 GB of compressed pool into ~12 GB of effective RAM) with negligible latency.

```bash
echo zstd > /sys/module/zswap/parameters/compressor
```

### Pool sizing

```bash
# zswap pool = 25% of RAM = 4 GB on a 16 GB system
echo 25 > /sys/module/zswap/parameters/max_pool_percent
```

At 25% with zstd's ~3:1 ratio, the pool holds ~12 GB of uncompressed pages — nearly the entire Soulmask working set. The remaining 12 GB of physical RAM is available for active pages. Total effective memory: ~24 GB.

### zswap shrinker (kernel 6.7+)

Enable the proactive shrinker so cold pages drain to disk *before* the pool hits its limit:

```bash
echo Y > /sys/module/zswap/parameters/shrinker_enabled
```

This prevents the "pool full → stall → evict → pool full" thrashing cycle.

---

## 4. Priority Management via cgroup v2

### Hierarchy design

```
/sys/fs/cgroup/
├── soulmask.slice/          ← protected, never swapped if possible
│   └── memory.low  = 12G   ← best-effort protection
│   └── memory.min  = 4G    ← hard guarantee
│   └── memory.zswap.writeback = 0  ← disable zswap→disk eviction
├── system.slice/            ← standard OS services
│   └── (apt, certbot, sshd, cron, ...)
├── dev.slice/               ← dev containers, builds
│   ├── memory.high = 8G    ← soft limit, throttles when exceeded
│   ├── memory.max  = 14G   ← hard limit, OOM killer fires
│   ├── memory.zswap.writeback = 1  ← allow eviction to disk
│   ├── cpu.weight  = 50    ← lower CPU priority
│   └── io.max       = "8:0 rbps=200M wbps=200M"  ← disk I/O cap
└── devcontainers.slice/     ← the dev's own devcontainer
    ├── memory.low = 2G     ← protection above other dev containers
    └── cpu.weight = 100    ← standard priority
```

### What each knob does

**`memory.low` (best-effort protection):** If memory is abundant, Soulmask gets up to 12 GB. Under pressure, the kernel reclaims from unprotected cgroups first. If pressure continues, Soulmask's protection shrinks proportionally but Soulmask is *always* the last to be reclaimed from.

**`memory.min` (hard guarantee):** Soulmask is *guaranteed* 4 GB. The kernel will OOM-kill other cgroups before dipping below this. Set this to Soulmask's observed active working set (measure with DAMON!).

**`memory.high` (soft limit):** When dev.slice exceeds 8 GB, the kernel throttles allocations and triggers reclaim *within that cgroup only*. This prevents a runaway Docker build from eating all RAM.

**`memory.max` (hard limit):** If dev.slice hits 14 GB, the OOM killer fires inside that cgroup. Soulmask is untouched.

**`cpu.weight`:** The dev containers get CPU weight 50 (default is 100). Soulmask at default 100 gets 2× the CPU share under contention. Not a hard cap — idle CPU is still available to dev.

**`io.max`:** Caps Docker build I/O to 200 MB/s reads and writes on device 8:0 (vda). Adjust based on your storage — you want builds to complete but not saturate the disk.

**`memory.zswap.writeback`:** Disabling writeback for Soulmask means that even when zswap evicts cold pages, Soulmask's pages stay in the compressed RAM pool. They may still be reclaimed by direct reclaim if pressure is extreme, but they won't be proactively evicted to disk.

### Applying automatically

Create a systemd service that runs before pterodactyl:

```ini
# /etc/systemd/system/soulmask-cgroup.service
[Unit]
Description=Set up Soulmask cgroup protections
Before=wings.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/setup-soulmask-cgroup.sh

[Install]
WantedBy=multi-user.target
```

```bash
# /usr/local/sbin/setup-soulmask-cgroup.sh
#!/bin/bash
set -e

CGROUP=/sys/fs/cgroup

# Create slices
mkdir -p "$CGROUP/soulmask.slice" "$CGROUP/dev.slice" "$CGROUP/devcontainers.slice"

# --- Soulmask protection ---
echo "12G" > "$CGROUP/soulmask.slice/memory.low"
echo "4G"  > "$CGROUP/soulmask.slice/memory.min"
echo 0     > "$CGROUP/soulmask.slice/memory.zswap.writeback"

# Move the pterodactyl-managed container processes here
# Pterodactyl uses Docker; the container's cgroup is under system.slice initially.
# We can't move it at boot (the container isn't running yet).
# Instead, use a pterodactyl hook or a udev-like watcher.

# --- Dev slice limits ---
echo "8G"  > "$CGROUP/dev.slice/memory.high"
echo "14G" > "$CGROUP/dev.slice/memory.max"
echo 50    > "$CGROUP/dev.slice/cpu.weight"

# --- Devcontainer protection ---
echo "2G"  > "$CGROUP/devcontainers.slice/memory.low"
echo 100   > "$CGROUP/devcontainers.slice/cpu.weight"
```

### Moving pterodactyl containers into the protected cgroup

Pterodactyl Wings starts Docker containers. The Docker daemon places them in the default cgroup. To move them:

**Option A — Docker cgroup parent (cleanest):**
Configure the pterodactyl Docker daemon or use `--cgroup-parent`:

```bash
# In the Wings container configuration, add:
#   "docker": { "cgroup-parent": "soulmask.slice" }
```

**Option B — Post-start hook:**
Pterodactyl supports hooks. Add a post-start script that finds the container's PID and moves it:

```bash
#!/bin/bash
# /var/lib/pterodactyl/hooks/post-start.sh
CONTAINER_ID="$1"
# Find the container's init PID
PID=$(docker inspect -f '{{.State.Pid}}' "$CONTAINER_ID")
echo "$PID" > /sys/fs/cgroup/soulmask.slice/cgroup.procs
```

---

## 5. I/O Throttling for Docker Builds

Docker builds (`docker build`) can saturate disk I/O. cgroup v2's `io.max` controller caps this per-device:

```bash
# /dev/vda = 8:0 (check with ls -la /dev/vda)
# Limit the dev.slice to 200 MB/s reads, 200 MB/s writes
echo "8:0 rbps=209715200 wbps=209715200" > /sys/fs/cgroup/dev.slice/io.max
```

This applies to *all* processes in the slice including `docker build`, `docker pull`, and any running dev containers. You can also set limits per individual container:

```bash
# Limit a specific build container
echo "8:0 rbps=104857600 wbps=104857600" > \
    /sys/fs/cgroup/dev.slice/docker-build-123/io.max
```

For the `docker build` process specifically, the heavy I/O is from the Docker daemon, not the client. The daemon's I/O can be constrained by putting it under `dev.slice`. But a cleaner approach is to use Docker's `--storage-opt` for build limits or set the limits on the container being built (not the build process itself, which is short-lived).

---

## 6. Network Queuing

For network priority (Soulmask game traffic > dev container traffic), use `tc` (traffic control):

```bash
# Create a priority qdisc on the primary interface
tc qdisc add dev eth0 root handle 1: prio

# Soulmask traffic → high priority band
tc filter add dev eth0 protocol ip parent 1: prio 1 \
    u32 match ip dport 7777 0xffff flowid 1:1

# Dev container traffic → low priority band
tc filter add dev eth0 protocol ip parent 1: prio 3 \
    u32 match ip src 172.20.0.0/16 flowid 1:3
```

This is crude — Soulmask uses specific UDP ports. A more maintainable approach uses the `net_cls` cgroup + iptables marking, but for a single-game-server setup, port-based classification is sufficient.

---

## 7. Persistence Across Restarts

| Setting | Where to persist |
|---------|-----------------|
| zswap compressor + pool size | Kernel command line: `zswap.compressor=zstd zswap.max_pool_percent=25` |
| zswap shrinker | `/etc/sysctl.d/99-zswap.conf`: `vm.zswap_shrinker_enabled=1` |
| swap partitions + priorities | `/etc/fstab` with `pri=` option |
| cgroup hierarchy | `soulmask-cgroup.service` (systemd oneshot, Before=wings.service) |
| Process → cgroup assignment | Pterodactyl hook or Docker `--cgroup-parent` |
| I/O limits | `dev-iolimit.service` (systemd oneshot) |
| Network QoS | `/etc/network/if-pre-up.d/tc-prio` or systemd network script |

### Kernel command line (GRUB)

```bash
# /etc/default/grub
GRUB_CMDLINE_LINUX="... zswap.compressor=zstd zswap.max_pool_percent=25"
update-grub
```

### fstab swap entries

```
# /etc/fstab
/dev/mapper/vg-swap_fast  none  swap  sw,pri=100  0  0
/dev/mapper/vg-swap_cold  none  swap  sw,pri=10   0  0
```

---

## 8. Kernel Upgrade: Debian 12 (6.12.90) → Debian 13 (7.0.10)

| Feature | 6.12 | 7.0 | Relevant? |
|---------|------|-----|-----------|
| DAMON | ✓ | ✓ improved | Yes — better monitoring for hot/cold decisions |
| zswap shrinker | ✓ (6.7+) | ✓ | Yes — already in 6.12 |
| zswap per-cgroup writeback | ✓ (6.8+) | ✓ | Yes — already in 6.12 |
| zswap same-value page handling | ✓ | ✓ improved | Minor — zero-page detection |
| DAMON intervals auto-tuning | — | ✓ (6.14+) | Yes — automatically finds optimal monitoring intervals |
| DAMOS quota goals (memcg) | — | ✓ (6.19+) | Yes — per-cgroup auto-tuning of reclamation |
| Multi-size THP for swap | ✓ (6.8+) | ✓ improved | Minor — swap performance |
| DAMON page-level monitoring | — | ✓ (6.18+) | Minor — finer access tracking |

**Verdict: Upgrade if convenient, but not urgent.** Your 6.12 kernel already has zswap, zswap shrinker, per-cgroup writeback control, and DAMON. The 7.0 kernel adds DAMON intervals auto-tuning (lets DAMON find optimal monitoring parameters automatically instead of manual trial-and-error) and DAMOS per-memcg quota goals (auto-tune reclamation per cgroup). Both are relevant to your use case but can be backported or worked around.

The strongest reason to upgrade: DAMON intervals auto-tuning means your hot/cold classification won't need manual parameter tuning (which we saw is fussy — wrong intervals show everything as cold).

---

## 9. DAMON Integration

Once DAMON is running (your scripts from this project), you can feed its hot/cold classification into the cgroup limits dynamically:

```bash
#!/bin/bash
# /usr/local/sbin/adjust-soulmask-protection.sh
# Run via cron every 5 minutes or triggered by memory pressure events

# Get Soulmask's hot memory from DAMON
HOT_KB=$(/root/work/damon-project/venv/bin/python3 -c "
import sys; sys.path.insert(0,'/root/work/damon-project/lib')
from damon_analysis import SysfsInterface, Classifier
s = SysfsInterface()
s.kdamond_update_tried_regions(0)
import time; time.sleep(0.2)
regions = s.read_tried_regions(0,0,0)
c = Classifier(hot_access_rate_pct=50, cold_age_sec=30)
classified = c.classify_regions(regions, 400000, 8000000)
summary = c.summary(classified)
print(int(summary['hot']['bytes'] / 1024 + summary['warm']['bytes'] / 1024))
")

# Set memory.min to hot + warm + 1GB buffer
MIN_MB=$(( HOT_KB / 1024 + 1024 ))
echo "${MIN_MB}M" > /sys/fs/cgroup/soulmask.slice/memory.min
```

---

## 10. Complete Setup Script

This is what you'd run once on the target system:

```bash
#!/bin/bash
# setup-memory-architecture.sh — run ONCE as root on the target VM
set -euo pipefail

echo "=== Memory Architecture Setup ==="

# 1. Kernel command line (zswap)
if ! grep -q 'zswap.compressor=zstd' /etc/default/grub; then
    sed -i 's/GRUB_CMDLINE_LINUX="/GRUB_CMDLINE_LINUX="zswap.compressor=zstd zswap.max_pool_percent=25 /' \
        /etc/default/grub
    update-grub
    echo "[✓] GRUB updated — reboot to apply"
fi

# 2. Runtime zswap (takes effect immediately)
echo zstd > /sys/module/zswap/parameters/compressor
echo 25   > /sys/module/zswap/parameters/max_pool_percent
echo Y    > /sys/module/zswap/parameters/shrinker_enabled
echo "[✓] zswap configured: zstd, 25% pool, shrinker on"

# 3. Sysctl
cat > /etc/sysctl.d/99-memory.conf <<'EOF'
# zswap
vm.zswap_shrinker_enabled=1
# Reduce swappiness — prefer zswap compression over disk swap
vm.swappiness=60
# Favor keeping file-backed pages (executables, libs) in RAM
vm.vfs_cache_pressure=50
EOF
sysctl -p /etc/sysctl.d/99-memory.conf
echo "[✓] sysctl configured"

# 4. cgroup hierarchy
mkdir -p /sys/fs/cgroup/soulmask.slice
mkdir -p /sys/fs/cgroup/dev.slice
mkdir -p /sys/fs/cgroup/devcontainers.slice

# Soulmask — protected
echo "12G" > /sys/fs/cgroup/soulmask.slice/memory.low
echo "4G"  > /sys/fs/cgroup/soulmask.slice/memory.min
echo 0     > /sys/fs/cgroup/soulmask.slice/memory.zswap.writeback

# Dev — limited
echo "8G"  > /sys/fs/cgroup/dev.slice/memory.high
echo "14G" > /sys/fs/cgroup/dev.slice/memory.max
echo 50    > /sys/fs/cgroup/dev.slice/cpu.weight
echo 1     > /sys/fs/cgroup/dev.slice/memory.zswap.writeback

# Devcontainer — standard priority, small protection
echo "2G"  > /sys/fs/cgroup/devcontainers.slice/memory.low
echo 100   > /sys/fs/cgroup/devcontainers.slice/cpu.weight
echo "[✓] cgroups created"

# 5. I/O limit for dev builds (check device number)
DEV=$(lsblk -o NAME,MAJ:MIN | awk '/vda/{print $2}')
echo "${DEV} rbps=209715200 wbps=209715200" > /sys/fs/cgroup/dev.slice/io.max
echo "[✓] I/O limit set on dev.slice (200 MB/s r/w)"

# 6. systemd service for persistence
cat > /etc/systemd/system/soulmask-cgroup.service <<'EOF'
[Unit]
Description=Soulmask cgroup protection
Before=wings.service
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/setup-soulmask-cgroup.sh

[Install]
WantedBy=multi-user.target
EOF

cp "$0" /usr/local/sbin/setup-soulmask-cgroup.sh
chmod +x /usr/local/sbin/setup-soulmask-cgroup.sh
systemctl daemon-reload
systemctl enable soulmask-cgroup.service
echo "[✓] systemd service installed"

echo ""
echo "=== Setup complete ==="
echo "Reboot to apply kernel command line changes."
echo "After reboot:"
echo "  cat /sys/module/zswap/parameters/compressor   # should show zstd"
echo "  cat /sys/fs/cgroup/soulmask.slice/memory.low  # should show 12884901888"
```

---

## Summary

| Decision | Choice | Why |
|----------|--------|-----|
| Compression | **zswap** (not zram) | Automatic LRU tiering; no LRU inversion |
| Algorithm | **zstd** | ~3:1 ratio, fast, plenty of CPU headroom |
| Pool size | **25% of RAM (4 GB)** | ~12 GB effective with zstd |
| Swap layout | Single 16 GB partition, or 4+16 GB tiered | zswap handles tiering internally |
| Soulmask protection | `memory.low=12G`, `memory.min=4G` | Last to reclaim, guaranteed 4 GB |
| Dev container limit | `memory.high=8G`, `memory.max=14G` | Throttles early, OOMs only in dev.slice |
| CPU priority | `cpu.weight=50` (dev) vs `100` (soulmask) | 2:1 preference under contention |
| I/O throttle | `io.max` at 200 MB/s for dev | Prevents build from saturating disk |
| Persistence | systemd oneshot service + GRUB cmdline | Reapplies on every boot |
| Kernel upgrade | Optional but recommended | DAMON auto-tuning in 7.0 is valuable |

