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
# Two further sections are PER-INSTANCE (SMT-01, 2026-10-20), for paths the
# game WRITES and that must NOT be shared between instances (the UE log
# directory, WS/Saved/Logs):
#   [per-instance-compressible] — own, initially empty rw tmpfs per TMPFS=1
#                                 instance, swappable (zswap-eligible)
#   [per-instance-noswap]       — same, but mounted `noswap`: stays resident
# Nothing is copied in or back, the shared /mnt/soulmask_tmpfs is not involved,
# and the pages are charged to the memcg of the task that writes them (the
# game container's cgroup), NOT to the soulmask_tmpfs-* slices. Owner and mode
# are derived from the on-disk directory; size comes from
# SOULMASK_TMPFS_PER_INSTANCE_SIZE (defaulted in the unit file). Contents are
# lost on restart/reboot by design; /run/tmpfiles.d/soulmask-per-instance.conf
# (written here, removed by teardown) lets systemd-tmpfiles-clean prune them by
# age. See SOULMASK-TMPFS.md "Per-instance tmpfs".
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

RAMDISK="${SOULMASK_TMPFS_RAMDISK:-/mnt/soulmask_tmpfs}"
TMPFS_SIZE="${SOULMASK_TMPFS_SIZE:-5G}"   # ~2.6G payload (2026-07-29) + generous headroom
# 1 = remount read-only after population, blocking all writes.
# 0 (default) = leave read-write. Needed because Wings' pre-boot chown walk
# (Lchownat, unconditional, no already-correct-owner skip) fails EROFS on
# any instance bind-mounted onto its OWN directory (e.g. ROLE=main also
# TMPFS=1) while read-only -- discovered 2026-07-31. See SOULMASK-TMPFS.md
# for the tradeoff this accepts (steamcmd's own update writes aren't
# guaranteed atomic; mitigated by tmpfs headroom, not eliminated).
READONLY="${SOULMASK_TMPFS_READONLY:-0}"
STATE_FILE="${SOULMASK_TMPFS_STATE_FILE:-/run/soulmask_tmpfs.state}"
TMPFILES_FILE="${SOULMASK_TMPFILES_DIR:-/run/tmpfiles.d}/soulmask-per-instance.conf"
# Per-instance sections: deliberately NO default for size / age here — the
# unit file (soulmask_tmpfs.service) owns them; setup refuses without them.
PI_SIZE="${SOULMASK_TMPFS_PER_INSTANCE_SIZE:-}"
PI_MAX_AGE="${SOULMASK_TMPFS_PER_INSTANCE_MAX_AGE:-}"
PI_KEEP="${SOULMASK_TMPFS_PER_INSTANCE_KEEP:-}"   # names exempted from age pruning (may be empty)
INSTANCES_DIR="${GSTAMMTISCH_ETC:-/etc/gstammtisch}/instances.d"
PATHS_FILE="${GSTAMMTISCH_ETC:-/etc/gstammtisch}/soulmask_tmpfs-paths.conf"
VOLUMES_ROOT="${SOULMASK_VOLUMES_ROOT:-/var/lib/pterodactyl/volumes}"

# cgroup v2 slice paths for the two content types
CG_ROOT="/sys/fs/cgroup"
SLICE_INCOMPRESSIBLE="soulmask_tmpfs-ZSwapMax0.slice"
SLICE_COMPRESSIBLE="soulmask_tmpfs-ZSwapMax1.slice"

[ "${DRY_RUN:-0}" = "1" ] && DRY_RUN=1 || DRY_RUN=0   # env DRY_RUN=1 or --dry-run
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
declare -a PI_COMPRESSIBLE_PATHS=()
declare -a PI_NOSWAP_PATHS=()
declare -a BOUND=()      # shared bind targets (state file TARGET=)
declare -a PI_BOUND=()   # per-instance tmpfs targets (state file PI_TARGET=)
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
    \[per-instance-compressible\]) current_section="per-instance-compressible"; continue ;;
    \[per-instance-noswap\])       current_section="per-instance-noswap";       continue ;;
    \[*) log "WARNING: unknown section header '$line' — skipping"; continue ;;
  esac
  case "$current_section" in
    incompressible) INCOMPRESSIBLE_PATHS+=("$line") ;;
    compressible)   COMPRESSIBLE_PATHS+=("$line") ;;
    per-instance-compressible) PI_COMPRESSIBLE_PATHS+=("$line") ;;
    per-instance-noswap)       PI_NOSWAP_PATHS+=("$line") ;;
    *) log "WARNING: path '$line' before any section header — skipping" ;;
  esac
done < "$PATHS_FILE"

if [ "${#INCOMPRESSIBLE_PATHS[@]}" -eq 0 ] && [ "${#COMPRESSIBLE_PATHS[@]}" -eq 0 ] \
   && [ "${#PI_COMPRESSIBLE_PATHS[@]}" -eq 0 ] && [ "${#PI_NOSWAP_PATHS[@]}" -eq 0 ]; then
  log "no paths configured in $PATHS_FILE — nothing to do"
  exit 0
fi
log "incompressible paths (${#INCOMPRESSIBLE_PATHS[@]}): ${INCOMPRESSIBLE_PATHS[*]}"
log "compressible paths (${#COMPRESSIBLE_PATHS[@]}): ${COMPRESSIBLE_PATHS[*]}"
if [ "${#PI_COMPRESSIBLE_PATHS[@]}" -gt 0 ] || [ "${#PI_NOSWAP_PATHS[@]}" -gt 0 ]; then
  log "per-instance compressible paths (${#PI_COMPRESSIBLE_PATHS[@]}): ${PI_COMPRESSIBLE_PATHS[*]}"
  log "per-instance noswap paths (${#PI_NOSWAP_PATHS[@]}): ${PI_NOSWAP_PATHS[*]}"
fi

# --- per-instance tmpfs (SMT-01) -----------------------------------------------
# Plan = decide and validate EVERYTHING (derive options, refuse on a foreign
# mount) before the first mount of any kind, so a refusal leaves the host as
# it was. Apply = execute the plan. Plan entries (pipe separated):
#   action|uuid|tag|noswap|target|uid|gid|mode      action = mount | keep
declare -a PI_PLAN=()

write_state() {
  [ $DRY_RUN -eq 0 ] || return 0
  mkdir -p "$(dirname "$STATE_FILE")"
  {
    echo "# soulmask_tmpfs state (generated $(date -Is)) — do not edit"
    echo "RAMDISK=$RAMDISK"
    echo "TMPFS_SIZE=$TMPFS_SIZE"
    echo "READONLY=$READONLY"
    echo "SOURCE_UUID=${SOURCE_UUID:-}"
    for t in "${BOUND[@]}"; do echo "TARGET=$t"; done
    for t in "${PI_BOUND[@]}"; do echo "PI_TARGET=$t"; done
  } > "$STATE_FILE"
}

_pi_plan_one() {
  local uuid="$1" rel="$2" noswap="$3"
  local target="$VOLUMES_ROOT/$uuid/$rel" tag="soulmask-pi-${uuid:0:8}"
  local fs src opts st uid gid mode hidden
  if [ ! -e "$target" ]; then
    log "instance $uuid: per-instance target '$rel' doesn't exist yet — skipping (will retry next run; not created here, ownership would be a guess)"
    return 0
  fi
  if [ ! -d "$target" ]; then
    log "FATAL: per-instance target $target exists but is not a directory"
    exit 1
  fi
  if mountpoint -q "$target" 2>/dev/null; then
    fs=$(findmnt -no FSTYPE "$target" 2>/dev/null | tail -n1 || true)
    src=$(findmnt -no SOURCE "$target" 2>/dev/null | tail -n1 || true)
    opts=$(findmnt -no OPTIONS "$target" 2>/dev/null | tail -n1 || true)
    if [ "$fs" != "tmpfs" ] || [ "$src" != "$tag" ]; then
      log "FATAL: refusing per-instance mount: $target is already a mount point that is not ours (fstype='$fs' source='$src', expected tmpfs/$tag). Not stacking a mount on it; inspect with findmnt, resolve it, and re-run."
      exit 1
    fi
    if [ "$noswap" = "1" ] && ! [[ ",$opts," == *,noswap,* ]]; then
      log "FATAL: refusing: $target is our tmpfs but is NOT mounted noswap ($opts) while the config requires [per-instance-noswap]. Run soulmask_tmpfs-teardown.sh (or umount it) and re-run."
      exit 1
    fi
    log "instance $uuid: per-instance tmpfs already mounted at $target — keeping"
    PI_PLAN+=("keep|$uuid|$tag|$noswap|$target|-|-|-")
    return 0
  fi
  # Owner/mode are DERIVED from the existing on-disk directory, never literals.
  if ! st=$(stat -c '%u %g %a' "$target" 2>/dev/null); then
    log "FATAL: cannot stat $target to derive uid/gid/mode"
    exit 1
  fi
  read -r uid gid mode <<< "$st"
  if ! [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]{3,4}$ ]]; then
    log "FATAL: unexpected stat output '$st' for $target"
    exit 1
  fi
  hidden=$(du -sb "$target" 2>/dev/null | awk '{print $1}') || hidden=0
  log "NOTICE: $target holds ${hidden:-0} bytes of existing on-disk content; it stays hidden under the new tmpfs (not moved, not deleted — it reappears after unmount; inspect via a non-recursive 'mount --bind $VOLUMES_ROOT/$uuid /mnt/probe' while mounted, see SOULMASK-TMPFS.md)"
  PI_PLAN+=("mount|$uuid|$tag|$noswap|$target|$uid|$gid|$mode")
}

pi_plan() {
  local rel uuid seen=" "
  [ "${#PI_COMPRESSIBLE_PATHS[@]}" -gt 0 ] || [ "${#PI_NOSWAP_PATHS[@]}" -gt 0 ] || return 0
  if ! [[ "$PI_SIZE" =~ ^[0-9]+[kKmMgG%]?$ ]]; then
    log "FATAL: SOULMASK_TMPFS_PER_INSTANCE_SIZE='$PI_SIZE' is unset or malformed (expected e.g. 256M); it is set in soulmask_tmpfs.service, the script has no default"
    exit 1
  fi
  if ! [[ "$PI_MAX_AGE" =~ ^[0-9]+[a-z]+$ ]]; then
    log "FATAL: SOULMASK_TMPFS_PER_INSTANCE_MAX_AGE='$PI_MAX_AGE' is unset or malformed (expected e.g. 3d); it is set in soulmask_tmpfs.service, the script has no default"
    exit 1
  fi
  for rel in "${PI_COMPRESSIBLE_PATHS[@]}" "${PI_NOSWAP_PATHS[@]}"; do
    if [[ "$rel" == /* || "$rel" == *..* || "$rel" =~ [[:space:]%\|] ]]; then
      log "FATAL: per-instance path '$rel' must be relative, without '..', whitespace, '%' or '|'"
      exit 1
    fi
    if [[ "$seen" == *" $rel "* ]]; then
      log "FATAL: per-instance path '$rel' is listed twice (in both per-instance sections, or repeated)"
      exit 1
    fi
    seen+="$rel "
  done
  for uuid in "${INSTANCE_UUIDS[@]}"; do
    for rel in "${PI_COMPRESSIBLE_PATHS[@]}"; do _pi_plan_one "$uuid" "$rel" 0; done
    for rel in "${PI_NOSWAP_PATHS[@]}";       do _pi_plan_one "$uuid" "$rel" 1; done
  done
}

# /run/tmpfiles.d age rule so backups (UE renames WS.log -> WS-backup-<ts>.log
# at every start) cannot fill the tmpfs. `X` exempts the live files; the age is
# measured against the newest of atime/mtime/ctime/btime anyway.
pi_write_tmpfiles() {
  local t k content=""
  if [ "${#PI_BOUND[@]}" -eq 0 ]; then
    if [ -e "$TMPFILES_FILE" ]; then
      log "no per-instance tmpfs mounted — removing stale $TMPFILES_FILE"
      run rm -f "$TMPFILES_FILE"
    fi
    return 0
  fi
  content="# generated by soulmask_tmpfs-setup.sh — do not edit (removed by teardown)"$'\n'
  for t in "${PI_BOUND[@]}"; do
    content+="e $t - - - $PI_MAX_AGE"$'\n'
    for k in $PI_KEEP; do content+="X $t/$k"$'\n'; done
  done
  if [ $DRY_RUN -eq 1 ]; then
    echo "  DRY-RUN: write $TMPFILES_FILE:"
    printf '%s' "$content" | sed 's/^/  DRY-RUN:   /'
    return 0
  fi
  mkdir -p "$(dirname "$TMPFILES_FILE")"
  printf '%s' "$content" > "$TMPFILES_FILE.new"
  mv -f "$TMPFILES_FILE.new" "$TMPFILES_FILE"
  log "wrote $TMPFILES_FILE (age ${PI_MAX_AGE}, pruned by systemd-tmpfiles-clean.timer)"
}

pi_apply() {
  local entry action uuid tag noswap target uid gid mode opts
  for entry in "${PI_PLAN[@]}"; do
    IFS='|' read -r action uuid tag noswap target uid gid mode <<< "$entry"
    if [ "$action" = "keep" ]; then
      PI_BOUND+=("$target")
      continue
    fi
    opts="size=${PI_SIZE},uid=${uid},gid=${gid},mode=${mode},nosuid,nodev,noexec"
    [ "$noswap" = "1" ] && opts+=",noswap"
    log "mounting per-instance tmpfs ($opts) on $target"
    if ! run mount -t tmpfs -o "$opts" "$tag" "$target"; then
      if [ "$noswap" = "1" ]; then
        log "FATAL: mount of $target FAILED with noswap — the kernel rejected it. NOT falling back to a swappable mount; the path is configured under [per-instance-noswap]."
      else
        log "FATAL: mount of $target FAILED"
      fi
      write_state   # record what was mounted so far so teardown can unwind it
      exit 1
    fi
    PI_BOUND+=("$target")
  done
  pi_write_tmpfiles
}

# Early exit when the shared part has nothing to do: per-instance mounts are
# independent of it and must still happen.
finish_without_shared() {
  pi_apply
  if [ "${#PI_BOUND[@]}" -gt 0 ]; then
    write_state
    log "done — ${#PI_BOUND[@]} per-instance tmpfs mount(s); shared part had nothing to do"
  fi
  exit 0
}

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

# Decide + validate the per-instance mounts before anything is mounted.
pi_plan

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
  log "none of the configured paths exist on source instance $SOURCE_UUID's volume yet — nothing to do for the shared tmpfs"
  finish_without_shared
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

# --- remount read-only (unless SOULMASK_TMPFS_READONLY=0) ---------------------
if [ $DRY_RUN -eq 0 ]; then
  if [ "$READONLY" = "1" ]; then
    log "remounting tmpfs read-only"
    run mount -o remount,ro "$RAMDISK"
  else
    log "SOULMASK_TMPFS_READONLY=0 — leaving tmpfs read-write (accepted tradeoff, see SOULMASK-TMPFS.md)"
  fi
fi

# --- bind-mount into every opted-in instance ----------------------------------
ALL_READY=("${READY_INCOMPRESSIBLE[@]}" "${READY_COMPRESSIBLE[@]}")
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

# --- per-instance tmpfs mounts (own, empty, rw; nothing copied) ---------------
pi_apply

# --- state file + verification ------------------------------------------------
if [ $DRY_RUN -eq 0 ]; then
  write_state
  log "done — ${#BOUND[@]} bind target(s) across ${#INSTANCE_UUIDS[@]} instance(s) now served from read-only tmpfs; ${#PI_BOUND[@]} per-instance tmpfs mount(s)"
  for t in "${BOUND[@]}" "${PI_BOUND[@]}"; do
    log "verify: $(findmnt "$t" 2>/dev/null | tail -n1 || echo "$t (findmnt failed)")"
  done
fi
