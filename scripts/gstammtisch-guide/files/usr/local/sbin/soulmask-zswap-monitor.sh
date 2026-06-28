#!/usr/bin/env bash
# Soulmask cgroup memory monitor — zswap pressure + pak slice + disk swap.
#
# Columns (GAME cgroup):
#   RAM       memory.current               — uncompressed pages in physical RAM
#   z_pool    memory.zswap.current         — compressed bytes in zswap pool
#   out       memory.swap.current          — uncompressed equiv of pages NOT in RAM
#             (game has writeback=0: 'out' = all in zswap, zero disk)
#   rflt/s    workingset_refault_anon /s   — PRIMARY: decompress events/s
#   mflt/s    pgmajfault /s                — major faults incl. disk reads
#
# Columns (PAK slice):
#   p_RAM     pak memory.current           — hot pak pages in RAM (uncompressed)
#   p_z       pak memory.zswap.current     — warm pak pages compressed in zswap
#   p_out     pak memory.swap.current      — uncompressed equiv not in RAM
#             (pak has writeback=1: p_z=0 and p_out>0 means pak IS on disk)
#   p_rf      pak workingset_refault_anon  — pak decompression events
#
# SYS column:
#   disk_sw   system-wide swap NOT in zswap pool (pages actually on disk)
#             Computed: /proc/swaps Used − zswap stored_pages × 4 KiB
#
# Refault/s thresholds (GAME, 3 players, 2026-06-27 calibration):
#   0–30/s     ideal; game's hot set is in RAM
#   30–100/s   acceptable background level at correct memory.min
#   100–500/s  constant → memory.min too low; raise it
#   5k–40k/s   area load event (player entering new zone); decays in ~5 min; normal

INTERVAL="${1:-5}"  # seconds between samples

PAK_CG="/sys/fs/cgroup/soulmask.slice/soulmask-paks.slice"

_soul_cg() {
  local cid pid
  for cid in $(docker ps -q 2>/dev/null); do
    docker top "$cid" 2>/dev/null | grep -q WSServer || continue
    pid=$(docker inspect -f '{{.State.Pid}}' "$cid" 2>/dev/null)
    echo "/sys/fs/cgroup$(awk -F: '/^0::/{print $3}' /proc/$pid/cgroup 2>/dev/null)"
    return
  done
}

_disk_sw_mb() {
  local swap_kib zswap_pages disk_kib
  swap_kib=$(awk 'NR>1 {sum+=$4} END{print sum+0}' /proc/swaps 2>/dev/null)
  zswap_pages=$(cat /sys/kernel/debug/zswap/stored_pages 2>/dev/null || echo 0)
  disk_kib=$(( swap_kib - zswap_pages * 4 ))
  [ "$disk_kib" -lt 0 ] && disk_kib=0
  echo $(( disk_kib / 1024 ))
}

CG=$(_soul_cg)
[ -d "${CG:-}" ] || { echo "Soulmask cgroup not found"; exit 1; }

PAK_OK=0
[ -d "$PAK_CG" ] && PAK_OK=1

echo "Soulmask memory monitor — Ctrl-C to stop   (interval: ${INTERVAL}s)"
echo "  RAM / z_pool / out : uncompressed-RAM / compressed-in-zswap / not-in-RAM(uncompressed)"
echo "  GAME writeback=0   : 'out' is ALL in zswap — nothing on disk for game cgroup"
echo "  PAK  writeback=1   : p_out may include disk (p_z=0 + p_out>0 → pak IS on disk)"
echo "  disk_sw            : system-wide swap on disk (= /proc/swaps Used − zswap pages)"
echo ""
printf "%-8s | %-6s %-7s %-7s %-9s %-9s | %-6s %-6s %-6s %-6s | %s\n" \
  "time" "RAM" "z_pool" "out" "rflt/s" "mflt/s" \
  "p_RAM" "p_z" "p_out" "p_rf/s" "disk_sw"
echo "---------+------------------------------------------------------+-----------------------------+--------"

# Prime counters so the first displayed row shows a real rate, not the historical total
prev_rf=$(grep '^workingset_refault_anon' "$CG/memory.stat" | awk '{print $2}')
prev_mf=$(grep '^pgmajfault'             "$CG/memory.stat" | awk '{print $2}')
prev_prf=0
[ "$PAK_OK" = "1" ] && prev_prf=$(grep '^workingset_refault_anon' "$PAK_CG/memory.stat" | awk '{print $2}')
prev_ts=$SECONDS
sleep "$INTERVAL"

while true; do
  # GAME cgroup
  RAM=$(  awk '{printf "%dM",$1/1048576}' "$CG/memory.current")
  ZPOOL=$(awk '{printf "%dM",$1/1048576}' "$CG/memory.zswap.current")
  OUT=$(  awk '{printf "%dM",$1/1048576}' "$CG/memory.swap.current")
  RF=$(   grep '^workingset_refault_anon' "$CG/memory.stat" | awk '{print $2}')
  MF=$(   grep '^pgmajfault'             "$CG/memory.stat" | awk '{print $2}')

  # PAK slice
  if [ "$PAK_OK" = "1" ]; then
    PRAM=$( awk '{printf "%dM",$1/1048576}' "$PAK_CG/memory.current")
    PZ=$(   awk '{printf "%dM",$1/1048576}' "$PAK_CG/memory.zswap.current")
    POUT=$( awk '{printf "%dM",$1/1048576}' "$PAK_CG/memory.swap.current")
    PRF=$(  grep '^workingset_refault_anon' "$PAK_CG/memory.stat" | awk '{print $2}')
  else
    PRAM="—" PZ="—" POUT="—" PRF=0
  fi

  # Disk swap (system-wide)
  DSW="$(_disk_sw_mb)M"

  # Rates
  elapsed=$(( SECONDS - prev_ts )); [ "$elapsed" -eq 0 ] && elapsed=1
  d_rf=$(( (RF - prev_rf) / elapsed ))
  d_mf=$(( (MF - prev_mf) / elapsed ))
  d_prf=0
  [ "$PAK_OK" = "1" ] && d_prf=$(( (PRF - prev_prf) / elapsed ))

  ts=$(date +%H:%M:%S)
  printf "%-8s | %-6s %-7s %-7s %-9s %-9s | %-6s %-6s %-6s %-6s | %s\n" \
    "$ts" "$RAM" "$ZPOOL" "$OUT" "${d_rf}/s" "${d_mf}/s" \
    "$PRAM" "$PZ" "$POUT" "${d_prf}/s" "$DSW"

  prev_rf=$RF prev_mf=$MF prev_prf=$PRF prev_ts=$SECONDS
  sleep "$INTERVAL"
done
