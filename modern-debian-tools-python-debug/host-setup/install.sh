#!/usr/bin/env bash
# mdt host-setup installer — prepares a host for tiered devcontainer/test work
# (dev.slice root ceiling + dev-interactive.slice + dev-background.slice +
# dev-gates.slice + dev-memory_min_guaranteed.slice +
# dev-buildkitd.slice + runtime IO governance + /etc/docker/daemon.json,
# which this owns fully).
#
#   sudo ./install.sh [--wizard] [--with-baseline] [--force] [--restart-docker]
#   sudo ./install.sh --wizard              # first install or preserve old values
#   sudo ./install.sh --wizard --force      # re-seed answers from this example
#   sudo ./install.sh --with-baseline       # remeasure in a quiet disk window
# Run these commands from the Docker host shell, never from a devcontainer;
# container root cannot apply the host's systemd, cgroup, or /etc policy.
#
# Idempotent. First run requires --wizard: it produces a host-sized candidate
# in a temporary directory, validates that candidate, and only then installs it
# under /etc/mdt. A plain first run never persists the incomplete example.
# --wizard walks that configuration step interactively instead
# (mdt-host-setup-wizard.py, alongside this script) — sizes the tiers against
# THIS host's own /proc/meminfo rather than the example's fixed numbers, then
# falls through into the same render/apply logic below. --with-baseline
# additionally runs the official io.cost benchmark (~12 min of saturated disk — quiet
# window!). --force is accepted only with --wizard; it deliberately starts the
# wizard from the current example instead of using the installed config as
# defaults. After successful candidate validation the old config is backed up
# and replaced. Without --force, --wizard preserves existing values as defaults.
# --restart-docker will
# automatically restart docker.socket and docker.service at the end (warning:
# disrupts all running containers). See README.md.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

usage() {
  cat <<'EOF'
mdt host-setup installer

Purpose
  Configure the host's dev.slice resource hierarchy, runtime IO caps,
  host-managed rootless BuildKit worker, and the owned cgroup-parent key in
  /etc/docker/daemon.json. Run from the Docker host shell, never a
  devcontainer: container root cannot control the host's systemd, cgroups,
  mounts, or /etc.

Usage
  sudo ./install.sh --wizard [--force] [--with-baseline] [--restart-docker]
  sudo ./install.sh --with-baseline
  sudo ./install.sh --restart-docker
  sudo ./install.sh --help

Options
  --wizard            Ask every host policy question, write a temporary
                      candidate, validate it, then install and apply it.
                      Required on a first install. Existing values are the
                      defaults unless --force is also supplied.
  --force             With --wizard, ignore the installed config as prompt
                      defaults and start from the current example. The old
                      config is backed up only after the candidate validates.
                      It does not mean "skip validation" and is rejected alone.
  --with-baseline     Run the official kernel io.cost coefficient benchmark
                      against the configured persistent file target. It runs
                      six fio passes (about 12 minutes minimum at defaults)
                      and saturates that disk; use a quiet maintenance window.
                      The cache is reused only when its device/target identity
                      still matches; it has no time-based expiry.
  --restart-docker    Restart docker.socket and docker.service after applying
                      the policy. This disrupts running containers. Without
                      it, changed Docker daemon defaults take effect after
                      a later deliberate restart.
  --help              Show this help and exit.

Files and ownership
  Read from this checkout: host-setup.env.example, unit templates, scripts,
  and the io.cost generator under scripts/debian-install-v2/tools/.
  Read on the host: /proc/meminfo, /proc/swaps, Docker mount discovery, and
  Docker/Buildx state.
  Write: /etc/mdt/host-setup.env and rendered /etc/systemd/system units;
  /var/lib/mdt/io-baseline.env and its persistent test file when a baseline
  is run; owned keys in /etc/docker/daemon.json. An existing config is backed
  up as /etc/mdt/host-setup.env.bak-TIMESTAMP before replacement.

Safety and result
  The wizard validates the complete candidate before any host policy is
  changed. A nonzero exit means the requested action did not complete; inspect
  the printed error before retrying. The installer is idempotent.
EOF
}

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
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)"; exit 2 ;;
  esac
done
# Host setup changes the host's systemd units, Docker daemon configuration, and
# cgroup tree. A devcontainer can be UID 0 and can even have the Docker socket,
# but it still has its own /etc, PID 1, and mount namespace; mutating those would
# not configure the host and could leave a misleading partial setup behind.
if [ -e /.dockerenv ] || [ -e /run/.containerenv ] || [ -n "${container:-}" ]; then
  echo "ERROR: host-setup/install.sh must run from a host shell, not inside a devcontainer or other container" >&2
  echo "UID 0 in a container is not host root; leave the devcontainer and run: sudo ./host-setup/install.sh --wizard" >&2
  exit 2
fi
# After the container check so a devcontainer gets the actionable host-context
# error even when the invoking user is not root. --help already exited above.
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

_pid1_comm=$(cat /proc/1/comm 2>/dev/null || true)
if [ "$_pid1_comm" != systemd ] || [ ! -d /run/systemd/system ]; then
  echo "ERROR: host-setup/install.sh requires systemd as PID 1 and /run/systemd/system; run it on the Docker host" >&2
  exit 2
fi

if [ "$FORCE" = 1 ] && [ "$WIZARD" != 1 ]; then
  echo "ERROR: --force requires --wizard; refusing to re-seed an unvalidated example" >&2
  exit 2
fi

DOCKER_BIN=/usr/bin/docker
if [ ! -x "$DOCKER_BIN" ]; then
  echo "ERROR: $DOCKER_BIN is unavailable; install Docker Engine before applying MDT host setup" >&2
  exit 2
fi
if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
  echo "ERROR: Docker is unavailable or unreachable; start dockerd and re-run install.sh" >&2
  exit 2
fi
if ! "$DOCKER_BIN" buildx version >/dev/null 2>&1; then
  echo "ERROR: Docker Buildx is unavailable; install/enable the Buildx CLI plugin and re-run install.sh" >&2
  exit 2
fi

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
CONFIG_DIR=/etc/mdt
CONFIG_PATH="$CONFIG_DIR/host-setup.env"
BUILDX_CONFIG_DIR=/etc/mdt/buildx
HAD_CONFIG=0
if [ -f "$CONFIG_PATH" ]; then
  HAD_CONFIG=1
fi

# Keep all candidate work outside /etc/mdt. In particular, do not create the
# destination directory, back up the old config, or persist the example until
# the exact file that will be sourced below has passed the data-only validator.
CANDIDATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mdt-host-setup.XXXXXXXX")"
trap 'rm -rf -- "$CANDIDATE_DIR"' EXIT
CANDIDATE_PATH="$CANDIDATE_DIR/host-setup.env"
if [ "$WIZARD" = 1 ]; then
  # Copy only to the safe candidate path so the wizard can use existing values
  # as defaults without being able to damage the installed config on failure.
  if [ "$HAD_CONFIG" = 1 ] && [ "$FORCE" = 0 ]; then
    cp -- "$CONFIG_PATH" "$CANDIDATE_PATH"
  fi
  python3 "$HERE/mdt-host-setup-wizard.py" \
    --example "$HERE/host-setup.env.example" \
    --output "$CANDIDATE_PATH" \
    --io-baseline-script "$HERE/scripts/mdt-io-baseline.py" \
    --install-script "$HERE/install.sh" \
    --skip-run-offer
elif [ "$HAD_CONFIG" = 1 ]; then
  cp -- "$CONFIG_PATH" "$CANDIDATE_PATH"
else
  cp -- "$HERE/host-setup.env.example" "$CANDIDATE_PATH"
fi

# Validate the complete file before sourcing it. The wizard owns this
# non-interactive validator too, so a hand-edited file cannot bypass the
# same memory hierarchy, live aggregate, and closed-vocabulary checks used by
# --wizard. It parses the file as data (not shell), and reads this host's live
# MemAvailable; a bad config fails before apt, units, Docker, or systemd are
# changed.
if ! python3 "$HERE/mdt-host-setup-wizard.py" \
  --validate-config "$CANDIDATE_PATH" \
  --example "$HERE/host-setup.env.example" \
  --meminfo-path /proc/meminfo; then
  if [ "$HAD_CONFIG" = 0 ] && [ "$WIZARD" = 0 ]; then
    echo "ERROR: no valid /etc/mdt/host-setup.env exists; the shipped example is incomplete" >&2
    echo "Run ./install.sh --wizard to create and review a host-sized configuration, then re-run install.sh" >&2
  else
    echo "ERROR: candidate host-setup.env failed strict validation; no host policy/config changes were applied" >&2
  fi
  exit 2
fi

# Candidate validation passed. Only now may the installed config directory,
# backup, and replacement be touched.
mkdir -p "$CONFIG_DIR" /var/lib/mdt
install -d -o root -g root -m 0755 "$BUILDX_CONFIG_DIR"
if [ "$WIZARD" = 1 ] || [ "$HAD_CONFIG" = 0 ]; then
  if [ "$HAD_CONFIG" = 1 ]; then
    backup="$CONFIG_PATH.bak-$(date +%Y%m%dT%H%M%S)"
    cp -- "$CONFIG_PATH" "$backup"
    echo "backed up existing config to $backup"
  fi
  install -m 0644 "$CANDIDATE_PATH" "$CONFIG_PATH"
fi

# shellcheck disable=SC1090,SC1091
. "$CONFIG_PATH"

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
  grep -qE "^${key}=" "$CONFIG_PATH" 2>/dev/null || MISSING_KEYS="$MISSING_KEYS $key"
done < <(grep -oE '^[A-Z_][A-Z0-9_]*=' "$HERE/host-setup.env.example" | sed 's/=$//')
if [ -n "$MISSING_KEYS" ]; then
  echo "ERROR: /etc/mdt/host-setup.env is missing declared keys:${MISSING_KEYS}" >&2
  echo "Add them from host-setup.env.example (or use --wizard), review the result, then re-run install.sh." >&2
  exit 2
fi

case "${BUILDX_ACCIDENTAL_CONTAINER_POLICY:-}" in
  terminate|report-only) ;;
  *) echo "ERROR: BUILDX_ACCIDENTAL_CONTAINER_POLICY must be exactly terminate or report-only" >&2; exit 2 ;;
esac
if [ -z "${DEV_BUILDKITD_IMAGE:-}" ]; then
  echo "ERROR: DEV_BUILDKITD_IMAGE is empty; refusing to start the managed BuildKit service" >&2
  exit 2
fi

# Device node for the static IO*Max lines (render-time; the runtime script
# re-discovers independently, so an install-time miss only drops the statics).
if [ -z "${IO_DEV_PATH:-}" ]; then
  for path in /var/lib/docker /; do
    _io_source=$(findmnt -no SOURCE --target "$path" 2>/dev/null || true)
    case "$_io_source" in
      /dev/?*) IO_DEV_PATH="$_io_source"; break ;;
    esac
  done
fi
if [ -n "${IO_DEV_PATH:-}" ]; then
  echo "io device for static caps: $IO_DEV_PATH"
else
  echo "WARN: findmnt did not expose a host /dev block-device node — static IO caps will be omitted"
fi

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
_nproc=$(nproc 2>/dev/null) || {
  echo "ERROR: could not determine host CPU count with nproc; refusing to invent a CPU quota" >&2
  exit 2
}
case "$_nproc" in
  ''|*[!0-9]*) echo "ERROR: nproc returned an invalid CPU count: $_nproc" >&2; exit 2 ;;
esac
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

# systemd accepts K/M/G suffixes in MemorySwapMax, but the cascade arithmetic
# below needs bytes. Normalize an explicit root value before deriving child
# values; otherwise a perfectly valid answer such as 4G reaches Bash's
# arithmetic evaluator and aborts the installer.
_size_to_bytes() {
  python3 - "$1" <<'PY'
import re
import sys
from decimal import Decimal, InvalidOperation

raw = sys.argv[1].strip()
match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGTPE]?)i?[Bb]?", raw, re.IGNORECASE)
if not match:
    raise SystemExit(1)
try:
    number = Decimal(match.group(1))
except InvalidOperation:
    raise SystemExit(1)
multiplier = {
    "": Decimal(1),
    "K": Decimal(1024),
    "M": Decimal(1024**2),
    "G": Decimal(1024**3),
    "T": Decimal(1024**4),
    "P": Decimal(1024**5),
    "E": Decimal(1024**6),
}[match.group(2).upper()]
value = int(number * multiplier)
if value < 0:
    raise SystemExit(1)
print(value)
PY
}
if [ -n "${DEV_SWAP_MAX:-}" ]; then
  if ! _swap_max_bytes=$(_size_to_bytes "$DEV_SWAP_MAX"); then
    echo "ERROR: DEV_SWAP_MAX is not a systemd size that can be converted to bytes: $DEV_SWAP_MAX" >&2
    exit 2
  fi
  DEV_SWAP_MAX="$_swap_max_bytes"
fi
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
# fio/pv: dependencies of the official io.cost baseline benchmark.
# systemd-oomd: without it every ManagedOOM*
# setting in dev-background.slice is a silent no-op (separate package on Debian).
apt-get update -qq || echo "WARN: apt-get update failed — install may use a stale index"
apt-get install -y --no-install-recommends fio pv systemd-oomd \
  || echo "WARN: apt install failed — install fio + pv + systemd-oomd manually"

MDT_BASELINE="${IO_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}"
MDT_TESTFILE="${IO_BASELINE_TESTFILE:-/var/lib/mdt/iocost-coef-fio.testfile}"

echo "== render + install units =="
RENDER_VARS="DEV_CPU_QUOTA DEV_ZSWAP_WRITEBACK DEV_SWAP_MAX \
DEV_MEMORY_LOW DEV_MEMORY_HIGH DEV_MEMORY_MAX \
DEV_INTERACTIVE_MEMORY_MIN DEV_INTERACTIVE_MEMORY_LOW DEV_INTERACTIVE_MEMORY_HIGH DEV_INTERACTIVE_MEMORY_MAX \
DEV_INTERACTIVE_MEMORY_SWAP_MAX DEV_INTERACTIVE_CPU_WEIGHT DEV_INTERACTIVE_CPU_QUOTA \
DEV_INTERACTIVE_IO_WEIGHT DEV_INTERACTIVE_ZSWAP_WRITEBACK \
DEV_BACKGROUND_MEMORY_MIN DEV_BACKGROUND_MEMORY_LOW DEV_BACKGROUND_MEMORY_HIGH DEV_BACKGROUND_MEMORY_MAX DEV_BACKGROUND_MEMORY_SWAP_MAX \
DEV_BACKGROUND_CPU_WEIGHT DEV_BACKGROUND_CPU_QUOTA DEV_BACKGROUND_IO_WEIGHT \
DEV_BACKGROUND_OOM_PRESSURE_LIMIT DEV_BACKGROUND_ZSWAP_WRITEBACK \
DEV_MEMORY_MIN_GUARANTEED_CEILING DEV_MEMORY_MIN_GUARANTEED_LOW DEV_MEMORY_MIN_GUARANTEED_HIGH DEV_MEMORY_MIN_GUARANTEED_MAX \
DEV_GATES_MEMORY_MIN DEV_GATES_MEMORY_LOW DEV_GATES_MEMORY_HIGH DEV_GATES_MEMORY_MAX DEV_GATES_MEMORY_SWAP_MAX \
DEV_GATES_CPU_WEIGHT DEV_GATES_CPU_QUOTA DEV_GATES_IO_WEIGHT DEV_GATES_OOM_PRESSURE_LIMIT \
DEV_GATES_ZSWAP_WRITEBACK \
DEV_BUILDKITD_MEMORY_MIN DEV_BUILDKITD_MEMORY_LOW DEV_BUILDKITD_MEMORY_HIGH DEV_BUILDKITD_MEMORY_MAX DEV_BUILDKITD_MEMORY_SWAP_MAX \
DEV_BUILDKITD_CPU_WEIGHT DEV_BUILDKITD_CPU_QUOTA DEV_BUILDKITD_IO_WEIGHT DEV_BUILDKITD_IMAGE \
DEV_BUILDKITD_MAX_PARALLELISM \
DEV_BUILDKITD_ZSWAP_WRITEBACK \
DEV_STATIC_RBW DEV_STATIC_WBW DEV_STATIC_RIOPS DEV_STATIC_WIOPS \
DOCKER_SCOPE_BACKSTOP_MEMORY_MAX DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX \
IO_DEV_PATH WATCHER_INTERVAL"
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
render "$HERE/buildkitd.toml.in"              /etc/mdt/buildkitd.toml
render "$HERE/units/mdt-host-slices.timer.in"  /etc/systemd/system/mdt-host-slices.timer
install -m 0644 "$HERE/units/mdt-host-slices.service" /etc/systemd/system/mdt-host-slices.service
if [ "${INOTIFY_OK:-}" = 1 ]; then
  install -m 0644 "$HERE/units/mdt-dev-cap-watcher.service" /etc/systemd/system/mdt-dev-cap-watcher.service
fi
# Not gated by INOTIFY_OK: this one reacts via `docker events`, not inotify —
# see scripts/mdt-io-cap-watcher.sh for why it can't use the same mechanism
# as mdt-dev-cap-watcher.py above.
install -m 0644 "$HERE/units/mdt-io-cap-watcher.service" /etc/systemd/system/mdt-io-cap-watcher.service
install -m 0644 "$HERE/units/mdt-buildkit-guard.service" /etc/systemd/system/mdt-buildkit-guard.service

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
install -d -m 0755 /usr/local/lib/mdt
install -m 0755 "$HERE/../../scripts/debian-install-v2/tools/iocost_coef_gen.py" \
  /usr/local/lib/mdt/iocost_coef_gen.py
install -m 0755 "$HERE/scripts/mdt-slice-audit.py"     /usr/local/sbin/mdt-slice-audit.py
install -m 0755 "$HERE/scripts/mdt-buildkit-guard.py"  /usr/local/sbin/mdt-buildkit-guard.py
install -m 0755 "$HERE/../scripts/mdt_buildkit_builder.py" /usr/local/sbin/mdt-buildkit-builder.py
install -m 0755 "$HERE/scripts/check.sh"               /usr/local/sbin/mdt-host-check.sh
install -m 0755 "$HERE/scripts/docker-safe-restart.sh" /usr/local/sbin/mdt-docker-safe-restart
install -D -m 0644 "$HERE/etc/profile.d/mdt-buildkit.sh" /etc/profile.d/mdt-buildkit.sh
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
for _slice in dev.slice dev-interactive.slice dev-background.slice dev-memory_min_guaranteed.slice dev-gates.slice dev-buildkitd.slice; do
  _load_state=$(systemctl show "$_slice" --property=LoadState --value)
  _fragment=$(systemctl show "$_slice" --property=FragmentPath --value)
  if [ "$_load_state" != loaded ] || [ -z "$_fragment" ]; then
    echo "ERROR: rendered $_slice is not a loaded systemd unit (LoadState=$_load_state FragmentPath=$_fragment)" >&2
    exit 2
  fi
done
_parent_load_state=$(systemctl show "$DOCKER_DAEMON_CGROUP_PARENT" --property=LoadState --value)
_parent_fragment=$(systemctl show "$DOCKER_DAEMON_CGROUP_PARENT" --property=FragmentPath --value)
if [ "$_parent_load_state" != loaded ] || [ -z "$_parent_fragment" ]; then
  echo "ERROR: DOCKER_DAEMON_CGROUP_PARENT=$DOCKER_DAEMON_CGROUP_PARENT is not a loaded systemd slice (LoadState=$_parent_load_state FragmentPath=$_parent_fragment); refusing to start governed services" >&2
  exit 2
fi
echo "verified Docker default cgroup parent: $DOCKER_DAEMON_CGROUP_PARENT (loaded, fragment=$_parent_fragment)"
systemctl start dev.slice dev-interactive.slice dev-background.slice dev-memory_min_guaranteed.slice \
  dev-gates.slice dev-buildkitd.slice
systemctl enable mdt-host-slices.service          # boot-time apply
systemctl enable --now mdt-host-slices.timer      # periodic sweep
if systemctl is-active --quiet mdt-buildkitd.service; then
  echo "restarting mdt-buildkitd.service; active builds will be interrupted"
  systemctl restart mdt-buildkitd.service
else
  systemctl enable --now mdt-buildkitd.service
fi
systemctl enable mdt-buildkitd.service
for _attempt in $(seq 1 30); do
  [ -S /run/mdt-buildkitd/buildkitd.sock ] && break
  sleep 1
done
if [ ! -S /run/mdt-buildkitd/buildkitd.sock ]; then
  echo "ERROR: mdt-buildkitd.service did not expose /run/mdt-buildkitd/buildkitd.sock" >&2
  systemctl status mdt-buildkitd.service --no-pager || true
  exit 2
fi
python3 "$HERE/scripts/mdt-buildkit-guard.py" \
  --config /etc/mdt/host-setup.env --docker "$DOCKER_BIN" \
  --verify-managed-container mdt-buildkitd
python3 "$HERE/../scripts/mdt_buildkit_builder.py" configure \
  --docker "$DOCKER_BIN" --endpoint unix:///run/mdt-buildkitd/buildkitd.sock \
  --buildx-config "$BUILDX_CONFIG_DIR"
# Buildx's shared state contains only the public remote-node registration. Keep
# the directory root-owned and readable, but not writable, for ordinary users:
# their exported BUILDX_BUILDER selects this node directly and never falls back
# to a per-user Docker builder. Any user who can use Docker can read the
# registration; only root can change which endpoint the host profile selects.
find "$BUILDX_CONFIG_DIR" -xdev -type d -exec chown root:root {} + -exec chmod 0755 {} +
find "$BUILDX_CONFIG_DIR" -xdev -type f -exec chown root:root {} + -exec chmod 0644 {} +
systemctl enable --now mdt-buildkit-guard.service
systemctl restart mdt-buildkit-guard.service
if [ "$INOTIFY_OK" = 1 ]; then
  systemctl enable --now mdt-dev-cap-watcher.service  # reactive per-container MemoryMax
fi
systemctl enable mdt-io-cap-watcher.service         # reactive per-container IO caps (docker events)

if [ "$WITH_BASELINE" = 1 ]; then
  echo "== io.cost baseline (six fio runs — disk will be saturated for ~12 min) =="
  IO_BASELINE_ENV="$MDT_BASELINE" IO_BASELINE_TESTFILE="$MDT_TESTFILE" \
    IO_DEV_PATH="${IO_DEV_PATH:-}" \
    /usr/local/sbin/mdt-io-baseline.py --force --output "$MDT_BASELINE" \
    --testfile "$MDT_TESTFILE" || echo "WARN: baseline failed — statics remain in force"
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
