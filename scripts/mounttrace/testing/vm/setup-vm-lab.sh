#!/usr/bin/env bash
# Link scripts/mounttrace/artifacts/vm-lab into the btrfs CoW area.
#
# Layout and contract: mdt-cockpit docs/BTRFS-COW.md, section 5 (tools address
# $MDT_COW_ROOT/<repo>/..., verify btrfs, never create the root) and section 6.2
# (a checkout path is linked to its CoW area as an ignored symlink; repository
# checkouts themselves stay on the normal filesystem).
#
#   $MDT_COW_ROOT/<repo>/vm-lab/<checkout>/mounttrace   (plain directory, 0700)
#     images/  sealed base images (case-b, case-b-ready, mounttrace-ready-v2)
#     runs/    one disposable overlay per test round
#     ssh/     the test-only guest key
#
# <checkout> is "main" for the main checkout, else the worktree directory name,
# so every worktree has its own VM state (as the harness requires).
# Idempotent. Refuses a non-empty real directory at the link path.
set -euo pipefail

die() { echo "setup-vm-lab: $*" >&2; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd "$HERE/../.." && pwd -P)"
LINK="$PROJECT_DIR/artifacts/vm-lab"

[ -n "${MDT_COW_ROOT:-}" ] || die "MDT_COW_ROOT is not set: the CoW area is off in this devcontainer (BTRFS-COW.md section 7)"
[ -d "$MDT_COW_ROOT" ] || die "MDT_COW_ROOT=$MDT_COW_ROOT does not exist; not creating it"
fstype="$(stat -f -c %T "$MDT_COW_ROOT")"
[ "$fstype" = btrfs ] || die "MDT_COW_ROOT=$MDT_COW_ROOT is $fstype, not btrfs; refusing a silent fallback"

toplevel="$(git -C "$PROJECT_DIR" rev-parse --show-toplevel)"
# --git-common-dir is relative to PROJECT_DIR in a main checkout: resolve it there.
common="$(cd "$PROJECT_DIR" && cd "$(git rev-parse --git-common-dir)" && pwd -P)"
main_root="$(dirname "$common")"
repo="$(basename "$main_root")"
if [ "$(cd "$toplevel" && pwd -P)" = "$main_root" ]; then checkout=main; else checkout="$(basename "$toplevel")"; fi
TARGET="$MDT_COW_ROOT/$repo/vm-lab/$checkout/mounttrace"

mkdir -p "$PROJECT_DIR/artifacts"
if [ -L "$LINK" ]; then
    current="$(readlink "$LINK")"
    [ "$current" = "$TARGET" ] || die "$LINK points to $current, expected $TARGET; remove it by hand if that is stale"
elif [ -e "$LINK" ]; then
    [ -d "$LINK" ] && [ -z "$(ls -A "$LINK")" ] || die "$LINK exists and is not an empty directory; refusing to hide its content"
    rmdir "$LINK"
fi
mkdir -p "$TARGET"
chmod 0700 "$TARGET"
[ -L "$LINK" ] || ln -s "$TARGET" "$LINK"
echo "vm-lab: $LINK -> $TARGET ($(stat -f -c %T "$TARGET"))"
