#!/usr/bin/env bash
# Offline tests for scripts/controller/hooks/ctx-hooks.py using fixture hook
# JSON and a fake extract command. No session, no network, no real transcript.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
HOOKS=${CTX_HOOKS_SCRIPT:-$SCRIPT_DIR/../hooks/ctx-hooks.py}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-ctx-hooks-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() {
  local needle=$1 description=$2
  shift 2
  ! grep -qF -- "$needle" "$@" || fail "$description (unexpected: $needle)"
}

STATE=$ROOT/state
TRANSCRIPT=$ROOT/session.jsonl
BIN=$ROOT/bin
mkdir -p "$STATE" "$BIN"

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

# 1. pre-compact success: readback-N.md written, marker stored, summariser note.
run "$ROOT/pc1.out" pre-compact '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
[[ -f $STATE/readback-1.md ]] || fail 'pre-compact did not write readback-1.md'
contains "$STATE/readback-1.md" 'Goal of the session' 'the readback document is stored'
contains "$ROOT/pc1.out" "session's ORIGINAL goal" 'summariser note asks for the goal'
contains "$ROOT/pc1.out" 'IN-FLIGHT list' 'summariser note asks for the in-flight list'
contains "$ROOT/pc1.out" 'readback-1.md' 'summariser note names the readback'
python3 - "$STATE/readback-state.json" <<'PY' || fail 'readback state did not record the marker'
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
[[ -f $STATE/readback-2.md ]] || fail 'second pre-compact did not write readback-2.md'
contains "$FAKE_EXTRACT_LOG" '--since fake-marker-7' 'the previous readback marker is used as the cut'

# 3. a lost state file cannot re-use a number (highest on disk wins).
rm -f -- "$STATE/readback-state.json"
run "$ROOT/pc3.out" pre-compact '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
[[ -f $STATE/readback-3.md ]] || fail 'readback numbering did not follow the files on disk'
[[ ! -e $STATE/readback-1.md ]] && fail 'the first readback vanished'

# 4. extract failure: exit 0, a log line, a durable failure, no readback file.
export FAKE_EXTRACT_RC=2
run "$ROOT/pc-fail.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' \
  --state-dir "$STATE" --extract "$BIN/fake-extract"
unset FAKE_EXTRACT_RC
contains "$ROOT/pc-fail.out" 'could not be built automatically' 'failure is announced to the summariser'
contains "$STATE/hooks/hook-log.jsonl" '"ev": "readback"' 'failure is logged'
python3 - "$STATE/readback-state.json" <<'PY' || fail 'a failed build was not recorded durably'
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

# 7. session-start[compact]: read order, newest readback path and size.
run "$ROOT/ss.out" session-start "$MAIN_JSON" --state-dir "$STATE" --extract "$BIN/fake-extract"
contains "$ROOT/ss.out" 'CONTROLLER RESUME' 'session-start prints the read order'
contains "$ROOT/ss.out" 'operator-decisions.md' 'the read order names the decision log'
contains "$ROOT/ss.out" 'HANDOFF.md' 'the read order names the handoff file'
contains "$ROOT/ss.out" "$STATE/readback-3.md" 'session-start names the newest readback'
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
contains "$ROOT/ss-fail.out" "readback-N.md" 'the manual command names the target file'

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

# 10. the pressure ladder: no note below 100k, one per 50k step, wording bands.
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
    assert first is not None and "finish the current unit of work" in first, first
    assert module.pressure_note(120_000, state, "main") is None
    above = module.pressure_note(210_000, state, "main")
    assert above is not None and "semantic boundary" in above, above
    # A compaction (context shrank) re-arms the ladder.
    assert module.pressure_note(40_000, state, "main") is None
    again = module.pressure_note(105_000, state, "main")
    assert again is not None, again
PY

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

# 15. the hook never writes a secret into its log or its output.
not_contains 'secret-token-fixture' 'the hook must not leak extract output into the log' \
  "$ROOT/pc1.out" "$STATE/hooks/hook-log.jsonl"

echo 'PASS controller ctx hooks: readback build, marker, numbering, failure, timeout, subagent, pressure ladder'
