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

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TESTING_DIR="$(cd "$HERE/.." && pwd -P)"
# Another project of this checkout (<repo>/scripts/<name>) may reuse this
# harness: MDT_VM_PROJECT_DIR selects whose artifacts/vm-lab holds the VM
# state and, unless MDT_VM_SOURCE_DIR says otherwise, what is mounted at
# /source. Unset = debian-install-v2 itself (unchanged behaviour and names).
STATE_PROJECT_NAMESPACE_DIR="${MDT_VM_PROJECT_DIR:-}"
SOURCE_NAMESPACE_DIR="${MDT_VM_SOURCE_DIR:-}"
# In-runner mount points. The host side is derived below, by fixed depth from
# this script: <project>/artifacts/vm-lab and <repo root>/downloads of the
# checkout that runs (a worktree uses its own).
VM_STATE_DIR="/vm-lab"
VM_DOWNLOADS_DIR="/downloads"
# Layout generation: bump when the runner's mounts/env/user contract changes,
# so a runner started by older code is refused instead of silently reused.
RUNNER_LAYOUT="vm-lab-3"
RUNNER_HOME="/tmp/vmlab-home"
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
# only exists at that path relative to vm/. VM state and downloads are
# separate read-write binds of the selected project's artifacts/vm-lab
# (tracked placeholder or setup-created CoW symlink) and the repo downloads/
# placeholder; the (read-only) project and testing mounts are never written.
# Also mount the project directory read-only at /source. This gives vmctl a
# stable namespace path for copying debian_install_v2 into the guest; path
# traversal through /work/.. cannot escape a Docker bind mount.
HOST_TESTING_DIR="$TESTING_DIR"
HOST_PROJECT_DIR="${HOST_TESTING_DIR%/testing}"
HOST_SOURCE_DIR="$HOST_PROJECT_DIR"
# Namespace-local project and repo root, derived from this script's location by
# fixed depth (<repo>/scripts/debian-install-v2/testing/vm), physical paths.
# The selected project's vm-lab and the repo downloads placeholder must be
# prepared by their owning workflows; Docker must never create either bind source.
PROJECT_DIR="$(cd "$TESTING_DIR/.." && pwd -P)"
REPO_ROOT="$(cd "$TESTING_DIR/../../.." && pwd -P)"
[ "$PROJECT_DIR" = "$REPO_ROOT/scripts/debian-install-v2" ] || die \
    "unexpected layout: $TESTING_DIR is not <repo>/scripts/debian-install-v2/testing"
STATE_PROJECT_DIR="$PROJECT_DIR"
if [ -n "$STATE_PROJECT_NAMESPACE_DIR" ]; then
    STATE_PROJECT_DIR="$(cd "$STATE_PROJECT_NAMESPACE_DIR" 2>/dev/null && pwd -P)" || die \
        "MDT_VM_PROJECT_DIR '$STATE_PROJECT_NAMESPACE_DIR' does not exist"
    case "${STATE_PROJECT_DIR#"$REPO_ROOT"/scripts/}" in
        "$STATE_PROJECT_DIR"|*/*|'') die \
            "MDT_VM_PROJECT_DIR must be a project of this checkout (<repo>/scripts/<name>), got $STATE_PROJECT_DIR" ;;
    esac
    # Default /source = that project, as its resolved physical path (a relative
    # or symlinked spelling must neither reach Docker nor change the names).
    [ -n "$SOURCE_NAMESPACE_DIR" ] || SOURCE_NAMESPACE_DIR="$STATE_PROJECT_DIR"
fi
MOUNT_TABLE=""
# Translate a cockpit-namespace path to the Docker daemon's host path through
# the cockpit's mount table (longest prefix). From a real host shell the path
# is already in the daemon's namespace. Fails closed when no mount contains it.
host_path_for() {
    local path="$1" what="$2" best_dest="" best_src="" d s prefix probe
    if [ "$inside_container" = "0" ]; then
        printf '%s\n' "$path"
        return 0
    fi
    while IFS=$'\t' read -r d s; do
        [ -n "$d" ] || continue
        # Prefix match on a path-component boundary; ${d%/} is empty for the
        # root mount, which then matches every path.
        prefix="${d%/}"
        probe="$path/"
        if [ "${probe:0:$((${#prefix} + 1))}" = "$prefix/" ] \
            && [ "${#d}" -gt "${#best_dest}" ]; then
            best_dest="$d"
            best_src="$s"
        fi
    done <<EOF
$MOUNT_TABLE
EOF
    [ -n "$best_dest" ] && [ -n "$best_src" ] || {
        echo "run-vm-harness: no Docker mount contains $what $path; refusing a namespace-wrong bind mount" >&2
        return 1
    }
    case "$best_src" in
        /*) ;;
        *)
            echo "run-vm-harness: Docker mount for $what $best_dest is not a physical host path ('$best_src'); refusing a namespace-wrong bind mount" >&2
            return 1
            ;;
    esac
    if [ "$best_dest" = "/" ]; then
        printf '%s%s\n' "$best_src" "$path"
    else
        printf '%s%s\n' "$best_src" "${path#"$best_dest"}"
    fi
}
HOST_REPO_ROOT="$REPO_ROOT"
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

    # Docker's authoritative mount table of this cockpit; host_path_for()
    # resolves every namespace path (testing dir, source, vm-lab, downloads,
    # main-checkout images) through it by longest prefix.
    MOUNT_TABLE="$(docker inspect "$SELF_CONTAINER_ID" --format \
        '{{range .Mounts}}{{printf "%s\t%s\n" .Destination .Source}}{{end}}' 2>/dev/null)" || die \
        "cannot inspect Docker mounts for cockpit '$SELF_CONTAINER_ID'; refusing a namespace-wrong bind mount"

    HOST_TESTING_DIR="$(host_path_for "$TESTING_DIR" "testing dir")" || exit 1
    HOST_PROJECT_DIR="${HOST_TESTING_DIR%/testing}"
    [ "$HOST_PROJECT_DIR" != "$HOST_TESTING_DIR" ] || die \
        "host testing dir '$HOST_TESTING_DIR' does not end in /testing; refusing a namespace-wrong bind mount"
    HOST_REPO_ROOT="${HOST_PROJECT_DIR%"/${PROJECT_DIR#"$REPO_ROOT"/}"}"
    [ "$HOST_REPO_ROOT" != "$HOST_PROJECT_DIR" ] || die \
        "host project dir '$HOST_PROJECT_DIR' does not end in /${PROJECT_DIR#"$REPO_ROOT"/}; refusing a namespace-wrong bind mount"
    # Keep the default source bind in the same mapped host namespace.
    HOST_SOURCE_DIR="$HOST_PROJECT_DIR"
    if [ -n "$SOURCE_NAMESPACE_DIR" ]; then
        HOST_SOURCE_DIR="$(host_path_for "$SOURCE_NAMESPACE_DIR" "requested VM source")" || exit 1
    fi
fi

# From a real host shell the caller's path is already in the daemon's
# namespace, so the explicit source is authoritative without Docker mount
# translation. The container path case above remains mandatory and fail-closed.
if [ "$inside_container" = "0" ] && [ -n "$SOURCE_NAMESPACE_DIR" ]; then
    HOST_SOURCE_DIR="$SOURCE_NAMESPACE_DIR"
fi

# --- state / downloads: fixed-depth paths of THIS checkout, then host paths.
# downloads is a tracked placeholder; the selected project's vm-lab is either
# its tracked placeholder or its setup-created CoW symlink. Prepare both
# through their owning workflows; this harness never creates bind sources.
VM_LAB_NS="$STATE_PROJECT_DIR/artifacts/vm-lab"
HOST_VM_LAB_DIR="$HOST_PROJECT_DIR/artifacts/vm-lab"
# Another project's vm-lab, or an ignored symlink into the CoW area
# (mdt-cockpit BTRFS-COW.md 6.2; this also applies to debian-install-v2's own
# vm-lab once it is a symlink, where binding the link path through the mount
# table translation would be wrong): bind its physical target, translated like
# every other path. The device:inode reuse check then compares the target too.
# Resolved lazily in ensure_runner, so --stop-daemon works without a vm-lab.
resolve_vm_lab() {
    if [ "$STATE_PROJECT_DIR" != "$PROJECT_DIR" ] || [ -L "$VM_LAB_NS" ]; then
        [ -d "$VM_LAB_NS" ] || die "$VM_LAB_NS is missing or a dangling symlink (run the project's vm-lab setup)"
        VM_LAB_NS="$(cd "$VM_LAB_NS" && pwd -P)"
        HOST_VM_LAB_DIR="$(host_path_for "$VM_LAB_NS" "vm-lab")" || exit 1
    fi
}
DOWNLOADS_NS="$REPO_ROOT/downloads"
HOST_DOWNLOADS_DIR="$HOST_REPO_ROOT/downloads"

# A fixed runner name would silently reuse whichever worktree first created
# it. That can make a gate execute another worktree's source. Derive both
# names from this worktree's path (plus the source: two projects may reuse
# this harness from one worktree, and their inputs must never be silently
# exchanged).
if [ -n "$STATE_PROJECT_NAMESPACE_DIR" ]; then
    RUNNER_FINGERPRINT="$(printf '%s\0%s\0%s' "$TESTING_DIR" "$SOURCE_NAMESPACE_DIR" "$STATE_PROJECT_DIR" | sha256sum | cut -c1-12)"
else
    RUNNER_FINGERPRINT="$(printf '%s\0%s' "$TESTING_DIR" "$SOURCE_NAMESPACE_DIR" | sha256sum | cut -c1-12)"
fi
IMAGE="debian-install-vm:$RUNNER_FINGERPRINT"
NAME="debian-install-vm-harness-$RUNNER_FINGERPRINT"

# Outside a Docker cockpit, this path is already in the daemon's namespace and
# a local check is authoritative. Inside a cockpit, HOST_SOURCE_DIR has been
# translated from a container path to a host-only path; never stat that
# translated value here. The daemon-side --mount below refuses a missing
# source instead of -v's silent creation of an empty directory.
if [ "$inside_container" = "0" ]; then
    [ -d "$HOST_SOURCE_DIR" ] || die \
        "VM source directory '$HOST_SOURCE_DIR' does not exist; refusing to start with a phantom bind"
fi

verify_cgroup_parent() {
    [ -n "$VM_CGROUP_PARENT" ] || die \
        'CGROUP_PARENT_DEV_GATES is not set; refusing to start the VM runner unplaced'
    case "$VM_CGROUP_PARENT" in
        *.slice) ;;
        *) die "invalid VM cgroup parent '$VM_CGROUP_PARENT' (expected a .slice unit)" ;;
    esac

    # A controlled native-vs-TCG benchmark needs to distinguish emulator
    # overhead from an imposed CPU/memory ceiling. system.slice is a built-in
    # systemd slice and therefore has no /etc/systemd/system/<name> file for
    # the generic probe below to verify. Permit this exact parent only when
    # the wrapper is running in the host namespace, where systemctl is the
    # authority; never infer it from a cgroup directory visible in a cockpit.
    if [ "$VM_CGROUP_PARENT" = "system.slice" ]; then
        [ "$inside_container" = "0" ] || die \
            'system.slice is only permitted from a host-shell invocation; use the governed dev-gates.slice from a cockpit'
        command -v systemctl >/dev/null 2>&1 || die \
            'systemctl is unavailable; refusing to use the built-in system.slice for an uncapped VM runner'
        local load_state control_group cpu_quota memory_high memory_max slice_cgroup
        local host_cpus slice_cpus
        load_state="$(systemctl show system.slice --property=LoadState --value 2>/dev/null || true)"
        control_group="$(systemctl show system.slice --property=ControlGroup --value 2>/dev/null || true)"
        cpu_quota="$(systemctl show system.slice --property=CPUQuotaPerSecUSec --value 2>/dev/null || true)"
        memory_high="$(systemctl show system.slice --property=MemoryHigh --value 2>/dev/null || true)"
        memory_max="$(systemctl show system.slice --property=MemoryMax --value 2>/dev/null || true)"
        [ "$load_state" = loaded ] || die \
            "system.slice is not a loaded systemd unit (LoadState='${load_state:-unknown}')"
        [ "$control_group" = /system.slice ] || die \
            "system.slice has unexpected ControlGroup '${control_group:-<empty>}'"
        [ "$cpu_quota" = infinity ] || die \
            "system.slice has a CPU quota ('$cpu_quota'); refusing an unrepresentative benchmark"
        [ "$memory_high" = infinity ] || die \
            "system.slice has MemoryHigh='$memory_high'; refusing an unrepresentative benchmark"
        [ "$memory_max" = infinity ] || die \
            "system.slice has MemoryMax='$memory_max'; refusing an unrepresentative benchmark"
        slice_cgroup="/sys/fs/cgroup$control_group"
        [ -d "$slice_cgroup" ] || die \
            "system.slice cgroup is not live at '$slice_cgroup'"
        [ "$(cat "$slice_cgroup/cpu.max" 2>/dev/null || true)" = 'max 100000' ] || die \
            'system.slice cpu.max is not unlimited; refusing an unrepresentative benchmark'
        [ "$(cat "$slice_cgroup/memory.high" 2>/dev/null || true)" = max ] || die \
            'system.slice memory.high is not unlimited; refusing an unrepresentative benchmark'
        [ "$(cat "$slice_cgroup/memory.max" 2>/dev/null || true)" = max ] || die \
            'system.slice memory.max is not unlimited; refusing an unrepresentative benchmark'
        host_cpus="$(cat /sys/fs/cgroup/cpuset.cpus.effective 2>/dev/null || true)"
        slice_cpus="$(cat "$slice_cgroup/cpuset.cpus.effective" 2>/dev/null || true)"
        [ -n "$host_cpus" ] && [ "$slice_cpus" = "$host_cpus" ] || die \
            "system.slice CPU set '$slice_cpus' does not match host CPU set '${host_cpus:-<unknown>}'"
        echo '+ verified host system.slice parent: no CPU/memory quota or CPU-set restriction' >&2
        return 0
    fi

    [ -n "$VM_PROBE_CGROUP_PARENT" ] || die \
        'CGROUP_PARENT_DEV_INTERACTIVE is not set; cannot safely verify the VM cgroup parent'
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
    resolve_vm_lab

    # downloads is a tracked placeholder; the selected project's vm-lab is a
    # tracked placeholder or a project-created CoW symlink. Never create bind
    # sources here (Docker would make a missing source root-owned). A deleted-
    # and-recreated folder is caught by the inode compare.
    for folder in "$VM_LAB_NS" "$DOWNLOADS_NS"; do
        if [ ! -d "$folder" ]; then
            if [ "$folder" = "$VM_LAB_NS" ]; then
                die "$folder is missing; restore its tracked placeholder or run the owning project's vm-lab setup to create its CoW symlink"
            fi
            die "$folder is missing; restore the repo's tracked placeholder with git checkout"
        fi
    done
    RUNNER_OWNER="$(stat -c '%u:%g' "$VM_LAB_NS")"
    downloads_owner="$(stat -c '%u:%g' "$DOWNLOADS_NS")"
    [ "$RUNNER_OWNER" = "$downloads_owner" ] || die \
        "vm-lab ($VM_LAB_NS) is owned by $RUNNER_OWNER but downloads ($DOWNLOADS_NS) by $downloads_owner; the runner executes as one owner, fix the ownership"
    if [ "${RUNNER_OWNER%%:*}" = "0" ] && [ "$(id -u)" != "0" ]; then
        die "vm-lab $VM_LAB_NS is root-owned; refusing to run the VM tooling as root against the worktree (chown it to $(id -u):$(id -g) first)"
    fi

    if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
        stale_runner() {
            echo "runner $NAME: $1" >&2
            echo "refusing to reuse a stale runner; run '$0 --stop-daemon' before retrying" >&2
            return 1
        }
        runner_label() {
            docker inspect "$NAME" --format "{{index .Config.Labels \"$1\"}}" 2>/dev/null || true
        }
        runner_mount_src() {
            docker inspect "$NAME" --format \
                "{{range .Mounts}}{{if eq .Destination \"$1\"}}{{.Source}}{{end}}{{end}}" 2>/dev/null || true
        }
        [ "$(runner_label mdt.vm.layout)" = "$RUNNER_LAYOUT" ] || stale_runner \
            "was started by older code (layout '$(runner_label mdt.vm.layout)', expected '$RUNNER_LAYOUT')" || return 1
        [ "$(runner_label mdt.vm.owner)" = "$RUNNER_OWNER" ] || stale_runner \
            "runs as '$(runner_label mdt.vm.owner)' but the vm-lab owner is '$RUNNER_OWNER'" || return 1
        for pair in "$VM_STATE_DIR|$HOST_VM_LAB_DIR" "$VM_DOWNLOADS_DIR|$HOST_DOWNLOADS_DIR"; do
            dst="${pair%%|*}"; want="${pair#*|}"
            have="$(runner_mount_src "$dst")"
            [ "$have" = "$want" ] || stale_runner \
                "mount $dst is '${have:-<none>}', expected '${want:-<none>}'" || return 1
        done
        # Same folder, not a deleted/replaced or later-mounted-over one:
        # compare device:inode of the caller's view and the runner's view.
        for pair in "$VM_STATE_DIR|$VM_LAB_NS" "$VM_DOWNLOADS_DIR|$DOWNLOADS_NS"; do
            dst="${pair%%|*}"; ns="${pair#*|}"
            here_id="$(stat -c '%d:%i' "$ns" 2>/dev/null || true)"
            runner_id="$(docker exec "$NAME" stat -c '%d:%i' "$dst" 2>/dev/null || true)"
            { [ -n "$here_id" ] && [ "$here_id" = "$runner_id" ]; } || stale_runner \
                "folder $dst is ${runner_id:-<unreadable>} in the runner but $ns is ${here_id:-<missing>} here (deleted, replaced or mounted over after the runner started)" || return 1
        done
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
    # The entry command runs as root: it creates a passwd/group entry for the
    # vm-lab owner (so ssh/ssh-keygen work for that uid), starts apt-cacher-ng,
    # and then idles. All vmctl work runs via `docker exec --user <owner>`, so
    # nothing written under the bind mounts is root-owned.
    docker run -d --name "$NAME" \
        --init \
        --cgroup-parent="$VM_CGROUP_PARENT" \
        --label "mdt.vm.testing-dir=$HOST_TESTING_DIR" \
        --label "mdt.vm.layout=$RUNNER_LAYOUT" \
        --label "mdt.vm.owner=$RUNNER_OWNER" \
        --mount "type=bind,src=$HOST_VM_LAB_DIR,dst=$VM_STATE_DIR" \
        --mount "type=bind,src=$HOST_DOWNLOADS_DIR,dst=$VM_DOWNLOADS_DIR" \
        --mount "type=bind,src=$HOST_TESTING_DIR,dst=/work,readonly" \
        --mount "type=bind,src=$HOST_SOURCE_DIR,dst=/source,readonly" \
        -w /work/vm \
        "$IMAGE" sh -c '
            set -eu
            uid="${1%%:*}"; gid="${1##*:}"; home="$2"
            getent group "$gid" >/dev/null || groupadd -g "$gid" vmlab
            getent passwd "$uid" >/dev/null || useradd -u "$uid" -g "$gid" -M -d "$home" -s /bin/bash vmlab
            mkdir -p "$home"; chown "$uid:$gid" "$home"
            apt-cacher-ng -c /etc/apt-cacher-ng ForeGround=0 >/tmp/apt-cacher-ng.startup.log 2>&1 || true
            exec sleep infinity
        ' _ "$RUNNER_OWNER" "$RUNNER_HOME" >/dev/null
}

case "${1:-}" in
    --stop-daemon)
        docker rm -f "$NAME" >/dev/null 2>&1 || true
        echo "stopped"
        exit 0
        ;;
    --daemon)
        ensure_runner
        echo "runner ready: docker exec -it --user $RUNNER_OWNER -e HOME=$RUNNER_HOME $NAME bash"
        exit 0
        ;;
esac

ensure_runner
# Always as the vm-lab owner (never root): files written under the bind mounts
# must stay deletable by the cockpit user.
exec docker exec -i --user "$RUNNER_OWNER" -e "HOME=$RUNNER_HOME" "$NAME" \
    ./vmctl --state-dir "$VM_STATE_DIR" --downloads-dir "$VM_DOWNLOADS_DIR" "$@"
