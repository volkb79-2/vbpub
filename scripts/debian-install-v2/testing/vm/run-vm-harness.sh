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
RUNNER_FINGERPRINT="$(printf '%s' "$TESTING_DIR" | sha256sum | cut -c1-12)"
IMAGE="debian-install-vm:$RUNNER_FINGERPRINT"
NAME="debian-install-vm-harness-$RUNNER_FINGERPRINT"
VM_STATE_DIR="/var/lib/mdt-debian-install-vm/$RUNNER_FINGERPRINT"
VM_CACHE_DIR="/var/cache/mdt-debian-install-vm/$RUNNER_FINGERPRINT"
DOCKERFILE_SHA="$(sha256sum "$HERE/Dockerfile" | awk '{print $1}')"

# A VM still consumes host CPU and host-side qcow2 I/O.  It is therefore a
# normal host workload, not an escape from the estate's cgroup policy.  The
# caller must supply the already-loaded dev tier injected by devcontainer.json
# (or by the gate launcher); an omitted value must never fall through to
# Docker's unbounded default or to an implicitly-created transient slice.
VM_CGROUP_PARENT="${CGROUP_PARENT_DEV_BACKGROUND:-}"
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
mount_dest="/workspaces/vbpub"
HOST_TESTING_DIR="$TESTING_DIR"
HOST_PROJECT_DIR="${HOST_TESTING_DIR%/testing}"
self_id="$(cat /etc/hostname 2>/dev/null || true)"
inside_container=0
if [ -e /.dockerenv ] || [ -e /run/.containerenv ]; then
    inside_container=1
fi
if [ -n "$self_id" ]; then
    host_mount_src="$(docker inspect "$self_id" --format \
        "{{range .Mounts}}{{if eq .Destination \"$mount_dest\"}}{{.Source}}{{end}}{{end}}" \
        2>/dev/null || true)"
    if [ -n "$host_mount_src" ]; then
        HOST_TESTING_DIR="$host_mount_src${TESTING_DIR#"$mount_dest"}"
        HOST_PROJECT_DIR="${HOST_TESTING_DIR%/testing}"
    fi
fi
if [ "$inside_container" = "1" ] && [ -z "${host_mount_src:-}" ]; then
    die "cannot resolve the Docker host path for $TESTING_DIR from this cockpit; refusing a namespace-wrong bind mount"
fi

verify_cgroup_parent() {
    [ -n "$VM_CGROUP_PARENT" ] || die \
        'CGROUP_PARENT_DEV_BACKGROUND is not set; refusing to start the VM runner unplaced'
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
    if [ -n "$self_id" ]; then
        current_parent="$(docker inspect "$self_id" --format '{{.HostConfig.CgroupParent}}' 2>/dev/null || true)"
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
        -v "$HOST_PROJECT_DIR:/source:ro" \
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
