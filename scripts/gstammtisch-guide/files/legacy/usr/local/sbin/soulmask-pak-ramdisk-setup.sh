#!/usr/bin/env bash
# Populate a SHARED tmpfs with Soulmask's pak file and bind-mount it into
# EVERY configured instance's Paks directory that opts in via PAK_RAMDISK=1
# (/etc/gstammtisch/instances.d/<uuid>.env). One tmpfs, one pak copy,
# bind-mounted N times: the pak file is byte-identical across instances —
# every Soulmask instance on this host runs the same Linux dedicated-server
# depot (appid 3017300, depot 3017301, no separate DLC depot; verified —
# SOULMASK.md §9). The container sees identical paths either way.
#
# Why a ramdisk at all: pak files as *file cache* are silently freed under
# memory pressure — they are "clean" (re-readable from disk), so the kernel
# just drops them without going through zswap. Next access = disk read
# (1–10 ms). On tmpfs they become *shmem* (anonymous-backed): the kernel
# CANNOT silently free them; they must go through zswap first. Warm pak
# pages stay in zswap instead of being freed to nowhere. Worst-case = zswap
# decompress (~3µs) vs disk re-read (~10 ms).
# On a busy dev host with Docker builds competing for RAM, this guarantee is
# what prevents multi-second game stalls: the kernel cannot evict warm pak
# pages silently.
#
# Note: pages charged to root cgroup (cp ran as root). memory.min on a
# Soulmask instance's own cgroup does NOT protect pak pages. Use
# soulmask-paks.slice for independent control (writeback=yes, low
# memory.min — see SOULMASK.md §2c).
#
# Invoked by soulmask-pak-ramdisk.service (Before=docker.service) — runs
# BEFORE Docker/Wings starts, so container volume dirs may not exist yet for
# instances that haven't been installed once. Those are skipped with a log
# line and picked up on a later run once Wings has created them.
#
# N-instance target discovery (2026-07-07): enumerates
# /etc/gstammtisch/instances.d/*.env (NOT `docker ps` / a single
# SOULMASK_PAK_DIR / /etc/soulmask-ramdisk.conf as before — this runs before
# docker.service so no container exists to inspect yet, and multiple targets
# need tracking). PAK_RAMDISK=1 in an instance's override file opts its
# <volume>/WS/Content/Paks directory in.
#
# Safe to run again: idempotent — already-mounted binds and an
# already-mounted tmpfs are left alone; only missing ones are added.
set -euo pipefail

# shellcheck source=/usr/local/sbin/soulmask-instance-lib.sh
LIB="${LIB:-/usr/local/sbin/soulmask-instance-lib.sh}"
if [ -f "$LIB" ]; then
  . "$LIB"
else
  echo "[pak-ramdisk] FATAL: $LIB not found (expected alongside this script)"; exit 1
fi

RAMDISK="/mnt/soulmask-paks"
TMPFS_SIZE="${SOULMASK_RAMDISK_SIZE:-3G}"   # headroom above current ~1.7G pak; raise for game updates
STATE_FILE="/run/soulmask-pak-ramdisk.state"
INSTANCES_DIR="${GSTAMMTISCH_ETC:-/etc/gstammtisch}/instances.d"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
log() { echo "[pak-ramdisk] $*"; }
run() { if [ $DRY_RUN -eq 1 ]; then echo "  DRY-RUN: $*"; else "$@"; fi; }

_parse_bytes() {
  local v="$1"
  case "$v" in
    *G) echo $(( ${v%G} * 1073741824 )) ;;
    *M) echo $(( ${v%M} * 1048576 )) ;;
    *)  echo "$v" ;;
  esac
}

# --- collect opted-in instances -------------------------------------------
declare -a TARGETS=()
shopt -s nullglob
for f in "$INSTANCES_DIR"/*.env; do
  uuid=$(basename "$f" .env)
  soulmask_load_instance_env "$uuid"
  [ "$PAK_RAMDISK" = "1" ] || continue
  pak_dir="/var/lib/pterodactyl/volumes/${uuid}/WS/Content/Paks"
  if [ ! -d "$pak_dir" ]; then
    log "instance $uuid: PAK_RAMDISK=1 but volume not found yet ($pak_dir) — skipping (will retry next run)"
    continue
  fi
  TARGETS+=("$pak_dir")
  log "instance $uuid: pak bind target $pak_dir"
done
shopt -u nullglob

if [ "${#TARGETS[@]}" -eq 0 ]; then
  log "no instance opts into PAK_RAMDISK=1 (or none of their volumes exist yet) — nothing to do"
  exit 0
fi

# --- pick a source pak copy: first target that already has a .pak file ----
SRC_DIR=""
for t in "${TARGETS[@]}"; do
  if find "$t" -maxdepth 1 -name '*.pak' -print -quit 2>/dev/null | grep -q .; then
    SRC_DIR="$t"
    break
  fi
done
if [ -z "$SRC_DIR" ]; then
  log "ERROR: none of the opted-in target directories contain a .pak file yet."
  log "  ${TARGETS[*]}"
  exit 1
fi
log "source pak copy: $SRC_DIR"

# --- size check: pak payload vs tmpfs size ---------------------------------
mapfile -t PAK_FILES < <(find "$SRC_DIR" -maxdepth 1 \( -name '*.pak' -o -name '*.sig' -o -name '*.utoc' -o -name '*.ucas' \) 2>/dev/null)
PAK_BYTES=0
if [ "${#PAK_FILES[@]}" -gt 0 ]; then
  PAK_BYTES=$(du -cb "${PAK_FILES[@]}" 2>/dev/null | tail -n1 | awk '{print $1}') || true
fi
PAK_BYTES="${PAK_BYTES:-0}"
TMPFS_BYTES=$(_parse_bytes "$TMPFS_SIZE")
if [ "$PAK_BYTES" -gt 0 ] && [ "$TMPFS_BYTES" -gt 0 ] && [ "$PAK_BYTES" -ge "$TMPFS_BYTES" ]; then
  log "ERROR: pak payload ($(( PAK_BYTES/1048576 ))M) >= tmpfs size ($(( TMPFS_BYTES/1048576 ))M)."
  log "  Raise SOULMASK_RAMDISK_SIZE (env) before retrying."
  exit 1
fi
log "pak payload: $(( PAK_BYTES/1048576 ))M ; tmpfs size: $TMPFS_SIZE (headroom: $(( (TMPFS_BYTES-PAK_BYTES)/1048576 ))M)"

# --- tmpfs -------------------------------------------------------------------
if ! mountpoint -q "$RAMDISK" 2>/dev/null; then
  log "creating tmpfs ($TMPFS_SIZE) at $RAMDISK"
  run mkdir -p "$RAMDISK"
  run mount -t tmpfs -o "size=${TMPFS_SIZE}" tmpfs "$RAMDISK"

  log "copying pak files from $SRC_DIR → $RAMDISK"
  # Copy all UE4 asset package types: .pak, .sig (signature), .utoc/.ucas (IO store, UE5+)
  run bash -c "find '$SRC_DIR' -maxdepth 1 \( -name '*.pak' -o -name '*.sig' -o -name '*.utoc' -o -name '*.ucas' \) \
    -exec cp -v {} '$RAMDISK/' \;"
  # Preserve the container user's ownership (cp as root produces root:root,
  # which blocks in-container steam updates writing through the bind).
  PAK_OWNER=$(stat -c '%u:%g' "$SRC_DIR" 2>/dev/null || echo "")
  [ -n "$PAK_OWNER" ] && run chown -R "$PAK_OWNER" "$RAMDISK"
else
  log "tmpfs already mounted at $RAMDISK — reusing existing pak copy"
fi

# --- bind-mount into every target that isn't already bound ------------------
BOUND=()
for t in "${TARGETS[@]}"; do
  if mountpoint -q "$t" 2>/dev/null; then
    log "already bind-mounted: $t"
  else
    log "bind mounting $RAMDISK → $t"
    run mount --bind "$RAMDISK" "$t"
  fi
  BOUND+=("$t")
done

if [ $DRY_RUN -eq 0 ]; then
  # Persist so teardown (ExecStop) and the toggle script can find every bind
  # target without a running container. TARGET= lines double as both
  # grep-able (any shell) and dot-sourceable (bash: last one wins, which is
  # why consumers grep instead of sourcing this file wholesale).
  {
    echo "# soulmask-pak-ramdisk state (generated $(date -Is)) — do not edit"
    echo "RAMDISK=$RAMDISK"
    echo "TMPFS_SIZE=$TMPFS_SIZE"
    for t in "${BOUND[@]}"; do echo "TARGET=$t"; done
  } > "$STATE_FILE"
  log "done — ${#BOUND[@]} instance(s) now served from tmpfs-backed pak (anon pages, zswap-eligible, memory.min protected)"
  for t in "${BOUND[@]}"; do
    log "verify: $(findmnt "$t" 2>/dev/null | tail -n1 || echo "$t (findmnt failed)")"
  done
fi
