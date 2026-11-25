#!/usr/bin/env bash
set -uo pipefail
# The caller's ambient controller configuration is not this check's business:
# every check here builds its own temporary controller root, so an exported
# CONTROLLER_HOME / RUNS_DIR / LOGS_DIR / REPO / HOSTS_FILE must not decide where
# a check reads or writes. Relying on the caller ("run it with env -u") moved the
# same defect into the operator's shell, where it read as a product bug.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)
queue_test=$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh
files=(
  "$SCRIPT_DIR/../ctl-status.sh"
  "$SCRIPT_DIR/../job-check.sh"
  "$SCRIPT_DIR/../lib.sh"
  "$SCRIPT_DIR/../hx.sh"
  "$SCRIPT_DIR/../codex-impl.sh"
  "$SCRIPT_DIR/../pi-impl.sh"
  "$SCRIPT_DIR/../codex-review.sh"
  "$SCRIPT_DIR/../pi-review.sh"
  "$SCRIPT_DIR/../opencode-impl.sh"
  "$SCRIPT_DIR/../resume-usage-limited.sh"
  "$SCRIPT_DIR/../start-after.sh"
  "$SCRIPT_DIR/../gate-run.sh"
  "$SCRIPT_DIR/../remote-gate.sh"
  "$SCRIPT_DIR/../chain-run.sh"
  "$SCRIPT_DIR/../ctl-doctor.sh"
  "$SCRIPT_DIR/../ctl-hygiene.sh"
  "$SCRIPT_DIR/../ctl-state-backup.sh"
  "$SCRIPT_DIR/../release-from-wt.sh"
  "$SCRIPT_DIR/../push-image.sh"
  "$SCRIPT_DIR/../dev"
  "$REPO_ROOT/hostobs/tools/dev"
  "$SCRIPT_DIR/../skills-sync.sh"
  "$SCRIPT_DIR/../clear-resume.sh"
  "$SCRIPT_DIR/../compact-resume.sh"
  "$SCRIPT_DIR/../resume-lib.sh"
  "$SCRIPT_DIR/../resume-read.sh"
  "$SCRIPT_DIR/../ctl-keepalive.sh"
  "$REPO_ROOT/scripts/netcup/runner/host-sat-queue.sh"
  "$REPO_ROOT/scripts/netcup/runner/remote-gate-cache.sh"
  "$REPO_ROOT/scripts/netcup/runner/tests/test_remote_gate_cache.sh"
  "$SCRIPT_DIR/test-ctl-status.sh"
  "$SCRIPT_DIR/test-job-check.sh"
  "$SCRIPT_DIR/test-wrappers.sh"
  "$SCRIPT_DIR/test-pi-impl.sh"
  "$SCRIPT_DIR/test-pi-review.sh"
  "$SCRIPT_DIR/test-hardening-tools.sh"
  "$SCRIPT_DIR/test-gate-slots.sh"
  "$SCRIPT_DIR/test-ctx-hooks.sh"
  "$SCRIPT_DIR/test-mutations.sh"
  "$SCRIPT_DIR/test-skills-sync.sh"
  "$SCRIPT_DIR/test-remote-gate.sh"
  "$SCRIPT_DIR/test-chain-run.sh"
  "$SCRIPT_DIR/test-ctl-doctor.sh"
  "$SCRIPT_DIR/test-ctl-hygiene.sh"
  "$SCRIPT_DIR/test-ctl-state-backup.sh"
  "$SCRIPT_DIR/test-clear-resume.sh"
  "$SCRIPT_DIR/test-compact-resume.sh"
  "$SCRIPT_DIR/test-resume-read.sh"
  "$SCRIPT_DIR/test-ctl-keepalive.sh"
  "$SCRIPT_DIR/run-suite.sh"
  "$queue_test"
)
for file in "${files[@]}"; do bash -n "$file" || exit $?; done
for file in "$SCRIPT_DIR/../dev_profile.py" "$SCRIPT_DIR/test_dev.py"; do
  python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$file" || exit $?
done
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck "${files[@]}" || exit $?
else
  echo 'INFO: shellcheck is not installed in this tester image'
fi
# the generic remote dev tool: fakes only (no host, docker or ssh); first so a break shows in a minute, not after the long suites
: "${TESTER_VENV:?run-suite.sh runs in the tester-unified gate container, which sets TESTER_VENV}"
"$TESTER_VENV/bin/python" -m pytest -q -p no:cacheprovider "$SCRIPT_DIR/test_dev.py" || exit $?
bash "$SCRIPT_DIR/test-ctl-status.sh" || exit $?
bash "$SCRIPT_DIR/test-job-check.sh" || exit $?
bash "$SCRIPT_DIR/test-wrappers.sh" || exit $?
bash "$SCRIPT_DIR/test-pi-impl.sh" || exit $?
bash "$SCRIPT_DIR/test-pi-review.sh" || exit $?
bash "$SCRIPT_DIR/test-hardening-tools.sh" || exit $?
bash "$SCRIPT_DIR/test-gate-slots.sh" || exit $?
bash "$SCRIPT_DIR/test-ctx-hooks.sh" || exit $?
bash "$SCRIPT_DIR/test-skills-sync.sh" || exit $?
bash "$SCRIPT_DIR/test-remote-gate.sh" || exit $?
bash "$SCRIPT_DIR/test-chain-run.sh" || exit $?
bash "$SCRIPT_DIR/test-ctl-doctor.sh" || exit $?
bash "$SCRIPT_DIR/test-ctl-hygiene.sh" || exit $?
bash "$SCRIPT_DIR/test-ctl-state-backup.sh" || exit $?
bash "$SCRIPT_DIR/test-clear-resume.sh" || exit $?
bash "$SCRIPT_DIR/test-compact-resume.sh" || exit $?
bash "$SCRIPT_DIR/test-resume-read.sh" || exit $?
bash "$SCRIPT_DIR/test-ctl-keepalive.sh" || exit $?
bash "$queue_test" || exit $?
bash "$REPO_ROOT/scripts/netcup/runner/tests/test_remote_gate_cache.sh" || exit $?
bash "$SCRIPT_DIR/test-mutations.sh" || exit $?
echo 'PASS controller suite'
