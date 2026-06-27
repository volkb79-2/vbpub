#!/usr/bin/env bash
# Apply cgroup-v2 knobs that systemd slice units can't express directly:
#   - dev-workloads.slice  : memory.zswap.writeback=1
#   - Soulmask container   : memory.min/low/high protection, zswap writeback=0,
#                            io.weight=4950 + io.bfq.weight=1000 (BFQ priority), cpu.weight=800
#   - bench containers     : io.weight=1, io.bfq.weight=1, io.max hard IOPS/bandwidth cap
#                            (devcontainer runs docker-repack; test-runner runs benchmarks)
# Idempotent; tolerant if containers aren't running yet.
#
# Requires BFQ scheduler on vda (/etc/modules-load.d/bfq.conf + udev rule).
# IMPORTANT: never call this during Soulmask startup. Apply only after RCON responds.
# See soulmask-cgroup-watcher.service which enforces this automatically.
set -uo pipefail
CG=/sys/fs/cgroup
log(){ echo "[cgroups] $*"; }

# I/O cap for bench containers: 30 MB/s, 100 r-IOPS, 400 w-IOPS.
# io.bfq.weight range is 1–1000 (unlike io.weight which is 1–10000).
#
# With the pak ramdisk active (soulmask-pak-ramdisk.service), pak pages are
# tmpfs-backed anon — they go through zswap, not page cache, so bench file I/O
# cannot evict them. The io.max cap here limits benchmark impact on Soulmask's
# periodic DB saves (which DO go through the real disk via writeback threads).
# No memory.high on bench: the devcontainer is also VSCode — capping total
# cgroup memory kills the IDE. BFQ io.weight + io.max is the right lever.
BENCH_RBPS="${BENCH_RBPS:-31457280}"
BENCH_WBPS="${BENCH_WBPS:-31457280}"
BENCH_RIOPS="${BENCH_RIOPS:-100}"
BENCH_WIOPS="${BENCH_WIOPS:-400}"

SOULMASK_MIN="${SOULMASK_MIN:-4608M}" # calibrated 2026-06-26: demand floor ~4G (2 players, run 5); +0.5G burst buffer (4608M = 4.5G)
SOULMASK_LOW="${SOULMASK_LOW:-12G}"
SOULMASK_HIGH="${SOULMASK_HIGH:-max}" # max = no ceiling in normal operation; set to e.g. 7G only during pressure tests

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
echo "default 4950"   > "$SCOPE/io.weight"              2>/dev/null && log "io.weight=4950"
echo "default 1000"   > "$SCOPE/io.bfq.weight"          2>/dev/null && log "io.bfq.weight=1000 (BFQ max; range 1-1000)"
echo 800              > "$SCOPE/cpu.weight"             2>/dev/null && log "cpu.weight=800"

# --- throttle bench containers (devcontainer + test-runner) ---
# docker-repack runs inside the devcontainer; test-runner is a separate bench container.
# We identify by image name patterns rather than fixed IDs since those change on restart.
_apply_bench_limits() {
  local cid="$1" label="$2"
  local pid scope
  pid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null || true)
  [ -z "$pid" ] && return
  scope="$CG$(awk -F: '/^0::/{print $3}' /proc/$pid/cgroup 2>/dev/null)"
  [ -d "$scope" ] || return
  echo "default 1" > "$scope/io.weight"     2>/dev/null
  echo "default 1" > "$scope/io.bfq.weight" 2>/dev/null
  for dev in 253:0 254:0; do
    echo "$dev rbps=${BENCH_RBPS} wbps=${BENCH_WBPS} riops=${BENCH_RIOPS} wiops=${BENCH_WIOPS}" \
      > "$scope/io.max" 2>/dev/null
  done
  log "$label ($cid): io.weight=1, io.bfq.weight=1, io.max=${BENCH_RIOPS}r/${BENCH_WIOPS}w IOPS 30MB/s"
}

for c in $(docker ps -q 2>/dev/null); do
  img=$(docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null || true)
  name=$(docker inspect -f '{{.Name}}' "$c" 2>/dev/null | tr -d '/' || true)
  case "$img" in
    *test-runner*) _apply_bench_limits "$c" "test-runner" ;;
  esac
  case "$name" in
    *devcontainer*|*dstdns-devcontainer*) _apply_bench_limits "$c" "devcontainer" ;;
  esac
done
