#!/usr/bin/env bash
# inner-test.sh — runs INSIDE the privileged systemd e2e container.
#
# WHAT EACH SECTION IS EVIDENCE FOR. Read this before quoting a summary line.
# The harness covers two unrelated things and they must not be conflated:
#
#   Sections 1, 2, 4, 5 — the SHIPPED wings patch series (slice -> scope
#                       redesign). 1 and 2 assert the placement and the
#                       property-durability model the series depends on; 4
#                       runs the series' own systemd integration tests from
#                       the compiled binary; 5 asserts that a panel-side
#                       re-assertion cannot evict a still-loading server.
#   Section 3         — the SEPARATE t3a-slice-manager component. It is NOT
#                       part of the wings patch series and never was; its
#                       budget/GC machinery is exactly what the redesign
#                       deleted from wings. It is exercised here only because
#                       this is the one harness with a real systemd.
#
# The final summary reports the two tallies separately, and names any section
# that did not run. A green section 3 says nothing whatsoever about the patch
# series, and an "ALL PASS" folded over both is what made an earlier hand-off
# overstate its evidence — so no line printed here says "ALL PASS".
set -uo pipefail

FAILS=0
SERIES_FAILS=0
OTHER_FAILS=0
SERIES_SKIPS=""
OTHER_SKIPS=""
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
# skip() records what did NOT run, so the summary can say so instead of
# reporting a PASS that covers less than it looks like it does.
skip() {
    echo "  SKIP: $*"
    if [[ "$SCOPE" == series ]]; then SERIES_SKIPS="$SERIES_SKIPS; $*"; else OTHER_SKIPS="$OTHER_SKIPS; $*"; fi
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

systemctl daemon-reload
sleep 2
[[ "$(cat "$scopedir/memory.min" 2>/dev/null)" == "67108864" ]] \
    && pass "systemd-set scope memory.min SURVIVED daemon-reload (the series' guarantee)" \
    || fail "systemd-set scope memory.min lost on daemon-reload: $(cat "$scopedir/memory.min" 2>/dev/null)"
[[ "$(cat "$scopedir/cpu.weight" 2>/dev/null)" == "800" ]] \
    && pass "systemd-set scope cpu.weight SURVIVED daemon-reload" \
    || fail "systemd-set scope cpu.weight lost on daemon-reload: $(cat "$scopedir/cpu.weight" 2>/dev/null)"

# The contrast, and the other half of why the systemd-owned channel is not a
# stylistic preference: a raw cgroupfs write to an attribute systemd MANAGES is
# not durable. systemd holds its own view of the unit's resource properties and
# re-derives every managed attribute from it the next time any property is set
# — which, for a Wings-managed server, is every property application.
#
# What this deliberately does NOT assert: that `daemon-reload` alone wipes the
# raw write. It does not, on systemd 257 — a docker-<id>.scope is transient, has
# no unit file, and a reload re-reads unit files. An earlier version of this
# section asserted exactly that and degraded to a NOTE on this host; the real,
# reproducible statement is the one below.
echo 33554432 > "$scopedir/memory.min" 2>/dev/null \
    && pass "raw-wrote 32M over the systemd-managed scope memory.min (the cgroupfs channel, for contrast)" \
    || fail "could not raw-write scope memory.min"
[[ "$(cat "$scopedir/memory.min" 2>/dev/null)" == "33554432" ]] \
    && pass "the raw write did land (so what follows is a real overwrite, not a failed write)" \
    || fail "raw write did not take: $(cat "$scopedir/memory.min" 2>/dev/null)"
# Any property set on the unit; systemd re-applies its WHOLE view, not just this one.
systemctl set-property --runtime "$scope" CPUWeight=777 2>/dev/null
sleep 1
[[ "$(cat "$scopedir/memory.min" 2>/dev/null)" == "67108864" ]] \
    && pass "raw-written scope memory.min DISCARDED the next time systemd touched the unit (why raw writes are not used)" \
    || fail "systemd did not re-derive memory.min from its own view: $(cat "$scopedir/memory.min" 2>/dev/null)"
docker rm -f e2e1 >/dev/null

scope_other
section "3. t3a-slice-manager black box -- SEPARATE COMPONENT, NOT the wings series"
# Everything below drives the standalone t3a-slice-manager binary against its
# own YAML: derived per-server slices, a memory_min_budget with a clamp policy,
# and orphan GC. None of it exists in the wings patch series any more — the
# slice->scope redesign deleted the budget arithmetic and the unit lifecycle
# outright. Green here is evidence about t3a-slice-manager and nothing else.
if [[ ! -x /usr/local/bin/wings-slice-manager ]]; then
    skip "section 3: slice-manager binary not in image"
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
    skip "section 4: cgroups.test binary not in image (build/wings-pterodactyl absent at harness build)"
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

scope_series
section "5. SHIPPED SERIES: a panel-side re-assertion must not evict a loading server"
# The kernel consequence of getting the re-assertion's band wrong, measured
# rather than argued. This is the failure a fix-verification found on
# 2026-09-09 (V1): the re-assert inferred its band from Docker's process state,
# so on the configuration SETUP.md recommends -- WINGS_CG_STEADY_MATCH set,
# because a world-streaming game's "done" line fires long before loading
# finishes -- a routine settings save applied the STEADY band, one-shot, to a
# server at its load-time peak.
#
# Both halves run here. The BUGGY sequence must reclaim the server through its
# own memory.min; the FIXED sequence, which re-asserts the startup band and
# never writes memory.high, must not touch the working set while repairing
# everything docker's write clobbered. Asserting the buggy half too is
# deliberate: if it ever stops evicting, this section has stopped measuring
# anything and its green means nothing.
#
# WHAT THIS SECTION IS, AND IS NOT. It asserts the KERNEL CONSEQUENCE: that the
# reclaim-through-your-own-floor bug is real on this kernel, and that the shape
# of the fix prevents it at the systemd/cgroup level. It is NOT a regression
# guard against the Go code regressing. Every command below is a raw
# docker/systemctl call; no Wings code runs in this section at all, so it stays
# green with the V1 code fix reverted (demonstrated by mutation on 2026-09-09 --
# see the correcting note in CONTINUATION-2026-09-08-scope-redesign.md, which
# supersedes commit 1b4a720f's "permanent regression guard" claim). The actual
# regression guard for the band choice is the unit suite --
# TestReassertUsesThePhaseNotTheEnvironmentState and its neighbours in
# server/slice_phase_test.go, which kill that mutant.
docker run -d --name e2e5 --cgroup-parent=wings.slice \
    --memory 512m --memory-reservation 512m --cpu-shares 2 busybox \
    sh -c 'dd if=/dev/zero of=/tmp/world bs=1M count=300 2>/dev/null; while :; do cat /tmp/world > /dev/null; sleep 2; done' >/dev/null
cid5=$(docker inspect -f '{{.Id}}' e2e5)
scope5="docker-$cid5.scope"
d5="/sys/fs/cgroup/wings.slice/$scope5"
sleep 10

# The bands. Startup: a generous ceiling while the world loads. Steady: the
# resting one, deliberately below the load peak, which is the only shape in
# which the startup band does anything at all (SETUP.md "ENGAGEMENT").
startup_band() { systemctl set-property --runtime "$scope5" MemoryMin=200M MemoryLow=300M MemoryHigh=400M MemoryMax=450M CPUWeight=800 IOWeight=4950; }
# What a panel settings save issues: docker's own container.Resources.
panel_save()   { docker update --memory 512m --memory-reservation 512m --cpu-shares 1024 e2e5 >/dev/null; }
mem_cur()      { cat "$d5/memory.current" 2>/dev/null; }

startup_band
sleep 6
peak=$(mem_cur)
[[ -n "$peak" && "$peak" -gt 209715200 ]] \
    && pass "server is at a load-time working set of $peak bytes, above its own 200M memory.min" \
    || fail "no load-time working set to measure ($peak bytes); the rest of this section proves nothing"

panel_save
sleep 1
[[ "$(cat "$d5/cpu.weight")" == "39" && "$(cat "$d5/memory.low")" == "536870912" ]] \
    && pass "the panel save clobbered the scope: cpu.weight 800->39, memory.low 300M->512M" \
    || fail "the panel save did not clobber as expected (cpu.weight=$(cat "$d5/cpu.weight") memory.low=$(cat "$d5/memory.low"))"

# BUGGY: the steady band, one-shot, while the server is still loading.
before=$(mem_cur)
systemctl set-property --runtime "$scope5" MemoryMin=200M MemoryLow=300M MemoryHigh=100M MemoryMax=450M CPUWeight=800 IOWeight=4950
sleep 3
after=$(mem_cur)
[[ -n "$after" && "$after" -lt 209715200 ]] \
    && pass "steady band mid-load reclaimed $before -> $after, THROUGH its own 200M memory.min (the eviction V1 described)" \
    || fail "steady band mid-load did not evict ($before -> $after); this section is no longer measuring the failure"

# FIXED: the startup band -- the phase the server layer actually holds -- and
# no memory.high at all, because docker cannot have touched it.
startup_band
sleep 8
panel_save
sleep 1
before=$(mem_cur)
systemctl set-property --runtime "$scope5" MemoryMin=200M MemoryLow=300M MemoryMax=450M CPUWeight=800 IOWeight=4950
sleep 3
after=$(mem_cur)
[[ -n "$after" && "$after" -gt 209715200 ]] \
    && pass "the re-assertion left the working set alone: $before -> $after, still above the 200M floor" \
    || fail "the re-assertion evicted the server ($before -> $after)"
[[ "$(cat "$d5/memory.high")" == "419430400" ]] \
    && pass "the re-assertion did not move memory.high (still the startup 400M)" \
    || fail "the re-assertion wrote memory.high: $(cat "$d5/memory.high")"
[[ "$(cat "$d5/cpu.weight")" == "800" && "$(cat "$d5/memory.low")" == "314572800" && "$(cat "$d5/memory.max")" == "471859200" ]] \
    && pass "everything the panel save DID reach was repaired: cpu.weight=800 memory.low=300M memory.max=450M" \
    || fail "repair incomplete (cpu.weight=$(cat "$d5/cpu.weight") memory.low=$(cat "$d5/memory.low") memory.max=$(cat "$d5/memory.max"))"
# The IO half of the same repair, and the scale trap Rule 7 warns about.
echo "  NOTE: IOWeight=$(systemctl show "$scope5" -p IOWeight --value) derives io.bfq.weight=$(cat "$d5/io.bfq.weight" 2>/dev/null) — different scales, they never agree numerically"
docker rm -f e2e5 >/dev/null

echo
echo "=== summary ==="
# Reported separately on purpose: only the first tally is evidence about the
# wings patch series. See the header of this file.
#
# verdict() builds one line per tally that states the failures AND anything that
# did not run, so a "PASS" here can never quietly cover a skipped section. The
# final line is a concatenation of exactly those two verdicts: there is
# deliberately no third, folded verdict, because a single quotable "ALL PASS"
# over both tallies is what made an earlier hand-off overstate its evidence.
verdict() { # verdict <fails> <skips>
    local v
    if [[ "$1" -eq 0 ]]; then v="PASS"; else v="$1 FAILURE(S)"; fi
    [[ -n "$2" ]] && v="$v, BUT NOT RUN:${2#;}"
    echo "$v"
}
series_verdict="$(verdict "$SERIES_FAILS" "$SERIES_SKIPS")"
other_verdict="$(verdict "$OTHER_FAILS" "$OTHER_SKIPS")"
echo "  SHIPPED SERIES (sections 1, 2, 4, 5): $series_verdict"
echo "  t3a-slice-manager, separate component (section 3): $other_verdict"
echo "E2E: series -> $series_verdict | t3a-slice-manager -> $other_verdict"
exit "$FAILS"
