#!/usr/bin/env bash
# ExecStop for soulmask-pak-ramdisk.service — unmount every pak bind target
# recorded in the state file written by soulmask-pak-ramdisk-setup.sh, then
# the shared tmpfs itself. A dedicated script (rather than an inline
# `/bin/sh -c` ExecStop=) because the state file now lists a variable number
# of N-instance bind targets (TARGET=<dir> lines) — trivial to loop over in
# bash, awkward to do portably in the `/bin/sh` (dash) the unit used to run.
#
# Tolerant if the state file or any mount is already gone — idempotent
# teardown, matching the rest of this project's style.
set -uo pipefail

STATE_FILE="/run/soulmask-pak-ramdisk.state"
RAMDISK="/mnt/soulmask-paks"

log() { echo "[pak-ramdisk] $*"; }

if [ -f "$STATE_FILE" ]; then
  while IFS= read -r line; do
    case "$line" in
      TARGET=*|PAK_DIR=*)
        # TARGET= is the N-instance format; PAK_DIR='...' is the legacy
        # single-instance format (pre 2026-07-07) — handled too so a stop
        # right after upgrading still unbinds the pre-upgrade mount.
        t="${line#*=}"
        t="${t#\'}"; t="${t%\'}"
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
