# shellcheck shell=bash disable=SC2034,SC2154 # sourced: the callers use the settings and own pane/log
# resume-lib.sh: the pane-idle/footer logic shared by clear-resume.sh and compact-resume.sh.
# Source it, never run it. The caller sets RESUME_PREFIX (the env prefix of its test seams,
# e.g. CLEAR_RESUME) before sourcing and pane/log after; one copy of the footer markers lives here.
#
# Footer markers, tied to the status line measured on Claude Code 2.1.331; adjust if it changes.
BUSY_MARKERS=('esc to interrupt')   # present while the session works (a caller may add more)
CLEARED_MARKER='ctx ?'              # present after /clear, before the first turn
COMPACTING_MARKER='Compacting'      # the spinner line of a running compaction (compact-resume.sh adds it to BUSY_MARKERS)
INPUT_MARKER='❯'                    # starts the input row of the prompt box (the LAST such line is the live one)
FOOT_LINES=4                        # how many non-blank bottom lines count as the footer

# Test seams: <RESUME_PREFIX>_POLL (seconds between polls), _IDLE_TRIES, _TYPE_PAUSE, _FINAL_WAIT.
seam() { local v=${RESUME_PREFIX}_$1; printf '%s' "${!v:-$2}"; }
poll=$(seam POLL 2) idle_tries=$(seam IDLE_TRIES 900)
type_pause=$(seam TYPE_PAUSE 1) final_wait=$(seam FINAL_WAIT 10)

say() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*" >>"$log"; }
foot() { tmux capture-pane -p -t "$pane" | grep -v '^[[:space:]]*$' | tail -n "$FOOT_LINES"; }
busy() { pane_state; [[ $? -eq 1 ]]; } # one capture per call
pane_state() { # ONE capture; 0 ready, 1 busy, 2 the input row has text
  # Input row = the LAST line starting with INPUT_MARKER (older prompt rows in the scrollback start
  # with it too); empty = the marker followed only by spaces / NBSP (or no such row at all).
  local t f m row; t=$(tmux capture-pane -p -t "$pane")
  f=$(grep -v '^[[:space:]]*$' <<<"$t" | tail -n "$FOOT_LINES")
  for m in "${BUSY_MARKERS[@]}"; do grep -qF -- "$m" <<<"$f" && return 1; done
  row=$(grep "^$INPUT_MARKER" <<<"$t" | tail -1)
  row=${row#"$INPUT_MARKER"}; row=${row//$'\xc2\xa0'/}; row=${row//[[:space:]]/}
  [[ -z $row ]] || return 2
}
wait_idle() { # $1 = max polls, $2 = "busy-only" ignores the input row; 0 after 3 ready checks in a row, else the last state
  local n=0 st=1 _
  for _ in $(seq "$1"); do
    pane_state; st=$?
    if [[ $st -eq 0 || ( $st -eq 2 && ${2:-} == busy-only ) ]]; then n=$((n+1)); [[ $n -ge 3 ]] && return 0; else n=0; fi
    sleep "$poll"
  done
  return $(( st == 0 ? 1 : st ))
}
idle_or_exit() { # $1 = max polls, $2 = exit code when the pane stayed busy, $3 = its message; an input row that never empties exits 5
  wait_idle "$1"
  case $? in
    0) return 0 ;;
    2) say "ERROR input line still has text after $1 polls; nothing more sent"; exit 5 ;;
    *) say "ERROR $3"; exit "$2" ;;
  esac
}
check_log() { { : >>"$log"; } 2>/dev/null || { echo "ERROR log file not writable: $log" >&2; exit 2; }; }
check_pane() { tmux has-session -t "$pane" 2>/dev/null || { say "ERROR no pane $pane"; exit 2; }; }
send_text() { # literal text (`--` stops flag parsing: a text starting with `-`), a pause, then Enter; failure exits 6
  tmux send-keys -t "$pane" -l -- "$1" || { say "ERROR send-keys failed (text)"; exit 6; }
  sleep "$type_pause"
  tmux send-keys -t "$pane" Enter || { say "ERROR send-keys failed (Enter)"; exit 6; }
}
