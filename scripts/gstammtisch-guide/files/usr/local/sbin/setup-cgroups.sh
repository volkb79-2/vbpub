#!/usr/bin/env bash
# Apply cgroup-v2 knobs that systemd slice units can't express directly:
#   - dev-workloads.slice : memory.zswap.writeback=1 (let dev pages drain to disk)
#   - Soulmask container   : memory.min / memory.low / memory.high protection, and
#                            memory.zswap.writeback=0 (keep its pages in the fast
#                            compressed pool, never proactively to disk).
# Idempotent; tolerant if Soulmask isn't running yet.
#
# IMPORTANT: set SOULMASK_MIN to Soulmask's MEASURED hot+warm working set (DAMON),
# not a guess. Too low => the game faults pages back under pressure => stutter.
#
# IMPORTANT: never call this during server startup. Apply only after RCON responds.
# See soulmask-cgroup-watcher.service which enforces this automatically.
set -uo pipefail
CG=/sys/fs/cgroup
log(){ echo "[cgroups] $*"; }

SOULMASK_MIN="${SOULMASK_MIN:-4608M}" # calibrated 2026-06-26: demand floor ~4G (2 players, run 5); +0.5G burst buffer (4608M = 4.5G)
SOULMASK_LOW="${SOULMASK_LOW:-12G}"
SOULMASK_HIGH="${SOULMASK_HIGH:-7G}"  # calibrated 2026-06-26: 3G headroom above 4G working set; raise for 10+ players

# --- dev workloads: allow zswap pages to be written back to disk ---
DEV="$CG/dev-workloads.slice"
if [ -d "$DEV" ] && [ -w "$DEV/memory.zswap.writeback" ]; then
  echo 1 > "$DEV/memory.zswap.writeback" && log "dev-workloads.slice memory.zswap.writeback=1"
else
  log "dev-workloads.slice not present yet (start it / launch a dev container first)"
fi

# --- locate the Soulmask container by its running process ---
CID=""
for c in $(docker ps -q 2>/dev/null); do
  if docker top "$c" 2>/dev/null | grep -q 'WSServer-Linux-Shipping'; then CID="$c"; break; fi
done
if [ -z "$CID" ]; then
  log "Soulmask container not running; skipping its cgroup knobs."
  exit 0
fi

# resolve its unified cgroup path (docker scope) two ways
PID=$(docker inspect -f '{{.State.Pid}}' "$CID" 2>/dev/null || true)
SCOPE=""
[ -n "$PID" ] && SCOPE="$CG$(awk -F: '/^0::/{print $3}' /proc/$PID/cgroup 2>/dev/null)"
[ -d "$SCOPE" ] || SCOPE=$(find "$CG" -type d -name "*${CID}*" 2>/dev/null | head -n1)
if [ -z "$SCOPE" ] || [ ! -d "$SCOPE" ]; then
  log "could not resolve Soulmask cgroup (cgroup v2 unified?); skipping."
  exit 0
fi

log "Soulmask cgroup: $SCOPE"
echo "$SOULMASK_HIGH" > "$SCOPE/memory.high"            2>/dev/null && log "memory.high=$SOULMASK_HIGH"
echo "$SOULMASK_LOW"  > "$SCOPE/memory.low"             2>/dev/null && log "memory.low=$SOULMASK_LOW"
echo "$SOULMASK_MIN"  > "$SCOPE/memory.min"             2>/dev/null && log "memory.min=$SOULMASK_MIN"
echo 0                > "$SCOPE/memory.zswap.writeback" 2>/dev/null && log "memory.zswap.writeback=0 (keep pages in pool)"
