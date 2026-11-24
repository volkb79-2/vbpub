#!/usr/bin/env bash
set -u
# self-resume.sh <tmux-pane-id> <successor-prompt-file> <log-file>
# Restart an interactive Claude Code session that runs in a tmux pane: wait until the pane is
# idle, send /clear, wait for the cleared status line, type the successor prompt and Enter.
# The session launches this DETACHED (`setsid nohup ... &`, never Bash run_in_background), then
# ends its turn; a controller may also run it on a sub-controller's pane. Procedure: AGENTS.md
# "Long-running controllers" (successor restart). Proven 5x on 2026-11-24.
#
# Exit codes: 0 prompt sent (a missing cleared marker only logs WARN); 2 bad arguments, unreadable
# prompt file, unwritable log or no such pane (nothing sent); 3 pane never idle within 30 min
# (nothing sent). Progress goes to <log-file>.
#
# Footer markers, tied to the status line measured on Claude Code 2.1.331; adjust if it changes.
BUSY_MARKER='esc to interrupt'   # present while the session works
CLEARED_MARKER='ctx ?'           # present after /clear, before the first turn
# Test seams (tests/test-self-resume.sh): poll interval in seconds and the poll limits.
poll=${SELF_RESUME_POLL:-2} idle_tries=${SELF_RESUME_IDLE_TRIES:-900} clear_tries=${SELF_RESUME_CLEAR_TRIES:-30}
type_pause=${SELF_RESUME_TYPE_PAUSE:-1} final_wait=${SELF_RESUME_FINAL_WAIT:-10}
[[ $# -eq 3 ]] || { echo "usage: ${0##*/} <tmux-pane-id> <successor-prompt-file> <log-file>" >&2; exit 2; }
pane=$1; prompt_file=$2; log=$3
say() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*" >>"$log"; }
foot() { tmux capture-pane -p -t "$pane" | grep -v '^[[:space:]]*$' | tail -4; }
{ : >>"$log"; } 2>/dev/null || { echo "ERROR log file not writable: $log" >&2; exit 2; }
[[ -r $prompt_file ]] || { say "ERROR prompt file unreadable: $prompt_file"; exit 2; }
tmux has-session -t "$pane" 2>/dev/null || { say "ERROR no pane $pane"; exit 2; }
say "start pane=$pane prompt=$prompt_file"
n=0
for _ in $(seq "$idle_tries"); do
  if foot | grep -qF -- "$BUSY_MARKER"; then n=0; else n=$((n+1)); [[ $n -ge 3 ]] && break; fi
  sleep "$poll"
done
[[ $n -ge 3 ]] || { say "ERROR pane never idle within 30 min"; exit 3; }
say "pane idle; sending /clear"
tmux send-keys -t "$pane" '/clear' Enter
ok=0
for _ in $(seq "$clear_tries"); do sleep "$poll"; if foot | grep -qF -- "$CLEARED_MARKER"; then ok=1; break; fi; done
if [[ $ok -eq 1 ]]; then say "clear confirmed ($CLEARED_MARKER)"; else say "WARN no '$CLEARED_MARKER' after 60 s; footer: $(foot | tr '\n' '|')"; fi
sleep "$poll"
tmux send-keys -t "$pane" -l "$(cat -- "$prompt_file")"
sleep "$type_pause"
tmux send-keys -t "$pane" Enter
sleep "$final_wait"
say "prompt sent; footer: $(foot | tr '\n' '|')"
