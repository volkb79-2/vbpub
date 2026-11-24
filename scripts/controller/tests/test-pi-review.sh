#!/usr/bin/env bash
set -uo pipefail
# The caller's ambient controller configuration is not this check's business:
# every check here builds its own temporary controller root, so an exported
# CONTROLLER_HOME / RUNS_DIR / LOGS_DIR / REPO / HOSTS_FILE must not decide where
# a check reads or writes.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
unset PI_ROUTE PI_MODEL PI_THINKING PI_SESSION_DIR PI_LIVE PI_REVIEW_MODEL PI_REVIEW_THINKING
unset OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2 CONTROLLER_OD_STATE
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SRC=${PI_REVIEW_SCRIPT:-$SCRIPT_DIR/../pi-review.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-pi-review-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
lacks() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }

HOME_ROOT=$ROOT/home
CONTROLLER_ROOT=$ROOT/controller
RUNS=$CONTROLLER_ROOT/runs
WORKTREE=$ROOT/worktree
PROMPT=$ROOT/brief.md
REPORTS=$ROOT/reports
STATE=$ROOT/opendesign-state.json
PI_ARGV_LOG=$ROOT/pi-argv.log
PI_PROMPT_LOG=$ROOT/pi-prompt.log
mkdir -p "$HOME_ROOT" "$ROOT/bin" "$CONTROLLER_ROOT" "$RUNS" "$REPORTS" "$WORKTREE"
printf 'review brief\n' > "$PROMPT"
# The reviewed worktree is a real repository with a clean tree: the read-only
# guard reads HEAD, status and a diff hash from git, so a fake directory
# cannot answer it.
git -C "$WORKTREE" init -q
git -C "$WORKTREE" config user.name test
git -C "$WORKTREE" config user.email test@example.invalid
printf 'baseline\n' > "$WORKTREE/tracked.txt"
git -C "$WORKTREE" add tracked.txt
git -C "$WORKTREE" commit -qm baseline
git -C "$WORKTREE" update-ref refs/remotes/origin/main HEAD

# A local controller configuration that asks for the AUTOMATIC route: the
# wrapper's pinned `deepseek-pi-<other>` must win over it, or a review would
# silently re-select an account from this file instead of the other one.
cat > "$CONTROLLER_ROOT/controller.local.env" <<'ENV'
PI_ROUTE="deepseek-pi"
PI_MODEL="deepseek-v4.1-flash-eco"
PI_THINKING="max"
PI_SESSION_DIR="$HOME/.pi/agent/sessions"
ENV
cat > "$HOME_ROOT/.env.ai" <<'ENV'
export OPENDESIGN_API_KEY_VOLKER=test-key
export OPENDESIGN_API_KEY_GITHUB2=test-key
ENV

# The implementer run records the wrapper resolves the OTHER account from.
printf 'deepseek-pi-volker\n' > "$RUNS/impl-volker.route"
printf 'deepseek-pi-github2\n' > "$RUNS/impl-github2.route"
# `deepseek-pi` selected an account at start without recording it as a pin, so
# the account has to come from the session thread.
printf 'deepseek-pi\n' > "$RUNS/impl-thread.route"
printf 'impl-thread-session\n' > "$RUNS/impl-thread.thread"
# A Codex route names no Open Design account at all, and has no thread.
printf 'luna\n' > "$RUNS/impl-luna.route"
# A pi route with a thread the shared state knows nothing about.
printf 'deepseek-pi\n' > "$RUNS/impl-thread-unknown.route"
printf 'ghost-session\n' > "$RUNS/impl-thread-unknown.thread"
python3 - "$STATE" <<'PY'
import json, sys
json.dump({
    "volker": {"last_used_epoch": 0, "blocked_until_epoch": 0},
    "github2": {"last_used_epoch": 0, "blocked_until_epoch": 0},
    "threads": {"impl-thread-session": "volker"},
}, open(sys.argv[1], "w", encoding="utf-8"), sort_keys=True, indent=2)
PY

# The fake pi: it emits the measured success stream, optionally writes the
# review report, and optionally performs what a misbehaving reviewer does to
# the worktree (edit a tracked file, create an untracked one, commit).
cat > "$ROOT/bin/pi" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$PI_ARGV_LOG"
printf '%s' "${!#}" > "$PI_PROMPT_LOG"
# The wrapper must give pi closed stdin; this returns at EOF.
while IFS= read -r _line; do :; done
cat -- "$FAKE_PI_STREAM"
if [[ -n ${FAKE_PI_REPORT:-} ]]; then printf 'review report\n' > "$FAKE_PI_REPORT"; fi
case ${FAKE_PI_ACTION:-none} in
  none) ;;
  write) printf 'reviewer edit\n' >> "$FAKE_PI_EDIT" ;;
  untracked) printf 'untracked file\n' > "$FAKE_PI_UNTRACKED" ;;
  commit)
    printf 'reviewer commit\n' >> "$FAKE_PI_EDIT"
    git -C "$FAKE_PI_WORKTREE" commit -qam 'reviewer commit'
    ;;
  *) echo "unknown FAKE_PI_ACTION=${FAKE_PI_ACTION}" >&2; exit 1 ;;
esac
exit "${FAKE_PI_RC:-0}"
SH
chmod +x "$ROOT/bin/pi"

export CONTROLLER_HOME=$CONTROLLER_ROOT CONTROLLER_REPO=$WORKTREE
export PI_ARGV_LOG PI_PROMPT_LOG

# One review launch. $1 run name, $2 write-the-report 1|0, $3 the fake
# reviewer's worktree action, the rest the account selector (`--implementer-run
# <run>` or `--account volker|github2`) and any further wrapper arguments.
launch() {
  local run=$1 writes=$2 action=$3
  shift 3
  local report=$REPORTS/$run.md fake_report=''
  (( writes == 1 )) && fake_report=$report
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_REPORT="$fake_report" \
    FAKE_PI_ACTION="$action" FAKE_PI_EDIT="$WORKTREE/tracked.txt" \
    FAKE_PI_UNTRACKED="$WORKTREE/untracked.txt" FAKE_PI_WORKTREE="$WORKTREE" \
    "$SRC" "$WORKTREE" "$PROMPT" "$run" --report "$report" "$@" \
    > "$ROOT/$run.launch.log" 2>&1 < /dev/null
}

last_argv() { grep -- '^-p ' "$PI_ARGV_LOG" 2>/dev/null | tail -n 1 || :; }
argv_has() {
  local argv
  argv=$(last_argv)
  [[ $argv == *"$1"* ]] || fail "$2 (argv: ${argv:-<none>})"
}
pi_invocations() {
  local count=0
  [[ -f $PI_ARGV_LOG ]] && count=$(grep -c -- '^-p ' "$PI_ARGV_LOG" || true)
  echo "${count:-0}"
}
done_line() {
  [[ -f $RUNS/$1.done ]] || fail "run $1 wrote no completion marker"
  head -n 1 "$RUNS/$1.done"
}
clean_worktree() {
  git -C "$WORKTREE" checkout -q -- . 2>/dev/null || :
  rm -f -- "$WORKTREE/untracked.txt"
}

# --- the account the reviewer runs on ---------------------------------------

# A Volker implementation is reviewed on github2.
check_other_account_for_volker() {
  local rc=0
  launch rev-from-volker 1 none --implementer-run impl-volker || rc=$?
  if (( rc != 0 )); then
    cat "$ROOT/rev-from-volker.launch.log" >&2
    fail "a clean review of a Volker implementation exited $rc"
  fi
  [[ $(cat "$RUNS/rev-from-volker.route") == deepseek-pi-github2 ]] \
    || fail 'a Volker implementation was not reviewed on github2'
  argv_has '--model opendesign_github2/' 'the reviewer model comes from the other account provider'
  argv_has '--thinking max' 'the reviewer runs at the catalog maximum thinking level'
  [[ $(done_line rev-from-volker) == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a clean review with its report is not SUCCESS'
  [[ -f $REPORTS/rev-from-volker.md ]] || fail 'the review report is present'
  lacks "$RUNS/rev-from-volker.done" REVIEW-MODIFIED-TREE 'a clean review does not flag the tree'
}

# A github2 implementation is reviewed on volker.
check_other_account_for_github2() {
  local rc=0
  launch rev-from-github2 1 none --implementer-run impl-github2 || rc=$?
  if (( rc != 0 )); then
    cat "$ROOT/rev-from-github2.launch.log" >&2
    fail "a clean review of a github2 implementation exited $rc"
  fi
  [[ $(cat "$RUNS/rev-from-github2.route") == deepseek-pi-volker ]] \
    || fail 'a github2 implementation was not reviewed on volker'
  argv_has '--model opendesign_volker/' 'the reviewer model comes from the Volker provider'
}

# An automatic implementer route records the account only on its session
# thread; the reviewer must still land on the other one.
check_other_account_from_thread() {
  local rc=0
  launch rev-from-thread 1 none --implementer-run impl-thread || rc=$?
  if (( rc != 0 )); then
    cat "$ROOT/rev-from-thread.launch.log" >&2
    fail "a review of a thread-recorded Volker run exited $rc"
  fi
  [[ $(cat "$RUNS/rev-from-thread.route") == deepseek-pi-github2 ]] \
    || fail 'a thread-recorded Volker run was not reviewed on github2'
}

# An implementer account that does not exist must refuse (exit 2) and start
# nothing: the wrapper never falls back to the implementer's own account.
check_unknown_implementer_account() {
  local before rc=0
  before=$(pi_invocations)
  launch refused-luna 1 none --implementer-run impl-luna || rc=$?
  (( rc == 2 )) || fail "an implementer run naming no account exited $rc, expected 2"
  contains "$ROOT/refused-luna.launch.log" 'cannot determine the Open Design account of implementer run impl-luna' \
    'the unknown implementer account is what refused the review'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with an unknown implementer account'
}

# The same refusal when the thread record exists but names no account.
check_unknown_thread_account() {
  local before rc=0
  before=$(pi_invocations)
  launch refused-thread 1 none --implementer-run impl-thread-unknown || rc=$?
  (( rc == 2 )) || fail "an unresolvable implementer thread exited $rc, expected 2"
  contains "$ROOT/refused-thread.launch.log" 'no Open Design account is recorded for implementer thread ghost-session' \
    'the unresolvable thread is what refused the review'
  lacks "$ROOT/refused-thread.launch.log" 'lookup failed' 'a lookup that found nothing is not reported as a failed lookup'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with an unresolvable implementer thread'
}

# --account pins the reviewer directly, without any implementer record.
check_account_pinning() {
  local rc=0
  launch rev-pinned 1 none --account volker || rc=$?
  if (( rc != 0 )); then
    cat "$ROOT/rev-pinned.launch.log" >&2
    fail "a clean pinned review exited $rc"
  fi
  [[ $(cat "$RUNS/rev-pinned.route") == deepseek-pi-volker ]] \
    || fail '--account did not pin the review account'
  argv_has '--model opendesign_volker/' 'the pinned account selects its provider'
}

# --- the reviewed worktree is left exactly as it was ------------------------

# A reviewer that edits a tracked file is caught, and its edit is NOT reverted.
check_modified_tracked_file() {
  local rc=0
  launch rev-write 1 write --implementer-run impl-volker || rc=$?
  (( rc == 4 )) || fail "a reviewer that edited a tracked file exited $rc, expected 4"
  contains "$RUNS/rev-write.done" 'REVIEW-MODIFIED-TREE' 'an edited tracked file is flagged in the completion marker'
  contains "$ROOT/rev-write.launch.log" 'REVIEW-MODIFIED-TREE' 'the flag is printed, not only recorded'
  contains "$WORKTREE/tracked.txt" 'reviewer edit' 'the wrapper did not revert the reviewer edit'
  clean_worktree
}

# An untracked file is a change too: the reviewer could leave a scratch tree.
check_modified_untracked_file() {
  local rc=0
  launch rev-untracked 1 untracked --implementer-run impl-github2 || rc=$?
  (( rc == 4 )) || fail "a reviewer that added an untracked file exited $rc, expected 4"
  contains "$RUNS/rev-untracked.done" 'REVIEW-MODIFIED-TREE' 'an added untracked file is flagged'
  [[ -f $WORKTREE/untracked.txt ]] || fail 'the wrapper removed the untracked file itself'
  clean_worktree
}

# A reviewer that COMMITS its change leaves a clean tree, which pi-impl.sh's
# own outcome classification calls SUCCESS. Only the HEAD comparison catches it.
check_modified_head() {
  local rc=0
  launch rev-commit 1 commit --implementer-run impl-volker || rc=$?
  (( rc == 4 )) || fail "a reviewer that committed exited $rc, expected 4"
  [[ $(done_line rev-commit) == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'the fixture did not reproduce a reviewer commit that pi-impl.sh calls SUCCESS'
  contains "$RUNS/rev-commit.done" 'REVIEW-MODIFIED-TREE' 'a reviewer commit is flagged in the completion marker'
  contains "$ROOT/rev-commit.launch.log" 'HEAD' 'the printed flag names the moved HEAD'
}

# A review that writes no review file is pi-impl.sh's NO-REPORT, passed through.
check_no_report() {
  local rc=0
  launch rev-no-report 0 none --implementer-run impl-github2 || rc=$?
  (( rc == 0 )) || fail "a review that wrote no report exited $rc, expected pi-impl's 0"
  [[ $(done_line rev-no-report) == 'EXIT=0 OUTCOME=NO-REPORT' ]] \
    || fail 'a review that wrote no review file is not NO-REPORT'
  lacks "$RUNS/rev-no-report.done" REVIEW-MODIFIED-TREE 'a NO-REPORT review did not flag the tree'
}

# A pre-existing dirty change is not charged to the reviewer: the comparison
# is against the tree's OWN before-state, so only a difference is flagged.
# pi-impl.sh still reports OUTCOME=UNCOMMITTED, which is pi-impl's word about
# the worktree and not about who changed it.
check_dirty_before_state_allowed() {
  local rc=0
  printf 'pre-existing change\n' >> "$WORKTREE/tracked.txt"
  launch rev-dirty 1 none --implementer-run impl-volker || rc=$?
  (( rc == 0 )) || fail "a review over a pre-existing dirty tree exited $rc, expected 0"
  [[ $(done_line rev-dirty) == 'EXIT=0 OUTCOME=UNCOMMITTED' ]] \
    || fail "a review over a dirty tree did not keep pi-impl's UNCOMMITTED word"
  lacks "$RUNS/rev-dirty.done" REVIEW-MODIFIED-TREE 'a pre-existing change was charged to the reviewer'
  clean_worktree
}

# A worktree whose BEFORE-state cannot be read is refused before pi starts.
check_unreadable_before_state_refused() {
  local shim=$ROOT/git-shim real_git before rc=0
  real_git=$(command -v git)
  mkdir -p "$shim"
  cat > "$shim/git" <<SH
#!/usr/bin/env bash
for a in "\$@"; do [[ \$a == status ]] && exit 128; done
exec $real_git "\$@"
SH
  chmod +x "$shim/git"
  before=$(pi_invocations)
  PATH="$shim:$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    "$SRC" "$WORKTREE" "$PROMPT" rev-unreadable --report "$REPORTS/rev-unreadable.md" \
    --account volker > "$ROOT/rev-unreadable.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 2 )) || fail "an unreadable before-state exited $rc, expected 2"
  contains "$ROOT/rev-unreadable.launch.log" 'cannot record the worktree state before the review' \
    'the unreadable before-state is what refused the review'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started without a readable before-state'
}

# --- refusals ---------------------------------------------------------------

# A report path inside the reviewed worktree cannot be proven unused.
check_report_inside_worktree() {
  local before rc=0
  before=$(pi_invocations)
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    "$SRC" "$WORKTREE" "$PROMPT" rev-inside --report "$WORKTREE/review.md" --account volker \
    > "$ROOT/rev-inside.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 2 )) || fail "a report inside the worktree exited $rc, expected 2"
  contains "$ROOT/rev-inside.launch.log" 'report path is inside the reviewed worktree' \
    'the in-worktree report path is what refused the review'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with its report inside the worktree'
}

check_account_and_implementer_conflict() {
  local before rc=0
  before=$(pi_invocations)
  launch refused-both 1 none --implementer-run impl-volker --account volker || rc=$?
  (( rc == 2 )) || fail "both account selectors exited $rc, expected 2"
  contains "$ROOT/refused-both.launch.log" 'not both' 'two account selectors are refused'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with two account selectors'
}

check_account_required() {
  local before rc=0
  before=$(pi_invocations)
  launch refused-none 1 none || rc=$?
  (( rc == 2 )) || fail "a missing account selector exited $rc, expected 2"
  contains "$ROOT/refused-none.launch.log" 'give one of --implementer-run' 'a missing selector is refused'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started without an account selector'
}

check_unknown_account_value() {
  local before rc=0
  before=$(pi_invocations)
  launch refused-account 1 none --account luna || rc=$?
  (( rc == 2 )) || fail "an unknown account value exited $rc, expected 2"
  contains "$ROOT/refused-account.launch.log" 'unknown account: luna' 'an unknown account value is refused'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with an unknown account value'
}

check_report_required() {
  local before rc=0
  before=$(pi_invocations)
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    "$SRC" "$WORKTREE" "$PROMPT" rev-no-report-path --account volker \
    > "$ROOT/rev-no-report-path.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 2 )) || fail "a review without --report exited $rc, expected 2"
  contains "$ROOT/rev-no-report-path.launch.log" 'usage: ' 'a review must name its report path'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started without a report path'
}

# --- documentation ---------------------------------------------------------

check_docs() {
  local readme=$SCRIPT_DIR/../README.md changes=$SCRIPT_DIR/../CHANGES.md
  contains "$readme" 'pi-review.sh' 'README names the pi review launcher'
  contains "$readme" 'REVIEW-MODIFIED-TREE' 'README documents the modified-tree flag'
  contains "$changes" 'pi-review.sh' 'CHANGES.md records the pi review launcher'
}

check_other_account_for_volker
check_other_account_for_github2
check_other_account_from_thread
check_unknown_implementer_account
check_unknown_thread_account
check_account_pinning
check_modified_tracked_file
check_modified_untracked_file
check_no_report
check_dirty_before_state_allowed
check_unreadable_before_state_refused
check_report_inside_worktree
check_account_and_implementer_conflict
check_account_required
check_unknown_account_value
check_report_required
check_modified_head
check_docs

[[ -z $(git -C "$WORKTREE" status --porcelain) ]] \
  || fail 'the reviewed worktree is not clean after the checks'
echo 'PASS pi-review: other-account selection, thread lookup, refusals, read-only tree guard (tracked/untracked/HEAD), report passthrough'
