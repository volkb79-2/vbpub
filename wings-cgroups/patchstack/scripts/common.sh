# shellcheck shell=bash
# Sourced by the other scripts. Resolves per-target settings.
# shellcheck disable=SC2034  # variables are consumed by the sourcing scripts
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_DIR="$(dirname "$STACK_DIR")"
BUILD_DIR="$PROJECT_DIR/build"

# shellcheck source=../stack.conf
source "$STACK_DIR/stack.conf"

resolve_target() {
    TARGET="${1:-pterodactyl}"
    case "$TARGET" in
        pterodactyl)
            REMOTE="$PTERODACTYL_REMOTE"; REF="$PTERODACTYL_REF"
            GO_IMAGE="$PTERODACTYL_GO_IMAGE"
            SRC_DIR="$BUILD_DIR/wings-pterodactyl"
            PATCH_DIR="$STACK_DIR/patches/pterodactyl-$REF"
            BRANCH="cgroup/$REF"
            ;;
        pelican)
            REMOTE="$PELICAN_REMOTE"; REF="$PELICAN_REF"
            GO_IMAGE="$PELICAN_GO_IMAGE"
            SRC_DIR="$BUILD_DIR/wings-pelican"
            PATCH_DIR="$STACK_DIR/patches/pelican-$REF"
            BRANCH="cgroup/$REF"
            ;;
        *) echo "unknown target '$TARGET' (use: pterodactyl | pelican)" >&2; exit 2 ;;
    esac
}

# Run a command inside a golang container with the source tree tar-piped in.
# Needed because this devcontainer's paths are not bind-mountable by the host
# Docker daemon; also makes the scripts work identically on any docker host.
# Usage: go_in_container <src-dir> [--docker] <shell command...>
go_in_container() {
    local src="$1"; shift
    local docker_args=()
    if [[ "${1:-}" == "--docker" ]]; then
        docker_args+=(-v /var/run/docker.sock:/var/run/docker.sock)
        shift
    fi
    docker volume create wingscg-gocache >/dev/null
    tar --exclude='.git' -C "$src" -cf - . | docker run --rm -i \
        -v wingscg-gocache:/go -e GOCACHE=/go/.cache -e GOFLAGS=-buildvcs=false \
        "${docker_args[@]}" "$GO_IMAGE" \
        sh -c "mkdir -p /src && tar -xf - -C /src && cd /src && $*"
}
