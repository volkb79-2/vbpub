#!/usr/bin/env bash
set -euo pipefail

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

cat > "$BIN/tmux" <<'TMUX'
#!/usr/bin/env bash
set -uo pipefail
: "${PI_CTL_TEST_TMUX_ARGV:?}"
: "${PI_CTL_TEST_TMUX_KEYS:?}"
printf '%s\0' "$@" >> "$PI_CTL_TEST_TMUX_ARGV"
{
  if [[ -v OPENDESIGN_API_KEY_VOLKER ]]; then printf 'VOLKER=present\n'; else printf 'VOLKER=unset\n'; fi
  if [[ -v OPENDESIGN_API_KEY_GITHUB2 ]]; then printf 'GITHUB2=present\n'; else printf 'GITHUB2=unset\n'; fi
} > "$PI_CTL_TEST_TMUX_KEYS"
case ${1-} in
  has-session) exit "${PI_CTL_TEST_HAS_SESSION_RC:-1}" ;;
  new-session) exit 0 ;;
  *) exit 2 ;;
esac
TMUX
cat > "$BIN/pi" <<'PI'
#!/usr/bin/env bash
set -uo pipefail
: "${PI_CTL_TEST_PI_ARGV:?}"
: "${PI_CTL_TEST_PI_KEYS:?}"
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

run_launcher() {
  local env_name
  local -a configured=()
  for env_name in PI_CTL_POLL_MS PI_CTL_MAX_MESSAGE_BYTES PI_CTL_MAX_SEND_BYTES \
    PI_CTL_SEND_LIMIT PI_CTL_INBOUND_LIMIT PI_CTL_LOOP_WINDOW_SECONDS \
    PI_CTL_COMPACT_AT PI_CTL_COMPACT_BACKOFF_SECONDS; do
    if [[ -v $env_name ]]; then configured+=("$env_name=${!env_name}"); fi
  done
  env -i HOME="$HOME_DIR" PATH="$PATH_VALUE" PI_CTL_ROOT="$STATE_ROOT" \
    PI_CTL_CWD="$WORK_DIR" PI_CTL_PRIME="$PRIME" PI_CTL_BIN="$BIN/pi" \
    PI_CTL_PROVIDER="${PI_CTL_PROVIDER:-opendesign_volker}" \
    PI_CTL_SESSION_DIR="$ROOT/sessions" PI_CTL_TEST_TMUX_ARGV="$TMUX_ARGV" \
    PI_CTL_TEST_TMUX_KEYS="$ROOT/tmux.keys" \
    PI_CTL_TEST_HAS_SESSION_RC="${PI_CTL_TEST_HAS_SESSION_RC:-1}" \
    OPENDESIGN_API_KEY_VOLKER='ambient-volker-secret-marker' \
    OPENDESIGN_API_KEY_GITHUB2='ambient-github2-secret-marker' \
    "${configured[@]}" \
    "$CONTROLLER_DIR/pi-ctl-launch.sh" "$@"
}

print_output=$(run_launcher --print-command hostobs)
[[ $print_output == *"opendesign_volker"* && $print_output == *"deepseek-v4.1-flash-eco"* && $print_output == *"max"* ]] \
  || fail '--print-command did not show the resolved defaults'
[[ ! -e $TMUX_ARGV && ! -e $STATE_ROOT ]] || fail '--print-command started or wrote launcher state'
[[ $print_output != *'test-volker-secret-marker'* && $print_output != *'test-github2-secret-marker'* ]] \
  || fail '--print-command exposed a key'

unset PI_CTL_TEST_HAS_SESSION_RC
run_launcher hostobs > "$ROOT/launch.out"
[[ -s $TMUX_ARGV ]] || fail 'launcher did not call the fake tmux'
mapfile -d '' -t tmux_args < "$TMUX_ARGV"
contains_arg 'has-session' "${tmux_args[@]}"
contains_arg 'new-session' "${tmux_args[@]}"
contains_arg 'vbpub-hostobs' "${tmux_args[@]}"
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
contains_arg 'opendesign_github2' "${tmux_args[@]}"
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
if PI_CTL_TEST_HAS_SESSION_RC=0 run_launcher hostobs > "$ROOT/existing.out" 2>&1; then fail 'existing tmux session was not refused'; fi
if grep -aFq 'test-volker-secret-marker' "$ROOT/existing.out"; then fail 'key appeared in existing-session refusal'; fi

mkdir -p "$STATE_ROOT/locks"
IFS= read -r stat_line < "/proc/$$/stat"
stat_rest=${stat_line##*)}
read -r -a stat_fields <<< "$stat_rest"
printf '%s %s\n' "$$" "${stat_fields[19]}" > "$STATE_ROOT/locks/hostobs.lock"
chmod 0600 "$STATE_ROOT/locks/hostobs.lock"
: > "$TMUX_ARGV"
if run_launcher hostobs > "$ROOT/lock.out" 2>&1; then fail 'live controller lock was not refused'; fi
[[ ! -s $TMUX_ARGV ]] || fail 'launcher reached tmux despite a live lock'
rm -f -- "$STATE_ROOT/locks/hostobs.lock"

run_pane() {
  local provider=$1
  env -i HOME="$HOME_DIR" PATH="$PATH_VALUE" PI_CTL_TEST_PI_ARGV="$PI_ARGV" \
    PI_CTL_TEST_PI_KEYS="$PI_KEYS" \
    "$CONTROLLER_DIR/pi-ctl-pane.sh" hostobs "$provider" deepseek-v4.1-flash-eco max \
      "$fresh_id" "$STATE_ROOT" "$PRIME" "$BIN/pi" "$ROOT/sessions" \
      "$CONTROLLER_DIR/pi-pidns-extension.js" "$CONTROLLER_DIR/pi-ctl/extension.mjs" \
      "$CONTROLLER_DIR/pi-pidns-bash" vbpub-hostobs > "$ROOT/pane.out" 2>&1
}
run_pane opendesign_volker
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

run_pane opendesign_github2
[[ $(<"$PI_KEYS") == $'VOLKER=unset\nGITHUB2=present' ]] || fail 'github2 launch did not export only the github2 key'
if grep -aFq 'test-volker-secret-marker' "$PI_ARGV" "$ROOT/pane.out"; then fail 'volker key appeared in github2 pi argv or output'; fi
if grep -aFq 'test-github2-secret-marker' "$PI_ARGV" "$ROOT/pane.out"; then fail 'github2 key appeared in pi argv or output'; fi

node --test "$CONTROLLER_DIR/pi-ctl/test-core.mjs"
echo 'PASS pi controller harness tests'
