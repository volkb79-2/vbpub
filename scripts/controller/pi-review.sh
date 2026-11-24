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
# Read-only. Before and after the launch the wrapper records the worktree's
# `git rev-parse HEAD`, `git status --porcelain=v1 -uall` (untracked files
# included, ignored files not) and a hash of `git diff HEAD`. Any difference
# appends the line `REVIEW-MODIFIED-TREE` to <run>.done, prints it, and exits
# 4; the changes are never reverted here, the controller decides. A worktree
# whose BEFORE-state cannot be recorded is refused (exit 2), and one whose
# AFTER-state cannot be read takes the same `REVIEW-MODIFIED-TREE` line with
# the reason on stderr, because the guarantee can no longer be certified.
# A dirty worktree is allowed (a review is compared with its own before-state);
# a review REPORT inside the worktree is refused, because a reviewer could
# write it there and an ignored path would not even show in status.
#
# The rest is pi-impl.sh's, unchanged: its outcome vocabulary, its report check
# (--report is passed through, so a review that writes no review file is
# OUTCOME=NO-REPORT) and its run records. The model and thinking level are
# pi-impl.sh's defaults (the eco model at `max`) unless the caller already set
# PI_MODEL / PI_THINKING, which pi-impl.sh preserves.
#
# Exit codes: 2 for the refusals above (usage, unreadable worktree, report
# inside the worktree, unknown account, account records that name none),
# otherwise pi-impl.sh's own code, returned unchanged (0 settled, 1 or another
# pi exit status, 2 refused, 3 both Open Design accounts blocked). 4 is
# REVIEW-MODIFIED-TREE and is not a pi-impl.sh code.
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
review_path=$(realpath -m -- "$report_path") || review_path=$report_path
case $review_path in
  "$worktree" | "$worktree"/*)
    echo "report path is inside the reviewed worktree: $review_path" >&2
    exit 2
    ;;
esac

review_account=''
if [[ -n $account ]]; then
  review_account=$account
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
        else
          echo "no Open Design account is recorded for implementer thread $thread_id (lookup exit $lookup_rc)" >&2
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
TREE_STATUS=''
TREE_DIFF=''
record_tree_state() {
  local wt=$1 head status diff
  head=$(git -C "$wt" --no-optional-locks rev-parse HEAD 2>/dev/null) || return 1
  status=$(git -C "$wt" --no-optional-locks status --porcelain=v1 -uall 2>/dev/null) || return 1
  # `diff HEAD` covers staged and unstaged tracked changes in one value; `--binary`
  # keeps the bytes (not the patch text) the thing that is hashed.
  diff=$(git -C "$wt" --no-optional-locks diff HEAD --binary 2>/dev/null |
    git hash-object --stdin 2>/dev/null) || return 1
  TREE_HEAD=$head
  TREE_STATUS=$status
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
before_status=$TREE_STATUS
before_diff=$TREE_DIFF

launch_rc=0
PI_ROUTE="deepseek-pi-$review_account" \
  "$SCRIPT_DIR/pi-impl.sh" "$worktree" "$prompt" "$name" --report "$report_path" || launch_rc=$?

changed=''
if ! record_tree_state "$worktree"; then
  changed='the worktree state can no longer be read'
else
  [[ $TREE_HEAD == "$before_head" ]] || changed='HEAD'
  [[ $TREE_STATUS == "$before_status" ]] || changed="${changed:+$changed, }status"
  [[ $TREE_DIFF == "$before_diff" ]] || changed="${changed:+$changed, }diff"
fi
if [[ -n $changed ]]; then
  printf 'REVIEW-MODIFIED-TREE\n' >> "$CONTROLLER_RUNS_DIR/$name.done"
  printf 'REVIEW-MODIFIED-TREE: %s (pi-impl exit %s); the worktree is left as it is\n' \
    "$changed" "$launch_rc" >&2
  exit 4
fi
exit "$launch_rc"
