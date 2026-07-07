#!/usr/bin/env bash
# Cgroup memory knob manager for Soulmask.
#
# Operates on either the GAME cgroup (default) or the PAK slice.
#
# N-instance (2026-07-07): when SLICE=game and more than one Soulmask
# instance is running, pick one with -c <uuid-or-prefix> (matches against
# the docker container name, which IS the Pterodactyl server UUID). With
# exactly one instance running, -c is optional. -c is ignored for
# --slice pak (the pak tmpfs/slice is shared across all instances — see
# SOULMASK.md "Multi-instance operations").
#
# Usage:
#   soulmask-mempress.sh [--slice game|pak] [-c <uuid-or-prefix>] <command> [args]
#   soulmask-pak-mempress.sh <command> [args]      # wrapper — pak slice
#
# Commands:
#   status                 show all knobs and zswap stats
#   min <val>              set memory.min (guaranteed uncompressed floor)
#   high <val>             set memory.high (soft ceiling)
#   apply <min> <high>     set both at once — prints persist commands
#   down [N]               step ceiling down N×25M, 250ms between steps
#   up [N]                 step ceiling up N×25M, 250ms between steps
#   reset                  high→max (remove ceiling; min unchanged)
#   finalize               lock sweet spot: min→current high, then reset
#
# Aliases: 'set' = 'high', 'floor' = 'min'
#
# Game slice — production config:
#   soulmask-mempress.sh apply 5G 6G
#   # persist: sed -i 's/SOULMASK_MIN=.*/.../' /usr/local/sbin/setup-cgroups.sh
#
# Pak slice — calibrate hot-pak floor:
#   soulmask-pak-mempress.sh down 20    # lower ceiling 500M (20×25M), watching refaults
#   soulmask-pak-mempress.sh finalize   # lock min to where refaults stayed near-zero
#
# Calibration workflow:
#   1. min <low_value>     set panic floor below expected sweet spot
#   2. down [N]            step ceiling down; N steps of 25M with 250ms between
#   3. up [N]              ease up if refaults spike or lag reported
#   4. finalize            lock: memory.min → current high, high → max

set -euo pipefail

# shellcheck source=/usr/local/sbin/soulmask-instance-lib.sh
LIB="${LIB:-/usr/local/sbin/soulmask-instance-lib.sh}"
if [ -f "$LIB" ]; then
  . "$LIB"
else
  echo "FATAL: $LIB not found (expected alongside this script)"; exit 1
fi

STEP=$(( 25 * 1048576 ))   # 25M per calibration step
STEP_DELAY=0.25            # seconds between steps (smooth compression bursts)

# ── slice + instance selection ────────────────────────────────────────────────
PAK_CG="/sys/fs/cgroup/soulmask.slice/soulmask-paks.slice"

SLICE="game"
[[ "$(basename "$0")" == *pak* ]] && SLICE="pak"
SEL=""
while :; do
  case "${1:-}" in
    --slice) SLICE="$2"; shift 2 ;;
    -c)      SEL="$2"; shift 2 ;;
    *)       break ;;
  esac
done
case "$SLICE" in game|pak) ;; *) echo "Unknown slice: $SLICE (use 'game' or 'pak')"; exit 1 ;; esac

# Resolve the target instance BEFORE defining _cg(), not inside it: a
# `CG=$(_cg)` call runs _cg in a subshell, so any variable it set there
# (e.g. the selected cid) would not survive back into this shell.
SELECTED_CID=""
if [ "$SLICE" = "game" ]; then
  SELECTED_CID=$(soulmask_select_instance "$SEL") || { echo "Cgroup not found (slice: $SLICE)"; exit 1; }
  echo "[mempress] instance: $(soulmask_uuid_of "$SELECTED_CID") ($SELECTED_CID)" >&2
fi

_cg() {
  if [ "$SLICE" = "pak" ]; then
    echo "$PAK_CG"
    return
  fi
  soulmask_cgroup_of "$SELECTED_CID"
}

CG=$(_cg) || { echo "Cgroup not found (slice: $SLICE)"; exit 1; }
[ -d "$CG" ] || { echo "Cgroup directory not found: $CG"; exit 1; }

_parse_bytes() {
  local v="$1"
  case "$v" in
    *G) echo $(( ${v%G} * 1073741824 )) ;;
    *M) echo $(( ${v%M} * 1048576 )) ;;
    *)  echo "$v" ;;
  esac
}
_mb() { echo $(( $1 / 1048576 )); }

_show() {
  local cur high min zpool out refault
  cur=$(cat "$CG/memory.current")
  high=$(cat "$CG/memory.high")
  min=$(cat "$CG/memory.min")
  refault=$(grep '^workingset_refault_anon' "$CG/memory.stat" | awk '{print $2}')
  zpool=$(cat "$CG/memory.zswap.current")
  out=$(cat "$CG/memory.swap.current")

  echo ""
  printf "  [%s]\n" "$SLICE"
  printf "  RAM now:    %dM\n" $(_mb "$cur")
  if [ "$high" = "max" ]; then
    printf "  high:       max  (no ceiling)\n"
  else
    printf "  high:       %dM  ← ceiling  [headroom: %dM]\n" \
      $(_mb "$high") $(( ($high - $cur) / 1048576 ))
  fi
  printf "  min:        %dM  (guaranteed uncompressed floor)\n" $(_mb "$min")
  if [ "$SLICE" = "game" ]; then
    low=$(cat "$CG/memory.low")
    printf "  low:        %dM  (soft global-pressure hint)\n" $(_mb "$low")
  fi
  printf "  z_pool:     %dM compressed in zswap\n" $(_mb "$zpool")
  if [ "$SLICE" = "pak" ]; then
    printf "  out:        %dM not in RAM  (writeback=1: may include disk pages)\n" $(_mb "$out")
    # If z_pool=0 and out>0 → pak pages are on disk, not in zswap
    if [ "$zpool" -eq 0 ] && [ "$out" -gt 0 ]; then
      printf "              ^ z_pool=0 → all out-of-RAM pak is on DISK\n"
    fi
  else
    printf "  out:        %dM not in RAM  (writeback=0: all in zswap, none on disk)\n" $(_mb "$out")
  fi
  printf "  refault:    %s cumulative  (run soulmask-zswap-monitor.sh for rate)\n" "$refault"
  echo ""
}

# ── commands ──────────────────────────────────────────────────────────────────

CMD="${1:-status}"

case "$CMD" in
  status|"")
    _show ;;

  min|floor)
    val="${2:?usage: soulmask-mempress.sh [--slice game|pak] min <value>   e.g. min 5G}"
    bytes=$(_parse_bytes "$val")
    cur_min=$(cat "$CG/memory.min")
    cur_high=$(cat "$CG/memory.high")
    if [ "$cur_high" != "max" ] && [ "$bytes" -ge "$cur_high" ]; then
      printf "STOP: min %dM ≥ ceiling %dM. Raise the ceiling first or use 'apply'.\n" \
        $(_mb "$bytes") $(_mb "$cur_high")
      exit 1
    fi
    dir="raised"; [ "$bytes" -lt "$cur_min" ] && dir="lowered"
    printf "memory.min: %dM → %dM  (%s)\n" $(_mb "$cur_min") $(_mb "$bytes") "$dir"
    echo "$bytes" > "$CG/memory.min"
    sleep 1; _show ;;

  high|set)
    val="${2:?usage: soulmask-mempress.sh [--slice game|pak] high <value>   e.g. high 6G}"
    bytes=$(_parse_bytes "$val")
    min=$(cat "$CG/memory.min")
    if [ "$bytes" -le "$min" ]; then
      printf "STOP: ceiling %dM ≤ floor %dM. Lower the floor first or use 'apply'.\n" \
        $(_mb "$bytes") $(_mb "$min")
      exit 1
    fi
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && cur_high_str="max" || cur_high_str="$(_mb "$cur_high")M"
    printf "memory.high: %s → %dM\n" "$cur_high_str" $(_mb "$bytes")
    echo "$bytes" > "$CG/memory.high"
    sleep 1; _show ;;

  apply)
    min_val="${2:?usage: soulmask-mempress.sh apply <min> <high>   e.g. apply 5G 6G}"
    high_val="${3:?usage: soulmask-mempress.sh apply <min> <high>   e.g. apply 5G 6G}"
    min_bytes=$(_parse_bytes "$min_val")
    high_bytes=$(_parse_bytes "$high_val")
    if [ "$min_bytes" -ge "$high_bytes" ]; then
      printf "STOP: min %dM must be < high %dM\n" $(_mb "$min_bytes") $(_mb "$high_bytes")
      exit 1
    fi
    printf "memory.min:  → %dM\n" $(_mb "$min_bytes")
    printf "memory.high: → %dM\n" $(_mb "$high_bytes")
    echo "$min_bytes"  > "$CG/memory.min"
    echo "$high_bytes" > "$CG/memory.high"
    sleep 1; _show

    # Normalise to G if exactly divisible
    _fmt() { local b="$1" g=$(( $1 / 1073741824 ))
      [ $(( g * 1073741824 )) -eq "$b" ] && echo "${g}G" || echo "$(_mb "$b")M"; }
    if [ "$SLICE" = "game" ]; then
      uuid=$(soulmask_uuid_of "$SELECTED_CID")
      echo "To persist across restarts (instance $uuid):"
      echo "  edit /etc/gstammtisch/instances.d/${uuid}.env :"
      echo "    SOULMASK_MIN=$(_fmt "$min_bytes")"
      echo "    SOULMASK_HIGH=$(_fmt "$high_bytes")"
      echo "  systemctl restart gstammtisch-cgroups   # or wait for soulmask-cgroup-watcher to re-apply"
    fi ;;

  down)
    N="${2:-1}"
    min=$(cat "$CG/memory.min")
    i=0
    while [ "$i" -lt "$N" ]; do
      cur_high=$(cat "$CG/memory.high")
      [ "$cur_high" = "max" ] && cur_high=$(cat "$CG/memory.current")
      aligned=$(( (cur_high / STEP) * STEP ))
      next=$(( aligned - STEP ))
      if [ "$next" -le "$min" ]; then
        printf "STOP at step %d/%d: next %dM would hit floor (%dM).\n" \
          $(( i + 1 )) "$N" $(_mb "$next") $(_mb "$min")
        printf "  soulmask-mempress.sh min <lower_value>  to set a lower floor\n"
        break
      fi
      printf "  [%d/%d] %dM → %dM\n" $(( i + 1 )) "$N" $(_mb "$cur_high") $(_mb "$next")
      echo "$next" > "$CG/memory.high"
      i=$(( i + 1 ))
      [ "$i" -lt "$N" ] && sleep "$STEP_DELAY"
    done
    sleep 0.5; _show ;;

  up)
    N="${2:-1}"
    cur_high=$(cat "$CG/memory.high")
    if [ "$cur_high" = "max" ]; then
      echo "Already at max — no ceiling to step up from."
      echo "Use 'high <val>' to set a ceiling first."
      _show; exit 0
    fi
    i=0
    while [ "$i" -lt "$N" ]; do
      cur=$(cat "$CG/memory.high")
      [ "$cur" = "max" ] && break
      next=$(( cur + STEP ))
      printf "  [%d/%d] %dM → %dM\n" $(( i + 1 )) "$N" $(_mb "$cur") $(_mb "$next")
      echo "$next" > "$CG/memory.high"
      i=$(( i + 1 ))
      [ "$i" -lt "$N" ] && sleep "$STEP_DELAY"
    done
    sleep 0.5; _show ;;

  reset)
    cur_high=$(cat "$CG/memory.high")
    [ "$cur_high" = "max" ] && { echo "Already at max — no ceiling active."; _show; exit 0; }
    printf "memory.high: %dM → max  (ceiling removed; floor unchanged)\n" $(_mb "$cur_high")
    echo max > "$CG/memory.high"
    echo "  Floor (memory.min) still at $(( $(cat "$CG/memory.min") / 1048576 ))M." ;;

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
    sleep 1; _show
    echo "To persist:"
    if [ "$SLICE" = "pak" ]; then
      echo "  Update MemoryMin= in /etc/systemd/system/soulmask-paks.slice to ${sweet}M"
      echo "  systemctl daemon-reload && systemctl restart soulmask-paks.slice"
    else
      uuid=$(soulmask_uuid_of "$SELECTED_CID")
      echo "  edit /etc/gstammtisch/instances.d/${uuid}.env :"
      echo "    SOULMASK_MIN=${sweet}M"
      echo "    # Also set a ceiling: SOULMASK_HIGH ~$(( sweet + 1024 ))M"
      echo "  systemctl restart gstammtisch-cgroups"
    fi ;;

  *)
    cat <<USAGE
Usage: soulmask-mempress.sh [--slice game|pak] [-c <uuid-or-prefix>] <command> [args]

  status                 show all knobs and zswap stats
  min <val>              set memory.min (floor)        e.g. min 5G
  high <val>             set memory.high (ceiling)     e.g. high 6G
  apply <min> <high>     set both at once              e.g. apply 5G 6G
  down [N]               step ceiling down N×25M       e.g. down 20  (=500M over 5s)
  up [N]                 step ceiling up N×25M
  reset                  remove ceiling (high → max)
  finalize               lock calibration: min→ceiling, high→max

Aliases: 'set' = 'high', 'floor' = 'min'
Pak slice shortcut:  soulmask-pak-mempress.sh <command> [args]

-c <uuid-or-prefix>: select which Soulmask instance (--slice game only).
  Required if more than one instance is running; auto-selected if only one.
USAGE
    exit 1 ;;
esac
