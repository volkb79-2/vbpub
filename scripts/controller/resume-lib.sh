# shellcheck shell=bash disable=SC2034,SC2154 # sourced: the callers use the settings and own pane/log
# resume-lib.sh: the pane-idle/footer logic shared by clear-resume.sh, compact-resume.sh and ctl-keepalive.sh.
# Source it, never run it. The caller sets RESUME_PREFIX (the env prefix of its test seams,
# e.g. CLEAR_RESUME) before sourcing and pane/log after; one copy of the footer and pane-view markers lives here.
#
# Footer markers, tied to the status line measured on Claude Code 2.1.331; adjust if it changes.
BUSY_MARKERS=('esc to interrupt')   # present while the session works (a caller may add more)
CLEARED_MARKER='ctx ?'              # present after /clear, before the first turn
COMPACTING_MARKER='Compacting'      # the spinner line of a running compaction (compact-resume.sh adds it to BUSY_MARKERS)
INPUT_MARKER='❯'                    # starts the input row of the prompt box (the LAST such line is the live one)
FOOT_LINES=4                        # how many non-blank bottom lines count as the footer
BUSY_SCAN_LINES=20                  # ... and the window used when the capture holds no prompt box (see busy_beside_box)
BOX_RULE_BYTES='(?:\xe2\x94\x80){20,}'                                         # a prompt-box rule: at least 20 x U+2500, nothing else (perl, bytes)
BOX_LABEL_RULE_BYTES='(?:\xe2\x94\x80){20,}[A-Za-z0-9._-]+(?:\xe2\x94\x80)+'   # the same rule with one tmux session label (spaces stripped)
BOX_FOOT_MARKER='⏵⏵ bypass permissions on'                                     # the mode line: rows below it are not part of the prompt box
BOX_PANEL_HINT='↓ to manage'                                                   # the mode line's unfocused agents-panel hint: only this panel layout is trimmed

# Test seams: <RESUME_PREFIX>_POLL (seconds between polls), _IDLE_TRIES, _TYPE_PAUSE, _FINAL_WAIT.
seam() { local v=${RESUME_PREFIX}_$1; printf '%s' "${!v:-$2}"; }
poll=$(seam POLL 2) idle_tries=$(seam IDLE_TRIES 900)
type_pause=$(seam TYPE_PAUSE 1) final_wait=$(seam FINAL_WAIT 10)

say() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*" >>"$log"; }

# ---- one pane view: the prompt box and every bottom window are measured from the MODE line, never from the last line
# ---- of the capture, so the agents panel Claude Code draws BELOW the mode line cannot push a busy marker or a menu
# ---- out of a window. The rows below the mode line are trimmed only while it carries the unfocused hint
# ---- BOX_PANEL_HINT: a focused panel is not captured yet and may look identical, so without the hint nothing is
# ---- trimmed and the capture is returned unchanged (fail-safe). Shared with ctl-keepalive.sh.
strip_sgr() { perl -pe 's/\e\[[0-9;]*m//g'; }
is_rule() { local r=${1//$'\xc2\xa0'/}; r=${r//[[:space:]]/}; perl -e 'exit($ARGV[0] =~ /^'"$BOX_RULE_BYTES"'$/ ? 0 : 1)' "$r"; }
is_label_rule() { local r=${1//$'\xc2\xa0'/}; r=${r//[[:space:]]/}; perl -e 'exit($ARGV[0] =~ /^'"$BOX_LABEL_RULE_BYTES"'$/ ? 0 : 1)' "$r"; }
pane_view() { # $1 = capture with escapes: the captured lines down to and including the mode line (unchanged if no box)
  local ln=() plain=() i idx=-1 n esc=$'\033' re
  re="^(${esc}\[[0-9;]*m)*$INPUT_MARKER"
  mapfile -t ln <<<"$1"; n=${#ln[@]}
  for ((i = 0; i < n; i++)); do [[ ${ln[i]} =~ $re ]] && idx=$i; done
  (( idx >= 0 )) || { printf '%s\n' "${ln[@]}"; return 0; }
  mapfile -t plain < <(strip_sgr <<<"$1")   # one perl for the whole capture, not one per line below the input row
  for ((i = idx + 1; i < n; i++)); do
    [[ ${plain[i]} == *"$BOX_FOOT_MARKER"* ]] || continue
    [[ ${plain[i]} == *"$BOX_PANEL_HINT"* ]] && n=$((i + 1))   # unfocused panel: nothing below the mode line is part of the box
    break
  done
  printf '%s\n' "${ln[@]:0:n}"
}
busy_beside_box() { # $1 = a capture: 0 a BUSY_MARKERS line is inside the box (at or below its last top rule), 1 the box
  # holds none, 2 no prompt box was recognised: then the marker counts anywhere in the last BUSY_SCAN_LINES non-blank
  # lines. The box is the anchor: pane_view trims to the mode line only in the ONE layout it recognises (the bypass mode
  # line carrying the unfocused panel hint), so a pane in another permission mode keeps its panel rows and the mode
  # line's `esc to interrupt` falls outside pane_state_of's FOOT_LINES window. 2 is NOT a ready answer: a caller that
  # would type into this pane must treat it as busy. Busy wrongly only waits or times out; idle wrongly is typed into.
  local ln=() i n idx=-1 start=0 boxed=0 win m re="^$INPUT_MARKER"
  mapfile -t ln < <(strip_sgr <<<"$1" | grep -av '^[[:space:]]*$')
  n=${#ln[@]}
  (( n > 0 )) || return 2
  for ((i = 0; i < n; i++)); do [[ ${ln[i]} =~ $re ]] && idx=$i; done
  if (( idx >= 1 )) && { is_rule "${ln[idx-1]}" || is_label_rule "${ln[idx-1]}"; }; then
    boxed=1; start=$((idx - 1))   # the last top rule: everything above it is history, everything below belongs to the box
  elif (( n > BUSY_SCAN_LINES )); then
    start=$((n - BUSY_SCAN_LINES))
  fi
  win=$(printf '%s\n' "${ln[@]:start}")
  for m in "${BUSY_MARKERS[@]}"; do grep -qF -- "$m" <<<"$win" && return 0; done
  (( boxed )) || return 2
  return 1
}
foot() { pane_view "$(tmux capture-pane -e -p -t "$pane")" | strip_sgr | grep -v '^[[:space:]]*$' | tail -n "$FOOT_LINES"; }
busy() { pane_state; [[ $? -eq 1 ]]; } # one capture per call
pane_state() { # ONE capture; 0 ready, 1 busy, 2 the input row has text. An unrecognised layout is busy, never ready: the
  # caller is about to type into this pane.
  local view st
  view=$(pane_view "$(tmux capture-pane -e -p -t "$pane")")
  busy_beside_box "$view"; st=$?
  (( st == 0 || st == 2 )) && return 1
  pane_state_of "$view"
}
input_row_of() { # $1 = a capture taken with -e; prints the live input row (marker kept), dim runs and SGR escapes removed
  # Input row = the LAST line starting with INPUT_MARKER (older prompt rows in the scrollback start with it too).
  # Claude Code shows a prompt SUGGESTION as dim text (ESC[2m ... ESC[0m or ESC[22m) in an EMPTY input row;
  # a plain capture cannot tell it from typed text (every idle pane looked non-empty on 2026-11-24). So the
  # capture keeps the escapes (-e) and the dim runs are dropped from the row, then the other SGR escapes.
  local esc=$'\033'
  grep -aE "^(${esc}\[[0-9;]*m)*$INPUT_MARKER" <<<"$1" | tail -1 | perl -pe 's/\e\[2m.*?(\e\[(0|22)m|$)//g; s/\e\[[0-9;]*m//g'
}
pane_state_of() { # $1 = a pane_view capture; 0 ready, 1 busy, 2 the input row has text (also used by ctl-keepalive.sh)
  # empty = the marker followed only by spaces / NBSP (or no such row at all); the footer text is matched
  # against the busy markers with the SGR escapes stripped.
  local f m row
  f=$(perl -pe 's/\e\[[0-9;]*m//g' <<<"$1" | grep -v '^[[:space:]]*$' | tail -n "$FOOT_LINES")
  for m in "${BUSY_MARKERS[@]}"; do grep -qF -- "$m" <<<"$f" && return 1; done
  row=$(input_row_of "$1")
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
resolve_pane() { # the target may be a session name or any tmux target: from here on `pane` is the canonical #{pane_id}
  local id; id=$(tmux display -p -t "$pane" '#{pane_id}' 2>/dev/null) || id=
  [[ $id =~ ^%[0-9]+$ ]] || { say "ERROR cannot resolve $pane to a pane id; nothing sent"; exit 2; }
  pane=$id
}
lock_pane() { # one lock per pane id, shared with ctl-keepalive.sh (it pings a pane under the same lock); a held lock is waited for
  local dir; dir=$(seam LOCK_DIR "${CONTROLLER_HOME:-$HOME/controller}/locks")
  if ! { mkdir -p -- "$dir" && exec {pane_lock_fd}>"$dir/$pane.lock"; } 2>/dev/null; then say "ERROR lock file not writable in $dir"; exit 2; fi
  flock -w "$(seam LOCK_WAIT 120)" "$pane_lock_fd" || { say "ERROR pane $pane is locked by another resume helper or the keep-alive watcher; nothing sent"; exit 7; }
}
check_pane() { tmux has-session -t "$pane" 2>/dev/null || { say "ERROR no pane $pane"; exit 2; }; }
send_text() { # literal text (`--` stops flag parsing: a text starting with `-`), a pause, then Enter; failure exits 6
  tmux send-keys -t "$pane" -l -- "$1" || { say "ERROR send-keys failed (text)"; exit 6; }
  sleep "$type_pause"
  tmux send-keys -t "$pane" Enter || { say "ERROR send-keys failed (Enter)"; exit 6; }
}
