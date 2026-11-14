#!/usr/bin/env bash
set -uo pipefail
unset CODEX_ROUTE CODEX_HOME_OVERRIDE CODEX_HOME_LUNA CODEX_HOME_DEEPSEEK_VOLKER CODEX_HOME_DEEPSEEK_GITHUB2 CODEX_HOME_REVIEW CODEX_MODEL CODEX_EFFORT
unset CODEX_REVIEW_MODEL CODEX_REVIEW_EFFORT CODEX_SQLITE_HOME
unset CONTROLLER_OD_STATE CONTROLLER_OD_PREFER_EXPIRING OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2
unset OPENCODE_COMMAND OPENCODE_MODEL OPENCODE_PROVIDER
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-wrapper-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }

mkdir -p "$ROOT/home" "$ROOT/bin" "$ROOT/controller" "$ROOT/repo" "$ROOT/venv/bin"
cat > "$ROOT/bin/api-check" <<'SH'
#!/usr/bin/env bash
echo called >> "$API_LOG"
exit "${API_RC:-0}"
SH
cat > "$ROOT/bin/fake-ssh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$SSH_LOG"
echo ssh-ok
SH
cat > "$ROOT/bin/codex" <<'SH'
#!/usr/bin/env bash
printf 'HOME=%s\nSQLITE=%s\nARGS=%s\n' "$CODEX_HOME" "$CODEX_SQLITE_HOME" "$*" >> "$CODEX_LOG"
if [[ ${FAKE_CODEX_402:-0} == 1 ]]; then
  printf '{"error":{"code":"insufficient_balance","details":{"coding_plan_exhausted":true,"reset_at":"2099-11-17T11:44:44Z"}}}\n' >&2
  exit 42
fi
printf '{"type":"item.completed","item":{"type":"agent_message","text":"complete"}}\n'
exit "${CODEX_RC:-0}"
SH
cat > "$ROOT/bin/opencode" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$OPENCODE_LOG"
printf '{"text":"opencode-complete"}\n'
SH
cat > "$ROOT/venv/bin/cmru" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$CMRU_LOG"
exit "${CMRU_RC:-0}"
SH
cat > "$ROOT/bin/docker" <<'SH'
#!/usr/bin/env bash
if [[ $1 == save ]]; then
  echo image-stream
  exit "${DOCKER_SAVE_RC:-0}"
fi
echo local-image
SH
cat > "$ROOT/bin/fake-hx" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$2" >> "$HX_LOG"
if [[ $2 == 'gunzip | docker load' ]]; then
  cat >/dev/null
  exit "${HX_LOAD_RC:-0}"
fi
echo remote-image
SH
chmod +x "$ROOT/bin/api-check" "$ROOT/bin/fake-ssh" "$ROOT/bin/codex" "$ROOT/bin/opencode" \
  "$ROOT/venv/bin/cmru" "$ROOT/bin/docker" "$ROOT/bin/fake-hx"
export API_LOG=$ROOT/api.log SSH_LOG=$ROOT/ssh.log CODEX_LOG=$ROOT/codex.log OPENCODE_LOG=$ROOT/opencode.log CMRU_LOG=$ROOT/cmru.log HX_LOG=$ROOT/hx.log
HX_WRAPPER=${HX_WRAPPER:-$SCRIPT_DIR/../hx.sh}
RELEASE_SCRIPT=${RELEASE_SCRIPT:-$SCRIPT_DIR/../release-from-wt.sh}
PUSH_IMAGE_SCRIPT=${PUSH_IMAGE_SCRIPT:-$SCRIPT_DIR/../push-image.sh}
CODEX_IMPL_SCRIPT=${CODEX_IMPL_SCRIPT:-$SCRIPT_DIR/../codex-impl.sh}
OPENCODE_IMPL_SCRIPT=${OPENCODE_IMPL_SCRIPT:-$SCRIPT_DIR/../opencode-impl.sh}

python3 - "$ROOT/controller/hosts.local.json" "$ROOT/bin" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"hosts": {"runner-a": {
    "ssh": [str(pathlib.Path(sys.argv[2]) / "fake-ssh"), "runner-a"],
    "api_check": [str(pathlib.Path(sys.argv[2]) / "api-check")],
    "guard": "test $(hostname -s) = runner-a",
    "queue_lanes": []
}}}))
PY

PATH="$ROOT/bin:$PATH" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  API_LOG="$API_LOG" SSH_LOG="$SSH_LOG" \
  "$HX_WRAPPER" runner-a 'echo remote-ok' > "$ROOT/hx.out" 2>&1 || fail 'identity-checked SSH call failed'
contains "$API_LOG" called 'provider identity runs on every SSH call'
contains "$SSH_LOG" 'runner-a' 'configured ssh argv is used'
# shellcheck disable=SC2016
contains "$SSH_LOG" 'test $(hostname -s) = runner-a' 'remote guard is included in the command'
rm -f -- "$ROOT/ssh.log"
if PATH="$ROOT/bin:$PATH" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  API_LOG="$API_LOG" SSH_LOG="$SSH_LOG" API_RC=1 \
  "$HX_WRAPPER" runner-a 'echo must-not-run' > "$ROOT/hx-fail.out" 2>&1; then fail 'failed provider identity was accepted'; fi
[[ ! -e $ROOT/ssh.log ]] || fail 'SSH ran after provider identity refusal'

mkdir -p "$ROOT/worktree"
printf 'brief\n' > "$ROOT/brief.md"
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_RC=7 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" codex-fail > "$ROOT/codex.out" 2>&1; then
  fail 'Codex child failure was not propagated'
else rc=$?; [[ $rc == 7 ]] || fail "Codex exit was $rc, expected 7"; fi
contains "$ROOT/controller/runs/codex-fail.done" 'EXIT=7' 'Codex child exit is persisted'
contains "$CODEX_LOG" "HOME=$ROOT/codex-luna" 'Luna profile path is explicit'
contains "$CODEX_LOG" 'SQLITE=' 'shared session database path is set'
mkdir -p "$ROOT/codex-deepseek-volker/catalog"
printf '{"models":[{"name":"fixture","tool_mode":"enabled"}]}\n' > "$ROOT/codex-deepseek-volker/catalog/opendesign-models.json"
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_ROUTE=deepseek-volker CODEX_HOME_DEEPSEEK_VOLKER="$ROOT/codex-deepseek-volker" CODEX_LOG="$ROOT/deepseek-codex.log" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" deepseek-catalog > "$ROOT/deepseek-catalog.out" 2>&1; then
  fail 'DeepSeek catalog with tool_mode was accepted'
else rc=$?; [[ $rc == 3 ]] || fail "unsafe DeepSeek catalog exit was $rc, expected 3"; fi
[[ ! -e $ROOT/deepseek-codex.log ]] || fail 'Codex ran after the DeepSeek catalog guard refused the catalog'

VOLKER_HOME=$ROOT/opendesign-volker
GITHUB2_HOME=$ROOT/opendesign-github2
mkdir -p "$VOLKER_HOME/catalog" "$GITHUB2_HOME/catalog"
printf '{"models":[{"name":"fixture","tool_mode":null}]}\n' > "$VOLKER_HOME/catalog/opendesign-models.json"
printf '{"models":[{"name":"fixture","tool_mode":null}]}\n' > "$GITHUB2_HOME/catalog/opendesign-models.json"
OD_STATE=$ROOT/opendesign-state.json
OD_FUTURE_EPOCH=$(( $(date +%s) + 86400 ))
rm -f -- "$CODEX_LOG"
for run in od-alternation-1 od-alternation-2 od-alternation-3 od-alternation-4; do
  PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CONTROLLER_OD_STATE="$OD_STATE" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
    CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
    OPENDESIGN_API_KEY_VOLKER=test-key OPENDESIGN_API_KEY_GITHUB2=test-key \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" "$run" > "$ROOT/$run.out" 2>&1 \
    || fail "automatic DeepSeek start failed: $run"
done
[[ $(cat "$ROOT/controller/runs/od-alternation-1.route") == deepseek-volker ]] || fail 'automatic DeepSeek selection must start with volker on an empty state'
[[ $(cat "$ROOT/controller/runs/od-alternation-2.route") == deepseek-github2 ]] || fail 'automatic DeepSeek selection must alternate to github2'
[[ $(cat "$ROOT/controller/runs/od-alternation-3.route") == deepseek-volker ]] || fail 'automatic DeepSeek selection must alternate back to volker'
[[ $(cat "$ROOT/controller/runs/od-alternation-4.route") == deepseek-github2 ]] || fail 'automatic DeepSeek selection must alternate back to github2'
contains "$CODEX_LOG" "HOME=$VOLKER_HOME" 'volker route selects the volker Codex home'
contains "$CODEX_LOG" "HOME=$GITHUB2_HOME" 'github2 route selects the github2 Codex home'
contains "$CODEX_LOG" 'deepseek-v4.1-flash-eco' 'DeepSeek route uses the configured default model'
contains "$CODEX_LOG" 'model_reasoning_effort="high"' 'DeepSeek route uses high reasoning effort'
not_contains "$CODEX_LOG" test-key 'API key values are absent from Codex argv logs'
not_contains "$OD_STATE" test-key 'Open Design account state contains no key material'
for run in od-alternation-1 od-alternation-2 od-alternation-3 od-alternation-4; do
  for extension in jsonl err done autoresume.log; do
    not_contains "$ROOT/controller/runs/$run.$extension" test-key "API key values are absent from $run.$extension"
  done
done
python3 - "$OD_STATE" <<'PY' || fail 'generated Open Design state contains fields outside its key-free schema'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert set(state) == {"volker", "github2"}
assert all(set(item) <= {"last_used_epoch", "blocked_until_epoch", "window_end_epoch"} for item in state.values())
PY

python3 - "$ROOT/od-unsafe-state.json" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": 0, "api_key": "test-key"}}, open(sys.argv[1], "w"))
PY
before_calls=$(wc -l < "$CODEX_LOG")
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-unsafe-state.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-unsafe-state > "$ROOT/od-unsafe-state.out" 2>&1; then
  fail 'Open Design account state accepted an unrecognized field that could contain key material'
fi
contains "$ROOT/od-unsafe-state.out" 'invalid Open Design account state schema' 'invalid state schema is rejected without echoing its contents'
not_contains "$ROOT/od-unsafe-state.out" test-key 'state validation does not print unrecognized credential fields'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran with an invalid account state schema'

python3 - "$ROOT/od-nonnumeric-state.json" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": "not-an-epoch", "blocked_until_epoch": 0}}, open(sys.argv[1], "w"))
PY
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-nonnumeric-state.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-nonnumeric-state > "$ROOT/od-nonnumeric-state.out" 2>&1; then
  fail 'Open Design account state accepted a non-numeric last_used_epoch'
fi
contains "$ROOT/od-nonnumeric-state.out" 'invalid last_used_epoch for account volker' 'numeric account-state fields are validated'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran with a non-numeric account state'

if (cd "$ROOT" && PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE=relative-state.json CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-relative-state > "$ROOT/od-relative-state.out" 2>&1); then
  fail 'relative Open Design state path was accepted'
fi
contains "$ROOT/od-relative-state.out" 'CONTROLLER_OD_STATE must be an absolute path' 'Open Design state path must be absolute'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran after rejecting a relative state path'

if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-invalid-preference.json" CONTROLLER_OD_PREFER_EXPIRING=yes \
  CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" \
  CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-invalid-preference \
  > "$ROOT/od-invalid-preference.out" 2>&1; then
  fail 'invalid Open Design expiring-window preference was accepted'
fi
contains "$ROOT/od-invalid-preference.out" 'CONTROLLER_OD_PREFER_EXPIRING must be 0 or 1' 'invalid expiring-window preference is rejected'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran after rejecting an invalid expiring-window preference'

python3 - "$ROOT/od-blocked.json" "$OD_FUTURE_EPOCH" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": int(sys.argv[2])},
           "github2": {"last_used_epoch": 100, "blocked_until_epoch": 0}}, open(sys.argv[1], "w"))
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-blocked.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-blocked-skip > "$ROOT/od-blocked-skip.out" 2>&1 \
  || fail 'automatic DeepSeek start failed with one blocked account'
[[ $(cat "$ROOT/controller/runs/od-blocked-skip.route") == deepseek-github2 ]] || fail 'automatic DeepSeek selection did not skip the blocked account'

python3 - "$ROOT/od-expired.json" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": 1},
           "github2": {"last_used_epoch": 100, "blocked_until_epoch": 0}}, open(sys.argv[1], "w"))
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-expired.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-expired-unblocked > "$ROOT/od-expired-unblocked.out" 2>&1 \
  || fail 'automatic DeepSeek start failed when a block had expired'
[[ $(cat "$ROOT/controller/runs/od-expired-unblocked.route") == deepseek-volker ]] || fail 'a past blocked_until_epoch remained blocked'

OD_EARLIEST_RESET=$((OD_FUTURE_EPOCH + 50))
OD_LATER_RESET=$((OD_FUTURE_EPOCH + 100))
python3 - "$ROOT/od-both-blocked.json" "$OD_LATER_RESET" "$OD_EARLIEST_RESET" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": int(sys.argv[2])},
           "github2": {"last_used_epoch": 2, "blocked_until_epoch": int(sys.argv[3])}}, open(sys.argv[1], "w"))
PY
before_calls=$(wc -l < "$CODEX_LOG")
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-both-blocked.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-both-blocked > "$ROOT/od-both-blocked.out" 2>&1; then
  fail 'automatic DeepSeek start accepted two blocked accounts'
else rc=$?; [[ $rc == 3 ]] || fail "both-blocked exit was $rc, expected 3"; fi
contains "$ROOT/od-both-blocked.out" "earliest reset is $(date -u -d "@$OD_EARLIEST_RESET" +%Y-%m-%dT%H:%M:%SZ)" 'both-blocked refusal reports the earliest reset'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran while both accounts were blocked'

python3 - "$ROOT/od-expiring.json" "$OD_FUTURE_EPOCH" <<'PY'
import json, sys
future = int(sys.argv[2])
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": 0, "window_end_epoch": future + 100},
           "github2": {"last_used_epoch": 100, "blocked_until_epoch": 0, "window_end_epoch": future + 50}}, open(sys.argv[1], "w"))
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-expiring.json" CONTROLLER_OD_PREFER_EXPIRING=1 \
  CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" CODEX_SQLITE_HOME="$ROOT/sqlite" \
  CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-expiring > "$ROOT/od-expiring.out" 2>&1 \
  || fail 'DeepSeek prefer-expiring start failed'
[[ $(cat "$ROOT/controller/runs/od-expiring.route") == deepseek-github2 ]] || fail 'prefer-expiring did not choose the earliest window end'

python3 - "$ROOT/od-pinned.json" "$OD_FUTURE_EPOCH" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": int(sys.argv[2])},
           "github2": {"last_used_epoch": 2, "blocked_until_epoch": 0}}, open(sys.argv[1], "w"))
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-pinned.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/od-pinned-codex.log" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-pinned > "$ROOT/od-pinned.out" 2>&1 \
  || fail 'pinned DeepSeek route failed instead of staying on volker'
[[ $(cat "$ROOT/controller/runs/od-pinned.route") == deepseek-volker ]] || fail 'pinned route changed accounts'
contains "$ROOT/od-pinned-codex.log" "HOME=$VOLKER_HOME" 'pinned DeepSeek route uses the named account home'

if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-402.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 FAKE_CODEX_402=1 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-402 > "$ROOT/od-402.out" 2>&1; then
  fail 'fake 402 Codex run unexpectedly succeeded'
else rc=$?; [[ $rc == 42 ]] || fail "402 child exit was $rc, expected 42"; fi
contains "$ROOT/od-402.out" 'blocked until 2099-11-17T11:44:44Z' '402 response records the account reset time'
python3 - "$ROOT/od-402.json" <<'PY' || fail '402 did not persist blocked_until_epoch for the run account'
from datetime import datetime, timezone
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
expected = int(datetime(2099, 11, 17, 11, 44, 44, tzinfo=timezone.utc).timestamp())
assert state["volker"]["blocked_until_epoch"] == expected
assert state["github2"]["blocked_until_epoch"] == 0
PY

if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_ROUTE=unknown CODEX_LOG="$CODEX_LOG" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" bad-route > "$ROOT/route.out" 2>&1; then
  fail 'unknown Codex route was accepted'
fi
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_HOME_REVIEW="$ROOT/codex-review" CODEX_LOG="$CODEX_LOG" \
  "$SCRIPT_DIR/../codex-review.sh" "$ROOT/worktree" "$ROOT/brief.md" review-run > "$ROOT/review.out" 2>&1; then :; else fail 'Codex review wrapper failed'; fi
contains "$CODEX_LOG" "HOME=$ROOT/codex-review" 'review uses its separately configured profile'
contains "$CODEX_LOG" 'gpt-6-sol' 'review uses the independent review model default'

PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  OPENCODE_COMMAND="$ROOT/bin/opencode" OPENCODE_LOG="$OPENCODE_LOG" \
  "$OPENCODE_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" opencode-run > "$ROOT/opencode.out" 2>&1 \
  || fail 'OpenCode wrapper failed'
contains "$ROOT/controller/runs/opencode-run.last.txt" opencode-complete 'OpenCode final text is extracted'
contains "$ROOT/controller/runs/opencode-run.done" 'EXIT=0' 'OpenCode completion is persisted'
contains "$OPENCODE_LOG" 'run -m opendesign_github2/deepseek-v4.1-flash-eco' 'OpenCode defaults to the github2 provider'
OPENCODE_LOG=$ROOT/opencode-override.log PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  OPENCODE_COMMAND="$ROOT/bin/opencode" OPENCODE_PROVIDER=opendesign_volker \
  "$OPENCODE_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" opencode-provider > "$ROOT/opencode-provider.out" 2>&1 \
  || fail 'OpenCode provider override failed'
contains "$ROOT/opencode-override.log" 'run -m opendesign_volker/deepseek-v4.1-flash-eco' 'OpenCode provider override selects volker'
OPENCODE_LOG=$ROOT/opencode-model.log PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  OPENCODE_COMMAND="$ROOT/bin/opencode" OPENCODE_PROVIDER=opendesign_volker OPENCODE_MODEL=opendesign_github2/custom-model \
  "$OPENCODE_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" opencode-model > "$ROOT/opencode-model.out" 2>&1 \
  || fail 'OpenCode full model override failed'
contains "$ROOT/opencode-model.log" 'run -m opendesign_github2/custom-model' 'full OpenCode model overrides provider selection'

REL_REPO=$ROOT/rel-worktree
mkdir -p "$REL_REPO/project/logs" "$REL_REPO/project/artifacts" "$REL_REPO/project/evidence"
git -C "$REL_REPO" init -q
git -C "$REL_REPO" config user.name test
git -C "$REL_REPO" config user.email test@example.invalid
printf 'log evidence\n' > "$REL_REPO/project/logs/run.log"
printf 'build output\n' > "$REL_REPO/project/artifacts/output.txt"
printf 'gate evidence\n' > "$REL_REPO/project/evidence/gate.txt"
git -C "$REL_REPO" add project && git -C "$REL_REPO" commit -qm baseline
git -C "$REL_REPO" branch -M rel-test
printf 'some avg10=1.00 avg60=1.00 avg300=1.00 total=1\n' > "$ROOT/memory.pressure"
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CMRU_LOG="$CMRU_LOG" CMRU_RC=0 RELEASE_PSI_LIMIT=invalid RELEASE_PSI_FILE="$ROOT/missing.pressure" \
  "$RELEASE_SCRIPT" "$REL_REPO" invalid-psi-limit.log --venv "$ROOT/venv" -- project \
  > "$ROOT/release-invalid-limit.out" 2>&1; then
  fail 'invalid RELEASE_PSI_LIMIT was accepted'
else rc=$?; [[ $rc == 2 ]] || fail "invalid PSI limit exit was $rc, expected 2"; fi
[[ ! -e $CMRU_LOG ]] || fail 'CMRU ran after invalid RELEASE_PSI_LIMIT'
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CMRU_LOG="$CMRU_LOG" CMRU_RC=0 RELEASE_PSI_POLL_SECONDS=invalid RELEASE_PSI_FILE="$ROOT/memory.pressure" \
  "$RELEASE_SCRIPT" "$REL_REPO" invalid-psi-poll.log --venv "$ROOT/venv" -- project \
  > "$ROOT/release-invalid-poll.out" 2>&1; then
  fail 'invalid RELEASE_PSI_POLL_SECONDS was accepted'
else rc=$?; [[ $rc == 2 ]] || fail "invalid PSI poll exit was $rc, expected 2"; fi
[[ ! -e $CMRU_LOG ]] || fail 'CMRU ran after invalid RELEASE_PSI_POLL_SECONDS'
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CMRU_LOG="$CMRU_LOG" CMRU_RC=7 RELEASE_PSI_FILE="$ROOT/memory.pressure" \
  "$RELEASE_SCRIPT" "$REL_REPO" release-test.log --venv "$ROOT/venv" -- project --set-version 1.2.3 \
  > "$ROOT/release.out" 2>&1; then fail 'release child failure was not propagated'; else rc=$?; [[ $rc == 7 ]] || fail "release exit was $rc, expected 7"; fi
contains "$ROOT/controller/logs/release-test.log" 'EXIT=7' 'release wrapper writes the child exit marker'
contains "$CMRU_LOG" 'release project --set-version 1.2.3' 'release argv is preserved'
archive=$(find "$ROOT/controller/releases-archive" -mindepth 1 -maxdepth 1 -type d -print -quit)
[[ -n $archive ]] || fail 'rel-* worktree evidence was not archived'
contains "$archive/project/logs/run.log" 'log evidence' 'project logs are archived before release'
contains "$archive/project/artifacts/output.txt" 'build output' 'project artifacts are archived before release'
contains "$archive/project/evidence/gate.txt" 'gate evidence' 'project evidence is archived before release'
printf 'dirty\n' > "$REL_REPO/untracked.txt"
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CMRU_LOG="$CMRU_LOG" RELEASE_PSI_FILE="$ROOT/memory.pressure" \
  "$RELEASE_SCRIPT" "$REL_REPO" dirty.log --venv "$ROOT/venv" -- project \
  > "$ROOT/release-dirty.out" 2>&1; then fail 'dirty release worktree was accepted'; fi
contains "$ROOT/release-dirty.out" 'refusing release from dirty worktree' 'release requires a clean worktree'

PATH="$ROOT/bin:$PATH" HX_SCRIPT="$ROOT/bin/fake-hx" HX_LOG="$HX_LOG" \
  "$PUSH_IMAGE_SCRIPT" runner-a tester-unified:local > "$ROOT/push.out" 2>&1 || fail 'image transfer failed'
contains "$ROOT/push.out" 'PUSH-EXIT=0/0/0' 'image transfer reports each pipeline exit'
contains "$ROOT/push.out" 'remote-image' 'image transfer checks the remote image'
rm -f -- "$ROOT/hx.log"
if PATH="$ROOT/bin:$PATH" HX_SCRIPT="$ROOT/bin/fake-hx" HX_LOG="$HX_LOG" HX_LOAD_RC=8 \
  "$PUSH_IMAGE_SCRIPT" runner-a tester-unified:local > "$ROOT/push-fail.out" 2>&1; then
  fail 'failed remote image load was accepted'
fi
contains "$ROOT/push-fail.out" 'PUSH-EXIT=0/0/8' 'pipeline captures the remote loader failure'
if "$PUSH_IMAGE_SCRIPT" runner-a 'image with spaces' > "$ROOT/push-invalid.out" 2>&1; then
  fail 'invalid image reference was accepted'
fi

echo 'PASS wrappers: identity SSH, route handling, child exit markers, release archive, pipeline status'
