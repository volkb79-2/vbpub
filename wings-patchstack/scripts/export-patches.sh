#!/usr/bin/env bash
# export-patches.sh [pterodactyl|pelican] — refresh the committed patch series
# from the working branch. Run after any change to the branch.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

cd "$SRC_DIR" || exit 1
base="$REF"
[[ "$REF" == v* ]] || base="$(git merge-base "origin/$REF" "$BRANCH")"

PATCH_PARENT="$(dirname "$PATCH_DIR")"
PATCH_NAME="$(basename "$PATCH_DIR")"
mkdir -p "$PATCH_PARENT"
TEMP_PARENT="$(mktemp -d "$PATCH_PARENT/.$PATCH_NAME.XXXXXX")"
STAGING_DIR="$TEMP_PARENT/new"
OLD_DIR="$TEMP_PARENT/old"
mkdir -p "$STAGING_DIR"

cleanup() {
    local status=$?
    trap - EXIT
    if [[ -d "$OLD_DIR" && ! -e "$PATCH_DIR" ]]; then
        if ! mv -T "$OLD_DIR" "$PATCH_DIR"; then
            echo "could not restore the previous patch series from $OLD_DIR" >&2
            echo "preserving recovery files under $TEMP_PARENT" >&2
            return 1
        fi
    fi
    rm -rf "$TEMP_PARENT"
    return "$status"
}
trap cleanup EXIT

git format-patch -o "$STAGING_DIR" "$base".."$BRANCH"
compgen -G "$STAGING_DIR/*.patch" >/dev/null || {
    echo "git format-patch produced no patches; keeping $PATCH_DIR unchanged" >&2
    exit 1
}
PYTHONPATH="$VBPUB_ROOT/libraries/patchseries/src" python3 -m patchseries check \
    "$STAGING_DIR" --vocabulary "$STACK_DIR/vocabulary.toml"

if [[ -e "$PATCH_DIR" ]]; then
    mv -T "$PATCH_DIR" "$OLD_DIR"
fi
if ! mv -T "$STAGING_DIR" "$PATCH_DIR"; then
    echo "could not install the validated patch series; restoring the previous series" >&2
    exit 1
fi
rm -rf "$OLD_DIR"
rm -rf "$TEMP_PARENT"
trap - EXIT
ls -1 "$PATCH_DIR"
