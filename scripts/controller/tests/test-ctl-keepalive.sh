#!/usr/bin/env bash
# shellcheck disable=SC2016 # fixture text carries literal $ and backticks
set -uo pipefail
# ctl-keepalive.sh --once against a STUB tmux first on PATH, fake ~/.claude/sessions + projects JSONL, a fake /proc and a
# fixed clock (KEEPALIVE_NOW). The stub records every call and serves capture-pane frames per phase (pre = before the
# ping was typed, typed = after send-keys -l), advancing one frame per call (the last repeats). No real tmux, no sleep.
#
# The synthetic frames cover the dialog cursor/footer, dim suggestions, drafts and read-back states. The sanitized real
# captures in fixtures/keepalive cover the idle box with an IDE-switch notice and an agents panel below the mode line.
for v in $(compgen -v | grep -E '^(KEEPALIVE_|KA_RESUME_|CLEAR_RESUME_|COMPACT_RESUME_)'); do unset "$v"; done
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
KA=${KEEPALIVE_SCRIPT:-$SCRIPT_DIR/../ctl-keepalive.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-keepalive-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }
T0=1800000000   # the fixed "now"
TEXT='[keepalive] cache refresh only: reply ok, no tools'
RULE=$(printf '─%.0s' {1..40})

mkdir -p "$ROOT/bin"
cat > "$ROOT/bin/tmux" <<'SH'
#!/usr/bin/env bash
s=$STUB_DIR
case $1 in
  has-session) echo "has-session $3" >>"$s/calls.log"; exit 0 ;;
  display)   # display -p -t <target> <format>; the target is a pane id or a session name from panes.txt (id session width)
    echo "display $4" >>"$s/calls.log"
    line=$(awk -v t="$4" '$1 == t || $2 == t { print; exit }' "$s/panes.txt"); [[ -n $line ]] || exit 1
    read -r id sess w <<<"$line"
    printf '%s\n' "$5" | sed -e "s/#{pane_id}/$id/g" -e "s/#{session_name}/$sess/g" -e "s/#{pane_width}/$w/g" ;;
  capture-pane)
    phase=$(<"$s/phase"); k=$(( $(cat "$s/idx.$phase" 2>/dev/null || echo 0) + 1 )); echo "$k" >"$s/idx.$phase"
    f=$(printf '%s/frames/%s.%03d' "$s" "$phase" "$k")
    [[ -e $f ]] || f=$(printf '%s\n' "$s/frames/$phase".* | sort | tail -1)
    echo "capture $phase" >>"$s/calls.log"
    if [[ " $* " == *" -e "* ]]; then cat -- "$f"; else sed 's/\x1b\[[0-9;]*m//g' "$f"; fi ;;
  send-keys)   # send-keys -t <pane> -l -- <text> | -t <pane> C-u | -t <pane> Enter | -t <pane> /clear Enter
    if [[ $4 == -l ]]; then
      printf '%s' "$6" >"$s/sent.txt"; echo "send-keys $3 -l <text>" >>"$s/calls.log"; echo typed >"$s/phase"
    else echo "send-keys $3 $4${5:+ $5}" >>"$s/calls.log"; fi ;;
esac
exit 0
SH
chmod +x "$ROOT/bin/tmux"

# box <row> [footer]: an idle prompt box, `%b` expands \xc2\xa0 (NBSP) and \e escapes in the row
box() { printf 'output\n%s\n%b\n%s\n%b\n' "$RULE" "$1" "$RULE" "${2:-  ctx 41%}"; }
BUSYL='  * Thinking (12s . esc to interrupt)'
MENU=' Do you want to proceed?\n ❯ 1. Yes\n   2. No\n\n Esc to cancel'
PANELFIX=$SCRIPT_DIR/fixtures/keepalive/idle-agents-panel.ansi                     # the sanitized REAL idle agents-panel capture
# panel_frame <dest> <extra panel rows> <busy 0|1> <unfocused hint 0|1>: a temp copy of the REAL capture, edited line by line:
# `· esc to interrupt` added to its mode line when busy, ` · ↓ to manage` removed when the panel has focus, N rows appended.
panel_frame() {
  local dest=$1 rows=$2 busy=$3 hint=$4 k mode_line
  mode_line=$(grep -nFm1 -- '⏵⏵ bypass permissions on' "$PANELFIX" | cut -d: -f1)
  local esc=$'\033'
  cp -- "$PANELFIX" "$dest"
  # append the busy marker at the END of the mode line's text: the line is captured with colour runs, and appending is what
  # a real busy pane shows (the mode line grows, the rest of the layout does not move)
  if (( busy )); then sed -i "${mode_line}s/${esc}\\[39m$/ · esc to interrupt${esc}\\[39m/" "$dest"; fi
  if (( ! hint )); then sed -i "${mode_line}s/ · ↓ to manage//" "$dest"; fi
  for ((k = 1; k <= rows; k++)); do printf '  \e[38;5;246m◯ general-purpose\e[39m  \e[38;5;246magent %s running a foreground tool\e[39m\n' "$k" >>"$dest"; done
}
# typed_row <dest> <text>: the read-back shape: the capture's EMPTY input row now holds the text
typed_row() { sed -i "s/❯$(printf '\xc2\xa0')/❯ $2/" "$1"; }
# draft_row <dest>: a continuation line right under the live input row (a multi-line draft)
draft_row() { perl -i -pe 'if (!$d && /\xe2\x9d\xaf\xc2\xa0/) { $_ .= "  foo\n"; $d = 1 }' "$1"; }
# menu_frame <dest> <extra panel rows>: an MB layout, built from the REAL capture's last section: the permission menu above
# the capture's own top box rule, its empty box, its mode line and its panel plus N extra rows
menu_frame() {
  local dest=$1 rows=$2 first k
  first=$(grep -nFm1 -- '❯' "$PANELFIX" | cut -d: -f1)   # -m1: grep stops itself, no `| head` SIGPIPE under pipefail
  { printf '%b\n' "$MENU"; tail -n "+$((first - 1))" "$PANELFIX"; for ((k = 1; k <= rows; k++)); do printf '  \e[38;5;246m◯ general-purpose\e[39m  \e[38;5;246magent %s running a foreground tool\e[39m\n' "$k"; done; } >"$dest"
}
# frame <dir> <phase> <n> <kind>
frame() {
  local f; f=$(printf '%s/frames/%s.%03d' "$1" "$2" "$3")
  case $4 in
    I) box '❯\xc2\xa0' >"$f" ;;                                           # idle, empty (NBSP after the marker)
    G) box '❯\xc2\xa0\e[2mrun the tests\e[0m' >"$f" ;;                    # idle, dim prompt suggestion = empty
    T) box '❯ half typed' >"$f" ;;                                        # the operator is typing
    O) { printf '❯ older prompt\nreply\n'; box '❯\xc2\xa0'; } >"$f" ;;    # older non-empty row in the scrollback, live row empty
    B) { printf '%s\n' "$BUSYL"; box '❯\xc2\xa0'; } >"$f" ;;             # working
    M) printf '%b\n' "$MENU" >"$f" ;;                                          # numbered permission menu, no prompt box
    MB) { printf '%b\n' "$MENU"; box '❯\xc2\xa0'; } >"$f" ;;                   # menu AND an empty prompt box (only the dialog check stops it)
    NB) printf 'output\n❯\xc2\xa0\n\n  ctx 41%%\n' >"$f" ;;               # no box rule above the input row
    FAR) { printf '%s\n❯\xc2\xa0\n%s\n' "$RULE" "$RULE"; printf 'line %s\n' a b c d e f g h; } >"$f" ;;   # row far above the bottom
    ROK) box "❯\xc2\xa0$TEXT" >"$f" ;;                                    # read-back: exactly our text, NBSP as the separator
    ROK2) box "❯ $TEXT" >"$f" ;;                                          # ... plain space
    ROK3) box "❯\xc2\xa0\xc2\xa0$TEXT\xc2\xa0 " >"$f" ;;                  # ... NBSP gap doubled, trailing NBSP
    LID) panel_frame "$f" 3 0 1 ;;                                        # the REAL idle agents panel plus 3 rows: pingable
    LB) panel_frame "$f" 6 1 1 ;;                                         # H1: real frame, `esc to interrupt` in the mode line, 9 panel rows
    LRB) panel_frame "$f" 6 1 1; typed_row "$f" "$TEXT" ;;                # H1: the read-back of the ping on such a pane
    LMB) menu_frame "$f" 12 ;;                                            # H1: a menu above the box, then the real mode line, then a 15-row panel
    LMLP) panel_frame "$f" 6 0 1; draft_row "$f" ;;                       # H1: a draft line under the live row, mode line and panel below
    LFOC) panel_frame "$f" 0 0 0 ;;                                       # M1: the real frame WITHOUT the unfocused-panel hint (focused panel)
    RPF) box '❯ [keepalive] cache ref' >"$f" ;;                           # only a prefix of our text
    RFO) box "❯ hello $TEXT" >"$f" ;;                                     # foreign text typed by the operator
    RBUSY) { printf '%s\n' "$BUSYL"; box "❯ $TEXT"; } >"$f" ;;            # a turn started: our text, busy
    RBUSYFO) { printf '%s\n' "$BUSYL"; box "❯ hello $TEXT"; } >"$f" ;;    # busy and foreign text
    MLP) box '❯\xc2\xa0\n  foo' >"$f" ;;                                    # multi-line draft, empty first line (continuation lines have no marker)
    RML1) box "❯\xc2\xa0\n  foo$TEXT" >"$f" ;;                             # read-back: the ping landed at the end of the draft line
    RML2) box "❯ $TEXT\n  foo" >"$f" ;;                                      # read-back: the cursor was on the marker row
    SG) printf 'output\n%s\n\e[0m\n❯\xc2\xa0\n%s\n  ctx 41%%\n' "$RULE" "$RULE" >"$f" ;;   # a codes-only line between rule and row
    RDLG) { printf '%b\n' "$MENU"; box "❯ $TEXT"; } >"$f" ;;                   # a menu opened, our text in the box
  esac
}
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%S.000Z; }
a_rec() { printf '{"type":"assistant","timestamp":"%s","message":{"usage":{"input_tokens":5,"cache_read_input_tokens":%s,"cache_creation_input_tokens":0}}}\n' "$(iso "$1")" "${2:-100000}" >>"$J"; }
u_rec() { printf '{"type":"user","timestamp":"%s","message":{"role":"user","content":"%s"}}\n' "$(iso "$1")" "$2" >>"$J"; }
t_rec() { printf '{"type":"user","timestamp":"%s","message":{"role":"user","content":[{"type":"tool_result","content":"x"}]}}\n' "$(iso "$1")" >>"$J"; }
s_rec() { printf '{"type":"assistant","isApiErrorMessage":true,"timestamp":"%s","message":{"model":"<synthetic>","usage":{"input_tokens":0,"output_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}\n' "$(iso "$1")" >>"$J"; }   # Claude Code's own API-error record
x_rec() { printf '{"type":"api-request","timestamp":"%s","id":"r"}\n' "$(iso "$1")" >>"$J"; }
# mk_session <pid> <name> <sid> <tmux field> [cwd]: the session file, a live fake process and the (empty) JSONL, mtime = now
mk_session() {
  local cwd=${5:-/workspaces/vbpub} pid=$1
  printf '{"pid":%s,"procStart":"%s","sessionId":"%s","name":"%s","cwd":"%s","tmux":"%s","status":"idle"}\n' \
    "$pid" "9$pid" "$3" "$2" "$cwd" "$4" >"$D/sessions/$pid.json"
  mkdir -p "$D/proc/$pid"; echo claude >"$D/proc/$pid/comm"
  printf '%s (claude) S 1 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 9%s 0 0\n' "$pid" "$pid" >"$D/proc/$pid/stat"
  J=$D/projects/${cwd//\//-}/$3.jsonl; mkdir -p "$(dirname "$J")"; : >"$J"
}
SID=aaaaaaaa-1111-2222-3333-444444444444
# scenario <name> <anchor age s> <pre frames> <typed frames>: one live session vbpub-platform in pane %1, last request <age> s ago
scenario() {
  D=$ROOT/$1; mkdir -p "$D/frames" "$D/sessions" "$D/projects" "$D/proc" "$D/home"; echo pre >"$D/phase"
  echo "%1 main 120" >"$D/panes.txt"
  mk_session 1001 vbpub-platform "$SID" 'main:@1.%1'
  u_rec $((T0 - $2 - 60)) 'real prompt'; a_rec $((T0 - $2))
  local i=0 k a; read -ra a <<<"$3"; for k in "${a[@]}"; do frame "$D" pre $((++i)) "$k"; done
  i=0; read -ra a <<<"$4"; for k in "${a[@]}"; do frame "$D" typed $((++i)) "$k"; done
  touch -d "@$T0" "$J"
}
prep() { echo pre >"$D/phase"; rm -f "$D"/idx.*; find "$D/projects" -name '*.jsonl' -exec touch -d "@$T0" {} +; }   # pre-typing frames, the JSONL is "now"
run() { # the watcher once; sets RC
  prep
  STUB_DIR=$D PATH=$ROOT/bin:$PATH KEEPALIVE_HOME=$D/home KEEPALIVE_SESSIONS_DIR=$D/sessions KEEPALIVE_PROJECTS_DIR=$D/projects \
    KEEPALIVE_PROC=$D/proc KEEPALIVE_NOW=$T0 KEEPALIVE_READBACK_WAIT=0.01 bash "$KA" --once 2>"$D/stderr"; RC=$?
}
calls() { cat -- "$D/calls.log" 2>/dev/null; }
klog() { cat -- "$D/home/logs/keepalive.log"; }
# no `cmd | grep -q` under pipefail: grep -q may close the pipe early (SIGPIPE, rc 141)
called() { grep -qF -- "$1" "$D/calls.log" 2>/dev/null; }
logged() { grep -qF -- "$1" "$D/home/logs/keepalive.log" 2>/dev/null; }
sent_keys() { calls | grep -c '^send-keys'; }
capture_functions() { # $1 = sanitized real capture; prints "<pane_state rc> <box_ok rc> <dialog_open rc>" of the TRIMMED view
  KEEPALIVE_SOURCE_ONLY=1 bash -c '
    set -uo pipefail
    source "$1"
    capture=$(<"$2")
    capture=$(pane_view "$capture")
    pane_state_of "$capture"; pane=$?
    box_ok "$capture"; box=$?
    dialog_open "$capture"; dialog=$?
    printf "%s %s %s\n" "$pane" "$box" "$dialog"
  ' _ "$KA" "$1"
}
# 1. the happy path; the text has no digit; the log line; the pid file is gone; Enter only after the read-back
scenario happy 3400 "I" "ROK"
run; [[ $RC -eq 0 ]] || fail "happy path exit $RC"
want=$(printf '%s\n' "display %1" "capture pre" "send-keys %1 -l <text>" "capture typed" "send-keys %1 Enter")
[[ $(calls) == "$want" ]] || { calls >&2; fail "happy path call sequence"; }
[[ $(<"$D/sent.txt") == "$TEXT" ]] || fail "the ping text"
[[ $(<"$D/sent.txt") != *[0-9]* ]] || fail "R2-2: the ping text must contain no digit (they select menu options)"
[[ $(<"$D/sent.txt") == '['* ]] || fail "the ping must start with ["
logged ' vbpub-platform aaaaaaaa ping 1/3 anchor=' || { klog >&2; fail "ping logged with N/MAX"; }
[[ ! -e $D/home/keepalive.pid ]] || fail "pid file removed on exit"
# 1b. R2-1: the read-back matches with NBSP or a plain space as the gap after the marker, doubled gaps and trailing NBSP
for kind in ROK ROK2 ROK3; do
  scenario "nbsp-$kind" 3400 "I" "$kind"; run
  [[ $(calls | grep -c 'send-keys %1 Enter') -eq 1 ]] || { calls >&2; fail "R2-1: read-back $kind did not reach Enter"; }
done
# 1c. the dim suggestion and an older non-empty row in the scrollback are an empty prompt; both ping
for kind in G O; do scenario "dim-$kind" 3400 "$kind" "ROK"; run; called 'send-keys %1 Enter' || fail "pane $kind must be pingable"; done
# 1d. both sanitized real captures are ready, their prompt box is intact, and no dialog is visible
for capture in idle-ide-notice.ansi idle-agents-panel.ansi; do
  result=$(capture_functions "$SCRIPT_DIR/fixtures/keepalive/$capture") || fail "cannot source the watcher for $capture"
  [[ $result == '0 0 1' ]] || fail "$capture: pane_state_of/box_ok/dialog_open returned $result, wanted 0 0 1"
  echo "PASS real capture $capture: pane_state_of=0 box_ok=true dialog_open=false"
done
# 1e. H1 on the REAL frame: the trimmed view keeps the real idle agents panel pingable (its panel rows are not the box).
scenario h1-real-panel 3400 "LID" "ROK"; run; called 'send-keys %1 Enter' || fail "the real idle agents panel must stay pingable"

# 2. R2-2: the read-back re-runs the busy and dialog checks
scenario rb-busy 3400 "I" "RBUSY"; run
called 'send-keys %1 C-u' || fail "R2-2: busy read-back with our text must send C-u"
! called 'send-keys %1 Enter' || fail "R2-2: Enter was sent into a busy pane"
logged 'abort-busy' || fail "abort-busy logged"
[[ $(<"$D/home/keepalive/$SID.abort") == $((T0 - 3400)) ]] || fail "abort file holds the anchor"
run; [[ $(calls | grep -c '^send-keys') -eq 2 ]] || fail "the aborted session is skipped while its anchor is unchanged"   # -l and C-u of the first run only
logged 'skip aborted-for-this-anchor' || fail "skip reason logged"
a_rec $((T0 - 3350)); run; [[ $(calls | grep -c 'send-keys %1 -l') -eq 2 ]] || fail "a changed anchor must be retried"
scenario rb-busy-foreign 3400 "I" "RBUSYFO"; run
[[ $(sent_keys) -eq 1 ]] || { calls >&2; fail "busy with foreign text: nothing but the typing may be sent (operator text is never erased)"; }
logged 'abort-foreign' || fail "abort-foreign logged for busy+foreign"
scenario rb-dialog 3400 "I" "RDLG"; run
[[ $(sent_keys) -eq 1 ]] || { calls >&2; fail "R2-2: a dialog during the read-back: nothing more may be sent"; }
logged 'abort-foreign dialog' || fail "dialog abort logged"
[[ -e $D/home/keepalive/$SID.abort ]] || fail "dialog abort writes the abort file"

# 3. read-back aborts: foreign text -> nothing more; our own partial typing -> C-u
scenario rb-foreign 3400 "I" "RFO"; run
[[ $(sent_keys) -eq 1 ]] || fail "foreign text: nothing more may be sent"
logged 'abort-foreign' || fail "abort-foreign logged"; [[ -e $D/home/keepalive/$SID.abort ]] || fail "abort file (foreign)"
scenario rb-prefix 3400 "I" "RPF"; run
called 'send-keys %1 C-u' || fail "partial typing must be cleared with C-u"
! called 'send-keys %1 Enter' || fail "no Enter after a partial read-back"
logged 'abort-cleared' || fail "abort-cleared logged"; [[ -e $D/home/keepalive/$SID.abort ]] || fail "abort file (cleared)"

# 4. pane checks before typing: nothing at all is typed
for kind in T B M MB NB FAR MLP; do
  scenario "pane-$kind" 3400 "$kind" "ROK"; run
  [[ $(sent_keys) -eq 0 ]] || { calls >&2; fail "pane $kind must not be pinged"; }
done
scenario pane-narrow 3400 "I" "ROK"; echo "%1 main 70" >"$D/panes.txt"; run
[[ $(sent_keys) -eq 0 ]] || fail "a pane narrower than 80 columns must not be pinged"; logged 'pane-narrow (70)' || fail "narrow logged"

# 4a. H1: on a frame built from the REAL agents-panel capture the trim is the only reason a busy pane whose mode line sits
#     above a tall panel is recognised. Each case would type (and submit) the ping at 3539714d1.
scenario h1-busy-panel 3400 "LB" "ROK"; run
[[ $(sent_keys) -eq 0 ]] || { calls >&2; fail "H1: a busy pane with 9 panel rows must not be pinged (esc to interrupt is 10 lines up)"; }
logged 'skip busy' || fail "H1: the pane must be skipped as busy, not as an unknown box layout"
scenario h1-readback 3400 "LID" "LRB"; run
called 'send-keys %1 C-u' || { calls >&2; fail "H1: a busy read-back over a panel must be cleared with C-u"; }
! called 'send-keys %1 Enter' || fail "H1: Enter was sent into a busy pane whose mode line sits above a panel"
logged 'abort-busy' || fail "H1: abort-busy logged for the panel read-back"
scenario h1-dialog 3400 "LMB" "ROK"; run
[[ $(sent_keys) -eq 0 ]] || { calls >&2; fail "H1: a menu above the box, a mode line and a 15-row panel must not be pinged"; }
logged 'skip dialog' || fail "H1: the dialog must be found below a tall panel too"
scenario h1-draft 3400 "LMLP" "ROK"; run
[[ $(sent_keys) -eq 0 ]] || { calls >&2; fail "H1: a draft line under the live row must never be typed into"; }
logged 'skip input-box-layout' || fail "H1: a draft is refused by the box geometry"
# 4a2. M1: the trim needs the mode line's unfocused-panel hint (`↓ to manage`); without it the panel may hold the
#      keyboard focus and the strict box geometry must refuse the pane (nothing is typed into a focused panel)
scenario m1-focused 3400 "LFOC" "ROK"; run
[[ $(sent_keys) -eq 0 ]] || { calls >&2; fail "M1: a panel without the unfocused hint may be focused and must not be pinged"; }
logged 'skip input-box-layout' || fail "M1: the focused-panel frame is refused by the box geometry"
scenario m1-unfocused 3400 "LID" "ROK"; run
called 'send-keys %1 Enter' || { calls >&2; fail "M1: the unfocused agents panel (hint present) stays pingable"; }

# 4b. M1: a multi-line draft (box row empty, continuation line below it) is never typed into, and never erased or submitted
#     when it only shows on the read-back (cursor on the draft line or on the marker row)
for kind in RML1 RML2; do
  scenario "ml-$kind" 3400 "I" "$kind"; run
  [[ $(sent_keys) -eq 1 ]] || { calls >&2; fail "M1: read-back $kind: nothing but the typing may be sent (no C-u, no Enter)"; }
  logged 'prompt box layout changed' || fail "M1: read-back $kind: abort logged"
done
# 4c. L4: a line that holds only colour codes between the rule and the row does not hide the box
scenario sgr 3400 "SG" "ROK"; run; called 'send-keys %1 Enter' || fail "L4: a codes-only line must be blank before the box check"

# 5. timing, context, cap and the stateless count
scenario young 3000 "I" "ROK"; run; [[ ! -e $D/calls.log ]] || fail "anchor 50 min old: no tmux call at all"
scenario old 3600 "I" "ROK"; run; [[ ! -e $D/calls.log ]] || fail "anchor 60 min old: the cache is gone, nothing sent"
scenario bigctx 3400 "I" "ROK"; a_rec $((T0 - 3400)) 300000; run
[[ ! -e $D/calls.log ]] || fail "context >= KEEPALIVE_CTX_MAX must not be kept warm"
logged 'skip context 300005 >= 300000' || fail "context skip logged"
# a request that is only in flight (api-request, no reply yet) is not the anchor: it is the assistant record that counts
scenario apireq 3400 "I" "ROK"; x_rec $((T0 - 60)); run; called 'send-keys %1 Enter' || fail "R2-5: the anchor is the newest assistant record, not an api-request"
# N from the JSONL: ping text never changes, the log carries N/MAX
scenario n2 3400 "I" "ROK"
: >"$J"; u_rec $((T0 - 8000)) 'real'; a_rec $((T0 - 7900)); u_rec $((T0 - 7000)) '[keepalive] x'; a_rec $((T0 - 6990)); t_rec $((T0 - 6980))
u_rec $((T0 - 3500)) '[keepalive] x'; a_rec $((T0 - 3400)); run
logged 'ping 3/3' || { klog >&2; fail "two earlier pings (a tool result between them does not reset): this is 3/3"; }
scenario cap 3400 "I" "ROK"
: >"$J"; u_rec $((T0 - 9000)) 'real'; a_rec $((T0 - 8900))
for d in 7000 5000 3500; do u_rec $((T0 - d)) '[keepalive] x'; a_rec $((T0 - d + 10)); done
a_rec $((T0 - 3400)); run; [[ ! -e $D/calls.log ]] || fail "KEEPALIVE_MAX (default 3) reached: no ping, restarting changes nothing"
logged 'skip cap 3/3' || { klog >&2; fail "cap logged"; }
scenario reset 3400 "I" "ROK"
: >"$J"; for d in 9000 8000 7000; do u_rec $((T0 - d)) '[keepalive] x'; a_rec $((T0 - d + 5)); done
u_rec $((T0 - 4000)) '<task-notification>done</task-notification>'; a_rec $((T0 - 3400)); run
logged 'ping 1/3' || { klog >&2; fail "a task notification or a real prompt ends the count"; }
# M2: after a failed ping Claude Code writes a synthetic API-error assistant record with all-zero usage: it is no request
scenario synth 3400 "I" "ROK"; u_rec $((T0 - 3390)) '[keepalive] x'; s_rec $((T0 - 3380)); run
[[ ! -e $D/calls.log ]] || fail "M2: a synthetic error record must not move the anchor (one ping per anchor)"
logged 'skip one-ping-per-anchor' || fail "M2: one-ping-per-anchor logged"
scenario synthctx 3400 "I" "ROK"; a_rec $((T0 - 3399)) 300000; s_rec $((T0 - 3300)); run
[[ ! -e $D/calls.log ]] || fail "M2: a synthetic error record must not reset ctx to 0 (the 300k guard holds)"
logged 'skip context 300005' || fail "M2: context skip logged"
# L2: policy constants are not environment-overridable
scenario envmax 3400 "I" "ROK"
: >"$J"; u_rec $((T0 - 8000)) 'real'; a_rec $((T0 - 7900)); u_rec $((T0 - 7000)) '[keepalive] x'; a_rec $((T0 - 6990))
u_rec $((T0 - 3500)) '[keepalive] x'; a_rec $((T0 - 3400)); KEEPALIVE_MAX=1 KEEPALIVE_NAMES=nobody run
logged 'ping 3/3' || { klog >&2; fail "L2: an inherited KEEPALIVE_MAX / KEEPALIVE_NAMES must change nothing"; }
# R2-5: a ping that failed (no reply: the anchor stays) is not followed by another within the same window
scenario failed 3400 "I" "ROK"; u_rec $((T0 - 3390)) '[keepalive] x'; run
[[ ! -e $D/calls.log ]] || fail "R2-5: at most one ping per anchor"
logged 'skip one-ping-per-anchor' || fail "one-ping-per-anchor logged"

# 6. the session mapping: every guard skips without touching tmux
scenario dead 3400 "I" "ROK"; rm -rf "$D/proc/1001"; run; [[ ! -e $D/calls.log ]] || fail "dead pid"
scenario empty-window 3000 "I" "ROK"; rm -rf "$D/proc/1001"; run
[[ $RC -eq 0 && ! -e $D/calls.log ]] || fail "an empty window with only a dead pid record exits 0 and sends nothing"
logged 'dead-or-not-claude' || fail "the dead pid record was not skipped"
scenario comm 3400 "I" "ROK"; echo bash >"$D/proc/1001/comm"; run; [[ ! -e $D/calls.log ]] || fail "comm is not claude"
scenario reuse 3400 "I" "ROK"; sed -i 's/ 0 1 0 91001 / 0 1 0 5 /' "$D/proc/1001/stat"; run; [[ ! -e $D/calls.log ]] || fail "procStart mismatch (pid reuse)"
logged 'pid-reused' || fail "pid reuse logged"
scenario other 3400 "I" "ROK"; sed -i 's/vbpub-platform/some-other/' "$D/sessions/1001.json"; run; [[ ! -e $D/calls.log ]] || fail "unlisted name"
scenario dup 3400 "I" "ROK"; mk_session 1002 vbpub-platform bbbbbbbb-1111-2222-3333-444444444444 'main:@1.%2'
echo "%2 main 120" >>"$D/panes.txt"; run; [[ ! -e $D/calls.log ]] || fail "two live files with one name: both skipped"
logged 'duplicate-name' || fail "duplicate logged"
scenario stale 3400 "I" "ROK"; mk_session 1002 vbpub-platform bbbbbbbb-1111-2222-3333-444444444444 'main:@1.%2'; rm -rf "$D/proc/1002"
run; called 'send-keys %1 Enter' || fail "a stale (dead) duplicate does not block the live session"
scenario mismatch 3400 "I" "ROK"; echo "%1 elsewhere 120" >"$D/panes.txt"; run; [[ $(sent_keys) -eq 0 ]] || fail "reused pane id in another tmux session"
logged 'pane-mismatch' || fail "pane mismatch logged"
scenario notmux 3400 "I" "ROK"; sed -i 's/"tmux":"main:@1.%1"/"tmux":""/' "$D/sessions/1001.json"; run; [[ ! -e $D/calls.log ]] || fail "no tmux field"
scenario nojsonl 3400 "I" "ROK"; rm -f "$J"; run; [[ ! -e $D/calls.log ]] || fail "JSONL missing"
scenario cwd 3400 "I" "ROK"; mv "$J" "$D/projects/-tmp-x-$SID.jsonl"; run; [[ ! -e $D/calls.log ]] || fail "the JSONL path is derived from the cwd"

# 7. locks: R2-3, the pane lock is keyed on the resolved %pane id and shared with both resume helpers; the singleton lock
hold() { # <lockfile> <seconds>: hold the lock in the background until the ready file appears, then keep it for <seconds>
  rm -f -- "$D/ready"; ( flock 9; : >"$D/ready"; sleep "$2" ) 9>"$1" & HOLD=$!
  local i; for i in $(seq 200); do [[ -e $D/ready ]] && return 0; sleep 0.05; done; fail "lock holder did not start"
}
scenario lock 3400 "I" "ROK"; mkdir -p "$D/home/locks"; hold "$D/home/locks/%1.lock" 2; run; wait "$HOLD"
[[ $(sent_keys) -eq 0 ]] || fail "a held pane lock: the watcher must skip"; logged 'skip lock-busy' || fail "lock-busy logged"
run; called 'send-keys %1 Enter' || fail "the lock is released after the ping (and a second run still works)"
scenario single 3400 "I" "ROK"; mkdir -p "$D/home/locks"; hold "$D/home/locks/keepalive.lock" 2; run; wait "$HOLD"
[[ $RC -eq 0 ]] || fail "second instance exits 0"; contains "$D/stderr" "already running" "singleton message"
[[ ! -e $D/calls.log && ! -e $D/home/keepalive.pid ]] || fail "second instance must neither poll nor write the pid file"
# the resume helpers resolve a session-name target to the same %pane id and wait for the watcher's lock (exit 7)
helper() { # <script> <args...> with the stub and a short lock wait; sets RC
  STUB_DIR=$D PATH=$ROOT/bin:$PATH CLEAR_RESUME_LOCK_DIR=$D/home/locks COMPACT_RESUME_LOCK_DIR=$D/home/locks \
    CLEAR_RESUME_LOCK_WAIT=0.2 COMPACT_RESUME_LOCK_WAIT=0.2 CLEAR_RESUME_POLL=0.01 CLEAR_RESUME_TYPE_PAUSE=0.01 CLEAR_RESUME_FINAL_WAIT=0.01 \
    CLEAR_RESUME_CLEAR_TRIES=1 COMPACT_RESUME_POLL=0.01 COMPACT_RESUME_TYPE_PAUSE=0.01 COMPACT_RESUME_FINAL_WAIT=0.01 COMPACT_RESUME_SETTLE=0.01 \
    COMPACT_RESUME_START_TRIES=1 bash "$@" 2>"$D/stderr"; RC=$?
}
scenario lockhelp 3400 "I I I I I I I I I I I I" "I"; echo "prompt" >"$D/prompt.txt"; echo "guide" >"$D/guide.txt"; mkdir -p "$D/home/locks"
hold "$D/home/locks/%1.lock" 2
helper "$SCRIPT_DIR/../clear-resume.sh" main "$D/prompt.txt" "$D/log"; wait "$HOLD"
[[ $RC -eq 7 ]] || fail "R2-3: clear-resume on the session-name target must wait on the %1 lock and exit 7, got $RC"
[[ $(sent_keys) -eq 0 ]] || fail "clear-resume sent keys with the lock held"; contains "$D/log" "ERROR pane %1 is locked" "lock refusal logged"
scenario lockhelp2 3400 "I I I I I I I I I I I I" "I"; echo "prompt" >"$D/prompt.txt"; echo "guide" >"$D/guide.txt"; mkdir -p "$D/home/locks"
hold "$D/home/locks/%1.lock" 2
helper "$SCRIPT_DIR/../compact-resume.sh" main "$D/guide.txt" "$D/prompt.txt" "$D/log"; wait "$HOLD"
[[ $RC -eq 7 ]] || fail "R2-3: compact-resume on the session-name target must exit 7 while %1 is locked, got $RC"
scenario helpok 3400 "I I I I I I I I I I I I" "I"; echo "prompt" >"$D/prompt.txt"; mkdir -p "$D/home/locks"
helper "$SCRIPT_DIR/../clear-resume.sh" main "$D/prompt.txt" "$D/log"
[[ $RC -eq 0 ]] || { cat "$D/log" >&2; fail "clear-resume with a free lock exit $RC"; }
called 'send-keys %1 /clear Enter' || { calls >&2; fail "the helper must send to the resolved pane id %1, not the session name"; }
! called 'send-keys main' || fail "the helper sent to the unresolved target"
[[ -e $D/home/locks/%1.lock ]] || fail "the helper's lock file is keyed on the resolved pane id"

# 7a. N1 without a signal: a stub poll_once sets stop during the poll, and the watch loop must not enter the sleep.
# L1: the stub FAILS (exit 43) on a second poll, which it runs in the PARENT shell, so a regression that drops both `stop`
# checks reports FAIL instead of hanging the gate until the timeout.
scenario stop-after-poll 3400 "I" "ROK"; prep
KEEPALIVE_SOURCE_ONLY=1 SLEEP_MARKER=$D/slept bash -c '
  source "$1"
  stop=0; polls=0
  poll_once() { (( ++polls > 1 )) && exit 43; stop=1; }
  sleep() { printf called >"$SLEEP_MARKER"; exit 42; }
  watch_loop
' _ "$KA" || fail "the watch loop did not end immediately after stop was set"
[[ ! -e $D/slept ]] || fail "the watch loop entered the poll sleep after stop was set"
# 7b. L1: a SIGTERM during the 1-s read-back does not leave the typed ping behind: the ping is completed first
scenario term 3400 "I" "ROK"; prep
STUB_DIR=$D PATH=$ROOT/bin:$PATH KEEPALIVE_HOME=$D/home KEEPALIVE_SESSIONS_DIR=$D/sessions KEEPALIVE_PROJECTS_DIR=$D/projects \
  KEEPALIVE_PROC=$D/proc KEEPALIVE_NOW=$T0 KEEPALIVE_READBACK_WAIT=1 bash "$KA" --once 2>"$D/stderr" & TERMPID=$!
for _ in $(seq 100); do [[ -e $D/sent.txt ]] && break; sleep 0.05; done
[[ -e $D/sent.txt ]] || fail "the watcher never typed the ping"
kill -TERM "$TERMPID"; wait "$TERMPID"; RC=$?
[[ $RC -eq 0 ]] || fail "SIGTERM exit $RC"
called 'send-keys %1 Enter' || { calls >&2; fail "L1: the read-back must finish (Enter) before a SIGTERM ends the watcher"; }
[[ ! -e $D/home/keepalive.pid ]] || fail "pid file removed after SIGTERM"
# 7c. L5: a target tmux cannot resolve to a pane id exits 2, nothing sent (no silent fallback to the unresolved name)
scenario noresolve 3400 "I" "I"; echo "prompt" >"$D/prompt.txt"
helper "$SCRIPT_DIR/../clear-resume.sh" no-such-target "$D/prompt.txt" "$D/log"
[[ $RC -eq 2 ]] || fail "L5: unresolvable target exit $RC (want 2)"; [[ $(sent_keys) -eq 0 ]] || fail "L5: keys were sent"
contains "$D/log" "ERROR cannot resolve no-such-target" "L5: refusal logged"

# 8. constants: the cap default (3) and the DeepSeek / Open Design numbers are named in the script header
grep -q '^KEEPALIVE_MAX=3 ' "$KA" || fail "KEEPALIVE_MAX is 3, one named constant"
[[ $(grep -c '^KEEPALIVE_MAX=' "$KA") -eq 1 ]] || fail "KEEPALIVE_MAX is defined once"
grep -q '^DEEPSEEK_OPENDESIGN_CACHE_TTL_MIN=110$' "$KA" || fail "DeepSeek / Open Design cache TTL constant"
grep -q '^DEEPSEEK_OPENDESIGN_KEEPALIVE_AT_MIN=100$' "$KA" || fail "DeepSeek / Open Design keep-alive constant"

echo 'PASS ctl-keepalive'
