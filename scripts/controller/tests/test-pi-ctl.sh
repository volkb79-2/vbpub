#!/usr/bin/env bash
set -euo pipefail

# The launcher defaults are under test here; ambient operator routing must not
# change the expected default-provider case below.
unset PI_CTL_PROVIDER
unset PI_CTL_TEST_ROOT

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CONTROLLER_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-pi-ctl.XXXXXX")
cleanup() { rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
contains_arg() {
  local wanted=$1 argument
  shift
  for argument in "$@"; do [[ $argument == "$wanted" ]] && return 0; done
  fail "expected argv element: $wanted"
}
command_contains_word() {
  local wanted=$1 command=$2
  [[ " $command " == *" $wanted "* ]]
}

BIN=$ROOT/bin
HOME_DIR=$ROOT/home
STATE_ROOT=$ROOT/state
WORK_DIR=$ROOT/work
PRIME=$ROOT/PRIME-hostobs.md
mkdir -p "$BIN" "$HOME_DIR" "$WORK_DIR"
chmod 0700 "$ROOT" "$BIN" "$HOME_DIR" "$WORK_DIR"
printf 'Role rule marker for launcher test.\n' > "$PRIME"
chmod 0600 "$PRIME"
cat > "$HOME_DIR/.env.ai" <<'ENVFILE'
OPENDESIGN_API_KEY_VOLKER='test-volker-secret-marker'
OPENDESIGN_API_KEY_GITHUB2='test-github2-secret-marker'
ENVFILE
chmod 0600 "$HOME_DIR/.env.ai"
EMPTY_BIN=$ROOT/empty-bin
mkdir -p "$EMPTY_BIN"

cat > "$BIN/tmux" <<'TMUX'
#!/usr/bin/env bash
set -uo pipefail
: "${PI_CTL_TEST_TMUX_ARGV:?}"
: "${PI_CTL_TEST_TMUX_KEYS:?}"
: "${PI_CTL_TEST_TMUX_STATE:?}"
printf '%s\0' "$@" >> "$PI_CTL_TEST_TMUX_ARGV"
{
  if [[ -v OPENDESIGN_API_KEY_VOLKER ]]; then printf 'VOLKER=present\n'; else printf 'VOLKER=unset\n'; fi
  if [[ -v OPENDESIGN_API_KEY_GITHUB2 ]]; then printf 'GITHUB2=present\n'; else printf 'GITHUB2=unset\n'; fi
} > "$PI_CTL_TEST_TMUX_KEYS"
case ${1-} in
  has-session) exit "${PI_CTL_TEST_HAS_SESSION_RC:-1}" ;;
  new-session)
    : > "$PI_CTL_TEST_TMUX_STATE"
    pane_command=${!#}
    if [[ ${PI_CTL_TEST_READY_MODE:-ready} == ready ]]; then
      [[ $pane_command =~ PI_CTL_READY_FILE=([^[:space:]]+) ]] || exit 3
      ready_file=${BASH_REMATCH[1]}
      [[ $pane_command =~ PI_CTL_READY_TOKEN=([^[:space:]]+) ]] || exit 3
      ready_token=${BASH_REMATCH[1]}
      mkdir -p "$(dirname -- "$ready_file")"
      printf '%s\n' "$ready_token" > "$ready_file"
    fi
    exit 0
    ;;
  *) exit 2 ;;
esac
TMUX
cat > "$BIN/pi" <<'PI'
#!/usr/bin/env bash
set -uo pipefail
: "${PI_CTL_TEST_PI_ARGV:?}"
: "${PI_CTL_TEST_PI_KEYS:?}"
: "${PI_CTL_LOCK_FD:?pi did not inherit the controller lock descriptor}"
: "${PI_CTL_TEST_LOCK_PATH:?}"
[[ $(readlink -- "/proc/self/fd/$PI_CTL_LOCK_FD") == "$PI_CTL_TEST_LOCK_PATH" ]] || exit 41
grep -Eq '^lock:[[:space:]]*[0-9]+:[[:space:]]+FLOCK[[:space:]]+ADVISORY[[:space:]]+WRITE([[:space:]]|$)' \
  "/proc/self/fdinfo/$PI_CTL_LOCK_FD" || exit 42
printf '%s\0' "$@" > "$PI_CTL_TEST_PI_ARGV"
{
  if [[ -v OPENDESIGN_API_KEY_VOLKER ]]; then printf 'VOLKER=present\n'; else printf 'VOLKER=unset\n'; fi
  if [[ -v OPENDESIGN_API_KEY_GITHUB2 ]]; then printf 'GITHUB2=present\n'; else printf 'GITHUB2=unset\n'; fi
} > "$PI_CTL_TEST_PI_KEYS"
PI
chmod 0700 "$BIN/tmux" "$BIN/pi"
PATH_VALUE=$BIN:/usr/bin:/bin
TMUX_ARGV=$ROOT/tmux.argv
PI_ARGV=$ROOT/pi.argv
PI_KEYS=$ROOT/pi.keys
TMUX_STATE=$ROOT/tmux.state

run_launcher() {
  local env_name
  local state_root=${PI_CTL_TEST_ROOT:-$STATE_ROOT}
  local -a configured=()
  local -a routing=()
  if [[ -v PI_CTL_PROVIDER ]]; then routing+=("PI_CTL_PROVIDER=$PI_CTL_PROVIDER"); fi
  for env_name in PI_CTL_POLL_MS PI_CTL_MAX_MESSAGE_BYTES PI_CTL_MAX_SEND_BYTES \
    PI_CTL_SEND_LIMIT PI_CTL_INBOUND_LIMIT PI_CTL_LOOP_WINDOW_SECONDS \
    PI_CTL_COMPACT_AT PI_CTL_COMPACT_BACKOFF_SECONDS PI_CTL_STARTUP_TIMEOUT_SECONDS; do
    if [[ -v $env_name ]]; then configured+=("$env_name=${!env_name}"); fi
  done
  env -i HOME="$HOME_DIR" PATH="$PATH_VALUE" PI_CTL_ROOT="$state_root" \
    PI_CTL_CWD="$WORK_DIR" PI_CTL_PRIME="$PRIME" PI_CTL_BIN="$BIN/pi" \
    PI_CTL_SESSION_DIR="$ROOT/sessions" PI_CTL_TEST_TMUX_ARGV="$TMUX_ARGV" \
    PI_CTL_TEST_TMUX_KEYS="$ROOT/tmux.keys" PI_CTL_TEST_TMUX_STATE="$TMUX_STATE" \
    PI_CTL_TEST_READY_MODE="${PI_CTL_TEST_READY_MODE:-ready}" \
    PI_CTL_TEST_HAS_SESSION_RC="${PI_CTL_TEST_HAS_SESSION_RC:-1}" \
    OPENDESIGN_API_KEY_VOLKER='ambient-volker-secret-marker' \
    OPENDESIGN_API_KEY_GITHUB2='ambient-github2-secret-marker' \
    "${configured[@]}" "${routing[@]}" \
    "$CONTROLLER_DIR/pi-ctl-launch.sh" "$@"
}

print_output=$(run_launcher --print-command hostobs)
[[ $print_output == *"opendesign_volker"* && $print_output == *"deepseek-v4.1-flash-eco"* && $print_output == *"max"* ]] \
  || fail '--print-command did not show the resolved defaults'
[[ $print_output == *"$STATE_ROOT/locks/hostobs.lock"* && $print_output == *'flock held by the pane until pi exits'* ]] \
  || fail '--print-command did not describe the persistent pane lock'
[[ ! -e $TMUX_ARGV && ! -e $STATE_ROOT ]] || fail '--print-command started or wrote launcher state'
[[ $print_output != *'test-volker-secret-marker'* && $print_output != *'test-github2-secret-marker'* ]] \
  || fail '--print-command exposed a key'

symlink_target=$ROOT/symlink-target
root_link=$ROOT/state-link
mkdir -m 0755 "$symlink_target"
ln -s "$symlink_target" "$root_link"
: > "$TMUX_ARGV"
if PI_CTL_TEST_ROOT="$root_link/" run_launcher --print-command hostobs > "$ROOT/root-link.out" 2>&1; then
  fail 'launcher accepted a symlinked root with a trailing slash'
fi
grep -Fq 'PI_CTL_ROOT contains a symlink path component' "$ROOT/root-link.out" \
  || fail 'launcher did not identify the symlinked root refusal'
[[ $(stat -c '%a' "$symlink_target") == 755 && ! -e $symlink_target/locks ]] \
  || fail 'symlinked root refusal changed or created target state'
[[ ! -s $TMUX_ARGV ]] || fail 'launcher called tmux for a symlinked root'

alias_target=$ROOT/alias-target
mkdir -p "$alias_target/child"
alias_path=$ROOT/state-alias
ln -s "$alias_target/child" "$alias_path"
normalized_root=$alias_path/../normalized-root
: > "$TMUX_ARGV"
if PI_CTL_TEST_ROOT="$normalized_root" run_launcher --print-command hostobs > "$ROOT/normalized-link.out" 2>&1; then
  fail 'launcher accepted a symlink component removed by path normalization'
fi
grep -Fq 'PI_CTL_ROOT contains a symlink path component' "$ROOT/normalized-link.out" \
  || fail 'launcher did not inspect path components before normalization'
[[ ! -e $alias_target/normalized-root && ! -s $TMUX_ARGV ]] \
  || fail 'normalized symlink refusal created state or called tmux'

if env -i HOME="$HOME_DIR" PATH="$EMPTY_BIN" /bin/bash "$CONTROLLER_DIR/pi-ctl-launch.sh" --print-command hostobs \
  > "$ROOT/no-flock-launch.out" 2>&1; then
  fail 'launcher accepted a missing flock command'
fi
grep -Fq 'flock is required' "$ROOT/no-flock-launch.out" || fail 'launcher did not explain the flock requirement'

unset PI_CTL_TEST_HAS_SESSION_RC
run_launcher hostobs > "$ROOT/launch.out"
[[ -s $TMUX_ARGV ]] || fail 'launcher did not call the fake tmux'
mapfile -d '' -t tmux_args < "$TMUX_ARGV"
contains_arg 'has-session' "${tmux_args[@]}"
contains_arg 'new-session' "${tmux_args[@]}"
contains_arg 'vbpub-hostobs' "${tmux_args[@]}"
pane_command=${tmux_args[-1]}
command_contains_word 'opendesign_volker' "$pane_command" \
  || fail 'default provider was not an exact pane-command argument'
command_contains_word 'deepseek-v4.1-flash-eco' "$pane_command" \
  || fail 'default model was not an exact pane-command argument'
command_contains_word 'max' "$pane_command" \
  || fail 'default thinking level was not an exact pane-command argument'
[[ $(<"$STATE_ROOT/sessions/hostobs.session") == 'vbpub-hostobs' ]] || fail 'launcher did not persist the per-role pi session id'
[[ $(stat -c '%a' "$STATE_ROOT") == 700 && $(stat -c '%a' "$STATE_ROOT/sessions") == 700 ]] \
  || fail 'launcher state directories were not private'
[[ $(<"$ROOT/tmux.keys") == $'VOLKER=unset\nGITHUB2=unset' ]] || fail 'launcher forwarded an ambient API key to tmux'
if grep -aFq 'test-volker-secret-marker' "$TMUX_ARGV" "$ROOT/launch.out"; then fail 'key appeared in tmux argv or launcher output'; fi
if grep -aFq 'test-github2-secret-marker' "$TMUX_ARGV" "$ROOT/launch.out"; then fail 'key appeared in tmux argv or launcher output'; fi
if grep -aFq 'ambient-volker-secret-marker' "$TMUX_ARGV" "$ROOT/launch.out"; then fail 'ambient volker key appeared in tmux argv or launcher output'; fi
if grep -aFq 'ambient-github2-secret-marker' "$TMUX_ARGV" "$ROOT/launch.out"; then fail 'ambient github2 key appeared in tmux argv or launcher output'; fi

: > "$TMUX_ARGV"
PI_CTL_SEND_LIMIT=3 run_launcher hostobs > "$ROOT/limit.out"
mapfile -d '' -t tmux_args < "$TMUX_ARGV"
[[ ${tmux_args[*]} == *'PI_CTL_SEND_LIMIT=3'* ]] || fail 'launcher did not pass a safe loop-limit override into the pane'

: > "$TMUX_ARGV"
PI_CTL_PROVIDER=opendesign_github2 run_launcher hostobs > "$ROOT/github2.out"
mapfile -d '' -t tmux_args < "$TMUX_ARGV"
pane_command=${tmux_args[-1]}
command_contains_word 'opendesign_github2' "$pane_command" \
  || fail 'github2 provider was not an exact pane-command argument'
if grep -aFq 'test-volker-secret-marker' "$TMUX_ARGV" "$ROOT/github2.out"; then fail 'volker key appeared in tmux argv or output'; fi
if grep -aFq 'test-github2-secret-marker' "$TMUX_ARGV" "$ROOT/github2.out"; then fail 'github2 key appeared in tmux argv or output'; fi

: > "$TMUX_ARGV"
run_launcher --fresh hostobs > "$ROOT/fresh.out"
fresh_id=$(<"$STATE_ROOT/sessions/hostobs.session")
[[ $fresh_id =~ ^vbpub-hostobs-[A-Za-z0-9._-]+$ ]] || fail '--fresh did not select a new pi session id'
: > "$TMUX_ARGV"
run_launcher hostobs > "$ROOT/resume.out"
mapfile -d '' -t tmux_args < "$TMUX_ARGV"
[[ ${tmux_args[*]} == *"$fresh_id"* ]] || fail 'normal launch did not resume the selected fresh session id'

: > "$TMUX_ARGV"
if PI_CTL_TEST_READY_MODE=fail PI_CTL_STARTUP_TIMEOUT_SECONDS=1 run_launcher --fresh hostobs > "$ROOT/startup-failed.out" 2>&1; then
  fail 'launcher reported success before extension session_start readiness'
fi
[[ $(<"$STATE_ROOT/sessions/hostobs.session") == "$fresh_id" ]] \
  || fail 'failed pane startup replaced the last resumable session id'

: > "$TMUX_ARGV"
if PI_CTL_TEST_HAS_SESSION_RC=0 run_launcher hostobs > "$ROOT/existing.out" 2>&1; then fail 'existing tmux session was not refused'; fi
if grep -aFq 'test-volker-secret-marker' "$ROOT/existing.out"; then fail 'key appeared in existing-session refusal'; fi

mkdir -p "$STATE_ROOT/locks"
: > "$TMUX_ARGV"

run_pane() {
  local provider=$1
  local role=${2:-hostobs}
  local state_root=${3:-$STATE_ROOT}
  env -i HOME="$HOME_DIR" PATH="$PATH_VALUE" PI_CTL_TEST_PI_ARGV="$PI_ARGV" \
    PI_CTL_TEST_PI_KEYS="$PI_KEYS" PI_CTL_TEST_LOCK_PATH="$state_root/locks/$role.lock" \
    "$CONTROLLER_DIR/pi-ctl-pane.sh" "$role" "$provider" deepseek-v4.1-flash-eco max \
      "$fresh_id" "$state_root" "$PRIME" "$BIN/pi" "$ROOT/sessions" \
      "$CONTROLLER_DIR/pi-pidns-extension.js" "$CONTROLLER_DIR/pi-ctl/extension.mjs" \
      "$CONTROLLER_DIR/pi-pidns-bash" "vbpub-$role"
}

if env -i HOME="$HOME_DIR" PATH="$EMPTY_BIN" /bin/bash "$CONTROLLER_DIR/pi-ctl-pane.sh" \
  hostobs opendesign_volker deepseek-v4.1-flash-eco max "$fresh_id" "$STATE_ROOT" "$PRIME" "$BIN/pi" \
  "$ROOT/sessions" "$CONTROLLER_DIR/pi-pidns-extension.js" "$CONTROLLER_DIR/pi-ctl/extension.mjs" \
  "$CONTROLLER_DIR/pi-pidns-bash" vbpub-hostobs > "$ROOT/no-flock-pane.out" 2>&1; then
  fail 'pane accepted a missing flock command'
fi
grep -Fq 'flock is required' "$ROOT/no-flock-pane.out" || fail 'pane did not explain the flock requirement'

run_pane opendesign_volker > "$ROOT/pane.out" 2>&1
[[ $(<"$PI_KEYS") == $'VOLKER=present\nGITHUB2=unset' ]] || fail 'volker launch did not export only the volker key'
mapfile -d '' -t pi_args < "$PI_ARGV"
contains_arg '--no-extensions' "${pi_args[@]}"
contains_arg '--no-approve' "${pi_args[@]}"
contains_arg "$CONTROLLER_DIR/pi-pidns-extension.js" "${pi_args[@]}"
contains_arg "$CONTROLLER_DIR/pi-ctl/extension.mjs" "${pi_args[@]}"
contains_arg 'opendesign_volker' "${pi_args[@]}"
contains_arg 'deepseek-v4.1-flash-eco' "${pi_args[@]}"
contains_arg 'max' "${pi_args[@]}"
contains_arg "$fresh_id" "${pi_args[@]}"
if grep -aFq 'test-volker-secret-marker' "$PI_ARGV" "$ROOT/pane.out"; then fail 'volker key appeared in pi argv or output'; fi
if grep -aFq 'test-github2-secret-marker' "$PI_ARGV" "$ROOT/pane.out"; then fail 'github2 key appeared in pi argv or output'; fi

lock_file=$STATE_ROOT/locks/hostobs.lock
lock_inode=$(stat -c '%d:%i' "$lock_file")
[[ $(stat -c '%a' "$lock_file") == 600 ]] || fail 'pane lock file is not private'
: > "$PI_ARGV"
ready_fifo=$ROOT/lock-ready.fifo
release_fifo=$ROOT/lock-release.fifo
mkfifo "$ready_fifo" "$release_fifo"
(
  exec {holder_fd}>>"$lock_file"
  if flock -n "$holder_fd"; then holder_result=held; else holder_result=failed; fi
  printf '%s\n' "$holder_result" > "$ready_fifo"
  IFS= read -r release_token < "$release_fifo"
  [[ $holder_result == held && $release_token == release ]]
) &
holder_pid=$!
IFS= read -r holder_result < "$ready_fifo"
: > "$TMUX_ARGV"
if run_launcher hostobs > "$ROOT/lock.out" 2>&1; then fail 'launcher accepted a live kernel session lock'; fi
grep -Fq 'already has a live session lock' "$ROOT/lock.out" || fail 'launcher did not explain the live lock refusal'
[[ ! -s $TMUX_ARGV ]] || fail 'launcher reached tmux despite a live flock'
if run_pane opendesign_github2 > "$ROOT/contended-pane.out" 2>&1; then
  contention_status=0
else
  contention_status=$?
fi
printf 'release\n' > "$release_fifo"
if wait "$holder_pid"; then holder_status=0; else holder_status=$?; fi
[[ $holder_result == held && $holder_status == 0 ]] || fail 'test-owned holder did not acquire and release the flock normally'
[[ $contention_status != 0 ]] || fail 'pane started pi while another process held the role flock'
grep -Fq 'already has a live session lock' "$ROOT/contended-pane.out" \
  || fail 'pane did not explain the live flock refusal'
[[ ! -s $PI_ARGV ]] || fail 'pane reached pi despite a live flock'
[[ $(stat -c '%d:%i' "$lock_file") == "$lock_inode" ]] || fail 'the persistent lock file inode was replaced'
flock -n "$lock_file" true || fail 'pi exit did not release its kernel session lock'

lock_target=$ROOT/lock-target
printf 'lock target\n' > "$lock_target"
symlink_lock_root=$ROOT/symlink-lock-root
mkdir -p "$symlink_lock_root/locks"
ln -s "$lock_target" "$symlink_lock_root/locks/symlink-role.lock"
if run_pane opendesign_volker symlink-role "$symlink_lock_root" > "$ROOT/symlink-lock.out" 2>&1; then fail 'pane accepted a symlink lock path'; fi
grep -Fq 'regular, non-symlink file' "$ROOT/symlink-lock.out" || fail 'pane did not refuse a symlink lock path clearly'
[[ $(<"$lock_target") == 'lock target' ]] || fail 'symlink lock refusal changed its target'

nonregular_lock_root=$ROOT/nonregular-lock-root
mkdir -p "$nonregular_lock_root/locks/symlink-role.lock"
if run_pane opendesign_volker symlink-role "$nonregular_lock_root" > "$ROOT/nonregular-lock.out" 2>&1; then fail 'pane accepted a non-regular lock path'; fi
grep -Fq 'regular, non-symlink file' "$ROOT/nonregular-lock.out" || fail 'pane did not refuse a non-regular lock path clearly'
[[ $(stat -c '%d:%i' "$lock_file") == "$lock_inode" ]] || fail 'the lock inode changed during refusal checks'

run_pane opendesign_github2 > "$ROOT/pane.out" 2>&1
[[ $(<"$PI_KEYS") == $'VOLKER=unset\nGITHUB2=present' ]] || fail 'github2 launch did not export only the github2 key'
if grep -aFq 'test-volker-secret-marker' "$PI_ARGV" "$ROOT/pane.out"; then fail 'volker key appeared in github2 pi argv or output'; fi
if grep -aFq 'test-github2-secret-marker' "$PI_ARGV" "$ROOT/pane.out"; then fail 'github2 key appeared in pi argv or output'; fi

node --test --test-reporter=tap "$CONTROLLER_DIR/pi-ctl/test-core.mjs"
echo 'PASS pi controller harness tests'
