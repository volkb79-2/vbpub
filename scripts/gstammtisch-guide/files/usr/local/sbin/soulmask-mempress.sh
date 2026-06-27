#!/usr/bin/env bash
# Gradually lower memory.high on the Soulmask cgroup to force pages into zswap.
# Used for finding the memory.min sweet spot: the lowest RAM level where
# refault/s stays near-zero during active gameplay.
#
# Usage:
#   soulmask-mempress.sh                    # show current state
#   soulmask-mempress.sh down [N]           # step down N×64M (default 1)
#   soulmask-mempress.sh up [N]             # step up N×64M
#   soulmask-mempress.sh set <value>        # set memory.high absolute (e.g. 6000M)
#   soulmask-mempress.sh floor <value>      # set panic floor (memory.min) for deeper testing
#   soulmask-mempress.sh reset              # memory.high → max (floor unchanged)
#   soulmask-mempress.sh finalize           # memory.high → max, memory.min → current sweet spot
#
# Workflow:
#   1. soulmask-mempress.sh floor 3G        # set panic floor (allows testing below 4608M)
#   2. soulmask-mempress.sh down            # step 64M at a time; pause at 512M boundaries
#   3. Watch refault/s in soulmask-zswap-monitor.sh
#   4. When refault/s is low and stable → soulmask-mempress.sh finalize
#   5. Update SOULMASK_MIN in /usr/local/sbin/setup-cgroups.sh
set -euo pipefail

STEP=$((64 * 1048576))   # 64M in bytes

_parse_bytes() {
  # Accept: "4096M" "4G" or raw bytes
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

_show() {
  local cur high min zpool swap refault
  cur=$(cat "$CG/memory.current")
  high=$(cat "$CG/memory.high")
  min=$(cat "$CG/memory.min")
  refault=$(grep '^workingset_refault_anon' "$CG/memory.stat" | awk '{print $2}')
  zpool=$(cat "$CG/memory.zswap.current")
  swap=$(cat "$CG/memory.swap.current")

  printf "memory.current: %dM\n" $(( cur / 1048576 ))
  if [ "$high" = "max" ]; then
    printf "memory.high:    max (no pressure applied)\n"
  else
    printf "memory.high:    %dM  ← active pressure ceiling\n" $(( high / 1048576 ))
    printf "  headroom:     %dM above floor\n" $(( (high - min) / 1048576 ))
  fi
  printf "memory.min:     %dM  (floor / panic stop)\n" $(( min / 1048576 ))
  printf "zswap pool:     %dM compressed / %dM uncompressed\n" \
    $(( zpool / 1048576 )) $(( swap / 1048576 ))
  printf "refault_anon:   %s (cumulative)\n" "$refault"
}

_boundary_check() {
  local val="$1"
  if [ $(( val % (512 * 1048576) )) -eq 0 ]; then
    echo ""
    echo "*** 512M BOUNDARY — pause for player testing ***"
    echo "    Watch: soulmask-zswap-monitor.sh"
    echo "    refault/s ≈ 0 and stable → step lower"
    echo "    refault/s > 0 or lag reported → this is the sweet spot"
    echo "    soulmask-mempress.sh finalize   (when done)"
  fi
}

CMD="${1:-status}"

case "$CMD" in
  status|"")
    _show ;;

  down)
    N="${2:-1}"
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && cur_high=$(cat "$CG/memory.current")
    aligned=$(( (cur_high / STEP) * STEP ))
    new=$(( aligned - N * STEP ))
    min=$(cat "$CG/memory.min")
    if [ "$new" -le "$min" ]; then
      echo "STOP: would hit floor (memory.min = $((min/1048576))M)."
      echo "  To test below $((min/1048576))M, lower the panic floor first:"
      echo "    soulmask-mempress.sh floor <lower_value>   e.g. floor 3G"
      echo "  Choose a value well below your expected sweet spot."
      exit 1
    fi
    echo "memory.high: $((cur_high/1048576))M → $((new/1048576))M  (−$((N*64))M)"
    echo "$new" > "$CG/memory.high"
    sleep 1
    _show
    _boundary_check "$new" ;;

  up)
    N="${2:-1}"
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && { echo "already at max"; _show; exit 0; }
    new=$(( cur_high + N * STEP ))
    echo "memory.high: $((cur_high/1048576))M → $((new/1048576))M  (+$((N*64))M)"
    echo "$new" > "$CG/memory.high"
    sleep 1
    _show ;;

  set)
    val="${2:?usage: soulmask-mempress.sh set <value>   e.g. 6000M or 6G}"
    bytes=$(_parse_bytes "$val")
    min=$(cat "$CG/memory.min")
    if [ "$bytes" -le "$min" ]; then
      echo "STOP: $val ($((bytes/1048576))M) ≤ floor ($((min/1048576))M). Lower the floor first:"
      echo "  soulmask-mempress.sh floor <lower_value>"
      exit 1
    fi
    echo "memory.high → $val"
    echo "$bytes" > "$CG/memory.high"
    sleep 1
    _show
    _boundary_check "$bytes" ;;

  floor)
    # Set memory.min to a lower panic floor to allow deeper memory.high testing.
    # The floor is a safety net only — the kernel won't push Soulmask below it
    # even under extreme global pressure.
    val="${2:?usage: soulmask-mempress.sh floor <value>   e.g. 3G or 3072M}"
    bytes=$(_parse_bytes "$val")
    cur_min=$(cat "$CG/memory.min")
    cur_high=$(cat "$CG/memory.high")

    if [ "$bytes" -ge "$cur_min" ]; then
      echo "INFO: $val is not lower than current floor ($((cur_min/1048576))M) — no change needed."
      exit 0
    fi

    echo "WARNING: lowering memory.min reduces the kernel's RAM guarantee for Soulmask."
    echo "  This is intentional during sweet-spot testing."
    echo "  After testing, 'finalize' will set memory.min to the found sweet spot."
    echo ""
    echo "memory.min: $((cur_min/1048576))M → $((bytes/1048576))M  (panic floor lowered)"
    echo "$bytes" > "$CG/memory.min"

    # If memory.high is now at or below the new floor (shouldn't happen, but guard):
    if [ "$cur_high" != "max" ] && [ "$cur_high" -le "$bytes" ]; then
      echo "Adjusting memory.high above new floor..."
      echo $(( bytes + STEP )) > "$CG/memory.high"
    fi
    sleep 1
    _show ;;

  reset)
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && { echo "already at max"; _show; exit 0; }
    echo "memory.high: $((cur_high/1048576))M → max  (pressure removed, floor unchanged)"
    echo max > "$CG/memory.high"
    echo ""
    echo "Floor (memory.min) is still $(( $(cat "$CG/memory.min") / 1048576 ))M."
    echo "Run 'finalize' to lock in the sweet spot, or 'floor' to restore the original floor." ;;

  finalize)
    # Lock in the found sweet spot:
    #   memory.high → max (no more artificial ceiling)
    #   memory.min  → current memory.high (the sweet spot we found)
    cur_high=$(cat "$CG/memory.high")
    if [ "$cur_high" = "max" ]; then
      echo "memory.high is already max — nothing to finalize."
      echo "Set memory.min manually: echo <bytes> > $CG/memory.min"
      exit 1
    fi
    sweet=$((cur_high / 1048576))
    echo "Sweet spot: ${sweet}M"
    echo "  memory.high: ${sweet}M → max"
    echo "  memory.min:  $(($(cat "$CG/memory.min") / 1048576))M → ${sweet}M"
    echo "$cur_high" > "$CG/memory.min"
    echo max > "$CG/memory.high"
    echo ""
    echo "To persist across restarts, update setup-cgroups.sh:"
    echo "  sed -i 's/SOULMASK_MIN=.*/SOULMASK_MIN=\"\${SOULMASK_MIN:-${sweet}M}\"/' \\"
    echo "    /usr/local/sbin/setup-cgroups.sh"
    echo "  systemctl restart gstammtisch-cgroups"
    sleep 1
    _show ;;

  *)
    echo "Usage: $(basename "$0") [status|down [N]|up [N]|set <val>|floor <val>|reset|finalize]"
    exit 1 ;;
esac
