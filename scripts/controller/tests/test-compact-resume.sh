#!/usr/bin/env bash
# shellcheck disable=SC1091,SC2016 # the fixtures carry a literal $HOME and backticks; the shared frame lib is sourced
set -uo pipefail
# compact-resume.sh against a STUB tmux first on PATH: it records every call and serves capture-pane
# output from frame files that advance one per call (the last frame repeats), so the checks are
# deterministic under any host load. The script's poll interval is shortened through its test seams.
unset COMPACT_RESUME_POLL COMPACT_RESUME_IDLE_TRIES COMPACT_RESUME_TYPE_PAUSE COMPACT_RESUME_FINAL_WAIT \
  COMPACT_RESUME_START_TRIES COMPACT_RESUME_DONE_TRIES COMPACT_RESUME_SETTLE COMPACT_RESUME_RECEIPT_DIR
unset CLAUDE_CODE_SESSION_ID COMPACT_RESUME_SESSION_ID TMUX_PANE
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SCRIPT=${COMPACT_RESUME_SCRIPT:-$SCRIPT_DIR/../compact-resume.sh}
# shellcheck source=pane-frame-lib.sh
source "$SCRIPT_DIR/pane-frame-lib.sh"   # panel_frame and the REAL agents-panel capture (PANELFIX), shared with the watcher test
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-compact-resume-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
export COMPACT_RESUME_LOCK_DIR=$ROOT/locks   # the per-pane lock must not touch the real $HOME/controller/locks
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }
RULE=$(printf '─%.0s' {1..40})

mkdir -p "$ROOT/bin"
# Phases: "start" until /compact is submitted, then "compact". Every literal send is stored separately
# and concatenated by phase so tests pin both the chunk calls and the resulting text.
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
    if [[ " $* " == *" -e "* ]]; then cat -- "$f"; else sed 's/\x1b\[[0-9;]*m//g' "$f"; fi
    if [[ $phase == compact && $k == "${STUB_RECEIPT_AT:-1}" ]]; then
      mkdir -p "$COMPACT_RESUME_RECEIPT_DIR"
      printf '{"session_id":"stub-session","timestamp":"stub","write_id":%s}\n' "$k" \
        >"$COMPACT_RESUME_RECEIPT_DIR/${STUB_RECEIPT_PANE:-%9}.json"
    fi ;;
  send-keys)
    # send-keys -t <pane> -l [--] <text> | -t <pane> Enter
    # Like tmux, a text that starts with "-" is parsed as flags (exit 1) unless "--" precedes it.
    if [[ $4 == "-l" ]]; then
      [[ -e $s/sendfail ]] && exit 1
      if [[ $5 == -- ]]; then t=$6; else t=$5; [[ $t == -* ]] && exit 1; fi
      n=$(( $(cat "$s/nsent" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$s/nsent"
      printf '%s' "$t" >"$s/raw.$n"
      # tmux drops an ending ";" but removes the backslash from an ending "\;".
      if [[ ${t: -2} == '\;' ]]; then t=${t:0:${#t}-2}; t+=';'
      elif [[ $t == *';' ]]; then t=${t%?}; fi
      printf '%s' "$t" >"$s/sent.$n"; printf '%s' "$t" >>"$s/sent-$phase"
      printf '%s' "$t" >>"$s/sent-chunks"
      echo "send-keys $3 -l <text $n>" >>"$s/calls.log"
    else
      echo "send-keys $3 $4" >>"$s/calls.log"
      [[ $(<"$s/phase") == start ]] && echo compact >"$s/phase"
    fi ;;
esac
exit 0
SH
chmod +x "$ROOT/bin/tmux"

# frame <dir> <phase> <n> <kind>: B busy, K compacting marker, I idle (a real prompt box, input row = marker + NBSP
# only), T the same box with text typed in the input row, S an older non-empty input row in the scrollback and an empty
# current one; the L kinds are the REAL agents-panel capture with N appended panel rows, busy (L<n>B), and LMB is the
# same capture in ANOTHER permission mode (no mode-line marker, so nothing is trimmed: the box still anchors the busy check).
frame() {
  local f; f=$(printf '%s/frames/%s.%03d' "$1" "$2" "$3")
  case $4 in
    B) printf 'output\n%s\n❯\xc2\xa0\n%s\n  * Thinking (12s . esc to interrupt)\n  ctx 442k\n' "$RULE" "$RULE" >"$f" ;;  # busy marker inside a real box
    K) printf 'output\n%s\n❯\xc2\xa0\n%s\n  * Compacting conversation... (3s)\n  ctx 442k\n' "$RULE" "$RULE" >"$f" ;;  # only busy sign is below the box
    I) printf 'output\n%s\n❯\xc2\xa0\n%s\n  ctx 442k\n' "$RULE" "$RULE" >"$f" ;;
    T) printf 'output\n%s\n❯ half-typed text\n%s\n  ctx 442k\n' "$RULE" "$RULE" >"$f" ;;
    S) printf '❯ older prompt\nreply\n%s\n❯\xc2\xa0\n%s\n  ctx 442k\n' "$RULE" "$RULE" >"$f" ;;
    G) printf 'output\n%s\n❯\xc2\xa0\x1b[2mrun the tests\x1b[0m\n%s\n  ctx 442k\n' "$RULE" "$RULE" >"$f" ;;        # dim prompt suggestion = empty
    X) printf 'output\n%s\n\x1b[39m❯\x1b[0m half-typed \x1b[2mtext\x1b[22m\n%s\n  ctx 442k\n' "$RULE" "$RULE" >"$f" ;; # coloured marker, plain typed text
    LI) panel_frame "$f" 3 0 1 ;;
    L1B) panel_frame "$f" 1 1 1 ;;
    L3B) panel_frame "$f" 3 1 1 ;;
    L6B) panel_frame "$f" 6 1 1 ;;
    L12B) panel_frame "$f" 12 1 1 ;;
    LMB) panel_frame "$f" 6 1 1 '⏵⏵ accept edits on' ;;   # busy in another permission mode: no mode-line marker, no trim
    EMPTY|QMENU|BANG|NORULE|DRAFT|DRAFT_FIX|DRAFT_VERIFY|MENUBOX|H1I|H1B|SPIN|SPIN_EDGE|STALESPIN|SPIN_PROSE|PROSE) stray_frame "$f" "$4" ;;   # shared refusal / H1 / L2 / stale-scrollback frames
  esac
}
# scenario <name> <start frames> <compact frames>: fresh stub dir, guidance, prompt and log
scenario() {
  D=$ROOT/$1; mkdir -p "$D/frames"; echo start >"$D/phase"; local i=0 k a
  read -ra a <<<"$2"; for k in "${a[@]}"; do frame "$D" start $((++i)) "$k"; done; i=0
  read -ra a <<<"$3"; for k in "${a[@]}"; do frame "$D" compact $((++i)) "$k"; done
  printf 'Keep the handover path.\n"quoted" $HOME `tick` -dash\nlast line\n' >"$D/guidance.txt"
  printf '/compact Keep the handover path. "quoted" $HOME `tick` -dash last line' >"$D/expected-start.txt"
  printf -- '- Resume: read the handover.\n$HOME `tick`\nline three\n' >"$D/prompt.txt"
  printf -- '- Resume: read the handover.\n$HOME `tick`\nline three' >"$D/expected-resume.txt"
}
# run <args...>: the script under the stub, short polls; sets RC
run() {
  STUB_DIR=$D PATH=$ROOT/bin:$PATH COMPACT_RESUME_RECEIPT_DIR=$D/receipts \
    STUB_RECEIPT_AT=${STUB_RECEIPT_AT:-1} STUB_RECEIPT_PANE=${STUB_RECEIPT_PANE:-%9} \
    COMPACT_RESUME_POLL=0.01 COMPACT_RESUME_TYPE_PAUSE=0.01 \
    COMPACT_RESUME_FINAL_WAIT=0.01 COMPACT_RESUME_SETTLE=0.01 bash "$SCRIPT" "$@" </dev/null 2>"$D/stderr"; RC=$?
}
calls() { cat -- "$D/calls.log" 2>/dev/null; }
rep() { local n=$1; shift; local i; for ((i = 0; i < n; i++)); do printf '%s\n' "$@"; done; }

# 1. Busy then idle (3 idle checks in a row), /compact <guidance> typed exactly (newlines to spaces)
#    and submitted. A changed receipt confirms it; compaction-idle, settle, a second idle gate and
#    only then the resume prompt are required.
#    start: B I I I (4 checks) + footer-before = 5 captures. compact: receipt frame + five wait-idle
#    captures + done footer + three prompt-gate captures + final footer = 11 captures.
scenario happy "B I I I" "I K K I I I"
run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "happy path exit $RC"
want=$( { echo "has-session %9"; rep 5 "capture start"; echo "send-keys %9 -l <text 1>"; echo "send-keys %9 -l <text 2>"; echo "send-keys %9 Enter"
          rep 10 "capture compact"; echo "send-keys %9 -l <text 3>"; echo "send-keys %9 Enter"; echo "capture compact"; } )
[[ $(calls) == "$want" ]] || { calls >&2; fail "happy path call sequence"; }
cmp -s "$D/sent-start" "$D/expected-start.txt" || fail "/compact line was not sent exactly"
cmp -s "$D/sent-compact" "$D/expected-resume.txt" || fail "resume prompt was not sent exactly"
cmp -s "$D/sent.1" <(printf '/compact ') || fail "the command prefix was not a separate first send"
contains "$D/log" "pane idle, input empty; footer before: ctx 442k" "idle logged with the footer"
contains "$D/log" "compaction receipt observed:" "compaction receipt logged"
contains "$D/log" "compaction done; footer:" "compaction end logged"
contains "$D/log" "resume prompt sent; footer:" "final footer logged"

# A receipt appearing at the first otherwise-idle frame confirms an instant compaction.
scenario instant-receipt "I" "I I I I"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "instant receipt exit $RC"
cmp -s "$D/sent-compact" "$D/expected-resume.txt" || fail "instant receipt: resume prompt not sent"

# A busy compaction remains eligible for a receipt after the 60-poll confirmation window.
delay_frames=
for ((i = 0; i < 61; i++)); do delay_frames+="B "; done
scenario delayed-receipt "I" "$delay_frames I I I I I"
STUB_RECEIPT_AT=62 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "delayed receipt exit $RC"
[[ $(calls | grep -c '^capture compact') -gt 60 ]] || fail "receipt wait stopped before poll 61"
cmp -s "$D/sent-compact" "$D/expected-resume.txt" || fail "delayed receipt: resume prompt not sent"

# 2. Idle blips (fewer than 3 in a row) do not count as idle, before the /compact or during it.
scenario blips "I B I I B I I I" "K I I K I I K I I I"
run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "idle blips exit $RC"
[[ $(calls | grep -c '^capture compact') -eq 15 ]] || { calls >&2; fail "blips: compact captures (start 1 + wait 9 + done footer + 3 checks + final footer)"; }
cmp -s "$D/sent-compact" "$D/expected-resume.txt" || fail "blips: resume prompt not sent"

# 3. Never idle (continuously busy): exit 3, nothing sent.
scenario busy "B" "I"
COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "never idle exit $RC (want 3)"
[[ $(calls) == "$(printf '%s\n' 'has-session %9'; rep 5 'capture start')" ]] || { calls >&2; fail "never idle: call sequence"; }
contains "$D/log" "ERROR pane never idle" "never idle logged"
[[ ! -e $D/sent.1 ]] || fail "never idle: something was sent"
not_contains <(calls) "send-keys" "never idle: keys were sent"

# 4. Compaction remains busy without a receipt through the completion bound: exit 4, /compact was
#    sent, and the resume prompt was not.
scenario stuck "I" "B"
STUB_RECEIPT_AT=none COMPACT_RESUME_START_TRIES=3 COMPACT_RESUME_DONE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 4 ]] || fail "never finishing exit $RC (want 4)"
contains "$D/log" "ERROR compaction not finished" "never finishing logged"
cmp -s "$D/sent-start" "$D/expected-start.txt" || fail "never finishing: /compact not sent"
[[ ! -e $D/sent.3 ]] || fail "never finishing: the resume prompt was sent"
[[ $(calls | grep -c '^send-keys') -eq 3 ]] || fail "never finishing: only the command, guidance and Enter expected"
not_contains "$D/log" "compaction done" "never finishing: logged as done"

# 5. An unchanged receipt is stale; an idle pane without a receipt and a pasted line that becomes
#    idle are both unconfirmed and must not send the resume prompt.
scenario unchanged-receipt "I" "I"
mkdir -p "$D/receipts"
printf '{"session_id":"old","timestamp":"old","write_id":1}\n' >"$D/receipts/%9.json"
STUB_RECEIPT_AT=none COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 8 ]] || fail "unchanged receipt exit $RC (want 8)"
contains "$D/log" "SessionStart(compact) hook may be missing" "unchanged receipt missing-hook guidance"
contains "$D/log" "$D/receipts/%9.json" "receipt failure names its path"
[[ ! -e $D/sent.3 ]] || fail "unchanged receipt: resume prompt was sent"
scenario no-receipt-idle "I" "I"
STUB_RECEIPT_AT=none COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 8 ]] || fail "idle without receipt exit $RC (want 8)"
[[ ! -e $D/sent.3 ]] || fail "idle without receipt: resume prompt was sent"
scenario pasted-without-receipt "I" "B I I I"
STUB_RECEIPT_AT=none COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 8 ]] || fail "pasted line that becomes idle exit $RC (want 8)"
[[ ! -e $D/sent.3 ]] || fail "pasted line without receipt: resume prompt was sent"
scenario wrong-pane-receipt "I" "I"
STUB_RECEIPT_AT=1 STUB_RECEIPT_PANE=%8 COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 8 ]] || fail "receipt for another pane exit $RC (want 8)"
[[ ! -e $D/sent.3 ]] || fail "other pane receipt: resume prompt was sent"
# Spinner text on screen without a receipt is not confirmation, even when it disappears into idle.
scenario stale-spinner "I" "SPIN I I I"
stale_frame=$D/frames/compact.001
if [[ $(grep -cF "$RULE" "$stale_frame") -ne 2 ]] || ! grep -qF '❯' "$stale_frame"; then
  fail "stale spinner fixture must include a normal prompt box"
fi
STUB_RECEIPT_AT=none COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 8 ]] || fail "stale spinner text exit $RC (want 8)"
[[ ! -e $D/sent.3 ]] || fail "stale spinner text: resume prompt was sent"
# Chunk boundaries are <=400 Unicode characters, valid in the C locale, preserve `;`, and retain text order.
scenario chunked "I" "K K I I I"
{ printf '%0399d' 0; printf ';'; printf ' é%.0s' {1..410}; } >"$D/guidance.txt"
{ printf '/compact '; cat "$D/guidance.txt"; } >"$D/expected-start.txt"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "chunked text exit $RC"
cmp -s "$D/sent-start" "$D/expected-start.txt" || fail "chunked command/guidance changed text or lost a semicolon"
for raw in "$D"/raw.*; do
  perl -e 'local $/; my $s=<>; utf8::decode($s) or die "invalid UTF-8\n"; my $n=length($s); $n-- if $s =~ /\\;\z/; exit($n <= 400 && $s !~ /(?<!\\);\z/ ? 0 : 1)' "$raw" || fail "chunk exceeds 400 characters, splits UTF-8, or has an unescaped trailing semicolon: $raw"
done
# A semicolon-only chunk and a prompt ending in semicolon both arrive intact.
scenario semicolons "I" "K K I I I"
printf ';' >"$D/guidance.txt"
printf '/compact ;' >"$D/expected-start.txt"
printf 'resume;' >"$D/prompt.txt"
printf 'resume;' >"$D/expected-resume.txt"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "semicolon text exit $RC"
cmp -s "$D/sent-start" "$D/expected-start.txt" || fail "semicolon-only guidance was dropped"
cmp -s "$D/sent-compact" "$D/expected-resume.txt" || fail "prompt-ending semicolon was dropped"
cmp -s "$D/raw.2" <(printf '\\;') || fail "semicolon-only chunk was not escaped for tmux"
cmp -s "$D/raw.3" <(printf 'resume\\;') || fail "prompt-ending semicolon was not escaped for tmux"
# A literal backslash before the semicolon survives tmux's final-two-character handling.
scenario escaped-semicolon "I" "K K I I I"
printf 'literal\\;' >"$D/guidance.txt"
printf '/compact literal\\;' >"$D/expected-start.txt"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "escaped semicolon text exit $RC"
cmp -s "$D/sent-start" "$D/expected-start.txt" || fail "literal backslash-semicolon changed in tmux"
cmp -s "$D/raw.2" <(printf 'literal\\\\;') || fail "literal backslash-semicolon was not escaped once for tmux"
# Latin-1-range Unicode needs explicit UTF-8 encoding after Perl decodes the input.
scenario latin1 "I" "K K I I I"
printf 'éü§·' >"$D/guidance.txt"
printf '/compact éü§·' >"$D/expected-start.txt"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "Latin-1-range UTF-8 exit $RC"
cmp -s "$D/sent-start" "$D/expected-start.txt" || fail "Latin-1-range characters were not byte-identical"
# Guidance itself may mention Compacting; without a receipt it remains unconfirmed.
scenario guidance-mentions-spinner "I" "I"
printf 'Compacting is only text in this guidance.\n' >"$D/guidance.txt"
STUB_RECEIPT_AT=none COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 8 && ! -e $D/sent.3 ]] || fail "guidance text falsely confirmed compaction"
# An empty guidance file still submits the bare slash command.
scenario empty-guidance "I" "K K I I I"
: >"$D/guidance.txt"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "empty guidance exit $RC"
[[ $(calls | grep -c '^send-keys') -eq 4 ]] || fail "empty guidance must send bare command, Enter, prompt and Enter"
cmp -s "$D/sent.1" <(printf '/compact ') || fail "empty guidance did not send bare /compact"

# 5b. A failing send-keys exits 6, is not logged as sent and nothing more is sent (the happy path types a
#     resume prompt starting with "-", which the stub rejects without "--": the old code lost it silently).
scenario sendfail "I" "I"
touch "$D/sendfail"
run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 6 ]] || fail "send-keys failure exit $RC (want 6)"
contains "$D/log" "ERROR send-keys failed (text)" "send failure logged"
[[ ! -e $D/sent.1 ]] || fail "send failure: something was recorded as sent"
not_contains "$D/log" "compaction running" "send failure: went on waiting for a compaction"

# Invalid UTF-8 in guidance fails the chunker before Enter can submit /compact.
scenario invalid-guidance "I" "I"
printf '\377' >"$D/guidance.txt"
run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 6 ]] || fail "invalid guidance UTF-8 exit $RC (want 6)"
contains "$D/log" "ERROR UTF-8 chunker failed" "invalid guidance UTF-8 logged"
[[ $(calls | grep -c '^send-keys') -eq 1 ]] || fail "invalid guidance: Enter or more text was sent"
[[ $(<"$D/nsent") -eq 1 ]] || fail "invalid guidance: chunker sent text after its decode failed"

# Invalid UTF-8 in the resume prompt fails before send_text can submit it.
scenario invalid-prompt "I" "K K I I I"
printf '\377' >"$D/prompt.txt"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 6 ]] || fail "invalid prompt UTF-8 exit $RC (want 6)"
contains "$D/log" "ERROR UTF-8 chunker failed" "invalid prompt UTF-8 logged"
[[ $(<"$D/nsent") -eq 2 ]] || fail "invalid prompt: prompt text was sent"
[[ $(calls | grep -c 'send-keys %9 Enter') -eq 1 ]] || fail "invalid prompt: an Enter followed the failed chunker"

# 5c. Text in the input row: nothing is sent until it is empty again, before the /compact and before the resume
#     prompt (text typed DURING the compaction does not stall the compaction wait); the LAST input row counts
#     (an older non-empty one in the scrollback does not); NBSP-only is empty.
#     start: T T I I I + footer = 6; compact: I K K I T I I | T T I I I = 13.
scenario typing "T T I I I" "I K K I T I I T T I I I"
run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "typing exit $RC"
want=$( { echo "has-session %9"; rep 6 "capture start"; echo "send-keys %9 -l <text 1>"; echo "send-keys %9 -l <text 2>"; echo "send-keys %9 Enter"
          rep 13 "capture compact"; echo "send-keys %9 -l <text 3>"; echo "send-keys %9 Enter"; echo "capture compact"; } )
[[ $(calls) == "$want" ]] || { calls >&2; fail "typing: nothing may be sent before the input row is empty"; }
scenario scroll "S" "K I I I"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "older non-empty input row in the scrollback blocked the send (exit $RC)"
[[ $(calls | grep -c '^capture start') -eq 4 ]] || fail "scrollback row: expected 3 checks + the footer"

# 5d. The input row never empties: exit 5 and nothing is ever sent while it has text (checked from the recorded
#     send-keys calls), at the start, and again before the resume prompt (/compact sent, prompt not).
scenario typed "T" "I"
COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 5 ]] || fail "input never empty exit $RC (want 5)"
contains "$D/log" "ERROR input line still has text" "input never empty logged"
not_contains <(calls) "send-keys" "input never empty: keys were sent"
scenario typedlate "I" "K I I I T"
COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 5 ]] || fail "input never empty before the prompt exit $RC (want 5)"
[[ $(calls | grep -c '^send-keys') -eq 3 && ! -e $D/sent.3 ]] || { calls >&2; fail "input never empty before the prompt: only the command, guidance and Enter may be sent"; }

# 5e. A dim prompt suggestion in the input row (ESC[2m ... ESC[0m) is not typed text: the helper proceeds;
#     plain text after a coloured marker is: nothing is sent (the stub serves the escapes only for capture -e).
scenario dim "G" "K I I I"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "dim suggestion blocked the send (exit $RC)"
[[ $(calls | grep -c '^capture start') -eq 4 ]] || fail "dim suggestion: expected 3 checks + the footer"
scenario coloured "X" "I"
COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 5 ]] || fail "typed text after a coloured marker exit $RC (want 5)"
not_contains <(calls) "send-keys" "typed text after a coloured marker: /compact was sent"

# 5f. F4: an agents panel below the mode line never hides a running turn. With 1, 3, 6 or 12 appended panel rows the mode
#     line's `esc to interrupt` sits among the last 16 non-blank lines of the untrimmed capture; 3, 6 and 12 rows put it
#     OUTSIDE compact-resume.sh's 6-line FOOT_LINES window, while with 1 row it is the 5th non-blank line from the bottom,
#     already inside it - so at 91e9d65f3 wait_idle counted three "idle" polls for those three heights and typed
#     "/compact ..." into a running turn. L1B is kept for the trimmed view (it must stay busy through the box rule).
for kind in L1B L3B L6B L12B; do
  scenario "panel-$kind" "$kind" "I"
  COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
  [[ $RC -eq 3 ]] || fail "agents panel $kind: exit $RC (want 3, the pane is busy)"
  not_contains <(calls) "send-keys" "agents panel $kind: keys were sent into a running turn"
done
# 5g. The same busy frame in ANOTHER permission mode: its mode line is not `⏵⏵ bypass permissions on`, so pane_view
#     trims nothing and the busy check must still find the marker below the prompt box's top rule. At 91e9d65f3 this
#     read as idle as well.
scenario panel-othermode "LMB" "I"
COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "other permission mode: exit $RC (want 3, the pane is busy)"
not_contains <(calls) "send-keys" "other permission mode: keys were sent into a running turn"
# 5h. The REAL idle agents panel with the unfocused hint stays ready: the trim must not break the happy path.
scenario panel-idle "LI" "K I I I"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "idle agents panel exit $RC"
[[ $(calls | grep -c '^capture start') -eq 4 ]] || fail "idle agents panel: expected 3 idle checks + the footer"
cmp -s "$D/sent-start" "$D/expected-start.txt" || fail "idle agents panel: the /compact line was not sent exactly"
cmp -s "$D/sent-compact" "$D/expected-resume.txt" || fail "idle agents panel: the resume prompt was not sent exactly"

# 5i. M2: a pane the helper cannot POSITIVELY recognise as an idle prompt box is never typed into. These four are the
#     PIN the review found missing (they already read busy at 4708ed03e): delete the `st == 2` refusal in pane_state, or
#     let an empty capture return 1, and each reads READY and /compact + the prompt are typed into it. EMPTY = a failed or
#     empty capture; QMENU = a column-0 question menu (no `❯` cursor, no footer, no input row either); BANG = a bash-mode
#     `!` row, which is not the prompt box; NORULE = a `❯` row with a rule BELOW but none above.
for kind in EMPTY QMENU BANG NORULE; do
  scenario "refuse-$kind" "$kind" "I"
  COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
  [[ $RC -eq 3 ]] || fail "refusal $kind: exit $RC (want 3, not ready)"
  not_contains <(calls) "send-keys" "refusal $kind: keys were sent into a pane that was never recognised as idle"
done
# 5j. M1: a DRAFT with an empty first row reads READY at 4708ed03e, so /compact and the draft get submitted. With the
#     plain rule required BELOW the row it has no box. The reason and the footer must be logged ONCE (H1).
for kind in DRAFT DRAFT_FIX DRAFT_VERIFY; do
  scenario "refuse-$kind" "$kind" "I"
  COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
  [[ $RC -eq 3 ]] || fail "refusal $kind: exit $RC (want 3, a multi-line draft is not ready)"
  not_contains <(calls) "send-keys" "refusal $kind: keys were sent over a draft"
  contains "$D/log" "WARN pane not ready: no prompt box recognised" "refusal $kind: the reason was not logged"
  [[ $(grep -c 'WARN pane not ready' "$D/log") -eq 1 ]] || fail "refusal $kind: the reason must be logged ONCE per wait"
  contains "$D/log" "|  ctx 41%" "refusal $kind: the footer is missing from the logged reason"
done
# 5k. M1: the permission menu ABOVE an intact empty box reads READY at 4708ed03e and the Enter answers the menu; only
#     dialog_open stops it, and the helpers never ran that check before.
scenario refuse-MENUBOX "MENUBOX" "I"
COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "refusal MENUBOX: exit $RC (want 3, a dialog is open)"
not_contains <(calls) "send-keys" "refusal MENUBOX: keys were sent while a menu was open"
contains "$D/log" "WARN pane not ready: a dialog is open" "refusal MENUBOX: the dialog reason was not logged"
# 5l. H1: the LIVE main pane's labelled top rule is a box: the idle frame must be typed into (at 4708ed03e it is not: no
#     box is recognised, the pane reads busy at every poll, /compact is never sent). The same frame with `esc to interrupt`
#     on the mode line must stay busy (that case already reads busy on the base through the no-box scan: a pin, not a
#     regression).
scenario h1-idle "H1I" "K I I I"
COMPACT_RESUME_START_TRIES=3 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || fail "H1: the live main pane's labelled top rule must read ready (exit $RC)"
[[ $(calls | grep -c '^capture start') -eq 4 ]] || fail "H1: expected 3 idle checks + the footer on the labelled box"
cmp -s "$D/sent-start" "$D/expected-start.txt" || fail "H1: the /compact line was not sent exactly on the labelled box"
cmp -s "$D/sent-compact" "$D/expected-resume.txt" || fail "H1: the resume prompt was not sent exactly on the labelled box"
scenario h1-busy "H1B" "I"
COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
[[ $RC -eq 3 ]] || fail "H1 busy: exit $RC (want 3, the labelled box is busy)"
not_contains <(calls) "send-keys" "H1 busy: keys were sent into a running turn under the labelled box"
# 5m. L2: a recognized `✶ Compacting …` spinner above the box makes the pane busy, including on the sixth non-blank
#     line above its top rule (the SPINNER_SCAN_LINES boundary). Spinner refusals are logged only once per wait.
for kind in SPIN SPIN_EDGE; do
  scenario "l2-$kind" "$kind" "I"
  COMPACT_RESUME_IDLE_TRIES=5 run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log"
  [[ $RC -eq 3 ]] || fail "L2 $kind: exit $RC (want 3, a compaction is running above the box)"
  not_contains <(calls) "send-keys" "L2 $kind: keys were sent while a compaction was running above the box"
  [[ $(grep -c 'WARN pane not ready: compaction spinner above the prompt box' "$D/log") -eq 1 ]] || fail "L2 $kind: spinner reason was not logged exactly once"
done
# 6. Argument and precondition errors exit 2 and send nothing.
scenario args "I" "I"
run; [[ $RC -eq 2 ]] || fail "no arguments exit $RC"
contains "$D/stderr" "usage:" "usage line"
run '%9' "$D/guidance.txt" "$D/prompt.txt"; [[ $RC -eq 2 ]] || fail "three arguments exit $RC"
run '%9' "$D/missing.txt" "$D/prompt.txt" "$D/log"; [[ $RC -eq 2 ]] || fail "missing guidance exit $RC"
contains "$D/log" "ERROR guidance file unreadable" "missing guidance logged"
run '%9' "$D/guidance.txt" "$D/missing.txt" "$D/log"; [[ $RC -eq 2 ]] || fail "missing prompt exit $RC"
contains "$D/log" "ERROR prompt file unreadable" "missing prompt logged"
run '%9' "$D/guidance.txt" "$D/prompt.txt" "$ROOT/no-such-dir/log"; [[ $RC -eq 2 ]] || fail "unwritable log exit $RC"
contains "$D/stderr" "log file not writable" "unwritable log message"
[[ ! -e $D/calls.log ]] || fail "precondition errors reached tmux"
touch "$D/nosession"
run '%9' "$D/guidance.txt" "$D/prompt.txt" "$D/log2"; [[ $RC -eq 2 ]] || fail "missing pane exit $RC"
contains "$D/log2" "ERROR no pane %9" "missing pane logged"
[[ $(calls) == "has-session %9" ]] || fail "missing pane: only has-session expected"

echo 'PASS compact-resume'
