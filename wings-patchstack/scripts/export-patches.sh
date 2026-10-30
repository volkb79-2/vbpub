#!/usr/bin/env bash
# export-patches.sh [pterodactyl|pelican] — refresh the committed patch series
# from the working branch. Run after any change to the branch.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

cd "$SRC_DIR" || exit 1
base="$REF"
[[ "$REF" == v* ]] || base="$(git merge-base "origin/$REF" "$BRANCH")"

rm -f "$PATCH_DIR"/*.patch
mkdir -p "$PATCH_DIR"
git format-patch -o "$PATCH_DIR" "$base".."$BRANCH"
ls -1 "$PATCH_DIR"
