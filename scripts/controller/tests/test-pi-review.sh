#!/usr/bin/env bash
set -uo pipefail
# The caller's ambient controller configuration is not this check's business:
# every check here builds its own temporary controller root, so an exported
# CONTROLLER_HOME / RUNS_DIR / LOGS_DIR / REPO / HOSTS_FILE must not decide where
# a check reads or writes.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
unset PI_ROUTE PI_MODEL PI_THINKING PI_SESSION_DIR PI_LIVE
unset OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2 CONTROLLER_OD_STATE
unset PI_EXTRA_ARGS PI_REVIEW_MAX_SECONDS PI_REVIEW_MAX_STREAM_BYTES PI_REVIEW_POLL_SECONDS PI_REVIEW_KILL_WAIT_SECONDS
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
ORIGINAL_BRANCH=$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD)
cp -- "$WORKTREE/.git/info/exclude" "$ROOT/exclude.orig"

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

# The fake pi: it emits the measured success stream, records its argv, and
# optionally performs what a misbehaving reviewer does to the worktree (edit a
# tracked file, create an untracked one, commit, switch branch, hide an edit
# behind an index flag, hide a file behind info/exclude).
cat > "$ROOT/bin/pi" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$PI_ARGV_LOG"
# One argument per NUL-terminated record: a tool list is ONE argv element
# ("read,grep,find,ls"), and a space-joined log cannot show whether it arrived
# as one value or as five. The read-only-tool-set checks read this log.
for arg in "$@"; do printf '%s\0' "$arg"; done >> "$PI_ARGV_LOG0"
printf '%s' "${!#}" > "$PI_PROMPT_LOG"
# The wrapper must give pi closed stdin; this returns at EOF.
while IFS= read -r _line; do :; done
cat -- "$FAKE_PI_STREAM"
if [[ -n ${FAKE_PI_BURST_BYTES:-} ]]; then
  # A stream that grows past a budget without the run ending: the 3 GB echo
  # loop shape, at a size the test can afford.
  head -c "$FAKE_PI_BURST_BYTES" /dev/zero | tr '\0' 'x' >> "${FAKE_PI_BURST_TARGET:-/dev/null}"
fi
case ${FAKE_PI_ACTION:-none} in
  none) ;;
  write) printf 'reviewer edit\n' >> "$FAKE_PI_EDIT" ;;
  untracked) printf 'untracked file\n' > "$FAKE_PI_UNTRACKED" ;;
  untracked-content) printf 'changed after launch\n' > "$FAKE_PI_UNTRACKED" ;;
  untracked-in-dir) printf 'second file\n' > "$FAKE_PI_UNTRACKED_DIR/second.txt" ;;
  commit)
    printf 'reviewer commit\n' >> "$FAKE_PI_EDIT"
    git -C "$FAKE_PI_WORKTREE" commit -qam 'reviewer commit'
    ;;
  switch) git -C "$FAKE_PI_WORKTREE" switch -qc reviewer-scratch ;;
  assume-unchanged)
    printf 'reviewer edit\n' >> "$FAKE_PI_EDIT"
    git -C "$FAKE_PI_WORKTREE" update-index --assume-unchanged -- tracked.txt
    ;;
  exclude) printf 'hidden.txt\n' >> "$FAKE_PI_EXCLUDE" ;;
  slow) sleep "${FAKE_PI_SLOW_SECONDS:-2}" ;;
  *) echo "unknown FAKE_PI_ACTION=${FAKE_PI_ACTION}" >&2; exit 1 ;;
esac
exit "${FAKE_PI_RC:-0}"
SH
chmod +x "$ROOT/bin/pi"

export CONTROLLER_HOME=$CONTROLLER_ROOT CONTROLLER_REPO=$WORKTREE
export PI_ARGV_LOG PI_PROMPT_LOG
PI_ARGV_LOG0=$ROOT/pi-argv0.log
export PI_ARGV_LOG0

# One review launch. $1 run name, $2 write-the-report 1|0, $3 the fake
# reviewer's worktree action, the rest the account selector (`--implementer-run
# <run>` or `--account volker|github2`) and any further wrapper arguments.
launch() {
  local run=$1 action=$2
  shift 2
  local report=$REPORTS/$run.md
  : > "$PI_ARGV_LOG0"
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="${FAKE_PI_STREAM_TEST:-$SCRIPT_DIR/fixtures/pi-success.jsonl}" \
      PI_EXTRA_ARGS="${PI_EXTRA_ARGS_TEST:-}" \
    FAKE_PI_ACTION="$action" FAKE_PI_EDIT="$WORKTREE/tracked.txt" \
    FAKE_PI_SLOW_SECONDS="${FAKE_PI_SLOW_SECONDS_TEST:-${FAKE_PI_SLOW_SECONDS:-2}}" \
    FAKE_PI_UNTRACKED="$WORKTREE/untracked.txt" FAKE_PI_WORKTREE="$WORKTREE" \
    FAKE_PI_UNTRACKED_DIR="$WORKTREE/scratch" \
    FAKE_PI_EXCLUDE="$WORKTREE/.git/info/exclude" \
    PI_REVIEW_MAX_SECONDS="${PI_REVIEW_MAX_SECONDS_TEST:-7200}" \
    PI_REVIEW_MAX_STREAM_BYTES="${PI_REVIEW_MAX_STREAM_BYTES_TEST:-268435456}" \
    PI_REVIEW_POLL_SECONDS="${PI_REVIEW_POLL_SECONDS_TEST:-15}" \
    PI_REVIEW_KILL_WAIT_SECONDS="${PI_REVIEW_KILL_WAIT_SECONDS_TEST:-10}" \
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
  launch rev-from-volker none --implementer-run impl-volker || rc=$?
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
  contains "$REPORTS/rev-from-volker.md" 'Completed the fixture task.' \
    'the report is the reviewer final message, saved by the launcher'
  [[ -f $REPORTS/rev-from-volker.md ]] || fail 'the review report is present'
  lacks "$RUNS/rev-from-volker.done" REVIEW-MODIFIED-TREE 'a clean review does not flag the tree'
}

# A github2 implementation is reviewed on volker.
check_other_account_for_github2() {
  local rc=0
  launch rev-from-github2 none --implementer-run impl-github2 || rc=$?
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
  launch rev-from-thread none --implementer-run impl-thread || rc=$?
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
  launch refused-luna none --implementer-run impl-luna || rc=$?
  (( rc == 2 )) || fail "an implementer run naming no account exited $rc, expected 2"
  contains "$ROOT/refused-luna.launch.log" 'cannot determine the Open Design account of implementer run impl-luna' \
    'the unknown implementer account is what refused the review'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with an unknown implementer account'
}

# The same refusal when the thread record exists but names no account.
check_unknown_thread_account() {
  local before rc=0
  before=$(pi_invocations)
  launch refused-thread none --implementer-run impl-thread-unknown || rc=$?
  (( rc == 2 )) || fail "an unresolvable implementer thread exited $rc, expected 2"
  contains "$ROOT/refused-thread.launch.log" 'no Open Design account is recorded for implementer thread ghost-session' \
    'the unresolvable thread is what refused the review'
  lacks "$ROOT/refused-thread.launch.log" 'lookup failed' 'a lookup that found nothing is not reported as a failed lookup'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with an unresolvable implementer thread'
}

# --account pins the reviewer directly, without any implementer record.
check_account_pinning() {
  local rc=0
  launch rev-pinned none --account volker || rc=$?
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
  launch rev-write write --implementer-run impl-volker || rc=$?
  (( rc == 4 )) || fail "a reviewer that edited a tracked file exited $rc, expected 4"
  contains "$RUNS/rev-write.done" 'REVIEW-MODIFIED-TREE' 'an edited tracked file is flagged in the completion marker'
  contains "$ROOT/rev-write.launch.log" 'REVIEW-MODIFIED-TREE' 'the flag is printed, not only recorded'
  contains "$WORKTREE/tracked.txt" 'reviewer edit' 'the wrapper did not revert the reviewer edit'
  clean_worktree
}

# An untracked file is a change too: the reviewer could leave a scratch tree.
check_modified_untracked_file() {
  local rc=0
  launch rev-untracked untracked --implementer-run impl-github2 || rc=$?
  (( rc == 4 )) || fail "a reviewer that added an untracked file exited $rc, expected 4"
  contains "$RUNS/rev-untracked.done" 'REVIEW-MODIFIED-TREE' 'an added untracked file is flagged'
  [[ -f $WORKTREE/untracked.txt ]] || fail 'the wrapper removed the untracked file itself'
  clean_worktree
}

# A reviewer that COMMITS its change leaves a clean tree, which pi-impl.sh's
# own outcome classification calls SUCCESS. Only the HEAD comparison catches it.
check_modified_head() {
  local rc=0
  launch rev-commit commit --implementer-run impl-volker || rc=$?
  (( rc == 4 )) || fail "a reviewer that committed exited $rc, expected 4"
  [[ $(done_line rev-commit) == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'the fixture did not reproduce a reviewer commit that pi-impl.sh calls SUCCESS'
  contains "$RUNS/rev-commit.done" 'REVIEW-MODIFIED-TREE' 'a reviewer commit is flagged in the completion marker'
  contains "$ROOT/rev-commit.launch.log" 'HEAD' 'the printed flag names the moved HEAD'
}


# A pre-existing dirty change is not charged to the reviewer: the comparison
# is against the tree's OWN before-state, so only a difference is flagged.
# pi-impl.sh still reports OUTCOME=UNCOMMITTED, which is pi-impl's word about
# the worktree and not about who changed it.
check_dirty_before_state_allowed() {
  local rc=0
  printf 'pre-existing change\n' >> "$WORKTREE/tracked.txt"
  launch rev-dirty none --implementer-run impl-volker || rc=$?
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
  launch refused-both none --implementer-run impl-volker --account volker || rc=$?
  (( rc == 2 )) || fail "both account selectors exited $rc, expected 2"
  contains "$ROOT/refused-both.launch.log" 'not both' 'two account selectors are refused'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with two account selectors'
}

check_account_required() {
  local before rc=0
  before=$(pi_invocations)
  launch refused-none none || rc=$?
  (( rc == 2 )) || fail "a missing account selector exited $rc, expected 2"
  contains "$ROOT/refused-none.launch.log" 'give one of --implementer-run' 'a missing selector is refused'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started without an account selector'
}

check_unknown_account_value() {
  local before rc=0
  before=$(pi_invocations)
  launch refused-account none --account luna || rc=$?
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

# --- the read-only tool set -------------------------------------------------

argv0_elements() {
  # One argv element per NUL record; an unset log means pi never ran.
  [[ -s $PI_ARGV_LOG0 ]] || fail "pi never ran (no argv log at $PI_ARGV_LOG0)"
  mapfile -d '' -t ARGV0 < "$PI_ARGV_LOG0"
}
argv0_has() {
  argv0_elements
  local element found=0
  for element in "${ARGV0[@]}"; do [[ $element == "$1" ]] && found=1; done
  (( found == 1 )) || fail "$2 (argv: ${ARGV0[*]})"
}
argv0_lacks() {
  argv0_elements
  local element
  for element in "${ARGV0[@]}"; do
    [[ $element == "$1" ]] && fail "$2 (argv: ${ARGV0[*]})"
  done
  return 0
}
argv0_pair() {
  argv0_elements
  local i found=0
  for (( i = 0; i + 1 < ${#ARGV0[@]}; i++ )); do
    [[ ${ARGV0[i]} == "$1" && ${ARGV0[i + 1]} == "$2" ]] && found=1
  done
  (( found == 1 )) || fail "$3 (argv: ${ARGV0[*]})"
}

# pi's docs: the default tools are read, bash, edit and write, and `--tools` REPLACES
# the selection. A review must therefore name the whole read-only set as one
# element and must not name a shell or a writer anywhere in its argv.
check_read_only_tool_set() {
  local rc=0
  launch rev-toolset none --implementer-run impl-volker || rc=$?
  if (( rc != 0 )); then
    cat "$ROOT/rev-toolset.launch.log" >&2
    fail "a review with the read-only tool set exited $rc"
  fi
  argv0_pair '--tools' 'read,grep,find,ls' '--tools names exactly the read-only set, as ONE argv element'
  argv0_pair '--exclude-tools' 'mcp__*' 'MCP tools are excluded for the review'
  argv0_has '--no-extensions' 'the review loads no extension'
  argv0_has '--no-mcp' 'the review connects no MCP server'
  argv0_lacks 'bash' 'the reviewer argv does not name bash'
  argv0_lacks 'edit' 'the reviewer argv does not name edit'
  argv0_lacks 'write' 'the reviewer argv does not name write'
  argv0_lacks 'powershell' 'the reviewer argv does not name powershell'
}

# A caller's own PI_EXTRA_ARGS must not widen a review back to a shell: the
# launcher replaces the variable, it does not merge with it.
check_caller_extra_args_replaced() {
  local rc=0
  local PI_EXTRA_ARGS_TEST=$'--tools\nbash,read'
  launch rev-extrawide none --implementer-run impl-github2 || rc=$?
  if (( rc != 0 )); then
    cat "$ROOT/rev-extrawide.launch.log" >&2
    fail "a review whose caller set PI_EXTRA_ARGS exited $rc"
  fi
  argv0_pair '--tools' 'read,grep,find,ls' 'the launcher REPLACES a caller PI_EXTRA_ARGS instead of merging it'
  argv0_lacks 'bash,read' 'a caller-provided tool list never reaches pi'
}

# pi-impl.sh refuses the launch contract it owns, before it claims the name.
check_extra_args_cannot_override_contract() {
  local before rc=0
  before=$(pi_invocations)
  PI_EXTRA_ARGS=$'--model\nluna/x' env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" \
    CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 PI_ROUTE=deepseek-pi-volker \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    "$SCRIPT_DIR/../pi-impl.sh" "$WORKTREE" "$PROMPT" rev-contract \
    > "$ROOT/rev-contract.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 2 )) || fail "PI_EXTRA_ARGS naming --model exited $rc, expected pi-impl's 2"
  contains "$ROOT/rev-contract.launch.log" "PI_EXTRA_ARGS may not set '--model'" \
    'pi-impl refuses an extra argument that would replace its own model'
  [[ $(pi_invocations) == "$before" ]] || fail 'a launch ran with a replaced model argument'
}

# L2: a non-empty PI_EXTRA_ARGS must be visible in the run's own log. It can
# arrive from the invoking caller (pi-review.sh) or, unnoticed, from a host's
# controller.local.env, which lib.sh sources with `set -a`: an implementer that
# silently lost `edit`/`write` would fail mid-task with no loud error.
check_extra_args_announced() {
  local rc=0
  PI_EXTRA_ARGS=$'--tools\nread,grep,find,ls' env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" \
    CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 PI_ROUTE=deepseek-pi-volker \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    "$SCRIPT_DIR/../pi-impl.sh" "$WORKTREE" "$PROMPT" rev-announce \
    > "$ROOT/rev-announce.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 0 )) || fail "a launch with a two-element PI_EXTRA_ARGS exited $rc"
  contains "$ROOT/rev-announce.launch.log" 'PI_EXTRA_ARGS adds 2 pi argument(s)' \
    'the seam is announced with its argument count'
  contains "$ROOT/rev-announce.launch.log" 'not the plain implementer argv' \
    'the announcement says this launch is not the plain implementer argv'
}


# A review that runs past its wall budget is STOPPED and marked REVIEW-BUDGET.
check_budget_wall_time() {
  local rc=0
  PI_REVIEW_MAX_SECONDS_TEST=1 PI_REVIEW_POLL_SECONDS_TEST=1 FAKE_PI_SLOW_SECONDS_TEST=10 \
    launch rev-budget-wall slow --account volker || rc=$?
  (( rc == 5 )) || fail "a review over its wall budget exited $rc, expected 5"
  contains "$ROOT/rev-budget-wall.launch.log" 'REVIEW-BUDGET' 'the budget stop is printed'
  contains "$ROOT/rev-budget-wall.launch.log" 'wall time' 'the msg names wall time as the reason'
  contains "$RUNS/rev-budget-wall.done" 'REVIEW-BUDGET' 'the run is marked REVIEW-BUDGET'
  [[ ! -e $REPORTS/rev-budget-wall.md ]] || fail 'a stopped review wrote a report'
}

# A stream that grows past its byte budget is the 3 GB echo loop shape.
check_budget_stream_bytes() {
  local rc=0
  PI_REVIEW_MAX_STREAM_BYTES_TEST=1000 PI_REVIEW_POLL_SECONDS_TEST=1 \
    FAKE_PI_BURST_BYTES=20000 FAKE_PI_BURST_TARGET="$RUNS/rev-budget-stream.jsonl" \
    FAKE_PI_SLOW_SECONDS_TEST=10 launch rev-budget-stream slow --implementer-run impl-volker || rc=$?
  (( rc == 5 )) || fail "a review over its stream budget exited $rc, expected 5"
  contains "$RUNS/rev-budget-stream.done" 'REVIEW-BUDGET' 'the run is marked REVIEW-BUDGET'
  contains "$ROOT/rev-budget-stream.launch.log" 'stream' 'the message names the stream size'
}

# A malformed budget value is a refusal, not an invented default.
check_budget_value_refused() {
  local before rc=0
  before=$(pi_invocations)
  PI_REVIEW_MAX_SECONDS_TEST=soon launch rev-budget-bad none --account volker || rc=$?
  (( rc == 2 )) || fail "a non-integer budget exited $rc, expected 2"
  contains "$ROOT/rev-budget-bad.launch.log" 'PI_REVIEW_MAX_SECONDS must be a non-negative integer' \
    'a non-integer budget is refused'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with a malformed budget'
}

# The default wall-clock budget is what an ordinary caller gets when it sets
# nothing. It was 1800 s, which stopped real max-effort reviews; the value is
# asserted from the script's own default, not from a test-side mirror.
check_budget_default_wall() {
  local rc=0
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    "$SRC" "$WORKTREE" "$PROMPT" rev-budget-default --report "$REPORTS/rev-budget-default.md" \
    --account volker > "$ROOT/rev-budget-default.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 0 )) || fail "a pinned review exited $rc"
  contains "$ROOT/rev-budget-default.launch.log" 'wall=7200s' \
    'the wall-clock budget defaults to 7200 s and is printed at launch'
  [[ -e $REPORTS/rev-budget-default.md ]] || fail 'the default-budget review wrote no report'
}

# --- a stale report ----------------------------------------------------------

# A retried review reuses its report path; the reviewer cannot rewrite the file,
# so an existing one would certify a run that produced nothing.
check_stale_report_refused() {
  local before rc=0
  printf 'previous run report\n' > "$REPORTS/rev-stale.md"
  before=$(pi_invocations)
  launch rev-stale none --account volker || rc=$?
  (( rc == 2 )) || fail "a review over an existing report exited $rc, expected 2"
  contains "$ROOT/rev-stale.launch.log" 'the review report already exists' \
    'an existing report path is what refused the review'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started over an existing report'
  [[ $(cat "$REPORTS/rev-stale.md") == 'previous run report' ]] || fail 'the stale report was overwritten'
  rm -f -- "$REPORTS/rev-stale.md"
}

# --- worktree-scoped state classes ------------------------------------------

# A branch switch at the SAME commit moves no sha, no status and no diff.
check_modified_head_branch() {
  local rc=0
  launch rev-switch switch --implementer-run impl-volker || rc=$?
  (( rc == 4 )) || fail "a reviewer that switched branch exited $rc, expected 4"
  contains "$RUNS/rev-switch.done" 'REVIEW-MODIFIED-TREE' 'a branch switch is flagged'
  contains "$ROOT/rev-switch.launch.log" 'head-branch' 'the printed flag names the symbolic HEAD'
  git -C "$WORKTREE" switch -q "$ORIGINAL_BRANCH" || :
  git -C "$WORKTREE" branch -qD reviewer-scratch 2>/dev/null || :
}

# `update-index --assume-unchanged` hides a tracked edit from status AND diff.
check_modified_index_flags() {
  local rc=0
  launch rev-index assume-unchanged --implementer-run impl-github2 || rc=$?
  (( rc == 4 )) || fail "a reviewer that hid an edit behind an index flag exited $rc, expected 4"
  contains "$RUNS/rev-index.done" 'REVIEW-MODIFIED-TREE' 'an index flag is flagged'
  contains "$ROOT/rev-index.launch.log" 'index-flags' 'the printed flag names the index entry flags'
  git -C "$WORKTREE" update-index --no-assume-unchanged -- tracked.txt 2>/dev/null || :
  git -C "$WORKTREE" update-index --really-refresh -q 2>/dev/null || :
  clean_worktree
}

# `info/exclude` is the other way to hide a new file from status.
check_modified_exclude() {
  local rc=0
  launch rev-exclude exclude --implementer-run impl-volker || rc=$?
  (( rc == 4 )) || fail "a reviewer that edited info/exclude exited $rc, expected 4"
  contains "$RUNS/rev-exclude.done" 'REVIEW-MODIFIED-TREE' 'an exclude change is flagged'
  contains "$ROOT/rev-exclude.launch.log" 'exclude' 'the printed flag names the exclude file'
  cp -- "$ROOT/exclude.orig" "$WORKTREE/.git/info/exclude"
}

# An edit to a file that was ALREADY untracked leaves the status line identical.
check_modified_untracked_content() {
  local rc=0
  printf 'implementer leftover\n' > "$WORKTREE/leftover.txt"
  launch rev-untracked-content untracked-content --implementer-run impl-github2 || rc=$?
  (( rc == 4 )) || fail "a reviewer that edited a pre-existing untracked file exited $rc, expected 4"
  contains "$RUNS/rev-untracked-content.done" 'REVIEW-MODIFIED-TREE' 'an untracked content change is flagged'
  contains "$ROOT/rev-untracked-content.launch.log" 'untracked-content' 'the printed flag names the untracked content'
  rm -f -- "$WORKTREE/leftover.txt"
  clean_worktree
}

# The diff leg on its own: a further edit to an ALREADY dirty tracked file keeps
# the same ` M` status line and only the diff hash changes.
check_modified_diff_of_dirty_file() {
  local rc=0
  printf 'pre-existing change\n' >> "$WORKTREE/tracked.txt"
  launch rev-dirty-diff write --implementer-run impl-volker || rc=$?
  (( rc == 4 )) || fail "a reviewer that edited an already-dirty file exited $rc, expected 4"
  contains "$RUNS/rev-dirty-diff.done" 'REVIEW-MODIFIED-TREE' 'a further edit to a dirty file is flagged'
  contains "$ROOT/rev-dirty-diff.launch.log" 'diff' 'the printed flag names the diff'
  clean_worktree
}


# `-uall`: a second file inside an ALREADY-untracked DIRECTORY leaves the plain
# porcelain line `?? scratch/` unchanged, so the STATUS leg needs the deep form.
# The flag names status specifically: without `-uall` only the untracked-content
# hash would differ and the message would not say "status".
check_untracked_in_untracked_dir() {
  local rc=0
  mkdir -p "$WORKTREE/scratch"
  printf 'first file\n' > "$WORKTREE/scratch/first.txt"
  launch rev-uall untracked-in-dir --implementer-run impl-github2 || rc=$?
  (( rc == 4 )) || fail "a file added inside an untracked directory exited $rc, expected 4"
  contains "$RUNS/rev-uall.done" 'REVIEW-MODIFIED-TREE' 'a deep untracked addition is flagged'
  contains "$ROOT/rev-uall.launch.log" 'status' 'the printed flag names the status comparison (-uall)'
  rm -rf -- "$WORKTREE/scratch"
  clean_worktree
}

# A worktree whose AFTER-state cannot be read cannot be certified: the flag is
# written with the reason, not a silent pass.
check_unreadable_after_state_flagged() {
  local shim=$ROOT/git-after real_git counter=$ROOT/git-after.count rc=0
  real_git=$(command -v git)
  mkdir -p "$shim"
  # status call 1 is the before-state, 2 is pi-impl's own classification, 3 is the
  # after-state: failing from the third on models a tree that stopped answering.
  cat > "$shim/git" <<SH
#!/usr/bin/env bash
is_status=0
for a in "\$@"; do [[ \$a == status ]] && is_status=1; done
if (( is_status == 1 )); then
  n=\$(( \$(cat "$counter" 2>/dev/null || printf 0) + 1 ))
  printf '%s' "\$n" > "$counter"
  (( n >= 3 )) && exit 1
fi
exec $real_git "\$@"
SH
  chmod +x "$shim/git"
  rm -f -- "$counter"
  PATH="$shim:$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    "$SRC" "$WORKTREE" "$PROMPT" rev-after-unreadable --report "$REPORTS/rev-after-unreadable.md" \
    --account volker > "$ROOT/rev-after-unreadable.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 4 )) || fail "an unreadable after-state exited $rc, expected 4"
  contains "$ROOT/rev-after-unreadable.launch.log" 'can no longer be read' \
    'an unreadable after-state is flagged with its reason'
  contains "$RUNS/rev-after-unreadable.done" 'REVIEW-MODIFIED-TREE' \
    'an unreadable after-state writes the flag, not a pass'
  rm -f -- "$counter"
}

# --- a foreign completion marker --------------------------------------------

# A refused launch must not append its verdict to an earlier run's marker. The
# launch is refused by pi-impl (the name already has evidence) and the tree
# changes in the same window (a git shim reports a status change on the third
# `status` call, which is the after-state read).
check_foreign_marker_refused() {
  local shim=$ROOT/git-foreign real_git counter=$ROOT/git-foreign.count rc=0
  real_git=$(command -v git)
  mkdir -p "$shim"
  cat > "$shim/git" <<SH
#!/usr/bin/env bash
is_status=0
for a in "\$@"; do [[ \$a == status ]] && is_status=1; done
if (( is_status == 1 )); then
  n=\$(( \$(cat "$counter" 2>/dev/null || printf 0) + 1 ))
  printf '%s' "\$n" > "$counter"
  $real_git "\$@"
  (( n >= 3 )) && printf ' M tracked.txt\n'
  exit 0
fi
exec $real_git "\$@"
SH
  chmod +x "$shim/git"
  printf 'EXIT=0 OUTCOME=SUCCESS\n' > "$RUNS/rev-foreign.done"
  rm -f -- "$counter"
  PATH="$shim:$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    "$SRC" "$WORKTREE" "$PROMPT" rev-foreign --report "$REPORTS/rev-foreign.md" \
    --account volker > "$ROOT/rev-foreign.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 2 )) || fail "a refused launch over a foreign marker exited $rc, expected 2"
  contains "$ROOT/rev-foreign.launch.log" 'the review did not start' \
    'the refusal says the review never started'
  [[ $(cat "$RUNS/rev-foreign.done") == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a refused launch appended its verdict to another run marker'
  rm -f -- "$RUNS/rev-foreign.done" "$counter"
}

# An unresolvable report path is refused instead of compared in its raw spelling:
# the old fallback compared the unresolved string, so a symlinked or bound alias
# could slip a report inside the worktree.
check_unresolvable_report_path_refused() {
  local shim=$ROOT/realpath-shim real_realpath before rc=0
  real_realpath=$(command -v realpath)
  mkdir -p "$shim"
  # `realpath -m` effectively never fails on its own, so the refusal branch is
  # pinned through the only seam that reaches it: a realpath that FAILS for the
  # report path only (the worktree and prompt are resolved with -e earlier).
  cat > "$shim/realpath" <<SH
#!/usr/bin/env bash
for a in "\$@"; do [[ \$a == -m ]] && exit 1; done
exec $real_realpath "\$@"
SH
  chmod +x "$shim/realpath"
  before=$(pi_invocations)
  PATH="$shim:$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    "$SRC" "$WORKTREE" "$PROMPT" rev-unresolvable --report "$REPORTS/rev-unresolvable.md" \
    --account volker > "$ROOT/rev-unresolvable.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 2 )) || fail "an unresolvable report path exited $rc, expected 2"
  contains "$ROOT/rev-unresolvable.launch.log" 'cannot resolve the review report path' \
    'an unresolvable report path is refused instead of compared in its raw spelling'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with an unresolvable report path'
}

# M1: a review pi-impl classified as CONTEXT-FULL still carries text (pi writes a
# partial message for stopReason=length), and pi-impl exits 0. Persisting that
# text would certify a review that was cut off mid-way.
check_length_cutoff_not_persisted() {
  local rc=0
  FAKE_PI_STREAM_TEST="$SCRIPT_DIR/fixtures/pi-length.jsonl" \
    launch rev-lengthcut none --account volker || rc=$?
  (( rc == 6 )) || fail "a context-full review exited $rc, expected 6 (REVIEW-NOT-SETTLED)"
  [[ $(done_line rev-lengthcut) == 'EXIT=0 OUTCOME=CONTEXT-FULL' ]] \
    || fail 'the fixture did not reproduce a zero-exit CONTEXT-FULL run'
  contains "$RUNS/rev-lengthcut.done" 'REVIEW-NOT-SETTLED' 'a cut-off review is marked in its marker'
  contains "$ROOT/rev-lengthcut.launch.log" 'CONTEXT-FULL' 'the message names the outcome class'
  [[ ! -e $REPORTS/rev-lengthcut.md ]] || fail 'a cut-off review persisted its partial text as the report'
}

# The same rule for the other non-success classes a zero pi exit can carry. A
# USAGE-LIMIT was previously reported as REVIEW-NO-REPORT, hiding "the review
# account is out of balance".
check_non_success_classes_not_persisted() {
  local rc=0
  FAKE_PI_STREAM_TEST="$SCRIPT_DIR/fixtures/pi-402.jsonl" \
    launch rev-usage-limit none --account volker || rc=$?
  (( rc == 6 )) || fail "a usage-limited review exited $rc, expected 6 (REVIEW-NOT-SETTLED)"
  contains "$RUNS/rev-usage-limit.done" 'OUTCOME=USAGE-LIMIT' 'the usage limit class is recorded'
  contains "$ROOT/rev-usage-limit.launch.log" 'REVIEW-NOT-SETTLED' 'a usage-limited review is not reported as a missing report'
  lacks "$ROOT/rev-usage-limit.launch.log" 'REVIEW-NO-REPORT' 'a usage limit is not folded into the empty-message verdict'
  [[ ! -e $REPORTS/rev-usage-limit.md ]] || fail 'a usage-limited review wrote a report'
}

# M2: <run>.review/ is rendered before pi-impl refuses a reused run name, so a
# retry with the same name would rewrite the earlier invocation's before-state
# and then refuse to launch. The refusal must come first, and the evidence must
# survive byte for byte.
check_earlier_material_not_rewritten() {
  local rc=0 before_sha
  launch rev-material none --account volker || rc=$?
  (( rc == 0 )) || fail "the first review of a name exited $rc"
  before_sha=$(git hash-object -- "$RUNS/rev-material.review/before-state.txt")
  # A retry of the same RUN NAME, with a fresh report path at the wrapper's
  # level: the name has evidence, so pi-impl would refuse it, and the material
  # must not be rewritten before that happens. That earlier before-state is the
  # thing the refusal is about, so it is read after the retry.
  rc=0
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    "$SRC" "$WORKTREE" "$PROMPT" rev-material --report "$REPORTS/rev-material-retry.md" \
    --account volker > "$ROOT/rev-material-retry.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 2 )) || fail "a retry over an earlier review's material exited $rc, expected 2"
  contains "$ROOT/rev-material-retry.launch.log" 'an earlier review of this name already recorded its before-state' \
    'the earlier before-state is what refused the retry'
  [[ $(git hash-object -- "$RUNS/rev-material.review/before-state.txt") == "$before_sha" ]] \
    || fail 'a refused retry rewrote the earlier review before-state'
  [[ ! -e $REPORTS/rev-material-retry.md ]] || fail 'a refused retry wrote a report'
}

# The malformed-budget refusal must come BEFORE the material is rendered: it
# used to run after `prepare_review_material`, so a `PI_REVIEW_MAX_SECONDS=soon`
# typo wrote a fresh `<run>.review/before-state.txt` and then refused -- leaving
# evidence of a launch that never happened.
check_budget_refusal_before_material() {
  local before rc=0
  before=$(pi_invocations)
  PI_REVIEW_MAX_SECONDS_TEST=soon launch rev-budget-order none --account volker || rc=$?
  (( rc == 2 )) || fail "a malformed budget exited $rc, expected 2"
  contains "$ROOT/rev-budget-order.launch.log" 'PI_REVIEW_MAX_SECONDS must be a non-negative integer' \
    'the malformed value is what refused'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with a malformed budget'
  [[ ! -e $RUNS/rev-budget-order.review/before-state.txt ]] \
    || fail 'the malformed-budget refusal rendered a before-state before refusing'
}

# M3: the process-group ownership is VERIFIED before a group signal is used,
# and the form actually used is printed with the pgid that was read. A ps shim
# reports the launch's pgid as something other than the launch pid, so only the
# pid-only fallback is reachable -- the old code signalled the group, hit ESRCH,
# swallowed it with `|| true`, and claimed a group stop it never made.
#
# The M3 branch that cannot be constructed portably (a launch NOT confirmed
# stopped after TERM+KILL+waits must be reported and not waited on -- a KILL to
# a child this wrapper owns always works here) is a documented safety valve in
# the script and the README; this suite does not certify it.
check_budget_stop_verifies_group() {
  local rc=0 shim=$ROOT/ps-pgid-shim real_ps
  real_ps=$(command -v ps)
  mkdir -p "$shim"
  cat > "$shim/ps" <<SH
#!/usr/bin/env bash
for a in "\$@"; do
  case \$a in
    pgid=*) printf '1\\n'; exit 0 ;;
  esac
done
exec $real_ps "\$@"
SH
  chmod +x "$shim/ps"
  PATH="$shim:$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_ACTION=slow \
    FAKE_PI_SLOW_SECONDS=3 PI_REVIEW_MAX_SECONDS=1 PI_REVIEW_POLL_SECONDS=1 \
    PI_REVIEW_KILL_WAIT_SECONDS=1 \
    "$SRC" "$WORKTREE" "$PROMPT" rev-pgid --report "$REPORTS/rev-pgid.md" --account volker \
    > "$ROOT/rev-pgid.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 5 )) || fail "a budget stop over a non-leader launch exited $rc, expected 5"
  contains "$ROOT/rev-pgid.launch.log" 'to the launch pid alone' \
    'the log says which form was used, because the launch is not its own group leader'
  contains "$ROOT/rev-pgid.launch.log" 'pgid=1' 'the log names the pgid it actually read'
  lacks "$ROOT/rev-pgid.launch.log" 'to the launch process group' \
    'the group form is not claimed when the pgid was not verified'
  contains "$RUNS/rev-pgid.done" 'REVIEW-BUDGET' 'the run is still marked REVIEW-BUDGET'
  # No elapsed bound here: whether the launch's bash exits on TERM before its
  # foreground child is not the wrapper's behaviour to assert. The deterministic
  # facts are which signal form was used, that the run is marked, and that no
  # report was written -- the old group-only path claimed the group form.
  [[ ! -e $REPORTS/rev-pgid.md ]] || fail 'a stopped review wrote a report'
}

# L5: the check and the write use the SAME (resolved) path. A symlink at the
# report path pointing INSIDE the reviewed worktree is resolved first, so the
# containment refusal (which reasons about the resolved path) stops it; the old
# code wrote to the caller's raw spelling after checking the resolved one.
check_report_symlink_resolved_before_use() {
  local before rc=0
  ln -s "$WORKTREE/planted-review.md" "$REPORTS/rev-symlink.md"
  before=$(pi_invocations)
  launch rev-symlink none --account volker || rc=$?
  (( rc == 2 )) || fail "a report symlinked into the worktree exited $rc, expected 2"
  contains "$ROOT/rev-symlink.launch.log" 'report path is inside the reviewed worktree' \
    'the resolved target, not the raw spelling, is what the containment check compared'
  contains "$ROOT/rev-symlink.launch.log" "$WORKTREE/planted-review.md" \
    'the refusal names the resolved target'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started with its report symlinked into the worktree'
  [[ ! -e $WORKTREE/planted-review.md ]] || fail 'the report was written through a symlink into the worktree'
  rm -f -- "$REPORTS/rev-symlink.md"
}

# The reviewer has no write tool: the launcher saves its final message.
check_report_from_final_message() {
  local rc=0
  launch rev-final none --implementer-run impl-github2 || rc=$?
  (( rc == 0 )) || fail "a clean review that wrote no report file exited $rc, expected 0"
  [[ $(done_line rev-final) == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a clean review with text is not SUCCESS'
  contains "$REPORTS/rev-final.md" 'Completed the fixture task.' \
    'the report is the reviewer final assistant message'
  lacks "$RUNS/rev-final.done" REVIEW-MODIFIED-TREE 'a clean review does not flag the tree'
}

# A settled run whose final message carries no text leaves no review.
check_no_text_is_no_report() {
  local rc=0
  FAKE_PI_STREAM_TEST="$SCRIPT_DIR/fixtures/pi-thinking-only.jsonl" \
    launch rev-notext none --implementer-run impl-volker || rc=$?
  (( rc == 2 )) || fail "a review that said nothing exited $rc, expected 2"
  contains "$ROOT/rev-notext.launch.log" 'REVIEW-NO-REPORT' 'a review without text is REVIEW-NO-REPORT'
  contains "$RUNS/rev-notext.done" 'REVIEW-NO-REPORT' 'the marker carries the no-report verdict'
  [[ ! -e $REPORTS/rev-notext.md ]] || fail 'a review that said nothing wrote a report'
}


# --- the account record's other spellings and failure modes ------------------

# The Codex route spellings pin the same account mapping as the pi ones; 97 of
# the 162 real `.route` files in the release run dir use them.
printf 'deepseek-volker\n' > "$RUNS/impl-codex-volker.route"
printf 'deepseek-github2\n' > "$RUNS/impl-codex-github2.route"

check_other_account_for_codex_volker() {
  local rc=0
  launch rev-codex-volker none --implementer-run impl-codex-volker || rc=$?
  (( rc == 0 )) || { cat "$ROOT/rev-codex-volker.launch.log" >&2; fail "a Codex deepseek-volker review exited $rc"; }
  [[ $(cat "$RUNS/rev-codex-volker.route") == deepseek-pi-github2 ]] \
    || fail 'a Codex deepseek-volker implementation was not reviewed on github2'
}

check_other_account_for_codex_github2() {
  local rc=0
  launch rev-codex-github2 none --implementer-run impl-codex-github2 || rc=$?
  (( rc == 0 )) || { cat "$ROOT/rev-codex-github2.launch.log" >&2; fail "a Codex deepseek-github2 review exited $rc"; }
  [[ $(cat "$RUNS/rev-codex-github2.route") == deepseek-pi-volker ]] \
    || fail 'a Codex deepseek-github2 implementation was not reviewed on volker'
}

# A lookup that FAILED is not a lookup that found nothing: an unreadable account
# state refuses with the failure wording, never with "no account is recorded".
check_lookup_failed_branch() {
  local before rc=0 corrupt=$ROOT/opendesign-corrupt.json
  printf '{"volker": {"last_used_epoch": 0, "blocked_until_epoch": 0}, "threads": {"t": "luna"}}\n' > "$corrupt"
  before=$(pi_invocations)
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$corrupt" PI_LIVE=0 \
    "$SRC" "$WORKTREE" "$PROMPT" rev-lookup-failed --report "$REPORTS/rev-lookup-failed.md" \
    --implementer-run impl-thread > "$ROOT/rev-lookup-failed.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 2 )) || fail "a corrupt account state exited $rc, expected 2"
  contains "$ROOT/rev-lookup-failed.launch.log" 'the Open Design account lookup failed for implementer thread' \
    'a failed lookup is reported as a failure'
  lacks "$ROOT/rev-lookup-failed.launch.log" 'no Open Design account is recorded' \
    'a failed lookup is not reported as an absent account'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review started after a failed account lookup'
}

# --account bypasses the implementer record by design, and says so on stderr.
check_account_pin_stderr_note() {
  local rc=0
  launch rev-account-note none --account volker || rc=$?
  (( rc == 0 )) || fail "a pinned review exited $rc"
  contains "$ROOT/rev-account-note.launch.log" '--account pins the reviewer with no implementer record to cross-check' \
    'a pinned review warns that nothing cross-checked the account'
}

# r1 #11: pi's own status passes through, and a nonzero one must not read green.
check_pi_rc_passthrough() {
  local rc=0
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_OD_STATE="$STATE" PI_LIVE=0 \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_RC=1 FAKE_PI_ACTION=none \
    "$SRC" "$WORKTREE" "$PROMPT" rev-pi-rc --report "$REPORTS/rev-pi-rc.md" --account volker \
    > "$ROOT/rev-pi-rc.launch.log" 2>&1 < /dev/null || rc=$?
  (( rc == 1 )) || fail "a pi exit 1 exited $rc, expected 1 passed through"
  [[ $(done_line rev-pi-rc) == 'EXIT=1 OUTCOME=ERROR' ]] \
    || fail 'a nonzero pi exit is not recorded as ERROR'
  [[ ! -e $REPORTS/rev-pi-rc.md ]] || fail 'a failed review wrote a report'
}

# --- documentation ---------------------------------------------------------

# --- documentation ---------------------------------------------------------

check_docs() {
  local readme=$SCRIPT_DIR/../README.md changes=$SCRIPT_DIR/../CHANGES.md
  local agents=$SCRIPT_DIR/../../../AGENTS.md
  contains "$readme" 'pi-review.sh' 'README names the pi review launcher'
  contains "$readme" 'REVIEW-MODIFIED-TREE' 'README documents the modified-tree flag'
  # shellcheck disable=SC2016 # the backticked route row is the literal README text
  contains "$readme" '| `deepseek-pi-<other>` | `pi-review.sh` |' 'README keeps the pi review route row'
  contains "$readme" '--tools read,grep,find,ls' 'README names the reviewer tool set it enforces'
  contains "$readme" 'PI_REVIEW_MAX_SECONDS' 'README names the wall-clock budget'
  contains "$readme" 'PI_REVIEW_MAX_STREAM_BYTES' 'README names the stream budget'
  contains "$readme" 'REVIEW-BUDGET' 'README documents the budget verdict'
  contains "$readme" 'REVIEW-NO-REPORT' 'README documents the no-text verdict'
  contains "$readme" 'REVIEW-NOT-SETTLED' 'README documents the not-settled verdict'
  contains "$readme" 'default **7200**' 'README states the raised wall-clock default'
  contains "$readme" 'not confined to' 'README says the read tool is not confined to the worktree'
  contains "$readme" 'means exactly one thing' 'README states what exit 0 means for a review'
  contains "$readme" '<run>.review/before-state.txt' 'README names the persisted before-state'
  contains "$readme" 'ls-files -s -v' 'README lists the index-flag comparison'
  contains "$readme" 'info/exclude' 'README lists the exclude comparison'
  contains "$readme" 'untracked files' 'README lists the untracked-content comparison'
  contains "$readme" 'does NOT cover' 'README states what the comparison does NOT cover'
  # M4: the old sentence claimed EVERY pi/pi-impl exit status (0 included) passes
  # through unchanged, which the REVIEW-NO-REPORT and report-write paths
  # contradict. The list now names the launcher's own codes and says what 0 means.
  contains "$readme" 'Otherwise pi-impl' "README states which code a settling review returns"
  contains "$readme" 'REVIEW-NOT-SETTLED' "README lists the launcher's own exit codes"
  lacks "$readme" 'any** pi or pi-impl exit status' 'README no longer claims every pi exit status passes through'
  contains "$readme" 'UNREACHABLE for a review' 'README states that rc 3 is unreachable for a review'
  contains "$readme" 'never through the' 'README states the kill path'
  contains "$readme" '<run>.lock' 'README names the owner record the kill path uses'
  contains "$changes" 'pi-review.sh' 'CHANGES.md records the pi review launcher'
  contains "$changes" 'PI_EXTRA_ARGS' 'CHANGES.md records the PI_EXTRA_ARGS seam'
  contains "$changes" 'RG-315' 'CHANGES.md names the remote-gate progress glob fix'
  contains "$agents" 'pi-review.sh' 'AGENTS.md names the pi review launcher'
}

check_other_account_for_volker
check_other_account_for_github2
check_other_account_from_thread
check_other_account_for_codex_volker
check_other_account_for_codex_github2
check_lookup_failed_branch
check_account_pin_stderr_note
check_pi_rc_passthrough
check_unknown_implementer_account
check_unknown_thread_account
check_account_pinning
check_modified_tracked_file
check_modified_untracked_file
check_dirty_before_state_allowed
check_unreadable_before_state_refused
check_report_inside_worktree
check_account_and_implementer_conflict
check_account_required
check_unknown_account_value
check_report_required
check_modified_head
check_read_only_tool_set
check_caller_extra_args_replaced
check_extra_args_cannot_override_contract
check_budget_wall_time
check_budget_stream_bytes
check_budget_value_refused
check_budget_default_wall
check_extra_args_announced
check_stale_report_refused
check_modified_head_branch
check_modified_index_flags
check_modified_exclude
check_modified_untracked_content
check_modified_diff_of_dirty_file
check_untracked_in_untracked_dir
check_unreadable_after_state_flagged
check_foreign_marker_refused
check_unresolvable_report_path_refused
check_report_from_final_message
check_no_text_is_no_report
check_length_cutoff_not_persisted
check_non_success_classes_not_persisted
check_earlier_material_not_rewritten
check_budget_refusal_before_material
check_budget_stop_verifies_group
check_report_symlink_resolved_before_use
check_docs

[[ -z $(git -C "$WORKTREE" status --porcelain) ]] \
  || fail 'the reviewed worktree is not clean after the checks'
echo 'PASS pi-review: other-account selection, thread lookup, refusals, read-only tool set, review budget, stale-report refusal, worktree state classes, report from the final message'
