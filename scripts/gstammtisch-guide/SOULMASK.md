# gstammtisch — Soulmask Server Specifics

> Game-server-specific operations: priority/protection, orderly shutdown, and
> RCON administration. General memory architecture is in
> [MEMORY-ARCHITECTURE.md](MEMORY-ARCHITECTURE.md).

## 1. What we're running

Pterodactyl Wings → Docker → `WSServer-Linux-Shipping` (egg: `egg-soulmask.json`).
Key facts pulled from the egg:

| Item | Value | Note |
|---|---|---|
| Image | `ghcr.io/ptero-eggs/steamcmd:debian` | SteamCMD egg |
| Startup | `WSServer-Linux-Shipping {{MAP_TYPE}} -server … -saving={{SAVE_TIME}} -backup={{BACKUP_TIME}} … -rconport={{RCON_PORT}} -rconpsw={{RCON_PASSWORD}} -MULTIHOME=0.0.0.0` | |
| **Stop command** | `^C` | Wings sends **SIGINT** → the server saves + exits cleanly |
| Periodic save | `-saving=600` (objects→DB), `-backup=960` (DB backup) | timer-based persistence |
| RCON | `RCON_PORT` (19000), `RCON_PASSWORD` | Source RCON over TCP, **IP-whitelisted** |
| Footprint | ~13–14 GB RAM, 80 % of 1 core, much of it cold | |

The process `WSServer-Linux-Shipping` is a **child** of the container entrypoint — it shows in `docker top`/host `ps`, **not** in `docker ps {{.Command}}` (which shows the truncated entrypoint). Every helper here detects the container via `docker top`.

> **Never use `docker start`/`stop` on the Soulmask container directly.** Wings tracks container lifecycle via its own internal state; bypassing it leaves Wings showing `state: offline` even while the process is live — the panel shows 0 CPU/RAM/console, crash detection stops working, and Wings may attempt a conflicting reconciliation on its next tick. Always use the Wings API or the panel (§5 below).

## 2. Priority & protection

Soulmask must always preempt dev work and be the last thing reclaimed. Layers:

1. **Pterodactyl panel** — set the server's memory/CPU/IO limits there (Wings applies them on every (re)start; survives container recreation). This is the reliable lever.
2. **cgroup protection** — [`setup-cgroups.sh`](files/usr/local/sbin/setup-cgroups.sh) sets on the Soulmask scope:
   - `memory.min` = **measured hot+warm set** (DAMON — see [OBSERVATION.md §8](OBSERVATION.md)); the "RAM never swapped" guarantee.
   - `memory.low` = 12G (best-effort).
   - `memory.zswap.writeback = 0` — its pages stay in the fast compressed pool, never proactively to disk (no fault-back stutter).
   - `io.weight = 4950`, `io.bfq.weight = 1000` — top I/O priority under BFQ (see §2b).
   - `cpu.weight = 800` — CPU preemption headroom over default-weight containers.
3. **dev work is fenced off** in `dev-workloads.slice` (low weight, capped, OOM-first). See [MEMORY-ARCHITECTURE.md §5](MEMORY-ARCHITECTURE.md).

> Set `memory.min` from real measurement and err **high** — a too-low floor lets the game fault pages back during save/join/AI bursts → visible stutter.

## 2b. I/O scheduler & bench isolation

### The problem that existed

`setup-cgroups.sh` set Soulmask `io.weight=4950` but background benchmarks (`docker-repack`, the `dstdns/test-runner` container) were still disrupting the game server despite also having `ionice=idle`. **Both were no-ops.** Root cause: the disk scheduler was `[none]`.

| Setting | Expected effect | Actual effect with `[none]` |
|---|---|---|
| cgroup `io.weight` | proportional I/O share | **ignored** — no scheduler to enforce it |
| `ionice -c idle` | lowest ioprio class | **ignored** — `[none]` passes I/O straight to device queue without reordering |
| `io.max` (hard cap) | works via blk-throttle | ✓ works regardless of scheduler |

The I/O issue also isn't primarily bandwidth — it's **device utilization from random seeks**. The benchmark's write pattern (3.4 M write IOs vs 674 K read IOs observed cumulatively) causes many small random seeks that saturate the device queue, raising latency for everyone on the disk. Even at low `%util`, queue contention raises `w_await` and `r_await` for Soulmask's periodic saves.

### The fix: BFQ scheduler

BFQ (Budget Fair Queueing) is the **only multi-queue scheduler** that natively enforces cgroup v2 `io.weight`. It also adds seek-awareness: it tracks "think time" per process to distinguish sequential bursts from random access, giving latency-sensitive processes time slices on the disk head.

```bash
# Load module (already persisted in /etc/modules-load.d/bfq.conf)
modprobe bfq
# Activate for vda (already persisted in /etc/udev/rules.d/60-bfq-scheduler.rules)
echo bfq > /sys/block/vda/queue/scheduler
# Verify
cat /sys/block/vda/queue/scheduler   # → none mq-deadline [bfq]
```

Switching to BFQ immediately activates the existing `io.weight` values AND adds a BFQ-specific knob `io.bfq.weight` (range 1–1000; separate from `io.weight`'s 1–10000 range). Both are set by `setup-cgroups.sh`.

### Live settings (applied by `setup-cgroups.sh` after RCON responds)

| Container | `io.weight` | `io.bfq.weight` | `io.max` | `cpu.weight` |
|---|---|---|---|---|
| Soulmask | 4950 | **1000** (BFQ max) | none | **800** |
| devcontainer (docker-repack) | 1 | **1** (BFQ min) | 100r/400w IOPS, 30 MB/s | 100 |
| test-runner | 1 | **1** (BFQ min) | 100r/400w IOPS, 30 MB/s | 100 |

Effective I/O ratio (BFQ): **1000:1**. Bench gets CPU slices on the disk head only when Soulmask's queue is idle.

No `memory.high` on bench containers — the devcontainer is also the VSCode workspace, and capping its total cgroup memory kills the IDE. BFQ priority + `io.max` is the right lever without side-effects.

The `io.max` hard caps are belt-and-suspenders: they cut the benchmark's 709 r/s burst (observed without caps) to ≤100 IOPS before it even reaches the BFQ scheduler, ensuring the device queue depth stays clean for Soulmask's periodic DB saves.

### Verifying live state

```bash
# Confirm BFQ is active
cat /sys/block/vda/queue/scheduler   # → [bfq]

# Resolve cgroups
SOUL_PID=$(docker top b87c0a5b-2387-4a1c-8863-ff23e6800a1d 2>/dev/null | awk '/WSServer/{print $2}' | head -1)
SOUL_CG=/sys/fs/cgroup$(awk -F: '/^0::/{print $3}' /proc/$SOUL_PID/cgroup)

# Read all I/O and CPU settings at once
for k in io.weight io.bfq.weight cpu.weight; do
    echo "$k = $(cat $SOUL_CG/$k 2>/dev/null)"
done
cat $SOUL_CG/io.max   # should be empty (no limit on Soulmask itself)

# Watch bench cgroup I/O in real time
BENCH_CG=$(docker inspect -f '{{.State.Pid}}' dstdns-devcontainer-vb 2>/dev/null \
  | xargs -I{} awk -F: '/^0::/{print $3}' /proc/{}/cgroup \
  | xargs -I{} echo /sys/fs/cgroup{})
cat $BENCH_CG/io.stat   # watch rbytes/wbytes accumulate slowly under throttle
```

### Why `io.latency` is absent — and what replaces it

`io.latency` would give Soulmask a **hard latency guarantee**: when its I/O latency exceeds a target (e.g. 5 ms), the kernel automatically throttles competing cgroups to restore it. This is stronger than weight-based priority.

It's absent because the Debian 13 kernel explicitly disabled it:
```
# CONFIG_BLK_CGROUP_IOLATENCY is not set   ← from /boot/config-$(uname -r)
```

Debian dropped `io.latency` in favour of `io.cost.qos`, which is compiled in:
```
CONFIG_BLK_CGROUP_IOCOST=y
```

**`io.cost.qos`** (available now at `/sys/fs/cgroup/io.cost.qos`) is the modern replacement. Instead of reacting to exceeded latency, it models the device's capacity as a virtual time budget and distributes tokens proportionally to `io.weight`. With Soulmask at 4950 and bench at 1, Soulmask gets 99.98 % of all tokens. Enabling it is complementary to BFQ:

```bash
# Enable io.cost model on vda (254:0) — auto-tunes device characteristics
# rlat/wlat: 5 ms target at 95th percentile (matches our thin-provisioned SSD-backed vda)
echo "254:0 enable=1 ctrl=auto rpct=95.00 rlat=5000 wpct=95.00 wlat=5000 min=1.00 max=99.00" \
  > /sys/fs/cgroup/io.cost.qos
# Verify
cat /sys/fs/cgroup/io.cost.qos
```

With BFQ + io.max already in place, `io.cost.qos` is not strictly necessary — but it adds a second enforcement layer and makes the existing io.weight ratio enforceable even at the root cost model level.

**When you'd get native `io.latency`:**
- Compile a custom kernel with `CONFIG_BLK_CGROUP_IOLATENCY=y`
- Switch to an Ubuntu LTS or RHEL kernel (both enable it)
- Once the 6.18 LTS kernel is available via trixie-backports (planned — see [MEMORY-ARCHITECTURE.md §8](MEMORY-ARCHITECTURE.md)), check if Debian re-enables it; if not, `io.cost.qos` achieves the same outcome with less kernel overhead

## 2c. Pak file ramdisk — eliminating major page faults entirely

### Why file cache is the weak link

`memory.min` protects Soulmask's *anonymous* pages (heap, stack, engine allocations). Pak files are **clean file-backed pages** — the kernel can free them at any time (they can be re-read from disk) and does not route them through zswap. `memory.zswap.writeback=0` has no effect on them. Under pressure, they simply vanish from the page cache silently.

When a pak page is missing and the game thread accesses it:
- **Current (disk-backed)**: major page fault → disk read → 1–10 ms per page → sequential stalls when many pages are gone → 3-second "chest open" hang
- **With ramdisk**: minor page fault → zswap decompress → ~1–10 µs → effectively zero stall

The conversion is simple: **store the pak file on tmpfs**. tmpfs pages are anonymous (swap-backed). They go through zswap, are covered by `memory.min`, and cannot be evicted by benchmark file I/O.

### gstammtisch specifics

The game has one pak file: `WS-LinuxServer.pak` at **~1.7 GB**.
- Available RAM at steady state: ~6.8 GB → fits entirely in RAM with 5 GB to spare.
- If the game grows or RAM shrinks: cold pak sections compress at ~3.6× in zswap → 1.7 GB uncompressed → ~470 MB compressed. Never hits disk (`memory.zswap.writeback=0`).
- Startup cost: one sequential read of 1.7 GB from disk to populate the tmpfs (≈ 5–10 s on this SSD-backed VM). Every subsequent access is RAM-speed.

### Why hot-activation is impossible

The game mmap's the pak file at startup. That mmap is bound to the original disk inode — no filesystem change after the process starts can redirect it. Even if we mount a tmpfs over the Paks directory while the server is running (and even `nsenter` into the container's mount namespace to do so), the game thread still reads pak pages from the old inode.

**The change always requires a container restart.** What we CAN do on the fly is prepare the ramdisk while the server is running; the new mapping takes effect on the next container start.

### Volume path discovery — portability

The setup script does NOT hard-code the Pterodactyl volume UUID. It discovers the pak directory at runtime using the first match from:

1. `SOULMASK_PAK_DIR` env var (explicit override)
2. `/etc/soulmask-ramdisk.conf` containing `SOULMASK_PAK_DIR=` or `SOULMASK_VOLUME_UUID=`
3. `/run/soulmask-pak-ramdisk.state` — path saved from a prior successful run
4. Filesystem scan: `find /var/lib/pterodactyl/volumes -xdev -name 'WS-LinuxServer.pak'`  
   (`-xdev` stops at mount boundaries, so a live tmpfs overlay on the Paks directory doesn't confuse the scan)

On a fresh deployment where the UUID is unknown, the scan finds it automatically. If you want to pin it explicitly:
```bash
echo "SOULMASK_VOLUME_UUID=b87c0a5b-2387-4a1c-8863-ff23e6800a1d" > /etc/soulmask-ramdisk.conf
```

### Toggle (on-the-fly, requires server restart to take effect)

```bash
# See current state
sudo soulmask-pak-ramdisk-toggle.sh status

# Activate ramdisk (copies pak now; takes effect on next Wings stop+start)
sudo soulmask-pak-ramdisk-toggle.sh on

# Deactivate (unmounts now; takes effect on next Wings stop+start)
sudo soulmask-pak-ramdisk-toggle.sh off

# Toggle current state
sudo soulmask-pak-ramdisk-toggle.sh
```

After toggling: stop the server from the Wings panel → wait for clean exit → start it again. The script will remind you.

### Live activation (container restart only — no host reboot needed)

Wings recreates the Soulmask container on each start (`docker rm` + `docker create` + `docker start`). Docker binds the volume with `MS_BIND|MS_REC` (recursive), so any host-side submount already in place on `$VOL_BASE/WS/Content/Paks/` is automatically visible inside the new container. This means **only a server stop+start via Wings is needed**, not a host reboot.

**Estimated downtime: ~2 minutes** (1.7G pak copy ~5–10 s, server startup ~60–90 s).

#### Step 1 — dry-run (verify path discovery, no changes)
```bash
sudo /usr/local/sbin/soulmask-pak-ramdisk-setup.sh --dry-run
```
✓ Confirm: shows correct `$PAK_DIR` (the real volume path, not a tmpfs)

#### Step 2 — set up ramdisk while server is running
```bash
sudo soulmask-pak-ramdisk-toggle.sh on
```
This copies ~1.7G from disk to tmpfs and bind-mounts it over `$PAK_DIR`. The running server is not affected — it still reads paks from its current mmap. RAM usage rises by ~1.7G during the copy (tmpfs pages).

✓ Confirm:
```bash
sudo soulmask-pak-ramdisk-toggle.sh status
# → ACTIVE — ramdisk is mounted
findmnt $(cat /run/soulmask-pak-ramdisk.state | cut -d= -f2 | tr -d "'")
# → SOURCE: tmpfs, FSTYPE: tmpfs
```

#### Step 3 — enable the service (persist across host reboots)
```bash
sudo systemctl enable soulmask-pak-ramdisk.service
```
Without this, the ramdisk is lost after the next host reboot and the container would start with disk-backed paks again.

✓ Confirm: `systemctl is-enabled soulmask-pak-ramdisk.service` → `enabled`

#### Step 4 — save the game
```bash
exec-soulmask-rcon.sh SaveWorld 0          # serialize actors → in-memory DB
exec-soulmask-rcon.sh BackupDataBase world # flush in-memory DB → world.db on disk
```
✓ Confirm: `BackupDataBase world` output contains `succeed` (or similar acknowledgement)

#### Step 5 — stop the server via Wings panel
Wings panel → server → power → **Stop**. Wait for "Offline" status.

✓ Confirm:
```bash
docker ps | grep -v soulmask   # Soulmask container no longer listed
findmnt $(cat /run/soulmask-pak-ramdisk.state | cut -d= -f2 | tr -d "'")
# → still shows tmpfs (host-side mounts survive container stop)
```

#### Step 6 — start the server via Wings panel
Wings panel → server → power → **Start**. Wait for "Running" status.

#### Step 7 — verify new container sees tmpfs
```bash
CID=$(for c in $(docker ps -q); do
  docker top "$c" 2>/dev/null | grep -q WSServer && echo "$c"; done | head -1)
docker exec "$CID" findmnt /home/container/WS/Content/Paks
```
✓ Confirm: `FSTYPE` column shows `tmpfs`

If it shows `ext4` (or any non-tmpfs): the ramdisk was NOT picked up by the new container. Do not proceed — see troubleshooting below.

#### Step 8 — wait for RCON, verify cgroup shmem
```bash
exec-soulmask-rcon.sh List_OnlinePlayers     # wait until RCON responds (~60–90 s)

SOUL_CG=/sys/fs/cgroup$(docker inspect -f '{{.State.Pid}}' "$CID" \
  | xargs -I{} awk -F: '/^0::/{print $3}' /proc/{}/cgroup)
grep '^shmem ' "$SOUL_CG/memory.stat"
```
✓ Confirm: `shmem` value is > 0 (pak pages are in RAM as tmpfs-backed shmem, not file cache)

#### Troubleshooting: container sees ext4 instead of tmpfs

This means Docker created the container before seeing the ramdisk mount. Causes:
- Wings started the container before `soulmask-pak-ramdisk-setup.sh` finished the bind
- The bind mount was not recursive (unusual Docker config)

Fix:
```bash
# Tear down, stop server again, re-run setup, start server
sudo soulmask-pak-ramdisk-toggle.sh off
# (stop server via Wings)
sudo soulmask-pak-ramdisk-toggle.sh on
# Verify with findmnt BEFORE starting from Wings
findmnt $(cat /run/soulmask-pak-ramdisk.state | cut -d= -f2 | tr -d "'")
# (start server via Wings)
```

### Enable at boot (permanent)

Once activated via the live procedure above, the service is already enabled. On a fresh host install:
```bash
# Dry-run first to verify volume discovery:
sudo /usr/local/sbin/soulmask-pak-ramdisk-setup.sh --dry-run

# Enable permanently (runs Before=docker.service on every boot):
sudo systemctl enable --now soulmask-pak-ramdisk.service

# Then do a Wings stop+start — same steps 4–8 above
```

### Memory accounting after ramdisk

| Layer | Size | Where |
|---|---|---|
| Hot pak regions (game loop, tight mesh) | ~30–100 MB | RAM (LRU hot) |
| Warm pak regions (currently explored world) | ~1–2 GB | RAM (LRU warm) |
| Cold pak regions (unexplored areas) | ~0–1.2 GB | zswap (~350 MB compressed) |
| Soulmask anon RSS | ~4 GB | RAM (protected by `memory.min=4608M`) |
| Soulmask cold anon | ~5.7 GB | zswap (~1.9 GB compressed, `writeback=0`) |

Total RAM used by Soulmask ≈ 5–6 GB vs 9.7 GB without ramdisk — because the pak file is now part of the LRU pool and cold sections compress efficiently.

> **Before enabling**: stop the server via Wings, then run the service manually to confirm the bind mount works cleanly, then start the server and confirm RCON responds and players can connect. The only observable difference to players should be: initial world load may be slightly faster (pak is in RAM from the first access); subsequent area transitions are faster (no disk reads for pak pages).

## 2d. Monitoring — zswap accounting & periodic saves

### Per-cgroup zswap usage

Soulmask's cgroup exposes three key files:

- `memory.current` — bytes in RAM (anon + file cache, **not** swap)
- `memory.swap.current` — bytes in swap, uncompressed equivalent (zswap + disk swap)
- `memory.zswap.current` — bytes in zswap pool, **compressed** (the actual pool storage used)

With `writeback=0`, no pages hit disk; `memory.swap.current` ≈ total in zswap (uncompressed). The ratio `memory.swap.current / memory.zswap.current` is the effective compression ratio.

```bash
SOUL_CG=/sys/fs/cgroup$(docker inspect -f '{{.State.Pid}}' \
  $(docker ps -q --filter ancestor=ghcr.io/ptero-eggs/steamcmd:debian | head -1) \
  | xargs -I{} awk -F: '/^0::/{print $3}' /proc/{}/cgroup)

echo "=== Soulmask memory ==="
RAM=$(cat $SOUL_CG/memory.current)
SWAP=$(cat $SOUL_CG/memory.swap.current)
ZSWAP=$(cat $SOUL_CG/memory.zswap.current)
awk "BEGIN{printf \"  in RAM:        %4dM\n  in swap total: %4dM (uncompressed)\n  in zswap pool: %4dM (compressed, %.1fx ratio)\n  disk swap:     %4dM (want 0 with writeback=0)\n\",
  $RAM/1048576, $SWAP/1048576, $ZSWAP/1048576, $SWAP/$ZSWAP, ($SWAP-$ZSWAP)/1048576}"

echo ""
echo "=== memory.stat breakdown ==="
grep -E '^(anon |file |shmem |file_mapped ) ' $SOUL_CG/memory.stat \
  | awk '{printf "  %-15s %dM\n",$1,$2/1048576}'
echo "  (shmem=0 before ramdisk; shmem>0 = pak pages in RAM after ramdisk)"

echo ""
echo "=== Global zswap pool ==="
PAGES=$(sudo cat /sys/kernel/debug/zswap/stored_pages)
POOL=$(sudo cat /sys/kernel/debug/zswap/pool_total_size)
awk "BEGIN{printf \"  stored:     %4dM uncompressed (%d pages)\n  pool size:  %4dM compressed (%.1fx ratio)\n  max_pool:   30%% of RAM\n\",
  $PAGES*4/1024, $PAGES, $POOL/1048576, $PAGES*4096/$POOL}"
```

**Baseline values (2026-06-27, 2 players online):**
- RAM: 6022M | swap (uncompressed): 9363M | zswap (compressed): 1804M → **5.19× compression**
- `anon=3983M, file=198M, shmem=0M` — no ramdisk yet
- Global pool: 6.7G uncompressed → 2.2G compressed at 3.04× (Soulmask dominates the pool)

The 5.19× ratio vs 3.04× global is explained by zswap's same-filled page optimization: UE4 pre-allocates heap pools that are mostly zero-filled — those are stored as single entries (no pool space), inflating the apparent ratio.

### Tracking the pak ramdisk separately

After `soulmask-pak-ramdisk.service` is enabled, pak tmpfs pages accessed by the container are charged to its cgroup as `shmem` pages (subset of `file` in `memory.stat`):

- **Pak pages in RAM**: `grep '^shmem ' $SOUL_CG/memory.stat` — goes from 0 to ≤1700M
- **Pak pages in zswap**: shows up as additional `memory.swap.current` vs pre-ramdisk baseline
- **Pak pages on disk**: 0 always (pak files are read-only; disk copy stays at `$PAK_DIR/` as the source for the tmpfs copy, never directly accessed by the game)

Pak and game anon pages are **not separately tracked within the same cgroup** — they both contribute to `memory.zswap.current`. To see roughly how much of zswap is pak: `(memory.swap.current - pre_ramdisk_baseline) / compression_ratio`.

### Soulmask save & backup mechanism

The egg description and RCON command semantics reveal a **two-stage pipeline**:

| Stage | Trigger | What it does | RCON equivalent |
|---|---|---|---|
| **Save** | `-saving=600` (10 min) | Serializes live UE4 actor graph → **in-memory SQLite DB** (RAM). CPU-intensive; no disk I/O. | `SaveWorld 0` |
| **Backup** | `-backup=960` (16 min) | Flushes the in-memory DB → **`world.db` on disk**. Pure sequential write, no game logic. | `BackupDataBase world` |

Egg variable descriptions confirm this verbatim:
- `-saving`: *"Specifies the interval for writing game objects to the database"* (in-memory)
- `-backup`: *"Specifies the interval for writing the game database to disk"*

`SaveWorld 0` = in-memory DB update only (no disk write).  
`SaveWorld 1` = confirmed to write to disk immediately (`world.db` path in response). **Unclear whether it also first does the in-memory update** (i.e. acts as `SaveWorld 0` + `BackupDataBase world`) or only flushes whatever is currently in the in-memory DB. Until confirmed, the safe manual sequence for a full pre-maintenance save is:
```bash
exec-soulmask-rcon.sh SaveWorld 0        # serialize live actors → in-memory DB
exec-soulmask-rcon.sh BackupDataBase world  # flush in-memory DB → world.db on disk
```

**What gets accessed:**
- **Save timer (600s)**: reads UE4 actor objects (anon RAM) → writes to in-memory DB. No disk I/O, no pak reads.
- **Backup timer (960s)**: reads in-memory DB → writes `world.db` to disk. IO spike, no CPU serialization.
- Both paths are on real LVM (`WS/Saved/`), completely separate from the pak ramdisk (`Content/Paks/`). They do not interact.

**Do saves warm the LRU / prevent zswap eviction?**
The `-saving` timer accesses the UE4 actor graph (anon pages). Pages that are zswap'd get decompressed back into RAM when touched, resetting their LRU clock. But Soulmask almost certainly does delta saves — only recently dirtied actors are serialized. Cold regions stay cold. In any case, with `memory.zswap.writeback=0` on Soulmask, no pages reach disk regardless of save frequency. The right lever for protecting the working set is `memory.min` height, not save cadence.

**Should saves be more frequent?**
At most 16 minutes of data loss on crash (last disk flush). For 2 players, 600s/960s is fine. Shortening adds CPU serialization spikes more often. On demand: `SaveWorld 0` then `BackupDataBase world` (or just `SaveWorld 1` if confirmed to do both).

## 3. Orderly shutdown on host `reboot` — the problem & fix

**Problem.** Stopping from the panel works (Wings sends `^C`/SIGINT → game saves). But a host `reboot` **bypasses Wings**: systemd stops `docker.service`, Docker kills the container with its default **SIGTERM + ~10 s**, then SIGKILL. Soulmask saves on **SIGINT, not SIGTERM** — wrong signal, too little time → **no save**. ("Connection severs immediately" = Wings/UI going down at the same moment.)

**Fix.** A systemd unit that, on shutdown, saves via RCON and waits — sequenced so it runs while the container is still alive:
- [`soulmask-graceful-stop.service`](files/etc/systemd/system/soulmask-graceful-stop.service): `After=docker.service`. On shutdown systemd stops units in reverse start order ⇒ **this (Wings stop + save+wait) → docker**. `TimeoutStopSec=180` allows a large-world DB write.
- [`soulmask-shutdown.sh`](files/usr/local/sbin/soulmask-shutdown.sh) (its `ExecStop`): **first stops Wings** (matched by Docker Compose service label) so Wings can't auto-restart Soulmask mid-save (Wings runs as a Docker Compose container — `ptero-wings-wings-1` — not a systemd service, so there is no `wings.service` dependency to exploit). Then issues RCON `SaveAndExit 10`, waits for the container to exit, and falls back to `docker kill -s INT` (the same SIGINT the panel uses) if RCON is unavailable.

**Verify:** after `systemctl enable soulmask-graceful-stop.service`, run a real `reboot` and confirm the save/DB file mtime advanced and the logs show a clean shutdown. Interim habit until verified: stop the server from the panel before any manual reboot.

## 4. RCON administration — [`exec-soulmask-rcon.sh`](scripts/exec-soulmask-rcon.sh)

Runs **any** RCON command against the running container and prints the reply, with a connection test first. Command list: <https://saraserenity.net/soulmask/remote_console.php>.

```bash
docker pull itzg/rcon-cli                         # once (pre-pull so shutdown never needs the registry)
exec-soulmask-rcon.sh -d                          # debug: detection + port/pass status + connection test
exec-soulmask-rcon.sh List_OnlinePlayers          # (alias: lp) — read-only, used as the pre-flight test
exec-soulmask-rcon.sh SaveWorld 0                 # serialize actors → in-memory DB only (no disk write)
exec-soulmask-rcon.sh BackupDataBase world        # flush in-memory DB → world.db on disk
exec-soulmask-rcon.sh SaveWorld 1                 # writes to disk immediately (confirmed); whether it also updates in-memory DB first is unconfirmed — use 0+BackupDataBase to be safe
exec-soulmask-rcon.sh SaveAndExit 15              # save + 15s shutdown countdown  (cancel: StopCloseServer)
exec-soulmask-rcon.sh broadcast Reboot in 60s     # multi-word args are forwarded intact
```

**How it works / why these choices:**
- **Source RCON**, so the tiny `itzg/rcon-cli` image works directly.
- **IP whitelist:** the helper runs `itzg/rcon-cli` inside the Soulmask container's *own* network namespace (`--network container:<cid>`), so it connects to **127.0.0.1** — a loopback connection that the whitelist accepts, with no IP discovery needed and nothing exposed publicly.
- **Port/password** are read from the container env (`RCON_PORT`/`RCON_PASSWORD`, injected by Wings) — nothing hard-coded.
- **Multi-arg:** all arguments are forwarded to `rcon-cli`, which joins them into one command — so `SaveWorld 0`, `kick <id> <reason>`, etc. work.

**If the connection test fails**, the `-d` output localizes it: container detection, empty password env, or — most likely — the **RCON IP whitelist** rejecting the connection. If loopback is rejected, add `127.0.0.1` to Soulmask's RCON allowed-IP list (server config) or whitelist the helper's bridge IP.

### 4b. High-frequency RCON latency probe (`rcon_probe.py`)

`exec-soulmask-rcon.sh` spawns a fresh `nsenter` + Docker container per call.
The process overhead alone is **~720 ms**, making it useless as a game-thread
latency probe.  `rcon_probe.py` keeps a **persistent TCP connection** to the
RCON port and sends a probe command every 200 ms on the same socket.

True game-thread round-trip (measured at RSS ≈ 3.9 G in zswap recovery):
**7–34 ms** — a 20–100× improvement in absolute accuracy.

```bash
# Port/password are injected into the container env by Wings.
PORT=$(sudo docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \
       <container-id> | sed -n 's/^RCON_PORT=//p')
PASS=$(sudo docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \
       <container-id> | sed -n 's/^RCON_PASSWORD=//p')
PID=$(sudo docker top <container-id> | awk '/WSServer/{print $2}' | head -1)
SCOPE=$(sudo awk -F: '/^0::/{print $3}' /proc/$PID/cgroup \
        | xargs -I{} echo /sys/fs/cgroup{})

# Run inside the container's network namespace for loopback access
sudo nsenter --net=/proc/$PID/ns/net \
  python3 scripts/damon-analysis/rcon_probe.py \
  --host 127.0.0.1 --port $PORT --password $PASS \
  --interval 0.2 --pid $PID --cgroup-scope $SCOPE \
  --output scripts/damon-analysis/output/rcon_<run>.jsonl
```

Output per probe (JSONL): `ts`, `elapsed`, `rtt_ms`, `ok`, `rss_kb`,
`swap_kb`, `memory_high_bytes`.

**Corrected interpretation of run 3 latency data:**
The run 3 "RCON latency" log (720 ms median, 10 s max) reflected almost
entirely the `nsenter` + `docker run` process-spawn cost, not the game thread.
The 10-second stalls should be treated as **unmeasured** — the persistent probe
is required for valid latency data.  Run 4 onward uses `rcon_probe.py`.

## 5. Wings daemon API — scripted start/stop

Wings exposes a local HTTPS API on port 8080.  No extra secrets needed — the
auth token is already on the host at `/etc/pterodactyl/config.yml` (root-only, mode 600).

```bash
# Read the token without printing it to the terminal
WINGS_TOKEN=$(sudo awk '/^token:/{print $2}' /etc/pterodactyl/config.yml)
SERVER_UUID="b87c0a5b-2387-4a1c-8863-ff23e6800a1d"

# Start / stop / restart / kill
curl -sk -X POST \
  -H "Authorization: Bearer $WINGS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"start"}' \
  https://localhost:8080/api/servers/$SERVER_UUID/power

# Check state ("running" | "offline" | "starting" | "stopping")
curl -sk \
  -H "Authorization: Bearer $WINGS_TOKEN" \
  https://localhost:8080/api/servers/$SERVER_UUID \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['state'], d['utilization'])"
```

Valid `action` values: `start`, `stop`, `restart`, `kill`.

> `kill` sends SIGKILL immediately — no save. Use `stop` (Wings sends SIGINT → clean save) or prefer RCON `SaveAndExit` for a graceful, timer-controlled shutdown.

The panel URL is `https://tsstammtisch.dchive.de:444`.  Claude Code can reach the
Wings daemon directly (above) without needing a panel API key.

## 6. Quick command reference

| Purpose | RCON command |
|---|---|
| List players | `List_OnlinePlayers` (alias `lp`) |
| In-memory DB update (no disk) | `SaveWorld 0` — what the `-saving` timer does |
| Flush in-memory DB to disk | `BackupDataBase world` — what the `-backup` timer does |
| Disk write shortcut (unconfirmed if it also updates in-memory first) | `SaveWorld 1` |
| Save + shutdown countdown | `SaveAndExit <seconds>` |
| Cancel a pending shutdown | `StopCloseServer` |
| Broadcast a message | `broadcast <text>` |
| Plain shutdown countdown | `shutdown <seconds>` |

Full list: <https://saraserenity.net/soulmask/remote_console.php>

## 7. DAMON measurement — 2026-06-25 (baseline, no players)

### Run parameters

```bash
sudo damon_cli.py timeseries-pid <pid> \
  --duration 900 --interval 30 \
  --sample-us 100000 --aggr-us 2000000
# 29 snapshots, t=36s–914s; server started cold, no players connected
```

### What DAMON actually measures

DAMON monitors **virtual address space** (vaddr ops), not physical pages.
It walks page-table entries every `sample_us` and marks a region "accessed" if
any PTE had its access bit set.  A page counts as warm even while it is in swap
— the next time it faults back in and the access bit is set, DAMON sees it.
This is why tracked warm memory (12–15 GiB) can greatly exceed RSS (7.7 GiB):

| Metric | Value | What it means |
|---|---|---|
| DAMON warm+hot | 13–15 GiB | virtual pages accessed in the last `aggr_us` window |
| RSS | 7.2–7.7 GiB | physical frames actually in RAM right now |
| VmSwap | 2.5 GiB | physical frames pushed to zswap / disk swap |
| Gap (~5 GiB) | = 15 − 7.7 − 2.5 | mmap'd game assets evicted from page cache; faulted on demand |

Soulmask (Unreal Engine) memory-maps large `.pak` / world data files.
The engine's virtual footprint is 15 GiB but the kernel can only keep ~10 GiB
resident at once (7.7 GiB RAM + 2.5 GiB swap) on this 16 GiB host.
DAMON warm ≠ "must be in RAM simultaneously".

### Steady-state snapshot (t > 200 s, typical)

| Class | Bytes | Count | Interpretation |
|---|---|---|---|
| hot | 1–2 **MiB** | 1–2 regions | tiny: tight game-loop pages (physics tick, netcode) |
| warm | 12–15 GiB | 18–20 regions | almost everything; accessed ≥ once per 2 s window |
| cold | 0–3 GiB | 0–5 regions | cycling with ~10-min save bursts (DB flush) |
| idle | 0 | 0 | nothing stays unaccessed for >120 s |

Key points:
- **Essentially no hot, almost all warm.** Unreal Engine's streaming design
  touches 15 GiB of virtual space broadly rather than hammering a small set.
- **Cold cycling** correlates with `−saving=600` (10-min save).  During the
  DB flush, large write buffers are accessed then released → briefly cold.
- **The 30 s measurement interval is too coarse** to see the save burst clearly
  (it can fall between two snapshots).  Use `--interval 5` for save profiling.

### `SOULMASK_MIN` decision (run 1, superseded)

DAMON warm 13 GiB exceeded available RAM on this 16 GiB host.  Interim value
`SOULMASK_MIN = 10G` was set from RSS (7.7 G) + VmSwap (2.5 G).  Superseded
by the calibrated run below — see §7b.

### Limitations of this run / what to improve

| Issue | Fix |
|---|---|
| `--interval 30` misses the save burst | re-run with `--interval 5` |
| `--min-regions 10` → coarse 1.5 GiB buckets | use `--min-regions 100 --max-regions 2000` |
| No CPU% or IO/s data | already fixed; new run captures these |
| No players → steady-state load is lower | repeat with players online |
| Thresholds: everything lands in "warm" | lower warm threshold or use auto-tune |

See §8 for the tuning rationale and §7b for the calibrated result.

## 7b. DAMON measurement — 2026-06-26 (calibrated, no players)

### Run parameters

```bash
sudo damon_cli.py timeseries-pid <pid> \
  --duration 1800 --interval 5 \
  --sample-us 100000 --aggr-us 2000000 \
  --min-regions 100 --max-regions 2000 \
  --hot-rate 30 --warm-rate 10 --cold-age 5 --idle-age 60
# 189 snapshots, t=12s–1190s (~20 min); server started via Wings API, no players
```

### Classification definitions used

With `sample_us=100 ms` and `aggr_us=2 s` → **20 samples per window**:

| Class | Threshold | Meaning in real terms |
|---|---|---|
| **hot** | `access_rate ≥ 30 %` | accessed in **≥ 6 / 20** samples = at least once every **~330 ms** |
| **warm** | `access_rate ≥ 10 %` | accessed in **≥ 2 / 20** samples = at least once every **~1 s** |
| **cold** | `access_rate == 0 %` AND `age ≥ 5 s` | zero accesses, idle for 5–60 s |
| **idle** | `access_rate == 0 %` AND `age ≥ 60 s` | zero accesses, idle for > 60 s |

`access_rate` is per-region: DAMON marks a region "accessed in this sample" if
**any PTE** within the region had its access bit set since the previous sample.
Regions adapt in size — frequently-accessed zones get split smaller, quiet zones
merge larger.

### Why is the hot area so small? (intentional)

Hot (~30 MiB) is the correct answer — not an artefact.  The detail:

- 300–370 hot **regions** in steady state, but each averages ~80 KiB.
  DAMON's adaptive splitting breaks the hot zone into many tiny pieces.
- The three biggest hot regions: **5 MiB @ 100 %**, **3 MiB @ 100 %**,
  **2 MiB @ 95 %** — these are the game server's actual tight-loop structures
  (network receive buffers, entity-component ticking state, main-thread stack).
- The remaining ~22 MiB of hot is 300+ sub-100 KiB fragments — scattered
  allocations the engine touches on every tick.
- The 5 GiB warm is game **world data** scanned once per tick (~10 Hz server
  rate), not hammered repeatedly.  Accessing 5 GiB in 100 ms intervals means
  each region gets hit roughly once or twice per 2 s window → 5–10 % rate →
  warm, not hot.

Relaxing `--hot-rate` to 15 % would reclassify the 10–30 % tier as hot
(growing hot to ~1–2 GiB) but does **not change `SOULMASK_MIN`** — only the
hot+warm total matters for the cgroup floor.  The relaxed threshold is useful
for visual inspection of which warm regions are "more important" but gives no
operational benefit until we add a DAMOS `lru_prio` scheme to explicitly pin
them.

For even finer hot-region resolution, use `--min-regions 500 --max-regions
10000` (next run) — currently DAMON hits ~680 regions spontaneously; raising
the ceiling lets it go finer in the warm zone.

### Startup phases

| Phase | t | hot | warm | cold | idle | CPU% | Note |
|---|---|---|---|---|---|---|---|
| Init | 12–31s | 22–43 MiB | 195–467 MiB | 2175–2467 MiB | 0 | 100% | engine loading, 156–340 regions |
| World load burst | 37–125s | 0–25 MiB | 0.7–2.6 GiB | 1.5–9.8 GiB | 0–691 MiB | **100–161%** | RSS grows 1→10 GiB; multi-core streaming |
| Settling | 131–300s | 0–73 MiB | 5.7–9.0 GiB | 4.3–7.9 GiB | 0.7–2.5 GiB | 80–130% | world resident, warm expanding |

CPU peaked at **160 %** — Soulmask uses multiple cores during the initial world
load.  Steady-state game tick is single-threaded (~66 %).

### Steady-state (t > 300 s, 142 snapshots, no players)

| Class | Median | p25–p75 | Max |
|---|---|---|---|
| **hot** | **30 MiB** | 30–30 MiB | 101 MiB |
| **warm** | **4.6 GiB** | 4.4–5.0 GiB | 6.3 GiB |
| cold | 6.0 GiB | 5.6–6.3 GiB | 7.3 GiB |
| idle | 3.7 GiB | 3.3–4.1 GiB | 4.5 GiB |
| RSS | 9.70 GiB | — | 9.79 GiB |
| VmSwap | **0** | — | **0** |
| CPU% | 66 % | 65–68 % | 78 % |
| Regions | 522 | 177–679 | 679 |

**Key insight:** the first run's `warm = 12–15 GiB` was an artefact of coarse
1.5 GiB buckets + `warm_rate = 5 %`.  A single page access anywhere in a
1.5 GiB region once per 2 s qualified the whole block.  With 100-region
granularity and `warm_rate = 10 %`, the true warm footprint is **4.6 GiB**.

VmSwap = 0 throughout — `memory.min = 10G` (then revised to 7G) prevented
all swapping on this run.

### `SOULMASK_MIN` calibrated value

```
hot+warm median: 4.63 GiB
hot+warm max:    6.40 GiB  (settling phase; players expected to raise this ~1 GiB)
safety margin:   + 600 MiB
─────────────────────────
SOULMASK_MIN = 7G
```

Applied 2026-06-26.  Cold (6.0 GiB) + idle (3.7 GiB) = **9.7 GiB** the kernel
can compress into zswap under pressure.  With `memory.zswap.writeback = 0`
these stay in the compressed pool and never hit disk.

### Remaining unknowns

| Scenario | Expected effect | When to measure |
|---|---|---|
| Players online (10–30) | warm +1–2 GiB; hot spikes; save events more frequent | during a play session |
| Save event (`-saving=600`) | cold → warm flip at 10-min mark; IO burst | capture across a save |
| Map DLC vs base map | current run was DLC map; base map has fewer assets | compare maps |

### Next measurement recommendations

```bash
--min-regions 500 --max-regions 10000   # finer: ~20 MiB buckets, see warm sub-structure
--hot-rate 15 --warm-rate 5             # wider hot tier for visual sub-classification
--monitoring_intervals_autotune          # kernel auto-tunes sample/aggr during load bursts
--interval 5 --duration 3600            # capture a full 10-min save cycle
```

## 7c. DAMON measurement — 2026-06-26 (zswap compression experiment, 1 player)

### Run parameters

```
--hot-rate 15  --warm-rate 5  --cold-age 15  --idle-age 60
--min-regions 500  --max-regions 10000
--sample-us 100000  --aggr-us 2000000  --interval 5  --duration 1800
```

No devcontainer running. Soulmask started fresh via Wings API.
`zswap.max_pool_percent` raised to 50% before start (pool ceiling 8 GiB).
`setup-cgroups.sh` was **not** called this run — `memory.min=0`, `memory.zswap.writeback`
not set initially; `writeback=0` applied manually before the compression experiment.

New metrics captured per snapshot: `minflt_rate`, `majflt_rate`, `ivcsw_rate`,
`sched_wait_ms` (added to `damon_cli.py` after this run, available from run 4 onward).

### Phases and results

| Phase | t (s) | RSS | zswap VmSwap | CPU | hot | warm |
|---|---|---|---|---|---|---|
| Startup | 0–120 | 9.74 G | 0 | 66% | 159 M | 5.9 G |
| No-player steady | 300–480 | 9.73 G | 0 | 71% | 156 M | 5.7 G |
| Player joins | 480– | 9.74 G | 0 | 85% | 206 M | 6.7 G |
| Save event | ~600 | 9.74 G | 0 | 106% peak | — | — |
| memory.high=7G applied | 901 | 9.74 G→3.5 G | 0→6.1 G | 380% burst | — | — |
| Compressed steady | 915–1650 | 3.8 G median | 6.0 G | 91% | 207 M | 6.7 G |
| Restored (memory.high=max) | 1700+ | 0.76 G* | 9.0 G* | 102% | 225 M | 6.9 G |

\* RSS was still refaulting from zswap at end of run — had not yet returned to 9.7 G.

**Total: 279 snapshots over 1803 s.**

### Player join effect

One player joining at t ≈ 474 s produced minimal memory change:
- CPU: +3% (66% no-player → 85% with player)
- warm: +1 G (5.7 G → 6.7 G), stable thereafter
- hot: +50 M median, noisy 90–450 M range

Server was already running full world simulation; adding one player is relatively
cheap on memory. Hot footprint driven by game loop, not player count.

### Save event (t ≈ 600 s)

- CPU spike: 82% baseline → **106%** peak (world serialisation, ~30 s duration)
- IO write bytes (`/proc/<pid>/io`): 0 — Unreal Engine uses async buffered writes;
  actual block-device flush goes through kernel writeback threads, not the game PID.
- Memory: no significant hot/warm shift during save.

### Compression experiment — key findings

`memory.high=7G` was applied at t = 901 s (with `memory.min=0`, no floor protection).
The kernel burst-reclaimed far past the 7 G target:

```
t=901s  RSS=9.74 G   swap=0      CPU=78%
t=908s  RSS=9.69 G   swap=57 M   CPU=88%    ← reclaim begins
t=915s  RSS=7.80 G   swap=1.95 G CPU=317%   ← burst
t=921s  RSS=4.92 G   swap=4.82 G CPU=381%   ← burst peak
t=927s  RSS=5.23 G   swap=4.47 G CPU=112%   ← settling
t=933s+  RSS≈3.8 G   swap≈6.0 G  CPU=91%   ← new steady state
```

The kernel overshot 7 G all the way to **3.8 G RSS** because `memory.min=0` provided
no floor.  Without a lower bound, kswapd runs in large bursts and does not back off.

**Compressed steady state (2-minute window after settling):**

| Metric | Value |
|---|---|
| Physical RSS | 3.8 G (median), 520 M–5.2 G range during settling |
| zswap VmSwap | 6.0 G |
| Compression ratio | **2.96×** for warm pages; **3.58×** for cold+idle combined |
| CPU overhead | +5–6% vs uncompressed (91% vs 85%) |
| hot tier (DAMON) | 207 M median — unchanged; kernel keeps hot pages resident |
| warm tier (DAMON) | 6.7 G median — unchanged; pages accessed through zswap on demand |

DAMON warm stayed at 6.7 G even with RSS at 3.8 G: DAMON measures virtual address
accesses, not physical residency.  Warm pages were decompressed on each access and
immediately re-eligible for reclaim.

### Effective memory footprint at compressed steady state

```
Physical RAM:   3.8 G
zswap pool:     ~2.0 G compressed  (6.0 G uncompressed at 2.96×)
─────────────────────────
Effective total:  5.8 G  vs  9.7 G uncompressed  →  40% RAM saving
```

### zswap compression ratios (Soulmask game data, zstd)

| Pages in zswap | Ratio | Notes |
|---|---|---|
| Warm pages only (~6 G) | 2.96× | active game data, higher entropy |
| Cold + idle added (~9 G total) | 3.58× | older/colder data compresses better |
| System baseline (no Soulmask) | 3.7× | system pages incl. zeroed |

### RCON latency as server-side proxy

RCON round-trip time (via `exec-soulmask-rcon.sh`) measures game-thread
responsiveness.  Baseline ~720 ms is high due to `nsenter`/docker-exec overhead,
not raw game latency — use the **relative delta**, not the absolute value.

| State | RCON median | RCON p95 | RCON max | minflt/s |
|---|---|---|---|---|
| Compressed (RSS≈750 M, swap≈9 G) | 726 ms | **10 350 ms** | **10 377 ms** | 3 563 |
| Restored (memory.high=max) | 712 ms | 781 ms | 1 021 ms | 14 280* |

\* Restore minflt peak 75 047/s = page-fault storm as pages refault from zswap.

The 10-second stall spikes in the compressed state are the rubberbanding the player
felt.  With 3.8 G RSS, DAMON hot=0 M appeared in 3 consecutive snapshots (~18 s) —
the reclaim storm temporarily evicted even hot pages, stalling the game thread.

### Caveats and confounds

- `memory.min=0` throughout — the kernel had no floor protection, causing the
  extreme overshoot.  Run 4 will set `memory.min` equal to the current stage floor.
- Rubberbanding onset time is uncertain: the player reported rubberbanding, but the
  compression went from 9.7 G → 3.8 G in a single burst.  We do not know whether
  3.8 G alone would cause rubberbanding if reached gradually.
- RCON latency baseline (~720 ms) is dominated by script overhead and cannot be used
  as an absolute game latency measure.
- Run 3 captured 279 snapshots but `minflt_rate`/`ivcsw_rate`/`sched_wait_ms` were
  **not** in the JSONL (added to `damon_cli.py` after the run); only in the side log
  `latency_20260626_011223.jsonl`.

### What run 4 must fix / changes made

1. `minflt_rate`, `majflt_rate`, `ivcsw_rate`, `sched_wait_ms` added to DAMON JSONL
   (done in `damon_cli.py`).
2. `rcon_probe.py` — persistent-connection 200 ms RCON probe, replaces old script-per-call
   approach.  True baseline RTT: **7–34 ms** (vs 720 ms script overhead).
3. Apply `memory.zswap.writeback=0` and `memory.min` **immediately after container start**
   via a watch-and-apply script — not via `setup-cgroups.sh` (which requires Soulmask to
   already be running).
4. Use **inverted phase order** (run 4 plan — see below): start the server *under*
   memory constraint, then relax upward.  Avoids repeated reclaim bursts; going up is
   always smooth (demand-driven refaults only).

### Run 4 plan — start constrained, relax upward

**Rationale:** relaxing memory.high upward causes zero burst — pages only return to RAM
when the game actually faults them in (`zswpin`).  The rate of `zswpin` at each 500 M
relaxation step is the direct answer to "how much does this tier of RAM benefit the game?"
If `zswpin` drops to near-zero at level X, the game has filled its natural demand; X is
the production `memory.high` ceiling.

**Setup (before Wings start):**
```bash
# Widen zswap pool so the full 9.7 G working set fits compressed at startup
sudo sh -c 'echo 50 > /sys/module/zswap/parameters/max_pool_percent'
```

**Constraint watcher — apply immediately after container cgroup is created:**
```bash
# Run this in a terminal before Wings start; it fires within 1 s of container creation
UUID="b87c0a5b-2387-4a1c-8863-ff23e6800a1d"
until SCOPE=$(find /sys/fs/cgroup/system.slice -name "*${UUID}*" -type d 2>/dev/null \
              | head -1) && [ -n "$SCOPE" ]; do sleep 0.2; done
echo "Scope: $SCOPE"
sudo bash -c "
  echo 2147483648 > $SCOPE/memory.high   # 2 G hard ceiling
  echo 1073741824 > $SCOPE/memory.min    # 1 G floor (OOM guard during startup)
  echo 0          > $SCOPE/memory.zswap.writeback
"
echo "Constraints applied"
```

**Wings start (in another terminal):**
```bash
WINGS_TOKEN=$(sudo awk '/^token:/{print $2}' /etc/pterodactyl/config.yml)
curl -sk -X POST -H "Authorization: Bearer $WINGS_TOKEN" \
  -H "Content-Type: application/json" -d '{"action":"start"}' \
  https://localhost:8080/api/servers/b87c0a5b-2387-4a1c-8863-ff23e6800a1d/power
```

**Wait for RCON** (server ready indicator):
```bash
until sudo /usr/local/sbin/exec-soulmask-rcon.sh -d List_OnlinePlayers &>/dev/null
do sleep 5; done && echo "Server ready"
```

**Start measurements (three parallel processes):**
```bash
# 1. DAMON snapshots
sudo python3 damon_cli.py timeseries-pid $PID \
  --duration 3600 --interval 5 \
  --min-regions 500 --max-regions 10000 \
  --hot-rate 15 --warm-rate 5 --cold-age 15 --idle-age 60 \
  --output-file output/soulmask_run4_<ts>.jsonl

# 2. Persistent RCON probe at 200 ms
sudo nsenter --net=/proc/$PID/ns/net \
  python3 rcon_probe.py --host 127.0.0.1 --port $PORT --password $PASS \
  --interval 0.2 --pid $PID --cgroup-scope $SCOPE \
  --output output/rcon_run4_<ts>.jsonl

# 3. zswap stats every 5 s (inline or from previous zswap collector script)
```

**Relaxation stages (500 M per step, 3–5 min each):**

| Stage | memory.high | memory.min | Expected RSS | Watch for |
|---|---|---|---|---|
| 0 (start) | 2 G | 1 G | ≤ 2 G | Server starts OK; zswap fills |
| 1 | 2.5 G | 2 G | ~2.5 G | zswpin burst, then quiet |
| 2 | 3 G | 2.5 G | ~3 G | RSS growth rate |
| 3 | 3.5 G | 3 G | ~3.5 G | |
| 4 | 4 G | 3.5 G | ~4 G | |
| 5 | 4.5 G | 4 G | ~4.5 G | |
| 6 | 5 G | 4.5 G | ~5 G | zswpin → 0? (natural demand floor) |
| 7 | 5.5 G | 5 G | ~5.5 G | |
| 8 | 6 G | 5.5 G | ~6 G | |
| 9 | max | 6 G | → 9.7 G | Refault storm, RSS recovers |

Step command (applied every 3–5 min by controller script):
```bash
# memory.min rises with memory.high — kernel cannot overshoot below new floor
sudo bash -c "echo $NEW_HIGH > $SCOPE/memory.high; echo $PREV_HIGH > $SCOPE/memory.min"
```

**Key metrics to watch per stage:**
- `zswpin` rate (from `/proc/vmstat` delta): demand for this 500 M tier
- `rtt_ms` from RCON probe: game-thread responsiveness (target < 50 ms)
- `minflt_rate` from DAMON JSONL: zswap decompression rate
- `sched_wait_ms` from DAMON JSONL: game thread scheduler stall time
- Player-reported feel: ping visible in ESC menu

## 7d. DAMON measurement — 2026-06-26 (upward relaxation experiment, 2 players)

### Run parameters

```
DAMON: --interval 10 --max-regions 40 --warm-rate 0.10 --hot-rate 0.01 --duration 7200
RCON probe: rcon_probe.py --interval 0.2 (persistent TCP, 200 ms)
Players: 2 (Lesandrina, Shakes)
Server started via Wings; pages already in zswap from prior run (warm start, not cold-disk)
Output: soulmask_run5_20260626_021811.jsonl / rcon_run5_20260626_021947.jsonl
```

**Goal:** start with memory.high below the natural working set, then relax upward in steps.
Observe how much RSS grows per step and when minflt_rate (zswap decompression demand) drops
to baseline — the point where the game's natural working set is fully in RAM.

### Cold-start constraint lesson (run 4, immediately prior)

Before this run, an attempt was made to start the server with `memory.high=2G` from a true
cold start (pages on disk, not in zswap):

| Attempt | Outcome |
|---|---|
| `memory.high=2G` during cold disk load | Server **crashed** ~10 min into startup |
| `memory.high=4G` mid-startup (raised to rescue) | Server recovered, RCON responded |

**Finding:** 2G is below the minimum needed to complete UE4 initialization from disk.
The loading sequence allocates large buffers, decompresses pak files, and initialises world
state — these operations cannot be satisfied through zswap alone during startup.

Post-startup, the same 2G ceiling was re-applied (warm start, pages already in zswap):
- RSS overshot to **135M** (kernel reclaimed far past the 2G target with `memory.min=0`)
- RCON did not respond at 135M — game threads were fully evicted to zswap

**Rule:** never apply a tight `memory.high` during cold-disk startup. Apply constraints only
after the server is fully initialised (RCON responds).

### Relaxation stages

Server started with `memory.high=4G` (first viable post-startup state). Players joined.
Each stage held until RSS was stable (delta < 150M over 5 consecutive 10s snapshots).

| Stage | memory.high | RSS settled | swap | minflt_rate | RCON p50 | RCON max | Player feel |
|---|---|---|---|---|---|---|---|
| Baseline | 4G | ~2.0G | ~7.8G | 3895–4050/s | 17ms | 157ms | hard lags, intermittent |
| +1G | 5G | ~3.1G | ~6.6G | 3300–10700/s | 18ms | 43ms | better |
| +1G | 6G | ~4.0G | ~5.9G | 3000–12000/s | 17ms | 59ms | pretty good |
| +1G | 7G | ~4.0G | ~5.8G | 3000–9900/s | 15ms | 839ms | really fine |

### Key finding — natural demand-driven working set

**RSS did not grow when memory.high was raised from 6G to 7G.**

With `memory.high=7G` and 2 active players, RSS stabilised at **~4.0G** — identical to the
6G level. The extra 1G of headroom was never used, because the game had no demand for those
pages during the observed play session.

This is the demand floor: pages only enter RAM through fault (when the game's code actually
reads or writes a virtual address currently in zswap). The remaining ~5.8G stays in zswap as
cold world data (unexplored areas, pak assets not currently needed).

```
memory.high = 7G   ← kernel ceiling (reclaim threshold)
RSS         = 4.0G ← actual demand (warm pages accessed since startup)
zswap       = 5.8G ← cold/idle data, accessible but untouched this session
headroom    = 3.0G ← unused; covers save bursts, new area loads, more players
```

### Why RSS < memory.high

`memory.high` is a **ceiling**, not a target. The kernel begins reclaiming pages from the
cgroup only when RSS *exceeds* it — it does not push pages into RAM. Pages enter RAM
exclusively through **demand faults**: when the game thread accesses a virtual address
currently in zswap, the CPU raises a minor page fault, the kernel decompresses that page
from the zswap pool, places it in physical RAM, and resumes the thread.

RSS therefore reflects what the game has actively touched since startup under this
constraint — not the ceiling's permission. With 2 players in a known area, the game
touched ~4G of its 9.7G virtual working set. Raising the ceiling beyond 4G does not
cause pages to refault proactively; only player exploration of new areas or world events
(saves, AI spawns, NPC pathfinding) would pull additional pages in.

### minflt_rate interpretation

`minflt_rate` from `/proc/<pid>/stat` field 10 counts **minor page faults per second**.
Each zswap decompression = 1 minor fault.

| State | minflt_rate | Interpretation |
|---|---|---|
| 2G ceiling (135M RSS) | 3895/s | nearly all accesses hitting zswap |
| 4G ceiling (2G RSS) | 3895–4050/s | constant zswap pressure — cause of lags |
| 6G ceiling (4G RSS) | 3000–12000/s | baseline ~3k/s; bursts when accessing new regions |
| 7G ceiling (4G RSS) | 3000–9900/s | same baseline; peak demand same but ceiling gives headroom |

The lag-free threshold was not sharply crossed at any single step — the game improved
gradually from 4G → 7G. The 7G state with baseline minflt ~3000/s represents the
residual decompression from pages that drift out of the active working set between accesses
(normal LRU churn), not gameplay-induced stalls.

### RCON RTT at steady state (7G, 2 players)

```
p50 = 15ms  p95 = 34ms  max = 839ms  failures = 0
```

The single 839ms spike is consistent with a periodic save event (world serialisation
briefly delays the game thread). No sustained latency elevation observed.

Note: RCON RTT probes the game's network/command thread, which tends to stay hot in RAM.
It is a valid indicator of game-thread availability but does not capture world-simulation
stalls (which affect physics/AI/player interaction). minflt_rate is a better proxy for
the stall source.

### What memory.min and memory.high actually guarantee

These two knobs are orthogonal:

| Knob | What it does | What it does NOT do |
|---|---|---|
| `memory.high` | triggers self-reclaim when RSS exceeds this ceiling | does not push pages into RAM; does not floor RSS |
| `memory.min` | protects against **external** reclaim — other cgroups (devcontainer, kernel) cannot steal pages below this floor | does not protect against the cgroup's own `memory.high` reclaim; does not pre-populate RAM |

Practical consequence: with `memory.min=4.5G` and `memory.high=7G`, the kernel guarantees
Soulmask keeps at least 4.5G in RAM even if the devcontainer or another workload is under
memory pressure.  Since the measured working set is ~4G, 4.5G gives ~500M buffer for save
bursts and working-set fluctuations — enough to keep pages hot without an RCON stall.

Pages only enter RAM through **demand faults** — when the game thread reads/writes a virtual
address currently in zswap, the kernel decompresses that page and places it in RAM.
`memory.min` protects those pages from being stolen back; it does not pro-actively populate RAM.

### Production recommendation (2-player baseline)

| Knob | Value | Rationale |
|---|---|---|
| `memory.high` | **max** | no artificial ceiling; Soulmask uses exactly what it needs |
| `memory.min` | **4608M** (4.5G) | measured 4G working set + 0.5G buffer; protects against external reclaim |
| `memory.zswap.writeback` | **0** | keeps cold pages in fast compressed pool, never evicted to disk |
| `memory.low` | 12G | best-effort; unchanged from prior calibration |

**`memory.high` is a pressure-test tool, not a production knob.**  On a host with ample
RAM, setting a ceiling gives no benefit — it only forces unnecessary zswap compression and
adds CPU overhead for decompression on every access.  `memory.high` is set only during
controlled experiments (§7e below) to deliberately induce memory pressure and find the
minimum viable working set.

Update `setup-cgroups.sh` `SOULMASK_MIN` once confirmed stable with 10+ players.
Expect the demand floor to grow ~0.5–1G per additional 10 players.

### Startup constraint rule

**Never apply `memory.high` during server startup.**

During cold-disk startup, UE4 needs >4G to load pak files, initialise world state, and
decompress assets.  Applying a ceiling before initialisation completes either crashes the
server (2G during cold start → crash within 10 min) or leaves it unresponsive (2G from
warm-zswap start → 135M RSS, RCON silent).

`memory.min` is safe to apply any time (it protects, not restricts), but in normal
production operation `memory.high=max` means there is nothing to apply anyway.

Correct startup sequence for pressure tests:
```
1. Start server with memory.high=max  (default; soulmask-cgroup-watcher handles this)
2. Wait until RCON responds: List_OnlinePlayers succeeds
3. THEN apply memory.high test ceiling
```

`soulmask-cgroup-watcher.service` enforces this automatically — it detects the container,
polls until RCON responds, then calls `setup-cgroups.sh` (which defaults to `memory.high=max`).

### Remaining unknowns

| Scenario | Expected effect |
|---|---|
| 10+ players online | RSS likely grows 4G → 5–6G; may need memory.high=8G |
| Large-area exploration | minflt burst as new world chunks load from zswap |
| Save event under load | CPU spike + possible RCON stall spike (1–2 s) |
| True production memory.min | Rerun with players, observe demand floor over 30+ min |

## 7e. Planned run 6 — tighten from unconstrained (find minimum viable ceiling)

**Goal:** find the lowest `memory.high` at which gameplay remains acceptable, without the
cold-start crash risk of run 4.  Start unlimited so the server fully populates its working
set, then apply descending pressure in small steps until players notice issues or zswap
shows sustained write activity.

**Why this direction (down) instead of run 5 (up):**
- Run 5 (constrained start → relax up): safe, but starting point (2–4G) made RCON
  unreliable, so the experiment started well above the constraint we wanted to test.
- Run 6 (unconstrained start → tighten down): server starts at full speed, we observe
  the exact threshold where quality degrades.  Tightening is also gradual (500M steps)
  so the kernel has time to compress pages incrementally — no reclaim burst.

### Setup

```bash
# 1. Start server via Wings with no memory.high (default = max)
WINGS_TOKEN=$(sudo awk '/^token:/{print $2}' /etc/pterodactyl/config.yml)
curl -sk -X POST -H "Authorization: Bearer $WINGS_TOKEN" \
  -H "Content-Type: application/json" -d '{"action":"start"}' \
  https://localhost:8080/api/servers/b87c0a5b-2387-4a1c-8863-ff23e6800a1d/power

# 2. Wait for steady state (RCON responds + RSS stable)
until sudo /usr/local/sbin/exec-soulmask-rcon.sh List_OnlinePlayers &>/dev/null
do sleep 10; done && echo "RCON ready"

# 3. Resolve PID and scope
PID=$(sudo docker top b87c0a5b-2387-4a1c-8863-ff23e6800a1d 2>/dev/null | awk '/WSServer/{print $2}' | head -1)
SCOPE=$(sudo awk -F: '/^0::/{print $3}' /proc/$PID/cgroup | xargs -I{} echo /sys/fs/cgroup{})
echo "PID=$PID  SCOPE=$SCOPE"

# 4. Confirm starting state (should be RSS≈9.7G, swap=0, memory.high=max)
awk '/VmRSS|VmSwap/{printf "%s %dM\n",$1,$2/1024}' /proc/$PID/status
sudo cat $SCOPE/memory.high
```

### Start measurements (three parallel terminals)

```bash
# Terminal A — DAMON snapshots
DIR=/home/vb/volkb79-2/vbpub/scripts/damon-analysis
TS=$(date +%Y%m%d_%H%M%S)
cd $DIR && sudo python3 damon_cli.py timeseries-pid $PID \
  --interval 10 --max-regions 40 --warm-rate 0.10 --hot-rate 0.01 \
  --duration 7200 --output-file output/soulmask_run6_${TS}.jsonl

# Terminal B — persistent RCON probe at 200 ms
PASS=$(sudo docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \
       $(docker ps -q | head -1) | sed -n 's/^RCON_PASSWORD=//p')
sudo nsenter --net=/proc/$PID/ns/net \
  python3 $DIR/rcon_probe.py \
  --host 127.0.0.1 --port 19000 --password "$PASS" \
  --interval 0.2 --pid $PID --cgroup-scope "$SCOPE" \
  --output $DIR/output/rcon_run6_${TS}.jsonl

# Terminal C — zswap pressure monitor (delta of /proc/vmstat per step)
watch -n 5 'grep -E "zswp(in|out)|zswap" /proc/vmstat'
```

### Players log in, let RSS stabilise, then tighten

After players are connected and RSS is stable (observe for 5+ min):

```bash
# Read initial zswap counters
grep -E "zswpin|zswpout" /proc/vmstat

# Apply first ceiling just below current RSS (round down to nearest 500M)
# e.g. if RSS=9.7G, start at 9G
sudo bash -c "echo 9663676416 > $SCOPE/memory.high"   # 9G
echo "$(date +%H:%M:%S)  memory.high=$(( $(sudo cat $SCOPE/memory.high) / 1073741824 ))G"
```

### Tightening steps (500M per step, ~3 min between)

| Step | memory.high | Bytes | Expected RSS | Watch for |
|---|---|---|---|---|
| start | max | — | ~9.7G | baseline; zswpout=0 |
| 1 | 9G | 9663676416 | ~9G | first zswpout activity |
| 2 | 8.5G | 9126805504 | ~8.5G | zswpout rate |
| 3 | 8G | 8589934592 | ~8G | minflt rises? |
| 4 | 7.5G | 8053063680 | ~7.5G | |
| 5 | 7G | 7516192768 | ~7G | |
| 6 | 6.5G | 6979321856 | ~6.5G | |
| 7 | 6G | 6442450944 | ~6G | sched_wait rises? |
| 8 | 5.5G | 5905580032 | ~5.5G | |
| 9 | 5G | 5368709120 | ~5G | player reports lag? |
| 10 | 4.5G | 4831838208 | ~4.5G | RCON RTT spikes? |
| 11 | 4G | 4294967296 | ~4G | confirmed lag threshold from run 5 |

Step command (no memory.min adjustment needed — we're not setting a floor during test):
```bash
sudo bash -c "echo $BYTES > $SCOPE/memory.high"
echo "$(date +%H:%M:%S)  high=$(( $(sudo cat $SCOPE/memory.high) / 1073741824 ))G  RSS=$(awk '/VmRSS/{print $2}' /proc/$PID/status | awk '{printf "%dM\n",$1/1024}')  zswpout=$(grep zswpout /proc/vmstat | awk '{print $2}')"
```

### Stop condition

Stop tightening when **any two** of:
1. `/proc/vmstat zswpout` increases by >1000 pages between 3-min samples (sustained swap-out)
2. RCON probe shows spikes >200ms or failure rate >5%
3. Player reports noticeable lag or rubberbanding

Record the step at which this occurs — that is the **minimum viable ceiling**.
The previous step (500M higher) is the safe production `memory.high` if a ceiling is desired.

### Key metrics per step

```bash
# Quick snapshot after each step (run once per step, wait 3 min first)
python3 - <<'EOF'
import json, statistics, time
RCON_FILE = "output/rcon_run6_<TS>.jsonl"   # update timestamp
lines = open(RCON_FILE).readlines()
cutoff = time.time() - 180   # last 3 min
rows = [json.loads(l) for l in lines if json.loads(l)['ts'] > cutoff]
ok = [r for r in rows if r['ok']]
rtts = [r['rtt_ms'] for r in ok]
s = sorted(rtts)
print(f"RCON: {len(ok)}/{len(rows)} OK  p50={statistics.median(rtts):.0f}ms  p95={s[int(len(s)*.95)]:.0f}ms  max={max(rtts):.0f}ms")
print(f"RSS={ok[-1]['rss_kb']//1024}M  swap={ok[-1]['swap_kb']//1024}M")
EOF
```

---

## 9. Manual observation commands

Quick reference for watching Soulmask live without starting a full DAMON run.

### 9.1 Resolve PID and cgroup scope

```bash
PID=$(sudo docker top b87c0a5b-2387-4a1c-8863-ff23e6800a1d 2>/dev/null \
      | awk '/WSServer/{print $2}' | head -1)
SCOPE=$(sudo awk -F: '/^0::/{print $3}' /proc/$PID/cgroup \
        | xargs -I{} echo /sys/fs/cgroup{})
echo "PID=$PID"
echo "SCOPE=$SCOPE"
```

### 9.2 RSS, swap, and cgroup limits at a glance

```bash
# Memory footprint
awk '/VmRSS|VmSwap|VmPeak/{printf "%-10s %dM\n",$1,$2/1024}' /proc/$PID/status

# Active cgroup limits
for k in memory.high memory.min memory.low memory.zswap.writeback; do
    v=$(sudo cat $SCOPE/$k 2>/dev/null)
    # Convert bytes to G for non-"max" values
    [[ "$v" =~ ^[0-9]+$ ]] && v="$v  ($(( v / 1073741824 ))G)" 
    echo "  $k = $v"
done
```

### 9.3 zswap pool (requires root — debugfs)

```bash
# Current pool: stored pages and compressed size
sudo bash -c '
  SP=$(cat /sys/kernel/debug/zswap/stored_pages)
  PS=$(cat /sys/kernel/debug/zswap/pool_total_size)
  UNCOMPRESSED=$(( SP * 4096 ))
  echo "stored pages:     $SP  ($(( UNCOMPRESSED / 1048576 ))M uncompressed)"
  echo "pool size:        $(( PS / 1048576 ))M compressed"
  echo "compression ratio: $(awk "BEGIN{printf \"%.2fx\", $UNCOMPRESSED/$PS}")"
'
```

### 9.4 zswap activity — pages moving in/out

```bash
# Cumulative counters — take two readings and diff
grep -E "zswpin|zswpout" /proc/vmstat

# Live delta every 10s (Ctrl-C to stop)
python3 -c "
import time
def read():
    d={}
    for l in open('/proc/vmstat'):
        if 'zswp' in l:
            k,v=l.split(); d[k]=int(v)
    return d
prev=read(); time.sleep(10)
while True:
    cur=read()
    now=time.strftime('%H:%M:%S')
    print(f'{now}  zswpin={cur[\"zswpin\"]-prev[\"zswpin\"]}/10s  zswpout={cur[\"zswpout\"]-prev[\"zswpout\"]}/10s')
    prev=cur; time.sleep(10)
"
```

**Interpretation:**
- `zswpout > 0` — kernel is compressing pages into zswap (pressure).  Occasional bursts
  (save events, joins) are normal; sustained high rates indicate the ceiling is too tight.
- `zswpin > 0` — game is reading pages back from zswap (demand faults).  Some is normal;
  high rates correlate with minflt spikes and player-perceived lag.

### 9.5 minflt rate (zswap decompression demand)

```bash
# Single reading: minor page faults/s over 5s
python3 -c "
import time
fields = open('/proc/$PID/stat').read().split()
f0 = int(fields[9]); t0 = time.time()
time.sleep(5)
fields = open('/proc/$PID/stat').read().split()
f1 = int(fields[9]); dt = time.time() - t0
print(f'minflt_rate: {int((f1-f0)/dt)}/s  (zswap decompressions per second)')
print('  < 2000/s = low pressure')
print('  2000–6000/s = moderate (occasional lag bursts possible)')
print('  > 6000/s = high pressure (sustained lag)')
"
```

### 9.6 RCON quick test and latency

```bash
# Connectivity check (also shows online players)
sudo /usr/local/sbin/exec-soulmask-rcon.sh List_OnlinePlayers

# Persistent latency probe (200 ms interval, Ctrl-C to stop, prints JSONL)
PASS=$(sudo docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \
       $(docker ps -q | head -1) | sed -n 's/^RCON_PASSWORD=//p')
sudo nsenter --net=/proc/$PID/ns/net \
  python3 /home/vb/volkb79-2/vbpub/scripts/damon-analysis/rcon_probe.py \
  --host 127.0.0.1 --port 19000 --password "$PASS" \
  --interval 0.2 --pid $PID --cgroup-scope "$SCOPE" \
  | python3 -c "
import json,sys,statistics,collections
window=collections.deque(maxlen=50)
for line in sys.stdin:
    d=json.loads(line); window.append(d)
    ok=[r for r in window if r['ok']]
    if ok:
        rtts=[r['rtt_ms'] for r in ok]
        print(f\"{d['elapsed']:.0f}s  rtt p50={statistics.median(rtts):.0f}ms max={max(rtts):.0f}ms  RSS={d['rss_kb']//1024}M\", flush=True)
"
```

**Interpretation:**
- p50 < 30ms, max < 200ms: healthy
- p50 30–100ms: game thread under pressure (zswap decompression competing for CPU)
- max > 500ms or failures: RCON listener evicted from RAM; serious pressure

### 9.7 Apply / read / clear memory.high

```bash
# Read current ceiling
sudo cat $SCOPE/memory.high   # "max" = unlimited; integer bytes otherwise

# Apply a ceiling (e.g. 7G = 7516192768 bytes)
sudo bash -c "echo 7516192768 > $SCOPE/memory.high"

# Remove ceiling (back to unlimited)
sudo bash -c "echo max > $SCOPE/memory.high"

# Common byte values:
#   4G  = 4294967296    5G  = 5368709120    6G  = 6442450944
#   7G  = 7516192768    8G  = 8589934592    9G  = 9663676416
#   8.5G = 9126805504   7.5G = 8053063680   6.5G = 6979321856
#   5.5G = 5905580032   4.5G = 4831838208   4608M = 4831838208
```

### 9.8 Combined live status (one-liner)

```bash
# Print a status line every 10s
while true; do
    RSS=$(awk '/VmRSS/{print $2}' /proc/$PID/status)
    SWP=$(awk '/VmSwap/{print $2}' /proc/$PID/status)
    MH=$(sudo cat $SCOPE/memory.high)
    [[ "$MH" =~ ^[0-9]+$ ]] && MH_LABEL="$(( MH/1073741824 ))G" || MH_LABEL="max"
    ZOUT=$(grep zswpout /proc/vmstat | awk '{print $2}')
    ZIN=$(grep zswpin  /proc/vmstat | awk '{print $2}')
    echo "$(date +%H:%M:%S)  RSS=$((RSS/1024))M  swap=$((SWP/1024))M  high=$MH_LABEL  zswpout=$ZOUT  zswpin=$ZIN"
    sleep 10
done
```

---

## 8. DAMON tuning guide — regions, thresholds, auto-tune, zswap push

### 8.1 Region count (`--min-regions` / `--max-regions`)

DAMON starts with `min_regions` regions and splits/merges them adaptively.
With `min_regions=10` on a 15 GiB VAS, each region averages **1.5 GiB** —
far too coarse to distinguish game subsystems.

```bash
# Recommended next run
sudo damon_cli.py timeseries-pid <pid> \
  --interval 5 --duration 1800 \
  --sample-us 100000 --aggr-us 2000000 \
  --min-regions 100 --max-regions 2000     # ← 10× finer, ~150 MiB buckets
```

Trade-off: more regions → more sysfs reads per collection and slightly more
DAMON CPU.  On a 8-core host monitoring one game server, 2000 max-regions is
negligible.

### 8.2 Hot/warm/cold thresholds

Our classifier uses `nr_accesses / (aggr_us / sample_us)`:

| Threshold | Value | Meaning at `aggr=2s, sample=100ms` (max_nr=20) |
|---|---|---|
| `--hot-rate 50` | 50% | ≥10 accesses/2 s = 5 Hz — almost nothing qualifies |
| `--warm-rate 5` | 5% | ≥1 access/2 s — everything qualifies, useless |
| `--cold-age 30` | 30 s | zero-rate for 30 s |
| `--idle-age 120` | 120 s | zero-rate for 120 s |

Because Soulmask scans broadly and nearly every 2 s window has at least one
touch anywhere in a 750 MiB region, everything folds into "warm".

**Better thresholds for Soulmask:**

```bash
  --hot-rate  30   # ≥6 accesses/2s (30% of max) — the tight loop pages
  --warm-rate 10   # ≥2 accesses/2s — genuinely active
  --cold-age   5   # zero for 5 s → cold; catches short-lived allocations
  --idle-age  60   # zero for 60 s → safe to compress aggressively
```

This splits the 15 GiB warm blob into meaningful tiers.

### 8.3 Proactive zswap compression via DAMOS `pageout`

DAMON can *act* on regions, not just observe.  The `pageout` action calls
`madvise(MADV_PAGEOUT)` on matched regions, moving them to swap (zswap first,
since `zswap.shrinker_enabled=Y` and Soulmask's `zswap.writeback=0`).

Goal: keep hot pages uncompressed in RAM; compress idle pages immediately.

```bash
# Scheme: pageout pages that are cold for 60s
damo start <pid> \
  --monitoring_intervals 100000 2000000 40000000 \
  --monitoring_nr_regions_range 100 2000 \
  --damos_action pageout \
  --damos_access_rate 0% 5% \
  --damos_sz_region 4096 max \
  --damos_age 60s max \
  --damos_max_nr_snapshots 10000
```

With `memory.zswap.writeback=0` on Soulmask's cgroup, the paged-out data goes
to zswap (compressed RAM) and **never hits disk**.  This effectively moves idle
pages from uncompressed RAM to a zstd-compressed pool, giving warm pages more
room to stay resident.

`lru_deprio` is a softer alternative — it hints to the kernel LRU to
deprioritise matched pages, letting natural reclaim compress them on pressure
rather than forcing it immediately.

### 8.4 DAMON auto-tuning (`--monitoring_intervals_autotune`)

damo v3.2.9 + kernel 7.0 support **automatic interval tuning**.  Instead of
hard-coding `sample_us` and `aggr_us`, DAMON tunes them continuously to
maintain a target:

```bash
damo start <pid> \
  --monitoring_intervals_autotune \   # goal: 4% sz_bp, 5ms–10s range
  --monitoring_nr_regions_range 100 2000 \
  ...
```

What it does:
- **Goal**: keep the "accessed" fraction of the monitored address space at ~4%
  of physical memory (the `intervals_goal/` sysfs knob, kernel 6.3+).
- **Mechanism**: if DAMON sees too much or too little activity, it raises/lowers
  `aggr_us` (between 5 ms and 10 s).  `sample_us` scales with it.
- **Effect during startup**: heavy activity → short intervals (fine-grained).
  Steady state → intervals relax (lower overhead).  Save burst → intervals
  tighten again automatically.

This replaces the manual `--aggr-us 2000000` guess with a self-calibrating
value, which matters most when the workload phase changes (startup → play →
save → idle).

The sysfs path written by autotune:
```
/sys/kernel/mm/damon/admin/kdamonds/0/contexts/0/monitoring_attrs/intervals/
├── sample_us          ← DAMON updates this
├── aggr_us            ← DAMON updates this
└── intervals_goal/
    ├── target_metric  = sz_bp
    ├── target_value   = 400        (4.00%)
    └── current_value  ← live reading
```

**Recommended next measurement command** (combines all improvements):

```bash
SOULMASK_PID=$(sudo docker top b87c0a5b-2387-4a1c-8863-ff23e6800a1d \
               | awk '/WSServer/{print $2}' | head -1)

sudo /home/vb/volkb79-2/vbpub/scripts/damon-analysis/venv/bin/python3 \
  /home/vb/volkb79-2/vbpub/scripts/damon-analysis/damon_cli.py \
  timeseries-pid $SOULMASK_PID \
  --duration 1800 --interval 5 \
  --min-regions 100 --max-regions 2000 \
  --hot-rate 30 --warm-rate 10 \
  --cold-age 5 --idle-age 60 \
  --output-file scripts/damon-analysis/output/soulmask_calibrated.jsonl
```

Run this with players online and during a save event for the most useful
profile.
