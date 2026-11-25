#!/usr/bin/env bash
# shellcheck disable=SC1091,SC2034,SC2154 # settings and helpers come from the sourced resume-lib.sh
set -u
# ctl-keepalive.sh [--once]
# Prompt-cache keep-alive watcher for the interactive Claude Code controller panes in tmux. Spec:
# docs/design/agctl/CONTEXT-ECONOMY.md section 2 (+ review items R2-1/2/3/5). ONE process for all panes, started detached
# by hand (`setsid nohup ctl-keepalive.sh &`); ctl-doctor reports it running or absent and prints this start command, but
# never starts it. No controller session owns it. `--once` runs one poll (tests; it pings too).
# 55-59 min after the last real API request (newest assistant record with usage in the session JSONL) it types
# one digit-free ping into the pane's EMPTY single-row prompt box, reads it back after 1 s and presses Enter only on
# an exact match; anything else is cleared (our own text) or left alone (foreign text, dialog, busy) and
# ~/controller/keepalive/<sessionId>.abort holds the anchor. The count N comes from the JSONL, the cap is
# KEEPALIVE_MAX, at most one ping per anchor. Every pane check (busy, dialog, prompt box) reads ONE `pane_view` of the
# capture: all bottom windows are measured from the mode line, and the rows below it (the agents panel) are trimmed
# at the mode line (the agents panel below it is not part of the box). Pane lock
# ~/controller/locks/<%pane_id>.lock is shared with the
# resume helpers. Files: logs/keepalive.log, keepalive.pid, locks/keepalive.lock. Skips are logged on change only.
#
# NOTE (DeepSeek / Open Design controllers): no code path until such a controller exists; operator ruling, the
# constants are recorded here so a future pi/DeepSeek session class has its numbers. Its cache TTL is about
# 110 min (not the 1 h of Claude Code), so keeping it warm aims at 100 min after the last request.
DEEPSEEK_OPENDESIGN_CACHE_TTL_MIN=110
DEEPSEEK_OPENDESIGN_KEEPALIVE_AT_MIN=100
#
# Test seams ONLY (paths and clock, never policy): KEEPALIVE_HOME, KEEPALIVE_SESSIONS_DIR, KEEPALIVE_PROJECTS_DIR,
# KEEPALIVE_PROC, KEEPALIVE_NOW (epoch seconds), KEEPALIVE_READBACK_WAIT, and KEEPALIVE_SOURCE_ONLY (source the functions
# without starting the watcher). The constants below are plain assignments: an inherited environment variable cannot
# change the cap or the name list.
KEEPALIVE_POLL=60                                 # seconds between polls
KEEPALIVE_IDLE_MIN=3300                           # ping from 55 min after the anchor ...
KEEPALIVE_IDLE_MAX=3540                           # ... to 59 min (after that the cache is gone: a ping would only pay twice)
KEEPALIVE_MAX=3                                   # pings in a row without a real prompt (operator question 3, default 3)
KEEPALIVE_CTX_MAX=300000                          # tokens: at or above, the session must compact instead
KEEPALIVE_NAMES="vbpub-main-controller vbpub-platform vbpub-core vbpub-assay vbpub-hostobs vbpub-edge vbpub-rollback"
KEEPALIVE_MIN_WIDTH=80                            # panes narrower than this could wrap the ping
# The ping: no digits (they would select a numbered menu option), starts with `[` (never a slash command, `!` or `#`).
PING_PREFIX='[keepalive'                          # what the JSONL scan counts
PING_TEXT='[keepalive] cache refresh only: reply ok, no tools'
# ---- pane markers: the sanitized real idle captures in tests/fixtures/keepalive cover the box, notice and agents-panel
# ---- layouts; the dialog cursor/footer remain UNVERIFIED on Claude Code 2.1.331. A Claude Code release may change them.
DIALOG_CURSOR_RE='^[[:space:]]*❯[[:space:]]*[0-9]+\.[[:space:]]'   # cursor on a numbered option of a permission/question menu
DIALOG_FOOTER='Esc to cancel'                                     # footer of those menus
DIALOG_LINES=15                                                   # how many bottom non-blank lines are searched for them
BOX_RULE_BYTES='(?:\xe2\x94\x80){20,}'                            # a prompt-box rule: at least 20 x U+2500, nothing else (perl, bytes)
BOX_LABEL_RULE_BYTES='(?:\xe2\x94\x80){20,}[A-Za-z0-9._-]+(?:\xe2\x94\x80)+'   # same rule with one tmux session label (spaces stripped)
BOX_LINES=6                                                       # the input row must be within this many bottom non-blank lines
BOX_FOOT_MARKER='⏵⏵ bypass permissions on'                        # the mode line: footer rows below it are not part of the prompt box

RESUME_PREFIX=KA_RESUME   # resume-lib.sh test seams; not KEEPALIVE_*, that namespace is this script's constants
home_dir=${KEEPALIVE_HOME:-${CONTROLLER_HOME:-$HOME/controller}}
sessions_dir=${KEEPALIVE_SESSIONS_DIR:-$HOME/.claude/sessions}
projects_dir=${KEEPALIVE_PROJECTS_DIR:-$HOME/.claude/projects}
proc=${KEEPALIVE_PROC:-/proc}
readback_wait=${KEEPALIVE_READBACK_WAIT:-1}
log=$home_dir/logs/keepalive.log
# shellcheck source=resume-lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/resume-lib.sh"
BUSY_MARKERS+=("$COMPACTING_MARKER")   # the spinner of a running compaction counts as busy, as in compact-resume.sh
FOOT_LINES=6                           # ... and it sits higher than the working spinner

now() { printf '%s' "${KEEPALIVE_NOW:-$(date +%s)}"; }
klog() { # name sessionId-prefix action reason
  printf '%s %s %s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "${2:0:8}" "$3" "$4" >>"$log"
}
declare -A last_reason
skip() { # name sid reason [key]: a skip is logged once per change of reason per key (default: the name); a poll every minute would flood the log
  local key=${4:-$1}
  [[ ${last_reason[$key]:-} == "$3" ]] && return 0
  last_reason[$key]=$3; klog "$1" "$2" skip "$3"
}
strip_sgr() { perl -pe 's/\e\[[0-9;]*m//g'; }

# analyze <jsonl> -> "<anchor epoch> <N> <ctx tokens> <ping newer than the anchor 0|1>", or "none"
analyze() {
  python3 -I - "$1" "$PING_PREFIX" <<'PY'
import datetime, json, os, sys
path, prefix = sys.argv[1], sys.argv[2]
def epoch(ts):
    return int(datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
size = os.path.getsize(path)
with open(path, "rb") as fh:
    fh.seek(max(0, size - 400_000))
    lines = fh.read().decode("utf-8", "replace").splitlines()
anchor = ctx = None
n, counting, ping_ts = 0, True, 0
for line in reversed(lines):
    try:
        rec = json.loads(line)
        kind, msg, ts = rec.get("type"), rec.get("message") or {}, epoch(rec["timestamp"])
    except Exception:
        continue
    if kind == "assistant" and anchor is None:
        u = msg.get("usage") or {}
        tokens = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if rec.get("isApiErrorMessage") or msg.get("model") == "<synthetic>" or tokens == 0:
            continue   # Claude Code's own error record (all-zero usage) is not an API request
        anchor, ctx = ts, tokens
    elif kind == "user" and counting and not rec.get("isMeta"):
        c = msg.get("content")
        if isinstance(c, list) and c and isinstance(c[0], dict) and c[0].get("type") != "tool_result":
            c = c[0].get("text", "")
        if not isinstance(c, str):
            continue   # a tool result is not a prompt
        counting = c.startswith(prefix)
        if counting:
            ping_ts = ping_ts or ts   # the newest prompt, when it is a ping
            n += 1
    if anchor is not None and not counting:
        break
print("none" if anchor is None else f"{anchor} {n} {ctx} {int(ping_ts > anchor)}")
PY
}

# live_sessions: one US-separated line per session file whose name is wanted: pid procStart sessionId name cwd tmux
live_sessions() {
  python3 -I - "$sessions_dir" <<'PY'
import glob, json, os, sys
sep = "\x1f"
for path in sorted(glob.glob(os.path.join(sys.argv[1], "*.json"))):
    try:
        d = json.load(open(path))
        print(sep.join(str(d.get(k, "")) for k in ("pid", "procStart", "sessionId", "name", "cwd", "tmux")))
    except Exception:
        continue
PY
}

pane_view() { # $1 = capture with escapes: the prompt box and every bottom window are measured from the MODE line, not from
  # the last line of the capture: nothing below the mode line (the agents panel) is part of the prompt box. No input row
  # or no mode line: the capture is returned unchanged (fail-safe).
  local ln=() i idx=-1 n esc=$'\033' re mode
  re="^(${esc}\[[0-9;]*m)*$INPUT_MARKER"
  mapfile -t ln <<<"$1"; n=${#ln[@]}
  for ((i = 0; i < n; i++)); do [[ ${ln[i]} =~ $re ]] && idx=$i; done
  (( idx >= 0 )) || { printf '%s\n' "${ln[@]}"; return 0; }
  for ((i = idx + 1; i < n; i++)); do
    mode=$(strip_sgr <<<"${ln[i]}")
    [[ $mode == *"$BOX_FOOT_MARKER"* ]] && { n=$((i + 1)); break; }   # the mode line: the agents panel below it is not the box
  done
  printf '%s\n' "${ln[@]:0:n}"
}
dialog_open() { # $1 = capture with escapes: a numbered permission/question menu is on screen
  local plain; plain=$(strip_sgr <<<"$1" | grep -v '^[[:space:]]*$' | tail -n "$DIALOG_LINES")
  grep -qE -- "$DIALOG_CURSOR_RE" <<<"$plain" || grep -qF -- "$DIALOG_FOOTER" <<<"$plain"
}
is_rule() { local r=${1//$'\xc2\xa0'/}; r=${r//[[:space:]]/}; perl -e 'exit($ARGV[0] =~ /^'"$BOX_RULE_BYTES"'$/ ? 0 : 1)' "$r"; }
is_label_rule() { local r=${1//$'\xc2\xa0'/}; r=${r//[[:space:]]/}; perl -e 'exit($ARGV[0] =~ /^'"$BOX_LABEL_RULE_BYTES"'$/ ? 0 : 1)' "$r"; }
box_ok() { # $1 = capture: the live input row is alone in its box (a box rule right above AND below), within the last BOX_LINES non-blank lines
  # $1 must be a pane_view capture: the trim at the mode line lives there, once.
  local ln=() i n idx=-1 re="^$INPUT_MARKER"
  mapfile -t ln < <(strip_sgr <<<"$1" | grep -av '^[[:space:]]*$')   # colour codes first: a codes-only line is blank, as in pane_state_of
  n=${#ln[@]}
  for ((i = 0; i < n; i++)); do [[ ${ln[i]} =~ $re ]] && idx=$i; done
  (( idx >= 1 && idx + 1 < n && idx >= n - BOX_LINES )) || return 1
  { is_rule "${ln[idx-1]}" || is_label_rule "${ln[idx-1]}"; } && is_rule "${ln[idx+1]}"   # label only above; a draft adds lines below
}
norm_row() { # the input row with NBSP as a space and the gap after the marker squeezed (R2-1), trailing blanks dropped
  local r=$1
  r=${r//$'\xc2\xa0'/ }; r=${r#"$INPUT_MARKER"}
  r=${r#"${r%%[![:space:]]*}"}; r=${r%"${r##*[![:space:]]}"}
  printf '%s %s' "$INPUT_MARKER" "$r"
}
abort_mark() { # sid anchor: the session is skipped while its anchor is this one
  mkdir -p -- "$home_dir/keepalive" && printf '%s\n' "$2" >"$home_dir/keepalive/$1.abort"
}

# ping <name> <sid> <pane> <anchor> <n>: checks, type, read back, Enter; the caller holds the pane lock
ping_pane() {
  local name=$1 sid=$2 pane=$3 anchor=$4 n=$5 t rc rest row want="$INPUT_MARKER $PING_TEXT"
  t=$(tmux capture-pane -e -p -t "$pane") || { skip "$name" "$sid" "capture-failed"; return; }
  t=$(pane_view "$t")   # one view for the busy, dialog and box checks: each window starts at the mode line
  pane_state_of "$t"; rc=$?
  [[ $rc -eq 1 ]] && { skip "$name" "$sid" "busy"; return; }
  dialog_open "$t" && { skip "$name" "$sid" "dialog"; return; }
  box_ok "$t" || { skip "$name" "$sid" "input-box-layout"; return; }
  [[ $rc -eq 0 ]] || { skip "$name" "$sid" "input-not-empty"; return; }
  tmux send-keys -t "$pane" -l -- "$PING_TEXT" || { klog "$name" "$sid" error "send-keys failed"; return; }
  sleep "$readback_wait"
  t=$(tmux capture-pane -e -p -t "$pane") || { klog "$name" "$sid" abort-foreign "read-back capture failed; nothing more sent"; abort_mark "$sid" "$anchor"; return; }
  t=$(pane_view "$t")   # the same view as before typing: a busy pane whose mode line sits above a tall panel still reads busy
  row=$(norm_row "$(input_row_of "$t")"); rest=${row#"$INPUT_MARKER "}
  local ours=0 prefix=0
  [[ $row == "$want" ]] && ours=1
  [[ $ours -eq 0 && $PING_TEXT == "$rest"* ]] && prefix=1   # strict prefix (or nothing): only our partial typing is there
  if dialog_open "$t"; then   # a dialog opened in between: our text may be in it; nothing more is sent
    klog "$name" "$sid" abort-foreign "dialog opened during the read-back"
  elif ! box_ok "$t"; then   # a multi-line draft or an unknown layout: C-u would erase a line of it, Enter would submit it
    klog "$name" "$sid" abort-foreign "prompt box layout changed during the read-back; nothing more sent"
  elif pane_state_of "$t"; [[ $? -eq 1 ]]; then   # a turn started in between (task notification): do not queue the ping into it
    if [[ $ours -eq 1 || $prefix -eq 1 ]]; then tmux send-keys -t "$pane" C-u; klog "$name" "$sid" abort-busy "turn started during the read-back; input cleared"
    else klog "$name" "$sid" abort-foreign "busy and foreign text in the input row; nothing more sent"; fi
  elif [[ $ours -eq 1 ]]; then
    tmux send-keys -t "$pane" Enter || { klog "$name" "$sid" error "send-keys Enter failed"; return; }
    klog "$name" "$sid" ping "$n/$KEEPALIVE_MAX anchor=$anchor"; last_reason[$name]=; return
  elif [[ $prefix -eq 1 ]]; then
    tmux send-keys -t "$pane" C-u; klog "$name" "$sid" abort-cleared "only our partial typing was in the input row"
  else
    klog "$name" "$sid" abort-foreign "foreign text in the input row; nothing more sent"
  fi
  abort_mark "$sid" "$anchor"
}

# handle <name> <sid> <cwd> <tmux field>: one mapped, live, unique session
handle() {
  local name=$1 sid=$2 cwd=$3 field=$4 jsonl mt info anchor n ctx after elapsed pane_id session width resolved lock
  local tsession=${field%%:*} tpane=${field##*.}
  [[ $field == *:*.%* && $tpane =~ ^%[0-9]+$ ]] || { skip "$name" "$sid" "no-tmux-pane"; return; }
  jsonl=$projects_dir/${cwd//\//-}/$sid.jsonl
  [[ -f $jsonl ]] || { skip "$name" "$sid" "no-jsonl"; return; }
  # The mtime is never newer-than-anchor information: it only rules out what is certainly too old (anchor <= mtime).
  mt=$(stat -c %Y -- "$jsonl") || return
  (( $(now) - mt >= KEEPALIVE_IDLE_MAX )) && return
  info=$(analyze "$jsonl") || { skip "$name" "$sid" "jsonl-unreadable"; return; }
  [[ $info == none ]] && { skip "$name" "$sid" "no-anchor"; return; }
  read -r anchor n ctx after <<<"$info"
  elapsed=$(( $(now) - anchor ))
  (( elapsed >= KEEPALIVE_IDLE_MIN && elapsed < KEEPALIVE_IDLE_MAX )) || { last_reason[$name]=; return; }
  [[ -f $home_dir/keepalive/$sid.abort && $(<"$home_dir/keepalive/$sid.abort") == "$anchor" ]] && { skip "$name" "$sid" "aborted-for-this-anchor"; return; }
  (( n >= KEEPALIVE_MAX )) && { skip "$name" "$sid" "cap $n/$KEEPALIVE_MAX"; return; }
  (( after == 1 )) && { skip "$name" "$sid" "one-ping-per-anchor"; return; }
  (( ctx >= KEEPALIVE_CTX_MAX )) && { skip "$name" "$sid" "context $ctx >= $KEEPALIVE_CTX_MAX: compact instead"; return; }
  resolved=$(tmux display -p -t "$tpane" '#{pane_id} #{session_name} #{pane_width}' 2>/dev/null) || resolved=
  read -r pane_id session width <<<"$resolved"
  [[ $pane_id == "$tpane" && $session == "$tsession" ]] || { skip "$name" "$sid" "pane-mismatch ($tpane in $tsession is now ${pane_id:-gone} in ${session:-?})"; return; }
  [[ $width =~ ^[0-9]+$ ]] || width=0
  (( width >= KEEPALIVE_MIN_WIDTH )) || { skip "$name" "$sid" "pane-narrow ($width)"; return; }
  lock=$home_dir/locks/$pane_id.lock
  {
    flock -n 9 || { skip "$name" "$sid" "lock-busy"; return; }
    ping_pane "$name" "$sid" "$pane_id" "$anchor" $((n + 1))
  } 9>"$lock"
}

poll_once() {
  local pid pstart sid name cwd field stat rest f k
  declare -A seen=() live=()
  while IFS=$'\x1f' read -r pid pstart sid name cwd field; do
    [[ " $KEEPALIVE_NAMES " == *" $name "* ]] || continue
    if [[ ! $pid =~ ^[0-9]+$ || ! -d $proc/$pid || $(<"$proc/$pid/comm") != claude ]] 2>/dev/null; then skip "$name" "$sid" "dead-or-not-claude (pid $pid)" "$name#$pid"; continue; fi
    stat=$(<"$proc/$pid/stat") || continue
    rest=${stat##*) }; read -ra f <<<"$rest"
    [[ ${f[19]:-} == "$pstart" ]] || { skip "$name" "$sid" "pid-reused (procStart mismatch)" "$name#$pid"; continue; }
    seen[$name]=$(( ${seen[$name]:-0} + 1 ))
    live[$pid]="$sid"$'\x1f'"$name"$'\x1f'"$cwd"$'\x1f'"$field"
  done < <(live_sessions)
  for k in "${!live[@]}"; do
    IFS=$'\x1f' read -r sid name cwd field <<<"${live[$k]}"
    if (( ${seen[$name]} > 1 )); then skip "$name" "$sid" "duplicate-name (${seen[$name]} live sessions)"; continue; fi
    (( stop )) || handle "$name" "$sid" "$cwd" "$field"
  done
}

watch_loop() {
  while :; do
    poll_once
    (( stop )) && break
    sleep "$KEEPALIVE_POLL" 8>&- & wait $!
  done
}

if [[ ${KEEPALIVE_SOURCE_ONLY:-} == 1 ]]; then
  [[ ${BASH_SOURCE[0]} != "$0" ]] || { echo "KEEPALIVE_SOURCE_ONLY only works when sourced" >&2; exit 2; }
  return 0
fi

[[ ${1:-} == --once || $# -eq 0 ]] || { echo "usage: ${0##*/} [--once]" >&2; exit 2; }
if ! { mkdir -p -- "$home_dir/logs" "$home_dir/locks" "$home_dir/keepalive" && : >>"$log"; } 2>/dev/null; then echo "ERROR cannot write below $home_dir" >&2; exit 2; fi
exec 8>"$home_dir/locks/keepalive.lock"
flock -n 8 || { echo "ctl-keepalive: already running (lock $home_dir/locks/keepalive.lock)" >&2; exit 0; }
printf '%s\n' "$$" >"$home_dir/keepalive.pid"
trap 'rm -f -- "$home_dir/keepalive.pid"' EXIT
stop=0; trap 'stop=1' TERM INT   # a signal never interrupts a ping: the read-back finishes, then the loop ends
klog watcher - start "poll=$KEEPALIVE_POLL window=$KEEPALIVE_IDLE_MIN-$KEEPALIVE_IDLE_MAX max=$KEEPALIVE_MAX names=$KEEPALIVE_NAMES"
if [[ ${1:-} == --once ]]; then poll_once; exit 0; fi
# the sleep must not inherit the singleton lock (fd 8), or a restart would find it held for up to a minute
watch_loop
