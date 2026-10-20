#!/usr/bin/env bash
# soulmask_logs-prune.sh — keep the per-instance log tmpfs mounts (SMT-01)
# below their size cap. Run by soulmask-logs-prune.timer (every 10 min) and
# once at the end of soulmask_tmpfs-setup.sh.
#
# WHY this and not a systemd-tmpfiles age rule: an age rule cannot bound size
# (a 66 MB log plus three same-day backups already overflows a small tmpfs, and
# tmpfiles-clean runs daily), and with an age rule an idle live log is deleted
# under the running game. This is the single pruning mechanism.
#
# WHAT it touches — exactly and only:
#   * mounts recorded as PI_TARGET= in the state file that are, RIGHT NOW,
#     a tmpfs whose source is a soulmask-pi-<uuid8> tag (never a plain disk
#     directory: if the tmpfs is not mounted it does nothing);
#   * inside such a mount, regular files (not symlinks, not directories) in the
#     TOP LEVEL whose name matches the backup pattern the game produces:
#         WS-backup-<chars from [A-Za-z0-9._-]>.log
#   The live log WS.log, and everything else, is never deleted: it does not
#   match the pattern.
#
# POLICY (both values from /etc/gstammtisch/soulmask_tmpfs.env, validated here, no defaults):
#   1. keep only the newest SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS backups
#      (by mtime); delete the older ones;
#   2. then, while the tmpfs usage is above
#      SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT percent of its size, delete
#      the OLDEST remaining backup;
#   3. if usage is still above the mark with no backup left, say so loudly and
#      exit 1 (the live log alone is too big — the size cap needs raising).
#
# Usage: soulmask_logs-prune.sh [--dry-run|--help]   (env DRY_RUN=1 also)
set -uo pipefail

LIB="${LIB:-/usr/local/sbin/soulmask-instance-lib.sh}"
if [ -f "$LIB" ]; then
  # shellcheck disable=SC1090
  . "$LIB"
else
  echo "[soulmask_logs-prune] FATAL: $LIB not found"; exit 1
fi

STATE_FILE="${SOULMASK_TMPFS_STATE_FILE:-/run/soulmask_tmpfs.state}"
[ "${DRY_RUN:-0}" = "1" ] && DRY_RUN=1 || DRY_RUN=0
case "${1:-}" in
  --dry-run) DRY_RUN=1 ;;
  --help|-h)
    sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

log() { echo "[soulmask_logs-prune] $*"; }

if ! errs=$(soulmask_pi_validate_prune_env 2>&1 >/dev/null); then
  while IFS= read -r l; do log "FATAL: $l"; done <<< "$errs"
  exit 2
fi
KEEP="$SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS"
HIGH="$SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT"

if [ ! -f "$STATE_FILE" ]; then
  log "no state file $STATE_FILE — no per-instance tmpfs recorded, nothing to do"
  exit 0
fi

# used/size in bytes of the filesystem holding $1 (real df; the live log counts).
_usage() { df --output=used,size -B1 "$1" 2>/dev/null | tail -n1; }

_delete() {
  if [ $DRY_RUN -eq 1 ]; then echo "  DRY-RUN: rm -f -- $1"; return 0; fi
  rm -f -- "$1"
}

rc=0
while IFS= read -r line; do
  case "$line" in PI_TARGET=*) ;; *) continue ;; esac
  t="${line#PI_TARGET=}"
  [ -n "$t" ] || continue
  # Only ever prune a mount that is ours RIGHT NOW.
  if ! mountpoint -q "$t" 2>/dev/null; then
    log "$t: not a mount point — NOT pruning (would be the on-disk directory)"
    continue
  fi
  fs=$(findmnt -no FSTYPE "$t" 2>/dev/null | tail -n1)
  src=$(findmnt -no SOURCE "$t" 2>/dev/null | tail -n1)
  if [ "$fs" != "tmpfs" ] || [[ "$src" != soulmask-pi-* ]]; then
    log "ERROR: $t is a mount but not one of ours (fstype='$fs' source='$src') — NOT pruning"
    rc=1
    continue
  fi

  # Backups, newest first. NUL-delimited so any name is handled; the name is
  # then re-validated against the exact pattern.
  declare -a backups=()
  while IFS= read -r -d '' rec; do
    f="${rec#* }"
    base="${f##*/}"
    [[ "$base" =~ ^WS-backup-[A-Za-z0-9._-]+\.log$ ]] || continue
    backups+=("$f")
  done < <(find "$t" -maxdepth 1 -type f -name 'WS-backup-*.log' -printf '%T@ %p\0' 2>/dev/null | sort -z -rn)

  deleted=0
  # 1. count: keep the newest KEEP
  while [ "${#backups[@]}" -gt "$KEEP" ]; do
    victim="${backups[${#backups[@]}-1]}"
    log "$t: removing $victim (more than $KEEP backup(s) kept)"
    _delete "$victim"; deleted=$((deleted + 1))
    unset 'backups[${#backups[@]}-1]'
  done

  # 2. high water mark
  read -r used size <<< "$(_usage "$t")"
  if ! [[ "${used:-}" =~ ^[0-9]+$ && "${size:-}" =~ ^[1-9][0-9]*$ ]]; then
    log "ERROR: $t: cannot read usage from df ('${used:-}' '${size:-}')"
    rc=1
    continue
  fi
  while [ $(( used * 100 )) -gt $(( HIGH * size )) ] && [ "${#backups[@]}" -gt 0 ]; do
    victim="${backups[${#backups[@]}-1]}"
    log "$t: usage $(( used * 100 / size ))% > ${HIGH}% — removing oldest backup $victim"
    if [ $DRY_RUN -eq 1 ]; then
      used=$(( used - $(stat -c %s -- "$victim" 2>/dev/null || echo 0) ))   # simulate
      _delete "$victim"
    else
      _delete "$victim"
      read -r used size <<< "$(_usage "$t")"
    fi
    deleted=$((deleted + 1))
    unset 'backups[${#backups[@]}-1]'
  done
  if [ $(( used * 100 )) -gt $(( HIGH * size )) ]; then
    log "ERROR: $t: usage $(( used * 100 / size ))% is still above ${HIGH}% with no backup left to remove — the live log alone is too large; raise SOULMASK_TMPFS_PER_INSTANCE_SIZE"
    rc=1
  else
    log "$t: ok ($(( used * 100 / size ))% used, ${#backups[@]} backup(s) kept, $deleted removed)"
  fi
done < "$STATE_FILE"

# Standing alarm: the last setup run recorded per-instance errors (it exited 75,
# which the unit treats as success so that the shared tmpfs and the wings restart
# are not lost). Keep failing until a clean setup run rewrites the state.
pi_errors=$(sed -n 's/^PI_ERRORS=//p' "$STATE_FILE" | tail -n1)
if [[ "${pi_errors:-0}" =~ ^[0-9]+$ ]] && [ "${pi_errors:-0}" -gt 0 ]; then
  log "ERROR: the last soulmask_tmpfs setup run recorded $pi_errors per-instance error(s):"
  while IFS= read -r l; do log "  - ${l#PI_ERROR=}"; done < <(grep '^PI_ERROR=' "$STATE_FILE")
  log "  fix the cause and re-run soulmask_tmpfs-setup.sh (or restart soulmask_tmpfs.service with the game stopped)"
  rc=1
fi
exit $rc
