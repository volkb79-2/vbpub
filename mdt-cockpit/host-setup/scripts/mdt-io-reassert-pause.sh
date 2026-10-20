#!/usr/bin/env bash
# Pause the minute-by-minute re-assert of the scheduler, queue values, vm.dirty_* and io.cost for a bounded time
# (MDT-HS-07), so a hand experiment on a managed host is not undone within a minute. The io.max caps stay enforced.
#   mdt-io-reassert-pause <minutes> <reason...>   pause for 1..240 minutes (the reason is logged every minute)
#   mdt-io-reassert-pause --status                show the pause, if any
#   mdt-io-reassert-pause --clear                 end the pause now
# The pause lives in /run/mdt/io-reassert.pause ("<expiry-epoch> <reason>"): runtime state, gone at reboot.
set -uo pipefail
LIB="${LIB:-/usr/local/sbin/mdt-container-io-caps.lib.sh}"
log() { echo "[mdt-io-reassert-pause] $*" >&2; }
[ -r "$LIB" ] || { log "ERROR: cannot read $LIB"; exit 2; }
# shellcheck disable=SC1090
. "$LIB"
file=$(_mdt_io_pause_file)
usage() { echo "usage: mdt-io-reassert-pause <minutes 1..$MDT_IO_REASSERT_PAUSE_MAX_MINUTES> <reason...> | --status | --clear" >&2; exit 2; }
case "${1:-}" in
  --status)
    if msg=$(_mdt_io_pause_state); then echo "re-assert PAUSED $msg"; else rc=$?; [ "$rc" = 1 ] && echo "re-assert is active (no pause)" || echo "$msg"; fi
    exit 0 ;;
  --clear) rm -f -- "$file"; echo "pause cleared; the next reconcile run re-asserts"; exit 0 ;;
  ''|-h|--help) usage ;;
esac
minutes="$1"; shift
case "$minutes" in ''|*[!0-9]*) usage ;; esac
{ [ "$minutes" -ge 1 ] && [ "$minutes" -le "$MDT_IO_REASSERT_PAUSE_MAX_MINUTES" ]; } || usage
reason="$*"
[ -n "$reason" ] || { log "a reason is required"; usage; }
mkdir -p "$(dirname "$file")" || exit 1
expiry=$(( $(_mdt_now) + minutes * 60 ))
printf '%s %s\n' "$expiry" "$reason" > "$file.tmp" && mv -f "$file.tmp" "$file" || exit 1
echo "re-assert paused for $minutes minute(s) until $(date -u -d "@$expiry" +%FT%TZ 2>/dev/null || echo "$expiry") ($reason); the io.max caps stay enforced. End early: mdt-io-reassert-pause --clear"
