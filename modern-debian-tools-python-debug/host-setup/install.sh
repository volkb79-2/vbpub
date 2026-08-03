#!/usr/bin/env bash
# mdt host-setup installer — prepares a host for tiered devcontainer/test work
# (dev.slice root ceiling + dev-interactive.slice + dev-background.slice +
# dev-buildkitd.slice + runtime IO governance + /etc/docker/daemon.json,
# which this owns fully).
#
#   sudo ./install.sh [--with-baseline] [--force]
#
# Idempotent. First run seeds /etc/mdt/host-setup.env from the example (review
# it, then re-run to apply your edits). --with-baseline additionally runs the
# fio benchmark (~4 min of saturated disk — quiet window!). --force backs up
# an already-installed /etc/mdt/host-setup.env and re-seeds it from the
# current example (needed to pick up newly-added variables — otherwise this
# script never touches a config that's already there). See README.md.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

WITH_BASELINE=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --with-baseline) WITH_BASELINE=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)"; exit 2 ;;
  esac
done
# After arg parsing so --help works unprivileged.
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "== config =="
mkdir -p /etc/mdt /var/lib/mdt
if [ -f /etc/mdt/host-setup.env ] && [ "$FORCE" = 1 ]; then
  backup="/etc/mdt/host-setup.env.bak-$(date +%Y%m%dT%H%M%S)"
  cp /etc/mdt/host-setup.env "$backup"
  cp "$HERE/host-setup.env.example" /etc/mdt/host-setup.env
  echo "--force: backed up existing config to $backup, re-seeded from example — REVIEW IT and re-run to apply edits"
elif [ ! -f /etc/mdt/host-setup.env ]; then
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

# dev-buildkitd.slice CPUQuota: auto-detect (nproc - 2) cores, floored at 1,
# when left unset in host-setup.env — same auto-discovery convention as
# IO_DEV_PATH above. An explicit value in host-setup.env always wins.
if [ -z "${DEV_BUILDKITD_CPU_QUOTA:-}" ]; then
  _nproc=$(nproc 2>/dev/null || echo 2)
  _buildkitd_cores=$(( _nproc > 2 ? _nproc - 2 : 1 ))
  DEV_BUILDKITD_CPU_QUOTA="${_buildkitd_cores}00%"
  echo "dev-buildkitd.slice CPUQuota: auto-detected $_buildkitd_cores cores (host has $_nproc) -> $DEV_BUILDKITD_CPU_QUOTA"
fi

echo "== packages =="
# fio: the baseline benchmark. systemd-oomd: without it every ManagedOOM*
# setting in dev-background.slice is a silent no-op (separate package on Debian).
apt-get update -qq || echo "WARN: apt-get update failed — install may use a stale index"
apt-get install -y --no-install-recommends fio systemd-oomd \
  || echo "WARN: apt install failed — install fio + systemd-oomd manually"

echo "== io baseline: bootstrap from an existing measurement if we have none yet =="
# mdt owns /var/lib/mdt/io-baseline.env as its canonical path (no runtime
# cross-reference to gstammtisch, which is scoped to wings/soulmask/tmpfs
# only). If gstammtisch already measured this host recently, reuse those
# values instead of re-running the ~4min disk-saturating benchmark.
MDT_BASELINE="${IO_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}"
GSTAMMTISCH_BASELINE=/var/lib/gstammtisch/io-baseline.env
if [ ! -f "$MDT_BASELINE" ] && [ -f "$GSTAMMTISCH_BASELINE" ]; then
  mkdir -p "$(dirname "$MDT_BASELINE")"
  cp "$GSTAMMTISCH_BASELINE" "$MDT_BASELINE"
  echo "bootstrapped $MDT_BASELINE from gstammtisch's existing measurement ($(grep -o 'MEASURED_AT=.*' "$MDT_BASELINE" || true))"
fi

echo "== render + install units =="
RENDER_VARS="DEV_INTERACTIVE_MEMORY_HIGH DEV_INTERACTIVE_MEMORY_MAX DEV_INTERACTIVE_MEMORY_LOW \
DEV_INTERACTIVE_CPU_WEIGHT DEV_INTERACTIVE_IO_WEIGHT DEV_INTERACTIVE_ZSWAP_WRITEBACK \
DEV_BACKGROUND_MEMORY_HIGH DEV_BACKGROUND_MEMORY_MAX DEV_BACKGROUND_MEMORY_SWAP_MAX \
DEV_BACKGROUND_CPU_WEIGHT DEV_BACKGROUND_IO_WEIGHT DEV_BACKGROUND_OOM_PRESSURE_LIMIT \
DEV_BUILDKITD_MEMORY_HIGH DEV_BUILDKITD_MEMORY_MAX DEV_BUILDKITD_MEMORY_SWAP_MAX \
DEV_BUILDKITD_CPU_WEIGHT DEV_BUILDKITD_CPU_QUOTA DEV_BUILDKITD_IO_WEIGHT DEV_BUILDKITD_IMAGE \
DEV_STATIC_RBW DEV_STATIC_WBW DEV_STATIC_RIOPS DEV_STATIC_WIOPS \
DOCKER_SCOPE_BACKSTOP_MEMORY_MAX DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX \
IO_DEV_PATH SWEEP_INTERVAL"
render() { # render <template> <dest>
  local src="$1" dst="$2" v args=()
  for v in $RENDER_VARS; do args+=(-e "s|@$v@|${!v:-}|g"); done
  sed "${args[@]}" "$src" > "$dst"
  # Optional settings: a directive whose value resolves to empty (the var
  # was left unset in host-setup.env) is DROPPED from the rendered unit
  # entirely, not left as an invalid `Key=` line — "not set" means "not
  # applied," systemd's own default for that property then governs (see
  # host-setup.env.example, dev-buildkitd.slice section).
  sed -i '/^[A-Za-z][A-Za-z0-9]*=$/d' "$dst"
  echo "rendered $dst"
}
render "$HERE/units/dev.slice.in"              /etc/systemd/system/dev.slice
render "$HERE/units/dev-interactive.slice.in"  /etc/systemd/system/dev-interactive.slice
render "$HERE/units/dev-background.slice.in"   /etc/systemd/system/dev-background.slice
render "$HERE/units/dev-buildkitd.slice.in"    /etc/systemd/system/dev-buildkitd.slice
render "$HERE/units/mdt-buildkitd.service.in"  /etc/systemd/system/mdt-buildkitd.service
render "$HERE/units/mdt-host-slices.timer.in"  /etc/systemd/system/mdt-host-slices.timer
install -m 0644 "$HERE/units/mdt-host-slices.service" /etc/systemd/system/mdt-host-slices.service

mkdir -p /etc/systemd/system/docker-.scope.d
render "$HERE/units/docker-scope-default-limits.conf.in" \
  /etc/systemd/system/docker-.scope.d/50-default-limits.conf

# Post-render fixups:
# - no device node discovered → IO*Max lines would be invalid; drop them.
#   (dev.slice now carries the one IO ceiling for both tiers — see units/dev.slice.in)
if [ -z "${IO_DEV_PATH:-}" ]; then
  sed -i '/^IO\(Read\|Write\)\(Bandwidth\|IOPS\)Max=/d' /etc/systemd/system/dev.slice
  echo "no device — dropped static IO*Max lines (runtime caps may still apply if discovery succeeds later)"
fi
# - MemoryZSwapWriteback= needs systemd >= 256; older hosts get the raw-write
#   fallback from mdt-apply-dev-caps.sh instead.
SD_VER=$(systemctl --version | awk 'NR==1{print $2}')
if [ -n "$SD_VER" ] && [ "$SD_VER" -lt 256 ] 2>/dev/null; then
  sed -i '/^MemoryZSwapWriteback=/d' /etc/systemd/system/dev-interactive.slice
  echo "systemd $SD_VER < 256 — dropped MemoryZSwapWriteback= (runtime raw-write fallback covers it)"
fi

echo "== docker daemon.json (merge, not overwrite — full ownership, host dev-tier cgroup governance rollout) =="
# Sets/updates only the keys this project cares about; leaves any other
# existing key in the file untouched. Requires a dockerd RESTART (not
# reload) for cgroup-parent to take effect — NOT done here, that's
# disruptive (restarts every running container) and belongs in a scheduled
# window, not a routine install.sh run.
python3 - "$DOCKER_DAEMON_CGROUP_PARENT" "$DOCKER_DAEMON_LIVE_RESTORE" \
         "$DOCKER_DAEMON_LOG_DRIVER" "$DOCKER_DAEMON_LOG_MAX_SIZE" "$DOCKER_DAEMON_LOG_MAX_FILE" <<'PY'
import json, sys, pathlib

cgroup_parent, live_restore, log_driver, log_max_size, log_max_file = sys.argv[1:6]
path = pathlib.Path("/etc/docker/daemon.json")
try:
    config = json.loads(path.read_text()) if path.exists() else {}
except json.JSONDecodeError as exc:
    sys.exit(f"refusing to merge into an unparseable {path}: {exc}")

config["cgroup-parent"] = cgroup_parent
config["live-restore"] = live_restore.strip().lower() in {"1", "true", "yes"}
config["log-driver"] = log_driver
config.setdefault("log-opts", {})
config["log-opts"]["max-size"] = log_max_size
config["log-opts"]["max-file"] = log_max_file

path.write_text(json.dumps(config, indent=2) + "\n")
print(f"merged into {path}: cgroup-parent={cgroup_parent} (needs a dockerd RESTART to take effect)")
PY

echo "== scripts =="
install -m 0755 "$HERE/scripts/mdt-apply-dev-caps.sh" /usr/local/sbin/mdt-apply-dev-caps.sh
install -m 0755 "$HERE/scripts/mdt-io-baseline.py"    /usr/local/sbin/mdt-io-baseline.py
install -m 0755 "$HERE/scripts/mdt-slice-audit.py"    /usr/local/sbin/mdt-slice-audit.py
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
# dev.slice first — its children nest under it by name, but starting it
# explicitly means the root's IO ceiling is in force even before any of the
# three has its own first member.
systemctl start dev.slice dev-interactive.slice dev-background.slice dev-buildkitd.slice 2>/dev/null || true
systemctl enable mdt-host-slices.service          # boot-time apply
systemctl enable --now mdt-host-slices.timer      # periodic sweep
systemctl enable --now mdt-buildkitd.service      # host-managed BuildKit worker

if [ "$WITH_BASELINE" = 1 ]; then
  echo "== io baseline (fio — disk will be saturated for ~4 min) =="
  IO_BASELINE_ENV="$MDT_BASELINE" \
    /usr/local/sbin/mdt-io-baseline.py --force || echo "WARN: baseline failed — statics remain in force"
fi

systemctl start mdt-host-slices.service           # apply runtime caps now
echo "== done — verify with: mdt-host-check.sh =="
echo "== REMINDER: /etc/docker/daemon.json's cgroup-parent change needs a scheduled"
echo "   'systemctl restart docker' to take effect — this restarts every running"
echo "   container. Not done automatically by this script."
