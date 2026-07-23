#!/usr/bin/env bash
# rebase.sh <pterodactyl|pelican> <new-ref>
# Rebase the patch branch onto a new upstream release, e.g.:
#   rebase.sh pterodactyl v1.13.2
# On success: updates stack.conf's ref, re-exports the patch series, and
# reminds you to run test.sh + build-image.sh. On conflict: leaves you in the
# rebase to resolve by hand.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
[[ $# -eq 2 ]] || { echo "usage: $0 <pterodactyl|pelican> <new-ref>" >&2; exit 2; }
resolve_target "$1"
NEW_REF="$2"

cd "$SRC_DIR" || exit 1
git fetch --depth 50 origin "$NEW_REF" || git fetch origin "$NEW_REF"
old_base="$REF"
[[ "$REF" == v* ]] || old_base="$(git merge-base "origin/$REF" "$BRANCH")"
new_base="$NEW_REF"
git rev-parse --verify --quiet "$NEW_REF" >/dev/null || new_base="FETCH_HEAD"

NEW_BRANCH="cgroup/$NEW_REF"
git branch "$NEW_BRANCH" "$BRANCH"
if ! git rebase --onto "$new_base" "$old_base" "$NEW_BRANCH"; then
    cat >&2 <<EOF

Rebase hit conflicts (expected occasionally — the touch points are stable but
not frozen). Resolve, then:
    git rebase --continue
    $SCRIPT_DIR/export-patches.sh $TARGET      # after updating stack.conf ref
    $SCRIPT_DIR/test.sh $TARGET
EOF
    exit 1
fi

# Point stack.conf at the new ref so PATCH_DIR/export/test pick it up.
sed -i "s|^\(${TARGET^^}[A-Z_]*_REF\)=.*|\1=\"$NEW_REF\"|" "$STACK_DIR/stack.conf" 2>/dev/null || \
sed -i "s|^\(PTERODACTYL_REF\|PELICAN_REF\)=\"$REF\"|\1=\"$NEW_REF\"|" "$STACK_DIR/stack.conf"

echo "rebased onto $NEW_REF as $NEW_BRANCH. Next:"
echo "  $SCRIPT_DIR/export-patches.sh $TARGET"
echo "  $SCRIPT_DIR/test.sh $TARGET   (INTEGRATION=1 recommended)"
echo "  $SCRIPT_DIR/build-image.sh $TARGET"
