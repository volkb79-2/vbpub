# shellcheck shell=bash disable=SC2034,SC2154 # sourced: the callers use the settings and own pane/log
# resume-lib.sh: the pane-idle/footer logic shared by clear-resume.sh, compact-resume.sh and ctl-keepalive.sh.
# Source it, never run it. The caller sets RESUME_PREFIX (the env prefix of its test seams,
# e.g. CLEAR_RESUME) before sourcing and pane/log after; one copy of the footer and pane-view markers lives here.
#
# Footer markers, tied to the status line measured on Claude Code 2.1.331; adjust if it changes.
# KNOWN LIMIT, deliberately not covered by a width floor: the busy markers are matched as a per-line SUBSTRING, so a pane
# narrow enough to wrap or truncate `esc to interrupt` can read idle. The keep-alive watcher refuses panes narrower than
# KEEPALIVE_MIN_WIDTH (80 columns); the resume helpers have no such check (a helper that waited on a width would strand
# an idle controller), so the limit stands there, recorded here and in docs/design/agctl/CONTEXT-ECONOMY.md.
BUSY_MARKERS=('esc to interrupt')   # present while the session works (a caller may add more)
CLEARED_MARKER='ctx ?'              # present after /clear, before the first turn
COMPACTING_MARKER='Compacting'      # the spinner line of a running compaction (compact-resume.sh adds it to BUSY_MARKERS)
INPUT_MARKER='❯'                    # starts the input row of the prompt box (the LAST such line is the live one)
FOOT_LINES=4                        # how many non-blank bottom lines count as the footer
SPINNER_SCAN_LINES=6                # ... and how many non-blank lines ABOVE the box's top rule are searched; tests pin line 6
BOX_RULE_BYTES='(?:\xe2\x94\x80){20,}'                                         # a prompt-box rule: at least 20 x U+2500, nothing else (perl, bytes)
BOX_LABEL_RULE_BYTES='(?:\xe2\x94\x80){20,}[A-Za-z0-9._-]+(?:\xe2\x94\x80)+'   # the same rule with one tmux session label (spaces stripped); ctl-keepalive.sh's box_ok only
BOX_TOP_RULE_BYTES='(?:\xe2\x94\x80){20,}(?:.*(?:\xe2\x94\x80))?'              # the resume helpers' TOP rule: starts with 20+ x U+2500 and ends with U+2500 - ANY label between, e.g. the live main pane's `──── ◉ xhigh · /effort  vbpub-main-controller ─`
BOX_FOOT_MARKER='⏵⏵ bypass permissions on'                                     # the mode line: rows below it are not part of the prompt box
BOX_PANEL_HINT='↓ to manage'                                                   # the mode line's unfocused agents-panel hint: only this panel layout is trimmed
DIALOG_CURSOR_RE='^[[:space:]]*❯[[:space:]]*[0-9]+\.[[:space:]]'   # cursor on a numbered option of a permission/question menu
                                                                  # (verbatim from ctl-keepalive.sh: the watcher's dialog
                                                                  # check must stay byte-identical)
DIALOG_FOOTER='Esc to cancel'                                     # footer of those menus
DIALOG_LINES=15                                                   # how many bottom non-blank lines are searched for them
SPINNER_RE='^[ \t]*[·✢✳✶✻✽*][ \t]+Compacting\b.*(?:…|\.\.\.)'  # actual spinner set, followed by a required ellipsis
not_ready_said=0                   # one "not ready" reason per wait_idle, not one per poll (see pane_state/say_once)

# Test seams: <RESUME_PREFIX>_POLL (seconds between polls), _IDLE_TRIES, _TYPE_PAUSE, _FINAL_WAIT.
seam() { local v=${RESUME_PREFIX}_$1; printf '%s' "${!v:-$2}"; }
poll=$(seam POLL 2) idle_tries=$(seam IDLE_TRIES 900)
type_pause=$(seam TYPE_PAUSE 1) final_wait=$(seam FINAL_WAIT 10)

say() { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*" >>"$log"; }
say_once() { # $1 = a message: logged at most once between two wait_idle calls (a 900-poll wait would flood the log)
  (( not_ready_said )) && return 0
  not_ready_said=1
  say "$1"
}

# ---- one pane view: the prompt box and every bottom window are measured from the MODE line, never from the last line
# ---- of the capture, so the agents panel Claude Code draws BELOW the mode line cannot push a busy marker or a menu
# ---- out of a window. The rows below the mode line are trimmed only while it carries the unfocused hint
# ---- BOX_PANEL_HINT: a focused panel is not captured yet and may look identical, so without the hint nothing is
# ---- trimmed and the capture is returned unchanged (fail-safe). Shared with ctl-keepalive.sh.
strip_sgr() { perl -pe 's/\e\[[0-9;]*m//g'; }
is_rule() { local r=${1//$'\xc2\xa0'/}; r=${r//[[:space:]]/}; perl -e 'exit($ARGV[0] =~ /^'"$BOX_RULE_BYTES"'$/ ? 0 : 1)' -- "$r"; }
is_label_rule() { local r=${1//$'\xc2\xa0'/}; r=${r//[[:space:]]/}; perl -e 'exit($ARGV[0] =~ /^'"$BOX_LABEL_RULE_BYTES"'$/ ? 0 : 1)' -- "$r"; }
is_top_rule() { # $1 = the line directly above the input row: the prompt box's TOP rule, 20+ x U+2500, then ANY label,
  # then U+2500 at the END (H1 accepts the live main pane's `───── ◉ xhigh · /effort  vbpub-main-controller ─`, which
  # is_label_rule's [A-Za-z0-9._-] label set refuses). The trailing whitespace is not part of a capture's rule line, so
  # the bytes after it are what must end in the glyph; a row with text after the glyph is refused.
  # The pattern and the check are perl on BYTES, so no locale can alter them. Ready needs BOTH this matcher and a plain
  # rule BELOW the input row (box_at).
  local r; r=$(printf '%s' "$1" | perl -pe 's/^\s+//; s/\s+$//')
  perl -e 'exit($ARGV[0] =~ /^'"$BOX_TOP_RULE_BYTES"'$/ ? 0 : 1)' -- "$r"; }   # `--`: this line may begin with `-`
spinner_above() { # stdin = the non-blank lines just above the box's top rule: 0 only for a recognized spinner and ellipsis
  # (L2). Match decoded Unicode text against explicit glyphs and ASCII spacing, independent of the process locale.
  perl -e 'use utf8; my $hit = 0;
    while (<STDIN>) {
      my $txt = $_; utf8::decode($txt);
      $hit = 1, last if $txt =~ /'"$SPINNER_RE"'/;
    }
    exit($hit ? 0 : 1)'
}
pane_view() { # $1 = capture with escapes: the captured lines down to and including the MODE line, trimmed ONLY when the
  # mode line carries the unfocused panel hint; otherwise (a focused panel, another permission mode, an unknown layout)
  # the capture is returned unchanged (fail-safe). The ONE view the busy, dialog and box checks of both callers measure.
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
box_at() { # $1 = a pane_view capture; prints the TOP RULE's 0-based index in the capture's non-blank lines when the
  # LAST input row sits in a recognised box, else nothing. A box needs BOTH rules (M1): the top rule (is_top_rule)
  # directly above the input row and a PLAIN rule directly below it (is_rule) - the box_ok geometry WITHOUT box_ok's
  # BOX_LINES window. Only the label above is tolerated, so a draft line below the row, a menu or history line above it,
  # or a panel row underneath refuses the box. A refused box is NOT ready; see busy_beside_box.
  local ln=() i n idx=-1 re="^$INPUT_MARKER"
  mapfile -t ln < <(strip_sgr <<<"$1" | grep -av '^[[:space:]]*$')
  n=${#ln[@]}
  for ((i = 0; i < n; i++)); do [[ ${ln[i]} =~ $re ]] && idx=$i; done
  (( idx >= 1 && idx + 1 < n )) || return 1
  is_top_rule "${ln[idx-1]}" && is_rule "${ln[idx+1]}" || return 1
  printf '%s' "$((idx - 1))"
}
busy_beside_box() { # $1 = a pane_view capture: 0 a BUSY_MARKERS line is at or below the box's last top rule (the box),
  # 3 a recognized compaction spinner starts one of the SPINNER_SCAN_LINES non-blank lines just ABOVE that rule (L2; the
  # spinner sits above the box while a compaction runs and the bottom window does not reach it); 1 the box holds neither;
  # 2 NO prompt box was recognised. 2 is NEVER a ready answer: a caller that would type into this pane must treat it as
  # busy, and without a recognisable box nothing about the pane is known (L1: the old no-box 20-line fallback could only
  # return 0 or 2, and 2 was busy anyway, so it could never change a verdict - it is gone, with its constant).
  # The box is the anchor: pane_view trims to the mode line only in the ONE layout it recognises (the bypass mode line
  # carrying the unfocused panel hint), so a pane in another permission mode keeps its panel rows and the mode line's
  # `esc to interrupt` falls outside pane_state_of's FOOT_LINES window. Busy wrongly only waits or times out; idle
  # wrongly is typed into.
  local ln=() start=0 win m top
  mapfile -t ln < <(strip_sgr <<<"$1" | grep -av '^[[:space:]]*$')
  top=$(box_at "$1") || return 2
  win=$(printf '%s\n' "${ln[@]:top}")   # from the last top rule downwards: everything above it is history
  for m in "${BUSY_MARKERS[@]}"; do grep -qF -- "$m" <<<"$win" && return 0; done
  start=$((top - SPINNER_SCAN_LINES)); (( start < 0 )) && start=0
  win=$(printf '%s\n' "${ln[@]:start:top-start}")   # strictly ABOVE the top rule (the bottom window ends below it)
  spinner_above <<<"$win" && return 3
  return 1
}
foot() { pane_view "$(tmux capture-pane -e -p -t "$pane")" | foot_of; }
foot_of() { strip_sgr | grep -v '^[[:space:]]*$' | tail -n "$FOOT_LINES"; }   # stdin = a pane_view capture
busy() { pane_state; [[ $? -eq 1 ]]; } # one capture per call (wait_idle and compact-resume.sh reset the once-per-wait log)
pane_state() { # ONE capture; 0 ready, 1 busy, 2 the input row has text. Anything not positively recognised as an IDLE
  # prompt box is BUSY, never ready: the caller is about to type into this pane. A busy marker inside the box is just
  # busy (no log); the two states the helper cannot read - no recognised box (H1), and an open dialog - log their reason
  # and the footer once per wait (say_once), instead of leaving exit 3/4/5 to say only "the pane never went idle".
  local view st
  view=$(pane_view "$(tmux capture-pane -e -p -t "$pane")")
  busy_beside_box "$view"; st=$?
  (( st == 0 )) && return 1
  if (( st == 3 )); then
    say_once "WARN pane not ready: compaction spinner above the prompt box; footer: $(foot_of <<<"$view" | tr '\n' '|')"
    return 1
  fi
  if (( st == 2 )); then
    say_once "WARN pane not ready: no prompt box recognised (no top rule above the input row with a plain rule below it); footer: $(foot_of <<<"$view" | tr '\n' '|')"
    return 1
  fi
  if dialog_open "$view"; then
    say_once "WARN pane not ready: a dialog is open; footer: $(foot_of <<<"$view" | tr '\n' '|')"
    return 1
  fi
  pane_state_of "$view"
}
dialog_open() { # $1 = a pane_view capture: a numbered permission/question menu is on screen (moved here from
  # ctl-keepalive.sh so the resume helpers run the same check; the watcher's behaviour is unchanged)
  local plain; plain=$(strip_sgr <<<"$1" | grep -v '^[[:space:]]*$' | tail -n "$DIALOG_LINES")
  grep -qE -- "$DIALOG_CURSOR_RE" <<<"$plain" || grep -qF -- "$DIALOG_FOOTER" <<<"$plain"
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
  not_ready_said=0   # one not-ready reason per wait, however many polls it takes
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
