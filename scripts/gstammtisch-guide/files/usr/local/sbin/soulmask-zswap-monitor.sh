#!/usr/bin/env bash
# Soulmask cgroup memory monitor — zswap pressure + pak slice + disk swap.
#
# Columns (GAME cgroup):
#   RAM     memory.current             — physical RAM (anon+file+kernel incl. zswap pool)
#   z_pool  memory.zswap.current       — compressed bytes in zswap pool
#   z_eq    zswapped (memory.stat)     — cold-in-zswap, uncompressed equiv
#           true compression ratio = z_eq / z_pool  (e.g. 5742/1806 = 3.18×)
#           [NOT memory.swap.current — that also counts swapcached pages still in RAM]
#   rflt/s  workingset_refault_anon/s  — PRIMARY: zswap decompress events/s
#   mflt/s  pgmajfault/s               — major faults (≈ rflt/s when writeback=0)
#
# Columns (PAK slice):
#   p_RAM   pak memory.current         — pak pages in physical RAM
#   p_z     pak memory.zswap.current   — pak compressed in zswap
#   p_disk  pak on REAL disk           — pak memory.swap.current − pak zswapped
#           p_z>0  → warm pak in zswap (~3µs access)
#           p_disk>0 → cold pak written to disk (~10ms access)
#   p_rf/s  pak workingset_refault_anon/s — pak decompression events
#   p_mf/s  pak pgmajfault/s           — disk reads (0 unless p_disk>0)
#
# SYS column:
#   disk_sw  system-wide pages on disk — /proc/swaps Used − zswap stored_pages × 4 KiB
#
# Refault/s thresholds (GAME, 3 players, 2026-06-27 calibration):
#   0–30/s     ideal; game's hot set fits in RAM
#   30–100/s   acceptable at correct memory.min
#   100–500/s  continuous → memory.min too low; raise it
#   5k–40k/s   area load event (player entering new zone); decays ~5 min; normal

# ─── argument parsing ─────────────────────────────────────────────────────────
case "${1:-}" in
  --help|-h)
    cat <<'HELP'
Usage: soulmask-zswap-monitor.sh [INTERVAL_SECONDS]

  INTERVAL  seconds between samples (default: 5)

Columns:
  RAM      memory.current (RAM incl. compressed zswap pool in kernel memory)
  z_pool   compressed bytes in zswap pool  (memory.zswap.current)
  z_eq     cold-in-zswap uncompressed      (memory.stat 'zswapped')
           compression ratio = z_eq / z_pool
  rflt/s   zswap decompress events/s (PRIMARY pressure metric)
  mflt/s   major faults/s (≈rflt/s when writeback=0; spike = unexpected disk reads)
  p_z      pak compressed in zswap
  p_disk   pak on REAL disk = pak memory.swap.current − pak zswapped
  p_rf/s   pak decompression events/s
  p_mf/s   pak disk read faults/s (0 when pak is in zswap or RAM)
  disk_sw  system-wide pages on real disk swap
HELP
    exit 0 ;;
  --*)
    echo "Unknown option: $1" >&2
    echo "Use --help for usage." >&2
    exit 1 ;;
esac

INTERVAL="${1:-5}"
case "$INTERVAL" in
  ''|*[!0-9.]*) echo "Invalid interval '$INTERVAL' — must be a number. Use --help." >&2; exit 1 ;;
esac

# ─── locate cgroups ───────────────────────────────────────────────────────────
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

# ─── one-time writeback status banner ─────────────────────────────────────────
GAME_WB=$(cat "$CG/memory.zswap.writeback" 2>/dev/null || echo "?")
if [ "$GAME_WB" = "0" ]; then
  GAME_WB_LABEL="writeback=0  (confirmed: game pages NEVER reach disk — all cold pages stay in zswap)"
else
  GAME_WB_LABEL="writeback=$GAME_WB  WARNING: game pages may be written to disk"
fi

PAK_WB_LABEL="(pak slice not found)"
if [ "$PAK_OK" = "1" ]; then
  PAK_WB=$(cat "$PAK_CG/memory.zswap.writeback" 2>/dev/null || echo "?")
  if [ "$PAK_WB" = "1" ]; then
    PAK_WB_LABEL="writeback=1  (cold pak may reach disk — watch p_disk column)"
  else
    PAK_WB_LABEL="writeback=$PAK_WB"
  fi
fi

echo "Soulmask memory monitor — Ctrl-C to stop   (interval: ${INTERVAL}s)"
echo ""
echo "  GAME  $GAME_WB_LABEL"
echo "  PAK   $PAK_WB_LABEL"
echo ""
echo "  Column guide:"
echo "    z_eq          cold-in-zswap uncompressed (memory.stat 'zswapped', not memory.swap.current)"
echo "    z_eq / z_pool true compression ratio"
echo "    p_disk        pak on REAL disk = pak swap.current − pak zswapped"
echo "    p_mf/s        pak disk reads/s  (0 = pak never touches disk)"
echo "    disk_sw       system-wide disk swap  (game contributes 0 when writeback=0)"
echo ""
printf "%-8s | %-6s %-7s %-7s %-9s %-9s | %-6s %-6s %-7s %-7s %-6s | %s\n" \
  "time" "RAM" "z_pool" "z_eq" "rflt/s" "mflt/s" \
  "p_RAM" "p_z" "p_disk" "p_rf/s" "p_mf/s" "disk_sw"
echo "---------+--------------------------------------------------+---------------------------------------+--------"

# ─── prime counters: read current totals so first row shows real rates ─────────
STAT=$(cat "$CG/memory.stat")
prev_rf=$(awk '/^workingset_refault_anon /{print $2}' <<< "$STAT")
prev_mf=$(awk '/^pgmajfault /{print $2}' <<< "$STAT")
prev_prf=0; prev_pmf=0
if [ "$PAK_OK" = "1" ]; then
  PSTAT=$(cat "$PAK_CG/memory.stat")
  prev_prf=$(awk '/^workingset_refault_anon /{print $2}' <<< "$PSTAT")
  prev_pmf=$(awk '/^pgmajfault /{print $2}' <<< "$PSTAT")
fi
prev_ts=$SECONDS
sleep "$INTERVAL"

# ─── sampling loop ────────────────────────────────────────────────────────────
while true; do
  # GAME cgroup — read memory.stat once per sample
  STAT=$(cat "$CG/memory.stat")
  RAM=$(  awk '{printf "%dM",$1/1048576}' "$CG/memory.current")
  ZPOOL=$(awk '{printf "%dM",$1/1048576}' "$CG/memory.zswap.current")
  ZEQ_B=$(awk '/^zswapped /{print $2}' <<< "$STAT"); ZEQ_B="${ZEQ_B:-0}"
  ZEQ="$(( ZEQ_B / 1048576 ))M"
  RF=$(   awk '/^workingset_refault_anon /{print $2}' <<< "$STAT"); RF="${RF:-0}"
  MF=$(   awk '/^pgmajfault /{print $2}' <<< "$STAT"); MF="${MF:-0}"

  # PAK slice — read memory.stat once per sample
  if [ "$PAK_OK" = "1" ]; then
    PSTAT=$(cat "$PAK_CG/memory.stat")
    PRAM=$(  awk '{printf "%dM",$1/1048576}' "$PAK_CG/memory.current")
    PZ=$(    awk '{printf "%dM",$1/1048576}' "$PAK_CG/memory.zswap.current")
    PSWAP=$( cat "$PAK_CG/memory.swap.current")
    PZEQ=$(  awk '/^zswapped /{print $2}' <<< "$PSTAT"); PZEQ="${PZEQ:-0}"
    PDISK_B=$(( PSWAP - PZEQ )); [ "$PDISK_B" -lt 0 ] && PDISK_B=0
    PDISK="${PDISK_B}M"
    PRF=$(awk '/^workingset_refault_anon /{print $2}' <<< "$PSTAT"); PRF="${PRF:-0}"
    PMF=$(awk '/^pgmajfault /{print $2}' <<< "$PSTAT"); PMF="${PMF:-0}"
  else
    PRAM="—"; PZ="—"; PDISK="—"; PRF=0; PMF=0
  fi

  DSW="$(_disk_sw_mb)M"

  # rates (delta over elapsed seconds)
  elapsed=$(( SECONDS - prev_ts )); [ "$elapsed" -eq 0 ] && elapsed=1
  d_rf=$(( (RF  - prev_rf)  / elapsed ))
  d_mf=$(( (MF  - prev_mf)  / elapsed ))
  d_prf=0; d_pmf=0
  if [ "$PAK_OK" = "1" ]; then
    d_prf=$(( (PRF - prev_prf) / elapsed ))
    d_pmf=$(( (PMF - prev_pmf) / elapsed ))
  fi

  PPRF_S="${d_prf}/s"; PPMF_S="${d_pmf}/s"
  [ "$PAK_OK" != "1" ] && PPRF_S="—" && PPMF_S="—"

  printf "%-8s | %-6s %-7s %-7s %-9s %-9s | %-6s %-6s %-7s %-7s %-6s | %s\n" \
    "$(date +%H:%M:%S)" "$RAM" "$ZPOOL" "$ZEQ" "${d_rf}/s" "${d_mf}/s" \
    "$PRAM" "$PZ" "$PDISK" "$PPRF_S" "$PPMF_S" "$DSW"

  prev_rf=$RF; prev_mf=$MF; prev_prf=$PRF; prev_pmf=$PMF; prev_ts=$SECONDS
  sleep "$INTERVAL"
done
