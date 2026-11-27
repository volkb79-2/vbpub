#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: pi-ctl-launch.sh [--print-command] [--fresh] <role>

Start or resume a pi sub-controller in tmux. PI_CTL_PROVIDER accepts
opendesign_volker (default) or opendesign_github2.
EOF
}

print_command=0
fresh=0
role=
while (($#)); do
  case $1 in
    --print-command) print_command=1 ;;
    --fresh) fresh=1 ;;
    --help|-h) usage; exit 0 ;;
    --*) usage; exit 2 ;;
    *)
      if [[ -n $role ]]; then usage; exit 2; fi
      role=$1
      ;;
  esac
  shift
done
[[ -n $role ]] || { usage; exit 2; }
[[ $role =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || {
  printf 'ERROR: invalid controller role name: %s\n' "$role" >&2
  exit 2
}

command -v flock >/dev/null 2>&1 || {
  echo 'ERROR: flock is required to hold the controller session lock' >&2
  exit 2
}

reject_symlink_components() {
  local candidate=$1 current=/ rest component
  [[ $candidate == /* ]] || return 1
  rest=${candidate#/}
  while [[ -n $rest ]]; do
    if [[ $rest == */* ]]; then
      component=${rest%%/*}
      rest=${rest#*/}
    else
      component=$rest
      rest=
    fi
    [[ -z $component || $component == . ]] && continue
    if [[ $component == .. ]]; then
      current=${current%/*}
      [[ -n $current ]] || current=/
      continue
    fi
    if [[ $current == / ]]; then current=/$component; else current=$current/$component; fi
    [[ ! -L $current ]] || return 1
  done
  return 0
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
COMMON_GIT_DIR=$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)
MAIN_ROOT=$(dirname -- "$COMMON_GIT_DIR")
: "${HOME:?HOME is required}"
provider=${PI_CTL_PROVIDER:-opendesign_volker}
case $provider in
  opendesign_volker|opendesign_github2) ;;
  *) printf 'ERROR: PI_CTL_PROVIDER must be opendesign_volker or opendesign_github2\n' >&2; exit 2 ;;
esac
model=${PI_CTL_MODEL:-deepseek-v4.1-flash-eco}
thinking=${PI_CTL_THINKING:-max}
cwd_input=${PI_CTL_CWD:-$REPO_ROOT}
[[ -d $cwd_input ]] || { printf 'ERROR: PI_CTL_CWD is not a directory: %s\n' "$cwd_input" >&2; exit 2; }
cwd=$(cd -- "$cwd_input" && pwd -P)
root=${PI_CTL_ROOT:-$HOME/controller/pi-ctl}
[[ $root == /* ]] || { echo 'ERROR: PI_CTL_ROOT must be an absolute path' >&2; exit 2; }
reject_symlink_components "$root" || {
  printf 'ERROR: PI_CTL_ROOT contains a symlink path component: %s\n' "$root" >&2
  exit 2
}
root=$(realpath -m -- "$root")
[[ $root != / ]] || { echo 'ERROR: PI_CTL_ROOT cannot be /' >&2; exit 2; }
for repo_path in "$REPO_ROOT" "$MAIN_ROOT"; do
  case $root in
    "$repo_path"|"$repo_path"/*)
      echo 'ERROR: PI_CTL_ROOT must be outside the repository' >&2
      exit 2
      ;;
  esac
done
for checked_path in "$root/locks" "$root/locks/$role.lock" "$root/sessions" "$root/sessions/$role.session"; do
  reject_symlink_components "$checked_path" || {
    printf 'ERROR: PI_CTL_ROOT state path contains a symlink component: %s\n' "$checked_path" >&2
    exit 2
  }
done
prime=${PI_CTL_PRIME:-$MAIN_ROOT/.controller-state/PROGRAM-2026-11-24/PRIME-$role.md}
[[ $prime == /* ]] || { echo 'ERROR: PI_CTL_PRIME must be an absolute path' >&2; exit 2; }
pi_bin=${PI_CTL_BIN:-pi}
session_dir=${PI_CTL_SESSION_DIR:-$HOME/.pi/agent/sessions}
[[ $session_dir == /* ]] || { echo 'ERROR: PI_CTL_SESSION_DIR must be an absolute path' >&2; exit 2; }
ready_timeout=${PI_CTL_STARTUP_TIMEOUT_SECONDS:-30}
[[ $ready_timeout =~ ^[0-9]+$ && $ready_timeout -ge 1 && $ready_timeout -le 120 ]] || {
  echo 'ERROR: PI_CTL_STARTUP_TIMEOUT_SECONDS must be from 1 to 120' >&2
  exit 2
}
pidns_extension=$SCRIPT_DIR/pi-pidns-extension.js
controller_extension=$SCRIPT_DIR/pi-ctl/extension.mjs
pidns_wrapper=$SCRIPT_DIR/pi-pidns-bash
pane_script=$SCRIPT_DIR/pi-ctl-pane.sh
session_name=vbpub-$role
session_file=$root/sessions/$role.session

if (( fresh )); then
  printf -v stamp '%(%Y%m%dT%H%M%SZ)T' -1
  session_id=vbpub-$role-$stamp-$$
elif [[ -e $session_file || -L $session_file ]]; then
  [[ -f $session_file && ! -L $session_file && -r $session_file ]] || {
    printf 'ERROR: invalid active session record: %s\n' "$session_file" >&2
    exit 2
  }
  IFS= read -r session_id < "$session_file" || :
  [[ $session_id =~ ^[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]$ ]] || {
    printf 'ERROR: invalid active session id for role %s\n' "$role" >&2
    exit 2
  }
  case $session_id in
    "vbpub-$role"|"vbpub-$role-"*) ;;
    *) printf 'ERROR: active session id does not belong to role %s\n' "$role" >&2; exit 2 ;;
  esac
else
  session_id=vbpub-$role
fi

pi_args=(--no-extensions --no-approve
  -e "$pidns_extension" -e "$controller_extension"
  --provider "$provider" --model "$model" --thinking "$thinking"
  --session-dir "$session_dir" --session-id "$session_id" --name "$session_name")
controller_env=()
for env_name in PI_CTL_POLL_MS PI_CTL_MAX_MESSAGE_BYTES PI_CTL_MAX_SEND_BYTES \
  PI_CTL_SEND_LIMIT PI_CTL_INBOUND_LIMIT PI_CTL_LOOP_WINDOW_SECONDS \
  PI_CTL_COMPACT_AT PI_CTL_COMPACT_BACKOFF_SECONDS; do
  if [[ -v $env_name ]]; then controller_env+=("$env_name=${!env_name}"); fi
done
ready_token=$session_id-$$
ready_file=$root/sessions/.ready-$role-$ready_token
pane_args=(env "${controller_env[@]}" "PI_CTL_READY_FILE=$ready_file" "PI_CTL_READY_TOKEN=$ready_token" "$pane_script" "$role" "$provider" "$model" "$thinking" \
  "$session_id" "$root" "$prime" "$pi_bin" "$session_dir" "$pidns_extension" \
  "$controller_extension" "$pidns_wrapper" "$session_name")
printf -v pane_command '%q ' "${pane_args[@]}"
pane_command=${pane_command% }

if (( print_command )); then
  printf 'tmux session: %s\nworking directory: %q\nactive pi session id: %s\nsession lock: %s (flock held by the pane until pi exits)\n' \
    "$session_name" "$cwd" "$session_id" "$root/locks/$role.lock"
  echo 'lock requirement: util-linux flock must be available; pi started by hand without the pane lock is refused'
  printf 'pi command: %q' "$pi_bin"
  for argument in "${pi_args[@]}"; do printf ' %q' "$argument"; done
  printf '\npane command: %s\n' "$pane_command"
  exit 0
fi

# A caller that sourced credentials earlier must not pass them into the tmux client.
unset OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2

[[ -r $prime && -f $prime && ! -L $prime ]] || {
  printf 'ERROR: role rules file is missing or not a regular file: %s\n' "$prime" >&2
  exit 2
}
command -v tmux >/dev/null 2>&1 || { echo 'ERROR: tmux is not available on PATH' >&2; exit 2; }
umask 077
mkdir -p -- "$root/locks" "$root/sessions"
chmod 0700 -- "$root" "$root/locks" "$root/sessions"

lock_file=$root/locks/$role.lock
if [[ -L $lock_file || ( -e $lock_file && ! -f $lock_file ) ]]; then
  printf 'ERROR: controller lock path is not a regular, non-symlink file: %s\n' "$lock_file" >&2
  exit 2
fi
if ! flock -n "$lock_file" true; then
  printf 'ERROR: controller %s already has a live session lock\n' "$role" >&2
  exit 2
fi

if tmux has-session -t "$session_name" 2>/dev/null; then
  printf 'ERROR: tmux session %s already exists\n' "$session_name" >&2
  exit 2
fi

rm -f -- "$ready_file"
if ! tmux new-session -d -s "$session_name" -c "$cwd" "$pane_command"; then
  printf 'ERROR: tmux could not create controller session %s\n' "$session_name" >&2
  exit 2
fi

ready=0
for ((tick = 0; tick < ready_timeout * 10; tick++)); do
  if [[ -f $ready_file && ! -L $ready_file ]]; then
    IFS= read -r observed_token < "$ready_file" || :
    if [[ $observed_token == "$ready_token" ]]; then ready=1; break; fi
  fi
  if ! tmux has-session -t "$session_name" 2>/dev/null; then break; fi
  sleep 0.1
done
if (( ! ready )); then
  printf 'ERROR: pi pane %s did not signal extension session_start within %s seconds; saved session id was not changed\n' "$session_name" "$ready_timeout" >&2
  exit 2
fi

session_tmp=$session_file.tmp.$BASHPID
printf '%s\n' "$session_id" > "$session_tmp"
chmod 0600 -- "$session_tmp"
mv -f -- "$session_tmp" "$session_file"
rm -f -- "$ready_file"
printf 'Started pi controller %s in tmux session %s (session id %s).\n' "$role" "$session_name" "$session_id"
