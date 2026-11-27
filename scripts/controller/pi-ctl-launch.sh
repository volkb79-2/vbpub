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
prime=${PI_CTL_PRIME:-$MAIN_ROOT/.controller-state/PROGRAM-2026-11-24/PRIME-$role.md}
[[ $prime == /* ]] || { echo 'ERROR: PI_CTL_PRIME must be an absolute path' >&2; exit 2; }
pi_bin=${PI_CTL_BIN:-pi}
session_dir=${PI_CTL_SESSION_DIR:-$HOME/.pi/agent/sessions}
[[ $session_dir == /* ]] || { echo 'ERROR: PI_CTL_SESSION_DIR must be an absolute path' >&2; exit 2; }
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
pane_args=(env "${controller_env[@]}" "$pane_script" "$role" "$provider" "$model" "$thinking" \
  "$session_id" "$root" "$prime" "$pi_bin" "$session_dir" "$pidns_extension" \
  "$controller_extension" "$pidns_wrapper" "$session_name")
printf -v pane_command '%q ' "${pane_args[@]}"
pane_command=${pane_command% }

if (( print_command )); then
  printf 'tmux session: %s\nworking directory: %q\nactive pi session id: %s\n' "$session_name" "$cwd" "$session_id"
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
if [[ -L $root || -L $root/locks || -L $root/sessions ]]; then
  echo 'ERROR: PI_CTL_ROOT or one of its controller directories is a symlink' >&2
  exit 2
fi
umask 077
mkdir -p -- "$root/locks" "$root/sessions"
chmod 0700 -- "$root" "$root/locks" "$root/sessions"

lock_file=$root/locks/$role.lock
process_start_ticks() {
  local target_pid=$1 stat_line rest
  [[ -r /proc/$target_pid/stat ]] || return 1
  IFS= read -r stat_line < "/proc/$target_pid/stat" || return 1
  rest=${stat_line##*)}
  local -a fields=()
  read -r -a fields <<< "$rest"
  [[ ${fields[19]-} =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "${fields[19]}"
}

if [[ -e $lock_file || -L $lock_file ]]; then
  [[ -f $lock_file && ! -L $lock_file && -r $lock_file ]] || {
    printf 'ERROR: invalid live lock for controller %s\n' "$role" >&2
    exit 2
  }
  lock_pid='' lock_start='' lock_extra=''
  IFS=' ' read -r lock_pid lock_start lock_extra < "$lock_file" || :
  [[ $lock_pid =~ ^[1-9][0-9]*$ && ( $lock_start =~ ^[0-9]+$ || $lock_start == unknown ) && -z $lock_extra ]] || {
    printf 'ERROR: unreadable live lock for controller %s\n' "$role" >&2
    exit 2
  }
  if [[ -e /proc/$lock_pid ]]; then
    if [[ $lock_start == unknown ]]; then
      printf 'ERROR: controller %s has a live lock (pid %s)\n' "$role" "$lock_pid" >&2
      exit 2
    fi
    current_start=$(process_start_ticks "$lock_pid") || {
      printf 'ERROR: cannot verify controller lock liveness for pid %s\n' "$lock_pid" >&2
      exit 2
    }
    if [[ $current_start == "$lock_start" ]]; then
      printf 'ERROR: controller %s has a live lock (pid %s)\n' "$role" "$lock_pid" >&2
      exit 2
    fi
  fi
  rm -f -- "$lock_file"
fi

if tmux has-session -t "$session_name" 2>/dev/null; then
  printf 'ERROR: tmux session %s already exists\n' "$session_name" >&2
  exit 2
fi

session_tmp=$session_file.tmp.$$
printf '%s\n' "$session_id" > "$session_tmp"
chmod 0600 -- "$session_tmp"
mv -f -- "$session_tmp" "$session_file"

tmux new-session -d -s "$session_name" -c "$cwd" "$pane_command"
printf 'Started pi controller %s in tmux session %s (session id %s).\n' "$role" "$session_name" "$session_id"
