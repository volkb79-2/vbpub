# DAMON Analysis Scripts — Usage Reference

> Project: `/root/work/damon-project/`  
> Environment: Python venv with damo v3.2.9, Linux 7.0+ with `CONFIG_DAMON_SYSFS=y`  
> Root required for all commands that start/stop DAMON.

---

## File Map

| File | Role | Standalone? |
|------|------|-------------|
| `damon_cli.py` | **Primary CLI** — subcommand-based entry point | Yes |
| `analyze_process.py` | Per-process vaddr analysis | Yes (needs `lib/`) |
| `analyze_container.py` | Container analysis (Docker/Podman) | Yes (needs `lib/`) |
| `batch_report.py` | Batch-analyze multiple PIDs | Yes (needs `lib/`) |
| `visualize_memory.py` | Post-process JSON → charts | Yes |
| `lib/damon_analysis.py` | Shared library (Classifier, Monitor, etc.) | No — imported by others |
| `run_analysis.sh` | Thin shell wrapper → `damon_cli.py` | Delegates |
| `DAMON-GUIDE.md` | Comprehensive DAMON/DAMO reference | Read-only |

---

## Quick Start

```bash
cd /root/work/damon-project

# Always run as root
sudo ./damon_cli.py diagnose          # check everything is ready
sudo ./damon_cli.py classify 12345    # quick hot/warm/cold on a PID
```

---

## `damon_cli.py` — Primary CLI

Single entry point for all operations. Uses argparse subcommands.

```
sudo ./damon_cli.py <command> [options]
```

### Global Flags

| Flag | Effect |
|------|--------|
| `--verbose`, `-v` | INFO logging — shows damo commands, file paths, timing |
| `--debug` | Full Python tracebacks on error instead of one-liners |

### Subcommands

#### `diagnose`

Full system readiness check — kernel config, sysfs, damo version, loaded modules,
memory stats, top processes.

```bash
sudo ./damon_cli.py diagnose
```

#### `classify <PID>`

Monitor a process and classify its memory regions as hot/warm/cold/idle.

```bash
sudo ./damon_cli.py classify 12345 \
    --duration 60 \
    --output json \
    --output-file result.json \
    --hot-rate 50 --warm-rate 5 --cold-age 30 --idle-age 120
```

| Option | Default | Description |
|--------|---------|-------------|
| `--duration SEC` | 60 | Monitoring duration (seconds) |
| `--output {text,json,csv}` | text | Output format |
| `--output-file PATH` | stdout | File to write output |
| `--hot-rate PCT` | 50 | Hot threshold — access rate % |
| `--warm-rate PCT` | 5 | Warm threshold — access rate % |
| `--cold-age SEC` | 30 | Cold threshold — age in seconds |
| `--idle-age SEC` | 120 | Idle threshold — age in seconds |
| `--sample-us US` | 100000 | Sampling interval (µs) |
| `--aggr-us US` | 2000000 | Aggregation interval (µs) |
| `--update-us US` | — | Update interval (µs) |
| `--min-regions N` | — | Minimum monitoring regions |
| `--max-regions N` | — | Maximum monitoring regions |

**Output modes:**
- `text` — human-readable table with summary + region details
- `json` — structured output with metadata, summary, and per-region arrays
- `csv` — flat CSV for spreadsheet import

#### `profile-pid <PID>`

Same as `classify` but intended for quick ad-hoc profiling. Passes all extra
arguments through to `analyze_process.py`.

```bash
sudo ./damon_cli.py profile-pid 12345 --duration 30
```

#### `profile-system [DURATION]`

System-wide physical memory profile using `paddr` operations.

```bash
sudo ./damon_cli.py profile-system 120     # 2 minutes
```

#### `profile-container <NAME>`

Profile all processes in a Docker/Podman container.

```bash
sudo ./damon_cli.py profile-container my-container --duration 60
```

#### `monitor-pid <PID>`

Live terminal dashboard — refreshes every 5 seconds. Ctrl+C to stop.

```bash
sudo ./damon_cli.py monitor-pid 12345
```

#### `damon-stat [on|off|status]`

Control the `damon_stat` kernel module (must be disabled for manual DAMON use).

```bash
sudo ./damon_cli.py damon-stat status
sudo ./damon_cli.py damon-stat off      # before manual DAMON use
sudo ./damon_cli.py damon-stat on       # re-enable after
```

#### `auto-reclaim [on|off|status]`

Control DAMON_RECLAIM — proactive cold-page reclamation.

```bash
# Check status
sudo ./damon_cli.py auto-reclaim status

# Enable with custom thresholds
sudo ./damon_cli.py auto-reclaim on \
    --min-age 60 \          # pages not accessed for 60s → reclaim
    --quota-sz 512M \       # max 512 MiB reclaimed per second
    --quota-ms 10           # max 10ms CPU time per second
```

#### `auto-lru-sort [on|off|status]`

Control DAMON_LRU_SORT — proactive LRU hot/cold prioritization.

```bash
# Enable
sudo ./damon_cli.py auto-lru-sort on \
    --hot-thres 500 \       # ≥50% access → prioritize on LRU
    --cold-age 120          # no access for 120s → deprioritize
```

---

## `analyze_process.py` — Standalone Process Analysis

Direct script for per-process vaddr monitoring. Used by `damon_cli.py classify`
but can be called independently.

```bash
sudo ./venv/bin/python3 analyze_process.py <PID> [options]
sudo ./venv/bin/python3 analyze_process.py --command "myapp --flag" [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--duration SEC` | 60 | Monitoring duration |
| `--sample-us US` | 100000 | Sampling interval (µs) |
| `--aggr-us US` | 2000000 | Aggregation interval (µs) |
| `--hot-rate PCT` | 50 | Hot threshold |
| `--warm-rate PCT` | 5 | Warm threshold |
| `--cold-age SEC` | 30 | Cold age threshold |
| `--idle-age SEC` | 120 | Idle age threshold |
| `--output {text,json,csv}` | text | Output format |
| `--output-file PATH` | stdout | Output destination |
| `--interval SEC` | 10 | Snapshot interval (continuous mode) |
| `--continuous` | off | Run continuously, print snapshots |
| `--min-regions N` | 10 | Minimum monitoring regions |
| `--max-regions N` | 1000 | Maximum monitoring regions |
| `--ops {vaddr,paddr}` | vaddr | Operations set |

**Examples:**

```bash
# Quick 30s text profile
sudo ./venv/bin/python3 analyze_process.py 12345 --duration 30

# JSON output to file, 2-minute run
sudo ./venv/bin/python3 analyze_process.py 12345 --duration 120 \
    --output json --output-file profile.json

# Continuous mode — prints a snapshot every 5 seconds
sudo ./venv/bin/python3 analyze_process.py 12345 --continuous --interval 5

# Custom intervals for better classification
sudo ./venv/bin/python3 analyze_process.py 12345 \
    --duration 300 --sample-us 400000 --aggr-us 8000000
```

---

## `analyze_container.py` — Container Analysis

Profiles all processes inside a Docker or Podman container.

```bash
sudo ./venv/bin/python3 analyze_container.py <CONTAINER_NAME> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--duration SEC` | 120 | Monitoring duration |
| `--mode {process,physical}` | process | process = per-PID vaddr; physical = paddr with memcg filter |
| `--cgroup-path PATH` | auto | Required for `--mode physical` |
| `--output {text,json,csv}` | text | Output format |
| `--output-file PATH` | stdout | Output destination |

**How it works (process mode):**

1. `docker inspect --format '{{.State.Pid}}' <name>` → get container init PID
2. Walk `/proc/<pid>/stat` recursively to find all child processes
3. Skip processes with RSS < 100 kB
4. Run `Monitor.configure_vaddr()` + `start()` + `collect()` + `stop()` for each
5. Classify all regions, aggregate results

**How it works (physical mode):**

1. Find cgroup path from Docker/Podman inspect
2. Use damo with `--damos_filter reject none memcg <path>` to filter physical
   pages belonging to that cgroup
3. This mode requires knowing the cgroup mount path relative to cgroup root

**Limitations:**
- Processes are profiled **sequentially** (no parallel kdamond support yet)
- Requires `docker` or `podman` CLI on the host
- Physical mode memcg filtering may need manual `--cgroup-path` on some setups

---

## `batch_report.py` — Multi-PID Batch Analysis

Runs `analyze_process.py` logic against a hardcoded list of PIDs and prints
a comparison table.

```bash
sudo ./venv/bin/python3 batch_report.py
```

Edit the `PIDS` list at the top of the script to customize targets. Output is
plain text — a summary table followed by per-process active region details.

---

## `visualize_memory.py` — Post-Processing Visualization

Reads JSON output from `classify` / `analyze_process.py` / `analyze_container.py`
and generates visualizations.

```bash
python3 visualize_memory.py <input.json> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--format {ascii,chart,png,all}` | all | Output format |
| `--output PATH` | auto | File path (for PNG) |
| `--title TEXT` | "Memory Access Analysis" | Chart title |

**ASCII heatmap** — single-row representation of the address space, each
character represents temperature (0=coldest, 9=hottest).

**Distribution chart** — bar chart showing hot/warm/cold/idle breakdown by
region count and byte size.

**Size-class chart** — breaks regions into size buckets (4 KiB, 16 KiB, …,
1 GiB+) showing how much memory falls into each.

**PNG output** — two-panel matplotlib figure (temperature scatter + class
breakdown bar chart). Requires `pip install matplotlib`.

---

## `lib/damon_analysis.py` — Shared Library

Imported by all scripts. Public classes:

### `SysfsInterface`

Static methods for reading/writing DAMON sysfs files. Handles kdamond creation,
context/target/scheme management, tried_regions reading.

```python
from damon_analysis import SysfsInterface
s = SysfsInterface()
regions = s.read_tried_regions(0, 0, 0)  # kdamond=0, ctx=0, scheme=0
```

### `Classifier`

Hot/warm/cold/idle classification with configurable thresholds.

```python
from damon_analysis import Classifier
c = Classifier(hot_access_rate_pct=50, warm_access_rate_pct=5,
               cold_age_sec=30, idle_age_sec=120)
classified = c.classify_regions(regions, sample_us=100000, aggr_us=2000000)
summary = c.summary(classified)
```

### `Monitor`

High-level session manager — uses damo CLI for start/stop, sysfs for reading.

```python
from damon_analysis import Monitor
m = Monitor(damo_bin='/path/to/damo')
m.configure_vaddr(pid=12345, sample_us=400000, aggr_us=8000000)
m.start()
# ... wait ...
regions = m.collect()
m.stop()
```

**Known gaps:**
- Only uses kdamond index 0 / context 0 / scheme 0 (no parallel support)
- `min_regions` / `max_regions` accepted but not forwarded to damo
- `_damo()` uses `subprocess.run` with `capture_output=True` — hangs if damo
  prompts interactively (e.g., "May I disable damon_stat?")

### `ReportFormatter`

Three output formats:

```python
from damon_analysis import ReportFormatter
f = ReportFormatter()
print(f.human_readable(classified, title="My Analysis"))
print(f.json_report(classified, metadata={}))
print(f.csv_report(classified))
```

### Utility Functions

```python
from damon_analysis import get_process_info, get_container_pids
info = get_process_info(12345)          # {pid, comm, vm_size_kb, vm_rss_kb}
pids = get_container_pids("my-app")     # [12345, 12346, ...]
```

---

## Typical Workflows

### Workflow 1: Quick Process Check

```bash
# What's using memory in my process RIGHT NOW?
sudo ./damon_cli.py classify $(pidof myapp) --duration 30
```

### Workflow 2: Deep Hot/Cold Classification

```bash
# 5-minute analysis with better intervals
sudo ./damon_cli.py classify $(pidof myapp) \
    --duration 300 \
    --output json --output-file deep_profile.json

# Visualize
python3 visualize_memory.py deep_profile.json --format chart
python3 visualize_memory.py deep_profile.json --format png --output heatmap.png
```

### Workflow 3: System-Wide Swap/ZRAM Decision

```bash
# 10-minute physical memory analysis
sudo damo start paddr -s 400ms -a 8s \
    --damos_action stat \
    --damos_access_rate '0%' max \
    --damos_sz_region 0 max \
    --damos_age 0 max

sleep 600

# Collect
sudo python3 -c "
import sys; sys.path.insert(0,'lib')
from damon_analysis import SysfsInterface, Classifier, ReportFormatter
s = SysfsInterface()
s.kdamond_update_tried_regions(0); import time; time.sleep(0.2)
regions = s.read_tried_regions(0,0,0)
c = Classifier(cold_age_sec=60, idle_age_sec=300)
classified = c.classify_regions(regions, 400000, 8000000)
print('Hot (keep in RAM + ZRAM):', c.summary(classified)['hot']['bytes'])
print('Cold (ZSWAP):', c.summary(classified)['cold']['bytes'])
print('Idle (disk swap):', c.summary(classified)['idle']['bytes'])
"

sudo damo stop
```

### Workflow 4: Enable Automatic Reclamation

```bash
# Start reclaiming pages not touched for 2 minutes
sudo ./damon_cli.py auto-reclaim on --min-age 120 --quota-sz 256M
# Check progress
sudo ./damon_cli.py auto-reclaim status
# Disable
sudo ./damon_cli.py auto-reclaim off
```

---

## Deploying to Another System

Minimum required files (3):

```
target-system/
├── lib/
│   └── damon_analysis.py    ← the shared library
├── analyze_process.py        ← for per-PID analysis
└── damon-venv/               ← venv with damo installed
```

Setup on target:

```bash
# 1. Install venv support (Debian/Ubuntu)
sudo apt-get install -y python3-venv

# 2. Create venv + install damo
python3 -m venv damon-venv
damon-venv/bin/pip install damo

# 3. Copy project files
scp -r lib/ analyze_process.py target:~

# 4. Verify
sudo damon-venv/bin/python3 -c "
from lib.damon_analysis import SysfsInterface
print('DAMON available:', SysfsInterface.is_available())
"

# 5. Run
sudo damon-venv/bin/python3 analyze_process.py <PID> --duration 60 --output json
```

For the full CLI experience, also copy `damon_cli.py` and `batch_report.py`.

---

## Debugging

### Script hangs

1. Check if damon_stat is running: `cat /sys/module/damon_stat/parameters/enabled`
2. Check if kdamond is already busy: `cat /sys/kernel/mm/damon/admin/kdamonds/0/state`
3. Force stop: `damo stop`
4. Run with verbose logging: `./damon_cli.py --verbose classify 12345`

### "No regions collected"

- Increase duration: `--duration 120`
- Increase aggregation interval: `--aggr-us 8000000`
- Verify process has mapped memory: `cat /proc/<PID>/status | grep VmRSS`
- For vaddr, ensure the process isn't a zombie

### "Device or resource busy"

DAMON is already running. Stop it first: `damo stop`

### Interactive prompt blocking subprocess

The `Monitor._damo()` method captures stdout/stderr but cannot respond to
prompts (e.g., damo asking "May I disable damon_stat?"). Disable damon_stat
beforehand: `./damon_cli.py damon-stat off`

---

## New in This Version

### Region Granularity Control

`classify`, `profile-pid`, and the underlying `Monitor` class now support fine-grained
control over DAMON's region count:

```bash
# More regions = finer granularity, higher CPU overhead
sudo ./damon_cli.py classify 12345 --min-regions 20 --max-regions 2000

# Fewer regions = coarser, lower overhead (good for production DAMOS)
sudo ./damon_cli.py classify 12345 --min-regions 5 --max-regions 100
```

| Flag | Default | Description |
|------|---------|-------------|
| `--min-regions N` | kernel default (10) | Minimum regions — lower bound on monitoring quality |
| `--max-regions N` | kernel default (1000) | Maximum regions — upper bound on CPU overhead |
| `--sample-us US` | 100000 | Sampling interval in microseconds |
| `--aggr-us US` | 2000000 | Aggregation interval in microseconds |
| `--update-us US` | kernel default | Update interval in microseconds |

These are passed through to damo's `--min_nr_regions`, `--max_nr_regions`,
and `--update_us` options.

### Parallel kdamond Support

The `Monitor` class accepts a `kdamond_idx` parameter for running multiple
analyses simultaneously:

```python
from damon_analysis import Monitor

m1 = Monitor(kdamond_idx=0)   # monitors PID 1234 (vaddr)
m2 = Monitor(kdamond_idx=1)   # monitors system RAM (paddr)

m1.configure_vaddr(pid=1234)
m2.configure_paddr()

# Create both kdamonds
m1.sysfs.create_kdamond(0)
m1.sysfs.create_kdamond(1)

m1.start()
m2.start()
# Both run in parallel — different kernel threads

regions1 = m1.collect()
regions2 = m2.collect()

m1.stop()
m2.stop()
```

### Requirements Check

New `requirements` subcommand — non-root friendly prerequisites check:

```bash
./damon_cli.py requirements
```

Checks: Python version, damo installation, shared library, kernel config flags,
sysfs availability, damon_stat status, and optional tools (Docker, perf).

### New Reference Document

**`GAMINGHOST-SWAP.md`** — comprehensive guide for the Soulmask gaming host:
zswap vs zram analysis, swap architecture design, cgroup v2 priority hierarchy,
I/O throttling, persistence across reboots, DAMON-driven dynamic tuning.
