#!/usr/bin/env bash
# P-plat-19g: one acceptance + measurement run of the dedicated-dev memory model (design note 4.1, 8 step 6).
# Root, ON the host. Starts two capped probe containers, samples every 10 s into <evidence>/samples.csv and writes
# <evidence>/manifest.env; accept-eval.py judges it. Usage:
#   accept-run.sh --evidence DIR [--warmup 600] [--window 1800] [--probe-image REF] [--interactive]
# Test hooks (env): MDT_ACCEPT_{CGROUP,PROC,HOST_ENV,INTERVAL,MAX_TICKS,UNITS,PAGESIZE}.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CG=${MDT_ACCEPT_CGROUP:-/sys/fs/cgroup}; PROC=${MDT_ACCEPT_PROC:-/proc}
HOSTENV=${MDT_ACCEPT_HOST_ENV:-/etc/mdt/host-setup.env}
INTERVAL=${MDT_ACCEPT_INTERVAL:-10}; MAX_TICKS=${MDT_ACCEPT_MAX_TICKS:-0}
PAGESIZE=${MDT_ACCEPT_PAGESIZE:-$(getconf PAGESIZE)}
# name=cgroup path below the root; the floored units of 4.1 (system-slice services, cgprofiled, user.slice, the cockpit tier).
UNITS=${MDT_ACCEPT_UNITS:-"ssh.service=system.slice/ssh.service docker.service=system.slice/docker.service containerd.service=system.slice/containerd.service systemd-journald.service=system.slice/systemd-journald.service mdt-psi-guard.service=system.slice/mdt-psi-guard.service cgprofiled.service=cgprofile.slice/cgprofiled.service user.slice=user.slice dev-interactive.slice=dev.slice/dev-interactive.slice"}
EVID=""; WARMUP=600; WINDOW=1800; IMAGE=${MDT_ACCEPT_PROBE_IMAGE:-tester-unified:local}; INTERACTIVE=0
PROBE_GATES=mdt-accept-probe-gates; PROBE_BG=mdt-accept-probe-bg
usage() { echo "usage: $0 --evidence DIR [--warmup S] [--window S] [--probe-image REF] [--interactive]" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --evidence) EVID=${2:-}; shift 2 ;; --warmup) WARMUP=${2:-}; shift 2 ;; --window) WINDOW=${2:-}; shift 2 ;;
    --probe-image) IMAGE=${2:-}; shift 2 ;; --interactive) INTERACTIVE=1; shift ;; *) usage ;;
  esac
done
[ -n "$EVID" ] || usage
case "$WARMUP$WINDOW" in ''|*[!0-9]*) usage ;; esac
role=$(sed -n 's/^HOST_ROLE=//p' "$HOSTENV" 2>/dev/null | tail -n 1 | tr -d "\"' ")
if [ "$role" != dedicated-dev ]; then echo "accept-run: HOST_ROLE=${role:-<unset>} in $HOSTENV, not dedicated-dev; refusing" >&2; exit 3; fi
mkdir -p "$EVID" || exit 2
# A probe cycling ~3.8G anon plus random reads of a 4G file (4.1); python3 must exist in the image.
read -r -d '' PROBE_PY <<'PY'
import os, random, time
n = 3800 * 2**20; buf = bytearray(n); step = 2**20
for i in range(0, n, 4096): buf[i] = 1
path = "/tmp/probe.dat"; size = 4 * 2**30
with open(path, "wb") as f:
    chunk = os.urandom(step)
    for _ in range(size // step): f.write(chunk)
fd = os.open(path, os.O_RDONLY)
while True:
    for _ in range(256): os.pread(fd, step, random.randrange(0, size - step))
    off = random.randrange(0, n - 64 * step)
    for i in range(off, off + 64 * step, 4096): buf[i] = (buf[i] + 1) & 255
PY
SESSIONS=(); SLP=""; T_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
rdnum() { local v; v=$(cat "$1" 2>/dev/null) || v=""; case $v in ''|*[!0-9]*) echo "" ;; *) echo "$v" ;; esac; }
finish() {
  trap '' INT TERM
  [ -n "$SLP" ] && kill "$SLP" 2>/dev/null
  for s in "${SESSIONS[@]}"; do cgprofilectl stop "$s" >/dev/null 2>&1; done
  for n in "$PROBE_GATES" "$PROBE_BG"; do docker rm -f "$n" >/dev/null 2>&1; done
  echo "end_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$EVID/manifest.env"
}
trap finish EXIT; trap 'exit 130' INT; trap 'exit 143' TERM
{
  echo "host=$(hostname)"; echo "start_utc=$T_START"; echo "kit_sha=$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "warmup=$WARMUP"; echo "window=$WINDOW"; echo "interval=$INTERVAL"; echo "probe_image=$IMAGE"
  for d in "$CG"/*.slice "$CG"/*.slice/dev-*.slice; do [ -r "$d/memory.max" ] && echo "memory.max:${d#"$CG"/}=$(cat "$d/memory.max")"; done
} >"$EVID/manifest.env"
for n in "$PROBE_GATES" "$PROBE_BG"; do docker rm -f "$n" >/dev/null 2>&1; done   # stale probe of a killed run, exact name
if ! docker run -d --name "$PROBE_GATES" --label mdt.accept=1 --memory 4G --memory-swap 12G \
     --cgroup-parent "${CGROUP_PARENT_DEV_GATES:-dev-gates.slice}" "$IMAGE" python3 -c "$PROBE_PY" >/dev/null \
   || ! docker run -d --name "$PROBE_BG" --label mdt.accept=1 --memory 4G --memory-swap 12G \
     --cgroup-parent "${CGROUP_PARENT_DEV_BACKGROUND:-dev-background.slice}" "$IMAGE" python3 -c "$PROBE_PY" >/dev/null; then
  echo "accept-run: a probe container did not start (image $IMAGE needs python3)" >&2; exit 3
fi
if command -v cgprofilectl >/dev/null 2>&1; then
  for u in $UNITS; do
    sid=$(cgprofilectl start --target "cgroup:/${u#*=}" --meta "{\"kit\":\"plat-19g\",\"unit\":\"${u%%=*}\"}" 2>>"$EVID/cgprofile.err") \
      && [ -n "$sid" ] && { SESSIONS+=("$sid"); echo "${u%%=*} $sid" >>"$EVID/cgprofile-sessions.txt"; }
  done
else echo "cgprofile=skipped (cgprofilectl not on PATH)" >>"$EVID/manifest.env"; fi
cat <<MSG
accept-run: probes mdt-accept-probe-gates / mdt-accept-probe-bg running; warm-up ${WARMUP}s then window ${WINDOW}s.
Operator part, start now: one real gate lane, the cockpit attached and idle, an ssh login (user.slice). After the run
measure 'ssh <host> true' idle and loaded for accept-eval.py --ssh-idle/--ssh-loaded.
MSG
[ "$INTERACTIVE" = 1 ] && read -r -p "start those, then press Enter " _ </dev/tty

ev() { awk '$1=="max"{m=$2}$1=="oom"{o=$2}$1=="oom_kill"{k=$2}END{printf "%d,%d,%d", m, o, k}' "$1" 2>/dev/null || printf ',,'; }
pres() { awk '$1=="some"{split($2,a,"="); print a[2]}' "$1" 2>/dev/null; }
ucols() {
  local p=$CG/$1 cur
  [ -r "$p/memory.stat" ] || { printf ',,,'; return; }
  cur=$(rdnum "$p/memory.current")
  awk -v c="$cur" '$1=="active_anon"{a=$2}$1=="active_file"{f=$2}$1=="workingset_refault_anon"{x=$2}$1=="workingset_refault_file"{y=$2}
    END{printf "%s,%d,%d,%d", c, a+f, x, y}' "$p/memory.stat"
}
hdr="utc,elapsed,phase,U,init_current,W"
for u in $UNITS; do n=${u%%=*}; hdr+=",$n.current,$n.active,$n.refault_anon,$n.refault_file"; done
hdr+=",system.psi_some_avg10,user.psi_some_avg10"
for t in dev gates bg; do hdr+=",$t.max,$t.oom,$t.oom_kill"; done
echo "$hdr,vmstat_oom_kill" >"$EVID/samples.csv"
tier_path() { case $1 in dev) echo dev.slice ;; gates) echo dev.slice/dev-gates.slice ;; bg) echo dev.slice/dev-background.slice ;; esac; }
ticks=0; T0=$SECONDS
while :; do
  el=$((SECONDS - T0)); [ "$el" -ge $((WARMUP + WINDOW)) ] && break
  mt=$(awk '/^MemTotal:/{print $2*1024}' "$PROC/meminfo"); mf=$(awk '/^MemFree:/{print $2*1024}' "$PROC/meminfo")
  sum=0; for d in "$CG"/*/; do v=$(rdnum "${d}memory.current"); [ -n "$v" ] && sum=$((sum + v)); done
  W=$(awk -v ps="$PAGESIZE" '$1=="high"{s+=$2}END{print s*ps}' "$PROC/zoneinfo")
  phase=window; [ "$el" -lt "$WARMUP" ] && phase=warmup
  row="$(date -u +%H:%M:%S),$el,$phase,$((mt - mf - sum)),$(rdnum "$CG/init.scope/memory.current"),$W"
  for u in $UNITS; do row+=",$(ucols "${u#*=}")"; done
  row+=",$(pres "$CG/system.slice/memory.pressure"),$(pres "$CG/user.slice/memory.pressure")"
  for t in dev gates bg; do row+=",$(ev "$CG/$(tier_path $t)/memory.events")"; done
  echo "$row,$(awk '$1=="oom_kill"{print $2}' "$PROC/vmstat")" >>"$EVID/samples.csv"
  ticks=$((ticks + 1)); [ "$MAX_TICKS" -gt 0 ] && [ "$ticks" -ge "$MAX_TICKS" ] && break
  if [ "$INTERVAL" != 0 ]; then sleep "$INTERVAL" & SLP=$!; wait "$SLP"; SLP=""; fi
done
echo "ticks=$ticks" >>"$EVID/manifest.env"
