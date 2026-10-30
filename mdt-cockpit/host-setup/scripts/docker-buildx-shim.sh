#!/bin/sh
# MDT host-setup — Docker CLI plugin shim that makes the host-managed BuildKit
# worker (mdt-managed) the default builder for every Buildx build on this host.
#
# Installed by install.sh as /usr/local/lib/docker/cli-plugins/docker-buildx.
# The Docker CLI searches its plugin directories in a fixed order and
# /usr/local/lib is searched before /usr/libexec, where the packaged plugin lives, so
# every `docker build`, `docker buildx ...` and `docker compose build` that
# reaches Buildx passes through this file first, whoever runs it and from
# whatever environment (login shell, VS Code's SSH server, cron, a systemd
# unit): no profile script is involved. docs/BUILD-ARCHITECTURE.md
# "Making every build use mdt-managed" has the reasoning.
#
# What it does, in order:
#   1. Metadata handshake and MDT_BUILDX_PASSTHROUGH=1: exec the real plugin untouched.
#   2. Not the local daemon (DOCKER_HOST is set and is not unix://): untouched. A build for
#      another engine must not be redirected to this host's worker.
#   3. A stale BUILDX_CONFIG=/etc/mdt/buildx the caller cannot write is dropped (see the comment in the code).
#   4. BUILDX_BUILDER unset or `default`: select mdt-managed. The Docker CLI sets
#      BUILDX_BUILDER=default itself for plain `docker build` (measured on Docker 29.9.1),
#      so `default` cannot be told apart from "the caller chose nothing"; an explicit
#      `--builder default` flag still wins over the environment.
#   5. mdt-managed not registered for THIS user yet: register it, once. The create runs the plugin
#      binary standalone, so BUILDX_BUILDER (`create --name X` treats an exported X as an existing
#      instance) and the DOCKER_CLI_PLUGIN_* variables the CLI hands a plugin (they make the binary
#      expect the `buildx` plugin argv form) are removed from its environment. The registration
#      is a plain Buildx `remote` node that points at the service socket. It never creates
#      a docker-container builder, which mdt-buildkit-guard would remove.
# Anything else (a builder the caller named, e.g. --builder or BUILDX_BUILDER=other)
# is passed through. A failure to register is reported on stderr and NOT papered over:
# Buildx then fails with "no builder mdt-managed found" instead of building on Docker's
# built-in builder, outside the governed slice.
#
# The real plugin is looked up in the directories after this one. They are listed in
# one line so a test can point them at a fake (host-setup/tests/test_docker_buildx_shim.py).
set -u

MDT_BUILDER=mdt-managed
MDT_ENDPOINT=unix:///run/mdt-buildkitd/buildkitd.sock
MDT_SOCKET=/run/mdt-buildkitd/buildkitd.sock
MDT_SHARED_CONFIG=/etc/mdt/buildx
REAL_DIRS="/usr/local/libexec/docker/cli-plugins /usr/lib/docker/cli-plugins /usr/libexec/docker/cli-plugins"

real=
for dir in $REAL_DIRS; do
  if [ -x "$dir/docker-buildx" ]; then
    real="$dir/docker-buildx"
    break
  fi
done
if [ -z "$real" ]; then
  echo "mdt docker-buildx shim: cannot run 'docker buildx': the packaged Buildx plugin is not installed in any of: $REAL_DIRS. Install the docker-buildx-plugin package, or remove this shim (/usr/local/lib/docker/cli-plugins/docker-buildx) so Docker uses its own plugin again." >&2
  exit 1
fi

if [ "${1:-}" = "docker-cli-plugin-metadata" ] || [ "${MDT_BUILDX_PASSTHROUGH:-}" = "1" ]; then
  exec "$real" "$@"
fi

case "${DOCKER_HOST:-}" in
  ""|unix://*) ;;
  *) exec "$real" "$@" ;;
esac

# The retired shared registration: older profile scripts exported BUILDX_CONFIG=/etc/mdt/buildx, and
# a shell or script started before the upgrade still carries it. That directory is root-owned, and
# Buildx writes build refs and a last-activity stamp into its state directory on every build, so
# for anyone but root it fails with "permission denied". Step around it for a user who cannot
# write there; the per-user registration below takes over.
if [ "${BUILDX_CONFIG:-}" = "$MDT_SHARED_CONFIG" ] && [ ! -w "$MDT_SHARED_CONFIG/activity" ]; then
  unset BUILDX_CONFIG
fi

case "${BUILDX_BUILDER:-}" in
  ""|default)
    BUILDX_BUILDER=$MDT_BUILDER
    export BUILDX_BUILDER
    ;;
esac

if [ "$BUILDX_BUILDER" = "$MDT_BUILDER" ]; then
  # Where this user's Buildx keeps its state: BUILDX_CONFIG, else <DOCKER_CONFIG or ~/.docker>/buildx.
  if [ -n "${BUILDX_CONFIG:-}" ]; then
    config_dir=$BUILDX_CONFIG
  elif [ -n "${DOCKER_CONFIG:-}" ]; then
    config_dir=$DOCKER_CONFIG/buildx
  elif [ -n "${HOME:-}" ]; then
    config_dir=$HOME/.docker/buildx
  else
    # The Docker CLI itself falls back to the account's passwd entry when HOME is unset (a systemd
    # unit run as a user, cron), so register where it will look.
    passwd_home=$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)
    if [ -n "$passwd_home" ]; then config_dir=$passwd_home/.docker/buildx; else config_dir=; fi
  fi
  # Every message below is followed by Buildx failing with 'no builder mdt-managed found'; the build is
  # never moved to another builder, so each says what to change.
  if [ -z "$config_dir" ]; then
    echo "mdt docker-buildx shim: cannot tell where to register $MDT_BUILDER: BUILDX_CONFIG, DOCKER_CONFIG and HOME are all unset and the account has no passwd home directory (a service started without a home). Set one of them (for a systemd unit: Environment=HOME=/path), or choose a builder explicitly with --builder NAME." >&2
  elif [ ! -e "$config_dir/instances/$MDT_BUILDER" ]; then
    if [ ! -S "$MDT_SOCKET" ]; then
      echo "mdt docker-buildx shim: $MDT_SOCKET is missing, so $MDT_BUILDER cannot be registered in $config_dir and this build will fail with 'no builder $MDT_BUILDER found' (it does NOT fall back to Docker's built-in builder). Start the BuildKit service: systemctl start mdt-buildkitd.service (diagnose: systemctl status mdt-buildkitd.service; install: host-setup/install.sh)." >&2
    else
      register_out=$(env -u BUILDX_BUILDER -u DOCKER_CLI_PLUGIN_ORIGINAL_CLI_COMMAND -u DOCKER_CLI_PLUGIN_SOCKET \
        "$real" create --name "$MDT_BUILDER" --driver remote \
        --driver-opt default-load=true "$MDT_ENDPOINT" 2>&1)
      # (a concurrent first use may have registered it between the test and the create)
      if [ $? -ne 0 ] && [ ! -e "$config_dir/instances/$MDT_BUILDER" ]; then
        echo "mdt docker-buildx shim: could not register $MDT_BUILDER for user $(id -un 2>/dev/null || echo '?') in $config_dir (BUILDX_CONFIG, else DOCKER_CONFIG/buildx, else ~/.docker/buildx decide this path); Buildx said: ${register_out:-<no output>}. Check that the directory is writable, then retry; this build will fail with 'no builder $MDT_BUILDER found'. To build elsewhere on purpose use --builder NAME or MDT_BUILDX_PASSTHROUGH=1." >&2
      fi
    fi
  fi
fi

exec "$real" "$@"
