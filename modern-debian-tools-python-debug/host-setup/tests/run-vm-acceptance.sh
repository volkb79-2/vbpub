#!/usr/bin/env bash
# Execute MDT's host-level installer acceptance inside a disposable QEMU guest.
# The outer container only orchestrates QEMU; the guest owns PID 1, systemd,
# Docker, cgroupfs, and every service/config assertion.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MDT_ROOT="$(cd "$HERE/../.." && pwd)"
WORKTREE_ROOT="$(cd "$MDT_ROOT/.." && pwd)"
HARNESS="$(cd "$WORKTREE_ROOT/scripts/debian-install-v2/testing/vm" && pwd)/run-vm-harness.sh"
RUN="mdt-system-$(printf '%s' "$MDT_ROOT" | sha256sum | cut -c1-10)"
GUEST_ROOT="/home/tester/vbpub/modern-debian-tools-python-debug"
GUEST_SHARED_SCRIPTS="/home/tester/vbpub/scripts/debian-install-v2"
LOCK_ID="$(printf '%s' "$MDT_ROOT" | sha256sum | cut -c1-12)"
LOCK_PATH="/tmp/mdt-host-setup-vm-${LOCK_ID}.lock"

exec 9>"$LOCK_PATH"
flock -n 9 || {
    echo "run-vm-acceptance: another MDT VM lane is already running for this worktree" >&2
    exit 1
}

run_with_timeout() {
    local seconds="$1"
    shift
    timeout --foreground "$seconds" "$@"
}

cleanup() {
    MDT_VM_SOURCE_DIR="$WORKTREE_ROOT" "$HARNESS" destroy "$RUN" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# The generic harness derives the physical host path from Docker's mount table
# and refuses a namespace-only path. Source is read-only; guest changes stay
# in the disposable VM overlay.
export MDT_VM_SOURCE_DIR="$WORKTREE_ROOT"
run_with_timeout 15m "$HARNESS" prepare-base case-b
run_with_timeout 2m "$HARNESS" start "$RUN" --case case-b --no-apt-cache
run_with_timeout 10m "$HARNESS" wait "$RUN"

run_with_timeout 12m "$HARNESS" ssh "$RUN" -- \
    'sudo DEBIAN_FRONTEND=noninteractive apt-get update &&
     sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       docker.io docker-buildx-plugin python3 python3-pytest systemd systemd-sysv util-linux'

run_with_timeout 5m "$HARNESS" copy "$RUN" /source/modern-debian-tools-python-debug "$GUEST_ROOT"
run_with_timeout 2m "$HARNESS" ssh "$RUN" -- "mkdir -p '$GUEST_SHARED_SCRIPTS'"
run_with_timeout 5m "$HARNESS" copy "$RUN" /source/scripts/debian-install-v2 "$GUEST_SHARED_SCRIPTS"

run_with_timeout 25m "$HARNESS" ssh "$RUN" -- \
    "set -eu
     cd '$GUEST_ROOT'
     test \"\$(ps -p 1 -o comm=)\" = systemd
     test \"\$(stat -fc %T /sys/fs/cgroup)\" = cgroup2fs
     if systemd-detect-virt --container >/dev/null 2>&1; then
         echo 'MDT VM acceptance: guest is a container' >&2
         exit 1
     fi
     vm_type=\"\$(systemd-detect-virt --vm 2>/dev/null || true)\"
     case \"\$vm_type\" in qemu|kvm) ;; *) echo \"MDT VM acceptance: expected qemu/kvm, got \$vm_type\" >&2; exit 1 ;; esac
     python3 - <<'PY'
from pathlib import Path

src = Path('host-setup/host-setup.env.example')
dst = Path('/tmp/mdt-host-setup.env')
values = {
    'DEV_MEMORY_HIGH': '1.5G', 'DEV_MEMORY_MAX': '2G',
    'DEV_INTERACTIVE_MEMORY_HIGH': '512M', 'DEV_INTERACTIVE_MEMORY_MAX': '1G',
    'DEV_BACKGROUND_MEMORY_HIGH': '512M', 'DEV_BACKGROUND_MEMORY_MAX': '1G',
    'DEV_GATES_MEMORY_HIGH': '256M', 'DEV_GATES_MEMORY_MAX': '512M',
    'DEV_BUILDKITD_MEMORY_HIGH': '512M', 'DEV_BUILDKITD_MEMORY_MAX': '1G',
}
out = []
seen = set()
for line in src.read_text().splitlines(keepends=True):
    key = line.split('=', 1)[0] if '=' in line and not line.lstrip().startswith('#') else ''
    if key in values:
        out.append(f'{key}={values[key]}\\n')
        seen.add(key)
    else:
        out.append(line)
missing = set(values) - seen
if missing:
    raise SystemExit(f'missing expected config keys: {sorted(missing)}')
dst.write_text(''.join(out))
PY
     sudo install -D -m 0644 /tmp/mdt-host-setup.env /etc/mdt/host-setup.env
     sudo systemctl enable --now docker
     sudo bash host-setup/install.sh
     sudo /usr/local/sbin/mdt-host-check.sh
     for unit in dev.slice dev-gates.slice dev-buildkitd.slice; do
         state=\"\$(sudo systemctl show \"\$unit\" -p LoadState --value)\"
         fragment=\"\$(sudo systemctl show \"\$unit\" -p FragmentPath --value)\"
         test \"\$state\" = loaded
         test -n \"\$fragment\"
     done
     sudo test -f /etc/docker/daemon.json
     sudo grep -q 'dev-background.slice' /etc/docker/daemon.json
     sudo docker info >/dev/null
     sudo docker buildx version
     echo 'MDT VM acceptance: systemd, cgroup2, Docker, rendered slices, and host check passed'"
