#!/usr/bin/env bash
# Create one user's area on the btrfs copy-on-write filesystem (docs/BTRFS-COW.md section 3.2).
#   mdt-cow-init-user.sh [--dry-run] <mountpoint> <user> [extra-dir ...]
# Creates <mountpoint>/<user> (mode 0750, owned by <user> and its primary group) and <mountpoint>/<user>/shared,
# plus any extra directories given (single plain names, created below <mountpoint>/<user>). Idempotent. Sets no
# properties, creates no subvolumes: those are the tools' and the operator's per-directory decisions.
# Refuses a mountpoint that is not a btrfs mount point, and an existing <user> directory owned by someone else.
# Run as root on the host. Not installed by install.sh: run it by hand.
set -uo pipefail
log() { echo "[mdt-cow-init-user] $*" >&2; }
die() { log "ERROR: $*"; exit "${2:-1}"; }
usage() { echo "usage: mdt-cow-init-user.sh [--dry-run] <mountpoint> <user> [extra-dir ...]" >&2; exit 2; }

dry=0
if [ "${1:-}" = "--dry-run" ]; then dry=1; shift; fi
case "${1:-}" in ''|-h|--help) usage ;; esac
mnt="$1"; user="${2:-}"; shift; shift 2>/dev/null || true
[ -n "$user" ] || usage

[ "$(id -u)" = 0 ] || die "must run as root (the top level is root-owned)"
[ -d "$mnt" ] || die "$mnt is not a directory"
mountpoint -q -- "$mnt" || die "$mnt is not a mount point: refusing to create anything on the underlying filesystem"
fstype=$(stat -f -c %T -- "$mnt")
[ "$fstype" = "btrfs" ] || die "$mnt is '$fstype', not btrfs"
uid=$(id -u -- "$user" 2>/dev/null) || die "unknown user '$user'"
gid=$(id -g -- "$user")
[ "$uid" != 0 ] || die "refusing user area for uid 0: containers must not get a root-owned writable area"
case "$user" in */*|.*|'') die "unsafe user name '$user'" ;; esac

run() { if [ "$dry" = 1 ]; then log "dry-run: $*"; else "$@"; fi; }

area="$mnt/$user"
if [ -e "$area" ]; then
  [ -d "$area" ] && [ ! -L "$area" ] || die "$area exists and is not a plain directory"
  owner=$(stat -c %u -- "$area")
  [ "$owner" = "$uid" ] || die "$area is owned by uid $owner, not $user ($uid): not touching it"
else
  run install -d -o "$uid" -g "$gid" -m 0750 -- "$area" || die "cannot create $area"
fi

for rel in shared "$@"; do
  case "$rel" in */*|.*|'') die "extra directory '$rel' must be a single plain name (no '/', not hidden)" ;; esac
  [ -d "$area/$rel" ] && continue
  run install -d -o "$uid" -g "$gid" -m 0750 -- "$area/$rel" || die "cannot create $area/$rel"
done

log "ready: $area (btrfs, owner $user). Container side: MDT_COW_ROOT=$area"
