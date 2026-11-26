#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SOURCE=${PI_PIDNS_WRAPPER_SCRIPT:-$SCRIPT_DIR/../pi-pidns-bash}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-pi-pidns.XXXXXX")
cleanup() { rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }

FAKE_BWRAP=$ROOT/bin/bwrap
WRAPPER=$ROOT/pi-pidns-bash
mkdir -p "$ROOT/bin"
cat > "$FAKE_BWRAP" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
: > "${FAKE_BWRAP_ARGV:?}"
for arg in "$@"; do printf '%s\0' "$arg" >> "$FAKE_BWRAP_ARGV"; done
for name in TMUX TMUX_PANE DOCKER_HOST; do
  if [[ -v $name ]]; then printf '%s=present\n' "$name"; else printf '%s=unset\n' "$name"; fi
done > "${FAKE_BWRAP_ENV:?}"
if [[ ${FAKE_BWRAP_START:-1} == 1 ]]; then
  printf '{"child-pid":2}\n' >&3
fi
exit "${FAKE_BWRAP_EXIT:-0}"
SH
chmod 0755 "$FAKE_BWRAP"
sed "s|^BWRAP=/usr/bin/bwrap$|BWRAP=$FAKE_BWRAP|" "$SOURCE" > "$WRAPPER"
chmod 0755 "$WRAPPER"
FAKE_BWRAP_ARGV=$ROOT/bwrap.argv
FAKE_BWRAP_ENV=$ROOT/bwrap.env
export FAKE_BWRAP_ARGV FAKE_BWRAP_ENV

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
  sed "s|^BWRAP=/usr/bin/bwrap$|BWRAP=$missing|" "$SOURCE" > "$ROOT/missing-wrapper"
  chmod 0755 "$ROOT/missing-wrapper"
  if TMUX_TMPDIR="$ROOT/tmux-missing" "$ROOT/missing-wrapper" -c 'printf ran' > "$output" 2>&1; then
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

check_live_isolation() {
  local output=$ROOT/live.out errors=$ROOT/live.err release=$ROOT/child.release
  local child_pid rc
  if [[ ! -x /usr/bin/bwrap ]]; then
    fail 'live PID namespace check requires /usr/bin/bwrap; this is not a user namespace availability skip'
  fi
  if TMUX_TMPDIR="$ROOT/tmux-live" "$SOURCE" -c true > "$output" 2> "$errors"; then
    :
  else
    if grep -Eiq 'operation not permitted|no permissions to create.*namespace|failed to create.*user namespace' "$errors"; then
      echo 'SKIP live PID namespace check: this gate container does not permit user/PID namespaces' >&2
      return 0
    fi
    cat "$errors" >&2
    fail 'the live namespace probe failed for a reason other than namespace availability'
  fi

  mkfifo "$release" || fail 'could not create the live-test child release FIFO'
  /bin/bash -c 'IFS= read -r release_line < "$1"' pidns-child "$release" &
  child_pid=$!
  if (( child_pid <= 2 )); then
    printf 'release\n' > "$release"
    wait "$child_pid" || :
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

case ${PI_PIDNS_TEST_FOCUS:-all} in
  argv) check_wrapper_argv_and_environment ;;
  docker-socket) check_docker_host_socket_is_covered ;;
  missing-bwrap) check_missing_bwrap_refuses ;;
  fail-closed) check_bwrap_setup_failure_is_fail_closed ;;
  command-status) check_command_status_is_preserved ;;
  live) check_live_isolation ;;
  all)
    check_wrapper_argv_and_environment
    check_docker_host_socket_is_covered
    check_missing_bwrap_refuses
    check_bwrap_setup_failure_is_fail_closed
    check_command_status_is_preserved
    check_live_isolation
    ;;
  *) fail "unknown PI_PIDNS_TEST_FOCUS=$PI_PIDNS_TEST_FOCUS" ;;
esac
echo 'PASS pi-pidns: bwrap argv, socket hiding, fail-closed setup, live PID namespace'
