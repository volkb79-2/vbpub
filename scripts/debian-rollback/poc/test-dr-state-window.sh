#!/usr/bin/env bash
# Offline test of dr-state's dedupe window decision (no btrfs, no guest): `dr-state _window-blocks NAME INTERVAL NOW`
# exits 0 when the snapshot NAME is younger than INTERVAL seconds at NOW.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DR="$HERE/dr-state"
fail=0
made=$(date -u -d "2026-11-18 13:11:58" +%s)
name="@os-pre-20261118131158-605-apt"

expect() { # expect LABEL WANT(0|1) NAME INTERVAL NOW
  "$DR" _window-blocks "$3" "$4" "$5" >/dev/null 2>&1; local rc=$?
  [ "$rc" = "$2" ] || { echo "FAIL $1: want exit $2, got $rc"; fail=1; }
}

expect young-blocks 0 "$name" 900 $((made + 100))
expect just-inside-blocks 0 "$name" 900 $((made + 899))
expect exactly-at-interval-takes 1 "$name" 900 $((made + 900))
expect old-takes 1 "$name" 900 $((made + 5000))
expect same-second-blocks 0 "$name" 900 "$made"
expect future-name-takes 1 "$name" 900 $((made - 60))        # clock jump: never block on a name from the future
expect interval-zero-takes 1 "$name" 0 $((made + 1))
expect malformed-takes 1 "@os-pre-notadate-605-apt" 900 $((made + 10))
expect short-stamp-takes 1 "@os-pre-2026111813-605-apt" 900 $((made + 10))
expect no-prefix-takes 1 "garbage" 900 $((made + 10))
expect impossible-date-takes 1 "@os-pre-20261340250000-1-apt" 900 $((made + 10))

[ "$fail" = 0 ] && echo "test-dr-state-window: OK"
exit "$fail"
