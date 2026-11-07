#!/usr/bin/env bash
# Description: shared helpers for the runner scripts (sourced, never executed): env loading and the identity guard.
# shellcheck shell=bash
runner_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runner_env=${RUNNER_ENV:-$runner_dir/runner.env}
if [[ ! -f $runner_env ]]; then
  echo "runner env file not found: $runner_env (copy runner.env.example to runner.env and fill it in)" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "$runner_env"
for _v in RUNNER_FQDN RUNNER_SHORT RUNNER_SERVER_ID RUNNER_USER RUNNER_UID RUNNER_HOME COW_DEVICE COW_PARENT_DISK \
    COW_MOUNT COW_ROOT GATES_SLICE; do
  [[ -n ${!_v:-} ]] || { echo "runner env: $_v is not set in $runner_env" >&2; exit 2; }
done
[[ $RUNNER_SERVER_ID =~ ^[0-9]+$ && $RUNNER_UID =~ ^[0-9]+$ ]] || { echo 'runner env: RUNNER_SERVER_ID and RUNNER_UID must be numeric' >&2; exit 2; }

identity_check() {
  test "$(hostname -f)" = "$RUNNER_FQDN"
  test "$(hostnamectl --static)" = "$RUNNER_SHORT"
  test "$(cat /etc/vbpub/server-id)" = "$RUNNER_SERVER_ID"
}

_normalize_endpoint_gid() {
  local value=$1
  while [[ ${#value} -gt 1 && $value == 0* ]]; do value=${value#0}; done
  printf '%s' "$value"
}

load_host_endpoints() {
  local endpoints_file=/etc/host-endpoints.env
  if [[ ${NETCUP_TEST_HOST_ENDPOINTS_FILE+x} ]]; then
    endpoints_file=$NETCUP_TEST_HOST_ENDPOINTS_FILE
  fi
  [[ -f $endpoints_file ]] || {
    echo "host endpoints: required record not found: $endpoints_file" >&2
    exit 2
  }
  # shellcheck disable=SC1090
  source "$endpoints_file"
  for _v in MDT_HOST_DOCKER_GID MDT_DOCKER_API_SOCKET MDT_DOCKER_API_SOCKET_DIR \
      CGPROFILE_CONTROL_SOCKET CGPROFILE_CONTROL_SOCKET_DIR CGPROFILE_CONTROL_GID \
      CGPROFILE_OBSERVER_SOCKET_DIR TOPOS_OBSERVE_SOCKET TOPOS_OBSERVE_SOCKET_DIR TOPOS_OBSERVE_GID \
      MDT_BUILDKIT_SOCKET MDT_BUILDKIT_SOCKET_DIR MDT_BUILDKIT_GID; do
    [[ -n ${!_v:-} ]] || {
      echo "host endpoints: $_v is not set in $endpoints_file" >&2
      exit 2
    }
  done

  [[ $MDT_HOST_DOCKER_GID =~ ^[0-9]+$ ]] || {
    echo 'host endpoints: MDT_HOST_DOCKER_GID must be numeric' >&2
    exit 2
  }
  local docker_gid
  docker_gid=$(_normalize_endpoint_gid "$MDT_HOST_DOCKER_GID")
  local -a level_gids=(CGPROFILE_CONTROL_GID TOPOS_OBSERVE_GID MDT_BUILDKIT_GID)
  local name value normalized left right left_gid right_gid i j
  for name in "${level_gids[@]}"; do
    value=${!name}
    [[ $value =~ ^[0-9]+$ ]] || {
      echo "host endpoints: $name must be numeric" >&2
      exit 2
    }
    normalized=$(_normalize_endpoint_gid "$value")
    [[ $normalized != 0 ]] || {
      echo "host endpoints: $name must not be 0" >&2
      exit 2
    }
    [[ $normalized != "$docker_gid" ]] || {
      echo "host endpoints: $name must differ from MDT_HOST_DOCKER_GID" >&2
      exit 2
    }
  done
  for ((i = 0; i < ${#level_gids[@]}; i++)); do
    left=${level_gids[i]}
    left_gid=$(_normalize_endpoint_gid "${!left}")
    for ((j = i + 1; j < ${#level_gids[@]}; j++)); do
      right=${level_gids[j]}
      right_gid=$(_normalize_endpoint_gid "${!right}")
      [[ $left_gid != "$right_gid" ]] || {
        echo "host endpoints: $left must differ from $right" >&2
        exit 2
      }
    done
  done
}

# The synced source checkout; with RUNNER_EXPECTED_COMMIT set, refuse any other commit.
source_repo() {
  local repo=$RUNNER_HOME/src/vbpub-current
  if [[ -n ${RUNNER_EXPECTED_COMMIT:-} ]]; then
    test "$(git -C "$repo" rev-parse HEAD)" = "$RUNNER_EXPECTED_COMMIT"
  fi
  printf '%s\n' "$repo"
}

require_gates_slice() {
  test "$(systemctl show "$GATES_SLICE" --property=LoadState --value)" = loaded
  test -n "$(systemctl show "$GATES_SLICE" --property=FragmentPath --value)"
}
