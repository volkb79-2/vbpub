#!/usr/bin/env bash
# P-plat-19m: restore the host after an interrupted acceptance squeeze run (DESIGN-ACCEPT-KIT-V2.md 2.6).
# Trusts <evidence>/squeeze-state.env, never an in-memory variable, so it repairs the host after ANY death of
# accept-run.sh: SIGKILL, an OOM kill, the systemd stop timeout. Idempotent, and it compares before it
# writes: a file already holding its original counts as restored (that is also the WAL line whose write never
# happened); a file holding any value the kit wrote for that relpath (from ANY step) is the kit's and is restored;
# a file holding a value the kit never wrote belongs to another writer: it is NOTEd, left alone and reported as
# `restore=foreign:<rel>` in the manifest -- the run's restoration is then INCOMPLETE (exit 1) and the WAL stays,
# because only a later write or the reconcile can prove what that file should hold.
# The WAL appends ONE `MAX=<relpath>|<original>|<value the kit wrote>|<utc>` (or `MIN=...`) line per write; the
# original of a relpath is the FIRST line's (the value read at start), and the value in force is the LAST line's
# -- the whole chain of written values is 'the kit's' (a death between a line and its write leaves the previous
# step's value, which is still the kit's own).
# Order: (1) `docker rm -f` the WAL's exact names; (2) poll dev.slice/memory.current down to the original
# dev.slice/memory.max - 64M for <= 60 s; (3a) the memory.min originals, child before parent, unconditionally
# (a min lowering cannot OOM, and a scope directory that is gone counts as restored); (3b) then the
# memory.max originals, child before parent, each only while its own memory.current <= target - 64M, else
# up to 8 x 256M `memory.reclaim` on dev-gates.slice and a deferral (RESTORE-PENDING in manifest.env, WAL
# kept, exit 1); (4) `off` to the WAL-named kdamond, read back -- `nr_kdamonds` is NEVER written: any write
# to it recreates every kdamond directory and fails EBUSY while one is on.
#   accept-restore.sh --evidence DIR [--from-unit [--lock-timeout S]] [--in-run]
# `container=<name>`, `boot_id=`, `kdamond=<idx>` are single lines.
# Exit 0 restored, 3 nothing to do or refused, 1 a lowering is deferred (RESTORE-PENDING) or a foreign value was
# left alone (restore=foreign:<rel>); in both exit-1 cases the WAL is kept. --from-unit is for the
# systemd-run unit's ExecStopPost, which runs after ANY end of the main process: there "nothing to do" (3)
# is the normal outcome and becomes 0 so the unit does not end failed, and the lock wait becomes
# `flock -w <S>` because a slow-dying child can still hold the inherited descriptor. --in-run is for
# accept-run.sh's OWN finish: the caller already holds the lock, so it is neither taken nor waited for.
# Test hooks: MDT_ACCEPT_{CGROUP,PROC,BOOT_ID,SQUEEZE_STATE,LOCK,DAMON_ADMIN,MANIFEST,POLL_STEP,RESTORE_TIMEOUT,RM_TIMEOUT}.
set -uo pipefail
CG=${MDT_ACCEPT_CGROUP:-/sys/fs/cgroup}; PROC=${MDT_ACCEPT_PROC:-/proc}
BOOT_ID_FILE=${MDT_ACCEPT_BOOT_ID:-$PROC/sys/kernel/random/boot_id}
FROM_UNIT=0; IN_RUN=0; LOCK_TIMEOUT=30; EVID=""
HEADROOM=$(( 64 * 1024 * 1024 )); RESTORE_TIMEOUT=${MDT_ACCEPT_RESTORE_TIMEOUT:-60}
STEP=${MDT_ACCEPT_POLL_STEP:-1}; PENDING=0; FOREIGN=0
RM_TIMEOUT=${MDT_ACCEPT_RM_TIMEOUT:-30}
usage() { echo "usage: $0 --evidence DIR [--from-unit [--lock-timeout S]] [--in-run]" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --evidence) [ $# -ge 2 ] || usage; EVID=$2; shift 2 ;;
    --from-unit) FROM_UNIT=1; shift ;;
    --in-run) IN_RUN=1; shift ;;
    --lock-timeout) [ $# -ge 2 ] || usage; LOCK_TIMEOUT=$2; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$EVID" ] || usage
case "$LOCK_TIMEOUT" in ''|*[!0-9]*) usage ;; esac
say() { echo "accept-restore: $*"; }
note() { echo "accept-restore: NOTE $*"; }
# --from-unit turns "nothing to do" into success for the unit. A REFUSAL (a lock that could not be taken) stays
# visible as exit 3: turning it into 0 would report the clean-run case for a restore that never ran.
nothing_exit() { if [ "$1" = 3 ] && [ "$FROM_UNIT" = 1 ]; then exit 0; fi; exit "$1"; }
STATE=${MDT_ACCEPT_SQUEEZE_STATE:-$EVID/squeeze-state.env}
MAN=${MDT_ACCEPT_MANIFEST:-$EVID/manifest.env}
LOCK=${MDT_ACCEPT_LOCK:-/run/mdt-accept.lock}
DAMON_ADMIN=${MDT_ACCEPT_DAMON_ADMIN:-/sys/kernel/mm/damon/admin}
[ -f "$STATE" ] || { say "no $STATE: nothing to do"; nothing_exit 3; }
if [ "$IN_RUN" != 1 ]; then   # --in-run means accept-run.sh's own finish: the caller holds the lock already
  if ! ( umask 022; : >>"$LOCK" ) 2>/dev/null; then
    # "cannot open the lock" is indeterminate, never "no run is live": refuse rather than write under a run
    say "cannot open the lock $LOCK, so it is indeterminate whether a run is live; refusing, nothing done" >&2; exit 3
  fi
  exec 9>>"$LOCK"
  if [ "$FROM_UNIT" = 1 ]; then
    flock -w "$LOCK_TIMEOUT" 9 || { say "the lock is still held after ${LOCK_TIMEOUT}s; refusing, nothing done" >&2; exit 3; }
  else
    flock -n 9 || { say "a live run holds the lock; refusing, nothing done" >&2; exit 3; }
  fi
fi

declare -a MAXES=() MINS=() NAMES=()
boot=""; kd=""
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    MAX=*) MAXES+=("${line#MAX=}") ;;
    MIN=*) MINS+=("${line#MIN=}") ;;
    container=*) NAMES+=("${line#container=}") ;;
    boot_id=*) boot=${line#boot_id=} ;;
    kdamond=*) kd=${line#kdamond=} ;;
    dev_orig=*|k_tested=*|pid=*|started_utc=*|kit_boot=*|'') ;;
    '# '*|'#'*) ;;
    *) note "unknown WAL line '${line%%=*}': host fact only, nothing restored from it" ;;
  esac
done <"$STATE"
now_boot=$(tr -d '[:space:]' <"$BOOT_ID_FILE" 2>/dev/null) || now_boot=""
if [ -z "$now_boot" ]; then   # an unreadable boot_id is INDETERMINATE, and restoring is the safe direction
  note "cannot read $BOOT_ID_FILE, so whether the host rebooted cannot be told; restoring the originals anyway"
fi
if [ -n "$boot" ] && [ -n "$now_boot" ] && [ "$now_boot" != "$boot" ]; then
  # A reboot already reset every cgroup file: writing the originals back would fight the fresh boot
  say "boot_id $boot -> $now_boot: the host rebooted since the WAL was written; nothing written"
  rm -f "$STATE"; printf 'restore=ok\nrestore_note=rebooted\n' >>"$MAN" 2>/dev/null
  exit 0
fi

for n in "${NAMES[@]+"${NAMES[@]}"}"; do   # (1) the exact names; --rm never relied on, every name logged
  say "rm -f $n"
  # a squeezed dockerd can block this `rm` exactly as it blocks the probes: bound it, log its rc, and never let it
  # stop the cgroup restorations or the kdamond off below (those are what un-squeeze the host)
  rc_rm=0
  timeout -k 5 "$RM_TIMEOUT" docker rm -f "$n" >/dev/null 2>&1 || rc_rm=$?
  [ "$rc_rm" = 0 ] || note "docker rm -f $n did not complete (rc $rc_rm, ${RM_TIMEOUT}s bound); the restorations below run anyway"
  unset rc_rm
done
dev_orig=""
for e in "${MAXES[@]+"${MAXES[@]}"}"; do
  case "$e" in dev.slice/memory.max\|*) dev_orig=${e#dev.slice/memory.max|}; dev_orig=${dev_orig%%|*} ;; esac
done
if [ -n "$dev_orig" ] && [ "$dev_orig" != max ]; then   # (2) room for the max restorations
  want=$(( dev_orig - HEADROOM )); t0=$SECONDS
  while :; do
    now=$(cat "$CG/dev.slice/memory.current" 2>/dev/null) || now=""
    case "$now" in ''|*[!0-9]*) now="" ;; esac
    [ -n "$now" ] && [ "$now" -le "$want" ] && break
    [ "$STEP" != 0 ] || break
    if [ $(( SECONDS - t0 )) -ge "$RESTORE_TIMEOUT" ]; then
      note "dev.slice/memory.current ${now:-unreadable} > $want after ${RESTORE_TIMEOUT}s; the max restores decide on their own 64M rule"; break
    fi
    sleep "$STEP"
  done
fi

wal_entries() {   # stdin: `<relpath>|<original>|<written>|<utc>` lines -> depth<TAB>rel<TAB>first-original<TAB>written-set
  # The ORIGINAL is the first line's (the value read at start); every value the kit wrote for that relpath is the
  # kit's (a death between a line and its write leaves an earlier step's value in the file). The sort puts the
  # deepest relpath first, so a child is restored before its parent.
  awk -F'|' '{ rel=$1
       if (!(rel in seen)) { seen[rel]=1; n++; r[n]=rel; orig[rel]=$2; ws[rel]=$3 }
       else if ($3 != "") { ws[rel] = ws[rel] "," $3 } }
     END { for (i=1;i<=n;i++) { m=split(r[i],a,"/"); print m "\t" r[i] "\t" orig[r[i]] "\t" ws[r[i]] } }' \
    | LC_ALL=C sort -t$'\t' -k1,1nr | cut -f2-
}
restore_one() { # <max|min> <relpath>\t<first-original>\t<written-set>
  local kind=$1 entry=$2 rel orig set f dir cur now rdir v ok=0 _
  local -a _vals=()
  IFS=$'\t' read -r rel orig set <<<"$entry"
  f="$CG/$rel"; dir=$(dirname "$f")
  if [ ! -d "$dir" ]; then note "$rel: its cgroup is gone (removed by docker or by the boot); counts as restored"; return 0; fi
  cur=$(cat "$f" 2>/dev/null) || cur=""
  [ "$cur" = "$orig" ] && return 0   # already at the original (also the WAL line whose write never happened)
  IFS=',' read -ra _vals <<<"$set"
  for v in "${_vals[@]+"${_vals[@]}"}"; do [ -n "$v" ] && [ "$cur" = "$v" ] && ok=1 && break; done
  if [ "$ok" != 1 ]; then
    note "$rel holds ${cur:-unreadable}, which the kit never wrote (it wrote ${set:-<nothing>}; original $orig); another writer owns it, left alone"
    printf 'restore=foreign:%s\n' "$rel" >>"$MAN" 2>/dev/null
    FOREIGN=1; return 0
  fi
  if [ "$kind" = max ] && [ "$orig" != max ]; then   # (3b) a lowering: only with 64M of headroom KNOWN
    now=$(cat "$dir/memory.current" 2>/dev/null) || now=""
    case "$now" in ''|*[!0-9]*)   # an unreadable memory.current is "no headroom known", never "0" (fail closed)
      note "$rel: $dir/memory.current is unreadable, so there is no known headroom; the lowering is DEFERRED"
      printf 'RESTORE-PENDING %s %s %s\n' "$rel" "$orig" "unreadable" >>"$MAN" 2>/dev/null
      PENDING=1; return 0 ;;
    esac
    if [ "$now" -gt $(( orig - HEADROOM )) ]; then
      rdir="$CG/dev.slice/dev-gates.slice"
      for _ in 1 2 3 4 5 6 7 8; do   # the load tiers only, never dev.slice itself (the cockpit lives there)
        [ -w "$rdir/memory.reclaim" ] && echo 256M >"$rdir/memory.reclaim" 2>/dev/null
        now=$(cat "$dir/memory.current" 2>/dev/null) || now="$now"
        case "$now" in ''|*[!0-9]*) now=0 ;; esac
        [ "$now" -le $(( orig - HEADROOM )) ] && break
      done
      if [ "$now" -gt $(( orig - HEADROOM )) ]; then
        note "$rel: memory.current $now > $(( orig - HEADROOM )) (target $orig - 64M); the lowering is DEFERRED"
        printf 'RESTORE-PENDING %s %s %s\n' "$rel" "$orig" "$now" >>"$MAN" 2>/dev/null
        PENDING=1; return 0
      fi
    fi
  fi
  if echo "$orig" >"$f" 2>/dev/null && [ "$(cat "$f" 2>/dev/null)" = "$orig" ]; then
    say "$rel $cur -> $orig (kit wrote ${set})"
  else
    note "$rel: writing $orig failed (still $(cat "$f" 2>/dev/null)); left for the reconcile"; PENDING=1
  fi
}
if [ "${#MINS[@]}" -gt 0 ]; then   # (3a) child before parent, unconditionally: a min lowering cannot OOM
  while IFS= read -r e; do [ -n "$e" ] && restore_one min "$e"; done < <(printf '%s\n' "${MINS[@]}" | wal_entries)
fi
if [ "${#MAXES[@]}" -gt 0 ]; then   # (3b) child before parent, each under its own 64M rule
  while IFS= read -r e; do [ -n "$e" ] && restore_one max "$e"; done < <(printf '%s\n' "${MAXES[@]}" | wal_entries)
fi
if [ -n "$kd" ] && [ -d "$DAMON_ADMIN/kdamonds/$kd" ]; then   # (4) off only, read back
  kof="$DAMON_ADMIN/kdamonds/$kd/state"
  if echo off >"$kof" 2>/dev/null && [ "$(cat "$kof" 2>/dev/null)" = off ]; then
    say "kdamond $kd -> off"
  else
    note "kdamond $kd: could not be switched off; its state file was left as it was (nr_kdamonds is never written)"
  fi
fi
if [ "$PENDING" = 1 ]; then
  printf 'restore=pending\n' >>"$MAN" 2>/dev/null
fi
if [ "$PENDING" = 1 ] || [ "$FOREIGN" = 1 ]; then
  # The stricter choice: exit 1 and KEEP the WAL in both cases. `restore=ok` is written only when every file the
  # kit wrote is back at its original; a foreign value needs another writer to prove it, a deferred lowering
  # needs the reconcile, and either one may be finished by a later accept-restore.sh from the same WAL.
  [ "$FOREIGN" = 1 ] && say "a file the kit never wrote was left alone (restore=foreign in $MAN); the state file is kept"
  [ "$PENDING" = 1 ] && say "a lowering is deferred (RESTORE-PENDING in $MAN); the state file is kept; the unlocked reconcile completes it"
  exit 1
fi
rm -f "$STATE"
printf 'restore=ok\nrestore_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$MAN" 2>/dev/null
say "restored: every kit-written value is back at its original, state file removed"
exit 0
