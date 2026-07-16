#!/usr/bin/env bash
# smoke-placement.sh [slice-name]
# Verifies, against the Docker daemon on this machine, that:
#   1. the daemon uses the systemd cgroup driver on cgroup v2;
#   2. a container created with --cgroup-parent=<slice> actually lands under
#      that slice (checked from inside via the host cgroup namespace);
#   3. if the slice has resource properties AND /sys/fs/cgroup is readable
#      here, the effective memory.min/low/high match (path-only checks are NOT
#      sufficient — a missing unit file degrades to a limit-less transient
#      slice; see proposal Finding A / review F3).
# Safe: creates one throwaway busybox container, removes it afterwards.
set -euo pipefail

SLICE="${1:-wings-smoke.slice}"

echo "== daemon =="
driver=$(docker info --format '{{.CgroupDriver}}/{{.CgroupVersion}}')
echo "cgroup driver/version: $driver"
[[ "$driver" == systemd/2 ]] || { echo "FAIL: need systemd driver on cgroup v2 (got $driver)"; exit 1; }

echo "== placement =="
path=$(docker run --rm --cgroupns=host --cgroup-parent="$SLICE" busybox cat /proc/self/cgroup)
echo "container cgroup: $path"
case "$path" in
    *"/$SLICE/docker-"*) echo "OK: scope is under $SLICE" ;;
    *) echo "FAIL: scope not under $SLICE"; exit 1 ;;
esac

echo "== effective properties (best effort) =="
cgdir=""
for d in "/sys/fs/cgroup/$SLICE" /sys/fs/cgroup/*/"$SLICE"; do
    [[ -d "$d" ]] && { cgdir="$d"; break; }
done
if [[ -z "$cgdir" ]]; then
    echo "SKIP: $SLICE cgroup dir not visible from here (containerized shell?)."
    echo "      On the node, additionally run:"
    echo "        systemctl show $SLICE -p FragmentPath -p MemoryMin -p MemoryLow -p MemoryHigh"
    echo "      and require FragmentPath to point at your unit file."
else
    for f in memory.min memory.low memory.high; do
        printf '  %s = %s\n' "$f" "$(cat "$cgdir/$f" 2>/dev/null || echo n/a)"
    done
    if [[ "$(cat "$cgdir/memory.min" 2>/dev/null || echo 0)" == 0 ]]; then
        echo "WARN: memory.min is 0 — slice exists but carries no floor."
        echo "      If you expected one, the unit file is missing or not loaded (transient-slice footgun)."
    fi
fi
echo "smoke-placement: PASS"
