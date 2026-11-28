#!/usr/bin/env bash
# shellcheck disable=SC1091,SC2034,SC2154 # settings and helpers come from the sourced resume-lib.sh
set -u
# compact-resume.sh <tmux-pane-id> <compact-guidance-file> <resume-prompt-file> <log-file>
# DEFAULT restart of an interactive Claude Code session in a tmux pane: wait until the pane is
# idle, send `/compact ` and its guidance as separate literal chunks, confirm the
# SessionStart(compact) receipt, wait until it finishes (then idle again; max 60 min),
# and type the resume prompt and Enter.
# The session is kept as a summary and its background work (subagents, Monitors, background
# shells, codex runs) survives, unlike /clear (clear-resume.sh). Measured 2026-11-24: 442k -> ~60k
# tokens in ~46 s. Launch it DETACHED (`setsid nohup ... &`, never Bash run_in_background), then
# end the turn. Procedure: AGENTS.md "Successor restart of an interactive session".
#
# Exit codes: 0 resume prompt sent after a confirmed compaction; 2 bad
# arguments, unreadable guidance/prompt file, unwritable log or no such pane (nothing sent);
# 3 pane never idle within 30 min (nothing sent); 4 compaction not finished within 60 min (the
# /compact was sent, the resume prompt was not; an UNRECOGNISED layout - no prompt box, a draft, a
# dialog or a failed capture - counts as busy here too, so the exit does not prove a compaction was
# running); 5 the input line never emptied (someone has text there, or the pane stayed busy before
# the resume prompt; checked before /compact and before the prompt, nothing is typed over it); 6 a
# send-keys call failed (text may be partly typed, nothing more is sent); 7 the per-pane lock (also
# taken by ctl-keepalive.sh) was not obtained within 2 min (nothing sent). A pane that is not ready
# for a reason the helper cannot read logs that reason once per wait, with the footer; 8 no changed
# SessionStart(compact) receipt was observed after /compact, so the resume prompt was not sent (the
# session may have compacted if its hook is missing). Progress goes to <log-file>. Footer markers
# and the shared idle logic: resume-lib.sh. Test seams (tests/test-compact-resume.sh):
# COMPACT_RESUME_{POLL,IDLE_TRIES,TYPE_PAUSE,FINAL_WAIT,START_TRIES,DONE_TRIES,SETTLE,LOCK_DIR,LOCK_WAIT,RECEIPT_DIR}.
# Receipt confirmation defaults to a 120-second window (60 polls at 2 seconds).
RESUME_PREFIX=COMPACT_RESUME
# shellcheck source=resume-lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/resume-lib.sh"
BUSY_MARKERS+=("$COMPACTING_MARKER")
FOOT_LINES=6                   # the compaction status line sits higher than the working one
start_tries=$(seam START_TRIES 60) done_tries=$(seam DONE_TRIES 1800) settle=$(seam SETTLE 3)
[[ $# -eq 4 ]] || { echo "usage: ${0##*/} <tmux-pane-id> <compact-guidance-file> <resume-prompt-file> <log-file>" >&2; exit 2; }
pane=$1; guidance_file=$2; prompt_file=$3; log=$4
check_log
[[ -r $guidance_file ]] || { say "ERROR guidance file unreadable: $guidance_file"; exit 2; }
[[ -r $prompt_file ]] || { say "ERROR prompt file unreadable: $prompt_file"; exit 2; }
check_pane
resolve_pane   # the target may be a session name: lock and send on the canonical %pane id
lock_pane      # the keep-alive watcher pings under the same per-pane lock; a held lock is waited for (max 2 min)
say "start pane=$pane guidance=$guidance_file prompt=$prompt_file"
idle_or_exit "$idle_tries" 3 "pane never idle within 30 min"
guidance=$(cat -- "$guidance_file"); guidance=${guidance//$'\n'/ }
say "pane idle, input empty; footer before: $(foot | grep -o 'ctx [0-9?]*k\?' | tail -1)"
receipt_dir=$(seam RECEIPT_DIR "${CONTROLLER_HOME:-$HOME/controller}/compact-receipts")
receipt_path=$receipt_dir/$pane.json
receipt_before=$(cat -- "$receipt_path" 2>/dev/null || :)
send_chunks '/compact '
[[ -z $guidance ]] || send_chunks "$guidance"
sleep "$type_pause"
tmux send-keys -t "$pane" Enter || { say "ERROR send-keys failed (Enter)"; exit 6; }
started=0; quiet=0; not_ready_said=0   # one not-ready line for this wait (see resume-lib.sh say_once)
for phase_name in window completion; do
  phase_tries=$start_tries
  [[ $phase_name == completion ]] && phase_tries=$done_tries
  for _ in $(seq "$phase_tries"); do
    receipt_now=$(cat -- "$receipt_path" 2>/dev/null || :)
    if [[ -n $receipt_now && $receipt_now != "$receipt_before" ]]; then
      started=1
      break 2
    fi
    view=$(pane_view "$(tmux capture-pane -e -p -t "$pane")")
    busy_beside_box "$view"; pane_status=$?
    if [[ $pane_status -eq 0 || $pane_status -eq 3 || $pane_status -eq 2 ]]; then
      quiet=0
    else
      quiet=$((quiet + 1))
    fi
    if [[ $quiet -ge 3 && $phase_name == completion ]]; then
      say "ERROR no changed compaction receipt at $receipt_path; the SessionStart(compact) hook may be missing; resume prompt NOT sent"
      exit 8
    fi
    sleep "$poll"
  done
  if [[ $phase_name == window && $quiet -ge 3 ]]; then
    say "ERROR no changed compaction receipt at $receipt_path; the SessionStart(compact) hook may be missing; resume prompt NOT sent"
    exit 8
  fi
done
if [[ $started -ne 1 ]]; then
  say "ERROR compaction not finished within 60 min (no receipt); resume prompt NOT sent"
  exit 4
fi
say "compaction receipt observed: $receipt_path"
wait_idle "$done_tries" busy-only || { say "ERROR compaction not finished within 60 min"; exit 4; }
say "compaction done; footer: $(foot | tr '\n' '|')"
sleep "$settle"
idle_or_exit "$idle_tries" 5 "pane busy for 30 min after the compaction; resume prompt NOT sent"
send_text "$(cat -- "$prompt_file")"
sleep "$final_wait"
say "resume prompt sent; footer: $(foot | tr '\n' '|')"
