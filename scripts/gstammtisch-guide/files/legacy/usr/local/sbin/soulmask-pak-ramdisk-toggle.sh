#!/usr/bin/env bash
# Toggle the Soulmask pak ramdisk on or off.
#
# The ramdisk only takes effect at container start — the game mmap's pak files
# at startup and the mmap stays bound to the original inode for its lifetime.
# This script sets up or tears down the host-side mounts; you must then do a
# clean stop + start of the Soulmask server via Wings for the change to apply.
#
# N-instance (2026-07-07): this toggles the ONE shared tmpfs used by every
# instance whose /etc/gstammtisch/instances.d/<uuid>.env sets PAK_RAMDISK=1
# (see soulmask-pak-ramdisk-setup.sh) — it is not per-instance itself. Every
# opted-in instance needs its own clean stop+start for the change to apply
# to it.
#
# Usage:
#   soulmask-pak-ramdisk-toggle.sh           # toggle (on→off, off→on)
#   soulmask-pak-ramdisk-toggle.sh on        # activate (idempotent)
#   soulmask-pak-ramdisk-toggle.sh off       # deactivate (idempotent)
#   soulmask-pak-ramdisk-toggle.sh status    # show current state and exit
set -euo pipefail

SERVICE="soulmask-pak-ramdisk.service"
STATE_FILE="/run/soulmask-pak-ramdisk.state"

log() { echo "[pak-ramdisk] $*"; }

_is_active() {
  systemctl is-active --quiet "$SERVICE" 2>/dev/null
}

_show_status() {
  if _is_active; then
    log "ACTIVE — ramdisk is mounted"
    # State file (N-instance, 2026-07-07): one "TARGET=<dir>" line per
    # bind-mounted instance, written by soulmask-pak-ramdisk-setup.sh.
    if [ -f "$STATE_FILE" ]; then
      local line found=0
      while IFS= read -r line; do
        case "$line" in
          TARGET=*) log "  bind: /mnt/soulmask-paks → ${line#TARGET=}"; found=1 ;;
        esac
      done < "$STATE_FILE"
      [ "$found" -eq 1 ] || log "  (state file present but lists no bind targets)"
    fi
    log "  (current server session(s) use disk-backed pak until their next restart)"
  else
    log "INACTIVE — pak files served from disk"
  fi
}

_activate() {
  if _is_active; then
    log "already active — nothing to do"
    _show_status; return
  fi
  log "activating ramdisk (copying ~1.7G pak, may take a few seconds)..."
  systemctl start "$SERVICE"
  _show_status
  echo ""
  log "ACTION REQUIRED: stop each opted-in (PAK_RAMDISK=1) Soulmask instance via Wings, then start it again."
  log "  Each running session still uses disk-backed pak until ITS next container start."
  log "  Wings → server → power → stop → start"
}

_deactivate() {
  if ! _is_active; then
    log "already inactive — nothing to do"
    return
  fi
  log "deactivating ramdisk..."
  systemctl stop "$SERVICE"
  log "INACTIVE — ramdisk unmounted, pak directories reverted to disk"
  echo ""
  log "ACTION REQUIRED: stop each previously-opted-in Soulmask instance via Wings, then start it again."
  log "  Each running session still uses ramdisk-backed pak until ITS next container start."
  log "  Wings → server → power → stop → start"
}

ACTION="${1:-toggle}"

case "$ACTION" in
  status)
    _show_status ;;
  on|activate|enable)
    _activate ;;
  off|deactivate|disable)
    _deactivate ;;
  toggle)
    if _is_active; then _deactivate; else _activate; fi ;;
  *)
    echo "Usage: $(basename "$0") [on|off|toggle|status]" >&2
    exit 1 ;;
esac
