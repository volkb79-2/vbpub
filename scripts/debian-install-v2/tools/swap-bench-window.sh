#!/usr/bin/env bash
# swap-bench-window.sh - free ONE managed swap partition for an iobench raw run, then give it back.
#
#   swap-bench-window.sh begin <partition> --expect-partuuid <uuid> --expect-size <bytes> [--expect-server-id N] [--commit]
#   swap-bench-window.sh end   <partition> --expect-partuuid <uuid> --expect-size <bytes> [--expect-server-id N] [--commit]
#
# --expect-partuuid / --expect-size are mandatory in both modes. The partition is identified by its
# GPT PARTUUID plus the filesystem LABEL written by mkswap; managed swap partitions do not carry a GPT
# PARTLABEL. --expect-server-id is optional; when given, /etc/vbpub/server-id must exist and match.
# Without --commit no device or fstab changes are made; end may create its lock file.
#
# begin: lock -> guards -> record PARTLABEL snapshot/label/UUID/PARTUUID/size -> swapoff (bounded by a timeout)
#        -> verify identity -> wipefs -a on this one device -> verify clean.
# end:   lock -> verify identity unchanged -> no live swapoff and nothing holds the device
#        -> clear the iobench marker MiB -> mkswap -L <label> -U <uuid>
#        -> swapon the selected device with its fstab row's options -> verify -> archive state.
#
# The script never edits /etc/fstab: swap is registered by PARTUUID, which a re-mkswap does not change.
# It requires the Linux swap GPT type and a vbpub-swap<N> (or vbpub-swap-pre<N>) filesystem LABEL.
# It never frees the last swap and allows one open window at a time.
# Needs util-linux (blkid findmnt flock lsblk mkswap swapon swapoff wipefs), coreutils,
# gawk/grep, and psmisc (fuser).
set -Eeuo pipefail
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

usage() { echo "usage: $0 begin|end /dev/vdaN --expect-partuuid <uuid> --expect-size <bytes> [--expect-server-id N] [--commit]" >&2; exit 2; }
die() { echo "refusing: $1" >&2; exit "${2:-1}"; }

[ $# -ge 2 ] || usage
mode=$1
dev=$2
shift 2
expect_id=""
have_id=0
expect_partuuid=""
expect_size=""
commit=0
while [ $# -gt 0 ]; do
  case $1 in
    --expect-server-id) [ $# -ge 2 ] || usage; expect_id=$2; have_id=1; shift 2 ;;
    --expect-partuuid) [ $# -ge 2 ] || usage; expect_partuuid=$2; shift 2 ;;
    --expect-size) [ $# -ge 2 ] || usage; expect_size=$2; shift 2 ;;
    --commit) commit=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ "$mode" = begin ] || [ "$mode" = end ] || usage

UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
LABEL_RE='^vbpub-swap(-pre)?[0-9]+$'
GPT_SWAP=0657fd6d-a4ab-43c4-84e5-0933c84b4f4f
MIN_MARGIN_KIB=1048576      # the margin can only be raised (SWAPBENCH_MARGIN_KIB), never lowered
MAX_USED_KIB=2097152        # hard cap on pages in use: swapoff faults them all back into RAM; edit + re-review to raise

[[ $dev =~ ^/dev/[a-z]+[0-9]*p?[0-9]+$ ]] || die "$dev is not a partition path" 2
[ "$(id -u)" = 0 ] || die "must run as root (uid 0)" 2
expect_partuuid=${expect_partuuid,,}
[[ $expect_partuuid =~ $UUID_RE ]] || die "--expect-partuuid is required and must be a plain UUID (copy it from the layout check)" 2
[[ $expect_size =~ ^[0-9]{1,15}$ ]] || die "--expect-size is required and must be the partition size in bytes (copy it from the layout check)" 2
if [ "$have_id" = 1 ]; then
  [[ $expect_id =~ ^[0-9]+$ ]] || die "--expect-server-id must be a number" 2
  [ -r /etc/vbpub/server-id ] || die "/etc/vbpub/server-id does not exist yet: provision-runner.sh writes it; never create it by hand; before provisioning omit --expect-server-id" 2
  [ "$(cat /etc/vbpub/server-id)" = "$expect_id" ] || die "/etc/vbpub/server-id does not equal --expect-server-id" 2
fi
SWAPOFF_TIMEOUT=${SWAPBENCH_SWAPOFF_TIMEOUT:-900}
[[ $SWAPOFF_TIMEOUT =~ ^[0-9]{1,4}$ ]] && [ "$SWAPOFF_TIMEOUT" -ge 1 ] && [ "$SWAPOFF_TIMEOUT" -le 3600 ] \
  || die "SWAPBENCH_SWAPOFF_TIMEOUT must be 1..3600 seconds" 2
margin=$MIN_MARGIN_KIB
if [ -n "${SWAPBENCH_MARGIN_KIB:-}" ]; then
  [[ $SWAPBENCH_MARGIN_KIB =~ ^[0-9]{1,12}$ ]] || die "SWAPBENCH_MARGIN_KIB must be an integer >= $MIN_MARGIN_KIB (it can only raise the margin)" 2
  margin=$((10#$SWAPBENCH_MARGIN_KIB))        # 10#: a leading zero must not be read as octal
  [ "$margin" -ge "$MIN_MARGIN_KIB" ] || die "SWAPBENCH_MARGIN_KIB must be an integer >= $MIN_MARGIN_KIB (it can only raise the margin)" 2
fi
# fuser is needed by `end`; begin requires it too so a window is never opened that cannot be closed.
command -v fuser >/dev/null || die "fuser (package psmisc) is not installed; the check that nothing holds the device cannot run" 2
# Every external tool is required up front: a missing findmnt would otherwise read as "not mounted" (fail-open).
for tool in flock findmnt lsblk blkid blockdev wipefs mkswap swapon swapoff timeout awk grep dd sync install \
    cat tr ls basename id date mv rm; do
  command -v "$tool" >/dev/null || die "$tool is not installed; refusing to run without it" 2
done

run() { echo "+ $*"; if [ "$commit" = 1 ]; then "$@"; fi; }
state_dir=/var/lib/vbpub/swapbench
state=$state_dir/$(basename "$dev").env
active() { awk -v d="$dev" 'NR>1 && $1==d {f=1} END{exit !f}' /proc/swaps; }
# The kernel keeps a device listed while swapoff is still running. Treat every live non-zombie
# swapoff as relevant, regardless of option form, operand order, or by-* device spelling.
swapoff_running() {
  local d
  for d in /proc/[0-9]*; do
    [ "$(cat "$d/comm" 2>/dev/null || true)" = swapoff ] || continue
    [ -n "$(tr -d '\0' < "$d/cmdline" 2>/dev/null || true)" ] && return 0
  done
  return 1
}
probe() { blkid -p -s "$1" -o value "$dev" 2>/dev/null || true; }
partuuid_of() {
  local p
  p=$(blkid -p -s PART_ENTRY_UUID -o value "$dev" 2>/dev/null || true)
  echo "${p,,}"
}
size_of() { blockdev --getsize64 "$dev" 2>/dev/null || true; }
fstab_swap_options() {
  awk -v source="PARTUUID=$S_PARTUUID" '
    $1 !~ /^#/ && $1 == source {
      matches++
      if (NF < 4 || $2 != "none" || $3 != "swap" || $4 == "") bad=1
      else options=$4
    }
    END {
      if (bad || matches != 1) exit 1
      print options
    }
  ' /etc/fstab
}
verify_restored() {
  local want_pri got_pri
  if ! { [ "$(partuuid_of)" = "$S_PARTUUID" ] && [ "$(size_of)" = "$S_SIZE" ] \
      && [ "$(lsblk -dno PARTLABEL "$dev" 2>/dev/null || true)" = "$S_PARTLABEL" ] \
      && [ "$(probe TYPE)" = swap ] && [ "$(probe UUID)" = "$S_UUID" ] \
      && [ "$(probe LABEL)" = "$S_LABEL" ] && active; }; then
    echo "VERIFY FAILED for $dev: PARTUUID/size/PARTLABEL/swap identity/active state did not match the saved state (state kept)" >&2
    return 1
  fi
  want_pri=$(awk -v opts="$fstab_opts" \
    'BEGIN {n=split(opts,o,","); for(i=1;i<=n;i++) if (o[i] ~ /^pri=[0-9]+$/) {sub(/^pri=/,"",o[i]); print o[i]}}')
  got_pri=$(awk -v d="$dev" 'NR>1 && $1==d {print $5}' /proc/swaps)
  if [ -n "$want_pri" ] && [ "$want_pri" != "$got_pri" ]; then
    echo "VERIFY FAILED for $dev: active priority is '$got_pri', fstab says pri=$want_pri (state kept)" >&2
    return 1
  fi
  RESTORE_GOT_PRI=$got_pri
}
# One lock for the whole tool. Begin's own swapoff child inherits the lock; end also scans /proc
# so a foreign swapoff makes it refuse even though it does not hold this lock.
take_lock() {
  install -d -m 0700 "$state_dir"
  exec 9>"$state_dir/.lock"
  flock -n 9 || die "another swap-bench-window run, or a swapoff begun by an interrupted run, still holds $state_dir/.lock"
}
# Strict reader for the state file: six known keys, one per line, no shell evaluation, values re-validated.
read_state() {
  local line k v seen=" "
  S_DEV=""; S_PARTUUID=""; S_UUID=""; S_LABEL=""; S_SIZE=""; S_PARTLABEL=""
  [ -f "$state" ] && [ ! -L "$state" ] && [ -r "$state" ] || die "no readable state file $state (no open window)"
  while IFS= read -r line || [ -n "$line" ]; do
    [[ $line =~ ^(DEV|PARTUUID|UUID|LABEL|SIZE|PARTLABEL)=([^[:cntrl:]]*)$ ]] || die "malformed line in $state"
    k=${BASH_REMATCH[1]}
    v=${BASH_REMATCH[2]}
    case $seen in *" $k "*) die "duplicate key $k in $state" ;; esac
    seen="$seen$k "
    printf -v "S_$k" '%s' "$v"
  done < "$state"
  [[ $S_DEV =~ ^/dev/[a-z]+[0-9]*p?[0-9]+$ ]] && [[ $S_PARTUUID =~ $UUID_RE ]] && [[ $S_UUID =~ $UUID_RE ]] \
    && [[ $S_LABEL =~ $LABEL_RE ]] && [[ -z $S_PARTLABEL || $S_PARTLABEL =~ ^[A-Za-z0-9_.-]+$ ]] \
    && [[ $S_SIZE =~ ^[0-9]{1,15}$ ]] || die "state file $state is incomplete or holds invalid values"
  [ "$S_DEV" = "$dev" ] || die "state is for another device (not $dev)"
}
identity_before_wipe() {
  local current_partlabel
  current_partlabel=$(lsblk -dno PARTLABEL "$dev" 2>/dev/null || true)
  [ "$(partuuid_of)" = "$1" ] && [ "$(size_of)" = "$2" ] && [ "$(probe UUID)" = "$3" ] \
    && [ "$(probe LABEL)" = "$4" ] && [ "$current_partlabel" = "$5" ]
}

case $mode in
begin)
  [ "$commit" = 0 ] || take_lock
  if compgen -G "$state_dir/*.env" >/dev/null; then
    die "a window is already open ($(printf '%s ' "$state_dir"/*.env)); run end first (one window at a time)"
  fi
  [ "$(lsblk -dno TYPE "$dev" 2>/dev/null || true)" = part ] || die "not a partition"
  partlabel=$(lsblk -dno PARTLABEL "$dev" 2>/dev/null || true)
  [[ $partlabel =~ ^[A-Za-z0-9_.-]*$ ]] || die "cannot read a valid PARTLABEL snapshot (empty is allowed)"
  ptype=$(lsblk -dno PARTTYPE "$dev" 2>/dev/null | tr A-F a-f || true)
  [ "$ptype" = "$GPT_SWAP" ] || die "GPT type $ptype is not Linux swap"
  [ "$(probe TYPE)" = swap ] || die "$dev has no swap signature (already freed?)"
  partuuid=$(partuuid_of)
  uuid=$(probe UUID)
  label=$(probe LABEL)
  size=$(size_of)
  [[ $partuuid =~ $UUID_RE ]] || { printf 'refusing: PARTUUID %q is not a plain UUID\n' "$partuuid" >&2; exit 1; }
  [[ $uuid =~ $UUID_RE ]] || { printf 'refusing: swap UUID %q is not a plain lower-case UUID\n' "$uuid" >&2; exit 1; }
  [[ $label =~ $LABEL_RE ]] || { printf 'refusing: swap LABEL %q is not vbpub-swap<N> or vbpub-swap-pre<N>\n' "$label" >&2; exit 1; }
  [[ $size =~ ^[0-9]{1,15}$ ]] || die "cannot read the partition size"
  [ "$partuuid" = "$expect_partuuid" ] || die "PARTUUID $partuuid of $dev does not equal --expect-partuuid $expect_partuuid" 2
  [ "$size" = "$expect_size" ] || die "size $size of $dev does not equal --expect-size $expect_size" 2
  [ "$(grep -c "^PARTUUID=${partuuid}[[:space:]]\+none[[:space:]]\+swap[[:space:]]" /etc/fstab || true)" = 1 ] \
    || die "/etc/fstab does not hold exactly one PARTUUID=$partuuid swap line"
  [ -z "$(findmnt -rn -S "$dev")" ] || die "mounted"
  holders="/sys/class/block/$(basename "$dev")/holders"
  [ -d "$holders" ] || die "cannot read $holders"
  [ -z "$(ls "$holders")" ] || die "has holders"
  active || die "$dev is not swap-active (unexpected state)"
  used_kib=$(awk -v d="$dev" 'NR>1 && $1==d {print $4}' /proc/swaps)
  [[ $used_kib =~ ^[0-9]+$ ]] || die "cannot read the swap usage of $dev"
  # only other swap PARTITIONS count as room (zram/swapfiles are not assumed to absorb the pages)
  other_free_kib=$(awk -v d="$dev" 'NR>1 && $1!=d && $2=="partition" {f+=$3-$4} END{print f+0}' /proc/swaps)
  avail_kib=$(awk '$1=="MemAvailable:"{print $2}' /proc/meminfo)
  [[ $avail_kib =~ ^[0-9]+$ ]] || die "cannot read MemAvailable"
  [ "$used_kib" -le "$MAX_USED_KIB" ] || die "${used_kib} KiB in use on $dev exceeds the hard cap ${MAX_USED_KIB} KiB (swapoff would fault them all back into RAM)"
  [ "$other_free_kib" -gt $(( used_kib + margin )) ] || die "remaining swap cannot absorb ${used_kib} KiB (free ${other_free_kib} KiB) or no other swap partition"
  [ "$avail_kib" -gt $(( used_kib + margin )) ] || die "MemAvailable ${avail_kib} KiB too small to take ${used_kib} KiB"
  echo "checks OK: $dev PARTLABEL=${partlabel:-none} PARTUUID=$partuuid UUID=$uuid LABEL=$label size=$size used=${used_kib}KiB"
  state_content=$(printf 'DEV=%s\nPARTUUID=%s\nUUID=%s\nLABEL=%s\nSIZE=%s\nPARTLABEL=%s\n' \
    "$dev" "$partuuid" "$uuid" "$label" "$size" "$partlabel")
  swapoff_cmd=(timeout --signal=INT --kill-after=60 "$SWAPOFF_TIMEOUT" swapoff "$dev")
  if [ "$commit" = 1 ]; then
    # Exclusive create is durable before swapoff. A failed create never removes a pre-existing record.
    if ! ( set -C; printf '%s\n' "$state_content" > "$state" ); then
      die "cannot create $state (exists or not writable); check $state_dir by hand"
    fi
    sync -f "$state" "$state_dir" || sync
    echo "recorded in $state:"
    printf '  %s\n' "${state_content//$'\n'/$'\n'  }"
    echo "+ ${swapoff_cmd[*]}"
    rc=0
    "${swapoff_cmd[@]}" || rc=$?
    if [ "$rc" != 0 ]; then
      if swapoff_running; then
        echo "swapoff of $dev (rc=$rc) is STILL RUNNING in the kernel (blocked on disk reads; INT/KILL are honoured only between memory maps): state file KEPT ($state). Wait until no swapoff process remains (if it is begin's child it also holds .lock; a foreign one is caught by end's process scan), then run: $0 end $dev --expect-partuuid $partuuid --expect-size $size [--commit]; $dev may or may not be swap-active by then, end handles both" >&2
      elif active; then
        rm -f -- "$state"
        echo "swapoff failed or timed out (rc=$rc) and $dev is still swap-active: nothing changed; state file removed; safe to retry begin" >&2
      else
        echo "swapoff returned rc=$rc but $dev is no longer in /proc/swaps: state file KEPT ($state); run: $0 end $dev ... to restore" >&2
      fi
      exit 1
    fi
    if active; then
      echo "ABORT: swapoff returned but $dev is still active (state kept; run end to inspect and restore)" >&2
      exit 1
    fi
    # Re-check all recorded identities immediately before the one irreversible command.
    if ! identity_before_wipe "$partuuid" "$size" "$uuid" "$label" "$partlabel"; then
      echo "ABORT: PARTUUID/size/UUID/LABEL/PARTLABEL of $dev changed after swapoff; no wipe done (state kept; end verifies identity before restoring)" >&2
      exit 1
    fi
    if active; then
      echo "ABORT: $dev became swap-active again before the wipe (state kept; run end to inspect and restore)" >&2
      exit 1
    fi
  else
    echo "+ (would record $state, then:)"
    echo "+ ${swapoff_cmd[*]}"
  fi
  run wipefs -a "$dev"
  if [ "$commit" = 1 ]; then
    [ -z "$(probe TYPE)" ] || { echo "ABORT: signature remains" >&2; exit 1; }
    echo "freed: $dev is out of swap and signature-free."
  else
    echo "(dry-run: nothing was changed)"
  fi
  echo "benchmark next: run iobench on $dev, then: $0 end $dev --expect-partuuid $partuuid --expect-size $size --commit" ;;
end)
  [ ! -d "$state_dir" ] || take_lock
  read_state
  [ "$S_PARTUUID" = "$expect_partuuid" ] && [ "$S_SIZE" = "$expect_size" ] \
    || die "--expect-partuuid/--expect-size do not equal the values recorded at begin ($S_PARTUUID, $S_SIZE)" 2
  if swapoff_running; then
    die "a swapoff process is still running (wait for it to exit, then run end again)"
  fi
  [ "$(partuuid_of)" = "$S_PARTUUID" ] && [ "$(size_of)" = "$S_SIZE" ] || die "PARTUUID/size changed since begin"
  [ "$(lsblk -dno PARTLABEL "$dev" 2>/dev/null || true)" = "$S_PARTLABEL" ] || die "PARTLABEL changed"
  [ -z "$(findmnt -rn -S "$dev")" ] || die "$dev is mounted"
  fstab_opts=$(fstab_swap_options) || die "cannot read exactly one valid PARTUUID=$S_PARTUUID swap row from /etc/fstab"
  if active; then
    verify_restored || exit 1
    echo "$dev is already swap-active with the recorded identity (LABEL=$S_LABEL UUID=$S_UUID); no separate restore is required."
    if [ "$commit" = 1 ]; then
      mv -- "$state" "$state.done-$(date -u +%Y%m%dT%H%M%SZ)"
      echo "state archived."
    else
      echo "(dry-run: --commit would archive the state file)"
    fi
    exit 0
  fi
  if fuser -s "$dev" 2>/dev/null; then die "$dev is still open by a process (iobench/fio running?)"; fi
  # Clear the final whole MiB, where iobench writes its marker.
  run dd if=/dev/zero of="$dev" bs=1M seek=$(( S_SIZE / 1048576 - 1 )) count=1 status=none
  run mkswap -L "$S_LABEL" -U "$S_UUID" "$dev"
  run swapon --options "$fstab_opts" "$dev"
  if [ "$commit" = 1 ]; then
    verify_restored || exit 1
    mv -- "$state" "$state.done-$(date -u +%Y%m%dT%H%M%SZ)"
    echo "restored: $dev label=$S_LABEL uuid=$S_UUID partuuid=$S_PARTUUID active pri=${RESTORE_GOT_PRI:-?}"
  fi ;;
esac
