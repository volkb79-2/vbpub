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
# SOULMASK_TMPFS_PER_INSTANCE_SIZE (from /etc/gstammtisch/soulmask_tmpfs.env). Contents are
# lost on restart/reboot by design; soulmask_logs-prune.sh (run once at the end
# of setup and every 10 min by soulmask-logs-prune.timer) keeps the tmpfs below
# its cap by deleting old WS-backup-*.log files, never the live log. A
# per-instance problem never blocks the shared tmpfs; setup finishes it and
# then exits non-zero. See SOULMASK-TMPFS.md "Per-instance tmpfs".
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
# Per-instance sections: deliberately NO default for size / KEEP_BACKUPS /
# HIGH_WATER_PCT here — /etc/gstammtisch/soulmask_tmpfs.env owns them; setup
# (and soulmask_logs-prune.sh) refuse without them.
PI_SIZE="${SOULMASK_TMPFS_PER_INSTANCE_SIZE:-}"
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
# DESIGN (see SOULMASK-TMPFS.md "Per-instance tmpfs"):
#  * Plan = decide and validate everything before the first per-instance mount.
#    Apply = execute the plan.
#  * A per-instance problem (bad config, symlinked path, foreign mount, failed
#    mount) is recorded as a PI error and NEVER blocks the shared tmpfs: the game
#    needs the shared paks to start at all, whereas its logs just stay on disk.
#    The script finishes the shared part and then exits NON-ZERO.
#  * The reverse holds too: a failure of the shared part still applies the
#    per-instance mounts (EXIT trap), so the logs are not silently left on ext4.
#  * If a mount fails mid-apply, every mount made in THIS run is rolled back.
# Plan entries (pipe separated):  action|uuid|tag|noswap|target|uid|gid|mode   (action = mount | keep)
declare -a PI_PLAN=()
declare -a PI_MADE=()                  # per-instance mounts created by THIS run (for rollback)
declare -A PI_DERIVED=()               # target -> "uid gid mode uuid" (this run)
declare -A OLD_PI_DERIVED=()           # same, from the previous state file
declare -a OLD_TARGETS=() OLD_PI_TARGETS=()
PI_KIB=""
PI_ERRORS=0
PI_APPLIED=0
PRUNE_SCRIPT="${SOULMASK_PRUNE_SCRIPT:-$(dirname "${BASH_SOURCE[0]}")/soulmask_logs-prune.sh}"

declare -a PI_ERROR_MSGS=()
PI_EXIT_CODE=75   # EX_TEMPFAIL: shared part done, per-instance errors; unit has SuccessExitStatus=75
# Journal priority <3> (err) only when stdout is the journal (systemd sets
# JOURNAL_STREAM), so `journalctl -p err` shows it and a terminal stays clean.
pi_error() {
  if [ -n "${JOURNAL_STREAM:-}" ]; then echo "<3>[soulmask_tmpfs] ERROR: $*"; else log "ERROR: $*"; fi
  PI_ERRORS=$(( PI_ERRORS + 1 ))
  # The recorded copy becomes a `PI_ERROR=<msg>` state-file line that root scripts
  # parse: one line only, and no '=' (nothing in a message may look like KEY=value).
  local m="$*"
  m="${m//$'\n'/ }"; m="${m//$'\r'/ }"; m="${m//=/:}"
  PI_ERROR_MSGS+=("$m")
}

# Read the previous state file so a rewrite never orphans live mounts.
load_old_state() {
  local line rest p
  [ -f "$STATE_FILE" ] || return 0
  while IFS= read -r line; do
    case "$line" in
      TARGET=*)      OLD_TARGETS+=("${line#TARGET=}") ;;
      PI_TARGET=*)   OLD_PI_TARGETS+=("${line#PI_TARGET=}") ;;
      PI_DERIVED=*)  rest="${line#PI_DERIVED=}"; p="${rest%% *}"; OLD_PI_DERIVED["$p"]="${rest#* }" ;;
    esac
  done < "$STATE_FILE"
}

_in_list() { local n="$1" x; shift; for x in "$@"; do [ "$x" = "$n" ] && return 0; done; return 1; }

# State = this run's mounts MERGED with earlier recorded mounts that are still
# mounted (and, for per-instance ones, still carry our source tag): a mount the
# current plan does not mention (instance flipped to TMPFS=0, path removed from
# the conf, ...) must stay recorded so teardown can still unmount it.
write_state() {
  [ $DRY_RUN -eq 0 ] || return 0
  local -a bound=("${BOUND[@]}") pib=("${PI_BOUND[@]}")
  local t src d
  for t in "${OLD_TARGETS[@]}"; do
    _in_list "$t" "${bound[@]}" && continue
    mountpoint -q "$t" 2>/dev/null && bound+=("$t")
  done
  for t in "${OLD_PI_TARGETS[@]}"; do
    _in_list "$t" "${pib[@]}" && continue
    mountpoint -q "$t" 2>/dev/null || continue
    src=$(findmnt -no SOURCE "$t" 2>/dev/null | tail -n1 || true)
    [[ "$src" == soulmask-pi-* ]] && pib+=("$t")
  done
  mkdir -p "$(dirname "$STATE_FILE")"
  {
    echo "# soulmask_tmpfs state (generated $(date -Is)) — do not edit"
    echo "RAMDISK=$RAMDISK"
    echo "TMPFS_SIZE=$TMPFS_SIZE"
    echo "READONLY=$READONLY"
    echo "SOURCE_UUID=${SOURCE_UUID:-}"
    # Per-instance errors of THIS run (0 = clean). Read by the prune service
    # (fails while > 0) and by soulmask_tmpfs-toggle.sh status.
    echo "PI_ERRORS=$PI_ERRORS"
    for t in "${PI_ERROR_MSGS[@]:0:20}"; do echo "PI_ERROR=$t"; done
    for t in "${bound[@]}"; do echo "TARGET=$t"; done
    for t in "${pib[@]}"; do echo "PI_TARGET=$t"; done
    for t in "${pib[@]}"; do
      d="${PI_DERIVED[$t]:-${OLD_PI_DERIVED[$t]:-}}"
      if [ -n "$d" ]; then echo "PI_DERIVED=$t $d"; fi
    done
  } > "$STATE_FILE.new"
  # atomic: a reader (prune timer, toggle, teardown) or a kill mid-write never sees a truncated file
  mv -f "$STATE_FILE.new" "$STATE_FILE"
  return 0
}

# Containment (SMT-R-01). The path lives in a tree the container can write, so
# a symlink planted there must never redirect a ROOT mount onto a host
# directory. Refuse if the volume or ANY component of <volume>/<rel> is a
# symlink, or if the canonical path leaves the canonical volume (or the volume
# leaves VOLUMES_ROOT). Returns 0 = safe and exists, 1 = refused (error
# logged), 2 = does not exist yet (logged, skip).
_pi_check_path() {
  local uuid="$1" rel="$2" vol="$VOLUMES_ROOT/$1" cur comp real real_root real_vol
  local -a parts=()
  if [ ! -e "$vol" ] && [ ! -L "$vol" ]; then
    log "instance $uuid: volume $vol doesn't exist yet — skipping per-instance '$rel'"
    return 2
  fi
  if [ -L "$vol" ]; then
    pi_error "refusing per-instance mount: volume $vol is a symlink"
    return 1
  fi
  if ! real_root=$(realpath -e -- "$VOLUMES_ROOT" 2>/dev/null) || ! real_vol=$(realpath -e -- "$vol" 2>/dev/null) \
     || [[ "$real_vol" != "$real_root"/* ]]; then
    pi_error "refusing per-instance mount: volume $vol does not resolve inside $VOLUMES_ROOT"
    return 1
  fi
  IFS=/ read -ra parts <<< "$rel"
  cur="$vol"
  for comp in "${parts[@]}"; do
    [ -n "$comp" ] || continue
    cur="$cur/$comp"
    if [ -L "$cur" ]; then
      pi_error "refusing per-instance mount: $cur is a symlink (a container-writable tree must not redirect a root mount)"
      return 1
    fi
    if [ ! -e "$cur" ]; then
      log "instance $uuid: per-instance target '$rel' doesn't exist yet — skipping (will retry next run; not created here, ownership would be a guess)"
      return 2
    fi
  done
  if ! real=$(realpath -e -- "$cur" 2>/dev/null) || [[ "$real" != "$real_vol"/* ]]; then
    pi_error "refusing per-instance mount: $cur resolves outside volume $real_vol"
    return 1
  fi
  return 0
}

# Print the mount options missing from (or wrong in) $1 versus the expected set.
_pi_opts_missing() {
  local opts=",$1," kib="$2" uid="$3" gid="$4" mode="$5" noswap="$6" miss="" want m
  for want in rw nosuid nodev noexec "size=${kib}k" "uid=$uid" "gid=$gid"; do
    [[ "$opts" == *",$want,"* ]] || miss+=" $want"
  done
  m=$(printf '%s' "$opts" | sed -n 's/.*,mode=\([0-7]*\),.*/\1/p')
  if ! { [ -n "$m" ] && [ $(( 8#$m )) -eq $(( 8#$mode )) ]; }; then miss+=" mode=$mode"; fi
  if [ "$noswap" = "1" ]; then
    [[ "$opts" == *,noswap,* ]] || miss+=" noswap"
  else
    [[ "$opts" != *,noswap,* ]] || miss+=" (unexpected noswap)"
  fi
  echo "$miss"
}

_pi_plan_one() {
  local uuid="$1" rel="$2" noswap="$3" rc=0
  local target="$VOLUMES_ROOT/$uuid/$rel" tag="soulmask-pi-${uuid:0:8}"
  local fs src opts st uid gid mode hidden d miss
  _pi_check_path "$uuid" "$rel" || rc=$?
  [ $rc -eq 0 ] || return 0
  if [ ! -d "$target" ]; then
    pi_error "refusing per-instance mount: $target exists but is not a directory"
    return 0
  fi
  if mountpoint -q "$target" 2>/dev/null; then
    fs=$(findmnt -no FSTYPE "$target" 2>/dev/null | tail -n1 || true)
    src=$(findmnt -no SOURCE "$target" 2>/dev/null | tail -n1 || true)
    opts=$(findmnt -no OPTIONS "$target" 2>/dev/null | tail -n1 || true)
    if [ "$fs" != "tmpfs" ] || [ "$src" != "$tag" ]; then
      pi_error "refusing per-instance mount: $target is already a mount point that is not ours (fstype='$fs' source='$src', expected tmpfs/$tag). Not stacking a mount on it; resolve it and re-run."
      return 0
    fi
    # "Ours" needs more than a tag anyone can type: it must be recorded in the
    # state file (with the uid/gid/mode derived when it was mounted) AND carry
    # exactly the options this run would have mounted.
    d="${OLD_PI_DERIVED[$target]:-}"
    if [ -z "$d" ]; then
      pi_error "refusing per-instance mount: $target carries our tag but is not recorded in $STATE_FILE — not ours to keep. This happens if an earlier setup was killed between the mount and the state write. With the game STOPPED run soulmask_tmpfs-teardown.sh (it also finds tagged mounts that are not in the state file) or 'umount' it, then re-run."
      return 0
    fi
    read -r uid gid mode _ <<< "$d"
    miss=$(_pi_opts_missing "$opts" "$PI_KIB" "$uid" "$gid" "$mode" "$noswap")
    if [ -n "$miss" ]; then
      pi_error "refusing to keep $target: its mount options ($opts) lack/disagree on:$miss — the unit's configuration changed since it was mounted. Run soulmask_tmpfs-teardown.sh (with the game stopped) and re-run."
      return 0
    fi
    log "instance $uuid: per-instance tmpfs already mounted at $target with the expected options — keeping"
    PI_DERIVED["$target"]="$d"
    PI_PLAN+=("keep|$uuid|$tag|$noswap|$target|$uid|$gid|$mode")
    return 0
  fi
  # Owner/mode are DERIVED from the existing on-disk directory, never literals.
  if ! st=$(stat -c '%u %g %a' "$target" 2>/dev/null); then
    pi_error "cannot stat $target to derive uid/gid/mode"
    return 0
  fi
  read -r uid gid mode <<< "$st"
  if ! [[ "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]{3,4}$ ]]; then
    pi_error "unexpected stat output '$st' for $target"
    return 0
  fi
  hidden=$(du -sb "$target" 2>/dev/null | awk '{print $1}') || hidden=0
  log "NOTICE: $target holds ${hidden:-0} bytes of existing on-disk content; it stays hidden under the new tmpfs (not moved, not deleted — it reappears after unmount; inspect via a non-recursive 'mount --bind $VOLUMES_ROOT/$uuid /mnt/probe' while mounted, see SOULMASK-TMPFS.md)"
  PI_DERIVED["$target"]="$uid $gid $mode $uuid"
  PI_PLAN+=("mount|$uuid|$tag|$noswap|$target|$uid|$gid|$mode")
}

_overlaps() {  # 0 if a == b, or either is a path-prefix of the other
  local a="${1%/}" b="${2%/}"
  [[ "$a" == "$b" || "$a" == "$b"/* || "$b" == "$a"/* ]]
}

pi_plan() {
  local rel other uuid bad=0 re='[[:space:]%|*?[]' re_id='[[:space:]%|/]' re_root='[[:space:]%|]'
  local -a all_pi=("${PI_COMPRESSIBLE_PATHS[@]}" "${PI_NOSWAP_PATHS[@]}")
  local -a shared=("${INCOMPRESSIBLE_PATHS[@]}" "${COMPRESSIBLE_PATHS[@]}")
  local -a errs=()
  local i j e
  [ "${#all_pi[@]}" -gt 0 ] || return 0

  if ! PI_KIB=$(soulmask_pi_size_kib "$PI_SIZE"); then
    pi_error "SOULMASK_TMPFS_PER_INSTANCE_SIZE='$PI_SIZE' is unset or invalid: expected a positive integer with optional k/m/g suffix (e.g. 256M). 0 means UNLIMITED to tmpfs and a percentage is relative to RAM, so both are refused; set it in /etc/gstammtisch/soulmask_tmpfs.env (no script default)"
    bad=1
  fi
  mapfile -t errs < <(soulmask_pi_validate_prune_env 2>&1 >/dev/null)
  for e in "${errs[@]}"; do [ -n "$e" ] && { pi_error "$e"; bad=1; }; done
  if [[ "$VOLUMES_ROOT" =~ $re_root ]]; then
    pi_error "VOLUMES_ROOT '$VOLUMES_ROOT' contains whitespace, '%' or '|'"; bad=1
  fi
  for uuid in "${INSTANCE_UUIDS[@]}"; do
    if [[ "$uuid" =~ $re_id ]]; then pi_error "instance id '$uuid' contains whitespace, '%', '|' or '/'"; bad=1; fi
  done
  for rel in "${all_pi[@]}"; do
    if [[ "$rel" == /* || "$rel" == *..* || "$rel" =~ $re ]]; then
      pi_error "per-instance path '$rel' must be relative, without '..', whitespace, '%', '|' or glob characters"
      bad=1
    fi
  done
  # no two per-instance paths may nest or repeat, and none may overlap a shared path
  for (( i = 0; i < ${#all_pi[@]}; i++ )); do
    for (( j = i + 1; j < ${#all_pi[@]}; j++ )); do
      if _overlaps "${all_pi[i]}" "${all_pi[j]}"; then
        pi_error "per-instance paths '${all_pi[i]}' and '${all_pi[j]}' are the same or nested (a child mount would sit under its parent)"
        bad=1
      fi
    done
    for other in "${shared[@]}"; do
      if _overlaps "${all_pi[i]}" "$other"; then
        pi_error "per-instance path '${all_pi[i]}' overlaps shared path '$other'"
        bad=1
      fi
    done
  done
  [ $bad -eq 0 ] || { log "per-instance mounts DISABLED for this run (configuration errors above); the shared tmpfs continues"; return 0; }

  for uuid in "${INSTANCE_UUIDS[@]}"; do
    for rel in "${PI_COMPRESSIBLE_PATHS[@]}"; do _pi_plan_one "$uuid" "$rel" 0; done
    for rel in "${PI_NOSWAP_PATHS[@]}";       do _pi_plan_one "$uuid" "$rel" 1; done
  done
}

pi_apply() {
  local entry action uuid tag noswap target uid gid mode opts failed=0 rc i t
  local -a keep_after=()
  PI_APPLIED=1
  for entry in "${PI_PLAN[@]}"; do
    IFS='|' read -r action uuid tag noswap target uid gid mode <<< "$entry"
    if [ "$action" = "keep" ]; then
      PI_BOUND+=("$target")
      continue
    fi
    [ $failed -eq 0 ] || continue
    # Re-check immediately before mounting (TOCTOU: the tree is container-writable).
    rc=0; _pi_check_path "$uuid" "${target#"$VOLUMES_ROOT/$uuid/"}" || rc=$?
    if [ $rc -ne 0 ]; then
      [ $rc -eq 2 ] && pi_error "per-instance target $target vanished between planning and mounting"
      failed=1; continue
    fi
    opts="size=${PI_SIZE},uid=${uid},gid=${gid},mode=${mode},nosuid,nodev,noexec"
    [ "$noswap" = "1" ] && opts+=",noswap"
    log "mounting per-instance tmpfs ($opts) on $target"
    if ! run mount -t tmpfs -o "$opts" "$tag" "$target"; then
      if [ "$noswap" = "1" ]; then
        pi_error "mount of $target FAILED with noswap — the kernel rejected it. NOT falling back to a swappable mount; the path is configured under [per-instance-noswap]."
      else
        pi_error "mount of $target FAILED"
      fi
      failed=1; continue
    fi
    PI_BOUND+=("$target"); PI_MADE+=("$target")
    write_state   # record each mount right away: a kill before the end must not leave an unrecorded mount
  done
  if [ $failed -eq 1 ] && [ "${#PI_MADE[@]}" -gt 0 ]; then
    log "rolling back the ${#PI_MADE[@]} per-instance mount(s) made in this run"
    for (( i = ${#PI_MADE[@]} - 1; i >= 0; i-- )); do
      t="${PI_MADE[i]}"
      if run umount "$t"; then
        log "rolled back $t"
      else
        pi_error "could not roll back $t (stays mounted and recorded; run soulmask_tmpfs-teardown.sh)"
        keep_after+=("$t")
      fi
    done
    # PI_BOUND = kept mounts + whatever could not be rolled back
    local -a nb=()
    for t in "${PI_BOUND[@]}"; do
      if ! _in_list "$t" "${PI_MADE[@]}"; then
        nb+=("$t")                                   # kept from an earlier run
      elif _in_list "$t" "${keep_after[@]}"; then
        nb+=("$t")                                   # made now but could not be rolled back
      fi
    done
    PI_BOUND=("${nb[@]}")
  fi
}

# One prune pass right after setup (the timer repeats it every 10 minutes).
pi_prune_once() {
  [ "${#PI_BOUND[@]}" -gt 0 ] || return 0
  if [ $DRY_RUN -eq 1 ]; then
    echo "  DRY-RUN: $PRUNE_SCRIPT"
    return 0
  fi
  if ! "$PRUNE_SCRIPT"; then
    log "WARNING: $PRUNE_SCRIPT reported a problem (see above); the timer will retry"
  fi
}

# Early exit when the shared part has nothing to do: per-instance mounts are
# independent of it and must still happen.
finish_without_shared() {
  pi_apply
  write_state
  pi_prune_once
  if [ "${#PI_BOUND[@]}" -gt 0 ]; then
    log "done — ${#PI_BOUND[@]} per-instance tmpfs mount(s); shared part had nothing to do"
  fi
  exit 0
}

# Any exit: a per-instance error makes the run non-zero even after a successful
# shared part, and a shared-part failure still applies the per-instance mounts.
_on_exit() {
  local rc=$?
  trap - EXIT
  set +e
  if [ $PI_APPLIED -eq 0 ] && [ "${#PI_PLAN[@]}" -gt 0 ]; then
    log "shared part ended early (exit $rc) — still applying the per-instance mounts so logs do not silently stay on ext4"
    pi_apply
    write_state
  fi
  if [ $rc -eq 0 ] && [ $PI_ERRORS -gt 0 ]; then
    log "$PI_ERRORS per-instance error(s) recorded (see ERROR lines above and PI_ERROR= in $STATE_FILE); the shared part completed — exiting $PI_EXIT_CODE (unit: SuccessExitStatus=$PI_EXIT_CODE; the prune service now fails until a clean run)"
    rc=$PI_EXIT_CODE
  fi
  exit $rc
}
trap _on_exit EXIT

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

# Decide + validate the per-instance mounts before anything is mounted. A
# per-instance problem is recorded (PI_ERRORS) and does NOT stop the shared part.
load_old_state
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
pi_prune_once   # one prune pass now; soulmask-logs-prune.timer repeats it
