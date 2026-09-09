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

# docker stop (graceful, SIGRTMIN+3 per the Dockerfile's STOPSIGNAL) before
# the unconditional docker rm -f fallback: systemd's normal shutdown
# sequence deactivates swap units as a standard step, giving any swap this
# run activated on a loop device (see the big warning below) a real chance
# to come off cleanly before the container disappears. A bare `rm -f`
# SIGKILLs PID 1 immediately with no such chance. Best-effort either way --
# see the warning for why this is defense-in-depth, not a real fix.
cleanup() {
    docker stop -t 15 "$NAME" >/dev/null 2>&1 || true
    docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ============================================================================
# INCIDENT, 2026-09-09: this container's real --commit-path test tier
# (test_real_commit_via_loop_device_materializes_partition_nodes and its GPT
# sibling, in test_inuse_partition_editor_r1.py) had apparently never
# successfully run before that day -- this container couldn't even start on
# this host's docker daemon until a same-day fix. That fix let those tests
# run for real for the first time, surfacing a dormant bug with real
# consequences: they call `inuse_partition_editor.py add-swap --commit`
# against a loop device, and that command's own intended, correct production
# behavior is to run `mkswap` + `swapon -a` for real -- fully provisioning
# working swap is the whole point of the tool. Swap activated on a loop
# device is REAL, HOST-KERNEL-GLOBAL kernel state, not something a
# container's namespaces contain -- and the tests' cleanup only did
# `losetup --detach`, never `swapoff` first (fixed same-day). The result: a
# real host-wide swap area was left active, referencing an already-detached
# loop device. Deactivating a loop-backed swap area whose backing device is
# gone is a known-hazardous operation (evacuating swapped pages can need
# memory the loop driver itself would have to supply, a reclaim-recursion
# trap) -- `swapoff` hung in uninterruptible (D) sleep, memory pressure
# cascaded across the shared host, and even the production game server's own
# process got caught in D state. The operator had to manually SIGKILL
# multiple docker container cgroups (production and dev alike) trying to
# free memory, and ultimately had to REBOOT THE HOST. See
# resume-2026-09-08-netcup-debian-install-v2-livetest.md's incident section
# and cgroupns-host-loop-device-host-hang-incident.md for the full account.
#
# The direct test bug is fixed (swapoff before detach, both tests). That is
# defense-in-depth, not a guarantee: a `docker rm -f`/SIGKILL mid-test (which
# is exactly what happened during the incident's own investigation) skips
# Python `finally` blocks entirely, so an interrupted run can still leak real
# host swap regardless of this fix. Do not treat this container as safe to
# force-kill mid-run, and do not run this script's default (full R0+R1,
# which includes the swap-activating tests) unattended or interrupted on
# this shared host. --cgroupns=host is ALSO still off-limits here (see git
# blame on this file, 2026-09-09) as a separate, independent hazard -- it is
# not established to be what caused this specific incident (loop-device
# swap is host-global regardless of cgroupns mode), but it is a live-state-
# contention risk on shared infrastructure in its own right and there is no
# reason to accept it.
# ============================================================================
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
