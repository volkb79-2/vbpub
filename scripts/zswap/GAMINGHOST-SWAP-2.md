# Memory Architecture for Soulmask Gaming Host

> **Target system:** Debian 12 VM, 8 cores, 16 GB RAM, kernel 6.12.90 (backports)  
> **Workload:** Pterodactyl Wings → Docker → Soulmask game server (13-14 GB, 80% single-core)  
> **Secondary workload:** VSC SSH dev, up to 20 dev containers, large Docker builds (~12 GB images)  
> **Constraint:** RAM is the scarce resource — frequent container start/tear-down and image builds compete with the game server.

---

## 1. Executive Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Compression layer | **zswap** (not zram) | Automatic LRU tiering — hot pages stay compressed in RAM, cold pages drain to disk. zram causes LRU inversion (cold pages calcify in fast RAM, hot pages spill to slow disk). |
| Compression algorithm | **zstd** | ~2.5–3:1 ratio at moderate CPU cost. 8-core system has ample CPU headroom. |
| zswap pool size | **25% of RAM = 4 GB** | With zstd ~3:1 ratio, ~12 GB effective. Soulmask's active set stays compressed; cold pages tier to disk. |
| Swap layout | **Single 20 GB swap partition** | zswap handles tiering internally. Multiple partitions on one disk don't increase parallelism. |
| Soulmask protection | cgroup `memory.low=12G`, `memory.min=4G` | Soulmask is the last process to be reclaimed. `memory.min` guarantees 4 GB even under extreme pressure. |
| Dev container limits | cgroup `memory.high=8G`, `memory.max=14G` | Throttles reclaim early; OOM kills only inside dev.slice. |
| I/O throttle | cgroup `io.max` at 200 MB/s r/w for dev.slice | Prevents Docker builds from saturating the single disk. |
| Kernel upgrade | Debian 13 (7.0.10) optional | DAMON intervals auto-tuning adds value but 6.12 already has zswap shrinker, per-cgroup writeback, DAMON. |

---

## 2. zswap vs zram — The Critical Choice

### 2.1 How They Differ Architecturally

```
zswap:  RAM → [kernel swap-out] → zswap intercepts
            → compress → store in RAM pool
            → pool full? → LRU evict coldest → disk swap
            → page fault? → decompress from pool OR read from disk

zram:   RAM → [kernel swap-out] → block layer → /dev/zram0 (compressed block device)
            → /dev/zram0 full? → spill to next swap device (disk)
            → BUT: cold pages from early in the session permanently occupy zram
            → hot pages from NOW forced to disk: LRU INVERSION
```

### 2.2 Why zram + disk swap is harmful (LRU inversion)

From Chris Down (Meta kernel MM engineer, works on swap at scale):

> *"Do not run zram alongside disk swap wherever possible. In such setups, zram fills fast RAM with cold, stale pages while pushing your active working set onto slow disk, making things actively worse than if you had no compressed swap at all."*

The mechanism:
1. System boots, Soulmask starts, some init pages swap out early → land in zram
2. Hours pass, dev containers start/stop, memory pressure builds
3. zram is now **full** of cold init pages that haven't been accessed in hours
4. Soulmask's current active pages (game tick, physics, network buffers) are swapped NOW
5. zram is full → these hot pages spill to **slow disk swap**
6. Soulmask stutters on every game tick that faults a page back from disk

zswap avoids this because its LRU eviction is part of the kernel's reclaim path — cold pages are *automatically* pushed to disk, keeping the compressed RAM pool for the current working set.

### 2.3 When zram IS appropriate

- **Diskless embedded systems** (Raspberry Pi, IoT) — no disk swap to fall back to
- **Security isolation** — keeping all data in RAM, never touching persistent storage
- **Android** — uses zram with userspace OOM manager (lmkd) to mitigate LRU inversion

For your gaming VM with a disk, **zswap is the clear winner**.

### 2.4 Source

Chris Down, *"Debunking zswap and zram myths"* (March 2026):  
https://chrisdown.name/2026/03/24/zswap-vs-zram-when-to-use-what.html

---

## 3. Swap Device Layout

### 3.1 Single disk reality

Your VM has one virtual disk (vda). Multiple partitions on the same backing storage do not provide parallel I/O — the hypervisor serializes access. Therefore:

**Recommendation: One swap partition, 20 GB.**

zswap manages tiering internally via LRU eviction. A second partition with lower priority adds complexity for marginal (if any) benefit on single-disk VM.

### 3.2 Partitioning (60 GB free at end of disk)

```
/dev/vda5  (LVM PV)
├── root_lv      ~40 GB  system root (existing)
├── swap_lv       20 GB  swap  ← NEW
└── (remaining    ~0 GB  headroom for future)
```

Create with LVM for flexibility:

```bash
lvcreate -L 20G -n swap_lv vg0
mkswap /dev/vg0/swap_lv
echo '/dev/vg0/swap_lv  none  swap  sw  0  0' >> /etc/fstab
swapon /dev/vg0/swap_lv
```

### 3.3 If you had multiple physical disks

If this were a bare-metal host with NVMe + SATA SSD:

```
/dev/nvme0n1p1  8 GB   swap  pri=100   ← fast, small — zswap LRU evictions land here
/dev/sda2       32 GB  swap  pri=10    ← slow, large — cold overflow
```

zswap evictions naturally prefer the high-priority device. When it fills, cold data spills to the large device. The kernel's LRU ensures the coldest pages end up on the slowest storage.

---

## 4. zswap Configuration

### 4.1 Kernel command line (persistent)

```bash
# /etc/default/grub
GRUB_CMDLINE_LINUX="... zswap.compressor=zstd zswap.max_pool_percent=25"
update-grub
```

### 4.2 Runtime (immediate effect)

```bash
echo zstd > /sys/module/zswap/parameters/compressor
echo 25   > /sys/module/zswap/parameters/max_pool_percent
echo Y    > /sys/module/zswap/parameters/shrinker_enabled
echo 80   > /sys/module/zswap/parameters/accept_threshold_percent
```

### 4.3 What each parameter does

| Parameter | Value | Effect |
|-----------|-------|--------|
| `compressor` | `zstd` | ~3:1 compression ratio. Alternatives: `lz4` (faster, ~2:1), `deflate` (denser, ~4:1, CPU-heavy) |
| `max_pool_percent` | `25` | 25% of 16 GB = 4 GB pool. At 3:1 = 12 GB effective compressed storage |
| `shrinker_enabled` | `Y` | Proactively evicts cold pages to disk *before* pool hits limit. Prevents "pool full → stall → evict" thrashing |
| `accept_threshold_percent` | `80` | After pool hits limit, don't accept new pages until it shrinks to 80%. Prevents rapid fill/evict cycles |

### 4.4 Compression algorithm comparison (16 GB system)

| Algorithm | Comp Ratio | Effective Pool (4 GB) | CPU per 1 GB swap | Latency (decompress) |
|-----------|-----------|----------------------|-------------------|---------------------|
| **zstd** | ~3:1 | ~12 GB | Medium | ~5 µs/page |
| lz4 | ~2:1 | ~8 GB | Very low | ~2 µs/page |
| lz4hc | ~2.5:1 | ~10 GB | Medium | ~3 µs/page |
| deflate | ~4:1 | ~16 GB | High | ~15 µs/page |

zstd gives the best balance: 12 GB effective with moderate CPU cost. With Soulmask using only 80% of one core, you have 7.2 cores idle for compression.

### 4.5 Verify at runtime

```bash
# Check compressor
cat /sys/module/zswap/parameters/compressor

# Pool usage stats
cat /sys/kernel/debug/zswap/pool_total_size    # current pool size
cat /sys/kernel/debug/zswap/stored_pages       # pages currently compressed
cat /sys/kernel/debug/zswap/written_back_pages # pages evicted to disk

# Hit rates (good indicator of effectiveness)
grep -r . /sys/kernel/debug/zswap/ 2>/dev/null
```

---

## 5. cgroup v2 Priority Architecture

### 5.1 Hierarchy

```
/sys/fs/cgroup/
├── soulmask.slice/          ← game server: PROTECTED
│   ├── memory.low  = 12G   ← best-effort protection
│   ├── memory.min  = 4G    ← hard guarantee
│   ├── memory.zswap.writeback = 0   ← never proactively evict
│   └── cpu.weight   = 100  ← standard (default)
│
├── system.slice/            ← systemd services (automatic)
│   └── (sshd, cron, apt, certbot, pterodactyl wings, ...)
│
├── dev.slice/               ← dev containers: LIMITED
│   ├── memory.high = 8G    ← soft limit → throttles reclaim
│   ├── memory.max  = 14G   ← hard limit → OOM killer
│   ├── memory.zswap.writeback = 1   ← allow eviction to disk
│   ├── cpu.weight  = 50    ← lower CPU priority
│   └── io.max      = "8:0 rbps=209715200 wbps=209715200"
│
└── devcontainers.slice/     ← the dev's own devcontainer
    ├── memory.low  = 2G    ← protection above dev.slice
    └── cpu.weight  = 100   ← standard priority
```

### 5.2 How each knob behaves under pressure

**`memory.low` (best-effort protection):**
- If total memory usage < 16 GB: Soulmask gets up to 12 GB, no questions asked
- If pressure builds: kernel reclaims from **unprotected** cgroups first (dev.slice, system.slice)
- If pressure continues: Soulmask's protection shrinks **proportionally** — it's the *last* to be reclaimed from, but not immune

**`memory.min` (hard guarantee):**
- Soulmask is guaranteed 4 GB of RAM, period
- The kernel will OOM-kill processes in *other* cgroups before dipping below this
- Set this to Soulmask's measured active working set (use DAMON to find this)

**`memory.high` (soft limit, throttling):**
- When dev.slice exceeds 8 GB, the kernel **throttles allocation** and triggers reclaim *within dev.slice only*
- This slows down the offending container/build without killing anything
- Much gentler than `memory.max`

**`memory.max` (hard limit, OOM):**
- If dev.slice hits 14 GB, the kernel OOM-kills the largest process *inside dev.slice*
- Soulmask (in a different slice) is never considered

**`cpu.weight`:**
- Under CPU contention, Soulmask gets 100/(100+50) = 67% of CPU, dev gets 33%
- When dev is idle (most of the time), Soulmask gets 100%
- Not a hard cap — idle CPU is always available

**`memory.zswap.writeback`:**
- `0` for Soulmask means: even when zswap evicts cold pages, Soulmask's pages stay in the compressed RAM pool
- They can still be reclaimed under extreme pressure (memory.min takes priority), but they won't be *proactively* evicted to disk

### 5.3 I/O Throttling

```bash
# Find device major:minor
lsblk -o NAME,MAJ:MIN | grep vda
# → vda  8:0

# Limit dev.slice to 200 MB/s reads + writes
echo "8:0 rbps=209715200 wbps=209715200" > /sys/fs/cgroup/dev.slice/io.max

# Check current limits
cat /sys/fs/cgroup/dev.slice/io.max
```

This prevents a `docker build` or `docker pull` from saturating the disk. 200 MB/s is enough for builds to complete in reasonable time while leaving I/O headroom for Soulmask saves and system operations.

### 5.4 Network Priority (Optional)

For ensuring Soulmask game traffic isn't starved by Docker pulls:

```bash
# Simple port-based priority with tc
tc qdisc add dev eth0 root handle 1: prio

# Soulmask UDP game ports → high priority (band 1)
tc filter add dev eth0 protocol ip parent 1: prio 1 \
    u32 match ip dport 7777 0xffff flowid 1:1
tc filter add dev eth0 protocol ip parent 1: prio 1 \
    u32 match ip dport 7778 0xffff flowid 1:1

# Everything else → normal priority (band 2, default)
```

This is coarse but effective for a single-service host. For more sophisticated QoS, use `net_cls` cgroup + iptables marking.

---

## 6. Persistence Across Reboots

### 6.1 systemd service — cgroup setup

```ini
# /etc/systemd/system/soulmask-cgroup.service
[Unit]
Description=Soulmask cgroup memory protection
Before=wings.service
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/setup-cgroups.sh

[Install]
WantedBy=multi-user.target
```

```bash
# /usr/local/sbin/setup-cgroups.sh
#!/bin/bash
set -e

CGROUP=/sys/fs/cgroup

# Create slices
mkdir -p "$CGROUP/soulmask.slice"
mkdir -p "$CGROUP/dev.slice"
mkdir -p "$CGROUP/devcontainers.slice"

# --- Soulmask ---
echo "12G" > "$CGROUP/soulmask.slice/memory.low"
echo "4G"  > "$CGROUP/soulmask.slice/memory.min"
echo 0     > "$CGROUP/soulmask.slice/memory.zswap.writeback"

# --- Dev containers ---
echo "8G"  > "$CGROUP/dev.slice/memory.high"
echo "14G" > "$CGROUP/dev.slice/memory.max"
echo 50    > "$CGROUP/dev.slice/cpu.weight"
echo 1     > "$CGROUP/dev.slice/memory.zswap.writeback"

# I/O throttle (adjust device number with `lsblk -o NAME,MAJ:MIN`)
echo "8:0 rbps=209715200 wbps=209715200" > "$CGROUP/dev.slice/io.max"

# --- Devcontainer ---
echo "2G"  > "$CGROUP/devcontainers.slice/memory.low"
echo 100   > "$CGROUP/devcontainers.slice/cpu.weight"
```

```bash
systemctl daemon-reload
systemctl enable soulmask-cgroup.service
```

### 6.2 sysctl

```bash
# /etc/sysctl.d/99-memory.conf
vm.swappiness = 60
vm.vfs_cache_pressure = 50
vm.watermark_scale_factor = 10
```

| Parameter | Value | Why |
|-----------|-------|-----|
| `swappiness` | 60 | Default. Lower (e.g., 30) keeps more file cache; higher (e.g., 80) swaps more aggressively. 60 is balanced. |
| `vfs_cache_pressure` | 50 | Below 100 = prefer keeping directory/inode caches. These are cheap to reclaim but useful for Docker operations. |
| `watermark_scale_factor` | 10 | Default. Controls how aggressively kswapd wakes up. Only tune if you see kswapd CPU spikes. |

### 6.3 Docker cgroup parent

To automatically place Soulmask containers into the protected cgroup, configure Docker:

**Option A — Pterodactyl Wings configuration:**
```yaml
# In the Wings config or per-server settings:
docker:
  cgroup_parent: "soulmask.slice"
```

**Option B — Docker daemon default:**
```json
// /etc/docker/daemon.json
{
  "cgroup-parent": "soulmask.slice"
}
```

**Option C — Post-start hook (most reliable for Pterodactyl):**
```bash
#!/bin/bash
# /var/lib/pterodactyl/hooks/post-start.sh
# Called by Pterodactyl after starting a server container

CONTAINER_ID="$1"
PID=$(docker inspect -f '{{.State.Pid}}' "$CONTAINER_ID" 2>/dev/null)
if [ -n "$PID" ]; then
    echo "$PID" > /sys/fs/cgroup/soulmask.slice/cgroup.procs
fi
```

### 6.4 Dev containers

For containers started by the developer via SSH, use a shell wrapper or Docker Compose `cgroup_parent`:

```yaml
# docker-compose.yml for dev stacks
services:
  myapp:
    cgroup_parent: dev.slice
    # ...
```

Or a shell alias:
```bash
alias docker-dev='docker run --cgroup-parent=/dev.slice ...'
```

The devcontainer itself:
```bash
# Place in devcontainers.slice (protected from other dev containers)
echo $$ > /sys/fs/cgroup/devcontainers.slice/cgroup.procs
```

### 6.5 zswap kernel command line

```bash
# /etc/default/grub
GRUB_CMDLINE_LINUX="... zswap.compressor=zstd zswap.max_pool_percent=25 zswap.shrinker_enabled=1"
update-grub
```

---

## 7. DAMON Integration for Dynamic Tuning

### 7.1 Measure Soulmask's active working set

Run once during normal gameplay to establish baselines:

```bash
cd /root/work/damon-project

# Find Soulmask PID (via Docker)
PID=$(docker inspect -f '{{.State.Pid}}' soulmask-server)

# 5-minute physical memory analysis
sudo ./damon_cli.py classify "$PID" \
    --duration 300 \
    --sample-us 400000 --aggr-us 8000000 \
    --hot-rate 50 --cold-age 60 \
    --output json --output-file soulmask-baseline.json
```

### 7.2 Auto-adjust memory.min based on DAMON

```bash
#!/bin/bash
# /usr/local/sbin/adjust-soulmask-protection.sh
# Run via cron every 5 minutes

PID=$(docker inspect -f '{{.State.Pid}}' soulmask-server 2>/dev/null)
[ -z "$PID" ] && exit 0

# Quick DAMON snapshot
HOT_BYTES=$(/root/work/damon-project/venv/bin/python3 -c "
import sys; sys.path.insert(0,'/root/work/damon-project/lib')
from damon_analysis import SysfsInterface, Classifier
import subprocess, time

# Start short monitoring
subprocess.run(['damo','start','--target_pid','$PID','-s','400ms','-a','8s',
    '--damos_action','stat','--damos_access_rate','0%','max',
    '--damos_sz_region','0','max','--damos_age','0','max'],
    capture_output=True, timeout=30)
time.sleep(20)

s = SysfsInterface()
s.kdamond_update_tried_regions(0); time.sleep(0.2)
regions = s.read_tried_regions(0,0,0)

c = Classifier(hot_access_rate_pct=50, warm_access_rate_pct=5)
classified = c.classify_regions(regions, 400000, 8000000)
summary = c.summary(classified)

subprocess.run(['damo','stop'], capture_output=True, timeout=10)
print(int(summary['hot']['bytes'] + summary['warm']['bytes']))
")

# Set memory.min to hot+warm + 1 GB buffer
MIN_MB=$(( HOT_BYTES / 1024 / 1024 + 1024 ))
echo "${MIN_MB}M" > /sys/fs/cgroup/soulmask.slice/memory.min
```

---

## 8. Swappiness and Kernel VM Tuning

### 8.1 swappiness deep dive

`vm.swappiness` (0–200, default 60) controls how aggressively the kernel swaps anonymous pages (process heap/stack) vs. reclaiming file-backed pages (executables, libraries, file cache).

| Value | Behavior |
|-------|----------|
| 0 | Only swap to avoid OOM. Heavy preference for file cache. |
| 60 | Default. Balanced. |
| 100 | Equal preference for swapping anon vs reclaiming file pages. |
| 200 | Aggressive swapping. Useful when you have fast swap (zswap) and want to free RAM for file cache. |

**For this system: `vm.swappiness = 80–100`.**  
With zswap providing fast compressed swap, swapping is *relatively cheap*. A higher swappiness means:
- Soulmask's cold heap pages compress into zswap (fast to fault back)
- File cache stays in RAM (Docker images, libraries — frequently reused)
- Under memory pressure, reclaim balances both sources

Counter-argument: if Soulmask's working set is truly 12+ GB hot, swappiness barely matters — the kernel won't swap hot pages regardless of the setting. The parameter only affects the *relative priority* of cold anon vs cold file pages.

### 8.2 When to use swappiness=200

Only if you confirm via DAMON that a significant portion of Soulmask's memory is "warm" (5–50% access rate) — pages it needs occasionally but not constantly. Higher swappiness pushes these to zswap, freeing physical RAM for the truly hot pages and the dev containers.

---

## 9. Kernel Upgrade: 6.12.90 → 7.0.10

### What 7.0 adds that matters here

| Feature | In 6.12? | Value |
|---------|----------|-------|
| DAMON | ✓ | Same core — 7.0 has intervals auto-tuning, page-level monitoring |
| zswap shrinker | ✓ (6.7+) | Same |
| zswap per-cgroup writeback | ✓ (6.8+) | Same — you can already use `memory.zswap.writeback` |
| DAMON intervals auto-tuning | ✗ (7.0+) | DAMON finds optimal sample/aggr intervals automatically. Currently you must tune manually (see §3.2 of DAMON-GUIDE.md) |
| DAMOS per-memcg quota goals | ✗ (7.0+) | Auto-tune DAMOS reclaim per cgroup — e.g., auto-adjust how aggressively Soulmask's cold pages are reclaimed |
| DAMON page-level monitoring | ✗ (7.0+) | Finer access tracking at page granularity instead of region granularity |

**Verdict: Upgrade if convenient, not urgent.** Debian 13 with backports kernel 7.0.10 is available and stable. The DAMON auto-tuning alone is worth it — it eliminates the manual trial-and-error of finding the right sampling/aggregation intervals (which we saw is fiddly). But everything else in this guide works identically on 6.12.

---

## 10. Setup Checklist

```bash
# === ONCE, as root ===

# 1. Create swap LV
lvcreate -L 20G -n swap_lv vg0
mkswap /dev/vg0/swap_lv
echo '/dev/vg0/swap_lv  none  swap  sw  0  0' >> /etc/fstab
swapon /dev/vg0/swap_lv

# 2. Configure zswap
echo zstd > /sys/module/zswap/parameters/compressor
echo 25   > /sys/module/zswap/parameters/max_pool_percent
echo Y    > /sys/module/zswap/parameters/shrinker_enabled
echo 80   > /sys/module/zswap/parameters/accept_threshold_percent

# 3. Install sysctl config
cat > /etc/sysctl.d/99-memory.conf <<'EOF'
vm.swappiness = 80
vm.vfs_cache_pressure = 50
EOF
sysctl -p /etc/sysctl.d/99-memory.conf

# 4. Install cgroup setup script
cp setup-cgroups.sh /usr/local/sbin/
chmod +x /usr/local/sbin/setup-cgroups.sh
/usr/local/sbin/setup-cgroups.sh  # run once now

# 5. Install and enable systemd service
cat > /etc/systemd/system/soulmask-cgroup.service <<'EOF'
[Unit]
Description=Soulmask cgroup memory protection
Before=wings.service
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/setup-cgroups.sh

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable soulmask-cgroup.service

# 6. Update GRUB for persistence
sed -i 's/^GRUB_CMDLINE_LINUX="/GRUB_CMDLINE_LINUX="zswap.compressor=zstd zswap.max_pool_percent=25 /' \
    /etc/default/grub
update-grub

# 7. Install Docker cgroup parent config
mkdir -p /etc/docker
jq '. + {"cgroup-parent": "soulmask.slice"}' /etc/docker/daemon.json \
    > /tmp/daemon.json && mv /tmp/daemon.json /etc/docker/daemon.json
systemctl restart docker

# 8. Verify
echo "=== Verification ==="
cat /sys/module/zswap/parameters/compressor
cat /sys/module/zswap/parameters/max_pool_percent
cat /sys/fs/cgroup/soulmask.slice/memory.low
cat /sys/fs/cgroup/dev.slice/memory.high
swapon --show
```

---

## Appendix A: Monitoring zswap Effectiveness

```bash
# zswap pool usage over time
watch -n 5 'cat /sys/kernel/debug/zswap/pool_total_size && \
            cat /sys/kernel/debug/zswap/stored_pages && \
            cat /sys/kernel/debug/zswap/written_back_pages'

# Per-cgroup memory pressure
cat /sys/fs/cgroup/soulmask.slice/memory.pressure
cat /sys/fs/cgroup/dev.slice/memory.pressure

# Swap usage breakdown
cat /proc/swaps
free -h

# Which cgroup a process is in
cat /proc/$(pidof soulmask)/cgroup
```

## Appendix B: References

- Chris Down, *"Debunking zswap and zram myths"*: https://chrisdown.name/2026/03/24/zswap-vs-zram-when-to-use-what.html
- Kernel docs — zswap: https://docs.kernel.org/7.0/admin-guide/mm/zswap.html
- Kernel docs — zram: https://docs.kernel.org/7.0/admin-guide/blockdev/zram.html
- Kernel docs — cgroup v2: https://docs.kernel.org/7.0/admin-guide/cgroup-v2.html
- DAMON Guide: `/root/work/damon-project/DAMON-GUIDE.md`
- DAMON Scripts: `/root/work/damon-project/SCRIPTS.md`








--- 







# original prompt reply (main article from chris down)


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

