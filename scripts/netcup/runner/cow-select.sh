#!/usr/bin/env bash
# Description: pure helpers for finding the install-plan CoW partition; sourced by the runner.
# shellcheck shell=bash

COW_LABEL=MDT_COW

# cow_select_device < `lsblk -P -o PATH,PARTLABEL DISK`
# Resolve exactly one planned partition by PARTLABEL. Never infer a device number or filesystem label.
# Prints the device path; returns 1 when there is none and 3 when there are several.
cow_select_device() {
  local line path partlabel found=()
  while IFS= read -r line; do
    path="" partlabel=""
    # lsblk -P prints KEY="value" pairs; extract the two known fields without
    # evaluating device-controlled output or depending on Bash regex quoting.
    if [[ $line == *'PATH="'* ]]; then
      path=${line#*'PATH="'}
      path=${path%%\"*}
    fi
    if [[ $line == *'PARTLABEL="'* ]]; then
      partlabel=${line#*'PARTLABEL="'}
      partlabel=${partlabel%%\"*}
    fi
    [[ -n $path ]] || continue
    [[ $partlabel == "$COW_LABEL" ]] && found+=("$path")
  done
  case ${#found[@]} in
    0) return 1 ;;
    1) printf '%s\n' "${found[0]}" ;;
    *) echo "several PARTLABEL=$COW_LABEL partitions (${found[*]}); refusing to choose" >&2; return 3 ;;
  esac
}

# cow_fstab_entry_ok EXISTING EXPECTED UUID DEVICE
# The runner accepts its exact UUID entry or the equivalent entry written by the installer for this planned volume.
cow_fstab_entry_ok() {
  local existing=$1 expected=$2 uuid=$3 device=$4
  [[ $existing == "$expected" ]] && return 0
  local src _mp fstype
  read -r src _mp fstype _ <<<"$existing"
  [[ $fstype == btrfs ]] || return 1
  [[ $src == "UUID=$uuid" || $src == "LABEL=$COW_LABEL" || $src == "$device" ]]
}

# cow_selection_message DEVICE FSTYPE REUSED|FORMATTED
# One operator-facing line naming the selected CoW partition and what happened to it.
cow_selection_message() {
  local device=$1 fs_type=$2 action=$3
  [[ $action == reused || $action == formatted ]] || return 2
  printf 'selected MDT_COW by PARTLABEL: %s (%s, %s)\n' "$device" "$fs_type" "$action"
}
