#!/usr/bin/env bash
# mdt host-setup installer — prepares a host for tiered devcontainer/test work
# (dev.slice root ceiling + dev-interactive.slice + dev-background.slice +
# dev-gates.slice + dev-memory_min_guaranteed.slice +
# dev-buildkitd.slice + runtime IO governance + /etc/docker/daemon.json,
# which this owns fully).
#
#   sudo ./install.sh [--wizard] [--with-baseline] [--force] [--restart-docker]
#
# Idempotent. First run seeds /etc/mdt/host-setup.env from the example (review
# it, then re-run to apply your edits). --wizard walks that seeding step
# interactively instead (mdt-host-setup-wizard.py, alongside this script) — sizes the tiers
# against THIS host's own /proc/meminfo rather than the example's fixed
# numbers, then falls through into the same render/apply logic below either
# way. --with-baseline additionally runs the fio benchmark (~4 min of
# saturated disk — quiet window!). --force backs up an already-installed
# /etc/mdt/host-setup.env and re-seeds it from the current example (needed to
# pick up newly-added variables — otherwise this script never touches a
# config that's already there; --wizard does this backup-then-regenerate
# automatically too, whenever a config already exists). --restart-docker will
# automatically restart docker.socket and docker.service at the end (warning:
# disrupts all running containers). See README.md.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

WITH_BASELINE=0
FORCE=0
AUTO_RESTART_DOCKER=0
WIZARD=0
for arg in "$@"; do
  case "$arg" in
    --wizard) WIZARD=1 ;;
    --with-baseline) WITH_BASELINE=1 ;;
    --force) FORCE=1 ;;
    --restart-docker) AUTO_RESTART_DOCKER=1 ;;
    -h|--help) sed -n '2,21p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)"; exit 2 ;;
  esac
done
# After arg parsing so --help works unprivileged.
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "== reactive per-container cap watcher: inotify availability check =="
# mdt-dev-cap-watcher.py needs a working inotify_init1() — true on any
# kernel since 2.6.27, but checked explicitly rather than let a confusing
# errno surface later inside the service. A failure here skips installing
# the watcher entirely; mdt-apply-dev-caps.sh's periodic sweep still runs
# either way, just without the reactive/instant half.
if python3 -c "
import ctypes, ctypes.util, sys
libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
fd = libc.inotify_init1(0)
sys.exit(0 if fd >= 0 else 1)
" 2>/dev/null; then
  INOTIFY_OK=1
  echo "inotify: available"
else
  INOTIFY_OK=0
  echo "WARN: inotify_init1() failed on this host — skipping mdt-dev-cap-watcher.service; the periodic sweep (mdt-host-slices.timer) still applies IO caps and will be the only source of per-container limits"
fi

echo "== config =="
mkdir -p /etc/mdt /var/lib/mdt
if [ "$WIZARD" = 1 ]; then
  # Runs BEFORE (instead of) the cp-based seed/re-seed below — the wizard's
  # only contract with the rest of this script is "produce a valid
  # /etc/mdt/host-setup.env," identical in shape to what a human hand-edit
  # (or the plain seed below) would produce; everything downstream (render(),
  # RENDER_VARS, unit installation) is unmodified and runs exactly as it
  # always has against whatever the wizard wrote.
  if [ -f /etc/mdt/host-setup.env ]; then
    backup="/etc/mdt/host-setup.env.bak-$(date +%Y%m%dT%H%M%S)"
    cp /etc/mdt/host-setup.env "$backup"
    echo "--wizard: backed up existing config to $backup before regenerating it interactively"
  fi
  python3 "$HERE/mdt-host-setup-wizard.py" \
    --example "$HERE/host-setup.env.example" \
    --output /etc/mdt/host-setup.env \
    --io-baseline-script "$HERE/scripts/mdt-io-baseline.py" \
    --install-script "$HERE/install.sh" \
    --skip-run-offer
elif [ -f /etc/mdt/host-setup.env ] && [ "$FORCE" = 1 ]; then
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

# A config that predates a key added to host-setup.env.example since has NO
# line for it at all (not even empty) -- render()'s own "empty/unset =
# directive dropped" rule then silently removes every directive that uses
# it from the rendered unit(s), which for e.g. dev-gates.slice means an
# UNBOUNDED admission capacity object with no mechanical signal at all
# (RG-55 P8 review round 1, B3). Compares NAME presence only (`^KEY=`), not
# values: several keys are intentionally shipped empty in the example
# itself (DEV_MEMORY_MIN_GUARANTEED_CEILING, DEV_BUILDKITD_CPU_QUOTA,
# IO_DEV_PATH) -- "declared, empty" is a deliberate choice, not a finding.
MISSING_KEYS=""
while IFS= read -r key; do
  grep -qE "^${key}=" /etc/mdt/host-setup.env 2>/dev/null || MISSING_KEYS="$MISSING_KEYS $key"
done < <(grep -oE '^[A-Z_][A-Z0-9_]*=' "$HERE/host-setup.env.example" | sed 's/=$//')
if [ -n "$MISSING_KEYS" ]; then
  echo "WARN: your /etc/mdt/host-setup.env predates these keys:${MISSING_KEYS} — the rendered unit(s) using them will be unbounded (directive dropped, not defaulted). Add them from host-setup.env.example (see README.md \"Upgrading a host that already runs mdt host-setup\" for the additive sequence), then re-run this script. (--force/--wizard also pick them up, but re-render/reactivate the WHOLE estate from the example's numbers live — see the same README section before using either on an already-tuned host.)"
fi

# Device node for the static IO*Max lines (render-time; the runtime script
# re-discovers independently, so an install-time miss only drops the statics).
if [ -z "${IO_DEV_PATH:-}" ]; then
  for path in /var/lib/docker /; do
    IO_DEV_PATH=$(findmnt -no SOURCE --target "$path" 2>/dev/null) && [ -n "$IO_DEV_PATH" ] && break
  done
fi
echo "io device for static caps: ${IO_DEV_PATH:-<none discovered — static IO caps omitted>}"

# CPUQuota, absolute ceilings mirroring the IO one: auto-
# detected from nproc when left unset in host-setup.env, floored at 1 core —
# same auto-discovery convention as IO_DEV_PATH above. An explicit value in
# host-setup.env always wins, per-slice. dev.slice's own ceiling bounds the
# WHOLE tier combined (DEV_CPU_RESERVE_CORES=1 default: nproc-1, leaving one
# core for the host/production regardless of how many dev-tier children are
# busy at once); each child ALSO gets its own tighter ceiling
# (DEV_SUBSLICE_CPU_RESERVE_CORES=3 default: nproc-3) so no single tier can
# alone claim the parent's whole budget — multiple tiers combined are still
# bounded by dev.slice's own quota either way.
_nproc=$(nproc 2>/dev/null || echo 2)
DEV_CPU_RESERVE_CORES="${DEV_CPU_RESERVE_CORES:-1}"
DEV_SUBSLICE_CPU_RESERVE_CORES="${DEV_SUBSLICE_CPU_RESERVE_CORES:-3}"
if [ -z "${DEV_CPU_QUOTA:-}" ] && [ "$_nproc" -le "$DEV_CPU_RESERVE_CORES" ] 2>/dev/null; then
  echo "WARN: host has $_nproc CPU core(s), so DEV_CPU_RESERVE_CORES=$DEV_CPU_RESERVE_CORES cannot be fully reserved; the dev.slice auto-quota will use its 1-core floor"
fi
if [ "$_nproc" -le "$DEV_SUBSLICE_CPU_RESERVE_CORES" ] 2>/dev/null \
   && { [ -z "${DEV_INTERACTIVE_CPU_QUOTA:-}" ] || [ -z "${DEV_BACKGROUND_CPU_QUOTA:-}" ] \
     || [ -z "${DEV_GATES_CPU_QUOTA:-}" ] || [ -z "${DEV_BUILDKITD_CPU_QUOTA:-}" ]; }; then
  echo "WARN: host has $_nproc CPU core(s), so DEV_SUBSLICE_CPU_RESERVE_CORES=$DEV_SUBSLICE_CPU_RESERVE_CORES cannot be fully reserved; child-slice auto-quotas will use their 1-core floor"
fi
_auto_cpu_quota() { # _auto_cpu_quota <reserve-cores> -> "<cores>00%"
  local reserve="$1" cores
  cores=$(( _nproc > reserve ? _nproc - reserve : 1 ))
  echo "${cores}00%"
}
if [ -z "${DEV_CPU_QUOTA:-}" ]; then
  DEV_CPU_QUOTA=$(_auto_cpu_quota "$DEV_CPU_RESERVE_CORES")
  echo "dev.slice CPUQuota: auto-detected (host has $_nproc, reserving $DEV_CPU_RESERVE_CORES) -> $DEV_CPU_QUOTA"
fi
for _tier_var in DEV_INTERACTIVE_CPU_QUOTA DEV_BACKGROUND_CPU_QUOTA DEV_GATES_CPU_QUOTA DEV_BUILDKITD_CPU_QUOTA; do
  if [ -z "$(eval echo "\${$_tier_var:-}")" ]; then
    eval "$_tier_var=\$(_auto_cpu_quota \"\$DEV_SUBSLICE_CPU_RESERVE_CORES\")"
    echo "$_tier_var: auto-detected (host has $_nproc, reserving $DEV_SUBSLICE_CPU_RESERVE_CORES) -> $(eval echo "\${$_tier_var}")"
  fi
done

# Swap cascade: auto-detect total host swap, then apply
# DEV_SWAP_CASCADE_PCT (default 80%) at each level — host swap -> dev.slice's
# own MemorySwapMax -> each child's own (same 80%, of the PARENT's derived
# value, not of the host total again) -> the watcher's per-container
# MemorySwapMax (mdt-dev-cap-watcher.py reads DEV_SWAP_CASCADE_PCT itself and
# applies it a third time, of whichever child's derived value matches). Same
# "absolute ceiling at every level" reasoning as CPU/IO above. Bytes, not a
# size-suffixed string — valid for systemd set-property AND the Python
# watcher's own arithmetic without a unit parser on either side.
DEV_SWAP_CASCADE_PCT="${DEV_SWAP_CASCADE_PCT:-80}"
_host_swap_bytes=""
if grep -q '^SwapTotal:' /proc/meminfo 2>/dev/null; then
  _host_swap_kib=$(awk '$1 == "SwapTotal:" && $2 ~ /^[0-9]+$/ {print $2; exit}' /proc/meminfo 2>/dev/null)
  if [ -n "${_host_swap_kib:-}" ]; then
    _host_swap_bytes=$(( _host_swap_kib * 1024 ))
  fi
fi
_pct_of() { echo $(( $1 * $2 / 100 )); } # _pct_of <bytes> <pct> -> bytes
if [ -n "${_host_swap_bytes:-}" ] && [ "$_host_swap_bytes" -gt 0 ]; then
  if [ -z "${DEV_SWAP_MAX:-}" ]; then
    DEV_SWAP_MAX=$(_pct_of "$_host_swap_bytes" "$DEV_SWAP_CASCADE_PCT")
    echo "dev.slice MemorySwapMax: auto-detected (host swap ${_host_swap_bytes}B, ${DEV_SWAP_CASCADE_PCT}%) -> ${DEV_SWAP_MAX}B"
  fi
  for _swap_var in DEV_INTERACTIVE_MEMORY_SWAP_MAX DEV_BACKGROUND_MEMORY_SWAP_MAX DEV_GATES_MEMORY_SWAP_MAX DEV_BUILDKITD_MEMORY_SWAP_MAX; do
    if [ -z "$(eval echo "\${$_swap_var:-}")" ]; then
      eval "$_swap_var=\$(_pct_of \"\$DEV_SWAP_MAX\" \"\$DEV_SWAP_CASCADE_PCT\")"
      echo "$_swap_var: auto-detected (${DEV_SWAP_CASCADE_PCT}% of dev.slice's ${DEV_SWAP_MAX}B) -> $(eval echo "\${$_swap_var}")B"
    fi
  done
elif [ "${_host_swap_bytes:-}" = 0 ]; then
  echo "WARN: no swap detected (/proc/meminfo SwapTotal=0) — MemorySwapMax auto-detect skipped; explicit host-setup.env values (if any) still apply"
else
  echo "WARN: could not read /proc/meminfo's SwapTotal — MemorySwapMax auto-detect skipped; set DEV_SWAP_MAX and the *_MEMORY_SWAP_MAX values explicitly if this host has swap"
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
RENDER_VARS="DEV_CPU_QUOTA DEV_ZSWAP_WRITEBACK DEV_SWAP_MAX \
DEV_INTERACTIVE_MEMORY_HIGH DEV_INTERACTIVE_MEMORY_MAX DEV_INTERACTIVE_MEMORY_LOW \
DEV_INTERACTIVE_MEMORY_SWAP_MAX DEV_INTERACTIVE_CPU_WEIGHT DEV_INTERACTIVE_CPU_QUOTA \
DEV_INTERACTIVE_IO_WEIGHT DEV_INTERACTIVE_ZSWAP_WRITEBACK \
DEV_BACKGROUND_MEMORY_HIGH DEV_BACKGROUND_MEMORY_MAX DEV_BACKGROUND_MEMORY_SWAP_MAX \
DEV_BACKGROUND_CPU_WEIGHT DEV_BACKGROUND_CPU_QUOTA DEV_BACKGROUND_IO_WEIGHT \
DEV_BACKGROUND_OOM_PRESSURE_LIMIT DEV_BACKGROUND_ZSWAP_WRITEBACK \
DEV_MEMORY_MIN_GUARANTEED_CEILING \
DEV_GATES_MEMORY_HIGH DEV_GATES_MEMORY_MAX DEV_GATES_MEMORY_SWAP_MAX \
DEV_GATES_CPU_WEIGHT DEV_GATES_CPU_QUOTA DEV_GATES_IO_WEIGHT DEV_GATES_OOM_PRESSURE_LIMIT \
DEV_GATES_ZSWAP_WRITEBACK \
DEV_BUILDKITD_MEMORY_HIGH DEV_BUILDKITD_MEMORY_MAX DEV_BUILDKITD_MEMORY_SWAP_MAX \
DEV_BUILDKITD_CPU_WEIGHT DEV_BUILDKITD_CPU_QUOTA DEV_BUILDKITD_IO_WEIGHT DEV_BUILDKITD_IMAGE \
DEV_BUILDKITD_ZSWAP_WRITEBACK \
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
render "$HERE/units/dev-memory_min_guaranteed.slice.in" \
  /etc/systemd/system/dev-memory_min_guaranteed.slice
render "$HERE/units/dev-gates.slice.in"        /etc/systemd/system/dev-gates.slice
render "$HERE/units/dev-buildkitd.slice.in"    /etc/systemd/system/dev-buildkitd.slice
render "$HERE/units/mdt-buildkitd.service.in"  /etc/systemd/system/mdt-buildkitd.service
render "$HERE/units/mdt-host-slices.timer.in"  /etc/systemd/system/mdt-host-slices.timer
install -m 0644 "$HERE/units/mdt-host-slices.service" /etc/systemd/system/mdt-host-slices.service
if [ "${INOTIFY_OK:-}" = 1 ]; then
  install -m 0644 "$HERE/units/mdt-dev-cap-watcher.service" /etc/systemd/system/mdt-dev-cap-watcher.service
fi
# Not gated by INOTIFY_OK: this one reacts via `docker events`, not inotify —
# see scripts/mdt-io-cap-watcher.sh for why it can't use the same mechanism
# as mdt-dev-cap-watcher.py above.
install -m 0644 "$HERE/units/mdt-io-cap-watcher.service" /etc/systemd/system/mdt-io-cap-watcher.service

mkdir -p /etc/systemd/system/docker-.scope.d
render "$HERE/units/docker-scope-default-limits.conf.in" \
  /etc/systemd/system/docker-.scope.d/50-default-limits.conf

# Directory-mountable Docker API socket (/run/docker-api/docker.sock),
# alongside the default /run/docker.sock — see units/docker-api-socket.conf
# for why. No template variables (a fixed, well-known path, like
# /run/mdt-buildkitd above), so installed directly rather than rendered.
mkdir -p /etc/systemd/system/docker.socket.d
install -m 0644 "$HERE/units/docker-api-socket.conf" \
  /etc/systemd/system/docker.socket.d/50-mdt-dedicated-api-socket.conf

# /run/cgprofile tmpfiles.d entry (RG-55 A2/D-30, M5): the directory
# templates/devcontainer.json bind-mounts to reach the cgroup-profiler
# daemon's control socket. No template variables, installed directly like
# docker-api-socket.conf above — see units/mdt-cgprofile.conf for the
# full reasoning. Named with the mdt- prefix (round-2 review S15(r2)),
# matching every other mdt-owned drop-in on this host
# (/etc/modules-load.d/mdt-bfq.conf, docker.socket.d's own
# 50-mdt-dedicated-api-socket.conf above) rather than the unprefixed
# "cgprofile.conf" the daemon package's own future deployment would also
# plausibly want to ship into the same directory -- an unprefixed name
# invites two independent packages fighting over one file, silently, with
# whichever installs last winning. `systemd-tmpfiles --create` applies it
# immediately (idempotent — a directory that already has the right
# mode/owner is a no-op) rather than waiting for the next boot's automatic
# systemd-tmpfiles-setup.service run, so `--mount`'s "source must already
# exist" requirement is satisfied the moment this script finishes, not
# after a reboot.
install -m 0644 "$HERE/units/mdt-cgprofile.conf" /etc/tmpfiles.d/mdt-cgprofile.conf
systemd-tmpfiles --create /etc/tmpfiles.d/mdt-cgprofile.conf \
  || echo "WARN: systemd-tmpfiles --create failed for /etc/tmpfiles.d/mdt-cgprofile.conf — /run/cgprofile may not exist yet (retries at next boot's systemd-tmpfiles-setup.service)"

# Post-render fixups:
# - no device node discovered → IO*Max lines would be invalid; drop them.
#   (dev.slice now carries the one IO ceiling for both tiers — see units/dev.slice.in)
if [ -z "${IO_DEV_PATH:-}" ]; then
  sed -i '/^IO\(Read\|Write\)\(Bandwidth\|IOPS\)Max=/d' /etc/systemd/system/dev.slice
  echo "no device — dropped static IO*Max lines (runtime caps may still apply if discovery succeeds later)"
fi
# - MemoryZSwapWriteback= needs systemd >= 256; older hosts get the raw-write
#   fallback from mdt-apply-dev-caps.sh instead. Applies to dev.slice AND
#   every child.
SD_VER=$(systemctl --version | awk 'NR==1{print $2}')
if [ -n "$SD_VER" ] && [ "$SD_VER" -lt 256 ] 2>/dev/null; then
  sed -i '/^MemoryZSwapWriteback=/d' \
    /etc/systemd/system/dev.slice \
    /etc/systemd/system/dev-interactive.slice \
    /etc/systemd/system/dev-background.slice \
    /etc/systemd/system/dev-gates.slice \
    /etc/systemd/system/dev-buildkitd.slice
  echo "systemd $SD_VER < 256 — dropped MemoryZSwapWriteback= from dev.slice + every child (runtime raw-write fallback covers it)"
fi

echo "== docker daemon.json (merge, not overwrite — cgroup-parent only, host dev-tier cgroup governance rollout) =="
# Sets/updates ONLY cgroup-parent; leaves every other existing key untouched
# — including live-restore/log-driver/log-opts, which debian-install-v2 owns
# now (D-F1 ownership split: assigning a default container cgroup placement
# is squarely an mdt/dev-workload concern, base docker daemon logging/restart
# policy is not). Each tool's own read-merge-write cycle is order-independent
# — whichever runs later simply adds to what's already there. Requires a
# dockerd RESTART (not reload) for cgroup-parent to take effect — NOT done
# here, that's disruptive (restarts every running container) and belongs in
# a scheduled window, not a routine install.sh run.
python3 - "$DOCKER_DAEMON_CGROUP_PARENT" <<'PY'
import json, sys, pathlib

cgroup_parent, = sys.argv[1:2]
path = pathlib.Path("/etc/docker/daemon.json")
try:
    config = json.loads(path.read_text()) if path.exists() else {}
except json.JSONDecodeError as exc:
    sys.exit(f"refusing to merge into an unparseable {path}: {exc}")

config["cgroup-parent"] = cgroup_parent

path.write_text(json.dumps(config, indent=2) + "\n")
print(f"merged into {path}: cgroup-parent={cgroup_parent} (needs a dockerd RESTART to take effect)")
PY

echo "== scripts =="
# mdt-container-caps.lib.sh: sourced by BOTH scripts below via
# "$(dirname "$0")/mdt-container-caps.lib.sh" — must land in the same
# directory as them, not just be present in the repo checkout.
install -m 0644 "$HERE/scripts/mdt-container-caps.lib.sh" /usr/local/sbin/mdt-container-caps.lib.sh
install -m 0755 "$HERE/scripts/mdt-apply-dev-caps.sh"  /usr/local/sbin/mdt-apply-dev-caps.sh
install -m 0755 "$HERE/scripts/mdt-io-cap-watcher.sh"  /usr/local/sbin/mdt-io-cap-watcher.sh
install -m 0755 "$HERE/scripts/mdt-io-baseline.py"     /usr/local/sbin/mdt-io-baseline.py
install -m 0755 "$HERE/scripts/mdt-slice-audit.py"     /usr/local/sbin/mdt-slice-audit.py
install -m 0755 "$HERE/scripts/check.sh"               /usr/local/sbin/mdt-host-check.sh
install -m 0755 "$HERE/scripts/docker-safe-restart.sh" /usr/local/sbin/mdt-docker-safe-restart
if [ "$INOTIFY_OK" = 1 ]; then
  install -m 0755 "$HERE/scripts/mdt-dev-cap-watcher.py" /usr/local/sbin/mdt-dev-cap-watcher.py
fi

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
systemctl start dev.slice dev-interactive.slice dev-background.slice dev-memory_min_guaranteed.slice \
  dev-gates.slice dev-buildkitd.slice 2>/dev/null || true
systemctl enable mdt-host-slices.service          # boot-time apply
systemctl enable --now mdt-host-slices.timer      # periodic sweep
systemctl enable --now mdt-buildkitd.service      # host-managed BuildKit worker
if [ "$INOTIFY_OK" = 1 ]; then
  systemctl enable --now mdt-dev-cap-watcher.service  # reactive per-container MemoryMax
fi
systemctl enable mdt-io-cap-watcher.service         # reactive per-container IO caps (docker events)

if [ "$WITH_BASELINE" = 1 ]; then
  echo "== io baseline (fio — disk will be saturated for ~4 min) =="
  IO_BASELINE_ENV="$MDT_BASELINE" \
    /usr/local/sbin/mdt-io-baseline.py --force || echo "WARN: baseline failed — statics remain in force"
fi

systemctl start mdt-host-slices.service           # apply runtime caps now
# Both reactive watchers load their configuration and baseline once per
# process. Restart after the optional baseline measurement so a re-run of this
# installer cannot leave an already-running watcher applying stale values.
if [ "$INOTIFY_OK" = 1 ]; then
  systemctl restart mdt-dev-cap-watcher.service
fi
systemctl restart mdt-io-cap-watcher.service

# Optional: automatically restart docker if requested (--restart-docker flag)
if [ "$AUTO_RESTART_DOCKER" = 1 ]; then
  echo "== docker restart (--restart-docker flag set) =="
  echo "restarting docker.socket and docker.service..."
  systemctl restart docker.socket docker.service
  if [ $? -eq 0 ]; then
    echo "docker restarted successfully"
  else
    echo "WARNING: docker restart failed — see 'systemctl status docker.socket docker.service' for details"
    exit 1
  fi
else
  echo "== done — verify with: mdt-host-check.sh =="
  echo "== REMINDER: /etc/docker/daemon.json's cgroup-parent change needs a scheduled"
  echo "   'systemctl restart docker' to take effect — this restarts every running"
  echo "   container. Not done automatically by this script."
  echo "== REMINDER: the new /run/docker-api/docker.sock listener needs a scheduled"
  echo "   'systemctl restart docker.socket docker.service' to take effect — dockerd"
  echo "   only picks up newly-added listen sockets at its own startup, so restarting"
  echo "   docker.socket alone (which just rebinds) is not enough. Same disruption as"
  echo "   the daemon.json restart above; batch them into the same window. Not done"
  echo "   automatically by this script (use --restart-docker flag to enable)."
fi
