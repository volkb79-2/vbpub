#!/usr/bin/env bash
# soulmask_tmpfs-setup.sh — Populate a SINGLE shared tmpfs with ALL of
# Soulmask's byte-identical install content and bind-mount it into EVERY
# configured instance that opts in via TMPFS=1
# (/etc/gstammtisch/instances.d/<uuid>.env).
#
# Unifies the legacy soulmask-pak-ramdisk-setup.sh (pak files) and
# soulmask-static-ramdisk-setup.sh (Engine, WS/Binaries, bundled Steam libs)
# into one service/script/tmpfs, and adds the previously-uncovered paths
# that steamcmd touches during game updates (steamapps/, steamcmd/,
# Manifest_*.txt) so that during normal read-only operation NOTHING steam
# writes reaches the underlying disk.
#
# After population the tmpfs is remounted READ-ONLY (mount -o remount,ro).
# Steam/game updates are deliberately blocked: any write fails immediately
# with EROFS instead of partially corrupting state (see SOULMASK-TMPFS.md).
# Updates become a deliberate manual process:
#   1. Stop all containers (Wings panel)
#   2. systemctl stop soulmask_tmpfs.service
#   3. Start the ROLE=main server — steamcmd auto-updates on underlying disk
#   4. Stop main server
#   5. systemctl start soulmask_tmpfs.service (re-populates from main's
#      freshly-updated disk, remounts read-only)
#   6. Start all containers
#
# The paths to share are configured in /etc/gstammtisch/soulmask_tmpfs-paths.conf,
# split into two sections:
#   [incompressible] — pak files (zstd-incompressible, 1.006×)
#       → pages charged to soulmask_tmpfs-ZSwapMax0.slice (MemoryZSwapMax=0)
#   [compressible]   — binaries, libs, Steam runtime, steamcmd, manifests
#       → pages charged to soulmask_tmpfs-ZSwapMax1.slice (zswap-eligible)
#
# The setup script moves its own process between the two cgroup slices during
# the respective copy phases so the kernel charges pages to the correct slice.
#
# Source instance: the instance with ROLE=main in its config. Only ONE
# instance may have ROLE=main — asserted at startup. This guarantees the
# ramdisk is populated from the authoritative (most-recently-updated) copy.
#
# Invoked by soulmask_tmpfs.service (Before=docker.service) — runs BEFORE
# Docker/Wings starts, so an instance's volume (and the paths inside it) may
# not exist yet for a not-yet-installed server. Those are skipped with a log
# line and picked up on a later run once Wings has created them.
#
# Idempotent: an already-mounted tmpfs, already-populated paths, and
# already-bound targets are left alone; only what's missing is added.
# A read-only tmpfs is remounted rw for the copy phase, then ro again.
set -euo pipefail

# shellcheck source=/usr/local/sbin/soulmask-instance-lib.sh
LIB="${LIB:-/usr/local/sbin/soulmask-instance-lib.sh}"
if [ -f "$LIB" ]; then
  . "$LIB"
else
  echo "[soulmask_tmpfs] FATAL: $LIB not found (expected alongside this script)"; exit 1
fi

RAMDISK="/mnt/soulmask_tmpfs"
TMPFS_SIZE="${SOULMASK_TMPFS_SIZE:-5G}"   # ~2.6G payload (2026-07-29) + generous headroom
STATE_FILE="/run/soulmask_tmpfs.state"
INSTANCES_DIR="${GSTAMMTISCH_ETC:-/etc/gstammtisch}/instances.d"
PATHS_FILE="${GSTAMMTISCH_ETC:-/etc/gstammtisch}/soulmask_tmpfs-paths.conf"
VOLUMES_ROOT="${SOULMASK_VOLUMES_ROOT:-/var/lib/pterodactyl/volumes}"

# cgroup v2 slice paths for the two content types
CG_ROOT="/sys/fs/cgroup"
SLICE_INCOMPRESSIBLE="soulmask_tmpfs-ZSwapMax0.slice"
SLICE_COMPRESSIBLE="soulmask_tmpfs-ZSwapMax1.slice"

DRY_RUN=0
SHOW_HELP=0
case "${1:-}" in
  --help|-h)   SHOW_HELP=1 ;;
  --dry-run)   DRY_RUN=1 ;;
esac

if [ $SHOW_HELP -eq 1 ]; then
  echo "Usage: $(basename "$0") [--dry-run|--help]"
  echo ""
  echo "Populate a shared read-only tmpfs with Soulmask's byte-identical install"
  echo "content and bind-mount it into every TMPFS=1 instance."
  echo ""
  echo "Invoked by soulmask_tmpfs.service (Before=docker.service). Normally you"
  echo "should use 'systemctl start soulmask_tmpfs.service' or the toggle script:"
  echo "  soulmask_tmpfs-toggle.sh on|off|status"
  echo ""
  echo "Options:"
  echo "  --dry-run   Preview what would be done without making changes"
  echo "  --help      Show this message"
  echo ""
  echo "Config files:"
  echo "  /etc/gstammtisch/soulmask_tmpfs-paths.conf   — what to share"
  echo "  /etc/gstammtisch/instances.d/<uuid>.env      — TMPFS=1 to opt in"
  echo "  /etc/gstammtisch/instance-defaults.env        — TMPFS=0 default"
  echo ""
  echo "Manual update procedure: see SOULMASK-TMPFS.md"
  exit 0
fi

log() { echo "[soulmask_tmpfs] $*"; }
run() { if [ $DRY_RUN -eq 1 ]; then echo "  DRY-RUN: $*"; else "$@"; fi; }

_parse_bytes() {
  local v="$1"
  case "$v" in
    *G) echo $(( ${v%G} * 1073741824 )) ;;
    *M) echo $(( ${v%M} * 1048576 )) ;;
    *)  echo "$v" ;;
  esac
}

# Move the current process ($$) into the named cgroup slice so pages
# allocated by subsequent cp/mkdir are charged to that slice.
# Uses systemd to resolve the slice's cgroup path (handles nested slices
# correctly — systemd nests slices, e.g. soulmask_tmpfs-ZSwapMax0.slice
# lives under /sys/fs/cgroup/soulmask_tmpfs.slice/).
_move_to_slice() {
  local slice="$1" cg_path
  # Ensure the slice cgroup exists (systemctl start creates it)
  systemctl start "$slice" 2>/dev/null || true
  cg_path=$(systemctl show -P ControlGroup "$slice" 2>/dev/null) || true
  if [ -n "$cg_path" ] && [ -d "$CG_ROOT/$cg_path" ]; then
    if echo $$ > "$CG_ROOT/$cg_path/cgroup.procs" 2>/dev/null; then
      : # moved successfully
    else
      log "WARNING: could not move to cgroup slice $slice (permission denied or cgroup v2 required); pages will be charged to the service's default slice"
    fi
  else
    log "WARNING: could not resolve cgroup path for $slice (systemctl show returned '$cg_path'); pages charged to default slice"
  fi
}

# --- read configured paths ---------------------------------------------------
if [ ! -f "$PATHS_FILE" ]; then
  log "FATAL: $PATHS_FILE not found — expected sections [incompressible] and [compressible]"
  exit 1
fi

declare -a INCOMPRESSIBLE_PATHS=()
declare -a COMPRESSIBLE_PATHS=()
current_section=""
while IFS= read -r line; do
  # Strip inline comments and whitespace
  line="${line%%#*}"
  # Trim leading/trailing whitespace (spaces + tabs)
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue
  case "$line" in
    \[incompressible\]) current_section="incompressible"; continue ;;
    \[compressible\])   current_section="compressible";   continue ;;
    \[*) log "WARNING: unknown section header '$line' — skipping"; continue ;;
  esac
  case "$current_section" in
    incompressible) INCOMPRESSIBLE_PATHS+=("$line") ;;
    compressible)   COMPRESSIBLE_PATHS+=("$line") ;;
    *) log "WARNING: path '$line' before any section header — skipping" ;;
  esac
done < "$PATHS_FILE"

if [ "${#INCOMPRESSIBLE_PATHS[@]}" -eq 0 ] && [ "${#COMPRESSIBLE_PATHS[@]}" -eq 0 ]; then
  log "no paths configured in $PATHS_FILE — nothing to do"
  exit 0
fi
log "incompressible paths (${#INCOMPRESSIBLE_PATHS[@]}): ${INCOMPRESSIBLE_PATHS[*]}"
log "compressible paths (${#COMPRESSIBLE_PATHS[@]}): ${COMPRESSIBLE_PATHS[*]}"

# --- collect opted-in instances + find ROLE=main source -----------------------
declare -a INSTANCE_UUIDS=()
SOURCE_UUID=""
shopt -s nullglob
for f in "$INSTANCES_DIR"/*.env; do
  # Skip .example files (detect before basename — basename strips .env, not .example)
  [[ "$f" == *.example ]] && continue
  uuid=$(basename "$f" .env)
  soulmask_load_instance_env "$uuid"
  [ "$TMPFS" = "1" ] || continue
  INSTANCE_UUIDS+=("$uuid")
  if [ "$ROLE" = "main" ]; then
    if [ -n "$SOURCE_UUID" ]; then
      log "FATAL: multiple instances have ROLE=main ($SOURCE_UUID and $uuid). Only one ROLE=main is allowed — it is the authoritative source for the tmpfs."
      exit 1
    fi
    SOURCE_UUID="$uuid"
    log "source instance (ROLE=main): $uuid"
  fi
done
shopt -u nullglob

if [ "${#INSTANCE_UUIDS[@]}" -eq 0 ]; then
  log "no instance opts into TMPFS=1 — nothing to do"
  exit 0
fi
log "opted-in instances: ${INSTANCE_UUIDS[*]}"

if [ -z "$SOURCE_UUID" ]; then
  log "FATAL: no instance has ROLE=main — cannot determine authoritative source for tmpfs population."
  log "  Set ROLE=main in exactly one /etc/gstammtisch/instances.d/<uuid>.env."
  exit 1
fi

SOURCE_VOLUME="$VOLUMES_ROOT/$SOURCE_UUID"
if [ ! -d "$SOURCE_VOLUME" ]; then
  log "FATAL: source instance $SOURCE_UUID volume not found at $SOURCE_VOLUME"
  exit 1
fi

# --- resolve every path against the source volume, size-check -----------------
declare -A SRC_OF=()      # rel path -> source path on disk
declare -a READY_INCOMPRESSIBLE=()
declare -a READY_COMPRESSIBLE=()
TOTAL_BYTES=0

_resolve_paths() {
  local -n _paths="$1" _ready="$2"
  local rel src size
  for rel in "${_paths[@]}"; do
    src="$SOURCE_VOLUME/$rel"
    if [ ! -e "$src" ]; then
      log "path '$rel': source instance $SOURCE_UUID doesn't have it yet — skipping (will retry next run)"
      continue
    fi
    SRC_OF["$rel"]="$src"
    _ready+=("$rel")
    size=$(du -sb "$src" 2>/dev/null | awk '{print $1}') || size=0
    TOTAL_BYTES=$(( TOTAL_BYTES + size ))
    log "path '$rel': source $src ($(( size / 1048576 ))M)"
  done
}
_resolve_paths INCOMPRESSIBLE_PATHS READY_INCOMPRESSIBLE
_resolve_paths COMPRESSIBLE_PATHS   READY_COMPRESSIBLE

if [ "${#READY_INCOMPRESSIBLE[@]}" -eq 0 ] && [ "${#READY_COMPRESSIBLE[@]}" -eq 0 ]; then
  log "none of the configured paths exist on source instance $SOURCE_UUID's volume yet — nothing to do"
  exit 0
fi

TMPFS_BYTES=$(_parse_bytes "$TMPFS_SIZE")
if [ "$TOTAL_BYTES" -ge "$TMPFS_BYTES" ]; then
  log "ERROR: total payload ($(( TOTAL_BYTES / 1048576 ))M) >= tmpfs size ($(( TMPFS_BYTES / 1048576 ))M)."
  log "  Raise SOULMASK_TMPFS_SIZE (env) before retrying."
  exit 1
fi
log "total payload: $(( TOTAL_BYTES / 1048576 ))M ; tmpfs size: $TMPFS_SIZE (headroom: $(( (TMPFS_BYTES - TOTAL_BYTES) / 1048576 ))M)"

# --- tmpfs -------------------------------------------------------------------
if mountpoint -q "$RAMDISK" 2>/dev/null; then
  # Check if currently read-only; if so, remount rw for the copy phase
  if findmnt -no OPTIONS "$RAMDISK" 2>/dev/null | grep -q '\<ro\>'; then
    log "tmpfs is read-only — remounting rw for copy phase"
    run mount -o remount,rw "$RAMDISK"
  else
    log "tmpfs already mounted rw at $RAMDISK — reusing, only missing paths are copied in"
  fi
else
  log "creating tmpfs ($TMPFS_SIZE) at $RAMDISK"
  run mkdir -p "$RAMDISK"
  run mount -t tmpfs -o "size=${TMPFS_SIZE}" tmpfs "$RAMDISK"
fi

# --- populate: incompressible phase (ZSwapMax0 slice) ------------------------
_copy_paths() {
  local -n _paths="$1"
  local rel ramdisk_copy
  for rel in "${_paths[@]}"; do
    ramdisk_copy="$RAMDISK/$rel"
    if [ -e "$ramdisk_copy" ]; then
      log "path '$rel': already present in $RAMDISK — reusing"
      continue
    fi
    log "copying '$rel' → $ramdisk_copy"
    run mkdir -p "$(dirname "$ramdisk_copy")"
    run cp -a "${SRC_OF[$rel]}" "$ramdisk_copy"
  done
}

log "--- incompressible phase (slice $SLICE_INCOMPRESSIBLE) ---"
_move_to_slice "$SLICE_INCOMPRESSIBLE"
_copy_paths READY_INCOMPRESSIBLE

log "--- compressible phase (slice $SLICE_COMPRESSIBLE) ---"
_move_to_slice "$SLICE_COMPRESSIBLE"
_copy_paths READY_COMPRESSIBLE

# --- set ownership ------------------------------------------------------------
if [ $DRY_RUN -eq 0 ]; then
  OWNER=$(stat -c '%u:%g' "$SOURCE_VOLUME" 2>/dev/null || echo "")
  if [ -n "$OWNER" ]; then
    log "setting ownership $OWNER on $RAMDISK"
    run chown -R "$OWNER" "$RAMDISK"
  fi
fi

# --- remount read-only --------------------------------------------------------
if [ $DRY_RUN -eq 0 ]; then
  log "remounting tmpfs read-only"
  run mount -o remount,ro "$RAMDISK"
fi

# --- bind-mount into every opted-in instance ----------------------------------
ALL_READY=("${READY_INCOMPRESSIBLE[@]}" "${READY_COMPRESSIBLE[@]}")
declare -a BOUND=()
for uuid in "${INSTANCE_UUIDS[@]}"; do
  for rel in "${ALL_READY[@]}"; do
    target="$VOLUMES_ROOT/${uuid}/${rel}"
    ramdisk_copy="$RAMDISK/$rel"
    if [ ! -e "$target" ]; then
      log "instance $uuid: target '$rel' doesn't exist yet — skipping (will retry next run)"
      continue
    fi
    if mountpoint -q "$target" 2>/dev/null; then
      # Already bind-mounted — but if it's pointing at the OLD (legacy) ramdisk,
      # unmount and rebind to the new unified one. Detect by checking if the
      # mount source contains 'soulmask-paks' or 'soulmask-static'.
      existing_src=$(findmnt -no SOURCE "$target" 2>/dev/null || true)
      if echo "$existing_src" | grep -qE 'soulmask-paks|soulmask-static'; then
        log "instance $uuid: $rel is bind-mounted from legacy ramdisk ($existing_src) — remounting to unified tmpfs"
        run umount "$target"
      else
        log "already bind-mounted: $target"
        BOUND+=("$target")
        continue
      fi
    fi
    log "bind mounting $ramdisk_copy → $target"
    run mount --bind "$ramdisk_copy" "$target"
    BOUND+=("$target")
  done
done

# --- state file + verification ------------------------------------------------
if [ $DRY_RUN -eq 0 ]; then
  {
    echo "# soulmask_tmpfs state (generated $(date -Is)) — do not edit"
    echo "RAMDISK=$RAMDISK"
    echo "TMPFS_SIZE=$TMPFS_SIZE"
    echo "SOURCE_UUID=$SOURCE_UUID"
    for t in "${BOUND[@]}"; do echo "TARGET=$t"; done
  } > "$STATE_FILE"
  log "done — ${#BOUND[@]} bind target(s) across ${#INSTANCE_UUIDS[@]} instance(s) now served from read-only tmpfs"
  for t in "${BOUND[@]}"; do
    log "verify: $(findmnt "$t" 2>/dev/null | tail -n1 || echo "$t (findmnt failed)")"
  done
fi
