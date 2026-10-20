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
#
# RUNNING GAMES ARE REFUSED FIRST (SMT-01). A host umount succeeds even while a
# game runs: the container keeps its OWN copy of every mount in its mount
# namespace, so the game would go on using (and, for the per-instance log tmpfs,
# writing into) an orphan nobody can see. Before unmounting ANYTHING — shared
# binds included — this script therefore checks every instance whose targets are
# still mounted (docker ps, exact container name = instance uuid). If one is
# running, or cannot be determined, it refuses the WHOLE teardown, touches
# nothing and exits 1.
#
# Per-instance tmpfs mounts (PI_TARGET=) are found two ways: from the state file
# and by their `soulmask-pi-<uuid8>` mount source (findmnt), so a mount made by a
# setup that was killed before it wrote its state is still unwound. A per-instance
# target that is busy stays recorded and makes this script exit non-zero.
# Children are unmounted before parents.
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
  echo "in the state file, every per-instance tmpfs (PI_TARGET= or found by its"
  echo "soulmask-pi- mount source), then the shared tmpfs itself. Refuses (exit 1,"
  echo "nothing unmounted) while a game container of an affected instance runs."
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
VOLUMES_ROOT="${SOULMASK_VOLUMES_ROOT:-/var/lib/pterodactyl/volumes}"
RC=0
declare -a PI_BUSY=()
declare -A PI_DERIVED_OF=()
declare -a PI_TARGETS=()          # recorded + discovered per-instance mounts
declare -A PI_UUID_OF=()          # target -> instance uuid ("" = unknown)
declare -a BIND_TARGETS=()        # TARGET= lines of the state files

# Also clean up legacy state files if they still exist (post-migration)
LEGACY_PAK_STATE="/run/soulmask-pak-ramdisk.state"
LEGACY_STATIC_STATE="/run/soulmask-static-ramdisk.state"

log() { echo "[soulmask_tmpfs] $*"; }

# umount that honours DRY_RUN (prints, does nothing, reports success).
_umount() {
  if [ $DRY_RUN -eq 1 ]; then echo "  DRY-RUN: umount $*"; return 0; fi
  umount "$@" 2>/dev/null
}

# 0 = a container named exactly $1 is running, 1 = it is not, 2 = cannot tell.
# "Cannot tell" must never read as "not running": only a daemon that is itself
# stopped (e.g. at shutdown, after docker.service) counts as no container.
_container_running() {
  local out
  if out=$(docker ps --filter "name=^${1}\$" --format '{{.Names}}' 2>/dev/null); then
    printf '%s\n' "$out" | grep -qxF "$1" && return 0
    return 1
  fi
  systemctl is-active --quiet docker.service 2>/dev/null || return 1
  return 2
}

# The instance uuid of a path below VOLUMES_ROOT ("" if it is not below it).
_uuid_from_path() {
  local rest
  case "$1" in
    "$VOLUMES_ROOT"/*) rest="${1#"$VOLUMES_ROOT"/}"; echo "${rest%%/*}" ;;
    *) echo "" ;;
  esac
}

_in_list() { local n="$1" x; shift; for x in "$@"; do [ "$x" = "$n" ] && return 0; done; return 1; }

# Fill PI_TARGETS / PI_UUID_OF / PI_DERIVED_OF / BIND_TARGETS from a state file.
_read_state() {
  local f="$1" line rest p u
  local -A derived=()
  [ -f "$f" ] || return 0
  while IFS= read -r line; do
    case "$line" in
      TARGET=*)     [ -n "${line#*=}" ] && BIND_TARGETS+=("${line#*=}") ;;
      PI_TARGET=*)
        p="${line#*=}"
        if [ -n "$p" ] && ! _in_list "$p" "${PI_TARGETS[@]}"; then PI_TARGETS+=("$p"); fi
        ;;
      PI_DERIVED=*) rest="${line#PI_DERIVED=}"; p="${rest%% *}"; derived["$p"]="${rest#* }" ;;
    esac
  done < "$f"
  for p in "${!derived[@]}"; do
    PI_DERIVED_OF["$p"]="${derived[$p]}"
    u=""; read -r _ _ _ u <<< "${derived[$p]}"
    PI_UUID_OF["$p"]="$u"
  done
  return 0
}

# Add tagged mounts the state file does not know (a setup killed between the
# mount and its state write). The instance is taken from the path below
# VOLUMES_ROOT and must agree with the tag; otherwise it stays unknown (refused).
_discover_tagged() {
  local tg src u
  while read -r tg src; do
    [[ "$src" == soulmask-pi-* ]] || continue
    [ -n "$tg" ] || continue
    _in_list "$tg" "${PI_TARGETS[@]}" && continue
    u=$(_uuid_from_path "$tg")
    if [ -n "$u" ] && [ "soulmask-pi-${u:0:8}" != "$src" ]; then u=""; fi
    log "found tagged per-instance tmpfs not in the state file: $tg (source $src)"
    PI_TARGETS+=("$tg")
    PI_UUID_OF["$tg"]="$u"
  done < <(findmnt -rn -t tmpfs -o TARGET,SOURCE 2>/dev/null || true)
  return 0
}

_read_state "$STATE_FILE"
_read_state "$LEGACY_PAK_STATE"
_read_state "$LEGACY_STATIC_STATE"
for t in "${PI_TARGETS[@]}"; do
  # RECORDED targets without a recorded instance: fall back to the path
  if [ -z "${PI_UUID_OF[$t]:-}" ]; then PI_UUID_OF["$t"]=$(_uuid_from_path "$t"); fi
done
# discovered ones were already checked tag-versus-path; a disagreement stays unknown
_discover_tagged

# --- preflight: refuse BEFORE unmounting anything ------------------------------
_preflight() {
  local t u rc bad=0
  local -a uuids=()
  for t in "${PI_TARGETS[@]}"; do
    mountpoint -q "$t" 2>/dev/null || continue
    u="${PI_UUID_OF[$t]:-}"
    if [ -z "$u" ]; then
      log "ERROR: refusing teardown: cannot tell which instance owns the per-instance tmpfs $t (not recorded, and not below $VOLUMES_ROOT), so a running game cannot be ruled out"
      bad=1
    elif ! _in_list "$u" "${uuids[@]}"; then
      uuids+=("$u")
    fi
  done
  for t in "${BIND_TARGETS[@]}"; do
    mountpoint -q "$t" 2>/dev/null || continue
    u=$(_uuid_from_path "$t")
    if [ -n "$u" ] && ! _in_list "$u" "${uuids[@]}"; then uuids+=("$u"); fi
  done
  for u in "${uuids[@]}"; do
    rc=0; _container_running "$u" || rc=$?
    if [ $rc -eq 0 ]; then
      log "ERROR: refusing teardown: container $u is RUNNING. A host unmount would succeed, but the container keeps its own copy of the mounts (and would keep writing logs into an orphaned tmpfs). Stop the game server first."
      bad=1
    elif [ $rc -eq 2 ]; then
      log "ERROR: refusing teardown: cannot determine whether container $u is running (docker unreachable while docker.service is active)"
      bad=1
    fi
  done
  if [ $bad -ne 0 ]; then
    log "teardown REFUSED — nothing was unmounted, the state file is unchanged"
    exit 1
  fi
  return 0
}
_preflight

# Per-instance tmpfs mounts. Unlike a bind target, one that cannot be unmounted
# is NOT swallowed: it is reported, remembered, and fails the run.
_unmount_pi() {
  local t
  [ "${#PI_TARGETS[@]}" -gt 0 ] || return 0
  while IFS= read -r t; do
    [ -n "$t" ] || continue
    if ! mountpoint -q "$t" 2>/dev/null; then
      log "  (per-instance tmpfs already unmounted: $t)"
      continue
    fi
    if _umount "$t"; then
      log "unmounted per-instance tmpfs $t"
    else
      log "ERROR: per-instance tmpfs $t is busy (a host process still references it) — NOT unmounted; resolve and re-run"
      PI_BUSY+=("$t"); RC=1
    fi
  done < <(printf '%s\n' "${PI_TARGETS[@]}" | awk '{print length($0) "\t" $0}' | sort -rn -k1,1 | cut -f2-)
  return 0
}

_unbind_from_state() {
  local f="$1" line t
  [ -f "$f" ] || return 0
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
  return 0
}

# Unbind current unified targets
_unbind_from_state "$STATE_FILE"

# Unmount the per-instance tmpfs mounts
_unmount_pi

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
      for t in "${PI_BUSY[@]}"; do
        if [ -n "${PI_DERIVED_OF[$t]:-}" ]; then echo "PI_DERIVED=$t ${PI_DERIVED_OF[$t]}"; fi
      done
    } > "$STATE_FILE"
  fi
fi
if [ $RC -ne 0 ]; then
  log "teardown INCOMPLETE — ${#PI_BUSY[@]} per-instance tmpfs mount(s) busy"
else
  log "teardown complete"
fi
exit $RC
