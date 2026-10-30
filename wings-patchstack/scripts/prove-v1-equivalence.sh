#!/usr/bin/env bash
# Prove the 2026-10 canonical-layout migration for v1.13.3. This is deliberately
# tied to the historical base, not the currently active upstream in stack.conf.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$(dirname "$SCRIPT_DIR")"

OLD_SERIES="$STACK_DIR/archive/deployed-v1/pterodactyl-v1.13.3"
NEW_SERIES="$STACK_DIR/patches/pterodactyl-v1.13.3"
[[ -d "$OLD_SERIES" ]] || { echo "archived deployed series missing: $OLD_SERIES" >&2; exit 1; }
[[ -d "$NEW_SERIES" ]] || { echo "canonical v1.13.3 series missing: $NEW_SERIES" >&2; exit 1; }
source "$OLD_SERIES/UPSTREAM-BASE.conf"

TEMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TEMP_DIR"; }
trap cleanup EXIT

UPSTREAM_DIR="$TEMP_DIR/upstream"
OLD_TREE="$TEMP_DIR/old-series"
NEW_TREE="$TEMP_DIR/new-series"
git clone --quiet --branch "$UPSTREAM_REF" --depth 1 "$UPSTREAM_REMOTE" "$UPSTREAM_DIR"
ACTUAL_COMMIT="$(git -C "$UPSTREAM_DIR" rev-parse --verify 'HEAD^{commit}')"
if [[ "$ACTUAL_COMMIT" != "$UPSTREAM_COMMIT" ]]; then
    echo "equivalence base mismatch: expected $UPSTREAM_COMMIT, cloned $ACTUAL_COMMIT" >&2
    exit 1
fi
git -C "$UPSTREAM_DIR" config user.name patchseries-equivalence
git -C "$UPSTREAM_DIR" config user.email patchseries-equivalence@localhost
git -C "$UPSTREAM_DIR" worktree add --quiet -b "proof-old-$$" "$OLD_TREE" "$UPSTREAM_COMMIT"
git -C "$UPSTREAM_DIR" worktree add --quiet -b "proof-new-$$" "$NEW_TREE" "$UPSTREAM_COMMIT"

git -C "$OLD_TREE" am "$OLD_SERIES"/*.patch
git -C "$NEW_TREE" am "$NEW_SERIES"/*.patch

OLD_HEAD="$(git -C "$OLD_TREE" rev-parse HEAD)"
NEW_HEAD="$(git -C "$NEW_TREE" rev-parse HEAD)"
OLD_TREE_SHA="$(git -C "$OLD_TREE" rev-parse 'HEAD^{tree}')"
NEW_TREE_SHA="$(git -C "$NEW_TREE" rev-parse 'HEAD^{tree}')"
printf 'upstream commit: %s\n' "$UPSTREAM_COMMIT"
printf 'old series HEAD: %s\n' "$OLD_HEAD"
printf 'new series HEAD: %s\n' "$NEW_HEAD"
printf 'old tree: %s\n' "$OLD_TREE_SHA"
printf 'new tree: %s\n' "$NEW_TREE_SHA"

if [[ "$OLD_TREE_SHA" != "$NEW_TREE_SHA" ]]; then
    echo "git diff --stat:" >&2
    git -C "$UPSTREAM_DIR" diff --stat "$OLD_HEAD" "$NEW_HEAD" >&2
    echo "EQUIVALENCE: FAIL (tree hashes differ)" >&2
    exit 1
fi
DIFF_STAT="$(git -C "$UPSTREAM_DIR" diff --stat "$OLD_HEAD" "$NEW_HEAD")"
if [[ -n "$DIFF_STAT" ]]; then
    printf '%s\n' "$DIFF_STAT" >&2
    echo "EQUIVALENCE: FAIL (git diff --stat is not empty)" >&2
    exit 1
fi
echo "git diff --stat: empty"
echo "EQUIVALENCE: PASS"
