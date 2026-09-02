#!/usr/bin/env bash
# debian-install-v2 — io.cost calibration wrapper around the vendored,
# unmodified tools/iocost_coef_gen.py (Linux kernel source, GPL-2.0).
#
# WHY THIS EXISTS, and why it's separate from mdt-io-baseline.py's own
# fio benchmark (modern-debian-tools-python-debug/host-setup): that one
# measures 4 SUSTAINED-CEILING numbers (max r/w iops at fixed QD, max r/w
# bandwidth) to derive a flat percentage cap for dev.slice's io.max. This
# one measures a MATRIX — sequential vs random x read vs write, each its
# own fio run — and fits per-category throughput instead of one ceiling.
# That matrix is a much closer proxy for how swap-out/zswap-writeback
# actually loads a device (small, close-to-random 4K writes at whatever
# queue depth memory pressure happens to generate) than either of
# mdt-io-baseline.py's two numbers, which is why it's worth running even
# if io.cost itself never gets turned on as the active mechanism — the
# data has standalone diagnostic value for the swap-performance question.
#
# ############################################################################
# WARNING — READ BEFORE RUNNING ON A HOST WITH LIVE PRODUCTION WORKLOADS:
#
#   iocost_coef_gen.py TEMPORARILY SETS THE TARGET DEVICE'S IO SCHEDULER TO
#   `none` AND DISABLES REQUEST MERGING FOR THE ENTIRE CALIBRATION RUN (6
#   sequential fio jobs x --duration seconds each, default 120s = 12+
#   minutes, typically 15-20+ with fio startup/teardown overhead). It
#   restores the original scheduler afterward (also on most error paths,
#   via atexit) — but for the WHOLE run, BFQ is NOT the active scheduler.
#
#   On a host using BFQ for weight-based tiering (host-setup's
#   dev-interactive/dev-background split, or any wings-cgroups-style
#   per-service io.bfq.weight), this means EVERY io.bfq.weight on that
#   device is INERT for the whole calibration — not just saturated, but
#   completely unprioritized, on top of an already-saturating fio load.
#   This is a materially bigger ask than mdt-io-baseline.py's own "run in
#   a quiet window" (~4 min, weights stay live throughout that one).
#
#   Do not run this against a device backing a live production workload
#   without a deliberate, communicated maintenance window.
# ############################################################################
#
# Usage: iocost-calibrate.sh [--testdev /dev/DEVICE] [--duration SECONDS] [-- iocost_coef_gen.py args...]
# Output: /var/lib/debian-install-v2/iocost-model.env (KEY=VALUE, same
# shape mdt-io-baseline.py uses at /var/lib/mdt/io-baseline.env, so the
# two can be diffed/compared directly) plus a human-readable summary on
# stdout.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${IOCOST_RESULT_DIR:-/var/lib/debian-install-v2}"
OUT_FILE="$OUT_DIR/iocost-model.env"

TESTDEV=""
DURATION=""
EXTRA_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --testdev) TESTDEV="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --) shift; EXTRA_ARGS+=("$@"); break ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

for cmd in findmnt pv dd fio; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "iocost-calibrate: required command '$cmd' missing (apt install fio pv)" >&2; exit 1; }
done

echo "############################################################################"
echo "# iocost-calibrate: this will run BFQ->none on the target device's scheduler"
echo "# for the ENTIRE calibration (6 fio runs, ~$(( ${DURATION:-120} * 6 / 60 )) min minimum,"
echo "# more with startup/teardown overhead). Every io.bfq.weight on that device is"
echo "# INERT for that whole window, not just saturated. Confirm this is a"
echo "# deliberate maintenance window before continuing."
echo "############################################################################"
read -r -p "Type 'yes' to proceed: " CONFIRM
[ "$CONFIRM" = "yes" ] || { echo "aborted"; exit 1; }

mkdir -p "$OUT_DIR"

ARGV=(python3 "$HERE/iocost_coef_gen.py")
[ -n "$TESTDEV" ] && ARGV+=(--testdev "$TESTDEV")
[ -n "$DURATION" ] && ARGV+=(--duration "$DURATION")
ARGV+=("${EXTRA_ARGS[@]}")

echo "iocost-calibrate: running: ${ARGV[*]}"
RESULT_LINE=$("${ARGV[@]}")
echo "iocost-calibrate: raw result: $RESULT_LINE"

# RESULT_LINE shape: "MAJ:MIN rbps=N rseqiops=N rrandiops=N wbps=N wseqiops=N wrandiops=N"
DEVNO="${RESULT_LINE%% *}"
{
  echo "MEASURED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "DEVNO=$DEVNO"
  echo "MEASURE_METHOD=iocost_coef_gen-upstream"
  for pair in ${RESULT_LINE#* }; do
    key="${pair%%=*}"
    val="${pair#*=}"
    echo "$(echo "$key" | tr '[:lower:]' '[:upper:]')=$val"
  done
} > "$OUT_FILE"

echo
echo "=== iocost-calibrate: results written to $OUT_FILE ==="
cat "$OUT_FILE"
echo
echo "=== comparison against mdt-io-baseline.py's own numbers, if present ==="
MDT_BASELINE="${MDT_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}"
if [ -f "$MDT_BASELINE" ]; then
  # shellcheck disable=SC1090
  . "$MDT_BASELINE"
  . "$OUT_FILE"
  echo "swap/zswap-writeback is small, close-to-random 4K writes — WRANDIOPS is"
  echo "the number that answers 'can this device absorb a writeback burst', not"
  echo "the sustained WIOPS_MAX mdt-io-baseline.py measured at a fixed QD32:"
  printf '  mdt-io-baseline WIOPS_MAX (4k, fixed QD32):      %s\n' "${WIOPS_MAX:-?}"
  printf '  iocost WRANDIOPS (4k, random, this device'"'"'s QD): %s\n' "${WRANDIOPS:-?}"
else
  echo "no $MDT_BASELINE found — run mdt-io-baseline.py too for a side-by-side view"
fi
