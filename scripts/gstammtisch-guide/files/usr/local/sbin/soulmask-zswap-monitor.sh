#!/usr/bin/env bash
# Watch Soulmask zswap usage in real time.
# Shows refault rate (decompress events) — the primary indicator of zswap pressure.
# Run while players are active; correlate refault rate spikes with visible lag.
INTERVAL=${1:-5}  # seconds between samples (default 5)

_soul_cg() {
  local cid pid
  for cid in $(docker ps -q 2>/dev/null); do
    docker top "$cid" 2>/dev/null | grep -q WSServer && {
      pid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null)
      echo "/sys/fs/cgroup$(awk -F: '/^0::/{print $3}' /proc/$pid/cgroup 2>/dev/null)"
      return
    }
  done
}

CG=$(_soul_cg)
[ -d "$CG" ] || { echo "Soulmask cgroup not found"; exit 1; }

printf "%-8s  %-8s %-10s  %-10s %-10s  %-10s %-8s  %-6s\n" \
  "time" "RAM(M)" "zswap_pool" "swap_total" "refault/s" "majfault/s" "shmem_pak" "ratio"

prev_refault=0; prev_majfault=0; prev_ts=$SECONDS
while true; do
  RAM=$(cat $CG/memory.current | awk '{printf "%d",$1/1048576}')
  ZPOOL=$(cat $CG/memory.zswap.current | awk '{printf "%dM",$1/1048576}')
  SWAP=$(cat $CG/memory.swap.current | awk '{printf "%dM",$1/1048576}')
  REFAULT=$(grep '^workingset_refault_anon' $CG/memory.stat | awk '{print $2}')
  MAJFAULT=$(grep '^pgmajfault' $CG/memory.stat | awk '{print $2}')
  SHMEM_ROOT=$(grep '^shmem ' /sys/fs/cgroup/memory.stat | awk '{printf "%dM",$2/1048576}')
  ZSWAP_COMPRESSED=$(cat $CG/memory.zswap.current)
  SWAP_UNCOMP=$(cat $CG/memory.swap.current)
  RATIO=$(awk "BEGIN{printf \"%.1fx\", ($ZSWAP_COMPRESSED>0 ? $SWAP_UNCOMP/$ZSWAP_COMPRESSED : 0)}")

  elapsed=$(( SECONDS - prev_ts ))
  [ $elapsed -eq 0 ] && elapsed=1
  d_refault=$(( (REFAULT - prev_refault) / elapsed ))
  d_majfault=$(( (MAJFAULT - prev_majfault) / elapsed ))

  ts=$(date +%H:%M:%S)
  printf "%-8s  %-8s %-10s  %-10s %-10s  %-10s %-8s  %-6s\n" \
    "$ts" "${RAM}M" "$ZPOOL" "$SWAP" "${d_refault}/s" "${d_majfault}/s" "$SHMEM_ROOT" "$RATIO"

  prev_refault=$REFAULT; prev_majfault=$MAJFAULT; prev_ts=$SECONDS
  sleep "$INTERVAL"
done
