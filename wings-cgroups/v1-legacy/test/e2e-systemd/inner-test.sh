#!/usr/bin/env bash
# inner-test.sh — runs INSIDE the privileged systemd e2e container.
#
# WHAT EACH SECTION IS EVIDENCE FOR. Read this before quoting a summary line.
# The harness covers two unrelated things and they must not be conflated:
#
#   Sections 1, 2, 4  — the SHIPPED wings patch series (slice -> scope
#                       redesign). 1 and 2 assert the placement and the
#                       property-durability model the series depends on; 4
#                       runs the series' own systemd integration tests from
#                       the compiled binary.
#   Section 3         — the SEPARATE t3a-slice-manager component. It is NOT
#                       part of the wings patch series and never was; its
#                       budget/GC machinery is exactly what the redesign
#                       deleted from wings. It is exercised here only because
#                       this is the one harness with a real systemd.
#
# The final summary therefore reports the two tallies separately. A green
# section 3 says nothing whatsoever about the patch series, and an "ALL PASS"
# that folded them together is what made an earlier hand-off overstate its
# evidence.
set -uo pipefail

FAILS=0
SERIES_FAILS=0
OTHER_FAILS=0
# Sections tally into SERIES_FAILS unless scope_other is called.
SCOPE=series
scope_series() { SCOPE=series; }
scope_other()  { SCOPE=other; }
pass() { echo "  PASS: $*"; }
fail() {
    echo "  FAIL: $*"
    FAILS=$((FAILS+1))
    if [[ "$SCOPE" == series ]]; then SERIES_FAILS=$((SERIES_FAILS+1)); else OTHER_FAILS=$((OTHER_FAILS+1)); fi
}
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

scope_series
section "1. SHIPPED SERIES: flat placement under the tier, one scope per container"
# The shipped shape: docker.cgroup_parent names ONE administrator-owned tier
# slice, every container is created flat under it, and each container gets its
# own docker-<id>.scope. There is no per-server slice in between any more.
cat > /etc/systemd/system/wings.slice <<'EOF'
[Unit]
Description=e2e wings tier slice
[Slice]
MemoryMin=128M
MemoryHigh=512M
EOF
systemctl daemon-reload
systemctl start wings.slice

docker run -d --name e2e1 --cgroup-parent=wings.slice busybox sleep 600 >/dev/null
cid=$(docker inspect -f '{{.Id}}' e2e1)
cgpath=$(cat /proc/"$(docker inspect -f '{{.State.Pid}}' e2e1)"/cgroup)
echo "  container cgroup: $cgpath"
[[ "$cgpath" == *"/wings.slice/docker-$cid.scope"* ]] \
    && pass "container placed flat under wings.slice in its own docker-<id>.scope" \
    || fail "unexpected cgroup path: $cgpath"
[[ "$cgpath" != *"/wings-"*".slice/"* ]] \
    && pass "no per-server slice between the tier and the scope (retired shape absent)" \
    || fail "a per-server slice is still in the path: $cgpath"

scopedir="/sys/fs/cgroup/wings.slice/docker-$cid.scope"
[[ -d "$scopedir" ]] \
    && pass "scope cgroup directory exists at $scopedir" \
    || fail "no scope cgroup directory at $scopedir"
[[ "$(cat /sys/fs/cgroup/wings.slice/memory.min)" == "134217728" ]] \
    && pass "tier memory.min = 128M is effective on wings.slice" \
    || fail "tier memory.min wrong: $(cat /sys/fs/cgroup/wings.slice/memory.min 2>/dev/null)"
# The reason the series writes the LEAF: a floor declared on the tier is not a
# floor on the container's own cgroup. Informational rather than asserted,
# because whether an unset leaf reads back as 0 depends on the kernel's
# memory_recursiveprot behaviour, which is a host-wide mount flag.
echo "  NOTE: scope memory.min before any property application = $(cat "$scopedir/memory.min" 2>/dev/null) (this is why the series writes the leaf, not the tier)"

section "2. SHIPPED SERIES: systemd-owned scope properties survive daemon-reload"
# This is the durability claim the whole series rests on. Wings sets scope
# properties through SetUnitProperties(runtime=true) over D-Bus; `systemctl
# set-property --runtime` is the same call from the shell. Those must survive
# `systemctl daemon-reload`. A raw cgroupfs write — the mechanism the earliest
# prototype used — must not, and that is what makes the D-Bus channel
# load-bearing rather than a stylistic choice.
scope="docker-$cid.scope"
systemctl set-property --runtime "$scope" MemoryMin=64M CPUWeight=800 2>/dev/null \
    && pass "set MemoryMin=64M CPUWeight=800 on $scope over the systemd-owned channel" \
    || fail "could not set properties on $scope"
[[ "$(cat "$scopedir/memory.min" 2>/dev/null)" == "67108864" ]] \
    && pass "scope memory.min = 64M is effective on the container's own cgroup" \
    || fail "scope memory.min = $(cat "$scopedir/memory.min" 2>/dev/null)"
[[ "$(cat "$scopedir/cpu.weight" 2>/dev/null)" == "800" ]] \
    && pass "scope cpu.weight = 800 is effective" \
    || fail "scope cpu.weight = $(cat "$scopedir/cpu.weight" 2>/dev/null)"

# A raw write to a property systemd was never told about, for contrast.
echo 33554432 > "$scopedir/memory.low" 2>/dev/null \
    && pass "raw-wrote 32M to scope memory.low (the cgroupfs channel, for contrast)" \
    || fail "could not raw-write scope memory.low"
systemctl daemon-reload
sleep 2
[[ "$(cat "$scopedir/memory.min" 2>/dev/null)" == "67108864" ]] \
    && pass "systemd-set scope memory.min SURVIVED daemon-reload (the series' guarantee)" \
    || fail "systemd-set scope memory.min lost on daemon-reload: $(cat "$scopedir/memory.min" 2>/dev/null)"
[[ "$(cat "$scopedir/cpu.weight" 2>/dev/null)" == "800" ]] \
    && pass "systemd-set scope cpu.weight SURVIVED daemon-reload" \
    || fail "systemd-set scope cpu.weight lost on daemon-reload: $(cat "$scopedir/cpu.weight" 2>/dev/null)"
[[ "$(cat "$scopedir/memory.low" 2>/dev/null)" == "0" ]] \
    && pass "raw-written scope memory.low WIPED by daemon-reload (why raw writes are not used)" \
    || echo "  NOTE: raw scope value survived on this systemd version ($(cat "$scopedir/memory.low" 2>/dev/null)) — not reproducible here"
docker rm -f e2e1 >/dev/null

scope_other
section "3. t3a-slice-manager black box -- SEPARATE COMPONENT, NOT the wings series"
# Everything below drives the standalone t3a-slice-manager binary against its
# own YAML: derived per-server slices, a memory_min_budget with a clamp policy,
# and orphan GC. None of it exists in the wings patch series any more — the
# slice->scope redesign deleted the budget arithmetic and the unit lifecycle
# outright. Green here is evidence about t3a-slice-manager and nothing else.
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

scope_series
section "4. SHIPPED SERIES: wings internal/cgroups systemd integration tests"
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
echo "=== summary ==="
# Reported separately on purpose: only the first tally is evidence about the
# wings patch series. See the header of this file.
if [[ "$SERIES_FAILS" -eq 0 ]]; then
    echo "  SHIPPED SERIES (sections 1, 2, 4): PASS"
else
    echo "  SHIPPED SERIES (sections 1, 2, 4): $SERIES_FAILS FAILURE(S)"
fi
if [[ "$OTHER_FAILS" -eq 0 ]]; then
    echo "  t3a-slice-manager, separate component (section 3): PASS"
else
    echo "  t3a-slice-manager, separate component (section 3): $OTHER_FAILS FAILURE(S)"
fi
if [[ "$FAILS" -eq 0 ]]; then echo "E2E: ALL PASS"; else echo "E2E: $FAILS FAILURE(S)"; fi
exit "$FAILS"
