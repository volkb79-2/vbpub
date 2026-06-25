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
# 64 snapshots captured before analysis (12s–406s); server started via Wings API
```

### Startup phases

| Phase | t | hot | warm | cold | idle | CPU% | Note |
|---|---|---|---|---|---|---|---|
| Init | 12–31s | 22–43 MiB | 195–467 MiB | 2175–2467 MiB | 0 | 100% | loading engine, 156–340 regions |
| World load burst | 37–125s | 0–25 MiB | 0.7–2.6 GiB | 1.5–9.8 GiB | 0–691 MiB | **100–161%** | RSS grows 1→10 GiB; multi-core streaming |
| Settling | 131–287s | 0–73 MiB | 5.7–9.0 GiB | 4.3–7.9 GiB | 0.7–2.5 GiB | 80–130% | world resident, warm expanding |

CPU peaked at **160 %** — Soulmask uses multiple cores during the initial world
load, not just the single-core game loop.

### Steady-state snapshot (t > 287 s, no players)

| Class | Median | Max | Min | Interpretation |
|---|---|---|---|---|
| **hot** | **19 MiB** | 22 MiB | 13 MiB | tight game-loop pages (netcode, physics tick) |
| **warm** | **5.1 GiB** | 6.4 GiB | 4.4 GiB | actively used — must stay in RAM |
| cold | 6.5 GiB | 7.4 GiB | 5.5 GiB | accessed <10% /2 s or last 5–60 s — safe for zswap |
| idle | 2.7 GiB | 3.2 GiB | 2.1 GiB | not accessed for >60 s — prime zswap candidates |
| RSS | 9.7 GiB | — | — | physical RAM (no swap — `memory.min=10G` was holding) |
| VmSwap | **0** | — | — | no disk swap at all with the previous `memory.min=10G` |
| CPU% | 67 % | 81 % | 63 % | single-threaded game tick at steady state |

**Key insight:** the first run's `warm = 12–15 GiB` was an artefact of coarse
1.5 GiB/region buckets + `warm_rate = 5 %`.  A single page access anywhere in
a 1.5 GiB region every 2 s made the whole block "warm".  With 100-region
granularity (~100 MiB buckets) and `warm_rate = 10 %`, the true warm footprint
is **5.1 GiB** — the rest is cold/idle and compressible.

### `SOULMASK_MIN` calibrated value

```
hot+warm median: 5.1 GiB
hot+warm max:    6.4 GiB  (settling phase, player joins expected to be similar)
safety margin:   + 600 MiB (covers save bursts, player joins — not yet measured)
─────────────────────────
SOULMASK_MIN = 7G
```

Applied 2026-06-26.  Cold (6.5 GiB) + idle (2.7 GiB) = **9.2 GiB** that the
kernel is now free to compress into zswap or reclaim under pressure, recovering
that RAM for dev containers.  With `memory.zswap.writeback = 0`, they stay in
the fast compressed pool and do not hit disk.

### Remaining unknowns

| Scenario | Expected effect | When to measure |
|---|---|---|
| Players online (10–30) | warm may grow 1–2 GiB; hot spikes | during a play session |
| Save event (`-saving=600`) | brief cold → warm flip, IO burst | capture across a save |
| Map DLC vs base map | DLC has more assets → larger warm | compare maps |

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
