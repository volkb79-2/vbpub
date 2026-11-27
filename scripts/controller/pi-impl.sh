#!/usr/bin/env bash
# Launch one pi Open Design job and persist its event stream, overwatch state
# and completion outcome.
# Usage: pi-impl.sh <worktree> <absolute-prompt-file> <run-name> [resume <session-id>] [--report <absolute-path>]
#
# Outcome vocabulary written to <run>.done as `EXIT=<n> OUTCOME=<class>`
# (the first line keeps its historical shape; extra lines are additive):
#
#   SUCCESS          settled `stop` with assistant text, a clean worktree, and
#                    the expected report present when a report path is known.
#   TOOL-CALL-NO-TEXT a settled `stop` whose final assistant message carried a
#                    tool call and no text. Not a success: the run ended on a
#                    call, so the work it was about to do is not evidenced. Not
#                    an EMPTY-COMPLETION either (that class is "no text AND no
#                    tool call") and therefore never continued.
#   CONTEXT-FULL     a settled run whose final `stopReason` is `length`: the
#                    context window filled and the harness stopped the model.
#                    The work is intact; the controller starts a FRESH successor
#                    from the run's forward-oriented state file. The wrapper
#                    never continues it (continuing a full context is the one
#                    continuation that cannot succeed).
#   EMPTY-COMPLETION the final assistant message carried no text (empty or
#                    thinking-only) and no tool call. This is a provider/model
#                    anomaly, not a limit: the wrapper continues the same
#                    session (see <run>.events) before giving up.
#   EMPTY-COMPLETION-RETRIES-EXHAUSTED
#                    still empty after the bounded continues; the controller
#                    decides to restart or abandon.
#   UNCOMMITTED      exit 0 and the worktree has uncommitted changes.
#   NO-REPORT        the expected report file is missing.
#   USAGE-LIMIT      a provider 402 `insufficient_balance` stop.
#   ERROR            a provider error stop, or a nonzero pi exit.
#   INCOMPLETE       a settled or unreadable stream that is not a stop and not
#                    a named limit (an aborted or unsettled stream, a stop the
#                    classifier cannot attribute), or a finish whose worktree
#                    status cannot be read.
#
# The expected report is `--report <path>` or `PI_REPORT`; unset means no
# report check. Every active run also gets <run>.live (liveness/progress,
# read by job-status.sh) and <run>.events (JSON lines, one per continue).
#
# `PI_EXTRA_ARGS` adds pi arguments to this launch: ONE ARGV ELEMENT PER LINE
# (newline separated, so a value may itself contain spaces; no word splitting
# and no eval, so nothing in it is ever reinterpreted as shell syntax). It is
# appended AFTER pi-impl.sh's own arguments and before the `--` prompt
# separator, and it may NOT set the launch contract pi-impl.sh owns: `-p`,
# `--print`, `--mode`, `--model`, `--thinking`, `--session`, `--session-id`,
# `--session-dir`, `-c`/`--continue`, `-r`/`--resume`, `--fork` and a bare `--`
# are refused before anything is claimed or launched. The seam exists for a
# caller that must restrict pi's tools; an empty or unset value adds nothing.
# Implementer mode refuses extension load/disable flags so this seam cannot
# replace the fixed PID namespace backend. In REVIEW mode the seam is narrower
# still (see PI_RUN_MODE below).
#
# PI_RUN_MODE selects the contract this launch runs under:
#
#   implementer (the default) the implementer tool set pi supplies itself, and
#                            every prompt this launch sends gets the fixed
#                            implementer rule paragraph in
#                            implementer-prompt-rules.md beside this script.
#   review                   pi's argv ALWAYS carries the read-only set defined
#                            ONCE in pi-review-argv beside this script (no tool
#                            that can run a program and none that can write), a
#                            caller's PI_EXTRA_ARGS is refused by name when it
#                            would add or widen tools, extensions, skills, prompt
#                            templates or project trust, and every prompt gets
#                            the fixed review rule paragraph in
#                            review-prompt-rules.md instead.
#
# Why a mode and not a caller convention: a review launched or RESUMED through
# this wrapper directly (not through pi-review.sh) used to get the full
# implementer tools unless the caller remembered to export the seam. A pi
# reviewer once ran kill/trap scripts in the shared devcontainer for two hours,
# and "the caller remembers" is exactly the kind of rule that does not hold. The
# mode is therefore the wrapper's own switch, and the tool list has one
# definition (pi-review-argv) shared with pi-review.sh, which sets the mode
# instead of carrying a second copy of the literal list.
#
# The mode is STICKY per run name and per pi session. Every accepted launch
# records it beside the other run records as <runs-dir>/<name>.mode
# ('review' or 'implementer'); a resume records the session mode before pi is
# invoked, while a first launch records it after pi returns with the session id.
# On a resume, the name and session
# records are read, <runs-dir>/*.thread is scanned for exact matches, and the
# bounded heads of <runs-dir>/*.jsonl are scanned for session events. Review in
# ANY of those sources forces review; a resume asking
# for implementer mode is refused before the run name is claimed when the
# review source is already present; the claimed name is re-read before argv is
# built to catch a mode written during preflight. Session mode
# writes are locked and monotonic, so review can never be replaced with
# implementer. A resume takes the recorded mode whenever it does not narrow it:
#  * recorded 'review' + a caller that NAMED implementer mode is REFUSED (exit
#    2) before the name is claimed: a review's session never gets a shell. A
#    caller that named no mode takes the record; a value only the host's
#    controller.local.env supplied is a default for launches that name none, so
#    it cannot flip a review either (it is announced, never obeyed, on a resume).
#  * recorded 'implementer' + a caller asking for review mode is allowed (the
#    stricter mode) and the record is rewritten to 'review'.
#  * a record that exists but is neither word is REFUSED: no correct writer
#    produces it, and guessing would be a fallback that cannot fail loudly.
#  * a record with review EVIDENCE beside it (<runs-dir>/<name>.review/
#    before-state.txt, written by pi-review.sh) is read as review even when the
#    record is missing: the session is then known to be a review, and the safe
#    direction is failing closed.
#  * missing records with no review evidence and no matching session/thread
#    or stream record saying review keep the mode the caller named (or the
#    default), so a pre-existing IMPLEMENTER session resumes unchanged.
#  * a resume id must be named EXACTLY by a thread record, session-mode record,
#    or run-stream session event. Pi's prefix lookup is not allowed here, and
#    ids ending `.jsonl` are refused because pi treats them as stream paths.
# The caller's value wins over controller.local.env in the LESS SAFE direction:
# PI_RUN_MODE is saved before that file is sourced and re-applied after, so a
# stale `PI_RUN_MODE=implementer` in a local environment file cannot flip a
# review back to the implementer tool set.
#
# Review mode enforces its tool selection, not a sandbox: the `read` tool still
# reads anything the wrapper's user can (credentials included). Implementer
# bash runs in its own user and PID namespace, which prevents its builtin `kill`
# and direct signal syscalls from addressing outer processes by PID. The prompt
# still forbids experiments in the shared devcontainer; the pi-shims PATH entry
# also refuses ordinary `pkill`, `killall` and signalling `pgrep` spellings.
#
# A run NAME is owned by one live wrapper: the wrapper takes an exclusive
# flock on <runs-dir>/<name>.lock before it writes anything (lib.sh
# controller_run_claim) and releases it on every exit path, so two wrappers on
# one name can never interleave on the name-owned records. A second invocation
# is refused with exit 2 and the live owner's pid; the kernel releases the
# lock when the owner dies, so a TERMed or KILLed wrapper cannot strand a name.
# CONTROLLER_RUN_FORCE=1 retires the previous owner's records into
# <runs-dir>/retired/<name>/<utc-stamp>/ (never deletes them) and is refused
# while an owner is still alive.
#
# Signal guard. Every pi run this wrapper starts gets
# <this dir>/pi-shims FIRST on its PATH, for the first run, a resume and every
# automatic continuation alike. `pkill`, `killall` and a `pgrep` carrying a
# signal argument refuse there with `pi-shim: refused <name>` and exit 126: a
# pattern kill in the shared devcontainer hits other controller sessions (a pi
# agent whose brief banned it still ran `pkill -f 'sleep 30'` and killed other
# sessions' monitors), and a written ban did not hold. Plain `pgrep` passes
# through to the real pgrep. The shims are checked before launch and the launch
# is refused (exit 2) if any is missing or not executable; no environment
# variable switches the guard off.
#
# pi's bash tool inherits PATH and does not run a login shell: it spawns
# /bin/bash with `-c` (dist/utils/shell.js:17-18 and :112-119) under an env that
# is `{...process.env}` with pi's own bin dir prepended (dist/utils/shell.js:147-157,
# passed at dist/core/tools/bash.js:78 and :160/:287). That inherited env is why
# the wrapper's PATH assignment reaches the agent, and the prepended bin dir is
# why a launch is refused while `$PI_CODING_AGENT_DIR/bin` (default
# `$HOME/.pi/agent/bin`) holds any of the three names. That value is read the
# way pi reads it (dist/utils/paths.js `normalizePath` expands a leading `~/`
# against the home directory; dist/config.js:511-513), and a value that is not
# absolute after that expansion is refused, because pi would join it to ITS OWN
# working directory, which this wrapper cannot check beforehand.
#
# PID namespace guard. Every implementer launch also loads the tracked
# pi-pidns-extension.js explicitly with `-e` and `--no-extensions`. That
# extension replaces the built-in bash tool and handles user `!`/`!!` commands;
# both call pi-pidns-bash, which starts each command in a fresh user+PID
# namespace. The project settings file cannot replace the explicit extension:
# `--no-extensions` selects only CLI extension paths (dist/core/resource-loader.js:426-428),
# a same-name extension tool replaces built-in bash (docs/extensions.md:2358-2360),
# and the extension supplies its own shellPath through createLocalBashOperations
# (dist/core/tools/bash.js:156-157). Pi's user_bash event covers `!` and `!!`
# (docs/extensions.md:1078-1095). Review mode has no bash in its fixed tool set
# and does not load this extension.
#
# This isolates bash process signalling, not filesystem access: file writes and
# other Unix sockets remain available. `/proc` is deliberately left as the host
# procfs because `--proc /proc` is unavailable in the supported container; a
# shell can see outer PIDs there, but its PID namespace cannot signal them. PID
# 1 inside is bwrap, so `kill -0 1` succeeds by design. `sudo` fails inside, and
# background jobs end with their command. Only the Docker default socket and an
# existing socket named by DOCKER_HOST=unix:// outside /run/docker-api are
# covered; other Unix sockets and network access are not isolated.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
caller_pi_route=${PI_ROUTE-}
caller_pi_model=${PI_MODEL-}
caller_pi_thinking=${PI_THINKING-}
caller_pi_session_dir=${PI_SESSION_DIR-}
caller_pi_extra_args=${PI_EXTRA_ARGS-}
# `${VAR-}` cannot tell an UNSET value from an explicitly EMPTY one, and the
# difference decides what a launch does with a host's controller.local.env (see
# the restore below). `-v` answers set-ness even for an empty value.
caller_pi_extra_args_set=0
[[ -v PI_EXTRA_ARGS ]] && caller_pi_extra_args_set=1
caller_pi_run_mode=${PI_RUN_MODE-}
caller_pi_run_mode_set=0
[[ -v PI_RUN_MODE ]] && caller_pi_run_mode_set=1
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
[[ -z $caller_pi_route ]] || PI_ROUTE=$caller_pi_route
[[ -z $caller_pi_model ]] || PI_MODEL=$caller_pi_model
[[ -z $caller_pi_thinking ]] || PI_THINKING=$caller_pi_thinking
[[ -z $caller_pi_session_dir ]] || PI_SESSION_DIR=$caller_pi_session_dir
# The caller's extra arguments win over a local environment file, like the four
# above: a stale PI_EXTRA_ARGS in controller.local.env must not widen (or narrow)
# a launch the caller configured, and a caller who EXPORTED it as empty has said
# "no extra arguments" -- not "unset", which would let that file's value in.
if (( caller_pi_extra_args_set == 1 )); then PI_EXTRA_ARGS=$caller_pi_extra_args; fi
# The same rule, and it is the one that matters for safety: a caller that asks
# for review mode is not flipped back to the implementer tool set by a
# `PI_RUN_MODE=implementer` line in controller.local.env (lib.sh sources that
# file with `set -a`). A caller that sets nothing keeps whatever the file says;
# an explicit empty value stays empty and is refused below.
if (( caller_pi_run_mode_set == 1 )); then PI_RUN_MODE=$caller_pi_run_mode; fi

usage() {
  echo "usage: $0 <worktree> <absolute-prompt-file> <run-name> [resume <session-id>] [--report <absolute-path>]" >&2
  exit 2
}

report_path=''
positional=()
while (( $# > 0 )); do
  case $1 in
    --report)
      (( $# >= 2 )) || usage
      report_path=$2
      shift 2
      ;;
    --report=*)
      report_path=${1#--report=}
      shift
      ;;
    *)
      positional+=("$1")
      shift
      ;;
  esac
done
[[ -z ${PI_REPORT-} ]] || report_path=${report_path:-$PI_REPORT}
set -- "${positional[@]}"
[[ $# -ge 3 && $# -le 5 ]] || usage
if [[ -n $report_path ]]; then
  [[ $report_path == /* ]] || { echo 'report path must be absolute' >&2; exit 2; }
fi
[[ $2 == /* ]] || { echo 'prompt path must be absolute' >&2; exit 2; }
worktree=$(realpath -e -- "$1") || usage
prompt=$(realpath -e -- "$2") || usage
name=$3
[[ $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $name != *..* ]] || {
  echo "invalid run name: $name" >&2
  exit 2
}
mode=${4:-new}
session_id=${5:-}
valid_session_id() {
  [[ $1 =~ ^[A-Za-z0-9._-]+$ && $1 != *..* && $1 != . && $1 != *.jsonl ]]
}
if [[ $mode == resume ]]; then
  valid_session_id "$session_id" || usage
elif [[ $mode != new || $# -gt 4 ]]; then
  usage
fi

# --- the run mode: review or implementer (see the header) ---------------------
# Two facts are kept apart here:
#  * WHICH mode was NAMED (requested_mode, with request_origin recording who
#    named it), and
#  * which mode a launch RUNS under once the run-name record has been read.
# `-v` answers "was it set" even for an explicitly EMPTY value, so a set-but-empty
# request is refused by name instead of being defaulted: a launch that named
# nothing takes the default, one that named something wrong is told so.
requested_mode=''
request_origin=''
if [[ -v PI_RUN_MODE ]]; then
  requested_mode=$PI_RUN_MODE
  case $requested_mode in
    implementer | review) ;;
    *)
      echo "PI_RUN_MODE must be 'implementer' or 'review' (got '$requested_mode')" >&2
      exit 2
      ;;
  esac
  # Who named it decides what it may do: a CALLER's value is a request (it may
  # narrow a recorded implementer run to review), while a value only the host's
  # controller.local.env supplied is a DEFAULT for launches that name none.
  if (( caller_pi_run_mode_set == 1 )); then request_origin=caller; else request_origin=local-env; fi
fi
mode_record=$CONTROLLER_RUNS_DIR/$name.mode
session_modes_dir=$CONTROLLER_RUNS_DIR/session-modes
session_mode_lock_fd=''
session_mode_lock_id=''
mode_record_value=''
session_mode_record_value=''
session_review_found=0
session_review_source=''

read_mode_record_if_present() {
  local path=$1
  mode_record_value=''
  if [[ ! -e $path && ! -L $path ]]; then return 1; fi
  if [[ -L $path || ! -f $path || ! -r $path ]]; then
    printf 'cannot establish run mode: %s is unreadable or not a regular file\n' "$path" >&2
    return 2
  fi
  mode_record_value=$(<"$path") || {
    printf 'cannot establish run mode: %s could not be read\n' "$path" >&2
    return 2
  }
  case $mode_record_value in
    implementer | review) return 0 ;;
    *)
      printf "cannot establish run mode: %s is not 'review' or 'implementer'\n" "$path" >&2
      return 2
      ;;
  esac
}

inspect_review_material() {
  local directory=$1 before_state
  before_state=$directory/before-state.txt
  review_material_present=0
  if [[ -L $directory || ( -e $directory && ( ! -d $directory || ! -r $directory || ! -x $directory ) ) ]]; then
    printf 'cannot establish review mode: review material directory is unreadable or not searchable: %s\n' \
      "$directory" >&2
    return 2
  fi
  [[ -e $directory ]] || return 0
  if [[ -e $before_state || -L $before_state ]]; then
    if [[ -L $before_state || ! -f $before_state || ! -r $before_state ]]; then
      printf 'cannot establish review mode: review before-state is unreadable or not a regular file: %s\n' \
        "$before_state" >&2
      return 2
    fi
    review_material_present=1
  fi
}

# Session events are at the head of pi's JSONL stream. Read at most 64 KiB per
# file: run streams can be gigabytes, and later events are not needed to bind a
# run name to the session pi opened. Any unreadable candidate makes the scan
# incomplete and therefore refuses a resume rather than being treated as no
# record.
scan_run_stream_sessions() {
  python3 - "$1" <<'PY'
import json
import re
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
run_name_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
session_id_pattern = re.compile(r"^[A-Za-z0-9._-]+$")
for path in sorted(run_dir.glob("*.jsonl")):
    if path.is_symlink() or not path.is_file():
        print(f"cannot scan pi session stream: {path} is not a regular file", file=sys.stderr)
        raise SystemExit(2)
    try:
        with path.open("rb") as source:
            head = source.read(65536)
    except OSError as error:
        print(f"cannot scan pi session stream: {path}: {error}", file=sys.stderr)
        raise SystemExit(2)
    for line in head.splitlines():
        try:
            event = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != "session":
            continue
        value = event.get("id") or event.get("sessionId")
        if not isinstance(value, str) or not session_id_pattern.fullmatch(value):
            break
        name = path.name[:-len(".jsonl")]
        if not run_name_pattern.fullmatch(name) or ".." in name:
            print(f"cannot use pi session stream with an invalid run name: {path}", file=sys.stderr)
            raise SystemExit(2)
        print(f"{name}\t{value}")
        break
PY
}

acquire_session_mode_lock() {
  local id=$1 lock_path nonblocking=${2:-0}
  valid_session_id "$id" || {
    printf 'cannot use unsafe pi session id for a mode record: %s\n' "$id" >&2
    return 2
  }
  if [[ -n $session_mode_lock_fd && $session_mode_lock_id == "$id" ]]; then return 0; fi
  if [[ -n $session_mode_lock_fd ]]; then
    echo 'cannot acquire two pi session mode locks in one launch' >&2
    return 2
  fi
  if [[ -L $session_modes_dir || ( -e $session_modes_dir && ! -d $session_modes_dir ) ]]; then
    printf 'cannot use pi session mode directory: %s is not a real directory\n' "$session_modes_dir" >&2
    return 2
  fi
  mkdir -p -- "$session_modes_dir" || {
    printf 'cannot create pi session mode directory: %s\n' "$session_modes_dir" >&2
    return 2
  }
  lock_path=$session_modes_dir/$id.lock
  exec {session_mode_lock_fd}> "$lock_path" || {
    printf 'cannot open pi session mode lock: %s\n' "$lock_path" >&2
    session_mode_lock_fd=''
    return 2
  }
  if [[ $nonblocking == 1 ]]; then
    flock -x -n "$session_mode_lock_fd" || {
      printf 'cannot lock pi session mode without waiting: %s\n' "$lock_path" >&2
      release_session_mode_lock
      return 2
    }
  elif ! flock -x "$session_mode_lock_fd"; then
    printf 'cannot lock pi session mode: %s\n' "$lock_path" >&2
    release_session_mode_lock
    return 2
  fi
  session_mode_lock_id=$id
}

release_session_mode_lock() {
  [[ -n $session_mode_lock_fd ]] || return 0
  exec {session_mode_lock_fd}>&-
  session_mode_lock_fd=''
  session_mode_lock_id=''
}

write_session_mode() {
  local id=$1 requested_mode=${2:-$run_mode} own_lock=0 path stored_mode='' write_mode rc=0
  write_mode=$requested_mode
  [[ $requested_mode == review || $requested_mode == implementer ]] || {
    printf 'cannot write invalid pi session mode: %s\n' "$requested_mode" >&2
    return 2
  }
  valid_session_id "$id" || {
    printf 'cannot use unsafe pi session id for a mode record: %s\n' "$id" >&2
    return 2
  }
  if [[ $session_mode_lock_id != "$id" ]]; then
    acquire_session_mode_lock "$id" || return 2
    own_lock=1
  fi
  path=$session_modes_dir/$id
  if read_mode_record_if_present "$path"; then
    stored_mode=$mode_record_value
  else
    rc=$?
    if (( rc != 1 )); then
      (( own_lock == 0 )) || release_session_mode_lock
      return 2
    fi
  fi
  # A session record only moves toward the stricter mode. In particular, a
  # stale implementer invocation can never replace a review record.
  if [[ $stored_mode == review || $requested_mode == review ]]; then write_mode=review; fi
  if ! printf '%s\n' "$write_mode" > "$path"; then
    printf 'cannot write pi session mode record: %s\n' "$path" >&2
    (( own_lock == 0 )) || release_session_mode_lock
    return 2
  fi
  (( own_lock == 0 )) || release_session_mode_lock
}

# Forced name reuse retires the old thread and stream. Preserve their mode
# under the session id first, using the same monotonic writer as live resumes.
# The lock is nonblocking here: a concurrent resume may hold the session lock
# while waiting for this run-name lock, so waiting in the opposite order could
# deadlock. Refusing the forced reuse leaves the old records intact.
persist_retired_run_session_mode() {
  local run_name=$1 thread_path thread_id='' stream_id='' old_id='' old_mode=''
  local stream_name stream_session_id streams rc old_review_material
  thread_path=$out/$run_name.thread
  inspect_review_material "$out/$run_name.review" || return 2
  old_review_material=$review_material_present
  if [[ -e $thread_path || -L $thread_path ]]; then
    if [[ -L $thread_path || ! -f $thread_path || ! -r $thread_path ]]; then
      printf 'cannot preserve retired pi session mode: thread record is unreadable: %s\n' "$thread_path" >&2
      return 2
    fi
    thread_id=$(<"$thread_path") || return 2
    valid_session_id "$thread_id" || {
      printf 'cannot preserve retired pi session mode: thread record has an invalid session id: %s\n' "$thread_path" >&2
      return 2
    }
  fi
  streams=$(scan_run_stream_sessions "$out") || return 2
  while IFS=$'\t' read -r stream_name stream_session_id; do
    [[ $stream_name == "$run_name" ]] || continue
    stream_id=$stream_session_id
    break
  done <<< "$streams"
  if [[ -n $thread_id && -n $stream_id && $thread_id != "$stream_id" ]]; then
    printf 'cannot preserve retired pi session mode: %s.thread and %s.jsonl name different sessions\n' \
      "$run_name" "$run_name" >&2
    return 2
  fi
  old_id=${thread_id:-$stream_id}
  [[ -n $old_id ]] || return 0

  if read_mode_record_if_present "$out/$run_name.mode"; then
    old_mode=$mode_record_value
  else
    rc=$?
    (( rc == 1 )) || return 2
    if (( old_review_material == 1 )); then
      old_mode=review
    elif read_mode_record_if_present "$session_modes_dir/$old_id"; then
      old_mode=$mode_record_value
    else
      rc=$?
      (( rc == 1 )) || return 2
    fi
  fi
  [[ -n $old_mode ]] || {
    printf 'cannot preserve retired pi session mode: %s has a session id but no recorded mode or review evidence\n' \
      "$run_name" >&2
    printf '  remedy: use a new run name, or write the true mode of that session (the one word review or implementer; review if unsure) to %s and force the reuse again\n' \
      "$out/$run_name.mode" >&2
    return 2
  }
  acquire_session_mode_lock "$old_id" 1 || return 2
  if ! write_session_mode "$old_id" "$old_mode"; then
    release_session_mode_lock
    return 2
  fi
  release_session_mode_lock
}

# A NEW launch is decided by the caller alone: a name that already has a mode
# record is refused as a collision unless CONTROLLER_RUN_FORCE retires the
# records, so the stickiness below belongs to a RESUME only. Reading the record
# of a name this invocation is about to retire would refuse the deliberate
# reuse the force flag exists for.
run_mode=${requested_mode:-implementer}
mode_source=${request_origin:-default}
if [[ $mode == resume ]]; then
  # Serialize mode resolution with every other resume of this session. The lock
  # remains held through the accepted launch's session record write below; a
  # second run name cannot pass a stale implementer read after review was stored.
  acquire_session_mode_lock "$session_id" || exit 2
  stream_sessions=$(scan_run_stream_sessions "$CONTROLLER_RUNS_DIR") || exit 2
  session_id_found=0
  record_mode=''
  mode_record_state=absent
  if read_mode_record_if_present "$mode_record"; then
    record_mode=$mode_record_value
    mode_record_state=valid
  else
    record_rc=$?
    if (( record_rc == 2 )); then mode_record_state=invalid; fi
  fi
  session_mode_path=$session_modes_dir/$session_id
  session_mode_state=absent
  if read_mode_record_if_present "$session_mode_path"; then
    session_mode_record_value=$mode_record_value
    session_mode_state=valid
    session_id_found=1
  else
    record_rc=$?
    if (( record_rc == 2 )); then session_mode_state=invalid; fi
  fi
  [[ $session_mode_state != invalid ]] || {
    echo "cannot establish the run mode of pi session $session_id: $session_mode_path is not 'review' or 'implementer'" >&2
    exit 2
  }
  inspect_review_material "$CONTROLLER_RUNS_DIR/$name.review" || exit 2
  current_review_material_present=$review_material_present
  if (( current_review_material_present == 1 )); then
    session_review_found=1
    session_review_source=review-material
  fi

  # Older runs already have <name>.thread and <name>.mode, but no session-mode
  # record. Match the thread content exactly, then read that name's mode. An
  # unreadable candidate cannot be proven unrelated, so it refuses the resume.
  for thread_path in "$CONTROLLER_RUNS_DIR"/*.thread; do
    [[ -e $thread_path || -L $thread_path ]] || continue
    if [[ -L $thread_path || ! -f $thread_path || ! -r $thread_path ]]; then
      printf 'cannot establish pi session mode: thread record is unreadable: %s\n' "$thread_path" >&2
      exit 2
    fi
    thread_session=$(<"$thread_path") || {
      printf 'cannot establish pi session mode: thread record could not be read: %s\n' "$thread_path" >&2
      exit 2
    }
    [[ $thread_session == "$session_id" ]] || continue
    session_id_found=1
    thread_name=${thread_path##*/}
    thread_name=${thread_name%.thread}
    [[ $thread_name != "$name" ]] || continue
    [[ $thread_name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $thread_name != *..* ]] || {
      printf 'cannot establish pi session mode: matching thread has an invalid run name: %s\n' "$thread_path" >&2
      exit 2
    }
    thread_mode_path=$CONTROLLER_RUNS_DIR/$thread_name.mode
    inspect_review_material "$CONTROLLER_RUNS_DIR/$thread_name.review" || exit 2
    thread_has_review_material=$review_material_present
    if read_mode_record_if_present "$thread_mode_path"; then
      if [[ $mode_record_value == review ]]; then
        session_review_found=1
        session_review_source=thread-record
      fi
    else
      record_rc=$?
      (( record_rc == 1 )) || exit 2
    fi
    if (( thread_has_review_material == 1 )); then
      session_review_found=1
      session_review_source=thread-review-material
    fi
  done
  while IFS=$'\t' read -r stream_name stream_session_id; do
    [[ -n $stream_name && $stream_session_id == "$session_id" ]] || continue
    session_id_found=1
  done <<< "$stream_sessions"
  while IFS=$'\t' read -r stream_name stream_session_id; do
    [[ -n $stream_name && $stream_session_id == "$session_id" ]] || continue
    [[ $stream_name != "$name" ]] || continue
    inspect_review_material "$CONTROLLER_RUNS_DIR/$stream_name.review" || exit 2
    stream_has_review_material=$review_material_present
    stream_mode_path=$CONTROLLER_RUNS_DIR/$stream_name.mode
    if read_mode_record_if_present "$stream_mode_path"; then
      if [[ $mode_record_value == review ]]; then
        session_review_found=1
        session_review_source='stream-record'
      fi
    else
      record_rc=$?
      (( record_rc == 1 )) || exit 2
    fi
    if (( stream_has_review_material == 1 )); then
      session_review_found=1
      session_review_source='stream-review-material'
    fi
  done <<< "$stream_sessions"
  (( session_id_found == 1 )) || {
    printf 'cannot resume unknown pi session id %s: no record names this exact id\n' "$session_id" >&2
    exit 2
  }
  if [[ $session_mode_state == valid && $session_mode_record_value == review ]]; then
    session_review_found=1
    session_review_source=session-record
  fi
  case $mode_record_state in
    valid)
      # A resume takes the RECORD. Refused only when the CALLER asked for
      # implementer mode on a review: a review's session never gets a shell or a
      # writer. A resume that names no mode at all takes the record (that is what
      # the record is for), and a host file's value is not a request against it
      # -- saying so, because a silent override is exactly what a host setting
      # must not be able to do.
      if [[ $record_mode == review && $request_origin == caller && $requested_mode == implementer ]]; then
        echo "run name $name was started as a REVIEW and cannot be resumed in implementer mode" >&2
        echo "  $mode_record records 'review': a review's session never gets a shell or a writer, so the mode is sticky" >&2
        echo '  resume it with PI_RUN_MODE=review, or name no mode at all and the record decides;' >&2
        echo '  retire the name (CONTROLLER_RUN_FORCE=1) only to reuse it for a different run' >&2
        exit 2
      fi
      if [[ $request_origin == local-env && $record_mode != "$requested_mode" ]]; then
        # A host file's value is a DEFAULT for launches that name none, never a
        # request against a record: announced, not obeyed. (The caller's own
        # direction is handled by the refusal above and the narrowing below.)
        printf 'pi-impl: controller.local.env asks for %s mode, but %s is a recorded %s run; the record decides\n' \
          "$requested_mode" "$name" "$record_mode" >&2
      fi
      run_mode=$record_mode
      mode_source=record
      if [[ $record_mode == implementer && $request_origin == caller && $requested_mode == review ]]; then
        # Only this direction is reachable here: review-recording with a caller
        # asking for implementer was refused above. The stricter mode wins, so
        # the session keeps a shell-less, writer-less argv for the rest of its
        # life and the record says so.
        run_mode=review
        mode_source=record-narrowed
        printf 'pi-impl: %s was started as implementer and is now launched as review; the stricter mode wins and the record becomes review\n' "$name" >&2
      fi
      ;;
    invalid)
      echo "cannot establish the run mode of $name: $mode_record is not 'review' or 'implementer'" >&2
      echo '  a record this wrapper never writes is not a fact to guess from; retire the name (CONTROLLER_RUN_FORCE=1) or write the mode it really ran under' >&2
      exit 2
      ;;
    absent)
      # No record: review EVIDENCE beside the name decides. pi-review.sh renders
      # <run>.review/before-state.txt before it launches, so a resume of a name
      # that carries that material is a resume of a review even when the mode
      # record was lost; with no such evidence the mode the launch named (or the
      # default) stands, which is what keeps a pre-existing implementer session
      # unchanged.
      if (( current_review_material_present == 1 )); then
        run_mode=review
        mode_source=review-material
        if (( session_review_found == 0 )); then
          session_review_found=1
          session_review_source=review-material
        fi
        printf 'pi-impl: %s has review material but no mode record; it is resumed as a review (fail closed)\n' "$name" >&2
      fi
      ;;
  esac
  if (( session_review_found == 1 )); then
    if [[ $request_origin == caller && $requested_mode == implementer ]]; then
      if [[ $session_review_source == review-material ]]; then
        printf 'run name %s has review material and cannot be resumed in implementer mode\n' "$name" >&2
      else
        printf 'pi session %s is recorded as a REVIEW and cannot be resumed in implementer mode\n' "$session_id" >&2
      fi
      echo '  a review session never gets a shell or a writer, so the mode is sticky across run names' >&2
      echo '  resume it with PI_RUN_MODE=review, or name no mode at all and the session record decides' >&2
      exit 2
    fi
    if [[ $request_origin == local-env && $requested_mode != review ]]; then
      printf 'pi-impl: controller.local.env asks for %s mode, but session %s is a recorded review; the record decides\n' \
        "$requested_mode" "$session_id" >&2
    fi
    run_mode=review
    [[ $mode_record_state == valid && $record_mode == review ]] || mode_source=$session_review_source
  fi
fi
case $run_mode in
  review) review_argv_file=$SCRIPT_DIR/pi-review-argv ;;
  *) review_argv_file='' ;;
esac
review_argv=()
if [[ $run_mode == review ]]; then
  # The one definition of the read-only set. A review that cannot name its tool
  # set must not start: refusing here is the same rule as the signal guard's, and
  # it happens before the name is claimed, so a refusal leaves no run record.
  if [[ ! -r $review_argv_file ]]; then
    echo "the review argv file is missing or unreadable: $review_argv_file" >&2
    echo '  the read-only tool set is defined once there; a review cannot launch without it' >&2
    exit 2
  fi
  review_argv=()
  mapfile -t review_argv < "$review_argv_file" || exit 2
  (( ${#review_argv[@]} > 0 )) || { echo "the review argv file is empty: $review_argv_file" >&2; exit 2; }
fi
printf 'pi-impl: run mode %s (%s) for %s\n' "$run_mode" "$mode_source" "$name" >&2

# The clean/committed classification below is a fact read from git, not a
# guess: a worktree that is not a git worktree cannot answer it, so refuse.
if ! git -C "$worktree" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "worktree is not a git worktree: $worktree" >&2
  exit 2
fi

retry_limit=${PI_EMPTY_RETRY_LIMIT:-2}
[[ $retry_limit =~ ^[0-9]+$ ]] || {
  echo 'PI_EMPTY_RETRY_LIMIT must be a non-negative integer' >&2
  exit 2
}
continue_prompt_file=${PI_CONTINUE_PROMPT_FILE:-"$SCRIPT_DIR/continue-empty-completion.md"}
if [[ ! -r $continue_prompt_file ]]; then
  echo "continue prompt file is not readable: $continue_prompt_file" >&2
  exit 2
fi
# The fixed rule paragraph of this mode. It is a tracked file beside this script
# and it is APPENDED to the prompt of every launch; a launch without it is
# refused rather than started without the rule (the same fail-closed shape as
# the signal guard and the continuation prompt above).
rules_file=$SCRIPT_DIR/implementer-prompt-rules.md
[[ $run_mode != review ]] || rules_file=$SCRIPT_DIR/review-prompt-rules.md
if [[ ! -r $rules_file ]]; then
  echo "pi $run_mode prompt rule file is missing or unreadable: $rules_file" >&2
  echo '  every launch appends its fixed rule paragraph; a prompt without it is not the contract this wrapper promises' >&2
  exit 2
fi
live_interval=${PI_LIVE_INTERVAL_SECONDS:-30}
if [[ ! $live_interval =~ ^[0-9]+$ ]] || (( live_interval == 0 )); then
  echo 'PI_LIVE_INTERVAL_SECONDS must be a positive integer' >&2
  exit 2
fi

# PI_EXTRA_ARGS: extra pi argv elements, one per line (see the header). Refused
# before the run name is claimed, so a malformed value retires and overwrites
# nothing.
extra_pi_args=()
parse_extra_pi_args() {
  local line
  [[ -n ${PI_EXTRA_ARGS-} ]] || return 0
  while IFS= read -r line; do
    [[ -n $line ]] || continue
    [[ ! $line =~ ^[[:space:]]*$ ]] || continue
    case $line in
      -p | --print | --mode | --mode=* | --model | --model=* | --thinking | --thinking=* | \
        --session | --session=* | --session-id | --session-id=* | --session-dir | --session-dir=* | \
        -c | --continue | -r | --resume | --fork | --fork=* | --)
        echo "PI_EXTRA_ARGS may not set '$line': pi-impl.sh owns the launch contract (print mode, mode, model, thinking, session)" >&2
        return 1
        ;;
    esac
    if [[ $run_mode == review ]]; then
      # Review mode owns what the reviewer may DO, so the seam may not touch it.
      # Every spelling is pi 1.6.0's own (its `--help` and dist/cli/args.js, read
      # before this list was written):
      #   --tools/-t        dist/cli/args.js:114  replaces the allowlist, and a
      #                     +name entry ADDS a tool (dist/core/settings-manager.js:57-70)
      #   --exclude-tools/-xt                :127  edits that selection
      #   --no-tools/-nt, --no-builtin-tools/-nbt :108/:111 change it (the latter
      #                     keeps extension/custom tools enabled)
      #   --extension/-e    :156  loads code, and explicit -e paths still work
      #                     WITH --no-extensions (its own --help, :320)
      #   --no-extensions/-ne, --no-mcp      :160/:163  the flags the review sets
      #   --skill, --no-skills/-ns, --prompt-template, --no-prompt-templates/-np
      #                     :166,:188,:170,:191  instructions the reviewer reads
      #   --approve/-a, --no-approve/-na     :229/:232  project trust, which lets
      #                     project-local extensions load and execute
      #                     (dist/main.js:631-659, dist/core/project-trust.js:18-24)
      # Flags outside this list cannot add a tool or code here: `--no-extensions`
      # and `--no-mcp` load none, and an unparsed `--flag value` only reaches
      # extensions through extensionFlagValues (dist/cli/args.js:236-256,
      # dist/main.js:665), of which there are none.
      case $line in
        --tools | --tools=* | -t | --exclude-tools | --exclude-tools=* | -xt | \
          --no-tools | -nt | --no-builtin-tools | -nbt | \
          --extension | --extension=* | -e | --no-extensions | -ne | --no-mcp | \
          --skill | --skill=* | --no-skills | -ns | \
          --prompt-template | --prompt-template=* | --no-prompt-templates | -np | \
          --approve | -a | --no-approve | -na)
          echo "PI_EXTRA_ARGS may not set '$line' in review mode: PI_RUN_MODE=review owns pi's tool selection, extensions, MCP, skills, prompt templates and project trust" >&2
          return 1
          ;;
      esac
    fi
    if [[ $run_mode == implementer ]]; then
      # The extension is a launch invariant, not another PI_EXTRA_ARGS seam.
      # An extra extension could intercept user_bash before the guard handler
      # or replace the bash tool after it.
      case $line in
        --extension | --extension=* | -e | --no-extensions | -ne)
          echo "PI_EXTRA_ARGS may not load or disable extensions in implementer mode: the PID namespace shell is fixed" >&2
          return 1
          ;;
      esac
    fi
    extra_pi_args+=("$line")
  done <<< "$PI_EXTRA_ARGS"
  return 0
}
parse_extra_pi_args || exit 2
if [[ $run_mode == review ]] && (( ${#extra_pi_args[@]} > 0 )); then
  # Review mode has no legitimate use for the seam: the review argv already
  # names exactly what the reviewer gets, so ANY extra argument is refused here
  # rather than classified. That is what makes this a refusal instead of a
  # vocabulary to keep up with: a spelling this wrapper's list above missed, a
  # `+name` tool modifier, an extension flag added by a future pi release and an
  # otherwise harmless flag all take the same exit, and the reason is named.
  printf 'PI_EXTRA_ARGS may not add anything in review mode (got %s element(s)); the review argv is owned by PI_RUN_MODE=review\n' \
    "${#extra_pi_args[@]}" >&2
  exit 2
fi
# L2: the seam can come from a caller's export OR from a host's
# controller.local.env (lib.sh sources that with `set -a`, so an unset caller
# value is silently replaced by the file's). A widened or narrowed implementer
# run must not be invisible: every non-empty seam is announced, with the count
# of arguments it adds, so the run's own log carries the fact.
if (( ${#extra_pi_args[@]} > 0 )); then
  printf 'pi-impl: PI_EXTRA_ARGS adds %s pi argument(s); this launch is not the plain implementer argv\n' \
    "${#extra_pi_args[@]}" >&2
fi

# A same-name invocation can finish and release its claim while this wrapper is
# preflighting. Once this wrapper owns the name, re-read its mode and review
# material before constructing prompts or argv; a late review record can only
# narrow this launch, never leave it with implementer tools.
recheck_claimed_resume_mode() {
  [[ $mode == resume ]] || return 0
  local claimed_name_mode=''
  if read_mode_record_if_present "$mode_record"; then
    claimed_name_mode=$mode_record_value
  else
    local record_rc=$?
    (( record_rc == 1 )) || return 2
  fi
  if [[ $claimed_name_mode == review ]]; then
    if [[ $request_origin == caller && $requested_mode == implementer ]]; then
      printf 'run name %s was started as a REVIEW and cannot be resumed in implementer mode\n' "$name" >&2
      return 2
    fi
    run_mode=review
    mode_source=claimed-name-record
    session_review_found=1
    session_review_source='name-record'
  fi
  inspect_review_material "$CONTROLLER_RUNS_DIR/$name.review" || return 2
  current_review_material_present=$review_material_present
  if (( current_review_material_present == 1 )); then
    if [[ $request_origin == caller && $requested_mode == implementer ]]; then
      printf 'run name %s has review material and cannot be resumed in implementer mode\n' "$name" >&2
      return 2
    fi
    run_mode=review
    mode_source=review-material
    session_review_found=1
    session_review_source=review-material
  fi
  if [[ $run_mode == review ]]; then
    if (( ${#extra_pi_args[@]} > 0 )); then
      printf 'PI_EXTRA_ARGS may not add anything in review mode (got %s element(s)); the review argv is owned by PI_RUN_MODE=review\n' \
        "${#extra_pi_args[@]}" >&2
      return 2
    fi
    review_argv_file=$SCRIPT_DIR/pi-review-argv
    if [[ ! -r $review_argv_file ]]; then
      echo "the review argv file is missing or unreadable: $review_argv_file" >&2
      return 2
    fi
    review_argv=()
    mapfile -t review_argv < "$review_argv_file" || return 2
    (( ${#review_argv[@]} > 0 )) || {
      echo "the review argv file is empty: $review_argv_file" >&2
      return 2
    }
    rules_file=$SCRIPT_DIR/review-prompt-rules.md
    if [[ ! -r $rules_file ]]; then
      echo "pi review prompt rule file is missing or unreadable: $rules_file" >&2
      return 2
    fi
    printf 'pi-impl: run mode review (%s) for %s\n' "$mode_source" "$name" >&2
  fi
}

# The signal guard (see the header): every shim must exist and be executable.
# This is a fact read from the filesystem, so a missing or non-executable shim
# refuses the launch instead of starting an agent with an unguarded PATH. It is
# checked before the name is claimed, so a refusal leaves no run record behind.
pi_shims_dir=$SCRIPT_DIR/pi-shims
for shim in pkill killall pgrep; do
  if [[ ! -f $pi_shims_dir/$shim || ! -x $pi_shims_dir/$shim ]]; then
    echo "pi signal guard is incomplete: $pi_shims_dir/$shim is missing or not executable" >&2
    exit 2
  fi
done
# pi prepends its OWN tool directory to the PATH of every command its bash tool
# runs (dist/utils/shell.js:147-157 `getShellEnv`, whose binDir is
# dist/config.js:538-540 `join(getAgentDir(), "bin")`, and getAgentDir is
# `$PI_CODING_AGENT_DIR` or $HOME/.pi/agent, dist/config.js:510-515). A
# pkill/killall/pgrep sitting there would be found BEFORE the shims, so the guard
# cannot promise anything while one exists: refuse instead of starting an agent
# with a guard that a download could shadow.
#
# The value is resolved the way pi resolves it (dist/config.js:511-513 calls
# utils/paths.js normalizePath:100-124, which expands a leading `~/` against the
# home directory and returns everything else as written). Bash does NOT
# tilde-expand a variable's value, so a literal test of
# `$PI_CODING_AGENT_DIR/bin/pkill` would miss `~/agents/pi/bin/pkill`: the
# launch would proceed and pi would then put the REAL expanded directory ahead
# of the shims. A value still not absolute after that expansion is joined by pi
# to PI's OWN working directory (the worktree), which this wrapper cannot stat
# as one path, so it is refused with a named message rather than guessed at.
pi_agent_dir=${PI_CODING_AGENT_DIR:-}
if [[ -n $pi_agent_dir ]]; then
  # SC2088 is silenced for the WHOLE statement: the `~/` here is pi's documented
  # spelling, matched literally (bash must not expand it into $HOME before the
  # comparison), not a tilde the author expected to expand.
  # shellcheck disable=SC2088
  case $pi_agent_dir in
    '~') pi_agent_dir=$HOME ;;
    '~/'*) pi_agent_dir=$HOME/${pi_agent_dir#'~/'} ;;
    /*) : ;;
    *)
      echo "pi signal guard cannot be enforced: PI_CODING_AGENT_DIR must be an absolute path or start with ~/ (got '$pi_agent_dir')" >&2
      echo 'pi expands a leading ~/ and joins anything else to its own working directory, so its bin directory cannot be checked before the launch; name an absolute path' >&2
      exit 2
      ;;
  esac
else
  pi_agent_dir=$HOME/.pi/agent
fi
for shim in pkill killall pgrep; do
  if [[ -e $pi_agent_dir/bin/$shim ]]; then
    echo "pi signal guard cannot be enforced: $pi_agent_dir/bin/$shim shadows the shim" >&2
    echo 'pi prepends its own bin directory to the agent PATH; remove that file or move it out of the way' >&2
    exit 2
  fi
done

# The implementer shell boundary is a launch prerequisite. Probe it before the
# run name is claimed, so missing bubblewrap, unavailable namespaces, or a bad
# wrapper leaves no run record. Project shellPath/extensions cannot override
# this CLI-selected extension: --no-extensions ignores discovered settings
# extensions, while the explicit -e extension replaces the built-in bash tool
# and installs a custom createLocalBashOperations({shellPath}) backend.
pidns_wrapper=$SCRIPT_DIR/pi-pidns-bash
pidns_extension=$SCRIPT_DIR/pi-pidns-extension.js
if [[ $run_mode == implementer ]]; then
  if [[ ! -x $pidns_wrapper ]]; then
    echo "pi PID namespace wrapper is missing or not executable: $pidns_wrapper" >&2
    exit 2
  fi
  if [[ ! -r $pidns_extension ]]; then
    echo "pi PID namespace extension is missing or unreadable: $pidns_extension" >&2
    exit 2
  fi
  if pidns_probe_output=$("$pidns_wrapper" -c true 2>&1); then
    :
  else
    pidns_probe_rc=$?
    printf 'pi implementer launch refused before run name claim: PID namespace preflight failed (exit %s): %s\n' \
      "$pidns_probe_rc" "${pidns_probe_output:-no diagnostic from the wrapper}" >&2
    exit 2
  fi
fi

out=$CONTROLLER_RUNS_DIR
# Ownership first: nothing below may write a name-owned record until this
# invocation holds the name. A refusal here names the live owner and exits 2.
controller_run_claim "$name" || exit 2
recheck_claimed_resume_mode || exit 2
retire_existing=0

if [[ $mode == new ]]; then
  existing=()
  for extension in 'done' 'jsonl' 'err' 'events' 'live' 'last.txt' 'wt' 'route' 'thread' 'mode' 'autoresume.log'; do
    [[ -e $out/$name.$extension ]] && existing+=("$out/$name.$extension")
  done
  if (( ${#existing[@]} > 0 )); then
    if [[ ${CONTROLLER_RUN_FORCE:-0} != 1 ]]; then
      echo "controller run name already has evidence: $name" >&2
      printf '  %s\n' "${existing[@]}" >&2
      echo 'set CONTROLLER_RUN_FORCE=1 only to replace an intentionally abandoned or failed run' >&2
      # This invocation REFUSED, so it must not have overwritten the identity of
      # the owner it refused on: the owner record is written only after this
      # preflight passes (lib.sh controller_run_record_owner).
      exit 2
    fi
    # Force means RETIRE: the previous identity moves aside instead of being
    # overwritten. The retirement itself waits until the launch is real (below):
    # a forced launch that is refused later (a bad route, blocked accounts, a
    # missing credential) must leave the previous records where they are.
    retire_existing=1
  fi
fi

route=${PI_ROUTE:-deepseek-pi}
route_record=$route
od_account=''
od_start_kind=''
od_recorded_account=''
case $route in
  deepseek-pi)
    od_start_kind=auto
    if [[ $mode == resume ]]; then
      if od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        find-thread-account "$session_id" "$out"); then
        od_recorded_account=$od_account
        od_start_kind=resume
      else
        lookup_rc=$?
        if (( lookup_rc != 1 )); then exit "$lookup_rc"; fi
        printf 'no recorded Open Design account for session %s; picking a fresh account\n' "$session_id" >&2
        od_start_kind=auto-resume
      fi
    fi
    ;;
  deepseek-pi-volker|deepseek-pi-github2)
    od_account=${route#deepseek-pi-}
    od_start_kind=pinned
    ;;
  *) echo "unknown PI_ROUTE=$route" >&2; exit 2 ;;
esac

model=${PI_MODEL:-deepseek-v4.1-flash-eco}
thinking=${PI_THINKING:-max}
session_dir=${PI_SESSION_DIR:-"$HOME/.pi/agent/sessions"}

if [[ -n $od_start_kind ]]; then
  case $od_start_kind in
    auto)
      od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        start-pi auto) || exit $?
      ;;
    auto-resume)
      od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        start-pi auto "$session_id") || exit $?
      ;;
    resume)
      od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        start-pi-resume "$od_recorded_account" "$session_id") || exit $?
      ;;
    pinned)
      if [[ $mode == resume ]]; then
        CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
          start-pi "$od_account" "$session_id" >/dev/null || exit $?
      else
        CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
          start-pi "$od_account" >/dev/null || exit $?
      fi
      ;;
  esac
  case $od_account in
    volker|github2) route_record=deepseek-pi-$od_account ;;
    *) echo 'Open Design account selector returned an invalid account' >&2; exit 2 ;;
  esac
else
  echo 'pi route did not select an Open Design account' >&2
  exit 2
fi

if [[ ! -r $HOME/.env.ai ]]; then
  echo "pi route requires the local \$HOME/.env.ai file" >&2
  exit 2
fi
# shellcheck disable=SC1091
if ! . "$HOME/.env.ai" >/dev/null 2>&1; then
  echo 'could not load the local pi API-key environment' >&2
  exit 2
fi
case $od_account in
  volker)
    # Length test, never a value test: `bash -x` would trace the value into stderr.
    # shellcheck disable=SC2015 # intended: any failed test (unset or empty) takes the error branch
    [[ -v OPENDESIGN_API_KEY_VOLKER ]] && (( ${#OPENDESIGN_API_KEY_VOLKER} > 0 )) || {
      echo "OPENDESIGN_API_KEY_VOLKER is not set by \$HOME/.env.ai" >&2
      exit 2
    }
    export OPENDESIGN_API_KEY_VOLKER
    unset OPENDESIGN_API_KEY_GITHUB2
    provider=opendesign_volker
    ;;
  github2)
    # shellcheck disable=SC2015 # intended: any failed test (unset or empty) takes the error branch
    [[ -v OPENDESIGN_API_KEY_GITHUB2 ]] && (( ${#OPENDESIGN_API_KEY_GITHUB2} > 0 )) || {
      echo "OPENDESIGN_API_KEY_GITHUB2 is not set by \$HOME/.env.ai" >&2
      exit 2
    }
    export OPENDESIGN_API_KEY_GITHUB2
    unset OPENDESIGN_API_KEY_VOLKER
    provider=opendesign_github2
    ;;
esac

prompt_text=''
IFS= read -r -d '' prompt_text < "$prompt" || :
continue_prompt=''
IFS= read -r -d '' continue_prompt < "$continue_prompt_file" || :
# The mode's fixed rule paragraph, appended after the brief with one blank line
# between them. The brief itself is never rewritten (it is the caller's file);
# the prompt pi receives is the brief PLUS this paragraph, on the first launch of
# every invocation (a resume is a launch too). The EMPTY-COMPLETION continuation
# is the one other prompt this wrapper sends, so it carries the same paragraph.
rules_text=''
IFS= read -r -d '' rules_text < "$rules_file" || :
printf -v prompt_text '%s\n%s' "$prompt_text" "$rules_text"
printf -v continue_prompt '%s\n%s' "$continue_prompt" "$rules_text"
base_pi_args=(-p --mode json --model "$provider/$model" --thinking "$thinking" --session-dir "$session_dir")
# Every implementer invocation carries the protected shell extension, including
# the first launch, a resume and each automatic continuation. `--no-extensions`
# excludes project/user extension discovery but keeps the explicit `-e` path.
if [[ $run_mode == implementer ]]; then
  base_pi_args+=(--no-extensions -e "$pidns_extension")
fi
# A review's read-only set comes from its one definition (pi-review-argv) and is
# part of the wrapper's OWN argv, so every path below -- the first run, a resume
# and every automatic continuation -- carries it through invoke_pi unchanged.
if [[ $run_mode == review ]]; then
  base_pi_args+=("${review_argv[@]}")
fi
# PI_EXTRA_ARGS is appended after the wrapper's own arguments: a caller can add
# arguments, never replace the launch contract (reserved flags are refused above).
(( ${#extra_pi_args[@]} == 0 )) || base_pi_args+=("${extra_pi_args[@]}")
resume_target=''
[[ $mode != resume ]] || resume_target=$session_id

# Every refusal is behind us (a bad route, blocked accounts, a missing local
# credential file or key): the launch is real. Only now does this invocation write
# its identity into the name's owner record and touch the name's own records. A
# launch that refused earlier leaves the previous owner's identity, the previous
# completion marker and the previous worktree/route/thread records exactly as they
# were, so job-check never reports DIED for a name that never started and a
# refused new launch leaves no evidence that would refuse the retry.
if (( retire_existing == 1 )); then
  # Forced reuse of a name that has evidence: the previous identity moves aside
  # instead of being overwritten, and the caller is told what was retired and why.
  if ! persist_retired_run_session_mode "$name"; then
    echo "controller: could not preserve the pi session mode for $name; refusing to retire its records" >&2
    exit 2
  fi
  if retired=$(controller_run_retire_records "$name"); then
    printf 'pi-impl: CONTROLLER_RUN_FORCE=1 retired %s records into %s\n' "$name" "$retired" >&2
    printf '  retired: %s\n' "${existing[@]}" >&2
  else
    echo "controller: could not retire the records of $name; refusing to overwrite them" >&2
    exit 2
  fi
fi
if [[ $mode == resume ]]; then
  # The session lock was acquired before mode resolution. Persist the effective
  # mode and release it before pi starts; a second run name now observes review
  # before it can claim its own name or launch.
  write_session_mode "$session_id" || exit 2
  release_session_mode_lock
fi
controller_run_record_owner
printf '%s\n' "$worktree" > "$out/$name.wt"
printf '%s\n' "$route_record" > "$out/$name.route"
# The mode is a name-owned record like the route, written only by a launch that
# got past every refusal, so a refused launch cannot change the mode a resume
# will be forced into.
printf '%s\n' "$run_mode" > "$out/$name.mode"
if [[ $mode == resume ]]; then
  rm -f -- "$out/$name.done"
  printf '%s\n' "$session_id" > "$out/$name.thread"
fi

# One pi invocation. $3 is 1 for the first run (truncate stream and stderr) and
# 0 for a continuation that APPENDS to the run's stream and stderr directly, so
# the liveness sidecar and every stream reader see the continuation's events as
# they happen (a continuation continues the whole task and can run for hours).
invoke_pi() {
  local prompt_text=$1 stream=$2 first_run=$3
  local -a argv=("${base_pi_args[@]}")
  [[ -z $resume_target ]] || argv+=(--session "$resume_target")
  # The shim directory goes FIRST on the agent's PATH and only there: this
  # wrapper's own commands keep the caller's PATH. pi's bash tool hands its
  # inherited environment to the shell it spawns, so the agent sees the shims.
  # `${PATH:+:$PATH}` and not `${PATH-}`: an UNSET or EMPTY PATH would otherwise
  # hand the child a trailing empty field, and an empty PATH field means the
  # CURRENT DIRECTORY (the worktree) both to the shim's own lookup and to every
  # command the agent runs.
  if [[ $first_run == 1 ]]; then
    (cd -- "$worktree" && controller_close_ownership_fds \
      && PATH=$pi_shims_dir${PATH:+:$PATH} exec nice pi "${argv[@]}" -- "$prompt_text" \
      < /dev/null > "$stream" 2> "$out/$name.err")
  else
    (cd -- "$worktree" && controller_close_ownership_fds \
      && PATH=$pi_shims_dir${PATH:+:$PATH} exec nice pi "${argv[@]}" -- "$prompt_text" \
      < /dev/null >> "$stream" 2>> "$out/$name.err")
  fi
}

# Classify the last assistant message of the (possibly appended) run stream.
# Fields, tab separated: session id, stop reason, usage-limited flag, settled
# flag, aborted flag, has-text flag, has-tool-call flag. The turn count, tool
# call count and token total are the live sidecar's job (pi-live.py).
inspect_run() {
  python3 - "$out/$name.jsonl" "$out/$name.last.txt" <<'PY'
import json
import sys
from pathlib import Path

stream = Path(sys.argv[1])
last_text = ""
session_id = ""
stop_reason = ""
usage_limited = False
settled = False
aborted = False
has_tool_call = False

def coding_plan(value):
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if current.get("code") == "insufficient_balance":
                return True
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False

def message_text(message):
    value = message.get("content")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = []
        for part in value:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "".join(chunks)
    return message.get("text", "") if isinstance(message.get("text"), str) else ""

def has_tool_calls(message):
    value = message.get("content")
    if isinstance(value, list):
        for part in value:
            if isinstance(part, dict) and part.get("type") == "toolCall":
                return True
    return False

with stream.open(encoding="utf-8", errors="replace") as source:
    for line in source:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "session" and not session_id:
            value = event.get("id") or event.get("sessionId")
            if isinstance(value, str):
                session_id = value
        if event.get("type") == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                stop_reason = str(message.get("stopReason") or "")
                last_text = message_text(message)
                has_tool_call = has_tool_calls(message)
                error_message = message.get("errorMessage")
                if stop_reason == "error" and isinstance(error_message, str):
                    prefix, separator, encoded = error_message.partition(":")
                    if separator and prefix.strip() == "402":
                        try:
                            usage_limited = coding_plan(json.loads(encoded.strip()))
                        except ValueError:
                            usage_limited = False
        if event.get("type") == "agent_settled":
            settled = True
            aborted = event.get("aborted") is True

Path(sys.argv[2]).write_text(last_text + "\n", encoding="utf-8")
print("\t".join((
    session_id, stop_reason, "1" if usage_limited else "0",
    "1" if settled else "0", "1" if aborted else "0",
    "1" if last_text.strip() else "0", "1" if has_tool_call else "0",
)))
PY
}

# Report the last outcome class. $1 is the pi exit status; the remaining
# classification facts come from inspect_run.
classify_run() {
  local rc=$1
  if [[ $stop_reason == error && $usage_limited == 1 ]]; then
    echo USAGE-LIMIT
  elif [[ $stop_reason == error ]]; then
    echo ERROR
  elif [[ $rc != 0 ]]; then
    echo ERROR
  elif [[ $settled != 1 || $aborted != 0 || -z $thread_id ]]; then
    echo INCOMPLETE
  elif [[ $stop_reason == length ]]; then
    # The context window filled: a real end state of a long run, with a DIFFERENT
    # remedy from an unattributable stream. Decided before the generic
    # not-a-stop branch, and never continued.
    echo CONTEXT-FULL
  elif [[ $stop_reason != stop ]]; then
    echo INCOMPLETE
  elif [[ $has_text != 1 && $has_tool_call == 1 ]]; then
    # A settled message that ends on a tool call and says nothing: the run
    # stopped mid-work. Not a success (the contract requires text), and not an
    # EMPTY-COMPLETION (that class is the one the wrapper continues).
    echo TOOL-CALL-NO-TEXT
  elif [[ $has_text != 1 ]]; then
    # No text and no tool call: pi treats that as the end of the run, so a
    # provider/model anomaly shows up as an exit-0 stop with nothing said.
    echo EMPTY-COMPLETION
  elif ! worktree_state=$(git -C "$worktree" --no-optional-locks status --porcelain 2>/dev/null); then
    # A worktree that cannot answer must not read as clean (fail closed).
    echo INCOMPLETE
  elif [[ -n $worktree_state ]]; then
    echo UNCOMMITTED
  elif [[ -n $report_path && ! -f $report_path ]]; then
    echo NO-REPORT
  else
    echo SUCCESS
  fi
}

live_pid=''
live_stop() {
  [[ -n $live_pid ]] || return 0
  kill -- "$live_pid" 2>/dev/null || true
  wait "$live_pid" 2>/dev/null || true
  live_pid=''
}
# Every exit path drops the name: the EXIT trap covers success, an error path
# and SIGTERM (bash runs it for TERM), and a SIGKILL leaves it to the kernel,
# which releases the flock when the last holder dies.
trap 'live_stop; controller_run_release' EXIT
if [[ ${PI_LIVE:-1} == 1 ]]; then
  # The sidecar must not inherit the name lock: it can outlive a killed wrapper
  # by one interval, and it holds no ownership of the run name.
  ( controller_close_ownership_fds
    exec python3 "$SCRIPT_DIR/pi-live.py" --stream "$out/$name.jsonl" --worktree "$worktree" \
      --out "$out/$name.live" --run "$name" --wrapper-pid $$ --interval "$live_interval" \
      >/dev/null 2>>"$out/$name.err" ) &
  live_pid=$!
fi

# A resumed run keeps the retry history of its earlier invocations.
if [[ $mode == new ]]; then : > "$out/$name.events"; else : >> "$out/$name.events"; fi
if invoke_pi "$prompt_text" "$out/$name.jsonl" 1; then
  rc=0
else
  rc=$?
fi

inspection=$(inspect_run)
IFS=$'\t' read -r stream_session stop_reason usage_limited settled aborted \
  has_text has_tool_call <<< "$inspection"
stream_session_mismatch=0
if [[ $mode == resume && -n $stream_session && $stream_session != "$session_id" ]]; then
  printf 'pi-impl: resumed session %s produced stream session %s; refusing the mismatched run\n' \
    "$session_id" "$stream_session" >&2
  stream_session_mismatch=1
  rc=2
  thread_id=$session_id
else
  thread_id=${stream_session:-$session_id}
fi
if [[ -n $thread_id && $stream_session_mismatch == 0 ]]; then
  printf '%s\n' "$thread_id" > "$out/$name.thread"
  # A completed invocation learns its session id from pi's stream and then
  # records the mode. While the invocation is still running, resume resolution
  # can recover the id and mode from the bounded stream head plus this name's
  # mode record.
  if ! write_session_mode "$thread_id"; then
    printf 'pi-impl: session mode for %s could not be recorded; the run-name mode remains available\n' \
      "$thread_id" >&2
  fi
  if CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
    record-thread "$thread_id" "$od_account"; then
    :
  else
    thread_state_rc=$?
    printf 'Open Design session-account bookkeeping failed for run %s (exit %s); preserving pi exit %s\n' \
      "$name" "$thread_state_rc" "$rc" >&2
  fi
fi
outcome=$(classify_run "$rc")
if (( stream_session_mismatch == 1 )); then outcome=ERROR; fi

# Continue the SAME session only for the EMPTY-COMPLETION class, which is a
# provider/model anomaly. Every other class is a real state the controller must
# see; nothing is retried on a count of turns, tokens or tool calls.
attempt=0
resume_target=$thread_id
while (( attempt < retry_limit )) && [[ $outcome == EMPTY-COMPLETION ]]; do
  if [[ -z $thread_id ]]; then
    printf '{"ev":"continue-skipped","reason":"no pi session id in the stream"}\n' \
      >> "$out/$name.events"
    break
  fi
  attempt=$((attempt + 1))
  printf '{"ev":"empty-completion-retry-start","attempt":%s}\n' "$attempt" >> "$out/$name.events"
  if invoke_pi "$continue_prompt" "$out/$name.jsonl" 0; then
    attempt_rc=0
  else
    attempt_rc=$?
  fi
  rc=$attempt_rc
  inspection=$(inspect_run)
  IFS=$'\t' read -r stream_session stop_reason usage_limited settled aborted \
    has_text has_tool_call <<< "$inspection"
  thread_id=${stream_session:-$thread_id}
  if [[ -n $thread_id ]]; then
    if ! write_session_mode "$thread_id"; then
      printf 'pi-impl: session mode for %s could not be recorded after continuation; the run-name mode remains available\n' \
        "$thread_id" >&2
    fi
  fi
  outcome=$(classify_run "$rc")
  printf '{"ev":"empty-completion-retry","attempt":%s,"exit":%s,"outcome":"%s"}\n' \
    "$attempt" "$rc" "$outcome" >> "$out/$name.events"
done
if (( attempt > 0 )) && [[ $outcome == EMPTY-COMPLETION ]]; then
  outcome=EMPTY-COMPLETION-RETRIES-EXHAUSTED
fi

if (( stream_session_mismatch == 0 )); then
  if CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" block-run "$od_account" \
    "$out/$name.err" "$out/$name.jsonl"; then
    :
  else
    bookkeeping_rc=$?
    printf 'block-run bookkeeping failed for run %s (exit %s); preserving pi exit %s\n' \
      "$name" "$bookkeeping_rc" "$rc" >&2
  fi
fi

printf '{"ev":"outcome","exit":%s,"outcome":"%s","retries":%s}\n' \
  "$rc" "$outcome" "$attempt" >> "$out/$name.events"
printf 'EXIT=%s OUTCOME=%s\n' "$rc" "$outcome" > "$out/$name.done"
if (( attempt > 0 )); then printf 'RETRIES=%s\n' "$attempt" >> "$out/$name.done"; fi
live_stop
exit "$rc"
