#!/usr/bin/env bash
# Upgrade-path acceptance, run as root INSIDE the disposable QEMU guest by
# run-vm-acceptance.sh after vm-governance-acceptance.sh (MDT-HS-02). The earlier VM steps
# always installed from a fresh, COMPLETE env file, which is why the real upgrade failure
# was never seen: an EXISTING /etc/mdt/host-setup.env that predates the runtime-optional
# keys was refused by the Python validator before the shell exemption could run.
#
#   1. old env file (runtime-optional keys removed) + plain install.sh  -> succeeds, NOTICEs
#   2. an unconfirmed terminate guard policy                            -> refuses (rc 2),
#      touches nothing, and the running guard stays active
#   3. the same policy once its confirmation is recorded                -> proceeds, guard kept
#   4. DAMON_STAT defaults to disabled
# Real systemd, real services. Never run this on a real host.
set -euo pipefail

CONF=/etc/mdt/host-setup.env
RECORD=/var/lib/mdt/guard-terminate.confirmed
LIB=/usr/local/sbin/mdt-container-io-caps.lib.sh
HS=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

fail() { echo "VM UPGRADE FAIL: $*" >&2; exit 1; }
ok() { echo "VM UPGRADE OK: $*"; }

[ "$(id -u)" = 0 ] || fail "must run as root in the guest"
systemd-detect-virt --container >/dev/null 2>&1 && fail "refusing to run in a container"
cd "$HS"

optional_re=$(grep -vE '^[[:space:]]*(#|$)' runtime-optional-keys.re | head -n 1)
[ -n "$optional_re" ] || fail "runtime-optional-keys.re is empty"

# ---- 1. an OLD env file: the example's keys minus the runtime-optional ones ----------
cp "$CONF" /tmp/host-setup.env.before-upgrade
removed=0
: > /tmp/host-setup.env.old
while IFS= read -r line; do
    key=${line%%=*}
    if [[ "$line" =~ ^[A-Z][A-Z0-9_]*= ]] && [[ "$key" =~ $optional_re ]]; then
        removed=$((removed + 1))
        continue
    fi
    printf '%s\n' "$line" >> /tmp/host-setup.env.old
done < "$CONF"
[ "$removed" -gt 0 ] || fail "the installed env had no runtime-optional key to remove (nothing to upgrade from)"
# the rest of the file must still pass as an old, complete-but-for-the-optional-keys file
install -m 0644 /tmp/host-setup.env.old "$CONF"
grep -qE '^DEV_GATES_IO_WBPS_MAX=' "$CONF" && fail "the old env file still carries an optional key"

out=$(bash "$HS/install.sh" 2>&1) || { printf '%s\n' "$out" | tail -n 30; fail "plain install.sh on an OLD env file exited non-zero"; }
printf '%s\n' "$out" | grep -q "NOTICE: .* runtime-optional key(s) are absent" || fail "no NOTICE about the absent runtime-optional keys"
printf '%s\n' "$out" | grep -q "NOTICE: no DEV_IO_{RBPS,WBPS,RIOPS,WIOPS}_MAX pin is set" || fail "no NOTICE naming the effective cap source"
printf '%s\n' "$out" | grep -q "missing declared keys" && fail "install.sh reported missing declared keys"
cmp -s /tmp/host-setup.env.old "$CONF" || fail "a non-wizard install rewrote the existing env file"
systemctl is-active --quiet mdt-buildkit-guard.service || fail "the guard is not active after the upgrade install"
ok "plain install.sh on an old env file (no optional keys) succeeded with NOTICEs and left the env untouched"

# ---- 2. unconfirmed terminate: refuse first, touch nothing, keep the running guard ----
rm -f "$RECORD"
sed -i 's|^BUILDX_ACCIDENTAL_CONTAINER_POLICY=.*|BUILDX_ACCIDENTAL_CONTAINER_POLICY=terminate|' "$CONF"
grep -q '^BUILDX_ACCIDENTAL_CONTAINER_POLICY=terminate$' "$CONF" || fail "could not set the terminate policy"
before=$(stat -c '%Y %s' /etc/systemd/system/dev.slice /usr/local/sbin/mdt-dev-governance-reconcile.sh /etc/mdt/buildkitd.toml)
set +e
out=$(bash "$HS/install.sh" 2>&1); rc=$?
set -e
[ "$rc" = 2 ] || { printf '%s\n' "$out" | tail -n 20; fail "unconfirmed terminate exited $rc, expected 2"; }
printf '%s\n' "$out" | grep -q "has no recorded confirmation" || fail "the refusal does not say why"
systemctl is-active --quiet mdt-buildkit-guard.service || fail "the refusal tore down the running guard"
systemctl is-enabled --quiet mdt-buildkit-guard.service || fail "the refusal disabled the guard"
[ "$before" = "$(stat -c '%Y %s' /etc/systemd/system/dev.slice /usr/local/sbin/mdt-dev-governance-reconcile.sh /etc/mdt/buildkitd.toml)" ] \
    || fail "the refusal modified installed files (it must refuse before touching anything)"
[ ! -e "$RECORD" ] || fail "a refusal wrote a confirmation record"
ok "unconfirmed terminate refused (rc 2) before any mutation; the running guard stayed active and enabled"

# ---- 3. the same policy once confirmed (what the wizard records) --------------------
bash -c "log(){ :; }; . '$LIB'; _mdt_guard_record_write '$RECORD' terminate"
grep -q '^confirmed_at=' "$RECORD" || fail "no timestamp in the confirmation record"
out=$(bash "$HS/install.sh" 2>&1) || { printf '%s\n' "$out" | tail -n 20; fail "a confirmed terminate policy was refused on re-install"; }
printf '%s\n' "$out" | grep -q "was already confirmed" || fail "the re-install did not use the recorded confirmation"
systemctl is-active --quiet mdt-buildkit-guard.service || fail "the guard is not running after a confirmed re-install"
ok "an unchanged, already-confirmed terminate policy proceeds and keeps the guard"

# back to report-only: the record is removed, so a later terminate needs confirming again
sed -i 's|^BUILDX_ACCIDENTAL_CONTAINER_POLICY=.*|BUILDX_ACCIDENTAL_CONTAINER_POLICY=report-only|' "$CONF"
out=$(bash "$HS/install.sh" 2>&1) || { printf '%s\n' "$out" | tail -n 30; fail "install.sh with report-only failed"; }
[ ! -e "$RECORD" ] || fail "report-only did not clear the confirmation record"
ok "report-only clears the record"

# ---- 4. DAMON_STAT defaults to disabled ---------------------------------------------
cp /tmp/host-setup.env.before-upgrade "$CONF"
grep -q '^DAMON_STAT_DISABLE_DEFAULT="yes"$' "$HS/host-setup.env.example" || fail "the example does not default to disabling DAMON_STAT"
[ -f /etc/tmpfiles.d/mdt-damon-stat.conf ] || fail "the DAMON_STAT tmpfiles rule is not installed"
if [ -e /sys/module/damon_stat/parameters/enabled ]; then
    [ "$(cat /sys/module/damon_stat/parameters/enabled)" = N ] || fail "DAMON_STAT is still enabled"
fi
ok "DAMON_STAT is disabled by default"

echo "VM UPGRADE ACCEPTANCE PASSED"
