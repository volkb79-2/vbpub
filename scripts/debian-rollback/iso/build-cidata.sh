#!/usr/bin/env bash
# build-cidata.sh --config dr-install.conf --out IMAGE
#
# Make the config medium the installer looks for first: a small image whose volume label is CIDATA holding
# dr-install.conf. For a physical host, write it to a USB stick yourself (`dd`, careful with the device name: this script
# never writes to a device and refuses a /dev path). For the guest tests it is attached read-only as a virtio disk
# (`vm-lab start --plain-host --config-disk`). ISO9660 (Rock Ridge) is used so that no root and no mount are needed to build it;
# dr-autoinstall mounts any device labelled CIDATA read-only, whatever its filesystem.
# The config is validated with dr-plan --check-config first (unknown keys, bad characters and duplicates are refused).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
die() { echo "build-cidata: $*" >&2; exit 1; }
CONF="" OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONF=${2:?}; shift 2 ;;
    --out) OUT=${2:?}; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -f "$CONF" ] || die "--config file not found"
[ -n "$OUT" ] || die "--out is required"
case "$OUT" in /dev/*) die "refusing to write to a device path: $OUT" ;; esac
[ ! -e "$OUT" ] || [ -f "$OUT" ] || die "$OUT exists and is not a regular file"
command -v xorriso >/dev/null || die "xorriso missing"
"$HERE/dr-plan" --check-config --config "$CONF" >/dev/null || die "the config does not validate (see dr-plan's message above)"

tmp=$(mktemp -d "${TMPDIR:-/tmp}/cidata.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/tree"   # only this directory is imaged; the error log lives next to it, never inside
install -m 0644 "$CONF" "$tmp/tree/dr-install.conf"
rm -f "$OUT.part"
xorriso -as mkisofs -quiet -V CIDATA -r -J -o "$OUT.part" "$tmp/tree" >/dev/null 2>"$tmp/err" || { cat "$tmp/err" >&2; rm -f "$OUT.part"; die "xorriso failed"; }
mv "$OUT.part" "$OUT"
echo "$OUT $(stat -c %s "$OUT") bytes"
