#!/usr/bin/env bash
# build-image.sh [pterodactyl|pelican] [version-suffix]
# Builds the deployable Wings image with the repo's own Dockerfile.
# Tag: wings-local:<ref>-cgroup.<n> — registry-less on purpose (a stray
# `docker compose pull` fails loudly instead of reverting to stock upstream).
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
resolve_target "${1:-pterodactyl}"

SUFFIX="${2:-cgroup.1}"
VERSION="${REF#v}-$SUFFIX"
TAG="$IMAGE_PREFIX:$VERSION"
[[ "$TARGET" == "pelican" ]] && TAG="$IMAGE_PREFIX-pelican:$VERSION"

cd "$SRC_DIR" || exit 1
git rev-parse --verify --quiet "$BRANCH" >/dev/null && git checkout -q "$BRANCH"
tar --exclude='.git' -cf - . | docker build -t "$TAG" --build-arg VERSION="$VERSION" -
echo "built: $TAG"
