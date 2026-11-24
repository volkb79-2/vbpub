#!/usr/bin/env bash
# shellcheck disable=SC1091,SC2034,SC2154 # settings and helpers come from the sourced resume-lib.sh
set -u
# clear-resume.sh <tmux-pane-id> <successor-prompt-file> <log-file>
# CLEAN-SLATE restart of an interactive Claude Code session that runs in a tmux pane: wait until
# the pane is idle, send /clear, wait for the cleared status line, type the successor prompt and
# Enter. /clear ends the session's background work; the default restart that keeps it is
# compact-resume.sh. The session launches this DETACHED (`setsid nohup ... &`, never Bash
# run_in_background), then ends its turn; a controller may also run it on a sub-controller's pane.
# Procedure: AGENTS.md "Successor restart of an interactive session". Proven 5x on 2026-11-24.
#
# Exit codes: 0 prompt sent (a missing cleared marker only logs WARN); 2 bad arguments, unreadable
# prompt file, unwritable log or no such pane (nothing sent); 3 pane never idle within 30 min
# (nothing sent); 5 the input line never emptied (someone has text there; checked before /clear and
# before the prompt, nothing is typed over it; also the pane still busy 30 min after /clear, prompt
# not sent); 6 a send-keys call failed (nothing more is sent).
# Progress goes to <log-file>. Footer markers and the shared idle logic: resume-lib.sh. Test seams (tests/test-clear-resume.sh): CLEAR_RESUME_{POLL,IDLE_TRIES,TYPE_PAUSE,
# FINAL_WAIT,CLEAR_TRIES}.
RESUME_PREFIX=CLEAR_RESUME
# shellcheck source=resume-lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/resume-lib.sh"
clear_tries=$(seam CLEAR_TRIES 30)
[[ $# -eq 3 ]] || { echo "usage: ${0##*/} <tmux-pane-id> <successor-prompt-file> <log-file>" >&2; exit 2; }
pane=$1; prompt_file=$2; log=$3
check_log
[[ -r $prompt_file ]] || { say "ERROR prompt file unreadable: $prompt_file"; exit 2; }
check_pane
say "start pane=$pane prompt=$prompt_file"
idle_or_exit "$idle_tries" 3 "pane never idle within 30 min"
say "pane idle, input empty; sending /clear"
tmux send-keys -t "$pane" '/clear' Enter || { say "ERROR send-keys failed (/clear)"; exit 6; }
ok=0
for _ in $(seq "$clear_tries"); do sleep "$poll"; if foot | grep -qF -- "$CLEARED_MARKER"; then ok=1; break; fi; done
if [[ $ok -eq 1 ]]; then say "clear confirmed ($CLEARED_MARKER)"; else say "WARN no '$CLEARED_MARKER' after 60 s; footer: $(foot | tr '\n' '|')"; fi
sleep "$poll"
idle_or_exit "$idle_tries" 5 "pane busy for 30 min after /clear; prompt NOT sent"
send_text "$(cat -- "$prompt_file")"
sleep "$final_wait"
say "prompt sent; footer: $(foot | tr '\n' '|')"
