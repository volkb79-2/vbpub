# Swap Architecture Guide — Gaming Host (Soulmask + Dev)

**System**: Debian 12, kernel 6.12.90, 8 cores, 16 GB RAM, root on LVM (vda5),
~60 GB free at end of disk.  
**Workload**: Soulmask game server (~13–14 GB, 80 % on 1 core, mostly cold) +
parallel dev work (VS Code SSH, up to 20 containers, heavy Docker builds,
frequent container start/teardown).  
**Scarce resource**: RAM.

----------------------------------------------------------------------------
## 1. ZSWAP vs ZRAM — the architecture choice

### 1.1 How each one works

```
               ZRAM                                       ZSWAP
 ┌──────────────┬──────────────┐             ┌──────────────┬──────────────┐
 │  Active RAM  │    ZRAM      │             │  Active RAM  │  ZSWAP pool  │
 │              │  (compressed)│             │              │  (compressed)│
 │  hot pages   │  ALL swapped │             │  hot pages   │  warm pages  │
 │              │  pages live  │             │              │              │
 └──────────────┴──────────────┘             └──────┬───────┴──────┬───────┘
                                                    │              │
      NO DISK BACKING                               │ pool FULL    │ LRU evict
      Pages NEVER evict                             ↓              ↓
      Cold pages waste RAM                   ┌─────────────────────────────┐
      forever                                  │     Disk swap (4 files)   │
                                               │  coldest compressed pages  │
                                               └─────────────────────────────┘
```

| Property | ZRAM | ZSWAP |
|---|---|---|
| **Where swapped pages live** | In a compressed RAM block device | In a compressed RAM cache, with disk fallback |
| **Cold page eviction** | Never (pages stick until freed by app) | Automatic — LRU evicts coldest pages to disk |
| **Compression stages** | 1 (RAM only) | 1 (RAM, same compressed page written to disk if pool full) |
| **Swap-in from RAM** | ~10–50 µs (decompress) | ~10–50 µs (decompress from pool) |
| **Swap-in from disk** | N/A (no disk backing) | ~0.5–5 ms (read compressed page from SSD, decompress) |
| **CPU overhead** | Lower (no writeback logic) | Slightly higher (LRU tracking + writeback) |
| **Best for** | Workloads that **fit entirely** in compressed RAM | Workloads with **hot/cold separation** |
| **Danger** | OOM if compressed pool fills (no overflow) | None — pool fills → writes to disk |
| **Shrinker (6.8+)** | N/A | Proactively writes coldest pages to disk, prevents pool exhaustion |
| **Multi-Gen LRU friendly** | No (pages are "anonymous" inside ZRAM) | Yes — MGLRU can distinguish hot vs cold before pages enter ZSWAP |

### 1.2 Why ZSWAP wins for THIS host

**The DAMO analysis showed lots of cold RAM.**  Soulmask allocates 13–14 GB but only
actively touches a fraction (the hot working set, maybe 3–5 GB of game-state
logic).  The rest — assets loaded at startup, cached geometry, initialised but
rarely-touched data structures — is cold.

- **With ZRAM**: Soulmask's cold pages are compressed into the ZRAM block
  device.  They *never leave* RAM.  When a dev container starts and asks for 4 GB,
  the kernel must either reclaim the ZRAM pages (which means **decompressing,
  recompressing, writing to disk** — triple work) or, worse, OOM because ZRAM
  has no overflow path.

- **With ZSWAP**: The cold pages are compressed once into the pool.  When memory
  pressure rises (dev container requesting memory), the **ZSWAP shrinker**
  (6.8+) writes the **coldest** compressed pages directly to the disk swap
  files *while they're still compressed*.  No re-compression.  The freed pool
  space is used for the new container's warm pages.  Soulmask's hot pages stay
  in real RAM or the ZSWAP pool.

**The single-compression path is the killer feature.**  With ZRAM+disk
combinations (architectures 2, 6, 7 in the vbpub toolkit) every page that
overflows must be **decompressed from ZRAM and recompressed on disk** — double
the CPU cost and double the latency.  ZSWAP compresses once, and the same
compressed bytes travel to disk if evicted.

### 1.3 Common counter-arguments — addressed

> "But ZRAM is faster because there's no disk I/O!"

True **only when the pool never fills**.  On a 16 GB host where Soulmask alone
occupies 14 GB, the pool WILL fill after a few hundred MB of cold pages.
Every cold page beyond that triggers the "now we have to do something" path.
With ZRAM, that path is "OOM, or decompress→recompress→disk" (slow).  With
ZSWAP, that path is "write already-compressed page to disk" (fast).

> "But disk swap is slow!"

The disk swap tier is for **cold** pages — pages the game server hasn't
touched in minutes or hours.  If those pages are *never* touched again, the
disk write is a one-time cost.  If they *are* touched occasionally, a 1–2 ms
SSD read is far better than killing a container because RAM ran out.

> "The vbpub toolkit says ZSWAP is ALWAYS recommended."

Their recommendation is **directionally correct for your use case** but too
absolute in general.  For a 2 GB VPS running a single static workload, pure
ZRAM is perfectly fine.  For YOUR host (13–14 GB consumed by one process with
lots of cold pages, dev work sharing the same machine), ZSWAP+disk is
unambiguously the right answer.

----------------------------------------------------------------------------
## 2. Recommended swap architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Physical RAM (16 GB)                    │
│  ┌──────────────────┐  ┌────────────────────────────────┐  │
│  │  Active RAM      │  │  ZSWAP compressed pool         │  │
│  │  Soulmask hot    │  │  pool: 35 % RAM = 5.6 GB       │  │
│  │  Dev tools hot   │  │  effective: ~14–17 GB (zstd)   │  │
│  │  Kernel caches   │  │  hold warm pages of both       │  │
│  │                  │  │  Soulmask + dev containers      │  │
│  └──────────────────┘  └────────────┬───────────────────┘  │
└─────────────────────────────────────┼───────────────────────┘
                                      │ pool FULL → LRU evict
                                      ↓ (pages stay compressed)
┌─────────────────────────────────────────────────────────────┐
│              Disk swap — 4 files × 4 GB = 16 GB             │
│  /var/swap/swap0  (priority 10)                            │
│  /var/swap/swap1  (priority 10)   ← round-robin striping   │
│  /var/swap/swap2  (priority 10)   between equal-priority   │
│  /var/swap/swap3  (priority 10)   devices                  │
└─────────────────────────────────────────────────────────────┘

Total virtual address space: ~49 GB
  = 16 GB RAM + 17 GB ZSWAP effective + 16 GB disk overflow
```

### 2.1 Sizing rationale

| Parameter | Value | Why |
|---|---|---|
| **ZSWAP pool** | 35 % of RAM = 5.6 GB | Higher than the toolkit's default 20 % because Soulmask's 14 GB includes substantial cold data. 5.6 GB with 3× compression holds ~17 GB — absorbing the warm fraction of both workloads. |
| **Disk swap total** | 16 GB (1× RAM) | Not the toolkit's 2× RAM (32 GB). 16 GB is sufficient as an *overflow safety net*. The ZSWAP pool handles the warm working set; disk only catches truly cold pages. 32 GB would eat half the remaining 60 GB free space — wasteful. |
| **Swap files** | 4 (not 8) | On a single SSD-backed VM, 4 files give ~90 % of the I/O striping benefit. 8 files adds management overhead with negligible throughput gain. |
| **Per-file size** | 4 GB | Clean division, large enough to avoid fragmentation. |
| **Swap type** | Files in `/var/swap/` | Your root is on LVM (vda5). Creating additional partitions requires LVM resizing or disk repartitioning — complex and risky on a running system. Swap files are simpler and the performance difference vs. a swap LV on the same SSD is negligible (< 5 %). |

### 2.2 Compressor: zstd (not lz4)

| Compressor | Compression ratio | Compress speed | Decompress speed |
|---|---|---|---|
| lz4 | 2.0–2.5× | ~4 GB/s per core | ~8 GB/s per core |
| zstd (level 1) | 2.5–3.5× | ~2 GB/s per core | ~5 GB/s per core |

**You have 7 idle cores.**  The extra CPU cost of zstd over lz4 is absorbed
effortlessly.  The extra compression ratio means ~3–7 GB more *effective*
capacity from the same 5.6 GB pool — RAM that's actually available for dev
containers.  This trade-off is worth it.

zstd decompression latency is ~50–100 µs per 4 KB page — still orders of
magnitude below an SSD read (~500–2000 µs).  The "slowdown" from zstd over
lz4 is imperceptible compared to hitting the disk.

----------------------------------------------------------------------------
## 3. Step-by-step implementation

### 3.1 Kernel command line (reapplied on every boot)

Edit `/etc/default/grub`:

```bash
GRUB_CMDLINE_LINUX="... zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent=35 zswap.shrinker_enabled=1"
```

Then:

```bash
update-grub
```

**Critical**: `zswap.shrinker_enabled=1` is OFF by default even in kernel
6.12.  Without it, the ZSWAP pool fills and then *rejects* new swap-out
requests — triggering direct disk swap (bypassing ZSWAP entirely) or, worse,
OOM kills.  The shrinker writes the coldest compressed pages to disk
proactively, keeping the pool available for new warm pages.

Verify after reboot:

```bash
cat /sys/module/zswap/parameters/enabled           # → Y
cat /sys/module/zswap/parameters/compressor         # → zstd
cat /sys/module/zswap/parameters/max_pool_percent   # → 35
cat /sys/module/zswap/parameters/shrinker_enabled   # → Y
```

### 3.2 Create swap files

```bash
SWAP_DIR="/var/swap"
SWAP_COUNT=4
SWAP_SIZE_MB=4096   # 4 GB each → 16 GB total

mkdir -p "$SWAP_DIR"
chmod 700 "$SWAP_DIR"

for i in $(seq 0 $((SWAP_COUNT - 1))); do
    FILE="${SWAP_DIR}/swap${i}"
    if [ ! -f "$FILE" ]; then
        dd if=/dev/zero of="$FILE" bs=1M count="$SWAP_SIZE_MB" status=progress
        chmod 600 "$FILE"
        mkswap "$FILE"
    fi
done

# Activate all with equal priority for round-robin striping
swapon -p 10 "${SWAP_DIR}"/swap[0-3]
```

### 3.3 Make swap files persistent

Add to `/etc/fstab`:

```
# ZSWAP disk backing — 4 × 4 GB swap files, equal priority for I/O striping
/var/swap/swap0  none  swap  sw,pri=10  0  0
/var/swap/swap1  none  swap  sw,pri=10  0  0
/var/swap/swap2  none  swap  sw,pri=10  0  0
/var/swap/swap3  none  swap  sw,pri=10  0  0
```

The kernel round-robins allocation across all devices with the same priority.
This is transparent to ZSWAP — it happens at the swap subsystem layer.

### 3.4 Sysctl tuning

Create `/etc/sysctl.d/99-swap.conf`:

```ini
# ── Multi-Gen LRU (already default in 6.12, explicit for safety) ─
vm.lru_gen_enabled = 0x7

# ── Swappiness ──────────────────────────────────────────────────
# Lower than default 60 — prefer to use the ZSWAP pool before
# hitting disk, but don't be afraid to swap cold pages out.
vm.swappiness = 30

# ── Page cluster for SSD ────────────────────────────────────────
# Default 4 = 64 KB I/O — optimized for HDD.
# SSD: 2 = 4 pages = 16 KB per I/O → lower latency.
vm.page-cluster = 2

# ── Memory watermarks ───────────────────────────────────────────
# Default 10.  Higher = kswapd wakes up earlier, starts reclaim
# before pressure becomes acute.  Smoother under load.
vm.watermark_scale_factor = 50

# ── Cache pressure ──────────────────────────────────────────────
# Keep default.  Docker builds benefit from cached layers.
vm.vfs_cache_pressure = 100

# ── OOM admin reserve ───────────────────────────────────────────
# Guarantee root can SSH in and kill processes even under OOM.
vm.admin_reserve_kbytes = 65536
vm.user_reserve_kbytes = 32768

# ── Overcommit ──────────────────────────────────────────────────
# Heuristic overcommit (default 0) is fine with our swap setup.
# Don't switch to 2 (never) without extensive testing;
# it can reject valid memory requests.
vm.overcommit_memory = 0
```

Apply immediately:

```bash
sysctl --system
```

### 3.5 Verify Multi-Gen LRU is active

```bash
cat /sys/kernel/mm/lru_gen/enabled   # → 0x0007
```

If it returns `0x0000`, it's compiled in but not running — check `dmesg | grep lru_gen` for why (usually not enough nodes or memory).  On a 16 GB single-node VM it should work by default.

### 3.6 Network — low-latency qdisc

Create `/etc/sysctl.d/99-network.conf`:

```ini
net.core.default_qdisc = fq_codel
```

Apply:

```bash
sysctl -p /etc/sysctl.d/99-network.conf
# Replace existing qdisc on active interface (non-persistent, but
# default_qdisc handles new connections after sysctl)
tc qdisc replace dev eth0 root fq_codel 2>/dev/null || true
```

`fq_codel` is ideal for mixed workloads: it gives each flow (game client
connection) a fair share of the bottleneck, keeps bufferbloat low, and
doesn't require per-service configuration.

----------------------------------------------------------------------------
## 4. Process & container prioritization

### 4.1 The three tiers

```
Tier 1 — SOULMASK (game server)
  CPU weight:   500  (default = 100)
  I/O weight:   500
  Memory:       min 4 GB, low 8 GB, max 16 GB
  Behavior:     Always preempts Tier 2 and 3

Tier 2 — SYSTEM SERVICES (apt, certbot, SSHD, systemd)
  CPU weight:   100  (default)
  I/O weight:   100
  Behavior:     Normal priority; short-lived, small footprint

Tier 3 — DEV WORKLOADS (containers, builds, VS Code)
  CPU weight:   50
  I/O weight:   50
  Memory:       max 6 GB, high 5 GB, low 1 GB
  Behavior:     Preempted by Tier 1; never starves Soulmask
```

### 4.2 Implementing in Docker

**Soulmask container (via Pterodactyl egg configuration)**:

```json
{
  "limits": {
    "memory": 16384,
    "memory_reservation": 8192,
    "cpu_shares": 2048,
    "blkio_weight": 1000
  }
}
```

These translate to Docker flags:

```bash
docker run \
  --memory=16g \
  --memory-reservation=8g \
  --cpu-shares=2048 \
  --blkio-weight=1000 \
  ...
```

| Flag | What it does |
|---|---|
| `--memory=16g` | Hard cap — container cannot exceed this (matches physical RAM, prevents runaway) |
| `--memory-reservation=8g` | Soft guarantee — kernel avoids reclaiming below this unless system is desperate |
| `--cpu-shares=2048` | 2048 / 1024 (default) = 2× weight → Soulmask gets 2× CPU time vs. normal containers |
| `--blkio-weight=1000` | 1000 / 500 (default) = 2× I/O bandwidth priority |

**Dev containers (via docker-compose or CLI)**:

```bash
docker run \
  --cgroup-parent=/dev-workloads.slice \
  --memory=6g \
  --memory-reservation=2g \
  --cpu-shares=256 \
  --blkio-weight=100 \
  --label=workload=dev \
  --label=priority=low \
  ...
```

Or in `docker-compose.yml`:

```yaml
services:
  dev-service:
    cgroup_parent: /dev-workloads.slice
    mem_limit: 6g
    mem_reservation: 2g
    cpu_shares: 256
    blkio_config:
      weight: 100
    labels:
      workload: dev
      priority: low
```

### 4.3 Dev workload cgroup slice (persistent)

Create `/etc/systemd/system/dev-workloads.slice`:

```ini
[Unit]
Description=Slice for development workloads
Before=slices.target
DefaultDependencies=no

[Slice]
MemoryMax=6G
MemoryHigh=5G
MemoryLow=1G
CPUWeight=50
IOWeight=50

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl start dev-workloads.slice
```

### 4.4 Docker daemon constraints for builds

Edit `/etc/docker/daemon.json`:

```json
{
  "storage-driver": "overlay2",
  "log-driver": "local",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "live-restore": true,
  "features": {
    "buildkit": true
  },
  "default-ulimits": {
    "nofile": { "Name": "nofile", "Hard": 64000, "Soft": 64000 }
  }
}
```

For build-time limits, set environment variables when building:

```bash
# Limit per-build-step memory
export BUILDKIT_STEP_MEM_MAX=4g

# Build with explicit resource constraints
docker build \
  --memory=6g \
  --memory-swap=12g \
  --cpu-shares=256 \
  -t my-image .
```

### 4.5 Disk space guard for Docker builds

Docker builds create massive temporary writes.  Control growth:

```bash
cat > /etc/cron.hourly/docker-prune << 'CRON'
#!/bin/bash
set -euo pipefail

# Prune old build cache
docker builder prune --force --filter until=48h 2>/dev/null || true

# If disk usage exceeds 75 %, prune aggressively
USAGE=$(df /var/lib/docker | awk 'NR==2 {print $5}' | tr -d '%')
if [ "${USAGE:-0}" -gt 75 ]; then
    docker system prune --force --filter until=6h 2>/dev/null || true
fi
CRON

chmod +x /etc/cron.hourly/docker-prune
```

----------------------------------------------------------------------------
## 5. Boot-time persistence

All the above must re-apply automatically after reboot.

Create `/etc/systemd/system/swap-tune.service`:

```ini
[Unit]
Description=Apply swap and memory tuning on boot
After=local-fs.target swap.target
Before=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/apply-swap-tune.sh

[Install]
WantedBy=multi-user.target
```

Create `/usr/local/sbin/apply-swap-tune.sh`:

```bash
#!/bin/bash
set -euo pipefail

# ── Apply sysctl ──────────────────────────────────────
sysctl --system

# ── Ensure ZSWAP shrinker is on ───────────────────────
# (kernel cmdline should handle this, but double-check)
if [ -f /sys/module/zswap/parameters/shrinker_enabled ]; then
    echo Y > /sys/module/zswap/parameters/shrinker_enabled || true
fi

# ── Check swap files exist and are active ─────────────
SWAP_DIR="/var/swap"
SWAP_COUNT=4
SWAP_SIZE_MB=4096

for i in $(seq 0 $((SWAP_COUNT - 1))); do
    FILE="${SWAP_DIR}/swap${i}"
    if [ ! -f "$FILE" ]; then
        dd if=/dev/zero of="$FILE" bs=1M count="$SWAP_SIZE_MB" status=none
        chmod 600 "$FILE"
        mkswap "$FILE"
    fi
done

# Activate all swap files if not already active
swapon -a

# ── Apply fq_codel to active interfaces ──────────────
for iface in $(ls /sys/class/net/ | grep -v lo); do
    tc qdisc replace dev "$iface" root fq_codel 2>/dev/null || true
done

# ── Signal success ────────────────────────────────────
echo "[swap-tune] System swap configuration applied."
```

```bash
chmod +x /usr/local/sbin/apply-swap-tune.sh
systemctl daemon-reload
systemctl enable swap-tune.service
```

----------------------------------------------------------------------------
## 6. Monitoring — the right metrics

**DO NOT use `vmstat si`**.  It counts both fast RAM-compressed hits (ZSWAP pool
decompression) and slow disk reads — mixing them into one misleading number.

### 6.1 What to monitor

| Metric | Command | Good | Warning | Critical |
|---|---|---|---|---|
| **Major page faults** | `grep pgmajfault /proc/vmstat` | Stable | Slowly rising | Spiking |
| **ZSWAP writeback ratio** | See script below | < 1 % | 1–10 % | > 10 % |
| **PSI memory pressure** | `cat /proc/pressure/memory` | `full avg10=0.00` | `full avg10 < 5` | `full avg10 > 10` |
| **OOM kills** | `dmesg \| grep -i "killed process"` | 0 | 0 | Any |
| **Disk swap usage** | `swapon --show` | < 50 % | 50–80 % | > 80 % |
| **ZSWAP pool usage** | See script below | < 80 % | 80–95 % | > 95 % |

### 6.2 Quick health check script

```bash
#!/bin/bash
# save as /usr/local/bin/swap-health

echo "=== ZSWAP Pool ==="
POOL=$(cat /sys/kernel/debug/zswap/pool_pages 2>/dev/null || echo "N/A")
STORED=$(cat /sys/kernel/debug/zswap/stored_pages 2>/dev/null || echo "N/A")
WB=$(cat /sys/kernel/debug/zswap/written_back_pages 2>/dev/null || echo "N/A")
echo "  pool_pages:       $POOL"
echo "  stored_pages:     $STORED"
echo "  written_back:     $WB"
if [ "$POOL" != "N/A" ] && [ "$POOL" -gt 0 ]; then
    RATIO=$(echo "scale=2; 100 * $WB / $POOL" | bc)
    echo "  writeback ratio:  ${RATIO}%"
    if (( $(echo "$RATIO > 10" | bc -l) )); then
        echo "  ⚠️  HIGH — consider increasing ZSWAP pool or adding RAM"
    elif (( $(echo "$RATIO > 1" | bc -l) )); then
        echo "  ⚡ Moderate — acceptable"
    else
        echo "  ✅ Excellent"
    fi
fi

echo ""
echo "=== Major Page Faults ==="
PGF=$(grep pgmajfault /proc/vmstat | awk '{print $2}')
echo "  pgmajfault:  $PGF"

echo ""
echo "=== PSI Memory Pressure ==="
cat /proc/pressure/memory

echo ""
echo "=== Swap Usage ==="
swapon --show 2>/dev/null || echo "  No swap devices"
```

Run with:

```bash
watch -n 10 swap-health
```

### 6.3 Long-term trend (add to cron)

```bash
cat > /etc/cron.d/zswap-stats << 'CRON'
*/15 * * * * root echo "$(date -Iseconds) $(grep pgmajfault /proc/vmstat) $(cat /sys/kernel/debug/zswap/pool_pages 2>/dev/null) $(cat /sys/kernel/debug/zswap/written_back_pages 2>/dev/null)" >> /var/log/zswap-stats.log
CRON
```

Install with:

```bash
mkdir -p /var/log
touch /var/log/zswap-stats.log
chmod 644 /var/log/zswap-stats.log
```

Rotate via `/etc/logrotate.d/zswap-stats`:

```
/var/log/zswap-stats.log {
    monthly
    rotate 6
    missingok
    notifempty
    compress
    copytruncate
}
```

----------------------------------------------------------------------------
## 7. Review of the vbpub toolkit

The toolkit at `https://github.com/volkb79-2/vbpub` provides a well-structured
swap configuration framework, but several findings need adjustment for your
specific host:

### 7.1 What the toolkit gets right

| Finding | Verdict | Notes |
|---|---|---|
| ZSWAP > ZRAM for general use | ✅ Correct for your case | Their "Architecture 3" (ZSWAP + multiple swap files) is the right *category* of solution |
| Multi-device swap striping | ✅ Correct | Round-robin across equal-priority devices works at the kernel swap layer |
| pgmajfault over vmstat si | ✅ Correct | They explicitly call this out in SWAP_ARCHITECTURE.md |
| ZSWAP shrinker awareness | ✅ Correct | They document the kernel 6.8+ shrinker |
| PARTUUID for fstab | ✅ Correct | Stable across mkswap calls, unlike filesystem UUID |

### 7.2 What needs adjustment for your host

| Toolkit default | Toolkit reasoning | Adjusted for your host | Why |
|---|---|---|---|
| **20 % ZSWAP pool** | "Good for general use" | **35 %** | 14 GB consumed by Soulmask needs room for warm fraction in pool |
| **lz4 compressor** | "Best speed/ratio balance" | **zstd** | RAM is the bottleneck, not CPU. 7 idle cores absorb zstd overhead. Extra 30–40 % compression wins. |
| **8 swap files** | "8 parallel I/O streams" | **4** | Single SSD VM — 4 files provide > 90 % of the benefit. 8 adds complexity with negligible gain. |
| **2× RAM swap size** | "Consistent across all RAM sizes" | **1× RAM (16 GB)** | Toolkit default would be 32 GB — half your free disk. 16 GB is sufficient overflow. |
| **ZRAM is "NOT RECOMMENDED"** | Too absolute | **Agree for THIS host, but nuanced** | ZRAM is fine for VPS with no disk, or workloads fully fitting in compressed RAM. Your 14 GB Soulmask footprint makes ZRAM alone dangerous. |

### 7.3 What the toolkit misses entirely

- **Multi-Gen LRU** — Never mentioned, but critical for quality reclaim decisions (kernel 6.1+)
- **Cgroup v2 for container prioritization** — Zero coverage; only swap topology is addressed
- **Docker BuildKit I/O constraints** — Builds generate enormous write amplification
- **Network qdisc tuning** — fq_codel for game server + dev coexistence
- **`vm.page-cluster` for SSD** — Default 4 (64 KB) is wasteful on SSD-backed VMs
- **Persistence strategy** — No guidance on how settings survive reboots and container restarts

### 7.4 Verdict

The toolkit is a *good starting point* and its core insight (ZSWAP + multi-device
swap) is correct for your host.  Use its scripts for **initial setup** (they
handle partition detection, file creation, fstab entries cleanly), but then
**override the tuning** with the values in this guide.  Specifically:

- If you use `setup-swap.sh`, run it with:
  ```bash
  SWAP_ARCH=3 \
  ZSWAP_COMPRESSOR=zstd \
  ZSWAP_POOL_PERCENT=35 \
  SWAP_DISK_TOTAL_GB=16 \
  SWAP_STRIPE_WIDTH=4 \
  ./setup-swap.sh
  ```
  Then override the sysctl values manually (the script may not set `vm.page-cluster`
  or `vm.watermark_scale_factor`).

- If you DON'T use the toolkit, Section 3 of this guide is fully self-contained
  and does not depend on any external scripts.

----------------------------------------------------------------------------
## 8. Kernel upgrade — debian 13 / kernel 7.0

### 8.1 What's real vs not

| Assertion | Reality |
|---|---|
| "Debian 13 Trixie" | **Not released**. In testing. Will ship with **kernel 6.12 LTS**. |
| "Kernel 7.0.10" | **Does not exist**. Kernel 7.0 was released April 2026; it is **not an LTS release**. |
| "Kernel 7 features" | The notable swap feature is "Phase II swap performance" — a lock-contention reduction for the swap address space. Benefits large NUMA systems with massive parallel swap I/O. Marginal on an 8-core VM. |
| "Kernel 6.18 LTS" | Expected late 2025. Includes "better swapping performance." This IS worth watching. |

### 8.2 Recommendation

**Stay on kernel 6.12 LTS.**  You already have:

- ZSWAP with shrinker (6.8+)
- Multi-Gen LRU (6.1+)
- Multi-size THP for anonymous memory (6.8+)
- PREEMPT_RT for x86_64 (6.12)
- All cgroup v2 controllers you need

There is **no feature in kernel 7.0** that changes the fundamental swap
architecture or materially improves performance on a system of your scale.

If you want to upgrade later, wait for **kernel 6.18 LTS** (expected in
bookworm-backports early 2026), which includes documented "better swapping
performance."  Skip 7.0 entirely — it's a non-LTS release.

----------------------------------------------------------------------------
## 9. Summary — configuration checklist

```
[ ] Kernel cmdline: zswap.enabled=1 zswap.compressor=zstd
                     zswap.max_pool_percent=35 zswap.shrinker_enabled=1
[ ] update-grub && reboot
[ ] Verify: cat /sys/module/zswap/parameters/* → all values correct
[ ] Create /var/swap/ with 4 × 4 GB files
[ ] /etc/fstab entries with equal pri=10
[ ] /etc/sysctl.d/99-swap.conf (vm.swappiness=30, vm.page-cluster=2,
      vm.watermark_scale_factor=50, vm.lru_gen_enabled=0x7,
      vm.admin_reserve_kbytes=65536)
[ ] /etc/sysctl.d/99-network.conf (net.core.default_qdisc=fq_codel)
[ ] sysctl --system
[ ] /etc/docker/daemon.json (buildkit enabled, log limits)
[ ] systemctl daemon-reload && systemctl restart docker
[ ] /etc/systemd/system/dev-workloads.slice (memory max/high/low, low cpu/io weights)
[ ] systemctl start dev-workloads.slice
[ ] Pterodactyl egg: Soulmask limits (memory 16g, memory-reservation 8g,
      cpu-shares 2048, blkio-weight 1000)
[ ] Dev container aliases: --cgroup-parent=/dev-workloads.slice --cpu-shares=256
[ ] /usr/local/sbin/apply-swap-tune.sh (boot-time reapplicator)
[ ] /etc/systemd/system/swap-tune.service (oneshot, enabled)
[ ] /etc/cron.hourly/docker-prune (disk guard)
[ ] /usr/local/bin/swap-health (monitoring script)
[ ] watch -n 10 swap-health (verify no red flags during normal operation)
```

----------------------------------------------------------------------------
## 10. References

- [vbpub toolkit](https://github.com/volkb79-2/vbpub/tree/main/scripts/debian-install) — swap configuration scripts, benchmarked Feb 2025
- [Kernel zswap documentation](https://docs.kernel.org/admin-guide/mm/zswap.html) — official 7.1-era docs (same semantics as 6.12)
- [Kernel cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html) — memory, io, cpu controller details
- [Kernel sysctl vm documentation](https://docs.kernel.org/admin-guide/sysctl/vm.html) — page-cluster, swappiness, watermark parameters
- [Linux kernel version history](https://en.wikipedia.org/wiki/Linux_kernel_version_history) — release dates and feature lists per version






---





# original reply in UI

this answer is based on references to the `https://github.com/volkb79-2/vbpub/tree/main/scripts/debian-install` findings.

Alright — I have all the information I need. This is a substantial multi-part question. Here's the comprehensive analysis.

---

## 1. Review of the vbpub Toolkit's Swap Architecture Claims

I reviewed the full repository — the `SWAP_ARCHITECTURE.md`, `setup-swap.sh`, and `FINAL_SUMMARY.md` documents. Here's what they get right, what they get wrong, and what's nuanced for your specific case.

### What the toolkit gets **right**

| Claim | Assessment |
|-------|-----------|
| **ZSWAP > ZRAM for mixed workloads** | Correct for your use case. The key insight — ZRAM pages never automatically evict to disk; ZSWAP's LRU-based writeback evicts cold compressed pages to disk when the pool is full — is **exactly** what you need (DAMO showed lots of cold RAM) |
| **Multi-device swap striping works** | Correct. The kernel does round-robin across equal-priority swap devices at the `get_swap_page` level. |
| **`pgmajfault` is the real metric** | Correct — `vmstat si` counts RAM-decompressed hits too, which are fast; only `pgmajfault` measures actual disk reads. |
| **ZSWAP shrinker (6.8+)** | Correct — this prevents OOM by proactively writing cold pool pages to disk. Kernel 6.12 has it. |
| **Partition swap > file swap** | True in isolation (no filesystem overhead), but the difference on an SSD-backed VM is small. |
| **sfdisk `--force --no-reread` workflow** | Correct approach for repartitioning in-use disks. |

### What the toolkit gets **wrong or overstated**

| Claim | Issue |
|-------|-------|
| **"ZSWAP is ALWAYS recommended over ZRAM"** | Too absolute. ZRAM is *better* when: (a) there's no disk swap (low-end VPS), (b) total working set fits in compressed RAM, (c) you need maximum latency predictability. The toolkit's own SWAP_ARCH=7 (ZRAM + uncompressed partition) actually has merit in some cases. |
| **"8 swap files → 8 parallel I/O streams"** | Technically true at the kernel swap layer but **misleading for a single SSD** — you're still bottlenecked on one device's queue depth. NVMe can handle ~64K IOPS natively without striping across files. 4 files gives you most of the benefit; 8 is overkill. |
| **Swap sizing: 2x RAM formula** | Defaults to 32GB for 16GB RAM. With only ~60GB free disk, 32GB for swap is excessive — it eats half the free space. For a game server + dev system, 16GB (1x RAM) is more appropriate. |
| **ZSWAP pool: 20% of RAM = 3.2GB** | Too low when 80% of RAM (13-14GB) is consumed by one process with lots of cold pages. 3.2GB pool with 2.5x compression holds ~8GB of cold data — but Soulmask alone could overflow it. For your case, 30-40% pool is better. |
| **lz4 as default compressor** | lz4 is fast but gives ~2-2.5x compression. With RAM being YOUR scarce resource, **zstd** (2.5-3.5x) is likely the better trade-off. You have 8 cores with only 1 at 80% — the other 7 have headroom. |

### What the toolkit **completely misses**

- **No cgroup integration** — zero mention of `memory.low`, `memory.min`, `cpu.weight`, `io.weight` for Docker container prioritization
- **No mention of Multi-Gen LRU** (`CONFIG_LRU_GEN`) which landed in 6.1 and significantly improves reclaim quality by maintaining multiple LRU generations per page type
- **No `vm.page-cluster` tuning** for SSD — the default 4 (64KB I/O) is optimized for HDD; SSDs benefit from `vm.page-cluster=2` or `3` (16-32KB) for lower latency
- **No zswap shrinker enablement** — the kernel docs show this is disabled by default and must be explicitly enabled
- **No Docker BuildKit I/O constraints** — builds generate enormous write amplification on the overlayfs layer

---

## 2. Recommended Swap Architecture for THIS System

Your configuration: **16GB RAM, Soulmask consumes ~13-14GB (mostly cold after startup), dev workloads need to co-exist, ~60GB free disk, SSD-backed VM.**

```
ZSWAP + 4 swap files on /var/swap/
├── ZSWAP pool:    35% of RAM  (~5.6GB → ~14-17GB effective with zstd)
├── swap file 1:    4GB         (priority 10, equal to others)
├── swap file 2:    4GB         (priority 10)
├── swap file 3:    4GB         (priority 10)
└── swap file 4:    4GB         (priority 10)
    Total disk:     16GB
```

### Why this specific configuration

**ZSWAP (not ZRAM)** — because:
- Your DAMO analysis shows lots of cold RAM → ZSWAP's LRU eviction to disk is exactly what solves this
- When dev containers start and request memory, ZSWAP automatically writes Soulmask's cold compressed pages to disk, freeing RAM
- When the dev containers are torn down, the game server's hot pages stay in RAM or the ZSWAP pool
- Single compression stage: pages stay compressed all the way to disk

**zstd compressor (not lz4)** — because:
- RAM is your bottleneck, not CPU (7 idle cores)
- zstd gives 2.5-3.5x compression vs lz4's 2-2.5x — that's ~3-7GB more effective capacity
- zstd decompression is nearly as fast as lz4 on modern CPUs
- For ZSWAP specifically, the trade-off favors ratio since swap-outs are async

**35% pool (not 20%)** — because:
- Soulmask's 13-14GB is partially cold. The ZSWAP pool needs to be sized to hold the *hot working set* of the game server plus the kernel's active data
- With 35% (= 5.6GB) pool and 3x zstd compression → ~17GB effective compressed cache
- This gives headroom to absorb the hot portion of both Soulmask and dev work simultaneously
- If the pool fills, the shrinker (which you MUST enable — see below) writes coldest pages to disk

**4 swap files (not 8)** because:
- On a single SSD-backed VM, 4 files give ~90% of the striping benefit with half the filesystem overhead
- Each file = 4GB, total 16GB swap (1x RAM)
- 16GB is sufficient: ZSWAP's compressed pool handles the warm data, the swap files only hold the truly cold overflow
- 4 files in `/var/swap/` avoids the complexity of repartitioning the LVM setup (root on LV on vda5)

**Total effective memory: ~16GB RAM + ~17GB ZSWAP (compressed) + 16GB disk = ~49GB virtual address space**

### How it plays out in practice

```
Normal state:
  RAM:     Soulmask hot pages (5GB) + Dev tools hot (2GB) + ZSWAP pool (5.6GB)
  ZSWAP:   Soulmask warm pages (8GB compressed → ~3GB pool used)
  Disk:    Cold pages (auto-managed by ZSWAP)

Dev container starts (memory pressure):
  → ZSWAP shrinker identifies coldest compressed pages
  → Writes them (still compressed!) to swap files
  → Frees ZSWAP pool space
  → Kernel reclaims RAM from ZSWAP pool pages if needed
  → Dev container gets its memory

Dev container stops (memory freed):
  → Soulmask's previously-evicted pages are paged back in if accessed
  → ZSWAP re-caches them in the pool
  → System returns to normal state
```

---

## 3. Critical Kernel Tuning — Not in the Toolkit

These must be applied via `/etc/sysctl.d/99-swap.conf`:

```ini
# ── Multi-Gen LRU ───────────────────────────
# Already default in 6.12, but explicit:
vm.lru_gen_enabled = 0x7           # enable all 3 generation types

# ── ZSWAP tuning ────────────────────────────
vm.swappiness = 30                 # lower than default 60; prefer to use ZSWAP pool
                                   # before hitting disk, but don't be afraid to swap
                                   # cold pages out either

# ── ZSWAP SHRINKER (critical!) ──────────────
# THIS IS OFF BY DEFAULT EVEN IN 6.12.
# Enable at boot via kernel parameter:
#   zswap.shrinker_enabled=1
# Or at runtime:
#   echo Y > /sys/module/zswap/parameters/shrinker_enabled

# ── Page cluster for SSD ────────────────────
vm.page-cluster = 2                # 4 pages = 16KB I/O (SSD-friendly)
                                   # Default 4 = 64KB is for HDDs

# ── Memory watermarks ──────────────────────
vm.watermark_scale_factor = 50     # More aggressive background reclaim
                                   # (default 10, range 10-1000)
                                   # Higher = sooner kswapd wakeup,
                                   # smoother pressure under load

# ── Cache pressure ──────────────────────────
vm.vfs_cache_pressure = 100        # Keep default — don't drop caches too eagerly
                                   # since Docker builds benefit from cached layers

# ── OOM safety ──────────────────────────────
vm.admin_reserve_kbytes = 65536    # 64MB reserved for root SSH login
vm.user_reserve_kbytes = 32768     # 32MB for user processes
```

**How to make the shrinker permanent**: add `zswap.shrinker_enabled=1` to `GRUB_CMDLINE_LINUX` in `/etc/default/grub`, then `update-grub`.

---

## 4. Process / Container Prioritization

### The architecture challenge

Pterodactyl Wings manages the Soulmask container via Docker. It creates the container, which gets placed in cgroup v2 under `/sys/fs/cgroup/system.slice/docker-<container-id>.scope/`. Dev containers you start via `docker compose` or `docker run` also end up in the same tree.

The key: **cgroup v2 is a tree with hierarchical resource distribution**. You can set protections at the parent level that apply to all children.

### Step-by-step priority scheme

```
/sys/fs/cgroup/
├── system.slice/
│   ├── docker.service          # Docker daemon (default)
│   ├── wings.service           # Pterodactyl Wings (default)
│   └── docker-<soulmask>.scope # Soulmask container
│       └── memory.low = 8G     # Protect 8GB of Soulmask's hot memory
│         memory.min = 4G       # 4GB hard guarantee
│           cpu.weight = 500    # High CPU weight (default is 100)
│            io.weight = 500    # High I/O weight
├── user.slice/                 # Your SSH session, dev shell
│   └── (dev tools have default weights)
├── dev-workloads.slice/        # NEW — create this for dev containers
│   └── memory.max = 6G         # Hard cap all dev containers at 6GB
│       memory.high = 5G        # Soft limit — start reclaiming at 5GB
│         memory.low = 1G       # Minimum guarantee
│           cpu.weight = 50     # Low CPU — Soulmask preempts these
│            io.weight = 50     # Low I/O priority
```

### Implementation

**Option A: Docker resource flags (simplest)**
```bash
# Start Soulmask via Wings (configure in Pterodactyl egg):
docker run --memory-reservation 8g --memory 16g --cpu-shares 2048 --blkio-weight 1000 ...

# Start dev containers with low priority:
docker run --memory 6g --memory-reservation 4g --cpu-shares 256 --blkio-weight 100 ...
```

**Option B: cgroup delegation (more precise)**
Create a persistent systemd slice for dev workloads:

```ini
# /etc/systemd/system/dev-workloads.slice
[Unit]
Description=Slice for development workloads
Before=slices.target

[Slice]
MemoryMax=6G
MemoryHigh=5G
MemoryMin=1G
CPUWeight=50
IOWeight=50
```

Then make Docker use this slice for dev containers:
```bash
docker run --cgroup-parent=/dev-workloads.slice ...
```

Or via docker compose:
```yaml
services:
  dev-service:
    cgroup_parent: /dev-workloads.slice
```

**Option C: Systemd drop-in for Wings (for persistence across Wings updates)**
```ini
# /etc/systemd/system/wings.service.d/99-soulmask-priority.conf
[Service]
# These become the default limits for containers Wings starts
Slice=system.slice
CPUWeight=500
IOWeight=500
```

### RAM that should never be swapped

The game server's **hot pages** need protection. Since DAMO showed lots of cold pages, the hot set is likely small (maybe 2-4GB of active game logic). 

```bash
# Method 1: cgroup memory.min (hard protection)
# Set this on the Soulmask container's cgroup:
echo 4G > /sys/fs/cgroup/system.slice/docker-<id>.scope/memory.min

# Method 2: mlock inside the container
# Requires modifying the game server startup to call mlockall(MCL_CURRENT|CL_FUTURE)
# Not practical for a managed game server binary.

# Method 3: memory.low (best-effort protection)
echo 8G > /sys/fs/cgroup/system.slice/docker-<id>.scope/memory.low
```

`memory.low` is the sweet spot — it tells the kernel "try to keep at least 8GB available for this container" but doesn't force it if the system genuinely needs it (which prevents OOM).

**For system services** (apt, certbot, etc.): these run in `system.slice` with default weights. The root cgroup's `memory.low` protection doesn't apply to them since they're in the same slice. But they're typically short-lived and small-footprint, so they won't materially compete with Soulmask.

---

## 5. Docker Build I/O Limits

**Problem**: Docker builds create massive write amplification. Each `RUN` layer creates a new overlayfs diff. Building a 12GB image can write 20-50GB to disk temporarily.

**Solution — multi-layered**:

```bash
# 1. Limit build memory (prevents builder from starving system)
docker build --memory=4g --memory-swap=8g ...

# 2. BuildKit I/O constraints (via environment)
BUILDKIT_STEP_MEM_MAX=4g           # Memory per build step
BUILDKIT_CACHE_MOUNT_NS=shared     # Share cache across builds

# 3. I/O bandwidth limits on the builder cgroup
# If using --cgroup-parent for builds:
echo "8:0  rbps=100M wbps=50M" > /sys/fs/cgroup/<builder-slice>/io.max
# This limits the build to 100MB/s read, 50MB/s write

# 4. Use tmpfs for build cache and temp directories
docker build --build-arg BUILDKIT_INLINE_CACHE=1 \
  --cache-from type=local,src=/tmp/buildkit-cache \
  --cache-to type=local,dest=/tmp/buildkit-cache,mode=max ...
```

**Disk space guard** for the build server:
```bash
# Set a Docker daemon-level storage limit
# /etc/docker/daemon.json
{
  "storage-driver": "overlay2",
  "storage-opts": [
    "overlay2.size=50G"   # Limit each container's layer size
  ],
  "log-driver": "local",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

**WARNING**: `overlay2.size` requires the underlying filesystem to support project quotas (xfs with `pquota` mount option, or ext4 with `project`). If you're on ext4 (default Debian), this won't work without remounting. Alternative: monitor disk usage with a cron job and prune old build cache:

```bash
# /etc/cron.hourly/docker-prune
#!/bin/bash
docker builder prune --force --filter until=24h
docker image prune --force --filter until=24h
df /var/lib/docker | awk 'NR==2 {if ($5+0 > 80) system("docker system prune --force --all")}'
```

---

## 6. Network Queuing

For Soulmask (a game server), you want **low latency, not high throughput** for its UDP traffic. Dev work (docker pulls, git clones) can tolerate higher latency.

```bash
# Replace the default pfifo_fast qdisc with fq_codel on the main interface
# (low latency, fair queuing, controlled delay)
tc qdisc replace dev eth0 root fq_codel

# To prioritize game server traffic:
# Mark Soulmask's packets with a higher priority DSCP value
iptables -t mangle -A OUTPUT -p udp --dport 7777 -j DSCP --set-dscp 46  # EF
iptables -t mangle -A OUTPUT -p udp --dport 7777 -j MARK --set-mark 10

# Then let fq_codel handle the rest fairly
```

**Persistence**: set `net.core.default_qdisc = fq_codel` in `/etc/sysctl.d/99-network.conf`.

For your use case, `fq_codel` is likely sufficient without per-port prioritization — it inherently keeps latency low by giving each flow a fair share of the bottleneck.

---

## 7. Settings Persistence Strategy

Since Pterodactyl Wings manages the Soulmask container and may recreate it on updates/restarts, you need **defense in depth**:

### Layer 1: Boot-time baseline (always applied)

Create a systemd oneshot service that fires after networking and Docker are up:

```ini
# /etc/systemd/system/swap-tune.service
[Unit]
Description=Apply swap and memory tuning
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/apply-swap-tune.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

The script `/usr/local/sbin/apply-swap-tune.sh` applies:
- All sysctl settings from `/etc/sysctl.d/99-swap.conf`
- Enables zswap shrinker if not default
- Sets up fq_codel on the interface
- Creates `/var/swap/` with swap files if missing (after filesystem is mounted)

### Layer 2: Docker-level constraints

Configure the Pterodactyl egg for Soulmask with resource limits:
- Memory limit: `--memory=16g --memory-reservation=8g`
- CPU shares: `--cpu-shares=2048` (double default)
- I/O weight: `--blkio-weight=1000`

These are applied by Wings when starting the container and survive container restarts.

### Layer 3: Post-start watcher (optional)

```ini
# /etc/systemd/system/soulmask-priority.path
[Unit]
Description=Watch for Soulmask container
PathModified=/sys/fs/cgroup
[Install]
WantedBy=multi-user.target
```

Combined with a service that applies cgroup limits when the container appears. But this is fragile — Layers 1+2 cover 99% of cases.

---

## 8. Debian 13 / Kernel 7.0 Question

### Fact check

| Claim | Reality |
|-------|---------|
| "Debian 13 Trixie" | Not yet released. Still **testing** (Debian 13 will ship with **kernel 6.12 LTS**) |
| "Kernel 7.0.10" | Does not exist. Kernel 7.0 was released **April 2026** per the version history. |
| "Kernel 6.12.90" | This is what you have — it's the **LTS kernel** (supported through Dec 2028). You're likely on a backports kernel if you're on Debian 12 and running 6.12. |
| "Debian 12 + kernel 6.12" | Valid if you installed `linux-image-6.12.0` from bookworm-backports |

### Would upgrading help?

**Short answer: No benefit in upgrading from 6.12.90** for this use case. Here's why:

| Feature | Your kernel (6.12) | Kernel 7.0 | Kernel 6.18 (next LTS) |
|---------|-------------------|------------|----------------------|
| zswap | ✅ Full support | ✅ Same | ✅ Same |
| zswap shrinker | ✅ (6.8+) | ✅ | ✅ |
| Multi-Gen LRU | ✅ (6.1+) | ✅ | ✅ |
| Multi-size THP | ✅ (6.8+) | ✅ | ✅ |
| "Better swapping perf" | N/A | N/A | **✅** (6.18 LTS) |
| "Phase II swap table" | N/A | **✅** | N/A (not LTS) |

**Key findings**:
1. **Kernel 7.0's "Phase II swap performance with swap table"** is a swap metadata optimization — it reduces contention on the swap address space lock during concurrent swap-out. This benefits large NUMA systems with massive parallel swap I/O. On your 8-core VM, the benefit is marginal.
2. **Kernel 6.18** (next LTS, Nov 2025) includes "better swapping performance" — but you'd need to wait for it to be available in backports.
3. **Neither upgrade changes the fundamental architecture**: ZSWAP + disk swap is the same on 6.12, 7.0, and beyond.

**Recommendation**: Stay on Debian 12 with kernel 6.12 from backports. It's an LTS kernel, it has all the features you need (zswap shrinker, MGLRU, multi-size THP), and it's stable. There is **zero** compelling reason to chase a non-existent "Debian 13" or kernel 7.0 for your use case.

If you DO want a newer kernel, wait for **6.18 LTS** (expected late 2025, will become available in backports), which has explicit swap performance improvements. That is the next LTS release you care about, not 7.0.

---

## 9. Summary: Concrete Action Plan

| # | What | How |
|---|------|-----|
| 1 | **Install backports kernel** | `apt install -t bookworm-backports linux-image-amd64` (if not already on 6.12) |
| 2 | **Enable zswap + zswap shrinker** | Add `zswap.enabled=1 zswap.shrinker_enabled=1 zswap.compressor=zstd zswap.max_pool_percent=35` to kernel cmdline |
| 3 | **Create 4×4GB swap files** | `mkdir -p /var/swap && for i in {0..3}; do dd if=/dev/zero of=/var/swap/swap$i bs=1M count=4096; mkswap /var/swap/swap$i; done` |
| 4 | **Activate with equal priority** | `swapon -p 10 /var/swap/swap[0-3]` |
| 5 | **Apply sysctl tuning** | `/etc/sysctl.d/99-swap.conf` with values from section 3 above |
| 6 | **Enable MGLRU** | `vm.lru_gen_enabled=0x7` in sysctl |
| 7 | **Set Soulmask Docker limits** | In Pterodactyl egg: `--memory=16g --memory-reservation=8g --cpu-shares=2048 --blkio-weight=1000` |
| 8 | **Create dev slice** | `/etc/systemd/system/dev-workloads.slice` with `MemoryMax=6G CPUWeight=50 IOWeight=50` |
| 9 | **Apply network qdisc** | `sysctl net.core.default_qdisc=fq_codel` |
| 10 | **Set up persistence** | Boot-time oneshot service (section 7) |
| 11 | **Monitor** | `pgmajfault` (not `vmstat si`), `/proc/pressure/memory`, `/sys/kernel/debug/zswap/writeback_ratio` |

Your intuition about compression > disk swap is **correct** for the warm working set, but ZSWAP already gives you that (pages stay compressed in RAM until the pool fills, then only the *coldest* compressed pages go to disk). The critical finesse that the toolkit misses is: **enable the zswap shrinker** (off by default!!) and **use zstd, not lz4**. Those two changes alone will make more difference than any architecture choice.
