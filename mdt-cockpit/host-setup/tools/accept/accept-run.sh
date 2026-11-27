#!/usr/bin/env bash
# P-plat-19m: one acceptance SQUEEZE run of the dedicated-dev memory model (DESIGN-ACCEPT-KIT-V2.md 2, 3, 7 step 3).
# Root, ON the host, launched DETACHED through `systemd-run` whose ExecStopPost runs accept-restore.sh: a run has to
# outlive an ssh drop, and a SIGKILL or an OOM kill of the unit has to un-squeeze the host. The traps below cover
# EXIT/INT/TERM/HUP only; the write-ahead state file (squeeze-state.env) plus accept-restore.sh cover the rest.
#   accept-run.sh --evidence DIR [--steps N] [--step SIZE] [--dwell S] [--warmup S] [--probe-image REF]
#                  [--control auto|on|off]
# Exit 0 PASS, 1 FAIL (step 0 failed), 4 INCONCLUSIVE, 3 refused, 2 usage.
# Test hooks (env): MDT_ACCEPT_{CGROUP,PROC,HOST_ENV,BOOT_ID,INTERVAL,DWELL_TICKS,SETTLE_TICKS,WARMUP_TICKS,MAX_TICKS,UNITS,
# PAGESIZE,LOCK,MIN_DISK,SQUEEZE_STATE,HOTSET,HOTSET_JSON,HOTSET_EVERY,DAMON,DAMON_STAT,JUDGE,SSH_TARGET,EXTRA_UNITS,
# PROBE_IMAGE,PROBE_CMD_<NAME>,PROBE_TIMEOUT,PROBE_TIMEOUT_<NAME>,POLL_STEP,GROW_TRIES,PSI_HOLD,RESTORE_TIMEOUT,
# RM_TIMEOUT,SYSTEMCTL_TIMEOUT,GUARD_EVERY,MIN_EVID_DISK,SSHD_BANNER_CMD}.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
for tool in awk basename bash cat cut date df dirname docker flock getconf grep head hostname id journalctl logger mkdir od python3 rm sed seq sha256sum sh sleep sort ssh sync systemctl tail timeout tr wc; do
  command -v "$tool" >/dev/null 2>&1 || { echo "accept-run: required host tool $tool is missing from PATH; refusing before host changes" >&2; exit 3; }
done
KIT_SHA=${MDT_ACCEPT_SOURCE_SHA:-}
if [[ ! "$KIT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "accept-run: MDT_ACCEPT_SOURCE_SHA must be the full 40-character lowercase commit SHA of this archived kit; refusing" >&2
  exit 3
fi
CG=${MDT_ACCEPT_CGROUP:-/sys/fs/cgroup}
PROC=${MDT_ACCEPT_PROC:-/proc}
HOSTENV=${MDT_ACCEPT_HOST_ENV:-/etc/mdt/host-setup.env}
BOOTID_FILE=${MDT_ACCEPT_BOOT_ID:-$PROC/sys/kernel/random/boot_id}
INTERVAL=${MDT_ACCEPT_INTERVAL:-5}
PAGESIZE=${MDT_ACCEPT_PAGESIZE:-$(getconf PAGESIZE)}
MIB=$((1024 * 1024))
GIB=$((1024 * MIB))
LOCK=${MDT_ACCEPT_LOCK:-/run/mdt-accept.lock}
SQUEEZE_UNPROTECTED=$((256 * MIB))
DUMMY=mdt-accept-squeeze
PARENT=dev-gates.slice
HOTSET=${MDT_ACCEPT_HOTSET:-$HERE/accept-hotset.py}
DAMON_ADMIN=${MDT_ACCEPT_DAMON:-/sys/kernel/mm/damon/admin}
JUDGE=${MDT_ACCEPT_JUDGE:-}
SSH_TARGET=${MDT_ACCEPT_SSH_TARGET:-}
SSH_KEY=${MDT_ACCEPT_SSH_KEY:-}
SSH_ARGS=()
if [ -n "$SSH_KEY" ]; then
  [ -r "$SSH_KEY" ] || { echo "accept-run: MDT_ACCEPT_SSH_KEY=$SSH_KEY is not readable; refusing before host changes" >&2; exit 3; }
  SSH_ARGS=(-i "$SSH_KEY" -o IdentitiesOnly=yes)
fi
EXTRA_UNITS=${MDT_ACCEPT_EXTRA_UNITS:-}
POLL=${MDT_ACCEPT_POLL_STEP:-1}
RM_TIMEOUT=${MDT_ACCEPT_RM_TIMEOUT:-30}
SYSTEMCTL_TIMEOUT=${MDT_ACCEPT_SYSTEMCTL_TIMEOUT:-2}
GROW_TRIES=${MDT_ACCEPT_GROW_TRIES:-60}
MAX_TICKS=${MDT_ACCEPT_MAX_TICKS:-0}
PSI_HOLD=${MDT_ACCEPT_PSI_HOLD:-10}
GUARD_EVERY=${MDT_ACCEPT_GUARD_EVERY:-5}   # L5: seconds between the guard runs inside one tick (test hook: 0 = every probe)
UNITS=${MDT_ACCEPT_UNITS:-"ssh.service=system.slice/ssh.service docker.service=system.slice/docker.service containerd.service=system.slice/containerd.service systemd-journald.service=system.slice/systemd-journald.service mdt-psi-guard.service=system.slice/mdt-psi-guard.service cgprofiled.service=cgprofile.slice/cgprofiled.service user.slice=user.slice dev-interactive.slice=dev.slice/dev-interactive.slice"}
EVID=""; STEPS=6; STEP=$((128 * MIB)); DWELL=300; SETTLE=60; WARMUP=600
IMAGE=${MDT_ACCEPT_PROBE_IMAGE:-tester-unified:local}; CONTROL=auto
usage() { echo "usage: $0 --evidence DIR [--steps N] [--step SIZE] [--dwell S] [--warmup S] [--probe-image REF] [--control auto|on|off]" >&2; exit 2; }
say() { echo "accept-run: $*"; }
note() { echo "accept-run: NOTE $*"; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
systemctl_read() { # bounded read; SYSTEMCTL_RC is `timeout`, a numeric rc, or zero; SYSTEMCTL_OUT is never invented
  local rc=0
  SYSTEMCTL_OUT=$(timeout -k 1 "$SYSTEMCTL_TIMEOUT" systemctl "$@" 2>/dev/null) || rc=$?
  case "$rc" in 124|137) SYSTEMCTL_RC=timeout ;; *) SYSTEMCTL_RC=$rc ;; esac
  return 0
}
parse_size() { # 128M / 4096 / 2G -> bytes; a bare number is bytes
  local v=$1 n mult
  case $v in *K|*k) mult=1024 ;; *M|*m) mult=$MIB ;; *G|*g) mult=$GIB ;; *) mult=1 ;; esac
  n=${v%[KkMmGg]}
  case $n in ''|*[!0-9]*) return 1 ;; esac
  echo $((n * mult))
}
rdnum() { local v; v=$(cat "$1" 2>/dev/null) || v=""; case $v in ''|*[!0-9]*) echo "" ;; *) echo "$v" ;; esac; }
proc_cpu_ticks() { # /proc/<pid>/stat comm may contain spaces and ')' characters; parse fields after its final ')'
  awk '{ rest=$0; sub(/^.*\) /, "", rest); n=split(rest, f, /[[:space:]]+/); if (n < 13) exit 1; print f[12]+f[13] }' "$1" 2>/dev/null
}
envval() { sed -n "s/^$1=//p" "$HOSTENV" 2>/dev/null | tail -n 1 | tr -d "\"' "; }
while [ $# -gt 0 ]; do
  case "$1" in
    --evidence) [ $# -ge 2 ] || usage; EVID=$2; shift 2 ;;
    --steps) [ $# -ge 2 ] || usage; STEPS=$2; shift 2 ;;
    --step) [ $# -ge 2 ] || usage; STEP=$(parse_size "$2") || usage; shift 2 ;;
    --dwell) [ $# -ge 2 ] || usage; DWELL=$2; shift 2 ;;
    --warmup) [ $# -ge 2 ] || usage; WARMUP=$2; shift 2 ;;
    --probe-image) [ $# -ge 2 ] || usage; IMAGE=$2; shift 2 ;;
    --control) [ $# -ge 2 ] || usage; CONTROL=$2; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$EVID" ] || usage
case "$STEPS$DWELL$WARMUP$INTERVAL" in ''|*[!0-9]*) usage ;; esac
case "$STEP$PSI_HOLD$GROW_TRIES" in ''|*[!0-9]*) usage ;; esac
case "$SYSTEMCTL_TIMEOUT" in ''|0*|*[!0-9]*) usage ;; esac
case "$CONTROL" in auto|on|off) ;; *) usage ;; esac
if [ -n "${MDT_ACCEPT_WARMUP_TICKS:-}" ]; then
  case $MDT_ACCEPT_WARMUP_TICKS in *[!0-9]*) usage ;; esac
  WARMUP_TICKS=$MDT_ACCEPT_WARMUP_TICKS
elif [ "$INTERVAL" = 0 ]; then
  [ "$WARMUP" = 0 ] || { echo "accept-run: a tick-driven run with --warmup > 0 needs MDT_ACCEPT_WARMUP_TICKS; refusing" >&2; exit 3; }
  WARMUP_TICKS=0
else
  WARMUP_TICKS=$(( (WARMUP + INTERVAL - 1) / INTERVAL ))
fi
WARMUP_RUN_SECONDS=$((WARMUP_TICKS * INTERVAL))
role=$(sed -n 's/^HOST_ROLE=//p' "$HOSTENV" 2>/dev/null | tail -n 1 | tr -d "\"' ")
if [ "$role" != dedicated-dev ]; then echo "accept-run: HOST_ROLE=${role:-<unset>} in $HOSTENV, not dedicated-dev; refusing" >&2; exit 3; fi
# Environment refusals must happen before the lock, evidence directory, stale
# WAL restore, or any cgroup/DAMON write. This pass is read-only; setup repeats
# its DAMON checks immediately before taking ownership and writing sysfs.
STATE=${MDT_ACCEPT_SQUEEZE_STATE:-$EVID/squeeze-state.env}
DAMON_STAT_OVERRIDE=${MDT_ACCEPT_DAMON_STAT:-}
LIB="$HERE/../../../../scripts/damon-analysis/lib"
for p in "$HERE/accept-restore.sh" "$HERE/accept-eval.py" "$HOTSET"; do
  [ -f "$p" ] || { echo "accept-run: $p is missing; refusing before host changes" >&2; exit 3; }
done
if [ ! -d "$LIB" ] && [ -z "${MDT_ACCEPT_HOTSET_JSON:-}" ]; then
  echo "accept-run: $LIB is missing (an accept checkout needs mdt-cockpit/ AND scripts/damon-analysis/); refusing before host changes" >&2
  exit 3
fi
HOTSET_ARGS=(--evidence "$EVID" --admin "$DAMON_ADMIN" --state "$STATE" --units "$UNITS" --cgroup "$CG" --proc "$PROC")
[ -d "$LIB" ] && HOTSET_ARGS+=(--lib "$LIB")
[ -n "${MDT_ACCEPT_HOTSET_JSON:-}" ] && HOTSET_ARGS+=(--fixture "$MDT_ACCEPT_HOTSET_JSON")
[ -n "$DAMON_STAT_OVERRIDE" ] && HOTSET_ARGS+=(--damon-stat "$DAMON_STAT_OVERRIDE")
hotset() { python3 "$HOTSET" "$@" "${HOTSET_ARGS[@]}"; }
[ -n "$SSH_TARGET" ] || { echo "accept-run: MDT_ACCEPT_SSH_TARGET is required (this host over loopback, e.g. root@127.0.0.1); refusing before host changes" >&2; exit 3; }
BOOT_ID=$(tr -d '[:space:]' < "$BOOTID_FILE" 2>/dev/null) || BOOT_ID=""
[ -n "$BOOT_ID" ] || { echo "accept-run: cannot read $BOOTID_FILE; refusing before host changes (the restore's reboot rule needs it)" >&2; exit 3; }
# The LITERAL kernel path, never $BOOTID_FILE: a test override must not be
# able to change which remote host is being verified.
ssh_boot=$(timeout -k 5 20 ssh "${SSH_ARGS[@]}" -o BatchMode=yes -o ConnectTimeout=10 "$SSH_TARGET" cat /proc/sys/kernel/random/boot_id 2>/dev/null | tr -d '[:space:]') || ssh_boot=""
if [ "$ssh_boot" != "$BOOT_ID" ]; then
  echo "accept-run: MDT_ACCEPT_SSH_TARGET=$SSH_TARGET reports boot_id '${ssh_boot:-<none>}' but this host is '$BOOT_ID': a remote target would time another machine's sshd (3.2); refusing before host changes" >&2
  exit 3
fi
hotset preflight >/dev/null || { say "the read-only cgprofile, DAMON_STAT, DAMON sysfs and capability preflight refused; no host state has been changed"; exit 3; }
# One run at a time. This lock is what pauses the reconcile's memory block (2.3) and makes install.sh refuse; the
# kernel releases it on ANY death of this process, SIGKILL included, so a dead run never leaves the host unpaused.
exec 9>>"$LOCK" || { echo "accept-run: cannot open the lock $LOCK; refusing, nothing done" >&2; exit 3; }
flock -n 9 || { echo "accept-run: another run holds $LOCK; refusing" >&2; exit 3; }
mkdir -p "$EVID/steps" "$EVID/probes-state" || exit 2
MAN=$EVID/manifest.env; PROBES=$EVID/probes.csv; SAMPLES=$EVID/samples.csv; STEPSEN=$EVID/steps.env
if [ -s "$STATE" ]; then # a SIGKILLed run left its write-ahead log: restore it first, then use the fresh evidence (2.6)
  say "a state file of an earlier run is present; restoring it first"
  if "$HERE/accept-restore.sh" --evidence "$EVID" --in-run; then
    note "the earlier state file is restored"
  else
    stale_rc=$?
    echo "accept-run: stale write-ahead state file $STATE was not fully restored (accept-restore exit $stale_rc); refusing this run (exit 3), leaving the stale state and run evidence in place" >&2
    exit 3
  fi
fi
# Do not truncate the previous ladder until its WAL has restored cleanly.
: >"$STEPSEN"   # the previous run's step lines would otherwise be read as this run's ladder
# ---------- preflight: every fact a label or a write depends on, else exit 3 (2.1, 2.2, 3.2, 4.1, 4.4) ----------
command -v python3 >/dev/null 2>&1 || { echo "accept-run: python3 is missing (the judge and the hot-set driver need it); refusing" >&2; exit 3; }
# The five files' START values are captured here, ONCE, and are what every WAL line carries as its original and
# what the restore writes back: a MIN read again at step s is the kit's own step s-1 value, never the original.
DEV_MAX_START=$(rdnum "$CG/dev.slice/memory.max")
TIER_MAX_START=$(rdnum "$CG/dev.slice/$PARENT/memory.max")
DEV_MIN_START=$(rdnum "$CG/dev.slice/memory.min")
TIER_MIN_START=$(rdnum "$CG/dev.slice/$PARENT/memory.min")
[ -n "$DEV_MAX_START" ] || { echo "accept-run: dev.slice/memory.max is not numeric; the ladder cannot raise it; refusing" >&2; exit 3; }
[ -n "$TIER_MAX_START" ] || { echo "accept-run: dev.slice/$PARENT/memory.max is not numeric (an absent tier would let docker make an unlimited transient slice); refusing" >&2; exit 3; }
[ -n "$DEV_MIN_START" ] || { echo "accept-run: dev.slice/memory.min is not numeric, so its original could not be restored; refusing" >&2; exit 3; }
[ -n "$TIER_MIN_START" ] || { echo "accept-run: dev.slice/$PARENT/memory.min is not numeric, so its original could not be restored; refusing" >&2; exit 3; }
# L7: a run that starts before the reconcile tick would read a leftover (raised) MIN as its "original". The max is
# checked below; the mins get the same readback: systemd's MemoryMin is what install.sh rendered, so a kernel min
# ABOVE it is a leftover raise (the kit only ever raises), never a value to record as the start.
for pair in dev.slice:$DEV_MIN_START "$PARENT:$TIER_MIN_START"; do
  s=${pair%%:*}; have=${pair#*:}
  systemctl_read show -p MemoryMin --value "$s"
  sd=$(parse_size "${SYSTEMCTL_OUT:-}" 2>/dev/null) || sd=""
  [ -n "$sd" ] || { echo "accept-run: cannot read $s's MemoryMin via systemctl; a leftover min would become this run's original; refusing" >&2; exit 3; }
  if [ "$have" -gt "$sd" ]; then
    echo "accept-run: $s/memory.min $have is above systemd's MemoryMin $sd: a leftover raise of an interrupted run (the kit only raises). Restore it first (tools/accept/accept-restore.sh --evidence <its EV>), then re-run; refusing, nothing done" >&2
    exit 3
  fi
done
if [ "$TIER_MAX_START" -gt "$DEV_MAX_START" ]; then
  echo "accept-run: $PARENT/memory.max $TIER_MAX_START is above dev.slice's $DEV_MAX_START; the ladder only raises and could not keep the tier on dev's ceiling; refusing" >&2; exit 3
fi
mt_kb=$(awk '/^MemTotal:/{print $2}' "$PROC/meminfo" 2>/dev/null)
[ -n "$mt_kb" ] || { echo "accept-run: no MemTotal in $PROC/meminfo; refusing" >&2; exit 3; }
MEM_TOTAL=$((mt_kb * 1024))
EXCLUDED=$(envval MDT_EXCLUDED_RAM); EXCLUDED=$(parse_size "${EXCLUDED:-0}") || EXCLUDED=0
if keep_raw=$(envval DEV_HOST_MEMORY_KEEP) && [ -n "$keep_raw" ]; then
  K0=$(parse_size "$keep_raw") || { echo "accept-run: DEV_HOST_MEMORY_KEEP=$keep_raw is not a size; refusing" >&2; exit 3; }
else # neither readable -> exit 3, never an invented K0
  K0=$(PYTHONPATH="$HERE/../.." python3 -c '
import os, sys
import mdt_capacity as cap
values = {}
if os.path.isfile(sys.argv[1]):
    for line in open(sys.argv[1]):
        k, sep, v = line.partition("=")
        if sep:
            values[k.strip()] = v.strip().strip("\"'"'"'")
try:
    print(cap.host_floors(values, int(sys.argv[2])).keep)
except Exception:
    sys.exit(1)
' "$HOSTENV" "$MEM_TOTAL" 2>/dev/null) || K0=""
  [ -n "$K0" ] || { echo "accept-run: neither DEV_HOST_MEMORY_KEEP nor mdt_capacity.host_floors() gives K0; refusing" >&2; exit 3; }
fi
want_dev=$((MEM_TOTAL - EXCLUDED - K0))
if [ "$DEV_MAX_START" -ne "$want_dev" ]; then
  echo "accept-run: dev.slice/memory.max $DEV_MAX_START != MemTotal $MEM_TOTAL - MDT_EXCLUDED_RAM $EXCLUDED - K0 $K0 = $want_dev, so every K(s) label would be a lie; refusing (run install.sh, or correct the pin)" >&2
  exit 3
fi
if pin_max=$(envval DEV_MEMORY_MAX) && [ -n "$pin_max" ]; then
  echo "accept-run: DEV_MEMORY_MAX=$pin_max pins the ceiling, so K(s) = K0 - s*STEP is not the host reserve written here; refusing" >&2; exit 3
fi
# F_installed = MemoryMin of the three slices. The units' floors are already inside system.slice's derived value and
# must NOT be added again (that double-counts ~420M on r1002 and stops the ladder two steps in, 2.1).
F_SYSTEM=""; F_CGP=""; F_USER=""
for s in system.slice cgprofile.slice user.slice; do
  systemctl_read show -p MemoryMin --value "$s"
  v=$(parse_size "${SYSTEMCTL_OUT:-}" 2>/dev/null) || v=""
  case $s in system.slice) F_SYSTEM=$v ;; cgprofile.slice) F_CGP=$v ;; user.slice) F_USER=$v ;; esac
done
if [ -z "$F_SYSTEM" ] || [ -z "$F_CGP" ] || [ -z "$F_USER" ]; then
  echo "accept-run: cannot read the MemoryMin of system.slice, cgprofile.slice and user.slice via systemctl; the ladder floor is unknown; refusing" >&2; exit 3
fi
F_INSTALLED=$((F_SYSTEM + F_CGP + F_USER))
rootdir=$(timeout -k 5 10 docker info -f '{{.DockerRootDir}}' 2>/dev/null) || rootdir=""
[ -n "$rootdir" ] || { echo "accept-run: could not read Docker's data root within 10s; refusing rather than checking an invented filesystem" >&2; exit 3; }
avail=$(df -Pk "$rootdir" 2>/dev/null | awk 'NR==2{print $4}')
[ "${avail:-0}" -ge "${MDT_ACCEPT_MIN_DISK:-4194304}" ] || { echo "accept-run: less than 4G free in the Docker data root; refusing" >&2; exit 3; }
# M3: the write-ahead log lives on the EVIDENCE filesystem, which may NOT be the Docker data root (/root vs /var/lib).
# A full evidence disk would let every cgroup write go ahead unrecorded, and the restore would then find a value it
# cannot prove. So the preflight checks the filesystem the WAL is actually written to.
ev_avail=$(df -Pk "$EVID" 2>/dev/null | awk 'NR==2{print $4}')
[ "${ev_avail:-0}" -ge "${MDT_ACCEPT_MIN_EVID_DISK:-65536}" ] || {
  echo "accept-run: less than $(( ${MDT_ACCEPT_MIN_EVID_DISK:-65536} / 1024 ))M free in the evidence filesystem of $EVID (${ev_avail:-unreadable} KiB); the write-ahead log could not be trusted; refusing" >&2
  exit 3
}
# Repeat the read-only capability check immediately before setup to catch state
# drift while the preceding host/cgroup preflight was being evaluated.
hotset preflight >/dev/null || { say "the hot-set environment preflight refused; no host state has been changed"; exit 3; }
# L4: the traps are armed BEFORE `hotset setup`, because setup WALs ownership/counts, grows the daemon/context, and
# switches the kdamond ON. An INT/TERM/HUP in that window used to leave the kdamond to ExecStopPost only (not when
# the kit is run by hand). The full finish() replaces these the moment the ladder state exists; until then the WAL
# is restored the same way.
early_stop() { # <rc>
  trap - EXIT INT TERM HUP
  [ -s "$STATE" ] && "$HERE/accept-restore.sh" --evidence "$EVID" --in-run >/dev/null 2>&1
  exit "$1"
}
trap 'early_stop $?' EXIT; trap 'early_stop 130' INT; trap 'early_stop 143' TERM; trap 'early_stop 129' HUP
setup_out=$(hotset setup) || { say "the hot-set setup refused after its read-only preflight; its DAMON WAL restore ran"; exit 3; }
DAMON_STATE=$(printf '%s\n' "$setup_out" | sed -n 's/^damon_stat_enabled=//p' | tail -n 1); DAMON_STATE=${DAMON_STATE:-absent}
KDM=$(printf '%s\n' "$setup_out" | sed -n 's/^kdamond=//p' | tail -n 1)
DAMON_OK=1
# ---------- traps and the finish path (2.6) ----------
# `EXIT_RC` starts EMPTY, never 0: finish() takes `EXIT_RC=${EXIT_RC:-$rc}` ("no verdict was decided yet, so this
# exit's status is the run's"). A non-empty seed made `${EXIT_RC:-$rc}` ignore the real status of every refusal that
# exits AFTER this trap is armed -- the unwritable-WAL refusal (exit 3) and the dummy-start, missing-scope and
# placement-mismatch ones -- so a host-refusing run reported success. Empty means "take $rc"; the ladder still
# decides its own status below (EXIT_RC=$VERDICT_RC) and the INT/TERM/HUP traps set theirs. (Both halves pinned by
# ShellKitTests::test_an_unwritable_state_path_writes_no_cgroup_file.)
START_UTC=$(now); T0=$SECONDS; TICKS=0; EXIT_RC=; FINISHED=""; SLP=""
DEV_WRITTEN=$DEV_MAX_START; TIER_WRITTEN=$TIER_MAX_START
DEV_MIN_WRITTEN=$DEV_MIN_START; TIER_MIN_WRITTEN=$TIER_MIN_START
SCOPE=""; SCOPE_MIN_WRITTEN=0; SCOPE_ANON=0
OOM0=$(awk '$1=="oom_kill"{print $2+0}' "$PROC/vmstat" 2>/dev/null); OOM0=${OOM0:-0}
ABORT=""; ABORT_VERDICT=""; ABORT_CAUSE=""; STEP_VERDICT=""; STEP_CAUSE=""; LADDER_INCONCLUSIVE=0
LAST_PASS_STEP=""; K_LAST_PASS=""; FIRST_FAIL_STEP=""; K_FIRST_FAIL=""; STEPS_RUN=0
finish() {
  local rc=$?
  trap - EXIT INT TERM HUP
  [ -n "$FINISHED" ] && exit "${EXIT_RC:-$rc}"
  FINISHED=1
  EXIT_RC=${EXIT_RC:-$rc}
  [ -n "$SLP" ] && kill "$SLP" 2>/dev/null
  say "rm -f $DUMMY" # (1) the exact name; --rm is never relied on, and every rm is logged
  # M2: a squeezed dockerd can block exactly as it stalls the dockerd/containerd probes. A bare `docker rm -f` here
  # would hang finish() and keep the ExecStopPost restore from ever running its cgroup writes; bound it and go on.
  rc_rm=0
  timeout -k 5 "$RM_TIMEOUT" docker rm -f "$DUMMY" >/dev/null 2>&1 || rc_rm=$?
  [ "$rc_rm" = 0 ] || note "docker rm -f $DUMMY did not complete (rc $rc_rm, ${RM_TIMEOUT}s bound); the restore below runs anyway"
  unset rc_rm
  if [ -s "$STATE" ]; then
    if "$HERE/accept-restore.sh" --evidence "$EVID" --in-run; then
      : # accept-restore.sh wrote restore=ok itself, and only when nothing was left alone
    else
      # exit 1: a lowering is deferred (RESTORE-PENDING) or a value the kit never wrote was left alone
      # (restore=foreign:<rel>); either way the kit cannot state that the host is back at its originals
      if ! grep -q '^restore=' "$MAN" 2>/dev/null; then   # a crash/refusal wrote no line of its own
        if grep -q '^restore=foreign:' "$MAN" 2>/dev/null; then echo "restore=foreign" >>"$MAN"
        else echo "restore=pending" >>"$MAN"; fi
      fi
      note "the restore did not complete (see $MAN for RESTORE-PENDING / DAMON-RESTORE-PENDING / restore=foreign:<rel>); a later accept-restore.sh must finish it from the kept state file"
      [ "$EXIT_RC" = 0 ] && EXIT_RC=4 # never a PASS while a lowering is still deferred: an install would apply it
    fi
  else
    # nothing was written to a cgroup file: an absent state file is a clean run, never a restore claim about a host
    # that was touched
    echo "restore=ok" >>"$MAN"
  fi
  if command -v journalctl >/dev/null 2>&1; then
    timeout -k 5 10 journalctl -k --since "$START_UTC UTC" --no-pager 2>/dev/null \
      | grep -iE 'out of memory|oom-kill' >"$EVID/kernel-oom.txt" || true
    echo "kernel_oom_lines=$(wc -l <"$EVID/kernel-oom.txt" 2>/dev/null)" >>"$MAN"
  fi
  # 4.1: damon_measure is ok only when no unit's floor was refused; accept-hotset records one
  # `damon_measure=failed:<unit>:<why>` line per refused unit (6's format), so this only fills in the gaps
  if [ -s "$EVID/hotset-refused" ]; then
    for u in $(cat "$EVID/hotset-refused"); do
      grep -q "^damon_measure=failed:$u:" "$MAN" 2>/dev/null || echo "damon_measure=failed:$u:sz_ops_filter_passed 0" >>"$MAN"
    done
  else
    echo "damon_measure=ok" >>"$MAN"
  fi
  echo "end_utc=$(now)" >>"$MAN"
  exit "$EXIT_RC"
}
trap finish EXIT; trap 'EXIT_RC=130; exit 130' INT; trap 'EXIT_RC=143; exit 143' TERM; trap 'EXIT_RC=129; exit 129' HUP
# ---------- the evidence header ----------
{
  echo "host=$(hostname)"; echo "start_utc=$START_UTC"; echo "kit_version=2"; echo "pid=$$"; echo "interval=$INTERVAL"
  echo "squeeze_step=$STEPS"; echo "squeeze_dwell=$DWELL"; echo "step_bytes=$STEP"
  echo "warmup=$WARMUP_RUN_SECONDS"; echo "warmup_ticks=$WARMUP_TICKS"
  echo "k_tested=$K0"; echo "floors_installed=$F_INSTALLED"
  echo "floors_snapshot=system.slice=$F_SYSTEM cgprofile.slice=$F_CGP user.slice=$F_USER"
  echo "limit_source=derived"; echo "probe_image=$IMAGE"; echo "damon_stat_enabled=$DAMON_STATE"
  [ -n "$KDM" ] && echo "kdamond=$KDM"
  echo "kit_sha=$KIT_SHA"
  echo "kit_sha256=$(cat "$HERE"/accept-run.sh "$HERE"/accept-hotset.py "$HERE"/accept-eval.py "$HERE"/accept-restore.sh 2>/dev/null | sha256sum | cut -d' ' -f1)"
  echo "host_setup_env_sha256=$(sha256sum "$HOSTENV" 2>/dev/null | cut -d' ' -f1)"
  awk '/^MemTotal:/{print "mem_total=" $2*1024}/^SwapTotal:/{print "swap_total=" $2*1024}' "$PROC/meminfo"
  echo "dev_max_start=$DEV_MAX_START"; echo "tier_max_start=$TIER_MAX_START"
  for d in "$CG"/*.slice "$CG"/*.slice/dev-*.slice; do
    for f in memory.max memory.min memory.low; do [ -r "$d/$f" ] && echo "$f:${d#"$CG"/}=$(cat "$d/$f")"; done
  done
} >"$MAN"
grep -E '^(HOST_ROLE|DEV_|MDT_EXCLUDED_|DOCKER_SCOPE)' "$HOSTENV" >"$EVID/host-setup.env.snapshot" 2>/dev/null
# ---------- the dummy (2.2): anon grown only by SIGUSR1, its UNPROTECTED part kept hot ----------
read -r -d '' DUMMY_PY <<'PY' || true
import os, random, signal, sys, time
end = time.monotonic() + float(sys.argv[1]); a0, delta, steps, page = (int(x) for x in sys.argv[2:6])
buf = bytearray(a0 + (steps + 1) * delta)
filled = [0]
pending = []
signal.signal(signal.SIGUSR1, lambda *_: pending.append(1))
def fill(n):
    while filled[0] < n:
        chunk = min(1 << 20, n - filled[0])
        buf[filled[0]:filled[0] + chunk] = os.urandom(chunk)
        filled[0] += chunk
fill(a0)
cg = os.environ.get("MDT_DUMMY_CGROUP") or "/sys/fs/cgroup"
def protected():
    try:
        with open(os.path.join(cg, "memory.min")) as fh:
            return min(int(fh.read().strip()), filled[0])
    except (OSError, ValueError):
        return 0   # unreadable min: keep the whole anon hot, the safe direction
hot = 0; last = 0.0
while time.monotonic() < end:
    while pending:
        pending.pop()
        fill(filled[0] + delta)
    tick = time.monotonic()
    if tick - last < 1.0:
        continue
    last = tick
    pages = max(1, (filled[0] - protected()) // page)
    for _ in range(min(pages, 12288)):   # 64 MiB/s at 4 KiB pages: the unprotected tail stays hot
        buf[max(0, filled[0] - 1 - (hot % pages) * page)]
        hot += 1
    for k in range(1024):   # the newest 4 MiB, once a second: the 1 Hz proof that the job runs
        buf[max(0, filled[0] - 1 - k * page)]
PY
D0=$(awk '$1=="anon"{a=$2}$1=="kernel"{k=$2}$1=="shmem"{s=$2}$1=="file_mapped"{f=$2}END{print a+k+s+f+0}' "$CG/dev.slice/memory.stat" 2>/dev/null)
D0=${D0:-0}
A0=$((DEV_MAX_START - D0 - 64 * MIB))
[ "$A0" -lt $((256 * MIB)) ] && A0=$((256 * MIB))
DEEPEST=$((DEV_MAX_START + STEPS * STEP))
dummy_budget=$((WARMUP_RUN_SECONDS + (STEPS + 2) * DWELL)) # warm-up, every ladder step, and the control pass
dummy_deadline=$((2 * dummy_budget)) # twice the computed nominal run budget; finish() removes it first
echo "dummy_a0=$A0" >>"$MAN"; echo "dummy_d0=$D0" >>"$MAN"; echo "dummy_deepest_ceiling=$DEEPEST" >>"$MAN"
echo "dummy_deadline_s=$dummy_deadline" >>"$MAN"
printf 'container=%s\nboot_id=%s\npid=%s\nstarted_utc=%s\nk_tested=%s\ndev_orig=%s\n' \
  "$DUMMY" "$BOOT_ID" "$$" "$START_UTC" "$K0" "$DEV_MAX_START" >>"$STATE" || {
  # M3: no cgroup write without its write-ahead record. hotset setup already WAL-recorded kdamond= and the traps are
  # armed, so finish() still switches it off; no ceiling and no min has been touched at this point.
  echo "accept-run: cannot append to the write-ahead state file $STATE (full or read-only evidence filesystem); the run is aborted before any cgroup write" >&2
  exit 3
}
find_scope() { local d="$CG/dev.slice/$PARENT/docker-$1.scope"; [ -d "$d" ] && printf '%s\n' "$d"; }
scope_stat() { awk -v k="$2" '$1==k{print $2+0}' "$1/memory.stat" 2>/dev/null || echo 0; }
# a stale container of a killed run, by its exact name. M2's lesson holds on this path too: a squeezed dockerd stalls
# `rm` exactly as it stalls the probes, and an unbounded rm here would hang the run BEFORE the dummy exists, with no
# named refusal and no bound to read. Bound it, log the failure, and let the `docker run --name` below decide (a name
# that really survived makes it refuse with exit 3 instead of the run standing still).
rc_rm=0
timeout -k 5 "$RM_TIMEOUT" docker rm -f "$DUMMY" >/dev/null 2>&1 || rc_rm=$?
[ "$rc_rm" = 0 ] || note "the stale-container docker rm -f $DUMMY did not complete (rc $rc_rm, ${RM_TIMEOUT}s bound); the dummy start below decides"
unset rc_rm
# shellcheck disable=SC2086 # DUMMY_PY is one -c argument; the reads below are the argv
id=$(timeout -k 5 120 docker run -d --rm --name "$DUMMY" --label mdt.accept=1 --network none --cpus 1 \
      --cgroup-parent "$PARENT" --memory "$DEEPEST" --memory-swap "$((DEEPEST + 16 * GIB))" --oom-score-adj 1000 \
      -e MDT_DUMMY_CGROUP="$CG/dev.slice/$PARENT" "$IMAGE" python3 -c "$DUMMY_PY" "$dummy_deadline" "$A0" "$STEP" "$STEPS" "$PAGESIZE") \
  || { say "the dummy did not start (the image $IMAGE needs python3)"; exit 3; }
SCOPE=$(find_scope "$id")
if [ -z "$SCOPE" ]; then say "the dummy's scope dev.slice/$PARENT/docker-$id.scope is not in $CG; stopping"; exit 3; fi
if [ "$(rdnum "$SCOPE/memory.max")" != "$DEEPEST" ] || [ "$(rdnum "$SCOPE/memory.swap.max")" != "$((16 * GIB))" ]; then
  say "the dummy is not under $PARENT with memory.max $DEEPEST and 16G swap ($SCOPE); stopping"; exit 3
fi
SCOPE_MIN_WRITTEN=$(rdnum "$SCOPE/memory.min"); SCOPE_MIN_WRITTEN=${SCOPE_MIN_WRITTEN:-0}
say "$((STEPS + 1)) dwells; warm-up ${WARMUP}s, dwell ${DWELL}s; K0=$((K0 / MIB))M; F_installed=$((F_INSTALLED / MIB))M; dev.slice ${DEV_MAX_START} -> ${DEEPEST}"
say "operator part, start now: one gate lane, the cockpit attached and idle, one ssh login."
# ---------- sampling (6): one row per tick, guards and the 5-file drift readback on every tick ----------
pres() { awk '$1=="some"{split($2,a,"="); print a[2]}' "$1" 2>/dev/null; }
presfull() { awk '$1=="full"{split($2,a,"="); print a[2]}' "$1" 2>/dev/null; }
ev() { # max_local,oom,oom_kill: the rise rule needs the LOCAL max (memory.events is hierarchical)
  local m="" o="" k=""
  [ -r "$1/memory.events.local" ] && m=$(awk '$1=="max"{print $2}' "$1/memory.events.local")
  [ -r "$1/memory.events" ] && read -r o k < <(awk '$1=="oom"{o=$2}$1=="oom_kill"{k=$2}END{print o+0, k+0}' "$1/memory.events")
  printf '%s,%s,%s' "$m" "$o" "$k"
}
ucols() {
  local p=$CG/$1
  [ -r "$p/memory.stat" ] || { printf ',,,'; return; }
  awk -v c="$(rdnum "$p/memory.current")" '$1=="active_anon"{a=$2}$1=="active_file"{f=$2}$1=="workingset_refault_anon"{x=$2}$1=="workingset_refault_file"{y=$2}
    END{printf "%s,%d,%d,%d", c, a+f, x, y}' "$p/memory.stat"
}
tier_path() { case $1 in dev) echo dev.slice ;; gates) echo "dev.slice/$PARENT" ;; bg) echo "dev.slice/dev-background.slice" ;; esac; }
containerd_restarts() {
  systemctl_read show -p NRestarts --value containerd.service
  case ${SYSTEMCTL_RC}:${SYSTEMCTL_OUT:-} in
    0:*[!0-9]*|0:) : ;;   # empty or malformed is missing, never a zero restart count
    0:*) printf '%s' "$SYSTEMCTL_OUT" ;;
  esac
}
sum_root_min() {
  local d v s=0
  for d in "$CG"/*/; do v=$(rdnum "${d}memory.min"); [ -n "$v" ] && s=$((s + v)); done
  echo "$s"
}
hdr="utc,elapsed,phase,U,init_current,W,swap_used,pswpin,pswpout,zswpout,zswpwb,dev.current,dev.mem_max,dev.swap_current"
for u in $UNITS; do n=${u%%=*}; hdr+=",$n.current,$n.active,$n.refault_anon,$n.refault_file"; done
hdr+=",system.psi_some_avg10,user.psi_some_avg10"
for t in dev gates bg; do hdr+=",$t.max_local,$t.oom,$t.oom_kill"; done
hdr+=",vmstat_oom_kill,step,k_target,dev_max,tier_max,dummy_min,dummy_unprotected,mem_available,mem_free,root_some_avg10,root_full_avg10"
hdr+=",system.psi_full_avg10,user.psi_full_avg10,cgprofile.psi_some_avg10,cgprofile.psi_full_avg10,containerd_restarts,limit_source,dev.min,tier.min"
echo "$hdr" >"$SAMPLES"
echo "utc,step,probe,kind,rc,seconds,detail" >"$PROBES"
abort() { # <verdict> <cause>: an abort is a step verdict plus a ladder stop (2.4)
  ABORT=1; ABORT_VERDICT=$1; ABORT_CAUSE=$2
  return 1
}
wal_add() { # <line>: the write-ahead record MUST land before the effect it protects (M3). A failed append aborts the
            # run: without it the restore could never prove which value is the kit's, and the raised value would stay.
  printf '%s\n' "$1" >>"$STATE" 2>/dev/null || { note "cannot append to the write-ahead state file $STATE; the run is aborted rather than writing a cgroup file unrecorded"; abort INCONCLUSIVE wal; return 1; }
  return 0
}
psi_holding() { # <slice>: full avg10 >= 5 held for PSI_HOLD seconds (psi-guard's own trigger numbers)
  local v t
  v=$(presfull "$CG/$1/memory.pressure")
  case $v in ''|*[!0-9.]*) abort INCONCLUSIVE "psi_unreadable:$1"; return 2 ;; esac   # fail closed (nit: never "not holding")
  if awk -v v="$v" 'BEGIN{exit !(v >= 5)}'; then
    t=$(cat "$EVID/probes-state/psi-$1.since" 2>/dev/null) || t=""
    [ -z "$t" ] && { date +%s >"$EVID/probes-state/psi-$1.since"; return 1; }
    if [ $(( $(date +%s) - t )) -ge "$PSI_HOLD" ]; then rm -f "$EVID/probes-state/psi-$1.since"; return 0; fi
    return 1
  fi
  rm -f "$EVID/probes-state/psi-$1.since"; return 1
}
LAST_GUARD=0
guard_if_stale() { # L5: run the guard when the last one is >= GUARD_EVERY s old (a fast fake tick pays nothing)
  local now_s; now_s=$(date +%s)
  [ $(( now_s - LAST_GUARD )) -ge "$GUARD_EVERY" ] || return 0
  guard_tick
}
guard_tick() { # 2.4: the host is never squeezed into OOM
  local avail oomk
  avail=$(awk '/^MemAvailable:/{print $2*1024}' "$PROC/meminfo")
  case ${avail:-} in ''|*[!0-9]*) abort FAIL mem_available; return 1 ;; esac
  [ "$avail" -ge $((128 * MIB)) ] || { abort FAIL mem_available; return 1; }
  oomk=$(awk '$1=="oom_kill"{print $2+0}' "$PROC/vmstat" 2>/dev/null)
  case ${oomk:-} in ''|*[!0-9]*) ;; *) [ "$oomk" -gt "$OOM0" ] && { abort FAIL oom_kill; return 1; } ;; esac
  local ph
  LAST_GUARD=$(date +%s)
  psi_holding system.slice; ph=$?
  [ "$ph" = 2 ] && return 1
  [ "$ph" = 0 ] && { abort FAIL psi_full:system.slice; return 1; }
  psi_holding user.slice; ph=$?
  [ "$ph" = 2 ] && return 1
  [ "$ph" = 0 ] && { abort FAIL psi_full:user.slice; return 1; }
  return 0
}
drift_tick() { # every tick reads all five files back (2.3): a foreign value aborts the ladder as drift:<file>
  local f have
  for f in dev.slice/memory.max dev.slice/$PARENT/memory.max dev.slice/memory.min dev.slice/$PARENT/memory.min; do
    have=$(rdnum "$CG/$f")
    case $f in
      *dev-gates.slice/memory.max) [ "$have" = "$TIER_WRITTEN" ] || { abort INCONCLUSIVE "drift:$f"; return 1; } ;;
      dev.slice/memory.max) [ "$have" = "$DEV_WRITTEN" ] || { abort INCONCLUSIVE "drift:$f"; return 1; } ;;
      *dev-gates.slice/memory.min) [ "$have" = "$TIER_MIN_WRITTEN" ] || { abort INCONCLUSIVE "drift:$f"; return 1; } ;;
      *) [ "$have" = "$DEV_MIN_WRITTEN" ] || { abort INCONCLUSIVE "drift:$f"; return 1; } ;;
    esac
  done
  [ -n "$SCOPE" ] || return 0
  have=$(rdnum "$SCOPE/memory.min")
  [ "$have" = "$SCOPE_MIN_WRITTEN" ] || { abort INCONCLUSIVE "drift:scope_min"; return 1; }
  return 0
}
write_row() { # <phase> <step>
  local ph=$1 s=$2 mt mf st sf oomk pin pout zout zwb sum W row avail kt dmax tmax dmin dunprot
  read -r mt mf st sf < <(awk '/^MemTotal:/{a=$2*1024}/^MemFree:/{b=$2*1024}/^SwapTotal:/{c=$2*1024}/^SwapFree:/{d=$2*1024}END{print a+0, b+0, c+0, d+0}' "$PROC/meminfo")
  read -r oomk pin pout zout zwb < <(awk '$1=="oom_kill"{a=$2}$1=="pswpin"{b=$2}$1=="pswpout"{c=$2}$1=="zswpout"{d=$2}$1=="zswpwb"{e=$2}END{print a+0, b+0, c+0, d+0, e+0}' "$PROC/vmstat")
  sum=0; for d in "$CG"/*/; do v=$(rdnum "${d}memory.current"); [ -n "$v" ] && sum=$((sum + v)); done
  W=$(awk -v ps="$PAGESIZE" '$1=="high"{s+=$2}END{print s*ps}' "$PROC/zoneinfo")
  avail=$(awk '/^MemAvailable:/{print $2*1024}' "$PROC/meminfo")
  kt=$((K0 - s * STEP)); [ "$kt" -lt 0 ] && kt=0
  dmax=$(rdnum "$CG/dev.slice/memory.max"); tmax=$(rdnum "$CG/dev.slice/$PARENT/memory.max")
  dmin=$(rdnum "$SCOPE/memory.min"); dunprot=$((SCOPE_ANON - ${dmin:-0})); [ "$dunprot" -lt 0 ] && dunprot=0
  row="$(date -u +%H:%M:%S),$((SECONDS - T0)),$ph,$((mt - mf - sum)),$(rdnum "$CG/init.scope/memory.current"),$W,$((st - sf)),$pin,$pout,$zout,$zwb"
  row+=",$(rdnum "$CG/dev.slice/memory.current"),$dmax,$(rdnum "$CG/dev.slice/memory.swap.current")"
  for u in $UNITS; do row+=",$(ucols "${u#*=}")"; done
  row+=",$(pres "$CG/system.slice/memory.pressure"),$(pres "$CG/user.slice/memory.pressure")"
  for t in dev gates bg; do row+=",$(ev "$CG/$(tier_path $t)")"; done
  row+=",$oomk,$s,$kt,$dmax,$tmax,$(rdnum "$SCOPE/memory.min"),$dunprot,${avail:-},$mf,$(pres "$CG/memory.pressure"),$(presfull "$CG/memory.pressure")"
  row+=",$(presfull "$CG/system.slice/memory.pressure"),$(presfull "$CG/user.slice/memory.pressure")"
  row+=",$(pres "$CG/cgprofile.slice/memory.pressure"),$(presfull "$CG/cgprofile.slice/memory.pressure"),$(containerd_restarts),derived"
  row+=",$(rdnum "$CG/dev.slice/memory.min"),$(rdnum "$CG/dev.slice/$PARENT/memory.min")"
  echo "$row" >>"$SAMPLES"
}
# ---------- probes (3.2, 3.3): the shell records rc, seconds and a key=value detail; the judge decides ----------
probe_var() { # <name> <CMD|TIMEOUT>: the env override, e.g. MDT_ACCEPT_PROBE_CMD_SSHD (names are upper-cased)
  local key="MDT_ACCEPT_$2_$1"; key=${key//[._-]/_}; key=${key^^}
  printf '%s' "${!key:-}"
}
probe_timeout() { local v; v=$(probe_var "$1" PROBE_TIMEOUT); printf '%s' "${v:-${MDT_ACCEPT_PROBE_TIMEOUT:-10}}"; }
us_of_seconds() { # <S.mmm|S> -> microseconds on stdout; rc 1 when it is not a decimal seconds value
  # The `seconds` column is a DECIMAL (S.mmm), so it is never an operand of `$(( ))`: bash reads a leading 0 as OCTAL
  # (`0.045` -> digits 0045 = 37) and a `.` is not an integer operator at all (`0.045: syntax error: invalid
  # arithmetic operator`). Both halves are read base 10 here: whole seconds times 1,000,000 plus the fraction padded
  # or cut to exactly three digits. `<limit>.000` (a timeout) and a bare `<limit>` take the same path.
  local t=$1 w frac
  case $t in
    ''|*[!0-9.]*|.*|*.) return 1 ;;
    *.*) w=${t%%.*}; frac=${t#*.}
         case $frac in ''|*[!0-9]*) return 1 ;; esac
         frac="${frac}000"; frac=${frac:0:3} ;;
    *) w=$t; frac=000 ;;
  esac
  case $w in ''|*[!0-9]*) w=0 ;; esac   # '.500' style is refused above; an empty whole part cannot reach here
  printf '%d' $(( 10#$w * 1000000 + 10#$frac * 1000 ))   # three fraction digits are milliseconds
}
seconds_of_us() { # <microseconds> -> the S.mmm the `seconds` column carries
  printf '%d.%03d' $(($1 / 1000000)) $(( ($1 / 1000) % 1000 ))
}
timed() { # <timeout> <cmd...>: T_RC, T_SECONDS, T_OUT; a timeout is recorded at its full sample length
  local limit=$1 t0 t1 us lim_us; shift
  t0=$(date +%s%N); T_OUT=$(timeout -k 1 "$limit" "$@" 2>&1); T_RC=$?; t1=$(date +%s%N)
  us=$(( (t1 - t0) / 1000 )); [ "$us" -lt 0 ] && us=0
  T_SECONDS=$(seconds_of_us "$us")
  if [ "$T_RC" = 124 ] || [ "$T_RC" = 137 ]; then
    T_RC=timeout
    # the limit rendered as S.mmm through the same decimal reader: `printf '%d.000' "$limit"` cannot render a
    # fractional limit (0.5), so the timeout row's seconds did not carry the limit it was recorded against
    lim_us=$(us_of_seconds "$limit") || lim_us=0
    T_SECONDS=$(seconds_of_us "$lim_us")
  fi
  return 0
}
probe_record() { # <step> <name> <kind> <rc> <seconds> <detail>
  local detail=${6//[$'\n\r,\t']/ }
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ),$1,$2,$3,$4,$5,$detail" >>"$PROBES"
}
probe_run() { # <step> <name>
  local s=$1 n=$2 cmd lim exec_rc nrestarts nrestarts_rc
  cmd=$(probe_var "$n" PROBE_CMD)
  if [ -n "$cmd" ]; then # the seam: per-probe argv/timeout; an overrunning probe is killed and recorded, never a hang
    lim=$(probe_timeout "$n"); timed "$lim" sh -c "$cmd"
    probe_record "$s" "$n" liveness "$T_RC" "$T_SECONDS" "${T_OUT:-rc=$T_RC}"
    return 0
  fi
  case $n in
    ssh)
      timed 15 ssh "${SSH_ARGS[@]}" -o BatchMode=yes -o ConnectTimeout=10 "$SSH_TARGET" true
      probe_record "$s" ssh ssh "$T_RC" "$T_SECONDS" "target=$SSH_TARGET" ;;
    sshd) # 3.3: `active` AND the banner seen; the probe's own rc carries the rule, the judge reads rc.
      # L1: is-active's rc is kept separately -- the banner read overwrites T_RC, so a socket-activated or foreign
      # :22 listener answered a `inactive` ssh.service as a PASS. Both rcs have to be 0 (or the banner seen).
      local act ban brc arc secs secs_us ban_us banner
      timed 2 systemctl is-active ssh.service; act=$T_OUT; arc=$T_RC; secs=$T_SECONDS
      secs_us=$(us_of_seconds "$secs") || secs_us=0
      [ "$act" = active ] || arc=1
      # the banner reader: the real /dev/tcp read, unless a test names its own argv (port 22 cannot be exercised
      # hermetically; the seam keeps L1's two-rc rule testable without a listener)
      banner=${MDT_ACCEPT_SSHD_BANNER_CMD:-}
      [ -n "$banner" ] || banner='exec 3<>/dev/tcp/127.0.0.1/22 && head -c 9 <&3'
      timed 2 bash -c "$banner"; brc=$T_RC
      ban_us=$(us_of_seconds "$T_SECONDS") || ban_us=0   # T_SECONDS is still the banner's read here
      case $T_OUT in *SSH-2.0*) ban=yes ;; *) ban=no ;; esac
      [ "$ban" = yes ] || brc=1
      [ "$arc" = 0 ] && [ "$brc" = 0 ] && [ "$ban" = yes ] && T_RC=0 || T_RC=1
      # the row's seconds is the LARGER of the two commands, compared as integers (microseconds) and printed back as
      # S.mmm: `secs > T_SECONDS` in `$(( ))` made both operands bash arithmetic (a decimal is a syntax error).
      probe_record "$s" sshd liveness "$T_RC" "$(seconds_of_us "$(( secs_us > ban_us ? secs_us : ban_us ))")" "active=$act banner=$ban is_active_rc=$arc banner_rc=$brc" ;;
    journald)
      local nonce tag start
      nonce=$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n'); tag="mdt-accept-$nonce"; start=$(date +%s)
      timed 5 bash -c "logger -t '$tag' '$nonce'; i=0; while [ \$i -lt 50 ]; do journalctl -t '$tag' --since @$start -o cat -n 1 2>/dev/null | grep -q '$nonce' && exit 0; sleep 0.1; i=\$((i+1)); done; exit 1"
      probe_record "$s" journald liveness "$T_RC" "$T_SECONDS" "nonce=$nonce visible=$([ "$T_RC" = 0 ] && echo yes || echo no)" ;;
    dockerd)
      timed "$(probe_timeout dockerd)" sh -c 'exec 2>/dev/null; exec docker info -f "{{.ServerVersion}}"'
      if [ "$T_RC" = 0 ] && [[ "$T_OUT" =~ ^[0-9]+([.][0-9]+)+([+-][A-Za-z0-9.-]+)?$ ]]; then
        : # a successful client response with a version-shaped server value
      elif [ "$T_RC" = 0 ]; then
        T_RC=1
      fi
      probe_record "$s" dockerd liveness "$T_RC" "$T_SECONDS" "version=$T_OUT" ;;
    containerd)
      timed "$(probe_timeout containerd)" docker exec "$DUMMY" true
      exec_rc=$T_RC
      systemctl_read show -p NRestarts --value containerd.service
      nrestarts=""; nrestarts_rc=$SYSTEMCTL_RC
      if [ "$SYSTEMCTL_RC" = 0 ]; then
        case ${SYSTEMCTL_OUT:-} in ''|*[!0-9]*) nrestarts_rc=missing ;; *) nrestarts=$SYSTEMCTL_OUT ;; esac
      fi
      probe_record "$s" containerd liveness "$exec_rc" "$T_SECONDS" \
        "exec=$([ "$exec_rc" = 0 ] && echo true || echo fail) nrestarts=$nrestarts nrestarts_rc=$nrestarts_rc" ;;
    cgprofiled)
      if command -v cgprofilectl >/dev/null 2>&1; then
        timed "$(probe_timeout cgprofiled)" cgprofilectl --json status
        if [ "$T_RC" = 0 ] && printf '%s' "$T_OUT" | grep -q '"version"'; then
          probe_record "$s" cgprofiled liveness 0 "$T_SECONDS" "state=ok"
        else
          probe_record "$s" cgprofiled liveness "$T_RC" "$T_SECONDS" "state=fail"
        fi
      else # the CLI's absence is a NOTE, never a refusal (4.4); recorded degraded, the verdict ignores it
        timed 5 systemctl is-active cgprofiled.service
        probe_record "$s" cgprofiled liveness "$T_RC" "$T_SECONDS" "state=degraded active=$T_OUT systemctl_rc=$T_RC socket=$([ -S /run/cgprofile/control.sock ] && echo yes || echo no)"
      fi ;;
    psi_guard) # 3.3: active AND its utime+stime advanced since the previous sample (user ticks, never a stopwatch)
      local pid cur prev ticks=-1 act prc=0 t0 t1 us arc pidrc
      t0=$(date +%s%N)
      systemctl_read is-active mdt-psi-guard.service; act=$SYSTEMCTL_OUT; arc=$SYSTEMCTL_RC
      systemctl_read show -p MainPID --value mdt-psi-guard.service; pid=$SYSTEMCTL_OUT; pidrc=$SYSTEMCTL_RC
      case ${pid:-} in ''|0|*[!0-9]*) cur="" ;; *) cur=$(proc_cpu_ticks "$PROC/$pid/stat") || cur="" ;; esac
      prev=$(cat "$EVID/probes-state/psi_guard.ticks" 2>/dev/null) || prev=""
      case ${cur:-} in
        ''|*[!0-9]*) ;;                                   # no pid or no stat: ticks stay -1 (unknown, INFO)
        *) echo "$cur" >"$EVID/probes-state/psi_guard.ticks"
           [ -n "${prev:-}" ] && ticks=$((cur - prev)) ;;  # the first sample has no previous one: unknown, not a pass
      esac
      if [ "$arc" = timeout ] || [ "$pidrc" = timeout ]; then prc=timeout; fi
      [ "$act" = active ] || { [ "$prc" = timeout ] || prc=1; }
      if [ "${ticks:--1}" -lt 1 ] && [ "${ticks:--1}" != -1 ] && [ "$prc" != timeout ]; then prc=1; fi
      if [ "${ticks:--1}" = -1 ] && [ "$prc" != timeout ]; then prc=unmeasurable; fi  # missing data, never a pass (L-i)
      # H2: the probe's OWN duration. `SECONDS - T0` was the RUN's elapsed time, which grows by one dwell per step,
      # so the judge's step-0 latency baseline would FAIL a healthy daemon at step 3-4. The judge now exempts
      # psi_guard from the latency rule (its pass rule is "active + ticks advance", 3.3); the column still has to
      # measure the probe and not the run, because every other reader of probes.csv would read that growth as lag.
      t1=$(date +%s%N); us=$(( (t1 - t0) / 1000 )); [ "$us" -lt 0 ] && us=0
      probe_record "$s" psi_guard liveness "$prc" "$(seconds_of_us "$us")" "active=$act ticks=$ticks" ;;
    extra)
      local u st main ls st_rc main_rc ls_rc row_rc
      for u in $EXTRA_UNITS; do
        systemctl_read is-active "$u"; st=$SYSTEMCTL_OUT; st_rc=$SYSTEMCTL_RC
        systemctl_read show -p MainPID --value "$u"; main=$SYSTEMCTL_OUT; main_rc=$SYSTEMCTL_RC
        systemctl_read show -p LoadState --value "$u"; ls=$SYSTEMCTL_OUT; ls_rc=$SYSTEMCTL_RC
        case $main_rc in timeout) main=timeout ;; *) case ${main:-} in ''|0|*[!0-9]*) main=gone ;; *) main=alive ;; esac ;; esac
        case $ls_rc in timeout) ls=timeout ;; 0) [ "$ls" = loaded ] || ls=not-installed ;; *) ls=missing ;; esac
        row_rc=0
        if [ "$st_rc" = timeout ] || [ "$main_rc" = timeout ] || [ "$ls_rc" = timeout ]; then row_rc=timeout; fi
        probe_record "$s" "extra:$u" liveness "$row_rc" "$((SECONDS - T0))" \
          "active=$st active_rc=$st_rc main=$main mainpid_rc=$main_rc loadstate=$ls loadstate_rc=$ls_rc"
      done ;;
    *) probe_record "$s" "$n" liveness 0 0 "unknown-probe" ;;
  esac
}
probe_cycle() { # <step> <i> <judged_ticks>: 8 liveness cycles and 10 ssh samples per judged window
  local s=$1 i=$2 judged=$3 every
  every=$(( judged / 8 )); [ "$every" -lt 1 ] && every=1
  if [ $(( i % every )) -eq 0 ]; then
    local p; for p in sshd journald dockerd containerd cgprofiled psi_guard; do
      guard_if_stale || return 1   # L5: a tick's probes run back to back; the guard runs between them
      probe_run "$s" "$p"
    done
    [ -n "$EXTRA_UNITS" ] && probe_run "$s" extra
  fi
  # L6: ceil(judged / 10), so the window holds the 10 samples the p95 rule is written for (judged/10 gave 12 at
  # 48 ticks, and a 6-step run then judged ">= 2 of 10" over 12).
  every=$(( (judged + 9) / 10 )); [ "$every" -lt 1 ] && every=1
  [ $(( i % every )) -eq 0 ] && probe_run "$s" ssh
  return 0
}
tick() { # <phase> <step> [judged_ticks] [i]
  local ph=$1 s=$2 judged=${3:-0} i=${4:-0}
  guard_tick || return 1
  drift_tick || return 1
  SCOPE_ANON=$(scope_stat "$SCOPE" anon); SCOPE_ANON=${SCOPE_ANON:-0}
  if [ "$ph" = judge ] && { [ "$i" = 1 ] || [ "$HOTSET_EVERY" -le 1 ] || [ $(((i - 1) % HOTSET_EVERY)) -eq 0 ]; }; then
    hotset sample --step "$s" >>"$EVID/hotset-$s.log" 2>&1 || {
      note "the hot-set sample of step $s failed (see $EVID/hotset-$s.log); the step is INCONCLUSIVE"
      abort INCONCLUSIVE damon_sample
      return 1
    }
  fi
  write_row "$ph" "$s"
  if [ "$ph" = judge ]; then
    probe_cycle "$s" "$i" "$judged" || return 1
  fi
  TICKS=$((TICKS + 1))
  if [ "${MAX_TICKS:-0}" -gt 0 ] && [ "$TICKS" -ge "$MAX_TICKS" ]; then abort INCONCLUSIVE ticks; return 1; fi
  if [ "$INTERVAL" != 0 ]; then sleep "$INTERVAL" & SLP=$!; wait "$SLP"; SLP=""; fi
  return 0
}
dwell() { # <phase> <step> <settling_ticks> <judged_ticks>
  local ph=$1 s=$2 n=$3 judged=$4 i=0
  while [ "$i" -lt "$n" ]; do
    i=$((i + 1))
    tick "$ph" "$s" "$judged" "$i" || return 1
  done
  return 0
}
step_write() { # <step>: the per-step sequence of 2.3; the dummy never grows before the ceiling is raised
  local s=$1 target ts anon cur protect smin0 smin rootnew scope_cur got tries=0
  drift_tick || return 1   # a foreign value must never become this step's "original": the WAL would record it and the
                           # restore would write it BACK (2.3's compare-then-restore, checked before the first write)
  target=$((DEV_MAX_START + s * STEP))
  cur=$(rdnum "$CG/dev.slice/memory.max")
  case ${cur:-} in ''|*[!0-9]*) abort INCONCLUSIVE drift:dev.slice/memory.max; return 1 ;; esac
  [ "$target" -gt "$cur" ] || { abort INCONCLUSIVE drift:dev.slice/memory.max; return 1; }
  wal_add "MAX=dev.slice/memory.max|$DEV_MAX_START|$target|$(now)" || return 1
  echo "$target" >"$CG/dev.slice/memory.max" 2>/dev/null && [ "$(rdnum "$CG/dev.slice/memory.max")" = "$target" ] ||
    { abort INCONCLUSIVE write:dev.slice/memory.max; return 1; }
  DEV_WRITTEN=$target
  ts=$(rdnum "$CG/dev.slice/memory.max"); ts=${ts:-0}
  [ "$target" -le "$ts" ] || { abort FAIL tier_above_parent; return 1; }
  wal_add "MAX=dev.slice/$PARENT/memory.max|$TIER_MAX_START|$target|$(now)" || return 1
  echo "$target" >"$CG/dev.slice/$PARENT/memory.max" 2>/dev/null && [ "$(rdnum "$CG/dev.slice/$PARENT/memory.max")" = "$target" ] ||
    { abort INCONCLUSIVE "write:dev.slice/$PARENT/memory.max"; return 1; }
  TIER_WRITTEN=$target
  anon=$(scope_stat "$SCOPE" anon); anon=${anon:-0}   # the baseline BEFORE the signal: after it, the grown value
  timeout -k 5 10 docker kill -s USR1 "$DUMMY" >/dev/null 2>&1 || { abort INCONCLUSIVE no_dummy; return 1; }
  while :; do # the dummy grew by >= STEP - 16M for <= 60 s, else the step is INCONCLUSIVE grow
    tries=$((tries + 1))
    got=$(scope_stat "$SCOPE" anon); got=${got:-0}
    [ "$got" -gt 0 ] && [ $((got - anon)) -ge $((STEP - 16 * MIB)) ] && break
    [ "$tries" -ge "$GROW_TRIES" ] && { abort INCONCLUSIVE grow; return 1; }
    [ "$POLL" != 0 ] && sleep "$POLL"
    guard_if_stale || return 1   # L5: the <= 60 s grow wait is not a gap in the guard
  done
  smin0=$(rdnum "$SCOPE/memory.min"); smin0=${smin0:-0}
  anon=$(scope_stat "$SCOPE" anon); anon=${anon:-0}
  scope_cur=$(rdnum "$SCOPE/memory.current"); scope_cur=${scope_cur:-0}
  # bound 3: what may be protected under the new ceiling is (dev max - 1G - start), capped by the anon
  protect=$((target - GIB - DEV_MIN_START)); [ "$protect" -gt "$anon" ] && protect=$anon; [ "$protect" -lt 0 ] && protect=0
  [ $((DEV_MIN_START + protect)) -le $((target - GIB)) ] || { abort FAIL bound3_dev; return 1; }
  [ $((TIER_MIN_START + protect)) -le "$target" ] || { abort FAIL bound3_tier; return 1; }
  smin=$scope_cur; [ "$anon" -lt "$smin" ] && smin=$anon
  [ "$smin" -le "$scope_cur" ] || { abort FAIL bound3_scope; return 1; }
  # bound 2: Sigma min over root children <= MemTotal - 256M before writes 3-5 (2.4). The dummy's scope is a leaf
  # under the tier, so only its own new protect enters the root sum.
  rootnew=$(( $(sum_root_min) + protect ))
  [ "$rootnew" -le $((MEM_TOTAL - SQUEEZE_UNPROTECTED)) ] || { abort FAIL bound_min; return 1; }
  wal_add "MIN=dev.slice/memory.min|$DEV_MIN_START|$((DEV_MIN_START + protect))|$(now)" || return 1
  echo "$((DEV_MIN_START + protect))" >"$CG/dev.slice/memory.min" 2>/dev/null &&
    [ "$(rdnum "$CG/dev.slice/memory.min")" = "$((DEV_MIN_START + protect))" ] || { abort INCONCLUSIVE write:dev.slice/memory.min; return 1; }
  DEV_MIN_WRITTEN=$(rdnum "$CG/dev.slice/memory.min")
  wal_add "MIN=dev.slice/$PARENT/memory.min|$TIER_MIN_START|$((TIER_MIN_START + protect))|$(now)" || return 1
  echo "$((TIER_MIN_START + protect))" >"$CG/dev.slice/$PARENT/memory.min" 2>/dev/null &&
    [ "$(rdnum "$CG/dev.slice/$PARENT/memory.min")" = "$((TIER_MIN_START + protect))" ] || { abort INCONCLUSIVE "write:dev.slice/$PARENT/memory.min"; return 1; }
  TIER_MIN_WRITTEN=$(rdnum "$CG/dev.slice/$PARENT/memory.min")
  wal_add "MIN=dev.slice/$PARENT/$(basename "$SCOPE")/memory.min|$smin0|$smin|$(now)" || return 1
  echo "$smin" >"$SCOPE/memory.min" 2>/dev/null && [ "$(rdnum "$SCOPE/memory.min")" = "$smin" ] ||
    { abort INCONCLUSIVE write:scope_min; return 1; }
  SCOPE_MIN_WRITTEN=$(rdnum "$SCOPE/memory.min")
  # 5: read back all five, and only then start the settling window; every compared value is the kit's own read-back (L-m)
  [ "$(rdnum "$CG/dev.slice/memory.max")" = "$DEV_WRITTEN" ] || { abort INCONCLUSIVE drift:dev.slice/memory.max; return 1; }
  [ "$(rdnum "$CG/dev.slice/$PARENT/memory.max")" = "$TIER_WRITTEN" ] || { abort INCONCLUSIVE "drift:dev.slice/$PARENT/memory.max"; return 1; }
  [ "$(rdnum "$CG/dev.slice/memory.min")" = "$DEV_MIN_WRITTEN" ] || { abort INCONCLUSIVE drift:dev.slice/memory.min; return 1; }
  [ "$(rdnum "$CG/dev.slice/$PARENT/memory.min")" = "$TIER_MIN_WRITTEN" ] || { abort INCONCLUSIVE "drift:dev.slice/$PARENT/memory.min"; return 1; }
  [ "$(rdnum "$SCOPE/memory.min")" = "$SCOPE_MIN_WRITTEN" ] || { abort INCONCLUSIVE drift:scope_min; return 1; }
  hotset refresh-schemes --step "$s" >/dev/null 2>&1 || {
    note "the hot-set schemes could not be re-enumerated for step $s; stopping before measurement"
    abort INCONCLUSIVE damon_refresh
    return 1
  }
  return 0
}
step_judge() { # <step>: the judge turns this step's rows and probes into verdict/cause/detail; the stop rule reads it
  local s=$1 label
  rm -f "$EVID/steps/$s.env"
  if [ -n "$JUDGE" ]; then
    sh -c "$JUDGE --live-step '$EVID' --step '$s'" >"$EVID/judge-$s.log" 2>&1 || true
  else
    python3 "$HERE/accept-eval.py" --live-step "$EVID" --step "$s" >"$EVID/judge-$s.log" 2>&1 || true
  fi
  if [ ! -s "$EVID/steps/$s.env" ]; then # a judge that writes nothing is missing data, never a pass
    printf 'verdict=INCONCLUSIVE\ncause=missing:judge\ndetail=the judge wrote no verdict (see judge-%s.log)\n' "$s" >"$EVID/steps/$s.env"
  fi
  STEP_VERDICT=$(sed -n 's/^verdict=//p' "$EVID/steps/$s.env" | tail -n 1)
  STEP_CAUSE=$(sed -n 's/^cause=//p' "$EVID/steps/$s.env" | tail -n 1)
  local detail; detail=$(sed -n 's/^detail=//p' "$EVID/steps/$s.env" | tail -n 1)
  label=""; case $STEP_VERDICT in PASS|FAIL) label="K=$(( (K0 - s * STEP) / MIB ))M" ;; esac
  echo "STEP $s${label:+ $label} $STEP_VERDICT cause=${STEP_CAUSE:-<none>} $detail" >>"$STEPSEN"
  echo "accept-run: STEP $s${label:+ $label} $STEP_VERDICT cause=${STEP_CAUSE:-<none>} $detail"
  case $STEP_VERDICT in
    PASS) LAST_PASS_STEP=$s; K_LAST_PASS=$((K0 - s * STEP)) ;;
    FAIL) FIRST_FAIL_STEP=$s; K_FIRST_FAIL=$((K0 - s * STEP)) ;;
    *) ;;
  esac
}
step_mark() { # <step>: an aborted step the shell judged itself (the judge never sees a half-taken step)
  local s=$1 label
  printf 'verdict=%s\ncause=%s\ndetail=aborted before the judged window finished\n' "$ABORT_VERDICT" "$ABORT_CAUSE" >"$EVID/steps/$s.env"
  STEP_VERDICT=$ABORT_VERDICT; STEP_CAUSE=$ABORT_CAUSE
  label=""; case $ABORT_VERDICT in PASS|FAIL) label="K=$(( (K0 - s * STEP) / MIB ))M" ;; esac
  echo "STEP $s${label:+ $label} $ABORT_VERDICT cause=$ABORT_CAUSE (aborted)" >>"$STEPSEN"
  echo "accept-run: STEP $s${label:+ $label} $ABORT_VERDICT cause=$ABORT_CAUSE (aborted)"
  case $ABORT_VERDICT in FAIL) FIRST_FAIL_STEP=$s; K_FIRST_FAIL=$((K0 - s * STEP)) ;; esac
}
# ---------- the ladder (2.5) ----------
if [ "$INTERVAL" = 0 ]; then # a tick-driven run has no wall clock in a verdict: the tick counts are mandatory
  DWELL_TICKS=${MDT_ACCEPT_DWELL_TICKS:?a run with MDT_ACCEPT_INTERVAL=0 needs MDT_ACCEPT_DWELL_TICKS}
else
  DWELL_TICKS=${MDT_ACCEPT_DWELL_TICKS:-$(( DWELL / INTERVAL ))}
fi
# 3.3's psi-guard probe compares two samples: seed the first from the daemon's own counter, so the first JUDGED
# sample already has a previous one (an unseeded first sample would read as "no CPU used").
systemctl_read show -p MainPID --value mdt-psi-guard.service
pg_pid=$SYSTEMCTL_OUT
[ "$SYSTEMCTL_RC" != timeout ] || note "psi_guard initial MainPID read timed out (systemctl rc=timeout); its first probe may be unmeasurable"
case ${pg_pid:-} in ''|0|*[!0-9]*) ;; *) proc_cpu_ticks "$PROC/$pg_pid/stat" >"$EVID/probes-state/psi_guard.ticks" 2>/dev/null || : ;; esac
SETTLE_TICKS=${MDT_ACCEPT_SETTLE_TICKS:-$(( DWELL_TICKS * SETTLE / DWELL ))}
# 4.2: one sample per aggregation interval (8 s), never per tick: a 5 s tick would read one apply twice and skip the
# next, exactly the aliasing N5 removed from the kernel's own refresh_ms.
HOTSET_EVERY=${MDT_ACCEPT_HOTSET_EVERY:-0}
if [ "$HOTSET_EVERY" = 0 ]; then
  if [ "$INTERVAL" = 0 ]; then HOTSET_EVERY=1; else HOTSET_EVERY=$(( (8 + INTERVAL - 1) / INTERVAL )); fi
fi
[ "$HOTSET_EVERY" -lt 1 ] && HOTSET_EVERY=1
[ "$SETTLE_TICKS" -ge "$DWELL_TICKS" ] && SETTLE_TICKS=$((DWELL_TICKS - 1))
[ "$SETTLE_TICKS" -lt 0 ] && SETTLE_TICKS=0
JUDGED_TICKS=$((DWELL_TICKS - SETTLE_TICKS)); [ "$JUDGED_TICKS" -lt 1 ] && JUDGED_TICKS=1
dwell warmup 0 "$WARMUP_TICKS" 0 || step_mark 0
for s in $(seq 0 "$STEPS"); do
  [ -n "$ABORT" ] && break
  if [ "$s" -gt 0 ]; then # step 0 writes nothing: it is the read of the live configuration
    [ $((K0 - s * STEP)) -ge $((F_INSTALLED + 128 * MIB)) ] || { note "K($s) = $((K0 - s * STEP)) < F_installed $F_INSTALLED + 128M: the ladder stops"; break; }
    step_write "$s" || { step_mark "$s"; break; }
  fi
  dwell settle "$s" "$SETTLE_TICKS" 0 || { step_mark "$s"; break; }
  dwell judge "$s" "$JUDGED_TICKS" "$JUDGED_TICKS" || { step_mark "$s"; break; }
  STEPS_RUN=$((STEPS_RUN + 1))
  step_judge "$s"
  case $STEP_VERDICT in
    PASS) continue ;;
    FAIL) break ;;
    *)
      [ "$STEP_CAUSE" = load_not_real ] && continue  # the only non-blocking inconclusive continues the ladder
      [ "$STEP_VERDICT" = INCONCLUSIVE ] && LADDER_INCONCLUSIVE=1
      break ;;
  esac
done
# the DAMON-off control pass (9 risk 3): one dwell at the ceilings in force (the deepest step actually taken),
# with the kdamond off. INFO only, never judged; the hard criteria keep the DAMON-on numbers.
CONTROL_RESULT=not_run
if [ "$CONTROL" != off ] && [ "${DAMON_OK:-1}" = 1 ] && [ -n "$KDM" ] && [ -z "$ABORT" ] && [ -n "$LAST_PASS_STEP" ]; then
  say "the DAMON-off control pass (INFO only)"
  if hotset off >/dev/null 2>&1; then
    if dwell control "$LAST_PASS_STEP" "$SETTLE_TICKS" "$JUDGED_TICKS"; then
      CONTROL_RESULT=complete
    else
      CONTROL_RESULT="aborted:${ABORT_VERDICT:-unknown}:${ABORT_CAUSE:-unknown}"
      note "the control pass was aborted by a guard (INFO only): ${ABORT_CAUSE:-unknown}"
      ABORT=""; ABORT_VERDICT=""; ABORT_CAUSE=""
    fi
    hotset on >/dev/null 2>&1 || { note "the kdamond could not be switched back on"; CONTROL_RESULT="${CONTROL_RESULT}:damon_on_failed"; }
  else
    note "the kdamond could not be switched off; the control pass is skipped"
    CONTROL_RESULT=skipped:damon_off_failed
  fi
fi
# ---------- the run's verdict (3.5): PASS iff a step passed and the floors/M are derivable; FAIL iff step 0 failed ----------
VERDICT=INCONCLUSIVE; VERDICT_RC=4
if [ "$FIRST_FAIL_STEP" = 0 ]; then VERDICT=FAIL; VERDICT_RC=1
elif [ "$ABORT_VERDICT" = INCONCLUSIVE ] || [ "$LADDER_INCONCLUSIVE" = 1 ]; then VERDICT=INCONCLUSIVE; VERDICT_RC=4
elif [ -n "$LAST_PASS_STEP" ]; then VERDICT=PASS; VERDICT_RC=0; fi
{
  echo "control_pass=$CONTROL_RESULT"
  echo "squeeze_steps_run=$STEPS_RUN"
  echo "squeeze_last_pass_step=${LAST_PASS_STEP:-}"
  echo "squeeze_first_fail_step=${FIRST_FAIL_STEP:-}"
  echo "k_last_pass=${K_LAST_PASS:-}"
  echo "k_first_fail=${K_FIRST_FAIL:-}"
  echo "squeeze_verdict=$VERDICT"
} >>"$MAN"
say "$VERDICT ($TICKS ticks)"
EXIT_RC=$VERDICT_RC
exit "$EXIT_RC"
