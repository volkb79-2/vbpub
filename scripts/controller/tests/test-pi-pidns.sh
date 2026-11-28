#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if [[ ${PI_PIDNS_WRAPPER_SCRIPT+x} ]]; then
  if [[ ! -f $PI_PIDNS_WRAPPER_SCRIPT || ! -r $PI_PIDNS_WRAPPER_SCRIPT ]]; then
    printf 'ERROR: unreadable override PI_PIDNS_WRAPPER_SCRIPT: %s\n' "$PI_PIDNS_WRAPPER_SCRIPT" >&2
    exit 2
  fi
fi
if [[ ${PI_PIDNS_WORKER_SCRIPT+x} ]]; then
  if [[ ! -f $PI_PIDNS_WORKER_SCRIPT || ! -r $PI_PIDNS_WORKER_SCRIPT ]]; then
    printf 'ERROR: unreadable override PI_PIDNS_WORKER_SCRIPT: %s\n' "$PI_PIDNS_WORKER_SCRIPT" >&2
    exit 2
  fi
fi
SOURCE=${PI_PIDNS_WRAPPER_SCRIPT:-$SCRIPT_DIR/../pi-pidns-bash}
WORKER_SOURCE=${PI_PIDNS_WORKER_SCRIPT:-$SCRIPT_DIR/../pi-pidns-bash-worker.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-pi-pidns.XXXXXX")
cleanup() { rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }

FAKE_BWRAP=$ROOT/bin/bwrap
WRAPPER_DIR=$ROOT/wrapper
WRAPPER=$WRAPPER_DIR/pi-pidns-bash
mkdir -p "$ROOT/bin"
cat > "$FAKE_BWRAP" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
: > "${FAKE_BWRAP_ARGV:?}"
for arg in "$@"; do printf '%s\0' "$arg" >> "$FAKE_BWRAP_ARGV"; done
for name in TMUX TMUX_PANE DOCKER_HOST OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2 \
  OPENDESIGN_API_KEY_FUTURE_ACCOUNT PIDNS_KEPT_VARIABLE; do
  if [[ -v $name ]]; then printf '%s=present\n' "$name"; else printf '%s=unset\n' "$name"; fi
done > "${FAKE_BWRAP_ENV:?}"
if [[ ${PYTHONPATH+x} ]]; then
  printf 'PYTHONPATH=%s\n' "$PYTHONPATH" >> "$FAKE_BWRAP_ENV"
else
  printf 'PYTHONPATH=unset\n' >> "$FAKE_BWRAP_ENV"
fi
if [[ -n ${FAKE_BWRAP_SIGNAL_STATUS:-} ]]; then
  while read -r field value _; do
    if [[ $field == SigIgn: ]]; then
      printf '%s\n' "$value" > "$FAKE_BWRAP_SIGNAL_STATUS"
      break
    fi
  done < "/proc/$$/status"
fi
/usr/bin/env | /usr/bin/grep -E '^(BASH_ENV|ENV|SHELLOPTS|BASHOPTS)=' > "${FAKE_BWRAP_STARTUP_ENV:?}" || :
if declare -F printf >/dev/null; then
  printf 'FUNCTION_printf=present\n' >> "$FAKE_BWRAP_ENV"
else
  printf 'FUNCTION_printf=unset\n' >> "$FAKE_BWRAP_ENV"
fi
if [[ ${FAKE_BWRAP_START:-1} == 1 ]]; then
  printf '{"child-pid":2}\n' >&3
fi
exit "${FAKE_BWRAP_EXIT:-0}"
SH
chmod 0755 "$FAKE_BWRAP"
FAKE_BWRAP_ARGV=$ROOT/bwrap.argv
FAKE_BWRAP_ENV=$ROOT/bwrap.env
FAKE_BWRAP_STARTUP_ENV=$ROOT/bwrap-startup-env
export FAKE_BWRAP_ARGV FAKE_BWRAP_ENV FAKE_BWRAP_STARTUP_ENV
make_wrapper() {
  local directory=$1 bwrap_path=$2
  mkdir -p "$directory"
  if [[ -r $WORKER_SOURCE ]]; then
    cp -- "$SOURCE" "$directory/pi-pidns-bash"
    sed "s|^BWRAP=/usr/bin/bwrap$|BWRAP=$bwrap_path|" "$WORKER_SOURCE" \
      > "$directory/pi-pidns-bash-worker.sh"
  else
    fail "worker source is unreadable: $WORKER_SOURCE"
  fi
  chmod 0755 "$directory/pi-pidns-bash"
}
make_wrapper "$WRAPPER_DIR" "$FAKE_BWRAP"

argv_is() {
  local expected_name=$1
  shift
  local -a actual=() expected=("$@")
  [[ -s $FAKE_BWRAP_ARGV ]] || fail "$expected_name: fake bwrap received no argv"
  mapfile -d '' -t actual < "$FAKE_BWRAP_ARGV"
  [[ ${#actual[@]} == "${#expected[@]}" ]] \
    || fail "$expected_name: bwrap argv length was ${#actual[@]}, expected ${#expected[@]} (${actual[*]})"
  local i
  for (( i = 0; i < ${#expected[@]}; i++ )); do
    [[ ${actual[i]} == "${expected[i]}" ]] \
      || fail "$expected_name: bwrap argv element $i was '${actual[i]}', expected '${expected[i]}'"
  done
}

expected_base() {
  local tmux_dir=$1
  EXPECTED=(
    --unshare-user --unshare-pid --die-with-parent
    --dev-bind / /
    --perms 0700 --tmpfs "$tmux_dir"
    --perms 0700 --tmpfs /run/docker-api
  )
  if [[ -S /var/run/docker.sock ]]; then
    local resolved
    resolved=$(realpath -e -- /var/run/docker.sock) || fail 'cannot resolve the default Docker socket in the test host'
    case $resolved in
      /run/docker-api | /run/docker-api/*) ;;
      *) EXPECTED+=(--bind /dev/null "$resolved") ;;
    esac
  fi
}

check_wrapper_argv_and_environment() {
  local tmux_dir=$ROOT/tmuxdir/tmux-$UID command_text="printf ran > $ROOT/command-ran"
  rm -f -- "$FAKE_BWRAP_ARGV" "$ROOT/command-ran"
  env TMUX=outer-session TMUX_PANE=%4 DOCKER_HOST=tcp://outer.example \
    TMUX_TMPDIR="$ROOT/tmuxdir" FAKE_BWRAP_START=1 FAKE_BWRAP_EXIT=0 \
    "$WRAPPER" -c "$command_text" || fail 'fake bwrap success did not return success'
  expected_base "$tmux_dir"
  EXPECTED+=(--json-status-fd 3 -- /bin/bash -c 'exec 3>&-; exec /bin/bash "$@"' pi-pidns-shell -c "$command_text")
  argv_is 'complete isolation argv' "${EXPECTED[@]}"
  ! grep -qF -- '--proc' "$FAKE_BWRAP_ARGV" || fail 'the wrapper tried to mount /proc'
  [[ $(stat -c '%a' -- "$tmux_dir") == 700 ]] || fail 'the host tmux socket directory was not prepared with mode 0700'
  for name in TMUX TMUX_PANE DOCKER_HOST; do
    grep -qF "$name=unset" "$FAKE_BWRAP_ENV" || fail "$name remained set for the isolated shell"
  done
  [[ ! -e $ROOT/command-ran ]] || fail 'fake bwrap unexpectedly ran the shell command'
}

check_docker_host_socket_is_covered() {
  local socket=$ROOT/docker-api.sock tmux_dir=$ROOT/tmux-docker/tmux-$UID
  python3 - "$socket" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_UNIX)
sock.bind(sys.argv[1])
sock.close()
PY
  [[ -S $socket ]] || fail 'the test did not create its own Unix socket fixture'
  rm -f -- "$FAKE_BWRAP_ARGV"
  env DOCKER_HOST="unix://$socket" TMUX_TMPDIR="$ROOT/tmux-docker" \
    FAKE_BWRAP_START=1 FAKE_BWRAP_EXIT=0 "$WRAPPER" -c true \
    || fail 'the wrapper refused a coverable DOCKER_HOST Unix socket'
  expected_base "$tmux_dir"
  EXPECTED+=(--bind /dev/null "$socket" --json-status-fd 3 -- /bin/bash -c \
    'exec 3>&-; exec /bin/bash "$@"' pi-pidns-shell -c true)
  argv_is 'DOCKER_HOST socket cover' "${EXPECTED[@]}"
}

check_missing_bwrap_refuses() {
  local missing=$ROOT/missing-bwrap output=$ROOT/missing.out
  make_wrapper "$ROOT/missing-wrapper" "$missing"
  if TMUX_TMPDIR="$ROOT/tmux-missing" "$ROOT/missing-wrapper/pi-pidns-bash" -c 'printf ran' > "$output" 2>&1; then
    fail 'a missing bwrap executable let the shell wrapper run'
  else
    local rc=$?
    [[ $rc == 125 ]] || fail "a missing bwrap executable returned $rc instead of 125"
  fi
  contains "$output" 'command was not run (exit 125)' 'missing bwrap reports a fail-closed refusal'
}

check_bwrap_setup_failure_is_fail_closed() {
  local output=$ROOT/setup-failure.out
  rm -f -- "$ROOT/command-ran"
  if env TMUX_TMPDIR="$ROOT/tmux-fail" FAKE_BWRAP_START=0 FAKE_BWRAP_EXIT=1 \
    "$WRAPPER" -c "printf ran > $ROOT/command-ran" > "$output" 2>&1; then
    fail 'bwrap setup failure returned success'
  else
    local rc=$?
    [[ $rc == 125 ]] || fail "bwrap setup failure returned $rc instead of 125"
  fi
  [[ ! -e $ROOT/command-ran ]] || fail 'a command ran after bwrap setup failed'
  [[ $(grep -cF 'pi-pidns-bash:' "$output") == 1 ]] \
    || fail 'bwrap setup failure did not produce exactly one clear wrapper message'
}

check_command_status_is_preserved() {
  if env TMUX_TMPDIR="$ROOT/tmux-status" FAKE_BWRAP_START=1 FAKE_BWRAP_EXIT=37 \
    "$WRAPPER" -c true > "$ROOT/status.out" 2>&1; then
    fail 'the wrapper replaced a non-zero shell exit with success'
  else
    local rc=$?
    [[ $rc == 37 ]] || fail "the wrapper changed a command exit of 37 to $rc"
  fi
}

check_startup_environment_is_sanitized() {
  local bash_env=$ROOT/injected-bash-env function_marker=$ROOT/function-ran
  local bash_env_marker=$ROOT/bash-env-ran output=$ROOT/startup-env.out
  cat > "$bash_env" <<'SH'
: > "${PIDNS_BASH_ENV_MARKER:?}"
SH
  rm -f -- "$function_marker" "$bash_env_marker" "$FAKE_BWRAP_STARTUP_ENV"
  # shellcheck disable=SC2016 # Literal function source imported by Bash through env.
  if env BASH_ENV="$bash_env" PIDNS_BASH_ENV_MARKER="$bash_env_marker" \
    PIDNS_FUNCTION_MARKER="$function_marker" \
    SHELLOPTS=braceexpand BASHOPTS=checkwinsize \
    'BASH_FUNC_printf%%=() { /usr/bin/touch -- "$PIDNS_FUNCTION_MARKER"; builtin printf "$@"; }' \
    TMUX_TMPDIR="$ROOT/tmux-startup-env" FAKE_BWRAP_START=0 FAKE_BWRAP_EXIT=1 \
    "$WRAPPER" -c true > "$output" 2>&1; then
    fail 'a wrapper setup failure unexpectedly returned success'
  else
    local rc=$?
    [[ $rc == 125 ]] || fail "sanitized wrapper setup failure returned $rc instead of 125"
  fi
  [[ ! -e $bash_env_marker ]] || fail 'BASH_ENV ran outside the PID namespace boundary'
  [[ ! -e $function_marker ]] || fail 'the exported printf function ran outside the PID namespace boundary'
  [[ ! -s $FAKE_BWRAP_STARTUP_ENV ]] || fail 'Bash startup variables reached the bwrap launcher environment'
  contains "$FAKE_BWRAP_ENV" 'FUNCTION_printf=unset' \
    'the exported printf function was not imported by the bwrap launcher'
}

check_python_startup_environment_isolated() {
  local pythonpath_dir=$ROOT/pythonpath cwd_dir=$ROOT/python-cwd
  local pythonpath_marker=$ROOT/pythonpath-sitecustomize-ran
  local cwd_marker=$ROOT/cwd-sitecustomize-ran
  local output=$ROOT/python-startup-env.out rc
  mkdir -p "$pythonpath_dir" "$cwd_dir"
  cat > "$pythonpath_dir/sitecustomize.py" <<'PY'
import os
from pathlib import Path

Path(os.environ["PIDNS_SITE_MARKER"]).write_text("ran", encoding="utf-8")
PY
  cp -- "$pythonpath_dir/sitecustomize.py" "$cwd_dir/sitecustomize.py"
  rm -f -- "$pythonpath_marker" "$cwd_marker"

  if (cd -- "$cwd_dir" && env PYTHONPATH="$pythonpath_dir:" \
    PIDNS_SITE_MARKER="$pythonpath_marker" TMUX_TMPDIR="$ROOT/tmux-pythonpath" \
    FAKE_BWRAP_START=0 FAKE_BWRAP_EXIT=1 "$WRAPPER" -c true > "$output" 2>&1); then
    fail 'fake bwrap setup failure with PYTHONPATH unexpectedly returned success'
  else
    rc=$?
    [[ $rc == 125 ]] || fail "PYTHONPATH startup probe returned $rc instead of 125"
  fi
  [[ ! -e $pythonpath_marker ]] || fail 'PYTHONPATH sitecustomize ran before the namespace'
  contains "$FAKE_BWRAP_ENV" "PYTHONPATH=$pythonpath_dir:" \
    'PYTHONPATH was not passed through to the bwrap command environment'

  if (cd -- "$cwd_dir" && env PYTHONPATH=: PIDNS_SITE_MARKER="$cwd_marker" \
    TMUX_TMPDIR="$ROOT/tmux-python-cwd" FAKE_BWRAP_START=0 FAKE_BWRAP_EXIT=1 \
    "$WRAPPER" -c true > "$output" 2>&1); then
    fail 'fake bwrap setup failure with an empty PYTHONPATH entry unexpectedly returned success'
  else
    rc=$?
    [[ $rc == 125 ]] || fail "empty-entry startup probe returned $rc instead of 125"
  fi
  [[ ! -e $cwd_marker ]] || fail 'cwd sitecustomize from an empty PYTHONPATH entry ran before the namespace'
  contains "$FAKE_BWRAP_ENV" 'PYTHONPATH=:' \
    'an empty PYTHONPATH entry was not passed through to the bwrap command environment'
}

check_model_tool_environment_has_no_route_keys() {
  local tmux_dir=$ROOT/tmux-key-strip
  rm -f -- "$FAKE_BWRAP_ARGV" "$FAKE_BWRAP_ENV"
  # Every route key the shell backend is started with, plus one account name the
  # launcher does not know: the strip must be by prefix, not by a fixed list.
  env TMUX_TMPDIR="$ROOT/tmux-key-strip" \
    OPENDESIGN_API_KEY_VOLKER='pidns-test-volker-marker' \
    OPENDESIGN_API_KEY_GITHUB2='pidns-test-github2-marker' \
    OPENDESIGN_API_KEY_FUTURE_ACCOUNT='pidns-test-future-marker' \
    PIDNS_KEPT_VARIABLE='kept-marker' \
    FAKE_BWRAP_START=1 FAKE_BWRAP_EXIT=0 \
    "$WRAPPER" -c true > "$ROOT/key-strip.out" 2>&1 \
    || fail 'the key-strip probe did not reach the bwrap launcher'
  for name in OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2 OPENDESIGN_API_KEY_FUTURE_ACCOUNT; do
    grep -qF "$name=unset" "$FAKE_BWRAP_ENV" \
      || fail "$name reached the model-facing shell environment"
  done
  # Only the route keys go: the shell keeps everything else it was started with.
  if ! grep -qF 'PIDNS_KEPT_VARIABLE=present' "$FAKE_BWRAP_ENV"; then
    fail 'the key strip removed an unrelated variable from the shell environment'
  fi
  ! grep -qF 'pidns-test-volker-marker' "$FAKE_BWRAP_ARGV" || fail 'a route key appeared in the bwrap argv'
}

check_exec_signal_dispositions_restored() {
  local status_file=$ROOT/fake-bwrap-sigign
  local ignored_mask sig_ign
  rm -f -- "$status_file"
  env TMUX_TMPDIR="$ROOT/tmux-signal-dispositions" \
    FAKE_BWRAP_SIGNAL_STATUS="$status_file" FAKE_BWRAP_START=1 FAKE_BWRAP_EXIT=0 \
    "$WRAPPER" -c true > "$ROOT/signal-dispositions.out" 2>&1 \
    || fail 'fake bwrap signal-disposition probe failed'
  [[ -s $status_file ]] || fail 'fake bwrap did not record SigIgn from its child status'
  sig_ign=$(<"$status_file")
  ignored_mask=$((16#$sig_ign))
  # /proc reports bit (signal number - 1): SIGPIPE is bit 12, SIGXFSZ bit 24.
  if (( ignored_mask & 0x1000 )); then
    fail 'SIGPIPE was still ignored after the Python bootstrap execve'
  fi
  if (( ignored_mask & 0x1000000 )); then
    fail 'SIGXFSZ was still ignored after the Python bootstrap execve'
  fi
}

check_live_isolation() {
  local output=$ROOT/live.out errors=$ROOT/live.err release=$ROOT/child.release
  local child_pid rc
  if [[ ! -x /usr/bin/bwrap ]]; then
    live_state='live PID namespace check SKIPPED'
    echo 'SKIP live PID namespace check: this gate image has no /usr/bin/bwrap, so isolation is checked only against the fake bwrap' >&2
    return 0
  fi
  if TMUX_TMPDIR="$ROOT/tmux-live" "$SOURCE" -c true > "$output" 2> "$errors"; then
    :
  else
    if grep -Eiq 'operation not permitted|no permissions to create.*namespace|failed to create.*user namespace' "$errors"; then
      live_state='live PID namespace check SKIPPED'
      echo 'SKIP live PID namespace check: this gate container does not permit user/PID namespaces' >&2
      return 0
    fi
    cat "$errors" >&2
    fail 'the live namespace probe failed for a reason other than namespace availability'
  fi

  local owner_file=$ROOT/outer-owner.lock owner_pid owner_ticks outer_ticks
  local owner_command owner_child owner_fd poll
  local release=$ROOT/owner-release
  mkfifo "$release" || fail 'could not create the owner-record release FIFO'
  exec {owner_fd}<>"$release" || fail 'could not open the owner-record release FIFO read-write'
  owner_command=". '$SCRIPT_DIR/../lib.sh'; CONTROLLER_RUN_LOCK_PATH='$owner_file'; controller_run_record_owner; IFS= read -r -t 60 release_line < '$release'"
  TMUX_TMPDIR="$ROOT/tmux-owner" "$SOURCE" -c "$owner_command" > "$ROOT/owner-record.out" 2> "$errors" &
  owner_child=$!
  for ((poll = 0; poll < 600; poll++)); do
    [[ -s $owner_file ]] && break
    sleep 0.1
  done
  if [[ ! -s $owner_file ]]; then
    printf 'release\n' >&"$owner_fd"
    wait "$owner_child" || :
    fail 'the live PID namespace owner record did not appear within the bounded wait'
  fi
  owner_pid=$(sed -n 's/.*pid=\([0-9]*\).*/\1/p' "$owner_file")
  owner_ticks=$(sed -n 's/.*pid_start_ticks=\([0-9]*\).*/\1/p' "$owner_file")
  if [[ ! $owner_pid =~ ^[0-9]+$ || $owner_pid -le 2 || ! -r /proc/$owner_pid/stat ]]; then
    printf 'release\n' >&"$owner_fd"
    wait "$owner_child" || :
    fail 'owner record written inside pi PID namespace did not hold an outer procfs pid'
  fi
  outer_ticks=$(sed 's/^[^)]*) //' "/proc/$owner_pid/stat" | awk '{print $20}')
  if [[ $owner_ticks != "$outer_ticks" ]]; then
    printf 'release\n' >&"$owner_fd"
    wait "$owner_child" || :
    fail 'owner record start ticks did not match the outer procfs entry'
  fi
  printf 'release\n' >&"$owner_fd"
  if wait "$owner_child"; then
    :
  else
    rc=$?
    fail "owner-record wrapper exited with status $rc after release"
  fi
  exec {owner_fd}>&-

  mkfifo "$release" || fail 'could not create the live-test child release FIFO'
  /bin/bash -c 'IFS= read -r release_line < "$1"' pidns-child "$release" &
  child_pid=$!
  if (( child_pid <= 2 )); then
    printf 'release\n' > "$release"
    wait "$child_pid" || :
    live_state='live PID namespace check SKIPPED'
    echo "SKIP live PID namespace check: test child pid $child_pid could alias an in-namespace pid" >&2
    return 0
  fi
  if TMUX_TMPDIR="$ROOT/tmux-live" "$SOURCE" -c "kill -0 $child_pid" > "$output" 2> "$errors"; then
    rc=0
  else
    rc=$?
  fi
  # Release and reap only the child this test started. The probe above uses
  # signal 0; no real signal is sent to any process.
  printf 'release\n' > "$release"
  wait "$child_pid" || fail 'the live isolation test child did not finish after its FIFO was released'
  [[ $rc != 0 ]] || fail 'kill -0 from the isolated shell reached a test child outside its PID namespace'
  contains "$errors" 'No such process' 'the outer test pid is not addressable inside the namespace'
}

live_state='live PID namespace'
case ${PI_PIDNS_TEST_FOCUS:-all} in
  argv) check_wrapper_argv_and_environment ;;
  docker-socket) check_docker_host_socket_is_covered ;;
  missing-bwrap) check_missing_bwrap_refuses ;;
  fail-closed) check_bwrap_setup_failure_is_fail_closed ;;
  command-status) check_command_status_is_preserved ;;
  startup-env)
    check_startup_environment_is_sanitized
    check_python_startup_environment_isolated
    ;;
  key-strip) check_model_tool_environment_has_no_route_keys ;;
  signals) check_exec_signal_dispositions_restored ;;
  live) check_live_isolation ;;
  all)
    check_wrapper_argv_and_environment
    check_docker_host_socket_is_covered
    check_missing_bwrap_refuses
    check_bwrap_setup_failure_is_fail_closed
    check_command_status_is_preserved
    check_startup_environment_is_sanitized
    check_python_startup_environment_isolated
    check_model_tool_environment_has_no_route_keys
    check_exec_signal_dispositions_restored
    check_live_isolation
    ;;
  *) fail "unknown PI_PIDNS_TEST_FOCUS=$PI_PIDNS_TEST_FOCUS" ;;
esac
echo "PASS pi-pidns: bwrap argv, socket hiding, fail-closed setup, $live_state"
