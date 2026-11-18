#!/usr/bin/env bash
# Build the cloud-init NoCloud seed ISO (volume id "cidata") that drives the PoC builder guest:
# user-data and meta-data from seed/, plus poc/install-btrfs.sh and poc/dr-state under poc/ on the
# medium (the guest mounts /dev/sr0 at /mnt/seed and runs /mnt/seed/poc/install-btrfs.sh).
#   build-seed.sh [--bootloader grub|systemd-boot] OUTPUT.iso
#
# The medium also carries a one-line `bootloader` marker. Its default is grub, matching the
# installer default; the builder guest reads it and passes the selected value explicitly.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

stage_seed() { # stage_seed STAGE [grub|systemd-boot]
  local stage=$1 bootloader=${2:-grub}
  case "$bootloader" in
    grub|systemd-boot) ;;
    *)
      echo "build-seed: --bootloader must be grub|systemd-boot, got '$bootloader'" >&2
      return 2
      ;;
  esac
  mkdir -p "$stage/poc"
  cp "$HERE/seed/user-data" "$HERE/seed/meta-data" "$stage/"
  cp "$HERE/install-btrfs.sh" "$HERE/dr-state" "$stage/poc/"
  printf '%s\n' "$bootloader" >"$stage/bootloader"
}

main() {
  set -euo pipefail
  local out="" bootloader=grub stage
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --bootloader)
        [ "$#" -ge 2 ] || { echo "build-seed: --bootloader needs grub|systemd-boot" >&2; return 2; }
        bootloader=$2
        shift 2
        ;;
      --bootloader=*)
        bootloader=${1#--bootloader=}
        shift
        ;;
      --)
        shift
        break
        ;;
      -*)
        echo "build-seed: unknown option '$1'; usage: build-seed.sh [--bootloader grub|systemd-boot] OUTPUT.iso" >&2
        return 2
        ;;
      *)
        [ -z "$out" ] || { echo "build-seed: too many arguments; usage: build-seed.sh [--bootloader grub|systemd-boot] OUTPUT.iso" >&2; return 2; }
        out=$1
        shift
        ;;
    esac
  done
  [ "$#" -eq 0 ] || { echo "build-seed: too many arguments; usage: build-seed.sh [--bootloader grub|systemd-boot] OUTPUT.iso" >&2; return 2; }
  [ -n "$out" ] || { echo "build-seed: usage: build-seed.sh [--bootloader grub|systemd-boot] OUTPUT.iso" >&2; return 2; }
  case "$bootloader" in
    grub|systemd-boot) ;;
    *)
      echo "build-seed: --bootloader must be grub|systemd-boot, got '$bootloader'" >&2
      return 2
      ;;
  esac
  [ ! -e "$out" ] || { echo "build-seed: $out exists; refusing to overwrite" >&2; return 1; }
  stage=$(mktemp -d)
  trap 'if [ -n "${stage:-}" ]; then rm -rf -- "$stage"; fi' EXIT
  stage_seed "$stage" "$bootloader"
  xorriso -as mkisofs -quiet -volid cidata -joliet -rock -o "$out" "$stage"
  sha256sum "$out"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
