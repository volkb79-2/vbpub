#!/bin/bash
# A4 matrix: short cells with bounded bytes in flight (dirty limit x queue depth x request size), no dev caps.
# Each cell: set knobs, read them back, drain leftover dirty data, quiet gap, LOAD_SECS of the A/B load.
# REF (= A4) runs first and last to measure drift (device burst/refill, neighbours).
# Restores the values found at start (and the pre-test dev caps) on any exit.
set -u
D=/var/log/mdt-stall; W=/var/tmp/mdt-loadtest; PROBE_DIR=/var/lib/pterodactyl/mdt-probe
Q=/sys/block/vda/queue
LOAD_SECS=${LOAD_SECS:-60}; QUIET_SECS=${QUIET_SECS:-20}; DRAIN_MAX=${DRAIN_MAX:-180}
mkdir -p "$W" "$PROBE_DIR"
log() { echo "$(date -u +%FT%TZ) $(date +%s) $*" >> "$D/ab-phases.log"; }

ORIG_DR=$(sysctl -n vm.dirty_ratio); ORIG_DBR=$(sysctl -n vm.dirty_background_ratio)
ORIG_DB=$(sysctl -n vm.dirty_bytes); ORIG_DBB=$(sysctl -n vm.dirty_background_bytes)
ORIG_NR=$(cat $Q/nr_requests); ORIG_MSK=$(cat $Q/max_sectors_kb)
if [ "$ORIG_DB" != 0 ] || [ "$ORIG_DBB" != 0 ]; then
  log "MATRIX REFUSED: dirty_bytes=$ORIG_DB dirty_background_bytes=$ORIG_DBB are set (expected ratio mode)"; exit 2
fi
log "MATRIX ORIG dirty_ratio=$ORIG_DR/$ORIG_DBR nr_requests=$ORIG_NR max_sectors_kb=$ORIG_MSK load=${LOAD_SECS}s"

restore() {
  sysctl -q -w vm.dirty_ratio="$ORIG_DR" vm.dirty_background_ratio="$ORIG_DBR"
  echo "$ORIG_NR" > $Q/nr_requests
  echo "$ORIG_MSK" > $Q/max_sectors_kb
  systemctl set-property --runtime dev.slice "IOReadBandwidthMax=/dev/vda 80M" "IOWriteBandwidthMax=/dev/vda 40M" "IOReadIOPSMax=/dev/vda 800" "IOWriteIOPSMax=/dev/vda 600"
  systemctl set-property --runtime dev-gates.slice "IOReadBandwidthMax=/dev/vda 60M" "IOWriteBandwidthMax=/dev/vda 30M" "IOReadIOPSMax=/dev/vda 600" "IOWriteIOPSMax=/dev/vda 450"
  systemctl stop mdt-ab-probe.service mdt-ab-load-seq.service mdt-ab-load-rand.service 2>/dev/null
  systemctl reset-failed mdt-ab-probe.service mdt-ab-load-seq.service mdt-ab-load-rand.service 2>/dev/null
  rm -rf "$W" "$PROBE_DIR"
  log "RESTORED (MATRIX) dirty_ratio=$(sysctl -n vm.dirty_ratio)/$(sysctl -n vm.dirty_background_ratio) dirty_bytes=$(sysctl -n vm.dirty_bytes) nr_requests=$(cat $Q/nr_requests) max_sectors_kb=$(cat $Q/max_sectors_kb) dev=$(cat /sys/fs/cgroup/dev.slice/io.max | tr '\n' ' ')"
}
trap restore EXIT

systemctl is-active -q mdt-ab-probe.service || systemd-run -q --unit=mdt-ab-probe --slice=system.slice python3 -c '
import os, time, json
fd = os.open("/var/lib/pterodactyl/mdt-probe/probe.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
out = open("/var/log/mdt-stall/ab-probe.jsonl", "a", buffering=1)
while True:
    t0 = time.monotonic(); os.write(fd, b"x" * 200); t1 = time.monotonic(); os.fdatasync(fd); t2 = time.monotonic()
    out.write(json.dumps({"t": round(time.time(), 3), "w_ms": round((t1 - t0) * 1000, 2), "s_ms": round((t2 - t1) * 1000, 2)}) + "\n")
    time.sleep(max(0, 0.5 - (time.monotonic() - t0)))
'
for u in dev.slice dev-gates.slice; do systemctl set-property --runtime $u IOReadBandwidthMax= IOWriteBandwidthMax= IOReadIOPSMax= IOWriteIOPSMax=; done

drain() {  # wait until dirty + writeback pages are below 4 MiB, at most DRAIN_MAX seconds
  local t0 b; t0=$(date +%s)
  while :; do
    b=$(awk '/^nr_dirty |^nr_writeback /{s+=$2} END{print s*4096}' /proc/vmstat)
    [ "$b" -lt 4194304 ] && return 0
    if [ $(( $(date +%s) - t0 )) -ge "$DRAIN_MAX" ]; then log "DRAIN TIMEOUT dirty+writeback=$b"; return 0; fi
    sleep 1
  done
}

cell() {
  local name=$1 db=$2 dbb=$3 nr=$4 msk=$5
  sysctl -q -w vm.dirty_bytes="$db" vm.dirty_background_bytes="$dbb"
  echo "$msk" > $Q/max_sectors_kb; echo "$nr" > $Q/nr_requests
  local g_db g_dbb g_nr g_msk
  g_db=$(sysctl -n vm.dirty_bytes); g_dbb=$(sysctl -n vm.dirty_background_bytes); g_nr=$(cat $Q/nr_requests); g_msk=$(cat $Q/max_sectors_kb)
  if [ "$g_db/$g_dbb/$g_nr/$g_msk" != "$db/$dbb/$nr/$msk" ]; then
    log "SKIP $name: wanted $db/$dbb/$nr/$msk, kernel has $g_db/$g_dbb/$g_nr/$g_msk"; return
  fi
  drain; sleep "$QUIET_SECS"
  log "PHASE $name START $g_db/$g_dbb nr_requests=$g_nr max_sectors_kb=$g_msk"
  systemd-run -q --unit=mdt-ab-load-seq --slice=dev-gates.slice bash -c "i=0; cd $W; while true; do dd if=/dev/urandom of=seq.\$i bs=1M count=1024 status=none; rm -f seq.\$((i-2)); i=\$((i+1)); done"
  systemd-run -q --unit=mdt-ab-load-rand --slice=dev-gates.slice fio --name=small --directory=$W --rw=randwrite --bs=4k --size=256M --nrfiles=64 --numjobs=2 --fsync=16 --time_based --runtime=$LOAD_SECS --ioengine=psync --direct=0 --group_reporting --output=$D/ab-fio-$name.json --output-format=json
  sleep "$LOAD_SECS"
  local n=0
  while systemctl is-active -q mdt-ab-load-rand.service && [ $n -lt 60 ]; do sleep 1; n=$((n + 1)); done
  systemctl stop mdt-ab-load-seq.service mdt-ab-load-rand.service 2>/dev/null
  systemctl reset-failed mdt-ab-load-seq.service mdt-ab-load-rand.service 2>/dev/null
  rm -f $W/seq.*
  log "PHASE $name END fio_wait=${n}s"
}

M=16777216; M4=4194304; M64=67108864
cell REF1-d64-q16-r256   $M64 $M     16 256
cell d16-q4-r1024        $M   $M4    4  1024
cell d16-q16-r256        $M   $M4    16 256
cell d16-q8-r512         $M   $M4    8  512
cell d64-q8-r1024        $M64 $M     8  1024
cell d16-q4-r256         $M   $M4    4  256
cell d16-q16-r1024       $M   $M4    16 1024
cell d16-q8-r256         $M   $M4    8  256
cell d64-q8-r512         $M64 $M     8  512
cell d16-q4-r512         $M   $M4    4  512
cell d16-q8-r1024        $M   $M4    8  1024
cell d16-q16-r512        $M   $M4    16 512
cell REF2-d64-q16-r256   $M64 $M     16 256
drain
log "MATRIX DONE"
