#!/usr/bin/env bash
# Offline tests for scripts/controller/hooks/ctx-hooks.py using fixture hook
# JSON and a fake extract command. No session, no network, no real transcript.
set -uo pipefail
# The caller's ambient controller configuration is not this check's business:
# every check here builds its own temporary controller root, so an exported
# CONTROLLER_HOME / RUNS_DIR / LOGS_DIR / REPO / HOSTS_FILE must not decide where
# a check reads or writes. Relying on the caller ("run it with env -u") moved the
# same defect into the operator's shell, where it read as a product bug.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
# The hook reads CTX_* tuning and ~/.claude/settings.json: neither the caller's
# environment nor its real HOME may decide what is judged here.
while IFS= read -r name; do unset "$name"; done < <(compgen -v | grep -E '^CTX_|^CLAUDE_PROJECT_DIR$' | grep -vx CTX_HOOKS_SCRIPT)
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
HOOKS=${CTX_HOOKS_SCRIPT:-$SCRIPT_DIR/../hooks/ctx-hooks.py}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-ctx-hooks-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
matches() { grep -qE -- "$2" "$1" || fail "$3 (no match: $2)"; }
not_contains() {
  local needle=$1 description=$2
  shift 2
  ! grep -qF -- "$needle" "$@" || fail "$description (unexpected: $needle)"
}

export HOME=$ROOT/home
mkdir -p "$HOME/.claude"
export CONTROLLER_HOME=$ROOT/controller-home
STATE=$ROOT/state
TRANSCRIPT=$ROOT/session.jsonl
BIN=$ROOT/bin
mkdir -p "$STATE" "$BIN"
# A call that names no --state-dir would resolve to the `.controller-state` of the checkout that holds the
# script, and a run from the main checkout would append to the LIVE hook log. Every call here gets this
# throwaway directory instead; the checks that test the walk-up and the script-repo fallback clear it.
export CTX_STATE_DIR=$ROOT/state-default

# A transcript whose last assistant usage says the context is 150k tokens.
python3 - "$TRANSCRIPT" <<'PY'
import json
import sys

records = [
    {"type": "user", "uuid": "u1", "timestamp": "2026-11-15T09:00:00Z",
     "message": {"role": "user", "content": "Controller goal fixture."}},
]
for fill in (60_000, 150_000):
    records.append({"type": "assistant", "uuid": f"a{fill}",
                    "timestamp": "2026-11-15T09:01:00Z",
                    "message": {"role": "assistant", "id": f"m{fill}",
                                "content": [{"type": "text", "text": "prose"}],
                                "usage": {"input_tokens": fill,
                                          "cache_read_input_tokens": 0,
                                          "cache_creation_input_tokens": 0}}})
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for record in records:
        handle.write(json.dumps(record) + "\n")
PY

# Fake extract: honours --since and prints a readback document with a marker.
cat > "$BIN/fake-extract" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$FAKE_EXTRACT_LOG"
payload=$(cat <<'TX'
<!-- nyxloom-extract: format=claude-code marker=fake-marker-7 placement=both -->
## Goal of the session (anchor -- included once per read-back, never truncated)
OPERATOR: Controller goal fixture.
## Delta since the previous read-back
fake light+darkness: secret-token-fixture must never appear
[readback marker: fake-marker-7]
<!-- nyxloom-extract: format=claude-code marker=fake-marker-7 placement=both -->
TX
)
payload=${payload//fake-marker-7/${FAKE_MARKER:-fake-marker-7}}
printf '%s\n' "$payload"
exit "${FAKE_EXTRACT_RC:-0}"
SH
# A fake extract that hangs, for the timeout case.
cat > "$BIN/fake-extract-slow" <<'SH'
#!/usr/bin/env bash
sleep 60
printf 'never\n'
SH
chmod +x "$BIN/fake-extract" "$BIN/fake-extract-slow"
export FAKE_EXTRACT_LOG=$ROOT/extract-argv.log
: > "$FAKE_EXTRACT_LOG"

hook() { # hook <mode> <json> [extra args...]
  local mode=$1 payload=$2
  shift 2
  printf '%s' "$payload" | python3 "$HOOKS" "$mode" "$@"
}

run() { # run <outfile> <mode> <json> [extra args...]
  local out=$1 mode=$2 payload=$3
  shift 3
  hook "$mode" "$payload" "$@" > "$out" 2>&1
  local rc=$?
  [[ $rc == 0 ]] || fail "$mode exited $rc (a hook must never fail the session): $(cat "$out")"
}

MAIN_JSON='{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","hook_event_name":"SessionStart","source":"compact"}'

# Compact receipts are scoped to a real tmux pane and top-level compact events.
export TMUX_PANE=%41
run "$ROOT/receipt-1.out" session-start "$MAIN_JSON" --state-dir "$STATE" --extract "$BIN/fake-extract"
RECEIPT=$CONTROLLER_HOME/compact-receipts/%41.json
[[ -f $RECEIPT ]] || fail 'compact SessionStart did not write its receipt'
python3 - "$RECEIPT" <<'PY' || fail 'compact receipt fields are missing'
import json, sys
receipt = json.load(open(sys.argv[1], encoding="utf-8"))
assert receipt["session_id"] == "sess-1", receipt
assert receipt["timestamp"].endswith("+00:00"), receipt
assert receipt["write_id"], receipt
PY
cp "$RECEIPT" "$ROOT/receipt-first.json"
run "$ROOT/receipt-2.out" session-start "$MAIN_JSON" --state-dir "$STATE" --extract "$BIN/fake-extract"
! cmp -s "$ROOT/receipt-first.json" "$RECEIPT" || fail 'second compact receipt did not change'
cp "$RECEIPT" "$ROOT/receipt-first.json"
for source in resume startup; do
  run "$ROOT/receipt-$source.out" session-start \
    '{"session_id":"sess-1","cwd":"'"$ROOT"'","source":"'"$source"'"}' \
    --state-dir "$STATE" --extract "$BIN/fake-extract"
  cmp -s "$ROOT/receipt-first.json" "$RECEIPT" || fail "$source unexpectedly wrote a compact receipt"
done
for pane in invalid '' ; do
  if [[ -n $pane ]]; then export TMUX_PANE=$pane; else unset TMUX_PANE; fi
  run "$ROOT/receipt-bad-pane.out" session-start "$MAIN_JSON" --state-dir "$STATE" --extract "$BIN/fake-extract"
  cmp -s "$ROOT/receipt-first.json" "$RECEIPT" || fail 'bad or missing TMUX_PANE wrote a receipt'
done
export TMUX_PANE=%41
run "$ROOT/receipt-subagent.out" session-start \
  '{"session_id":"sess-1","cwd":"'"$ROOT"'","source":"compact","agent_id":"child-1"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
cmp -s "$ROOT/receipt-first.json" "$RECEIPT" || fail 'subagent compaction wrote a receipt'
CTX_HOOK_ROLE=agent run "$ROOT/receipt-agent-role.out" session-start "$MAIN_JSON" \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
cmp -s "$ROOT/receipt-first.json" "$RECEIPT" || fail 'agent role compaction wrote a receipt'
CTX_SESSION_KIND=spawned run "$ROOT/receipt-spawned-kind.out" session-start "$MAIN_JSON" \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
cmp -s "$ROOT/receipt-first.json" "$RECEIPT" || fail 'spawned session kind wrote a receipt'
printf 'blocked\n' > "$ROOT/controller-home-file"
before_output=$ROOT/receipt-unwritable.out
CONTROLLER_HOME=$ROOT/controller-home-file hook session-start "$MAIN_JSON" --state-dir "$STATE" --extract "$BIN/fake-extract" \
  > "$before_output" 2>&1 || fail 'unwritable compact receipt changed hook exit status'
cmp -s "$ROOT/receipt-1.out" "$before_output" || fail 'unwritable compact receipt changed hook output'

# 1. pre-compact success: readback-<sid>-N.md written, marker stored, summariser note.
run "$ROOT/pc1.out" pre-compact '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
[[ -f $STATE/readback-sess-1-1.md ]] || fail 'pre-compact did not write readback-sess-1-1.md'
contains "$STATE/readback-sess-1-1.md" 'Goal of the session' 'the readback document is stored'
contains "$ROOT/pc1.out" "session's ORIGINAL goal" 'summariser note asks for the goal'
contains "$ROOT/pc1.out" 'IN-FLIGHT list' 'summariser note asks for the in-flight list'
contains "$ROOT/pc1.out" 'readback-sess-1-1.md' 'summariser note names the readback'
[[ ! -e $STATE/readback-state-sess-1.json ]] || fail 'the readback state landed in the state directory root'
python3 - "$STATE/hooks/readback-state-sess-1.json" <<'PY' || fail 'readback state did not record the marker (expected under hooks/)'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["marker"] == "fake-marker-7", state
assert state["n"] == 1, state
PY
contains "$FAKE_EXTRACT_LOG" '--preset readback' 'the extract command uses the readback preset'
contains "$FAKE_EXTRACT_LOG" '--budget-tokens 30000' 'the controller budget is the default'
contains "$FAKE_EXTRACT_LOG" '--tail-prose-tokens 10000' 'the default tail is 10000'

# 2. second compaction: N increments and the previous marker is passed as --since.
run "$ROOT/pc2.out" pre-compact '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"manual"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
[[ -f $STATE/readback-sess-1-2.md ]] || fail 'second pre-compact did not write readback-sess-1-2.md'
contains "$FAKE_EXTRACT_LOG" '--since fake-marker-7' 'the previous readback marker is used as the cut'

# 3. a lost state file cannot re-use a number (highest on disk wins).
rm -f -- "$STATE/hooks/readback-state-sess-1.json"
run "$ROOT/pc3.out" pre-compact '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
[[ -f $STATE/readback-sess-1-3.md ]] || fail 'readback numbering did not follow the files on disk'
[[ ! -e $STATE/readback-sess-1-1.md ]] && fail 'the first readback vanished'

# 4. extract failure: exit 0, a log line, a durable failure, no readback file.
export FAKE_EXTRACT_RC=2
run "$ROOT/pc-fail.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
unset FAKE_EXTRACT_RC
contains "$ROOT/pc-fail.out" 'could not be built automatically' 'failure is announced to the summariser'
contains "$STATE/hooks/hook-log.jsonl" '"ev": "readback"' 'failure is logged'
python3 - "$STATE/hooks/readback-state-sess-1.json" <<'PY' || fail 'a failed build was not recorded durably'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["ok"] is False and "extract exited 2" in state["reason"], state
PY

# 5. timeout: the 20 s hard cap keeps the hook responsive.
start=$SECONDS
run "$ROOT/pc-slow.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract-slow" --timeout 1
(( SECONDS - start <= 5 )) || fail 'the hook did not return quickly on a slow extract'
contains "$ROOT/pc-slow.out" 'timeout' 'a slow extract is reported as a timeout'
python3 - "$HOOKS" <<'PY' || fail 'the 20 s hard cap is not enforced'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ctx_hooks", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.HARD_TIMEOUT == 20.0, module.HARD_TIMEOUT
PY

# 6. missing transcript: no crash, no extraction attempted, manual build path.
: > "$FAKE_EXTRACT_LOG"
run "$ROOT/pc-none.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"/nonexistent/session.jsonl","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
contains "$ROOT/pc-none.out" 'could not be built automatically' 'a missing transcript is reported'
[[ ! -s $FAKE_EXTRACT_LOG ]] || fail 'a missing transcript still ran the extract'

# 7. session-start[compact]: read order, this session's readback path and size
#    (the builds above ended in failures, so a fresh successful build comes first).
run "$ROOT/pc7.out" pre-compact '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
run "$ROOT/ss.out" session-start "$MAIN_JSON" --state-dir "$STATE" --extract "$BIN/fake-extract"
contains "$ROOT/ss.out" 'CONTROLLER RESUME' 'session-start prints the read order'
contains "$ROOT/ss.out" 'operator-decisions.md' 'the read order names the decision log'
contains "$ROOT/ss.out" 'HANDOFF.md' 'the read order names the handoff file'
contains "$ROOT/ss.out" "$STATE/readback-sess-1-4.md" 'session-start names the newest readback'
contains "$ROOT/ss.out" 'estimated tokens' 'session-start reports the readback size'
python3 - "$ROOT/ss.out" <<'PY' || fail 'session-start output exceeds the hook cap'
import sys
text = open(sys.argv[1], encoding="utf-8").read()
assert len(text) <= 10000, len(text)
PY

# 8. session-start after a FAILED build prints the exact manual command.
export FAKE_EXTRACT_RC=2
run "$ROOT/pc-fail2.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$ROOT/state-fail" --extract "$BIN/fake-extract"
unset FAKE_EXTRACT_RC
run "$ROOT/ss-fail.out" session-start "$MAIN_JSON" --state-dir "$ROOT/state-fail" \
  --extract "$BIN/fake-extract"
contains "$ROOT/ss-fail.out" 'BUILD IT BY HAND' 'a failed build yields the manual command'
contains "$ROOT/ss-fail.out" '--preset readback' 'the manual command names the preset'
contains "$ROOT/ss-fail.out" "$TRANSCRIPT" 'the manual command names the session file'
contains "$ROOT/ss-fail.out" "readback-sess-1-N.md" 'the manual command names the target file'

# 9. a subagent's own transcript is used, never the parent's.
PARENT=$ROOT/transcripts/sess/subagents
mkdir -p "$PARENT" "$ROOT/transcripts"
python3 - "$ROOT/transcripts/parent-session.jsonl" "$PARENT/agent-child1.jsonl" <<'PY'
import json
import sys

def record(path, fill):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "assistant", "uuid": "a1",
                                 "timestamp": "2026-11-15T09:00:00Z",
                                 "message": {"role": "assistant", "id": "m1",
                                             "content": [{"type": "text", "text": "x"}],
                                             "usage": {"input_tokens": fill}}}) + "\n")

record(sys.argv[1], 250_000)
record(sys.argv[2], 120_000)
PY
run "$ROOT/sub.out" post-tool-use \
  '{"session_id":"sess","transcript_path":"'"$ROOT/transcripts/parent-session.jsonl"'","agent_id":"child1","cwd":"'"$ROOT"'","agent_type":"general-purpose"}' \
  --state-dir "$STATE"
contains "$ROOT/sub.out" '120k' 'a subagent note uses its own transcript size'
not_contains '250k' 'a subagent note must not report the parent size' "$ROOT/sub.out"

# 10. the pressure ladder (module level): no note below 100k, one per 50k step,
#     a compaction (context shrank) re-arms it.
python3 - "$HOOKS" <<'PY' || fail 'the pressure ladder behaves wrongly'
import importlib.util, sys, tempfile
spec = importlib.util.spec_from_file_location("ctx_hooks", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from pathlib import Path
with tempfile.TemporaryDirectory() as tmp:
    state = Path(tmp)
    assert module.pressure_note(90_000, state, "main") is None
    first = module.pressure_note(105_000, state, "main")
    assert first is not None and "Context pressure is rising" in first, first
    assert module.pressure_note(120_000, state, "main") is None
    above = module.pressure_note(210_000, state, "main")
    assert above is not None and "semantic boundary" in above, above
    assert module.pressure_note(40_000, state, "main") is None
    again = module.pressure_note(105_000, state, "main")
    assert again is not None, again
PY

# 10b. role wording, module level: each role by its distinguishing phrase.
python3 - "$HOOKS" <<'PY' || fail 'the pressure wording does not differ by role'
import importlib.util, os, sys, tempfile
spec = importlib.util.spec_from_file_location("ctx_hooks", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from pathlib import Path
def note(fill, who, kind, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        return module.pressure_note(fill, Path(tmp), who, kind, **kw)
ctl_low = note(105_000, "c", "controller")
ctl_inform = note(200_000, "c", "controller")
ctl_urge = note(250_000, "c", "controller")
ctl_force = note(300_000, "c", "controller")
agent_low = note(150_000, "a", "spawned")
agent_soft = note(210_000, "a", "spawned")
# below 200k: persist and plan the stop, no instruction to compact yet (the AGENTS.md ladder bullet says the same)
assert "Context pressure is rising" in ctl_low and "from 200k on, plan a voluntary stop" in ctl_low, ctl_low
assert "compact-resume.sh" not in ctl_low and "FORCED" not in ctl_low and "STOP voluntarily" not in ctl_low, ctl_low
# 200k INFORM: a voluntary stop for compaction at the next semantic boundary, not yet forced
assert "[controller] Past 200k:" in ctl_inform and "reach a voluntary stop for compaction" in ctl_inform, ctl_inform
assert "good compaction point" in ctl_inform and "compact-resume.sh DETACHED on your own pane" in ctl_inform, ctl_inform
assert "FORCED" not in ctl_inform and "second reminder" not in ctl_inform, ctl_inform
# 250k: the same reminder with a stronger urge, and it names the forced yield
assert "[controller] Past 250k (second reminder, more urgent)" in ctl_urge and "STOP voluntarily" in ctl_urge, ctl_urge
assert "At 300k a yield is FORCED" in ctl_urge and "compact-resume.sh DETACHED" in ctl_urge, ctl_urge
# 300k: the forced yield, with the grace and what is still allowed
assert "FORCED YIELD: past 300k. 5 more tool calls pass, then every tool call is DENIED" in ctl_force, ctl_force
assert "compact-resume launch" in ctl_force and "Write/Edit under the state directory" in ctl_force, ctl_force
# the removed 500k/650k ceilings and the old wording are gone from every controller note
for text in (ctl_low, ctl_inform, ctl_urge, ctl_force):
    assert "500k" not in text and "650k" not in text and "ceiling" not in text and "set READY" not in text, text
assert agent_low.startswith("[ctx 150k") and "PARTIAL" not in agent_low and "[agent]" not in agent_low, agent_low
assert "finish or return PARTIAL with a handoff report" in agent_soft, agent_soft
assert "handover" not in agent_soft and "READY" not in agent_soft and "FORCED" not in agent_soft, agent_soft
# every role is told to weigh the same three things (AGENTS.md ladder); the marker-only agent note is not.
for text in (ctl_low, ctl_inform, ctl_urge, ctl_force, agent_soft):
    assert "Weigh the REMAINING EFFORT" in text and "prose readback alone" in text, text
assert "Weigh" not in agent_low, agent_low
# the thresholds are named values: the same fill flips the wording, through the environment or `limits`.
assert "FORCED YIELD" in note(260_000, "c", "controller", limits=(100_000, 150_000, 250_000, 2))
assert "2 more tool calls pass" in note(260_000, "c", "controller", limits=(100_000, 150_000, 250_000, 2))
assert "FORCED YIELD" not in note(300_000, "c", "controller", limits=(200_000, 250_000, 350_000, 5))
assert module.controller_limits() == (200_000, 250_000, 300_000, 5)
os.environ.update(CTX_INFORM_AT="150000", CTX_URGE_AT="x", CTX_FORCE_AT="-5", CTX_FORCE_GRACE="0")
assert module.controller_limits() == (150_000, 250_000, 300_000, 0)   # bad values keep the default; grace 0 is legal
# nit2: the three thresholds are kept in order (inform <= urge <= force), so an inverted pair
# cannot produce contradictory wording.
os.environ.update(CTX_INFORM_AT="260000", CTX_URGE_AT="200000", CTX_FORCE_AT="250000", CTX_FORCE_GRACE="5")
assert module.controller_limits() == (260_000, 260_000, 260_000, 5), module.controller_limits()
os.environ.update(CTX_INFORM_AT="200000", CTX_URGE_AT="240000", CTX_FORCE_AT="210000")
assert module.controller_limits() == (200_000, 240_000, 240_000, 5), module.controller_limits()
for name in ("CTX_INFORM_AT", "CTX_URGE_AT", "CTX_FORCE_AT", "CTX_FORCE_GRACE"):
    del os.environ[name]
assert "PARTIAL" in note(150_000, "a", "spawned", soft=120_000)

def kind(argv, hook):
    _, args = module.options(["post-tool-use", *argv])
    return module.session_kind(args, hook)

assert kind([], {}) == "controller"
assert kind([], {"agent_id": "x"}) == "spawned"
assert kind(["--session-kind", "spawned"], {}) == "spawned"
assert kind(["--session-kind", "controller"], {"agent_id": "x"}) == "controller"
assert kind(["--session-kind", "bogus"], {"agent_id": "x"}) == "spawned"
os.environ["CTX_HOOK_ROLE"] = "agent"
assert kind([], {}) == "spawned"
os.environ["CTX_HOOK_ROLE"] = "controller"
assert kind([], {"agent_id": "x"}) == "controller"
assert kind(["--session-kind", "spawned"], {}) == "spawned"   # the flag wins over the live hook's variable
PY

# 10c. end to end at fake-transcript sizes around the ladder's edges. `notes` runs
#      one post-tool-use in a fresh state dir and leaves the output in $ROOT/n.out.
cat > "$ROOT/mk.py" <<'PY'
import json, sys
path, fill = sys.argv[1], int(sys.argv[2])
model = sys.argv[3] if len(sys.argv) > 3 else "claude-opus-5-5"
with open(path, "w", encoding="utf-8") as handle:
    handle.write(json.dumps({"type": "assistant", "uuid": "a1", "timestamp": "2026-11-15T09:00:00Z",
                             "message": {"role": "assistant", "id": "m1", "model": model,
                                         "content": [{"type": "text", "text": "x"}],
                                         "usage": {"input_tokens": fill}}}) + "\n")
PY
SEQ=0
notes() { # notes <fill> <model> [extra hook args...]; env of the caller applies
  local fill=$1 model=$2
  shift 2
  SEQ=$((SEQ + 1))
  python3 "$ROOT/mk.py" "$ROOT/t$SEQ.jsonl" "$fill" "$model"
  run "$ROOT/n.out" post-tool-use \
    '{"session_id":"s'"$SEQ"'","transcript_path":"'"$ROOT/t$SEQ.jsonl"'","cwd":"'"$ROOT"'","tool_name":"Bash"}' \
    --state-dir "$ROOT/state-n$SEQ" "$@"
}
notes 99000 claude-opus-5-5
[[ ! -s $ROOT/n.out ]] || fail 'a controller at 99k got a note'
notes 151000 claude-opus-5-5
contains "$ROOT/n.out" 'Context pressure is rising' 'controller below 200k is told to persist and plan the stop'
contains "$ROOT/n.out" '[ctx 151k]' 'the free marker names the fill'
not_contains 'compact-resume.sh' 'controller below 200k is not told to compact yet' "$ROOT/n.out"
notes 199999 claude-opus-5-5
not_contains 'voluntary stop for compaction at the next' 'one token below 200000 is still the low wording' "$ROOT/n.out"
notes 200000 claude-opus-5-5
contains "$ROOT/n.out" 'reach a voluntary stop for compaction at the next semantic boundary' 'exactly 200000 INFORMs the controller'
contains "$ROOT/n.out" 'compact-resume.sh DETACHED' 'the INFORM note names the detached launch'
not_contains 'FORCED' 'the INFORM note does not force' "$ROOT/n.out"
notes 249999 claude-opus-5-5
not_contains 'more urgent' 'one token below 250000 is the first reminder' "$ROOT/n.out"
notes 250000 claude-opus-5-5
contains "$ROOT/n.out" 'second reminder, more urgent' 'exactly 250000 is the stronger reminder'
notes 299999 claude-opus-5-5
not_contains 'FORCED YIELD' 'one token below 300000 is not forced' "$ROOT/n.out"
notes 300000 claude-opus-5-5
contains "$ROOT/n.out" 'FORCED YIELD: past 300k. 5 more tool calls pass' 'exactly 300000 announces the forced yield and the grace'
notes 651000 claude-opus-5-5
contains "$ROOT/n.out" 'FORCED YIELD' 'far past 300k stays in the forced wording (no 500k/650k tier)'
not_contains 'set READY' 'the old handover wording is gone' "$ROOT/n.out"
CTX_HOOK_ROLE=agent notes 99000 claude-opus-5-5
[[ ! -s $ROOT/n.out ]] || fail 'an agent at 99k got a note'
CTX_HOOK_ROLE=agent notes 151000 claude-opus-5-5
contains "$ROOT/n.out" '[ctx 151k]' 'an agent below its soft threshold still gets the marker'
not_contains 'PARTIAL' 'an agent below 200k is not told to wrap up' "$ROOT/n.out"
CTX_HOOK_ROLE=agent notes 201000 claude-opus-5-5
contains "$ROOT/n.out" 'finish or return PARTIAL with a handoff report' 'an agent at 200k is told to finish or return PARTIAL'
not_contains 'READY' 'an agent is not told to write a controller handover' "$ROOT/n.out"
# the controller thresholds come from named variables; the agent keeps CTX_SOFT_AGENT.
CTX_FORCE_AT=400000 CTX_URGE_AT=350000 notes 320000 claude-opus-5-5
not_contains 'FORCED YIELD' 'CTX_FORCE_AT moves the forced yield' "$ROOT/n.out"
CTX_FORCE_AT=garbage notes 300000 claude-opus-5-5
contains "$ROOT/n.out" 'FORCED YIELD' 'an invalid CTX_FORCE_AT falls back to 300000'
CTX_FORCE_GRACE=2 notes 300000 claude-opus-5-5
contains "$ROOT/n.out" '2 more tool calls pass' 'CTX_FORCE_GRACE sets the grace'
CTX_HOOK_ROLE=agent notes 301000 claude-opus-5-5
not_contains 'FORCED' 'an agent is never forced' "$ROOT/n.out"
CTX_HOOK_ROLE=agent CTX_SOFT_AGENT=120000 notes 151000 claude-opus-5-5
contains "$ROOT/n.out" 'PARTIAL' 'CTX_SOFT_AGENT lowers the agent threshold'

# 10d0. the growth marker counts calls to the forced yield (300k), never to a settings.json window or
#       a 500k ceiling; past the forced yield it prints no calls-left figure.
marker_probe() { # marker_probe <tag> <first fill> <second fill>: the second run's growth marker
  local tag=$1 json
  json='{"session_id":"mark-'"$tag"'","transcript_path":"'"$ROOT/mark-$tag.jsonl"'","cwd":"'"$ROOT"'"}'
  python3 "$ROOT/mk.py" "$ROOT/mark-$tag.jsonl" "$2"
  hook post-tool-use "$json" --state-dir "$ROOT/state-mark-$tag" > /dev/null
  python3 "$ROOT/mk.py" "$ROOT/mark-$tag.jsonl" "$3"
  hook post-tool-use "$json" --state-dir "$ROOT/state-mark-$tag"
}
printf '%s' '{"autoCompactWindow":900000}' > "$HOME/.claude/settings.json"
# 151k right after a 101k marker = 50000 tokens/call; calls left = (300k - 151k) / 50000.
marker_probe a 101000 151000 > "$ROOT/mark-a.out"
contains "$ROOT/mark-a.out" '~2 calls to the 300k forced yield at this rate' 'the marker counts calls to the forced yield'
not_contains 'ceiling' 'no ceiling in the marker, whatever settings.json says' "$ROOT/mark-a.out"
marker_probe b 251000 301000 > "$ROOT/mark-b.out"
contains "$ROOT/mark-b.out" '+50k over' 'the marker still shows the growth'
not_contains 'calls to the' 'past the forced yield there is no calls-left figure' "$ROOT/mark-b.out"
rm -f -- "$HOME/.claude/settings.json"

# 10d0b. no ceiling or autoCompactWindow reaches the thresholds: a 400k or 900k window changes nothing.
#        Exact boundaries of the agent soft threshold (200000).
mkdir -p "$ROOT/h-big/.claude"
printf '%s' '{"autoCompactWindow":400000,"modelSettings":{"claude-sonnet-5-5":{"autoCompactWindow":400000}}}' > "$ROOT/h-big/.claude/settings.json"
HOME=$ROOT/h-big notes 299999 claude-sonnet-5-5
not_contains 'FORCED YIELD' 'a 400k window does not move the forced yield below 300k' "$ROOT/n.out"
HOME=$ROOT/h-big notes 300000 claude-sonnet-5-5
contains "$ROOT/n.out" 'FORCED YIELD' 'a 400k window still forces at exactly 300k'
not_contains 'lowered from' 'no threshold is lowered by a window' "$ROOT/n.out"
CTX_HOOK_ROLE=agent notes 199999 claude-opus-5-5
not_contains 'PARTIAL' 'one token below 200000 is below the agent soft threshold' "$ROOT/n.out"
CTX_HOOK_ROLE=agent notes 200000 claude-opus-5-5
contains "$ROOT/n.out" 'PARTIAL' 'exactly 200000 is at the agent soft threshold'
contains "$ROOT/n.out" 'Weigh the REMAINING EFFORT' 'the agent note carries the weigh sentence'
compgen -G "$ROOT/state-n$SEQ/hooks/pressure-*" > /dev/null || fail 'pressure state files are not under <state>/hooks/'
! compgen -G "$ROOT/state-n$SEQ/pressure-*" > /dev/null || fail 'a pressure state file landed in the state dir root'

# 10d1. a controller past 200k whose HANDOFF.md is older than 15 minutes is warned;
#       a fresh file, an agent, and a fill below 200k are not.
mkdir -p "$ROOT/state-stale"
printf '## RESUME HERE #1\n' > "$ROOT/state-stale/HANDOFF.md"
touch -d '30 minutes ago' "$ROOT/state-stale/HANDOFF.md"
python3 "$ROOT/mk.py" "$ROOT/stale.jsonl" 251000
STALE_JSON='{"session_id":"st","transcript_path":"'"$ROOT/stale.jsonl"'","cwd":"'"$ROOT"'"}'
stale_run() { # stale_run <tag> [extra args...]
  local tag=$1
  shift
  rm -rf "$ROOT/state-stale/hooks/pressure-"*
  run "$ROOT/stale-$tag.out" post-tool-use "$STALE_JSON" --state-dir "$ROOT/state-stale" "$@"
}
stale_run old
contains "$ROOT/stale-old.out" 'STALE STATE: HANDOFF.md was last written at' 'a 30-minute-old handover is flagged to a controller'
matches "$ROOT/stale-old.out" '\(3[0-9] min ago\)' 'the warning names the age'
touch "$ROOT/state-stale/HANDOFF.md"
stale_run fresh
not_contains 'STALE STATE' 'a fresh handover is not flagged' "$ROOT/stale-fresh.out"
touch -d '5 minutes ago' "$ROOT/state-stale/HANDOFF.md"
stale_run edge
not_contains 'STALE STATE' 'a handover under the 15-minute limit is not flagged' "$ROOT/stale-edge.out"
touch -d '30 minutes ago' "$ROOT/state-stale/HANDOFF.md"
stale_run agent --session-kind spawned
not_contains 'STALE STATE' 'an agent is never told to append to the controller handover' "$ROOT/stale-agent.out"
python3 "$ROOT/mk.py" "$ROOT/stale.jsonl" 151000
stale_run low
not_contains 'STALE STATE' 'below 200k there is no stale warning' "$ROOT/stale-low.out"
python3 "$ROOT/mk.py" "$ROOT/stale.jsonl" 251000
stale_run nofile --handoff-file "$ROOT/does-not-exist.md"
not_contains 'STALE STATE' 'a missing handover file is not flagged' "$ROOT/stale-nofile.out"
# exact boundaries: the handover is 30 minutes old (stale) in both runs; 200000 is the stale threshold.
python3 "$ROOT/mk.py" "$ROOT/stale.jsonl" 199999
stale_run just-below
not_contains 'STALE STATE' 'one token below 200000 there is no stale warning' "$ROOT/stale-just-below.out"
python3 "$ROOT/mk.py" "$ROOT/stale.jsonl" 200000
stale_run at-200k
contains "$ROOT/stale-at-200k.out" 'STALE STATE' 'exactly 200000 gets the stale warning'
# the 15-minute limit, with a pinned clock: 14 min 59 s is fresh, exactly 15 min is stale.
python3 - "$HOOKS" <<'PY' || fail 'the 15-minute stale boundary is wrong'
import importlib.util, os, sys, tempfile
spec = importlib.util.spec_from_file_location("ctx_hooks", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from pathlib import Path
base = 1_000_000_000
with tempfile.TemporaryDirectory() as tmp:
    handoff = Path(tmp) / "HANDOFF.md"
    handoff.write_text("x")
    os.utime(handoff, (base, base))
    for seconds, stale in ((899, False), (900, True), (960, True)):
        module.time.time = lambda s=seconds: base + s
        text = module.pressure_note(250_000, Path(tmp) / f"s{seconds}", "main", handoff=handoff)
        assert ("STALE STATE" in text) == stale, (seconds, text)
        assert module.handoff_age(handoff)[1] == seconds // 60
PY

# 10d2. the read order is taken from the newest RESUME HERE block's READ FIRST line.
mkdir -p "$ROOT/state-ro"
cat > "$ROOT/state-ro/HANDOFF.md" <<'MD'
## RESUME HERE #1 (older)
READ FIRST: OLD-LIST-MARKER
## RESUME HERE #2 (2026-11-24T10:00Z, ctl, Opus) SUPERSEDES #1
FIRST ACTION: none
READ FIRST: (1) docs/NEW-LIST-MARKER-ONE.md; (2) plan.yaml item XYZ-7
  (3) .controller-state/operator-decisions.md last 2 sections
AFTER A RESTART OF THE ENVIRONMENT: RESTART-SECTION-MARKER
(#2 10:30Z) update line UPDATE-LINE-MARKER
MD
run "$ROOT/ro.out" session-start "$MAIN_JSON" --state-dir "$ROOT/state-ro" --extract "$BIN/fake-extract"
contains "$ROOT/ro.out" 'NEW-LIST-MARKER-ONE' 'the read order is the newest block READ FIRST list'
contains "$ROOT/ro.out" 'last 2 sections' 'a continuation line of the READ FIRST list is kept'
contains "$ROOT/ro.out" 'RESUME HERE #2' 'the output names the block it was taken from'
contains "$ROOT/ro.out" 'FIRST ACTION: none' 'the block FIRST ACTION line is printed (05b nit 4)'
contains "$ROOT/ro.out" "$ROOT/scripts/controller/resume-read.sh main" 'the main controller is pointed at resume-read.sh main'
contains "$ROOT/ro.out" 'scripts/controller/resume-read.sh <topic>' 'a sub-controller is pointed at resume-read.sh <topic>'
contains "$ROOT/ro.out" 'FIRST COMMAND is ONE tool call' 'resume-read.sh is the single first command'
contains "$ROOT/ro.out" 'PRIME-main-controller.md' 'the live pointer to the main controller PRIME file is carried'
contains "$ROOT/ro.out" 'PROGRAM.md section 1-3 headings' 'the live pointer to PROGRAM.md section 1-3 is carried'
contains "$ROOT/ro.out" 'ListAgents' 'the live reality-check wording is carried'
contains "$ROOT/ro.out" 'write your state file at the next boundary' 'the live closing line is carried'
not_contains 'OLD-LIST-MARKER' 'an older block is never used' "$ROOT/ro.out"
not_contains 'RESTART-SECTION-MARKER' 'the list ends where the next section starts' "$ROOT/ro.out"
not_contains 'PROGRESS-last' 'no plan item id is hard-coded in the hook' "$ROOT/ro.out"
mkdir -p "$ROOT/state-ro2"
printf '## RESUME HERE #3 (no list here)\nFIRST ACTION: none\n' > "$ROOT/state-ro2/HANDOFF.md"
run "$ROOT/ro2.out" session-start "$MAIN_JSON" --state-dir "$ROOT/state-ro2" --extract "$BIN/fake-extract"
contains "$ROOT/ro2.out" 'FALLBACK' 'a block without READ FIRST is said to be a fallback'
contains "$ROOT/ro2.out" 'FIRST ACTION: none' 'the FIRST ACTION line is printed in the fallback too'
contains "$ROOT/ro2.out" 'HANDOFF.md' 'the fallback names the handover file'
# state dir found by walking up from the hook's cwd (a cd into a worktree).
mkdir -p "$ROOT/proj/.controller-state" "$ROOT/proj/.worktrees/w1/scripts"
printf '## RESUME HERE #9\nREAD FIRST: WALKUP-MARKER\n' > "$ROOT/proj/.controller-state/HANDOFF.md"
CTX_STATE_DIR='' run "$ROOT/walk.out" session-start \
  '{"session_id":"w","cwd":"'"$ROOT/proj/.worktrees/w1/scripts"'","source":"compact"}' --extract "$BIN/fake-extract"
contains "$ROOT/walk.out" 'WALKUP-MARKER' 'the state directory is found above a worktree cwd'
CTX_HANDOFF_FILE="$ROOT/state-ro/HANDOFF.md" run "$ROOT/envh.out" session-start "$MAIN_JSON" \
  --state-dir "$ROOT/state-ro2" --extract "$BIN/fake-extract"
contains "$ROOT/envh.out" 'NEW-LIST-MARKER-ONE' 'CTX_HANDOFF_FILE points the hook at another handover file'

# 10d3. PreCompact tells the summariser how old the handover is and what merged since.
GREPO=$ROOT/grepo
git init -q "$GREPO"
GITENV=(-c user.email=t@example.invalid -c user.name=t -c commit.gpgsign=false)
OLD_DATE=$(date -u -d '3 hours ago' +%Y-%m-%dT%H:%M:%SZ)
GIT_COMMITTER_DATE=$OLD_DATE GIT_AUTHOR_DATE=$OLD_DATE git -C "$GREPO" "${GITENV[@]}" commit -q --allow-empty -m 'old-merge-subject' \
  || fail 'could not create the old fixture commit'
git -C "$GREPO" "${GITENV[@]}" commit -q --allow-empty -m 'recent-merge-subject' || fail 'could not create the recent fixture commit'
[[ $(git -C "$GREPO" rev-list --count HEAD) == 2 ]] || fail 'the fixture repository does not hold both commits'
# a side branch merged with --no-ff (only the merge is first-parent), then origin/main is pinned and
# HEAD moves on, so a list built from HEAD or without --first-parent would show the extra subjects.
MAINB=$(git -C "$GREPO" symbolic-ref --short HEAD)
git -C "$GREPO" checkout -q -b side-branch
git -C "$GREPO" "${GITENV[@]}" commit -q --allow-empty -m 'side-branch-subject' || fail 'could not create the side commit'
git -C "$GREPO" checkout -q "$MAINB"
git -C "$GREPO" "${GITENV[@]}" merge -q --no-ff -m 'merged-side-subject' side-branch || fail 'could not create the merge'
git -C "$GREPO" update-ref refs/remotes/origin/main HEAD
git -C "$GREPO" "${GITENV[@]}" commit -q --allow-empty -m 'head-only-subject' || fail 'could not create the head-only commit'
mkdir -p "$ROOT/state-pc"
printf '## RESUME HERE #1\n' > "$ROOT/state-pc/HANDOFF.md"
touch -d '30 minutes ago' "$ROOT/state-pc/HANDOFF.md"
run "$ROOT/pcn.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' --state-dir "$ROOT/state-pc" --extract "$BIN/fake-extract" --repo-root "$GREPO"
contains "$ROOT/pcn.out" "HANDOFF.md was last written at" 'the summariser is told when the handover was written'
matches "$ROOT/pcn.out" '\(3[0-9] min before this compaction\)' 'the summariser is told its age'
contains "$ROOT/pcn.out" 'recent-merge-subject' 'a merge since the handover is listed'
not_contains 'old-merge-subject' 'a merge older than the handover is not listed' "$ROOT/pcn.out"
contains "$ROOT/pcn.out" 'merged-side-subject' 'the merge commit of a side branch is listed'
not_contains 'side-branch-subject' 'a commit inside a merged side branch is not first-parent' "$ROOT/pcn.out"
not_contains 'head-only-subject' 'the list is read from origin/main, not from HEAD' "$ROOT/pcn.out"

# 10e. statusline and stop carry the fill.
python3 "$ROOT/mk.py" "$ROOT/sl.jsonl" 201000
run "$ROOT/sl.out" statusline '{"transcript_path":"'"$ROOT/sl.jsonl"'"}'
contains "$ROOT/sl.out" 'ctx 201k' 'statusline prints the fill'
run "$ROOT/sp.out" stop '{"transcript_path":"'"$ROOT/sl.jsonl"'"}'
contains "$ROOT/sp.out" 'ctx 201k]' 'stop prints the fill in its systemMessage'

# 10d. an unexpected crash inside run() is logged, exits 0, and a session-start
#      crash still tells the session how to build the readback by hand.
python3 - "$HOOKS" "$ROOT/crash.log" <<'PY' || fail 'a crash inside run() escaped or went unlogged'
import importlib.util, io, sys, contextlib, json
spec = importlib.util.spec_from_file_location("ctx_hooks", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from pathlib import Path
def boom(mode, args):
    module.ACTIVE_LOG = Path(sys.argv[2])
    raise RuntimeError("boom")
module.run = boom
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert module.main(["session-start"]) == 0
assert "build the readback by hand" in out.getvalue(), out.getvalue()
assert module.main(["pre-compact"]) == 0
lines = [json.loads(x) for x in open(sys.argv[2], encoding="utf-8")]
assert lines and lines[-1]["ev"] == "hook-error" and "boom" in lines[-1]["err"], lines
PY
# a non-string transcript_path is ignored rather than crashing the hook.
run "$ROOT/badtype.out" session-start '{"transcript_path":123,"cwd":["a"],"agent_id":{"x":1},"source":5}' \
  --state-dir "$ROOT/state-badtype"
contains "$ROOT/badtype.out" 'CONTROLLER RESUME' 'a malformed hook input still prints the read order'

# 11. the pressure note is delivered as PostToolUse additionalContext JSON.
run "$ROOT/ptu.out" post-tool-use \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","tool_name":"Bash"}' \
  --state-dir "$STATE"
python3 - "$ROOT/ptu.out" <<'PY' || fail 'the pressure note is not valid PostToolUse JSON'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
hook_output = payload["hookSpecificOutput"]
assert hook_output["hookEventName"] == "PostToolUse", payload
assert "Context pressure is rising" in hook_output["additionalContext"], payload
PY

# 12. statusline and stop are quiet, valid, and never raise.
run "$ROOT/status.out" statusline '{}'
contains "$ROOT/status.out" 'ctx ?' 'statusline without a transcript still answers'
run "$ROOT/stop.out" stop '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'"}'
python3 - "$ROOT/stop.out" <<'PY' || fail 'the stop marker is not valid JSON'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert "ctx" in payload["systemMessage"], payload
PY

# 13. no raise: garbage stdin, an unknown mode and a bad flag all exit 0.
printf 'not json at all' | python3 "$HOOKS" pre-compact > "$ROOT/garbage.out" 2>&1 \
  || fail 'garbage stdin failed the hook'
run "$ROOT/unknown.out" not-a-mode '{}'
printf '{}' | python3 "$HOOKS" session-start --bad-flag > "$ROOT/badflag.out" 2>&1 \
  || fail 'a bad flag failed the hook'

# 14. a controller readback carries NO goal anchor (its anchor is plan.yaml);
#     a spawned session's controller can pin one with --goal-file.
not_contains '--goal-prompt' 'a controller readback must not carry the first prompt as a goal' \
  "$FAKE_EXTRACT_LOG"
GOAL=$ROOT/goal.md
printf 'Hold the release line until the report is signed.\n' > "$GOAL"
run "$ROOT/pc-goal.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"manual"}' \
  --state-dir "$ROOT/state-goal" --extract "$BIN/fake-extract" --goal-file "$GOAL"
contains "$FAKE_EXTRACT_LOG" "--goal-file $GOAL" 'an optional goal file is forwarded to the extract'
export FAKE_EXTRACT_RC=2
run "$ROOT/pc-goalfail.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$ROOT/state-goalfail" --extract "$BIN/fake-extract" --goal-file "$GOAL"
unset FAKE_EXTRACT_RC
run "$ROOT/ss-goalfail.out" session-start "$MAIN_JSON" --state-dir "$ROOT/state-goalfail" \
  --extract "$BIN/fake-extract" --goal-file "$GOAL"
contains "$ROOT/ss-goalfail.out" "--goal-file $GOAL" 'the manual command keeps the pinned goal'

# 14a. the goal anchor: only a SPAWNED session (kind) or an explicit flag/env
#      asks for it; --goal-file wins over it.
PC_JSON='{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}'
: > "$FAKE_EXTRACT_LOG"
run "$ROOT/pc-spawned.out" pre-compact "$PC_JSON" --state-dir "$ROOT/state-k1" \
  --extract "$BIN/fake-extract" --session-kind spawned
contains "$FAKE_EXTRACT_LOG" '--goal-prompt' 'a spawned session asks for its goal prompt'
: > "$FAKE_EXTRACT_LOG"
run "$ROOT/pc-flag.out" pre-compact "$PC_JSON" --state-dir "$ROOT/state-k2" \
  --extract "$BIN/fake-extract" --goal-prompt
contains "$FAKE_EXTRACT_LOG" '--goal-prompt' 'an explicit flag asks for the goal prompt'
: > "$FAKE_EXTRACT_LOG"
CTX_READBACK_GOAL_PROMPT=1 run "$ROOT/pc-env.out" pre-compact "$PC_JSON" \
  --state-dir "$ROOT/state-k3" --extract "$BIN/fake-extract"
contains "$FAKE_EXTRACT_LOG" '--goal-prompt' 'the env switch asks for the goal prompt'
: > "$FAKE_EXTRACT_LOG"
run "$ROOT/pc-both.out" pre-compact "$PC_JSON" --state-dir "$ROOT/state-k4" \
  --extract "$BIN/fake-extract" --goal-prompt --goal-file "$GOAL"
not_contains '--goal-prompt' 'a pinned goal file wins over the first prompt' "$FAKE_EXTRACT_LOG"
contains "$FAKE_EXTRACT_LOG" "--goal-file $GOAL" 'the pinned goal file is forwarded'
: > "$FAKE_EXTRACT_LOG"
run "$ROOT/pc-ctl.out" pre-compact "$PC_JSON" --state-dir "$ROOT/state-k5" \
  --extract "$BIN/fake-extract" --session-kind controller
not_contains '--goal-prompt' 'an explicit controller never gets the first prompt' "$FAKE_EXTRACT_LOG"

# 14d. a PreCompact fired inside a subagent builds nothing into the controller's
#      state (unless a kind is named explicitly); it still exits 0.
: > "$FAKE_EXTRACT_LOG"
SUB_PC='{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto","agent_id":"child9"}'
run "$ROOT/pc-sub.out" pre-compact "$SUB_PC" --state-dir "$ROOT/state-sub" \
  --extract "$BIN/fake-extract"
[[ ! -s $FAKE_EXTRACT_LOG ]] || fail 'a subagent compaction ran the extract'
! compgen -G "$ROOT/state-sub/readback-state*" > /dev/null || fail 'a subagent compaction touched the readback state'
! compgen -G "$ROOT/state-sub/hooks/readback-state*" > /dev/null || fail 'a subagent compaction wrote a readback state under hooks/'
contains "$ROOT/state-sub/hooks/hook-log.jsonl" 'subagent compaction' 'the skipped subagent compaction is logged'
SUB_OWN='{"session_id":"sess","transcript_path":"'"$ROOT/transcripts/parent-session.jsonl"'","cwd":"'"$ROOT"'","trigger":"auto","agent_id":"child1"}'
run "$ROOT/pc-sub2.out" pre-compact "$SUB_OWN" --state-dir "$ROOT/state-sub2" \
  --extract "$BIN/fake-extract" --session-kind spawned
contains "$FAKE_EXTRACT_LOG" "agent-child1.jsonl" 'an explicit kind builds from the subagent own transcript'
contains "$FAKE_EXTRACT_LOG" '--goal-prompt' 'an explicit spawned kind asks for the goal prompt'

# 14e. redaction patterns reach the extract (flag and env), and a pattern -- which
#      can itself be a secret -- is never logged, stored or printed.
: > "$FAKE_EXTRACT_LOG"
CTX_READBACK_REDACT_PATTERNS=$'env-pattern-secret-1\n\nenv-pattern-secret-2' \
  run "$ROOT/pc-redact.out" pre-compact "$PC_JSON" --state-dir "$ROOT/state-redact" \
  --extract "$BIN/fake-extract" --redact-pattern flag-pattern-secret
contains "$FAKE_EXTRACT_LOG" '--redact-pattern flag-pattern-secret' 'the flag pattern reaches the extract'
contains "$FAKE_EXTRACT_LOG" '--redact-pattern env-pattern-secret-1' 'the first env pattern reaches the extract'
contains "$FAKE_EXTRACT_LOG" '--redact-pattern env-pattern-secret-2' 'the second env pattern reaches the extract'
[[ $(grep -o -- '--redact-pattern' "$FAKE_EXTRACT_LOG" | wc -l) == 3 ]] || fail 'a blank env line became a pattern'
not_contains 'pattern-secret' 'a redaction pattern must never be stored or logged' \
  "$ROOT/state-redact/hooks/readback-state-sess-1.json" "$ROOT/state-redact/hooks/hook-log.jsonl" \
  "$ROOT/pc-redact.out"
contains "$ROOT/state-redact/hooks/readback-state-sess-1.json" '<REGEX>' 'the stored command shows a placeholder'
export FAKE_EXTRACT_RC=2
CTX_READBACK_REDACT_PATTERNS='env-pattern-secret-1' run "$ROOT/pc-redact-fail.out" pre-compact "$PC_JSON" \
  --state-dir "$ROOT/state-redact-fail" --extract "$BIN/fake-extract"
unset FAKE_EXTRACT_RC
CTX_READBACK_REDACT_PATTERNS='env-pattern-secret-1' run "$ROOT/ss-redact.out" session-start "$MAIN_JSON" \
  --state-dir "$ROOT/state-redact-fail" --extract "$BIN/fake-extract"
contains "$ROOT/ss-redact.out" '--redact-pattern <REGEX>' 'the manual command names the redaction flag'
not_contains 'pattern-secret' 'the manual command must not print a pattern' "$ROOT/ss-redact.out"

# 14b. an extract that reports `none` (an empty delta) must NOT overwrite the
#      stored marker: the next readback would otherwise restart from scratch.
export FAKE_MARKER=none
run "$ROOT/pc-nonemarker.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$ROOT/state-goal" --extract "$BIN/fake-extract"
unset FAKE_MARKER
python3 - "$ROOT/state-goal/hooks/readback-state-sess-1.json" <<'PY' || fail 'a none marker replaced the stored marker'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["marker"] == "fake-marker-7", state
assert state["ok"] is True and state["n"] == 2, state
PY

# 14c. the SessionStart output is capped under the harness's 10,000 characters
#      even when the stored failure reason is enormous.
mkdir -p "$ROOT/state-huge/hooks"
python3 - "$ROOT/state-huge/hooks/readback-state-sess-1.json" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"ok": False, "reason": "x" * 30_000, "marker": "m1"}, handle)
PY
run "$ROOT/ss-huge.out" session-start "$MAIN_JSON" --state-dir "$ROOT/state-huge" \
  --extract "$BIN/fake-extract"
python3 - "$ROOT/ss-huge.out" <<'PY' || fail 'an enormous failure reason overflowed the SessionStart cap'
import sys
text = open(sys.argv[1], encoding="utf-8").read()
assert len(text) <= 10000, len(text)
assert text.startswith("CONTROLLER RESUME"), text[:80]
PY

# 16. readback state is per session: two sessions share one state dir and never see
#     each other's files, markers or numbers; a failed build is never covered by an
#     older readback; a session that never built one gets the manual command.
PS=$ROOT/state-ps
mkdir -p "$PS"
printf 'legacy readback of another session\n' > "$PS/readback-8.md"
pc_json() { printf '{"session_id":"%s","transcript_path":"%s","cwd":"%s","trigger":"auto"}' "$1" "$TRANSCRIPT" "$ROOT"; }
ss_json() { printf '{"session_id":"%s","transcript_path":"%s","cwd":"%s","source":"compact"}' "$1" "$TRANSCRIPT" "$ROOT"; }
run "$ROOT/ps-a1.out" pre-compact "$(pc_json sess-A)" --state-dir "$PS" --extract "$BIN/fake-extract"
: > "$FAKE_EXTRACT_LOG"
run "$ROOT/ps-b1.out" pre-compact "$(pc_json sess-B)" --state-dir "$PS" --extract "$BIN/fake-extract"
[[ -f $PS/readback-sess-A-1.md && -f $PS/readback-sess-B-1.md ]] || fail 'each session did not get its own readback file'
not_contains '--since' 'session B must not start from session A marker' "$FAKE_EXTRACT_LOG"
run "$ROOT/ps-a2.out" pre-compact "$(pc_json sess-A)" --state-dir "$PS" --extract "$BIN/fake-extract"
[[ -f $PS/readback-sess-A-2.md && ! -e $PS/readback-sess-B-2.md ]] || fail 'readback numbers are not per session'
run "$ROOT/ps-ssa.out" session-start "$(ss_json sess-A)" --state-dir "$PS" --extract "$BIN/fake-extract"
contains "$ROOT/ps-ssa.out" "$PS/readback-sess-A-2.md" 'session A is pointed at its own newest readback'
not_contains 'readback-sess-B' 'session A is never pointed at session B' "$ROOT/ps-ssa.out"
not_contains 'readback-8.md' 'a legacy session-less readback is never printed' "$ROOT/ps-ssa.out"
run "$ROOT/ps-ssb.out" session-start "$(ss_json sess-B)" --state-dir "$PS" --extract "$BIN/fake-extract"
contains "$ROOT/ps-ssb.out" "$PS/readback-sess-B-1.md" 'session B is pointed at its own readback'
FAKE_EXTRACT_RC=2 run "$ROOT/ps-a3.out" pre-compact "$(pc_json sess-A)" --state-dir "$PS" --extract "$BIN/fake-extract"
run "$ROOT/ps-ssa2.out" session-start "$(ss_json sess-A)" --state-dir "$PS" --extract "$BIN/fake-extract"
contains "$ROOT/ps-ssa2.out" 'LAST BUILD FAILED' 'a failed build is reported'
contains "$ROOT/ps-ssa2.out" 'BUILD IT BY HAND' 'a failed build yields the manual command'
not_contains 'NEWEST READBACK' 'a stale readback is not printed after a failed build' "$ROOT/ps-ssa2.out"
not_contains 'readback-sess-A-2.md' 'the older readback is not named after a failed build' "$ROOT/ps-ssa2.out"
run "$ROOT/ps-ssb2.out" session-start "$(ss_json sess-B)" --state-dir "$PS" --extract "$BIN/fake-extract"
contains "$ROOT/ps-ssb2.out" "$PS/readback-sess-B-1.md" 'session B is unaffected by session A failing'
run "$ROOT/ps-ssc.out" session-start "$(ss_json sess-C)" --state-dir "$PS" --extract "$BIN/fake-extract"
contains "$ROOT/ps-ssc.out" 'BUILD IT BY HAND' 'a session that never built one gets the manual command'
not_contains 'NEWEST READBACK' 'a session without a build is never given another readback' "$ROOT/ps-ssc.out"

# 16b. the role rule precedes the main controller's list; a non-writer session is told not to follow it.
python3 - "$ROOT/ro.out" <<'PY' || fail 'the role rule is missing or comes after the list'
import sys
text = open(sys.argv[1], encoding="utf-8").read()
for needle in ("MAIN CONTROLLER (vbpub-main-controller)", "SUB-CONTROLLER vbpub-<topic>", "/STATE.md",
               "-successor.md", "ANY OTHER SESSION", "do not act as a controller", "is NOT yours"):
    assert needle in text, needle
assert text.index("Find your role") < text.index("NEW-LIST-MARKER-ONE"), text
PY
CTX_MAIN_CONTROLLER=vbpub-other run "$ROOT/ro3.out" session-start "$MAIN_JSON" --state-dir "$ROOT/state-ro" --extract "$BIN/fake-extract"
contains "$ROOT/ro3.out" 'MAIN CONTROLLER (vbpub-other)' 'the main controller name is configurable'

# 16c. CTX_HOOK_ROLE=agent skips the readback build like a subagent; an explicit kind still builds.
: > "$FAKE_EXTRACT_LOG"
CTX_HOOK_ROLE=agent run "$ROOT/pc-roleenv.out" pre-compact "$(pc_json sess-R)" --state-dir "$ROOT/state-roleenv" \
  --extract "$BIN/fake-extract"
[[ ! -s $FAKE_EXTRACT_LOG ]] || fail 'CTX_HOOK_ROLE=agent ran the extract'
! compgen -G "$ROOT/state-roleenv/readback-*" > /dev/null || fail 'CTX_HOOK_ROLE=agent wrote a readback'
! compgen -G "$ROOT/state-roleenv/hooks/readback-*" > /dev/null || fail 'CTX_HOOK_ROLE=agent wrote readback state under hooks/'
contains "$ROOT/state-roleenv/hooks/hook-log.jsonl" 'subagent compaction' 'the skipped build is logged'
CTX_HOOK_ROLE=agent run "$ROOT/pc-roleenv2.out" pre-compact "$(pc_json sess-R)" --state-dir "$ROOT/state-roleenv" \
  --extract "$BIN/fake-extract" --session-kind spawned
[[ -f $ROOT/state-roleenv/readback-sess-R-1.md ]] || fail 'an explicit session kind did not build'

# 17. no .controller-state above the cwd: fall back to the repo holding the script; with none, write nothing.
SOLO=$ROOT/solo
mkdir -p "$SOLO/scripts/controller/hooks" "$ROOT/elsewhere"
cp -- "$HOOKS" "$SOLO/scripts/controller/hooks/ctx-hooks.py"
python3 "$ROOT/mk.py" "$ROOT/solo.jsonl" 151000
SOLO_JSON='{"session_id":"solo","transcript_path":"'"$ROOT/solo.jsonl"'","cwd":"'"$ROOT/elsewhere"'"}'
printf '%s' "$SOLO_JSON" | CTX_STATE_DIR='' python3 "$SOLO/scripts/controller/hooks/ctx-hooks.py" post-tool-use > "$ROOT/solo1.out" 2>&1 \
  || fail 'a hook with no state directory failed'
contains "$ROOT/solo1.out" 'Context pressure is rising' 'the note is still delivered without a state directory'
[[ ! -e $ROOT/elsewhere/.controller-state && ! -e $SOLO/.controller-state ]] || fail 'the hook created a state directory'
mkdir -p "$SOLO/.controller-state"
printf '%s' "$SOLO_JSON" | CTX_STATE_DIR='' python3 "$SOLO/scripts/controller/hooks/ctx-hooks.py" post-tool-use > "$ROOT/solo2.out" 2>&1 \
  || fail 'the script-repo fallback failed'
[[ -f $SOLO/.controller-state/hooks/hook-log.jsonl ]] || fail 'the script repo state directory was not used'
[[ ! -e $ROOT/elsewhere/.controller-state ]] || fail 'the hook wrote under the cwd'

# 18. pre-tool-use, the forced yield: 5 grace calls from 300k, then every call is denied except the
#     handover itself (Write/Edit under the state dir or plan.yaml, the compact-resume launch).
PT=$ROOT/state-pt
mkdir -p "$PT"
python3 "$ROOT/mk.py" "$ROOT/pt-hi.jsonl" 300000
python3 "$ROOT/mk.py" "$ROOT/pt-lo.jsonl" 299999
pt() { # pt <transcript> <sid> <tool> <tool_input json> [more args]: output in $ROOT/pt.out
  local t=$1 sid=$2 tool=$3 input=$4
  shift 4
  run "$ROOT/pt.out" pre-tool-use \
    '{"session_id":"'"$sid"'","transcript_path":"'"$t"'","cwd":"'"$ROOT"'","tool_name":"'"$tool"'","tool_input":'"$input"'}' \
    --state-dir "$PT" "$@"
}
is_denied() { python3 - "$ROOT/pt.out" <<'PY'
import json, sys
out = json.load(open(sys.argv[1], encoding="utf-8"))["hookSpecificOutput"]
assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny", out
text = out["permissionDecisionReason"]
for needle in ("FORCED YIELD", "300k", "5-call grace", "write the handover", "scripts/controller/compact-resume.sh",
               "DETACHED", "end the turn"):
    assert needle in text, (needle, text)
PY
}
# is_denied_with <needle>...: a deny, plus the caller's own needles (the launch template, H1/M1).
is_denied_with() {
  is_denied || return 1
  local needle
  for needle in "$@"; do
    grep -qF -- "$needle" "$ROOT/pt.out" || fail "the deny reason does not name: $needle"
  done
}
is_allowed() { [[ ! -s $ROOT/pt.out ]]; }
pt "$ROOT/pt-lo.jsonl" s1 Bash '{"command":"ls"}'
is_allowed || fail 'a call below 300k was not passed silently'
for i in 1 2 3 4 5; do
  pt "$ROOT/pt-hi.jsonl" s1 Bash '{"command":"ls"}'
  is_allowed || fail "grace call $i at 300k was denied"
done
pt "$ROOT/pt-hi.jsonl" s1 Bash '{"command":"ls"}'
is_denied || fail 'the 6th call at 300k was not denied with the yield instruction'
pt "$ROOT/pt-hi.jsonl" s1 Read '{"file_path":"/etc/hostname"}'
is_denied || fail 'a Read after the grace was not denied'
contains "$PT/hooks/hook-log.jsonl" '"ev": "force-deny"' 'a deny is logged'
# the allow-list: the handover itself still passes
pt "$ROOT/pt-hi.jsonl" s1 Write '{"file_path":"'"$PT"'/HANDOFF.md","content":"x"}'
is_allowed || fail 'a handover write under the state directory was denied'
pt "$ROOT/pt-hi.jsonl" s1 Edit '{"file_path":"'"$PT"'/handovers/vbpub-x-successor.md","old_string":"a","new_string":"b"}'
is_allowed || fail 'an Edit of a handover file was denied'
pt "$ROOT/pt-hi.jsonl" s1 Write '{"file_path":"'"$ROOT"'/plan.yaml","content":"x"}'
is_allowed || fail 'a plan.yaml sync was denied'
pt "$ROOT/pt-hi.jsonl" s1 Write '{"file_path":"'"$ROOT"'/src/code.py","content":"x"}'
is_denied || fail 'a write outside the state directory was allowed'
pt "$ROOT/pt-hi.jsonl" s1 Write '{"file_path":"'"$PT"'/../src/escape.md","content":"x"}'
is_denied || fail 'a .. escape out of the state directory was allowed'
pt "$ROOT/pt-hi.jsonl" s1 Write '{"file_path":"'"$PT"'-sibling/x.md","content":"x"}'
is_denied || fail 'a sibling directory sharing the state directory prefix was allowed'
# The launch must be THIS hook's own scripts/controller/compact-resume.sh (M1): a copy under the
# state directory, another checkout's copy, or any second command after the launch is refused. The
# path comes from the hook under test (a mutant copy resolves to its own directory).
LAUNCH_SCRIPT=$(python3 - "$HOOKS" <<'PY'
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("ctx_hooks", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(Path(module.controller_dir()) / "compact-resume.sh")
PY
)
CLEAR_SCRIPT=${LAUNCH_SCRIPT%compact-resume.sh}clear-resume.sh
SCRATCH=$ROOT/scratch
mkdir -p "$SCRATCH"
# shellcheck disable=SC2016 # the fixtures carry a literal $TMUX_PANE and $(id)
for launch in \
  "setsid nohup $LAUNCH_SCRIPT \"\$TMUX_PANE\" $PT/compact-guidance.txt $PT/compact-prompt.txt $PT/launch.log >/dev/null 2>&1 &" \
  "nohup $LAUNCH_SCRIPT %13 g.md p.md $PT/launch.log > $PT/launch.log 2>&1 &" \
  "$CLEAR_SCRIPT %4 $PT/prompt.md $PT/launch.log" \
  "bash $LAUNCH_SCRIPT %13 g.md p.md $PT/launch.log"; do
  pt "$ROOT/pt-hi.jsonl" s1 Bash '{"command":"'"${launch//\"/\\\"}"'"}'
  is_allowed || fail "the compact-resume launch was denied: $launch"
done
# the deny reason carries a copy-paste template with the state dir in it (H1)
pt "$ROOT/pt-hi.jsonl" s1 Bash '{"command":"ls"}'
is_denied_with "$LAUNCH_SCRIPT" "$PT/compact-guidance.txt" "under the state dir" "ONE bare command" \
  || fail 'the deny reason does not carry the launch template of H1'
# shellcheck disable=SC2016
for bad in \
  "$LAUNCH_SCRIPT %13 g p l; rm -rf /x" \
  "$LAUNCH_SCRIPT %13 g p l && ls" \
  "ls | $LAUNCH_SCRIPT %13 g p l" \
  "echo $LAUNCH_SCRIPT %13" \
  'compact-resume.sh %13 g p l' \
  "$LAUNCH_SCRIPT-evil %13" \
  "$LAUNCH_SCRIPT \$(id) g p l" \
  "$LAUNCH_SCRIPT %13 g p l & git push origin main" \
  "$LAUNCH_SCRIPT & ls" \
  "$LAUNCH_SCRIPT %13 g p l > $HOME/.bashrc" \
  "$PT/scripts/controller/compact-resume.sh %13 g p l" \
  "$ROOT/scripts/controller/compact-resume.sh %13 g p l" \
  'ls
'"$LAUNCH_SCRIPT"' %13 g p l'; do
  pt "$ROOT/pt-hi.jsonl" s1 Bash "$(python3 -c 'import json,sys; print(json.dumps({"command": sys.argv[1]}))' "$bad")"
  is_denied || fail "a Bash call that is not just the launch was allowed: $bad"
done
# a lone `&` is a command separator (M1): each of these chains a second command onto the launch.
# shellcheck disable=SC2016
for chained in \
  "$LAUNCH_SCRIPT %13 g p l & rm -rf $ROOT/victim &" \
  "setsid nohup $LAUNCH_SCRIPT %13 g p l > /dev/null & echo done &"; do
  pt "$ROOT/pt-hi.jsonl" s1 Bash "$(python3 -c 'import json,sys; print(json.dumps({"command": sys.argv[1]}))' "$chained")"
  is_denied || fail "a lone '&' chained a second command past the allow-list: $chained"
done
# The H1 allow-list: what an interactive controller really needs once the grace is used. Read is a
# READ-ONLY pass (the harness refuses an Edit of a file this conversation did not Read, and
# resume-read.sh prints HANDOFF.md through a Bash call, so no Read is registered for it).
pt "$ROOT/pt-hi.jsonl" s1 Read '{"file_path":"'"$PT"'/HANDOFF.md"}'
is_allowed || fail 'a Read of HANDOFF.md under the state directory was denied (H1)'
pt "$ROOT/pt-hi.jsonl" s1 Read '{"file_path":"'"$PT"'/../state-pt/plan.yaml"}'
is_allowed || fail 'a Read with a ../ segment inside the state directory was denied'
pt "$ROOT/pt-hi.jsonl" s1 Read '{"file_path":"'"$ROOT"'/plan.yaml"}'
is_allowed || fail 'a Read of plan.yaml was denied'
pt "$ROOT/pt-hi.jsonl" s1 Read '{"file_path":"'"$LAUNCH_SCRIPT"'"}'
is_allowed || fail 'a Read under scripts/controller/ was denied'
pt "$ROOT/pt-hi.jsonl" s1 Read '{"file_path":"'"$ROOT"'/src/code.py"}'
is_denied || fail 'a Read outside the allowed roots was allowed'
pt "$ROOT/pt-hi.jsonl" s1 Read '{"file_path":"'"$PT"'-sibling/HANDOFF.md"}'
is_denied || fail 'a Read of a sibling of the state directory was allowed'
# Skill: the controller-handover skill is what the deny tells the session to load.
pt "$ROOT/pt-hi.jsonl" s1 Skill '{"skill":"controller-handover"}'
is_allowed || fail 'the controller-handover skill was denied (H1)'
pt "$ROOT/pt-hi.jsonl" s1 Skill '{"skill":"assay-cli"}'
is_denied || fail 'an unrelated skill was allowed'
# Write/Edit under the hook input's scratchpad_dir (real hook input carries it).
pt_scratch() { # pt_scratch <tool> <file_path>
  run "$ROOT/pt.out" pre-tool-use \
    '{"session_id":"s1","transcript_path":"'"$ROOT/pt-hi.jsonl"'","cwd":"'"$ROOT"'","scratchpad_dir":"'"$SCRATCH"'","tool_name":"'"$1"'","tool_input":{"file_path":"'"$2"'","content":"x"}}' \
    --state-dir "$PT"
}
pt_scratch Write "$SCRATCH/compact-guidance.txt"
is_allowed || fail 'a Write to the scratchpad dir was denied (H1)'
pt_scratch Edit "$SCRATCH/sub/prompt.txt"
is_allowed || fail 'an Edit inside the scratchpad dir was denied'
pt_scratch Write "$ROOT/src/code.py"
is_denied || fail 'a Write outside the state dir and the scratchpad was allowed'
pt_scratch Write "$SCRATCH-sibling/x.md"
is_denied || fail 'a sibling of the scratchpad dir was allowed'
# MultiEdit is the same handover write as Edit.
pt "$ROOT/pt-hi.jsonl" s1 MultiEdit '{"file_path":"'"$PT"'/HANDOFF.md","edits":[{"old_string":"a","new_string":"b"}]}'
is_allowed || fail 'a MultiEdit of the handover was denied'
pt "$ROOT/pt-hi.jsonl" s1 MultiEdit '{"file_path":"'"$ROOT"'/src/code.py","edits":[]}'
is_denied || fail 'a MultiEdit outside the state directory was allowed'
# fail open: a tool_input that is not an object, a missing file_path, and a garbage payload.
pt "$ROOT/pt-hi.jsonl" s1 Bash '{"command":["ls","-l"]}'
is_denied || fail 'a non-string command was allowed (it must just be denied, not crash)'
pt "$ROOT/pt-hi.jsonl" s1 Write '{"file_path":null}'
is_denied || fail 'a null file_path was allowed'
pt "$ROOT/pt-hi.jsonl" s1 Write '{}'
is_denied || fail 'a write with no file_path was allowed'
pt "$ROOT/pt-hi.jsonl" s1 Bash 'not-an-object'
is_allowed || fail 'stdio that is not hook JSON must deny nothing (the fail-open)'
[[ ! -s $ROOT/pt-garbage.out ]] || fail 'a null tool_input was denied (the fail-open must hold)'
[[ ! -s $ROOT/pt-garbage2.out ]] || fail 'garbage stdin was denied (the fail-open must hold)'

# allowed calls are not counted: 3 handover writes, then 5 plain calls still pass and the 6th is denied
for i in 1 2 3; do pt "$ROOT/pt-hi.jsonl" s2 Write '{"file_path":"'"$PT"'/HANDOFF.md","content":"x"}'; done
for i in 1 2 3 4 5; do
  pt "$ROOT/pt-hi.jsonl" s2 Bash '{"command":"ls"}'
  is_allowed || fail "plain call $i after handover writes was denied"
done
pt "$ROOT/pt-hi.jsonl" s2 Bash '{"command":"ls"}'
is_denied || fail 'handover writes were counted against the grace'
# the countdown rides on post-tool-use; falling below 300k (a compaction) resets the count
pt "$ROOT/pt-hi.jsonl" s3 Bash '{"command":"ls"}'
pt "$ROOT/pt-hi.jsonl" s3 Bash '{"command":"ls"}'
PT_POST='{"session_id":"s3","transcript_path":"'"$ROOT/pt-hi.jsonl"'","cwd":"'"$ROOT"'","tool_name":"Bash"}'
run "$ROOT/pt-post1.out" post-tool-use "$PT_POST" --state-dir "$PT"
# L4: the 300k step note prints what is LEFT (2 of the 5 grace calls are used), like the countdown,
# not the full grace.
contains "$ROOT/pt-post1.out" 'FORCED YIELD: past 300k. 3 more tool calls pass' 'the 300k note announces the grace that is left'
run "$ROOT/pt-post2.out" post-tool-use "$PT_POST" --state-dir "$PT"
contains "$ROOT/pt-post2.out" '3 grace tool calls left' 'every call past 300k counts the grace down'
pt "$ROOT/pt-lo.jsonl" s3 Bash '{"command":"ls"}'
[[ ! -e $PT/hooks/force-s3-main.json ]] || fail 'the force count survived a compaction'
for i in 1 2 3 4 5; do pt "$ROOT/pt-hi.jsonl" s3 Bash '{"command":"ls"}'; is_allowed || fail "grace call $i after a reset was denied"; done
# not for agents, not without a readable fill, grace 0 and a moved threshold
for i in 1 2 3 4 5 6 7; do
  CTX_HOOK_ROLE=agent pt "$ROOT/pt-hi.jsonl" s4 Bash '{"command":"ls"}'
  is_allowed || fail 'an agent session was denied'
done
pt "$ROOT/pt-hi.jsonl" s5 Bash '{"command":"ls"}' --session-kind spawned
is_allowed || fail 'a spawned session was denied'
for i in 1 2 3 4 5 6 7; do pt "$ROOT/no-such-transcript.jsonl" s6 Bash '{"command":"ls"}'; is_allowed || fail 'an unreadable fill denied a call'; done
CTX_FORCE_GRACE=0 pt "$ROOT/pt-hi.jsonl" s7 Bash '{"command":"ls"}'
contains "$ROOT/pt.out" '"permissionDecision": "deny"' 'CTX_FORCE_GRACE=0 did not deny the first call'
contains "$ROOT/pt.out" '0-call grace' 'the deny names the configured grace'
CTX_FORCE_AT=350000 pt "$ROOT/pt-hi.jsonl" s8 Bash '{"command":"ls"}'
is_allowed || fail 'CTX_FORCE_AT did not move the forced yield'

# 18a. M2/L1: the fill is never read from the pre-compaction tail, and the grace count of the
#      fill band that ended is dropped by pre-compact and by session-start.
python3 - "$ROOT/pt-compacted.jsonl" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(json.dumps({"type": "assistant", "uuid": "old", "timestamp": "2026-11-24T18:00:00Z",
                             "message": {"role": "assistant", "id": "mold", "model": "claude-opus-5-5",
                                         "content": [{"type": "text", "text": "pre-compaction"}],
                                         "usage": {"input_tokens": 320_000}}}) + "\n")
    handle.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                             "timestamp": "2026-11-24T18:21:00Z"}) + "\n")
    # the attachments the harness re-adds right after the boundary
    for line in ("file-a", "file-b", "file-c"):
        handle.write(json.dumps({"type": "user", "message": {"role": "user", "content": line}}) + "\n")
PY
python3 - "$HOOKS" "$ROOT/pt-compacted.jsonl" <<'PY' || fail 'the fill is still read across a compact_boundary (M2)'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ctx_hooks", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.last_assistant(sys.argv[2]) == (None, None), module.last_assistant(sys.argv[2])
PY
# the grace is exhausted (used 9 > 5): the ONLY thing that lets this call pass is the boundary
# stop in last_assistant, which makes the fill unknown -> fail open.
printf '{"used": 9, "at": 320000}' > "$PT/hooks/force-sC-main.json"
run "$ROOT/pt-c1.out" pre-tool-use '{"session_id":"sC","transcript_path":"'"$ROOT/pt-compacted.jsonl"'","cwd":"'"$ROOT"'","tool_name":"Bash","tool_input":{"command":"scripts/controller/resume-read.sh main"}}' --state-dir "$PT"
[[ ! -s $ROOT/pt-c1.out ]] || fail 'the first call after a compaction was denied on the pre-compaction fill (M2)'
[[ -e $PT/hooks/force-sC-main.json ]] || fail 'an unknown fill must not consume or clear the grace count'
# a force file with the grace used is dropped by pre-compact and by session-start (both, so the
# reset does not depend on which of the two hooks fires)
force_file=$PT/hooks/force-sess-1-main.json
printf '{"used": 9, "at": 300000}' > "$force_file"
run "$ROOT/pt-pc.out" pre-compact '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' --state-dir "$PT" --extract "$BIN/fake-extract"
[[ ! -e $force_file ]] || fail 'pre-compact did not drop the grace count of the ending fill band (M2)'
printf '{"used": 9, "at": 300000}' > "$force_file"
run "$ROOT/pt-ss.out" session-start "$MAIN_JSON" --state-dir "$PT" --extract "$BIN/fake-extract"
[[ ! -e $force_file ]] || fail 'session-start did not drop the grace count of the ending fill band (M2)'
# another session's force file is untouched by both
printf '{"used": 9, "at": 300000}' > "$PT/hooks/force-sess-OTHER-main.json"
run "$ROOT/pt-pc2.out" pre-compact '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' --state-dir "$PT" --extract "$BIN/fake-extract"
[[ -f $PT/hooks/force-sess-OTHER-main.json ]] || fail 'pre-compact dropped another session grace count'
# L2: the grace count is incremented under a lock, so parallel tool calls of one message cannot
# lose increments (12 concurrent increments must end at 12 exactly).
python3 - "$HOOKS" "$ROOT/state-race" <<'PY' || fail 'the grace counter lost increments under concurrency (L2)'
import importlib.util, subprocess, sys
from pathlib import Path
code = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('ctx_hooks', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.bump_force_count(__import__('pathlib').Path(sys.argv[2]), 'race', 'main', 320000))
"""
state = Path(sys.argv[2])
state.mkdir(parents=True, exist_ok=True)
procs = [subprocess.Popen([sys.executable, "-c", code, sys.argv[1], str(state)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(12)]
seen = sorted(int(p.communicate()[0].decode().strip()) for p in procs)
assert seen == list(range(1, 13)), seen
spec = importlib.util.spec_from_file_location("ctx_hooks", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.force_count(state, 'race', 'main') == 12, module.force_count(state, 'race', 'main')
PY
# L1: a <synthetic> record and an all-zero usage record are not a context reading
python3 - "$ROOT/pt-synthetic.jsonl" <<'PY'
import json, sys
records = [
    {"type": "assistant", "uuid": "real", "timestamp": "2026-11-24T17:00:00Z",
     "message": {"role": "assistant", "id": "mreal", "model": "claude-opus-5-5",
                 "content": [{"type": "text", "text": "real"}],
                 "usage": {"input_tokens": 260_000}}},
    {"type": "assistant", "uuid": "s1", "timestamp": "2026-11-24T17:01:00Z",
     "message": {"role": "assistant", "id": "ms1", "model": "<synthetic>",
                 "stop_reason": "tool_use", "content": [{"type": "text", "text": "synthetic"}],
                 "usage": {"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}},
    {"type": "assistant", "uuid": "s2", "timestamp": "2026-11-24T17:02:00Z",
     "message": {"role": "assistant", "id": "ms2", "model": "claude-opus-5-5",
                 "content": [{"type": "text", "text": "zero"}],
                 "usage": {"input_tokens": 0}}},
]
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    for record in records:
        handle.write(json.dumps(record) + "\n")
PY
python3 - "$HOOKS" "$ROOT/pt-synthetic.jsonl" <<'PY' || fail 'a synthetic or all-zero record still resets the fill (L1)'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ctx_hooks", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fill, model = module.last_assistant(sys.argv[2])
assert (fill, model) == (260_000, "claude-opus-5-5"), (fill, model)
PY
run "$ROOT/pt-syn.out" post-tool-use '{"session_id":"sS","transcript_path":"'"$ROOT/pt-synthetic.jsonl"'","cwd":"'"$ROOT"'","tool_name":"Bash"}' --state-dir "$PT"
contains "$ROOT/pt-syn.out" 'ctx 260k' 'the note reports the last real fill, not the synthetic record (L1)'
not_contains 'ctx 0k' 'a zero-usage synthetic record is never the fill' "$ROOT/pt-syn.out"

# 18b. pre-compact prunes: the newest 3 readbacks per session stay, old pressure/force/readback-state files go.
PR=$ROOT/state-prune
mkdir -p "$PR/hooks"
for n in 1 2 3 4 5; do printf x > "$PR/readback-sess-P-$n.md"; done
printf x > "$PR/readback-other-1.md"
printf x > "$PR/hooks/pressure-new.json"
printf x > "$PR/hooks/hook-log.jsonl"
for old in pressure-old force-old readback-state-old; do printf x > "$PR/hooks/$old.json"; touch -d '30 days ago' "$PR/hooks/$old.json"; done
touch -d '30 days ago' "$PR/hooks/hook-log.jsonl"
run "$ROOT/prune.out" pre-compact "$(pc_json sess-P)" --state-dir "$PR" --extract "$BIN/fake-extract"
for n in 4 5 6; do [[ -f $PR/readback-sess-P-$n.md ]] || fail "readback $n was pruned"; done
for n in 1 2 3; do [[ ! -e $PR/readback-sess-P-$n.md ]] || fail "readback $n was not pruned"; done
[[ -f $PR/readback-other-1.md ]] || fail 'another session readback was pruned'
for old in pressure-old force-old readback-state-old; do [[ ! -e $PR/hooks/$old.json ]] || fail "$old.json was not pruned"; done
[[ -f $PR/hooks/pressure-new.json && -f $PR/hooks/hook-log.jsonl && -f $PR/hooks/readback-state-sess-P.json ]] \
  || fail 'pruning removed a recent file or the hook log'
# a readback state written by the old hook (in the state root) is still honoured
mkdir -p "$ROOT/state-legacy"
printf 'old readback\n' > "$ROOT/state-legacy/readback-sess-L-3.md"
printf '{"ok": true, "n": 3, "path": "%s", "marker": "legacy-marker"}' "$ROOT/state-legacy/readback-sess-L-3.md" > "$ROOT/state-legacy/readback-state-sess-L.json"
run "$ROOT/legacy.out" session-start "$(ss_json sess-L)" --state-dir "$ROOT/state-legacy" --extract "$BIN/fake-extract"
contains "$ROOT/legacy.out" "$ROOT/state-legacy/readback-sess-L-3.md" 'a readback state in the old location is still read'
# a call that names no state directory writes to the test default, never to a checkout's live state
contains "$ROOT/state-default/hooks/hook-log.jsonl" 'unknown-mode' 'a call without --state-dir logged to the throwaway default'

# 15. the hook never writes a secret into its log or its output.
not_contains 'secret-token-fixture' 'the hook must not leak extract output into the log' \
  "$ROOT/pc1.out" "$STATE/hooks/hook-log.jsonl"

echo 'PASS controller ctx hooks: readback build, marker, numbering, failure, timeout, subagent, 200/250/300k ladder, forced yield, prune'
