#!/usr/bin/env bash
# Soulmask 3-phase startup cgroup lifecycle.
#
# Phase 1 — Startup (memory.min=9G, memory.high=max):
#   The game loads its world, actor graph, and assets without zswap pressure.
#   Without a high floor, early-loaded pages get pushed to zswap while loading
#   is still in progress → constant refault pressure → slow startup.
#
# Phase 2 — Squeeze (step memory.high 9G → 5G, 100M per 0.5s):
#   After RCON confirms the server is ready for players, gradually lower
#   memory.high in small steps. Each step compresses ~25K pages to zswap,
#   completing in ~50ms — imperceptible vs a single 4G burst. This primes
#   zswap with the game's cold data without causing a visible lag spike.
#   Panic floor is set to 3G during the squeeze so memory.min < memory.high.
#
# Phase 3 — Steady state (memory.high=max, memory.min=6G):
#   Release the ceiling. The game reclaims its hot set from zswap (~10s burst,
#   then settles). Set memory.min=6G (calibrated 2026-06-27, 3 players) so the
#   kernel never pushes the hot set below 6G regardless of other tenant pressure.
#
# Call this once after each Wings start/restart of the Soulmask container.
# Can be run as a systemd service or manually after a Wings power cycle.
#
# Environment overrides:
#   SOULMASK_STARTUP_MIN    default 9G
#   SOULMASK_SQUEEZE_TARGET default 5G
#   SOULMASK_SQUEEZE_FLOOR  default 3G   (panic floor during squeeze)
#   SOULMASK_SQUEEZE_STEP   default 100  (MB per step)
#   SOULMASK_SQUEEZE_DELAY  default 0.5  (seconds between steps)
#   SOULMASK_SETTLE_TIME    default 30   (seconds to wait after squeeze before Phase 3)
#   SOULMASK_STEADY_MIN     default 6G
#   SOULMASK_RCON_TIMEOUT   default 600  (max seconds to wait for RCON ready)
set -euo pipefail

STARTUP_MIN="${SOULMASK_STARTUP_MIN:-9G}"
SQUEEZE_TARGET="${SOULMASK_SQUEEZE_TARGET:-5G}"
SQUEEZE_FLOOR="${SOULMASK_SQUEEZE_FLOOR:-3G}"
SQUEEZE_STEP_MB="${SOULMASK_SQUEEZE_STEP:-100}"
SQUEEZE_DELAY="${SOULMASK_SQUEEZE_DELAY:-0.5}"
SETTLE_TIME="${SOULMASK_SETTLE_TIME:-30}"
STEADY_MIN="${SOULMASK_STEADY_MIN:-6G}"
RCON_TIMEOUT="${SOULMASK_RCON_TIMEOUT:-600}"
RCON_POLL=10

log() { echo "[startup-cgroup $(date +%H:%M:%S)] $*"; }

_parse_bytes() {
  local v="$1"
  case "$v" in
    *G) echo $(( ${v%G} * 1073741824 )) ;;
    *M) echo $(( ${v%M} * 1048576 )) ;;
    *)  echo "$v" ;;
  esac
}

_cg() {
  local pid cid
  for cid in $(docker ps -q 2>/dev/null); do
    docker top "$cid" 2>/dev/null | grep -q WSServer || continue
    pid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null)
    echo "/sys/fs/cgroup$(awk -F: '/^0::/{print $3}' /proc/$pid/cgroup 2>/dev/null)"
    return
  done
}

# --- wait for Soulmask container ---
log "waiting for Soulmask container (max 5 min)..."
CG=""
for _ in $(seq 1 60); do
  CG=$(_cg 2>/dev/null) && [ -d "$CG" ] && break
  CG=""; sleep 5
done
[ -d "${CG:-}" ] || { log "ERROR: Soulmask container not found after 5 min"; exit 1; }
log "cgroup: $CG"

# ─── Phase 1: startup floor ───────────────────────────────────────────────────
STARTUP_BYTES=$(_parse_bytes "$STARTUP_MIN")
log "Phase 1: memory.min → $STARTUP_MIN  memory.high → max"
echo "$STARTUP_BYTES" > "$CG/memory.min"
echo max > "$CG/memory.high"
log "  game loading — $(( $(cat "$CG/memory.current") / 1048576 ))M in RAM"

# ─── wait for RCON ready ──────────────────────────────────────────────────────
log "waiting for RCON (max ${RCON_TIMEOUT}s)..."
waited=0
while true; do
  # exec-soulmask-rcon.sh with no args = connection test only, exits 0 on success
  if exec-soulmask-rcon.sh > /dev/null 2>&1; then
    log "RCON ready — server accepting connections"
    break
  fi
  if [ "$waited" -ge "$RCON_TIMEOUT" ]; then
    log "WARN: RCON not ready after ${RCON_TIMEOUT}s — proceeding anyway"
    break
  fi
  sleep "$RCON_POLL"
  waited=$(( waited + RCON_POLL ))
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
log "Phase 3: memory.high → max  memory.min → $STEADY_MIN"
echo max > "$CG/memory.high"
echo "$STEADY_BYTES" > "$CG/memory.min"

sleep 5  # brief pause for page reclaim to begin before logging
log "lifecycle complete:"
log "  memory.min = $STEADY_MIN (hot set guaranteed in uncompressed RAM)"
log "  memory.high = max (no artificial ceiling)"
log "  RAM now: $(( $(cat "$CG/memory.current")/1048576 ))M"
log "  zswap: $(( $(cat "$CG/memory.swap.current")/1048576 ))M uncompressed / $(( $(cat "$CG/memory.zswap.current")/1048576 ))M compressed"
log "  monitor: soulmask-zswap-monitor.sh"
