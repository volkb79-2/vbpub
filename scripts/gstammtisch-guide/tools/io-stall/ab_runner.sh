#!/bin/bash
# A/B runner for the IO stall experiment. Runs on the host as root, detached.
# Phases: A0 (no dev limits, defaults), A1 (+dirty bytes), A2 (+nr_requests 32), A3 (+dev caps).
# Restores sysctl/nr_requests to their ORIGINAL values and the dev caps to the pre-test caps at the end.
set -u
D=/var/log/mdt-stall
W=/var/tmp/mdt-loadtest          # on the root ext4 (same filesystem and journal as the game volumes)
PROBE_DIR=/var/lib/pterodactyl/mdt-probe
LOAD_SECS=${LOAD_SECS:-240}
COOL_SECS=${COOL_SECS:-120}
mkdir -p "$W" "$PROBE_DIR"
log() { echo "$(date -u +%FT%TZ) $(date +%s) $*" >> "$D/ab-phases.log"; }

ORIG_DR=$(sysctl -n vm.dirty_ratio); ORIG_DBR=$(sysctl -n vm.dirty_background_ratio)
ORIG_NR=$(cat /sys/block/vda/queue/nr_requests)
log "ORIG dirty_ratio=$ORIG_DR dirty_background_ratio=$ORIG_DBR nr_requests=$ORIG_NR"

restore() {
  sysctl -q -w vm.dirty_ratio="$ORIG_DR" vm.dirty_background_ratio="$ORIG_DBR"
  echo "$ORIG_NR" > /sys/block/vda/queue/nr_requests
  systemctl set-property --runtime dev.slice "IOReadBandwidthMax=/dev/vda 80M" "IOWriteBandwidthMax=/dev/vda 40M" "IOReadIOPSMax=/dev/vda 800" "IOWriteIOPSMax=/dev/vda 600"
  systemctl set-property --runtime dev-gates.slice "IOReadBandwidthMax=/dev/vda 60M" "IOWriteBandwidthMax=/dev/vda 30M" "IOReadIOPSMax=/dev/vda 600" "IOWriteIOPSMax=/dev/vda 450"
  systemctl stop mdt-ab-probe.service mdt-ab-load-seq.service mdt-ab-load-rand.service 2>/dev/null
  rm -rf "$W" "$PROBE_DIR"
  log "RESTORED dirty_ratio=$ORIG_DR/$ORIG_DBR nr_requests=$ORIG_NR dev caps 80M/40M/800/600 gates 60M/30M/600/450"
}
trap restore EXIT

nocaps() { for u in dev.slice dev-gates.slice; do systemctl set-property --runtime $u IOReadBandwidthMax= IOWriteBandwidthMax= IOReadIOPSMax= IOWriteIOPSMax=; done; }

# Probe: game-like log writer (append + fdatasync every 0.5 s), latency per op in ms.
systemd-run -q --unit=mdt-ab-probe --slice=system.slice python3 -c '
import os, time, json
fd = os.open("/var/lib/pterodactyl/mdt-probe/probe.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
out = open("/var/log/mdt-stall/ab-probe.jsonl", "a", buffering=1)
while True:
    t0 = time.monotonic(); os.write(fd, b"x" * 200); t1 = time.monotonic(); os.fdatasync(fd); t2 = time.monotonic()
    out.write(json.dumps({"t": round(time.time(), 3), "w_ms": round((t1 - t0) * 1000, 2), "s_ms": round((t2 - t1) * 1000, 2)}) + "\n")
    time.sleep(max(0, 0.5 - (time.monotonic() - t0)))
'

load_start() {
  # (1) build-like: create new 1 GiB files buffered (new block allocation -> ordered data in the journal)
  systemd-run -q --unit=mdt-ab-load-seq --slice=dev-gates.slice bash -c "i=0; cd $W; while true; do dd if=/dev/urandom of=seq.\$i bs=1M count=1024 status=none; rm -f seq.\$((i-2)); i=\$((i+1)); done"
  # (2) gate-like: many small files with fsync (git/pip/pytest pattern)
  systemd-run -q --unit=mdt-ab-load-rand --slice=dev-gates.slice fio --name=small --directory=$W --rw=randwrite --bs=4k --size=256M --nrfiles=64 --numjobs=2 --fsync=16 --time_based --runtime=$LOAD_SECS --ioengine=psync --direct=0 --group_reporting --output=$D/ab-fio-$PHASE.json --output-format=json
}
load_stop() { systemctl stop mdt-ab-load-seq.service mdt-ab-load-rand.service 2>/dev/null; rm -f $W/seq.*; sync; }

phase() {
  PHASE=$1; export PHASE
  log "PHASE $PHASE START $(sysctl -n vm.dirty_bytes)/$(sysctl -n vm.dirty_background_bytes) nr_requests=$(cat /sys/block/vda/queue/nr_requests) devmax=$(cat /sys/fs/cgroup/dev.slice/io.max | tr '\n' ' ')"
  load_start
  sleep "$LOAD_SECS"
  load_stop
  log "PHASE $PHASE END"
  sleep "$COOL_SECS"
}

log "BASELINE START"; sleep 120; log "BASELINE END"
nocaps
phase A0
sysctl -q -w vm.dirty_bytes=268435456 vm.dirty_background_bytes=67108864
phase A1
echo 32 > /sys/block/vda/queue/nr_requests
phase A2
systemctl set-property --runtime dev.slice "IOReadBandwidthMax=/dev/vda 80M" "IOWriteBandwidthMax=/dev/vda 40M" "IOReadIOPSMax=/dev/vda 800" "IOWriteIOPSMax=/dev/vda 600"
systemctl set-property --runtime dev-gates.slice "IOReadBandwidthMax=/dev/vda 60M" "IOWriteBandwidthMax=/dev/vda 30M" "IOReadIOPSMax=/dev/vda 600" "IOWriteIOPSMax=/dev/vda 450"
phase A3
log "ALL DONE"
