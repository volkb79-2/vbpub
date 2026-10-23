#!/usr/bin/env bash
# mdt host-setup installer — prepares a host for tiered devcontainer/test work
# (dev.slice root ceiling + dev-interactive.slice + dev-background.slice +
# dev-gates.slice + dev-memory_min_guaranteed.slice +
# dev-buildkitd.slice + runtime IO governance + /etc/docker/daemon.json,
# which this owns fully).
#
#   sudo ./install.sh [--wizard] [--with-baseline] [--reset] [--restart-docker]
#   sudo ./install.sh --wizard              # first install or preserve old values
#   sudo ./install.sh --wizard --reset      # re-seed answers from this example
#   sudo ./install.sh --with-baseline       # remeasure in a quiet disk window
#   sudo ./install.sh --observer-only       # provision the cgprofile observer directory only
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
# additionally runs the shared Linux-derived io.cost benchmark (~12 min of saturated disk — quiet
# window!). --reset is accepted only with --wizard; it deliberately starts the
# wizard from the current example instead of using the installed config as
# defaults. After successful candidate validation the old config is backed up
# and replaced. Without --reset, --wizard preserves existing values as defaults.
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
  sudo ./install.sh --wizard [--reset] [--with-baseline] [--restart-docker]
  sudo ./install.sh --with-baseline
  sudo ./install.sh --restart-docker
  sudo ./install.sh --observer-only
  sudo ./install.sh --require-damon-disable   # make a failed DAMON_STAT write fatal
  sudo ./install.sh --help

Options
  --wizard            Ask every host policy question, write a temporary
                      candidate, validate it, then install and apply it.
                      Required on a first install. Existing values are the
                      defaults unless --reset is also supplied.
  --reset             With --wizard, ignore the installed config as prompt
                      defaults and start from the current example plus live
                      host proposals. The old config is backed up only after
                      the candidate validates. It does not mean "skip
                      validation" and is rejected alone. This option resets
                      wizard input defaults; it does not delete the old file.
  --with-baseline     Run the shared Linux-derived io.cost coefficient benchmark
                      against the configured persistent file target. It runs
                      six fio passes (about 12 minutes minimum at defaults)
                      and saturates that disk; use a quiet maintenance window.
                      Without --wizard, current benchmark results are reused
                      when their device/target identity still matches;
                      --with-baseline deliberately remeasures. There is no
                      time-based expiry. With --wizard, the question is asked
                      near the start, one background run is waited for, and
                      the installer never starts a second run.
  --restart-docker    Restart docker.socket and docker.service after applying
                      the policy. This disrupts running containers. Without
                      it, changed Docker daemon defaults take effect after
                      a later deliberate restart.
  --restart-buildkitd Allow restarting mdt-buildkitd.service when its rendered unit
                      or config CHANGED while a build may be active. Without it an
                      unchanged service is left alone, and a needed restart during
                      an active build is NOT done (the install says so and exits
                      non-zero; re-run with this flag at a moment you choose).
  --observer-only     Install the cgprofile-observer group and its dedicated
                      /run/cgprofile/observer tmpfiles rule. Requires the
                      existing /run/cgprofile control directory to be a
                      real root:docker 0770 directory. Does not run the
                      general host policy workflow.
  --help              Show this help and exit.

Files and ownership
  Read from this checkout: host-setup.env.example, unit templates, scripts,
  and the io.cost generator under scripts/debian-install-v2/tools/.
  Read on the host: /proc/meminfo, /proc/swaps, Docker mount discovery, and
  Docker/Buildx state.
  Write: /etc/mdt/host-setup.env and rendered /etc/systemd/system units;
  /var/lib/mdt/io-baseline.env and its persistent test file when a benchmark
  is run; owned keys in /etc/docker/daemon.json. An existing config is backed
  up as /etc/mdt/host-setup.env.bak-TIMESTAMP before replacement.

Safety and result
  The wizard validates the complete candidate before any host policy is
  changed. A nonzero exit means the requested action did not complete; inspect
  the printed error before retrying. The installer is idempotent.
EOF
}

provision_cgprofile_observer() {
  local root="${1:?host root argument is required}"
  local prefix=""
  if [ "$root" != / ]; then
    [[ "$root" == /* ]] || { echo "ERROR: host root must be absolute: $root" >&2; return 2; }
    prefix="${root%/}"
  fi
  local control_directory="${prefix}/run/cgprofile"
  local observer_directory="${control_directory}/observer"
  local sysusers_file="${prefix}/etc/sysusers.d/mdt-cgprofile.conf"
  local tmpfiles_file="${prefix}/etc/tmpfiles.d/mdt-cgprofile-observer.conf"

  if [ -L "$control_directory" ] || [ ! -d "$control_directory" ]; then
    echo "ERROR: $control_directory must already be a real directory; run the full host setup first" >&2
    return 2
  fi
  local docker_entry docker_gid control_metadata
  docker_entry="$(getent group docker 2>/dev/null)" || {
    echo "ERROR: could not resolve the host docker group" >&2
    return 2
  }
  IFS=: read -r _ _ docker_gid _ <<< "$docker_entry"
  [[ "$docker_gid" =~ ^[1-9][0-9]*$ ]] || {
    echo "ERROR: could not resolve a valid host docker group GID" >&2
    return 2
  }
  control_metadata="$(stat -c '%u %g %a' "$control_directory")" || {
    echo "ERROR: could not inspect $control_directory" >&2
    return 2
  }
  [[ "$control_metadata" == "0 $docker_gid 770" ]] || {
    echo "ERROR: $control_directory must be root:docker 0770; found $control_metadata" >&2
    return 2
  }
  local existing_observer_group existing_observer_gid="" existing_observer_metadata
  existing_observer_group="$(getent group cgprofile-observer 2>/dev/null || true)"
  if [[ -n "$existing_observer_group" ]]; then
    IFS=: read -r _ _ existing_observer_gid _ <<< "$existing_observer_group"
    [[ "$existing_observer_gid" =~ ^[1-9][0-9]*$ ]] || {
      echo "ERROR: cgprofile-observer has an invalid GID: ${existing_observer_gid:-<empty>}" >&2
      return 2
    }
    [[ "$existing_observer_gid" != "$docker_gid" ]] || {
      echo "ERROR: cgprofile-observer GID must differ from the docker group GID ($docker_gid)" >&2
      return 2
    }
  fi
  if [ -L "$observer_directory" ]; then
    echo "ERROR: refusing symlink at $observer_directory" >&2
    return 2
  fi
  if [ -e "$observer_directory" ]; then
    if [ ! -d "$observer_directory" ]; then
      echo "ERROR: refusing non-directory at $observer_directory; inspect and remove or repair this path before rerunning --observer-only" >&2
      return 2
    fi
    if [[ -z "$existing_observer_gid" ]]; then
      echo "ERROR: refusing pre-existing $observer_directory because cgprofile-observer does not exist; inspect the path and group before rerunning --observer-only" >&2
      return 2
    fi
    existing_observer_metadata="$(stat -c '%u %g %a' "$observer_directory")" || {
      echo "ERROR: could not inspect pre-existing $observer_directory" >&2
      return 2
    }
    [[ "$existing_observer_metadata" == "0 $existing_observer_gid 751" ]] || {
      echo "ERROR: refusing drifted $observer_directory: found $existing_observer_metadata; expected 0 $existing_observer_gid 751. Inspect and repair this path before rerunning --observer-only" >&2
      return 2
    }
  fi

  install -D -m 0644 "$HERE/units/mdt-cgprofile.sysusers" "$sysusers_file"
  install -D -m 0644 "$HERE/units/mdt-cgprofile-observer.conf" "$tmpfiles_file"
  systemd-sysusers "$sysusers_file"
  local observer_gid observer_metadata
  local observer_group_entry
  observer_group_entry="$(getent group cgprofile-observer 2>/dev/null)" \
    || { echo 'ERROR: could not resolve cgprofile-observer after systemd-sysusers' >&2; return 1; }
  IFS=: read -r _ _ observer_gid _ <<< "$observer_group_entry"
  [[ "$observer_gid" =~ ^[1-9][0-9]*$ ]] \
    || { echo "ERROR: cgprofile-observer has an invalid GID: $observer_gid" >&2; return 1; }
  [[ "$observer_gid" != "$docker_gid" ]] \
    || { echo "ERROR: cgprofile-observer GID must differ from the docker group GID ($docker_gid)" >&2; return 1; }
  systemd-tmpfiles --create "$tmpfiles_file" \
    || { echo "ERROR: systemd-tmpfiles --create failed for $tmpfiles_file" >&2; return 1; }
  [ -d "$observer_directory" ] && [ ! -L "$observer_directory" ] \
    || { echo "ERROR: $observer_directory is not a real directory" >&2; return 1; }
  observer_metadata="$(stat -c '%u %g %a' "$observer_directory")" \
    || { echo "ERROR: $observer_directory was not created by host setup" >&2; return 1; }
  [[ "$observer_metadata" == "0 $observer_gid 751" ]] \
    || { echo "ERROR: $observer_directory has unexpected owner, group, or mode: $observer_metadata" >&2; return 1; }
  echo "cgprofile observer directory ready: $observer_directory (root:$observer_gid 0751)"
}

WITH_BASELINE=0
RESET=0
AUTO_RESTART_DOCKER=0
RESTART_BUILDKITD=0
WIZARD=0
OBSERVER_ONLY=0
REQUIRE_DAMON=0
for arg in "$@"; do
  case "$arg" in
    --wizard) WIZARD=1 ;;
    --observer-only) OBSERVER_ONLY=1 ;;
    --with-baseline) WITH_BASELINE=1 ;;
    --reset) RESET=1 ;;
    --restart-docker) AUTO_RESTART_DOCKER=1 ;;
    --restart-buildkitd) RESTART_BUILDKITD=1 ;;
    --require-damon-disable) REQUIRE_DAMON=1 ;;
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

if [ "$OBSERVER_ONLY" = 1 ]; then
  if [ "$WIZARD" = 1 ] || [ "$WITH_BASELINE" = 1 ] || [ "$RESET" = 1 ] \
    || [ "$AUTO_RESTART_DOCKER" = 1 ]; then
    echo "ERROR: --observer-only cannot be combined with general host-setup options" >&2
    exit 2
  fi
  for tool in install systemd-sysusers systemd-tmpfiles getent stat cut; do
    command -v "$tool" >/dev/null 2>&1 \
      || { echo "ERROR: required host command is unavailable: $tool" >&2; exit 2; }
  done
  provision_cgprofile_observer /
  exit $?
fi

if [ "$RESET" = 1 ] && [ "$WIZARD" != 1 ]; then
  echo "ERROR: --reset requires --wizard; refusing to re-seed an unvalidated example" >&2
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
# The docker-buildx shim (installed below) hands every call to the packaged plugin. It
# looks in these directories, in this order; refuse before touching the host if none holds
# one, rather than replace a working `docker buildx` with a shim that has nothing to run.
BUILDX_SHIM_TARGET=/usr/local/lib/docker/cli-plugins/docker-buildx
_mdt_real_buildx=
for _dir in /usr/local/libexec/docker/cli-plugins /usr/lib/docker/cli-plugins /usr/libexec/docker/cli-plugins; do
  if [ -x "$_dir/docker-buildx" ]; then _mdt_real_buildx="$_dir/docker-buildx"; break; fi
done
if [ -z "$_mdt_real_buildx" ]; then
  echo "ERROR: no packaged Buildx plugin found in /usr/local/libexec, /usr/lib or /usr/libexec docker/cli-plugins; the docker-buildx shim has nothing to run. Install docker-buildx-plugin and re-run install.sh" >&2
  exit 2
fi

# --with-baseline is an explicit request for the wizard to start the
# measurement before its resource questions. Make the benchmark prerequisites
# available before launching that wizard; otherwise the wizard would have to
# defer the measurement until after it exits, defeating the one-background-job
# lifecycle and causing the operator to see a misleading second opportunity.
if [ "$WITH_BASELINE" = 1 ] && [ "$WIZARD" = 1 ] && { ! command -v fio >/dev/null 2>&1 || ! command -v pv >/dev/null 2>&1; }; then
  echo "== benchmark prerequisites (before wizard) =="
  echo "--with-baseline needs fio and pv before the wizard can start its one background benchmark"
  apt-get update -qq || echo "WARN: apt-get update failed — trying the installed package index"
  apt-get install -y --no-install-recommends fio pv || echo "WARN: could not install fio/pv before the wizard — the benchmark will be deferred and attempted once after package installation"
fi

echo "== container memory inotify watcher: availability check =="
# mdt-container-memory-inotify-watcher.py needs a working inotify_init1() — true on any
# kernel since 2.6.27, but checked explicitly rather than let a confusing
# errno surface later inside the service. A failure here skips installing
# the optional per-container memory watcher; slice-level memory limits and
# the separate periodic IO/cgroup-flag reconciliation still apply.
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
  echo "WARN: inotify_init1() failed on this host — skipping the optional per-container memory watcher; no periodic memory-cap fallback exists, while parent slice limits and the separate IO reconciliation remain active"
fi

echo "== config =="
CONFIG_DIR=/etc/mdt
CONFIG_PATH="$CONFIG_DIR/host-setup.env"
BUILDX_CONFIG_DIR=/etc/mdt/buildx
# Replace (or append) the marked block of KEY=VALUE lines in a pam_env-style file such as
# /etc/environment: idempotent, keeps every other line byte for byte, never duplicates a
# key that is already set OUTSIDE the block (that is reported, not overwritten).
_mdt_install_environment_block() {
  local block_file="$1" target="$2" tmp key
  tmp="$(mktemp "${target}.mdt.XXXXXXXX")"
  [ -f "$target" ] || : > "$target"
  awk '/^# BEGIN mdt-buildkit /{skip=1} !skip{print} /^# END mdt-buildkit$/{skip=0}' "$target" > "$tmp"
  while IFS= read -r key; do
    case "$key" in \#*|"") continue ;; esac
    key="${key%%=*}"
    if grep -q "^${key}=" "$tmp"; then
      echo "WARN: $target already sets $key ($(grep -m1 "^${key}=" "$tmp")) outside the MDT BuildKit block; pam_env applies the later assignment, which is the MDT block's ${key}=$(grep -m1 "^${key}=" "$block_file" | cut -d= -f2-). Remove the older line if it is not intended; the docker-buildx shim selects mdt-managed either way." >&2
    fi
  done < "$block_file"
  cat "$block_file" >> "$tmp"
  chmod 0644 "$tmp"
  mv -f -- "$tmp" "$target"
}
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
BASELINE_STATUS_PATH="$CANDIDATE_DIR/benchmark.status"
if [ "$WIZARD" = 1 ]; then
  # Copy only to the safe candidate path so the wizard can use existing values
  # as defaults without being able to damage the installed config on failure.
  if [ "$HAD_CONFIG" = 1 ] && [ "$RESET" = 0 ]; then
    cp -- "$CONFIG_PATH" "$CANDIDATE_PATH"
  fi
  WIZARD_ARGS=(
    --example "$HERE/host-setup.env.example"
    --output "$CANDIDATE_PATH"
    --io-baseline-script "$HERE/scripts/mdt-io-baseline.py"
    --install-script "$HERE/install.sh"
    --baseline-status "$BASELINE_STATUS_PATH"
    --skip-run-offer
  )
  [ "$WITH_BASELINE" = 1 ] && WIZARD_ARGS+=(--with-baseline)
  python3 "$HERE/mdt-host-setup-wizard.py" "${WIZARD_ARGS[@]}"
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

# Candidate validation passed. Everything from here to the COMMIT POINT below is
# PRE-FLIGHT: it reads the validated candidate (never the installed file), may refuse
# (exit 2), and mutates NOTHING on the host. Validate everything first, then act
# (MDT-HS-02: the guard refusal used to disable the running guard first).
# shellcheck disable=SC1090,SC1091
. "$CANDIDATE_PATH"

# A config that predates a key added to host-setup.env.example since has NO
# line for it at all (not even empty) -- render()'s own "empty/unset =
# directive dropped" rule then silently removes every directive that uses
# it from the rendered unit(s), which for e.g. dev-gates.slice means an
# UNBOUNDED admission capacity object with no mechanical signal at all
# (RG-55 P8 review round 1, B3). Compares NAME presence only (`^KEY=`), not
# values: several keys are intentionally shipped empty in the example
# itself (DEV_MEMORY_MIN_GUARANTEED_CEILING, DEV_BUILDKITD_CPU_QUOTA,
# IO_DEV_PATH) -- "declared, empty" is a deliberate choice, not a finding.
#
# RUNTIME-OPTIONAL keys (the DEV_IO_*_MAX pins, the per-tier IO pins, and
# IO_BASELINE_ROTATIONAL_FLAG) are exempt: for them an absent line means "not
# set" (a policy default), not "silently unbounded", so a plain install.sh works
# on an existing env file and prints one NOTICE instead of refusing.  The list is
# host-setup/runtime-optional-keys.re, read by the Python validator and the shell helper alike.
log(){ echo "$*" >&2; }
# shellcheck source=./scripts/mdt-container-io-caps.lib.sh
. "$HERE/scripts/mdt-container-io-caps.lib.sh"
MISSING_KEYS=$(_mdt_missing_declared_keys "$CANDIDATE_PATH" "$HERE/host-setup.env.example" required | sed 's/^/ /' | tr -d '\n')
MISSING_OPTIONAL=$(_mdt_missing_declared_keys "$CANDIDATE_PATH" "$HERE/host-setup.env.example" optional | wc -l)
if [ "$MISSING_OPTIONAL" -gt 0 ]; then
  echo "NOTICE: $MISSING_OPTIONAL runtime-optional key(s) are absent from $CONFIG_PATH (the DEV_IO_*_MAX pins, IO_BASELINE_ROTATIONAL_FLAG and the IO_WRITEBACK_*/IO_ROOT_QUEUE_* writeback keys, which read as `kernel`); they are treated as NOT SET. Run install.sh --wizard to review and add them." >&2
fi
if [ -n "$MISSING_KEYS" ]; then
  echo "ERROR: /etc/mdt/host-setup.env is missing declared keys:${MISSING_KEYS}" >&2
  echo "Add them from host-setup.env.example (or use --wizard), review the result, then re-run install.sh." >&2
  exit 2
fi

GUARD_RECORD=/var/lib/mdt/guard-terminate.confirmed
GUARD_DECISION=$(_mdt_guard_decision "${BUILDX_ACCIDENTAL_CONTAINER_POLICY:-}" "$WIZARD" "$GUARD_RECORD" "${DEV_BUILDKITD_IMAGE:-}")
case "$GUARD_DECISION" in
  record)
    echo "WARN: BUILDX_ACCIDENTAL_CONTAINER_POLICY=terminate may remove active Docker-container Buildx workers and interrupt builds; second wizard confirmation received and recorded at the commit point."
    ;;
  keep)
    echo "WARN: BUILDX_ACCIDENTAL_CONTAINER_POLICY=terminate was already confirmed ($(grep -m1 '^confirmed_at=' "$GUARD_RECORD")) and is unchanged; the running guard is kept."
    ;;
  clear) ;;
  refuse)
    echo "ERROR: BUILDX_ACCIDENTAL_CONTAINER_POLICY=terminate has no recorded confirmation ($GUARD_RECORD): a new or changed terminate setting (policy, BuildKit image or host) needs install.sh --wizard and a second explicit confirmation. Nothing was changed: any running mdt-buildkit-guard.service is left exactly as it was." >&2
    echo "Run ./install.sh --wizard to choose report-only or explicitly confirm terminate." >&2
    exit 2 ;;
  *) echo "ERROR: BUILDX_ACCIDENTAL_CONTAINER_POLICY must be exactly terminate or report-only" >&2; exit 2 ;;
esac
case "${DEVCONTAINER_MISSING_BIND_SOURCE_POLICY:-}" in
  create-by-spelling|fail) ;;
  *) echo "ERROR: DEVCONTAINER_MISSING_BIND_SOURCE_POLICY must be exactly create-by-spelling or fail" >&2; exit 2 ;;
esac
configured_memory_keys=()
for key in \
  WATCHER_INTERACTIVE_MEMORY_HIGH WATCHER_INTERACTIVE_MEMORY_MAX \
  WATCHER_BACKGROUND_MEMORY_HIGH WATCHER_BACKGROUND_MEMORY_MAX \
  WATCHER_GATES_MEMORY_HIGH WATCHER_GATES_MEMORY_MAX; do
  value="${!key:-}"
  case "$value" in
    ""|-) ;;
    *) configured_memory_keys+=("$key") ;;
  esac
done
if [ "${#configured_memory_keys[@]}" -gt 0 ]; then
  printf 'Per-container memory controls configured for this host: %s\n' \
    "${configured_memory_keys[*]}"
fi
case "${WATCHER_MEMORY_POLICY:-}" in
  enabled)
    if [ "${#configured_memory_keys[@]}" -eq 0 ]; then
      echo "ERROR: WATCHER_MEMORY_POLICY=enabled requires at least one explicit WATCHER_*_MEMORY_HIGH/MAX value" >&2
      exit 2
    fi
    MEMORY_WATCHER_CONFIGURED=1
    ;;
  disabled)
    if [ "${#configured_memory_keys[@]}" -gt 0 ]; then
      echo "ERROR: per-container memory values are configured while WATCHER_MEMORY_POLICY=disabled; set the policy to enabled or change those values to '-'" >&2
      exit 2
    fi
    MEMORY_WATCHER_CONFIGURED=0
    ;;
  *) echo "ERROR: WATCHER_MEMORY_POLICY must be exactly enabled or disabled" >&2; exit 2 ;;
esac
if [ "$MEMORY_WATCHER_CONFIGURED" = 1 ] && [ "$INOTIFY_OK" != 1 ]; then
  echo "ERROR: WATCHER_MEMORY_POLICY=enabled, but inotify is unavailable; refusing to continue without applying the configured policy" >&2
  exit 2
fi
if [ -z "${DEV_BUILDKITD_IMAGE:-}" ]; then
  echo "ERROR: DEV_BUILDKITD_IMAGE is empty; refusing to start the managed BuildKit service" >&2
  exit 2
fi
# The BuildKit cache source: a Docker volume name or an absolute host path (host-setup.env.example). An empty value
# keeps the historical volume. A host path must already exist as a real directory owned by the worker's uid (1000).
DEV_BUILDKITD_CACHE="${DEV_BUILDKITD_CACHE:-mdt-buildkitd-cache}"
case "$DEV_BUILDKITD_CACHE" in
  *$'\n'*)
    echo "ERROR: DEV_BUILDKITD_CACHE must be a single line" >&2
    exit 2
    ;;
  /*)
    if ! printf '%s' "$DEV_BUILDKITD_CACHE" | grep -Eq '^/[A-Za-z0-9._/+-]+$'; then
      echo "ERROR: DEV_BUILDKITD_CACHE=$DEV_BUILDKITD_CACHE has characters outside letters, digits and . _ / + -" >&2
      exit 2
    fi
    if [ ! -d "$DEV_BUILDKITD_CACHE" ] || [ -L "$DEV_BUILDKITD_CACHE" ]; then
      echo "ERROR: DEV_BUILDKITD_CACHE=$DEV_BUILDKITD_CACHE must be an existing real directory (create it, for a btrfs subvolume: btrfs subvolume create)" >&2
      exit 2
    fi
    if [ "$(stat -c %u:%g "$DEV_BUILDKITD_CACHE")" != "1000:1000" ]; then
      echo "ERROR: DEV_BUILDKITD_CACHE=$DEV_BUILDKITD_CACHE must be owned by 1000:1000 (the rootless BuildKit user): chown 1000:1000 $DEV_BUILDKITD_CACHE" >&2
      exit 2
    fi
    ;;
  *)
    if ! printf '%s' "$DEV_BUILDKITD_CACHE" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]*$'; then
      echo "ERROR: DEV_BUILDKITD_CACHE=$DEV_BUILDKITD_CACHE is neither an absolute path nor a Docker volume name" >&2
      exit 2
    fi
    ;;
esac

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
IO_SD_DEV=""
IO_SD_STATE=none          # none = no device found | unresolved = found, whole disk not resolvable | ok
if [ -n "${IO_DEV_PATH:-}" ]; then
  # systemd gets the WHOLE-DISK node by MAJ:MIN, derived (never a mapper/partition node: MDT-HS-04)
  if IO_SD_DEV=$(_mdt_io_systemd_dev "$IO_DEV_PATH" 2>&1); then
    echo "io device for static caps: $IO_DEV_PATH -> $IO_SD_DEV"
    IO_SD_STATE=ok
  else
    echo "WARN: block device $IO_DEV_PATH was found but its whole disk could not be resolved (${IO_SD_DEV}) — the static IO caps are omitted and dev.slice will have NO static IO cap; the reconcile will report ERROR on every run until IO_DEV_PATH names a resolvable node"
    IO_SD_DEV=""
    IO_SD_STATE=unresolved
  fi
else
  echo "WARN: findmnt did not expose a host /dev block-device node — static IO caps will be omitted"
fi

# IO scheduler (MDT-HS-07), PRE-FLIGHT part: read-only. The choice must be one the derived root disk lists in
# queue/scheduler (bfq is exempt: its module is loaded at apply time and the read-back verifies); the check is the
# shared Python module's, with the list read from the disk.
case "$(_mdt_io_sched_value)" in
  ""|kernel|bfq) ;;
  *)
    if ! _s_node=$(_mdt_root_disk_node 2>&1); then
      echo "ERROR: IO_ROOT_SCHEDULER is set but the whole disk under / cannot be derived (${_s_node}); nothing was changed. Remedy: run install.sh on the host, or set IO_ROOT_SCHEDULER to empty." >&2
      exit 2
    fi
    if ! _s_list=$(_mdt_io_sched_offered "${_s_node#/dev/}" 2>&1); then
      echo "ERROR: cannot read the scheduler list of ${_s_node} (${_s_list}); nothing was changed." >&2
      exit 2
    fi
    if ! python3 "$HERE/mdt-host-setup-wizard.py" --check-scheduler "$(_mdt_io_sched_value)" --available-schedulers "$(echo "$_s_list" | tr '\n' ' ')"; then
      echo "ERROR: IO_ROOT_SCHEDULER=$(_mdt_io_sched_value) is not valid for ${_s_node}; nothing was changed." >&2
      exit 2
    fi
    ;;
esac

# The static fallback in the rendered dev.slice unit comes from the SAME
# resolution the runtime reconcile uses (scripts/mdt-container-io-caps.lib.sh):
# the operator-pinned DEV_IO_{RBPS,WBPS,RIOPS,WIOPS}_MAX when set, else
# DEV_STATIC_*.  Rendered as plain byte counts, so systemd never applies its
# own K/M/G base to an MDT number.  An invalid value refuses the install.
if ! _mdt_resolve_static_io; then
  echo "ERROR: invalid IO cap value in host-setup.env (see above); no host policy changes beyond this point were applied" >&2
  exit 2
fi
DEV_STATIC_RBW="$MDT_STATIC_RBPS"; DEV_STATIC_WBW="$MDT_STATIC_WBPS"
DEV_STATIC_RIOPS="$MDT_STATIC_RIOPS"; DEV_STATIC_WIOPS="$MDT_STATIC_WIOPS"
_mdt_io_pin_notice >&2

# Writeback / root-queue settings (MDT-HS-05), PRE-FLIGHT part: read-only. The queue keys need the
# whole disk under / (derived, never typed) and max_sectors_kb must not exceed that disk's
# max_hw_sectors_kb; the grammar and dirty_background < dirty were already checked by the shared
# Python validator (--validate-config above), and the bound is checked by the SAME module.
WB_DEV=""
if _mdt_io_queue_keys_set; then
  if ! _wb_node=$(_mdt_root_disk_node 2>&1); then
    echo "ERROR: IO_ROOT_QUEUE_* is set but the whole disk under / cannot be derived (${_wb_node}); nothing was changed. Remedy: run install.sh on the host, or set IO_ROOT_QUEUE_NR_REQUESTS and IO_ROOT_QUEUE_MAX_SECTORS_KB to kernel." >&2
    exit 2
  fi
  WB_DEV=${_wb_node#/dev/}
  _wb_max_sectors=$(_mdt_io_wb_value IO_ROOT_QUEUE_MAX_SECTORS_KB)
  if [ -n "$_wb_max_sectors" ]; then
    if ! _wb_hw=$(_mdt_io_queue_attr "$WB_DEV" max_hw_sectors_kb 2>&1); then
      echo "ERROR: cannot read max_hw_sectors_kb of $WB_DEV (${_wb_hw}), so IO_ROOT_QUEUE_MAX_SECTORS_KB=$_wb_max_sectors cannot be bounded; nothing was changed." >&2
      exit 2
    fi
    if ! _wb_lbs=$(_mdt_io_queue_attr "$WB_DEV" logical_block_size 2>&1); then
      echo "ERROR: cannot read logical_block_size of $WB_DEV (${_wb_lbs}), so IO_ROOT_QUEUE_MAX_SECTORS_KB=$_wb_max_sectors cannot be checked for alignment; nothing was changed." >&2
      exit 2
    fi
    if ! python3 "$HERE/mdt-host-setup-wizard.py" --check-max-sectors-kb "$_wb_max_sectors" --max-hw-sectors-kb "$_wb_hw" --logical-block-size "$_wb_lbs"; then
      echo "ERROR: IO_ROOT_QUEUE_MAX_SECTORS_KB=$_wb_max_sectors is not valid for $WB_DEV (max_hw_sectors_kb=$_wb_hw, logical_block_size=$_wb_lbs); nothing was changed." >&2
      exit 2
    fi
  fi
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
# `auto` is the explicit config spelling for install-time derivation. `-`
# means omit the directive (systemd's unlimited/default behavior); keep a
# separate bit because both render to an empty template value but only `auto`
# may be derived below.
for _quota_var in DEV_CPU_QUOTA DEV_INTERACTIVE_CPU_QUOTA DEV_BACKGROUND_CPU_QUOTA DEV_GATES_CPU_QUOTA DEV_BUILDKITD_CPU_QUOTA; do
  _quota_value="${!_quota_var:-}"
  _unset_var="${_quota_var}_UNSET"
  case "$_quota_value" in
    auto) printf -v "$_quota_var" '%s' "" ;;
    -) printf -v "$_unset_var" '%s' 1; printf -v "$_quota_var" '%s' "" ;;
  esac
done
if [ -z "${DEV_CPU_QUOTA:-}" ] && [ "${DEV_CPU_QUOTA_UNSET:-0}" != 1 ]; then
  DEV_CPU_QUOTA=$(_auto_cpu_quota "$DEV_CPU_RESERVE_CORES")
  echo "dev.slice CPUQuota: auto-detected (host has $_nproc, reserving $DEV_CPU_RESERVE_CORES) -> $DEV_CPU_QUOTA"
fi
for _tier_var in DEV_INTERACTIVE_CPU_QUOTA DEV_BACKGROUND_CPU_QUOTA DEV_GATES_CPU_QUOTA DEV_BUILDKITD_CPU_QUOTA; do
  _unset_var="${_tier_var}_UNSET"
  if [ -z "${!_tier_var:-}" ] && [ "${!_unset_var:-0}" != 1 ]; then
    printf -v "$_tier_var" '%s' "$(_auto_cpu_quota "$DEV_SUBSLICE_CPU_RESERVE_CORES")"
    echo "$_tier_var: auto-detected (host has $_nproc, reserving $DEV_SUBSLICE_CPU_RESERVE_CORES) -> ${!_tier_var}"
  fi
done

# Swap cascade: auto-detect total host swap, then apply
# DEV_SWAP_CASCADE_PCT (default 80%) at each level — host swap -> dev.slice's
# own MemorySwapMax -> each child's own (same 80%, of the PARENT's derived
# value, not of the host total again) -> the watcher's per-container
# MemorySwapMax (mdt-container-memory-inotify-watcher.py reads DEV_SWAP_CASCADE_PCT itself and
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
for _swap_var in DEV_SWAP_MAX DEV_INTERACTIVE_MEMORY_SWAP_MAX DEV_BACKGROUND_MEMORY_SWAP_MAX DEV_GATES_MEMORY_SWAP_MAX DEV_BUILDKITD_MEMORY_SWAP_MAX; do
  _swap_value="${!_swap_var:-}"
  _unset_var="${_swap_var}_UNSET"
  case "$_swap_value" in
    auto) printf -v "$_swap_var" '%s' "" ;;
    -) printf -v "$_unset_var" '%s' 1; printf -v "$_swap_var" '%s' "" ;;
  esac
done
# The wizard keeps the operator's explicit `-` in the candidate so the
# distinction from `auto` remains visible during validation and review. Before
# rendering, turn that sentinel into an omitted systemd directive. CPU/swap
# `-` values above also leave *_UNSET markers so they are not auto-derived.
for _optional_memory_var in \
  DEV_MEMORY_MIN_GUARANTEED_CEILING DEV_MEMORY_LOW DEV_MEMORY_HIGH DEV_MEMORY_MAX \
  DEV_INTERACTIVE_MEMORY_MIN DEV_INTERACTIVE_MEMORY_LOW DEV_INTERACTIVE_MEMORY_HIGH DEV_INTERACTIVE_MEMORY_MAX \
  DEV_BACKGROUND_MEMORY_MIN DEV_BACKGROUND_MEMORY_LOW DEV_BACKGROUND_MEMORY_HIGH DEV_BACKGROUND_MEMORY_MAX \
  DEV_GATES_MEMORY_MIN DEV_GATES_MEMORY_LOW DEV_GATES_MEMORY_HIGH DEV_GATES_MEMORY_MAX \
  DEV_BUILDKITD_MEMORY_MIN DEV_BUILDKITD_MEMORY_LOW DEV_BUILDKITD_MEMORY_HIGH DEV_BUILDKITD_MEMORY_MAX \
  DEV_MEMORY_MIN_GUARANTEED_LOW DEV_MEMORY_MIN_GUARANTEED_HIGH DEV_MEMORY_MIN_GUARANTEED_MAX; do
  if [ "${!_optional_memory_var:-}" = - ]; then
    printf -v "$_optional_memory_var" '%s' ""
  fi
done
if [ -n "${DEV_SWAP_MAX:-}" ]; then
  if ! _swap_max_bytes=$(_size_to_bytes "$DEV_SWAP_MAX"); then
    echo "ERROR: DEV_SWAP_MAX is not a systemd size that can be converted to bytes: $DEV_SWAP_MAX" >&2
    exit 2
  fi
  DEV_SWAP_MAX="$_swap_max_bytes"
fi
if [ -n "${_host_swap_bytes:-}" ] && [ "$_host_swap_bytes" -gt 0 ]; then
  if [ -z "${DEV_SWAP_MAX:-}" ] && [ "${DEV_SWAP_MAX_UNSET:-0}" != 1 ]; then
    DEV_SWAP_MAX=$(_pct_of "$_host_swap_bytes" "$DEV_SWAP_CASCADE_PCT")
    echo "dev.slice MemorySwapMax: auto-detected (host swap ${_host_swap_bytes}B, ${DEV_SWAP_CASCADE_PCT}%) -> ${DEV_SWAP_MAX}B"
  fi
  for _swap_var in DEV_INTERACTIVE_MEMORY_SWAP_MAX DEV_BACKGROUND_MEMORY_SWAP_MAX DEV_GATES_MEMORY_SWAP_MAX DEV_BUILDKITD_MEMORY_SWAP_MAX; do
    _unset_var="${_swap_var}_UNSET"
    if [ -z "${!_swap_var:-}" ] && [ "${!_unset_var:-0}" != 1 ] && [ -n "${DEV_SWAP_MAX:-}" ]; then
      printf -v "$_swap_var" '%s' "$(_pct_of "$DEV_SWAP_MAX" "$DEV_SWAP_CASCADE_PCT")"
      echo "$_swap_var: auto-detected (${DEV_SWAP_CASCADE_PCT}% of dev.slice's ${DEV_SWAP_MAX}B) -> ${!_swap_var}B"
    elif [ -z "${!_swap_var:-}" ] && [ "${!_unset_var:-0}" != 1 ]; then
      echo "$_swap_var: parent swap ceiling is unset — leaving this child swap ceiling unset"
    fi
  done
elif [ "${_host_swap_bytes:-}" = 0 ]; then
  echo "WARN: no swap detected (/proc/meminfo SwapTotal=0) — MemorySwapMax auto-detect skipped; explicit host-setup.env values (if any) still apply"
else
  echo "WARN: could not read /proc/meminfo's SwapTotal — MemorySwapMax auto-detect skipped; set DEV_SWAP_MAX and the *_MEMORY_SWAP_MAX values explicitly if this host has swap"
fi

# === COMMIT POINT: nothing above this line mutates the host (MDT-HS-02) ===
# Every refusal above has been evaluated. Only now are /etc/mdt, the buildx config
# directory, the installed config and the guard confirmation record touched.
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
case "$GUARD_DECISION" in
  record) _mdt_guard_record_write "$GUARD_RECORD" terminate "${DEV_BUILDKITD_IMAGE:-}" ;;
  clear) rm -f -- "$GUARD_RECORD" ;;
esac

echo "== packages =="
# fio/pv: dependencies of the shared io.cost baseline benchmark.
# systemd-oomd: without it every ManagedOOM*
# setting in dev-background.slice is a silent no-op (separate package on Debian).
apt-get update -qq || echo "WARN: apt-get update failed — install may use a stale index"
apt-get install -y --no-install-recommends fio pv systemd-oomd \
  || echo "WARN: apt install failed — install fio + pv + systemd-oomd manually"

MDT_BASELINE="${IO_BASELINE_ENV:-/var/lib/mdt/io-baseline.env}"
MDT_TESTFILE="${IO_BASELINE_TESTFILE:-/var/lib/mdt/iocost-coef-fio.testfile}"

# The service/script names are intentionally a clean vocabulary, not aliases
# layered over the old names. Stop and remove the old installed components
# before daemon-reload: an old enabled events watcher can otherwise continue
# applying a stale per-container policy while the new setup is being installed.
for _old_unit in \
  mdt-host-slices.timer mdt-host-slices.service \
  mdt-io-cap-watcher.service mdt-dev-cap-watcher.service; do
  if systemctl is-active --quiet "$_old_unit" || systemctl is-enabled --quiet "$_old_unit"; then
    echo "retiring obsolete MDT unit: $_old_unit"
    systemctl disable --now "$_old_unit" 2>/dev/null || true
  fi
  rm -f "/etc/systemd/system/$_old_unit"
done
rm -f \
  /usr/local/sbin/mdt-apply-dev-caps.sh \
  /usr/local/sbin/mdt-io-cap-watcher.sh \
  /usr/local/sbin/mdt-dev-cap-watcher.py \
  /usr/local/sbin/mdt-container-caps.lib.sh \
  /usr/local/sbin/mdt-slice-audit.py

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
DEV_BUILDKITD_CPU_WEIGHT DEV_BUILDKITD_CPU_QUOTA DEV_BUILDKITD_IO_WEIGHT DEV_BUILDKITD_IMAGE DEV_BUILDKITD_CACHE \
DEV_BUILDKITD_MAX_PARALLELISM \
DEV_BUILDKITD_ZSWAP_WRITEBACK \
DEV_STATIC_RBW DEV_STATIC_WBW DEV_STATIC_RIOPS DEV_STATIC_WIOPS \
DOCKER_SCOPE_BACKSTOP_MEMORY_MAX DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX \
IO_DEV_PATH IO_SD_DEV WATCHER_INTERVAL"
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
_BK_DIGEST_BEFORE=$(_mdt_files_digest "${MDT_BUILDKITD_FILES[@]}")
render "$HERE/units/dev-gates.slice.in"        /etc/systemd/system/dev-gates.slice
render "$HERE/units/dev-buildkitd.slice.in"    /etc/systemd/system/dev-buildkitd.slice
render "$HERE/units/mdt-buildkitd.service.in"  /etc/systemd/system/mdt-buildkitd.service
render "$HERE/buildkitd.toml.in"              /etc/mdt/buildkitd.toml
[ "$(_mdt_files_digest "${MDT_BUILDKITD_FILES[@]}")" = "$_BK_DIGEST_BEFORE" ] && BUILDKITD_CHANGED=0 || BUILDKITD_CHANGED=1
install -m 0755 "$HERE/scripts/mdt-buildkitd-stamp.sh" /usr/local/sbin/mdt-buildkitd-stamp.sh
render "$HERE/units/mdt-dev-governance-reconcile.timer.in"  /etc/systemd/system/mdt-dev-governance-reconcile.timer
install -m 0644 "$HERE/units/mdt-dev-governance-reconcile.service" /etc/systemd/system/mdt-dev-governance-reconcile.service
if [ "${INOTIFY_OK:-}" = 1 ]; then
  install -m 0644 "$HERE/units/mdt-container-memory-inotify-watcher.service" /etc/systemd/system/mdt-container-memory-inotify-watcher.service
fi
# Not gated by INOTIFY_OK: this one reacts via `docker events`, not inotify —
# see scripts/mdt-container-io-events-watcher.sh for why it can't use the same mechanism
# as mdt-container-memory-inotify-watcher.py above.
install -m 0644 "$HERE/units/mdt-container-io-events-watcher.service" /etc/systemd/system/mdt-container-io-events-watcher.service
install -m 0644 "$HERE/units/mdt-buildkit-guard.service" /etc/systemd/system/mdt-buildkit-guard.service

mkdir -p /etc/systemd/system/docker-.scope.d
render "$HERE/units/docker-scope-default-limits.conf.in" \
  /etc/systemd/system/docker-.scope.d/50-default-limits.conf

if [ -n "${IO_SD_DEV:-}" ]; then
  echo "static IO fallback: dev.slice on ${IO_SD_DEV} (${IO_DEV_PATH}) = ${DEV_STATIC_RIOPS}r/${DEV_STATIC_WIOPS}w IOPS, ${DEV_STATIC_RBW}/${DEV_STATIC_WBW} bytes/s read/write (DEV_IO_*_MAX when set, else DEV_STATIC_*)"
  echo "  applies from boot, and whenever no current plausible identity-verified baseline is available; the reconciliation service re-asserts it explicitly, applies min(pinned, ${DEV_IO_CAP_PCT:-60}% of baseline) per value when a baseline exists, and verifies the kernel io.max (check with: mdt-cgroup-io-limits.sh dev.slice --systemd)"
else
  if [ "$IO_SD_STATE" = unresolved ]; then
    _io_why="the block device ${IO_DEV_PATH} was found but its whole disk could not be resolved (dev.slice has NO static IO cap; the reconcile reports ERROR on every run until IO_DEV_PATH names a resolvable node)"
  else
    _io_why="no host block device was discovered"
  fi
  echo "static IO fallback: NOT ACTIVE — ${_io_why}; the four configured values (${DEV_STATIC_RIOPS}r/${DEV_STATIC_WIOPS}w IOPS, ${DEV_STATIC_RBW}/${DEV_STATIC_WBW} bandwidth) are retained for the next host-side device discovery"
fi

# Directory-mountable Docker API socket (/run/docker-api/docker.sock),
# alongside the default /run/docker.sock — see units/docker-api-socket.conf
# for why. No template variables (a fixed, well-known path, like
# /run/mdt-buildkitd above), so installed directly rather than rendered.
mkdir -p /etc/systemd/system/docker.socket.d
install -m 0644 "$HERE/units/docker-api-socket.conf" \
  /etc/systemd/system/docker.socket.d/50-mdt-dedicated-api-socket.conf

# /run/cgprofile tmpfiles.d entry (RG-55 A2/D-30, M5): the directory
# templates/devcontainer.json bind-mounts to reach the cgroup-profiler
# daemon's control socket. The observer directory has a separate rule so its
# dedicated provisioning mode need not rewrite this control directory. No
# template variables, installed directly like docker-api-socket.conf above —
# see units/mdt-cgprofile.conf for the full reasoning. Named with the mdt-
# prefix (round-2 review S15(r2)),
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
# after a reboot. The dedicated read-only observer group and directory are
# provisioned and verified by the same helper used by --observer-only.
install -m 0644 "$HERE/units/mdt-cgprofile.conf" /etc/tmpfiles.d/mdt-cgprofile.conf
systemd-tmpfiles --create /etc/tmpfiles.d/mdt-cgprofile.conf \
  || { echo "ERROR: systemd-tmpfiles --create failed for /etc/tmpfiles.d/mdt-cgprofile.conf" >&2; exit 1; }
provision_cgprofile_observer /

# DAMON_STAT is a host-wide consumer that excludes cgprofile's generic DAMON
# sysfs sessions. Disabled by default (operator decision 2026-10-20); `no` removes the override.
export DAMON_STAT_TMPFILES=/etc/tmpfiles.d/mdt-damon-stat.conf
_mdt_apply_damon_step "$REQUIRE_DAMON" || exit 2

# Post-render fixups:
# - no device node discovered → IO*Max lines would be invalid; drop them.
#   (dev.slice now carries the one IO ceiling for both tiers — see units/dev.slice.in)
if [ -z "${IO_SD_DEV:-}" ]; then
  sed -i '/^IO\(Read\|Write\)\(Bandwidth\|IOPS\)Max=/d' /etc/systemd/system/dev.slice
  if [ "$IO_SD_STATE" = unresolved ]; then
    echo "device $IO_DEV_PATH found but its whole disk could not be resolved — dropped static IO*Max lines; dev.slice has NO static IO cap"
  else
    echo "no device discovered — dropped static IO*Max lines (runtime caps may still apply if discovery succeeds later)"
  fi
fi
# - MemoryZSwapWriteback= needs systemd >= 256; older hosts get the raw-write
#   fallback from mdt-dev-governance-reconcile.sh instead. Applies to dev.slice AND
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
# mdt-container-io-caps.lib.sh: sourced by BOTH scripts below via
# "$(dirname "$0")/mdt-container-io-caps.lib.sh" — must land in the same
# directory as them, not just be present in the repo checkout.
install -m 0644 "$HERE/scripts/mdt-container-io-caps.lib.sh" /usr/local/sbin/mdt-container-io-caps.lib.sh
install -m 0755 "$HERE/scripts/mdt-dev-governance-reconcile.sh"  /usr/local/sbin/mdt-dev-governance-reconcile.sh
# Read-only IO limit listing (reads cgroup CONTENT; cgroupfs files report size 0).
install -m 0755 "$HERE/scripts/mdt-cgroup-io-limits.sh"  /usr/local/sbin/mdt-cgroup-io-limits.sh
install -m 0755 "$HERE/scripts/mdt-container-io-events-watcher.sh"  /usr/local/sbin/mdt-container-io-events-watcher.sh
# These Python entrypoints import the shared parser for consistent --version,
# help, and error output. Keep it beside them so /usr/local/sbin is on
# sys.path when systemd or an operator executes the installed script.
install -m 0644 "$HERE/scripts/mdt_cli.py"             /usr/local/sbin/mdt_cli.py
install -m 0755 "$HERE/scripts/mdt-io-baseline.py"     /usr/local/sbin/mdt-io-baseline.py
install -d -m 0755 /usr/local/lib/mdt
install -m 0755 "$HERE/../../scripts/debian-install-v2/tools/iocost_coef_gen.py" \
  /usr/local/lib/mdt/iocost_coef_gen.py
# the ONE plausibility definition shared with debian-install-v2's iocost-calibrate.sh
install -m 0644 "$HERE/../../scripts/debian-install-v2/tools/iocost_plausibility.py" \
  /usr/local/lib/mdt/iocost_plausibility.py
install -m 0755 "$HERE/scripts/mdt-slice-memory-min-low-audit.py"     /usr/local/sbin/mdt-slice-memory-min-low-audit.py
install -m 0755 "$HERE/scripts/mdt-buildkit-guard.py"  /usr/local/sbin/mdt-buildkit-guard.py
install -m 0755 "$HERE/../scripts/mdt_buildkit_builder.py" /usr/local/sbin/mdt-buildkit-builder.py
install -m 0755 "$HERE/scripts/check.sh"               /usr/local/sbin/mdt-host-check.sh
install -m 0755 "$HERE/scripts/docker-safe-restart.sh" /usr/local/sbin/mdt-docker-safe-restart
install -D -m 0644 "$HERE/etc/profile.d/mdt-buildkit.sh" /etc/profile.d/mdt-buildkit.sh
# Every way a build can start: the shim makes mdt-managed the default builder for ANY process
# that runs the docker CLI (no profile script involved), /etc/environment covers PAM sessions
# (ssh incl. VS Code's SSH server, sudo, su, login, cron) and the profile script covers the
# remaining bash/sh login shells. docs/BUILD-ARCHITECTURE.md "Making every build use mdt-managed".
install -D -m 0755 "$HERE/scripts/docker-buildx-shim.sh" "$BUILDX_SHIM_TARGET"
_mdt_install_environment_block "$HERE/etc/environment.mdt-buildkit" /etc/environment
if [ "$INOTIFY_OK" = 1 ]; then
  install -m 0755 "$HERE/scripts/mdt-container-memory-inotify-watcher.py" /usr/local/sbin/mdt-container-memory-inotify-watcher.py
fi

SCHEDULER_FAILED=0
if [ -z "$(_mdt_io_sched_value)" ]; then
  # IO_ROOT_SCHEDULER absent or empty = NOT MANAGED: an old env file keeps the legacy BFQ rule (changing the
  # scheduler for a host that never chose is a hazard); the wizard asks for the key (MDT-HS-07).
  echo "== BFQ scheduler (legacy: IO_ROOT_SCHEDULER is not set) =="
  echo "NOTICE: IO_ROOT_SCHEDULER is not set, so the LEGACY BFQ rule is kept. io.cost (IO_COST_MODEL/IO_COST_QOS) replaces BFQ as the weight mechanism and mq-deadline/none need no module: run install.sh --wizard to choose."
  if _l_node=$(_mdt_root_disk_node 2>/dev/null) && _l_live=$(_mdt_io_sched_current "${_l_node#/dev/}" 2>/dev/null) && [ -n "$_l_live" ] && [ "$_l_live" != bfq ]; then
    echo "NOTICE: the live scheduler of ${_l_node#/dev/} is '$_l_live' but the legacy rule re-applies bfq on every udev change event and at boot; set IO_ROOT_SCHEDULER (install.sh --wizard) to keep '$_l_live', or accept bfq."
  fi
  install -m 0644 "$HERE/etc/modules-load.d/bfq.conf"            /etc/modules-load.d/mdt-bfq.conf
  install -m 0644 "$HERE/etc/udev/rules.d/60-bfq-scheduler.rules" /etc/udev/rules.d/60-mdt-bfq-scheduler.rules
  modprobe bfq 2>/dev/null || echo "WARN: modprobe bfq failed"
  udevadm control --reload-rules 2>/dev/null \
    && udevadm trigger --action=change --subsystem-match=block 2>/dev/null \
    || echo "WARN: udev reload/trigger failed — set the scheduler manually"
else
  echo "== IO scheduler (MDT-HS-07) =="
  _mdt_apply_scheduler_settings || SCHEDULER_FAILED=1
fi

echo "== writeback and root-queue settings (MDT-HS-05) =="
# After the BFQ block on purpose: a scheduler change resets nr_requests, so the queue rule
# (61-mdt-io-queue.rules) is applied once the scheduler is settled. A failed read-back does not
# abort the half-finished install; it is reported and the install exits non-zero at the end.
WRITEBACK_FAILED=0
_mdt_apply_writeback_settings || WRITEBACK_FAILED=1
# The queue rule (61) runs after the scheduler rule (60), and a scheduler change resets nr_requests: verify the
# scheduler once more after the queue settings were applied, so the final state is what is reported.
if [ "$SCHEDULER_FAILED" = 0 ] && [ -n "$(_mdt_io_sched_value)" ] && _f_node=$(_mdt_root_disk_node 2>/dev/null); then
  _mdt_verify_scheduler "${_f_node#/dev/}" || SCHEDULER_FAILED=1
fi

echo "== io.cost model and QoS (MDT-HS-07) =="
install -m 0755 "$HERE/scripts/mdt-io-cost-apply.sh" /usr/local/sbin/mdt-io-cost-apply.sh
install -m 0755 "$HERE/scripts/mdt-io-reassert-pause.sh" /usr/local/sbin/mdt-io-reassert-pause
install -m 0644 "$HERE/units/mdt-io-cost.service" /etc/systemd/system/mdt-io-cost.service
IOCOST_FAILED=0
_mdt_apply_iocost || IOCOST_FAILED=1

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
systemctl enable mdt-dev-governance-reconcile.service          # boot-time apply
systemctl enable --now mdt-dev-governance-reconcile.timer      # periodic sweep
BUILDKITD_RESTART_PENDING=0
_bk_active=0; systemctl is-active --quiet mdt-buildkitd.service && _bk_active=1
_bk_pending=0; _bk_why=""
if [ "$_bk_active" = 1 ] && _bk_why=$(_mdt_buildkitd_pending); then _bk_pending=1; fi
case "$(_mdt_buildkitd_restart_decision "$BUILDKITD_CHANGED" "$RESTART_BUILDKITD" "$_bk_active" "$_bk_pending")" in
  start)
    systemctl enable --now mdt-buildkitd.service ;;
  unchanged)
    echo "mdt-buildkitd.service: unit and config unchanged and the running service matches them, not restarted (a running build is not interrupted)" ;;
  restart)
    echo "restarting mdt-buildkitd.service (${_bk_why:-its unit or config changed}); active builds will be interrupted"
    systemctl restart mdt-buildkitd.service ;;
  pending)
    BUILDKITD_RESTART_PENDING=1
    echo "WARN: ********************************************************************" >&2
    echo "WARN: mdt-buildkitd.service RESTART PENDING (${_bk_why:-its unit or config changed}) but a build is (or may be) active, so it was NOT restarted;" >&2
    echo "WARN: it keeps running its OLD configuration until you restart it. Re-run install.sh --restart-buildkitd (or" >&2
    echo "WARN: systemctl restart mdt-buildkitd.service) when no build is running. This install exits non-zero so it is not missed." >&2
    echo "WARN: ********************************************************************" >&2
    systemctl enable mdt-buildkitd.service ;;
esac
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
# /etc/mdt/buildx is the install-time proof that the service is reachable through a
# Buildx remote node, and the root-owned reference registration. It is NOT what ordinary
# users build with: Buildx writes build refs and a last-activity stamp into its state
# directory on every build, so a root-owned directory fails for them with "permission
# denied" (measured, docs/BUILD-ARCHITECTURE.md). Each user gets the same remote node in
# their own ~/.docker/buildx, registered by the docker-buildx shim on first use.
find "$BUILDX_CONFIG_DIR" -xdev -type d -exec chown root:root {} + -exec chmod 0755 {} +
find "$BUILDX_CONFIG_DIR" -xdev -type f -exec chown root:root {} + -exec chmod 0644 {} +
systemctl enable --now mdt-buildkit-guard.service
systemctl restart mdt-buildkit-guard.service
# End-to-end proof for a brand-new user with an EMPTY environment (env -i: no BUILDX_*, no DOCKER_*,
# no profile): the shim must register mdt-managed in that user's own state and Buildx must
# reach the service through it. This is the path VS Code's SSH server, cron and systemd units take.
echo "== managed builder as a fresh user with an empty environment =="
_probe_home="$(mktemp -d "${TMPDIR:-/tmp}/mdt-probe-home.XXXXXXXX")"
_probe_out="$(env -i PATH="$PATH" HOME="$_probe_home" "$DOCKER_BIN" buildx inspect --bootstrap 2>&1 || true)"
rm -rf -- "$_probe_home"
if ! printf '%s\n' "$_probe_out" | grep -Eq '^Name:[[:space:]]+mdt-managed$' \
  || ! printf '%s\n' "$_probe_out" | grep -Eq '^Driver:[[:space:]]+remote$'; then
  echo "ERROR: a fresh user with an empty environment does not get mdt-managed (remote driver) from 'docker buildx inspect'; the docker-buildx shim at $BUILDX_SHIM_TARGET is not taking effect. Check: 'docker info | grep -A3 buildx' must show Path: $BUILDX_SHIM_TARGET (a ~/.docker/cli-plugins/docker-buildx of the installing user would shadow it); 'systemctl status mdt-buildkitd.service' must be active; /run/mdt-buildkitd/buildkitd.sock must exist. Output of the probe:" >&2
  printf '%s\n' "$_probe_out" | head -20 >&2
  exit 2
fi
echo "ok: fresh user resolves mdt-managed (remote) without any environment"
if [ "$MEMORY_WATCHER_CONFIGURED" = 1 ]; then
  systemctl enable --now mdt-container-memory-inotify-watcher.service
else
  # An older install may have enabled the watcher when shipped leaf caps were
  # defaults. Stop it when the current validated config explicitly disables
  # every pair, so no stale process keeps applying its old in-memory policy.
  if systemctl is-active --quiet mdt-container-memory-inotify-watcher.service \
    || systemctl is-enabled --quiet mdt-container-memory-inotify-watcher.service; then
    systemctl disable --now mdt-container-memory-inotify-watcher.service \
      || { echo "ERROR: could not stop the per-container memory watcher after its policy was disabled" >&2; exit 2; }
  fi
  echo "per-container memory watcher disabled by WATCHER_MEMORY_POLICY=disabled"
fi
systemctl enable mdt-container-io-events-watcher.service         # reactive per-container IO caps (docker events)

BASELINE_HANDLED=0
if [ "$WIZARD" = 1 ] && [ "$WITH_BASELINE" = 1 ] && [ -f "$BASELINE_STATUS_PATH" ]; then
  _baseline_status="$(<"$BASELINE_STATUS_PATH")"
  case "$_baseline_status" in
    current|running|completed|declined|failed)
      BASELINE_HANDLED=1
      echo "== io.cost benchmark =="
      echo "wizard already handled the single benchmark lifecycle ($_baseline_status); no second run will be started"
      ;;
    deferred) echo "== io.cost benchmark deferred by wizard; prerequisites are now installed, running once during install ==" ;;
    *) echo "WARN: unrecognized wizard benchmark status; install will run the requested benchmark once" ;;
  esac
fi
if [ "$WITH_BASELINE" = 1 ] && [ "$BASELINE_HANDLED" = 0 ]; then
  echo "== io.cost baseline (six fio runs — disk will be saturated for ~12 min) =="
  IO_BASELINE_ENV="$MDT_BASELINE" IO_BASELINE_TESTFILE="$MDT_TESTFILE" \
    IO_DEV_PATH="${IO_DEV_PATH:-}" \
    /usr/local/sbin/mdt-io-baseline.py --force --output "$MDT_BASELINE" \
    --testfile "$MDT_TESTFILE" || echo "WARN: baseline failed — statics remain in force"
fi

systemctl start mdt-dev-governance-reconcile.service           # apply runtime caps now
# Both reactive watchers load their configuration and baseline once per
# process. Restart after the optional baseline measurement so a re-run of this
# installer cannot leave an already-running watcher applying stale values.
if [ "$MEMORY_WATCHER_CONFIGURED" = 1 ]; then
  systemctl restart mdt-container-memory-inotify-watcher.service
fi
systemctl restart mdt-container-io-events-watcher.service
if [ -n "${IO_COST_MODEL:-}${IO_COST_QOS:-}" ]; then
  systemctl enable mdt-io-cost.service >/dev/null 2>&1 || echo "WARN: could not enable mdt-io-cost.service; io.cost will not be re-applied at boot" >&2
fi

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
if [ "${BUILDKITD_RESTART_PENDING:-0}" = 1 ]; then
  echo "ERROR: mdt-buildkitd.service was not restarted although its unit/config changed (a build may be active); the rest of the install completed. Re-run install.sh --restart-buildkitd when no build is running." >&2
  exit 2
fi
if [ "$SCHEDULER_FAILED" = 1 ] || [ "$IOCOST_FAILED" = 1 ]; then
  echo "ERROR: the IO scheduler / io.cost settings were NOT verified live (see the ERROR lines above); the rest of the install completed. Fix the named cause and re-run install.sh." >&2
  exit 2
fi
if [ "$WRITEBACK_FAILED" = 1 ]; then
  echo "ERROR: the writeback / root-queue settings were NOT verified live (see the ERROR lines above); the rest of the install completed. Fix the named cause and re-run install.sh." >&2
  exit 2
fi
