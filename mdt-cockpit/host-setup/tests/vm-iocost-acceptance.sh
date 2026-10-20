#!/usr/bin/env bash
# IO scheduler and io.cost acceptance (MDT-HS-07), run as root INSIDE the disposable QEMU guest by
# run-vm-acceptance.sh after vm-writeback-acceptance.sh. Real systemd, real udev, a real block queue and the real
# root io.cost files: the fake-based tests (test_hs07.py) cover the logic; this proves install.sh set the
# scheduler, the io.cost model and the QoS LIVE, retired the BFQ rules, and that mdt-host-check.sh reports the
# weight mechanism from the live files.
#
#   1. mq-deadline + a linear model + a QoS, with a known-copy non-MDT BFQ rule planted -> live scheduler,
#      model and QoS match, BFQ rules retired, mdt-io-cost.service enabled, host-check reports io.cost
#   2. back to kernel / kernel / off -> the rule is removed and QoS reads enable=0
# If the guest disk does not list mq-deadline, the REFUSAL path is asserted instead and said so.
# Never run this on a real host.
set -euo pipefail

CONF=/etc/mdt/host-setup.env
LIB=/usr/local/sbin/mdt-container-io-caps.lib.sh
HS=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RULE=/etc/udev/rules.d/60-mdt-io-scheduler.rules
OLD_MDT=/etc/udev/rules.d/60-mdt-bfq-scheduler.rules
OLD_FOREIGN=/etc/udev/rules.d/60-bfq-scheduler.rules
CG=/sys/fs/cgroup
MODEL="100000000 1000 1000 50000000 500 500"
QOS="95 5000 95 20000 5 400"

fail() { echo "VM IOCOST FAIL: $*" >&2; exit 1; }
ok() { echo "VM IOCOST OK: $*"; }

[ "$(id -u)" = 0 ] || fail "must run as root in the guest"
systemd-detect-virt --container >/dev/null 2>&1 && fail "refusing to run in a container"
cd "$HS"

set_conf() { # KEY VALUE (the value is written inside double quotes)
  grep -q "^$1=" "$CONF" || echo "$1=\"\"" >> "$CONF"
  sed -i "s|^$1=.*|$1=\"$2\"|" "$CONF"
}

log() { :; }
# shellcheck disable=SC1090
. "$LIB"
node=$(_mdt_root_disk_node) || fail "cannot derive the root disk node on the guest (BLOCKED condition)"
dev=${node#/dev/}
mm=$(_mdt_dev_majmin "$node") || fail "cannot derive the whole-disk MAJ:MIN of $node (BLOCKED condition: io.cost needs the whole disk)"
list=$(_mdt_io_sched_list "$dev") || fail "cannot read the scheduler list of $dev"
echo "guest root disk: $node ($mm), schedulers: $(echo "$list" | tr '\n' ' ')"

if ! echo "$list" | grep -qx mq-deadline; then
  echo "VM IOCOST NOTE: this guest disk does not list mq-deadline; asserting the REFUSAL path instead"
  set_conf IO_ROOT_SCHEDULER mq-deadline
  set +e
  out=$(bash "$HS/install.sh" 2>&1); rc=$?
  set -e
  [ "$rc" = 2 ] || fail "install.sh did not refuse (rc $rc) an IO_ROOT_SCHEDULER the disk does not list"
  printf '%s\n' "$out" | grep -q "is not offered by this disk" || fail "the refusal did not name the disk's list"
  set_conf IO_ROOT_SCHEDULER kernel
  ok "refusal path asserted (mq-deadline not offered by $dev)"
  echo "VM IOCOST ACCEPTANCE PASSED"
  exit 0
fi

# ---- 1. mq-deadline + model + QoS ----------------------------------------------------------------
cp "$HS/etc/udev/rules.d/60-bfq-scheduler.rules" "$OLD_FOREIGN"      # a byte-identical known copy, not MDT-installed
set_conf IO_ROOT_SCHEDULER mq-deadline
set_conf IO_COST_MODEL "$MODEL"
set_conf IO_COST_QOS "$QOS"
out=$(bash "$HS/install.sh" 2>&1) || { printf '%s\n' "$out" | tail -n 40; fail "install.sh exited non-zero with the scheduler and io.cost keys set"; }
[ -f "$RULE" ] || fail "$RULE was not rendered"
grep -q "KERNEL==\"$dev\"" "$RULE" || fail "$RULE does not match the derived disk $dev: $(cat "$RULE")"
[ ! -e "$OLD_MDT" ] || fail "MDT's old BFQ rule was not retired"
[ ! -e "$OLD_FOREIGN" ] || fail "the byte-identical non-MDT BFQ rule was not retired"
[ ! -e /etc/modules-load.d/mdt-bfq.conf ] || fail "the bfq modules-load file is still installed with IO_ROOT_SCHEDULER=mq-deadline"
printf '%s\n' "$out" | grep -q "byte-identical to a known repo copy" || fail "no NOTICE for the retired non-MDT rule"
ok "install.sh rendered $RULE (disk $dev, derived) and retired both BFQ rules"

cur=$(_mdt_io_sched_current "$dev")
echo "live /sys/block/$dev/queue/scheduler: $(cat "/sys/block/$dev/queue/scheduler")"
[ "$cur" = mq-deadline ] || fail "live scheduler of $dev is '$cur', expected mq-deadline"
model_line=$(grep -m1 "^$mm " "$CG/io.cost.model" || true)
qos_line=$(grep -m1 "^$mm " "$CG/io.cost.qos" || true)
echo "$model_line" | grep -q "rbps=100000000" || fail "live io.cost.model has no rbps=100000000: '$model_line'"
echo "$model_line" | grep -q "ctrl=user" || fail "live io.cost.model is not ctrl=user: '$model_line'"
echo "$qos_line" | grep -q "enable=1" || fail "live io.cost.qos is not enabled: '$qos_line'"
echo "$qos_line" | grep -q "rlat=5000" || fail "live io.cost.qos lacks rlat=5000: '$qos_line'"
systemctl is-enabled --quiet mdt-io-cost.service || fail "mdt-io-cost.service is not enabled (the settings would not survive a reboot)"
ok "LIVE: scheduler mq-deadline, io.cost.model [$model_line], io.cost.qos [$qos_line], mdt-io-cost.service enabled"

check=$(/usr/local/sbin/mdt-host-check.sh 2>&1 || true)
printf '%s\n' "$check" | grep -q "OK   $dev weight mechanism: io.cost" || fail "mdt-host-check.sh did not report io.cost as the weight mechanism"
printf '%s\n' "$check" | grep -q "OK   io.cost model verified" || fail "mdt-host-check.sh did not verify the io.cost model"
printf '%s\n' "$check" | grep -q "OK   io.cost qos verified" || fail "mdt-host-check.sh did not verify the io.cost QoS"
printf '%s\n' "$check" | grep -q "no disk uses BFQ" && fail "mdt-host-check.sh still prints the BFQ-only warning"
ok "mdt-host-check.sh reports the weight mechanism io.cost and verifies model and QoS"

# the boot unit re-applies from the env (a oneshot: restart it after drifting the QoS by hand)
echo "$mm enable=0" > "$CG/io.cost.qos"
systemctl restart mdt-io-cost.service || fail "mdt-io-cost.service failed"
grep -m1 "^$mm " "$CG/io.cost.qos" | grep -q "enable=1" || fail "mdt-io-cost.service did not re-apply the QoS from the env"
ok "mdt-io-cost.service re-applies the QoS from the env"

# ---- 2. back to kernel / kernel / off -----------------------------------------------------------
set_conf IO_ROOT_SCHEDULER kernel
set_conf IO_COST_MODEL kernel
set_conf IO_COST_QOS off
out=$(bash "$HS/install.sh" 2>&1) || { printf '%s\n' "$out" | tail -n 40; fail "install.sh exited non-zero for kernel/kernel/off"; }
[ ! -e "$RULE" ] || fail "$RULE still exists with IO_ROOT_SCHEDULER=kernel"
grep -m1 "^$mm " "$CG/io.cost.qos" | grep -q "enable=0" || fail "IO_COST_QOS=off did not write enable=0"
grep -m1 "^$mm " "$CG/io.cost.model" | grep -q "ctrl=auto" || fail "IO_COST_MODEL=kernel did not write ctrl=auto"
printf '%s\n' "$out" | grep -q "rotational/ssd HEURISTIC" || fail "no NOTICE about the kernel default model"
ok "kernel/kernel/off: the rule is gone, QoS reads enable=0 and the model ctrl=auto"
echo "VM IOCOST ACCEPTANCE PASSED"
