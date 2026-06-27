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
set -euo pipefail

VOL_BASE="/var/lib/pterodactyl/volumes/b87c0a5b-2387-4a1c-8863-ff23e6800a1d"
PAK_DIR="$VOL_BASE/WS/Content/Paks"
RAMDISK="/mnt/soulmask-paks"
TMPFS_SIZE="3G"   # headroom above current 1.7G pak; raise if DLC paks are added

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
log() { echo "[pak-ramdisk] $*"; }
run() { if [ $DRY_RUN -eq 1 ]; then echo "  DRY-RUN: $*"; else "$@"; fi; }

# Already mounted — nothing to do
if mountpoint -q "$RAMDISK" 2>/dev/null; then
    log "tmpfs already mounted at $RAMDISK"
    if mountpoint -q "$PAK_DIR" 2>/dev/null; then
        log "bind mount already active at $PAK_DIR — nothing to do"
        exit 0
    fi
fi

log "creating tmpfs ($TMPFS_SIZE) at $RAMDISK"
run mkdir -p "$RAMDISK"
run mount -t tmpfs -o "size=${TMPFS_SIZE}" tmpfs "$RAMDISK"

log "copying pak files from $PAK_DIR → $RAMDISK"
run cp -v "$PAK_DIR"/WS-LinuxServer.pak "$RAMDISK/"
run cp -v "$PAK_DIR"/WS-LinuxServer.sig "$RAMDISK/" 2>/dev/null || true
# Copy any additional .pak / .sig / .utoc / .ucas files added by updates
run bash -c "find '$PAK_DIR' -maxdepth 1 \( -name '*.pak' -o -name '*.sig' -o -name '*.utoc' -o -name '*.ucas' \) \
    ! -name 'WS-LinuxServer.*' \
    -exec cp -v {} '$RAMDISK/' \;"

log "bind mounting $RAMDISK → $PAK_DIR"
run mount --bind "$RAMDISK" "$PAK_DIR"

if [ $DRY_RUN -eq 0 ]; then
    log "done — pak files are now tmpfs-backed (anon pages, zswap-eligible, memory.min protected)"
    log "verify: findmnt $PAK_DIR"
    findmnt "$PAK_DIR" || true
fi
