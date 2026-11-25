#!/usr/bin/env bash
# P-plat-19m: one acceptance SQUEEZE run of the dedicated-dev memory model (DESIGN-ACCEPT-KIT-V2.md 2, 3, 7 step 3).
# Root, ON the host, launched DETACHED through `systemd-run` whose ExecStopPost runs accept-restore.sh: a run has to
# outlive an ssh drop, and a SIGKILL or an OOM kill of the unit has to un-squeeze the host. The traps below cover
# EXIT/INT/TERM/HUP only; the write-ahead state file (squeeze-state.env) plus accept-restore.sh cover the rest.
#   accept-run.sh --evidence DIR [--steps N] [--step SIZE] [--dwell S] [--warmup S] [--probe-image REF]
#                  [--control auto|on|off]
# Exit 0 PASS, 1 FAIL (step 0 failed), 4 INCONCLUSIVE, 3 refused, 2 usage.
# Test hooks (env): MDT_ACCEPT_{CGROUP,PROC,HOST_ENV,BOOT_ID,INTERVAL,DWELL_TICKS,SETTLE_TICKS,MAX_TICKS,UNITS,
# PAGESIZE,LOCK,MIN_DISK,SQUEEZE_STATE,HOTSET,HOTSET_JSON,HOTSET_EVERY,DAMON,DAMON_STAT,JUDGE,SSH_TARGET,EXTRA_UNITS,
# PROBE_IMAGE,PROBE_CMD_<NAME>,PROBE_TIMEOUT,PROBE_TIMEOUT_<NAME>,POLL_STEP,GROW_TRIES,PSI_HOLD,RESTORE_TIMEOUT}.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
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
EXTRA_UNITS=${MDT_ACCEPT_EXTRA_UNITS:-}
POLL=${MDT_ACCEPT_POLL_STEP:-1}
GROW_TRIES=${MDT_ACCEPT_GROW_TRIES:-60}
PSI_HOLD=${MDT_ACCEPT_PSI_HOLD:-10}
UNITS=${MDT_ACCEPT_UNITS:-"ssh.service=system.slice/ssh.service docker.service=system.slice/docker.service containerd.service=system.slice/containerd.service systemd-journald.service=system.slice/systemd-journald.service mdt-psi-guard.service=system.slice/mdt-psi-guard.service cgprofiled.service=cgprofile.slice/cgprofiled.service user.slice=user.slice dev-interactive.slice=dev.slice/dev-interactive.slice"}
EVID=""; STEPS=6; STEP=$((128 * MIB)); DWELL=300; SETTLE=60; WARMUP=600
IMAGE=${MDT_ACCEPT_PROBE_IMAGE:-tester-unified:local}; CONTROL=auto
usage() { echo "usage: $0 --evidence DIR [--steps N] [--step SIZE] [--dwell S] [--warmup S] [--probe-image REF] [--control auto|on|off]" >&2; exit 2; }
say() { echo "accept-run: $*"; }
note() { echo "accept-run: NOTE $*"; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
parse_size() { # 128M / 4096 / 2G -> bytes; a bare number is bytes
  local v=$1 n mult
  case $v in *K|*k) mult=1024 ;; *M|*m) mult=$MIB ;; *G|*g) mult=$GIB ;; *) mult=1 ;; esac
  n=${v%[KkMmGg]}
  case $n in ''|*[!0-9]*) return 1 ;; esac
  echo $((n * mult))
}
rdnum() { local v; v=$(cat "$1" 2>/dev/null) || v=""; case $v in ''|*[!0-9]*) echo "" ;; *) echo "$v" ;; esac; }
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
case "$CONTROL" in auto|on|off) ;; *) usage ;; esac
role=$(sed -n 's/^HOST_ROLE=//p' "$HOSTENV" 2>/dev/null | tail -n 1 | tr -d "\"' ")
if [ "$role" != dedicated-dev ]; then echo "accept-run: HOST_ROLE=${role:-<unset>} in $HOSTENV, not dedicated-dev; refusing" >&2; exit 3; fi
# One run at a time. This lock is what pauses the reconcile's memory block (2.3) and makes install.sh refuse; the
# kernel releases it on ANY death of this process, SIGKILL included, so a dead run never leaves the host unpaused.
exec 9>>"$LOCK" || { echo "accept-run: cannot open the lock $LOCK; refusing, nothing done" >&2; exit 3; }
flock -n 9 || { echo "accept-run: another run holds $LOCK; refusing" >&2; exit 3; }
mkdir -p "$EVID/steps" "$EVID/probes-state" || exit 2
STATE=${MDT_ACCEPT_SQUEEZE_STATE:-$EVID/squeeze-state.env}
MAN=$EVID/manifest.env; PROBES=$EVID/probes.csv; SAMPLES=$EVID/samples.csv; STEPSEN=$EVID/steps.env
: >"$STEPSEN"   # the previous run's step lines would otherwise be read as this run's ladder
if [ -s "$STATE" ]; then # a SIGKILLed run left its write-ahead log: restore it first, then use the fresh evidence (2.6)
  say "a state file of an earlier run is present; restoring it first"
  if "$HERE/accept-restore.sh" --evidence "$EVID" --in-run; then note "the earlier state file is restored"
  else note "the earlier lowering is deferred; this run's own WAL covers the rest"; fi
fi
# ---------- preflight: every fact a label or a write depends on, else exit 3 (2.1, 2.2, 3.2, 4.1, 4.4) ----------
command -v python3 >/dev/null 2>&1 || { echo "accept-run: python3 is missing (the judge and the hot-set driver need it); refusing" >&2; exit 3; }
for p in "$HERE/accept-restore.sh" "$HERE/accept-eval.py" "$HOTSET"; do
  [ -f "$p" ] || { echo "accept-run: $p is missing; refusing" >&2; exit 3; }
done
LIB="$HERE/../../../../scripts/damon-analysis/lib"
if [ ! -d "$LIB" ] && [ -z "${MDT_ACCEPT_HOTSET_JSON:-}" ]; then
  echo "accept-run: $LIB is missing (an accept checkout needs mdt-cockpit/ AND scripts/damon-analysis/); refusing" >&2; exit 3
fi
DEV_MAX_START=$(rdnum "$CG/dev.slice/memory.max")
TIER_MAX_START=$(rdnum "$CG/dev.slice/$PARENT/memory.max")
[ -n "$DEV_MAX_START" ] || { echo "accept-run: dev.slice/memory.max is not numeric; the ladder cannot raise it; refusing" >&2; exit 3; }
[ -n "$TIER_MAX_START" ] || { echo "accept-run: dev.slice/$PARENT/memory.max is not numeric (an absent tier would let docker make an unlimited transient slice); refusing" >&2; exit 3; }
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
  v=$(systemctl show -p MemoryMin --value "$s" 2>/dev/null); v=$(parse_size "${v:-}" 2>/dev/null) || v=""
  case $s in system.slice) F_SYSTEM=$v ;; cgprofile.slice) F_CGP=$v ;; user.slice) F_USER=$v ;; esac
done
if [ -z "$F_SYSTEM" ] || [ -z "$F_CGP" ] || [ -z "$F_USER" ]; then
  echo "accept-run: cannot read the MemoryMin of system.slice, cgprofile.slice and user.slice via systemctl; the ladder floor is unknown; refusing" >&2; exit 3
fi
F_INSTALLED=$((F_SYSTEM + F_CGP + F_USER))
[ -n "$SSH_TARGET" ] || { echo "accept-run: MDT_ACCEPT_SSH_TARGET is required (this host over loopback, e.g. root@127.0.0.1); refusing" >&2; exit 3; }
BOOT_ID=$(tr -d '[:space:]' < "$BOOTID_FILE" 2>/dev/null) || BOOT_ID=""
[ -n "$BOOT_ID" ] || { echo "accept-run: cannot read $BOOTID_FILE; refusing (the restore's reboot rule needs it)" >&2; exit 3; }
ssh_boot=$(timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_TARGET" cat "$BOOTID_FILE" 2>/dev/null | tr -d '[:space:]') || ssh_boot=""
if [ "$ssh_boot" != "$BOOT_ID" ]; then
  echo "accept-run: MDT_ACCEPT_SSH_TARGET=$SSH_TARGET reports boot_id '${ssh_boot:-<none>}' but this host is '$BOOT_ID': a remote target would time another machine's sshd (3.2); refusing" >&2
  exit 3
fi
rootdir=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null); avail=$(df -Pk "${rootdir:-/}" 2>/dev/null | awk 'NR==2{print $4}')
[ "${avail:-0}" -ge "${MDT_ACCEPT_MIN_DISK:-4194304}" ] || { echo "accept-run: less than 4G free in the Docker data root; refusing" >&2; exit 3; }
DAMON_STAT_OVERRIDE=${MDT_ACCEPT_DAMON_STAT:-}
HOTSET_ARGS=(--evidence "$EVID" --admin "$DAMON_ADMIN" --state "$STATE" --units "$UNITS" --cgroup "$CG" --proc "$PROC")
[ -d "$LIB" ] && HOTSET_ARGS+=(--lib "$LIB")
[ -n "${MDT_ACCEPT_HOTSET_JSON:-}" ] && HOTSET_ARGS+=(--fixture "$MDT_ACCEPT_HOTSET_JSON")
[ -n "$DAMON_STAT_OVERRIDE" ] && HOTSET_ARGS+=(--damon-stat "$DAMON_STAT_OVERRIDE")
hotset() { python3 "$HOTSET" "$@" "${HOTSET_ARGS[@]}"; }
setup_out=$(hotset setup) || { say "the hot-set preflight refused (see above); nothing was changed"; exit 3; }
DAMON_STATE=$(printf '%s\n' "$setup_out" | sed -n 's/^damon_stat_enabled=//p' | tail -n 1); DAMON_STATE=${DAMON_STATE:-absent}
KDM=$(printf '%s\n' "$setup_out" | sed -n 's/^kdamond=//p' | tail -n 1)
DAMON_OK=1
# ---------- traps and the finish path (2.6) ----------
START_UTC=$(now); T0=$SECONDS; TICKS=0; EXIT_RC=0; FINISHED=""; SLP=""
DEV_WRITTEN=$DEV_MAX_START; TIER_WRITTEN=$TIER_MAX_START
DEV_MIN_WRITTEN=$(rdnum "$CG/dev.slice/memory.min"); DEV_MIN_WRITTEN=${DEV_MIN_WRITTEN:-0}
TIER_MIN_WRITTEN=$(rdnum "$CG/dev.slice/$PARENT/memory.min"); TIER_MIN_WRITTEN=${TIER_MIN_WRITTEN:-0}
SCOPE=""; SCOPE_MIN_WRITTEN=0; SCOPE_ANON=0
OOM0=$(awk '$1=="oom_kill"{print $2+0}' "$PROC/vmstat" 2>/dev/null); OOM0=${OOM0:-0}
ABORT=""; ABORT_VERDICT=""; ABORT_CAUSE=""; STEP_VERDICT=""; STEP_CAUSE=""
LAST_PASS_STEP=""; K_LAST_PASS=""; FIRST_FAIL_STEP=""; K_FIRST_FAIL=""; STEPS_RUN=0
finish() {
  local rc=$?
  trap - EXIT INT TERM HUP
  [ -n "$FINISHED" ] && exit "${EXIT_RC:-$rc}"
  FINISHED=1
  EXIT_RC=${EXIT_RC:-$rc}
  [ -n "$SLP" ] && kill "$SLP" 2>/dev/null
  say "rm -f $DUMMY" # (1) the exact name; --rm is never relied on, and every rm is logged
  docker rm -f "$DUMMY" >/dev/null 2>&1 || true
  if [ -s "$STATE" ]; then
    if "$HERE/accept-restore.sh" --evidence "$EVID" --in-run; then
      echo "restore=ok" >>"$MAN"
    else
      note "a lowering is deferred (RESTORE-PENDING in $MAN); the unlocked reconcile completes it"
      echo "restore=pending" >>"$MAN"
      [ "$EXIT_RC" = 0 ] && EXIT_RC=4 # never a PASS while a lowering is still deferred: an install would apply it
    fi
  else
    echo "restore=ok" >>"$MAN"
  fi
  if command -v journalctl >/dev/null 2>&1; then
    journalctl -k --since "$START_UTC UTC" --no-pager 2>/dev/null | grep -iE 'out of memory|oom-kill' >"$EVID/kernel-oom.txt"
    echo "kernel_oom_lines=$(wc -l <"$EVID/kernel-oom.txt" 2>/dev/null)" >>"$MAN"
  fi
  # 4.1: damon_measure is ok only when no unit's floor was refused; accept-hotset records the refusals one per unit
  if [ -s "$EVID/hotset-refused" ]; then
    echo "damon_measure=failed:$(paste -sd' ' "$EVID/hotset-refused")" >>"$MAN"
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
  echo "squeeze_step=$STEPS"; echo "squeeze_dwell=$DWELL"; echo "step_bytes=$STEP"; echo "warmup=$WARMUP"
  echo "k_tested=$K0"; echo "floors_installed=$F_INSTALLED"
  echo "floors_snapshot=system.slice=$F_SYSTEM cgprofile.slice=$F_CGP user.slice=$F_USER"
  echo "limit_source=derived"; echo "probe_image=$IMAGE"; echo "damon_stat_enabled=$DAMON_STATE"
  [ -n "$KDM" ] && echo "kdamond=$KDM"
  echo "kit_sha=$(git -c safe.directory='*' -C "$HERE" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "kit_sha256=$(cat "$HERE"/accept-run.sh "$HERE"/accept-eval.py "$HERE"/accept-restore.sh 2>/dev/null | sha256sum | cut -d' ' -f1)"
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
dummy_deadline=$((WARMUP + (STEPS + 2) * DWELL + 600)) # a safety cap only: step 0, the control pass and slack are in it
echo "dummy_a0=$A0" >>"$MAN"; echo "dummy_d0=$D0" >>"$MAN"; echo "dummy_deepest_ceiling=$DEEPEST" >>"$MAN"
printf 'container=%s\nboot_id=%s\npid=%s\nstarted_utc=%s\nk_tested=%s\ndev_orig=%s\n' \
  "$DUMMY" "$BOOT_ID" "$$" "$START_UTC" "$K0" "$DEV_MAX_START" >>"$STATE"
find_scope() { local d="$CG/dev.slice/$PARENT/docker-$1.scope"; [ -d "$d" ] && printf '%s\n' "$d"; }
scope_stat() { awk -v k="$2" '$1==k{print $2+0}' "$1/memory.stat" 2>/dev/null || echo 0; }
docker rm -f "$DUMMY" >/dev/null 2>&1 || true # a stale container of a killed run, by its exact name
# shellcheck disable=SC2086 # DUMMY_PY is one -c argument; the reads below are the argv
id=$(docker run -d --rm --name "$DUMMY" --label mdt.accept=1 --network none --cpus 1 \
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
containerd_restarts() { local v; v=$(systemctl show -p NRestarts --value containerd.service 2>/dev/null); case $v in ''|*[!0-9]*) echo "" ;; *) echo "$v" ;; esac; }
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
psi_holding() { # <slice>: full avg10 >= 5 held for PSI_HOLD seconds (psi-guard's own trigger numbers)
  local v t
  v=$(presfull "$CG/$1/memory.pressure"); case $v in ''|*[!0-9.]*) return 1 ;; esac
  if awk -v v="$v" 'BEGIN{exit !(v >= 5)}'; then
    t=$(cat "$EVID/probes-state/psi-$1.since" 2>/dev/null) || t=""
    [ -z "$t" ] && { date +%s >"$EVID/probes-state/psi-$1.since"; return 1; }
    if [ $(( $(date +%s) - t )) -ge "$PSI_HOLD" ]; then rm -f "$EVID/probes-state/psi-$1.since"; return 0; fi
    return 1
  fi
  rm -f "$EVID/probes-state/psi-$1.since"; return 1
}
guard_tick() { # 2.4: the host is never squeezed into OOM
  local avail oomk
  avail=$(awk '/^MemAvailable:/{print $2*1024}' "$PROC/meminfo")
  case ${avail:-} in ''|*[!0-9]*) abort FAIL mem_available; return 1 ;; esac
  [ "$avail" -ge $((128 * MIB)) ] || { abort FAIL mem_available; return 1; }
  oomk=$(awk '$1=="oom_kill"{print $2+0}' "$PROC/vmstat" 2>/dev/null)
  case ${oomk:-} in ''|*[!0-9]*) ;; *) [ "$oomk" -gt "$OOM0" ] && { abort FAIL oom_kill; return 1; } ;; esac
  psi_holding system.slice && { abort FAIL psi_full:system.slice; return 1; }
  psi_holding user.slice && { abort FAIL psi_full:user.slice; return 1; }
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
timed() { # <timeout> <cmd...>: T_RC, T_SECONDS, T_OUT; a timeout is recorded at its full sample length
  local limit=$1 t0 t1 us; shift
  t0=$(date +%s%N); T_OUT=$(timeout -k 1 "$limit" "$@" 2>&1); T_RC=$?; t1=$(date +%s%N)
  us=$(( (t1 - t0) / 1000 )); [ "$us" -lt 0 ] && us=0
  T_SECONDS=$(printf '%d.%03d' $((us / 1000000)) $(( (us / 1000) % 1000 )))
  if [ "$T_RC" = 124 ] || [ "$T_RC" = 137 ]; then T_RC=timeout; T_SECONDS=$(printf '%d.000' "$limit"); fi
  return 0
}
probe_record() { # <step> <name> <kind> <rc> <seconds> <detail>
  local detail=${6//[$'\n\r,\t']/ }
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ),$1,$2,$3,$4,$5,$detail" >>"$PROBES"
}
probe_run() { # <step> <name>
  local s=$1 n=$2 cmd lim
  cmd=$(probe_var "$n" PROBE_CMD)
  if [ -n "$cmd" ]; then # the seam: per-probe argv/timeout; an overrunning probe is killed and recorded, never a hang
    lim=$(probe_timeout "$n"); timed "$lim" sh -c "$cmd"
    probe_record "$s" "$n" liveness "$T_RC" "$T_SECONDS" "${T_OUT:-rc=$T_RC}"
    return 0
  fi
  case $n in
    ssh)
      timed 15 ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_TARGET" true
      probe_record "$s" ssh ssh "$T_RC" "$T_SECONDS" "target=$SSH_TARGET" ;;
    sshd) # 3.3: `active` AND the banner seen; the probe's own rc carries the rule, the judge reads rc
      local act ban brc
      timed 2 systemctl is-active ssh.service; act=$T_OUT; T_RC=0
      [ "$act" = active ] || T_RC=1
      timed 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/22 && head -c 9 <&3'; brc=$T_RC
      case $T_OUT in *SSH-2.0*) ban=yes ;; *) ban=no ;; esac
      [ "$brc" = 0 ] && [ "$ban" = no ] && T_RC=1
      probe_record "$s" sshd liveness "$T_RC" "$T_SECONDS" "active=$act banner=$ban" ;;
    journald)
      local nonce tag start
      nonce=$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n'); tag="mdt-accept-$nonce"; start=$(date +%s)
      timed 5 bash -c "logger -t '$tag' '$nonce'; i=0; while [ \$i -lt 50 ]; do journalctl -t '$tag' --since @$start -o cat -n 1 2>/dev/null | grep -q '$nonce' && exit 0; sleep 0.1; i=\$((i+1)); done; exit 1"
      probe_record "$s" journald liveness "$T_RC" "$T_SECONDS" "nonce=$nonce visible=$([ "$T_RC" = 0 ] && echo yes || echo no)" ;;
    dockerd)
      timed "$(probe_timeout dockerd)" docker info -f '{{.ServerVersion}}'
      [ -n "$T_OUT" ] && [ "$T_RC" != timeout ] && T_RC=0
      probe_record "$s" dockerd liveness "$T_RC" "$T_SECONDS" "version=$T_OUT" ;;
    containerd)
      timed "$(probe_timeout containerd)" docker exec "$DUMMY" true
      probe_record "$s" containerd liveness "$T_RC" "$T_SECONDS" "exec=$([ "$T_RC" = 0 ] && echo true || echo fail) nrestarts=$(containerd_restarts)" ;;
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
        probe_record "$s" cgprofiled liveness 0 "$T_SECONDS" "state=degraded active=$T_OUT socket=$([ -S /run/cgprofile/control.sock ] && echo yes || echo no)"
      fi ;;
    psi_guard) # 3.3: active AND its utime+stime advanced since the previous sample (user ticks, never a stopwatch)
      local pid cur prev ticks=-1 act prc=0
      act=$(systemctl is-active mdt-psi-guard.service 2>/dev/null)
      pid=$(systemctl show -p MainPID --value mdt-psi-guard.service 2>/dev/null)
      case ${pid:-} in ''|0|*[!0-9]*) cur="" ;; *) cur=$(awk '{print $14+$15}' "$PROC/$pid/stat" 2>/dev/null) ;; esac
      prev=$(cat "$EVID/probes-state/psi_guard.ticks" 2>/dev/null) || prev=""
      case ${cur:-} in
        ''|*[!0-9]*) ;;                                   # no pid or no stat: ticks stay -1 (unknown, INFO)
        *) echo "$cur" >"$EVID/probes-state/psi_guard.ticks"
           [ -n "${prev:-}" ] && ticks=$((cur - prev)) ;;  # the first sample has no previous one: unknown, not a pass
      esac
      [ "$act" = active ] || prc=1
      [ "${ticks:--1}" -lt 1 ] && [ "${ticks:--1}" != -1 ] && prc=1   # a loop that burns no CPU does not run
      [ "${ticks:--1}" = -1 ] && prc=unmeasurable                     # no pid: missing data, never a pass (L-i)
      probe_record "$s" psi_guard liveness "$prc" "$((SECONDS - T0))" "active=$act ticks=$ticks" ;;
    extra)
      local u st main ls
      for u in $EXTRA_UNITS; do
        st=$(systemctl is-active "$u" 2>/dev/null); main=$(systemctl show -p MainPID --value "$u" 2>/dev/null)
        ls=$(systemctl show -p LoadState --value "$u" 2>/dev/null)
        case ${main:-} in ''|0|*[!0-9]*) main=gone ;; *) main=alive ;; esac
        [ "$ls" = loaded ] || ls=not-installed
        probe_record "$s" "extra:$u" liveness 0 "$((SECONDS - T0))" "active=$st main=$main loadstate=$ls"
      done ;;
    *) probe_record "$s" "$n" liveness 0 0 "unknown-probe" ;;
  esac
}
probe_cycle() { # <step> <i> <judged_ticks>: 8 liveness cycles and 10 ssh samples per judged window
  local s=$1 i=$2 judged=$3 every
  every=$(( judged / 8 )); [ "$every" -lt 1 ] && every=1
  if [ $(( i % every )) -eq 0 ]; then
    local p; for p in sshd journald dockerd containerd cgprofiled psi_guard; do probe_run "$s" "$p"; done
    [ -n "$EXTRA_UNITS" ] && probe_run "$s" extra
  fi
  every=$(( judged / 10 )); [ "$every" -lt 1 ] && every=1
  [ $(( i % every )) -eq 0 ] && probe_run "$s" ssh
  return 0
}
tick() { # <phase> <step> [judged_ticks] [i]
  local ph=$1 s=$2 judged=${3:-0} i=${4:-0}
  guard_tick || return 1
  drift_tick || return 1
  SCOPE_ANON=$(scope_stat "$SCOPE" anon); SCOPE_ANON=${SCOPE_ANON:-0}
  write_row "$ph" "$s"
  if [ "$ph" = judge ]; then
    probe_cycle "$s" "$i" "$judged"
    if [ "$HOTSET_EVERY" -le 1 ] || [ $(( i % HOTSET_EVERY )) -eq 0 ]; then
      hotset sample --step "$s" >>"$EVID/hotset-$s.log" 2>&1 ||
        note "the hot-set sample of step $s failed (see $EVID/hotset-$s.log)"
    fi
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
  local s=$1 target ts anon cur protect dmin0 tmin0 smin0 smin rootnew scope_cur got tries=0
  drift_tick || return 1   # a foreign value must never become this step's "original": the WAL would record it and the
                           # restore would write it BACK (2.3's compare-then-restore, checked before the first write)
  target=$((DEV_MAX_START + s * STEP))
  cur=$(rdnum "$CG/dev.slice/memory.max")
  case ${cur:-} in ''|*[!0-9]*) abort INCONCLUSIVE drift:dev.slice/memory.max; return 1 ;; esac
  [ "$target" -gt "$cur" ] || { abort INCONCLUSIVE drift:dev.slice/memory.max; return 1; }
  printf 'MAX=%s|%s|%s|%s\n' dev.slice/memory.max "$DEV_MAX_START" "$target" "$(now)" >>"$STATE"
  echo "$target" >"$CG/dev.slice/memory.max" 2>/dev/null && [ "$(rdnum "$CG/dev.slice/memory.max")" = "$target" ] ||
    { abort FAIL write:dev.slice/memory.max; return 1; }
  DEV_WRITTEN=$target
  ts=$(rdnum "$CG/dev.slice/memory.max"); ts=${ts:-0}
  [ "$target" -le "$ts" ] || { abort FAIL tier_above_parent; return 1; }
  printf 'MAX=%s|%s|%s|%s\n' "dev.slice/$PARENT/memory.max" "$TIER_MAX_START" "$target" "$(now)" >>"$STATE"
  echo "$target" >"$CG/dev.slice/$PARENT/memory.max" 2>/dev/null && [ "$(rdnum "$CG/dev.slice/$PARENT/memory.max")" = "$target" ] ||
    { abort FAIL "write:dev.slice/$PARENT/memory.max"; return 1; }
  TIER_WRITTEN=$target
  anon=$(scope_stat "$SCOPE" anon); anon=${anon:-0}   # the baseline BEFORE the signal: after it, the grown value
  docker kill -s USR1 "$DUMMY" >/dev/null 2>&1 || { abort INCONCLUSIVE no_dummy; return 1; }
  while :; do # the dummy grew by >= STEP - 16M for <= 60 s, else the step is INCONCLUSIVE grow
    tries=$((tries + 1))
    got=$(scope_stat "$SCOPE" anon); got=${got:-0}
    [ "$got" -gt 0 ] && [ $((got - anon)) -ge $((STEP - 16 * MIB)) ] && break
    [ "$tries" -ge "$GROW_TRIES" ] && { abort INCONCLUSIVE grow; return 1; }
    [ "$POLL" != 0 ] && sleep "$POLL"
  done
  dmin0=$(rdnum "$CG/dev.slice/memory.min"); dmin0=${dmin0:-0}
  tmin0=$(rdnum "$CG/dev.slice/$PARENT/memory.min"); tmin0=${tmin0:-0}
  smin0=$(rdnum "$SCOPE/memory.min"); smin0=${smin0:-0}
  anon=$(scope_stat "$SCOPE" anon); anon=${anon:-0}
  scope_cur=$(rdnum "$SCOPE/memory.current"); scope_cur=${scope_cur:-0}
  # bound 3: what may be protected under the new ceiling is (dev max - 1G - start), capped by the anon
  protect=$((target - GIB - dmin0)); [ "$protect" -gt "$anon" ] && protect=$anon; [ "$protect" -lt 0 ] && protect=0
  [ $((dmin0 + protect)) -le $((target - GIB)) ] || { abort FAIL bound3_dev; return 1; }
  [ $((tmin0 + protect)) -le "$target" ] || { abort FAIL bound3_tier; return 1; }
  smin=$scope_cur; [ "$anon" -lt "$smin" ] && smin=$anon
  [ "$smin" -le "$scope_cur" ] || { abort FAIL bound3_scope; return 1; }
  # bound 2: Sigma min over root children <= MemTotal - 256M before writes 3-5 (2.4). The dummy's scope is a leaf
  # under the tier, so only its own new protect enters the root sum.
  rootnew=$(( $(sum_root_min) + protect ))
  [ "$rootnew" -le $((MEM_TOTAL - SQUEEZE_UNPROTECTED)) ] || { abort FAIL bound_min; return 1; }
  printf 'MIN=%s|%s|%s|%s\n' dev.slice/memory.min "$dmin0" "$((dmin0 + protect))" "$(now)" >>"$STATE"
  echo "$((dmin0 + protect))" >"$CG/dev.slice/memory.min" 2>/dev/null &&
    [ "$(rdnum "$CG/dev.slice/memory.min")" = "$((dmin0 + protect))" ] || { abort FAIL write:dev.slice/memory.min; return 1; }
  DEV_MIN_WRITTEN=$(rdnum "$CG/dev.slice/memory.min")
  printf 'MIN=%s|%s|%s|%s\n' "dev.slice/$PARENT/memory.min" "$tmin0" "$((tmin0 + protect))" "$(now)" >>"$STATE"
  echo "$((tmin0 + protect))" >"$CG/dev.slice/$PARENT/memory.min" 2>/dev/null &&
    [ "$(rdnum "$CG/dev.slice/$PARENT/memory.min")" = "$((tmin0 + protect))" ] || { abort FAIL "write:dev.slice/$PARENT/memory.min"; return 1; }
  TIER_MIN_WRITTEN=$(rdnum "$CG/dev.slice/$PARENT/memory.min")
  printf 'MIN=%s|%s|%s|%s\n' "dev.slice/$PARENT/$(basename "$SCOPE")/memory.min" "$smin0" "$smin" "$(now)" >>"$STATE"
  echo "$smin" >"$SCOPE/memory.min" 2>/dev/null && [ "$(rdnum "$SCOPE/memory.min")" = "$smin" ] ||
    { abort FAIL write:scope_min; return 1; }
  SCOPE_MIN_WRITTEN=$(rdnum "$SCOPE/memory.min")
  # 5: read back all five, and only then start the settling window; every compared value is the kit's own read-back (L-m)
  [ "$(rdnum "$CG/dev.slice/memory.max")" = "$DEV_WRITTEN" ] || { abort INCONCLUSIVE drift:dev.slice/memory.max; return 1; }
  [ "$(rdnum "$CG/dev.slice/$PARENT/memory.max")" = "$TIER_WRITTEN" ] || { abort INCONCLUSIVE "drift:dev.slice/$PARENT/memory.max"; return 1; }
  [ "$(rdnum "$CG/dev.slice/memory.min")" = "$DEV_MIN_WRITTEN" ] || { abort INCONCLUSIVE drift:dev.slice/memory.min; return 1; }
  [ "$(rdnum "$CG/dev.slice/$PARENT/memory.min")" = "$TIER_MIN_WRITTEN" ] || { abort INCONCLUSIVE "drift:dev.slice/$PARENT/memory.min"; return 1; }
  [ "$(rdnum "$SCOPE/memory.min")" = "$SCOPE_MIN_WRITTEN" ] || { abort INCONCLUSIVE drift:scope_min; return 1; }
  hotset refresh-schemes --step "$s" >/dev/null 2>&1 || note "the hot-set schemes could not be re-enumerated for step $s"
  return 0
}
step_judge() { # <step>: the judge turns this step's rows and probes into verdict/cause/detail; the stop rule reads it
  local s=$1
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
  echo "STEP $s K=$(( (K0 - s * STEP) / MIB ))M $STEP_VERDICT cause=${STEP_CAUSE:-<none>} $detail" >>"$STEPSEN"
  echo "accept-run: STEP $s K=$(( (K0 - s * STEP) / MIB ))M $STEP_VERDICT cause=${STEP_CAUSE:-<none>} $detail"
  case $STEP_VERDICT in
    PASS) LAST_PASS_STEP=$s; K_LAST_PASS=$((K0 - s * STEP)) ;;
    FAIL) FIRST_FAIL_STEP=$s; K_FIRST_FAIL=$((K0 - s * STEP)) ;;
    *) ;;
  esac
}
step_mark() { # <step>: an aborted step the shell judged itself (the judge never sees a half-taken step)
  local s=$1
  printf 'verdict=%s\ncause=%s\ndetail=aborted before the judged window finished\n' "$ABORT_VERDICT" "$ABORT_CAUSE" >"$EVID/steps/$s.env"
  STEP_VERDICT=$ABORT_VERDICT; STEP_CAUSE=$ABORT_CAUSE
  echo "STEP $s K=$(( (K0 - s * STEP) / MIB ))M $ABORT_VERDICT cause=$ABORT_CAUSE (aborted)" >>"$STEPSEN"
  echo "accept-run: STEP $s K=$(( (K0 - s * STEP) / MIB ))M $ABORT_VERDICT cause=$ABORT_CAUSE (aborted)"
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
pg_pid=$(systemctl show -p MainPID --value mdt-psi-guard.service 2>/dev/null)
case ${pg_pid:-} in ''|0|*[!0-9]*) ;; *) awk '{print $14+$15}' "$PROC/$pg_pid/stat" 2>/dev/null >"$EVID/probes-state/psi_guard.ticks" ;; esac
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
dwell warmup 0 "$SETTLE_TICKS" 0 || step_mark 0
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
    *) [ "$STEP_CAUSE" = load_not_real ] && continue; break ;; # only "load was real" continues the ladder
  esac
done
# the DAMON-off control pass (9 risk 3): one dwell at the ceilings in force (the deepest step actually taken),
# with the kdamond off. INFO only, never judged; the hard criteria keep the DAMON-on numbers.
if [ "$CONTROL" != off ] && [ "${DAMON_OK:-1}" = 1 ] && [ -n "$KDM" ] && [ -z "$ABORT" ] && [ -n "$LAST_PASS_STEP" ]; then
  say "the DAMON-off control pass (INFO only)"
  if hotset off >/dev/null 2>&1; then
    dwell control "$LAST_PASS_STEP" "$SETTLE_TICKS" "$JUDGED_TICKS" || note "the control pass was aborted by a guard"
    hotset on >/dev/null 2>&1 || note "the kdamond could not be switched back on"
  else
    note "the kdamond could not be switched off; the control pass is skipped"
  fi
fi
# ---------- the run's verdict (3.5): PASS iff a step passed and the floors/M are derivable; FAIL iff step 0 failed ----------
VERDICT=INCONCLUSIVE; VERDICT_RC=4
if [ "$FIRST_FAIL_STEP" = 0 ]; then VERDICT=FAIL; VERDICT_RC=1
elif [ -n "$LAST_PASS_STEP" ]; then VERDICT=PASS; VERDICT_RC=0; fi
{
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
