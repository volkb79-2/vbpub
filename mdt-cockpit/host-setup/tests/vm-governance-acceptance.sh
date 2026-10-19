#!/usr/bin/env bash
# dev.slice IO governance acceptance, run as root INSIDE the disposable QEMU
# guest by run-vm-acceptance.sh (never on a real host: it writes /etc/mdt,
# runs the reconcile and writes cgroupfs).  Real systemd, real cgroupfs, real
# size-0 control files.  Fake-based unit tests (test_io_governance.py) cover
# the logic; this proves it against the real kernel and systemd (MDT-016).
set -euo pipefail

CONF=/etc/mdt/host-setup.env
RECONCILE=/usr/local/sbin/mdt-dev-governance-reconcile.sh
LISTER=/usr/local/sbin/mdt-cgroup-io-limits.sh
LIB=/usr/local/sbin/mdt-container-io-caps.lib.sh
BASELINE=/var/lib/mdt/io-baseline.env
CG=/sys/fs/cgroup
MIB=1048576

fail() { echo "VM GOVERNANCE FAIL: $*" >&2; exit 1; }
ok() { echo "VM GOVERNANCE OK: $*"; }

[ "$(id -u)" = 0 ] || fail "must run as root in the guest"
systemd-detect-virt --container >/dev/null 2>&1 && fail "refusing to run in a container"
[ -x "$RECONCILE" ] || fail "reconcile not installed: $RECONCILE"
[ -x "$LISTER" ] || fail "listing tool not installed: $LISTER"

set_conf() { # set_conf KEY VALUE -- the key must already exist (host-setup.env.example declares every key)
  grep -q "^$1=" "$CONF" || fail "$1 is not declared in $CONF"
  sed -i "s|^$1=.*|$1=$2|" "$CONF"
}
clear_pins() {
  local tier field
  for field in RBPS WBPS RIOPS WIOPS; do
    set_conf "DEV_IO_${field}_MAX" ""
    for tier in INTERACTIVE BACKGROUND GATES BUILDKITD; do set_conf "DEV_${tier}_IO_${field}_MAX" ""; done
  done
}

# The whole disk systemd caps, resolved by the same helper the reconcile uses.
log() { :; }
# shellcheck disable=SC1090
. "$LIB"
# shellcheck disable=SC1090
. "$CONF"
IO_DEV_PATH="${IO_DEV_PATH:-$(findmnt -no SOURCE --target /var/lib/docker 2>/dev/null || findmnt -no SOURCE --target /)}"
case "$IO_DEV_PATH" in /dev/?*) ;; *) fail "no /dev block device for the guest root: '$IO_DEV_PATH'" ;; esac
MAJMIN=$(_mdt_dev_majmin "$IO_DEV_PATH") || fail "cannot resolve $IO_DEV_PATH to a whole disk"
echo "guest io device: $IO_DEV_PATH -> disk $MAJMIN"

field() { # field <cgroup-rel> <rbps|wbps|riops|wiops> -> the kernel's value for MAJMIN
  awk -v mm="$MAJMIN" -v f="$2" '$1==mm { for (i=2;i<=NF;i++) { split($i,a,"="); if (a[1]==f) print a[2] } }' "$CG/$1/io.max"
}
expect() { # expect <cgroup-rel> <field> <value>
  local got
  got=$(field "$1" "$2")
  [ "$got" = "$3" ] || fail "$1 io.max $2 is '${got:-<none>}', expected '$3' (io.max: $(tr '\n' ';' <"$CG/$1/io.max"))"
}
run_reconcile() { "$RECONCILE" >/tmp/reconcile.out 2>&1; }

for cgdir in dev.slice dev.slice/dev-gates.slice; do
  [ -d "$CG/$cgdir" ] || fail "$cgdir is not an active cgroup in the guest (install.sh should have started it)"
done

# 1. install.sh rendered the unit file static fallback (plain byte counts).
grep -Eq "^IOReadBandwidthMax=$IO_DEV_PATH [0-9]+$" /etc/systemd/system/dev.slice \
  || fail "dev.slice unit lacks a rendered byte-count IOReadBandwidthMax static: $(grep IO /etc/systemd/system/dev.slice | tr '\n' ';')"
ok "install.sh rendered the dev.slice static fallback"

# 2. real cgroupfs control files report size 0: a size test never sees the limit.
clear_pins
rm -f "$BASELINE"
run_reconcile || { cat /tmp/reconcile.out; fail "reconcile with NO baseline exited non-zero"; }
[ -n "$(cat "$CG/dev.slice/io.max")" ] || fail "dev.slice io.max is empty after a reconcile with no baseline (the MDT-016 failure)"
if [ -s "$CG/dev.slice/io.max" ]; then fail "expected cgroupfs io.max to report size 0 (the size-0 trap premise)"; fi
ok "cgroupfs io.max reports size 0 yet holds a limit; a size test would miss it"

# 3. NO baseline: the explicit fallback is held (read back from the guest cgroupfs).
expect dev.slice rbps $((150 * MIB)); expect dev.slice wbps $((150 * MIB))
expect dev.slice riops 500; expect dev.slice wiops 500
grep -q "is authoritative" /tmp/reconcile.out || fail "no read-back certification logged"
ok "no baseline: dev.slice holds the explicit static fallback"

# 4. STALE baseline (old schema): rejected as stale, fallback still held.
cat >"$BASELINE" <<'EOF'
SCHEMA_VERSION=2
MEASURE_METHOD=iocost-coef-gen
MEASURED_AT=2026-01-01T00:00:00Z
KERNEL_RELEASE=0.0.0-stale
RIOPS_MAX=94246
WIOPS_MAX=67748
RBW_MAX_BPS=4287506096
WBW_MAX_BPS=2001630742
EOF
run_reconcile || { cat /tmp/reconcile.out; fail "reconcile with a STALE baseline exited non-zero"; }
grep -qi "stale\|ignoring" /tmp/reconcile.out || fail "stale baseline not reported"
expect dev.slice riops 500; expect dev.slice wbps $((150 * MIB))
rm -f "$BASELINE"
ok "stale baseline: rejected, fallback held"

# 5. absolute keys win; gate tier gets bandwidth AND IOPS caps.
set_conf DEV_IO_RBPS_MAX 80M; set_conf DEV_IO_WBPS_MAX 40M
set_conf DEV_IO_RIOPS_MAX 800; set_conf DEV_IO_WIOPS_MAX 600
set_conf DEV_GATES_IO_RBPS_MAX 40M; set_conf DEV_GATES_IO_WBPS_MAX 20M
set_conf DEV_GATES_IO_RIOPS_MAX 300; set_conf DEV_GATES_IO_WIOPS_MAX 200
run_reconcile || { cat /tmp/reconcile.out; fail "reconcile with absolute keys exited non-zero"; }
expect dev.slice rbps $((80 * MIB)); expect dev.slice wbps $((40 * MIB))
expect dev.slice riops 800; expect dev.slice wiops 600
expect dev.slice/dev-gates.slice rbps $((40 * MIB)); expect dev.slice/dev-gates.slice wbps $((20 * MIB))
expect dev.slice/dev-gates.slice riops 300; expect dev.slice/dev-gates.slice wiops 200
ok "absolute keys applied; gate tier has bandwidth and IOPS caps"

# 6. the listing tool prints the applied caps from the real size-0 files.
out=$("$LISTER" dev.slice --all --systemd)
echo "$out"
echo "$out" | grep -Eq "ri=800 wi=600" || fail "listing tool does not show the dev.slice caps"
echo "$out" | grep -Eq "ri=300 wi=200" || fail "listing tool does not show the effective gate caps"
echo "$out" | grep -q "DropInPaths=" || fail "--systemd did not print drop-in paths"
ok "mdt-cgroup-io-limits.sh shows the applied caps from real cgroupfs"

# 7. read-back failure path: a systemctl that accepts set-property but changes
# nothing, on top of a drifted kernel value, must make the reconcile exit non-zero.
SHIM=$(mktemp -d)
cat >"$SHIM/systemctl" <<'EOF'
#!/usr/bin/env bash
[ "${1:-}" = set-property ] && exit 0
exec /usr/bin/systemctl "$@"
EOF
chmod +x "$SHIM/systemctl"
echo "$MAJMIN rbps=1048576 wbps=1048576 riops=10 wiops=10" >"$CG/dev.slice/io.max"
if PATH="$SHIM:$PATH" "$RECONCILE" >/tmp/reconcile-bad.out 2>&1; then
  cat /tmp/reconcile-bad.out
  fail "reconcile exited 0 although the kernel does not hold the intended values"
fi
grep -q "NOT in effect" /tmp/reconcile-bad.out || fail "read-back failure not logged as ERROR"
if grep -q "is authoritative" /tmp/reconcile-bad.out; then fail "certified despite a mismatch"; fi
rm -rf "$SHIM"
ok "read-back failure path exits non-zero and does not certify"

# 8. recovery after drift. Do NOT assume set-property with an UNCHANGED value
# re-applies to the cgroup: first set a DIFFERENT value (which must change the
# kernel value), then the target, and read the kernel back each time.
set_conf DEV_IO_RIOPS_MAX 700
run_reconcile || { cat /tmp/reconcile.out; fail "reconcile with a changed value exited non-zero"; }
expect dev.slice riops 700
set_conf DEV_IO_RIOPS_MAX 800
run_reconcile || { cat /tmp/reconcile.out; fail "recovery reconcile exited non-zero"; }
expect dev.slice riops 800; expect dev.slice/dev-gates.slice wiops 200
ok "recovery: a different value, then the target, each read back from the kernel"

echo "VM GOVERNANCE ACCEPTANCE PASSED"
