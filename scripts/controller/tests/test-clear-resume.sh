#!/usr/bin/env bash
# shellcheck disable=SC2016 # the prompt fixture carries a literal $HOME and backticks
set -uo pipefail
# clear-resume.sh against a STUB tmux first on PATH: it records every call and serves capture-pane
# output from frame files that advance one per call (the last frame repeats), so the checks are
# deterministic under any host load. The script's poll interval is shortened through its test seams.
unset CLEAR_RESUME_POLL CLEAR_RESUME_IDLE_TRIES CLEAR_RESUME_CLEAR_TRIES CLEAR_RESUME_TYPE_PAUSE CLEAR_RESUME_FINAL_WAIT
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SCRIPT=${CLEAR_RESUME_SCRIPT:-$SCRIPT_DIR/../clear-resume.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-clear-resume-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }

mkdir -p "$ROOT/bin"
cat > "$ROOT/bin/tmux" <<'SH'
#!/usr/bin/env bash
s=$STUB_DIR
case $1 in
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

# frame <dir> <phase> <n> <kind>: B busy, I idle (input row = marker + NBSP only), T idle with text typed in the
# input row, S idle with an older non-empty input row in the scrollback and an empty current one, D idle with a
# stale busy line above the last 4, C cleared, N not yet cleared
frame() {
  local f; f=$(printf '%s/frames/%s.%03d' "$1" "$2" "$3")
  case $4 in
    B) printf 'output\n  * Thinking (12s . esc to interrupt)\n\n' >"$f" ;;
    I) printf 'output\n❯\xc2\xa0\n\n  ctx 41%%\n' >"$f" ;;
    T) printf 'output\n❯ half-typed text\n\n  ctx 41%%\n' >"$f" ;;
    S) printf '❯ older prompt\nreply\n❯\xc2\xa0\n\n  ctx 41%%\n' >"$f" ;;
    G) printf 'output\n❯\xc2\xa0\x1b[2mrun the tests\x1b[0m\n\n  ctx 41%%\n' >"$f" ;;        # dim prompt suggestion = empty
    X) printf 'output\n\x1b[39m❯\x1b[0m half-typed \x1b[2mtext\x1b[22m\n\n  ctx 41%%\n' >"$f" ;; # coloured marker, plain typed text
    D) printf '(esc to interrupt)\n1\n2\n3\n4\n> \n' >"$f" ;;
    C) printf '\n> \n  ctx ?\n' >"$f" ;;
    N) printf '> \n  loading\n' >"$f" ;;
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
    bash "$SCRIPT" "$@" 2>"$D/stderr"; RC=$?
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
