#!/usr/bin/env bash
# Measure the disk's random-read IOPS ceiling with fio and cache the result at
# /var/lib/gstammtisch/io-baseline.env (RIOPS_MAX=<n>).
#
# Consumers:
#   - setup-cgroups.sh   → BENCH_RIOPS = 2/3 × RIOPS_MAX (bench/buildkit io.max)
#   - ciu governance     → derives blkio read-iops caps for besteffort stacks
#
# ⚠ Generates ~10s of saturating random-read I/O (plus a one-time 1G layout
#   write for the test file). Run while the game server is STOPPED, or accept
#   possible in-game lag. Deliberately NOT run automatically at boot — the game
#   cold-loads from disk at boot and would fight the benchmark.
#
# Usage: io-baseline.sh [--force]
#   Refuses to re-measure if the cached result is <30 days old, unless --force.
set -euo pipefail

OUT=/var/lib/gstammtisch/io-baseline.env
TESTFILE="${IO_BASELINE_TESTFILE:-/var/lib/pterodactyl/io-baseline.testfile}"
RUNTIME="${IO_BASELINE_RUNTIME:-10}"

[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
command -v fio >/dev/null || { echo "fio not installed (apt install fio)"; exit 1; }

if [ -f "$OUT" ] && [ "${1:-}" != "--force" ]; then
  age_days=$(( ( $(date +%s) - $(stat -c %Y "$OUT") ) / 86400 ))
  if [ "$age_days" -lt 30 ]; then
    echo "existing baseline is ${age_days}d old (<30d): $(cat "$OUT")"
    echo "use --force to re-measure"
    exit 0
  fi
fi

# Warn loudly if the game is running — the test will contend with it.
for c in $(docker ps -q 2>/dev/null); do
  if docker top "$c" 2>/dev/null | grep -q 'WSServer-Linux-Shipping'; then
    echo "WARNING: Soulmask is RUNNING — this test may cause in-game lag."
    echo "         Ctrl-C within 5s to abort..."
    sleep 5
    break
  fi
done

mkdir -p "$(dirname "$OUT")"
TMP_JSON=$(mktemp)
trap 'rm -f "$TMP_JSON" "$TESTFILE"' EXIT

# libaio so iodepth=32 is a real async queue depth. fio's default psync engine
# silently caps the queue at 1 ("note: ... queue depth will be capped at 1"),
# which measures single-request latency (~7k IOPS here), not the device ceiling.
ENGINE=libaio
fio --enghelp 2>/dev/null | grep -qw libaio || {
  ENGINE=psync
  echo "WARN: libaio engine unavailable — psync fallback measures queue-depth-1 latency, not the true IOPS ceiling"
}

echo "fio randread: ${RUNTIME}s, 4k, direct=1, ioengine=$ENGINE, iodepth=32, file=$TESTFILE (1G)"
# --output: fio's report goes to a file; NOTE: fio may still prepend informational
# "note:" lines to that file, so the parser skips to the first '{'.
fio --name=riops-baseline --filename="$TESTFILE" --size=1G \
  --rw=randread --bs=4k --direct=1 --ioengine="$ENGINE" --iodepth=32 --numjobs=1 \
  --time_based --runtime="$RUNTIME" \
  --output-format=json --output="$TMP_JSON" >/dev/null
RIOPS_MAX=$(python3 - "$TMP_JSON" << 'PYEOF'
import json, sys
raw = open(sys.argv[1]).read()
print(int(json.loads(raw[raw.index("{"):])["jobs"][0]["read"]["iops"]))
PYEOF
)

{
  echo "RIOPS_MAX=$RIOPS_MAX"
  echo "RIOPS_ENGINE=$ENGINE"
} > "$OUT"
echo "measured RIOPS_MAX=${RIOPS_MAX} (engine=$ENGINE) → wrote $OUT"
echo "bench read cap (2/3): $(( RIOPS_MAX * 2 / 3 )) — applied on next setup-cgroups.sh run"
