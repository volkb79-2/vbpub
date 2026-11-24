# shellcheck shell=bash disable=SC2034,SC2154 # sourced: the callers use the settings and own pane/log
# resume-lib.sh: the pane-idle/footer logic shared by clear-resume.sh and compact-resume.sh.
# Source it, never run it. The caller sets RESUME_PREFIX (the env prefix of its test seams,
# e.g. CLEAR_RESUME) before sourcing and pane/log after; one copy of the footer markers lives here.
#
# Footer markers, tied to the status line measured on Claude Code 2.1.331; adjust if it changes.
BUSY_MARKERS=('esc to interrupt')   # present while the session works (a caller may add more)
CLEARED_MARKER='ctx ?'              # present after /clear, before the first turn
FOOT_LINES=4                        # how many non-blank bottom lines count as the footer

# Test seams: <RESUME_PREFIX>_POLL (seconds between polls), _IDLE_TRIES, _TYPE_PAUSE, _FINAL_WAIT.
seam() { local v=${RESUME_PREFIX}_$1; printf '%s' "${!v:-$2}"; }
poll=$(seam POLL 2) idle_tries=$(seam IDLE_TRIES 900)
type_pause=$(seam TYPE_PAUSE 1) final_wait=$(seam FINAL_WAIT 10)

say() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*" >>"$log"; }
foot() { tmux capture-pane -p -t "$pane" | grep -v '^[[:space:]]*$' | tail -n "$FOOT_LINES"; }
busy() { # one capture per call
  local t m; t=$(foot)
  for m in "${BUSY_MARKERS[@]}"; do grep -qF -- "$m" <<<"$t" && return 0; done
  return 1
}
wait_idle() { # $1 = max polls; returns 0 after 3 idle checks in a row
  local n=0 _
  for _ in $(seq "$1"); do
    if busy; then n=0; else n=$((n+1)); [[ $n -ge 3 ]] && return 0; fi
    sleep "$poll"
  done
  return 1
}
check_log() { { : >>"$log"; } 2>/dev/null || { echo "ERROR log file not writable: $log" >&2; exit 2; }; }
check_pane() { tmux has-session -t "$pane" 2>/dev/null || { say "ERROR no pane $pane"; exit 2; }; }
send_text() { # literal text, a pause, then Enter
  tmux send-keys -t "$pane" -l "$1"
  sleep "$type_pause"
  tmux send-keys -t "$pane" Enter
}
