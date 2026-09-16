#!/usr/bin/env bash
# Run debian-install-v2's real block-device/swap contract tests inside the
# QEMU guest. The outer runner is deliberately unprivileged; only the guest
# gets root and a kernel-private loop/swap namespace.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="debian-install-v2-r1-$$"
GUEST_SRC="/home/tester/debian-install-v2"
RUNNER_SRC="/source/debian_install_v2"

# A fixed forwarded SSH port must not be shared by concurrent runs of this
# worktree. Keep the lock for the complete lane so the runner, guest state,
# and forwarded port are one serialized resource. The lock lives outside the
# judged tree and a second invocation fails clearly instead of testing the
# wrong guest.
LOCK_ID="$(printf '%s' "$HERE" | sha256sum | cut -c1-12)"
LOCK_PATH="/tmp/mdt-debian-install-vm-${LOCK_ID}.lock"
exec 9>"$LOCK_PATH"
flock -n 9 || {
    echo "run-vm-tests: another VM lane is already running for this worktree" >&2
    exit 1
}

run_with_timeout() {
    local seconds="$1"
    shift
    timeout --foreground "$seconds" "$@"
}

cleanup() {
    "$HERE/run-vm-harness.sh" destroy "$RUN" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$HERE/run-vm-harness.sh" prepare-base case-b
# Debian's generic-cloud image uses HTTPS apt sources. The optional
# apt-cacher-ng helper is deliberately HTTP-only, so do not install its
# proxy configuration for this lane; otherwise apt treats the proxy as an
# HTTPS CONNECT endpoint and fails before the guest test dependencies exist.
"$HERE/run-vm-harness.sh" start "$RUN" --case case-b --no-apt-cache
"$HERE/run-vm-harness.sh" wait "$RUN"

# The guest is the only place where this opt-in is ever set. The test itself
# also requires systemd-detect-virt --vm to report QEMU/KVM, so a copied test
# command cannot accidentally activate swap on a bare host or Docker kernel.
run_with_timeout 10m "$HERE/run-vm-harness.sh" ssh "$RUN" -- \
    'sudo DEBIAN_FRONTEND=noninteractive apt-get update &&
     sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3 python3-pytest util-linux fdisk udev systemd rsync'

# run-vm-harness.sh executes vmctl inside the persistent runner container.
# The project is mounted read-only at /source; do not pass the cockpit/
# worktree path here because the Docker daemon's namespace is different.
run_with_timeout 5m "$HERE/run-vm-harness.sh" copy "$RUN" "$RUNNER_SRC" "$GUEST_SRC"

run_with_timeout 15m "$HERE/run-vm-harness.sh" ssh "$RUN" -- \
    "set -eu
     if systemd-detect-virt --container >/dev/null 2>&1; then
         echo 'VM precondition failed: guest is running in a container' >&2
         exit 1
     fi
     vm_type=\"\$(systemd-detect-virt --vm 2>/dev/null || true)\"
     case \"\$vm_type\" in
         qemu|kvm) ;;
         *) echo \"VM precondition failed: expected qemu/kvm, got \${vm_type:-none}\" >&2; exit 1 ;;
     esac
     for tool in sfdisk blockdev partx losetup mkswap swapon swapoff; do
         command -v \"\$tool\" >/dev/null || { echo \"VM precondition failed: missing \$tool\" >&2; exit 1; }
     done
     cd '$GUEST_SRC'
     sudo env VBPUB_ALLOW_VM_GLOBAL_SWAP_TEST=1 \
       PYTHONPATH='$GUEST_SRC' python3 -m pytest \
       debian_install_v2/tests/test_inuse_partition_editor.py \
       debian_install_v2/tests/test_inuse_partition_editor_r1.py -q \
       --junitxml=/tmp/debian-install-v2-r1.xml
     python3 - <<'PY'
import xml.etree.ElementTree as ET

expected = {
    'test_real_commit_via_loop_device_materializes_partition_nodes',
    'test_real_gpt_commit_via_loop_device_materializes_partition_nodes',
}
root = ET.parse('/tmp/debian-install-v2-r1.xml').getroot()
cases = {case.get('name'): case for case in root.iter('testcase')}
missing = sorted(expected - cases.keys())
bad = []
for name in sorted(expected & cases.keys()):
    case = cases[name]
    if any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')):
        bad.append(name)
if missing or bad:
    raise SystemExit(f'VM lane did not prove both real tests: missing={missing}, not-passed={bad}')
print('verified: both VM-only real loop/swap tests passed')
PY"
