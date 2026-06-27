#!/usr/bin/env bash
# Gradually lower memory.high on the Soulmask cgroup to force pages into zswap.
# Used for finding the memory.min sweet spot: the lowest RAM level that keeps
# refault/s at 0 during active gameplay.
#
# Usage:
#   soulmask-mempress.sh               # show current state
#   soulmask-mempress.sh down          # step down 64M
#   soulmask-mempress.sh down <N>      # step down N*64M in one go
#   soulmask-mempress.sh up            # step up 64M (ease pressure)
#   soulmask-mempress.sh reset         # set memory.high back to max
#   soulmask-mempress.sh set <value>   # set absolute value (e.g. 8000M)
#
# After finding the sweet spot:
#   1. Note the current memory.high value (the lowest stable level)
#   2. soulmask-mempress.sh reset       (remove the ceiling)
#   3. echo <sweet_spot_bytes> > $CG/memory.min   (make it a permanent floor)
#   4. Update SOULMASK_MIN in /usr/local/sbin/setup-cgroups.sh
set -euo pipefail

STEP=$((64 * 1048576))   # 64M in bytes

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
  local cur high min refault
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
  printf "memory.min:     %dM  (permanent floor)\n" $(( min / 1048576 ))
  printf "zswap pool:     %dM compressed / %dM uncompressed\n" \
    $(( zpool / 1048576 )) $(( swap / 1048576 ))
  printf "refault_anon:   %s (cumulative)\n" "$refault"
}

CMD="${1:-status}"

case "$CMD" in
  status|"")
    _show ;;

  down)
    N="${2:-1}"
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && cur_high=$(cat "$CG/memory.current")
    # align down to nearest 64M boundary first, then step
    aligned=$(( (cur_high / STEP) * STEP ))
    new=$(( aligned - N * STEP ))
    min=$(cat "$CG/memory.min")
    if [ "$new" -le "$min" ]; then
      echo "STOP: would hit memory.min ($((min/1048576))M). Cannot go lower than the floor."
      exit 1
    fi
    echo "memory.high: $((cur_high/1048576))M → $((new/1048576))M  (−$((N*64))M)"
    echo "$new" > "$CG/memory.high"
    sleep 1
    _show
    # Warn on 512M boundaries
    boundary=$(( new / (512*1048576) ))
    if [ $(( new % (512*1048576) )) -eq 0 ]; then
      echo ""
      echo "*** 512M BOUNDARY — good time to pause for player testing ***"
      echo "    Watch refault/s:  soulmask-zswap-monitor.sh"
      echo "    If stable → continue stepping down"
      echo "    If refault/s > 0 or players report lag → this is the sweet spot"
    fi ;;

  up)
    N="${2:-1}"
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && { echo "already at max"; exit 0; }
    new=$(( cur_high + N * STEP ))
    echo "memory.high: $((cur_high/1048576))M → $((new/1048576))M  (+$((N*64))M)"
    echo "$new" > "$CG/memory.high"
    sleep 1
    _show ;;

  reset)
    cur_high=$(cat "$CG/memory.high")
    echo "memory.high: $((cur_high/1048576))M → max  (pressure removed)"
    echo max > "$CG/memory.high"
    echo ""
    echo "Next: if you found the sweet spot, set memory.min to that value:"
    echo "  echo <bytes> > $CG/memory.min"
    echo "  Update SOULMASK_MIN in /usr/local/sbin/setup-cgroups.sh" ;;

  set)
    val="${2:?usage: soulmask-mempress.sh set <value>  e.g. 8000M}"
    # Accept M suffix
    bytes=$(echo "$val" | awk '/M$/{print $0+0; gsub("M",""); exit} {print $0}' | \
            awk '{if($0~/[0-9]+M$/){gsub("M",""); print $0*1048576} else print $0}')
    bytes=$(echo "$val" | sed 's/M$//' | awk '{print $1 * 1048576}')
    echo "memory.high → $val ($bytes bytes)"
    echo "$bytes" > "$CG/memory.high"
    sleep 1
    _show ;;

  *)
    echo "Usage: $(basename "$0") [status|down [N]|up [N]|reset|set <value>]"
    exit 1 ;;
esac
