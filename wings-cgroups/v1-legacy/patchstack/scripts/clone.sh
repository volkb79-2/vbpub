#!/usr/bin/env bash
# clone.sh [pterodactyl|pelican|all] — (re)create the upstream working clones.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

clone_one() {
    resolve_target "$1"
    if [[ -d "$SRC_DIR/.git" ]]; then
        echo "$SRC_DIR already exists; fetching $REF instead"
        git -C "$SRC_DIR" fetch --depth 50 origin "$REF"
        return
    fi
    mkdir -p "$BUILD_DIR"
    if [[ "$REF" == v* ]]; then
        git clone --branch "$REF" --depth 50 "$REMOTE" "$SRC_DIR"
    else
        git clone --depth 50 "$REMOTE" "$SRC_DIR"
    fi
    git -C "$SRC_DIR" config user.name  "$(git config user.name  || echo wings-cgroups)"
    git -C "$SRC_DIR" config user.email "$(git config user.email || echo wings-cgroups@localhost)"
}

if [[ "${1:-all}" == "all" ]]; then
    clone_one pterodactyl
    clone_one pelican
else
    clone_one "$1"
fi
