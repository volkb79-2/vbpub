#!/usr/bin/env bash
# Build the cloud-init NoCloud seed ISO (volume id "cidata") that drives the PoC builder guest:
# user-data and meta-data from seed/, plus poc/install-btrfs.sh and the complete poc/files/ payload
# under poc/ on the medium (the guest mounts /dev/sr0 at /mnt/seed and runs
# /mnt/seed/poc/install-btrfs.sh).
#   build-seed.sh [--bootloader grub|systemd-boot] OUTPUT.iso
#
# The medium also carries a one-line `bootloader` marker. Its default is grub, matching the
# installer default; the builder guest reads it and passes the selected value explicitly.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEED_STAGE=""

stage_seed() { # stage_seed STAGE [grub|systemd-boot] [FILES_SOURCE]
  local stage=$1 bootloader=${2:-grub} source=${3:-$HERE/../files}
  case "$bootloader" in
    grub|systemd-boot) ;;
    *)
      echo "build-seed: --bootloader must be grub|systemd-boot, got '$bootloader'" >&2
      return 2
      ;;
  esac
  [ -d "$source" ] || {
    echo "build-seed: files payload source $source is missing" >&2
    return 1
  }
  mkdir -p "$stage/poc/files"
  cp "$HERE/seed/user-data" "$HERE/seed/meta-data" "$stage/"
  cp "$HERE/install-btrfs.sh" "$stage/poc/"
  cp -a "$source/." "$stage/poc/files/"
  find "$stage/poc/files" \
    \( -type d -name __pycache__ -o -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
    -prune -exec rm -rf -- {} +
  printf '%s\n' "$bootloader" >"$stage/bootloader"
}

main() {
  set -euo pipefail
  local out="" bootloader=grub
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
  SEED_STAGE=$(mktemp -d)
  trap 'if [ -n "${SEED_STAGE:-}" ]; then rm -rf -- "$SEED_STAGE"; fi' EXIT
  stage_seed "$SEED_STAGE" "$bootloader"
  xorriso -as mkisofs -quiet -volid cidata -joliet -rock -uid 0 -gid 0 -o "$out" "$SEED_STAGE"
  sha256sum "$out"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
