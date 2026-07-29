#!/usr/bin/env bash
# soulmask-static-ramdisk-setup.sh — Populate a SHARED tmpfs with Soulmask's
# static, read-only install content and bind-mount it into EVERY configured
# instance that opts in via STATIC_RAMDISK=1
# (/etc/gstammtisch/instances.d/<uuid>.env). Generalizes the pak-ramdisk
# pattern (soulmask-pak-ramdisk-setup.sh — kept unchanged, still the
# dedicated mechanism for the pak file specifically) to an arbitrary LIST of
# relative paths, each of which may be a directory or a single file,
# configured in /etc/gstammtisch/static-ramdisk-paths.conf.
#
# Why: identical content across instances still gets cached twice in RAM
# when each instance's own on-disk copy is a SEPARATE inode/file, even
# though the bytes are the same — Linux page cache is keyed per-inode, not
# per-content. Bind-mounting ONE shared tmpfs copy into every opted-in
# instance makes the page cache genuinely shared. As a side effect (same
# rationale as the pak ramdisk, SOULMASK.md §2c): tmpfs content is
# anonymous-backed, so the kernel cannot silently drop it as reclaimable
# clean file cache under pressure — it goes through zswap first, which
# matters more for this content than for the pak: WS/Binaries and Engine
# are executable code touched continuously during execution, not just at
# load time.
#
# Invoked by soulmask-static-ramdisk.service (Before=docker.service) — runs
# BEFORE Docker/Wings starts, so an instance's volume (and the paths inside
# it) may not exist yet for a not-yet-installed server. Those are skipped
# with a log line and picked up on a later run once Wings has created them.
#
# Safe to run again: idempotent — an already-mounted tmpfs, an
# already-populated path inside it, and an already-bound target are all
# left alone; only what's missing is added.
set -euo pipefail

# shellcheck source=/usr/local/sbin/soulmask-instance-lib.sh
LIB="${LIB:-/usr/local/sbin/soulmask-instance-lib.sh}"
if [ -f "$LIB" ]; then
  . "$LIB"
else
  echo "[static-ramdisk] FATAL: $LIB not found (expected alongside this script)"; exit 1
fi

RAMDISK="/mnt/soulmask-static"
TMPFS_SIZE="${SOULMASK_STATIC_RAMDISK_SIZE:-1G}"   # ~386M payload (2026-07-21) + headroom for game updates
STATE_FILE="/run/soulmask-static-ramdisk.state"
INSTANCES_DIR="${GSTAMMTISCH_ETC:-/etc/gstammtisch}/instances.d"
PATHS_FILE="${GSTAMMTISCH_ETC:-/etc/gstammtisch}/static-ramdisk-paths.conf"
VOLUMES_ROOT="${SOULMASK_VOLUMES_ROOT:-/var/lib/pterodactyl/volumes}"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
log() { echo "[static-ramdisk] $*"; }
run() { if [ $DRY_RUN -eq 1 ]; then echo "  DRY-RUN: $*"; else "$@"; fi; }

_parse_bytes() {
  local v="$1"
  case "$v" in
    *G) echo $(( ${v%G} * 1073741824 )) ;;
    *M) echo $(( ${v%M} * 1048576 )) ;;
    *)  echo "$v" ;;
  esac
}

# --- read configured paths ---------------------------------------------------
if [ ! -f "$PATHS_FILE" ]; then
  log "FATAL: $PATHS_FILE not found — one relative path per line, e.g.:"
  log "  Engine"
  log "  WS/Binaries"
  exit 1
fi
mapfile -t REL_PATHS < <(grep -vE '^\s*(#|$)' "$PATHS_FILE")
if [ "${#REL_PATHS[@]}" -eq 0 ]; then
  log "no paths configured in $PATHS_FILE — nothing to do"
  exit 0
fi

# --- collect opted-in instances -------------------------------------------
declare -a INSTANCE_UUIDS=()
shopt -s nullglob
for f in "$INSTANCES_DIR"/*.env; do
  uuid=$(basename "$f" .env)
  soulmask_load_instance_env "$uuid"
  [ "$STATIC_RAMDISK" = "1" ] || continue
  INSTANCE_UUIDS+=("$uuid")
done
shopt -u nullglob

if [ "${#INSTANCE_UUIDS[@]}" -eq 0 ]; then
  log "no instance opts into STATIC_RAMDISK=1 — nothing to do"
  exit 0
fi
log "opted-in instances: ${INSTANCE_UUIDS[*]}"

# --- resolve one source per path, size-check before touching anything ------
declare -A SRC_OF=()      # rel path -> source path on disk
declare -a READY_PATHS=() # rel paths with a source found this run
TOTAL_BYTES=0
for rel in "${REL_PATHS[@]}"; do
  src=""
  for uuid in "${INSTANCE_UUIDS[@]}"; do
    candidate="$VOLUMES_ROOT/${uuid}/${rel}"
    if [ -e "$candidate" ]; then
      src="$candidate"
      break
    fi
  done
  if [ -z "$src" ]; then
    log "path '$rel': no opted-in instance's volume has it yet — skipping (will retry next run)"
    continue
  fi
  SRC_OF["$rel"]="$src"
  READY_PATHS+=("$rel")
  size=$(du -sb "$src" 2>/dev/null | awk '{print $1}')
  TOTAL_BYTES=$(( TOTAL_BYTES + size ))
  log "path '$rel': source $src ($(( size / 1048576 ))M)"
done

if [ "${#READY_PATHS[@]}" -eq 0 ]; then
  log "none of the configured paths exist on any opted-in instance's volume yet — nothing to do"
  exit 0
fi

TMPFS_BYTES=$(_parse_bytes "$TMPFS_SIZE")
if [ "$TOTAL_BYTES" -ge "$TMPFS_BYTES" ]; then
  log "ERROR: total payload ($(( TOTAL_BYTES / 1048576 ))M) >= tmpfs size ($(( TMPFS_BYTES / 1048576 ))M)."
  log "  Raise SOULMASK_STATIC_RAMDISK_SIZE (env) before retrying."
  exit 1
fi
log "total payload: $(( TOTAL_BYTES / 1048576 ))M ; tmpfs size: $TMPFS_SIZE (headroom: $(( (TMPFS_BYTES - TOTAL_BYTES) / 1048576 ))M)"

# --- tmpfs -------------------------------------------------------------------
if ! mountpoint -q "$RAMDISK" 2>/dev/null; then
  log "creating tmpfs ($TMPFS_SIZE) at $RAMDISK"
  run mkdir -p "$RAMDISK"
  run mount -t tmpfs -o "size=${TMPFS_SIZE}" tmpfs "$RAMDISK"
else
  log "tmpfs already mounted at $RAMDISK — reusing, only missing paths are copied in"
fi

# --- populate the tmpfs (once per path, regardless of instance count) ------
for rel in "${READY_PATHS[@]}"; do
  ramdisk_copy="$RAMDISK/$rel"
  if [ -e "$ramdisk_copy" ]; then
    log "path '$rel': already present in $RAMDISK — reusing"
    continue
  fi
  log "copying '$rel' → $ramdisk_copy"
  run mkdir -p "$(dirname "$ramdisk_copy")"
  run cp -a "${SRC_OF[$rel]}" "$ramdisk_copy"
done
if [ $DRY_RUN -eq 0 ]; then
  # Preserve the container user's ownership (cp as root produces root:root,
  # which blocks in-container steam updates writing through the bind) —
  # every instance uses the same uid/gid, so any one's volume ownership works.
  OWNER=$(stat -c '%u:%g' "$VOLUMES_ROOT/${INSTANCE_UUIDS[0]}" 2>/dev/null || echo "")
  [ -n "$OWNER" ] && run chown -R "$OWNER" "$RAMDISK"
fi

# --- bind-mount into every opted-in instance that has the target ready -----
declare -a BOUND=()
for uuid in "${INSTANCE_UUIDS[@]}"; do
  for rel in "${READY_PATHS[@]}"; do
    target="$VOLUMES_ROOT/${uuid}/${rel}"
    ramdisk_copy="$RAMDISK/$rel"
    if [ ! -e "$target" ]; then
      log "instance $uuid: target '$rel' doesn't exist yet — skipping (will retry next run)"
      continue
    fi
    if mountpoint -q "$target" 2>/dev/null; then
      log "already bind-mounted: $target"
    else
      log "bind mounting $ramdisk_copy → $target"
      run mount --bind "$ramdisk_copy" "$target"
    fi
    BOUND+=("$target")
  done
done

if [ $DRY_RUN -eq 0 ]; then
  # Persist so teardown (ExecStop) can find every bind target without a
  # running container — same TARGET= convention as the pak ramdisk's state
  # file, grep-able rather than dot-sourced since there are many lines.
  {
    echo "# soulmask-static-ramdisk state (generated $(date -Is)) — do not edit"
    echo "RAMDISK=$RAMDISK"
    echo "TMPFS_SIZE=$TMPFS_SIZE"
    for t in "${BOUND[@]}"; do echo "TARGET=$t"; done
  } > "$STATE_FILE"
  log "done — ${#BOUND[@]} bind target(s) across ${#INSTANCE_UUIDS[@]} instance(s) now served from tmpfs-backed static content"
  for t in "${BOUND[@]}"; do
    log "verify: $(findmnt "$t" 2>/dev/null | tail -n1 || echo "$t (findmnt failed)")"
  done
fi
