#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)
queue_test=$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh
files=(
  "$SCRIPT_DIR/../ctl-status.sh"
  "$SCRIPT_DIR/../job-check.sh"
  "$SCRIPT_DIR/../lib.sh"
  "$SCRIPT_DIR/../hx.sh"
  "$SCRIPT_DIR/../codex-impl.sh"
  "$SCRIPT_DIR/../codex-review.sh"
  "$SCRIPT_DIR/../opencode-impl.sh"
  "$SCRIPT_DIR/../resume-usage-limited.sh"
  "$SCRIPT_DIR/../start-after.sh"
  "$SCRIPT_DIR/../gate-run.sh"
  "$SCRIPT_DIR/../release-from-wt.sh"
  "$SCRIPT_DIR/../push-image.sh"
  "$REPO_ROOT/scripts/netcup/runner/host-sat-queue.sh"
  "$SCRIPT_DIR/test-ctl-status.sh"
  "$SCRIPT_DIR/test-job-check.sh"
  "$SCRIPT_DIR/test-wrappers.sh"
  "$SCRIPT_DIR/test-hardening-tools.sh"
  "$SCRIPT_DIR/test-mutations.sh"
  "$SCRIPT_DIR/run-suite.sh"
  "$queue_test"
)
for file in "${files[@]}"; do bash -n "$file" || exit $?; done
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck "${files[@]}" || exit $?
else
  echo 'INFO: shellcheck is not installed in this tester image'
fi
bash "$SCRIPT_DIR/test-ctl-status.sh" || exit $?
bash "$SCRIPT_DIR/test-job-check.sh" || exit $?
bash "$SCRIPT_DIR/test-wrappers.sh" || exit $?
bash "$SCRIPT_DIR/test-hardening-tools.sh" || exit $?
bash "$queue_test" || exit $?
bash "$SCRIPT_DIR/test-mutations.sh" || exit $?
echo 'PASS controller suite'
