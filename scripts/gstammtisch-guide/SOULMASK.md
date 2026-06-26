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
3. **dev work is fenced off** in `dev-workloads.slice` (low weight, capped, OOM-first). See [MEMORY-ARCHITECTURE.md §5](MEMORY-ARCHITECTURE.md).

> Set `memory.min` from real measurement and err **high** — a too-low floor lets the game fault pages back during save/join/AI bursts → visible stutter.

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
exec-soulmask-rcon.sh SaveWorld 0                 # save WITHOUT exiting (good for a cron save)
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
| Save only (no exit) | `SaveWorld 0` |
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
| `memory.high` | **7G** | covers natural 4G demand + 3G headroom for bursts and more players |
| `memory.min` | **4.5G** | measured 4G working set + 500M buffer; protects against external reclaim |
| `memory.zswap.writeback` | **0** | keeps cold pages in fast compressed pool, never evicted to disk |
| `memory.low` | 12G | best-effort; unchanged from prior calibration |

Update `setup-cgroups.sh` `SOULMASK_MIN=4.5G` once confirmed stable with 10+ players.
Expect `SOULMASK_MIN` to increase ~0.5–1G per additional 10 players as hot+warm expands.

### Startup constraint rule

**Never apply `memory.high` or `memory.min` during server startup.**

During cold-disk startup, UE4 needs >4G to load pak files, initialise world state, and
decompress assets.  Applying a ceiling before initialisation completes either crashes the
server (2G during cold start → crash within 10 min) or leaves it unresponsive (2G from
warm-zswap start → 135M RSS, RCON silent).

Correct startup sequence:
```
1. Start server with memory.high=max, memory.min=0  (no constraints)
2. Wait until RCON responds: List_OnlinePlayers succeeds
3. THEN apply memory.high + memory.min
```

The setup-cgroups.sh script already handles this correctly (it is called after Wings start,
which implies the server is running).  The bug in run 4 was applying constraints via the
watcher script at container-creation time, before RCON was ready.

### Remaining unknowns

| Scenario | Expected effect |
|---|---|
| 10+ players online | RSS likely grows 4G → 5–6G; may need memory.high=8G |
| Large-area exploration | minflt burst as new world chunks load from zswap |
| Save event under load | CPU spike + possible RCON stall spike (1–2 s) |
| True production memory.min | Rerun with players, observe demand floor over 30+ min |

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
