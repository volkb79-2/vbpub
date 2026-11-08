#!/usr/bin/env bash
# Guest-side endpoint acceptance for topos's socket-activated host service.
#
# Run only inside a fresh disposable guest made by the Debian VM harness:
#
#   run-vm-harness.sh copy RUN topos/tools /root/tools
#   run-vm-harness.sh copy RUN <wheel-directory> /root/wheels
#   run-vm-harness.sh ssh RUN -- sudo env TOPOS_DISPOSABLE_GUEST=yes \
#       TOPOS_PIN=0.8.0@sha256:<hex> CLI_EXTENDED_PIN=0.5.3@sha256:<hex> \
#       DAEMON_LOCK_PIN=0.8.0@sha256:<hex> \
#       bash /root/tools/vm-socket-acceptance.sh clean-guest
#
# The script installs and removes the service, so it refuses unless the guest
# is explicitly opted in, identifies as the harness guest, and has no topos
# installation. It is not a cockpit or live-host acceptance command.
set -u

WHEELS="${WHEELS:-/root/wheels}"
FAILED=0
CASE=""

pass() { printf 'PASS  [%s] %s\n' "$CASE" "$1"; }
fail() { printf 'FAIL  [%s] %s\n' "$CASE" "$1"; FAILED=$((FAILED + 1)); }

check() {
    local description="$1"
    shift
    if "$@" >/tmp/vm-accept.out 2>&1; then
        pass "$description"
    else
        fail "$description"
        sed 's/^/        /' /tmp/vm-accept.out
    fi
}

expect_exit() {
    local wanted="$1" description="$2" got
    shift 2
    "$@" >/tmp/vm-accept.out 2>&1
    got=$?
    if [ "$got" -eq "$wanted" ]; then
        pass "$description (exit $got)"
    else
        fail "$description (exit $got, wanted $wanted)"
        sed 's/^/        /' /tmp/vm-accept.out
    fi
}

refuse() { echo "refusing: $1" >&2; exit 2; }

need_guest() {
    [ "$(id -u)" -eq 0 ] || refuse "run as root inside the guest"
    [ "${TOPOS_DISPOSABLE_GUEST:-}" = yes ] || refuse "set TOPOS_DISPOSABLE_GUEST=yes for a disposable VM-harness guest"
    systemd-detect-virt --vm >/dev/null 2>&1 || refuse "not a VM"
    [ "$(hostname)" = vbpub-test ] && id tester >/dev/null 2>&1 \
        || refuse "expected the run-vm-harness guest (hostname vbpub-test and user tester)"
    local path
    for path in /opt/topos /etc/topos /run/topos /etc/systemd/system/topos.service; do
        [ ! -e "$path" ] && [ ! -L "$path" ] || refuse "$path exists; this guest is not fresh"
    done
    ! systemctl is-active --quiet topos.service || refuse "topos.service is already active"
    : "${TOPOS_PIN:?set TOPOS_PIN}" "${CLI_EXTENDED_PIN:?set CLI_EXTENDED_PIN}" \
      "${DAEMON_LOCK_PIN:?set DAEMON_LOCK_PIN}"
    [ -d "$WHEELS" ] || refuse "wheel directory is missing: $WHEELS"
}

find_wheel() {
    local pattern="$1" found
    found="$(find "$WHEELS" -maxdepth 1 -type f -name "$pattern" -print -quit)"
    [ -n "$found" ] || refuse "no $pattern in $WHEELS"
    printf '%s\n' "$found"
}

bootstrap() {
    local topos_wheel clix_wheel augeaswrap_wheel
    topos_wheel="$(find_wheel 'topos-*-py3-none-any.whl')"
    clix_wheel="$(find_wheel 'cli_extended-*-py3-none-any.whl')"
    augeaswrap_wheel="$(find_wheel 'augeaswrap-*-py3-none-any.whl')"
    env -u TOPOS_OBSERVE_SOCKET \
        PYTHONPATH="$topos_wheel:$clix_wheel:$augeaswrap_wheel" \
        python3 -m topos.service.cli "$@"
}

endpoint_cli() {
    local clix_wheel augeaswrap_wheel
    clix_wheel="$(find_wheel 'cli_extended-*-py3-none-any.whl')"
    augeaswrap_wheel="$(find_wheel 'augeaswrap-*-py3-none-any.whl')"
    PYTHONPATH="$clix_wheel:$augeaswrap_wheel" python3 -m augeaswrap "$@"
}

clean_guest() {
    local topod=/opt/topos/current/venv/bin/toposd
    local topos=/opt/topos/current/venv/bin/topos
    local collision_owner=e2-vm-collision
    local missing_socket=/tmp/e2-no-observe.sock

    CASE="endpoint collision preflight"
    expect_exit 0 "publish a colliding endpoint for another owner" endpoint_cli endpoints publish \
        --owner "$collision_owner" --writer 'vm-socket-acceptance 0.8.0' \
        --entry TOPOS_OBSERVE_SOCKET=/run/e2-vm-collision/observe.sock
    expect_exit 2 "topos dry-run refuses the endpoint key collision" \
        bootstrap install --wheel "$TOPOS_PIN" --cli-extended-wheel "$CLI_EXTENDED_PIN" \
        --daemon-lock "$DAEMON_LOCK_PIN" --wheel-source "$WHEELS" \
        --observe-group topos-observe --enable --yes --dry-run
    grep -q "$collision_owner" /tmp/vm-accept.out \
        && pass "collision refusal names its owner" \
        || fail "collision refusal omits the other owner"
    expect_exit 0 "withdraw the colliding endpoint" endpoint_cli endpoints withdraw --owner "$collision_owner"

    CASE="install and endpoint clients"
    expect_exit 0 "install pinned topos service" bootstrap install \
        --wheel "$TOPOS_PIN" --cli-extended-wheel "$CLI_EXTENDED_PIN" \
        --daemon-lock "$DAEMON_LOCK_PIN" --wheel-source "$WHEELS" \
        --observe-group topos-observe --enable --yes
    [ -r /etc/host-endpoints.d/topos.env ] \
        && grep -q '^TOPOS_OBSERVE_SOCKET=' /etc/host-endpoints.d/topos.env \
        && grep -q '^TOPOS_OBSERVE_SOCKET_DIR=' /etc/host-endpoints.d/topos.env \
        && grep -q '^TOPOS_OBSERVE_GID=' /etc/host-endpoints.d/topos.env \
        && pass "topos fragment publishes socket, directory, and group ID" \
        || fail "topos endpoint fragment is missing expected entries"
    grep -q '^TOPOS_OBSERVE_SOCKET=' /etc/host-endpoints.env \
        && grep -q '^# BEGIN host-endpoints' /etc/environment \
        && grep -Fq '. /etc/host-endpoints.env' /etc/profile.d/host-endpoints.sh \
        && pass "aggregate and login profile export the endpoint" \
        || fail "aggregate or login profile is missing the endpoint"

    expect_exit 2 "client refuses when the endpoint variable is unset" \
        env -i PATH="$PATH" "$topos" daemon current
    grep -q 'TOPOS_OBSERVE_SOCKET' /tmp/vm-accept.out \
        && pass "unset refusal names TOPOS_OBSERVE_SOCKET" \
        || fail "unset refusal omits TOPOS_OBSERVE_SOCKET"
    expect_exit 2 "client uses an explicitly wrong environment socket without fallback" \
        env -i PATH="$PATH" TOPOS_OBSERVE_SOCKET="$missing_socket" "$topos" daemon current
    grep -q "$missing_socket" /tmp/vm-accept.out \
        && pass "connection error names the supplied socket" \
        || fail "connection error does not name the supplied socket"
    expect_exit 0 "explicit --socket overrides a wrong environment value" \
        env -i PATH="$PATH" TOPOS_OBSERVE_SOCKET="$missing_socket" \
        "$topos" daemon status --socket "$(sed -n 's/^TOPOS_OBSERVE_SOCKET=//p' /etc/host-endpoints.d/topos.env)" --json
    expect_exit 0 "doctor verifies endpoint-record" "$topod" doctor --check endpoint-record --json

    CASE="withdraw before removal"
    expect_exit 0 "uninstall topos" env -u TOPOS_OBSERVE_SOCKET "$topod" uninstall --yes
    [ ! -e /etc/host-endpoints.d/topos.env ] \
        && ! grep -q '^TOPOS_OBSERVE_SOCKET=' /etc/host-endpoints.env \
        && pass "uninstall withdrew the topos endpoint fragment and aggregate keys" \
        || fail "topos endpoint data remains after uninstall"

    if [ "$FAILED" -gt 0 ]; then
        printf 'FAIL  %s endpoint acceptance check(s) failed\n' "$FAILED"
        return 1
    fi
    printf 'PASS  all topos VM endpoint acceptance checks\n'
}

mode="${1:-}"
need_guest
case "$mode" in
    clean-guest) clean_guest ;;
    *) refuse "usage: $0 clean-guest" ;;
esac
