# DAMON/DAMO Setup Journal — System r1002

> **Date:** 2025-06-25  
> **Kernel:** 7.0.10+deb13-amd64 (Debian 13 "trixie")  
> **Goal:** Install DAMO user-space tool + create analysis scripts for
> hot/warm/cold memory classification using DAMON.

---

## Step 1 — System Discovery

### 1.1 Kernel & DAMON config

```bash
uname -r
# → 7.0.10+deb13-amd64

cat /boot/config-7.0.10+deb13-amd64 | grep -i DAMON
# CONFIG_DAMON=y
# CONFIG_DAMON_VADDR=y
# CONFIG_DAMON_PADDR=y
# CONFIG_DAMON_SYSFS=y
# CONFIG_DAMON_RECLAIM=y
# CONFIG_DAMON_LRU_SORT=y
# CONFIG_DAMON_STAT=y
# CONFIG_DAMON_STAT_ENABLED_DEFAULT=y    ← important! see Step 4
```

All needed DAMON features are built into the kernel. No custom kernel build required.

### 1.2 sysfs interface available

```bash
ls /sys/kernel/mm/damon/admin/kdamonds/
# → nr_kdamonds  (value: 0)
```

### 1.3 Memory layout

```bash
cat /proc/meminfo | head -15
# MemTotal:  ~8 GiB
# MemAvailable: ~6 GiB
# SwapTotal:  ~16 GiB
# SwapFree:   ~16 GiB  (swap unused)
# Zswap:      0 kB     (zswap not active)
```

---

## Step 2 — Install Python venv support

The default Debian Python environment is externally-managed (PEP 668),
so `pip3 install damo` fails. We need `python3-venv`.

### 2.1 Install python3.13-venv

```bash
apt-get install -y python3.13-venv
```

**Packages installed:**
- `python3.13-venv` (3.13.5-2+deb13u2)
- `python3-pip-whl` (25.1.1+dfsg-1)
- `python3-setuptools-whl` (78.1.1-0.1)

**Packages removed as side-effect:**
- `librtmp1`
- `linux-image-6.12.90+deb13.1-amd64` (old kernel, safe to remove on this system)

---

## Step 3 — Create venv and install damo

```bash
mkdir -p /root/work/damon-project
python3 -m venv /root/work/damon-project/venv
/root/work/damon-project/venv/bin/pip install damo
```

**Installed:** `damo-3.2.9`

### 3.1 Verify

```bash
/root/work/damon-project/venv/bin/damo version
# → 3.2.9

/root/work/damon-project/venv/bin/damo report sysinfo
# damo version: 3.2.9
# kernel version: 7.0.10+deb13-amd64
# DAMON version: 7.0
# available DAMON features:
# - interface/damon_sysfs
# - interface/damon_reclaim
# - interface/damon_lru_sort
# - interface/damon_stat
```

---

## Step 4 — Resolve DAMON_STAT Conflict

### 4.1 The problem

`CONFIG_DAMON_STAT_ENABLED_DEFAULT=y` means the `damon_stat` kernel module
starts automatically at boot and **occupies the DAMON kdamond**. Attempting
`damo start` fails with:

```
Cannot turn on damon since damon_stat is running.
You should disable it first. May I disable it for you? [Y/n]
```

Or when the kdamond is already busy:

```
could not turn on damon (writing on to .../state failed ([Errno 16] Device or resource busy))
```

### 4.2 Check status

```bash
cat /sys/module/damon_stat/parameters/enabled
# → Y
```

### 4.3 Disable damon_stat

The shell redirect `echo N > /sys/module/damon_stat/parameters/enabled` may
be blocked by sandbox restrictions. Use Python instead:

```bash
python3 -c "
with open('/sys/module/damon_stat/parameters/enabled', 'w') as f:
    f.write('N')
print('disabled')
"
```

### 4.4 Re-enable after manual DAMON use

```bash
python3 -c "
with open('/sys/module/damon_stat/parameters/enabled', 'w') as f:
    f.write('Y')
print('enabled')
"
```

> The analysis scripts in this project (`analyze_process.py`) handle
> damon_stat automatically via the damo CLI.

---

## Step 5 — Test damo end-to-end

### 5.1 Basic monitoring test (PID 1 = systemd)

```bash
# Start monitoring
/root/work/damon-project/venv/bin/damo start --target_pid 1 -s 100ms -a 2s

# Check it's running
cat /sys/kernel/mm/damon/admin/kdamonds/0/state
# → on

# Get a snapshot
/root/work/damon-project/venv/bin/damo report access
# → heatmap + region list with access frequencies and ages

# Stop
/root/work/damon-project/venv/bin/damo stop
```

### 5.2 Verify tried_regions are populated

After a damo start with `--damos_action stat`:

```bash
python3 -c "
import os
tr = '/sys/kernel/mm/damon/admin/kdamonds/0/contexts/0/schemes/0/tried_regions'
print('total_bytes:', open(os.path.join(tr, 'total_bytes')).read().strip())
"
# → e.g. 978485248 (~933 MiB)
```

**Key discovery:** The tried_regions subdirectories use **non-sequential
numeric names** (e.g., 85, 87, 89, ...), not 0, 1, 2, .... Our
`read_tried_regions` code had to iterate `os.listdir()` instead of assuming
sequential indices.

### 5.3 Age unit discovery

The `age` value in `tried_regions/<N>/age` is in **aggregation intervals**,
not microseconds. If `aggr_us=2_000_000`, an age of `6` means `6 × 2s = 12s`.

Our `Classifier.classify_regions()` converts: `age_us = raw_age × aggr_us`.

---

## Step 6 — Project File Layout

```
/root/work/damon-project/
├── venv/                          # Python virtualenv with damo 3.2.9
│   └── bin/damo
├── damon_cli.py                   # ★ Python-native master CLI (primary entry point)
├── run_analysis.sh                # Shell wrapper → delegates to damon_cli.py
├── DAMON-GUIDE.md                 # Comprehensive reference (660+ lines)
├── lib/
│   └── damon_analysis.py          # Shared library (SysfsInterface, Classifier,
│                                  #   Monitor, ReportFormatter, utilities)
├── analyze_process.py             # Process memory analysis (hot/warm/cold)
├── analyze_container.py           # Container memory analysis
├── visualize_memory.py            # Visualization (ASCII heatmap, charts, PNG)
├── output/                        # Default output directory
└── SETUP-JOURNAL.md              # This file
```

### 6.1 Make scripts executable

```bash
chmod +x /root/work/damon-project/run_analysis.sh
chmod +x /root/work/damon-project/analyze_process.py
chmod +x /root/work/damon-project/analyze_container.py
chmod +x /root/work/damon-project/visualize_memory.py
chmod +x /root/work/damon-project/lib/damon_analysis.py
```

---

## Step 7 — Debugging the Python Library

### 7.1 Direct sysfs writes failed

Initially the `Monitor` class used direct sysfs writes (writing to
`/sys/kernel/mm/damon/admin/kdamonds/0/state`). This produced:

```
OSError: [Errno 22] Invalid argument
```

The damo tool handles many sysfs edge cases (directory creation order,
default value initialization, kernel version quirks). Direct sysfs writes
are fragile.

### 7.2 Solution: use damo CLI as backend

Refactored `Monitor` to call `damo start` / `damo stop` via `subprocess.run()`,
keeping only the sysfs read path for `tried_regions` collection (which works
reliably).

### 7.3 damo binary path

The script auto-detects the venv damo binary relative to its own location:

```python
_DAMO_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'venv', 'bin', 'damo')
```

### 7.4 tried_regions non-sequential indices

The kernel creates tried_regions subdirectories with non-sequential numeric
names. Fixed by iterating `os.listdir()` and filtering directories.

---

## Step 8 — Verified Working Commands

### Full system diagnose

```bash
cd /root/work/damon-project
sudo ./run_analysis.sh diagnose
```

### Process analysis (JSON output)

```bash
sudo ./run_analysis.sh classify <PID> --duration 30 --output json
```

### Process analysis (text output)

```bash
sudo ./run_analysis.sh profile-pid <PID> --duration 20
```

### Continuous monitoring

```bash
sudo ./run_analysis.sh monitor-pid <PID>
```

### Enable DAMON_RECLAIM

```bash
sudo ./run_analysis.sh auto-reclaim on --min-age 60 --quota-sz 512M
sudo ./run_analysis.sh auto-reclaim status
```

### Visualize JSON results

```bash
python3 visualize_memory.py output/result.json --format chart
```

---

## Step 9 — Classification Thresholds (Defaults)

| Class | Access Rate | Age | Interpretation |
|-------|------------|-----|----------------|
| **Hot**  | ≥ 50% | — | Frequently accessed — keep in fast RAM |
| **Warm** | ≥ 5% | — | Moderately accessed, or low access but not yet aged |
| **Cold** | < 5% | ≥ 30s | Rarely accessed, stable pattern — compress/swap candidate |
| **Idle** | 0% | ≥ 120s | Completely untouched — evict to disk |

Override with: `--hot-rate`, `--warm-rate`, `--cold-age`, `--idle-age`

---

## Step 10 — Lessons Learned

1. **DAMON_STAT blocks manual DAMON use** — always check
   `/sys/module/damon_stat/parameters/enabled` first.

2. **Default intervals (5ms/100ms) are useless** — use at least
   `-s 100ms -a 2s` for meaningful results. For hot/cold classification on
   8 GiB systems, `-s 100ms -a 2s` is a good starting point.

3. **Use damo CLI, not raw sysfs** — damo handles the finicky sysfs
   interface (directory creation order, kernel version differences,
   default values). Reserve raw sysfs for reading results only.

4. **tried_regions age is in aggregation intervals** — multiply by
   `aggr_us` to get microseconds.

5. **tried_regions subdirectories are non-sequential** — kernel creates
   them with arbitrary numeric names; iterate the directory listing.

6. **Short monitoring runs show everything as "warm"** — age counter
   needs multiple aggregation periods to accumulate. For cold/idle
   classification, run for at least `cold_age * 2` seconds.

---

## Step 11 — Python-Native CLI (Replaced Bash Orchestration)

### 11.1 Why

The original `run_analysis.sh` was ~330 lines of bash with fragile string
parsing, limited error messages on failures, and no stack traces. Debugging a
bash script that calls Python sub-scripts is two layers of indirection.

### 11.2 Solution

Created `damon_cli.py` — a single-file Python CLI using `argparse` subcommands.
The old `run_analysis.sh` is now a 6-line wrapper that delegates to it.

### 11.3 Entry points (all equivalent)

```bash
# Python-native (canonical)
sudo ./damon_cli.py diagnose
sudo ./damon_cli.py classify 12345 --duration 30 --output json

# Shell wrapper (delegates to damon_cli.py)
sudo ./run_analysis.sh diagnose

# Full path
sudo /root/work/damon-project/venv/bin/python3 /root/work/damon-project/damon_cli.py diagnose
```

### 11.4 New `damon-stat` subcommand

Explicit control over the damon_stat kernel module (previously just a warning
in diagnose):

```bash
sudo ./damon_cli.py damon-stat status
sudo ./damon_cli.py damon-stat off
sudo ./damon_cli.py damon-stat on
```

### 11.5 Debug flags

```bash
# --verbose: INFO-level logging (shows damo commands being run, file paths, timing)
sudo ./damon_cli.py --verbose classify 12345 --duration 10

# --debug: full Python tracebacks on error instead of one-line messages
sudo ./damon_cli.py --debug profile-pid 99999
```

### 11.6 Command reference

| Command | Description |
|---------|-------------|
| `diagnose` | Full system readiness check |
| `profile-pid <PID>` | Profile a process (delegates to `analyze_process.py`) |
| `classify <PID>` | Hot/warm/cold classification with thresholds |
| `profile-container <NAME>` | Container analysis (delegates to `analyze_container.py`) |
| `profile-system [DURATION]` | Physical address space profile |
| `monitor-pid <PID>` | Live terminal dashboard |
| `damon-stat [on\|off\|status]` | Control damon_stat kernel module |
| `auto-reclaim [on\|off\|status]` | Control DAMON_RECLAIM |
| `auto-lru-sort [on\|off\|status]` | Control DAMON_LRU_SORT |

---

## Step 12 — Documentation Updates

### 12.1 DAMON-GUIDE.md expanded

Added sections 11–16 covering all practical discoveries from testing:
- vaddr vs paddr trade-off (virtual gaps, RSS vs address space)
- Region granularity control (min/max regions, tuning guidance)
- Parallel kdamonds (how to run multiple analyses simultaneously)
- Container analysis approach and limitations
- tried_regions internals (non-sequential indices, age units in aggregation intervals)
- Real-world tuning confirmation from this system

### 12.2 SCRIPTS.md created

Full usage reference for every script in the project:
- `damon_cli.py` — all subcommands with option tables
- `analyze_process.py` — standalone usage with all flags
- `analyze_container.py` — Docker/Podman workflow
- `batch_report.py` — multi-PID comparison
- `visualize_memory.py` — post-processing charts
- `lib/damon_analysis.py` — Python API reference
- Typical workflows (quick check, deep classification, swap decisions)
- Deployment guide (minimal 3-file setup for another system)
- Debugging section (common failures and fixes)

---

## Reference: Key Paths on This System

| Path | Purpose |
|------|---------|
| `/sys/kernel/mm/damon/admin/` | DAMON sysfs root |
| `/sys/kernel/mm/damon/admin/kdamonds/nr_kdamonds` | Number of kdamonds (write to create) |
| `/sys/kernel/mm/damon/admin/kdamonds/0/state` | kdamond control (on/off/commit/...) |
| `/sys/module/damon_stat/parameters/enabled` | DAMON_STAT toggle |
| `/sys/module/damon_reclaim/parameters/` | DAMON_RECLAIM settings |
| `/sys/module/damon_lru_sort/parameters/` | DAMON_LRU_SORT settings |
| `/boot/config-7.0.10+deb13-amd64` | Kernel build config |
| `/root/work/damon-project/venv/bin/damo` | damo executable (v3.2.9) |
| `/root/work/damon-project/venv/bin/python3` | Python with damo module |
