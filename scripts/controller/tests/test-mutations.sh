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
for baseline in test-job-check.sh test-hardening-tools.sh; do
  if ! bash "$SCRIPT_DIR/$baseline" > "$ROOT/${baseline%.sh}-unmutated.log" 2>&1; then
    cat "$ROOT/${baseline%.sh}-unmutated.log" >&2
    echo "FAIL: unmutated $baseline must pass before its mutants are judged" >&2
    exit 1
  fi
done
echo 'PASS unmutated job-check and hardening tests before their mutation checks'
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
expect_killed job-empty-final-message-detector \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-empty-final.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-tool-call-case-detection \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-tool-call-case.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-absolute-report-path \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-report-path.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-gate-evidence-binding \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-gate-binding.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-gate-final-verdict \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-gate-final-verdict.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-report-head-binding \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-report-head.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-reasoning-end-classification \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-reasoning-end.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-usage-limit-classification \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-usage-limit.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-usage-retry-dated-binding \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-usage-retry-binding.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-killed-run-classification \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-killed-run.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-trailing-mid-wait-classification \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-mid-wait-trailing.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-mid-wait-resolution \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-mid-wait-resolution.sh" bash "$SCRIPT_DIR/test-job-check.sh"
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
expect_killed codex-autoresume-bound \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-autoresume-bound.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed codex-autoresume-limit-validation \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-autoresume-limit-validation.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed controller-runs-dir-environment-override \
  env CONTROLLER_LIB_SCRIPT="$ROOT/mutants/scripts/controller/controller-run-dir-override.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-usage-limit-detection \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-usage-detection.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-future-dated-retry \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-future-retry.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-skip-list-guard \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-skip-list.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-route-sidecar-guard \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-route-sidecar.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-route-value-guard \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-route-value.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-max-age-validation \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-max-age-validation.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-excluded-prefixes \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-excluded-prefixes.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-worktree-map \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-worktree-map.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-live-wrapper \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-live-wrapper.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-claim-lock \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-claim-lock.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed start-after-time-guard \
  env START_AFTER_SCRIPT="$ROOT/mutants/scripts/controller/start-after-time-guard.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed start-after-target-validation \
  env START_AFTER_SCRIPT="$ROOT/mutants/scripts/controller/start-after-target-validation.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed gate-run-distinct-lock-guard \
  env GATE_RUN_SCRIPT="$ROOT/mutants/scripts/controller/gate-run-distinct-locks.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed gate-run-absolute-lock-guard \
  env GATE_RUN_SCRIPT="$ROOT/mutants/scripts/controller/gate-run-absolute-locks.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed gate-run-poll-both-final-wait-locks \
  env GATE_RUN_SCRIPT="$ROOT/mutants/scripts/controller/gate-run-poll-second-slot.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed gate-run-refusal-names-example \
  env GATE_RUN_SCRIPT="$ROOT/mutants/scripts/controller/gate-run-refusal-example.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed job-any-multi-trailer-allowed-set \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-any-multi-trailer.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-usage-limit-dominates-message-flags \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-usage-limit-message-flags.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-reattach-container-evidence \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-reattach-evidence.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed codex-stale-run-marker-guard \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-stale-run-markers.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed codex-stale-run-resume-mode \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-stale-run-resume-mode.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed usage-retry-dated-parser \
  env CONTROLLER_LIB_SCRIPT="$ROOT/mutants/scripts/controller/usage-retry-time-only-lib.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed build-readback-budget-minimum \
  env BUILD_READBACK_SCRIPT="$ROOT/mutants/scripts/controller/build-readback-budget-minimum.py" bash "$SCRIPT_DIR/test-hardening-tools.sh"
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
echo 'PASS hand mutations: 60 killed'
