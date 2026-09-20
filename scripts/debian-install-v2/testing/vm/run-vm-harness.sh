#!/usr/bin/env bash
# Build (if needed) the QEMU/TCG VM harness image and run a vmctl
# subcommand inside it. The source directory is bind-mounted read-only;
# VM state persists across invocations in the worktree-specific runner.
#
# Usage: scripts/debian-install-v2/testing/vm/run-vm-harness.sh <vmctl args...>
# Examples:
#   run-vm-harness.sh prepare-base case-b
#   run-vm-harness.sh start smoke1 --case case-b
#   run-vm-harness.sh wait smoke1
#   run-vm-harness.sh ssh smoke1 -- uname -a
#   run-vm-harness.sh destroy smoke1
#
# Deliberately a single, long-lived container reused across invocations
# (not a fresh one per command, unlike the retired container runner): a VM
# started by one `run-vm-harness.sh start` call needs to still be running
# (same container, same qemu.pid) when a later `wait`/`ssh`/`destroy` call
# comes back to it. Start one persistent runner with `run-vm-harness.sh
# --daemon` first, or let the first non-daemon subcommand auto-start one
# (it stays running; `run-vm-harness.sh --stop-daemon` tears it down).
# Docker's init reaper is enabled on that runner because QEMU's `-daemonize`
# deliberately detaches its process; a plain `sleep infinity` as PID 1 would
# leave exited QEMU children as zombies after every disposable run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTING_DIR="$(cd "$HERE/.." && pwd)"
# A fixed runner name would silently reuse whichever worktree first created
# it. That can make a gate execute another worktree's source. Derive both
# names from this worktree's path so concurrent gates cannot share a mounted
# runner or image tag.
# Include an optional alternate source in the identity.  Two projects may
# deliberately reuse this generic VM harness from one worktree, but their
# read-only guest input must never be silently exchanged.
SOURCE_NAMESPACE_DIR="${MDT_VM_SOURCE_DIR:-}"
RUNNER_FINGERPRINT="$(printf '%s\0%s' "$TESTING_DIR" "$SOURCE_NAMESPACE_DIR" | sha256sum | cut -c1-12)"
IMAGE="debian-install-vm:$RUNNER_FINGERPRINT"
NAME="debian-install-vm-harness-$RUNNER_FINGERPRINT"
VM_STATE_DIR="/var/lib/mdt-debian-install-vm/$RUNNER_FINGERPRINT"
VM_CACHE_DIR="/var/cache/mdt-debian-install-vm/$RUNNER_FINGERPRINT"
DOCKERFILE_SHA="$(sha256sum "$HERE/Dockerfile" | awk '{print $1}')"

# A VM still consumes host CPU and host-side qcow2 I/O.  It is therefore a
# normal host workload, not an escape from the estate's cgroup policy.  The
# caller must supply the already-loaded gate tier injected by devcontainer.json
# (or by the gate launcher); an omitted value must never fall through to
# Docker's unbounded default or to an implicitly-created transient slice.
VM_CGROUP_PARENT="${CGROUP_PARENT_DEV_GATES:-}"
VM_PROBE_CGROUP_PARENT="${CGROUP_PARENT_DEV_INTERACTIVE:-}"
BUILD_BUILDER="${BUILDX_BUILDER:-}"
CGROUP_PROBE_IMAGE="${MDT_VM_CGROUP_PROBE_IMAGE:-debian:trixie-slim}"

die() {
    echo "run-vm-harness: $*" >&2
    exit 1
}

# Bind-mount the PARENT testing/ directory read-only, not just vm/: vmctl's
# prepare-base reaches one level up for ../download-base-image.sh, which
# only exists at that path relative to vm/. VM state and the image cache live
# in the persistent runner container, never in the judged worktree.
# Also mount the project directory read-only at /source. This gives vmctl a
# stable namespace path for copying debian_install_v2 into the guest; path
# traversal through /work/.. cannot escape a Docker bind mount.
HOST_TESTING_DIR="$TESTING_DIR"
HOST_PROJECT_DIR="${HOST_TESTING_DIR%/testing}"
HOST_SOURCE_DIR="$HOST_PROJECT_DIR"
SELF_HOSTNAME="$(cat /etc/hostname 2>/dev/null || true)"
SELF_CONTAINER_ID=""
inside_container=0
if [ -e /.dockerenv ] || [ -e /run/.containerenv ]; then
    inside_container=1
fi
if [ "$inside_container" = "1" ]; then
    [ -n "$SELF_HOSTNAME" ] || die \
        "cannot identify this Docker cockpit (no /etc/hostname); refusing a namespace-wrong bind mount"

    # /etc/hostname is the container's configured hostname, not necessarily
    # its Docker name.  Looking up that string as a container name worked for
    # the original devcontainer only because both happened to match.  Walk
    # Docker's live containers and match Config.Hostname instead; require one
    # exact match so a duplicate hostname cannot make us mount another
    # worktree's source.
    container_ids="$(docker ps -q --no-trunc 2>/dev/null)" || die \
        "cannot query Docker to identify this cockpit; refusing a namespace-wrong bind mount"
    while IFS= read -r candidate_id; do
        [ -n "$candidate_id" ] || continue
        candidate_hostname="$(docker inspect "$candidate_id" --format '{{.Config.Hostname}}' 2>/dev/null || true)"
        if [ "$candidate_hostname" = "$SELF_HOSTNAME" ]; then
            if [ -n "$SELF_CONTAINER_ID" ]; then
                die "multiple Docker containers have hostname '$SELF_HOSTNAME'; refusing ambiguous host-path resolution"
            fi
            SELF_CONTAINER_ID="$candidate_id"
        fi
    done <<EOF
$container_ids
EOF
    [ -n "$SELF_CONTAINER_ID" ] || die \
        "cannot map cockpit hostname '$SELF_HOSTNAME' to a live Docker container; refusing a namespace-wrong bind mount"

    # Find the longest Docker bind/volume mount prefix containing this
    # namespace path.  This removes the repo-layout assumption that the
    # cockpit mount must be exactly /workspaces/vbpub while still deriving the
    # physical host path from Docker's authoritative mount table.
    host_mount_src=""
    mount_dest=""
    while IFS=$'\t' read -r candidate_dest candidate_src; do
        [ -n "$candidate_dest" ] || continue
        case "$TESTING_DIR/" in
            "$candidate_dest"/*|"$candidate_dest")
                if [ "${#candidate_dest}" -gt "${#mount_dest}" ]; then
                    mount_dest="$candidate_dest"
                    host_mount_src="$candidate_src"
                fi
                ;;
        esac
    done < <(docker inspect "$SELF_CONTAINER_ID" --format \
        '{{range .Mounts}}{{printf "%s\t%s\n" .Destination .Source}}{{end}}' 2>/dev/null) || die \
        "cannot inspect Docker mounts for cockpit '$SELF_CONTAINER_ID'; refusing a namespace-wrong bind mount"
    [ -n "$mount_dest" ] && [ -n "$host_mount_src" ] || die \
        "no Docker mount contains $TESTING_DIR; refusing a namespace-wrong bind mount"
    case "$host_mount_src" in
        /*) ;;
        *) die "Docker mount for $mount_dest is not a physical host path ('$host_mount_src'); refusing a namespace-wrong bind mount" ;;
    esac
    if [ "$mount_dest" = "/" ]; then
        host_suffix="$TESTING_DIR"
    else
        host_suffix="${TESTING_DIR#"$mount_dest"}"
    fi
    HOST_TESTING_DIR="$host_mount_src$host_suffix"
    HOST_PROJECT_DIR="${HOST_TESTING_DIR%/testing}"

    # MDT and future consumers can provide a project source below any of the
    # cockpit's authoritative Docker bind mounts.  Resolve it using the same
    # longest-prefix namespace mapping as the harness itself; never hand a
    # container path to the daemon and never invent a host path when no mount
    # contains it.
    if [ -n "$SOURCE_NAMESPACE_DIR" ]; then
        source_mount_src=""
        source_mount_dest=""
        while IFS=$'\t' read -r candidate_dest candidate_src; do
            [ -n "$candidate_dest" ] || continue
            case "$SOURCE_NAMESPACE_DIR/" in
                "$candidate_dest"/*|"$candidate_dest")
                    if [ "${#candidate_dest}" -gt "${#source_mount_dest}" ]; then
                        source_mount_dest="$candidate_dest"
                        source_mount_src="$candidate_src"
                    fi
                    ;;
            esac
        done < <(docker inspect "$SELF_CONTAINER_ID" --format \
            '{{range .Mounts}}{{printf "%s\t%s\n" .Destination .Source}}{{end}}' 2>/dev/null) || die \
            "cannot inspect Docker mounts for source '$SOURCE_NAMESPACE_DIR'; refusing a namespace-wrong bind mount"
        [ -n "$source_mount_dest" ] && [ -n "$source_mount_src" ] || die \
            "no Docker mount contains requested VM source $SOURCE_NAMESPACE_DIR; refusing a namespace-wrong bind mount"
        case "$source_mount_src" in
            /*) ;;
            *) die "Docker mount for source $source_mount_dest is not a physical host path ('$source_mount_src'); refusing a namespace-wrong bind mount" ;;
        esac
        if [ "$source_mount_dest" = "/" ]; then
            source_suffix="$SOURCE_NAMESPACE_DIR"
        else
            source_suffix="${SOURCE_NAMESPACE_DIR#"$source_mount_dest"}"
        fi
        HOST_SOURCE_DIR="$source_mount_src$source_suffix"
    fi
fi

# From a real host shell the caller's path is already in the daemon's
# namespace, so the explicit source is authoritative without Docker mount
# translation. The container path case above remains mandatory and fail-closed.
if [ "$inside_container" = "0" ] && [ -n "$SOURCE_NAMESPACE_DIR" ]; then
    HOST_SOURCE_DIR="$SOURCE_NAMESPACE_DIR"
fi

[ -d "$HOST_SOURCE_DIR" ] || die \
    "VM source directory '$HOST_SOURCE_DIR' does not exist; refusing to start with a phantom bind"

verify_cgroup_parent() {
    [ -n "$VM_CGROUP_PARENT" ] || die \
        'CGROUP_PARENT_DEV_GATES is not set; refusing to start the VM runner unplaced'
    [ -n "$VM_PROBE_CGROUP_PARENT" ] || die \
        'CGROUP_PARENT_DEV_INTERACTIVE is not set; cannot safely verify the VM cgroup parent'
    case "$VM_CGROUP_PARENT" in
        *.slice) ;;
        *) die "invalid VM cgroup parent '$VM_CGROUP_PARENT' (expected a .slice unit)" ;;
    esac
    case "$VM_PROBE_CGROUP_PARENT" in
        *.slice) ;;
        *) die "invalid cgroup probe parent '$VM_PROBE_CGROUP_PARENT' (expected a .slice unit)" ;;
    esac

    # When the caller is a Docker cockpit, it is already running in
    # VM_PROBE_CGROUP_PARENT. Confirm that Docker agrees with that ambient
    # placement before borrowing the tier for this one-shot host-cgroupfs
    # probe. A typo in --cgroup-parent otherwise causes systemd to auto-create
    # an unlimited transient slice (fail-open). A genuine host-shell caller
    # has no container identity to compare, so the host-cgroupfs/unit probe
    # below remains the authority.
    if [ -n "$SELF_CONTAINER_ID" ]; then
        current_parent="$(docker inspect "$SELF_CONTAINER_ID" --format '{{.HostConfig.CgroupParent}}' 2>/dev/null || true)"
    else
        current_parent=""
    fi
    if [ -n "$current_parent" ] && [ "${current_parent##*/}" != "$VM_PROBE_CGROUP_PARENT" ]; then
        die \
            "current cockpit cgroup parent is '${current_parent:-<unknown>}', expected '$VM_PROBE_CGROUP_PARENT'"
    fi

    # systemctl talks to the host's bus, which is intentionally not mounted in
    # the cockpit.  Instead, this unprivileged probe sees the Docker host's
    # cgroupfs and system-unit directory.  Requiring both a live cgroup
    # directory and the unit file catches both a stale name and an uninstalled
    #/auto-created transient slice.  The probe itself is placed in the already
    # verified interactive tier.
    probe_image="$IMAGE"
    if ! docker image inspect "$probe_image" >/dev/null 2>&1; then
        probe_image="$CGROUP_PROBE_IMAGE"
    fi
    probe="$(docker run --rm --pull=missing --cgroupns=host --network=none \
        --cgroup-parent="$VM_PROBE_CGROUP_PARENT" \
        --mount type=bind,src=/sys/fs/cgroup,dst=/hostcg,ro \
        --mount type=bind,src=/etc/systemd/system,dst=/hostunits,ro \
        "$probe_image" bash -c '
            parent="$1"
            test -f "/hostunits/$parent" &&
                find /hostcg -type d -name "$parent" -print -quit | grep -q .
        ' _ "$VM_CGROUP_PARENT" 2>/dev/null && echo verified || true)"
    [ "$probe" = verified ] || die \
        "cgroup parent '$VM_CGROUP_PARENT' is not an installed, live host slice; refusing to start the VM runner"
}

verify_build_environment() {
    [ -n "$BUILD_BUILDER" ] || die \
        'BUILDX_BUILDER is not set; refusing to build the VM runner through Docker default'
    docker buildx inspect --builder="$BUILD_BUILDER" >/dev/null 2>&1 || die \
        "Buildx builder '$BUILD_BUILDER' is not available; refusing an ungoverned runner build"
}

ensure_runner() {
    # Verify both the host tier and the named BuildKit backend before doing any
    # image build work. `docker build` without an explicit builder can silently
    # use Docker's default daemon path, which defeats the host's BuildKit cgroup
    # governance for this otherwise unprivileged VM harness.
    verify_build_environment
    verify_cgroup_parent

    if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
        mounted_testing="$(docker inspect "$NAME" --format \
            '{{range .Mounts}}{{if eq .Destination "/work"}}{{.Source}}{{end}}{{end}}' \
            2>/dev/null || true)"
        if [ "$mounted_testing" != "$HOST_TESTING_DIR" ]; then
            echo "runner $NAME is mounted from $mounted_testing, expected $HOST_TESTING_DIR" >&2
            echo "refusing to reuse a runner from another worktree" >&2
            return 1
        fi
        runner_image="$(docker inspect "$NAME" --format '{{.Image}}' 2>/dev/null || true)"
        local_image="$(docker image inspect "$IMAGE" --format '{{.Id}}' 2>/dev/null || true)"
        local_dockerfile_sha="$(docker image inspect "$IMAGE" --format \
            '{{index .Config.Labels "mdt.vm.dockerfile-sha"}}' 2>/dev/null || true)"
        if [ -n "$runner_image" ] && [ "$runner_image" = "$local_image" ] \
            && [ "$local_dockerfile_sha" = "$DOCKERFILE_SHA" ]; then
            return 0
        fi
        echo "runner $NAME uses image ${runner_image:-<unknown>} / Dockerfile ${local_dockerfile_sha:-<unknown>}, but the current $IMAGE is ${local_image:-<missing>} / Dockerfile $DOCKERFILE_SHA" >&2
        echo "refusing to reuse a stale runner; run '$0 --stop-daemon' before retrying" >&2
        return 1
    fi

    docker buildx build \
        --builder="$BUILD_BUILDER" \
        --load \
        --progress=plain \
        --label="mdt.vm.dockerfile-sha=$DOCKERFILE_SHA" \
        -f "$HERE/Dockerfile" \
        -t "$IMAGE" \
        "$HERE"
    # A stopped-but-present container with this name (e.g. OOM-killed, or
    # the host itself restarted) would otherwise make the docker run below
    # fail with "name already in use" instead of self-healing -- remove
    # any dead leftover by name first, not just check `docker ps` for a
    # live one (review finding, 2026-09-09).
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    echo "+ starting persistent VM-harness runner ($NAME)" >&2
    docker run -d --name "$NAME" \
        --init \
        --cgroup-parent="$VM_CGROUP_PARENT" \
        --label "mdt.vm.testing-dir=$HOST_TESTING_DIR" \
        -e "MDT_VM_STATE_DIR=$VM_STATE_DIR" \
        -e "MDT_VM_CACHE_DIR=$VM_CACHE_DIR" \
        -v "$HOST_TESTING_DIR:/work:ro" \
        -v "$HOST_SOURCE_DIR:/source:ro" \
        -w /work/vm \
        "$IMAGE" sleep infinity >/dev/null
}

case "${1:-}" in
    --stop-daemon)
        docker rm -f "$NAME" >/dev/null 2>&1 || true
        echo "stopped"
        exit 0
        ;;
    --daemon)
        ensure_runner
        echo "runner ready: docker exec -it $NAME bash"
        exit 0
        ;;
esac

ensure_runner
exec docker exec -i \
    -e "MDT_VM_STATE_DIR=$VM_STATE_DIR" \
    -e "MDT_VM_CACHE_DIR=$VM_CACHE_DIR" \
    "$NAME" ./vmctl "$@"
