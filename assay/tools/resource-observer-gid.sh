#!/usr/bin/env bash
# Shared host-identity guard for Assay's observer-enabled gate launchers.
# Call only after the launcher has selected its approved observer socket path.

assay_resource_observer_gid() {
  local observer_source="$1"
  local parent parent_uid parent_mode
  local socket_uid socket_gid socket_mode host_docker_gid

  parent="$(dirname "$observer_source")"
  [[ -d "$parent" && ! -L "$parent" ]] || {
    printf 'cgprofile observer directory is absent, not a directory, or a symlink: %s; complete the host socket cutover\n' "$parent" >&2
    return 1
  }
  read -r parent_uid parent_mode < <(stat -Lc '%u %a' "$parent") || {
    printf 'could not inspect the cgprofile observer directory: %s\n' "$parent" >&2
    return 1
  }
  [[ "$parent_uid" == 0 ]] || {
    printf 'cgprofile observer directory is not root-owned: %s; complete the host socket cutover\n' "$parent" >&2
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
    printf 'cgprofile observer socket gid %s equals the host docker gid %s (from %s); the observer must use its dedicated group, not the host docker group\n' \
      "$socket_gid" "$host_docker_gid" "$(assay_host_docker_gid_source)" >&2
    return 1
  }
  (( (8#$socket_mode & 0007) == 0 && (8#$socket_mode & 0020) != 0 )) || {
    printf 'cgprofile observer socket must be group-connectable and not accessible to other users\n' >&2
    return 1
  }
  printf '%s\n' "$socket_gid"
}

# The host Docker GID: (1) the explicit ASSAY_GATE_HOST_DOCKER_GID override,
# then (2) MDT_HOST_DOCKER_GID supplied by the host launcher. Either selected
# value must be a non-root decimal GID; set-but-empty or malformed is an error.
# The Docker API socket's owning GID is not authoritative: a proxy or relay may
# use a different access group. The group database is never consulted.
assay_host_docker_gid() {
  local variable value
  for variable in ASSAY_GATE_HOST_DOCKER_GID MDT_HOST_DOCKER_GID; do
    [[ -v "$variable" ]] || continue
    value="${!variable}"
    [[ "$value" =~ ^[1-9][0-9]*$ ]] || {
      printf '%s must be a verified non-root numeric GID\n' "$variable" >&2
      return 1
    }
    printf '%s\n' "$value"
    return 0
  done
  printf 'host Docker GID is unavailable; set ASSAY_GATE_HOST_DOCKER_GID or MDT_HOST_DOCKER_GID to the host Docker group numeric GID\n' >&2
  return 1
}

# Where assay_host_docker_gid took its value from, for diagnostics only.
assay_host_docker_gid_source() {
  if [[ -v ASSAY_GATE_HOST_DOCKER_GID ]]; then
    printf 'ASSAY_GATE_HOST_DOCKER_GID\n'
  elif [[ -v MDT_HOST_DOCKER_GID ]]; then
    printf 'MDT_HOST_DOCKER_GID\n'
  fi
}
