#!/usr/bin/env bash
# Populate a tmpfs with Soulmask's pak file and bind-mount it over the on-disk
# Paks directory before Docker/Wings starts. The container sees identical paths.
#
# Why: pak files are clean file cache — evicted silently under memory pressure,
# causing major page faults (disk reads, 1–10 ms each) → 3-second game stalls.
# On tmpfs they become anonymous pages: covered by memory.min, routed through
# zswap, never freed by benchmark I/O. Worst-case fault = zswap decompress (~1µs).
#
# Invoked by soulmask-pak-ramdisk.service (Before=docker.service).
# Safe to run again: idempotent if tmpfs is already mounted.
#
# Volume discovery order (first match wins):
#   1. SOULMASK_PAK_DIR env var
#   2. /etc/soulmask-ramdisk.conf  (SOULMASK_PAK_DIR= or SOULMASK_VOLUME_UUID=)
#   3. /run/soulmask-pak-ramdisk.state  (saved from a prior run)
#   4. filesystem scan: find WS-LinuxServer.pak under /var/lib/pterodactyl/volumes
#      (uses -xdev to skip tmpfs overlays so a live ramdisk doesn't confuse the scan)
set -euo pipefail

RAMDISK="/mnt/soulmask-paks"
TMPFS_SIZE="${SOULMASK_RAMDISK_SIZE:-3G}"   # headroom above current 1.7G pak; raise for updates
STATE_FILE="/run/soulmask-pak-ramdisk.state"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
log() { echo "[pak-ramdisk] $*"; }
run() { if [ $DRY_RUN -eq 1 ]; then echo "  DRY-RUN: $*"; else "$@"; fi; }

# --- discover PAK_DIR ---
_discover_pak_dir() {
  # 1. Explicit env
  if [ -n "${SOULMASK_PAK_DIR:-}" ]; then
    echo "$SOULMASK_PAK_DIR"; return 0
  fi
  # 2. Config file
  if [ -f /etc/soulmask-ramdisk.conf ]; then
    # shellcheck disable=SC1091
    . /etc/soulmask-ramdisk.conf
    if [ -n "${SOULMASK_PAK_DIR:-}" ]; then
      echo "$SOULMASK_PAK_DIR"; return 0
    fi
    if [ -n "${SOULMASK_VOLUME_UUID:-}" ]; then
      echo "/var/lib/pterodactyl/volumes/${SOULMASK_VOLUME_UUID}/WS/Content/Paks"; return 0
    fi
  fi
  # 3. State file from a previous successful setup
  if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    . "$STATE_FILE"
    if [ -n "${PAK_DIR:-}" ] && [ -d "$PAK_DIR" ]; then
      echo "$PAK_DIR"; return 0
    fi
  fi
  # 4. Filesystem scan: -xdev stops at mount boundaries so a live tmpfs overlay
  #    on PAK_DIR doesn't shadow the real on-disk path we're trying to find.
  local hit
  hit=$(find /var/lib/pterodactyl/volumes -xdev -maxdepth 7 \
    -name 'WS-LinuxServer.pak' -print -quit 2>/dev/null || true)
  if [ -n "$hit" ]; then
    dirname "$hit"; return 0
  fi
  return 1
}

PAK_DIR=$(_discover_pak_dir) || {
  log "ERROR: cannot locate WS-LinuxServer.pak."
  log "  Set SOULMASK_PAK_DIR or SOULMASK_VOLUME_UUID in /etc/soulmask-ramdisk.conf."
  log "  Example: echo 'SOULMASK_VOLUME_UUID=<uuid>' > /etc/soulmask-ramdisk.conf"
  exit 1
}
log "pak directory: $PAK_DIR"

# Already fully mounted — nothing to do
if mountpoint -q "$RAMDISK" 2>/dev/null && mountpoint -q "$PAK_DIR" 2>/dev/null; then
  log "ramdisk already active at $RAMDISK → $PAK_DIR — nothing to do"
  exit 0
fi

log "creating tmpfs ($TMPFS_SIZE) at $RAMDISK"
run mkdir -p "$RAMDISK"
run mount -t tmpfs -o "size=${TMPFS_SIZE}" tmpfs "$RAMDISK"

log "copying pak files from $PAK_DIR → $RAMDISK"
# Copy all UE4 asset package types: .pak, .sig (signature), .utoc/.ucas (IO store, UE5+)
run bash -c "find '$PAK_DIR' -maxdepth 1 \( -name '*.pak' -o -name '*.sig' -o -name '*.utoc' -o -name '*.ucas' \) \
  -exec cp -v {} '$RAMDISK/' \;"

log "bind mounting $RAMDISK → $PAK_DIR"
run mount --bind "$RAMDISK" "$PAK_DIR"

if [ $DRY_RUN -eq 0 ]; then
  # Persist discovered path so teardown and toggle can find it without a running container
  echo "PAK_DIR='$PAK_DIR'" > "$STATE_FILE"
  log "done — pak files are now tmpfs-backed (anon pages, zswap-eligible, memory.min protected)"
  log "verify: findmnt '$PAK_DIR'"
  findmnt "$PAK_DIR" || true
fi
