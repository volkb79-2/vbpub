#!/usr/bin/env bash
# inner-test.sh — runs INSIDE the privileged systemd e2e container.
# Asserts effective cgroup guarantees, daemon-reload survival (Finding D
# regression), and the slice-manager black-box scenario.
set -uo pipefail

FAILS=0
pass() { echo "  PASS: $*"; }
fail() { echo "  FAIL: $*"; FAILS=$((FAILS+1)); }
section() { echo; echo "=== $* ==="; }

wait_for() { # wait_for <seconds> <cmd...>
    local n="$1"; shift
    for _ in $(seq "$n"); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
    return 1
}

section "0. environment"
wait_for 60 systemctl is-system-running --quiet || true   # degraded is fine
wait_for 60 docker info || { echo "FATAL: inner dockerd never came up"; docker info; exit 2; }
driver=$(docker info --format '{{.CgroupDriver}}/{{.CgroupVersion}}')
echo "  inner docker cgroup driver: $driver"
[[ "$driver" == systemd/2 ]] || { echo "FATAL: expected systemd/2"; exit 2; }
docker pull -q busybox >/dev/null || { echo "FATAL: cannot pull busybox"; exit 2; }

section "1. slice units with real floors -> effective values"
cat > /etc/systemd/system/wings.slice <<'EOF'
[Unit]
Description=e2e wings tier slice
[Slice]
MemoryMin=128M
MemoryHigh=512M
EOF
cat > /etc/systemd/system/wings-e2etest.slice <<'EOF'
[Unit]
Description=e2e per-server slice
[Slice]
MemoryMin=64M
MemoryHigh=128M
CPUWeight=800
EOF
systemctl daemon-reload
systemctl start wings.slice wings-e2etest.slice

docker run -d --name e2e1 --cgroup-parent=wings-e2etest.slice busybox sleep 600 >/dev/null
cgpath=$(cat /proc/"$(docker inspect -f '{{.State.Pid}}' e2e1)"/cgroup)
echo "  container cgroup: $cgpath"
[[ "$cgpath" == *"/wings.slice/wings-e2etest.slice/docker-"* ]] \
    && pass "scope nested under wings.slice/wings-e2etest.slice (dash auto-nesting)" \
    || fail "unexpected cgroup path: $cgpath"

cgdir=/sys/fs/cgroup/wings.slice/wings-e2etest.slice
[[ "$(cat $cgdir/memory.min)" == "67108864" ]] \
    && pass "effective child memory.min = 64M" || fail "child memory.min = $(cat $cgdir/memory.min 2>/dev/null)"
[[ "$(cat $cgdir/memory.high)" == "134217728" ]] \
    && pass "effective child memory.high = 128M" || fail "child memory.high = $(cat $cgdir/memory.high 2>/dev/null)"
[[ "$(cat /sys/fs/cgroup/wings.slice/memory.min)" == "134217728" ]] \
    && pass "effective parent memory.min = 128M" || fail "parent memory.min wrong"
[[ "$(cat $cgdir/cpu.weight)" == "800" ]] \
    && pass "effective cpu.weight = 800" || fail "cpu.weight = $(cat $cgdir/cpu.weight 2>/dev/null)"

section "2. daemon-reload survival (Finding D regression)"
# Raw-written scope values must be wiped; systemd-owned slice values must survive.
scope="docker-$(docker inspect -f '{{.Id}}' e2e1).scope"
scopedir="$cgdir/$scope"
echo 33554432 > "$scopedir/memory.min" 2>/dev/null \
    && pass "raw-wrote 32M to scope memory.min (simulating the old watcher)" \
    || fail "could not raw-write scope memory.min"
systemctl daemon-reload
sleep 2
[[ "$(cat "$scopedir/memory.min" 2>/dev/null)" == "0" ]] \
    && pass "raw scope value WIPED by daemon-reload (Finding D reproduced)" \
    || echo "  NOTE: raw scope value survived on this systemd version ($(cat "$scopedir/memory.min" 2>/dev/null)) — Finding D not reproducible here"
[[ "$(cat $cgdir/memory.min)" == "67108864" ]] \
    && pass "slice-held memory.min SURVIVED daemon-reload (the fix)" \
    || fail "slice memory.min lost on daemon-reload: $(cat $cgdir/memory.min 2>/dev/null)"
docker rm -f e2e1 >/dev/null

section "3. wings-slice-manager black box"
if [[ ! -x /usr/local/bin/wings-slice-manager ]]; then
    echo "  SKIP: slice-manager binary not in image"
else
    cat > /etc/wings-slice-manager.yaml <<'EOF'
parent_slice: wings.slice
slice_prefix: wings-
memory_min_budget: 96M
budget_policy: clamp
reconcile_interval: 3s
gc_grace: 4s
log_level: debug
EOF
    /usr/local/bin/wings-slice-manager -config /etc/wings-slice-manager.yaml \
        > /var/log/wsm.log 2>&1 &
    WSM_PID=$!
    sleep 2
    kill -0 "$WSM_PID" 2>/dev/null || { fail "slice-manager did not stay up"; sed -n '1,20p' /var/log/wsm.log; }

    docker run -d --name e2e2 --cgroup-parent=wings-mgd01.slice \
        -e WINGS_CGROUP_PARENT=wings-mgd01.slice \
        -e WINGS_CG_MEMORY_MIN=64M -e WINGS_CG_MEMORY_HIGH=96M \
        busybox sleep 600 >/dev/null
    wait_for 15 bash -c '[[ "$(systemctl show wings-mgd01.slice -p MemoryMin --value)" == "67108864" ]]' \
        && pass "manager applied MemoryMin=64M to wings-mgd01.slice" \
        || { fail "manager did not apply MemoryMin (got: $(systemctl show wings-mgd01.slice -p MemoryMin --value))"; tail -20 /var/log/wsm.log; }
    [[ "$(cat /sys/fs/cgroup/wings.slice/wings-mgd01.slice/memory.high 2>/dev/null)" == "100663296" ]] \
        && pass "effective memory.high = 96M via manager" \
        || fail "manager memory.high missing/wrong"

    # Budget: second container asks for 64M but only 96M-64M=32M budget remains -> clamp.
    docker run -d --name e2e3 --cgroup-parent=wings-mgd02.slice \
        -e WINGS_CGROUP_PARENT=wings-mgd02.slice -e WINGS_CG_MEMORY_MIN=64M \
        busybox sleep 600 >/dev/null
    wait_for 15 bash -c '[[ "$(systemctl show wings-mgd02.slice -p MemoryMin --value)" == "33554432" ]]' \
        && pass "budget clamp: second slice floor clamped to remaining 32M" \
        || { fail "budget clamp mismatch (got: $(systemctl show wings-mgd02.slice -p MemoryMin --value))"; tail -20 /var/log/wsm.log; }

    # GC: remove containers, slices must be stopped after grace. A stopped
    # transient unit can linger "loaded (inactive)" in systemd for a while, so
    # assert on what matters operationally: the slice cgroup is gone.
    docker rm -f e2e2 e2e3 >/dev/null
    wait_for 25 bash -c '[[ ! -e /sys/fs/cgroup/wings.slice/wings-mgd01.slice && ! -e /sys/fs/cgroup/wings.slice/wings-mgd02.slice ]]' \
        && pass "orphaned slices GC'd after grace (cgroups removed)" \
        || { fail "slice cgroups still present after GC window ($(systemctl is-active wings-mgd01.slice; systemctl is-active wings-mgd02.slice))"; tail -20 /var/log/wsm.log; }

    kill "$WSM_PID" 2>/dev/null
fi

section "4. wings internal/cgroups integration (patch 0004: D-Bus lifecycle, budget, GC)"
if [[ ! -x /usr/local/bin/cgroups.test ]]; then
    echo "  SKIP: cgroups.test binary not in image (build/wings-pterodactyl absent at harness build)"
else
    if /usr/local/bin/cgroups.test -test.v \
        > /var/log/cgroups-test.log 2>&1; then
        pass "wings internal/cgroups integration test"
        grep -E '^(=== RUN|--- (PASS|FAIL))' /var/log/cgroups-test.log | sed 's/^/  /'
    else
        fail "wings internal/cgroups integration test"
        tail -40 /var/log/cgroups-test.log
    fi
fi

echo
if [[ "$FAILS" -eq 0 ]]; then echo "E2E: ALL PASS"; else echo "E2E: $FAILS FAILURE(S)"; fi
exit "$FAILS"
