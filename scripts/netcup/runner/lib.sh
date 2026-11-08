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

load_host_endpoints() {
  local endpoints_file=${1:-}
  [[ $# == 1 && -n $endpoints_file ]] || {
    echo 'host endpoints: loader requires an explicit record path' >&2
    return 2
  }
  if [[ -L $endpoints_file ]]; then
    echo "host endpoints: $endpoints_file must not be a symlink" >&2
    return 2
  fi
  if [[ ! -e $endpoints_file ]]; then
    echo "host endpoints: required record not found: $endpoints_file" >&2
    return 2
  fi
  if [[ ! -f $endpoints_file ]]; then
    echo "host endpoints: $endpoints_file is not a regular file" >&2
    return 2
  fi

  local stat_result owner_uid mode
  stat_result=$(stat -c '%u:%a' -- "$endpoints_file" 2>/dev/null) || {
    echo "host endpoints: cannot inspect owner and mode of $endpoints_file" >&2
    return 2
  }
  owner_uid=${stat_result%%:*}
  mode=${stat_result#*:}
  [[ $owner_uid == 0 ]] || {
    echo "host endpoints: $endpoints_file is not owned by root (uid $owner_uid)" >&2
    return 2
  }
  [[ $mode =~ ^[0-7]{1,4}$ ]] || {
    echo "host endpoints: cannot read a valid permission mode for $endpoints_file" >&2
    return 2
  }
  (( (8#$mode & 022) == 0 )) || {
    echo "host endpoints: $endpoints_file has group/other writable permissions (mode $mode)" >&2
    return 2
  }
  [[ -r $endpoints_file ]] || {
    echo "host endpoints: $endpoints_file is not readable" >&2
    return 2
  }

  local -a required_keys=(
    MDT_HOST_DOCKER_GID MDT_DOCKER_API_SOCKET MDT_DOCKER_API_SOCKET_DIR
    CGPROFILE_CONTROL_SOCKET CGPROFILE_CONTROL_SOCKET_DIR CGPROFILE_CONTROL_GID
    CGPROFILE_OBSERVER_SOCKET CGPROFILE_OBSERVER_SOCKET_DIR
    TOPOS_OBSERVE_SOCKET TOPOS_OBSERVE_SOCKET_DIR TOPOS_OBSERVE_GID
    MDT_BUILDKIT_SOCKET MDT_BUILDKIT_SOCKET_DIR MDT_BUILDKIT_GID
  )
  local -A runner_keys=(
    [MDT_HOST_DOCKER_GID]=1
    [MDT_DOCKER_API_SOCKET]=1
    [MDT_DOCKER_API_SOCKET_DIR]=1
    [CGPROFILE_CONTROL_SOCKET]=1
    [CGPROFILE_CONTROL_SOCKET_DIR]=1
    [CGPROFILE_CONTROL_GID]=1
    [CGPROFILE_OBSERVER_SOCKET]=1
    [CGPROFILE_OBSERVER_SOCKET_DIR]=1
    [TOPOS_OBSERVE_SOCKET]=1
    [TOPOS_OBSERVE_SOCKET_DIR]=1
    [TOPOS_OBSERVE_GID]=1
    [MDT_BUILDKIT_SOCKET]=1
    [MDT_BUILDKIT_SOCKET_DIR]=1
    [MDT_BUILDKIT_GID]=1
  )
  local -A seen_keys=()
  local -A parsed_runner_keys=()
  local line_number=0 line key value record_re='^([A-Z][A-Z0-9_]{0,63})=([A-Za-z0-9_./:@+-]+)$'
  local name
  local LC_ALL=C

  # A record is data, never shell input. Clear ambient values first so a
  # missing record key cannot be supplied by the caller's environment.
  for name in "${required_keys[@]}"; do unset "$name"; done
  while IFS= read -r line || [[ -n $line ]]; do
    line_number=$((line_number + 1))
    [[ -z $line || $line == \#* ]] && continue
    if (( ${#line} > 4161 )); then
      echo "host endpoints: invalid record in $endpoints_file:$line_number" >&2
      return 2
    fi
    if [[ ! $line =~ $record_re ]]; then
      echo "host endpoints: invalid record in $endpoints_file:$line_number" >&2
      return 2
    fi
    key=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    if (( ${#value} > 4096 )); then
      echo "host endpoints: invalid record in $endpoints_file:$line_number" >&2
      return 2
    fi
    if [[ ${seen_keys[$key]+present} ]]; then
      echo "host endpoints: duplicate key $key in $endpoints_file:$line_number" >&2
      return 2
    fi
    seen_keys[$key]=1
    if [[ ${runner_keys[$key]+present} ]]; then
      parsed_runner_keys[$key]=$value
    fi
  done < "$endpoints_file"

  for _v in "${required_keys[@]}"; do
    [[ -n ${parsed_runner_keys[$_v]:-} ]] || {
      echo "host endpoints: $_v is not set in $endpoints_file" >&2
      return 2
    }
  done

  local -a path_keys=(
    MDT_DOCKER_API_SOCKET MDT_DOCKER_API_SOCKET_DIR
    CGPROFILE_CONTROL_SOCKET CGPROFILE_CONTROL_SOCKET_DIR
    CGPROFILE_OBSERVER_SOCKET CGPROFILE_OBSERVER_SOCKET_DIR
    TOPOS_OBSERVE_SOCKET TOPOS_OBSERVE_SOCKET_DIR
    MDT_BUILDKIT_SOCKET MDT_BUILDKIT_SOCKET_DIR
  )
  for name in "${path_keys[@]}"; do
    value=${parsed_runner_keys[$name]}
    [[ $value == /* ]] || {
      echo "host endpoints: $name must be absolute" >&2
      return 2
    }
  done
  local -a socket_pairs=(
    'MDT_DOCKER_API_SOCKET:MDT_DOCKER_API_SOCKET_DIR'
    'CGPROFILE_CONTROL_SOCKET:CGPROFILE_CONTROL_SOCKET_DIR'
    'CGPROFILE_OBSERVER_SOCKET:CGPROFILE_OBSERVER_SOCKET_DIR'
    'TOPOS_OBSERVE_SOCKET:TOPOS_OBSERVE_SOCKET_DIR'
    'MDT_BUILDKIT_SOCKET:MDT_BUILDKIT_SOCKET_DIR'
  )
  local socket_name dir_name socket_path expected_dir
  for name in "${socket_pairs[@]}"; do
    socket_name=${name%%:*}
    dir_name=${name#*:}
    socket_path=${parsed_runner_keys[$socket_name]}
    expected_dir=$(dirname -- "$socket_path")
    [[ ${parsed_runner_keys[$dir_name]} == "$expected_dir" ]] || {
      echo "host endpoints: $dir_name must equal dirname($socket_name)" >&2
      return 2
    }
  done

  [[ ${parsed_runner_keys[MDT_HOST_DOCKER_GID]} =~ ^[1-9][0-9]*$ ]] || {
    echo 'host endpoints: MDT_HOST_DOCKER_GID must be a positive decimal GID without leading zeros' >&2
    return 2
  }
  local docker_gid=${parsed_runner_keys[MDT_HOST_DOCKER_GID]}
  local -a level_gids=(CGPROFILE_CONTROL_GID TOPOS_OBSERVE_GID MDT_BUILDKIT_GID)
  local name value left right left_gid right_gid i j
  for name in "${level_gids[@]}"; do
    value=${parsed_runner_keys[$name]}
    [[ $value =~ ^[1-9][0-9]*$ ]] || {
      echo "host endpoints: $name must be a positive decimal GID without leading zeros" >&2
      return 2
    }
    [[ $value != "$docker_gid" ]] || {
      echo "host endpoints: $name must differ from MDT_HOST_DOCKER_GID" >&2
      return 2
    }
  done
  for ((i = 0; i < ${#level_gids[@]}; i++)); do
    left=${level_gids[i]}
    left_gid=${parsed_runner_keys[$left]}
    for ((j = i + 1; j < ${#level_gids[@]}; j++)); do
      right=${level_gids[j]}
      right_gid=${parsed_runner_keys[$right]}
      [[ $left_gid != "$right_gid" ]] || {
        echo "host endpoints: $left must differ from $right" >&2
        return 2
      }
    done
  done

  # Commit the record to the caller's environment only after every line,
  # required key, path and GID relationship has been accepted.
  for name in "${required_keys[@]}"; do
    export "$name=${parsed_runner_keys[$name]}"
  done
}

require_host_docker_group_match() {
  local docker_group_entry docker_group_gid
  docker_group_entry=$(getent group docker) || {
    echo 'host endpoints: local docker group is missing' >&2
    exit 2
  }
  docker_group_gid=${docker_group_entry#*:*:}
  docker_group_gid=${docker_group_gid%%:*}
  [[ $docker_group_gid == "$MDT_HOST_DOCKER_GID" ]] || {
    echo 'host endpoints: MDT_HOST_DOCKER_GID does not match the local docker group GID' >&2
    exit 2
  }
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
