# Soulmask startup — console markers and phase timeline

Measured from a real start of the production instance
(`b87c0a5b-2387-4a1c-8863-ff23e6800a1d`, DLC_Level01, 0 players) on
2026-07-20, offsets from container start `04:40:11`. These are the lines that
appear on the **container console stream** (docker stdout) — i.e. the only
lines Wings' matchers (`startup.done`, `WINGS_CG_STEADY_MATCH`) can see. Game
log files under `WS/Saved/Logs/` are a superset but are not on this stream.

Used by:
- the cgroups startup band (`wings-cgroups`, patch 0007+): which line ends the
  startup phase and starts the `memory.high` squeeze.
- the Panel activity events emitted at each phase.

## Timeline

| +time | console line (substring) | meaning |
|---|---|---|
| +0:02 | `Checking for available updates...` | steam self-update starts |
| +0:07 | `Success! App '3017300' already up to date.` | steam update done (nothing to download — 5s) |
| +0:07 | `WSServer-Linux-Shipping … -server …` | game process launched |
| +0:08 | `LogOnline: STEAM: … Game Server API initialized` | steam game-server API up |
| +0:43 | `LogLoad: LoadMap: /Game/…/DLC_Level01_Main` | **world load begins** |
| +1:09 | `LogLoad: Game class is 'BP_GameModeBase_DLC_C'` | game mode loaded |
| +1:20 | `LogWorld: Bringing World … up for play` | world coming up |
| +1:20 | `logServerSupervise: Listening on FServerListener` | local control (RCON/supervise) listening |
| +1:31 | `LogWorld: Bringing up level for play took: 31.13` | level up |
| +1:36 | `LogLoad: (Engine Initialization) Total time: 88.33 seconds` | **engine init complete** |
| +1:36 | `LogLoad: Took 52.74 seconds to LoadMap(…)` | map fully loaded |
| +2:50 | `LogOnline: STEAM: …SteamServerConnectedGS` | connected to Steam backend |
| **+4:18** | `logSoulmaskSession: [SERVER_LIST] registe server … succeed.` | **registered / joinable** |
| +4:35 | `LogWS: Create Dungeon Successed: …` (first) | first procedural dungeon built |
| +22:00 | `LogGameMode: … WaitingToStart to InProgress` | first player joined; match live |

## Three facts that matter for keying off these

1. **The memory load-burst is ~+0:43 → +2:05** (`LoadMap` → engine init; RSS
   grows ~1→10 GiB per the DAMON trace in `SOULMASK.md`). It is essentially
   **over ~2.5 minutes before registration (+4:18)**. So a `memory.high`
   squeeze keyed to registration or to `Create Dungeon Successed` starts well
   after the peak — the cold tail has settled, which is what we want.

2. **`[SERVER_LIST] … registe server … succeed` is a 2-minute heartbeat**, not
   a one-shot. The first occurrence (+4:18) is the real "now listed" event; it
   then repeats every ~120 s forever. Fine as a first-match trigger.

3. **`Create Dungeon Successed` also recurs** — once per dungeon during world
   generation (~10× between +4:35 and +5:39 in this run), then stops. Not a
   forever-heartbeat like registration, but not unique either. First match is
   what a trigger acts on, so recurrence is harmless.

`Create Dungeon Successed` is the egg's existing `startup.done` and is the
**chosen squeeze trigger** — it fires ~17 s after registration, and firing a
little late is safe (the `high=7G` ceiling only proactively frees memory the
game does not need; if the game is still warm, system pressure reclaims the
genuinely-cold pages first).

## Matchers (for the egg / config)

`WINGS_CG_STEADY_MATCH` — ends the startup band, starts the `high` squeeze.
Empty = fall back to the egg's `startup.done`. For Soulmask, `startup.done`
(`Create Dungeon Successed`) is the intended trigger, so leaving it empty is
correct. The registration alternative, if ever preferred:

```
regex:\[SERVER_LIST\] registe server .* succeed
```

## Activity events (curated, cgroups-relevant)

Emitted to the Panel activity log (`SendActivityLogs`) — not a status badge
(Wings has only offline/starting/running/stopping), a persisted activity row:

| event | keyed on | note |
|---|---|---|
| `steam-update-started` | `Checking for available updates` | always present |
| `steam-update-done` | `Success! App '3017300' (already up to date\|fully installed)` | **both variants** — see below |
| `world-load-begin` | `LogLoad: LoadMap:` | start of the memory burst |
| `steady-reached` | the `WINGS_CG_STEADY_MATCH` trigger | steady band applied |

## steamcmd update vocabulary — VERIFY on a patch day

This run was already up to date, so only the `already up to date` line was
captured. On an actual update steamcmd prints different lines. The following
are steamcmd's documented format and should be **confirmed against a real
patch-day console capture** before being relied on:

```
[----] Verifying installation...
 Update state (0x5) verifying install, progress: 42.13 (…)
 Update state (0x61) downloading, progress: 12.34 (… / …)
 Update state (0x101) committing, progress: …
Success! App '3017300' fully installed.
```

So a robust "update finished" match is `already up to date` OR `fully
installed`; a "patch downloading" informational event (absent when
up-to-date) is `Update state (0x…) downloading`.
