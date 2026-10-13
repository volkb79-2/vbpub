#!/usr/bin/env bash
# Shared host-identity guard for Assay's observer-enabled gate launchers.
# Call only after the launcher has selected its approved observer socket path.

assay_resource_observer_gid() {
  local observer_source="$1" control_directory="${2:-/run/cgprofile}"
  local parent parent_uid parent_gid parent_mode
  local socket_uid socket_gid socket_mode host_docker_gid

  parent="$(dirname "$observer_source")"
  [[ -d "$parent" && ! -L "$parent" ]] || {
    printf 'cgprofile observer directory is absent, not a directory, or a symlink: %s\n' "$parent" >&2
    return 1
  }
  read -r parent_uid parent_gid parent_mode < <(stat -Lc '%u %g %a' "$parent") || {
    printf 'could not inspect the cgprofile observer directory: %s\n' "$parent" >&2
    return 1
  }
  [[ "$parent_uid" == 0 ]] || {
    printf 'cgprofile observer directory is not root-owned: %s\n' "$parent" >&2
    return 1
  }
  (( (8#$parent_mode & 0022) == 0 && (8#$parent_mode & 0010) != 0 )) || {
    printf 'cgprofile observer directory must be group-searchable and not writable by group or other users\n' >&2
    return 1
  }
  [[ -S "$observer_source" && ! -L "$observer_source" ]] || {
    printf 'cgprofile observer endpoint is not a Unix socket: %s\n' "$observer_source" >&2
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
  if [[ "$socket_gid" != "$parent_gid" ]]; then
    printf 'cgprofile observer socket group does not match its root-owned directory\n' >&2
    return 1
  fi

  host_docker_gid="$(assay_host_docker_gid "$control_directory")" || return 1
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

assay_host_docker_gid() {
  local control_directory="${1:-/run/cgprofile}"
  local control_uid control_gid control_mode
  if [[ -v ASSAY_GATE_HOST_DOCKER_GID ]]; then
    [[ "$ASSAY_GATE_HOST_DOCKER_GID" =~ ^[1-9][0-9]*$ ]] || {
      printf 'ASSAY_GATE_HOST_DOCKER_GID must be a verified non-root numeric GID\n' >&2
      return 1
    }
    printf '%s\n' "$ASSAY_GATE_HOST_DOCKER_GID"
    return 0
  fi
  [[ -d "$control_directory" && ! -L "$control_directory" ]] || {
    printf 'host Docker GID is unavailable; pass ASSAY_GATE_HOST_DOCKER_GID from the host launcher\n' >&2
    return 1
  }
  read -r control_uid control_gid control_mode < <(stat -Lc '%u %g %a' "$control_directory") || {
    printf 'could not determine the host Docker group from %s\n' "$control_directory" >&2
    return 1
  }
  [[ "$control_uid" == 0 && "$control_gid" =~ ^[1-9][0-9]*$ && "$control_mode" == 770 ]] || {
    printf 'host Docker group source %s must be root-owned, have a non-root group, and mode 0770\n' "$control_directory" >&2
    return 1
  }
  printf '%s\n' "$control_gid"
}
