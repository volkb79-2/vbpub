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
contains "$ROOT/healthy.out" 'last: Implementation complete. Report:' 'prototype final-message preview remains available'
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
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" any > "$ROOT/route-any-claude.out" 2>&1 || {
    cat "$ROOT/route-any-claude.out" >&2
    fail 'route any should accept the prototype Claude trailer form'
  }
contains "$ROOT/route-any-claude.out" 'trailers OK (all commits incl. merges: any)' 'prototype any route accepts Claude trailers'
git -C "$REPO" switch -q task

GOOD_HEAD=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" commit --amend -m 'merge side without route trailer' -q
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$REPO CONTROLLER_BASE_REF=origin/main \
  "$SRC" healthy "$REPO" any > "$ROOT/merge-trailer.out" 2>&1; then
  fail 'merge commit without a trailer was accepted'
fi
contains "$ROOT/merge-trailer.out" 'NO/WRONG TRAILER' 'merge trailer is audited'
contains "$ROOT/merge-trailer.out" 'merge side without route trailer' 'merge failure identifies the merge commit'
git -C "$REPO" reset --hard -q "$GOOD_HEAD"

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
contains "$ROOT/missing-base.out" 'BASE REF MISSING: missing-ref' 'missing trailer audit base fails loudly'

echo 'PASS job-check: real-run fixture, completion, absolute report, clean tree, all commit trailers including merge'
