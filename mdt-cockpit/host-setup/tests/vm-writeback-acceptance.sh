#!/usr/bin/env bash
# Writeback and root-queue settings acceptance (MDT-HS-05), run as root INSIDE the disposable QEMU
# guest by run-vm-acceptance.sh after vm-governance-acceptance.sh. Real systemd, real udev, real
# sysctl and a real block queue: the fake-based tests (test_hs05.py) cover the logic; this proves the
# rendered files exist, install.sh applied them and the LIVE values match the env.
#
#   1. set the four keys, plant the interim files, run install.sh  -> files rendered, live values
#      match, interim files retired, mdt-host-check.sh reports OK for each key
#   2. set one queue key and one sysctl key back to `kernel`       -> MDT's line is gone, a NOTICE
#      says the running value cannot be restored
# Never run this on a real host.
set -euo pipefail

CONF=/etc/mdt/host-setup.env
LIB=/usr/local/sbin/mdt-container-io-caps.lib.sh
HS=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SYSCTL=/etc/sysctl.d/90-mdt-io-writeback.conf
RULE=/etc/udev/rules.d/61-mdt-io-queue.rules
INTERIM_SYSCTL=/etc/sysctl.d/90-mdt-io-writeback-interim.conf
INTERIM_RULE=/etc/udev/rules.d/61-mdt-io-queue-interim.rules

fail() { echo "VM WRITEBACK FAIL: $*" >&2; exit 1; }
ok() { echo "VM WRITEBACK OK: $*"; }

[ "$(id -u)" = 0 ] || fail "must run as root in the guest"
systemd-detect-virt --container >/dev/null 2>&1 && fail "refusing to run in a container"
cd "$HS"

set_conf() { # the key must already be declared in the env file
  grep -q "^$1=" "$CONF" || echo "$1=$2" >> "$CONF"
  sed -i "s|^$1=.*|$1=$2|" "$CONF"
}

log() { :; }
# shellcheck disable=SC1090
. "$LIB"
node=$(_mdt_root_disk_node) || fail "cannot derive the root disk node on the guest (BLOCKED condition)"
dev=${node#/dev/}
[ -d "/sys/block/$dev/queue" ] || fail "/sys/block/$dev/queue does not exist for the derived disk $dev"
hw=$(cat "/sys/block/$dev/queue/max_hw_sectors_kb")
sectors=64
[ "$hw" -ge "$sectors" ] || sectors=$hw
echo "guest root disk: $node (max_hw_sectors_kb=$hw), max_sectors_kb under test: $sectors"

# ---- 1. set, plant interim files, install ------------------------------------------------
set_conf IO_WRITEBACK_DIRTY_BYTES 16777216
set_conf IO_WRITEBACK_DIRTY_BACKGROUND_BYTES 4194304
set_conf IO_ROOT_QUEUE_NR_REQUESTS 16
set_conf IO_ROOT_QUEUE_MAX_SECTORS_KB "$sectors"
printf 'vm.dirty_bytes = 33554432\n' > "$INTERIM_SYSCTL"
printf 'ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="%s", ATTR{queue/nr_requests}="32"\n' "$dev" > "$INTERIM_RULE"

out=$(bash "$HS/install.sh" 2>&1) || { printf '%s\n' "$out" | tail -n 40; fail "install.sh exited non-zero with the writeback keys set"; }
printf '%s\n' "$out" | grep -q "removed the interim file $INTERIM_SYSCTL" || fail "no NOTICE for the removed interim sysctl file"
printf '%s\n' "$out" | grep -q "removed the interim file $INTERIM_RULE" || fail "no NOTICE for the removed interim udev rule"
[ -f "$SYSCTL" ] || fail "$SYSCTL was not rendered"
[ -f "$RULE" ] || fail "$RULE was not rendered"
grep -q "^vm.dirty_bytes = 16777216$" "$SYSCTL" || fail "$SYSCTL lacks vm.dirty_bytes: $(cat "$SYSCTL")"
grep -q "KERNEL==\"$dev\"" "$RULE" || fail "$RULE does not match the derived disk $dev: $(cat "$RULE")"
[ ! -e "$INTERIM_SYSCTL" ] || fail "the interim sysctl file was not removed"
[ ! -e "$INTERIM_RULE" ] || fail "the interim udev rule was not removed"
ok "install.sh rendered $SYSCTL and $RULE (disk $dev, derived) and retired the interim files"

live() { tr -d '[:space:]' < "$1"; }
[ "$(live /proc/sys/vm/dirty_bytes)" = 16777216 ] || fail "live vm.dirty_bytes is $(live /proc/sys/vm/dirty_bytes)"
[ "$(live /proc/sys/vm/dirty_background_bytes)" = 4194304 ] || fail "live vm.dirty_background_bytes is $(live /proc/sys/vm/dirty_background_bytes)"
[ "$(live "/sys/block/$dev/queue/nr_requests")" = 16 ] || fail "live nr_requests is $(live "/sys/block/$dev/queue/nr_requests")"
[ "$(live "/sys/block/$dev/queue/max_sectors_kb")" = "$sectors" ] || fail "live max_sectors_kb is $(live "/sys/block/$dev/queue/max_sectors_kb")"
ok "the LIVE values match: dirty_bytes 16777216, dirty_background_bytes 4194304, $dev nr_requests 16, max_sectors_kb $sectors"

check=$(/usr/local/sbin/mdt-host-check.sh 2>&1 || true)
printf '%s\n' "$check" | grep -q "OK   vm.dirty_bytes = 16777216" || fail "mdt-host-check.sh did not report OK for vm.dirty_bytes"
printf '%s\n' "$check" | grep -q "OK   $dev nr_requests = 16" || fail "mdt-host-check.sh did not report OK for nr_requests"
printf '%s\n' "$check" | grep -q "MISMATCH" && fail "mdt-host-check.sh reported a MISMATCH"
ok "mdt-host-check.sh reports each key OK"

# ---- 2. back to kernel: MDT's line goes, the running value is not guessed back ------------------
set_conf IO_WRITEBACK_DIRTY_BYTES kernel
set_conf IO_WRITEBACK_DIRTY_BACKGROUND_BYTES kernel
set_conf IO_ROOT_QUEUE_MAX_SECTORS_KB kernel
out=$(bash "$HS/install.sh" 2>&1) || { printf '%s\n' "$out" | tail -n 40; fail "install.sh exited non-zero when keys were set back to kernel"; }
printf '%s\n' "$out" | grep -q "cannot restore the kernel's boot value" || fail "no NOTICE that the kernel value cannot be restored"
[ ! -e "$SYSCTL" ] || fail "$SYSCTL still exists with no sysctl key set"
grep -q "max_sectors_kb" "$RULE" && fail "$RULE still carries max_sectors_kb"
grep -q 'ATTR{queue/nr_requests}="16"' "$RULE" || fail "$RULE lost nr_requests"
[ "$(live /proc/sys/vm/dirty_bytes)" = 16777216 ] || fail "the running vm.dirty_bytes was changed instead of left alone"
ok "keys set back to kernel: MDT's lines are gone and the running values were not guessed back"

# leave the guest with no managed writeback state for the steps that follow
set_conf IO_ROOT_QUEUE_NR_REQUESTS kernel
bash "$HS/install.sh" >/dev/null 2>&1 || fail "the cleanup install.sh exited non-zero"
echo "VM WRITEBACK ACCEPTANCE PASSED"
