#!/usr/bin/env bash
set -euo pipefail

if (($# != 13)); then
  echo 'ERROR: pi controller pane received an invalid launch contract' >&2
  exit 2
fi

role=$1
provider=$2
model=$3
thinking=$4
session_id=$5
root=$6
prime=$7
pi_bin=$8
session_dir=$9
pidns_extension=${10}
controller_extension=${11}
pidns_wrapper=${12}
expected_session_name=${13}

[[ $role =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { echo 'ERROR: invalid controller role name' >&2; exit 2; }
case $provider in
  opendesign_volker|opendesign_github2) ;;
  *) echo 'ERROR: PI_CTL_PROVIDER must be opendesign_volker or opendesign_github2' >&2; exit 2 ;;
esac
[[ $session_id =~ ^[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]$ ]] || { echo 'ERROR: invalid pi session id' >&2; exit 2; }
[[ $expected_session_name == "vbpub-$role" ]] || { echo 'ERROR: invalid tmux session name' >&2; exit 2; }
[[ -r $HOME/.env.ai && -f $HOME/.env.ai ]] || { echo 'ERROR: local pi API-key environment file is missing or unreadable' >&2; exit 2; }

# The credential file is opened only after tmux has started this pane. It is
# never passed as argv, and source output is suppressed.
unset OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2
# shellcheck disable=SC1091
if ! . "$HOME/.env.ai" >/dev/null 2>&1; then
  echo 'ERROR: could not load the local pi API-key environment' >&2
  exit 2
fi

case $provider in
  opendesign_volker)
    [[ -v OPENDESIGN_API_KEY_VOLKER && -n $OPENDESIGN_API_KEY_VOLKER ]] || {
      echo 'ERROR: OPENDESIGN_API_KEY_VOLKER is not set by ~/.env.ai' >&2
      exit 2
    }
    export OPENDESIGN_API_KEY_VOLKER
    unset OPENDESIGN_API_KEY_GITHUB2
    ;;
  opendesign_github2)
    [[ -v OPENDESIGN_API_KEY_GITHUB2 && -n $OPENDESIGN_API_KEY_GITHUB2 ]] || {
      echo 'ERROR: OPENDESIGN_API_KEY_GITHUB2 is not set by ~/.env.ai' >&2
      exit 2
    }
    export OPENDESIGN_API_KEY_GITHUB2
    unset OPENDESIGN_API_KEY_VOLKER
    ;;
esac

export PI_CTL_NAME=$role
export PI_CTL_ROOT=$root
export PI_CTL_PRIME=$prime
export PI_CTL_PIDNS_WRAPPER=$pidns_wrapper
export PI_CTL_PROVIDER=$provider

exec "$pi_bin" \
  --no-extensions --no-approve \
  -e "$pidns_extension" -e "$controller_extension" \
  --provider "$provider" --model "$model" --thinking "$thinking" \
  --session-dir "$session_dir" --session-id "$session_id" --name "$expected_session_name"
