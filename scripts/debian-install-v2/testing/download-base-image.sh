#!/usr/bin/env bash
# Download (and checksum-verify) Debian's own official genericcloud qcow2
# image -- the same shape of pre-built cloud image a real hosting provider
# like Netcup deploys, which is exactly the point: this is a REAL boot
# (bootloader, initramfs, kernel), not something built from scratch with
# debootstrap, and it's the same kind of image the real Case B live-test
# hosts are provisioned from. Cached under testing/.cache/ so a second run
# of testing/vm/vmctl prepare-base doesn't re-download; only refetches if
# missing or the
# checksum doesn't match what's currently published (Debian repoints
# "latest" at a new build periodically).
#
# Usage: testing/download-base-image.sh
# Prints the path to the verified local qcow2 on success.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="$HERE/.cache"
IMAGE_NAME="debian-13-genericcloud-amd64.qcow2"
BASE_URL="https://cloud.debian.org/images/cloud/trixie/latest"
LOCAL_PATH="$CACHE_DIR/$IMAGE_NAME"
SUMS_PATH="$CACHE_DIR/SHA512SUMS"

mkdir -p "$CACHE_DIR"

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
