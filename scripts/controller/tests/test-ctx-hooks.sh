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
    assert first is not None and "never stop or wait for a compaction" in first, first
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
ctl_soft = note(660_000, "c", "controller")
agent_low = note(150_000, "a", "spawned")
agent_soft = note(210_000, "a", "spawned")
assert "never stop or wait for a compaction" in ctl_low, ctl_low
assert "end the turn" not in ctl_low and "READY" not in ctl_low, ctl_low
assert "write the handover (controller-handover skill)" in ctl_soft, ctl_soft
assert "set READY" in ctl_soft and "then end the turn" in ctl_soft, ctl_soft
assert "never stop" not in ctl_soft and "continue" not in ctl_soft.lower(), ctl_soft
assert agent_low.startswith("[ctx 150k") and "PARTIAL" not in agent_low and "[agent]" not in agent_low, agent_low
assert "finish or return PARTIAL with a handoff report" in agent_soft, agent_soft
assert "handover" not in agent_soft and "READY" not in agent_soft, agent_soft
# the soft threshold is a parameter: the same fill flips the wording.
assert "set READY" in note(300_000, "c", "controller", soft=250_000)
assert "set READY" not in note(300_000, "c", "controller", soft=350_000)
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
contains "$ROOT/n.out" 'never stop or wait for a compaction' 'controller below soft is told to continue'
contains "$ROOT/n.out" '[ctx 151k]' 'the free marker names the fill'
not_contains 'then end the turn' 'controller below soft is never told to end' "$ROOT/n.out"
notes 201000 claude-opus-5-5
contains "$ROOT/n.out" 'never stop or wait for a compaction' 'controller at 201k is still below soft'
notes 651000 claude-opus-5-5
contains "$ROOT/n.out" 'write the handover (controller-handover skill)' 'controller at soft writes the handover'
contains "$ROOT/n.out" 'set READY, then end the turn' 'controller at soft ends the turn'
not_contains 'never stop' 'controller at soft is not told to keep going' "$ROOT/n.out"
CTX_HOOK_ROLE=agent notes 99000 claude-opus-5-5
[[ ! -s $ROOT/n.out ]] || fail 'an agent at 99k got a note'
CTX_HOOK_ROLE=agent notes 151000 claude-opus-5-5
contains "$ROOT/n.out" '[ctx 151k]' 'an agent below its soft threshold still gets the marker'
not_contains 'PARTIAL' 'an agent below 200k is not told to wrap up' "$ROOT/n.out"
CTX_HOOK_ROLE=agent notes 201000 claude-opus-5-5
contains "$ROOT/n.out" 'finish or return PARTIAL with a handoff report' 'an agent at 200k is told to finish or return PARTIAL'
not_contains 'READY' 'an agent is not told to write a controller handover' "$ROOT/n.out"
# one env name per kind, and the controller's old name stays an alias.
CTX_SOFT_CONTROLLER=300000 notes 351000 claude-opus-5-5
contains "$ROOT/n.out" 'set READY' 'CTX_SOFT_CONTROLLER lowers the controller threshold'
CTX_CONTROLLER_SOFT=300000 notes 351000 claude-opus-5-5
contains "$ROOT/n.out" 'set READY' 'CTX_CONTROLLER_SOFT still works as an alias'
CTX_SOFT_CONTROLLER=900000 CTX_CONTROLLER_SOFT=300000 notes 351000 claude-opus-5-5
not_contains 'set READY' 'the canonical name wins over the alias' "$ROOT/n.out"
CTX_SOFT_CONTROLLER=garbage notes 651000 claude-opus-5-5
contains "$ROOT/n.out" 'set READY' 'an invalid threshold falls back to 650000'
CTX_HOOK_ROLE=agent CTX_SOFT_AGENT=120000 notes 151000 claude-opus-5-5
contains "$ROOT/n.out" 'PARTIAL' 'CTX_SOFT_AGENT lowers the agent threshold'

# 10d0. the ceiling in the growth line: modelSettings.<model>.autoCompactWindow,
#       else the top-level one, else the documented default; a bad file never raises.
ceiling_probe() { # ceiling_probe <home> <model> <tag>: prints "<k> ceiling" read off the growth marker
  local home=$1 model=$2 tag=$3
  local st=$ROOT/state-ceil-$tag
  local json='{"session_id":"ceil-'"$tag"'","transcript_path":"'"$ROOT/ceil-$tag.jsonl"'","cwd":"'"$ROOT"'"}'
  python3 "$ROOT/mk.py" "$ROOT/ceil-$tag.jsonl" 101000 "$model"
  HOME=$home hook post-tool-use "$json" --state-dir "$st" > /dev/null
  python3 "$ROOT/mk.py" "$ROOT/ceil-$tag.jsonl" 120000 "$model"
  for _ in 1 2 3 4; do HOME=$home hook post-tool-use "$json" --state-dir "$st" > /dev/null; done
  python3 "$ROOT/mk.py" "$ROOT/ceil-$tag.jsonl" 151000 "$model"
  HOME=$home hook post-tool-use "$json" --state-dir "$st" | grep -o '~[0-9]* calls to the [0-9]*k ceiling'
}
mkdir -p "$ROOT/h-model/.claude" "$ROOT/h-top/.claude" "$ROOT/h-none" "$ROOT/h-bad/.claude" "$ROOT/h-badwin/.claude"
printf '%s' '{"autoCompactWindow":600000,"modelSettings":{"claude-opus-5-5":{"autoCompactWindow":800000},"claude-sonnet-5-5":{"autoCompactWindow":400000}}}' > "$ROOT/h-model/.claude/settings.json"
printf '%s' '{"autoCompactWindow":600000}' > "$ROOT/h-top/.claude/settings.json"
printf '%s' '{"modelSettings": [' > "$ROOT/h-bad/.claude/settings.json"
printf '%s' '{"autoCompactWindow":600000,"modelSettings":{"claude-opus-5-5":{"autoCompactWindow":"big"}}}' > "$ROOT/h-badwin/.claude/settings.json"
# 151k after a 101k marker over 5 calls = 10000 tokens/call; calls left = (ceiling - 151k) / 10000.
[[ $(ceiling_probe "$ROOT/h-model" claude-opus-5-5 a) == '~64 calls to the 800k ceiling' ]] || fail 'the model-specific ceiling (opus) is not used'
[[ $(ceiling_probe "$ROOT/h-model" claude-sonnet-5-5 b) == '~24 calls to the 400k ceiling' ]] || fail 'the model-specific ceiling (sonnet) is not used'
[[ $(ceiling_probe "$ROOT/h-model" claude-unknown-9 c) == '~44 calls to the 600k ceiling' ]] || fail 'an unlisted model does not fall back to the top-level window'
[[ $(ceiling_probe "$ROOT/h-top" claude-opus-5-5 d) == '~44 calls to the 600k ceiling' ]] || fail 'the top-level window is not used'
[[ $(ceiling_probe "$ROOT/h-none" claude-opus-5-5 e) == '~34 calls to the 500k ceiling' ]] || fail 'a missing settings file does not give the documented default'
[[ $(ceiling_probe "$ROOT/h-bad" claude-opus-5-5 f) == '~34 calls to the 500k ceiling' ]] || fail 'a malformed settings file does not give the documented default'
[[ $(ceiling_probe "$ROOT/h-badwin" claude-opus-5-5 g) == '~44 calls to the 600k ceiling' ]] || fail 'a non-numeric model window does not fall through to the top-level one'

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
  rm -rf "$ROOT/state-stale/pressure-"*
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
not_contains 'OLD-LIST-MARKER' 'an older block is never used' "$ROOT/ro.out"
not_contains 'RESTART-SECTION-MARKER' 'the list ends where the next section starts' "$ROOT/ro.out"
not_contains 'PROGRESS-last' 'no plan item id is hard-coded in the hook' "$ROOT/ro.out"
mkdir -p "$ROOT/state-ro2"
printf '## RESUME HERE #3 (no list here)\nFIRST ACTION: none\n' > "$ROOT/state-ro2/HANDOFF.md"
run "$ROOT/ro2.out" session-start "$MAIN_JSON" --state-dir "$ROOT/state-ro2" --extract "$BIN/fake-extract"
contains "$ROOT/ro2.out" 'FALLBACK' 'a block without READ FIRST is said to be a fallback'
contains "$ROOT/ro2.out" 'HANDOFF.md' 'the fallback names the handover file'
# state dir found by walking up from the hook's cwd (a cd into a worktree).
mkdir -p "$ROOT/proj/.controller-state" "$ROOT/proj/.worktrees/w1/scripts"
printf '## RESUME HERE #9\nREAD FIRST: WALKUP-MARKER\n' > "$ROOT/proj/.controller-state/HANDOFF.md"
run "$ROOT/walk.out" session-start \
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
git -C "$GREPO" update-ref refs/remotes/origin/main HEAD
mkdir -p "$ROOT/state-pc"
printf '## RESUME HERE #1\n' > "$ROOT/state-pc/HANDOFF.md"
touch -d '30 minutes ago' "$ROOT/state-pc/HANDOFF.md"
run "$ROOT/pcn.out" pre-compact \
  '{"session_id":"sess-1","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$ROOT"'","trigger":"auto"}' --state-dir "$ROOT/state-pc" --extract "$BIN/fake-extract" --repo-root "$GREPO"
contains "$ROOT/pcn.out" "HANDOFF.md was last written at" 'the summariser is told when the handover was written'
matches "$ROOT/pcn.out" '\(3[0-9] min before this compaction\)' 'the summariser is told its age'
contains "$ROOT/pcn.out" 'recent-merge-subject' 'a merge since the handover is listed'
not_contains 'old-merge-subject' 'a merge older than the handover is not listed' "$ROOT/pcn.out"

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
assert "never stop or wait for a compaction" in hook_output["additionalContext"], payload
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
[[ ! -e $ROOT/state-sub/readback-state.json ]] || fail 'a subagent compaction touched the readback state'
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
  "$ROOT/state-redact/readback-state.json" "$ROOT/state-redact/hooks/hook-log.jsonl" \
  "$ROOT/pc-redact.out"
contains "$ROOT/state-redact/readback-state.json" '<REGEX>' 'the stored command shows a placeholder'
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
python3 - "$ROOT/state-goal/readback-state.json" <<'PY' || fail 'a none marker replaced the stored marker'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["marker"] == "fake-marker-7", state
assert state["ok"] is True and state["n"] == 2, state
PY

# 14c. the SessionStart output is capped under the harness's 10,000 characters
#      even when the stored failure reason is enormous.
mkdir -p "$ROOT/state-huge"
python3 - "$ROOT/state-huge/readback-state.json" <<'PY'
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

# 15. the hook never writes a secret into its log or its output.
not_contains 'secret-token-fixture' 'the hook must not leak extract output into the log' \
  "$ROOT/pc1.out" "$STATE/hooks/hook-log.jsonl"

echo 'PASS controller ctx hooks: readback build, marker, numbering, failure, timeout, subagent, pressure ladder'
