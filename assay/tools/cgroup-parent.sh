#!/usr/bin/env bash
# Resolve and verify the host cgroup tier before assay's nyxloom gate launches
# its container. A missing systemd slice name fails open into an unlimited
# transient slice, so accepting the spelling without inspecting the host
# cgroup tree is not verification.

set -euo pipefail

PROBE_IMAGE="${ASSAY_CGROUP_PROBE_IMAGE:-tester-unified:local}"

die() { printf 'cgroup-parent: %s\n' "$*" >&2; exit 1; }

slice="${CGROUP_PARENT_DEV_BACKGROUND:-}"
if [ -z "$slice" ]; then
  die 'CGROUP_PARENT_DEV_BACKGROUND is unset. Refusing to launch a gate
container without an explicitly declared background cgroup tier.'
fi

case "$slice" in
  *.slice) ;;
  *) die "\"$slice\" does not name a systemd slice unit" ;;
esac

stem="${slice%.slice}"
case "$stem" in
  -*|*-) die "\"$slice\" has an empty hierarchy component" ;;
esac
rel=""
acc=""
IFS='-' read -r -a parts <<<"$stem"
for part in "${parts[@]}"; do
  [ -n "$part" ] || die "\"$slice\" has an empty hierarchy component"
  if [ -z "$acc" ]; then acc="$part"; else acc="$acc-$part"; fi
  rel="$rel/$acc.slice"
done

verdict="$(
  docker run --rm -i --cgroupns=host --network=none \
    -e "CG_REL=$rel" "$PROBE_IMAGE" sh -s <<'PROBE' 2>/dev/null || true
d="/sys/fs/cgroup${CG_REL}"
if [ ! -d "$d" ]; then echo "MISSING $d"; exit 0; fi
read_or() { if [ -r "$1" ]; then cat "$1"; else echo "$2"; fi; }
configured=0
if [ "$(read_or "$d/memory.max" max)"           != "max" ];         then configured=1; fi
if [ "$(read_or "$d/memory.high" max)"          != "max" ];         then configured=1; fi
if [ "$(read_or "$d/memory.swap.max" max)"      != "max" ];         then configured=1; fi
if [ "$(read_or "$d/cpu.weight" 100)"           != "100" ];         then configured=1; fi
if [ "$(read_or "$d/io.weight" "default 100")"  != "default 100" ]; then configured=1; fi
if [ "$configured" = "1" ]; then echo "OK"; else echo "UNCONFIGURED $d"; fi
PROBE
)"

case "$verdict" in
  OK) printf '%s\n' "$slice" ;;
  MISSING\ *) die "slice \"$slice\" has no host cgroup at ${verdict#MISSING }; refusing a fail-open transient slice" ;;
  UNCONFIGURED\ *) die "slice \"$slice\" exists at ${verdict#UNCONFIGURED } but has only kernel-default resource knobs" ;;
  *) die "could not verify host cgroup slice \"$slice\"" ;;
esac
