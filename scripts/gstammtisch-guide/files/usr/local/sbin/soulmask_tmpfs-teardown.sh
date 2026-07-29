#!/usr/bin/env bash
# ExecStop for soulmask_tmpfs.service — unmount every bind target recorded
# in the state file written by soulmask_tmpfs-setup.sh, then the shared
# tmpfs itself.
#
# Handles both the new format (TARGET=<path>) and legacy formats from
# migration (TARGET=<path> from the old soulmask-pak-ramdisk.state /
# soulmask-static-ramdisk.state) so a stop during/after migration cleans up
# everything.
#
# Tolerant if the state file or any mount is already gone — idempotent
# teardown.
set -uo pipefail

SHOW_HELP=0
case "${1:-}" in
  --help|-h) SHOW_HELP=1 ;;
esac

if [ $SHOW_HELP -eq 1 ]; then
  echo "Usage: $(basename "$0") [--help]"
  echo ""
  echo "ExecStop for soulmask_tmpfs.service — unmount every bind target recorded"
  echo "in the state file, then the shared tmpfs itself."
  echo ""
  echo "Handles legacy state files (soulmask-pak-ramdisk.state,"
  echo "soulmask-static-ramdisk.state) for migration cleanup."
  echo ""
  echo "Normally invoked by systemd. Run manually only for debugging:"
  echo "  sudo $(basename "$0")"
  echo ""
  echo "See SOULMASK-TMPFS.md for the full design."
  exit 0
fi

STATE_FILE="/run/soulmask_tmpfs.state"
RAMDISK="/mnt/soulmask_tmpfs"

# Also clean up legacy state files if they still exist (post-migration)
LEGACY_PAK_STATE="/run/soulmask-pak-ramdisk.state"
LEGACY_STATIC_STATE="/run/soulmask-static-ramdisk.state"

log() { echo "[soulmask_tmpfs] $*"; }

_unbind_from_state() {
  local f="$1"
  [ -f "$f" ] || return
  while IFS= read -r line; do
    case "$line" in
      TARGET=*)
        t="${line#*=}"
        [ -n "$t" ] || continue
        if umount "$t" 2>/dev/null; then
          log "unbound $t"
        else
          log "  (already unmounted: $t)"
        fi
        ;;
    esac
  done < "$f"
}

# Unbind current unified targets
_unbind_from_state "$STATE_FILE"

# Clean up any leftover legacy bind mounts
_unbind_from_state "$LEGACY_PAK_STATE"
_unbind_from_state "$LEGACY_STATIC_STATE"

# Also scan for any stray legacy mounts on the old mount points
for legacy_mp in /mnt/soulmask-paks /mnt/soulmask-static; do
  if mountpoint -q "$legacy_mp" 2>/dev/null; then
    # Find bind mounts sourced from this legacy tmpfs and unbind them
    while IFS= read -r target; do
      [ -n "$target" ] || continue
      if umount "$target" 2>/dev/null; then
        log "unbound legacy target $target"
      fi
    done < <(findmnt -nl -o TARGET --source "$legacy_mp" 2>/dev/null || true)
    if umount "$legacy_mp" 2>/dev/null; then
      log "unmounted legacy tmpfs $legacy_mp"
    fi
  fi
done

# Unmount the unified tmpfs
if mountpoint -q "$RAMDISK" 2>/dev/null; then
  # If it's read-only, remount rw first so umount doesn't complain
  if findmnt -no OPTIONS "$RAMDISK" 2>/dev/null | grep -q '\<ro\>'; then
    mount -o remount,rw "$RAMDISK" 2>/dev/null || true
  fi
  if umount "$RAMDISK" 2>/dev/null; then
    log "unmounted tmpfs $RAMDISK"
  else
    log "  (tmpfs busy — may still have bind targets; trying lazy umount)"
    umount -l "$RAMDISK" 2>/dev/null || log "  (could not unmount $RAMDISK)"
  fi
else
  log "  (tmpfs already unmounted: $RAMDISK)"
fi

# Clean up state files
rm -f "$STATE_FILE" "$LEGACY_PAK_STATE" "$LEGACY_STATIC_STATE"
log "teardown complete"
exit 0
