#!/usr/bin/env bash
set -uo pipefail
unset CODEX_ROUTE CODEX_HOME_OVERRIDE CODEX_HOME_LUNA CODEX_HOME_DEEPSEEK CODEX_HOME_REVIEW CODEX_MODEL CODEX_EFFORT
unset CODEX_REVIEW_MODEL CODEX_REVIEW_EFFORT CODEX_SQLITE_HOME
unset OPENCODE_COMMAND OPENCODE_MODEL
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-mutation-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
python3 "$SCRIPT_DIR/mutate-sources.py" "$ROOT" || exit $?
if ! bash "$SCRIPT_DIR/test-wrappers.sh" > "$ROOT/wrappers-unmutated.log" 2>&1; then
  cat "$ROOT/wrappers-unmutated.log" >&2
  echo 'FAIL: unmutated wrapper tests must pass before wrapper mutants are judged' >&2
  exit 1
fi
echo 'PASS unmutated wrapper tests before wrapper mutation checks'
expect_killed() {
  local label=$1; shift
  if "$@" > "$ROOT/$label.log" 2>&1; then
    echo "SURVIVED: $label" >&2
    exit 1
  fi
  echo "KILLED: $label"
}

expect_killed ctl-infra-attention-threshold \
  env CTL_STATUS_SCRIPT="$ROOT/mutants/scripts/controller/ctl-status-infra.sh" bash "$SCRIPT_DIR/test-ctl-status.sh"
expect_killed ctl-pause-newer-than-pass \
  env CTL_STATUS_SCRIPT="$ROOT/mutants/scripts/controller/ctl-status-flag.sh" bash "$SCRIPT_DIR/test-ctl-status.sh"
expect_killed job-mid-wait-detector \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-mid-wait.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-empty-final-message-detector \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-empty-final.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-tool-call-case-detection \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-tool-call-case.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-absolute-report-path \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-report-path.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-run-name-traversal-guard \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-run-traversal.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-prototype-missing-done-success \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-missing-done-success.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-report-litter-detector \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-report-litter.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-merge-trailer-audit \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-merge-trailer.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-any-claude-trailer-compatibility \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-any-claude-trailer.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed hx-provider-identity-guard \
  env HX_WRAPPER="$ROOT/mutants/scripts/controller/hx-identity.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed release-evidence-archive \
  env RELEASE_SCRIPT="$ROOT/mutants/scripts/controller/release-archive.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed release-clean-worktree-guard \
  env RELEASE_SCRIPT="$ROOT/mutants/scripts/controller/release-clean.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed release-psi-input-validation \
  env RELEASE_SCRIPT="$ROOT/mutants/scripts/controller/release-psi-validation.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-deepseek-catalog-safety \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-deepseek-catalog.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed push-pipeline-exit-propagation \
  env PUSH_IMAGE_SCRIPT="$ROOT/mutants/scripts/controller/push-image-status.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed queue-auto-pause-threshold \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-threshold.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-pause-write-failure-fallback \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-fallback.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-incomplete-resource-probe \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-probe.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-numeric-probe-validation \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-numeric-probe.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-pause-host-alias-validation \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-pause-alias.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-resume-host-alias-validation \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-resume-alias.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-results-section-guard \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-status-results.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-provider-identity-before-launch \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-run-identity.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-host-alias-validation \
  env QUEUE_SCRIPT="$ROOT/mutants-host-alias/scripts/netcup/runner/host-sat-queue.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-lane-shape-validation \
  env QUEUE_SCRIPT="$ROOT/mutants-queue-lane-shape/scripts/netcup/runner/host-sat-queue.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-stop-host-alias-guard \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-stop-alias.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-start-host-table-error \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-start-config.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-stop-host-table-error \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-stop-config.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-controller-library-selection \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-controller-lib.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed ctl-status-fresh-results-guard \
  env CTL_STATUS_SCRIPT="$ROOT/mutants/scripts/controller/ctl-status-fresh-results.sh" bash "$SCRIPT_DIR/test-ctl-status.sh"
echo 'PASS hand mutations: 32 killed'
