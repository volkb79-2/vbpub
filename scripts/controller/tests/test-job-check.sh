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
mkdir -p "$HOME_ROOT/runs" "$REPO"
git -C "$REPO" init -q
git -C "$REPO" config user.name test
git -C "$REPO" config user.email test@example.invalid
printf 'baseline\n' > "$REPO/base.txt"
git -C "$REPO" add base.txt && git -C "$REPO" commit -qm baseline
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

printf '{"type":"item.completed","item":{"type":"agent_message","text":"Implementation complete. Report: %s"}}\n' "$ROOT/report.md" > "$HOME_ROOT/runs/healthy.jsonl"
printf 'EXIT=0\n' > "$HOME_ROOT/runs/healthy.done"
python3 - "$ROOT/report.md" <<'PY'
import pathlib, sys
pathlib.Path(sys.argv[1]).write_text("Review evidence.\n" * 100)
PY
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna "$ROOT/report.md" > "$ROOT/healthy.out" 2>&1 || {
    cat "$ROOT/healthy.out" >&2
    fail 'clean run with trailers on merge and regular commits should pass'
  }
contains "$ROOT/healthy.out" 'trailers OK (all commits incl. merges: luna)' 'trailer audit includes merge commits'
contains "$ROOT/healthy.out" 'JOB-CHECK: green' 'healthy job is green'

cp "$SCRIPT_DIR/fixtures/stalled-run.jsonl" "$HOME_ROOT/runs/stalled.jsonl"
printf 'EXIT=0\n' > "$HOME_ROOT/runs/stalled.done"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" stalled "$REPO" luna > "$ROOT/stalled.out" 2>&1; then fail 'stalled real-run fixture was accepted'; fi
contains "$ROOT/stalled.out" 'MID-WAIT ending' 'trimmed real-run fixture detects a mid-wait ending'

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

printf '{"type":"item.completed","item":{"type":"agent_message","text":"Complete."}}\n' \
  > "$HOME_ROOT/runs/child-fail.jsonl"
printf 'EXIT=7\n' > "$HOME_ROOT/runs/child-fail.done"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" child-fail "$REPO" luna > "$ROOT/child-fail.out" 2>&1; then fail 'nonzero child exit was accepted'; fi
contains "$ROOT/child-fail.out" 'CHILD EXIT IS NOT ZERO: EXIT=7' 'child exit marker is checked'

printf '{"type":"item.completed","item":{"type":"agent_message","text":"working"}}\n' \
  > "$HOME_ROOT/runs/running.jsonl"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO JOB_STALL_SECONDS=60 \
  "$SRC" running "$REPO" luna > "$ROOT/running.out" 2>&1 || fail 'fresh unfinished run was treated as stalled'
touch -d "@$(( $(date +%s) - 120 ))" "$HOME_ROOT/runs/running.jsonl"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO JOB_STALL_SECONDS=60 \
  "$SRC" running "$REPO" luna > "$ROOT/stalled-age.out" 2>&1; then fail 'old unfinished run was accepted'; fi
contains "$ROOT/stalled-age.out" 'RUNNING STALLED' 'stale unfinished run detector fires'

printf 'dirty\n' > "$REPO/dirty.txt"
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" luna > "$ROOT/dirty.out" 2>&1; then fail 'dirty worktree was accepted'; fi
contains "$ROOT/dirty.out" 'DIRTY WORKTREE' 'dirty worktree guard fires'

if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=missing-ref \
  "$SRC" healthy "$REPO" luna > "$ROOT/missing-base.out" 2>&1; then fail 'missing base ref was accepted'; fi
contains "$ROOT/missing-base.out" 'BASE REF MISSING: missing-ref' 'missing trailer audit base fails loudly'

echo 'PASS job-check: real-run fixture, completion, absolute report, clean tree, all commit trailers including merge'
