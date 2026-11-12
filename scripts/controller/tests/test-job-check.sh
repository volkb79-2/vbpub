#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SRC=${JOB_CHECK_SCRIPT:-$SCRIPT_DIR/../job-check.sh}
# Sanitized event envelope from codex/runs/assay-wp3.jsonl; the original message is replaced.
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/job-check-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }

HOME_ROOT=$ROOT/controller
REPO=$ROOT/repo
CONTROLLER_LOGS_DIR=$ROOT/configured-logs
CONTROLLER_RUNS_DIR=$HOME_ROOT/runs
export CONTROLLER_LOGS_DIR CONTROLLER_RUNS_DIR
mkdir -p "$HOME_ROOT/runs" "$CONTROLLER_LOGS_DIR" "$REPO"
git -C "$REPO" init -q
git -C "$REPO" config user.name test
git -C "$REPO" config user.email test@example.invalid
mkdir -p "$REPO/reports" "$REPO/scripts/controller"
printf 'tracked report directory\n' > "$REPO/reports/README.md"
printf 'tracked nested report directory\n' > "$REPO/scripts/controller/README.md"
printf 'baseline\n' > "$REPO/base.txt"
git -C "$REPO" add base.txt reports scripts && git -C "$REPO" commit -qm baseline
BASE=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" update-ref refs/remotes/origin/main "$BASE"
git -C "$REPO" switch -qc task
printf 'implementation\n' > "$REPO/impl.txt"
git -C "$REPO" add impl.txt
git -C "$REPO" commit -qm 'implementation' -m 'Co-Authored-By: Codex gpt-6-luna <noreply@openai.com>'
git -C "$REPO" branch side
printf 'review-fix\n' > "$REPO/fix.txt"
git -C "$REPO" add fix.txt
git -C "$REPO" commit -qm 'review fix' -m 'Co-Authored-By: Codex gpt-6-luna <noreply@openai.com>'
git -C "$REPO" switch -q side
printf 'side\n' > "$REPO/side.txt"
git -C "$REPO" add side.txt
git -C "$REPO" commit -qm 'side change' -m 'Co-Authored-By: Codex gpt-6-luna <noreply@openai.com>'
git -C "$REPO" switch -q task
git -C "$REPO" merge --no-ff side -qm 'merge side' -m 'Co-Authored-By: Codex gpt-6-luna <noreply@openai.com>'

write_gate_log() {
  local head
  head=$(git -C "$REPO" rev-parse HEAD)
  printf "run-gate.commit=%s\nPASS ctl-status\nrun-gate: lane 'suite' verdict PASS; exit_code 0\n" \
    "$head" > "$CONTROLLER_LOGS_DIR/suite.log"
}
write_report() {
  local head
  head=$(git -C "$REPO" rev-parse HEAD)
  python3 - "$ROOT/report.md" "$head" <<'PY'
import pathlib, sys
pathlib.Path(sys.argv[1]).write_text("Reviewed head: " + sys.argv[2] + "\n" + "Review evidence.\n" * 100)
PY
}
write_reattach_evidence() {
  local head short container
  head=$(git -C "$REPO" rev-parse HEAD)
  short=${head:0:8}
  container="vbpub-run-gate-controller-suite-$short-261112-0101-123456789"
  rm -f -- "$CONTROLLER_LOGS_DIR"/*.log
  printf "run-gate: collected %s (exited 0 at 2026-11-12T01:01:01Z)\nPASS controller suite\nrun-gate: lane 'suite' verdict PASS; exit_code 0\n" \
    "$container" > "$CONTROLLER_LOGS_DIR/reattached.log"
  printf "run-gate: docker argv: docker run -d --name %s --label run-gate.commit=%s tester-unified:local bash -c suite\n" \
    "$container" "$head" > "$CONTROLLER_LOGS_DIR/original.log"
  python3 - "$ROOT/report.md" "$head" "$container" <<'PY'
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text(
    "Reviewed head: " + sys.argv[2] + "\n"
    + "Re-attached container: " + sys.argv[3] + "\n"
    + "Review evidence.\n" * 100
)
PY
}
write_gate_log
write_report

printf '{"type":"item.completed","item":{"type":"agent_message","text":"Implementation complete. Report: %s"}}\n{"type":"turn.completed"}\n' \
  "$ROOT/report.md" > "$HOME_ROOT/runs/healthy.jsonl"
printf 'EXIT=0\n' > "$HOME_ROOT/runs/healthy.done"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna "$ROOT/report.md" > "$ROOT/healthy.out" 2>&1 || {
    cat "$ROOT/healthy.out" >&2
    fail 'clean run with trailers on merge and regular commits should pass'
  }
contains "$ROOT/healthy.out" 'trailers OK (all commits incl. merges: luna)' 'trailer audit includes merge commits'
contains "$ROOT/healthy.out" 'JOB-CHECK: green' 'healthy job is green'
contains "$ROOT/healthy.out" 'last: Implementation complete. Report:' 'prototype final-message preview remains available'
contains "$ROOT/healthy.out" 'gate evidence:' 'gate evidence is bound to the branch head'
git -C "$REPO" switch -qc deepseek-task "$BASE"
printf 'deepseek implementation\n' > "$REPO/deepseek.txt"
git -C "$REPO" add deepseek.txt
git -C "$REPO" commit -qm 'DeepSeek route commit' -m 'Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>'
write_gate_log
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" deepseek > "$ROOT/route-deepseek.out" 2>&1 || {
    cat "$ROOT/route-deepseek.out" >&2
    fail 'deepseek route should require its own exact trailer'
  }
contains "$ROOT/route-deepseek.out" 'trailers OK (all commits incl. merges: deepseek)' 'deepseek route remains available'
git -C "$REPO" switch -q task
write_gate_log
printf "run-gate.commit=%s\nPASS ctl-status\nrun-gate: lane 'suite' verdict PASS; exit_code 0\n" \
  "$(git -C "$REPO" rev-parse HEAD)" > "$CONTROLLER_LOGS_DIR/suite.log"
printf "run-gate.commit=%s\nPASS ctl-status\nrun-gate: lane 'suite' verdict PASS; exit_code 0\n" \
  "$BASE" > "$CONTROLLER_LOGS_DIR/stale.log"
rm -f -- "$CONTROLLER_LOGS_DIR/suite.log"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" none > "$ROOT/stale-gate.out" 2>&1; then fail 'stale gate evidence was accepted'; fi
contains "$ROOT/stale-gate.out" 'GATE EVIDENCE MISSING' 'gate log must name the exact current head'
write_gate_log
printf "run-gate.commit=%s\nPASS ctl-status\nFAIL hardening\nrun-gate: lane 'suite' verdict FAIL; exit_code 1\n" \
  "$(git -C "$REPO" rev-parse HEAD)" > "$CONTROLLER_LOGS_DIR/failing.log"
rm -f -- "$CONTROLLER_LOGS_DIR/suite.log"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" none > "$ROOT/no-pass-gate.out" 2>&1; then fail 'gate evidence without a PASS line was accepted'; fi
contains "$ROOT/no-pass-gate.out" 'GATE EVIDENCE MISSING' 'a final FAIL verdict cannot be rescued by earlier PASS lines'
if grep -qF 'JOB-CHECK: green' "$ROOT/no-pass-gate.out"; then fail 'mixed PASS and final FAIL gate evidence printed green'; fi
write_gate_log
rm -f -- "$CONTROLLER_LOGS_DIR/failing.log"
python3 - "$ROOT/report.md" <<'PY'
import pathlib, sys
pathlib.Path(sys.argv[1]).write_text("Review evidence.\n" * 100)
PY
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna "$ROOT/report.md" > "$ROOT/stale-report.out" 2>&1; then
  fail 'report without the current head was accepted'
fi
contains "$ROOT/stale-report.out" 'REPORT DOES NOT NAME HEAD' 'report must name the current full or short head'
write_report
printf 'EXIT=0 AUTORESUME=2\n' > "$HOME_ROOT/runs/healthy.done"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna > "$ROOT/autoresume-done.out" 2>&1 || fail 'successful auto-resumed run marker was rejected'
contains "$ROOT/autoresume-done.out" 'JOB-CHECK: green' 'EXIT and AUTORESUME done markers are accepted'
printf 'EXIT=0\n' > "$HOME_ROOT/runs/healthy.done"
python3 - "$ROOT/report.md" "$(git -C "$REPO" rev-parse --short HEAD)" <<'PY'
import pathlib, sys
pathlib.Path(sys.argv[1]).write_text("Reviewed head: " + sys.argv[2] + "\n" + "Review evidence.\n" * 100)
PY
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" none "$ROOT/report.md" > "$ROOT/short-report-head.out" 2>&1 \
  || fail 'report naming the short current head was rejected'
contains "$ROOT/short-report-head.out" 'JOB-CHECK: green' 'short HEAD report binding is accepted'
write_report
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" any > "$ROOT/route-any.out" 2>&1 || {
    cat "$ROOT/route-any.out" >&2
    fail 'route any should accept a supported Codex trailer on every commit'
  }
contains "$ROOT/route-any.out" 'trailers OK (all commits incl. merges: any)' 'prototype any route remains available'

git -C "$REPO" switch -qc claude-task "$BASE"
printf 'claude implementation\n' > "$REPO/claude.txt"
git -C "$REPO" add claude.txt
git -C "$REPO" commit -qm 'Claude route commit' -m 'Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>'
write_gate_log
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" any > "$ROOT/route-any-claude.out" 2>&1 || {
    cat "$ROOT/route-any-claude.out" >&2
    fail 'route any should accept the prototype Claude trailer form'
  }
contains "$ROOT/route-any-claude.out" 'trailers OK (all commits incl. merges: any)' 'prototype any route accepts Claude trailers'
git -C "$REPO" switch -q task
write_gate_log

git -C "$REPO" switch -qc any-multi-trailer "$BASE"
printf 'multi-trailer implementation\n' > "$REPO/multi-trailer.txt"
git -C "$REPO" add multi-trailer.txt
git -C "$REPO" commit -qm 'Claude and Codex multi-trailer commit' \
  -m 'Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>' \
  -m 'Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>'
write_gate_log
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" any > "$ROOT/route-any-multi.out" 2>&1 || {
    cat "$ROOT/route-any-multi.out" >&2
    fail 'route any should accept a commit with multiple known agent trailers'
  }
contains "$ROOT/route-any-multi.out" 'INFO: multiple agent trailers' 'multiple known agent trailers are informational'
contains "$ROOT/route-any-multi.out" 'trailers OK (all commits incl. merges: any)' 'allowed-set trailer mode accepts multiple known agents'
git -C "$REPO" switch -q task
write_gate_log

GOOD_HEAD=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" commit --amend -m 'merge side without route trailer' -q
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" any > "$ROOT/merge-trailer.out" 2>&1; then
  fail 'merge commit without a trailer was accepted'
fi
contains "$ROOT/merge-trailer.out" 'NO/WRONG TRAILER' 'merge trailer is audited'
contains "$ROOT/merge-trailer.out" 'merge side without route trailer' 'merge failure identifies the merge commit'
git -C "$REPO" reset --hard -q "$GOOD_HEAD"
write_gate_log

write_reattach_evidence
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" none "$ROOT/report.md" > "$ROOT/reattach-gate.out" 2>&1 \
  || {
    cat "$ROOT/reattach-gate.out" >&2
    fail 're-attached gate evidence with a bound container label was rejected'
  }
contains "$ROOT/reattach-gate.out" 're-attach gate evidence:' 're-attached evidence is accepted explicitly'
contains "$ROOT/reattach-gate.out" 'run-gate.commit=' 're-attach output names the resolved commit label'
write_report
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" none "$ROOT/report.md" > "$ROOT/reattach-report-missing.out" 2>&1; then
  fail 're-attached evidence was accepted without a report naming its container'
fi
contains "$ROOT/reattach-report-missing.out" 'GATE EVIDENCE MISSING' 're-attach requires the report to name the collected container'
write_reattach_evidence
python3 - "$CONTROLLER_LOGS_DIR/original.log" "$(git -C "$REPO" rev-parse HEAD)" "$BASE" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(path.read_text().replace(sys.argv[2], sys.argv[3]))
PY
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" none "$ROOT/report.md" > "$ROOT/reattach-wrong-label.out" 2>&1; then
  fail 're-attached evidence with a label for another commit was accepted'
fi
contains "$ROOT/reattach-wrong-label.out" 'GATE EVIDENCE MISSING' 're-attach label must bind the full current head'
write_gate_log
write_report

if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" ../escape "$REPO" luna > "$ROOT/traversal.out" 2>&1; then
  fail 'run-name traversal was accepted'
else rc=$?; [[ $rc == 2 ]] || fail "run-name traversal exit was $rc, expected 2"; fi
contains "$ROOT/traversal.out" 'invalid run name' 'run-name traversal guard fires before path checks'
if grep -qF 'RUN FILE MISSING:' "$ROOT/traversal.out"; then fail 'run path was built before rejecting traversal'; fi

mkdir -p "$REPO/reports" "$REPO/scripts/controller/reports"
printf 'stray report\n' > "$REPO/reports/stray.md"
printf 'nested stray report\n' > "$REPO/scripts/controller/reports/stray.md"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna > "$ROOT/stray-report.out" 2>&1; then
  fail 'untracked report litter was accepted'
fi
contains "$ROOT/stray-report.out" 'STRAY REPORT FILES IN WORKTREE (relative-path litter)' 'prototype report-litter detector is preserved'
contains "$ROOT/stray-report.out" '?? reports/' 'root report litter is listed'
contains "$ROOT/stray-report.out" '?? scripts/controller/reports/' 'nested project report litter is listed'
rm -f -- "$REPO/reports/stray.md" "$REPO/scripts/controller/reports/stray.md"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna > "$ROOT/healthy-after-stray.out" 2>&1 \
  || fail 'clean worktree did not recover after report-litter fixture removal'

cp "$SCRIPT_DIR/fixtures/stalled-run.jsonl" "$HOME_ROOT/runs/stalled.jsonl"
printf 'EXIT=0\n' > "$HOME_ROOT/runs/stalled.done"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" stalled "$REPO" luna > "$ROOT/stalled.out" 2>&1; then fail 'stalled real-run fixture was accepted'; fi
contains "$ROOT/stalled.out" 'MID-WAIT ending' 'trimmed real-run fixture detects a mid-wait ending'

for fixture in reasoning-end intent-only usage-limit usage-limit-mid-wait usage-limit-dated clean-run reviewer-wait-mention killed-run; do
  cp "$SCRIPT_DIR/fixtures/$fixture.jsonl" "$HOME_ROOT/runs/$fixture.jsonl"
  printf 'EXIT=0\n' > "$HOME_ROOT/runs/$fixture.done"
done
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" reasoning-end "$REPO" none > "$ROOT/reasoning-end.out" 2>&1; then fail 'reasoning-end stream was accepted'; fi
contains "$ROOT/reasoning-end.out" 'REASONING-END' 'completed reasoning-only final item is classified'
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" intent-only "$REPO" none > "$ROOT/intent-fixture.out" 2>&1; then fail 'real intent-only ending was accepted'; fi
contains "$ROOT/intent-fixture.out" 'INTENT-ONLY ending' 'real intent-only fixture is classified'
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" usage-limit "$REPO" none > "$ROOT/usage-limit.out" 2>&1; then fail 'usage-limited stream was accepted'; fi
contains "$ROOT/usage-limit.out" 'USAGE-LIMIT (reset 3:46 PM)' 'usage-limit classification prints the reset time'
if grep -qF 'KILLED' "$ROOT/usage-limit.out"; then fail 'usage-limit classification was obscured by the generic killed class'; fi
if grep -qF 'EMPTY-RUN' "$ROOT/usage-limit.out"; then fail 'usage-limit classification was obscured by the empty-message class'; fi
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" usage-limit-mid-wait "$REPO" none > "$ROOT/usage-limit-mid-wait.out" 2>&1; then
  fail 'usage-limit ending after a pending-wait message was accepted'
fi
contains "$ROOT/usage-limit-mid-wait.out" 'USAGE-LIMIT (reset 3:46 PM)' 'usage-limit dominates a prior pending-wait message'
if grep -qF 'MID-WAIT ending' "$ROOT/usage-limit-mid-wait.out"; then
  fail 'pending-wait text obscured the usage-limit classification order'
fi
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" usage-limit-dated "$REPO" none > "$ROOT/usage-limit-dated.out" 2>&1; then
  fail 'dated usage-limit stream was accepted'
fi
contains "$ROOT/usage-limit-dated.out" 'USAGE-LIMIT (reset Nov 17th, 2026 12:59 AM)' 'dated usage-limit classification prints the retry text verbatim'
if grep -qF 'KILLED' "$ROOT/usage-limit-dated.out"; then
  fail 'dated usage-limit classification was obscured by the generic killed class'
fi
if grep -qF 'MID-WAIT ending' "$ROOT/usage-limit-dated.out"; then
  fail 'dated usage-limit classification was obscured by a wait detector'
fi
rm -f -- "$CONTROLLER_LOGS_DIR"/*.log
printf "run-gate.commit=%s\nPASS ctl-status\nrun-gate: lane 'suite' verdict PASS; exit_code 0\n" \
  "$BASE" > "$CONTROLLER_LOGS_DIR/old-head-pass.log"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" usage-limit "$REPO" none > "$ROOT/usage-old-gate.out" 2>&1; then
  fail 'usage-limited stream with an old green gate log was accepted'
fi
contains "$ROOT/usage-old-gate.out" 'USAGE-LIMIT (reset 3:46 PM)' 'usage-limit remains red with only old-head gate evidence'
contains "$ROOT/usage-old-gate.out" 'GATE EVIDENCE MISSING' 'old-head PASS is not current-head gate evidence'
if grep -qF 'JOB-CHECK: green' "$ROOT/usage-old-gate.out"; then fail 'usage-limited run printed green with an old-head gate'; fi
write_gate_log
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" clean-run "$REPO" none > "$ROOT/clean-run.out" 2>&1 || fail 'clean completed run was rejected'
contains "$ROOT/clean-run.out" 'JOB-CHECK: green' 'real clean-run fixture remains green'
printf '%s\n' \
  '{"type":"item.completed","item":{"type":"reasoning","text":"Thinking."}}' \
  '{"type":"item.completed","item":{"type":"command_execution","command":"true"}}' \
  '{"type":"item.completed","item":{"type":"agent_message","text":"Complete."}}' \
  '{"type":"turn.completed"}' > "$HOME_ROOT/runs/reasoning-followed-by-action.jsonl"
printf 'EXIT=0\n' > "$HOME_ROOT/runs/reasoning-followed-by-action.done"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" reasoning-followed-by-action "$REPO" none > "$ROOT/reasoning-followed.out" 2>&1 \
  || fail 'reasoning followed by a tool call and final message was treated as reasoning-end'
if grep -qF 'REASONING-END' "$ROOT/reasoning-followed.out"; then
  fail 'reasoning-end classification ignored a later tool call or message'
fi
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" reviewer-wait-mention "$REPO" none > "$ROOT/reviewer-wait.out" 2>&1 || fail 'reviewer closing message that discusses a wait was rejected'
if grep -qF 'MID-WAIT ending' "$ROOT/reviewer-wait.out"; then fail 'reviewer closing message mentioning a wait tripped MID-WAIT'; fi

write_wait_case() {
  local name=$1 expected=$2 message=$3
  python3 - "$HOME_ROOT/runs/$name.jsonl" "$message" <<'PY'
import json
import pathlib
import sys

events = [
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": sys.argv[2]}},
    {"type": "turn.completed"},
]
pathlib.Path(sys.argv[1]).write_text("\n".join(map(json.dumps, events)) + "\n")
PY
  printf 'EXIT=0\n' > "$HOME_ROOT/runs/$name.done"
  if [[ $expected == wait ]]; then
    if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
      "$SRC" "$name" "$REPO" none > "$ROOT/$name.out" 2>&1; then
      fail "pending wait ending was accepted: $message"
    fi
    contains "$ROOT/$name.out" 'MID-WAIT ending' "pending wait ending is classified: $name"
  else
    CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
      "$SRC" "$name" "$REPO" none > "$ROOT/$name.out" 2>&1 \
      || fail "completed closing message was rejected: $message"
    if grep -qF 'MID-WAIT ending' "$ROOT/$name.out"; then
      fail "non-pending closing message tripped MID-WAIT: $message"
    fi
  fi
}
wait_cases=(
  'wait-still-on-gate|wait|I am still waiting on the gate.'
  'wait-bare-on-gate|wait|Still waiting on the gate.'
  'wait-open-gate|wait|I am waiting for the gate to open.'
  'wait-continuing|wait|I am continuing to wait.'
  'wait-no-slot|wait|No slot release yet ... continuing to wait.'
  'wait-completed|clean|I was still waiting on the gate, but it opened and the check passed.'
  'wait-resolved-after-pending|clean|I am still waiting on the gate, but it opened and the check passed.'
  'wait-quoted|clean|The reviewer quoted “I am continuing to wait” as an example; the gate passed and the review is complete.'
  'wait-no-longer|clean|I am no longer waiting on the gate; the gate passed.'
)
for wait_case in "${wait_cases[@]}"; do
  IFS='|' read -r name expected message <<< "$wait_case"
  write_wait_case "$name" "$expected" "$message"
done

if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" killed-run "$REPO" none > "$ROOT/killed-run.out" 2>&1; then fail 'run without turn.completed was accepted'; fi
contains "$ROOT/killed-run.out" 'KILLED (no turn.completed)' 'unfinished stream is classified as killed'

if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna relative-report.md > "$ROOT/relative.out" 2>&1; then fail 'relative report path was accepted'; fi
contains "$ROOT/relative.out" 'REPORT PATH NOT ABSOLUTE' 'report path guard requires an absolute path'

printf 'short\n' > "$ROOT/short-report.md"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna "$ROOT/short-report.md" > "$ROOT/skeleton.out" 2>&1; then
  fail 'short report skeleton was accepted'
fi
contains "$ROOT/skeleton.out" 'REPORT LOOKS LIKE A SKELETON' 'report completeness guard fires'

printf '{"type":"item.completed","item":{"type":"agent_message","text":"I will inspect the gate output next."}}\n' \
  > "$HOME_ROOT/runs/intent.jsonl"
printf 'EXIT=0\n' > "$HOME_ROOT/runs/intent.done"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" intent "$REPO" luna > "$ROOT/intent.out" 2>&1; then fail 'intent-only ending was accepted'; fi
contains "$ROOT/intent.out" 'INTENT-ONLY ending' 'intent-only ending detector fires'

printf '{"type":"item.completed","item":{"type":"agent_message","text":"*** Begin Patch"}}\n' \
  > "$HOME_ROOT/runs/tooltext.jsonl"
printf 'EXIT=0\n' > "$HOME_ROOT/runs/tooltext.done"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" tooltext "$REPO" luna > "$ROOT/tooltext.out" 2>&1; then fail 'tool-call text was accepted'; fi
contains "$ROOT/tooltext.out" 'TOOL-CALL-AS-TEXT' 'tool-call text detector fires'

printf '{"type":"item.completed","item":{"type":"agent_message","text":""}}\n' \
  > "$HOME_ROOT/runs/empty-final.jsonl"
printf 'EXIT=0\n' > "$HOME_ROOT/runs/empty-final.done"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" empty-final "$REPO" luna > "$ROOT/empty-final.out" 2>&1; then
  fail 'empty final message was accepted'
fi
contains "$ROOT/empty-final.out" 'EMPTY-RUN (no final agent message)' 'empty final-message detector fires'

printf '{"type":"item.completed","item":{"type":"agent_message","text":"Complete."}}\n' \
  > "$HOME_ROOT/runs/child-fail.jsonl"
printf 'EXIT=7\n' > "$HOME_ROOT/runs/child-fail.done"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" child-fail "$REPO" luna > "$ROOT/child-fail.out" 2>&1; then fail 'nonzero child exit was accepted'; fi
contains "$ROOT/child-fail.out" 'CHILD EXIT IS NOT ZERO: EXIT=7' 'child exit marker is checked'

printf '{"type":"item.completed","item":{"type":"agent_message","text":"working"}}\n' \
  > "$HOME_ROOT/runs/running.jsonl"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO \
  "$SRC" running "$REPO" luna > "$ROOT/running.out" 2>&1 || fail 'unfinished run did not retain the prototype active status'
contains "$ROOT/running.out" 'RUNNING (no .done)' 'prototype unfinished-run status is preserved'

if ! CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" missing-stream "$REPO" luna > "$ROOT/missing-stream.out" 2>&1; then
  cat "$ROOT/missing-stream.out" >&2
  fail 'prototype missing-.done behavior should report RUNNING with exit 0'
fi
contains "$ROOT/missing-stream.out" 'RUNNING (no .done)' 'missing stream does not change prototype status'

printf 'dirty\n' > "$REPO/dirty.txt"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna > "$ROOT/dirty.out" 2>&1; then fail 'dirty worktree was accepted'; fi
contains "$ROOT/dirty.out" 'DIRTY WORKTREE' 'dirty worktree guard fires'

if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=missing-ref \
  "$SRC" healthy "$REPO" luna > "$ROOT/missing-base.out" 2>&1; then fail 'missing base ref was accepted'; fi
contains "$ROOT/missing-base.out" 'BASE REF MISSING OR HEAD UNREADABLE: missing-ref' 'missing trailer audit base fails loudly'

echo 'PASS job-check: real-run fixtures, dated usage-limit, completion, absolute report, clean tree, all commit trailers including merge'
