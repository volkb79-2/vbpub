#!/usr/bin/env bash
# Guest-side acceptance for the cgprofile socket-activated host service (plan 7, CP-61).
#
# Runs INSIDE a disposable QEMU guest started by the existing Debian VM harness
# (scripts/debian-install-v2/testing/vm/run-vm-harness.sh), as root. It is never run on a
# cockpit host: it installs and uninstalls the whole service and creates test users.
#
#   run-vm-harness.sh copy  RUN cgprofile/tools  /root/tools
#   run-vm-harness.sh copy  RUN <wheel directory>              /root/wheels
#   run-vm-harness.sh ssh   RUN -- sudo env CGP_DISPOSABLE_GUEST=yes CGPROFILE_PIN=... CLI_EXTENDED_PIN=... AUGEASWRAP_PIN=... \
#       bash /root/tools/vm-socket-acceptance.sh clean-guest
#   (on a SECOND fresh guest; the script seeds the old host-setup files itself)
#   run-vm-harness.sh ssh   RUN2 -- sudo env CGP_DISPOSABLE_GUEST=yes CGPROFILE_PIN=... CLI_EXTENDED_PIN=... AUGEASWRAP_PIN=... \
#       bash /root/tools/vm-socket-acceptance.sh old-layout-guest
#
# Guard: a VM is not enough (the live netcup hosts are VMs). It refuses unless
# CGP_DISPOSABLE_GUEST=yes is set, the guest is a run-vm-harness guest (hostname vbpub-test,
# user tester) and cgprofile is not installed or running yet; see need_guest.
# Required environment: CGPROFILE_PIN, CLI_EXTENDED_PIN and AUGEASWRAP_PIN (<version>@sha256:<hex>).
# Optional: WHEELS (default /root/wheels), OLD_WHEEL_PIN (an older cgprofile pin that exists
# in WHEELS, for the real --allow-downgrade rollback), CONTROL_USER (default root), and
# FORGOTTEN_USER (default cgptest-retired), a disposable account removed during the rerun case.
#
# The TTY wizard install is not automated (questionary needs a pty and key input): run
# `cgprofiled install` without flags once by hand on a TTY on the clean guest, and finish it with
# `cgprofiled doctor --json`. Everything else below is.
set -u

WHEELS="${WHEELS:-/root/wheels}"
CONTROL_USER="${CONTROL_USER:-root}"
FORGOTTEN_USER="${FORGOTTEN_USER:-cgptest-retired}"
FAILED=0
CASE=""

pass() { printf 'PASS  [%s] %s\n' "$CASE" "$1"; }
fail() { printf 'FAIL  [%s] %s\n' "$CASE" "$1"; FAILED=$((FAILED + 1)); }
check() {  # check "description" command...
    local description="$1"; shift
    if "$@" >/tmp/vm-accept.out 2>&1; then pass "$description"; else fail "$description"; sed 's/^/        /' /tmp/vm-accept.out; fi
}
expect_exit() {  # expect_exit CODE "description" command...
    local want="$1" description="$2"; shift 2
    "$@" >/tmp/vm-accept.out 2>&1
    local got=$?
    if [ "$got" -eq "$want" ]; then pass "$description (exit $got)"; else fail "$description (exit $got, wanted $want)"; sed 's/^/        /' /tmp/vm-accept.out; fi
}
inode() { stat -c %i "$1"; }
wait_for_socket() {  # wait_for_socket PATH SECONDS: a TCG guest needs about 5 s to start the daemon
    local waited=0
    while [ "$waited" -lt "$2" ] && [ ! -S "$1" ]; do sleep 1; waited=$((waited + 1)); done
}
group_has_member() {
    getent group "$1" | cut -d: -f4 | tr ',' '\n' | grep -Fx "$2" >/dev/null
}

refuse() { echo "refusing: $1" >&2; exit 2; }

# A VM is NOT proof of a disposable guest: the live netcup hosts are VMs too. This script
# installs and removes the whole service and, in old-layout-guest, deletes /opt/cgprofile,
# /run/cgprofile and /etc/cgprofile, so it runs only where ALL of these hold:
#   - root, a VM, and the explicit opt-in CGP_DISPOSABLE_GUEST=yes on the command line;
#   - a guest created by run-vm-harness.sh (vmctl's cloud-init gives it the hostname
#     vbpub-test and the user tester);
#   - nothing of cgprofile is installed or running yet (checked before anything is seeded).
# The unit test harness may set CGP_TEST_ROOT only together with CGP_TEST_MODE=1;
# live VM acceptance runs must never redirect the installed-path checks.
need_guest() {
    local root=""
    [ "$(id -u)" -eq 0 ] || { echo "run as root inside the guest" >&2; exit 2; }
    if [ -n "${CGP_TEST_ROOT:-}" ] || [ "${CGP_TEST_MODE:-}" = 1 ]; then
        [ "${CGP_TEST_MODE:-}" = 1 ] && [ -n "${CGP_TEST_ROOT:-}" ] \
            || refuse "CGP_TEST_ROOT is only allowed with CGP_TEST_MODE=1 in the unit test harness"
        root="$CGP_TEST_ROOT"
    fi
    [ "${CGP_DISPOSABLE_GUEST:-}" = yes ] || refuse "set CGP_DISPOSABLE_GUEST=yes to confirm this is a disposable run-vm-harness guest"
    systemd-detect-virt --vm >/dev/null 2>&1 || refuse "not a VM"
    [ "$(hostname)" = vbpub-test ] && id tester >/dev/null 2>&1 \
        || refuse "this is not a run-vm-harness guest (hostname vbpub-test and user tester expected)"
    local path
    for path in /opt/cgprofile /etc/cgprofile /etc/systemd/system/cgprofiled.service /run/cgprofile; do
        [ ! -e "$root$path" ] && [ ! -L "$root$path" ] || refuse "$path already exists: cgprofile is installed here, this is not a fresh disposable guest"
    done
    ! systemctl is-active --quiet cgprofiled.service || refuse "cgprofiled.service is running: this is not a fresh disposable guest"
    : "${CGPROFILE_PIN:?set CGPROFILE_PIN}" "${CLI_EXTENDED_PIN:?set CLI_EXTENDED_PIN}" \
      "${AUGEASWRAP_PIN:?set AUGEASWRAP_PIN}"
}

# The installer must not depend on the caller's endpoint variables (a fresh host has none, and
# load_endpoints exports them into this shell after the first install), so every install and
# uninstall runs with both unset.
NOENDPOINT="env -u CGPROFILE_CONTROL_SOCKET -u CGPROFILE_OBSERVER_SOCKET"

# The bootstrap route: the three pinned wheels on PYTHONPATH, no venv needed yet.
bootstrap() {
    local cgprofile_wheel clix_wheel augeaswrap_wheel
    cgprofile_wheel="$(ls "$WHEELS"/cgprofile-*-py3-none-any.whl | head -n1)"
    clix_wheel="$(ls "$WHEELS"/cli_extended-*-py3-none-any.whl | head -n1)"
    augeaswrap_wheel="$(ls "$WHEELS"/augeaswrap-*-py3-none-any.whl | head -n1)"
    $NOENDPOINT PYTHONPATH="$cgprofile_wheel:$clix_wheel:$augeaswrap_wheel" python3 -m cgprofile.cli.cgprofiled "$@"
}
CGPD=/opt/cgprofile/current/venv/bin/cgprofiled
CGPCTL=/opt/cgprofile/current/venv/bin/cgprofilectl

install_flags=(--wheel "${CGPROFILE_PIN:-}" --cli-extended-wheel "${CLI_EXTENDED_PIN:-}" --wheel-source "$WHEELS"
               --augeaswrap-wheel "${AUGEASWRAP_PIN:-}"
               --control-group cgprofile-control --observer-group cgprofile-observer --enable --yes)

endpoint_cli() {
    local clix_wheel augeaswrap_wheel
    clix_wheel="$(ls "$WHEELS"/cli_extended-*-py3-none-any.whl | head -n1)"
    augeaswrap_wheel="$(ls "$WHEELS"/augeaswrap-*-py3-none-any.whl | head -n1)"
    PYTHONPATH="$clix_wheel:$augeaswrap_wheel" python3 -m augeaswrap "$@"
}

load_endpoints() {
    [ -r /etc/host-endpoints.env ] || refuse "the host endpoint aggregate is missing after install"
    set -a
    . /etc/host-endpoints.env
    set +a
    : "${CGPROFILE_CONTROL_SOCKET:?host endpoint record lacks CGPROFILE_CONTROL_SOCKET}" \
      "${CGPROFILE_OBSERVER_SOCKET:?host endpoint record lacks CGPROFILE_OBSERVER_SOCKET}"
    CONTROL="$CGPROFILE_CONTROL_SOCKET"
    OBSERVER="$CGPROFILE_OBSERVER_SOCKET"
}

p5_refusal() {
    local description="$1" config_before record_before pending_before current_before opt_before
    shift
    config_before="$(sha256sum /etc/cgprofile/cgprofiled.toml)"
    record_before="$(sha256sum /etc/cgprofile/install.toml)"
    pending_before="$(if [ -e /opt/cgprofile/PENDING ]; then cat /opt/cgprofile/PENDING; else echo absent; fi)"
    current_before="$(readlink /opt/cgprofile/current)"
    opt_before="$(find /opt/cgprofile -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)"
    expect_exit 2 "$description" $NOENDPOINT "$CGPD" install --yes "$@"
    if [ "$config_before" = "$(sha256sum /etc/cgprofile/cgprofiled.toml)" ] \
        && [ "$record_before" = "$(sha256sum /etc/cgprofile/install.toml)" ] \
        && [ "$pending_before" = "$(if [ -e /opt/cgprofile/PENDING ]; then cat /opt/cgprofile/PENDING; else echo absent; fi)" ] \
        && [ "$current_before" = "$(readlink /opt/cgprofile/current)" ] \
        && [ "$opt_before" = "$(find /opt/cgprofile -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)" ]; then
        pass "$description leaves config, record and install tree untouched"
    else
        fail "$description changed an install file"
    fi
}

p5_groups() {
    local docker_gid observer_gid docker_created=no
    if getent group docker >/dev/null; then
        docker_gid="$(getent group docker | cut -d: -f3)"
    else
        groupadd --system docker || refuse "could not create the temporary docker group for P-5 acceptance"
        docker_created=yes
        docker_gid="$(getent group docker | cut -d: -f3)"
    fi
    observer_gid="$(getent group cgprofile-observer | cut -d: -f3)"
    groupadd --non-unique --gid "$docker_gid" cgptest-docker-gid \
        || refuse "could not create the temporary docker-GID alias group"
    groupadd --non-unique --gid "$observer_gid" cgptest-observer-gid \
        || refuse "could not create the temporary duplicate-GID group"

    p5_refusal "control refuses the docker group by name" --control-group docker
    if grep -q "the control group 'docker' is the docker group" /tmp/vm-accept.out; then
        pass "control refusal names the unsafe group and dedicated default"
    else
        fail "control refusal does not explain the docker group"
    fi
    p5_refusal "observer refuses the docker group by name" --observer-group docker
    if grep -q "the observer group 'docker' is the docker group" /tmp/vm-accept.out; then
        pass "observer refusal names the unsafe group and dedicated default"
    else
        fail "observer refusal does not explain the docker group"
    fi
    p5_refusal "control refuses a group with the docker GID" --control-group cgptest-docker-gid
    p5_refusal "observer refuses a group with the docker GID" --observer-group cgptest-docker-gid
    # `root` is GID 0 itself: no alias group is created (C4: groupdel refuses to remove a group that is
    # the primary group of root, so an alias with GID 0 could never be cleaned up).
    p5_refusal "control refuses GID 0" --control-group root
    p5_refusal "observer refuses GID 0" --observer-group root
    p5_refusal "control refuses the observer group name" --control-group cgprofile-observer
    p5_refusal "control refuses a duplicate level GID" --control-group cgptest-observer-gid

    groupdel cgptest-docker-gid
    groupdel cgptest-observer-gid
    if [ "$docker_created" = yes ]; then
        groupdel docker
    fi
}

clean_guest() {
    CASE="1 endpoint collision preflight"
    expect_exit 0 "seed another owner's colliding control endpoint" endpoint_cli endpoints publish \
        --owner ren-b-collision --writer 'vm-socket-acceptance 3.1' \
        --entry CGPROFILE_CONTROL_SOCKET=/run/ren-b-collision/control.sock
    expect_exit 2 "cgprofile preflight refuses the endpoint key collision" \
        bootstrap install "${install_flags[@]}" --dry-run
    grep -q 'ren-b-collision' /tmp/vm-accept.out \
        && pass "collision refusal names the other owner" \
        || fail "collision refusal omits the other owner"
    expect_exit 0 "withdraw the seeded collision fragment" endpoint_cli endpoints withdraw --owner ren-b-collision

    CASE="1 install + doctor"
    id "$FORGOTTEN_USER" >/dev/null 2>&1 && refuse "the temporary forgotten-user account already exists"
    useradd --no-create-home --shell /usr/sbin/nologin "$FORGOTTEN_USER"
    expect_exit 0 "unattended pinned install" bootstrap install "${install_flags[@]}" \
        --add-user "control=$CONTROL_USER" --add-user "observer=$FORGOTTEN_USER"
    load_endpoints
    check "cgprofile fragment publishes both sockets and group IDs" bash -c \
        "grep -Fx 'CGPROFILE_CONTROL_SOCKET=$CONTROL' /etc/host-endpoints.d/cgprofile.env && grep -Fx 'CGPROFILE_OBSERVER_SOCKET=$OBSERVER' /etc/host-endpoints.d/cgprofile.env && grep -q '^CGPROFILE_CONTROL_GID=' /etc/host-endpoints.d/cgprofile.env && grep -q '^CGPROFILE_OBSERVER_GID=' /etc/host-endpoints.d/cgprofile.env"
    check "aggregate and environment block contain the endpoints" bash -c \
        "grep -Fx 'CGPROFILE_CONTROL_SOCKET=$CONTROL' /etc/host-endpoints.env && grep -q '^# BEGIN host-endpoints' /etc/environment && grep -q '^CGPROFILE_OBSERVER_SOCKET=$OBSERVER$' /etc/environment"
    check "profile.d sources the aggregate" grep -Fq '. /etc/host-endpoints.env' /etc/profile.d/host-endpoints.sh
    expect_exit 2 "env-i client refuses without the control endpoint" env -i PATH="$PATH" "$CGPCTL" status
    grep -q 'CGPROFILE_CONTROL_SOCKET' /tmp/vm-accept.out \
        && pass "unset endpoint refusal names CGPROFILE_CONTROL_SOCKET" \
        || fail "unset endpoint refusal omits CGPROFILE_CONTROL_SOCKET"
    check "cgprofiled doctor --json is green" bash -c "$CGPD doctor --json | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if all(c[\"status\"] in (\"ok\",\"skip\") for c in d[\"checks\"]) else 1)'"
    check "cgprofilectl doctor --json runs" bash -c "$CGPCTL doctor --json >/dev/null"

    CASE="1 P-5 install flag refusals"
    p5_groups

    CASE="2 layout + systemd verify"
    for path in /run/cgprofile /run/cgprofile/control /run/cgprofile/observer; do
        check "$path is root:root 0755" bash -c "[ \"\$(stat -c '%U:%G %a' $path)\" = 'root:root 755' ]"
    done
    check "/run/cgprofile/state is root:root 0700 (nested RuntimeDirectory leaves the parent 0755)" \
        bash -c "[ \"\$(stat -c '%U:%G %a' /run/cgprofile/state)\" = 'root:root 700' ] && [ \"\$(stat -c '%U:%G %a' /run/cgprofile)\" = 'root:root 755' ]"
    check "control node root:cgprofile-control 0660" bash -c "[ \"\$(stat -c '%U:%G %a' '$CONTROL')\" = 'root:cgprofile-control 660' ]"
    check "observer node root:cgprofile-observer 0660" bash -c "[ \"\$(stat -c '%U:%G %a' '$OBSERVER')\" = 'root:cgprofile-observer 660' ]"
    check "systemd-analyze verify" systemd-analyze verify /etc/systemd/system/cgprofiled.service \
        /etc/systemd/system/cgprofile-control.socket /etc/systemd/system/cgprofile-observer.socket
    for unit in control observer; do
        check "cgprofile-$unit.socket has exactly one ListenStream" \
            bash -c "[ \"\$(grep -c '^ListenStream=' /etc/systemd/system/cgprofile-$unit.socket)\" = 1 ]"
    done
    check "old-layout nodes are absent" bash -c "[ ! -e /run/cgprofile/ctl.sock ] && [ ! -e /run/cgprofile/observer/resource-observer.sock ]"

    CASE="3 restarts keep or change the inode as designed"
    local control_before observer_before
    control_before="$(inode $CONTROL)"; observer_before="$(inode $OBSERVER)"
    check "restart cgprofiled.service" systemctl restart cgprofiled.service
    [ "$(inode $CONTROL)" = "$control_before" ] && [ "$(inode $OBSERVER)" = "$observer_before" ] \
        && pass "service restart keeps both inodes" || fail "service restart changed an inode"
    check "both levels answer after the service restart" bash -c "$CGPCTL status --json >/dev/null && $CGPD doctor --check connect --json >/dev/null"
    local service_pid
    service_pid="$(systemctl show -p MainPID --value cgprofiled.service)"
    check "restart cgprofile-control.socket" systemctl restart cgprofile-control.socket
    [ "$(inode $CONTROL)" != "$control_before" ] && pass "control socket restart gives a new inode" || fail "control inode unchanged"
    sleep 1
    check "the service restarted through Requires=" bash -c "[ \"\$(systemctl show -p MainPID --value cgprofiled.service)\" != '$service_pid' ]"
    check "control answers on the new inode" bash -c "$CGPCTL status --json >/dev/null"
    check "restart cgprofile-observer.socket" systemctl restart cgprofile-observer.socket
    check "observer answers on its new inode" bash -c "$CGPD doctor --check connect --json >/dev/null"

    CASE="4 activation contract"
    expect_exit 2 "cgprofiled run without LISTEN_FDS and without --self-bind refuses" env -u LISTEN_FDS -u LISTEN_PID "$CGPD" run
    local cfg=/tmp/cgp-selfbind.toml dir=/tmp/cgp-selfbind
    mkdir -p "$dir/control" "$dir/observer" "$dir/sessions"
    cat >"$cfg" <<EOF
sessions_dir = "$dir/sessions"

[sockets.control]
path = "$dir/control/cgprofile.sock"
group = "cgprofile-control"
mode = "0660"

[sockets.observer]
path = "$dir/observer/cgprofile.sock"
group = "cgprofile-observer"
mode = "0660"
EOF
    # C2: dedicated groups (a GID-0 group is refused by P-5) and the daemon must be provably inside
    # cgprofile.slice, or it refuses to start.
    systemd-run --quiet --unit cgp-selfbind-test --slice cgprofile.slice --collect "$CGPD" --config "$cfg" run --self-bind
    wait_for_socket "$dir/control/cgprofile.sock" 30
    check "--self-bind serves explicit test paths" test -S "$dir/control/cgprofile.sock"
    systemctl stop cgp-selfbind-test.service >/dev/null 2>&1 || true

    CASE="5 observer-only user"
    id cgptest-obs >/dev/null 2>&1 || useradd --no-create-home --shell /usr/sbin/nologin cgptest-obs
    gpasswd -a cgptest-obs cgprofile-observer
    check "observer endpoint answers for the observer-only user" runuser -u cgptest-obs -- python3 - <<EOF
import json, socket, threading
# C1: the observer serves contract 2 resource-limits (and 3 window verbs) only; a contract-1 host
# request is refused. observer-refused is an answer from a live endpoint to a caller outside the gates slice.
s = socket.socket(socket.AF_UNIX); s.connect("$OBSERVER")
s.sendall((json.dumps({"verb": "resource-limits", "args": {"thread_id": threading.get_native_id()}, "contract": 2}) + "\n").encode())
body = json.loads(s.makefile().readline())
assert body["contract"] == 2 and (body["ok"] is True or body["error"]["code"] == "observer-refused"), body
EOF
    check "control is EACCES for the observer-only user" runuser -u cgptest-obs -- python3 - <<EOF
import socket
s = socket.socket(socket.AF_UNIX)
try:
    s.connect("$CONTROL")
except PermissionError:
    raise SystemExit(0)
raise SystemExit(1)
EOF
    check "doctor reports control connect as skip for that caller" bash -c \
        "runuser -u cgptest-obs -- $CGPD doctor --check connect --json | grep -q 'skipped expected access for control'"

    CASE="7 downgrade refusal"
    expect_exit 2 "an older pin is refused without --allow-downgrade (before any fetch)" \
        bootstrap install --wheel "2.1.0@sha256:$(printf '0%.0s' $(seq 64))" --cli-extended-wheel "$CLI_EXTENDED_PIN" \
        --wheel-source "$WHEELS" --control-group cgprofile-control --observer-group cgprofile-observer --enable --yes
    if [ -n "${OLD_WHEEL_PIN:-}" ]; then
        expect_exit 0 "--allow-downgrade rolls back and removes the socket units" \
            bootstrap install --wheel "$OLD_WHEEL_PIN" --cli-extended-wheel "$CLI_EXTENDED_PIN" --wheel-source "$WHEELS" \
            --control-group cgprofile-control --observer-group cgprofile-observer --enable --yes --allow-downgrade
        check "socket units are gone after the rollback" bash -c "[ ! -e /etc/systemd/system/cgprofile-control.socket ]"
        expect_exit 0 "rolling forward again works" bootstrap install "${install_flags[@]}"
    fi

    CASE="9 enable of a slice without [Install]"
    check "systemctl enable cgprofile.slice returns 0" systemctl enable cgprofile.slice

    CASE="10 rerun with a saved record that lists users (H2)"
    check "control user is a member before the rerun" group_has_member cgprofile-control "$CONTROL_USER"
    check "observer test user is a member before the rerun" group_has_member cgprofile-observer "$FORGOTTEN_USER"
    expect_exit 0 "cgprofiled install --yes with no other flag reapplies the recorded inputs" $NOENDPOINT "$CGPD" install --yes
    check "install.toml still lists the control user" grep -q "control_users = \[\"$CONTROL_USER\"\]" /etc/cgprofile/install.toml
    check "install.toml still lists the observer test user" grep -q "observer_users = \[\"$FORGOTTEN_USER\"\]" /etc/cgprofile/install.toml
    check "rerun without --add-user keeps control membership" group_has_member cgprofile-control "$CONTROL_USER"
    check "rerun without --add-user keeps observer membership" group_has_member cgprofile-observer "$FORGOTTEN_USER"
    check "a config edit survives the rerun" bash -c "echo '# kept' >> /etc/cgprofile/cgprofiled.toml && $NOENDPOINT $CGPD install --yes && grep -q '^# kept' /etc/cgprofile/cgprofiled.toml"
    userdel "$FORGOTTEN_USER"
    expect_exit 0 "rerun drops a recorded user whose account was deleted" $NOENDPOINT "$CGPD" install --yes
    if grep -q "dropped recorded users that no longer exist: $FORGOTTEN_USER" /tmp/vm-accept.out; then
        pass "install reports the deleted recorded user"
    else
        fail "install did not report the deleted recorded user"
    fi
    if grep -q "$FORGOTTEN_USER" /etc/cgprofile/install.toml; then
        fail "install record still contains the deleted account"
    else
        pass "install record drops the deleted account"
    fi

    CASE="8 uninstall contract (venv route, twice)"
    uninstall_checks "$NOENDPOINT $CGPD"

    CASE="8 uninstall contract (zipimport route)"
    expect_exit 0 "reinstall" bootstrap install "${install_flags[@]}"
    load_endpoints
    uninstall_checks "bootstrap"
}

uninstall_checks() {  # uninstall_checks "<command words>"
    local runner="$1"
    local slice_before
    slice_before="$(systemctl show -p ActiveState --value cgprofile.slice)"
    [ "$slice_before" = active ] && pass "cgprofile.slice is active before uninstall" || fail "slice state before: $slice_before"
    expect_exit 0 "uninstall --yes" $runner uninstall --yes
    check "cgprofile.slice is not active after uninstall (and not merely unloaded)" \
        bash -c "[ \"\$(systemctl show -p ActiveState --value cgprofile.slice)\" != active ]"
    for leftover in /etc/systemd/system/cgprofiled.service /etc/systemd/system/cgprofile.slice \
        /etc/systemd/system/cgprofile-control.socket /etc/systemd/system/cgprofile-observer.socket \
        /etc/sysusers.d/cgprofile.conf /etc/cgprofile /opt/cgprofile /run/cgprofile /usr/local/bin/cgprofilectl; do
        check "$leftover is gone" bash -c "[ ! -e $leftover ] && [ ! -L $leftover ]"
    done
    check "cgprofile endpoint fragment and aggregate keys are withdrawn" bash -c \
        "[ ! -e /etc/host-endpoints.d/cgprofile.env ] && { [ ! -e /etc/host-endpoints.env ] || { ! grep -q '^# owner=cgprofile$' /etc/host-endpoints.env && ! grep -q '^CGPROFILE_.*_SOCKET=' /etc/host-endpoints.env; }; }"
    check "no failed cgprofile unit remains" bash -c "! systemctl --failed --no-legend | grep -q cgp"
    check "groups and /var/lib/cgprofile remain by default" bash -c "getent group cgprofile-control >/dev/null && getent group cgprofile-observer >/dev/null && [ -d /var/lib/cgprofile ]"
    expect_exit 0 "a second uninstall is a clean no-op" bootstrap uninstall --yes
}

old_layout_guest() {
    CASE="6 P-11 old-layout refusal"
    seed_old_layout
    expect_exit 2 "install refuses the seeded host-setup state" bootstrap install "${install_flags[@]}"
    grep -q 'mdt-cgprofile.conf' /tmp/vm-accept.out && pass "the refusal names the tmpfiles rule" || fail "refusal text lacks the tmpfiles rule"
    # C3: the seeded config carries the removed ctl_socket key; the refusal still has to list the removal steps.
    grep -q 'old config keys: ctl_socket' /tmp/vm-accept.out && grep -q 'Manual steps:' /tmp/vm-accept.out \
        && pass "the refusal lists the old config key and the manual removal steps" \
        || fail "refusal text lacks the old config key or the manual removal list"
    grep -q '2.x layout: /etc/systemd/system/cgpd.service' /tmp/vm-accept.out \
        && grep -q '2.x layout: /usr/local/bin/cgpctl' /tmp/vm-accept.out \
        && pass "the refusal names the 2.x layout" || fail "refusal text lacks the 2.x layout paths"
    CASE="8 uninstall on an mdt-style install"
    expect_exit 2 "uninstall refuses before changing anything" bootstrap uninstall --yes
    check "the seeded files are untouched" bash -c "[ -f /etc/tmpfiles.d/mdt-cgprofile.conf ] && [ -f /opt/cgprofile/2.1.0/MANIFEST ] && [ -f /etc/systemd/system/cgpd.service ] && [ -L /usr/local/bin/cgpctl ]"
    CASE="6 P-11 after manual removal"
    # Only what seed_old_layout wrote may be removed: its marker proves this run created it.
    [ -f /opt/cgprofile/.vm-acceptance-seed ] || refuse "the seed marker is missing; not deleting /opt/cgprofile"
    rm -f /etc/tmpfiles.d/mdt-cgprofile.conf /etc/tmpfiles.d/mdt-cgprofile-observer.conf \
        /etc/sysusers.d/mdt-cgprofile.conf /etc/systemd/system/cgpd.service /usr/local/bin/cgpctl
    rmdir /etc/systemd/system/cgpd.service.d
    rm -rf /opt/cgprofile /run/cgprofile /etc/cgprofile
    systemctl daemon-reload
    expect_exit 0 "fresh install succeeds once the old files are removed" bootstrap install "${install_flags[@]}"
}

seed_old_layout() {
    mkdir -p /etc/tmpfiles.d /etc/sysusers.d /etc/cgprofile /opt/cgprofile/2.1.0 /run/cgprofile/observer
    printf 'd /run/cgprofile 0770 root docker -\n' >/etc/tmpfiles.d/mdt-cgprofile.conf
    printf 'd /run/cgprofile/observer 0751 root cgprofile-observer -\n' >/etc/tmpfiles.d/mdt-cgprofile-observer.conf
    printf 'g cgprofile-observer - - -\n' >/etc/sysusers.d/mdt-cgprofile.conf
    printf 'ctl_socket = "/run/cgprofile/ctl.sock"\n' >/etc/cgprofile/cgprofiled.toml
    # the 2.x layout (cgpd/cgpctl): P-11 refuses it and lists the removal, nothing is migrated
    mkdir -p /etc/systemd/system/cgpd.service.d /usr/local/bin
    printf '[Service]\nExecStart=/bin/true\n' >/etc/systemd/system/cgpd.service
    printf 'interval = 2\n' >/etc/cgprofile/cgpd.toml
    ln -s /opt/cgprofile/current/venv/bin/cgpctl /usr/local/bin/cgpctl
    printf 'installer=mdt host-setup (MDT-055)\n' >/opt/cgprofile/2.1.0/MANIFEST
    printf 'seeded by vm-socket-acceptance.sh\n' >/opt/cgprofile/.vm-acceptance-seed
    chmod 0770 /run/cgprofile
}

mode="${1:-}"
case "$mode" in
    guard-check) need_guest; echo "guard ok: this looks like a fresh disposable run-vm-harness guest" ;;
    clean-guest) need_guest; clean_guest ;;
    old-layout-guest) need_guest; old_layout_guest ;;
    *) echo "usage: $0 guard-check | clean-guest | old-layout-guest" >&2; exit 2 ;;
esac
echo
if [ "$FAILED" -eq 0 ]; then echo "ALL PASS"; else echo "$FAILED FAILED"; exit 1; fi
