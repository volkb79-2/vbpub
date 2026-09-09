#!/usr/bin/env bash
# Build (if needed) the QEMU/TCG VM harness image and run a vmctl
# subcommand inside it, with this directory bind-mounted so VM state
# (.vm/) persists across invocations on the host filesystem.
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
# (not a fresh one per command, unlike run-privileged-tests.sh): a VM
# started by one `run-vm-harness.sh start` call needs to still be running
# (same container, same qemu.pid) when a later `wait`/`ssh`/`destroy` call
# comes back to it. Start one persistent runner with `run-vm-harness.sh
# --daemon` first, or let the first non-daemon subcommand auto-start one
# (it stays running; `run-vm-harness.sh --stop-daemon` tears it down).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTING_DIR="$(cd "$HERE/.." && pwd)"
IMAGE="debian-install-vm:local"
NAME="debian-install-vm-harness"

# Bind-mount the PARENT testing/ directory, not just vm/: vmctl's
# prepare-base reaches one level up for ../download-base-image.sh, which
# only exists on the host at that path relative to vm/, not inside it.
mount_dest="/workspaces/vbpub"
HOST_TESTING_DIR="$TESTING_DIR"
self_id="$(cat /etc/hostname 2>/dev/null || true)"
if [ -n "$self_id" ]; then
    host_mount_src="$(docker inspect "$self_id" --format \
        "{{range .Mounts}}{{if eq .Destination \"$mount_dest\"}}{{.Source}}{{end}}{{end}}" \
        2>/dev/null || true)"
    if [ -n "$host_mount_src" ]; then
        HOST_TESTING_DIR="$host_mount_src${TESTING_DIR#"$mount_dest"}"
    fi
fi

ensure_runner() {
    docker build -q -f "$HERE/Dockerfile" -t "$IMAGE" "$HERE" >/dev/null
    if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
        echo "+ starting persistent VM-harness runner ($NAME)" >&2
        docker run -d --name "$NAME" \
            -v "$HOST_TESTING_DIR:/work:rw" \
            -w /work/vm \
            "$IMAGE" sleep infinity >/dev/null
    fi
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
exec docker exec -i "$NAME" ./vmctl "$@"
