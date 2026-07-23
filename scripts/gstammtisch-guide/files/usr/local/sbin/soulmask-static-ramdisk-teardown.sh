#!/usr/bin/env bash
# ExecStop for soulmask-static-ramdisk.service — unmount every bind target
# recorded in the state file written by soulmask-static-ramdisk-setup.sh,
# then the shared tmpfs itself. Mirrors soulmask-pak-ramdisk-teardown.sh's
# structure exactly; kept as a separate script/service/state-file/tmpfs
# from the pak one on purpose (see soulmask-static-ramdisk-setup.sh's
# header) rather than merged into it.
#
# Tolerant if the state file or any mount is already gone — idempotent
# teardown, matching the rest of this project's style.
set -uo pipefail

STATE_FILE="/run/soulmask-static-ramdisk.state"
RAMDISK="/mnt/soulmask-static"

log() { echo "[static-ramdisk] $*"; }

if [ -f "$STATE_FILE" ]; then
  while IFS= read -r line; do
    case "$line" in
      TARGET=*)
        t="${line#TARGET=}"
        [ -n "$t" ] || continue
        if umount "$t" 2>/dev/null; then
          log "unbound $t"
        else
          log "  (already unmounted: $t)"
        fi
        ;;
    esac
  done < "$STATE_FILE"
else
  log "no state file — nothing recorded to unbind (best-effort tmpfs-only teardown)"
fi

if umount "$RAMDISK" 2>/dev/null; then
  log "unmounted tmpfs $RAMDISK"
else
  log "  (tmpfs already unmounted: $RAMDISK)"
fi

rm -f "$STATE_FILE"
exit 0
