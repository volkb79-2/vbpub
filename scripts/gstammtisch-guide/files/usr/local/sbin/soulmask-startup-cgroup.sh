#!/usr/bin/env bash
# Soulmask 3-phase startup cgroup lifecycle.
#
# Phase 1 — Startup (memory.min=9G, memory.high=max):
#   The game loads its world, actor graph, and assets without zswap pressure.
#   Without a high floor, early-loaded pages get pushed to zswap while loading
#   is still in progress → constant refault pressure → slow startup.
#
# Phase 2 — Squeeze (step memory.high 9G → 6G, 100M per 0.5s):
#   After the game log shows Steam server-list registration ("[SERVER_LIST]
#   registe server ... succeed." — the real ready signal; RCON responds long
#   before the server is actually ready), gradually lower
#   memory.high in small steps. Each step compresses ~25K pages to zswap,
#   completing in ~50ms — imperceptible vs a single 4G burst. This primes
#   zswap with the game's cold data without causing a visible lag spike.
#   Panic floor is set to 3G during the squeeze so memory.min < memory.high.
#
# Phase 3 — Steady state (memory.high=7G, memory.min=6G):
#   Production band: 6G floor + 7G ceiling. The game reclaims its hot set from
#   zswap (short burst, then settles within the band). setup-cgroups.sh (via the
#   cgroup watcher) also applies these values after RCON — startup-cgroup.sh is
#   the fast path for a clean controlled priming; the watcher is the fallback.
#
# Call this once after each Wings start/restart of the Soulmask container.
# Can be run as a systemd service or manually after a Wings power cycle.
#
# N-instance (2026-07-07): pass -c <uuid-or-prefix> to select which running
# Soulmask instance this lifecycle applies to (matches the docker container
# name, which IS the Pterodactyl server UUID). With exactly one instance
# running, -c is optional — it's auto-selected. With several, -c is
# required (the script lists candidates and exits if omitted).
#   soulmask-startup-cgroup.sh -c b87c0a5b
#
# Environment overrides:
#   SOULMASK_STARTUP_MIN    default 9G
#   SOULMASK_SQUEEZE_TARGET default 6G
#   SOULMASK_SQUEEZE_FLOOR  default 3G   (panic floor during squeeze)
#   SOULMASK_SQUEEZE_STEP   default 100  (MB per step)
#   SOULMASK_SQUEEZE_DELAY  default 0.5  (seconds between steps)
#   SOULMASK_SETTLE_TIME    default 30   (seconds to wait after squeeze before Phase 3)
#   SOULMASK_STEADY_MIN     default 6G
#   SOULMASK_STEADY_HIGH    default 7G
#   SOULMASK_READY_TIMEOUT  default 600  (max seconds to wait for server-ready log line)
set -euo pipefail

# shellcheck source=/usr/local/sbin/soulmask-instance-lib.sh
LIB="${LIB:-/usr/local/sbin/soulmask-instance-lib.sh}"
if [ -f "$LIB" ]; then
  . "$LIB"
else
  echo "FATAL: $LIB not found (expected alongside this script)"; exit 1
fi

SEL=""
if [ "${1:-}" = "-c" ]; then SEL="${2:-}"; shift 2; fi

STARTUP_MIN="${SOULMASK_STARTUP_MIN:-9G}"
SQUEEZE_TARGET="${SOULMASK_SQUEEZE_TARGET:-6G}"
SQUEEZE_FLOOR="${SOULMASK_SQUEEZE_FLOOR:-3G}"
SQUEEZE_STEP_MB="${SOULMASK_SQUEEZE_STEP:-100}"
SQUEEZE_DELAY="${SOULMASK_SQUEEZE_DELAY:-0.5}"
SETTLE_TIME="${SOULMASK_SETTLE_TIME:-30}"
STEADY_MIN="${SOULMASK_STEADY_MIN:-6G}"
STEADY_HIGH="${SOULMASK_STEADY_HIGH:-7G}"
READY_TIMEOUT="${SOULMASK_READY_TIMEOUT:-${SOULMASK_RCON_TIMEOUT:-600}}"
READY_POLL=10
READY_RE='SERVER_LIST.*registe server.*succeed'

log() { echo "[startup-cgroup $(date +%H:%M:%S)] $*"; }

_parse_bytes() {
  local v="$1"
  case "$v" in
    *G) echo $(( ${v%G} * 1073741824 )) ;;
    *M) echo $(( ${v%M} * 1048576 )) ;;
    *)  echo "$v" ;;
  esac
}

_find_game() {
  local cid
  cid=$(soulmask_select_instance "$SEL" 2>/tmp/soulmask-startup-select-err) || return 1
  GAME_CID="$cid"
  CG=$(soulmask_cgroup_of "$cid") || return 1
  return 0
}

# --- wait for Soulmask container ---
log "waiting for Soulmask container (max 5 min)..."
CG=""; GAME_CID=""
for _ in $(seq 1 60); do
  _find_game && [ -d "$CG" ] && break
  CG=""; sleep 5
done
if [ ! -d "${CG:-}" ]; then
  log "ERROR: could not resolve a Soulmask instance after 5 min"
  cat /tmp/soulmask-startup-select-err >&2 2>/dev/null || true
  exit 1
fi
log "cgroup: $CG (container $GAME_CID, instance $(soulmask_uuid_of "$GAME_CID"))"

# ─── Phase 1: startup floor ───────────────────────────────────────────────────
STARTUP_BYTES=$(_parse_bytes "$STARTUP_MIN")
log "Phase 1: memory.min → $STARTUP_MIN  memory.high → max"
echo "$STARTUP_BYTES" > "$CG/memory.min"
echo max > "$CG/memory.high"
log "  game loading — $(( $(cat "$CG/memory.current") / 1048576 ))M in RAM"

# ─── wait for server-ready (Steam server-list registration in game log) ──────
# RCON responding is NOT a readiness signal — the RCON thread answers long before
# the world is loaded (and stays responsive even when the game thread stalls).
log "waiting for server-list registration in game log (max ${READY_TIMEOUT}s)..."
waited=0
while true; do
  # grep -c not -q/-m1: early-exit grep + pipefail misreads a matched line as
  # "not ready" when >~64KB of log follows it (opus review F1).
  if docker logs "$GAME_CID" 2>&1 | grep -c -- "$READY_RE" >/dev/null; then
    log "server registered on server list — ready for players"
    break
  fi
  if [ "$waited" -ge "$READY_TIMEOUT" ]; then
    log "WARN: no registration line after ${READY_TIMEOUT}s — proceeding anyway"
    break
  fi
  sleep "$READY_POLL"
  waited=$(( waited + READY_POLL ))
  cur=$(( $(cat "$CG/memory.current") / 1048576 ))
  log "  ${waited}s: ${cur}M in RAM, still loading..."
done

# ─── Phase 2: gradual squeeze ─────────────────────────────────────────────────
SQUEEZE_BYTES=$(_parse_bytes "$SQUEEZE_TARGET")
SQUEEZE_FLOOR_BYTES=$(_parse_bytes "$SQUEEZE_FLOOR")
STEP_BYTES=$(( SQUEEZE_STEP_MB * 1048576 ))

log "Phase 2: setting panic floor → $SQUEEZE_FLOOR (allows stepping below startup floor)"
echo "$SQUEEZE_FLOOR_BYTES" > "$CG/memory.min"

cur=$(cat "$CG/memory.current")
log "Phase 2: squeeze memory.high: $(( cur/1048576 ))M → $SQUEEZE_TARGET  (${SQUEEZE_STEP_MB}M / ${SQUEEZE_DELAY}s)"
echo "$cur" > "$CG/memory.high"

while true; do
  high=$(cat "$CG/memory.high")
  [ "$high" = "max" ] && high=$(cat "$CG/memory.current")
  aligned=$(( (high / STEP_BYTES) * STEP_BYTES ))
  next=$(( aligned - STEP_BYTES ))
  [ "$next" -le "$SQUEEZE_BYTES" ] && next="$SQUEEZE_BYTES"
  echo "$next" > "$CG/memory.high"
  refault=$(grep '^workingset_refault_anon' "$CG/memory.stat" | awk '{print $2}')
  log "  memory.high → $(( next/1048576 ))M  RAM=$(( $(cat "$CG/memory.current")/1048576 ))M  refault_cumul=$refault"
  [ "$next" -le "$SQUEEZE_BYTES" ] && break
  sleep "$SQUEEZE_DELAY"
done
log "squeeze complete: $(( $(cat "$CG/memory.swap.current")/1048576 ))M now in zswap"

# ─── settle ───────────────────────────────────────────────────────────────────
if [ "$SETTLE_TIME" -gt 0 ]; then
  log "Phase 2b: settling ${SETTLE_TIME}s before releasing ceiling..."
  sleep "$SETTLE_TIME"
fi

# ─── Phase 3: steady state ────────────────────────────────────────────────────
STEADY_BYTES=$(_parse_bytes "$STEADY_MIN")
STEADY_HIGH_BYTES=$(_parse_bytes "$STEADY_HIGH")
log "Phase 3: memory.min → $STEADY_MIN  memory.high → $STEADY_HIGH  (production band)"
echo "$STEADY_BYTES"      > "$CG/memory.min"
echo "$STEADY_HIGH_BYTES" > "$CG/memory.high"

sleep 5  # brief pause for page reclaim to begin before logging
log "lifecycle complete:"
log "  memory.min = $STEADY_MIN (floor: guaranteed uncompressed RAM)"
log "  memory.high = $STEADY_HIGH (ceiling: production band cap)"
log "  RAM now: $(( $(cat "$CG/memory.current")/1048576 ))M"
log "  zswap: $(( $(cat "$CG/memory.swap.current")/1048576 ))M uncompressed / $(( $(cat "$CG/memory.zswap.current")/1048576 ))M compressed"
log "  monitor: soulmask-zswap-monitor.sh"
