#!/usr/bin/env bash
# shellcheck disable=SC1091,SC2016 # the prompt fixture carries a literal $HOME and backticks; the shared frame lib is sourced
set -uo pipefail
# clear-resume.sh against a STUB tmux first on PATH: it records every call and serves capture-pane
# output from frame files that advance one per call (the last frame repeats), so the checks are
# deterministic under any host load. The script's poll interval is shortened through its test seams.
unset CLEAR_RESUME_POLL CLEAR_RESUME_IDLE_TRIES CLEAR_RESUME_CLEAR_TRIES CLEAR_RESUME_TYPE_PAUSE CLEAR_RESUME_FINAL_WAIT
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SCRIPT=${CLEAR_RESUME_SCRIPT:-$SCRIPT_DIR/../clear-resume.sh}
# shellcheck source=pane-frame-lib.sh
source "$SCRIPT_DIR/pane-frame-lib.sh"   # panel_frame and the REAL agents-panel capture (PANELFIX), shared with the watcher test
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-clear-resume-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
export CLEAR_RESUME_LOCK_DIR=$ROOT/locks   # the per-pane lock must not touch the real $HOME/controller/locks
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }
RULE=$(printf '─%.0s' {1..40})

mkdir -p "$ROOT/bin"
cat > "$ROOT/bin/tmux" <<'SH'
#!/usr/bin/env bash
s=$STUB_DIR
case $1 in
  display) printf '%s\n' "$4" ;;   # display -p -t <target> '#{pane_id}': the targets here are pane ids already
  has-session) echo "has-session $3" >>"$s/calls.log"; [[ ! -e $s/nosession ]]; exit ;;
  capture-pane)
    phase=$(<"$s/phase"); k=$(( $(cat "$s/idx.$phase" 2>/dev/null || echo 0) + 1 )); echo "$k" >"$s/idx.$phase"
    f=$(printf '%s/frames/%s.%03d' "$s" "$phase" "$k")
    [[ -e $f ]] || f=$(printf '%s\n' "$s/frames/$phase".* | sort | tail -1)
    echo "capture $phase" >>"$s/calls.log"
    # like tmux: -e keeps the SGR escapes of the fixture, without it they are dropped
    if [[ " $* " == *" -e "* ]]; then cat -- "$f"; else sed 's/\x1b\[[0-9;]*m//g' "$f"; fi ;;
  send-keys)
    # send-keys -t <pane> /clear Enter | -t <pane> -l [--] <text> | -t <pane> Enter
    # Like tmux, a text that starts with "-" is parsed as flags (exit 1) unless "--" precedes it.
    if [[ $4 == "-l" ]]; then
      [[ -e $s/sendfail ]] && exit 1
      if [[ $5 == -- ]]; then t=$6; else t=$5; [[ $t == -* ]] && exit 1; fi
      printf '%s' "$t" >"$s/sent.txt"; echo "send-keys $3 -l <text>" >>"$s/calls.log"
    else echo "send-keys $3 $4${5:+ $5}" >>"$s/calls.log"; [[ $4 == /clear ]] && echo cleared >"$s/phase"; fi ;;
esac
exit 0
SH
chmod +x "$ROOT/bin/tmux"

# frame <dir> <phase> <n> <kind>: B busy, I idle (a real prompt box, input row = marker + NBSP only), T the same box
# with text typed in the input row, S an older non-empty input row in the scrollback and an empty current one, D idle
# with a stale busy line ABOVE the box's top rule (the history, not the box), C cleared, N not yet cleared; the L kinds
# are the REAL agents-panel capture with N
# appended panel rows, idle (LI) or busy (L<n>B), and LMB is the same capture in ANOTHER permission mode (no mode-line
# marker, so nothing is trimmed: the box must still anchor the busy check).
frame() {
  local f; f=$(printf '%s/frames/%s.%03d' "$1" "$2" "$3")
  case $4 in
    B) printf 'output\n  * Thinking (12s . esc to interrupt)\n\n' >"$f" ;;
    I) printf 'output\n%s\n❯\xc2\xa0\n%s\n  ctx 41%%\n' "$RULE" "$RULE" >"$f" ;;
    T) printf 'output\n%s\n❯ half-typed text\n%s\n  ctx 41%%\n' "$RULE" "$RULE" >"$f" ;;
    S) printf '❯ older prompt\nreply\n%s\n❯\xc2\xa0\n%s\n  ctx 41%%\n' "$RULE" "$RULE" >"$f" ;;
    G) printf 'output\n%s\n❯\xc2\xa0\x1b[2mrun the tests\x1b[0m\n%s\n  ctx 41%%\n' "$RULE" "$RULE" >"$f" ;;        # dim prompt suggestion = empty
    X) printf 'output\n%s\n\x1b[39m❯\x1b[0m half-typed \x1b[2mtext\x1b[22m\n%s\n  ctx 41%%\n' "$RULE" "$RULE" >"$f" ;; # coloured marker, plain typed text
    D) printf '(esc to interrupt)\n1\n2\n3\n4\n%s\n❯\xc2\xa0\n%s\n  ctx 41%%\n' "$RULE" "$RULE" >"$f" ;;   # a stale busy line ABOVE the prompt box
    C) printf '%s\n❯\xc2\xa0\n%s\n  ctx ?\n' "$RULE" "$RULE" >"$f" ;;
    N) printf '%s\n❯\xc2\xa0\n%s\n  loading\n' "$RULE" "$RULE" >"$f" ;;
    LI) panel_frame "$f" 3 0 1 ;;
    L1B) panel_frame "$f" 1 1 1 ;;
    L3B) panel_frame "$f" 3 1 1 ;;
    L6B) panel_frame "$f" 6 1 1 ;;
    L12B) panel_frame "$f" 12 1 1 ;;
    LMB) panel_frame "$f" 6 1 1 '⏵⏵ accept edits on' ;;   # busy in another permission mode: no mode-line marker, no trim
    EMPTY|QMENU|BANG|NORULE|DRAFT|DRAFT_FIX|DRAFT_VERIFY|MENUBOX|H1I|H1B|SPIN|PROSE) stray_frame "$f" "$4" ;;   # the shared refusal / H1 / L2 frames
  esac
}
# scenario <name> <start frames> <cleared frames>: fresh stub dir, prompt and log
scenario() {
  D=$ROOT/$1; mkdir -p "$D/frames"; echo start >"$D/phase"; local i=0 k a
  read -ra a <<<"$2"; for k in "${a[@]}"; do frame "$D" start $((++i)) "$k"; done; i=0
  read -ra a <<<"$3"; for k in "${a[@]}"; do frame "$D" cleared $((++i)) "$k"; done
  printf -- '- Resume as the successor.\n"quoted" $HOME `tick` -dash\nline two\n' >"$D/prompt.txt"
  printf -- '- Resume as the successor.\n"quoted" $HOME `tick` -dash\nline two' >"$D/expected.txt"
}
# run <args...>: the script under the stub, short polls; sets RC
run() {
  STUB_DIR=$D PATH=$ROOT/bin:$PATH CLEAR_RESUME_POLL=0.01 CLEAR_RESUME_TYPE_PAUSE=0.01 CLEAR_RESUME_FINAL_WAIT=0.01 \
    bash "$SCRIPT" "$@" </dev/null 2>"$D/stderr"; RC=$?
}
calls() { cat -- "$D/calls.log" 2>/dev/null; }
rep() { local n=$1; shift; local i; for ((i = 0; i < n; i++)); do printf '%s\n' "$@"; done; }

# 1. Busy then idle (with an idle blip that resets the count): waits for 3 idle checks in a row,
#    sends /clear, waits for the cleared marker (3 polls), 3 more checks before the prompt, types the
#    prompt exactly, then Enter.
scenario happy "B B I I B I I I" "N N C"
run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "happy path exit $RC"
want=$( { echo "has-session %9"; rep 8 "capture start"; echo "send-keys %9 /clear Enter"; rep 6 "capture cleared"
          echo "send-keys %9 -l <text>"; echo "send-keys %9 Enter"; echo "capture cleared"; } )
[[ $(calls) == "$want" ]] || { calls >&2; fail "happy path call sequence"; }
cmp -s "$D/sent.txt" "$D/expected.txt" || fail "prompt text was not sent exactly"
contains "$D/log" "pane idle, input empty; sending /clear" "idle logged"
contains "$D/log" "clear confirmed (ctx ?)" "clear logged"
contains "$D/log" "prompt sent; footer:" "final footer logged"

# 2. A busy marker above the last four non-blank lines is not busy (3 idle checks suffice).
scenario stale "D" "C"
run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "stale busy line exit $RC"
[[ $(calls | grep -c '^capture start') -eq 3 ]] || fail "stale busy line: expected exactly 3 idle checks"

# 3. Never idle (continuously busy): exit 3, nothing sent.
scenario busy "B" "C"
CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "never idle exit $RC (want 3)"
[[ $(calls) == "$(printf '%s\n' 'has-session %9'; rep 5 'capture start')" ]] || fail "never idle: call sequence"
contains "$D/log" "ERROR pane never idle" "never idle logged"
[[ ! -e $D/sent.txt ]] || fail "never idle: a prompt was sent"
not_contains <(calls) "send-keys" "never idle: keys were sent"

# 4. Only short idle runs (never 3 in a row) is also never idle.
scenario blips "B I I B I I B I I" "C"
CLEAR_RESUME_IDLE_TRIES=9 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "idle blips exit $RC (want 3)"
not_contains <(calls) "send-keys" "idle blips: keys were sent"

# 5. No cleared marker within the limit: WARN, the prompt is still sent, exit 0.
scenario nocheck "I" "N"
CLEAR_RESUME_CLEAR_TRIES=3 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "no cleared marker exit $RC"
contains "$D/log" "WARN no 'ctx ?' after 60 s" "WARN logged"
cmp -s "$D/sent.txt" "$D/expected.txt" || fail "no cleared marker: prompt not sent"
[[ $(calls | grep -c '^capture cleared') -eq 8 ]] || fail "no cleared marker: 3 polls + WARN footer + 3 pre-prompt checks + final footer"

# 5b. A failing send-keys of the prompt exits 6 and is not logged as sent (the happy path above types a
#     prompt starting with "-", which the stub rejects without "--": the old code lost it silently).
scenario sendfail "I" "C"
touch "$D/sendfail"
run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 6 ]] || fail "send-keys failure exit $RC (want 6)"
contains "$D/log" "ERROR send-keys failed (text)" "send failure logged"
not_contains "$D/log" "prompt sent" "send failure: logged as sent"

# 5c. Text in the input row: nothing is sent until it is empty again, before /clear and before the prompt;
#     the LAST input row counts (an older non-empty one in the scrollback does not), NBSP-only is empty.
scenario typing "T T I I I" "C T T C C C"
run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "typing exit $RC"
want=$( { echo "has-session %9"; rep 5 "capture start"; echo "send-keys %9 /clear Enter"; rep 6 "capture cleared"
          echo "send-keys %9 -l <text>"; echo "send-keys %9 Enter"; echo "capture cleared"; } )
[[ $(calls) == "$want" ]] || { calls >&2; fail "typing: nothing may be sent before the input row is empty"; }
scenario scroll "S" "C"
run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "older non-empty input row in the scrollback blocked the send (exit $RC)"
[[ $(calls | grep -c '^capture start') -eq 3 ]] || fail "scrollback row: expected exactly 3 checks"

# 5d. The input row never empties: exit 5, nothing is ever sent (checked from the recorded send-keys calls),
#     at the start and again before the prompt (after /clear).
scenario typed "T" "C"
CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 5 ]] || fail "input never empty exit $RC (want 5)"
contains "$D/log" "ERROR input line still has text" "input never empty logged"
not_contains <(calls) "send-keys" "input never empty: keys were sent"
scenario typedlate "I" "C T"
CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 5 ]] || fail "input never empty after /clear exit $RC (want 5)"
[[ $(calls | grep -c '^send-keys') -eq 1 ]] || { calls >&2; fail "input never empty after /clear: only /clear Enter may be sent"; }
[[ ! -e $D/sent.txt ]] || fail "input never empty after /clear: the prompt was typed"

# 5e. A dim prompt suggestion in the input row (ESC[2m ... ESC[0m) is not typed text: the helper proceeds;
#     plain text after a coloured marker is: nothing is sent (the stub serves the escapes only for capture -e).
scenario dim "G" "C"
run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "dim suggestion blocked the send (exit $RC)"
[[ $(calls | grep -c '^capture start') -eq 3 ]] || fail "dim suggestion: expected exactly 3 checks"
scenario coloured "X" "C"
CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 5 ]] || fail "typed text after a coloured marker exit $RC (want 5)"
not_contains <(calls) "send-keys" "typed text after a coloured marker: /clear was sent"

# 5f. F4: an agents panel below the mode line never hides a running turn. With 1, 3, 6 or 12 appended panel rows the mode
#     line's `esc to interrupt` sits OUTSIDE the last 4 non-blank lines of the untrimmed capture: the 3 fixture panel lines
#     put the mode line 5 non-blank lines above the bottom with 1 appended row, and one more for each further row (16 at
#     12), so at 91e9d65f3 wait_idle counted three "idle" polls and sent /clear into a running turn.
for kind in L1B L3B L6B L12B; do
  scenario "panel-$kind" "$kind" "C"
  CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
  [[ $RC -eq 3 ]] || fail "agents panel $kind: exit $RC (want 3, the pane is busy)"
  not_contains <(calls) "send-keys" "agents panel $kind: keys were sent into a running turn"
done
# 5g. The same busy frame in ANOTHER permission mode: its mode line is not `⏵⏵ bypass permissions on`, so pane_view
#     trims nothing and the busy check must still find the marker below the prompt box's top rule. At 91e9d65f3 this
#     read as idle as well.
scenario panel-othermode "LMB" "C"
CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "other permission mode: exit $RC (want 3, the pane is busy)"
not_contains <(calls) "send-keys" "other permission mode: keys were sent into a running turn"
# 5h. The REAL idle agents panel with the unfocused hint stays ready: the trim must not break the happy path.
scenario panel-idle "LI" "C C"
run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "idle agents panel exit $RC"
[[ $(calls | grep -c '^capture start') -eq 3 ]] || fail "idle agents panel: expected exactly 3 idle checks"
contains <(calls) 'send-keys %9 /clear Enter' "idle agents panel: /clear was not sent"
cmp -s "$D/sent.txt" "$D/expected.txt" || fail "idle agents panel: the prompt was not sent exactly"

# 5i. M2: a pane the helper cannot POSITIVELY recognise as an idle prompt box is never typed into. These four already
#     read busy at 4708ed03e (and at 91e9d65f3) - they are the PIN the review found missing, not a behaviour change: delete
#     the `st == 2` refusal in pane_state (or let an empty capture return 1), and each one reads READY (pane_state_of finds
#     no busy marker and no `❯` row) and /clear + the prompt are typed into it.
#     - EMPTY: a failed or empty capture.
#     - QMENU: a column-0 question menu: no `❯` cursor, no `Esc to cancel`, and no input row at all.
#     - BANG: a bash-mode `!` row, which is not the prompt box's `❯` row, so the row checks would pass.
#     - NORULE: a `❯` row with a rule BELOW it and none above.
for kind in EMPTY QMENU BANG NORULE; do
  scenario "refuse-$kind" "$kind" "C"
  CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
  [[ $RC -eq 3 ]] || fail "refusal $kind: exit $RC (want 3, not ready)"
  not_contains <(calls) "send-keys" "refusal $kind: keys were sent into a pane that was never recognised as idle"
done
# 5j. M1: a DRAFT with an empty first row reads READY at 4708ed03e (busy_beside_box sees only the rule above the row and
#     pane_state_of sees an empty row), so the three idle polls pass and /clear is submitted into the draft. With the
#     plain rule required BELOW the row it has no box. The reason and the footer are logged ONCE (H1).
for kind in DRAFT DRAFT_FIX DRAFT_VERIFY; do
  scenario "refuse-$kind" "$kind" "C"
  CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
  [[ $RC -eq 3 ]] || fail "refusal $kind: exit $RC (want 3, a multi-line draft is not ready)"
  not_contains <(calls) "send-keys" "refusal $kind: keys were sent over a draft"
  contains "$D/log" "WARN pane not ready: no prompt box recognised" "refusal $kind: the reason was not logged"
  [[ $(grep -c 'WARN pane not ready' "$D/log") -eq 1 ]] || fail "refusal $kind: the reason must be logged ONCE per wait"
  contains "$D/log" "|  ctx 41%" "refusal $kind: the footer is missing from the logged reason"
done
# 5k. M1: the permission menu ABOVE an intact empty box: the box is recognised, no busy marker is inside it and the row
#     is empty, so at 4708ed03e the helper reads READY and the Enter answers the menu - only dialog_open stops it, and
#     the helpers never ran that check before. (A menu with no box at all is covered by QMENU.)
scenario refuse-MENUBOX "MENUBOX" "C"
CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "refusal MENUBOX: exit $RC (want 3, a dialog is open)"
not_contains <(calls) "send-keys" "refusal MENUBOX: keys were sent while a menu was open"
contains "$D/log" "WARN pane not ready: a dialog is open" "refusal MENUBOX: the dialog reason was not logged"
# 5l. H1: the LIVE main pane's labelled top rule (`─── ◉ xhigh · /effort  vbpub-main-controller ─`) is a box: the idle
#     frame must be typed into (at 4708ed03e it is not: no box is recognised, the pane reads busy at every poll, /clear
#     is never sent). The same frame with `esc to interrupt` on the mode line must stay busy (already busy on the base
#     through the no-box scan: a pin, not a regression).
scenario h1-idle "H1I" "C"
run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "H1: the live main pane's labelled top rule must read ready (exit $RC)"
[[ $(calls | grep -c '^capture start') -eq 3 ]] || fail "H1: expected exactly 3 idle checks on the labelled box"
contains <(calls) 'send-keys %9 /clear Enter' "H1: /clear was not sent into the labelled box"
cmp -s "$D/sent.txt" "$D/expected.txt" || fail "H1: the prompt was not sent exactly on the labelled box"
scenario h1-busy "H1B" "C"
CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "H1 busy: exit $RC (want 3, the labelled box is busy)"
not_contains <(calls) "send-keys" "H1 busy: keys were sent into a running turn under the labelled box"
# 5m. L2: a `✶ Compacting` spinner six non-blank lines ABOVE the box's top rule (outside the FOOT_LINES window a plain
#     box has) makes the pane busy; the same lines with prose that only CONTAINS the word do not, so the rule is not a
#     bare substring. At 4708ed03e the spinner frame reads READY (only `esc to interrupt` is a busy marker there).
scenario l2-spinner "SPIN" "C"
CLEAR_RESUME_IDLE_TRIES=5 run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "L2 spinner: exit $RC (want 3, a compaction is running above the box)"
not_contains <(calls) "send-keys" "L2 spinner: keys were sent while a compaction was running above the box"
scenario l2-prose "PROSE" "C"
run '%9' "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "L2 prose: plain prose containing the word must not read busy (exit $RC)"
[[ $(calls | grep -c '^capture start') -eq 3 ]] || fail "L2 prose: expected exactly 3 idle checks"

# 6. Argument and precondition errors exit 2 and send nothing.
scenario args "I" "C"
run; [[ $RC -eq 2 ]] || fail "no arguments exit $RC"
contains "$D/stderr" "usage:" "usage line"
run '%9' "$D/prompt.txt"; [[ $RC -eq 2 ]] || fail "two arguments exit $RC"
run '%9' "$D/missing.txt" "$D/log"; [[ $RC -eq 2 ]] || fail "missing prompt exit $RC"
contains "$D/log" "ERROR prompt file unreadable" "missing prompt logged"
run '%9' "$D/prompt.txt" "$ROOT/no-such-dir/log"; [[ $RC -eq 2 ]] || fail "unwritable log exit $RC"
contains "$D/stderr" "log file not writable" "unwritable log message"
[[ ! -e $D/calls.log ]] || fail "precondition errors reached tmux"
touch "$D/nosession"
run '%9' "$D/prompt.txt" "$D/log2"; [[ $RC -eq 2 ]] || fail "missing pane exit $RC"
contains "$D/log2" "ERROR no pane %9" "missing pane logged"
[[ $(calls) == "has-session %9" ]] || fail "missing pane: only has-session expected"

echo 'PASS clear-resume'
