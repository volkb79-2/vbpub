#!/bin/bash
# cgroup-list-io-limits.sh [slice|abs-path] [--all]   e.g.  cgroup-list-io-limits.sh dev.slice --all
set -u
arg=${1:-dev.slice}; all=${2:-}
case $arg in /*) root=$arg ;; *) root=/sys/fs/cgroup/$arg ;; esac
[ -d "$root" ] || { echo "no such cgroup: $root" >&2; exit 1; }
declare -A devname
while read -r mm name; do devname[$mm]=$name; done < <(lsblk -dnro MAJ:MIN,NAME)
h() { case $1 in max|-) echo "$1" ;; *) numfmt --to=iec --suffix=B/s "$1" ;; esac; }
{
  printf 'CGROUP\tDEV\tRBPS\tWBPS\tRIOPS\tWIOPS\tWEIGHT\tLAT_TARGET_us\n'
  find "$root" -type d | sort | while read -r cg; do
    rel=$(sed -E "s/docker-([0-9a-f]{12})[0-9a-f]{52}\.scope/docker-\1.scope/" <<<"${cg#/sys/fs/cgroup/}")
    if [ ! -e "$cg/io.max" ]; then
      [ -n "$all" ] && printf '%s\t(io controller off)\t-\t-\t-\t-\t-\t-\n' "$rel"; continue
    fi
    w=$(awk '$1=="default"{print $2}' "$cg/io.weight" 2>/dev/null); w=${w:--}
    declare -A lat=()
    [ -r "$cg/io.latency" ] && while read -r mm t; do lat[$mm]=${t#target=}; done < "$cg/io.latency"
    # cgroupfs files always report size 0, so test content, never [ -s ].
    iomax=$(cat "$cg/io.max")
    if [ -n "$iomax" ]; then
      while read -r mm rest; do
        declare -A v=([rbps]=max [wbps]=max [riops]=max [wiops]=max)
        for kv in $rest; do v[${kv%%=*}]=${kv#*=}; done
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$rel" "${devname[$mm]:-$mm}" \
          "$(h "${v[rbps]}")" "$(h "${v[wbps]}")" "${v[riops]}" "${v[wiops]}" "$w" "${lat[$mm]:--}"
        unset 'lat[$mm]'
      done <<<"$iomax"
    elif [ -n "$all" ] && [ ${#lat[@]} -eq 0 ]; then
      printf '%s\t-\tmax\tmax\tmax\tmax\t%s\t-\n' "$rel" "$w"
    fi
    for mm in "${!lat[@]}"; do
      printf '%s\t%s\tmax\tmax\tmax\tmax\t%s\t%s\n' "$rel" "${devname[$mm]:-$mm}" "$w" "${lat[$mm]}"
    done
    unset lat
  done
} | column -t -s $'\t'

