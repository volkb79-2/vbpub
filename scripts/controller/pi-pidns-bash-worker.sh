#!/bin/bash
# pi invokes this as `<shellPath> -c <command>`. Keep setup outside the new
# namespace small and fail closed: the command is only handed to bash after
# bubblewrap reports that its child was created inside the namespace.
set -u

BWRAP=/usr/bin/bwrap

fail_closed() {
  printf 'pi-pidns-bash: %s; command was not run (exit 125)\n' "$1" >&2
  exit 125
}

[[ -x $BWRAP ]] || fail_closed "$BWRAP is missing or not executable"
[[ ${UID-} =~ ^[0-9]+$ ]] || fail_closed 'cannot determine the numeric uid for the tmux socket directory'

tmux_tmpdir=${TMUX_TMPDIR:-/tmp}
[[ $tmux_tmpdir == /* ]] || fail_closed 'TMUX_TMPDIR must be an absolute path'
tmux_socket_dir=$(/usr/bin/realpath -m -- "$tmux_tmpdir/tmux-$UID") \
  || fail_closed 'cannot resolve the tmux socket directory'
[[ $tmux_socket_dir == /* ]] || fail_closed 'the tmux socket directory did not resolve to an absolute path'
/usr/bin/mkdir -p -- "$tmux_socket_dir" \
  || fail_closed 'cannot create the tmux socket directory'
/usr/bin/chmod 0700 -- "$tmux_socket_dir" \
  || fail_closed 'cannot set the tmux socket directory mode to 0700'
[[ -d $tmux_socket_dir ]] || fail_closed 'the tmux socket path is not a directory'

# The environment variables are read before they are removed. A DOCKER_HOST
# Unix socket is covered below when it already exists outside /run/docker-api.
docker_host=${DOCKER_HOST-}
unset TMUX TMUX_PANE DOCKER_HOST

declare -a bwrap_args=(
  --unshare-user
  --unshare-pid
  --die-with-parent
  --dev-bind / /
  --perms 0700 --tmpfs "$tmux_socket_dir"
  --perms 0700 --tmpfs /run/docker-api
)
declare -a docker_sockets=()

cover_docker_socket() {
  local candidate=$1 resolved existing

  # Only an existing filesystem socket needs covering. A dangling or
  # unexpected path is ambiguous, so refuse rather than let the caller believe
  # a Docker endpoint was hidden.
  if [[ ! -e $candidate && ! -L $candidate ]]; then
    return 0
  fi
  [[ -S $candidate ]] || fail_closed "Docker socket path exists but is not a socket: $candidate"
  resolved=$(/usr/bin/realpath -e -- "$candidate") \
    || fail_closed "cannot resolve Docker socket path: $candidate"
  case $resolved in
    /run/docker-api | /run/docker-api/*) return 0 ;;
  esac
  for existing in "${docker_sockets[@]}"; do
    [[ $existing != "$resolved" ]] || return 0
  done
  docker_sockets+=("$resolved")
}

cover_docker_socket /var/run/docker.sock
if [[ $docker_host == unix://* ]]; then
  docker_socket=${docker_host#unix://}
  [[ $docker_socket == /* ]] || fail_closed 'DOCKER_HOST unix URL must name an absolute socket path'
  cover_docker_socket "$docker_socket"
fi
for docker_socket in "${docker_sockets[@]}"; do
  bwrap_args+=(--bind /dev/null "$docker_socket")
done

umask 077
scratch_dir=$(/usr/bin/mktemp -d /tmp/pi-pidns-bash.XXXXXXXX) \
  || fail_closed 'cannot create private namespace status storage'
# shellcheck disable=SC2329 # Bash invokes this function indirectly via the EXIT trap below.
cleanup() {
  /usr/bin/rm -rf -- "$scratch_dir"
}
trap cleanup EXIT
status_file=$scratch_dir/status.jsonl
: > "$status_file" || fail_closed 'cannot create namespace status file'
exec 3>"$status_file" || fail_closed 'cannot open namespace status file'

# --json-status-fd distinguishes a bwrap setup error from the command's own
# exit code. No child-pid record means no command was started. This option also
# keeps command stdout/stderr streaming through the normal pi shell backend.
# Bash startup options are interpreter state; keep them out of bwrap's
# environment even if this worker was started by a Bash build that re-exported
# them.
if /usr/bin/env -u SHELLOPTS -u BASHOPTS "$BWRAP" "${bwrap_args[@]}" --json-status-fd 3 -- \
  /bin/bash -c 'exec 3>&-; exec /bin/bash "$@"' pi-pidns-shell "$@"; then
  command_rc=0
else
  command_rc=$?
fi
exec 3>&-

if [[ $(<"$status_file") != *'"child-pid"'* ]]; then
  fail_closed 'bubblewrap could not create the isolated shell'
fi
exit "$command_rc"
