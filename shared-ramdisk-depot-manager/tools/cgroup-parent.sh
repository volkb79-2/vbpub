#!/usr/bin/env bash
# cgroup-parent.sh — resolve and VERIFY the host cgroup tier for a container
# srdm's tooling is about to start, then print it on stdout.
#
# Why this exists. This host runs production game servers and edge infra
# beside dev/test/build work, so nothing may land at Docker's unconfined
# default. AGENTS.md names the tier in $CGROUP_PARENT_DEV_BACKGROUND and
# forbids a hardcoded fallback.
#
# And the failure mode is nasty: a typo'd or nonexistent slice name does NOT
# error. systemd silently creates an unlimited transient slice and the
# container starts normally — so the mistake removes EVERY limit while
# looking like success. Verified 2026-07-27; see nyxloom/infra/slices/README.md.
# Anything that accepts a slice name therefore has to prove it is real.
#
# The canonical proof is `systemctl show <slice> --property=LoadState`, but
# the cockpit has no systemd and no host cgroupfs (private cgroup namespace,
# unprivileged). So this script proves it the way it can: it reads the HOST
# cgroup tree through a throwaway --cgroupns=host container and asserts that
#   (a) the slice's cgroup directory exists, and
#   (b) at least one resource knob differs from the kernel default
# A fail-open transient slice satisfies (a) and fails (b) — every knob is
# still at its default, which is exactly what "unlimited" means here.
#
# Resolution order (first non-empty wins):
#   1. the slice named as $1 — how nyxloom.toml declares srdm's tier. An
#      EXPLICIT per-project override is sanctioned by AGENTS.md; what is
#      forbidden is falling back to a hardcoded name when nothing is set.
#   2. $CGROUP_PARENT_DEV_BACKGROUND, the ambient devcontainer tier.
#   3. nothing — refuse.
#
# Usage:  tools/cgroup-parent.sh [slice-name]
# Exits non-zero, printing to stderr, when it cannot prove the tier is real.

set -euo pipefail

PROBE_IMAGE="${SRDM_PROBE_IMAGE:-alpine:latest}"

die() { printf 'cgroup-parent: %s\n' "$*" >&2; exit 1; }

slice="${1:-${CGROUP_PARENT_DEV_BACKGROUND:-}}"
if [ -z "$slice" ]; then
  die 'no slice given and $CGROUP_PARENT_DEV_BACKGROUND is unset.
  This is a configuration error, not something to guess around: falling back
  to Docker'"'"'s default would place this container beside production.
  Fix it by exporting the tier, e.g.
      export CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice
  or by passing the slice explicitly as the first argument.'
fi

case "$slice" in
  *.slice) ;;
  *) die "\"$slice\" does not name a systemd slice unit (expected a .slice suffix)" ;;
esac

# systemd derives nesting from the dash-separated name: dev-background.slice
# lives at /sys/fs/cgroup/dev.slice/dev-background.slice. Rebuild that path
# rather than guessing at a flat layout.
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
# Kernel defaults for an unconfigured (i.e. transient, fail-open) slice.
# Any single knob away from its default means a unit file configured this.
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
  OK)
    printf '%s\n' "$slice"
    ;;
  MISSING\ *)
    die "slice \"$slice\" has no cgroup at ${verdict#MISSING }.
  A container placed under a name systemd does not know does NOT fail — it
  gets an unlimited transient slice next to production. Refusing to launch.
  Install the tier (modern-debian-tools-python-debug/host-setup/install.sh)
  or pass a slice that exists."
    ;;
  UNCONFIGURED\ *)
    die "slice \"$slice\" exists at ${verdict#UNCONFIGURED } but every resource
  knob is still at its kernel default, which is what a fail-open transient
  slice looks like. Refusing to treat it as a real tier."
    ;;
  *)
    die "could not probe the host cgroup tree for \"$slice\" (is the docker
  socket reachable, and is $PROBE_IMAGE available?)"
    ;;
esac
