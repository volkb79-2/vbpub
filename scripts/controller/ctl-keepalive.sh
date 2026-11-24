#!/usr/bin/env bash
# shellcheck disable=SC1091,SC2034,SC2154 # settings and helpers come from the sourced resume-lib.sh
set -u
# ctl-keepalive.sh [--once]
# Prompt-cache keep-alive watcher for the interactive Claude Code controller sessions in tmux panes.
# Spec: docs/design/agctl/CONTEXT-ECONOMY.md section 2 (+ the review items R2-1, R2-2, R2-3, R2-5).
# ONE process for all panes, started detached by hand or by ctl-doctor.sh --fix: `setsid nohup ctl-keepalive.sh &`.
# Never started by a controller session: it must survive every controller restart. `--once` runs one poll
# (tests; a manual dry look does NOT exist: --once pings too).
#
# What it does: once a mapped session has been idle for 55-59 min (measured from the newest assistant record
# with usage in its JSONL = the last API request, the TTL anchor), it types one short ping into the pane so the
# 1-hour prompt cache is renewed. The ping count N is derived from the JSONL ("[keepalive" user prompts back
# from the end, ended by the first real prompt or task notification), so there is no state to lose; the cap is
# KEEPALIVE_MAX. At most one ping per anchor (a ping that failed leaves the anchor where it was: the next one
# waits for the next window, not the next poll). Sessions at or above KEEPALIVE_CTX_MAX tokens are not kept
# warm (they must compact).
#
# Pane -> session: ~/.claude/sessions/<pid>.json (never #{pane_title}). A file counts only when its name is in
# KEEPALIVE_NAMES, the pid is alive with /proc/<pid>/comm = claude, procStart = field 22 of /proc/<pid>/stat,
# its `tmux` field is <tmux-session>:@<window>.%<pane> and #{session_name} of that pane matches, and the JSONL
# ~/.claude/projects/<cwd with "/" -> "-">/<sessionId>.jsonl exists. Two live files with one name: both skipped.
#
# Pane safety: the ping is typed only into an idle, dialog-free, EMPTY prompt box (the dim prompt suggestion
# counts as empty: resume-lib.sh pane_state_of, reused, not copied), then read back after 1 s: Enter only when
# the row is exactly the ping. Otherwise C-u (only our own partial text / our text on a busy pane) or nothing
# (foreign text, dialog), and ~/controller/keepalive/<sessionId>.abort holds the anchor so the session is
# skipped until its anchor changes. The pane lock ~/controller/locks/<%pane_id>.lock is shared with
# compact-resume.sh and clear-resume.sh.
# Files: log ~/controller/logs/keepalive.log (one line per decision), pid ~/controller/keepalive.pid, singleton
# lock ~/controller/locks/keepalive.lock. Skip reasons are logged when they change, not every minute.
#
# NOTE (DeepSeek / Open Design controllers): no code path until such a controller exists; operator ruling, the
# constants are recorded here so a future pi/DeepSeek session class has its numbers. Its cache TTL is about
# 110 min (not the 1 h of Claude Code), so keeping it warm aims at 100 min after the last request.
DEEPSEEK_OPENDESIGN_CACHE_TTL_MIN=110
DEEPSEEK_OPENDESIGN_KEEPALIVE_AT_MIN=100
#
# Test seams (tests/test-ctl-keepalive.sh): KEEPALIVE_HOME (default ~/controller), KEEPALIVE_SESSIONS_DIR
# (~/.claude/sessions), KEEPALIVE_PROJECTS_DIR (~/.claude/projects), KEEPALIVE_PROC (/proc), KEEPALIVE_NOW (epoch
# seconds), KEEPALIVE_READBACK_WAIT (1), KEEPALIVE_NAMES, and the constants below as environment overrides.

# ---- constants (the defaults are the specified values; the environment override is for the tests only) --------
KEEPALIVE_POLL=${KEEPALIVE_POLL:-60}              # seconds between polls
KEEPALIVE_IDLE_MIN=${KEEPALIVE_IDLE_MIN:-3300}    # ping from 55 min after the anchor ...
KEEPALIVE_IDLE_MAX=${KEEPALIVE_IDLE_MAX:-3540}    # ... to 59 min (after that the cache is gone: a ping would only pay twice)
KEEPALIVE_MAX=${KEEPALIVE_MAX:-3}                 # pings in a row without a real prompt (operator question 3, default 3)
KEEPALIVE_CTX_MAX=${KEEPALIVE_CTX_MAX:-300000}    # tokens: at or above, the session must compact instead
KEEPALIVE_NAMES=${KEEPALIVE_NAMES:-"vbpub-main-controller vbpub-platform vbpub-core vbpub-assay vbpub-hostobs vbpub-edge vbpub-rollback"}
KEEPALIVE_MIN_WIDTH=80                            # panes narrower than this could wrap the ping
# The ping: no digits (they would select a numbered menu option), starts with `[` (never a slash command, `!` or `#`).
PING_PREFIX='[keepalive'                          # what the JSONL scan counts
PING_TEXT='[keepalive] cache refresh only: reply ok, no tools'
# ---- pane markers: UNVERIFIED on Claude Code 2.1.331 (replace the fixtures in tests/test-ctl-keepalive.sh with real
# ---- captured panes when the operator has them; a Claude Code release may change them) ------------------------------
DIALOG_CURSOR_RE='^[[:space:]]*❯[[:space:]]*[0-9]+\.[[:space:]]'   # cursor on a numbered option of a permission/question menu
DIALOG_FOOTER='Esc to cancel'                                     # footer of those menus
DIALOG_LINES=15                                                   # how many bottom non-blank lines are searched for them
BOX_RULE_BYTES='(?:\xe2\x94\x80){20,}'                            # a prompt-box rule: at least 20 x U+2500, nothing else (perl, bytes)
BOX_LINES=6                                                       # the input row must be within this many bottom non-blank lines

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

# analyze <jsonl> <now> -> "<anchor epoch> <N> <ctx tokens> <ping newer than the anchor 0|1>", or "none"
analyze() {
  python3 -I - "$1" "$2" "$PING_PREFIX" <<'PY'
import datetime, json, os, sys
path, now, prefix = sys.argv[1], int(sys.argv[2]), sys.argv[3]
def epoch(ts):
    return int(datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
size = os.path.getsize(path)
with open(path, "rb") as fh:
    fh.seek(max(0, size - 400_000))
    lines = fh.read().decode("utf-8", "replace").splitlines()
anchor = ctx = None
n, counting, ping_after = 0, True, 0
newest_prompt_seen = False
for line in reversed(lines):
    try:
        rec = json.loads(line)
    except Exception:
        continue
    if not isinstance(rec, dict):
        continue
    kind = rec.get("type")
    if kind == "assistant" and anchor is None:
        usage = (rec.get("message") or {}).get("usage")
        if usage and rec.get("timestamp"):
            try:
                anchor = epoch(rec["timestamp"])
            except Exception:
                continue
            ctx = sum(int(usage.get(k, 0) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    elif kind == "user" and not rec.get("isMeta") and counting:
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list) and content and isinstance(content[0], dict) and content[0].get("type") != "tool_result":
            text = str(content[0].get("text", ""))
        else:
            continue   # a tool result is not a prompt
        if text.startswith(prefix):
            if not newest_prompt_seen:
                newest_prompt_seen = True
                ts = rec.get("timestamp")
                rec_epoch = epoch(ts) if ts else 0
                ping_after = rec_epoch
            n += 1
        else:
            counting = False
            newest_prompt_seen = True
    if anchor is not None and not counting:
        break
if anchor is None:
    print("none")
else:
    print(anchor, n, ctx, 1 if ping_after > anchor else 0)
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

dialog_open() { # $1 = capture with escapes: a numbered permission/question menu is on screen
  local plain; plain=$(strip_sgr <<<"$1" | grep -v '^[[:space:]]*$' | tail -n "$DIALOG_LINES")
  grep -qE -- "$DIALOG_CURSOR_RE" <<<"$plain" || grep -qF -- "$DIALOG_FOOTER" <<<"$plain"
}
box_ok() { # $1 = capture: the live input row is within the last BOX_LINES non-blank lines, with a box rule right above it
  local t=$1 esc=$'\033' ln=() i n idx=-1 prev re
  re="^(${esc}\[[0-9;]*m)*$INPUT_MARKER"
  mapfile -t ln < <(grep -av '^[[:space:]]*$' <<<"$t")
  n=${#ln[@]}
  for ((i = 0; i < n; i++)); do [[ ${ln[i]} =~ $re ]] && idx=$i; done
  (( idx >= 1 && idx >= n - BOX_LINES )) || return 1
  prev=$(strip_sgr <<<"${ln[idx-1]}"); prev=${prev//$'\xc2\xa0'/}; prev=${prev//[[:space:]]/}
  perl -e 'exit($ARGV[0] =~ /^'"$BOX_RULE_BYTES"'$/ ? 0 : 1)' "$prev"
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
  pane_state_of "$t"; rc=$?
  [[ $rc -eq 1 ]] && { skip "$name" "$sid" "busy"; return; }
  dialog_open "$t" && { skip "$name" "$sid" "dialog"; return; }
  box_ok "$t" || { skip "$name" "$sid" "input-box-layout"; return; }
  [[ $rc -eq 0 ]] || { skip "$name" "$sid" "input-not-empty"; return; }
  tmux send-keys -t "$pane" -l -- "$PING_TEXT" || { klog "$name" "$sid" error "send-keys failed"; return; }
  sleep "$readback_wait"
  t=$(tmux capture-pane -e -p -t "$pane") || { klog "$name" "$sid" abort-foreign "read-back capture failed; nothing more sent"; abort_mark "$sid" "$anchor"; return; }
  row=$(norm_row "$(input_row_of "$t")"); rest=${row#"$INPUT_MARKER "}
  local ours=0 prefix=0
  [[ $row == "$want" ]] && ours=1
  [[ $ours -eq 0 && $PING_TEXT == "$rest"* ]] && prefix=1   # strict prefix (or nothing): only our partial typing is there
  if dialog_open "$t"; then   # a dialog opened in between: our text may be in it; nothing more is sent
    klog "$name" "$sid" abort-foreign "dialog opened during the read-back"
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
  info=$(analyze "$jsonl" "$(now)") || { skip "$name" "$sid" "jsonl-unreadable"; return; }
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
    handle "$name" "$sid" "$cwd" "$field"
  done
}

[[ ${1:-} == --once || $# -eq 0 ]] || { echo "usage: ${0##*/} [--once]" >&2; exit 2; }
if ! { mkdir -p -- "$home_dir/logs" "$home_dir/locks" "$home_dir/keepalive" && : >>"$log"; } 2>/dev/null; then echo "ERROR cannot write below $home_dir" >&2; exit 2; fi
exec 8>"$home_dir/locks/keepalive.lock"
flock -n 8 || { echo "ctl-keepalive: already running (lock $home_dir/locks/keepalive.lock)" >&2; exit 0; }
printf '%s\n' "$$" >"$home_dir/keepalive.pid"
trap 'rm -f -- "$home_dir/keepalive.pid"' EXIT
trap 'exit 0' TERM INT
klog watcher - start "poll=$KEEPALIVE_POLL window=$KEEPALIVE_IDLE_MIN-$KEEPALIVE_IDLE_MAX max=$KEEPALIVE_MAX names=$KEEPALIVE_NAMES"
if [[ ${1:-} == --once ]]; then poll_once; exit 0; fi
while :; do poll_once; sleep "$KEEPALIVE_POLL"; done
