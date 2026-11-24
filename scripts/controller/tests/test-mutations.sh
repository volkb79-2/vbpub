#!/usr/bin/env bash
set -uo pipefail
# The caller's ambient controller configuration is not this check's business:
# every check here builds its own temporary controller root, so an exported
# CONTROLLER_HOME / RUNS_DIR / LOGS_DIR / REPO / HOSTS_FILE must not decide where
# a check reads or writes. Relying on the caller ("run it with env -u") moved the
# same defect into the operator's shell, where it read as a product bug.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
unset CODEX_ROUTE CODEX_HOME_OVERRIDE CODEX_HOME_LUNA CODEX_HOME_DEEPSEEK_VOLKER CODEX_HOME_DEEPSEEK_GITHUB2 CODEX_HOME_REVIEW CODEX_MODEL CODEX_EFFORT
unset CODEX_REVIEW_MODEL CODEX_REVIEW_EFFORT CODEX_SQLITE_HOME
unset PI_TEST_FOCUS
unset CONTROLLER_OD_STATE CONTROLLER_OD_PREFER_EXPIRING CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2
unset CONTROLLER_OD_ACCOUNTS_SCRIPT OPENCODE_COMMAND OPENCODE_MODEL OPENCODE_PROVIDER OPENCODE_IMPL_SCRIPT
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-mutation-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
python3 "$SCRIPT_DIR/mutate-sources.py" "$ROOT" || exit $?
if ! CONTROLLER_TEST_PARALLEL_OD_STARTS=0 bash "$SCRIPT_DIR/test-wrappers.sh" > "$ROOT/wrappers-unmutated.log" 2>&1; then
  cat "$ROOT/wrappers-unmutated.log" >&2
  echo 'FAIL: unmutated wrapper tests must pass before wrapper mutants are judged' >&2
  exit 1
fi
echo 'PASS unmutated wrapper tests before wrapper mutation checks'
for baseline in test-job-check.sh test-hardening-tools.sh test-gate-slots.sh test-pi-impl.sh; do
  if ! bash "$SCRIPT_DIR/$baseline" > "$ROOT/${baseline%.sh}-unmutated.log" 2>&1; then
    cat "$ROOT/${baseline%.sh}-unmutated.log" >&2
    echo "FAIL: unmutated $baseline must pass before its mutants are judged" >&2
    exit 1
  fi
done
echo 'PASS unmutated job-check and hardening tests before their mutation checks'
export CONTROLLER_TEST_PARALLEL_OD_STARTS=0
expect_killed() {
  local label=$1; shift
  if "$@" > "$ROOT/$label.log" 2>&1; then
    echo "SURVIVED: $label" >&2
    exit 1
  fi
  echo "KILLED: $label"
  killed_count=$((killed_count + 1))
}
killed_count=0

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
expect_killed job-dead-owner-running \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-dead-owner-running.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-owner-pid-reuse \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-owner-pid-alive-only.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-owner-boot-id \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-owner-boot.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-owner-liveness \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-owner-liveness.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed codex-run-name-claim \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-run-name-claim.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-resume-stale-marker \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-resume-stale-marker.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-effort-vocabulary \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-effort-vocabulary.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-run-name-child-close \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-run-name-child-close/codex-impl.sh" \
    bash "$SCRIPT_DIR/test-wrappers.sh"
# CTL-22: one mutant per EXEC SITE, because the resume and the auto-resume
# lines are the routine paths and the shared-function mutant above cannot
# distinguish them from the new-launch line.
expect_killed codex-newlaunch-child-close \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-newlaunch-child-close.sh" \
    bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-resume-child-close \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-resume-child-close.sh" \
    bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-autoresume-child-close \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-autoresume-child-close.sh" \
    bash "$SCRIPT_DIR/test-wrappers.sh"
# CTL-23: the owner record is written only by a launch that is real, so each
# refusal after the claim has its own mutant that writes it just before that
# refusal, judged by the case that refuses there.
for codex_record_mutant in codex-record-owner-early codex-record-owner-before-count codex-record-owner-before-accounts \
  codex-force-retire-early; do
  expect_killed "$codex_record_mutant" \
    env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/$codex_record_mutant.sh" \
      bash "$SCRIPT_DIR/test-wrappers.sh"
done
for job_check_mutant in job-check-not-started-off job-check-not-started-held job-check-not-started-stream \
  job-check-not-started-content job-check-died-held-lock job-check-died-no-probe; do
  expect_killed "$job_check_mutant" \
    env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/$job_check_mutant.sh" bash "$SCRIPT_DIR/test-job-check.sh"
done
expect_killed job-prototype-missing-done-owner-record \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-missing-done-owner-record.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-report-litter-detector \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-report-litter.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-merge-trailer-audit \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-merge-trailer.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-any-claude-trailer-compatibility \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-any-claude-trailer.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-check-deepseek-pinned-routes \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-deepseek-pinned-routes.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-check-pi-route \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-pi-route.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-check-pi-message-end \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-pi-message-end.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-check-context-full-class \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-context-full-class.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-check-tool-call-no-text-class \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-tool-call-no-text-class.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-check-tool-call-part-detection \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-tool-call-part-detection.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-check-pi-402-error-code \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-pi-402-error-code.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed usage-retry-pi-error-message \
  env CONTROLLER_LIB_SCRIPT="$ROOT/mutants/scripts/controller/usage-retry-pi-error-message.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed ctl-status-pi-route \
  env CTL_STATUS_SCRIPT="$ROOT/mutants/scripts/controller/ctl-status-pi-route.sh" bash "$SCRIPT_DIR/test-ctl-status.sh"
expect_killed ctl-status-pi-stall \
  env CTL_STATUS_SCRIPT="$ROOT/mutants/scripts/controller/ctl-status-pi-stall.sh" bash "$SCRIPT_DIR/test-ctl-status.sh"
expect_killed hx-provider-identity-guard \
  env HX_WRAPPER="$ROOT/mutants/scripts/controller/hx-identity.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed release-evidence-archive \
  env RELEASE_SCRIPT="$ROOT/mutants/scripts/controller/release-archive.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed release-clean-worktree-guard \
  env RELEASE_SCRIPT="$ROOT/mutants/scripts/controller/release-clean.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed release-psi-input-validation \
  env RELEASE_SCRIPT="$ROOT/mutants/scripts/controller/release-psi-validation.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-deepseek-catalog-safety \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-deepseek-catalog-safety/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-pinned-route-no-fallback \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-pinned-route-fallback.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-route-sidecar-pinned-form \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-route-sidecar-unpinned.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed codex-unknown-route-refusal \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-unknown-route-fallback.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-manual-resume-account \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-manual-resume-account.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed codex-bookkeeping-exit-overwrite \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-bookkeeping-exit-overwrite.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed docs-codex-home-override-route \
  env README_SOURCE="$ROOT/mutants/scripts/controller/README-home-override-route.md" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed docs-rules-stale-last-text \
  env RULES_SOURCE="$ROOT/mutants/scripts/controller/RULES-stale-last-text.md" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed opencode-default-provider \
  env OPENCODE_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/opencode-default-provider.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
for pi_case in \
  pi-prompt-absolute:prompt-absolute pi-worktree-exists:worktree-exists \
  pi-session-id-guard:session-id pi-mode-guard:mode pi-run-name-guard:run-name \
  pi-run-collision-guard:run-collision pi-auth-file-guard:auth-file \
  pi-auth-source-guard:auth-source pi-prompt-preserve-newlines:prompt-newline \
  pi-volker-key-guard:volker-key-missing pi-github2-key-guard:github2-key-missing \
  pi-unknown-route-guard:unknown-route pi-route-sidecar-pinned:pinned-route \
  pi-resume-recorded-account:resume-recorded pi-provider-account:provider-account \
  pi-source-auth-env:source-env pi-session-sidecar:session-sidecar \
  pi-stdin-closed:stdin-closed pi-402-outcome:quota-outcome \
  pi-account-block-run:quota-block pi-key-selected-export:github2-key-export \
  pi-key-volker-export:volker-key-export pi-key-unselected-unset:unselected-key-unset \
  pi-volker-key-xtrace:xtrace-key pi-github2-key-xtrace:xtrace-key \
  pi-error-outcome:outcome-error pi-nonzero-exit-outcome:outcome-rc \
  pi-success-stop-only:outcome-stop-other pi-context-full-missing:outcome-length \
  pi-context-full-order:outcome-length pi-context-full-continued:outcome-length \
  pi-tool-call-no-text-success:outcome-tool-call \
  pi-empty-completion-class:outcome-empty pi-thinking-only-class:outcome-empty \
  pi-uncommitted-class:outcome-uncommitted pi-no-report-class:outcome-no-report \
  pi-worktree-git-fact:repo-not-a-worktree \
  pi-status-fail-closed:git-unreadable pi-continue-live-stream:retry-stream \
  pi-continue-bound:retry-bound pi-continue-class:no-retry-classes \
  pi-continue-exhausted:outcome-empty pi-continue-prompt-file:prompt-file \
  pi-continue-bound-validation:retry-validation \
  pi-report-unset-skips:report-unset \
  pi-run-name-refusal-overwrites:refusal-own-record \
  pi-run-name-refusal-overwrites:refusal-keeps-records \
  pi-record-owner-before-accounts:refusal-keeps-records \
  pi-record-owner-before-credentials:refusal-keeps-records \
  pi-done-removed-before-credentials:refusal-keeps-records \
  pi-route-written-before-credentials:refusal-keeps-records \
  pi-force-retire-early:refusal-keeps-records \
  pi-force-retire-before-credentials:refusal-keeps-records \
  pi-resume-block-inverted:accepted-resume-records \
  pi-record-owner-new-only:accepted-resume-records; do
  pi_mutant=${pi_case%%:*}
  pi_focus=${pi_case#*:}
  expect_killed "$pi_mutant" \
    env PI_TEST_FOCUS="$pi_focus" PI_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/$pi_mutant.sh" \
      bash "$SCRIPT_DIR/test-pi-impl.sh"
done
for pi_live_case in \
  pi-live-atomic:live-atomic pi-live-tool-in-flight:live-snapshot \
  pi-live-missing-fact:live-snapshot pi-live-usage-trend:live-usage-trend \
  pi-live-tokens-zero-as-measurement:live-usage-trend \
  pi-live-progress-stream-only:live-progress \
  pi-live-progress-counts-agent:live-progress \
  pi-live-progress-counts-sidecar:live-progress \
  pi-live-progress-any-change:live-progress \
  pi-live-progress-sum-delta:live-progress \
  pi-live-progress-churn-resets-baseline:live-progress \
  pi-live-progress-pid-recycle:live-progress \
  pi-live-progress-lift-unmeasured:live-progress \
  pi-live-progress-measured-kept:live-progress \
  pi-live-progress-null-clamped:live-progress \
  pi-live-progress-tool-start:live-progress \
  pi-live-progress-silent-cap:live-progress \
  pi-live-tool-output-progress:live-snapshot \
  pi-live-steering-includes-wrapper:live-progress \
  job-status-stall-threshold:status-states job-status-hung-tool:status-states \
  job-status-hung-without-progress:status-states \
  job-status-progress-unknown-as-hung:status-states \
  job-status-progress-unmeasured-as-measured:status-states \
  job-status-progress-absent-flag-as-measured:status-states \
  job-status-hung-tool-command:status-states job-status-steer-target:status-states \
  job-status-since-commit-unqualified:status-states \
  job-status-tokens-last-report:status-states \
  job-status-default-stall:status-defaults job-status-default-hung:status-defaults \
  job-status-wrapper-dead:status-states job-status-finished:status-states \
  job-status-no-live:status-states; do
  pi_live_mutant=${pi_live_case%%:*}
  pi_live_focus=${pi_live_case#*:}
  if [[ $pi_live_mutant == job-status* ]]; then
    expect_killed "$pi_live_mutant" \
      env PI_TEST_FOCUS="$pi_live_focus" \
        JOB_STATUS_SCRIPT="$ROOT/mutants/scripts/controller/$pi_live_mutant.sh" \
        bash "$SCRIPT_DIR/test-pi-impl.sh"
  else
    expect_killed "$pi_live_mutant" \
      env PI_TEST_FOCUS="$pi_live_focus" \
        PI_LIVE_SCRIPT="$ROOT/mutants/scripts/controller/$pi_live_mutant.py" \
        bash "$SCRIPT_DIR/test-pi-impl.sh"
  fi
done
for pi_doc_mutant in README-pi-route-table DESIGN-pi-402-classification CONSUMER-pi-launch-route \
                     README-long-tool-state README-steering-action README-died-state \
                     DESIGN-long-tool-rationale CONSUMER-steering-target CONSUMER-codex-marker-shape \
                     CONSUMER-pi-thinking-not-recorded; do
  case $pi_doc_mutant in
    README-pi-route-table|README-long-tool-state|README-steering-action|README-died-state) doc_env=README_SOURCE ;;
    DESIGN-pi-402-classification|DESIGN-long-tool-rationale) doc_env=DESIGN_SOURCE ;;
    CONSUMER-*) doc_env=CONSUMER_SOURCE ;;
  esac
  doc_path="$ROOT/mutants/scripts/controller/$pi_doc_mutant.md"
  expect_killed "$pi_doc_mutant" env PI_TEST_FOCUS=docs "$doc_env=$doc_path" bash "$SCRIPT_DIR/test-pi-impl.sh"
done
for od_pi_case in \
  od-pi-402-reset-detection:quota-block od-pi-session-event-id:session-event-id \
  od-pi-route-thread-map:session-event-id od-pi-blocked-account-selection:blocked-skip \
  od-pi-resume-blocked-fallback:resume-fallback \
  od-pi-resume-both-blocked-exit:both-blocked-resume od-pi-unknown-reset-default:quota-reset; do
  od_pi_mutant=${od_pi_case%%:*}
  od_pi_focus=${od_pi_case#*:}
  expect_killed "$od_pi_mutant" \
    env PI_TEST_FOCUS="$od_pi_focus" \
      PI_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/$od_pi_mutant/pi-impl.sh" \
      bash "$SCRIPT_DIR/test-pi-impl.sh"
done
expect_killed od-blocked-account-selection \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-blocked-account-selection/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-prefer-expiring-window \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-prefer-expiring-window/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-preference-value-guard \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-preference-value-guard/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-insufficient-balance-detection \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-insufficient-balance-detection/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-unknown-reset-default-block \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-unknown-reset-default-block/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-unknown-reset-knob \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-unknown-reset-knob/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-unknown-block-invalid-value-fallback \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-unknown-block-invalid-value-fallback/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-preserve-later-block \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-preserve-later-block/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-catalog-guard-before-start-record \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-catalog-guard-before-start-record/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-manual-resume-blocked-fallback \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-manual-resume-blocked-fallback/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
for gateway_mutant in od-gateway-text-detection od-gateway-text-anchor od-gateway-text-event-type; do
  expect_killed "$gateway_mutant" \
    env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/$gateway_mutant/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
done
for gateway_mutant in usage-retry-gateway-text usage-retry-gateway-anchor usage-retry-gateway-event-type; do
  expect_killed "$gateway_mutant" \
    env CONTROLLER_LIB_SCRIPT="$ROOT/mutants/scripts/controller/$gateway_mutant.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
done
expect_killed od-reject-unknown-state-fields \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-reject-unknown-state-fields/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-numeric-state-fields \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-numeric-state-fields/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-both-blocked-exit-code \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-both-blocked-exit-code/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-state-path-absolute-guard \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/od-state-path-absolute-guard/codex-impl.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
expect_killed od-exclusive-state-flock \
  env CONTROLLER_OD_ACCOUNTS_SCRIPT="$ROOT/mutants/scripts/controller/od-exclusive-state-flock/opendesign-accounts.py" \
    bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed od-resume-blocked-fallback \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/od-resume-blocked-fallback/resume-usage-limited.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed codex-autoresume-bound \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-autoresume-bound.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed codex-autoresume-limit-validation \
  env CODEX_IMPL_SCRIPT="$ROOT/mutants/scripts/controller/codex-autoresume-limit-validation.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed controller-runs-dir-environment-override \
  env CONTROLLER_LIB_SCRIPT="$ROOT/mutants/scripts/controller/controller-run-dir-override.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-usage-limit-detection \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-usage-detection.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed usage-retry-insufficient-balance \
  env CONTROLLER_LIB_SCRIPT="$ROOT/mutants/scripts/controller/usage-retry-insufficient-balance.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-future-dated-retry \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-future-retry.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-skip-list-guard \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-skip-list.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-route-sidecar-guard \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-route-sidecar.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-route-value-guard \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-route-value.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
expect_killed resume-sol-route-guard \
  env RESUME_USAGE_LIMITED_SCRIPT="$ROOT/mutants/scripts/controller/resume-sol-route.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
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
for gate_slots_mutant in slots-range slots-default one-slot-needs-gate2 one-slot-message lock-dir-absolute lock-dir-unused-below-three \
                         distinct-detail two-slot-distinct-oneline first-pass-last-slot poll-last-slot poll-open-last-slot fd-shared fd-first-slots \
                         slot-label lock-held-while-running wait-two wait-many wait-one progress-many progress-two progress-one labels-join exit-code; do
  expect_killed "gate-run-$gate_slots_mutant" \
    env GATE_RUN_SCRIPT="$ROOT/mutants/scripts/controller/gate-run-$gate_slots_mutant.sh" bash "$SCRIPT_DIR/test-gate-slots.sh"
done
for gate_lib_mutant in derive-default-dir derive-dir-slash derive-name derive-range; do
  expect_killed "gate-run-lib-$gate_lib_mutant" \
    env GATE_RUN_SCRIPT="$ROOT/mutants/scripts/controller/gate-lib-$gate_lib_mutant/gate-run.sh" bash "$SCRIPT_DIR/test-gate-slots.sh"
done
expect_killed job-any-sol-trailer \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-any-sol-trailer.sh" bash "$SCRIPT_DIR/test-job-check.sh"
expect_killed job-sol-route-exact-trailer \
  env JOB_CHECK_SCRIPT="$ROOT/mutants/scripts/controller/job-check-sol-route-trailer.sh" bash "$SCRIPT_DIR/test-job-check.sh"
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
expect_killed ctx-hooks-since-marker \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-since-marker.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-number-next \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-number-next.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-subagent-own \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-subagent-own.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-failure-record \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-failure-record.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-pressure-rearm \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-pressure-rearm.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-hard-timeout \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-hard-timeout.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-kind-wording \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-kind-wording.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-pattern-leak \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-pattern-leak.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-spawned-goal \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-spawned-goal.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-subagent-skip \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-subagent-skip.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed ctx-hooks-crash-log \
  env CTX_HOOKS_SCRIPT="$ROOT/mutants/scripts/controller/ctx-hooks-crash-log.py" bash "$SCRIPT_DIR/test-ctx-hooks.sh"
expect_killed push-pipeline-exit-propagation \
  env PUSH_IMAGE_SCRIPT="$ROOT/mutants/scripts/controller/push-image-status.sh" bash "$SCRIPT_DIR/test-wrappers.sh"
for skills_mutant in skills-stamp-ownership skills-modified-clobber skills-stale-detection \
                     skills-dangling-symlink skills-check-exit skills-usage-range \
                     skills-dest-arity skills-install-failure skills-synced-guard skills-dry-run-writes; do
  expect_killed "$skills_mutant" \
    env SKILLS_SYNC_SCRIPT="$ROOT/mutants/scripts/controller/$skills_mutant.sh" bash "$SCRIPT_DIR/test-skills-sync.sh"
done
for cache_mutant in remote-gate-cache-inplace-publish remote-gate-cache-name-only-validity \
                    remote-gate-cache-lock-ignored remote-gate-cache-absent-as-refresh \
                    remote-gate-cache-identity-missing remote-gate-cache-stray-repo; do
  expect_killed "$cache_mutant" \
    env REMOTE_GATE_CACHE_SCRIPT="$ROOT/mutants/scripts/netcup/runner/$cache_mutant.sh" \
      bash "$REPO_ROOT/scripts/netcup/runner/tests/test_remote_gate_cache.sh"
done
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
for gate_run_profile_mutant in default override; do
  expect_killed "gate-run-profile-$gate_run_profile_mutant" \
    env GATE_RUN_SCRIPT="$ROOT/mutants/scripts/controller/gate-run-profile-$gate_run_profile_mutant.sh" bash "$SCRIPT_DIR/test-hardening-tools.sh"
done
for remote_gate_mutant in identity allow-list project-dir-guard slot-roundup weight-min oom-delta commit-binding slot-descriptors base-forward api-override runner-ref base-passthrough base-twice \
                          old-env-silent old-env-one-dropped old-env-py-name old-env-dry-hold weights-unreadable-silent weights-missing-warns repo-name-default snapshot-fixed-name snapshot-leftover; do
  expect_killed "remote-gate-$remote_gate_mutant" \
    env REMOTE_GATE_SCRIPT="$ROOT/mutants/scripts/controller/remote-gate-$remote_gate_mutant.sh" bash "$SCRIPT_DIR/test-remote-gate.sh"
done
for chain_run_mutant in log-name remote-log-name marker-text stale-marker head-pin dirty-tree stop-on-fail stop-without-flag \
                        remote-base remote-commit remote-head-lookup remote-host-ambiguity launcher-check subdir-guard \
                        lane-name-guard lane-cwd default-launcher export-image export-socket profile-default unset-env unset-env-name; do
  expect_killed "chain-run-$chain_run_mutant" \
    env CHAIN_RUN_SCRIPT="$ROOT/mutants/scripts/controller/chain-run-$chain_run_mutant.sh" bash "$SCRIPT_DIR/test-chain-run.sh"
done
for doctor_mutant in tool-flock-severity tool-docker-severity home-missing env-missing env-syntax env-stray hosts-missing hosts-ssh-shape \
                     hosts-guard hosts-lanes-shape hosts-lanes-warn hosts-api-exec hosts-key-missing hosts-key-mode hosts-pin hosts-pin-devnull hosts-pin-devnull-ok hosts-strict \
                     hosts-nopin hosts-leak allow-unknown allow-unset remote-file-split image-absent image-exact-name image-unreachable \
                     image-no-docker image-toml pinned-warn pinned-logs pinned-scan pinned-home profile-default gate-lock-unset gate-lock-absolute gate-lock-parent gate-lock-distinct gate-slots-invalid gate-slots-print gate-slots-default-print gate-slots-leak gate-slots-fallback gate-slot2-one-slot gate-derived-check gate-derived-range gate-lock-dir-absolute gate-lock-dir-unused gate-collide-pair-text gate-derived-fix dir-missing dir-writable \
                     symlink-detect residency-ignore residency-outside residency-runs json exit-code usage-exit; do
  # Only the non-root block of the doctor test can kill this one: as root it is reported skipped, never killed.
  if [[ $doctor_mutant == dir-writable && $(id -u) == 0 ]]; then
    echo "SKIPPED: ctl-doctor-$doctor_mutant (its test block needs a non-root uid)"
    continue
  fi
  expect_killed "ctl-doctor-$doctor_mutant" \
    env CTL_DOCTOR_SCRIPT="$ROOT/mutants/scripts/controller/ctl-doctor-$doctor_mutant.sh" bash "$SCRIPT_DIR/test-ctl-doctor.sh"
done
for doctor_lib_mutant in readonly-mkdir readonly-resolve configured-home configured-runs; do
  expect_killed "ctl-doctor-lib-$doctor_lib_mutant" \
    env CTL_DOCTOR_SCRIPT="$ROOT/mutants/scripts/controller/doctor-lib-$doctor_lib_mutant/ctl-doctor.sh" bash "$SCRIPT_DIR/test-ctl-doctor.sh"
done
for backup_mutant in cap jsonl git-dir work-tree unset relative list-only mode follow-links sidecar verify-compare runs-state \
                     remote-summaries empty overwrite link-targets hardlink-targets chmod-existing chmod-never verify-empty verify-no-sidecar linked-detect linked-members; do
  expect_killed "ctl-state-backup-$backup_mutant" \
    env CTL_STATE_BACKUP_SCRIPT="$ROOT/mutants/scripts/controller/ctl-state-backup-$backup_mutant.sh" bash "$SCRIPT_DIR/test-ctl-state-backup.sh"
done
echo "PASS hand mutations: $killed_count killed"
