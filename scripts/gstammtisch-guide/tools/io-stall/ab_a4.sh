#!/bin/bash
# Phase A4: bound the bytes in flight (small dirty limits, small queue, small requests), no dev caps.
# Restores everything afterwards (same as ab_runner.sh's restore).
set -u
D=/var/log/mdt-stall; W=/var/tmp/mdt-loadtest; PROBE_DIR=/var/lib/pterodactyl/mdt-probe
LOAD_SECS=240; COOL_SECS=90
mkdir -p "$W" "$PROBE_DIR"
log() { echo "$(date -u +%FT%TZ) $(date +%s) $*" >> "$D/ab-phases.log"; }
ORIG_MSK=$(cat /sys/block/vda/queue/max_sectors_kb); ORIG_NR=$(cat /sys/block/vda/queue/nr_requests)
restore() {
  sysctl -q -w vm.dirty_ratio=20 vm.dirty_background_ratio=10
  echo "$ORIG_NR" > /sys/block/vda/queue/nr_requests
  echo "$ORIG_MSK" > /sys/block/vda/queue/max_sectors_kb
  systemctl set-property --runtime dev.slice "IOReadBandwidthMax=/dev/vda 80M" "IOWriteBandwidthMax=/dev/vda 40M" "IOReadIOPSMax=/dev/vda 800" "IOWriteIOPSMax=/dev/vda 600"
  systemctl set-property --runtime dev-gates.slice "IOReadBandwidthMax=/dev/vda 60M" "IOWriteBandwidthMax=/dev/vda 30M" "IOReadIOPSMax=/dev/vda 600" "IOWriteIOPSMax=/dev/vda 450"
  systemctl stop mdt-ab-probe.service mdt-ab-load-seq.service mdt-ab-load-rand.service 2>/dev/null
  rm -rf "$W" "$PROBE_DIR"
  log "RESTORED (A4) dirty_ratio=20/10 nr_requests=$ORIG_NR max_sectors_kb=$ORIG_MSK dev caps 80M/40M/800/600 gates 60M/30M/600/450"
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
sysctl -q -w vm.dirty_bytes=67108864 vm.dirty_background_bytes=16777216
echo 16 > /sys/block/vda/queue/nr_requests
echo 256 > /sys/block/vda/queue/max_sectors_kb
PHASE=A4
log "PHASE A4 START $(sysctl -n vm.dirty_bytes)/$(sysctl -n vm.dirty_background_bytes) nr_requests=$(cat /sys/block/vda/queue/nr_requests) max_sectors_kb=$(cat /sys/block/vda/queue/max_sectors_kb) devmax=$(cat /sys/fs/cgroup/dev.slice/io.max | tr '\n' ' ')"
systemd-run -q --unit=mdt-ab-load-seq --slice=dev-gates.slice bash -c "i=0; cd $W; while true; do dd if=/dev/urandom of=seq.\$i bs=1M count=1024 status=none; rm -f seq.\$((i-2)); i=\$((i+1)); done"
systemd-run -q --unit=mdt-ab-load-rand --slice=dev-gates.slice fio --name=small --directory=$W --rw=randwrite --bs=4k --size=256M --nrfiles=64 --numjobs=2 --fsync=16 --time_based --runtime=$LOAD_SECS --ioengine=psync --direct=0 --group_reporting --output=$D/ab-fio-$PHASE.json --output-format=json
sleep $LOAD_SECS
systemctl stop mdt-ab-load-seq.service mdt-ab-load-rand.service 2>/dev/null; rm -f $W/seq.*
log "PHASE A4 END"
sleep $COOL_SECS
log "A4 DONE"
