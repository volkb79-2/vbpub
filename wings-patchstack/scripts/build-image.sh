#!/usr/bin/env bash
# build-image.sh [pterodactyl] — derive the version and stamp series provenance.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
[[ $# -le 1 ]] || { echo "usage: $0 [pterodactyl]" >&2; exit 2; }
resolve_target "${1:-pterodactyl}"

[[ -d "$SRC_DIR/.git" ]] || { echo "run clone.sh + apply.sh first ($SRC_DIR missing)" >&2; exit 1; }
verify_remote_pin
verify_pinned_object
git -C "$SRC_DIR" rev-parse --verify --quiet "$BRANCH" >/dev/null || {
    echo "patch branch $BRANCH is missing; run apply.sh first" >&2
    exit 1
}
CURRENT_HEAD="$(git -C "$SRC_DIR" rev-parse HEAD)"
BRANCH_HEAD="$(git -C "$SRC_DIR" rev-parse "$BRANCH")"
[[ "$CURRENT_HEAD" == "$BRANCH_HEAD" ]] || {
    echo "checkout HEAD does not match patch branch $BRANCH" >&2
    exit 1
}
[[ -z "$(git -C "$SRC_DIR" status --porcelain)" ]] || {
    echo "patched checkout is dirty; refusing to label uncommitted source as the exported series" >&2
    exit 1
}

PYTHONPATH="$VBPUB_ROOT/libraries/patchseries/src" python3 -m patchseries check \
    "$PATCH_DIR" --vocabulary "$STACK_DIR/vocabulary.toml"

# Re-apply the committed series in a temporary worktree. This binds the image
# digest to the source tree actually built, not just to an ancestor SHA.
CHECK_TEMP="$(mktemp -d)"
CHECK_BRANCH="provenance-check-$$"
CHECK_TREE="$CHECK_TEMP/source"
RESERVATION_ID=""
IMAGE_BUILT=0
cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "$RESERVATION_ID" && "$IMAGE_BUILT" -eq 0 ]]; then
        if ! PYTHONPATH="$VBPUB_ROOT/libraries/patchseries/src" python3 "$SCRIPT_DIR/provenance.py" release \
            --reservation-id "$RESERVATION_ID"; then
            echo "warning: could not release failed build reservation $RESERVATION_ID" >&2
        fi
    elif [[ -n "$RESERVATION_ID" ]]; then
        echo "warning: image build succeeded but its reservation remains active: $RESERVATION_ID" >&2
    fi
    git -C "$SRC_DIR" worktree remove --force "$CHECK_TREE" >/dev/null 2>&1 || true
    git -C "$SRC_DIR" branch -D "$CHECK_BRANCH" >/dev/null 2>&1 || true
    rm -rf "$CHECK_TEMP"
    return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
git -C "$SRC_DIR" worktree add --quiet -b "$CHECK_BRANCH" "$CHECK_TREE" "$UPSTREAM_COMMIT"
git -C "$CHECK_TREE" am "$PATCH_DIR"/*.patch
EXPECTED_TREE="$(git -C "$CHECK_TREE" rev-parse 'HEAD^{tree}')"
ACTUAL_TREE="$(git -C "$SRC_DIR" rev-parse "$BRANCH^{tree}")"
if [[ "$EXPECTED_TREE" != "$ACTUAL_TREE" ]]; then
    echo "patched checkout tree differs from the exported patch series" >&2
    exit 1
fi

PROVENANCE="$(PYTHONPATH="$VBPUB_ROOT/libraries/patchseries/src" python3 "$SCRIPT_DIR/provenance.py" reserve \
    --series "$PATCH_DIR" \
    --upstream-version "${REF#v}" \
    --upstream-commit "$UPSTREAM_COMMIT")"
IFS=$'\t' read -r VERSION SUFFIX SERIES_DIGEST PATCHSERIES_VERSION PINNED_COMMIT RESERVATION_ID <<< "$PROVENANCE"
[[ -n "$VERSION" && -n "$SUFFIX" && -n "$SERIES_DIGEST" && -n "$PATCHSERIES_VERSION" && -n "$RESERVATION_ID" ]] || {
    echo "provenance helper returned an incomplete record" >&2
    exit 1
}
[[ "$PINNED_COMMIT" == "$UPSTREAM_COMMIT" ]] || {
    echo "provenance helper returned a different upstream commit" >&2
    exit 1
}

TAG="$IMAGE_PREFIX:$VERSION"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cd "$SRC_DIR" || exit 1
tar --exclude='.git' -cf - . | docker build \
    --label "org.opencontainers.image.version=$VERSION" \
    --label "org.opencontainers.image.created=$BUILD_DATE" \
    --label "org.vbpub.wings.series-digest=$SERIES_DIGEST" \
    --label "org.vbpub.wings.upstream-commit=$UPSTREAM_COMMIT" \
    --label "org.vbpub.wings.patchseries-version=$PATCHSERIES_VERSION" \
    --label "org.vbpub.wings.build-date=$BUILD_DATE" \
    --build-arg "VERSION=$VERSION" -t "$TAG" -
IMAGE_BUILT=1

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$TAG")"
PYTHONPATH="$VBPUB_ROOT/libraries/patchseries/src" python3 "$SCRIPT_DIR/provenance.py" record \
    --upstream-version "${REF#v}" \
    --suffix "$SUFFIX" \
    --series-digest "$SERIES_DIGEST" \
    --reservation-id "$RESERVATION_ID" \
    --image-id "$IMAGE_ID" \
    --built-at "$BUILD_DATE"
RESERVATION_ID=""
echo "built: $TAG ($IMAGE_ID)"
