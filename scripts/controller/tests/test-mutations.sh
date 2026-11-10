#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-mutation-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
python3 "$SCRIPT_DIR/mutate-sources.py" "$ROOT" || exit $?
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
expect_killed job-tool-call-case-detection \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-tool-call-case.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-absolute-report-path \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-report-path.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed hx-provider-identity-guard \
  env HX_WRAPPER="$ROOT/mutants/scripts/controller/hx-identity.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed release-evidence-archive \
  env RELEASE_SCRIPT="$ROOT/mutants/scripts/controller/release-archive.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed release-clean-worktree-guard \
  env RELEASE_SCRIPT="$ROOT/mutants/scripts/controller/release-clean.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed push-pipeline-exit-propagation \
  env PUSH_IMAGE_SCRIPT="$ROOT/mutants/scripts/controller/push-image-status.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed queue-auto-pause-threshold \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-threshold.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-pause-write-failure-fallback \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-fallback.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-incomplete-resource-probe \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-probe.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-stop-host-alias-guard \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-stop-alias.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-start-host-table-error \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-start-config.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-stop-host-table-error \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-stop-config.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
expect_killed queue-controller-library-selection \
  env QUEUE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/queue-controller-lib.sh" bash "$REPO_ROOT/scripts/netcup/runner/tests/test-queue.sh"
echo 'PASS hand mutations: 16 killed'
