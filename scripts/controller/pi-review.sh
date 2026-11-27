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
# 1. ENFORCED before the launch: the run is started in pi-impl.sh's REVIEW mode
#    (`PI_RUN_MODE=review`), whose read-only tool set has no shell and no writer:
#    `--tools read,grep,find,ls --exclude-tools mcp__* --no-extensions --no-mcp`.
#    The list has ONE definition, the tracked file `pi-review-argv` beside
#    pi-impl.sh; this launcher sets the mode and never carries a second copy of
#    it. The reviewer cannot run a program, so it cannot loop in a shell or write
#    a multi-gigabyte stream through one. What it needs instead is pre-rendered:
#    the commits, the diff and the file list are written to
#    <runs-dir>/<run>.review/ (OUTSIDE the reviewed worktree) and a copy of the
#    prompt naming those files and the tool set is what pi reads. The mode is
#    sticky per run name and per pi session: a later resume of this name is
#    forced back into review mode even when the caller does not ask for it, and
#    a resume of that session under a fresh name is also forced into review.
#    Either resume is refused if the caller asks for implementer mode. pi-impl.sh also
#    appends its own fixed review rule paragraph (`review-prompt-rules.md`:
#    execute nothing) to the prompt of every launch.
#    A review report is not written by the reviewer either (it has no write
#    tool): the reviewer's FINAL assistant message is the report, and this
#    wrapper persists it at --report after the run settles -- but ONLY for a
#    settled SUCCESS/UNCOMMITTED class (see the exit codes).
#    NOT prevented, and worth saying: the `read` tool is not confined to the
#    worktree. It can read anything the wrapper's user can, including
#    credentials under $HOME. The review prompt and the rendered material are
#    the only inputs this wrapper controls.
# 2. ENFORCED during the run: a review budget. PI_REVIEW_MAX_SECONDS (wall
#    time, default 7200) and PI_REVIEW_MAX_STREAM_BYTES (<run>.jsonl size,
#    default 268435456) are checked every PI_REVIEW_POLL_SECONDS (default 15).
#    Over budget the launch is stopped (TERM to the launch's own process group,
#    which reaches pi-impl, the pi it execs and the liveness sidecar, then KILL
#    after PI_REVIEW_KILL_WAIT_SECONDS, default 10) and the run is marked
#    `REVIEW-BUDGET` (exit 5). The group is VERIFIED with `ps -o pgid=` first:
#    when the launch is not its own group leader, the fallback is a TERM to the
#    launch pid alone and the log says which form was used. A launch that
#    outlasts KILL is reported with its pid and NOT waited for, so a stop can
#    never block the wrapper. The effective limits are printed at launch, and
#    the marker records WHICH form the stop used (`REVIEW-BUDGET-STOP=group`
#    or `=pid`): a pid stop reaches only the wrapper's child, so the pi it
#    execs may still be running and a reader must not take it as confirmation
#    that the reviewer stopped.
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
# already set PI_MODEL / PI_THINKING, which pi-impl.sh preserves. The run's mode
# record (<run>.mode) is review for a name this launcher starts, and pi-impl.sh
# also records the learned session id. A manual `pi-impl.sh ... resume
# <session-id>` of either that name or session is a review too (pi-impl.sh forces
# review and refuses an implementer resume), and it does the same for a name
# that carries <run>.review/ material but no mode record.
#
# Exit codes: 2 for the refusals above (usage, unreadable worktree, report
# inside the worktree, an existing report, an earlier review of this name's own
# before-state, unknown account, account records that name none, an invalid
# budget value) and for REVIEW-NO-REPORT or a report that cannot be written --
# including the two post-launch write failures (the path taken by another
# writer after the checks, or a `cp` that fails), which record REVIEW-NO-REPORT
# in the marker of THIS launch because its line 1 is still `EXIT=0
# OUTCOME=SUCCESS`; 4 is REVIEW-MODIFIED-TREE; 5 is REVIEW-BUDGET, whose marker
# line 2 is `REVIEW-BUDGET-STOP=group|pid` (a `pid` stop reaches only the
# wrapper's child, so the pi it execs may still be running); 6 is
# REVIEW-NOT-SETTLED (pi-impl
# settled in a class that is not SUCCESS/UNCOMMITTED, so no report was written:
# CONTEXT-FULL, TOOL-CALL-NO-TEXT, EMPTY-COMPLETION*, NO-REPORT, USAGE-LIMIT,
# ERROR, INCOMPLETE). 0 means exactly one thing: the review settled in a
# success class AND its report was saved. Every other pi or pi-impl exit status
# is passed through unchanged (1, 2 refused, 3 both Open Design accounts
# blocked). None of 4, 5, 6 is a pi-impl.sh code; pi itself can exit 5 too, so a
# bare 5 is not the budget verdict -- the marker is.
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
report_path_taken() {
  # `-L` as well as `-e`: a DANGLING symlink is not `-e`, and cp would write
  # through it to a target nothing compared.
  [[ -e $review_path || -L $review_path ]]
}
if report_path_taken; then
  echo "the review report already exists: $review_path" >&2
  echo '  a review that writes no report must not leave an earlier report certified as this run' >&2
  exit 2
fi

# The review material lives per RUN NAME, and its before-state is the only
# record of what the tree looked like when an EARLIER review of that name
# started. A retried review re-renders <run>.review/ BEFORE pi-impl.sh refuses
# the name (it refuses a name that already has evidence), which would delete
# that record and leave a before-state describing a launch that never started.
# Refuse here, before a single byte of it is rewritten.
REVIEW_MATERIAL_DIR=$CONTROLLER_RUNS_DIR/$name.review
if [[ -e $REVIEW_MATERIAL_DIR/before-state.txt ]]; then
  echo "an earlier review of this name already recorded its before-state: $REVIEW_MATERIAL_DIR/before-state.txt" >&2
  echo '  a retried review must use a fresh run name; reusing this one would record a launch that never started' >&2
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
      # A route that names no Open Design account in its own record: `luna`,
      # `sol` and `opencode` never do, and a `.route` that is missing or garbled
      # answers nothing. (Every current writer DOES record the resolved account
      # -- pi-impl.sh:220,289 and codex-impl.sh:248 -- so the thread fallback
      # exists for the states those writers do not produce, and for the
      # defensive case of an unreadable record.) Ask the shared thread record.
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
  # through --git-path. NOTE: `info/exclude` belongs to the REPOSITORY, not to one
  # worktree, so in a linked worktree this resolves to the common dir's file and
  # another session editing it would flag a review (a false alarm, the safe
  # direction). An absent file is recorded as absent rather than as empty.
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

# --- the review budget -------------------------------------------------------
# PI_REVIEW_MAX_SECONDS default: 7200 s (two hours), not 1800. A max-effort pi
# review of a ~1200-line diff did not finish inside 3600 s, so a 30-minute
# default stopped real reviews and reported them as budget stops. The budget
# bounds a runaway; it must not pace an expected review.
review_max_seconds=${PI_REVIEW_MAX_SECONDS:-7200}
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
# The limits a run actually got, in its own log: a stopped review and a
# surprising default are then read from the record, not re-derived from docs.
printf 'pi-review: budget wall=%ss stream=%s bytes poll=%ss kill-wait=%ss\n' \
  "$review_max_seconds" "$review_max_bytes" "$review_poll" "$review_kill_wait" >&2

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
# (REVIEW_MATERIAL_DIR is declared with the report/name refusals above, so the
# refusal can run before this function writes anything.)
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

# The read-only tool set has ONE definition: `pi-review-argv` beside
# pi-impl.sh, which pi-impl.sh reads in review mode and puts in its own argv, so
# the first run, every automatic continuation and every resume carry it alike.
# This launcher sets the mode instead of exporting the list through the
# PI_EXTRA_ARGS seam (which pi-impl.sh also refuses, by name, for anything that
# would add a tool, an extension, a skill, a prompt template or project trust in
# review mode). `export` is used so the value survives into the launch below;
# a caller's PI_EXTRA_ARGS is cleared rather than replaced, because review mode
# refuses a widened seam by name and does not need a restricted one.
# shellcheck disable=SC2034 # PI_EXTRA_ARGS is read by pi-impl.sh, not here
export PI_RUN_MODE=review PI_EXTRA_ARGS=''

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
# persisted below from the run's final message. PI_RUN_MODE is exported rather
# than passed as an assignment: the sticky mode record is what a later resume
# reads, and this launch must state its mode the same way every other launch
# does.
PI_ROUTE="deepseek-pi-$review_account" PI_REPORT='' \
  "$SCRIPT_DIR/pi-impl.sh" "$worktree" "$review_prompt" "$name" &
launch_pid=$!
set +m

# `set -m` is what gives the launch its own process group, and a group signal
# is only a stop if that is TRUE. It is read from the kernel, not assumed: a
# `kill -- -PID` against a group the launch does not lead is ESRCH, an error
# that `|| true` would swallow while `kill -0` keeps answering "alive" and the
# wrapper then waits out the whole run with a budget that never stopped it.
launch_pgid=''
launch_pgid=$(ps -o pgid= -p "$launch_pid" 2>/dev/null | tr -d '[:space:]')
if [[ $launch_pgid =~ ^[0-9]+$ ]] && (( launch_pgid == launch_pid )); then
  stop_group=1
  stop_form=group
else
  # Not its own group leader: signal the launch pid alone. That reaches
  # pi-impl.sh and NOT the pi it execs, which the message says -- a stop that
  # cannot reach the reviewer's process is not the same guarantee, and the
  # marker records the form so a reader of the marker alone sees it too.
  stop_group=0
  stop_form=pid
fi

# Three states, from two facts that really answer: `kill -0` (does the pid
# exist at all) and `ps` (its state, so a ZOMBIE -- which has already ended and
# only waits to be collected -- is "gone" and not "running"). A liveness the
# second fact cannot answer is "unknown": never "alive" (which would let a
# missing `ps` block a stop forever) and never "gone" (which would certify a
# stop that was never observed). The poll loop continues on "unknown" so the
# WALL budget still fires; the post-stop path decides on "gone" only.
launch_state=''
launch_state_of() {
  local pid=$1 stat
  launch_state=unknown
  kill -0 "$pid" 2>/dev/null || { launch_state=gone; return 0; }
  stat=$(ps -o stat= -p "$pid" 2>/dev/null | tr -d '[:space:]') || stat=''
  [[ -n $stat ]] || { launch_state=unknown; return 0; }
  case $stat in
    Z* | X*) launch_state=gone ;;
    *) launch_state=alive ;;
  esac
  return 0
}
stop_launch() {
  # $1 TERM|KILL. One signal to the group when that is really the launch's
  # group, else one to the launch pid. The form used is printed: a caller reading
  # the log must be able to tell which guarantee it got.
  local signal=$1
  if (( stop_group == 1 )); then
    kill -"$signal" -- "-$launch_pid" 2>/dev/null || true
    printf 'pi-review: budget stop %s to the launch process group (%s)\n' "$signal" "$launch_pid" >&2
  else
    kill -"$signal" "$launch_pid" 2>/dev/null || true
    printf 'pi-review: budget stop %s to the launch pid alone (%s): it is not its own process group leader (pgid=%s), so the pi it started is not signalled\n' \
      "$signal" "$launch_pid" "${launch_pgid:-unknown}" >&2
  fi
}

started_epoch=$EPOCHSECONDS
# Poll in 0.2s slices up to PI_REVIEW_POLL_SECONDS, so a run that ends inside
# the interval is reaped at once instead of after a full interval (the suite
# runs many short launches and must not pay the long-case interval each time).
next_poll=$started_epoch
# NOTE on the loop condition: bash reaps an exited background child itself, so
# `kill -0` stops answering as soon as the launch ends; that is the cheap
# "it is over" signal here. The three-state reading is used only where a
# decision depends on it (the stop below), so a normal run pays no extra fork.
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
stopped_after_kill=0
if [[ -n $budget_reason ]]; then
  # TERM, give it PI_REVIEW_KILL_WAIT_SECONDS to settle, then KILL what is left.
  stop_launch TERM
  ticks=$(( review_kill_wait * 10 ))
  while (( ticks > 0 )) && launch_state_of "$launch_pid" && [[ $launch_state != gone ]]; do
    sleep 0.1
    ticks=$(( ticks - 1 ))
  done
  launch_state_of "$launch_pid"
  if [[ $launch_state != gone ]]; then
    stop_launch KILL
    # A KILL window is best effort. Never wait forever on it: a launch that is
    # still there is reported and the wrapper exits anyway, because a budget
    # guarantee that can hang is not a guarantee.
    ticks=$(( review_kill_wait * 10 ))
    while (( ticks > 0 )) && launch_state_of "$launch_pid" && [[ $launch_state != gone ]]; do
      sleep 0.1
      ticks=$(( ticks - 1 ))
    done
    launch_state_of "$launch_pid"
    [[ $launch_state == gone ]] || stopped_after_kill=1
  fi
fi
if (( stopped_after_kill == 0 )); then
  # Gone, or observed gone before the stop: reaping it is what keeps a zombie
  # from being read as a running review, and it cannot block (the child has
  # ended).
  wait "$launch_pid" || launch_rc=$?
fi

if [[ -n $budget_reason ]]; then
  marker=$CONTROLLER_RUNS_DIR/$name.done
  if marker_is_ours; then
    # pi-impl.sh settled while the stop was in flight: keep its outcome as the
    # record and add this invocation's verdict beside it (every reader scans the
    # whole marker). A marker written BY THIS LAUNCH is the one place the verdict
    # belongs. The form the stop used is part of the record: a `pid` stop reaches
    # pi-impl.sh only, so the reviewer's own `pi` may still be running and the
    # marker must not read as "the review is over".
    printf 'REVIEW-BUDGET\nREVIEW-BUDGET-STOP=%s\n' "$stop_form" >> "$marker"
  elif [[ $marker_before == absent ]]; then
    # The launch never wrote a marker of its own: record the verdict alone rather
    # than leaving the run with no evidence at all.
    printf 'EXIT=5 OUTCOME=REVIEW-BUDGET\nREVIEW-BUDGET-STOP=%s\n' "$stop_form" > "$marker"
  else
    echo "the review was stopped over budget ($budget_reason) but left no marker of its own: $name" >&2
    echo "  $marker is an earlier run's record; nothing was appended to it" >&2
  fi
  if (( stopped_after_kill == 1 )); then
    # The launch never returned, so there is no exit status to print: a `0`
    # here would be an invented fact about a child nobody reaped.
    printf 'REVIEW-BUDGET: the review was stopped over budget (%s); pi-impl exit unknown (the launch is still there)\n' \
      "$budget_reason" >&2
    # The stop is NOT confirmed: a process still there after TERM and KILL, or a
    # liveness `ps` could not read (which is reported as unknown rather than as
    # either "alive" or "stopped"). Name the pid, say which was observed, exit
    # anyway: waiting here is what would block for the rest of the run.
    printf 'REVIEW-BUDGET: launch %s not confirmed stopped after TERM+KILL (last state: %s); not waiting for it\n' \
      "$launch_pid" "${launch_state:-unknown}" >&2
  else
    printf 'REVIEW-BUDGET: the review was stopped over budget (%s); pi-impl exit %s\n' \
      "$budget_reason" "$launch_rc" >&2
  fi
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
#
# It is saved ONLY for a settled-success outcome class. Having text in
# last.txt is not the same thing: pi writes partial text for a
# `stopReason=length` message, so a review cut off by its context window leaves
# text and a zero exit from pi-impl (CONTEXT-FULL). Persisting that would
# certify a review that was explicitly cut off mid-way, which is the class of
# failure this wrapper exists to close.
run_outcome=''
read_run_outcome() {
  # The outcome class pi-impl.sh recorded for THIS launch: line 1 of the
  # marker, which is `<run>.done`'s historical `EXIT=<n> OUTCOME=<class>`. A
  # marker that is not ours belongs to an earlier invocation of the name and
  # answers nothing about this run.
  local first
  run_outcome=''
  marker_is_ours || return 1
  first=$(head -n 1 "$marker" 2>/dev/null) || return 1
  case $first in
    *OUTCOME=*) run_outcome=${first#*OUTCOME=} ;;
    *) return 1 ;;
  esac
  run_outcome=${run_outcome%%[[:space:]]*}
  [[ -n $run_outcome ]] || return 1
  return 0
}
marker=$CONTROLLER_RUNS_DIR/$name.done
# Read once, for every exit path: the class is the reason printed beside a
# nonzero status too.
read_run_outcome || :
if (( launch_rc == 0 )) && [[ $run_outcome == SUCCESS || $run_outcome == UNCOMMITTED ]] &&
  [[ -s $CONTROLLER_RUNS_DIR/$name.last.txt ]] &&
  grep -q '[^[:space:]]' "$CONTROLLER_RUNS_DIR/$name.last.txt"; then
  # Write to the RESOLVED path, the one every check above reasoned about, and
  # re-check it first: a symlink that appeared between the refusal and this
  # write would otherwise land the report where nothing was compared. The
  # re-check and the `cp` are two steps, so this is check-then-use (unavoidable
  # in bash): a writer that creates the path BETWEEN these two lines still wins
  # that instant. What it closes is the window between the pre-launch refusal
  # and this write, which is a whole run wide, not that instant.
  if report_path_taken; then
    # Neither branch below persists a report, so this launch's marker must carry
    # the verdict: line 1 is pi-impl.sh's `EXIT=0 OUTCOME=SUCCESS`, and a reader
    # of line 1 alone would certify a review whose report was never written.
    [[ -e $marker ]] && marker_is_ours && printf 'REVIEW-NO-REPORT\n' >> "$marker"
    echo "the review report appeared before the write: $review_path" >&2
    echo '  a review that writes no report must not leave an earlier report certified as this run' >&2
    echo '  REVIEW-NO-REPORT is recorded beside pi-impl.sh line 1, so no reader sees this run as green' >&2
    exit 2
  fi
  if ! cp -- "$CONTROLLER_RUNS_DIR/$name.last.txt" "$review_path"; then
    # The same rule for a write that fails outright (a full or read-only report
    # directory, a concurrent chmod): exit 2 with the verdict in the marker.
    [[ -e $marker ]] && marker_is_ours && printf 'REVIEW-NO-REPORT\n' >> "$marker"
    echo "cannot save the review report at $review_path" >&2
    echo '  REVIEW-NO-REPORT is recorded beside pi-impl.sh line 1, so no reader sees this run as green' >&2
    exit 2
  fi
  exit 0
fi
if (( launch_rc == 0 )); then
  # Exit 0 means exactly one thing for a review: it settled in a success class
  # AND its report was saved. Everything else is a non-zero code, with the class
  # written beside pi-impl.sh's own line so no reader sees it as green.
  case $run_outcome in
    SUCCESS | UNCOMMITTED)
      # A success class with no text to persist: pi-impl.sh's SUCCESS requires
      # text, so this is the class/last.txt pair disagreeing. Report it as the
      # missing report it is rather than inventing a class.
      [[ -e $marker ]] && marker_is_ours && printf 'REVIEW-NO-REPORT\n' >> "$marker"
      echo "REVIEW-NO-REPORT: the review settled in class $run_outcome with no review text to persist; nothing was written to $review_path" >&2
      exit 2
      ;;
    EMPTY-COMPLETION | EMPTY-COMPLETION-RETRIES-EXHAUSTED)
      # A settled run that said nothing leaves no review at all: the class is the
      # reason (empty or thinking-only), and this is still exit 0 from pi.
      [[ -e $marker ]] && marker_is_ours && printf 'REVIEW-NO-REPORT\n' >> "$marker"
      echo "REVIEW-NO-REPORT: the review settled in class $run_outcome without any review text; nothing was written to $review_path" >&2
      exit 2
      ;;
    *)
      # CONTEXT-FULL, TOOL-CALL-NO-TEXT, NO-REPORT, INCOMPLETE, ERROR,
      # USAGE-LIMIT or an unreadable class: the run settled with a zero exit but
      # is NOT a success, and its final text (a `stopReason=length` message DOES
      # carry text) is a cut-off fragment. Persisting it would certify a review
      # that did not complete.
      [[ -e $marker ]] && marker_is_ours && printf 'REVIEW-NOT-SETTLED\n' >> "$marker"
      echo "REVIEW-NOT-SETTLED: the review settled in class ${run_outcome:-unknown}, which is not a success class (SUCCESS or UNCOMMITTED); nothing was written to $review_path" >&2
      exit 6
      ;;
  esac
fi
# A nonzero pi/pi-impl status passes through unchanged (never 0), and it writes
# no report either: the class, when one was recorded, is printed for the reader.
if [[ -n $run_outcome ]]; then
  printf 'pi-review: the review ended in class %s with pi-impl exit %s; no report was written\n' \
    "$run_outcome" "$launch_rc" >&2
fi
exit "$launch_rc"
