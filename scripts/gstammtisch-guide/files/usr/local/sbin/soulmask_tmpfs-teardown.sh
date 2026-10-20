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

[ "${DRY_RUN:-0}" = "1" ] && DRY_RUN=1 || DRY_RUN=0   # env DRY_RUN=1 or --dry-run
SHOW_HELP=0
case "${1:-}" in
  --help|-h) SHOW_HELP=1 ;;
  --dry-run) DRY_RUN=1 ;;
esac

if [ $SHOW_HELP -eq 1 ]; then
  echo "Usage: $(basename "$0") [--dry-run|--help]   (env DRY_RUN=1 also dry-runs)"
  echo ""
  echo "ExecStop for soulmask_tmpfs.service — unmount every bind target recorded"
  echo "in the state file, every per-instance tmpfs (PI_TARGET=; a busy one is"
  echo "reported and makes the exit status non-zero), then the shared tmpfs itself."
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

STATE_FILE="${SOULMASK_TMPFS_STATE_FILE:-/run/soulmask_tmpfs.state}"
RAMDISK="${SOULMASK_TMPFS_RAMDISK:-/mnt/soulmask_tmpfs}"
TMPFILES_FILE="${SOULMASK_TMPFILES_DIR:-/run/tmpfiles.d}/soulmask-per-instance.conf"
RC=0
declare -a PI_BUSY=()

# Also clean up legacy state files if they still exist (post-migration)
LEGACY_PAK_STATE="/run/soulmask-pak-ramdisk.state"
LEGACY_STATIC_STATE="/run/soulmask-static-ramdisk.state"

log() { echo "[soulmask_tmpfs] $*"; }

# umount that honours DRY_RUN (prints, does nothing, reports success).
_umount() {
  if [ $DRY_RUN -eq 1 ]; then echo "  DRY-RUN: umount $*"; return 0; fi
  umount "$@" 2>/dev/null
}

# Per-instance tmpfs mounts (PI_TARGET=). Unlike a bind target, one that cannot
# be unmounted is NOT swallowed: it is reported, remembered, and fails the run.
_unmount_pi_from_state() {
  local f="$1" line t
  [ -f "$f" ] || return 0
  while IFS= read -r line; do
    case "$line" in
      PI_TARGET=*)
        t="${line#*=}"
        [ -n "$t" ] || continue
        if ! mountpoint -q "$t" 2>/dev/null; then
          log "  (per-instance tmpfs already unmounted: $t)"
        elif _umount "$t"; then
          log "unmounted per-instance tmpfs $t"
        else
          log "ERROR: per-instance tmpfs $t is busy (a process still has files open on it) — NOT unmounted; stop the game server and re-run"
          PI_BUSY+=("$t")
          RC=1
        fi
        ;;
    esac
  done < "$f"
}

_unbind_from_state() {
  local f="$1"
  [ -f "$f" ] || return
  while IFS= read -r line; do
    case "$line" in
      TARGET=*)
        t="${line#*=}"
        [ -n "$t" ] || continue
        if _umount "$t"; then
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

# Unmount the per-instance tmpfs mounts, and stop age-pruning them
_unmount_pi_from_state "$STATE_FILE"
if [ -e "$TMPFILES_FILE" ]; then
  if [ $DRY_RUN -eq 1 ]; then
    echo "  DRY-RUN: rm -f $TMPFILES_FILE"
  else
    rm -f "$TMPFILES_FILE" && log "removed $TMPFILES_FILE"
  fi
fi

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
  if [ $DRY_RUN -eq 0 ] && findmnt -no OPTIONS "$RAMDISK" 2>/dev/null | grep -q '\<ro\>'; then
    mount -o remount,rw "$RAMDISK" 2>/dev/null || true
  fi
  if _umount "$RAMDISK"; then
    log "unmounted tmpfs $RAMDISK"
  elif [ $DRY_RUN -eq 0 ]; then
    log "  (tmpfs busy — may still have bind targets; trying lazy umount)"
    umount -l "$RAMDISK" 2>/dev/null || log "  (could not unmount $RAMDISK)"
  fi
else
  log "  (tmpfs already unmounted: $RAMDISK)"
fi

# Clean up state files. A per-instance tmpfs that was busy stays recorded (alone)
# so a later teardown still finds it.
if [ $DRY_RUN -eq 1 ]; then
  echo "  DRY-RUN: rm -f $STATE_FILE $LEGACY_PAK_STATE $LEGACY_STATIC_STATE"
else
  rm -f "$STATE_FILE" "$LEGACY_PAK_STATE" "$LEGACY_STATIC_STATE"
  if [ "${#PI_BUSY[@]}" -gt 0 ]; then
    {
      echo "# soulmask_tmpfs state (teardown left these busy per-instance tmpfs mounts) — do not edit"
      for t in "${PI_BUSY[@]}"; do echo "PI_TARGET=$t"; done
    } > "$STATE_FILE"
  fi
fi
if [ $RC -ne 0 ]; then
  log "teardown INCOMPLETE — ${#PI_BUSY[@]} per-instance tmpfs mount(s) busy"
else
  log "teardown complete"
fi
exit $RC
