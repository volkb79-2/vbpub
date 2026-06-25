#!/usr/bin/env bash
# swap-health.sh [watch] — zswap / swap / pressure snapshot.
# Monitor the RIGHT metrics: pgmajfault (real disk reads) and PSI, NOT `vmstat si`
# (which mixes fast zswap-pool hits with slow disk reads into one misleading number).
set -uo pipefail
Z=/sys/kernel/debug/zswap
hr(){ printf '%s\n' "------------------------------------------------------------"; }

snap(){
  hr
  echo "ZSWAP  (compressor=$(cat /sys/module/zswap/parameters/compressor 2>/dev/null)" \
       "pool%=$(cat /sys/module/zswap/parameters/max_pool_percent 2>/dev/null)" \
       "shrinker=$(cat /sys/module/zswap/parameters/shrinker_enabled 2>/dev/null)" \
       "enabled=$(cat /sys/module/zswap/parameters/enabled 2>/dev/null))"
  if [ -r "$Z/stored_pages" ]; then
    stored=$(cat "$Z/stored_pages"); pool=$(cat "$Z/pool_total_size"); wb=$(cat "$Z/written_back_pages")
    echo "  stored_pages=$stored  pool_total_size=${pool}B  written_back_pages=$wb"
    if [ "${pool:-0}" -gt 0 ]; then
      awk -v s="$stored" -v p="$pool" 'BEGIN{
        printf "  effective compression: %.2fx  (%.1f MB stored in %.1f MB pool)\n",
        (s*4096)/p, s*4096/1048576, p/1048576}'
    fi
    # writeback ratio: high => pool too small / not enough RAM (consider raising pool% or RAM)
    if [ "${stored:-0}" -gt 0 ]; then
      awk -v wb="$wb" -v s="$stored" 'BEGIN{printf "  writeback ratio: %.1f%% (>10%% = pressure on the pool)\n", 100*wb/(wb+s+1)}'
    fi
    for r in reject_compress_poor reject_alloc_fail pool_limit_hit stored_incompressible_pages decompress_fail; do
      [ -r "$Z/$r" ] && echo "  $r=$(cat "$Z/$r")"
    done
  else
    echo "  (debugfs zswap stats not readable — run as root; debugfs at /sys/kernel/debug)"
  fi
  hr; echo "SWAP DEVICES"; swapon --show 2>/dev/null || echo "  (none active)"
  hr; echo "SWAP COUNTERS (/proc/vmstat)"; grep -E 'pgmajfault|pswpin|pswpout|zswpin|zswpout|zswpwb' /proc/vmstat
  hr; echo "MEMORY PRESSURE (PSI)"; cat /proc/pressure/memory 2>/dev/null
  hr; echo "FREE"; free -h
}

if [ "${1:-}" = "watch" ]; then watch -n 5 "$0"; else snap; fi
