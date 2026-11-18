#!/usr/bin/env bash
# Build the cloud-init NoCloud seed ISO (volume id "cidata") that drives the PoC builder guest:
# user-data and meta-data from seed/, plus poc/install-btrfs.sh and poc/dr-state under poc/ on the
# medium (the guest mounts /dev/sr0 at /mnt/seed and runs /mnt/seed/poc/install-btrfs.sh).
#   build-seed.sh OUTPUT.iso
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out=${1:?usage: build-seed.sh OUTPUT.iso}
[ ! -e "$out" ] || { echo "build-seed: $out exists; refusing to overwrite" >&2; exit 1; }
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/poc"
cp "$HERE/seed/user-data" "$HERE/seed/meta-data" "$stage/"
cp "$HERE/install-btrfs.sh" "$HERE/dr-state" "$stage/poc/"
xorriso -as mkisofs -quiet -volid cidata -joliet -rock -o "$out" "$stage"
sha256sum "$out"
