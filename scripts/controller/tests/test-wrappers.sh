#!/usr/bin/env bash
set -uo pipefail
unset CODEX_ROUTE CODEX_HOME_OVERRIDE CODEX_HOME_LUNA CODEX_HOME_DEEPSEEK CODEX_HOME_REVIEW CODEX_MODEL CODEX_EFFORT
unset CODEX_REVIEW_MODEL CODEX_REVIEW_EFFORT CODEX_SQLITE_HOME
unset OPENCODE_COMMAND OPENCODE_MODEL
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
printf '{"type":"item.completed","item":{"type":"agent_message","text":"complete"}}\n'
exit "${CODEX_RC:-0}"
SH
cat > "$ROOT/bin/opencode" <<'SH'
#!/usr/bin/env bash
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
export API_LOG=$ROOT/api.log SSH_LOG=$ROOT/ssh.log CODEX_LOG=$ROOT/codex.log CMRU_LOG=$ROOT/cmru.log HX_LOG=$ROOT/hx.log
HX_WRAPPER=${HX_WRAPPER:-$SCRIPT_DIR/../hx.sh}
RELEASE_SCRIPT=${RELEASE_SCRIPT:-$SCRIPT_DIR/../release-from-wt.sh}
PUSH_IMAGE_SCRIPT=${PUSH_IMAGE_SCRIPT:-$SCRIPT_DIR/../push-image.sh}
CODEX_IMPL_SCRIPT=${CODEX_IMPL_SCRIPT:-$SCRIPT_DIR/../codex-impl.sh}

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
mkdir -p "$ROOT/codex-deepseek/catalog"
printf '{"models":[{"name":"fixture","tool_mode":"enabled"}]}\n' > "$ROOT/codex-deepseek/catalog/opendesign-models.json"
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_ROUTE=deepseek CODEX_HOME_DEEPSEEK="$ROOT/codex-deepseek" CODEX_LOG="$ROOT/deepseek-codex.log" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" deepseek-catalog > "$ROOT/deepseek-catalog.out" 2>&1; then
  fail 'DeepSeek catalog with tool_mode was accepted'
else rc=$?; [[ $rc == 3 ]] || fail "unsafe DeepSeek catalog exit was $rc, expected 3"; fi
[[ ! -e $ROOT/deepseek-codex.log ]] || fail 'Codex ran after the DeepSeek catalog guard refused the catalog'
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_ROUTE=unknown CODEX_LOG="$CODEX_LOG" \
  "$SCRIPT_DIR/../codex-impl.sh" "$ROOT/worktree" "$ROOT/brief.md" bad-route > "$ROOT/route.out" 2>&1; then
  fail 'unknown Codex route was accepted'
fi
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_HOME_REVIEW="$ROOT/codex-review" CODEX_LOG="$CODEX_LOG" \
  "$SCRIPT_DIR/../codex-review.sh" "$ROOT/worktree" "$ROOT/brief.md" review-run > "$ROOT/review.out" 2>&1; then :; else fail 'Codex review wrapper failed'; fi
contains "$CODEX_LOG" "HOME=$ROOT/codex-review" 'review uses its separately configured profile'
contains "$CODEX_LOG" 'gpt-6-sol' 'review uses the independent review model default'

PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  OPENCODE_COMMAND="$ROOT/bin/opencode" \
  "$SCRIPT_DIR/../opencode-impl.sh" "$ROOT/worktree" "$ROOT/brief.md" opencode-run > "$ROOT/opencode.out" 2>&1 \
  || fail 'OpenCode wrapper failed'
contains "$ROOT/controller/runs/opencode-run.last.txt" opencode-complete 'OpenCode final text is extracted'
contains "$ROOT/controller/runs/opencode-run.done" 'EXIT=0' 'OpenCode completion is persisted'

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
