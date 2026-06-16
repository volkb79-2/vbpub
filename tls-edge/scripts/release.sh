#!/usr/bin/env bash
# ─── scripts/release.sh — cut a new tls-edge release ────────────────────────
#
# Standalone (most common):
#   bash tls-edge/scripts/release.sh 0.2.0
#
# Via release-runner (set TLS_EDGE_VERSION in .release-vars first):
#   echo "TLS_EDGE_VERSION=0.2.0" > tls-edge/.release-vars
#   python3 release-runner.py --project tls-edge
#
# What this script does:
#   1. Validates git state (on main, clean working tree, tag not taken)
#   2. Updates tls-edge/VERSION
#   3. Commits with a conventional message
#   4. Creates an annotated tag  tls-edge-v<version>
#   5. Builds the release artifact (scripts/build-artifact.sh)
#   6. Publishes to GitHub Releases with checksum sidecar (scripts/publish-release.py)
#   7. Prints the push command — you run that manually to push
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
TLS_EDGE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(git -C "$TLS_EDGE_ROOT" rev-parse --show-toplevel)"
RELEASE_VARS="$TLS_EDGE_ROOT/.release-vars"
VERSION_FILE="$TLS_EDGE_ROOT/VERSION"

# ─── Colour helpers ──────────────────────────────────────────────────────────
GRN='\033[0;32m'; CYN='\033[0;36m'; RED='\033[0;31m'; RST='\033[0m'
ok()    { echo -e "${GRN}  ✓${RST}  $*"; }
info()  { echo -e "${CYN}==>${RST} $*"; }
fatal() { echo -e "${RED}  ✗${RST}  $*" >&2; exit 1; }

# ─── Resolve version ─────────────────────────────────────────────────────────
# Priority: positional arg → TLS_EDGE_VERSION env → .release-vars
NEW_VERSION="${1:-${TLS_EDGE_VERSION:-}}"

if [[ -z "$NEW_VERSION" ]] && [[ -f "$RELEASE_VARS" ]]; then
    # shellcheck disable=SC1090
    source "$RELEASE_VARS"
    NEW_VERSION="${TLS_EDGE_VERSION:-}"
fi

[[ -n "$NEW_VERSION" ]] || fatal "Version not specified.
  Usage: $0 <version>       (e.g. $0 0.2.0)
  Or:    echo \"TLS_EDGE_VERSION=0.2.0\" > tls-edge/.release-vars
         python3 release-runner.py --project tls-edge"

NEW_VERSION="${NEW_VERSION#v}"   # strip optional leading 'v'
TAG="tls-edge-v${NEW_VERSION}"

# ─── Validate git state ───────────────────────────────────────────────────────
CURRENT_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
[[ "$CURRENT_BRANCH" == "main" ]] \
    || fatal "Must be on 'main' (currently on '$CURRENT_BRANCH')."

DIRTY=$(git -C "$REPO_ROOT" status --porcelain -- . ":(exclude)$TLS_EDGE_ROOT/.release-vars")
[[ -z "$DIRTY" ]] \
    || fatal "Working tree is dirty — commit or stash changes first:\n$DIRTY"

git -C "$REPO_ROOT" tag -l "$TAG" | grep -q "^${TAG}$" \
    && fatal "Tag '$TAG' already exists."

# ─── Bump VERSION ─────────────────────────────────────────────────────────────
info "Bumping VERSION → $NEW_VERSION"
printf '%s\n' "$NEW_VERSION" > "$VERSION_FILE"
git -C "$REPO_ROOT" add "$VERSION_FILE"
if git -C "$REPO_ROOT" diff --cached --quiet -- "$VERSION_FILE"; then
    # VERSION already equals NEW_VERSION (e.g. first release at the current
    # version) — nothing to commit; tag the current HEAD instead of failing.
    info "VERSION already $NEW_VERSION — no bump commit needed; tagging current HEAD."
else
    git -C "$REPO_ROOT" commit -m "tls-edge: release v${NEW_VERSION}"
    ok "Committed version bump."
fi

# ─── Create annotated tag ─────────────────────────────────────────────────────
git -C "$REPO_ROOT" tag -a "$TAG" -m "tls-edge ${NEW_VERSION}"
ok "Created tag: $TAG"

# ─── Build release artifact ───────────────────────────────────────────────────
info "Building release artifact (dist/${TAG}.tar.xz)..."
bash "$SCRIPT_DIR/build-artifact.sh"
ok "Artifact built."

# ─── Publish to GitHub Releases ───────────────────────────────────────────────
info "Publishing to GitHub Releases..."
python3 "$SCRIPT_DIR/publish-release.py"
ok "GitHub Release published."

# ─── Done ─────────────────────────────────────────────────────────────────────
echo
echo "Next step — push the commit and tag:"
echo
echo "    git push origin main $TAG"
echo
