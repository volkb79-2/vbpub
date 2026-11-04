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
