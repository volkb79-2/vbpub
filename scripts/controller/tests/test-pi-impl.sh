#!/usr/bin/env bash
set -uo pipefail
unset PI_ROUTE PI_MODEL PI_THINKING PI_SESSION_DIR OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SRC=${PI_IMPL_SCRIPT:-$SCRIPT_DIR/../pi-impl.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-pi-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() {
  local needle=$1 description=$2
  shift 2
  ! grep -R -qF -- "$needle" "$@" || fail "$description"
}

HOME_ROOT=$ROOT/home
CONTROLLER_ROOT=$ROOT/controller
WORKTREE=$ROOT/worktree
PROMPT=$ROOT/brief.md
STATE=$ROOT/opendesign-state.json
PI_ARGV_LOG=$ROOT/pi-argv.log
PI_ENV_LOG=$ROOT/pi-env.log
PI_PROMPT_LOG=$ROOT/pi-prompt.log
mkdir -p "$HOME_ROOT" "$ROOT/bin" "$CONTROLLER_ROOT" "$WORKTREE"
printf 'brief\n' > "$PROMPT"
cat > "$CONTROLLER_ROOT/controller.local.env" <<'ENV'
PI_ROUTE="deepseek-pi"
PI_MODEL="deepseek-v4.1-flash-eco"
PI_THINKING="high"
PI_SESSION_DIR="$HOME/.pi/agent/sessions"
ENV
cat > "$HOME_ROOT/.env.ai" <<'ENV'
export OPENDESIGN_API_KEY_VOLKER=test-key
export OPENDESIGN_API_KEY_GITHUB2=test-key
ENV

cat > "$ROOT/bin/pi" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$PI_ARGV_LOG"
printf '%s' "${!#}" > "$PI_PROMPT_LOG"
printf 'VOLKER_PRESENT=%s GITHUB2_PRESENT=%s\n' \
  "${OPENDESIGN_API_KEY_VOLKER:+yes}" "${OPENDESIGN_API_KEY_GITHUB2:+yes}" >> "$PI_ENV_LOG"
# This blocks while stdin remains open. The wrapper must give pi /dev/null.
if [[ -p /dev/stdin && -n ${FAKE_PI_STDIN_OPEN_MARKER:-} ]]; then
  : > "$FAKE_PI_STDIN_OPEN_MARKER"
fi
while IFS= read -r _line; do :; done
printf 'STDIN_EOF=1\n' >> "$PI_ENV_LOG"
python3 - "$FAKE_PI_STREAM" "${FAKE_PI_SESSION_ID:-}" <<'PY'
import json
import sys

for line in open(sys.argv[1], encoding="utf-8"):
    event = json.loads(line)
    if event.get("type") == "session" and sys.argv[2]:
        event["id"] = sys.argv[2]
    print(json.dumps(event, separators=(",", ":")))
PY
exit "${FAKE_PI_RC:-0}"
SH
chmod +x "$ROOT/bin/pi"

export CONTROLLER_HOME=$CONTROLLER_ROOT CONTROLLER_REPO=$WORKTREE
export CONTROLLER_OD_STATE=$STATE
export PI_ARGV_LOG PI_ENV_LOG PI_PROMPT_LOG

launch() {
  local run=$1 route=$2 fixture=$3 input=$4
  shift 4
  local fake_session_id="${run}-session"
  if [[ ${1:-} == resume && -n ${2:-} ]]; then fake_session_id=$2; fi
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE="$route" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/$fixture.jsonl" FAKE_PI_SESSION_ID="$fake_session_id" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" "$run" "$@" \
    > "$ROOT/$run.launch.log" 2>&1 < "$input"
}

wait_launch() {
  local run=$1 route=$2 fixture=$3 input=$4
  shift 4
  launch "$run" "$route" "$fixture" "$input" "$@" &
  local pid=$!
  if ! wait "$pid"; then
    cat "$ROOT/$run.launch.log" >&2
    fail "pi wrapper failed for $run"
  fi
}

wait_launch auto-1 deepseek-pi pi-success /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/auto-1.route") == deepseek-pi-volker ]] || fail 'first automatic pi start must select volker'
if ! grep -qF -- 'EXIT=0 OUTCOME=SUCCESS' "$CONTROLLER_ROOT/runs/auto-1.done"; then
  echo 'pi completion diagnostics:' >&2
  for evidence in auto-1.done auto-1.jsonl auto-1.err; do
    if [[ -f $CONTROLLER_ROOT/runs/$evidence ]]; then
      printf '%s: ' "$evidence" >&2
      cat "$CONTROLLER_ROOT/runs/$evidence" >&2
    fi
  done
  printf 'launch log: ' >&2
  cat "$ROOT/auto-1.launch.log" >&2
  fail 'normal pi completion is recorded as success'
fi
contains "$CONTROLLER_ROOT/runs/auto-1.thread" 'auto-1-session' 'the pi session id is recorded in a sidecar'
contains "$CONTROLLER_ROOT/runs/auto-1.last.txt" 'Completed the fixture task.' 'last assistant text is saved'

wait_launch auto-2 deepseek-pi pi-success /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/auto-2.route") == deepseek-pi-github2 ]] || fail 'second automatic pi start must alternate to github2'
wait_launch auto-3 deepseek-pi pi-success /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/auto-3.route") == deepseek-pi-volker ]] || fail 'third automatic pi start must alternate back to volker'
contains "$PI_ARGV_LOG" '--model opendesign_volker/deepseek-v4.1-flash-eco' 'pi uses the selected Volker provider and default model'
contains "$PI_ARGV_LOG" '--thinking high' 'pi defaults to high thinking'
contains "$PI_ARGV_LOG" "--session-dir $HOME_ROOT/.pi/agent/sessions" 'pi uses its default session directory'
printf 'brief\n' > "$ROOT/prompt-with-newline.md"
PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=prompt-newline-session \
  FAKE_PI_RC=0 "$SRC" "$WORKTREE" "$ROOT/prompt-with-newline.md" prompt-newline \
  > "$ROOT/prompt-newline.launch.log" 2>&1 < /dev/null || fail 'pi wrapper rejected a prompt file ending in a newline'
cmp -s "$PI_PROMPT_LOG" "$ROOT/prompt-with-newline.md" \
  || fail 'pi prompt argument lost the prompt file trailing newline'
contains "$PI_ENV_LOG" 'VOLKER_PRESENT=yes GITHUB2_PRESENT=' 'pi receives only the selected Volker API-key variable'
contains "$PI_ENV_LOG" 'VOLKER_PRESENT= GITHUB2_PRESENT=yes' 'pi receives only the selected github2 API-key variable'
python3 - "$STATE" <<'PY'
import json, sys
path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["threads"].pop("auto-1-session", None)
json.dump(state, open(path, "w", encoding="utf-8"))
PY
found_account=$(CONTROLLER_RUNS_DIR="$CONTROLLER_ROOT/runs" CONTROLLER_OD_STATE="$STATE" \
  python3 "$SCRIPT_DIR/../opendesign-accounts.py" find-thread-account auto-1-session "$CONTROLLER_ROOT/runs") \
  || fail 'pi session id in the run stream and route sidecar was not found'
[[ $found_account == volker ]] || fail 'pi session lookup returned the wrong recorded account'
wait_launch resume-recorded deepseek-pi pi-success /dev/null resume auto-1-session
[[ $(cat "$CONTROLLER_ROOT/runs/resume-recorded.route") == deepseek-pi-volker ]] || fail 'resume stays on the account recorded for the pi session'
contains "$PI_ARGV_LOG" '--session auto-1-session' 'resume passes the recorded pi session id'

PI_MODEL=deepseek-v4.1-flash PI_THINKING=low wait_launch model-override deepseek-pi pi-success /dev/null
contains "$PI_ARGV_LOG" '--model opendesign_github2/deepseek-v4.1-flash' 'caller model override survives local controller settings'
contains "$PI_ARGV_LOG" '--thinking low' 'caller thinking override survives local controller settings'

wait_launch quota-volker deepseek-pi-volker pi-402 /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/quota-volker.done") == 'EXIT=0 OUTCOME=USAGE-LIMIT' ]] || fail 'pi exit zero with a final 402 is classified as usage-limited'
[[ $(cat "$CONTROLLER_ROOT/runs/quota-volker.route") == deepseek-pi-volker ]] || fail 'pinned pi route records its selected account'
contains "$ROOT/quota-volker.launch.log" 'reset time observed' 'pi 402 reset is reported by shared account bookkeeping'
python3 - "$STATE" <<'PY' || fail 'pi 402 reset was not recorded for the selected account'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["volker"]["blocked_until_epoch"] > 4_000_000_000
PY

wait_launch blocked-skip deepseek-pi pi-success /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/blocked-skip.route") == deepseek-pi-github2 ]] || fail 'automatic pi start skips the account blocked by the 402'

wait_launch resume-fallback deepseek-pi pi-success /dev/null resume auto-1-session
[[ $(cat "$CONTROLLER_ROOT/runs/resume-fallback.route") == deepseek-pi-github2 ]] || fail 'resume falls back when its recorded account is blocked'
contains "$ROOT/resume-fallback.launch.log" 'account fallback from deepseek-volker to deepseek-github2 because the recorded account is blocked' \
  'resume logs the shared helper account fallback'

UNKNOWN_STATE=$ROOT/opendesign-unknown-reset.json
if ! PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-github2 \
  CONTROLLER_OD_STATE="$UNKNOWN_STATE" CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS=21600 \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-402-unknown.jsonl" \
  FAKE_PI_SESSION_ID=unknown-reset-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" unknown-reset > "$ROOT/unknown-reset.launch.log" 2>&1 \
  < /dev/null; then
  cat "$ROOT/unknown-reset.launch.log" >&2
  fail 'pi 402 without reset_at did not complete with its usage-limit outcome'
fi
contains "$CONTROLLER_ROOT/runs/unknown-reset.done" 'EXIT=0 OUTCOME=USAGE-LIMIT' 'pi 402 without reset_at is still classified as usage-limited'
contains "$ROOT/unknown-reset.launch.log" 'reset time unknown; temporary block for 21600 seconds' \
  'pi 402 without reset_at uses the bounded six-hour default'
python3 - "$UNKNOWN_STATE" <<'PY' || fail 'unknown-reset 402 did not persist a temporary block'
import json, sys, time
state = json.load(open(sys.argv[1], encoding="utf-8"))
until = state["github2"]["blocked_until_epoch"]
assert until > time.time() + 20_000
PY

python3 - "$STATE" <<'PY'
import json, sys
path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["github2"]["blocked_until_epoch"] = 4_102_444_800
json.dump(state, open(path, "w", encoding="utf-8"))
PY
before=$(wc -l < "$PI_ARGV_LOG")
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$WORKTREE" "$PROMPT" both-blocked > "$ROOT/both-blocked.log" 2>&1; then
  fail 'automatic pi start ran while both Open Design accounts were blocked'
else rc=$?; [[ $rc == 3 ]] || fail "both-blocked pi exit was $rc, expected 3"; fi
contains "$ROOT/both-blocked.log" 'both Open Design accounts are blocked' 'both blocked pi accounts report their reset'
[[ $(wc -l < "$PI_ARGV_LOG") == "$before" ]] || fail 'pi started even though both accounts were blocked'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$WORKTREE" "$PROMPT" both-blocked-resume resume auto-1-session \
  > "$ROOT/both-blocked-resume.log" 2>&1; then
  fail 'pi resumed while both Open Design accounts were blocked'
else rc=$?; [[ $rc == 3 ]] || fail "both-blocked resume exit was $rc, expected 3"; fi
contains "$ROOT/both-blocked-resume.log" 'both Open Design accounts are blocked' 'both blocked pi resume accounts report their reset'
[[ $(wc -l < "$PI_ARGV_LOG") == "$before" ]] || fail 'pi resume started while both accounts were blocked'

mkfifo "$ROOT/open-stdin.fifo"
exec 9<> "$ROOT/open-stdin.fifo"
setsid env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_RC=0 \
  FAKE_PI_STDIN_OPEN_MARKER="$ROOT/stdin-open.marker" \
  "$SRC" "$WORKTREE" "$PROMPT" stdin-closed > "$ROOT/stdin-closed.launch.log" 2>&1 \
  < "$ROOT/open-stdin.fifo" &
stdin_pid=$!
while kill -0 "$stdin_pid" 2>/dev/null && [[ ! -e $ROOT/stdin-open.marker && ! -e $CONTROLLER_ROOT/runs/stdin-closed.done ]]; do
  sleep 0.05
done
if [[ -e $ROOT/stdin-open.marker ]]; then
  kill -TERM -- "-$stdin_pid" 2>/dev/null || true
  wait "$stdin_pid" 2>/dev/null || true
  fail 'fake pi remained attached to the caller stdin'
fi
if ! wait "$stdin_pid"; then
  cat "$ROOT/stdin-closed.launch.log" >&2
  fail 'pi did not finish when its caller stdin remained open'
fi
exec 9>&-
contains "$PI_ENV_LOG" 'STDIN_EOF=1' 'fake pi observed closed stdin'

if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$WORKTREE" relative-prompt invalid-relative > "$ROOT/relative-prompt.log" 2>&1; then
  fail 'relative prompt path was accepted'
else rc=$?; [[ $rc == 2 ]] || fail "relative prompt exit was $rc, expected 2"; fi
contains "$ROOT/relative-prompt.log" 'prompt path must be absolute' 'prompt path is required to be absolute'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$ROOT/missing-worktree" "$PROMPT" missing-worktree \
  > "$ROOT/missing-worktree.log" 2>&1; then
  fail 'pi wrapper accepted a missing worktree'
else rc=$?; [[ $rc == 2 ]] || fail "missing worktree exit was $rc, expected 2"; fi
contains "$ROOT/missing-worktree.log" 'usage: ' 'pi wrapper requires an existing worktree'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$WORKTREE" "$PROMPT" extra-arguments new unused \
  > "$ROOT/extra-arguments.log" 2>&1; then
  fail 'pi wrapper accepted extra arguments for a new run'
else rc=$?; [[ $rc == 2 ]] || fail "extra argument exit was $rc, expected 2"; fi
contains "$ROOT/extra-arguments.log" 'usage: ' 'pi wrapper rejects unused mode arguments'

VALIDATION_STATE=$ROOT/validation-state.json
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=unknown \
  CONTROLLER_OD_STATE="$VALIDATION_STATE" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
  FAKE_PI_SESSION_ID=unknown-route-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" unknown-route > "$ROOT/unknown-route.log" 2>&1; then
  fail 'unknown pi route was accepted'
else rc=$?; [[ $rc == 2 ]] || fail "unknown route exit was $rc, expected 2"; fi
contains "$ROOT/unknown-route.log" 'unknown PI_ROUTE=unknown' 'unknown pi routes fail closed'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  CONTROLLER_OD_STATE="$VALIDATION_STATE" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
  FAKE_PI_SESSION_ID=invalid-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" invalid-session resume 'invalid session id' \
  > "$ROOT/invalid-session.log" 2>&1; then
  fail 'pi wrapper accepted an invalid session id'
else rc=$?; [[ $rc == 2 ]] || fail "invalid session exit was $rc, expected 2"; fi
contains "$ROOT/invalid-session.log" 'usage: ' 'pi resume requires a valid session id'

NO_KEY_HOME=$ROOT/no-key-home
mkdir -p "$NO_KEY_HOME"
: > "$NO_KEY_HOME/.env.ai"
before=$(wc -l < "$PI_ARGV_LOG")
if PATH="$ROOT/bin:$PATH" HOME="$NO_KEY_HOME" PI_ROUTE=deepseek-pi-github2 \
  "$SRC" "$WORKTREE" "$PROMPT" missing-key > "$ROOT/missing-key.log" 2>&1; then
  fail 'pi wrapper started without the selected API-key environment variable'
else rc=$?; [[ $rc == 2 ]] || fail "missing key exit was $rc, expected 2"; fi
contains "$ROOT/missing-key.log" 'OPENDESIGN_API_KEY_GITHUB2 is not set' 'missing selected provider key is refused'
[[ $(wc -l < "$PI_ARGV_LOG") == "$before" ]] || fail 'pi started without the selected provider key'
if PATH="$ROOT/bin:$PATH" HOME="$NO_KEY_HOME" PI_ROUTE=deepseek-pi-volker \
  "$SRC" "$WORKTREE" "$PROMPT" missing-volker-key > "$ROOT/missing-volker-key.log" 2>&1; then
  fail 'pi wrapper started without the selected Volker API-key environment variable'
else rc=$?; [[ $rc == 2 ]] || fail "missing Volker key exit was $rc, expected 2"; fi
contains "$ROOT/missing-volker-key.log" 'OPENDESIGN_API_KEY_VOLKER is not set' 'missing selected Volker key is refused'
[[ $(wc -l < "$PI_ARGV_LOG") == "$before" ]] || fail 'pi started without the selected Volker key'

MISSING_ENV_HOME=$ROOT/missing-env-home
mkdir -p "$MISSING_ENV_HOME"
if PATH="$ROOT/bin:$PATH" HOME="$MISSING_ENV_HOME" PI_ROUTE=deepseek-pi-volker \
  "$SRC" "$WORKTREE" "$PROMPT" missing-env > "$ROOT/missing-env.log" 2>&1; then
  fail 'pi wrapper started without the local .env.ai file'
else rc=$?; [[ $rc == 2 ]] || fail "missing env-file exit was $rc, expected 2"; fi
contains "$ROOT/missing-env.log" "pi route requires the local \$HOME/.env.ai file" 'missing .env.ai is refused'

UNEXPORTED_HOME=$ROOT/unexported-home
mkdir -p "$UNEXPORTED_HOME"
cat > "$UNEXPORTED_HOME/.env.ai" <<'ENV'
OPENDESIGN_API_KEY_VOLKER=test-key
OPENDESIGN_API_KEY_GITHUB2=test-key
ENV
if ! PATH="$ROOT/bin:$PATH" HOME="$UNEXPORTED_HOME" PI_ROUTE=deepseek-pi-github2 \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=unexported-session \
  FAKE_PI_RC=0 "$SRC" "$WORKTREE" "$PROMPT" unexported-env > "$ROOT/unexported-env.launch.log" 2>&1 \
  < /dev/null; then
  cat "$ROOT/unexported-env.launch.log" >&2
  fail 'pi wrapper did not export a key loaded from .env.ai'
fi
tail -n 1 "$PI_ENV_LOG" > "$ROOT/unexported-env.names"
contains "$ROOT/unexported-env.names" 'VOLKER_PRESENT= GITHUB2_PRESENT=yes' 'wrapper exports only the selected key name from sourced shell variables'
if ! PATH="$ROOT/bin:$PATH" HOME="$UNEXPORTED_HOME" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=unexported-volker-session \
  FAKE_PI_RC=0 "$SRC" "$WORKTREE" "$PROMPT" unexported-volker-env > "$ROOT/unexported-volker-env.launch.log" 2>&1 \
  < /dev/null; then
  cat "$ROOT/unexported-volker-env.launch.log" >&2
  fail 'pi wrapper did not export the Volker key loaded from .env.ai'
fi
tail -n 1 "$PI_ENV_LOG" > "$ROOT/unexported-volker-env.names"
contains "$ROOT/unexported-volker-env.names" 'VOLKER_PRESENT=yes GITHUB2_PRESENT=' 'wrapper exports the selected Volker key name'

if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=collision-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" auto-1 > "$ROOT/collision.log" 2>&1; then
  fail 'pi wrapper replaced a prior run name without explicit force'
else rc=$?; [[ $rc == 2 ]] || fail "run collision exit was $rc, expected 2"; fi
contains "$ROOT/collision.log" 'controller run name already has evidence: auto-1' 'pi new-run evidence collision is refused'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=escape-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" ../escape > "$ROOT/run-traversal.log" 2>&1; then
  fail 'pi wrapper accepted a traversing run name'
else rc=$?; [[ $rc == 2 ]] || fail "run traversal exit was $rc, expected 2"; fi
contains "$ROOT/run-traversal.log" 'invalid run name' 'pi run name traversal guard fires'

not_contains test-key 'run evidence contains no key material' "$CONTROLLER_ROOT/runs"
not_contains test-key 'account state contains no key material' "$STATE"
not_contains test-key 'unknown-reset account state contains no key material' "$UNKNOWN_STATE"
not_contains test-key 'pi argv contains no key material' "$PI_ARGV_LOG"
not_contains test-key 'pi environment log contains no key material' "$PI_ENV_LOG"
not_contains test-key 'wrapper logs contain no key material' "$ROOT"/*.launch.log

README_SOURCE=${README_SOURCE:-$SCRIPT_DIR/../README.md}
DESIGN_SOURCE=${DESIGN_SOURCE:-$SCRIPT_DIR/../docs/DESIGN-GUIDE.md}
CONSUMER_SOURCE=${CONSUMER_SOURCE:-$SCRIPT_DIR/../docs/CONSUMERS.md}
RULES_SOURCE=${RULES_SOURCE:-$SCRIPT_DIR/../rules.md}
ENV_SOURCE=${ENV_SOURCE:-$SCRIPT_DIR/../controller.local.env.example}
contains "$README_SOURCE" "\`deepseek-pi\` | \`pi-impl.sh\` | Favored Open Design route" 'README names pi as the favored route'
contains "$README_SOURCE" 'docs/DESIGN-GUIDE.md#pi-open-design-route' 'README links the pi route design rationale'
contains "$README_SOURCE" 'docs/CONSUMERS.md#run-pi-on-open-design' 'README links the pi adoption guide'
contains "$DESIGN_SOURCE" 'Pi 1.5.0 can report a provider 402' 'design guide explains pi exit-zero 402 classification'
contains "$CONSUMER_SOURCE" "\`deepseek-pi\` is the favored Pi route" 'consumer guide names the favored pi route'
contains "$CONSUMER_SOURCE" 'PI_ROUTE=deepseek-pi' 'consumer guide has a copyable pi launch'
contains "$RULES_SOURCE" 'deepseek-pi-volker' 'controller rules list pinned pi routes'
contains "$ENV_SOURCE" 'PI_THINKING="high"' 'local environment example documents the thinking default'
if grep -Eq 'OPENDESIGN_API_KEY_[A-Z0-9_]+=' "$ENV_SOURCE"; then
  fail 'controller local environment example contains an API-key assignment'
fi

echo 'PASS pi-impl: detached launch, sessions, account alternation, 402 block/outcome, resume, stdin, key isolation, route and prompt guards'
