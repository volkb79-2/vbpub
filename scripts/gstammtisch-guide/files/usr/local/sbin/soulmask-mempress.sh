#!/usr/bin/env bash
# Cgroup memory knob manager for the Soulmask container.
#
# Two modes:
#
#   Production config — set the steady-state operating band:
#     soulmask-mempress.sh apply 5G 6G      # min=5G floor, high=6G ceiling
#     soulmask-mempress.sh min 5G           # floor only
#     soulmask-mempress.sh high 6G          # ceiling only
#
#   Calibration — find the hot-set sweet spot:
#     soulmask-mempress.sh min 3G           # lower floor below test range
#     soulmask-mempress.sh down [N]         # step ceiling down N×64M; pause at 512M marks
#     soulmask-mempress.sh up [N]           # step ceiling up N×64M
#     soulmask-mempress.sh finalize         # lock sweet spot: min→current high, high→max
#
#   Other:
#     soulmask-mempress.sh status           # show all knobs + cumulative refault
#     soulmask-mempress.sh reset            # high→max (remove ceiling; min unchanged)
#
# Backward-compat aliases: 'set' = 'high', 'floor' = 'min'
#
# Production workflow (new host or after re-calibration):
#   soulmask-mempress.sh apply 5G 6G
#   # then persist:
#   sed -i 's/SOULMASK_MIN=.*/SOULMASK_MIN="${SOULMASK_MIN:-5G}"/' /usr/local/sbin/setup-cgroups.sh
#   sed -i 's/SOULMASK_HIGH=.*/SOULMASK_HIGH="${SOULMASK_HIGH:-6G}"/' /usr/local/sbin/setup-cgroups.sh
#   systemctl restart gstammtisch-cgroups
#
# Calibration workflow:
#   1. soulmask-mempress.sh min 3G          # set panic floor below test range
#   2. soulmask-mempress.sh down            # step 64M; pause at 512M marks
#   3. Watch refault/s in soulmask-zswap-monitor.sh
#   4. Sweet spot found → soulmask-mempress.sh finalize
#   5. Persist with the sed commands printed by finalize

set -euo pipefail

STEP=$((64 * 1048576))   # 64M per calibration step

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

CG=$(_cg) || { echo "Soulmask not running"; exit 1; }
[ -d "$CG" ] || { echo "cgroup not found: $CG"; exit 1; }

_mb() { echo $(( $1 / 1048576 )); }

_show() {
  local cur high min low zpool zswap_uncomp refault
  cur=$(cat "$CG/memory.current")
  high=$(cat "$CG/memory.high")
  min=$(cat "$CG/memory.min")
  low=$(cat "$CG/memory.low")
  refault=$(grep '^workingset_refault_anon' "$CG/memory.stat" | awk '{print $2}')
  zpool=$(cat "$CG/memory.zswap.current")
  zswap_uncomp=$(cat "$CG/memory.swap.current")

  echo ""
  printf "  RAM now:        %dM\n" $(_mb "$cur")
  if [ "$high" = "max" ]; then
    printf "  memory.high:    max  (no ceiling)\n"
  else
    printf "  memory.high:    %dM  ← soft ceiling  [headroom: %dM]\n" \
      $(_mb "$high") $(( ($high - $cur) / 1048576 ))
  fi
  printf "  memory.min:     %dM  (guaranteed uncompressed floor)\n" $(_mb "$min")
  printf "  memory.low:     %dM  (soft global-pressure hint)\n" $(_mb "$low")
  printf "  zswap:          %dM compressed / %dM uncompressed equiv\n" \
    $(_mb "$zpool") $(_mb "$zswap_uncomp")
  printf "  refault_anon:   %s cumulative  (run soulmask-zswap-monitor.sh for rate)\n" "$refault"
  echo ""
}

_boundary_check() {
  local val="$1"
  if [ $(( val % (512 * 1048576) )) -eq 0 ]; then
    echo "*** 512M BOUNDARY — pause for player feedback ***"
    echo "    refault/s < 30 and stable → 'down' to go lower"
    echo "    refault/s > 100 or players report lag → 'finalize' here"
    echo "    soulmask-zswap-monitor.sh to watch live"
    echo ""
  fi
}

CMD="${1:-status}"

case "$CMD" in
  # ── status ────────────────────────────────────────────────────────────────
  status|"")
    _show ;;

  # ── min / floor  (set memory.min = guaranteed floor) ─────────────────────
  min|floor)
    val="${2:?usage: soulmask-mempress.sh min <value>   e.g. 5G or 5120M}"
    bytes=$(_parse_bytes "$val")
    cur_min=$(cat "$CG/memory.min")
    cur_high=$(cat "$CG/memory.high")

    # Guard: min must not exceed ceiling when ceiling is active
    if [ "$cur_high" != "max" ] && [ "$bytes" -ge "$cur_high" ]; then
      printf "STOP: %s (%dM) ≥ ceiling (%dM). Raise the ceiling first:\n" \
        "$val" $(_mb "$bytes") $(_mb "$cur_high")
      echo "  soulmask-mempress.sh high <higher_value>"
      echo "  soulmask-mempress.sh apply <min> <high>"
      exit 1
    fi

    dir="raised"; [ "$bytes" -lt "$cur_min" ] && dir="lowered"
    printf "memory.min: %dM → %dM  (floor %s)\n" $(_mb "$cur_min") $(_mb "$bytes") "$dir"
    echo "$bytes" > "$CG/memory.min"
    sleep 1
    _show ;;

  # ── high / set  (set memory.high = soft ceiling) ─────────────────────────
  high|set)
    val="${2:?usage: soulmask-mempress.sh high <value>   e.g. 6G or 6144M}"
    bytes=$(_parse_bytes "$val")
    min=$(cat "$CG/memory.min")

    if [ "$bytes" -le "$min" ]; then
      printf "STOP: %s (%dM) ≤ floor (%dM). Lower the floor first:\n" \
        "$val" $(_mb "$bytes") $(_mb "$min")
      echo "  soulmask-mempress.sh min <lower_value>"
      echo "  soulmask-mempress.sh apply <min> <high>"
      exit 1
    fi

    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && cur_high_str="max" || cur_high_str="$(_mb "$cur_high")M"
    printf "memory.high: %s → %dM\n" "$cur_high_str" $(_mb "$bytes")
    echo "$bytes" > "$CG/memory.high"
    sleep 1
    _show
    _boundary_check "$bytes" ;;

  # ── apply <min> <high>  (set both at once) ───────────────────────────────
  apply)
    min_val="${2:?usage: soulmask-mempress.sh apply <min> <high>   e.g. apply 5G 6G}"
    high_val="${3:?usage: soulmask-mempress.sh apply <min> <high>   e.g. apply 5G 6G}"
    min_bytes=$(_parse_bytes "$min_val")
    high_bytes=$(_parse_bytes "$high_val")

    if [ "$min_bytes" -ge "$high_bytes" ]; then
      printf "STOP: min (%dM) must be < high (%dM)\n" $(_mb "$min_bytes") $(_mb "$high_bytes")
      exit 1
    fi

    # Write min first — then high (avoids transient min > high conflict)
    printf "memory.min:  → %dM\n" $(_mb "$min_bytes")
    printf "memory.high: → %dM\n" $(_mb "$high_bytes")
    echo "$min_bytes"  > "$CG/memory.min"
    echo "$high_bytes" > "$CG/memory.high"
    sleep 1
    _show

    # Print persist commands
    min_g=$(( min_bytes / 1073741824 )); rem=$(( min_bytes % 1073741824 ))
    [ "$rem" -eq 0 ] && min_str="${min_g}G" || min_str="$(_mb "$min_bytes")M"
    high_g=$(( high_bytes / 1073741824 )); rem=$(( high_bytes % 1073741824 ))
    [ "$rem" -eq 0 ] && high_str="${high_g}G" || high_str="$(_mb "$high_bytes")M"

    echo "To persist across restarts:"
    echo "  sed -i 's/SOULMASK_MIN=.*/SOULMASK_MIN=\"\${SOULMASK_MIN:-${min_str}}\"/' /usr/local/sbin/setup-cgroups.sh"
    echo "  sed -i 's/SOULMASK_HIGH=.*/SOULMASK_HIGH=\"\${SOULMASK_HIGH:-${high_str}}\"/' /usr/local/sbin/setup-cgroups.sh"
    echo "  systemctl restart gstammtisch-cgroups" ;;

  # ── down / up  (calibration stepping) ────────────────────────────────────
  down)
    N="${2:-1}"
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && cur_high=$(cat "$CG/memory.current")
    aligned=$(( (cur_high / STEP) * STEP ))
    new=$(( aligned - N * STEP ))
    min=$(cat "$CG/memory.min")
    if [ "$new" -le "$min" ]; then
      printf "STOP: would hit floor (%dM).\n" $(_mb "$min")
      printf "  To test below %dM, lower the floor first:\n" $(_mb "$min")
      echo "  soulmask-mempress.sh min <lower_value>   e.g. min 3G"
      exit 1
    fi
    printf "memory.high: %dM → %dM  (−%dM)\n" $(_mb "$cur_high") $(_mb "$new") $(( N * 64 ))
    echo "$new" > "$CG/memory.high"
    sleep 1
    _show
    _boundary_check "$new" ;;

  up)
    N="${2:-1}"
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && { echo "already at max — use 'high <val>' to set a ceiling"; _show; exit 0; }
    new=$(( cur_high + N * STEP ))
    printf "memory.high: %dM → %dM  (+%dM)\n" $(_mb "$cur_high") $(_mb "$new") $(( N * 64 ))
    echo "$new" > "$CG/memory.high"
    sleep 1
    _show ;;

  # ── reset  (remove ceiling) ───────────────────────────────────────────────
  reset)
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && { echo "already at max — no ceiling active"; _show; exit 0; }
    printf "memory.high: %dM → max  (ceiling removed; floor unchanged)\n" $(_mb "$cur_high")
    echo max > "$CG/memory.high"
    echo "  Floor (memory.min) still at $(( $(cat "$CG/memory.min") / 1048576 ))M."
    echo "  Use 'apply <min> <high>' to restore a production ceiling." ;;

  # ── finalize  (lock calibration sweet spot) ──────────────────────────────
  finalize)
    cur_high=$(cat "$CG/memory.high")
    if [ "$cur_high" = "max" ]; then
      echo "memory.high is already max — nothing to finalize."
      echo "Use 'apply <min> <high>' to set the production band directly."
      exit 1
    fi
    sweet=$(_mb "$cur_high")
    old_min=$(_mb "$(cat "$CG/memory.min")")
    printf "Sweet spot: %dM\n" "$sweet"
    printf "  memory.min:  %dM → %dM\n" "$old_min" "$sweet"
    printf "  memory.high: %dM → max\n" "$sweet"
    echo "$cur_high" > "$CG/memory.min"
    echo max > "$CG/memory.high"
    sleep 1
    _show
    echo "To persist across restarts:"
    echo "  sed -i 's/SOULMASK_MIN=.*/SOULMASK_MIN=\"\${SOULMASK_MIN:-${sweet}M}\"/' /usr/local/sbin/setup-cgroups.sh"
    echo "  # If you also want a production ceiling (recommended: sweet + 1G):"
    echo "  sed -i 's/SOULMASK_HIGH=.*/SOULMASK_HIGH=\"\${SOULMASK_HIGH:-$(( sweet + 1024 ))M}\"/' /usr/local/sbin/setup-cgroups.sh"
    echo "  systemctl restart gstammtisch-cgroups" ;;

  *)
    cat <<'USAGE'
Usage: soulmask-mempress.sh <command> [args]

  status                 show all knobs and zswap stats
  min <val>              set memory.min (floor)   e.g. min 5G
  high <val>             set memory.high (ceiling) e.g. high 6G
  apply <min> <high>     set both at once          e.g. apply 5G 6G
  down [N]               step ceiling down N×64M (calibration)
  up [N]                 step ceiling up N×64M
  reset                  remove ceiling (high → max)
  finalize               lock calibration sweet spot

Aliases: 'set' = 'high', 'floor' = 'min'
USAGE
    exit 1 ;;
esac
