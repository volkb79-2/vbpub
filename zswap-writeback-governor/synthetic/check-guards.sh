#!/usr/bin/env bash
# Static refusal check for the synthetic's safety guard. It only reads text files (grep/awk);
# it executes none of the synthetic and changes nothing, so it is safe anywhere.
#
# It fails (exit 1) when:
#   G1  a guest script that mutates the machine does not source lib-guard.sh and call
#       require_harness_guest, or calls it AFTER its first mutating command;
#   G2  any non-comment `swapoff -a` appears (the guard allows only the guest's own /swapfile);
#   G3  `systemd-detect-virt` is used in a guest script (production is a KVM guest too: it proves nothing);
#   G4  lib-guard.sh lost the marker path, the nonce comparison, or the refuse-foreign-swap check;
#   G5  the driver stopped writing the marker, or passes no ZWG_NONCE to a guest script;
#   G6  a Python helper touches swap or sysctls;
#   G7  a Python helper (workload, bgpressure, sampler) lacks or never calls its marker check.
# Run: bash check-guards.sh   (also lint-clean under `shellcheck`)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
fail=0
bad() { echo "check-guards: FAIL $*" >&2; fail=1; }

noncomment() { grep -v '^[[:space:]]*#' "$1"; }

MUTATING='(^|[^[:alnum:]_-])(swapon|swapoff|mkswap|sysctl +-q? *-w|sysctl -w|fallocate|apt-get|mount +-t|mkdir -p /sys/fs/cgroup|set_cg +|set_band +)|> */sys/|> */etc/'

for f in setup-guest.sh run-variant.sh ensure-kernel.sh; do
    path="$HERE/guest/$f"
    [ -f "$path" ] || { bad "G1 $f missing"; continue; }
    guard_line="$(grep -n '^[[:space:]]*require_harness_guest[[:space:]]*\(#.*\)\?$' "$path" | head -1 | cut -d: -f1 || true)"
    src_line="$(grep -n '^[[:space:]]*\. .*lib-guard.sh' "$path" | head -1 | cut -d: -f1 || true)"
    [ -n "$guard_line" ] && [ -n "$src_line" ] || { bad "G1 $f does not source lib-guard.sh and call require_harness_guest"; continue; }
    first_mut="$(awk -v pat="$MUTATING" '/^[[:space:]]*#/ {next} $0 ~ pat {print NR; exit}' "$path")"
    if [ -n "$first_mut" ] && [ "$first_mut" -le "$guard_line" ]; then
        bad "G1 $f: first mutating line $first_mut is not after require_harness_guest (line $guard_line)"
    fi
done

for f in "$HERE"/guest/* "$HERE"/run-synthetic.sh; do
    if noncomment "$f" | grep -Eq 'swapoff[[:space:]]+-a'; then bad "G2 $f uses swapoff -a"; fi
done
for f in "$HERE"/guest/*.sh "$HERE"/guest/*.py; do
    if noncomment "$f" | grep -q 'systemd-detect-virt'; then bad "G3 $f relies on systemd-detect-virt"; fi
done

lib="$HERE/guest/lib-guard.sh"
grep -q '/etc/zwg-harness-guest' "$lib" || bad "G4 marker path missing from lib-guard.sh"
grep -q 'nonce=\$ZWG_NONCE' "$lib" || bad "G4 nonce comparison missing from lib-guard.sh"
grep -q 'zwg_require_only_own_swap' "$lib" || bad "G4 foreign-swap refusal missing from lib-guard.sh"
grep -q 'exit 97' "$lib" || bad "G4 lib-guard.sh no longer exits non-zero on refusal"

grep -q 'mark_guest' "$HERE/run-synthetic.sh" || bad "G5 driver does not write the marker"
for s in ensure-kernel.sh setup-guest.sh run-variant.sh; do
    noncomment "$HERE/run-synthetic.sh" | grep -q "ZWG_NONCE=.*$s\|$s" || bad "G5 driver does not run $s"
done
[ "$(noncomment "$HERE/run-synthetic.sh" | grep -c 'ZWG_RUN=\$run ZWG_NONCE=')" -ge 3 ] \
    || bad "G5 driver passes ZWG_RUN/ZWG_NONCE to fewer than three guest invocations"

for f in "$HERE"/guest/*.py; do
    if noncomment "$f" | grep -Eq 'swapon|swapoff|sysctl|/proc/sys/vm'; then bad "G6 $f touches swap or sysctls"; fi
done

for f in "$HERE"/guest/*.py; do
    noncomment "$f" | grep -q 'require_harness_guest()' || bad "G7 $f has no require_harness_guest"
    [ "$(noncomment "$f" | grep -c 'require_harness_guest()')" -ge 2 ] || bad "G7 $f defines but never calls require_harness_guest"
done

if [ "$fail" = 0 ]; then echo "check-guards: ok"; fi
exit "$fail"
