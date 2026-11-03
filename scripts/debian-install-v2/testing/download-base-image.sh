#!/usr/bin/env bash
# Download (and checksum-verify) Debian's own official genericcloud qcow2
# image -- the same shape of pre-built cloud image a real hosting provider
# like Netcup deploys, which is exactly the point: this is a REAL boot
# (bootloader, initramfs, kernel), not something built from scratch with
# debootstrap, and it's the same kind of image the real Case B live-test
# hosts are provisioned from. The image is a shared download: it lives in
# the gitignored downloads/ folder of the MAIN checkout (the parent of git's
# common dir, so every worktree shares one copy; outside git: the script-relative
# repo root with an INFO line; override: MDT_DOWNLOADS_DIR, which the VM wrapper
# sets to its downloads bind mount). Concurrent runs are serialized with flock. A second run of
# testing/vm/vmctl prepare-base doesn't re-download; it only refetches if
# the file is missing or its checksum doesn't match what's currently
# published (Debian repoints
# "latest" at a new build periodically).
#
# Usage: testing/download-base-image.sh
# Prints the path to the verified local qcow2 on success.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# HERE is <repo>/scripts/debian-install-v2/testing: fixed depth, no git needed.
REPO_ROOT="$(cd "$HERE/../../.." && pwd -P)"
if [ -n "${MDT_DOWNLOADS_DIR:-}" ]; then
    DOWNLOADS_DIR="$MDT_DOWNLOADS_DIR"
else
    # Shared downloads live at the MAIN checkout root, so every worktree reuses
    # one copy. Outside git (or an unusual layout) use the script-relative root.
    git_common="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [ -n "$git_common" ] && [ "$(basename "$git_common")" = ".git" ]; then
        DOWNLOADS_DIR="$(cd "$(dirname "$git_common")" && pwd -P)/downloads"
    else
        echo "INFO: no usable git common dir for $REPO_ROOT; downloads go to $REPO_ROOT/downloads (set MDT_DOWNLOADS_DIR to override)" >&2
        DOWNLOADS_DIR="$REPO_ROOT/downloads"
    fi
fi
IMAGE_NAME="debian-13-genericcloud-amd64.qcow2"
BASE_URL="https://cloud.debian.org/images/cloud/trixie/latest"
LOCAL_PATH="$DOWNLOADS_DIR/$IMAGE_NAME"
SUMS_PATH="$DOWNLOADS_DIR/SHA512SUMS"

mkdir -p "$DOWNLOADS_DIR" || { echo "cannot create downloads dir $DOWNLOADS_DIR (set MDT_DOWNLOADS_DIR)" >&2; exit 1; }
# One shared downloads folder: serialize fetch/verify/rename across worktrees.
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
