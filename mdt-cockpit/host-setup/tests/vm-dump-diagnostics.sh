#!/usr/bin/env bash
# Guest-side evidence dump. run-vm-acceptance.sh runs this (inside the guest)
# when any acceptance step fails, BEFORE its EXIT trap destroys the VM, so a
# failure leaves its own evidence in the lane log (the 2026-10-19 vm-system
# failure left none: "mdt-buildkitd.service ... timeout exceeded" and nothing else).
# Read-only; every command is best-effort.
set -u
section() { printf '\n===== VM DIAGNOSTICS: %s =====\n' "$*"; }
try() { "$@" 2>&1 || echo "(exit $? from: $*)"; }

section "failed units"
try systemctl --failed --no-pager
for unit in $(systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}') \
            mdt-buildkitd.service mdt-dev-governance-reconcile.service docker.service; do
    section "systemctl status $unit"
    try systemctl status "$unit" --no-pager -l
    section "journal of $unit (last 120 lines)"
    try journalctl -u "$unit" --no-pager -n 120
done
section "journalctl -u mdt-buildkitd.service (full boot)"
try journalctl -u mdt-buildkitd.service --no-pager -b
section "docker images / containers / disk"
try docker images
try docker ps -a
try df -h /var/lib/docker /run
section "network (can the guest reach the registry?)"
try getent hosts registry-1.docker.io
try timeout 20 curl -sS -o /dev/null -w 'registry-1.docker.io HTTP %{http_code} in %{time_total}s\n' https://registry-1.docker.io/v2/
section "rendered buildkitd unit and slice io.max"
try grep -E '^(TimeoutStartSec|ExecStart(Pre)?=)' /etc/systemd/system/mdt-buildkitd.service
try cat /sys/fs/cgroup/dev.slice/io.max
section "kernel messages (tail)"
try dmesg --ctime -T
echo "===== end VM diagnostics ====="
