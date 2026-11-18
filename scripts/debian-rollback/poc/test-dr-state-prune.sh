#!/usr/bin/env bash
# Offline test of dr-state's prune selection (no btrfs, no guest):
#   dr-state _prune-select KEEP PROTECTED_CSV NAME...   prints the snapshots that would be deleted, oldest first.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DR="$HERE/dr-state"
fail=0
A=@os-pre-20261118100000-1-apt; B=@os-pre-20261118100100-2-apt; C=@os-pre-20261118100200-3-apt
D=@os-pre-20261118100300-4-apt; E=@os-pre-20261118100400-5-s8

expect() { # expect LABEL "WANT (space separated, oldest first)" KEEP PROTECTED NAME...
  local label=$1 want=$2; shift 2
  local got; got=$("$DR" _prune-select "$@" 2>&1 | tr '\n' ' ' | sed 's/ $//')
  [ "$got" = "$want" ] || { echo "FAIL $label: want '$want' got '$got'"; fail=1; }
}

expect keep-two "$A $B $C" 2 "" $A $B $C $D $E
expect order-independent "$A $B $C" 2 "" $E $C $A $D $B
expect fewer-than-keep "" 10 "" $A $B $C
expect exactly-keep "" 3 "" $A $B $C
expect protected-oldest-survives "$B $C" 2 "$A" $A $B $C $D $E
expect protected-in-the-middle "$A $C" 2 "$B" $A $B $C $D $E
expect protected-newest-is-irrelevant "$A $B $C" 2 "$E" $A $B $C $D $E
expect several-protected "$C" 2 "$A,$B" $A $B $C $D $E
expect keep-zero-deletes-unprotected "$B $C $D $E" 0 "$A" $A $B $C $D $E
expect main-state-ignored "$A" 2 "" @os-0001 $A $B $C
expect only-main-state "" 2 "" @os-0001
expect empty-protected-field "$A" 1 "" $A $B
expect no-names "" 3 ""

[ "$fail" = 0 ] && echo "test-dr-state-prune: OK"
exit "$fail"
