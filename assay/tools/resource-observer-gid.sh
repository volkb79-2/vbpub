#!/usr/bin/env bash
# Shared host-identity guard for Assay's observer-enabled gate launchers.
# Call only after the launcher has selected its approved observer socket path.

assay_resource_observer_gid() {
  local observer_source="$1"
  local parent parent_uid parent_gid parent_mode
  local socket_uid socket_gid socket_mode host_docker_gid

  parent="$(dirname "$observer_source")"
  [[ -d "$parent" && ! -L "$parent" ]] || {
    printf 'cgprofile observer directory is absent, not a directory, or a symlink: %s; complete the host socket cutover\n' "$parent" >&2
    return 1
  }
  read -r parent_uid parent_gid parent_mode < <(stat -Lc '%u %g %a' "$parent") || {
    printf 'could not inspect the cgprofile observer directory: %s\n' "$parent" >&2
    return 1
  }
  [[ "$parent_uid" == 0 && "$parent_gid" == 0 ]] || {
    printf 'cgprofile observer directory is not root:root owned: %s; complete the host socket cutover\n' "$parent" >&2
    return 1
  }
  (( (8#$parent_mode & 0022) == 0 && (8#$parent_mode & 0010) != 0 )) || {
    printf 'cgprofile observer directory must be group-searchable and not writable by group or other users; complete the host socket cutover\n' >&2
    return 1
  }
  [[ -S "$observer_source" && ! -L "$observer_source" ]] || {
    printf 'cgprofile observer endpoint is not a Unix socket: %s; complete the host socket cutover\n' "$observer_source" >&2
    return 1
  }
  read -r socket_uid socket_gid socket_mode < <(stat -Lc '%u %g %a' "$observer_source") || {
    printf 'could not inspect the cgprofile observer socket: %s\n' "$observer_source" >&2
    return 1
  }
  [[ "$socket_uid" == 0 ]] || {
    printf 'cgprofile observer socket is not root-owned\n' >&2
    return 1
  }
  [[ "$socket_gid" =~ ^[1-9][0-9]*$ ]] || {
    printf 'cgprofile observer socket must use a non-root group\n' >&2
    return 1
  }

  host_docker_gid="$(assay_host_docker_gid)" || return 1
  [[ "$socket_gid" != "$host_docker_gid" ]] || {
    printf 'cgprofile observer socket must use its dedicated group, not the host docker group\n' >&2
    return 1
  }
  (( (8#$socket_mode & 0007) == 0 && (8#$socket_mode & 0020) != 0 )) || {
    printf 'cgprofile observer socket must be group-connectable and not accessible to other users\n' >&2
    return 1
  }
  printf '%s\n' "$socket_gid"
}

# The host Docker GID: (1) the explicit ASSAY_GATE_HOST_DOCKER_GID override
# forwarded by the outer launcher, else (2) the owning GID of the Docker API
# socket this launcher talks to (DOCKER_HOST when unix://, else
# /var/run/docker.sock), else refuse naming both sources. The group database is
# never consulted: the launcher can run in a container whose database does not
# describe the host.
assay_host_docker_gid() {
  local docker_socket docker_socket_gid
  if [[ -v ASSAY_GATE_HOST_DOCKER_GID ]]; then
    [[ "$ASSAY_GATE_HOST_DOCKER_GID" =~ ^[1-9][0-9]*$ ]] || {
      printf 'ASSAY_GATE_HOST_DOCKER_GID must be a verified non-root numeric GID\n' >&2
      return 1
    }
    printf '%s\n' "$ASSAY_GATE_HOST_DOCKER_GID"
    return 0
  fi
  docker_socket=/var/run/docker.sock
  if [[ "${DOCKER_HOST:-}" == unix://?* ]]; then
    docker_socket="${DOCKER_HOST#unix://}"
  fi
  if [[ -S "$docker_socket" ]]; then
    docker_socket_gid="$(stat -Lc '%g' "$docker_socket" 2>/dev/null)" || docker_socket_gid=
  else
    docker_socket_gid=
  fi
  [[ "$docker_socket_gid" =~ ^[1-9][0-9]*$ ]] || {
    printf 'host Docker GID is unavailable: set ASSAY_GATE_HOST_DOCKER_GID to the host docker group numeric GID, or make the Docker API socket %s visible with a non-root owning group\n' "$docker_socket" >&2
    return 1
  }
  printf '%s\n' "$docker_socket_gid"
}
