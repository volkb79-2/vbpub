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

SOULMASK_MIN="${SOULMASK_MIN:-5G}"    # calibrated 2026-06-27: 3-player hot set ~6G; 5G floor allows brief zswap under multi-tenant pressure
SOULMASK_LOW="${SOULMASK_LOW:-12G}"
SOULMASK_HIGH="${SOULMASK_HIGH:-6G}"  # 6G ceiling keeps soulmask from consuming ramdisk under Docker build pressure; area-load spikes peak ~6G so ceiling is tight but not harmful

# Discover block device major:minor for the pterodactyl volume directory.
# io.max needs the actual block device, not the filesystem.
# Prefers the pterodactyl path; falls back through docker data dir to root.
_io_dev() {
  local dev
  for path in /var/lib/pterodactyl/volumes /var/lib/docker /; do
    dev=$(findmnt -no MAJ:MIN --target "$path" 2>/dev/null) && echo "$dev" && return
  done
}
IO_DEV=$(_io_dev)
[ -n "$IO_DEV" ] && log "discovered io device: $IO_DEV" || log "WARN: could not discover block device for io.max"

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
_cg_write() {
  local file="$1" val="$2" label="$3"
  if echo "$val" > "$file" 2>/tmp/cg-write-err; then
    log "  $label = $val"
  else
    log "  WARN: $label write failed: $(cat /tmp/cg-write-err)"
  fi
}
_cg_write "$SCOPE/memory.high"            "$SOULMASK_HIGH" "memory.high"
_cg_write "$SCOPE/memory.low"             "$SOULMASK_LOW"  "memory.low"
_cg_write "$SCOPE/memory.min"             "$SOULMASK_MIN"  "memory.min"
_cg_write "$SCOPE/memory.zswap.writeback" "0"              "memory.zswap.writeback"
_cg_write "$SCOPE/io.weight"              "default 4950"   "io.weight"
_cg_write "$SCOPE/io.bfq.weight"          "default 1000"   "io.bfq.weight"
_cg_write "$SCOPE/cpu.weight"             "800"            "cpu.weight"
# Verify what was actually written (detects if Wings overrode our values)
log "  verify: min=$(cat "$SCOPE/memory.min" | awk '{printf "%dM",$1/1048576}') high=$(cat "$SCOPE/memory.high" | awk 'BEGIN{m=1073741824} $0=="max"{print "max";exit} {printf "%dG",$1/m}')"

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
  if [ -n "$IO_DEV" ]; then
    echo "$IO_DEV rbps=${BENCH_RBPS} wbps=${BENCH_WBPS} riops=${BENCH_RIOPS} wiops=${BENCH_WIOPS}" \
      > "$scope/io.max" 2>/dev/null
  fi
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
