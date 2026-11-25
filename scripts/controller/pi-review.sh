#!/usr/bin/env bash
# Start a round-1 pi review on the Open Design account the implementer did NOT
# use, and prove the review left the reviewed worktree exactly as it found it.
# Usage: pi-review.sh <worktree> <absolute-prompt-file> <run-name>
#                     --report <absolute-review-path>
#                     (--implementer-run <run> | --account volker|github2)
#
# Account. With --implementer-run the implementer's account is read from
# <run>.route when that pins one (`deepseek-pi-volker`, `deepseek-pi-github2`,
# and the Codex spellings `deepseek-volker`, `deepseek-github2`); otherwise
# from <run>.thread through `opendesign-accounts.py find-thread-account`. The
# review route is then `deepseek-pi-<the other account>`.
# An account that cannot be found is a refusal (exit 2), with the two record
# paths it read printed: this wrapper never falls back to the account the
# implementer used. `--account` pins the review account directly, for an
# implementer whose run record names none.
#
# Read-only, enforced and detected.
#
# 1. ENFORCED before the launch: pi is started with a tool set that has no
#    shell and no writer -- `--tools read,grep,find,ls`, `--exclude-tools
#    mcp__*`, `--no-extensions`, `--no-mcp` -- passed through pi-impl.sh's
#    PI_EXTRA_ARGS seam. The reviewer cannot run a program, so it cannot loop
#    in a shell or write a multi-gigabyte stream through one. What it needs
#    instead is pre-rendered: the commits, the diff and the file list are
#    written to <runs-dir>/<run>.review/ (OUTSIDE the reviewed worktree) and a
#    copy of the prompt naming those files and the tool set is what pi reads.
#    A review report is not written by the reviewer either (it has no write
#    tool): the reviewer's FINAL assistant message is the report, and this
#    wrapper persists it at --report after the run settles.
# 2. ENFORCED during the run: a review budget. PI_REVIEW_MAX_SECONDS (wall
#    time, default 1800) and PI_REVIEW_MAX_STREAM_BYTES (<run>.jsonl size,
#    default 268435456) are checked every PI_REVIEW_POLL_SECONDS (default 15).
#    Over budget the launch is stopped (TERM to the launch's own process group,
#    which reaches pi and the liveness sidecar, then KILL after
#    PI_REVIEW_KILL_WAIT_SECONDS, default 10) and the run is marked
#    `REVIEW-BUDGET` (exit 5).
#
# 3. DETECTED after the fact (not prevented): before and after the launch the
#    wrapper records the worktree's `git rev-parse HEAD`, `git status
#    --porcelain=v1 -uall` (untracked files included, ignored files not) and a
#    hash of `git diff HEAD`. Any difference appends the line
#    `REVIEW-MODIFIED-TREE` to <run>.done, prints it, and exits 4; the changes
#    are never reverted here, the controller decides. A worktree whose
#    BEFORE-state cannot be recorded is refused (exit 2), and one whose
#    AFTER-state cannot be read takes the same `REVIEW-MODIFIED-TREE` line with
#    the reason on stderr, because the guarantee can no longer be certified.
#    A dirty worktree is allowed (a review is compared with its own before-state,
#    so a pre-existing change is not charged to the reviewer). pi-impl.sh's own
#    UNCOMMITTED class still fires on such a tree (that word is about the worktree,
#    not about who changed it), while REVIEW-MODIFIED-TREE fires only when the
#    before and after states differ.
#
# A review REPORT path inside the worktree is refused, because this wrapper
# writes the report there after the run and a write inside the reviewed tree is
# exactly what the comparison is looking for.
#
# The rest is pi-impl.sh's, unchanged: its outcome vocabulary and its run
# records (--report is NOT passed to it, because the reviewer cannot write the
# file; the wrapper persists the final message instead). The model and thinking
# level are pi-impl.sh's defaults (the eco model at `max`) unless the caller
# already set PI_MODEL / PI_THINKING, which pi-impl.sh preserves.
#
# Exit codes: 2 for the refusals above (usage, unreadable worktree, report
# inside the worktree, unknown account, account records that name none, an
# invalid budget value), otherwise pi-impl.sh's own code, returned unchanged
# (0 settled, 1 or another pi exit status, 2 refused, 3 both Open Design
# accounts blocked). 4 is REVIEW-MODIFIED-TREE and 5 is REVIEW-BUDGET; neither
# is a pi-impl.sh code.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init

usage() {
  echo "usage: $0 <worktree> <absolute-prompt-file> <run-name> --report <absolute-review-path> (--implementer-run <run> | --account volker|github2)" >&2
  exit 2
}

report_path=''
implementer=''
account=''
positional=()
while (( $# > 0 )); do
  case $1 in
    --report) (( $# >= 2 )) || usage; report_path=$2; shift 2 ;;
    --report=*) report_path=${1#--report=}; shift ;;
    --implementer-run) (( $# >= 2 )) || usage; implementer=$2; shift 2 ;;
    --implementer-run=*) implementer=${1#--implementer-run=}; shift ;;
    --account) (( $# >= 2 )) || usage; account=$2; shift 2 ;;
    --account=*) account=${1#--account=}; shift ;;
    *) positional+=("$1"); shift ;;
  esac
done
set -- "${positional[@]}"
(($# == 3)) || usage
worktree_arg=$1 prompt_arg=$2 name=$3
[[ $prompt_arg == /* ]] || { echo 'prompt path must be absolute' >&2; exit 2; }
[[ -n $report_path ]] || usage
[[ $report_path == /* ]] || { echo 'report path must be absolute' >&2; exit 2; }
if [[ -n $implementer && -n $account ]]; then
  echo 'give either --implementer-run <run> or --account volker|github2, not both' >&2
  exit 2
fi
if [[ -z $implementer && -z $account ]]; then
  echo 'give one of --implementer-run <run> and --account volker|github2' >&2
  exit 2
fi
if [[ -n $account && $account != volker && $account != github2 ]]; then
  echo "unknown account: $account (expected volker or github2)" >&2
  exit 2
fi

# Both names slice run-record paths below, so they are validated with
# pi-impl.sh's own rule before any path is built from them.
validate_run_name() {
  [[ $1 =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $1 != *..* ]] || {
    echo "invalid run name: $1" >&2
    exit 2
  }
}
validate_run_name "$name"
[[ -z $implementer ]] || validate_run_name "$implementer"

worktree=$(realpath -e -- "$worktree_arg") || usage
prompt=$(realpath -e -- "$prompt_arg") || usage
if ! git -C "$worktree" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "worktree is not a git worktree: $worktree" >&2
  exit 2
fi
review_path=$(realpath -m -- "$report_path") || {
  # No silent substitution: an unresolved path is compared against the worktree
  # in its raw spelling, so a symlinked or bound alias would slip through.
  echo "cannot resolve the review report path: $report_path" >&2
  exit 2
}
case $review_path in
  "$worktree" | "$worktree"/*)
    echo "report path is inside the reviewed worktree: $review_path" >&2
    exit 2
    ;;
esac

# A retried review normally reuses its report path, and the reviewer cannot
# rewrite the file: an existing one would leave the previous run's report
# standing as this run's. Refuse before anything is rendered or launched.
if [[ -e $review_path ]]; then
  echo "the review report already exists: $review_path" >&2
  echo '  a review that writes no report must not leave an earlier report certified as this run' >&2
  exit 2
fi

review_account=''
if [[ -n $account ]]; then
  review_account=$account
  # The caller is asserting a fact the wrapper cannot check: nothing here
  # compares the pin with an implementer record, so say so at the call site.
  echo "--account pins the reviewer with no implementer record to cross-check; the caller is asserting this is not the implementer's account" >&2
else
  route_file=$CONTROLLER_RUNS_DIR/$implementer.route
  thread_file=$CONTROLLER_RUNS_DIR/$implementer.thread
  route_value=''
  thread_id=''
  [[ ! -r $route_file ]] || route_value=$(<"$route_file")
  implementer_account=''
  case $route_value in
    deepseek-pi-volker | deepseek-volker) implementer_account=volker ;;
    deepseek-pi-github2 | deepseek-github2) implementer_account=github2 ;;
    *)
      # A route that pins no account (`deepseek-pi`/`deepseek` selected one at
      # start without recording it as a pin; `luna`/`sol`/`opencode` name no
      # Open Design account): ask the shared thread record instead.
      [[ ! -r $thread_file ]] || thread_id=$(<"$thread_file")
      if [[ -n $thread_id ]]; then
        lookup_rc=0
        found_account=''
        found_account=$(python3 "$SCRIPT_DIR/opendesign-accounts.py" \
          find-thread-account "$thread_id" "$CONTROLLER_RUNS_DIR") || lookup_rc=$?
        if ((lookup_rc == 0)); then
          implementer_account=$found_account
        elif ((lookup_rc == 1)); then
          echo "no Open Design account is recorded for implementer thread $thread_id" >&2
          exit 2
        else
          # A lookup that FAILED is not a lookup that found nothing: the account
          # state is unreadable, and this refusal must not report it as absence.
          echo "the Open Design account lookup failed for implementer thread $thread_id (exit $lookup_rc)" >&2
          exit 2
        fi
      fi
      ;;
  esac
  if [[ -z $implementer_account ]]; then
    echo "cannot determine the Open Design account of implementer run $implementer" >&2
    echo "  $route_file: ${route_value:-missing}" >&2
    echo "  $thread_file: ${thread_id:-missing}" >&2
    echo '  pin the review account with --account volker|github2 instead' >&2
    exit 2
  fi
  case $implementer_account in
    volker) review_account=github2 ;;
    github2) review_account=volker ;;
    *) echo 'account lookup returned an invalid account' >&2; exit 2 ;;
  esac
fi

TREE_HEAD=''
TREE_SYMHEAD=''
TREE_STATUS=''
TREE_INDEX=''
TREE_EXCLUDE=''
TREE_UNTRACKED=''
TREE_DIFF=''
record_tree_state() {
  local wt=$1 head symhead status index exclude untracked diff path
  head=$(git -C "$wt" --no-optional-locks rev-parse HEAD 2>/dev/null) || return 1
  # The symbolic HEAD: a branch switch at the same commit moves this and nothing
  # else, so `rev-parse HEAD` alone would certify a tree that is now on a
  # scratch branch (the next fix round would commit onto it).
  if symhead=$(git -C "$wt" --no-optional-locks symbolic-ref -q HEAD 2>/dev/null); then
    :
  else
    symhead=DETACHED
  fi
  status=$(git -C "$wt" --no-optional-locks status --porcelain=v1 -uall 2>/dev/null) || return 1
  # The index ENTRY flags (`-v`, not just `-s`): `--assume-unchanged` and
  # `--skip-worktree` hide a tracked edit from both `status` and `diff HEAD`.
  index=$(git -C "$wt" --no-optional-locks ls-files -s -v -z 2>/dev/null |
    git hash-object --stdin 2>/dev/null) || return 1
  # `info/exclude` is the other way to hide new files from `status`. It is read
  # through --git-path, so a linked worktree resolves its own path, not the
  # common one; an absent file is recorded as absent rather than as empty.
  path=$(git -C "$wt" --no-optional-locks rev-parse --git-path info/exclude 2>/dev/null) || return 1
  [[ $path == /* ]] || path=$wt/$path
  if [[ -e $path ]]; then
    exclude=$(git hash-object -- "$path" 2>/dev/null) || return 1
  else
    exclude=ABSENT
  fi
  # The CONTENT of pre-existing untracked files: `?? f` has the same status line
  # before and after, so an edit to a file the implementer left behind would
  # otherwise escape. Every path is hashed (sorted, so the digest is stable); a
  # list that cannot be read is a failed record, never an empty one.
  untracked=$(git -C "$wt" --no-optional-locks ls-files -o --exclude-standard -z 2>/dev/null |
    xargs -0 -r git -C "$wt" --no-optional-locks hash-object -- 2>/dev/null |
    git hash-object --stdin 2>/dev/null) || return 1
  # `diff HEAD` covers staged and unstaged tracked changes in one value; `--binary`
  # keeps the bytes (not the patch text) the thing that is hashed. Every config
  # hook that could rewrite the output is disabled: an external driver or a
  # textconv attribute is not part of the tree being compared.
  diff=$(git -C "$wt" --no-optional-locks diff --no-ext-diff --no-textconv --no-color HEAD --binary 2>/dev/null |
    git hash-object --stdin 2>/dev/null) || return 1
  TREE_HEAD=$head
  TREE_SYMHEAD=$symhead
  TREE_STATUS=$status
  TREE_INDEX=$index
  TREE_EXCLUDE=$exclude
  TREE_UNTRACKED=$untracked
  TREE_DIFF=$diff
  return 0
}
# A worktree whose state cannot be recorded must not launch: the read-only
# guarantee would rest on a comparison that was never made.
if ! record_tree_state "$worktree"; then
  echo "cannot record the worktree state before the review: $worktree" >&2
  exit 2
fi
before_head=$TREE_HEAD
before_symhead=$TREE_SYMHEAD
before_status=$TREE_STATUS
before_index=$TREE_INDEX
before_exclude=$TREE_EXCLUDE
before_untracked=$TREE_UNTRACKED
before_diff=$TREE_DIFF

launch_rc=0

marker=$CONTROLLER_RUNS_DIR/$name.done
# A marker that exists BEFORE the launch belongs to an earlier run of this name.
# Its modification time (nanoseconds) is what tells a fresh marker from it: the
# presence test alone is a name-for-object check and would let this invocation
# stamp a fact onto somebody else's record.
marker_before=$(stat -c %y -- "$marker" 2>/dev/null || printf 'absent')

# Did THIS invocation write the name's completion marker? A marker that existed
# before the launch belongs to an earlier run of the name, and appending to it
# would record a fact about a launch that never started (r1 #7).
marker_is_ours() {
  local now
  now=$(stat -c %y -- "$marker" 2>/dev/null || printf 'absent')
  [[ $marker_before == absent && $now != absent ]] && return 0
  [[ $marker_before != absent && $now != "$marker_before" ]] && return 0
  return 1
}

# --- the material a writer-less, shell-less reviewer is given -----------------
# pi runs with `read`, `grep`, `find` and `ls` only, so it can neither run `git`
# nor write the report: the launcher renders the change for reading and saves
# the run's final message as the report. Everything here lives in
# <runs-dir>/<run>.review/, OUTSIDE the reviewed worktree -- a write inside it
# would be exactly what the after-state comparison is looking for.
REVIEW_MATERIAL_DIR=$CONTROLLER_RUNS_DIR/$name.review
review_prompt=''
prepare_review_material() {
  local wt=$1 base='' path=$REVIEW_MATERIAL_DIR
  mkdir -p -- "$path" || return 1
  # The base is the merge base with the upstream branch the guard and the gate
  # already use, resolved through HEAD's own history (never a guessed commit).
  if base=$(git -C "$wt" --no-optional-locks merge-base HEAD origin/main 2>/dev/null) && [[ -n $base ]]; then
    :
  elif base=$(git -C "$wt" --no-optional-locks rev-parse --verify -q 'HEAD^' 2>/dev/null) && [[ -n $base ]]; then
    :
  else
    base=''
  fi
  if [[ -n $base ]]; then
    git -C "$wt" --no-optional-locks log --no-decorate --format='%h %s' "$base..HEAD" \
      > "$path/commits.txt" 2>/dev/null || return 1
    git -C "$wt" --no-optional-locks diff --no-ext-diff --no-textconv --no-color -U3 \
      "$base" HEAD > "$path/diff.txt" 2>/dev/null || return 1
    git -C "$wt" --no-optional-locks diff --no-ext-diff --no-textconv --no-color --name-status \
      "$base" HEAD > "$path/files.txt" 2>/dev/null || return 1
  else
    # A root commit has no base: show the commit itself, which is its whole diff.
    git -C "$wt" --no-optional-locks log -n 20 --no-decorate --format='%h %s' \
      > "$path/commits.txt" 2>/dev/null || return 1
    git -C "$wt" --no-optional-locks show --no-color --no-ext-diff -U3 --format=fuller \
      HEAD > "$path/diff.txt" 2>/dev/null || return 1
    git -C "$wt" --no-optional-locks show --no-color --name-status --format= HEAD \
      > "$path/files.txt" 2>/dev/null || return 1
  fi
  git -C "$wt" --no-optional-locks status --porcelain=v1 -uall \
    > "$path/status.txt" 2>/dev/null || return 1
  # The reviewed tree may be dirty before the review starts (that is allowed):
  # pi cannot run `git`, so the uncommitted change is rendered too.
  git -C "$wt" --no-optional-locks diff HEAD --no-ext-diff --no-textconv --no-color -U3 \
    > "$path/uncommitted.diff" 2>/dev/null || return 1
  review_prompt=$path/prompt.md
  # The before-state is PERSISTED, not only held in this shell's memory: if this
  # wrapper is killed (TERM, OOM, a dying devcontainer) nothing is certified, and
  # a later reader must still be able to see what the tree looked like when the
  # review started. Written before the launch, never rewritten afterwards.
  {
    printf 'head=%s\n' "$TREE_HEAD"
    printf 'symhead=%s\n' "$TREE_SYMHEAD"
    printf 'status_sha=%s\n' "$(printf '%s' "$TREE_STATUS" | git hash-object --stdin)"
    printf 'index_sha=%s\n' "$TREE_INDEX"
    printf 'exclude=%s\n' "$TREE_EXCLUDE"
    printf 'untracked_sha=%s\n' "$TREE_UNTRACKED"
    printf 'diff_sha=%s\n' "$TREE_DIFF"
    printf 'recorded_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$path/before-state.txt" || return 1
  # shellcheck disable=SC2016 # the backticked words are literal prose for the reviewer, not expansions
  {
    cat -- "$prompt"
    printf '\n\n---\n\n'
    printf '## Review material (rendered by pi-review.sh before this launch)\n\n'
    printf 'This run has no shell and no write tool: the available tools are `read`, `grep`, `find` and \
`ls`. You cannot run `git`, a script, or any command. The change under review is rendered for you:\n\n'
    if [[ -n $base ]]; then
      printf '* `%s/commits.txt` -- the commits under review (`%s..HEAD`, one `sha subject` line each)\n' "$path" "${base:0:12}"
      printf '* `%s/uncommitted.diff` -- the uncommitted change against HEAD (`git diff HEAD`), when the tree is dirty\n' "$path"
    else
      printf '* `%s/commits.txt` -- the repository has no upstream base; recent commits, one `sha subject` line each\n' "$path"
      printf '* `%s/uncommitted.diff` -- the uncommitted change against HEAD (`git diff HEAD`), when the tree is dirty\n' "$path"
    fi
    printf '* `%s/diff.txt` -- the full diff under review (no color, no external diff driver)\n' "$path"
    printf '* `%s/files.txt` -- the changed files with their status (`--name-status`)\n' "$path"
    printf '* `%s/status.txt` -- `git status --porcelain=v1 -uall` at launch time, so a pre-existing dirty tree is visible\n' "$path"
    printf '\nRead those files (and the tree with `read`/`grep`/`find`/`ls`) and review the change.\n'
    printf 'Your FINAL assistant message IS the review report: pi-review.sh saves it verbatim to the report path.\n'
    printf 'Do not attempt to write a file; there is no tool for it.\n'
  } > "$review_prompt" || return 1
  return 0
}
if ! prepare_review_material "$worktree"; then
  echo "cannot render the review material in $REVIEW_MATERIAL_DIR; nothing was launched" >&2
  exit 2
fi
# --- the review budget -------------------------------------------------------
review_max_seconds=${PI_REVIEW_MAX_SECONDS:-1800}
review_max_bytes=${PI_REVIEW_MAX_STREAM_BYTES:-268435456}
review_poll=${PI_REVIEW_POLL_SECONDS:-15}
review_kill_wait=${PI_REVIEW_KILL_WAIT_SECONDS:-10}
for pair in "PI_REVIEW_MAX_SECONDS=$review_max_seconds" "PI_REVIEW_MAX_STREAM_BYTES=$review_max_bytes" \
  "PI_REVIEW_POLL_SECONDS=$review_poll" "PI_REVIEW_KILL_WAIT_SECONDS=$review_kill_wait"; do
  value=${pair#*=}
  [[ $value =~ ^[0-9]+$ ]] || {
    echo "${pair%%=*} must be a non-negative integer (got: $value)" >&2
    exit 2
  }
done
(( review_max_seconds > 0 )) || { echo 'PI_REVIEW_MAX_SECONDS must be a positive integer' >&2; exit 2; }
(( review_max_bytes > 0 )) || { echo 'PI_REVIEW_MAX_STREAM_BYTES must be a positive integer' >&2; exit 2; }
(( review_poll > 0 )) || { echo 'PI_REVIEW_POLL_SECONDS must be a positive integer' >&2; exit 2; }

# The read-only tool set, through pi-impl.sh's PI_EXTRA_ARGS seam (one argv
# element per line, so nothing here is word-split). The launcher owns this
# variable: a caller's value is replaced, not merged, so a review cannot be
# widened back to a shell.
# Built with printf, one element per line, so the list is readable and a
# mutant of it is plain text.
export PI_EXTRA_ARGS
PI_EXTRA_ARGS=$(printf '%s\n' --tools read,grep,find,ls --exclude-tools 'mcp__*' --no-extensions --no-mcp)

# --- the launch, under the budget --------------------------------------------
# `set -m` puts the launch in its OWN process group, which is what makes a stop
# possible: TERM reaches pi-impl.sh, the pi it execs and the liveness sidecar
# it started, not this wrapper.
stream=$CONTROLLER_RUNS_DIR/$name.jsonl
launch_rc=0
budget_reason=''
set -m
# PI_REPORT is cleared: the reviewer cannot write the file, so pi-impl.sh must
# not run its report check (that would always read NO-REPORT); the report is
# persisted below from the run's final message.
PI_ROUTE="deepseek-pi-$review_account" PI_REPORT='' \
  "$SCRIPT_DIR/pi-impl.sh" "$worktree" "$review_prompt" "$name" &
launch_pid=$!
set +m
started_epoch=$EPOCHSECONDS
# Poll in 0.2s slices up to PI_REVIEW_POLL_SECONDS, so a run that ends inside
# the interval is reaped at once instead of after a full interval (the suite
# runs many short launches and must not pay the long-case interval each time).
next_poll=$started_epoch
while kill -0 "$launch_pid" 2>/dev/null; do
  now_epoch=$EPOCHSECONDS
  if (( now_epoch - started_epoch >= review_max_seconds )); then
    budget_reason="wall time $((now_epoch - started_epoch))s >= ${review_max_seconds}s"
    break
  fi
  if (( now_epoch >= next_poll )); then
    if [[ -r $stream ]]; then
      stream_bytes=$(stat -c %s "$stream" 2>/dev/null || printf '0')
      if [[ $stream_bytes =~ ^[0-9]+$ ]] && (( stream_bytes >= review_max_bytes )); then
        budget_reason="stream ${stream_bytes} bytes >= ${review_max_bytes} bytes"
        break
      fi
    fi
    next_poll=$((now_epoch + review_poll))
  fi
  sleep 0.2
done
if [[ -n $budget_reason ]]; then
  # TERM the whole group, give it PI_REVIEW_KILL_WAIT_SECONDS to settle, then
  # KILL what is left. Never leave a stopped run's processes behind.
  kill -TERM -- "-$launch_pid" 2>/dev/null || true
  ticks=$(( review_kill_wait * 10 ))
  while (( ticks > 0 )) && kill -0 "$launch_pid" 2>/dev/null; do
    sleep 0.1
    ticks=$(( ticks - 1 ))
  done
  kill -0 "$launch_pid" 2>/dev/null && kill -KILL -- "-$launch_pid" 2>/dev/null || true
fi
wait "$launch_pid" || launch_rc=$?

if [[ -n $budget_reason ]]; then
  marker=$CONTROLLER_RUNS_DIR/$name.done
  if marker_is_ours; then
    # pi-impl.sh settled while the stop was in flight: keep its outcome as the
    # record and add this invocation's verdict beside it (every reader scans the
    # whole marker). A marker written BY THIS LAUNCH is the one place the verdict
    # belongs.
    printf 'REVIEW-BUDGET\n' >> "$marker"
  elif [[ $marker_before == absent ]]; then
    # The launch never wrote a marker of its own: record the verdict alone rather
    # than leaving the run with no evidence at all.
    printf 'EXIT=5 OUTCOME=REVIEW-BUDGET\n' > "$marker"
  else
    echo "the review was stopped over budget ($budget_reason) but left no marker of its own: $name" >&2
    echo "  $marker is an earlier run's record; nothing was appended to it" >&2
  fi
  printf 'REVIEW-BUDGET: the review was stopped over budget (%s); pi-impl exit %s\n' \
    "$budget_reason" "$launch_rc" >&2
  exit 5
fi


changed=''
if ! record_tree_state "$worktree"; then
  changed='the worktree state can no longer be read'
else
  [[ $TREE_HEAD == "$before_head" ]] || changed='HEAD'
  [[ $TREE_SYMHEAD == "$before_symhead" ]] || changed="${changed:+$changed, }head-branch"
  [[ $TREE_STATUS == "$before_status" ]] || changed="${changed:+$changed, }status"
  [[ $TREE_INDEX == "$before_index" ]] || changed="${changed:+$changed, }index-flags"
  [[ $TREE_EXCLUDE == "$before_exclude" ]] || changed="${changed:+$changed, }exclude"
  [[ $TREE_UNTRACKED == "$before_untracked" ]] || changed="${changed:+$changed, }untracked-content"
  [[ $TREE_DIFF == "$before_diff" ]] || changed="${changed:+$changed, }diff"
fi
if [[ -n $changed ]]; then
  if ! marker_is_ours && [[ $marker_before != absent ]]; then
    # The tree changed while this launch never wrote its own completion marker:
    # that marker is another invocation's record. Refuse instead of appending a
    # fact about a review that did not start to a record that is not ours.
    echo "the reviewed worktree changed ($changed) while the review did not start: $name" >&2
    echo "  $marker is an earlier run's record; nothing was appended to it" >&2
    exit 2
  fi
  # pi-impl.sh writes this marker on every launch that got past the run-name
  # claim; a missing one means the run never produced its own record, which the
  # operator must not read as pi-impl's verdict.
  [[ -e $marker ]] || printf 'pi-review: %s left no completion marker; recording only the review flag\n' "$name" >&2
  printf 'REVIEW-MODIFIED-TREE\n' >> "$marker"
  printf 'REVIEW-MODIFIED-TREE: %s (pi-impl exit %s); the worktree is left as it is\n' \
    "$changed" "$launch_rc" >&2
  exit 4
fi

# The reviewer has no write tool, so pi-impl.sh was NOT given --report: its
# report check would always read NO-REPORT. The report is the run's final
# assistant message, saved here -- outside the reviewed worktree, after the
# after-state comparison, so writing it cannot change what is being judged.
if (( launch_rc == 0 )) && [[ -s $CONTROLLER_RUNS_DIR/$name.last.txt ]] \
  && grep -q '[^[:space:]]' "$CONTROLLER_RUNS_DIR/$name.last.txt"; then
  if ! cp -- "$CONTROLLER_RUNS_DIR/$name.last.txt" "$report_path"; then
    echo "cannot save the review report at $report_path" >&2
    exit 2
  fi
  exit 0
fi
if (( launch_rc == 0 )); then
  # A settled run that said nothing leaves no review: the outcome class pi-impl.sh
  # recorded (EMPTY-COMPLETION-RETRIES-EXHAUSTED and friends) is the reason, and
  # this is still exit 0 from pi. A review that produced no text is not a success.
  marker=$CONTROLLER_RUNS_DIR/$name.done
  [[ -e $marker ]] && marker_is_ours && printf 'REVIEW-NO-REPORT\n' >> "$marker"
  echo "REVIEW-NO-REPORT: the review settled without any review text; nothing was written to $report_path" >&2
  exit 2
fi
exit "$launch_rc"
