#!/usr/bin/env bash
# Host-setup's endpoint fragment operations. This file is sourced by install.sh
# and can also be sourced by isolated tests with an explicit fake host root.

legacy_host_setup_paths() {
  cat <<'EOF'
/etc/tmpfiles.d/mdt-cgprofile.conf
/etc/tmpfiles.d/mdt-cgprofile-observer.conf
/etc/tmpfiles.d/mdt-topos.conf
/etc/sysusers.d/mdt-cgprofile.conf
/etc/systemd/system/topos.service.d/50-mdt-socket-group.conf
/usr/local/sbin/mdt-cgprofile-install.sh
/etc/profile.d/mdt-buildkit.sh
EOF
}

refuse_legacy_host_setup_files() {
  local root="${1:?host root is required}" prefix="" path host_path
  local -a found=()
  if [ "$root" != / ]; then
    [[ "$root" == /* ]] || { echo "ERROR: host root must be absolute: $root" >&2; return 2; }
    prefix="${root%/}"
  fi
  while IFS= read -r path; do
    host_path="${prefix}${path}"
    if [ -e "$host_path" ] || [ -L "$host_path" ]; then found+=("$path"); fi
  done < <(legacy_host_setup_paths)
  if [ -f "${prefix}/etc/environment" ] \
    && grep -qE '^# BEGIN mdt-buildkit([[:space:]]|$)' "${prefix}/etc/environment"; then
    found+=("/etc/environment (# BEGIN mdt-buildkit block)")
  fi
  [ "${#found[@]}" -eq 0 ] && return 0

  echo "ERROR: legacy cgprofile/topos/BuildKit host-setup files remain; no changes were made:" >&2
  printf '  %s\n' "${found[@]}" >&2
  echo "Remove these old files manually, then re-run host-setup:" >&2
  for path in "${found[@]}"; do
    case "$path" in
      "/etc/environment (# BEGIN mdt-buildkit block)")
        echo "  sudoedit /etc/environment  # remove only the old mdt-buildkit block and preserve unrelated lines" >&2
        ;;
      *) echo "  sudo rm -f -- '$path'" >&2 ;;
    esac
  done
  return 2
}

require_augeaswrap() {
  local command_name="${AUGEASWRAP_BIN:-augeaswrap}"
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "ERROR: host endpoint publication requires augeaswrap 0.1.0; install the augeaswrap wheel and make '$command_name' available on PATH before running host-setup/install.sh" >&2
    return 2
  }
}

host_setup_endpoint_entries() {
  local checkout_root="${1:?checkout root is required}" root="${2:-/}" prefix=""
  local docker_entry docker_gid buildkit_socket buildkit_gid docker_api_socket
  local -a docker_api_sockets=()
  if [ "$root" != / ]; then
    [[ "$root" == /* ]] || { echo "ERROR: host root must be absolute: $root" >&2; return 2; }
    prefix="${root%/}"
  fi

  docker_entry="$(getent group docker 2>/dev/null)" || {
    echo "ERROR: could not resolve the host docker group for endpoint publication" >&2
    return 2
  }
  IFS=: read -r _ _ docker_gid _ <<< "$docker_entry"
  [[ "$docker_gid" =~ ^[1-9][0-9]*$ ]] || {
    echo "ERROR: the host docker group has an invalid GID for endpoint publication" >&2
    return 2
  }

  buildkit_socket="$(host_setup_buildkit_socket_path "$checkout_root")" || return $?
  # Read the live host socket group only after the service has exposed and
  # sealed the socket.
  [ -S "${prefix}${buildkit_socket}" ] && [ ! -L "${prefix}${buildkit_socket}" ] || {
    echo "ERROR: $buildkit_socket is not a real BuildKit socket; endpoint facts are not ready" >&2
    return 2
  }
  buildkit_gid="$(stat -c '%g' "${prefix}${buildkit_socket}")" || {
    echo "ERROR: could not resolve the live BuildKit socket GID" >&2
    return 2
  }
  [[ "$buildkit_gid" =~ ^[1-9][0-9]*$ ]] || {
    echo "ERROR: the live BuildKit socket has an invalid GID for endpoint publication" >&2
    return 2
  }

  mapfile -t docker_api_sockets < <(sed -n 's/^ListenStream=//p' "$checkout_root/units/docker-api-socket.conf")
  [ "${#docker_api_sockets[@]}" -eq 1 ] && [[ "${docker_api_sockets[0]}" == /* ]] || {
    echo "ERROR: units/docker-api-socket.conf must declare exactly one absolute ListenStream path" >&2
    return 2
  }
  docker_api_socket="${docker_api_sockets[0]}"

  printf '%s\n' \
    'BUILDX_BUILDER=mdt-managed' \
    "BUILDKIT_HOST=unix://${buildkit_socket}" \
    "MDT_HOST_DOCKER_GID=${docker_gid}" \
    "MDT_BUILDKIT_SOCKET=${buildkit_socket}" \
    "MDT_BUILDKIT_SOCKET_DIR=${buildkit_socket%/*}" \
    "MDT_BUILDKIT_GID=${buildkit_gid}" \
    "MDT_DOCKER_API_SOCKET=${docker_api_socket}" \
    "MDT_DOCKER_API_SOCKET_DIR=${docker_api_socket%/*}"
}

host_setup_buildkit_socket_path() {
  local checkout_root="${1:?checkout root is required}" runtime_dir buildkit_addr
  local -a runtime_dirs=() buildkit_addrs=()
  mapfile -t runtime_dirs < <(sed -n 's/^RuntimeDirectory=//p' "$checkout_root/units/mdt-buildkitd.service.in")
  mapfile -t buildkit_addrs < <(sed -n 's/.*--addr unix:\/\/\([^[:space:]\\]*\).*/\1/p' "$checkout_root/units/mdt-buildkitd.service.in")
  [ "${#runtime_dirs[@]}" -eq 1 ] && [[ "${runtime_dirs[0]}" =~ ^[A-Za-z0-9_.-]+$ ]] \
    || { echo "ERROR: units/mdt-buildkitd.service.in must declare exactly one RuntimeDirectory name" >&2; return 2; }
  [ "${#buildkit_addrs[@]}" -eq 1 ] && [[ "${buildkit_addrs[0]}" == /* ]] \
    || { echo "ERROR: units/mdt-buildkitd.service.in must declare exactly one absolute BuildKit --addr path" >&2; return 2; }
  runtime_dir="/run/${runtime_dirs[0]}"
  buildkit_addr="${buildkit_addrs[0]}"
  # RuntimeDirectory is bound to the container path passed to --addr; the
  # host socket keeps the address basename under that host-owned directory.
  printf '%s/%s\n' "$runtime_dir" "${buildkit_addr##*/}"
}

withdraw_host_setup_endpoints() {
  local command_name="${AUGEASWRAP_BIN:-augeaswrap}"
  require_augeaswrap || return $?
  "$command_name" endpoints withdraw --owner mdt-host-setup
}

publish_host_setup_endpoints() {
  local checkout_root="${1:?checkout root is required}" mode="${2:-publish}" root="${3:-/}"
  local entries writer="mdt host-setup install" command_name="${AUGEASWRAP_BIN:-augeaswrap}"
  local entry
  local -a entry_args=() values=()
  if [ "$mode" = withdraw ]; then
    withdraw_host_setup_endpoints
    return $?
  fi
  entries="$(host_setup_endpoint_entries "$checkout_root" "$root")" || return $?
  mapfile -t values <<< "$entries"
  if [ "$mode" = dry-run ]; then
    printf '# host-endpoints v1\n# owner=mdt-host-setup\n# writer=%s\n' "$writer"
    printf '%s\n' "${values[@]}"
    return 0
  fi
  [ "$mode" = publish ] || { echo "ERROR: unknown endpoint operation: $mode" >&2; return 2; }
  require_augeaswrap || return $?
  for entry in "${values[@]}"; do entry_args+=(--entry "$entry"); done
  "$command_name" endpoints publish --owner mdt-host-setup --writer "$writer" "${entry_args[@]}"
}
