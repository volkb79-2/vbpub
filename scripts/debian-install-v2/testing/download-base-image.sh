#!/usr/bin/env bash
# Download (and checksum-verify) Debian's own official genericcloud qcow2
# image -- the same shape of pre-built cloud image a real hosting provider
# like Netcup deploys, which is exactly the point: this is a REAL boot
# (bootloader, initramfs, kernel), not something built from scratch with
# debootstrap, and it's the same kind of image the real Case B live-test
# hosts are provisioned from. The image is a shared download: it lives in
# the repo's shared base-image folder (the `vm-images` entry of the repo path
# config, asked of libraries/cowtree/repo-path: the plain downloads/vm-images
# folder, or its CoW subvolume on a CoW host; the repo root is derived by fixed
# depth from this script). The in-runner VM wrapper passes its mount point of
# that folder as the optional argument (an explicit argument always wins).
# Concurrent runs are serialized with flock. A second run of
# testing/vm/vmctl prepare-base doesn't re-download; it only refetches if
# the file is missing or its checksum doesn't match what's currently
# published (Debian repoints
# "latest" at a new build periodically).
#
# Usage: testing/download-base-image.sh [images-dir]
# Prints the path to the verified local qcow2 on success.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# HERE is <repo>/scripts/debian-install-v2/testing: fixed depth, no git needed.
REPO_ROOT="$(cd "$HERE/../../.." && pwd -P)"
# The default folder is the resolved `vm-images` path; it is asked only when no
# argument was given (the in-runner wrapper has no repo checkout to ask).
if [ -n "${1:-}" ]; then
    DOWNLOADS_DIR="$1"
else
    err_file="$(mktemp)"
    if ! DOWNLOADS_DIR="$("$REPO_ROOT/libraries/cowtree/repo-path" vm-images --repo-root "$REPO_ROOT" 2>"$err_file")"; then
        echo "download-base-image: cannot resolve the vm-images folder from the repo path config of $REPO_ROOT: $(tr '\n' ' ' <"$err_file")(pass an images dir argument to override)" >&2
        rm -f "$err_file"
        exit 1
    fi
    rm -f "$err_file"
fi
IMAGE_NAME="debian-13-genericcloud-amd64.qcow2"
BASE_URL="https://cloud.debian.org/images/cloud/trixie/latest"
LOCAL_PATH="$DOWNLOADS_DIR/$IMAGE_NAME"
SUMS_PATH="$DOWNLOADS_DIR/SHA512SUMS"

[ -d "$DOWNLOADS_DIR" ] || { echo "images dir $DOWNLOADS_DIR is missing (this script never creates it: 'mkdir -p $DOWNLOADS_DIR'; in plain mode the resolved vm-images folder is the committed downloads/vm-images placeholder of the checkout; on a CoW host run 'libraries/cowtree/repo-path vm-images --provision' to create the subvolume)" >&2; exit 1; }
# One shared images folder: serialize fetch/verify/rename across processes.
exec 9>"$DOWNLOADS_DIR/.download.lock"
flock 9

echo "+ fetching current SHA512SUMS" >&2
wget -q -O "$SUMS_PATH.new" "$BASE_URL/SHA512SUMS"
mv "$SUMS_PATH.new" "$SUMS_PATH"

expected="$(grep " \*\?$IMAGE_NAME\$" "$SUMS_PATH" | awk '{print $1}')"
if [ -z "$expected" ]; then
    echo "could not find $IMAGE_NAME in SHA512SUMS" >&2
    exit 1
fi

if [ -f "$LOCAL_PATH" ]; then
    actual="$(sha512sum "$LOCAL_PATH" | awk '{print $1}')"
    if [ "$actual" = "$expected" ]; then
        echo "$LOCAL_PATH"
        exit 0
    fi
    echo "+ cached image checksum stale, re-downloading" >&2
    rm -f "$LOCAL_PATH"
fi

echo "+ downloading $IMAGE_NAME (this is a one-time cost, ~400-600MB)" >&2
wget -q -O "$LOCAL_PATH.part" "$BASE_URL/$IMAGE_NAME"
actual="$(sha512sum "$LOCAL_PATH.part" | awk '{print $1}')"
if [ "$actual" != "$expected" ]; then
    echo "checksum mismatch after download: expected $expected, got $actual" >&2
    rm -f "$LOCAL_PATH.part"
    exit 1
fi
mv "$LOCAL_PATH.part" "$LOCAL_PATH"
echo "$LOCAL_PATH"
