#!/usr/bin/env bash
# mdt-cgroup-io-limits.sh — read-only listing of the io.max limits a cgroup
# subtree really has, with the EFFECTIVE limit (the tightest value along the
# ancestor path, per device) next to each one.  Installed by install.sh as
# /usr/local/sbin/mdt-cgroup-io-limits.sh.
#
#   mdt-cgroup-io-limits.sh [slice|abs-path] [--all] [--systemd]
#     slice        a unit name (dev.slice, dev-gates.slice) or an absolute
#                  cgroup path; default dev.slice
#     --all        also list cgroups with no own entry (shows the inherited
#                  EFFECTIVE limit) and the ones whose io controller is off
#     --systemd    for every slice/scope listed also print systemd's view:
#                  the IO*Max properties and the drop-in paths that define them
#                  (an empty runtime drop-in in /run/systemd/system.control is
#                  exactly what "dev.slice has no cap" looked like on 2026-10-19)
#
# Environment: CGROUP_ROOT (default /sys/fs/cgroup; also a test seam).
#
# WHY THIS READS CONTENT AND NEVER TESTS FILE SIZE: cgroupfs control files
# always report size 0, whatever they hold.  Testing a control file's size
# (the shell "-s" file test) is therefore always false, so a listing built on
# it never shows a limit — the bug the first operator script had.  Every read
# below is `cat`, and the tests run this script against files that really
# report size 0.
set -u
ROOT="${CGROUP_ROOT:-/sys/fs/cgroup}"
arg=""; all=""; sysd=""
for a in "$@"; do
  case "$a" in
    --all) all=1 ;;
    --systemd) sysd=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*) echo "unknown option: $a" >&2; exit 2 ;;
    *) arg="$a" ;;
  esac
done
arg="${arg:-dev.slice}"
case "$arg" in
  /*) top="$arg" ;;
  *)
    top="$ROOT/$arg"
    [ -d "$top" ] || top=$(find "$ROOT" -maxdepth 4 -type d -name "$arg" 2>/dev/null | head -n 1)
    ;;
esac
[ -n "$top" ] && [ -d "$top" ] || { echo "no such cgroup: $arg (under $ROOT)" >&2; exit 1; }

declare -A devname
if command -v lsblk >/dev/null 2>&1; then
  while read -r mm name; do devname[$mm]=$name; done < <(lsblk -dnro MAJ:MIN,NAME 2>/dev/null)
fi
human() { # bytes/s -> IEC, "max" and "-" pass through
  case "$1" in
    max|-) echo "$1" ;;
    *) if command -v numfmt >/dev/null 2>&1; then numfmt --to=iec --suffix=B/s "$1"; else echo "$1"; fi ;;
  esac
}
short() { sed -E 's/docker-([0-9a-f]{12})[0-9a-f]{52}\.scope/docker-\1.scope/' <<<"$1"; }

# effective_for <cgroup-dir> <maj:min> -> "rbps wbps riops wiops": the minimum
# of each field over the cgroup and every ancestor up to ROOT.
effective_for() {
  local dir="$1" mm="$2" cur content line m kv f v
  local -A min=([rbps]=max [wbps]=max [riops]=max [wiops]=max)
  cur="$dir"
  while :; do
    content=$(cat "$cur/io.max" 2>/dev/null)
    while read -r m line; do
      [ "$m" = "$mm" ] || continue
      for kv in $line; do
        f=${kv%%=*}; v=${kv#*=}
        case "$f" in rbps|wbps|riops|wiops) ;; *) continue ;; esac
        [ "$v" = max ] && continue
        if [ "${min[$f]}" = max ] || [ "$v" -lt "${min[$f]}" ]; then min[$f]=$v; fi
      done
    done <<<"$content"
    [ "$cur" = "$ROOT" ] || [ "$cur" = "/" ] && break
    cur=$(dirname "$cur")
    case "$cur" in "$ROOT"*) ;; *) break ;; esac
  done
  echo "${min[rbps]} ${min[wbps]} ${min[riops]} ${min[wiops]}"
}

# devices_for <cgroup-dir>: every maj:min named by the cgroup or an ancestor
devices_for() {
  local cur="$1" out=""
  while :; do
    out+=$'\n'$(awk '{print $1}' "$cur/io.max" 2>/dev/null)
    [ "$cur" = "$ROOT" ] || [ "$cur" = "/" ] && break
    cur=$(dirname "$cur")
    case "$cur" in "$ROOT"*) ;; *) break ;; esac
  done
  printf '%s\n' "$out" | grep -E '^[0-9]+:[0-9]+$' | sort -u
}

row() { printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$@"; }
{
  row CGROUP DEV RBPS WBPS RIOPS WIOPS EFFECTIVE WEIGHT LAT_TARGET_us
  while read -r cg; do
    rel=$(short "${cg#"$ROOT"/}")
    [ "$cg" = "$ROOT" ] && rel=/
    if [ ! -e "$cg/io.max" ]; then
      [ -n "$all" ] && row "$rel" "(io controller off)" - - - - - - -
      continue
    fi
    w=$(awk '$1=="default"{print $2}' "$cg/io.weight" 2>/dev/null); w=${w:--}
    declare -A lat=()
    if [ -r "$cg/io.latency" ]; then
      while read -r mm t; do [ -n "$mm" ] && lat[$mm]=${t#target=}; done < <(cat "$cg/io.latency" 2>/dev/null)
    fi
    iomax=$(cat "$cg/io.max" 2>/dev/null)
    own=""
    while read -r mm rest; do
      [ -n "$mm" ] || continue
      own+=" $mm"
      declare -A v=([rbps]=max [wbps]=max [riops]=max [wiops]=max)
      for kv in $rest; do v[${kv%%=*}]=${kv#*=}; done
      read -r er ew eri ewi <<<"$(effective_for "$cg" "$mm")"
      row "$rel" "${devname[$mm]:-$mm}" "$(human "${v[rbps]}")" "$(human "${v[wbps]}")" \
        "${v[riops]}" "${v[wiops]}" \
        "r=$(human "$er") w=$(human "$ew") ri=$eri wi=$ewi" "$w" "${lat[$mm]:--}"
      unset 'lat[$mm]'
      unset v
    done <<<"$iomax"
    if [ -n "$all" ]; then
      # inherited-only devices: no own entry, but an ancestor limits them
      while read -r mm; do
        [ -n "$mm" ] || continue
        case " $own " in *" $mm "*) continue ;; esac
        read -r er ew eri ewi <<<"$(effective_for "$cg" "$mm")"
        row "$rel" "${devname[$mm]:-$mm}" max max max max \
          "r=$(human "$er") w=$(human "$ew") ri=$eri wi=$ewi" "$w" "${lat[$mm]:--}"
      done < <(devices_for "$cg")
      [ -z "$own" ] && [ -z "$(devices_for "$cg")" ] && row "$rel" - max max max max "unlimited" "$w" -
    fi
    for mm in "${!lat[@]}"; do
      row "$rel" "${devname[$mm]:-$mm}" max max max max - "$w" "${lat[$mm]}"
    done
    unset lat
  done < <(find "$top" -type d | sort)
} | if command -v column >/dev/null 2>&1; then column -t -s $'\t'; else cat; fi

if [ -n "$sysd" ]; then
  echo
  echo "== systemd view (IO properties and the drop-ins that define them) =="
  while read -r cg; do
    unit=${cg##*/}
    case "$unit" in *.slice|*.scope) ;; *) continue ;; esac
    unit=$(short "$unit")
    case "$unit" in docker-*.scope) unit=${cg##*/} ;; esac
    echo "-- $unit"
    systemctl show "$unit" \
      -p IOReadBandwidthMax -p IOWriteBandwidthMax -p IOReadIOPSMax -p IOWriteIOPSMax -p DropInPaths 2>&1 \
      | sed 's/^/   /'
  done < <(find "$top" -type d | sort)
fi
