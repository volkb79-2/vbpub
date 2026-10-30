# shellcheck shell=bash
# Sourced by the other scripts. Resolves per-target settings.
# shellcheck disable=SC2034  # variables are consumed by the sourcing scripts
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$(dirname "$SCRIPT_DIR")"
VBPUB_ROOT="$(dirname "$STACK_DIR")"
PROJECT_DIR="$VBPUB_ROOT/wings-cgroups"
BUILD_DIR="$PROJECT_DIR/build"

# shellcheck source=../stack.conf
source "$STACK_DIR/stack.conf"

resolve_target() {
    TARGET="${1:-pterodactyl}"
    case "$TARGET" in
        pterodactyl)
            REMOTE="$PTERODACTYL_REMOTE"; REF="$PTERODACTYL_REF"
            UPSTREAM_COMMIT="$PTERODACTYL_COMMIT"
            GO_IMAGE="$PTERODACTYL_GO_IMAGE"
            SRC_DIR="$BUILD_DIR/wings-pterodactyl"
            PATCH_DIR="$STACK_DIR/patches/pterodactyl-$REF"
            BRANCH="cgroup/$REF"
            ;;
        pelican)
            echo "Pelican series is archived and stale; port it before using the build workflow." >&2
            exit 2
            ;;
        *) echo "unknown target '$TARGET' (use: pterodactyl)" >&2; exit 2 ;;
    esac
}

verify_remote_pin() {
    local actual
    git -C "$SRC_DIR" fetch --depth 50 origin "$REF"
    actual="$(git -C "$SRC_DIR" rev-parse --verify 'FETCH_HEAD^{commit}')"
    if [[ "$actual" != "$UPSTREAM_COMMIT" ]]; then
        echo "upstream pin mismatch for $TARGET $REF: expected $UPSTREAM_COMMIT, fetched $actual" >&2
        return 1
    fi
}

verify_pinned_object() {
    local actual
    if ! actual="$(git -C "$SRC_DIR" rev-parse --verify "$UPSTREAM_COMMIT^{commit}" 2>/dev/null)"; then
        echo "pinned upstream commit is unavailable: $UPSTREAM_COMMIT" >&2
        return 1
    fi
    if [[ "$actual" != "$UPSTREAM_COMMIT" ]]; then
        echo "pinned upstream commit is unavailable or mismatched: $UPSTREAM_COMMIT (found $actual)" >&2
        return 1
    fi
}
