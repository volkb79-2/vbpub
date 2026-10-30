#!/usr/bin/env bash
# forward-port.sh pterodactyl <new-ref> <full-commit-sha>
# Replay the exported commits onto a new upstream base; it does not rewrite the
# existing patch branch or update stack.conf.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
[[ $# -eq 3 ]] || {
    echo "usage: $0 pterodactyl <new-ref> <full-commit-sha>" >&2
    exit 2
}
resolve_target "$1"
[[ -d "$SRC_DIR/.git" ]] || { echo "run clone.sh first ($SRC_DIR missing)" >&2; exit 1; }
NEW_REF="$2"
NEW_COMMIT="$3"
[[ "$NEW_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
    echo "new upstream commit must be a full 40-character lowercase SHA" >&2
    exit 2
}

git -C "$SRC_DIR" fetch --depth 50 origin "$NEW_REF"
ACTUAL_COMMIT="$(git -C "$SRC_DIR" rev-parse --verify 'FETCH_HEAD^{commit}')"
[[ "$ACTUAL_COMMIT" == "$NEW_COMMIT" ]] || {
    echo "new upstream pin mismatch: $NEW_REF resolved to $ACTUAL_COMMIT, expected $NEW_COMMIT" >&2
    exit 1
}
git -C "$SRC_DIR" cat-file -e "$NEW_COMMIT^{commit}"

NEW_BRANCH="cgroup/$NEW_REF"
if git -C "$SRC_DIR" rev-parse --verify --quiet "$NEW_BRANCH" >/dev/null; then
    echo "branch $NEW_BRANCH already exists; leaving it untouched." >&2
    exit 1
fi
git -C "$SRC_DIR" checkout -q -b "$NEW_BRANCH" "$NEW_COMMIT"
if ! git -C "$SRC_DIR" am "$PATCH_DIR"/*.patch; then
    cat >&2 <<EOF

git am stopped while replaying the exported series. Resolve the current patch
and continue the forward-port manually, or use 'git am --abort' to return to
the new upstream base. No existing branch or stack.conf pin was changed.
EOF
    exit 1
fi

echo "replayed the series as $NEW_BRANCH at $NEW_COMMIT. Review the full patch
series and run its gates before updating stack.conf and exporting the new files."
