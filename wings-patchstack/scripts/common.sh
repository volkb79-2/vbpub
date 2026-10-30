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

# Run a command inside a golang container with the source tree tar-piped in.
# Needed because this devcontainer's paths are not bind-mountable by the host
# Docker daemon; also makes the scripts work identically on any docker host.
# Usage: go_in_container <src-dir> [--docker] [--with-git] [--cpus N] <shell command...>
#   --docker    hand the host docker socket in (integration tests)
#   --with-git  keep .git in the tar; assay's R1 lane needs the repository to
#               resolve base..HEAD and to materialise its snapshot
# Extraction is --no-same-owner on purpose: tar run as root would otherwise
# restore the HOST uid, and git then refuses the tree as "dubious ownership".
# That is unfixable from inside for assay's lane, because assay runs every git
# child with GIT_CONFIG_NOSYSTEM=1 and GIT_CONFIG_GLOBAL=/dev/null, so no
# `safe.directory` exception can be honoured. The owner has to actually match.
#   --cpus N    cap the container (house rule: this box is shared with a
#               production game server; one capped container at a time)
go_in_container() {
    local src="$1"; shift
    local docker_args=()
    local tar_args=(--exclude='.git')
    if [[ -z "${CGROUP_PARENT_DEV_GATES:-}" ]]; then
        echo "CGROUP_PARENT_DEV_GATES must be supplied by the host environment" >&2
        return 2
    fi
    # Check the unit when this process can actually reach the host systemd.
    # In the devcontainer, /run/systemd/system is not mounted and systemctl is
    # only a shim; there the trusted value is injected by devcontainer.json.
    if [[ -d /run/systemd/system ]]; then
        local load_state
        if ! load_state="$(systemctl show "$CGROUP_PARENT_DEV_GATES" \
            --property=LoadState --value 2>/dev/null)"; then
            echo "could not verify the host gate slice; use the host-provided CGROUP_PARENT_DEV_GATES" >&2
            return 2
        fi
        if [[ "$load_state" != loaded ]]; then
            echo "host gate slice is not LoadState=loaded; use the host-provided CGROUP_PARENT_DEV_GATES" >&2
            return 2
        fi
    fi
    docker_args+=(--cgroup-parent "$CGROUP_PARENT_DEV_GATES")
    while :; do
        case "${1:-}" in
            --docker)   docker_args+=(-v /var/run/docker.sock:/var/run/docker.sock); shift ;;
            --with-git) tar_args=(); shift ;;
            --cpus)     docker_args+=(--cpus "$2"); shift 2 ;;
            *)          break ;;
        esac
    done
    docker volume create wingscg-gocache >/dev/null
    tar "${tar_args[@]}" -C "$src" -cf - . | docker run --rm -i \
        -v wingscg-gocache:/go -e GOCACHE=/go/.cache -e GOFLAGS=-buildvcs=false \
        "${docker_args[@]}" "$GO_IMAGE" \
        sh -c "mkdir -p /src && tar -xf - --no-same-owner -C /src && cd /src && $*"
}
