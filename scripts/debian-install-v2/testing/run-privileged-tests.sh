#!/usr/bin/env bash
# Build (if needed), boot, and run the root-gated real-commit test tier for
# inuse_partition_editor.py inside the privileged systemd container defined
# alongside this script, then always tear the container down.
#
# Usage: scripts/debian-install-v2/testing/run-privileged-tests.sh [pytest args...]
# With no arguments it runs the full R0+R1 suite. Every test in that suite
# is safe to run as the container's root — the ones that need real root
# and a loop device (skipped everywhere else) activate automatically here;
# everything else is a fast, harmless fake-run unit test.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEBIAN_INSTALL_DIR="$(cd "$HERE/.." && pwd)"
IMAGE="debian-install-privileged:local"
NAME="debian-install-privileged-tests-$$"

# If THIS shell is itself running inside a container that talks to the
# daemon via docker-outside-of-docker (this repo's devcontainer does),
# bind-mount sources are resolved in the DAEMON's filesystem, not this
# container's — translate our /workspaces/vbpub-relative path to the real
# host path recorded on this container's own mount, or `-v` would silently
# mount an empty/wrong directory. On a plain host talking to its own local
# daemon (no such indirection), fall back to the path as-is.
mount_dest="/workspaces/vbpub"
HOST_DEBIAN_INSTALL_DIR="$DEBIAN_INSTALL_DIR"
self_id="$(cat /etc/hostname 2>/dev/null || true)"
if [ -n "$self_id" ]; then
    host_mount_src="$(docker inspect "$self_id" --format \
        "{{range .Mounts}}{{if eq .Destination \"$mount_dest\"}}{{.Source}}{{end}}{{end}}" \
        2>/dev/null || true)"
    if [ -n "$host_mount_src" ]; then
        HOST_DEBIAN_INSTALL_DIR="$host_mount_src${DEBIAN_INSTALL_DIR#"$mount_dest"}"
    fi
fi

echo "+ docker build -f $HERE/Dockerfile -t $IMAGE $DEBIAN_INSTALL_DIR"
docker build -q -f "$HERE/Dockerfile" -t "$IMAGE" "$DEBIAN_INSTALL_DIR" >/dev/null

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "+ docker run -d --name $NAME --privileged ... -v $HOST_DEBIAN_INSTALL_DIR:/work"
docker run -d --name "$NAME" --privileged \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    -v "$HOST_DEBIAN_INSTALL_DIR:/work:rw" \
    "$IMAGE" >/dev/null

# Never bind-mount the host's /dev in — every test image this suite creates
# is a sparse file under the container's own /tmp (a tmpfs, gone with the
# container); that isolation is the whole safety property of this setup.

# is-system-running --wait blocks until the manager reaches a final state;
# "degraded" (rc=1) is a normal outcome here — journald commonly reports
# "failed" under this container runtime with no effect on
# losetup/sfdisk/mkswap/swapon, which is all this suite needs. Only retry
# while the bus itself isn't up yet (the brief window right after
# `docker run -d`), not while waiting out a real "degraded".
for _ in 1 2 3 4 5; do
    state="$(docker exec "$NAME" systemctl is-system-running 2>&1)" && break
    case "$state" in *"Failed to connect"*) sleep 1 ;; *) break ;; esac
done

TEST_ARGS=("$@")
if [ ${#TEST_ARGS[@]} -eq 0 ]; then
    TEST_ARGS=(debian_install_v2/tests/test_inuse_partition_editor.py \
               debian_install_v2/tests/test_inuse_partition_editor_r1.py -q)
fi

echo "+ docker exec -w /work $NAME python3 -m pytest ${TEST_ARGS[*]}"
docker exec -w /work -e PYTHONPATH=. "$NAME" python3 -m pytest "${TEST_ARGS[@]}"
