#!/usr/bin/env bash
# Run debian-install-v2's real block-device/swap contract tests inside the
# QEMU guest. The outer runner is deliberately unprivileged; only the guest
# gets root and a kernel-private loop/swap namespace.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="debian-install-v2-r1-$$"
GUEST_SRC="/home/tester/debian-install-v2"
RUNNER_SRC="/source/debian_install_v2"

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
"$HERE/run-vm-harness.sh" ssh "$RUN" -- \
    'sudo DEBIAN_FRONTEND=noninteractive apt-get update &&
     sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3 python3-pytest python3-pytest-cov util-linux fdisk udev systemd rsync'

# run-vm-harness.sh executes vmctl inside the persistent runner container.
# The project is mounted read-only at /source; do not pass the cockpit/
# worktree path here because the Docker daemon's namespace is different.
"$HERE/run-vm-harness.sh" copy "$RUN" "$RUNNER_SRC" "$GUEST_SRC"

"$HERE/run-vm-harness.sh" ssh "$RUN" -- \
    "cd '$GUEST_SRC' && sudo env VBPUB_ALLOW_VM_GLOBAL_SWAP_TEST=1 \
       PYTHONPATH='$GUEST_SRC' python3 -m pytest \
       debian_install_v2/tests/test_inuse_partition_editor.py \
       debian_install_v2/tests/test_inuse_partition_editor_r1.py -q"
