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
- [`soulmask-graceful-stop.service`](files/etc/systemd/system/soulmask-graceful-stop.service): `After=docker.service`, `Before=wings.service`. On shutdown systemd stops units in reverse start order ⇒ **wings → this (save+wait) → docker**. Wings is down first (won't auto-restart the container); the container is still up while we save it; then docker tears it down. `TimeoutStopSec=180` allows a large-world DB write.
- [`soulmask-shutdown.sh`](files/usr/local/sbin/soulmask-shutdown.sh) (its `ExecStop`): issues RCON `SaveAndExit 10`, waits for the container to exit, and falls back to `docker kill -s INT` (the same SIGINT the panel uses) if RCON is unavailable.

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

## 5. Quick command reference

| Purpose | RCON command |
|---|---|
| List players | `List_OnlinePlayers` (alias `lp`) |
| Save only (no exit) | `SaveWorld 0` |
| Save + shutdown countdown | `SaveAndExit <seconds>` |
| Cancel a pending shutdown | `StopCloseServer` |
| Broadcast a message | `broadcast <text>` |
| Plain shutdown countdown | `shutdown <seconds>` |

Full list: <https://saraserenity.net/soulmask/remote_console.php>
