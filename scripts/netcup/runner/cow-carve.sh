#!/usr/bin/env bash
# Description: pure helpers for provision-runner.sh's cow-partition carve (sourced, never executed; they read stdin so tests need no devices).
# shellcheck shell=bash

COW_LABEL=MDT_COW

# cow_pick_free_row MIN_MIB  < `parted -m -s DISK unit MiB print free`
# Prints "START_MIB:END_MIB" of the LARGEST free row (start rounded up, end rounded down to whole MiB).
# Returns 1 when there is no free row at all, 2 when the largest row is smaller than MIN_MIB (a tail sliver, a
# gap that is too small): never carve a useless partition.
cow_pick_free_row() {
  local min_mib=${1:?min MiB}
  local row
  row="$(awk -F: 'tolower($5) ~ /^free/ {s=int($2+0.999); e=int($3); if (e - s > best) {best=e - s; start=s; end=e}} END {if (start != "") print start ":" end}')"
  if [[ -z $row ]]; then
    echo "no free space row found" >&2
    return 1
  fi
  local start=${row%%:*} end=${row##*:}
  if (( end - start < min_mib )); then
    echo "largest free row is $(( end - start )) MiB (${start}..${end} MiB), below the required ${min_mib} MiB; refusing to carve a cow partition" >&2
    return 2
  fi
  printf '%s\n' "$row"
}

# cow_plan_range FREE_START_MIB FREE_END_MIB COW_MIB
# Prints "START:END": cow at the END of the free row (the reserve stays directly behind root). The row has already
# been checked to be at least COW_MIB, so the size is never silently clipped.
cow_plan_range() {
  local free_start=$1 free_end=$2 cow_mib=$3
  local cow_start=$(( free_end - cow_mib ))
  if (( cow_start < free_start )); then
    echo "free row ${free_start}..${free_end} MiB is smaller than the requested ${cow_mib} MiB" >&2
    return 2
  fi
  printf '%s:%s\n' "$cow_start" "$free_end"
}

# cow_select_device  < `lsblk -P -o PATH,PARTLABEL,FSTYPE,LABEL DISK`
# Idempotence key: PARTLABEL=MDT_COW, or an existing btrfs filesystem labelled MDT_COW. Never the device number.
# Prints the device path; returns 1 when there is none and 3 when there are several (never pick, never add one).
cow_select_device() {
  local line path partlabel fstype label found=()
  while IFS= read -r line; do
    path="" partlabel="" fstype="" label=""
    # lsblk -P prints KEY="value" pairs; parse with regexes (no eval of device-controlled text)
    [[ $line =~ (^|\ )PATH=\"([^\"]*)\" ]] && path=${BASH_REMATCH[2]}
    [[ $line =~ (^|\ )PARTLABEL=\"([^\"]*)\" ]] && partlabel=${BASH_REMATCH[2]}
    [[ $line =~ (^|\ )FSTYPE=\"([^\"]*)\" ]] && fstype=${BASH_REMATCH[2]}
    [[ $line =~ (^|\ )LABEL=\"([^\"]*)\" ]] && label=${BASH_REMATCH[2]}
    [[ -n $path ]] || continue
    if [[ $partlabel == "$COW_LABEL" || ( $fstype == btrfs && $label == "$COW_LABEL" ) ]]; then
      found+=("$path")
    fi
  done
  case ${#found[@]} in
    0) return 1 ;;
    1) printf '%s\n' "${found[0]}" ;;
    *) echo "several cow candidates (${found[*]}); refusing to choose" >&2; return 3 ;;
  esac
}
