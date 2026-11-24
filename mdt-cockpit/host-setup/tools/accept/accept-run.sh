#!/usr/bin/env bash
# P-plat-19g: one acceptance + measurement run of the dedicated-dev memory model (design note 4.1, 8 step 6).
# Root, ON the host, launched DETACHED (systemd-run / nohup): a 40 min run must survive an ssh drop. Starts two capped
# probe containers, samples every 10 s into <evidence>/samples.csv and writes <evidence>/manifest.env; accept-eval.py judges it.
#   accept-run.sh --evidence DIR [--warmup 600] [--window 1800] [--probe-image REF]
# Test hooks (env): MDT_ACCEPT_{CGROUP,PROC,HOST_ENV,INTERVAL,MAX_TICKS,UNITS,PAGESIZE,LOCK,MIN_DISK}.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CG=${MDT_ACCEPT_CGROUP:-/sys/fs/cgroup}; PROC=${MDT_ACCEPT_PROC:-/proc}
HOSTENV=${MDT_ACCEPT_HOST_ENV:-/etc/mdt/host-setup.env}
INTERVAL=${MDT_ACCEPT_INTERVAL:-10}; MAX_TICKS=${MDT_ACCEPT_MAX_TICKS:-0}
PAGESIZE=${MDT_ACCEPT_PAGESIZE:-$(getconf PAGESIZE)}
# name=cgroup path below the root; the floored units of 4.1 (system-slice services, cgprofiled, user.slice, the cockpit tier).
UNITS=${MDT_ACCEPT_UNITS:-"ssh.service=system.slice/ssh.service docker.service=system.slice/docker.service containerd.service=system.slice/containerd.service systemd-journald.service=system.slice/systemd-journald.service mdt-psi-guard.service=system.slice/mdt-psi-guard.service cgprofiled.service=cgprofile.slice/cgprofiled.service user.slice=user.slice dev-interactive.slice=dev.slice/dev-interactive.slice"}
EVID=""; WARMUP=600; WINDOW=1800; IMAGE=${MDT_ACCEPT_PROBE_IMAGE:-tester-unified:local}
PROBE_GATES=mdt-accept-probe-gates; PROBE_BG=mdt-accept-probe-bg
PARENT_GATES=${CGROUP_PARENT_DEV_GATES:-dev-gates.slice}; PARENT_BG=${CGROUP_PARENT_DEV_BACKGROUND:-dev-background.slice}
usage() { echo "usage: $0 --evidence DIR [--warmup S] [--window S] [--probe-image REF]" >&2; exit 2; }
while [ $# -gt 0 ]; do
  [ $# -ge 2 ] || usage
  case "$1" in
    --evidence) EVID=$2 ;; --warmup) WARMUP=$2 ;; --window) WINDOW=$2 ;; --probe-image) IMAGE=$2 ;; *) usage ;;
  esac
  shift 2
done
[ -n "$EVID" ] || usage
case "$WARMUP$WINDOW" in ''|*[!0-9]*) usage ;; esac
role=$(sed -n 's/^HOST_ROLE=//p' "$HOSTENV" 2>/dev/null | tail -n 1 | tr -d "\"' ")
if [ "$role" != dedicated-dev ]; then echo "accept-run: HOST_ROLE=${role:-<unset>} in $HOSTENV, not dedicated-dev; refusing" >&2; exit 3; fi
# One run at a time: a second run's stale-probe removal would kill the first run's probes mid-window.
exec 9>"${MDT_ACCEPT_LOCK:-/run/mdt-accept.lock}" || exit 2
flock -n 9 || { echo "accept-run: another run holds the lock; refusing" >&2; exit 3; }
mkdir -p "$EVID" || exit 2
rdnum() { local v; v=$(cat "$1" 2>/dev/null) || v=""; case $v in ''|*[!0-9]*) echo "" ;; *) echo "$v" ;; esac; }
for p in "$PARENT_GATES" "$PARENT_BG"; do   # an absent tier unit would make docker create an unlimited transient slice
  case $p in dev-*.slice) ;; *) echo "accept-run: cgroup parent $p is not a dev-*.slice; refusing" >&2; exit 3 ;; esac
  [ -n "$(rdnum "$CG/dev.slice/$p/memory.max")" ] || { echo "accept-run: $CG/dev.slice/$p has no numeric memory.max; refusing" >&2; exit 3; }
done
root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null); avail=$(df -P -B1 --output=avail "${root:-/}" 2>/dev/null | tail -n 1 | tr -d ' ')
# Each probe writes a 4G file into its overlay layer: 8G in the Docker data root for the whole run.
[ "${avail:-0}" -ge "${MDT_ACCEPT_MIN_DISK:-12884901888}" ] || { echo "accept-run: less than 12G free in the Docker data root; refusing" >&2; exit 3; }
# A probe: ~3.8G of INCOMPRESSIBLE anon (zswap must not absorb the page-out) cycled by random reads and rewrites, plus random
# reads of a 4G file. argv: deadline seconds (it exits by itself), anon MiB, file MiB, file path. python3 must exist in the image.
read -r -d '' PROBE_PY <<'PY'
import os, random, sys, time
end = time.monotonic() + float(sys.argv[1]); n = int(sys.argv[2]) * 2**20; size = int(sys.argv[3]) * 2**20; step = 2**20
buf = bytearray(n)
for i in range(0, n, step): buf[i:i + step] = os.urandom(step)
chunk = os.urandom(step)
with open(sys.argv[4], "wb") as f:
    for _ in range(size // step): f.write(chunk)
fd = os.open(sys.argv[4], os.O_RDONLY)
while time.monotonic() < end:
    for _ in range(64): os.pread(fd, step, random.randrange(0, size - step + 1))
    for _ in range(64):
        o = random.randrange(0, n - 65536 + 1); buf[o:o + 65536] = os.urandom(65536)
    for _ in range(512): buf[random.randrange(n)]
PY
PROBE_ARGS="$((WARMUP + WINDOW + 300)) 3800 4096 /tmp/probe.dat"
SLP=""; T_START=$(date -u +'%Y-%m-%d %H:%M:%S')
finish() {
  trap '' INT TERM HUP
  [ -n "$SLP" ] && kill "$SLP" 2>/dev/null
  for p in gates bg; do   # liveness first: an early-dead probe means the load was not real
    n=mdt-accept-probe-$p; echo "probe_${p}_end=$(docker inspect -f '{{.State.Running}} {{.State.OOMKilled}}' "$n" 2>/dev/null || echo gone)" >>"$EVID/manifest.env"
  done
  for n in "$PROBE_GATES" "$PROBE_BG"; do docker rm -f "$n" >/dev/null 2>&1; done
  if command -v journalctl >/dev/null 2>&1; then
    journalctl -k --since "$T_START UTC" --no-pager 2>/dev/null | grep -iE 'out of memory|oom-kill' >"$EVID/kernel-oom.txt"
    echo "kernel_oom_lines=$(wc -l <"$EVID/kernel-oom.txt" 2>/dev/null)" >>"$EVID/manifest.env"
  fi
  echo "end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$EVID/manifest.env"
}
trap finish EXIT; trap 'exit 130' INT; trap 'exit 143' TERM; trap 'exit 129' HUP
{
  echo "host=$(hostname)"; echo "start_utc=$T_START"; echo "warmup=$WARMUP"; echo "window=$WINDOW"; echo "interval=$INTERVAL"; echo "probe_image=$IMAGE"
  echo "kit_sha=$(git -c safe.directory='*' -C "$HERE" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "kit_sha256=$(cat "$HERE"/accept-run.sh "$HERE"/accept-eval.py | sha256sum | cut -d' ' -f1)"
  awk '/^MemTotal:/{print "mem_total=" $2*1024}/^SwapTotal:/{print "swap_total=" $2*1024}' "$PROC/meminfo"
  for d in "$CG"/*.slice "$CG"/*.slice/dev-*.slice; do   # the limits under test (K): max, min and low of every slice
    for f in memory.max memory.min memory.low; do [ -r "$d/$f" ] && echo "$f:${d#"$CG"/}=$(cat "$d/$f")"; done
  done
  echo "cgprofile=not captured (CP-55: the daemon refuses cgroup targets)"
} >"$EVID/manifest.env"
grep -E '^(HOST_ROLE|DEV_|MDT_EXCLUDED_|DOCKER_SCOPE)' "$HOSTENV" >"$EVID/host-setup.env.snapshot" 2>/dev/null
for n in "$PROBE_GATES" "$PROBE_BG"; do docker rm -f "$n" >/dev/null 2>&1; done   # stale probe of a killed run, exact name
start_probe() {   # name parent: start, then verify placement and caps; any mismatch exits 3 (finish removes both probes)
  local id d
  # shellcheck disable=SC2086
  id=$(docker run -d --rm --name "$1" --label mdt.accept=1 --memory 4G --memory-swap 12G --cpus 1 --network none \
        --cgroup-parent "$2" "$IMAGE" python3 -c "$PROBE_PY" $PROBE_ARGS) || { echo "accept-run: probe $1 did not start (image $IMAGE needs python3)" >&2; exit 3; }
  d="$CG/dev.slice/$2/docker-$id.scope"
  if [ "$(rdnum "$d/memory.max")" != 4294967296 ] || [ "$(rdnum "$d/memory.swap.max")" != 8589934592 ]; then
    echo "accept-run: probe $1 is not under $2 with memory.max 4G and swap 8G ($d); stopping" >&2; exit 3
  fi
}
start_probe "$PROBE_GATES" "$PARENT_GATES"; start_probe "$PROBE_BG" "$PARENT_BG"
cat <<MSG
accept-run: probes running; warm-up ${WARMUP}s then window ${WINDOW}s. Operator part, start now: one real gate lane, the
cockpit attached and idle, an ssh login (user.slice); time 'ssh <host> true' idle and loaded for $EVID/ssh.txt.
MSG

ev() {   # max_local,oom,oom_kill: the rise rule needs the LOCAL max (memory.events is hierarchical and counts the probes' own container limits)
  local m="" o="" k=""
  [ -r "$1/memory.events.local" ] && m=$(awk '$1=="max"{print $2}' "$1/memory.events.local")
  [ -r "$1/memory.events" ] && read -r o k < <(awk '$1=="oom"{o=$2}$1=="oom_kill"{k=$2}END{print o+0, k+0}' "$1/memory.events")
  printf '%s,%s,%s' "$m" "$o" "$k"
}
pres() { awk '$1=="some"{split($2,a,"="); print a[2]}' "$1" 2>/dev/null; }
ucols() {
  local p=$CG/$1
  [ -r "$p/memory.stat" ] || { printf ',,,'; return; }
  awk -v c="$(rdnum "$p/memory.current")" '$1=="active_anon"{a=$2}$1=="active_file"{f=$2}$1=="workingset_refault_anon"{x=$2}$1=="workingset_refault_file"{y=$2}
    END{printf "%s,%d,%d,%d", c, a+f, x, y}' "$p/memory.stat"
}
hdr="utc,elapsed,phase,U,init_current,W,swap_used,pswpin,pswpout,zswpout,zswpwb,dev.current,dev.mem_max,dev.swap_current"
for u in $UNITS; do n=${u%%=*}; hdr+=",$n.current,$n.active,$n.refault_anon,$n.refault_file"; done
hdr+=",system.psi_some_avg10,user.psi_some_avg10"
for t in dev gates bg; do hdr+=",$t.max_local,$t.oom,$t.oom_kill"; done
echo "$hdr,vmstat_oom_kill" >"$EVID/samples.csv"
tier_path() { case $1 in dev) echo dev.slice ;; gates) echo "dev.slice/$PARENT_GATES" ;; bg) echo "dev.slice/$PARENT_BG" ;; esac; }
ticks=0; T0=$SECONDS
while :; do
  el=$((SECONDS - T0)); [ "$el" -ge $((WARMUP + WINDOW)) ] && break
  read -r mt mf st sf < <(awk '/^MemTotal:/{a=$2*1024}/^MemFree:/{b=$2*1024}/^SwapTotal:/{c=$2*1024}/^SwapFree:/{d=$2*1024}END{print a+0, b+0, c+0, d+0}' "$PROC/meminfo")
  read -r oomk pin pout zout zwb < <(awk '$1=="oom_kill"{a=$2}$1=="pswpin"{b=$2}$1=="pswpout"{c=$2}$1=="zswpout"{d=$2}$1=="zswpwb"{e=$2}END{print a+0, b+0, c+0, d+0, e+0}' "$PROC/vmstat")
  sum=0; for d in "$CG"/*/; do v=$(rdnum "${d}memory.current"); [ -n "$v" ] && sum=$((sum + v)); done
  W=$(awk -v ps="$PAGESIZE" '$1=="high"{s+=$2}END{print s*ps}' "$PROC/zoneinfo")
  phase=window; [ "$el" -lt "$WARMUP" ] && phase=warmup
  row="$(date -u +%H:%M:%S),$el,$phase,$((mt - mf - sum)),$(rdnum "$CG/init.scope/memory.current"),$W,$((st - sf)),$pin,$pout,$zout,$zwb"
  row+=",$(rdnum "$CG/dev.slice/memory.current"),$(rdnum "$CG/dev.slice/memory.max"),$(rdnum "$CG/dev.slice/memory.swap.current")"
  for u in $UNITS; do row+=",$(ucols "${u#*=}")"; done
  row+=",$(pres "$CG/system.slice/memory.pressure"),$(pres "$CG/user.slice/memory.pressure")"
  for t in dev gates bg; do row+=",$(ev "$CG/$(tier_path $t)")"; done
  echo "$row,$oomk" >>"$EVID/samples.csv"
  ticks=$((ticks + 1)); [ "$MAX_TICKS" -gt 0 ] && [ "$ticks" -ge "$MAX_TICKS" ] && break
  if [ "$INTERVAL" != 0 ]; then sleep "$INTERVAL" & SLP=$!; wait "$SLP"; SLP=""; fi
done
echo "ticks=$ticks" >>"$EVID/manifest.env"
