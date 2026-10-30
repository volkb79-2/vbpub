#!/usr/bin/env bash
# clone.sh [pterodactyl|all] — create or verify the pinned upstream clone.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

clone_one() {
    resolve_target "$1"
    if [[ -d "$SRC_DIR/.git" ]]; then
        echo "$SRC_DIR already exists; verifying the pinned $REF"
    else
        mkdir -p "$BUILD_DIR"
        git clone --branch "$REF" --depth 50 "$REMOTE" "$SRC_DIR"
        git -C "$SRC_DIR" config user.name "$(git config user.name || echo wings-cgroups)"
        git -C "$SRC_DIR" config user.email "$(git config user.email || echo wings-cgroups@localhost)"
    fi
    verify_remote_pin
    verify_pinned_object
    echo "verified upstream $REF at $UPSTREAM_COMMIT"
}

if [[ "${1:-all}" == "all" ]]; then
    clone_one pterodactyl
else
    clone_one "$1"
fi
