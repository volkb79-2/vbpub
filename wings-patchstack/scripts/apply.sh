#!/usr/bin/env bash
# apply.sh [pterodactyl] — create the patch branch off the pinned upstream commit
# and apply the committed patch series onto it.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

[[ -d "$SRC_DIR/.git" ]] || { echo "run clone.sh first ($SRC_DIR missing)" >&2; exit 1; }
ls "$PATCH_DIR"/*.patch >/dev/null 2>&1 || { echo "no patches in $PATCH_DIR" >&2; exit 1; }
verify_remote_pin
verify_pinned_object

cd "$SRC_DIR" || exit 1
base="$UPSTREAM_COMMIT"

if git rev-parse --verify --quiet "$BRANCH" >/dev/null; then
    echo "branch $BRANCH already exists; leaving it untouched." >&2
    echo "Delete it first (git branch -D $BRANCH) to re-apply." >&2
    exit 1
fi
git checkout -q -b "$BRANCH" "$base"
if ! git am "$PATCH_DIR"/*.patch; then
    echo "" >&2
    echo "git am failed — upstream drifted. Resolve by hand, then" >&2
    echo "export-patches.sh $TARGET to refresh the series. (git am --abort to bail.)" >&2
    exit 1
fi
git log --oneline "$base"..HEAD
