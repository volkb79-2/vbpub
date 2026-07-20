#!/usr/bin/env bash
# mdt host-setup installer — prepares a host for tiered devcontainer/test work
# (interactive.slice + besteffort.slice + runtime IO governance).
#
#   sudo ./install.sh [--with-baseline]
#
# Idempotent. First run seeds /etc/mdt/host-setup.env from the example (review
# it, then re-run to apply your edits). --with-baseline additionally runs the
# fio benchmark (~4 min of saturated disk — quiet window!). See README.md.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

WITH_BASELINE=0
for arg in "$@"; do
  case "$arg" in
    --with-baseline) WITH_BASELINE=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)"; exit 2 ;;
  esac
done
# After arg parsing so --help works unprivileged.
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "== config =="
mkdir -p /etc/mdt /var/lib/mdt
if [ ! -f /etc/mdt/host-setup.env ]; then
  cp "$HERE/host-setup.env.example" /etc/mdt/host-setup.env
  echo "seeded /etc/mdt/host-setup.env from example — REVIEW IT and re-run to apply edits"
fi
# shellcheck disable=SC1091
. /etc/mdt/host-setup.env

# Device node for the static IO*Max lines (render-time; the runtime script
# re-discovers independently, so an install-time miss only drops the statics).
if [ -z "${IO_DEV_PATH:-}" ]; then
  for path in /var/lib/docker /; do
    IO_DEV_PATH=$(findmnt -no SOURCE --target "$path" 2>/dev/null) && [ -n "$IO_DEV_PATH" ] && break
  done
fi
echo "io device for static caps: ${IO_DEV_PATH:-<none discovered — static IO caps omitted>}"

echo "== packages =="
# fio: the baseline benchmark. systemd-oomd: without it every ManagedOOM*
# setting in besteffort.slice is a silent no-op (separate package on Debian).
apt-get update -qq || echo "WARN: apt-get update failed — install may use a stale index"
apt-get install -y --no-install-recommends fio systemd-oomd \
  || echo "WARN: apt install failed — install fio + systemd-oomd manually"

echo "== render + install units =="
RENDER_VARS="INTERACTIVE_MEMORY_HIGH INTERACTIVE_MEMORY_MAX INTERACTIVE_MEMORY_LOW \
INTERACTIVE_CPU_WEIGHT INTERACTIVE_IO_WEIGHT INTERACTIVE_ZSWAP_WRITEBACK \
BESTEFFORT_MEMORY_HIGH BESTEFFORT_MEMORY_MAX BESTEFFORT_MEMORY_SWAP_MAX \
BESTEFFORT_CPU_WEIGHT BESTEFFORT_IO_WEIGHT BESTEFFORT_OOM_PRESSURE_LIMIT \
BESTEFFORT_STATIC_RBW BESTEFFORT_STATIC_WBW BESTEFFORT_STATIC_RIOPS BESTEFFORT_STATIC_WIOPS \
IO_DEV_PATH SWEEP_INTERVAL"
render() { # render <template> <dest>
  local src="$1" dst="$2" v args=()
  for v in $RENDER_VARS; do args+=(-e "s|@$v@|${!v:-}|g"); done
  sed "${args[@]}" "$src" > "$dst"
  echo "rendered $dst"
}
render "$HERE/units/interactive.slice.in"      /etc/systemd/system/interactive.slice
render "$HERE/units/besteffort.slice.in"       /etc/systemd/system/besteffort.slice
render "$HERE/units/mdt-host-slices.timer.in"  /etc/systemd/system/mdt-host-slices.timer
install -m 0644 "$HERE/units/mdt-host-slices.service" /etc/systemd/system/mdt-host-slices.service

# Post-render fixups:
# - no device node discovered → IO*Max lines would be invalid; drop them.
if [ -z "${IO_DEV_PATH:-}" ]; then
  sed -i '/^IO\(Read\|Write\)\(Bandwidth\|IOPS\)Max=/d' /etc/systemd/system/besteffort.slice
  echo "no device — dropped static IO*Max lines (runtime caps may still apply if discovery succeeds later)"
fi
# - MemoryZSwapWriteback= needs systemd >= 256; older hosts get the raw-write
#   fallback from mdt-apply-dev-caps.sh instead.
SD_VER=$(systemctl --version | awk 'NR==1{print $2}')
if [ -n "$SD_VER" ] && [ "$SD_VER" -lt 256 ] 2>/dev/null; then
  sed -i '/^MemoryZSwapWriteback=/d' /etc/systemd/system/interactive.slice
  echo "systemd $SD_VER < 256 — dropped MemoryZSwapWriteback= (runtime raw-write fallback covers it)"
fi

echo "== scripts =="
install -m 0755 "$HERE/scripts/mdt-apply-dev-caps.sh" /usr/local/sbin/mdt-apply-dev-caps.sh
install -m 0755 "$HERE/scripts/mdt-io-baseline.py"    /usr/local/sbin/mdt-io-baseline.py
install -m 0755 "$HERE/scripts/check.sh"              /usr/local/sbin/mdt-host-check.sh

echo "== BFQ scheduler (io.weight needs it; io.max caps work on any scheduler) =="
install -m 0644 "$HERE/etc/modules-load.d/bfq.conf"            /etc/modules-load.d/mdt-bfq.conf
install -m 0644 "$HERE/etc/udev/rules.d/60-bfq-scheduler.rules" /etc/udev/rules.d/60-mdt-bfq-scheduler.rules
modprobe bfq 2>/dev/null || echo "WARN: modprobe bfq failed"
udevadm control --reload-rules 2>/dev/null \
  && udevadm trigger --action=change --subsystem-match=block 2>/dev/null \
  || echo "WARN: udev reload/trigger failed — set the scheduler manually"

echo "== activate =="
systemctl daemon-reload
# Slices activate on demand, but starting them now makes the cgroups exist so
# the zswap policy applies immediately and check.sh has something to look at.
systemctl start interactive.slice besteffort.slice 2>/dev/null || true
systemctl enable mdt-host-slices.service          # boot-time apply
systemctl enable --now mdt-host-slices.timer      # periodic sweep

if [ "$WITH_BASELINE" = 1 ]; then
  echo "== io baseline (fio — disk will be saturated for ~4 min) =="
  IO_BASELINE_ENV="${IO_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}" \
    /usr/local/sbin/mdt-io-baseline.py || echo "WARN: baseline failed — statics remain in force"
fi

systemctl start mdt-host-slices.service           # apply runtime caps now
echo "== done — verify with: mdt-host-check.sh =="
